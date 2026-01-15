import * as pulumi from "@pulumi/pulumi";
import * as aws from "@pulumi/aws";

const config = new pulumi.Config("node-fleet");
const clusterName = config.require("clusterName");

// VPC with public and private subnets across 2 AZs
export const vpc = new aws.ec2.Vpc("node-fleet-vpc", {
  cidrBlock: "10.0.0.0/16",
  enableDnsHostnames: true,
  enableDnsSupport: true,
  tags: {
    Name: `${clusterName}-vpc`,
    Project: "node-fleet",
  },
});

// Internet Gateway
export const igw = new aws.ec2.InternetGateway("node-fleet-igw", {
  vpcId: vpc.id,
  tags: {
    Name: `${clusterName}-igw`,
    Project: "node-fleet",
  },
});

// Public Subnets (for master and workers with public IPs)
export const publicSubnet1 = new aws.ec2.Subnet("public-subnet-1", {
  vpcId: vpc.id,
  cidrBlock: "10.0.1.0/24",
  availabilityZone: "ap-southeast-1a",
  mapPublicIpOnLaunch: true,
  tags: {
    Name: `${clusterName}-public-1a`,
    Project: "node-fleet",
    Type: "public",
  },
});

export const publicSubnet2 = new aws.ec2.Subnet("public-subnet-2", {
  vpcId: vpc.id,
  cidrBlock: "10.0.2.0/24",
  availabilityZone: "ap-southeast-1b",
  mapPublicIpOnLaunch: true,
  tags: {
    Name: `${clusterName}-public-1b`,
    Project: "node-fleet",
    Type: "public",
  },
});

// Private Subnets (for Lambda if needed)
export const privateSubnet1 = new aws.ec2.Subnet("private-subnet-1", {
  vpcId: vpc.id,
  cidrBlock: "10.0.11.0/24",
  availabilityZone: "ap-southeast-1a",
  tags: {
    Name: `${clusterName}-private-1a`,
    Project: "node-fleet",
    Type: "private",
  },
});

export const privateSubnet2 = new aws.ec2.Subnet("private-subnet-2", {
  vpcId: vpc.id,
  cidrBlock: "10.0.12.0/24",
  availabilityZone: "ap-southeast-1b",
  tags: {
    Name: `${clusterName}-private-1b`,
    Project: "node-fleet",
    Type: "private",
  },
});

// Public Route Table
export const publicRouteTable = new aws.ec2.RouteTable("public-rt", {
  vpcId: vpc.id,
  tags: {
    Name: `${clusterName}-public-rt`,
    Project: "node-fleet",
  },
});

// Route to Internet Gateway
export const publicRoute = new aws.ec2.Route("public-route", {
  routeTableId: publicRouteTable.id,
  destinationCidrBlock: "0.0.0.0/0",
  gatewayId: igw.id,
});

// Associate public subnets with public route table
export const publicSubnet1Association = new aws.ec2.RouteTableAssociation(
  "public-subnet-1-assoc",
  {
    subnetId: publicSubnet1.id,
    routeTableId: publicRouteTable.id,
  }
);

export const publicSubnet2Association = new aws.ec2.RouteTableAssociation(
  "public-subnet-2-assoc",
  {
    subnetId: publicSubnet2.id,
    routeTableId: publicRouteTable.id,
  }
);

// Elastic IP for NAT Gateway
export const natEip = new aws.ec2.Eip("nat-eip", {
  vpc: true,
  tags: {
    Name: `${clusterName}-nat-eip`,
    Project: "node-fleet",
  },
});

// NAT Gateway in Public Subnet 1
export const natGateway = new aws.ec2.NatGateway("nat-gateway", {
  allocationId: natEip.id,
  subnetId: publicSubnet1.id,
  tags: {
    Name: `${clusterName}-nat-gw`,
    Project: "node-fleet",
  },
}, { dependsOn: [igw] }); // Ensure IGW exists first

// Private Route Table
export const privateRouteTable = new aws.ec2.RouteTable("private-rt", {
  vpcId: vpc.id,
  routes: [
    {
      cidrBlock: "0.0.0.0/0",
      natGatewayId: natGateway.id,
    },
  ],
  tags: {
    Name: `${clusterName}-private-rt`,
    Project: "node-fleet",
  },
});

// Associate private subnets with private route table
export const privateSubnet1Association = new aws.ec2.RouteTableAssociation(
  "private-subnet-1-assoc",
  {
    subnetId: privateSubnet1.id,
    routeTableId: privateRouteTable.id,
  }
);

export const privateSubnet2Association = new aws.ec2.RouteTableAssociation(
  "private-subnet-2-assoc",
  {
    subnetId: privateSubnet2.id,
    routeTableId: privateRouteTable.id,
  }
);

// DynamoDB VPC Endpoint (Gateway)
export const dynamodbEndpoint = new aws.ec2.VpcEndpoint("dynamodb-endpoint", {
  vpcId: vpc.id,
  serviceName: `com.amazonaws.ap-southeast-1.dynamodb`,
  vpcEndpointType: "Gateway",
  routeTableIds: [privateRouteTable.id],
  tags: {
    Name: `${clusterName}-dynamodb-endpoint`,
    Project: "node-fleet",
  },
});

// Export subnet AZs for Multi-AZ logic
export const workerSubnets = [publicSubnet1.id, publicSubnet2.id];
export const workerAZs = [
  publicSubnet1.availabilityZone,
  publicSubnet2.availabilityZone,
  privateSubnet1.availabilityZone,
  privateSubnet2.availabilityZone,
];
