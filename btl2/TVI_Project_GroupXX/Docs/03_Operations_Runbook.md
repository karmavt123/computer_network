# Operations runbook

Day-2 procedures for the TVI network as built in this repository. Every procedure names the file to change,
the command to run and the check that proves it worked. Device names and addresses are the ones in
`Design/IP_Schema.md`.

**Golden rule.** The data model `Tools/tvi_model.py` is the source of truth. A change made only on a device
is overwritten by the next regeneration and is invisible to the documentation. Change the model (or the
automation payload), regenerate, test, then deploy.

```text
edit Tools/tvi_model.py
python3 Tools/gen_configs.py && python3 Tools/gen_docs.py && python3 Tools/gen_walkthrough.py && python3 Tools/gen_latex.py
python3 -m unittest Tools.test_model Tools.test_configs      # must be OK before anything is deployed
git diff Configurations/                                     # review exactly what will change on which device
```

## 1. Access and credentials

| Item | Value |
|---|---|
| Management source | AUTO-HOST `10.1.99.100` (VLAN 99, HQ). The VTY ACL accepts only `10.1.99.0/24`, `10.2.99.0/24`, `10.3.99.0/24`. |
| Protocol | SSH v2 only (`transport input ssh`) |
| Account | `netadmin` (privilege 15); password chosen when the configs were generated (`TVI_LAB_PASSWORD`) |
| Automation credentials | environment variables `TVI_SSH_USER`, `TVI_SSH_PASS`, `TVI_ENABLE_PASS`, or an interactive prompt. Never in a file. |
| Device addresses | switches: VLAN 99 SVI; routers: Loopback0 (`Automation/inventory.yaml`) |

## 2. Routine procedures

### 2.1 Back up every device (read-only)

```bash
cd Automation
python3 auto_provision.py --backup-only
ls backups/            # <hostname>_<YYYYmmdd_HHMMSS>_backup.cfg, one per device
tail logs/automation.log
```

Each file holds `show running-config` and `show ip interface brief`. A device that cannot be reached is
logged (`Connection timeout` / `Authentication failed`) and the run continues with the next one; the exit
code is non-zero if any device failed.

### 2.2 Add a VLAN everywhere (the fast path)

For a VLAN that only has to *exist* on the switches (the automation task of the brief):

```bash
python3 auto_provision.py --roles core,access --vlan 110:LAB_DEMO --dry-run   # look first
python3 auto_provision.py --roles core,access --vlan 110:LAB_DEMO --save
```

The script captures the state, creates the VLAN, verifies it with `show vlan brief`, captures again and
writes `logs/<host>_<time>_change.diff` with the lines that changed. To make the VLAN permanent add it to
`Automation/payload.yaml` (`vlans.extended`).

### 2.3 Add a routed user VLAN (gateway, DHCP, ACL)

A VLAN that needs a gateway is a design change - do it in the model so that the SVI, the HSRP group, the DHCP
pool, the relay, the ACL, the IP plan and the diagrams all follow:

1. `Tools/tvi_model.py`: add the id to `VLAN_NAMES` / `VLAN_PURPOSE` / `ZONE_OF`, and a subnet in
   `SITE_VLANS[<site>]` taken from that site's *reserved* blocks (`SITE_RESERVED`). Keep it power-of-two aligned.
2. Regenerate and run the tests (the overlap and capacity tests catch a bad subnet).
3. Deploy the changed files. In the lab: paste the changed sections; on a running network: generate the
   difference with `git diff` and push those lines.
4. Verify: `show standby brief` (new group, complementary roles), a client gets a lease, `show ip dhcp binding`.

### 2.4 Add an access switch at HQ

1. Model: `_dev("HCMC-ACC-SW03", "HQ", "access", "IOSvL2", mgmt=host("HQ", 99, 13), ...)` and two `LINKS`
   entries (one trunk to each core), plus access-port entries for its hosts.
2. Regenerate; the new `HCMC-ACC-SW03.cfg`, the core trunk ports (with root guard), the inventory and the
   wiring table are produced.
3. Cable, paste, generate the RSA key, then `python3 auto_provision.py --devices HCMC-ACC-SW03 --save`.
4. Verify on the new switch: `show spanning-tree summary` (one uplink forwarding, one alternate per VLAN group),
   `show ip dhcp snooping`.

### 2.5 Add a branch site

1. Model: new entry in `SITES` (next `/16` and `/48`), its `SITE_VLANS`, two edge routers and a switch in
   `DEVICES`, WAN `/30`s in `WAN`, two tunnels in `TUNNELS` (one per ISP, hub = HQ edges).
2. Regenerate: the hub edges get the new tunnels, ISAKMP keys and route-reflector clients; the prefix-list
   `PL-TVI-AGG` gets the new aggregate on every edge; DHCP pools appear on the DC server.
3. Deploy the hub changes first (adding a neighbor does not disturb existing sessions), then the branch.
4. Verify: `show ip bgp summary` on both hubs, `show crypto isakmp sa`, a branch PC gets a DHCP lease.

### 2.6 Rotate a shared key (IPsec PSK, BGP MD5, HSRP)

```bash
export IPSEC_PSK='new-value'        # or BGP_KEY / HSRP_KEY
python3 Tools/gen_configs.py
```

Both ends of a session must change together. Do one tunnel (or one HSRP group) at a time, backup path first:
change both ends of the ISP-B tunnels, check `show crypto isakmp sa`, then the ISP-A tunnels. A BGP password
change resets the session; an HSRP key mismatch makes both routers active (dual-active) until both sides match.

### 2.7 Planned maintenance of a core switch (no user impact beyond ~2 s)

1. On the switch to be serviced: `interface range vlan 10,30,50,99,100` -> `standby <group> priority 90`
   (below the peer's 100). The peer preempts immediately; check `show standby brief` on the peer.
2. `router ospf 10` -> `max-metric router-lsa` to move transit traffic away.
3. Do the work / reload.
4. Remove `max-metric`, restore the priorities (or reload the startup-config). The switch reclaims its
   groups after the 60 s preempt delay.

### 2.8 Restore a device

Paste the startup-config from `Configurations/` (or the newest file in `Automation/backups/`), then:

```text
crypto key generate rsa general-keys modulus 2048
copy running-config startup-config
```

## 3. Expected state (what "healthy" looks like)

| Check | Command | Healthy |
|---|---|---|
| Core bundle | `show etherchannel summary` | `Po1(SU)`, both members `(P)` |
| Spanning tree | `show spanning-tree root` | each core is root for its VLAN group |
| Gateways | `show standby brief` | one Active and one Standby per group; owner as in the HSRP table of the IP plan |
| HQ OSPF | `show ip ospf neighbor` | core: peer core + 2 edges; edge: 2 cores + the other edge - all FULL |
| Default route | `show ip route 0.0.0.0` on a core | via HCMC-EDGE-RTR01 (`O*E1`) |
| BGP at HQ | `show ip bgp summary` on an HQ edge | ISP, the other edge and two branches Established |
| BGP at a branch | `show ip bgp summary` | ISP and one hub Established |
| Tunnels | `show crypto isakmp sa` | `QM_IDLE` to every peer |
| DHCP | `show ip dhcp binding` on HCMC-DC-SRV01 | leases from all three sites |
| Time | `show ntp associations` | synchronised to `10.1.100.10` |

`python3 Lab/collect_evidence.py` runs all of these and marks each output PASS or CHECK.

## 4. Troubleshooting

### 4.1 A client gets no DHCP address

1. Access switch: `show ip dhcp snooping` - is snooping on for the VLAN and is the **uplink trusted**?
   `show interfaces status err-disabled` - port security or BPDU guard may have shut the port.
2. Gateway: `show running-config interface <SVI/sub-if> | include helper` -> `10.1.100.10`.
3. Gateway: `ping 10.1.100.10 source <gateway interface>`. From a branch this crosses the tunnel: if it fails,
   go to 4.3.
4. Server: `show ip dhcp pool` (addresses left?), `show ip dhcp conflict`, `debug ip dhcp server packet`.
5. The ACL on the gateway must permit `udp bootpc -> bootps` (first line of the guest and IoT ACLs).

### 4.2 Both routers of an HSRP group are Active

They do not hear each other's hellos. Check, in this order: the VLAN is allowed on every trunk between them
(`show interfaces trunk`), Po1 is up, the authentication string and HSRP version match
(`show standby <if> <group>`), no ACL blocks `udp/1985` to `224.0.0.102`.

### 4.3 A branch cannot reach HQ

| Step | Command (branch edge) | If it fails |
|---|---|---|
| 1 | `ping <ISP address> ` (the `/30` peer) | physical/WAN problem |
| 2 | `show ip bgp summary` - ISP session | check `show ip route 0.0.0.0`; no default = no tunnel |
| 3 | `ping <hub WAN address> source Gi0/0` | ISP routing |
| 4 | `show crypto isakmp sa` | not `QM_IDLE`: PSK mismatch (`debug crypto isakmp`), or UDP/500 blocked |
| 5 | `show interface Tunnel<n>` | down: IPsec profile / source interface |
| 6 | `show ip bgp summary` - hub session | Idle/Active: BGP password, tunnel addressing |
| 7 | `show ip route 10.1.0.0` | present = control plane fine; look at ACLs / firewall zones |

`show track 1` tells you whether the gateway role has already moved to the other router.

### 4.4 HQ reaches the Internet but not a branch (or the reverse)

`show ip bgp 10.2.0.0` on **both** HQ edges. Each must have a path whose next hop is reachable:
a tunnel peer (`10.255.1.x`) or the other edge's loopback, reached over `Gi0/3`. If the next hop is the other
edge and `show ip route <its loopback>` points at a core instead of `Gi0/3`, the edge-to-edge link is down -
traffic will loop between the core and the edge (see `Docs/05_Validation.md`, scenario S9).

### 4.5 OSPF adjacency stuck

`show ip ospf interface brief`: is the interface listed and not passive? Both ends must agree on the network
type (`point-to-point` on the routed links and VLAN 90), the area, and the MTU.

### 4.6 Voice quality complaints

`show policy-map interface Gi0/0 output` on the edge of the affected site: drops in `CM-VOICE` mean more
voice than the priority queue allows (raise the percentage or the circuit); packets counted only in
`class-default` mean the marking is wrong - check `show policy-map interface <LAN if> input`.

## 5. Changing names before submission

Device names, VLAN names, subnets and keys all live in `Tools/tvi_model.py`; regenerate and everything that
is generated follows (configs, IP plan, diagrams, inventory, wiring table, evidence plan, LaTeX tables and
snippets, the walkthrough). Hand-written files that mention names literally and must be searched and replaced:
`Lab/EVE-NG_Setup.md`, `Lab/Test_Plan.md`, `Lab/Failover_Demo_Script.md`, `Docs/01`, `Docs/03`-`Docs/05`,
the `VI_*` guide and the report sections.
