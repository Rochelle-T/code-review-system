# Code Review Automation Tool

A microservices system that automatically reviews GitHub pull requests. When a PR is opened or updated, GitHub sends a webhook. The system queues a review job and runs static analysis (flake8 + bandit), plus an optional AI review. It then posts the findings as a PR comment and stores them for a dashboard. Everything runs on Kubernetes, with autoscaling, CI/CD and Prometheus/Grafana monitoring.

## How the abstract maps to the code

| Component | Implementation | Folder / file |
|---|---|---|
| API Gateway | Nginx: routing + JWT check via `auth_request` | `k8s/30-gateway.yaml` |
| Auth Service | FastAPI, issues and verifies JWTs | `services/auth-service` |
| Webhook Listener | FastAPI, verifies GitHub HMAC signature, queues jobs | `services/webhook-listener` |
| Message Queue | Redis list `review-jobs` (LPUSH / BRPOP) | `k8s/10-redis.yaml` |
| Code Analysis | Python worker: flake8 + bandit + optional LLM review | `services/code-analysis` |
| Notification | FastAPI, posts PR comment via GitHub API | `services/notification-service` |
| Report Service | FastAPI, stores and serves results | `services/report-service` |
| Database | PostgreSQL, `reports` table | `k8s/11-postgres.yaml` |
| Frontend | React dashboard served by Nginx | `frontend/index.html` |
| Containerisation | One Dockerfile per service | `services/*/Dockerfile` |
| Orchestration | Kubernetes Deployments + Services | `k8s/` |
| Autoscaling | HPA on Code Analysis, 1 to 5 pods at 50% CPU | `k8s/40-hpa.yaml` |
| Configuration | ConfigMap (`app-config`) | `k8s/01-configmap.yaml` |
| Secrets | Secret (`app-secrets`), created from `.env` | `scripts/deploy.sh` |
| CI/CD | GitHub Actions: lint, test, build, push to GHCR, optional deploy | `.github/workflows/ci-cd.yml` |
| Monitoring | Prometheus scrapes `/metrics` every 15s; Grafana dashboard | `k8s/monitoring/` |

### Request flow

1. A pull request is opened on GitHub, which sends a webhook to the API Gateway at `/webhook`.
2. The gateway forwards it to the Webhook Listener, which checks the signature and pushes a job onto the Redis queue (`LPUSH`).
3. A Code Analysis pod takes the job (`BRPOP`), downloads the changed `.py` files and runs flake8, bandit and the optional AI review.
4. The results go to the Notification Service, which posts the PR comment.
5. The results also go to the Report Service, which stores them in PostgreSQL.
6. The dashboard reads them through the gateway at `/api/reports`, which requires a JWT.

---

## Prerequisites

You need a laptop with **at least 8 GB RAM** (16 GB is comfortable) and these tools:

| Tool | Get it |
|---|---|
| Docker Desktop (running) | https://www.docker.com/products/docker-desktop |
| minikube | https://minikube.sigs.k8s.io/docs/start/ |
| kubectl | https://kubernetes.io/docs/tasks/tools/ |
| Helm | https://helm.sh/docs/intro/install/ |
| Python 3.10+ | https://www.python.org/downloads/ (for the load test script) |
| Node.js 18+ | https://nodejs.org (only for `npx smee-client`) |
| Git | https://git-scm.com |

**Windows users:** run every command below in **Git Bash** (installed with Git), not PowerShell. Use `python` or `py` wherever this guide says `python`.

Check that everything is installed:

```bash
docker --version && minikube version && kubectl version --client && helm version && python --version && node --version
```

---

## Part A: Run the system (about 40 minutes, mostly waiting)

### A1. Configure secrets

```bash
cd code-review-system
cp .env.example .env
```

Open `.env` and set your own values. For now you can leave `GITHUB_TOKEN` as it is; you'll fill it in during Part B. `ADMIN_PASSWORD` is your dashboard login password.

### A2. Start the cluster

```bash
bash scripts/setup-cluster.sh
```

This starts minikube with 4 CPUs and 6 GB of memory, and enables metrics-server, which the HPA needs. If your laptop has only 8 GB RAM, edit the script to use `--memory=4096`.

### A3. Build the images (5–10 min the first time)

```bash
bash scripts/build-images.sh
```

### A4. Deploy

```bash
bash scripts/deploy.sh
```

When it finishes, all pods should show `Running` and `1/1`. You can check with `docker version`.

### A5. Install monitoring (5–10 min)

```bash
bash scripts/install-monitoring.sh
```

### A6. Open the ports, then leave this terminal open

```bash
bash scripts/port-forward.sh
```

### A7. Smoke test (in a new terminal)

1. Open http://localhost:8080 and sign in as `admin` with your `ADMIN_PASSWORD`.
2. Send 5 fake pull requests:
   ```bash
   python scripts/load_test.py --count 5
   ```
3. Within a few seconds, 5 reviews tagged "load test" appear on the dashboard. Click one to see its findings.

If that works, the gateway, auth, webhook, queue, analysis, report service and database all work.

---

## Part B: Connect a real GitHub repository (about 15 minutes)

### B1. Create a test repo and a token

1. On GitHub, create a new **public** repo, e.g. `review-playground`, and tick "Add a README".
2. Go to **Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token**. Select the `repo` scope and copy the token.
3. Put the token in `.env` as `GITHUB_TOKEN=ghp_...`, then redeploy:
   ```bash
   bash scripts/deploy.sh
   ```
   If `port-forward.sh` stopped during the redeploy, restart it.

### B2. Let GitHub reach your laptop

1. Go to https://smee.io and click **Start a new Channel**. Copy the URL (`https://smee.io/AbC123...`).
2. In a new terminal, run the following and keep it running:
   ```bash
   npx smee-client --url https://smee.io/YOUR_CHANNEL --target http://localhost:8080/webhook
   ```

(ngrok also works: `ngrok http 8080`, then use `https://<id>.ngrok-free.app/webhook` as the payload URL.)

### B3. Add the webhook

In the test repo, go to **Settings → Webhooks → Add webhook** and fill in:

| Field | Value |
|---|---|
| Payload URL | your smee.io URL |
| Content type | `application/json` |
| Secret | exactly the `WEBHOOK_SECRET` from `.env` |
| Events | "Let me select individual events" → tick **Pull requests** only |

Save. GitHub sends a "ping"; the smee terminal should show it delivered with status 200.

### B4. Open a test pull request

1. In the test repo, click **Add file → Upload files** and upload `demo/payment_handler.py` from this project.
2. Choose **Create a new branch**, then **Propose changes**, then **Create pull request**.
3. Within 10–20 seconds a comment appears on the PR with a table of issues: an undefined variable, `eval`, a hard-coded password, `shell=True`, MD5, and others. The review also appears on the dashboard.

To watch it happen live:

```bash
kubectl logs -f deploy/webhook-listener -n code-review-system
kubectl logs -f deploy/code-analysis -n code-review-system
```

**Optional AI review:** put an Anthropic API key in `LLM_API_KEY` in `.env` and redeploy. PR comments will then include an "AI review" section.

---

## Part C: CI/CD with GitHub Actions (about 10 minutes)

1. Create a GitHub repo for **this project**, separate from the test repo, and push the code:
   ```bash
   git init && git add . && git commit -m "Code review automation"
   git branch -M main
   git remote add origin https://github.com/<you>/code-review-system.git
   git push -u origin main
   ```
   `.env` is git-ignored, so your secrets are not pushed.
2. Open the **Actions** tab. The workflow runs three jobs:
   - **Lint and test:** flake8 and 12 unit tests.
   - **Build and push:** builds 6 images and pushes them to GitHub Container Registry. You can see them under your profile → **Packages**.
   - **Deploy:** skipped unless enabled (see step 3).
3. **Optional automatic deploy to your laptop's cluster:**
   1. Go to repo **Settings → Actions → Runners → New self-hosted runner** and follow the commands on the laptop running minikube.
   2. Go to **Settings → Secrets and variables → Actions → Variables** and add `ENABLE_DEPLOY` = `true`.

   Every push to `main` will then redeploy automatically.

---

## Part D: Demo script (about 15 minutes)

Before presenting, run through this once. Have these open:
- **Terminal 1:** `port-forward.sh` running.
- **Terminal 2:** the smee client running.
- **Terminal 3:** free for commands.
- **Browser tabs:** the dashboard, Grafana (dashboard "Code Review Automation"), the test repo, and the GitHub Actions page.

**1. Architecture (2 min).** Show the architecture diagram, then the running system:

```bash
kubectl get all -n code-review-system
```

Point out that each microservice is a separate Deployment with its own Service, Redis is the queue, and Postgres is the database.

**2. Live code review (3 min).**
1. Open a PR in the test repo, or push a new commit to the existing PR.
2. Show the smee terminal receiving the event, then run `kubectl logs deploy/code-analysis -n code-review-system --tail=5`.
3. Show the PR comment that appears.
4. Show the same review on the dashboard and click it to see the findings grouped by file.

**3. Security (1 min).**
- Run `curl -i http://localhost:8080/api/reports`. It returns **401**, because the gateway asks the Auth Service to validate a JWT.
- A webhook with a wrong signature is also rejected with 401.

**4. Autoscaling (4 min).** Open two extra terminals:

```bash
# Terminal A
kubectl get hpa -n code-review-system -w
# Terminal B
kubectl get pods -n code-review-system -l app=code-analysis -w
```

Then fire a burst of 400 pull requests:

```bash
python scripts/load_test.py --count 400
```

- Within about 30–60 seconds, CPU goes above the 50% target and the HPA scales Code Analysis from **1 → 3 → 5 pods**.
- On Grafana, show the pod count rising, the queue length draining faster, and CPU per pod.
- About 1–2 minutes after the queue empties, the HPA scales back down to 1.

**5. Configuration and secrets (1 min).**

```bash
kubectl get configmap app-config -n code-review-system -o yaml
kubectl get secret app-secrets -n code-review-system
```

The ConfigMap holds plain configuration. The Secret holds the DB password, GitHub token, webhook secret and LLM key, which are not stored in code or in git.

**6. Self-healing (1 min).**

```bash
kubectl delete pod -l app=report-service -n code-review-system
kubectl get pods -n code-review-system -w
```

Kubernetes recreates the pod immediately.

**7. CI/CD (2 min).** Make a small change, for example the dashboard title in `frontend/index.html`, then commit and push. Show the Actions run going green and the new images in Packages.

**8. Monitoring (1 min).** In Prometheus (http://localhost:9090 → Status → Targets), show all services being scraped. In Grafana, show request rate and p95 latency per service.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Pod stuck in `ImagePullBackOff` / `ErrImageNeverPull` | The images aren't inside minikube. Run `bash scripts/build-images.sh`, then `bash scripts/deploy.sh`. |
| Pod in `CrashLoopBackOff` | Check the logs with `kubectl logs deploy/<name> -n code-review-system`. Usually a missing `.env` value; fix it and run `deploy.sh` again. |
| HPA shows `<unknown>/50%` | Metrics-server needs 1–2 minutes after start. Check `minikube addons list` shows metrics-server enabled. |
| HPA doesn't scale | Send more load (`--count 1000`). Check with `kubectl top pods -n code-review-system`. |
| Load test: "N failed" | `port-forward.sh` isn't running, or `.env` `WEBHOOK_SECRET` was changed without running `deploy.sh`. |
| GitHub ping shows 401 | The webhook "Secret" in GitHub doesn't match `WEBHOOK_SECRET` in `.env`. |
| PR comment not posted | Check `kubectl logs deploy/notification-service -n code-review-system`. A 401/403/404 means the token is wrong or lacks the `repo` scope. |
| Dashboard says "Can't reach the API" | The port-forward died, which happens after a redeploy. Restart `port-forward.sh`. |
| Grafana dashboard missing | Re-run `kubectl apply -f k8s/monitoring/grafana-dashboard.yaml` and refresh Grafana after 30s. |
| Prometheus/Grafana pods `Pending` | Not enough memory. Run `minikube delete`, then start again with more memory. |
| Need a clean slate | Run `bash scripts/teardown.sh`, then `deploy.sh` and `install-monitoring.sh`. |

Run the unit tests locally:

```bash
pip install -r requirements-dev.txt && pytest -v
```

## Project structure

```
code-review-system/
├── services/
│   ├── auth-service/            JWT login / verify
│   ├── webhook-listener/        GitHub webhook → Redis queue
│   ├── code-analysis/           queue worker: flake8 + bandit + AI (samples/ = demo code)
│   ├── notification-service/    posts PR comments
│   └── report-service/          PostgreSQL storage + API
├── frontend/                    React dashboard (single page, served by Nginx)
├── k8s/                         namespace, configmap, all deployments/services, gateway, HPA
│   └── monitoring/              Helm values, ServiceMonitor, Grafana dashboard
├── scripts/                     setup, build, deploy, monitoring, port-forward, load test
├── tests/                       unit tests run by CI
├── demo/payment_handler.py      file to upload in a test PR
└── .github/workflows/ci-cd.yml  GitHub Actions pipeline
```

## Limitations and future work

- **Python only:** analysis covers Python files. Other languages could be added with Semgrep or ESLint.
- **Postgres storage:** Postgres uses `emptyDir`, so data resets if its pod restarts. A PersistentVolumeClaim would fix this.
- **Queue reliability:** `BRPOP` can lose a job if a worker crashes mid-analysis. `BLMOVE` with a processing list would add retries.
- **Single user:** the dashboard has one admin user. A users table and OAuth login with GitHub would support more.
- **Cluster:** the system runs on a single-node minikube cluster. A managed cluster with Ingress and TLS would be the next step.
