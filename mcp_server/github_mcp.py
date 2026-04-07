import os
import re
from typing import Optional

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

load_dotenv()

PORT = int(os.getenv("MCP_SERVER_PORT", "8001"))
GITHUB_API_URL = os.getenv("GITHUB_API_URL", "https://api.github.com")
MAX_DIFF_LINES = int(os.getenv("MAX_DIFF_LINES", "400"))
MAX_DIFF_CHARS = int(os.getenv("MAX_DIFF_CHARS", "20000"))

MOCK_PR = {
    "title": "Add user search endpoint",
    "author": "dev-contributor",
    "files_changed": 2,
    "diff": """diff --git a/api/search.py b/api/search.py
+def search_users(query: str):
+    conn = get_db()
+    # Search users by name
+    result = conn.execute(f"SELECT * FROM users WHERE name = '{query}'")
+    return result.fetchall()
""",
}

app = FastAPI(title="AgentLens GitHub MCP Server")


class FetchPRRequest(BaseModel):
    pr_url: Optional[str] = None
    use_mock: bool = False


def _parse_pr_url(pr_url: str) -> tuple[str, str, str]:
    match = re.match(r"^https://github\.com/([^/]+)/([^/]+)/pull/(\d+)$", pr_url)
    if not match:
        raise HTTPException(
            status_code=400,
            detail="PR URL must look like https://github.com/<owner>/<repo>/pull/<number>",
        )
    return match.group(1), match.group(2), match.group(3)


def _github_headers(token: str, accept: str) -> dict[str, str]:
    headers = {
        "Accept": accept,
        "User-Agent": "agentlens-demo",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _limit_diff(diff: str) -> tuple[str, bool, int, int]:
    original_lines = len(diff.splitlines())
    original_chars = len(diff)
    truncated = False
    limited = diff

    if original_lines > MAX_DIFF_LINES:
        head_count = MAX_DIFF_LINES // 2
        tail_count = MAX_DIFF_LINES - head_count
        lines = limited.splitlines()
        limited = "\n".join(
            lines[:head_count]
            + ["... DIFF TRUNCATED ..."]
            + lines[-tail_count:]
        )
        truncated = True

    if len(limited) > MAX_DIFF_CHARS:
        half = MAX_DIFF_CHARS // 2
        limited = (
            limited[:half]
            + "\n... DIFF TRUNCATED ...\n"
            + limited[-half:]
        )
        truncated = True

    return limited, truncated, original_lines, original_chars


@app.post("/fetch_pr")
def fetch_pr(request: FetchPRRequest) -> dict[str, object]:
    if request.use_mock:
        limited_diff, truncated, original_lines, original_chars = _limit_diff(MOCK_PR["diff"])
        return {
            **MOCK_PR,
            "diff": limited_diff,
            "diff_truncated": truncated,
            "original_diff_lines": original_lines,
            "original_diff_chars": original_chars,
            "returned_diff_lines": len(limited_diff.splitlines()),
            "returned_diff_chars": len(limited_diff),
        }

    if not request.pr_url:
        raise HTTPException(status_code=400, detail="Either pr_url or use_mock=true is required")

    github_token = os.getenv("GITHUB_TOKEN", "")

    owner, repo, pr_number = _parse_pr_url(request.pr_url)
    pr_endpoint = f"{GITHUB_API_URL}/repos/{owner}/{repo}/pulls/{pr_number}"

    try:
        with httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            pr_response = client.get(
                pr_endpoint,
                headers=_github_headers(github_token, "application/vnd.github+json"),
            )
            pr_response.raise_for_status()
            pr_payload = pr_response.json()

            diff_response = client.get(
                pr_endpoint,
                headers=_github_headers(github_token, "application/vnd.github.v3.diff"),
            )
            diff_response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"GitHub API request failed: {exc.response.text}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Failed to reach GitHub API: {exc}") from exc

    limited_diff, truncated, original_lines, original_chars = _limit_diff(diff_response.text)

    return {
        "title": pr_payload.get("title", ""),
        "diff": limited_diff,
        "author": pr_payload.get("user", {}).get("login", "unknown"),
        "files_changed": pr_payload.get("changed_files", 0),
        "diff_truncated": truncated,
        "original_diff_lines": original_lines,
        "original_diff_chars": original_chars,
        "returned_diff_lines": len(limited_diff.splitlines()),
        "returned_diff_chars": len(limited_diff),
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
