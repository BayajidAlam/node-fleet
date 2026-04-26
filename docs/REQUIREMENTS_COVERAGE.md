# Requirements Coverage — node-fleet K3s Autoscaler

How every functional, non-functional, and bonus requirement is implemented.

---

## Overview Diagrams

![System Overview](diagrams/screenshots/00-System-Overview.png)

![Lambda 7-Step Flow](diagrams/screenshots/00-Lambda-7-Step-Flow.png)

---

## Requirement Types

### FR — Functional Requirements
**What the system does.** Direct features and behaviors the system must provide.

| # | Requirement | Short Answer |
|---|---|---|
| FR-1 | Metric Collection | Prometheus scrapes all nodes every 15s via kubernetes_sd |
| FR-2 | Scaling Logic | 3-layer engine: reactive CPU/mem/pods → custom app → predictive |
| FR-3 | Node Provisioning | EC2 RunInstances + userdata auto-joins K3s via Secrets Manager token |
| FR-4 | Node Deprovisioning | SSM async drain (300s timeout) → validate → terminate |
| FR-5 | State Management | DynamoDB conditional write lock — prevents race conditions |

### NFR — Non-Functional Requirements
**How well the system does it.** Quality attributes — speed, reliability, cost, security, observability.

| # | Requirement | Short Answer |
|---|---|---|
| NFR-1 | Performance < 3 min | Achieved ~100s avg (44% faster than required) |
| NFR-2 | Reliability | `finally` block always releases lock; drain state survives crashes |
| NFR-3 | Lambda < 30s | Async design keeps Lambda at ~1.4s avg (95% under limit) |
| NFR-4 | Security | All creds in Secrets Manager; IAM least-privilege per role |
| NFR-5 | Observability | CloudWatch logs + 9 custom metrics + 8 alarms + Grafana dashboards |

### Bonus Challenges
**Optional enhancements** beyond core requirements. All 6 implemented.

| # | Challenge | Short Answer |
|---|---|---|
| BONUS-1 | Multi-AZ distribution | Workers spread across AZ-1a/1b private subnets via `multi_az_helper.py` |
| BONUS-2 | Spot instances | 70% Spot / 30% On-Demand mix; interruption handled via EventBridge |
| BONUS-3 | Predictive scaling | 7-day lookback → pre-scale before predicted next-hour spike |
| BONUS-4 | Custom app metrics | Queue depth, latency p95, error rate feed into Layer 2 decision |
| BONUS-5 | GitOps | FluxCD polls GitHub every 1 min; applies changes within 1 min of push |
| BONUS-6 | Slack notifications | SNS → Lambda → Slack webhook for every scale event and failure |

> **Diagrams:** `docs/diagrams/` contains one Excalidraw file per requirement — open in VS Code (Excalidraw extension) or drag to [excalidraw.com](https://excalidraw.com).

---

## Functional Requirements

### FR-1: Metric Collection
> Prometheus scrapes CPU, memory, pod count from all K3s nodes. Collect custom app metrics (pending pods, API latency).

![FR-1 Metric Collection](diagrams/screenshots/FR-1-Metric-Collection.png)

**Implementation:**

Prometheus deployed as pod on master node with `kubernetes_sd_configs` discovering all nodes automatically. node-exporter DaemonSet runs on every worker exposing hardware metrics.

| Metric | PromQL | Source |
|--------|--------|--------|
| CPU % | `avg(rate(node_cpu_seconds_total{mode!="idle"}[5m])) * 100` | node-exporter |
| Memory % | `(1 - avg(node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100` | node-exporter |
| Pending pods | `sum(kube_pod_status_phase{phase="Pending"})` | kube-state-metrics |
| Node count | `count(up{job="node-exporter"}) - 1` | node-exporter (excludes master) |
| Queue depth | `app_queue_depth{queue="default"}` | demo-app /metrics |
| Latency p95 | `histogram_quantile(0.95, rate(http_request_duration_seconds_bucket{service="api"}[5m])) * 1000` | demo-app /metrics |
| Error rate | `(rate(http_requests_total{service="api", status=~"5.."}[5m]) / rate(http_requests_total{service="api"}[5m])) * 100` | demo-app /metrics |

**Files:** `lambda/metrics_collector.py`, `lambda/custom_metrics.py`, `monitoring/prometheus/prometheus-config.yaml`

---

### FR-2: Scaling Logic
> Scale UP: CPU >70% OR pending pods >3 min. Scale DOWN: CPU <30% for >10 min AND no pending pods. Min=2, Max=10.

![FR-2 Scaling Logic](diagrams/screenshots/FR-2-Scaling-Logic.png)

**Implementation:**

Three-layer decision engine in `lambda/scaling_decision.py`:

**Layer 1 — Reactive (CPU/memory/pods):**

| Trigger | Threshold | Window | Window Size |
|---------|-----------|--------|-------------|
| Scale UP — CPU | >70% | 3 consecutive checks | 3×2min = ~6min |
| Scale UP — memory | >75% | 3 consecutive checks | ~6min |
| Scale UP — pending pods | >0 | 2 consecutive checks | 2×2min = ~4min |
| Scale DOWN — CPU | <30% | 5 consecutive checks | 5×2min = ~10min |
| Scale DOWN — memory | <50% | 5 consecutive checks | ~10min |
| Scale DOWN — pending pods | =0 | 5 consecutive checks | required for scale-down |

**Cooldowns** prevent thrashing:
- Scale-up cooldown: 300s (5 min)
- Scale-down cooldown: 600s (10 min)

**Layer 2 — Custom app metrics (instantaneous):**
- Queue depth >1000: scale up
- Latency p95 >2000ms: scale up
- Error rate >5%: scale up

**Layer 3 — Predictive:**
- 7-day lookback from `node-fleet-prod-metrics-history` DynamoDB table (30-day TTL)
- Detects hourly + weekly patterns; predicts next-hour load
- Scales up immediately when predicted next-hour CPU >70%

**Increment logic:**
- Normal scale-up: +1 node
- Urgent (CPU>85% OR pending>5): +2 nodes
- Scale-down: always -1 node (conservative)

**Files:** `lambda/scaling_decision.py`, `lambda/predictive_scaling.py`, `lambda/custom_metrics.py`

---

### FR-3: Node Provisioning
> Launch EC2 with pre-configured K3s agent. Auto-join cluster. Health check: wait for Ready.

![FR-3 Node Provisioning](diagrams/screenshots/FR-3-Node-Provisioning.png)

**Implementation:**

`lambda/ec2_manager.py` → `launch_worker_instance()`:

1. Selects subnet based on multi-AZ distribution (`lambda/multi_az_helper.py`)
2. Picks On-Demand or Spot based on 70/30 ratio (`lambda/spot_instance_helper.py`)
3. `ec2.run_instances()` with Launch Template (pre-baked AMI + userdata)
4. Userdata (`k3s/worker-userdata.sh`) runs at boot:
   - Fetches K3s token from Secrets Manager `node-fleet/k3s-token`
   - Resolves master IP via EC2 tag `Role=k3s-master` (no hardcoded IP)
   - Joins cluster: `curl -sfL https://get.k3s.io | K3S_URL=... K3S_TOKEN=... sh -`
5. Next Lambda invocation (`check_pending_scale_ups`) calls `ec2.describe_instances` — waits for EC2 `running` state **AND** `elapsed >= 120s` (userdata + k3s join time) before clearing from pending list
6. DynamoDB `pending_scale_up` list tracks launched instance IDs — checked in Step 0 each invocation

**Worker joins cluster in ~90–120s** (meets <3 min NFR). 120s guard aligns with 2-min EventBridge cycle — zero added delay in practice.

**Files:** `lambda/ec2_manager.py`, `lambda/multi_az_helper.py`, `lambda/spot_instance_helper.py`, `k3s/worker-userdata.sh`

---

### FR-4: Node Deprovisioning
> Graceful drain before termination. 5-min drain timeout. Never terminate critical pod nodes.

![FR-4 Node Deprovisioning](diagrams/screenshots/FR-4-Node-Deprovisioning.png)

**Implementation:**

`lambda/ec2_manager.py` → `drain_and_terminate_worker()`:

**Step 1 — Safety check** (never terminate if node hosts):
```python
critical_pods = [
    pods in kube-system (CoreDNS, metrics-server) that are NOT DaemonSet,
    StatefulSet pods,
    single-replica Deployment pods
]
```
If critical pods found → skip this node, try next candidate.

**Step 2 — Async drain via SSM:**
```bash
aws ssm send-command \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["kubectl drain <node> --ignore-daemonsets --delete-emptydir-data --timeout=300s"]'
```
Returns SSM Command ID immediately (<5s). Lambda exits. No blocking.

**Step 3 — Next invocation validates drain:**
```python
# BOTH conditions required — exit code alone not enough
if exit_status != 0 or "drained" not in output_string:
    return False  # abort termination
```
Only on success: `ec2.terminate_instances()` + `kubectl delete node`.

**Drain timeout = 300s** (5 min per requirement). Lock expiry = 360s (covers drain + buffer).

**Files:** `lambda/ec2_manager.py`, `lambda/state_manager.py`

---

### FR-5: State Management
> Track active scaling ops to prevent race conditions. Store cluster state in DynamoDB.

![FR-5 State Management](diagrams/screenshots/FR-5-State-Management.png)

**Implementation:**

DynamoDB table `node-fleet-prod-state` (single item per cluster):

```json
{
  "cluster_id": "node-fleet-prod",
  "node_count": 3,
  "last_scale_time": 1706184600,
  "last_scale_action": "scale_up",
  "scaling_in_progress": true,
  "lock_acquired_at": 1706184540,
  "lock_expiry": 1706184900,
  "draining_instances": [
    {
      "instance_id": "i-0abc123",
      "node_name": "ip-10-0-1-50",
      "command_id": "cmd-0xyz789",
      "master_instance_id": "i-0master456",
      "start_time": 1706184540
    }
  ],
  "pending_scale_up": [
    {"instance_id": "i-0new789", "launch_time": 1706184600}
  ],
  "metrics_history": [...last 10 readings...]
}
```

**Distributed lock — atomic conditional write:**
```python
dynamodb.update_item(
    ConditionExpression='attribute_not_exists(scaling_in_progress) OR scaling_in_progress = :false OR lock_acquired_at < :expired'
    # :expired = current_time - 360 (stale lock threshold)
)
# ConditionalCheckFailedException → another Lambda holds lock → exit
```

If two Lambda invocations fire simultaneously, only one acquires lock. Other exits gracefully. Lock released in `finally` block — always executes even on exception.

**Stale lock auto-clear:** if `lock_age > 360s` → force release (covers crashed Lambda).

**Files:** `lambda/state_manager.py`, `lambda/autoscaler.py`

---

## Non-Functional Requirements

### NFR-1: Performance — Scaling Decision <3 Minutes

![NFR-1 Performance](diagrams/screenshots/NFR-1-Performance.png)

**How:** EventBridge fires Lambda every 2 min. Lambda query Prometheus + decision + EC2 launch = ~1.4s. New worker Ready in ~90–120s. Total: **~92–122s** (<3 min ✅).

### NFR-2: Reliability — Handle Lambda Failures Gracefully

![NFR-2 Reliability](diagrams/screenshots/NFR-2-Reliability.png)

**How:**
- DLQ (SQS Dead Letter Queue) catches failed Lambda invocations
- `finally` block always releases DynamoDB lock — no stuck locks from crashes
- Drain state persists in DynamoDB — next invocation continues where previous left off
- Spot interruption EventBridge rule triggers Lambda to drain interrupted instance immediately

**Files:** `lambda/autoscaler.py` (finally block), `pulumi/src/lambda.ts` (DLQ config)

### NFR-3: Cost — Lambda Execution <30 Seconds

![NFR-3 Cost](diagrams/screenshots/NFR-3-Cost.png)

**How:** Async design keeps Lambda under 5s normally:
- Drain is async (SSM send-command returns in <5s)
- Scale-up launch returns in <3s (EC2 RunInstances is async)
- Heavy operations (waiting for node Ready) done across invocations, not in single execution
- Actual measured: **~1.4s avg** (<<30s ✅)

### NFR-4: Security — IAM Roles, No Hardcoded Credentials

![NFR-4 Security](diagrams/screenshots/NFR-4-Security.png)

**How:**
- All credentials in Secrets Manager — fetched at runtime, never in code/env vars
- Lambda role: least-privilege (only required EC2/DDB/SSM/CW actions on specific resources)
- Master role: read-only EC2 + Secrets Manager (k3s-token, prometheus-auth, grafana-password) + CloudWatch read
- Worker role: read-only EC2 + k3s-token secret + CloudWatch read (for Grafana dashboards)
- Prometheus: basic auth with bcrypt hash, credentials from Secrets Manager
- SSH key stored in Secrets Manager `node-fleet/ssh-key`

**Files:** `pulumi/src/iam.ts`, `lambda/autoscaler.py` (Secrets Manager fetches)

### NFR-5: Observability — Log All Scaling Events to CloudWatch

![NFR-5 Observability](diagrams/screenshots/NFR-5-Observability.png)

**How:**
- Lambda logs to `/aws/lambda/node-fleet-prod-autoscaler` (30-day retention)
- 9 custom CloudWatch metrics in `NodeFleet/Autoscaler` namespace:
  - `ClusterCPUUtilization`, `ClusterMemoryUtilization`, `CurrentNodeCount`
  - `ScaleUpEvents`, `ScaleDownEvents`, `ScalingFailures`
  - `PendingPods`, `NodeJoinLatency`, `AutoscalerInvocations`
- 8 CloudWatch Alarms: CPU overload, high memory, pending pods, at-max-capacity, node-join-failure, scaling-failures, Lambda errors, Lambda timeout
- Grafana dashboards visualize all metrics in real time

**Files:** `lambda/custom_metrics.py`, `pulumi/src/cloudwatch-alarms.ts`

---

## Bonus Challenges

### Bonus-1: Multi-AZ Worker Distribution

![BONUS-1 Multi-AZ](diagrams/screenshots/BONUS-1-Multi-AZ-Distribution.png)

**How:** Workers spread across `ap-southeast-1a` and `ap-southeast-1b` private subnets. `lambda/multi_az_helper.py` (`select_subnet_for_new_instance`) counts workers per subnet and always launches in the subnet with the fewest existing workers.

On scale-down: `_select_instances_for_termination` prefers Spot instances first (lower cost, already interruptible), then newest-first as tie-breaker. AZ rebalancing is achieved passively via scale-up placement logic.

**Files:** `lambda/multi_az_helper.py`, `pulumi/src/ec2-worker.ts` (private subnets)

---

### Bonus-2: Spot Instances + Interruption Handling + On-Demand Fallback

![BONUS-2 Spot Instances](diagrams/screenshots/BONUS-2-Spot-Instances.png)

**How:** `lambda/spot_instance_helper.py` maintains 70% Spot / 30% On-Demand mix.

- Spot launch attempted first via `workerSpotTemplate`
- On `SpotMaxPriceTooLow` or capacity error: falls back to On-Demand `workerTemplate`
- Spot interruption rule in EventBridge: fires Lambda 2 min before interruption
- Lambda drains interrupted instance immediately (doesn't wait for normal cycle)

**Files:** `lambda/spot_instance_helper.py`, `pulumi/src/lambda.ts` (spot-interruption EventBridge rule)

---

### Bonus-3: Predictive Scaling

![BONUS-3 Predictive Scaling](diagrams/screenshots/BONUS-3-Predictive-Scaling.png)

**How:** `lambda/predictive_scaling.py` stores every metric reading in `node-fleet-prod-metrics-history` DynamoDB table (30-day TTL, 7-day lookback for pattern analysis). Detects hourly and weekly patterns from historical data. When predicted next-hour load exceeds 70% CPU, proactively scales up ahead of the spike.

Requires 20+ stored data points to activate (at 2-min intervals ≈ 40 min of history). Until then, falls back to reactive only.

**Files:** `lambda/predictive_scaling.py`

---

### Bonus-4: Custom App Metrics

![BONUS-4 Custom App Metrics](diagrams/screenshots/BONUS-4-Custom-App-Metrics.png)

**How:** `lambda/custom_metrics.py` queries demo-app `/metrics` endpoint for:
- Queue depth (`app_queue_depth`)
- Request latency p95 (`http_request_duration_seconds_bucket`)
- Error rate (`http_requests_total` with 5xx status)
- Active connections

These feed into scaling decisions as Layer 2 (runs after reactive, before predictive).

**Files:** `lambda/custom_metrics.py`

---

### Bonus-5: GitOps with FluxCD

![BONUS-5 GitOps](diagrams/screenshots/BONUS-5-GitOps-FluxCD.png)

**How:** FluxCD installed on master node. GitRepository source polls GitHub every 1 min (`interval: 1m`). Any push to `gitops/` auto-applies K8s manifests within 1 min.

```
gitops/
├── infrastructure/   → Prometheus, Grafana, node-exporter, kube-state-metrics
├── apps/             → demo-app deployment
└── monitoring/       → alerts, dashboards ConfigMaps
```

Scale-down drains remove pods gracefully before termination — FluxCD reconciles remaining pods to healthy nodes automatically.

**Files:** `gitops/install-flux.sh`, `gitops/infrastructure/`, `gitops/apps/`

---

### Bonus-6: Slack Notifications

![BONUS-6 Slack](diagrams/screenshots/BONUS-6-Slack-Notifications.png)

**How:** `lambda/slack_notifier.py` sends via SNS → Lambda subscription → Slack webhook (stored in Secrets Manager `node-fleet/slack-webhook`).

Notifications sent for:
- Scale-up: node count before/after, reason, new instance IDs
- Scale-down: node drained, reason
- Failures: Lambda errors, drain failures
- Lock contention warnings

**Files:** `lambda/slack_notifier.py`, `pulumi/src/lambda.ts` (SNS topic + subscription)

---

## Flow Diagrams

See `docs/diagrams/` folder — one Excalidraw file per requirement:

| File | Contents |
|------|----------|
| `00-System-Overview.excalidraw` | Full system architecture with all AWS services |
| `00-Lambda-7-Step-Flow.excalidraw` | Lambda orchestration 7-step flow |
| `FR-1-Metric-Collection.excalidraw` | Prometheus scrape flow + PromQL queries |
| `FR-2-Scaling-Logic.excalidraw` | 3-layer scaling decision algorithm |
| `FR-3-Node-Provisioning.excalidraw` | Scale-up sequence: trigger → EC2 launch → node join |
| `FR-4-Node-Deprovisioning.excalidraw` | Scale-down sequence: drain → validate → terminate |
| `FR-5-State-Management.excalidraw` | DynamoDB lock + state schema |
| `NFR-1-Performance.excalidraw` | End-to-end latency breakdown |
| `NFR-2-Reliability.excalidraw` | Failure handling / DLQ / finally block |
| `NFR-3-Cost.excalidraw` | Lambda async design / cost breakdown |
| `NFR-4-Security.excalidraw` | IAM roles + Secrets Manager paths |
| `NFR-5-Observability.excalidraw` | CloudWatch metrics + alarms + Grafana |
| `BONUS-1-Multi-AZ-Distribution.excalidraw` | AZ-aware launch + drain rebalancing |
| `BONUS-2-Spot-Instances.excalidraw` | Spot/OD mix + interruption handling |
| `BONUS-3-Predictive-Scaling.excalidraw` | 7-day history → pre-scale logic |
| `BONUS-4-Custom-App-Metrics.excalidraw` | Queue/latency/error rate → Layer 2 |
| `BONUS-5-GitOps-FluxCD.excalidraw` | FluxCD reconcile flow |
| `BONUS-6-Slack-Notifications.excalidraw` | SNS → Slack notification flow |
