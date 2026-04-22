# node-fleet K3s Autoscaler — Project Context

> Single source of truth. Read first. Location: `.agents/CONTEXT.md`

---

## What This Project Is

**SmartScale K3s Autoscaler** — production K3s autoscaler on AWS EC2. Built for TechFlow Solutions (e-commerce, 15k+ daily users, ~80 lakh BDT/month transactions).

**Problem:**
- Cuts 60,000 BDT/month off-peak waste (9PM–9AM, CPU 20-30%)
- Prevents outages during peak/flash sales (CPU 85-95%)
- Replaces 15-20 min manual scaling with <3 min automated

**Status:** All core + 7 bonus challenges complete.

**Repository:** https://github.com/BayajidAlam/node-fleet | **Branch:** main

---

## Architecture

```
EventBridge (every 2 min)
    │
    ▼
AWS Lambda (Python 3.11, 256MB, 60s timeout)
    │
    ├─── Secrets Manager ──→ Prometheus credentials
    │
    ├─── Prometheus NodePort:30090 ──→ CPU / Memory / Pending Pods
    │         (K3s cluster, basic auth)
    │
    ├─── DynamoDB ──→ acquire lock, read state + history
    │
    ├─── Scaling Decision Engine
    │         │
    │         ├── scale_up  ──→ EC2 RunInstances (on-demand or spot)
    │         │                  Worker UserData ──→ Secrets Manager (K3s token)
    │         │                  SSH to master ──→ wait for "Ready"
    │         │
    │         └── scale_down ──→ SSH to master (kubectl drain, 300s timeout)
    │                            EC2 TerminateInstances
    │                            kubectl delete node (remove ghost)
    │
    ├─── DynamoDB ──→ update state, release lock
    ├─── CloudWatch ──→ metrics + logs
    └─── SNS ──→ Slack webhook
```

### Data Flow

```
1. EventBridge fires every 2 min
2. Lambda checks DynamoDB lock → exits if scaling in progress
3. Lambda fetches Prometheus credentials from Secrets Manager
4. Lambda queries Prometheus /api/v1/query for cluster metrics
5. Scaling decision evaluated with 5-reading history from DynamoDB
6. If scale_up:  acquire lock → RunInstances → worker joins via UserData → wait Ready
   If scale_down: acquire lock → SSH drain → TerminateInstances → delete node
7. DynamoDB state updated, lock released in `finally` block
8. CloudWatch metrics published, Slack notification sent via SNS
```

---

## Folder Structure

```
node-fleet/
├── .agents/
│   ├── CONTEXT.md                 ← THIS FILE
│   ├── app-agent.agent.md
│   ├── infra-agent.agent.md
│   ├── review-agent.agent.md
│   ├── docs-agent.agent.md
│   └── skills/
├── lambda/                        ← Python 3.11 Lambda code
│   ├── autoscaler.py              ← Main handler (6-step flow)
│   ├── scaling_decision.py        ← Thresholds and scaling logic
│   ├── metrics_collector.py       ← Prometheus PromQL queries
│   ├── ec2_manager.py             ← EC2 launch/terminate/drain via SSH
│   ├── state_manager.py           ← DynamoDB lock + state
│   ├── slack_notifier.py          ← SNS → Slack webhook
│   ├── multi_az_helper.py         ← Multi-AZ subnet balancing
│   ├── spot_instance_helper.py    ← 70% Spot mix, interruption handling
│   ├── predictive_scaling.py      ← 7-day historical pattern analysis
│   ├── custom_metrics.py          ← Queue depth, latency p95, error rate
│   ├── cost_optimizer.py          ← Instance hours, cost tracking
│   ├── dynamic_scheduler.py       ← Dynamic EventBridge interval
│   ├── audit_logger.py            ← DynamoDB streams audit trail
│   └── requirements.txt
├── pulumi/src/                    ← TypeScript IaC (NOT Python)
│   ├── vpc.ts, ec2-master.ts, ec2-worker.ts, lambda.ts
│   ├── dynamodb.ts, iam.ts, security-groups.ts
│   ├── cloudwatch-alarms.ts, sns.ts, secrets.ts, s3.ts, keypair.ts
├── k3s/
│   ├── master-setup.sh            ← K3s master init, Prometheus, basic auth
│   └── worker-userdata.sh         ← Auto-join via Secrets Manager token
├── gitops/infrastructure/
│   └── prometheus-deployment.yaml ← retention: 7d, scrape: 15s
├── monitoring/grafana-dashboards/
├── demo-app/
├── tests/lambda/                  ← 120+ pytest tests
└── docs/
```

---

## Scaling Rules

### Scale UP (ANY condition):

| Condition | Threshold | Variable | Window |
|-----------|-----------|----------|--------|
| CPU | >70% | `CPU_SCALE_UP_THRESHOLD=70.0` | `window=3` (3×2min=~6min) |
| Pending pods | >0 | pending>0 | `window=2` (2×2min=~4min) |
| Memory | >75% | `MEMORY_SCALE_UP_THRESHOLD=75.0` | `window=3` |

### Scale DOWN (ALL conditions):

| Condition | Threshold | Variable | Window |
|-----------|-----------|----------|--------|
| CPU | <30% | `CPU_SCALE_DOWN_THRESHOLD=30.0` | `window=5` (5×2min=10min) |
| Pending pods | =0 | <1 | `window=5` |
| Memory | <50% | `MEMORY_SCALE_DOWN_THRESHOLD=50.0` | `window=5` |

**Constraints:** Min 2 / Max 10 nodes. Scale-up +1 (or +2 if CPU>85% OR pending>5). Scale-down -1.
Cooldowns: up=5min (300s), down=10min (600s).

---

## Lambda Spec

| Setting | Value |
|---------|-------|
| Runtime | Python 3.11 |
| Memory | 256 MB |
| Timeout | 60s |
| Trigger | EventBridge `rate(2 minutes)` |
| DLQ | SQS `node-fleet-cluster-autoscaler-dlq` (14-day retention) |

**6-Step Flow:**
1. Check DynamoDB lock — exit if `scaling_in_progress = true`
2. Fetch Prometheus credentials from Secrets Manager
3. Query Prometheus `/api/v1/query` (CPU, memory, pending pods)
4. Evaluate scaling with history
5. If action: acquire lock → EC2 API calls → update state
6. Release lock in `finally` (always)

---

## DynamoDB Schema

**Table:** `k3s-autoscaler-state` (or `STATE_TABLE` env var)
- PK: `cluster_id` (String)
- Attrs: `node_count`, `last_scale_time`, `scaling_in_progress`, `lock_acquired_at`, `lock_expiry`
- Lock expiry: **360s** (300s drain + node join buffer)
- Stale lock auto-release: `lock_acquired_at < now - 360`

---

## Prometheus Config

| Setting | Value |
|---------|-------|
| Scrape interval | 15s |
| Retention | **7d** (NOT 30d) |
| Exposure | NodePort 30090, basic auth |
| Credentials | `node-fleet/prometheus-auth` |

```promql
CPU:     avg(rate(node_cpu_seconds_total{mode!="idle"}[5m])) * 100
Memory:  (1 - avg(node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100
Pending: sum(kube_pod_status_phase{phase="Pending"})
Nodes:   count(kube_node_info)
```

---

## Secrets Manager Keys

| Secret | Content | Used By |
|--------|---------|---------|
| `node-fleet/k3s-token` | K3s join token | worker UserData |
| `node-fleet/prometheus-auth` | `{"username":"...","password":"..."}` | Lambda |
| `node-fleet/ssh-key` | RSA private key PEM | Lambda SSH |
| `node-fleet/slack-webhook` | Slack webhook URL | Slack notifier Lambda |

---

## Graceful Scale-Down Order

1. Select safest node (fewest pods, no critical/StatefulSet/single-replica)
2. SSH to master → `kubectl drain <node> --ignore-daemonsets --delete-emptydir-data --force --timeout=300s`
   - drain implicitly cordons — no separate cordon step
3. Validate: exit code=0 AND `"drained"` in output — skip terminate if failed
4. `ec2.terminate_instances(InstanceIds=[instance_id])`
5. SSH → `kubectl delete node <node>` (remove ghost)

**Never drain/terminate nodes with:** `kube-system` pods, StatefulSet pods, single-replica deployments.

---

## Master IP Resolution

```python
response = self.ec2_client.describe_instances(
    Filters=[
        {'Name': 'tag:Role', 'Values': ['k3s-master']},
        {'Name': 'instance-state-name', 'Values': ['running']}
    ]
)
return response['Reservations'][0]['Instances'][0]['PrivateIpAddress']
```

---

## Prometheus Credentials Pattern

```python
def get_prometheus_credentials():
    try:  # Secrets Manager first
        sm = boto3.client('secretsmanager')
        creds = json.loads(sm.get_secret_value(SecretId="node-fleet/prometheus-auth")['SecretString'])
        return creds['username'], creds['password']
    except Exception:  # Fallback to env vars
        u, p = os.environ.get("PROMETHEUS_USERNAME"), os.environ.get("PROMETHEUS_PASSWORD")
        if not u or not p:
            raise ValueError("Prometheus credentials unavailable")
        return u, p
```

---

## CloudWatch Alarms (`pulumi/src/cloudwatch-alarms.ts`)

| Alarm | Condition | Action |
|-------|-----------|--------|
| Scaling failures | Lambda errors | SNS notification |
| CPU emergency | >90% for 5min | SNS urgent alert |
| Max capacity | Node count=10 for 10+min | SNS capacity warning |
| Node join failure | Join latency > threshold | SNS alert |

---

## Bonus Features

| Bonus | Module | Notes |
|-------|--------|-------|
| Multi-AZ workers | `multi_az_helper.py` | Balances across ap-southeast-1a/1b |
| Spot instances (70%) | `spot_instance_helper.py` | Interruption drain + On-Demand fallback |
| Predictive scaling | `predictive_scaling.py` | 7-day history, pre-scales 10min early |
| Custom app metrics | `custom_metrics.py` | Queue depth, latency p95, error rate |
| GitOps | `gitops/` | Versioned K8s manifests |
| Slack notifications | `slack_notifier.py` + `sns.ts` | Scale up/down/fail/warning |
| Cost dashboard | `cost_optimizer.py` | Instance hours, savings %, Lambda cost |

---

## Agent Strategy

| Agent | Scope | Skills |
|-------|-------|--------|
| `app-agent` | `lambda/`, `demo-app/`, `tests/` | `code-reviewer`, `aws-solution-architect` |
| `infra-agent` | `pulumi/`, `k3s/`, `gitops/`, `monitoring/` | `pulumi-best-practices`, `aws-solution-architect` |
| `review-agent` | All code — read-only review | `code-reviewer`, `aws-solution-architect`, `aws-diagrams` |
| `docs-agent` | `docs/`, `README.md` | `documentation-authoring`, `aws-diagrams` |

---

## Dev Commands

```bash
# Infrastructure
cd pulumi && pulumi up
pulumi stack output masterIp

# Lambda packaging
cd lambda
pip install -r requirements.txt -t .
zip -r function.zip . --exclude "*.pyc" "venv/*" "tests/*"
aws lambda update-function-code --function-name node-fleet-cluster-autoscaler --zip-file fileb://function.zip

# K3s cluster
ssh -i node-fleet-key.pem ubuntu@<master-ip>
./k3s/master-setup.sh
kubectl get nodes
kubectl apply -f gitops/infrastructure/prometheus-deployment.yaml

# Testing
cd tests && python -m pytest lambda/ -v
k6 run demo-app/load-test.js --vus 100 --duration 5m
```

---

## Skills Available

| Skill | When | Path |
|-------|------|------|
| `aws-solution-architect` | Architecture, HA, cost | `.agents/skills/aws-solution-architect/SKILL.md` |
| `pulumi-best-practices` | `pulumi/src/*.ts` code | `.agents/skills/pulumi-best-practices/SKILL.md` |
| `k3s-devops` | K3s ops, drain/join, SSH | `.agents/skills/k3s-devops/SKILL.md` |
| `aws-cloud-patterns` | EC2, Lambda, DynamoDB, SSM | `.agents/skills/aws-cloud-patterns/SKILL.md` |
| `python-lambda-backend` | Lambda patterns, scaling, testing | `.agents/skills/python-lambda-backend/SKILL.md` |
| `prometheus-monitoring` | PromQL, alerts, Grafana | `.agents/skills/prometheus-monitoring/SKILL.md` |
| `code-reviewer` | Pre-merge review | `.agents/skills/code-reviewer/SKILL.md` |
| `aws-diagrams` | Excalidraw diagrams | `.agents/skills/aws-diagrams/SKILL.md` |

---

## Known Gotchas

1. **Pulumi is TypeScript** — all IaC `.ts` in `pulumi/src/`. No `.py` in pulumi.
2. **Master IP is dynamic** — always use `_get_master_ip()` EC2 tag lookup.
3. **Prometheus retention 7d** — use `--storage.tsdb.retention.time=7d`.
4. **DLQ needs `sqs:SendMessage`** on Lambda role (`pulumi/src/iam.ts`).
5. **Window math**: `window=5` at 2-min = 10min. `window=2` = ~4min.
6. **Lock expiry is 360s** — not 120s. Covers drain (300s) + join.
7. **Drain validation**: exit code AND `"drained"`. Exit 0 alone insufficient.
8. **Spot drain timeout** = 300s. Not 120s.
