#!/bin/bash

# Configuration
MASTER_IP="13.212.80.238"
KEY_FILE="node-fleet-key.pem"

# Check if key exists
if [ ! -f "$KEY_FILE" ]; then
    echo "Error: Key file '$KEY_FILE' not found in current directory."
    exit 1
fi

echo "==================================================="
echo "   Remote Load Test Deployer"
echo "==================================================="
echo "Target Master: $MASTER_IP"

# 1. Copy the manifest to the master node
echo "→ Copying load-generator.yaml to master node..."
scp -i "$KEY_FILE" -o StrictHostKeyChecking=no -o IdentitiesOnly=yes \
    tests/load-generator.yaml ubuntu@$MASTER_IP:/tmp/load-generator.yaml

# 2. Apply it using remote kubectl
echo "→ Applying manifest on cluster..."
ssh -i "$KEY_FILE" -o StrictHostKeyChecking=no -o IdentitiesOnly=yes \
    ubuntu@$MASTER_IP "sudo k3s kubectl apply -f /tmp/load-generator.yaml"

echo "==================================================="
echo "✅ Load Generator Deployed Successfully!"
echo "   Monitor the Grafana Dashboard for scaling events."
echo "==================================================="
