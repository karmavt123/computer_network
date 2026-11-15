def commands_for(dev, payload, extra_vlans=None):
    """Return the list of configuration commands for one device (pure function - easy to test)."""
    cmds = list(payload["baseline"].get("aaa", [])) + list(payload["baseline"]["common"])
    # per-role exceptions, e.g. the NTP master must not be told to use itself as NTP server
    skip = tuple(payload["baseline"].get("skip_by_role", {}).get(dev.get("role"), []))
    if skip:
        cmds = [c for c in cmds if not c.startswith(skip)]
    if dev.get("role") in payload.get("vlan_roles", []):
        for v in build_vlans(payload, extra_vlans):
            cmds += [f"vlan {v['id']}", f" name {v['name']}"]
    return cmds
