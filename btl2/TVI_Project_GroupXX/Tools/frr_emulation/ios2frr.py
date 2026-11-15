#!/usr/bin/env python3
"""
ios2frr.py - mechanical translation of the *Layer-3 control plane* of a generated Cisco IOS
startup-config into an FRRouting configuration.

Why: the startup-configs in ../../Configurations are generated and only statically checked.  FRR speaks the
same protocols (OSPFv2, BGP-4, route reflection, prefix-lists, route-maps) with an almost identical CLI, so the
routing sections can be translated line by line and executed in Linux network namespaces (see emulate.py).
That exercises the routing *logic* of the submitted configs - best-path selection, route reflection,
next-hop resolution, default origination, failover - with real protocol daemons.

What is translated            | What is NOT (and therefore not proven by the emulation)
------------------------------|---------------------------------------------------------------
interface IPv4 addresses      | IOS syntax acceptance on IOSv/IOSvL2
OSPF (process, per-interface) | IPv6 / OSPFv3 / MP-BGP IPv6 (this sandbox kernel has no IPv6)
BGP IPv4 (eBGP, iBGP, RR)     | IPsec (the GRE/IPsec tunnels become plain VXLAN point-to-point tunnels)
prefix-lists, route-maps      | L2: VLAN trunks, LACP, STP, DHCP snooping, DAI, port-security
static routes (incl. Null0)   | HSRP protocol itself (emulate.py applies the configured priority/track/preempt rules)
NAT overload (as iptables)    | ACLs, zone-based firewall, QoS, DHCP/DNS/NTP services

Only the standard library is used.  The parser is deliberately small: it understands the constructs that
Tools/gen_configs.py emits, nothing more.
"""
import ipaddress
import re


def linux_ifname(ios_name: str) -> str:
    """GigabitEthernet0/1.20 -> g0-1.20, Vlan10 -> vl10, LoopbackN -> lo, Tunnel3 -> tu3."""
    ios_name = ios_name.replace("Gi", "GigabitEthernet", 1) if re.match(r"Gi\d", ios_name) else ios_name
    mt = re.match(r"GigabitEthernet(\d+)/(\d+)(?:\.(\d+))?$", ios_name)
    if mt:
        return f"g{mt.group(1)}-{mt.group(2)}" + (f".{mt.group(3)}" if mt.group(3) else "")
    mt = re.match(r"Vlan(\d+)$", ios_name)
    if mt:
        return f"vl{mt.group(1)}"
    if ios_name.startswith("Loopback"):
        return "lo"
    mt = re.match(r"Tunnel(\d+)$", ios_name)
    if mt:
        return f"tu{mt.group(1)}"
    return ios_name.lower()


def prefix(addr: str, mask: str) -> str:
    return f"{addr}/{ipaddress.ip_network(f'{addr}/{mask}', strict=False).prefixlen}"


def blocks(text: str):
    """Yield (header_line, [indented child lines]) for every top-level IOS statement."""
    head, body = None, []
    for raw in text.splitlines():
        if not raw.strip() or raw.startswith("!"):
            continue
        if raw[0] != " ":
            if head is not None:
                yield head, body
            head, body = raw.rstrip(), []
        elif head is not None and raw.strip() != "!":
            body.append(raw.strip())
    if head is not None:
        yield head, body


class IosConfig:
    """The L3-relevant facts of one startup-config."""

    def __init__(self, text: str):
        self.hostname = ""
        self.interfaces = {}     # ios name -> dict
        self.tracks = {}         # id -> prefix whose presence in the RIB is tracked
        self.track_poll = 15.0   # IOS default polling interval of 'ip route' track objects (seconds)
        self.statics = []        # (prefix, nexthop | "Null0")
        self.prefix_lists = []   # raw lines
        self.route_maps = []     # (header, body)
        self.ospf = None         # body lines of "router ospf N"
        self.bgp = None          # (asn, body lines)
        self.nat = None          # dict(acl=name, out_if=ios name)
        self.acls = {}           # name -> lines
        for head, body in blocks(text):
            w = head.split()
            if w[0] == "hostname":
                self.hostname = w[1]
            elif w[0] == "interface":
                self.interfaces[w[1]] = self._interface(body)
            elif head.startswith("track timer ip route "):
                self.track_poll = float(w[4])
            elif w[0] == "track" and w[1].isdigit() and "ip route" in head:
                self.tracks[int(w[1])] = prefix(w[4], w[5])
            elif head.startswith("ip route "):
                self.statics.append((prefix(w[2], w[3]), w[4]))
            elif head.startswith("ip prefix-list "):
                self.prefix_lists.append(head)
            elif w[0] == "route-map":
                self.route_maps.append((head, body))
            elif head.startswith("router ospf "):
                self.ospf = body
            elif head.startswith("router bgp "):
                self.bgp = (w[2], body)
            elif head.startswith("ip nat inside source list "):
                self.nat = dict(acl=w[5], out_if=w[7])
            elif head.startswith("ip access-list extended "):
                self.acls[w[3]] = body

    @staticmethod
    def _interface(body):
        d = dict(ip=None, shutdown=False, ospf=False, p2p=False, l3=True, hsrp={}, tunnel={}, nat=None, dot1q=None,
                 ospf_timers=[])
        for ln in body:
            w = ln.split()
            if ln.startswith("ip address ") and len(w) == 4:
                d["ip"] = prefix(w[2], w[3])
            elif ln == "shutdown":
                d["shutdown"] = True
            elif ln.startswith("switchport"):
                d["l3"] = False
            elif ln == "no switchport":
                d["l3"] = True
            elif re.match(r"ip ospf \d+ area \d+$", ln):
                d["ospf"] = True
            elif ln == "ip ospf network point-to-point":
                d["p2p"] = True
            elif re.match(r"ip ospf (hello|dead)-interval \d+$", ln):
                d["ospf_timers"].append(ln)
            elif ln.startswith("encapsulation dot1Q "):
                d["dot1q"] = int(w[2])
            elif ln.startswith("tunnel source "):
                d["tunnel"]["source"] = w[2]
            elif ln.startswith("tunnel destination "):
                d["tunnel"]["destination"] = w[2]
            elif ln in ("ip nat inside", "ip nat outside"):
                d["nat"] = w[2]
            elif w[0] == "standby" and len(w) > 2 and w[1].isdigit():
                g = d["hsrp"].setdefault(int(w[1]), dict(vip=None, priority=100, preempt=False, delay=0, tracks=[],
                                                         hold=10.0))
                if w[2] == "ip":
                    g["vip"] = w[3]
                elif w[2] == "priority":
                    g["priority"] = int(w[3])
                elif w[2] == "preempt":
                    g["preempt"] = True
                    if "minimum" in w:
                        g["delay"] = int(w[w.index("minimum") + 1])
                elif w[2] == "track":
                    g["tracks"].append((int(w[3]), int(w[5])))
                elif w[2] == "timers":      # standby N timers msec 500 msec 1500
                    nums = [int(x) for x in w[3:] if x.isdigit()]
                    g["hold"] = nums[-1] / (1000.0 if "msec" in w else 1.0)
        return d


def to_frr(cfg: IosConfig, logfile: str, md5: bool = True, connect_timer: int = 10) -> str:
    """Render frr.conf.  `connect_timer` shortens BGP ConnectRetry so the emulation recovers quickly after a
    restored link; it does not influence failure detection (keepalive/hold are taken from the IOS config)."""
    o = ["frr defaults traditional", f"hostname {cfg.hostname}", f"log file {logfile}", "log timestamp precision 3",
         "ip forwarding", "!"]
    has_ospf = cfg.ospf is not None
    lo_done = False
    for name, d in cfg.interfaces.items():
        if not d["l3"] or d["shutdown"] or not d["ip"] or not d["ospf"] or not has_ospf:
            continue
        lin = linux_ifname(name)
        if lin == "lo":
            if lo_done:
                continue
            lo_done = True
        o += [f"interface {lin}", " ip ospf area 0"]
        if d["p2p"]:
            o.append(" ip ospf network point-to-point")
        o += [" " + t for t in d["ospf_timers"]]
        o.append("!")
    for pfx, nh in cfg.statics:
        o.append(f"ip route {pfx} {nh}")
    o += cfg.prefix_lists
    for head, body in cfg.route_maps:
        o += [head] + [" " + b for b in body] + ["!"]
    if has_ospf:
        o.append("router ospf")
        for ln in cfg.ospf:
            w = ln.split()
            if w[0] == "router-id":
                o.append(f" ospf router-id {w[1]}")
            elif ln == "passive-interface default":
                o.append(" passive-interface default")
            elif ln.startswith("no passive-interface "):
                o.append(f" no passive-interface {linux_ifname(w[2])}")
            elif ln.startswith(("auto-cost", "default-information")):
                o.append(" " + ln)
        o.append("!")
    if cfg.bgp:
        asn, body = cfg.bgp
        o += [f"router bgp {asn}",
              " no bgp ebgp-requires-policy",   # IOS has no such safeguard; keep IOS semantics
              " bgp network import-check"]     # IOS only originates a 'network' that exists in the RIB
        af, skip_af = None, False
        for ln in body:
            w = ln.split()
            if ln == "!":
                continue
            if w[0] == "address-family":
                af, skip_af = w[1], w[1] != "ipv4"
                if not skip_af:
                    o.append(" address-family ipv4 unicast")
                continue
            if ln == "exit-address-family":
                if not skip_af:
                    o.append(" exit-address-family")
                af, skip_af = None, False
                continue
            if skip_af or (w[0] == "neighbor" and ":" in w[1]):   # IPv6 peers: kernel has no IPv6 here
                continue
            ind = "  " if af else " "
            if w[0] == "neighbor":
                if w[2] == "password" and not md5:
                    continue
                if w[2] == "update-source":
                    lo = next(d["ip"] for n, d in cfg.interfaces.items() if n == w[3])
                    o.append(f"{ind}neighbor {w[1]} update-source {lo.split('/')[0]}")
                    continue
                if w[2:] == ["next-hop-self", "all"]:
                    o.append(f"{ind}neighbor {w[1]} next-hop-self force")
                    continue
                if w[2] == "remote-as":
                    o += [f"{ind}{ln}", f"{ind}neighbor {w[1]} timers connect {connect_timer}"]
                    continue
            if w[0] == "network" and "mask" in w:
                o.append(f"{ind}network {prefix(w[1], w[3])}")
                continue
            if ln.startswith("bgp log-neighbor-changes"):
                o.append(f"{ind}{ln}")
                continue
            o.append(f"{ind}{ln}")
        o.append("!")
    return "\n".join(o) + "\n"


def nat_rules(cfg: IosConfig):
    """`ip nat inside source list ACL interface X overload` -> iptables POSTROUTING arguments."""
    if not cfg.nat:
        return []
    out_if = linux_ifname(cfg.nat["out_if"])
    rules = []

    def net(a, wild):
        return str(ipaddress.ip_network(f"{a}/{ipaddress.ip_address(int(ipaddress.ip_address(wild)) ^ 0xFFFFFFFF)}"))
    for ln in cfg.acls.get(cfg.nat["acl"], []):
        w = ln.split()
        if w[0] not in ("permit", "deny") or w[1] != "ip":
            continue
        src = net(w[2], w[3])
        dst = None if w[4] == "any" else net(w[4], w[5])
        r = ["-o", out_if, "-s", src] + (["-d", dst] if dst else [])
        rules.append(r + ["-j", "MASQUERADE" if w[0] == "permit" else "RETURN"])
    return rules


if __name__ == "__main__":
    import sys
    c = IosConfig(open(sys.argv[1]).read())
    print(to_frr(c, "/tmp/frr.log"))
    for r in nat_rules(c):
        print("! iptables -t nat -A POSTROUTING", " ".join(r))
