import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from litellm import completion, completion_cost
from pydantic import BaseModel

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from otel_instrumentor import agent_span, setup_tracing

load_dotenv()

PORT = int(os.getenv("SECURITY_AGENT_PORT", "8011"))
DECISION_AGENT_URL = os.getenv("DECISION_AGENT_URL", "http://localhost:8012")
OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
TRACER = setup_tracing(service_name="security-agent", otlp_endpoint=OTLP_ENDPOINT)

app = FastAPI(title="AgentLens Security Agent")


class SecurityRequest(BaseModel):
    pr_data: dict[str, Any]
    diff: str
    triage_result: Optional[dict[str, Any]] = None
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


def _demo_security(_: str) -> dict[str, Any]:
    return {
        "has_vulnerabilities": False,
        "severity": "none",
        "findings": [],
        "reasoning": (
            "Demo mode intentionally lets the bad decision pass through so the "
            "evaluation span can show why standard telemetry is not enough."
        ),
        "confidence": 0.87,
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
        raise LLMCallError(f"Security agent LLM call failed: {exc}") from exc


@app.post("/run")
def run_agent(request: SecurityRequest) -> dict[str, Any]:
    with agent_span(
        TRACER,
        "security_agent",
        agent_framework="fastapi",
        handoff_from="triage_agent",
        handoff_reason="contains_code_changes",
    ) as span:
        prompt = f"""
You are a security review agent. Scan this PR diff for security vulnerabilities.
Focus on: SQL injection, XSS, hardcoded secrets, path traversal, command injection.

Respond with JSON: {{
  "has_vulnerabilities": bool,
  "severity": "critical|high|medium|low|none",
  "findings": ["..."],
  "reasoning": "...",
  "confidence": 0.0-1.0
}}

PR diff: {request.diff}
""".strip()

        if request.use_mock:
            security_result = _demo_security(request.diff)
            llm_meta = {
                "model": "demo/mock",
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cost_usd": 0.0,
            }
        else:
            try:
                security_result, llm_meta = _call_llm(prompt)
            except LLMCallError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
        span.record_llm_call(
            model=llm_meta["model"],
            prompt_tokens=llm_meta["prompt_tokens"],
            completion_tokens=llm_meta["completion_tokens"],
            cost_usd=llm_meta["cost_usd"],
        )
        span.record_decision(
            decision=str(security_result.get("severity", "none")),
            confidence=float(security_result.get("confidence", 0.0) or 0.0),
            reasoning=str(security_result.get("reasoning", "")),
        )

        try:
            with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
                decision_response = client.post(
                    f"{DECISION_AGENT_URL.rstrip('/')}/run",
                    json={
                        "triage_result": request.triage_result or {},
                        "security_result": security_result,
                        "diff": request.diff,
                        "use_mock": request.use_mock,
                    },
                )
                decision_response.raise_for_status()
                decision_result = decision_response.json()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=exc.response.status_code,
                detail=f"Decision agent error: {exc.response.text}",
            ) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Failed to reach decision agent: {exc}") from exc

        return {
            "security_result": security_result,
            "decision_result": decision_result,
            **decision_result,
        }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
