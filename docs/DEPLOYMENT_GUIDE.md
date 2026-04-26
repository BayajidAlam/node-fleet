# node-fleet — Deployment Guide

---

## ⚠️ Critical Lessons (Read Before Deploying)

Hard-won from production. Skipping these wastes hours.

1. **Lambda build MUST use Linux wheels** — Building `cryptography`/`paramiko` on Windows creates Windows-native `.so` files that crash on Lambda (Linux). Use `--platform manylinux2014_x86_64`.

2. **Store K3s token BEFORE workers boot** — Workers fetch the join token from Secrets Manager at boot via userdata. If the token isn't there when a worker first boots, it fails silently and never joins the cluster.

3. **Disable EventBridge before debugging Lambda** — If Lambda has a bug (e.g., sees 0 nodes), it fires every 2 minutes and spins up instances. Disable EventBridge immediately when debugging.

4. **Prometheus uses `kubernetes_sd_configs`** — Auto-discovers nodes via K8s API. Node-exporter metrics come from pod IPs (job=`kubernetes-pods`), not node IPs directly. Workers in private subnets cannot be scraped on port 9100 at node IP — scraping works via pod IP only.

5. **Deploy `node-exporter` DaemonSet manually** — Not bundled in K3s. Without it, Prometheus has no CPU/memory metrics.

6. **Use SSM for kubectl, not SSH** — Master has SSM agent. Workers may not. Use `aws ssm send-command` instead of SSH for cluster operations.

7. **EventBridge resets lambda schedule on Lambda code update** — Re-verify EventBridge is enabled after updating Lambda function code.

8. **EventBridge deploys at rate(5 minutes) despite Pulumi source saying rate(2 minutes)** — Pulumi state drift. Always verify and fix manually after `pulumi up` (see Step 7).

9. **Install FluxCD BEFORE running deploy.sh** — FluxCD manages K8s manifests via GitOps. Without it, gitops auto-reconciliation does not work and changes to gitops/ won't apply automatically.

10. **Grafana and Prometheus MUST run on master node** — Workers are auto-scaled and can be terminated. Without `nodeSelector: node-role.kubernetes.io/control-plane: "true"` on both deployments, they may land on workers and lose CloudWatch IAM access. The gitops manifests already have this set.

11. **Pulumi state .attrs files can corrupt** — If Pulumi fails with `invalid character '\x00'`, delete `~/.pulumi/stacks/<project>/<stack>.json.attrs` and `.bak.attrs`. These go all-null on interrupted deployments.

12. **Pulumi pending operations block re-deploys** — Interrupted `pulumi up` leaves `pending_operations` in state. Fix: `pulumi stack export > state.json`, remove `pending_operations` array with `node -e`, `pulumi stack import`. Then `pulumi refresh --yes` before retrying.

13. **SSH and K3s API (6443) blocked by default** — Master security group only allows SSH/6443 from VPC (`10.0.0.0/16`). For external access (deploy from laptop), add `0.0.0.0/0` rules manually after `pulumi up`.

14. **Pulumi passphrase needed for deploy.sh** — `deploy.sh` calls `pulumi stack output` to resolve Lambda/S3 names. Without `PULUMI_CONFIG_PASSPHRASE` set, outputs fail silently. Run: `export PULUMI_CONFIG_PASSPHRASE="<passphrase>"` before `./deploy.sh`.

15. **K3s TLS cert excludes public IP** — Fresh K3s install generates cert for private IP only. kubectl from outside fails with x509 error. Fix on master: add `tls-san: [<public-ip>]` to `/etc/rancher/k3s/config.yaml` and `systemctl restart k3s`.

16. **Grafana fix script requires Node.js** — `monitoring/fix-grafana.js` replaces the old bash script (which needed `jq` and worked only on master). Run from local machine: `node monitoring/fix-grafana.js <master-ip>`.

---

## Prerequisites

### Required Tools

| Tool | Version | Install |
|------|---------|---------|
| AWS CLI | 2.x | `curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o awscliv2.zip && unzip awscliv2.zip && sudo ./aws/install` |
| Pulumi CLI | 3.x | `curl -fsSL https://get.pulumi.com \| sh` |
| Node.js | 18+ | `curl -fsSL https://deb.nodesource.com/setup_18.x \| sudo -E bash - && sudo apt install -y nodejs` |
| Python | 3.11+ | `sudo apt install python3.11 python3.11-venv` |
| kubectl | 1.28+ | `curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" && chmod +x kubectl && sudo mv kubectl /usr/local/bin/` |
| k6 (optional) | latest | `sudo apt-key adv --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys C5AD17C747E3415A && sudo apt install k6` |

### AWS Permissions

Your AWS IAM user/role needs:
- `AdministratorAccess` OR the specific permissions to create: VPC, EC2, Lambda, DynamoDB, Secrets Manager, EventBridge, SNS, CloudWatch, IAM roles, ECR

```bash
aws sts get-caller-identity   # verify credentials work
aws configure list             # verify region = ap-southeast-1
```

---

## Full Deployment (From Zero)

![System Overview](diagrams/screenshots/00-System-Overview.png)

### Step 1 — Clone and Configure

```bash
git clone https://github.com/BayajidAlam/node-fleet.git
cd node-fleet

# Configure AWS region
aws configure set region ap-southeast-1
```

### Step 2 — Deploy AWS Infrastructure (Pulumi)

```bash
cd pulumi
npm install

# Set passphrase (required for encrypted config values)
export PULUMI_CONFIG_PASSPHRASE="<your-passphrase>"

# Preview changes first — always
pulumi preview

# Deploy (creates VPC, EC2 master, Lambda, DynamoDB, Secrets, SNS, CloudWatch, ECR)
pulumi up --yes

# Get master IP
export MASTER_IP=$(pulumi stack output masterPublicIpAddress)
echo "Master IP: $MASTER_IP"

cd ..
```

> **If pulumi up fails with `invalid character '\x00'`:** delete `~/.pulumi/stacks/node-fleet/dev.json.attrs` then retry.
>
> **If pulumi up fails with "pending operations":** run `pulumi stack export > s.json`, edit `s.json` to set `pending_operations: []`, then `pulumi stack import --file s.json && pulumi refresh --yes`.

### Step 3 — Open Security Group for External Access

After `pulumi up`, master security group only allows SSH/6443 from VPC. Open for external access:

```bash
# Get master security group ID
MASTER_SG=$(aws ec2 describe-instances \
  --filters "Name=tag:Role,Values=k3s-master" "Name=instance-state-name,Values=running" \
  --query "Reservations[0].Instances[0].SecurityGroups[0].GroupId" \
  --output text --region ap-southeast-1)

# Allow SSH from internet
aws ec2 authorize-security-group-ingress \
  --group-id $MASTER_SG --protocol tcp --port 22 --cidr 0.0.0.0/0 \
  --region ap-southeast-1

# Allow K3s API from internet (for kubectl)
aws ec2 authorize-security-group-ingress \
  --group-id $MASTER_SG --protocol tcp --port 6443 --cidr 0.0.0.0/0 \
  --region ap-southeast-1
```

### Step 4 — Set Up K3s Master

```bash
# SSH to master
ssh -i node-fleet-key.pem ubuntu@$MASTER_IP

# Add TLS SAN for public IP (required for kubectl from outside)
sudo mkdir -p /etc/rancher/k3s
echo "tls-san:
  - $MASTER_IP" | sudo tee /etc/rancher/k3s/config.yaml
sudo systemctl restart k3s
sleep 10

# On master: run setup script
./k3s/master-setup.sh
# This installs: K3s server, Prometheus, basic auth, kube-state-metrics

# Verify K3s running
kubectl get nodes
# NAME          STATUS   ROLES                  AGE   VERSION
# master-node   Ready    control-plane,master   2m    v1.28.x

exit
```

### Step 5 — Store K3s Token (CRITICAL: do BEFORE launching workers)

```bash
# Get token from master
TOKEN=$(ssh -i node-fleet-key.pem ubuntu@$MASTER_IP \
  "sudo cat /var/lib/rancher/k3s/server/node-token")

# Store in Secrets Manager
aws secretsmanager put-secret-value \
  --secret-id node-fleet/k3s-token \
  --secret-string "$TOKEN" \
  --region ap-southeast-1

# Verify
aws secretsmanager get-secret-value \
  --secret-id node-fleet/k3s-token \
  --query SecretString --output text
```

### Step 6 — Install FluxCD (GitOps)

FluxCD auto-reconciles K8s manifests from the GitHub repo every 10 minutes.

```bash
# On master node
ssh -i node-fleet-key.pem ubuntu@$MASTER_IP
cd /home/ubuntu
./gitops/install-flux.sh     # installs Flux + connects to GitHub repo
sudo k3s kubectl get kustomizations -A   # should show apps/infrastructure/monitoring: Ready
exit
```

### Step 7 — Deploy Lambda, Monitoring, and Grafana Dashboards

```bash
# Set passphrase before running deploy.sh
export PULUMI_CONFIG_PASSPHRASE="<your-passphrase>"

# Full deploy: Lambda + monitoring stack + Grafana dashboard import
./deploy.sh $MASTER_IP

# Or skip infra if already deployed:
./deploy.sh $MASTER_IP --skip-infra
```

The deploy script does:
1. Builds Lambda zip (Linux wheels) and deploys to `node-fleet-prod-autoscaler`
2. Creates monitoring namespace + `grafana-dashboards` ConfigMap from JSON files
3. Deploys Prometheus, Grafana, cost-exporter via gitops manifests
4. Runs `monitoring/fix-grafana.js` — resolves datasource UIDs, imports all 4 dashboards into Node-Fleet folder

> **If Grafana login fails after deploy:** Run `kubectl exec -n monitoring $(kubectl get pod -n monitoring -l app=grafana -o name) -- grafana cli admin reset-admin-password Admin@123` then restart pod.

### Step 8 — Fix EventBridge Rate (verify after pulumi up)

`pulumi up` sometimes deploys `rate(5 minutes)` due to state drift. Verify and fix:

```bash
# Check current rate
aws events list-rules --name-prefix node-fleet-prod-autoscaler \
  --region ap-southeast-1 --query "Rules[*].[Name,ScheduleExpression]" --output table

# Fix to 2 minutes if wrong
aws events put-rule \
  --name node-fleet-prod-autoscaler-schedule \
  --schedule-expression "rate(2 minutes)" \
  --state ENABLED \
  --region ap-southeast-1
```

### Step 9 — Verify Everything

```bash
# Check K3s nodes (should show master + workers)
export KUBECONFIG=/tmp/k3s-kubeconfig.yaml
kubectl get nodes -o wide

# Check all monitoring pods on master node
kubectl get pods -n monitoring -o wide

# Check Prometheus targets (all should be 'up')
curl -s http://$MASTER_IP:30090/api/v1/targets | python3 -c \
  'import sys,json; [print(t["labels"]["job"], t["health"]) for t in json.load(sys.stdin)["data"]["activeTargets"]]'

# Check Lambda invoked recently
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Invocations \
  --dimensions Name=FunctionName,Value=node-fleet-prod-autoscaler \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 3600 --statistics Sum --region ap-southeast-1

# Full verification script
bash scripts/verify-autoscaler-requirements.sh

# Access Grafana (password from Secrets Manager: node-fleet/grafana-admin-password)
echo "Grafana: http://$MASTER_IP:30300"
```

---

## EC2 Worker User Data Script

`k3s/worker-userdata.sh` runs on each new worker at first boot. Idempotent — safe to re-run.

```bash
#!/bin/bash
# No set -e — errors handled manually with retry loops
exec > >(tee /var/log/k3s-worker-setup.log)
exec 2>&1

# 1. Install dependencies
apt-get update -qq && apt-get install -y awscli jq curl

# 2. Fetch K3s token from Secrets Manager (retries 30×, 30s apart = 15 min max)
K3S_TOKEN=""
for i in $(seq 1 30); do
  K3S_TOKEN=$(aws secretsmanager get-secret-value \
    --secret-id node-fleet/k3s-token \
    --region ap-southeast-1 \
    --query SecretString --output text 2>/dev/null || echo "")
  [ -n "$K3S_TOKEN" ] && [ "$K3S_TOKEN" != "null" ] && break
  sleep 30
done
[ -z "$K3S_TOKEN" ] && echo "❌ Token fetch failed" && exit 1

# 3. Resolve master IP via EC2 tag (no hardcoded IPs)
MASTER_IP=""
for i in $(seq 1 20); do
  MASTER_IP=$(aws ec2 describe-instances \
    --region ap-southeast-1 \
    --filters "Name=tag:Role,Values=k3s-master" "Name=instance-state-name,Values=running" \
    --query 'Reservations[0].Instances[0].PrivateIpAddress' \
    --output text 2>/dev/null || echo "")
  [ -n "$MASTER_IP" ] && [ "$MASTER_IP" != "None" ] && break
  sleep 15
done
[ -z "$MASTER_IP" ] && echo "❌ Master not found" && exit 1

# 4. Join K3s cluster (retries 10×, 30s apart)
for i in $(seq 1 10); do
  curl -sfL https://get.k3s.io | \
    K3S_URL=https://${MASTER_IP}:6443 K3S_TOKEN=${K3S_TOKEN} sh - && break
  sleep 30
done

# 5. Verify agent running
systemctl is-active --quiet k3s-agent || { echo "❌ k3s-agent not running"; exit 1; }
echo "✅ Worker joined cluster at $MASTER_IP"
```

**Key design decisions:**
- Token fetched at runtime from Secrets Manager — never in User Data plaintext
- Master IP resolved via `tag:Role=k3s-master` — no hardcoded IP (survives master replacement)
- Retry loops on all steps — handles race condition where Lambda launches worker before master is fully ready
- Full log to `/var/log/k3s-worker-setup.log` for debugging via SSM or CloudWatch

---

## Lambda Build (Manual)

```bash
cd lambda

# Windows-safe build (manylinux wheels for Lambda Linux environment)
pip install \
  --platform manylinux2014_x86_64 \
  --only-binary=:all: \
  --target=. \
  cryptography paramiko

pip install -r requirements.txt --target=. --ignore-installed cryptography paramiko

# Package
zip -r function.zip . \
  --exclude "*.pyc" "__pycache__/*" "venv/*" "tests/*" "*.zip"

# Deploy to Lambda
aws lambda update-function-code \
  --function-name node-fleet-prod-autoscaler \
  --zip-file fileb://function.zip \
  --region ap-southeast-1

cd ..
```

---

## Day 2 Operations

### Update Lambda Code

```bash
cd lambda
# Edit Python files...
zip -r function.zip . --exclude "*.pyc" "__pycache__/*" "venv/*" "tests/*"
aws lambda update-function-code \
  --function-name node-fleet-prod-autoscaler \
  --zip-file fileb://function.zip
```

### Rotate Secrets

```bash
# Rotate Prometheus credentials
aws secretsmanager update-secret \
  --secret-id node-fleet/prometheus-auth \
  --secret-string '{"username":"prometheus","password":"<new-password>"}' \
  --region ap-southeast-1

# Update Prometheus web.yml on master with new bcrypt hash
ssh -i node-fleet-key.pem ubuntu@$MASTER_IP
htpasswd -nbB prometheus <new-password>
# → paste hash into /etc/prometheus/web.yml
kubectl rollout restart deployment/prometheus -n monitoring
```

### Add Capacity Manually (Emergency)

```bash
# Temporarily raise MAX_NODES
aws lambda update-function-configuration \
  --function-name node-fleet-prod-autoscaler \
  --environment "Variables={MAX_NODES=15,...}" \
  --region ap-southeast-1

# Or manually launch a worker using the launch template
TEMPLATE_ID=$(cd pulumi && pulumi stack output workerLaunchTemplateId)
aws ec2 run-instances \
  --launch-template LaunchTemplateId=$TEMPLATE_ID \
  --count 1 \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Project,Value=node-fleet},{Key=ManagedBy,Value=manual}]' \
  --region ap-southeast-1
```

### Emergency: Disable Autoscaler

```bash
# Disable EventBridge (stops Lambda invocations)
aws events disable-rule \
  --name node-fleet-prod-autoscaler-schedule \
  --region ap-southeast-1

# Re-enable when fixed
aws events enable-rule \
  --name node-fleet-prod-autoscaler-schedule \
  --region ap-southeast-1
```

### Check DynamoDB State

```bash
aws dynamodb get-item \
  --table-name node-fleet-prod-state \
  --key '{"cluster_id":{"S":"node-fleet-prod"}}' \
  --region ap-southeast-1 | jq .
```

### Manual Lock Release

```bash
# Use only if Lambda crashed and left lock stuck
aws dynamodb update-item \
  --table-name node-fleet-prod-state \
  --key '{"cluster_id":{"S":"node-fleet-prod"}}' \
  --update-expression 'REMOVE scaling_in_progress, lock_acquired_at, lock_expiry' \
  --region ap-southeast-1
```

### View Lambda Logs

```bash
# Stream live logs
aws logs tail /aws/lambda/node-fleet-prod-autoscaler --follow

# Last 50 invocations
aws logs get-log-events \
  --log-group-name /aws/lambda/node-fleet-prod-autoscaler \
  --log-stream-name "$(aws logs describe-log-streams \
    --log-group-name /aws/lambda/node-fleet-prod-autoscaler \
    --order-by LastEventTime --descending \
    --query 'logStreams[0].logStreamName' --output text)" \
  --limit 100
```

---

## Teardown

```bash
# 1. Disable autoscaler first
aws events disable-rule --name node-fleet-prod-autoscaler-schedule --region ap-southeast-1

# 2. Drain all workers gracefully
for node in $(kubectl get nodes -l 'node-role.kubernetes.io/worker' -o name); do
  kubectl drain $node --ignore-daemonsets --delete-emptydir-data --timeout=5m
done

# 3. Destroy infrastructure (WARNING: destructive)
cd pulumi
pulumi destroy --yes

# 4. Clean up local files
rm -f node-fleet-key.pem lambda/function.zip
```

---

## Rollback Procedures

### Rollback Lambda to Previous Version

```bash
# List versions
aws lambda list-versions-by-function \
  --function-name node-fleet-prod-autoscaler \
  --query 'Versions[*].[Version,LastModified]' --output table

# Point alias to previous version
aws lambda update-alias \
  --function-name node-fleet-prod-autoscaler \
  --name live \
  --function-version <previous-version>
```

### Rollback Pulumi Infrastructure

```bash
cd pulumi
# View history
pulumi stack history

# Roll back to specific deployment
pulumi stack import --file <exported-state.json>
```

### Rollback K8s Manifests (GitOps)

```bash
# All K8s manifests are in Git — rollback via git revert
git revert <commit-sha>
git push origin main
# FluxCD auto-applies within 1 minute
```
