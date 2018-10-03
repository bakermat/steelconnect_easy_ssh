#!/usr/local/bin/python3
"""
Easy way to SSH to SteelConnect devices, via either SCM SSH Tunnel
or directly via SSH.

Prerequisites:
- Python 3.6 or higher.
- OS with OpenSSH client (Linux, Mac, Windows 10 w/ April 2018 update*)
- REST API + SSH enabled in SCM realm and public key of client in SCM
- requests module
- steelconnection module
Modules can be installed with 'pip3 install requests steelconnection'

* = Windows 10 w/April 2018 supports OpenSSH, but still no nc/HTTP proxy
support so SCM SSH tunnel won't work.

USAGE:
    steelconnect_easy_ssh.py, no parameters required.
    Rename config.ini.example to config.ini to set SCM credentials, optional.

"""
import configparser
import signal
import socket
import subprocess
import sys
import time
from collections import namedtuple
import requests
import steelconnection


config = configparser.ConfigParser()
config.read('config.ini')
try:
    SCM_REALM = config['SCM']['REALM']
    SCM_USER = config['SCM']['USERNAME']
    SCM_PW = config['SCM']['PASSWORD']
except KeyError:
    print("config.ini file not found, please enter SCM details:")


def handle_error(function):
    """ Function to capture possible errors """
    def handle_problems(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except requests.exceptions.RequestException:
            print(f"Error: can't connect to SCM. Please verify config.ini or network connectivity.")
            sys.exit(0)
        except steelconnection.exceptions.AuthenticationError:
            print(f"401 Error: Incorrect username or password for {SCM_REALM}")
            sys.exit(0)
        except steelconnection.exceptions.APINotEnabled:
            print(f"502 Error: REST API is not enabled on {SCM_REALM}")
            sys.exit(0)
    return handle_problems


@handle_error
def get_items(sc, items):
    """Call SCM Config API for items"""
    items = sc.get(items)
    return items


def getstatus_items(sc, items):
    """Call SCM Reporting API for items"""
    items = sc.getstatus(items)
    return items


def get_org_details(orgs):
    """Get node and site details, store in Org object"""
    org_details = []
    for org in orgs:
        org_id = org['id']
        org_name = org['name']
        org_longname = org['longname']
        Org = namedtuple('Org', ['org_id', 'org_name', 'org_longname'])
        org_details.extend([Org(org_id, org_name, org_longname)])
    return org_details


def get_node_details(sc, sites, nodes, orgs, uplinks_status, nodes_status):
    """Put all relevant node, site, org and uplink info in Node object"""
    node_details = []
    # Loop through all sites, get nodes per site, get org, uplinks per node
    for site in sites:
        for node in nodes:
            if node['site'] == site['id']:
                uplink_details = []
                site_name = site['name']
                site_id = site['id']
                node_id = node['id']
                model = sc.lookup.model(node['model'])
                serial = node['serial'] or 'shadow'
                for org in orgs:
                    if node['org'] == org.org_id:
                        org_name = org.org_name
                        break
                for uplink in uplinks_status:
                    if node['id'] == uplink['node']:
                        # store all valid uplink IPs
                        if not ((uplink['v4ip_ext'] is None) or (uplink['v4ip_ext'] == '')):
                            uplink_details.append(uplink['v4ip'])
                            uplink_details.append(uplink['v4ip_ext'])
                # this removes redundant IPs, in case v4ip == v4ip_ext
                uplink_details = list(dict.fromkeys(uplink_details))
                # if device is HA master/backup, add HA state to site name
                for node_status in nodes_status:
                    if node['id'] == node_status['id']:
                        if ((node_status['ha_state'] == 'master') or
                                (node_status['ha_state'] == 'backup')):
                            ha_state_msg = " [HA " + node_status['ha_state'].capitalize() + "]"
                            site_name = site_name + ha_state_msg
                if serial != 'shadow' and "Xirrus" not in model:
                    Node = namedtuple('Node', ['site_name', 'site_id', 'node_id',
                                               'model', 'serial', 'org', 'uplinks'])
                    node_details.extend([Node(site_name, site_id, node_id,
                                              model, serial, org_name, uplink_details)])
    # sort nodes by org + site_name, case-insensitive
    node_details = sorted(node_details, key=lambda x: (x.org.casefold(), x.site_name.casefold()))
    return node_details


def list_nodes(node_details, active_tunnels):
    """ List all sites and nodes for user to select from in main() """
    tunnel_list = {}
    print("-"*104)
    print("Id"+(" ")*4+"Organisation"+(" ")*24+"Site"+(" ")*32+"Model"+(" ")*5+"Serial")
    print("-"*104)
    for index, node in enumerate(node_details, start=1):
        for active_tunnel in active_tunnels:
            if node.node_id == active_tunnel['node_id']:
                print(f"{index: <4}* {node.org: <35} {node.site_name: <35}"
                      f" {node.model: <9} {node.serial: <17}")
                break
        else:
            print(f"{index: <5} {node.org: <35} {node.site_name: <35}"
                  f" {node.model: <9} {node.serial: <17}")
        tunnel_list[index] = {'node_id': node.node_id, 'site_id': node.site_id,
                              'name': node.site_name, 'uplinks': node.uplinks}
    return tunnel_list


def select_node_detail(node):
    """ Print and build dict of uplinks for submenu once site is selected."""
    uplink_list = {}
    print(f"Select how to setup tunnel to {node['name']}")
    print(f"1 Build SSH tunnel via SteelConnect Manager")
    for index, uplink in enumerate(node['uplinks'], start=2):
        print(f"{index} SSH to {uplink}")
        uplink_list[index] = uplink
    return uplink_list


def start_tunnel(sc, index):
    """ Start SCM SSH tunnel to node."""
    print(f"Starting tunnel to: {index['name']}")
    sc.post('sshtunnel/'+index['node_id'])
    time.sleep(3)
    tunnel_status = sc.get('sshtunnel/'+index['node_id'])
    ssh_command = tunnel_status['ssh_help']
    # change keepalive from 60 to 30 seconds for increased session stability
    ssh_command = ssh_command.replace("ServerAliveInterval=60", "ServerAliveInterval=30")
    try:
        subprocess.run(ssh_command, shell=True)
    except OSError as error_msg:
        print(f"Error: {str(error_msg)}")
    else:
        # after SSH session exits, stop tunnel to clean up and show list
        stop_tunnel(sc, index['node_id'])
        main(sc)


def stop_tunnel(sc, node):
    """ Stop SCM SSH tunnel """
    sc.delete('sshtunnel/'+node)


def start_ssh_direct(sc, ip_addr):
    """ Setup direct SSH connection to node """
    print(f"Connecting via SSH to {ip_addr}")
    # setup SSH connection, with 3 second timeout.
    ssh_command = "ssh -tt -o ConnectTimeout=3 -o ServerAliveInterval=30 root@" + ip_addr
    try:
        subprocess.run(ssh_command, shell=True)
    except OSError as error_msg:
        print(f"Error: {str(error_msg)}")
    else:
        main(sc)


@handle_error
def main(sc=None):
    """ Main function """
    try:
        if sc is None:
            sc = steelconnection.SConnect(SCM_REALM, SCM_USER, SCM_PW)
    except NameError:
        sc = steelconnection.SConnect()
    orgs = get_items(sc, 'orgs')
    sites = get_items(sc, 'sites')
    nodes = get_items(sc, 'nodes')
    uplinks_status = getstatus_items(sc, 'uplinks')
    nodes_status = getstatus_items(sc, 'nodes')
    org_details = get_org_details(orgs)
    node_details = get_node_details(sc, sites, nodes, org_details, uplinks_status, nodes_status)
    active_tunnels = get_items(sc, 'sshtunnel')
    nodes_list = list_nodes(node_details, active_tunnels)
    try:
        selected_site = int(input("Type number to select site, or anything else to quit: "))
        uplink_list = select_node_detail(nodes_list[selected_site])
        selected_ssh_option = int(input("Selection: "))
    except ValueError:
        sys.exit(0)
    if selected_ssh_option == 1:
        start_tunnel(sc, nodes_list[selected_site])
    else:
        start_ssh_direct(sc, uplink_list[selected_ssh_option])


def signal_handler(sig, frame):
    """Catch CTRL+C when exiting application for clean exit. """
    print("\nCTRL+C pressed. Bye!")
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)
if __name__ == "__main__":
    main()
