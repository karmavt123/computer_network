"""Web UI and HTTP API of one peer daemon.

The browser only talks to its own peer daemon over HTTP; everything else
(tracker calls, P2P frames, UDP file transfer) is done by the daemon.

=================================  ==============================================
Route                              Purpose
=================================  ==============================================
``POST /login`` ``/api/login``     sign in (password checked by the tracker),
                                   sets an HttpOnly session cookie
``POST /logout`` ``/api/logout``   sign out
``GET  /api/me``                   current user of this browser
``GET  /get-list`` ``/api/peers``  peers from the tracker (+ connected flag)
``POST /connect-peer``             CONNECT handshake with one peer or all peers
``POST /broadcast-peer``           message to all connected peers in a channel
``POST /send-peer``                direct message to one peer
``POST /api/send``                 either of the two above (used by the UI)
``GET  /api/messages``             new messages: ``?after=<seq>&channel=<id>``
``GET  /api/status``               daemon status for the header
``POST /api/files/send``           start a UDP transfer (binary body)
``GET  /api/transfers``            progress of all transfers
``GET  /api/files``                received files
``GET  /api/files/download``       download a received file: ``?name=<file>``
=================================  ==============================================
"""

import json
import mimetypes
import re
from pathlib import Path
from urllib.parse import quote, unquote

from chat.accounts import Sessions
from chat.peer import CHANNELS, ChatNode, TrackerUnavailable
from chat.udp_files import MAX_FILE_SIZE
from daemon import StreapRous

app = StreapRous()
sessions = Sessions()
node = None
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def reply(data, status=200, headers=None):
    """JSON response tuple understood by the HTTP adapter."""
    return (status, "application/json; charset=utf-8",
            json.dumps(data, ensure_ascii=False).encode("utf-8"), headers or {})


def parse(body):
    """Decode a JSON object body, or return None."""
    try:
        value = json.loads(body or "{}")
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def current_user(headers):
    """User of the request's session, if it is the user signed in on this node."""
    username = sessions.username(headers)
    return username if username and username == node.username else None


def login_required(handler):
    """Decorator: answer 401 unless the request carries a valid session."""
    def wrapper(headers, body):
        username = current_user(headers)
        if not username:
            return reply({"error": "Login required"}, 401)
        return handler(headers, body, username)
    wrapper.__name__ = handler.__name__
    wrapper.__doc__ = handler.__doc__
    return wrapper


def routes(*paths, methods=("POST",)):
    """Register one handler under several paths."""
    def decorator(func):
        for path in paths:
            app.route(path, methods=methods)(func)
        return func
    return decorator


# ----------------------------------------------------------------------
# static files of the single-page UI
# ----------------------------------------------------------------------
def _static(name, content_type):
    return (200, content_type, (STATIC_DIR / name).read_bytes(), {})


@app.route("/", methods=["GET"])
def home(headers, body):
    """The chat page."""
    return _static("chat.html", "text/html; charset=utf-8")


@app.route("/assets/app.js", methods=["GET"])
def javascript(headers, body):
    """Client-side JavaScript."""
    return _static("app.js", "text/javascript; charset=utf-8")


@app.route("/assets/styles.css", methods=["GET"])
def stylesheet(headers, body):
    """Style sheet."""
    return _static("chat.css", "text/css; charset=utf-8")


# ----------------------------------------------------------------------
# authentication
# ----------------------------------------------------------------------
@routes("/login", "/api/login")
def login(headers, body):
    """Check the credentials at the tracker, then start a cookie session."""
    data = parse(body)
    if (not data or not isinstance(data.get("username"), str)
            or not isinstance(data.get("password"), str)):
        return reply({"error": "Username and password are required"}, 400)
    try:
        username = node.login(data["username"], data["password"])
    except TrackerUnavailable as exc:
        status = exc.status if exc.status in (400, 401, 403) else 503
        return reply({"error": str(exc)}, status)
    cookie = sessions.create(username)
    return reply({"ok": True, "username": username, "peer_id": node.peer_id}, 200, {
        "Set-Cookie": "{}={}; HttpOnly; SameSite=Lax; Path=/; Max-Age=28800".format(
            sessions.cookie_name, cookie)})


@routes("/logout", "/api/logout")
def logout(headers, body):
    """End the session and tell the tracker."""
    if current_user(headers):
        node.logout()
    sessions.destroy(headers)
    return reply({"ok": True}, headers={
        "Set-Cookie": "{}=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0".format(
            sessions.cookie_name)})


@app.route("/api/me", methods=["GET"])
@login_required
def me(headers, body, username):
    """Who is signed in."""
    return reply({"username": username, "peer_id": node.peer_id})


# ----------------------------------------------------------------------
# peers and messages
# ----------------------------------------------------------------------
@routes("/get-list", "/api/peers", methods=("GET",))
@login_required
def peers(headers, body, username):
    """Peer list from the tracker; the cached list if the tracker is down."""
    try:
        node.refresh_peers()
        return reply({"peers": node.peers(), "tracker_online": True})
    except TrackerUnavailable as exc:
        return reply({"peers": node.peers(), "tracker_online": False,
                      "warning": "Tracker unavailable: {}".format(exc)})


@routes("/connect-peer", "/api/connect-peer")
@login_required
def connect_peer(headers, body, username):
    """CONNECT handshake with ``peer_id``, or with every peer if ``all`` is true."""
    data = parse(body) or {}
    if data.get("all"):
        return reply({"ok": True, "results": node.connect_all()})
    if not isinstance(data.get("peer_id"), str):
        return reply({"error": "peer_id is required"}, 400)
    try:
        return reply({"ok": True, "connection": node.connect(data["peer_id"])})
    except LookupError as exc:
        return reply({"error": str(exc)}, 404)
    except (OSError, ValueError, ConnectionError) as exc:
        return reply({"error": "Could not connect: {}".format(exc)}, 502)


def _send(channel, text, recipient):
    try:
        message, report = node.send(channel, text, recipient=recipient)
    except LookupError as exc:
        return reply({"error": str(exc)}, 404)
    except (ValueError, PermissionError) as exc:
        return reply({"error": str(exc)}, 400)
    return reply({"ok": True, "message": message, "delivery": report})


@routes("/api/send")
@login_required
def send_message(headers, body, username):
    """Broadcast (``channel``) or direct message (``peer_id``)."""
    data = parse(body) or {}
    recipient = data.get("peer_id")
    if recipient is not None and not isinstance(recipient, str):
        return reply({"error": "peer_id must be a string"}, 400)
    return _send(data.get("channel"), data.get("text"), recipient)


@routes("/broadcast-peer")
@login_required
def broadcast_peer(headers, body, username):
    """Broadcast to all connected peers (assignment API name)."""
    data = parse(body) or {}
    return _send(data.get("channel", "general"), data.get("text"), None)


@routes("/send-peer")
@login_required
def send_peer(headers, body, username):
    """Direct message to one peer (assignment API name)."""
    data = parse(body) or {}
    if not isinstance(data.get("peer_id"), str):
        return reply({"error": "peer_id is required"}, 400)
    return _send(None, data.get("text"), data["peer_id"])


@app.route("/api/channels", methods=["GET"])
@login_required
def channels(headers, body, username):
    """Joined channels."""
    return reply({"channels": [{"id": c, "name": "# " + c} for c in CHANNELS]})


@app.route("/api/messages", methods=["GET"])
@login_required
def messages(headers, body, username):
    """Messages newer than the cursor ``after`` (a local sequence number)."""
    query = headers.get(":query") or {}
    try:
        after = max(0, int(query.get("after", headers.get("x-since", "0"))))
    except ValueError:
        after = 0
    channel = query.get("channel") or headers.get("x-channel") or None
    return reply({"messages": node.messages(after, channel)})


@app.route("/api/status", methods=["GET"])
@login_required
def status(headers, body, username):
    """Daemon status."""
    return reply(node.status())


# ----------------------------------------------------------------------
# files
# ----------------------------------------------------------------------
@app.route("/api/files/send", methods=["POST"])
@login_required
def send_file(headers, body, username):
    """Start a UDP transfer of the request body; returns at once (202)."""
    if not isinstance(body, bytes):
        return reply({"error": "Send the file as application/octet-stream"}, 400)
    if len(body) > MAX_FILE_SIZE:
        return reply({"error": "Maximum file size is 100 MiB"}, 413)
    peer_id = headers.get("x-peer-id", "")
    filename = Path(unquote(headers.get("x-filename", "file.bin"))).name[:180] or "file.bin"
    if not peer_id:
        return reply({"error": "Choose a destination peer"}, 400)
    try:
        transfer_id = node.send_file(peer_id, body, filename)
    except LookupError as exc:
        return reply({"error": str(exc)}, 404)
    return reply({"ok": True, "transfer_id": transfer_id}, 202)


@app.route("/api/transfers", methods=["GET"])
@login_required
def transfers(headers, body, username):
    """Progress of outgoing and incoming transfers."""
    return reply({"transfers": node.transfers()})


@app.route("/api/files", methods=["GET"])
@login_required
def files(headers, body, username):
    """Files received by this peer."""
    folder = node.files.download_dir
    items = [{"name": p.name, "size": p.stat().st_size,
              "url": "/api/files/download?name=" + quote(p.name)}
             for p in sorted(folder.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
             if p.is_file() and not p.name.startswith(".")]
    return reply({"files": items})


@app.route("/api/files/download", methods=["GET"])
@login_required
def download(headers, body, username):
    """Return a received file as an attachment."""
    name = (headers.get(":query") or {}).get("name", "")
    try:
        path = node.downloaded_file(name)
    except LookupError as exc:
        return reply({"error": str(exc)}, 404)
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return (200, content_type, path.read_bytes(), {
        "Content-Disposition": "attachment; filename*=UTF-8''" + quote(path.name)})


def create_chat_app(ip, port, peer_id, peer_host, peer_port, file_port,
                    tracker_url, shared_key, download_dir, loss_rate=0.0,
                    mode="coroutine"):
    """Start the peer services and serve the web UI."""
    global node
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", peer_id):
        raise ValueError("--peer-id must use 1-32 letters, numbers, underscore, dot or hyphen")
    # Browsers share cookies across ports of one host, so each peer needs its own name.
    sessions.cookie_name = "session_" + re.sub(r"[^A-Za-z0-9_]", "_", peer_id)
    node = ChatNode(peer_id, peer_host, peer_port, file_port, tracker_url,
                    shared_key, download_dir, loss_rate=loss_rate)
    node.start()
    app.prepare_address(ip, port)
    print("[Chat] Web UI at http://{}:{}; peer {} chat TCP {}:{}, file UDP {}".format(
        ip, port, peer_id, peer_host, node.peer_port, node.file_port))
    try:
        app.run(mode)
    finally:
        node.close()
