"""Tests of the HTTP framework: parser, both server modes, proxy."""

import json
import socket
import tempfile
import threading
import time
import unittest
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from tests import ROOT  # noqa: F401
from tests.helpers import free_port, wait_until
from daemon import backend
from daemon.httpadapter import HttpAdapter
from daemon.request import Request
from daemon.streaprous import StreapRous
import daemon.proxy as proxy
from start_proxy import parse_virtual_hosts


def raw_http(port, request, timeout=5):
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as conn:
        conn.sendall(request)
        chunks = []
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    return b"".join(chunks)


class RequestTest(unittest.TestCase):
    def test_query_string_and_route_lookup(self):
        handler = object()
        req = Request().prepare("GET /api/messages?after=5&channel=%40p2 HTTP/1.1\r\n"
                                "Host: x\r\nCookie: a=1; b=2",
                                {("GET", "/api/messages"): handler}, body=b"",
                                client="10.0.0.9")
        self.assertIs(req.hook, handler)
        self.assertEqual(req.query, {"after": "5", "channel": "@p2"})
        self.assertEqual(req.headers[":client"], "10.0.0.9")
        self.assertEqual(req.cookies["b"], "2")

    def test_client_cannot_inject_pseudo_headers(self):
        req = Request().prepare("GET / HTTP/1.1\r\n:client: 6.6.6.6\r\nX-A: 1", {},
                                body=b"", client="10.0.0.1")
        self.assertEqual(req.headers[":client"], "10.0.0.1")

    def test_binary_body_is_not_decoded(self):
        body = bytes(range(256))
        req = Request().prepare("POST /f HTTP/1.1\r\nContent-Type: application/octet-stream",
                                {}, body=body)
        self.assertEqual(req.body, body)

    def test_legacy_full_message_form(self):
        req = Request().prepare("POST /echo HTTP/1.1\r\nHost: a\r\n\r\n{\"x\":1}")
        self.assertEqual(json.loads(req.body), {"x": 1})


def make_app():
    app = StreapRous()

    @app.route("/slow", methods=["GET"])
    def slow(headers, body):
        time.sleep(0.5)                       # a blocking handler
        return {"ok": True}

    @app.route("/echo", methods=["POST"])
    def echo(headers, body):
        return {"body": body, "client": headers[":client"]}

    @app.route("/async", methods=["GET"])
    async def coroutine(headers, body):
        return {"async": True}

    @app.route("/boom", methods=["GET"])
    def boom(headers, body):
        raise RuntimeError("bug")

    return app


class ServerModesTest(unittest.TestCase):
    """The same app served in coroutine and in threading mode."""

    def serve(self, mode):
        port = free_port()
        backend.set_mode(mode)
        app = make_app()
        threading.Thread(target=backend.create_backend, args=("127.0.0.1", port, app.routes),
                         daemon=True).start()
        wait_until(lambda: self._up(port))
        self.addCleanup(backend.set_mode, "coroutine")
        return port

    @staticmethod
    def _up(port):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return True
        except OSError:
            return False

    def check_mode(self, mode):
        port = self.serve(mode)
        base = "http://127.0.0.1:{}".format(port)
        request = urllib.request.Request(base + "/echo", data=b'{"a": 1}', method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(json.loads(response.read())["client"], "127.0.0.1")
        with urllib.request.urlopen(base + "/async", timeout=5) as response:
            self.assertEqual(json.loads(response.read()), {"async": True})
        reply = raw_http(port, b"GET /boom HTTP/1.1\r\nHost: x\r\n\r\n")
        self.assertIn(b"500 Internal Server Error", reply)
        reply = raw_http(port, b"GARBAGE\r\n\r\n")
        self.assertIn(b"400", reply)

        # 20 clients call a handler that blocks for 0.5 s. Served one after the
        # other this would take 10 s; concurrently it takes about 0.5-1 s.
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(
                lambda _: urllib.request.urlopen(base + "/slow", timeout=15).status, range(20)))
        elapsed = time.monotonic() - started
        self.assertEqual(results, [200] * 20)
        self.assertLess(elapsed, 2.0, "{} mode served clients one by one".format(mode))

    def test_coroutine_mode(self):
        self.check_mode("coroutine")

    def test_threading_mode(self):
        self.check_mode("threading")

    def test_static_files_and_path_traversal(self):
        port = self.serve("coroutine")
        ok = raw_http(port, b"GET /index.html HTTP/1.1\r\nHost: x\r\n\r\n")
        self.assertIn(b"200 OK", ok)
        bad = raw_http(port, b"GET /../apps/chat_app.py HTTP/1.1\r\nHost: x\r\n\r\n")
        self.assertIn(b"404", bad)
        bad = raw_http(port, b"GET /../../../../etc/passwd.html HTTP/1.1\r\nHost: x\r\n\r\n")
        self.assertIn(b"404", bad)


class ProxyTest(unittest.TestCase):
    def setUp(self):
        self.backends = []
        for name in ("A", "B"):
            app = StreapRous()
            app.route("/who", methods=["GET"])(
                lambda headers, body, name=name: {"backend": name, "host": headers.get("host"),
                                                  "xff": headers.get("x-forwarded-for")})
            port = free_port()
            threading.Thread(target=backend.create_backend, args=("127.0.0.1", port, app.routes),
                             daemon=True).start()
            self.backends.append(port)
        self.dead = free_port()
        for port in self.backends:
            wait_until(lambda port=port: ServerModesTest._up(port))
        self.proxy_port = free_port()
        routes = {
            "pool.local": (["127.0.0.1:{}".format(p) for p in self.backends], "round-robin",
                           {"Host": "$host"}),
            "failover.local": (["127.0.0.1:{}".format(self.dead),
                                "127.0.0.1:{}".format(self.backends[0])], "round-robin", {}),
            "dead.local": ("127.0.0.1:{}".format(self.dead), "round-robin", {}),
        }
        threading.Thread(target=proxy.run_proxy, args=("127.0.0.1", self.proxy_port, routes),
                         daemon=True).start()
        wait_until(lambda: ServerModesTest._up(self.proxy_port))

    def get(self, host):
        reply = raw_http(self.proxy_port,
                         "GET /who HTTP/1.1\r\nHost: {}\r\n\r\n".format(host).encode())
        head, _, body = reply.partition(b"\r\n\r\n")
        return head.split(b" ")[1].decode(), body

    def test_round_robin_alternates(self):
        seen = [json.loads(self.get("pool.local")[1])["backend"] for _ in range(4)]
        self.assertEqual(sorted(seen), ["A", "A", "B", "B"])
        self.assertNotEqual(seen[0], seen[1])
        reply = json.loads(self.get("pool.local")[1])
        self.assertEqual(reply["host"], "pool.local")
        self.assertEqual(reply["xff"], "127.0.0.1")

    def test_failover_unknown_host_and_dead_backend(self):
        for _ in range(3):
            status, body = self.get("failover.local")
            self.assertEqual(status, "200")
        self.assertEqual(self.get("nowhere.local")[0], "404")
        self.assertEqual(self.get("dead.local")[0], "502")

    def test_config_parser(self):
        with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as conf:
            conf.write('# comment\nhost "a.local" {\n proxy_pass http://1.2.3.4:80;\n}\n'
                       'host "b.local" {\n proxy_set_header Host $host;\n'
                       ' proxy_pass http://1.1.1.1:1;\n proxy_pass http://2.2.2.2:2;\n'
                       ' dist_policy least-conn\n}\n')
        routes = parse_virtual_hosts(conf.name)
        self.assertEqual(routes["a.local"], ("1.2.3.4:80", "round-robin", {}))
        self.assertEqual(routes["b.local"][1], "least-conn")
        self.assertEqual(routes["b.local"][2], {"Host": "$host"})
        self.assertEqual(len(routes["b.local"][0]), 2)
        self.assertEqual(len(parse_virtual_hosts(str(ROOT / "config" / "proxy.conf"))), 3)


if __name__ == "__main__":
    unittest.main()
