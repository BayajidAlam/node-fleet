import * as pulumi from "@pulumi/pulumi";
import * as aws from "@pulumi/aws";

const config = new pulumi.Config("node-fleet");
const clusterName = config.require("clusterName");

const region = aws.getRegionOutput().name;
const accountId = aws.getCallerIdentityOutput().accountId;

// ─────────────────────────────────────────────────────────────────────────────
// Master Node Role
// ─────────────────────────────────────────────────────────────────────────────

export const masterRole = new aws.iam.Role("master-role", {
  assumeRolePolicy: JSON.stringify({
    Version: "2012-10-17",
    Statement: [
      {
        Action: "sts:AssumeRole",
        Effect: "Allow",
        Principal: { Service: "ec2.amazonaws.com" },
      },
    ],
  }),
  tags: { Name: `${clusterName}-master-role`, Project: "node-fleet" },
});

// Master only needs to read its own secrets and describe EC2 for cluster awareness
export const masterPolicy = new aws.iam.RolePolicy("master-policy", {
  role: masterRole.id,
  policy: pulumi.all([region, accountId]).apply(([r, a]) =>
    JSON.stringify({
      Version: "2012-10-17",
      Statement: [
        {
          // Describe-only EC2 actions have no resource-level restrictions in IAM
          Sid: "EC2ReadOnly",
          Effect: "Allow",
          Action: ["ec2:DescribeInstances", "ec2:DescribeTags"],
          Resource: "*",
        },
        {
          // Scoped to only the node-fleet secrets this host needs
          Sid: "SecretsManagerNodeFleet",
          Effect: "Allow",
          Action: [
            "secretsmanager:GetSecretValue",
            "secretsmanager:UpdateSecret",
          ],
          Resource: [
            `arn:aws:secretsmanager:${r}:${a}:secret:node-fleet/k3s-token*`,
            `arn:aws:secretsmanager:${r}:${a}:secret:node-fleet/prometheus-auth*`,
            `arn:aws:secretsmanager:${r}:${a}:secret:node-fleet/grafana-admin-password*`,
          ],
        },
      ],
    }),
  ),
});

// Allow SSM agent on master to receive Run Commands from Lambda
export const masterSsmPolicy = new aws.iam.RolePolicyAttachment(
  "master-ssm-policy",
  {
    role: masterRole.name,
    policyArn: "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
  },
);

export const masterInstanceProfile = new aws.iam.InstanceProfile(
  "master-instance-profile",
  { role: masterRole.name },
);

// ─────────────────────────────────────────────────────────────────────────────
// Worker Node Role
// ─────────────────────────────────────────────────────────────────────────────

export const workerRole = new aws.iam.Role("worker-role", {
  assumeRolePolicy: JSON.stringify({
    Version: "2012-10-17",
    Statement: [
      {
        Action: "sts:AssumeRole",
        Effect: "Allow",
        Principal: { Service: "ec2.amazonaws.com" },
      },
    ],
  }),
  tags: { Name: `${clusterName}-worker-role`, Project: "node-fleet" },
});

// Workers only need to read the join token at boot time
export const workerPolicy = new aws.iam.RolePolicy("worker-policy", {
  role: workerRole.id,
  policy: pulumi.all([region, accountId]).apply(([r, a]) =>
    JSON.stringify({
      Version: "2012-10-17",
      Statement: [
        {
          Sid: "EC2ReadOnly",
          Effect: "Allow",
          Action: ["ec2:DescribeInstances", "ec2:DescribeTags"],
          Resource: "*",
        },
        {
          Sid: "SecretsManagerK3sTokenOnly",
          Effect: "Allow",
          Action: ["secretsmanager:GetSecretValue"],
          Resource: `arn:aws:secretsmanager:${r}:${a}:secret:node-fleet/k3s-token*`,
        },
      ],
    }),
  ),
});

export const workerInstanceProfile = new aws.iam.InstanceProfile(
  "worker-instance-profile",
  { role: workerRole.name },
);

// ─────────────────────────────────────────────────────────────────────────────
// Lambda Autoscaler Role
// ─────────────────────────────────────────────────────────────────────────────

export const lambdaRole = new aws.iam.Role("lambda-role", {
  assumeRolePolicy: JSON.stringify({
    Version: "2012-10-17",
    Statement: [
      {
        Action: "sts:AssumeRole",
        Effect: "Allow",
        Principal: { Service: "lambda.amazonaws.com" },
      },
    ],
  }),
  tags: { Name: `${clusterName}-lambda-role`, Project: "node-fleet" },
});

// Lambda policy — each statement scoped to specific resource ARNs
export const lambdaPolicy = new aws.iam.RolePolicy("lambda-policy", {
  role: lambdaRole.id,
  policy: pulumi
    .all([region, accountId, workerRole.arn])
    .apply(([r, a, workerArn]) =>
      JSON.stringify({
        Version: "2012-10-17",
        Statement: [
          {
            // EC2 Describe actions cannot be resource-scoped (AWS limitation)
            Sid: "EC2Describe",
            Effect: "Allow",
            Action: [
              "ec2:DescribeInstances",
              "ec2:DescribeInstanceStatus",
              "ec2:DescribeTags",
              "ec2:DescribeLaunchTemplates",
              "ec2:DescribeSpotPriceHistory",
              "ec2:DescribeAvailabilityZones",
              "ec2:DescribeSubnets",
              "ec2:DescribeSpotInstanceRequests",
            ],
            Resource: "*",
          },
          {
            // Write EC2 actions scoped to node-fleet tagged resources
            Sid: "EC2WriteTagged",
            Effect: "Allow",
            Action: [
              "ec2:RunInstances",
              "ec2:TerminateInstances",
              "ec2:CreateTags",
              "ec2:RequestSpotInstances",
              "ec2:CancelSpotInstanceRequests",
            ],
            Resource: "*",
            Condition: {
              StringEquals: {
                "aws:RequestedRegion": r,
                "ec2:ResourceTag/Project": "node-fleet",
              },
            },
          },
          {
            // Allow RunInstances to tag new instances on creation
            Sid: "EC2RunInstancesTagging",
            Effect: "Allow",
            Action: ["ec2:CreateTags"],
            Resource: `arn:aws:ec2:${r}:${a}:instance/*`,
            Condition: {
              StringEquals: { "ec2:CreateAction": "RunInstances" },
            },
          },
          {
            // DynamoDB scoped to autoscaler tables only
            Sid: "DynamoDBAutoscalerTables",
            Effect: "Allow",
            Action: [
              "dynamodb:GetItem",
              "dynamodb:PutItem",
              "dynamodb:UpdateItem",
              "dynamodb:Query",
            ],
            Resource: [
              `arn:aws:dynamodb:${r}:${a}:table/${clusterName}-state`,
              `arn:aws:dynamodb:${r}:${a}:table/${clusterName}-metrics-history`,
            ],
          },
          {
            // S3 scoped to Lambda artifacts bucket only
            Sid: "S3LambdaArtifacts",
            Effect: "Allow",
            Action: ["s3:GetObject", "s3:ListBucket"],
            Resource: [
              `arn:aws:s3:::${clusterName}-lambda-artifacts-*`,
              `arn:aws:s3:::${clusterName}-lambda-artifacts-*/*`,
            ],
          },
          {
            // Secrets Manager scoped to node-fleet namespace only
            Sid: "SecretsManagerNodeFleet",
            Effect: "Allow",
            Action: ["secretsmanager:GetSecretValue"],
            Resource: `arn:aws:secretsmanager:${r}:${a}:secret:node-fleet/*`,
          },
          {
            // SNS scoped to the autoscaler notifications topic
            Sid: "SNSPublishNotifications",
            Effect: "Allow",
            Action: ["sns:Publish"],
            Resource: `arn:aws:sns:${r}:${a}:${clusterName}-notifications`,
          },
          {
            // SQS scoped to the autoscaler DLQ only
            Sid: "SQSDlq",
            Effect: "Allow",
            Action: ["sqs:SendMessage"],
            Resource: `arn:aws:sqs:${r}:${a}:${clusterName}-autoscaler-dlq`,
          },
          {
            // CloudWatch metrics — namespace-scoped via condition
            Sid: "CloudWatchMetrics",
            Effect: "Allow",
            Action: ["cloudwatch:PutMetricData"],
            Resource: "*",
            Condition: {
              StringEquals: { "cloudwatch:namespace": "NodeFleet/Autoscaler" },
            },
          },
          {
            Sid: "CloudWatchReadMetrics",
            Effect: "Allow",
            Action: ["cloudwatch:GetMetricStatistics"],
            Resource: "*",
          },
          {
            // Logs scoped to this Lambda's log group
            Sid: "CloudWatchLogs",
            Effect: "Allow",
            Action: [
              "logs:CreateLogGroup",
              "logs:CreateLogStream",
              "logs:PutLogEvents",
            ],
            Resource: `arn:aws:logs:${r}:${a}:log-group:/aws/lambda/${clusterName}-autoscaler:*`,
          },
          {
            // Pass worker role to EC2 only (not admin pass-role)
            Sid: "IAMPassWorkerRole",
            Effect: "Allow",
            Action: "iam:PassRole",
            Resource: workerArn,
          },
          {
            // Required for Spot — scoped to EC2 Spot service-linked role only
            Sid: "IAMSpotServiceLinkedRole",
            Effect: "Allow",
            Action: "iam:CreateServiceLinkedRole",
            Resource:
              "arn:aws:iam::*:role/aws-service-role/spot.amazonaws.com/AWSServiceRoleForEC2Spot",
            Condition: {
              StringLike: {
                "iam:AWSServiceName": "spot.amazonaws.com",
              },
            },
          },
          {
            // SSM SendCommand scoped to master node (by tag) and drain document only
            Sid: "SSMDrainMaster",
            Effect: "Allow",
            Action: ["ssm:SendCommand"],
            Resource: [
              `arn:aws:ssm:${r}::document/AWS-RunShellScript`,
              `arn:aws:ec2:${r}:${a}:instance/*`,
            ],
            Condition: {
              StringEquals: {
                "ec2:ResourceTag/Role": "k3s-master",
                "ec2:ResourceTag/Project": "node-fleet",
              },
            },
          },
          {
            // SSM GetCommandInvocation — scoped to this account/region
            Sid: "SSMGetCommandResult",
            Effect: "Allow",
            Action: ["ssm:GetCommandInvocation"],
            Resource: `arn:aws:ssm:${r}:${a}:*`,
          },
          {
            // Cost Explorer has no resource-level restrictions
            Sid: "CostExplorer",
            Effect: "Allow",
            Action: ["ce:GetCostAndUsage", "ce:GetDimensionValues"],
            Resource: "*",
          },
        ],
      }),
    ),
});

// Attach AWS managed VPC execution policy (required for Lambda in VPC)
export const lambdaVpcPolicy = new aws.iam.RolePolicyAttachment(
  "lambda-vpc-policy",
  {
    role: lambdaRole.name,
    policyArn:
      "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole",
  },
);
