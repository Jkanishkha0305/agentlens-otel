import unittest
from pathlib import Path
import sys
import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

fake_otel = types.ModuleType("otel_instrumentor")
fake_otel.agent_span = lambda *args, **kwargs: None
fake_otel.setup_tracing = lambda *args, **kwargs: None
sys.modules.setdefault("otel_instrumentor", fake_otel)

from agents.decision_agent import DecisionRequest, _fallback_eval


class DecisionAgentTests(unittest.TestCase):
    def test_eval_flags_confident_wrong_sql_decision(self) -> None:
        request = DecisionRequest(
            triage_result={"priority": "low_priority", "confidence": 0.92},
            security_result={"has_vulnerabilities": False},
            diff=(
                "diff --git a/api/search.py b/api/search.py\n"
                "+result = conn.execute(f\"SELECT * FROM users WHERE name = '{query}'\")\n"
            ),
            use_mock=True,
        )

        result = _fallback_eval(
            request,
            {"final_priority": "low_priority", "action": "merge", "confidence": 0.92},
        )

        self.assertEqual(result["eval_score"], 0.1)
        self.assertIn("SQL injection", result["eval_reason"])

    def test_eval_rewards_correct_blocking_decision(self) -> None:
        request = DecisionRequest(
            triage_result={"priority": "critical", "confidence": 0.9},
            security_result={"has_vulnerabilities": True},
            diff=(
                "diff --git a/api/search.py b/api/search.py\n"
                "+result = conn.execute(f\"SELECT * FROM users WHERE name = '{query}'\")\n"
            ),
            use_mock=False,
        )

        result = _fallback_eval(
            request,
            {"final_priority": "critical", "action": "block", "confidence": 0.9},
        )

        self.assertEqual(result["eval_score"], 0.95)


if __name__ == "__main__":
    unittest.main()
