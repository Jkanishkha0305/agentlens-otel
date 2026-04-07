import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_server.github_mcp import MAX_DIFF_CHARS, MAX_DIFF_LINES, _limit_diff


class LimitDiffTests(unittest.TestCase):
    def test_limit_diff_preserves_small_diffs(self) -> None:
        diff = "line1\nline2\nline3"

        limited, truncated, original_lines, original_chars = _limit_diff(diff)

        self.assertEqual(limited, diff)
        self.assertFalse(truncated)
        self.assertEqual(original_lines, 3)
        self.assertEqual(original_chars, len(diff))

    def test_limit_diff_truncates_large_diffs(self) -> None:
        huge_diff = "\n".join(f"line-{i}" for i in range(MAX_DIFF_LINES + 50))
        huge_diff += "\n" + ("x" * MAX_DIFF_CHARS)

        limited, truncated, original_lines, original_chars = _limit_diff(huge_diff)

        self.assertTrue(truncated)
        self.assertGreater(original_lines, MAX_DIFF_LINES)
        self.assertGreater(original_chars, MAX_DIFF_CHARS)
        self.assertIn("... DIFF TRUNCATED ...", limited)
        self.assertLessEqual(len(limited), MAX_DIFF_CHARS + len("\n... DIFF TRUNCATED ...\n"))


if __name__ == "__main__":
    unittest.main()
