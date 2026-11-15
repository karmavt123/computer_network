"""Start the tracker: peer registry, user login and relay fallback.

Example::

    python start_tracker.py --server-ip 127.0.0.1 --server-port 8001
"""

import argparse
import signal

from apps.tracker_app import DEFAULT_USERS, create_tracker
from daemon.backend import MODES

def _stop_on_sigterm(signum, frame):
    """Treat ``kill`` (SIGTERM) like Ctrl+C so the process leaves gracefully."""
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _stop_on_sigterm)
    parser = argparse.ArgumentParser(description="Start the chat tracker")
    parser.add_argument("--server-ip", default="127.0.0.1", help="bind address")
    parser.add_argument("--server-port", type=int, default=8001, help="HTTP port")
    parser.add_argument("--users", default=str(DEFAULT_USERS),
                        help="account file (default db/users.json)")
    parser.add_argument("--mode", choices=MODES[:2], default="coroutine",
                        help="coroutine (asyncio, default) or threading")
    args = parser.parse_args()
    create_tracker(args.server_ip, args.server_port, args.users, args.mode)
