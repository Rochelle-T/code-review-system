#!/usr/bin/env bash
# Step 2: build every service image directly inside minikube (no registry needed).
set -euo pipefail
cd "$(dirname "$0")/.."
for svc in auth-service webhook-listener code-analysis notification-service report-service; do
  echo "==> Building codereview/$svc"
  minikube image build -t "codereview/$svc:latest" "services/$svc"
done
echo "==> Building codereview/frontend"
minikube image build -t codereview/frontend:latest frontend
echo "All images built. Next: ./scripts/deploy.sh"
