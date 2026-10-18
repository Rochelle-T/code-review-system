import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from conftest import load_service

hook = load_service("webhook-listener", "app.py", "webhook_app")
SECRET = "test-secret"


class FakeRedis:
    def __init__(self):
        self.items = []

    def lpush(self, _queue, value):
        self.items.append(value)


@pytest.fixture
def client(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(hook, "WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(hook, "r", fake)
    c = TestClient(hook.app)
    c.fake = fake
    return c


def signed_post(client, payload, event="pull_request", secret=SECRET):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/webhook", content=body, headers={"X-GitHub-Event": event, "X-Hub-Signature-256": sig})


PR_EVENT = {"action": "opened", "repository": {"full_name": "acme/app"},
            "pull_request": {"number": 7, "title": "Add feature", "html_url": "https://x", "head": {"sha": "abc123"},
                             "user": {"login": "dev"}}}


def test_valid_pr_event_is_queued(client):
    resp = signed_post(client, PR_EVENT)
    assert resp.json()["status"] == "queued"
    job = json.loads(client.fake.items[0])
    assert job["repo"] == "acme/app" and job["pr_number"] == 7 and job["demo"] is False


def test_bad_signature_rejected(client):
    resp = signed_post(client, PR_EVENT, secret="wrong")
    assert resp.status_code == 401 and client.fake.items == []


def test_closed_pr_ignored(client):
    resp = signed_post(client, {**PR_EVENT, "action": "closed"})
    assert resp.json()["status"] == "ignored" and client.fake.items == []
