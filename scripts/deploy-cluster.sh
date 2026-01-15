#!/bin/bash
set -e

# Automated K3s Cluster Setup and Application Deployment
# This script waits for K3s initialization and deploys all monitoring and demo apps

MASTER_IP="18.142.249.81"
SSH_KEY="node-fleet-key.pem"
MAX_RETRIES=20
RETRY_DELAY=30

echo "=== SmartScale K3s Automated Deployment ==="
echo "Master IP: $MASTER_IP"
echo ""

# Function to check K3s status
check_k3s_ready() {
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=10 \
        ubuntu@$MASTER_IP "sudo systemctl is-active k3s" 2>/dev/null
}

# Wait for K3s to be ready
echo "[1/6] Waiting for K3s master to initialize..."
for i in $(seq 1 $MAX_RETRIES); do
    echo "  Attempt $i/$MAX_RETRIES..."
    
    if check_k3s_ready | grep -q "active"; then
        echo "  ✓ K3s is active!"
        break
    fi
    
    if [ $i -eq $MAX_RETRIES ]; then
        echo "  ✗ K3s failed to start after $((MAX_RETRIES * RETRY_DELAY)) seconds"
        exit 1
    fi
    
    sleep $RETRY_DELAY
done

# Get kubeconfig from master
echo ""
echo "[2/6] Fetching kubeconfig from master..."
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no ubuntu@$MASTER_IP \
    "sudo cat /etc/rancher/k3s/k3s.yaml" > /tmp/k3s-kubeconfig.yaml

# Update server address in kubeconfig
sed -i "s/127.0.0.1/$MASTER_IP/g" /tmp/k3s-kubeconfig.yaml
export KUBECONFIG=/tmp/k3s-kubeconfig.yaml

echo "  ✓ Kubeconfig configured"

# Verify cluster access
echo ""
echo "[3/6] Verifying cluster access..."
kubectl get nodes
echo ""

# Deploy Prometheus
echo "[4/6] Deploying Prometheus..."
kubectl apply -f gitops/infrastructure/prometheus/
echo "  ✓ Prometheus deployed"

# Deploy Grafana  
echo ""
echo "[5/6] Deploying Grafana..."
kubectl apply -f gitops/infrastructure/grafana/
echo "  ✓ Grafana deployed"

# Deploy demo application
echo ""
echo "[6/6] Deploying demo application..."
kubectl apply -f gitops/apps/demo-app/
echo "  ✓ Demo app deployed"

# Wait for pods to be ready
echo ""
echo "Waiting for pods to be ready..."
kubectl wait --for=condition=ready pod -l app=prometheus --timeout=120s -n monitoring || true
kubectl wait --for=condition=ready pod -l app=grafana --timeout=120s -n monitoring || true
kubectl wait --for=condition=ready pod -l app=demo-app --timeout=120s || true

# Show deployment status
echo ""
echo "=== Deployment Status ==="
echo ""
echo "Nodes:"
kubectl get nodes
echo ""
echo "Monitoring Pods:"
kubectl get pods -n monitoring
echo ""
echo "Demo App:"
kubectl get pods,svc -l app=demo-app
echo ""
echo "Prometheus URL: http://$MASTER_IP:30090"
echo "Grafana URL: http://$MASTER_IP:30091"
echo ""
echo "✓ Automated deployment complete!"
