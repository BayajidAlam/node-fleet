---
name: python-lambda-backend
version: 1.0.0
description: Python Lambda patterns for node-fleet. Scaling decision logic, window math, cooldown, DynamoDB state schema, async drain state, packaging, testing.
---

> Read `.agents/CONTEXT.md` first — architecture, scaling rules, secrets paths.

# Python Lambda Backend

## When to Use

- Modifying any `lambda/*.py` file
- Changing thresholds/window logic in `lambda/scaling_decision.py`
- Adding bonus modules (predictive, custom metrics, etc.)
- Writing/fixing `tests/lambda/` tests
- Packaging Lambda for deployment

---

## 1. Scaling Decision: Window Math Is Exact

`window=3` at 2-min intervals = 6 min, not 3 min. Match spec exactly.

**Canonical constants** (`lambda/scaling_decision.py`):
```python
CPU_SCALE_UP_THRESHOLD      = 70.0
CPU_SCALE_DOWN_THRESHOLD    = 30.0
MEMORY_SCALE_UP_THRESHOLD   = 75.0
MEMORY_SCALE_DOWN_THRESHOLD = 50.0
SCALE_UP_COOLDOWN   = 300   # 5 min
SCALE_DOWN_COOLDOWN = 600   # 10 min

SCALE_UP_WINDOW   = 3  # 3 readings × 2min = ~6min
SCALE_DOWN_WINDOW = 5  # 5 readings × 2min = 10min
PENDING_WINDOW    = 2  # 2 readings × 2min = ~4min (closest to 3-min spec)
```

**Scale-up** (ANY condition):
```python
def should_scale_up(self, history):
    if len(history) < SCALE_UP_WINDOW:
        return False, "insufficient_history"
    recent = history[-SCALE_UP_WINDOW:]
    if all(r['cpu'] > CPU_SCALE_UP_THRESHOLD for r in recent):
        return True, f"cpu_sustained_{CPU_SCALE_UP_THRESHOLD}"
    if all(r['memory'] > MEMORY_SCALE_UP_THRESHOLD for r in recent):
        return True, f"memory_sustained_{MEMORY_SCALE_UP_THRESHOLD}"
    recent_p = history[-PENDING_WINDOW:]
    if len(recent_p) >= PENDING_WINDOW and all(r['pending_pods'] > 0 for r in recent_p):
        return True, "pending_pods_sustained"
    return False, "below_threshold"
```

**Scale-down** (ALL conditions):
```python
def should_scale_down(self, history):
    if len(history) < SCALE_DOWN_WINDOW:
        return False, "insufficient_history"
    recent = history[-SCALE_DOWN_WINDOW:]
    if (all(r['cpu'] < CPU_SCALE_DOWN_THRESHOLD for r in recent) and
        all(r['memory'] < MEMORY_SCALE_DOWN_THRESHOLD for r in recent) and
        all(r['pending_pods'] == 0 for r in recent)):
        return True, "all_metrics_low"
    return False, "not_all_conditions_met"
```

---

## 2. Cooldown: Check Before Lock, Record After Action

Prevents oscillation (scale up → down → up within minutes).

```python
def is_in_cooldown(self, state, action):
    now = datetime.now(timezone.utc).timestamp()
    elapsed = now - float(state.get('last_scale_time', 0))
    cooldown = SCALE_UP_COOLDOWN if action == 'scale_up' else SCALE_DOWN_COOLDOWN
    return elapsed < cooldown

def record_scaling_action(self, state_manager, cluster_id, action, node_count):
    state_manager.update_state(cluster_id, {
        'last_scale_time':   str(int(datetime.now(timezone.utc).timestamp())),
        'last_scale_action': action,
        'node_count':        node_count,
    })
```

Check cooldown BEFORE acquiring DynamoDB lock — avoids unnecessary lock contention.

---

## 3. Metrics History in DynamoDB

Single-reading decisions are noisy. History enables window-based decisions.

**Schema** (`METRICS_HISTORY_TABLE`):
```
PK: cluster_id (String)
SK: timestamp  (Number — Unix epoch)
Attrs: cpu, memory, pending_pods, node_count
TTL: expires_at (now + 3600 — only need last 5 readings)
```

```python
def store_metrics(self, cluster_id, metrics):
    now = int(datetime.now(timezone.utc).timestamp())
    self.dynamodb.put_item(
        TableName=self.metrics_table,
        Item={
            'cluster_id':   {'S': cluster_id},
            'timestamp':    {'N': str(now)},
            'cpu':          {'N': str(metrics['cpu'])},
            'memory':       {'N': str(metrics['memory'])},
            'pending_pods': {'N': str(metrics['pending_pods'])},
            'node_count':   {'N': str(metrics['node_count'])},
            'expires_at':   {'N': str(now + 3600)},
        }
    )

def get_recent_metrics(self, cluster_id, limit=10):
    response = self.dynamodb.query(
        TableName=self.metrics_table,
        KeyConditionExpression='cluster_id = :cid',
        ExpressionAttributeValues={':cid': {'S': cluster_id}},
        ScanIndexForward=False,
        Limit=limit
    )
    items = sorted(response['Items'], key=lambda x: x['timestamp']['N'])
    return [{'cpu': float(i['cpu']['N']), 'memory': float(i['memory']['N']),
             'pending_pods': int(i['pending_pods']['N']),
             'timestamp': int(i['timestamp']['N'])} for i in items]
```

---

## 4. Nodes to Add on Scale-Up

```python
def nodes_to_add(self, metrics):
    if metrics.get('cpu', 0) > 85.0 or metrics.get('pending_pods', 0) > 5:
        return 2  # flash sale / extreme load
    return 1      # default
```

Scale-down: always remove exactly 1 node, never bulk.

---

## 5. Lambda Packaging (Windows-Compatible)

```bash
cd lambda
pip install -r requirements.txt -t .
zip -r ../function.zip . \
  --exclude "*.pyc" \
  --exclude "__pycache__/*" \
  --exclude "venv/*" \
  --exclude "tests/*" \
  --exclude "*.egg-info/*"
cd ..
aws lambda update-function-code \
  --function-name node-fleet-cluster-autoscaler \
  --zip-file fileb://function.zip
```

Required env vars:
```
CLUSTER_ID  PROMETHEUS_URL  STATE_TABLE  METRICS_HISTORY_TABLE
MIN_NODES  MAX_NODES  WORKER_LAUNCH_TEMPLATE_ID  WORKER_SPOT_TEMPLATE_ID
SPOT_PERCENTAGE  ENABLE_PREDICTIVE_SCALING  ENABLE_CUSTOM_METRICS
```

---

## 6. Testing with moto

```python
from moto import mock_ec2, mock_dynamodb

@mock_dynamodb
def test_acquire_lock_prevents_concurrent_scaling():
    from state_manager import StateManager
    sm = StateManager(table_name='k3s-autoscaler-state', region='ap-southeast-1')
    sm.create_table()
    assert sm.acquire_lock('test-cluster') == True
    assert sm.acquire_lock('test-cluster') == False  # blocked

def test_scale_up_on_sustained_cpu():
    from scaling_decision import ScalingDecision
    sd = ScalingDecision()
    history = [
        {'cpu': 75.0, 'memory': 60.0, 'pending_pods': 0},
        {'cpu': 78.0, 'memory': 62.0, 'pending_pods': 0},
        {'cpu': 80.0, 'memory': 61.0, 'pending_pods': 0},
    ]
    should, reason = sd.should_scale_up(history)
    assert should == True and 'cpu' in reason
```

```bash
cd tests/lambda
pip install -r requirements.txt
python -m pytest . -v --tb=short
python -m pytest . --cov=lambda --cov-report=html
```

---

## Quick Reference

| Constant | Value | File |
|----------|-------|------|
| CPU scale-up | >70% | `scaling_decision.py` |
| CPU scale-down | <30% | `scaling_decision.py` |
| Memory scale-up | >75% | `scaling_decision.py` |
| Memory scale-down | <50% | `scaling_decision.py` |
| Scale-up window | 3 readings | `scaling_decision.py` |
| Scale-down window | 5 readings | `scaling_decision.py` |
| Pending pods window | 2 readings | `scaling_decision.py` |
| Scale-up cooldown | 300s | `scaling_decision.py` |
| Scale-down cooldown | 600s | `scaling_decision.py` |
| Lock expiry | 360s | `state_manager.py` |
| Min workers | 2 | env var |
| Max workers | 10 | env var |

## Checklist

- [ ] All threshold constants defined once in `scaling_decision.py`
- [ ] Window math commented: `window=5` × 2min = 10min
- [ ] Scale-up adds 2 only if CPU >85% OR pending_pods >5
- [ ] Scale-down removes exactly 1 node
- [ ] Cooldown checked BEFORE lock acquisition
- [ ] Metrics TTL = 3600s (prevents unbounded growth)
- [ ] Tests use moto — no real AWS calls in unit tests
