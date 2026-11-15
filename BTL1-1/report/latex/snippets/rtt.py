class RttEstimator:
    """Retransmission timeout from smoothed RTT samples (RFC 6298)."""

    ALPHA, BETA = 1 / 8, 1 / 4

    def __init__(self):
        self.srtt = None
        self.rttvar = None
        self.rto = INITIAL_RTO

    def sample(self, rtt):
        """Feed one RTT measured on a chunk that was sent only once."""
        if self.srtt is None:
            self.srtt, self.rttvar = rtt, rtt / 2
        else:
            self.rttvar = (1 - self.BETA) * self.rttvar + self.BETA * abs(self.srtt - rtt)
            self.srtt = (1 - self.ALPHA) * self.srtt + self.ALPHA * rtt
        self.rto = min(MAX_RTO, max(MIN_RTO, self.srtt + 4 * self.rttvar))

    def backoff(self):
        """Double the timeout after a loss (exponential back-off)."""
        self.rto = min(MAX_RTO, self.rto * 2)
