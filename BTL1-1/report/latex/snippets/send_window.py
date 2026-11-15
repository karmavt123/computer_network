def _send_window(self, sock, address, transfer_id, view, count, stats, progress):
    congestion, rtt = CubicWindow(), RttEstimator()
    pending = {}          # seq -> [packet, sent_at, retransmitted, attempts]
    acked = set()
    base = next_seq = 0
    dup_for, dup_acks = None, 0
    complete = False
    last_report = 0.0
    sock.settimeout(0.01)
    while not complete:
        # 1. Fill the window with new chunks.
        while next_seq < count and next_seq < base + congestion.window:
            packet = self._data_packet(transfer_id, next_seq, view)
            self._send(sock, packet, address)
            stats["datagrams_sent"] += 1
            pending[next_seq] = [packet, time.monotonic(), False, 0]
            next_seq += 1
        # 2. Process ACKs: "seq" acknowledges one chunk (selective), "cum"
        #    acknowledges every chunk below it (covers lost earlier ACKs).
        ack = self._recv_ack(sock, address, transfer_id)
        if ack:
            if ack.get("error"):
                raise TransferError("Receiver reported: {}".format(ack["error"]))
            seq, cum = ack.get("seq"), ack.get("cum")
            newly = [seq] if isinstance(seq, int) and seq in pending else []
            if isinstance(cum, int):
                newly += [s for s in pending if s < cum and s != seq]
            for done_seq in newly:
                entry = pending.pop(done_seq)
                if done_seq == seq and not entry[2]:   # Karn: skip retransmitted
                    rtt.sample(time.monotonic() - entry[1])
                acked.add(done_seq)
                congestion.on_ack()
                stats["acked"] += 1
            while base in acked:
                acked.discard(base)
                base += 1
            # Fast retransmit: three ACKs for later chunks while chunk "cum"
            # is still missing mean it was most likely lost.
            if isinstance(cum, int) and cum in pending and isinstance(seq, int) and seq > cum:
                dup_acks = dup_acks + 1 if cum == dup_for else 1
                dup_for = cum
                if dup_acks == 3 and not pending[cum][2]:
                    entry = pending[cum]
                    self._send(sock, entry[0], address)
                    entry[1], entry[2] = time.monotonic(), True
                    congestion.on_loss()
                    stats["fast_retransmits"] += 1
                    stats["retransmissions"] += 1
                    stats["datagrams_sent"] += 1
            if ack.get("complete"):
                stats["acked"] = count
                complete = True
        # 3. Retransmit chunks whose timer expired (only those: selective).
        now = time.monotonic()
        expired = [seq for seq, entry in pending.items() if now - entry[1] >= rtt.rto]
        if expired:
            stats["timeouts"] += 1
            congestion.on_loss()
            rtt.backoff()
            for seq in expired:
                entry = pending[seq]
                entry[3] += 1
                if entry[3] > MAX_RETRANSMISSIONS:
                    raise TransferError("Chunk {} was lost {} times; receiver unreachable"
                                        .format(seq, MAX_RETRANSMISSIONS))
                self._send(sock, entry[0], address)
                entry[1], entry[2] = now, True
                stats["retransmissions"] += 1
                stats["datagrams_sent"] += 1
        # 4. All chunks ACKed but the final "complete" ACK was lost:
        #    probe with the last chunk; the receiver re-ACKs from its cache.
        if not pending and next_seq == count and not complete:
            pending[count - 1] = [self._data_packet(transfer_id, count - 1, view),
                                  now - rtt.rto, True, 0]
        if now - last_report > 0.1:
            stats["cwnd"] = congestion.window
            stats["rto"] = round(rtt.rto, 4)
            progress(dict(stats))
            last_report = now
    stats["cwnd"] = congestion.window
    progress(dict(stats))
    return congestion
