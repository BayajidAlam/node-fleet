#!/bin/bash
#
# Verify Lambda Autoscaler Against Requirements
#
set -e

echo "========================================"
echo "Lambda Autoscaler Requirements Verification"
echo "========================================"
echo ""

# Get Lambda function details
FUNCTION_NAME="node-fleet-dev-autoscaler"
AWS_REGION=$(aws configure get region || echo "ap-southeast-1")

echo "→ Checking Lambda function configuration..."
LAMBDA_CONFIG=$(aws lambda get-function-configuration --function-name "$FUNCTION_NAME" --output json)

# Extract key configuration
RUNTIME=$(echo "$LAMBDA_CONFIG" | jq -r '.Runtime')
TIMEOUT=$(echo "$LAMBDA_CONFIG" | jq -r '.Timeout')
MEMORY=$(echo "$LAMBDA_CONFIG" | jq -r '.MemorySize')
ENV_VARS=$(echo "$LAMBDA_CONFIG" | jq -r '.Environment.Variables')

echo ""
echo "📋 LAMBDA CONFIGURATION"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
printf "%-30s %s\n" "Runtime:" "$RUNTIME"
printf "%-30s %s\n" "Timeout:" "${TIMEOUT}s"
printf "%-30s %s\n" "Memory:" "${MEMORY}MB"
echo ""

# Check environment variables against requirements
echo "📋 ENVIRONMENT VARIABLES (Requirements Check)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

check_env() {
    local var_name=$1
    local required=$2
    local expected=$3
    
    local value=$(echo "$ENV_VARS" | jq -r ".${var_name} // \"NOT_SET\"")
    
    if [ "$value" = "NOT_SET" ]; then
        if [ "$required" = "REQUIRED" ]; then
            echo "❌ ${var_name}: NOT SET (REQUIRED)"
        else
            echo "⚠️  ${var_name}: NOT SET (Optional)"
        fi
    else
        if [ -n "$expected" ] && [ "$value" != "$expected" ]; then
            echo "⚠️  ${var_name}: ${value} (Expected: ${expected})"
        else
            echo "✅ ${var_name}: ${value}"
        fi
    fi
}

check_env "CLUSTER_ID" "REQUIRED"
check_env "PROMETHEUS_URL" "REQUIRED"
check_env "STATE_TABLE" "REQUIRED"
check_env "METRICS_HISTORY_TABLE" "REQUIRED"
check_env "MIN_NODES" "REQUIRED" "2"
check_env "MAX_NODES" "REQUIRED" "10"
check_env "WORKER_LAUNCH_TEMPLATE_ID" "REQUIRED"
check_env "WORKER_SPOT_TEMPLATE_ID" "OPTIONAL"
check_env "SPOT_PERCENTAGE" "OPTIONAL" "70"
check_env "ENABLE_PREDICTIVE_SCALING" "OPTIONAL" "true"
check_env "ENABLE_CUSTOM_METRICS" "OPTIONAL"

echo ""
echo "📋 REQUIREMENTS COMPLIANCE CHECK"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Requirement 1: Metric Collection
echo ""
echo "1️⃣  METRIC COLLECTION (Req: Prometheus scrape every 15s, 7-day retention)"
PROM_SCRAPE=$(aws logs tail /aws/lambda/$FUNCTION_NAME --since 10m --format short 2>&1 | grep "Successfully collected metrics" | tail -1)
if [ -n "$PROM_SCRAPE" ]; then
    echo "   ✅ Lambda queries Prometheus successfully"
    echo "      Latest: $(echo "$PROM_SCRAPE" | grep -oP "collected metrics: \K.*")"
else
    echo "   ❌ No recent Prometheus metric collection found"
fi

# Requirement 2: Scaling Thresholds
echo ""
echo "2️⃣  SCALING THRESHOLDS"
echo "   Required Scale-UP triggers (ANY):"
echo "      • CPU > 70% for 3min"
echo "      • Pending pods > 0 for 3min  "
echo "      • Memory > 75%"
RECENT_DECISION=$(aws logs tail /aws/lambda/$FUNCTION_NAME --since 1h --format short 2>&1 | grep "Reactive scaling decision" | tail -1)
if [ -n "$RECENT_DECISION" ]; then
    echo "   ✅ Scaling decision engine active"
    echo "      Latest: $(echo "$RECENT_DECISION" | grep -oP "decision: \K.*")"
else
    echo "   ⚠️  No recent scaling decisions (cluster might be stable)"
fi

echo ""
echo "   Required Scale-DOWN triggers (ALL):"
echo "      • CPU < 30% for 10min"
echo "      • No pending pods"
echo "      • Memory < 50%"

# Check if scale-down has occurred
SCALE_DOWN_EVENT=$(aws logs tail /aws/lambda/$FUNCTION_NAME --since 2h --format short 2>&1 | grep "scale_down" | tail -1)
if [ -n "$SCALE_DOWN_EVENT" ]; then
    echo "   ✅ Scale-down logic working (event found in last 2h)"
else
    echo "   ℹ️  No scale-down in last 2h (load might be steady)"
fi

# Requirement 3: Node Constraints
echo ""
echo "3️⃣  NODE CONSTRAINTS"
MIN=$(echo "$ENV_VARS" | jq -r '.MIN_NODES // "2"')
MAX=$(echo "$ENV_VARS" | jq -r '.MAX_NODES // "10"')
CURRENT=$(aws dynamodb get-item --table-name "node-fleet-dev-state" --key '{"cluster_id":{"S":"node-fleet-dev"}}' --output json 2>/dev/null | jq -r '.Item.node_count.N // "unknown"')

echo "   Required: MIN=2, MAX=10"
if [ "$MIN" = "2" ] && [ "$MAX" = "10" ]; then
    echo "   ✅ Configured correctly: MIN=$MIN, MAX=$MAX"
else
    echo "   ⚠️  Misconfigured: MIN=$MIN, MAX=$MAX"
fi
echo "   Current cluster nodes: $CURRENT"

# Requirement 4: Cooldown Periods
echo ""
echo "4️⃣  COOLDOWN PERIODS"
echo "   Required: 5min after scale-up, 10min after scale-down"
COOLDOWN_CHECK=$(aws logs tail /aws/lambda/$FUNCTION_NAME --since 30m --format short 2>&1 | grep "cooldown" | tail -3)
if [ -n "$COOLDOWN_CHECK" ]; then
    echo "   ✅ Cooldown logic detected in logs:"
    echo "$COOLDOWN_CHECK" | sed 's/^/      /'
else
    echo "   ℹ️  No recent cooldown events"
fi

# Requirement 5: Graceful Drain
echo ""
echo "5️⃣  GRACEFUL NODE DRAIN (Req: kubectl drain, respect PDBs, 5min timeout)"
DRAIN_EVENT=$(aws logs tail /aws/lambda/$FUNCTION_NAME --since 2h --format short 2>&1 | grep -A5 "Draining node" | head -10)
if [ -n "$DRAIN_EVENT" ]; then
    echo "   ✅ Node drain logic executed recently:"
    echo "$DRAIN_EVENT" | sed 's/^/      /'
else
    echo "   ℹ️  No drain operations in last 2h"
fi

# Requirement 6: DynamoDB State Management
echo ""
echo "6️⃣  DYNAMODB STATE & LOCKING (Req: Conditional writes, race prevention)"
TABLE_NAME=$(echo "$ENV_VARS" | jq -r '.STATE_TABLE // "node-fleet-dev-state"')
STATE_DATA=$(aws dynamodb get-item --table-name "$TABLE_NAME" --key '{"cluster_id":{"S":"node-fleet-dev"}}' --output json 2>/dev/null)

if [ -n "$STATE_DATA" ]; then
    echo "   ✅ DynamoDB state table accessible"
    NODE_COUNT=$(echo "$STATE_DATA" | jq -r '.Item.node_count.N // "0"')
    LAST_SCALE=$(echo "$STATE_DATA" | jq -r '.Item.last_scale_time.S // "Never"')
    SCALING_PROGRESS=$(echo "$STATE_DATA" | jq -r '.Item.scaling_in_progress.BOOL // false')
    
    echo "      • Node Count: $NODE_COUNT"
    echo "      • Last Scale: $LAST_SCALE"
    echo "      • Scaling In Progress: $SCALING_PROGRESS"
else
    echo "   ❌ Cannot read DynamoDB state"
fi

LOCK_USAGE=$(aws logs tail /aws/lambda/$FUNCTION_NAME --since 30m --format short 2>&1 | grep "Lock acquired\|Lock released" | tail -4)
if [ -n "$LOCK_USAGE" ]; then
    echo "   ✅ Lock acquisition/release working:"
    echo "$LOCK_USAGE" | sed 's/^/      /'
else
    echo "   ⚠️  No recent lock activity"
fi

# Requirement 7: CloudWatch Metrics Publishing
echo ""
echo "7️⃣  CLOUDWATCH METRICS PUBLISHING"
METRICS_PUBLISHED=$(aws logs tail /aws/lambda/$FUNCTION_NAME --since 30m --format short 2>&1 | grep "Published.*CloudWatch metrics" | tail -1)
if [ -n "$METRICS_PUBLISHED" ]; then
    echo "   ✅ CloudWatch metrics being published"
    echo "      $METRICS_PUBLISHED"
    
    # List available metrics
    METRIC_LIST=$(aws cloudwatch list-metrics --namespace NodeFleet/Autoscaler --output json | jq -r '.Metrics[].MetricName' | sort -u)
    echo "   Available metrics:"
    echo "$METRIC_LIST" | sed 's/^/      • /'
else
    echo "   ❌ No recent metric publications"
fi

# Requirement 8: Slack Notifications
echo ""
echo "8️⃣  SLACK NOTIFICATIONS (Req: Notify on all scaling events)"
SLACK_SENT=$(aws logs tail /aws/lambda/$FUNCTION_NAME --since 2h --format short 2>&1 | grep "Notification sent" | tail -1)
if [ -n "$SLACK_SENT" ]; then
    echo "   ✅ Slack notifications working"
    echo "      $SLACK_SENT"
else
    echo "   ℹ️  No notifications in last 2h (no scaling events)"
fi

# Requirement 9: EventBridge Schedule
echo ""
echo "9️⃣  EVENTBRIDGE SCHEDULE (Req: Every 2 minutes)"
SCHEDULE_RULE=$(aws events describe-rule --name "node-fleet-dev-autoscaler-schedule" --output json 2>/dev/null)
if [ -n "$SCHEDULE_RULE" ]; then
    SCHEDULE=$(echo "$SCHEDULE_RULE" | jq -r '.ScheduleExpression')
    STATE=$(echo "$SCHEDULE_RULE" | jq -r '.State')
    
    echo "   Schedule: $SCHEDULE"
    echo "   State: $STATE"
    
    if [ "$SCHEDULE" = "rate(2 minutes)" ]; then
        echo "   ✅ Configured as required (2 minutes)"
    else
        echo "   ⚠️  Currently: $SCHEDULE (Requirement: rate(2 minutes))"
    fi
else
    echo "   ❌ Cannot find EventBridge schedule"
fi

# Bonus Features
echo ""
echo "🌟 BONUS FEATURES"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Predictive Scaling
PREDICTIVE_ENABLED=$(echo "$ENV_VARS" | jq -r '.ENABLE_PREDICTIVE_SCALING // "false"')
echo "   Predictive Scaling: $PREDICTIVE_ENABLED"
if [ "$PREDICTIVE_ENABLED" = "true" ]; then
    echo "   ✅ Enabled (analyzes 7-day patterns)"
else
    echo "   ⚠️  Disabled"
fi

# Spot Instances
SPOT_PCT=$(echo "$ENV_VARS" | jq -r '.SPOT_PERCENTAGE // "0"')
echo "   Spot Instance Mix: ${SPOT_PCT}% Spot / $((100-SPOT_PCT))% On-Demand"
if [ "$SPOT_PCT" -gt 0 ]; then
    echo "   ✅ Cost optimization via Spot instances"
fi

# Custom Metrics
CUSTOM_METRICS=$(echo "$ENV_VARS" | jq -r '.ENABLE_CUSTOM_METRICS // "false"')
echo "   Custom Metrics (Queue/API): $CUSTOM_METRICS"

echo ""
echo "========================================"
echo "SUMMARY"
echo "========================================"

# Count checks
TOTAL_CHECKS=9
echo "Core Requirements: $TOTAL_CHECKS checks"
echo ""
echo "✅ = Working as expected"
echo "⚠️  = Working but needs attention"
echo "❌ = Not working / Missing"
echo "ℹ️  = No recent activity (normal if cluster stable)"
echo ""
echo "Review the output above for detailed compliance status."
echo ""
