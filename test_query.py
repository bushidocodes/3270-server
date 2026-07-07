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

from dataclasses import replace

import server
from server import (
    read_partition_query, read_partition_query_list, parse_query_reply,
    set_reply_mode, request_reply_mode, RM_FIELD, RM_EXTENDED_FIELD, RM_CHARACTER,
    QR_REPLY_MODES, _iac_escape,
)

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


# The base + APL descriptors a US ws3270 actually reports (CP037 base, CP310 APL).
_WS3270_SETS = ((0x00, 0x10, 0x00, 0x02B90025), (0x01, 0x00, 0xF1, 0x03C30136))


def _charsets(ge=True, cgcsgid=True, dbcs=False, sets=_WS3270_SETS):
    """Build a Character Sets (0x85) Query Reply structured field.

    ``sets`` is a list of ``(SET, FLAGS, LCID, CGCSGID)`` descriptors. DBCS
    descriptors carry an extra SW/SH/SUBSN/SUBSN block (DL=11) before the CGCSGID.
    """
    flags = (server.CS_FLAG_GE if ge else 0)
    flags |= (server.CS_FLAG_CGCSGID if cgcsgid else 0)
    flags |= (server.CS_FLAG_DBCS if dbcs else 0)
    dl = 11 if dbcs else 7
    body = bytearray([flags, 0x00, 0x09, 0x0C, 0x00, 0x00, 0x00, 0x00, dl])
    for st, fl, lcid, cg in sets:
        body += bytes([st, fl, lcid])
        if dbcs:
            body += bytes([0x00, 0x00, 0x00, 0x00])   # SW SH SUBSN SUBSN
        body += cg.to_bytes(4, "big")
    sf_body = bytes([server.SF_QUERY_REPLY, server.QR_CHARSETS]) + bytes(body)
    length = 2 + len(sf_body)
    return bytes([(length >> 8) & 0xFF, length & 0xFF]) + sf_body


# ── the outbound query ───────────────────────────────────────────────────────

def test_read_partition_query_bytes():
    # F3 (WSF) + a 5-byte Read Partition (Query) structured field.
    assert read_partition_query() == bytes([0xF3, 0x00, 0x05, 0x01, 0xFF, 0x02])


def test_read_partition_query_list_bytes():
    # F3 (WSF) + a Read Partition Query List (type 0x03) requesting All (0x80).
    assert read_partition_query_list() == bytes([0xF3, 0x00, 0x06, 0x01, 0xFF, 0x03, 0x80])


def test_iac_escape_doubles_ff():
    # The partition byte 0xFF must be doubled so Telnet carries it as data.
    assert _iac_escape(read_partition_query_list()) == \
        bytes([0xF3, 0x00, 0x06, 0x01, 0xFF, 0xFF, 0x03, 0x80])


# ── Set Reply Mode (#112) ────────────────────────────────────────────────────

def test_set_reply_mode_bytes():
    # F3 (WSF) + [len][len] 09 (Set Reply Mode) 00 (partition) <mode> [attr-types].
    assert set_reply_mode(RM_FIELD) == bytes([0xF3, 0x00, 0x05, 0x09, 0x00, 0x00])
    assert set_reply_mode(RM_EXTENDED_FIELD) == bytes([0xF3, 0x00, 0x05, 0x09, 0x00, 0x01])
    assert set_reply_mode(RM_CHARACTER) == bytes([0xF3, 0x00, 0x05, 0x09, 0x00, 0x02])
    # A Character-mode attribute-type list lengthens the field; len counts itself.
    assert set_reply_mode(RM_CHARACTER, [0x41, 0x42]) == \
        bytes([0xF3, 0x00, 0x07, 0x09, 0x00, 0x02, 0x41, 0x42])


def test_request_reply_mode_sends_when_the_terminal_supports_it():
    """A terminal that advertised Reply Modes is switched to Character mode: the
    Set Reply Mode WSF is sent (IAC-escaped, IAC-EOR-terminated) and the model
    records the active mode."""
    srv, cli = socket.socketpair()
    result = {}

    def run():
        model = replace(server.parse_terminal_type("IBM-3278-2-E"),
                        query_caps=frozenset({QR_REPLY_MODES}))
        result["model"] = server.request_reply_mode(srv, model, RM_CHARACTER)

    t = threading.Thread(target=run, daemon=True); t.start()
    try:
        cli.settimeout(5)
        got = cli.recv(64)
        assert got == _iac_escape(set_reply_mode(RM_CHARACTER)) + bytes([IAC, EOR])
        t.join(timeout=5)
    finally:
        srv.close(); cli.close()
    assert result["model"].reply_mode == RM_CHARACTER


def test_request_reply_mode_skips_a_terminal_without_the_capability():
    """A terminal that did not advertise Reply Modes stays in Field mode and gets
    no Set Reply Mode WSF (nothing is sent)."""
    srv, cli = socket.socketpair()
    result = {}

    def run():
        model = server.parse_terminal_type("IBM-3278-2-E")   # no query_caps
        result["model"] = server.request_reply_mode(srv, model, RM_CHARACTER)

    t = threading.Thread(target=run, daemon=True); t.start()
    t.join(timeout=5)
    try:
        cli.settimeout(0.3)
        sent = b""
        try:
            sent = cli.recv(64)
        except socket.timeout:
            pass
        assert sent == b""                       # nothing was sent
    finally:
        srv.close(); cli.close()
    assert result["model"].reply_mode == RM_FIELD


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


def test_parse_summary_enumerates_capabilities():
    # A real terminal advertises many capabilities only in the Summary (0x80),
    # not as standalone replies. The parser must fold the Summary's QCODE list in
    # — so colour/highlight/etc. are detected from it even with no 0x86/0x87 SF.
    summary = bytes([0x00, 0x0A, 0x81, server.QR_SUMMARY,
                     server.QR_SUMMARY, server.QR_USABLE_AREA, server.QR_CHARSETS,
                     server.QR_COLOR, server.QR_HIGHLIGHT, server.QR_REPLY_MODES])
    rec = bytes([server.AID_SF]) + summary + _usable_area(80, 24)
    caps = parse_query_reply(rec)
    assert caps["color"] and caps["highlight"]
    assert caps["charsets"] and caps["reply_modes"]
    assert server.QR_REPLY_MODES in caps["qcodes"]


# ── the Character Sets (0x85) reply payload ──────────────────────────────────

# A real Character Sets reply captured from ws3270 v4.4 (basic-TN3270, IBM-3279-2-E).
# Ground truth so the parser is tested against a genuine terminal, not just our own
# builder. Decodes to: GE supported; base set CP037 (CGCSGID 0x02B90025, CPGID 37);
# graphic set LCID 0xF1 = CP310 APL/line-drawing (CGCSGID 0x03C30136, CPGID 310).
_REAL_WS3270_CHARSETS = bytes.fromhex(
    "001b81858200090c000000000700100002b900250100f103c30136")


def test_charsets_builder_matches_the_real_ws3270_reply():
    # Our synthetic builder reproduces a real terminal's bytes exactly — so tests
    # built on it are testing the true wire format.
    assert _charsets() == _REAL_WS3270_CHARSETS


def test_parse_charsets_real_ws3270_reply():
    caps = parse_query_reply(bytes([server.AID_SF]) + _REAL_WS3270_CHARSETS)
    assert caps["ge"] is True                       # advertises Graphic Escape
    assert caps["dbcs"] is False
    assert caps["base_cgcsgid"] == 0x02B90025        # base set…
    assert (caps["base_cgcsgid"] & 0xFFFF) == 37     # …CPGID 37 = CP037 (US EBCDIC)
    # The APL/line-drawing graphic set (CP310) that Graphic Escape draws from.
    assert any((cs["cgcsgid"] & 0xFFFF) == 310 and cs["lcid"] == 0xF1
               for cs in caps["char_sets"])


def test_parse_charsets_descriptors():
    caps = parse_query_reply(bytes([server.AID_SF]) + _charsets())
    assert [cs["set"] for cs in caps["char_sets"]] == [0x00, 0x01]
    assert [cs["lcid"] for cs in caps["char_sets"]] == [0x00, 0xF1]
    assert [cs["cgcsgid"] for cs in caps["char_sets"]] == [0x02B90025, 0x03C30136]


def test_parse_charsets_no_graphic_escape():
    # A terminal that does not set the GE flag is reported as not GE-capable.
    caps = parse_query_reply(bytes([server.AID_SF]) + _charsets(ge=False))
    assert caps["ge"] is False


def test_parse_charsets_dbcs_reply():
    # A DBCS reply (DL=11 descriptors, a set with the DBCS flag) is detected.
    dbcs_sets = ((0x00, 0x10, 0x00, 0x02B90025),
                 (0x80, server.CS_DBCS_SET, 0xF8, 0x02B90025))
    caps = parse_query_reply(
        bytes([server.AID_SF]) + _charsets(dbcs=True, sets=dbcs_sets))
    assert caps["dbcs"] is True
    assert [cs["set"] for cs in caps["char_sets"]] == [0x00, 0x80]


def test_parse_charsets_is_defensive_against_a_partial_descriptor():
    # A self-consistent SF whose descriptor region ends with a partial (3-byte)
    # entry must not raise: the loop stops at the last whole descriptor.
    prefix = bytes([0x82, 0x00, 0x09, 0x0C, 0x00, 0x00, 0x00, 0x00, 0x07])  # flags…DL=7
    desc1 = bytes([0x00, 0x10, 0x00]) + (0x02B90025).to_bytes(4, "big")     # whole
    partial = bytes([0x01, 0x00, 0xF1])                                     # no CGCSGID
    sf_body = bytes([server.SF_QUERY_REPLY, server.QR_CHARSETS]) + prefix + desc1 + partial
    length = 2 + len(sf_body)
    rec = bytes([server.AID_SF, (length >> 8) & 0xFF, length & 0xFF]) + sf_body
    caps = parse_query_reply(rec)
    assert caps["char_sets"] == [
        {"set": 0x00, "flags": 0x10, "lcid": 0x00, "cgcsgid": 0x02B90025}]


def test_parse_charsets_absent_leaves_defaults():
    # A reply with no 0x85 payload leaves the char-set fields at their defaults.
    caps = parse_query_reply(_reply_record(color=True))
    assert caps["char_sets"] == [] and caps["ge"] is False
    assert caps["base_cgcsgid"] is None


class _ChunkSocket:
    """A fake socket that hands back preset byte chunks from recv()."""
    def __init__(self, *chunks):
        self._chunks = list(chunks)

    def recv(self, n):
        return self._chunks.pop(0) if self._chunks else b""


def test_read_record_unescapes_iac_and_terminates_on_eor():
    # A record whose data contains 0xFF arrives Telnet-escaped as FF FF; the read
    # must collapse it back to a single 0xFF and stop at the real IAC EOR — the
    # bug that made Query Replies (full of 0xFF colour values) unparseable.
    payload = bytes([0x88, 0x00, 0x06, 0x81, 0x86, 0xFF, 0x00])   # has a data 0xFF
    wire = payload.replace(b"\xff", b"\xff\xff") + bytes([IAC, EOR])
    got = server.read_record(_ChunkSocket(wire))
    assert got == payload


def test_read_record_splices_out_late_telnet_option():
    # A stray option triplet embedded in the stream is removed, not returned.
    wire = bytes([0x88, IAC, DO, BINARY, 0x00, 0x04, 0x81, 0x86, IAC, EOR])
    got = server.read_record(_ChunkSocket(wire))
    assert got == bytes([0x88, 0x00, 0x04, 0x81, 0x86])


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


def test_query_terminal_skips_tn3270e_terminal():
    """A TN3270E terminal is never queried: the DEVICE-TYPE sub-negotiation
    already identified it, and real TN3270E emulators (ws3270) don't answer a
    Read Partition Query — so sending one would only stall the session. No bytes
    are sent and the model is returned unchanged."""
    srv, cli = socket.socketpair()
    try:
        base = server.parse_terminal_type("IBM-3278-2-E")   # extended
        model = server.replace(base, tn3270e=True)          # ...but over TN3270E
        out = server.query_terminal(srv, model)
        assert out is model
        cli.settimeout(0.2)
        try:
            assert cli.recv(16) == b""     # nothing was sent
        except socket.timeout:
            pass
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
        got = cli.recv(64)                       # the WSF Read Partition Query List
        assert got == _iac_escape(read_partition_query_list()) + bytes([IAC, EOR])
        cli.sendall(_reply_record(cols=80, rows=32, color=True)
                    + bytes([IAC, EOR]))
        t.join(timeout=5)
    finally:
        srv.close()
        cli.close()

    m = result["model"]
    assert (m.alt_cols, m.alt_rows) == (80, 32)   # usable area from the reply
    assert m.color                                 # Color Query Reply seen


def test_query_terminal_folds_in_charsets():
    """A terminal's Character Sets reply is decoded onto the model: Graphic Escape
    support, the base CGCSGID, and the descriptor list — so the send path can gate
    GE / code-page / DBCS on real capability instead of guessing."""
    srv, cli = socket.socketpair()
    result = {}

    def run():
        model = server.parse_terminal_type("IBM-3279-2-E")
        result["model"] = server.query_terminal(srv, model)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    try:
        cli.settimeout(5)
        cli.recv(64)                                  # the outbound query
        rec = bytes([server.AID_SF]) + _usable_area(80, 24) + _charsets()
        cli.sendall(rec + bytes([IAC, EOR]))
        t.join(timeout=5)
    finally:
        srv.close()
        cli.close()

    m = result["model"]
    assert m.graphic_escape is True                    # from the 0x85 GE flag
    assert m.dbcs_capable is False
    assert m.base_cgcsgid == 0x02B90025                 # CP037 base set
    assert (0x01, 0x00, 0xF1, 0x03C30136) in m.char_sets   # the APL graphic set
    assert server.QR_CHARSETS in m.query_caps


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
                    reply = {DO: WILL, DONT: WONT, WILL: DO, WONT: DONT}[cmd]
                    if opt == 40:  # refuse TN3270E → exercise the basic TN3270 path
                        reply = {DO: WONT, WILL: DONT}.get(cmd, reply)
                    sock.sendall(bytes([IAC, reply, opt]))
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
        wsf = _read_record(sock, leftover)             # the Read Partition Query List
        assert wsf == _iac_escape(read_partition_query_list())   # IAC EOR stripped
        sock.sendall(_reply_record(cols=80, rows=24) + bytes([IAC, EOR]))
        logon = _read_record(sock)                     # the logon panel
    finally:
        sock.close()
        srv.close()

    assert logon[0] == ERASE_WRITE
    assert "LOGON" in logon.decode("cp037", errors="replace")
