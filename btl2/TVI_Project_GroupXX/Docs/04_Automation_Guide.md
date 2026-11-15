# Automation guide

How the tooling fits together, what each script does line by line where it matters, and how to extend it.

## 1. The pipeline

```text
                     Tools/tvi_model.py            <- the ONLY place where names, addresses, VLANs, links live
                            |
      +---------------------+--------------------------+----------------------+
      |                     |                          |                      |
 gen_configs.py        gen_docs.py               gen_latex.py          gen_walkthrough.py
 16 startup-configs    IP_Schema.md/.xlsx        Report/latex/tables   Docs/02_Config_Walkthrough.md
 Configurations/       diagrams (.png/.svg)      .../snippets
                       TVI_Topology.drawio       .../figures (.pdf)
                       inventory.yaml            .../generated/facts.tex
                       wiring_table.md
                       evidence_plan.yaml
      |                     |
      |   test_model.py (20)  test_configs.py (22)        <- static checks, no lab needed
      |   frr_emulation/emulate.py                         <- routing logic with real OSPF/BGP daemons
      v                     v
   EVE-NG lab  <--- Automation/auto_provision.py (Netmiko)  /  deploy_baseline.yml (Ansible)
        |
        +--------> Lab/collect_evidence.py  ->  Report/evidence/*.txt  ->  \evidence{...} in the LaTeX report
```

Why a model and generators instead of sixteen hand-written files: the three /16 plans, 25 gateway subnets,
8 tunnel end points and 13 BGP sessions must agree across devices. Written by hand, a single mistyped mask
breaks an adjacency and is hard to find; generated from one table, both ends of every link come from the same
line of data, and a test can prove it.

## 2. `Automation/auto_provision.py` (rubric step 9)

### 2.1 What the brief asks and where it is implemented

| Requirement in the brief | Implementation |
|---|---|
| Hosts, management IPs, device types and SSH ports in an external inventory | `inventory.yaml` (generated), read by `load_inventory()`; `defaults` are merged into every device |
| No plaintext credentials in scripts | `get_credentials()`: `TVI_SSH_USER` / `TVI_SSH_PASS` / `TVI_ENABLE_PASS`, otherwise `input()` + `getpass()` |
| Push baseline (AAA, NTP, syslog, banner) to core and access switches | `payload.yaml: baseline.aaa + baseline.common`, assembled by `commands_for()` |
| Create and name VLAN 10/20/30/99 from the input payload | `payload.yaml: vlans.standard` (+ `extended`, + `--vlan ID:NAME`), only for roles in `vlan_roles` |
| `show running-config` and `show ip interface brief` before and after | `capture()` called twice in `process_device()`; commands listed in `payload.yaml: capture` |
| `<hostname>_<timestamp>_backup.cfg` in `backups/` | `capture()` |
| Log successes and SSH exceptions to `automation.log` | `setup_logging()`; `NetmikoAuthenticationException`, `NetmikoTimeoutException` and any other exception are caught per device |

### 2.2 Flow for one device (`process_device`)

1. Build the command list (pure function `commands_for`: baseline, minus role-specific exceptions such as
   "the NTP master does not get `ntp server`", plus VLANs for switches).
2. `--dry-run`: print the list and stop - nothing is opened.
3. Connect (`ConnectHandler`), `enable`.
4. Capture **before** -> backup file.
5. `send_config_set(commands)`; with `--save` also `write memory`.
6. Capture **after** -> second backup file; `write_diff()` stores the unified diff of the two captures in
   `logs/<host>_<time>_change.diff` and logs how many lines changed.
7. On switches: read `show vlan brief` and warn if a VLAN from the payload is not there.
8. Any exception: log it, print one line, return `failed` - the other devices are still processed.

`run_automation()` processes the devices one after another by default (`--workers N` uses a thread pool
of N sessions) and returns a non-zero exit code if at least one device failed, so the script can be used in
a pipeline.

### 2.3 Options

| Option | Effect |
|---|---|
| `--inventory`, `--payload`, `--backup-dir`, `--log-dir` | alternative files / directories |
| `--devices A,B` / `--roles core,access` | limit the run |
| `--vlan 110:LAB_DEMO` (repeatable) | add VLANs for this run only |
| `--dry-run` | show what would be sent |
| `--backup-only` | capture state, change nothing |
| `--save` | `copy running-config startup-config` after the change |
| `--workers N` | N parallel SSH sessions (default 1) |

### 2.4 Idempotence and safety

Every baseline line is a statement of desired state (`ntp server X`, `vlan 10` / `name ...`): sending it
twice changes nothing, which is what the change diff of a second run shows (0 lines). The script never
removes configuration, never touches interfaces or routing, and takes a backup before it changes anything -
the rollback is "paste the *before* file".

### 2.5 Tests

`Automation/tests/test_auto_provision.py` and `test_collect_evidence.py` replace `ConnectHandler` by a fake
object, so they run without devices: device selection, dry-run opens no connection, backup file names,
timeout and authentication failures are logged and do not stop the run, VLAN verification, role-specific
skips, the change diff.

```bash
python3 -m unittest -v Automation.tests.test_auto_provision Automation.tests.test_collect_evidence
```

## 3. `Automation/deploy_baseline.yml` (Ansible alternative)

Same intent with `cisco.ios` modules: state capture before (`ios_command` + `copy` to
`<hostname>_<timestamp>_backup.cfg`), baseline lines and banner (`ios_config`), VLANs (`ios_vlans`), state
capture after. Hosts come from `ansible_hosts.ini` (generated), variables from
`group_vars/all/vars.yml`, secrets from `group_vars/all/vault.yml` encrypted with Ansible Vault.

**Status: written, not executed.** The collection could not be installed where the project was built, so not
even `--syntax-check` was run. Before relying on it:

```bash
ansible-galaxy collection install cisco.ios ansible.netcommon
ansible-playbook --syntax-check deploy_baseline.yml
ansible-playbook deploy_baseline.yml --check --ask-vault-pass      # dry run
```

The Python script is the one to demonstrate; mention the playbook as the declarative alternative.

## 4. `Lab/collect_evidence.py`

Runs the verification commands of the test plan and stores each output as `Report/evidence/<id>.txt`.
The list is `Lab/evidence_plan.yaml` (generated from the model): id, device, commands, and optional
`expect` / `reject` regular expressions that produce a PASS / CHECK hint. Only `show`, `ping` and
`traceroute` are accepted - the collector cannot change a device.

```bash
cd Lab
python3 collect_evidence.py --dry-run
python3 collect_evidence.py                       # all automatic items
python3 collect_evidence.py --only 's10_*' --tag core1_down    # snapshot during a failover test
```

Items marked `manual` (ISP routers, which TVI does not manage) and everything typed on a VPCS host have to
be pasted by hand into `Report/evidence/<id>.txt`; the script prints the list.

## 5. Generators

| Script | Reads | Writes |
|---|---|---|
| `Tools/gen_configs.py` | model, env `TVI_LAB_PASSWORD`, `IPSEC_PSK`, `BGP_KEY`, `HSRP_KEY` | `Configurations/*/*.cfg` |
| `Tools/gen_docs.py` | model | `Design/*`, `Automation/inventory.yaml`, `ansible_hosts.ini`, `Lab/wiring_table.md`, `Lab/evidence_plan.yaml` |
| `Tools/gen_walkthrough.py` | configs | `Docs/02_Config_Walkthrough.md` |
| `Tools/gen_latex.py` | model, configs, emulation results | `Report/latex/{tables,snippets,figures,generated}` |

`gen_docs.py` and `gen_latex.py` need `openpyxl`, `pyyaml` and Playwright's Chromium for the diagrams
(`pip install openpyxl pyyaml playwright && playwright install chromium`).

## 6. Extending

* **New baseline line**: add it to `payload.yaml` -> `--dry-run` -> run. If it must also be in the
  startup-configs, add it to `baseline()` in `gen_configs.py`.
* **New check**: a test in `Tools/test_configs.py` (it reads only the rendered files, so it also protects
  against generator bugs).
* **New evidence item**: `add(...)` in `evidence_plan()` in `gen_docs.py`.
* **NAPALM / telemetry**: `capture()` is the hook - add commands to `payload.yaml: capture`, or parse the
  captured text (`show ip interface brief` is already stored) into JSON.
