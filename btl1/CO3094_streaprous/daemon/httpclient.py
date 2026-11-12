#
# Copyright (C) 2026 pdnguyen of HCMC University of Technology VNU-HCM.
# All rights reserved.
# This file is part of the CO3093/CO3094 course.
#
# StreapRous release
#

"""
daemon.httpclient
~~~~~~~~~~~~~~~~~

Minimal HTTP/1.1 client over plain sockets used for the peer-to-peer part of the chat
application (peer -> tracker and peer -> peer calls). Every call opens a short lived
connection (``Connection: close``) so the response is delimited by EOF.

* :func:`http_request` - blocking version;
* :func:`async_http_request` - non-blocking version for ``async`` handlers (asyncio streams).

Both return :class:`ClientResponse` and raise :class:`PeerError` on network failures, so callers
only need one ``except`` clause.
"""

import asyncio
import json
import socket


class PeerError(Exception):
    """Network failure while talking to a remote daemon (refused, timeout, bad reply)."""


class ClientResponse:
    """Status line, headers and body of an HTTP response."""

    def __init__(self, status, headers, body):
        self.status = status
        self.headers = headers
        self.body = body

    @property
    def ok(self):
        return 200 <= self.status < 300

    def json(self):
        """Decode the body as JSON (``{}`` when empty or invalid)."""
        try:
            return json.loads(self.body.decode("utf-8")) if self.body else {}
        except ValueError:
            return {}


def build_request(method, host, port, path, body=None, headers=None):
    """Serialise an HTTP/1.1 request; ``body`` may be ``dict`` (sent as JSON), str or bytes."""
    hdrs = {"Host": "{}:{}".format(host, port), "Connection": "close", "Accept": "application/json"}
    if isinstance(body, (dict, list)):
        body = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    elif isinstance(body, str):
        body = body.encode("utf-8")
    hdrs.update(headers or {})
    hdrs["Content-Length"] = str(len(body or b""))
    head = "{} {} HTTP/1.1\r\n".format(method.upper(), path)
    head += "".join("{}: {}\r\n".format(k, v) for k, v in hdrs.items()) + "\r\n"
    return head.encode("utf-8") + (body or b"")


def parse_response(raw):
    """Split a raw HTTP response into a :class:`ClientResponse`."""
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.decode("iso-8859-1").split("\r\n")
    try:
        status = int(lines[0].split()[1])
    except (IndexError, ValueError):
        raise PeerError("malformed HTTP response")
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()
    return ClientResponse(status, headers, body)


def http_request(host, port, method, path, body=None, headers=None, timeout=4.0):
    """Blocking HTTP request. Raises :class:`PeerError` on any network problem."""
    payload = build_request(method, host, port, path, body, headers)
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(payload)
            chunks = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
    except (OSError, ValueError) as exc:
        raise PeerError("{}:{} unreachable ({})".format(host, port, exc))
    return parse_response(b"".join(chunks))


async def async_http_request(host, port, method, path, body=None, headers=None, timeout=4.0):
    """Non-blocking HTTP request. Raises :class:`PeerError` on any network problem."""
    payload = build_request(method, host, port, path, body, headers)
    writer = None
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, int(port)), timeout)
        writer.write(payload)
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(-1), timeout)
    except (OSError, ValueError, asyncio.TimeoutError) as exc:
        raise PeerError("{}:{} unreachable ({})".format(host, port, exc or "timeout"))
    finally:
        if writer is not None:
            try:
                writer.close()
                await writer.wait_closed()
            except (OSError, ConnectionError):
                pass
    return parse_response(raw)
