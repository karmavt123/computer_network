"""Unit tests of the wire format: framing, encryption, control-packet MACs."""

import socket
import struct
import unittest

from tests import ROOT  # noqa: F401  (puts the project on sys.path)
from chat.protocol import (MAX_FRAME, FrameBuffer, KeyRing, ProtocolError, decrypt_chat,
                           encode_frame, encrypt_chat, recv_frame, send_frame)


def chat_frame(**overrides):
    frame = {"type": "CHAT", "message_id": "m1", "sender": "alice", "channel": "general",
             "timestamp": 1, "origin": "peer1", "to": None}
    frame.update(overrides)
    return frame


class FramingTest(unittest.TestCase):
    def test_round_trip_over_a_socket_pair(self):
        left, right = socket.socketpair()
        with left, right:
            send_frame(left, {"type": "CONNECT", "peer_id": "p1", "text": "xin chào"})
            self.assertEqual(recv_frame(right)["text"], "xin chào")

    def test_frame_split_into_single_bytes_is_reassembled(self):
        """TCP may deliver a frame in pieces; the buffer must wait for all of it."""
        data = encode_frame({"a": 1}) + encode_frame({"b": 2})
        buffer, frames = FrameBuffer(), []
        for i in range(len(data)):
            buffer.feed(data[i:i + 1])
            frames.extend(buffer.frames())
        self.assertEqual(frames, [{"a": 1}, {"b": 2}])

    def test_oversized_and_empty_frames_are_rejected(self):
        with self.assertRaises(ProtocolError):
            encode_frame({"x": "y" * MAX_FRAME})
        for length in (0, MAX_FRAME + 1):
            buffer = FrameBuffer()
            buffer.feed(struct.pack("!I", length) + b"{}")
            with self.assertRaises(ProtocolError):
                list(buffer.frames())

    def test_non_object_json_is_rejected(self):
        buffer = FrameBuffer()
        buffer.feed(struct.pack("!I", 2) + b"[]")
        with self.assertRaises(ProtocolError):
            list(buffer.frames())


class EncryptionTest(unittest.TestCase):
    def setUp(self):
        self.keys = KeyRing("shared")

    def test_chat_text_round_trip_and_ciphertext_hides_text(self):
        wire = encrypt_chat(self.keys, chat_frame(), "bí mật")
        self.assertNotIn("text", wire)
        self.assertNotIn("bí mật", str(wire))
        self.assertEqual(decrypt_chat(self.keys, wire)["text"], "bí mật")

    def test_changing_visible_metadata_breaks_authentication(self):
        for field, value in (("sender", "mallory"), ("channel", "study"), ("to", "peer9"),
                             ("origin", "peer2"), ("timestamp", 2)):
            wire = encrypt_chat(self.keys, chat_frame(), "hello")
            wire[field] = value
            with self.subTest(field=field), self.assertRaises(ProtocolError):
                decrypt_chat(self.keys, wire)

    def test_wrong_key_is_rejected(self):
        wire = encrypt_chat(self.keys, chat_frame(), "hello")
        with self.assertRaises(ProtocolError):
            decrypt_chat(KeyRing("other"), wire)

    def test_control_packet_mac(self):
        packet = self.keys.sign({"kind": "ACK", "seq": 3})
        self.assertTrue(self.keys.verify(packet))
        self.assertFalse(self.keys.verify({**packet, "seq": 4}))
        self.assertFalse(KeyRing("other").verify(packet))
        self.assertFalse(self.keys.verify({"kind": "ACK", "seq": 3}))

    def test_purposes_use_different_keys(self):
        nonce = b"\0" * 12
        a = self.keys.chat.encrypt(nonce, b"x", None)
        b = self.keys.file.encrypt(nonce, b"x", None)
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()
