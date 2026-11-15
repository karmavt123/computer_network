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
"""

from .request import Request
from .response import Response
from .dictionary import CaseInsensitiveDict

import asyncio
import inspect
import time
import json

# Import streaprous raw connection functions
try:
    from streaprous_socket.streaprous_raw_connection import (
        run_conn_recv, run_conn_send, close_connection
    )
except ImportError:
    run_conn_recv = run_conn_send = close_connection = None

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

        # Connection handler.
        self.conn = conn
        # Connection address.
        self.connaddr = addr
        # Request handler
        req = self.request
        # Response handler
        resp = self.response

        try:
            conn.settimeout(30)
            data = bytearray()
            while b"\r\n\r\n" not in data and len(data) < 65536:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data.extend(chunk)
            if b"\r\n\r\n" not in data:
                response = self._http_response(400, b'{"error":"Malformed request"}', "application/json")
            else:
                head, body = bytes(data).split(b"\r\n\r\n", 1)
                header_text = head.decode("iso-8859-1", errors="replace")
                content_length = 0
                for line in header_text.split("\r\n")[1:]:
                    if line.lower().startswith("content-length:"):
                        try:
                            content_length = max(int(line.split(":", 1)[1].strip()), 0)
                        except ValueError:
                            content_length = 0
                if content_length > 100 * 1024 * 1024:
                    response = self._http_response(413, b'{"error":"Request body too large"}', "application/json")
                    conn.sendall(response)
                    return
                while len(body) < content_length:
                    chunk = conn.recv(min(4096, content_length - len(body)))
                    if not chunk:
                        break
                    body += chunk
                msg = head.decode("iso-8859-1") + "\r\n\r\n" + body.decode("utf-8", errors="replace")
                req.prepare(msg, routes)
                if req.headers.get("content-type", "").split(";", 1)[0].strip() == "application/octet-stream":
                    req.body = body
                if req.hook:
                    result = req.hook(req.headers or {}, req.body if req.body is not None else "")
                    if inspect.isawaitable(result):
                        result = asyncio.run(result)
                    response = self._route_response(result)
                else:
                    response = resp.build_response(req)
            conn.sendall(response)
        except Exception as e:
            print("[HttpAdapter] Error handling request: {}".format(e))
            try:
                conn.sendall(self._http_response(500, b'{"error":"Internal server error"}', "application/json"))
            except OSError:
                pass
        finally:
            conn.close()

    @staticmethod
    def _http_response(status, body, content_type="application/json", extra_headers=None):
        reasons = {200: "OK", 201: "Created", 204: "No Content", 400: "Bad Request",
                   401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 409: "Conflict",
                   413: "Payload Too Large", 500: "Internal Server Error"}
        headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
            "Connection": "close",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        }
        headers.update(extra_headers or {})
        head = "HTTP/1.1 {} {}\r\n".format(status, reasons.get(status, "OK"))
        head += "".join("{}: {}\r\n".format(k, v) for k, v in headers.items()) + "\r\n"
        return head.encode("ascii") + body

    @classmethod
    def _route_response(cls, result):
        status, content_type, body, headers = 200, "application/json; charset=utf-8", b"", {}
        if isinstance(result, tuple) and len(result) == 4:
            status, content_type, body, headers = result
        elif isinstance(result, bytes):
            body = result
        elif isinstance(result, str):
            content_type, body = "text/html; charset=utf-8", result.encode("utf-8")
        else:
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
        if not isinstance(body, bytes):
            body = str(body).encode("utf-8")
        return cls._http_response(status, body, content_type, headers)

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
        # Request handler
        req = self.request
        # Response handler
        resp = self.response

        print("[HttpAdapter] Invoke handle_client_raw for {}:{}".format(conn_info.client_ip, conn_info.client_port))

        # Receive data using streaprous raw connection
        data = run_conn_recv(raw_socket, conn_info)
        
        if not data:
            print("[HttpAdapter] No data received or connection closed")
            return

        # Prepare request
        msg = data.decode("utf-8")
        req.prepare(msg, routes)
        
        # Handle request hook
        if req.hook:
            #
            # TODO: handle for App hook here
            #
            response = ""
        else:
            # Build response - returns bytes
            response = resp.build_response(req)
        
        # Send response using streaprous raw connection
        response = response if isinstance(response, bytes) else response.encode("utf-8")
        print(f"[HttpAdapter] HTTP Response ({len(response)} bytes): {response[:200]}")
        #
        # TODO run_conn_send packet
        # run_conn_send(..)
        
        # Wait longer to ensure response is transmitted, client processes it, and sends ACK
        import time
        time.sleep(5.0)
        
        # Close connection with FIN packet
        #
        # TODO run_conn_send packet
        # close_connection(...)

    async def handle_client_coroutine(self, reader, writer):
        """
        Handle an incoming client connection using stream reader writer asynchronously.

        This method reads the request from the socket, prepares the request object,
        invokes the appropriate route handler if available, builds the response,
        and sends it back to the client.

        :param conn (socket): The client socket connection.
        :param addr (tuple): The client's address.
        :param routes (dict): The route mapping for dispatching requests.
        """
        # Request handler
        req = self.request
        # Response handler
        resp = self.response

        print("[HttpAdapter] Invoke handle_client_coroutine connection {})".format(addr))
        addr = writer.get_extra_info("peername")

        # TODO Handle the request asynchronously
        msg = await reader.read(1024)


        req.prepare(msg.decode("utf-8"), routes={})

        # Handle request hook
        if req.hook:
            #
            # TODO: handle for App hook here
            #
            response = ""

        # Build response
        #print("[HttpAdapter] Start **ASYNC** build_response with type {}".format(type(req)))
        response = resp.build_response(req)

        # Send all the response asynchronously
        writer.write(response)
        await writer.drain()

    @property
    def extract_cookies(self, req, resp):
        """
        Build cookies from the :class:`Request <Request>` headers.

        :param req:(Request) The :class:`Request <Request>` object.
        :param resp: (Response) The res:class:`Response <Response>` object.
        :rtype: cookies - A dictionary of cookie key-value pairs.
        """
        cookies = {}
        for header in headers:
            if header.startswith("Cookie:"):
                cookie_str = header.split(":", 1)[1].strip()
                for pair in cookie_str.split(";"):
                    key, value = pair.strip().split("=")
                    cookies[key] = value
        return cookies

    def build_response(self, req, resp):
        """Builds a :class:`Response <Response>` object 

        :param req: The :class:`Request <Request>` used to generate the response.
        :param resp: The  response object.
        :rtype: Response
        """
        response = Response()

        # Set encoding.
        response.encoding = get_encoding_from_headers(response.headers)
        response.raw = resp
        response.reason = response.raw.reason

        if isinstance(req.url, bytes):
            response.url = req.url.decode("utf-8")
        else:
            response.url = req.url

        # Add new cookies from the server.
        response.cookies = extract_cookies(req)

        # Give the Response some context.
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

        # Set encoding.
        response.raw = resp

        if isinstance(req.url, bytes):
            response.url = req.url.decode("utf-8")
        else:
            response.url = req.url

        # Give the Response some context.
        response.request = req
        response.connection = self

        return response


    # def get_connection(self, url, proxies=None):
        # """Returns a url connection for the given URL. 

        # :param url: The URL to connect to.
        # :param proxies: (optional) A Requests-style dictionary of proxies used on this request.
        # :rtype: int
        # """

        # proxy = select_proxy(url, proxies)

        # if proxy:
            # proxy = prepend_scheme_if_needed(proxy, "http")
            # proxy_url = parse_url(proxy)
            # if not proxy_url.host:
                # raise InvalidProxyURL(
                    # "Please check proxy URL. It is malformed "
                    # "and could be missing the host."
                # )
            # proxy_manager = self.proxy_manager_for(proxy)
            # conn = proxy_manager.connection_from_url(url)
        # else:
            # # Only scheme should be lower case
            # parsed = urlparse(url)
            # url = parsed.geturl()
            # conn = self.poolmanager.connection_from_url(url)

        # return conn


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

        :class:`HttpAdapter <HttpAdapter>`.

        :param proxy: The url of the proxy being used for this request.
        :rtype: dict
        """
        headers = {}
        #
        # TODO: build your authentication here
        #       username, password =...
        # we provide dummy auth here
        #
        username, password = ("user1", "password")

        if username:
            headers["Proxy-Authorization"] = (username, password)

        return headers
