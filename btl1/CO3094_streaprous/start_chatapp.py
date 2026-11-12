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
start_chatapp
~~~~~~~~~~~~~~~~~

Entry point of the hybrid chat application. One process per role::

    python start_chatapp.py --role tracker --server-port 9000
    python start_chatapp.py --role peer --server-port 9101 --tracker 127.0.0.1:9000
"""

import argparse
import socket


def guess_lan_ip():
    """Best-effort address of this machine on the local network."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))   # no packet is sent
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog='ChatApp', description='Hybrid chat: tracker or peer daemon')
    parser.add_argument('--role', choices=['tracker', 'peer'], required=True)
    parser.add_argument('--server-ip', default='0.0.0.0', help='bind address (default 0.0.0.0)')
    parser.add_argument('--server-port', type=int, default=None,
                        help='default 9000 for the tracker, 9101 for a peer')
    parser.add_argument('--tracker', default='127.0.0.1:9000', help='peer only: tracker host:port')
    parser.add_argument('--advertise-ip', default='127.0.0.1',
                        help="peer only: address other peers use to reach this daemon "
                             "('auto' picks the LAN address)")
    parser.add_argument('--mode', choices=['coroutine', 'threading', 'raw'], default='coroutine',
                        help='server transport (default: coroutine = asyncio, non-blocking)')
    args = parser.parse_args()

    if args.role == 'tracker':
        from apps import create_tracker
        create_tracker(args.server_ip, args.server_port or 9000, args.mode)
    else:
        from apps import create_peer
        advertise = guess_lan_ip() if args.advertise_ip == 'auto' else args.advertise_ip
        create_peer(args.server_ip, args.server_port or 9101, args.tracker, advertise, args.mode)
