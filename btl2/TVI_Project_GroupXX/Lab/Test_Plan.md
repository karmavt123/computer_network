# Verification & test plan (maps 1:1 to the 10-step rubric)

Use this as your **evidence checklist**: run every command in the lab, take a screenshot (or paste the text) and
name it as in the "Evidence" column. The report template (`../Report/`) has a slot for each file.
Outputs are described, not reproduced - what you paste into the report must be **your** lab's real output.

| Prefix | Meaning |
|---|---|
| `[PASS]` | what a correct lab shows |
| `[FAIL-EXPECTED]` | a test that **must be blocked** (security policy) - the failure is the proof |

## Step 3 - Layer 2: VLANs, trunks, EtherChannel, RSTP root enforcement

| # | Device | Command | [PASS] criteria | Evidence |
|---|---|---|---|---|
| 3.1 | HCMC-CORE-SW01 | `show vlan brief` | VLANs 10,20,30,40,50,60,90,99,100,998,999 with names | `s3_1_vlans.png` |
| 3.2 | HCMC-CORE-SW01 | `show interfaces trunk` | Po1 + Gi0/2, Gi0/3 trunking, native 998, allowed list as designed | `s3_2_trunks.png` |
| 3.3 | HCMC-CORE-SW01 | `show etherchannel summary` | `Po1(SU)`, members `Gi0/0(P) Gi0/1(P)`, protocol LACP | `s3_3_po1.png` |
| 3.4 | HCMC-CORE-SW01 | `show spanning-tree root` | "This bridge is the root" for VLAN 10,30,50,90,99,100 (priority 4096+VLAN) | `s3_4_root_sw01.png` |
| 3.5 | HCMC-CORE-SW02 | `show spanning-tree root` | root for VLAN 20,40,60 | `s3_5_root_sw02.png` |
| 3.6 | HCMC-ACC-SW01 | `show spanning-tree vlan 10` | root port towards CORE-SW01, the other uplink *Altn BLK* | `s3_6_acc_stp.png` |
| 3.7 | HCMC-CORE-SW01 | `show spanning-tree inconsistentports` after temporarily raising ACC priority below 4096 (optional) | root-inconsistent on the core->access port (root guard) | `s3_7_rootguard.png` |

## Step 4 - FHRP and centralised IP services

| # | Device | Command | [PASS] criteria | Evidence |
|---|---|---|---|---|
| 4.1 | CORE-SW01 / SW02 | `show standby brief` | complementary Active/Standby, priority 110/100, `P` (preempt) flag | `s4_1_hsrp.png` |
| 4.2 | DC-SRV01 | `show ip dhcp pool` / `show ip dhcp binding` | leases for all VLANs incl. **Hanoi and Da Nang** clients (relay across tunnel) | `s4_2_dhcp.png` |
| 4.3 | each VPCS | `ip dhcp` then `show ip` | address inside the VLAN pool, gateway = HSRP VIP (.1) | `s4_3_vpcs_<name>.png` |
| 4.4 | PC-ENG01 | `ping 10.1.4.1` (VIP) and `ping 10.1.100.10` | replies | `s4_4_ping_vip.png` |
| 4.5 | CORE-SW01 | `show ip dhcp relay` is not available on all images; use `show running-config interface vlan 20 \| include helper` | `ip helper-address 10.1.100.10` | `s4_5_helper.png` |
| 4.6 | DC-SRV01 | `show ntp status` ; from any router `show ntp associations` | synchronised to 10.1.100.10 (allow a few minutes) | `s4_6_ntp.png` |
| 4.7 | PC-ENG01 | `ping dc-srv01.tvi.local` after setting `ip dns 10.1.100.10` on the VPCS | resolves 10.1.100.10 | `s4_7_dns.png` |

## Step 5 - Underlay (OSPF / OSPFv3)

| # | Device | Command | [PASS] criteria | Evidence |
|---|---|---|---|---|
| 5.1 | CORE-SW01 | `show ip ospf neighbor` | FULL with CORE-SW02 (Vlan90), EDGE-RTR01, EDGE-RTR02 | `s5_1_ospf_nbr.png` |
| 5.2 | CORE-SW01 | `show ip route ospf` | O*E1 0.0.0.0/0 via 10.1.250.2 (metric 10 + cost); loopbacks of the other devices | `s5_2_ospf_routes.png` |
| 5.3 | CORE-SW01 | `show ipv6 ospf neighbor` / `show ipv6 route ospf` | same adjacencies for IPv6; `::/0` from edges | `s5_3_ospfv3.png` |
| 5.4 | CORE-SW01 | `ping 10.1.255.11 source Loopback0` and `.12`, `.2` | success - **all loopbacks reachable inside the site** | `s5_4_loopbacks_hq.png` |
| 5.5 | HANOI-EDGE-RTR01 | `show ip ospf neighbor`, `ping 10.2.255.2 source Loopback0` | FULL with RTR02 on Gi0/1.99; ping ok | `s5_5_hanoi.png` |
| 5.6 | DANANG-EDGE-RTR01 | same as 5.5 with `10.3.255.2` | ok | `s5_6_danang.png` |

## Step 6 - Overlay (eBGP to ISPs + IPsec + iBGP)

| # | Device | Command | [PASS] criteria | Evidence |
|---|---|---|---|---|
| 6.1 | HCMC-EDGE-RTR01 | `show ip bgp summary` | ISP-A Established (1 prefix = default); iBGP to RTR02, Hanoi-RTR01, Da Nang-RTR01 Established | `s6_1_bgp_sum.png` |
| 6.2 | HCMC-EDGE-RTR01 | `show crypto isakmp sa` ; `show crypto ipsec sa \| include encaps\|decaps` | `QM_IDLE`, packet counters increasing | `s6_2_ipsec.png` |
| 6.3 | HCMC-EDGE-RTR01 | `show ip bgp` | 10.2.0.0/16 and 10.3.0.0/16 learned from clients, `0.0.0.0/0` from ISP-A | `s6_3_bgp_table.png` |
| 6.4 | HANOI-EDGE-RTR01 | `show ip route bgp` | 10.1.0.0/16 and 10.3.0.0/16 via Tunnel1 next-hop 10.255.1.1 | `s6_4_hanoi_routes.png` |
| 6.5 | HANOI-EDGE-RTR01 | `show bgp ipv6 unicast summary` ; `ping 2001:db8:1:ffff::11` | v6 sessions up; reply | `s6_5_bgp_v6.png` |
| 6.6 | PC-HN-ENG01 | `ping 10.1.100.10` ; `trace 10.1.100.10` | reply; first hops = Hanoi VIP -> tunnel (no ISP hops visible: encrypted) | `s6_6_e2e_hn_hq.png` |
| 6.7 | PC-DN-LOG01 | `ping <PC-HN-ENG01 address>` | reply (path DN -> HQ hub -> Hanoi) | `s6_7_dn_hn.png` |
| 6.8 | ISP-A-RTR | `show ip bgp summary` | 3 TVI customers Established, INTERNET Established | `s6_8_isp.png` |

## Step 7 - Security and hardening

**ACL / segmentation matrix** (VPCS `ping`; run each from the source):

| # | Source -> Destination | Expected | Why |
|---|---|---|---|
| 7.1 | PC-EXEC01 -> PC-ENG01 | `[PASS]` | EXEC may initiate; ENG ACL allows echo-reply back |
| 7.2 | PC-ENG01 -> PC-EXEC01 | `[FAIL-EXPECTED]` | nobody may initiate towards V10 |
| 7.3 | PC-GUEST01 -> 8.8.8.8 | `[PASS]` | Internet only (NAT overload on edge) |
| 7.4 | PC-GUEST01 -> 10.1.100.10 / PC-ENG01 | `[FAIL-EXPECTED]` | RFC1918 denied for guests |
| 7.5 | PC-ENG01 -> 8.8.8.8 | `[PASS]` | corporate Internet access |
| 7.6 | PC-HN-ENG01 -> 8.8.8.8 | `[PASS]` | **local breakout** - `show ip nat translations` on HANOI-EDGE shows it; the HQ edge shows nothing |
| 7.7 | PC-DN-IOT01 -> 10.1.100.10 | `[PASS]` | IoT diagnostics to the infrastructure server only |
| 7.8 | PC-DN-IOT01 -> PC-ENG01, 8.8.8.8, any peer | `[FAIL-EXPECTED]` | IoT is isolated |
| 7.9 | PC-DN-LOG01 -> PC-DN-IOT01 | `[PASS]` | logistics ops may manage IoT |
| 7.10 | PC-HN-ENG01 -> AUTO-HOST (10.1.99.100) | `[FAIL-EXPECTED]` | management VLAN not reachable from users |
| 7.11 | AUTO-HOST -> `ssh netadmin@10.2.255.1` | `[PASS]` | VTY ACL allows 10.x.99.0/24 |
| 7.12 | PC-ENG01 -> `ssh` to any switch (use AUTO-HOST rather) | `[FAIL-EXPECTED]` | VTY-MGMT denies |

Evidence: `s7_acl_matrix.png` (one screenshot per group), `show access-lists` counters on the SVI ACL (hit counts prove the deny hit).

**NAT / ZBF / Layer-2 security**

| # | Device | Command | [PASS] criteria | Evidence |
|---|---|---|---|---|
| 7.13 | HCMC-EDGE-RTR01 | `show ip nat translations` / `show ip nat statistics` | translations for guest / corporate traffic to 8.8.8.8 | `s7_13_nat.png` |
| 7.14 | HCMC-EDGE-RTR01 | `show zone-pair security` | sessions inspected on ZP-INSIDE-OUTSIDE | `s7_14_zbf.png` |
| 7.15 | HCMC-ACC-SW01 | `show ip dhcp snooping binding` | bindings for PCs (MAC, IP, VLAN, port) | `s7_15_snoop.png` |
| 7.16 | HCMC-ACC-SW01 | `show ip arp inspection vlan 10,20` / `statistics` | DAI enabled; forwarded counters; drops if you spoof | `s7_16_dai.png` |
| 7.17 | HCMC-ACC-SW01 | `show port-security interface gi0/2` | sticky MAC learned, violation mode restrict | `s7_17_portsec.png` |
| 7.18 | PC-GUEST01 (or a rogue router) | start a second DHCP server on an access port (optional) | snooping drops the offers (`show ip dhcp snooping statistics`) | `s7_18_rogue.png` |

## Step 8 - QoS

DSCP/TOS values: EF=46 -> `tos 184`, AF31=26 -> `tos 104`, CS3=24 -> `tos 96`.

| # | Device | Command | [PASS] criteria | Evidence |
|---|---|---|---|---|
| 8.1 | HCMC-EDGE-RTR01 | `show policy-map interface GigabitEthernet0/0 output` (baseline) | class counters at ~0 | `s8_1_before.png` |
| 8.2 | HCMC-EDGE-RTR01 | `ping 10.2.255.1 source Loopback0 repeat 500 size 160 tos 184` | CM-VOICE `packets matched` increases (priority queue) | `s8_2_voice.png` |
| 8.3 | same | `ping ... tos 104 repeat 500` and `tos 96 repeat 500` | CM-VIDEO and CM-SIGNALING counters increase | `s8_3_video_sig.png` |
| 8.4 | same | `ping ... repeat 500 size 1400` (best effort) | class-default increases | `s8_4_default.png` |
| 8.5 | HCMC-EDGE-RTR01 | congestion: start 3 simultaneous large pings (`repeat 5000 size 1400`) then repeat 8.2 | voice RTT stable / no drops in CM-VOICE while class-default shows drops (`show policy-map` -> `total drops`) | `s8_5_congestion.png` |
| 8.6 | HANOI-EDGE-RTR01 | `show policy-map interface GigabitEthernet0/1.40 input` after generating RTP-range UDP from the voice VLAN (optional) | CM-IN-VOICE matches, set dscp ef | `s8_6_mark.png` |

Note: the shaper is 20 Mbit/s (`WAN_RATE_BPS` in the model) so the lab links - which are much faster - are artificially
congested. State this in the report.

## Step 9 - Automation

| # | Where | Command | [PASS] criteria | Evidence |
|---|---|---|---|---|
| 9.1 | AUTO-HOST | `python3 auto_provision.py --dry-run` | prints the commands per device, no connection | `s9_1_dry.png` |
| 9.2 | AUTO-HOST | `python3 auto_provision.py --save` (enter credentials at the getpass prompt) | summary `n/n device(s) OK` | `s9_2_run.png` |
| 9.3 | AUTO-HOST | `ls backups/ ; head backups/HCMC-CORE-SW01_*_backup.cfg` | two files per device (before/after) named `<host>_<timestamp>_backup.cfg` | `s9_3_backups.png` |
| 9.4 | AUTO-HOST | `tail logs/automation.log` | successes **and** error lines (see 9.6) | `s9_4_log.png` |
| 9.5 | AUTO-HOST | `python3 auto_provision.py --roles core --vlan 110:LAB_DEMO --save` then `show vlan brief \| include 110` on the core | VLAN 110 LAB_DEMO exists | `s9_5_newvlan.png` |
| 9.6 | AUTO-HOST | shut one switch's SVI99 or stop its VM, rerun; or type a wrong password | `NetmikoTimeoutException` / `NetmikoAuthenticationException` logged, run continues | `s9_6_errors.png` |
| 9.7 | AUTO-HOST | `python3 -m unittest -v Automation.tests.test_auto_provision` | 7 tests OK | `s9_7_tests.png` |

## Step 10 - End-to-end and failover

Run the full connectivity matrix once (all `[PASS]` pings between sites, trace route), then the failover scenarios in
`Failover_Demo_Script.md` and record the **measured** convergence times (number of lost pings x 1 s) for the report.
If the report claims a failover, the live demo must show the same - rehearse it.

| # | Scenario | Evidence |
|---|---|---|
| 10.1 | continuous `ping -t`/`ping` PC-ENG01 -> PC-HN-ENG01 while shutting a LACP member | `s10_1_lacp.png` |
| 10.2 | core node failure (all CORE-SW01 interfaces down) | `s10_2_core.png` |
| 10.3 | ISP-A failure at HQ (EDGE-RTR01 Gi0/0 shut) | `s10_3_isp.png` |
| 10.4 | Hanoi tunnel failure | `s10_4_hanoi.png` |
