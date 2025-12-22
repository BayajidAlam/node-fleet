# SmartScale K3s Autoscaler - Complete Implementation Plan

## Project Overview

**Goal:** Build production-ready K3s autoscaler for AWS that reduces infrastructure costs by 60-70% through intelligent, automated scaling with Spot instances, Multi-AZ resilience, predictive scaling, and GitOps.

**Timeline:** 10-12 weeks  
**Effort:** ~160-180 hours  
**Team:** 1-2 developers  
**Automation:** 100% - Zero manual steps required  
**Features:** All core requirements + ALL bonus features (Multi-AZ, Spot instances, Predictive scaling, GitOps, Slack notifications)

---

## Phase 1: Infrastructure Foundation (Week 1-2)

**Goal:** Set up base AWS infrastructure with Pulumi (TypeScript)

### Week 1: VPC, Networking & Security

#### 1.1 Project Initialization (2 hours)

**Automated Setup Script:** `scripts/init-project.sh`

```bash
#!/bin/bash
set -e

echo "🚀 Initializing SmartScale project..."

# Create directory structure
mkdir -p pulumi lambda k3s tests monitoring demo-app docs scripts

# Initialize Pulumi
cd pulumi
npm init -y
npm install @pulumi/pulumi @pulumi/aws @pulumi/tls @pulumi/random typescript @types/node

# Create tsconfig
npx tsc --init --target ES2020 --module commonjs --outDir dist

# Initialize Pulumi stack
pulumi login
pulumi stack init smartscale-dev --non-interactive || pulumi stack select smartscale-dev

# Configure via Pulumi config (no manual input)
pulumi config set aws:region ap-south-1
pulumi config set smartscale:clusterName "smartscale-prod"
pulumi config set smartscale:minNodes 2
pulumi config set smartscale:maxNodes 10
pulumi config set --secret smartscale:slackWebhookUrl "https://hooks.slack.com/services/YOUR/WEBHOOK/URL"

# Install Python dependencies
cd ../lambda
python3.11 -m venv venv
source venv/bin/activate
pip install boto3 requests

# Install test dependencies
cd ../tests
npm install

cd ..
echo "✅ Project initialized! Run 'cd pulumi && pulumi up' to deploy."
```

**Run once:**

```bash
chmod +x scripts/init-project.sh
./scripts/init-project.sh
```

#### 1.2 VPC & Networking (4 hours)

**File:** `pulumi/vpc.ts`

```typescript
import * as aws from "@pulumi/aws";

// VPC with public subnets (cost-optimized, no NAT Gateway)
export const vpc = new aws.ec2.Vpc("smartscale-vpc", {
  cidrBlock: "10.0.0.0/16",
  enableDnsHostnames: true,
  enableDnsSupport: true,
  tags: { Name: "smartscale-vpc" },
});

// Internet Gateway
export const igw = new aws.ec2.InternetGateway("smartscale-igw", {
  vpcId: vpc.id,
  tags: { Name: "smartscale-igw" },
});

// Public subnets in 2 AZs
export const publicSubnet1 = new aws.ec2.Subnet("public-subnet-1", {
  vpcId: vpc.id,
  cidrBlock: "10.0.1.0/24",
  availabilityZone: "ap-south-1a",
  mapPublicIpOnLaunch: true,
  tags: { Name: "smartscale-public-1" },
});

export const publicSubnet2 = new aws.ec2.Subnet("public-subnet-2", {
  vpcId: vpc.id,
  cidrBlock: "10.0.2.0/24",
  availabilityZone: "ap-south-1b",
  mapPublicIpOnLaunch: true,
  tags: { Name: "smartscale-public-2" },
});

// Route table
export const publicRouteTable = new aws.ec2.RouteTable("public-rt", {
  vpcId: vpc.id,
  routes: [
    {
      cidrBlock: "0.0.0.0/0",
      gatewayId: igw.id,
    },
  ],
  tags: { Name: "smartscale-public-rt" },
});

// Route table associations
new aws.ec2.RouteTableAssociation("public-rta-1", {
  subnetId: publicSubnet1.id,
  routeTableId: publicRouteTable.id,
});

new aws.ec2.RouteTableAssociation("public-rta-2", {
  subnetId: publicSubnet2.id,
  routeTableId: publicRouteTable.id,
});
```

#### 1.3 Security Groups (2 hours)

**File:** `pulumi/security.ts`

````typescript
import * as aws from "@pulumi/aws";
import { vpc } from "./vpc";

// Master node security group
export const masterSg = new aws.ec2.SecurityGroup("k3s-master-sg", {
    vpcId: vpc.id,
    description: "K3s master node security group",
    ingress: [
        // SSH access
        { protocol: "tcp", fromPort: 22, toPort: 22, cidrBlocks: ["0.0.0.0/0"] },
        // K3s API server
        { protocol: "tcp", fromPort: 6443, toPort: 6443, cidrBlocks: ["10.0.0.0/16"] },
        // Prometheus NodePort
        { protocol: "tcp", fromPort: 30090, toPort: 30090, cidrBlocks: ["10.0.0.0/16"] },
        // Grafana
        { protocol: "tcp", fromPort: 3000, toPort: 3000, cidrBlocks: ["0.0.0.0/0"] },
        // Demo app NodePort
        { protocol: "tcp", fromPort: 30080, toPort: 30080, cidrBlocks: ["0.0.0.0/0"] },
    ],
    egress: [
        { protocol: "-1", fromPort: 0, toPort: 0, cidrBlocks: ["0.0.0.0/0"] }
    ],
    tags: { Name: "k3s-master-sg" }
});

// Worker node security group
export const workerSg = new aws.ec2.SecurityGroup("k3s-worker-sg", {
    vpcId: vpc.id,
    description: "K3s worker node security group",
    ingress: [
       3.1 SSH Key Pair (Automated) (0.5 hours)

**File:** `pulumi/keypair.ts`

```typescript
import * as aws from "@pulumi/aws";
import * as tls from "@pulumi/tls";
import * as fs from "fs";

// Generate SSH key pair automatically
const sshKey = new tls.PrivateKey("smartscale-ssh-key", {
    algorithm: "RSA",
    rsaBits: 4096
});

// Register key pair with AWS
export const keyPair = new aws.ec2.KeyPair("smartscale-key", {
    publicKey: sshKey.publicKeyOpenssh,
    keyName: "smartscale-key"
});

// Save private key to local file (for emergency SSH access)
sshKey.privateKeyPem.apply(key => {
    fs.writeFileSync("../smartscale-key.pem", key, { mode: 0o600 });
});

export const keyName = keyPair.keyName;
````

#### 1. // SSH access

        { protocol: "tcp", fromPort: 22, toPort: 22, cidrBlocks: ["0.0.0.0/0"] },
        // Kubelet API
        { protocol: "tcp", fromPort: 10250, toPort: 10250, cidrBlocks: ["10.0.0.0/16"] },
        // K3s from master
        { protocol: "tcp", fromPort: 6443, toPort: 6443, cidrBlocks: ["10.0.0.0/16"] },
        // All traffic within VPC (for pod networking)
        { protocol: "-1", fromPort: 0, toPort: 0, cidrBlocks: ["10.0.0.0/16"] },
    ],
    egress: [
        { protocol: "-1", fromPort: 0, toPort: 0, cidrBlocks: ["0.0.0.0/0"] }
    ],
    tags: { Name: "k3s-worker-sg" }

});

// Lambda security group
export const lambdaSg = new aws.ec2.SecurityGroup("lambda-sg", {
vpcId: vpc.id,
description: "Lambda autoscaler security group",
egress: [
{ protocol: "-1", fromPort: 0, toPort: 0, cidrBlocks: ["0.0.0.0/0"] }
],
tags: { Name: "lambda-sg" }
});

````

#### 1.4 IAM Roles (2 hours)

**File:** `pulumi/iam.ts`

```typescript
import * as aws from "@pulumi/aws";

// Master node IAM role
const masterRole = new aws.iam.Role("k3s-master-role", {
    assumeRolePolicy: JSON.stringify({
        Version: "2012-10-17",
        Statement: [{
            Action: "sts:AssumeRole",
            Effect: "Allow",
            Principal: { Service: "ec2.amazonaws.com" }
        }]
    })
});

new aws.iam.RolePolicyAttachment("master-ssm-policy", {
    role: masterRole.name,
    policyArn: "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
});

export const masterInstanceProfile = new aws.iam.InstanceProfile("master-profile", {
    role: masterRole.name
});

// Worker node IAM role
const workerRole = new aws.iam.Role("k3s-worker-role", {
    assumeRolePolicy: JSON.stringify({
        Version: "2012-10-17",
        Statement: [{
            Action: "sts:AssumeRole",
            Effect: "Allow",
            Principal: { Service: "ec2.amazonaws.com" }
        }]
    })
});

const workerPolicy = new aws.iam.Policy("worker-policy", {
    policy: JSON.stringify({
        Version: "2012-10-17",
        Statement: [
            {
                Effect: "Allow",
                Action: ["secretsmanager:GetSecretValue"],
                Resource: "*" // Will be restricted to specific secret
            },
            {
                Effect: "Allow",
                Action: ["ec2:DescribeInstances", "ec2:DescribeTags"],
                Resource: "*"
            }
        ]
    })
});

new aws.iam.RolePolicyAttachment("worker-policy-attach", {
    role: workerRole.name,
    policyArn: workerPolicy.arn
});

new aws.iam.RolePolicyAttachment("worker-ssm-policy", {
    role: workerRole.name,
    policyArn: "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
});

export const workerInstanceProfile = new aws.iam.InstanceProfile("worker-profile", {
    role: workerRole.name
});

// Lambda execution role
const lambdaRole = new aws.iam.Role("autoscaler-lambda-role", {
    assumeRolePolicy: JSON.stringify({
        Version: "2012-10-17",
        Statement: [{
            Action: "sts:AssumeRole",
            Effect: "Allow",
            Principal: { Service: "lambda.amazonaws.com" }
        }]
    })
});

const lambdaPolicy = new aws.iam.Policy("lambda-autoscaler-policy", {
    policy: JSON.stringify({
        Version: "2012-10-17",
        Statement: [
            {
                Effect: "Allow",
                Action: [
                    "ec2:RunInstances",
                    "ec2:TerminateInstances",
                    "ec2:DescribeInstances",
                    "ec2:CreateTags"
                ],
                Resource: "*"
            },
            {
                Effect: "Allow",
                Action: [
                    "dynamodb:PutItem",
                    "dynamodb:GetItem",
                    "dynamodb:UpdateItem",
                    "dynamodb:DeleteItem"
                ],
                Resource: "*" // Will be restricted to specific table
            },
            {
                Effect: "Allow",
                Action: ["secretsmanager:GetSecretValue"],
                Resource: "*"
            },
            {
                Effect: "Allow",
                Action: ["sns:Publish"],
                Resource: "*"
            },
            {
                Effect: "Allow",
                Action: [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents"
                ],
                Resource: "*"
            },
            {
                Effect: "Allow",
                Action: [
                    "ec2:CreateNetworkInterface",
                    "ec2:DescribeNetworkInterfaces",
                    "ec2:DeleteNetworkInterface"
                ],
                Resource: "*"
            }
        ]
    })
});

new aws.iam.RolePolicyAttachment("lambda-policy-attach", {
    role: lambdaRole.name,
    policyArn: lambdaPolicy.arn
});

export { lambdaRole };
````

**Validation:**

```bash
cd pulumi
pulumi up --yes

# Verify resources created
aws ec2 describe-vpcs --filters "Name=tag:Name,Values=smartscale-vpc"
aws ec2 describe-security-groups --filters "Name=tag:Name,Values=k3s-master-sg"
aws iam list-roles | grep k3s
```

### Week 2: State Management & Secrets

#### 1.5 DynamoDB State Table (1 hour)

**File:** `pulumi/dynamodbAutomated) (1 hour)

**File:** `pulumi/secrets.ts`

```typescript
import * as aws from "@pulumi/aws";
import * as random from "@pulumi/random";
import * as pulumi from "@pulumi/pulumi";

const config = new pulumi.Config("smartscale");

// Generate random K3s token (will be updated by master node on first boot)
const k3sToken = new random.RandomPassword("k3s-token", {
    length: 32,
    special: false
});

export const k3sSecret = new aws.secretsmanager.Secret("k3s-token", {
    name: "smartscale/k3s-token",
    description: "K3s cluster join token",
    recoveryWindowInDays: 0 // Immediate deletion on destroy
});

export const k3sSecretVersion = new aws.secretsmanager.SecretVersion("k3s-token-version", {
    secretId: k3sSecret.id,
    secretString: k3sToken.result
});

// Slack webhook secret (from Pulumi config)
const slackWebhookUrl = config.requireSecret("slackWebhookUrl");

export const slackSecret = new aws.secretsmanager.Secret("slack-webhook", {
    name: "smartscale/slack-webhook",
    description: "Slack webhook URL for notifications"
});

export const slackSecretVersion = new aws.secretsmanager.SecretVersion("slack-webhook-version", {
    secretId: slackSecret.id,
    secretString: slackWebhookUrl
        name: "date",
        type: "S"
    }],
    tags: { Name: "smartscale-cost-history" }
});
```

#### 1.6 Secrets Manager (1 hour)

**File:** `pulumi/secrets.ts`

````typescript
import * as aws from "@pulumi/aws";
import * as random from "@pulumi/random";

// Generate random K3s tokenAutomated) (1 hour)

**File:** `pulumi/sns.ts`

```typescript
import * as aws from "@pulumi/aws";
import * as pulumi from "@pulumi/pulumi";

const config = new pulumi.Config("smartscale");

export const slackTopic = new aws.sns.Topic("smartscale-notifications", {
    name: "smartscale-notifications",
    displayName: "SmartScale Autoscaler Notifications"
});

// Lambda function to forward SNS to Slack webhook (no manual HTTPS subscription)
const slackNotifierCode = `
import json
import urllib.request
import boto3

def handler(event, context):
    # Get webhook URL from Secrets Manager
    secrets = boto3.client('secretsmanager')
    webhook_url = secrets.get_secret_value(SecretId='smartscale/slack-webhook')['SecretString']

    # Extract SNS message
    message = event['Records'][0]['Sns']['Message']

    # Send to Slack
    payload = {'text': message}
    req = urllib.request.Request(webhook_url, json.dumps(payload).encode('utf-8'))
    req.add_header('Content-Type', 'application/json')
    urllib.request.urlopen(req)

    return {'statusCode': 200}
`;

const slackNotifierLambda = new aws.lambda.Function("slack-notifier", {
    runtime: "python3.11",
    handler: "index.handler",
    role: lambdaRole.arn, // Reuse autoscaler role
    code: new pulumi.asset.AssetArchive({
        "index.py": new pulumi.asset.StringAsset(slackNotifierCode)
    })
});

// SNS subscribes to Lambda (not webhook directly - more reliable)
exAutomated Deployment Script:** `scripts/deploy-phase1.sh`

```bash
#!/bin/bash
set -e

echo "🚀 Deploying Phase 1: Infrastructure Foundation"

cd pulumi

# Install dependencies
npm install @pulumi/random @pulumi/tls

# Deploy infrastructure
pulumi up --yes --skip-preview

# Verify resources (automated)
echo "✅ Verifying deployment..."
pulumi stack output vpcId
pulumi stack output stateTableName
pulumi stack output k3sTokenSecretArn

# Run automated tests
echo "🧪 Running infrastructure tests..."
aws dynamodb describe-table --table-name $(pulumi stack output stateTableName) > /dev/null
aws secretsmanager describe-secret --secret-id $(pulumi stack output k3sTokenSecretArn) > /dev/null
aws sns get-topic-attributes --topic-arn $(pulumi stack output notificationTopicArn) > /dev/null

echo "✅ Phase 1 complete! VPC, IAM, DynamoDB, Secrets created."
````

**Run:**

````bash
chmod +x scripts/deploy-phase1.sh
./scripts/deploy-phase1.shkTopic.arn

**File:** `pulumi/sns.ts`

```typescript
import * as aws from "@pulumi/aws";

export const slackTopic = new aws.sns.Topic("smartscale-notifications", {
    name: "smartscale-notifications",
    displayName: "SmartScale Autoscaler Notifications"
});

// Subscribe Slack webhook (configure manually or via Pulumi)
// You'll need to set up Slack webhook first
export const slackSubscription = new aws.sns.TopicSubscription("slack-sub", {
    topic: slackTopic.arn,
    protocol: "https",
    endpoint: "<SLACK_WEBHOOK_URL>" // TODO: Replace with actual webhook
});

export const snsTopicArn = slackTopic.arn;
````

#### 1.8 Main Pulumi Entry Point (1 hour)

**File:** `pulumi/index.ts`

```typescript
import * as pulumi from "@pulumi/pulumi";
import { vpc, publicSubnet1, publicSubnet2 } from "./vpc";
import { masterSg, workerSg, lambdaSg } from "./security";
import {
  masterInstanceProfile,
  workerInstanceProfile,
  lambdaRole,
} from "./iam";
import { stateTable, costHistoryTable } from "./dynamodb";
import { k3sSecret, k3sSecretVersion } from "./secrets";
import { slackTopic, snsTopicArn } from "./sns";

// Export key outputs
export const vpcId = vpc.id;
export const publicSubnetIds = [publicSubnet1.id, publicSubnet2.id];
export const masterSecurityGroupId = masterSg.id;
export const workerSecurityGroupId = workerSg.id;
export const k3sTokenSecretArn = k3sSecret.arn;
export const stateTableName = stateTable.name;
export const notificationTopicArn = snsTopicArn;

// Will add EC2 and Lambda in next phases
```

**Deploy & Verify:**

```bash
cd pulumi
npm install @pulumi/random
pulumi up --yes

# Verify all resources
pulumi stack output
aws dynamodb list-tables
aws secretsmanager list-secrets
aws sns list-topics
```

**Deliverables Week 1-2:**

- ✅ VPC with 2 public subnets
- ✅ Security groups (master, worker, Lambda)
- ✅ IAM roles with least privilege
- ✅ DynamoDB state tables
- ✅ Secrets Manager for K3s token
- ✅ SNS topic for notifications
- ✅ Infrastructure version controlled in Git

---

## Phase 2: K3s Cluster Setup (Week 3)

**Goal:** Deploy K3s master and initial worker nodes

### Week 3: Master & Worker Deployment

#### 2.1 K3s Master UserData Script (2 hours)

**File:** `k3s/master-setup.sh`

```bash
#!/bin/bash
set -e

# Wait for cloud-init
while [ ! -f /var/lib/cloud/instance/boot-finished ]; do sleep 1; done

# Install K3s master
curl -sfL https://get.k3s.io | sh -s - server \
  --disable traefik \
  --disable servicelb \
  --write-kubeconfig-mode 644 \
  --node-name master-1 \
  --cluster-init

# Wait for K3s to be ready
until kubectl get nodes; do sleep 5; done

# Get K3s token
K3S_TOKEN=$(cat /var/lib/rancher/k3s/server/node-token)

# Store token in AWS Secrets Manager
aws secretsmanager update-secret \
  --secret-id smartscale/k3s-token \
  --secret-string "$K3S_TOKEN" \
  --region ap-south-1

# Install Prometheus
kubectl create namespace monitoring || true

cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: prometheus-config
  namespace: monitoring
data:
  prometheus.yml: |
    global:
      scrape_interval: 15s
    scrape_configs:
      - job_name: 'kubernetes-nodes'
        kubernetes_sd_configs:
          - role: node
        relabel_configs:
          - source_labels: [__address__]
            target_label: __address__
            replacement: \${1}:10250
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: prometheus
  namespace: monitoring
spec:
  replicas: 1
  selector:
    matchLabels:
      app: prometheus
  template:
    metadata:
      labels:
        app: prometheus
    spec:
      containers:
      - name: prometheus
        image: prom/prometheus:latest
        ports:
        - containerPort: 9090
        volumeMounts:
        - name: config
          mountPath: /etc/prometheus
      volumes:
      - name: config
        configMap:
          name: prometheus-config
---
apiVersion: v1
kind: Service
metadata:
  name: prometheus
  namespace: monitoring
import { keyName } from "./keypair"; // Automated SSH key

// Get latest Ubuntu AMI (automated, no manual selection)
const ubuntuAmi = aws.ec2.getAmi({
    mostRecent: true,
    owners: ["099720109477"], // Canonical
    filters: [
        { name: "name", values: ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"] },
        { name: "virtualization-type", values: ["hvm"] }
    ]
});

// Read master setup script (automated provisioning)
const masterUserData = fs.readFileSync("../k3s/master-setup.sh", "utf8");

// Master EC2 instance (fully automated)
export const masterInstance = new aws.ec2.Instance("k3s-master", {
    instanceType: "t3.medium",
    ami: ubuntuAmi.then(ami => ami.id),
    subnetId: publicSubnet1.id,
    vpcSecurityGroupIds: [masterSg.id],
    iamInstanceProfile: masterInstanceProfile.name,
    keyName: keyName, // Automated SSH key from keypair.ts
    userData: masterUserData,
    tags: {
        Name: "k3s-master",
        Role: "k3s-master",
        Project: "smartscale"
    },
    rootBlockDevice: {
        volumeSize: 30,
        volumeType: "gp3",
        deleteOnTermination: true
    }
});

// Wait for K3s to be ready (automated healthcheck)
export const masterReadyCheck = new aws.cloudwatch.MetricAlarm("master-ready-check", {
    comparisonOperator: "GreaterThanThreshold",
    evaluationPeriods: 1,
    metricName: "StatusCheckFailed",
    namespace: "AWS/EC2",
    period: 300,
    statistic: "Average",
    threshold: 0,
    dimensions: {
        InstanceId: masterInstance.id
        - containerPort: 3000
        env:
        - name: GF_SECURITY_ADMIN_PASSWORD
          value: "admin123"  # Change in production
---
apiVersion: v1
kind: Service
metadata:
  name: grafana
  namespace: monitoring
spec:
  type: NodePort
  selector:
    app: grafana
  ports:
  - port: 3000
    targetPort: 3000
    nodePort: 3000
EOF

echo "K3s master setup complete"
```

#### 2.2 Master EC2 Instance (Pulumi) (2 hours)

**File:** `pulumi/ec2-master.ts`

```typescript
import * as aws from "@pulumi/aws";
import * as pulumi from "@pulumi/pulumi";
import * as fs from "fs";
import { publicSubnet1 } from "./vpc";
import { masterSg } from "./security";
import { masterInstanceProfile } from "./iam";

// Get latest Ubuntu AMI
const amiId = aws.ec2
  .getAmi({
    mostRecent: true,
    owners: ["099720109477"], // Canonical
    filters: [
      {
        name: "name",
        values: ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"],
      },
      { name: "virtualization-type", values: ["hvm"] },
    ],
  })
  .then((ami) => ami.id);

// Read master setup script
const masterUserData = fs.readFileSync("../k3s/master-setup.sh", "utf8");

// Master EC2 instance
export const masterInstance = new aws.ec2.Instance("k3s-master", {
  instanceType: "t3.medium",
  ami: amiId,
  subnetId: publicSubnet1.id,
  vpcSecurityGroupIds: [masterSg.id],
  iamInstanceProfile: masterInstanceProfile.name,
  keyName: "smartscale-key", // Create SSH key first
  userData: masterUserData,
  tags: {
    Name: "k3s-master",
    Role: "k3s-master",
    Project: "smartscale",
  },
  rootBlockDevice: {
    volumeSize: 30,
    volumeType: "gp3",
  },
});

export const masterPublicIp = masterInstance.publicIp;
export const masterPrivateIp = masterInstance.privateIp;
```

#### 2.3 Worker UserData Script (2 hours)

**File:** `k3s/worker-userdata.sh`

````bash
#!/bin/bash
set -e
Automated) (1 hour)

**File:** `pulumi/ec2-initial-workers.ts`

```typescript
import * as aws from "@pulumi/aws";
import { workerLaunchTemplate } from "./ec2-worker";
import { publicSubnet1, publicSubnet2 } from "./vpc";

// Automatically launch 2 initial workers
export const initialWorker1 = new aws.ec2.Instance("initial-worker-1", {
    instanceType: "t3.medium",
    ami: workerLaunchTemplate.imageId,
    subnetId: publicSubnet1.id,
    vpcSecurityGroupIds: workerLaunchTemplate.vpcSecurityGroupIds,
    iamInstanceProfile: workerLaunchTemplate.iamInstanceProfile.then(p => ({ name: p!.name })),
    keyName: workerLaunchTemplate.keyName,
    userData: workerLaunchTemplate.userData,
    tags: {
        Name: "k3s-worker-1",
        Role: "k3s-worker",
        Project: "smartscale",
        ManagedBy: "pulumi-initial"
    }
});

export const initialWorker2 = new aws.ec2.Instance("initial-worker-2", {
    instanceType: "t3.medium",
    ami: workerLaunchTemplate.imageId,
    subnetId: publicSubnet2.id, // Different AZ
    vpcSecurityGroupIds: workerLaunchTemplate.vpcSecurityGroupIds,
    iamInstanceProfile: workerLaunchTemplate.iamInstanceProfile.then(p => ({ name: p!.name })),
    keyName: workerLaunchTemplate.keyName,
    userData: workerLaunchTemplate.userData,
    tags: {
        Name: "k3s-worker-2",
        Role: "k3s-worker",
        Project: "smartscale",
        ManagedBy: "pulumi-initial"
    }
});
````

**Automated Deployment Script:** `scripts/deploy-phase2.sh`

```bash
#!/bin/bash
set -e

echo "🚀 Deploying Phase 2: K3s Cluster"

cd pulumi

# Deploy master and workers
pulumi up --yes --skip-preview

# Wait for K3s cluster to be ready
MASTER_IP=$(pulumi stack output masterPublicIp)
echo "⏳ Waiting for K3s master at $MASTER_IP..."

# Automated health check (no manual SSH)
for i in {1..30}; do
    if ssh -i ../smartscale-key.pem -o StrictHostKeyChecking=no ubuntu@$MASTER_IP "kubectl get nodes" 2>/dev/null; then
        echo "✅ K3s master is ready!"
        break
    fi
    echo "   Attempt $i/30: Still waiting..."
    sleep 30
done

# Verify workers joined (automated)
echo "🔍 Verifying worker nodes..."
NODE_COUNT=$(ssh -i ../smartscale-key.pem ubuntu@$MASTER_IP "kubectl get nodes --no-headers | wc -l")

if [ "$NODE_COUNT" -ge 3 ]; then
    echo "✅ Phase 2 complete! K3s cluster has $NODE_COUNT nodes (1 master + 2 workers)"
    ssh -i ../smartscale-key.pem ubuntu@$MASTER_IP "kubectl get nodes"
else
    echo "❌ Warning: Expected 3 nodes, got $NODE_COUNT. Workers may still be joining..."
fi

# Export kubeconfig (automated remote access)
ssh -i ../smartscale-key.pem ubuntu@$MASTER_IP "sudo cat /etc/rancher/k3s/k3s.yaml" | \
    sed "s/127.0.0.1/$MASTER_IP/g" > ../k3s-kubeconfig.yaml

echo "📝 Kubeconfig saved to k3s-kubeconfig.yaml"
echo "   Use: export KUBECONFIG=\$(pwd)/k3s-kubeconfig.yaml"
```

**Run:**

```bash
chmod +x scripts/deploy-phase2.sh
./scripts/deploy-phase2.sh
```

#### 2.4 Worker Launch Template (Pulumi) (2 hours)

**File:** `pulumi/ec2-worker.ts`

```typescript
import * as aws from "@pulumi/aws";
import * as fs from "fs";
import { workerSg } from "./security";
import { workerInstanceProfile } from "./iam";

// Get latest Ubuntu AMI
const amiId = aws.ec2
  .getAmi({
    mostRecent: true,
    owners: ["099720109477"],
    filters: [
      {
        name: "name",
        values: ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"],
      },
      { name: "virtualization-type", values: ["hvm"] },
    ],
  })
  .then((ami) => ami.id);

// Read worker setup script
const workerUserData = fs.readFileSync("../k3s/worker-userdata.sh", "utf8");

// Worker launch template
export const workerLaunchTemplate = new aws.ec2.LaunchTemplate(
  "worker-template",
  {
    namePrefix: "k3s-worker-",
    imageId: amiId,
    instanceType: "t3.medium",
    keyName: "smartscale-key",
    vpcSecurityGroupIds: [workerSg.id],
    iamInstanceProfile: { name: workerInstanceProfile.name },
    userData: Buffer.from(workerUserData).toString("base64"),
    blockDeviceMappings: [
      {
        deviceName: "/dev/sda1",
        ebs: {
          volumeSize: 20,
          volumeType: "gp3",
          deleteOnTermination: true,
        },
      },
    ],
    tagSpecifications: [
      {
        resourceType: "instance",
        tags: {
          Name: "k3s-worker",
          Role: "k3s-worker",
          Project: "smartscale",
          ManagedBy: "autoscaler",
        },
      },
    ],
  }
);

export const workerLaunchTemplateId = workerLaunchTemplate.id;
```

#### 2.5 Initial Worker Deployment (1 hour)

```bash
# Update pulumi/index.ts to include EC2 resources
cd pulumi
pulumi up --yes

# SSH to master after ~5 minutes
MASTER_IP=$(pulumi stack output masterPublicIp)
ssh -i ~/.ssh/smartscale-key.pem ubuntu@$MASTER_IP

# Verify K3s master
kubectl get nodes
# Should show master node

# Manually launch 2 initial workers for testing
aws ec2 run-instances \
  --launch-template LaunchTemplateId=$(pulumi stack output workerLaunchTemplateId) \
  --subnet-id <subnet-id> \
  --count 2 \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=k3s-worker-initial}]'

# Wait 5 minutes, verify workers joined
kubectl get nodes
# Should show master + 2 workers
```

**Deliverables Week 3:**

- ✅ K3s master node running
- ✅ Prometheus deployed (NodePort 30090)
- ✅ Grafana deployed (NodePort 3000)
- ✅ Worker launch template configured
- ✅ 2 initial workers joined cluster
- ✅ K3s token stored in Secrets Manager

---

## Phase 3: Lambda Autoscaler Logic (Week 4-5)

**Goal:** Implement Python Lambda for autoscaling decisions

### Week 4: Core Lambda Functions

#### 3.1 Project Structure (0.5 hours)

```bash
cd lambda
python3.11 -m venv venv
source venv/bin/activate
pip install boto3 requests

# Create requirements.txt
cat > requirements.txt <<EOF
boto3==1.34.10
requests==2.31.0
EOF

# Create Python modules
touch autoscaler.py metrics_collector.py ec2_manager.py
touch state_manager.py k3s_helper.py slack_notifier.py
```

#### 3.2 Metrics Collector (3 hours)

**File:** `lambda/metrics_collector.py`

```python
import requests
from typing import Dict

class MetricsCollector:
    def __init__(self, prometheus_url: str):
        self.prometheus_url = prometheus_url

    def query_prometheus(self, query: str) -> float:
        """Execute PromQL query and return scalar result"""
        try:
            response = requests.get(
                f"{self.prometheus_url}/api/v1/query",
                params={"query": query},
                timeout=10
            )
            response.raise_for_status()
            result = response.json()

            if result["status"] != "success":
                raise Exception(f"Prometheus query failed: {result}")

            # Extract scalar value
            return float(result["data"]["result"][0]["value"][1])
        except Exception as e:
            print(f"Error querying Prometheus: {e}")
            raise

    def get_cluster_metrics(self) -> Dict[str, float]:
        """Get all required cluster metrics"""
        return {
            "cpu_percent": self.query_prometheus(
                'avg(rate(node_cpu_seconds_total{mode!="idle"}[5m])) * 100'
            ),
            "memory_percent": self.query_prometheus(
                '(1 - avg(node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100'
            ),
            "pending_pods": self.query_prometheus(
                'sum(kube_pod_status_phase{phase="Pending"})'
            ),
            "node_count": self.query_prometheus(
                'count(kube_node_info)'
            )
        }
```

#### 3.3 State Manager (DynamoDB) (3 hours)

**File:** `lambda/state_manager.py`

```python
import boto3
from datetime import datetime
from typing import Optional, Dict
from decimal import Decimal

class StateManager:
    def __init__(self, table_name: str, cluster_id: str):
        self.dynamodb = boto3.resource('dynamodb')
        self.table = self.dynamodb.Table(table_name)
        self.cluster_id = cluster_id

    def acquire_lock(self) -> bool:
        """Acquire distributed lock (prevent concurrent scaling)"""
        try:
            self.table.put_item(
                Item={
                    'cluster_id': self.cluster_id,
                    'scaling_in_progress': True,
                    'lock_timestamp': datetime.utcnow().isoformat()
                },
                ConditionExpression='attribute_not_exists(scaling_in_progress) OR scaling_in_progress = :false',
                ExpressionAttributeValues={':false': False}
            )
            return True
        except self.dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
            print("Lock already held by another Lambda invocation")
            return False

    def release_lock(self):
        """Release distributed lock"""
        self.table.update_item(
            Key={'cluster_id': self.cluster_id},
            UpdateExpression='SET scaling_in_progress = :false',
            ExpressionAttributeValues={':false': False}
        )

    def get_state(self) -> Dict:
        """Get current autoscaler state"""
        try:
            response = self.table.get_item(Key={'cluster_id': self.cluster_id})
            return response.get('Item', {})
        except Exception as e:
            print(f"Error getting state: {e}")
            return {}

    def update_state(self, updates: Dict):
        """Update autoscaler state"""
        # Convert floats to Decimal for DynamoDB
        updates_decimal = {k: Decimal(str(v)) if isinstance(v, float) else v
                          for k, v in updates.items()}

        update_expr = "SET " + ", ".join([f"{k} = :{k}" for k in updates_decimal.keys()])
        expr_values = {f":{k}": v for k, v in updates_decimal.items()}

        self.table.update_item(
            Key={'cluster_id': self.cluster_id},
            UpdateExpression=update_expr,
            ExpressionAttributeValues=expr_values
        )
```

#### 3.4 EC2 Manager (4 hours)

**File:** `lambda/ec2_manager.py`

```python
import boto3
from typing import List, Dict

class EC2Manager:
    def __init__(self, launch_template_id: str, subnet_ids: List[str]):
        self.ec2 = boto3.client('ec2')
        self.launch_template_id = launch_template_id
        self.subnet_ids = subnet_ids

    def launch_workers(self, count: int) -> List[str]:
        """Launch worker instances"""
        try:
            response = self.ec2.run_instances(
                LaunchTemplate={'LaunchTemplateId': self.launch_template_id},
                MinCount=count,
                MaxCount=count,
                SubnetId=self.subnet_ids[0],  # Rotate subnets in production
                TagSpecifications=[{
                    'ResourceType': 'instance',
                    'Tags': [
                        {'Key': 'Name', 'Value': 'k3s-worker'},
                        {'Key': 'ManagedBy', 'Value': 'autoscaler'},
                        {'Key': 'LaunchTime', 'Value': str(datetime.utcnow())}
                    ]
                }]
            )

            instance_ids = [i['InstanceId'] for i in response['Instances']]
            print(f"Launched {count} workers: {instance_ids}")
            return instance_ids
        except Exception as e:
            print(f"Error launching instances: {e}")
            raise

    def get_worker_instances(self) -> List[Dict]:
        """Get all worker instances"""
        response = self.ec2.describe_instances(
            Filters=[
                {'Name': 'tag:Role', 'Values': ['k3s-worker']},
                {'Name': 'instance-state-name', 'Values': ['running', 'pending']}
            ]
        )

        instances = []
        for reservation in response['Reservations']:
            for instance in reservation['Instances']:
                instances.append({
                    'id': instance['InstanceId'],
                    'state': instance['State']['Name'],
                    'private_ip': instance.get('PrivateIpAddress'),
                    'launch_time': instance['LaunchTime']
                })
        return instances

    def terminate_worker(self, instance_id: str):
        """Terminate a worker instance"""
        try:
            self.ec2.terminate_instances(InstanceIds=[instance_id])
            print(f"Terminated instance: {instance_id}")
        except Exception as e:
            print(f"Error terminating instance: {e}")
            raise
```

### Week 5: Scaling Logic & Integration

#### 3.5 Scaling Decision Engine (4 hours)

**File:** `lambda/autoscaler.py`

````python
import os
from datetime import datetime, timedelta
from typing import Dict, Tuple
from metrics_collector import MetricsCollector
from state_manager import StateManager
from ec2_manager import EC2Manager
from slack_notifier import SlackNotifier

# Configuration
MIN_NODES = 2
MAX_NODES = 10
SCALE_UP_COOLDOWN = 300  # 5 minutes
SCALE_DOWN_COOLDOWN = 600  # 10 minutes
CPU_SCALE_UP_THRESHOLD = 70
CPU_SCALE_DOWN_THRESHOLD = 30
MEMORY_SCALE_UP_THRESHOLD = 75
MEMORY_SCALE_DOWN_THRESHOLD = 50

class Autoscaler:
    def __init__(self):
        self.metrics = MetricsCollector(os.environ['PROMETHEUS_URL'])
        self.state = StateManager(os.environ['STATE_TABLE'], os.environ['CLUSTER_ID'])
        self.ec2 = EC2Manager(
            os.environ['LAUNCH_TEMPLATE_ID'],
            os.environ['SUBNET_IDS'].split(',')
        )
        self.notifier = SlackNotifier(os.environ['SNS_TOPIC_ARN'])

    def evaluate_scaling(self, metrics: Dict, state: Dict) -> Tuple[str, int, str]:
        """
        Determine scaling action
        Returns: (action, count, reason)
        """
        current_nodes = int(metrics['node_count'])
        cpu = metrics['cpu_percent']
        memory = metrics['memory_percent']
        pending_pods = metrics['pending_pods']

        # Check cooldown
        last_scale_time = state.get('last_scale_time')
        if last_scale_time:
            last_scale = datetime.fromisoformat(last_scale_time)
            time_since_scale = (datetime.utcnow() - last_scale).total_seconds()

            if time_since_scale < SCALE_UP_COOLDOWN:
                return ('no_action', 0, f'Cooldown: {int(SCALE_UP_COOLDOWN - time_since_scale)}s remaining')

        # Scale-up conditions
        if cpu > CPU_SCALE_UP_THRESHOLD:
            if current_nodes >= MAX_NODES:
                return ('no_action', 0, f'Maximum nodes ({MAX_NODES}) reached')
            count = 1 if cpu < 85 else 2
            return ('scale_up', count, f'CPU at {cpu:.1f}% (threshold: {CPU_SCALE_UP_THRESHOLD}%)')

        if pending_pods > 0:
            if current_nodes >= MAX_NODES:
                return ('no_action', 0, f'Maximum nodes reached, {int(pending_pods)} pods pending')
            count = 1 if pending_pods < 5 else 2
            return ('scale_up', count, f'{int(pending_pods)} pods pending')

        if memory > MEMORY_SCALE_UP_THRESHOLD:
            if current_nodes >= MAX_NODES:
                return ('no_action', 0, 'Maximum nodes reached')
            return ('scale_up', 1, f'Memory at {memory:.1f}% (threshold: {MEMORY_SCALE_UP_THRESHOLD}%)')

        # Scale-down conditions (stricter)
        if last_scale_time:
            last_scale = datetime.fromisoformat(last_scale_time)
            time_since_scale = (datetime.utcnow() - last_scale).total_seconds()

            if time_since_scale < SCALE_DOWN_COOLDOWN:
                return ('no_action', 0, 'Scale-down cooldown active')

        if cpu < CPU_SCALE_DOWN_THRESHOLD and memory < MEMORY_SCALE_DOWN_THRESHOLD and pending_pods == 0:
            if current_nodes <= MIN_NODES:
                return ('no_action', 0, f'Minimum nodes ({MIN_NODES}) reached')

            # Check sustained low usage
            low_cpu_count = state.get('low_cpu_count', 0)
            if low_cpu_count >= 5:  # 10 minutes of low CPU (5 checks × 2 min)
                return ('scale_down', 1, f'CPU at {cpu:.1f}% for {low_cpu_count * 2} minutes')

        return ('no_action', 0, 'Metrics within normal range')

    def execute_scaling(self, action: str, count: int, reason: str):
        """Execute scaling action"""
        if action == 'scale_up':
            instance_ids = self.ec2.launch_workers(count)
            self.notifier.send_notification(
                f"🟢 Scale-up: Added {count} nodes\nReason: {reason}\nInstances: {instance_ids}"
            )
        elif action == 'scale_down':
            workers = self.ec2.get_worker_instances()
            # Remove oldest worker
            oldest = sorted(workers, key=lambda x: x['launch_time'])[0]
            self.ec2.terminate_worker(oldest['id'])
            self.notifier.send_notification(
                f"🔵 Scale-down: Removed 1 node\nReason: {reason}\nInstance: {oldest['id']}"
            )

def lambda_handler(event, context):
    """Main Lambda handler"""
    autoscaler = Autoscaler()

    # Acquire lock
    if not autoscaler.state.acquire_lock():
        return {'statusCode': 200, 'body': 'Another scaling operation in progress'}

    try:
        # Get metrics
        metrics = autoscaler.metrics.get_cluster_metrics()
        print(f"Cluster metrics: {metrics}")

        # Get current state
        state = autoscaler.state.get_state()

        # Evaluate scaling decision
        action, count, reason = autoscaler.evaluate_scaling(metrics, state)
        print(f"Decision: {action}, Count: {count}, Reason: {reason}")

        # Execute if needed
        if action != 'no_action':
            autoscaler.execute_scaling(action, count, reason)

            # Update state
            autoscaler.state.update_state({
                'last_scale_time': datetime.utcnow().isoformat(),
                'last_action': action,
                'last_reason': reason,
                'low_cpu_count': 0
            })
        else:
            # Track low CPU periods
  Automated Deployment Script:** `scripts/deploy-phase3.sh`

```bash
#!/bin/bash
set -e

echo "🚀 Deploying Phase 3: Lambda Autoscaler"

# Package Lambda (automated)
cd lambda
source venv/bin/activate
pip install -r requirements.txt -t .
cd ..

# Deploy Lambda via Pulumi
cd pulumi
pulumi up --yes --skip-preview

# Automated Lambda testing
echo "🧪 Testing Lambda function..."
aws lambda invoke \
  --function-name k3s-autoscaler \
  --payload '{}' \
  --log-type Tail \
  /tmp/lambda-test-response.json \
  --query 'LogResult' --output text | base64 -d

echo "📋 Lambda response:"
cat /tmp/lambda-test-response.json | jq .

# Verify EventBridge trigger
echo "🔍 V-7: Automated Testing

**Automated Test Script:** `scripts/run-all-tests.sh`

```bash
#!/bin/bash
set -e

echo "🧪 Running SmartScale Automated Test Suite"

# Phase 1: Unit Tests (TypeScript/Jest)
echo "1️⃣ Unit Tests (TypeScript/Jest)"
cd tests
npm install
npm run test:ci

# Phase 2: Integration Tests (with LocalStack)
echo "2️⃣ Integration Tests (LocalStack)"
docker run -d --name smartscale-localstack -p 4566:4566 \
    -e SERVICES=lambda,dynamodb,ec2,secretsmanager \
    localstack/localstack

sleep 10  # Wait for LocalStack
export AWS_ENDPOINT_URL=http://localhost:4566
npm run test:integration

docker stop smartscale-localstack
docker rm smartscale-localstack

# Phase 3: Load Tests (k6)
echo "3️⃣ Load Tests (k6)"
export DEMO_APP_URL=$(cd ../pulumi && pulumi stack output masterPublicIp | xargs -I {} echo "http://{}:30080")

k6 run --out json=../test-results/load/gradual.json load/load-test.js
k6 run --out json=../test-results/load/flash-sale.json load/load-test-flash-sale.js

# Phase 4: Autoscaling Validation (automated) (fully automated)

**(See COST_DASHBOARD_IMPLEMENTATION_PLAN.md for full details)**

**Automated Deployment Script:** `scripts/deploy-phase5-monitoring.sh`

```bash
#!/bin/bash
set -e

echo "🚀 Deploying Phase 5: Monitoring & Cost Dashboard"

# Get master IP
MASTER_IP=$(cd pulumi && pulumi stack output masterPublicIp)
KUBECONFIG="../k3s-kubeconfig.yaml"

# Deploy Prometheus cost rules (automated)
echo "📊 Deploying Prometheus cost tracking rules..."
kubectl --kubeconfig=$KUBECONFIG apply -f ../k3s/prometheus-cost-rules.yaml

# Deploy Grafana datasources (automated)
echo "📈 Configuring Grafana data sources..."
kubectl --kubeconfig=$KUBECONFIG apply -f ../monitoring/grafana-datasources.yaml

# Import Grafana dashboards (automated via ConfigMaps)
echo "📊 Importing Grafana dashboards..."
kubectl --kubeconfig=$KUBECONFIG create configmap grafana-dashboards \
    --from-file=../monitoring/grafana-dashboards/ \
    --namespace=monitoring \
    --dry-run=client -o yaml | kubectl --kubeconfig=$KUBECONFIG apply -f -

# Configure Grafana to auto-load dashboards
kubectl --kubeconfig=$KUBECONFIG apply -f - <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: grafana-dashboard-provider
  namespace: monitoring
data:
  dashboards.yaml: |
    apiVersion: 1
    providers:
      - name: 'default'
        orgId: 1Automated) (4 hours)

**Automated Security Script:** `scripts/harden-security.sh`

```bash
#!/bin/bash
set -e

echo "🔒 Hardening SmartScale Security"

# Enable CloudTrail (automated)
echo "1️⃣ Enabling CloudTrail audit logging..."
aws cloudtrail create-trail \
    --name smartscale-audit \
    --s3-bucket-name smartscale-audit-$(aws sts get-caller-identity --query Account --output text) \
    --is-multi-region-trail \
    --no-cli-pager || echo "   CloudTrail already exists"

aws cloudtrail start-logging --name smartscale-audit

# Enable GuardDuty (automated threat detection)
echo "2️⃣ Enabling GuardDuty..."
aws guardduty create-detector --enable --no-cli-pager 2>/dev/null || echo "   GuardDuty already enabled"

# Rotate K3s token (automated)
echo "3️⃣ Rotating K3s token..."
NEW_TOKEN=$(openssl rand -base64 32)
aws secretsmanager update-secret \
    --secret-id smartscale/k3s-token \
    --secret-string "$NEW_TOKEN"

# Enable VPC Flow Logs (automated)
echo "4️⃣ Enabling VPC Flow Logs..."
VPC_ID=$(cd pulumi && pulumi stack output vpcId)
aws ec2 create-flow-logs \
    --resource-type VPC \
    --resource-ids $VPC_ID \
    --traffic-type ALL \
    --log-destination-type cloud-watcAutomated) (2 hours)

**Automated Backup Script:** `scripts/enable-backups.sh`

```bash
#!/bin/bash
set -e

echo "💾 Enabling SmartScale Backups"

# DynamoDB point-in-time recovery (automated)
echo "1️⃣ Enabling DynamoDB backups..."
for table in k3s-autoscaler-state smartscale-cost-history; do
    aws dynamodb update-continuous-backups \
        --table-name $table \
        --point-in-time-recovery-specification PointInTimeRecoveryEnabled=true \
        --no-cli-pager
    echo "   ✅ $table backup enabled"
done

# K3s etcd backups (automated CronJob)
echo "2️⃣ Deploying K3s backup CronJob..."
KUBECONFIG="../k3s-kubeconfig.yaml"
kubectl --kubeconfig=$KUBECONFIG apply -f - <<EOF
apiVersion: batch/v1
kind: CronJob
metadata:
  name: k3s-etcd-backup
  namespace: kube-system
spec:
  schedule: "0 2 * * *"  # Daily at 2 AM
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: backup
            image: amazon/aws-cli:latest
            command:
            - /bin/bash
            - -c
            - |
              tar -czf /tmp/k3s-backup-\$(date +%Y%m%d).tar.gz /var/lib/rancher/k3s/server
              aws s3 cp /tmp/k3s-backup-\$(date +%Y%m%d).tar.gz s3://smartscale-backups-$(aws sts get-caller-identity --query Account --output text)/
          restartPolicy: OnFailure
          hostPath:
            path: /var/lib/rancher/k3s/server
EOF

# Create S3 bucket for backups (automated)
echo "3️⃣ Creating S3 backup bucket..."
BUCKET_NAME="smartscale-backups-$(aws sts get-caller-identity --query Account --output text)"
aws s3 mb s3://$BUCKET_NAME --region ap-south-1 2>/dev/null || echo "   Bucket already exists"

# Enable S3 versioning
aws s3api put-bucket-versioning \
    --bucket $BUCKET_NAME \
    --versioning-configuration Status=Enabled

# Tag infrastructure for disaster recovery
echo "4️⃣ Tagging infrastructure..."
git tag v1.0-production-$(date +%Y%m%d)
git push --tags 2>/dev/null || echo "   Already tagged"

# Export stack state
cd pulumi
pulumi stack export --file ../backups/pulumi-state-$(date +%Y%m%d).json
aws s3 cp ../backups/pulumi-state-$(date +%Y%m%d).json s3://$BUCKET_NAME/pulumi-state/

echo "✅ Backups enabled! Recovery RPO: 24 hours, RTO: 2 hours"
````

**Run:**

```bash
chmod +x scripts/enable-backups.sh
./scripts/enable-backups.sh
# Enable AWS Config (automated compliance checking)
echo "6️⃣ Enabling AWS Config..."
aws configservice put-configuration-recorder \
    --configuration-recorder name=smartscale-config,roleARN=arn:aws:iam::$(aws sts get-caller-identity --query Account --output text):role/aws-service-role/config.amazonaws.com/AWSServiceRoleForConfig \
    --no-cli-pager 2>/dev/null || echo "   Config already enabled"

echo "✅ Security hardening complete!"
```

**Run:**

```bash
chmod +x scripts/harden-security.sh
./scripts/harden-security.sh.."
    sleep 10
done

echo "✅ Phase 5 complete! Access Grafana at http://$MASTER_IP:3000"
echo "   Username: admin"
echo "   Password: admin123 (change on first login)"
```

**Run:**

```bash
chmod +x scripts/deploy-phase5-monitoring.sh
./scripts/deploy-phase5-monitoring.sh
```

**Deliverables Week 8:**

- ✅ Prometheus metrics configured (automated)
- ✅ CloudWatch billing integrated (automated)
- ✅ Cost dashboard in Grafana (auto-imported)
- ✅ Before/after savings shown (automated calculation)
- ✅ Alerts configured (automated via ConfigMaps) or timeout
  wait $SCALE_UP_PID || echo "⚠️ Scale-up test completed with warnings"

# Run scale-down test

./test-scale-down.sh > ../../test-results/scenarios/scale-down.log 2>&1

# Phase 5: Failure Scenarios

echo "5️⃣ Failure Scenario Tests"
cd ../scenarios
chmod +x \*.sh

./test-prometheus-down.sh > ../../test-results/scenarios/prometheus-down.log 2>&1
./test-quota-exceeded.sh > ../../test-results/scenarios/quota-exceeded.log 2>&1

echo "✅ All tests complete! Results in test-results/"
echo "📊 Coverage report: tests/coverage/lcov-report/index.html"

````

**Run:**
```bash
chmod +x scripts/run-all-tests.sh
./scripts/run-all-tests

    echo "   Waiting for EventBridge trigger..."
    sleep 15
done

echo "✅ Phase 3 complete! Lambda autoscaler deployed and running."
````

**Run:**

```bash
chmod +x scripts/deploy-phase3.sh
./scripts/deploy-phase3.sh
    finally:
        autoscaler.state.release_lock()
```

#### 3.6 Slack Notifier (1 hour)

**File:** `lambda/slack_notifier.py`

```python
import boto3

class SlackNotifier:
    def __init__(self, sns_topic_arn: str):
        self.sns = boto3.client('sns')
        self.topic_arn = sns_topic_arn

    def send_notification(self, message: str):
        """Send notification via SNS (which forwards to Slack)"""
        try:
            self.sns.publish(
                TopicArn=self.topic_arn,
                Message=message,
                Subject='SmartScale Autoscaler'
            )
            print(f"Notification sent: {message}")
        except Exception as e:
            print(f"Error sending notification: {e}")
```

#### 3.7 Lambda Deployment (Pulumi) (2 hours)

**File:** `pulumi/lambda.ts`

````typescript
import * as aws from "@pulumi/aws";
import * as pulumi from "@pulumi/pulumi";
import { lambdaRole } from "./iam";
import { lambdaSg } from "./security";
import { publicSubnet1 } from "./vpc";
import { stateTable } from "./dynamodb";
import { snsTopicArn } from "./sns";
import { workerLaunchTemplateId } from "./ec2-worker";
import { masterPrivateIp } from "./ec2-master";

// Package Lambda function
const lambdaCode = new pulumi.asset.AssetArchive({
    ".": new pulumi.asset.FileArchive("../lambda")
});

// Lambda function
export const autoscalerLambda = new aws.lambda.Function("k3s-autoscaler", {
    runtime: "python3.11",
    handler: "autoscaler.lambda_handler",
    role: lambdaRole.arn,
    code: lambdaCode,
    timeout: 120,
    memorySize: 256,
    environment: {
        variables: {
            PROMETHEUS_URL: pulumi.interpolate`http://${masterPrivateIp}:30090`,
            STATE_TABLE: stateTable.name,
            CLUSTER_ID: "smartscale-prod",
            LAUNCH_TEMPLATE_ID: workerLaunchTemplateId,
            SUBNET_IDS: publicSubnet1.id,
            SNS_TOPIC_ARN: snsTopicArn,
            PYTHONUNBUFFERED: "1"
        }
    },
   Fully Automated Deployment

**Zero Manual Steps - Complete Automation:**

### One-Command Deployment

**Master Deployment Script:** `scripts/deploy-smartscale.sh`

```bash
#!/bin/bash
set -e

echo "🚀 SmartScale K3s Autoscaler - Fully Automated Deployment"
echo "=========================================================="

# Prerequisites check
echo "📋 Checking prerequisites..."
command -v aws >/dev/null 2>&1 || { echo "❌ AWS CLI required"; exit 1; }
command -v pulumi >/dev/null 2>&1 || { echo "❌ Pulumi required"; exit 1; }
command -v node >/dev/null 2>&1 || { echo "❌ Node.js required"; exit 1; }
command -v python3.11 >/dev/null 2>&1 || { echo "❌ Python 3.11 required"; exit 1; }
command -v kubectl >/dev/null 2>&1 || { echo "❌ kubectl required"; exit 1; }
command -v k6 >/dev/null 2>&1 || { echo "❌ k6 required"; exit 1; }

# Set Slack webhook
read -p "Enter Slack webhook URL: " SLACK_WEBHOOK
pulumi config set --secret smartscale:slackWebhookUrl "$SLACK_WEBHOOK"

echo "✅ Prerequisites validated"

# Phase 1: Infrastructure
echo ""
echo "Phase 1/6: Infrastructure Foundation"
./scripts/deploy-phase1.sh

# Phase 2: K3s Cluster
echo ""
echo "Phase 2/6: K3s Cluster Setup"
./scripts/deploy-phase2.sh

# Phase 3: Lambda Autoscaler
echo ""
echo "Phase 3/6: Lambda Autoscaler"
./scripts/deploy-phase3.sh

# Phase 4: Testing
echo ""
echo "Phase 4/6: Automated Testing"
./scripts/run-all-tests.sh

# Phase 5: Monitoring
echo ""
echo "Phase 5/6: Monitoring & Cost Dashboard"
./scripts/deploy-phase5-monitoring.sh

# Phase 6: Security & Backups
echo ""
echo "Phase 6/6: Security Hardening & Backups"
./scripts/harden-security.sh
./scripts/enable-backups.sh

# Summary
echo ""
echo "=========================================================="
echo "✅ SmartScale Deployment Complete!"
echo "=========================================================="
echo ""
echo "📊 Access Points:"
echo "   Grafana:    http://$(cd pulumi && pulumi stack output masterPublicIp):3000"
echo "   Prometheus: http://$(cd pulumi && pulumi stack output masterPublicIp):30090"
echo "   Demo App:   http://$(cd pulumi && pulumi stack output masterPublicIp):30080"
echo ""
echo "🔑 Credentials:"
echo "   Grafana:    admin / admin123 (change on first login)"
echo "   SSH Key:    smartscale-key.pem (auto-generated)"
echo "   Kubeconfig: k3s-kubeconfig.yaml"
echo ""
echo "💰 Cost Tracking:"
echo "   Current nodes: $(kubectl --kubeconfig=k3s-kubeconfig.yaml get nodes --no-headers | wc -l)"
echo "   Dashboard: http://$(cd pulumi && pulumi stack output masterPublicIp):3000/d/cost-overview"
echo ""
echo "📝 Next Steps:"
echo "   1. Change Grafana password"
echo "   2. Review cost dashboard for baseline"
echo "   3. Run load test: k6 run tests/load/load-test.js"
echo "   4. Monitor autoscaling in Grafana"
echo ""
echo "🎉 SmartScale is now managing your K3s cluster!"
````

### Quick Start (Literally One Command)

```bash
# Clone repository
git clone https://github.com/BayajidAlam/node-fleet.git
cd node-fleet

# Run complete deployment
chmod +x scripts/*.sh
./scripts/deploy-smartscale.sh
```

**That's it! Everything else is automated.**

## Automation Summary

✅ **100% Automated:**

- Infrastructure provisioning (Pulumi)
- SSH key generation (TLS provider)
- K3s cluster setup (UserData scripts)
- Worker node joins (Secrets Manager + UserData)
- Lambda deployment (Pulumi + packaging)
- EventBridge scheduling (Pulumi)
- Prometheus/Grafana deployment (kubectl apply)
- Dashboard imports (ConfigMaps)
- CloudWatch integration (AWS CLI)
- Testing (Jest, k6, bash scripts)
- Security hardening (AWS CLI scripts)
- Backup configuration (DynamoDB, S3, CronJobs)
- CI/CD pipeline (GitHub Actions)

❌ **Zero Manual Steps:**

- No console clicking
- No manual SSH commands
- No manual kubectl apply
- No manual dashboard imports
- No manual testing

🎯 **Result:** From zero to production in ~2 hours (mostly waiting for resources to provision)ort const autoscalerLambdaArn = autoscalerLambda.arn;

````

**Deploy:**
```bash
cd lambda
pip install -r requirements.txt -t .
cd ../pulumi
pulumi up --yes

# Test Lambda manually
aws lambda invoke \
  --function-name k3s-autoscaler \
  --payload '{}' \
  /tmp/response.json

cat /tmp/response.json
````

**Deliverables Week 4-5:**

- ✅ Metrics collector (Prometheus integration)
- ✅ DynamoDB state manager with locking
- ✅ EC2 manager (launch/terminate)
- ✅ Scaling decision engine
- ✅ Slack notifications
- ✅ Lambda deployed with EventBridge trigger
- ✅ End-to-end autoscaling working

---

## Phase 4: Testing & Validation (Week 6-7)

**Goal:** Comprehensive testing with TypeScript/Jest

### Week 6: Unit & Integration Tests

**(See TESTING_GUIDE.md and tests/package.json for full details)**

```bash
cd tests
npm install
npm test
```

### Week 7: Load Testing & Scenarios

```bash
# Load tests
k6 run load/load-test.js
k6 run load/load-test-flash-sale.js

# Manual tests
./manual/test-scale-up.sh
./manual/test-scale-down.sh

# Failure scenarios
./scenarios/test-quota-exceeded.sh
./scenarios/test-prometheus-down.sh
```

**Deliverables Week 6-7:**

- ✅ 8 unit tests passing
- ✅ 4 integration tests passing
- ✅ Load tests validate autoscaling
- ✅ Failure scenarios handled gracefully

---

## Phase 5: Monitoring & Cost Dashboard (Week 8)

**Goal:** Grafana dashboards with cost tracking

**(See COST_DASHBOARD_IMPLEMENTATION_PLAN.md for full details)**

**Deliverables Week 8:**

- ✅ Prometheus metrics configured
- ✅ CloudWatch billing integrated
- ✅ Cost dashboard in Grafana
- ✅ Before/after savings shown
- ✅ Alerts configured

---

## Phase 6: Production Hardening (Week 9-10)

**Goal:** Security, documentation, deployment automation

### Week 9: Security & Compliance

#### 6.1 Security Hardening (4 hours)

```bash
# Rotate secrets
aws secretsmanager rotate-secret \
  --secret-id smartscale/k3s-token \
  --rotation-lambda-arn <rotation-lambda>

# Enable CloudTrail
aws cloudtrail create-trail \
  --name smartscale-audit \
  --s3-bucket-name smartscale-audit-logs

# Enable GuardDuty
aws guardduty create-detector --enable

# Review IAM policies (principle of least privilege)
aws iam simulate-principal-policy \
  --policy-source-arn <role-arn> \
  --action-names ec2:RunInstances
```

#### 6.2 Backup & Disaster Recovery (2 hours)

```bash
# DynamoDB point-in-time recovery
aws dynamodb update-continuous-backups \
  --table-name k3s-autoscaler-state \
  --point-in-time-recovery-specification PointInTimeRecoveryEnabled=true

# K3s etcd backups
kubectl apply -f k3s/backup-cronjob.yaml

# Infrastructure as Code backups
git tag v1.0-production
git push --tags
```

#### 6.3 Documentation (4 hours)

- [ ] Architecture diagrams
- [ ] Runbooks (scale-up/down procedures)
- [ ] Troubleshooting guide
- [ ] Cost analysis report

### Week 10: CI/CD & Final Testing

#### 6.4 GitHub Actions Workflow (3 hours)

**File:** `.github/workflows/deploy.yml`

```yaml
name: Deploy SmartScale

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-node@v3
        with:
          node-version: "18"

      - name: Install dependencies
        run: |
          cd tests
          npm install

      - name: Run tests
        run: |
          cd tests
          npm run test:ci

      - name: Upload coverage
        uses: codecov/codecov-action@v3
        with:
          files: ./tests/coverage/lcov.info

  deploy-infrastructure:
    needs: test
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-node@v3
      - uses: pulumi/actions@v4
        with:
          command: up
          stack-name: smartscale-prod
        env:
          PULUMI_ACCESS_TOKEN: ${{ secrets.PULUMI_ACCESS_TOKEN }}
          AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
          AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}

  deploy-lambda:
    needs: deploy-infrastructure
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: "3.11"

      - name: Package Lambda
        run: |
          cd lambda
          pip install -r requirements.txt -t .
          zip -r function.zip .

      - name: Deploy Lambda
        run: |
          aws lambda update-function-code \
            --function-name k3s-autoscaler \
            --zip-file fileb://lambda/function.zip
```

#### 6.5 Final Production Checklist (2 hours)

```bash
#!/bin/bash
echo "=== SmartScale Production Readiness Checklist ==="

# 1. Infrastructure
echo "1. Checking infrastructure..."
pulumi stack output --json | jq .

# 2. K3s cluster
echo "2. Checking K3s cluster..."
kubectl get nodes
kubectl get pods -A

# 3. Lambda function
echo "3. Checking Lambda..."
aws lambda get-function --function-name k3s-autoscaler

# 4. Autoscaling working
echo "4. Testing autoscaler..."
aws lambda invoke --function-name k3s-autoscaler /tmp/test.json
cat /tmp/test.json

# 5. Monitoring
echo "5. Checking Prometheus & Grafana..."
curl -s http://$MASTER_IP:30090/-/healthy
curl -s http://$MASTER_IP:3000/api/health

# 6. Costs
echo "6. Current AWS costs..."
aws cloudwatch get-metric-statistics \
  --namespace AWS/Billing \
  --metric-name EstimatedCharges \
  --dimensions Name=Currency,Value=USD \
  --start-time $(date -u -d '1 day ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 86400 \
  --statistics Maximum \
  --region us-east-1

echo "=== Production Ready! ==="
```

**Deliverables Week 9-10:**

- ✅ Security hardened (secrets rotation, IAM policies)
- ✅ Disaster recovery plan
- ✅ Complete documentation
- ✅ CI/CD pipeline
- ✅ Production deployment successful

---

## Success Metrics

**Technical:**

- [ ] Autoscaling triggers within 5 minutes of load change
- [ ] No manual intervention required for 30 days
- [ ] 99.9% uptime for K3s cluster
- [ ] Lambda execution time <10 seconds
- [ ] Zero race conditions (DynamoDB locking works)

**Business:**

- [ ] 40-50% cost reduction vs fixed 10-node cluster
- [ ] Average node count 4-6 (vs 10 baseline)
- [ ] Monthly AWS bill $180-220 (vs $360 baseline)
- [ ] ROI achieved in Month 1

**Operational:**

- [ ] Prometheus metrics accurate
- [ ] Grafana dashboards accessible
- [ ] Slack alerts working
- [ ] All tests passing in CI/CD
- [ ] Documentation complete

---

## Timeline Summary

| Phase     | Weeks        | Key Deliverable                              |
| --------- | ------------ | -------------------------------------------- |
| Phase 1   | 1-2          | Infrastructure (VPC, IAM, DynamoDB, Secrets) |
| Phase 2   | 3            | K3s Cluster (Master + Workers)               |
| Phase 3   | 4-5          | Lambda Autoscaler (Python logic)             |
| Phase 4   | 6-7          | Testing (TypeScript/Jest, k6, manual)        |
| Phase 5   | 8            | Monitoring (Grafana cost dashboard)          |
| Phase 6   | 9-10         | Production (Security, CI/CD, docs)           |
| **Total** | **10 weeks** | **Production-ready autoscaler**              |

---

## Cost Breakdown

**Development Costs:**

- AWS resources during dev: ~$100-150 (10 weeks)
- Domain/SSL (optional): $12/year
- Developer time: 120-150 hours

**Monthly Production Costs:**

- EC2 (avg 5 nodes): $150
- Lambda: $5
- DynamoDB: $3
- Other: $12
- **Total: ~$170-200/month**

**vs Baseline:** $360/month (10 fixed nodes)  
**Savings:** $160-190/month (44-53%)

---

## Next Steps

Ready to begin? Start with Phase 1:

```bash
# 1. Clone repository
git clone https://github.com/BayajidAlam/node-fleet.git
cd node-fleet

# 2. Initialize Pulumi
cd pulumi
npm install
pulumi login
pulumi stack init smartscale-dev

# 3. Configure AWS
pulumi config set aws:region ap-south-1
aws configure

# 4. Deploy first resources
pulumi up

# Follow implementation plan week by week!
```

Good luck! 🚀
