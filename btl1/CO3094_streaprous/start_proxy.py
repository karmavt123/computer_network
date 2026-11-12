# reverse_proxy.py
#
# Copyright (C) 2026 pdnguyen of HCMC University of Technology VNU-HCM.
# All rights reserved.
# This file is part of the CO3093/CO3094 course,
# and is released under the "MIT License Agreement". Please see the LICENSE
# file that should have been included as part of this package.
#
# StreapRous release
#
# The authors hereby grant to Licensee personal permission to use
# and modify the Licensed Source Code for the sole purpose of studying
# while attending the course
#


"""
start_proxy
~~~~~~~~~~~~~~~~~

This module serves as the entry point for launching a proxy server using Python's socket framework.
It parses command-line arguments to configure the server's IP address and port, reads virtual host
definitions from a configuration file, and initializes the proxy server with routing information.

Requirements:
--------------
- socket: provide socket networking interface.
- threading: enables concurrent client handling via threads.
- argparse: parses command-line arguments for server configuration.
- re: used for regular expression matching in configuration parsing
- response: response utilities.
- httpadapter: the class for handling HTTP requests.
- urlparse: parses URLs to extract host and port information.
- daemon.create_proxy: initializes and starts the proxy server.

"""

import socket
import threading
import argparse
import re
from urllib.parse import urlparse
from collections import defaultdict

from daemon import create_proxy

PROXY_PORT = 8080


def parse_virtual_hosts(config_file):
    """
    Parses virtual host blocks from a config file.

    Each ``host "name" { ... }`` block may contain ``proxy_pass http://ip:port;`` (repeatable),
    ``proxy_set_header Name value;`` (repeatable) and ``dist_policy policy;``.

    :config_file (str): Path to the proxy configuration file.
    :rtype dict: ``{host: (proxy_pass, policy, set_headers)}`` where ``proxy_pass`` is a
                 single ``"ip:port"`` string or a list of them.
    """

    with open(config_file, 'r') as f:
        config_text = f.read()

    # Match each host block
    host_blocks = re.findall(r'host\s+"([^"]+)"\s*\{(.*?)\}', config_text, re.DOTALL)

    routes = {}
    for host, block in host_blocks:
        # Find all proxy_pass entries
        proxy_passes = re.findall(r'proxy_pass\s+http://([^\s;]+);', block)

        # Find dist_policy if present, default policy is round-robin
        policy_match = re.search(r'dist_policy\s+([\w-]+)', block)
        dist_policy = policy_match.group(1) if policy_match else 'round-robin'

        # Find proxy_set_header directives
        set_headers = {name: value.strip() for name, value in
                       re.findall(r'proxy_set_header\s+(\S+)\s+([^;]+);', block)}

        #
        # @bksysnet: Build the mapping and policy
        # one proxy_pass -> plain string; several -> list balanced by the policy
        #
        if len(proxy_passes) == 1:
            routes[host] = (proxy_passes[0], dist_policy, set_headers)
        else:
            routes[host] = (proxy_passes, dist_policy, set_headers)

    for key, value in routes.items():
        print(key, value)
    return routes


if __name__ == "__main__":
    """
    Entry point for launching the proxy server.

    This block parses command-line arguments to determine the server's IP address
    and port. It then calls `create_backend(ip, port)` to start the RESTful
    application server.

    :arg --server-ip (str): IP address to bind the server (default: 127.0.0.1).
    :arg --server-port (int): Port number to bind the server (default: 9000).
    """

    parser = argparse.ArgumentParser(prog='Proxy', description='', epilog='Proxy daemon')
    parser.add_argument('--server-ip', default='0.0.0.0')
    parser.add_argument('--server-port', type=int, default=PROXY_PORT)
    parser.add_argument('--config', default='config/proxy.conf')

    args = parser.parse_args()
    ip = args.server_ip
    port = args.server_port

    routes = parse_virtual_hosts(args.config)

    create_proxy(ip, port, routes)
