def _loop(self):
    while not self._stopping.is_set():
        try:
            events = self.selector.select(timeout=0.5)
        except OSError:
            break
        for key, mask in events:
            key.data.handle(mask)          # dispatch to the callback object
        self._close_idle()

def handle(self, mask):
    """Selector callback of the listening socket: accept a connection."""
    try:
        sock, address = self._socket.accept()
    except (BlockingIOError, InterruptedError):
        return
    except OSError:
        return
    sock.setblocking(False)
    connection = _Connection(self, sock, address)
    self.connections.add(connection)
    self.selector.register(sock, selectors.EVENT_READ, connection)

def _on_readable(self):
    try:
        data = self.sock.recv(4096)
    except (BlockingIOError, InterruptedError):
        return
    except OSError:
        self.close()
        return
    if not data:                       # orderly close by the sender
        self.close()
        return
    self.last_activity = time.monotonic()
    self.inbox.feed(data)
    try:
        for frame in self.inbox.frames():
            reply = self.server.on_frame(frame, self.address)
            if reply is not None:
                self.outbox.extend(encode_frame(reply))
    except ProtocolError as exc:
        print("[Peer] Rejected frame from {}: {}".format(self.address, exc))
        self.close()
        return
    if self.outbox:
        self.server.selector.modify(self.sock, selectors.EVENT_READ | selectors.EVENT_WRITE,
                                    self)
