"""HTTP routes for the peer tracker process."""

import ipaddress
import json
import re
import socket

from daemon import StreapRous
from chat.service import Tracker, send_frame

app = StreapRous()
tracker = Tracker()


def reply(data, status=200):
    return status, "application/json; charset=utf-8", json.dumps(data, ensure_ascii=False).encode("utf-8"), {}


def parse(body):
    try:
        value = json.loads(body or "{}")
        return value if isinstance(value, dict) else None
    except (TypeError, json.JSONDecodeError):
        return None


def valid_host(host):
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return bool(re.fullmatch(r"[A-Za-z0-9.-]{1,253}", host))


@app.route("/", methods=["GET"])
def home(headers, body):
    return "<!doctype html><title>HCMUT Chat Tracker</title><h1>HCMUT Chat Tracker</h1><p>Tracker is running.</p>"


@app.route("/submit-info", methods=["POST"])
@app.route("/submit-info/", methods=["POST"])
def submit_info(headers, body):
    data = parse(body)
    if not data:
        return reply({"error": "Expected a JSON object"}, 400)
    peer_id, host = str(data.get("peer_id", "")), str(data.get("host", ""))
    try:
        port = int(data.get("port", 0))
        udp_port = int(data.get("udp_port", 0))
    except (ValueError, TypeError):
        port, udp_port = 0, 0
    if (not re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", peer_id) or not valid_host(host)
            or not 1 <= port <= 65535 or not 1 <= udp_port <= 65535):
        return reply({"error": "Invalid peer_id, host, or port"}, 400)
    peers = tracker.register({"peer_id": peer_id, "host": host, "port": port, "udp_port": udp_port})
    return reply({"ok": True, "peers": peers})


@app.route("/get-list", methods=["GET"])
@app.route("/get-list/", methods=["GET"])
def get_list(headers, body):
    return reply({"peers": tracker.list_peers()})


@app.route("/remove", methods=["POST"])
@app.route("/remove/", methods=["POST"])
def remove_peer(headers, body):
    data = parse(body)
    if not data or not data.get("peer_id"):
        return reply({"error": "peer_id is required"}, 400)
    tracker.unregister(str(data["peer_id"]))
    return reply({"ok": True})


@app.route("/broadcast", methods=["POST"])
@app.route("/broadcast/", methods=["POST"])
def broadcast(headers, body):
    data = parse(body)
    required = ("origin", "message_id", "sender", "channel", "timestamp", "nonce", "ciphertext")
    if not data or data.get("type") != "CHAT" or not all(key in data for key in required):
        return reply({"error": "Invalid encrypted chat frame"}, 400)
    peers = tracker.list_peers()
    if not any(peer["peer_id"] == data["origin"] for peer in peers):
        return reply({"error": "Origin peer is not registered"}, 403)
    delivered, errors = 0, []
    for peer in peers:
        if peer["peer_id"] == data["origin"]:
            continue
        try:
            with socket.create_connection((peer["host"], int(peer["port"])), timeout=2) as conn:
                send_frame(conn, data)
            delivered += 1
        except (OSError, ValueError) as exc:
            errors.append("{}: {}".format(peer["peer_id"], exc))
    return reply({"ok": True, "delivered": delivered, "errors": errors})


def create_tracker(ip, port):
    app.prepare_address(ip, port)
    print("[Tracker] Listening at {}:{}".format(ip, port))
    app.run()
