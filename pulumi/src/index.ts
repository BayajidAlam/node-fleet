import * as pulumi from "@pulumi/pulumi";
import * as aws from "@pulumi/aws";

// Import all infrastructure modules
import {
  vpc,
  publicSubnet1,
  publicSubnet2,
  privateSubnet1,
  privateSubnet2,
} from "./vpc";
import { masterSg, workerSg, lambdaSg } from "./security-groups";
import {
  masterRole,
  workerRole,
  lambdaRole,
  masterInstanceProfile,
  workerInstanceProfile,
} from "./iam";
import { stateTable, metricsHistoryTable } from "./dynamodb";
import { k3sSecret, slackSecret } from "./secrets";
import { slackTopic, slackNotifierLambda } from "./sns";
import { keyPair } from "./keypair";
import { masterInstance, masterPublicIp, masterPrivateIp } from "./ec2-master";
import {
  workerLaunchTemplate,
  workerSpotTemplate,
  initialWorker1,
  initialWorker2,
} from "./ec2-worker";
import {
  autoscalerLambda,
  autoscalerSchedule,
  autoscalerLambdaArn,
  autoscalerScheduleName,
} from "./lambda";
import {
  scalingFailureAlarmArn,
  cpuOverloadAlarmArn,
  maxCapacityAlarmArn,
  nodeJoinFailureAlarmArn,
  highMemoryAlarmArn,
  lambdaTimeoutAlarmArn,
  lambdaErrorAlarmArn,
  pendingPodsAlarmArn,
} from "./cloudwatch-alarms";
import { lambdaArtifactsBucket } from "./s3";

// Export all infrastructure outputs
export const vpcId = vpc.id;
export const publicSubnetIds = [publicSubnet1.id, publicSubnet2.id];
export const privateSubnetIds = [privateSubnet1.id, privateSubnet2.id];
export const masterSecurityGroupId = masterSg.id;
export const workerSecurityGroupId = workerSg.id;
export const lambdaSecurityGroupId = lambdaSg.id;
export const stateTableName = stateTable.name;
export const metricsHistoryTableName = metricsHistoryTable.name;
export const k3sTokenSecretArn = k3sSecret.arn;
export const slackWebhookSecretArn = slackSecret.arn;
export const notificationTopicArn = slackTopic.arn;
export const sshKeyName = keyPair.keyName;
export const masterRoleArn = masterRole.arn;
export const workerRoleArn = workerRole.arn;
export const lambdaRoleArn = lambdaRole.arn;

// K3s cluster exports
export const masterInstanceId = masterInstance.id;
export const masterPublicIpAddress = masterPublicIp;
export const masterPrivateIpAddress = masterPrivateIp;
export const initialWorker1InstanceId = initialWorker1.id;
export const initialWorker2InstanceId = initialWorker2.id;
export const workerLaunchTemplateId = workerLaunchTemplate.id;
export const workerSpotLaunchTemplateId = workerSpotTemplate.id;

// Lambda autoscaler exports
export const autoscalerFunctionArn = autoscalerLambdaArn;
export const autoscalerScheduleRuleName = autoscalerScheduleName;
export const lambdaArtifactsBucketName = lambdaArtifactsBucket.id;

// CloudWatch alarm exports
export const alarms = {
  scalingFailure: scalingFailureAlarmArn,
  cpuOverload: cpuOverloadAlarmArn,
  maxCapacity: maxCapacityAlarmArn,
  nodeJoinFailure: nodeJoinFailureAlarmArn,
  highMemory: highMemoryAlarmArn,
  lambdaTimeout: lambdaTimeoutAlarmArn,
  lambdaError: lambdaErrorAlarmArn,
  pendingPods: pendingPodsAlarmArn,
};
