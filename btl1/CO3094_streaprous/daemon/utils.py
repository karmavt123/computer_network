#
# Copyright (C) 2026 pdnguyen of HCMC University of Technology VNU-HCM.
# All rights reserved.
# This file is part of the CO3093/CO3094 course.
#
# StreapRous release
#

"""
daemon.utils
~~~~~~~~~~~~~~~~~

Small helpers shared by the HTTP daemons: URL helpers and the routines that read
one complete HTTP request (headers + Content-Length body) from a blocking socket
or from an asyncio stream.
"""

import asyncio
import re
from urllib.parse import urlparse, unquote

#: Upper bound of the header section (bytes).
MAX_HEADER_BYTES = 64 * 1024
#: Upper bound of a request body (bytes).
MAX_BODY_BYTES = 5 * 1024 * 1024

_CONTENT_LENGTH = re.compile(rb"^content-length:\s*(\d+)\s*$", re.I | re.M)
_EXPECT_CONTINUE = re.compile(rb"^expect:\s*100-continue\s*$", re.I | re.M)


class RequestTooLarge(Exception):
    """Raised when a request exceeds MAX_HEADER_BYTES / MAX_BODY_BYTES."""


def get_auth_from_url(url):
    """Given a url with authentication components, extract them into a tuple of
    username,password.

    :rtype: (str,str)
    """
    parsed = urlparse(url)

    try:
        auth = (unquote(parsed.username), unquote(parsed.password))
    except (AttributeError, TypeError):
        auth = ("", "")

    return auth


def content_length_of(head):
    """Return the Content-Length declared in a raw header block (0 if absent)."""
    match = _CONTENT_LENGTH.search(head)
    return int(match.group(1)) if match else 0


def read_http_request(conn, timeout=30.0):
    """
    Read one complete HTTP request from a blocking socket.

    The first ``recv`` is not enough for POST requests: the body may arrive
    in later TCP segments, so we keep reading until ``Content-Length`` bytes of
    body have been collected.

    :param conn (socket.socket): connected socket.
    :param timeout (float): per-recv timeout in seconds.
    :rtype bytes: the raw request (``b""`` if the peer closed without sending).
    :raises RequestTooLarge: header/body exceeds the configured limits.
    """
    conn.settimeout(timeout)
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = conn.recv(4096)
        if not chunk:
            return data
        data += chunk
        if len(data) > MAX_HEADER_BYTES and b"\r\n\r\n" not in data:
            raise RequestTooLarge("header too large")

    head, _, body = data.partition(b"\r\n\r\n")
    length = content_length_of(head)
    if length > MAX_BODY_BYTES:
        raise RequestTooLarge("body too large")
    if length > len(body) and _EXPECT_CONTINUE.search(head):
        conn.sendall(b"HTTP/1.1 100 Continue\r\n\r\n")
    while len(body) < length:
        chunk = conn.recv(min(65536, length - len(body)))
        if not chunk:
            break
        body += chunk
    return head + b"\r\n\r\n" + body


async def read_http_request_async(reader, writer=None, timeout=30.0):
    """
    Asynchronous counterpart of :func:`read_http_request` using asyncio streams.

    :param reader (asyncio.StreamReader): stream to read from.
    :param writer (asyncio.StreamWriter): used to answer ``Expect: 100-continue``.
    :rtype bytes: the raw request (``b""`` if the peer closed without sending).
    """
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = await asyncio.wait_for(reader.read(4096), timeout)
        if not chunk:
            return data
        data += chunk
        if len(data) > MAX_HEADER_BYTES and b"\r\n\r\n" not in data:
            raise RequestTooLarge("header too large")

    head, _, body = data.partition(b"\r\n\r\n")
    length = content_length_of(head)
    if length > MAX_BODY_BYTES:
        raise RequestTooLarge("body too large")
    if length > len(body) and writer is not None and _EXPECT_CONTINUE.search(head):
        writer.write(b"HTTP/1.1 100 Continue\r\n\r\n")
        await writer.drain()
    while len(body) < length:
        chunk = await asyncio.wait_for(reader.read(min(65536, length - len(body))), timeout)
        if not chunk:
            break
        body += chunk
    return head + b"\r\n\r\n" + body
