---
name: prometheus-monitoring
version: 1.0.0
description: Prometheus monitoring for node-fleet. PromQL queries for scaling, scrape config, retention, basic auth, Grafana dashboards, CloudWatch alarms, alert rules.
---

> Read `.agents/CONTEXT.md` first — architecture, scaling rules, secrets paths.

# Prometheus Monitoring

## When to Use

- Modifying `gitops/infrastructure/prometheus-deployment.yaml`
- Debugging `lambda/metrics_collector.py`
- Adding/tuning PromQL queries
- Grafana dashboards in `monitoring/grafana-dashboards/`
- CloudWatch alarms in `pulumi/src/cloudwatch-alarms.ts`
- Alert rules in `gitops/monitoring/alerts.yaml`

---

## 1. Canonical PromQL (Use Exactly These)

Tuned for `node_exporter` + K3s. Changing `[5m]` window alters smoothing and alarm behavior.

```promql
# CPU utilization (0-100%)
avg(rate(node_cpu_seconds_total{mode!="idle"}[5m])) * 100

# Memory utilization (0-100%)
(1 - avg(node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100

# Pending pods
sum(kube_pod_status_phase{phase="Pending"})

# Worker node count
count(kube_node_info)

# Per-node CPU (drain candidate selection)
rate(node_cpu_seconds_total{mode!="idle",instance=~"<ip>:.*"}[5m]) * 100
```

**Query from Lambda** (`lambda/metrics_collector.py`):
```python
def query_prometheus(url, query, auth):
    resp = requests.get(f"{url}/api/v1/query",
                        params={"query": query}, auth=auth, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if data['status'] != 'success':
        raise ValueError(f"Prometheus query failed: {data}")
    results = data['data']['result']
    if not results:
        return 0.0  # no data = 0 (e.g., no pending pods)
    return float(results[0]['value'][1])
```

---

## 2. Deployment: Retention + Scrape Config

```yaml
# gitops/infrastructure/prometheus-deployment.yaml
containers:
- name: prometheus
  image: prom/prometheus:v2.45.0
  args:
  - "--config.file=/etc/prometheus/prometheus.yml"
  - "--storage.tsdb.path=/prometheus"
  - "--storage.tsdb.retention.time=7d"   # NOT 30d — cost constraint
  - "--web.enable-lifecycle"
```

**`prometheus.yml`**:
```yaml
global:
  scrape_interval: 15s       # must be 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'node-exporter'
    kubernetes_sd_configs:
      - role: node
    relabel_configs:
      - source_labels: [__address__]
        regex: '(.*):10250'
        replacement: '${1}:9100'
        target_label: __address__

  - job_name: 'kube-state-metrics'
    static_configs:
      - targets: ['kube-state-metrics.monitoring:8080']
```

---

## 3. Basic Auth (Security Requirement)

NodePort 30090 reachable from any VPC instance. Auth required.

```yaml
# nginx sidecar ConfigMap
data:
  nginx.conf: |
    server {
      listen 30090;
      location / {
        auth_basic "Prometheus";
        auth_basic_user_file /etc/nginx/auth/.htpasswd;
        proxy_pass http://localhost:9090;
      }
    }
```

```bash
htpasswd -nbB prometheus <password> > .htpasswd
kubectl create secret generic prometheus-auth --from-file=.htpasswd -n monitoring
```

Credentials stored at `node-fleet/prometheus-auth` in Secrets Manager — never hardcoded.

---

## 4. Alert Rules (`gitops/monitoring/alerts.yaml`)

```yaml
groups:
  - name: node-fleet
    rules:
    - alert: HighCPU
      expr: avg(rate(node_cpu_seconds_total{mode!="idle"}[5m])) * 100 > 85
      for: 5m
      labels: {severity: warning}

    - alert: EmergencyCPU
      expr: avg(rate(node_cpu_seconds_total{mode!="idle"}[5m])) * 100 > 90
      for: 2m
      labels: {severity: critical}
      annotations:
        summary: "CPU >90% — possible autoscaler failure"

    - alert: PendingPodsStuck
      expr: sum(kube_pod_status_phase{phase="Pending"}) > 0
      for: 10m
      labels: {severity: warning}
      annotations:
        summary: "Pods pending 10+ min — node join may have failed"

    - alert: MaxCapacityReached
      expr: count(kube_node_info) >= 10
      for: 10m
      labels: {severity: warning}

    - alert: NodeNotReady
      expr: kube_node_status_condition{condition="Ready",status="true"} == 0
      for: 5m
      labels: {severity: critical}
```

---

## 5. Grafana Dashboard Panels

| Panel | Query | Type |
|-------|-------|------|
| CPU Utilization | `avg(rate(node_cpu_seconds_total{mode!="idle"}[5m])) * 100` | Gauge + time series |
| Memory Utilization | `(1 - avg(node_memory_MemAvailable_bytes/node_memory_MemTotal_bytes)) * 100` | Gauge |
| Worker Node Count | `count(kube_node_info)` | Stat |
| Pending Pods | `sum(kube_pod_status_phase{phase="Pending"})` | Stat (red if >0) |
| Scale Events | CloudWatch `NodeFleet/Autoscaler/ScalingAction` | Bar chart |
| Cost/hour | CloudWatch `NodeFleet/Autoscaler/EstimatedCostPerHour` | Time series |
| Spot vs On-Demand | CloudWatch `NodeFleet/Autoscaler/SpotCount` | Pie chart |

CPU gauge thresholds: green 0–70%, yellow 70–85%, red 85–100%.

---

## 6. RBAC for K3s

```yaml
# gitops/monitoring/prometheus-rbac.yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: prometheus
rules:
- apiGroups: [""]
  resources: [nodes, nodes/proxy, services, endpoints, pods]
  verbs: [get, list, watch]
- nonResourceURLs: [/metrics]
  verbs: [get]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: prometheus
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: prometheus
subjects:
- kind: ServiceAccount
  name: prometheus
  namespace: monitoring
```

---

## Checklist

- [ ] Retention = `7d` (not 30d)
- [ ] Scrape interval = `15s`
- [ ] Basic auth enabled — creds in `node-fleet/prometheus-auth`
- [ ] NodePort = `30090`
- [ ] Lambda SG has TCP 30090 → master SG egress
- [ ] RBAC applied — ClusterRole + ClusterRoleBinding
- [ ] Rate queries use `[5m]` window
- [ ] kube-state-metrics deployed (needed for `kube_pod_status_phase`)
- [ ] node-exporter on all nodes (needed for CPU/memory)
- [ ] Alerts: HighCPU, EmergencyCPU, PendingPodsStuck, MaxCapacity, NodeNotReady
