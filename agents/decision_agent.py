import json
import os
import sys
from pathlib import Path
from typing import Any

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from litellm import completion, completion_cost
from opentelemetry import trace
from pydantic import BaseModel

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from otel_instrumentor import agent_span, setup_tracing

load_dotenv()

PORT = int(os.getenv("DECISION_AGENT_PORT", "8012"))
OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
TRACER = setup_tracing(service_name="decision-agent", otlp_endpoint=OTLP_ENDPOINT)

app = FastAPI(title="AgentLens Decision Agent")


class DecisionRequest(BaseModel):
    triage_result: dict[str, Any]
    security_result: dict[str, Any]
    diff: str
    use_mock: bool = False


class LLMCallError(RuntimeError):
    pass


def _select_model() -> str:
    if os.getenv("OPENAI_API_KEY"):
        return os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    if os.getenv("GEMINI_API_KEY"):
        return os.getenv("GEMINI_MODEL", "gemini/gemini-2.0-flash")
    raise RuntimeError("No LLM API key configured. Set OPENAI_API_KEY or GEMINI_API_KEY.")


def _parse_json_content(content: str) -> dict[str, Any]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1]
        cleaned = cleaned.rsplit("```", 1)[0]
    return json.loads(cleaned)


def _usage_value(usage: Any, field: str) -> int:
    if usage is None:
        return 0
    if isinstance(usage, dict):
        return int(usage.get(field, 0) or 0)
    return int(getattr(usage, field, 0) or 0)


def _fallback_eval(request: DecisionRequest, final_decision: dict[str, Any]) -> dict[str, Any]:
    diff = request.diff.lower()
    unsafe_sql = "select * from users where name" in diff and "{query}" in diff
    if unsafe_sql and final_decision.get("final_priority") != "critical":
        return {
            "eval_score": 0.1,
            "eval_reason": "Missed SQL injection vulnerability in PR diff",
        }
    return {
        "eval_score": 0.95,
        "eval_reason": "Final decision aligned with the actual risk present in the PR diff.",
    }


def _demo_final_decision(_: DecisionRequest) -> dict[str, Any]:
    return {
        "final_priority": "low_priority",
        "action": "merge",
        "summary": (
            "Demo mode intentionally preserves the bad low-priority call so the "
            "evaluation telemetry can prove the point of AgentLens."
        ),
        "confidence": 0.92,
    }


def _call_llm(prompt: str) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        model = _select_model()
        response = completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        content = response.choices[0].message.content or "{}"
        usage = getattr(response, "usage", None)
        cost_usd = 0.0
        try:
            cost_usd = float(completion_cost(completion_response=response))
        except Exception:
            cost_usd = 0.0
        return _parse_json_content(content), {
            "model": model,
            "prompt_tokens": _usage_value(usage, "prompt_tokens"),
            "completion_tokens": _usage_value(usage, "completion_tokens"),
            "cost_usd": cost_usd,
        }
    except Exception as exc:
        raise LLMCallError(f"Decision agent LLM call failed: {exc}") from exc


@app.post("/run")
def run_agent(request: DecisionRequest) -> dict[str, Any]:
    with agent_span(
        TRACER,
        "decision_agent",
        agent_framework="fastapi",
        handoff_from="security_agent",
    ) as span:
        decision_prompt = f"""
You are the final decision agent for PR triage.
Given triage and security analysis, make a final verdict.

Respond with JSON: {{
  "final_priority": "critical|high|medium|low_priority",
  "action": "block|review|merge",
  "summary": "...",
  "confidence": 0.0-1.0
}}

Triage: {json.dumps(request.triage_result)}
Security: {json.dumps(request.security_result)}
""".strip()

        if request.use_mock:
            final_decision = _demo_final_decision(request)
            decision_meta = {
                "model": "demo/mock",
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cost_usd": 0.0,
            }
        else:
            try:
                final_decision, decision_meta = _call_llm(decision_prompt)
            except LLMCallError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc

        eval_prompt = f"""
You are an expert code reviewer evaluating an AI agent's PR triage decision.

The agent decided: {json.dumps(final_decision)}
The actual PR diff contains: {request.diff}

Score the decision quality from 0.0 (completely wrong) to 1.0 (perfect).
Respond with JSON: {{"eval_score": 0.0-1.0, "eval_reason": "..."}}
""".strip()

        if request.use_mock:
            eval_result = _fallback_eval(request, final_decision)
            eval_meta = {
                "model": "demo/mock",
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cost_usd": 0.0,
            }
        else:
            try:
                eval_result, eval_meta = _call_llm(eval_prompt)
            except LLMCallError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc

        current_span = trace.get_current_span()
        current_span.set_attribute("llm.decision.model", decision_meta["model"])
        current_span.set_attribute("llm.eval.model", eval_meta["model"])
        current_span.set_attribute("llm.eval.prompt_tokens", eval_meta["prompt_tokens"])
        current_span.set_attribute("llm.eval.completion_tokens", eval_meta["completion_tokens"])
        current_span.set_attribute("llm.eval.cost_usd", eval_meta["cost_usd"])

        span.record_llm_call(
            model=decision_meta["model"],
            prompt_tokens=decision_meta["prompt_tokens"] + eval_meta["prompt_tokens"],
            completion_tokens=decision_meta["completion_tokens"] + eval_meta["completion_tokens"],
            cost_usd=decision_meta["cost_usd"] + eval_meta["cost_usd"],
        )
        span.record_decision(
            decision=str(final_decision.get("final_priority", "medium")),
            confidence=float(final_decision.get("confidence", 0.0) or 0.0),
            reasoning=str(final_decision.get("summary", "")),
            eval_score=float(eval_result.get("eval_score", 0.0) or 0.0),
            eval_reason=str(eval_result.get("eval_reason", "")),
        )

        return {
            **final_decision,
            **eval_result,
        }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
