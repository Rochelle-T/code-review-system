"""Code Analysis Service.

A queue worker: takes review jobs from Redis, downloads the changed Python files of the
pull request, runs static analysis (flake8 + bandit) and an optional AI review, then sends
the results to the Notification Service and the Report Service.

This is the CPU-heavy service that the Kubernetes HPA scales from 1 to 5 pods.
"""
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

import redis
import requests
from prometheus_client import Counter, Gauge, Histogram, start_http_server

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("code-analysis")

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
QUEUE_NAME = os.getenv("QUEUE_NAME", "review-jobs")
NOTIFICATION_URL = os.getenv("NOTIFICATION_URL", "http://notification-service:8000")
REPORT_URL = os.getenv("REPORT_URL", "http://report-service:8000")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_API = os.getenv("GITHUB_API", "https://api.github.com")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "claude-haiku-5-5")
MAX_FILES = int(os.getenv("MAX_FILES", "50"))
METRICS_PORT = int(os.getenv("METRICS_PORT", "8000"))
SAMPLES_DIR = Path(__file__).parent / "samples"

# flake8 codes treated as real errors (syntax errors, undefined names, invalid comparisons, ...)
ERROR_PREFIXES = ("E9", "F63", "F7", "F82")
FLAKE8_FORMAT = "%(path)s::%(row)d::%(col)d::%(code)s::%(text)s"

JOBS = Counter("review_jobs_processed_total", "Review jobs processed", ["status"])
DURATION = Histogram("review_job_duration_seconds", "Time taken to analyse one review job",
                     buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120))
QUEUE_LENGTH = Gauge("review_queue_length", "Jobs waiting in the review queue")
FINDINGS = Counter("review_findings_total", "Findings reported", ["severity"])


# ---------------------------------------------------------------- GitHub
def _gh_headers(raw: bool = False) -> dict:
    headers = {"Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers


def fetch_pr_files(job: dict, workdir: Path) -> str:
    """Download changed .py files of the PR into workdir. Returns the combined diff text."""
    patches, saved, page = [], 0, 1
    while saved < MAX_FILES:
        resp = requests.get(f"{GITHUB_API}/repos/{job['repo']}/pulls/{job['pr_number']}/files",
                            headers=_gh_headers(), params={"per_page": 100, "page": page}, timeout=20)
        resp.raise_for_status()
        batch = resp.json()
        for f in batch:
            name = f["filename"]
            if f.get("status") == "removed" or not name.endswith(".py") or saved >= MAX_FILES:
                continue
            target = (workdir / name).resolve()
            if workdir.resolve() not in target.parents:  # never write outside the temp dir
                continue
            content = requests.get(f"{GITHUB_API}/repos/{job['repo']}/contents/{quote(name)}",
                                   headers=_gh_headers(raw=True), params={"ref": job["head_sha"]}, timeout=20)
            content.raise_for_status()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content.content)
            saved += 1
            if f.get("patch"):
                patches.append(f"--- {name}\n{f['patch']}")
        if len(batch) < 100:
            break
        page += 1
    return "\n\n".join(patches)


# ---------------------------------------------------------------- static analysis
def flake8_severity(code: str) -> str:
    return "error" if code.startswith(ERROR_PREFIXES) else "warning"


def parse_flake8(output: str) -> list[dict]:
    findings = []
    for line in output.splitlines():
        parts = line.split("::", 4)
        if len(parts) != 5:
            continue
        path, row, _col, code, text = parts
        findings.append({"tool": "flake8", "file": path.removeprefix("./"), "line": int(row),
                         "code": code, "message": text, "severity": flake8_severity(code)})
    return findings


def parse_bandit(output: str) -> list[dict]:
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return []
    findings = []
    for res in data.get("results", []):
        severity = "error" if res.get("issue_severity") in ("HIGH", "MEDIUM") else "warning"
        findings.append({"tool": "bandit", "file": res["filename"].removeprefix("./"),
                         "line": res["line_number"], "code": res["test_id"],
                         "message": res["issue_text"], "severity": severity})
    return findings


def run_flake8(workdir: Path) -> list[dict]:
    proc = subprocess.run([sys.executable, "-m", "flake8", "--max-line-length=120",
                           f"--format={FLAKE8_FORMAT}", "."],
                          cwd=workdir, capture_output=True, text=True, timeout=300)
    return parse_flake8(proc.stdout)


def run_bandit(workdir: Path) -> list[dict]:
    proc = subprocess.run([sys.executable, "-m", "bandit", "-r", ".", "-f", "json", "-q"],
                          cwd=workdir, capture_output=True, text=True, timeout=300)
    return parse_bandit(proc.stdout)


# ---------------------------------------------------------------- AI review (optional)
def ai_review(diff_text: str) -> str | None:
    """Ask an LLM for a short review of the diff. Skipped when no API key is configured."""
    if not LLM_API_KEY or not diff_text.strip():
        return None
    prompt = ("You are a senior code reviewer. Review this pull request diff. List the 3-5 most important "
              "problems (bugs, security, readability) as short markdown bullet points, then one line of "
              "overall verdict. Be concise.\n\n" + diff_text[:12000])
    try:
        resp = requests.post("https://api.anthropic.com/v1/messages", timeout=60, headers={
            "x-api-key": LLM_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json",
        }, json={"model": LLM_MODEL, "max_tokens": 800, "messages": [{"role": "user", "content": prompt}]})
        resp.raise_for_status()
        blocks = resp.json().get("content", [])
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip() or None
    except requests.RequestException as exc:
        log.warning("AI review skipped: %s", exc)
        return None


# ---------------------------------------------------------------- job processing
def analyse(job: dict) -> dict:
    started = time.time()
    workdir = Path(tempfile.mkdtemp(prefix="review-"))
    try:
        if job.get("demo"):
            for sample in SAMPLES_DIR.glob("*.py"):
                shutil.copy(sample, workdir / sample.name)
            diff_text = ""
        else:
            diff_text = fetch_pr_files(job, workdir)
        files_analyzed = len(list(workdir.rglob("*.py")))
        findings = run_flake8(workdir) + run_bandit(workdir) if files_analyzed else []
        findings.sort(key=lambda f: (f["file"], f["line"]))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    errors = sum(1 for f in findings if f["severity"] == "error")
    return {
        **job,
        "files_analyzed": files_analyzed,
        "findings": findings,
        "total_issues": len(findings),
        "errors": errors,
        "warnings": len(findings) - errors,
        "ai_summary": ai_review(diff_text),
        "duration_seconds": round(time.time() - started, 2),
    }


def process_job(job: dict) -> None:
    started = time.time()
    try:
        result = analyse(job)
        for f in result["findings"]:
            FINDINGS.labels(f["severity"]).inc()
        try:
            requests.post(f"{NOTIFICATION_URL}/notify", json=result, timeout=30).raise_for_status()
        except requests.RequestException as exc:
            log.warning("Notification failed for job %s: %s", job.get("job_id"), exc)
        requests.post(f"{REPORT_URL}/internal/reports", json=result, timeout=30).raise_for_status()
        JOBS.labels("success").inc()
        log.info("Job %s done: %s issues in %ss", job.get("job_id"), result["total_issues"],
                 result["duration_seconds"])
    except Exception:  # keep the worker alive whatever happens to one job
        JOBS.labels("failed").inc()
        log.exception("Job %s failed", job.get("job_id"))
    finally:
        DURATION.observe(time.time() - started)


def main() -> None:
    start_http_server(METRICS_PORT)  # exposes /metrics for Prometheus and the k8s probes
    queue = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    log.info("Worker started, waiting for jobs on '%s'", QUEUE_NAME)
    while True:
        try:
            QUEUE_LENGTH.set(queue.llen(QUEUE_NAME))
            item = queue.brpop(QUEUE_NAME, timeout=5)
        except redis.ConnectionError as exc:
            log.warning("Redis unavailable: %s", exc)
            time.sleep(3)
            continue
        if item:
            process_job(json.loads(item[1]))


if __name__ == "__main__":
    main()
