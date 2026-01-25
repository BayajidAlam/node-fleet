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
          encrypted: "true",
          deleteOnTermination: "true",
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
  },
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
          encrypted: "true",
          deleteOnTermination: "true",
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
  },
);

// BONUS: Multi-AZ subnet list for zone-aware distribution
export const workerSubnets = [publicSubnet1.id, publicSubnet2.id];
export const workerLaunchTemplateId = workerLaunchTemplate.id;
export const workerSpotTemplateId = workerSpotTemplate.id;

// NOTE: If you already have 1 worker running manually, Pulumi will create 1 additional worker
// to reach the minimum of 2 workers. If you have no workers, it will create both.
export const initialWorker1 = new aws.ec2.Instance("initial-worker-1", {
  ami: ubuntuAmi.then((ami) => ami.id),
  instanceType: "t3.medium",
  keyName: keyPair.keyName,
  subnetId: publicSubnet1.id, // AZ-1
  vpcSecurityGroupIds: [workerSg.id],
  iamInstanceProfile: workerInstanceProfile.name,
  userData: workerUserData,
  rootBlockDevice: {
    volumeSize: 20,
    volumeType: "gp3",
    encrypted: true,
    deleteOnTermination: true,
  },
  tags: {
    Name: `${clusterName}-worker-1`,
    Role: "k3s-worker",
    Project: "node-fleet",
    ManagedBy: "pulumi-initial",
    AvailabilityZone: "az-1",
    InitialWorker: "true",
  },
});

export const initialWorker2 = new aws.ec2.Instance("initial-worker-2", {
  ami: ubuntuAmi.then((ami) => ami.id),
  instanceType: "t3.medium",
  keyName: keyPair.keyName,
  subnetId: publicSubnet2.id, // AZ-2 (different AZ for HA)
  vpcSecurityGroupIds: [workerSg.id],
  iamInstanceProfile: workerInstanceProfile.name,
  userData: workerUserData,
  rootBlockDevice: {
    volumeSize: 20,
    volumeType: "gp3",
    encrypted: true,
    deleteOnTermination: true,
  },
  tags: {
    Name: `${clusterName}-worker-2`,
    Role: "k3s-worker",
    Project: "node-fleet",
    ManagedBy: "pulumi-initial",
    AvailabilityZone: "az-2",
    InitialWorker: "true",
  },
});

export const initialWorker1Id = initialWorker1.id;
export const initialWorker2Id = initialWorker2.id;
