# node-fleet — Architecture

> **Cluster topology**: 1 fixed master (t3.medium) + 2–10 autoscaled workers (t3.small). All node counts in this doc refer to **workers** unless stated.

**Diagrams:** [System Overview](diagrams/screenshots/00-System-Overview.png) · [Lambda 7-Step Flow](diagrams/screenshots/00-Lambda-7-Step-Flow.png) · [Scaling Logic](diagrams/screenshots/FR-2-Scaling-Logic.png) · [Multi-AZ](diagrams/screenshots/BONUS-1-Multi-AZ-Distribution.png)

**Requirements Coverage:** [`REQUIREMENTS_COVERAGE.md`](REQUIREMENTS_COVERAGE.md) — every FR/NFR/Bonus verified against implementation.

---

## 1. System Overview

node-fleet solves a real cost problem: a static worker fleet (baseline assumption: 5 workers running 24/7) wastes 60,000 BDT/month during off-peak hours (9PM–9AM, CPU 20–30%) and takes 15–20 minutes to scale manually during flash sales. The autoscaler replaces the static fleet with a dynamic 2–10 worker pool and eliminates both problems.

**How it works**: An EventBridge rule fires a Python Lambda every 2 minutes. Lambda queries Prometheus for real-time cluster metrics, runs a three-layer decision engine (reactive thresholds + predictive patterns + custom app metrics), and executes the scaling action. New workers launch via EC2 RunInstances and auto-join the K3s cluster through a userdata script. Scale-down uses an async SSM drain pattern to safely evict workloads before termination.

### Architecture Principles

| Principle | Implementation |
|-----------|---------------|
| **Serverless-First** | Lambda for orchestration — $0.40/month vs $7+/month for EC2 cron |
| **Event-Driven** | EventBridge triggers; SSM async drain; EventBridge for Spot interruptions |
| **State-Isolated** | DynamoDB conditional writes prevent split-brain between concurrent Lambdas |
| **Observable** | Prometheus + Grafana + CloudWatch + SNS covers all observability layers |
| **Secure by Default** | No hardcoded credentials; Secrets Manager for all secrets; least-privilege IAM |
| **Gradual and Safe** | Scale-down drains workloads first; critical pod protection prevents disruptions |

### Key Design Decisions

| Decision | Chosen | Alternative Rejected | Why |
|----------|--------|---------------------|-----|
| K8s distribution | K3s | EKS | 50% lower resource overhead; free control plane; same kubectl |
| Autoscaler runtime | AWS Lambda | EC2 cron | $0.40/month; no patching; auto-scales; event-driven |
| State backend | DynamoDB | RDS | Serverless pricing; atomic conditional writes for locking |
| IaC | Pulumi TypeScript | Terraform | Type safety; real language; better test integration |
| Metrics | Prometheus | CloudWatch | K8s-native; PromQL expressiveness; free; 7-day history |
| Scheduling | EventBridge | Lambda cron | Native AWS; clean rate expressions; easy to disable |
| Prometheus access | NodePort 30090 | LoadBalancer | Saves ~$16/month ELB cost; Lambda has direct VPC access |
| Drain mechanism | SSM async | SSH | No bastion host; Lambda can't SSH; SSM works without open SSH port |
| Secret storage | Secrets Manager | S3 | Secrets Manager: encrypted at rest + in transit, per-secret IAM scoping, automatic rotation support, audit via CloudTrail — S3 requires separate KMS + bucket policy management with higher misconfiguration risk |

---

## 2. Component Architecture

### AWS Infrastructure

![System Overview](diagrams/screenshots/00-System-Overview.png)

### Component Roles

| Component | Role | Connection to others |
|-----------|------|---------------------|
| **EventBridge** | Fires Lambda every 2 min | → Lambda invocation |
| **Lambda** | 7-step autoscaler orchestrator | ↔ DynamoDB, → Prometheus, → EC2, → SSM, → CloudWatch, → SNS |
| **Prometheus** | Metrics aggregation | ← node-exporter, ← kube-state-metrics, ← demo-app; → Lambda (PromQL) |
| **DynamoDB** | Distributed lock + state + drain tracking | ↔ Lambda |
| **Secrets Manager** | K3s token, Prometheus auth, Slack webhook | ← Lambda, ← worker userdata |
| **EC2 Launch Template** | Reproducible worker config (t3.small, 20GB gp3, Spot) | ← Lambda RunInstances |
| **K3s** | Container orchestration | Workers auto-join via token from Secrets Manager |
| **SSM** | Remote kubectl commands for drain | ← Lambda (async), → master node |
| **CloudWatch** | 9 custom metrics + 8 alarms + 30d logs | ← Lambda |
| **SNS** | Alert routing | ← Lambda, → Slack webhook Lambda |
| **Grafana** | 4 dashboards | ← Prometheus, ← CloudWatch |
| **FluxCD** | GitOps — auto-apply K8s manifests | ← Git repo (main branch) |
| **ECR** | Container registry for demo app | ← worker IAM pull permissions |
| **SQS (DLQ)** | Dead-letter queue — captures Lambda invocation failures | ← Lambda (on failure); IAM: `sqs:SendMessage` scoped to DLQ ARN |
| **Dynamic Scheduler** | Adjusts EventBridge rate (1–5 min) based on time-of-day and load | ← `lambda/dynamic_scheduler.py`; → EventBridge `put-rule` API |

### Lambda 7-Step Orchestration

Every EventBridge invocation runs these steps in order. Lock released in `finally` — always, even on crash.

| Step | Action | Detail |
|------|--------|--------|
| **1** | Check pending drains | Query DynamoDB `draining_instances`; if SSM drain complete (exit 0 + "drained" in output) → terminate + `kubectl delete node` |
| **2** | Acquire DynamoDB lock | Conditional write: `attribute_not_exists(scaling_in_progress) OR lock_expiry < now`; if fails → exit (other Lambda running) |
| **3** | Fetch metrics | PromQL to Prometheus NodePort :30090 (basic auth from Secrets Manager): CPU, memory, pending pods, node count |
| **4** | Scaling decision | `scaling_decision.py`: evaluate thresholds against `metrics_history` windows + cooldowns + predictive layer |
| **5** | Execute action | Scale-up → EC2 `RunInstances` + poll Ready; Scale-down → SSM `kubectl drain` (async, returns in <5s) |
| **6** | Update state | DynamoDB (node_count, last_scale_action, draining_instances); CloudWatch custom metrics; SNS → Slack |
| **7** | Release lock | Always in `finally` block; deletes `scaling_in_progress` attribute |

---

## 3. Network Topology

![Network Topology — VPC, Subnets, Security Groups](diagrams/network-topology.png)

![Multi-AZ Worker Distribution](diagrams/screenshots/BONUS-1-Multi-AZ-Distribution.png)

### VPC Design

**CIDR**: `10.0.0.0/16` (65,536 IPs)  
**Region**: `ap-southeast-1` (Singapore)

| Subnet | CIDR | AZ | Hosts | Route Table |
|--------|------|----|-------|-------------|
| Public-1a | 10.0.1.0/24 | ap-southeast-1a | **K3s Master** (10.0.1.176) + NAT Gateway | 0.0.0.0/0 → IGW |
| Public-1b | 10.0.2.0/24 | ap-southeast-1b | (reserved for future AZ expansion) | 0.0.0.0/0 → IGW |
| Private-1a | 10.0.11.0/24 | ap-southeast-1a | Worker-1 (10.0.11.104) | 0.0.0.0/0 → NAT (public-1a) |
| Private-1b | 10.0.12.0/24 | ap-southeast-1b | Worker-2 (10.0.12.24) | 0.0.0.0/0 → NAT (public-1a) |

> **Single NAT Gateway**: Both private subnets route through the NAT in public-1a. This is a cost trade-off (~$32/month per NAT). If AZ-1a fails, workers in private-1b lose outbound internet until the NAT recovers. For full HA, add a second NAT in public-1b and a separate private route table for private-1b.

**Why private subnets for workers?** Workers have no public IPs. All outbound internet traffic (ECR pulls, Secrets Manager, package downloads) goes through NAT Gateways. This reduces attack surface and prevents direct internet access to K3s API.

### Security Groups

**sg-master**

| Direction | Protocol | Port | Source | Purpose |
|-----------|----------|------|--------|---------|
| Inbound | TCP | 6443 | 10.0.0.0/16 | K3s API server (workers + Lambda in VPC) |
| Inbound | TCP | 30090 | 10.0.0.0/16 | Prometheus NodePort (Lambda queries via VPC) |
| Inbound | TCP | 30300 | 0.0.0.0/0 | Grafana UI |
| Inbound | TCP | 30080 | 0.0.0.0/0 | Demo App NodePort |
| Inbound | TCP | 22 | 10.0.0.0/16 | SSH (VPC only — use bastion or VPN) |
| Inbound | UDP | 8472 | 10.0.0.0/16 | Flannel VXLAN (pod-to-pod across nodes) |
| Inbound | TCP | 10250 | 10.0.0.0/16 | Kubelet |
| Inbound | TCP | 9100 | 10.0.0.0/16 | Node Exporter |
| Outbound | ALL | ALL | 0.0.0.0/0 | Internet |

**sg-worker**

| Direction | Protocol | Port | Source | Purpose |
|-----------|----------|------|--------|---------|
| Inbound | TCP | 22 | 10.0.0.0/16 | SSH (VPC only) |
| Inbound | UDP | 8472 | 10.0.0.0/16 | Flannel VXLAN (pod-to-pod) |
| Inbound | TCP | 10250 | 10.0.0.0/16 | Kubelet |
| Inbound | TCP | 9100 | 10.0.0.0/16 | Node Exporter |
| Inbound | TCP | 30000-32767 | 10.0.0.0/16 | NodePort services (VPC internal) |
| Outbound | ALL | ALL | 0.0.0.0/0 | Internet via NAT |

**sg-lambda**

| Direction | Protocol | Port | Destination | Purpose |
|-----------|----------|------|-------------|---------|
| Outbound | ALL | ALL | 0.0.0.0/0 | AWS APIs + Prometheus (unrestricted egress) |

---

## 4. Data Flow Sequences

![Lambda 7-Step Orchestration Flow](diagrams/screenshots/00-Lambda-7-Step-Flow.png)

### Scale-Up Flow (Decision to Capacity)

```
① EventBridge fires Lambda (rate: 2 min)
     ↓
② Lambda queries DynamoDB: check draining_instances from prior run
     → If drain completed: terminate instance + kubectl delete node
     ↓
③ Lambda acquires DynamoDB lock
     ConditExpression: attribute_not_exists(scaling_in_progress) OR lock_expiry < now
     → If locked: exit gracefully (concurrent invocation)
     ↓
④ Lambda queries Prometheus (HTTP GET :30090/api/v1/query, basic auth)
     Returns: cpu=74.3%, memory=68.1%, pending_pods=3, node_count=3
     ↓
⑤ Decision engine: CPU 74.3% for 3rd consecutive reading → scale_up
     pending_pods=3 for 2nd consecutive reading → scale_up confirmed
     +1 node (CPU<85% AND pending<5 — standard increment, not urgent)
     ↓
⑥ EC2 RunInstances (×1)
     Launch Template: t3.small, 20GB gp3 encrypted, Spot (70%)
     AZ distribution: 1× ap-southeast-1a (least workers)
     Userdata: fetch K3s token from Secrets Manager → k3s agent join
     ↓
⑦ Lambda stores pending instance ID in DynamoDB: pending_scale_ups=["i-0abc123"]
   Lock released (finally block) — Lambda exits (~28s total)
     ↓
   [Worker boots async: ~60-90s]
     sudo -E K3S_URL=https://<master-private-ip>:6443 \
           K3S_TOKEN=<token> \
           sh -s - agent
     → Node appears in kubectl get nodes
     ↓
⑧ Invocation N+1 (2 minutes later): Step 1 checks pending_scale_ups
     EC2 describe: instance Running AND elapsed >= 120s (userdata + K8s join) → confirmed (~130s)
     If not confirmed after 10min (600s) → abandon + alert
     ↓
⑨ DynamoDB: node_count=4, last_scale_time=now, last_scale_action=scale_up
   CloudWatch: ScaleUpEvents+1, CurrentNodeCount=4, NodeJoinLatency=130000ms
   SNS → Slack: "🟢 Scale-Up: CPU 74.3%, +1 node, now 4 total"
     ↓
⑩ Lock released (finally block)
```

### Scale-Down Flow (Async SSM Pattern)

**Why async?** `kubectl drain` can take up to 300 seconds. Lambda timeout is 30s (NFR-3). Initiating drain via SSM (which returns in <5s) and checking result on the next invocation keeps Lambda well within timeout.

**Invocation N:**
```
① No pending drains (DDB draining_instances = empty)
② Lock acquired
③ Metrics: CPU=22%, memory=41%, pending=0 (5th consecutive low reading)
④ Decision: scale_down
⑤ Select drain candidate:
     - Exclude: nodes with StatefulSet pods
     - Exclude: nodes with kube-system non-DaemonSet pods  
     - Exclude: nodes with single-replica Deployment pods
     - Choose: AZ with most workers (balance Multi-AZ)
     - Tiebreak: fewest running pods (minimize disruption)
     → Selected: "ip-10-0-12-47" (3 pods, AZ-1b, no critical pods)
⑥ SSM send-command on master:
     kubectl drain ip-10-0-12-47 --ignore-daemonsets \
       --delete-emptydir-data --timeout=5m
     Returns immediately with command_id="cmd-0xyz789"
⑦ DynamoDB: draining_instances=["i-0abc999:cmd-0xyz789"]  (instance_id:command_id)
⑧ Lock released
```

**Invocation N+1 (2 minutes later):**
```
① Pending drain found: i-0abc999 (command cmd-0xyz789)
② SSM GetCommandInvocation → Status: Success, ExitCode: 0
③ Validate: exit_status==0 AND "drained" in output → ✅ both true
④ EC2 TerminateInstances: i-0abc999
⑤ SSM on master: kubectl delete node ip-10-0-12-47
⑥ DynamoDB: node_count=4, draining_instances=[], last_scale_action=scale_down
⑦ Slack: "🔵 Scale-Down: CPU 22%, removed 1 node, now 4 total"
⑧ Lock released
```

---

## 5. State Management

### DynamoDB Schema

```json
{
  "cluster_id":          "node-fleet-prod",     // PK — partition key
  "node_count":          4,                      // current worker count
  "last_scale_time":     1706184600,             // Unix timestamp
  "last_scale_action":   "scale_up",             // "scale_up" | "scale_down"
  "last_scale_reason":   "CPU 74.3% [3/3]",      // human-readable
  "scaling_in_progress": "true",                 // present=locked, absent=free
  "lock_acquired_at":    1706184540,             // Unix timestamp
  "lock_expiry":         1706184900,             // acquired_at + 360
  "draining_instances":  ["i-0abc999"],          // async drain tracking
  "metrics_history":     [                       // last 7 readings for window evaluation
    {"ts": 1706184540, "cpu": 74.3, "mem": 68.1, "pending": 3},
    {"ts": 1706184420, "cpu": 71.2, "mem": 66.4, "pending": 2}
  ],
  "ttl":                 1706271000              // DynamoDB auto-delete +24h
}
```

### Lock State Machine

```
FREE STATE                  LOCKED STATE
(no scaling_in_progress)    (scaling_in_progress = "true")

Lambda A arrives:           Lambda B arrives (concurrent):
  ConditionExpression         ConditionCheckFailedException
  → attribute_not_exists        → exit gracefully
  → SUCCESS                       (no retry)
  → begin scaling

Lambda A crashes:           Next Lambda arrives after 360s:
  lock_expiry = acquired+360   lock_age > 360 → force release
  → next Lambda clears it      → fresh acquire → proceed
```

### Async Drain State

`draining_instances` list in DynamoDB tracks instances mid-drain across invocations:

```python
# Invocation N: store drain state
state_manager.store_drain_state(instance_id, ssm_command_id)
# → DDB: draining_instances = ["i-0abc999:cmd-0xyz789"]

# Invocation N+1: check and complete
for instance_id, command_id in state_manager.get_pending_drains():
    result = ssm.get_command_invocation(InstanceId=instance_id, CommandId=command_id)
    if result['StatusDetails'] == 'Success' and 'drained' in result['StandardOutputContent']:
        ec2.terminate_instances(InstanceIds=[instance_id])
        state_manager.clear_drain_instance(instance_id)
```

---

## 6. Prometheus Configuration

### prometheus.yml

```yaml
global:
  scrape_interval: 15s        # Lambda polls every 2min, so 15s scrape is sufficient
  evaluation_interval: 15s
  external_labels:
    cluster: "node-fleet-prod"

scrape_configs:
  # System metrics: CPU, memory, disk I/O, network
  - job_name: "node-exporter"
    static_configs:            # static preferred over kubernetes_sd (no RBAC needed)
      - targets:
          - "10.0.11.10:9100"   # example — actual IPs updated on worker launch/terminate
          - "10.0.12.10:9100"   # Lambda updates prometheus.yml via SSM on each scale event
    relabel_configs:
      - source_labels: [__address__]
        regex: '([^:]+):.*'
        target_label: node
        replacement: '$1'

  # Kubernetes object metrics: pod phases, node info, resource requests
  - job_name: "kube-state-metrics"
    static_configs:
      - targets: ["kube-state-metrics.monitoring.svc.cluster.local:8080"]

  # Application metrics: queue depth, request latency, error rate
  - job_name: "demo-app"
    kubernetes_sd_configs:
      - role: pod
    relabel_configs:
      - source_labels: [__meta_kubernetes_pod_label_app]
        regex: demo-app
        action: keep
```

**Why `static_configs` not `kubernetes_sd_configs` for node-exporter?**  
`kubernetes_sd_configs` requires a ClusterRole that can `list` nodes. Without explicit RBAC, all targets show `0/0 (no data)`. Static configs with known private IPs are simpler and more reliable for a fixed-size infrastructure.

### Storage & Access

```bash
# Prometheus startup flags
--storage.tsdb.path=/var/lib/prometheus
--storage.tsdb.retention.time=7d      # 7 days needed for predictive scaling
--web.config.file=/etc/prometheus/web.yml    # basic auth
--web.listen-address=0.0.0.0:9090

# NodePort service exposes on :30090
# Credentials from Secrets Manager: node-fleet/prometheus-auth
```

### PromQL Query Reference

| Use Case | Query |
|----------|-------|
| CPU utilization | `avg(rate(node_cpu_seconds_total{mode!="idle"}[5m])) * 100` |
| Memory utilization | `(1 - avg(node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100` |
| Pending pods | `sum(kube_pod_status_phase{phase="Pending"})` |
| Worker node count | `count(kube_node_info) - 1` (subtract master) |
| Network receive MB/s | `sum(rate(node_network_receive_bytes_total{device!~"lo\|veth.*"}[5m])) / 1024 / 1024` |
| Network transmit MB/s | `sum(rate(node_network_transmit_bytes_total{device!~"lo\|veth.*"}[5m])) / 1024 / 1024` |
| Disk read MB/s | `sum(rate(node_disk_read_bytes_total[5m])) / 1024 / 1024` |
| Disk write MB/s | `sum(rate(node_disk_written_bytes_total[5m])) / 1024 / 1024` |
| API latency p95 | `histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m])) * 1000` |
| Error rate % | `sum(rate(http_requests_total{status=~"5.."}[2m])) / sum(rate(http_requests_total[2m])) * 100` |
| Queue depth | `app_queue_depth{queue="default"}` |

---

## 7. Pod Placement

Current pod distribution across nodes (enforced via `nodeAffinity` and `nodeSelector`):

| Node | IP | AZ | Pods |
|------|----|----|------|
| **Master** `ip-10-0-1-176` | 10.0.1.176 | ap-southeast-1a | coredns, local-path-provisioner, metrics-server, ingress-nginx-controller, prometheus, grafana, cost-exporter, kube-state-metrics, node-exporter |
| **Worker-1** `ip-10-0-11-104` | 10.0.11.104 | ap-southeast-1a | demo-app (replica-1), node-exporter |
| **Worker-2** `ip-10-0-12-24` | 10.0.12.24 | ap-southeast-1b | demo-app (replica-2), node-exporter |

**Placement rules enforced in manifests:**

| Component | Rule | Reason |
|-----------|------|--------|
| prometheus, grafana, cost-exporter, kube-state-metrics | `nodeSelector: control-plane=true` + toleration | Must survive worker scale-down — fixed master never terminated |
| demo-app | `nodeAffinity: DoesNotExist(control-plane)` | Workload pods must not consume master resources |
| node-exporter | DaemonSet — runs on all nodes | Must scrape every node's metrics |
| ingress-nginx | No constraint (lands on master by default) | Master is fixed; acceptable for ingress |

**K8s network CIDRs (actual):**

| Layer | CIDR | Allocator |
|-------|------|-----------|
| VPC | 10.0.0.0/16 | AWS |
| Pod network | 10.42.0.0/16 | Flannel VXLAN (MTU 8951) |
| Service CIDR | 10.43.0.0/16 | K3s |
| Master pods | 10.42.0.0/24 | K3s node CIDR |
| Worker-1 pods | 10.42.3.0/24 | K3s node CIDR |
| Worker-2 pods | 10.42.2.0/24 | K3s node CIDR |

---

## 8. High Availability Design


| Risk | Mitigation |
|------|-----------|
| Single Lambda invocation fails | EventBridge retries; next invocation (2min) picks up |
| Lambda timeout during scaling | Lock auto-expires at 360s; next invocation force-clears |
| Worker fails to join cluster | Polling detects NotReady after 5min → terminate + alert |
| Spot interruption | EventBridge rule catches 2-min warning → cordon + drain → replacement |
| Scale-down disrupts stateful workloads | Critical pod check before drain — StatefulSet/kube-system/single-replica protected |
| Two Lambdas scale simultaneously | DynamoDB conditional write — only one gets the lock |
| All workers terminate (min=0) | Min node floor (2) enforced at decision time — hard floor |
| Master failure | Master is not autoscaled; manual recovery needed; state in DynamoDB survives |
| AZ failure | Workers distributed across AZ-1a and AZ-1b; min 1 per AZ maintained |

---

## 9. Security Architecture

All implementation details: [SECURITY_CHECKLIST.md](SECURITY_CHECKLIST.md)

### No Hardcoded Credentials

```python
# Correct pattern — Secrets Manager first, env var fallback, then fail
def get_prometheus_credentials():
    try:
        secret = secretsmanager.get_secret_value(SecretId='node-fleet/prometheus-auth')
        data = json.loads(secret['SecretString'])
        return data['username'], data['password']
    except Exception:
        username = os.environ.get('PROMETHEUS_USERNAME')
        password = os.environ.get('PROMETHEUS_PASSWORD')
        if not username or not password:
            raise ValueError("Prometheus credentials not found in Secrets Manager or env vars")
        return username, password
```

### Lambda IAM (Least Privilege)

```json
{
  "EC2": "RunInstances + TerminateInstances + DescribeInstances scoped to tag:Project=node-fleet",
  "DynamoDB": "PutItem/GetItem/UpdateItem/DeleteItem scoped to table ARN",
  "SecretsManager": "GetSecretValue scoped to node-fleet/* paths only",
  "SSM": "SendCommand + GetCommandInvocation (for drain)",
  "SNS": "Publish scoped to topic ARN",
  "CloudWatch": "PutMetricData (custom namespace only)",
  "SQS": "SendMessage scoped to DLQ ARN (for Lambda failures)",
  "VPC": "CreateNetworkInterface + DeleteNetworkInterface (required for VPC Lambda)"
}
```

### Encryption at Rest

| Resource | Encryption |
|----------|-----------|
| EBS volumes (all) | AWS-managed KMS |
| DynamoDB | Server-side encryption (SSE) |
| Secrets Manager | AES-256 |
| S3 (Lambda artifacts) | SSE-S3 |
| K3s API (in-transit) | TLS 1.3 — all kubectl + agent-join traffic encrypted |

### Secrets Manager Paths

| Path | Contents |
|------|---------|
| `node-fleet/k3s-token` | K3s server join token |
| `node-fleet/prometheus-auth` | `{"username":"...", "password":"..."}` |
| `node-fleet/ssh-key` | Master node SSH private key (PEM) |
| `node-fleet/slack-webhook` | Slack incoming webhook URL |

---

## 10. Monitoring Dashboards

Grafana runs on master node at `http://<master-ip>:30300`. Four dashboards cover all observability layers. Both Prometheus and CloudWatch datasources are provisioned automatically.

### Cluster Overview
Real-time cluster health — node count, CPU/memory utilization, network I/O, disk I/O, pending pods, scaling events timeline, pod distribution heatmap.

![Cluster Observability](diagrams/screenshots/NFR-5-Observability.png)

### Autoscaler Performance
Lambda execution metrics (CloudWatch) + cost savings gauge (Prometheus). Shows Lambda duration, invocation count, scaling decisions (scale-up vs scale-down ratio), node join latency, Lambda errors, and live node count.

![Autoscaler Performance](diagrams/screenshots/NFR-1-Performance.png)

### Application Metrics
Demo app observability — request rate (QPS), latency percentiles (p50/p95/p99), error rates (4xx/5xx), queue depth. Triggers autoscaler via custom metrics thresholds.

![Application Metrics](diagrams/screenshots/BONUS-4-Custom-App-Metrics.png)

### Cost Dashboard
See [COST_ANALYSIS.md](COST_ANALYSIS.md) for full breakdown. Real-time hourly/daily/monthly cost projections, savings vs static fleet, spot vs on-demand breakdown, CloudWatch billing panel.

![Cost Tracking](diagrams/screenshots/NFR-3-Cost.png)
