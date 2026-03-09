#!/bin/bash
set -e

# Log everything
exec > >(tee /var/log/k3s-worker-setup.log)
exec 2>&1

echo "🚀 K3s Worker Node Setup Starting..."
echo "Time: $(date)"

# Install AWS CLI and jq
echo "📦 Installing dependencies..."
apt-get update -qq
apt-get install -y awscli jq curl

# Get K3s token from Secrets Manager
echo "🔐 Retrieving K3s token from Secrets Manager..."
K3S_TOKEN=$(aws secretsmanager get-secret-value \
  --secret-id node-fleet/k3s-token \
  --region ap-southeast-1 \
  --query SecretString \
  --output text)

if [ -z "$K3S_TOKEN" ]; then
  echo "❌ Failed to retrieve K3s token"
  exit 1
fi

# Get master node IP from EC2 tags
echo "🔍 Finding K3s master node..."
MASTER_IP=$(aws ec2 describe-instances \
  --region ap-southeast-1 \
  --filters "Name=tag:Role,Values=k3s-master" "Name=instance-state-name,Values=running" \
  --query 'Reservations[0].Instances[0].PrivateIpAddress' \
  --output text)

if [ -z "$MASTER_IP" ] || [ "$MASTER_IP" = "None" ]; then
  echo "❌ Failed to find master node"
  exit 1
fi

echo "✅ Master node found at: $MASTER_IP"

# Install K3s agent
echo "🎯 Joining K3s cluster..."
curl -sfL https://get.k3s.io | K3S_URL=https://${MASTER_IP}:6443 K3S_TOKEN=${K3S_TOKEN} sh -

# Wait for node to be ready
echo "⏳ Waiting for node to join cluster..."
sleep 30

echo "✅ K3s worker node setup complete!"
echo "Time: $(date)"
