"""Unit tests of the payload encryption (X25519 + HKDF + AES-256-GCM)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from daemon import secure


class TestSecure(unittest.TestCase):
    def setUp(self):
        a_priv, a_pub = secure.new_keypair()
        b_priv, b_pub = secure.new_keypair()
        self.key_a = secure.derive_key(a_priv, b_pub, "alice", "bob")
        self.key_b = secure.derive_key(b_priv, a_pub, "bob", "alice")

    def test_both_sides_derive_the_same_key(self):
        self.assertEqual(self.key_a, self.key_b)
        self.assertEqual(len(self.key_a), 32)

    def test_roundtrip_and_ciphertext_hides_plaintext(self):
        box = secure.seal(self.key_a, {"text": "top secret"}, "alice", "bob")
        self.assertNotIn("top secret", str(box))
        self.assertEqual(secure.unseal(self.key_b, box, "alice", "bob"), {"text": "top secret"})

    def test_nonce_is_fresh_for_every_message(self):
        one = secure.seal(self.key_a, {"n": 1}, "alice", "bob")
        two = secure.seal(self.key_a, {"n": 1}, "alice", "bob")
        self.assertNotEqual(one["nonce"], two["nonce"])
        self.assertNotEqual(one["ct"], two["ct"])

    def test_tampering_is_detected(self):
        box = secure.seal(self.key_a, {"text": "hello"}, "alice", "bob")
        raw = bytearray(__import__("base64").b64decode(box["ct"]))
        raw[0] ^= 1
        box["ct"] = __import__("base64").b64encode(bytes(raw)).decode()
        with self.assertRaises(secure.InvalidTag):
            secure.unseal(self.key_b, box, "alice", "bob")

    def test_cannot_be_replayed_to_another_user(self):
        box = secure.seal(self.key_a, {"text": "hello"}, "alice", "bob")
        with self.assertRaises(secure.InvalidTag):
            secure.unseal(self.key_b, box, "alice", "carol")

    def test_third_party_cannot_decrypt(self):
        c_priv, _ = secure.new_keypair()
        _, a_pub = secure.new_keypair()
        eve = secure.derive_key(c_priv, a_pub, "alice", "bob")
        box = secure.seal(self.key_a, {"text": "hello"}, "alice", "bob")
        with self.assertRaises(secure.InvalidTag):
            secure.unseal(eve, box, "alice", "bob")


if __name__ == "__main__":
    unittest.main()
