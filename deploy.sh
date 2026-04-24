#!/bin/bash
#
# deploy.sh — Unified node-fleet deployment entry point
#
# Usage:
#   ./deploy.sh <master-public-ip> [--skip-infra] [--skip-lambda] [--skip-monitoring]
#
# Options:
#   --skip-infra       Skip Pulumi infrastructure deployment
#   --skip-lambda      Skip Lambda build and deploy
#   --skip-monitoring  Skip monitoring/Grafana configuration
#
# Environment variables:
#   SSH_KEY     Path to SSH key (default: node-fleet-key.pem)
#   AWS_REGION  AWS region (default: ap-southeast-1)
#
set -euo pipefail

# ─── Parse arguments ────────────────────────────────────────────────────────
MASTER_IP="${MASTER_IP:-${1:-}}"
SKIP_INFRA=false
SKIP_LAMBDA=false
SKIP_MONITORING=false

for arg in "$@"; do
  case "$arg" in
    --skip-infra)       SKIP_INFRA=true ;;
    --skip-lambda)      SKIP_LAMBDA=true ;;
    --skip-monitoring)  SKIP_MONITORING=true ;;
  esac
done

if [ -z "$MASTER_IP" ]; then
  echo "❌ Error: master public IP is required."
  echo "Usage: ./deploy.sh <master-ip> [--skip-infra] [--skip-lambda] [--skip-monitoring]"
  echo "  OR:  MASTER_IP=<ip> ./deploy.sh"
  exit 1
fi

SSH_KEY="${SSH_KEY:-node-fleet-key.pem}"
AWS_REGION="${AWS_REGION:-ap-southeast-1}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors
GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; }

echo ""
echo "=============================================="
echo "  node-fleet K3s Autoscaler — Full Deployment"
echo "=============================================="
echo "  Master IP  : $MASTER_IP"
echo "  SSH Key    : $SSH_KEY"
echo "  AWS Region : $AWS_REGION"
echo ""

# ─── Step 1: Deploy Infrastructure (Pulumi) ─────────────────────────────────
if [ "$SKIP_INFRA" = false ]; then
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  info "STEP 1/5: Deploying infrastructure with Pulumi..."
  cd "$SCRIPT_DIR/pulumi"
  pulumi up --yes --skip-preview
  success "Infrastructure deployed"
  cd "$SCRIPT_DIR"
else
  warn "STEP 1/5: Skipping infrastructure (--skip-infra)"
fi

# ─── Step 2: Build and deploy Lambda ────────────────────────────────────────
if [ "$SKIP_LAMBDA" = false ]; then
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  info "STEP 2/5: Building Lambda package..."
  cd "$SCRIPT_DIR/pulumi"
  bash build-lambda.sh

  LAMBDA_ZIP="$SCRIPT_DIR/pulumi/lambda-deployment.zip"
  if [ ! -f "$LAMBDA_ZIP" ]; then
    error "Lambda ZIP not found after build: $LAMBDA_ZIP"
    exit 1
  fi

  # Upload to S3 and update Lambda function
  BUCKET=$(pulumi stack output lambdaArtifactsBucketName 2>/dev/null || echo "")
  FUNCTION_NAME=$(pulumi stack output autoscalerFunctionName 2>/dev/null || echo "")

  if [ -n "$BUCKET" ] && [ -n "$FUNCTION_NAME" ]; then
    info "Uploading Lambda to s3://$BUCKET..."
    aws s3 cp "$LAMBDA_ZIP" "s3://$BUCKET/lambda-deployment.zip" --region "$AWS_REGION"
    aws lambda update-function-code \
      --function-name "$FUNCTION_NAME" \
      --s3-bucket "$BUCKET" \
      --s3-key lambda-deployment.zip \
      --region "$AWS_REGION" \
      --output json | jq -r '"Lambda updated: " + .FunctionArn'
    success "Lambda deployed: $FUNCTION_NAME"
  else
    warn "Could not resolve Lambda/S3 names from pulumi outputs. Run 'pulumi up' first."
  fi
  cd "$SCRIPT_DIR"
else
  warn "STEP 2/5: Skipping Lambda build (--skip-lambda)"
fi

# ─── Step 3: Wait for K3s and deploy cluster apps ───────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
info "STEP 3/5: Deploying K3s cluster applications..."
export MASTER_IP SSH_KEY
bash "$SCRIPT_DIR/scripts/deploy-cluster.sh" "$MASTER_IP"
success "Cluster applications deployed"

# ─── Step 4: Configure monitoring (if not skipped) ──────────────────────────
if [ "$SKIP_MONITORING" = false ]; then
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  info "STEP 4/5: Configuring monitoring stack..."

  # deploy_monitoring.sh requires KUBECONFIG to be set
  export KUBECONFIG=/tmp/k3s-kubeconfig.yaml
  if [ -f "$KUBECONFIG" ]; then
    bash "$SCRIPT_DIR/scripts/deploy_monitoring.sh"
    success "Monitoring stack applied"
  else
    warn "KUBECONFIG not found at /tmp/k3s-kubeconfig.yaml, skipping monitoring re-apply"
  fi

  # Configure Grafana dashboards
  info "Configuring Grafana CloudWatch datasource and dashboards..."
  node "$SCRIPT_DIR/monitoring/fix-grafana.js" "$MASTER_IP"
  success "Grafana configured"
else
  warn "STEP 4/5: Skipping monitoring configuration (--skip-monitoring)"
fi

# ─── Step 5: Verify deployment ───────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
info "STEP 5/5: Verifying deployment..."
export KUBECONFIG=/tmp/k3s-kubeconfig.yaml
kubectl get nodes 2>/dev/null || warn "kubectl not available locally, verify manually"

echo ""
echo "=============================================="
echo -e "${GREEN}✅  node-fleet Deployment Complete!${NC}"
echo "=============================================="
echo ""
echo "Access URLs:"
echo "  Prometheus : http://$MASTER_IP:30090"
echo "  Grafana    : http://$MASTER_IP:30300"
echo "  Demo App   : http://$MASTER_IP:30080"
echo ""
echo "Autoscaler monitoring:"
echo "  Lambda logs : aws logs tail /aws/lambda/<cluster>-autoscaler --follow --region $AWS_REGION"
echo "  CloudWatch  : aws cloudwatch list-metrics --namespace NodeFleet/Autoscaler"
echo ""
echo "Verify autoscaler:"
echo "  bash scripts/verify-autoscaler-requirements.sh"
echo ""
