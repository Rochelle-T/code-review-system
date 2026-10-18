#!/usr/bin/env python3
"""Send many signed *demo* pull-request webhooks through the API Gateway.

Each one becomes a real review job (analysing the bundled sample files), which loads the
Code Analysis Service and makes the HPA add pods. Demo jobs never call GitHub.
Uses only the Python standard library.

    python scripts/load_test.py --count 400
"""
import argparse
import concurrent.futures
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path


def read_webhook_secret() -> str:
    if os.getenv("WEBHOOK_SECRET"):
        return os.environ["WEBHOOK_SECRET"]
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.strip().startswith("WEBHOOK_SECRET="):
                return line.split("=", 1)[1].strip()
    raise SystemExit("WEBHOOK_SECRET not found. Set it in .env or as an environment variable.")


def send(url: str, secret: str, i: int) -> int:
    number = 1000 + i
    payload = {
        "action": "opened",
        "demo": True,
        "repository": {"full_name": "demo/sample-repo"},
        "pull_request": {
            "number": number,
            "title": f"Load test PR {number}",
            "html_url": f"https://github.com/demo/sample-repo/pull/{number}",
            "head": {"sha": hashlib.sha1(str(number).encode()).hexdigest()},
            "user": {"login": "load-tester"},
        },
    }
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "X-GitHub-Event": "pull_request",
        "X-Hub-Signature-256": signature,
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except OSError:
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=400, help="number of fake PR events (default 400)")
    parser.add_argument("--url", default="http://localhost:8080/webhook", help="gateway webhook URL")
    parser.add_argument("--workers", type=int, default=20, help="parallel senders")
    args = parser.parse_args()

    secret = read_webhook_secret()
    print(f"Sending {args.count} demo PR events to {args.url} ...")
    start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        statuses = list(pool.map(lambda i: send(args.url, secret, i), range(args.count)))
    ok = statuses.count(200)
    print(f"Done in {time.time() - start:.1f}s: {ok} queued, {len(statuses) - ok} failed")
    if ok < len(statuses):
        print("Failures: is the gateway port-forward running, and does WEBHOOK_SECRET match the cluster secret?")
    print("Now watch:  kubectl get hpa -n code-review-system -w")


if __name__ == "__main__":
    main()
