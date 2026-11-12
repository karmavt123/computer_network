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
It implements a basic backend HTTP server on top of three interchangeable transports.
All of them hand the received bytes to the same :class:`HttpAdapter` pipeline.

====================  ==========================================================
mode                  description
====================  ==========================================================
``coroutine``         asyncio streams, non-blocking, one event loop (default)
``threading``         standard blocking sockets, one daemon thread per client
``raw``               streaprous raw TCP connection (needs sudo + non-loopback IP)
====================  ==========================================================

Notes:
------
- Handlers can be plain functions or ``async def`` coroutines in every mode.
- Socket errors are logged and never terminate the accept loop.

Usage Example:
--------------
>>> create_backend("127.0.0.1", 9000, routes={}, mode="threading")

"""

import asyncio
import inspect
import os
import socket
import threading

from .httpadapter import HttpAdapter

MODE_COROUTINE = "coroutine"
MODE_THREADING = "threading"
MODE_RAW = "raw"
MODES = (MODE_COROUTINE, MODE_THREADING, MODE_RAW)

#: Transport used when ``create_backend`` is called without ``mode``.
DEFAULT_MODE = os.environ.get("STREAPROUS_MODE", MODE_COROUTINE)


def _print_routes(routes):
    """Log the registered webapp routes."""
    if not routes:
        return
    print("[Backend] route settings")
    for key, value in routes.items():
        flag = "**ASYNC** " if inspect.iscoroutinefunction(value) else ""
        print("   + ('{}', '{}'): {}{}".format(key[0], key[1], flag, value))


# --------------------------------------------------------------------- threading
def handle_client(ip, port, conn, addr, routes):
    """
    Initializes an HttpAdapter instance and delegates the client handling logic to it.

    :param ip (str): IP address of the server.
    :param port (int): Port number the server is listening on.
    :param conn (socket.socket): Client connection socket.
    :param addr (tuple): client address (IP, port).
    :param routes (dict): Dictionary of route handlers.
    """
    print("[Backend] Invoke handle_client accepted connection from {}".format(addr))
    daemon = HttpAdapter(ip, port, conn, addr, routes)

    # Handle client
    daemon.handle_client(conn, addr, routes)


def run_backend_standard(ip, port, routes):
    """
    Starts the backend with standard blocking sockets; one daemon thread serves each client.

    :param ip (str): IP address to bind the server.
    :param port (int): Port number to listen on.
    :param routes (dict): Dictionary of route handlers.
    """
    print("[Backend] run_backend_standard (threading) on {}:{}".format(ip, port))

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind((ip, port))
        server.listen(128)
        print("[Backend] Listening on port {}".format(port))
        _print_routes(routes)

        while True:
            try:
                conn, addr = server.accept()
            except OSError as e:
                print("[Backend] accept failed: {}".format(e))
                continue
            print("[Backend] Accepted connection from {}:{}".format(addr[0], addr[1]))
            threading.Thread(target=handle_client, args=(ip, port, conn, addr, routes),
                             daemon=True).start()
    except KeyboardInterrupt:
        print("\n[Backend] stopped")
    except socket.error as e:
        print("Socket error: {}".format(e))
    finally:
        server.close()


# --------------------------------------------------------------------- coroutine
async def handle_client_coroutine(reader, writer, routes=None):
    """
    Coroutine in async communication to initialize connection instance
    then delegates the client handling logic to it.

    :param reader (StreamReader): Stream reader wrapper.
    :param write (Stream write): Stream write wrapper.
    :param routes (dict): Dictionary of route handlers.
    """
    addr = writer.get_extra_info("peername")
    print("[Backend] Invoke handle_client_coroutine accepted connection from {}".format(addr))
    daemon = HttpAdapter(None, None, None, addr, routes or {})
    await daemon.handle_client_coroutine(reader, writer)


async def async_server(ip="0.0.0.0", port=7000, routes={}):
    """Serve forever with asyncio; every connection is a task on one event loop."""
    print("[Backend] async_server **ASYNC** listening on port {}".format(port))
    _print_routes(routes)

    async def _client(reader, writer):
        await handle_client_coroutine(reader, writer, routes)

    server = await asyncio.start_server(_client, ip, port, backlog=128)
    async with server:
        await server.serve_forever()


def run_backend_coroutine(ip, port, routes):
    """Blocking entry for :func:`async_server` (Ctrl-C to stop)."""
    try:
        asyncio.run(async_server(ip, port, routes))
    except KeyboardInterrupt:
        print("\n[Backend] stopped")
    except OSError as e:
        print("Socket error: {}".format(e))


# --------------------------------------------------------------------------- raw
def handle_client_raw(server, conn_info, routes):
    """
    Handle client connection using streaprous raw connection.

    :param server: Raw socket
    :param conn_info: conn_struct tuple for the established connection
    :param routes (dict): Dictionary of route handlers.
    """
    print("[Backend] Invoke handle_client_raw for {}:{}".format(conn_info.client_ip, conn_info.client_port))

    # Create HttpAdapter with connection info
    daemon = HttpAdapter(
        conn_info.server_ip,
        conn_info.server_port,
        None,  # conn not used with raw sockets
        (conn_info.client_ip, conn_info.client_port),
        routes
    )

    # Store connection info for use in HttpAdapter
    daemon.conn_info = conn_info
    daemon.raw_socket = server

    # Handle client using raw connection
    daemon.handle_client_raw(server, conn_info, routes)


def run_backend(ip, port, routes, interface=None):
    """
    Starts the backend server on the streaprous raw connection (external IP only).
    Each connection is handled in a separate thread.

    :param ip (str): IP address to bind the server.
    :param port (int): Port number to listen on.
    :param routes (dict): Dictionary of route handlers.
    :param interface (str, optional): Network interface to bind to. Defaults to platform default.
    """
    # Import lazily: netifaces / raw sockets are only needed in this mode
    from streaprous_socket.streaprous_raw_connection import create_raw_socket, accept

    print("[Backend] run_backend with routes={}".format(routes))

    # Check if IP is localhost/loopback
    is_localhost = (ip == "127.0.0.1" or ip == "::1" or ip.startswith("127.") or ip == "localhost")
    if is_localhost:
        raise ValueError("Invalid localhost address: the raw transport needs a real interface IP")

    # Auto-detect interface if not provided
    if interface is None:
        from start_backend import detect_interface
        interface = detect_interface(ip)

    try:
        server = create_raw_socket()

        print("[Backend] Listening on port {}".format(port))
        _print_routes(routes)

        # Main accept loop using streaprous raw connection
        while True:
            conn_info = accept(server, ip, port, interface)
            if conn_info is None:
                continue

            print("[Backend] Accepted connection from {}:{}".format(conn_info.client_ip, conn_info.client_port))
            threading.Thread(target=handle_client_raw, args=(server, conn_info, routes),
                             daemon=True).start()

    except PermissionError:
        print("Permission error: Raw sockets require root privileges (sudo)")
    except KeyboardInterrupt:
        print("\n[Backend] stopped")
    except socket.error as e:
        print("Socket error: {}".format(e))
    except Exception as e:
        print("Error: {}".format(e))


def create_backend(ip, port, routes={}, interface=None, mode=None):
    """
    Entry point for creating and running the backend server.

    :param ip (str): IP address to bind the server.
    :param port (int): Port number to listen on.
    :param routes (dict, optional): Dictionary of route handlers. Defaults to empty dict.
    :param interface (str, optional): Network interface (raw mode only).
    :param mode (str, optional): ``coroutine`` | ``threading`` | ``raw``.
    """
    mode = mode or DEFAULT_MODE
    if mode not in MODES:
        raise ValueError("unknown backend mode '{}', expected one of {}".format(mode, MODES))
    print("[Backend] mode = {}".format(mode))

    if mode == MODE_COROUTINE:
        run_backend_coroutine(ip, port, routes)
    elif mode == MODE_THREADING:
        run_backend_standard(ip, port, routes)
    else:
        run_backend(ip, port, routes, interface)
