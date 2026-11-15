"""End-to-end tests: a real tracker and real peers in separate processes.

This is the "local network simulation" of the rubric: every process is
started with the same command a user would type in its own terminal.
"""

import hashlib
import json
import os
import signal
import socket
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from tests import ROOT  # noqa: F401
from tests.helpers import Client, Cluster, wait_until
from chat.peer import ChatNode
from chat.protocol import encrypt_chat


def peer_ids(client):
    status, body = client.call("/api/peers")
    return {p["peer_id"]: p for p in body["peers"]}


def texts(client, channel=None):
    path = "/api/messages?after=0" + ("&channel=" + channel.replace("@", "%40") if channel else "")
    return [m["text"] for m in client.call(path)[1]["messages"]]


class ChatFlowTest(unittest.TestCase):
    """Login, discovery, connection, broadcast, DM, files, failures."""

    mode = "coroutine"

    @classmethod
    def setUpClass(cls):
        cls.cluster = Cluster(mode=cls.mode)
        cls.tracker = cls.cluster.start_tracker()
        cls.a = cls.cluster.start_peer("p1", loss=0.1)       # drops 10 % of its DATA
        cls.b = cls.cluster.start_peer("p2", loss=0.15)
        cls.c = cls.cluster.start_peer("p3")

    @classmethod
    def tearDownClass(cls):
        cls.cluster.close()

    def test_01_login_is_checked_by_the_tracker(self):
        self.assertEqual(self.a.call("/api/me")[0], 401)
        status, body = self.a.call("/login", {"username": "alice", "password": "nope"})
        self.assertEqual(status, 401)
        self.cluster.login("p1", "alice")
        self.cluster.login("p2", "bob")
        self.cluster.login("p3", "carol")
        self.assertEqual(self.a.call("/api/me")[1]["username"], "alice")

    def test_02_tracker_tracks_users_addresses_and_ports(self):
        peers = {p["peer_id"]: p for p in self.tracker.call("/get-list")[1]["peers"]}
        self.assertEqual({k: v["username"] for k, v in peers.items()},
                         {"p1": "alice", "p2": "bob", "p3": "carol"})
        self.assertEqual(peers["p1"]["port"], self.cluster.peers["p1"]["tcp"])
        self.assertEqual(peers["p1"]["udp_port"], self.cluster.peers["p1"]["udp"])
        self.assertEqual(peers["p1"]["observed_ip"], "127.0.0.1")
        self.assertNotIn("token", peers["p1"])

    def test_03_registration_cannot_be_hijacked(self):
        fake = {"peer_id": "p1", "host": "127.0.0.1", "port": 1234, "udp_port": 1235}
        self.assertEqual(self.tracker.call("/submit-info", fake)[0], 403)
        self.assertEqual(self.tracker.call("/remove", {"peer_id": "p1"})[0], 403)
        self.assertEqual(self.tracker.call("/login", {"peer_id": "p1", "token": "x",
                                                      "username": "dave",
                                                      "password": "hcmut2026"})[0], 403)
        peers = {p["peer_id"]: p for p in self.tracker.call("/get-list")[1]["peers"]}
        self.assertEqual(peers["p1"]["username"], "alice")

    def test_04_broadcast_reaches_connected_peers_directly(self):
        status, body = self.a.call("/connect-peer", {"peer_id": "p2"})
        self.assertEqual(status, 200, body)
        self.assertTrue(peer_ids(self.b)["p1"]["connected"])      # handshake is mutual
        status, body = self.a.call("/broadcast-peer", {"channel": "general",
                                                       "text": "chào cả nhóm"})
        self.assertEqual(body["delivery"]["direct"], ["p2"])     # p3 is not connected
        self.assertEqual(body["delivery"]["relayed"], [])
        wait_until(lambda: "chào cả nhóm" in texts(self.b, "general"))
        time.sleep(0.3)
        self.assertNotIn("chào cả nhóm", texts(self.c))

        self.a.call("/connect-peer", {"all": True})
        self.a.call("/api/send", {"channel": "study", "text": "bài tập"})
        wait_until(lambda: "bài tập" in texts(self.c, "study"))
        message = wait_until(lambda: [m for m in self.c.call(
            "/api/messages?after=0&channel=study")[1]["messages"] if m["verified"]])[0]
        self.assertEqual((message["sender"], message["origin"]), ("alice", "p1"))

    def test_05_direct_message_only_reaches_its_recipient(self):
        status, body = self.b.call("/send-peer", {"peer_id": "p3", "text": "riêng cho carol"})
        self.assertEqual(status, 200, body)
        wait_until(lambda: "riêng cho carol" in texts(self.c, "@p2"))
        self.assertIn("riêng cho carol", texts(self.b, "@p3"))     # sender's copy
        self.assertNotIn("riêng cho carol", texts(self.a))

    def test_06_validation_errors(self):
        self.assertEqual(self.a.call("/api/send", {"channel": "nope", "text": "x"})[0], 400)
        self.assertEqual(self.a.call("/api/send", {"channel": "general", "text": ""})[0], 400)
        self.assertEqual(self.a.call("/api/send", {"channel": "general",
                                                   "text": "x" * 2001})[0], 400)
        self.assertEqual(self.a.call("/send-peer", {"peer_id": "ghost", "text": "x"})[0], 404)
        self.assertEqual(Client(self.a.base).call("/api/peers")[0], 401)   # no cookie

    def test_07_file_transfer_over_lossy_udp(self):
        data = os.urandom(400_000)
        status, body = self.a.call("/api/files/send", raw=data, headers={
            "Content-Type": "application/octet-stream", "X-Peer-Id": "p2",
            "X-Filename": "b%C3%A1o%20c%C3%A1o.bin"})
        self.assertEqual(status, 202, body)
        transfer_id = body["transfer_id"]

        def finished():
            items = {t["transfer_id"]: t for t in self.a.call("/api/transfers")[1]["transfers"]}
            item = items.get(transfer_id)
            return item if item and item["status"] != "sending" else None
        item = wait_until(finished, timeout=60)
        self.assertEqual(item["status"], "done", item)
        self.assertGreater(item["retransmissions"], 0)      # lost DATA had to be resent
        received = (self.cluster.downloads("p2") / "báo cáo.bin").read_bytes()
        self.assertEqual(hashlib.sha256(received).digest(), hashlib.sha256(data).digest())
        incoming = self.b.call("/api/transfers")[1]["transfers"][0]
        self.assertEqual((incoming["direction"], incoming["status"]), ("in", "done"))
        files = self.b.call("/api/files")[1]["files"]
        status, content = self.b.call(files[0]["url"])
        self.assertEqual(content, data)

    def test_08_crashed_peer_does_not_break_the_others(self):
        node = self.cluster.start_peer("p4")
        self.cluster.login("p4", "dave")
        self.a.call("/connect-peer", {"peer_id": "p4"})
        self.cluster.stop("p4", signal.SIGKILL)              # no goodbye to anyone
        status, body = self.a.call("/broadcast-peer", {"channel": "general",
                                                       "text": "ai còn đó?"})
        self.assertEqual(status, 200)
        self.assertIn("p4", body["delivery"]["failed"])
        self.assertIn("p2", body["delivery"]["direct"])
        self.assertFalse(peer_ids(self.a)["p4"]["connected"])

    def test_09_graceful_exit_removes_the_peer(self):
        self.cluster.start_peer("p5")
        wait_until(lambda: "p5" in peer_ids(self.a))
        self.cluster.stop("p5", signal.SIGINT)                # Ctrl+C
        wait_until(lambda: "p5" not in peer_ids(self.a))

    def test_10_crashed_peer_can_restart_at_once(self):
        """The saved peer token lets a restarted peer reclaim its id."""
        self.cluster.start_peer("p6")
        self.cluster.stop("p6", signal.SIGKILL)
        self.cluster.start_peer("p6")                         # same download folder
        self.cluster.login("p6", "dave")
        wait_until(lambda: peer_ids(self.a).get("p6", {}).get("username") == "dave")

    def test_11_sigterm_is_a_graceful_exit(self):
        self.cluster.start_peer("p7")
        wait_until(lambda: "p7" in peer_ids(self.a))
        self.cluster.stop("p7", signal.SIGTERM)               # plain "kill"
        wait_until(lambda: "p7" not in peer_ids(self.a))


class ThreadingModeTest(ChatFlowTest):
    """The same scenario with the thread-per-client HTTP servers."""

    mode = "threading"


class TrackerDownTest(unittest.TestCase):
    def test_chat_continues_without_the_tracker(self):
        cluster = Cluster()
        self.addCleanup(cluster.close)
        cluster.start_tracker()
        a, b = cluster.start_peer("p1"), cluster.start_peer("p2")
        cluster.login("p1", "alice")
        cluster.login("p2", "bob")
        a.call("/connect-peer", {"peer_id": "p2"})
        cluster.stop("tracker", signal.SIGKILL)
        status, body = a.call("/api/peers")
        self.assertEqual(status, 200)
        self.assertFalse(body["tracker_online"])
        self.assertIn("p2", {p["peer_id"] for p in body["peers"]})      # cached list
        status, body = a.call("/broadcast-peer", {"channel": "general", "text": "P2P vẫn chạy"})
        self.assertEqual(body["delivery"]["direct"], ["p2"])
        wait_until(lambda: "P2P vẫn chạy" in texts(b))


class ScaleTest(unittest.TestCase):
    def test_twenty_clients_register_and_one_broadcast_reaches_twelve_peers(self):
        cluster = Cluster()
        self.addCleanup(cluster.close)
        tracker = cluster.start_tracker()

        # 20 extra clients hit the tracker at the same time.
        def register(i):
            return tracker.call("/submit-info", {"peer_id": "load{}".format(i),
                                                 "host": "127.0.0.1", "port": 20000 + i,
                                                 "udp_port": 21000 + i})[0]
        with ThreadPoolExecutor(max_workers=20) as pool:
            self.assertEqual(list(pool.map(register, range(20))), [200] * 20)

        names = ["n{}".format(i) for i in range(12)]
        with ThreadPoolExecutor(max_workers=12) as pool:
            clients = dict(zip(names, pool.map(cluster.start_peer, names)))
        for name in names:
            cluster.login(name, "alice")
        sender = clients["n0"]
        results = sender.call("/connect-peer", {"all": True})[1]["results"]
        self.assertEqual(sorted(k for k, v in results.items() if v is None), sorted(names[1:]))
        body = sender.call("/broadcast-peer", {"channel": "general", "text": "hello 12"})[1]
        self.assertEqual(sorted(body["delivery"]["direct"]), sorted(names[1:]))
        for name in names[1:]:
            wait_until(lambda name=name: "hello 12" in texts(clients[name]))


class NodeUnitTest(unittest.TestCase):
    """ChatNode logic that is hard to provoke from outside."""

    def setUp(self):
        self.node = ChatNode("me", "127.0.0.1", 0, 0, "http://127.0.0.1:9", "k",
                             "/tmp/chat-node-test")
        self.addCleanup(self.node.files.close)
        self.node._peers = [{"peer_id": "p2", "host": "127.0.0.1", "port": 9,
                             "udp_port": 9, "username": "bob"}]

    def frame(self, sender, **extra):
        frame = {"type": "CHAT", "message_id": os.urandom(6).hex(), "sender": sender,
                 "channel": "general", "timestamp": 1, "origin": "p2", "to": None}
        frame.update(extra)
        return encrypt_chat(self.node.keys, frame, "hi")

    def test_sender_name_is_checked_against_the_tracker(self):
        self.node.receive(self.frame("bob"))
        self.node.receive(self.frame("alice"))          # p2 is bob, not alice
        verified = [(m["sender"], m["verified"]) for m in self.node.messages()]
        self.assertEqual(verified, [("bob", True), ("alice", False)])

    def test_duplicates_and_foreign_dms_are_dropped(self):
        frame = self.frame("bob")
        self.assertIsNotNone(self.node.receive(frame))
        self.assertIsNone(self.node.receive(frame))                     # direct + relay copy
        self.assertIsNone(self.node.receive(self.frame("bob", to="someone-else",
                                                       channel="dm")))
        dm = self.node.receive(self.frame("bob", to="me", channel="dm"))
        self.assertEqual(dm["channel"], "@p2")

    def test_cursor_is_local_not_the_senders_clock(self):
        """Regression: a peer with a slow clock used to have its messages skipped."""
        late = self.frame("bob", timestamp=10**13)
        early = self.frame("bob", timestamp=1)                           # wrong clock
        self.node.receive(late)
        cursor = self.node.messages()[-1]["seq"]
        self.node.receive(early)
        self.assertEqual(len(self.node.messages(after=cursor)), 1)

    def test_relay_fallback_when_direct_delivery_fails(self):
        self.node.username, self.node.token = "alice", "t"
        self.node._connected = {"p2": time.time()}
        self.node._deliver = lambda peer, wire: (_ for _ in ()).throw(ConnectionRefusedError())
        calls = []

        def fake_tracker(path, data=None):
            calls.append((path, data["targets"]))
            return {"delivered": data["targets"]}
        self.node._tracker = fake_tracker
        message, report = self.node.send("general", "via tracker")
        self.assertEqual(calls, [("/relay", ["p2"])])
        self.assertEqual(report["relayed"], ["p2"])
        self.assertEqual(report["failed"], {})


if __name__ == "__main__":
    unittest.main()
