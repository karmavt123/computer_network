def write_diff(before: Path, after: Path, log_dir: Path, hostname: str):
    """Unified diff of the two captures (what did this run really change?). Returns (path, changed_lines)."""
    import difflib

    def body(p):   # drop the three header lines (stage / timestamp differ by construction)
        return [ln for ln in p.read_text(encoding="utf-8").splitlines() if not ln.startswith("! ")]
    diff = list(difflib.unified_diff(body(before), body(after), before.name, after.name, lineterm="", n=1))
    changed = sum(1 for ln in diff if ln[:1] in "+-" and ln[:3] not in ("+++", "---"))
    path = log_dir / after.name.replace("_backup.cfg", "_change.diff")
    path.write_text("\n".join(diff) + "\n", encoding="utf-8")
    return path, changed
