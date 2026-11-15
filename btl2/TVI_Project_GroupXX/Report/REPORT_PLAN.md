# Report plan (LaTeX) - what is ready, what is needed, how it will be written

The LaTeX sources are in `Report/latex/`. This file is the contract for the writing phase.

## 1. Rubric -> sections -> page budget

| Rubric criterion (weight) | Section | Pages | Main inputs (already generated unless marked) |
|---|---|---|---|
| 1. Executive summary & business alignment (10 %) | 1 | 1 | `Docs/01` s.1; facts macros; **measured failover numbers (lab)** |
| - | 2 Requirements & methodology | 1.5 | `Docs/01` s.1, s.8; brief |
| 2. Architecture & topology diagrams (15 %) | 3 | 4 | 4 vector figures, device table |
| 3. IP addressing schema (15 %) | 4 | 4 | 6 tables (sites, VLANs, 3 x VLSM, links) |
| 4. Core infrastructure & technology rationale (20 %) | 5 | 7-8 | `Docs/02`, `Docs/01` s.2-5; 11 config listings; BGP table |
| 5. Security, QoS & automation (15 %) | 6 | 5 | `Docs/02` s.6-7, `Docs/04`; 7 listings |
| 6. Testing, verification & failover (15 %) | 7 | 6-8 | **`Report/evidence/*.txt` + screenshots (lab)**; emulation table |
| (brief: 1-page reflection) | 8 Challenges & solutions | 1 | `Docs/01` s.5 + what happened in the lab |
| - | 9 Limitations, roadmap, conclusion | 1 | `Docs/05`, `Docs/01` s.7-8 |
| 7. Formatting, clarity, professionalism (10 %) | whole document | - | TOC, lists of figures/tables/listings, numbered references, consistent captions |
| | Appendices | 6-8 | HSRP table, cabling, extra config and code excerpts, work breakdown |

Target: about 30 pages of body plus appendices.

## 2. Needed from the team before the text can be final

| # | Item | Where it goes |
|---|---|---|
| 1 | Group number, member names + student IDs + responsibilities, lecturer, semester, date | `latex/report-meta.tex` |
| 2 | University logo (PNG) | `latex/figures/hcmut.png` |
| 3 | Platform actually used (EVE-NG / GNS3 / CML / Packet Tracer with approval), image versions, host RAM | Section 2.2 |
| 4 | CLI evidence: run `python3 Lab/collect_evidence.py` on AUTO-HOST | `Report/evidence/*.txt` |
| 5 | Host-side tests (VPCS ping / trace, ACL matrix), EVE-NG topology screenshot | `Report/evidence/img/` |
| 6 | Failover measurements (seconds of lost ping per scenario) | Table 7.x |
| 7 | Automation run: terminal output, `backups/` listing, `automation.log` with one error case | Section 7.7 |
| 8 | Commands the images rejected, problems met in the lab | Sections 8 and 9 |
| 9 | Decision: was the Ansible playbook executed? | Section 6.3 |

Sections 1-6, 8 (the four design problems), 9 and the appendices can be written **now** from the repository.
Section 7 can be drafted now and completed when items 4-7 exist.

## 3. Writing rules (to meet criterion 7 and avoid the "AI-generated without editing" penalty)

* English, precise and plain; every claim is tied to TVI (a site, a number, a requirement), not to a textbook.
* One idea per paragraph; tables for comparisons; a listing only when the text discusses its lines.
* No number, address or command typed by hand: tables, snippets and `\Fact...` macros come from the generator.
* Nothing is claimed that was not run. Design-only items are labelled in their first sentence.
  Emulation results are labelled as FRR, never as lab output.
* Every figure, table and listing is referenced in the text; every RFC cited is in `references.bib`.
* The team reads and edits the result: the oral defence will ask about it.

## 4. The old Markdown report

`Report/TVI_Technical_Explanation_Report.md` / `.pdf` is the earlier draft. It is kept until the LaTeX version
replaces it (`make final` overwrites the PDF).
