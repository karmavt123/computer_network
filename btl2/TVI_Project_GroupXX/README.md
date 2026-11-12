# TVI Enterprise Network - BTL2 (Computer Networks, HCMUT)

Resilient, secure, automated 3-site network (HCMC HQ - Hanoi R&D - Da Nang logistics) with dual ISPs, IPsec overlay, HSRP/LACP campus, QoS and Python/Ansible automation.

> **Before you submit:** rename `TVI_Project_GroupXX` to your group number, put your names in the report header, run the lab
> yourself and replace every **[INSERT ...]** placeholder in the report with your own screenshots/measurements, and make sure
> **every member can explain every file** (the course checks code integrity and may ask you to defend it).

## Layout

```text
TVI_Project_GroupXX/
  Report/                 TVI_Technical_Explanation_Report.md/.pdf   <- technical explanation (13 pages)
  Configurations/         HQ_ / Hanoi_ / DaNang_Startup_Configs, ISP_Configs   <- 16 generated startup-configs
  Automation/             inventory.yaml, payload.yaml, auto_provision.py (Netmiko),
                          deploy_baseline.yml (Ansible), tests/, backups/, logs/
  Design/                 IP_Schema.md/.xlsx, TVI_Topology.drawio, img/*.png|svg
  Lab/                    EVE-NG_Setup.md, Test_Plan.md, Failover_Demo_Script.md, wiring_table.md, push_configs.py
  PacketTracer_Reduced/   reduced variant + configs (only with instructor approval)
  Tools/                  tvi_model.py (single source of truth), gen_configs.py, gen_docs.py, test_model.py
```

Everything in `Configurations/`, `Design/`, `Automation/inventory.yaml`, `Lab/wiring_table.md` is **generated from `Tools/tvi_model.py`**.
Change the model, rerun the generators, and all documents stay consistent.

## Quick start

```bash
# 1. (optional) regenerate everything after editing Tools/tvi_model.py
export TVI_LAB_PASSWORD='choose-a-lab-password'   # used to build the type-5 hash in the configs
export IPSEC_PSK='...'  BGP_KEY='...'  HSRP_KEY='...'   # lab secrets, otherwise defaults from the model are used
python3 Tools/gen_configs.py
python3 Tools/gen_docs.py            # needs: pip install openpyxl pyyaml playwright (diagrams: chromium)

# 2. static checks (no lab needed)
python3 -m unittest -v Tools.test_model
python3 -m unittest -v Automation.tests.test_auto_provision

# 3. build the lab: Lab/EVE-NG_Setup.md  -> verify: Lab/Test_Plan.md -> demo: Lab/Failover_Demo_Script.md

# 4. automation (from AUTO-HOST in the lab)
cd Automation
pip install -r requirements.txt
export TVI_SSH_USER=netadmin     # or let the script ask; the password is asked with getpass
python3 auto_provision.py --dry-run
python3 auto_provision.py --roles core,access --vlan 110:LAB_DEMO --save
#   Ansible alternative (check first: ansible-playbook --syntax-check deploy_baseline.yml):
ansible-galaxy collection install cisco.ios ansible.netcommon
cp group_vars/all/vault.yml.example group_vars/all/vault.yml && ansible-vault encrypt group_vars/all/vault.yml
ansible-playbook deploy_baseline.yml --ask-vault-pass
```

## Secrets

* No device password is stored in clear text in scripts or configs: the startup-configs contain only a **type-5 hash** of the lab
  password; automation takes credentials from environment variables or `getpass`; Ansible uses Vault.
* The configs *do* contain the lab IPsec/BGP/HSRP keys (the defaults in `tvi_model.py`, overridable with the env vars above) because IOS has to be given them;
  they are lab values - change them and never reuse them anywhere real. `Automation/.gitignore` keeps `.env`, vault files, backups and logs out of git.
* Backups under `Automation/backups/` contain device configs (with those keys): do not publish a real run.

## Rubric mapping (1-10)

| Step | Where |
|---|---|
| 1-2 Architecture, IP plan | `Report` (sec. 3, 8), `Design/*` |
| 3 L2 | `Configurations/*Switch*`, `Test_Plan.md` step 3 |
| 4 FHRP + DHCP/DNS/NTP | core/DC-SRV configs, `Test_Plan.md` step 4 |
| 5 Underlay | OSPF/OSPFv3 in every config, step 5 |
| 6 Overlay | edge configs (eBGP, IPsec VTI, iBGP RR), step 6 |
| 7 Security | ACL/ZBF/L2 security blocks, step 7 |
| 8 QoS | edge configs (PM-MARK-IN, PM-WAN-PARENT/CHILD), step 8 |
| 9 Automation | `Automation/` + step 9 |
| 10 E2E + failover | `Failover_Demo_Script.md`, report sec. 10 |

## Honest status

* Validated **statically** (18 model checks + 7 automation mock tests pass): no overlapping subnets, unique addresses, mirrored link ends, all references defined.
* **Not** validated on real IOSv/IOSvL2 images or in Packet Tracer; some commands (storm-control, ZBF, certain QoS lines) may be rejected by your image - remove them and write that down.
* `deploy_baseline.yml` was not syntax-checked (collections could not be downloaded in the build environment).
* `Lab/push_configs.py` (paste via EVE-NG console) is experimental.
* Test-plan "expected" values describe what *should* appear; your screenshots are the evidence.
* EVPN-VXLAN, SD-WAN, ZTNA/SASE and WLC are design discussion only.

## Zip layout for submission (as required by the brief)

```text
TVI_Project_GroupXX/
  Report/TVI_Technical_Explanation_Report.pdf
  Configurations/{HQ_Startup_Configs, Hanoi_Startup_Configs, DaNang_Startup_Configs}   (+ ISP_Configs)
  Automation/{inventory.yaml, auto_provision.py | deploy_baseline.yml, backups/}
```
Keep `Tools/`, `Design/`, `Lab/` as supporting material (or drop them if the grader wants exactly the three folders - check the brief).

## Academic integrity

The project is meant to be understood by the group. The identical structure/naming across groups triggers review: change names, subnets, VLAN IDs,
descriptions and the topology to your own choices (edit `Tools/tvi_model.py`, regenerate) and be ready to explain every line in the oral defence.
