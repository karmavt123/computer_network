# Routing emulation with FRRouting

`emulate.py` builds the TVI topology out of Linux network namespaces, runs **real OSPF and BGP daemons**
(FRRouting) with configurations translated mechanically from `Configurations/*/*.cfg` (`ios2frr.py`),
then injects failures and measures what end-to-end traffic experiences.

It exists because the startup-configs are generated and, until the EVE-NG lab is built, only statically
checked. Static checks prove that both ends of a session agree; they cannot prove that the *routing logic*
converges and fails over. This does - and it found two design errors that no syntax check would
(see `Docs/01_Design_Rationale.md`, section 5).

**This is not the graded lab.** Its outputs are FRR outputs and must never be presented as IOSv outputs.

## What is and is not emulated

| Emulated faithfully | Approximated | Not emulated |
|---|---|---|
| IPv4 addressing of every L3 interface | GRE/IPsec tunnel -> plain VXLAN point-to-point tunnel over the same underlay path (no encryption) | IOS syntax acceptance on IOSv / IOSvL2 |
| OSPF: areas, network types, passive interfaces, hello/dead timers, conditional default origination | HSRP -> a rule engine that applies the configured priority, preempt, preempt delay (scaled), hold time and track decrements, and moves the virtual IP | IPv6, OSPFv3, MP-BGP IPv6 (kernel of the build host has no IPv6) |
| BGP: eBGP/iBGP sessions, MD5, timers, route reflection, next-hop-self, local preference, prefix-lists, `network` with RIB anchor | L2: each VLAN is one Linux bridge (no trunks, STP, LACP) | ACLs, zone-based firewall, QoS, DHCP/DNS/NTP, DHCP snooping, DAI, port security |
| Static routes incl. Null0 summaries | Hosts use static addresses (first DHCP address of the VLAN) | |
| NAT overload (iptables MASQUERADE from the NAT ACL) | | |

Link model (default): every link goes through a bridge, as in EVE-NG/GNS3. A failed interface or a dead
router leaves the far end up/up, so neighbours must detect the failure with protocol timers - the
conservative case. `--carrier-loss` uses direct cables instead (both ends see the link go down).

## Run

```bash
sudo apt-get install -y frr iproute2 iputils-ping traceroute iptables      # Debian/Ubuntu
sudo python3 Tools/frr_emulation/emulate.py                 # all scenarios, about 8 minutes
sudo python3 Tools/frr_emulation/emulate.py --only S2,S4    # some
sudo python3 Tools/frr_emulation/emulate.py --no-edge-link --only S2    # reproduce the routing loop
sudo python3 Tools/frr_emulation/emulate.py --keep          # leave it running, then e.g.
sudo ip netns exec tvi-hcmc-edge-rtr01 vtysh -N tvi-hcmc-edge-rtr01 -c 'show bgp ipv4 unicast'
sudo python3 Tools/frr_emulation/emulate.py --cleanup
```

Needs Linux with root (namespaces, veth, bridge, vxlan, iptables NAT) and FRR 8 or newer. It runs in a Linux
VM or a privileged container on any laptop, including Apple-silicon Macs that cannot run IOSv.

## Output

| File | Content |
|---|---|
| `results/EMULATION_RESULTS.md` | baseline paths, HSRP owners, one table per scenario (loss, longest outage, path after failure) |
| `results/summary.json` | the same as data (read by `Tools/gen_latex.py` for the report table) |
| `results/<scenario>/<hostname>.txt` | `show bgp`, `show ip route`, `show ip ospf neighbor` of every router after the failure |
| `results/<scenario>/hsrp_active.json` | gateway owner per group after the failure |

In the repository the per-scenario folders of the last run are packed into `results/raw_outputs.zip`.

## Scenarios

`S1`-`S8` are single failures and must pass. `S9` is a deliberate double failure (edge-to-edge link plus a
branch uplink) that reproduces the routing loop the edge-to-edge link was added to prevent; it is expected
to fail and documents the residual risk.

## Reading the numbers

Outage = longest run of lost echo requests (one every 0.2 s). The figures belong to this emulation: FRR
reacts to a local interface failure immediately, IOS adds its own delays (BGP next-hop trigger 5 s, track
polling 1 s). Use them as evidence that the design converges and as an order of magnitude; the report must
quote the values measured in the EVE-NG lab.
