def register(self, peer, token=None, observed_ip=None):
    """Create or refresh a peer record (``/submit-info``, ``/add-list``).

    :param peer: dict with ``peer_id``, ``host``, ``port``, ``udp_port``.
    :param token: peer token from an earlier registration, if any.
    :param observed_ip: source IP of the HTTP request, as seen by the tracker.
    :returns: ``(token, public peer list)``.
    """
    now = self._clock()
    with self._lock:
        self._expire_locked(now)
        record = self._peers.get(peer["peer_id"])
        if record is not None:
            self._check_token_locked(record, token)
        else:
            record = {"token": secrets.token_urlsafe(24), "username": None,
                      "login_at": None, "registered_at": now}
            self._peers[peer["peer_id"]] = record
        record.update(peer_id=peer["peer_id"], host=peer["host"],
                      port=int(peer["port"]), udp_port=int(peer["udp_port"]),
                      observed_ip=observed_ip, last_seen=now)
        return record["token"], self._public_locked()
