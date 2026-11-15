#
# Copyright (C) 2026 pdnguyen of HCMC University of Technology VNU-HCM.
# All rights reserved.
# This file is part of the CO3093/CO3094 course.
#
# StreapRous release
#
# The authors hereby grant to Licensee personal permission to use
# and modify the Licensed Source Code for the sole purpose of studying
# while attending the course
#

"""
daemon.request
~~~~~~~~~~~~~~~~~

This module provides a Request object to manage and persist
request settings (cookies, auth, proxies).

The parser works on the header block and the body separately, so a binary
body (a file upload) is never decoded as text. Besides the real headers,
route handlers receive three *pseudo-headers* that cannot be sent by a
client (header names starting with ``:`` are dropped while parsing):

* ``:path``   - request path without the query string,
* ``:query``  - dict of query parameters (first value of each),
* ``:client`` - IP address of the TCP peer that sent the request.
"""
import base64
from urllib.parse import parse_qs, unquote

from .dictionary import CaseInsensitiveDict

BINARY_CONTENT_TYPES = ("application/octet-stream",)


class Request():
    """The fully mutable "class" `Request <Request>` object,
    containing the exact bytes that will be sent to the server.

    Instances are generated from a "class" `Request <Request>` object, and
    should not be instantiated manually; doing so may produce undesirable
    effects.

    Usage::

      >>> import deamon.request
      >>> req = request.Request()
      ## Incoming message obtain aka. incoming_msg
      >>> r = req.prepare(incoming_head, routes, incoming_body)
      >>> r
      <Request [GET /]>
    """
    __attrs__ = [
        "method",
        "url",
        "headers",
        "body",
        "_raw_headers",
        "_raw_body",
        "reason",
        "cookies",
        "body",
        "routes",
        "hook",
    ]

    def __init__(self):
        #: HTTP verb to send to the server.
        self.method = None
        #: HTTP URL (request target) as sent by the client.
        self.url = None
        #: dictionary of HTTP headers (names in lower case).
        self.headers = None
        #: HTTP path without the query string.
        self.path = None
        #: HTTP version of the request line.
        self.version = None
        #: Query parameters.
        self.query = {}
        # The cookies set used to create Cookie header
        self.cookies = None
        #: request body (str, or bytes for binary content types).
        self.body = None
        # The raw header
        self._raw_headers = None
        #: The raw body
        self._raw_body = None
        #: Routes
        self.routes = {}
        #: Hook point for routed mapped-path
        self.hook = None

    def __repr__(self):
        return "<Request [{} {}]>".format(self.method, self.path)

    def extract_request_line(self, request):
        """Return ``(method, target, version)`` of the first line."""
        try:
            first_line = request.splitlines()[0]
            method, target, version = first_line.split()
        except (IndexError, ValueError):
            return None, None, None
        return method.upper(), target, version

    def prepare_headers(self, request):
        """Parse the header lines of ``request`` into a lower-case dict."""
        headers = {}
        for line in request.split('\r\n')[1:]:
            key, sep, value = line.partition(':')
            key = key.strip().lower()
            if not sep or not key or key.startswith(':'):
                continue
            headers[key] = value.strip()
        return headers

    def fetch_headers_body(self, request):
        """Split a complete message into header block and body."""
        parts = request.split("\r\n\r\n", 1)
        return parts[0], (parts[1] if len(parts) > 1 else "")

    def prepare(self, request, routes=None, body=None, client=None):
        """Prepare the request.

        :param request: header block (str), or a complete message when
            ``body`` is None (legacy form used by the course skeleton).
        :param routes: route table ``{(METHOD, path): handler}``.
        :param body: raw body bytes, if already separated.
        :param client: IP address of the sender.
        """
        if body is None:
            head, text_body = self.fetch_headers_body(request)
            body = text_body.encode("utf-8")
        else:
            head = request
        self.method, self.url, self.version = self.extract_request_line(head)
        self._raw_headers, self._raw_body = head, body
        self.headers = self.prepare_headers(head)
        path, _, query = (self.url or "").partition("?")
        self.path = unquote(path) if path else path
        self.query = {key: values[0] for key, values in
                      parse_qs(query, keep_blank_values=True).items()}
        content_type = self.headers.get("content-type", "").split(";", 1)[0]
        if content_type.strip().lower() in BINARY_CONTENT_TYPES:
            self.body = body
        else:
            self.body = body.decode("utf-8", errors="replace")
        self.headers[":path"] = self.path
        self.headers[":query"] = self.query
        self.headers[":client"] = client
        self.routes = routes or {}
        self.hook = self.routes.get((self.method, self.path))
        self.prepare_cookies(self.headers.get('cookie', ''))
        return self

    def prepare_body(self, data, files=None, json=None):
        """Attach ``data`` as the body and set Content-Length."""
        self.body = data if data is not None else b""
        self.prepare_content_length(self.body)
        return self

    def prepare_content_length(self, body):
        """Set the Content-Length header from ``body``."""
        if self.headers is None:
            self.headers = {}
        raw = body.encode("utf-8") if isinstance(body, str) else (body or b"")
        self.headers["content-length"] = str(len(raw))
        return self

    def prepare_auth(self, auth, url=""):
        """Set HTTP Basic authentication (RFC 7617) from ``(user, password)``."""
        if auth:
            if self.headers is None:
                self.headers = {}
            token = base64.b64encode("{}:{}".format(*auth).encode("utf-8"))
            self.headers["authorization"] = "Basic " + token.decode("ascii")
        return self

    def prepare_cookies(self, cookies):
        """Parse a Cookie header value into :attr:`cookies`."""
        self.cookies = CaseInsensitiveDict()
        for item in (cookies or "").split(';'):
            key, sep, value = item.strip().partition('=')
            if sep:
                self.cookies[key] = value
        return self
