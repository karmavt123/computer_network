"""
tvi_model.py - single source of truth for the TVI enterprise network.

Everything else (startup-configs, IP schema, diagrams, inventory) is generated from this
file, so the documents can never disagree with the configurations.

Run `python3 -m unittest -v Tools.test_model` (from the project root) to validate the
addressing plan (overlaps, duplicate IPs, link consistency).
"""
from __future__ import annotations

import hashlib
import ipaddress as ipa
import os

# --------------------------------------------------------------------------------------
# Global parameters
# --------------------------------------------------------------------------------------
DOMAIN = "tvi.local"
AS_TVI, AS_ISP_A, AS_ISP_B, AS_NET = 65000, 64501, 64502, 64510   # RFC 5398 / private ASNs
HSRP_V6_GROUP_OFFSET = 1000        # IPv6 HSRP group number = VLAN id + 1000 (IPv4 group = VLAN id)
WAN_RATE_BPS = 20_000_000          # simulated ISP circuit rate used by the QoS shaper
NTP_SERVER = "10.1.100.10"         # HCMC-DC-SRV01
DHCP_SERVER = "10.1.100.10"
DHCP_SERVER6 = "2001:db8:1:100::10"
SYSLOG_HOST = "10.1.99.100"        # AUTO-HOST (management VLAN, HQ)
IOT_PLATFORM = "10.1.100.20"       # abstract IoT platform (MQTT/HTTPS), only referenced by ACLs

# Lab secrets: taken from the environment so that nothing "real" is hard-coded.  The
# defaults are LAB-ONLY values and are documented in the README.
LAB_PASSWORD = os.environ.get("TVI_LAB_PASSWORD", "TVI#Lab-2026")
IPSEC_PSK = os.environ.get("TVI_IPSEC_PSK", "TVI-IPsec-Lab-Key-2026")
BGP_KEY = os.environ.get("TVI_BGP_KEY", "TVI-BGP-Lab-2026")
HSRP_KEY = os.environ.get("TVI_HSRP_KEY", "TVI-HSRP-Lab-2026")
ADMIN_USER = "netadmin"

BANNER = ("Unauthorized access to TVI Corporate Infrastructure is prohibited. "
          "All activity is monitored and logged.")


def md5_crypt(password: str, salt: str) -> str:
    """Cisco 'type 5' secret ($1$...), pure Python (the stdlib crypt module is gone in 3.13)."""
    pw, s = password.encode(), salt.encode()
    final = hashlib.md5(pw + s + pw).digest()
    ctx = pw + b"$1$" + s
    for pl in range(len(pw), 0, -16):
        ctx += final[:min(16, pl)]
    i = len(pw)
    while i:
        ctx += b"\0" if i & 1 else pw[:1]
        i >>= 1
    final = hashlib.md5(ctx).digest()
    for i in range(1000):
        c = pw if i & 1 else final
        if i % 3:
            c += s
        if i % 7:
            c += pw
        c += final if i & 1 else pw
        final = hashlib.md5(c).digest()
    itoa = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

    def to64(v, n):
        r = ""
        for _ in range(n):
            r += itoa[v & 0x3F]
            v >>= 6
        return r
    out = ""
    for a, b, c in ((0, 6, 12), (1, 7, 13), (2, 8, 14), (3, 9, 15), (4, 10, 5)):
        out += to64(final[a] << 16 | final[b] << 8 | final[c], 4)
    out += to64(final[11], 2)
    return f"$1${salt}${out}"


SECRET5 = md5_crypt(LAB_PASSWORD, "tvi1")

# --------------------------------------------------------------------------------------
# Sites, VLANs, subnets
# --------------------------------------------------------------------------------------
SITES = {
    "HQ": dict(id=1, code="HCMC", city="Ho Chi Minh City", dirname="HQ_Startup_Configs",
               v4="10.1.0.0/16", v6="2001:db8:1::/48",
               users_now=2000, users_2030=4000),
    "HN": dict(id=2, code="HANOI", city="Hanoi (R&D campus)", dirname="Hanoi_Startup_Configs",
               v4="10.2.0.0/16", v6="2001:db8:2::/48",
               users_now=1500, users_2030=4000),
    "DN": dict(id=3, code="DANANG", city="Da Nang (logistics hub)", dirname="DaNang_Startup_Configs",
               v4="10.3.0.0/16", v6="2001:db8:3::/48",
               users_now=500, users_2030=2000),
}

VLAN_NAMES = {
    10: "EXEC_FINANCE", 20: "RND_ENGINEERING", 21: "AI_LAB_COMPUTE", 30: "LOGISTICS_OPS",
    40: "VOICE", 50: "GUEST", 60: "IOT_SENSORS", 61: "IOT_AUTOMATION", 62: "IOT_SCANNERS_CCTV",
    90: "L3_TRANSIT", 99: "MANAGEMENT", 100: "DC_SERVERS", 998: "NATIVE_UNUSED", 999: "BLACKHOLE",
}
VLAN_PURPOSE = {
    10: "Executives and Finance (maximum protection)",
    20: "Engineering / general corporate staff",
    21: "AI-lab compute and private-cloud access (Hanoi, east-west heavy)",
    30: "Logistics and warehouse operations staff",
    40: "VoIP phones and video endpoints (EF/AF31/CS3)",
    50: "Guests and visitors (Internet only)",
    60: "IoT: trackers / RFID / environmental sensors",
    61: "IoT: AGVs, robots, PLC / warehouse automation",
    62: "IoT: handheld scanners, CCTV",
    90: "Core-to-core L3 transit (OSPF adjacency between the two core switches)",
    99: "Network management (SSH, SNMP, syslog, automation host)",
    100: "Data-centre services: DHCP, DNS, NTP",
    998: "Unused native VLAN for trunks (VLAN-hopping defence)",
    999: "Blackhole VLAN for unused / shut-down access ports",
}
ZONE_OF = {10: "CORP-EXEC", 20: "CORP", 21: "CORP", 30: "CORP", 40: "VOICE", 50: "GUEST",
           60: "IOT", 61: "IOT", 62: "IOT", 90: "INFRA", 99: "MGMT", 100: "SERVERS"}

# VLSM, largest blocks first inside each site /16.  Rule used: <= /22 per user VLAN
# (<= 1022 hosts per broadcast domain) except IoT, which are mostly passive devices.
SITE_VLANS = {
    "HQ": {10: "10.1.0.0/23", 50: "10.1.2.0/23", 20: "10.1.4.0/22", 30: "10.1.8.0/22",
           40: "10.1.12.0/22", 60: "10.1.16.0/22", 99: "10.1.99.0/24", 100: "10.1.100.0/24",
           90: "10.1.248.0/30"},
    "HN": {10: "10.2.0.0/24", 50: "10.2.1.0/24", 20: "10.2.4.0/22", 21: "10.2.8.0/22",
           40: "10.2.12.0/22", 60: "10.2.16.0/24", 99: "10.2.99.0/24"},
    "DN": {10: "10.3.0.0/26", 50: "10.3.0.128/25", 20: "10.3.1.0/24", 30: "10.3.4.0/22",
           40: "10.3.8.0/23", 99: "10.3.99.0/24",
           60: "10.3.128.0/20", 61: "10.3.144.0/20", 62: "10.3.160.0/20"},
}
# Reserved (documented, not configured): growth to 10,000+ users / 30,000 IoT by 2030.
SITE_RESERVED = {
    "HQ": [("10.1.20.0/22", "Growth: next user VLANs (11/21/31)"),
           ("10.1.24.0/21", "Growth: user VLANs"), ("10.1.32.0/19", "Growth: user VLANs 2030"),
           ("10.1.64.0/19", "Growth: user VLANs 2030"),
           ("10.1.128.0/18", "Data-centre EVPN-VXLAN fabric (VTEP loopbacks, L3VNI, tenant VRFs) - design only")],
    "HN": [("10.2.20.0/22", "Growth: next user VLANs"), ("10.2.24.0/21", "Growth: user VLANs"),
           ("10.2.32.0/19", "Growth: user VLANs 2030"), ("10.2.64.0/19", "Growth: AI-lab VLANs 2030"),
           ("10.2.128.0/18", "Growth: second R&D building / private-cloud pods")],
    "DN": [("10.3.12.0/22", "Growth: next user VLANs"), ("10.3.16.0/20", "Growth: user VLANs"),
           ("10.3.32.0/19", "Growth: user VLANs 2030"),
           ("10.3.176.0/20", "Growth: IoT VLAN 63"), ("10.3.192.0/19", "Growth: IoT VLANs 64-65 (to 30,000+ endpoints)"), ("10.3.224.0/20", "Growth: IoT / OT segments")],
}
INFRA_BLOCKS = {  # per site infrastructure ranges (documented in the schema)
    "HQ": [("10.1.250.0/24", "Core-to-edge and edge-to-edge routed /30 links"),
           ("10.1.255.0/24", "Loopbacks /32")],
    "HN": [("10.2.255.0/24", "Loopbacks /32")],
    "DN": [("10.3.255.0/24", "Loopbacks /32")],
}
DHCP_EXCLUDED = 20  # first N addresses of every subnet kept for gateways / static infra


def vlans_of(site):
    return sorted(SITE_VLANS[site])


def net(site, vlan):
    return ipa.ip_network(SITE_VLANS[site][vlan])


def host(site, vlan, n):
    return str(net(site, vlan).network_address + n)


def mask(site, vlan):
    return str(net(site, vlan).netmask)


def v6net(site, vlan):
    return f"2001:db8:{SITES[site]['id']}:{vlan}::/64"


def v6host(site, vlan, n):
    return f"2001:db8:{SITES[site]['id']}:{vlan}::{n}"


def user_vlans(site):
    """VLANs that carry end systems (DHCP-served, carried on access trunks)."""
    return [v for v in vlans_of(site) if v not in (90, 100) and v != 99] + \
           ([99] if 99 in SITE_VLANS[site] else [])


def dhcp_vlans(site):
    return [v for v in vlans_of(site) if v not in (90, 99, 100)]


def acl_name_for(vlan):
    return {10: "ACL-EXEC-IN", 20: "ACL-CORP-IN", 21: "ACL-CORP-IN", 40: "ACL-CORP-IN",
            30: "ACL-LOGISTICS-IN", 50: "ACL-GUEST-IN",
            60: "ACL-IOT-IN", 61: "ACL-IOT-IN", 62: "ACL-IOT-IN"}.get(vlan)


def acl6_name_for(vlan):
    return {10: "ACL6-EXEC-IN", 20: "ACL6-CORP-IN", 21: "ACL6-CORP-IN", 40: "ACL6-CORP-IN",
            30: "ACL6-LOGISTICS-IN", 50: "ACL6-GUEST-IN",
            60: "ACL6-IOT-IN", 61: "ACL6-IOT-IN", 62: "ACL6-IOT-IN"}.get(vlan)


# --------------------------------------------------------------------------------------
# Devices
# --------------------------------------------------------------------------------------
# role: core | access | edge | dcsrv | isp | internet | host
DEVICES = {}


def _dev(name, site, role, image, lo=None, mgmt=None, desc=""):
    DEVICES[name] = dict(name=name, site=site, role=role, image=image, lo=lo, mgmt=mgmt, desc=desc)


def lo4(site, n):
    return f"10.{SITES[site]['id']}.255.{n}"


def lo6(site, n):
    return f"2001:db8:{SITES[site]['id']}:ffff::{n}"


# HQ
_dev("HCMC-CORE-SW01", "HQ", "core", "IOSvL2", lo=1, mgmt=host("HQ", 99, 2), desc="Collapsed core / L3 gateway, STP root for odd VLANs")
_dev("HCMC-CORE-SW02", "HQ", "core", "IOSvL2", lo=2, mgmt=host("HQ", 99, 3), desc="Collapsed core / L3 gateway, STP root for even VLANs")
_dev("HCMC-ACC-SW01", "HQ", "access", "IOSvL2", mgmt=host("HQ", 99, 11), desc="Access switch (user floors)")
_dev("HCMC-ACC-SW02", "HQ", "access", "IOSvL2", mgmt=host("HQ", 99, 12), desc="Access switch (voice + data-centre services)")
_dev("HCMC-EDGE-RTR01", "HQ", "edge", "IOSv", lo=11, mgmt=lo4("HQ", 11), desc="WAN edge, ISP-A, IPsec hub, NAT, ZBF")
_dev("HCMC-EDGE-RTR02", "HQ", "edge", "IOSv", lo=12, mgmt=lo4("HQ", 12), desc="WAN edge, ISP-B, IPsec hub, NAT, ZBF")
_dev("HCMC-DC-SRV01", "HQ", "dcsrv", "IOSv", mgmt=host("HQ", 100, 10), desc="Centralised DHCP/DHCPv6/DNS/NTP (router acting as server)")
# Hanoi
_dev("HANOI-EDGE-RTR01", "HN", "edge", "IOSv", lo=1, mgmt=lo4("HN", 1), desc="Branch gateway (HSRP), ISP-A, IPsec spoke")
_dev("HANOI-EDGE-RTR02", "HN", "edge", "IOSv", lo=2, mgmt=lo4("HN", 2), desc="Branch gateway (HSRP), ISP-B, IPsec spoke")
_dev("HANOI-RND-SW01", "HN", "access", "IOSvL2", mgmt=host("HN", 99, 11), desc="R&D access switch")
# Da Nang
_dev("DANANG-EDGE-RTR01", "DN", "edge", "IOSv", lo=1, mgmt=lo4("DN", 1), desc="Branch gateway (HSRP), ISP-A, IPsec spoke")
_dev("DANANG-EDGE-RTR02", "DN", "edge", "IOSv", lo=2, mgmt=lo4("DN", 2), desc="Branch gateway (HSRP), ISP-B, IPsec spoke")
_dev("DANANG-LOG-SW01", "DN", "access", "IOSvL2", mgmt=host("DN", 99, 11), desc="Logistics / IoT access switch")
# Providers
_dev("ISP-A-RTR", None, "isp", "IOSv", mgmt="192.0.2.1", desc="ISP-A (AS64501)")
_dev("ISP-B-RTR", None, "isp", "IOSv", mgmt="192.0.2.2", desc="ISP-B (AS64502)")
_dev("INTERNET-RTR", None, "internet", "IOSv", mgmt="192.0.2.3", desc="Simulated Internet / SaaS (AS64510)")
# End systems
HOSTS = {
    "AUTO-HOST": dict(site="HQ", vlan=99, ip=host("HQ", 99, 100), desc="Automation / syslog host (Linux)"),
    "PC-EXEC01": dict(site="HQ", vlan=10, ip=None, desc="Executive PC (VPCS)"),
    "PC-ENG01": dict(site="HQ", vlan=20, ip=None, desc="Engineering PC (VPCS)"),
    "PC-GUEST01": dict(site="HQ", vlan=50, ip=None, desc="Guest laptop (VPCS)"),
    "PC-VOICE01": dict(site="HQ", vlan=40, ip=None, desc="IP phone (VPCS)"),
    "PC-HN-ENG01": dict(site="HN", vlan=20, ip=None, desc="Hanoi engineer (VPCS)"),
    "PC-HN-AI01": dict(site="HN", vlan=21, ip=None, desc="Hanoi AI-lab node (VPCS)"),
    "PC-DN-LOG01": dict(site="DN", vlan=30, ip=None, desc="Da Nang warehouse operator (VPCS)"),
    "PC-DN-IOT01": dict(site="DN", vlan=60, ip=None, desc="Da Nang IoT tracker (VPCS)"),
}

CORE1, CORE2 = "HCMC-CORE-SW01", "HCMC-CORE-SW02"
ACC1, ACC2 = "HCMC-ACC-SW01", "HCMC-ACC-SW02"
E1, E2 = "HCMC-EDGE-RTR01", "HCMC-EDGE-RTR02"
DCSRV = "HCMC-DC-SRV01"
HN_E1, HN_E2, HN_SW = "HANOI-EDGE-RTR01", "HANOI-EDGE-RTR02", "HANOI-RND-SW01"
DN_E1, DN_E2, DN_SW = "DANANG-EDGE-RTR01", "DANANG-EDGE-RTR02", "DANANG-LOG-SW01"
ISPA, ISPB, NET = "ISP-A-RTR", "ISP-B-RTR", "INTERNET-RTR"

# Physical links: (devA, ifA, devB, ifB, kind, note)
LINKS = [
    (CORE1, "Gi0/0", CORE2, "Gi0/0", "etherchannel", "Po1 member (LACP)"),
    (CORE1, "Gi0/1", CORE2, "Gi0/1", "etherchannel", "Po1 member (LACP)"),
    (CORE1, "Gi0/2", ACC1, "Gi0/0", "trunk", "802.1Q trunk"),
    (CORE1, "Gi0/3", ACC2, "Gi0/0", "trunk", "802.1Q trunk"),
    (CORE2, "Gi0/2", ACC1, "Gi0/1", "trunk", "802.1Q trunk"),
    (CORE2, "Gi0/3", ACC2, "Gi0/1", "trunk", "802.1Q trunk"),
    (CORE1, "Gi1/0", E1, "Gi0/1", "routed", "p2p 10.1.250.0/30"),
    (CORE1, "Gi1/1", E2, "Gi0/1", "routed", "p2p 10.1.250.4/30"),
    (CORE2, "Gi1/0", E1, "Gi0/2", "routed", "p2p 10.1.250.8/30"),
    (CORE2, "Gi1/1", E2, "Gi0/2", "routed", "p2p 10.1.250.12/30"),
    (E1, "Gi0/3", E2, "Gi0/3", "routed", "p2p 10.1.250.16/30 (edge-to-edge)"),
    (ACC1, "Gi0/2", "PC-EXEC01", "e0", "access", "VLAN 10"),
    (ACC1, "Gi0/3", "PC-ENG01", "e0", "access", "VLAN 20"),
    (ACC1, "Gi1/0", "PC-GUEST01", "e0", "access", "VLAN 50"),
    (ACC1, "Gi1/1", "AUTO-HOST", "eth0", "access", "VLAN 99"),
    (ACC2, "Gi0/2", DCSRV, "Gi0/0", "access", "VLAN 100"),
    (ACC2, "Gi0/3", "PC-VOICE01", "e0", "access", "VLAN 40"),
    (E1, "Gi0/0", ISPA, "Gi0/0", "wan", "ISP-A 203.0.113.0/30"),
    (E2, "Gi0/0", ISPB, "Gi0/0", "wan", "ISP-B 198.51.100.0/30"),
    (HN_E1, "Gi0/0", ISPA, "Gi0/1", "wan", "ISP-A 203.0.113.4/30"),
    (HN_E2, "Gi0/0", ISPB, "Gi0/1", "wan", "ISP-B 198.51.100.4/30"),
    (HN_E1, "Gi0/1", HN_SW, "Gi0/0", "trunk", "802.1Q trunk"),
    (HN_E2, "Gi0/1", HN_SW, "Gi0/1", "trunk", "802.1Q trunk"),
    (HN_SW, "Gi0/2", "PC-HN-ENG01", "e0", "access", "VLAN 20"),
    (HN_SW, "Gi0/3", "PC-HN-AI01", "e0", "access", "VLAN 21"),
    (DN_E1, "Gi0/0", ISPA, "Gi0/2", "wan", "ISP-A 203.0.113.8/30"),
    (DN_E2, "Gi0/0", ISPB, "Gi0/2", "wan", "ISP-B 198.51.100.8/30"),
    (DN_E1, "Gi0/1", DN_SW, "Gi0/0", "trunk", "802.1Q trunk"),
    (DN_E2, "Gi0/1", DN_SW, "Gi0/1", "trunk", "802.1Q trunk"),
    (DN_SW, "Gi0/2", "PC-DN-LOG01", "e0", "access", "VLAN 30"),
    (DN_SW, "Gi0/3", "PC-DN-IOT01", "e0", "access", "VLAN 60"),
    (ISPA, "Gi0/3", NET, "Gi0/0", "wan", "203.0.113.12/30"),
    (ISPB, "Gi0/3", NET, "Gi0/1", "wan", "198.51.100.12/30"),
]

# Core <-> edge routed /30 links (HQ)
CORE_EDGE_P2P = {  # (core, edge): (core_if, edge_if, base network)
    (CORE1, E1): ("Gi1/0", "Gi0/1", "10.1.250.0/30"),
    (CORE1, E2): ("Gi1/1", "Gi0/1", "10.1.250.4/30"),
    (CORE2, E1): ("Gi1/0", "Gi0/2", "10.1.250.8/30"),
    (CORE2, E2): ("Gi1/1", "Gi0/2", "10.1.250.12/30"),
}


# Edge <-> edge routed /30 at HQ.  Without it the core is a "BGP-free" transit between the two edges:
# when EDGE-RTR01 loses a tunnel but keeps its ISP default, it must hand branch traffic to EDGE-RTR02;
# through the core that packet would follow the OSPF default straight back to EDGE-RTR01 (routing loop).
EDGE_EDGE_P2P = dict(a=E1, a_if="Gi0/3", b=E2, b_if="Gi0/3", net="10.1.250.16/30",
                     a_ip="10.1.250.17", b_ip="10.1.250.18", mask="255.255.255.252",
                     a_ip6="2001:db8:1:fa05::1", b_ip6="2001:db8:1:fa05::2")


def p2p_idx(core, edge):
    return list(CORE_EDGE_P2P).index((core, edge)) + 1


def p2p_v4(core, edge):
    n = ipa.ip_network(CORE_EDGE_P2P[(core, edge)][2])
    return str(n.network_address + 1), str(n.network_address + 2), str(n.netmask)


def p2p_v6(core, edge):
    k = p2p_idx(core, edge)
    return f"2001:db8:1:fa{k:02x}::1", f"2001:db8:1:fa{k:02x}::2"


# WAN (ISP facing) /30s: edge -> (isp, isp_if, isp_ip, edge_ip, mask, asn, network)
WAN = {
    E1:    dict(isp=ISPA, isp_if="Gi0/0", net="203.0.113.0/30", asn=AS_ISP_A),
    HN_E1: dict(isp=ISPA, isp_if="Gi0/1", net="203.0.113.4/30", asn=AS_ISP_A),
    DN_E1: dict(isp=ISPA, isp_if="Gi0/2", net="203.0.113.8/30", asn=AS_ISP_A),
    E2:    dict(isp=ISPB, isp_if="Gi0/0", net="198.51.100.0/30", asn=AS_ISP_B),
    HN_E2: dict(isp=ISPB, isp_if="Gi0/1", net="198.51.100.4/30", asn=AS_ISP_B),
    DN_E2: dict(isp=ISPB, isp_if="Gi0/2", net="198.51.100.8/30", asn=AS_ISP_B),
}
for _e, _w in WAN.items():
    _n = ipa.ip_network(_w["net"])
    _w["isp_ip"] = str(_n.network_address + 1)
    _w["ip"] = str(_n.network_address + 2)
    _w["mask"] = str(_n.netmask)
ISP_NET_LINK = {  # ISP <-> INTERNET
    ISPA: dict(isp_if="Gi0/3", net="203.0.113.12/30", net_if="Gi0/0"),
    ISPB: dict(isp_if="Gi0/3", net="198.51.100.12/30", net_if="Gi0/1"),
}
for _i, _w in ISP_NET_LINK.items():
    _n = ipa.ip_network(_w["net"])
    _w["isp_ip"] = str(_n.network_address + 1)
    _w["net_ip"] = str(_n.network_address + 2)
    _w["mask"] = str(_n.netmask)
ISP_AGG = {ISPA: "203.0.113.0/24", ISPB: "198.51.100.0/24"}
ISP_LO = {ISPA: "192.0.2.1", ISPB: "192.0.2.2", NET: "192.0.2.3"}
SAAS_IP = "8.8.8.8"

# GRE/IPsec tunnels, hub (HQ edge) <-> spoke (branch edge)
TUNNELS = {
    1: dict(hub=E1, spoke=HN_E1, via="ISP-A"),
    2: dict(hub=E2, spoke=HN_E2, via="ISP-B"),
    3: dict(hub=E1, spoke=DN_E1, via="ISP-A"),
    4: dict(hub=E2, spoke=DN_E2, via="ISP-B"),
}
for _t, _d in TUNNELS.items():
    _n = ipa.ip_network(f"10.255.1.{4 * (_t - 1)}/30")
    _d["net"] = str(_n)
    _d["hub_ip"], _d["spoke_ip"] = str(_n.network_address + 1), str(_n.network_address + 2)
    _d["mask"] = str(_n.netmask)
    _d["hub_ip6"], _d["spoke_ip6"] = f"2001:db8:ff:{_t}::1", f"2001:db8:ff:{_t}::2"
    _d["hub_out"], _d["spoke_out"] = WAN[_d["hub"]]["ip"], WAN[_d["spoke"]]["ip"]


def tunnels_of(dev):
    return {t: d for t, d in TUNNELS.items() if dev in (d["hub"], d["spoke"])}


def loopback4(dev):
    d = DEVICES[dev]
    return lo4(d["site"], d["lo"]) if d["lo"] and d["site"] else None


def loopback6(dev):
    d = DEVICES[dev]
    return lo6(d["site"], d["lo"]) if d["lo"] and d["site"] else None


def branch_active_e1(site, vlan):
    """HSRP load-sharing at branches: even position in the VLAN list -> E1 active."""
    vl = [v for v in vlans_of(site) if v not in (90, 100)]
    return vl.index(vlan) % 2 == 0


def core_active_sw01(vlan):
    """HSRP/STP load-sharing at HQ: SW01 active/root for these VLANs, SW02 for the rest."""
    return vlan in (10, 30, 50, 99, 100, 90)


# Endpoints for test plan --------------------------------------------------------------
SITE_AGG4 = {s: SITES[s]["v4"] for s in SITES}
SITE_AGG6 = {s: SITES[s]["v6"] for s in SITES}

# --------------------------------------------------------------------------------------
# Global ACL helper data
# --------------------------------------------------------------------------------------
def wild(n):
    n = ipa.ip_network(n)
    return f"{n.network_address} {n.hostmask}"


def all_nets(vlan):
    return [SITE_VLANS[s][vlan] for s in SITE_VLANS if vlan in SITE_VLANS[s]]


EXEC_NETS = all_nets(10)
MGMT_NETS = all_nets(99)
IOT_NETS = all_nets(60) + all_nets(61) + all_nets(62)
VOICE_NETS = all_nets(40)
