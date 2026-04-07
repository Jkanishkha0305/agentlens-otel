"""
AgentLens Demo — The SQL Injection Scenario

Standard observability shows: HTTP 200, 1.2s latency, 4200 tokens
AgentLens shows: agent.eval.score=0.1, "Missed SQL injection vulnerability"

This is the gap in Prove AI's current OTEL config.
Their spanmetrics connector only tracks env + component.
AgentLens adds 5 more agent-specific dimensions including agent.eval.score.
"""

import time

from orchestrator import run_pipeline


def main() -> None:
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

    time.sleep(1)
    run_pipeline(use_mock=True)

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


if __name__ == "__main__":
    main()
