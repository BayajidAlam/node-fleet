#!/bin/bash
#
# Fix Grafana Dashboards - Configure CloudWatch data source and import dashboards
#
set -e

MASTER_IP="${1:-47.129.152.2}"
GRAFANA_PORT="30030"
GRAFANA_URL="http://${MASTER_IP}:${GRAFANA_PORT}"
GRAFANA_USER="admin"

echo "========================================"
echo "Fixing Grafana Dashboards"
echo "========================================"

# Get Grafana password from Kubernetes secret
echo "→ Fetching Grafana password..."
GRAFANA_PASSWORD=$(kubectl get secret -n monitoring grafana-admin -o jsonpath='{.data.password}' | base64 -d 2>/dev/null || echo "admin")

echo "→ Grafana URL: ${GRAFANA_URL}"
echo "→ Username: ${GRAFANA_USER}"

# Check Grafana is accessible
if ! curl -s -o /dev/null -w "%{http_code}" "${GRAFANA_URL}/api/health" | grep -q "200"; then
    echo "❌ ERROR: Grafana is not accessible at ${GRAFANA_URL}"
    echo "   Make sure you're running this from the master node or can access NodePort 30030"
    exit 1
fi

echo "✅ Grafana is accessible"

# Step 1: Check if CloudWatch data source exists
echo ""
echo "→ Checking CloudWatch data source..."
DATASOURCE_CHECK=$(curl -s -u "${GRAFANA_USER}:${GRAFANA_PASSWORD}" \
    "${GRAFANA_URL}/api/datasources/name/CloudWatch" || echo "")

if echo "$DATASOURCE_CHECK" | grep -q '"id"'; then
    echo "✅ CloudWatch data source already exists"
    DATASOURCE_ID=$(echo "$DATASOURCE_CHECK" | jq -r '.id')
else
    echo "→ Creating CloudWatch data source..."
    
    # Get AWS credentials from environment or instance metadata
    AWS_REGION=$(curl -s http://169.254.169.254/latest/meta-data/placement/region 2>/dev/null || echo "ap-southeast-1")
    
    DATASOURCE_PAYLOAD=$(cat <<EOF
{
  "name": "CloudWatch",
  "type": "cloudwatch",
  "access": "proxy",
  "isDefault": false,
  "jsonData": {
    "authType": "ec2_iam_role",
    "defaultRegion": "${AWS_REGION}"
  }
}
EOF
)
    
    DATASOURCE_RESPONSE=$(curl -s -X POST \
        -H "Content-Type: application/json" \
        -u "${GRAFANA_USER}:${GRAFANA_PASSWORD}" \
        -d "${DATASOURCE_PAYLOAD}" \
        "${GRAFANA_URL}/api/datasources")
    
    if echo "$DATASOURCE_RESPONSE" | grep -q '"id"'; then
        DATASOURCE_ID=$(echo "$DATASOURCE_RESPONSE" | jq -r '.id')
        echo "✅ CloudWatch data source created (ID: ${DATASOURCE_ID})"
    else
        echo "❌ Failed to create CloudWatch data source"
        echo "$DATASOURCE_RESPONSE" | jq '.'
        exit 1
    fi
fi

# Step 2: Update dashboard JSON files to use CloudWatch datasource UID
echo ""
echo "→ Getting CloudWatch datasource UID..."
DATASOURCE_UID=$(curl -s -u "${GRAFANA_USER}:${GRAFANA_PASSWORD}" \
    "${GRAFANA_URL}/api/datasources/${DATASOURCE_ID}" | jq -r '.uid')

echo "   CloudWatch UID: ${DATASOURCE_UID}"

# Step 3: Import dashboards from JSON files
DASHBOARD_DIR="/home/ubuntu/monitoring/grafana-dashboards"

if [ ! -d "$DASHBOARD_DIR" ]; then
    echo "⚠️  Dashboard directory not found at ${DASHBOARD_DIR}"
    echo "   Creating directory and copying dashboards..."
    mkdir -p "$DASHBOARD_DIR"
fi

echo ""
echo "→ Importing dashboards..."

# Function to import a dashboard
import_dashboard() {
    local file=$1
    local title=$(jq -r '.title' "$file")
    
    echo "   • ${title}..."
    
    # Update namespace from SmartScale to NodeFleet/Autoscaler in queries
    local updated_json=$(jq --arg uid "$DATASOURCE_UID" '
        .panels[]?.targets[]?.datasource = {"type": "cloudwatch", "uid": $uid} |
        .panels[]?.targets[]?.namespace = (
            if .namespace == "SmartScale" then "NodeFleet/Autoscaler"
            elif .namespace == "AWS/Lambda" then "AWS/Lambda"
            elif .namespace == "AWS/EC2" then "AWS/EC2"
            else .namespace
            end
        )
    ' "$file")
    
    # Wrap in dashboard object for import API
    local payload=$(jq -n --argjson dashboard "$updated_json" '{
        dashboard: $dashboard,
        overwrite: true,
        message: "Imported via fix-grafana-dashboards.sh"
    }')
    
    local response=$(curl -s -X POST \
        -H "Content-Type: application/json" \
        -u "${GRAFANA_USER}:${GRAFANA_PASSWORD}" \
        -d "${payload}" \
        "${GRAFANA_URL}/api/dashboards/db")
    
    if echo "$response" | grep -q '"status":"success"'; then
        local dash_uid=$(echo "$response" | jq -r '.uid')
        echo "      ✅ Imported (UID: ${dash_uid})"
    else
        echo "      ❌ Failed"
        echo "$response" | jq '.'
    fi
}

# Import each dashboard
for dashboard in ${DASHBOARD_DIR}/*.json; do
    if [ -f "$dashboard" ]; then
        import_dashboard "$dashboard"
    fi
done

echo ""
echo "========================================"
echo "✅ Dashboard Fix Complete!"
echo "========================================"
echo ""
echo "Next steps:"
echo "1. Access Grafana at: ${GRAFANA_URL}"
echo "2. Login with: ${GRAFANA_USER} / ${GRAFANA_PASSWORD}"
echo "3. Navigate to Dashboards to view:"
echo "   • Autoscaler Performance"
echo "   • Cluster Overview"
echo "   • Cost Tracking"
echo ""
echo "Note: If dashboards still show 'No data':"
echo "  - Wait 5 minutes for Lambda to publish more metrics"
echo "  - Check Lambda logs: aws logs tail /aws/lambda/node-fleet-dev-autoscaler"
echo "  - Verify CloudWatch metrics: aws cloudwatch list-metrics --namespace NodeFleet/Autoscaler"
echo ""
