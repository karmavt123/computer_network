"""HTTP API of the tracker process (client-server part of the hybrid app).

==========================  =====================================================
Route                       Purpose
==========================  =====================================================
``POST /submit-info``       register a peer or refresh it (heartbeat);
``POST /add-list``          the first call returns the peer token
``GET  /get-list``          live peers: id, user, IP, TCP and UDP ports
``POST /login``             check a user's password; record the user on the peer
``POST /logout``            clear the user of a peer
``POST /remove``            graceful leave of a peer
``POST /relay``             fallback: forward an (encrypted) chat frame to peers
``POST /broadcast``         that the sender cannot reach directly
``GET  /``                  human-readable status page
==========================  =====================================================

Every call that changes a peer record must carry the peer token.
"""

import html
import ipaddress
import json
import re
import socket
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from chat.accounts import USERNAME_PATTERN, UserStore
from chat.protocol import CHAT_REQUIRED_FIELDS, ProtocolError, send_frame
from chat.tracker import Tracker, TrackerError
from daemon import StreapRous

PEER_ID_PATTERN = r"[A-Za-z0-9_.-]{1,32}"
DEFAULT_USERS = Path(__file__).resolve().parent.parent / "db" / "users.json"
RELAY_TIMEOUT = 2

app = StreapRous()
tracker = Tracker()
users = None
_relay_pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="relay")


def reply(data, status=200):
    """JSON response tuple understood by the HTTP adapter."""
    return (status, "application/json; charset=utf-8",
            json.dumps(data, ensure_ascii=False).encode("utf-8"), {})


def parse(body):
    """Decode a JSON object body, or return None."""
    try:
        value = json.loads(body or "{}")
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def valid_host(host):
    """True for an IP address or a plausible host name."""
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return bool(re.fullmatch(r"[A-Za-z0-9.-]{1,253}", host))


def routes(*paths, methods=("POST",)):
    """Register one handler under each path, with and without a trailing slash."""
    def decorator(func):
        for path in paths:
            app.route(path, methods=methods)(func)
            app.route(path + "/", methods=methods)(func)
        return func
    return decorator


def owned(data):
    """Extract ``(peer_id, token)`` from a request body."""
    return str(data.get("peer_id", "")), data.get("token")


@routes("/submit-info", "/add-list")
def submit_info(headers, body):
    """Register a peer or refresh its heartbeat."""
    data = parse(body)
    if data is None:
        return reply({"error": "Expected a JSON object"}, 400)
    peer_id, host = str(data.get("peer_id", "")), str(data.get("host", ""))
    try:
        port, udp_port = int(data.get("port", 0)), int(data.get("udp_port", 0))
    except (TypeError, ValueError):
        port = udp_port = 0
    if (not re.fullmatch(PEER_ID_PATTERN, peer_id) or not valid_host(host)
            or not 1 <= port <= 65535 or not 1 <= udp_port <= 65535):
        return reply({"error": "Invalid peer_id, host or port"}, 400)
    try:
        token, peers = tracker.register(
            {"peer_id": peer_id, "host": host, "port": port, "udp_port": udp_port},
            token=data.get("token"), observed_ip=headers.get(":client"))
    except TrackerError as exc:
        return reply({"error": str(exc)}, exc.status)
    return reply({"ok": True, "token": token, "peers": peers})


@routes("/get-list", methods=("GET",))
def get_list(headers, body):
    """List live peers."""
    return reply({"peers": tracker.list_peers()})


@routes("/login")
def login(headers, body):
    """Check credentials and bind the user to the calling peer."""
    data = parse(body)
    if data is None:
        return reply({"error": "Expected a JSON object"}, 400)
    username, password = data.get("username"), data.get("password")
    if (not isinstance(username, str) or not isinstance(password, str)
            or not re.fullmatch(USERNAME_PATTERN, username)):
        return reply({"error": "Username and password are required"}, 400)
    if not users.check(username, password):
        return reply({"error": "Invalid username or password"}, 401)
    try:
        tracker.bind_user(*owned(data), username)
    except TrackerError as exc:
        return reply({"error": str(exc)}, exc.status)
    return reply({"ok": True, "username": username})


@routes("/logout")
def logout(headers, body):
    """Clear the user of the calling peer."""
    data = parse(body) or {}
    try:
        tracker.unbind_user(*owned(data))
    except TrackerError as exc:
        return reply({"error": str(exc)}, exc.status)
    return reply({"ok": True})


@routes("/remove")
def remove_peer(headers, body):
    """Remove the calling peer (graceful shutdown)."""
    data = parse(body) or {}
    try:
        tracker.unregister(*owned(data))
    except TrackerError as exc:
        return reply({"error": str(exc)}, exc.status)
    return reply({"ok": True})


def _relay_one(peer, frame):
    with socket.create_connection((peer["host"], int(peer["port"])),
                                  timeout=RELAY_TIMEOUT) as conn:
        send_frame(conn, frame)


@routes("/relay", "/broadcast")
def relay(headers, body):
    """Forward an encrypted chat frame to peers the sender cannot reach.

    The tracker cannot read the frame (it has no chat key); it only checks
    that the caller owns the origin peer id and copies the frame.
    """
    data = parse(body)
    if data is None or not isinstance(data.get("frame"), dict):
        return reply({"error": "Expected {peer_id, token, targets, frame}"}, 400)
    peer_id, token = owned(data)
    frame = data["frame"]
    if not tracker.verify(peer_id, token):
        return reply({"error": "Unknown peer or wrong token"}, 403)
    if frame.get("type") != "CHAT" or frame.get("origin") != peer_id or not all(
            key in frame for key in CHAT_REQUIRED_FIELDS):
        return reply({"error": "Invalid chat frame"}, 400)
    wanted = data.get("targets")
    peers = [p for p in tracker.list_peers() if p["peer_id"] != peer_id
             and (not isinstance(wanted, list) or p["peer_id"] in wanted)]
    futures = {p["peer_id"]: _relay_pool.submit(_relay_one, p, frame) for p in peers}
    delivered, errors = [], {}
    for target, future in futures.items():
        try:
            future.result()
            delivered.append(target)
        except (OSError, ProtocolError) as exc:
            errors[target] = str(exc)
    return reply({"ok": True, "delivered": delivered, "errors": errors})


@app.route("/", methods=["GET"])
def home(headers, body):
    """Status page: the peers the tracker currently knows."""
    rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}:{}</td><td>{}</td><td>{}</td></tr>".format(
            html.escape(p["peer_id"]), html.escape(p["username"] or "-"),
            html.escape(p["host"]), p["port"], p["udp_port"],
            html.escape(str(p["observed_ip"] or "-")))
        for p in tracker.list_peers()) or "<tr><td colspan=5>No peer registered</td></tr>"
    return ("<!doctype html><meta charset=utf-8><meta http-equiv=refresh content=5>"
            "<title>Tracker</title><style>body{font-family:system-ui;margin:2rem}"
            "td,th{border-bottom:1px solid #ddd;padding:.4rem .8rem;text-align:left}"
            "</style><h1>Tracker</h1><table><tr><th>Peer</th><th>User</th>"
            "<th>TCP chat</th><th>UDP file</th><th>Seen from</th></tr>"
            + rows + "</table>")


def create_tracker(ip, port, users_path=DEFAULT_USERS, mode="coroutine"):
    """Load the accounts and serve the tracker API."""
    global users
    users = UserStore(users_path)
    if not users.usernames():
        raise SystemExit("No accounts in {}; run tools/add_user.py first".format(users_path))
    app.prepare_address(ip, port)
    print("[Tracker] Listening at {}:{} ({} accounts, mode {})".format(
        ip, port, len(users.usernames()), mode))
    app.run(mode)
