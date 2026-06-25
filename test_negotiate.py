"""Regression test for issue #27: negotiate() must drain all Telnet option
replies so the first screen arrives with no leftover negotiation bytes ahead of
the 3270 data stream.

Boots the real server in-process and drives a connection through the
test_concurrent harness, asserting the logon panel begins exactly at the
ERASE_WRITE order (0xF5) — not at a stray IAC negotiation triplet.
"""
import socket
import threading
import time

import pytest

import server
from test_concurrent import negotiate, recv_until_eor, build_aid

IAC, EOR = 0xFF, 0xEF
PORT = 2396


@pytest.fixture(scope="module")
def running_server():
    threading.Thread(
        target=server.run_tn3270_server,
        kwargs={"host": "127.0.0.1", "port": PORT},
        daemon=True,
    ).start()
    # Wait for the listener to accept connections.
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", PORT), timeout=0.5).close()
            break
        except OSError:
            time.sleep(0.05)
    yield


def _connect():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(8)
    sock.connect(("127.0.0.1", PORT))
    return sock


def test_logon_stream_has_no_negotiation_prefix(running_server):
    sock = _connect()
    try:
        leftover = negotiate(sock)
        data = recv_until_eor(sock, leftover)
    finally:
        sock.close()

    assert data[0] == 0xF5, f"stream should start at ERASE_WRITE, got {data[:8].hex()}"
    assert data[1] != IAC  # not a negotiation triplet
    assert "z/OS V2R5.0 TSO/E LOGON" in data.decode("cp037", errors="replace")


def test_login_round_trip_after_clean_negotiation(running_server):
    sock = _connect()
    try:
        leftover = negotiate(sock)
        recv_until_eor(sock, leftover)  # logon panel
        sock.sendall(build_aid("IBMUSER", "SYS1"))
        menu = recv_until_eor(sock)
    finally:
        sock.close()

    # The AID was parsed correctly (no leftover client-side option replies
    # polluting the server's read), so we land on the ISPF menu.
    assert menu[0] == 0xF5
    assert "ISPF Primary Option Menu" in menu.decode("cp037", errors="replace")
