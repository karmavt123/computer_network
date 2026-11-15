"""Wire format shared by the tracker, the peer daemons and the file sender.

Three things live here so that every process speaks exactly the same
protocol:

* TCP framing for chat frames: ``uint32 length (network order) || UTF-8 JSON``.
  TCP is a byte stream, so a receiver needs the length to know where one
  message ends and the next begins.
* Key derivation: one shared passphrase is stretched into independent keys
  for chat payloads, file chunks and UDP control packets.
* Authenticated encryption of chat text with AES-GCM, where the visible
  metadata of the frame is bound as associated data (AAD).
"""

import base64
import hashlib
import hmac
import json
import secrets
import struct

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:  # pragma: no cover - checked at start-up
    AESGCM = None

#: Largest accepted chat frame (bytes of JSON after the length prefix).
MAX_FRAME = 64 * 1024

#: Frame types exchanged between peer daemons over TCP.
FRAME_CONNECT = "CONNECT"
FRAME_CONNECT_OK = "CONNECT_OK"
FRAME_CHAT = "CHAT"

#: Visible fields of a CHAT frame that are authenticated (not encrypted).
CHAT_AAD_FIELDS = ("type", "message_id", "sender", "channel", "timestamp",
                   "origin", "to")

#: Fields a CHAT frame must carry on the wire.
CHAT_REQUIRED_FIELDS = ("message_id", "sender", "channel", "timestamp",
                        "origin", "nonce", "ciphertext")


class ProtocolError(ValueError):
    """Raised when a peer sends something that violates the protocol."""


def read_exact(sock, size):
    """Read exactly ``size`` bytes from a blocking socket.

    ``recv`` may return fewer bytes than requested (a TCP segment boundary
    is not a message boundary), so the call is repeated until enough data
    has arrived.

    :param sock: connected TCP socket.
    :param size: number of bytes to read.
    :raises ConnectionError: if the peer closes the connection early.
    """
    chunks = bytearray()
    while len(chunks) < size:
        part = sock.recv(size - len(chunks))
        if not part:
            raise ConnectionError("Peer closed the connection")
        chunks.extend(part)
    return bytes(chunks)


def encode_frame(payload):
    """Serialise ``payload`` (a dict) into a length-prefixed frame."""
    encoded = json.dumps(payload, ensure_ascii=False,
                         separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_FRAME:
        raise ProtocolError("Message exceeds the 64 KiB protocol limit")
    return struct.pack("!I", len(encoded)) + encoded


def send_frame(sock, payload):
    """Send one frame over a blocking TCP socket."""
    sock.sendall(encode_frame(payload))


def decode_frame_body(raw):
    """Decode the JSON body of a frame and check that it is an object."""
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("Frame is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ProtocolError("Frame must be a JSON object")
    return value


def check_frame_length(size):
    """Validate a length prefix before allocating memory for the body."""
    if size == 0 or size > MAX_FRAME:
        raise ProtocolError("Invalid frame length {}".format(size))
    return size


def recv_frame(sock):
    """Receive one frame from a blocking TCP socket."""
    size = check_frame_length(struct.unpack("!I", read_exact(sock, 4))[0])
    return decode_frame_body(read_exact(sock, size))


class FrameBuffer:
    """Incremental frame decoder for non-blocking sockets.

    Bytes are appended as they arrive with :meth:`feed`; :meth:`frames`
    yields every complete frame and keeps any partial frame for later.
    This is how the callback-driven peer listener copes with a frame that
    is split over several ``recv`` calls (TCP fragmentation).
    """

    def __init__(self):
        self._data = bytearray()

    def feed(self, data):
        """Append received bytes."""
        self._data.extend(data)

    def frames(self):
        """Yield all complete frames currently buffered."""
        while len(self._data) >= 4:
            size = check_frame_length(struct.unpack("!I", self._data[:4])[0])
            if len(self._data) < 4 + size:
                return
            body = bytes(self._data[4:4 + size])
            del self._data[:4 + size]
            yield decode_frame_body(body)


class KeyRing:
    """Independent keys derived from one shared passphrase.

    The same key must never serve two purposes, so HMAC-SHA256 with a
    distinct label derives one key for chat text, one for file chunks and
    one for authenticating UDP control packets.
    """

    def __init__(self, passphrase):
        if AESGCM is None:
            raise RuntimeError("Install the 'cryptography' package: "
                               "python -m pip install -r requirements.txt")
        master = hashlib.sha256(passphrase.encode("utf-8")).digest()
        self.chat = AESGCM(self._derive(master, b"hcmut-chat-text"))
        self.file = AESGCM(self._derive(master, b"hcmut-chat-file-aes"))
        self.control_key = self._derive(master, b"hcmut-udp-control")

    @staticmethod
    def _derive(master, label):
        return hmac.new(master, label, hashlib.sha256).digest()

    def sign(self, packet):
        """Return ``packet`` with an HMAC-SHA256 ``mac`` field added."""
        unsigned = {key: value for key, value in packet.items() if key != "mac"}
        return {**unsigned, "mac": self._mac(unsigned)}

    def verify(self, packet):
        """Check the ``mac`` field of a control packet in constant time."""
        supplied = packet.get("mac")
        if not isinstance(supplied, str):
            return False
        unsigned = {key: value for key, value in packet.items() if key != "mac"}
        return hmac.compare_digest(supplied, self._mac(unsigned))

    def _mac(self, unsigned):
        canonical = json.dumps(unsigned, sort_keys=True,
                               separators=(",", ":")).encode("utf-8")
        return hmac.new(self.control_key, canonical, hashlib.sha256).hexdigest()


def chat_aad(frame):
    """Canonical associated data of a chat frame.

    Changing any of these visible fields in transit (for example the sender
    or the channel) makes AES-GCM authentication fail at the receiver.
    """
    metadata = {key: frame.get(key) for key in CHAT_AAD_FIELDS}
    return json.dumps(metadata, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def encrypt_chat(keys, frame, text):
    """Return a copy of ``frame`` with ``text`` replaced by nonce + ciphertext."""
    nonce = secrets.token_bytes(12)
    ciphertext = keys.chat.encrypt(nonce, text.encode("utf-8"), chat_aad(frame))
    wire = {key: value for key, value in frame.items() if key != "text"}
    wire["nonce"] = base64.b64encode(nonce).decode("ascii")
    wire["ciphertext"] = base64.b64encode(ciphertext).decode("ascii")
    return wire


def decrypt_chat(keys, wire):
    """Decrypt a CHAT frame; raises ``ProtocolError`` if it was tampered with."""
    try:
        nonce = base64.b64decode(wire["nonce"], validate=True)
        ciphertext = base64.b64decode(wire["ciphertext"], validate=True)
        plain = keys.chat.decrypt(nonce, ciphertext, chat_aad(wire))
        text = plain.decode("utf-8")
    except Exception as exc:  # InvalidTag, binascii.Error, KeyError, ...
        raise ProtocolError("Chat payload failed authentication") from exc
    message = {key: value for key, value in wire.items()
               if key not in ("nonce", "ciphertext")}
    message["text"] = text
    return message
