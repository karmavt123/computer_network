def on_ack(self):
    """Grow the window after a new chunk was acknowledged."""
    now = self._clock()
    if self.cwnd < self.ssthresh:
        self.cwnd = min(self.maximum, self.cwnd + 1.0)
    else:
        if self.epoch is None:
            self.epoch = now
        elapsed = now - self.epoch
        k = ((self.w_max * (1.0 - self.BETA)) / self.C) ** (1.0 / 3.0)
        target = self.C * ((elapsed - k) ** 3) + self.w_max
        self.cwnd = min(self.maximum, max(self.cwnd + 1.0 / self.cwnd, target))
    self._record(now)

def on_loss(self):
    """Multiplicative decrease after a retransmission timeout."""
    now = self._clock()
    self.w_max = self.cwnd
    self.cwnd = max(self.MIN_WINDOW, self.cwnd * self.BETA)
    self.ssthresh = self.cwnd
    self.epoch = now
    self._record(now)
