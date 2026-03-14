#!/bin/bash

# Configuration
MASTER_IP="${MASTER_IP:-${1}}"
if [ -z "$MASTER_IP" ]; then
    echo "Error: MASTER_IP env var or first argument is required."
    echo "Usage: MASTER_IP=<ip> ./remove_load_test.sh  OR  ./remove_load_test.sh <ip>"
    exit 1
fi
KEY_FILE="${KEY_FILE:-node-fleet-key.pem}"

if [ ! -f "$KEY_FILE" ]; then
    echo "Error: Key file '$KEY_FILE' not found."
    exit 1
fi

echo "→ Removing load generator..."
ssh -i "$KEY_FILE" -o StrictHostKeyChecking=no -o IdentitiesOnly=yes \
    ubuntu@$MASTER_IP "sudo k3s kubectl delete -f /tmp/load-generator.yaml"

echo "✅ Load Generator Removed."
