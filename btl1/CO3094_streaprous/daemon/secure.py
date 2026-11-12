#
# Copyright (C) 2026 pdnguyen of HCMC University of Technology VNU-HCM.
# All rights reserved.
# This file is part of the CO3093/CO3094 course.
#
# StreapRous release
#

"""
daemon.secure
~~~~~~~~~~~~~~~~~

Payload encryption for the peer-to-peer links (library: ``cryptography``).

* Key agreement: every ``/peer-hello`` carries an ephemeral **X25519** public key; both peers
  derive the same 256-bit key with **HKDF-SHA256** (salt = the two usernames, sorted).
* Data: every message / file chunk is sealed with **AES-256-GCM** (random 96-bit nonce).
  The sender and recipient names are bound as associated data, so a captured packet cannot be
  replayed towards another user and any bit flip is detected (``InvalidTag``).

A sniffer on the LAN therefore only sees ``{"nonce": ..., "ct": ...}``.
"""

import base64
import json
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

__all__ = ["InvalidTag", "new_keypair", "derive_key", "seal", "unseal"]

INFO = b"streaprous-p2p-v1"


def _b64(raw):
    return base64.b64encode(raw).decode("ascii")


def new_keypair():
    """Return ``(private_key, public_key_b64)`` for one handshake."""
    private = X25519PrivateKey.generate()
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return private, _b64(public)


def derive_key(private, peer_public_b64, user_a, user_b):
    """Shared AES key between ``user_a`` and ``user_b`` (order independent)."""
    peer_public = X25519PublicKey.from_public_bytes(base64.b64decode(peer_public_b64, validate=True))
    secret = private.exchange(peer_public)
    salt = ":".join(sorted((user_a, user_b))).encode("utf-8")
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=INFO).derive(secret)


def seal(key, obj, sender, recipient):
    """Encrypt a JSON-serialisable ``obj``; returns ``{"nonce": b64, "ct": b64}``."""
    nonce = os.urandom(12)
    aad = "{}>{}".format(sender, recipient).encode("utf-8")
    ct = AESGCM(key).encrypt(nonce, json.dumps(obj).encode("utf-8"), aad)
    return {"nonce": _b64(nonce), "ct": _b64(ct)}


def unseal(key, box, sender, recipient):
    """Decrypt what :func:`seal` produced. Raises ``InvalidTag`` / ``ValueError`` when tampered."""
    aad = "{}>{}".format(sender, recipient).encode("utf-8")
    nonce = base64.b64decode(box["nonce"], validate=True)
    ct = base64.b64decode(box["ct"], validate=True)
    return json.loads(AESGCM(key).decrypt(nonce, ct, aad).decode("utf-8"))
