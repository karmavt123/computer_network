#
# Copyright (C) 2026 pdnguyen of HCMC University of Technology VNU-HCM.
# All rights reserved.
# This file is part of the CO3093/CO3094 course.
#
# StreapRous release
#

"""
daemon.auth
~~~~~~~~~~~~~~~~~

Authentication helpers for the StreapRous HTTP server.

Two mechanisms are provided (see RFC 7235 and RFC 6265):

* **HTTP Basic** - the server answers ``401`` + ``WWW-Authenticate`` and the browser
  retries with ``Authorization: Basic base64(user:password)``.
* **Cookie sessions** - after a successful login the server sends ``Set-Cookie`` with
  an unguessable session id; the browser returns it in the ``Cookie`` header.

Passwords are never stored in clear text: ``db/users.json`` keeps salted PBKDF2 hashes.
"""

import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_DIR = os.path.join(BASE_DIR, "db")
USERS_FILE = os.path.join(DB_DIR, "users.json")

PBKDF2_ROUNDS = 60_000

#: Demo accounts created when ``db/users.json`` does not exist yet.
DEFAULT_USERS = {
    "alice": "alice123",
    "bob": "bob123",
    "carol": "carol123",
    "admin": "admin123",
}


def hash_password(password, salt=None, rounds=PBKDF2_ROUNDS):
    """Return ``pbkdf2$rounds$salt$hexdigest`` for ``password``."""
    salt = salt or secrets.token_hex(8)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 salt.encode("utf-8"), rounds)
    return "pbkdf2${}${}${}".format(rounds, salt, digest.hex())


def verify_password(password, stored):
    """Constant-time check of ``password`` against a stored hash string."""
    try:
        _, rounds, salt, _ = stored.split("$")
        expected = hash_password(password, salt, int(rounds))
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(expected, stored)


class UserStore:
    """File backed user database (``username -> password hash``)."""

    def __init__(self, path=USERS_FILE):
        self.path = path
        self._lock = threading.Lock()
        self.users = {}
        self.load()

    def load(self):
        """Load the database, creating the demo accounts on first use."""
        with self._lock:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as handle:
                    self.users = json.load(handle)
                return
            self.users = {name: hash_password(pw) for name, pw in DEFAULT_USERS.items()}
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump(self.users, handle, indent=2)

    def exists(self, username):
        return username in self.users

    def check(self, username, password):
        """Return True when the credentials are valid."""
        stored = self.users.get(username)
        if stored is None:
            # burn comparable time so unknown users are not distinguishable
            verify_password(password, hash_password("x", "0" * 16))
            return False
        return verify_password(password, stored)


class SessionStore:
    """Thread-safe in-memory session table: ``token -> (username, expiry)``."""

    def __init__(self, ttl=3600):
        self.ttl = ttl
        self._lock = threading.Lock()
        self._sessions = {}

    def create(self, username):
        token = secrets.token_urlsafe(24)
        with self._lock:
            self._sessions[token] = (username, time.time() + self.ttl)
        return token

    def get(self, token):
        """Return the username owning ``token`` or None when unknown/expired."""
        if not token:
            return None
        with self._lock:
            item = self._sessions.get(token)
            if item is None:
                return None
            if item[1] < time.time():
                del self._sessions[token]
                return None
            return item[0]

    def destroy(self, token):
        with self._lock:
            self._sessions.pop(token, None)

    def tokens_of(self, username):
        """Live (non expired) session ids of ``username``."""
        now = time.time()
        with self._lock:
            return [t for t, (u, exp) in self._sessions.items() if u == username and exp >= now]


class LoginThrottle:
    """Very small brute-force guard: lock a username after repeated failures."""

    def __init__(self, max_failures=5, lock_seconds=15):
        self.max_failures = max_failures
        self.lock_seconds = lock_seconds
        self._lock = threading.Lock()
        self._state = {}

    def blocked(self, username):
        with self._lock:
            fails, until = self._state.get(username, (0, 0))
            return until > time.time()

    def failure(self, username):
        with self._lock:
            fails, until = self._state.get(username, (0, 0))
            fails += 1
            if fails >= self.max_failures:
                until, fails = time.time() + self.lock_seconds, 0
            self._state[username] = (fails, until)

    def success(self, username):
        with self._lock:
            self._state.pop(username, None)


def parse_basic_auth(header_value):
    """
    Decode an ``Authorization: Basic ...`` header.

    :rtype tuple: ``(username, password)`` or ``None`` if absent/malformed.
    """
    if not header_value:
        return None
    parts = header_value.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "basic":
        return None
    try:
        decoded = base64.b64decode(parts[1], validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    if ":" not in decoded:
        return None
    user, _, password = decoded.partition(":")
    return user, password


def parse_cookie_header(header_value):
    """Parse a ``Cookie:`` header value into a ``dict`` (RFC 6265 section 5.4)."""
    cookies = {}
    for pair in (header_value or "").split(";"):
        if "=" not in pair:
            continue
        key, _, value = pair.strip().partition("=")
        if key:
            cookies[key] = value.strip('"')
    return cookies


def build_set_cookie(name, value, max_age=3600, path="/", http_only=True, same_site="Lax"):
    """Build a ``Set-Cookie`` header value."""
    parts = ["{}={}".format(name, value), "Path={}".format(path)]
    if max_age is not None:
        parts.append("Max-Age={}".format(max_age))
    if http_only:
        parts.append("HttpOnly")
    if same_site:
        parts.append("SameSite={}".format(same_site))
    return "; ".join(parts)
