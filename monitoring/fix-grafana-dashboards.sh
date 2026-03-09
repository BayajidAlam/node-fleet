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

# Get Grafana password from Kubernetes environment (manually verified as admin123)
echo "→ Fetching Grafana password..."
GRAFANA_PASSWORD="admin123"

# Prometheus Credentials (from Secrets Manager verified earlier)
PROM_USER="prometheus-admin"
PROM_PASS="KvV&!FbJBge3QSVnsnffFT2Y6D8?A<&k"

echo "→ Grafana URL: ${GRAFANA_URL}"
echo "→ Username: ${GRAFANA_USER}"

# Check Grafana is accessible
if ! curl -s -o /dev/null -w "%{http_code}" "${GRAFANA_URL}/api/health" | grep -q "200"; then
    echo "❌ ERROR: Grafana is not accessible at ${GRAFANA_URL}"
    echo "   Make sure you're running this from the master node or can access NodePort 30030"
    exit 1
fi

echo "✅ Grafana is accessible"

# Step 1: Check and Create CloudWatch Data Source
echo ""
echo "→ Checking CloudWatch data source..."
CW_DATASOURCE_CHECK=$(curl -s -u "${GRAFANA_USER}:${GRAFANA_PASSWORD}" \
    "${GRAFANA_URL}/api/datasources/name/CloudWatch" || echo "")

if echo "$CW_DATASOURCE_CHECK" | grep -q '"id"'; then
    echo "✅ CloudWatch data source already exists"
    CW_DATASOURCE_ID=$(echo "$CW_DATASOURCE_CHECK" | jq -r '.id')
else
    echo "→ Creating CloudWatch data source..."
    AWS_REGION="ap-southeast-1"
    
    CW_PAYLOAD=$(cat <<EOF
{
  "name": "CloudWatch",
  "type": "cloudwatch",
  "access": "proxy",
  "jsonData": {
    "authType": "ec2_iam_role",
    "defaultRegion": "${AWS_REGION}"
  }
}
EOF
)
    CW_RESPONSE=$(curl -s -X POST -H "Content-Type: application/json" \
        -u "${GRAFANA_USER}:${GRAFANA_PASSWORD}" -d "${CW_PAYLOAD}" \
        "${GRAFANA_URL}/api/datasources")
    CW_DATASOURCE_ID=$(echo "$CW_RESPONSE" | jq -r '.id')
    echo "✅ CloudWatch data source created (ID: ${CW_DATASOURCE_ID})"
fi

# Step 2: Check and Create Prometheus Data Source
echo ""
echo "→ Checking Prometheus data source..."
PROM_DATASOURCE_CHECK=$(curl -s -u "${GRAFANA_USER}:${GRAFANA_PASSWORD}" \
    "${GRAFANA_URL}/api/datasources/name/Prometheus" || echo "")

if echo "$PROM_DATASOURCE_CHECK" | grep -q '"id"'; then
    echo "✅ Prometheus data source already exists"
    PROM_DATASOURCE_ID=$(echo "$PROM_DATASOURCE_CHECK" | jq -r '.id')
else
    echo "→ Creating Prometheus data source..."
    PROM_URL="http://${MASTER_IP}:30090"
    
    PROM_PAYLOAD=$(cat <<EOF
{
  "name": "Prometheus",
  "type": "prometheus",
  "access": "proxy",
  "url": "${PROM_URL}",
  "basicAuth": true,
  "basicAuthUser": "${PROM_USER}",
  "secureJsonData": {
    "basicAuthPassword": "${PROM_PASS}"
  },
  "jsonData": {
    "httpMethod": "POST"
  }
}
EOF
)
    PROM_RESPONSE=$(curl -s -X POST -H "Content-Type: application/json" \
        -u "${GRAFANA_USER}:${GRAFANA_PASSWORD}" -d "${PROM_PAYLOAD}" \
        "${GRAFANA_URL}/api/datasources")
    PROM_DATASOURCE_ID=$(echo "$PROM_RESPONSE" | jq -r '.id')
    echo "✅ Prometheus data source created (ID: ${PROM_DATASOURCE_ID})"
fi

# Step 3: Get UIDs
CW_UID=$(curl -s -u "${GRAFANA_USER}:${GRAFANA_PASSWORD}" "${GRAFANA_URL}/api/datasources/${CW_DATASOURCE_ID}" | jq -r '.uid')
PROM_UID=$(curl -s -u "${GRAFANA_USER}:${GRAFANA_PASSWORD}" "${GRAFANA_URL}/api/datasources/${PROM_DATASOURCE_ID}" | jq -r '.uid')

echo "   CloudWatch UID: ${CW_UID}"
echo "   Prometheus UID: ${PROM_UID}"

# Step 4: Import dashboards
DASHBOARD_DIR="./monitoring/grafana-dashboards"
echo ""
echo "→ Importing dashboards..."

import_dashboard() {
    local file=$1
    local title=$(jq -r '.title' "$file")
    echo "   • ${title}..."
    
    # Intelligently map data sources:
    # 1. Targets with 'expr' get Prometheus
    # 2. Targets with 'namespace' (CloudWatch style) or specifically 'cloudwatch' get CloudWatch
    local updated_json=$(jq --arg cw_uid "$CW_UID" --arg prom_uid "$PROM_UID" '
        (.panels[]? | select(.targets != null)) |= (
            .targets[]? |= (
                if .expr != null then
                    .datasource = {"type": "prometheus", "uid": $prom_uid}
                else
                    .datasource = {"type": "cloudwatch", "uid": $cw_uid}
                end
            )
        )
    ' "$file")
    
    local payload=$(jq -n --argjson dashboard "$updated_json" '{
        dashboard: $dashboard,
        overwrite: true,
        message: "Imported via fix-grafana-dashboards.sh"
    }')
    
    local response=$(curl -s -X POST -H "Content-Type: application/json" \
        -u "${GRAFANA_USER}:${GRAFANA_PASSWORD}" -d "${payload}" \
        "${GRAFANA_URL}/api/dashboards/db")
    
    if echo "$response" | grep -q '"status":"success"'; then
        echo "      ✅ Imported"
    else
        echo "      ❌ Failed: $(echo "$response" | jq -r '.message')"
    fi
}

for dashboard in ${DASHBOARD_DIR}/*.json; do
    [ -f "$dashboard" ] && import_dashboard "$dashboard"
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
