"""Tests for 3270 Query / Query Reply structured-field support.

A host learns a terminal's true geometry and capabilities by sending a Read
Partition (Query) structured field; the terminal answers (inbound AID 0x88)
with Query Reply fields. :func:`server.query_terminal` drives that exchange for
extended (``-E``) terminals and folds the reply into the negotiated
:class:`server.TerminalModel`. These tests cover the outbound query bytes, the
reply parser, the ``query_terminal`` reconciliation over a socket pair, and the
full session start (negotiate → query → logon) through ``handle_client``.
"""
import socket
import threading
import time

import server
from server import read_partition_query, parse_query_reply

IAC, EOR, SE = 0xFF, 0xEF, 0xF0
DO, DONT, WILL, WONT, SB = 253, 254, 251, 252, 250
BINARY, TERMINAL_TYPE, EOR_OPT = 0, 24, 25
ERASE_WRITE = 0xF5


# ── query reply record builders ──────────────────────────────────────────────

def _usable_area(cols, rows):
    # 00 0A 81 81 flags flags W W H H  (length 0x000A through the height field)
    return bytes([0x00, 0x0A, 0x81, 0x81, 0x00, 0x00,
                  (cols >> 8) & 0xFF, cols & 0xFF,
                  (rows >> 8) & 0xFF, rows & 0xFF])


def _qr(qcode):
    return bytes([0x00, 0x04, 0x81, qcode])  # minimal Query Reply field


def _reply_record(cols=80, rows=32, color=True, highlight=True):
    rec = bytearray([server.AID_SF])
    rec += _usable_area(cols, rows)
    if color:
        rec += _qr(server.QR_COLOR)
    if highlight:
        rec += _qr(server.QR_HIGHLIGHT)
    return bytes(rec)


# ── the outbound query ───────────────────────────────────────────────────────

def test_read_partition_query_bytes():
    # F3 (WSF) + a 5-byte Read Partition (Query) structured field.
    assert read_partition_query() == bytes([0xF3, 0x00, 0x05, 0x01, 0xFF, 0x02])


# ── the reply parser ─────────────────────────────────────────────────────────

def test_parse_full_reply():
    caps = parse_query_reply(_reply_record(cols=80, rows=32))
    assert (caps["usable_cols"], caps["usable_rows"]) == (80, 32)
    assert caps["color"] and caps["highlight"]
    assert caps["qcodes"] == {server.QR_USABLE_AREA, server.QR_COLOR, server.QR_HIGHLIGHT}


def test_parse_usable_area_only():
    caps = parse_query_reply(_reply_record(cols=132, rows=27, color=False, highlight=False))
    assert (caps["usable_cols"], caps["usable_rows"]) == (132, 27)
    assert not caps["color"] and not caps["highlight"]


def test_parse_rejects_non_sf_aid():
    # A normal Enter reply (AID 0x7D) is not a Query Reply.
    caps = parse_query_reply(bytes([0x7D]) + _usable_area(80, 24))
    assert caps["usable_cols"] is None and not caps["qcodes"]


def test_parse_is_defensive_against_garbage():
    for bad in (b"", bytes([server.AID_SF]),
                bytes([server.AID_SF, 0x00, 0x0A, 0x81, 0x81, 0x00]),   # truncated
                bytes([server.AID_SF, 0xFF, 0xFF, 0x81, 0x81]),          # length overrun
                bytes([server.AID_SF, 0x00, 0x00, 0x00, 0x00])):         # length 0
        caps = parse_query_reply(bad)   # must not raise
        assert caps["usable_cols"] in (None, )  # nothing extracted


# ── query_terminal over a socket pair ────────────────────────────────────────

def test_query_terminal_skips_base_terminal():
    """A non-extended terminal is never queried — no bytes are sent, and the
    model is returned unchanged (so the session can't block on a reply that a
    base terminal won't send)."""
    srv, cli = socket.socketpair()
    try:
        model = server.parse_terminal_type("IBM-3278-2")   # no -E
        out = server.query_terminal(srv, model)
        assert out is model
        cli.settimeout(0.2)
        try:
            assert cli.recv(16) == b""     # nothing was sent
        except socket.timeout:
            pass                            # also fine: nothing to read
    finally:
        srv.close()
        cli.close()


def test_query_terminal_folds_in_reply():
    """An extended terminal is queried; its reported usable area and colour
    replace the type-string guess in the returned model."""
    srv, cli = socket.socketpair()
    result = {}

    def run():
        model = server.parse_terminal_type("IBM-3278-2-E")   # extended, mono guess
        result["model"] = server.query_terminal(srv, model)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    try:
        cli.settimeout(5)
        got = cli.recv(64)                       # the WSF Read Partition Query
        assert got == read_partition_query() + bytes([IAC, EOR])
        cli.sendall(_reply_record(cols=80, rows=32, color=True)
                    + bytes([IAC, EOR]))
        t.join(timeout=5)
    finally:
        srv.close()
        cli.close()

    m = result["model"]
    assert (m.alt_cols, m.alt_rows) == (80, 32)   # usable area from the reply
    assert m.color                                 # Color Query Reply seen


# ── full session start through handle_client ─────────────────────────────────

def _read_record(sock, initial=b""):
    buf = bytearray(initial)
    while not (len(buf) >= 2 and buf[-2:] == bytes([IAC, EOR])):
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("closed")
        buf.extend(chunk)
    return bytes(buf[:-2])


def _negotiate(sock, term=b"IBM-3278-2-E"):
    """Answer the server's Telnet negotiation reporting ``term``; return any
    bytes that arrived past the end of negotiation (the WSF query, typically)."""
    buf = bytearray()
    got_binary = got_eor = got_term = False
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
                if not (got_binary and got_eor and got_term):
                    sock.sendall(bytes([IAC, {DO: WILL, DONT: WONT, WILL: DO, WONT: DONT}[cmd], opt]))
                if opt == BINARY and cmd in (DO, WILL):
                    got_binary = True
                if opt == EOR_OPT and cmd in (DO, WILL):
                    got_eor = True
                del buf[:3]
            elif cmd == SB:
                se = buf.find(bytes([IAC, SE]), 2)
                if se == -1:
                    break
                if len(buf) >= 4 and buf[2] == TERMINAL_TYPE and buf[3] == 1:
                    sock.sendall(bytes([IAC, SB, TERMINAL_TYPE, 0]) + term + bytes([IAC, SE]))
                    got_term = True
                del buf[:se + 2]
            else:
                del buf[:2]
        # Return only once a non-IAC data byte (the WSF/screen stream) leads the
        # buffer — i.e. every negotiation triplet, including the server's echoed
        # responses, has been drained. Returning merely because the buffer went
        # empty would leave those trailing triplets to pollute the next record.
        if buf and buf[0] != IAC:
            return bytes(buf)
        if time.time() > deadline:
            raise TimeoutError("negotiation timed out")
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("closed during negotiation")
        buf.extend(chunk)


def test_extended_client_query_then_logon():
    """A -E client negotiates, answers the Read Partition Query, and still lands
    on the logon panel — proving the query exchange is wired into the session
    start without derailing it."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        try:
            conn, a = srv.accept()
            server.handle_client(conn, a)
        except Exception:
            pass

    threading.Thread(target=serve, daemon=True).start()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    sock.connect(("127.0.0.1", port))
    try:
        leftover = _negotiate(sock, term=b"IBM-3278-2-E")
        wsf = _read_record(sock, leftover)             # the Read Partition Query
        assert wsf == read_partition_query()           # F3 00 05 01 FF 02, IAC EOR stripped
        sock.sendall(_reply_record(cols=80, rows=24) + bytes([IAC, EOR]))
        logon = _read_record(sock)                     # the logon panel
    finally:
        sock.close()
        srv.close()

    assert logon[0] == ERASE_WRITE
    assert "LOGON" in logon.decode("cp037", errors="replace")
