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
daemon.proxy
~~~~~~~~~~~~~~~~~

This module implements a simple reverse proxy server using Python's socket and threading libraries.
It routes incoming HTTP requests to backend services based on hostname mappings and returns
the corresponding responses to clients.

Features
--------
- virtual hosts (``Host`` header) mapped to one or several ``proxy_pass`` backends;
- distribution policies: ``round-robin`` (default), ``least-conn``, ``random``, ``ip-hash``;
- ``proxy_set_header`` rewriting (``$host`` expands to the original Host);
- ``X-Forwarded-For`` header and 502/504 answers when a backend is down or too slow.

Requirement:
-----------------
- socket: provides socket networking interface.
- threading: enables concurrent client handling via threads.
- response: customized :class: `Response <Response>` utilities.
- dictionary: :class: `CaseInsensitiveDict <CaseInsensitiveDict>` for managing headers and cookies.

"""
import itertools
import random
import socket
import threading
import zlib

from .response import Response
from .utils import RequestTooLarge, read_http_request

#: A dictionary mapping hostnames to backend IP and port tuples.
#: Used to determine routing targets for incoming requests.
PROXY_PASS = {
    "192.168.56.103:8080": ('192.168.56.103', 9000),
    "app1.local": ('192.168.56.103', 9001),
    "app2.local": ('192.168.56.103', 9002),
}

BACKEND_TIMEOUT = 15.0

#: round-robin cursors and active connection counters, protected by ``_policy_lock``.
_policy_lock = threading.Lock()
_rr_counters = {}
_active = {}


def _http_error(status, message):
    """A tiny plain-text HTTP error produced by the proxy itself."""
    return Response().assemble(status, "{} {}".format(status, message), "text/plain; charset=utf-8")


def forward_request(host, port, request):
    """
    Forwards an HTTP request to a backend server and retrieves the response.

    :params host (str): IP address of the backend server.
    :params port (int): port number of the backend server.
    :params request (bytes|str): incoming HTTP request.

    :rtype bytes: Raw HTTP response from the backend server. A failure is mapped to
                  ``502 Bad Gateway`` (refused/reset) or ``504 Gateway Timeout``.
    """
    if isinstance(request, str):
        request = request.encode()
    backend = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    backend.settimeout(BACKEND_TIMEOUT)

    try:
        backend.connect((host, port))
        backend.sendall(request)
        response = b""
        while True:
            chunk = backend.recv(65536)
            if not chunk:
                break
            response += chunk
        return response or _http_error(502, "Bad Gateway (empty answer)")
    except socket.timeout:
        print("[Proxy] backend {}:{} timed out".format(host, port))
        return _http_error(504, "Gateway Timeout")
    except socket.error as e:
        print("[Proxy] backend {}:{} unreachable: {}".format(host, port, e))
        return _http_error(502, "Bad Gateway")
    finally:
        backend.close()


def _lookup_route(hostname, routes):
    """Find the route for ``hostname`` (exact, then without port, then ``*``)."""
    candidates = [hostname, hostname.split(":")[0], "*"]
    for name in candidates:
        if name in routes:
            return name, routes[name]
    return None, None


def pick_backend(hostname, backends, policy, client_ip=""):
    """
    Choose one entry of ``backends`` according to ``policy``.

    :rtype str: ``"ip:port"``
    """
    if len(backends) == 1:
        return backends[0]
    policy = (policy or "round-robin").replace("_", "-").lower()
    with _policy_lock:
        if policy == "random":
            return random.choice(backends)
        if policy == "ip-hash":
            return backends[zlib.crc32(client_ip.encode()) % len(backends)]
        if policy == "least-conn":
            return min(backends, key=lambda b: _active.get(b, 0))
        counter = _rr_counters.setdefault(hostname, itertools.count())
        return backends[next(counter) % len(backends)]


def resolve_routing_policy(hostname, routes, client_ip=""):
    """
    Handles an routing policy to return the matching proxy_pass.
    It determines the target backend to forward the request to.

    :params hostname (str): value of the request ``Host`` header.
    :params routes (dict): dictionary mapping hostnames and location.
    :params client_ip (str): used by the ``ip-hash`` policy.
    :rtype tuple: ``(host, port, extra_headers)``; ``host`` is ``""`` when nothing matches.
    """
    key, route = _lookup_route(hostname, routes)
    if route is None:
        print("[Proxy] no route for host '{}'".format(hostname))
        return "", 0, {}

    proxy_map, policy = route[0], route[1]
    extra = route[2] if len(route) > 2 else {}
    backends = proxy_map if isinstance(proxy_map, list) else [proxy_map]
    if not backends:
        print("[Proxy] empty proxy_pass list of '{}'".format(key))
        return "", 0, extra

    chosen = pick_backend(key, backends, policy, client_ip)
    proxy_host, _, proxy_port = chosen.rpartition(":")
    try:
        return proxy_host, int(proxy_port), dict(extra, _backend=chosen)
    except ValueError:
        print("[Proxy] invalid backend address '{}'".format(chosen))
        return "", 0, extra


def _rewrite_request(raw, client_ip, hostname, set_headers):
    """Insert X-Forwarded-For and apply ``proxy_set_header`` rules to the request head."""
    head, sep, body = raw.partition(b"\r\n\r\n")
    lines = head.decode("iso-8859-1").split("\r\n")
    overrides = {k.lower(): v.replace("$host", hostname) for k, v in set_headers.items()
                 if not k.startswith("_")}
    overrides["x-forwarded-for"] = client_ip
    kept = [lines[0]]
    for line in lines[1:]:
        name = line.split(":", 1)[0].strip().lower()
        if name in overrides:
            continue
        kept.append(line)
    for name, value in overrides.items():
        kept.append("{}: {}".format("-".join(p.capitalize() for p in name.split("-")), value))
    return "\r\n".join(kept).encode("iso-8859-1") + sep + body


def handle_client(ip, port, conn, addr, routes):
    """
    Handles an individual client connection by parsing the request,
    determining the target backend, and forwarding the request.

    The handler extracts the Host header from the request to
    matches the hostname against known routes. In the matching
    condition,it forwards the request to the appropriate backend.

    The handler sends the backend response back to the client or
    returns 404 if the hostname is unreachable or is not recognized.

    :params ip (str): IP address of the proxy server.
    :params port (int): port number of the proxy server.
    :params conn (socket.socket): client connection socket.
    :params addr (tuple): client address (IP, port).
    :params routes (dict): dictionary mapping hostnames and location.
    """
    try:
        try:
            raw = read_http_request(conn)
        except RequestTooLarge:
            conn.sendall(_http_error(413, "Request Too Large"))
            return
        except (socket.timeout, OSError):
            return
        if not raw:
            return

        hostname = ""
        for line in raw.split(b"\r\n\r\n", 1)[0].decode("iso-8859-1").splitlines()[1:]:
            if line.lower().startswith('host:'):
                hostname = line.split(':', 1)[1].strip()
                break
        if not hostname:
            conn.sendall(_http_error(400, "Missing Host header"))
            return

        print("[Proxy] {} at Host: {}".format(addr, hostname))

        # Resolve the matching destination in routes (port is an integer already)
        resolved_host, resolved_port, extra = resolve_routing_policy(hostname, routes, addr[0])

        if resolved_host:
            backend_key = extra.get("_backend", "")
            print("[Proxy] Host name {} is forwarded to {}:{}".format(hostname, resolved_host, resolved_port))
            request = _rewrite_request(raw, addr[0], hostname, extra)
            with _policy_lock:
                _active[backend_key] = _active.get(backend_key, 0) + 1
            try:
                response = forward_request(resolved_host, resolved_port, request)
            finally:
                with _policy_lock:
                    _active[backend_key] = max(0, _active.get(backend_key, 1) - 1)
        else:
            response = _http_error(404, "Not Found")
        conn.sendall(response)
    except OSError as e:
        print("[Proxy] client {} dropped: {}".format(addr, e))
    finally:
        conn.close()


def run_proxy(ip, port, routes):
    """
    Starts the proxy server and listens for incoming connections.

    The process dinds the proxy server to the specified IP and port.
    In each incomping connection, it accepts the connections and
    spawns a new thread for each client using `handle_client`.


    :params ip (str): IP address to bind the proxy server.
    :params port (int): port number to listen on.
    :params routes (dict): dictionary mapping hostnames and location.

    """

    proxy = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    proxy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        proxy.bind((ip, port))
        proxy.listen(128)
        print("[Proxy] Listening on IP {} port {}".format(ip, port))
        while True:
            try:
                conn, addr = proxy.accept()
            except OSError as e:
                print("[Proxy] accept failed: {}".format(e))
                continue
            # one daemon thread per client connection
            threading.Thread(target=handle_client, args=(ip, port, conn, addr, routes),
                             daemon=True).start()
    except KeyboardInterrupt:
        print("\n[Proxy] stopped")
    except socket.error as e:
        print("Socket error: {}".format(e))
    finally:
        proxy.close()


def create_proxy(ip, port, routes):
    """
    Entry point for launching the proxy server.

    :params ip (str): IP address to bind the proxy server.
    :params port (int): port number to listen on.
    :params routes (dict): dictionary mapping hostnames and location.
    """

    run_proxy(ip, port, routes)
