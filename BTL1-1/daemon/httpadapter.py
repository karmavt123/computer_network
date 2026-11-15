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
daemon.httpadapter
~~~~~~~~~~~~~~~~~

This module provides a http adapter object to manage and persist
http settings (headers, bodies). The adapter supports both
raw URL paths and RESTful route definitions, and integrates with
Request and Response objects to handle client-server communication.

One request is served per connection (``Connection: close``). The same
dispatch logic is used by the three transports of the backend:

* ``handle_client``           - blocking socket, one thread per client;
* ``handle_client_coroutine`` - asyncio streams (non-blocking); a blocking
  route handler runs in the default thread-pool executor so that it can
  never stall the event loop;
* ``handle_client_raw``       - the course's raw TCP implementation.
"""

import asyncio
import inspect
import json

from .request import Request
from .response import Response

# Import streaprous raw connection functions
try:
    from streaprous_socket.streaprous_raw_connection import (
        run_conn_recv, run_conn_send, close_connection
    )
except ImportError:
    run_conn_recv = run_conn_send = close_connection = None

#: Largest accepted header block.
MAX_HEADER_BYTES = 64 * 1024
#: Largest accepted body (a 100 MiB file plus some slack).
MAX_BODY_BYTES = 101 * 1024 * 1024
#: Seconds a client may stay silent while sending its request.
CLIENT_TIMEOUT = 30

REASONS = {200: "OK", 201: "Created", 204: "No Content", 400: "Bad Request",
           401: "Unauthorized", 403: "Forbidden", 404: "Not Found",
           405: "Method Not Allowed", 409: "Conflict", 413: "Payload Too Large",
           500: "Internal Server Error", 502: "Bad Gateway",
           503: "Service Unavailable", 504: "Gateway Timeout"}


class BadRequest(Exception):
    """The client sent a request the adapter cannot parse."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def content_length_of(head):
    """Return the Content-Length announced in a header block (0 if absent)."""
    for line in head.split("\r\n")[1:]:
        name, sep, value = line.partition(":")
        if sep and name.strip().lower() == "content-length":
            try:
                length = int(value.strip())
            except ValueError as exc:
                raise BadRequest(400, "Invalid Content-Length") from exc
            if length < 0:
                raise BadRequest(400, "Invalid Content-Length")
            if length > MAX_BODY_BYTES:
                raise BadRequest(413, "Request body too large")
            return length
    return 0


class HttpAdapter:
    """
    A mutable :class:`HTTP adapter <HTTP adapter>` for managing client connections
    and routing requests.

    The `HttpAdapter` class encapsulates the logic for receiving HTTP requests,
    dispatching them to appropriate route handlers, and constructing responses.
    It supports RESTful routing via hooks and integrates with :class:`Request <Request>`
    and :class:`Response <Response>` objects for full request lifecycle management.

    Attributes:
        ip (str): IP address of the client.
        port (int): Port number of the client.
        conn (socket): Active socket connection.
        connaddr (tuple): Address of the connected client.
        routes (dict): Mapping of route paths to handler functions.
        request (Request): Request object for parsing incoming data.
        response (Response): Response object for building and sending replies.
        conn_info (conn_struct): streaprous connection info for raw socket.
        raw_socket (socket): Raw socket for streaprous communication.
    """

    __attrs__ = [
        "ip",
        "port",
        "conn",
        "connaddr",
        "routes",
        "request",
        "response",
        "conn_info",
        "raw_socket",
    ]

    def __init__(self, ip, port, conn, connaddr, routes):
        """
        Initialize a new HttpAdapter instance.

        :param ip (str): IP address of the client.
        :param port (int): Port number of the client.
        :param conn (socket): Active socket connection.
        :param connaddr (tuple): Address of the connected client.
        :param routes (dict): Mapping of route paths to handler functions.
        """
        #: IP address.
        self.ip = ip
        #: Port.
        self.port = port
        #: Connection
        self.conn = conn
        #: Conndection address
        self.connaddr = connaddr
        #: Routes
        self.routes = routes or {}
        #: Request
        self.request = Request()
        #: Response
        self.response = Response()
        #: streaprous connection info
        self.conn_info = None
        #: raw socket for streaprous communication
        self.raw_socket = None

    # ------------------------------------------------------------------
    # transport 1: blocking socket (one thread per client)
    # ------------------------------------------------------------------
    def handle_client(self, conn, addr, routes):
        """
        Handle an incoming client connection.

        This method reads the request from the socket, prepares the request object,
        invokes the appropriate route handler if available, builds the response,
        and sends it back to the client.

        :param conn (socket): The client socket connection.
        :param addr (tuple): The client's address.
        :param routes (dict): The route mapping for dispatching requests.
        """
        self.conn, self.connaddr, self.routes = conn, addr, routes or {}
        try:
            conn.settimeout(CLIENT_TIMEOUT)
            try:
                head, body = self._read_request(conn)
                response = self.dispatch(head, body, addr[0] if addr else None)
            except BadRequest as exc:
                response = self.error_response(exc.status, str(exc))
            conn.sendall(response)
        except OSError as exc:            # timeout, reset, broken pipe
            print("[HttpAdapter] Connection error with {}: {}".format(addr, exc))
        finally:
            conn.close()

    @staticmethod
    def _read_request(conn):
        """Read one request (header block + body) from a blocking socket."""
        data = bytearray()
        while b"\r\n\r\n" not in data:
            if len(data) > MAX_HEADER_BYTES:
                raise BadRequest(400, "Header block too large")
            chunk = conn.recv(4096)
            if not chunk:
                raise BadRequest(400, "Connection closed before the end of the headers")
            data.extend(chunk)
        head_bytes, _, body = bytes(data).partition(b"\r\n\r\n")
        head = head_bytes.decode("iso-8859-1")
        length = content_length_of(head)
        body = bytearray(body)
        while len(body) < length:
            chunk = conn.recv(min(1 << 16, length - len(body)))
            if not chunk:
                raise BadRequest(400, "Connection closed before the end of the body")
            body.extend(chunk)
        return head, bytes(body[:length])

    # ------------------------------------------------------------------
    # transport 2: asyncio streams (non-blocking)
    # ------------------------------------------------------------------
    async def handle_client_coroutine(self, reader, writer):
        """
        Handle an incoming client connection using stream reader writer asynchronously.

        This method reads the request from the socket, prepares the request object,
        invokes the appropriate route handler if available, builds the response,
        and sends it back to the client.

        :param reader (StreamReader): stream of the client connection.
        :param writer (StreamWriter): writer of the client connection.
        """
        addr = writer.get_extra_info("peername")
        self.connaddr = addr
        try:
            try:
                head_bytes = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"),
                                                    CLIENT_TIMEOUT)
                head = head_bytes[:-4].decode("iso-8859-1")
                length = content_length_of(head)
                body = await asyncio.wait_for(reader.readexactly(length), CLIENT_TIMEOUT)
                response = await self.dispatch_async(head, body, addr[0] if addr else None)
            except asyncio.LimitOverrunError:
                response = self.error_response(400, "Header block too large")
            except asyncio.IncompleteReadError:
                return                                  # client went away
            except BadRequest as exc:
                response = self.error_response(exc.status, str(exc))
            writer.write(response)
            await writer.drain()
        except (asyncio.TimeoutError, OSError) as exc:
            print("[HttpAdapter] Connection error with {}: {}".format(addr, exc))
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    # ------------------------------------------------------------------
    # transport 3: the course's raw TCP implementation (needs root)
    # ------------------------------------------------------------------
    def handle_client_raw(self, raw_socket, conn_info, routes):
        """
        Handle an incoming client connection using streaprous raw connection.

        This method reads the request from the raw socket, prepares the request object,
        invokes the appropriate route handler if available, builds the response,
        and sends it back to the client using raw sockets.

        :param raw_socket (socket): The raw socket for communication.
        :param conn_info (conn_struct): The connection info for the client.
        :param routes (dict): The route mapping for dispatching requests.
        """
        self.routes = routes or {}
        data = run_conn_recv(raw_socket, conn_info)
        if not data:
            print("[HttpAdapter] No data received or connection closed")
            return
        head_bytes, _, body = data.partition(b"\r\n\r\n")
        try:
            response = self.dispatch(head_bytes.decode("iso-8859-1"), body,
                                     conn_info.client_ip)
        except BadRequest as exc:
            response = self.error_response(exc.status, str(exc))
        run_conn_send(raw_socket, conn_info, response)
        close_connection(raw_socket, conn_info)

    # ------------------------------------------------------------------
    # dispatch
    # ------------------------------------------------------------------
    def _prepare(self, head, body, client):
        req = self.request
        req.prepare(head, self.routes, body=body, client=client)
        if req.method is None:
            raise BadRequest(400, "Malformed request line")
        return req

    def dispatch(self, head, body, client=None):
        """Run the route handler (or serve a static file); return response bytes."""
        req = self._prepare(head, body, client)
        if not req.hook:
            return self.response.build_response(req)
        try:
            result = req.hook(req.headers, req.body)
            if inspect.isawaitable(result):
                result = asyncio.run(result)
        except Exception as exc:            # a bug in a handler must not kill the server
            print("[HttpAdapter] Handler error on {} {}: {!r}".format(req.method, req.path, exc))
            return self.error_response(500, "Internal server error")
        return self.route_response(result)

    async def dispatch_async(self, head, body, client=None):
        """Coroutine version of :meth:`dispatch` for the asyncio transport."""
        req = self._prepare(head, body, client)
        loop = asyncio.get_running_loop()
        try:
            if not req.hook:
                return await loop.run_in_executor(None, self.response.build_response, req)
            if inspect.iscoroutinefunction(req.hook):
                result = await req.hook(req.headers, req.body)
            else:
                result = await loop.run_in_executor(None, req.hook, req.headers, req.body)
                if inspect.isawaitable(result):
                    result = await result
        except Exception as exc:
            print("[HttpAdapter] Handler error on {} {}: {!r}".format(req.method, req.path, exc))
            return self.error_response(500, "Internal server error")
        return self.route_response(result)

    # ------------------------------------------------------------------
    # response helpers
    # ------------------------------------------------------------------
    @staticmethod
    def http_response(status, body, content_type="application/json", extra_headers=None):
        """Serialise a complete HTTP/1.1 response."""
        headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
            "Connection": "close",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Server": "StreapRous/1.1",
        }
        headers.update(extra_headers or {})
        head = "HTTP/1.1 {} {}\r\n".format(status, REASONS.get(status, "Unknown"))
        head += "".join("{}: {}\r\n".format(k, v) for k, v in headers.items()) + "\r\n"
        return head.encode("latin-1") + body

    @classmethod
    def error_response(cls, status, message):
        """JSON error response ``{"error": message}``."""
        body = json.dumps({"error": message}).encode("utf-8")
        return cls.http_response(status, body, "application/json; charset=utf-8")

    @classmethod
    def route_response(cls, result):
        """Turn a handler's return value into response bytes.

        A handler may return ``(status, content_type, body, headers)``,
        bytes, a str (HTML) or any JSON-serialisable value.
        """
        status, content_type, body, headers = 200, "application/json; charset=utf-8", b"", {}
        if isinstance(result, tuple) and len(result) == 4:
            status, content_type, body, headers = result
        elif isinstance(result, bytes):
            body = result
        elif isinstance(result, str):
            content_type, body = "text/html; charset=utf-8", result.encode("utf-8")
        else:
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
        if isinstance(body, str):
            body = body.encode("utf-8")
        return cls.http_response(status, body, content_type, headers)

    # Kept for compatibility with the earlier names used in this project.
    _http_response = http_response
    _route_response = route_response

    def extract_cookies(self, req):
        """
        Build cookies from the :class:`Request <Request>` headers.

        :param req:(Request) The :class:`Request <Request>` object.
        :rtype: cookies - A dictionary of cookie key-value pairs.
        """
        return dict(req.cookies.store) if req.cookies else {}

    def build_response(self, req, resp):
        """Builds a :class:`Response <Response>` object

        :param req: The :class:`Request <Request>` used to generate the response.
        :param resp: The  response object.
        :rtype: Response
        """
        response = Response(req)
        response.raw = resp
        response.url = req.url
        response.cookies = self.extract_cookies(req)
        response.request = req
        response.connection = self
        return response

    def build_json_response(self, req, resp):
        """Builds a :class:`Response <Response>` object from JSON data

        :param req: The :class:`Request <Request>` used to generate the response.
        :param resp: The  response object.
        :rtype: Response
        """
        response = Response(req)
        response.raw = resp
        response.url = req.url
        response.request = req
        response.connection = self
        return response

    def add_headers(self, request):
        """
        Add headers to the request.

        This method is intended to be overridden by subclasses to inject
        custom headers. It does nothing by default.

        :param request: :class:`Request <Request>` to add headers to.
        """

    def build_proxy_headers(self, proxy):
        """Returns a dictionary of the headers to add to any request sent
        through a proxy.

        The proxy of this project does not require authentication, so no
        ``Proxy-Authorization`` header is produced.

        :param proxy: The url of the proxy being used for this request.
        :rtype: dict
        """
        return {}
