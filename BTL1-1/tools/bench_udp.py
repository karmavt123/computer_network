"""Measure the UDP file transfer under simulated packet loss.

Two endpoints run in one process on the loopback interface. The same loss
rate is applied to DATA (sender side) and to ACK datagrams (receiver
side). Results are printed as a table and saved as JSON (with the
congestion-window trace) for the report.

Usage::

    python tools/bench_udp.py --size 2 --loss 0 0.05 0.1 0.2 --runs 3
"""

import argparse
import hashlib
import json
import os
import statistics
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chat.protocol import KeyRing  # noqa: E402
from chat.udp_files import ReliableUdpFiles  # noqa: E402


def one_run(data, loss, seed):
    """Send ``data`` once; return the sender's statistics."""
    with tempfile.TemporaryDirectory() as folder:
        keys = KeyRing("bench")
        sender = ReliableUdpFiles("s", "127.0.0.1", 0, keys, Path(folder) / "s",
                                  loss_rate=loss, seed=seed)
        receiver = ReliableUdpFiles("r", "127.0.0.1", 0, keys, Path(folder) / "r",
                                    loss_rate=loss, seed=seed + 1000)
        try:
            stats = sender.send_bytes({"host": "127.0.0.1", "udp_port": receiver.port},
                                      data, "bench.bin")
            received = (Path(folder) / "r" / "bench.bin").read_bytes()
            stats["intact"] = hashlib.sha256(received).digest() == hashlib.sha256(data).digest()
        finally:
            sender.close()
            receiver.close()
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--size", type=float, default=2.0, help="file size in MiB")
    parser.add_argument("--loss", type=float, nargs="+", default=[0, 0.05, 0.1, 0.2])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--out", default="bench_udp.json")
    args = parser.parse_args()

    data = os.urandom(int(args.size * 1024 * 1024))
    results = []
    print("loss  runs  time(s)  KiB/s   datagrams  retx  fast  timeouts  intact")
    for loss in args.loss:
        runs = [one_run(data, loss, seed=11 + i) for i in range(args.runs)]
        row = {
            "loss": loss, "runs": args.runs, "size": len(data), "chunks": runs[0]["chunks"],
            "duration": statistics.median(r["duration"] for r in runs),
            "throughput_kib_s": statistics.median(r["throughput_kib_s"] for r in runs),
            "datagrams_sent": statistics.median(r["datagrams_sent"] for r in runs),
            "retransmissions": statistics.median(r["retransmissions"] for r in runs),
            "fast_retransmits": statistics.median(r["fast_retransmits"] for r in runs),
            "timeouts": statistics.median(r["timeouts"] for r in runs),
            "intact": all(r["intact"] for r in runs),
            "cwnd_trace": runs[0]["cwnd_trace"],
        }
        results.append(row)
        print("{loss:<5} {runs:<5} {duration:<8.2f} {throughput_kib_s:<7.0f} "
              "{datagrams_sent:<10.0f} {retransmissions:<5.0f} {fast_retransmits:<5.0f} "
              "{timeouts:<9.0f} {intact}".format(**row))
    Path(args.out).write_text(json.dumps(results))
    print("saved", args.out)


if __name__ == "__main__":
    main()
