"""
Test that malformed client input does NOT crash the TN3270 server (issue #4).

Verifies:
  1. Server survives a lone IAC byte (recv boundary - buffer[i+1] OOB)
  2. Server survives IAC DO with no option byte (buffer[i+2] OOB)
  3. Server survives IAC SB with only 2 bytes (buffer[i+3] OOB)
  4. Server survives a bare IAC EOR - empty AID buffer (buffer[0] OOB)
  5. After EVERY bad client the server is still alive (accepts a new connection)
"""
from __future__ import annotations

import socket
import threading
import time

import pytest

from server import run_tn3270_server

IAC = 0xFF
EOR = 0xEF
DO = 0xFD
SB = 0xFA
TERMINAL_TYPE = 0x18

TEST_PORT = 13270


@pytest.fixture(scope="module", autouse=True)
def _robustness_server():
    thread = threading.Thread(
        target=run_tn3270_server,
        kwargs={"host": "127.0.0.1", "port": TEST_PORT},
        daemon=True,
    )
    thread.start()
    deadline = time.time() + 5
    while time.time() < deadline:
        if server_alive():
            break
        time.sleep(0.05)
    else:
        pytest.fail("robustness test server did not start")
    yield


def _open_and_drain(timeout=3):
    """Connect, drain the server's opening negotiation bytes, return socket."""
    s = socket.socket()
    s.settimeout(timeout)
    s.connect(("127.0.0.1", TEST_PORT))
    try:
        s.recv(4096)
    except socket.timeout:
        pass
    return s


def server_alive(timeout=3) -> bool:
    """Return True if the server still accepts a fresh TCP connection."""
    try:
        s = socket.socket()
        s.settimeout(timeout)
        s.connect(("127.0.0.1", TEST_PORT))
        data = s.recv(4096)
        s.close()
        return len(data) > 0
    except Exception:
        return False


def test_server_alive_at_start():
    assert server_alive()


def test_lone_iac_does_not_kill_server():
    s = _open_and_drain()
    s.sendall(bytes([IAC]))
    s.close()
    time.sleep(0.3)
    assert server_alive()


def test_iac_do_truncated_does_not_kill_server():
    s = _open_and_drain()
    s.sendall(bytes([IAC, DO]))
    s.close()
    time.sleep(0.3)
    assert server_alive()


def test_iac_sb_truncated_does_not_kill_server():
    s = _open_and_drain()
    s.sendall(bytes([IAC, SB, TERMINAL_TYPE]))
    s.close()
    time.sleep(0.3)
    assert server_alive()


def test_bare_iac_eor_does_not_kill_server():
    s = _open_and_drain()
    s.sendall(bytes([IAC, EOR]))
    s.close()
    time.sleep(0.3)
    assert server_alive()
