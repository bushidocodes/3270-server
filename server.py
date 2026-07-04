import os
import socket
import ssl
import binascii
import threading
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum


# Per-connection session context. Each client is served on its own thread (see
# _client_thread), so its colour capability and EBCDIC code page are recorded here
# once after negotiation and read by the render/encode helpers for every panel —
# without threading flags through each one. Defined before the encode helpers
# below because a module-level constant (_BIND_IMAGE) encodes text at import time.
_session = threading.local()

# The EBCDIC code page used to encode/decode text when a session hasn't chosen a
# different one. cp037 (US) is what the bundled panels assume, so it stays the
# default — leaving the mono data stream byte-for-byte unchanged.
DEFAULT_CODE_PAGE = "cp037"


def _session_code_page() -> str:
    """The current session's EBCDIC code page (thread-local, see :data:`_session`),
    or the US default when none was negotiated/selected."""
    return getattr(_session, "code_page", DEFAULT_CODE_PAGE)


def to_ebcdic(s: str, code_page: str = None, errors: str = "strict") -> bytes:
    """Encode text to EBCDIC. Uses the session's code page (thread-local) unless
    ``code_page`` is given — the explicit argument lets a caller override per field
    (e.g. a future mixed-CCSID panel) without touching the session default."""
    return s.encode(code_page or _session_code_page(), errors)


def from_ebcdic(b: bytes, code_page: str = None, errors: str = "strict") -> str:
    """Decode EBCDIC to text, mirroring :func:`to_ebcdic`'s code-page resolution."""
    return bytes(b).decode(code_page or _session_code_page(), errors)


def encode_pack_addr(row: int, col: int, cols=80) -> bytes:
    """Encodes a 12-bit 3270 presentation space address from row/col"""
    addr = row * cols + col
    if addr < 0 or addr >= 0x1000:
        raise ValueError("Address out of range")
    hi_chunk = (addr >> 6) & 0b0011_1111
    lo_chunk = addr & 0b0011_1111
    hi = hi_chunk | 0b1100_0000
    lo = lo_chunk | 0b0100_0000
    return bytes([hi, lo])


def write_control_character(
    reset_mdts: bool = True,
    sound_alarm: bool = False,
    keyboard_restore: bool = False,
    start_printer: bool = False,
) -> bytes:
    # WCC bit layout per x3270/wc3270 source (3270ds.h):
    #   0x40 = WCC_RESET_BIT      (always set for normal SNA/LU2 writes)
    #   0x08 = WCC_START_PRINTER_BIT
    #   0x04 = WCC_SOUND_ALARM_BIT
    #   0x02 = WCC_KEYBOARD_RESTORE_BIT  ← unlocks keyboard after AID
    #   0x01 = WCC_RESET_MDT_BIT         ← clears all MDT flags
    wcc = 0x40  # WCC_RESET_BIT: always include for LU2 mode
    if reset_mdts:
        wcc |= 0x01
    if sound_alarm:
        wcc |= 0x04
    if start_printer:
        wcc |= 0x08
    if keyboard_restore:
        wcc |= 0x02
    return bytes([wcc])


class DisplayIntensity(Enum):
    NORMAL = 0
    HIGH = 1
    HIGHLIGHTED = 2
    NON_DISPLAY = 3


class FieldType(Enum):
    ALPHANUMERIC = 0
    NUMERIC = 1


_FA_BASE = 0x40  # bit 6: marks byte as a field attribute (valid FA range 0x40-0x7F)


def field_attribute(
    display: DisplayIntensity = DisplayIntensity.NORMAL,
    protected: bool = True,
    field_type: FieldType = FieldType.ALPHANUMERIC,
    mdt: bool = False,
) -> int:
    attr = _FA_BASE
    if display == DisplayIntensity.HIGH:
        attr |= 0x08          # FA_INT_HIGH_SEL (bits 3-2 = 10)
    elif display == DisplayIntensity.HIGHLIGHTED:
        attr |= 0x04          # FA_INT_NORM_SEL (bits 3-2 = 01)
    elif display == DisplayIntensity.NON_DISPLAY:
        attr |= 0x0C          # FA_INT_ZERO_NSEL (bits 3-2 = 11)
    if protected:
        attr |= 0x20          # FA_PROTECT
    if field_type == FieldType.NUMERIC:
        attr |= 0x10          # FA_NUMERIC
    if mdt:
        attr |= 0x01          # FA_MDT
    return attr


IAC = 0xFF
EOR = 0xEF
SBA = 0x11
SF = 0x1D
IC = 0x13

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
        self._sock.sendall(header + data)

    def send_bind(self, image=_BIND_IMAGE):
        """Send the SNA BIND image (DATA-TYPE BIND-IMAGE), binding the LU-LU
        session. Required once BIND-IMAGE is negotiated: the client won't accept
        3270-DATA until it has seen a BIND (RFC 2355 §10.3), and being bound is
        what lets the client's ATTN key send its Telnet IP. Idempotent."""
        header = bytes([E_DT_BIND_IMAGE, 0x00, E_RSF_NO_RESPONSE, 0x00, 0x00])
        self._sock.sendall(header + image + bytes([IAC, EOR]))
        self.bound = True

    def send_sscp(self, text):
        """Send an SSCP-LU-DATA message (unformatted EBCDIC text) — used while the
        SYSREQ key has put the session in the SSCP-LU (suspended) mode."""
        header = bytes([E_DT_SSCP_LU_DATA, 0x00, E_RSF_NO_RESPONSE, 0x00, 0x00])
        self._sock.sendall(header + to_ebcdic(text) + bytes([IAC, EOR]))

    def send_unbind(self, reason=0x01):
        """Tell the client the session has ended (DATA-TYPE UNBIND). 0x01 is
        'normal end of session' (RFC 2355 §10.3)."""
        self._sock.sendall(bytes([E_DT_UNBIND, 0x00, E_RSF_NO_RESPONSE, 0x00, 0x00,
                                  reason, IAC, EOR]))
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
# provides the Screen/Field model that the panels render through. Screens are now
# authored declaratively in panels/*.dtl and loaded via dtl.load_panel() — see
# send_tso_logon / send_ispf_menu below.


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
    # Decoded from the Character Sets Query Reply (0x85) payload, so the send path
    # can gate features on what the terminal actually supports (see query_terminal).
    graphic_escape: bool = False    # advertises the alternate graphic (GE) set
    dbcs_capable: bool = False       # advertises a double-byte (DBCS) set
    base_cgcsgid: int = None         # base set's CGCSGID (GCSGID<<16 | CPGID), or None
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
# a Read Partition (Query) structured field, and the terminal answers (inbound
# AID 0x88) with a set of Query Reply structured fields describing its usable
# area, colour, highlighting, and more. This is also how an IBM-DYNAMIC terminal
# reports the size the type string can't. We fold the reply back into the
# TerminalModel so the session knows the terminal's true size and colour support.

WSF = 0xF3                  # Write Structured Field command (outbound)
SF_READ_PARTITION = 0x01    # structured-field id: Read Partition
SF_QUERY_REPLY = 0x81       # structured-field id: Query Reply (inbound)
AID_SF = 0x88               # inbound AID that introduces Query Reply data

# Read Partition request types (byte after the partition id).
SF_RP_QUERY = 0x02          # plain Query
SF_RP_QLIST = 0x03          # Query List
SF_RPQ_ALL = 0x80           # ...request type: return all supported QCODEs

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


def read_partition_query() -> bytes:
    """The plain Read Partition (Query) structured field: ``F3`` (WSF) then
    ``00 05 01 FF 02`` — length 0x0005, id 0x01 (Read Partition), partition 0xFF
    (whole device), type 0x02 (Query). Logical bytes; the caller IAC-escapes."""
    return bytes([WSF, 0x00, 0x05, SF_READ_PARTITION, 0xFF, SF_RP_QUERY])


def read_partition_query_list() -> bytes:
    """A Read Partition **Query List** asking the terminal to enumerate every
    QCODE it supports: ``F3`` then ``00 06 01 FF 03 80`` — type 0x03 (Query
    List), request type 0x80 (All). Logical bytes; the caller IAC-escapes. The
    reply's Summary (QCODE 0x80) lists the full capability set."""
    return bytes([WSF, 0x00, 0x06, SF_READ_PARTITION, 0xFF, SF_RP_QLIST, SF_RPQ_ALL])


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
        updates["alt_cols"] = caps["usable_cols"]
        updates["alt_rows"] = caps["usable_rows"]
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


# Credentials — keys are uppercase userids
_CREDENTIALS = {
    "IBMUSER": "SYS1",
    "TESTUSER": "RACF",
}
# Passwords are stored and compared uppercase (default RACF behavior without MIXEDCASE option)

# Field addresses (row * 80 + col_after_sf) for fields the server reads back
# TSO logon panel: input fields start at col 17, SF is at col 16
LOGON_USERID_SF_COL = 16
LOGON_USERID_ROW = 5
LOGON_PASSWORD_SF_COL = 16
LOGON_PASSWORD_ROW = 6
LOGON_PROC_SF_COL = 16
LOGON_PROC_ROW = 7

LOGON_USERID_ADDR = LOGON_USERID_ROW * 80 + (LOGON_USERID_SF_COL + 1)
LOGON_PASSWORD_ADDR = LOGON_PASSWORD_ROW * 80 + (LOGON_PASSWORD_SF_COL + 1)
LOGON_PROC_ADDR = LOGON_PROC_ROW * 80 + (LOGON_PROC_SF_COL + 1)

# ISPF menu: Option ===> input SF at col 13, data at col 14
ISPF_OPTION_SF_COL = 13
ISPF_OPTION_ROW = 2
ISPF_OPTION_ADDR = ISPF_OPTION_ROW * 80 + (ISPF_OPTION_SF_COL + 1)


def redact_fields(fields):
    """Return a copy of a parsed fields dict with the password field redacted.

    handle_client() logs the parsed fields for debugging, but the dict contains
    the decoded plaintext password keyed by LOGON_PASSWORD_ADDR. Emitting it to
    stdout would leak the password on every login, defeating the safe-logging
    guard in read_client_input(). Mask it before logging.
    """
    return {
        k: ("***" if k == LOGON_PASSWORD_ADDR else v)
        for k, v in fields.items()
    }


def _wants_color(model) -> bool:
    """Whether to render colour for this session — a 3279-family or any
    extended-data-stream (-E) terminal (see parse_terminal_type)."""
    return bool(model is not None and model.color)


def _send_screen(client_socket, screen, color: bool = None):
    """Render a screen.Screen to the 3270 data stream and send it. ``color``
    enables extended (colour/highlight) attributes; when omitted it defaults to
    the session's colour capability (:data:`_session`). A mono session leaves it
    false, so the bytes are unchanged."""
    if color is None:
        color = getattr(_session, "color", False)
    data = screen.render(color=color)
    print("TX:", binascii.hexlify(data))
    client_socket.sendall(data)


def send_tso_logon(client_socket, error_msg: str = None, model=None, alarm=False):
    """Send the z/OS TSO/E LOGON panel, rendered from panels/logon.dtl. On a
    colour terminal the panel's declared colours are emitted and a logon error
    is shown in red. ``alarm`` sounds the terminal alarm (an error message whose
    <msg> asks for it, e.g. a bad password), the way real ISPF beeps on error."""
    # Imported lazily: screen.py imports primitives from this module, so a
    # top-level import here would create a circular import at load time.
    from dtl import load_panel
    from screen import Text, Color

    color = _wants_color(model)
    screen = load_panel("logon")
    if error_msg:
        col = max(0, (80 - len(error_msg)) // 2)
        screen.add(Text(19, col, error_msg, DisplayIntensity.HIGH,
                        color=Color.RED if color else None))
        screen.sound_alarm = alarm
    _send_screen(client_socket, screen, color=color)
    return screen


def send_ispf_menu(client_socket, userid: str, short_msg: str = None):
    """Send the ISPF Primary Option Menu, rendered from panels/ispf.dtl."""
    from dtl import load_panel
    from screen import Text

    time_str = datetime.now().strftime("%H:%M")
    screen = load_panel("ispf", ZUSER=userid.ljust(8), ZTIME=time_str)
    if short_msg:
        screen.add(Text(2, 25, short_msg[:54], DisplayIntensity.HIGH))
    _send_screen(client_socket, screen)
    return screen


# The ISPF menu message occupies row 2 from column 25 to the right edge (the
# start-field byte sits at column 25, its 54 characters of text at 26..79).
_MENU_MSG_ROW, _MENU_MSG_COL, _MENU_MSG_WIDTH = 2, 25, 54


def _update_menu_message(client_socket, screen, short_msg):
    """Redisplay the ISPF menu's message line *in place* with a plain Write,
    the way real ISPF does — so the option the user typed (and the rest of the
    panel) stays put instead of being repainted and cleared.

    The message text is blank-filled to a fixed width so a shorter message fully
    overwrites a longer previous one, and the cursor is returned to the command
    field. ``screen`` is the Screen from the last full render (unchanged layout),
    reused to locate the command field."""
    from screen import Text

    color = getattr(_session, "color", False)
    text = (short_msg or "")[:_MENU_MSG_WIDTH].ljust(_MENU_MSG_WIDTH)
    msg_item = Text(_MENU_MSG_ROW, _MENU_MSG_COL, text, DisplayIntensity.HIGH)
    cursor_at = None
    if screen.command_field is not None:
        cf = screen.command_field
        cursor_at = (cf.row, cf.col + 1)   # the command field's data start
    data = screen.render_partial([msg_item], color=color, cursor_at=cursor_at)
    print("TX:", binascii.hexlify(data))
    client_socket.sendall(data)


def _dialog_vars(userid: str, model: "TerminalModel" = None):
    """The live ISPF dialog variables shown by Dialog Test (option 7), as
    ``{vname, vvalue}`` rows for the panel's ``<lstfld>`` table. These are real
    ISPF system-variable names with this session's current values — including
    ``ZTERM``, the terminal model negotiated at connect time."""
    now = datetime.now()
    term = model.term_type if model else "IBM-3278-2"
    return [
        {"vname": "ZUSER",   "vvalue": userid},
        {"vname": "ZPREFIX", "vvalue": userid},
        {"vname": "ZAPPLID", "vvalue": "ISR"},
        {"vname": "ZTIME",   "vvalue": now.strftime("%H:%M")},
        {"vname": "ZDATE",   "vvalue": now.strftime("%y/%m/%d")},
        {"vname": "ZSCREEN", "vvalue": "1"},
        {"vname": "ZTERM",   "vvalue": term},
        {"vname": "ZENVIR",  "vvalue": "ISPF 7.1"},
        {"vname": "ZKEYS",   "vvalue": "DLGTKEYS"},
    ]


def _run_tso_command(cmd: str) -> str:
    """Run a TSO command entered in the Command Shell (option 6) and return the
    response line. A small set of real commands is handled (TIME, READY); any
    other verb yields the authentic 'command not found' message TSO issues."""
    verb = cmd.split()[0].upper()
    if verb in ("TIME",):
        now = datetime.now()
        # Mirrors the real TSO TIME message (Julian date, day of week).
        return (now.strftime("IKJ56650I TIME-%I:%M:%S %p")
                + now.strftime(" DATE-%Y.%j DAY-").upper()
                + now.strftime("%A").upper())
    if verb in ("READY", "LISTBC", "PROFILE"):
        return "READY"
    return f"IKJ56500I COMMAND {verb} NOT FOUND"


# Sentinel returned by _await_action when the panel should be left (the client
# disconnected, or a PF3/PF15-style leave key was pressed).
_LEAVE = object()


def _await_action(client_socket, screen):
    """Send ``screen`` and read one response, handling the cases every simple
    sub-panel loop shares: a disconnect or a leave key (PF3/PF15) both yield
    :data:`_LEAVE`; PF1 with a help panel shows the help overlay and redisplays.
    Anything else is returned as ``(aid_str, fields, cursor)`` for the caller."""
    while True:
        _send_screen(client_socket, screen)
        result = read_client_input(client_socket)
        if result is None:
            return _LEAVE
        aid, fields, cursor = result
        aid_str = aid_to_string(aid)
        if screen.command_for(aid_str) in _LEAVE_COMMANDS:
            return _LEAVE
        if aid_str == "PF1":
            # Context-sensitive HELP: the field the cursor is on, else the panel.
            help_panel = screen.help_for(cursor) or screen.help
            if help_panel:
                _show_overlay(client_socket, help_panel)
                continue  # redisplay this panel after help
        return aid_str, fields, cursor


def _show_command_shell(client_socket):
    """ISPF option 6: a TSO Command Shell. Loops reading a command from the
    panel's <cmdarea>, running it, and showing the response, until the user
    presses PF3 (or PF1 for help). Enter runs the typed command and stays."""
    from dtl import load_panel

    msg = ""
    while True:
        screen = load_panel("command", CMDMSG=msg)
        action = _await_action(client_socket, screen)
        if action is _LEAVE:
            return
        _aid_str, fields, _cursor = action
        cmd = (screen.command_value(fields) or "").strip()
        msg = _run_tso_command(cmd) if cmd else ""


def _library_members():
    """The ISPF panel library (ISPPLIB) as member rows for the memlist table —
    the real panels/*.dtl files, so the Library utility lists what's actually
    there. Each row is {mname, mtype, mdesc}."""
    import os
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "panels")
    desc = {
        "logon": "TSO/E logon panel",
        "ispf": "Primary Option Menu",
        "settings": "Settings (action bar)",
        "utility": "Utility Selection Panel",
        "command": "TSO Command Shell",
        "dlgtest": "Dialog Test - Variables",
        "memlist": "Library - Member List",
        "tsohelp": "Logon help",
        "sizehelp": "Logon Size field help",
        "ispfhelp": "ISPF menu help",
        "viewentry": "View entry panel",
        "editentry": "Edit entry panel",
        "browse": "Browse frame",
        "foreground": "Foreground selection menu",
        "batch": "Batch selection menu",
        "ibmprod": "IBM Products menu",
        "sclm": "SCLM main menu",
        "workplace": "Object/Action Workplace",
        "zsystem": "z/OS System applications",
        "zuser": "z/OS User applications",
    }
    try:
        names = sorted(f[:-4] for f in os.listdir(base) if f.endswith(".dtl"))
    except OSError:
        names = []
    return [{"mname": n.upper(), "mtype": "Panel(DTL)", "mdesc": desc.get(n, "")}
            for n in names]


def _member_path(member: str):
    """Resolve a panel-library member name to its panels/<name>.dtl path, or
    None if the name is invalid or no such member exists. The name is restricted
    to ISPF member syntax (1-8 ASCII alphanumerics, leading letter), which also
    makes path traversal impossible — no dots or separators can appear."""
    import os
    if not (member and len(member) <= 8 and member.isascii()
            and member.isalnum() and member[0].isalpha()):
        return None
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "panels")
    path = os.path.join(base, f"{member.lower()}.dtl")
    return path if os.path.isfile(path) else None


def _screen_size(model):
    """The presentation-space ``(rows, cols)`` to render on for this terminal:
    the model's alternate size for models 3/4/5 (32x80, 43x80, 27x132), else the
    24x80 default that every model shares."""
    if model is None:
        return 24, 80
    return model.alt_rows, model.alt_cols


def _show_browse(client_socket, member: str, path: str, verb: str = "BROWSE",
                 model=None):
    """Browse a panel-library member's source (ISPF option 1 View, or option 2
    Edit with verb="EDIT"). Renders the file's lines below a header, with the
    footer rule on the last row, paging with PF7/PF8; PF3/PF15 returns to the
    entry panel. On a larger terminal (model 3/4/5) the panel is drawn on the
    alternate screen, so a taller/wider screen shows more lines per page."""
    from dtl import load_panel
    from screen import Text, DisplayIntensity

    rows, cols = _screen_size(model)
    alternate = rows > 24 or cols > 80     # bigger than the 24x80 default space
    page = rows - 2                        # row 0 is the header, the last row the footer
    line_width = cols - 1                  # leave the attribute byte a column
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    top = 0
    while True:
        top = max(0, min(top, max(0, len(lines) - 1)))
        shown_end = min(top + page, len(lines))
        title = (f"{verb}    ISPF.ISPPLIB({member.upper()})".ljust(cols - 25)
                 + f"Line {top + 1:08d}")[:line_width]
        foot = (f"Lines {top + 1}-{shown_end} of {len(lines)}"
                "     PF7=Up  PF8=Down  PF3=Exit")[:line_width]
        screen = load_panel("browse", BRTITLE=title)
        screen.width, screen.depth, screen.alternate = cols, rows, alternate
        for i, ln in enumerate(lines[top:top + page]):
            # Browsed content is arbitrary; drop any byte the session's EBCDIC
            # code page can't encode so the render can never crash.
            safe = from_ebcdic(to_ebcdic(ln, errors="replace"))
            screen.add(Text(1 + i, 0, safe[:line_width]))
        screen.add(Text(rows - 1, 0, foot, DisplayIntensity.HIGH))
        _send_screen(client_socket, screen)
        result = read_client_input(client_socket)
        if result is None:
            return
        aid, _, _ = result
        aid_str = aid_to_string(aid)
        if screen.command_for(aid_str) in _LEAVE_COMMANDS:
            return
        if aid_str in ("PF8", "PF20"):
            top += page
        elif aid_str in ("PF7", "PF19"):
            top -= page
        # any other key just redisplays the current page


def _show_view(client_socket, entry_panel: str = "viewentry", verb: str = "BROWSE",
               model=None):
    """ISPF option 1 (View) / option 2 (Edit): prompt for a panel-library member
    on ``entry_panel`` and open its source (as ``verb`` — BROWSE or EDIT). An
    unknown member is reported via &VIEWMSG; PF3/PF15 returns."""
    from dtl import load_panel

    msg = ""
    while True:
        screen = load_panel(entry_panel, VIEWMSG=msg)
        action = _await_action(client_socket, screen)
        if action is _LEAVE:
            return
        _aid_str, fields, _cursor = action
        member = (fields.get(screen.field_addr("member")) or "").strip()
        if not member:
            continue
        path = _member_path(member)
        if path:
            _show_browse(client_socket, member, path, verb=verb, model=model)
            msg = ""
        else:
            msg = f"MEMBER {member.upper()} NOT FOUND"


def _show_member_list(client_socket, model=None):
    """Utilities -> Library (3.1): the panel-library member list, with ISPF
    point-and-shoot — put the cursor on a member row and press Enter to browse
    that member's source. PF7/PF8 page the list; PF3/PF15 returns. On a larger
    terminal (model 3/4/5) the list is drawn on the alternate screen, so more
    members are shown per page."""
    from dtl import load_panel
    from screen import Text, DisplayIntensity

    rows, cols = _screen_size(model)
    alternate = rows > 24 or cols > 80
    # The <lstfld> data rows start at row 6; leave the last two rows for the
    # footer and its rule. That's 16 rows on a model 2, more on a larger screen.
    page = rows - 8
    members = _library_members()
    top = 0
    while True:
        top = max(0, min(top, max(0, len(members) - 1)))
        window = members[top:top + page]
        foot = (f"Member {top + 1}-{top + len(window)} of {len(members)}"
                "     PF7=Up  PF8=Down  PF3=Exit")[:cols - 1]
        screen = load_panel("memlist", rows=window,
                            screen_rows=rows, screen_cols=cols)
        screen.alternate = alternate
        screen.add(Text(rows - 2, 0, foot, DisplayIntensity.HIGH))
        screen.add(Text(rows - 1, 0, "-" * (cols - 1), DisplayIntensity.HIGH))
        # Map each rendered data row to its member (the member name renders as a
        # Text in the Name column); the cursor's row then selects the member.
        member_by_row = {}
        for m in window:
            for it in screen.items:
                if isinstance(it, Text) and it.text == m["mname"]:
                    member_by_row[it.row] = m["mname"]
                    break
        action = _await_action(client_socket, screen)
        if action is _LEAVE:
            return
        aid_str, _fields, cursor = action
        if aid_str in ("PF8", "PF20"):
            top += page
        elif aid_str in ("PF7", "PF19"):
            top -= page
        elif aid_str == "Enter" and cursor is not None:
            member = member_by_row.get(cursor // cols)   # width-aware row decode
            if member:
                path = _member_path(member)
                if path:
                    _show_browse(client_socket, member, path, model=model)
        # otherwise just redisplay the current page


def _show_submenu(client_socket, panel_name: str, leaves=None, initial=None):
    """Display a nested selection menu (e.g. option 3, Utilities) and drive it
    like the Primary Option Menu: read the option from the panel's <cmdarea>,
    validate it against the panel's <choice> selections. A leaf listed in
    ``leaves`` ({option: handler(client_socket)}) invokes its handler to show
    that sub-panel; any other valid option reports back via &SELMSG. PF3/PF15
    returns; PF1 shows help.

    ``initial`` pre-selects a sub-option without displaying the menu first, so a
    dotted jump from the parent (``3.1``) lands straight on the leaf; PF3 from
    there falls back to this menu."""
    from dtl import load_panel

    leaves = leaves or {}
    msg = ""
    pending = (initial or "").strip().upper() or None
    while True:
        screen = load_panel(panel_name, SELMSG=msg)
        if pending is not None:
            opt, pending = pending, None
        else:
            action = _await_action(client_socket, screen)
            if action is _LEAVE:
                return
            aid_str, fields, cursor = action
            opt = (screen.command_value(fields) or "").strip().upper()
            # Point-and-shoot: Enter on a choice row selects it when nothing typed.
            if not opt and aid_str == "Enter":
                opt = screen.selection_at(cursor) or ""
            if not opt:
                continue
        # A further dotted tail would forward into a deeper leaf; we nest one
        # level, so select on the head and ignore any deeper segment.
        head = opt.split(".", 1)[0]
        if head in leaves:
            leaves[head](client_socket)  # a handler that shows the leaf sub-panel
            msg = ""
        elif head in screen.selections:
            msg = f"OPTION {head} ({screen.selections[head].strip()}) NOT YET IMPLEMENTED"
        else:
            msg = f"INVALID OPTION: {opt}"


def _show_overlay(client_socket, panel_name: str, rows=None, enter_returns=True):
    """Display an overlay panel (help or sub-panel) and wait for the user to
    leave it. The underlying panel is re-sent by the caller's loop on return —
    mirroring ISPF's PF1 HELP and option-select behaviour.

    ``rows`` populates a ``<lstfld>`` list/table on the panel (e.g. the Dialog
    Test variable display), passed straight through to ``load_panel``.

    ``enter_returns`` controls what a bare Enter does. Help panels dismiss on
    Enter (the default); a read-only display/table panel (member list, variable
    display) sets it False so Enter just redisplays and only PF3/PF15 exits —
    the way ISPF treats those panels.
    """
    from dtl import load_panel

    abc_idx = None  # which action-bar choice the cursor is parked on, or None
    while True:
        screen = load_panel(panel_name, rows=rows)
        if abc_idx is not None and screen.action_bar:
            ch = screen.action_bar[abc_idx % len(screen.action_bar)]
            screen.cursor_at = (ch["row"], ch["col"] + 1)  # on the choice label
        _send_screen(client_socket, screen)
        result = read_client_input(client_socket)
        if result is None:
            return
        aid, fields, cursor = result
        aid_str = aid_to_string(aid)
        if screen.command_for(aid_str) in _LEAVE_COMMANDS:
            return
        if screen.action_bar and aid_str in ("PF10", "PF11"):
            # F10/F11 move the cursor left/right along the action-bar choices
            # (jumping onto the bar from elsewhere), wrapping around.
            n = len(screen.action_bar)
            if abc_idx is None:
                abc_idx = 0 if aid_str == "PF11" else n - 1
            else:
                abc_idx = (abc_idx + 1) % n if aid_str == "PF11" else (abc_idx - 1) % n
            continue
        if aid_str == "Enter":
            # Point-and-shoot: Enter with the cursor on an action-bar choice
            # opens that choice's pull-down; otherwise Enter dismisses the
            # overlay (help panels) or just redisplays it (display panels).
            choice = screen.action_choice_at(cursor)
            if choice and choice.get("pdc"):
                action = _show_pulldown(client_socket, screen, choice)
                if action is None:
                    return  # client disconnected
                if _run_pdc_action(client_socket, screen, action):
                    return  # the action left the overlay (e.g. EXIT)
                continue
            if enter_returns:
                return
            continue  # display panel: Enter stays; only PF3/PF15 exits


def _pdc_item_text(row, col, number, item, inner):
    """Build one framed pull-down item line ``| N. label |``, underlining the
    item's mnemonic letter (DTL ``<M>``) when it has one. Mono renders identically
    to the plain framed line, so only colour/extended terminals show the underline."""
    from screen import Text, Highlight
    label = item["label"]
    t = f"{number}. {label}"
    framed = "|" + (" " + t).ljust(inner) + "|"
    m = item.get("mnemonic")
    if m is not None:
        pos = 2 + (len(t) - len(label)) + m      # past ``| `` and the ``N. `` prefix
        if 0 <= pos < len(framed):
            runs = [(framed[:pos], None, None),
                    (framed[pos], None, Highlight.UNDERSCORE),
                    (framed[pos + 1:], None, None)]
            return Text.rich(row, col, [r for r in runs if r[0]],
                             intensity=DisplayIntensity.HIGH)
    return Text(row, col, framed, DisplayIntensity.HIGH)


def _show_pulldown(client_socket, screen, choice):
    """Overlay a choice's pull-down menu and wait for the user to act on it.

    Returns the selected pull-down item's action string when the cursor is on an
    item and Enter is pressed; ``""`` if the pull-down is closed without a
    selection; or ``None`` if the client disconnected.
    """
    from screen import Text

    pdc = choice["pdc"]
    texts = [f"{n}. {p['label']}" for n, p in enumerate(pdc, 1)]
    inner = max(len(t) for t in texts) + 2
    top = choice["row"] + 1
    col = choice["col"]
    border = "+" + "-" * inner + "+"
    screen.add(Text(top, col, border, DisplayIntensity.HIGH))
    action_by_row = {}
    for n, item in enumerate(pdc):
        row = top + 1 + n
        screen.add(_pdc_item_text(row, col, n + 1, item, inner))
        action_by_row[row] = item["action"]
    screen.add(Text(top + 1 + len(texts), col, border, DisplayIntensity.HIGH))
    _send_screen(client_socket, screen)

    result = read_client_input(client_socket)
    if result is None:
        return None
    aid, _, cursor = result
    if aid_to_string(aid) == "Enter" and cursor is not None:
        crow, ccol = divmod(cursor, 80)
        if crow in action_by_row and col <= ccol <= col + inner + 1:
            return action_by_row[crow]
    return ""  # closed without selecting an item


def _run_pdc_action(client_socket, screen, action) -> bool:
    """Run a selected pull-down action. Returns True if it should leave the
    overlay (an EXIT-family alias), False otherwise (the panel is redisplayed).
    """
    act = (action or "").strip().lower()
    if act.startswith("alias "):
        target = act.split(None, 1)[1].strip()
        if target == "help" and screen.help:
            _show_overlay(client_socket, screen.help)
            return False
        if target in ("exit", "end", "return"):
            return True
    return False  # passthru / unknown / no selection: just redisplay


def aid_to_string(aid: int):
    aid_codes = {
        0x60: "No AID",
        0x7D: "Enter",
        0x6D: "Clear",
        0x6C: "PA1",
        0x6E: "PA2",
        0x6B: "PA3",
        0xF1: "PF1",
        0xF2: "PF2",
        0xF3: "PF3",
        0xF4: "PF4",
        0xF5: "PF5",
        0xF6: "PF6",
        0xF7: "PF7",
        0xF8: "PF8",
        0xF9: "PF9",
        0x7A: "PF10",
        0x7B: "PF11",
        0x7C: "PF12",
        0xC1: "PF13",
        0xC2: "PF14",
        0xC3: "PF15",
        0xC4: "PF16",
        0xC5: "PF17",
        0xC6: "PF18",
        0xC7: "PF19",
        0xC8: "PF20",
        0xC9: "PF21",
        0x4A: "PF22",
        0x4B: "PF23",
        0x4C: "PF24",
    }
    return aid_codes.get(aid, f"Unknown AID {hex(aid)}")


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


def _parse_3270_reply(buffer):
    """Decode one inbound 3270 AID reply into ``(aid, {addr: text}, cursor)``.

    Byte 0 is the AID. A normal (non short-read) reply then carries the 12-bit
    cursor address, followed by SBA-addressed modified fields; short reads
    (CLEAR/PA) and synthetic test payloads start straight into an SBA/SF order.
    """
    aid = buffer[0]
    # Log only the AID (not the raw bytes) to avoid leaking password data in logs
    print(f"RX: {len(buffer)} bytes, AID: {aid_to_string(aid)}")

    SBA_ORD = 0x11
    SF_ORD = 0x1D
    cursor = None
    if len(buffer) >= 3 and buffer[1] not in (SBA_ORD, SF_ORD):
        cursor = ((buffer[1] & 0x3F) << 6) | (buffer[2] & 0x3F)

    results = {}
    i = 1
    while i < len(buffer):
        if buffer[i] == SBA_ORD and i + 2 < len(buffer):
            addr_hi, addr_lo = buffer[i + 1], buffer[i + 2]
            addr = ((addr_hi & 0x3F) << 6) | (addr_lo & 0x3F)
            i += 3
            field_bytes = bytearray()
            while i < len(buffer) and buffer[i] not in (SBA_ORD, SF_ORD):
                field_bytes.append(buffer[i])
                i += 1
            field_text = from_ebcdic(field_bytes).strip()
            if field_text:
                results[addr] = field_text
        else:
            i += 1

    return aid, results, cursor


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
            return _parse_3270_reply(ev[2])   # ("record", data_type, payload)

    buffer = read_record(client_socket)
    if not buffer:
        return None
    return _parse_3270_reply(buffer)


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


# ISPF commands that leave the current panel. A panel's <keyl> binds function
# keys (PF3/PF15) to one of these; the session loop acts on the resolved command
# rather than hard-coding key numbers.
_LEAVE_COMMANDS = {"EXIT", "END", "RETURN", "LOGOFF"}

# Primary-menu options that open a nested selection sub-menu (option -> panel).
# Driven uniformly through _show_submenu, so each supports the dotted jump
# (e.g. "9.2") and reports an unimplemented leaf via &SELMSG.
_SUBMENUS = {
    "4": "foreground",   # Foreground language processing
    "5": "batch",        # Batch language processing
    "9": "ibmprod",      # IBM Products
    "10": "sclm",        # SCLM
    "12": "zsystem",     # z/OS System programmer applications
    "13": "zuser",       # z/OS User applications
}

_message_catalog = None


def _messages():
    """Lazily load and cache the TSO message catalog (messages/tsomsgs.dtl)."""
    global _message_catalog
    if _message_catalog is None:
        from dtl import load_message_member  # lazy: avoid circular import
        _message_catalog = load_message_member("tsomsgs")
    return _message_catalog


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
    client_socket.settimeout(600)
    # Record the session's colour capability so every panel renders in colour on
    # a colour terminal (see _send_screen / _session).
    _session.color = _wants_color(model)
    # Pick the session's EBCDIC code page from the terminal's discovered base
    # character set (#137), so text is encoded/decoded in the page the terminal
    # actually uses (e.g. cp273 for a German ws3270), defaulting to US cp037.
    _session.code_page = code_page_for_model(model)
    # If BIND-IMAGE was negotiated, bind the LU-LU session before the first
    # screen: the client won't accept 3270-DATA until it has seen a BIND, and
    # being bound is what lets its ATTN key reach us (RFC 2355 §10.3).
    if model.tn3270e_bind_image:
        client_socket.send_bind()

    while True:
        # Logon loop
        error_msg = None
        error_alarm = False
        userid = None
        while True:
            screen = send_tso_logon(client_socket, error_msg, model=model,
                                    alarm=error_alarm)
            result = read_client_input(client_socket)
            if result is None:
                return
            aid, fields, cursor = result
            print(f"AID={hex(aid)}, fields={redact_fields(fields)}")

            aid_str = aid_to_string(aid)
            cmd = screen.command_for(aid_str)
            if cmd in _LEAVE_COMMANDS:
                # Keylist bound this key (PF3/PF15) to EXIT — log off.
                return
            if cmd == "HELP":
                # Field-level help (cursor on a field with its own help) wins over
                # the panel's general help.
                help_panel = screen.help_for(cursor) or screen.help
                if help_panel:
                    _show_overlay(client_socket, help_panel)
                    continue

            # Validate fields against their <varclass> checks (e.g. SIZE range)
            # before processing the logon, as ISPF validates panel fields.
            verr = screen.first_validation_error(fields)
            if verr:
                msgid, subs = verr
                error_msg = _messages().format(msgid, **subs)
                error_alarm = _messages().alarm(msgid)
                continue

            userid_raw = fields.get(LOGON_USERID_ADDR, "").strip().upper()
            password_raw = fields.get(LOGON_PASSWORD_ADDR, "").strip().upper()

            if not userid_raw:
                error_msg = _messages().format("IKJ56700I")
                error_alarm = _messages().alarm("IKJ56700I")
                continue

            if _CREDENTIALS.get(userid_raw) != password_raw:
                error_msg = _messages().format("IKJ56425I", USERID=userid_raw)
                error_alarm = _messages().alarm("IKJ56425I")
                continue

            userid = userid_raw
            break

        # ISPF menu loop
        short_msg = None
        screen = None
        # Only repaint the whole panel when it actually changed — the first time
        # in, and after any sub-panel that overwrote the screen. A stay-on-the-
        # menu message (INVALID OPTION, …) is instead patched in place with a
        # plain Write, so the option the user typed survives the redisplay.
        needs_full_redraw = True
        while True:
            if needs_full_redraw:
                screen = send_ispf_menu(client_socket, userid, short_msg)
            else:
                _update_menu_message(client_socket, screen, short_msg)
            result = read_client_input(client_socket)
            if result is None:
                return
            aid, fields, cursor = result
            print(f"AID={hex(aid)}, fields={redact_fields(fields)}")
            # Assume the next outcome repaints the panel (a sub-panel, or exit).
            # The stay-on-the-menu message branches below flip this off so the
            # message is patched in place with a plain Write instead.
            needs_full_redraw = True

            aid_str = aid_to_string(aid)
            # Read the option from the panel's <cmdarea> (its ZCMD command
            # field), resolved by role rather than a hard-coded address.
            option = (screen.command_value(fields) or "").strip().upper()
            # Point-and-shoot: with no typed option, Enter on a choice row picks it.
            if not option and aid_str == "Enter":
                option = screen.selection_at(cursor) or ""

            cmd = screen.command_for(aid_str)
            if option == "X" or cmd in _LEAVE_COMMANDS:
                # X, or a keylist key (PF3/PF15) bound to EXIT — back to logon
                break
            if cmd == "HELP":
                help_panel = screen.help_for(cursor) or screen.help
                if help_panel:
                    _show_overlay(client_socket, help_panel)
                    continue

            # A typed value is a menu selection, a command from the panel's
            # <cmdtbl>, or invalid. (The "X" exit choice is handled above.)
            head = option.split(".", 1)[0]
            tail = option.split(".", 1)[1] if "." in option else None
            if option == "0":
                # Option 0 (Settings) opens a real sub-panel (with an action bar).
                _show_overlay(client_socket, "settings")
                short_msg = None
            elif option == "1":
                # Option 1 (View) prompts for a member and browses its source.
                _show_view(client_socket, model=model)
                short_msg = None
            elif option == "2":
                # Option 2 (Edit) prompts for a member and opens it (view-only).
                _show_view(client_socket, entry_panel="editentry", verb="EDIT",
                           model=model)
                short_msg = None
            elif option == "11":
                # Option 11 (Workplace) opens an informational panel.
                _show_overlay(client_socket, "workplace")
                short_msg = None
            elif head == "3":
                # Option 3 (Utilities) opens a nested selection sub-menu; its
                # Library leaf (3.1) lists the real panel-library members. A
                # dotted path (e.g. "3.1") jumps straight to the sub-option,
                # ISPF-style, without stopping at the utility menu first.
                _show_submenu(client_socket, "utility",
                              leaves={"1": lambda cs: _show_member_list(cs, model=model)},
                              initial=tail)
                short_msg = None
            elif option == "6":
                # Option 6 (Command) opens a TSO Command Shell sub-panel.
                _show_command_shell(client_socket)
                short_msg = None
            elif option == "7":
                # Option 7 (Dialog Test) opens the variable-display sub-panel,
                # its <lstfld> table populated from the live session variables.
                # It's a display panel: Enter stays, only PF3 exits.
                _show_overlay(client_socket, "dlgtest",
                              rows=_dialog_vars(userid, model),
                              enter_returns=False)
                short_msg = None
            elif head in _SUBMENUS:
                # Options 4/5/9/10/12/13 open their nested selection sub-menu,
                # supporting the same dotted jump (e.g. "9.2") as Utilities.
                _show_submenu(client_socket, _SUBMENUS[head], initial=tail)
                short_msg = None
            elif option in screen.selections:
                short_msg = f"OPTION {option} NOT YET IMPLEMENTED"
                needs_full_redraw = False   # patch the message, keep the input
            elif option:
                action = screen.lookup_command(option)
                if action and action.lower().startswith("alias ") \
                        and action.split()[1].upper() in _LEAVE_COMMANDS:
                    break  # e.g. BYE -> "alias exit" leaves ISPF
                elif action:
                    short_msg = f"COMMAND {option} NOT YET IMPLEMENTED"
                    needs_full_redraw = False
                else:
                    short_msg = f"INVALID OPTION: {option}"
                    needs_full_redraw = False
            else:
                short_msg = None
                needs_full_redraw = False    # bare Enter: keep whatever was typed


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
