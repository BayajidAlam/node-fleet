# 🧩 SmartScale K3s Autoscaler - Final Project

> **TechFlow Solutions E-Commerce Platform Autoscaling System**  
> Intelligent K3s autoscaler that reduces infrastructure costs by 40-50% while improving reliability

[![Project Status](https://img.shields.io/badge/Status-In%20Development-yellow)]()
[![AWS](https://img.shields.io/badge/AWS-Cloud-orange)]()
[![K3s](https://img.shields.io/badge/K3s-Kubernetes-blue)]()
[![Python](https://img.shields.io/badge/Python-3.11-blue)]()

---

## 📋 Project Overview

### Business Context
TechFlow Solutions is a Dhaka-based e-commerce startup serving **15,000+ daily users** and processing **80 lakh BDT in monthly transactions**. The platform currently runs on a fixed 5-node K3s cluster costing **1.2 lakh BDT/month**, with:

- **60,000 BDT/month wasted** on unused capacity during off-peak hours
- **8 lakh BDT lost** in a recent flash sale crash due to insufficient capacity
- **15-20 minute manual scaling** process causing service degradation

### Solution
An intelligent, automated autoscaling system that:
- ✅ Monitors cluster metrics in real-time using Prometheus
- ✅ Makes smart scaling decisions via AWS Lambda
- ✅ Automatically provisions/deprovisions EC2 worker nodes
- ✅ Scales seamlessly without manual intervention
- ✅ Reduces costs by 40-50% while preventing outages
- ✅ Responds to traffic spikes within 3 minutes

---

## 🏗️ Architecture

### High-Level System Design

```
┌─────────────────────────────────────────────────────────────────┐
│                        AWS Cloud (VPC)                          │
│                                                                 │
│  ┌──────────────────┐         ┌─────────────────────────────┐ │
│  │  K3s Master Node │◄────────┤  Prometheus (Metrics)       │ │
│  │   (t3.medium)    │         │  - CPU, Memory, Pods        │ │
│  └────────┬─────────┘         └─────────────┬───────────────┘ │
│           │                                   │                 │
│           │ Manages                          │ Scrapes         │
│           ▼                                   ▼                 │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │           K3s Worker Nodes (Auto-Scaled)                 │ │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐    │ │
│  │  │ Worker1 │  │ Worker2 │  │ Worker3 │  │ Worker4 │ ...│ │
│  │  └─────────┘  └─────────┘  └─────────┘  └─────────┘    │ │
│  │         Min: 2 nodes  │  Max: 10 nodes                   │ │
│  └──────────────────────────────────────────────────────────┘ │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │  AWS Lambda Autoscaler (Python 3.11)                     │ │
│  │  ┌────────────────────────────────────────────────────┐  │ │
│  │  │ Every 2 min:                                       │  │ │
│  │  │ 1. Query Prometheus metrics                        │  │ │
│  │  │ 2. Check DynamoDB for locks                        │  │ │
│  │  │ 3. Evaluate scaling conditions                     │  │ │
│  │  │ 4. Launch/Terminate EC2 instances                  │  │ │
│  │  │ 5. Update state & send Slack alerts                │  │ │
│  │  └────────────────────────────────────────────────────┘  │ │
│  └──────────────────────────────────────────────────────────┘ │
│           │                                                     │
│           ├──────────► DynamoDB (State Management)             │
│           ├──────────► Secrets Manager (K3s Token)             │
│           ├──────────► ECR (Container Registry)                │
│           ├──────────► SNS → Slack (Notifications)             │
│           └──────────► CloudWatch (Logs & Alarms)              │
└─────────────────────────────────────────────────────────────────┘

          ▲
          │ Triggers every 2 minutes
          │
┌─────────┴────────┐
│  EventBridge     │
│  Scheduler       │
└──────────────────┘
```

---

## 🎯 Scaling Logic

### Scale UP Conditions (ANY must be true)
- ⚠️ Average CPU > **70%** for 3 consecutive minutes
- ⚠️ Pending pods exist for > **3 minutes**
- ⚠️ Memory utilization > **75%** cluster-wide

### Scale DOWN Conditions (ALL must be true)
- ✅ Average CPU < **30%** for 10+ minutes
- ✅ No pending pods exist
- ✅ Memory utilization < **50%**

### Constraints
- **Minimum Nodes**: 2 (high availability)
- **Maximum Nodes**: 10 (cost/quota limit)
- **Scale-up**: Add 1-2 nodes at a time
- **Scale-down**: Remove 1 node at a time

### Cooldown Periods
- **After Scale-Up**: 5 minutes (prevent thrashing)
- **After Scale-Down**: 10 minutes (stability)

---

## 🛠️ Technology Stack

| Category | Technology | Purpose |
|----------|-----------|---------|
| **IaC** | Pulumi (Python) | Infrastructure provisioning |
| **Orchestration** | K3s | Lightweight Kubernetes |
| **Cloud Provider** | AWS (EC2, Lambda, DynamoDB) | Compute & services |
| **Monitoring** | Prometheus + Grafana | Metrics collection & visualization |
| **Autoscaler** | AWS Lambda (Python 3.11) | Scaling decision engine |
| **State Management** | DynamoDB | Distributed locks & state |
| **Secret Management** | AWS Secrets Manager | Secure K3s token storage |
| **Container Registry** | Amazon ECR | Docker image storage |
| **Load Testing** | k6 | Traffic simulation |
| **Alerting** | CloudWatch + SNS + Slack | Real-time notifications |
| **CI/CD** | GitHub Actions | Automated deployment |

---

## 📁 Project Structure

```
SmartScale-K3s-Autoscaler/
├── pulumi/                      # Infrastructure as Code
│   ├── __main__.py             # Main Pulumi program
│   ├── Pulumi.yaml             # Project configuration
│   ├── requirements.txt        # Python dependencies
│   ├── vpc.py                  # VPC & networking
│   ├── ec2.py                  # EC2 instances & launch templates
│   ├── lambda_function.py      # Lambda resource definitions
│   ├── dynamodb.py             # State management table
│   ├── iam.py                  # IAM roles & policies
│   ├── secrets.py              # Secrets Manager configuration
│   └── monitoring.py           # CloudWatch alarms & dashboards
│
├── lambda/                      # Lambda autoscaler code
│   ├── autoscaler.py           # Main scaling logic
│   ├── metrics_collector.py    # Prometheus query functions
│   ├── ec2_manager.py          # EC2 launch/terminate operations
│   ├── state_manager.py        # DynamoDB operations
│   ├── k3s_helper.py           # K3s node management
│   ├── slack_notifier.py       # Slack webhook integration
│   ├── requirements.txt        # Lambda dependencies
│   └── utils.py                # Shared utilities
│
├── k3s/                         # K3s cluster configuration
│   ├── master-setup.sh         # Master node initialization
│   ├── worker-userdata.sh      # Worker node auto-join script
│   ├── prometheus.yml          # Prometheus scrape configs
│   └── prometheus-deployment.yaml  # Prometheus K8s manifest
│
├── monitoring/                  # Observability configs
│   ├── grafana-dashboards/
│   │   ├── cluster-overview.json
│   │   ├── autoscaler-metrics.json
│   │   └── cost-tracking.json
│   ├── cloudwatch-alarms.json  # Alarm definitions
│   └── alert-rules.yml         # Prometheus alert rules
│
├── demo-app/                    # Sample application
│   ├── app.py                  # Flask API
│   ├── Dockerfile              # Container definition
│   ├── deployment.yaml         # K8s deployment manifest
│   └── requirements.txt        # App dependencies
│
├── tests/                       # Testing suite
│   ├── load-test.js            # k6 load testing script
│   ├── test-scale-up.sh        # Scale-up scenario test
│   ├── test-scale-down.sh      # Scale-down scenario test
│   └── test-plan.md            # Testing strategy
│
├── .github/                     # CI/CD workflows
│   └── workflows/
│       ├── deploy.yml          # Deployment pipeline
│       └── test.yml            # Automated testing
│
├── docs/                        # Documentation
│   ├── architecture.md         # Detailed architecture
│   ├── scaling-algorithm.md    # Algorithm explanation
│   ├── runbook.md              # Operational procedures
│   ├── troubleshooting.md      # Common issues & fixes
│   └── diagrams/               # Architecture diagrams
│
├── .gitignore                   # Git ignore rules
├── README.md                    # This file
└── SETUP.md                     # Step-by-step setup guide
```

---

## 🚀 Quick Start

### Prerequisites
- AWS Account with Free Tier access
- AWS CLI configured (`aws configure`)
- Python 3.11+
- Pulumi CLI installed
- kubectl installed
- Docker installed

### Installation Steps

1. **Clone the repository**
   ```bash
   git clone <your-repo-url>
   cd k3-scale
   ```

2. **Install Pulumi dependencies**
   ```bash
   cd pulumi
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. **Configure Pulumi stack**
   ```bash
   pulumi login
   pulumi stack init dev
   pulumi config set aws:region us-east-1
   pulumi config set slackWebhookUrl <your-webhook-url> --secret
   ```

4. **Deploy infrastructure**
   ```bash
   pulumi up
   ```

5. **Set up K3s cluster**
   ```bash
   cd ../k3s
   ./master-setup.sh
   ```

6. **Deploy demo application**
   ```bash
   cd ../demo-app
   kubectl apply -f deployment.yaml
   ```

7. **Run load tests**
   ```bash
   cd ../tests
   k6 run load-test.js
   ```

---

## 📊 Key Prometheus Queries

### CPU Utilization
```promql
avg(rate(node_cpu_seconds_total{mode!="idle"}[5m])) * 100
```

### Memory Utilization
```promql
(1 - avg(node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100
```

### Pending Pods
```promql
sum(kube_pod_status_phase{phase="Pending"})
```

### Node Count
```promql
count(kube_node_info)
```

---

## 🔐 Security Features

- ✅ K3s join token stored in AWS Secrets Manager (encrypted)
- ✅ IAM roles with least-privilege policies
- ✅ Prometheus endpoint secured with authentication
- ✅ EC2 volumes encrypted at rest
- ✅ Inter-node communication encrypted
- ✅ Security groups restrict access to necessary ports only
- ✅ No hardcoded credentials in code

---

## 💰 Cost Optimization

### Before Autoscaler
- **Fixed 5 nodes** running 24/7
- **Monthly cost**: ~1.2 lakh BDT
- **Utilization**: 20-30% off-peak (12 hours/day)
- **Wasted cost**: ~60,000 BDT/month

### After Autoscaler
- **Dynamic scaling**: 2-10 nodes based on demand
- **Expected monthly cost**: ~60,000-70,000 BDT
- **Savings**: 40-50% reduction
- **Improved reliability**: No capacity-related outages

---

## 📈 Monitoring & Alerting

### CloudWatch Dashboards
- Real-time node count and scaling events
- CPU/Memory utilization trends (24-hour view)
- Pending pods and pod distribution
- Lambda execution metrics
- Cost tracking

### Slack Notifications
- 🟢 Scale-up initiated (nodes added, reason, new total)
- 🔵 Scale-down initiated (node drained, reason, new total)
- 🔴 Scaling failures with error details
- ⚠️ Cluster at max capacity warning
- ✅ Scaling operation completed

---

## 🧪 Testing Strategy

### Load Testing (k6)
- Simulate gradual traffic increase (0 → 10,000 RPS)
- Flash sale scenario (instant spike)
- Sustained high load (30+ minutes)

### Scale-Up Testing
- Verify nodes launch within 3 minutes
- Confirm automatic K3s cluster join
- Check pod distribution across new nodes

### Scale-Down Testing
- Verify graceful pod drainage
- Confirm no service disruption
- Test PodDisruptionBudget respect

### Failure Scenarios
- Lambda timeout during scaling
- EC2 quota exceeded
- Prometheus unavailable
- DynamoDB lock stuck
- Node join failure

---

## 👥 Team & Contributions

**Developer**: [Your Name]  
**Project Duration**: Dec 16, 2024 - Jan 15, 2025  
**Institution**: Poridhi.io System Design Batch 1

---

## 📚 Documentation

- [Architecture Details](docs/architecture.md)
- [Scaling Algorithm](docs/scaling-algorithm.md)
- [Operational Runbook](docs/runbook.md)
- [Troubleshooting Guide](docs/troubleshooting.md)

---

## 🏆 Bonus Features Implemented

- [ ] Multi-AZ node distribution
- [ ] Spot instance integration
- [ ] Predictive scaling (historical analysis)
- [ ] Custom application metrics (queue depth, latency)
- [ ] GitOps with FluxCD/ArgoCD
- [ ] Real-time cost tracking dashboard

---

## 📝 Development Log

Regular commits tracking progress every 4-5 hour lab session:

- **Session 1** (Dec XX): Initial setup, folder structure
- **Session 2** (Dec XX): Pulumi infrastructure base
- **Session 3** (Dec XX): Lambda autoscaler core logic
- **Session 4** (Dec XX): K3s cluster setup & Prometheus
- **Session 5** (Dec XX): Integration testing
- **Session 6** (Dec XX): Monitoring & alerting
- **Session 7** (Dec XX): Load testing & optimization
- **Session 8** (Dec XX): Documentation & final demo

---

## 📄 License

This project is developed as part of the Poridhi.io System Design Final Exam.

---

## 🔗 References

- [K3s Documentation](https://docs.k3s.io/)
- [AWS Lambda Best Practices](https://docs.aws.amazon.com/lambda/latest/dg/best-practices.html)
- [Prometheus Query Examples](https://prometheus.io/docs/prometheus/latest/querying/examples/)
- [Pulumi AWS Provider](https://www.pulumi.com/registry/packages/aws/)
- [k6 Load Testing](https://k6.io/docs/)

---

**🚀 "Scale Smart. Automate Everything. Document Clearly."**
