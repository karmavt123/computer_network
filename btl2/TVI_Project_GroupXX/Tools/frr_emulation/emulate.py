#!/usr/bin/env python3
"""
emulate.py - run the Layer-3 control plane of the TVI design with real routing daemons (FRRouting) in Linux
network namespaces, then inject failures and measure what happens to end-to-end traffic.

    sudo python3 Tools/frr_emulation/emulate.py                 # build, run every scenario, write results/
    sudo python3 Tools/frr_emulation/emulate.py --only base,S2  # selected scenarios
    sudo python3 Tools/frr_emulation/emulate.py --no-edge-link  # reproduce the routing loop the edge-to-edge link fixes
    sudo python3 Tools/frr_emulation/emulate.py --keep          # leave the lab running (inspect with: vtysh -N <ns>)
    sudo python3 Tools/frr_emulation/emulate.py --cleanup       # remove every namespace / daemon of the lab

Requirements: Linux, root, iproute2, iptables, ping, traceroute, FRR >= 8 (apt-get install frr iproute2
iputils-ping traceroute iptables).  Kernel features used: netns, veth, bridge, vxlan, iptables NAT.

Scope - read ios2frr.py first.  This is NOT the graded EVE-NG lab and its outputs must not be presented as
IOSv outputs.  It answers one question the static checks cannot: "does the routing logic of the generated
configs converge and fail over the way the design claims?".
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import tvi_model as m                                                    # noqa: E402
from ios2frr import IosConfig, linux_ifname, nat_rules, to_frr          # noqa: E402

PFX = "tvi-"
RUN = Path("/tmp/tvi-emu")
FRR_BIN = "/usr/lib/frr"
L2NS = PFX + "l2"
L3_ROLES = {"core", "edge", "dcsrv", "isp", "internet"}
T0 = time.time()


def log(*a):
    print(f"[{time.time() - T0:7.1f}s]", *a, flush=True)


def sh(cmd, ns=None, check=True, timeout=60):
    if isinstance(cmd, str):
        cmd = cmd.split()
    if ns:
        cmd = ["ip", "netns", "exec", ns] + cmd
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}\n{r.stdout}{r.stderr}")
    return r.stdout


def ns_of(dev):
    return PFX + dev.lower()


def vty(dev, command):
    return sh(["vtysh", "-N", ns_of(dev), "-c", command], ns=ns_of(dev), check=False)


# ----------------------------------------------------------------------------------------------------------
class Lab:
    def __init__(self, edge_link=True, md5=True, preempt_scale=0.1, carrier_loss=False):
        self.edge_link, self.md5, self.preempt_scale = edge_link, md5, preempt_scale
        self.carrier_loss = carrier_loss
        self._lk = 0
        self.cfg = {}            # router hostname -> IosConfig
        self.hosts = {}          # host name -> dict(ns, ip, gw)
        self.segments = {}       # bridge -> [(ns, ifname)]
        self.down_if = set()     # (dev, linux ifname) administratively failed by a scenario
        self.down_node = set()
        self.ip_owner = {}       # ip -> "HOST if"
        self.hsrp_groups = []
        self.hsrp_events = []
        self.hsrp_active = {}    # (site, group) -> dev
        self.track_state = {}    # (dev, prefix) -> [effective state, time the raw state started to differ]
        self._pn = 0
        self._stop = threading.Event()
        for f in sorted((ROOT / "Configurations").rglob("*.cfg")):
            if m.DEVICES[f.stem]["role"] in L3_ROLES:
                self.cfg[f.stem] = IosConfig(f.read_text())

    # ---------------------------------------------------------------- build
    def cleanup(self):
        sh(["pkill", "-f", f"{FRR_BIN}/.* -N {PFX}"], check=False)
        time.sleep(0.5)
        for ln in sh("ip netns list", check=False).splitlines():
            n = ln.split()[0]
            if n.startswith(PFX):
                sh(["ip", "netns", "del", n], check=False)
        for base in ("/etc/frr", "/var/run/frr"):
            for p in Path(base).glob(PFX + "*"):
                shutil.rmtree(p, ignore_errors=True)

    def _mkns(self, ns, router):
        sh(["ip", "netns", "add", ns])
        sh("ip link set lo up", ns=ns)
        for k, v in (("net.ipv4.conf.all.rp_filter", 0), ("net.ipv4.conf.default.rp_filter", 0),
                     ("net.ipv4.ip_forward", 1 if router else 0),
                     ("net.ipv4.conf.all.send_redirects", 0), ("net.ipv4.conf.default.send_redirects", 0),
                     ("net.ipv4.conf.all.accept_redirects", 0), ("net.ipv4.conf.default.accept_redirects", 0)):
            sh(["sysctl", "-qw", f"{k}={v}"], ns=ns)

    def _attach(self, ns, ifname, bridge):
        if bridge not in self.segments:
            sh(["ip", "-n", L2NS, "link", "add", bridge, "type", "bridge", "stp_state", "0", "forward_delay", "0"])
            sh(["ip", "-n", L2NS, "link", "set", bridge, "up"])
            self.segments[bridge] = []
        self._pn += 1
        port = f"p{self._pn}"
        sh(["ip", "link", "add", ifname, "netns", ns, "type", "veth", "peer", "name", port, "netns", L2NS])
        sh(["ip", "-n", L2NS, "link", "set", port, "master", bridge, "up"])
        sh(["ip", "-n", ns, "link", "set", ifname, "up"])
        self.segments[bridge].append((ns, ifname))

    def build(self):
        self.cleanup()
        RUN.mkdir(exist_ok=True)
        os.chmod(RUN, 0o777)
        self._mkns(L2NS, False)
        for dev in self.cfg:
            self._mkns(ns_of(dev), True)
        # --- point-to-point routed / WAN links (from the model's cabling table)
        ee = m.EDGE_EDGE_P2P
        for (da, ia, db, ib, kind, _note) in m.LINKS:
            if kind not in ("routed", "wan") or da not in self.cfg or db not in self.cfg:
                continue
            if not self.edge_link and {da, db} == {ee["a"], ee["b"]}:
                log("!! edge-to-edge link deliberately NOT built (--no-edge-link)")
                continue
            if self.carrier_loss:        # like a physical cable: both ends see the link go down
                sh(["ip", "link", "add", linux_ifname(ia), "netns", ns_of(da), "type", "veth",
                    "peer", "name", linux_ifname(ib), "netns", ns_of(db)])
            else:                         # like EVE-NG/GNS3: a bridge in between, the far end stays up/up
                self._lk += 1
                for dev, ifn in ((da, ia), (db, ib)):
                    self._attach(ns_of(dev), linux_ifname(ifn), f"lk{self._lk}")
        # --- L2 segments: one bridge per (site, VLAN); access switches / trunks / Po1 are abstracted away
        for dev, c in self.cfg.items():
            site = m.DEVICES[dev]["site"]
            for ifn, d in c.interfaces.items():
                if not d["ip"] or d["shutdown"]:
                    continue
                vlan = int(ifn[4:]) if ifn.startswith("Vlan") else d["dot1q"]
                if vlan:
                    self._attach(ns_of(dev), linux_ifname(ifn), f"b{site.lower()}{vlan}")
        for (da, ia, db, ib, kind, note) in m.LINKS:      # routers that sit on an access port (DC-SRV)
            if kind == "access" and db in self.cfg:
                self._attach(ns_of(db), linux_ifname(ib), f"b{m.DEVICES[db]['site'].lower()}{int(note.split()[1])}")
        # --- addresses, tunnels
        for dev, c in self.cfg.items():
            ns = ns_of(dev)
            existing = sh("ip -o link show", ns=ns)
            for ifn, d in c.interfaces.items():
                if not d["ip"] or d["shutdown"] or not d["l3"]:
                    continue
                lin = linux_ifname(ifn)
                if d["tunnel"]:
                    src = c.interfaces[d["tunnel"]["source"]]["ip"].split("/")[0]
                    sh(["ip", "-n", ns, "link", "add", lin, "type", "vxlan", "id", ifn[6:], "local", src,
                        "remote", d["tunnel"]["destination"], "dstport", "4789"])
                elif lin != "lo" and f" {lin}@" not in existing and f" {lin}:" not in existing:
                    continue                                 # e.g. g0-3 when --no-edge-link
                sh(["ip", "-n", ns, "addr", "add", d["ip"], "dev", lin])
                sh(["ip", "-n", ns, "link", "set", lin, "up"])
                self.ip_owner[d["ip"].split("/")[0]] = f"{dev} {ifn}"
                for g, h in d["hsrp"].items():
                    self.ip_owner.setdefault(h["vip"], f"HSRP VIP {m.DEVICES[dev]['site']} group {g}")
            for r in nat_rules(c):
                sh(["iptables", "-t", "nat", "-A", "POSTROUTING"] + r, ns=ns)
        # --- end hosts (static addressing: first DHCP address of the VLAN, gateway = HSRP VIP)
        for h, d in m.HOSTS.items():
            ns, site, vlan = ns_of(h), d["site"], d["vlan"]
            self._mkns(ns, False)
            self._attach(ns, "eth0", f"b{site.lower()}{vlan}")
            ip = d["ip"] or m.host(site, vlan, m.DHCP_EXCLUDED + 1)
            plen = m.SITE_VLANS[site][vlan].split("/")[1]
            sh(["ip", "-n", ns, "addr", "add", f"{ip}/{plen}", "dev", "eth0"])
            sh(["ip", "-n", ns, "route", "add", "default", "via", m.host(site, vlan, 1)])
            self.hosts[h] = dict(ns=ns, ip=ip, gw=m.host(site, vlan, 1))
            self.ip_owner[ip] = h
        log(f"topology: {len(self.cfg)} routers, {len(self.hosts)} hosts, {len(self.segments)} L2 segments")

    def start_routing(self):
        for dev, c in self.cfg.items():
            ns = ns_of(dev)
            etc, run = Path("/etc/frr") / ns, Path("/var/run/frr") / ns
            etc.mkdir(parents=True, exist_ok=True)
            run.mkdir(parents=True, exist_ok=True)
            (etc / "frr.conf").write_text(to_frr(c, str(RUN / f"{dev}.log"), md5=self.md5))
            (etc / "vtysh.conf").write_text("service integrated-vtysh-config\n")
            shutil.chown(etc, "frr", "frr")
            shutil.chown(run, "frr", "frr")
            for f in etc.iterdir():
                shutil.chown(f, "frr", "frr")
            daemons = ["zebra", "staticd"] + (["ospfd"] if c.ospf is not None else []) + (["bgpd"] if c.bgp else [])
            for d in daemons:
                sh([f"{FRR_BIN}/{d}", "-d", "-N", ns], ns=ns)
        time.sleep(1.5)
        for dev in self.cfg:
            ns = ns_of(dev)
            out = subprocess.run(["ip", "netns", "exec", ns, "vtysh", "-N", ns, "-b"], capture_output=True, text=True)
            bad = [ln for ln in (out.stdout + out.stderr).splitlines() if "rror" in ln or "nknown" in ln or "%" in ln]
            if bad:
                log(f"!! {dev}: FRR rejected: {bad[:6]}")
        log("routing daemons started")

    # ---------------------------------------------------------------- HSRP rule engine
    def _init_hsrp(self):
        groups = {}
        for dev, c in self.cfg.items():
            site = m.DEVICES[dev]["site"]
            for ifn, d in c.interfaces.items():
                for g, h in d["hsrp"].items():
                    if not d["ip"] or not h["vip"]:
                        continue
                    seg = f"b{site.lower()}{int(ifn[4:]) if ifn.startswith('Vlan') else d['dot1q']}"
                    groups.setdefault((site, g), dict(vip=h["vip"], plen=d["ip"].split("/")[1], seg=seg, members=[]))[
                        "members"].append(dict(dev=dev, ifn=linux_ifname(ifn), ip=d["ip"].split("/")[0], base=h["priority"],
                                               preempt=h["preempt"], delay=h["delay"], hold=h["hold"],
                                               tracks=[(c.tracks[t], dec) for t, dec in h["tracks"]], better_since=None))
        self.hsrp_groups = groups

    def _alive(self, mem):
        return mem["dev"] not in self.down_node and (mem["dev"], mem["ifn"]) not in self.down_if

    def _hsrp_tick(self, first=False):
        tracks = {}
        for (site, g), grp in self.hsrp_groups.items():
            for mem in grp["members"]:
                prio = mem["base"]
                for pfx, dec in mem["tracks"]:
                    key = (mem["dev"], pfx)
                    if key not in tracks:
                        raw = mem["dev"] not in self.down_node and bool(sh(
                            ["ip", "-n", ns_of(mem["dev"]), "route", "show", "exact", pfx], check=False).strip())
                        # IOS polls the object every 'track timer ip route' seconds: a change counts only once it
                        # has lasted that long (also keeps a sub-second route replacement from flapping the gateway)
                        st = self.track_state.setdefault(key, [raw, None])
                        if raw == st[0]:
                            st[1] = None
                        else:
                            st[1] = st[1] or time.time()
                            if first or time.time() - st[1] >= self.cfg[mem["dev"]].track_poll:
                                st[0], st[1] = raw, None
                        tracks[key] = st[0]
                    if not tracks[key]:
                        prio -= dec
                mem["prio"] = prio
            alive = [x for x in grp["members"] if self._alive(x)]
            best = max(alive, key=lambda x: (x["prio"], x["ip"]), default=None)
            cur = next((x for x in grp["members"] if x["dev"] == self.hsrp_active.get((site, g))), None)
            new = cur
            now = time.time()
            if first or cur is None:
                new = best
            elif not self._alive(cur):
                cur.setdefault("dead_since", now)
                if now - cur["dead_since"] >= cur["hold"]:        # hold time = failure detection
                    new = best
            elif best is not cur and best["prio"] > cur["prio"] and best["preempt"]:
                best["better_since"] = best["better_since"] or now
                if now - best["better_since"] >= best["delay"] * self.preempt_scale:
                    new = best
            for x in grp["members"]:
                if x is not best:
                    x["better_since"] = None
                if self._alive(x):
                    x.pop("dead_since", None)
            if new is not cur and new is not None:
                if cur is not None:
                    sh(["ip", "-n", ns_of(cur["dev"]), "addr", "del", f"{grp['vip']}/{grp['plen']}", "dev", cur["ifn"]],
                       check=False)
                sh(["ip", "-n", ns_of(new["dev"]), "addr", "add", f"{grp['vip']}/{grp['plen']}", "dev", new["ifn"]],
                   check=False)
                # real HSRP keeps a virtual MAC; here the MAC changes, so make the neighbours re-ARP
                for ns, ifname in self.segments[grp["seg"]]:
                    sh(["ip", "-n", ns, "neigh", "del", grp["vip"], "dev", ifname], check=False)
                self.hsrp_active[(site, g)] = new["dev"]
                best["better_since"] = None
                if not first:
                    self.hsrp_events.append(dict(t=round(now - T0, 1), site=site, group=g, old=cur and cur["dev"],
                                                 new=new["dev"], prio={x["dev"]: x["prio"] for x in grp["members"]}))

    def start_hsrp(self):
        self._init_hsrp()
        self._hsrp_tick(first=True)

        def loop():
            while not self._stop.is_set():
                try:
                    self._hsrp_tick()
                except Exception as e:                       # namespace vanished during cleanup, etc.
                    log("hsrp engine:", e)
                self._stop.wait(0.2)
        threading.Thread(target=loop, daemon=True).start()

    # ---------------------------------------------------------------- failures
    def _ifaces(self, dev, ios_if):
        lin = linux_ifname(ios_if)
        names = [ln.split(":")[1].strip().split("@")[0] for ln in sh("ip -o link show", ns=ns_of(dev)).splitlines()]
        sel = [n for n in names if n == lin or n.startswith(lin + ".")]
        c = self.cfg[dev]
        # IOS: a tunnel's line protocol follows its source interface
        sel += [linux_ifname(n) for n, d in c.interfaces.items() if d["tunnel"].get("source", "").replace(
            "GigabitEthernet", "Gi") == ios_if.replace("GigabitEthernet", "Gi") and not d["shutdown"]]
        return sel

    def if_state(self, dev, ios_if, up):
        for n in self._ifaces(dev, ios_if):
            sh(["ip", "-n", ns_of(dev), "link", "set", n, "up" if up else "down"], check=False)
            (self.down_if.discard if up else self.down_if.add)((dev, n))

    def node_state(self, dev, up):
        (self.down_node.discard if up else self.down_node.add)(dev)
        for ln in sh("ip -o link show", ns=ns_of(dev)).splitlines():
            n = ln.split(":")[1].strip().split("@")[0]
            if n != "lo":
                sh(["ip", "-n", ns_of(dev), "link", "set", n, "up" if up else "down"], check=False)
        # a dead router also loses its loopback-sourced sessions: emulate with daemons' interfaces down only

    # ---------------------------------------------------------------- probes
    def ping_ok(self, src, dst, count=3):
        r = subprocess.run(["ip", "netns", "exec", self.hosts[src]["ns"], "ping", "-n", "-c", str(count), "-i", "0.2",
                            "-W", "1", dst], capture_output=True, text=True)
        return r.returncode == 0 and f"{count} received" in r.stdout

    def trace(self, src, dst):
        out = sh(["traceroute", "-n", "-q", "1", "-w", "1", "-m", "14", dst], ns=self.hosts[src]["ns"], check=False)
        hops = []
        for ln in out.splitlines()[1:]:
            w = ln.split()
            if len(w) < 2 or w[1] == "*":
                hops.append("*")
                continue
            # show the device only: the interface of an ICMP time-exceeded source is the router's interface
            # towards the *sender*, which is misleading on asymmetric paths
            owner = self.ip_owner.get(w[1], w[1])
            hops.append(owner if owner.startswith("HSRP") else owner.split()[0])
        return hops

    def bgp_states(self):
        res = {}
        for dev, c in self.cfg.items():
            if not c.bgp or dev in self.down_node:
                continue
            try:
                j = json.loads(vty(dev, "show bgp ipv4 unicast summary json") or "{}")
            except json.JSONDecodeError:
                j = {}
            res[dev] = {p: v.get("state") for p, v in j.get("peers", {}).items()}
        return res

    def wait_ready(self, flows, timeout=150, need_bgp=True):
        t = time.time()
        why = ""
        while time.time() - t < timeout:
            st = self.bgp_states()
            notup = [(d, p) for d, ps in st.items() for p, s in ps.items() if s != "Established"]
            empty = [d for d, ps in st.items() if not ps]
            if need_bgp and (notup or empty):
                why = f"BGP not established: {notup[:3]} {empty[:3]}"
            else:
                bad = [f for f in flows if not self.ping_ok(f[1], f[2], 2)]
                if not bad:
                    return time.time() - t
                why = f"no connectivity: {[b[0] for b in bad]}"
            time.sleep(2)
        raise RuntimeError(f"lab not ready after {timeout}s - {why}")

    def stop(self):
        self._stop.set()


# ----------------------------------------------------------------------------------------------------------
DC = m.DHCP_SERVER
SAAS = m.SAAS_IP


def flows_for(lab):
    ip = lambda h: lab.hosts[h]["ip"]                                    # noqa: E731
    return [
        ("HQ-ENG -> Hanoi-ENG", "PC-ENG01", ip("PC-HN-ENG01")),
        ("Hanoi-AI -> DC server", "PC-HN-AI01", DC),
        ("Hanoi-ENG -> DC server", "PC-HN-ENG01", DC),
        ("DaNang-LOG -> Hanoi-AI (branch-branch)", "PC-DN-LOG01", ip("PC-HN-AI01")),
        ("DaNang-IoT -> DC server", "PC-DN-IOT01", DC),
        ("HQ-ENG -> Internet 8.8.8.8", "PC-ENG01", SAAS),
        ("Hanoi-AI -> Internet 8.8.8.8 (local breakout)", "PC-HN-AI01", SAAS),
        ("DaNang-IoT -> Internet 8.8.8.8 (local breakout)", "PC-DN-IOT01", SAAS),
    ]


SCENARIOS = [
    # id, title, [(kind, dev, ios_if)], expectation
    ("S1", "HQ ISP-A uplink fails (HCMC-EDGE-RTR01 Gi0/0)", [("if", m.E1, "Gi0/0")],
     "EDGE-RTR01 stops originating the OSPF default; core follows EDGE-RTR02; tunnels 2/4 carry inter-site traffic"),
    ("S2", "Hanoi ISP-A uplink fails (HANOI-EDGE-RTR01 Gi0/0)", [("if", m.HN_E1, "Gi0/0")],
     "Tunnel1 dies; HQ reaches Hanoi via EDGE-RTR01 -> EDGE-RTR02 -> Tunnel2; Hanoi HSRP moves to EDGE-RTR02 (track)"),
    ("S3", "HQ edge router 1 fails completely (HCMC-EDGE-RTR01)", [("node", m.E1, None)],
     "all HQ WAN traffic through EDGE-RTR02 / ISP-B"),
    ("S4", "HQ core switch 1 fails completely (HCMC-CORE-SW01)", [("node", m.CORE1, None)],
     "CORE-SW02 becomes HSRP active for every VLAN; OSPF reconverges on the CORE-SW02 uplinks"),
    ("S5", "Core1-Edge1 routed link fails (HCMC-CORE-SW01 Gi1/0)", [("if", m.CORE1, "Gi1/0")],
     "OSPF reroutes through the remaining uplinks; no gateway change"),
    ("S6", "Da Nang ISP-B uplink fails (DANANG-EDGE-RTR02 Gi0/0)", [("if", m.DN_E2, "Gi0/0")],
     "VLANs whose gateway was EDGE-RTR02 move to EDGE-RTR01 (track); Internet breakout via ISP-A"),
    ("S7", "Hanoi edge router 2 fails completely (HANOI-EDGE-RTR02)", [("node", m.HN_E2, None)],
     "HANOI-EDGE-RTR01 becomes active for all VLANs after the HSRP hold time"),
    ("S8", "Whole provider ISP-A fails (ISP-A-RTR)", [("node", m.ISPA, None)],
     "every site falls back to ISP-B: tunnels 2/4, EDGE-RTR02 default, branch gateways on EDGE-RTR02"),
    ("S9", "DOUBLE failure: HQ edge-to-edge link + Hanoi ISP-A uplink",
     [("if", m.E1, "Gi0/3"), ("if", m.HN_E1, "Gi0/0")],
     "known limit: EDGE-RTR01 must reach EDGE-RTR02 through the BGP-free core, which sends branch traffic back "
     "on its default route -> loop for HQ->Hanoi (this is the single-failure behaviour WITHOUT the edge-to-edge link)"),
]


def ping_stream(lab, src, dst, duration, interval=0.2):
    # -c (not -w): with a deadline, ping quits at the first ICMP error and would hide the rest of an outage
    return subprocess.Popen(["ip", "netns", "exec", lab.hosts[src]["ns"], "ping", "-n", "-i", str(interval), "-W", "1",
                             "-c", str(int(duration / interval)), dst], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)


def analyse(out, interval=0.2):
    got = sorted({int(x) for x in re.findall(r"bytes from .*icmp_seq=(\d+)", out)})
    mt = re.search(r"(\d+) packets transmitted, (\d+) received", out)
    sent = int(mt.group(1)) if mt else 0
    lost = sorted(set(range(1, sent + 1)) - set(got))
    run = best = 0
    prev = None
    for s in lost:
        run = run + 1 if prev is not None and s == prev + 1 else 1
        best = max(best, run)
        prev = s
    return dict(sent=sent, received=len(got), lost=len(lost), longest_outage_s=round(best * interval, 1),
                ttl_exceeded=len(re.findall(r"Time to live exceeded", out)),
                unreachable=len(re.findall(r"Unreachable|unreachable", out)))


def capture(lab, outdir):
    outdir.mkdir(parents=True, exist_ok=True)
    cmds = ["show bgp ipv4 unicast summary", "show bgp ipv4 unicast", "show ip route", "show ip ospf neighbor"]
    for dev, c in lab.cfg.items():
        if dev in lab.down_node:
            continue
        txt = []
        for cmd in cmds:
            if ("bgp" in cmd and not c.bgp) or ("ospf" in cmd and c.ospf is None):
                continue
            txt.append(f"{dev}# {cmd}\n{vty(dev, cmd)}")
        (outdir / f"{dev}.txt").write_text("\n".join(txt))
    (outdir / "hsrp_active.json").write_text(json.dumps({f"{s} group {g}": d for (s, g), d in sorted(
        lab.hsrp_active.items())}, indent=1))


def run_scenario(lab, sid, title, actions, expect, flows, outdir, measure=30):
    log(f"=== {sid}: {title}")
    lab.hsrp_events.clear()
    before = {f[0]: lab.trace(f[1], f[2]) for f in flows}
    procs = {f[0]: ping_stream(lab, f[1], f[2], measure) for f in flows}
    time.sleep(4)
    t_fail = time.time()
    for kind, dev, ifn in actions:
        lab.node_state(dev, False) if kind == "node" else lab.if_state(dev, ifn, False)
    res = {}
    for name, p in procs.items():
        res[name] = analyse(p.communicate()[0])
    after = {}
    for f in flows:
        ok = lab.ping_ok(f[1], f[2], 3)
        after[f[0]] = lab.trace(f[1], f[2])
        res[f[0]].update(final_ok=ok, path_before=before[f[0]], path_after=after[f[0]])
    capture(lab, outdir / sid)
    events = [dict(e, t_after_failure=round(e["t"] - (t_fail - T0), 1)) for e in lab.hsrp_events]
    # restore
    for kind, dev, ifn in actions:
        lab.node_state(dev, True) if kind == "node" else lab.if_state(dev, ifn, True)
    try:
        t_rec = round(lab.wait_ready(flows, timeout=200), 1)
        # let HSRP owners return (preempt delay) so the next scenario starts from the designed state
        t = time.time()
        while time.time() - t < 60 * lab.preempt_scale + 20:
            if all(lab.hsrp_active[k] == v for k, v in lab.designed_hsrp.items()):
                break
            time.sleep(1)
    except RuntimeError as e:
        t_rec = str(e)
    verdict = all(v["final_ok"] for v in res.values())
    log(f"    {'PASS' if verdict else 'FAIL'}  worst outage "
        f"{max(v['longest_outage_s'] for v in res.values())}s  recovery after restore: {t_rec}")
    return dict(id=sid, title=title, expect=expect, passed=verdict, flows=res, hsrp_events=events,
                recovery_after_restore_s=t_rec)


def write_report(lab, base, results, outdir, args):
    L = ["# FRR emulation of the TVI routing design - results", "",
         f"Generated by `Tools/frr_emulation/emulate.py` on {time.strftime('%Y-%m-%d %H:%M')} "
         f"(FRR {sh([FRR_BIN + '/zebra', '--version']).split()[2]}, Linux namespaces"
         + ("" if lab.edge_link else ", **edge-to-edge link removed**") + ").", "",
         ("Link model: direct cables - both ends of a failed link see it go down." if lab.carrier_loss else
          "Link model: every link passes through a bridge (as in EVE-NG/GNS3) - a failed interface or a dead router "
          "leaves the far end up/up, so neighbours must detect the failure with their protocol timers "
          "(OSPF dead 4 s, HSRP hold 1.5 s, BGP hold 9 s). This is the conservative case."), "",
         "> **What this is:** the IPv4 routing sections of the generated Cisco startup-configs, translated mechanically",
         "> (`ios2frr.py`) and executed by real OSPF/BGP daemons. **What it is not:** the graded EVE-NG/IOSv lab.",
         "> IPsec is replaced by VXLAN tunnels, HSRP by a rule engine applying the configured priority/track/preempt",
         f"> values (hold time and track polling interval honoured, preempt delay scaled x{lab.preempt_scale}); L2/ACL/ZBF/QoS/IPv6 are not emulated.",
         "> Outage figures are for this emulation only - measure your own in the lab.", "",
         "## Baseline", "",
         f"* Routing converged and all flows reachable {base['ready_s']} s after the daemons started.",
         f"* BGP sessions Established: {base['bgp_up']} / {base['bgp_total']}.", "",
         "| Flow | Path (traceroute) |", "|---|---|"]
    for k, v in base["paths"].items():
        L.append(f"| {k} | {' -> '.join(v)} |")
    L += ["", "HSRP active router per group (rule engine, from `standby` lines):", "",
          "| Site | Group/VLAN | Active |", "|---|---|---|"]
    for (s, g), d in sorted(base["hsrp"].items()):
        L.append(f"| {s} | {g} | {d} |")
    L += ["", "## Failure scenarios", "",
          f"Each flow is pinged every 0.2 s for {args.measure} s; the failure is injected after 4 s. "
          "*Longest outage* = longest run of consecutive lost echo requests x 0.2 s.", "",
          "| ID | Failure | Result | Worst outage | Flows still broken | Recovery after restore |", "|---|---|---|---|---|---|"]
    for r in results:
        worst = max(v["longest_outage_s"] for v in r["flows"].values())
        broken = [k for k, v in r["flows"].items() if not v["final_ok"]]
        L.append(f"| {r['id']} | {r['title']} | {'PASS' if r['passed'] else '**FAIL**'} | {worst} s | "
                 f"{', '.join(broken) or '-'} | {r['recovery_after_restore_s']} s |")
    for r in results:
        L += ["", f"### {r['id']} - {r['title']}", "", f"Expected: {r['expect']}.", "",
              "| Flow | Sent | Lost | Longest outage | TTL exceeded | OK at end | Path after failure |",
              "|---|---|---|---|---|---|---|"]
        for k, v in r["flows"].items():
            L.append(f"| {k} | {v['sent']} | {v['lost']} | {v['longest_outage_s']} s | {v['ttl_exceeded']} | "
                     f"{'yes' if v['final_ok'] else '**no**'} | {' -> '.join(v['path_after'])} |")
        if r["hsrp_events"]:
            L += ["", "HSRP changes: " + "; ".join(
                f"{e['site']} group {e['group']}: {e['old']} -> {e['new']} (+{e['t_after_failure']} s)"
                for e in r["hsrp_events"] if e["t_after_failure"] >= 0 and e["t_after_failure"] <= args.measure)]
    L += ["", "Raw `show` outputs of every router after each failure: `results/<ID>/<hostname>.txt`."]
    (outdir / "EMULATION_RESULTS.md").write_text("\n".join(L) + "\n")
    (outdir / "summary.json").write_text(json.dumps(dict(baseline=dict(base, hsrp={f"{s}/{g}": d for (s, g), d in
                                                                                 base["hsrp"].items()}),
                                                         scenarios=results), indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="comma separated scenario ids (base,S1,...)")
    ap.add_argument("--no-edge-link", action="store_true", help="do not build the HQ edge-to-edge link")
    ap.add_argument("--no-md5", action="store_true", help="strip BGP passwords (kernels without TCP-MD5)")
    ap.add_argument("--preempt-scale", type=float, default=0.1, help="scale HSRP 'preempt delay minimum' (default 0.1)")
    ap.add_argument("--carrier-loss", action="store_true",
                    help="point-to-point links are direct veth pairs: a failure is seen by BOTH ends at once (physical "
                         "cabling). Default: every link passes through a bridge, as in EVE-NG, so the far end stays "
                         "up and must detect the failure with protocol timers (the conservative case)")
    ap.add_argument("--measure", type=int, default=30, help="seconds of continuous ping per scenario")
    ap.add_argument("--out", default=str(HERE / "results"))
    ap.add_argument("--keep", action="store_true", help="leave namespaces and daemons running")
    ap.add_argument("--cleanup", action="store_true")
    a = ap.parse_args()
    if os.geteuid() != 0:
        sys.exit("run as root (network namespaces)")
    lab = Lab(edge_link=not a.no_edge_link, md5=not a.no_md5, preempt_scale=a.preempt_scale,
              carrier_loss=a.carrier_loss)
    if a.cleanup:
        lab.cleanup()
        return
    outdir = Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    only = set(a.only.split(",")) if a.only else None
    try:
        lab.build()
        lab.start_routing()
        flows = flows_for(lab)
        # gateways first come up on the designed HSRP owners, then the rule engine takes over
        t = time.time()
        lab.start_hsrp()
        ready = round(lab.wait_ready(flows, timeout=240), 1)
        time.sleep(60 * lab.preempt_scale + 3)
        lab.designed_hsrp = dict(lab.hsrp_active)
        st = lab.bgp_states()
        base = dict(ready_s=round(time.time() - t, 1), bgp_total=sum(len(v) for v in st.values()),
                    bgp_up=sum(s == "Established" for v in st.values() for s in v.values()),
                    paths={f[0]: lab.trace(f[1], f[2]) for f in flows}, hsrp=dict(lab.hsrp_active))
        log(f"baseline ready in {ready}s; BGP {base['bgp_up']}/{base['bgp_total']} established")
        capture(lab, outdir / "base")
        results = []
        for sid, title, actions, expect in SCENARIOS:
            if only and sid not in only:
                continue
            if not lab.edge_link and any(i == "Gi0/3" for _k, _d, i in actions):
                continue
            results.append(run_scenario(lab, sid, title, actions, expect, flows, outdir, a.measure))
        write_report(lab, base, results, outdir, a)
        log(f"report: {outdir / 'EMULATION_RESULTS.md'}")
    finally:
        lab.stop()
        if not a.keep:
            lab.cleanup()


if __name__ == "__main__":
    main()
