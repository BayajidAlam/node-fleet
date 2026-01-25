# SmartScale K3s Autoscaler

<div align="center">

**Intelligent, Cost-Optimized Kubernetes Autoscaling for AWS**

[![AWS](https://img.shields.io/badge/AWS-EC2%20%7C%20Lambda%20%7C%20DynamoDB-orange)](https://aws.amazon.com)
[![K3s](https://img.shields.io/badge/K3s-v1.28-blue)](https://k3s.io)
[![Python](https://img.shields.io/badge/Python-3.11-blue)](https://python.org)
[![Pulumi](https://img.shields.io/badge/IaC-Pulumi%20TypeScript-purple)](https://pulumi.com)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

_Reduce infrastructure costs by 40-50% through intelligent, event-driven autoscaling_

[Features](#-features) • [Architecture](#-architecture) • [Quick Start](#-quick-start) • [Documentation](#-documentation) • [Demo](#-demo)

</div>

---

## 📋 Project Overview

### Business Problem

**Client**: TechFlow Solutions - E-commerce platform (Dhaka, Bangladesh)

**Current Pain Points**:

- 💸 **1.2 lakh BDT/month** infrastructure waste (5 nodes running 24/7, 60% idle during off-peak)
- 🔥 **Flash sale crashes** - manual scaling takes 15-20 minutes, losing 8 lakh BDT in revenue
- 👨‍💻 **Manual intervention** required for every traffic spike
- 📊 **No visibility** into actual resource utilization and scaling needs

### Our Solution

SmartScale is an **intelligent, serverless autoscaler** for K3s clusters that:

- ✅ **Scales automatically** in under 3 minutes (vs. 15-20 min manual)
- ✅ **Reduces costs by 40-50%** through dynamic right-sizing
- ✅ **Prevents outages** with predictive scaling and multi-AZ distribution
- ✅ **100% hands-off** - no human intervention required
- ✅ **Full observability** with Prometheus, Grafana, and Slack alerts

### Success Metrics

| Metric                   | Before      | After      | Improvement         |
| ------------------------ | ----------- | ---------- | ------------------- |
| **Monthly Cost**         | 1.2L BDT    | 60-70K BDT | **40-50% savings**  |
| **Scaling Time**         | 15-20 min   | <3 min     | **83% faster**      |
| **Service Disruptions**  | 2-3/month   | 0          | **100% eliminated** |
| **Manual Interventions** | 10-15/month | 0          | **Fully automated** |

---

## 🎯 Features

### Core Capabilities

#### 🤖 Intelligent Scaling Logic

- **Multi-metric decision making**: CPU, memory, pending pods, network I/O, disk I/O
- **Cooldown periods**: 5min after scale-up, 10min after scale-down
- **Min/max constraints**: 2-10 nodes with safety guardrails
- **Graceful drain**: Zero-downtime node removal respecting PodDisruptionBudgets

#### 📊 Real-Time Monitoring

- **Prometheus metrics**: 15-second granularity for all cluster resources
- **Custom CloudWatch metrics**: Autoscaler performance, scaling events, costs
- **Grafana dashboards**: 3 pre-built dashboards for cluster, autoscaler, and cost tracking
- **Slack notifications**: Emoji-formatted alerts for all scaling events

#### 🔒 Production-Grade Security

- **No hardcoded secrets**: All credentials in AWS Secrets Manager
- **Least-privilege IAM**: Separate roles for Lambda, EC2, and master/worker nodes
- **Encrypted storage**: EBS volumes, DynamoDB, and Secrets Manager all encrypted
- **VPC isolation**: Private subnets with NAT Gateway, security group whitelisting

#### 🔄 State Management

- **Distributed locking**: DynamoDB conditional writes prevent race conditions
- **Auto-recovery**: Expired locks (5min) auto-release, stuck operations detected
- **Audit trail**: DynamoDB Streams log all state changes to CloudWatch

### 🌟 Bonus Features (All Implemented!)

#### 1. **Multi-AZ High Availability**

- Workers distributed across 2 availability zones (ap-southeast-1a, ap-southeast-1b)
- Smart AZ-aware node selection maintains balanced distribution
- Minimum 1 node per AZ ensures zone-level fault tolerance

#### 2. **Spot Instance Integration**

- **70% Spot / 30% On-Demand** mix for maximum cost savings (60-70% off)
- Automatic interruption handling with 2-minute warning EventBridge integration
- Fallback to On-Demand when Spot capacity unavailable
- Instance type diversification (t3.small, t3a.small) for higher availability

#### 3. **Predictive Scaling**

- Analyzes 7-day historical CPU/traffic patterns in DynamoDB
- Pre-scales 10 minutes before known high-traffic periods:
  - Daily 9 AM business hours surge
  - Friday 8 PM flash sales
  - Holiday shopping peaks
- Time-series forecasting reduces customer-facing latency spikes

#### 4. **Custom Application Metrics**

- Application queue depth monitoring (in-app task queues)
- API latency tracking (p95, p99 response times)
- Error rate monitoring (5xx responses)
- Application-aware scaling decisions beyond CPU/memory

#### 5. **GitOps with FluxCD**

- All Kubernetes manifests version-controlled in Git
- Auto-sync every 1 minute from repository
- Rollback capability via `git revert`
- Complete audit trail of configuration changes

#### 6. **Real-Time Cost Dashboard**

- Live hourly cost calculation based on instance types
- Monthly cost projection and savings percentage
- Spot vs On-Demand cost breakdown
- Budget alerts and recommendations

---

## 🏗️ Architecture

### High-Level System Design

![System Architecture](docs/diagrams/system_architecture.png)

### Data Flow

1. **EventBridge** triggers Lambda every 2 minutes
2. **Lambda** queries **Prometheus** for CPU, memory, pending pods
3. **Lambda** checks **DynamoDB** for current state and acquires lock
4. **Scaling Decision** engine evaluates metrics vs thresholds
5. If scale-up: **Lambda** launches EC2 instances from Launch Template
6. If scale-down: **Lambda** drains node (`kubectl drain`) then terminates
7. **Lambda** updates **DynamoDB** state and releases lock
8. **SNS** → **Slack** notification sent with scaling details

### Technology Stack

| Component         | Technology                      | Justification                            |
| ----------------- | ------------------------------- | ---------------------------------------- |
| **Orchestration** | K3s (Lightweight Kubernetes)    | 50% lower resource usage vs standard K8s |
| **Autoscaler**    | AWS Lambda (Python 3.11)        | Serverless, $0.50/month vs $7/month EC2  |
| **Metrics**       | Prometheus + kube-state-metrics | Free, K8s-native, powerful PromQL        |
| **Visualization** | Grafana                         | Best-in-class dashboards, free           |
| **State Store**   | DynamoDB (On-Demand)            | Distributed locking, $1-2/month          |
| **Secrets**       | AWS Secrets Manager             | Encrypted, rotatable, $2/month           |
| **IaC**           | Pulumi (TypeScript)             | Type safety, real programming language   |
| **Load Testing**  | k6                              | Modern, scriptable, high performance     |
| **GitOps**        | FluxCD                          | Industry standard, auto-sync from Git    |

---

## 🚀 Quick Start

### Prerequisites

- AWS account with appropriate permissions
- AWS CLI configured (`aws configure`)
- Pulumi CLI installed ([install guide](https://www.pulumi.com/docs/get-started/install/))
- kubectl installed ([install guide](https://kubernetes.io/docs/tasks/tools/))
- Node.js 18+ and npm (for Pulumi)
- Python 3.11+ (for Lambda development)

### Installation (30 minutes)

#### Step 1: Clone Repository

```bash
git clone https://github.com/BayajidAlam/node-fleet.git
cd node-fleet
```

#### Step 2: Configure Pulumi

```bash
cd pulumi
npm install

# Initialize Pulumi stack
pulumi stack init dev

# Configure AWS region
pulumi config set aws:region ap-southeast-1

# Set cluster configuration
pulumi config set node-fleet:clusterName node-fleet-cluster
pulumi config set node-fleet:minNodes 2
pulumi config set node-fleet:maxNodes 10
```

#### Step 3: Deploy Infrastructure

```bash
# Preview changes
pulumi preview

# Deploy all AWS resources (VPC, EC2, Lambda, DynamoDB, etc.)
pulumi up --yes

# Save outputs for later use
pulumi stack output masterPublicIpAddress > ../master-ip.txt
pulumi stack output masterPrivateIpAddress > ../master-private-ip.txt
```

**Expected deployment time**: 10-15 minutes

#### Step 4: Setup K3s Master

```bash
# SSH to master node
export MASTER_IP=$(cat master-ip.txt)
ssh -i ~/.ssh/node-fleet.pem ubuntu@$MASTER_IP

# Run master setup script
sudo bash /tmp/master-setup.sh

# Verify K3s is running
sudo kubectl get nodes
# Expected output: 1 node in Ready state
```

#### Step 5: Verify Prometheus

```bash
# Test Prometheus API
curl "http://localhost:30090/api/v1/query?query=up"

# Access Grafana (from local machine)
kubectl port-forward -n monitoring svc/grafana 3000:80

# Open http://localhost:3000 (admin/admin)
```

#### Step 6: Test Autoscaler

```bash
# Check Lambda logs
aws logs tail /aws/lambda/node-fleet-cluster-autoscaler --follow

# Trigger scale-up by creating high CPU load
cd tests
./test-scale-up.sh

# Watch nodes scale up
watch -n 5 "kubectl get nodes"

# Expected: New worker nodes appear within 3 minutes
```

#### Step 7: Deploy Demo Application

```bash
# Build and push demo app to ECR
cd demo-app
aws ecr get-login-password --region ap-southeast-1 | docker login --username AWS --password-stdin <account-id>.dkr.ecr.ap-southeast-1.amazonaws.com
docker build -t node-fleet-demo .
docker tag node-fleet-demo:latest <account-id>.dkr.ecr.ap-southeast-1.amazonaws.com/node-fleet-demo:latest
docker push <account-id>.dkr.ecr.ap-southeast-1.amazonaws.com/node-fleet-demo:latest

# Deploy to K3s
kubectl apply -f k8s-deployment.yaml

# Access demo app
echo "http://$(cat ../master-ip.txt):30080"
```

### Quick Verification Checklist

- [ ] K3s master node running and healthy (`kubectl get nodes`)
- [ ] Prometheus collecting metrics (`curl http://master-ip:30090/api/v1/query?query=up`)
- [ ] Lambda function executing (`aws logs tail /aws/lambda/...`)
- [ ] DynamoDB state table exists (`aws dynamodb describe-table --table-name node-fleet-cluster-state`)
- [ ] Grafana dashboards accessible (`kubectl port-forward svc/grafana 3000:80`)
- [ ] Demo app responding (`curl http://master-ip:30080/health`)

---

## 📖 Documentation

### Core Documentation

- [**Requirements Specification**](docs/REQUIREMENTS.md) - Complete project requirements
- [**Solution Architecture**](docs/SOLUTION_ARCHITECTURE.md) - Detailed architecture and design decisions
- [**Implementation Plan**](docs/COMPLETE_IMPLEMENTATION_PLAN.md) - Step-by-step implementation guide
- [**Technology Stack**](docs/TECHNOLOGY_STACK.md) - Technology choices and justifications

### Operational Guides

- [**Deployment Scripts**](scripts/) - Automation scripts for cluster/monitoring deployment
- [**GitOps Directory**](gitops/) - FluxCD manifests and management scripts
- [**Testing Guide**](docs/TESTING_GUIDE.md) - Unit, integration, and load testing procedures
- [**Implementation Checklist**](docs/IMPLEMENTATION_CHECKLIST.md) - Progress tracking

### Advanced Features

- [**Bonus Features Guide**](docs/BONUS_FEATURES_GUIDE.md) - Multi-AZ, Spot instances, predictive scaling
- [**Cost Exporter**](monitoring/cost_exporter.py) - Real-time AWS cost tracking (434 lines)
- [**Grafana Dashboards**](monitoring/grafana-dashboards/) - 3 pre-built dashboards (cluster, autoscaler, cost)
- [**Verification Tools**](scripts/verify-autoscaler-requirements.sh) - Comprehensive compliance checker

### API Reference

- [**Pulumi Code**](pulumi/src/) - Infrastructure as Code modules
- [**Lambda Functions**](lambda/) - Python autoscaler logic (15 modules, 3,158 lines)
- [**K3s Setup Scripts**](k3s/) - Master and worker initialization
- [**Monitoring Stack**](monitoring/) - Prometheus, Grafana, exporters

---

## 🧪 Testing

### Unit Tests

```bash
cd lambda
pip install -r requirements.txt
pip install pytest pytest-cov moto

# Run all unit tests with coverage
pytest tests/ -v --cov=. --cov-report=html

# Expected: 85%+ code coverage
```

### Integration Tests

```bash
cd tests
npm install

# Run Pulumi infrastructure tests
npm test

# Run Lambda integration tests
pytest lambda/test_autoscaler_integration.py -v
```

### Load Testing (k6)

```bash
cd tests
k6 run load-test.js --vus 100 --duration 20m

# Expected behavior:
# - At 100+ VUs: CPU > 70%, autoscaler scales up within 3 min
# - After load drops: CPU < 30%, scale-down after 10+ min
```

### Manual Scaling Tests

```bash
# Test scale-up
cd tests
./test-scale-up.sh
# Watch: kubectl get nodes -w

# Test scale-down
./test-scale-down.sh
# Expected: 1 node drained and terminated after 10 min
```

---

## 📊 Monitoring & Alerting

### Grafana Dashboards

Access Grafana at `http://<master-ip>:32000` (default: admin/admin)

**Dashboard 1: Cluster Overview**

- Current node count
- CPU/Memory utilization (24h view)
- Network and Disk I/O trends
- Pending pods counter
- Scaling events timeline

**Dashboard 2: Autoscaler Performance**

- Lambda execution duration
- Scaling decisions breakdown (up/down/no-action)
- Node join latency histogram
- Cost savings estimate

**Dashboard 3: Cost Tracking** (BONUS)

- Real-time hourly cost
- Monthly cost projection
- Spot vs On-Demand breakdown
- Budget alerts

### CloudWatch Alarms

**Critical Alarms** (SNS → Email/SMS + Slack):

1. 🔴 **Scaling Failure** - 3+ failures in 15 minutes
2. 🔴 **CPU Overload** - Cluster CPU > 90% for 5+ minutes
3. 🔴 **At Max Capacity** - Node count = 10 for 10+ minutes
4. 🔴 **Node Join Failure** - New instance not Ready after 5 minutes

**Warning Alarms** (Slack only):

1. ⚠️ **High Memory** - Memory > 80% for 10 minutes
2. ⚠️ **Lambda Timeout** - Execution time > 50 seconds
3. ⚠️ **Prometheus Unavailable** - Query failures for 5+ minutes

### Slack Notifications

Configure webhook in Secrets Manager:

```bash
aws secretsmanager create-secret \
  --name node-fleet/slack-webhook \
  --secret-string "https://hooks.slack.com/services/YOUR/WEBHOOK/URL" \
  --region ap-southeast-1
```

Notification examples:

```
🟢 Scale-Up Initiated
Reason: CPU 78%, Pending pods: 5
Action: Added 2 nodes (t3.small)
New Total: 7 nodes
Estimated Cost Impact: +₹4,000/day

🔵 Scale-Down Completed
Reason: CPU 25%, No pending pods
Action: Removed 1 node (i-0abc123)
New Total: 3 nodes
Estimated Savings: -₹2,000/day
```

---

## 💰 Cost Analysis

### Baseline (Before Autoscaling)

| Resource             | Quantity | Unit Cost  | Monthly Cost    |
| -------------------- | -------- | ---------- | --------------- |
| t3.small (always on) | 5 nodes  | 24,000 BDT | **120,000 BDT** |
| **Total**            |          |            | **120,000 BDT** |

### Optimized (With Autoscaling)

| Resource                | Usage Pattern      | Monthly Cost    |
| ----------------------- | ------------------ | --------------- |
| t3.small (peak 9AM-9PM) | 5-7 nodes × 12h    | 30,000 BDT      |
| t3.small (off-peak)     | 2 nodes × 12h      | 12,000 BDT      |
| Spot instances (70%)    | 60-70% discount    | -18,000 BDT     |
| Lambda                  | 15,000 invocations | 500 BDT         |
| DynamoDB                | On-demand          | 200 BDT         |
| CloudWatch              | Custom metrics     | 300 BDT         |
| **Total**               |                    | **~60,000 BDT** |

**Savings**: **60,000 BDT/month (50%)** 🎉

### ROI Calculation

- **Development Time**: 80 hours @ 2,000 BDT/hour = 160,000 BDT
- **Monthly Savings**: 60,000 BDT
- **Payback Period**: 2.7 months
- **Annual Savings**: 720,000 BDT (~$6,000 USD)

---

## 🔒 Security Best Practices

### Implemented Security Measures

✅ **No Hardcoded Secrets**

- All credentials in AWS Secrets Manager
- K3s token, Prometheus auth, Slack webhook encrypted

✅ **Least-Privilege IAM**

- Separate roles for Lambda, EC2 master, EC2 workers
- Granular policies (no wildcard `*` permissions)

✅ **Network Isolation**

- Workers in private subnets (no public IPs)
- Security groups: whitelist only required ports
- NAT Gateway for outbound internet access

✅ **Encryption Everywhere**

- EBS volumes: AWS-managed KMS encryption
- DynamoDB: Server-side encryption
- Secrets Manager: AES-256 encryption
- K3s inter-node: TLS 1.3

✅ **Audit Logging**

- CloudWatch Logs: 30-day retention
- DynamoDB Streams: All state changes logged
- CloudTrail: API call auditing


---

## 🐛 Troubleshooting

### Common Issues

#### 1. Lambda Cannot Connect to Prometheus

**Symptom**: `ConnectionError: Failed to connect to Prometheus`

**Solution**:

```bash
# Verify Lambda is in correct VPC
aws lambda get-function-configuration --function-name node-fleet-cluster-autoscaler

# Check security group allows Lambda → Master:30090
aws ec2 describe-security-groups --group-ids sg-xxx

# Test connectivity from Lambda VPC
aws ec2 run-instances --subnet-id subnet-xxx --security-group-ids sg-lambda --user-data "curl http://master-private-ip:30090/api/v1/query?query=up"
```

#### 2. Worker Nodes Not Joining Cluster

**Symptom**: EC2 instance launches but never becomes `Ready`

**Solution**:

```bash
# SSH to worker and check logs
ssh -i ~/.ssh/node-fleet.pem ubuntu@worker-ip
sudo journalctl -u k3s-agent -f

# Common causes:
# - Invalid K3s token: Update Secrets Manager
# - Security group: Allow 6443/tcp from worker to master
# - Master IP resolution failed: Check EC2 tags
```

#### 3. DynamoDB Lock Stuck

**Symptom**: Lambda always exits with "Scaling already in progress"

**Solution**:

```bash
# Check lock status
aws dynamodb get-item --table-name node-fleet-cluster-state --key '{"cluster_id": {"S": "node-fleet-cluster"}}'

# Force release if stuck (emergency only)
aws dynamodb update-item --table-name node-fleet-cluster-state \
  --key '{"cluster_id": {"S": "node-fleet-cluster"}}' \
  --update-expression "SET scaling_in_progress = :false" \
  --expression-attribute-values '{":false": {"BOOL": false}}'
```

#### 4. Scale-Down Drain Timeout

**Symptom**: Node drain takes > 5 minutes, scale-down aborted

**Solution**:

```bash
# Identify stuck pods
kubectl get pods -o wide | grep <node-name>

# Check PodDisruptionBudgets
kubectl get pdb --all-namespaces

# Manually evict stuck pod (if safe)
kubectl delete pod <pod-name> --grace-period=30 --force
```

### Debug Mode

Enable verbose logging:

```bash
# Update Lambda environment variable
aws lambda update-function-configuration \
  --function-name node-fleet-cluster-autoscaler \
  --environment 'Variables={LOG_LEVEL=DEBUG,...}'

# Tail logs with filters
aws logs tail /aws/lambda/node-fleet-cluster-autoscaler --follow --filter-pattern "ERROR"
```

## 👥 Contributing

We welcome contributions! Please see our contributing guidelines:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

### Development Setup

```bash
# Setup Python virtual environment
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r lambda/requirements.txt
pip install pytest black flake8

# Setup pre-commit hooks
pre-commit install

# Run linters
black lambda/
flake8 lambda/
```

## 📞 Support

- **Issues**: [GitHub Issues](https://github.com/BayajidAlam/node-fleet/issues)
- **Email**: bayajidalam2001@gmail.com
- **Documentation**: [Full Docs](docs/)

---

<div align="center">

**Built with ❤️ for cost-conscious cloud architects**

_Scale Smart. Save More. Automate Everything._

</div>
