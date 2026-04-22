#!/bin/bash
set -e

# Ensure KUBECONFIG is set
if [ -z "$KUBECONFIG" ]; then
    echo "Error: KUBECONFIG environment variable is not set."
    echo "Usage: export KUBECONFIG=$(pwd)/kubeconfig.yaml"
    exit 1
fi

echo "Deploying Monitoring Stack (Prometheus & Grafana)..."

# Ensure monitoring namespace exists
kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -

# Create ConfigMap for cost-exporter script (must exist before the Deployment)
echo "Creating cost-exporter ConfigMap from source..."
kubectl create configmap cost-exporter-script \
  --from-file=cost-exporter.py=monitoring/cost_exporter.py \
  -n monitoring --dry-run=client -o yaml | kubectl apply -f -

# Create ConfigMap for Grafana dashboards
echo "Creating Grafana dashboards ConfigMap..."
kubectl create configmap grafana-dashboards \
  --from-file=monitoring/grafana-dashboards/ \
  -n monitoring --dry-run=client -o yaml | kubectl apply -f -

# Apply Prometheus recording rules
kubectl apply -f monitoring/prometheus-rules.yaml -n monitoring || true

# Apply cost-exporter deployment
kubectl apply -f monitoring/cost-exporter-deployment.yaml

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
