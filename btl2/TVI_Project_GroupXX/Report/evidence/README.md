# Evidence

CLI outputs and screenshots from **your** lab. Nothing here is generated outside the lab.

* `*.txt` - written by `python3 Lab/collect_evidence.py` (one file per item of `Lab/evidence_plan.yaml`), or pasted
  by hand for devices without SSH (ISP routers, VPCS hosts). Included in the report with `\evidence{<id>}{caption}`.
* `index.json` - PASS / CHECK hints of the last run.
* `img/` - screenshots (VPCS ping/trace, EVE-NG topology, automation run). Included with `\screenshot{file}{caption}{width}`.

Files contain device configuration details from the lab (hashes, keys of the lab). Keep the repository private.
