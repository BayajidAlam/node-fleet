# Requirements Coverage — node-fleet K3s Autoscaler

How every functional, non-functional, and bonus requirement is implemented.

---

## Functional Requirements

### FR-1: Metric Collection
> Prometheus scrapes CPU, memory, pod count from all K3s nodes. Collect custom app metrics (pending pods, API latency).

**Implementation:**

Prometheus deployed as pod on master node with `kubernetes_sd_configs` discovering all nodes automatically. node-exporter DaemonSet runs on every worker exposing hardware metrics.

| Metric | PromQL | Source |
|--------|--------|--------|
| CPU % | `avg by(instance)(rate(node_cpu_seconds_total{mode!="idle",job="kubernetes-pods"}[5m])) * 100` | node-exporter |
| Memory % | `(1 - node_memory_MemAvailable_bytes{job="kubernetes-pods"} / node_memory_MemTotal_bytes{job="kubernetes-pods"}) * 100` | node-exporter |
| Pending pods | `sum(kube_pod_status_phase{phase="Pending"})` | kube-state-metrics |
| Node count | `count(kube_node_info)` | kube-state-metrics |
| Queue depth | `app_queue_depth{queue="default"}` | demo-app /metrics |
| Latency p95 | `histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m])) * 1000` | demo-app /metrics |
| Error rate | `sum(rate(http_requests_total{status=~"5.."}[2m])) / sum(rate(http_requests_total[2m])) * 100` | demo-app /metrics |

**Files:** `lambda/metrics_collector.py`, `lambda/custom_metrics.py`, `monitoring/prometheus/prometheus-config.yaml`

---

### FR-2: Scaling Logic
> Scale UP: CPU >70% OR pending pods >3 min. Scale DOWN: CPU <30% for >10 min AND no pending pods. Min=2, Max=10.

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

**Layer 2 — Custom app metrics:**
- Queue depth >100: scale up
- Latency p95 >2000ms for 2 checks: scale up
- Error rate >5% for 2 checks: scale up

**Layer 3 — Predictive:**
- 7-day historical data in `node-fleet-prod-metrics-history` DynamoDB table
- Detects time-of-day patterns (peak hours 9AM-9PM)
- Pre-scales 10 min before predicted spike

**Increment logic:**
- Normal scale-up: +1 node
- Urgent (CPU>85% OR pending>5): +2 nodes
- Scale-down: always -1 node (conservative)

**Files:** `lambda/scaling_decision.py`, `lambda/predictive_scaling.py`, `lambda/custom_metrics.py`

---

### FR-3: Node Provisioning
> Launch EC2 with pre-configured K3s agent. Auto-join cluster. Health check: wait for Ready.

**Implementation:**

`lambda/ec2_manager.py` → `launch_worker_instance()`:

1. Selects subnet based on multi-AZ distribution (`lambda/multi_az_helper.py`)
2. Picks On-Demand or Spot based on 70/30 ratio (`lambda/spot_instance_helper.py`)
3. `ec2.run_instances()` with Launch Template (pre-baked AMI + userdata)
4. Userdata (`k3s/worker-userdata.sh`) runs at boot:
   - Fetches K3s token from Secrets Manager `node-fleet/k3s-token`
   - Resolves master IP via EC2 tag `Role=k3s-master` (no hardcoded IP)
   - Joins cluster: `curl -sfL https://get.k3s.io | K3S_URL=... K3S_TOKEN=... sh -`
5. Next Lambda invocation checks `kubectl get node <id>` — waits for `Ready` before marking scale-up complete
6. DynamoDB stores `pending_scale_ups` list — checked in Step 0 each invocation

**Worker joins cluster in ~90–120s** (meets <3 min NFR).

**Files:** `lambda/ec2_manager.py`, `lambda/multi_az_helper.py`, `lambda/spot_instance_helper.py`, `k3s/worker-userdata.sh`

---

### FR-4: Node Deprovisioning
> Graceful drain before termination. 5-min drain timeout. Never terminate critical pod nodes.

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

**Implementation:**

DynamoDB table `node-fleet-prod-state` (single item per cluster):

```json
{
  "cluster_id": "node-fleet-prod",
  "node_count": 3,
  "last_scale_time": 1706184600,
  "last_scale_action": "scale_up",
  "scaling_in_progress": "true",
  "lock_acquired_at": 1706184540,
  "lock_expiry": 1706184900,
  "draining_instances": ["i-0abc123:cmd-0xyz789"],
  "metrics_history": [...last 10 readings...]
}
```

**Distributed lock — atomic conditional write:**
```python
dynamodb.update_item(
    ConditionExpression='attribute_not_exists(scaling_in_progress) OR lock_expiry < :now'
)
# ConditionalCheckFailedException → another Lambda holds lock → exit
```

If two Lambda invocations fire simultaneously, only one acquires lock. Other exits gracefully. Lock released in `finally` block — always executes even on exception.

**Stale lock auto-clear:** if `lock_age > 360s` → force release (covers crashed Lambda).

**Files:** `lambda/state_manager.py`, `lambda/autoscaler.py`

---

## Non-Functional Requirements

### NFR-1: Performance — Scaling Decision <3 Minutes
**How:** EventBridge fires Lambda every 2 min. Lambda query Prometheus + decision + EC2 launch = ~1.4s. New worker Ready in ~90–120s. Total: **~92–122s** (<3 min ✅).

### NFR-2: Reliability — Handle Lambda Failures Gracefully
**How:**
- DLQ (SQS Dead Letter Queue) catches failed Lambda invocations
- `finally` block always releases DynamoDB lock — no stuck locks from crashes
- Drain state persists in DynamoDB — next invocation continues where previous left off
- Spot interruption EventBridge rule triggers Lambda to drain interrupted instance immediately

**Files:** `lambda/autoscaler.py` (finally block), `pulumi/src/lambda.ts` (DLQ config)

### NFR-3: Cost — Lambda Execution <30 Seconds
**How:** Async design keeps Lambda under 5s normally:
- Drain is async (SSM send-command returns in <5s)
- Scale-up launch returns in <3s (EC2 RunInstances is async)
- Heavy operations (waiting for node Ready) done across invocations, not in single execution
- Actual measured: **~1.4s avg** (<<30s ✅)

### NFR-4: Security — IAM Roles, No Hardcoded Credentials
**How:**
- All credentials in Secrets Manager — fetched at runtime, never in code/env vars
- Lambda role: least-privilege (only required EC2/DDB/SSM/CW actions on specific resources)
- Master role: read-only EC2 + specific Secrets Manager paths + CloudWatch read
- Worker role: read-only EC2 + k3s-token secret only
- Prometheus: basic auth with bcrypt hash, credentials from Secrets Manager
- SSH key stored in Secrets Manager `node-fleet/ssh-key`

**Files:** `pulumi/src/iam.ts`, `lambda/autoscaler.py` (Secrets Manager fetches)

### NFR-5: Observability — Log All Scaling Events to CloudWatch
**How:**
- Lambda logs to `/aws/lambda/node-fleet-prod-autoscaler` (30-day retention)
- 10 custom CloudWatch metrics in `NodeFleet/Autoscaler` namespace:
  - `ClusterCPUUtilization`, `ClusterMemoryUtilization`, `CurrentNodeCount`
  - `ScaleUpEvents`, `ScaleDownEvents`, `ScalingFailures`
  - `PendingPods`, `NodeJoinLatency`, `AutoscalerInvocations`
- 8 CloudWatch Alarms: CPU overload, high memory, pending pods, at-max-capacity, node-join-failure, scaling-failures, Lambda errors, Lambda timeout
- Grafana dashboards visualize all metrics in real time

**Files:** `lambda/custom_metrics.py`, `pulumi/src/cloudwatch-alarms.ts`

---

## Bonus Challenges

### Bonus-1: Multi-AZ Worker Distribution
**How:** Workers spread across `ap-southeast-1a` and `ap-southeast-1b` private subnets. `lambda/multi_az_helper.py` tracks per-AZ counts and always launches in the zone with fewer workers.

On scale-down: drains from the zone with most workers first (rebalancing).

**Files:** `lambda/multi_az_helper.py`, `pulumi/src/ec2-worker.ts` (private subnets)

---

### Bonus-2: Spot Instances + Interruption Handling + On-Demand Fallback
**How:** `lambda/spot_instance_helper.py` maintains 70% Spot / 30% On-Demand mix.

- Spot launch attempted first via `workerSpotTemplate`
- On `SpotMaxPriceTooLow` or capacity error: falls back to On-Demand `workerTemplate`
- Spot interruption rule in EventBridge: fires Lambda 2 min before interruption
- Lambda drains interrupted instance immediately (doesn't wait for normal cycle)

**Files:** `lambda/spot_instance_helper.py`, `pulumi/src/lambda.ts` (spot-interruption EventBridge rule)

---

### Bonus-3: Predictive Scaling
**How:** `lambda/predictive_scaling.py` stores every metric reading in `node-fleet-prod-metrics-history` DynamoDB table (7-day retention). Detects weekly patterns by comparing current time-of-day to historical 7-day average. Pre-scales 10 min before predicted peak.

Requires 7+ days of history to activate. Until then, falls back to reactive only.

**Files:** `lambda/predictive_scaling.py`

---

### Bonus-4: Custom App Metrics
**How:** `lambda/custom_metrics.py` queries demo-app `/metrics` endpoint for:
- Queue depth (`app_queue_depth`)
- Request latency p95 (`http_request_duration_seconds_bucket`)
- Error rate (`http_requests_total` with 5xx status)
- Active connections

These feed into scaling decisions as Layer 2 (runs after reactive, before predictive).

**Files:** `lambda/custom_metrics.py`

---

### Bonus-5: GitOps with FluxCD
**How:** FluxCD installed on master node watches this GitHub repo. Any push to `gitops/` folder auto-applies K8s manifests within 1 minute.

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
**How:** `lambda/slack_notifier.py` sends via SNS → Lambda subscription → Slack webhook (stored in Secrets Manager `node-fleet/slack-webhook`).

Notifications sent for:
- Scale-up: node count before/after, reason, new instance IDs
- Scale-down: node drained, reason
- Failures: Lambda errors, drain failures
- Lock contention warnings

**Files:** `lambda/slack_notifier.py`, `pulumi/src/lambda.ts` (SNS topic + subscription)

---

## Flow Diagrams

See `solution/diagrams/` folder:

| File | Contents |
|------|----------|
| `system-overview.excalidraw` | Full system architecture with all AWS services |
| `lambda-7-step-flow.excalidraw` | Lambda orchestration 7-step flow |
| `scaling-decision.excalidraw` | 3-layer scaling decision algorithm |
| `scale-up-sequence.excalidraw` | Scale-up sequence: trigger → EC2 launch → node join |
| `scale-down-sequence.excalidraw` | Scale-down sequence: drain → validate → terminate |
