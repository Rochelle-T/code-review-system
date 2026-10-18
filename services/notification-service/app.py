"""Notification Service: posts review findings back to the pull request as a comment."""
import logging
import os

import requests
from fastapi import FastAPI, HTTPException
from prometheus_client import Counter
from prometheus_fastapi_instrumentator import Instrumentator

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("notification-service")

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_API = os.getenv("GITHUB_API", "https://api.github.com")
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://localhost:8080")
MAX_ROWS = 25

COMMENTS = Counter("pr_comments_total", "PR comments posted", ["result"])

app = FastAPI(title="Notification Service")
Instrumentator().instrument(app).expose(app)


def _cell(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def format_comment(result: dict) -> str:
    findings = result.get("findings", [])
    total, errors, warnings = len(findings), result.get("errors", 0), result.get("warnings", 0)
    files = result.get("files_analyzed", 0)
    sha = (result.get("head_sha") or "")[:7]
    lines = ["## 🤖 Automated code review", ""]
    if total == 0:
        lines.append(f"✅ No issues found in {files} Python file(s) at `{sha}`.")
    else:
        lines += [
            f"Checked **{files}** Python file(s) at `{sha}` and found **{total}** issue(s): "
            f"{errors} error(s), {warnings} warning(s).",
            "",
            "| Severity | File | Line | Tool | Rule | Message |",
            "|---|---|---|---|---|---|",
        ]
        for f in findings[:MAX_ROWS]:
            icon = "🔴" if f["severity"] == "error" else "🟡"
            lines.append(f"| {icon} {f['severity']} | `{_cell(f['file'])}` | {f['line']} | {f['tool']} | "
                         f"`{_cell(f['code'])}` | {_cell(f['message'])} |")
        if total > MAX_ROWS:
            lines += ["", f"_…and {total - MAX_ROWS} more. Full report: {DASHBOARD_URL}_"]
    if result.get("ai_summary"):
        lines += ["", "### AI review", "", result["ai_summary"]]
    lines += ["", f"<sub>Analysis took {result.get('duration_seconds', 0)}s. Dashboard: {DASHBOARD_URL}</sub>"]
    return "\n".join(lines)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/notify")
def notify(result: dict):
    if result.get("demo"):
        COMMENTS.labels("skipped").inc()
        return {"status": "skipped", "reason": "demo job"}
    if not GITHUB_TOKEN:
        COMMENTS.labels("skipped").inc()
        log.warning("GITHUB_TOKEN not set; not posting a comment")
        return {"status": "skipped", "reason": "no GitHub token"}

    url = f"{GITHUB_API}/repos/{result['repo']}/issues/{result['pr_number']}/comments"
    resp = requests.post(url, json={"body": format_comment(result)}, timeout=20, headers={
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    })
    if resp.status_code >= 300:
        COMMENTS.labels("failed").inc()
        log.error("GitHub API error %s: %s", resp.status_code, resp.text[:300])
        raise HTTPException(status_code=502, detail=f"GitHub API error {resp.status_code}")
    COMMENTS.labels("posted").inc()
    log.info("Posted comment on %s#%s", result["repo"], result["pr_number"])
    return {"status": "posted", "url": resp.json().get("html_url")}
