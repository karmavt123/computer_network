#
# Copyright (C) 2026 pdnguyen of HCMC University of Technology VNU-HCM.
# All rights reserved.
# This file is part of the CO3093/CO3094 course,
# and is released under the LICENSE. Please see the LICENSE
# file that should have been included as part of this package.
#
# StreapRous release
#
# The authors hereby grant to Licensee personal permission to use
# and modify the Licensed Source Code for the sole purpose of studying
# while attending the course
#

"""
start_sampleapp
~~~~~~~~~~~~~~~~~

Start one peer of the chat application: the web UI (StreapRous), the P2P
chat listener (TCP), the file endpoint (UDP) and the tracker heartbeat.

Example::

    python start_sampleapp.py --server-port 8000 --peer-id peer1 \\
        --peer-port 9101 --file-port 9102 --tracker-url http://127.0.0.1:8001
"""

import argparse
import signal

from apps.chat_app import create_chat_app
from daemon.backend import MODES

def _stop_on_sigterm(signum, frame):
    """Treat ``kill`` (SIGTERM) like Ctrl+C so the process leaves gracefully."""
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _stop_on_sigterm)
    parser = argparse.ArgumentParser(description="Start one chat peer")
    parser.add_argument('--server-ip', default='127.0.0.1', help='HTTP bind IP of the web UI')
    parser.add_argument('--server-port', type=int, default=8000, help='HTTP port of the web UI')
    parser.add_argument('--peer-id', default='peer1', help='unique name registered at the tracker')
    parser.add_argument('--peer-host', default='127.0.0.1',
                        help='address other peers use to reach this one '
                             '(the LAN IP when several machines are used)')
    parser.add_argument('--peer-port', type=int, default=9101, help='TCP port for P2P chat')
    parser.add_argument('--file-port', type=int, default=9102, help='UDP port for file transfer')
    parser.add_argument('--tracker-url', default='http://127.0.0.1:8001', help='tracker base URL')
    parser.add_argument('--shared-key', default='hcmut-demo-change-this',
                        help='shared passphrase for AES-GCM; same value on every peer')
    parser.add_argument('--download-dir', default=None,
                        help='folder for received files (default downloads/<peer-id>)')
    parser.add_argument('--loss', type=float, default=0.0,
                        help='simulate UDP packet loss, e.g. 0.1 drops 10%% of datagrams')
    parser.add_argument('--mode', choices=MODES[:2], default='coroutine',
                        help='HTTP server mode: coroutine (default) or threading')
    args = parser.parse_args()
    if not 0.0 <= args.loss < 1.0:
        parser.error("--loss must be in [0, 1)")
    create_chat_app(args.server_ip, args.server_port, args.peer_id, args.peer_host,
                    args.peer_port, args.file_port, args.tracker_url, args.shared_key,
                    args.download_dir or "downloads/" + args.peer_id,
                    loss_rate=args.loss, mode=args.mode)
