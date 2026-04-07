"""
AgentLens Demo — The SQL Injection Scenario

Standard observability shows: HTTP 200, 1.2s latency, 4200 tokens
AgentLens shows: agent.eval.score=0.1, "Missed SQL injection vulnerability"

This is the gap in Prove AI's current OTEL config.
Their spanmetrics connector only tracks env + component.
AgentLens adds 5 more agent-specific dimensions including agent.eval.score.
"""

import argparse
import time

from orchestrator import run_pipeline


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run AgentLens in mock or real PR mode.")
    parser.add_argument(
        "--pr-url",
        help="GitHub pull request URL. If provided, AgentLens runs in real PR mode.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Force live mode even without a PR URL. Useful if TRIAGE_AGENT_URL points elsewhere.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    use_mock = not (args.live or args.pr_url)

    if use_mock:
        print(
            """
╔══════════════════════════════════════════════════════════════╗
║              AgentLens — Agent Decision Telemetry            ║
║         Extending Prove AI's observability-pipeline          ║
╚══════════════════════════════════════════════════════════════╝

Scenario: PR "Add user search endpoint" contains SQL injection.
          Agent classifies it as LOW PRIORITY with 0.92 confidence.
          Standard OTEL: green. AgentLens: eval_score=0.1.
"""
        )
    else:
        print(
            f"""
╔══════════════════════════════════════════════════════════════╗
║              AgentLens — Real PR Analysis                    ║
║         Extending Prove AI's observability-pipeline          ║
╚══════════════════════════════════════════════════════════════╝

Real mode enabled.
PR URL: {args.pr_url or "provided by downstream caller"}
This run analyzes a real GitHub pull request and emits live decision telemetry.
"""
        )

    time.sleep(1)
    result = run_pipeline(use_mock=use_mock, pr_url=args.pr_url)

    if use_mock:
        print(
            """
═══════════════════════════════════════════════════════════════
What Prove AI's current OTEL sees:
  • spans: ✓  latency: 1.2s  status: 200  tokens: 4200

What AgentLens adds:
  • agent.name: decision_agent
  • agent.decision: low_priority
  • agent.confidence: 0.92
  • agent.eval.score: 0.1          ← THE SIGNAL THAT MATTERS
  • agent.eval.reason: Missed SQL injection vulnerability
  • agent.handoff.from: security_agent
  • llm.model: demo/mock
═══════════════════════════════════════════════════════════════
"""
        )
    else:
        pr_data = result.get("pr_data", {})
        print(
            f"""
═══════════════════════════════════════════════════════════════
Real PR summary:
  • title: {pr_data.get("title", "unknown")}
  • author: {pr_data.get("author", "unknown")}
  • files_changed: {pr_data.get("files_changed", "unknown")}
  • diff_truncated: {pr_data.get("diff_truncated", False)}

Use Jaeger to inspect the agent spans and Grafana to inspect the exported metrics.
═══════════════════════════════════════════════════════════════
"""
        )


if __name__ == "__main__":
    main()
