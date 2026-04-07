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

PORT = int(os.getenv("TRIAGE_AGENT_PORT", "8010"))
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8001")
SECURITY_AGENT_URL = os.getenv("SECURITY_AGENT_URL", "http://localhost:8011")
OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
TRACER = setup_tracing(service_name="triage-agent", otlp_endpoint=OTLP_ENDPOINT)

app = FastAPI(title="AgentLens Triage Agent")


class TriageRequest(BaseModel):
    pr_url: Optional[str] = None
    use_mock: bool = True


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


def _demo_triage(diff: str) -> dict[str, Any]:
    return {
        "priority": "low_priority",
        "reasoning": (
            "Demo mode intentionally simulates a confident miss so the telemetry "
            "layer can show a bad decision that still looks operationally healthy."
        ),
        "confidence": 0.92,
        "contains_code_changes": bool(diff.strip()),
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
        raise LLMCallError(f"Triage agent LLM call failed: {exc}") from exc


@app.post("/run")
def run_agent(request: TriageRequest) -> dict[str, Any]:
    with agent_span(TRACER, "triage_agent", agent_framework="fastapi") as span:
        try:
            with httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
                pr_response = client.post(
                    f"{MCP_SERVER_URL.rstrip('/')}/fetch_pr",
                    json={"pr_url": request.pr_url, "use_mock": request.use_mock},
                )
                pr_response.raise_for_status()
                pr_data = pr_response.json()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=exc.response.status_code,
                detail=f"MCP server error: {exc.response.text}",
            ) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Failed to reach MCP server: {exc}") from exc

        diff = pr_data.get("diff", "")
        span.record_tool_use("github_mcp.fetch_pr", result_tokens=len(diff.split()))

        prompt = f"""
You are a PR triage agent. Given this PR diff, classify its priority.
Priority options: critical, high, medium, low_priority
Respond with JSON: {{"priority": "...", "reasoning": "...", "confidence": 0.0-1.0, "contains_code_changes": true/false}}

PR: {diff}
""".strip()

        if request.use_mock:
            triage_result = _demo_triage(diff)
            llm_meta = {
                "model": "demo/mock",
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cost_usd": 0.0,
            }
        else:
            try:
                triage_result, llm_meta = _call_llm(prompt)
            except LLMCallError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
        span.record_llm_call(
            model=llm_meta["model"],
            prompt_tokens=llm_meta["prompt_tokens"],
            completion_tokens=llm_meta["completion_tokens"],
            cost_usd=llm_meta["cost_usd"],
        )
        span.record_decision(
            decision=str(triage_result.get("priority", "medium")),
            confidence=float(triage_result.get("confidence", 0.0) or 0.0),
            reasoning=str(triage_result.get("reasoning", "")),
        )

        response_payload: dict[str, Any] = {
            "pr_data": pr_data,
            "triage_result": triage_result,
            **triage_result,
        }

        if triage_result.get("contains_code_changes"):
            try:
                with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
                    security_response = client.post(
                        f"{SECURITY_AGENT_URL.rstrip('/')}/run",
                        json={
                            "pr_data": pr_data,
                            "diff": diff,
                            "triage_result": triage_result,
                            "use_mock": request.use_mock,
                        },
                    )
                    security_response.raise_for_status()
                    security_payload = security_response.json()
            except httpx.HTTPStatusError as exc:
                response_payload["security_error"] = exc.response.text
                return response_payload
            except httpx.HTTPError as exc:
                response_payload["security_error"] = str(exc)
                return response_payload

            security_result = security_payload.get("security_result", security_payload)
            response_payload["security_result"] = security_result

            decision_result = security_payload.get("decision_result")
            if isinstance(decision_result, dict):
                response_payload["decision_result"] = decision_result
                response_payload.update(decision_result)

        return response_payload


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
