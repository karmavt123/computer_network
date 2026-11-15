"""Test helpers: free ports, an HTTP client with cookies, and a process cluster.

:class:`Cluster` starts a real tracker and real peer daemons as separate
processes (the same commands a user types in several terminals), so the
end-to-end tests exercise the actual sockets.
"""

import http.cookiejar
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PASSWORD = "hcmut2026"


def free_port(kind=socket.SOCK_STREAM):
    """Ask the OS for a currently unused port."""
    with socket.socket(socket.AF_INET, kind) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Client:
    """Minimal HTTP client that keeps cookies (one browser)."""

    def __init__(self, base):
        self.base = base
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, path, data=None, raw=None, headers=None, timeout=10):
        """Return ``(status, decoded JSON or bytes)``."""
        body = raw if raw is not None else (
            json.dumps(data).encode("utf-8") if data is not None else None)
        all_headers = {"Content-Type": "application/json"}
        all_headers.update(headers or {})
        request = urllib.request.Request(self.base + path, data=body, headers=all_headers,
                                         method="POST" if body is not None else "GET")
        try:
            with self.opener.open(request, timeout=timeout) as response:
                return response.status, self._decode(response.read(), response.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, self._decode(exc.read(), exc.headers)

    @staticmethod
    def _decode(raw, headers):
        if "json" in headers.get("Content-Type", ""):
            return json.loads(raw.decode("utf-8") or "{}")
        return raw


def wait_until(predicate, timeout=10.0, interval=0.05):
    """Poll ``predicate`` until it returns a truthy value or time runs out."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("condition not met within {} s".format(timeout))


class Cluster:
    """A tracker plus peers, each in its own process."""

    def __init__(self, mode="coroutine"):
        self.mode = mode
        self.workdir = Path(tempfile.mkdtemp(prefix="chat-test-"))
        self.users = self.workdir / "users.json"
        shutil.copy(ROOT / "db" / "users.json", self.users)
        self.processes = {}
        self.peers = {}
        self.tracker_port = None

    def _spawn(self, name, args):
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        with open(self.workdir / (name + ".log"), "w") as log:
            # The child keeps its own copy of the file descriptor.
            self.processes[name] = subprocess.Popen(
                [sys.executable] + args, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                env=env)

    def start_tracker(self):
        """Start the tracker and wait until it answers."""
        self.tracker_port = free_port()
        self._spawn("tracker", ["start_tracker.py", "--server-port", str(self.tracker_port),
                                "--users", str(self.users), "--mode", self.mode])
        tracker = Client(self.tracker_url)
        wait_until(lambda: self._answers(tracker, "/get-list"))
        return tracker

    @property
    def tracker_url(self):
        return "http://127.0.0.1:{}".format(self.tracker_port)

    def start_peer(self, peer_id, loss=0.0, key="test-shared-key", wait=True):
        """Start one peer daemon; returns a :class:`Client` for its web UI."""
        http_port, tcp_port, udp_port = free_port(), free_port(), free_port(socket.SOCK_DGRAM)
        self._spawn(peer_id, [
            "start_sampleapp.py", "--server-port", str(http_port), "--peer-id", peer_id,
            "--peer-port", str(tcp_port), "--file-port", str(udp_port),
            "--tracker-url", self.tracker_url, "--shared-key", key,
            "--download-dir", str(self.workdir / "downloads" / peer_id),
            "--loss", str(loss), "--mode", self.mode])
        client = Client("http://127.0.0.1:{}".format(http_port))
        self.peers[peer_id] = {"client": client, "http": http_port, "tcp": tcp_port,
                               "udp": udp_port}
        if wait:
            wait_until(lambda: self._answers(client, "/api/me", ok=(401,)))
        return client

    @staticmethod
    def _answers(client, path, ok=(200,)):
        try:
            return client.call(path, timeout=1)[0] in ok
        except OSError:
            return False

    def login(self, peer_id, username):
        """Sign ``username`` in on ``peer_id``; returns the browser client."""
        client = self.peers[peer_id]["client"]
        status, body = client.call("/api/login", {"username": username, "password": PASSWORD})
        assert status == 200, body
        return client

    def downloads(self, peer_id):
        return self.workdir / "downloads" / peer_id

    def stop(self, name, sig=signal.SIGINT):
        """Stop one process (SIGINT = Ctrl+C, SIGKILL = crash)."""
        process = self.processes.pop(name)
        process.send_signal(sig)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def close(self):
        for name in list(self.processes):
            self.stop(name, signal.SIGKILL)
        shutil.rmtree(self.workdir, ignore_errors=True)

    def log(self, name):
        return (self.workdir / (name + ".log")).read_text(errors="replace")
