# StreapRous P2P Chat - CO3094 Assignment 1

Stream-based HTTP daemon (proxy + backend + **StreapRous** webapp framework) and a **hybrid chat
application**: a central *tracker* (client-server paradigm) plus direct *peer-to-peer* messaging.
Python 3.10+, raw sockets / asyncio streams only - **no Flask/Django/WebSocket**. The browser UI uses
client-side JavaScript only.

```
 browser A --HTTP--> peer daemon A <==== P2P (AES-256-GCM) ====> peer daemon B <--HTTP-- browser B
                          \                                          /
                           +---- login / submit-info / get-list ----+---> tracker :9000
 optional: browser --HTTP--> proxy :8080 --(virtual host, round-robin)--> tracker / backends
```

## 1. Install

```bash
cd CO3094_streaprous
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt                          # only: cryptography
```

Nothing else is needed. (`requirements-raw.txt` adds `netifaces`, only for the optional raw transport.)

## 2. Run the chat (3 terminals, same machine)

| Terminal | Command |
|---|---|
| 1 - tracker | `python start_chatapp.py --role tracker --server-port 9000` |
| 2 - peer of user 1 | `python start_chatapp.py --role peer --server-port 9101 --tracker 127.0.0.1:9000` |
| 3 - peer of user 2 | `python start_chatapp.py --role peer --server-port 9102 --tracker 127.0.0.1:9000` |

Open `http://localhost:9101` and, in a **second browser window / profile**, `http://localhost:9102`.

Demo accounts (hashed in `db/users.json`): `alice / alice123`, `bob / bob123`, `carol / carol123`, `admin / admin123`.

Using the chat:

1. Sign in on each peer (one user per daemon; a third peer works the same way on port 9103).
2. In the sidebar press **Connect** next to a peer (or **Connect all**) - this is the P2P connection setup.
3. Write in `#general` (broadcast to every connected peer) or press **DM** for a private chat.
4. **+ New** creates a channel through the tracker (`members: bob, carol`, optionally *private*).
5. **Attach** sends a file (up to 20 MB) to the open channel / conversation; the receiver gets a download link.
6. Unread badges, toasts, the tab title and (optional) desktop notifications announce new messages.
   The pills in the header show the tracker and daemon status (red = unreachable).

### Several machines (LAN)

Run the tracker on one host and, on every other host, start the peer with its own LAN address so other
peers can reach it:

```bash
python start_chatapp.py --role peer --server-port 9101 --tracker 192.168.1.10:9000 --advertise-ip auto
```

### Proxy and plain backend (HTTP server part)

```bash
python start_backend.py --server-ip 127.0.0.1 --server-port 9000      # static HTTP server (default mode coroutine)
python start_proxy.py   --server-ip 0.0.0.0   --server-port 8080      # reads config/proxy.conf
curl -H "Host: localhost:8080" http://127.0.0.1:8080/index.html       # -> forwarded to 127.0.0.1:9000
```

`config/proxy.conf` shows virtual hosts, several `proxy_pass` lines with `dist_policy round-robin`
(also `least-conn`, `random`, `ip-hash`) and `proxy_set_header Host $host;`. A dead backend gives `502`,
an unknown host `404`.

### Transports (`--mode`)

| mode | what | needs |
|---|---|---|
| `coroutine` (default) | asyncio streams, one event loop, non-blocking | nothing |
| `threading` | blocking sockets, one daemon thread per client | nothing |
| `raw` | the course's raw TCP implementation (`streaprous_socket`) | `sudo`, `pip install -r requirements-raw.txt`, a non-loopback `--server-ip` |

```bash
sudo python start_backend.py --mode raw --server-ip <LAN-IP> --server-port 9000 --interface <eth0|en0>
```

## 3. Tests

```bash
python -m unittest -v tests.test_e2e tests.test_secure           # 28 tests, ~2 s
STREAPROUS_TEST_MODE=threading python -m unittest tests.test_e2e  # same suite on the threading transport
```

`tests/test_e2e.py` starts a tracker, three peers, two backends and the proxy as real processes and checks
login/cookies/Basic auth, brute-force lockout, channel access control, discovery, broadcast and unicast over the
P2P links, 20 concurrent senders, forged peers, XSS payloads, chunked file transfer (byte-exact), a peer that dies
mid-session, proxy round-robin, header rewriting, and error codes. `tests/test_secure.py` covers the cryptography.

## 4. Layout

```
daemon/        request.py response.py httpadapter.py backend.py proxy.py   HTTP server core
               auth.py (PBKDF2 users, sessions, Basic/cookie)  secure.py (X25519 + AES-GCM)
               httpclient.py (blocking + asyncio client for P2P calls)  utils.py (full-request reader)
apps/          tracker.py  peer.py  sampleapp.py
streaprous_socket/  raw TCP transport from the course (unchanged)
www/ static/  login.html chat.html  css/chat.css  js/chat.js login.js
config/        proxy.conf        db/ users.json (hashes)        tests/        report/
```

## 5. API summary (details and message formats: `report/REPORT.pdf`)

Tracker: `POST /login` `POST /logout` `POST /submit-info` `POST /add-list` `GET /get-list` `GET /channels`
`POST /verify` `GET /admin` (HTTP Basic, admin only) `GET /health`.
Peer: `POST /login` `POST /connect-peer` `POST /broadcast-peer` `POST /send-peer` `GET /get-list`
`POST /channels` `POST /send-file` `GET /download` `GET /api/state` | peer-to-peer: `POST /peer-hello`
`POST /peer-message` `POST /peer-file`.

## 6. Grading checklist (10 steps)

| # | Step | Where |
|---|---|---|
| 1 | Protocol design before coding | report sections 3-4 |
| 2 | Client-server handshake + acknowledgement | TCP accept / `GET /health`; raw handshake in `--mode raw` |
| 3 | Authentication & state | `apps/tracker.py` login, sessions, tracking list (ip, port, last seen) |
| 4 | Concurrency | asyncio event loop or thread per client; 20 parallel senders in the tests |
| 5 | Message routing | `/broadcast-peer` (channels) and `/send-peer` (unicast), direct P2P |
| 6 | Reliable file transfer | `/send-file` -> `/peer-file`: 48 KiB segments, per-segment ack + retransmission, SHA-256 check |
| 7 | Resilience | timeouts, dead peers dropped, tracker outage tolerated, never crashes the server |
| 8 | Security | AES-256-GCM payloads, X25519 key agreement, PBKDF2 passwords, HttpOnly cookies |
| 9 | HTTP UI with client-side JS only | `www/chat.html` + `static/js/chat.js` (progress bar, user list) |
| 10 | Tests & code quality | `tests/`, docstrings, PEP 8 style |

## 7. Known limitations

* Messages live in memory (no history after a restart, no store-and-forward for offline peers).
* File transfer uses the stream (TCP-based) HTTP transport of the framework, not UDP datagrams.
* Encryption is application-level (AES-GCM); there is no TLS and the key agreement is not authenticated
  beyond the tracker's identity check, so an active man-in-the-middle on the LAN is out of scope.
* In `--mode raw` only simple request/response pairs were verified (static pages); run the chat in `coroutine` or `threading`.

## License

Course code is under the course LICENSE (study use at HCMUT only).
