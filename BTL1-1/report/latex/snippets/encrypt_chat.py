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
