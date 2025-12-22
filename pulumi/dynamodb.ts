import * as pulumi from "@pulumi/pulumi";
import * as aws from "@pulumi/aws";

const config = new pulumi.Config("node-fleet");
const clusterName = config.require("clusterName");

// DynamoDB table for autoscaler state management
export const stateTable = new aws.dynamodb.Table("autoscaler-state", {
  name: `${clusterName}-state`,
  billingMode: "PAY_PER_REQUEST",
  hashKey: "cluster_id",
  attributes: [{ name: "cluster_id", type: "S" }],
  pointInTimeRecovery: { enabled: true },
  tags: {
    Name: `${clusterName}-state`,
    Project: "node-fleet",
  },
});

// BONUS: DynamoDB table for metrics history (predictive scaling)
export const metricsHistoryTable = new aws.dynamodb.Table("metrics-history", {
  name: `${clusterName}-metrics-history`,
  billingMode: "PAY_PER_REQUEST",
  hashKey: "cluster_id",
  rangeKey: "timestamp",
  attributes: [
    { name: "cluster_id", type: "S" },
    { name: "timestamp", type: "N" },
    { name: "hour_of_day", type: "N" },
  ],
  globalSecondaryIndexes: [
    {
      name: "hour-index",
      hashKey: "cluster_id",
      rangeKey: "hour_of_day",
      projectionType: "ALL",
    },
  ],
  ttl: {
    attributeName: "expiry_time",
    enabled: true,
  },
  pointInTimeRecovery: { enabled: true },
  tags: {
    Name: `${clusterName}-metrics-history`,
    Project: "node-fleet",
  },
});
