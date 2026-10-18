#!/usr/bin/env bash
# Step 4: install Prometheus + Grafana (kube-prometheus-stack) and our scrape config + dashboard.
set -euo pipefail
cd "$(dirname "$0")/.."
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
helm repo update prometheus-community
helm upgrade --install monitoring prometheus-community/kube-prometheus-stack \
  --namespace monitoring --create-namespace \
  -f k8s/monitoring/values.yaml --wait --timeout 10m
kubectl apply -f k8s/monitoring/servicemonitor.yaml
kubectl apply -f k8s/monitoring/grafana-dashboard.yaml
echo "Monitoring installed. Grafana: http://localhost:3000 (admin / admin) after ./scripts/port-forward.sh"
