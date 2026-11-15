"""Unit tests of the tracker registry and the account store."""

import tempfile
import unittest
from pathlib import Path

from tests import ROOT  # noqa: F401
from chat.accounts import Sessions, UserStore
from chat.tracker import BadToken, NotRegistered, Tracker


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def peer(peer_id, port=9000):
    return {"peer_id": peer_id, "host": "10.0.0.1", "port": port, "udp_port": port + 1}


class TrackerTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.tracker = Tracker(ttl=45, clock=self.clock)

    def test_first_registration_returns_token_and_list_hides_it(self):
        token, peers = self.tracker.register(peer("p1"), observed_ip="10.0.0.1")
        self.assertTrue(token)
        self.assertEqual(peers[0]["peer_id"], "p1")
        self.assertEqual(peers[0]["observed_ip"], "10.0.0.1")
        self.assertNotIn("token", peers[0])

    def test_peer_id_cannot_be_taken_over_without_token(self):
        token, _ = self.tracker.register(peer("p1"))
        with self.assertRaises(BadToken):
            self.tracker.register(peer("p1", 7000))                 # attacker, no token
        with self.assertRaises(BadToken):
            self.tracker.unregister("p1", "guess")
        self.tracker.register(peer("p1", 9100), token=token)        # owner may move
        self.assertEqual(self.tracker.get("p1")["port"], 9100)

    def test_user_binding(self):
        token, _ = self.tracker.register(peer("p1"))
        self.tracker.bind_user("p1", token, "alice")
        self.assertEqual(self.tracker.get("p1")["username"], "alice")
        self.tracker.unbind_user("p1", token)
        self.assertIsNone(self.tracker.get("p1")["username"])
        with self.assertRaises(NotRegistered):
            self.tracker.bind_user("p9", token, "alice")

    def test_silent_peer_expires_and_id_becomes_free(self):
        self.tracker.register(peer("p1"))
        self.clock.now += 46
        self.assertEqual(self.tracker.list_peers(), [])
        token, _ = self.tracker.register(peer("p1"))                # a new owner
        self.assertTrue(self.tracker.verify("p1", token))

    def test_heartbeat_keeps_peer_alive(self):
        token, _ = self.tracker.register(peer("p1"))
        for _ in range(5):
            self.clock.now += 30
            self.tracker.register(peer("p1"), token=token)
        self.assertEqual(len(self.tracker.list_peers()), 1)


class AccountsTest(unittest.TestCase):
    def test_passwords_are_salted_hashes(self):
        with tempfile.TemporaryDirectory() as folder:
            store = UserStore(Path(folder) / "users.json")
            store.add("alice", "secret1")
            store.add("bob", "secret1")
            text = (Path(folder) / "users.json").read_text()
            self.assertNotIn("secret1", text)
            self.assertTrue(store.check("alice", "secret1"))
            self.assertFalse(store.check("alice", "wrong"))
            self.assertFalse(store.check("nobody", "secret1"))
            reloaded = UserStore(Path(folder) / "users.json")
            self.assertTrue(reloaded.check("bob", "secret1"))
            records = reloaded._users
            self.assertNotEqual(records["alice"]["hash"], records["bob"]["hash"])

    def test_demo_accounts_exist(self):
        store = UserStore(ROOT / "db" / "users.json")
        self.assertTrue(store.check("alice", "hcmut2026"))

    def test_session_cookie(self):
        sessions = Sessions("session_p1")
        value = sessions.create("alice")
        headers = {"cookie": "other=1; session_p1=" + value}
        self.assertEqual(sessions.username(headers), "alice")
        forged = value.replace("alice", "admin", 1)
        self.assertIsNone(sessions.username({"cookie": "session_p1=" + forged}))
        sessions.destroy(headers)
        self.assertIsNone(sessions.username(headers))


if __name__ == "__main__":
    unittest.main()
