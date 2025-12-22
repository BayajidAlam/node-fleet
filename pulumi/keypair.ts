import * as pulumi from "@pulumi/pulumi";
import * as aws from "@pulumi/aws";
import * as tls from "@pulumi/tls";
import * as fs from "fs";

// Generate SSH key pair automatically
const sshKey = new tls.PrivateKey("smartscale-ssh-key", {
    algorithm: "RSA",
    rsaBits: 4096
});

// Register key pair with AWS
export const keyPair = new aws.ec2.KeyPair("smartscale-key", {
    publicKey: sshKey.publicKeyOpenssh,
    keyName: "smartscale-key"
});

// Save private key to local file (for emergency SSH access)
sshKey.privateKeyPem.apply(key => {
    const keyPath = "../smartscale-key.pem";
    fs.writeFileSync(keyPath, key, { mode: 0o600 });
    console.log(`SSH key saved to ${keyPath}`);
});
