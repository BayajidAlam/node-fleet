#!/bin/bash
# SmartScale K3s Autoscaler - Scale-Up Test
# Simulates high CPU load to trigger autoscaler scale-up event
# Expected: New nodes should join cluster within 3 minutes

set -e

echo "============================================================"
echo "SmartScale K3s Autoscaler - Scale-Up Test"
echo "============================================================"
echo ""

# Configuration
STRESS_REPLICAS=10
STRESS_TIMEOUT=600  # 10 minutes
CHECK_INTERVAL=10   # Check every 10 seconds

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Get current node count
initial_nodes=$(kubectl get nodes --no-headers 2>/dev/null | grep -c "Ready" || echo "0")

echo -e "${YELLOW}Current Configuration:${NC}"
echo "  Initial Node Count: $initial_nodes"
echo "  Stress Replicas: $STRESS_REPLICAS"
echo "  Stress Duration: ${STRESS_TIMEOUT}s ($(($STRESS_TIMEOUT / 60)) minutes)"
echo ""

# Verify kubectl access
if ! kubectl get nodes &>/dev/null; then
    echo -e "${RED}ERROR: Cannot connect to Kubernetes cluster${NC}"
    echo "Please ensure kubectl is configured and cluster is accessible"
    exit 1
fi

echo -e "${YELLOW}Step 1: Deploying CPU stress workload...${NC}"
echo ""

# Create stress deployment
cat <<EOF | kubectl apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: cpu-burn
  labels:
    app: cpu-burn
    test: scale-up
spec:
  replicas: $STRESS_REPLICAS
  selector:
    matchLabels:
      app: cpu-burn
  template:
    metadata:
      labels:
        app: cpu-burn
    spec:
      containers:
      - name: stress
        image: progrium/stress
        args:
          - "--cpu"
          - "2"
          - "--timeout"
          - "${STRESS_TIMEOUT}s"
        resources:
          requests:
            cpu: "500m"
            memory: "256Mi"
          limits:
            cpu: "1000m"
            memory: "512Mi"
EOF

echo -e "${GREEN}✓ CPU stress deployment created${NC}"
echo ""

# Wait for pods to start
echo -e "${YELLOW}Step 2: Waiting for stress pods to start...${NC}"
kubectl wait --for=condition=ready pod -l app=cpu-burn --timeout=120s 2>/dev/null || true
echo ""

# Get pod status
running_pods=$(kubectl get pods -l app=cpu-burn --no-headers 2>/dev/null | grep -c "Running" || echo "0")
pending_pods=$(kubectl get pods -l app=cpu-burn --no-headers 2>/dev/null | grep -c "Pending" || echo "0")

echo "Pod Status:"
echo "  Running: $running_pods"
echo "  Pending: $pending_pods"
echo ""

echo -e "${YELLOW}Step 3: Monitoring cluster for autoscaler response...${NC}"
echo ""
echo "Expected Behavior:"
echo "  1. High CPU usage detected (> 70%)"
echo "  2. Lambda autoscaler triggers scale-up"
echo "  3. New EC2 instances launch"
echo "  4. New nodes join k3s cluster within 3 minutes"
echo ""
echo "Monitoring for 5 minutes (Ctrl+C to stop early)..."
echo ""

# Monitor node count for 5 minutes
start_time=$(date +%s)
max_wait=300  # 5 minutes
scaled_up=false

while [ $(($(date +%s) - start_time)) -lt $max_wait ]; do
    current_nodes=$(kubectl get nodes --no-headers 2>/dev/null | grep -c "Ready" || echo "0")
    current_time=$(date +%H:%M:%S)
    elapsed=$(($(date +%s) - start_time))
    
    # Show current status
    echo "[$current_time] (${elapsed}s elapsed) Nodes: $current_nodes | Pods: Running=$running_pods, Pending=$pending_pods"
    
    # Check if scale-up occurred
    if [ "$current_nodes" -gt "$initial_nodes" ]; then
        if [ "$scaled_up" = false ]; then
            echo ""
            echo -e "${GREEN}✓ SCALE-UP DETECTED!${NC}"
            echo "  Initial nodes: $initial_nodes"
            echo "  Current nodes: $current_nodes"
            echo "  New nodes added: $((current_nodes - initial_nodes))"
            echo "  Time to scale: ${elapsed}s ($(($elapsed / 60))m $(($elapsed % 60))s)"
            echo ""
            scaled_up=true
        fi
    fi
    
    # Update pod counts
    running_pods=$(kubectl get pods -l app=cpu-burn --no-headers 2>/dev/null | grep -c "Running" || echo "0")
    pending_pods=$(kubectl get pods -l app=cpu-burn --no-headers 2>/dev/null | grep -c "Pending" || echo "0")
    
    sleep $CHECK_INTERVAL
done

echo ""
echo "============================================================"
echo "Test Complete"
echo "============================================================"

final_nodes=$(kubectl get nodes --no-headers 2>/dev/null | grep -c "Ready" || echo "0")

echo ""
echo "Results:"
echo "  Initial nodes: $initial_nodes"
echo "  Final nodes: $final_nodes"
echo "  Nodes added: $((final_nodes - initial_nodes))"
echo ""

if [ "$final_nodes" -gt "$initial_nodes" ]; then
    echo -e "${GREEN}✓ TEST PASSED: Autoscaler successfully scaled up${NC}"
else
    echo -e "${RED}✗ TEST FAILED: No scale-up detected within 5 minutes${NC}"
    echo ""
    echo "Troubleshooting:"
    echo "  1. Check CloudWatch logs: /aws/lambda/smartscale-autoscaler"
    echo "  2. Verify Prometheus metrics: kubectl port-forward -n monitoring svc/prometheus 9090:9090"
    echo "  3. Check DynamoDB state table for lock issues"
    echo "  4. Verify Lambda EventBridge trigger is active"
fi

echo ""
echo "Next Steps:"
echo "  1. View Grafana dashboards to see CPU metrics"
echo "  2. Check CloudWatch for Lambda execution logs"
echo "  3. Run: kubectl get nodes -o wide"
echo "  4. Cleanup: kubectl delete deployment cpu-burn"
echo ""
echo "To stop the stress test now:"
echo "  kubectl delete deployment cpu-burn"
echo "============================================================"
