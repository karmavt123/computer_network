"""
End-to-end tests: start a tracker, three peers, two plain backends and the proxy as real
processes on localhost and drive them with plain HTTP (standard library only).

Run:   python -m unittest -v tests.test_e2e
Mode:  STREAPROUS_TEST_MODE=threading python -m unittest -v tests.test_e2e   (default: coroutine)
"""

import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODE = os.environ.get("STREAPROUS_TEST_MODE", "coroutine")
HOST = "127.0.0.1"


def free_port():
    with socket.socket() as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


class Client:
    """Tiny browser: keeps cookies, speaks JSON."""

    def __init__(self, port, host=HOST):
        self.host, self.port, self.cookies = host, port, {}

    def request(self, method, path, body=None, headers=None, raw_body=None, ctype="application/json"):
        hdrs = {"X-Requested-With": "fetch"}
        data = raw_body
        if body is not None:
            data = json.dumps(body)
        if data is not None:
            hdrs["Content-Type"] = ctype
        if self.cookies:
            hdrs["Cookie"] = "; ".join("{}={}".format(k, v) for k, v in self.cookies.items())
        hdrs.update(headers or {})
        conn = http.client.HTTPConnection(self.host, self.port, timeout=10)
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        raw = resp.read()
        for header, value in resp.getheaders():
            if header.lower() == "set-cookie":
                name, _, rest = value.partition("=")
                token = rest.split(";")[0]
                if "Max-Age=0" in value:
                    self.cookies.pop(name, None)
                else:
                    self.cookies[name] = token
        conn.close()
        try:
            payload = json.loads(raw) if raw[:1] in (b"{", b"[") else raw
        except ValueError:
            payload = raw
        return resp.status, payload, dict(resp.getheaders())

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body, **kw)


def wait_port(port, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        try:
            socket.create_connection((HOST, port), 0.2).close()
            return True
        except OSError:
            time.sleep(0.1)
    return False


class Stack:
    """All daemons of the system under test."""

    def __init__(self):
        self.procs = []
        self.p = {k: free_port() for k in ("tracker", "alice", "bob", "carol", "proxy", "b1", "b2")}

    def spawn(self, *args):
        proc = subprocess.Popen([sys.executable, "-u"] + list(args), cwd=ROOT,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.procs.append(proc)

    def start(self):
        p = self.p
        self.spawn("start_chatapp.py", "--role", "tracker", "--server-ip", HOST,
                   "--server-port", str(p["tracker"]), "--mode", MODE)
        for name in ("alice", "bob", "carol"):
            self.spawn("start_chatapp.py", "--role", "peer", "--server-ip", HOST,
                       "--server-port", str(p[name]), "--tracker", "{}:{}".format(HOST, p["tracker"]),
                       "--advertise-ip", HOST, "--mode", MODE)
        for key, tag in (("b1", "one"), ("b2", "two")):
            self.spawn("tests/echo_backend.py", str(p[key]), tag)
        conf = tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False)
        conf.write('host "app.test" {\n proxy_set_header X-Seen-Host $host;\n'
                   ' proxy_pass http://%s:%d;\n proxy_pass http://%s:%d;\n dist_policy round-robin\n}\n'
                   'host "tracker.test" {\n proxy_pass http://%s:%d;\n}\n'
                   'host "dead.test" {\n proxy_pass http://%s:%d;\n}\n'
                   % (HOST, p["b1"], HOST, p["b2"], HOST, p["tracker"], HOST, free_port()))
        conf.close()
        self.conf = conf.name
        self.spawn("start_proxy.py", "--server-ip", HOST, "--server-port", str(p["proxy"]),
                   "--config", conf.name)
        for port in p.values():
            if not wait_port(port):
                raise RuntimeError("daemon on port %d did not start" % port)

    def stop(self):
        for proc in self.procs:
            proc.terminate()
        for proc in self.procs:
            try:
                proc.wait(3)
            except subprocess.TimeoutExpired:
                proc.kill()
        os.unlink(self.conf)


stack = None


def setUpModule():
    global stack
    stack = Stack()
    stack.start()


def tearDownModule():
    stack.stop()


def login(port, user, password):
    client = Client(port)
    status, data, _ = client.post("/login", {"username": user, "password": password})
    return client, status, data


class TestTracker(unittest.TestCase):
    def test_login_failures(self):
        c = Client(stack.p["tracker"])
        self.assertEqual(c.post("/login", {"username": "alice", "password": "nope"})[0], 401)
        self.assertEqual(c.post("/login", {"username": "ghost", "password": "x"})[0], 401)
        self.assertEqual(c.post("/login", {"username": "", "password": ""})[0], 400)
        self.assertEqual(c.post("/login", raw_body="{broken json", )[0], 400)

    def test_login_sets_httponly_cookie(self):
        c = Client(stack.p["tracker"])
        status, data, headers = c.post("/login", {"username": "bob", "password": "bob123"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        cookie = headers["Set-Cookie"]
        self.assertIn("sid=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)

    def test_protected_routes_need_auth(self):
        c = Client(stack.p["tracker"])
        for path in ("/get-list", "/channels"):
            self.assertEqual(c.get(path)[0], 401, path)
        self.assertEqual(c.post("/submit-info", {"ip": HOST, "port": 1})[0], 401)
        self.assertEqual(c.post("/add-list", {"channel": "#x", "members": []})[0], 401)

    def test_admin_basic_auth(self):
        c = Client(stack.p["tracker"])
        status, _, headers = c.get("/admin")
        self.assertEqual(status, 401)
        self.assertIn("Basic", headers["WWW-Authenticate"])
        import base64
        good = "Basic " + base64.b64encode(b"admin:admin123").decode()
        bad = "Basic " + base64.b64encode(b"admin:wrong").decode()
        user = "Basic " + base64.b64encode(b"alice:alice123").decode()
        self.assertEqual(c.get("/admin", headers={"Authorization": good})[0], 200)
        self.assertEqual(c.get("/admin", headers={"Authorization": bad})[0], 401)
        self.assertEqual(c.get("/admin", headers={"Authorization": user})[0], 401)

    def test_submit_info_validation_and_list(self):
        c, status, _ = login(stack.p["tracker"], "carol", "carol123")
        self.assertEqual(status, 200)
        self.assertEqual(c.post("/submit-info", {"ip": "not-an-ip", "port": 80})[0], 400)
        self.assertEqual(c.post("/submit-info", {"ip": HOST, "port": 70000})[0], 400)
        self.assertEqual(c.post("/submit-info", {"ip": HOST, "port": 4242})[0], 200)
        status, data, _ = c.get("/get-list")
        self.assertEqual(status, 200)
        self.assertIn("carol", [p["username"] for p in data["peers"]])
        # identity always comes from the session: a body username is ignored
        c.post("/submit-info", {"ip": HOST, "port": 4243, "username": "alice"})
        names = {p["username"]: p["port"] for p in c.get("/get-list")[1]["peers"]}
        self.assertEqual(names["carol"], 4243)

    def test_channel_directory_and_access_control(self):
        alice, _, _ = login(stack.p["tracker"], "alice", "alice123")
        bob, _, _ = login(stack.p["tracker"], "bob", "bob123")
        carol, _, _ = login(stack.p["tracker"], "carol", "carol123")
        status, data, _ = alice.post("/add-list", {"channel": "secret", "members": ["bob"], "private": True})
        self.assertEqual(status, 200)
        self.assertEqual(data["members"], ["alice", "bob"])
        self.assertIn("#secret", [c["name"] for c in bob.get("/channels")[1]["channels"]])
        self.assertNotIn("#secret", [c["name"] for c in carol.get("/channels")[1]["channels"]])
        self.assertEqual(carol.get("/get-list?channel=%23secret")[0], 403)
        self.assertEqual(carol.post("/add-list", {"channel": "#secret", "members": ["carol"]})[0], 403)
        self.assertEqual(bob.post("/add-list", {"channel": "#secret", "members": ["carol"]})[0], 403)
        self.assertEqual(alice.post("/add-list", {"channel": "#bad", "members": ["nobody"]})[0], 400)

    def test_brute_force_lockout(self):
        c = Client(stack.p["tracker"])
        for _ in range(5):
            c.post("/login", {"username": "admin", "password": "wrong"})
        self.assertEqual(c.post("/login", {"username": "admin", "password": "admin123"})[0], 429)

    def test_static_and_errors(self):
        c = Client(stack.p["tracker"])
        self.assertEqual(c.get("/")[0], 200)
        self.assertEqual(c.get("/css/chat.css")[2]["Content-Type"].split(";")[0], "text/css")
        self.assertEqual(c.get("/js/login.js")[2]["Content-Type"].split(";")[0], "text/javascript")
        self.assertEqual(c.get("/nope.html")[0], 404)
        self.assertEqual(c.get("/../../etc/passwd")[0], 404)
        self.assertEqual(c.get("/%2e%2e/%2e%2e/etc/passwd")[0], 404)
        self.assertEqual(c.request("DELETE", "/login")[0], 405)


def p2p_session(user, password, poll=True):
    client, status, data = login(stack.p[user], user, password)
    assert status == 200, (user, status, data)
    return client


def poll_messages(client, channel, expect, since=0, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        data = client.get("/api/state?since=%d" % since)[1]
        got = [m for m in data["messages"] if m["channel"] == channel and m["kind"] == "chat"]
        if len(got) >= expect:
            return got
        time.sleep(0.15)
    return []


class TestPeers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.alice = p2p_session("alice", "alice123")
        cls.bob = p2p_session("bob", "bob123")
        cls.carol = p2p_session("carol", "carol123")

    def test_1_peer_discovery_and_connect(self):
        status, data, _ = self.alice.get("/get-list")
        self.assertEqual(status, 200)
        self.assertEqual({p["username"] for p in data["peers"]}, {"bob", "carol"})
        self.assertTrue(all(not p["connected"] for p in data["peers"]))
        status, data, _ = self.alice.post("/connect-peer", {"username": "bob"})
        self.assertEqual((status, data["ok"]), (200, True))
        self.assertEqual(self.alice.post("/connect-peer", {"username": "nobody"})[0], 502)
        self.assertEqual(self.alice.post("/connect-peer", {"username": "alice"})[0], 502)
        self.assertEqual(self.alice.post("/connect-peer", {})[0], 400)
        # connect-all handles the remaining peer concurrently
        data = self.bob.post("/connect-peer", {"all": True})[1]
        self.assertTrue(all(r["ok"] for r in data["results"].values()))
        self.assertTrue(self.carol.post("/connect-peer", {"all": True})[1]["ok"])

    def test_2_broadcast_reaches_everyone_directly(self):
        status, data, _ = self.alice.post("/broadcast-peer", {"channel": "#general", "text": "hello all"})
        self.assertEqual(status, 200)
        self.assertEqual(sorted(data["delivered"]), ["bob", "carol"])
        for client in (self.bob, self.carol):
            got = poll_messages(client, "#general", 1)
            self.assertEqual(got[0]["text"], "hello all")
            self.assertEqual(got[0]["sender"], "alice")

    def test_3_unicast_stays_private(self):
        status, data, _ = self.bob.post("/send-peer", {"to": "alice", "text": "psst"})
        self.assertEqual(status, 200, data)
        self.assertEqual(poll_messages(self.alice, "dm:alice:bob", 1)[0]["text"], "psst")
        state = self.carol.get("/api/state")[1]
        self.assertFalse(any(m["text"] == "psst" for m in state["messages"]))
        self.assertFalse(any(c["name"] == "dm:alice:bob" for c in state["channels"]))

    def test_4_validation_and_csrf_header(self):
        self.assertEqual(self.alice.post("/broadcast-peer", {"channel": "#general", "text": "   "})[0], 400)
        self.assertEqual(self.alice.post("/broadcast-peer", {"channel": "#general", "text": "x" * 2001})[0], 400)
        self.assertEqual(self.alice.post("/broadcast-peer", {"channel": "#nope", "text": "x"})[0], 404)
        self.assertEqual(self.alice.post("/send-peer", {"to": "alice", "text": "x"})[0], 400)
        no_header = self.alice.request("POST", "/broadcast-peer", {"channel": "#general", "text": "x"},
                                       headers={"X-Requested-With": ""})
        self.assertEqual(no_header[0], 403)
        anonymous = Client(stack.p["alice"])
        self.assertEqual(anonymous.get("/api/state")[0], 401)
        self.assertEqual(anonymous.post("/send-peer", {"to": "bob", "text": "x"})[0], 401)

    def test_5_xss_is_stored_verbatim(self):
        payload = "<img src=x onerror=alert(1)>"
        self.alice.post("/broadcast-peer", {"channel": "#general", "text": payload})
        got = poll_messages(self.bob, "#general", 2)
        self.assertIn(payload, [m["text"] for m in got])  # the browser renders it with textContent

    def _upload(self, client, channel, blob, name="data.bin", fid=None, size=None):
        import base64
        chunk = 48 * 1024
        fid = fid or os.urandom(16).hex()
        total = max(1, -(-len(blob) // chunk))
        last = None
        for i in range(total):
            part = base64.b64encode(blob[i * chunk:(i + 1) * chunk]).decode()
            last = client.post("/send-file", {"channel": channel, "fid": fid, "name": name,
                                              "size": len(blob) if size is None else size,
                                              "idx": i, "total": total, "data": part})
            if last[0] != 200:
                break
        return fid, last

    def _wait_file(self, client, fid, timeout=6):
        end = time.time() + timeout
        while time.time() < end:
            for m in client.get("/api/state")[1]["messages"]:
                if m.get("kind") == "file" and m["file"]["fid"] == fid:
                    return m
            time.sleep(0.15)
        return None

    def test_5b_file_transfer_is_chunked_verified_and_sanitised(self):
        import hashlib
        blob = os.urandom(300_000)                      # 7 segments of 48 KiB
        fid, last = self._upload(self.alice, "dm:alice:bob", blob, name="../../evil name.bin")
        self.assertEqual(last[0], 200, last)
        msg = self._wait_file(self.bob, fid)
        self.assertIsNotNone(msg, "bob never got the file message")
        self.assertEqual(msg["file"]["name"], "evil name.bin")        # path stripped
        self.assertEqual(msg["file"]["sha256"], hashlib.sha256(blob).hexdigest())
        status, content, headers = self.bob.get("/download?fid=" + fid)
        self.assertEqual(status, 200)
        self.assertEqual(content, blob)                                # byte-for-byte identical
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertEqual(self.alice.get("/download?fid=" + fid)[0], 404)  # the sender keeps no copy
        self.assertEqual(Client(stack.p["bob"]).get("/download?fid=" + fid)[0], 401)
        self.assertEqual(self.bob.get("/download?fid=../../etc/passwd")[0], 400)

    def test_5c_file_to_channel_and_protocol_errors(self):
        blob = os.urandom(1000)
        fid, last = self._upload(self.alice, "#general", blob, name="note.txt")
        self.assertEqual(last[1]["delivery"], {"bob": "ok", "carol": "ok"})
        for client in (self.bob, self.carol):
            self.assertIsNotNone(self._wait_file(client, fid))
        # empty file is legal
        fid, last = self._upload(self.alice, "dm:alice:bob", b"", name="empty")
        self.assertEqual(last[0], 200)
        # segments must arrive in order
        status, data, _ = self.alice.post("/send-file", {"channel": "dm:alice:bob", "fid": os.urandom(8).hex(),
                                                          "name": "x", "size": 10, "idx": 2, "total": 3, "data": "AAAA"})
        self.assertEqual(status, 409)
        # announced size must match what was streamed
        _, last = self._upload(self.alice, "dm:alice:bob", b"1234567890", name="lie", size=999)
        self.assertEqual(last[0], 400)
        # cannot use someone else's conversation
        self.assertEqual(self._upload(self.alice, "dm:bob:carol", b"x")[1][0], 403)
        # too large
        self.assertEqual(self._upload(self.alice, "dm:alice:bob", b"x", size=50 * 1024 * 1024)[1][0], 400)

    def test_6_forged_peer_traffic_is_rejected(self):
        raw = Client(stack.p["bob"])
        bad = raw.post("/peer-message", {"username": "alice", "p2p_token": "forged", "channel": "#general",
                                         "text": "fake", "mid": "m1"})
        self.assertEqual(bad[0], 403)
        hello = raw.post("/peer-hello", {"username": "alice", "p2p_token": "forged", "ip": HOST, "port": 1})
        self.assertEqual(hello[0], 403)
        # a linked peer must encrypt: plaintext fields are refused
        link = Client(stack.p["bob"])
        self.assertEqual(link.post("/peer-message", {"username": "alice", "channel": "#general", "text": "plain"})[0], 403)
        stranger = Client(stack.p["bob"])
        self.assertEqual(stranger.post("/peer-message", {"username": "mallory", "p2p_token": "x",
                                                         "channel": "dm:bob:mallory", "text": "x"})[0], 403)

    def test_7_private_channel_membership(self):
        status, data, _ = self.alice.post("/channels", {"name": "lab", "members": "bob", "private": True})
        self.assertEqual(status, 200, data)
        self.alice.get("/get-list"), self.bob.get("/get-list")
        data = self.alice.post("/broadcast-peer", {"channel": "#lab", "text": "for bob only"})[1]
        self.assertEqual(data["delivered"], ["bob"])
        self.assertEqual(poll_messages(self.bob, "#lab", 1)[0]["text"], "for bob only")
        state = self.carol.get("/api/state")[1]
        self.assertFalse(any(m["channel"] == "#lab" for m in state["messages"]))
        # carol cannot inject into #lab even with a valid link to bob
        self.assertEqual(self.carol.post("/broadcast-peer", {"channel": "#lab", "text": "x"})[0], 404)

    def test_8_concurrent_messages(self):
        results = []

        def send(i):
            results.append(self.alice.post("/broadcast-peer", {"channel": "#general", "text": "c%d" % i})[0])

        threads = [threading.Thread(target=send, args=(i,)) for i in range(20)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(results, [200] * 20)
        got = poll_messages(self.bob, "#general", 22)
        self.assertGreaterEqual(len({m["text"] for m in got if m["text"].startswith("c")}), 20)

    def test_9_dead_peer_is_reported_not_fatal(self):
        # carol disappears abruptly: sender must survive and report the failure
        carol_proc = stack.procs[3]
        carol_proc.kill()
        carol_proc.wait()
        data = self.alice.post("/broadcast-peer", {"channel": "#general", "text": "anyone there?"})[1]
        self.assertIn("carol", data["failed"])
        self.assertIn("bob", data["delivered"])
        self.assertEqual(self.alice.get("/api/state")[0], 200)


class TestProxy(unittest.TestCase):
    def test_round_robin_and_header_rewrite(self):
        c = Client(stack.p["proxy"], host=HOST)
        seen = []
        for _ in range(6):
            status, data, _ = c.get("/", headers={"Host": "app.test"})
            self.assertEqual(status, 200)
            seen.append(data["backend"])
        # strict alternation (the start point depends on earlier tests, so compare neighbours)
        self.assertEqual(set(seen), {"one", "two"})
        self.assertTrue(all(a != b for a, b in zip(seen, seen[1:])), seen)
        self.assertEqual(data["seen_host_header"], "app.test")
        self.assertEqual(data["forwarded_for"], HOST)

    def test_post_body_through_proxy(self):
        c = Client(stack.p["proxy"])
        body = {"blob": "z" * 20000}
        status, data, _ = c.post("/echo", body, headers={"Host": "app.test"})
        self.assertEqual(status, 200)
        self.assertEqual(data["body_len"], len(json.dumps(body)))

    def test_virtual_host_to_tracker_and_errors(self):
        c = Client(stack.p["proxy"])
        self.assertEqual(c.get("/health", headers={"Host": "tracker.test"})[1]["role"], "tracker")
        self.assertEqual(c.get("/", headers={"Host": "unknown.test"})[0], 404)
        self.assertEqual(c.get("/", headers={"Host": "dead.test"})[0], 502)


if __name__ == "__main__":
    unittest.main(verbosity=2)
