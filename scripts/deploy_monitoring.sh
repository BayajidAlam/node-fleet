#!/bin/bash
set -e

# Ensure KUBECONFIG is set
if [ -z "$KUBECONFIG" ]; then
    echo "Error: KUBECONFIG environment variable is not set."
    echo "Usage: export KUBECONFIG=$(pwd)/kubeconfig.yaml"
    exit 1
fi

echo "Deploying Monitoring Stack (Prometheus & Grafana)..."

# Apply Infrastructure (Deployment, Service)
kubectl apply -R -f gitops/infrastructure/

# Apply Monitoring Configs (Roles, Alerts, Scrapers)
kubectl apply -R -f gitops/monitoring/

# Fix potential race condition for Grafana namespace
echo "Ensuring Grafana deployment..."
kubectl apply -f gitops/infrastructure/grafana.yaml || true

echo "Waiting for pods to be ready..."
kubectl wait --for=condition=ready pod -l app=prometheus -n monitoring --timeout=60s
kubectl wait --for=condition=ready pod -l app=grafana -n monitoring --timeout=60s

echo "✅ Monitoring Stack Deployed Successfully!"
echo "Prometheus is available at NodePort 30090"
echo "Grafana is available at NodePort 30300"
