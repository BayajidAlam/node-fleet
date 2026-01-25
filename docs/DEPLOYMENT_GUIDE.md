# SmartScale K3s Autoscaler - Deployment Guide

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Environment Setup](#environment-setup)
3. [Infrastructure Deployment](#infrastructure-deployment)
4. [K3s Cluster Setup](#k3s-cluster-setup)
5. [Verification Steps](#verification-steps)
6. [Post-Deployment Configuration](#post-deployment-configuration)
7. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### Required Tools

| Tool | Version | Installation Command |
|------|---------|---------------------|
| **AWS CLI** | 2.x | `curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip" && unzip awscliv2.zip && sudo ./aws/install` |
| **Pulumi CLI** | 3.x | `curl -fsSL https://get.pulumi.com \| sh` |
| **Node.js** | 18+ | `curl -fsSL https://deb.nodesource.com/setup_18.x \| sudo -E bash - && sudo apt install -y nodejs` |
| **kubectl** | 1.28+ | `curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" && chmod +x kubectl && sudo mv kubectl /usr/local/bin/` |
| **Python** | 3.11+ | `sudo apt install python3.11 python3.11-venv` |
| **jq** | 1.6+ | `sudo apt install jq` |

### AWS Account Requirements

1. **IAM Permissions**:
   - EC2: Full access (RunInstances, TerminateInstances, DescribeInstances, CreateTags)
   - VPC: Full access (CreateVpc, CreateSubnet, CreateSecurityGroup, etc.)
   - Lambda: Full access (CreateFunction, UpdateFunctionCode, etc.)
   - DynamoDB: Full access (CreateTable, PutItem, GetItem, UpdateItem)
   - Secrets Manager: Full access (CreateSecret, GetSecretValue)
   - SNS: Publish, CreateTopic
   - CloudWatch: PutMetricData, CreateLogGroup
   - IAM: CreateRole, AttachRolePolicy

2. **Service Quotas**:
   - EC2 vCPU limit: At least 20 vCPUs for t3 instances
   - VPC limit: 1 VPC available
   - Elastic IPs: 2 available (for NAT Gateways)

3. **AWS CLI Configuration**:
```bash
aws configure
# AWS Access Key ID: <your-access-key>
# AWS Secret Access Key: <your-secret-key>
# Default region name: ap-southeast-1
# Default output format: json
```

### Local Machine Setup

**Recommended Specs**:
- OS: Ubuntu 20.04+ or macOS 12+
- RAM: 4GB minimum
- Disk: 10GB free space

---

## Environment Setup

### 1. Clone Repository

```bash
git clone https://github.com/BayajidAlam/node-fleet.git
cd node-fleet
```

### 2. Install Dependencies

#### Pulumi Dependencies

```bash
cd pulumi
npm install
cd ..
```

#### Lambda Dependencies (for local testing)

```bash
cd lambda
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd ..
```

### 3. Configure Pulumi Stack

```bash
cd pulumi

# Login to Pulumi (choose local or cloud backend)
pulumi login --local  # OR pulumi login (for cloud backend)

# Initialize stack
pulumi stack init dev

# Set AWS region
pulumi config set aws:region ap-southeast-1

# Set cluster configuration
pulumi config set node-fleet:clusterName node-fleet-cluster
pulumi config set node-fleet:minNodes 2
pulumi config set node-fleet:maxNodes 10
pulumi config set node-fleet:spotPercentage 70

# Set SSH key name (create one in AWS Console first)
pulumi config set node-fleet:sshKeyName node-fleet-key

# Review configuration
pulumi config
```

---

## Infrastructure Deployment

### Step 1: Preview Infrastructure Changes

```bash
cd pulumi
pulumi preview
```

**Expected Output**:
```
Previewing update (dev)

     Type                              Name                           Plan
 +   pulumi:pulumi:Stack               node-fleet-dev                 create
 +   ├─ aws:ec2:Vpc                    main-vpc                       create
 +   ├─ aws:ec2:InternetGateway        internet-gateway               create
 +   ├─ aws:ec2:Subnet                 public-subnet-1a               create
 +   ├─ aws:ec2:Subnet                 public-subnet-1b               create
 +   ├─ aws:ec2:Subnet                 private-subnet-1a              create
 +   ├─ aws:ec2:Subnet                 private-subnet-1b              create
 +   ├─ aws:ec2:NatGateway             nat-gateway-1a                 create
 +   ├─ aws:ec2:NatGateway             nat-gateway-1b                 create
 +   ├─ aws:dynamodb:Table             state-table                    create
 +   ├─ aws:dynamodb:Table             metrics-history-table          create
 +   ├─ aws:secretsmanager:Secret      k3s-token                      create
 +   ├─ aws:secretsmanager:Secret      slack-webhook                  create
 +   ├─ aws:sns:Topic                  autoscaler-notifications       create
 +   ├─ aws:iam:Role                   lambda-role                    create
 +   ├─ aws:iam:Role                   master-instance-role           create
 +   ├─ aws:iam:Role                   worker-instance-role           create
 +   ├─ aws:ec2:SecurityGroup          master-sg                      create
 +   ├─ aws:ec2:SecurityGroup          worker-sg                      create
 +   ├─ aws:ec2:SecurityGroup          lambda-sg                      create
 +   ├─ aws:ec2:LaunchTemplate         master-launch-template         create
 +   ├─ aws:ec2:LaunchTemplate         worker-launch-template         create
 +   ├─ aws:ec2:Instance               k3s-master                     create
 +   ├─ aws:lambda:Function            autoscaler                     create
 +   └─ aws:cloudwatch:EventRule       autoscaler-schedule            create

Resources:
    + 82 to create
```

### Step 2: Deploy Infrastructure

```bash
pulumi up --yes
```

**Deployment Time**: 10-15 minutes

**Critical Outputs** (save these):

```bash
# After deployment, save outputs
pulumi stack output masterPublicIpAddress > ../master-ip.txt
pulumi stack output masterPrivateIpAddress > ../master-private-ip.txt
pulumi stack output vpcId > ../vpc-id.txt
pulumi stack output lambdaFunctionName > ../lambda-function-name.txt

# View all outputs
pulumi stack output --json | jq '.'
```

### Step 3: Configure Secrets

#### Set K3s Join Token (after master setup)

```bash
# This will be done after master is initialized
# See "K3s Cluster Setup" section below
```

#### Set Slack Webhook URL

```bash
aws secretsmanager update-secret \
  --secret-id node-fleet/slack-webhook \
  --secret-string "https://hooks.slack.com/services/YOUR/WEBHOOK/URL" \
  --region ap-southeast-1
```

---

## K3s Cluster Setup

### Step 1: Connect to Master Node

```bash
export MASTER_IP=$(cat master-ip.txt)
ssh -i ~/.ssh/node-fleet-key.pem ubuntu@$MASTER_IP
```

### Step 2: Initialize K3s Master

The master setup script is automatically executed via UserData during instance launch. Verify it completed successfully:

```bash
# Check K3s installation
sudo systemctl status k3s

# Expected output: active (running)

# Verify node is Ready
sudo kubectl get nodes

# Expected output:
# NAME           STATUS   ROLES                  AGE   VERSION
# ip-10-0-11-x   Ready    control-plane,master   5m    v1.28.x+k3s1
```

If K3s is not installed, manually run:

```bash
curl -sfL https://get.k3s.io | sh -s - server \
  --disable traefik \
  --write-kubeconfig-mode 644 \
  --node-name master
```

### Step 3: Store K3s Token in Secrets Manager

```bash
# On master node, get K3s join token
K3S_TOKEN=$(sudo cat /var/lib/rancher/k3s/server/node-token)

# On local machine, update Secrets Manager
aws secretsmanager update-secret \
  --secret-id node-fleet/k3s-token \
  --secret-string "$K3S_TOKEN" \
  --region ap-southeast-1
```

### Step 4: Deploy Prometheus

```bash
# On master node
cd /home/ubuntu/k3s

# Apply Prometheus manifests
sudo kubectl apply -f prometheus-namespace.yaml
sudo kubectl apply -f prometheus-configmap.yaml
sudo kubectl apply -f prometheus-deployment.yaml
sudo kubectl apply -f prometheus-service.yaml

# Wait for Prometheus to be ready
sudo kubectl wait --for=condition=ready pod -l app=prometheus -n monitoring --timeout=300s

# Verify Prometheus is accessible
curl http://localhost:30090/api/v1/query?query=up
```

### Step 5: Deploy Grafana

```bash
# On master node
sudo kubectl apply -f grafana-deployment.yaml
sudo kubectl apply -f grafana-service.yaml

# Wait for Grafana to be ready
sudo kubectl wait --for=condition=ready pod -l app=grafana -n monitoring --timeout=300s

# Get Grafana admin password (default: admin123)
echo "Grafana URL: http://$MASTER_IP:30030"
echo "Username: admin"
echo "Password: admin123"
```

### Step 6: Configure kubeconfig (Local Machine)

```bash
# On master node, copy kubeconfig
sudo cat /etc/rancher/k3s/k3s.yaml > ~/kubeconfig.yaml

# On local machine, download kubeconfig
scp -i ~/.ssh/node-fleet-key.pem ubuntu@$MASTER_IP:~/kubeconfig.yaml ./kubeconfig.yaml

# Update server IP to master's public IP
sed -i "s/127.0.0.1/$MASTER_IP/g" kubeconfig.yaml

# Set KUBECONFIG environment variable
export KUBECONFIG=$(pwd)/kubeconfig.yaml

# Test connection
kubectl get nodes
```

### Step 7: Launch Initial Worker Nodes

The autoscaler will maintain MIN_NODES (2) workers automatically. To manually launch initial workers:

```bash
# On local machine
LAUNCH_TEMPLATE_ID=$(pulumi stack output workerLaunchTemplateId)
SUBNET_1A=$(pulumi stack output privateSubnet1aId)
SUBNET_1B=$(pulumi stack output privateSubnet1bId)

# Launch worker in AZ-1a
aws ec2 run-instances \
  --launch-template LaunchTemplateId=$LAUNCH_TEMPLATE_ID \
  --subnet-id $SUBNET_1A \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Project,Value=node-fleet},{Key=Role,Value=k3s-worker}]' \
  --region ap-southeast-1

# Launch worker in AZ-1b
aws ec2 run-instances \
  --launch-template LaunchTemplateId=$LAUNCH_TEMPLATE_ID \
  --subnet-id $SUBNET_1B \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Project,Value=node-fleet},{Key=Role,Value=k3s-worker}]' \
  --region ap-southeast-1

# Wait 3-5 minutes for workers to join
watch -n 10 "kubectl get nodes"
```

---

## Verification Steps

### 1. Verify Infrastructure

```bash
# Check VPC
aws ec2 describe-vpcs --filters "Name=tag:Name,Values=node-fleet-vpc" --region ap-southeast-1

# Check DynamoDB tables
aws dynamodb list-tables --region ap-southeast-1 | grep node-fleet

# Check Lambda function
aws lambda get-function --function-name $(cat lambda-function-name.txt) --region ap-southeast-1

# Check EventBridge rule
aws events describe-rule --name node-fleet-dev-autoscaler-schedule --region ap-southeast-1
```

### 2. Verify K3s Cluster

```bash
# Get all nodes
kubectl get nodes

# Expected output: 1 master + 2 workers in Ready state

# Get all pods
kubectl get pods --all-namespaces

# Check Prometheus
kubectl get pods -n monitoring -l app=prometheus

# Check Grafana
kubectl get pods -n monitoring -l app=grafana
```

### 3. Verify Autoscaler Functionality

```bash
# Check Lambda logs
aws logs tail /aws/lambda/node-fleet-dev-autoscaler --follow --region ap-southeast-1

# Expected output (every 2 minutes):
# [INFO] Autoscaler invoked
# [INFO] Metrics: CPU=35%, Memory=40%, Pending=0
# [INFO] Decision: no_action (within limits)
```

### 4. Verify Prometheus Metrics

```bash
curl "http://$MASTER_IP:30090/api/v1/query?query=up" | jq '.'

# Expected: All targets up
```

### 5. Verify CloudWatch Metrics

```bash
# List custom metrics
aws cloudwatch list-metrics --namespace SmartScale --region ap-southeast-1

# Expected metrics:
# - AutoscalerInvocations
# - ClusterCPUUtilization
# - ClusterMemoryUtilization
# - PendingPods
# - CurrentNodeCount
```

### 6. Verify Slack Notifications

Trigger a test notification:

```bash
# Manually invoke Lambda with test event
aws lambda invoke \
  --function-name $(cat lambda-function-name.txt) \
  --payload '{}' \
  --region ap-southeast-1 \
  response.json

# Check Slack channel for notification
```

---

## Post-Deployment Configuration

### 1. Import Grafana Dashboards

```bash
# Access Grafana at http://<master-ip>:30030
# Login: admin/admin123

# Import pre-built dashboards from monitoring/grafana-dashboards/
# Dashboard 1: Cluster Overview (cluster-overview.json)
# Dashboard 2: Autoscaler Performance (autoscaler-performance.json)
# Dashboard 3: Cost Tracking (cost-tracking.json)
```

### 2. Configure CloudWatch Alarms

```bash
# Create CPU threshold alarm
aws cloudwatch put-metric-alarm \
  --alarm-name node-fleet-cpu-critical \
  --alarm-description "Cluster CPU > 90% for 5 minutes" \
  --metric-name ClusterCPUUtilization \
  --namespace SmartScale \
  --statistic Average \
  --period 300 \
  --threshold 90 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 1 \
  --region ap-southeast-1

# Create scaling failure alarm
aws cloudwatch put-metric-alarm \
  --alarm-name node-fleet-scaling-failures \
  --alarm-description "3+ scaling failures in 15 minutes" \
  --metric-name ScalingFailures \
  --namespace SmartScale \
  --statistic Sum \
  --period 900 \
  --threshold 3 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 1 \
  --region ap-southeast-1
```

### 3. Deploy Demo Application

```bash
cd demo-app

# Build and push to ECR (if using ECR)
aws ecr get-login-password --region ap-southeast-1 | docker login --username AWS --password-stdin <account-id>.dkr.ecr.ap-southeast-1.amazonaws.com
docker build -t node-fleet-demo .
docker tag node-fleet-demo:latest <account-id>.dkr.ecr.ap-southeast-1.amazonaws.com/node-fleet-demo:latest
docker push <account-id>.dkr.ecr.ap-southeast-1.amazonaws.com/node-fleet-demo:latest

# Deploy to K3s
kubectl apply -f k8s-deployment.yaml

# Access demo app
echo "Demo App URL: http://$MASTER_IP:30080"
```

---

## Troubleshooting

### Common Issues

#### Issue 1: Workers Not Joining Cluster

**Symptoms**: EC2 instances launched but not showing in `kubectl get nodes`

**Diagnosis**:

```bash
# SSH to worker node
ssh -i ~/.ssh/node-fleet-key.pem ubuntu@<worker-ip>

# Check K3s agent logs
sudo journalctl -u k3s-agent -f

# Common errors:
# - "Unable to connect to master:6443" → Security group issue
# - "Invalid token" → K3s token mismatch in Secrets Manager
# - "Connection refused" → Master node not ready
```

**Solution**:

```bash
# Fix security group (allow 6443/tcp from workers to master)
WORKER_SG=$(pulumi stack output workerSecurityGroupId)
MASTER_SG=$(pulumi stack output masterSecurityGroupId)

aws ec2 authorize-security-group-ingress \
  --group-id $MASTER_SG \
  --protocol tcp \
  --port 6443 \
  --source-group $WORKER_SG \
  --region ap-southeast-1

# Manually join worker
K3S_TOKEN=$(aws secretsmanager get-secret-value --secret-id node-fleet/k3s-token --region ap-southeast-1 --query SecretString --output text)
MASTER_PRIVATE_IP=$(cat master-private-ip.txt)

curl -sfL https://get.k3s.io | K3S_URL=https://$MASTER_PRIVATE_IP:6443 K3S_TOKEN=$K3S_TOKEN sh -
```

#### Issue 2: Lambda Cannot Query Prometheus

**Symptoms**: Lambda logs show "ConnectionError: Failed to connect to Prometheus"

**Diagnosis**:

```bash
# Check Lambda VPC configuration
aws lambda get-function-configuration --function-name $(cat lambda-function-name.txt) --region ap-southeast-1 | jq '.VpcConfig'

# Expected: VpcId and SubnetIds should match private subnets
```

**Solution**:

```bash
# Update Lambda security group to allow outbound to master:30090
LAMBDA_SG=$(pulumi stack output lambdaSecurityGroupId)

aws ec2 authorize-security-group-egress \
  --group-id $LAMBDA_SG \
  --protocol tcp \
  --port 30090 \
  --cidr 10.0.0.0/16 \
  --region ap-southeast-1

# Update master security group to allow inbound from Lambda
aws ec2 authorize-security-group-ingress \
  --group-id $MASTER_SG \
  --protocol tcp \
  --port 30090 \
  --source-group $LAMBDA_SG \
  --region ap-southeast-1
```

#### Issue 3: DynamoDB Lock Stuck

**Symptoms**: Lambda always logs "Scaling already in progress"

**Diagnosis**:

```bash
# Check DynamoDB state
aws dynamodb get-item \
  --table-name node-fleet-dev-state \
  --key '{"cluster_id": {"S": "node-fleet-cluster"}}' \
  --region ap-southeast-1 | jq '.Item'
```

**Solution**:

```bash
# Force release lock (emergency only)
aws dynamodb update-item \
  --table-name node-fleet-dev-state \
  --key '{"cluster_id": {"S": "node-fleet-cluster"}}' \
  --update-expression "SET scaling_in_progress = :false REMOVE lock_acquired_at" \
  --expression-attribute-values '{":false": {"BOOL": false}}' \
  --region ap-southeast-1
```

---

## Rollback Procedure

If deployment fails or needs rollback:

```bash
# Destroy all infrastructure
cd pulumi
pulumi destroy --yes

# This will:
# - Terminate all EC2 instances
# - Delete Lambda function
# - Delete DynamoDB tables
# - Delete VPC and all networking resources
# - Delete IAM roles (after detaching policies)

# Note: Secrets Manager secrets have 7-30 day recovery window
# To immediately delete:
aws secretsmanager delete-secret \
  --secret-id node-fleet/k3s-token \
  --force-delete-without-recovery \
  --region ap-southeast-1

aws secretsmanager delete-secret \
  --secret-id node-fleet/slack-webhook \
  --force-delete-without-recovery \
  --region ap-southeast-1
```

---

## Next Steps

1. **Load Testing**: Run `k6 run tests/load-test.js` to trigger autoscaling
2. **Monitoring Setup**: Import Grafana dashboards from `monitoring/grafana-dashboards/`
3. **Cost Tracking**: Set up AWS Cost Explorer tags to track node-fleet costs
4. **Production Hardening**: See [SECURITY_CHECKLIST.md](SECURITY_CHECKLIST.md)

---

_For detailed troubleshooting, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md)._
