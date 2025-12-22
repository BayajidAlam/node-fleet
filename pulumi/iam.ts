import * as pulumi from "@pulumi/pulumi";
import * as aws from "@pulumi/aws";

const config = new pulumi.Config("smartscale");
const clusterName = config.require("clusterName");

// IAM Role for K3s Master Node
export const masterRole = new aws.iam.Role("master-role", {
    assumeRolePolicy: JSON.stringify({
        Version: "2012-10-17",
        Statement: [{
            Action: "sts:AssumeRole",
            Effect: "Allow",
            Principal: {
                Service: "ec2.amazonaws.com"
            }
        }]
    }),
    tags: {
        Name: `${clusterName}-master-role`,
        Project: "smartscale"
    }
});

// Master role policies
export const masterPolicy = new aws.iam.RolePolicy("master-policy", {
    role: masterRole.id,
    policy: JSON.stringify({
        Version: "2012-10-17",
        Statement: [
            {
                Effect: "Allow",
                Action: [
                    "ec2:DescribeInstances",
                    "ec2:DescribeTags",
                    "secretsmanager:GetSecretValue",
                    "secretsmanager:UpdateSecret"
                ],
                Resource: "*"
            }
        ]
    })
});

export const masterInstanceProfile = new aws.iam.InstanceProfile("master-instance-profile", {
    role: masterRole.name
});

// IAM Role for K3s Worker Nodes
export const workerRole = new aws.iam.Role("worker-role", {
    assumeRolePolicy: JSON.stringify({
        Version: "2012-10-17",
        Statement: [{
            Action: "sts:AssumeRole",
            Effect: "Allow",
            Principal: {
                Service: "ec2.amazonaws.com"
            }
        }]
    }),
    tags: {
        Name: `${clusterName}-worker-role`,
        Project: "smartscale"
    }
});

// Worker role policies
export const workerPolicy = new aws.iam.RolePolicy("worker-policy", {
    role: workerRole.id,
    policy: JSON.stringify({
        Version: "2012-10-17",
        Statement: [
            {
                Effect: "Allow",
                Action: [
                    "ec2:DescribeInstances",
                    "ec2:DescribeTags",
                    "secretsmanager:GetSecretValue"
                ],
                Resource: "*"
            }
        ]
    })
});

export const workerInstanceProfile = new aws.iam.InstanceProfile("worker-instance-profile", {
    role: workerRole.name
});

// IAM Role for Lambda Autoscaler
export const lambdaRole = new aws.iam.Role("lambda-role", {
    assumeRolePolicy: JSON.stringify({
        Version: "2012-10-17",
        Statement: [{
            Action: "sts:AssumeRole",
            Effect: "Allow",
            Principal: {
                Service: "lambda.amazonaws.com"
            }
        }]
    }),
    tags: {
        Name: `${clusterName}-lambda-role`,
        Project: "smartscale"
    }
});

// Lambda role policies
export const lambdaPolicy = new aws.iam.RolePolicy("lambda-policy", {
    role: lambdaRole.id,
    policy: JSON.stringify({
        Version: "2012-10-17",
        Statement: [
            {
                Effect: "Allow",
                Action: [
                    "ec2:RunInstances",
                    "ec2:TerminateInstances",
                    "ec2:DescribeInstances",
                    "ec2:DescribeInstanceStatus",
                    "ec2:DescribeTags",
                    "ec2:CreateTags",
                    "ec2:DescribeLaunchTemplates"
                ],
                Resource: "*"
            },
            {
                Effect: "Allow",
                Action: [
                    "dynamodb:GetItem",
                    "dynamodb:PutItem",
                    "dynamodb:UpdateItem",
                    "dynamodb:Query"
                ],
                Resource: "*"
            },
            {
                Effect: "Allow",
                Action: [
                    "secretsmanager:GetSecretValue"
                ],
                Resource: "*"
            },
            {
                Effect: "Allow",
                Action: [
                    "sns:Publish"
                ],
                Resource: "*"
            },
            {
                Effect: "Allow",
                Action: [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents"
                ],
                Resource: "*"
            }
        ]
    })
});

// Attach AWS managed policies
export const lambdaVpcPolicy = new aws.iam.RolePolicyAttachment("lambda-vpc-policy", {
    role: lambdaRole.name,
    policyArn: "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
});
