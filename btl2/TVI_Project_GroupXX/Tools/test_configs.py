#!/usr/bin/env python3
"""
test_configs.py - cross-device consistency checks on the RENDERED startup-configs.

test_model.py checks the data model; this file deliberately ignores the model and reads only the text in
Configurations/*/*.cfg, the way a peer reviewer (or Batfish) would: two devices can only form a BGP session,
an OSPF adjacency, an HSRP group, an IPsec tunnel or a trunk if both ends agree.  Every test below is one
"both ends agree" rule.  Run from the project root:

    python3 -m unittest -v Tools.test_configs
"""
import ipaddress as ipa
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent / "frr_emulation"))
from ios2frr import IosConfig, blocks                                    # noqa: E402

TEXT = {f.stem: f.read_text() for f in sorted((ROOT / "Configurations").rglob("*.cfg"))}
CFG = {n: IosConfig(t) for n, t in TEXT.items()}
OWNER = {}                                    # ip -> (device, interface)
for dev, c in CFG.items():
    for ifn, d in c.interfaces.items():
        if d["ip"] and not d["shutdown"]:
            OWNER[d["ip"].split("/")[0]] = (dev, ifn)


def iface_lines(dev, ifn):
    for head, body in blocks(TEXT[dev]):
        if head == f"interface {ifn}":
            return body
    return []


def bgp_neighbors(dev):
    """ip -> dict(remote_as, password, timers) for IPv4 neighbours."""
    res = {}
    asn, body = CFG[dev].bgp
    for ln in body:
        w = ln.split()
        if w[0] == "neighbor" and ":" not in w[1] and len(w) > 3 and w[2] in ("remote-as", "password", "timers"):
            res.setdefault(w[1], {})[w[2]] = " ".join(w[3:])
    return asn, res


class BgpSessions(unittest.TestCase):
    def test_every_neighbor_statement_has_a_matching_peer(self):
        for dev, c in CFG.items():
            if not c.bgp:
                continue
            asn, nbrs = bgp_neighbors(dev)
            for ip, a in nbrs.items():
                self.assertIn(ip, OWNER, f"{dev}: neighbor {ip} is nobody's address")
                peer = OWNER[ip][0]
                pasn, pn = bgp_neighbors(peer)
                self.assertEqual(a["remote-as"], pasn, f"{dev} -> {peer}: wrong remote-as")
                back = [p for p in pn if p in OWNER and OWNER[p][0] == dev]
                self.assertEqual(len(back), 1, f"{peer} has no (or several) neighbor statement back to {dev}")
                b = pn[back[0]]
                self.assertEqual(b["remote-as"], asn)
                self.assertEqual(a.get("password"), b.get("password"), f"{dev} <-> {peer}: MD5 password differs")
                self.assertEqual(a.get("timers"), b.get("timers"), f"{dev} <-> {peer}: timers differ")

    def test_ibgp_between_loopbacks_sets_update_source(self):
        for dev, c in CFG.items():
            if not c.bgp:
                continue
            asn, nbrs = bgp_neighbors(dev)
            body = "\n".join(c.bgp[1])
            for ip, a in nbrs.items():
                if a["remote-as"] == asn and OWNER[ip][1].startswith("Loopback"):
                    self.assertIn(f"neighbor {ip} update-source Loopback0", body, dev)

    def test_every_ibgp_session_has_fast_timers(self):
        """Found with the FRR emulation: an iBGP peer whose loopback is covered by the site's Null0 summary
        keeps looking reachable to next-hop tracking after it died; only the hold timer removes its routes."""
        for dev, c in CFG.items():
            if not c.bgp or "EDGE" not in dev:
                continue
            asn, nbrs = bgp_neighbors(dev)
            for ip, a in nbrs.items():
                if a["remote-as"] == asn:
                    self.assertEqual(a.get("timers"), "3 9", f"{dev}: iBGP neighbor {ip} uses default timers")

    def test_route_reflector_clients_get_next_hop_self_all(self):
        """A reflector does not rewrite the next hop of reflected routes unless told to ('all')."""
        for dev, c in CFG.items():
            if not c.bgp:
                continue
            body = "\n".join(c.bgp[1])
            for ip in re.findall(r"neighbor (\S+) route-reflector-client", body):
                self.assertIn(f"neighbor {ip} next-hop-self all", body, dev)

    def test_network_statements_have_a_rib_anchor(self):
        """IOS originates 'network X mask Y' only if exactly X/Y is in the routing table."""
        for dev, c in CFG.items():
            if not c.bgp:
                continue
            have = {p for p, _ in c.statics} | {str(ipa.ip_network(d["ip"], strict=False))
                                                for d in c.interfaces.values() if d["ip"]}
            for a, mk in re.findall(r"network (\d\S+) mask (\S+)", "\n".join(c.bgp[1])):
                self.assertIn(str(ipa.ip_network(f"{a}/{mk}")), have, f"{dev}: network {a} has no exact route")

    def test_referenced_policies_exist(self):
        for dev, c in CFG.items():
            if not c.bgp:
                continue
            body = "\n".join(c.bgp[1])
            for pl in set(re.findall(r"^neighbor \S+ prefix-list (\S+)", body, re.M)):
                self.assertRegex(TEXT[dev], rf"(?m)^ipv?6? prefix-list {re.escape(pl)} ", (dev, pl))
            for rm in set(re.findall(r"route-map (\S+) (?:in|out)", body)):
                self.assertTrue(any(h.split()[1] == rm for h, _ in c.route_maps), (dev, rm))


class OspfAdjacencies(unittest.TestCase):
    def _active(self, dev):
        c = CFG[dev]
        if c.ospf is None:
            return {}
        act = {ln.split()[2] for ln in c.ospf if ln.startswith("no passive-interface ")}
        return {n: d for n, d in c.interfaces.items() if n in act and d["ip"] and d["ospf"] and not d["shutdown"]}

    def test_non_passive_interfaces_have_exactly_one_matching_neighbor(self):
        nets = {}
        for dev in CFG:
            for ifn, d in self._active(dev).items():
                nets.setdefault(str(ipa.ip_network(d["ip"], strict=False)), []).append(
                    (dev, ifn, d["p2p"], tuple(sorted(d["ospf_timers"]))))
        self.assertTrue(nets)
        for net, ends in nets.items():
            self.assertEqual(len(ends), 2, f"{net}: OSPF speakers {ends}")
            self.assertEqual(ends[0][2], ends[1][2], f"{net}: network type mismatch {ends}")
            self.assertEqual(ends[0][3], ends[1][3], f"{net}: hello/dead timers differ {ends}")
            self.assertEqual(ends[0][3], ("ip ospf dead-interval 4", "ip ospf hello-interval 1"),
                             f"{net}: adjacency without fast hellos (40 s outage when the neighbour dies silently)")

    def test_ospfv3_timers_mirror_ospfv2(self):
        for dev, t in TEXT.items():
            self.assertEqual(t.count(" ip ospf hello-interval 1"), t.count(" ospfv3 10 hello-interval 1"), dev)
            self.assertEqual(t.count(" ip ospf dead-interval 4"), t.count(" ospfv3 10 dead-interval 4"), dev)

    def test_no_passive_interface_lines_point_at_ospf_enabled_interfaces(self):
        for dev, c in CFG.items():
            for ln in (c.ospf or []):
                if ln.startswith("no passive-interface "):
                    ifn = ln.split()[2]
                    self.assertIn(ifn, c.interfaces, (dev, ifn))
                    self.assertTrue(c.interfaces[ifn]["ospf"], f"{dev} {ifn}: not in OSPF")

    def test_router_ids_unique(self):
        ids = [ln.split()[1] for c in CFG.values() for ln in (c.ospf or []) if ln.startswith("router-id")]
        self.assertEqual(len(ids), len(set(ids)))


class Hsrp(unittest.TestCase):
    def setUp(self):
        self.groups = {}
        for dev, c in CFG.items():
            for ifn, d in c.interfaces.items():
                for g, h in d["hsrp"].items():
                    net = str(ipa.ip_network(d["ip"], strict=False))
                    if h["vip"]:                      # IPv4 groups; their IPv6 twins are checked separately
                        self.groups.setdefault((net, g), []).append((dev, ifn, d, h))

    def test_two_members_same_vip_auth_and_timers(self):
        self.assertTrue(self.groups)
        for (net, g), mem in self.groups.items():
            self.assertEqual(len(mem), 2, f"{net} group {g}: {[x[0] for x in mem]}")
            self.assertEqual(mem[0][3]["vip"], mem[1][3]["vip"])
            self.assertIn(ipa.ip_address(mem[0][3]["vip"]), ipa.ip_network(net))
            self.assertNotIn(mem[0][3]["vip"], OWNER, "VIP collides with a real interface address")
            sig = []
            for dev, ifn, _d, _h in mem:
                ls = iface_lines(dev, ifn)
                sig.append(sorted(x for x in ls if re.match(rf"standby (version|{g} (timers|authentication))", x)))
            self.assertEqual(sig[0], sig[1], f"{net} group {g}: version/timers/authentication differ")

    def test_priorities_differ_and_tracking_can_flip_the_role(self):
        for (net, g), mem in self.groups.items():
            hi, lo = sorted(mem, key=lambda x: -x[3]["priority"])
            self.assertGreater(hi[3]["priority"], lo[3]["priority"])
            self.assertTrue(lo[3]["preempt"] and lo[3]["delay"] == 0, f"{lo[0]}: standby must preempt immediately")
            dec = sum(d for _t, d in hi[3]["tracks"])
            self.assertGreater(lo[3]["priority"], hi[3]["priority"] - dec,
                               f"{net} group {g}: decrement too small to hand the role over")

    def test_ipv6_gateway_is_a_separate_group_with_the_same_behaviour(self):
        """One HSRP group = one address family on IOS: IPv6 uses group <n + 1000>, mirroring the IPv4 group."""
        for (net, g), mem in self.groups.items():
            for dev, ifn, d, h in mem:
                ls = iface_lines(dev, ifn)
                self.assertFalse(any(x.startswith(f"standby {g} ipv6") for x in ls), f"{dev} {ifn}: v4+v6 in group {g}")
                self.assertIn(g + 1000, d["hsrp"], f"{dev} {ifn}: no IPv6 group for {g}")
                h6 = d["hsrp"][g + 1000]
                self.assertTrue(any(x.startswith(f"standby {g + 1000} ipv6 2001:") for x in ls))
                for key in ("priority", "preempt", "delay", "tracks", "hold"):
                    self.assertEqual(h[key], h6[key], f"{dev} {ifn}: IPv6 group differs in {key}")

    def test_tracked_objects_exist(self):
        for (net, g), mem in self.groups.items():
            for dev, _ifn, _d, h in mem:
                for t, _dec in h["tracks"]:
                    self.assertIn(t, CFG[dev].tracks, f"{dev}: track {t} undefined")


class Tunnels(unittest.TestCase):
    def test_tunnel_endpoints_mirror_each_other(self):
        seen = 0
        for dev, c in CFG.items():
            for ifn, d in c.interfaces.items():
                if not d["tunnel"]:
                    continue
                seen += 1
                dst = d["tunnel"]["destination"]
                self.assertIn(dst, OWNER, f"{dev} {ifn}: destination {dst} unknown")
                peer, pif = OWNER[dst]
                pt = CFG[peer].interfaces.get(ifn)
                self.assertIsNotNone(pt, f"{peer} has no {ifn}")
                self.assertEqual(pt["tunnel"]["source"], pif)
                src_ip = c.interfaces[d["tunnel"]["source"]]["ip"].split("/")[0]
                self.assertEqual(pt["tunnel"]["destination"], src_ip)
                self.assertEqual(ipa.ip_network(d["ip"], strict=False), ipa.ip_network(pt["ip"], strict=False))
                # pre-shared key for that peer, identical on both sides
                k1 = re.search(rf"crypto isakmp key (\S+) address {re.escape(dst)}", TEXT[dev])
                k2 = re.search(rf"crypto isakmp key (\S+) address {re.escape(src_ip)}", TEXT[peer])
                self.assertTrue(k1 and k2, f"{dev} <-> {peer}: missing ISAKMP key")
                self.assertEqual(k1.group(1), k2.group(1))
        self.assertEqual(seen, 8)

    def test_dual_stack_tunnels_use_gre_inside_ipsec(self):
        """An IKEv1 'tunnel mode ipsec ipv4' VTI carries IPv4 only; these tunnels also carry IPv6 (MP-BGP)."""
        n = 0
        for dev, c in CFG.items():
            for ifn, d in c.interfaces.items():
                if d["tunnel"]:
                    ls = iface_lines(dev, ifn)
                    if any(x.startswith("ipv6 address") for x in ls):
                        n += 1
                        self.assertNotIn("tunnel mode ipsec ipv4", ls, f"{dev} {ifn}")
                        self.assertIn("tunnel mode gre ip", ls, f"{dev} {ifn}")
                        self.assertTrue(any(x.startswith("tunnel protection ipsec profile") for x in ls), f"{dev} {ifn}")
        self.assertEqual(n, 8)

    def test_crypto_parameters_identical_everywhere(self):
        sigs = set()
        for dev, t in TEXT.items():
            if "crypto isakmp policy" not in t:
                continue
            pol = [b for h, b in blocks(t) if h.startswith("crypto isakmp policy")][0]
            ts = re.findall(r"^crypto ipsec transform-set .*$", t, re.M)
            sigs.add((tuple(pol), tuple(ts)))
        self.assertEqual(len(sigs), 1, "IKE policy / transform-set differ between devices")


class Layer2(unittest.TestCase):
    def _trunk(self, dev, ifn):
        ls = iface_lines(dev, ifn)
        nat = next((x.split()[-1] for x in ls if x.startswith("switchport trunk native vlan")), "1")
        allowed = next((x.split()[-1] for x in ls if x.startswith("switchport trunk allowed vlan")), "all")
        return nat, allowed, any("channel-group" in x for x in ls)

    def test_switch_to_switch_trunks_agree(self):
        sys.path.insert(0, str(ROOT / "Tools"))
        import tvi_model as m                    # only for the cabling table (who is wired to whom)
        n = 0
        for (da, ia, db, ib, kind, _note) in m.LINKS:
            if kind != "trunk" or "SW" not in da or "SW" not in db:
                continue
            a = self._trunk(da, ia.replace("Gi", "GigabitEthernet"))
            b = self._trunk(db, ib.replace("Gi", "GigabitEthernet"))
            self.assertEqual(a[0], b[0], f"{da} {ia} / {db} {ib}: native VLAN mismatch")
            self.assertEqual(a[1], b[1], f"{da} {ia} / {db} {ib}: allowed VLAN list mismatch")
            n += 1
        self.assertGreaterEqual(n, 4)

    def test_port_channel_members_use_lacp_active(self):
        for dev, t in TEXT.items():
            for mode in re.findall(r"channel-group \d+ mode (\S+)", t):
                self.assertEqual(mode, "active", dev)

    def test_access_and_svi_vlans_are_defined(self):
        for dev, t in TEXT.items():
            if not re.search(r"^vlan \d+", t, re.M):
                continue
            defined = set()
            for mt in re.finditer(r"^vlan ([\d,\-]+)$", t, re.M):
                for part in mt.group(1).split(","):
                    lo, _, hi = part.partition("-")
                    defined |= set(range(int(lo), int(hi or lo) + 1))
            used = {int(x) for x in re.findall(r"switchport access vlan (\d+)", t)} | \
                   {int(x) for x in re.findall(r"^interface Vlan(\d+)", t, re.M)}
            self.assertFalse(used - defined, f"{dev}: VLANs used but not created: {sorted(used - defined)}")


class Ipv6Acls(unittest.TestCase):
    def test_explicit_deny_keeps_neighbour_discovery(self):
        """An IPv6 ACL ending in an explicit 'deny ipv6 any any' cancels IOS's implicit ND permits."""
        n = 0
        for dev, t in TEXT.items():
            for head, body in blocks(t):
                if head.startswith("ipv6 access-list") and any(b.startswith("deny ipv6 any any") for b in body):
                    n += 1
                    for need in ("permit icmp any any nd-ns", "permit icmp any any nd-na"):
                        self.assertIn(need, body, f"{dev} {head}: IPv6 hosts could not resolve their gateway")
        self.assertGreater(n, 0)


class DhcpRelay(unittest.TestCase):
    def test_every_relayed_subnet_has_a_pool_with_the_hsrp_vip_as_gateway(self):
        server = next(d for d, t in TEXT.items() if "ip dhcp pool" in t)
        pools = {}
        for head, body in blocks(TEXT[server]):
            if head.startswith("ip dhcp pool"):
                net = next(x.split()[1:3] for x in body if x.startswith("network "))
                gw = next(x.split()[1] for x in body if x.startswith("default-router "))
                pools[str(ipa.ip_network("/".join(net)))] = gw
        server_ips = {ip for ip, (d, _i) in OWNER.items() if d == server}
        n = 0
        for dev, c in CFG.items():
            for ifn, d in c.interfaces.items():
                helper = [x.split()[-1] for x in iface_lines(dev, ifn) if x.startswith("ip helper-address")]
                if not helper:
                    continue
                n += 1
                self.assertTrue(set(helper) <= server_ips, f"{dev} {ifn}: helper {helper} is not the DHCP server")
                net = str(ipa.ip_network(d["ip"], strict=False))
                self.assertIn(net, pools, f"{dev} {ifn}: no DHCP pool for {net}")
                vips = [h["vip"] for h in d["hsrp"].values()]
                self.assertIn(pools[net], vips, f"{net}: pool gateway {pools[net]} is not the HSRP VIP")
        self.assertGreater(n, 20)


if __name__ == "__main__":
    unittest.main(verbosity=2)
