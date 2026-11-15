# Lab build guide - EVE-NG (primary platform)

> Everything below is derived from `Tools/tvi_model.py`. The cabling table is in `wiring_table.md`
> (generated), the startup-configs are in `../Configurations/`.

## 1. What you need

| Item | Notes |
|---|---|
| EVE-NG (Community or Pro) on **x86-64** hardware / VM with VT-x | 16 GB RAM recommended (see budget below). |
| Cisco **IOSv** (router) and **IOSvL2** (switch) images | `vios-adventerprisek9-m` / `viosl2-adventerprisek9-m`, 15.x. You must supply them (licensed). |
| VPCS (built in), one Linux node (any small Linux, e.g. Alpine/Ubuntu server) | Linux node = `AUTO-HOST` (automation + syslog). |

> **Apple-Silicon Macs cannot run EVE-NG/IOSv natively** (x86 images). If your machine is an M-series Mac use a
> Windows/Linux PC, a remote EVE-NG server or university lab server - or ask the instructor for approval to use the
> reduced Packet Tracer variant (`../PacketTracer_Reduced/`).

### RAM budget (approximate - check your image defaults)

| Nodes | Count | RAM each | Total |
|---|---|---|---|
| IOSv routers: 6 edge + DC-SRV + ISP-A + ISP-B + INTERNET | 10 | 512 MB | 5.0 GB |
| IOSvL2: 2 core + 2 access (HQ) + 2 branch switches | 6 | 768 MB | 4.5 GB |
| Linux AUTO-HOST | 1 | 512 MB | 0.5 GB |
| VPCS | 8 | ~0 | - |
| EVE-NG itself | | | ~2 GB |
| **Total** | | | **~12 GB** |

If you run short: start the ISP/INTERNET nodes first, lower IOSv RAM to 384 MB, or start sites one at a time
(HQ + ISPs first, then Hanoi, then Da Nang - this also matches the project timeline).

## 2. Create the lab

1. New lab `TVI`. Add the nodes exactly as named in the table. Set **NICs** (IOSv default is 4 - fine; IOSvL2 needs 8)
   and name every node with the hostname below (the name is also the file name of its config).

| Node (name = hostname) | Template | NICs | Startup-config file |
|---|---|---|---|
| HCMC-CORE-SW01 / SW02 | IOSvL2 | 8 | `Configurations/HQ_Startup_Configs/<name>.cfg` |
| HCMC-ACC-SW01 / SW02 | IOSvL2 | 8 | same folder |
| HCMC-EDGE-RTR01 / RTR02 | IOSv | 4 | same folder |
| HCMC-DC-SRV01 | IOSv | 4 | same folder |
| HANOI-EDGE-RTR01 / RTR02 | IOSv | 4 | `Hanoi_Startup_Configs` |
| HANOI-RND-SW01 | IOSvL2 | 8 | `Hanoi_Startup_Configs` |
| DANANG-EDGE-RTR01 / RTR02 | IOSv | 4 | `DaNang_Startup_Configs` |
| DANANG-LOG-SW01 | IOSvL2 | 8 | `DaNang_Startup_Configs` |
| ISP-A-RTR, ISP-B-RTR, INTERNET-RTR | IOSv | 4 | `ISP_Configs` |
| AUTO-HOST | Linux | 1 | see section 5 |
| PC-EXEC01, PC-ENG01, PC-GUEST01, PC-VOICE01, PC-HN-ENG01, PC-HN-AI01, PC-DN-LOG01, PC-DN-IOT01 | VPCS | 1 | section 4 |

2. **Cable** using `wiring_table.md`. EVE-NG numbers IOSv/IOSvL2 ports `Gi0/0, Gi0/1, Gi0/2, Gi0/3, Gi1/0 ...`
   in the order of the NICs - the same names used in the configs. Wire the VPCS `e0` and Linux `eth0` ports as listed.
3. Colour/label the three sites with the EVE-NG "Network/Text" objects (nice for the demo).

## 3. Load the configurations

Start the nodes in this order (gives BGP/IPsec something to talk to as soon as each site boots):
`ISP-A-RTR, ISP-B-RTR, INTERNET-RTR` -> `HCMC-*` -> `HANOI-*` -> `DANANG-*`.

For every IOS node open its console and:

```text
Router> enable
Router# configure terminal
(paste the whole <hostname>.cfg)      <- the file ends with "end"
Router# crypto key generate rsa general-keys modulus 2048     <- once per device, needed for SSH
Router# copy running-config startup-config
```

Notes
* Paste switches in 2 halves if the console drops characters (vIOS is slow on large pastes).
* If an image rejects a command (typical: `storm-control`, `switchport trunk encapsulation dot1q`,
  `ip verify unicast`, the zone-based firewall block) delete that line/block and continue - the configs mark the
  ZBF block clearly (`! ---- Zone-based firewall`). **Write down what you removed** and mention it in the report.
* Login after the paste: user `netadmin`, password = value of `TVI_LAB_PASSWORD` used when the configs were generated
  (default in `Tools/tvi_model.py`, change it - see README section "Secrets").
* The two HQ edge routers are cabled to each other (`Gi0/3` - `Gi0/3`, row "edge-to-edge" of the wiring table). Do not
  leave it out: without it a branch-uplink failure causes a routing loop at HQ (`Docs/01_Design_Rationale.md`, 5.1).
* If an image rejects `track timer ip route 1`, delete that line: failover still works, tracking just reacts up to 15 s later.
* Optional helper: `push_configs.py` pastes the files through the EVE-NG console ports for you (experimental).

## 4. VPCS end systems

Each VPCS node (console -> double click) - DHCP from the central server:

```text
PC-EXEC01>    ip dhcp
PC-ENG01>     ip dhcp
PC-GUEST01>   ip dhcp
PC-VOICE01>   ip dhcp
PC-HN-ENG01>  ip dhcp          # served from HQ across the IPsec tunnel through the DHCP relay
PC-HN-AI01>   ip dhcp
PC-DN-LOG01>  ip dhcp
PC-DN-IOT01>  ip dhcp
```

Expected: every PC receives an address from its VLAN's pool and the HSRP VIP as default gateway
(`show ip` on the VPCS). Save with `save`.

## 5. AUTO-HOST (Linux, VLAN 99, HQ)

```bash
sudo ip addr add 10.1.99.100/24 dev eth0
sudo ip link set eth0 up
sudo ip route add default via 10.1.99.1
# automation tooling
sudo apt-get update && sudo apt-get install -y python3-pip   # needs Internet access through the lab or a NAT cloud
pip3 install -r Automation/requirements.txt
```

The management VLAN is protected by the VTY ACL - SSH is accepted only from `10.x.99.0/24`, so run the
automation from this host (or add your own source address to `VTY-MGMT`). To copy the project into the node,
attach a **Cloud (pnet)** to the AUTO-HOST second NIC, or run the scripts from your own PC through an EVE-NG
`Cloud` node bridged into VLAN 99 (give the PC an address in 10.1.99.0/24 and extend `VTY-MGMT`).

Optional syslog collector on AUTO-HOST so that `logging host 10.1.99.100` has a receiver:
`sudo apt-get install -y rsyslog` and enable UDP 514 in `/etc/rsyslog.conf`.

## 6. Smoke test (5 minutes)

```text
HCMC-CORE-SW01# show etherchannel summary          -> Po1 (SU) Gi0/0(P) Gi0/1(P)
HCMC-CORE-SW01# show standby brief                 -> Active on V10/30/50/99/100, Standby on V20/40/60
HCMC-EDGE-RTR01# show ip ospf neighbor             -> FULL with both cores and HCMC-EDGE-RTR02 (Gi0/3, edge-to-edge link)
HCMC-EDGE-RTR01# show ip bgp summary               -> neighbour ISP-A, HCMC-EDGE-RTR02, Hanoi, Da Nang: Established
HCMC-EDGE-RTR01# show crypto isakmp sa             -> QM_IDLE to both branches
PC-HN-ENG01> ping 10.1.100.10                      -> reply (HQ DHCP/DNS server through the tunnel)
```

Then follow `Test_Plan.md`.
