"""
Create the core configuration which consists of
  * A new VPC
  * An internal subnet containing a Vault server
  * An external subnet containing a Bastion server

The core configuration create all of the infrastructure that is required for
the other production resources to function. In the furture this may include
other servers for services like Authentication.
"""

import library as lib
import configuration
import hosts
import time
import requests
import scalyr

keypair = None

# Region core is created in.  Later versions of boto3 should allow us to
# extract this from the session variable.  Hard coding for now.
CORE_REGION = 'us-east-1'

INCOMING_SUBNET = "52.3.13.189/32" # microns-bastion elastic IP

def create_config(session, domain):
    """Create the CloudFormationConfiguration object."""
    config = configuration.CloudFormationConfiguration(domain, CORE_REGION)

    if config.subnet_domain is not None:
        raise Exception("Invalid VPC domain name")

    vpc_id = lib.vpc_id_lookup(session, domain)
    if session is not None and vpc_id is None:
        raise Exception("VPC does not exists, exiting...")

    global keypair
    keypair = lib.keypair_lookup(session)

    config.add_arg(configuration.Arg.VPC("VPC", vpc_id,
                                         "ID of VPC to create resources in"))

    internal_subnet_id = lib.subnet_id_lookup(session, "external." + domain)
    config.add_arg(configuration.Arg.Subnet("ExternalSubnet",
                                            internal_subnet_id,
                                            "ID of External Subnet to create resources in"))

    internal_sg_id = lib.sg_lookup(session, vpc_id, "internal." + domain)
    config.add_arg(configuration.Arg.SecurityGroup("InternalSecurityGroup",
                                                   internal_sg_id,
                                                   "ID of internal Security Group"))

    config.add_ec2_instance("Auth",
                            "auth." + domain,
                            lib.ami_lookup(session, "auth.boss"),
                            keypair,
                            subnet = "ExternalSubnet",
                            public_ip = True,
                            security_groups = ["InternalSecurityGroup", "AuthSecurityGroup"])

    config.add_security_group("AuthSecurityGroup",
                              "http",
                              [("tcp", "80", "80", "128.244.0.0/16"),
                               ("tcp", "22", "22", INCOMING_SUBNET)])

    return config

def generate(folder, domain):
    """Create the configuration and save it to disk"""
    name = lib.domain_to_stackname("auth." + domain)
    config = create_config(None, domain)
    config.generate(name, folder)

def create(session, domain):
    """Create the configuration, launch it, and initialize Vault"""
    name = lib.domain_to_stackname("auth." + domain)
    config = create_config(session, domain)

    success = config.create(session, name)
    if success:
        try:
            time.sleep(15)
            password = lib.generate_password()
            print("Setting Admin password to: " + password)

            lib.call_ssh(session,
                           lib.keypair_to_file(keypair),
                           "bastion." + domain,
                           "auth." + domain,
                           "/srv/keycloak/bin/add-user.sh -r master -u admin -p " + password)

            lib.call_ssh(session,
                           lib.keypair_to_file(keypair),
                           "bastion." + domain,
                           "auth." + domain,
                           "sudo service keycloak restart")

            lib.call_vault(session,
                           lib.keypair_to_file(keypair),
                           "bastion." + domain,
                           "vault." + domain,
                           "vault-write", "secret/auth", password = password, username = "admin")

        except requests.exceptions.ConnectionError:
            print("Could not connect to Vault")