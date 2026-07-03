"""Tests for the NVT (line-mode / ASCII) fallback.

A client that refuses the 3270 binary framing — a plain line-mode telnet client —
can't carry a 3270 data stream. Instead of hanging the 3270 negotiation, the
server detects this and serves a minimal ASCII TSO ``READY`` command loop
(:func:`server.run_nvt_session`). These tests drive the real ``handle_client``
with a non-3270 client and confirm the banner, the commands, and that a genuine
3270 client is *not* misrouted into NVT.
"""
import socket
import threading

import server

IAC, DO, DONT, WILL, WONT, SB, SE = 255, 253, 254, 251, 252, 250, 240
BINARY, TTYPE, EOR, TN3270E = 0, 24, 25, 40


def _serve_one():
    """Run one real handle_client on an ephemeral port; return the port."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def run():
        try:
            conn, addr = srv.accept()
            server.handle_client(conn, addr)
        except Exception:
            pass
        finally:
            srv.close()

    threading.Thread(target=run, daemon=True).start()
    return port


def _connect(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect(("127.0.0.1", port))
    return s


def _recv_until(sock, marker, limit=8192):
    data = b""
    try:
        while marker not in data and len(data) < limit:
            chunk = sock.recv(1024)
            if not chunk:
                break
            data += chunk
    except socket.timeout:
        pass
    return data


def _refuse_3270(sock):
    """Answer the server's option offers like a line-mode telnet client: refuse
    BINARY/EOR/TERMINAL-TYPE/TN3270E."""
    _recv_until(sock, b"\xff", limit=64)          # let the offers arrive
    sock.sendall(bytes([IAC, WONT, BINARY, IAC, DONT, BINARY,
                        IAC, WONT, EOR, IAC, DONT, EOR,
                        IAC, WONT, TTYPE, IAC, DONT, TN3270E]))


# ── entering NVT ─────────────────────────────────────────────────────────────

def test_refusing_binary_enters_nvt_with_a_banner():
    s = _connect(_serve_one())
    _refuse_3270(s)
    banner = _recv_until(s, b"READY")
    assert b"line-mode" in banner            # the NVT banner
    assert b"READY" in banner                # the TSO prompt
    s.close()


def test_plain_typed_data_enters_nvt():
    # A client that sends a plain line (no Telnet negotiation) is line-mode too.
    s = _connect(_serve_one())
    _recv_until(s, b"\xff", limit=64)
    s.sendall(b"time\r\n")
    out = _recv_until(s, b"IKJ56650I")
    assert b"line-mode" in out and b"IKJ56650I" in out
    s.close()


# ── the command loop ─────────────────────────────────────────────────────────

def test_time_command():
    s = _connect(_serve_one())
    _refuse_3270(s)
    _recv_until(s, b"READY")
    s.sendall(b"TIME\r\n")
    assert b"IKJ56650I" in _recv_until(s, b"IKJ56650I")
    s.close()


def test_unknown_command_is_not_found():
    s = _connect(_serve_one())
    _refuse_3270(s)
    _recv_until(s, b"READY")
    s.sendall(b"FLIBBLE\r\n")
    assert b"IKJ56500I COMMAND FLIBBLE NOT FOUND" in _recv_until(s, b"NOT FOUND")
    s.close()


def test_ispf_explains_a_3270_terminal_is_required():
    s = _connect(_serve_one())
    _refuse_3270(s)
    _recv_until(s, b"READY")
    s.sendall(b"ISPF\r\n")
    assert b"3270" in _recv_until(s, b"3270")
    s.close()


def test_logoff_ends_the_session():
    s = _connect(_serve_one())
    _refuse_3270(s)
    _recv_until(s, b"READY")
    s.sendall(b"LOGOFF\r\n")
    out = _recv_until(s, b"ENDED")
    assert b"ENDED" in out
    # The server closes the connection after LOGOFF.
    assert s.recv(64) == b""
    s.close()


# ── a real 3270 client must NOT be misrouted into NVT ────────────────────────

def test_3270_client_is_not_treated_as_nvt():
    """A client that agrees BINARY/EOR and a terminal type gets the 3270 logon
    panel (an ERASE/WRITE), never the ASCII NVT banner."""
    s = _connect(_serve_one())
    buf = bytearray()
    got_ew = False
    while True:
        try:
            chunk = s.recv(1024)
        except socket.timeout:
            break
        if not chunk:
            break
        buf.extend(chunk)
        i = 0
        while i < len(buf):
            if buf[i] != IAC:
                i += 1
                continue
            if i + 2 >= len(buf):
                break
            cmd, opt = buf[i + 1], buf[i + 2]
            if cmd == DO and opt in (BINARY, EOR):
                s.sendall(bytes([IAC, WILL, opt]))
            elif cmd == WILL and opt in (BINARY, EOR):
                s.sendall(bytes([IAC, DO, opt]))
            elif cmd == DO and opt == TTYPE:
                s.sendall(bytes([IAC, WILL, TTYPE]))
            elif cmd == WILL and opt == TN3270E:
                pass
            elif cmd == DO and opt == TN3270E:
                s.sendall(bytes([IAC, WONT, TN3270E]))   # basic TN3270
            elif cmd == SB:
                se = buf.find(bytes([IAC, SE]), i)
                if se != -1 and buf[i + 2] == TTYPE and buf[i + 3] == 1:
                    s.sendall(bytes([IAC, SB, TTYPE, 0]) + b"IBM-3278-2" + bytes([IAC, SE]))
            i += 3
        del buf[:i]
        if 0xF5 in buf or 0xF5 in chunk:   # ERASE/WRITE = the 3270 logon panel
            got_ew = True
            break
    assert got_ew, "expected a 3270 ERASE/WRITE logon, not NVT"
    assert b"line-mode" not in bytes(buf)
    s.close()
