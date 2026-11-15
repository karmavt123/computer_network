#!/usr/bin/env python3
"""
collect_evidence.py - run the verification commands of the test plan on the lab devices and save every
output as a text file that the LaTeX report includes verbatim.

Why: the report is graded on *evidence* (rubric criterion 6: "CLI outputs of show ip route, traceroute,
ping ... failover scenarios documented and proven").  Sixty hand-made screenshots are slow to take and
easy to mix up; this script produces  Report/evidence/<id>.txt  for every item of  Lab/evidence_plan.yaml
in one run, with the device prompt, the command and a timestamp, plus  Report/evidence/index.json  with a
simple PASS / CHECK verdict (regular expressions from the plan).

    cd Lab
    python3 collect_evidence.py --dry-run                 # list what would be collected, no connection
    python3 collect_evidence.py                           # everything (credentials: env vars or getpass)
    python3 collect_evidence.py --only 's5_*,s6_*'        # only some items (shell-style patterns)
    python3 collect_evidence.py --only 's10_*' --tag after_core1_down   # failover snapshots under a new name

Credentials and the device list are shared with Automation/auto_provision.py (inventory.yaml, TVI_SSH_USER /
TVI_SSH_PASS / TVI_ENABLE_PASS or an interactive prompt) - nothing secret is stored here.
The script only runs read-only commands (show / ping / traceroute); it refuses anything else.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "Automation"))
import auto_provision as ap            # noqa: E402  (load_inventory, get_credentials, netmiko exceptions)

READ_ONLY = re.compile(r"^(show|ping|traceroute|terminal length)\b")


def load_plan(path: Path):
    plan = yaml.safe_load(path.read_text())["evidence"]
    for it in plan:
        for c in it["commands"]:
            if not READ_ONLY.match(c):
                raise ValueError(f"{it['id']}: '{c}' is not a read-only command")
    return plan


def select(plan, patterns):
    if not patterns:
        return plan
    pats = [p.strip() for p in patterns.split(",") if p.strip()]
    return [it for it in plan if any(fnmatch.fnmatch(it["id"], p) for p in pats)]


def verdict(text: str, item) -> str:
    """PASS if every 'expect' regex matches and no 'reject' regex does; CHECK otherwise (a human decides)."""
    exp, rej = item.get("expect", []), item.get("reject", [])
    if not exp and not rej:
        return "CAPTURED"
    ok = all(re.search(p, text, re.M) for p in exp) and not any(re.search(p, text, re.M) for p in rej)
    return "PASS" if ok else "CHECK"


def collect_device(dev, items, creds, outdir: Path, tag: str, connect=None):
    connect = connect or ap.ConnectHandler
    user, password, secret = creds
    results = []
    conn = connect(device_type=dev["device_type"], host=dev["ip"], port=dev.get("port", 22), username=user,
                   password=password, secret=secret, timeout=dev.get("timeout", 15),
                   conn_timeout=dev.get("timeout", 15))
    try:
        conn.enable()
        for it in items:
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            parts = [f"! evidence {it['id']} | {dev['hostname']} | {stamp} | {it['title']}"]
            for cmd in it["commands"]:
                out = conn.send_command(cmd, read_timeout=it.get("timeout", 60))
                parts.append(f"{dev['hostname']}#{cmd}\n{out.rstrip()}")
            text = "\n".join(parts) + "\n"
            name = it["id"] + (f"_{tag}" if tag else "") + ".txt"
            (outdir / name).write_text(text)
            results.append(dict(id=it["id"], file=name, device=dev["hostname"], title=it["title"], time=stamp,
                                step=it.get("step"), verdict=verdict(text, it)))
    finally:
        conn.disconnect()
    return results


def run(argv=None, connect=None, creds=None):
    p = argparse.ArgumentParser(description="Collect CLI evidence for the report")
    p.add_argument("--plan", default=str(HERE / "evidence_plan.yaml"))
    p.add_argument("--inventory", default=str(ROOT / "Automation" / "inventory.yaml"))
    p.add_argument("--out", default=str(ROOT / "Report" / "evidence"))
    p.add_argument("--only", help="comma separated shell patterns of evidence ids, e.g. 's3_*,s4_1*'")
    p.add_argument("--tag", default="", help="suffix for the output files (before/after snapshots)")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    plan = select(load_plan(Path(a.plan)), a.only)
    inv = {d["hostname"]: d for d in ap.load_inventory(Path(a.inventory))}
    manual = [it for it in plan if it.get("manual")]
    plan = [it for it in plan if not it.get("manual")]
    unknown = sorted({it["device"] for it in plan} - set(inv))
    if unknown:
        raise SystemExit(f"devices in the plan but not in the inventory: {unknown}")
    by_dev = {}
    for it in plan:
        by_dev.setdefault(it["device"], []).append(it)
    if a.dry_run:
        for dev, items in by_dev.items():
            print(f"{dev} ({inv[dev]['ip']})")
            for it in items:
                print(f"   {it['id']:<10} {it['title']}")
                for c in it["commands"]:
                    print(f"              # {c}")
        print(f"\n{len(plan)} evidence items on {len(by_dev)} devices")
        _print_manual(manual, Path(a.out))
        return 0
    outdir = Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    creds = creds or ap.get_credentials()
    index, failed = [], []
    for dev, items in by_dev.items():
        print(f"[*] {dev}: {len(items)} item(s)")
        try:
            index += collect_device(inv[dev], items, creds, outdir, a.tag, connect)
        except (ap.NetmikoTimeoutException, ap.NetmikoAuthenticationException, OSError) as e:
            print(f"    [!] {dev}: {type(e).__name__}: {e}")
            failed.append(dev)
    idx_file = outdir / ("index" + (f"_{a.tag}" if a.tag else "") + ".json")
    idx_file.write_text(json.dumps(index, indent=1))
    for r in index:
        print(f"    {r['verdict']:<8} {r['file']:<28} {r['title']}")
    n_check = sum(r["verdict"] == "CHECK" for r in index)
    print(f"\n{len(index)} file(s) written to {outdir}; {n_check} need a look (CHECK); "
          f"{len(failed)} device(s) unreachable: {failed}")
    _print_manual(manual, outdir)
    return 1 if failed else 0


def _print_manual(items, outdir: Path):
    """Devices TVI does not manage (ISP routers) and VPCS hosts have no SSH: capture those by hand."""
    for it in items:
        print(f"    MANUAL   {it['id']}.txt  <- console of {it['device']}: {'; '.join(it['commands'])}"
              f"   (paste into {outdir / (it['id'] + '.txt')})")


if __name__ == "__main__":
    sys.exit(run())
