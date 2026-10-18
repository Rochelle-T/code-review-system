#!/usr/bin/env bash
# Remove everything (keeps the minikube cluster). Use 'minikube delete' to remove the cluster too.
set -uo pipefail
kubectl delete namespace code-review-system --ignore-not-found
helm uninstall monitoring -n monitoring 2>/dev/null
kubectl delete namespace monitoring --ignore-not-found
