import * as pulumi from "@pulumi/pulumi";
import * as aws from "@pulumi/aws";
import { vpc } from "./vpc";

const config = new pulumi.Config("node-fleet");
const clusterName = config.require("clusterName");

// Security Group for K3s Master Node
export const masterSg = new aws.ec2.SecurityGroup("master-sg", {
  vpcId: vpc.id,
  description: "Security group for K3s master node",
  ingress: [
    // SSH from anywhere (can be restricted to your IP)
    {
      protocol: "tcp",
      fromPort: 22,
      toPort: 22,
      cidrBlocks: ["0.0.0.0/0"],
      description: "SSH",
    },
    // K3s API server
    {
      protocol: "tcp",
      fromPort: 6443,
      toPort: 6443,
      cidrBlocks: ["0.0.0.0/0"],
      description: "K3s API",
    },
    // Prometheus NodePort
    {
      protocol: "tcp",
      fromPort: 30090,
      toPort: 30090,
      cidrBlocks: ["0.0.0.0/0"],
      description: "Prometheus",
    },
    // Grafana NodePort
    {
      protocol: "tcp",
      fromPort: 30030,
      toPort: 30030,
      cidrBlocks: ["0.0.0.0/0"],
      description: "Grafana",
    },
    // Demo app NodePort
    {
      protocol: "tcp",
      fromPort: 30080,
      toPort: 30080,
      cidrBlocks: ["0.0.0.0/0"],
      description: "Demo App",
    },
    // Flannel VXLAN (K3s networking)
    {
      protocol: "udp",
      fromPort: 8472,
      toPort: 8472,
      cidrBlocks: ["10.0.0.0/16"],
      description: "Flannel VXLAN",
    },
    // Kubelet
    {
      protocol: "tcp",
      fromPort: 10250,
      toPort: 10250,
      cidrBlocks: ["10.0.0.0/16"],
      description: "Kubelet",
    },
    // Node Exporter (Prometheus)
    {
      protocol: "tcp",
      fromPort: 9100,
      toPort: 9100,
      cidrBlocks: ["10.0.0.0/16"],
      description: "Node Exporter",
    },
  ],
  egress: [
    {
      protocol: "-1",
      fromPort: 0,
      toPort: 0,
      cidrBlocks: ["0.0.0.0/0"],
      description: "Allow all outbound",
    },
  ],
  tags: {
    Name: `${clusterName}-master-sg`,
    Project: "node-fleet",
  },
});

// Security Group for K3s Worker Nodes
export const workerSg = new aws.ec2.SecurityGroup("worker-sg", {
  vpcId: vpc.id,
  description: "Security group for K3s worker nodes",
  ingress: [
    // SSH from anywhere
    {
      protocol: "tcp",
      fromPort: 22,
      toPort: 22,
      cidrBlocks: ["0.0.0.0/0"],
      description: "SSH",
    },
    // Flannel VXLAN
    {
      protocol: "udp",
      fromPort: 8472,
      toPort: 8472,
      cidrBlocks: ["10.0.0.0/16"],
      description: "Flannel VXLAN",
    },
    // Kubelet
    {
      protocol: "tcp",
      fromPort: 10250,
      toPort: 10250,
      cidrBlocks: ["10.0.0.0/16"],
      description: "Kubelet",
    },
    // Node Exporter (Prometheus)
    {
      protocol: "tcp",
      fromPort: 9100,
      toPort: 9100,
      cidrBlocks: ["10.0.0.0/16"],
      description: "Node Exporter",
    },
    // NodePort services range
    {
      protocol: "tcp",
      fromPort: 30000,
      toPort: 32767,
      cidrBlocks: ["10.0.0.0/16"],
      description: "NodePort services",
    },
  ],
  egress: [
    {
      protocol: "-1",
      fromPort: 0,
      toPort: 0,
      cidrBlocks: ["0.0.0.0/0"],
      description: "Allow all outbound",
    },
  ],
  tags: {
    Name: `${clusterName}-worker-sg`,
    Project: "node-fleet",
  },
});

// Security Group for Lambda Function
export const lambdaSg = new aws.ec2.SecurityGroup("lambda-sg", {
  vpcId: vpc.id,
  description: "Security group for autoscaler Lambda function",
  egress: [
    {
      protocol: "-1",
      fromPort: 0,
      toPort: 0,
      cidrBlocks: ["0.0.0.0/0"],
      description: "Allow all outbound",
    },
  ],
  tags: {
    Name: `${clusterName}-lambda-sg`,
    Project: "node-fleet",
  },
});
