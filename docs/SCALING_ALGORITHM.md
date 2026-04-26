# node-fleet — Scaling Algorithm

> Workers only: master never scales. `MIN_NODES=2`, `MAX_NODES=10` refer to workers.

---

## 1. Algorithm Overview

node-fleet uses a **hybrid three-layer decision engine** that runs every 2 minutes.

![Scaling Decision Algorithm](diagrams/screenshots/FR-2-Scaling-Logic.png)

| Layer | Type | Trigger |
|-------|------|---------|
| 1. Reactive | Threshold-based | CPU, memory, pending pods exceed sustained thresholds |
| 2. Custom App | Application signals | Queue depth, API latency, error rate |
| 3. Predictive | Historical patterns | 7-day CPU history detects upcoming spikes |

**Scale-up logic**: OR — any condition triggers a scale-up.  
**Scale-down logic**: AND — all conditions must hold simultaneously.

This asymmetry is intentional: it's safer to add a node too early than to remove one too aggressively.

---

## 2. Parameters Reference

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Evaluation interval | 2 min | Balance: frequent enough for fast response; infrequent enough to avoid thrashing |
| Scale-up CPU threshold | 70% | Leaves 30% headroom; 70% sustained = genuinely overloaded |
| Scale-down CPU threshold | 30% | Large gap (30% vs 70%) prevents oscillation |
| Scale-up memory threshold | 75% | Near OOM threshold on t3.small (2GB) |
| Scale-down memory threshold | 50% | Conservative — memory spikes can be sudden |
| Scale-up window | 3 readings (~6 min) | Prevents scaling on transient spikes |
| Scale-down window | 5 readings (~10 min) | Longer window = more conservative = safer |
| Pending pods window | 2 readings (~4 min) | Pending pods = direct demand signal; shorter window acceptable |
| Scale-up cooldown | 300s (5 min) | Allow new nodes to join and absorb load before next decision |
| Scale-down cooldown | 600s (10 min) | Longer — prevents removing a node just added |
| Scale-up increment | +1 (normal) or +2 (urgent) | Urgency: CPU>85% or pending_pods>5 |
| Scale-down increment | -1 | Always conservative — one node at a time |
| Drain timeout | 300s | Covers slow graceful shutdown (terminationGracePeriodSeconds=300) |
| Lock expiry | 360s | 300s drain + 60s buffer for node join overhead |
| Queue depth scale-up | >1000 tasks | Application-specific: overwhelmed task queue |
| Latency p95 scale-up | >2000ms | Response time degradation is user-visible |
| Error rate scale-up | >5% (2+ min) | Server-side errors signal capacity problem |
| Queue depth scale-down guard **[BONUS]** | <100 tasks (10+ min) | Don't remove capacity while queue still draining |

---

## 3. Metric Collection

Lambda queries Prometheus via HTTP on every invocation (Step 3).

![Metric Collection Flow](diagrams/screenshots/FR-1-Metric-Collection.png)

```python
class MetricsCollector:
    def collect(self) -> Dict:
        return {
            'cpu':      self._query('avg(rate(node_cpu_seconds_total{mode!="idle"}[5m])) * 100'),
            'memory':   self._query('(1 - avg(node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100'),
            'pending':  self._query('sum(kube_pod_status_phase{phase="Pending"})'),
            'nodes':    self._query('count(kube_node_info)'),
            'net_rx':   self._query('sum(rate(node_network_receive_bytes_total{device!~"lo|veth.*"}[5m])) / 1048576'),
            'disk_r':   self._query('sum(rate(node_disk_read_bytes_total[5m])) / 1048576'),
        }
    
    def _query(self, promql: str) -> Optional[float]:
        resp = requests.get(
            f"{self.url}/api/v1/query",
            params={'query': promql},
            auth=(self.username, self.password),
            timeout=10
        )
        resp.raise_for_status()
        result = resp.json()['data']['result']
        return float(result[0]['value'][1]) if result else None
```

**Why `mode!="idle"`?** Captures system + user + iowait + irq — all active CPU modes. `idle` represents unused CPU and must be excluded.

**Why 5-minute rate window?** Smooths out sub-minute CPU spikes (e.g., garbage collection). Lambda polls every 2 minutes, so a 5-minute rate window overlaps between readings — this is intentional, providing temporal smoothing.

**Why `MemAvailable` not `MemFree`?** `MemAvailable` includes reclaimable page cache and buffer memory. `MemFree` is misleadingly low because Linux aggressively uses spare RAM for caching.

---

## 4. Decision Logic — Full Pseudocode

```python
def evaluate(metrics: Dict, history: List[Dict], custom_metrics: Dict) -> Tuple[str, str, int]:
    """
    Returns: (action, reason, increment)
    action: "scale_up" | "scale_down" | "none"
    """
    
    # ── SAFETY LAYER ─────────────────────────────────────────────────────────
    current_nodes = state_manager.get_node_count()
    
    if state_manager.is_locked():
        return "none", "locked", 0
    
    last_scale = state_manager.get_last_scale_time()
    last_action = state_manager.get_last_scale_action()
    
    if last_action == "scale_up" and (now - last_scale) < COOLDOWN_SCALE_UP:
        return "none", f"cooldown: {now - last_scale}s < {COOLDOWN_SCALE_UP}s", 0
    
    if last_action == "scale_down" and (now - last_scale) < COOLDOWN_SCALE_DOWN:
        return "none", f"cooldown: {now - last_scale}s < {COOLDOWN_SCALE_DOWN}s", 0
    
    # ── LAYER 1: REACTIVE SCALE-UP (OR logic) ────────────────────────────────
    
    # CPU: 3 consecutive readings above threshold
    cpu_history = [h['cpu'] for h in history[-3:]] + [metrics['cpu']]
    if len(cpu_history) >= 3 and all(c > SCALE_UP_THRESHOLD_CPU for c in cpu_history[-3:]):
        urgency = metrics['cpu'] > 85
        increment = 2 if urgency else 1
        if current_nodes + increment <= MAX_NODES:
            return "scale_up", f"CPU {metrics['cpu']:.1f}% [3/3 windows]", increment
    
    # Memory: 3 consecutive readings above threshold
    mem_history = [h['memory'] for h in history[-3:]] + [metrics['memory']]
    if len(mem_history) >= 3 and all(m > SCALE_UP_THRESHOLD_MEMORY for m in mem_history[-3:]):
        if current_nodes + 1 <= MAX_NODES:
            return "scale_up", f"Memory {metrics['memory']:.1f}% [3/3 windows]", 1
    
    # Pending pods: 2 consecutive non-zero readings
    pending_history = [h['pending'] for h in history[-2:]] + [metrics['pending']]
    if len(pending_history) >= 2 and all(p > 0 for p in pending_history[-2:]):
        urgency = metrics['pending'] > 5
        increment = 2 if urgency else 1
        if current_nodes + increment <= MAX_NODES:
            return "scale_up", f"pending_pods={metrics['pending']} [2/2 windows]", increment
    
    # ── LAYER 2: CUSTOM APP METRICS (OR logic) ───────────────────────────────
    if custom_metrics:
        if custom_metrics.get('queue_depth', 0) > QUEUE_DEPTH_THRESHOLD:   # 1000
            return "scale_up", f"queue={custom_metrics['queue_depth']}", 1
        
        if custom_metrics.get('latency_p95_ms', 0) > LATENCY_P95_THRESHOLD:  # 2000ms
            return "scale_up", f"latency_p95={custom_metrics['latency_p95_ms']:.0f}ms", 1
        
        if custom_metrics.get('error_rate', 0) > ERROR_RATE_THRESHOLD:  # 5.0%
            return "scale_up", f"error_rate={custom_metrics['error_rate']:.1f}%", 1
    
    # ── LAYER 3: PREDICTIVE (OR logic) ───────────────────────────────────────
    if ENABLE_PREDICTIVE_SCALING and metrics_history_table:
        prediction = predictive_engine.should_prescale(history_7d)
        if prediction['should_scale']:
            return "scale_up", f"predictive: {prediction['reason']}", 1
    
    # ── SCALE-DOWN (AND logic — ALL conditions required) ─────────────────────
    if current_nodes > MIN_NODES:
        last_5_cpu     = [h['cpu']     for h in history[-5:]] + [metrics['cpu']]
        last_5_memory  = [h['memory']  for h in history[-5:]] + [metrics['memory']]
        last_5_pending = [h['pending'] for h in history[-5:]] + [metrics['pending']]
        
        # [BONUS] queue depth guard: don't scale down if queue backlogged
        queue_ok = True
        if custom_metrics and ENABLE_QUEUE_SCALE_DOWN_GUARD:
            queue_ok = custom_metrics.get('queue_depth', 0) < QUEUE_DEPTH_SCALE_DOWN   # <100
        
        if (len(last_5_cpu) >= 5 and
            all(c < SCALE_DOWN_THRESHOLD_CPU     for c in last_5_cpu[-5:])     and  # <30%
            all(m < SCALE_DOWN_THRESHOLD_MEMORY  for m in last_5_memory[-5:])  and  # <50%
            all(p == 0                           for p in last_5_pending[-5:])  and  # =0
            queue_ok):                                                                # queue<100
            
            reason = f"CPU<{SCALE_DOWN_THRESHOLD_CPU}% [5/5], Memory<{SCALE_DOWN_THRESHOLD_MEMORY}% [5/5], pending=0 [5/5], queue<{QUEUE_DEPTH_SCALE_DOWN}"
            return "scale_down", reason, -1
    
    return "none", "stable", 0
```

---

## 5. Scale-Up Decision Examples

### Example 1: Normal Scale-Up (+1)

```
Reading 1 (T=0):   CPU=72%, memory=65%, pending=0  → window: [72]
Reading 2 (T+2m):  CPU=71%, memory=67%, pending=0  → window: [72, 71]
Reading 3 (T+4m):  CPU=74%, memory=68%, pending=0  → window: [72, 71, 74] → ALL > 70% ✅
Decision: scale_up +1, reason: "CPU 74.0% [3/3 windows]"
```

### Example 2: Window Broken — No Action

```
Reading 1: CPU=75%  → window: [75]
Reading 2: CPU=68%  → window: [75, 68] — window BROKEN (68% < 70%)
Reading 3: CPU=76%  → window: [75, 68, 76] — still broken at position 2
Reading 4: CPU=72%  → window: [68, 76, 72] — all > 70% from reading 2? No: 68 fails
Reading 5: CPU=73%  → window: [76, 72, 73] — ALL > 70% ✅ → scale_up
```
The window requires 3 **consecutive** readings. One low reading resets the window.

### Example 3: Urgent Scale-Up (+2)

```
CPU=87%, pending=7 → scale_up +2 (CPU>85% OR pending>5 — either alone triggers urgency)
```

### Example 4: Custom Metrics Override

```
CPU=45%, memory=50%, pending=0 → reactive: no scale
queue_depth=1200 → custom metrics: scale_up +1
reason: "queue=1200 > threshold 1000"
```

---

## 6. Scale-Down Decision Examples

### Example 1: Normal Scale-Down

```
Window (5 readings):
  [CPU=25%, Mem=38%, pending=0]  × 5 consecutive readings
  → ALL conditions satisfied → scale_down -1
```

### Example 2: One Condition Fails — No Action

```
Window:
  CPU=[22, 24, 28, 26, 21] → all < 30% ✅
  Memory=[45, 47, 52, 49, 48] → reading 3: 52% > 50% ✗
  → Scale-down BLOCKED (memory condition fails)
```

### Example 3: Window Not Complete

```
Only 4 readings available (Lambda just restarted or cooldown reset window)
→ Insufficient history → no scale-down
```

---

## 7. Predictive Scaling

### How It Works

1. **History storage**: Every Lambda invocation stores metrics in `k3s-metrics-history` DynamoDB table with timestamp
2. **Pattern detection**: For the current hour-of-day and day-of-week, compute the rolling 7-day average CPU at `(current_hour + 10_minutes)`
3. **Pre-scale trigger**: If predicted CPU at `now+10min` exceeds 70%, scale up now (10 minutes early)
4. **Always active**: Runs on every invocation when `action == "none"` (reactive layer found no immediate trigger)

```python
def should_prescale(history_7d: List[Dict]) -> Dict:
    now = datetime.now(timezone.utc)
    target_hour = now.hour
    target_minute = (now.minute + 10) % 60
    day_of_week = now.weekday()
    
    # Find historical readings at same time (±15 minutes) over last 7 days
    same_time_readings = [
        h['cpu'] for h in history_7d
        if abs(parse_ts(h['timestamp']).hour - target_hour) == 0
        and abs(parse_ts(h['timestamp']).minute - target_minute) <= 15
    ]
    
    if len(same_time_readings) < 3:
        return {'should_scale': False, 'reason': 'insufficient history'}
    
    avg_cpu_at_target = sum(same_time_readings) / len(same_time_readings)
    
    if avg_cpu_at_target > SCALE_UP_THRESHOLD_CPU:
        return {
            'should_scale': True,
            'reason': f"7-day avg CPU at {target_hour}:{target_minute:02d} = {avg_cpu_at_target:.1f}% > 70%",
            'confidence': len(same_time_readings)
        }
    
    return {'should_scale': False, 'reason': f"predicted CPU {avg_cpu_at_target:.1f}% < 70%"}
```

**Practical impact**: Pre-scales before the 9AM business-hours rush and before known Friday flash sales. Nodes are Ready before demand hits, preventing the 6-minute window tax (3 readings × 2 minutes) during the spike.

---

## 8. Node Selection for Scale-Down

When scale-down is triggered, the following steps select which node to drain:

```python
def select_drain_candidate(workers: List[Dict]) -> Optional[str]:
    """Returns instance_id of node to drain, or None if no safe candidate."""
    
    # Step 1: Check each worker for critical pods
    eligible = []
    for worker in workers:
        has_critical, reason = _check_critical_pods(worker['instance_id'], worker['node_name'])
        if not has_critical:
            eligible.append(worker)
        else:
            logger.info(f"Skipping {worker['node_name']}: {reason}")
    
    if not eligible:
        logger.warning("No eligible nodes for scale-down (all have critical pods)")
        return None
    
    # Step 2: Prefer removing from AZ with most workers (maintain Multi-AZ balance)
    az_counts = Counter(w['az'] for w in eligible)
    max_az = max(az_counts, key=az_counts.get)
    az_candidates = [w for w in eligible if w['az'] == max_az]
    
    # Step 3: From that AZ, pick node with fewest running pods
    return min(az_candidates, key=lambda w: w['pod_count'])['instance_id']
```

**Critical pod categories** (checked via SSM `kubectl get pods` on master):

| Category | Check | Why Protected |
|----------|-------|---------------|
| StatefulSet pods | Pod has `ownerReference.kind=StatefulSet` | Stateful data; no safe replica elsewhere |
| kube-system non-DaemonSet | Namespace=kube-system AND not DaemonSet-owned | CoreDNS, metrics-server — loss breaks cluster |
| Single-replica Deployments | Deployment with `replicas=1` | No redundancy; eviction = downtime |
| DaemonSet pods | **Not** protected | DaemonSet re-schedules immediately on other nodes |

---

## 9. Dynamic Scheduling

`lambda/dynamic_scheduler.py` adjusts the EventBridge rule rate at runtime based on conditions:

| Condition | Rate | Rationale |
|-----------|------|-----------|
| Peak hours (9AM–9PM) or CPU>60% | 1 min | Faster response during business hours |
| Normal (CPU 30–60%) | 2 min | Default — balance cost vs responsiveness |
| Off-peak (9PM–9AM) AND CPU<30% | 5 min | Reduce Lambda invocations; saves ~$0.30/mo |

Lambda calls `events:put_rule` to update the schedule after each stable invocation. Cooldown on schedule changes: 30 minutes (prevents thrashing).

---

## 10. Safety Mechanisms Summary

| Mechanism | Implementation | Protects Against |
|-----------|----------------|-----------------|
| Min node floor | Check before any scale-down decision | Never going below 2 workers |
| Max node ceiling | Check before any scale-up decision | EC2 quota exhaustion + cost runaway |
| DynamoDB lock | Conditional write; single holder | Concurrent Lambda race condition |
| Lock expiry (360s) | TTL-based auto-clear | Lambda crash leaving orphaned lock |
| Drain validation | exit_code==0 AND "drained" keyword | Premature termination of partially-drained node |
| Drain timeout (300s) | SSM command timeout | Hung drain — aborts rather than force-terminates |
| Critical pod protection | SSM check before drain | StatefulSet data loss + CoreDNS outage |
| Cooldown periods | 300s/600s per action | Thrashing (scale-up → scale-down → scale-up) |
| Node join validation | EC2 running + elapsed >= 120s guard; abandon after 10 min (600s) | Premature confirmation before K8s join completes |
| Async drain (SSM) | Initiated N, completed N+1 | Lambda timeout during long drain operations |
| Finally block | Lock release unconditional | Exception leaving lock permanently held |
