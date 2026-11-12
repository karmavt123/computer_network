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
daemon.response
~~~~~~~~~~~~~~~~~

This module provides a :class: `Response <Response>` object to manage and persist
response settings (cookies, auth, proxies), and to construct HTTP responses
based on incoming requests.

It serves static objects (HTML/CSS/JS/images), converts the return value of a
webapp handler (dict / str / bytes / tuple) into an HTTP message and offers
helpers for JSON, redirects, cookies and ``401 Unauthorized``.
"""
import datetime
import json
import mimetypes
import os
from .dictionary import CaseInsensitiveDict

# Get the base directory of the project
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/"

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("application/json", ".json")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("image/x-icon", ".ico")

STATUS_REASONS = {
    100: "Continue", 200: "OK", 201: "Created", 204: "No Content",
    301: "Moved Permanently", 302: "Found", 304: "Not Modified",
    400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found",
    405: "Method Not Allowed", 408: "Request Timeout", 409: "Conflict",
    413: "Payload Too Large", 429: "Too Many Requests",
    500: "Internal Server Error", 502: "Bad Gateway", 503: "Service Unavailable",
    504: "Gateway Timeout",
}


class Response():
    """The :class:`Response <Response>` object, which contains a
    server's response to an HTTP request.

    Instances are generated from a :class:`Request <Request>` object, and
    should not be instantiated manually; doing so may produce undesirable
    effects.

    :class:`Response <Response>` object encapsulates headers, content,
    status code, cookies, and metadata related to the request-response cycle.
    It is used to construct and serve HTTP responses in a custom web server.

    :attrs status_code (int): HTTP status code (e.g., 404, 200).
    :attrs headers (dict): dictionary of response headers.
    :attrs url (str): url of the response.
    :attrsencoding (str): encoding used for decoding response content.
    :attrs history (list): list of previous Response objects (for redirects).
    :attrs reason (str): textual reason for the status code (e.g., "OK", "Not Found").
    :attrs cookies (CaseInsensitiveDict): response cookies.
    :attrs elapsed (datetime.timedelta): time taken to complete the request.
    :attrs request (PreparedRequest): the original request object.

    Usage::

      >>> import Response
      >>> resp = Response()
      >>> resp.build_response(req)
      >>> resp
      <Response>
    """

    __attrs__ = [
        "_content",
        "_header",
        "status_code",
        "method",
        "headers",
        "url",
        "history",
        "encoding",
        "reason",
        "cookies",
        "elapsed",
        "request",
        "body",
        "reason",
    ]

    def __init__(self, request=None):
        """
        Initializes a new :class:`Response <Response>` object.

        : params request : The originating request object.
        """

        self._content = False
        self._content_consumed = False
        self._next = None

        #: Integer Code of responded HTTP Status, e.g. 404 or 200.
        self.status_code = 200

        #: Case-insensitive Dictionary of Response Headers.
        self.headers = {}

        #: Extra headers as a list of (name, value); allows repeated Set-Cookie.
        self.extra_headers = []

        #: URL location of Response.
        self.url = None

        #: Encoding to decode with when accessing response text.
        self.encoding = None

        #: A list of :class:`Response <Response>` objects from
        #: the history of the Request.
        self.history = []

        #: Textual reason of responded HTTP Status, e.g. "Not Found" or "OK".
        self.reason = None

        #: A of Cookies the response headers.
        self.cookies = CaseInsensitiveDict()

        #: The amount of time elapsed between sending the request
        self.elapsed = datetime.timedelta(0)

        #: The :class:`PreparedRequest <PreparedRequest>` object to which this
        #: is a response.
        self.request = request

    # ------------------------------------------------------------ static objects
    def get_mime_type(self, path):
        """
        Determines the MIME type of a file based on its path.

        "params path (str): Path to the file.

        :rtype str: MIME type string (e.g., 'text/html', 'image/png').
        """

        try:
            mime_type, _ = mimetypes.guess_type(path)
        except Exception:
            return 'application/octet-stream'
        return mime_type or 'application/octet-stream'

    def prepare_content_type(self, mime_type='text/html'):
        """
        Prepares the Content-Type header and determines the base directory
        for serving the file based on its MIME type.

        :params mime_type (str): MIME type of the requested resource.

        :rtype str: Base directory path for locating the resource.

        :raises ValueError: If the MIME type is unsupported.
        """

        base_dir = ""

        # Validate header attr existence
        if not hasattr(self, "headers") or self.headers is None:
            self.headers = {}

        # Processing mime_type based on main_type and sub_type
        main_type, sub_type = mime_type.split('/', 1)
        print("[Response] Processing main_type={} sub_type={}".format(main_type, sub_type))
        self.headers['Content-Type'] = mime_type
        if mime_type == 'text/html':
            base_dir = BASE_DIR + "www/"
        elif main_type in ('text', 'image', 'font', 'video', 'audio'):
            # text/css, text/javascript, text/plain, text/csv, images, fonts, media
            base_dir = BASE_DIR + "static/"
        elif main_type == 'application':
            # application/json, application/javascript, application/xml, application/zip, ...
            base_dir = BASE_DIR + "static/"
        else:
            raise ValueError("Invalid MIME type: main_type={} sub_type={}".format(main_type, sub_type))

        return base_dir

    def build_content(self, path, base_dir):
        """
        Loads the objects file from storage space.

        :params path (str): relative path to the file.
        :params base_dir (str): base directory where the file is located.

        :rtype tuple: (int, bytes) representing content length and content data.
        """
        # Stop path traversal such as /../../etc/passwd
        root = os.path.realpath(base_dir)
        filepath = os.path.realpath(os.path.join(root, path.lstrip('/')))
        if filepath != root and not filepath.startswith(root + os.sep):
            print("[Response] blocked path traversal: {}".format(path))
            return -1, b""

        print("[Response] Serving the object at location {}".format(filepath))
        try:
            with open(filepath, "rb") as f:
                content = f.read()
        except Exception as e:
            print("[Response] build_content exception: {}".format(e))
            return -1, b""
        return len(content), content

    # ------------------------------------------------------------ raw assembling
    def build_response_header(self, request=None):
        """
        Constructs the HTTP response headers from the current status, headers
        and content.

        :params request (class:`Request <Request>`): incoming request object.

        :rtypes bytes: encoded HTTP response header.
        """
        if not hasattr(self, '_content') or not isinstance(self._content, (bytes, bytearray)):
            self._content = b""

        headers = [
            ("Content-Type", self.headers.get('Content-Type', 'text/html; charset=utf-8')),
            ("Content-Length", str(len(self._content))),
            ("Cache-Control", "no-cache"),
            ("Date", datetime.datetime.now(datetime.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")),
            ("Connection", "close"),
            ("Server", "Streaprous/1.0"),
            ("X-Content-Type-Options", "nosniff"),
        ]
        for key, value in self.headers.items():
            if key.lower() not in ("content-type", "content-length"):
                headers.append((key, value))
        headers.extend(self.extra_headers)

        reason = self.reason or STATUS_REASONS.get(self.status_code, "OK")
        lines = ["HTTP/1.1 {} {}".format(self.status_code, reason)]
        lines.extend("{}: {}".format(k, v) for k, v in headers)
        return ("\r\n".join(lines) + "\r\n\r\n").encode('utf-8')

    def assemble(self, status, body=b"", content_type="text/html; charset=utf-8", headers=None):
        """
        Build a complete HTTP message.

        :params status (int): HTTP status code.
        :params body (bytes|str): message body.
        :params content_type (str): value of Content-Type.
        :params headers (dict|list): extra headers; ``Set-Cookie`` may be a list.
        :rtype bytes: header + body.
        """
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.status_code = status
        self.reason = STATUS_REASONS.get(status, "OK")
        self._content = body or b""
        self.extra_headers = []
        items = headers.items() if isinstance(headers, dict) else (headers or [])
        for key, value in items:
            if key.lower() == "content-type":      # a handler may choose its own type
                content_type = value
                continue
            values = value if isinstance(value, (list, tuple)) else [value]
            for item in values:
                self.extra_headers.append((key, item))
        self.headers = {'Content-Type': content_type}
        if status in (204, 304):
            self._content = b""
        return self.build_response_header() + self._content

    def build_notfound(self):
        """
        Constructs a standard 404 Not Found HTTP response.

        :rtype bytes: Encoded 404 response.
        """
        return self.assemble(404, "404 Not Found", "text/html; charset=utf-8")

    def build_error(self, status, message=None):
        """JSON error body ``{"ok": false, "error": ...}`` with ``status``."""
        return self.build_json({"ok": False, "error": message or STATUS_REASONS.get(status, "Error")}, status)

    def build_json(self, obj, status=200, headers=None):
        """Serialise ``obj`` as JSON."""
        return self.assemble(status, json.dumps(obj, ensure_ascii=False),
                             "application/json; charset=utf-8", headers)

    def build_redirect(self, location, status=302, headers=None):
        """HTTP redirect (RFC 7231 section 6.4)."""
        merged = {"Location": location}
        merged.update(headers or {})
        return self.assemble(status, "", "text/html; charset=utf-8", merged)

    def build_unauthorized(self, realm="StreapRous", scheme="Basic"):
        """``401`` asking the client for credentials (RFC 7235 section 3.1)."""
        return self.assemble(
            401, "401 Unauthorized", "text/html; charset=utf-8",
            {"WWW-Authenticate": '{} realm="{}", charset="UTF-8"'.format(scheme, realm)})

    def build_hook_response(self, result):
        """
        Convert what a webapp handler returned into an HTTP message.

        Accepted return values:

        * ``bytes`` / ``str`` - 200, JSON content type when the text looks like JSON;
        * ``dict`` / ``list`` - serialised to JSON, 200;
        * ``(status, payload)`` or ``(status, payload, headers)`` tuple;
        * ``None`` - ``204 No Content``.
        """
        status, headers = 200, None
        if isinstance(result, tuple):
            status = result[0]
            headers = result[2] if len(result) > 2 else None
            result = result[1] if len(result) > 1 else None

        if result is None:
            return self.assemble(204 if status == 200 else status, b"", headers=headers)
        if isinstance(result, (dict, list)):
            return self.build_json(result, status, headers)
        if isinstance(result, (bytes, bytearray)):
            text = bytes(result)
        else:
            text = str(result).encode('utf-8')
        looks_json = text.lstrip()[:1] in (b'{', b'[')
        ctype = "application/json; charset=utf-8" if looks_json else "text/html; charset=utf-8"
        return self.assemble(status, text, ctype, headers)

    # --------------------------------------------------------------- entry point
    def build_response(self, request, envelop_content=None):
        """
        Builds a full HTTP response (static object) based on the request.

        :params request (class:`Request <Request>`): incoming request object.

        :rtype bytes: complete HTTP response using prepared headers and content.
        """
        if request.method not in ("GET", "HEAD"):
            return self.assemble(405, "405 Method Not Allowed", headers={"Allow": "GET, HEAD"})

        path = request.path
        if path == '/':
            path = '/index.html'
        elif path == '/favicon.ico':
            path = '/images/favicon.ico'
        if path.startswith('/static/'):
            path = path[len('/static'):]

        mime_type = self.get_mime_type(path)
        print("[Response] {} path {} mime_type {}".format(request.method, path, mime_type))

        try:
            base_dir = self.prepare_content_type(mime_type)
        except ValueError:
            return self.build_notfound()

        content_length, content = self.build_content(path, base_dir)
        if content_length < 0:
            return self.build_notfound()

        content_type = mime_type
        if mime_type.startswith("text/") or mime_type in ("application/json", "application/javascript"):
            content_type += "; charset=utf-8"
        if request.method == "HEAD":
            self._content = b""
            self.status_code, self.reason = 200, "OK"
            self.headers = {'Content-Type': content_type}
            head = self.build_response_header(request)
            return head.replace(b"Content-Length: 0", "Content-Length: {}".format(content_length).encode())
        return self.assemble(200, content, content_type)
