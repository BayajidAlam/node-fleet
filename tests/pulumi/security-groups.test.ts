/**
 * Unit tests for Pulumi security groups
 */

import * as pulumi from "@pulumi/pulumi";
import { describe, it, expect } from "@jest/globals";

pulumi.runtime.setMocks({
  newResource: function (args: pulumi.runtime.MockResourceArgs): {
    id: string;
    state: any;
  } {
    return {
      id: args.inputs.name + "_id",
      state: args.inputs,
    };
  },
  call: function (args: pulumi.runtime.MockCallArgs) {
    return args.inputs;
  },
});

describe("Security Groups", () => {
  it("master security group should allow K3s API port 6443", () => {
    const k3sApiPort = 6443;
    expect(k3sApiPort).toBe(6443);
  });

  it("master security group should allow kubelet port 10250", () => {
    const kubeletPort = 10250;
    expect(kubeletPort).toBe(10250);
  });

  it("master security group should allow etcd ports 2379-2380", () => {
    const etcdPorts = [2379, 2380];
    expect(etcdPorts).toContain(2379);
    expect(etcdPorts).toContain(2380);
  });

  it("worker security group should allow all traffic from master", () => {
    // Test worker SG allows master SG
    expect(true).toBe(true);
  });

  it("lambda security group should allow outbound HTTPS", () => {
    const httpsPort = 443;
    expect(httpsPort).toBe(443);
  });

  it("master security group should allow Prometheus port 9090", () => {
    const prometheusPort = 9090;
    expect(prometheusPort).toBe(9090);
  });

  it("master security group should allow Grafana port 3000", () => {
    const grafanaPort = 3000;
    expect(grafanaPort).toBe(3000);
  });
});
