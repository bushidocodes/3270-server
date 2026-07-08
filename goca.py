"""GDDM-style vector graphics: a minimal GOCA drawing-order builder (#309).

This is the largest, lowest-priority slice of the #102 protocol audit. The server
sends only alphanumeric 3270 data; drawing vectors needs the **graphic orders** of
GOCA (Graphics Object Content Architecture — the order set GDDM and the ISPF Graphic
Interface use for a graphics presentation space). This module builds a *minimal,
concrete* subset — enough to draw line segments and a filled rectangle — as the
issue scopes it, rather than the whole order set.

**Scope and verification.** These functions build the GOCA *drawing-order* bytes
(the graphics-segment payload). They are byte-structure builders only: the 3270
carriage that delivers a segment to a graphics presentation space (a graphics
partition plus the vendor-specific graphics-data structured field) is device- and
GDDM-specific and out of scope here — and unverifiable on the emulators this repo
tests against (``ws3270`` renders text, not vectors; ``x3270``'s graphics support is
partial). So, exactly as the issue asks, verification is **byte-structure only**:
each builder's layout is unit-tested against the GOCA reference, not driven through a
real terminal. Nothing in the bundled session emits graphics; this is opt-in
capability that also depends on the terminal advertising a graphics capability in
its Query Reply.

**Order formats** (GOCA for AFP Reference, "Drawing Orders"):

* *Fixed 1-byte* — order code only (No-Operation).
* *Fixed 2-byte* — order code + one data byte (the attribute-set orders).
* *Long* — order code + a 1-byte length + that many operand bytes.

Coordinates are 16-bit two's-complement big-endian signed integers (SBIN); the
order codes and operand layouts below are quoted from the GOCA reference's
"Summary List of Orders" and the per-order syntax tables.
"""
from __future__ import annotations

import struct
from typing import Iterable, Sequence, Tuple

# ── order codes (GOCA "Summary List of Orders") ──────────────────────────────
GNOP1 = 0x00     # No-Operation (fixed 1-byte)
GSCOL = 0x0A     # Set Color (fixed 2-byte)
GSMX = 0x0C      # Set Mix (fixed 2-byte)
GSLT = 0x18      # Set Line Type (fixed 2-byte)
GSLW = 0x19      # Set Line Width (fixed 2-byte)
GSCP = 0x21      # Set Current Position (long)
GEAR = 0x60      # End Area (long)
GBAR = 0x68      # Begin Area (long)
GBOX = 0xC0      # Box at Given Position (long)
GLINE = 0xC1     # Line at Given Position (long)
GCHST = 0xC3     # Character String at Given Position (long)
GFARC = 0xC7     # Full Arc at Given Position (long)

# ── Set Color (GSCOL) values (the 1 data byte; the drawing default is 0) ──────
COLOR_DEFAULT = 0x00
COLOR_BLUE = 0x01
COLOR_RED = 0x02
COLOR_MAGENTA = 0x03
COLOR_GREEN = 0x04
COLOR_CYAN = 0x05
COLOR_YELLOW = 0x06
COLOR_NEUTRAL = 0x07   # white on a dark background
COLOR_BLACK = 0x08

# ── Set Line Type (GSLT) values ──────────────────────────────────────────────
LINE_DEFAULT = 0x00
LINE_DOTTED = 0x01
LINE_SHORT_DASHED = 0x02
LINE_DASH_DOT = 0x03
LINE_LONG_DASHED = 0x05
LINE_SOLID = 0x07
LINE_INVISIBLE = 0x08

# ── Begin Area (GBAR) flag bits ──────────────────────────────────────────────
_GBAR_RES1 = 0x80          # reserved-for-migration; generators must set it to 1
GBAR_BOUNDARY = 0x40       # draw the area's boundary lines
GBAR_INSIDE_NONZERO = 0x20  # nonzero-winding interior (else alternate mode)

Point = Tuple[int, int]


def _sbin(n: int) -> bytes:
    """A GOCA coordinate: a 16-bit two's-complement big-endian signed integer."""
    return struct.pack(">h", n)


def _long(code: int, data: bytes) -> bytes:
    """A long-format order: ``code`` + a 1-byte operand length + the operand data
    (the length counts only the operand data, not the code or length byte)."""
    if len(data) > 255:
        raise ValueError("long-format order operand exceeds 255 bytes")
    return bytes([code, len(data)]) + data


def nop() -> bytes:
    """No-Operation (GNOP1) — a 1-byte order used to align following orders."""
    return bytes([GNOP1])


def set_color(color: int) -> bytes:
    """Set Color (GSCOL): the drawing colour of following primitives (2-byte
    order; ``color`` is one of the ``COLOR_*`` values)."""
    return bytes([GSCOL, color & 0xFF])


def set_mix(mix: int) -> bytes:
    """Set Mix (GSMX): how a primitive's colour combines with the destination."""
    return bytes([GSMX, mix & 0xFF])


def set_line_type(line_type: int) -> bytes:
    """Set Line Type (GSLT): the style (solid/dashed/…) of following lines."""
    return bytes([GSLT, line_type & 0xFF])


def set_line_width(width: int) -> bytes:
    """Set Line Width (GSLW): the width multiple of following lines."""
    return bytes([GSLW, width & 0xFF])


def set_current_position(x: int, y: int) -> bytes:
    """Set Current Position (GSCP): move the current position to ``(x, y)`` in the
    graphics presentation space. Long format, operand = one SBIN point (4 bytes)."""
    return _long(GSCP, _sbin(x) + _sbin(y))


def line(points: Sequence[Point]) -> bytes:
    """Line at Given Position (GLINE): draw connected straight segments through
    ``points`` (the first is the start point, each subsequent one an endpoint).
    Long format; the operand is the SBIN coordinate pairs (a multiple of 4 bytes)."""
    if len(points) < 2:
        raise ValueError("a line needs at least a start point and one endpoint")
    data = b"".join(_sbin(x) + _sbin(y) for x, y in points)
    return _long(GLINE, data)


def box(x0: int, y0: int, x1: int, y1: int) -> bytes:
    """Box at Given Position (GBOX): a rectangle with opposite corners ``(x0, y0)``
    and ``(x1, y1)``. Long format; the operand is a 2-byte reserved field (0) then
    the two SBIN corner points (GOCA *Box at Given Position*)."""
    data = b"\x00\x00" + _sbin(x0) + _sbin(y0) + _sbin(x1) + _sbin(y1)
    return _long(GBOX, data)


def begin_area(boundary: bool = True, inside_nonzero: bool = False) -> bytes:
    """Begin Area (GBAR): start a filled-area definition. ``boundary`` draws the
    boundary lines; ``inside_nonzero`` selects nonzero-winding fill (else alternate
    mode). The reserved RES1 bit is always set, per the reference. Terminate the
    area with :func:`end_area`; the primitives between the two define the boundary."""
    flags = _GBAR_RES1
    if boundary:
        flags |= GBAR_BOUNDARY
    if inside_nonzero:
        flags |= GBAR_INSIDE_NONZERO
    return _long(GBAR, bytes([flags]))


def end_area() -> bytes:
    """End Area (GEAR): close the area opened by :func:`begin_area` and fill it.
    Long format with an empty operand."""
    return _long(GEAR, b"")


def character_string(x: int, y: int, code_points: bytes) -> bytes:
    """Character String at Given Position (GCHST): draw ``code_points`` starting at
    ``(x, y)``. Long format; operand = the SBIN origin point then the raw code
    points (already in the graphics character set's encoding)."""
    return _long(GCHST, _sbin(x) + _sbin(y) + bytes(code_points))


def full_arc(x: int, y: int, mult_int: int = 1, mult_frac: int = 0) -> bytes:
    """Full Arc at Given Position (GFARC): a circle/ellipse centred at ``(x, y)``.
    Long format; operand = the SBIN centre point, then the multiplier's integer
    (``mult_int``) and fractional (``mult_frac``) bytes (GOCA *Full Arc*). The arc's
    shape/orientation come from the current arc parameters."""
    return _long(GFARC, _sbin(x) + _sbin(y) + bytes([mult_int & 0xFF, mult_frac & 0xFF]))


def segment(orders: Iterable[bytes]) -> bytes:
    """Concatenate ``orders`` into a graphics-segment drawing-order sequence — the
    payload a graphics presentation space executes. (The 3270 carriage that delivers
    it to the terminal is out of scope; see the module docstring.)"""
    return b"".join(orders)


def filled_rectangle(x0: int, y0: int, x1: int, y1: int,
                     color: int = COLOR_DEFAULT, boundary: bool = True) -> bytes:
    """A convenience drawing: a filled rectangle in ``color`` with corners
    ``(x0, y0)`` / ``(x1, y1)`` — Set Color, then a Begin Area / Box / End Area
    bracket. Returns the concatenated order bytes."""
    return segment([
        set_color(color),
        begin_area(boundary=boundary),
        box(x0, y0, x1, y1),
        end_area(),
    ])


def polyline(points: Sequence[Point], color: int = COLOR_DEFAULT,
             line_type: int = LINE_SOLID) -> bytes:
    """A convenience drawing: a coloured, styled polyline through ``points`` — Set
    Color, Set Line Type, then a single Line order."""
    return segment([
        set_color(color),
        set_line_type(line_type),
        line(points),
    ])
