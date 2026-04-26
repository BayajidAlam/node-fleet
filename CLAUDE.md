# CLAUDE.md

Guidance for Claude Code in this repo.

## What This Project Does

**node-fleet** — production K3s autoscaler on AWS EC2. EventBridge fires Python Lambda every 2 min; Lambda queries Prometheus metrics, makes scaling decision, launches or drains/terminates EC2 workers. DynamoDB provides distributed locking.

**Business context:** E-commerce startup, 15k+ daily users. Peak (9AM–9PM) CPU 70-80%; off-peak (9PM–9AM) CPU 20-30%; flash sales spike 85-95%. Static 5-node fleet wastes ~60,000 BDT/month off-peak.

**Goal:** Cut idle cost 54-58% vs. static fleet; respond to spikes in <3 min.

**Status:** All core + 7 bonus challenges complete.

## Prerequisites

- AWS CLI configured
- Pulumi CLI
- Node.js 18+
- Python 3.11+
- kubectl

## Commands

### Infrastructure (Pulumi — TypeScript, not Python)

```bash
cd pulumi && npm install
npm run build          # compile TypeScript
pulumi preview         # ALWAYS preview before up
pulumi up --yes        # deploy all AWS resources
pulumi stack output masterIp
```

### Lambda Build & Deploy

```bash
cd lambda
pip install -r requirements.txt -t .
zip -r function.zip . --exclude "*.pyc" "venv/*" "tests/*"
aws lambda update-function-code \
  --function-name node-fleet-prod-autoscaler \
  --zip-file fileb://function.zip
```

### Full Deployment

```bash
./deploy.sh <master-public-ip>              # full stack
./deploy.sh <ip> --skip-infra               # lambda + monitoring only
bash scripts/verify-autoscaler-requirements.sh
```

### K3s Cluster

```bash
ssh -i node-fleet-key.pem ubuntu@<master-ip>
./k3s/master-setup.sh        # first-time master init (Prometheus, basic auth)
kubectl get nodes
kubectl apply -f gitops/infrastructure/prometheus-deployment.yaml
```

### Testing

```bash
# Python Lambda unit tests (120+ cases)
cd tests/lambda && pip install -r requirements.txt
python -m pytest . -v

# TypeScript/Jest tests
cd tests && npm install
npm run test:unit
npm run test:integration
npm run test:coverage
npm run test:ci           # JUnit XML output for CI

# Load testing
k6 run tests/load/load-test.js --vus 100 --duration 5m
k6 run tests/load/load-test-flash-sale.js
```

### GitOps (FluxCD)

```bash
cd gitops && ./install-flux.sh
kubectl apply -f gitops/infrastructure/prometheus-deployment.yaml
./gitops/reconcile.sh    # force sync
```

## Architecture

### Lambda Orchestration (`lambda/autoscaler.py`)

7-step flow:

1. **Check pending drains** — SSM command status from previous invocation
2. **DynamoDB lock** — conditional write; stale lock auto-cleared after 360s; released in `finally`
3. **Prometheus metrics** — CPU, memory, pending pods via PromQL NodePort 30090 basic auth
4. **Scaling decision** — `scaling_decision.py` evaluates windows + cooldowns + predictive layer
5. **Execute** — EC2 RunInstances (scale-up) or SSM kubectl-drain (async) + TerminateInstances (scale-down)
6. **State update** — DynamoDB, CloudWatch custom metrics, SNS → Slack
7. **Lock release** — always in `finally`

### Scaling Thresholds

| Condition | Threshold | Window |
|-----------|-----------|--------|
| Scale UP: CPU | >70% | 3×2min (~6min) |
| Scale UP: memory | >75% | same |
| Scale UP: pending pods | any | 2×2min (~4min) |
| Scale DOWN: CPU | <30% | 5×2min=10min |
| Scale DOWN: memory | <50% | same |
| Scale DOWN: pending pods | =0 | same |
| Min/max nodes | 2/10 | — |
| Scale-up cooldown | — | 5min (300s) |
| Scale-down cooldown | — | 10min (600s) |

Scale-up +1 (or +2 if CPU>85% or pending>5). Scale-down -1.

### Key Modules

| Module | Role |
|--------|------|
| `lambda/autoscaler.py` | Main handler, 7-step |
| `lambda/scaling_decision.py` | Thresholds + cooldown |
| `lambda/ec2_manager.py` | Launch, drain (SSM async), terminate, kubectl delete |
| `lambda/state_manager.py` | DynamoDB lock + state |
| `lambda/metrics_collector.py` | Prometheus PromQL client |
| `lambda/predictive_scaling.py` | 7-day history, pre-scales 10min early |
| `lambda/spot_instance_helper.py` | 70% Spot mix, interruption + On-Demand fallback |
| `lambda/multi_az_helper.py` | Workers across ap-southeast-1a/1b |
| `lambda/custom_metrics.py` | Queue depth, latency p95, error rate |
| `lambda/cost_optimizer.py` | Instance hours, savings %, Lambda cost |
| `lambda/dynamic_scheduler.py` | Dynamic EventBridge interval |
| `lambda/slack_notifier.py` | SNS → Slack |
| `lambda/audit_logger.py` | DynamoDB streams audit trail |
| `pulumi/src/iam.ts` | Least-privilege Lambda IAM |
| `pulumi/src/cloudwatch-alarms.ts` | 8 CloudWatch alarms |
| `k3s/master-setup.sh` | K3s server init + Prometheus + basic auth |
| `k3s/worker-userdata.sh` | Auto-join via Secrets Manager token |

## Critical Implementation Rules

### Never hardcode master IP
Resolve via EC2 tag:
```python
ec2.describe_instances(Filters=[
    {'Name': 'tag:Role', 'Values': ['k3s-master']},
    {'Name': 'instance-state-name', 'Values': ['running']}
])
```

### Prometheus credentials: Secrets Manager first, env vars second, fail-fast
Path: `node-fleet/prometheus-auth` (keys: `username`, `password`). No hardcoded fallback.

### Drain validation: exit code AND keyword
```python
if exit_status != 0 or "drained" not in out_str:
    return False  # do NOT terminate
```
`kubectl drain` implicitly cordons — no separate cordon step.

### DynamoDB lock expiry = 360s
Covers worst-case drain (300s) + node-join. Released in `finally` always.

### Pulumi is TypeScript
IaC in `pulumi/src/*.ts`. No `.py` in pulumi dir.

### Scale-down drain is async (SSM)
Lambda initiates via SSM Run Command (<5s return). Next invocation (2min) checks status + terminates. Keeps Lambda under 30s.

### Never terminate nodes hosting critical pods
Exclude: `kube-system` pods (CoreDNS, metrics-server), StatefulSet pods, single-replica deployments.

## Secrets Manager Paths

| Secret | Path |
|--------|------|
| K3s join token | `node-fleet/k3s-token` |
| Prometheus auth | `node-fleet/prometheus-auth` |
| SSH key (master) | `node-fleet/ssh-key` |
| Slack webhook | `node-fleet/slack-webhook` |

## PromQL Queries

```promql
CPU:     avg(rate(node_cpu_seconds_total{mode!="idle"}[5m])) * 100
Memory:  (1 - avg(node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100
Pending: sum(kube_pod_status_phase{phase="Pending"})
Nodes:   count(kube_node_info)
```

Prometheus: NodePort 30090, basic auth, scrape 15s, retention 7d.

## Known Gotchas

1. **Window math**: `window=5` at 2-min = 10min. `window=2` = ~4min.
2. **Lock expiry is 360s** — not 120s.
3. **Drain validation**: exit code AND `"drained"`. Exit 0 alone insufficient.
4. **Spot drain timeout** = 300s. Not 120s.
5. **Prometheus retention 7d** — use `--storage.tsdb.retention.time=7d`.
6. **DLQ needs `sqs:SendMessage`** on Lambda role (`pulumi/src/iam.ts`).
7. **Async drain state** in DynamoDB `draining_instances` — check before lock.
8. **Worker count excludes master** — `MIN_NODES=2` = 2 workers, master separate.

## Skills Available

| Skill | When | Path |
|-------|------|------|
| `k3s-devops` | K3s ops, drain/join, SSH | `.agents/skills/k3s-devops/SKILL.md` |
| `aws-cloud-patterns` | EC2, Lambda, DynamoDB, SSM, Secrets Manager | `.agents/skills/aws-cloud-patterns/SKILL.md` |
| `python-lambda-backend` | Lambda patterns, scaling, packaging, testing | `.agents/skills/python-lambda-backend/SKILL.md` |
| `prometheus-monitoring` | PromQL, alerts, Grafana | `.agents/skills/prometheus-monitoring/SKILL.md` |
| `pulumi-best-practices` | `pulumi/src/*.ts` code | `.agents/skills/pulumi-best-practices/SKILL.md` |
| `aws-solution-architect` | Architecture, HA, cost | `.agents/skills/aws-solution-architect/SKILL.md` |
| `code-reviewer` | Pre-merge review | `.agents/skills/code-reviewer/SKILL.md` |
| `aws-diagrams` | Excalidraw diagrams | `.agents/skills/aws-diagrams/SKILL.md` |

## Agent Strategy

| Agent | Scope | Skills |
|-------|-------|--------|
| `app-agent` | `lambda/`, `tests/`, `demo-app/` | `python-lambda-backend`, `code-reviewer`, `aws-solution-architect` |
| `infra-agent` | `pulumi/`, `k3s/`, `gitops/`, `monitoring/` | `pulumi-best-practices`, `k3s-devops`, `aws-cloud-patterns` |
| `review-agent` | All code — read-only | `code-reviewer`, `aws-solution-architect`, `aws-diagrams` |
| `docs-agent` | `docs/`, `README.md` | `aws-diagrams`, `prometheus-monitoring` |

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/SCALING_ALGORITHM.md](docs/SCALING_ALGORITHM.md)
- [docs/DEPLOYMENT_GUIDE.md](docs/DEPLOYMENT_GUIDE.md)
- [docs/TESTING_RESULTS.md](docs/TESTING_RESULTS.md)
- [docs/SECURITY_CHECKLIST.md](docs/SECURITY_CHECKLIST.md)
- [docs/COST_ANALYSIS.md](docs/COST_ANALYSIS.md)
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)

## Documentation Style
- Use 'caveman-compressed' format for skill files and internal docs (terse, keyword-heavy, minimal grammar) to save tokens
- When asked to 'compress' files, compress ALL specified files directly rather than providing an assessment first

## Diagrams
- When user asks to 'add a visual' to a doc that already has ASCII art, REPLACE the ASCII art rather than moving or duplicating images
- For PNG diagram generation, verify fonts load correctly and shapes don't overlap before finalizing

## Requirements Verification
- For doc/requirements audits, systematically cross-check every claim against source requirements (e.g., SDB1) and fix inconsistencies across ALL related files in one pass