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

This module implements a simple reverse proxy server using Python's socket
and threading libraries. It routes incoming HTTP requests to backend
services based on the ``Host`` header (virtual hosts) and returns the
corresponding responses to clients.

* Several ``proxy_pass`` entries of one host form a pool; ``dist_policy``
  chooses the backend: ``round-robin`` (default), ``random`` or
  ``least-conn``.
* If the chosen backend is down, the next one of the pool is tried; when
  all fail the client receives ``502 Bad Gateway``.
* An unknown host receives ``404 Not Found``.
* ``proxy_set_header Name value;`` rewrites a request header (``$host`` is
  the original Host value); ``X-Forwarded-For`` is always added.
"""
import itertools
import random
import socket
import threading

from .httpadapter import BadRequest, HttpAdapter

BACKEND_TIMEOUT = 30

_lock = threading.Lock()
_counters = {}
_active = {}


def _error(status, text):
    return HttpAdapter.http_response(status, text.encode("utf-8"),
                                     "text/plain; charset=utf-8")


def forward_request(host, port, request):
    """
    Forwards an HTTP request to a backend server and retrieves the response.

    :params host (str): IP address of the backend server.
    :params port (int): port number of the backend server.
    :params request (bytes): complete HTTP request.

    :rtype bytes: Raw HTTP response from the backend server.
    :raises OSError: if the backend cannot be reached.
    """
    with socket.create_connection((host, int(port)), timeout=BACKEND_TIMEOUT) as backend:
        backend.sendall(request)
        backend.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            chunk = backend.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    return b"".join(chunks)


def _lookup(hostname, routes):
    """Find the virtual host of ``hostname`` (exact, then without the port)."""
    if hostname in routes:
        return hostname, routes[hostname]
    bare = hostname.rsplit(":", 1)[0]
    if bare in routes:
        return bare, routes[bare]
    return None, None


def _backends_of(entry):
    proxy_map = entry[0]
    return [proxy_map] if isinstance(proxy_map, str) else list(proxy_map)


def resolve_routing_policy(hostname, routes):
    """
    Handles an routing policy to return the ordered list of backends to try.

    The first element is chosen by the distribution policy; the remaining
    backends of the pool follow as fail-over candidates.

    :params hostname (str): Host header of the request.
    :params routes (dict): ``{host: (proxy_pass or [proxy_pass...], policy, headers)}``.
    :rtype list: ``[(host, port), ...]``; empty if the host is unknown.
    """
    key, entry = _lookup(hostname, routes)
    if entry is None:
        return []
    backends = _backends_of(entry)
    if not backends:
        return []
    policy = entry[1] if len(entry) > 1 else "round-robin"
    with _lock:
        if policy == "random":
            first = random.randrange(len(backends))
        elif policy == "least-conn":
            first = min(range(len(backends)),
                        key=lambda i: _active.get(backends[i], 0))
        else:                                   # round-robin
            counter = _counters.setdefault(key, itertools.count())
            first = next(counter) % len(backends)
    ordered = backends[first:] + backends[:first]
    result = []
    for item in ordered:
        host, _, port = item.partition(":")
        result.append((host, int(port or 80)))
    return result


def _rewrite_headers(head, client_ip, set_headers, original_host):
    """Apply ``proxy_set_header`` and add ``X-Forwarded-For``."""
    lines = head.split("\r\n")
    request_line, header_lines = lines[0], lines[1:]
    replaced = {name.lower() for name in set_headers} | {"connection"}
    kept = [line for line in header_lines
            if line.partition(":")[0].strip().lower() not in replaced]
    kept += ["{}: {}".format(name, value.replace("$host", original_host))
             for name, value in set_headers.items()]
    kept += ["X-Forwarded-For: {}".format(client_ip), "Connection: close"]
    return "\r\n".join([request_line] + kept)


def handle_client(ip, port, conn, addr, routes):
    """
    Handles an individual client connection by parsing the request,
    determining the target backend, and forwarding the request.

    :params ip (str): IP address of the proxy server.
    :params port (int): port number of the proxy server.
    :params conn (socket.socket): client connection socket.
    :params addr (tuple): client address (IP, port).
    :params routes (dict): dictionary mapping hostnames and location.
    """
    try:
        conn.settimeout(BACKEND_TIMEOUT)
        try:
            head, body = HttpAdapter._read_request(conn)
        except BadRequest as exc:
            conn.sendall(_error(exc.status, str(exc)))
            return
        hostname = ""
        for line in head.split("\r\n")[1:]:
            name, _, value = line.partition(":")
            if name.strip().lower() == "host":
                hostname = value.strip()
        candidates = resolve_routing_policy(hostname, routes)
        if not candidates:
            print("[Proxy] {} unknown host {!r}".format(addr, hostname))
            conn.sendall(_error(404, "404 Not Found: no virtual host {}".format(hostname)))
            return
        _, entry = _lookup(hostname, routes)
        set_headers = entry[2] if len(entry) > 2 else {}
        request = _rewrite_headers(head, addr[0], set_headers, hostname).encode(
            "iso-8859-1") + b"\r\n\r\n" + body
        for backend_host, backend_port in candidates:
            name = "{}:{}".format(backend_host, backend_port)
            with _lock:
                _active[name] = _active.get(name, 0) + 1
            try:
                response = forward_request(backend_host, backend_port, request)
                print("[Proxy] {} Host {} -> {}".format(addr, hostname, name))
                conn.sendall(response)
                return
            except OSError as exc:
                print("[Proxy] backend {} failed: {}".format(name, exc))
            finally:
                with _lock:
                    _active[name] -= 1
        conn.sendall(_error(502, "502 Bad Gateway: no backend of {} answered".format(hostname)))
    except OSError as exc:
        print("[Proxy] client {} error: {}".format(addr, exc))
    finally:
        conn.close()


def run_proxy(ip, port, routes):
    """
    Starts the proxy server and listens for incoming connections.

    The process binds the proxy server to the specified IP and port.
    For each incoming connection it spawns a new thread running
    :func:`handle_client`.

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
            conn, addr = proxy.accept()
            threading.Thread(target=handle_client, args=(ip, port, conn, addr, routes),
                             daemon=True).start()
    except KeyboardInterrupt:
        print("[Proxy] stopped")
    except OSError as exc:
        print("Socket error: {}".format(exc))
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
