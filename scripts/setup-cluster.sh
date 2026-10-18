#!/usr/bin/env bash
# Step 1: start a local Kubernetes cluster with enough resources for the app + monitoring.
set -euo pipefail
minikube start --cpus=4 --memory=6144 --driver=docker
minikube addons enable metrics-server   # required by the Horizontal Pod Autoscaler
kubectl get nodes
echo "Cluster ready. Next: ./scripts/build-images.sh"
