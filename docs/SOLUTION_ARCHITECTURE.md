# node-fleet K3s Autoscaler - Solution Architecture

> **Document Status**: Final
> **Version**: 1.0
> **Date**: 2026-01-25

## 1. Metric Collection Implementation

**Component**: `metrics_collector.py`
**Source of Truth**: Prometheus

The system collects metrics every 2 minutes via a targeted PromQL query pipeline.

```python
# Implementation Highlight from metrics_collector.py
def collect_metrics(self):
    promql = """
        avg(rate(node_cpu_seconds_total{mode!="idle"}[5m])) * 100
    """
    # ... query logic ...
```

**Key Metrics Monitored**:

1. **CPU Utilization**: Cluster-wide average (Reactive layer)
2. **Pending Pods**: Immediate signal for urgency (Reactive layer)
3. **Queue Depth**: Custom application metric (Bonus #4)
4. **Spot Interruption**: EventBridge signal (Bonus #2)

---

## 2. Scaling Logic & Decision Engine

**Component**: `scaling_decision.py`
**Algorithm**: Hybrid (Reactive + Predictive)

The decision engine uses a 3-layer approach detailed in `SCALING_ALGORITHM.md`.

```python
# Core Decision Logic
def evaluate(metrics):
    # Layer 1: Safety
    if locked or cooldown: return None

    # Layer 2: Reactive
    if cpu > 70 and sustained_for(4_min): return ScaleUp()
    if pending_pods > 0: return ScaleUp(urgent=True)

    # Layer 3: Predictive (Bonus #3)
    if prediction_model.will_spike_in(10_min): return ScaleUp(preventative=True)
```

---

## 3. Node Provisioning System

**Component**: `ec2_manager.py` + Pulumi (IaC)

Provisioning is automated using AWS EC2 Launch Templates managed by Pulumi.

1. **Infrastructure as Code**: `pulumi/src/ec2-worker.ts` defines templates.
2. **User Data Automation**:
   ```bash
   #!/bin/bash
   # User Data Script Highlight
   K3S_TOKEN=$(aws secretsmanager get-secret-value --secret-id node-fleet/k3s-token)
   curl -sfL https://get.k3s.io | K3S_URL=https://<master-ip>:6443 sh -
   ```
3. **Multi-AZ Awareness** (Bonus #1): Round-robin placement across 2 subnets.

---

## 4. Graceful Deprovisioning

**Component**: `ec2_manager.py`

Safety-first scale-down procedure:

1. **Selection**: Identify node with least load (and no critical pods).
2. **Cordon**: Mark unschedulable.
3. **Drain**: `kubectl drain <node> --ignore-daemonsets --delete-emptydir-data`.
4. **Termination**: EC2 API terminate call.

**Constraint**: Never scale down if `current_nodes <= MIN_NODES (2)`.

---

## 5. State Management

**Component**: `state_manager.py`
**Store**: DynamoDB Table `node-fleet-dev-state`

Distributed locking prevents race conditions between Lambda invocations.

- **Locking**: Conditional Write (`attribute_not_exists(lock)`).
- **TTL**: Auto-expiry after 5 minutes (safety valve).
- **Schema**:
  - `cluster_id` (PK)
  - `node_count`
  - `scaling_in_progress` (Bool)
  - `last_scale_time` (ISO8601)

---

## 6. Monitoring & Observability Strategy

**Tools**: Prometheus + Grafana + CloudWatch

1. **Grafana Dashboards** (in `monitoring/grafana-dashboards/`):
   - Cluster Overview: CPU/Mem heatmaps.
   - Autoscaler Performance: Lambda latency vs Scale events.
   - Cost Tracking (Bonus #7): Hourly spend estimations.

2. **CloudWatch Custom Metrics**:
   - `node-fleet/PendingPods`
   - `node-fleet/ScaleUpEvents`
   - `node-fleet/CostEstimates`

**CloudWatch Alarms**:

Scale-Up Alarm:
![Scale Up Alarm](alarms/Scale%20Up.png)

Scale-Down Alarm:
![Scale Down Alarm](alarms/Scale%20Down.png)

3. **Slack Notifications** (Bonus #6):
   - Rich-text alerts for Scale Up/Down and Failures.

---

## 7. Security Architecture

**Core Principle**: Zero Trust & Least Privilege

1. **Secrets**: All tokens (K3s, Slack) in AWS Secrets Manager.
2. **IAM**: Granular roles. Lambda can ONLY terminate instances tagged `Project: node-fleet`.
3. **Network**:
   - Private Subnets for Workers.
   - NAT Gateway for egress.
   - Security Groups: Strict ingress rules (Master <-> Worker).

For full checklist, see `SECURITY_CHECKLIST.md`.

---

## 8. Cost Optimization Strategy

**Target**: 50% reduction provided in `COST_ANALYSIS.md`.

1. **Spot Instances** (Bonus #2): 70% of fleet runs on Spot (60% cheaper).
2. **Dynamic Scaling**: Scale to Minimum (2 nodes) during off-peak (9PM-9AM).
3. **Single Lambda**: One function handles all logic ($1/month).

---

## 9. Testing Strategy (Comprehensive)

**Frameworks**: `pytest`, `k6`, `localstack`

1. **Unit Tests** (`tests/unit/`): Validates logic (e.g. "Does 71% CPU trigger scale?").
2. **Load Tests** (`tests/load-test.js`):
   - **Scenario**: Ramp 0 -> 100 users over 5 mins.
   - **Verification**: Watch node count increase from 2 -> 4.
3. **Failure Scenarios**:
   - **Spot Interruption**: Simulate termination signal -> verify instant replacement.
   - **Drain Failure**: Simulate hung pod -> verify rollback/force kill.

See `TESTING_RESULTS.md` for execution logs.

---

## 10. Deployment (IaC)

**Tool**: Pulumi (TypeScript)

Deploy the entire stack in one command:

```bash
cd pulumi
npm install
pulumi up --yes
```

This provisions:

- VPC & Networking
- IAM Roles
- DynamoDB Tables
- Lambda Function
- EventBridge Scheduler
- EC2 Instances (Master + Initial Workers)
