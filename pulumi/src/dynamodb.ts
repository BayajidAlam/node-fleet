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
