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

The same request pipeline (:meth:`HttpAdapter.dispatch` /
:meth:`HttpAdapter.adispatch`) is shared by the three transports
of the backend: threads on standard sockets, asyncio streams and the
streaprous raw socket.
"""

import asyncio
import base64
import inspect
import socket
import time
import traceback

from .request import Request
from .response import Response
from .dictionary import CaseInsensitiveDict
from .utils import (RequestTooLarge, get_auth_from_url, read_http_request,
                    read_http_request_async)

#: Seconds the raw transport lingers before sending FIN, so the client can ACK.
RAW_LINGER_SECONDS = 1.0


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
        self.routes = routes
        #: Request
        self.request = Request()
        #: Response
        self.response = Response()
        #: streaprous connection info
        self.conn_info = None
        #: raw socket for streaprous communication
        self.raw_socket = None

    # ------------------------------------------------------------------ pipeline
    @staticmethod
    def _hook_args(hook, req):
        """Handlers are ``f(headers, body)``; a third parameter receives the Request."""
        try:
            params = inspect.signature(hook).parameters
        except (TypeError, ValueError):
            return (req.headers, req.body)
        wants_request = len(params) >= 3 or "request" in params or "req" in params
        return (req.headers, req.body, req) if wants_request else (req.headers, req.body)

    def _prepare(self, raw, routes):
        """Parse ``raw`` bytes; returns an error response (bytes) or ``None``."""
        self.request = req = Request()
        self.response = Response(req)
        if not raw:
            return b""
        req.prepare(raw.decode("utf-8", errors="replace"), routes)
        if not req.valid:
            return self.response.build_error(400, "Malformed HTTP request")
        return None

    def _failure(self, exc):
        traceback.print_exc()
        return self.response.build_error(500, "Internal Server Error: {}".format(type(exc).__name__))

    def dispatch(self, raw, routes):
        """
        Run one request through the pipeline (blocking transports).

        :param raw (bytes): complete request.
        :param routes (dict): ``{(METHOD, path): handler}``.
        :rtype bytes: complete HTTP response ("" when there is nothing to answer).
        """
        error = self._prepare(raw, routes)
        if error is not None:
            return error
        req, resp = self.request, self.response
        try:
            if req.hook:
                result = req.hook(*self._hook_args(req.hook, req))
                if inspect.isawaitable(result):
                    result = asyncio.run(result)
                return resp.build_hook_response(result)
            return resp.build_response(req)
        except Exception as exc:
            return self._failure(exc)

    async def adispatch(self, raw, routes):
        """Asynchronous variant of :meth:`dispatch` - ``async`` handlers are awaited."""
        error = self._prepare(raw, routes)
        if error is not None:
            return error
        req, resp = self.request, self.response
        try:
            if req.hook:
                result = req.hook(*self._hook_args(req.hook, req))
                if inspect.isawaitable(result):
                    result = await result
                return resp.build_hook_response(result)
            return resp.build_response(req)
        except Exception as exc:
            return self._failure(exc)

    # ---------------------------------------------------------------- transports
    def handle_client(self, conn, addr, routes):
        """
        Handle an incoming client connection (standard socket, one thread each).

        This method reads the request from the socket, prepares the request object,
        invokes the appropriate route handler if available, builds the response,
        and sends it back to the client.

        :param conn (socket): The client socket connection.
        :param addr (tuple): The client's address.
        :param routes (dict): The route mapping for dispatching requests.
        """
        self.conn = conn
        self.connaddr = addr
        print("[HttpAdapter] Invoke handle_client connection {}".format(addr))

        try:
            try:
                raw = read_http_request(conn)
                response = self.dispatch(raw, routes)
            except RequestTooLarge:
                response = Response().build_error(413, "Request too large")
            except socket.timeout:
                response = Response().build_error(408, "Request timeout")
            if response:
                conn.sendall(response)
        except (OSError, ConnectionError) as exc:
            print("[HttpAdapter] connection {} dropped: {}".format(addr, exc))
        finally:
            try:
                conn.close()
            except OSError:
                pass

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
        from streaprous_socket.streaprous_raw_connection import (
            run_conn_recv, run_conn_send, close_connection)

        print("[HttpAdapter] Invoke handle_client_raw for {}:{}".format(
            conn_info.client_ip, conn_info.client_port))

        # Receive data using streaprous raw connection
        data = run_conn_recv(raw_socket, conn_info)
        if not data:
            print("[HttpAdapter] No data received or connection closed")
            return

        response = self.dispatch(data, routes)
        print("[HttpAdapter] HTTP Response ({} bytes): {}".format(len(response), response[:120]))

        run_conn_send(raw_socket, conn_info, response)
        # Give the client time to ACK the data before the FIN
        time.sleep(RAW_LINGER_SECONDS)
        close_connection(raw_socket, conn_info)

    async def handle_client_coroutine(self, reader, writer):
        """
        Handle an incoming client connection using stream reader writer asynchronously.

        This method reads the request from the socket, prepares the request object,
        invokes the appropriate route handler if available, builds the response,
        and sends it back to the client.

        :param reader (asyncio.StreamReader): incoming byte stream.
        :param writer (asyncio.StreamWriter): outgoing byte stream.
        """
        addr = writer.get_extra_info("peername")
        print("[HttpAdapter] Invoke handle_client_coroutine connection {}".format(addr))
        try:
            try:
                raw = await read_http_request_async(reader, writer)
                response = await self.adispatch(raw, self.routes or {})
            except RequestTooLarge:
                response = Response().build_error(413, "Request too large")
            except asyncio.TimeoutError:
                response = Response().build_error(408, "Request timeout")
            if response:
                writer.write(response)
                await writer.drain()
        except (OSError, ConnectionError) as exc:
            print("[HttpAdapter] connection {} dropped: {}".format(addr, exc))
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (OSError, ConnectionError):
                pass

    # ------------------------------------------------------------------- helpers
    def extract_cookies(self, req, resp=None):
        """
        Build cookies from the :class:`Request <Request>` headers.

        :param req:(Request) The :class:`Request <Request>` object.
        :rtype: cookies - A dictionary of cookie key-value pairs.
        """
        return dict(req.cookies or {})

    def build_response(self, req, resp=None):
        """Builds a :class:`Response <Response>` object

        :param req: The :class:`Request <Request>` used to generate the response.
        :param resp: The response object (optional).
        :rtype: Response
        """
        response = Response(req)
        response.url = req.url
        response.cookies = CaseInsensitiveDict(self.extract_cookies(req))
        response.connection = self
        return response

    def build_json_response(self, req, resp=None):
        """Builds a :class:`Response <Response>` object from JSON data

        :param req: The :class:`Request <Request>` used to generate the response.
        :param resp: The  response object.
        :rtype: Response
        """
        response = self.build_response(req, resp)
        response.headers["Content-Type"] = "application/json"
        return response

    def add_headers(self, request):
        """
        Add headers to the request.

        This method is intended to be overridden by subclasses to inject
        custom headers. It does nothing by default.

        :param request: :class:`Request <Request>` to add headers to.
        """
        pass

    def build_proxy_headers(self, proxy):
        """Returns a dictionary of the headers to add to any request sent
        through a proxy.

        Credentials are taken from the proxy URL (``http://user:pass@host:port``);
        no header is produced for an anonymous proxy.

        :param proxy: The url of the proxy being used for this request.
        :rtype: dict
        """
        headers = {}
        username, password = get_auth_from_url(proxy)

        if username:
            token = base64.b64encode("{}:{}".format(username, password).encode()).decode()
            headers["Proxy-Authorization"] = "Basic " + token

        return headers
