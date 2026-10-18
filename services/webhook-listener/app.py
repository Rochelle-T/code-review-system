"""Webhook Listener: validates GitHub pull-request webhooks and queues review jobs."""
import hashlib
import hmac
import json
import logging
import os
import time
import uuid

import redis
from fastapi import FastAPI, Header, HTTPException, Request
from prometheus_client import Counter
from prometheus_fastapi_instrumentator import Instrumentator

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("webhook-listener")

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
QUEUE_NAME = os.getenv("QUEUE_NAME", "review-jobs")
REVIEW_ACTIONS = {"opened", "synchronize", "reopened"}

r = redis.Redis(host=os.getenv("REDIS_HOST", "localhost"), port=int(os.getenv("REDIS_PORT", "6379")),
                decode_responses=True)

EVENTS = Counter("webhook_events_total", "Webhook events received", ["event", "result"])

app = FastAPI(title="Webhook Listener")
Instrumentator().instrument(app).expose(app)


def verify_signature(body: bytes, signature: str | None, secret: str) -> bool:
    """GitHub signs the raw request body with HMAC-SHA256 using the webhook secret."""
    if not secret or not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def build_job(payload: dict) -> dict:
    pr = payload["pull_request"]
    return {
        "job_id": uuid.uuid4().hex[:12],
        "repo": payload["repository"]["full_name"],
        "pr_number": pr["number"],
        "pr_title": pr.get("title", ""),
        "pr_url": pr.get("html_url", ""),
        "head_sha": pr["head"]["sha"],
        "author": (pr.get("user") or {}).get("login", "unknown"),
        "demo": payload.get("demo") is True,  # only set by scripts/load_test.py
        "queued_at": time.time(),
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(request: Request,
                  x_github_event: str | None = Header(default=None),
                  x_hub_signature_256: str | None = Header(default=None)):
    body = await request.body()
    event = x_github_event or "unknown"

    if not verify_signature(body, x_hub_signature_256, WEBHOOK_SECRET):
        EVENTS.labels(event, "rejected").inc()
        log.warning("Rejected %s event: bad signature", event)
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = json.loads(body or b"{}")
    if event == "ping":
        EVENTS.labels(event, "ok").inc()
        return {"status": "pong"}

    if event != "pull_request" or payload.get("action") not in REVIEW_ACTIONS:
        EVENTS.labels(event, "ignored").inc()
        return {"status": "ignored"}

    job = build_job(payload)
    r.lpush(QUEUE_NAME, json.dumps(job))
    EVENTS.labels(event, "queued").inc()
    log.info("Queued job %s for %s#%s", job["job_id"], job["repo"], job["pr_number"])
    return {"status": "queued", "job_id": job["job_id"]}
