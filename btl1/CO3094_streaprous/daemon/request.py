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
"""
import json
from urllib.parse import urlsplit, unquote, parse_qs

from .dictionary import CaseInsensitiveDict
from .auth import parse_basic_auth, parse_cookie_header


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
      >>> r = req.prepare(incoming_msg)
      >>> r
      <Request>
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
        #: HTTP URL to send the request to.
        self.url = None
        #: HTTP version string of the request line.
        self.version = None
        #: dictionary of HTTP headers.
        self.headers = None
        #: HTTP path (without the query string)
        self.path = None
        #: Parsed query string {name: value}
        self.query = {}
        # The cookies set used to create Cookie header
        self.cookies = {}
        #: (username, password) decoded from ``Authorization: Basic`` or None
        self.auth = None
        #: request body to send to the server.
        self.body = None
        # The raw header
        self._raw_headers = None
        #: The raw body
        self._raw_body = None
        #: Routes
        self.routes = {}
        #: Hook point for routed mapped-path
        self.hook = None
        #: False when the request line could not be parsed (-> 400)
        self.valid = False

    # ------------------------------------------------------------------ parsing
    def extract_request_line(self, request):
        """Split ``METHOD target VERSION``; returns ``(None, None, None)`` if malformed."""
        try:
            first_line = request.splitlines()[0]
            method, target, version = first_line.split()
            if not version.upper().startswith("HTTP/"):
                return None, None, None
        except (IndexError, ValueError):
            return None, None, None

        return method.upper(), target, version

    def prepare_headers(self, request):
        """Prepares the given HTTP headers (case-insensitive mapping)."""
        lines = request.split('\r\n')
        headers = CaseInsensitiveDict()
        for line in lines[1:]:
            if ': ' in line:
                key, val = line.split(': ', 1)
                headers[key.strip()] = val.strip()
            elif ':' in line:
                key, val = line.split(':', 1)
                headers[key.strip()] = val.strip()
        return headers

    def fetch_headers_body(self, request):
        """Split a request into its header section and body section."""
        parts = request.split("\r\n\r\n", 1)  # split once at blank line

        _headers = parts[0]
        _body = parts[1] if len(parts) > 1 else ""
        return _headers, _body

    def lookup_hook(self, routes):
        """Find the webapp handler registered for ``(METHOD, path)``.

        ``/login`` and ``/login/`` are treated as the same route.
        """
        for candidate in (self.path, self.path.rstrip('/') or '/', self.path.rstrip('/') + '/'):
            hook = routes.get((self.method, candidate))
            if hook is not None:
                return hook
        return None

    def prepare(self, request, routes=None):
        """Prepares the entire request with the given parameters."""
        self.valid = False
        self.method, target, self.version = self.extract_request_line(request)
        if self.method is None:
            print("[Request] malformed request line")
            return

        # Split the request target into path and query string
        parts = urlsplit(target)
        self.path = unquote(parts.path) or '/'
        self.query = {k: v[-1] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
        self.url = target

        # Parse headers and body
        self._raw_headers, self._raw_body = self.fetch_headers_body(request)
        self.headers = self.prepare_headers(self._raw_headers)
        try:
            declared = int(self.headers.get('content-length', '0') or 0)
        except ValueError:
            declared = 0
        self.body = self._raw_body[:declared] if declared else self._raw_body
        print("[Request] {} {} {} headers={}".format(
            self.method, self.path, self.version, len(self.headers)))

        # Cookies (RFC 6265) and HTTP Basic credentials (RFC 7617)
        self.cookies = parse_cookie_header(self.headers.get('cookie', ''))
        self.auth = parse_basic_auth(self.headers.get('authorization'))

        #
        # @bksysnet Preparing the webapp hook with StreapRous instance
        # The default behaviour with HTTP server is empty routed
        #
        if routes:
            self.routes = routes
            self.hook = self.lookup_hook(routes)
            print("[Request] Routing METHOD {} path {} -> hook {}".format(
                self.method, self.path, getattr(self.hook, "__name__", None)))

        self.valid = True

    # ---------------------------------------------------------------- body helpers
    def json(self):
        """Decode the body as JSON; returns ``None`` when it is not valid JSON."""
        try:
            return json.loads(self.body) if self.body else None
        except (ValueError, TypeError):
            return None

    def form(self):
        """Decode an ``application/x-www-form-urlencoded`` body to a dict."""
        return {k: v[-1] for k, v in parse_qs(self.body or "", keep_blank_values=True).items()}

    def payload(self):
        """Decode the body according to Content-Type (JSON or form) into a dict."""
        ctype = (self.headers.get('content-type', '') if self.headers else '').lower()
        if 'application/x-www-form-urlencoded' in ctype:
            return self.form()
        data = self.json()
        return data if isinstance(data, dict) else {}

    def get_cookie(self, name):
        return self.cookies.get(name)

    # ------------------------------------------------------- client side helpers
    def prepare_content_length(self, body):
        """Set the Content-Length header for ``body``."""
        if self.headers is None:
            self.headers = CaseInsensitiveDict()
        self.headers["Content-Length"] = str(len(body.encode("utf-8")) if body else 0)

    def prepare_body(self, data, files=None, json=None):
        """Attach ``data`` as the request body and refresh Content-Length."""
        self.body = data
        self.prepare_content_length(self.body)
        return

    def prepare_auth(self, auth, url=""):
        """Store ``(username, password)`` and emit the Basic Authorization header."""
        import base64
        if not auth:
            return
        self.auth = auth
        token = base64.b64encode("{}:{}".format(*auth).encode("utf-8")).decode("ascii")
        if self.headers is None:
            self.headers = CaseInsensitiveDict()
        self.headers["Authorization"] = "Basic " + token

    def prepare_cookies(self, cookies):
        """Emit the ``Cookie`` request header."""
        if self.headers is None:
            self.headers = CaseInsensitiveDict()
        self.headers["Cookie"] = cookies
