"""Tests for Graphic Escape (GE, 0x08) line-drawing.

GE takes the single following byte from the terminal's alternate *graphic*
character set — the 3270 line-drawing / APL glyphs — instead of the base EBCDIC
code page. :class:`GraphicText` renders a protected field of GE code points so a
panel can draw real box-drawing borders (┌─┐│└┘) rather than ASCII dashes. A run
of one repeated glyph compacts to a single GE'd RA order, exactly like a rule
line. The GE code points are from x3270's ``apl2uc[]`` table, so an emulator
renders/reads them back as the paired Unicode box-drawing characters.
"""
import server
from screen import (
    Screen, Text, GraphicText, Line, GE, RA, SF, SFE, SBA, _RA_MIN_RUN,
)


def _render(item, color=False, cols=80, rows=24):
    buf = bytearray()
    item.render(buf, color=color, cols=cols, rows=rows)
    return bytes(buf)


# ── emitting single glyphs ───────────────────────────────────────────────────

def test_single_glyph_is_ge_plus_code():
    # A lone glyph (run below the RA threshold) is a literal GE + code point.
    out = _render(GraphicText(0, 0, bytes([Line.TOP_LEFT.value])))
    ge = out.index(GE)
    assert out[ge + 1] == 0xC5                 # ┌  (U+250C)
    assert RA not in out                       # too short to compact


def test_field_is_protected_like_text():
    # GraphicText renders as a protected field: SBA + SF + protected attribute.
    out = _render(GraphicText(2, 5, bytes([Line.VERTICAL.value])))
    assert out[0] == SBA
    sf = out.index(SF)
    fa = out[sf + 1]
    assert fa & 0x20                           # protected bit set


def test_each_short_glyph_gets_its_own_ge():
    # Every graphic code needs its own GE escape (GE applies to one character).
    codes = bytes([Line.TOP_LEFT.value, Line.TOP_RIGHT.value])
    out = _render(GraphicText(0, 0, codes))
    assert out.count(bytes([GE])) == 2
    assert bytes([GE, 0xC5, GE, 0xD5]) in out


# ── run compaction: GE'd RA ──────────────────────────────────────────────────

def test_long_rule_uses_a_ge_repeat_to_address():
    # A wide rule is one RA order whose repeat character is GE-escaped:
    # RA <stop-addr> GE <glyph>  — not two bytes per cell.
    out = _render(GraphicText.rule(0, 0, 20))
    ra = out.index(RA)
    assert out[ra + 3] == GE                   # GE precedes the repeat char
    assert out[ra + 4] == Line.HORIZONTAL.value
    assert bytes([GE, Line.HORIZONTAL.value]) * 20 not in out   # not literal


def test_rule_stop_address_is_after_the_run():
    # Field attr occupies col 0; the run is cols 1..20, so RA stops at address
    # 1*80 ... i.e. row 0 col 21 = linear 21.
    out = _render(GraphicText.rule(0, 0, 20))
    ra = out.index(RA)
    assert out[ra + 1:ra + 3] == server.encode_pack_addr(0, 21)


def test_short_run_stays_literal():
    out = _render(GraphicText.rule(0, 0, _RA_MIN_RUN - 1))
    assert RA not in out
    assert bytes([GE, Line.HORIZONTAL.value]) * (_RA_MIN_RUN - 1) in out


def test_rule_stop_wraps_at_the_buffer_end():
    # Same circular-buffer rule as a plain RA (#132): a bottom-row rule filling
    # the last cell wraps its stop to address 0, not cols*rows.
    out = _render(GraphicText.rule(23, 0, 79))
    ra = out.index(RA)
    assert out[ra + 1:ra + 3] == server.encode_pack_addr(0, 0)


# ── box edges: corners around a compacted run ────────────────────────────────

def test_box_top_is_corner_run_corner():
    out = _render(GraphicText.box_top(1, 0, 40))
    sf = out.index(SF)
    # first data byte: GE + ┌
    assert out[sf + 2] == GE and out[sf + 3] == Line.TOP_LEFT.value
    # exactly one compacted horizontal run in the middle …
    assert out.count(bytes([RA])) == 1
    ra = out.index(RA)
    assert out[ra + 3] == GE and out[ra + 4] == Line.HORIZONTAL.value
    # … and it ends with GE + ┐
    assert out.rstrip(b"\x00").endswith(bytes([GE, Line.TOP_RIGHT.value]))


def test_box_bottom_uses_the_bottom_corners():
    out = _render(GraphicText.box_bottom(5, 0, 30))
    assert bytes([GE, Line.BOTTOM_LEFT.value]) in out    # └
    assert bytes([GE, Line.BOTTOM_RIGHT.value]) in out   # ┘


# ── colour parity with Text ──────────────────────────────────────────────────

def test_mono_render_has_no_extended_field_start():
    # On a mono terminal GraphicText emits plain SF (like Text), never SFE.
    mono = _render(GraphicText.rule(0, 0, 10, role="rule"), color=False)
    assert SFE not in mono
    assert SF in mono


def test_colour_render_emits_extended_field_start():
    col = _render(GraphicText.rule(0, 0, 10, role="rule"), color=True)
    assert SFE in col                          # role gives the rule a colour


# ── it composes into a Screen like any other item ────────────────────────────

def test_graphictext_renders_inside_a_screen():
    data = (Screen()
            .add(GraphicText.box_top(0, 0, 80))
            .add(Text(1, 2, "HELLO"))
            .render())
    assert data[0] == 0xF5                      # ERASE/WRITE
    assert GE in data                           # the border drew graphic glyphs
    assert data[-2:] == bytes([0xFF, 0xEF])     # IAC EOR
