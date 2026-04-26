#!/bin/bash

# Resolve repo root regardless of where script is invoked from
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Configuration
MASTER_IP="${MASTER_IP:-${1}}"
STOP_MODE=false
for arg in "$@"; do
    if [ "$arg" = "--stop" ]; then
        STOP_MODE=true
    fi
done

if [ -z "$MASTER_IP" ]; then
    echo "Error: MASTER_IP env var or first argument is required."
    echo "Usage: MASTER_IP=<ip> ./tests/deploy_load_test.sh [--stop]"
    echo "       --stop: delete load generator to trigger scale-down"
    exit 1
fi

# Auto-find key file: explicit env > repo root > current dir
if [ -z "$KEY_FILE" ]; then
    if [ -f "$REPO_ROOT/node-fleet-key.pem" ]; then
        KEY_FILE="$REPO_ROOT/node-fleet-key.pem"
    elif [ -f "node-fleet-key.pem" ]; then
        KEY_FILE="node-fleet-key.pem"
    else
        echo "Error: node-fleet-key.pem not found in repo root or current directory."
        exit 1
    fi
fi

MANIFEST="$REPO_ROOT/tests/load-generator.yaml"
if [ ! -f "$MANIFEST" ]; then
    echo "Error: tests/load-generator.yaml not found at $MANIFEST"
    exit 1
fi

echo "==================================================="
echo "   Remote Load Test Deployer"
echo "==================================================="
echo "Target Master: $MASTER_IP"

# --stop: delete load generator → CPU drops → scale-down triggers (~10-20min)
if [ "$STOP_MODE" = true ]; then
    echo "→ Stopping load generator..."
    ssh -i "$KEY_FILE" -o StrictHostKeyChecking=no -o IdentitiesOnly=yes \
        ubuntu@$MASTER_IP "sudo k3s kubectl delete deployment load-generator -n default --ignore-not-found=true"
    echo "==================================================="
    echo "✅ Load Generator Stopped. Autoscaler will scale-down in ~10-20 min."
    echo "==================================================="
    exit 0
fi

# 1. Copy the manifest to the master node
echo "→ Copying load-generator.yaml to master node..."
scp -i "$KEY_FILE" -o StrictHostKeyChecking=no -o IdentitiesOnly=yes \
    "$MANIFEST" ubuntu@$MASTER_IP:/tmp/load-generator.yaml

# 2. Cleanup old load-generator (any kind)
echo "→ Removing old load-generator (if exists)..."
ssh -i "$KEY_FILE" -o StrictHostKeyChecking=no -o IdentitiesOnly=yes \
    ubuntu@$MASTER_IP "sudo k3s kubectl delete daemonset,deployment load-generator -n default --ignore-not-found=true"

# 3. Apply new manifest
echo "→ Applying manifest on cluster..."
ssh -i "$KEY_FILE" -o StrictHostKeyChecking=no -o IdentitiesOnly=yes \
    ubuntu@$MASTER_IP "sudo k3s kubectl apply -f /tmp/load-generator.yaml"

echo "==================================================="
echo "✅ Load Generator Deployed Successfully!"
echo "   Monitor the Grafana Dashboard for scaling events."
echo "==================================================="
