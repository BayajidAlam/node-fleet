import * as pulumi from "@pulumi/pulumi";
import * as aws from "@pulumi/aws";
import { lambdaArtifactsBucket, lambdaPackage } from "./s3";

const config = new pulumi.Config("node-fleet");
const clusterName = config.require("clusterName");

// DynamoDB table for autoscaler state management
export const stateTable = new aws.dynamodb.Table("autoscaler-state", {
  name: `${clusterName}-state`,
  billingMode: "PAY_PER_REQUEST",
  hashKey: "cluster_id",
  attributes: [{ name: "cluster_id", type: "S" }],
  pointInTimeRecovery: { enabled: true },
  streamEnabled: true,
  streamViewType: "NEW_AND_OLD_IMAGES", // Capture full state changes for audit
  tags: {
    Name: `${clusterName}-state`,
    Project: "node-fleet",
  },
});

// BONUS: DynamoDB table for metrics history (predictive scaling)
export const metricsHistoryTable = new aws.dynamodb.Table("metrics-history", {
  name: `${clusterName}-metrics-history`,
  billingMode: "PAY_PER_REQUEST",
  hashKey: "timestamp", // ISO timestamp as primary key
  attributes: [{ name: "timestamp", type: "S" }],
  ttl: {
    attributeName: "ttl", // Auto-expire old metrics after 30 days
    enabled: true,
  },
  pointInTimeRecovery: { enabled: true },
  tags: {
    Name: `${clusterName}-metrics-history`,
    Project: "node-fleet",
  },
});

// Audit Lambda for DynamoDB Streams
const auditLogGroup = new aws.cloudwatch.LogGroup("audit-logs", {
  name: `/aws/lambda/${clusterName}-audit-logger`,
  retentionInDays: 90, // Keep audit logs for 90 days
  tags: {
    Name: `${clusterName}-audit-logs`,
    Project: "node-fleet",
  },
});

const auditLambdaRole = new aws.iam.Role("audit-lambda-role", {
  name: `${clusterName}-audit-lambda-role`,
  assumeRolePolicy: JSON.stringify({
    Version: "2012-10-17",
    Statement: [
      {
        Effect: "Allow",
        Principal: { Service: "lambda.amazonaws.com" },
        Action: "sts:AssumeRole",
      },
    ],
  }),
});

new aws.iam.RolePolicyAttachment("audit-lambda-basic-execution", {
  role: auditLambdaRole.name,
  policyArn: "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
});

new aws.iam.RolePolicy("audit-lambda-dynamodb-stream-policy", {
  role: auditLambdaRole.name,
  policy: stateTable.streamArn.apply((streamArn) =>
    JSON.stringify({
      Version: "2012-10-17",
      Statement: [
        {
          Effect: "Allow",
          Action: [
            "dynamodb:GetRecords",
            "dynamodb:GetShardIterator",
            "dynamodb:DescribeStream",
            "dynamodb:ListStreams",
          ],
          Resource: streamArn,
        },
      ],
    }),
  ),
});

export const auditLambda = new aws.lambda.Function("audit-logger", {
  name: `${clusterName}-audit-logger`,
  runtime: "python3.11",
  handler: "audit_logger.lambda_handler",
  role: auditLambdaRole.arn,
  s3Bucket: lambdaArtifactsBucket.id,
  s3Key: lambdaPackage.key,
  timeout: 60,
  memorySize: 256,
  environment: {
    variables: {
      CLUSTER_ID: clusterName,
    },
  },
  tags: {
    Name: `${clusterName}-audit-logger`,
    Project: "node-fleet",
  },
});

// Event source mapping: DynamoDB Stream → Audit Lambda
export const auditStreamMapping = new aws.lambda.EventSourceMapping(
  "audit-stream-mapping",
  {
    eventSourceArn: stateTable.streamArn,
    functionName: auditLambda.arn,
    startingPosition: "LATEST",
    batchSize: 10,
    maximumBatchingWindowInSeconds: 5,
  },
);
