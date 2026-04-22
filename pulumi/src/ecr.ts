import * as aws from "@pulumi/aws";

export const demoAppRepo = new aws.ecr.Repository("node-fleet-demo-app", {
  name: "node-fleet-demo-app",
  imageScanningConfiguration: {
    scanOnPush: true,
  },
  imageTagMutability: "MUTABLE",
  encryptionConfiguration: {
    encryptionType: "AES256",
  },
  tags: {
    Project: "node-fleet",
    Environment: "production",
    ManagedBy: "pulumi",
  },
});

new aws.ecr.LifecyclePolicy("demo-app-lifecycle", {
  repository: demoAppRepo.name,
  policy: JSON.stringify({
    rules: [
      {
        rulePriority: 1,
        description: "Keep last 10 images",
        selection: {
          tagStatus: "any",
          countType: "imageCountMoreThan",
          countNumber: 10,
        },
        action: { type: "expire" },
      },
    ],
  }),
});

export const demoAppRepoUrl = demoAppRepo.repositoryUrl;
