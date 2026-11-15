"""Start the central peer discovery tracker."""

import argparse
from apps.tracker_app import create_tracker


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Start the HCMUT chat peer tracker")
    parser.add_argument("--server-ip", default="127.0.0.1")
    parser.add_argument("--server-port", type=int, default=8001)
    args = parser.parse_args()
    create_tracker(args.server_ip, args.server_port)
