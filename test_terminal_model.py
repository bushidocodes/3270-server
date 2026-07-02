"""Tests for TN3270 terminal-model detection.

The client reports its device type during Telnet TERMINAL-TYPE negotiation;
:func:`server.parse_terminal_type` classifies that string into a
:class:`server.TerminalModel`, and :func:`server.tn3270_negotiate` returns the
model it negotiated. These tests cover the classifier directly and drive a real
negotiation over a socket pair to confirm the model is captured end-to-end
(previously the client's type was decoded, printed, and then discarded).
"""
import socket
import threading

import server
from server import parse_terminal_type


# ── the classifier ───────────────────────────────────────────────────────────

def test_model_2_is_default_24x80():
    m = parse_terminal_type("IBM-3278-2")
    assert (m.model, m.alt_rows, m.alt_cols) == (2, 24, 80)
    assert m.default_rows == 24 and m.default_cols == 80
    assert not m.color and not m.extended


def test_model_3_4_5_alternate_sizes():
    assert (parse_terminal_type("IBM-3278-3").alt_rows,
            parse_terminal_type("IBM-3278-3").alt_cols) == (32, 80)
    assert (parse_terminal_type("IBM-3278-4").alt_rows,
            parse_terminal_type("IBM-3278-4").alt_cols) == (43, 80)
    m5 = parse_terminal_type("IBM-3278-5")
    assert (m5.model, m5.alt_rows, m5.alt_cols) == (5, 27, 132)


def test_3279_is_colour():
    assert parse_terminal_type("IBM-3279-2").color
    assert not parse_terminal_type("IBM-3278-2").color


def test_extended_suffix():
    m = parse_terminal_type("IBM-3279-4-E")
    assert m.extended and m.color
    assert (m.model, m.alt_rows, m.alt_cols) == (4, 43, 80)


def test_lowercase_is_normalised():
    m = parse_terminal_type("ibm-3278-5")
    assert m.model == 5 and m.term_type == "IBM-3278-5"


def test_dynamic_falls_back_to_model_2():
    m = parse_terminal_type("IBM-DYNAMIC")
    assert m.model == 2 and (m.alt_rows, m.alt_cols) == (24, 80)


def test_unknown_and_empty_fall_back_to_model_2():
    for bad in ("", None, "IBM-3278-9", "garbage"):
        m = parse_terminal_type(bad)
        assert m.model == 2 and (m.alt_rows, m.alt_cols) == (24, 80)
    # An empty/None type still yields a usable, named baseline.
    assert parse_terminal_type("").term_type == "IBM-3278-2"


# ── end-to-end negotiation ───────────────────────────────────────────────────

IAC, SE = 0xFF, 0xF0
DO, DONT, WILL, WONT, SB = 253, 254, 251, 252, 250
BINARY, TERMINAL_TYPE, EOR_OPT = 0, 24, 25


def _drive_client(sock, term_type: bytes):
    """Minimal 3270 client: answer the server's option negotiation and report
    ``term_type`` when asked to SEND our terminal type."""
    buf = bytearray()
    got_binary = got_eor = sent_term = False
    sock.settimeout(5)
    while not (got_binary and got_eor and sent_term):
        try:
            data = sock.recv(1024)
        except socket.timeout:
            break
        if not data:
            break
        buf.extend(data)
        i = 0
        while i < len(buf):
            if buf[i] != IAC or i + 1 >= len(buf):
                i += 1
                continue
            cmd = buf[i + 1]
            if cmd in (DO, DONT, WILL, WONT):
                if i + 2 >= len(buf):
                    break
                opt = buf[i + 2]
                reply = {DO: WILL, DONT: WONT, WILL: DO, WONT: DONT}[cmd]
                sock.sendall(bytes([IAC, reply, opt]))
                if opt == BINARY and cmd in (DO, WILL):
                    got_binary = True
                if opt == EOR_OPT and cmd in (DO, WILL):
                    got_eor = True
                i += 3
                continue
            if cmd == SB:
                se = buf.find(bytes([IAC, SE]), i + 2)
                if se == -1:
                    break
                if buf[i + 2] == TERMINAL_TYPE and buf[i + 3] == 1:  # SEND
                    sock.sendall(bytes([IAC, SB, TERMINAL_TYPE, 0])
                                 + term_type + bytes([IAC, SE]))
                    sent_term = True
                i = se + 2
                continue
            i += 2


def _negotiate_model(term_type: bytes) -> server.TerminalModel:
    srv, cli = socket.socketpair()
    result = {}
    try:
        t = threading.Thread(
            target=lambda: result.__setitem__("model",
                                              server.tn3270_negotiate(srv)),
            daemon=True,
        )
        t.start()
        _drive_client(cli, term_type)
        t.join(timeout=5)
    finally:
        srv.close()
        cli.close()
    return result.get("model")


def test_negotiate_returns_model_5():
    model = _negotiate_model(b"IBM-3278-5")
    assert model is not None
    assert model.model == 5 and (model.alt_rows, model.alt_cols) == (27, 132)


def test_negotiate_returns_colour_model_4():
    model = _negotiate_model(b"IBM-3279-4-E")
    assert model.model == 4 and model.color and model.extended
