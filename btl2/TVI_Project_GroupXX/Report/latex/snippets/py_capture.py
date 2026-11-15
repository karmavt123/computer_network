def capture(conn, hostname, stage, backup_dir: Path, commands):
    """Run the capture commands and store them in a timestamped backup file."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = backup_dir / f"{hostname}_{ts}_backup.cfg"
    n = 1
    while path.exists():                       # two captures in the same second
        path = backup_dir / f"{hostname}_{ts}_{n}_backup.cfg"
        n += 1
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"! host: {hostname}\n! stage: {stage}\n! captured: {datetime.now().isoformat(timespec='seconds')}\n")
        for c in commands:
            fh.write(f"\n! ===== {c} =====\n")
            fh.write(conn.send_command(c) + "\n")
    return path
