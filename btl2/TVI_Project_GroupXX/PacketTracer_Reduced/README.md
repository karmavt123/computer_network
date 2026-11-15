# Packet Tracer - REDUCED variant

> **Use this only if your instructor approved it in advance** (the assignment says the main deliverable is EVE-NG/GNS3 with
> full-feature images; PT is allowed only as an approved fallback). This variant keeps the *ideas* (dual core, HSRP,
> LACP, DHCP relay, OSPF, redundant tunnels, local breakout NAT, ACL, VLAN security) in a topology Packet Tracer can run.

**Status: written but NOT tested in Packet Tracer.** PT versions differ in what the 3650 / 2911 images accept. Paste each file,
read the errors, and fix/remove the lines the PT build rejects; note every deviation in the report.

## What is reduced and why

| Full design | PT variant | Reason |
|---|---|---|
| GRE over IPsec + iBGP route reflectors | **GRE tunnels + OSPF** (IPsec crypto optional, see below) | PT does not support `tunnel protection ipsec profile`/BGP RR reliably |
| eBGP to two ISPs | one ISP router with static routes + NAT | PT BGP is limited; the ISP router stands for both ISPs |
| HSRPv2 + IPv6 | HSRP v1, IPv4 only | PT 3650 HSRPv2/IPv6 support varies |
| Zone-based firewall, DAI, DHCP snooping, storm-control | ACLs, port-security, BPDU guard | not available in PT |
| 3 sites, 16 devices | HQ (2 cores, 1 access, 2 edges) + Hanoi (1 edge, 1 switch) + ISP | PT performance |
| IOSv / IOSvL2 | **Cisco 3650-24PS** (L3 switch), **2960-24TT**, **2911** routers, Server-PT, PCs | PT device models |

## Devices and cabling

| Device | Model | Notes |
|---|---|---|
| HQ-CORE1, HQ-CORE2 | 3650-24PS | L3 switch (`ip routing`) |
| HQ-ACC | 2960-24TT | access |
| HQ-EDGE1, HQ-EDGE2 | 2911 | `Gi0/0` = ISP, `Gi0/1` = CORE1, `Gi0/2` = CORE2 |
| ISP | 2911 | `Gi0/0` -> EDGE1, `Gi0/1` -> EDGE2, `Gi0/2` -> HN-EDGE, Loopback0 8.8.8.8 = "Internet" |
| HN-EDGE | 2911 | `Gi0/0` -> ISP, `Gi0/1` -> HN-SW (802.1Q sub-interfaces) |
| HN-SW | 2960-24TT | access |
| SRV-DHCP | Server-PT | 10.1.100.10, VLAN 100 (DHCP + DNS service) |
| PC-EXEC, PC-ENG, PC-GUEST (HQ), PC-HN-ENG, PC-HN-GUEST | PC | DHCP |

| # | A | port | B | port | Type |
|---|---|---|---|---|---|
| 1-2 | HQ-CORE1 | Gi1/0/1-2 | HQ-CORE2 | Gi1/0/1-2 | Port-channel 1 (LACP) - copper straight/cross: use **cross-over** |
| 3 | HQ-CORE1 | Gi1/0/3 | HQ-ACC | Gi0/1 | trunk |
| 4 | HQ-CORE2 | Gi1/0/3 | HQ-ACC | Gi0/2 | trunk |
| 5 | HQ-CORE1 | Gi1/0/4 | HQ-EDGE1 | Gi0/1 | routed 10.1.250.0/30 |
| 6 | HQ-CORE1 | Gi1/0/5 | HQ-EDGE2 | Gi0/1 | routed 10.1.250.4/30 |
| 7 | HQ-CORE2 | Gi1/0/4 | HQ-EDGE1 | Gi0/2 | routed 10.1.250.8/30 |
| 8 | HQ-CORE2 | Gi1/0/5 | HQ-EDGE2 | Gi0/2 | routed 10.1.250.12/30 |
| 9 | HQ-EDGE1 | Gi0/0 | ISP | Gi0/0 | WAN 203.0.113.0/30 |
| 10 | HQ-EDGE2 | Gi0/0 | ISP | Gi0/1 | WAN 198.51.100.0/30 |
| 11 | HN-EDGE | Gi0/0 | ISP | Gi0/2 | WAN 203.0.113.4/30 |
| 12 | HN-EDGE | Gi0/1 | HN-SW | Gi0/1 | trunk |
| 13-16 | HQ-ACC Fa0/1 PC-EXEC, Fa0/2 PC-ENG, Fa0/3 PC-GUEST, Fa0/4 SRV-DHCP | | | | access |
| 17-18 | HN-SW Fa0/1 PC-HN-ENG, Fa0/2 PC-HN-GUEST | | | | access |

If a port name does not exist on your PT model, rename it in the config (the logic stays).

## Addressing (same scheme as the main design)

| Site / VLAN | Subnet | VIP/gateway | Notes |
|---|---|---|---|
| HQ V10 EXEC | 10.1.0.0/23 | 10.1.0.1 (CORE1 .2, CORE2 .3) | |
| HQ V20 ENG | 10.1.4.0/22 | 10.1.4.1 (.2/.3) | |
| HQ V50 GUEST | 10.1.2.0/23 | 10.1.2.1 (.2/.3) | |
| HQ V99 MGMT | 10.1.99.0/24 | 10.1.99.1 (.2/.3) | |
| HQ V100 SERVERS | 10.1.100.0/24 | 10.1.100.1 (.2/.3) | SRV-DHCP = 10.1.100.10 |
| Hanoi V20 ENG | 10.2.4.0/22 | 10.2.4.1 (HN-EDGE Gi0/1.20) | |
| Hanoi V50 GUEST | 10.2.1.0/24 | 10.2.1.1 (Gi0/1.50) | |
| Tunnel1 | 10.255.1.0/30 | EDGE1 .1 - HN .2 | via 203.0.113.2 <-> 203.0.113.6 |
| Tunnel2 | 10.255.1.4/30 | EDGE2 .5 - HN .6 | via 198.51.100.2 <-> 203.0.113.6 |
| Loopbacks | 10.1.255.1/32 CORE1, .2 CORE2, .11 EDGE1, .12 EDGE2; 10.2.255.1 HN-EDGE | | OSPF router-ids |

## Order of work

1. Place and cable the devices as above; set each device's hostname by pasting `configs/<name>.txt` (CLI tab).
2. On **SRV-DHCP** (Services tab): DHCP on -> create pools (default gateway = the VIP, DNS 10.1.100.10, start .21):
   `V10 10.1.0.0/255.255.254.0 gw 10.1.0.1`, `V20 10.1.4.0/255.255.252.0 gw 10.1.4.1`, `V50 10.1.2.0/255.255.254.0 gw 10.1.2.1`,
   `HN20 10.2.4.0/255.255.252.0 gw 10.2.4.1`, `HN50 10.2.1.0/255.255.255.0 gw 10.2.1.1`. (Server IP 10.1.100.10/24, gateway 10.1.100.1.)
3. Set all PCs to DHCP.
4. Test as listed below; take screenshots (they replace the CLI captures of the EVE-NG test plan).

## Tests

| Test | Command | Expected |
|---|---|---|
| LACP | CORE1 `show etherchannel summary` | Po1 (SU) |
| HSRP | CORE1/CORE2 `show standby brief` | complementary Active/Standby |
| STP | `show spanning-tree vlan 10` | CORE1 root |
| DHCP via relay | PC-HN-ENG `ipconfig /renew` | address 10.2.4.x, gw 10.2.4.1 |
| OSPF | EDGE1 `show ip ospf neighbor` | CORE1, CORE2, HN-EDGE (Tunnel1) |
| Tunnel | HN-EDGE `show ip route ospf`, `ping 10.1.100.10` | reply through Tunnel1/2 |
| Local breakout | PC-HN-ENG `ping 8.8.8.8`; HN-EDGE `show ip nat translations` | translation present on **HN-EDGE** (HN-EDGE's static default has AD 1 so it wins over the OSPF default from HQ) |
| Guest isolation | PC-GUEST `ping 10.1.100.10` -> blocked; `ping 8.8.8.8` ok | ACL counters (`show access-lists`) |
| Failover | shut `HQ-EDGE1 Gi0/0`; or CORE1 | OSPF re-routes via EDGE2/Tunnel2; HSRP standby becomes active |

## Optional: IPsec on the tunnels (not included, untested)
Some PT versions accept `crypto isakmp policy`, `crypto ipsec transform-set` and `crypto map` on the WAN interface protecting
the GRE traffic (ACL `permit gre host <local> host <peer>`). If your instructor wants encryption in PT, add it to the WAN
interfaces of EDGE1/EDGE2/HN-EDGE and document the exact syntax your PT version accepted.
