import * as pulumi from "@pulumi/pulumi";
import * as aws from "@pulumi/aws";
import { slackTopic } from "./sns";
import { autoscalerLambda } from "./lambda";

const config = new pulumi.Config("node-fleet");
const clusterName = config.require("clusterName");

// Critical Alarms (SNS → Email/SMS + Slack)

// 1. Scaling Failure Alarm - 3+ failures in 15 minutes
export const scalingFailureAlarm = new aws.cloudwatch.MetricAlarm(
  "scaling-failure-alarm",
  {
    name: `${clusterName}-scaling-failures`,
    comparisonOperator: "GreaterThanOrEqualToThreshold",
    evaluationPeriods: 1,
    metricName: "ScalingFailures",
    namespace: "NodeFleet/Autoscaler",
    period: 900, // 15 minutes
    statistic: "Sum",
    threshold: 3,
    alarmDescription: "Alert when 3+ scaling failures occur in 15 minutes",
    alarmActions: [slackTopic.arn],
    treatMissingData: "notBreaching",
  }
);

// 2. CPU Overload Alarm - Cluster CPU > 90% for 5+ minutes
export const cpuOverloadAlarm = new aws.cloudwatch.MetricAlarm(
  "cpu-overload-alarm",
  {
    name: `${clusterName}-cpu-overload`,
    comparisonOperator: "GreaterThanThreshold",
    evaluationPeriods: 1,
    metricName: "ClusterCPUUtilization",
    namespace: "NodeFleet/Autoscaler",
    period: 300, // 5 minutes
    statistic: "Average",
    threshold: 90,
    alarmDescription:
      "Alert when cluster CPU exceeds 90% for 5+ minutes (capacity exhausted)",
    alarmActions: [slackTopic.arn],
    treatMissingData: "notBreaching",
  }
);

// 3. At Max Capacity Alarm - Node count = 10 for 10+ minutes
export const maxCapacityAlarm = new aws.cloudwatch.MetricAlarm(
  "max-capacity-alarm",
  {
    name: `${clusterName}-at-max-capacity`,
    comparisonOperator: "GreaterThanOrEqualToThreshold",
    evaluationPeriods: 2,
    metricName: "CurrentNodeCount",
    namespace: "NodeFleet/Autoscaler",
    period: 300, // 5 minutes (2 periods = 10 minutes)
    statistic: "Average",
    threshold: 10,
    alarmDescription:
      "Alert when node count reaches maximum (10) for 10+ minutes - cannot scale further",
    alarmActions: [slackTopic.arn],
    treatMissingData: "notBreaching",
  }
);

// 4. Node Join Failure Alarm - New instance not Ready after 5 minutes
export const nodeJoinFailureAlarm = new aws.cloudwatch.MetricAlarm(
  "node-join-failure-alarm",
  {
    name: `${clusterName}-node-join-failure`,
    comparisonOperator: "GreaterThanThreshold",
    evaluationPeriods: 1,
    metricName: "NodeJoinLatency",
    namespace: "NodeFleet/Autoscaler",
    period: 300, // 5 minutes
    statistic: "Maximum",
    threshold: 300000, // 5 minutes in milliseconds
    alarmDescription:
      "Alert when new node takes longer than 5 minutes to become Ready",
    alarmActions: [slackTopic.arn],
    treatMissingData: "notBreaching",
  }
);

// Warning Alarms (Slack only)

// 5. High Memory Alarm - Memory > 80% for 10 minutes
export const highMemoryAlarm = new aws.cloudwatch.MetricAlarm(
  "high-memory-alarm",
  {
    name: `${clusterName}-high-memory`,
    comparisonOperator: "GreaterThanThreshold",
    evaluationPeriods: 2,
    metricName: "ClusterMemoryUtilization",
    namespace: "NodeFleet/Autoscaler",
    period: 300, // 5 minutes (2 periods = 10 minutes)
    statistic: "Average",
    threshold: 80,
    alarmDescription:
      "Warning: Cluster memory utilization exceeds 80% for 10 minutes",
    alarmActions: [slackTopic.arn],
    treatMissingData: "notBreaching",
  }
);

// 6. Lambda Timeout Warning - Execution time > 50 seconds
export const lambdaTimeoutAlarm = new aws.cloudwatch.MetricAlarm(
  "lambda-timeout-alarm",
  {
    name: `${clusterName}-lambda-timeout`,
    comparisonOperator: "GreaterThanThreshold",
    evaluationPeriods: 1,
    metricName: "Duration",
    namespace: "AWS/Lambda",
    period: 60,
    statistic: "Maximum",
    threshold: 50000, // 50 seconds in milliseconds
    alarmDescription:
      "Warning: Lambda execution time approaching 60s timeout limit",
    alarmActions: [slackTopic.arn],
    treatMissingData: "notBreaching",
    dimensions: {
      FunctionName: autoscalerLambda.name,
    },
  }
);

// 7. Lambda Errors Alarm - Any Lambda errors
export const lambdaErrorAlarm = new aws.cloudwatch.MetricAlarm(
  "lambda-error-alarm",
  {
    name: `${clusterName}-lambda-errors`,
    comparisonOperator: "GreaterThanThreshold",
    evaluationPeriods: 1,
    metricName: "Errors",
    namespace: "AWS/Lambda",
    period: 300, // 5 minutes
    statistic: "Sum",
    threshold: 0,
    alarmDescription: "Alert when Lambda function encounters any errors",
    alarmActions: [slackTopic.arn],
    treatMissingData: "notBreaching",
    dimensions: {
      FunctionName: autoscalerLambda.name,
    },
  }
);

// 8. Pending Pods Alarm - Pending pods for extended period
export const pendingPodsAlarm = new aws.cloudwatch.MetricAlarm(
  "pending-pods-alarm",
  {
    name: `${clusterName}-pending-pods`,
    comparisonOperator: "GreaterThanThreshold",
    evaluationPeriods: 2,
    metricName: "PendingPods",
    namespace: "NodeFleet/Autoscaler",
    period: 180, // 3 minutes (2 periods = 6 minutes)
    statistic: "Average",
    threshold: 0,
    alarmDescription:
      "Alert when pods remain pending for 6+ minutes (autoscaler may not be responding)",
    alarmActions: [slackTopic.arn],
    treatMissingData: "notBreaching",
  }
);

// Export alarm ARNs
export const scalingFailureAlarmArn = scalingFailureAlarm.arn;
export const cpuOverloadAlarmArn = cpuOverloadAlarm.arn;
export const maxCapacityAlarmArn = maxCapacityAlarm.arn;
export const nodeJoinFailureAlarmArn = nodeJoinFailureAlarm.arn;
export const highMemoryAlarmArn = highMemoryAlarm.arn;
export const lambdaTimeoutAlarmArn = lambdaTimeoutAlarm.arn;
export const lambdaErrorAlarmArn = lambdaErrorAlarm.arn;
export const pendingPodsAlarmArn = pendingPodsAlarm.arn;
