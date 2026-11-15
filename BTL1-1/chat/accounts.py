"""User accounts (kept by the tracker) and browser sessions (kept by a peer).

* :class:`UserStore` holds the accounts in ``db/users.json``. Passwords are
  stored as salted PBKDF2-HMAC-SHA256 hashes, never in plain text.
* :class:`Sessions` issues the HttpOnly cookie that a peer daemon gives to
  its browser after a successful login (RFC 6265 cookie authentication).
"""

import hashlib
import hmac
import json
import secrets
import threading
from pathlib import Path

PBKDF2_ITERATIONS = 200_000
USERNAME_PATTERN = r"[A-Za-z0-9_.-]{1,32}"


def hash_password(password, salt=None, iterations=PBKDF2_ITERATIONS):
    """Return ``(salt_hex, hash_hex, iterations)`` for ``password``."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 salt, iterations)
    return salt.hex(), digest.hex(), iterations


class UserStore:
    """Account database of the tracker, loaded from a JSON file."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._users = {}
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._users = data.get("users", {})

    def check(self, username, password):
        """Return True if ``password`` is correct for ``username``."""
        with self._lock:
            record = self._users.get(username)
        if record is None:
            # Hash anyway so a wrong username takes as long as a wrong password.
            hash_password(password, b"\0" * 16, PBKDF2_ITERATIONS)
            return False
        _, digest, _ = hash_password(password, bytes.fromhex(record["salt"]),
                                     int(record["iterations"]))
        return hmac.compare_digest(digest, record["hash"])

    def add(self, username, password):
        """Create or replace an account and save the file."""
        salt, digest, iterations = hash_password(password)
        with self._lock:
            self._users[username] = {"salt": salt, "hash": digest,
                                     "iterations": iterations}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"users": self._users}, indent=2),
                                 encoding="utf-8")

    def usernames(self):
        """Names of all accounts (used by tests and the admin script)."""
        with self._lock:
            return sorted(self._users)


class Sessions:
    """Cookie sessions of one peer daemon.

    A cookie value is ``username:token:signature`` where the signature is an
    HMAC over ``username:token`` with a key that exists only in this
    process. The value must also be in the live-session set, so logging out
    (or restarting the daemon) invalidates it.
    """

    def __init__(self, cookie_name="session"):
        self._key = secrets.token_bytes(32)
        self.cookie_name = cookie_name
        self._sessions = set()
        self._lock = threading.Lock()

    def create(self, username):
        """Start a session for an already authenticated ``username``."""
        token = secrets.token_urlsafe(24)
        value = "{}:{}:{}".format(username, token, self._sign(username, token))
        with self._lock:
            self._sessions.add(value)
        return value

    def username(self, headers):
        """Return the user of the request's session cookie, or None."""
        value = self._cookie(headers)
        with self._lock:
            if value not in self._sessions:
                return None
        try:
            username, token, signature = value.split(":", 2)
        except ValueError:
            return None
        expected = self._sign(username, token)
        return username if hmac.compare_digest(expected, signature) else None

    def destroy(self, headers):
        """End the session of the request's cookie (logout)."""
        value = self._cookie(headers)
        with self._lock:
            self._sessions.discard(value)

    def _sign(self, username, token):
        return hmac.new(self._key, (username + ":" + token).encode("utf-8"),
                        hashlib.sha256).hexdigest()

    def _cookie(self, headers):
        for pair in headers.get("cookie", "").split(";"):
            key, sep, value = pair.strip().partition("=")
            if sep and key == self.cookie_name:
                return value
        return ""
