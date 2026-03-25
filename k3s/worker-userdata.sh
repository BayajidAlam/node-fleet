#!/bin/bash
# No set -e — we handle errors manually with retry loops

# Log everything
exec > >(tee /var/log/k3s-worker-setup.log)
exec 2>&1

echo "🚀 K3s Worker Node Setup Starting..."
echo "Time: $(date)"

# Install AWS CLI and jq
echo "📦 Installing dependencies..."
apt-get update -qq
apt-get install -y awscli jq curl

# Retry fetching K3s token — master-setup.sh may not have run yet
echo "🔐 Retrieving K3s token from Secrets Manager (with retry)..."
K3S_TOKEN=""
for i in $(seq 1 30); do
  K3S_TOKEN=$(aws secretsmanager get-secret-value \
    --secret-id node-fleet/k3s-token \
    --region ap-southeast-1 \
    --query SecretString \
    --output text 2>/dev/null || echo "")
  if [ -n "$K3S_TOKEN" ] && [ "$K3S_TOKEN" != "null" ]; then
    echo "✅ Token retrieved on attempt $i"
    break
  fi
  echo "  Attempt $i/30: Token not available yet, waiting 30s..."
  sleep 30
done

if [ -z "$K3S_TOKEN" ] || [ "$K3S_TOKEN" = "null" ]; then
  echo "❌ Failed to retrieve K3s token after 15 minutes"
  exit 1
fi

# Retry finding master node — master may still be booting
echo "🔍 Finding K3s master node (with retry)..."
MASTER_IP=""
for i in $(seq 1 20); do
  MASTER_IP=$(aws ec2 describe-instances \
    --region ap-southeast-1 \
    --filters "Name=tag:Role,Values=k3s-master" "Name=instance-state-name,Values=running" \
    --query 'Reservations[0].Instances[0].PrivateIpAddress' \
    --output text 2>/dev/null || echo "")
  if [ -n "$MASTER_IP" ] && [ "$MASTER_IP" != "None" ] && [ "$MASTER_IP" != "null" ]; then
    echo "✅ Master node found at: $MASTER_IP (attempt $i)"
    break
  fi
  echo "  Attempt $i/20: Master not ready yet, waiting 15s..."
  sleep 15
done

if [ -z "$MASTER_IP" ] || [ "$MASTER_IP" = "None" ] || [ "$MASTER_IP" = "null" ]; then
  echo "❌ Failed to find master node after retries"
  exit 1
fi

# Retry K3s join — master K3s API may not be ready yet
echo "🎯 Joining K3s cluster at $MASTER_IP (with retry)..."
for i in $(seq 1 10); do
  if curl -sfL https://get.k3s.io | K3S_URL=https://${MASTER_IP}:6443 K3S_TOKEN=${K3S_TOKEN} sh -; then
    echo "✅ Joined cluster on attempt $i"
    break
  fi
  echo "  Attempt $i/10: Join failed, retrying in 30s..."
  sleep 30
done

# Verify service started
if ! systemctl is-active --quiet k3s-agent; then
  echo "❌ k3s-agent service not running after join"
  exit 1
fi

echo "✅ K3s worker node setup complete!"
echo "Time: $(date)"

