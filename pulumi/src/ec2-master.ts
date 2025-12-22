import * as pulumi from "@pulumi/pulumi";
import * as aws from "@pulumi/aws";
import * as fs from "fs";
import { publicSubnet1 } from "./vpc";
import { masterSg } from "./security-groups";
import { masterInstanceProfile } from "./iam";
import { keyPair } from "./keypair";

const config = new pulumi.Config("node-fleet");
const clusterName = config.require("clusterName");

// Get latest Ubuntu AMI (automated, no manual selection)
const ubuntuAmi = aws.ec2.getAmi({
  mostRecent: true,
  owners: ["099720109477"], // Canonical
  filters: [
    {
      name: "name",
      values: ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"],
    },
    { name: "virtualization-type", values: ["hvm"] },
  ],
});

// Read master setup script (automated provisioning)
const masterUserData = fs.readFileSync("../k3s/master-setup.sh", "utf8");

// Master EC2 instance (fully automated)
export const masterInstance = new aws.ec2.Instance(
  "k3s-master",
  {
    instanceType: "t3.medium",
    ami: ubuntuAmi.then((ami) => ami.id),
    subnetId: publicSubnet1.id,
    vpcSecurityGroupIds: [masterSg.id],
    iamInstanceProfile: masterInstanceProfile.name,
    keyName: keyPair.keyName,
    userData: masterUserData,
    tags: {
      Name: `${clusterName}-master`,
      Role: "k3s-master",
      Project: "node-fleet",
    },
    rootBlockDevice: {
      volumeSize: 30,
      volumeType: "gp3",
      deleteOnTermination: true,
    },
  },
  { dependsOn: [keyPair] }
);

export const masterPublicIp = masterInstance.publicIp;
export const masterPrivateIp = masterInstance.privateIp;
