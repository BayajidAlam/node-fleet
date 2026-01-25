#!/bin/bash
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

echo "=== 1. Checking Pod Status ==="
kubectl get pods -n monitoring

echo -e "\n=== 2. Checking Grafana Datasource Configuration (Inside Pod) ==="
GRAF_POD=$(kubectl get pods -n monitoring -l app.kubernetes.io/name=grafana -o jsonpath="{.items[0].metadata.name}")
echo "Grafana Pod: $GRAF_POD"
kubectl exec -n monitoring $GRAF_POD -- cat /etc/grafana/provisioning/datasources/datasources.yaml

echo -e "\n=== 3. Testing Prometheus Connectivity from Grafana Pod ==="
# Extract creds from the datasource file we just read (or just try the known good ones)
# We test access using the 'admin/prom-operator' credentials
kubectl exec -n monitoring $GRAF_POD -- wget -qO- --user=admin --password=prom-operator http://prometheus:9090/api/v1/query?query=up > /tmp/prom_test.json
if [ -s /tmp/prom_test.json ]; then
  echo "SUCCESS: Connected to Prometheus. Response size: $(wc -c < /tmp/prom_test.json)"
  head -c 100 /tmp/prom_test.json
else
  echo "FAILURE: Could not connect to Prometheus (or Empty Response)"
fi

echo -e "\n=== 4. Checking IAM Role on Worker Node ==="
# We are running this on Master, but let's check what the Master sees about Workers
WORKER_NODE=$(kubectl get nodes --no-headers | grep -v master | head -n1 | awk '{print $1}')
echo "Worker Node: $WORKER_NODE"
# Inspect Instance Profile via AWS CLI (runs on Host)
INSTANCE_ID=$(aws ec2 describe-instances --filters "Name=tag:Name,Values=*worker*" --query "Reservations[0].Instances[0].InstanceId" --output text)
echo "Worker Instance ID: $INSTANCE_ID"
ROLE_NAME=$(aws ec2 describe-instances --instance-ids $INSTANCE_ID --query "Reservations[0].Instances[0].IamInstanceProfile.Arn" --output text | awk -F/ '{print $2}')
PROFILE_NAME=$ROLE_NAME # Usually profile name matches last part of ARN
# Get Role Name from Profile
REAL_ROLE_NAME=$(aws iam get-instance-profile --instance-profile-name $PROFILE_NAME --query "InstanceProfile.Roles[0].RoleName" --output text)
echo "Worker Role Name: $REAL_ROLE_NAME"
# Check Policy
echo "Checking for CloudWatchReadOnlyAccess..."
aws iam list-attached-role-policies --role-name $REAL_ROLE_NAME | grep CloudWatchReadOnly

echo -e "\n=== 5. Testing CloudWatch Access (From Host) ==="
# Try listing metrics
aws cloudwatch list-metrics --namespace AWS/Lambda --region ap-southeast-1 --max-items 1 > /dev/null
if [ $? -eq 0 ]; then
   echo "SUCCESS: Host can list CloudWatch metrics."
else
   echo "FAILURE: Host cannot list CloudWatch metrics."
fi

echo -e "\n=== END REPORT ==="
