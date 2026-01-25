#!/bin/bash
#
# Configure Grafana with CloudWatch datasource and fix dashboard namespace
# Run this ON THE MASTER NODE
#
set -e

GRAFANA_URL="http://localhost:30030"
GRAFANA_USER="admin"
AWS_REGION="ap-southeast-1"

echo "=========================================="
echo "Grafana CloudWatch Configuration"
echo "=========================================="

# Get Grafana password
GRAFANA_PASSWORD=$(kubectl get secret -n monitoring grafana-admin -o jsonpath='{.data.password}' 2>/dev/null | base64 -d || echo "admin")

echo "Using Grafana credentials: $GRAFANA_USER / (password from secret)"

# Check Grafana accessibility
if ! curl -s -o /dev/null -w "%{http_code}" "${GRAFANA_URL}/api/health" | grep -q "200"; then
    echo "ERROR: Grafana not accessible. Is it running?"
    kubectl get pods -n monitoring | grep grafana
    exit 1
fi

echo "✅ Grafana is accessible"

# Step 1: Check/Create CloudWatch datasource
echo ""
echo "→ Configuring CloudWatch datasource..."

DATASOURCE_PAYLOAD='{
  "name": "CloudWatch",
  "type": "cloudwatch",
  "access": "proxy",
  "isDefault": false,
  "jsonData": {
    "authType": "ec2_iam_role",
    "defaultRegion": "'"${AWS_REGION}"'"
  }
}'

# Try to create (will fail if exists)
CREATE_RESPONSE=$(curl -s -X POST \
    -H "Content-Type: application/json" \
    -u "${GRAFANA_USER}:${GRAFANA_PASSWORD}" \
    -d "${DATASOURCE_PAYLOAD}" \
    "${GRAFANA_URL}/api/datasources" 2>&1)

if echo "$CREATE_RESPONSE" | grep -q '"id"'; then
    echo "✅ CloudWatch datasource created"
elif echo "$CREATE_RESPONSE" | grep -q "already exists"; then
    echo "ℹ️  CloudWatch datasource already exists"
else
    echo "⚠️  Datasource response: $CREATE_RESPONSE"
fi

# Get datasource UID
DATASOURCE_UID=$(curl -s -u "${GRAFANA_USER}:${GRAFANA_PASSWORD}" \
    "${GRAFANA_URL}/api/datasources/name/CloudWatch" | jq -r '.uid')

echo "   CloudWatch UID: $DATASOURCE_UID"

# Step 2: Update and import dashboards
echo ""
echo "→ Importing dashboards..."

DASHBOARD_DIR="/home/ubuntu/dashboards"

if [ ! -d "$DASHBOARD_DIR" ]; then
    echo "ERROR: Dashboard directory not found: $DASHBOARD_DIR"
    exit 1
fi

for dashboard_file in ${DASHBOARD_DIR}/*.json; do
    if [ ! -f "$dashboard_file" ]; then
        continue
    fi
    
    TITLE=$(jq -r '.title // "Unknown"' "$dashboard_file")
    echo "   • Importing: $TITLE"
    
    # Fix namespace and add datasource UID
    FIXED_DASHBOARD=$(jq --arg uid "$DATASOURCE_UID" '
        .id = null |
        .uid = null |
        walk(
            if type == "object" and has("namespace") then
                .namespace = (
                    if .namespace == "SmartScale" then "NodeFleet/Autoscaler"
                    else .namespace
                    end
                ) |
                .datasource = {"type": "cloudwatch", "uid": $uid}
            else
                .
            end
        )
    ' "$dashboard_file")
    
    # Wrap for import API
    IMPORT_PAYLOAD=$(jq -n --argjson dashboard "$FIXED_DASHBOARD" '{
        dashboard: $dashboard,
        overwrite: true,
        message: "Auto-imported with CloudWatch datasource"
    }')
    
    IMPORT_RESPONSE=$(curl -s -X POST \
        -H "Content-Type: application/json" \
        -u "${GRAFANA_USER}:${GRAFANA_PASSWORD}" \
        -d "$IMPORT_PAYLOAD" \
        "${GRAFANA_URL}/api/dashboards/db")
    
    if echo "$IMPORT_RESPONSE" | grep -q '"status":"success"'; then
        DASH_UID=$(echo "$IMPORT_RESPONSE" | jq -r '.uid')
        DASH_URL=$(echo "$IMPORT_RESPONSE" | jq -r '.url')
        echo "      ✅ Imported successfully"
        echo "         URL: ${GRAFANA_URL}${DASH_URL}"
    else
        echo "      ❌ Import failed"
        echo "$IMPORT_RESPONSE" | jq '.'
    fi
done

echo ""
echo "=========================================="
echo "✅ Dashboard Configuration Complete!"
echo "=========================================="
echo ""
echo "Access Grafana at: http://$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4):30030"
echo "Username: $GRAFANA_USER"
echo "Password: $GRAFANA_PASSWORD"
echo ""
echo "Note: Dashboards may take 1-2 minutes to show data as Lambda publishes metrics every 5 minutes"
echo ""
