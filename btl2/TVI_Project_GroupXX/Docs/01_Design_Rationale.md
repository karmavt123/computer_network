# Design rationale

Why the network looks the way it does. Each decision is tied to a sentence of the assignment brief; each
alternative that was rejected is named with the reason. Addresses and names are in `Design/IP_Schema.md`;
the lines of configuration are explained in `Docs/02_Config_Walkthrough.md`.

## 1. From the brief to the design

| # | The brief says | Design answer | Status |
|---|---|---|---|
| R1 | Three regions: HCMC HQ (data centre, 2,000+ users), Hanoi R&D (1,500+), Da Nang logistics (500+ users, IoT) | One `/16` + one `/48` per site; HQ = campus with collapsed core, branches = dual edge routers + access | implemented |
| R2 | "Elimination of all single points of failure across Layer 2 and Layer 3" | 2 cores (LACP Po1), 2 uplinks per access switch, HSRP on every gateway, 2 edge routers and 2 ISPs per site, 2 tunnels per branch, edge-to-edge link at HQ | implemented (limit: one access switch per branch in the lab) |
| R3 | "Direct cloud access from branch offices without backhauling traffic through the HQ" | Every edge router has its own ISP default route and NAT; only `10.0.0.0/8` destinations use the tunnels | implemented |
| R4 | "Provision new VLANs, routing rules and security policies programmatically" | Data model -> generated configs; Netmiko script and Ansible playbook for baseline, VLANs, backups | implemented |
| R5 | 4,000+ users, 10,000+ IoT endpoints, guests; 10,000+ users by 2030 | VLSM with contiguous reserved blocks; 12,279 IoT addresses in Da Nang today | implemented (addressing) |
| R6 | "10/40 Gbps backbone throughput at HQ and 99.99 % uptime" | Redundant pairs everywhere (section 6); lab links are 1 Gbit/s virtual interfaces standing in for 10/40G | design + lab substitute |
| R7 | "Integrate voice, video and data ... with strict QoS guarantees"; VoIP prioritised on all WAN links | Re-mark at the edge, LLQ inside a shaper on every WAN interface | implemented |
| R8 | FHRP for gateways; EtherChannel (LACP) on core links | HSRPv2 on every SVI / sub-interface; Po1 | implemented |
| R9 | Hierarchical IPv4 (VLSM) and IPv6; DHCP centralised at HQ with relay at branches | See IP plan; `HCMC-DC-SRV01` + `ip helper-address` everywhere | implemented |
| R10 | OSPF inside a site (underlay); BGP between sites and to ISPs (overlay) | OSPF/OSPFv3 area 0 per site; eBGP to ISPs; iBGP with route reflectors over IPsec | implemented |
| R11 | Micro-segmentation, NGFW at the edge, ACLs separating IoT, Guest and Corporate | Per-VLAN inbound ACLs; zone-based firewall on the edges as NGFW stand-in | implemented (stateless ACLs) |
| R12 | Port security, DHCP snooping, DAI | On every access switch | implemented |
| R13 | Data-centre fabric: spine-leaf with EVPN-VXLAN | Address block reserved, design described | design only |
| R14 | SD-WAN over dual-ISP broadband and MPLS | The hand-built overlay shows the mechanisms; controller-based SD-WAN described | design only |
| R15 | ZTNA / SASE for the hybrid workforce | Described as the target state | design only |
| R16 | WLC with CAPWAP; Wi-Fi 6/7 for Da Nang | Described; management VLAN and AP port model prepared | design only |
| R17 | "Dynamic VLAN assignment" in the campus | 802.1X/RADIUS described; lab uses static access VLANs | design only |

"Design only" items cannot run on the lab images (IOSv/IOSvL2) and are stated as such in the report.

## 2. Structural decisions and the alternatives

### 2.1 Collapsed core at HQ, not three tiers

Two L3 switches are distribution and core at once. A separate distribution layer pays off when many access
blocks have to be aggregated and isolated from each other; with the access layer of one building it adds two
more devices in every path and nothing else. The design leaves the door open: the routed uplinks and the OSPF
area already separate "core" from "edge", so distribution pairs can be inserted later without renumbering.

### 2.2 Layer 2 only where it is needed

VLANs span access-to-core at HQ and stop there. Core-to-edge, edge-to-edge and everything WAN-side is routed.
Spanning tree therefore protects a small domain (two cores, the access switches), and a loop cannot propagate
towards the WAN or another site - the "frequent broadcast storms" of the legacy flat network are confined by
construction, then capped by storm-control at the access ports.

### 2.3 Branches: routers as gateways

A branch has two edge routers and an access switch; the routers are the default gateways (802.1Q
sub-interfaces + HSRP). A pair of L3 switches per branch would be the next step at the 2030 size; today it
would double the branch device count for no functional gain.

### 2.4 WAN: hub-and-spoke with two tunnels per branch

| Option | Tunnels | Why not / why |
|---|---|---|
| Full mesh over both ISPs | 12 | Most traffic is branch <-> HQ (data centre, DHCP/DNS); branch <-> branch is rare. Twelve tunnels to operate for little gain |
| DMVPN (dynamic spoke-to-spoke) | 4 static + on demand | The right evolution; left out because NHRP adds a second control plane to verify and the lab images scale it poorly |
| **Hub-and-spoke, 2 x 2 tunnels** | **4** | Chosen: one tunnel per branch per ISP, hubs are the two HQ edges |
| SD-WAN controller | - | Production answer to "SD-WAN deployment"; not available in the lab. The manual overlay demonstrates what the controller automates |

Branch-to-branch traffic transits an HQ edge (one extra hop, no core involved).

### 2.5 Two routing protocols with a clear split

* **OSPF** knows everything *inside* a site: loopbacks, links, user subnets, and a default route.
* **BGP** knows exactly three prefixes between sites (`10.1/16`, `10.2/16`, `10.3/16`) plus the ISP defaults.

Sites never exchange OSPF routes, so a flapping VLAN in Hanoi cannot cause an SPF run in HCMC, and the WAN
policy (which path is primary) is expressed with BGP attributes instead of metric arithmetic. The core
switches run no BGP at all: they follow the OSPF default to an edge.

### 2.6 Internet breakout at every site

Each edge router receives a default route from its own ISP and translates locally. The NAT rule excludes
corporate destinations, so a broken VPN drops inter-site traffic instead of leaking it to the Internet.
This is the brief's "optimized traffic flow": a Hanoi user reaches a SaaS service in two hops
(gateway -> ISP), not through HCMC.

## 3. How traffic flows (normal operation)

Paths below were traced in the routing emulation (`Tools/frr_emulation/results/EMULATION_RESULTS.md`) and are
what `traceroute` should show in the lab.

| Flow | Path |
|---|---|
| HQ engineer -> Hanoi engineer | CORE-SW02 (gateway of VLAN 20) -> EDGE-RTR01 -> Tunnel1 -> HANOI-EDGE-RTR01 -> host |
| Hanoi engineer -> HQ data centre | HANOI-EDGE-RTR02 (gateway of VLAN 20) -> Tunnel2 -> EDGE-RTR02 -> core -> DC-SRV01 |
| Hanoi AI lab -> HQ data centre | HANOI-EDGE-RTR01 (gateway of VLAN 21) -> Tunnel1 -> EDGE-RTR01 -> core -> DC-SRV01 |
| Da Nang -> Hanoi | DANANG-EDGE-RTR01 -> Tunnel3 -> EDGE-RTR01 -> Tunnel1 -> HANOI-EDGE-RTR01 |
| HQ -> Internet | core -> EDGE-RTR01 (NAT) -> ISP-A |
| Hanoi -> Internet | the VLAN's gateway router (NAT) -> its own ISP; HQ is not in the path |

Two things are deliberate:

* **Gateways are split per VLAN** between the two routers of a site (HSRP active roles alternate), so both
  ISPs and both tunnels carry traffic all the time - a backup path that is used daily is known to work.
* **Paths can be asymmetric.** HQ always prefers the ISP-A tunnel towards a branch (local preference 200),
  while a branch VLAN whose gateway is the ISP-B router sends through the ISP-B tunnel. That is why the
  firewall rule between LAN and VPN zones is stateless (`pass`): a stateful rule would drop the half of the
  flow that returns through the other edge.

## 4. What happens when something fails

"Bound" is what the configured timers guarantee; the lab measurement goes into the report.

| Failure | What reacts | Bound |
|---|---|---|
| One Po1 member | LACP removes the member; no STP or routing change | < 1 s |
| Access uplink | RSTP alternate port takes over | ~1 s |
| Core switch | HSRP hold 1.5 s for the gateways; OSPF dead 4 s for transit through the dead core | <= 4 s |
| Core-edge link | OSPF (link-down or dead interval) | <= 4 s |
| HQ ISP-A uplink | EDGE-RTR01 loses its eBGP default and withdraws the OSPF default; cores follow EDGE-RTR02. Branches detect the dead tunnel by BGP hold | <= 9 s |
| Branch ISP uplink | The router loses its tunnel: route to `10.1.0.0/16` disappears, track object goes down (1 s poll), HSRP priority drops 30, the other router preempts immediately. HQ side: BGP hold, then EDGE-RTR01 uses EDGE-RTR02 over the edge-to-edge link | <= 10 s |
| HQ edge router | OSPF dead 4 s (default route), BGP hold 9 s on its sessions, including the edge-to-edge iBGP session | <= 9 s |
| Whole ISP | Sum of the above at every site; everything converges on the other ISP | <= 10 s |

Why 9 s is the dominant number: a tunnel end has no physical link to its peer, so the far end notices a
failure only when BGP keepalives stop (3 s keepalive, 9 s hold). BFD would bring this under a second; it is
the first item of the roadmap because the lab images do not support it on tunnel interfaces.

## 5. Problems found before the lab - and what they taught

All were found by reasoning about the generated configs or by running their routing sections in the
FRR emulation; none was visible in a syntax check. They are good material for the "Challenges" page.

### 5.1 Routing loop between the core and an edge

*Situation.* Hanoi loses ISP-A. Tunnel1 dies. EDGE-RTR01 still has its own Internet, so it keeps advertising
the OSPF default and the cores keep sending everything to it. For `10.2.0.0/16` EDGE-RTR01 now has one BGP
path: via EDGE-RTR02 (next hop = its loopback).

*Problem.* Originally the only way from one edge to the other was through a core switch. The core receives
a packet for `10.2.x.x`, knows no such route (it runs no BGP), follows its default - back to EDGE-RTR01.
The packet circles until its TTL expires.

*Fix.* A direct routed link between the two HQ edges (`Gi0/3`, `10.1.250.16/30`). The next hop is now one hop
away and the core is never asked to route a prefix it does not know.

*Proof.* Emulation scenario S2 (with the link: traffic flows via EDGE-RTR02) and S9 (link removed: traceroute
shows CORE-SW02 -> EDGE-RTR01 -> CORE-SW02 -> ... and "Time to live exceeded").

*Lesson.* A BGP-free core is safe only if every BGP router can reach every BGP next hop without transiting it.

### 5.2 A dead peer that still looked reachable

*Situation.* EDGE-RTR01 dies. EDGE-RTR02 had learned "Hanoi via EDGE-RTR01" (local preference 200) over the
edge-to-edge iBGP session.

*Problem.* OSPF removes EDGE-RTR01's loopback within seconds, and BGP normally invalidates routes whose next
hop is unreachable. But each edge has a static route `10.1.0.0/16 -> Null0` (the anchor for its BGP
aggregate), and the dead loopback `10.1.255.11` lies inside it. The next hop still "resolves" - to Null0.
EDGE-RTR02 kept the dead path as best and discarded the traffic until the BGP hold timer (180 s by default
on that session) expired.

*Fix.* `timers 3 9` on the edge-to-edge session too, so the session - and its routes - are gone after 9 s.

*Lesson.* A summary route to Null0 defeats next-hop tracking for every address it covers.

### 5.3 A standby router that waited a minute

*Problem.* `standby preempt delay minimum 60` was generated on both routers of a group. The delay is right
for the designed owner (after a reboot it should wait for routing to converge before it takes the gateway
back). On the *other* router it postpones a tracking-driven takeover by a minute. In addition, IOS polls
`track ... ip route` objects only every 15 s by default.

*Fix.* Delay only on the designed owner, plain `preempt` on its partner; `track timer ip route 1`.

### 5.4 Links that do not go down

*Problem.* In a virtual lab (and across any L2 device or provider circuit) a dead neighbour leaves the local
interface up/up. With default OSPF timers the neighbour is declared dead after 40 s.

*Fix.* `ip ospf hello-interval 1` / `dead-interval 4` (and the OSPFv3 equivalents) on every interface that
forms an adjacency. The emulation now connects every link through a bridge to reproduce exactly this.

### 5.5 IPv6 through an IPv4-only tunnel

*Problem.* The tunnels were first generated as IKEv1 virtual tunnel interfaces (`tunnel mode ipsec ipv4`).
Such a tunnel carries IPv4 only, but the design runs MP-BGP IPv6 sessions and IPv6 traffic across it - they
would never have come up.

*Fix.* GRE tunnels protected by IPsec in transport mode: GRE carries IPv4, IPv6 and multicast in one tunnel.
(IKEv2 "mixed mode" VTIs are the alternative on newer software.)

### 5.6 Smaller corrections of the same kind

* **One HSRP group, one address family.** IPv4 and IPv6 virtual gateways were in the same group number; the
  IPv6 gateway is now its own group (VLAN id + 1000) with identical priority, preempt and tracking.
* **IPv6 ACL with an explicit final deny** silently removes the implicit permits for neighbour discovery -
  IoT hosts could not have resolved their gateway over IPv6. `nd-ns` / `nd-na` are now permitted explicitly.

Each of these is now guarded by a test in `Tools/test_configs.py`, so a later change cannot reintroduce it.

## 6. Availability reasoning (99.99 %)

Four nines allow about 52.6 minutes of downtime per year: $(1 - 0.9999) \times 365.25 \times 24 \times 60 \approx 52.6$ min.

For two independent components in parallel, each with availability $a$, the pair has
$A = 1 - (1 - a)^2$. For components in series the availabilities multiply.

The figures below are **illustrative assumptions**, not vendor data: a single router or switch at
$a = 99.9\,\%$ (about 8.8 h/year), a single ISP circuit at $99.5\,\%$.

| Element | Single | Redundant pair |
|---|---|---|
| Core switch | 99.9 % | $1 - 0.001^2 = 99.9999\,\%$ |
| Edge router + its ISP circuit (in series) | $0.999 \times 0.995 = 99.4\,\%$ | $1 - 0.006^2 \approx 99.996\,\%$ |

Core pair and edge/ISP pair in series: $0.999999 \times 0.99996 \approx 99.996\,\%$ for "an HQ user can reach
the WAN". The redundant design reaches the target under these assumptions; any single box in the path
(99.4 % for the WAN edge) would miss it by two orders of magnitude.

Two honest limits:

* A host connected to **one** access switch has that switch's availability (99.9 % in this example). Four
  nines for an endpoint require dual-homing (servers) or accepting that the SLA applies to the shared
  infrastructure. The lab branch with a single access switch has the same limit.
* The arithmetic assumes failovers are fast and automatic. A 10 s convergence a few times a year costs
  seconds, not minutes - but a failover that does not happen (problems 5.1-5.3) costs the whole outage.
  That is why the failover tests matter more than the formula.

## 7. Growth to 2030

| Dimension | Today | 2030 | What changes |
|---|---|---|---|
| Users | 4,000 | 10,000+ | New user VLANs from the reserved blocks of each `/16` (HQ 35,840 addresses reserved, Hanoi 35,840, Da Nang 29,696); no renumbering |
| IoT | 10,000+ | 30,000+ | Da Nang has 3 x `/20` in use and 16,000+ reserved addresses |
| BGP | 3 aggregates | 3 + new sites | One `/16` per new site; the route reflectors already scale |
| Campus | collapsed core | add distribution pairs | Routed uplinks are in place; MST replaces rapid-PVST when the VLAN count grows |
| Backbone | 1G lab links | 10/40G | Po1 as 2 x 40G, 10G uplinks; no design change |
| WAN | 4 static tunnels | DMVPN or SD-WAN | Same addressing and BGP policy |
| Data centre | one server VLAN | EVPN-VXLAN fabric | `10.1.128.0/18` reserved for VTEPs and tenant VRFs |

## 8. Lab substitutions

| In the lab | Stands for | Consequence |
|---|---|---|
| IOSv router `HCMC-DC-SRV01` | DHCP/DNS/NTP servers | Same protocols; no server redundancy shown |
| Zone-based firewall on the edge routers | NGFW pair | Stateful inspection yes; no application/IPS features |
| 1 Gbit/s virtual links | 10/40 Gbit/s | Throughput target cannot be measured in the lab |
| Shaper at 20 Mbit/s | The ISP's contracted rate | Lets QoS be demonstrated on fast virtual links |
| Private AS 65000, documentation prefixes (RFC 5737 / RFC 3849) | TVI's public AS and provider-assigned space | NAT hides everything; nothing is advertised to the ISPs |
| IKEv1 + pre-shared keys (GRE over IPsec), local AAA | IKEv2 + certificates, TACACS+/RADIUS | Lab simplicity; listed in the roadmap |
| VPCS hosts | PCs, phones, sensors | Only ping/trace/DHCP |
