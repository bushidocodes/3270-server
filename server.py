import functools
import os
import socket
import ssl
import binascii
import threading
from dataclasses import dataclass, replace

# The pure 3270 codec primitives (order constants, coded-address packing,
# field-attribute/WCC encoding, EBCDIC conversion, and the per-session
# thread-local they read) live in ds3270 — the leaf module beneath both this
# protocol layer and the render model. They are re-exported here so existing
# call sites and tests (``from server import to_ebcdic`` …) work unchanged.
from ds3270 import (  # noqa: F401  (re-exported)
    DEFAULT_CODE_PAGE,
    DisplayIntensity,
    EOR,
    FieldType,
    IAC,
    IC,
    MAX_ADDRESSABLE_CELLS,
    SBA,
    SF,
    SessionContext,
    _session,
    activate_session,
    aid_to_string,
    current_session,
    encode_pack_addr,
    field_attribute,
    from_ebcdic,
    to_ebcdic,
    write_control_character,
)
# The outbound Write-Structured-Field builders — Read Partition (Query), Set
# Reply Mode, Erase/Reset, the explicit-partition set (#307), Load Programmed
# Symbols (#308) — live in structured_fields, a dependency-free leaf module
# beside ds3270/goca, each a one-liner over the wsf() framing primitive (#353).
# Re-exported here so existing call sites and tests (``from server import
# erase_reset`` …) work unchanged.
from structured_fields import (  # noqa: F401  (re-exported)
    CP_AM_12_14BIT,
    CP_UOM_CELLS,
    CS_BASE,
    ER_ALTERNATE,
    ER_DEFAULT,
    LPS_FLAG_CLEAR,
    LPS_FLAG_EXTENDED,
    LPS_FLAG_SKIP,
    LPS_TYPE1,
    LPS_TYPE2,
    LPS_TYPE3,
    LPS_TYPE5,
    LPS_VECTOR,
    ODS_ERASE_ALL_UNPROTECTED,
    ODS_ERASE_WRITE,
    ODS_ERASE_WRITE_ALTERNATE,
    ODS_WRITE,
    RM_CHARACTER,
    RM_EXTENDED_FIELD,
    RM_FIELD,
    SA_ORDER,
    SF_ACTIVATE_PARTITION,
    SF_CREATE_PARTITION,
    SF_DESTROY_PARTITION,
    SF_ERASE_RESET,
    SF_LOAD_PS,
    SF_OUTBOUND_3270DS,
    SF_READ_PARTITION,
    SF_RP_QLIST,
    SF_RP_QUERY,
    SF_RPQ_ALL,
    SF_SET_REPLY_MODE,
    WSF,
    XA_CHARSET,
    activate_partition,
    create_partition,
    destroy_partition,
    erase_reset,
    load_programmed_symbols,
    outbound_3270ds,
    read_partition_query,
    read_partition_query_list,
    select_char_set,
    set_reply_mode,
    wsf,
)
# The TSO/ISPF *application* — the logon flow, the ISPF menu loops, the panel
# behaviours and their selection routing — lives in session.py (#351), driven
# through the Transport port (see SocketTransport below). session imports DOWN
# into screen/dtl/ds3270 (never into this module), so this import is cycle-free.
import session

# Telnet commands (used by the TN3270E negotiation and the framing layer below).
SE = 240
SB = 250
WILL, WONT, DO, DONT = 251, 252, 253, 254

# ── TN3270E (RFC 2355) ───────────────────────────────────────────────────────
# TN3270E is basic TN3270 plus a negotiated Telnet option (40) that adds a
# DEVICE-TYPE / FUNCTIONS sub-negotiation and a 5-byte header on every 3270 data
# message. When it is negotiated the session socket is wrapped in TN3270EStream,
# which adds that header outbound (read_record strips it inbound), so the rest of
# the server sends and reads plain 3270 records unchanged.
TN3270E = 40  # Telnet option number
# START-TLS: the negotiated (in-band) TLS upgrade Telnet option. Unlike implicit
# TLS (the L: prefix / make_tls_context), the session begins in the clear on the
# normal port and is upgraded to TLS mid-stream (see _offer_starttls).
TELOPT_STARTTLS = 46
TLS_FOLLOWS = 1   # START-TLS sub-command: the TLS handshake data follows the SE

# TN3270E message sub-commands (RFC 2355 §3).
E_ASSOCIATE = 0
E_CONNECT = 1
E_DEVICE_TYPE = 2
E_FUNCTIONS = 3
E_IS = 4
E_REASON = 5
E_REJECT = 6
E_REQUEST = 7
E_SEND = 8

# Telnet commands used for the 3270 SYSREQ and ATTN keys (RFC 2355 §10.5, §11):
# a client maps SYSREQ to Abort Output and ATTN to Interrupt Process.
AO = 245   # Abort Output  → the SYSREQ key
IP = 244   # Interrupt Process → the ATTN key

# TN3270E DATA-TYPE header byte values (header byte 0).
E_DT_3270_DATA = 0x00
E_DT_RESPONSE = 0x02
E_DT_BIND_IMAGE = 0x03     # the SNA BIND image (binds the LU-LU session)
E_DT_UNBIND = 0x04
E_DT_SSCP_LU_DATA = 0x07   # the SSCP-LU session (used in SYSREQ "suspended" mode)
E_DT_BID = 0x09            # CONTENTION-RESOLUTION: client bids to send (unused, see below)

# REQUEST-FLAG (header byte 1). With the CONTENTION-RESOLUTION function the
# SEND-DATA bit grants the client permission to send on the 3270 session; a
# client that receives a 3270-DATA record without it must BID and wait before
# sending. We always grant it (send-a-screen-then-read), so a BID never occurs.
E_RQF_SEND_DATA = 0x01

# RESPONSE-FLAG (header byte 2). Outbound it asks whether the client should
# acknowledge; inbound (on a RESPONSE message) it says whether the ack is good.
E_RSF_NO_RESPONSE = 0x00
E_RSF_ERROR_RESPONSE = 0x01
E_RSF_ALWAYS_RESPONSE = 0x02
E_RSF_POSITIVE = 0x00     # inbound: positive response (record processed)
E_RSF_NEGATIVE = 0x01     # inbound: negative response (error; body = sense code)

# How many times a NAK'd (negatively-acknowledged) record is retransmitted before
# the server gives up — a small cap so a persistently-failing screen can't loop.
_RESPONSE_MAX_RETRIES = 2
# Upper bound on outstanding (unacknowledged) records tracked for retransmission,
# so a client that stops sending responses can't grow the map without limit.
_RESPONSE_PENDING_CAP = 128

# TN3270E FUNCTIONS (negotiable capabilities). We support BIND-IMAGE (so the
# session can be bound, which enables the ATTN key), RESPONSES, SYSREQ, and
# CONTENTION-RESOLUTION (the half-duplex send-permission handshake).
E_FUNC_BIND_IMAGE = 0
E_FUNC_DATA_STREAM_CTL = 1
E_FUNC_RESPONSES = 2
E_FUNC_SCS_CTL_CODES = 3
E_FUNC_SYSREQ = 4
E_FUNC_CONTENTION_RESOLUTION = 5
E_SUPPORTED_FUNCTIONS = frozenset({
    E_FUNC_BIND_IMAGE, E_FUNC_RESPONSES, E_FUNC_SYSREQ,
    E_FUNC_CONTENTION_RESOLUTION,
})


# A synthetic SNA BIND image for the LU-LU session, sent (DATA-TYPE BIND-IMAGE)
# once BIND-IMAGE is negotiated. We are not a real SNA gateway, so this is a
# well-formed, conventional LU2 (3270 display) BIND that x3270 parses happily.
# The byte at offset 24 is the screen-size code: 0x03 = "default 24x80,
# alternate = the terminal's own maximum" — model-agnostic, so it never shrinks
# a model 3/4/5 alternate screen (as a fixed 24x80 code would). Offset 27 is the
# PLU (application) name length; the name follows in EBCDIC. See RFC 2355 §10.3
# and the x3270 BIND parser (3270ds.h BIND_OFF_*, telnet.c process_bind).
_BIND_IMAGE = bytes([
    0x31,                                            # 0: BIND request code
    0x01, 0x03, 0x03, 0xB1, 0x90, 0x30, 0x80, 0x00,  # 1-8: FM/TS profiles, protocols
    0x02, 0x85, 0x87,                                # 9-11: pacing, max RU sec/pri
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,  # 12-19
    0x18, 0x50, 0x18, 0x50,                          # 20-23: RD/CD/RA/CA (24x80)
    0x03,                                            # 24: screen-size code
    0x00, 0x00,                                      # 25-26
    0x08,                                            # 27: PLU name length
]) + to_ebcdic("IBMTSO01") + bytes([0x00])           # 28-35: PLU name, then a pad
# byte so buflen strictly exceeds offset 28 + name length (x3270 needs the '>').


def _strip_leading_telnet(rec: bytes) -> bytes:
    """Drop any leading Telnet command sequences from a framed record.

    A client's option replies can still be arriving as negotiation ends and land
    ahead of the first data record (they carry no ``IAC EOR``, so they frame
    together with the record that follows). They are already-agreed replies to our
    own offer, so we simply skip them: a triplet (``IAC WILL/WONT/DO/DONT opt``)
    is 3 bytes, an ``IAC SB … IAC SE`` runs to its ``SE``. A real 3270 record
    begins with a header byte (not ``IAC``), so this is a no-op for normal data.
    """
    WILL, WONT, DO, DONT = 251, 252, 253, 254
    i = 0
    while i < len(rec) and rec[i] == IAC:
        if i + 1 >= len(rec):
            break
        c = rec[i + 1]
        if c in (WILL, WONT, DO, DONT):
            i += 3
        elif c == SB:
            se = rec.find(bytes([IAC, SE]), i + 2)
            i = se + 2 if se != -1 else len(rec)
        else:
            i += 2
    return rec[i:]


class TN3270EStream:
    """A socket wrapper that frames outbound records with the TN3270E 5-byte
    data header, so screen-sending code needn't know TN3270E is in effect.

    When the RESPONSES function was negotiated, each outbound record asks the
    client to acknowledge it (RESPONSE-FLAG = ALWAYS) under an incrementing
    sequence number, and the client's RESPONSE messages are consumed and logged
    here (see :meth:`on_response`, driven from :func:`read_record`) rather than
    being mistaken for screen input. Inbound header stripping happens in
    ``read_record``, which knows where a record ends (at IAC EOR).
    """

    def __init__(self, sock, responses=False, sysreq=False, bind_image=False,
                 contention=False):
        self._sock = sock
        self.responses = responses   # RESPONSES function negotiated?
        self.sysreq = sysreq         # SYSREQ function negotiated?
        self.bind_image = bind_image  # BIND-IMAGE function negotiated?
        self.contention = contention  # CONTENTION-RESOLUTION function negotiated?
        self.bound = False           # has a BIND-IMAGE been sent (session bound)?
        self._seq = 0                # outbound sequence number (mod 2^16)
        self.last_response = None    # (seq, positive_bool, code) of the last ack
        self._rxbuf = bytearray()    # inbound bytes not yet framed into records
        self.last_screen = None      # last 3270-DATA record sent (for redisplay)
        # Records sent under RESPONSES and not yet acknowledged: seq -> (data,
        # retries). A positive response prunes its entry; a negative one
        # retransmits it (up to _RESPONSE_MAX_RETRIES) so a NAK'd screen recovers.
        self._pending = {}

    def sendall(self, data):
        self.last_screen = data      # remember it so SYSREQ resume can redisplay
        self._send_3270(data, retries=0)

    def _send_3270(self, data, retries):
        """Frame and send one 3270-DATA record. Under RESPONSES it carries a fresh
        sequence and is tracked in ``_pending`` for possible retransmission."""
        # REQUEST-FLAG: with CONTENTION-RESOLUTION we grant the client permission
        # to send on the 3270 session (SEND-DATA) with every screen, so it never
        # has to bid before returning input.
        request_flag = E_RQF_SEND_DATA if self.contention else 0x00
        if self.responses:
            # Ask the client to acknowledge this record under a fresh sequence.
            self._seq = (self._seq + 1) & 0xFFFF
            header = bytes([E_DT_3270_DATA, request_flag, E_RSF_ALWAYS_RESPONSE,
                            (self._seq >> 8) & 0xFF, self._seq & 0xFF])
            self._pending[self._seq] = (data, retries)
            if len(self._pending) > _RESPONSE_PENDING_CAP:   # bound the memory
                self._pending.pop(next(iter(self._pending)))
        else:
            # No RESPONSES function: no response wanted, sequence number 0.
            header = bytes([E_DT_3270_DATA, request_flag, E_RSF_NO_RESPONSE, 0x00, 0x00])
        # RFC 2355 §3.5: the header does not change the IAC rules — any 0xFF in
        # it must be doubled. The SEQ-NUMBER bytes deterministically pass through
        # 0xFF (seq 0x00FF, and 0xFFEF is literally IAC EOR mid-header). ``data``
        # is already escaped and IAC-EOR-terminated by the caller, so only the
        # header needs escaping here.
        self._sock.sendall(_iac_escape(header) + data)

    def send_bind(self, image=_BIND_IMAGE):
        """Send the SNA BIND image (DATA-TYPE BIND-IMAGE), binding the LU-LU
        session. Required once BIND-IMAGE is negotiated: the client won't accept
        3270-DATA until it has seen a BIND (RFC 2355 §10.3), and being bound is
        what lets the client's ATTN key send its Telnet IP. Idempotent."""
        header = bytes([E_DT_BIND_IMAGE, 0x00, E_RSF_NO_RESPONSE, 0x00, 0x00])
        self._sock.sendall(_iac_escape(header + image) + bytes([IAC, EOR]))
        self.bound = True

    def send_sscp(self, text):
        """Send an SSCP-LU-DATA message (unformatted EBCDIC text) — used while the
        SYSREQ key has put the session in the SSCP-LU (suspended) mode."""
        header = bytes([E_DT_SSCP_LU_DATA, 0x00, E_RSF_NO_RESPONSE, 0x00, 0x00])
        self._sock.sendall(_iac_escape(header + to_ebcdic(text)) + bytes([IAC, EOR]))

    def send_unbind(self, reason=0x01):
        """Tell the client the session has ended (DATA-TYPE UNBIND). 0x01 is
        'normal end of session' (RFC 2355 §10.3)."""
        self._sock.sendall(_iac_escape(bytes([E_DT_UNBIND, 0x00, E_RSF_NO_RESPONSE,
                                              0x00, 0x00, reason]))
                           + bytes([IAC, EOR]))
        self.bound = False

    def redisplay(self):
        """Re-send the last 3270 screen (used to restore the panel after the
        SYSREQ SSCP-LU session is left)."""
        if self.last_screen is not None:
            self.sendall(self.last_screen)

    def on_response(self, payload: bytes):
        """Handle a client RESPONSE message (already includes its 5-byte header).
        Byte 2 is positive/negative; bytes 3-4 the acked sequence number; any
        body byte is the sense/response code.

        A positive response acknowledges the record — drop it from ``_pending``.
        A negative response (the client couldn't process the screen — a
        data-stream error) **retransmits** that record under a fresh sequence, up
        to :data:`_RESPONSE_MAX_RETRIES` times, so the session recovers instead of
        being left out of sync. Beyond the cap we give up rather than loop."""
        positive = len(payload) > 2 and payload[2] == E_RSF_POSITIVE
        seq = (payload[3] << 8 | payload[4]) if len(payload) >= 5 else None
        code = payload[5] if len(payload) >= 6 else None
        self.last_response = (seq, positive, code)
        kind = "positive" if positive else "negative"
        print(f"TN3270E {kind} response for seq {seq}"
              + (f", code {hex(code)}" if code is not None else ""))

        entry = self._pending.pop(seq, None) if seq is not None else None
        if positive or entry is None:
            return   # acknowledged, or an unknown/already-recovered sequence
        data, retries = entry
        if retries >= _RESPONSE_MAX_RETRIES:
            print(f"TN3270E: giving up on seq {seq} after {retries} retransmit(s)")
            return
        print(f"TN3270E: retransmitting seq {seq} (attempt {retries + 1})")
        self._send_3270(data, retries + 1)

    def next_event(self):
        """Return the next inbound event, or ``None`` on disconnect:

        - ``("record", data_type, payload)`` — one framed 3270 record (its 5-byte
          header parsed off), with RESPONSE acknowledgements consumed/logged here
          so they are never mistaken for input;
        - ``("sysreq",)`` — the client pressed SYSREQ (Telnet AO);
        - ``("attn",)`` — the client pressed ATTN (Telnet IP).
        """
        while True:
            unit = self._next_unit()
            if unit is None:
                return None
            kind, data = unit
            if kind == "cmd":
                if data == AO:
                    return ("sysreq",)
                if data == IP:
                    return ("attn",)
                continue                      # other standalone command: ignore
            rec = _strip_leading_telnet(data)  # drop any late negotiation bytes
            if not rec:
                continue
            if rec[:1] == bytes([E_DT_RESPONSE]):
                self.on_response(rec)
                continue
            return ("record", rec[0], rec[5:])  # data-type, header stripped

    def next_data_record(self):
        """The payload of the next inbound 3270 record (SYSREQ/ATTN skipped), or
        ``None`` — used by :func:`read_record` (e.g. the Query reply)."""
        while True:
            ev = self.next_event()
            if ev is None:
                return None
            if ev[0] == "record":
                return ev[2]

    def _next_unit(self):
        """One framing unit from ``_rxbuf``: ``("record", bytes-before-IAC-EOR)``
        or ``("cmd", command-byte)`` for a standalone Telnet command (AO/IP/…).
        Processes the Telnet layer — IAC IAC is data, IAC EOR ends a record,
        option triplets and SB…SE are spliced out — reading as needed."""
        while True:
            buf = self._rxbuf
            i = 0
            while i < len(buf):
                if buf[i] != IAC:
                    i += 1
                    continue
                if i + 1 >= len(buf):
                    break                        # need the command byte
                c = buf[i + 1]
                if c == EOR:
                    rec = bytes(buf[:i])
                    del buf[:i + 2]
                    return ("record", rec)
                if c == IAC:                     # escaped 0xFF → data
                    i += 2
                    continue
                if c in (WILL, WONT, DO, DONT):  # a mid-session option: splice out
                    del buf[i:i + 3]
                    continue
                if c == SB:
                    se = buf.find(bytes([IAC, SE]), i + 2)
                    if se == -1:
                        break                    # incomplete SB; wait for more
                    del buf[i:se + 2]
                    continue
                del buf[:i + 2]                  # a standalone command (AO/IP/…)
                return ("cmd", c)
            if len(buf) > MAX_BUFFER_SIZE:
                print(f"WARNING: client buffer exceeded {MAX_BUFFER_SIZE} bytes; closing connection")
                return None
            chunk = self._sock.recv(1024)
            if not chunk:
                return None
            buf.extend(chunk)

    def recv(self, n):
        return self._sock.recv(n)

    def settimeout(self, t):
        self._sock.settimeout(t)

    def close(self):
        self._sock.close()


# The low-level building blocks above (encode_pack_addr, field_attribute,
# write_control_character, the order constants) are consumed by screen.py, which
# provides the Screen/Field model that the panels render through. Screens are
# authored declaratively in panels/*.dtl and loaded via dtl.load_panel() — see
# session.send_tso_logon / session.send_ispf_menu (#351).


# ── terminal models ──────────────────────────────────────────────────────────
# The client tells us its device type during Telnet TERMINAL-TYPE negotiation
# (e.g. "IBM-3278-2", "IBM-3279-4-E"). Every 3270 model shares a 24x80 *default*
# presentation space — that is what ERASE_WRITE (0xF5) selects, and it is what
# all the bundled panels are authored for, so they render identically on every
# model. The model number only enlarges the *alternate* space, which is selected
# by ERASE/WRITE ALTERNATE (0x7E); using it (to lay panels out across a model
# 3/4/5's extra rows/columns) is a separate, larger piece of work. For now we
# detect and record the negotiated model so the session knows what it is talking
# to, exposes it as an ISPF dialog variable (ZTERM), and is ready for wide-panel
# support without guessing the geometry later.

# Model number -> alternate-screen (rows, cols). The default space is always
# 24x80; models 3/4/5 differ only in their alternate size.
_MODEL_DIMENSIONS = {
    2: (24, 80),
    3: (32, 80),
    4: (43, 80),
    5: (27, 132),
}


@dataclass(frozen=True)
class TerminalModel:
    """A negotiated 3270 terminal model, parsed from its TERMINAL-TYPE string.

    ``default_rows``/``default_cols`` are the 24x80 space every model shares and
    that the panels currently render on; ``alt_rows``/``alt_cols`` are the
    model's larger alternate space (equal to the default for model 2).
    """

    term_type: str          # the negotiated string, e.g. "IBM-3278-2"
    model: int              # 2..5 (2 for unknown / IBM-DYNAMIC until Query lands)
    alt_rows: int
    alt_cols: int
    extended: bool = False  # "-E" suffix: extended data stream (color/query/…)
    color: bool = False     # 3279-family device: colour-capable
    default_rows: int = 24
    default_cols: int = 80
    tn3270e: bool = False            # TN3270E (RFC 2355) negotiated for this session
    tn3270e_responses: bool = False  # TN3270E RESPONSES function agreed
    tn3270e_sysreq: bool = False     # TN3270E SYSREQ function agreed
    tn3270e_bind_image: bool = False  # TN3270E BIND-IMAGE function agreed
    tn3270e_contention: bool = False  # TN3270E CONTENTION-RESOLUTION function agreed
    # QCODEs the terminal advertised in its Query Reply (basic-TN3270 -E only),
    # e.g. {0x81, 0x85, 0x86, 0x87}. Empty when no Query was answered.
    query_caps: frozenset = frozenset()
    # The active inbound reply mode (#112): RM_FIELD (the default — text only),
    # RM_EXTENDED_FIELD or RM_CHARACTER (modified fields carry extended attributes
    # as SA orders). Raised by request_reply_mode when the terminal advertises
    # QR_REPLY_MODES; stays RM_FIELD otherwise (and always under TN3270E, where the
    # Query is unanswered).
    reply_mode: int = 0     # RM_FIELD
    # Decoded from the Character Sets Query Reply (0x85) payload, so the send path
    # can gate features on what the terminal actually supports (see query_terminal).
    graphic_escape: bool = False    # advertises the alternate graphic (GE) set
    dbcs_capable: bool = False       # advertises a double-byte (DBCS) set
    base_cgcsgid: int = None         # base set's CGCSGID (GCSGID<<16 | CPGID), or None
    # The explicit partition the host has made active (#307). 0 is the implicit
    # partition every session starts in; a Create/Activate Partition SF moves it.
    # This is the context (GA23-0059 "INPID") that identifies which partition an
    # inbound AID reply came from — a standard reply carries no inline partition id.
    active_partition: int = 0
    char_sets: tuple = ()            # ((set, flags, lcid, cgcsgid), …) descriptors
    # The client refused the 3270 binary framing — a line-mode (NVT/ASCII) client,
    # not a 3270 terminal. The session runs as a plain-ASCII TSO READY loop.
    nvt: bool = False


def parse_terminal_type(term_type: str) -> TerminalModel:
    """Classify a Telnet TERMINAL-TYPE string into a :class:`TerminalModel`.

    Understands the IBM ``IBM-<device>-<model>[-E]`` convention (3278 mono /
    3279 colour, models 2–5) and ``IBM-DYNAMIC``. Anything unrecognised — an
    empty string, ``IBM-DYNAMIC`` (whose real size comes from a Query Reply we
    do not yet implement), or a model outside 2–5 — falls back to model 2, the
    universal 24x80 baseline, so the session always has a usable geometry.
    """
    t = (term_type or "").strip().upper()
    extended = t.endswith("-E")
    core = t[:-2] if extended else t
    parts = core.split("-")
    device = parts[1] if len(parts) > 1 else ""
    # Colour-capable if a 3279 (colour device) or any extended-data-stream (-E)
    # terminal: -E means it accepts extended field attributes (colour/highlight),
    # and modern emulators report 3278-...-E yet display colour and don't answer a
    # Read Partition Query — so -E is the capability signal we have.
    color = device == "3279" or extended
    model = 2
    if len(parts) > 2 and parts[2].isdigit() and int(parts[2]) in _MODEL_DIMENSIONS:
        model = int(parts[2])
    rows, cols = _MODEL_DIMENSIONS[model]
    return TerminalModel(
        term_type=t or "IBM-3278-2",
        model=model,
        alt_rows=rows,
        alt_cols=cols,
        extended=extended,
        color=color,
    )


# ── structured fields: Query / Query Reply ───────────────────────────────────
# The TERMINAL-TYPE string is only a hint. The authoritative way for a host to
# learn a terminal's real geometry and capabilities is the Query: the host sends
# a Read Partition (Query) structured field (built in structured_fields, #353),
# and the terminal answers (inbound AID 0x88) with a set of Query Reply
# structured fields describing its usable area, colour, highlighting, and more.
# This is also how an IBM-DYNAMIC terminal reports the size the type string
# can't. We fold the reply back into the TerminalModel so the session knows the
# terminal's true size and colour support.

SF_QUERY_REPLY = 0x81       # structured-field id: Query Reply (inbound)
AID_SF = 0x88               # inbound AID that introduces Query Reply data
AID_CURSOR_SELECT = 0x7E    # inbound AID from the selector-pen / Cursor Select key (#104)
# 3270 selector-pen / cursor-select designator characters (the first byte of a
# detectable field). '?'/'>' are *selection* designators (deferred: cursor-select
# toggles ? <-> > and the field's MDT locally, and the modified '>' fields are read
# on the next Enter); ' '/'&' are *attention* designators (immediate: cursor-select
# sends the Cursor Select AID and a read-modified straight away). See #104.

# Query Reply codes (QCODE) we recognise (3270ds.h QR_*).
QR_SUMMARY = 0x80           # lists every QCODE the terminal supports
QR_USABLE_AREA = 0x81
QR_ALPHA_PART = 0x84        # alphanumeric partitions
QR_CHARSETS = 0x85          # character sets
QR_COLOR = 0x86
QR_HIGHLIGHT = 0x87         # (extended) highlighting
QR_REPLY_MODES = 0x88

# Character Sets Query Reply (0x85) FLAGS byte (GA23-0059 Character Sets QR).
CS_FLAG_GE = 0x80          # the alternate graphic (Graphic Escape) set is present
CS_FLAG_DBCS = 0x04        # a double-byte (DBCS) character set is present
CS_FLAG_CGCSGID = 0x02     # each descriptor carries a 4-byte CGCSGID
CS_DBCS_SET = 0x20         # per-descriptor FLAGS bit: this set is double-byte


def _iac_escape(data: bytes) -> bytes:
    """Double every IAC (0xFF) so the Telnet layer carries it as data rather
    than a command. The Read Partition query's partition byte is 0xFF, so an
    unescaped query is mis-framed by the terminal (it was, silently, until now)."""
    return data.replace(b"\xff", b"\xff\xff")


# Set Reply Mode (#112) is requested by default (below) for terminals that
# advertise it; the structured field itself is built in structured_fields.
_DEFAULT_REPLY_MODE = RM_CHARACTER


def request_reply_mode(client_socket, model: "TerminalModel",
                       mode: int = _DEFAULT_REPLY_MODE, attrs=()) -> "TerminalModel":
    """Ask the terminal to switch to reply ``mode`` so its Read Modified replies
    carry extended attributes, and record the active mode on the returned model.

    Sent only when the terminal advertised **Reply Modes** in its Query Reply
    (``QR_REPLY_MODES`` in ``query_caps``) — i.e. basic-TN3270 ``-E`` terminals;
    a TN3270E session never answers the Query, so it stays in Field mode. A terminal
    that doesn't support the mode is left in Field mode (``model`` unchanged)."""
    if QR_REPLY_MODES not in model.query_caps or mode == RM_FIELD:
        return model
    record = _iac_escape(set_reply_mode(mode, attrs)) + bytes([IAC, EOR])
    try:
        print("TX:", binascii.hexlify(record))
        client_socket.sendall(record)
    except OSError:
        return model
    return replace(model, reply_mode=mode)


# ── capability gates for the opt-in structured fields ────────────────────────
# The SF builders themselves live in structured_fields (#353); these
# session-level gates stay here, beside the Query Reply capabilities they read.

def partitions_supported(model: "TerminalModel") -> bool:
    """Whether the terminal advertised **Alphanumeric Partitions** (QR_ALPHA_PART,
    0x84) in its Query Reply — the gate for sending any explicit-partition SF. A
    terminal that doesn't advertise partitions can't be split (#307)."""
    return QR_ALPHA_PART in model.query_caps


def inbound_partition(model: "TerminalModel") -> int:
    """The partition an inbound AID reply is associated with: the host's active
    partition (GA23-0059 INPID). A standard 3270 reply carries no inline partition
    id, so — as the reference specifies — the originating partition is the one the
    host had activated when it issued the read, tracked on the model (#307)."""
    return model.active_partition


def programmed_symbols_supported(model: "TerminalModel") -> bool:
    """Whether the terminal advertised **Character Sets** (QR_CHARSETS, 0x85) in
    its Query Reply — the gate for loading programmed symbols. (A finer check would
    require the Character Sets reply's loadable-set flag; QR_CHARSETS is the gate the
    reference names and what this server parses.) Opt-in (#308)."""
    return QR_CHARSETS in model.query_caps


def parse_query_reply(record: bytes) -> dict:
    """Parse an inbound Query Reply record into a capabilities dict.

    ``record`` is the payload between the inbound AID and the IAC EOR (an AID
    0x88 followed by a run of ``[len_hi][len_lo][0x81][qcode][payload…]``
    structured fields; each ``len`` counts itself). Returns ``{qcodes,
    usable_rows, usable_cols, color, highlight, charsets, reply_modes}``.

    ``qcodes`` is the full set the terminal supports — the union of the QCODEs it
    returned *and* the ones the **Summary** reply (0x80) enumerates, since a
    terminal advertises many capabilities (e.g. highlighting) only in the Summary
    rather than as a standalone reply. The boolean flags are derived from that
    set. Malformed or truncated fields are skipped defensively.
    """
    caps = {"qcodes": set(), "usable_rows": None, "usable_cols": None,
            "color": False, "highlight": False, "charsets": False,
            "reply_modes": False,
            # Filled from the Character Sets (0x85) reply payload, when present:
            "char_sets": [], "ge": False, "dbcs": False, "base_cgcsgid": None}
    if not record or record[0] != AID_SF:
        return caps
    i = 1
    while i + 2 <= len(record):
        length = (record[i] << 8) | record[i + 1]
        if length < 4 or i + length > len(record):
            break
        sf = record[i:i + length]
        i += length
        if sf[2] != SF_QUERY_REPLY:
            continue
        qcode = sf[3]
        caps["qcodes"].add(qcode)
        if qcode == QR_SUMMARY:
            # The Summary payload is the list of supported QCODEs.
            caps["qcodes"].update(sf[4:])
        elif qcode == QR_USABLE_AREA and len(sf) >= 10:
            # sf[4],sf[5] = flags; sf[6:8] = width (cols); sf[8:10] = height (rows)
            caps["usable_cols"] = (sf[6] << 8) | sf[7]
            caps["usable_rows"] = (sf[8] << 8) | sf[9]
        elif qcode == QR_CHARSETS and len(sf) >= 13:
            # Character Sets reply: sf[4]=FLAGS, sf[5]=more-flags, sf[6:8]=SDW/SDH,
            # sf[8:12]=load-PS format types, sf[12]=DL (descriptor length), then a
            # list of DL-byte descriptors: SET, FLAGS, LCID, [DBCS: SW SH SUBSN
            # SUBSN,] CGCSGID(4). See x3270 do_qr_charsets (Common/sf.c).
            flags = sf[4]
            caps["ge"] = bool(flags & CS_FLAG_GE)
            has_cgcsgid = bool(flags & CS_FLAG_CGCSGID)
            dl = sf[12]
            pos = 13
            while dl >= 3 and pos + dl <= len(sf):
                d = sf[pos:pos + dl]
                cgcsgid = int.from_bytes(d[dl - 4:dl], "big") \
                    if (has_cgcsgid and dl >= 7) else None
                caps["char_sets"].append(
                    {"set": d[0], "flags": d[1], "lcid": d[2], "cgcsgid": cgcsgid})
                pos += dl
            caps["dbcs"] = bool(flags & CS_FLAG_DBCS) or \
                any(e["flags"] & CS_DBCS_SET for e in caps["char_sets"])
            base = next((e for e in caps["char_sets"] if e["set"] == 0x00), None)
            caps["base_cgcsgid"] = base["cgcsgid"] if base else None
    # Derive capability flags from the union of returned + Summary-listed QCODEs.
    caps["color"] = QR_COLOR in caps["qcodes"]
    caps["highlight"] = QR_HIGHLIGHT in caps["qcodes"]
    caps["charsets"] = QR_CHARSETS in caps["qcodes"]
    caps["reply_modes"] = QR_REPLY_MODES in caps["qcodes"]
    return caps


# CPGID (the low 16 bits of a CGCSGID) → Python EBCDIC codec, for the pages we can
# actually render. A terminal's discovered base character set (see
# base_cgcsgid / #137) selects the session code page; an unknown or unsupported
# page falls back to the US default rather than guessing wrong. Only codecs that
# ship with Python are listed — an entry we can't encode/decode is worse than the
# default. (ws3270 -charset german reports CPGID 273, french 297, etc.)
_CPGID_TO_CODEC = {
    37: "cp037",       # US / Canada (the default)
    273: "cp273",      # Austria / Germany
    500: "cp500",      # International / Belgium / Switzerland
    1140: "cp1140",    # US / Canada with euro
}


def code_page_for_model(model) -> str:
    """The EBCDIC codec to use for a session, chosen from the terminal's discovered
    base character set (CPGID) when we support it, else the cp037 default."""
    if model is not None and model.base_cgcsgid:
        codec = _CPGID_TO_CODEC.get(model.base_cgcsgid & 0xFFFF)
        if codec is not None:
            return codec
    return DEFAULT_CODE_PAGE


# How long to wait for a Query Reply before giving up. A terminal that answers
# does so at once; one that ignores the Query must not stall the session — the
# negotiation's 60s timeout would.
QUERY_REPLY_TIMEOUT = 2.0


def query_terminal(client_socket, model: "TerminalModel") -> "TerminalModel":
    """Ask an extended-data-stream terminal to describe itself and fold the
    reply into ``model``.

    Sent only for **basic-TN3270** extended (``-E``) terminals. Under TN3270E the
    DEVICE-TYPE sub-negotiation has already identified the terminal, and real
    TN3270E emulators (e.g. ws3270) do not answer a Read Partition Query — sending
    one there only stalls the session — so it is skipped. We also wait only
    briefly for the reply (:data:`QUERY_REPLY_TIMEOUT`) so a base terminal that
    ignores the Query can't block.

    A **Query List (All)** is sent so the terminal enumerates its whole
    capability set (see :func:`read_partition_query_list`). The reply's usable
    area becomes the model's authoritative alternate size, the advertised QCODEs
    are recorded on the model (``query_caps``), and colour support is taken from
    the reply — so a terminal that does *not* report colour overrides the
    type-string guess. On any error, silence, or non-reply, ``model`` is
    returned unchanged.
    """
    if not model.extended or model.tn3270e:
        return model
    # Frame the WSF as a 3270 record: IAC-escape it (the partition byte is 0xFF,
    # which the Telnet layer would otherwise read as a command) and terminate it
    # with IAC EOR the way every outbound screen is.
    query = _iac_escape(read_partition_query_list()) + bytes([IAC, EOR])
    try:
        print("TX:", binascii.hexlify(query))
        client_socket.settimeout(QUERY_REPLY_TIMEOUT)
        client_socket.sendall(query)
        record = read_record(client_socket)
    except OSError:
        return model
    if not record:
        return model
    caps = parse_query_reply(record)
    base_cgcsgid = ("0x{:08x}".format(caps["base_cgcsgid"])
                    if caps["base_cgcsgid"] is not None else None)
    print("Query Reply: qcodes={}, usable={}x{}, color={}, highlight={}, "
          "charsets={}, reply_modes={}, ge={}, dbcs={}, base_cgcsgid={}".format(
              sorted(hex(q) for q in caps["qcodes"]),
              caps["usable_cols"], caps["usable_rows"],
              caps["color"], caps["highlight"],
              caps["charsets"], caps["reply_modes"],
              caps["ge"], caps["dbcs"], base_cgcsgid))
    updates = {"query_caps": frozenset(caps["qcodes"])}
    if caps["usable_cols"] and caps["usable_rows"]:
        if caps["usable_cols"] * caps["usable_rows"] <= MAX_ADDRESSABLE_CELLS:
            updates["alt_cols"] = caps["usable_cols"]
            updates["alt_rows"] = caps["usable_rows"]
        else:
            # #348: an -oversize terminal (e.g. 132x60 = 7920 cells) reports a
            # usable area beyond what 12-bit coded addressing can reach; folding
            # it in unchecked made the first full-screen render raise (or, for
            # 4032-4095 cells, put a raw IAC on the wire). Keep the type-string
            # geometry — the largest model size we know the terminal supports —
            # so the session stays alive on a screen we can actually address.
            print("Query Reply: usable area {}x{} exceeds 12-bit addressing "
                  "({} > {} cells) — keeping {}x{}".format(
                      caps["usable_cols"], caps["usable_rows"],
                      caps["usable_cols"] * caps["usable_rows"],
                      MAX_ADDRESSABLE_CELLS, model.alt_cols, model.alt_rows))
    # The reply is authoritative for colour: a terminal that answers but does not
    # advertise Colour is mono, even if its type string looked extended.
    updates["color"] = caps["color"]
    # Character-set discovery: record what the terminal actually supports so the
    # send path can gate Graphic Escape / alternate code page / DBCS on it,
    # rather than emitting those blind (only present when the 0x85 reply carried
    # a payload).
    if caps["char_sets"]:
        updates["graphic_escape"] = caps["ge"]
        updates["dbcs_capable"] = caps["dbcs"]
        updates["base_cgcsgid"] = caps["base_cgcsgid"]
        updates["char_sets"] = tuple(
            (e["set"], e["flags"], e["lcid"], e["cgcsgid"])
            for e in caps["char_sets"])
    return replace(model, **updates)


MAX_BUFFER_SIZE = 65536  # 64 KiB; a legitimate 3270 data stream is at most a few KB


def read_record(client_socket):
    """Read one IAC-EOR-terminated 3270 record and return its payload (the bytes
    before the terminator), or ``None`` on disconnect or oversize.

    Shared by :func:`read_client_input` (AID replies) and :func:`query_terminal`
    (structured-field Query replies), which decode the payload differently.
    """
    # In TN3270E the wrapper frames records (one at a time, handling headers and
    # RESPONSE acknowledgements) since records can pipeline.
    if isinstance(client_socket, TN3270EStream):
        return client_socket.next_data_record()

    # Process the Telnet layer while accumulating: IAC IAC is a literal 0xFF data
    # byte (collapse it — a Query Reply's colour/partition values are full of
    # them), IAC EOR ends the record, and a late option triplet or standalone
    # command is spliced out. (The old "buffer ends with IAC EOR" shortcut both
    # failed to un-escape and could false-terminate on an escaped 0xFF.)
    buffer = bytearray()
    while True:
        out = bytearray()
        i = 0
        while i < len(buffer):
            b = buffer[i]
            if b != IAC:
                out.append(b)
                i += 1
                continue
            if i + 1 >= len(buffer):
                break                          # trailing lone IAC: need more bytes
            c = buffer[i + 1]
            if c == EOR:
                return bytes(out)              # record complete
            if c == IAC:
                out.append(IAC)                # IAC IAC → one 0xFF data byte
                i += 2
                continue
            if c in (WILL, WONT, DO, DONT):
                if i + 2 >= len(buffer):
                    break                      # incomplete option triplet
                i += 3                         # splice out a late option
                continue
            i += 2                             # splice out a standalone command
        if len(buffer) > MAX_BUFFER_SIZE:
            print(f"WARNING: client buffer exceeded {MAX_BUFFER_SIZE} bytes; closing connection")
            return None
        data = client_socket.recv(1024)
        if not data:
            return None
        buffer.extend(data)


# Signals that the SYSREQ SSCP-LU session ended with LOGOFF. The handlers below
# return this sentinel; :func:`read_client_input` turns it into a
# :class:`_SessionLogoff`, which unwinds any nested sub-panel read loops back to
# :func:`_client_thread` — a host-session-initiated end of the whole session,
# unlike a normal ISPF exit which only pops one panel.
_LOGOFF = object()


class _SessionLogoff(Exception):
    """Raised to tear the session down after a SYSREQ-session LOGOFF."""


def _decode_buffer_addr(hi: int, lo: int) -> int:
    """Decode an inbound 2-byte 3270 buffer address, honouring both forms
    (GA23-0059): top two bits of the first byte 01/11 mean a 12-bit *coded*
    address (6 bits per byte); 00 means a 14-bit *binary* address, which a
    terminal with more than 4096 cells (e.g. x3270 -oversize) legitimately
    sends for positions beyond coded range (#348)."""
    if hi & 0xC0:
        return ((hi & 0x3F) << 6) | (lo & 0x3F)
    return ((hi & 0x3F) << 8) | lo


def _parse_3270_reply(buffer):
    """Decode one inbound 3270 AID reply into ``(aid, {addr: text}, cursor,
    {addr: [(attr_type, attr_value), …]})``.

    Byte 0 is the AID. A normal (non short-read) reply then carries the 12-bit
    cursor address, followed by SBA-addressed modified fields; short reads
    (CLEAR/PA) and synthetic test payloads start straight into an SBA/SF order.

    Every SBA-addressed field is recorded, including one whose text strips to
    ``""``: only MDT-set fields ride in the reply, so an empty entry means the
    user *cleared* that field (#346) — distinct from an untouched field, which
    is absent from the dict altogether.

    Under Extended-Field / Character reply mode (#112) each modified field's data
    is preceded (Character mode: also interleaved) by its extended attributes as
    ``SA`` orders (``0x28 <type> <value>``). Those triples are consumed as
    attributes — recorded per field in the fourth element — rather than being
    mistaken for field text; in the default Field mode none are present, so the
    text is read exactly as before.
    """
    aid = buffer[0]
    # Log only the AID (not the raw bytes) to avoid leaking password data in logs
    print(f"RX: {len(buffer)} bytes, AID: {aid_to_string(aid)}")

    SBA_ORD = 0x11
    SF_ORD = 0x1D
    cursor = None
    if len(buffer) >= 3 and buffer[1] not in (SBA_ORD, SF_ORD):
        cursor = _decode_buffer_addr(buffer[1], buffer[2])

    results = {}
    field_attrs = {}
    i = 1
    while i < len(buffer):
        if buffer[i] == SBA_ORD and i + 2 < len(buffer):
            addr_hi, addr_lo = buffer[i + 1], buffer[i + 2]
            addr = _decode_buffer_addr(addr_hi, addr_lo)
            i += 3
            field_bytes = bytearray()
            attrs = []
            while i < len(buffer) and buffer[i] not in (SBA_ORD, SF_ORD):
                if buffer[i] == SA_ORDER and i + 2 < len(buffer):
                    # Extended-Field / Character reply mode: the field's (or a
                    # character's) extended attribute, as an SA type/value pair.
                    attrs.append((buffer[i + 1], buffer[i + 2]))
                    i += 3
                else:
                    field_bytes.append(buffer[i])
                    i += 1
            # Record the field even when it stripped to "" (#346): a field is
            # only in a Read Modified reply because its MDT is set, so a blank
            # one means the user *cleared* it (Erase EOF suppresses the nulls
            # entirely — just SBA + address arrives). Dropping it made a cleared
            # value indistinguishable from an untouched field, so consumers that
            # fall back to the rendered default (Screen.read_table_rows) would
            # resurrect the old value and a cleared cell could never stay blank.
            results[addr] = from_ebcdic(field_bytes).strip()
            if attrs:
                field_attrs[addr] = attrs
        else:
            i += 1

    return aid, results, cursor, field_attrs


def read_client_input(client_socket):
    """Read the next AID reply from the client, transparently handling the
    TN3270E SYSREQ and ATTN keys along the way.

    On a TN3270E stream that negotiated SYSREQ, the SYSREQ key arrives as a
    Telnet AO between records; it drops into the SSCP-LU (host-session) mode
    (:func:`_handle_sysreq`) and, unless that ends in LOGOFF, we loop back for
    the real next reply. ATTN (Telnet IP) is a mid-record interrupt that just
    redisplays the panel. Plain TN3270 sockets have no such signalling, so they
    read a record and parse it directly.

    Returns ``(aid, fields, cursor)`` or ``None`` on disconnect. A SYSREQ session
    that logs off raises :class:`_SessionLogoff`, unwinding to
    :func:`_client_thread`.
    """
    if isinstance(client_socket, TN3270EStream):
        while True:
            ev = client_socket.next_event()
            if ev is None:
                return None
            if ev[0] == "sysreq":
                if _handle_sysreq(client_socket) is _LOGOFF:
                    raise _SessionLogoff
                continue                      # resumed: read the real next reply
            if ev[0] == "attn":
                _handle_attn(client_socket)
                continue
            # The session loops read field text; the reply-mode attributes (4th
            # element) are available via _parse_3270_reply for a caller that wants
            # them, but aren't threaded through the 3-tuple here.
            aid, fields, cursor, _attrs = _parse_3270_reply(ev[2])
            return aid, fields, cursor

    buffer = read_record(client_socket)
    if not buffer:
        return None
    aid, fields, cursor, _attrs = _parse_3270_reply(buffer)
    return aid, fields, cursor


def _sscp_text(payload: bytes) -> str:
    """Decode the text a terminal typed in the SSCP-LU session. The payload is
    unformatted EBCDIC (SCS); an AID/cursor prefix, if the emulator sends the
    line as a 3270 reply, is harmless once the control bytes are stripped."""
    text = from_ebcdic(payload, errors="replace")
    return "".join(ch for ch in text if ch >= " ").strip()


def _handle_attn(client_socket):
    """Handle the ATTN key, which arrives as a Telnet IP (Interrupt Process).
    ATTN signals the application to interrupt the current transaction; with no
    long-running transaction to cancel here, we acknowledge it by redisplaying
    the current panel so the terminal is never left with a locked keyboard.

    An x3270-family emulator only sends ATTN once the TN3270E session is *bound*
    (its Attn action otherwise just locks the keyboard locally), which is why we
    send a BIND-IMAGE at session start when that function is negotiated — see
    :meth:`TN3270EStream.send_bind`. A plain-TN3270 client sends IP directly."""
    print("ATTN: redisplaying current screen")
    client_socket.redisplay()


def _handle_sysreq(client_socket):
    """Handle the SYSREQ key (TN3270E, Telnet AO). SYSREQ suspends the LU-LU
    (ISPF application) session and switches the terminal to the SSCP-LU session —
    the host's session manager. There the only command we honour is LOGOFF, which
    ends the session; anything else draws 'COMMAND UNRECOGNIZED'. A second SYSREQ
    resumes the application session, restoring the panel.

    Returns :data:`_LOGOFF` if the user logged off (caller should disconnect), or
    ``None`` to resume the suspended application session.
    """
    print("SYSREQ: entering SSCP-LU (host session) mode")
    client_socket.send_sscp(
        "\r\n IKJ56700A ENTER LOGOFF, OR PRESS SYSREQ TO RETURN\r\n")
    while True:
        ev = client_socket.next_event()
        if ev is None:
            return _LOGOFF                    # disconnect: end the session
        if ev[0] == "sysreq":
            print("SYSREQ: resuming application session")
            client_socket.redisplay()
            return None
        if ev[0] == "attn":
            continue                          # ATTN is a no-op in host session
        command = _sscp_text(ev[2])           # ("record", data_type, payload)
        print(f"SSCP-LU input: {command!r}")
        if command.upper() == "LOGOFF":
            client_socket.send_sscp("\r\n IKJ56470I LOGOFF COMPLETE\r\n")
            client_socket.send_unbind()
            return _LOGOFF
        client_socket.send_sscp(
            f"\r\n {command or '(empty)'} COMMAND UNRECOGNIZED\r\n")


def _send_e_sb(sock, payload: bytes):
    """Send a TN3270E sub-negotiation message: IAC SB TN3270E <payload> IAC SE."""
    msg = bytes([IAC, SB, TN3270E]) + bytes(payload) + bytes([IAC, SE])
    print("TX:", binascii.hexlify(msg))
    sock.sendall(msg)


def tn3270_negotiate(client_socket):
    """Negotiate the Telnet options and identify the terminal.

    Offers TN3270E (RFC 2355) alongside basic TN3270. If the client accepts
    (WILL TN3270E), its device type comes from the TN3270E DEVICE-TYPE exchange
    and a (empty) FUNCTIONS set is agreed, and the returned model has
    ``tn3270e=True`` so the session frames data with the 5-byte header. If the
    client refuses (WONT TN3270E), it falls back to the basic TERMINAL-TYPE
    exchange exactly as before.
    """
    DONT, DO, WONT, WILL, SB, SE = 254, 253, 252, 251, 250, 240
    BINARY, TERMINAL_TYPE, EOR_OPT = 0, 24, 25

    got_binary = got_eor = got_term = False
    e_state = "unknown"          # "unknown" until the client accepts/refuses
    got_device = got_functions = responses = sysreq = bind_image = False
    contention = False
    term_type = None
    nvt = False                  # client refused 3270 framing → line-mode (NVT)
    nvt_pending = b""            # any line-mode input typed during negotiation

    negot = bytearray()
    negot += bytes([IAC, WILL, BINARY, IAC, DO, BINARY])
    negot += bytes([IAC, WILL, EOR_OPT, IAC, DO, EOR_OPT])
    negot += bytes([IAC, DO, TN3270E])       # offer TN3270E
    negot += bytes([IAC, WILL, TERMINAL_TYPE, IAC, DO, TERMINAL_TYPE])
    negot += bytes([IAC, SB, TERMINAL_TYPE, 1, IAC, SE])
    print("TX:", binascii.hexlify(negot))
    client_socket.sendall(negot)

    client_socket.settimeout(60.0)
    buffer = bytearray()
    # Options we have already offered/agreed, so we don't re-acknowledge the
    # client's reply to our own offer — re-acking would ping-pong forever and
    # leave stray bytes ahead of the data stream.
    offered_will = {BINARY, EOR_OPT, TERMINAL_TYPE}
    offered_do = {BINARY, EOR_OPT, TERMINAL_TYPE, TN3270E}

    def done():
        if nvt:
            return True   # a line-mode client — stop negotiating, run NVT
        if e_state == "active":
            return got_binary and got_eor and got_device and got_functions
        if e_state == "off":
            return got_binary and got_eor and got_term
        return False  # still waiting for the client's TN3270E WILL/WONT

    while not done():
        data = client_socket.recv(1024)
        if not data:
            break
        buffer.extend(data)
        print("RX:", binascii.hexlify(data))

        i = 0
        while i < len(buffer):
            if buffer[i] != IAC:
                # Plain (non-IAC) data mid-negotiation means a line-mode NVT
                # client is typing, not a 3270 terminal completing options.
                nvt = True
                nvt_pending = bytes(buffer[i:])
                break
            if i + 1 >= len(buffer):
                break  # IAC at recv boundary; wait for more data
            cmd = buffer[i + 1]

            if cmd in (DO, DONT, WILL, WONT):
                if i + 2 >= len(buffer):
                    break  # incomplete triplet
                opt = buffer[i + 2]
                if opt == TN3270E:
                    if cmd == WILL and e_state != "active":
                        e_state = "active"
                        _send_e_sb(client_socket, bytes([E_SEND, E_DEVICE_TYPE]))
                    elif cmd == WONT and e_state != "active":
                        e_state = "off"
                elif cmd == WILL:
                    # Client agrees to (or offers) opt. Ack with DO only if we did
                    # not already offer/agree it (else we loop).
                    if opt not in offered_do:
                        client_socket.sendall(bytes([IAC, DO, opt]))
                        offered_do.add(opt)
                    got_binary = got_binary or opt == BINARY
                    got_eor = got_eor or opt == EOR_OPT
                elif cmd == DO:
                    if opt not in offered_will:
                        client_socket.sendall(bytes([IAC, WILL, opt]))
                        offered_will.add(opt)
                    got_binary = got_binary or opt == BINARY
                    got_eor = got_eor or opt == EOR_OPT
                elif cmd == DONT:
                    if opt in offered_will:
                        client_socket.sendall(bytes([IAC, WONT, opt]))
                        offered_will.discard(opt)
                elif cmd == WONT:
                    if opt in offered_do:
                        client_socket.sendall(bytes([IAC, DONT, opt]))
                        offered_do.discard(opt)
                # A client that refuses 8-bit BINARY can't carry a 3270 data
                # stream — it's a line-mode (NVT) client.
                if opt == BINARY and cmd in (WONT, DONT):
                    nvt = True
                i += 3
                continue

            if cmd == SB:
                se_pos = buffer.find(bytes([IAC, SE]), i + 2)
                if se_pos == -1:
                    break  # incomplete SB; wait for more data
                opt = buffer[i + 2]
                sub = bytes(buffer[i + 3:se_pos])   # payload after the option byte
                if opt == TERMINAL_TYPE and sub:
                    if sub[0] == 1:      # SEND → reply with our terminal type
                        client_socket.sendall(
                            bytes([IAC, SB, TERMINAL_TYPE, 0]) + b"IBM-3278-2"
                            + bytes([IAC, SE]))
                    elif sub[0] == 0:    # IS → the client's terminal type
                        term_type = sub[1:].decode(errors="ignore")
                        print("Client terminal type:", term_type)
                        got_term = True
                elif opt == TN3270E and len(sub) >= 2:
                    got_device, got_functions, dtype, funcs = _handle_tn3270e_sb(
                        client_socket, sub, got_device, got_functions)
                    if dtype is not None:
                        term_type = dtype
                    if funcs is not None:
                        responses = E_FUNC_RESPONSES in funcs
                        sysreq = E_FUNC_SYSREQ in funcs
                        bind_image = E_FUNC_BIND_IMAGE in funcs
                        contention = E_FUNC_CONTENTION_RESOLUTION in funcs
                i = se_pos + 2
                continue

            print("Unknown IAC command:", cmd)
            i += 2

    if nvt:
        print("Negotiation complete: line-mode (NVT) client — no 3270 framing")
        return replace(parse_terminal_type(term_type), nvt=True), nvt_pending

    e_active = e_state == "active"
    model = parse_terminal_type(term_type)
    if e_active:
        model = replace(model, tn3270e=True, tn3270e_responses=responses,
                        tn3270e_sysreq=sysreq, tn3270e_bind_image=bind_image,
                        tn3270e_contention=contention)
    print("Negotiation complete: binary={}, eor={}, tn3270e={}, device={}".format(
        got_binary, got_eor, e_active, model.term_type))
    print(f"Terminal model: {model.term_type} "
          f"(model {model.model}, alt {model.alt_rows}x{model.alt_cols}, "
          f"{'colour' if model.color else 'mono'}"
          f"{', extended' if model.extended else ''}"
          f"{', TN3270E' if e_active else ''}"
          f"{', RESPONSES' if e_active and responses else ''}"
          f"{', SYSREQ' if e_active and sysreq else ''}"
          f"{', BIND-IMAGE' if e_active and bind_image else ''}"
          f"{', CONTENTION-RESOLUTION' if e_active and contention else ''})")
    return model, b""


def _handle_tn3270e_sb(sock, sub: bytes, got_device: bool, got_functions: bool):
    """Handle one inbound TN3270E sub-negotiation message (``sub`` is the bytes
    after ``IAC SB TN3270E``). Returns ``(got_device, got_functions, device_type
    or None, agreed_functions or None)`` — ``agreed_functions`` is the frozenset
    of TN3270E functions in effect once this message settles FUNCTIONS, else
    ``None``.

    DEVICE-TYPE REQUEST → we answer DEVICE-TYPE IS (echoing the type, assigning a
    device name) and propose the FUNCTIONS we support (RESPONSES, SYSREQ).
    FUNCTIONS REQUEST → we answer FUNCTIONS IS with the intersection of the
    client's list and what we support. FUNCTIONS IS → the client's agreed set.
    """
    category, action = sub[0], sub[1]
    if category == E_DEVICE_TYPE and action == E_REQUEST:
        rest = sub[2:]
        devtype = rest[:rest.index(E_CONNECT)] if E_CONNECT in rest else rest
        device_type = devtype.decode(errors="ignore").strip()
        _send_e_sb(sock, bytes([E_DEVICE_TYPE, E_IS]) + devtype
                   + bytes([E_CONNECT]) + b"IBMTCP01")
        _send_e_sb(sock, bytes([E_FUNCTIONS, E_REQUEST]) + bytes(sorted(E_SUPPORTED_FUNCTIONS)))
        return True, got_functions, device_type, None
    if category == E_FUNCTIONS and action == E_IS:
        return got_device, True, None, frozenset(sub[2:]) & E_SUPPORTED_FUNCTIONS
    if category == E_FUNCTIONS and action == E_REQUEST:
        agreed = frozenset(sub[2:]) & E_SUPPORTED_FUNCTIONS
        _send_e_sb(sock, bytes([E_FUNCTIONS, E_IS]) + bytes(sorted(agreed)))
        return got_device, True, None, agreed
    return got_device, got_functions, None, None


_NVT_BANNER = (
    "z/OS 2.5  TSO/E  (line mode)\n"
    "\n"
    "This is a line-mode (NVT) session — your client did not negotiate the 3270\n"
    "data stream. Full-screen ISPF needs a 3270 terminal (e.g. wc3270/x3270).\n"
    "Type HELP for commands, LOGOFF to disconnect.\n"
)


def _strip_nvt(raw: bytes) -> str:
    """Decode one line of NVT input to ASCII, dropping any Telnet control bytes
    (IAC option triplets / commands) and CR/NUL that a line-mode client may
    interleave with the typed text."""
    out = bytearray()
    i = 0
    while i < len(raw):
        b = raw[i]
        if b == IAC:
            if i + 1 < len(raw) and raw[i + 1] in (WILL, WONT, DO, DONT):
                i += 3
            else:
                i += 2                 # IAC IAC (data 0xFF) or a bare command
            continue
        if b not in (0x0D, 0x00):      # drop CR and NUL (CR LF / CR NUL line ends)
            out.append(b)
        i += 1
    return out.decode("ascii", "replace")


def run_nvt_session(client_socket, initial=b""):
    """Serve a plain-ASCII **NVT (line-mode)** session — a minimal TSO ``READY``
    command loop — to a client that refused the 3270 binary framing. Commands:
    ``TIME``, ``HELP``, ``ISPF`` (explains a 3270 terminal is required), and
    ``LOGOFF``/``LOGOUT``/``EXIT``/``QUIT`` to disconnect; any other verb gets the
    authentic ``COMMAND xxx NOT FOUND``. Line-mode clients local-echo, so we send
    only responses and the ``READY`` prompt."""
    print("NVT: line-mode session")
    client_socket.settimeout(600)

    def send(text):
        client_socket.sendall(text.replace("\n", "\r\n").encode("ascii", "replace"))

    send("\n" + _NVT_BANNER + "\n")
    buf = bytearray(initial)
    need_prompt = True
    while True:
        if need_prompt:
            send("READY\n")
            need_prompt = False
        nl = buf.find(b"\n")
        if nl == -1:
            try:
                chunk = client_socket.recv(1024)
            except OSError:
                return
            if not chunk:
                return
            buf.extend(chunk)
            continue
        line = _strip_nvt(bytes(buf[:nl]))
        del buf[:nl + 1]
        cmd = line.strip()
        need_prompt = True
        if not cmd:
            continue
        verb = cmd.split()[0].upper()
        if verb in ("LOGOFF", "LOGOUT", "EXIT", "QUIT", "BYE"):
            send("IKJ56470I LINE-MODE SESSION ENDED\n")
            return
        if verb in ("ISPF", "ISPPDF", "PDF"):
            send("ISPF requires a full-screen 3270 terminal; "
                 "connect a 3270 emulator.\n")
            continue
        if verb == "HELP":
            send("Commands: TIME, ISPF, HELP, LOGOFF\n")
            continue
        send(_run_tso_command(cmd) + "\n")


# ── the Transport port (#351) ────────────────────────────────────────────────
# The seam between this protocol layer and the TSO/ISPF application in
# session.py. The application performs exactly two operations against the
# connection — send one rendered 3270 record, await one parsed AID reply — so
# the port is exactly those two methods, derived from the call sites rather
# than invented. Anything protocol-shaped (Telnet negotiation, TN3270E
# framing/RESPONSES/SYSREQ/ATTN, TLS, the reply codec) stays on this side of
# the seam, inside read_client_input and the stream classes above.

class SocketTransport:
    """The application's port onto a negotiated connection.

    Wraps whatever :func:`handle_client` ended negotiation with — a plain
    socket, a TLS-wrapped one, or a :class:`TN3270EStream` — behind the two
    operations :func:`session.run` needs. A test drives the same application
    with an in-memory fake instead (see test_session.py)."""

    def __init__(self, client_socket):
        self.client_socket = client_socket

    def send(self, data: bytes):
        """Send one fully rendered 3270 record (already IAC-escaped and
        IAC-EOR-terminated by the render layer)."""
        self.client_socket.sendall(data)

    def read_input(self):
        """Block for the next parsed AID reply: ``(aid, fields, cursor)``, or
        ``None`` on disconnect. TN3270E SYSREQ/ATTN and RESPONSES messages are
        consumed along the way; a SYSREQ-session LOGOFF raises
        :class:`_SessionLogoff`, unwinding the whole application."""
        return read_client_input(self.client_socket)


# ── application re-exports (#351) ────────────────────────────────────────────
# The application moved to session.py, but its public surface has always been
# importable from server (tests and callers do ``from server import
# send_tso_logon`` / ``server._show_help(sock, …)``). The pure names are
# re-exported directly; the panel-driving functions — which now take a
# Transport — are re-exported behind a socket-first adapter so existing call
# sites that pass a raw (or TN3270E-wrapped) socket keep working unchanged.
from session import (  # noqa: E402, F401  (re-exported)
    _CREDENTIALS,
    DEFAULT_COLS,
    ISPF_OPTION_ADDR,
    ISPF_OPTION_ROW,
    ISPF_OPTION_SF_COL,
    LOGON_PASSWORD_ADDR,
    LOGON_PASSWORD_ROW,
    LOGON_PASSWORD_SF_COL,
    LOGON_PROC_ADDR,
    LOGON_PROC_ROW,
    LOGON_PROC_SF_COL,
    LOGON_USERID_ADDR,
    LOGON_USERID_ROW,
    LOGON_USERID_SF_COL,
    _LEAVE,
    _LEAVE_COMMANDS,
    _SELECTION_HANDLERS,
    _dialog_vars,
    _is_cursor_select,
    _library_members,
    _member_path,
    _messages,
    _parse_selection,
    _pdc_item_text,
    _run_pdc_action,
    _run_tso_command,
    _scroll_amount,
    _screen_size,
    _submenu,
    _wants_color,
    redact_fields,
)


def _socket_first(fn):
    """Adapt a transport-first session.py function back to the socket-first
    signature this module used to define: the socket is wrapped in a
    :class:`SocketTransport` and the call is delegated unchanged."""
    @functools.wraps(fn)
    def shim(client_socket, *args, **kwargs):
        return fn(SocketTransport(client_socket), *args, **kwargs)
    return shim


_send_screen = _socket_first(session._send_screen)
send_tso_logon = _socket_first(session.send_tso_logon)
send_ispf_menu = _socket_first(session.send_ispf_menu)
_update_menu_message = _socket_first(session._update_menu_message)
_await_action = _socket_first(session._await_action)
_show_command_shell = _socket_first(session._show_command_shell)
_show_browse = _socket_first(session._show_browse)
_show_view = _socket_first(session._show_view)
_show_member_list = _socket_first(session._show_member_list)
_show_dialog_test = _socket_first(session._show_dialog_test)
_show_table_input = _socket_first(session._show_table_input)
_show_submenu = _socket_first(session._show_submenu)
_show_help = _socket_first(session._show_help)
_show_overlay = _socket_first(session._show_overlay)
_show_pulldown = _socket_first(session._show_pulldown)
_run_selection = _socket_first(session._run_selection)


def handle_client(client_socket, addr):
    print(f"Connection from {addr}")
    model, nvt_pending = tn3270_negotiate(client_socket)
    # A line-mode (NVT/ASCII) client can't carry a 3270 data stream; serve it a
    # plain-text TSO READY session instead of a hung 3270 negotiation.
    if model.nvt:
        run_nvt_session(client_socket, nvt_pending)
        return
    # When TN3270E was negotiated, wrap the socket so every subsequent record is
    # framed with the 5-byte data header (and inbound headers are stripped in
    # read_record) — transparently to all the screen-sending code below.
    if model.tn3270e:
        client_socket = TN3270EStream(client_socket, responses=model.tn3270e_responses,
                                      sysreq=model.tn3270e_sysreq,
                                      bind_image=model.tn3270e_bind_image,
                                      contention=model.tn3270e_contention)
    # Ask an extended terminal to describe itself (real size, colour); a base
    # terminal is left untouched. Uses the 60s negotiation timeout still in
    # effect, so a silent client can't wedge the session before the 600s below.
    model = query_terminal(client_socket, model)
    # If the terminal advertised Reply Modes, switch it to a richer inbound reply
    # mode so a modified field's extended attributes come back with it (#112). A
    # no-op under TN3270E (the Query is unanswered, so no reply-modes capability).
    model = request_reply_mode(client_socket, model)
    client_socket.settimeout(600)
    # Everything discovered about this session, as one explicit value (#352):
    # the colour capability (so every panel renders in colour on a colour
    # terminal), the EBCDIC code page picked from the terminal's discovered base
    # character set (#137, e.g. cp273 for a German ws3270, defaulting to US
    # cp037), and the negotiated model. Installed on the thread-local shim too,
    # so the ambient readers not yet migrated (the reply parser's
    # to_ebcdic/from_ebcdic) stay in agreement.
    ctx = SessionContext(code_page=code_page_for_model(model),
                         color=_wants_color(model), model=model)
    activate_session(ctx)
    # If BIND-IMAGE was negotiated, bind the LU-LU session before the first
    # screen: the client won't accept 3270-DATA until it has seen a BIND, and
    # being bound is what lets its ATTN key reach us (RFC 2355 §10.3).
    if model.tn3270e_bind_image:
        client_socket.send_bind()

    # Negotiation settled: hand the connection to the application (session.py,
    # #351), with the session context passed explicitly (#352). From here on the
    # TSO/ISPF flow drives the two-method Transport port and never sees the
    # socket, the TN3270E framing, or TLS.
    session.run(SocketTransport(client_socket), model, ctx=ctx)


def make_tls_context(certfile, keyfile=None):
    """Build a server-side TLS context from a PEM cert (and key, if separate).

    This is *implicit* TLS: the whole connection is TLS from the first byte, the
    way an x3270-family emulator connects when the host is given the ``L:``
    prefix (e.g. ``L:host:992``). ``keyfile`` may be ``None`` if the key is in
    ``certfile``."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2   # refuse the obsolete TLS 1.0/1.1
    ctx.load_cert_chain(certfile, keyfile)
    return ctx


_STARTTLS_TIMEOUT = 10.0


def _recv_byte(sock) -> int:
    b = sock.recv(1)
    if not b:
        raise ConnectionError("connection closed during STARTTLS")
    return b[0]


def _drain_subneg(sock) -> None:
    """Consume a Telnet subnegotiation up to and including its ``IAC SE``."""
    prev = None
    for _ in range(64):
        b = _recv_byte(sock)
        if prev == IAC and b == SE:
            return
        prev = b


def _client_accepts_starttls(sock) -> bool:
    """Read the client's reply to ``DO START-TLS`` one byte at a time. Return True
    on ``WILL`` (after consuming its ``SB START-TLS FOLLOWS SE``), False on
    ``WONT``. Unrelated Telnet commands the client might interleave are skipped;
    the loop is bounded so malformed input can't spin forever."""
    for _ in range(64):
        if _recv_byte(sock) != IAC:
            continue
        cmd = _recv_byte(sock)
        if cmd in (WILL, WONT, DO, DONT):
            opt = _recv_byte(sock)
            if opt != TELOPT_STARTTLS:
                continue                      # a different option — ignore it
            if cmd == WILL:
                _drain_subneg(sock)           # IAC SB START-TLS FOLLOWS IAC SE
                return True
            return False                      # WONT → stay in the clear
        if cmd == SB:
            _drain_subneg(sock)               # skip any other subnegotiation
    return False


def _offer_starttls(client_socket, tls_context):
    """Offer negotiated START-TLS (Telnet option 46) and, if the client accepts,
    upgrade the connection to TLS in place; return the (possibly wrapped) socket.

    We send ``DO START-TLS``; a willing client replies ``WILL START-TLS`` then
    ``SB START-TLS FOLLOWS SE``; we answer ``SB START-TLS FOLLOWS SE`` and run the
    TLS handshake as the server. The client begins TLS only *after* our FOLLOWS, so
    reading its plaintext replies one byte at a time can never swallow TLS handshake
    bytes. A refusal or any malformed/short exchange leaves the plaintext socket
    unchanged, so a client that doesn't do START-TLS still gets a session."""
    client_socket.settimeout(_STARTTLS_TIMEOUT)
    client_socket.sendall(bytes([IAC, DO, TELOPT_STARTTLS]))
    try:
        if not _client_accepts_starttls(client_socket):
            return client_socket
    except (OSError, ConnectionError):
        return client_socket
    client_socket.sendall(bytes([IAC, SB, TELOPT_STARTTLS, TLS_FOLLOWS, IAC, SE]))
    return tls_context.wrap_socket(client_socket, server_side=True)


def _client_thread(client_socket, addr, tls_context=None, starttls=False):
    try:
        # Complete the TLS handshake here (in the per-client thread, so a slow or
        # hostile client can't stall the accept loop) before any 3270 bytes flow.
        # handle_client then reads/writes through the TLS socket unchanged —
        # TN3270EStream and read_record only use recv/sendall.
        if tls_context is not None:
            if starttls:
                # Negotiated TLS: begin in the clear and offer an in-band upgrade.
                client_socket = _offer_starttls(client_socket, tls_context)
            else:
                # Implicit TLS: the whole connection is TLS from the first byte.
                client_socket = tls_context.wrap_socket(client_socket, server_side=True)
        handle_client(client_socket, addr)
    except _SessionLogoff:
        print(f"Client {addr} logged off via SYSREQ")
    except ssl.SSLError as e:
        print(f"TLS handshake with {addr} failed: {e}")
    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
        print(f"Client {addr} disconnected unexpectedly")
    except Exception as e:
        print(f"Error handling client {addr}: {e}")
    finally:
        client_socket.close()


def run_tn3270_server(host="127.0.0.1", port=2323, certfile=None, keyfile=None,
                      starttls=False):
    """Serve TN3270/TN3270E on ``host:port``. If ``certfile`` is given, the
    connection is secured with TLS: *implicit* TLS by default (encrypted from the
    first byte, the way the ``L:`` host prefix connects), or *negotiated*
    START-TLS when ``starttls`` is set (begins in the clear on the normal port and
    upgrades in-band, see :func:`_offer_starttls`). Without a cert the server is
    plaintext (the default, unchanged)."""
    tls_context = make_tls_context(certfile, keyfile) if certfile else None
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind((host, port))
        server_socket.listen(socket.SOMAXCONN)
        if tls_context is None:
            scheme = "TN3270"
        elif starttls:
            scheme = "TN3270 with negotiated START-TLS"
        else:
            scheme = "TN3270 over TLS"
        print(f"{scheme} server listening on {host}:{port}")
        while True:
            client_socket, addr = server_socket.accept()
            threading.Thread(target=_client_thread,
                             args=(client_socket, addr, tls_context, starttls),
                             daemon=True).start()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="A TN3270/TN3270E TSO/ISPF server.")
    parser.add_argument("--host", default=os.environ.get("TN3270_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("TN3270_PORT", "2323")))
    parser.add_argument("--certfile", default=os.environ.get("TN3270_CERTFILE"),
                        help="PEM certificate; enables implicit TLS when set")
    parser.add_argument("--keyfile", default=os.environ.get("TN3270_KEYFILE"),
                        help="PEM private key (omit if the key is in --certfile)")
    parser.add_argument("--starttls", action="store_true",
                        default=bool(os.environ.get("TN3270_STARTTLS")),
                        help="use negotiated START-TLS (in-band upgrade) instead of "
                             "implicit TLS; requires --certfile")
    args = parser.parse_args()
    if args.starttls and not args.certfile:
        parser.error("--starttls requires --certfile")
    run_tn3270_server(host=args.host, port=args.port,
                      certfile=args.certfile, keyfile=args.keyfile,
                      starttls=args.starttls)
