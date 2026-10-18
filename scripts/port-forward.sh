#!/usr/bin/env bash
# Opens local ports: app on 8080, Grafana on 3000, Prometheus on 9090. Ctrl+C stops all.
set -uo pipefail
pids=()
cleanup() { kill "${pids[@]}" 2>/dev/null; exit 0; }
trap cleanup INT TERM

kubectl -n code-review-system port-forward svc/gateway 8080:80 >/dev/null & pids+=($!)
if kubectl get ns monitoring >/dev/null 2>&1; then
  kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80 >/dev/null & pids+=($!)
  kubectl -n monitoring port-forward svc/monitoring-kube-prometheus-prometheus 9090:9090 >/dev/null & pids+=($!)
fi
sleep 2
echo "Dashboard:   http://localhost:8080   (admin / ADMIN_PASSWORD from .env)"
echo "Webhook URL: http://localhost:8080/webhook"
echo "Grafana:     http://localhost:3000   (admin / admin)"
echo "Prometheus:  http://localhost:9090"
echo "Leave this running. Press Ctrl+C to stop."
wait
