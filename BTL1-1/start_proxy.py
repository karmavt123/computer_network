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

Entry point of the reverse proxy. It reads the virtual hosts of
``config/proxy.conf`` and forwards each request to a backend chosen by the
host's distribution policy.

Configuration block::

    host "app.local" {
        proxy_set_header Host $host;
        proxy_pass http://127.0.0.1:9000;
        proxy_pass http://127.0.0.1:9001;
        dist_policy round-robin
    }
"""

import argparse
import re

from daemon import create_proxy

PROXY_PORT = 8080
POLICIES = ("round-robin", "random", "least-conn")


def parse_virtual_hosts(config_file):
    """
    Parses virtual host blocks from a config file.

    :param config_file (str): Path to the NGINX-like config file.
    :rtype dict: ``{host: (backend or [backends], policy, {header: value})}``.
        A host with one ``proxy_pass`` maps to a single ``"ip:port"`` string,
        a host with several maps to a list (a pool).
    """
    with open(config_file, 'r', encoding='utf-8') as f:
        config_text = re.sub(r"#[^\n]*", "", f.read())       # strip comments

    routes = {}
    for host, block in re.findall(r'host\s+"([^"]+)"\s*\{(.*?)\}', config_text, re.DOTALL):
        proxy_passes = re.findall(r'proxy_pass\s+http://([^\s;]+)\s*;', block)
        policy_match = re.search(r'dist_policy\s+([\w-]+)', block)
        policy = policy_match.group(1) if policy_match else 'round-robin'
        if policy not in POLICIES:
            raise ValueError("host {}: unknown dist_policy {}".format(host, policy))
        headers = dict(re.findall(r'proxy_set_header\s+([\w-]+)\s+([^;]+?)\s*;', block))
        if not proxy_passes:
            raise ValueError("host {} has no proxy_pass".format(host))
        target = proxy_passes[0] if len(proxy_passes) == 1 else proxy_passes
        routes[host] = (target, policy, headers)

    for key, value in routes.items():
        print("[Proxy] {} -> {} ({})".format(key, value[0], value[1]))
    return routes


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog='Proxy', description='Reverse proxy',
                                     epilog='Proxy daemon')
    parser.add_argument('--server-ip', default='0.0.0.0')
    parser.add_argument('--server-port', type=int, default=PROXY_PORT)
    parser.add_argument('--config', default='config/proxy.conf')
    args = parser.parse_args()

    create_proxy(args.server_ip, args.server_port, parse_virtual_hosts(args.config))
