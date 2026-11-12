#!/usr/bin/env python3
"""
push_configs.py - EXPERIMENTAL helper: paste the generated startup-configs into the EVE-NG consoles.

EVE-NG exposes every started node as a telnet port on the EVE-NG host (hover the node / "Console" link shows it,
e.g. telnet://192.168.56.101:32769).  Put your ports in console_ports.yaml (see console_ports.example.yaml) and run

    python3 push_configs.py --eve 192.168.56.101 --dry-run
    python3 push_configs.py --eve 192.168.56.101 --only HCMC-CORE-SW01

It talks raw TCP (Python 3.13 no longer ships telnetlib), strips telnet negotiation bytes, sends the file line by
line with a small delay and finally generates the RSA key and saves.  It has NOT been tested against a real EVE-NG -
if a paste looks corrupted, use the browser console and paste in smaller chunks instead.
"""
import argparse
import socket
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
CFG_ROOT = HERE.parent / "Configurations"


def clean(data: bytes) -> str:
    out, i = bytearray(), 0
    while i < len(data):
        if data[i] == 255 and i + 2 < len(data):   # IAC <cmd> <opt>
            i += 3
            continue
        out.append(data[i])
        i += 1
    return out.decode("ascii", "ignore")


def send_lines(sock, lines, delay):
    for ln in lines:
        sock.sendall(ln.encode() + b"\r\n")
        time.sleep(delay)
        sock.settimeout(0.01)
        try:
            sock.recv(65535)
        except socket.timeout:
            pass


def push(host, port, cfg_file, delay, keygen):
    lines = [l.rstrip("\n") for l in Path(cfg_file).read_text().splitlines() if l.strip() != ""]
    with socket.create_connection((host, port), timeout=10) as s:
        send_lines(s, ["", "", "enable", "configure terminal"], 0.5)
        send_lines(s, [l for l in lines if l != "end"], delay)
        send_lines(s, ["end"], 1)
        if keygen:
            send_lines(s, ["crypto key generate rsa general-keys modulus 2048"], 15)
        send_lines(s, ["copy running-config startup-config", ""], 5)
        s.settimeout(2)
        try:
            tail = clean(s.recv(65535))
        except socket.timeout:
            tail = ""
    return tail[-200:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eve", required=True, help="EVE-NG host/IP")
    ap.add_argument("--ports", default=str(HERE / "console_ports.yaml"))
    ap.add_argument("--only", help="comma separated node names")
    ap.add_argument("--delay", type=float, default=0.12)
    ap.add_argument("--no-keygen", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    ports = yaml.safe_load(open(a.ports))
    cfgs = {p.stem: p for p in CFG_ROOT.rglob("*.cfg")}
    only = set(a.only.split(",")) if a.only else None
    for node, port in ports.items():
        if only and node not in only:
            continue
        if node not in cfgs:
            print(f"[skip] {node}: no .cfg file")
            continue
        n = len(cfgs[node].read_text().splitlines())
        if a.dry_run:
            print(f"[dry-run] {node}: {a.eve}:{port} <- {cfgs[node].name} ({n} lines)")
            continue
        print(f"[*] {node}: {a.eve}:{port} ...", flush=True)
        print("    tail:", push(a.eve, int(port), cfgs[node], a.delay, not a.no_keygen).strip().replace("\n", " | "))


if __name__ == "__main__":
    main()
