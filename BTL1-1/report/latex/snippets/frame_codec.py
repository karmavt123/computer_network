def encode_frame(payload):
    """Serialise ``payload`` (a dict) into a length-prefixed frame."""
    encoded = json.dumps(payload, ensure_ascii=False,
                         separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_FRAME:
        raise ProtocolError("Message exceeds the 64 KiB protocol limit")
    return struct.pack("!I", len(encoded)) + encoded

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
