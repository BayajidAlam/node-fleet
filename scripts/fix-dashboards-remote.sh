#!/bin/bash
#
# Quick fix for Grafana dashboards - Run this from your local machine
#
set -e

MASTER_IP="47.129.152.2"
KEY_FILE="${1:-node-fleet-key.pem}"

echo "========================================"
echo "Grafana Dashboard Quick Fix"
echo "========================================"

if [ ! -f "$KEY_FILE" ]; then
    echo "❌ SSH key not found: $KEY_FILE"
    echo "Usage: ./scripts/fix-dashboards-remote.sh <path-to-key.pem>"
    exit 1
fi

echo "→ Copying fix script to master node..."
scp -i "$KEY_FILE" -o StrictHostKeyChecking=no -o IdentitiesOnly=yes \
    monitoring/fix-grafana-dashboards.sh \
    monitoring/grafana-dashboards/*.json \
    ubuntu@${MASTER_IP}:/tmp/

echo "→ Creating dashboard directory on master..."
ssh -i "$KEY_FILE" -o StrictHostKeyChecking=no -o IdentitiesOnly=yes ubuntu@${MASTER_IP} \
    "sudo mkdir -p /home/ubuntu/monitoring/grafana-dashboards && \
     sudo mv /tmp/*.json /home/ubuntu/monitoring/grafana-dashboards/ && \
     sudo mv /tmp/fix-grafana-dashboards.sh /home/ubuntu/ && \
     sudo chmod +x /home/ubuntu/fix-grafana-dashboards.sh"

echo "→ Running fix script on master node..."
ssh -i "$KEY_FILE" -o StrictHostKeyChecking=no -o IdentitiesOnly=yes ubuntu@${MASTER_IP} \
    "cd /home/ubuntu && sudo ./fix-grafana-dashboards.sh"

echo ""
echo "✅ Done! Access Grafana at: http://${MASTER_IP}:30030"
echo "   Username: admin"
echo "   Password: (run this to get it)"
echo "   kubectl get secret -n monitoring grafana-admin -o jsonpath='{.data.password}' | base64 -d"
echo ""
