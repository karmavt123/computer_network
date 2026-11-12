# TVI - IP Addressing & Subnetting Strategy (IPv4 VLSM + IPv6 hierarchy)

> Generated from `Tools/tvi_model.py` - the same data renders the startup-configs, the Excel file and the diagrams, so the documents cannot disagree with the lab. Excel version: `IP_Schema.xlsx` (formulas for capacity).

## 1. Design rules

1. **One /16 per site, summarisable**: HCMC `10.1.0.0/16`, Hanoi `10.2.0.0/16`, Da Nang `10.3.0.0/16`. Each site advertises exactly one aggregate into BGP (3 routes for the whole enterprise).
2. **VLSM, largest block first, power-of-two aligned** inside each /16; no VLAN is larger than a /22 (<= 1,022 hosts per broadcast domain) except IoT, which is passive and storm-controlled. Subnets are sized for today's load and every site keeps contiguous *reserved* space for the 2030 target (10,000+ users, 30,000+ IoT) - nothing has to be renumbered.
3. **Gateway convention**: `.1` = HSRP/anycast virtual IP, `.2` = first router/switch, `.3` = second; `.4-.20` reserved for static infrastructure (excluded from DHCP).
4. **IPv6 hierarchy**: `2001:db8::/32` (documentation prefix, stand-in for TVI's provider-assigned /32) -> one **/48 per site** (`2001:db8:<site>::/48`) -> one **/64 per VLAN** where the 4th hextet is the VLAN ID (VLAN 20 -> `2001:db8:1:20::/64`). Infrastructure lives in `fa<nn>` (core links), `ffff` (loopbacks) and `2001:db8:ff:<tunnel>::/64` (overlay).
5. **Infrastructure ranges** are kept out of user space: loopbacks `10.<site>.255.0/24`, core-edge links `10.1.250.0/24` (/30 each), overlay tunnels `10.255.1.0/24` (/30 each), management VLAN 99 `10.<site>.99.0/24`.
6. **Public/provider space** (RFC 5737 documentation ranges): ISP-A `203.0.113.0/24`, ISP-B `198.51.100.0/24`.

## 2. Site summary and capacity

| Site | Users now | Users 2030 | User capacity (V10/20/21/30) | IoT capacity | Reserved addresses | IPv4 aggregate | IPv6 aggregate |
|---|---|---|---|---|---|---|---|
| HCMC | 2000 | 4000 | 2551 | 1021 | 35840 | 10.1.0.0/16 | 2001:db8:1::/48 |
| HANOI | 1500 | 4000 | 2295 | 253 | 35840 | 10.2.0.0/16 | 2001:db8:2::/48 |
| DANANG | 500 | 2000 | 1335 | 12279 | 29696 | 10.3.0.0/16 | 2001:db8:3::/48 |

IoT requirement (10,000+ endpoints): Da Nang provides 3 x /20 = 12,279 usable addresses now (VLAN 60/61/62) plus 16,000+ reserved (30,000+ by 2030); HCMC/Hanoi add 1,021 + 253 for building IoT.

## 3.1 HCMC - Ho Chi Minh City (`10.1.0.0/16`, `2001:db8:1::/48`)

| VLAN | Name | IPv4 subnet | Mask | Usable | Gateway (HSRP VIP) | SVI / sub-if (A / B) | DHCP range | IPv6 /64 |
|---|---|---|---|---|---|---|---|---|
| 10 | EXEC_FINANCE | 10.1.0.0/23 | 255.255.254.0 | 510 | 10.1.0.1 | 10.1.0.2 / 10.1.0.3 | 10.1.0.21 - 10.1.1.254 | 2001:db8:1:10::/64 |
| 20 | RND_ENGINEERING | 10.1.4.0/22 | 255.255.252.0 | 1022 | 10.1.4.1 | 10.1.4.2 / 10.1.4.3 | 10.1.4.21 - 10.1.7.254 | 2001:db8:1:20::/64 |
| 30 | LOGISTICS_OPS | 10.1.8.0/22 | 255.255.252.0 | 1022 | 10.1.8.1 | 10.1.8.2 / 10.1.8.3 | 10.1.8.21 - 10.1.11.254 | 2001:db8:1:30::/64 |
| 40 | VOICE | 10.1.12.0/22 | 255.255.252.0 | 1022 | 10.1.12.1 | 10.1.12.2 / 10.1.12.3 | 10.1.12.21 - 10.1.15.254 | 2001:db8:1:40::/64 |
| 50 | GUEST | 10.1.2.0/23 | 255.255.254.0 | 510 | 10.1.2.1 | 10.1.2.2 / 10.1.2.3 | 10.1.2.21 - 10.1.3.254 | 2001:db8:1:50::/64 |
| 60 | IOT_SENSORS | 10.1.16.0/22 | 255.255.252.0 | 1022 | 10.1.16.1 | 10.1.16.2 / 10.1.16.3 | 10.1.16.21 - 10.1.19.254 | 2001:db8:1:60::/64 |
| 90 | L3_TRANSIT | 10.1.248.0/30 | 255.255.255.252 | 2 | - | 10.1.248.1 / 10.1.248.2 | - | 2001:db8:1:90::/64 |
| 99 | MANAGEMENT | 10.1.99.0/24 | 255.255.255.0 | 254 | 10.1.99.1 | 10.1.99.2 / 10.1.99.3 | static | 2001:db8:1:99::/64 |
| 100 | DC_SERVERS | 10.1.100.0/24 | 255.255.255.0 | 254 | 10.1.100.1 | 10.1.100.2 / 10.1.100.3 | static | 2001:db8:1:100::/64 |

Reserved / infrastructure:

| Block | Purpose |
|---|---|
| 10.1.20.0/22 | Growth: next user VLANs (11/21/31) |
| 10.1.24.0/21 | Growth: user VLANs |
| 10.1.32.0/19 | Growth: user VLANs 2030 |
| 10.1.64.0/19 | Growth: user VLANs 2030 |
| 10.1.128.0/18 | Data-centre EVPN-VXLAN fabric (VTEP loopbacks, L3VNI, tenant VRFs) - design only |
| 10.1.250.0/24 | Core-to-edge routed /30 links |
| 10.1.255.0/24 | Loopbacks /32 |

## 3.2 HANOI - Hanoi (R&D campus) (`10.2.0.0/16`, `2001:db8:2::/48`)

| VLAN | Name | IPv4 subnet | Mask | Usable | Gateway (HSRP VIP) | SVI / sub-if (A / B) | DHCP range | IPv6 /64 |
|---|---|---|---|---|---|---|---|---|
| 10 | EXEC_FINANCE | 10.2.0.0/24 | 255.255.255.0 | 254 | 10.2.0.1 | 10.2.0.2 / 10.2.0.3 | 10.2.0.21 - 10.2.0.254 | 2001:db8:2:10::/64 |
| 20 | RND_ENGINEERING | 10.2.4.0/22 | 255.255.252.0 | 1022 | 10.2.4.1 | 10.2.4.2 / 10.2.4.3 | 10.2.4.21 - 10.2.7.254 | 2001:db8:2:20::/64 |
| 21 | AI_LAB_COMPUTE | 10.2.8.0/22 | 255.255.252.0 | 1022 | 10.2.8.1 | 10.2.8.2 / 10.2.8.3 | 10.2.8.21 - 10.2.11.254 | 2001:db8:2:21::/64 |
| 40 | VOICE | 10.2.12.0/22 | 255.255.252.0 | 1022 | 10.2.12.1 | 10.2.12.2 / 10.2.12.3 | 10.2.12.21 - 10.2.15.254 | 2001:db8:2:40::/64 |
| 50 | GUEST | 10.2.1.0/24 | 255.255.255.0 | 254 | 10.2.1.1 | 10.2.1.2 / 10.2.1.3 | 10.2.1.21 - 10.2.1.254 | 2001:db8:2:50::/64 |
| 60 | IOT_SENSORS | 10.2.16.0/24 | 255.255.255.0 | 254 | 10.2.16.1 | 10.2.16.2 / 10.2.16.3 | 10.2.16.21 - 10.2.16.254 | 2001:db8:2:60::/64 |
| 99 | MANAGEMENT | 10.2.99.0/24 | 255.255.255.0 | 254 | 10.2.99.1 | 10.2.99.2 / 10.2.99.3 | static | 2001:db8:2:99::/64 |

Reserved / infrastructure:

| Block | Purpose |
|---|---|
| 10.2.20.0/22 | Growth: next user VLANs |
| 10.2.24.0/21 | Growth: user VLANs |
| 10.2.32.0/19 | Growth: user VLANs 2030 |
| 10.2.64.0/19 | Growth: AI-lab VLANs 2030 |
| 10.2.128.0/18 | Growth: second R&D building / private-cloud pods |
| 10.2.255.0/24 | Loopbacks /32 |

## 3.3 DANANG - Da Nang (logistics hub) (`10.3.0.0/16`, `2001:db8:3::/48`)

| VLAN | Name | IPv4 subnet | Mask | Usable | Gateway (HSRP VIP) | SVI / sub-if (A / B) | DHCP range | IPv6 /64 |
|---|---|---|---|---|---|---|---|---|
| 10 | EXEC_FINANCE | 10.3.0.0/26 | 255.255.255.192 | 62 | 10.3.0.1 | 10.3.0.2 / 10.3.0.3 | 10.3.0.21 - 10.3.0.62 | 2001:db8:3:10::/64 |
| 20 | RND_ENGINEERING | 10.3.1.0/24 | 255.255.255.0 | 254 | 10.3.1.1 | 10.3.1.2 / 10.3.1.3 | 10.3.1.21 - 10.3.1.254 | 2001:db8:3:20::/64 |
| 30 | LOGISTICS_OPS | 10.3.4.0/22 | 255.255.252.0 | 1022 | 10.3.4.1 | 10.3.4.2 / 10.3.4.3 | 10.3.4.21 - 10.3.7.254 | 2001:db8:3:30::/64 |
| 40 | VOICE | 10.3.8.0/23 | 255.255.254.0 | 510 | 10.3.8.1 | 10.3.8.2 / 10.3.8.3 | 10.3.8.21 - 10.3.9.254 | 2001:db8:3:40::/64 |
| 50 | GUEST | 10.3.0.128/25 | 255.255.255.128 | 126 | 10.3.0.129 | 10.3.0.130 / 10.3.0.131 | 10.3.0.149 - 10.3.0.254 | 2001:db8:3:50::/64 |
| 60 | IOT_SENSORS | 10.3.128.0/20 | 255.255.240.0 | 4094 | 10.3.128.1 | 10.3.128.2 / 10.3.128.3 | 10.3.128.21 - 10.3.143.254 | 2001:db8:3:60::/64 |
| 61 | IOT_AUTOMATION | 10.3.144.0/20 | 255.255.240.0 | 4094 | 10.3.144.1 | 10.3.144.2 / 10.3.144.3 | 10.3.144.21 - 10.3.159.254 | 2001:db8:3:61::/64 |
| 62 | IOT_SCANNERS_CCTV | 10.3.160.0/20 | 255.255.240.0 | 4094 | 10.3.160.1 | 10.3.160.2 / 10.3.160.3 | 10.3.160.21 - 10.3.175.254 | 2001:db8:3:62::/64 |
| 99 | MANAGEMENT | 10.3.99.0/24 | 255.255.255.0 | 254 | 10.3.99.1 | 10.3.99.2 / 10.3.99.3 | static | 2001:db8:3:99::/64 |

Reserved / infrastructure:

| Block | Purpose |
|---|---|
| 10.3.12.0/22 | Growth: next user VLANs |
| 10.3.16.0/20 | Growth: user VLANs |
| 10.3.32.0/19 | Growth: user VLANs 2030 |
| 10.3.176.0/20 | Growth: IoT VLAN 63 |
| 10.3.192.0/19 | Growth: IoT VLANs 64-65 (to 30,000+ endpoints) |
| 10.3.224.0/20 | Growth: IoT / OT segments |
| 10.3.255.0/24 | Loopbacks /32 |

## 4. Point-to-point, tunnel and WAN links

| Link | End A | IPv4 A | End B | IPv4 B | Subnet | IPv6 |
|---|---|---|---|---|---|---|
| Core-Edge p2p | HCMC-CORE-SW01 Gi1/0 | 10.1.250.1 | HCMC-EDGE-RTR01 Gi0/1 | 10.1.250.2 | 10.1.250.0/30 | 2001:db8:1:fa01::1 / 2001:db8:1:fa01::2 (/64) |
| Core-Edge p2p | HCMC-CORE-SW01 Gi1/1 | 10.1.250.5 | HCMC-EDGE-RTR02 Gi0/1 | 10.1.250.6 | 10.1.250.4/30 | 2001:db8:1:fa02::1 / 2001:db8:1:fa02::2 (/64) |
| Core-Edge p2p | HCMC-CORE-SW02 Gi1/0 | 10.1.250.9 | HCMC-EDGE-RTR01 Gi0/2 | 10.1.250.10 | 10.1.250.8/30 | 2001:db8:1:fa03::1 / 2001:db8:1:fa03::2 (/64) |
| Core-Edge p2p | HCMC-CORE-SW02 Gi1/1 | 10.1.250.13 | HCMC-EDGE-RTR02 Gi0/2 | 10.1.250.14 | 10.1.250.12/30 | 2001:db8:1:fa04::1 / 2001:db8:1:fa04::2 (/64) |
| IPsec VTI Tunnel1 (ISP-A) | HCMC-EDGE-RTR01 Tunnel1 | 10.255.1.1 | HANOI-EDGE-RTR01 Tunnel1 | 10.255.1.2 | 10.255.1.0/30 | 2001:db8:ff:1::1 / 2001:db8:ff:1::2 (/64) |
| IPsec VTI Tunnel2 (ISP-B) | HCMC-EDGE-RTR02 Tunnel2 | 10.255.1.5 | HANOI-EDGE-RTR02 Tunnel2 | 10.255.1.6 | 10.255.1.4/30 | 2001:db8:ff:2::1 / 2001:db8:ff:2::2 (/64) |
| IPsec VTI Tunnel3 (ISP-A) | HCMC-EDGE-RTR01 Tunnel3 | 10.255.1.9 | DANANG-EDGE-RTR01 Tunnel3 | 10.255.1.10 | 10.255.1.8/30 | 2001:db8:ff:3::1 / 2001:db8:ff:3::2 (/64) |
| IPsec VTI Tunnel4 (ISP-B) | HCMC-EDGE-RTR02 Tunnel4 | 10.255.1.13 | DANANG-EDGE-RTR02 Tunnel4 | 10.255.1.14 | 10.255.1.12/30 | 2001:db8:ff:4::1 / 2001:db8:ff:4::2 (/64) |
| WAN to ISP-A-RTR | HCMC-EDGE-RTR01 Gi0/0 | 203.0.113.2 | ISP-A-RTR Gi0/0 | 203.0.113.1 | 203.0.113.0/30 | n/a (IPv4 transport; IPv6 rides inside IPsec) |
| WAN to ISP-A-RTR | HANOI-EDGE-RTR01 Gi0/0 | 203.0.113.6 | ISP-A-RTR Gi0/1 | 203.0.113.5 | 203.0.113.4/30 | n/a (IPv4 transport; IPv6 rides inside IPsec) |
| WAN to ISP-A-RTR | DANANG-EDGE-RTR01 Gi0/0 | 203.0.113.10 | ISP-A-RTR Gi0/2 | 203.0.113.9 | 203.0.113.8/30 | n/a (IPv4 transport; IPv6 rides inside IPsec) |
| WAN to ISP-B-RTR | HCMC-EDGE-RTR02 Gi0/0 | 198.51.100.2 | ISP-B-RTR Gi0/0 | 198.51.100.1 | 198.51.100.0/30 | n/a (IPv4 transport; IPv6 rides inside IPsec) |
| WAN to ISP-B-RTR | HANOI-EDGE-RTR02 Gi0/0 | 198.51.100.6 | ISP-B-RTR Gi0/1 | 198.51.100.5 | 198.51.100.4/30 | n/a (IPv4 transport; IPv6 rides inside IPsec) |
| WAN to ISP-B-RTR | DANANG-EDGE-RTR02 Gi0/0 | 198.51.100.10 | ISP-B-RTR Gi0/2 | 198.51.100.9 | 198.51.100.8/30 | n/a (IPv4 transport; IPv6 rides inside IPsec) |
| ISP transit | ISP-A-RTR Gi0/3 | 203.0.113.13 | INTERNET-RTR Gi0/0 | 203.0.113.14 | 203.0.113.12/30 | n/a |
| ISP transit | ISP-B-RTR Gi0/3 | 198.51.100.13 | INTERNET-RTR Gi0/1 | 198.51.100.14 | 198.51.100.12/30 | n/a |

## 5. Loopbacks and management addresses

| Device | Role | Loopback0 IPv4 | Loopback0 IPv6 | Management address used by automation |
|---|---|---|---|---|
| HCMC-CORE-SW01 | core | 10.1.255.1 | 2001:db8:1:ffff::1 | 10.1.99.2 |
| HCMC-CORE-SW02 | core | 10.1.255.2 | 2001:db8:1:ffff::2 | 10.1.99.3 |
| HCMC-EDGE-RTR01 | edge | 10.1.255.11 | 2001:db8:1:ffff::11 | 10.1.255.11 |
| HCMC-EDGE-RTR02 | edge | 10.1.255.12 | 2001:db8:1:ffff::12 | 10.1.255.12 |
| HANOI-EDGE-RTR01 | edge | 10.2.255.1 | 2001:db8:2:ffff::1 | 10.2.255.1 |
| HANOI-EDGE-RTR02 | edge | 10.2.255.2 | 2001:db8:2:ffff::2 | 10.2.255.2 |
| DANANG-EDGE-RTR01 | edge | 10.3.255.1 | 2001:db8:3:ffff::1 | 10.3.255.1 |
| DANANG-EDGE-RTR02 | edge | 10.3.255.2 | 2001:db8:3:ffff::2 | 10.3.255.2 |
| ISP-A-RTR | isp | 192.0.2.1 | - | 192.0.2.1 |
| ISP-B-RTR | isp | 192.0.2.2 | - | 192.0.2.2 |
| INTERNET-RTR | internet | 192.0.2.3 | - | 192.0.2.3 |

## 6. Autonomous systems

| AS | Owner | Role |
|---|---|---|
| 65000 | TVI (all 6 edge routers) | iBGP over IPsec VTI, HQ routers = route reflectors |
| 64501 | ISP-A | eBGP: default route to TVI, transit to Internet |
| 64502 | ISP-B | eBGP: default route to TVI, transit to Internet |
| 64510 | INTERNET | Hosts 8.8.8.8/32 as SaaS / public-DNS stand-in |

## 7. Security zones (policy keyed on VLAN)

| Zone | VLANs | Policy summary |
|---|---|---|
| CORP-EXEC | 10 | Reaches everything except MGMT; nobody else may initiate towards it (TCP established / ICMP echo-reply only) |
| CORP | 20, 21, 30, 40 | Servers + Internet; EXEC/FINANCE and MGMT blocked; V20/21/40 also blocked from IoT |
| GUEST | 50 | DHCP + Internet only; every RFC1918 destination denied; public DNS (8.8.8.8 / 1.1.1.1) |
| IOT | 60, 61, 62 | DHCP/DNS/NTP + IoT platform (8883/443) only; no lateral movement; no Internet |
| MGMT | 99 | SSH (VTY ACL) only from management prefixes; reachable from nobody else |
| SERVERS | 100 | DHCP / DNS / NTP central services for all sites |
