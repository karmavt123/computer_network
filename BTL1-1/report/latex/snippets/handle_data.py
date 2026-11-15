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
