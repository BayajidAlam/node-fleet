import * as pulumi from "@pulumi/pulumi";
import * as aws from "@pulumi/aws";
import { lambdaRole } from "./iam";

const config = new pulumi.Config("node-fleet");
const clusterName = config.require("clusterName");

export const slackTopic = new aws.sns.Topic("node-fleet-notifications", {
  name: `${clusterName}-notifications`,
  displayName: "NodeFleet Autoscaler Notifications",
});

// Lambda function to forward SNS to Slack webhook (no manual HTTPS subscription)
const slackNotifierCode = `import json
import urllib.request
import boto3

def format_cloudwatch_alarm(alarm):
    state   = alarm.get('NewStateValue', '?')
    name    = alarm.get('AlarmName', '?')
    desc    = alarm.get('AlarmDescription', '')
    reason  = alarm.get('NewStateReason', '')
    region  = alarm.get('Region', '')
    time    = alarm.get('StateChangeTime', '')

    icon = {'ALARM': ':red_circle:', 'OK': ':large_green_circle:', 'INSUFFICIENT_DATA': ':white_circle:'}.get(state, ':white_circle:')
    return f"{icon} *CloudWatch {state}* — {name}\\n_{desc}_\\n*Reason:* {reason}\\n*Region:* {region} | {time}"

def handler(event, context):
    secrets     = boto3.client('secretsmanager')
    webhook_url = secrets.get_secret_value(SecretId='node-fleet/slack-webhook')['SecretString'].strip()

    record  = event['Records'][0]['Sns']
    raw_msg = record['Message']
    subject = record.get('Subject', '')

    # Format CloudWatch alarm JSON into readable text; pass autoscaler messages through as-is
    try:
        parsed = json.loads(raw_msg)
        if 'AlarmName' in parsed:
            text = format_cloudwatch_alarm(parsed)
        else:
            text = raw_msg
    except (json.JSONDecodeError, TypeError):
        text = raw_msg

    payload = json.dumps({'text': text}).encode('utf-8')
    req     = urllib.request.Request(webhook_url, payload)
    req.add_header('Content-Type', 'application/json')

    try:
        urllib.request.urlopen(req, timeout=10)
        return {'statusCode': 200}
    except Exception as e:
        print(f"Slack delivery failed: {e}")
        raise  # let Lambda retry via SNS DLQ
`;

export const slackNotifierLambda = new aws.lambda.Function("slack-notifier", {
  name: `${clusterName}-slack-notifier`,
  runtime: "python3.11",
  handler: "index.handler",
  role: lambdaRole.arn,
  code: new pulumi.asset.AssetArchive({
    "index.py": new pulumi.asset.StringAsset(slackNotifierCode),
  }),
  timeout: 30,
  memorySize: 128,
});

// SNS subscribes to Lambda (not webhook directly - more reliable)
export const slackSubscription = new aws.sns.TopicSubscription("slack-sub", {
  topic: slackTopic.arn,
  protocol: "lambda",
  endpoint: slackNotifierLambda.arn,
});

// Allow SNS to invoke Lambda
export const allowSns = new aws.lambda.Permission("allow-sns", {
  action: "lambda:InvokeFunction",
  function: slackNotifierLambda.name,
  principal: "sns.amazonaws.com",
  sourceArn: slackTopic.arn,
});
