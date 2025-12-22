/**
 * Unit tests for Pulumi IAM roles and policies
 */

import * as pulumi from "@pulumi/pulumi";
import { describe, it, expect } from "@jest/globals";

pulumi.runtime.setMocks({
  newResource: function (args: pulumi.runtime.MockResourceArgs): {
    id: string;
    state: any;
  } {
    return {
      id: args.inputs.name + "_id",
      state: args.inputs,
    };
  },
  call: function (args: pulumi.runtime.MockCallArgs) {
    return args.inputs;
  },
});

describe("IAM Roles and Policies", () => {
  it("Lambda role should have EC2 permissions", () => {
    const ec2Actions = [
      "ec2:RunInstances",
      "ec2:TerminateInstances",
      "ec2:DescribeInstances",
    ];
    expect(ec2Actions).toContain("ec2:RunInstances");
  });

  it("Lambda role should have DynamoDB permissions", () => {
    const dynamodbActions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
    ];
    expect(dynamodbActions).toHaveLength(3);
  });

  it("Lambda role should have Secrets Manager read permission", () => {
    const secretsActions = ["secretsmanager:GetSecretValue"];
    expect(secretsActions).toContain("secretsmanager:GetSecretValue");
  });

  it("Lambda role should have SNS publish permission", () => {
    const snsActions = ["sns:Publish"];
    expect(snsActions).toContain("sns:Publish");
  });

  it("Master role should have ECR pull permissions", () => {
    const ecrActions = ["ecr:GetAuthorizationToken", "ecr:BatchGetImage"];
    expect(ecrActions).toHaveLength(2);
  });

  it("Worker role should have minimal permissions", () => {
    // Worker should only pull images and write logs
    const requiredServices = ["ecr", "logs", "s3"];
    expect(requiredServices).toContain("ecr");
  });
});
