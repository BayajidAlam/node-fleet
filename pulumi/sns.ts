import * as pulumi from "@pulumi/pulumi";
import * as aws from "@pulumi/aws";
import { lambdaRole } from "./iam";

const config = new pulumi.Config("smartscale");
const clusterName = config.require("clusterName");

export const slackTopic = new aws.sns.Topic("smartscale-notifications", {
    name: `${clusterName}-notifications`,
    displayName: "SmartScale Autoscaler Notifications"
});

// Lambda function to forward SNS to Slack webhook (no manual HTTPS subscription)
const slackNotifierCode = `import json
import urllib.request
import boto3
import os

def handler(event, context):
    # Get webhook URL from Secrets Manager
    secrets = boto3.client('secretsmanager')
    webhook_url = secrets.get_secret_value(SecretId='smartscale/slack-webhook')['SecretString']
    
    # Extract SNS message
    message = event['Records'][0]['Sns']['Message']
    
    # Send to Slack
    payload = {'text': message}
    req = urllib.request.Request(webhook_url, json.dumps(payload).encode('utf-8'))
    req.add_header('Content-Type', 'application/json')
    
    try:
        urllib.request.urlopen(req)
        return {'statusCode': 200}
    except Exception as e:
        print(f"Error sending to Slack: {e}")
        return {'statusCode': 500, 'body': str(e)}
`;

export const slackNotifierLambda = new aws.lambda.Function("slack-notifier", {
    runtime: "python3.11",
    handler: "index.handler",
    role: lambdaRole.arn,
    code: new pulumi.asset.AssetArchive({
        "index.py": new pulumi.asset.StringAsset(slackNotifierCode)
    }),
    timeout: 30,
    memorySize: 128
});

// SNS subscribes to Lambda (not webhook directly - more reliable)
export const slackSubscription = new aws.sns.TopicSubscription("slack-sub", {
    topic: slackTopic.arn,
    protocol: "lambda",
    endpoint: slackNotifierLambda.arn
});

// Allow SNS to invoke Lambda
export const allowSns = new aws.lambda.Permission("allow-sns", {
    action: "lambda:InvokeFunction",
    function: slackNotifierLambda.name,
    principal: "sns.amazonaws.com",
    sourceArn: slackTopic.arn
});
