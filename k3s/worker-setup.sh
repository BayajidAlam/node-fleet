#!/bin/bash
set -e

# K3s Worker Node Setup Script
# This script runs on worker EC2 instances via UserData

echo "🚀 Starting K3s Worker Node Setup..."

# Wait for cloud-init to complete
while [ ! -f /var/lib/cloud/instance/boot-finished ]; do 
    echo "Waiting for cloud-init..."
    sleep 1
done

# Update system
apt-get update -qq
apt-get install -y curl wget awscli

# Get master node IP from EC2 tags
echo "🔍 Finding K3s master node..."
MASTER_IP=$(aws ec2 describe-instances \
  --region ap-south-1 \
  --filters "Name=tag:Role,Values=k3s-master" "Name=instance-state-name,Values=running" \
  --query 'Reservations[0].Instances[0].PrivateIpAddress' \
  --output text)

if [ -z "$MASTER_IP" ] || [ "$MASTER_IP" == "None" ]; then
    echo "❌ Error: Could not find master node IP"
    exit 1
fi

echo "✅ Found master at $MASTER_IP"

# Get K3s token from Secrets Manager
echo "🔐 Retrieving K3s token from Secrets Manager..."
K3S_TOKEN=$(aws secretsmanager get-secret-value \
  --secret-id node-fleet/k3s-token \
  --region ap-south-1 \
  --query SecretString \
  --output text)

if [ -z "$K3S_TOKEN" ]; then
    echo "❌ Error: Could not retrieve K3s token"
    exit 1
fi

# Wait for master to be ready
echo "⏳ Waiting for master to be ready..."
MAX_RETRIES=30
RETRY_COUNT=0

while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    if curl -sk https://$MASTER_IP:6443/ping &>/dev/null; then
        echo "✅ Master is ready!"
        break
    fi
    echo "Still waiting... (attempt $((RETRY_COUNT + 1))/$MAX_RETRIES)"
    sleep 10
    RETRY_COUNT=$((RETRY_COUNT + 1))
done

if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
    echo "❌ Error: Master node did not become ready in time"
    exit 1
fi

# Install K3s worker
echo "📦 Installing K3s worker and joining cluster..."
curl -sfL https://get.k3s.io | K3S_URL=https://$MASTER_IP:6443 K3S_TOKEN=$K3S_TOKEN sh -

# Verify join
echo "⏳ Verifying node joined cluster..."
sleep 10
systemctl status k3s-agent --no-pager || true

echo "✅ K3s worker setup complete and joined cluster!"
