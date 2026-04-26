---
name: aws-cloud-patterns
version: 1.0.0
description: AWS patterns for node-fleet. EC2 launch/terminate boto3, DynamoDB conditional lock, SSM async ops, Secrets Manager credential retrieval, CloudWatch custom metrics, IAM least-privilege.
---

> Read `.agents/CONTEXT.md` first — architecture, scaling rules, secrets paths.

# AWS Cloud Patterns

## When to Use

- Writing/modifying boto3 code in `lambda/`
- Adding AWS service integrations
- Reviewing IAM in `pulumi/src/iam.ts`
- DynamoDB locking or state storage
- SSM Run Command or Secrets Manager usage

---

## 1. EC2 Launch: Launch Template + Tag Immediately

Tags on launch enable EC2 tag-based lookups (node count, role filter). Launch Templates version instance config.

```python
def launch_worker(self, subnet_id, use_spot=False):
    template_id = (os.environ['WORKER_SPOT_TEMPLATE_ID'] if use_spot
                   else os.environ['WORKER_LAUNCH_TEMPLATE_ID'])

    response = self.ec2.run_instances(
        MinCount=1, MaxCount=1,
        LaunchTemplate={'LaunchTemplateId': template_id, 'Version': '$Latest'},
        SubnetId=subnet_id,
        TagSpecifications=[{
            'ResourceType': 'instance',
            'Tags': [
                {'Key': 'Role',       'Value': 'k3s-worker'},
                {'Key': 'ClusterId',  'Value': os.environ['CLUSTER_ID']},
                {'Key': 'ManagedBy',  'Value': 'autoscaler'},
                {'Key': 'LaunchTime', 'Value': datetime.utcnow().isoformat()},
            ]
        }]
    )
    return response['Instances'][0]
```

Rules: tag `Role`, `ClusterId`, `ManagedBy` on every launch; Spot via separate Launch Template; on Spot interruption warning drain immediately.

---

## 2. DynamoDB Conditional Lock

EventBridge = at-least-once. Two Lambda invocations can fire simultaneously. Conditional write ensures only one acquires lock.

```python
def acquire_lock(self, cluster_id):
    now = int(datetime.now(timezone.utc).timestamp())
    expiry = now + 360  # 360s = 300s drain + join buffer

    try:
        self.dynamodb.update_item(
            TableName=self.table_name,
            Key={'cluster_id': {'S': cluster_id}},
            UpdateExpression='SET scaling_in_progress = :true, lock_acquired_at = :now, lock_expiry = :exp',
            ConditionExpression=(
                'attribute_not_exists(scaling_in_progress) OR '
                'scaling_in_progress = :false OR '
                'lock_expiry < :now'   # auto-clear stale lock
            ),
            ExpressionAttributeValues={
                ':true':  {'BOOL': True},
                ':false': {'BOOL': False},
                ':now':   {'N': str(now)},
                ':exp':   {'N': str(expiry)},
            }
        )
        return True
    except self.dynamodb.exceptions.ConditionalCheckFailedException:
        return False

def release_lock(self, cluster_id):
    self.dynamodb.update_item(
        TableName=self.table_name,
        Key={'cluster_id': {'S': cluster_id}},
        UpdateExpression='SET scaling_in_progress = :false REMOVE lock_acquired_at, lock_expiry',
        ExpressionAttributeValues={':false': {'BOOL': False}}
    )
```

Invariants: lock expiry = **360s** (never 120s or 600s); stale lock condition must include `lock_expiry < :now`; release in `finally` block always.

---

## 3. SSM Run Command: Async kubectl

Lambda can't SSH directly to private-subnet master. SSM tunnels through AWS APIs — no inbound port.

```python
def ssm_run_kubectl(self, master_instance_id, kubectl_cmd):
    response = self.ssm.send_command(
        InstanceIds=[master_instance_id],
        DocumentName='AWS-RunShellScript',
        Parameters={'commands': [f'export KUBECONFIG=/etc/rancher/k3s/k3s.yaml && {kubectl_cmd}']},
        TimeoutSeconds=360,
    )
    return response['Command']['CommandId']

def check_ssm_command(self, command_id, instance_id):
    result = self.ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
    return {
        'status':    result['Status'],                  # InProgress/Success/Failed/Cancelled
        'output':    result['StandardOutputContent'],
        'error':     result['StandardErrorContent'],
        'exit_code': result.get('ResponseCode', -1)
    }
```

IAM: Lambda role needs `ssm:SendCommand`, `ssm:GetCommandInvocation`, `ssm:ListCommandInvocations`. Master EC2 profile needs `AmazonSSMManagedInstanceCore`.

---

## 4. Secrets Manager: Fail-Fast, No Hardcoded Fallback

Hardcoded fallback defeats secrets management. Fail loud, not silent.

```python
def get_prometheus_credentials():
    try:
        sm = boto3.client('secretsmanager')
        secret = sm.get_secret_value(SecretId='node-fleet/prometheus-auth')
        creds = json.loads(secret['SecretString'])
        return creds['username'], creds['password']
    except Exception:
        pass
    # Env var fallback for CI/test only
    u = os.environ.get('PROMETHEUS_USERNAME')
    p = os.environ.get('PROMETHEUS_PASSWORD')
    if not u or not p:
        raise ValueError("Prometheus credentials unavailable: Secrets Manager failed, env vars not set")
    return u, p
```

| Secret | Keys | Used by |
|--------|------|---------|
| `node-fleet/k3s-token` | `token` | worker UserData |
| `node-fleet/prometheus-auth` | `username`, `password` | Lambda autoscaler |
| `node-fleet/ssh-key` | `private_key` | Lambda SSH to master |
| `node-fleet/slack-webhook` | `url` | Slack notifier |

---

## 5. CloudWatch Custom Metrics

Logs alone = insufficient. Metrics enable dashboards + alarms on scaling failures and capacity limits.

```python
def _publish_metrics(self, node_count, cpu, action):
    cloudwatch.put_metric_data(
        Namespace='NodeFleet/Autoscaler',
        MetricData=[
            {'MetricName': 'NodeCount',        'Value': node_count, 'Unit': 'Count',
             'Dimensions': [{'Name': 'ClusterId', 'Value': CLUSTER_ID}]},
            {'MetricName': 'CPUUtilization',   'Value': cpu,        'Unit': 'Percent',
             'Dimensions': [{'Name': 'ClusterId', 'Value': CLUSTER_ID}]},
            {'MetricName': 'ScalingAction',    'Value': 1 if action != 'none' else 0, 'Unit': 'Count',
             'Dimensions': [{'Name': 'ClusterId', 'Value': CLUSTER_ID},
                             {'Name': 'Action', 'Value': action}]},
        ]
    )
```

| Alarm | Metric | Threshold | Action |
|-------|--------|-----------|--------|
| ScalingFailure | Lambda Errors | ≥1 in 5min | SNS urgent |
| CPUEmergency | CPUUtilization | >90% 5min | SNS urgent |
| MaxCapacity | NodeCount | =10 for 10min | SNS warn |
| NodeJoinTimeout | NodeJoinLatency | >300s | SNS alert |

---

## 6. EC2 Tag Lookup for Ground Truth

DynamoDB state can drift if Lambda crashes mid-operation. EC2 tags = ground truth.

```python
def get_actual_worker_count(self):
    response = self.ec2.describe_instances(
        Filters=[
            {'Name': 'tag:Role',          'Values': ['k3s-worker']},
            {'Name': 'tag:ClusterId',     'Values': [self.cluster_id]},
            {'Name': 'instance-state-name', 'Values': ['running', 'pending']}
        ]
    )
    return sum(len(r['Instances']) for r in response['Reservations'])
```

Reconcile DynamoDB `node_count` with EC2 tags at start of each invocation.

---

## IAM Least-Privilege (`pulumi/src/iam.ts`)

```typescript
ec2:           ["RunInstances", "TerminateInstances", "DescribeInstances",
                "DescribeInstanceStatus", "CreateTags"]
dynamodb:      ["GetItem", "PutItem", "UpdateItem", "Query"]
secretsmanager:["GetSecretValue"]
ssm:           ["SendCommand", "GetCommandInvocation", "ListCommandInvocations"]
cloudwatch:    ["PutMetricData"]
logs:          ["CreateLogGroup", "CreateLogStream", "PutLogEvents"]
sns:           ["Publish"]
sqs:           ["SendMessage"]  // DLQ
```

Scope `ec2:TerminateInstances` to tag condition `ManagedBy=autoscaler` — prevents terminating unrelated instances.

## Checklist

- [ ] DynamoDB lock uses `ConditionalCheckFailedException`
- [ ] Lock expiry = 360s; stale lock auto-cleared in condition expression
- [ ] `release_lock()` in `finally` block
- [ ] Secrets Manager path used — no hardcoded credential fallback
- [ ] EC2 instances tagged `Role`, `ClusterId`, `ManagedBy` on launch
- [ ] CloudWatch metrics use consistent `ClusterId` dimension
- [ ] IAM `ec2:TerminateInstances` scoped to `ManagedBy=autoscaler` tag
