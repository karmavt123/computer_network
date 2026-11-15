# 15-minute live demo - run sheet

Rehearse twice. Keep a terminal tab open per device you will touch. Everything below is a *command list*; read
the real output from your own lab and say what it proves.

| Time | Segment | What you do / show |
|---|---|---|
| 0:00 - 1:30 | Context | `Design/img/01_wan_overview.png`: 3 sites, dual ISP, IPsec hub-and-spoke, HSRP/LACP at HQ. State the 4 needs: no SPOF, direct breakout, IaC, QoS. |
| 1:30 - 3:30 | Health baseline | `show ip bgp summary` + `show crypto isakmp sa` (HCMC-EDGE-RTR01); `show standby brief` (core); `show etherchannel summary`. |
| 3:30 - 5:00 | End-to-end | PC-ENG01: `ping 10.1.100.10`; PC-HN-ENG01: `ip dhcp`, `ping 10.1.100.10`, `trace`; PC-GUEST01 `ping 8.8.8.8` ok, `ping 10.1.100.10` blocked. |
| 5:00 - 8:00 | **Failover A - core link** | Start continuous ping PC-ENG01 -> PC-HN-ENG01. (1) `HCMC-CORE-SW01(config)# interface gi0/0` `shutdown` -> no packet lost, Po1 shows 1 member. (2) Core node failure: `interface range gi0/2-3, gi1/0-1` + `shutdown` + `interface po1` `shutdown` -> `show standby brief` on SW02 becomes Active for all VLANs; note lost pings. (3) Restore, show HSRP preempt after the 60 s delay. |
| 8:00 - 10:30 | **Failover B - ISP/WAN** | `HCMC-EDGE-RTR01(config)# interface gi0/0` `shutdown`. Show: OSPF default now from RTR02 (`show ip route 0.0.0.0` on core, metric 20); tunnels 1/3 go down; The branches notice the dead tunnels after the BGP hold time (9 s): on HANOI-EDGE-RTR01 `show track 1` goes Down, `show standby brief` shows priority 80 and HANOI-EDGE-RTR02 Active for every VLAN; the continuous ping resumes over the ISP-B tunnel. Restore. |
| 10:30 - 13:00 | **Automation** | On AUTO-HOST: `python3 auto_provision.py --roles core,access --vlan 110:LAB_DEMO --save` -> show summary, `ls backups`, `tail logs/automation.log`, `show vlan brief \| include 110`. |
| 13:00 - 14:30 | Security + QoS proof | `show access-lists ACL-GUEST-IN` hit counters; `show policy-map interface gi0/0 output` voice counters after `ping ... tos 184`. |
| 14:30 - 15:00 | Wrap-up | Limits: EVPN-VXLAN design only, ZTNA/SASE design, single access switch per branch in lab. |

## Optional third failover (the most instructive one, 2 minutes)
`HANOI-EDGE-RTR01(config)# interface gi0/0` `shutdown` while PC-ENG01 pings PC-HN-AI01. HQ keeps its Internet, so the
cores still send everything to HCMC-EDGE-RTR01 - which now forwards Hanoi traffic to HCMC-EDGE-RTR02 over the
edge-to-edge link (`show ip bgp 10.2.0.0`, `show ip route 10.1.255.12` -> Gi0/3). Explain that without this link
the packet would loop between the core and the edge.

## Collecting proof while you rehearse
From AUTO-HOST, during each failure: `python3 Lab/collect_evidence.py --only 's10_*' --tag <scenario>` stores the
state of both cores and the edges in `Report/evidence/s10_*_<scenario>.txt` for the report.

## Pre-demo checklist
- [ ] Every node started 10 minutes before, `copy run start` done, `show ip bgp summary` all Established.
- [ ] HSRP preempt delay is 60 s on the designed owner - after restoring a device, wait a minute before the next test.
- [ ] Credentials entered by `getpass` - never paste passwords on screen; clear shell history afterwards.
- [ ] Spare: a saved EVE-NG lab snapshot / the startup-configs to reload if a node hangs.
- [ ] The report's convergence numbers are the ones you *measured*; do not claim anything the demo cannot repeat.
