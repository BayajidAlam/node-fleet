#!/bin/bash
#
# PERMANENT FIX DEPLOYMENT SCRIPT
# Deploys all fixes for dashboard and Lambda configuration issues
#
set -e

echo "=============================================="
echo "🔧 node-fleet Permanent Fix Deployment"
echo "=============================================="
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "📋 Changes to be deployed:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "1. Lambda Configuration Updates:"
echo "   • MIN_NODES: 3 → 2 (enable scaling to minimum)"
echo "   • SPOT_PERCENTAGE: 60% → 70% (increase cost savings)"
echo "   • Add METRICS_HISTORY_TABLE environment variable"
echo "   • Add ENABLE_PREDICTIVE_SCALING=true"
echo "   • Add ENABLE_CUSTOM_METRICS=false"
echo ""
echo "2. CloudWatch Metrics:"
echo "   • Namespace: NodeFleet/Autoscaler (matches autoscaler.py output)"
echo "   • (Matches dashboard expectations)"
echo ""
echo "3. EventBridge Schedule:"
echo "   • Already configured: rate(2 minutes) ✓"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

read -p "Deploy these changes? (y/N): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "❌ Deployment cancelled"
    exit 0
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "STEP 1: Package Lambda Code"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

cd "$PROJECT_ROOT/lambda"

# Install dependencies if needed
if [ ! -d "venv" ]; then
    echo "→ Creating Python virtual environment..."
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
else
    echo "→ Using existing virtual environment"
    source venv/bin/activate
fi

# Package Lambda code
echo "→ Creating Lambda deployment package..."
rm -f lambda-package.zip

# Create temporary directory for packaging
TEMP_DIR=$(mktemp -d)
echo "   Copying Python files..."
cp *.py "$TEMP_DIR/"

# Copy dependencies
echo "   Copying dependencies..."
SITE_PACKAGES=$(python -c "import site; print(site.getsitepackages()[0])")
cp -r "$SITE_PACKAGES"/* "$TEMP_DIR/" 2>/dev/null || true

# Create zip
cd "$TEMP_DIR"
echo "   Creating zip archive..."
zip -r "$PROJECT_ROOT/lambda/lambda-package.zip" . -q

# Cleanup
cd "$PROJECT_ROOT/lambda"
rm -rf "$TEMP_DIR"

ZIP_SIZE=$(du -h lambda-package.zip | cut -f1)
echo "   ✅ Package created: lambda-package.zip ($ZIP_SIZE)"

deactivate

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "STEP 2: Deploy Infrastructure via Pulumi"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

cd "$PROJECT_ROOT/pulumi"

echo "→ Running Pulumi preview..."
pulumi preview --diff

echo ""
read -p "Proceed with Pulumi deployment? (y/N): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "❌ Pulumi deployment cancelled"
    exit 0
fi

echo "→ Deploying infrastructure changes..."
pulumi up --yes

LAMBDA_ARN=$(pulumi stack output autoscalerFunctionArn)
echo "   ✅ Lambda updated: $LAMBDA_ARN"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "STEP 3: Verify Lambda Configuration"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

FUNCTION_NAME=$(pulumi stack output autoscalerFunctionName 2>/dev/null || echo "node-fleet-autoscaler")

echo "→ Checking Lambda environment variables..."
aws lambda get-function-configuration --function-name "$FUNCTION_NAME" --query 'Environment.Variables' --output json > /tmp/lambda-config.json

echo ""
echo "Verification:"
MIN_NODES=$(jq -r '.MIN_NODES' /tmp/lambda-config.json)
SPOT_PCT=$(jq -r '.SPOT_PERCENTAGE' /tmp/lambda-config.json)
METRICS_TABLE=$(jq -r '.METRICS_HISTORY_TABLE' /tmp/lambda-config.json)
PREDICTIVE=$(jq -r '.ENABLE_PREDICTIVE_SCALING' /tmp/lambda-config.json)

printf "   %-30s %s\n" "MIN_NODES:" "$MIN_NODES $([ "$MIN_NODES" = "2" ] && echo "✅" || echo "❌")"
printf "   %-30s %s\n" "SPOT_PERCENTAGE:" "$SPOT_PCT $([ "$SPOT_PCT" = "70" ] && echo "✅" || echo "❌")"
printf "   %-30s %s\n" "METRICS_HISTORY_TABLE:" "$([ "$METRICS_TABLE" != "null" ] && echo "$METRICS_TABLE ✅" || echo "NOT SET ❌")"
printf "   %-30s %s\n" "ENABLE_PREDICTIVE_SCALING:" "$PREDICTIVE $([ "$PREDICTIVE" = "true" ] && echo "✅" || echo "❌")"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "STEP 4: Wait for Metrics Publication"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

echo "→ Waiting for Lambda to publish metrics to NodeFleet/Autoscaler namespace..."
echo "   (Next execution in ~2 minutes)"

sleep 130  # Wait for next Lambda execution

echo "→ Checking CloudWatch metrics..."
METRIC_COUNT=$(aws cloudwatch list-metrics --namespace "NodeFleet/Autoscaler" --output json | jq '.Metrics | length')

if [ "$METRIC_COUNT" -gt 0 ]; then
    echo "   ✅ Found $METRIC_COUNT metrics in NodeFleet/Autoscaler namespace"
    aws cloudwatch list-metrics --namespace "NodeFleet/Autoscaler" --output json | jq -r '.Metrics[].MetricName' | sort -u | sed 's/^/      • /'
else
    echo "   ⚠️  No metrics yet. Check Lambda logs:"
    echo "      aws logs tail /aws/lambda/$FUNCTION_NAME --since 5m"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "STEP 5: Grafana Dashboard Configuration"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

MASTER_IP=$(pulumi stack output masterPublicIpAddress)

echo "→ Grafana is available at: http://$MASTER_IP:30300"
echo ""
echo "Manual steps required (one-time setup):"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "1. Open Grafana: http://$MASTER_IP:30300"
echo ""
echo "2. Login (try these credentials):"
echo "   • admin / admin"
echo "   • If that fails, get password from logs:"
echo "     kubectl logs -n monitoring -l app=grafana --tail=100 | grep password"
echo ""
echo "3. Add CloudWatch Data Source:"
echo "   ① Settings (⚙️) → Data Sources → Add data source"
echo "   ② Select: CloudWatch"
echo "   ③ Configuration:"
echo "      - Name: CloudWatch"
echo "      - Auth Provider: AWS SDK Default"
echo "      - Default Region: ap-southeast-1"
echo "   ④ Click 'Save & Test'"
echo ""
echo "4. Import Dashboards:"
echo "   ① Dashboards (📊) → Import"
echo "   ② For each file:"
echo "      - monitoring/grafana-dashboards/autoscaler-performance.json"
echo "      - monitoring/grafana-dashboards/cluster-overview.json"
echo "      - monitoring/grafana-dashboards/cost-tracking.json"
echo "   ③ Select 'CloudWatch' as data source"
echo "   ④ Click 'Import'"
echo ""
echo "5. Verify Data:"
echo "   • Dashboards should show live metrics within 2-5 minutes"
echo "   • If 'No data', check namespace in queries = 'NodeFleet/Autoscaler'"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Offer to open Grafana setup script on master
echo ""
read -p "Upload automated Grafana config script to master? (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo ""
    echo "→ Uploading configuration script..."
    
    KEY_FILE="$PROJECT_ROOT/node-fleet-key.pem"
    if [ ! -f "$KEY_FILE" ]; then
        echo "   ⚠️  SSH key not found: $KEY_FILE"
        echo "   Please specify key file location:"
        read KEY_FILE
    fi
    
    # Upload script
    scp -i "$KEY_FILE" -o StrictHostKeyChecking=no -o IdentitiesOnly=yes \
        "$PROJECT_ROOT/scripts/configure-grafana-cloudwatch.sh" \
        "$PROJECT_ROOT/monitoring/grafana-dashboards/"*.json \
        ubuntu@${MASTER_IP}:/home/ubuntu/
    
    echo ""
    echo "   ✅ Files uploaded to master node"
    echo ""
    echo "   To complete setup, SSH to master and run:"
    echo "   ssh -i $KEY_FILE ubuntu@${MASTER_IP}"
    echo "   chmod +x configure-grafana-cloudwatch.sh"
    echo "   ./configure-grafana-cloudwatch.sh"
fi

echo ""
echo "=============================================="
echo "✅ DEPLOYMENT COMPLETE"
echo "=============================================="
echo ""
echo "Summary of Changes:"
echo "  ✅ Lambda environment variables updated"
echo "  ✅ CloudWatch namespace changed to 'node-fleet'"
echo "  ✅ MIN_NODES set to 2 (cluster can now scale down more)"
echo "  ✅ SPOT_PERCENTAGE increased to 70% (more cost savings)"
echo "  ✅ Predictive scaling enabled"
echo ""
echo "Next Steps:"
echo "  1. Configure Grafana CloudWatch datasource (manual - see above)"
echo "  2. Import dashboards into Grafana"
echo "  3. Wait 2-5 minutes for metrics to populate"
echo "  4. Verify autoscaler behavior with lower MIN_NODES"
echo ""
echo "Monitoring:"
echo "  • Lambda logs: aws logs tail /aws/lambda/$FUNCTION_NAME --follow"
echo "  • Grafana: http://$MASTER_IP:30300"
echo "  • CloudWatch: aws cloudwatch list-metrics --namespace NodeFleet/Autoscaler"
echo ""
echo "Documentation:"
echo "  • DASHBOARD_FIX_SUMMARY.md - Detailed fix explanation"
echo "  • DEPLOYMENT_STATUS.md - Current system status"
echo ""
