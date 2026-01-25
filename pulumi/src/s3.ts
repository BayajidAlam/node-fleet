import * as pulumi from "@pulumi/pulumi";
import * as aws from "@pulumi/aws";

const config = new pulumi.Config("node-fleet");
const clusterName = config.require("clusterName");

// S3 bucket for Lambda deployment packages
export const lambdaArtifactsBucket = new aws.s3.Bucket("lambda-artifacts", {
  bucket: `${clusterName}-lambda-artifacts-${pulumi.getStack()}`,
  
  // Enable versioning for rollback capability
  versioning: {
    enabled: true,
  },
  
  // Encryption at rest
  serverSideEncryptionConfiguration: {
    rule: {
      applyServerSideEncryptionByDefault: {
        sseAlgorithm: "AES256",
      },
    },
  },
  
  // Lifecycle to clean up old versions
  lifecycleRules: [{
    enabled: true,
    noncurrentVersionExpiration: {
      days: 30,
    },
  }],
  
  tags: {
    Name: `${clusterName}-lambda-artifacts`,
    Project: "node-fleet",
    Purpose: "Lambda deployment packages",
  },
});

// Block public access
export const lambdaBucketPublicAccessBlock = new aws.s3.BucketPublicAccessBlock(
  "lambda-bucket-public-access-block",
  {
    bucket: lambdaArtifactsBucket.id,
    blockPublicAcls: true,
    blockPublicPolicy: true,
    ignorePublicAcls: true,
    restrictPublicBuckets: true,
  },
);

// Upload Lambda deployment package
export const lambdaPackage = new aws.s3.BucketObject("lambda-package", {
  bucket: lambdaArtifactsBucket.id,
  key: "lambda-deployment.zip",
  source: new pulumi.asset.FileAsset("/tmp/lambda-deployment.zip"),
  
  // Use content hash for versioning
  etag: pulumi.output("/tmp/lambda-deployment.zip").apply(() => {
    const crypto = require("crypto");
    const fs = require("fs");
    const fileBuffer = fs.readFileSync("/tmp/lambda-deployment.zip");
    return crypto.createHash("md5").update(fileBuffer).digest("hex");
  }),
  
  tags: {
    Name: "lambda-deployment-package",
    Project: "node-fleet",
  },
});
