import os

import httpx


def run_pipeline(use_mock: bool = True, pr_url: str | None = None):
    """Run the full AgentLens triage pipeline."""
    triage_agent_url = os.getenv("TRIAGE_AGENT_URL", "http://localhost:8010")

    print("=" * 60)
    print("AgentLens — Multi-Agent PR Triage")
    print("=" * 60)

    payload = {"use_mock": use_mock}
    if pr_url:
        payload["pr_url"] = pr_url

    print("\n[1/3] Triage Agent → analyzing PR...")
    response = httpx.post(f"{triage_agent_url.rstrip('/')}/run", json=payload, timeout=60)
    response.raise_for_status()
    result = response.json()

    print(f"      Decision: {result.get('final_priority', result.get('priority'))}")
    print(f"      Confidence: {result.get('confidence')}")
    print(f"      Eval Score: {result.get('eval_score', 'N/A')}")

    if result.get("eval_score") and result["eval_score"] < 0.3:
        print(f"\n⚠️  AgentLens ALERT: Low eval score ({result['eval_score']})")
        print(f"   Reason: {result.get('eval_reason')}")
        print("   Standard OTEL shows: HTTP 200, latency OK")
        print("   AgentLens shows: Decision quality POOR")

    print("\n📊 View traces: http://localhost:16686 (Jaeger)")
    print("📈 View metrics: http://localhost:3001 (Grafana)")
    return result


if __name__ == "__main__":
    run_pipeline(use_mock=True)
