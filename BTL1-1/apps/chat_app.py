"""Authenticated web UI and HTTP API for one P2P chat peer."""

import json
import os
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import unquote

from daemon import StreapRous
from chat.service import ChatNode, Sessions

app = StreapRous()
sessions = Sessions()
node = None
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
CHANNELS = [{"id": "general", "name": "# general"}, {"id": "study", "name": "# study"}]


def reply(data, status=200, headers=None):
    return (status, "application/json; charset=utf-8",
            json.dumps(data, ensure_ascii=False).encode("utf-8"), headers or {})


def parse(body):
    try:
        value = json.loads(body or "{}")
        return value if isinstance(value, dict) else None
    except (TypeError, json.JSONDecodeError):
        return None


def authorized(headers):
    return sessions.username(headers)


@app.route("/", methods=["GET"])
def home(headers, body):
    return (200, "text/html; charset=utf-8", (STATIC_DIR / "chat.html").read_bytes(), {})


@app.route("/assets/app.js", methods=["GET"])
def javascript(headers, body):
    return (200, "text/javascript; charset=utf-8", (STATIC_DIR / "app.js").read_bytes(), {})


@app.route("/assets/styles.css", methods=["GET"])
def stylesheet(headers, body):
    return (200, "text/css; charset=utf-8", (STATIC_DIR / "chat.css").read_bytes(), {})


@app.route("/api/login", methods=["POST"])
def login(headers, body):
    data = parse(body)
    if not data or not isinstance(data.get("username"), str) or not isinstance(data.get("password"), str):
        return reply({"error": "Username and password are required"}, 400)
    cookie = sessions.login(data["username"], data["password"])
    if not cookie:
        return reply({"error": "Invalid username or password"}, 401)
    return reply({"ok": True, "username": data["username"], "peer_id": node.peer_id}, 200,
                 {"Set-Cookie": "{}={}; HttpOnly; SameSite=Lax; Path=/; Max-Age=28800".format(sessions.cookie_name, cookie)})


@app.route("/api/logout", methods=["POST"])
def logout(headers, body):
    sessions.logout(headers)
    return reply({"ok": True}, headers={"Set-Cookie": "{}=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0".format(sessions.cookie_name)})


@app.route("/api/me", methods=["GET"])
def me(headers, body):
    username = authorized(headers)
    if not username:
        return reply({"error": "Login required"}, 401)
    return reply({"username": username, "peer_id": node.peer_id})


@app.route("/api/channels", methods=["GET"])
def channels(headers, body):
    if not authorized(headers):
        return reply({"error": "Login required"}, 401)
    return reply({"channels": CHANNELS})


@app.route("/api/peers", methods=["GET"])
def peers(headers, body):
    if not authorized(headers):
        return reply({"error": "Login required"}, 401)
    try:
        return reply({"peers": node.refresh_peers()})
    except (OSError, ValueError) as exc:
        return reply({"error": "Tracker unavailable: {}".format(exc), "peers": node.peers()}, 503)


@app.route("/api/connect-peer", methods=["POST"])
def connect_peer(headers, body):
    if not authorized(headers):
        return reply({"error": "Login required"}, 401)
    data = parse(body)
    if not data or not isinstance(data.get("peer_id"), str):
        return reply({"error": "peer_id is required"}, 400)
    try:
        return reply({"ok": True, "connection": node.connect(data["peer_id"])})
    except LookupError as exc:
        return reply({"error": str(exc)}, 404)
    except (OSError, ValueError, ConnectionError) as exc:
        return reply({"error": "Could not connect: {}".format(exc)}, 502)


@app.route("/api/messages", methods=["GET"])
def messages(headers, body):
    if not authorized(headers):
        return reply({"error": "Login required"}, 401)
    # The HTTP parser preserves the query string in path; accept cursor via a small header fallback.
    try:
        since = max(0, int(headers.get("x-since", "0")))
    except ValueError:
        since = 0
    channel = headers.get("x-channel")
    return reply({"messages": node.messages(since, channel)})


@app.route("/api/send", methods=["POST"])
def send_message(headers, body):
    username = authorized(headers)
    if not username:
        return reply({"error": "Login required"}, 401)
    data = parse(body)
    if not data or data.get("channel") not in {"general", "study"}:
        return reply({"error": "Choose a valid channel"}, 400)
    text = data.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        return reply({"error": "Message must contain 1 to 2000 characters"}, 400)
    recipient = data.get("peer_id")
    if recipient is not None and not isinstance(recipient, str):
        return reply({"error": "peer_id must be a string"}, 400)
    try:
        message, failures = node.send(data["channel"], text.strip(), sender=username, recipient=recipient)
    except LookupError as exc:
        return reply({"error": str(exc)}, 404)
    return reply({"ok": True, "message": message, "delivery_errors": failures})


@app.route("/api/status", methods=["GET"])
def status(headers, body):
    if not authorized(headers):
        return reply({"error": "Login required"}, 401)
    return reply({"peer_id": node.peer_id, "peer_port": node.peer_port,
                  "tracker": node.tracker_url, "connected_peers": len(node.peers())})


@app.route("/api/files/send", methods=["POST"])
def send_file(headers, body):
    username = authorized(headers)
    if not username:
        return reply({"error": "Login required"}, 401)
    if not isinstance(body, bytes):
        return reply({"error": "Send a file as application/octet-stream"}, 400)
    peer_id = headers.get("x-peer-id", "")
    filename = Path(unquote(headers.get("x-filename", "file.bin"))).name[:180] or "file.bin"
    if not peer_id:
        return reply({"error": "Choose a destination peer"}, 400)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix="hcmut-chat-", suffix=".upload", delete=False) as stream:
            stream.write(body)
            temp_path = stream.name
        result = node.send_file(peer_id, temp_path, filename)
        result["sender"] = username
        return reply({"ok": True, "transfer": result})
    except (OSError, ValueError, TimeoutError, LookupError) as exc:
        return reply({"error": str(exc)}, 502)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def create_chat_app(ip, port, peer_id, peer_host, peer_port, file_port, tracker_url, shared_key, download_dir):
    global node
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", peer_id):
        raise ValueError("--peer-id must use 1-32 letters, numbers, underscore, dot, or hyphen")
    # Browser cookies are shared across localhost ports, so each peer needs its own name.
    sessions.cookie_name = "session_" + peer_id.replace("-", "_").replace(".", "_")
    node = ChatNode(peer_id, peer_host, peer_port, file_port, tracker_url, shared_key, download_dir)
    node.start()
    app.prepare_address(ip, port)
    print("[Chat] Web UI at http://{}:{}; peer {} listening at {}:{}".format(ip, port, peer_id, peer_host, peer_port))
    try:
        app.run()
    finally:
        node.close()
