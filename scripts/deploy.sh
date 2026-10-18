#!/usr/bin/env bash
# Step 3: create the Secret from .env and apply all Kubernetes manifests.
set -euo pipefail
cd "$(dirname "$0")/.."
NS=code-review-system

if [ ! -f .env ]; then
  echo "Missing .env. Run: cp .env.example .env   then fill in the values." >&2
  exit 1
fi

kubectl apply -f k8s/00-namespace.yaml

# Strip Windows line endings so secrets don't end with a hidden \r
CLEAN_ENV="$(mktemp)"
sed 's/\r$//' .env > "$CLEAN_ENV"
kubectl -n "$NS" create secret generic app-secrets --from-env-file="$CLEAN_ENV" \
  --dry-run=client -o yaml | kubectl apply -f -
rm -f "$CLEAN_ENV"

kubectl apply -f k8s/
# Restart app pods so they pick up freshly built images / changed secrets.
# (Postgres and Redis are not restarted, so stored reports survive a redeploy.)
kubectl -n "$NS" rollout restart deployment auth-service webhook-listener code-analysis \
  notification-service report-service frontend gateway

echo "Waiting for pods to become ready..."
for d in $(kubectl -n "$NS" get deployments -o name); do
  kubectl -n "$NS" rollout status "$d" --timeout=240s
done
kubectl -n "$NS" get pods
echo
echo "Deployed. Next: ./scripts/port-forward.sh  then open http://localhost:8080"
