"""Static validation of the addressing plan and the generated configs (no devices needed).

    python3 -m unittest -v Tools.test_model      (from the project root)
"""
import ipaddress as ipa
import re
import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen_configs as g   # noqa: E402
import tvi_model as m     # noqa: E402

CONFIGS = g.render_all()


def cfg_of(name):
    for k, v in CONFIGS.items():
        if k.endswith(f"/{name}.cfg"):
            return v
    raise KeyError(name)


class AddressPlan(unittest.TestCase):
    def test_vlan_subnets_do_not_overlap(self):
        nets = []
        for s, vl in m.SITE_VLANS.items():
            for v, n in vl.items():
                nets.append((f"{s}/V{v}", ipa.ip_network(n)))
            for n, _ in m.SITE_RESERVED[s]:
                nets.append((f"{s}/reserved", ipa.ip_network(n)))
            for n, _ in m.INFRA_BLOCKS[s]:
                nets.append((f"{s}/infra", ipa.ip_network(n)))
        for i, (la, a) in enumerate(nets):
            for lb, b in nets[i + 1:]:
                self.assertFalse(a.overlaps(b), f"{la} {a} overlaps {lb} {b}")

    def test_everything_inside_the_site_aggregate(self):
        for s, d in m.SITES.items():
            agg = ipa.ip_network(d["v4"])
            for v, n in m.SITE_VLANS[s].items():
                self.assertTrue(ipa.ip_network(n).subnet_of(agg), (s, v))
            for n, _ in m.SITE_RESERVED[s]:
                self.assertTrue(ipa.ip_network(n).subnet_of(agg), (s, n))

    def test_site_aggregates_are_disjoint(self):
        nets = [ipa.ip_network(d["v4"]) for d in m.SITES.values()]
        for i in range(3):
            for j in range(i + 1, 3):
                self.assertFalse(nets[i].overlaps(nets[j]))

    def test_vlsm_alignment(self):
        for s, vl in m.SITE_VLANS.items():
            for v, n in vl.items():
                ipa.ip_network(n, strict=True)   # raises if host bits set

    def test_capacity_targets(self):
        for s, d in m.SITES.items():
            cap = sum(ipa.ip_network(n).num_addresses - 3 for v, n in m.SITE_VLANS[s].items()
                      if v in (10, 20, 21, 30))
            self.assertGreaterEqual(cap, d["users_now"], f"{s} users now")
        iot = sum(ipa.ip_network(n).num_addresses - 3 for s in m.SITE_VLANS
                  for v, n in m.SITE_VLANS[s].items() if v in (60, 61, 62))
        self.assertGreaterEqual(iot, 10_000)

    def test_ipv6_plan(self):
        for s, d in m.SITES.items():
            agg = ipa.ip_network(d["v6"])
            for v in m.SITE_VLANS[s]:
                self.assertTrue(ipa.ip_network(m.v6net(s, v)).subnet_of(agg))

    def test_wan_and_tunnel_subnets_unique(self):
        nets = [ipa.ip_network(w["net"]) for w in m.WAN.values()] + \
               [ipa.ip_network(w["net"]) for w in m.ISP_NET_LINK.values()] + \
               [ipa.ip_network(t["net"]) for t in m.TUNNELS.values()] + \
               [ipa.ip_network(x[2]) for x in m.CORE_EDGE_P2P.values()]
        self.assertEqual(len(nets), len(set(nets)))
        for i, a in enumerate(nets):
            for b in nets[i + 1:]:
                self.assertFalse(a.overlaps(b), (a, b))


class Topology(unittest.TestCase):
    def test_links_reference_known_nodes_and_unique_ports(self):
        known = set(m.DEVICES) | set(m.HOSTS)
        seen = Counter()
        for a, ia, b, ib, *_ in m.LINKS:
            self.assertIn(a, known)
            self.assertIn(b, known)
            seen[(a, ia)] += 1
            seen[(b, ib)] += 1
        self.assertEqual([k for k, v in seen.items() if v > 1], [])

    def test_every_host_is_cabled(self):
        cabled = {x for l in m.LINKS for x in (l[0], l[2])}
        for h in m.HOSTS:
            self.assertIn(h, cabled)


class Configs(unittest.TestCase):
    def test_ipv4_interface_addresses_unique(self):
        seen = {}
        for path, txt in CONFIGS.items():
            dev = Path(path).stem
            for ln in txt.splitlines():
                mt = re.match(r"\s*ip address (\d+\.\d+\.\d+\.\d+) (\d+\.\d+\.\d+\.\d+)$", ln)
                if mt:
                    ip = mt.group(1)
                    self.assertNotIn(ip, seen, f"{ip} on {dev} and {seen.get(ip)}")
                    seen[ip] = dev
        # HSRP virtual IPs must not collide with real addresses either
        for path, txt in CONFIGS.items():
            for mt in re.finditer(r"standby \d+ ip (\S+)", txt):
                self.assertNotIn(mt.group(1), seen, mt.group(1))

    def test_ipv6_interface_addresses_unique_per_link(self):
        seen = {}
        for path, txt in CONFIGS.items():
            dev = Path(path).stem
            for mt in re.finditer(r"^\s*ipv6 address (\S+)/\d+$", txt, re.M):
                a = mt.group(1).lower()
                if a in seen and seen[a] != dev:
                    self.fail(f"{a} duplicated on {dev} and {seen[a]}")
                seen[a] = dev

    def test_p2p_peers_share_subnet(self):
        for (core, edge), (cif, eif, net_) in m.CORE_EDGE_P2P.items():
            a, b, mk = m.p2p_v4(core, edge)
            n = ipa.ip_network(net_)
            self.assertIn(ipa.ip_address(a), n)
            self.assertIn(ipa.ip_address(b), n)
            self.assertIn(f"ip address {a} {mk}", cfg_of(core))
            self.assertIn(f"ip address {b} {mk}", cfg_of(edge))

    def test_wan_peers_agree(self):
        for edge, w in m.WAN.items():
            self.assertIn(f"ip address {w['ip']} {w['mask']}", cfg_of(edge))
            self.assertIn(f"ip address {w['isp_ip']} {w['mask']}", cfg_of(w["isp"]))
            self.assertIn(f"neighbor {w['isp_ip']} remote-as {w['asn']}", cfg_of(edge))
            self.assertIn(f"neighbor {w['ip']} remote-as {m.AS_TVI}", cfg_of(w["isp"]))

    def test_tunnel_endpoints_mirror(self):
        for t, d in m.TUNNELS.items():
            hub, spoke = cfg_of(d["hub"]), cfg_of(d["spoke"])
            self.assertIn(f"tunnel destination {d['spoke_out']}", hub)
            self.assertIn(f"tunnel destination {d['hub_out']}", spoke)
            self.assertIn(f"crypto isakmp key {m.IPSEC_PSK} address {d['spoke_out']}", hub)
            self.assertIn(f"crypto isakmp key {m.IPSEC_PSK} address {d['hub_out']}", spoke)
            self.assertIn(f"neighbor {d['spoke_ip']} remote-as {m.AS_TVI}", hub)
            self.assertIn(f"neighbor {d['hub_ip']} remote-as {m.AS_TVI}", spoke)

    def test_references_are_defined(self):
        """Every ACL / policy-map / class-map / route-map / prefix-list / track used is defined in the same file."""
        for path, txt in CONFIGS.items():
            defined = set()
            for mt in re.finditer(r"^(?:ip access-list (?:standard|extended)|ipv6 access-list|policy-map(?: type inspect)?|"
                                  r"class-map(?: type inspect)?(?: match-any| match-all)?|route-map|ip prefix-list|"
                                  r"ipv6 prefix-list|ipv6 dhcp pool|zone security) (\S+)", txt, re.M):
                defined.add(mt.group(1))
            used = set()
            for pat in (r"ip access-group (\S+) in", r"ipv6 traffic-filter (\S+) in", r"service-policy (?:input|output) (\S+)",
                        r"^\s*service-policy type inspect (\S+)", r"match access-group name (\S+)",
                        r"access-class (\S+) in", r"route-map (\S+) out", r"prefix-list (\S+) (?:in|out)",
                        r"ip nat inside source list (\S+)", r"^\s*(?:class|class type inspect) (CM-\S+)",
                        r"zone-member security (\S+)", r"ipv6 dhcp server (\S+)"):
                used |= set(re.findall(pat, txt, re.M))
            missing = {u for u in used if u not in defined and u != "class-default"}
            self.assertEqual(missing, set(), f"{path}: undefined {missing}")

    def test_no_cleartext_secrets_except_labs(self):
        for path, txt in CONFIGS.items():
            self.assertNotIn(m.LAB_PASSWORD, txt, path)
            self.assertNotIn("password 0", txt)

    def test_hsrp_priorities_are_split_between_peers(self):
        a, b = cfg_of(m.CORE1), cfg_of(m.CORE2)
        for v in m.vlans_of("HQ"):
            if v in (90,):
                continue
            pa = re.search(rf"standby {v} priority (\d+)", a).group(1)
            pb = re.search(rf"standby {v} priority (\d+)", b).group(1)
            self.assertEqual(sorted([pa, pb]), ["100", "110"], v)

    def test_stp_root_matches_hsrp_active(self):
        a = cfg_of(m.CORE1)
        for v in m.vlans_of("HQ"):
            if v == 90:
                continue
            pri = re.search(rf"standby {v} priority (\d+)", a).group(1)
            root = any(v in map(int, ln.split()[2].split(",")) and ln.endswith("4096")
                       for ln in a.splitlines() if ln.startswith("spanning-tree vlan"))
            self.assertEqual(pri == "110", root, v)


if __name__ == "__main__":
    unittest.main()
