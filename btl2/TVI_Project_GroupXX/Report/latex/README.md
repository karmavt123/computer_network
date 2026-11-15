# LaTeX report - how it is organised

**State: skeleton.** Structure, title page, tables, figures, listings and evidence slots are wired; the prose
is not written yet (red `TODO` markers). The yellow "Writing plan" boxes say what each section must contain
and which rubric criterion it serves; they disappear with `\draftfalse` in `main.tex`.

## Build

```bash
cd Report/latex
latexmk                # XeLaTeX + BibTeX -> build/main.pdf
make final             # copies it to ../TVI_Technical_Explanation_Report.pdf (the name the brief asks for)
```

Overleaf: upload the `Report/` folder (the report reads `../evidence/`), set the main file to
`latex/main.tex` and the compiler to **XeLaTeX** (LuaLaTeX also works). Without `latexmk`:
`xelatex main && bibtex main && xelatex main && xelatex main`.

## Files

| File | Edit by hand? | Purpose |
|---|---|---|
| `report-meta.tex` | **yes** | group, names, IDs, lecturer, date |
| `main.tex` | rarely | order of sections, draft switch |
| `sections/*.tex` | **yes** | the text |
| `tvi-report.sty` | no | layout, listing styles, macros |
| `references.bib` | yes | RFCs and standards already entered - check each entry you cite |
| `tables/*.tex` | **no** (generated) | IP plan, links, devices, VLANs, HSRP, BGP, cabling, emulation |
| `snippets/*` | **no** (generated) | excerpts of the startup-configs and of the automation code |
| `figures/*.pdf` | **no** (generated) | vector diagrams; put `figures/hcmut.png` here for the title page logo |
| `generated/facts.tex` | **no** (generated) | numbers as macros: `\FactNumDevices`, `\FactTestsModel`, ... |
| `../evidence/*.txt` | produced in the lab | CLI outputs from `Lab/collect_evidence.py` |
| `../evidence/img/*` | produced in the lab | screenshots (VPCS pings, EVE-NG topology) |

Regenerate the generated inputs after any model change: `make inputs` (or `python3 Tools/gen_latex.py`).

## Macros

| Macro | Use |
|---|---|
| `\cfgsnippet[label]{file}{caption}` | listing from `snippets/` with IOS highlighting (`\pysnippet`, `\yamlsnippet` likewise) |
| `\evidence[label]{id}{caption}` | CLI output `../evidence/<id>.txt`; a red box while the file is missing |
| `\screenshot[label]{file}{caption}{width}` | image from `../evidence/img/` |
| `\diagram[label]{name}{caption}{width}` | `figures/<name>.pdf`, falls back to `Design/img/<name>.png` |
| `\todo{...}` | red marker, counted on the last page of the draft |
| `\plan{...}` | writing notes, visible only in draft mode |
| `\dev{HCMC-CORE-SW01}`, `\cmd{show ip route}` | device names and commands in text |

## Before submission

1. `\draftfalse`, rebuild: the last page ("Draft status") disappears; search the PDF for `TODO` and `missing`.
2. Every number in Section 7 is one you measured in your lab.
3. `make final`.
