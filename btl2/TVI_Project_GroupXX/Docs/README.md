# Documentation index

| # | Document | Audience | Content |
|---|---|---|---|
| 01 | [Design rationale](01_Design_Rationale.md) | reviewer, team | Requirement -> decision traceability, alternatives rejected, traffic flows, failure behaviour, four design problems found and fixed, availability reasoning, growth, lab substitutions |
| 02 | [Configuration walkthrough](02_Config_Walkthrough.md) | team, examiner | Every configuration block with the live lines: what / why / verify / without it (generated) |
| 03 | [Operations runbook](03_Operations_Runbook.md) | operator | Backup, add VLAN / switch / branch, key rotation, maintenance, healthy state, troubleshooting |
| 04 | [Automation guide](04_Automation_Guide.md) | team, examiner | Pipeline, `auto_provision.py` in detail, Ansible, evidence collector, generators, how to extend |
| 05 | [Validation](05_Validation.md) | reviewer | Three validation layers, what each proves, emulation results, what only the lab can prove |
| VI | [Hướng dẫn cho nhóm](VI_Huong_dan_nhom.md) | team (Vietnamese) | Reading order, key ideas, oral-defence questions, what is not implemented, checklists |

Related: `../README.md` (quick start), `../Design/IP_Schema.md` (addressing), `../Lab/` (EVE-NG build, test plan,
demo script), `../Report/latex/` (report sources), `../Tools/frr_emulation/README.md` (routing emulation).
