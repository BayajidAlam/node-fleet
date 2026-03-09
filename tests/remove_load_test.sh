#!/bin/bash

# Configuration
MASTER_IP="13.212.80.238"
KEY_FILE="node-fleet-key.pem"

if [ ! -f "$KEY_FILE" ]; then
    echo "Error: Key file '$KEY_FILE' not found."
    exit 1
fi

echo "→ Removing load generator..."
ssh -i "$KEY_FILE" -o StrictHostKeyChecking=no -o IdentitiesOnly=yes \
    ubuntu@$MASTER_IP "sudo k3s kubectl delete -f /tmp/load-generator.yaml"

echo "✅ Load Generator Removed."
