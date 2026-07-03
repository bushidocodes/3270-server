"""End-to-end tests for TN3270E (RFC 2355).

A TN3270E-speaking client accepts the TN3270E option, drives the DEVICE-TYPE /
FUNCTIONS sub-negotiation, and then exchanges 3270 records that each carry the
5-byte TN3270E data header. These tests boot the real ``handle_client`` and
confirm: the option is negotiated, the outbound logon record is header-framed,
and an inbound reply that carries a header logs in and reaches the ISPF menu.
"""
import socket
import threading
import time

import pytest

import server

IAC, EOR, SE, SB = 0xFF, 0xEF, 0xF0, 0xFA
DO, DONT, WILL, WONT = 0xFD, 0xFE, 0xFB, 0xFC
BINARY, TERMINAL_TYPE, EOR_OPT, TN3270E = 0, 24, 25, 40
# TN3270E sub-commands
E_CONNECT, E_DEVICE_TYPE, E_FUNCTIONS, E_IS, E_REQUEST, E_SEND = 1, 2, 3, 4, 7, 8
ENTER = 0x7D
ERASE_WRITE = 0xF5
HEADER = bytes([0x00, 0x00, 0x00, 0x00, 0x00])   # expected outbound data header

USERID_ADDR = 5 * 80 + 17
PASSWORD_ADDR = 6 * 80 + 17
ZCMD_ADDR = 2 * 80 + 14


def _e_sb(sock, payload):
    sock.sendall(bytes([IAC, SB, TN3270E]) + bytes(payload) + bytes([IAC, SE]))


def _negotiate_e(sock, devtype=b"IBM-3278-2"):
    """Drive the server's negotiation as a TN3270E client. Accepts TN3270E,
    answers SEND DEVICE-TYPE with a DEVICE-TYPE REQUEST, and agrees an empty
    FUNCTIONS set. Returns (leftover_bytes, device_is_seen)."""
    buf = bytearray()
    seen_device_is = False
    deadline = time.time() + 10
    while True:
        while buf and buf[0] == IAC:
            if len(buf) < 2:
                break
            cmd = buf[1]
            if cmd in (DO, DONT, WILL, WONT):
                if len(buf) < 3:
                    break
                opt = buf[2]
                if opt == TN3270E and cmd == DO:
                    sock.sendall(bytes([IAC, WILL, TN3270E]))
                elif opt in (BINARY, EOR_OPT, TERMINAL_TYPE):
                    sock.sendall(bytes([IAC, {DO: WILL, WILL: DO, DONT: WONT, WONT: DONT}[cmd], opt]))
                else:
                    sock.sendall(bytes([IAC, {DO: WONT, WILL: DONT, DONT: WONT, WONT: DONT}[cmd], opt]))
                del buf[:3]
            elif cmd == SB:
                se = buf.find(bytes([IAC, SE]), 2)
                if se == -1:
                    break
                opt = buf[2]
                sub = bytes(buf[3:se])
                if opt == TERMINAL_TYPE and sub[:1] == bytes([1]):        # SEND
                    sock.sendall(bytes([IAC, SB, TERMINAL_TYPE, 0]) + devtype + bytes([IAC, SE]))
                elif opt == TN3270E and sub[:2] == bytes([E_SEND, E_DEVICE_TYPE]):
                    _e_sb(sock, bytes([E_DEVICE_TYPE, E_REQUEST]) + devtype)
                elif opt == TN3270E and sub[:2] == bytes([E_DEVICE_TYPE, E_IS]):
                    seen_device_is = True
                elif opt == TN3270E and sub[:2] == bytes([E_FUNCTIONS, E_REQUEST]):
                    _e_sb(sock, bytes([E_FUNCTIONS, E_IS]))               # agree: no functions
                del buf[:se + 2]
            else:
                del buf[:2]
        if buf and buf[0] != IAC:
            return bytes(buf), seen_device_is
        if time.time() > deadline:
            raise TimeoutError("TN3270E negotiation timed out")
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("closed during negotiation")
        buf.extend(chunk)


def _read_record(sock, initial=b""):
    buf = bytearray(initial)
    while not (len(buf) >= 2 and buf[-2:] == bytes([IAC, EOR])):
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("closed")
        buf.extend(chunk)
    return bytes(buf[:-2])


def _pack(addr):
    return bytes([((addr >> 6) & 0x3F) | 0xC0, (addr & 0x3F) | 0x40])


def _reply_e(fields=None, aid=ENTER, cursor=0):
    """An inbound TN3270E record: the 5-byte header, then AID, cursor, fields."""
    body = bytearray([aid]) + _pack(cursor)
    for addr, text in (fields or {}).items():
        body += bytes([0x11]) + _pack(addr) + text.encode("cp037")
    return HEADER + bytes(body) + bytes([IAC, EOR])


@pytest.fixture
def e_session():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        try:
            server.handle_client(*srv.accept())
        except Exception:
            pass

    threading.Thread(target=serve, daemon=True).start()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    sock.connect(("127.0.0.1", port))
    try:
        yield sock
    finally:
        sock.close()
        srv.close()


def test_tn3270e_negotiated_and_logon_is_header_framed(e_session):
    leftover, device_is = _negotiate_e(e_session, devtype=b"IBM-3278-2")
    logon = _read_record(e_session, leftover)
    assert device_is                       # server answered DEVICE-TYPE IS
    assert logon[:5] == HEADER             # the 5-byte TN3270E data header
    assert logon[5] == ERASE_WRITE         # then the 3270 data stream
    assert "z/OS V2R5.0 TSO/E LOGON" in logon.decode("cp037", errors="replace")


def test_login_round_trip_over_tn3270e(e_session):
    leftover, _ = _negotiate_e(e_session)
    _read_record(e_session, leftover)                       # logon panel
    # An inbound reply carrying the 5-byte header must be parsed (header stripped)
    # and log us in.
    e_session.sendall(_reply_e({USERID_ADDR: "IBMUSER", PASSWORD_ADDR: "SYS1"}))
    menu = _read_record(e_session)
    assert menu[:5] == HEADER
    assert "ISPF Primary Option Menu" in menu.decode("cp037", errors="replace")

    # And a subsequent option (typed on the header-framed command line) works.
    e_session.sendall(_reply_e({ZCMD_ADDR: "7"}))
    dlg = _read_record(e_session)
    assert "Dialog Test - Variables" in dlg.decode("cp037", errors="replace")


def test_tn3270e_dynamic_device_type(e_session):
    # IBM-DYNAMIC has no model in its name; the session still negotiates and runs.
    leftover, device_is = _negotiate_e(e_session, devtype=b"IBM-DYNAMIC")
    logon = _read_record(e_session, leftover)
    assert device_is and logon[:5] == HEADER and logon[5] == ERASE_WRITE
