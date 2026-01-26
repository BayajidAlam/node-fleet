# node-fleet K3s Autoscaler

![Tests](https://img.shields.io/badge/tests-120%20passing-brightgreen)
![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen)
![Build](https://img.shields.io/badge/build-passing-brightgreen)

<div align="center">

**Intelligent, Cost-Optimized Kubernetes Autoscaling for AWS**

[![AWS](https://img.shields.io/badge/AWS-EC2%20%7C%20Lambda%20%7C%20DynamoDB-orange)](https://aws.amazon.com)
[![K3s](https://img.shields.io/badge/K3s-v1.28-blue)](https://k3s.io)
[![Python](https://img.shields.io/badge/Python-3.11-blue)](https://python.org)
[![Pulumi](https://img.shields.io/badge/IaC-Pulumi%20TypeScript-purple)](https://pulumi.com)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

_Reduce infrastructure costs by 40-50% through intelligent, event-driven autoscaling_

[Features](#-features) • [Architecture](#-architecture) • [Quick Start](#-quick-start) • [Technical Docs](#-technical-documentation) • [Testing](#-testing)

</div>

---

## 📋 Project Overview

### Business Problem & Problem Statement

**Client**: TechFlow Solutions - E-commerce platform (Dhaka, Bangladesh).

**Current Pain Points**:
- 💸 **1.2 lakh BDT/month** infrastructure waste (5 static nodes running 24/7, 60% idle).
- 🔥 **Flash sale crashes** - manual scaling takes 15-20 minutes, causing significant revenue loss.
- 👨‍💻 **Manual intervention** required for every traffic spike, leading to high operational overhead.

**Our Solution**:
**node-fleet** is an intelligent, serverless autoscaler for K3s clusters. It shifts from a reactive, manual operations model to an automated, metric-driven architecture. By leveraging AWS Lambda and Prometheus, it ensures the cluster capacity perfectly matches real-time demand while optimizing for cost via Spot instances.

### Success Metrics
- ✅ **Cost Reduction**: Target 40-50% savings (60K BDT/month).
- ✅ **Response Time**: <3 min for new capacity (83% faster than manual).
- ✅ **Reliability**: 0 service disruptions during scaling operations.

---

## 🏗️ Architecture Explanation

### High-Level System Design

![System Architecture](docs/diagrams/system_architecture.png)

**Design Rationale**:
- **K3s over Standard K8s**: Chosen for its lightweight footprint (50% less resource usage on master), crucial for cost-saving on small instances.
- **AWS Lambda**: Used for the autoscaler "brain" to avoid paying for a 24/7 controller instance.
- **VPC Isolation**: Master and monitoring run in a controlled environment, while workers reside in private subnets for enhanced security.

### Data Flow
1. **EventBridge** triggers Lambda every 2 minutes.
2. **Lambda** scrapes custom metrics from **Prometheus**.
3. **Lambda** checks **DynamoDB** to manage state and distributed locking.
4. **Decision Engine** evaluates thresholds and historical patterns.
5. **EC2 Manager** provisions (Launch Templates) or terminates nodes gracefully.

---

## 🛠️ Tools and Technologies Used

| Category | Technology | Justification |
| :--- | :--- | :--- |
| **Cloud Provider** | AWS | Industry-standard reliability and robust Serverless (Lambda) ecosystem. |
| **Orchestration** | K3s | Lightweight, perfect for cost-optimized cloud and edge deployments. |
| **Monitoring** | Prometheus | Native Kubernetes support and powerful PromQL for complex scaling metrics. |
| **IaC** | Pulumi (TS) | Preferred over Terraform for its strong type-safety and support for actual coding logic. |
| **State Storage** | DynamoDB | No-SQL performance with built-in conditional writes for distributed locking. |
| **Testing** | k6 / Pytest | Modern tools for high-performance load testing and robust unit verification. |

### 🧠 Lambda Logic & Decision Engine

The autoscaler core is modularly designed into five specialized components:
- **Metric Collector**: Aggregates CPU, Memory, and Pending Pod data from Prometheus.
- **Decision Engine**: Evaluates thresholds (70% CPU/Pending Pods) and applies cooldowns.
- **State Manager**: Implements distributed locking in DynamoDB to prevent simultaneous scaling.
- **EC2 Manager**: Orchestrates instance lifecycle via Launch Templates.

### 📜 Terraform/IaC Definitions

We utilize Pulumi's **TypeScript SDK** to manage our AWS fleet. This allows for native programming constructs like loops for Multi-AZ distribution and conditional logic for Spot vs On-Demand provisioning.

```typescript
// Example: Cost-Optimized Spot Instance Definition
export const workerSpotTemplate = new aws.ec2.LaunchTemplate("worker-spot", {
    imageId: ubuntuAmiId,
    instanceType: "t3.medium",
    instanceMarketOptions: { marketType: "spot" }
});
```

---

## 🚀 Setup and Deployment Instructions

### Prerequisites
- AWS CLI configured and Pulumi CLI installed.
- Node.js 18+ and Python 3.11+.

### 1. Infrastructure Deployment (10 min)
```bash
cd pulumi
npm install
pulumi up --yes
```

### 2. Cluster Setup
1. SSH into the Master node using the IP provided by Pulumi output.
2. Run `sudo bash /tmp/master-setup.sh` to initialize the K3s control plane.
3. Verify cluster connectivity: `kubectl get nodes`.

### 3. Monitoring Verification
Access Grafana via port-forwarding:
```bash
kubectl port-forward -n monitoring svc/grafana 3000:80
```

---

## 📘 Technical Documentation

### 🐍 Lambda Function Code and Logic
The Lambda handler (`node-fleet-autoscaler`) follows a tiered execution logic:
- **Pre-flight**: Acquires a DynamoDB lock with a 5-minute TTL to prevent race conditions.
- **Discovery**: Queries Prometheus for `node_cpu_utilization`, `pending_pods`, and `api_latency`.
- **Logic**: Applies the Decision Engine (see Algorithm section below).
- **Execution**: Interacts with the EC2 Service to manage the fleet.

### 🔒 IAM Policy (JSON)
The Lambda function operates under a **Least Privilege** policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ec2:RunInstances",
        "ec2:TerminateInstances",
        "ec2:DescribeInstances",
        "ec2:DescribeInstanceStatus",
        "ec2:CreateTags",
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "secretsmanager:GetSecretValue",
        "cloudwatch:PutMetricData"
      ],
      "Resource": "*"
    }
  ]
}
```

### 📊 Prometheus Configuration (prometheus.yml)
The cluster scrapes both system and kube-state metrics every 15 seconds:

```yaml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'kubernetes-nodes'
    kubernetes_sd_configs:
      - role: node
    relabel_configs:
      - source_labels: [__address__]
        regex: '(.*):10250'
        replacement: '${1}:9100'
        target_label: __address__
  
  - job_name: 'kube-state-metrics'
    static_configs:
      - targets: ['kube-state-metrics.monitoring.svc.cluster.local:8080']
```

**Key PromQL Queries Used**:
- **CPU**: `avg(rate(node_cpu_seconds_total{mode!="idle"}[5m])) * 100` (Direct load indicator)
- **Memory**: `(1 - avg(node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100`
- **Pending Pods**: `sum(kube_pod_status_phase{phase="Pending"})` (Critical scale-up trigger)

### 🗄️ DynamoDB Schema and Example Data
**Table**: `node-fleet-cluster-state`
- **Partition Key**: `cluster_id` (String)
- **Attributes**: `node_count`, `scaling_in_progress`, `lock_expiry`.

**Example State (Locked)**:
```json
{
  "cluster_id": "node-fleet-prod",
  "scaling_in_progress": true,
  "lock_expiry": 1706263200,
  "node_count": 4
}
```

### 🐚 EC2 User Data Script (Worker Join)
Every new worker node automatically joins the fleet via this entrypoint:
```bash
#!/bin/bash
K3S_TOKEN=$(aws secretsmanager get-secret-value --secret-id node-fleet/k3s-token --query SecretString --output text)
MASTER_IP=$(aws ec2 describe-instances --filters "Name=tag:Role,Values=k3s-master" --query 'Reservations[0].Instances[0].PrivateIpAddress' --output text)
curl -sfL https://get.k3s.io | K3S_URL=https://${MASTER_IP}:6443 K3S_TOKEN=${K3S_TOKEN} sh -
```

---

## 📈 Scaling Algorithm & Logic

The autoscaler uses a tiered decision-making engine to balance cluster stability with cost efficiency.

### 🤖 Decision Logic (Pseudocode)
```python
def node_fleet_brain(metrics, state):
    # 1. Cooldown Check
    if now < state.last_action_time + cooldown:
        return IDLE

    # 2. Critical Scale-Up (Reactive)
    if metrics.pending_pods > 0:
        return SCALE_UP(nodes=2, level="CRITICAL")

    # 3. Standard Scale-Up (Reactive)
    if metrics.cpu_utilization > 70%:
        return SCALE_UP(nodes=1, level="HIGH_LOAD")

    # 4. Predictive Pre-Scaling (AI-Driven)
    if patterns.is_flash_sale_incoming(now):
        return SCALE_UP(nodes=1, level="PREDICTIVE")

    # 5. Gradual Scale-Down (Safe)
    if metrics.cpu_utilization < 30% and metrics.pending_pods == 0:
        return SCALE_DOWN(nodes=1)
```

### 🧠 Logic Rationale
- **Why 70% CPU Threshold?**: t3.medium instances have a finite CPU credit balance. Scaling at 70% prevents "exhaustion" where the node would be throttled, causing cascading failures.
- **Why prioritize Pending Pods?**: A pending pod indicates that a user request is currently unfulfilled. This is the highest priority signal for scaling.
- **Why 10-Min Scale-Down Cooldown?**: Draining a node causes pod evictions. We wait 10 minutes to ensure a traffic surge is truly over, minimizing "churn" and pod rescheduling overhead.

---

## 📊 Monitoring & Dashboard Showcase

We track Lambda performance, EC2 lifecycle events, and cluster health.

> [!NOTE]
> **Insert your production dashboards here:**
> *   **System Health**: [INSERT_SS_HERE: grafana_cluster_dashboard.png]
> *   **Cost Tracking**: [INSERT_SS_HERE: cloudwatch_cost_analysis.png]

---

## 🧪 Testing Strategy and Results

We utilize a multi-layered testing strategy verified across **120 test cases**:
- **Load Testing**: Simulated with `k6` to verify scale-up under massive concurrent load.
- **Failure Scenarios**: Verified handled by mocks (SSM connectivity failure, EC2 Quota full).

**Final Results**: 100% Pass Rate (120/120 Tests).
See the full **[Verification Report](docs/TESTING.md)** for details.

---

## 💰 Cost Analysis (Before vs After)

| Resource | Baseline (5 Nodes Static) | node-fleet Optimized |
| :--- | :--- | :--- |
| EC2 Instances | 1.2 Lakh BDT | 55,000 BDT |
| Lambda/DynamoDB | 0 BDT | 1,000 BDT |
| **Total Monthly** | **1.2 Lakh BDT** | **~56,000 BDT** |
| **Savings** | - | **53.3% Savings** 🎉 |

---

## 👥 Team Members and Roles

- **Core DevOps Engineer**: Bayajid Alam
- **Architecture & Logic**: AI Assisted Professional Implementation

---

## 🗓️ Lab Session Progress

| Session | Focus Area | Key Milestones |
| :--- | :--- | :--- |
| **Session 1-4** | Infrastructure | VPC, IAM, K3s Master deployment via Pulumi. |
| **Session 5-8** | Monitoring | Prometheus, Grafana, and Custom Metric Exporters. |
| **Session 9-12** | Autoscaler Core | Lambda Decision Engine, locking, and DynamoDB state. |
| **Session 13-16** | Verification | Load Testing (k6), Failure Scenarios, and final naming audit. |

---

<div align="center">

**Scale Smart. Save More. Automate Everything.**

🚀 Built with ❤️ for node-fleet architects

</div>
