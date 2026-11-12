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
apps.peer
~~~~~~~~~~~~~~~~~

One **peer daemon** of the hybrid chat application.

A peer is both a small web server for the browser of its user and a P2P node:

* browser <-> peer: HTML/CSS/JS plus the JSON API (``/login``, ``/get-list``, ``/connect-peer``,
  ``/broadcast-peer``, ``/send-peer``, ``/channels``, ``/api/state``);
* peer  <-> tracker: client-server phase (login, ``submit-info``, ``get-list``, channel directory);
* peer  <-> peer: live phase. Messages go **directly** to the other daemon
  (``/peer-hello`` and ``/peer-message``) without touching the tracker.

All handlers are ``async`` and every outgoing call uses the non-blocking
:func:`daemon.httpclient.async_http_request`, so one slow remote peer never stalls the daemon.
"""

import asyncio
import base64
import hashlib
import mimetypes
import os
import re
import threading
import time
import uuid

from daemon import StreapRous
from daemon.auth import SessionStore, build_set_cookie
from daemon.httpclient import PeerError, async_http_request
from daemon import secure

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = StreapRous()

GENERAL = "#general"
SESSION_TTL = 3600
MAX_TEXT = 2000
HEARTBEAT_EVERY = 10.0     # seconds between submit-info refreshes
SYNC_EVERY = 2.5           # seconds between tracker list refreshes
VERIFY_CACHE = 60.0
CHANNEL_RE = re.compile(r"^#[a-z0-9_-]{1,30}$")
CHUNK_BYTES = 48 * 1024        # size of one file segment (before base64)
MAX_FILE_BYTES = 20 * 1024 * 1024
RETRIES = 3                    # per-chunk retransmissions before a peer is reported as failed
FILES_DIR = os.path.join(BASE_DIR, "db", "files")
SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]")


class Node:
    """All mutable state of this daemon. A daemon belongs to one user at a time."""

    def __init__(self):
        self.lock = threading.RLock()
        self.reset()
        self.tracker = ("127.0.0.1", 9000)
        self.address = ("127.0.0.1", 9101)
        self.cookie = "psid"
        self.sessions = SessionStore(SESSION_TTL)

    def reset(self):
        self.username = None
        self.tracker_token = None      # session id at the tracker (secret)
        self.p2p_token = None          # proof shown to other peers
        self.known = {}                # username -> {"ip", "port"} from the tracker
        self.connected = {}            # username -> {"ip", "port", "token", "since"}
        self.verified = {}             # (username, token) -> expiry timestamp
        self.channels = {GENERAL: {"name": GENERAL, "owner": "system", "private": False, "members": "all"}}
        self.log = []                  # every message ever shown, ordered by local id
        self.seen = set()              # message ids already stored (deduplication)
        self.inbox = {}                # file id -> partial incoming transfer
        self.outbox = {}               # file id -> outgoing transfer state
        self.next_id = 1
        self.tracker_ok = False
        self.last_sync = 0.0
        self.last_submit = 0.0


node = Node()


def configure(tracker_host, tracker_port, advertise_ip, port):
    """Set the tracker address, the address advertised to other peers and the cookie name."""
    node.tracker = (tracker_host, int(tracker_port))
    node.address = (advertise_ip, int(port))
    # cookies are shared by all ports of one host, so each daemon needs its own cookie name
    node.cookie = "psid_{}".format(port)


# --------------------------------------------------------------------------- utils
def dm_name(a, b):
    """Canonical channel name of the private conversation between two users."""
    return "dm:" + ":".join(sorted((a, b)))


def add_message(channel, sender, text, mid=None, ts=None, kind="chat", delivery=None, extra=None):
    """Append a message to the log (deduplicated by ``mid``); returns the stored dict or None."""
    with node.lock:
        mid = mid or uuid.uuid4().hex
        if mid in node.seen:
            return None
        node.seen.add(mid)
        msg = {"id": node.next_id, "mid": mid, "channel": channel, "sender": sender, "text": text,
               "ts": ts or time.time(), "kind": kind, "delivery": delivery or {}}
        msg.update(extra or {})
        node.next_id += 1
        node.log.append(msg)
        return msg


def me():
    return node.username


def current_user(req, mutate=False):
    """
    Return the username bound to the request's session cookie, else ``None``.

    State-changing calls must also carry ``X-Requested-With`` which a cross-site HTML form
    cannot set (basic CSRF defence; the cookie is additionally ``SameSite=Lax``).
    """
    user = node.sessions.get(req.cookies.get(node.cookie))
    if not user or user != node.username:
        return None
    if mutate and req.headers.get("x-requested-with", "") != "fetch":
        return None
    return user


def denied(req, mutate=False):
    """Standard error for a failed :func:`current_user` check."""
    if current_user(req) is None:
        return 401, {"ok": False, "error": "not logged in"}
    return 403, {"ok": False, "error": "missing X-Requested-With header"}


def read_www(name):
    with open(os.path.join(BASE_DIR, "www", name), "rb") as handle:
        return handle.read()


async def tracker_call(method, path, body=None, cookie=True):
    """Call the tracker as the logged-in user; refreshes ``tracker_ok``."""
    headers = {"Cookie": "sid={}".format(node.tracker_token)} if cookie and node.tracker_token else {}
    host, port = node.tracker
    try:
        resp = await async_http_request(host, port, method, path, body, headers)
    except PeerError:
        node.tracker_ok = False
        raise
    node.tracker_ok = True
    return resp


async def sync_with_tracker(force=False):
    """Heartbeat (``submit-info``) plus refresh of the peer list and the channel directory."""
    now = time.time()
    if not node.username or not node.tracker_token:
        return
    if not force and now - node.last_sync < SYNC_EVERY:
        return
    node.last_sync = now
    try:
        if force or now - node.last_submit >= HEARTBEAT_EVERY:
            ip, port = node.address
            resp = await tracker_call("POST", "/submit-info", {"ip": ip, "port": port})
            if resp.ok:
                node.last_submit = now
        listing = await tracker_call("GET", "/get-list")
        chans = await tracker_call("GET", "/channels")
    except PeerError:
        return                              # keep chatting with the cached state
    if listing.status == 401:               # the tracker session expired: force a new login
        with node.lock:
            node.reset()
        return
    if listing.ok:
        with node.lock:
            node.known = {p["username"]: {"ip": p["ip"], "port": p["port"]}
                          for p in listing.json().get("peers", []) if p["username"] != node.username}
    if chans.ok:
        with node.lock:
            fresh = {c["name"]: c for c in chans.json().get("channels", [])}
            for name, chan in list(node.channels.items()):
                if not name.startswith("dm:") and name not in fresh and name != GENERAL:
                    del node.channels[name]
            node.channels.update(fresh)


async def verify_peer(username, token):
    """Is ``token`` the proof of a live session of ``username``? (cached; asks the tracker)."""
    now = time.time()
    with node.lock:
        known = node.connected.get(username)
        if known and known["token"] == token:
            return True                     # already authenticated: works even if the tracker is down
        if node.verified.get((username, token), 0) > now:
            return True
    try:
        resp = await tracker_call("POST", "/verify", {"username": username, "p2p_token": token})
    except PeerError:
        return False
    if resp.ok:
        with node.lock:
            node.verified[(username, token)] = now + VERIFY_CACHE
        return True
    return False


def channel_members(channel):
    """Usernames that must receive a message of ``channel`` (empty list = nobody known)."""
    info = node.channels.get(channel)
    if channel.startswith("dm:"):
        return [u for u in channel.split(":")[1:] if u != node.username]
    if info is None:
        return []
    if info["members"] == "all":
        return list(node.connected)
    return [u for u in info["members"] if u != node.username]


# ------------------------------------------------------------------ peer connection
async def connect_to(username):
    """
    Open a P2P link to ``username`` (the address comes from the tracking list).

    :rtype tuple: ``(ok, message)``
    """
    if username == node.username:
        return False, "cannot connect to yourself"
    if username not in node.known:
        await sync_with_tracker(force=True)
    target = node.known.get(username)
    if target is None:
        return False, "{} is not online".format(username)
    ip, port = node.address
    private, public = secure.new_keypair()
    try:
        resp = await async_http_request(
            target["ip"], target["port"], "POST", "/peer-hello",
            {"username": node.username, "p2p_token": node.p2p_token, "ip": ip, "port": port,
             "pub": public})
    except PeerError as exc:
        return False, str(exc)
    if not resp.ok:
        return False, resp.json().get("error", "peer refused (HTTP {})".format(resp.status))
    reply = resp.json()
    if reply.get("username") != username:
        return False, "the daemon at {}:{} is not {}".format(target["ip"], target["port"], username)
    try:
        key = secure.derive_key(private, reply.get("pub", ""), node.username, username)
    except (ValueError, TypeError):
        return False, "peer sent an invalid public key"
    with node.lock:
        first = username not in node.connected
        node.connected[username] = {"ip": target["ip"], "port": target["port"],
                                    "token": reply.get("p2p_token", ""), "since": time.time(), "key": key}
    if first:
        add_message(GENERAL, "system", "connected to {} (encrypted link)".format(username), kind="system")
    return True, "connected"


async def deliver(username, payload, path="/peer-message"):
    """
    POST one encrypted payload to a connected peer.

    A refused request is retransmitted (up to ``RETRIES`` times); an unreachable peer is
    dropped from the connected set and reported as ``failed``.
    """
    link = node.connected.get(username)
    if link is None:
        return "offline"
    envelope = {"username": node.username, "p2p_token": node.p2p_token}
    envelope.update(secure.seal(link["key"], payload, node.username, username))
    for attempt in range(RETRIES):
        try:
            resp = await async_http_request(link["ip"], link["port"], "POST", path, envelope)
        except PeerError:
            with node.lock:
                node.connected.pop(username, None)
            return "failed"
        if resp.ok:
            return "ok"
        if resp.status in (400, 403):          # permanent refusal, retrying is pointless
            break
    return "failed"


def open_envelope(data):
    """
    Authenticate and decrypt a peer-to-peer envelope.

    :rtype tuple: ``(sender, payload, None)`` on success, ``(None, None, (status, error))`` otherwise.
    """
    sender, token = str(data.get("username", "")), str(data.get("p2p_token", ""))
    link = node.connected.get(sender)
    if link is None or link["token"] != token:
        return None, None, (403, {"ok": False, "error": "say /peer-hello first"})
    try:
        return sender, secure.unseal(link["key"], data, sender, node.username), None
    except (secure.InvalidTag, ValueError, KeyError, TypeError):
        return None, None, (400, {"ok": False, "error": "payload could not be decrypted"})


async def post_message(channel, text):
    """Store a message locally and push it to every connected member concurrently."""
    msg = add_message(channel, node.username, text)
    recipients = channel_members(channel)
    payload = {"channel": channel, "text": text, "mid": msg["mid"], "ts": msg["ts"]}
    results = await asyncio.gather(*(deliver(u, payload) for u in recipients))
    delivery = dict(zip(recipients, results))
    with node.lock:
        msg["delivery"] = delivery
    return msg


# ---------------------------------------------------------------------- web pages
@app.route('/', methods=['GET'])
def index(headers, body, req):
    """Chat page for a logged-in user, otherwise the login page."""
    page = "chat.html" if current_user(req) else "login.html"
    return 200, read_www(page), {"Cache-Control": "no-store"}


@app.route('/login', methods=['POST'])
async def login(headers, body, req):
    """
    Log in through the tracker (client-server phase), then register this peer.

    The tracker's reply is mirrored in a local cookie ``psid_<port>`` for the browser.
    """
    data = req.payload()
    is_form = "x-www-form-urlencoded" in req.headers.get("content-type", "").lower()
    username = str(data.get("username", "")).strip()
    if node.username and node.username != username and node.sessions.tokens_of(node.username):
        return 409, {"ok": False, "error": "this daemon is in use by {}".format(node.username)}
    try:
        resp = await tracker_call("POST", "/login",
                                  {"username": username, "password": str(data.get("password", ""))},
                                  cookie=False)
    except PeerError as exc:
        return 503, {"ok": False, "error": "tracker unreachable: {}".format(exc)}
    reply = resp.json()
    if not resp.ok:
        return resp.status, {"ok": False, "error": reply.get("error", "login failed")}

    with node.lock:
        node.reset()
        node.username = reply["username"]
        node.tracker_token = reply["token"]
        node.p2p_token = reply["p2p_token"]
    token = node.sessions.create(node.username)
    await sync_with_tracker(force=True)
    cookie = {"Set-Cookie": build_set_cookie(node.cookie, token, SESSION_TTL)}
    if is_form:
        cookie["Location"] = "/"
        return 302, "", cookie
    return 200, {"ok": True, "username": node.username}, cookie


@app.route('/logout', methods=['POST'])
async def logout(headers, body, req):
    user = current_user(req, mutate=True)
    if not user:
        return denied(req)
    try:
        await tracker_call("POST", "/logout")
    except PeerError:
        pass
    node.sessions.destroy(req.cookies.get(node.cookie))
    with node.lock:
        node.reset()
    return 200, {"ok": True}, {"Set-Cookie": build_set_cookie(node.cookie, "", max_age=0)}


# ------------------------------------------------------------- browser-facing API
@app.route('/get-list', methods=['GET'])
async def get_list(headers, body, req):
    """Peer discovery: the tracker's active peers, flagged with the live P2P links."""
    if not current_user(req):
        return denied(req)
    await sync_with_tracker(force=True)
    peers = [{"username": u, "ip": p["ip"], "port": p["port"], "connected": u in node.connected}
             for u, p in sorted(node.known.items())]
    return {"ok": True, "me": node.username, "tracker_ok": node.tracker_ok, "peers": peers}


@app.route('/connect-peer', methods=['POST'])
async def connect_peer(headers, body, req):
    """Connection setup: body ``{"username": "bob"}`` or ``{"all": true}``."""
    if not current_user(req, mutate=True):
        return denied(req)
    data = req.payload()
    await sync_with_tracker(force=True)
    if data.get("all"):
        names = [u for u in node.known if u not in node.connected]
        results = await asyncio.gather(*(connect_to(u) for u in names))
        return {"ok": True, "results": {n: {"ok": r[0], "message": r[1]} for n, r in zip(names, results)}}
    username = str(data.get("username", "")).strip()
    if not username:
        return 400, {"ok": False, "error": "username is required"}
    ok, message = await connect_to(username)
    return (200 if ok else 502), {"ok": ok, "message": message, "username": username}


def _clean_text(data):
    text = str(data.get("text", "")).strip()
    return text if 0 < len(text) <= MAX_TEXT else None


@app.route('/broadcast-peer', methods=['POST'])
async def broadcast_peer(headers, body, req):
    """
    Send a message to every member of a channel over the direct P2P links.

    Body: ``{"channel": "#general", "text": "hello"}``. Messages are immutable once sent.
    """
    if not current_user(req, mutate=True):
        return denied(req)
    data = req.payload()
    channel, text = str(data.get("channel", GENERAL)), _clean_text(data)
    if text is None:
        return 400, {"ok": False, "error": "text must be 1..{} characters".format(MAX_TEXT)}
    if channel.startswith("dm:") or channel not in node.channels:
        await sync_with_tracker(force=True)
        if channel.startswith("dm:") or channel not in node.channels:
            return 404, {"ok": False, "error": "you have not joined {}".format(channel)}
    msg = await post_message(channel, text)
    return {"ok": True, "message": msg,
            "delivered": [u for u, s in msg["delivery"].items() if s == "ok"],
            "failed": [u for u, s in msg["delivery"].items() if s != "ok"]}


@app.route('/send-peer', methods=['POST'])
async def send_peer(headers, body, req):
    """Direct (unicast) message: body ``{"to": "bob", "text": "hi"}``; connects first if needed."""
    if not current_user(req, mutate=True):
        return denied(req)
    data = req.payload()
    target, text = str(data.get("to", "")).strip(), _clean_text(data)
    if not target or target == node.username:
        return 400, {"ok": False, "error": "a different recipient is required"}
    if text is None:
        return 400, {"ok": False, "error": "text must be 1..{} characters".format(MAX_TEXT)}
    if target not in node.connected:
        ok, message = await connect_to(target)
        if not ok:
            return 502, {"ok": False, "error": message}
    channel = dm_name(node.username, target)
    msg = await post_message(channel, text)
    status = 200 if msg["delivery"].get(target) == "ok" else 502
    return status, {"ok": status == 200, "message": msg}


@app.route('/channels', methods=['POST'])
async def create_channel(headers, body, req):
    """Create a channel / add members through the tracker: ``{"name": "project", "members": ["bob"]}``."""
    if not current_user(req, mutate=True):
        return denied(req)
    data = req.payload()
    name = str(data.get("name", "")).strip().lower()
    name = name if name.startswith("#") else "#" + name
    if not CHANNEL_RE.match(name):
        return 400, {"ok": False, "error": "channel name: letters, digits, - and _ (max 30)"}
    members = data.get("members", [])
    if isinstance(members, str):
        members = [m.strip() for m in members.split(",") if m.strip()]
    try:
        resp = await tracker_call("POST", "/add-list",
                                  {"channel": name, "members": members, "private": bool(data.get("private"))})
    except PeerError as exc:
        return 503, {"ok": False, "error": "tracker unreachable: {}".format(exc)}
    if not resp.ok:
        return resp.status, {"ok": False, "error": resp.json().get("error", "refused")}
    await sync_with_tracker(force=True)
    return {"ok": True, "channel": resp.json()}


@app.route('/api/state', methods=['GET'])
async def state(headers, body, req):
    """
    Polled by the browser (``?since=<last message id>``): peers, channels and new messages.

    Doubles as the heartbeat: an open page keeps this peer registered at the tracker.
    """
    if not current_user(req):
        return denied(req)
    await sync_with_tracker()
    try:
        since = int(req.query.get("since", 0))
    except ValueError:
        since = 0
    with node.lock:
        peers = sorted({*node.known, *node.connected})
        channels = [dict(c) for c in node.channels.values()]
        for other in node.connected:
            channels.append({"name": dm_name(node.username, other), "owner": "", "private": True,
                             "members": [node.username, other], "dm": other})
        for msg in node.log:
            if msg["channel"].startswith("dm:") and msg["channel"] not in {c["name"] for c in channels}:
                other = [u for u in msg["channel"].split(":")[1:] if u != node.username][0]
                channels.append({"name": msg["channel"], "owner": "", "private": True,
                                 "members": [node.username, other], "dm": other})
        return {
            "ok": True,
            "me": node.username,
            "address": "{}:{}".format(*node.address),
            "tracker": "{}:{}".format(*node.tracker),
            "tracker_ok": node.tracker_ok,
            "peers": [{"username": u, "online": u in node.known,
                       "connected": u in node.connected} for u in peers],
            "channels": channels,
            "messages": [m for m in node.log if m["id"] > since],
            "last_id": node.next_id - 1,
        }


# ---------------------------------------------------------- peer <-> peer protocol
@app.route('/peer-hello', methods=['POST'])
async def peer_hello(headers, body, req):
    """Another peer opens a link: its identity is checked with the tracker."""
    if not node.username:
        return 503, {"ok": False, "error": "daemon not logged in"}
    data = req.payload()
    sender, token = str(data.get("username", "")), str(data.get("p2p_token", ""))
    if not sender or sender == node.username or not await verify_peer(sender, token):
        return 403, {"ok": False, "error": "identity could not be verified"}
    try:
        link = {"ip": str(data["ip"]), "port": int(data["port"]), "token": token, "since": time.time()}
        private, public = secure.new_keypair()
        link["key"] = secure.derive_key(private, str(data["pub"]), node.username, sender)
    except (KeyError, ValueError, TypeError):
        return 400, {"ok": False, "error": "ip, port and a valid pub key are required"}
    # the advertised address must be the one the tracker holds for that user (no redirection)
    if sender not in node.known:
        await sync_with_tracker(force=True)
    registered = node.known.get(sender)
    if registered is None or registered["ip"] != link["ip"] or registered["port"] != link["port"]:
        return 403, {"ok": False, "error": "address does not match the tracking list"}
    with node.lock:
        first = sender not in node.connected
        node.connected[sender] = link
    if first:
        add_message(GENERAL, "system", "{} connected to you".format(sender), kind="system")
    return {"ok": True, "username": node.username, "p2p_token": node.p2p_token, "pub": public}


async def check_channel(channel, sender):
    """Access control: a DM must involve both of us, a group channel must list the sender."""
    if channel.startswith("dm:"):
        if set(channel.split(":")[1:]) != {sender, node.username}:
            return 403, {"ok": False, "error": "not your conversation"}
    elif channel != GENERAL:
        if channel not in node.channels:
            await sync_with_tracker(force=True)
        info = node.channels.get(channel)
        if info is None or (info["members"] != "all" and sender not in info["members"]):
            return 403, {"ok": False, "error": "{} is not a member of {}".format(sender, channel)}
    return None


@app.route('/peer-message', methods=['POST'])
async def peer_message(headers, body, req):
    """A connected peer delivers an encrypted chat message (authenticated by its p2p token + AES-GCM)."""
    if not node.username:
        return 503, {"ok": False, "error": "daemon not logged in"}
    sender, payload, error = open_envelope(req.payload())
    if error:
        return error
    channel, text = str(payload.get("channel", "")), _clean_text(payload)
    if text is None or not channel:
        return 400, {"ok": False, "error": "channel and text are required"}
    denied_access = await check_channel(channel, sender)
    if denied_access:
        return denied_access
    try:
        ts = float(payload.get("ts", time.time()))
    except (TypeError, ValueError):
        ts = time.time()
    add_message(channel, sender, text, mid=str(payload.get("mid") or uuid.uuid4().hex), ts=ts)
    return {"ok": True}


# ---------------------------------------------------------------- file transfer
def safe_name(name):
    """Strip any path and unusual characters from an uploaded file name."""
    name = SAFE_NAME.sub("_", os.path.basename(str(name))).strip(". ") or "file"
    return name[:100]


@app.route('/send-file', methods=['POST'])
async def send_file(headers, body, req):
    """
    Browser -> daemon: one segment of a file to send to a channel / peer.

    Body: ``{"channel", "fid", "name", "size", "idx", "total", "data": base64}``.
    Segments arrive in order. The daemon hashes them as they stream through, forwards each one
    (encrypted, with retransmission) to every recipient and attaches the SHA-256 of the whole
    file to the last segment so the receiver can verify the reassembled file.
    """
    if not current_user(req, mutate=True):
        return denied(req)
    d = req.payload()
    try:
        fid, idx, total, size = str(d["fid"]), int(d["idx"]), int(d["total"]), int(d["size"])
        raw = base64.b64decode(d["data"], validate=True)
        channel, name = str(d["channel"]), safe_name(d["name"])
    except (KeyError, ValueError, TypeError):
        return 400, {"ok": False, "error": "malformed segment"}
    if not re.fullmatch(r"[0-9a-f]{8,64}", fid):
        return 400, {"ok": False, "error": "bad file id"}
    if not (0 <= idx < total) or not 0 <= size <= MAX_FILE_BYTES or len(raw) > CHUNK_BYTES * 2:
        return 400, {"ok": False, "error": "segment out of range (max {} MB)".format(MAX_FILE_BYTES >> 20)}
    if channel.startswith("dm:") and node.username not in channel.split(":")[1:]:
        return 403, {"ok": False, "error": "not your conversation"}
    if not channel.startswith("dm:") and channel not in node.channels:
        return 404, {"ok": False, "error": "you have not joined {}".format(channel)}

    with node.lock:
        job = node.outbox.setdefault(fid, {"hash": hashlib.sha256(), "next": 0, "bytes": 0, "sha": ""})
        if idx > job["next"]:
            return 409, {"ok": False, "error": "segments must be sent in order", "expected": job["next"]}
        if idx == job["next"]:                    # a repeated idx is a retransmission: do not hash twice
            job["hash"].update(raw)
            job["bytes"] += len(raw)
            job["next"] += 1
            if job["next"] == total:
                if job["bytes"] != size:
                    return 400, {"ok": False, "error": "announced size does not match the data"}
                job["sha"] = job["hash"].hexdigest()
        sha = job["sha"] if idx == total - 1 else ""

    recipients = channel_members(channel)
    payload = {"channel": channel, "fid": fid, "name": name, "size": size, "sha256": sha,
               "idx": idx, "total": total, "data": d["data"]}
    results = await asyncio.gather(*(deliver(u, payload, "/peer-file") for u in recipients))
    delivery = dict(zip(recipients, results))
    if idx == total - 1:
        msg = add_message(channel, node.username, name, mid="file-" + fid, kind="file", delivery=delivery,
                          extra={"file": {"fid": fid, "name": name, "size": size, "sha256": sha}})
        if msg is None:                           # retransmission of the last segment: refresh the status
            with node.lock:
                for old in node.log:
                    if old["mid"] == "file-" + fid:
                        old["delivery"] = delivery
    failed = [u for u, s in delivery.items() if s != "ok"]
    return {"ok": not failed, "idx": idx, "delivery": delivery, "failed": failed}


@app.route('/peer-file', methods=['POST'])
async def peer_file(headers, body, req):
    """Receive one encrypted file segment, verify the whole file on the last one."""
    if not node.username:
        return 503, {"ok": False, "error": "daemon not logged in"}
    sender, p, error = open_envelope(req.payload())
    if error:
        return error
    try:
        channel, fid, name = str(p["channel"]), str(p["fid"]), safe_name(p["name"])
        idx, total, size, sha = int(p["idx"]), int(p["total"]), int(p["size"]), str(p.get("sha256", ""))
        raw = base64.b64decode(p["data"], validate=True)
    except (KeyError, ValueError, TypeError):
        return 400, {"ok": False, "error": "malformed segment"}
    if not re.fullmatch(r"[0-9a-f]{8,64}", fid) or not (0 <= idx < total) or size > MAX_FILE_BYTES:
        return 400, {"ok": False, "error": "segment out of range"}
    denied_access = await check_channel(channel, sender)
    if denied_access:
        return denied_access

    with node.lock:
        job = node.inbox.setdefault((sender, fid), {"chunks": {}, "name": name, "size": size,
                                                    "sha": "", "total": total, "channel": channel})
        if job["total"] != total or job["size"] != size:
            return 400, {"ok": False, "error": "segment does not match the announced file"}
        job["chunks"][idx] = raw                  # a retransmitted segment simply overwrites
        if sha:
            job["sha"] = sha                      # only the last segment carries the file hash
        have = len(job["chunks"])
        if have < total or not job["sha"]:
            return {"ok": True, "have": have, "total": total}
        sha = job["sha"]
        data = b"".join(job["chunks"][i] for i in range(total))
        del node.inbox[(sender, fid)]

    if len(data) != size or hashlib.sha256(data).hexdigest() != sha:
        add_message(channel, "system", "file '{}' from {} failed the integrity check".format(name, sender),
                    kind="system")
        return 422, {"ok": False, "error": "integrity check failed", "sha_ok": False}
    folder = os.path.join(FILES_DIR, node.username)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, fid + "_" + name), "wb") as handle:
        handle.write(data)
    add_message(channel, sender, name, mid="file-" + fid, kind="file",
                extra={"file": {"fid": fid, "name": name, "size": size, "sha256": sha}})
    return {"ok": True, "complete": True, "sha_ok": True}


@app.route('/download', methods=['GET'])
def download(headers, body, req):
    """Hand a received file to the browser (``GET /download?fid=<id>``)."""
    if not current_user(req):
        return denied(req)
    fid = req.query.get("fid", "")
    if not re.fullmatch(r"[0-9a-f]{8,64}", fid):
        return 400, {"ok": False, "error": "bad file id"}
    folder = os.path.join(FILES_DIR, node.username or "")
    for entry in (os.listdir(folder) if os.path.isdir(folder) else []):
        if entry.startswith(fid + "_"):
            with open(os.path.join(folder, entry), "rb") as handle:
                content = handle.read()
            name = entry[len(fid) + 1:]
            ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
            return 200, content, {"Content-Type": ctype,
                                  "Content-Disposition": 'attachment; filename="{}"'.format(name),
                                  "X-Content-Type-Options": "nosniff"}
    return 404, {"ok": False, "error": "unknown file"}


def create_peer(ip, port, tracker, advertise_ip="127.0.0.1", mode=None):
    """
    Launch a peer daemon.

    :param ip (str): bind address of the web server.
    :param port (int): listening port (also advertised to the tracker).
    :param tracker (str): ``host:port`` of the tracker.
    :param advertise_ip (str): address other peers must use to reach this daemon.
    """
    host, _, tport = tracker.rpartition(":")
    configure(host, tport, advertise_ip, port)
    app.prepare_address(ip, port, mode)
    app.run()
