#
# Copyright (C) 2026 pdnguyen of HCMC University of Technology VNU-HCM.
# All rights reserved.
# This file is part of the CO3093/CO3094 course,
# and is released under the LICENSE. Please see the LICENSE
# file that should have been included as part of this package.
#
# AsynapRous release
#
# The authors hereby grant to Licensee personal permission to use
# and modify the Licensed Source Code for the sole purpose of studying
# while attending the course
#


"""
start_sampleapp
~~~~~~~~~~~~~~~~~

This module provides a sample RESTful web application using the AsynapRous framework.

It defines basic route handlers and launches a TCP-based backend server to serve
HTTP requests. The application includes a login endpoint and a greeting endpoint,
and can be configured via command-line arguments.
"""

import argparse

from apps.chat_app import create_chat_app

if __name__ == "__main__":
    # Parse command-line arguments to configure server IP and port
    parser = argparse.ArgumentParser(description="Start one HCMUT collaboration peer")
    parser.add_argument('--server-ip', default='127.0.0.1', help='HTTP bind IP')
    parser.add_argument('--server-port', type=int, default=8000, help='HTTP port')
    parser.add_argument('--peer-id', default='peer1', help='Unique name registered at the tracker')
    parser.add_argument('--peer-host', default='127.0.0.1', help='Reachable address advertised to other peers')
    parser.add_argument('--peer-port', type=int, default=9101, help='TCP P2P messaging port')
    parser.add_argument('--file-port', type=int, default=9102, help='UDP file-transfer port')
    parser.add_argument('--tracker-url', default='http://127.0.0.1:8001', help='Tracker HTTP base URL')
    parser.add_argument('--shared-key', default='hcmut-demo-change-this', help='Shared AES-GCM key phrase; use the same value on every peer')
    parser.add_argument('--download-dir', default='downloads', help='Directory for received files')
    args = parser.parse_args()
    create_chat_app(args.server_ip, args.server_port, args.peer_id, args.peer_host,
                    args.peer_port, args.file_port, args.tracker_url,
                    args.shared_key, args.download_dir)
