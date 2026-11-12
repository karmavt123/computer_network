#
# Copyright (C) 2026 pdnguyen of HCMC University of Technology VNU-HCM.
# All rights reserved.
# This file is part of the CO3093/CO3094 course,
# and is released under the "MIT License Agreement". Please see the LICENSE
# file that should have been included as part of this package.
#
# StreapRous release
#
# The authors hereby grant to Licensee personal permission to use
# and modify the Licensed Source Code for the sole purpose of studying
# while attending the course
#


"""
apps.tracker
~~~~~~~~~~~~~~~~~

Centralised **tracker** of the hybrid chat application (client-server paradigm).

The tracker never relays chat messages. It only

* authenticates users (``POST /login``; cookie session or HTTP Basic),
* keeps the list of active peers (``POST /submit-info``, ``GET /get-list``),
* keeps the channel directory and membership (``POST /add-list``, ``GET /channels``),
* lets one peer check the identity of another (``POST /verify``).

Live conversations then travel directly from peer to peer (see :mod:`apps.peer`).
"""

import hashlib
import hmac
import html
import ipaddress
import re
import secrets
import threading
import time

from daemon import StreapRous
from daemon.auth import (LoginThrottle, SessionStore, UserStore, build_set_cookie)

app = StreapRous()

COOKIE = "sid"
SESSION_TTL = 3600
#: a peer that has not refreshed its registration for this long is "inactive"
PEER_TTL = 45
GENERAL = "#general"
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
CHANNEL_RE = re.compile(r"^#[a-z0-9_-]{1,30}$")

users = UserStore()
sessions = SessionStore(SESSION_TTL)
throttle = LoginThrottle()
_secret = secrets.token_bytes(32)

_lock = threading.RLock()
#: username -> {"ip", "port", "last_seen"}
peers = {}
#: channel -> {"owner", "private", "members": set}; #general is open to every user
channels = {GENERAL: {"owner": "system", "private": False, "members": set()}}


def p2p_token(session_token):
    """
    Token a peer shows to other peers instead of its session id.

    It is an HMAC of the session id, so another peer can neither log in with it nor
    recover the session id, but the tracker can still recognise it in ``/verify``.
    """
    return hmac.new(_secret, session_token.encode(), hashlib.sha256).hexdigest()[:32]


def authenticate(req):
    """
    Identify the caller.

    Order: session cookie, ``Authorization: Bearer <session>``, then HTTP Basic.

    :rtype tuple: ``(username, session_token_or_None)`` or ``(None, None)``.
    """
    token = req.cookies.get(COOKIE)
    if not token:
        authz = req.headers.get("authorization", "")
        if authz.lower().startswith("bearer "):
            token = authz[7:].strip()
    user = sessions.get(token)
    if user:
        return user, token
    if req.auth:
        name, password = req.auth
        if not throttle.blocked(name) and users.check(name, password):
            throttle.success(name)
            return name, None
        throttle.failure(name)
    return None, None


def unauthorized(message="Authentication required"):
    return 401, {"ok": False, "error": message}


def _active_peers():
    now = time.time()
    with _lock:
        for name in [n for n, p in peers.items() if now - p["last_seen"] > PEER_TTL]:
            del peers[name]
        return {n: dict(p) for n, p in peers.items()}


def _can_see(channel, user):
    info = channels.get(channel)
    if info is None:
        return False
    return channel == GENERAL or user == info["owner"] or user in info["members"] or not info["private"]


def _is_member(channel, user):
    info = channels.get(channel)
    if info is None:
        return False
    return channel == GENERAL or user == info["owner"] or user in info["members"]


# ----------------------------------------------------------------------------- auth
@app.route('/login', methods=['POST'])
def login(headers, body, req):
    """
    Authenticate a user.

    Body: JSON ``{"username": ..., "password": ...}`` or a classic HTML form.
    Success: ``200`` + ``Set-Cookie: sid=...`` (HttpOnly). A form post is redirected to ``/admin``.
    """
    data = req.payload()
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    is_form = "x-www-form-urlencoded" in req.headers.get("content-type", "").lower()

    if not NAME_RE.match(username) or not password:
        return 400, {"ok": False, "error": "username and password are required"}
    if throttle.blocked(username):
        return 429, {"ok": False, "error": "too many failed attempts, retry in a few seconds"}
    if not users.check(username, password):
        throttle.failure(username)
        print("[Tracker] failed login for '{}'".format(username))
        return unauthorized("Invalid username or password")

    throttle.success(username)
    token = sessions.create(username)
    cookie = {"Set-Cookie": build_set_cookie(COOKIE, token, SESSION_TTL)}
    print("[Tracker] '{}' logged in".format(username))
    if is_form:
        cookie["Location"] = "/admin" if username == "admin" else "/login.html?ok=1"
        return 302, "", cookie
    return 200, {"ok": True, "username": username, "token": token,
                 "p2p_token": p2p_token(token), "ttl": SESSION_TTL}, cookie


@app.route('/logout', methods=['POST'])
def logout(headers, body, req):
    """Destroy the session and drop the user from the tracking list."""
    user, token = authenticate(req)
    if not user:
        return unauthorized()
    if token:
        sessions.destroy(token)
    with _lock:
        peers.pop(user, None)
    return 200, {"ok": True}, {"Set-Cookie": build_set_cookie(COOKIE, "", max_age=0)}


@app.route('/verify', methods=['POST'])
def verify(headers, body, req):
    """A logged-in peer asks whether ``(username, p2p_token)`` belongs to a live session."""
    user, _ = authenticate(req)
    if not user:
        return unauthorized()
    data = req.payload()
    name, proof = str(data.get("username", "")), str(data.get("p2p_token", ""))
    for token in sessions.tokens_of(name):
        if hmac.compare_digest(p2p_token(token), proof):
            return {"ok": True, "username": name}
    return 403, {"ok": False, "error": "unknown peer"}


# ---------------------------------------------------------------- tracking list
@app.route('/submit-info', methods=['POST'])
def submit_info(headers, body, req):
    """
    Peer registration / heartbeat (initialisation phase).

    Body: ``{"ip": "10.0.0.5", "port": 9101}``. The username always comes from the session,
    never from the body, so a peer cannot register on behalf of someone else.
    """
    user, _ = authenticate(req)
    if not user:
        return unauthorized()
    data = req.payload()
    try:
        ip = str(ipaddress.ip_address(str(data.get("ip", "")).strip()))
        port = int(data.get("port"))
        if not 0 < port < 65536:
            raise ValueError
    except (ValueError, TypeError):
        return 400, {"ok": False, "error": "a valid ip and port are required"}

    with _lock:
        new = user not in peers
        peers[user] = {"ip": ip, "port": port, "last_seen": time.time()}
    if new:
        print("[Tracker] peer registered: {} at {}:{}".format(user, ip, port))
    return {"ok": True, "registered": user, "active": len(_active_peers())}


@app.route('/add-list', methods=['POST'])
def add_list(headers, body, req):
    """
    Add peers to the member list of a channel (creating the channel on first use).

    Body: ``{"channel": "#project", "members": ["bob", "carol"], "private": false}``.
    Only the owner may add other users; anyone may add themselves to a public channel.
    """
    user, _ = authenticate(req)
    if not user:
        return unauthorized()
    data = req.payload()
    name = str(data.get("channel", "")).strip().lower()
    if name and not name.startswith("#"):
        name = "#" + name
    members = data.get("members", [])
    if not CHANNEL_RE.match(name) or not isinstance(members, list):
        return 400, {"ok": False, "error": "channel must look like #name; members must be a list"}
    members = {str(m) for m in members if NAME_RE.match(str(m))}
    unknown = sorted(m for m in members if not users.exists(m))
    if unknown:
        return 400, {"ok": False, "error": "unknown users: " + ", ".join(unknown)}

    with _lock:
        info = channels.get(name)
        if info is None:
            info = channels[name] = {"owner": user, "private": bool(data.get("private")), "members": set()}
            members.add(user)
        elif name == GENERAL:
            return {"ok": True, "channel": name, "members": "all"}
        elif user != info["owner"] and members != {user}:
            return 403, {"ok": False, "error": "only the owner can add other members"}
        elif info["private"] and user != info["owner"]:
            return 403, {"ok": False, "error": "private channel"}
        info["members"] |= members
        result = sorted(info["members"] | {info["owner"]})
    return {"ok": True, "channel": name, "owner": info["owner"], "private": info["private"],
            "members": result}


@app.route('/get-list', methods=['GET'])
def get_list(headers, body, req):
    """
    Peer discovery: the list of active peers, optionally restricted to one channel
    (``GET /get-list?channel=%23project``).
    """
    user, _ = authenticate(req)
    if not user:
        return unauthorized()
    channel = req.query.get("channel")
    if channel and not _can_see(channel, user):
        return 403, {"ok": False, "error": "no such channel or access denied"}

    now = time.time()
    result = []
    for name, info in sorted(_active_peers().items()):
        if channel and not _is_member(channel, name):
            continue
        result.append({"username": name, "ip": info["ip"], "port": info["port"],
                       "age": round(now - info["last_seen"], 1)})
    return {"ok": True, "you": user, "peers": result}


@app.route('/channels', methods=['GET'])
def list_channels(headers, body, req):
    """Channels the caller has joined (channel management)."""
    user, _ = authenticate(req)
    if not user:
        return unauthorized()
    with _lock:
        listing = [{"name": n, "owner": c["owner"], "private": c["private"],
                    "members": "all" if n == GENERAL else sorted(c["members"] | {c["owner"]})}
                   for n, c in sorted(channels.items()) if _is_member(n, user)]
    return {"ok": True, "channels": listing}


# ------------------------------------------------------------------ admin / health
@app.route('/health', methods=['GET'])
def health(headers, body):
    return {"ok": True, "role": "tracker", "active_peers": len(_active_peers())}


@app.route('/admin', methods=['GET'])
def admin(headers, body, req):
    """HTML dashboard of the tracking list. Protected: HTTP Basic (RFC 7617) or an admin session."""
    user, _ = authenticate(req)
    if user != "admin":
        # 401 + WWW-Authenticate makes the browser open its login dialog (RFC 7235)
        return 401, "401 Unauthorized - admin only", {
            "WWW-Authenticate": 'Basic realm="Tracker admin", charset="UTF-8"'}
    rows = "".join(
        "<tr><td>{}</td><td>{}:{}</td><td>{:.0f}s ago</td></tr>".format(
            html.escape(n), html.escape(p["ip"]), p["port"], time.time() - p["last_seen"])
        for n, p in sorted(_active_peers().items())) or "<tr><td colspan=3>no active peer</td></tr>"
    chans = "".join("<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
        html.escape(n), html.escape(c["owner"]),
        "all" if n == GENERAL else html.escape(", ".join(sorted(c["members"] | {c["owner"]}))))
        for n, c in sorted(channels.items()))
    page = ("<!doctype html><html><head><meta charset=utf-8><title>Tracker admin</title>"
            "<link rel=stylesheet href=/css/chat.css></head><body class=plain><main class=card>"
            "<h1>Tracker admin</h1><h2>Active peers</h2><table><tr><th>user</th><th>address</th>"
            "<th>last seen</th></tr>{}</table><h2>Channels</h2><table><tr><th>channel</th><th>owner</th>"
            "<th>members</th></tr>{}</table></main></body></html>").format(rows, chans)
    return 200, page


def create_tracker(ip, port, mode=None):
    """Launch the tracker daemon."""
    app.prepare_address(ip, port, mode)
    app.run()
