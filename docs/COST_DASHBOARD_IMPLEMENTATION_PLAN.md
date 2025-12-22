# Grafana Cost Dashboard - Implementation Plan

## Overview

Build comprehensive AWS cost tracking dashboard in Grafana showing real-time costs, service breakdown, and savings vs fixed cluster.

**Total Timeline:** 3-4 weeks (working 2-3 hours/day)  
**Effort:** ~20-25 hours total  
**Dependencies:** Prometheus, Grafana, K3s cluster, AWS account

---

## Phase 1: Basic EC2 Cost Tracking (Week 1)

**Goal:** Show real-time EC2 worker node costs in Grafana

### Tasks

#### 1.1 Configure Prometheus Metrics (2 hours)

**Already exists:** Prometheus scrapes K3s API for node count

**Verify deployment:**

```bash
# Check Prometheus is running
kubectl get pods -n monitoring | grep prometheus

# Test Prometheus endpoint
curl http://$MASTER_IP:30090/api/v1/query?query=kube_node_info

# Expected response: JSON with node data
```

**Add cost calculation metric (optional):**

```yaml
# k3s/prometheus-custom-metrics.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: prometheus-cost-rules
  namespace: monitoring
data:
  cost-rules.yml: |
    groups:
      - name: cost_tracking
        interval: 15s
        rules:
          # Worker node count
          - record: smartscale:workers:count
            expr: count(kube_node_info{node=~"worker.*"})
          
          # Hourly cost rate
          - record: smartscale:cost:hourly_rate
            expr: count(kube_node_info{node=~"worker.*"}) * 0.0416
          
          # Daily cost projection
          - record: smartscale:cost:daily_projection
            expr: count(kube_node_info{node=~"worker.*"}) * 0.0416 * 24
```

**Deploy:**

```bash
kubectl apply -f k3s/prometheus-custom-metrics.yaml
kubectl rollout restart deployment prometheus -n monitoring
```

#### 1.2 Create Basic Grafana Dashboard (1.5 hours)

**File:** `monitoring/grafana-dashboards/ec2-cost-basic.json`

**Panels to create:**

1. **Current Node Count** (Stat panel)

   - Query: `count(kube_node_info{node=~"worker.*"})`
   - Display: Big number with trend

2. **Current Hourly Cost Rate** (Stat panel)

   - Query: `count(kube_node_info{node=~"worker.*"}) * 0.0416`
   - Unit: `$ (currency)`
   - Display: `$0.208/hr`

3. **Today's Cost So Far** (Stat panel)

   - Query: `sum_over_time((count(kube_node_info{node=~"worker.*"}) * 0.0416)[1d:15s]) / (3600/15)`
   - Explanation: Sum all 15-second samples over 24h, divide by samples per hour
   - Display: `$2.87` (updates in real-time)

4. **Node Count Over Time** (Graph panel)
   - Query: `count(kube_node_info{node=~"worker.*"})`
   - Time range: Last 24 hours
   - Shows scaling activity

**Create dashboard:**

```bash
# Use Grafana UI or import JSON
# http://$MASTER_IP:3000 (default port)
# Login: admin/admin (change password)
# Dashboards → Import → Upload ec2-cost-basic.json
```

#### 1.3 Test & Validate (0.5 hours)

**Validation checklist:**

```bash
# 1. Verify node count is accurate
kubectl get nodes | grep worker | wc -l
# Compare with Grafana "Current Node Count" panel

# 2. Trigger scale-up test
kubectl run stress-test --image=nginx --replicas=20
# Wait 5-10 minutes for autoscaler
# Verify Grafana shows node count increase

# 3. Check cost calculation
# Manual: 5 nodes × $0.0416 = $0.208/hr
# Compare with "Hourly Cost Rate" panel

# 4. Verify daily cost accumulates
# Come back after 1 hour, check "Today's Cost" increased

# Cleanup
kubectl delete deployment stress-test
```

**Deliverables:**

- ✅ Prometheus collecting node metrics
- ✅ Basic Grafana dashboard showing EC2 costs
- ✅ Real-time cost updates every 15 seconds
- ✅ 86% of total AWS costs tracked

---

## Phase 2: CloudWatch Billing Integration (Week 2)

**Goal:** Add all AWS service costs via CloudWatch billing metrics

### Tasks

#### 2.1 Enable AWS Billing Metrics (0.5 hours)

**AWS Console steps:**

```bash
# 1. Enable billing alerts
AWS Console → Billing Dashboard → Billing Preferences
☑ Receive Billing Alerts
☑ Receive Free Tier Usage Alerts
Click "Save preferences"

# 2. Wait 4-6 hours for first metrics to populate

# 3. Verify metrics exist
aws cloudwatch list-metrics --namespace AWS/Billing --region us-east-1

# Expected output:
{
  "Metrics": [
    {
      "Namespace": "AWS/Billing",
      "MetricName": "EstimatedCharges",
      "Dimensions": [{"Name": "ServiceName", "Value": "AmazonEC2"}]
    },
    {
      "Namespace": "AWS/Billing",
      "MetricName": "EstimatedCharges",
      "Dimensions": [{"Name": "ServiceName", "Value": "AWSLambda"}]
    },
    ...
  ]
}
```

**Important:** Billing metrics are ONLY available in `us-east-1` region, even if resources are in `ap-south-1`.

#### 2.2 Add CloudWatch Data Source to Grafana (1 hour)

**Option A: IAM Role (Recommended for EC2)**

```bash
# 1. Create IAM policy for Grafana
cat > grafana-cloudwatch-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "cloudwatch:DescribeAlarmsForMetric",
        "cloudwatch:ListMetrics",
        "cloudwatch:GetMetricStatistics",
        "cloudwatch:GetMetricData"
      ],
      "Resource": "*"
    }
  ]
}
EOF

aws iam create-policy \
  --policy-name GrafanaCloudWatchReadOnly \
  --policy-document file://grafana-cloudwatch-policy.json

# 2. Attach to EC2 master node role
INSTANCE_PROFILE=$(aws ec2 describe-instances \
  --filters "Name=tag:Role,Values=k3s-master" \
  --query 'Reservations[0].Instances[0].IamInstanceProfile.Arn' \
  --output text)

# Extract role name and attach policy
aws iam attach-role-policy \
  --role-name k3s-master-role \
  --policy-arn arn:aws:iam::$AWS_ACCOUNT_ID:policy/GrafanaCloudWatchReadOnly

# 3. Configure in Grafana UI
# Configuration → Data Sources → Add data source → CloudWatch
# Authentication Provider: EC2 IAM Role
# Default Region: us-east-1
# Click "Save & Test"
```

**Option B: Access Keys (Quick but less secure)**

```bash
# Create dedicated user
aws iam create-user --user-name grafana-billing-reader

# Attach read-only policy
aws iam attach-user-policy \
  --user-name grafana-billing-reader \
  --policy-arn arn:aws:iam::aws:policy/CloudWatchReadOnlyAccess

# Create access keys
aws iam create-access-key --user-name grafana-billing-reader

# Add to Grafana:
# Configuration → Data Sources → Add CloudWatch
# Authentication: Access & Secret Key
# Access Key ID: <from above>
# Secret Access Key: <from above>
# Default Region: us-east-1
```

#### 2.3 Create Service Cost Breakdown Dashboard (2 hours)

**File:** `monitoring/grafana-dashboards/aws-cost-breakdown.json`

**Panels:**

1. **Total AWS Bill** (Stat panel)

   - Data source: CloudWatch
   - Namespace: `AWS/Billing`
   - Metric: `EstimatedCharges`
   - Dimension: `Currency=USD`
   - Statistic: `Maximum`
   - Period: `6 hours`

2. **Cost by Service** (Bar chart)

   - Query for each service:

   ```
   EC2:           EstimatedCharges, ServiceName=AmazonEC2
   Lambda:        EstimatedCharges, ServiceName=AWSLambda
   DynamoDB:      EstimatedCharges, ServiceName=AmazonDynamoDB
   SecretsManager: EstimatedCharges, ServiceName=AWSSecretsManager
   SNS:           EstimatedCharges, ServiceName=AmazonSNS
   EventBridge:   EstimatedCharges, ServiceName=AmazonEventBridge
   CloudWatch:    EstimatedCharges, ServiceName=AmazonCloudWatch
   DataTransfer:  EstimatedCharges, ServiceName=AWSDataTransfer
   ```

3. **Service Cost Pie Chart** (Pie chart)

   - Same queries as above
   - Shows percentage breakdown

4. **Daily Cost Trend** (Time series)
   - Total estimated charges over last 30 days
   - Period: 1 day
   - Shows cost fluctuations

#### 2.4 Test CloudWatch Integration (0.5 hours)

**Validation:**

```bash
# 1. Check data is flowing
# Open Grafana → Explore → CloudWatch data source
# Query: EstimatedCharges (no dimensions)
# Should show current month's total bill

# 2. Compare with AWS Billing Console
# AWS Console → Billing → Bills
# Compare total with Grafana "Total AWS Bill" panel
# Allow 4-6 hour delay in CloudWatch data

# 3. Verify service breakdown
# Check EC2 cost in Grafana vs AWS Console
# Should match within $0.50 (CloudWatch rounds)
```

**Deliverables:**

- ✅ CloudWatch data source configured
- ✅ All AWS service costs visible
- ✅ Cost breakdown by service (EC2, Lambda, DynamoDB, etc.)
- ✅ 100% of AWS costs tracked (6-hour delay)

---

## Phase 3: Advanced Cost Analytics (Week 3)

**Goal:** Before/after comparison, savings calculator, projections

### Tasks

#### 3.1 Create Savings Comparison Dashboard (2 hours)

**File:** `monitoring/grafana-dashboards/cost-savings-analysis.json`

**Panels:**

1. **Fixed vs Autoscaled Cost** (Bar gauge)

   ```
   # Fixed baseline (10 nodes always)
   Constant: 10 * 0.0416 * 24 = $9.984/day

   # Current autoscaled cost (Prometheus)
   Query: sum_over_time((count(kube_node_info{node=~"worker.*"}) * 0.0416)[1d:15s]) / (3600/15)

   # Display both as horizontal bars
   ```

2. **Daily Savings Amount** (Stat panel)

   ```
   # Calculation
   (10 * 0.0416 * 24) - (actual_daily_cost)

   # Display: "$3.21 saved today"
   # Color: Green if positive, red if negative
   ```

3. **Savings Percentage** (Gauge)

   ```
   # Calculation
   ((fixed_cost - actual_cost) / fixed_cost) * 100

   # Display: "48% cost reduction"
   # Gauge: 0-100%
   ```

4. **30-Day Savings Trend** (Graph)

   ```
   # Daily savings over last 30 days
   # Shows which days saved most (weekends, low traffic)
   # Shows which days saved least (high traffic)
   ```

5. **Monthly Projection** (Stat panel)

   ```
   # Current month cost so far
   Query: CloudWatch EstimatedCharges, current month

   # Projected end-of-month total
   (current_cost / days_elapsed) * days_in_month

   # Display: "$210 projected for December"
   # vs "Baseline: $360 (10 nodes)"
   ```

#### 3.2 Add Cost Alerts (1 hour)

**Grafana Alerts:**

1. **Daily Cost Exceeds Threshold**

   ```yaml
   Alert: "Daily cost above $8"
   Condition: daily_cost > 8
   For: 1 hour
   Action: Send to Slack channel #smartscale-alerts
   Message: "⚠️ Daily AWS cost: $9.20 (threshold: $8.00)"
   ```

2. **No Cost Savings**

   ```yaml
   Alert: "Autoscaler not saving money"
   Condition: daily_cost >= (10 * 0.0416 * 24)
   For: 24 hours
   Action: Send to Slack
   Message: "❌ Cost same as fixed cluster! Check autoscaler."
   ```

3. **Budget Limit Warning**
   ```yaml
   Alert: "Monthly budget 80% consumed"
   Condition: monthly_cost > 240 # 80% of $300 budget
   For: 1 hour
   Action: Send to Slack
   Message: "💰 Month-to-date: $245 (Budget: $300)"
   ```

**Configure Slack notifications:**

```bash
# Grafana → Alerting → Contact points → New contact point
Name: smartscale-slack
Integration: Slack
Webhook URL: https://hooks.slack.com/services/YOUR/WEBHOOK/URL
# Get from Slack: Apps → Incoming Webhooks
```

#### 3.3 Add Historical Cost Tracking (1.5 hours)

**Problem:** Prometheus/Grafana only stores metrics for 15 days default

**Solution: Export daily costs to DynamoDB**

**Lambda function:** `lambda/cost_tracker.py`

```python
import boto3
from datetime import datetime

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table('smartscale-cost-history')

def lambda_handler(event, context):
    """
    Runs daily via EventBridge
    Queries CloudWatch billing metrics
    Stores in DynamoDB for long-term tracking
    """
    cloudwatch = boto3.client('cloudwatch', region_name='us-east-1')

    # Get yesterday's total cost
    response = cloudwatch.get_metric_statistics(
        Namespace='AWS/Billing',
        MetricName='EstimatedCharges',
        Dimensions=[{'Name': 'Currency', 'Value': 'USD'}],
        StartTime=datetime.utcnow() - timedelta(days=1),
        EndTime=datetime.utcnow(),
        Period=86400,  # 1 day
        Statistics=['Maximum']
    )

    daily_cost = response['Datapoints'][0]['Maximum']

    # Store in DynamoDB
    table.put_item(Item={
        'date': str(datetime.utcnow().date()),
        'total_cost': Decimal(str(daily_cost)),
        'ec2_cost': get_service_cost('AmazonEC2'),
        'lambda_cost': get_service_cost('AWSLambda'),
        'baseline_cost': Decimal('9.984'),  # 10 nodes fixed
        'savings': Decimal('9.984') - Decimal(str(daily_cost))
    })

    return {'statusCode': 200, 'body': 'Cost tracked'}
```

**EventBridge rule:**

```bash
# Run daily at 2 AM UTC
aws events put-rule \
  --name smartscale-daily-cost-tracker \
  --schedule-expression "cron(0 2 * * ? *)"

aws events put-targets \
  --rule smartscale-daily-cost-tracker \
  --targets "Id=1,Arn=arn:aws:lambda:ap-south-1:$ACCOUNT_ID:function:cost-tracker"
```

**Benefits:**

- ✅ Store costs indefinitely (not limited to Prometheus retention)
- ✅ Query historical savings over months/years
- ✅ Generate monthly/quarterly reports
- ✅ Track long-term cost trends

#### 3.4 Test Advanced Features (0.5 hours)

**Validation:**

```bash
# 1. Verify savings calculation
# Fixed: 10 nodes × $0.0416 × 24h = $9.984/day
# Current: Check Grafana "Daily Savings Amount"
# Math: $9.984 - actual_cost should match panel

# 2. Test cost alert
# Manually increase node count to trigger alert
kubectl scale deployment test-app --replicas=100
# Wait for autoscaler to add nodes
# Check Slack for alert notification

# 3. Verify DynamoDB storage
aws dynamodb scan --table-name smartscale-cost-history
# Should show daily cost entries

# 4. Generate test report
# Query DynamoDB for last 30 days
# Calculate total savings
# Should match Grafana monthly projection
```

**Deliverables:**

- ✅ Before/after cost comparison dashboard
- ✅ Savings calculator with percentage
- ✅ Cost alerts via Slack
- ✅ Historical cost data in DynamoDB
- ✅ Monthly savings reports

---

## Phase 4: Polish & Optimization (Week 4)

**Goal:** Performance tuning, documentation, final testing

### Tasks

#### 4.1 Optimize Dashboard Performance (1 hour)

**Issues to fix:**

1. **Slow queries:** Large time ranges slow down Grafana

   ```
   # Bad: Query 30 days at 15-second intervals
   # 30d × 86400s/d ÷ 15s = 172,800 data points

   # Good: Aggregate to hourly for 30-day view
   avg_over_time(metric[1h])
   # 30d × 24h = 720 data points (240x faster)
   ```

2. **Cache CloudWatch queries:**

   ```yaml
   # Grafana data source settings
   Cache timeout: 300 seconds (5 minutes)
   # Billing data updates every 4-6 hours anyway
   ```

3. **Use dashboard variables:**

   ```yaml
   # Add variable for time range
   Variable name: time_range
   Type: Interval
   Values: 1h, 6h, 24h, 7d, 30d
   # Use in queries: [${time_range}]
   ```

#### 4.2 Create Combined Master Dashboard (1.5 hours)

**File:** `monitoring/grafana-dashboards/smartscale-cost-overview.json`

**Layout:** Single comprehensive dashboard

```
┌─────────────────────────────────────────────────────────────┐
│ SmartScale Cost Dashboard                    Last update: 2m│
├─────────────────────────────────────────────────────────────┤
│ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐│
│ │ Nodes:5 │ │$0.208/hr│ │$2.87/day│ │ $210/mo │ │ 42% ↓   ││
│ └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘│
├─────────────────────────────────────────────────────────────┤
│ ┌────────────────────────────┐ ┌────────────────────────────┐│
│ │ Node Count (24h)           │ │ Hourly Cost (24h)          ││
│ │                         ╭─ │ │                         ╭─ ││
│ │                    ╭────╯  │ │                    ╭────╯  ││
│ │  ────╮        ╭────╯       │ │  ────╮        ╭────╯       ││
│ │      ╰────────╯            │ │      ╰────────╯            ││
│ └────────────────────────────┘ └────────────────────────────┘│
├─────────────────────────────────────────────────────────────┤
│ ┌────────────────────────────┐ ┌────────────────────────────┐│
│ │ Cost by Service            │ │ Fixed vs Autoscaled        ││
│ │ ▓▓▓▓▓▓▓▓▓▓▓▓ EC2    86%   │ │ Fixed 10:  $9.98  ████████ ││
│ │ ▓▓ Lambda         3%       │ │ Actual:    $5.20  ████     ││
│ │ ▓ DynamoDB        2%       │ │ Savings:   $4.78  (48%)    ││
│ │ ▓ Other           9%       │ │                            ││
│ └────────────────────────────┘ └────────────────────────────┘│
├─────────────────────────────────────────────────────────────┤
│ ┌────────────────────────────────────────────────────────────┐│
│ │ 30-Day Cost Trend                                          ││
│ │ $10 ┤                    ╭─╮                              ││
│ │     ┤         ╭─────╮    │ │                              ││
│ │  $5 ┤    ╭────╯     ╰────╯ ╰─╮                            ││
│ │     ┤────╯                    ╰────                        ││
│ │  $0 └────────────────────────────────────────────         ││
│ │     Dec 1      Dec 15            Dec 30                    ││
│ └────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
```

#### 4.3 Write Documentation (1 hour)

**File:** `monitoring/README.md`

```markdown
# SmartScale Cost Monitoring Guide

## Quick Access

- Grafana: http://<MASTER_IP>:3000
- Login: admin / <your-password>
- Dashboard: SmartScale Cost Overview

## Dashboards

1. **EC2 Cost Basic**: Real-time worker node costs
2. **AWS Cost Breakdown**: All services via CloudWatch
3. **Cost Savings Analysis**: Before/after comparison
4. **SmartScale Cost Overview**: Combined master dashboard

## Key Metrics

- **Current Node Count**: Number of active worker nodes
- **Hourly Cost Rate**: Real-time $/hour
- **Daily Cost**: Total spent today
- **Monthly Projection**: Estimated end-of-month bill
- **Savings %**: Cost reduction vs 10-node baseline

## Interpreting Costs

### Expected Daily Costs

- **Low traffic (2-3 nodes)**: $2-3/day
- **Medium traffic (4-6 nodes)**: $4-6/day
- **High traffic (8-10 nodes)**: $8-10/day

### Monthly Baseline

- Fixed 10 nodes: $299.52/month
- Autoscaled average: $150-180/month
- Target savings: 40-50%

## Alerts

Configured alerts send to Slack #smartscale-alerts:

- Daily cost > $8
- No savings vs baseline
- Monthly budget 80% consumed

## Troubleshooting

**CloudWatch data not showing:**

- Check billing metrics enabled in AWS Console
- Verify CloudWatch data source region is us-east-1
- Allow 4-6 hours for first data to appear

**Cost seems too high:**

- Check for stuck instances (not terminating)
- Verify autoscaler is running: `aws lambda invoke ...`
- Check if max nodes (10) reached during spike

**Savings lower than expected:**

- Check average node count over 7 days
- High traffic increases node count (expected)
- Compare weekend vs weekday costs
```

#### 4.4 Final End-to-End Testing (1 hour)

**Test Plan:**

```bash
#!/bin/bash
echo "=== SmartScale Cost Dashboard E2E Test ==="

# Test 1: Verify all data sources
echo "1. Testing Prometheus connection..."
curl -s http://$MASTER_IP:30090/-/healthy || echo "FAIL: Prometheus down"

echo "2. Testing Grafana..."
curl -s http://$MASTER_IP:3000/api/health || echo "FAIL: Grafana down"

echo "3. Testing CloudWatch data source..."
# Check via Grafana API
curl -s -u admin:$GRAFANA_PASSWORD \
  http://$MASTER_IP:3000/api/datasources | grep CloudWatch || echo "FAIL: CloudWatch not configured"

# Test 2: Generate scaling activity
echo "4. Triggering scale-up..."
kubectl run load-test --image=nginx --replicas=30
sleep 600  # Wait 10 minutes for autoscaler

NODES_BEFORE=$(kubectl get nodes --no-headers | wc -l)
echo "Nodes before scale-up: $NODES_BEFORE"

# Wait for scale-up
sleep 600  # Another 10 minutes

NODES_AFTER=$(kubectl get nodes --no-headers | wc -l)
echo "Nodes after scale-up: $NODES_AFTER"

if [ $NODES_AFTER -gt $NODES_BEFORE ]; then
  echo "✅ Scale-up successful"
else
  echo "❌ Scale-up failed"
fi

# Test 3: Verify cost calculations
echo "5. Verifying cost calculations..."
# Expected: NODES_AFTER × $0.0416 × hours
EXPECTED_HOURLY=$(echo "$NODES_AFTER * 0.0416" | bc)
echo "Expected hourly rate: \$$EXPECTED_HOURLY"
echo "Check Grafana 'Hourly Cost Rate' panel matches this value"

# Test 4: Check CloudWatch metrics
echo "6. Checking CloudWatch billing data..."
aws cloudwatch get-metric-statistics \
  --namespace AWS/Billing \
  --metric-name EstimatedCharges \
  --dimensions Name=Currency,Value=USD \
  --start-time $(date -u -d '1 day ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 86400 \
  --statistics Maximum \
  --region us-east-1

# Test 5: Verify alerts
echo "7. Testing cost alert..."
# Force high cost by scaling to max
kubectl scale deployment load-test --replicas=100
echo "Check Slack #smartscale-alerts for notification in 5 minutes"

# Cleanup
echo "8. Cleanup..."
kubectl delete deployment load-test

echo "=== Test Complete ==="
echo "Manual verification:"
echo "1. Open Grafana: http://$MASTER_IP:3000"
echo "2. Check 'SmartScale Cost Overview' dashboard"
echo "3. Verify all panels show data"
echo "4. Check Slack alerts received"
```

**Deliverables:**

- ✅ Optimized dashboard performance
- ✅ Combined master dashboard
- ✅ Complete documentation
- ✅ End-to-end testing completed
- ✅ Production-ready cost monitoring

---

## Phase 5: Long-Term Maintenance (Ongoing)

**Goal:** Keep cost tracking accurate and useful

### Monthly Tasks

#### 5.1 Review Cost Trends (30 minutes/month)

```bash
# Generate monthly report
aws dynamodb query \
  --table-name smartscale-cost-history \
  --key-condition-expression "date between :start and :end" \
  --expression-attribute-values '{
    ":start": {"S": "2025-12-01"},
    ":end": {"S": "2025-12-31"}
  }'

# Calculate monthly totals
# Compare to previous month
# Identify cost spikes
# Adjust autoscaler thresholds if needed
```

#### 5.2 Update Pricing (quarterly)

AWS pricing changes occasionally:

```yaml
# Update in Prometheus cost rules
# k3s/prometheus-custom-metrics.yaml

# Old: 0.0416 (check current AWS pricing)
# New: <updated rate>

# Redeploy Prometheus config
kubectl apply -f k3s/prometheus-custom-metrics.yaml
```

#### 5.3 Audit & Optimize (quarterly)

```bash
# Review top cost drivers
# Check for unused resources
# Optimize autoscaler parameters
# Consider Reserved Instances if usage stable
```

---

## Success Criteria

**Phase 1 Complete:**

- [ ] Prometheus collecting node count metrics
- [ ] Basic Grafana dashboard showing EC2 costs
- [ ] Real-time cost updates working
- [ ] Manual testing confirms accuracy

**Phase 2 Complete:**

- [ ] CloudWatch billing metrics enabled
- [ ] CloudWatch data source in Grafana
- [ ] All AWS services visible in dashboard
- [ ] Cost breakdown by service accurate

**Phase 3 Complete:**

- [ ] Savings comparison dashboard working
- [ ] Cost alerts configured and tested
- [ ] Historical costs stored in DynamoDB
- [ ] Monthly projections accurate

**Phase 4 Complete:**

- [ ] Dashboard performance optimized
- [ ] Master dashboard created
- [ ] Documentation written
- [ ] End-to-end testing passed

**Production Ready:**

- [ ] All dashboards accessible via Grafana
- [ ] Cost data updating in real-time (Prometheus) and daily (CloudWatch)
- [ ] Savings vs baseline clearly shown
- [ ] Slack alerts working
- [ ] Team trained on using dashboards

---

## Rollback Plan

If issues arise during implementation:

### Phase 1 Rollback

```bash
# Remove Prometheus custom metrics
kubectl delete configmap prometheus-cost-rules -n monitoring
kubectl rollout undo deployment prometheus -n monitoring

# Delete Grafana dashboard
# Grafana UI → Dashboard → Settings → Delete
```

### Phase 2 Rollback

```bash
# Remove CloudWatch data source
# Grafana → Configuration → Data Sources → CloudWatch → Delete

# Remove IAM permissions
aws iam detach-role-policy \
  --role-name k3s-master-role \
  --policy-arn arn:aws:iam::$ACCOUNT_ID:policy/GrafanaCloudWatchReadOnly
```

### Phase 3 Rollback

```bash
# Delete cost tracker Lambda
aws lambda delete-function --function-name cost-tracker

# Delete EventBridge rule
aws events delete-rule --name smartscale-daily-cost-tracker

# Delete DynamoDB table
aws dynamodb delete-table --table-name smartscale-cost-history
```

---

## Timeline Summary

| Phase     | Duration    | Effort        | Key Deliverable                     |
| --------- | ----------- | ------------- | ----------------------------------- |
| Phase 1   | Week 1      | 4 hours       | EC2 cost tracking                   |
| Phase 2   | Week 2      | 4 hours       | CloudWatch integration              |
| Phase 3   | Week 3      | 5 hours       | Savings analysis                    |
| Phase 4   | Week 4      | 4 hours       | Polish & testing                    |
| **Total** | **4 weeks** | **~20 hours** | **Production-ready cost dashboard** |

---

## Cost of Implementation

**AWS Costs:**

- CloudWatch billing metrics: **Free** (basic metrics)
- DynamoDB cost history table: **~$0.50/month** (minimal writes)
- Cost tracker Lambda: **~$0.10/month** (daily invocations)
- **Total additional cost: <$1/month**

**Time Investment:**

- Initial setup: 20 hours
- Monthly maintenance: 30 minutes
- ROI: Prove $130-160/month savings

**Worth it?** Absolutely! Pays for itself immediately by proving cost savings to stakeholders.

---

## Next Steps

Ready to start? Begin with Phase 1:

```bash
# 1. Verify Prometheus is running
kubectl get pods -n monitoring

# 2. Create prometheus-custom-metrics.yaml
vi k3s/prometheus-custom-metrics.yaml

# 3. Deploy cost tracking rules
kubectl apply -f k3s/prometheus-custom-metrics.yaml

# 4. Access Grafana
open http://$MASTER_IP:3000

# 5. Create your first cost panel!
```

Good luck! 🚀
