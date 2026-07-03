"""Tests for implicit TLS (secure 3270).

When a certificate is configured the server wraps every connection in TLS from
the first byte — the way an x3270-family emulator connects with the ``L:`` host
prefix. These tests confirm the handshake works and that a full TN3270E session
runs unchanged over the encrypted socket. The 3270/session code is untouched by
TLS (it only ever calls ``recv``/``sendall``), so the wrapping is all that needs
proving here; the plaintext path is exercised by every other test module.
"""
import socket
import ssl
import threading

import pytest

import server
from test_tn3270e import _negotiate_e, _read_record   # reuse the TN3270E client

USERID_ADDR = 5 * 80 + 17
PASSWORD_ADDR = 6 * 80 + 17


def _client_tls_context():
    """A client context that accepts the self-signed test cert (like an emulator
    run with -noverifycert)."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


@pytest.fixture
def tls_session(tls_cert):
    """A TLS server (one client) + a connected, TLS-wrapped client socket."""
    certfile, keyfile = tls_cert
    server_ctx = server.make_tls_context(certfile, keyfile)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        conn, addr = srv.accept()
        # _client_thread does the server-side TLS handshake, then handle_client.
        server._client_thread(conn, addr, tls_context=server_ctx)

    threading.Thread(target=serve, daemon=True).start()

    raw = socket.create_connection(("127.0.0.1", port), timeout=10)
    sock = _client_tls_context().wrap_socket(raw, server_hostname="localhost")
    sock.settimeout(10)
    try:
        yield sock
    finally:
        sock.close()
        srv.close()


def test_make_tls_context_loads_cert(tls_cert):
    certfile, keyfile = tls_cert
    ctx = server.make_tls_context(certfile, keyfile)
    assert isinstance(ctx, ssl.SSLContext)


def test_handshake_succeeds(tls_session):
    # If the fixture yielded, the TLS handshake completed; confirm a real cipher.
    assert tls_session.cipher() is not None


def test_tn3270e_session_runs_over_tls(tls_session):
    # The whole DEVICE-TYPE/FUNCTIONS negotiation and the logon screen flow over
    # the encrypted socket, byte-for-byte the same as plaintext.
    leftover, device_is = _negotiate_e(tls_session, devtype=b"IBM-3278-2")
    logon = _read_record(tls_session, leftover)
    assert device_is
    assert logon[:5] == bytes(5)                 # TN3270E data header
    assert "z/OS V2R5.0 TSO/E LOGON" in logon.decode("cp037", errors="replace")


def test_login_round_trip_over_tls(tls_session):
    leftover, _ = _negotiate_e(tls_session)
    _read_record(tls_session, leftover)          # logon panel
    body = bytearray([0x7D]) + bytes([0xC0, 0x40])   # AID=Enter, cursor
    for addr, text in ((USERID_ADDR, "IBMUSER"), (PASSWORD_ADDR, "SYS1")):
        body += bytes([0x11, ((addr >> 6) & 0x3F) | 0xC0, (addr & 0x3F) | 0x40])
        body += text.encode("cp037")
    tls_session.sendall(bytes([0x00, 0x00, 0x00, 0x00, 0x00]) + body
                        + bytes([0xFF, 0xEF]))
    menu = _read_record(tls_session)
    assert "ISPF Primary Option Menu" in menu.decode("cp037", errors="replace")


def test_plaintext_client_cannot_talk_to_tls_server(tls_cert):
    # A non-TLS client hitting the TLS port fails to negotiate rather than
    # leaking a plaintext session — the handshake rejects it.
    certfile, keyfile = tls_cert
    server_ctx = server.make_tls_context(certfile, keyfile)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    threading.Thread(
        target=lambda: server._client_thread(*srv.accept(), tls_context=server_ctx),
        daemon=True).start()

    raw = socket.create_connection(("127.0.0.1", port), timeout=10)
    raw.settimeout(5)
    # Send a plaintext TN3270 negotiation burst; the server expects a TLS
    # ClientHello, so OpenSSL rejects the bogus record (0xFF is not a valid TLS
    # content type) and the session never produces a valid 3270 record. The burst
    # is long enough to fill a TLS record header so the reject is immediate.
    raw.sendall(bytes([0xFF, 0xFB, 0x18]) * 8)   # IAC WILL TERMINAL-TYPE, repeated
    try:
        data = raw.recv(64)
    except (ConnectionResetError, ssl.SSLError, socket.timeout, OSError):
        data = b""
    # Either the connection is dropped, or whatever comes back is not a plaintext
    # TN3270E logon record (no ERASE/WRITE command byte).
    assert 0xF5 not in data
    raw.close()
    srv.close()
