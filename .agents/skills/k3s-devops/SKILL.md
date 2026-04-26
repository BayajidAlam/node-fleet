---
name: k3s-devops
version: 1.0.0
description: K3s cluster ops for node-fleet. Worker join/drain, kubectl patterns, SSH to master, Prometheus NodePort, graceful scale-down safety, K3s quirks.
---

> Read `.agents/CONTEXT.md` first — architecture, scaling rules, secrets paths.

# K3s DevOps

## When to Use

- Modifying `k3s/master-setup.sh` or `k3s/worker-userdata.sh`
- Drain/cordon/join logic in `lambda/ec2_manager.py`
- Debugging node join failures or drain timeouts
- Modifying `gitops/infrastructure/prometheus-deployment.yaml`
- Any kubectl op from Lambda (via SSH to master)

---

## 1. Worker Join via Secrets Manager

Token = credential. Never hardcode. Never bake into AMI/Launch Template.

**Wrong**:
```bash
K3S_URL=https://1.2.3.4:6443 K3S_TOKEN=hardcoded curl -sfL https://get.k3s.io | sh -
```

**Right** — `k3s/worker-userdata.sh`:
```bash
TOKEN=$(aws secretsmanager get-secret-value \
  --secret-id node-fleet/k3s-token \
  --region ap-southeast-1 \
  --query SecretString --output text)

MASTER_IP=$(aws ec2 describe-instances \
  --filters "Name=tag:Role,Values=k3s-master" "Name=instance-state-name,Values=running" \
  --query 'Reservations[0].Instances[0].PrivateIpAddress' \
  --output text)

curl -sfL https://get.k3s.io | K3S_URL="https://${MASTER_IP}:6443" K3S_TOKEN="${TOKEN}" sh -s - agent
```

Rules: token from Secrets Manager; master IP from EC2 tag; worker role needs `secretsmanager:GetSecretValue` on `node-fleet/k3s-token`.

---

## 2. Drain: Exit Code AND Keyword

`kubectl drain` can exit 0 without fully draining (local storage edge case). Exit code alone insufficient.

**Wrong**:
```python
result = subprocess.run(["kubectl", "drain", node_name, ...])
if result.returncode == 0:
    terminate_instance(instance_id)  # may not be drained
```

**Right**:
```python
result = ssm_run_command(
    f"kubectl drain {node_name} --ignore-daemonsets --delete-emptydir-data --force --timeout=300s"
)
if result['exit_status'] != 0 or "drained" not in result['output']:
    logger.error(f"Drain failed: exit={result['exit_status']}, out={result['output']}")
    return False  # do NOT terminate
terminate_instance(instance_id)
```

Flags: `--ignore-daemonsets` (safe skip), `--delete-emptydir-data` (evict emptyDir pods), `--force` (evict uncontrolled pods), `--timeout=300s` (never hang). Drain implicitly cordons — no separate cordon step.

---

## 3. Node Selection: Safest First

```python
def _select_node_to_drain(self, nodes):
    scored = []
    for node in nodes:
        score = 0
        for pod in self._get_node_pods(node['name']):
            if pod.get('namespace') == 'kube-system': score += 100
            if pod.get('is_statefulset'):             score += 50
            if pod.get('is_single_replica'):          score += 30
        scored.append({'node': node, 'score': score})
    return min(scored, key=lambda x: x['score'])['node']
```

Never drain nodes with: `kube-system` pods (CoreDNS, metrics-server), StatefulSet pods, single-replica deployments.

---

## 4. Async Drain via SSM (Lambda stays under 30s)

Sync drain = 300s. Lambda timeout = 60s. SSM returns in <5s, executes async.

```
Invocation N:
  ssm.send_command(kubectl drain ...) → command_id in <5s
  DynamoDB: draining_instances[instance_id] = {command_id, node_name}
  Lambda exits

Invocation N+1 (2 min later):
  ssm.get_command_invocation(command_id)
  "Success" + "drained" in output → terminate_instance()
  "InProgress" → wait next cycle (up to 300s total)
  "Failed" → alert, clear state, do NOT terminate
```

```python
def _check_pending_drains(self, state):
    for instance_id, drain_info in list(state.get('draining_instances', {}).items()):
        result = self.ssm.get_command_invocation(
            CommandId=drain_info['command_id'],
            InstanceId=drain_info['master_instance_id']
        )
        if result['Status'] == 'Success' and 'drained' in result['StandardOutputContent']:
            self.ec2.terminate_instances(InstanceIds=[instance_id])
            self._remove_ghost_node(drain_info['node_name'])
```

---

## 5. Prometheus NodePort 30090

No LoadBalancer cost. Lambda reaches via master private IP.

```yaml
# Service spec
type: NodePort
ports:
- port: 9090
  nodePort: 30090
```

Constraints: retention `7d`; scrape `15s`; basic auth required; Lambda SG must allow TCP 30090 → master SG.

```python
resp = requests.get(f"http://{master_ip}:30090/api/v1/query",
                    params={"query": query}, auth=(user, pw), timeout=10)
```

---

## 6. Ghost Node Cleanup

K3s doesn't auto-remove terminated instances. Ghost nodes corrupt node count.

```python
def _remove_ghost_node(self, node_name):
    self._ssh_master(f"kubectl delete node {node_name} --ignore-not-found")

def _wait_node_ready(self, node_name, timeout=180):
    result = self._ssh_master(
        f"kubectl wait node/{node_name} --for=condition=Ready --timeout={timeout}s"
    )
    return result.exit_status == 0
```

Always run after `terminate_instances()`. Always wait Ready before counting new node.

---

## Quick Reference

| Op | Command |
|----|---------|
| List nodes | `kubectl get nodes -o wide` |
| Drain | `kubectl drain <node> --ignore-daemonsets --delete-emptydir-data --force --timeout=300s` |
| Delete ghost | `kubectl delete node <node> --ignore-not-found` |
| Wait ready | `kubectl wait node/<node> --for=condition=Ready --timeout=180s` |
| K3s token | `sudo cat /var/lib/rancher/k3s/server/node-token` (master only) |
| Master logs | `sudo journalctl -u k3s -f` |
| Worker logs | `sudo journalctl -u k3s-agent -f` |

## Checklist

- [ ] Drain checks exit code AND `"drained"` keyword
- [ ] No hardcoded K3s token — Secrets Manager only
- [ ] Master IP via EC2 tag, not hardcoded
- [ ] Ghost nodes deleted after termination
- [ ] New nodes waited to Ready before count update
- [ ] Prometheus retention `7d`, scrape `15s`
- [ ] Lambda SG allows TCP 30090 → master SG
