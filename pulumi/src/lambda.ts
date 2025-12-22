import * as pulumi from "@pulumi/pulumi";
import * as aws from "@pulumi/aws";
import { lambdaRole } from "./iam";
import { lambdaSg } from "./security-groups";
import { privateSubnet1, privateSubnet2 } from "./vpc";
import { stateTable } from "./dynamodb";
import { k3sSecret, slackSecret } from "./secrets";
import { slackTopic } from "./sns";
import { masterPrivateIp } from "./ec2-master";
import { workerLaunchTemplate, workerSpotTemplate } from "./ec2-worker";

const config = new pulumi.Config("node-fleet");
const clusterName = config.require("clusterName");
const minNodes = config.getNumber("minNodes") || 2;
const maxNodes = config.getNumber("maxNodes") || 10;

// Create Lambda deployment package (placeholder - will be zipped separately)
const lambdaCode = new pulumi.asset.AssetArchive({
  ".": new pulumi.asset.FileArchive("../lambda"),
});

// Lambda function for autoscaler
export const autoscalerLambda = new aws.lambda.Function(
  "autoscaler-lambda",
  {
    name: `${clusterName}-autoscaler`,
    runtime: aws.lambda.Runtime.Python3d11,
    handler: "autoscaler.lambda_handler",
    role: lambdaRole.arn,
    code: lambdaCode,
    timeout: 300, // 5 minutes
    memorySize: 512,
    
    vpcConfig: {
      subnetIds: [privateSubnet1.id, privateSubnet2.id],
      securityGroupIds: [lambdaSg.id],
    },
    
    environment: {
      variables: {
        CLUSTER_ID: clusterName,
        PROMETHEUS_URL: pulumi.interpolate`http://${masterPrivateIp}:30090`,
        STATE_TABLE: stateTable.name,
        MIN_NODES: minNodes.toString(),
        MAX_NODES: maxNodes.toString(),
        WORKER_LAUNCH_TEMPLATE_ID: workerLaunchTemplate.id,
        WORKER_SPOT_TEMPLATE_ID: workerSpotTemplate.id,
        SPOT_PERCENTAGE: "70",
        SNS_TOPIC_ARN: slackTopic.arn,
      },
    },
    
    tags: {
      Name: `${clusterName}-autoscaler`,
      Project: "node-fleet",
    },
  }
);

// EventBridge rule to trigger Lambda every 2 minutes
export const autoscalerSchedule = new aws.cloudwatch.EventRule(
  "autoscaler-schedule",
  {
    name: `${clusterName}-autoscaler-schedule`,
    description: "Trigger K3s autoscaler every 2 minutes",
    scheduleExpression: "rate(2 minutes)",
    tags: {
      Project: "node-fleet",
    },
  }
);

// EventBridge target - invoke Lambda
export const autoscalerTarget = new aws.cloudwatch.EventTarget(
  "autoscaler-target",
  {
    rule: autoscalerSchedule.name,
    arn: autoscalerLambda.arn,
  }
);

// Grant EventBridge permission to invoke Lambda
export const lambdaPermission = new aws.lambda.Permission(
  "autoscaler-lambda-permission",
  {
    action: "lambda:InvokeFunction",
    function: autoscalerLambda.name,
    principal: "events.amazonaws.com",
    sourceArn: autoscalerSchedule.arn,
  }
);

// CloudWatch Log Group for Lambda (7 day retention)
export const lambdaLogGroup = new aws.cloudwatch.LogGroup(
  "autoscaler-logs",
  {
    name: `/aws/lambda/${autoscalerLambda.name}`,
    retentionInDays: 7,
    tags: {
      Project: "node-fleet",
    },
  }
);

export const autoscalerLambdaArn = autoscalerLambda.arn;
export const autoscalerScheduleName = autoscalerSchedule.name;
