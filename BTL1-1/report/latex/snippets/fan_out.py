def _fan_out(self, wire, targets):
    """Deliver ``wire`` to all targets in parallel; relay on failure."""
    report = {"direct": [], "relayed": [], "failed": {}}
    futures = {peer["peer_id"]: self._pool.submit(self._deliver, peer, wire)
               for peer in targets}
    unreachable = []
    for peer_id, future in futures.items():
        try:
            future.result()
            report["direct"].append(peer_id)
        except (OSError, ValueError) as exc:
            unreachable.append(peer_id)
            report["failed"][peer_id] = str(exc)
            with self._lock:
                self._connected.pop(peer_id, None)
    if unreachable and self.relay_fallback and self.token:
        try:
            result = self._tracker("/relay", {"peer_id": self.peer_id,
                                              "token": self.token,
                                              "targets": unreachable,
                                              "frame": wire})
            for peer_id in result.get("delivered", []):
                report["relayed"].append(peer_id)
                report["failed"].pop(peer_id, None)
        except TrackerUnavailable as exc:
            for peer_id in unreachable:
                report["failed"][peer_id] += "; relay: {}".format(exc)
    return report
