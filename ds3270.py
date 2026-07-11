"""Pure 3270 data-stream codec primitives.

The leaf module of the stack: the order constants, coded-address packing,
field-attribute and WCC encoding, and EBCDIC text conversion shared by the
render model (:mod:`screen`), the DTL front-end (:mod:`dtl`), and the protocol
layer (:mod:`server`). It imports nothing from any of them, so the render/codec
core is importable and testable with no sockets.
"""

import threading
from dataclasses import dataclass
from enum import Enum


# Per-connection session context. Each client is served on its own thread (see
# server._client_thread), so its colour capability and EBCDIC code page are
# recorded here once after negotiation and read by the render/encode helpers for
# every panel — without threading flags through each one. Defined before the
# encode helpers below because a module-level constant (server._BIND_IMAGE)
# encodes text at import time.
_session = threading.local()

# The EBCDIC code page used to encode/decode text when a session hasn't chosen a
# different one. cp037 (US) is what the bundled panels assume, so it stays the
# default — leaving the mono data stream byte-for-byte unchanged.
DEFAULT_CODE_PAGE = "cp037"


@dataclass(frozen=True)
class SessionContext:
    """One connection's session context as an explicit value (#352): the EBCDIC
    code page text is encoded/decoded in, whether the terminal renders colour
    (extended attributes), and the negotiated terminal model (a
    ``server.TerminalModel``, held opaquely so this module stays dependency-free).

    Passing one of these explicitly — ``Screen.render(session=...)``,
    ``session.run(ctx=...)`` — makes rendering deterministic with no thread-local
    priming. The :data:`_session` thread-local remains as the compatibility shim
    for the not-yet-migrated ambient readers; :func:`activate_session` and
    :func:`current_session` are its write and read halves."""

    code_page: str = DEFAULT_CODE_PAGE
    color: bool = False
    model: object = None

    def encode(self, s: str, errors: str = "strict") -> bytes:
        """Encode text to EBCDIC in this session's code page."""
        return s.encode(self.code_page, errors)

    def decode(self, b, errors: str = "strict") -> str:
        """Decode EBCDIC to text in this session's code page."""
        return bytes(b).decode(self.code_page, errors)


def current_session() -> SessionContext:
    """This thread's ambient session state as an explicit :class:`SessionContext`
    — the read half of the compatibility shim. Migrated code takes a
    SessionContext parameter and falls back here only when its caller passed
    none, so an unmigrated caller keeps today's behaviour."""
    return SessionContext(
        code_page=getattr(_session, "code_page", DEFAULT_CODE_PAGE),
        color=getattr(_session, "color", False),
        model=getattr(_session, "model", None),
    )


def activate_session(ctx: SessionContext) -> None:
    """Install ``ctx`` as this thread's ambient session — the write half of the
    compatibility shim. ``server.handle_client`` calls this once after
    negotiation so ambient readers that are not yet migrated (the module-level
    :func:`to_ebcdic`/:func:`from_ebcdic` in the reply parser, tests that poke
    :data:`_session`) agree with the explicit context threaded through the
    application."""
    _session.code_page = ctx.code_page
    _session.color = ctx.color
    _session.model = ctx.model


def _session_code_page() -> str:
    """The current session's EBCDIC code page (thread-local, see :data:`_session`),
    or the US default when none was negotiated/selected."""
    return getattr(_session, "code_page", DEFAULT_CODE_PAGE)


def to_ebcdic(s: str, code_page: str = None, errors: str = "strict") -> bytes:
    """Encode text to EBCDIC. Uses the session's code page (the thread-local
    compatibility shim) unless ``code_page`` is given — the explicit argument
    lets a caller override per field (e.g. a future mixed-CCSID panel) without
    touching the session default. New code should prefer carrying an explicit
    :class:`SessionContext` and calling :meth:`SessionContext.encode`."""
    return s.encode(code_page or _session_code_page(), errors)


def from_ebcdic(b: bytes, code_page: str = None, errors: str = "strict") -> str:
    """Decode EBCDIC to text, mirroring :func:`to_ebcdic`'s code-page resolution."""
    return bytes(b).decode(code_page or _session_code_page(), errors)


# The largest presentation space the 12-bit coded-address pipeline can serve
# (#348). A 12-bit address tops out at 4095, but encode_pack_addr sets the top
# two bits of the high byte (0b11), so addresses 4032-4095 (high chunk 0x3F)
# would encode to a high byte of 0xFF — a raw Telnet IAC that outbound screens
# do not escape, mis-framing the record at the terminal. Capping at 0x0FC0
# keeps 0xFF out of every coded address; every model 2-5 geometry (up to
# 43x80 = 3440 and 27x132 = 3564 cells) fits well inside it.
MAX_ADDRESSABLE_CELLS = 0x0FC0   # 4032 cells


def encode_pack_addr(row: int, col: int, cols=80) -> bytes:
    """Encodes a 12-bit 3270 presentation space address from row/col.

    Addresses ``>= MAX_ADDRESSABLE_CELLS`` (0x0FC0) raise: they would need a
    high byte of 0xFF, a raw Telnet IAC on the wire (see #348)."""
    addr = row * cols + col
    if addr < 0 or addr >= MAX_ADDRESSABLE_CELLS:
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
    detectable: bool = False,
) -> int:
    attr = _FA_BASE
    if display == DisplayIntensity.HIGH:
        attr |= 0x08          # FA_INT_HIGH_SEL (bits 3-2 = 10): intensified + detectable
    elif display == DisplayIntensity.HIGHLIGHTED:
        attr |= 0x04          # FA_INT_NORM_SEL (bits 3-2 = 01)
    elif display == DisplayIntensity.NON_DISPLAY:
        attr |= 0x0C          # FA_INT_ZERO_NSEL (bits 3-2 = 11)
    elif detectable:
        # A normal-intensity selector-pen/cursor-select DETECTABLE field: display
        # bits 01 (FA_INT_NORM_SEL). Intensified (HIGH, bits 10) is already
        # detectable; non-display (bits 11) can never be. See #104.
        attr |= 0x04
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


# AID (attention identifier) byte -> key name. Pure codec vocabulary: the first
# byte of every inbound 3270 reply names the key that submitted it. Shared by
# the protocol layer (reply parsing/logging in :mod:`server`) and the
# application (:mod:`session`), which routes on the decoded name.
_AID_CODES = {
    0x60: "No AID",
    0x7D: "Enter",
    0x7E: "CursorSelect",   # selector-pen / Cursor Select attention (#104)
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


def aid_to_string(aid: int):
    return _AID_CODES.get(aid, f"Unknown AID {hex(aid)}")
