def process_device(dev, creds, payload, opts, connect=None):
    connect = connect or ConnectHandler
    hostname = dev["hostname"]
    user, password, secret = creds
    params = dict(device_type=dev["device_type"], host=dev["ip"], port=dev.get("port", 22),
                  username=user, password=password, secret=secret, timeout=dev.get("timeout", 15),
                  conn_timeout=dev.get("timeout", 15))
    cmds = commands_for(dev, payload, opts.vlan)
    if opts.dry_run:
        print(f"\n[dry-run] {hostname} ({dev['ip']}) would receive {len(cmds)} commands:")
        print("\n".join("   " + c for c in cmds))
        LOG.info("DRY-RUN %s: %d commands", hostname, len(cmds))
        return "dry-run"
    print(f"[*] Connecting to {hostname} ({dev['ip']}) ...")
    try:
        conn = connect(**params)
        conn.enable()
        before = capture(conn, hostname, "before", opts.backup_dir, payload["capture"])
        print(f"    [+] {hostname}: state captured -> {before.name}")
        if not opts.backup_only:
            output = conn.send_config_set(cmds)
            LOG.info("Successfully configured %s:\n%s", hostname, output)
            if opts.save:
                conn.save_config()
                LOG.info("Saved running-config on %s", hostname)
            after = capture(conn, hostname, "after", opts.backup_dir, payload["capture"])
            print(f"    [+] {hostname}: {len(cmds)} commands applied, state captured -> {after.name}")
            diff_path, changed = write_diff(before, after, opts.log_dir, hostname)
            LOG.info("%s: %d line(s) differ between the two captures -> %s", hostname, changed, diff_path.name)
            print(f"    [+] {hostname}: {changed} changed line(s) -> logs/{diff_path.name}")
            if dev.get("role") in payload.get("vlan_roles", []):
                vl = conn.send_command("show vlan brief")
                missing = [str(v["id"]) for v in build_vlans(payload, opts.vlan)
                           if not re.search(rf"^\s*{v['id']}\s", vl, re.M)]
                if missing:
                    LOG.warning("%s: VLAN(s) not visible after push: %s", hostname, ",".join(missing))
                    print(f"    [!] {hostname}: VLANs not visible: {','.join(missing)}")
        else:
            LOG.info("Backup-only run completed on %s -> %s", hostname, before.name)
        conn.disconnect()
        return "ok"
    except NetmikoAuthenticationException as exc:
        LOG.error("Authentication failed on %s (%s): %s", hostname, dev["ip"], exc)
        print(f"    [x] {hostname}: authentication failed")
    except NetmikoTimeoutException as exc:
        LOG.error("Connection timeout on %s (%s): %s", hostname, dev["ip"], exc)
        print(f"    [x] {hostname}: connection timed out")
    except Exception as exc:  # noqa: BLE001 - keep going with the remaining devices
        LOG.error("Unexpected error on %s (%s): %s: %s", hostname, dev["ip"], type(exc).__name__, exc)
        print(f"    [x] {hostname}: {type(exc).__name__}: {exc}")
    return "failed"
