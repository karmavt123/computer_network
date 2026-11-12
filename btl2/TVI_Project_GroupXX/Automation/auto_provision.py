#!/usr/bin/env python3
"""
auto_provision.py - TVI network automation (Step 9)

 1. Inventory & credentials : hosts come from inventory.yaml; credentials from environment
                              variables (TVI_SSH_USER / TVI_SSH_PASS / TVI_ENABLE_PASS) or an
                              interactive getpass prompt.  Nothing secret is stored in any file.
 2. Baseline + VLAN push    : AAA / NTP / syslog / banner / SSH hardening on every device and the
                              corporate VLANs (10/20/30/99 + extended set) on every switch, all
                              driven by payload.yaml.  Extra VLANs can be injected at run time:
                              --vlan 110:LAB_DEMO
 3. State capture + backup  : `show running-config` and `show ip interface brief` are captured
                              BEFORE and AFTER the change into backups/<hostname>_<timestamp>_backup.cfg
                              (the stage is written in the file header); every success and every SSH
                              error is written to logs/automation.log.

Examples
    python3 auto_provision.py                       # all devices, push + backups
    python3 auto_provision.py --dry-run             # show what would be sent, change nothing
    python3 auto_provision.py --backup-only         # compliance snapshot only
    python3 auto_provision.py --roles core --vlan 110:LAB_DEMO --save
    python3 auto_provision.py --devices HCMC-CORE-SW01,HANOI-RND-SW01
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from getpass import getpass
from pathlib import Path

import yaml

try:  # netmiko is imported lazily so that --help / unit tests work without it
    from netmiko import ConnectHandler
    from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException
except ImportError:  # pragma: no cover
    ConnectHandler = None

    class NetmikoTimeoutException(Exception):  # type: ignore
        pass

    class NetmikoAuthenticationException(Exception):  # type: ignore
        pass

BASE = Path(__file__).resolve().parent
LOG = logging.getLogger("tvi.automation")


# --------------------------------------------------------------------------------------
def setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    LOG.setLevel(logging.INFO)
    for h in list(LOG.handlers):
        h.close()
        LOG.removeHandler(h)
    fh = logging.FileHandler(log_dir / "automation.log")
    fh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    LOG.addHandler(fh)


def load_yaml(path: Path):
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_inventory(path: Path):
    data = load_yaml(path)
    defaults = data.get("defaults", {})
    devices = []
    for d in data["devices"]:
        merged = {**defaults, **d}
        devices.append(merged)
    return devices


def select(devices, names=None, roles=None):
    out = devices
    if names:
        wanted = {n.strip() for n in names.split(",")}
        out = [d for d in out if d["hostname"] in wanted]
    if roles:
        r = {x.strip() for x in roles.split(",")}
        out = [d for d in out if d.get("role") in r]
    return out


def get_credentials():
    user = os.environ.get("TVI_SSH_USER") or input("Enter SSH Username: ")
    password = os.environ.get("TVI_SSH_PASS") or getpass("Enter SSH Password: ")
    secret = os.environ.get("TVI_ENABLE_PASS", password)
    return user, password, secret


def build_vlans(payload, extra):
    vlans = list(payload["vlans"].get("standard", [])) + list(payload["vlans"].get("extended", []))
    for item in extra or []:
        vid, _, name = item.partition(":")
        vlans.append({"id": int(vid), "name": name or f"VLAN{vid}"})
    seen, out = set(), []
    for v in vlans:
        if v["id"] not in seen:
            seen.add(v["id"])
            out.append(v)
    return out


def commands_for(dev, payload, extra_vlans=None):
    """Return the list of configuration commands for one device (pure function - easy to test)."""
    cmds = list(payload["baseline"].get("aaa", [])) + list(payload["baseline"]["common"])
    if dev.get("role") in payload.get("vlan_roles", []):
        for v in build_vlans(payload, extra_vlans):
            cmds += [f"vlan {v['id']}", f" name {v['name']}"]
    return cmds


def capture(conn, hostname, stage, backup_dir: Path, commands):
    """Run the capture commands and store them in a timestamped backup file."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = backup_dir / f"{hostname}_{ts}_backup.cfg"
    n = 1
    while path.exists():                       # two captures in the same second
        path = backup_dir / f"{hostname}_{ts}_{n}_backup.cfg"
        n += 1
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"! host: {hostname}\n! stage: {stage}\n! captured: {datetime.now().isoformat(timespec='seconds')}\n")
        for c in commands:
            fh.write(f"\n! ===== {c} =====\n")
            fh.write(conn.send_command(c) + "\n")
    return path


def process_device(dev, creds, payload, opts, connect=None):
    connect = connect or ConnectHandler
    hostname = dev["hostname"]
    user, password, secret = creds
    params = dict(device_type=dev["device_type"], host=dev["ip"], port=dev.get("port", 22),
                  username=user, password=password, secret=secret, timeout=dev.get("timeout", 15),
                  conn_timeout=dev.get("timeout", 15))
    cmds = commands_for(dev, payload, opts.vlan)
    if opts.dry_run:
        print(f"\n[dry-run] {hostname} ({dev['ip']}) would receive {len(cmds)} commands:")
        print("\n".join("   " + c for c in cmds))
        LOG.info("DRY-RUN %s: %d commands", hostname, len(cmds))
        return "dry-run"
    print(f"[*] Connecting to {hostname} ({dev['ip']}) ...")
    try:
        conn = connect(**params)
        conn.enable()
        before = capture(conn, hostname, "before", opts.backup_dir, payload["capture"])
        print(f"    [+] {hostname}: state captured -> {before.name}")
        if not opts.backup_only:
            output = conn.send_config_set(cmds)
            LOG.info("Successfully configured %s:\n%s", hostname, output)
            if opts.save:
                conn.save_config()
                LOG.info("Saved running-config on %s", hostname)
            after = capture(conn, hostname, "after", opts.backup_dir, payload["capture"])
            print(f"    [+] {hostname}: {len(cmds)} commands applied, state captured -> {after.name}")
            if dev.get("role") in payload.get("vlan_roles", []):
                vl = conn.send_command("show vlan brief")
                missing = [str(v["id"]) for v in build_vlans(payload, opts.vlan)
                           if not re.search(rf"^\s*{v['id']}\s", vl, re.M)]
                if missing:
                    LOG.warning("%s: VLAN(s) not visible after push: %s", hostname, ",".join(missing))
                    print(f"    [!] {hostname}: VLANs not visible: {','.join(missing)}")
        else:
            LOG.info("Backup-only run completed on %s -> %s", hostname, before.name)
        conn.disconnect()
        return "ok"
    except NetmikoAuthenticationException as exc:
        LOG.error("Authentication failed on %s (%s): %s", hostname, dev["ip"], exc)
        print(f"    [x] {hostname}: authentication failed")
    except NetmikoTimeoutException as exc:
        LOG.error("Connection timeout on %s (%s): %s", hostname, dev["ip"], exc)
        print(f"    [x] {hostname}: connection timed out")
    except Exception as exc:  # noqa: BLE001 - keep going with the remaining devices
        LOG.error("Unexpected error on %s (%s): %s: %s", hostname, dev["ip"], type(exc).__name__, exc)
        print(f"    [x] {hostname}: {type(exc).__name__}: {exc}")
    return "failed"


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="TVI baseline / VLAN provisioning and config backup")
    ap.add_argument("--inventory", type=Path, default=BASE / "inventory.yaml")
    ap.add_argument("--payload", type=Path, default=BASE / "payload.yaml")
    ap.add_argument("--backup-dir", type=Path, default=BASE / "backups")
    ap.add_argument("--log-dir", type=Path, default=BASE / "logs")
    ap.add_argument("--devices", help="comma separated hostnames")
    ap.add_argument("--roles", help="comma separated roles: core,access,edge")
    ap.add_argument("--vlan", action="append", metavar="ID:NAME", help="extra VLAN to create (repeatable)")
    ap.add_argument("--dry-run", action="store_true", help="print the commands, touch nothing")
    ap.add_argument("--backup-only", action="store_true", help="only capture state / backups")
    ap.add_argument("--save", action="store_true", help="write memory after a successful push")
    ap.add_argument("--workers", type=int, default=1, help="parallel SSH sessions (default 1)")
    return ap.parse_args(argv)


def run_automation(argv=None, connect=None, creds=None):
    opts = parse_args(argv)
    setup_logging(opts.log_dir)
    opts.backup_dir.mkdir(parents=True, exist_ok=True)
    payload = load_yaml(opts.payload)
    devices = select(load_inventory(opts.inventory), opts.devices, opts.roles)
    if not devices:
        print("No device matches the selection.")
        return {}
    creds = creds or ((("dry", "dry", "dry")) if opts.dry_run else get_credentials())
    LOG.info("Run started: %d device(s), dry_run=%s backup_only=%s", len(devices), opts.dry_run, opts.backup_only)
    results = {}
    if opts.workers > 1 and not opts.dry_run:
        with ThreadPoolExecutor(max_workers=opts.workers) as ex:
            futs = {d["hostname"]: ex.submit(process_device, d, creds, payload, opts, connect) for d in devices}
            results = {h: f.result() for h, f in futs.items()}
    else:
        for d in devices:
            results[d["hostname"]] = process_device(d, creds, payload, opts, connect)
    ok = sum(v in ("ok", "dry-run") for v in results.values())
    print(f"\nSummary: {ok}/{len(results)} device(s) OK. Details: {opts.log_dir / 'automation.log'}")
    LOG.info("Run finished: %s", results)
    return results


if __name__ == "__main__":
    res = run_automation()
    sys.exit(0 if all(v in ("ok", "dry-run") for v in res.values()) else 1)
