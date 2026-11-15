#!/usr/bin/env python3
"""
gen_walkthrough.py - write Docs/02_Config_Walkthrough.md: every building block of the generated startup-configs,
shown with the *current* lines from Configurations/ and explained (what / why for TVI / how to verify / what
breaks without it).  The snippets are cut from the real files at generation time, so the document cannot
show a command that is not in the lab.

    python3 Tools/gen_walkthrough.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import tvi_model as m                       # noqa: E402
from gen_latex import cfg_block             # noqa: E402

B = []      # (section, title, device, snippet, what, why, verify, without)


def blk(section, title, dev, snippet, what, why, verify, without):
    B.append((section, title, dev, snippet, what, why, verify, without))


# ------------------------------------------------------------------ 1. every device
blk("1. Baseline on every device", "Management-plane hardening", m.ACC1,
    cfg_block(m.ACC1, r"^service timestamps debug", r"^no ip routing|^vtp mode", max_lines=50),
    "Timestamps on logs, encrypted password storage, no HTTP server, AAA with a local privileged user (type-5 hash), "
    "NTP client, syslog to the automation host, legal banner, SSH v2 only, and a VTY access-class that admits only the "
    "three management VLANs (`10.x.99.0/24`). The console uses its own method list (`CONSOLE none`) so a lab console "
    "is never locked out by an AAA mistake.",
    "TVI's brief lists perimeter-only security as a current weakness. Device access is the first thing to close: "
    "management is reachable only from the management VLAN, only over SSH, and every login is logged with a correct "
    "time (NTP) to one collector. The same block is what `Automation/auto_provision.py` re-asserts, so drift is corrected "
    "by rerunning the script.",
    "`show ip ssh` (version 2.0), `show running-config | section line vty`, `show ntp associations`, `show logging`. "
    "From a user PC: `ssh` to any device must be refused; from AUTO-HOST (10.1.99.100) it must work.",
    "Telnet/HTTP management from any VLAN, unsynchronised log times (useless for incident analysis), and the automation "
    "has no stable way in.")

# ------------------------------------------------------------------ 2. Layer 2
blk("2. Layer 2 - HQ core and access", "VLAN database and deterministic RSTP roots", m.CORE1,
    cfg_block(m.CORE1, r"^spanning-tree mode", r"^track|^!$"),
    "Rapid-PVST+ with the root bridge pinned by priority: CORE-SW01 is root (4096) for one VLAN group and secondary "
    "(8192) for the other; CORE-SW02 is the mirror image. Loop guard is on globally.",
    "The root bridge decides which uplink of each access switch forwards. It is placed on the same switch that is HSRP "
    "active for the VLAN (see 3.1), so a frame is switched to the root and routed there - it never crosses Po1 just to "
    "reach its gateway. Splitting the VLANs between the two cores uses both uplinks of every access switch instead of "
    "leaving half the links idle. RSTP was kept instead of MST because the VLAN count is small; the report explains "
    "when MST becomes the better choice.",
    "`show spanning-tree root` on both cores (each says \"This bridge is the root\" for its group); on an access switch "
    "`show spanning-tree vlan 10` shows one Root port and one Alternate (blocking) port.",
    "The switch with the lowest MAC becomes root - possibly an access switch - and all inter-VLAN traffic takes a "
    "sub-optimal path; a rogue switch could take over the role.")

blk("2. Layer 2 - HQ core and access", "LACP EtherChannel between the cores (Po1)", m.CORE1,
    cfg_block(m.CORE1, r"^interface Port-channel1") + cfg_block(m.CORE1, r"^interface GigabitEthernet0/0"),
    "Two physical links bundled with LACP (`mode active` on both ends) into one logical trunk. Native VLAN 998 carries "
    "no hosts; DTP is disabled (`nonegotiate`).",
    "The brief requires EtherChannel on core links. One Po1 member can fail without a spanning-tree topology change and "
    "without losing the VLAN 90 transit between the cores. LACP (not `mode on`) detects a mis-cabled or one-way member "
    "instead of black-holing traffic on it.",
    "`show etherchannel summary` -> `Po1(SU)` with `Gi0/0(P) Gi0/1(P)`; shut one member: ping keeps running, the "
    "summary shows one (P) and one (D).",
    "A single inter-core link is a single point of failure for every VLAN whose root and HSRP-active roles sit on "
    "different sides after a failure.")

blk("2. Layer 2 - HQ core and access", "Core-to-access trunk with root guard", m.CORE1,
    cfg_block(m.CORE1, r"^interface GigabitEthernet0/2"),
    "802.1Q trunk to an access switch, only the VLANs that exist at the access layer are allowed, and `spanning-tree "
    "guard root` on the core side.",
    "\"Root bridge enforcement\" is an explicit rubric item. Priority alone is not enforcement: a switch plugged in with "
    "priority 0 would win. Root guard puts the port into *root-inconsistent* state the moment a superior BPDU arrives.",
    "`show spanning-tree inconsistentports` (empty in normal operation). Optional proof: set `spanning-tree vlan 10 "
    "priority 0` on an access switch - the core port goes root-inconsistent and recovers when the change is removed.",
    "Any device at the access layer can become root for a VLAN.")

blk("2. Layer 2 - HQ core and access", "Access-switch uplink (trusted) and user port (untrusted)", m.ACC1,
    cfg_block(m.ACC1, r"^ip dhcp snooping$", r"^interface Vlan99") + cfg_block(m.ACC1, r"^interface GigabitEthernet0/0") +
    cfg_block(m.ACC1, r"^interface GigabitEthernet0/2"),
    "DHCP snooping and Dynamic ARP Inspection on the user VLANs; uplinks are *trusted*, user ports are not. A user port "
    "is a fixed access port with PortFast + BPDU guard, port security (2 sticky MACs, `restrict`) and broadcast "
    "storm-control. Option 82 insertion is disabled because the relay is the L3 gateway, not this switch.",
    "The brief names broadcast storms and weak internal security as current problems, and the rubric asks for DHCP "
    "snooping/DAI. Snooping drops DHCP server messages arriving on user ports (rogue DHCP), and builds the binding "
    "table that DAI uses to drop spoofed ARP replies (man-in-the-middle). Port security limits MAC flooding; BPDU guard "
    "shuts a port where someone connects a switch; storm-control caps a broadcast storm at 5 % of the port.",
    "`show ip dhcp snooping binding` (one line per PC after `ip dhcp`), `show ip arp inspection vlan 10`, "
    "`show port-security interface gi0/2`, `show storm-control`.",
    "A PC running a DHCP server or ARP spoofing tool can redirect a whole VLAN through itself.")

# ------------------------------------------------------------------ 3. FHRP and services
blk("3. First-hop redundancy and IP services", "HSRPv2 gateway on a core SVI - active side", m.CORE2,
    cfg_block(m.CORE2, r"^track timer", r"^!$") + cfg_block(m.CORE2, r"^interface Vlan20"),
    "The default gateway of VLAN 20 is the *virtual* address `.1`; this switch owns it (priority 110). DHCP requests are "
    "relayed to the central server (`ip helper-address`), the inbound ACL enforces segmentation, and a second group "
    "(number + 1000 - an HSRP group has one address family) provides the IPv6 virtual gateway with identical "
    "settings. Hellos every 500 ms, hold 1.5 s, MD5 authentication. `preempt delay minimum 60`: "
    "after a reboot the switch waits a minute (routing must converge first) before it takes the role back. Track "
    "object 1 lowers the priority by 30 if the switch has no default route at all.",
    "\"FHRP must be configured for default gateway redundancy\" - and four-nines availability means a core switch may "
    "fail without any host noticing more than a couple of seconds. HSRPv2 (not v1) is required for IPv6 and millisecond "
    "timers. The 30-point decrement is larger than the 10-point priority gap, so tracking really moves the role.",
    "`show standby brief` on both cores: complementary Active/Standby, `P` flag set. `show track`.",
    "Hosts point at one physical switch; when it fails every host in the VLAN loses its gateway until someone "
    "reconfigures them.")

blk("3. First-hop redundancy and IP services", "The same group on the standby side", m.CORE1,
    cfg_block(m.CORE1, r"^interface Vlan20"),
    "Identical except for the real address (`.2`/`.3`), priority 100, and **`preempt` without delay**.",
    "The standby router must take over *immediately* when the active router's tracked priority drops below its own. "
    "With a delay on this side a tracking-driven failover would be postponed by 60 s. (This asymmetry is checked by "
    "`Tools/test_model.py` and `Tools/test_configs.py`.)",
    "Compare `show standby vlan 20` on both switches: same virtual IP, same timers, same authentication.",
    "Tracking-based failover becomes slow or never happens.")

blk("3. First-hop redundancy and IP services", "Branch gateway: router-on-a-stick sub-interface with route tracking",
    m.HN_E1,
    cfg_block(m.HN_E1, r"^track timer", r"^!$") + cfg_block(m.HN_E1, r"^interface GigabitEthernet0/1\.21"),
    "At the branches the two edge routers are the gateways (802.1Q sub-interfaces towards the access switch). The "
    "sub-interface also does NAT (inside), the firewall zone, QoS re-marking and OSPF. Track 1 follows the BGP route "
    "to the HQ aggregate `10.1.0.0/16`; `track timer ip route 1` polls it every second (IOS default: 15 s).",
    "If this router loses its ISP or its tunnel it can no longer reach HQ, but its LAN side is perfectly healthy - "
    "plain HSRP would keep it active and black-hole the VLAN. Tracking the *service* (the HQ route) hands the gateway "
    "to the other router, whose tunnel runs over the other ISP. Active roles are split between the two routers per "
    "VLAN so both ISPs carry traffic in normal operation.",
    "`show track 1`, `show standby brief`. Failover test: shut the WAN interface of the active router - the standby "
    "becomes active for its VLANs within the BGP hold time + 1 s.",
    "A branch ISP failure isolates half of the branch VLANs although a second ISP and router are available.")

blk("3. First-hop redundancy and IP services", "Central DHCP pool and DNS (HQ data centre)", m.DCSRV,
    cfg_block(m.DCSRV, r"^ip dhcp pool P-HANOI-V20", r"^ip dhcp pool P-HANOI-V21") + cfg_block(m.DCSRV, r"^ip dns server", r"^!"),
    "One pool per VLAN per site on a single server at HQ. The default router handed out is the HSRP virtual IP; "
    f"the first {m.DHCP_EXCLUDED} addresses of every subnet are excluded for infrastructure. The same node answers DNS "
    "for `tvi.local` and is the NTP master. (In the lab an IOS router plays the server.)",
    "\"DHCP servers must be centralized at HQ with DHCP Relay at branches.\" The relay sets `giaddr` to the gateway's own "
    "address, which is how the server picks the right pool; the reply returns through the tunnel to that gateway. The "
    "guest pool hands out public DNS only and no internal domain.",
    "On the server: `show ip dhcp binding` lists leases from 10.1.x, 10.2.x and 10.3.x. On a Hanoi PC: `ip dhcp` then "
    "`show ip` (gateway = `.1`).",
    "Each branch needs its own DHCP server to operate and to keep consistent.")

# ------------------------------------------------------------------ 4. Underlay
blk("4. Underlay routing - OSPF / OSPFv3", "OSPF on a core switch", m.CORE1,
    cfg_block(m.CORE1, r"^router ospf 10", r"^router ospfv3") + cfg_block(m.CORE1, r"^router ospfv3", r"^!$"),
    "One OSPF process, area 0, enabled per interface (`ip ospf 10 area 0`). `passive-interface default` means no hello "
    "is ever sent towards users; only the inter-core transit (VLAN 90) and the two routed uplinks form adjacencies. "
    "OSPFv3 with the IPv6 address family mirrors it. Reference bandwidth 10 Gbit/s keeps 1G and 10G links "
    "distinguishable.",
    "The brief prescribes OSPF inside a site. A single area is correct for this size (a handful of routers per site); "
    "areas would add ABRs and summarisation without any benefit. Passive-by-default removes the risk of a rogue OSPF "
    "speaker in a user VLAN.",
    "`show ip ospf neighbor` (FULL with the peer core and both edges), `show ip route ospf` (the default route is "
    "`O*E1`), `show ospfv3 neighbor`.",
    "Without passive-default a PC running a routing daemon can inject routes.")

blk("4. Underlay routing - OSPF / OSPFv3", "Conditional default route from the HQ edge", m.E1,
    cfg_block(m.E1, r"^router ospf 10", r"^router ospfv3"),
    "The edge router advertises `0.0.0.0/0` into OSPF as an external type-1 route with seed metric 10 (EDGE-RTR02 uses "
    "20) - **without** the `always` keyword.",
    "Type 1 adds the internal cost, so the choice between the two edges is deterministic (10 beats 20). Without "
    "`always` the default is advertised only while the router itself has one (learned by eBGP from its ISP): when ISP-A "
    "fails, EDGE-RTR01 withdraws the default and the cores switch to EDGE-RTR02 by themselves.",
    "`show ip route 0.0.0.0` on a core -> via EDGE-RTR01. Shut EDGE-RTR01 Gi0/0: the route changes to EDGE-RTR02 "
    "(metric 20 + cost).",
    "With `always`, the cores would keep sending Internet traffic to an edge that has lost its ISP.")

blk("4. Underlay routing - OSPF / OSPFv3", "Edge-to-edge routed link at HQ", m.E1,
    cfg_block(m.E1, r"^interface GigabitEthernet0/3"),
    "A direct /30 between the two HQ edge routers, in OSPF.",
    "The core switches run no BGP: they only know \"default -> EDGE-RTR01\". If EDGE-RTR01 loses the tunnel to a branch but "
    "keeps its ISP default, BGP tells it to reach the branch through EDGE-RTR02. Without this link the only path "
    "between the edges is through a core - which looks up the branch address, finds only the default route, and sends "
    "the packet straight back: a routing loop. With the link, the iBGP next hop (EDGE-RTR02's loopback) is one hop away "
    "and never transits the core. The FRR emulation reproduces both cases (scenario S2 with the link, S9 without).",
    "`show ip ospf neighbor` on an edge lists the other edge on Gi0/3; `show ip route 10.1.255.12` -> via Gi0/3.",
    "A single branch-ISP failure cuts HQ off from that branch although a backup tunnel is up.")

# ------------------------------------------------------------------ 5. Overlay
blk("5. WAN overlay - IPsec and BGP", "GRE tunnel protected by IPsec (hub side)", m.E1,
    cfg_block(m.E1, r"^crypto isakmp policy", r"^interface Tunnel") + cfg_block(m.E1, r"^interface Tunnel1"),
    "IKEv1 with a pre-shared key per peer (AES-256, SHA-256, DH group 14), an IPsec profile in transport mode, and one "
    "GRE tunnel interface per branch protected by that profile. The tunnel is a routed /30 (+ an IPv6 /64), so BGP "
    "simply runs across it; dead-peer detection is enabled. MTU/MSS are lowered to leave room for the GRE and ESP "
    "headers.",
    "The brief asks for IPsec tunnels over the dual-ISP underlay, and for dual-stack. A tunnel *interface* (rather than "
    "a crypto map) gives routing protocols, QoS and the firewall zone something to attach to. GRE is used inside IPsec "
    "because one GRE tunnel carries IPv4 and IPv6 (and multicast); an IKEv1 `tunnel mode ipsec ipv4` VTI carries IPv4 "
    "only, which would leave the IPv6 BGP sessions down. HQ is the hub because the data centre is there; four tunnels "
    "(2 branches x 2 ISPs) instead of a full mesh.",
    "`show crypto isakmp sa` (QM_IDLE), `show crypto ipsec sa | include encaps|decaps` (counters grow), "
    "`show interface Tunnel1` (Tunnel protocol/transport GRE/IP, protection IPsec), `ping ipv6` across the tunnel.",
    "Inter-site traffic would cross the Internet in clear text - unacceptable for Executive/Finance data.")

blk("5. WAN overlay - IPsec and BGP", "BGP on the HQ hub (route reflector)", m.E1,
    cfg_block(m.E1, r"^ip prefix-list PL-ISP-IN", r"^ address-family ipv6", max_lines=80),
    "eBGP to the ISP: accept only a default route, advertise nothing (the site uses private addresses behind NAT). "
    "iBGP to the other HQ edge (loopback to loopback) and to each branch over its tunnel. The branches are "
    "route-reflector clients. `next-hop-self all` rewrites the next hop even on reflected routes. Hold time 9 s on every "
    "session. The site aggregate is originated with a `network` statement anchored by a static route to Null0.",
    "BGP is prescribed for inter-site and ISP routing. Route reflection removes the need for a full iBGP mesh between "
    "six edge routers. Only three aggregates exist enterprise-wide, so the BGP table stays tiny however many VLANs are "
    "added. The 3/9 s timers bound failure detection to 9 s; they are also needed on the edge-to-edge session because "
    "the peer's loopback lies inside the Null0 summary, so next-hop tracking alone would keep a dead peer's routes "
    "(found with the FRR emulation, scenario S3).",
    "`show ip bgp summary` (all Established), `show ip bgp` (`10.2.0.0/16` and `10.3.0.0/16` with local-pref 200 via "
    "the tunnels), `show ip bgp neighbors <branch> advertised-routes`.",
    "No dynamic inter-site routing; static routes would not fail over.")

blk("5. WAN overlay - IPsec and BGP", "BGP on a branch (spoke)", m.HN_E1,
    cfg_block(m.HN_E1, r"^route-map RM-LOCALPREF", r"^ address-family ipv6", max_lines=60),
    "Same policy towards the ISP. One iBGP session to the hub over the tunnel; the branch tags what it advertises with "
    "local-preference 200 on the ISP-A router and 150 on the ISP-B router.",
    "Local preference is carried through the whole AS, so every HQ router agrees that the ISP-A tunnel is the primary "
    "path to this branch and the ISP-B tunnel the backup - deterministic and symmetric where it matters.",
    "On HQ: `show ip bgp 10.2.0.0` shows two paths, best = local-pref 200.",
    "Path selection between the two tunnels falls back to router-ID tie-breaking.")

blk("5. WAN overlay - IPsec and BGP", "Summary anchor and NAT overload (local Internet breakout)", m.HN_E1,
    cfg_block(m.HN_E1, r"^ip route 10\.", r"^!$") + cfg_block(m.HN_E1, r"^ip access-list extended ACL-NAT"),
    "The Null0 static makes the /16 exist in the routing table (BGP only originates what is there) and discards "
    "traffic for unused parts of the block. NAT overload translates private sources to the WAN address - except "
    "corporate-to-corporate traffic, which must use the tunnel.",
    "\"Direct cloud access from branch offices without backhauling through HQ\": every edge router has its own default "
    "route from its own ISP and its own NAT, so SaaS traffic leaves at the branch. Denying 10/8-to-10/8 in the NAT ACL "
    "means inter-site traffic fails closed (it is dropped, not leaked to the Internet) if the VPN is down.",
    "From a Hanoi PC: `ping 8.8.8.8`; on HANOI-EDGE: `show ip nat translations` shows the entry, the HQ edge shows none; "
    "`trace 8.8.8.8` has no HQ hop.",
    "All branch Internet traffic would hairpin through HQ (latency, HQ bandwidth, single dependency).")

# ------------------------------------------------------------------ 6. Security
blk("6. Security policy", "Segmentation ACLs: guests and IoT", m.CORE1,
    cfg_block(m.CORE1, r"^ip access-list extended ACL-GUEST-IN", r"^ip access-list extended ACL-IOT-IN") +
    cfg_block(m.CORE1, r"^ip access-list extended ACL-IOT-IN", r"^!$|^ipv6 access-list"),
    "Inbound ACLs on the gateway interface of each VLAN. Guests: DHCP and HSRP hellos, then *deny every private "
    "destination*, then permit the rest (= Internet). IoT: DHCP, DNS/NTP and ping to the infrastructure server, "
    "MQTT-over-TLS and HTTPS to the IoT platform, replies to sessions opened from outside, then deny and log.",
    "The rubric item is \"ACLs isolating Guest from Corp\"; the brief adds IoT. Filtering *inbound at the first L3 hop* "
    "stops unwanted traffic before it is routed anywhere. IoT devices are the least patchable systems on the network, "
    "so they get an allow-list: they can talk to their platform and nothing else - no lateral movement, no Internet.",
    "Guest PC: `ping 8.8.8.8` works, `ping 10.1.100.10` fails. `show access-lists ACL-GUEST-IN` - the deny counters "
    "increase. IoT PC: only `ping 10.1.100.10` works.",
    "One compromised sensor or visitor laptop can reach finance systems.")

blk("6. Security policy", "Zone-based firewall on the edge", m.E1,
    cfg_block(m.E1, r"^zone security INSIDE", r"^! ---- IPsec"),
    "Three zones: INSIDE (LAN side), OUTSIDE (ISP), VPN (tunnels). INSIDE->OUTSIDE is *inspected* (stateful: only "
    "replies come back). OUTSIDE->self permits exactly what the router needs (IKE, ESP, BGP, ICMP diagnostics). "
    "INSIDE<->VPN is *passed* statelessly.",
    "This is the lab stand-in for the NGFW at the edge. Nothing initiated from the Internet reaches the LAN. The VPN "
    "pairs are stateless on purpose: with two edges and two tunnels, a flow may leave through one edge and return "
    "through the other; a stateful rule would drop the returning half.",
    "`show zone-pair security`, `show policy-map type inspect zone-pair sessions` while a PC pings 8.8.8.8.",
    "The edge router forwards unsolicited Internet traffic to any translated session and exposes its own services.")

# ------------------------------------------------------------------ 7. QoS
blk("7. Quality of service", "Classification, re-marking and the WAN queueing policy", m.E1,
    cfg_block(m.E1, r"^ip access-list extended QOS-VOICE-RTP", r"^! ---- Segmentation|^!$", max_lines=70) +
    cfg_block(m.E1, r"^interface GigabitEthernet0/0"),
    "Inbound (`PM-MARK-IN`, on LAN-facing interfaces): voice RTP from the voice VLANs -> EF, video -> AF31, signalling "
    "-> CS3, everything else reset to 0. Outbound on the WAN (`PM-WAN-PARENT`): shape to the contracted rate, and "
    "inside the shaper give voice a strict-priority queue (20 %), video 25 %, signalling 5 %, network control 5 %, the "
    "rest fair-queued.",
    "\"Voice traffic must be prioritized over all WAN links.\" Marking is decided by the network (source VLAN + ports), "
    "never by the endpoint, so a PC cannot promote itself. The shaper is essential: an Ethernet hand-off never "
    "congests at the interface, so without shaping to the circuit rate the queues would never form and the ISP would "
    "drop packets blindly. IPsec copies the inner DSCP to the outer header, so the policy still sees the marking after "
    "encryption.",
    "`show policy-map interface gi0/0 output` before and after `ping <branch loopback> source lo0 repeat 300 tos 184` - "
    "the `CM-VOICE` counters grow; under load only `class-default` shows drops.",
    "Voice competes with bulk transfers on the WAN: jitter and loss during every busy period.")

SECTION_INTRO = {
    "1. Baseline on every device": "Emitted by `baseline()` in `Tools/gen_configs.py` for all 16 devices.",
    "2. Layer 2 - HQ core and access": "Rubric step 3. HQ has a real switched campus; the branches use one access "
                                       "switch dual-homed to the two edge routers.",
    "3. First-hop redundancy and IP services": "Rubric step 4.",
    "4. Underlay routing - OSPF / OSPFv3": "Rubric step 5. Each site is its own OSPF domain (area 0); sites never "
                                           "exchange OSPF routes with each other.",
    "5. WAN overlay - IPsec and BGP": "Rubric step 6.",
    "6. Security policy": "Rubric step 7. Layer-2 security is in section 2.",
    "7. Quality of service": "Rubric step 8.",
}


def main():
    out = ["# Configuration walkthrough", "",
           "> Generated by `Tools/gen_walkthrough.py`. The snippets are cut from the current files in `Configurations/`; "
           "the explanations are maintained in the generator. Regenerate after changing the model.", "",
           "How to read this: every block has **What** (what the lines do), **Why** (the requirement in the TVI brief it "
           "answers), **Verify** (the command that proves it in the lab) and **Without it** (the failure it prevents). "
           "If you can explain these four points for every block you can defend the design.", ""]
    cur = None
    n = 0
    for (section, title, dev, snippet, what, why, verify, without) in B:
        if section != cur:
            cur = section
            n = 0
            out += [f"## {section}", "", SECTION_INTRO[section], ""]
        n += 1
        out += [f"### {section.split('.')[0]}.{n} {title}", "", f"From `{dev}.cfg`:", "", "```text", snippet.rstrip(), "```", "",
                f"**What.** {what}", "", f"**Why.** {why}", "", f"**Verify.** {verify}", "", f"**Without it.** {without}", ""]
    out += ["## 8. After pasting a config", "",
            "Every file ends with two exec-mode commands that cannot live in a startup-config:", "",
            "```text", "crypto key generate rsa general-keys modulus 2048", "copy running-config startup-config", "```", "",
            "The RSA key is what turns SSH on; without it the automation cannot connect."]
    (ROOT / "Docs").mkdir(exist_ok=True)
    (ROOT / "Docs/02_Config_Walkthrough.md").write_text("\n".join(out) + "\n")
    print(f"walkthrough: {len(B)} blocks")


if __name__ == "__main__":
    main()
