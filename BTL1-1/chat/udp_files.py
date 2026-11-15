"""Reliable, encrypted file transfer over UDP.

UDP delivers datagrams without ordering, without retransmission and without
congestion control. This module adds those services at the application
layer:

========================  ===================================================
Service                   Mechanism
========================  ===================================================
Setup                     ``START`` (file name, size, chunk count, SHA-256),
                          answered by an ``ACK`` with ``seq = -1``
Ordering / reassembly     every ``DATA`` datagram carries a sequence number;
                          the receiver writes chunk ``seq`` at offset
                          ``seq * CHUNK_SIZE`` of a ``.part`` file
No head-of-line blocking  chunks are accepted in any order; a missing chunk
                          never delays the processing of later ones
Acknowledgement           one ``ACK`` per received chunk (selective) that
                          also carries ``cum``, the number of chunks received
                          in order (cumulative), so a lost ACK is repaired by
                          the next one; only missing chunks are resent
Fast retransmit           three ACKs for later chunks while chunk ``cum`` is
                          missing resend it without waiting for the timeout
Flow / congestion control sliding window of at most ``cwnd`` unacknowledged
                          chunks; ``cwnd`` follows slow start, then CUBIC
Retransmission timeout    SRTT/RTTVAR estimator (RFC 6298) with Karn's rule
Integrity / secrecy       AES-GCM per chunk (AAD = transfer id + sequence),
                          HMAC on START/ACK, SHA-256 of the whole file
========================  ===================================================

Packet loss can be simulated with ``loss_rate`` to demonstrate recovery.
"""

import base64
import hashlib
import hmac
import json
import os
import random
import secrets
import socket
import threading
import time
from pathlib import Path

#: Plain bytes per chunk. After AES-GCM (+16 bytes), base64 (x4/3) and the
#: JSON envelope a DATA datagram is about 1.25 KB, below the 1472-byte UDP
#: payload of a 1500-byte Ethernet MTU, so datagrams are never fragmented.
CHUNK_SIZE = 850
MAX_FILE_SIZE = 100 * 1024 * 1024
MAX_CHUNKS = MAX_FILE_SIZE // CHUNK_SIZE + 1
DATAGRAM_BUFFER = 2048

#: A receiver forgets a transfer that has been silent for this long.
TRANSFER_IDLE_TIMEOUT = 30.0
#: How long a finished transfer is remembered to re-ACK duplicate packets.
COMPLETED_MEMORY = 300.0

START_ATTEMPTS = 10
MAX_RETRANSMISSIONS = 12
MIN_RTO, MAX_RTO, INITIAL_RTO = 0.02, 1.0, 0.3


class TransferError(Exception):
    """A file transfer failed; the message says why."""


class CubicWindow:
    """Congestion window of the UDP sender: slow start, then CUBIC.

    * Slow start: ``cwnd`` grows by one chunk per ACK (doubles per RTT)
      until it reaches ``ssthresh``.
    * Congestion avoidance: ``cwnd`` follows the CUBIC curve
      ``W(t) = C (t - K)^3 + W_max`` with ``K = cbrt(W_max (1 - beta) / C)``,
      where ``t`` is the time since the last loss. A TCP-friendly floor of
      ``+1/cwnd`` per ACK keeps it growing on short RTTs.
    * Loss (a retransmission timeout): ``W_max = cwnd``,
      ``cwnd = ssthresh = beta * cwnd`` (multiplicative decrease).

    This is a simplified CUBIC (RFC 8312) bounded to ``maximum`` chunks.
    """

    C = 0.4
    BETA = 0.7
    #: Never shrink below 4 chunks, so that three later ACKs can still
    #: trigger a fast retransmit of a lost chunk.
    MIN_WINDOW = 4.0

    def __init__(self, initial=4, maximum=64, clock=time.monotonic):
        self.cwnd = float(initial)
        self.ssthresh = float(maximum)
        self.w_max = float(maximum)
        self.maximum = float(maximum)
        self.epoch = None
        self._clock = clock
        self.trace = [(0.0, self.cwnd)]
        self._t0 = clock()

    @property
    def window(self):
        """Current window in whole chunks (at least one)."""
        return max(1, min(int(self.maximum), int(self.cwnd)))

    def on_ack(self):
        """Grow the window after a new chunk was acknowledged."""
        now = self._clock()
        if self.cwnd < self.ssthresh:
            self.cwnd = min(self.maximum, self.cwnd + 1.0)
        else:
            if self.epoch is None:
                self.epoch = now
            elapsed = now - self.epoch
            k = ((self.w_max * (1.0 - self.BETA)) / self.C) ** (1.0 / 3.0)
            target = self.C * ((elapsed - k) ** 3) + self.w_max
            self.cwnd = min(self.maximum, max(self.cwnd + 1.0 / self.cwnd, target))
        self._record(now)

    def on_loss(self):
        """Multiplicative decrease after a retransmission timeout."""
        now = self._clock()
        self.w_max = self.cwnd
        self.cwnd = max(self.MIN_WINDOW, self.cwnd * self.BETA)
        self.ssthresh = self.cwnd
        self.epoch = now
        self._record(now)

    def _record(self, now):
        if len(self.trace) < 20000:
            self.trace.append((round(now - self._t0, 4), round(self.cwnd, 2)))


class RttEstimator:
    """Retransmission timeout from smoothed RTT samples (RFC 6298)."""

    ALPHA, BETA = 1 / 8, 1 / 4

    def __init__(self):
        self.srtt = None
        self.rttvar = None
        self.rto = INITIAL_RTO

    def sample(self, rtt):
        """Feed one RTT measured on a chunk that was sent only once."""
        if self.srtt is None:
            self.srtt, self.rttvar = rtt, rtt / 2
        else:
            self.rttvar = (1 - self.BETA) * self.rttvar + self.BETA * abs(self.srtt - rtt)
            self.srtt = (1 - self.ALPHA) * self.srtt + self.ALPHA * rtt
        self.rto = min(MAX_RTO, max(MIN_RTO, self.srtt + 4 * self.rttvar))

    def backoff(self):
        """Double the timeout after a loss (exponential back-off)."""
        self.rto = min(MAX_RTO, self.rto * 2)


class ReliableUdpFiles:
    """UDP endpoint of one peer: receives files and sends files to others.

    :param keys: :class:`chat.protocol.KeyRing` shared by all peers.
    :param loss_rate: probability (0..1) of dropping an outgoing datagram,
        used only to demonstrate loss recovery.
    :param on_event: optional callback ``on_event(kind, info)`` for progress.
    """

    def __init__(self, peer_id, host, port, keys, download_dir, loss_rate=0.0,
                 on_event=None, seed=None):
        self.peer_id, self.host = peer_id, host
        self.keys = keys
        self.loss_rate = float(loss_rate)
        self._random = random.Random(seed)
        self.on_event = on_event or (lambda kind, info: None)
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._transfers = {}
        self._completed = {}
        self._stop = threading.Event()
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.bind((host, port))
        self.port = self._socket.getsockname()[1]
        self._socket.settimeout(0.5)
        self._thread = threading.Thread(target=self._receive_loop, daemon=True,
                                        name="udp-file-receiver")
        self._thread.start()

    # ------------------------------------------------------------------
    # common helpers
    # ------------------------------------------------------------------
    def _send(self, sock, packet, address, signed=False):
        """Send one datagram, possibly dropping it to simulate loss."""
        if signed:
            packet = self.keys.sign(packet)
        data = json.dumps(packet, separators=(",", ":")).encode("utf-8")
        if self.loss_rate and self._random.random() < self.loss_rate:
            return len(data)
        try:
            return sock.sendto(data, address)
        except OSError:
            return 0

    @staticmethod
    def _decode(raw):
        packet = json.loads(raw.decode("utf-8"))
        if not isinstance(packet, dict):
            raise ValueError("datagram is not a JSON object")
        return packet

    def _ack(self, address, transfer_id, seq, complete=False, error=None, cum=None):
        packet = {"kind": "ACK", "transfer_id": transfer_id, "seq": seq,
                  "complete": complete}
        if cum is not None:
            packet["cum"] = cum
        if error:
            packet["error"] = error
        self._send(self._socket, packet, address, signed=True)

    # ------------------------------------------------------------------
    # receiver
    # ------------------------------------------------------------------
    def _receive_loop(self):
        last_cleanup = time.monotonic()
        while not self._stop.is_set():
            try:
                raw, address = self._socket.recvfrom(DATAGRAM_BUFFER)
            except socket.timeout:
                raw = None
            except OSError:
                break
            if raw is not None:
                try:
                    self._handle(self._decode(raw), address)
                except (ValueError, UnicodeError, KeyError, TypeError) as exc:
                    print("[UDP file] Dropped invalid datagram: {}".format(exc))
            if time.monotonic() - last_cleanup > 1.0:
                self.cleanup()
                last_cleanup = time.monotonic()

    def cleanup(self, now=None):
        """Drop transfers whose sender went silent and old completion records."""
        now = time.monotonic() if now is None else now
        with self._lock:
            stale = [tid for tid, t in self._transfers.items()
                     if now - t["updated"] > TRANSFER_IDLE_TIMEOUT]
            for tid in stale:
                transfer = self._transfers.pop(tid)
                self._discard_part(transfer)
            self._completed = {tid: value for tid, value in self._completed.items()
                               if now - value[2] < COMPLETED_MEMORY}
        for tid in stale:
            self.on_event("incoming_failed", {"transfer_id": tid,
                                              "error": "sender went silent"})
        return stale

    @staticmethod
    def _discard_part(transfer):
        try:
            transfer["handle"].close()
            os.unlink(transfer["part_path"])
        except OSError:
            pass

    def _handle(self, packet, address):
        transfer_id = packet.get("transfer_id")
        if not isinstance(transfer_id, str) or len(transfer_id) > 64:
            return
        kind = packet.get("kind")
        if kind == "START":
            self._handle_start(packet, address, transfer_id)
        elif kind == "DATA":
            self._handle_data(packet, address, transfer_id)

    def _handle_start(self, packet, address, transfer_id):
        if not self.keys.verify(packet):
            return
        with self._lock:
            if transfer_id in self._transfers:      # retransmitted START
                self._transfers[transfer_id]["updated"] = time.monotonic()
                known = True
            else:
                known = False
        if known:
            self._ack(address, transfer_id, -1)
            return
        size, count = int(packet.get("size", -1)), int(packet.get("chunks", -1))
        expected_count = max(1, -(-size // CHUNK_SIZE)) if size >= 0 else -1
        if not 0 <= size <= MAX_FILE_SIZE or count != expected_count:
            self._ack(address, transfer_id, -1, error="invalid size or chunk count")
            return
        filename = Path(str(packet.get("filename", "file.bin"))).name[:180] or "file.bin"
        part_path = self.download_dir / (".{}.part".format(transfer_id))
        handle = open(part_path, "w+b")
        transfer = {"filename": filename, "size": size, "chunks": count,
                    "sha256": str(packet.get("sha256", "")), "received": set(), "cum": 0,
                    "address": address, "updated": time.monotonic(),
                    "handle": handle, "part_path": part_path,
                    "sender": str(packet.get("sender", "?"))[:32]}
        with self._lock:
            self._transfers[transfer_id] = transfer
        self.on_event("incoming_started", {"transfer_id": transfer_id,
                                           "filename": filename, "size": size,
                                           "chunks": count,
                                           "peer_id": transfer["sender"]})
        self._ack(address, transfer_id, -1)

    def _handle_data(self, packet, address, transfer_id):
        seq = int(packet.get("seq", -1))
        with self._lock:
            completed = self._completed.get(transfer_id)
            transfer = self._transfers.get(transfer_id)
        if completed and completed[0] == address:
            self._ack(address, transfer_id, seq, complete=True)   # lost final ACK
            return
        if transfer is None or transfer["address"] != address:
            return
        if not 0 <= seq < transfer["chunks"]:
            return
        nonce = base64.b64decode(packet["nonce"], validate=True)
        ciphertext = base64.b64decode(packet["ciphertext"], validate=True)
        aad = "{}:{}".format(transfer_id, seq).encode("utf-8")
        try:
            chunk = self.keys.file.decrypt(nonce, ciphertext, aad)
        except Exception:
            self._ack(address, transfer_id, seq, error="AES-GCM authentication failed")
            return
        with self._lock:
            if seq not in transfer["received"]:
                transfer["handle"].seek(seq * CHUNK_SIZE)
                transfer["handle"].write(chunk)
                transfer["received"].add(seq)
                while transfer["cum"] in transfer["received"]:
                    transfer["cum"] += 1          # chunks 0..cum-1 all present
            transfer["updated"] = time.monotonic()
            cum = transfer["cum"]
            received = len(transfer["received"])
            done = received == transfer["chunks"] and transfer_id in self._transfers
            if done:
                del self._transfers[transfer_id]
        if not done:
            if received % 64 == 0:
                self.on_event("incoming_progress", {"transfer_id": transfer_id,
                                                    "received": received})
            self._ack(address, transfer_id, seq, cum=cum)
            return
        self._finish(transfer_id, transfer, address, seq)

    def _finish(self, transfer_id, transfer, address, seq):
        handle = transfer["handle"]
        handle.flush()
        handle.seek(0)
        digest = hashlib.sha256()
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
        size = handle.tell()
        handle.close()
        if size != transfer["size"] or not hmac.compare_digest(
                digest.hexdigest(), transfer["sha256"]):
            os.unlink(transfer["part_path"])
            self._ack(address, transfer_id, seq, error="file SHA-256 mismatch")
            self.on_event("incoming_failed", {"transfer_id": transfer_id,
                                              "error": "SHA-256 mismatch"})
            return
        target = self.download_dir / transfer["filename"]
        if target.exists():
            target = self.download_dir / "{}_{}{}".format(target.stem,
                                                          transfer_id[:8], target.suffix)
        os.replace(transfer["part_path"], target)
        with self._lock:
            self._completed[transfer_id] = (address, seq, time.monotonic())
        self._ack(address, transfer_id, seq, complete=True)
        self.on_event("incoming_done", {"transfer_id": transfer_id,
                                        "filename": target.name,
                                        "size": transfer["size"],
                                        "peer_id": transfer["sender"]})

    # ------------------------------------------------------------------
    # sender
    # ------------------------------------------------------------------
    def send_bytes(self, peer, content, filename, transfer_id=None, progress=None):
        """Send ``content`` (bytes) to ``peer`` and block until it is confirmed.

        :param peer: dict with ``host`` and ``udp_port`` of the receiver.
        :param progress: optional callback ``progress(stats_dict)``.
        :returns: statistics of the transfer.
        :raises TransferError: if the receiver refuses or stops answering.
        """
        size = len(content)
        if size > MAX_FILE_SIZE:
            raise TransferError("Maximum file size is 100 MiB")
        view = memoryview(content)
        count = max(1, -(-size // CHUNK_SIZE))
        transfer_id = transfer_id or secrets.token_hex(12)
        digest = hashlib.sha256(content).hexdigest()
        address = (peer["host"], int(peer["udp_port"]))
        stats = {"transfer_id": transfer_id, "filename": filename, "size": size,
                 "chunks": count, "sha256": digest, "acked": 0,
                 "datagrams_sent": 0, "retransmissions": 0, "fast_retransmits": 0,
                 "timeouts": 0}
        progress = progress or (lambda info: None)
        started = time.monotonic()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind((self.host, 0))
            self._start_handshake(sock, address, transfer_id, filename, size,
                                  count, digest)
            congestion = self._send_window(sock, address, transfer_id, view,
                                           count, stats, progress)
        stats["duration"] = round(time.monotonic() - started, 4)
        stats["throughput_kib_s"] = round(size / 1024 / max(stats["duration"], 1e-6), 1)
        stats["cwnd_trace"] = congestion.trace
        return stats

    def _start_handshake(self, sock, address, transfer_id, filename, size,
                         count, digest):
        start = {"kind": "START", "transfer_id": transfer_id, "sender": self.peer_id,
                 "filename": filename, "size": size, "chunks": count,
                 "sha256": digest}
        sock.settimeout(INITIAL_RTO)
        for _ in range(START_ATTEMPTS):
            self._send(sock, start, address, signed=True)
            deadline = time.monotonic() + INITIAL_RTO
            while time.monotonic() < deadline:
                ack = self._recv_ack(sock, address, transfer_id)
                if ack is None:
                    break                          # timeout: send START again
                if ack is False or ack.get("seq") != -1:
                    continue                       # unrelated or forged packet
                if ack.get("error"):
                    # Fixed: the receiver's reason reaches the caller instead of
                    # being swallowed and retried until "did not acknowledge".
                    raise TransferError("Receiver refused the file: {}".format(ack["error"]))
                return
        raise TransferError("Receiver did not acknowledge the transfer setup")

    def _recv_ack(self, sock, address, transfer_id):
        """Return an ACK dict, False for an ignorable packet, None on timeout."""
        try:
            raw, source = sock.recvfrom(DATAGRAM_BUFFER)
        except socket.timeout:
            return None
        if source != address:
            return False
        try:
            ack = self._decode(raw)
        except (ValueError, UnicodeError):
            return False
        if ack.get("transfer_id") != transfer_id or not self.keys.verify(ack):
            return False
        return ack

    def _data_packet(self, transfer_id, seq, view):
        nonce = secrets.token_bytes(12)
        aad = "{}:{}".format(transfer_id, seq).encode("utf-8")
        chunk = bytes(view[seq * CHUNK_SIZE:(seq + 1) * CHUNK_SIZE])
        encrypted = self.keys.file.encrypt(nonce, chunk, aad)
        return {"kind": "DATA", "transfer_id": transfer_id, "seq": seq,
                "nonce": base64.b64encode(nonce).decode("ascii"),
                "ciphertext": base64.b64encode(encrypted).decode("ascii")}

    def _send_window(self, sock, address, transfer_id, view, count, stats, progress):
        congestion, rtt = CubicWindow(), RttEstimator()
        pending = {}          # seq -> [packet, sent_at, retransmitted, attempts]
        acked = set()
        base = next_seq = 0
        dup_for, dup_acks = None, 0
        complete = False
        last_report = 0.0
        sock.settimeout(0.01)
        while not complete:
            # 1. Fill the window with new chunks.
            while next_seq < count and next_seq < base + congestion.window:
                packet = self._data_packet(transfer_id, next_seq, view)
                self._send(sock, packet, address)
                stats["datagrams_sent"] += 1
                pending[next_seq] = [packet, time.monotonic(), False, 0]
                next_seq += 1
            # 2. Process ACKs: "seq" acknowledges one chunk (selective), "cum"
            #    acknowledges every chunk below it (covers lost earlier ACKs).
            ack = self._recv_ack(sock, address, transfer_id)
            if ack:
                if ack.get("error"):
                    raise TransferError("Receiver reported: {}".format(ack["error"]))
                seq, cum = ack.get("seq"), ack.get("cum")
                newly = [seq] if isinstance(seq, int) and seq in pending else []
                if isinstance(cum, int):
                    newly += [s for s in pending if s < cum and s != seq]
                for done_seq in newly:
                    entry = pending.pop(done_seq)
                    if done_seq == seq and not entry[2]:   # Karn: skip retransmitted
                        rtt.sample(time.monotonic() - entry[1])
                    acked.add(done_seq)
                    congestion.on_ack()
                    stats["acked"] += 1
                while base in acked:
                    acked.discard(base)
                    base += 1
                # Fast retransmit: three ACKs for later chunks while chunk "cum"
                # is still missing mean it was most likely lost.
                if isinstance(cum, int) and cum in pending and isinstance(seq, int) and seq > cum:
                    dup_acks = dup_acks + 1 if cum == dup_for else 1
                    dup_for = cum
                    if dup_acks == 3 and not pending[cum][2]:
                        entry = pending[cum]
                        self._send(sock, entry[0], address)
                        entry[1], entry[2] = time.monotonic(), True
                        congestion.on_loss()
                        stats["fast_retransmits"] += 1
                        stats["retransmissions"] += 1
                        stats["datagrams_sent"] += 1
                if ack.get("complete"):
                    stats["acked"] = count
                    complete = True
            # 3. Retransmit chunks whose timer expired (only those: selective).
            now = time.monotonic()
            expired = [seq for seq, entry in pending.items() if now - entry[1] >= rtt.rto]
            if expired:
                stats["timeouts"] += 1
                congestion.on_loss()
                rtt.backoff()
                for seq in expired:
                    entry = pending[seq]
                    entry[3] += 1
                    if entry[3] > MAX_RETRANSMISSIONS:
                        raise TransferError("Chunk {} was lost {} times; receiver unreachable"
                                            .format(seq, MAX_RETRANSMISSIONS))
                    self._send(sock, entry[0], address)
                    entry[1], entry[2] = now, True
                    stats["retransmissions"] += 1
                    stats["datagrams_sent"] += 1
            # 4. All chunks ACKed but the final "complete" ACK was lost:
            #    probe with the last chunk; the receiver re-ACKs from its cache.
            if not pending and next_seq == count and not complete:
                pending[count - 1] = [self._data_packet(transfer_id, count - 1, view),
                                      now - rtt.rto, True, 0]
            if now - last_report > 0.1:
                stats["cwnd"] = congestion.window
                stats["rto"] = round(rtt.rto, 4)
                progress(dict(stats))
                last_report = now
        stats["cwnd"] = congestion.window
        progress(dict(stats))
        return congestion

    def close(self):
        """Stop the receiver thread and release the UDP port."""
        self._stop.set()
        self._socket.close()
        self._thread.join(timeout=2)
        with self._lock:
            for transfer in self._transfers.values():
                self._discard_part(transfer)
            self._transfers.clear()
