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
daemon.backend
~~~~~~~~~~~~~~~~~

This module provides a backend object to manage and persist backend daemon.
It accepts client connections and hands each one to an :class:`HttpAdapter`.

Three concurrency modes are available (``set_mode`` / ``--mode``):

========== ============================================================
coroutine  default. One asyncio event loop accepts and serves every
           client with non-blocking streams. Blocking route handlers
           run in a thread-pool executor, so the loop never stalls.
threading  blocking sockets, one daemon thread per client.
raw        the course's raw TCP implementation (``streaprous_socket``);
           needs root and a non-loopback interface.
========== ============================================================

Usage Example:
--------------
>>> create_backend("127.0.0.1", 9000, routes={})
"""

import asyncio
import functools
import inspect
import socket
import threading
from concurrent.futures import ThreadPoolExecutor

from .httpadapter import HttpAdapter

# Import streaprous raw connection
try:
    from streaprous_socket.streaprous_raw_connection import (
        create_raw_socket, accept, run_conn_recv, run_conn_send,
        close_connection, DEFAULT_INTERFACE_NAME
    )
    RAW_SOCKET_IMPORT_ERROR = None
except ImportError as raw_socket_import_error:
    RAW_SOCKET_IMPORT_ERROR = raw_socket_import_error
    create_raw_socket = accept = run_conn_recv = run_conn_send = close_connection = None
    DEFAULT_INTERFACE_NAME = None

MODES = ("coroutine", "threading", "raw")

#: Worker threads for blocking handlers in coroutine mode.
EXECUTOR_WORKERS = 64

#: Concurrency mode of the backend (see module docstring).
mode_async = "coroutine"


def set_mode(mode):
    """Select the concurrency mode used by :func:`run_backend`."""
    global mode_async
    if mode not in MODES:
        raise ValueError("mode must be one of {}".format(", ".join(MODES)))
    mode_async = mode


def _print_routes(port, routes, label):
    print("[Backend] {} listening on port {}".format(label, port))
    for key, value in (routes or {}).items():
        marker = "**ASYNC** " if inspect.iscoroutinefunction(value) else ""
        print("   + ('{}', '{}'): {}{}".format(key[0], key[1], marker, value.__name__))


def handle_client(ip, port, conn, addr, routes):
    """
    Initializes an HttpAdapter instance and delegates the client handling logic to it.

    :param ip (str): IP address of the server.
    :param port (int): Port number the server is listening on.
    :param conn (socket.socket): Client connection socket.
    :param addr (tuple): client address (IP, port).
    :param routes (dict): Dictionary of route handlers.
    """
    daemon = HttpAdapter(ip, port, conn, addr, routes)
    daemon.handle_client(conn, addr, routes)


# Kept under the skeleton's name; the threaded server uses the same logic.
handle_client_callback = handle_client


# Handle client using streaprous raw connection
def handle_client_raw(server, conn_info, routes):
    """
    Handle client connection using streaprous raw connection.

    :param server: Raw socket
    :param conn_info: conn_struct tuple for the established connection
    :param routes (dict): Dictionary of route handlers.
    """
    daemon = HttpAdapter(conn_info.server_ip, conn_info.server_port, None,
                         (conn_info.client_ip, conn_info.client_port), routes)
    daemon.conn_info = conn_info
    daemon.raw_socket = server
    daemon.handle_client_raw(server, conn_info, routes)


# Coroutine async/await for handling new client
async def handle_client_coroutine(reader, writer, routes=None):
    """
    Coroutine in async communication to initialize connection instance
    then delegates the client handling logic to it.

    :param reader (StreamReader): Stream reader wrapper.
    :param writer (StreamWriter): Stream writer wrapper.
    :param routes (dict): Dictionary of route handlers.
    """
    addr = writer.get_extra_info("peername")
    daemon = HttpAdapter(None, None, None, addr, routes)
    await daemon.handle_client_coroutine(reader, writer)


async def async_server(ip="0.0.0.0", port=7000, routes=None, ready=None):
    """Serve forever with asyncio streams (non-blocking, one event loop)."""
    _print_routes(port, routes, "async_server **ASYNC**")
    # Blocking route handlers run in this pool; its size bounds how many of
    # them can wait at the same time (the default pool has only cpu+4 workers).
    asyncio.get_running_loop().set_default_executor(
        ThreadPoolExecutor(max_workers=EXECUTOR_WORKERS, thread_name_prefix="handler"))
    handler = functools.partial(handle_client_coroutine, routes=routes)
    server = await asyncio.start_server(handler, ip, port, backlog=128,
                                        limit=64 * 1024)
    if ready is not None:
        ready.set()
    async with server:
        await server.serve_forever()


def run_backend(ip, port, routes, interface=None):
    """
    Starts the backend server in the configured mode.

    :param ip (str): IP address to bind the server.
    :param port (int): Port number to listen on.
    :param routes (dict): Dictionary of route handlers.
    :param interface (str, optional): Network interface (raw mode only).
    """
    if mode_async == "coroutine":
        try:
            asyncio.run(async_server(ip, port, routes))
        except KeyboardInterrupt:
            print("[Backend] stopped")
        return
    if mode_async == "threading":
        run_backend_standard(ip, port, routes)
        return
    run_backend_raw(ip, port, routes, interface)


def run_backend_raw(ip, port, routes, interface=None):
    """Accept loop on top of the course's raw TCP implementation."""
    if create_raw_socket is None:
        raise RuntimeError("Raw socket support is unavailable") from RAW_SOCKET_IMPORT_ERROR
    if interface is None:
        from start_backend import detect_interface
        interface = detect_interface(ip)
    try:
        server = create_raw_socket()
        _print_routes(port, routes, "raw backend")
        while True:
            conn_info = accept(server, ip, port, interface)
            if conn_info is None:
                continue
            threading.Thread(target=handle_client_raw, args=(server, conn_info, routes),
                             daemon=True).start()
    except PermissionError:
        print("Permission error: Raw sockets require root privileges (sudo)")
    except OSError as exc:
        print("Socket error: {}".format(exc))


def run_backend_standard(ip, port, routes):
    """
    Starts the backend server with blocking sockets, one thread per client.

    :param ip (str): IP address to bind the server.
    :param port (int): Port number to listen on.
    :param routes (dict): Dictionary of route handlers.
    """
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind((ip, port))
        server.listen(128)
        _print_routes(port, routes, "threaded backend")
        while True:
            conn, addr = server.accept()
            threading.Thread(target=handle_client, args=(ip, port, conn, addr, routes),
                             daemon=True).start()
    except KeyboardInterrupt:
        print("[Backend] stopped")
    except OSError as exc:
        print("Socket error: {}".format(exc))
    finally:
        server.close()


def create_backend(ip, port, routes=None, interface=None):
    """
    Entry point for creating and running the backend server.

    :param ip (str): IP address to bind the server.
    :param port (int): Port number to listen on.
    :param routes (dict, optional): Dictionary of route handlers. Defaults to empty dict.
    :param interface (str, optional): Network interface to bind to. Defaults to platform default.
    """
    run_backend(ip, port, routes or {}, interface)
