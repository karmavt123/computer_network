"""Unit tests of the reliable UDP file transfer (in one process, real sockets)."""

import hashlib
import json
import os
import socket
import tempfile
import time
import unittest
from pathlib import Path

from tests import ROOT  # noqa: F401
from chat import udp_files
from chat.protocol import KeyRing
from chat.udp_files import (CHUNK_SIZE, CubicWindow, ReliableUdpFiles, RttEstimator,
                            TransferError)


class Endpoints(unittest.TestCase):
    """Two UDP endpoints (sender, receiver) in temporary folders."""

    def make(self, name, loss=0.0, key="k", seed=1):
        folder = Path(self.tmp.name) / name
        endpoint = ReliableUdpFiles(name, "127.0.0.1", 0, KeyRing(key), folder,
                                    loss_rate=loss, seed=seed)
        self.addCleanup(endpoint.close)
        return endpoint

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    @staticmethod
    def target(endpoint):
        return {"host": "127.0.0.1", "udp_port": endpoint.port}

    def received(self, endpoint, name):
        return (endpoint.download_dir / name).read_bytes()


class TransferTest(Endpoints):
    def test_transfer_without_loss(self):
        sender, receiver = self.make("a"), self.make("b")
        data = os.urandom(200_000)
        stats = sender.send_bytes(self.target(receiver), data, "x.bin")
        self.assertEqual(self.received(receiver, "x.bin"), data)
        self.assertEqual(stats["chunks"], -(-len(data) // CHUNK_SIZE))
        self.assertEqual(stats["acked"], stats["chunks"])

    def test_transfer_survives_heavy_loss_in_both_directions(self):
        """20 % of DATA and 20 % of ACK datagrams are dropped."""
        sender, receiver = self.make("a", loss=0.2, seed=7), self.make("b", loss=0.2, seed=8)
        data = os.urandom(120_000)
        stats = sender.send_bytes(self.target(receiver), data, "lossy.bin")
        self.assertEqual(hashlib.sha256(self.received(receiver, "lossy.bin")).hexdigest(),
                         hashlib.sha256(data).hexdigest())
        self.assertGreater(stats["retransmissions"], 0)
        # Selective retransmission: far fewer datagrams than "resend everything".
        self.assertLess(stats["datagrams_sent"], 2 * stats["chunks"])

    def test_empty_file_and_name_sanitising(self):
        sender, receiver = self.make("a"), self.make("b")
        sender.send_bytes(self.target(receiver), b"", "../../etc/evil.txt")
        self.assertEqual(self.received(receiver, "evil.txt"), b"")
        self.assertFalse((Path(self.tmp.name) / "etc").exists())

    def test_same_name_does_not_overwrite(self):
        sender, receiver = self.make("a"), self.make("b")
        sender.send_bytes(self.target(receiver), b"one", "a.txt")
        sender.send_bytes(self.target(receiver), b"two", "a.txt")
        names = sorted(p.name for p in receiver.download_dir.iterdir())
        self.assertEqual(len(names), 2)

    def test_wrong_key_receiver_ignores_start(self):
        sender, receiver = self.make("a", key="k1"), self.make("b", key="k2")
        old = udp_files.START_ATTEMPTS
        udp_files.START_ATTEMPTS = 2
        try:
            with self.assertRaisesRegex(TransferError, "did not acknowledge"):
                sender.send_bytes(self.target(receiver), b"data", "x")
        finally:
            udp_files.START_ATTEMPTS = old

    def test_receiver_error_reaches_sender(self):
        """Regression: the receiver's reason used to be swallowed and retried."""
        sender, receiver = self.make("a"), self.make("b")
        receiver._handle_start = lambda packet, address, tid: receiver._ack(
            address, tid, -1, error="disk full")
        with self.assertRaisesRegex(TransferError, "disk full"):
            sender.send_bytes(self.target(receiver), b"data", "x")

    def test_unreachable_receiver_gives_clear_error(self):
        sender = self.make("a")
        old = udp_files.START_ATTEMPTS
        udp_files.START_ATTEMPTS = 2
        try:
            with self.assertRaises(TransferError):
                sender.send_bytes({"host": "127.0.0.1", "udp_port": 9}, b"data", "x")
        finally:
            udp_files.START_ATTEMPTS = old


class ReceiverTest(Endpoints):
    """Drive the receiver with hand-made datagrams."""

    def start(self, receiver, keys, data, transfer_id="t1"):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        sock.settimeout(1)
        self.addCleanup(sock.close)
        count = max(1, -(-len(data) // CHUNK_SIZE))
        start = keys.sign({"kind": "START", "transfer_id": transfer_id, "sender": "t",
                           "filename": "f.bin", "size": len(data), "chunks": count,
                           "sha256": hashlib.sha256(data).hexdigest()})
        sock.sendto(json.dumps(start).encode(), ("127.0.0.1", receiver.port))
        ack = json.loads(sock.recvfrom(2048)[0])
        self.assertEqual(ack["seq"], -1)
        return sock, count

    def test_out_of_order_and_duplicate_chunks(self):
        keys = KeyRing("k")
        receiver, helper = self.make("b"), self.make("h")
        data = os.urandom(CHUNK_SIZE * 5 + 17)
        sock, count = self.start(receiver, keys, data)
        order = [5, 3, 3, 0, 4, 1, 2]                    # reversed-ish with a duplicate
        last = None
        for seq in order:
            packet = helper._data_packet("t1", seq, memoryview(data))
            sock.sendto(json.dumps(packet).encode(), ("127.0.0.1", receiver.port))
            last = json.loads(sock.recvfrom(2048)[0])
        self.assertTrue(last["complete"])
        self.assertEqual(self.received(receiver, "f.bin"), data)

    def test_invalid_chunk_count_is_refused(self):
        keys = KeyRing("k")
        receiver = self.make("b")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(1)
        self.addCleanup(sock.close)
        start = keys.sign({"kind": "START", "transfer_id": "t2", "filename": "f",
                           "size": 10, "chunks": 99, "sha256": ""})
        sock.sendto(json.dumps(start).encode(), ("127.0.0.1", receiver.port))
        ack = json.loads(sock.recvfrom(2048)[0])
        self.assertIn("invalid", ack["error"])

    def test_silent_sender_is_forgotten(self):
        """Regression: half-finished transfers used to stay in memory forever."""
        keys = KeyRing("k")
        receiver = self.make("b")
        self.start(receiver, keys, b"x" * 5000, transfer_id="t3")
        self.assertIn("t3", receiver._transfers)
        part = receiver._transfers["t3"]["part_path"]
        self.assertTrue(part.exists())
        stale = receiver.cleanup(now=time.monotonic() + udp_files.TRANSFER_IDLE_TIMEOUT + 1)
        self.assertEqual(stale, ["t3"])
        self.assertNotIn("t3", receiver._transfers)
        self.assertFalse(part.exists())

    def test_tampered_chunk_is_rejected(self):
        keys = KeyRing("k")
        receiver, helper = self.make("b"), self.make("h")
        data = b"y" * 100
        sock, _ = self.start(receiver, keys, data, transfer_id="t4")
        packet = helper._data_packet("t4", 0, memoryview(data))
        packet["seq"] = 0
        packet["ciphertext"] = packet["ciphertext"][:-4] + "AAAA"
        sock.sendto(json.dumps(packet).encode(), ("127.0.0.1", receiver.port))
        ack = json.loads(sock.recvfrom(2048)[0])
        self.assertIn("authentication", ack["error"])


class CongestionTest(unittest.TestCase):
    def test_slow_start_then_multiplicative_decrease(self):
        now = [0.0]
        window = CubicWindow(initial=4, maximum=64, clock=lambda: now[0])
        for _ in range(10):
            window.on_ack()
        self.assertEqual(window.window, 14)               # +1 per ACK in slow start
        window.on_loss()
        self.assertAlmostEqual(window.cwnd, 14 * 0.7)
        self.assertEqual(window.ssthresh, window.cwnd)

    def test_cubic_growth_returns_to_wmax_after_k_seconds(self):
        now = [0.0]
        window = CubicWindow(initial=40, maximum=64, clock=lambda: now[0])
        window.on_loss()                                  # W_max = 40, cwnd = 28
        k = ((40 * 0.3) / 0.4) ** (1 / 3)
        now[0] = k
        window.on_ack()
        self.assertAlmostEqual(window.cwnd, 40, delta=0.5)
        now[0] = k + 3
        window.on_ack()
        self.assertGreater(window.cwnd, 40)               # convex probing beyond W_max

    def test_rtt_estimator(self):
        rtt = RttEstimator()
        for _ in range(20):
            rtt.sample(0.1)
        self.assertAlmostEqual(rtt.srtt, 0.1, places=3)
        self.assertLess(rtt.rto, 0.2)
        rtt.backoff()
        self.assertGreater(rtt.rto, 0.1)


if __name__ == "__main__":
    unittest.main()
