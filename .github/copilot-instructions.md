# node-fleet K3s Autoscaler - AI Agent Instructions

## Project Overview

**SmartScale K3s Autoscaler** — a production-grade, intelligent autoscaling system for K3s (lightweight Kubernetes) on AWS EC2. Reduces infrastructure costs by 40-50% through automated scaling driven by Prometheus metrics and AWS Lambda.

**Current Status**: Fully implemented and tested. All core requirements and all 7 bonus challenges are complete.

**Repository**: https://github.com/BayajidAlam/node-fleet  
**Branch**: main

---

## Full Requirements Context (SmartScale K3s Autoscaler Challenge)

### Business Context

- E-commerce platform: 15,000+ daily users, ~80 lakh BDT/month transactions
- Current: 5 worker nodes 24/7 at 1.2 lakh BDT/month — wastes 60,000 BDT/month off-peak
- Peak hours 9AM–9PM: CPU 70-80%; Off-peak 9PM–9AM: CPU 20-30%
- Flash sales cause CPU 85-95% spikes — past crash cost 8 lakh BDT + 2,000 complaints
- Goal: Eliminate manual 15-20 min scaling. Automate to <3 minutes.

### Scaling Rules (STRICT — enforced in `lambda/scaling_decision.py`)

**Scale UP when ANY is true:**

- Average CPU > 70% for 3 consecutive minutes (`CPU_SCALE_UP_THRESHOLD = 70.0`)
- Pending pods exist for > 3 minutes (`window=2` readings at 2-min intervals)
- Memory utilization > 75% cluster-wide (`MEMORY_SCALE_UP_THRESHOLD = 75.0`)

**Scale DOWN when ALL are true:**

- Average CPU < 30% for 10+ minutes (`CPU_SCALE_DOWN_THRESHOLD = 30.0`, `window=5`)
- No pending pods
- Memory utilization < 50% (`MEMORY_SCALE_DOWN_THRESHOLD = 50.0`)

**Constraints:**

- Min nodes: 2 (high availability), Max nodes: 10 (cost limit)
- Scale-up: add 1 node (2 if CPU > 85% or pending > 5)
- Scale-down: remove 1 node at a time
- Cooldown after scale-up: 5 minutes (`SCALE_UP_COOLDOWN = 300`)
- Cooldown after scale-down: 10 minutes (`SCALE_DOWN_COOLDOWN = 600`)

### Lambda Specification

- Runtime: Python 3.11
- Memory: 256 MB
- Timeout: 60 seconds
- Trigger: EventBridge every 2 minutes (`rate(2 minutes)`)
- Dead Letter Queue: SQS (`autoscaler-dlq`, 14-day retention)

**6-Step Lambda Flow:**

1. Check DynamoDB lock — exit if scaling already in progress
2. Query Prometheus API for CPU, memory, pending pods
3. Evaluate scaling conditions with history
4. If action needed: acquire lock, execute EC2 API calls
5. Log decision to CloudWatch, publish metrics
6. Release lock in `finally` block (always)

### DynamoDB Schema (table: `k3s-autoscaler-state`)

- Partition key: `cluster_id` (String)
- Attributes: `node_count`, `last_scale_time`, `scaling_in_progress`, `lock_acquired_at`, `lock_expiry`
- Lock expiry: 360 seconds (covers worst-case drain 300s + node join)
- Stale lock auto-cleanup after 360s

### Prometheus Configuration

- Scrape interval: 15 seconds
- Retention: 7 days (NOT 30d)
- Exposed via NodePort 30090 with basic auth
- Credentials stored in Secrets Manager at `node-fleet/prometheus-auth`

### PromQL Queries Used

```promql
CPU:     avg(rate(node_cpu_seconds_total{mode!="idle"}[5m])) * 100
Memory:  (1 - avg(node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100
Pending: sum(kube_pod_status_phase{phase="Pending"})
Nodes:   count(kube_node_info)
```

### Graceful Scale-Down (STRICT order)

1. Select safest node (weighted score: fewest pods, no critical/StatefulSet/single-replica pods)
2. `kubectl drain <node> --ignore-daemonsets --delete-emptydir-data --force --timeout=300s`
   - Note: `kubectl drain` implicitly cordons first — no separate cordon step needed
3. Validate drain: check exit code = 0 AND "drained" in output
4. Only terminate if drain succeeded — skip if failed
5. Delete node from cluster to prevent ghost nodes

**Never terminate nodes hosting:**

- System-critical pods in kube-system (CoreDNS, metrics-server, etc.)
- StatefulSet pods without proper migration
- Single-replica deployments

### Security Requirements

- K3s join token: Secrets Manager at `node-fleet/k3s-token` (NOT S3, NOT plaintext)
- Prometheus credentials: Secrets Manager at `node-fleet/prometheus-auth`
- SSH key for master: Secrets Manager at `node-fleet/ssh-key`
- No hardcoded credentials — fail-fast if Secrets Manager unavailable
- IAM least-privilege for Lambda role (see `pulumi/src/iam.ts`)
- EBS volumes encrypted, Prometheus restricted to Lambda VPC only
- Master IP resolved dynamically via EC2 tag `Role: k3s-master` — never hardcoded

### CloudWatch Alarms (all in `pulumi/src/cloudwatch-alarms.ts`)

- Scaling failures → SNS notification
- CPU > 90% for 5 minutes → emergency alert
- Node count at max (10) for 10+ minutes → capacity warning
- Node join failures → alert

---

## Actual Project Structure

```
node-fleet/
├── .github/
│   └── copilot-instructions.md   ← this file
├── lambda/                        ← Python 3.11 Lambda code
│   ├── autoscaler.py              ← Main handler (6-step flow)
│   ├── scaling_decision.py        ← All scaling logic & thresholds
│   ├── metrics_collector.py       ← Prometheus PromQL queries
│   ├── ec2_manager.py             ← EC2 launch/terminate/drain (SSH to master)
│   ├── state_manager.py           ← DynamoDB lock + state management
│   ├── slack_notifier.py          ← SNS → Slack webhook
│   ├── multi_az_helper.py         ← Multi-AZ subnet selection
│   ├── spot_instance_helper.py    ← Spot mix (70%), interruption handling
│   ├── predictive_scaling.py      ← Historical pattern analysis (7-day)
│   ├── custom_metrics.py          ← Queue depth, latency, error rate metrics
│   ├── cost_optimizer.py          ← Cost tracking and recommendations
│   ├── dynamic_scheduler.py       ← Adjusts EventBridge interval dynamically
│   ├── audit_logger.py            ← DynamoDB streams audit trail
│   └── requirements.txt
├── pulumi/                        ← TypeScript IaC (NOT Python)
│   └── src/
│       ├── vpc.ts                 ← VPC, subnets (2 AZs)
│       ├── ec2-master.ts          ← Master node (t3.medium)
│       ├── ec2-worker.ts          ← Worker launch templates (on-demand + spot)
│       ├── lambda.ts              ← Lambda + EventBridge + SQS DLQ
│       ├── dynamodb.ts            ← State table + metrics history table
│       ├── iam.ts                 ← Least-privilege Lambda role
│       ├── security-groups.ts     ← SGs for master, workers, Lambda
│       ├── cloudwatch-alarms.ts   ← All CloudWatch alarms
│       ├── sns.ts                 ← SNS topic + Slack notifier Lambda
│       ├── secrets.ts             ← Secrets Manager resources
│       ├── s3.ts                  ← Lambda artifacts bucket
│       └── keypair.ts             ← EC2 key pair
├── k3s/
│   ├── master-setup.sh            ← K3s master init + Prometheus + auth
│   └── worker-userdata.sh         ← Auto-join via Secrets Manager token
├── gitops/
│   └── infrastructure/
│       └── prometheus-deployment.yaml  ← Prometheus K8s manifest (retention: 7d)
├── monitoring/
│   └── grafana-dashboards/        ← Dashboard JSON files
├── demo-app/                      ← Flask app for load testing
├── tests/
│   └── lambda/                    ← 120+ pytest unit/integration tests
└── docs/                          ← Architecture diagrams, runbooks
```

---

## Architecture Data Flow

```
EventBridge (2min) → Lambda
    → Secrets Manager (Prometheus credentials)
    → Prometheus NodePort:30090 (CPU/memory/pending pods)
    → DynamoDB (acquire lock, read state/history)
    → Scaling Decision Engine
        → EC2 RunInstances / TerminateInstances
        → SSH to master (kubectl drain, node delete)
    → DynamoDB (update state, release lock)
    → CloudWatch (metrics, logs)
    → SNS → Slack (scale event notification)
```

---

## Implemented Bonus Features

All 7 bonus challenges are complete:

| Bonus                    | Module                         | Status                                   |
| ------------------------ | ------------------------------ | ---------------------------------------- |
| Multi-AZ workers         | `multi_az_helper.py`           | ✅ Balances across ap-south-1a/1b        |
| Spot instances (70% mix) | `spot_instance_helper.py`      | ✅ With interruption drain               |
| Predictive scaling       | `predictive_scaling.py`        | ✅ 7-day history, pre-scales 10min early |
| Custom app metrics       | `custom_metrics.py`            | ✅ Queue depth, latency p95, error rate  |
| GitOps                   | `gitops/`                      | ✅ Versioned K8s manifests               |
| Slack notifications      | `slack_notifier.py` + `sns.ts` | ✅ Scale up/down/fail/warning            |
| Cost dashboard           | `cost_optimizer.py`            | ✅ Instance hours, Lambda cost, savings  |

---

## Key Implementation Notes for AI Agent

### Master IP Resolution

**NEVER hardcode the master IP.** Always use:

```python
def _get_master_ip(self) -> str:
    response = self.ec2_client.describe_instances(
        Filters=[
            {'Name': 'tag:Role', 'Values': ['k3s-master']},
            {'Name': 'instance-state-name', 'Values': ['running']}
        ]
    )
    return response['Reservations'][0]['Instances'][0]['PrivateIpAddress']
```

### Prometheus Credentials

**NEVER use hardcoded fallback passwords.** Fetch from Secrets Manager first, then env vars, fail-fast:

```python
def get_prometheus_credentials():
    try:
        sm = boto3.client('secretsmanager')
        creds = json.loads(sm.get_secret_value(SecretId="node-fleet/prometheus-auth")['SecretString'])
        return creds['username'], creds['password']
    except Exception:
        username = os.environ.get("PROMETHEUS_USERNAME")
        password = os.environ.get("PROMETHEUS_PASSWORD")
        if not username or not password:
            raise ValueError("Prometheus credentials unavailable")
        return username, password
```

### DynamoDB Lock Pattern

```python
# Acquire: conditional write, expiry = 360s
# Release: always in finally block
# Stale lock: auto-release if lock_acquired_at < now - 360s
```

### Drain Validation

Always check exit code AND "drained" keyword in kubectl output:

```python
if exit_status != 0 or "drained" not in out_str:
    return False  # Do NOT terminate
```

### Scale-Down Window Math

- Lambda runs every 2 minutes
- `window=5` → 5 readings × 2min = 10 minutes sustained ✅
- `window=2` for pending pods → ~2-4 min (closest to 3-min requirement) ✅

### Pulumi is TypeScript (NOT Python)

All IaC in `pulumi/src/*.ts`. Do not write `.py` files in the pulumi directory.

---

## Commands Reference

```bash
# Infrastructure
cd pulumi && pulumi up
pulumi stack output masterIp

# Lambda deployment
cd lambda
pip install -r requirements.txt -t .
zip -r function.zip . --exclude "*.pyc" "venv/*" "tests/*"
aws lambda update-function-code --function-name node-fleet-cluster-autoscaler --zip-file fileb://function.zip

# K3s cluster
ssh -i node-fleet-key.pem ubuntu@<master-ip>
./k3s/master-setup.sh
kubectl get nodes

# Testing
cd tests && python -m pytest lambda/ -v
k6 run tests/load-test.js --vus 100 --duration 5m

# GitOps
kubectl apply -f gitops/infrastructure/prometheus-deployment.yaml
```

---

## Evaluation Criteria (for reference)

| Criteria                              | Weight |
| ------------------------------------- | ------ |
| Architecture Design & AWS Integration | 20%    |
| Lambda Autoscaler Implementation      | 20%    |
| Prometheus Monitoring Setup           | 15%    |
| Graceful Scaling Logic (Up & Down)    | 15%    |
| Security & IAM Best Practices         | 10%    |
| Documentation & Clarity               | 10%    |
| Presentation & Live Demo              | 10%    |
