"""Small threaded tracker and direct peer-to-peer chat transport.

The wire format is a four-byte network-order length followed by UTF-8 JSON.
This framing is necessary because TCP is a byte stream, not a message protocol.
"""

import hashlib
import base64
import hmac
import json
import secrets
import socket
import struct
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    AESGCM = None

MAX_FRAME = 64 * 1024
PEER_TTL_SECONDS = 45
FILE_CHUNK_SIZE = 850
MAX_FILE_SIZE = 100 * 1024 * 1024
CHAT_AAD_FIELDS = ("type", "message_id", "sender", "channel", "timestamp", "origin")


def chat_aad(message):
    """Bind visible chat metadata to the encrypted text to prevent field tampering."""
    metadata = {key: message[key] for key in CHAT_AAD_FIELDS}
    return json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode("utf-8")


class CubicWindow:
    """Small, bounded CUBIC congestion window for the UDP sender."""

    C = 0.4
    BETA = 0.7

    def __init__(self, initial=4, maximum=32):
        self.cwnd = float(initial)
        self.w_max = float(initial)
        self.maximum = float(maximum)
        self.epoch = None

    @property
    def window(self):
        return max(1, min(int(self.maximum), int(self.cwnd)))

    def on_ack(self, now):
        if self.epoch is None:
            self.epoch = now
        elapsed = max(0.0, now - self.epoch)
        k = ((self.w_max * (1.0 - self.BETA)) / self.C) ** (1.0 / 3.0)
        target = self.C * ((elapsed - k) ** 3) + self.w_max
        # Retain a TCP-friendly additive floor while CUBIC approaches Wmax.
        self.cwnd = min(self.maximum, max(self.cwnd + 1.0 / self.cwnd, target))

    def on_loss(self, now):
        self.w_max = self.cwnd
        self.cwnd = max(2.0, self.cwnd * self.BETA)
        self.epoch = now


def read_exact(sock, size):
    chunks = bytearray()
    while len(chunks) < size:
        part = sock.recv(size - len(chunks))
        if not part:
            raise ConnectionError("Peer closed the connection")
        chunks.extend(part)
    return bytes(chunks)


def send_frame(sock, payload):
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_FRAME:
        raise ValueError("Message exceeds the 64 KiB protocol limit")
    sock.sendall(struct.pack("!I", len(encoded)) + encoded)


def recv_frame(sock):
    size = struct.unpack("!I", read_exact(sock, 4))[0]
    if size == 0 or size > MAX_FRAME:
        raise ValueError("Invalid frame length")
    return json.loads(read_exact(sock, size).decode("utf-8"))


class Tracker:
    """Thread-safe in-memory registry. Heartbeats expire stale peer records."""

    def __init__(self):
        self._lock = threading.Lock()
        self._peers = {}

    def register(self, peer):
        with self._lock:
            self._peers[peer["peer_id"]] = {**peer, "last_seen": time.time()}
            return self._public_peers_locked()

    def list_peers(self):
        with self._lock:
            return self._public_peers_locked()

    def unregister(self, peer_id):
        with self._lock:
            self._peers.pop(peer_id, None)

    def _public_peers_locked(self):
        now = time.time()
        expired = [key for key, peer in self._peers.items()
                   if now - peer["last_seen"] > PEER_TTL_SECONDS]
        for key in expired:
            self._peers.pop(key, None)
        return [{key: value for key, value in peer.items() if key != "last_seen"}
                for peer in sorted(self._peers.values(), key=lambda item: item["peer_id"])]


class PeerServer:
    """A daemon TCP listener that accepts framed chat messages from other peers."""

    def __init__(self, host, port, on_message):
        self.host, self.port, self.on_message = host, port, on_message
        self._socket = None
        self._thread = None
        self._stopping = threading.Event()

    def start(self):
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((self.host, self.port))
        self._socket.listen(32)
        self._socket.settimeout(1.0)
        self._thread = threading.Thread(target=self._accept_loop, daemon=True, name="peer-listener")
        self._thread.start()

    def _accept_loop(self):
        while not self._stopping.is_set():
            try:
                conn, _ = self._socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        with conn:
            conn.settimeout(10)
            try:
                message = recv_frame(conn)
                if message.get("type") == "CONNECT":
                    send_frame(conn, {"type": "CONNECT_OK", "peer_id": self.on_message.__self__.peer_id})
                elif message.get("type") == "CHAT" and all(
                    key in message for key in ("message_id", "sender", "channel", "nonce", "ciphertext", "timestamp")
                ):
                    self.on_message(message)
            except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
                print("[Peer] Rejected incoming frame: {}".format(exc))

    def close(self):
        self._stopping.set()
        if self._socket:
            self._socket.close()


class ReliableUdpFiles:
    """Encrypted UDP file transfer with authenticated ACKs and a bounded CUBIC window."""

    def __init__(self, peer_id, host, port, key_material, download_dir):
        if AESGCM is None:
            raise RuntimeError("File transfer requires cryptography. Install it with pip install -r requirements.txt")
        self.peer_id, self.host, self.port = peer_id, host, port
        master_key = hashlib.sha256(key_material.encode("utf-8")).digest()
        self.aes = AESGCM(hmac.new(master_key, b"hcmut-chat-file-aes", hashlib.sha256).digest())
        self.control_key = hmac.new(master_key, b"hcmut-udp-control", hashlib.sha256).digest()
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._transfers = {}
        self._completed = {}
        self._stop = threading.Event()
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.bind((host, port))
        self._socket.settimeout(0.5)
        threading.Thread(target=self._receive_loop, daemon=True, name="udp-file-receiver").start()

    @staticmethod
    def _encode(packet):
        return json.dumps(packet, separators=(",", ":")).encode("utf-8")

    def _encode_control(self, packet):
        """Sign START/ACK control packets so they cannot be forged on the LAN."""
        unsigned = dict(packet)
        canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signed = {**unsigned, "mac": hmac.new(self.control_key, canonical, hashlib.sha256).hexdigest()}
        return self._encode(signed)

    def _valid_control(self, packet):
        supplied = packet.get("mac")
        if not isinstance(supplied, str):
            return False
        unsigned = {key: value for key, value in packet.items() if key != "mac"}
        canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
        expected = hmac.new(self.control_key, canonical, hashlib.sha256).hexdigest()
        return hmac.compare_digest(supplied, expected)

    def _ack(self, address, transfer_id, seq, complete=False, error=None):
        packet = {"kind": "ACK", "transfer_id": transfer_id, "seq": seq, "complete": complete}
        if error:
            packet["error"] = error
        try:
            self._socket.sendto(self._encode_control(packet), address)
        except OSError:
            pass

    def _receive_loop(self):
        while not self._stop.is_set():
            try:
                raw, address = self._socket.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                packet = json.loads(raw.decode("utf-8"))
                if not isinstance(packet, dict):
                    continue
                self._handle(packet, address)
            except (ValueError, UnicodeError, KeyError, TypeError, AttributeError) as exc:
                print("[UDP file] Dropped invalid datagram: {}".format(exc))

    def _handle(self, packet, address):
        transfer_id = packet.get("transfer_id")
        if not isinstance(transfer_id, str):
            return
        if packet.get("kind") == "START":
            if not self._valid_control(packet):
                return
            size, count = int(packet.get("size", -1)), int(packet.get("chunks", -1))
            if not 0 <= size <= MAX_FILE_SIZE or not 1 <= count <= MAX_FILE_SIZE // 850 + 1:
                self._ack(address, transfer_id, -1, error="invalid size or chunk count")
                return
            filename = Path(str(packet.get("filename", "file.bin"))).name[:180] or "file.bin"
            with self._lock:
                self._transfers[transfer_id] = {
                    "filename": filename, "size": size, "chunks": count,
                    "sha256": str(packet.get("sha256", "")), "parts": {},
                    "address": address, "updated": time.time(),
                }
            self._ack(address, transfer_id, -1)
            return
        if packet.get("kind") != "DATA":
            return
        with self._lock:
            completed = self._completed.get(transfer_id)
            transfer = self._transfers.get(transfer_id)
        if completed and completed[0] == address:
            duplicate_seq = int(packet.get("seq", completed[1]))
            self._ack(address, transfer_id, duplicate_seq, complete=True)
            return
        if transfer is None or transfer["address"] != address:
            return
        seq = int(packet.get("seq", -1))
        if not 0 <= seq < transfer["chunks"]:
            return
        nonce = base64.b64decode(packet["nonce"], validate=True)
        ciphertext = base64.b64decode(packet["ciphertext"], validate=True)
        if len(nonce) != 12:
            return
        aad = (transfer_id + ":" + str(seq)).encode("utf-8")
        try:
            chunk = self.aes.decrypt(nonce, ciphertext, aad)
        except Exception:
            self._ack(address, transfer_id, seq, error="AES-GCM authentication failed")
            return
        with self._lock:
            transfer["parts"].setdefault(seq, chunk)
            transfer["updated"] = time.time()
            all_received = len(transfer["parts"]) == transfer["chunks"]
        if not all_received:
            self._ack(address, transfer_id, seq)
            return
        data = b"".join(transfer["parts"][index] for index in range(transfer["chunks"]))
        if len(data) != transfer["size"] or not hmac.compare_digest(
            hashlib.sha256(data).hexdigest(), transfer["sha256"]
        ):
            self._ack(address, transfer_id, seq, error="file SHA-256 mismatch")
            with self._lock:
                self._transfers.pop(transfer_id, None)
            return
        target = self.download_dir / transfer["filename"]
        if target.exists():
            target = self.download_dir / (target.stem + "_" + transfer_id[:8] + target.suffix)
        target.write_bytes(data)
        with self._lock:
            self._transfers.pop(transfer_id, None)
            self._completed[transfer_id] = (address, seq, time.time())
            cutoff = time.time() - 300
            self._completed = {key: value for key, value in self._completed.items() if value[2] >= cutoff}
        self._ack(address, transfer_id, seq, complete=True)

    def send_file(self, peer, path, display_name=None):
        path = Path(path)
        size = path.stat().st_size
        if size > MAX_FILE_SIZE:
            raise ValueError("Maximum file size is 100 MiB")
        content = path.read_bytes()
        chunks = [content[pos:pos + 850] for pos in range(0, len(content), 850)] or [b""]
        transfer_id = secrets.token_hex(12)
        digest = hashlib.sha256(content).hexdigest()
        address = (peer["host"], int(peer["udp_port"]))
        start = {"kind": "START", "transfer_id": transfer_id, "sender": self.peer_id,
                 "filename": display_name or path.name, "size": size, "chunks": len(chunks), "sha256": digest}
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind((self.host, 0))
            sock.settimeout(0.3)
            ready = False
            for _ in range(10):
                sock.sendto(self._encode_control(start), address)
                try:
                    packet, source = sock.recvfrom(2048)
                    if source != address:
                        continue
                    ack = json.loads(packet.decode("utf-8"))
                    if not self._valid_control(ack):
                        continue
                    if ack.get("transfer_id") == transfer_id and ack.get("seq") == -1:
                        if ack.get("error"):
                            raise ValueError(ack["error"])
                        ready = True
                        break
                except (socket.timeout, ValueError, UnicodeError):
                    continue
            if not ready:
                raise TimeoutError("Receiver did not acknowledge file transfer setup")

            acked, pending, attempts = set(), {}, {}
            next_seq, base = 0, 0
            congestion = CubicWindow()
            last_datagram = None
            last_probe_at, probe_attempts = time.monotonic(), 0
            complete = False
            while not complete:
                window = congestion.window
                while next_seq < len(chunks) and next_seq < base + window:
                    nonce = secrets.token_bytes(12)
                    aad = (transfer_id + ":" + str(next_seq)).encode("utf-8")
                    encrypted = self.aes.encrypt(nonce, chunks[next_seq], aad)
                    datagram = self._encode({"kind": "DATA", "transfer_id": transfer_id, "seq": next_seq,
                                             "nonce": base64.b64encode(nonce).decode("ascii"),
                                             "ciphertext": base64.b64encode(encrypted).decode("ascii")})
                    sock.sendto(datagram, address)
                    pending[next_seq] = (datagram, time.monotonic())
                    if next_seq == len(chunks) - 1:
                        last_datagram = datagram
                    next_seq += 1
                try:
                    raw, source = sock.recvfrom(2048)
                    if source != address:
                        continue
                    ack = json.loads(raw.decode("utf-8"))
                    if ack.get("transfer_id") != transfer_id:
                        continue
                    if not self._valid_control(ack):
                        continue
                    if ack.get("error"):
                        raise ValueError(ack["error"])
                    seq = ack.get("seq")
                    if isinstance(seq, int) and seq in pending:
                        acked.add(seq)
                        pending.pop(seq, None)
                        congestion.on_ack(time.monotonic())
                        while base in acked:
                            acked.remove(base)
                            base += 1
                    complete = bool(ack.get("complete"))
                except socket.timeout:
                    now = time.monotonic()
                    expired = [seq for seq, (_, sent_at) in pending.items() if now - sent_at >= 0.3]
                    if expired:
                        congestion.on_loss(now)
                        for seq in expired:
                            attempts[seq] = attempts.get(seq, 0) + 1
                            if attempts[seq] > 12:
                                raise TimeoutError("Segment {} exceeded 12 retransmissions".format(seq))
                            datagram, _ = pending[seq]
                            sock.sendto(datagram, address)
                            pending[seq] = (datagram, now)
                    elif not pending and next_seq == len(chunks) and not complete and last_datagram:
                        # A lost completion ACK is recovered by retransmitting the last
                        # DATA packet; the receiver's completed cache responds with ACK.
                        if now - last_probe_at >= 0.3:
                            probe_attempts += 1
                            if probe_attempts > 12:
                                raise TimeoutError("Receiver did not confirm completed file transfer")
                            sock.sendto(last_datagram, address)
                            last_probe_at = now
        return {"transfer_id": transfer_id, "filename": display_name or path.name, "size": size,
                "chunks": len(chunks), "sha256": digest, "peer_id": peer["peer_id"]}

    def close(self):
        self._stop.set()
        self._socket.close()


class ChatNode:
    """Peer runtime and bounded in-memory message history."""

    def __init__(self, peer_id, peer_host, peer_port, file_port, tracker_url, shared_key, download_dir):
        self.peer_id = peer_id
        self.peer_host = peer_host
        self.peer_port = peer_port
        self.file_port = file_port
        self.tracker_url = tracker_url.rstrip("/")
        self._lock = threading.RLock()
        self._messages = deque(maxlen=500)
        self._peers = []
        self._server = PeerServer(peer_host, peer_port, self._receive)
        self.files = ReliableUdpFiles(peer_id, peer_host, file_port, shared_key, download_dir)
        self._stop = threading.Event()
        self._heartbeat_thread = None

    def start(self):
        self._server.start()
        self._heartbeat_thread = threading.Thread(target=self._heartbeat, daemon=True, name="tracker-heartbeat")
        self._heartbeat_thread.start()

    def _tracker_request(self, path, data=None):
        body = json.dumps(data or {}).encode("utf-8") if data is not None else None
        request = urllib.request.Request(
            self.tracker_url + path, data=body,
            headers={"Content-Type": "application/json"} if body is not None else {},
            method="POST" if body is not None else "GET",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))

    def _heartbeat(self):
        while not self._stop.is_set():
            try:
                result = self._tracker_request("/submit-info", {
                    "peer_id": self.peer_id,
                    "host": self.peer_host,
                    "port": self.peer_port,
                    "udp_port": self.file_port,
                })
                with self._lock:
                    self._peers = result.get("peers", [])
            except (OSError, ValueError, urllib.error.URLError) as exc:
                print("[Peer] Tracker unavailable: {}".format(exc))
            self._stop.wait(10)

    def refresh_peers(self):
        result = self._tracker_request("/get-list")
        peers = result.get("peers", [])
        with self._lock:
            self._peers = peers
        return peers

    def connect(self, peer_id):
        peer = next((item for item in self.peers() if item.get("peer_id") == peer_id), None)
        if peer is None:
            raise LookupError("Peer is not in the current tracker list")
        with socket.create_connection((peer["host"], int(peer["port"])), timeout=3) as conn:
            conn.settimeout(3)
            send_frame(conn, {"type": "CONNECT", "peer_id": self.peer_id})
            response = recv_frame(conn)
        if response.get("type") != "CONNECT_OK":
            raise ConnectionError("Peer did not acknowledge the connection")
        return response

    def peers(self):
        with self._lock:
            return list(self._peers)

    def send(self, channel, text, sender=None, recipient=None):
        message = {
            "type": "CHAT",
            "message_id": secrets.token_hex(12),
            "sender": sender or self.peer_id,
            "channel": channel,
            "text": text,
            "timestamp": int(time.time() * 1000),
        }
        self._receive(message)
        wire_message = {key: value for key, value in message.items() if key != "text"}
        wire_message["origin"] = self.peer_id
        nonce = secrets.token_bytes(12)
        ciphertext = self.files.aes.encrypt(nonce, text.encode("utf-8"), chat_aad(wire_message))
        wire_message["nonce"] = base64.b64encode(nonce).decode("ascii")
        wire_message["ciphertext"] = base64.b64encode(ciphertext).decode("ascii")
        failures = []
        if recipient is None:
            try:
                result = self._tracker_request("/broadcast", wire_message)
                failures.extend(result.get("errors", []))
            except (OSError, ValueError, urllib.error.URLError) as exc:
                failures.append("tracker: {}".format(exc))
            return message, failures
        targets = self.peers()
        targets = [peer for peer in targets if peer.get("peer_id") == recipient]
        if not targets:
            raise LookupError("Recipient is not in the current tracker list")
        for peer in targets:
            if peer["peer_id"] == self.peer_id:
                continue
            try:
                with socket.create_connection((peer["host"], int(peer["port"])), timeout=2) as conn:
                    send_frame(conn, wire_message)
            except (OSError, ValueError) as exc:
                failures.append("{}: {}".format(peer["peer_id"], exc))
        return message, failures

    def _receive(self, message):
        if "ciphertext" in message:
            try:
                nonce = base64.b64decode(message["nonce"], validate=True)
                ciphertext = base64.b64decode(message["ciphertext"], validate=True)
                plain = self.files.aes.decrypt(nonce, ciphertext, chat_aad(message))
                message = {**message, "text": plain.decode("utf-8")}
            except Exception as exc:
                print("[Peer] Rejected encrypted chat payload: {}".format(exc))
                return
        with self._lock:
            if any(item["message_id"] == message["message_id"] for item in self._messages):
                return
            self._messages.append(message)

    def messages(self, since=0, channel=None):
        with self._lock:
            return [dict(item) for item in self._messages
                    if item["timestamp"] >= since and (channel is None or item["channel"] == channel)]

    def send_file(self, peer_id, path, filename=None):
        peer = next((item for item in self.peers() if item.get("peer_id") == peer_id), None)
        if peer is None:
            raise LookupError("Peer is not in the current tracker list")
        if not peer.get("udp_port"):
            raise LookupError("Peer did not advertise a UDP file port")
        return self.files.send_file(peer, path, filename)

    def close(self):
        self._stop.set()
        self._server.close()
        self.files.close()
        try:
            self._tracker_request("/remove", {"peer_id": self.peer_id})
        except (OSError, ValueError, urllib.error.URLError):
            pass


class Sessions:
    """Cookie sessions authenticated with a process-local HMAC key."""

    _DEMO_USERS = {
        "alice": hashlib.sha256(b"hcmut2026").digest(),
        "bob": hashlib.sha256(b"hcmut2026").digest(),
    }

    def __init__(self):
        self._key = secrets.token_bytes(32)
        self.cookie_name = "session"
        self._sessions = set()
        self._lock = threading.Lock()

    def login(self, username, password):
        expected = self._DEMO_USERS.get(username)
        supplied = hashlib.sha256(password.encode("utf-8")).digest()
        if expected is None or not hmac.compare_digest(expected, supplied):
            return None
        token = secrets.token_urlsafe(24)
        signature = hmac.new(self._key, (username + ":" + token).encode(), hashlib.sha256).hexdigest()
        value = username + ":" + token + ":" + signature
        with self._lock:
            self._sessions.add(value)
        return value

    def username(self, headers):
        cookies = {}
        for pair in headers.get("cookie", "").split(";"):
            if "=" in pair:
                key, value = pair.strip().split("=", 1)
                cookies[key] = value
        value = cookies.get(self.cookie_name, "")
        with self._lock:
            if value not in self._sessions:
                return None
        try:
            username, token, signature = value.split(":", 2)
        except ValueError:
            return None
        expected = hmac.new(self._key, (username + ":" + token).encode(), hashlib.sha256).hexdigest()
        return username if hmac.compare_digest(expected, signature) else None

    def logout(self, headers):
        for pair in headers.get("cookie", "").split(";"):
            if pair.strip().startswith(self.cookie_name + "="):
                value = pair.strip().split("=", 1)[1]
                with self._lock:
                    self._sessions.discard(value)
