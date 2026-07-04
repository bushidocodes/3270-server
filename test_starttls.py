"""Tests for negotiated START-TLS (the in-band TLS upgrade, Telnet option 46).

Unlike implicit TLS (test_tls.py), the session begins in the clear on the normal
port and is upgraded mid-stream: the server sends ``DO START-TLS``; a willing
client replies ``WILL START-TLS`` + ``SB START-TLS FOLLOWS SE``; the server
answers ``SB START-TLS FOLLOWS SE`` and both run the TLS handshake, after which a
normal TN3270E session flows over the encrypted socket. A client that refuses
(``WONT``) keeps a working plaintext session.
"""
import socket
import ssl
import threading

import pytest

import server
from test_tls import _client_tls_context
from test_tn3270e import _negotiate_e, _read_record

IAC, SE, SB = 0xFF, 240, 250
WILL, WONT, DO, DONT = 251, 252, 253, 254
STARTTLS, FOLLOWS = server.TELOPT_STARTTLS, server.TLS_FOLLOWS

USERID_ADDR = 5 * 80 + 17
PASSWORD_ADDR = 6 * 80 + 17


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        d = sock.recv(n - len(buf))
        if not d:
            raise ConnectionError("closed")
        buf += d
    return buf


def _serve_starttls(tls_cert):
    """Start a one-client server in negotiated-START-TLS mode; return its port."""
    certfile, keyfile = tls_cert
    ctx = server.make_tls_context(certfile, keyfile)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        try:
            conn, addr = srv.accept()
            server._client_thread(conn, addr, tls_context=ctx, starttls=True)
        finally:
            srv.close()

    threading.Thread(target=serve, daemon=True).start()
    return port


def _client_upgrade(sock):
    """The client half of START-TLS: accept the server's offer and return a
    TLS-wrapped socket (faithful to what ws3270 does — WILL, then FOLLOWS, then
    handshake only after the server's FOLLOWS)."""
    assert _recv_exact(sock, 3) == bytes([IAC, DO, STARTTLS])   # server offers
    sock.sendall(bytes([IAC, WILL, STARTTLS]))
    sock.sendall(bytes([IAC, SB, STARTTLS, FOLLOWS, IAC, SE]))
    assert _recv_exact(sock, 6) == bytes([IAC, SB, STARTTLS, FOLLOWS, IAC, SE])
    return _client_tls_context().wrap_socket(sock, server_hostname="localhost")


# ── the happy path: upgrade, then a real session ─────────────────────────────

def test_starttls_upgrades_and_runs_a_session(tls_cert):
    port = _serve_starttls(tls_cert)
    raw = socket.create_connection(("127.0.0.1", port), timeout=10)
    tls = _client_upgrade(raw)
    tls.settimeout(10)

    assert tls.cipher() is not None                 # genuinely encrypted now
    leftover, device_is = _negotiate_e(tls, devtype=b"IBM-3278-2")
    logon = _read_record(tls, leftover)
    assert device_is
    assert "z/OS V2R5.0 TSO/E LOGON" in logon.decode("cp037", errors="replace")


def test_starttls_login_round_trip(tls_cert):
    port = _serve_starttls(tls_cert)
    raw = socket.create_connection(("127.0.0.1", port), timeout=10)
    tls = _client_upgrade(raw)
    tls.settimeout(10)

    leftover, _ = _negotiate_e(tls)
    _read_record(tls, leftover)                     # logon panel
    body = bytearray([0x7D]) + bytes([0xC0, 0x40])   # AID=Enter, cursor
    for addr, text in ((USERID_ADDR, "IBMUSER"), (PASSWORD_ADDR, "SYS1")):
        body += bytes([0x11, ((addr >> 6) & 0x3F) | 0xC0, (addr & 0x3F) | 0x40])
        body += text.encode("cp037")
    tls.sendall(bytes(5) + body + bytes([IAC, 0xEF]))
    menu = _read_record(tls)
    assert "ISPF Primary Option Menu" in menu.decode("cp037", errors="replace")


# ── a client that declines keeps a plaintext session ─────────────────────────

def test_client_refusing_starttls_gets_a_plaintext_session(tls_cert):
    port = _serve_starttls(tls_cert)
    raw = socket.create_connection(("127.0.0.1", port), timeout=10)
    raw.settimeout(10)
    # Server offers DO START-TLS; we decline.
    assert _recv_exact(raw, 3) == bytes([IAC, DO, STARTTLS])
    raw.sendall(bytes([IAC, WONT, STARTTLS]))
    # The session continues in the clear: a normal TN3270E negotiation and logon.
    leftover, _ = _negotiate_e(raw)
    logon = _read_record(raw, leftover)
    assert "z/OS V2R5.0 TSO/E LOGON" in logon.decode("cp037", errors="replace")


# ── the START-TLS reply reader in isolation ──────────────────────────────────

def test_accepts_starttls_reader_handles_will_and_wont():
    a, b = socket.socketpair()
    try:
        b.sendall(bytes([IAC, WILL, STARTTLS, IAC, SB, STARTTLS, FOLLOWS, IAC, SE]))
        assert server._client_accepts_starttls(a) is True
    finally:
        a.close(); b.close()

    a, b = socket.socketpair()
    try:
        b.sendall(bytes([IAC, WONT, STARTTLS]))
        assert server._client_accepts_starttls(a) is False
    finally:
        a.close(); b.close()


def test_accepts_starttls_reader_skips_unrelated_options():
    # A client that interleaves other Telnet options before its START-TLS reply is
    # tolerated — the reader skips them and still finds the WILL.
    a, b = socket.socketpair()
    try:
        b.sendall(bytes([IAC, DONT, 24, IAC, WILL, 0])            # unrelated
                  + bytes([IAC, WILL, STARTTLS, IAC, SB, STARTTLS, FOLLOWS, IAC, SE]))
        assert server._client_accepts_starttls(a) is True
    finally:
        a.close(); b.close()
