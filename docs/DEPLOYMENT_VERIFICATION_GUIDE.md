# Deployment Verification Guide

## 🎯 Overview

This guide provides **step-by-step verification** for deploying and validating the SmartScale K3s Autoscaler in AWS. Follow each section sequentially and check off items as you complete them.

**Estimated Time**: 2-3 hours  
**Prerequisites**: AWS account, Pulumi CLI, kubectl, Node.js, Python 3.11

---

## Phase 1: Pre-Deployment Checks (15 minutes)

### ✅ Environment Setup

```bash
# 1. Verify AWS credentials
aws sts get-caller-identity
# Expected: Returns your AWS account ID and user ARN

# 2. Check AWS region configuration
aws configure get region
# Expected: ap-southeast-1 (or your preferred region)

# 3. Verify Pulumi installation
pulumi version
# Expected: v3.x.x or higher

# 4. Check kubectl
kubectl version --client
# Expected: Client version displayed

# 5. Verify Node.js and npm
node --version && npm --version
# Expected: Node v18+ and npm v9+

# 6. Check Python version
python3 --version
# Expected: Python 3.11.x
```

### ✅ Cost Estimation

Before deploying, estimate your AWS costs:

```bash
# Check current EC2 pricing for t3.small in your region
aws pricing get-products \
  --service-code AmazonEC2 \
  --filters "Type=TERM_MATCH,Field=instanceType,Value=t3.small" \
  --region us-east-1 | grep -A 10 "ap-southeast-1"

# Estimated monthly costs:
# - 1 Master (always on): ~$15/month
# - 2 Workers (minimum): ~$30/month
# - Lambda: ~$0.50/month
# - DynamoDB: ~$1-2/month
# - NAT Gateway: ~$70/month (biggest cost!)
# - CloudWatch: ~$3-5/month
# Total: ~$120-125/month baseline
```

### ✅ Repository Setup

```bash
# Clone repository
git clone https://github.com/BayajidAlam/node-fleet.git
cd node-fleet

# Verify project structure
ls -la
# Expected: pulumi/, lambda/, k3s/, tests/, docs/, demo-app/, etc.

# Check all critical files exist
test -f pulumi/package.json && \
test -f lambda/autoscaler.py && \
test -f k3s/master-setup.sh && \
echo "✅ All critical files present" || \
echo "❌ Missing files - check repository"
```

---

## Phase 2: Infrastructure Deployment (30-45 minutes)

### ✅ Pulumi Stack Initialization

```bash
cd pulumi
npm install

# Initialize stack
pulumi stack init dev

# Configure required settings
pulumi config set aws:region ap-southeast-1
pulumi config set node-fleet:clusterName node-fleet-cluster
pulumi config set node-fleet:minNodes 2
pulumi config set node-fleet:maxNodes 10

# Verify configuration
pulumi config
# Expected: Shows all configured values
```

### ✅ Generate SSH Key Pair

```bash
# Generate key pair for EC2 access
ssh-keygen -t rsa -b 4096 -f ~/.ssh/node-fleet -N ""

# Import to AWS
aws ec2 import-key-pair \
  --key-name node-fleet \
  --public-key-material fileb://~/.ssh/node-fleet.pub \
  --region ap-southeast-1

# Verify import
aws ec2 describe-key-pairs --key-names node-fleet --region ap-southeast-1
# Expected: Key pair details returned
```

### ✅ Deploy Infrastructure

```bash
# Preview deployment (check what will be created)
pulumi preview

# Expected output:
# + Create 40-50 resources (VPC, subnets, EC2, Lambda, DynamoDB, etc.)
# Review carefully before proceeding

# Deploy infrastructure
pulumi up --yes

# Monitor deployment progress (20-30 minutes)
# Watch for any errors - common issues:
# - IAM permission errors
# - Resource quota limits
# - VPC CIDR conflicts
```

### ✅ Verify Infrastructure Created

```bash
# Get stack outputs
pulumi stack output

# Save critical outputs
export MASTER_PUBLIC_IP=$(pulumi stack output masterPublicIpAddress)
export MASTER_PRIVATE_IP=$(pulumi stack output masterPrivateIpAddress)
export LAMBDA_ARN=$(pulumi stack output autoscalerFunctionArn)
export STATE_TABLE=$(pulumi stack output stateTableName)

echo "Master Public IP: $MASTER_PUBLIC_IP"
echo "Lambda ARN: $LAMBDA_ARN"
echo "State Table: $STATE_TABLE"

# Verify VPC created
aws ec2 describe-vpcs --filters "Name=tag:Name,Values=node-fleet-vpc" --region ap-southeast-1
# Expected: VPC with CIDR 10.0.0.0/16

# Verify master instance running
aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=node-fleet-master" \
  --query "Reservations[0].Instances[0].State.Name" \
  --region ap-southeast-1
# Expected: "running"

# Verify Lambda function exists
aws lambda get-function --function-name node-fleet-cluster-autoscaler --region ap-southeast-1
# Expected: Function configuration returned

# Verify DynamoDB table created
aws dynamodb describe-table --table-name $STATE_TABLE --region ap-southeast-1
# Expected: Table details with status ACTIVE
```

**✅ Checkpoint**: All infrastructure resources created successfully

---

## Phase 3: K3s Cluster Setup (20-30 minutes)

### ✅ SSH to Master Node

```bash
# Test SSH connection
ssh -i ~/.ssh/node-fleet ubuntu@$MASTER_PUBLIC_IP "echo '✅ SSH connection successful'"

# If connection fails, check:
# 1. Security group allows SSH from your IP
# 2. Key permissions: chmod 600 ~/.ssh/node-fleet
# 3. Instance is fully initialized (wait 2-3 minutes after launch)
```

### ✅ Run Master Setup Script

```bash
# SSH to master
ssh -i ~/.ssh/node-fleet ubuntu@$MASTER_PUBLIC_IP

# On master node:
# The setup script should already be in /tmp from User Data
sudo bash /tmp/master-setup.sh

# Expected output:
# - System packages updated
# - K3s server installed
# - Prometheus deployed
# - Grafana deployed
# - kube-state-metrics deployed
# - All pods running

# Monitor installation (5-10 minutes)
# Press Ctrl+C when complete
```

### ✅ Verify K3s Cluster

```bash
# On master node:

# 1. Check K3s is running
sudo systemctl status k3s
# Expected: active (running)

# 2. Verify nodes
sudo kubectl get nodes
# Expected: 1 node in Ready state

# 3. Check all namespaces
sudo kubectl get pods --all-namespaces
# Expected: All pods Running or Completed

# 4. Verify Prometheus
sudo kubectl get pods -n monitoring
# Expected: prometheus-0 Running, kube-state-metrics Running

# 5. Test Prometheus API
curl -s "http://localhost:30090/api/v1/query?query=up" | jq '.status'
# Expected: "success"

# 6. Verify Grafana
sudo kubectl get svc -n monitoring grafana
# Expected: NodePort service on 32000
```

### ✅ Verify K3s Token in Secrets Manager

```bash
# On your local machine:
aws secretsmanager get-secret-value \
  --secret-id node-fleet/k3s-token \
  --region ap-southeast-1 \
  --query SecretString --output text

# Expected: K3s token string (starts with K10...)
# If empty or error, manually update:
# ssh to master and run:
# K3S_TOKEN=$(sudo cat /var/lib/rancher/k3s/server/node-token)
# aws secretsmanager update-secret --secret-id node-fleet/k3s-token --secret-string "$K3S_TOKEN"
```

**✅ Checkpoint**: K3s cluster operational with monitoring stack

---

## Phase 4: Lambda Autoscaler Verification (15-20 minutes)

### ✅ Check Lambda Execution

```bash
# 1. Verify Lambda is triggered by EventBridge
aws lambda get-function --function-name node-fleet-cluster-autoscaler --region ap-southeast-1

# 2. Check recent invocations
aws lambda get-function-configuration \
  --function-name node-fleet-cluster-autoscaler \
  --region ap-southeast-1 | jq '.LastUpdateStatus'
# Expected: "Successful"

# 3. Tail Lambda logs (wait for next 2-minute trigger)
aws logs tail /aws/lambda/node-fleet-cluster-autoscaler --follow --region ap-southeast-1

# Expected log output (within 2 minutes):
# START RequestId: ...
# [INFO] Autoscaler triggered for cluster: node-fleet-cluster
# [INFO] Step 1: Collecting metrics from Prometheus
# [INFO] Metrics collected: {'cpu_usage': X, 'memory_usage': Y, ...}
# [INFO] Step 2: Acquiring DynamoDB lock
# [INFO] Lock acquired for cluster ...
# [INFO] Step 3: Evaluating scaling decision (current nodes: 1)
# [INFO] Log: No scaling needed
# [INFO] Lock released for cluster ...
# END RequestId: ...
```

### ✅ Verify DynamoDB State

```bash
# Check state table
aws dynamodb get-item \
  --table-name $STATE_TABLE \
  --key '{"cluster_id": {"S": "node-fleet-cluster"}}' \
  --region ap-southeast-1

# Expected JSON output with:
# - cluster_id: "node-fleet-cluster"
# - node_count: 1 (or current count)
# - last_scale_time: Unix timestamp
# - scaling_in_progress: false
# - metrics_history: Array of last 10 readings
```

### ✅ Test Manual Lambda Invocation

```bash
# Invoke Lambda manually
aws lambda invoke \
  --function-name node-fleet-cluster-autoscaler \
  --region ap-southeast-1 \
  --log-type Tail \
  /tmp/lambda-output.json

# Check response
cat /tmp/lambda-output.json | jq '.'
# Expected: {"statusCode": 200, "body": "..."}

# Check execution logs
aws logs tail /aws/lambda/node-fleet-cluster-autoscaler \
  --since 1m \
  --region ap-southeast-1
```

**✅ Checkpoint**: Lambda executing successfully, querying Prometheus, updating DynamoDB

---

## Phase 5: Scale-Up Test (30-40 minutes)

### ✅ Deploy Demo Application

```bash
# On your local machine:
cd demo-app

# Build and tag image
docker build -t node-fleet-demo .

# Get ECR repository URL from Pulumi
export ECR_REPO=$(pulumi stack output ecrRepositoryUrl -C ../pulumi)

# Login to ECR
aws ecr get-login-password --region ap-southeast-1 | \
  docker login --username AWS --password-stdin $ECR_REPO

# Tag and push
docker tag node-fleet-demo:latest $ECR_REPO:latest
docker push $ECR_REPO:latest

# Deploy to K3s
ssh -i ~/.ssh/node-fleet ubuntu@$MASTER_PUBLIC_IP << 'EOF'
# Update image in deployment
sed -i "s|<account-id>.dkr.ecr.*|$(aws sts get-caller-identity --query Account --output text).dkr.ecr.ap-southeast-1.amazonaws.com/node-fleet-demo:latest|" /tmp/k8s-deployment.yaml
sudo kubectl apply -f /tmp/k8s-deployment.yaml
EOF

# Verify deployment
ssh -i ~/.ssh/node-fleet ubuntu@$MASTER_PUBLIC_IP \
  "sudo kubectl get pods -l app=demo-app"
# Expected: 5 pods Running (may take 2-3 minutes)
```

### ✅ Generate Load to Trigger Scale-Up

```bash
# Option 1: Using stress pods
ssh -i ~/.ssh/node-fleet ubuntu@$MASTER_PUBLIC_IP << 'EOF'
sudo kubectl run cpu-stress-1 --image=polinux/stress --restart=Never -- stress --cpu 2 --timeout 600s
sudo kubectl run cpu-stress-2 --image=polinux/stress --restart=Never -- stress --cpu 2 --timeout 600s
sudo kubectl run cpu-stress-3 --image=polinux/stress --restart=Never -- stress --cpu 2 --timeout 600s
EOF

# Option 2: Using k6 load test (from local machine)
cd ../tests
npm install
k6 run load-test.js --vus 100 --duration 10m

# Monitor CPU in Prometheus (from master)
ssh -i ~/.ssh/node-fleet ubuntu@$MASTER_PUBLIC_IP \
  "curl -s 'http://localhost:30090/api/v1/query?query=avg(rate(node_cpu_seconds_total\{mode!=\"idle\"\}[5m]))*100' | jq '.data.result[0].value[1]'"

# Expected: CPU percentage > 70%
```

### ✅ Monitor Scale-Up Event

```bash
# 1. Watch Lambda logs for scale-up decision
aws logs tail /aws/lambda/node-fleet-cluster-autoscaler --follow --region ap-southeast-1

# Expected log output (within 2-4 minutes):
# [INFO] Metrics collected: {'cpu_usage': 78.5, 'pending_pods': 5, ...}
# [INFO] CPU usage 78.5% > threshold 70%
# [INFO] Scale-up decision: Adding 2 nodes
# [INFO] Calculating spot/on-demand mix: {'spot': 1, 'ondemand': 1}
# [INFO] Launching 1 spot instances
# [INFO] Launching 1 on-demand instances
# [INFO] Successfully launched 2 instances: ['i-abc123', 'i-def456']
# [INFO] Polling instance status...
# [INFO] All nodes Ready
# [INFO] Updated state: node_count=3

# 2. Watch EC2 instances launching
watch -n 5 "aws ec2 describe-instances \
  --filters 'Name=tag:Project,Values=node-fleet' 'Name=instance-state-name,Values=running,pending' \
  --query 'Reservations[].Instances[].[InstanceId,State.Name,PrivateIpAddress,InstanceLifecycle]' \
  --output table --region ap-southeast-1"

# 3. Watch nodes joining cluster
ssh -i ~/.ssh/node-fleet ubuntu@$MASTER_PUBLIC_IP \
  "watch -n 5 'sudo kubectl get nodes'"

# Expected progression:
# Minute 0: 1 node (master)
# Minute 2: 1 node (Lambda detects high CPU)
# Minute 3: 3 nodes (2 workers launching)
# Minute 5-7: 3 nodes (all Ready)
```

### ✅ Verify Scale-Up Success

```bash
# 1. Check final node count
ssh -i ~/.ssh/node-fleet ubuntu@$MASTER_PUBLIC_IP \
  "sudo kubectl get nodes -o wide"
# Expected: 3 nodes total (1 master + 2 workers), all Ready

# 2. Verify instance mix
aws ec2 describe-instances \
  --filters "Name=tag:Project,Values=node-fleet" "Name=instance-state-name,Values=running" \
  --query "Reservations[].Instances[].[InstanceId,InstanceType,InstanceLifecycle]" \
  --output table --region ap-southeast-1
# Expected: Mix of "spot" and null (on-demand)

# 3. Check DynamoDB state updated
aws dynamodb get-item \
  --table-name $STATE_TABLE \
  --key '{"cluster_id": {"S": "node-fleet-cluster"}}' \
  --region ap-southeast-1 | jq '.Item.node_count.N'
# Expected: "3"

# 4. Verify Slack notification (check your Slack channel)
# Expected: 🟢 Scale-Up message with details

# 5. Check CloudWatch metrics
aws cloudwatch get-metric-statistics \
  --namespace NodeFleet/Autoscaler \
  --metric-name ScaleUpEvents \
  --start-time $(date -u -d '10 minutes ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300 \
  --statistics Sum \
  --region ap-southeast-1
# Expected: Datapoints with Sum=1
```

**✅ Checkpoint**: Scale-up successful - 2 new workers joined cluster

---

## Phase 6: Scale-Down Test (30-40 minutes)

### ✅ Stop Load Generation

```bash
# Kill stress pods
ssh -i ~/.ssh/node-fleet ubuntu@$MASTER_PUBLIC_IP << 'EOF'
sudo kubectl delete pod cpu-stress-1 cpu-stress-2 cpu-stress-3 --ignore-not-found
EOF

# Stop k6 if running (Ctrl+C)

# Verify CPU drops
ssh -i ~/.ssh/node-fleet ubuntu@$MASTER_PUBLIC_IP \
  "curl -s 'http://localhost:30090/api/v1/query?query=avg(rate(node_cpu_seconds_total\{mode!=\"idle\"\}[5m]))*100' | jq '.data.result[0].value[1]'"
# Expected: CPU < 30% (may take 5-10 minutes)
```

### ✅ Wait for Scale-Down Cooldown

```bash
# Scale-down requires:
# 1. CPU < 30% for 10 consecutive minutes
# 2. No pending pods
# 3. 10-minute cooldown since last scale event

# Monitor time remaining
aws dynamodb get-item \
  --table-name $STATE_TABLE \
  --key '{"cluster_id": {"S": "node-fleet-cluster"}}' \
  --region ap-southeast-1 | jq '.Item.last_scale_time.N'

# Calculate time since last scale
LAST_SCALE=$(aws dynamodb get-item --table-name $STATE_TABLE --key '{"cluster_id": {"S": "node-fleet-cluster"}}' --region ap-southeast-1 | jq -r '.Item.last_scale_time.N')
NOW=$(date +%s)
ELAPSED=$((NOW - LAST_SCALE))
REMAINING=$((600 - ELAPSED))  # 600 seconds = 10 minutes
echo "Time until scale-down eligible: $REMAINING seconds"

# Wait if necessary
if [ $REMAINING -gt 0 ]; then
  echo "Waiting $REMAINING seconds for cooldown..."
  sleep $REMAINING
fi
```

### ✅ Monitor Scale-Down Event

```bash
# Watch Lambda logs
aws logs tail /aws/lambda/node-fleet-cluster-autoscaler --follow --region ap-southeast-1

# Expected log output (after cooldown):
# [INFO] Metrics collected: {'cpu_usage': 25.3, 'pending_pods': 0, ...}
# [INFO] CPU usage 25.3% < threshold 30% for 10+ minutes
# [INFO] Scale-down decision: Removing 1 node
# [INFO] Selected node for removal: ip-10-0-11-20 (fewest pods: 2)
# [INFO] Cordoning node: ip-10-0-11-20
# [INFO] Draining node: ip-10-0-11-20
# [INFO] Drain successful
# [INFO] Terminating instance: i-abc123
# [INFO] Deleted node from cluster
# [INFO] Updated state: node_count=2

# Watch nodes being drained
ssh -i ~/.ssh/node-fleet ubuntu@$MASTER_PUBLIC_IP \
  "watch -n 5 'sudo kubectl get nodes'"

# Expected progression:
# Minute 0-10: 3 nodes (waiting for cooldown)
# Minute 12: 1 node SchedulingDisabled (cordoned)
# Minute 13-15: Node draining (pods evicted)
# Minute 16: 2 nodes (terminated node removed)
```

### ✅ Verify Scale-Down Success

```bash
# 1. Check final node count
ssh -i ~/.ssh/node-fleet ubuntu@$MASTER_PUBLIC_IP \
  "sudo kubectl get nodes"
# Expected: 2 nodes (1 master + 1 worker)

# 2. Verify instance terminated
aws ec2 describe-instances \
  --filters "Name=tag:Project,Values=node-fleet" \
  --query "Reservations[].Instances[].[InstanceId,State.Name]" \
  --output table --region ap-southeast-1
# Expected: 2 running, 1 terminated

# 3. Check DynamoDB updated
aws dynamodb get-item \
  --table-name $STATE_TABLE \
  --key '{"cluster_id": {"S": "node-fleet-cluster"}}' \
  --region ap-southeast-1 | jq '.Item.node_count.N'
# Expected: "2"

# 4. Verify no pods disrupted
ssh -i ~/.ssh/node-fleet ubuntu@$MASTER_PUBLIC_IP \
  "sudo kubectl get pods --all-namespaces | grep -i error"
# Expected: No errors

# 5. Verify Slack notification
# Expected: 🔵 Scale-Down message
```

**✅ Checkpoint**: Scale-down successful - gracefully removed 1 worker

---

## Phase 7: Monitoring & Observability (20 minutes)

### ✅ Access Grafana Dashboards

```bash
# Setup port-forward (from local machine)
ssh -i ~/.ssh/node-fleet -L 3000:localhost:32000 ubuntu@$MASTER_PUBLIC_IP -N &

# Open browser to http://localhost:3000
# Login: admin / admin (change password on first login)

# Verify dashboards exist:
# 1. Cluster Overview - CPU/Memory graphs, node count
# 2. Autoscaler Performance - Scaling events, Lambda metrics
# 3. Cost Tracking - Hourly cost, monthly projection

# Take screenshots:
# - Cluster Overview showing scale-up spike
# - Autoscaler Performance showing scaling events
# - Cost dashboard showing savings
```

### ✅ Verify CloudWatch Alarms

```bash
# List all alarms
aws cloudwatch describe-alarms \
  --alarm-name-prefix "node-fleet-cluster" \
  --region ap-southeast-1 \
  --query "MetricAlarms[].{Name:AlarmName,State:StateValue}" \
  --output table

# Expected alarms:
# - scaling-failures (OK)
# - cpu-overload (OK)
# - at-max-capacity (OK)
# - node-join-failure (OK)
# - high-memory (OK)
# - lambda-timeout (OK)
# - lambda-error (OK)
# - pending-pods (OK)

# Test alarm (optional - triggers notification)
aws cloudwatch set-alarm-state \
  --alarm-name "node-fleet-cluster-high-memory" \
  --state-value ALARM \
  --state-reason "Manual test" \
  --region ap-southeast-1

# Check Slack for notification
# Reset alarm
aws cloudwatch set-alarm-state \
  --alarm-name "node-fleet-cluster-high-memory" \
  --state-value OK \
  --state-reason "Test complete" \
  --region ap-southeast-1
```

### ✅ Verify Slack Notifications

```bash
# Check Slack webhook configured
aws secretsmanager get-secret-value \
  --secret-id node-fleet/slack-webhook \
  --region ap-southeast-1 \
  --query SecretString --output text

# If not configured, add webhook:
aws secretsmanager create-secret \
  --name node-fleet/slack-webhook \
  --secret-string "https://hooks.slack.com/services/YOUR/WEBHOOK/URL" \
  --region ap-southeast-1

# Test notification by triggering Lambda
aws lambda invoke \
  --function-name node-fleet-cluster-autoscaler \
  --region ap-southeast-1 \
  /tmp/test.json

# Check Slack channel for message
```

**✅ Checkpoint**: All monitoring and alerting operational

---

## Phase 8: Performance & Cost Analysis (30 minutes)

### ✅ Gather Performance Metrics

```bash
# 1. Lambda execution metrics
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Duration \
  --dimensions Name=FunctionName,Value=node-fleet-cluster-autoscaler \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300 \
  --statistics Average,Maximum \
  --region ap-southeast-1

# Expected: Average ~10-20s, Maximum <60s

# 2. Scaling event count
aws cloudwatch get-metric-statistics \
  --namespace NodeFleet/Autoscaler \
  --metric-name ScaleUpEvents \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 3600 \
  --statistics Sum \
  --region ap-southeast-1

# 3. Node join latency
aws cloudwatch get-metric-statistics \
  --namespace NodeFleet/Autoscaler \
  --metric-name NodeJoinLatency \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 3600 \
  --statistics Average,Maximum \
  --region ap-southeast-1

# Expected: Average ~120-180 seconds (2-3 minutes)
```

### ✅ Calculate Actual Costs

```bash
# 1. Get actual instance hours
aws ce get-cost-and-usage \
  --time-period Start=$(date -u -d '1 day ago' +%Y-%m-%d),End=$(date -u +%Y-%m-%d) \
  --granularity DAILY \
  --metrics UnblendedCost \
  --group-by Type=SERVICE \
  --region us-east-1

# 2. Project monthly cost
# Formula: (Daily cost) × 30 days

# 3. Compare to baseline
# Before autoscaling: 5 nodes × 24h × 30 days × $0.0208/hour = ~$75/month
# After autoscaling:
#   - Master: 1 × 24h × 30 days × $0.0208/hour = $15/month
#   - Workers (avg 2-3): 2.5 × 24h × 30 days × $0.0208/hour = $37.50/month
#   - Spot discount (70%): -$26.25/month
#   - Lambda: $0.50/month
#   - DynamoDB: $1.50/month
#   - NAT Gateway: $70/month (unchangeable)
#   Total: ~$98/month

# 4. Calculate savings
# Baseline: $75 + $70 NAT = $145/month
# Optimized: $98/month
# Savings: $47/month (32%)
```

### ✅ Generate Cost Report

```bash
# Create cost summary
cat > /tmp/cost-analysis.txt << 'EOF'
# SmartScale Cost Analysis

## Before Autoscaling
- EC2 (5 × t3.small, always on): $75/month
- NAT Gateway (2 AZs): $70/month
- Total: $145/month

## After Autoscaling
- EC2 Master (1 × t3.small): $15/month
- EC2 Workers (avg 2.5 nodes): $37.50/month
- Spot discount (70% of workers): -$26.25/month
- Lambda (15,000 invocations): $0.50/month
- DynamoDB (on-demand): $1.50/month
- CloudWatch: $3/month
- Secrets Manager: $2/month
- NAT Gateway: $70/month
- Total: $103.25/month

## Savings
- Monthly: $41.75 (28.8%)
- Annual: $501/year

## Performance Gains
- Scaling time: 15-20 min → <3 min (85% faster)
- Service disruptions: 2-3/month → 0 (100% eliminated)
- Manual interventions: 10-15/month → 0 (fully automated)
EOF

cat /tmp/cost-analysis.txt
```

**✅ Checkpoint**: Performance validated, cost savings documented

---

## Phase 9: Documentation & Artifacts (30 minutes)

### ✅ Capture Screenshots

Required screenshots for documentation:

1. **Grafana Cluster Overview**

   - URL: http://localhost:3000
   - Dashboard: Cluster Overview
   - Show: CPU spike during scale-up, node count increase
   - File: `docs/screenshots/grafana-cluster-overview.png`

2. **Grafana Autoscaler Performance**

   - Dashboard: Autoscaler Performance
   - Show: Scaling events timeline, Lambda execution times
   - File: `docs/screenshots/grafana-autoscaler-performance.png`

3. **CloudWatch Lambda Logs**

   - Show: Scale-up decision and execution
   - File: `docs/screenshots/cloudwatch-lambda-logs.png`

4. **EC2 Console**

   - Show: 3 instances (1 master + 2 workers), mix of Spot/On-Demand
   - File: `docs/screenshots/ec2-instances.png`

5. **Slack Notifications**

   - Show: Scale-up and scale-down messages
   - File: `docs/screenshots/slack-notifications.png`

6. **kubectl get nodes**
   ```bash
   ssh -i ~/.ssh/node-fleet ubuntu@$MASTER_PUBLIC_IP \
     "sudo kubectl get nodes -o wide" > docs/screenshots/kubectl-nodes.txt
   ```

### ✅ Create Deployment Summary

```bash
cat > docs/DEPLOYMENT_SUMMARY.md << 'EOF'
# Deployment Summary

**Deployment Date**: $(date +%Y-%m-%d)
**AWS Region**: ap-southeast-1
**Cluster ID**: node-fleet-cluster

## Infrastructure Deployed

### AWS Resources
- **VPC**: 1 (10.0.0.0/16)
- **Subnets**: 4 (2 public, 2 private across 2 AZs)
- **NAT Gateways**: 2 (high availability)
- **EC2 Instances**: 3 (1 master + 2 workers)
- **Lambda Functions**: 2 (autoscaler + audit logger)
- **DynamoDB Tables**: 2 (state + metrics history)
- **CloudWatch Alarms**: 8
- **Security Groups**: 3

### K3s Cluster
- **Master Nodes**: 1 (t3.small)
- **Worker Nodes**: 2-10 (dynamic, 70% Spot)
- **Kubernetes Version**: 1.28
- **Pod Network**: 10.244.0.0/16
- **Service Network**: 10.43.0.0/16

### Monitoring Stack
- **Prometheus**: Running (7-day retention)
- **Grafana**: Running (3 dashboards)
- **kube-state-metrics**: Running
- **CloudWatch**: Custom metrics enabled

## Validation Results

### Scale-Up Test
- **Trigger**: CPU > 70% + Pending pods
- **Response Time**: <3 minutes
- **Nodes Added**: 2 (1 Spot, 1 On-Demand)
- **Pod Disruption**: None
- **Result**: ✅ PASS

### Scale-Down Test
- **Trigger**: CPU < 30% for 10+ minutes
- **Response Time**: After cooldown
- **Nodes Removed**: 1
- **Drain Time**: <5 minutes
- **Pod Disruption**: None
- **Result**: ✅ PASS

### Performance Metrics
- **Lambda Avg Execution**: 15-20 seconds
- **Node Join Latency**: 2-3 minutes
- **Scaling Accuracy**: 100%
- **False Triggers**: 0

## Cost Analysis

### Monthly Costs
- **Before**: $145/month
- **After**: $103/month
- **Savings**: $42/month (29%)
- **Annual Savings**: $504/year

### ROI
- **Development Time**: 80 hours
- **Payback Period**: <4 months
- **3-Year Savings**: $1,512

## Issues Encountered

1. **Issue**: [Describe any issues]
   **Resolution**: [How it was resolved]

## Next Steps

- [ ] Set up monitoring alerts to team email
- [ ] Configure backup/disaster recovery
- [ ] Implement predictive scaling (bonus)
- [ ] Add cost dashboard to Grafana
- [ ] Schedule quarterly cost reviews

## Sign-Off

Deployed by: [Your Name]
Date: $(date +%Y-%m-%d)
Status: Production Ready ✅
EOF
```

### ✅ Update README with Live Data

```bash
# Add live deployment info to README
cat >> README.md << 'EOF'

---

## 🎉 Live Deployment

**Status**: ✅ Production Deployed
**Deployment Date**: $(date +%Y-%m-%d)
**Region**: ap-southeast-1

### Access Points
- **Grafana**: http://[MASTER_IP]:32000 (admin/[password])
- **Prometheus**: http://[MASTER_IP]:30090
- **Demo App**: http://[MASTER_IP]:30080

### Metrics (Last 24 Hours)
- **Scaling Events**: [X] scale-ups, [Y] scale-downs
- **Average Node Count**: [Z] nodes
- **Lambda Invocations**: [N] executions
- **Cost Savings**: [X]% vs baseline

Last updated: $(date)
EOF
```

**✅ Checkpoint**: All documentation complete with live data

---

## Phase 10: Cleanup (Optional - Test Environment Only)

### ⚠️ CAUTION: Only for test/dev environments

```bash
# 1. Scale down to minimum
ssh -i ~/.ssh/node-fleet ubuntu@$MASTER_PUBLIC_IP << 'EOF'
sudo kubectl delete pods --all --all-namespaces --grace-period=30
EOF

# 2. Destroy Pulumi stack
cd pulumi
pulumi destroy --yes

# 3. Delete SSH key from AWS
aws ec2 delete-key-pair --key-name node-fleet --region ap-southeast-1

# 4. Verify all resources deleted
aws ec2 describe-instances \
  --filters "Name=tag:Project,Values=node-fleet" \
  --region ap-southeast-1

# Expected: All instances terminated

# 5. Clean up local files
rm -rf ~/.ssh/node-fleet*
```

---

## ✅ Final Verification Checklist

### Infrastructure

- [ ] VPC and subnets created
- [ ] NAT Gateways operational in both AZs
- [ ] Security groups configured correctly
- [ ] EC2 master instance running
- [ ] Lambda function deployed and triggered

### K3s Cluster

- [ ] Master node Ready
- [ ] Prometheus collecting metrics
- [ ] Grafana accessible
- [ ] kube-state-metrics running
- [ ] Demo app deployed

### Autoscaling

- [ ] Lambda executes every 2 minutes
- [ ] DynamoDB lock mechanism working
- [ ] Scale-up triggers correctly (CPU > 70%)
- [ ] Workers join cluster within 3 minutes
- [ ] Scale-down triggers correctly (CPU < 30%, 10min)
- [ ] Graceful drain successful (no pod disruptions)
- [ ] Spot/On-Demand mix maintained (70/30)

### Monitoring

- [ ] CloudWatch logs flowing
- [ ] Custom metrics published
- [ ] All 8 alarms configured
- [ ] Slack notifications working
- [ ] Grafana dashboards populated

### Performance

- [ ] Lambda execution < 60s
- [ ] Node join latency < 5min
- [ ] Scaling response time < 3min
- [ ] Zero service disruptions

### Cost

- [ ] Cost tracking implemented
- [ ] Savings calculated and documented
- [ ] Billing alerts configured

### Documentation

- [ ] Screenshots captured
- [ ] Deployment summary written
- [ ] Cost analysis completed
- [ ] README updated with live info

---

## 🎯 Success Criteria

Your deployment is **production-ready** when:

✅ All checklist items marked complete  
✅ Scale-up test passed  
✅ Scale-down test passed  
✅ Zero pod disruptions during scaling  
✅ Cost savings ≥ 25%  
✅ All monitoring operational  
✅ Documentation complete

**Congratulations!** 🎉 You've successfully deployed a production-grade autoscaling system.

---

## 📞 Support

**Issues**: Create GitHub issue with logs and error messages  
**Questions**: Check docs/ folder or README  
**Emergency**: SSH to master, check CloudWatch logs

**Common Commands**:

```bash
# Check cluster health
ssh -i ~/.ssh/node-fleet ubuntu@$MASTER_PUBLIC_IP "sudo kubectl get nodes,pods --all-namespaces"

# View Lambda logs
aws logs tail /aws/lambda/node-fleet-cluster-autoscaler --follow

# Check DynamoDB state
aws dynamodb get-item --table-name node-fleet-cluster-state --key '{"cluster_id": {"S": "node-fleet-cluster"}}'

# Manual scaling (emergency)
# Scale up: Launch EC2 from worker template
# Scale down: kubectl drain + terminate instance
```

---

_Document Version: 1.0_  
_Last Updated: $(date)_
