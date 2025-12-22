import * as pulumi from "@pulumi/pulumi";
import * as aws from "@pulumi/aws";

// Import all infrastructure modules
import { vpc, publicSubnet1, publicSubnet2, privateSubnet1, privateSubnet2 } from "./vpc";
import { masterSg, workerSg, lambdaSg } from "./security-groups";
import { masterRole, workerRole, lambdaRole, masterInstanceProfile, workerInstanceProfile } from "./iam";
import { stateTable, metricsHistoryTable } from "./dynamodb";
import { k3sSecret, slackSecret } from "./secrets";
import { slackTopic, slackNotifierLambda } from "./sns";
import { keyPair } from "./keypair";

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
