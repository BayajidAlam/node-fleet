import * as pulumi from "@pulumi/pulumi";
import * as aws from "@pulumi/aws";
import * as fs from "fs";
import { publicSubnet1, publicSubnet2 } from "./vpc";
import { workerSg } from "./security-groups";
import { workerInstanceProfile } from "./iam";
import { keyPair } from "./keypair";

const config = new pulumi.Config("node-fleet");
const clusterName = config.require("clusterName");

const ubuntuAmi = aws.ec2.getAmi({
  mostRecent: true,
  owners: ["099720109477"],
  filters: [
    {
      name: "name",
      values: ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"],
    },
    { name: "virtualization-type", values: ["hvm"] },
  ],
});

const workerUserData = fs.readFileSync("../k3s/worker-userdata.sh", "utf8");

// Worker launch template for On-Demand instances
export const workerLaunchTemplate = new aws.ec2.LaunchTemplate(
  "worker-template",
  {
    namePrefix: "k3s-worker-",
    imageId: ubuntuAmi.then((ami) => ami.id),
    instanceType: "t3.medium",
    keyName: keyPair.keyName,
    vpcSecurityGroupIds: [workerSg.id],
    iamInstanceProfile: { name: workerInstanceProfile.name },
    userData: pulumi
      .output(workerUserData)
      .apply((data) => Buffer.from(data).toString("base64")),
    blockDeviceMappings: [
      {
        deviceName: "/dev/sda1",
        ebs: {
          volumeSize: 20,
          volumeType: "gp3",
          deleteOnTermination: true,
        },
      },
    ],
    tagSpecifications: [
      {
        resourceType: "instance",
        tags: {
          Name: `${clusterName}-worker`,
          Role: "k3s-worker",
          Project: "node-fleet",
          ManagedBy: "autoscaler",
          InstanceType: "on-demand",
        },
      },
    ],
  }
);

// BONUS: Spot instance launch template (60-70% cost savings)
export const workerSpotTemplate = new aws.ec2.LaunchTemplate(
  "worker-spot-template",
  {
    namePrefix: "k3s-worker-spot-",
    imageId: ubuntuAmi.then((ami) => ami.id),
    instanceType: "t3.medium",
    keyName: keyPair.keyName,
    vpcSecurityGroupIds: [workerSg.id],
    iamInstanceProfile: { name: workerInstanceProfile.name },
    userData: pulumi
      .output(workerUserData)
      .apply((data) => Buffer.from(data).toString("base64")),
    instanceMarketOptions: {
      marketType: "spot",
      spotOptions: {
        maxPrice: "0.0416", // t3.medium on-demand price as max
        spotInstanceType: "one-time",
        instanceInterruptionBehavior: "terminate",
      },
    },
    blockDeviceMappings: [
      {
        deviceName: "/dev/sda1",
        ebs: {
          volumeSize: 20,
          volumeType: "gp3",
          deleteOnTermination: true,
        },
      },
    ],
    tagSpecifications: [
      {
        resourceType: "instance",
        tags: {
          Name: `${clusterName}-worker-spot`,
          Role: "k3s-worker",
          Project: "node-fleet",
          ManagedBy: "autoscaler",
          InstanceType: "spot",
        },
      },
    ],
  }
);

// BONUS: Multi-AZ subnet list for zone-aware distribution
export const workerSubnets = [publicSubnet1.id, publicSubnet2.id];
export const workerLaunchTemplateId = workerLaunchTemplate.id;
export const workerSpotTemplateId = workerSpotTemplate.id;
