"""In-memory peer registry of the tracker (client-server part of the app).

The tracker answers three questions: which peers are alive, where they can
be reached (IP address, TCP chat port, UDP file port) and which user is
signed in on each of them.

Ownership of a ``peer_id``: the first ``/submit-info`` for a peer returns a
random *peer token*. Every later update, login, logout or removal for that
peer must present the token, so another host on the LAN cannot overwrite or
delete someone else's registration. A record whose heartbeat stops expires
after :data:`PEER_TTL_SECONDS` and its id becomes free again.
"""

import hmac
import secrets
import threading
import time

#: A peer that has not sent a heartbeat for this long is considered gone.
PEER_TTL_SECONDS = 45

#: Fields of a record that are shown to other peers.
PUBLIC_FIELDS = ("peer_id", "host", "port", "udp_port", "observed_ip",
                 "username", "login_at", "registered_at")


class TrackerError(Exception):
    """Base class of registry errors (mapped to HTTP status codes)."""

    status = 400


class NotRegistered(TrackerError):
    """The peer is unknown or its record has expired."""

    status = 404


class BadToken(TrackerError):
    """The caller does not own the peer id."""

    status = 403


class Tracker:
    """Thread-safe registry of active peers."""

    def __init__(self, ttl=PEER_TTL_SECONDS, clock=time.time):
        self._lock = threading.Lock()
        self._peers = {}
        self._ttl = ttl
        self._clock = clock

    # -- registration -----------------------------------------------------
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

    def unregister(self, peer_id, token):
        """Remove a peer on graceful shutdown (``/remove``)."""
        with self._lock:
            record = self._peers.get(peer_id)
            if record is None:
                return
            self._check_token_locked(record, token)
            del self._peers[peer_id]

    # -- users -----------------------------------------------------------
    def bind_user(self, peer_id, token, username):
        """Record that ``username`` signed in on ``peer_id`` (``/login``)."""
        with self._lock:
            record = self._owned_locked(peer_id, token)
            record["username"] = username
            record["login_at"] = self._clock()

    def unbind_user(self, peer_id, token):
        """Record a logout on ``peer_id``."""
        with self._lock:
            record = self._owned_locked(peer_id, token)
            record["username"] = None
            record["login_at"] = None

    def verify(self, peer_id, token):
        """Return True if ``token`` currently owns ``peer_id``."""
        with self._lock:
            try:
                self._owned_locked(peer_id, token)
            except TrackerError:
                return False
            return True

    # -- queries ---------------------------------------------------------
    def list_peers(self):
        """Public list of live peers, sorted by id (``/get-list``)."""
        with self._lock:
            self._expire_locked(self._clock())
            return self._public_locked()

    def get(self, peer_id):
        """Public record of one live peer, or None."""
        with self._lock:
            self._expire_locked(self._clock())
            record = self._peers.get(peer_id)
            return self._public(record) if record else None

    # -- helpers ---------------------------------------------------------
    def _owned_locked(self, peer_id, token):
        self._expire_locked(self._clock())
        record = self._peers.get(peer_id)
        if record is None:
            raise NotRegistered("Peer {} is not registered".format(peer_id))
        self._check_token_locked(record, token)
        return record

    @staticmethod
    def _check_token_locked(record, token):
        if not isinstance(token, str) or not hmac.compare_digest(
                token, record["token"]):
            raise BadToken("This peer id is registered by another node")

    def _expire_locked(self, now):
        expired = [key for key, record in self._peers.items()
                   if now - record["last_seen"] > self._ttl]
        for key in expired:
            del self._peers[key]

    def _public_locked(self):
        return [self._public(record) for _, record in sorted(self._peers.items())]

    @staticmethod
    def _public(record):
        return {key: record.get(key) for key in PUBLIC_FIELDS}
