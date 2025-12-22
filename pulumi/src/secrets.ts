import * as pulumi from "@pulumi/pulumi";
import * as aws from "@pulumi/aws";
import * as random from "@pulumi/random";

const config = new pulumi.Config("node-fleet");

// Generate random K3s token (will be updated by master node on first boot)
const k3sToken = new random.RandomPassword("k3s-token", {
  length: 32,
  special: false,
});

export const k3sSecret = new aws.secretsmanager.Secret("k3s-token", {
  name: "node-fleet/k3s-token",
  description: "K3s cluster join token",
  recoveryWindowInDays: 0, // Immediate deletion on destroy
});

export const k3sSecretVersion = new aws.secretsmanager.SecretVersion(
  "k3s-token-version",
  {
    secretId: k3sSecret.id,
    secretString: k3sToken.result,
  }
);

// Slack webhook secret (from Pulumi config)
const slackWebhookUrl = config.requireSecret("slackWebhookUrl");

export const slackSecret = new aws.secretsmanager.Secret("slack-webhook", {
  name: "node-fleet/slack-webhook",
  description: "Slack webhook URL for notifications",
  recoveryWindowInDays: 0,
});

export const slackSecretVersion = new aws.secretsmanager.SecretVersion(
  "slack-webhook-version",
  {
    secretId: slackSecret.id,
    secretString: slackWebhookUrl,
  }
);

// Prometheus basic auth credentials
const prometheusPassword = new random.RandomPassword("prometheus-password", {
  length: 32,
  special: true,
});

export const prometheusAuthSecret = new aws.secretsmanager.Secret(
  "prometheus-auth",
  {
    name: "node-fleet/prometheus-auth",
    description: "Prometheus basic authentication credentials",
    recoveryWindowInDays: 0,
  }
);

export const prometheusAuthSecretVersion = new aws.secretsmanager.SecretVersion(
  "prometheus-auth-version",
  {
    secretId: prometheusAuthSecret.id,
    secretString: pulumi.interpolate`{"username":"prometheus-admin","password":"${prometheusPassword.result}"}`,
  }
);
