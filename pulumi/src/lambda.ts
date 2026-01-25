import * as pulumi from "@pulumi/pulumi";
import * as aws from "@pulumi/aws";
import { lambdaRole } from "./iam";
import { lambdaSg } from "./security-groups";
import { privateSubnet1, privateSubnet2 } from "./vpc";
import { stateTable, metricsHistoryTable } from "./dynamodb";
import { slackTopic } from "./sns";
import { masterPrivateIp } from "./ec2-master";
import { workerLaunchTemplate, workerSpotTemplate } from "./ec2-worker";
import { lambdaArtifactsBucket, lambdaPackage } from "./s3";

const config = new pulumi.Config("node-fleet");
const clusterName = config.require("clusterName");
const minNodes = config.getNumber("minNodes") || 2;
const maxNodes = config.getNumber("maxNodes") || 10;

// Lambda function for autoscaler
export const autoscalerLambda = new aws.lambda.Function("autoscaler-lambda", {
  name: `${clusterName}-autoscaler`,
  runtime: aws.lambda.Runtime.Python3d11,
  handler: "autoscaler.lambda_handler",
  role: lambdaRole.arn,
  s3Bucket: lambdaArtifactsBucket.id,
  s3Key: lambdaPackage.key,
  timeout: 60, // 60 seconds (per spec)
  memorySize: 256, // 256 MB (per spec)

  vpcConfig: {
    subnetIds: [privateSubnet1.id, privateSubnet2.id],
    securityGroupIds: [lambdaSg.id],
  },

  environment: {
    variables: {
      CLUSTER_ID: clusterName,
      PROMETHEUS_URL: pulumi.interpolate`http://${masterPrivateIp}:30090`,
      STATE_TABLE: stateTable.name,
      METRICS_HISTORY_TABLE: metricsHistoryTable.name,
      MIN_NODES: "2", // Minimum WORKER nodes (not counting master) - total cluster = 3
      MAX_NODES: maxNodes.toString(), // Maximum WORKER nodes (not counting master)
      WORKER_LAUNCH_TEMPLATE_ID: workerLaunchTemplate.id,
      WORKER_SPOT_TEMPLATE_ID: workerSpotTemplate.id,
      SPOT_PERCENTAGE: "70",
      ENABLE_PREDICTIVE_SCALING: "true",
      ENABLE_CUSTOM_METRICS: "false",
      SNS_TOPIC_ARN: slackTopic.arn,
    },
  },

  tags: {
    Name: `${clusterName}-autoscaler`,
    Project: "node-fleet",
    LastUpdated: "2026-01-15T19:02:00",
  },
});

// EventBridge rule to trigger Lambda every 2 minutes
export const autoscalerSchedule = new aws.cloudwatch.EventRule(
  "autoscaler-schedule",
  {
    name: `${clusterName}-autoscaler-schedule`,
    description: "Trigger K3s autoscaler every 2 minutes (updated)",
    scheduleExpression: "rate(2 minutes)",
    tags: {
      Project: "node-fleet",
      LastUpdated: "2026-01-25",
    },
  },
);

// EventBridge target - invoke Lambda
export const autoscalerTarget = new aws.cloudwatch.EventTarget(
  "autoscaler-target",
  {
    rule: autoscalerSchedule.name,
    arn: autoscalerLambda.arn,
  },
);

// Grant EventBridge permission to invoke Lambda
export const lambdaPermission = new aws.lambda.Permission(
  "autoscaler-lambda-permission",
  {
    action: "lambda:InvokeFunction",
    function: autoscalerLambda.name,
    principal: "events.amazonaws.com",
    sourceArn: autoscalerSchedule.arn,
  },
);

// CloudWatch Log Group for Lambda (7 day retention)
export const lambdaLogGroup = new aws.cloudwatch.LogGroup("autoscaler-logs", {
  name: pulumi.interpolate`/aws/lambda/${autoscalerLambda.name}`,
  retentionInDays: 7,
  tags: {
    Project: "node-fleet",
  },
});

// EventBridge rule for EC2 Spot Instance Interruption Warning
export const spotInterruptionRule = new aws.cloudwatch.EventRule(
  "spot-interruption-rule",
  {
    name: `${clusterName}-spot-interruption-rule`,
    description: "Trigger autoscaler on Spot Instance Interruption Warning",
    eventPattern: JSON.stringify({
      source: ["aws.ec2"],
      "detail-type": ["EC2 Spot Instance Interruption Warning"],
    }),
    tags: {
      Project: "node-fleet",
    },
  },
);

// Target for Spot Interruption Rule
export const spotInterruptionTarget = new aws.cloudwatch.EventTarget(
  "spot-interruption-target",
  {
    rule: spotInterruptionRule.name,
    arn: autoscalerLambda.arn,
  },
);

// Permission for Spot Interruption Rule to invoke Lambda
export const spotLambdaPermission = new aws.lambda.Permission(
  "spot-lambda-permission",
  {
    action: "lambda:InvokeFunction",
    function: autoscalerLambda.name,
    principal: "events.amazonaws.com",
    sourceArn: spotInterruptionRule.arn,
  },
);

// Dynamic Scheduler Lambda - adjusts autoscaler frequency based on activity
const schedulerRole = new aws.iam.Role("scheduler-lambda-role", {
  name: `${clusterName}-scheduler-role`,
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

new aws.iam.RolePolicyAttachment("scheduler-basic-execution", {
  role: schedulerRole.name,
  policyArn: "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
});

new aws.iam.RolePolicy("scheduler-eventbridge-policy", {
  role: schedulerRole.name,
  policy: pulumi.all([autoscalerSchedule.arn]).apply(([scheduleArn]) =>
    JSON.stringify({
      Version: "2012-10-17",
      Statement: [
        {
          Effect: "Allow",
          Action: ["events:PutRule", "events:DescribeRule"],
          Resource: scheduleArn,
        },
        {
          Effect: "Allow",
          Action: ["cloudwatch:GetMetricStatistics"],
          Resource: "*",
        },
      ],
    }),
  ),
});

export const schedulerLambda = new aws.lambda.Function("dynamic-scheduler", {
  name: `${clusterName}-dynamic-scheduler`,
  runtime: "python3.11",
  handler: "dynamic_scheduler.lambda_handler",
  role: schedulerRole.arn,
  s3Bucket: lambdaArtifactsBucket.id,
  s3Key: lambdaPackage.key,
  timeout: 60,
  memorySize: 256,
  environment: {
    variables: {
      CLUSTER_ID: clusterName,
      RULE_NAME: autoscalerSchedule.name,
      STATE_TABLE: stateTable.name,
    },
  },
  tags: {
    Name: `${clusterName}-scheduler`,
    Project: "node-fleet",
  },
});

// Trigger scheduler every 10 minutes to evaluate frequency adjustment
const schedulerRule = new aws.cloudwatch.EventRule("scheduler-rule", {
  name: `${clusterName}-scheduler-rule`,
  description: "Trigger dynamic scheduler every 10 minutes",
  scheduleExpression: "rate(10 minutes)",
});

const schedulerTarget = new aws.cloudwatch.EventTarget("scheduler-target", {
  rule: schedulerRule.name,
  arn: schedulerLambda.arn,
});

new aws.lambda.Permission("scheduler-permission", {
  action: "lambda:InvokeFunction",
  function: schedulerLambda.name,
  principal: "events.amazonaws.com",
  sourceArn: schedulerRule.arn,
});

export const autoscalerLambdaArn = autoscalerLambda.arn;
export const autoscalerScheduleName = autoscalerSchedule.name;
