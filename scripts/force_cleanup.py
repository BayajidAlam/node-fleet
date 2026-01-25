
import boto3
import sys
import logging
import time

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger()

def cleanup_vpc(vpc_id, region="ap-southeast-1"):
    ec2 = boto3.resource('ec2', region_name=region)
    client = boto3.client('ec2', region_name=region)
    vpc = ec2.Vpc(vpc_id)

    if not vpc:
        logger.error(f"VPC {vpc_id} not found")
        return

    logger.info(f"Starting cleanup for VPC: {vpc_id}")

    # Delete VPC Endpoints
    logger.info("Checking VPC Endpoints...")
    try:
        endpoints = client.describe_vpc_endpoints(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])
        if endpoints['VpcEndpoints']:
            ep_ids = [ep['VpcEndpointId'] for ep in endpoints['VpcEndpoints']]
            logger.info(f"Deleting Endpoints: {ep_ids}")
            client.delete_vpc_endpoints(VpcEndpointIds=ep_ids)
            time.sleep(5) # Wait for deletion
    except Exception as e:
        logger.warning(f"Endpoint check failed: {e}")

    # Delete Subnets (and ENIs if any remain)
    logger.info("Checking Subnets...")
    for subnet in vpc.subnets.all():
        logger.info(f"Checking Subnet: {subnet.id}")
        # Check for lingering ENIs in subnet
        enis = client.describe_network_interfaces(Filters=[{'Name': 'subnet-id', 'Values': [subnet.id]}])
        for eni in enis['NetworkInterfaces']:
            logger.info(f"Deleting lingering ENI: {eni['NetworkInterfaceId']}")
            try:
                if eni['Attachment']['Status'] == 'attached':
                    client.detach_network_interface(AttachmentId=eni['Attachment']['AttachmentId'], Force=True)
                    time.sleep(2)
                client.delete_network_interface(NetworkInterfaceId=eni['NetworkInterfaceId'])
            except Exception as e:
                logger.warning(f"Failed to delete ENI {eni['NetworkInterfaceId']}: {e}")
        try:
            logger.info(f"Deleting Subnet: {subnet.id}")
            subnet.delete()
        except Exception as e:
             logger.error(f"Failed to delete subnet {subnet.id}: {e}")

    # Delete Internet Gateways
    logger.info("Checking Internet Gateways...")
    for igw in vpc.internet_gateways.all():
        logger.info(f"Detaching and Deleting IGW: {igw.id}")
        igw.detach_from_vpc(VpcId=vpc_id)
        igw.delete()

    # Delete Route Tables
    logger.info("Checking Route Tables...")
    for rt in vpc.route_tables.all():
        # Check if main
        is_main = False
        for assoc in rt.associations:
            if assoc.main:
                is_main = True
                break
        if not is_main:
            logger.info(f"Deleting Route Table: {rt.id}")
            try:
                # Disassociate first (if explicit)
                for assoc in rt.associations:
                    if not assoc.main:
                        client.disassociate_route_table(AssociationId=assoc.id)
                rt.delete()
            except Exception as e:
                 logger.error(f"Failed to delete Route Table {rt.id}: {e}")

    # Delete Security Groups
    logger.info("Checking Security Groups...")
    # First revoke all rules to break dependencies
    groups = list(vpc.security_groups.all())
    for sg in groups:
        if sg.group_name == 'default':
            continue
        logger.info(f"Revoking rules for SG: {sg.id}")
        try:
            if sg.ip_permissions:
                sg.revoke_ingress(IpPermissions=sg.ip_permissions)
            if sg.ip_permissions_egress:
                sg.revoke_egress(IpPermissions=sg.ip_permissions_egress)
        except Exception as e:
            logger.warning(f"Error revoking rules for {sg.id}: {e}")

    # Then delete groups
    for sg in groups:
        if sg.group_name == 'default':
            continue
        logger.info(f"Deleting Security Group: {sg.id}")
        try:
            sg.delete()
        except Exception as e:
             logger.error(f"Error deleting SG {sg.id}: {e}")

    # Delete VPC
    logger.info("Deleting VPC...")
    try:
        vpc.delete()
        logger.info(f"VPC {vpc_id} successfully deleted.")
    except Exception as e:
        logger.error(f"Failed to delete VPC {vpc_id}: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 force_cleanup.py <vpc-id>")
        sys.exit(1)
    cleanup_vpc(sys.argv[1])
