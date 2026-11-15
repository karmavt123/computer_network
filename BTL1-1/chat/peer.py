"""Peer daemon: tracker client, P2P chat listener/sender and file transfers.

Life cycle of a peer (the order the assignment brief describes):

1. **Registration** - ``/submit-info`` to the tracker with the reachable
   host, TCP chat port and UDP file port; repeated every
   :data:`HEARTBEAT_SECONDS` as a heartbeat.
2. **Login** - the user's credentials are checked by the tracker, which
   then records the user against this peer.
3. **Discovery** - ``/get-list`` returns the other live peers.
4. **Connection setup** - a ``CONNECT``/``CONNECT_OK`` handshake over TCP
   marks two peers as connected (both sides).
5. **Chatting** - broadcast frames go directly to every connected peer
   (no server in the path); direct messages go to one peer. Only if a peer
   cannot be reached directly is the tracker asked to relay the frame.

The listener is non-blocking and callback driven (``selectors``): one
thread multiplexes all incoming connections and calls a handler when a
socket becomes readable or writable.
"""

import json
import secrets
import selectors
import socket
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .protocol import (CHAT_REQUIRED_FIELDS, FRAME_CHAT, FRAME_CONNECT,
                       FRAME_CONNECT_OK, FrameBuffer, KeyRing, ProtocolError,
                       decrypt_chat, encode_frame, encrypt_chat, recv_frame,
                       send_frame)
from .udp_files import CHUNK_SIZE, ReliableUdpFiles, TransferError

HEARTBEAT_SECONDS = 10
TRACKER_TIMEOUT = 3
PEER_SEND_TIMEOUT = 2
IDLE_CONNECTION_SECONDS = 30
HISTORY_SIZE = 1000
MAX_TEXT = 2000

#: Public channels every peer has joined. Direct conversations use the
#: pseudo channel ``@<peer_id>``.
CHANNELS = ("general", "study")
DIRECT_CHANNEL = "dm"


class TrackerUnavailable(Exception):
    """The tracker could not be reached (``status`` None) or refused (HTTP status)."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class _Connection:
    """State of one accepted TCP connection inside the event loop."""

    def __init__(self, server, sock, address):
        self.server, self.sock, self.address = server, sock, address
        self.inbox = FrameBuffer()
        self.outbox = bytearray()
        self.last_activity = time.monotonic()

    def handle(self, mask):
        """Callback invoked by the selector loop."""
        if mask & selectors.EVENT_READ:
            self._on_readable()
        if mask & selectors.EVENT_WRITE and self.sock.fileno() != -1:
            self._on_writable()

    def _on_readable(self):
        try:
            data = self.sock.recv(4096)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            self.close()
            return
        if not data:                       # orderly close by the sender
            self.close()
            return
        self.last_activity = time.monotonic()
        self.inbox.feed(data)
        try:
            for frame in self.inbox.frames():
                reply = self.server.on_frame(frame, self.address)
                if reply is not None:
                    self.outbox.extend(encode_frame(reply))
        except ProtocolError as exc:
            print("[Peer] Rejected frame from {}: {}".format(self.address, exc))
            self.close()
            return
        if self.outbox:
            self.server.selector.modify(self.sock, selectors.EVENT_READ | selectors.EVENT_WRITE,
                                        self)

    def _on_writable(self):
        try:
            sent = self.sock.send(self.outbox)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            self.close()
            return
        del self.outbox[:sent]
        if not self.outbox:
            self.server.selector.modify(self.sock, selectors.EVENT_READ, self)

    def close(self):
        try:
            self.server.selector.unregister(self.sock)
        except (KeyError, ValueError):
            pass
        self.sock.close()
        self.server.connections.discard(self)


class PeerServer:
    """Callback-driven (non-blocking) TCP listener for peer frames.

    :param on_frame: ``on_frame(frame, address)`` returns a reply frame or None.
    """

    def __init__(self, host, port, on_frame):
        self.host, self.port, self.on_frame = host, port, on_frame
        self.selector = selectors.DefaultSelector()
        self.connections = set()
        self._socket = None
        self._thread = None
        self._stopping = threading.Event()

    def start(self):
        """Bind, listen and start the event-loop thread."""
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((self.host, self.port))
        self.port = self._socket.getsockname()[1]
        self._socket.listen(64)
        self._socket.setblocking(False)
        self.selector.register(self._socket, selectors.EVENT_READ, self)
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="peer-event-loop")
        self._thread.start()

    def handle(self, mask):
        """Selector callback of the listening socket: accept a connection."""
        try:
            sock, address = self._socket.accept()
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            return
        sock.setblocking(False)
        connection = _Connection(self, sock, address)
        self.connections.add(connection)
        self.selector.register(sock, selectors.EVENT_READ, connection)

    def _loop(self):
        while not self._stopping.is_set():
            try:
                events = self.selector.select(timeout=0.5)
            except OSError:
                break
            for key, mask in events:
                key.data.handle(mask)          # dispatch to the callback object
            self._close_idle()

    def _close_idle(self):
        now = time.monotonic()
        for connection in list(self.connections):
            if now - connection.last_activity > IDLE_CONNECTION_SECONDS:
                connection.close()

    def close(self):
        """Stop the loop and close every socket."""
        self._stopping.set()
        if self._thread:
            self._thread.join(timeout=2)
        for connection in list(self.connections):
            connection.close()
        if self._socket:
            try:
                self.selector.unregister(self._socket)
            except (KeyError, ValueError):
                pass
            self._socket.close()
        self.selector.close()


class ChatNode:
    """Runtime of one peer: state, protocol logic and the tracker client."""

    def __init__(self, peer_id, peer_host, peer_port, file_port, tracker_url,
                 shared_key, download_dir, loss_rate=0.0, relay_fallback=True):
        self.peer_id = peer_id
        self.peer_host = peer_host
        self.tracker_url = tracker_url.rstrip("/")
        self.relay_fallback = relay_fallback
        self.keys = KeyRing(shared_key)
        self.username = None
        # The peer token is kept on disk so that a peer restarted after a
        # crash can reclaim its id instead of waiting for the record to expire.
        self._token_file = Path(download_dir) / ".tracker_token"
        self.token = self._load_token()
        self.tracker_online = False
        self._lock = threading.RLock()
        self._messages = deque(maxlen=HISTORY_SIZE)
        self._seen_ids = deque(maxlen=HISTORY_SIZE * 4)
        self._seq = 0
        self._peers = []
        self._connected = {}
        self._transfers = {}
        self._stop = threading.Event()
        self._pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="peer-send")
        self._server = PeerServer(peer_host, peer_port, self._on_frame)
        self.files = ReliableUdpFiles(peer_id, peer_host, file_port, self.keys,
                                      download_dir, loss_rate=loss_rate,
                                      on_event=self._on_file_event)
        self.peer_port = peer_port
        self.file_port = self.files.port
        self._heartbeat_thread = None

    # ------------------------------------------------------------------
    # start / stop
    # ------------------------------------------------------------------
    def start(self):
        """Open the listener and start the tracker heartbeat."""
        self._server.start()
        self.peer_port = self._server.port
        try:
            self.register()
        except TrackerUnavailable as exc:
            print("[Peer] Tracker unavailable at start-up: {}".format(exc))
        self._heartbeat_thread = threading.Thread(target=self._heartbeat, daemon=True,
                                                  name="tracker-heartbeat")
        self._heartbeat_thread.start()

    def close(self):
        """Leave gracefully: tell the tracker, then close every socket."""
        self._stop.set()
        if self.token:
            try:
                self._tracker("/remove", {"peer_id": self.peer_id, "token": self.token})
            except TrackerUnavailable:
                pass
        self._server.close()
        self.files.close()
        self._pool.shutdown(wait=False)

    # ------------------------------------------------------------------
    # tracker client
    # ------------------------------------------------------------------
    def _tracker(self, path, data=None):
        """Call the tracker's HTTP API; returns the decoded JSON reply."""
        body = json.dumps(data).encode("utf-8") if data is not None else None
        request = urllib.request.Request(
            self.tracker_url + path, data=body, method="POST" if body else "GET",
            headers={"Content-Type": "application/json"} if body else {})
        try:
            with urllib.request.urlopen(request, timeout=TRACKER_TIMEOUT) as response:
                result = json.loads(response.read().decode("utf-8"))
            self.tracker_online = True
            return result
        except urllib.error.HTTPError as exc:
            self.tracker_online = True
            try:
                reason = json.loads(exc.read().decode("utf-8")).get("error", str(exc))
            except (ValueError, UnicodeError):
                reason = str(exc)
            raise TrackerUnavailable(reason, exc.code) from exc
        except (OSError, ValueError) as exc:
            self.tracker_online = False
            raise TrackerUnavailable(str(exc)) from exc

    def register(self):
        """Send ``/submit-info`` (first registration or heartbeat)."""
        payload = {"peer_id": self.peer_id, "host": self.peer_host,
                   "port": self.peer_port, "udp_port": self.file_port}
        if self.token:
            payload["token"] = self.token
        result = self._tracker("/submit-info", payload)
        with self._lock:
            if result.get("token") and result["token"] != self.token:
                self.token = result["token"]
                self._save_token()
            self._peers = result.get("peers", [])
        return result

    def _load_token(self):
        try:
            return self._token_file.read_text(encoding="ascii").strip() or None
        except OSError:
            return None

    def _save_token(self):
        try:
            self._token_file.parent.mkdir(parents=True, exist_ok=True)
            self._token_file.write_text(self.token, encoding="ascii")
            self._token_file.chmod(0o600)
        except OSError as exc:
            print("[Peer] Could not save the tracker token: {}".format(exc))

    def _heartbeat(self):
        while not self._stop.wait(HEARTBEAT_SECONDS):
            try:
                self.register()
            except TrackerUnavailable as exc:
                print("[Peer] Heartbeat failed: {}".format(exc))

    def login(self, username, password):
        """Authenticate at the tracker, which records the user on this peer."""
        if not self.token:
            self.register()
        self._tracker("/login", {"peer_id": self.peer_id, "token": self.token,
                                 "username": username, "password": password})
        self.username = username
        return username

    def logout(self):
        """Tell the tracker that nobody is signed in on this peer any more."""
        self.username = None
        if self.token:
            try:
                self._tracker("/logout", {"peer_id": self.peer_id, "token": self.token})
            except TrackerUnavailable:
                pass

    def refresh_peers(self):
        """Fetch the live peer list (``/get-list``)."""
        peers = self._tracker("/get-list").get("peers", [])
        with self._lock:
            self._peers = peers
        return peers

    def peers(self):
        """Last known peer list, each entry flagged ``connected``."""
        with self._lock:
            return [{**peer, "connected": peer["peer_id"] in self._connected}
                    for peer in self._peers]

    def _peer(self, peer_id, refresh=True):
        """Look a peer up in the cached list; ask the tracker once on a miss."""
        found = next((p for p in self.peers() if p.get("peer_id") == peer_id), None)
        if found is None and refresh:
            try:
                self.refresh_peers()
            except TrackerUnavailable:
                return None
            return self._peer(peer_id, refresh=False)
        return found

    def _refresh_quietly(self):
        try:
            self.refresh_peers()
        except TrackerUnavailable:
            pass

    # ------------------------------------------------------------------
    # incoming frames (called from the event loop)
    # ------------------------------------------------------------------
    def _on_frame(self, frame, address):
        kind = frame.get("type")
        if kind == FRAME_CONNECT:
            other = str(frame.get("peer_id", ""))[:32]
            if other and other != self.peer_id:
                with self._lock:
                    self._connected[other] = time.time()
                    known = any(p["peer_id"] == other for p in self._peers)
                if not known:
                    # Learn the newcomer's address without blocking the event loop.
                    self._pool.submit(self._refresh_quietly)
            return {"type": FRAME_CONNECT_OK, "peer_id": self.peer_id,
                    "username": self.username}
        if kind == FRAME_CHAT:
            self.receive(frame)
        return None

    def receive(self, frame):
        """Validate, decrypt and store a CHAT frame. Returns the message or None."""
        if not all(key in frame for key in CHAT_REQUIRED_FIELDS):
            return None
        try:
            message = decrypt_chat(self.keys, frame)
        except ProtocolError as exc:
            print("[Peer] {}".format(exc))
            return None
        recipient = message.get("to")
        if recipient:
            if recipient != self.peer_id:
                return None                     # a DM for someone else
            message["channel"] = "@" + message["origin"]
        elif message.get("channel") not in CHANNELS:
            return None
        origin = self._peer(message["origin"], refresh=False)   # never block the loop
        if origin is None or origin.get("username") != message["sender"]:
            # Maybe our copy of the tracker list is just old: refresh it in the
            # background; "verified" is evaluated again whenever messages are read.
            self._pool.submit(self._refresh_quietly)
        with self._lock:
            self._mark_alive(message["origin"])
        return self._store(message)

    def _store(self, message):
        with self._lock:
            if message["message_id"] in self._seen_ids:
                return None                     # duplicate (e.g. direct + relay)
            self._seen_ids.append(message["message_id"])
            self._seq += 1
            message = {**message, "seq": self._seq}
            self._messages.append(message)
            return message

    def _mark_alive(self, peer_id):
        if peer_id in self._connected:
            self._connected[peer_id] = time.time()

    # ------------------------------------------------------------------
    # outgoing
    # ------------------------------------------------------------------
    def connect(self, peer_id):
        """CONNECT/CONNECT_OK handshake with one peer (``/connect-peer``)."""
        peer = self._peer(peer_id)
        if peer is None:
            raise LookupError("Peer {} is not in the tracker list".format(peer_id))
        if peer_id == self.peer_id:
            raise LookupError("Cannot connect to yourself")
        with socket.create_connection((peer["host"], int(peer["port"])),
                                      timeout=PEER_SEND_TIMEOUT) as conn:
            conn.settimeout(PEER_SEND_TIMEOUT)
            send_frame(conn, {"type": FRAME_CONNECT, "peer_id": self.peer_id,
                              "username": self.username})
            response = recv_frame(conn)
        if response.get("type") != FRAME_CONNECT_OK:
            raise ConnectionError("Peer did not acknowledge the connection")
        with self._lock:
            self._connected[peer_id] = time.time()
        return response

    def connect_all(self):
        """Connect to every listed peer; returns ``{peer_id: error or None}``."""
        results = {}
        self._refresh_quietly()
        others = [p["peer_id"] for p in self.peers() if p["peer_id"] != self.peer_id]
        futures = {pid: self._pool.submit(self.connect, pid) for pid in others}
        for pid, future in futures.items():
            try:
                future.result()
                results[pid] = None
            except (OSError, LookupError, ConnectionError, ValueError) as exc:
                results[pid] = str(exc)
        return results

    def _deliver(self, peer, wire):
        """Send one frame directly to one peer over a fresh TCP connection."""
        with socket.create_connection((peer["host"], int(peer["port"])),
                                      timeout=PEER_SEND_TIMEOUT) as conn:
            send_frame(conn, wire)

    def send(self, channel, text, recipient=None):
        """Broadcast to the connected peers, or send a DM to ``recipient``.

        :returns: ``(stored message, delivery report)``.
        """
        if not self.username:
            raise PermissionError("Sign in first")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
            raise ValueError("Message must contain 1 to {} characters".format(MAX_TEXT))
        text = text.strip()
        if recipient is None and channel not in CHANNELS:
            raise ValueError("Unknown channel")
        frame = {"type": FRAME_CHAT, "message_id": secrets.token_hex(12),
                 "sender": self.username,
                 "channel": DIRECT_CHANNEL if recipient else channel,
                 "timestamp": int(time.time() * 1000), "origin": self.peer_id,
                 "to": recipient}
        if recipient:
            target = self._peer(recipient)
            if target is None:
                raise LookupError("Recipient is not in the tracker list")
            targets = [target]
        else:
            with self._lock:
                connected = set(self._connected)
            targets = [p for p in self.peers()
                       if p["peer_id"] in connected and p["peer_id"] != self.peer_id]
        wire = encrypt_chat(self.keys, frame, text)
        local = dict(frame, text=text,
                     channel="@" + recipient if recipient else channel)
        stored = self._store(local)
        report = self._fan_out(wire, targets)
        return stored, report

    def _fan_out(self, wire, targets):
        """Deliver ``wire`` to all targets in parallel; relay on failure."""
        report = {"direct": [], "relayed": [], "failed": {}}
        futures = {peer["peer_id"]: self._pool.submit(self._deliver, peer, wire)
                   for peer in targets}
        unreachable = []
        for peer_id, future in futures.items():
            try:
                future.result()
                report["direct"].append(peer_id)
            except (OSError, ValueError) as exc:
                unreachable.append(peer_id)
                report["failed"][peer_id] = str(exc)
                with self._lock:
                    self._connected.pop(peer_id, None)
        if unreachable and self.relay_fallback and self.token:
            try:
                result = self._tracker("/relay", {"peer_id": self.peer_id,
                                                  "token": self.token,
                                                  "targets": unreachable,
                                                  "frame": wire})
                for peer_id in result.get("delivered", []):
                    report["relayed"].append(peer_id)
                    report["failed"].pop(peer_id, None)
            except TrackerUnavailable as exc:
                for peer_id in unreachable:
                    report["failed"][peer_id] += "; relay: {}".format(exc)
        return report

    def messages(self, after=0, channel=None):
        """Messages with a local sequence number greater than ``after``.

        The cursor is a local counter, not the sender's clock, so a peer
        whose clock is behind cannot make its messages invisible.
        """
        with self._lock:
            users = {p["peer_id"]: p.get("username") for p in self._peers}
            result = []
            for m in self._messages:
                if m["seq"] > after and (channel is None or m["channel"] == channel):
                    # The shared key proves the frame comes from a group member;
                    # the tracker's record also tells whether the claimed sender
                    # is the user signed in on the origin peer.
                    verified = m["origin"] == self.peer_id or users.get(m["origin"]) == m["sender"]
                    result.append({**m, "verified": verified})
            return result

    # ------------------------------------------------------------------
    # files
    # ------------------------------------------------------------------
    def send_file(self, peer_id, content, filename):
        """Start sending ``content`` to ``peer_id`` in the background.

        :returns: the transfer id; progress is visible in :meth:`transfers`.
        """
        peer = self._peer(peer_id)
        if peer is None:
            raise LookupError("Peer {} is not in the tracker list".format(peer_id))
        if not peer.get("udp_port"):
            raise LookupError("Peer did not advertise a UDP file port")
        transfer_id = secrets.token_hex(12)
        info = {"transfer_id": transfer_id, "direction": "out", "peer_id": peer_id,
                "filename": filename, "size": len(content),
                "chunks": max(1, -(-len(content) // CHUNK_SIZE)), "acked": 0,
                "status": "sending", "error": None, "started": time.time()}
        with self._lock:
            self._transfers[transfer_id] = info

        def progress(stats):
            with self._lock:
                info.update({k: v for k, v in stats.items() if k != "cwnd_trace"})

        def run():
            try:
                stats = self.files.send_bytes(peer, content, filename, transfer_id, progress)
                with self._lock:
                    info.update({k: v for k, v in stats.items() if k != "cwnd_trace"})
                    info["cwnd_trace"] = stats["cwnd_trace"]
                    info["status"] = "done"
            except (TransferError, OSError) as exc:
                with self._lock:
                    info["status"], info["error"] = "failed", str(exc)

        threading.Thread(target=run, daemon=True, name="udp-send-" + transfer_id[:6]).start()
        return transfer_id

    def _on_file_event(self, kind, data):
        with self._lock:
            info = self._transfers.setdefault(data["transfer_id"], {
                "transfer_id": data["transfer_id"], "direction": "in",
                "status": "receiving", "error": None, "received": 0,
                "started": time.time()})
            if kind == "incoming_started":
                info.update(filename=data["filename"], size=data["size"],
                            chunks=data["chunks"], peer_id=data["peer_id"])
            elif kind == "incoming_progress":
                info["received"] = data["received"]
            elif kind == "incoming_done":
                info.update(status="done", filename=data["filename"],
                            received=info.get("chunks", 0))
            elif kind == "incoming_failed":
                info.update(status="failed", error=data["error"])

    def transfers(self, include_trace=False):
        """Snapshot of all file transfers of this peer, newest first."""
        with self._lock:
            items = [dict(t) for t in self._transfers.values()]
        if not include_trace:
            for item in items:
                item.pop("cwnd_trace", None)
        return sorted(items, key=lambda t: t.get("started", 0), reverse=True)

    def downloaded_file(self, name):
        """Path of a received file, guarded against path traversal."""
        path = (self.files.download_dir / Path(name).name).resolve()
        if path.parent != self.files.download_dir.resolve() or not path.is_file():
            raise LookupError("No such file")
        return path

    def status(self):
        """Summary shown in the UI header."""
        with self._lock:
            connected = sorted(self._connected)
        return {"peer_id": self.peer_id, "username": self.username,
                "host": self.peer_host, "peer_port": self.peer_port,
                "udp_port": self.file_port, "tracker": self.tracker_url,
                "tracker_online": self.tracker_online,
                "registered": bool(self.token), "connected_peers": connected,
                "loss_rate": self.files.loss_rate}
