#!/bin/bash
# SmartScale K3s Autoscaler - Scale-Down Test
# Removes load and monitors autoscaler scale-down after cooldown period
# Expected: Nodes should be removed after 10 minutes of low CPU (< 30%)

set -e

echo "============================================================"
echo "SmartScale K3s Autoscaler - Scale-Down Test"
echo "============================================================"
echo ""

# Configuration
COOLDOWN_WAIT=660  # 11 minutes (10 min cooldown + 1 min buffer)
CHECK_INTERVAL=10   # Check every 10 seconds

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Get current node count
initial_nodes=$(kubectl get nodes --no-headers 2>/dev/null | grep -c "Ready" || echo "0")

echo -e "${YELLOW}Current Configuration:${NC}"
echo "  Initial Node Count: $initial_nodes"
echo "  Cooldown Wait: ${COOLDOWN_WAIT}s ($(($COOLDOWN_WAIT / 60)) minutes)"
echo ""

# Verify kubectl access
if ! kubectl get nodes &>/dev/null; then
    echo -e "${RED}ERROR: Cannot connect to Kubernetes cluster${NC}"
    exit 1
fi

echo -e "${YELLOW}Step 1: Removing all stress workloads...${NC}"
echo ""

# Delete any existing stress deployments
kubectl delete deployment cpu-burn --ignore-not-found=true
kubectl delete deployment stress-test --ignore-not-found=true

# Wait for pods to terminate
echo "Waiting for pods to terminate..."
kubectl wait --for=delete pod -l app=cpu-burn --timeout=60s 2>/dev/null || true
echo -e "${GREEN}✓ Workloads removed${NC}"
echo ""

echo -e "${YELLOW}Step 2: Verifying low cluster utilization...${NC}"
echo ""

# Check if there are many pods running
total_pods=$(kubectl get pods --all-namespaces --no-headers 2>/dev/null | grep -c "Running" || echo "0")
echo "Current running pods: $total_pods"

if [ "$total_pods" -gt 50 ]; then
    echo -e "${YELLOW}WARNING: Cluster has many running pods ($total_pods)${NC}"
    echo "Scale-down may not occur if cluster resource usage is still high"
    echo ""
fi

echo -e "${YELLOW}Step 3: Waiting for cooldown period (11 minutes)...${NC}"
echo ""
echo "Why 11 minutes?"
echo "  - Autoscaler requires 10 consecutive minutes of low CPU (< 30%)"
echo "  - Lambda runs every 2 minutes"
echo "  - We wait 11 minutes to ensure at least one check occurs after cooldown"
echo ""
echo "Monitoring cluster state..."
echo ""

# Monitor node count during cooldown
start_time=$(date +%s)
scaled_down=false
scale_down_time=0

while [ $(($(date +%s) - start_time)) -lt $COOLDOWN_WAIT ]; do
    current_nodes=$(kubectl get nodes --no-headers 2>/dev/null | grep -c "Ready" || echo "0")
    current_time=$(date +%H:%M:%S)
    elapsed=$(($(date +%s) - start_time))
    remaining=$((COOLDOWN_WAIT - elapsed))
    
    # Show current status
    echo "[$current_time] (${elapsed}s) Nodes: $current_nodes | Wait remaining: ${remaining}s"
    
    # Check if scale-down occurred
    if [ "$current_nodes" -lt "$initial_nodes" ]; then
        if [ "$scaled_down" = false ]; then
            echo ""
            echo -e "${GREEN}✓ SCALE-DOWN DETECTED!${NC}"
            echo "  Initial nodes: $initial_nodes"
            echo "  Current nodes: $current_nodes"
            echo "  Nodes removed: $((initial_nodes - current_nodes))"
            echo "  Time to scale: ${elapsed}s ($(($elapsed / 60))m $(($elapsed % 60))s)"
            echo ""
            scaled_down=true
            scale_down_time=$elapsed
        fi
    fi
    
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
echo "  Nodes removed: $((initial_nodes - final_nodes))"
echo ""

if [ "$final_nodes" -lt "$initial_nodes" ]; then
    echo -e "${GREEN}✓ TEST PASSED: Autoscaler successfully scaled down${NC}"
    echo "  Scale-down occurred after: $(($scale_down_time / 60))m $(($scale_down_time % 60))s"
    echo "  Within expected timeframe: $([ $scale_down_time -le $COOLDOWN_WAIT ] && echo 'YES' || echo 'NO')"
elif [ "$initial_nodes" -eq 2 ]; then
    echo -e "${YELLOW}⚠ NOTICE: Already at minimum node count (2)${NC}"
    echo "  Cannot scale down below minimum (safety constraint)"
    echo "  This is expected behavior - TEST PASSED"
else
    echo -e "${RED}✗ TEST FAILED: No scale-down detected within 11 minutes${NC}"
    echo ""
    echo "Possible Reasons:"
    echo "  1. Cluster CPU still above 30% threshold"
    echo "  2. Pending pods exist (prevents scale-down)"
    echo "  3. Lambda autoscaler not running/disabled"
    echo "  4. DynamoDB lock contention"
    echo "  5. Critical pods on worker nodes (prevents drain)"
    echo ""
    echo "Troubleshooting:"
    echo "  1. Check CloudWatch logs: aws logs tail /aws/lambda/smartscale-autoscaler --follow"
    echo "  2. Check cluster CPU: kubectl top nodes"
    echo "  3. Check pending pods: kubectl get pods --all-namespaces --field-selector=status.phase=Pending"
    echo "  4. Review DynamoDB state: aws dynamodb get-item --table-name k3s-autoscaler-state --key '{\"cluster_id\":{\"S\":\"smartscale-prod\"}}'"
fi

echo ""
echo "Current Cluster State:"
kubectl get nodes -o wide 2>/dev/null || echo "Failed to get nodes"
echo ""

echo "Next Steps:"
echo "  1. View Grafana dashboards to confirm low CPU metrics"
echo "  2. Check CloudWatch for Lambda scaling decisions"
echo "  3. Verify DynamoDB state table last_scale_action"
echo "  4. If test failed, wait longer and check again"
echo "============================================================"
