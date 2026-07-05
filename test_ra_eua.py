"""Tests for the RA (Repeat to Address) and EUA (Erase Unprotected to Address) orders.

RA compacts a run of one repeated character (a rule line / fill) into a single
4-byte order instead of one byte per character — the rendered result is identical,
only the wire stream is shorter. EUA nulls every unprotected (input) field while
leaving protected text intact — the native "clear the entry fields" operation.
"""
import server
from screen import (
    Screen, Text, Field, WRITE, RA, EUA, SBA, IC, _RA_MIN_RUN,
)
from dtl import load_panel


def _render(item, color=False, cols=80):
    buf = bytearray()
    item.render(buf, color=color, cols=cols)
    return bytes(buf)


# ── RA: repeat to address ────────────────────────────────────────────────────

def test_long_repeat_run_uses_ra():
    out = _render(Text(1, 0, "-" * 20))
    assert RA in out
    ra = out.index(RA)
    assert out[ra + 3] == 0x60                 # RA fills with EBCDIC dash '-'
    assert b"\x60" * 20 not in out             # ...not 20 literal dashes


def test_ra_stop_address_is_after_the_run():
    # Field start at col 0 occupies col 0; the run is cols 1..20, so RA stops at
    # linear address 1*80 + 0 + 1 + 20 = 101 = (row 1, col 21).
    out = _render(Text(1, 0, "-" * 20))
    ra = out.index(RA)
    assert out[ra + 1:ra + 3] == server.encode_pack_addr(1, 21)


def test_ra_stop_wraps_at_the_buffer_end():
    # A full-width rule on the bottom row fills through the very last cell. The
    # 3270 buffer is circular, so the RA stop must wrap to address 0 rather than
    # encode cols*rows (1920 on a 24x80 screen), which real terminals reject as
    # "RA address 1920 > maximum 1919".
    buf = bytearray()
    Text(23, 0, "-" * 79).render(buf, cols=80, rows=24)   # ends at cell 1919
    ra = buf.index(RA)
    assert buf[ra + 1:ra + 3] == server.encode_pack_addr(0, 0)   # stop = address 0


def test_ra_stop_wraps_on_the_alternate_screen():
    # Same boundary on a model-3 (32x80) alternate screen: the last cell is 2559,
    # so a bottom-row rule wraps its stop to 0 against the 2560-cell buffer.
    buf = bytearray()
    Text(31, 0, "-" * 79).render(buf, cols=80, rows=32)
    ra = buf.index(RA)
    assert buf[ra + 1:ra + 3] == server.encode_pack_addr(0, 0)


def test_short_run_stays_literal():
    # A run shorter than the break-even threshold is cheaper as literal bytes.
    out = _render(Text(1, 0, "-" * (_RA_MIN_RUN - 1)))
    assert RA not in out
    assert b"\x60" * (_RA_MIN_RUN - 1) in out


def test_mixed_text_stays_literal():
    out = _render(Text(1, 0, "-- HI --"))       # not a single repeated character
    assert RA not in out


def test_space_fill_uses_ra():
    out = _render(Text(2, 25, " " * 54))
    ra = out.index(RA)
    assert out[ra + 3] == 0x40                  # RA fills with EBCDIC space


def test_ra_compacts_the_logon_rule_lines():
    # The logon panel's column divider rules are long repeated-dash runs, so the
    # render compacts them with RA (Repeat to Address) rather than literal bytes.
    assert RA in load_panel("logon").render()


# ── EUA: erase unprotected to address ────────────────────────────────────────

def test_erase_input_is_a_write_with_eua():
    data = Screen().render_erase_input()
    assert data[0] == WRITE
    assert EUA in data
    assert data[-2:] == bytes([0xFF, 0xEF])     # IAC EOR


def test_erase_input_wcc_resets_mdt_and_restores_keyboard():
    wcc = Screen().render_erase_input()[1]
    assert wcc & 0x02                           # keyboard restore
    assert wcc & 0x01                           # reset-MDT (cleared fields are unmodified)


def test_erase_input_positions_at_top_and_erases_around():
    data = Screen().render_erase_input()
    # SBA(0,0) then EUA with a stop address of (0,0) — erases the whole buffer's
    # unprotected positions.
    top = server.encode_pack_addr(0, 0)
    assert bytes([SBA]) + top in data
    eua = data.index(EUA)
    assert data[eua + 1:eua + 3] == top


def test_erase_input_positions_cursor():
    data = Screen().render_erase_input(cursor_at=(5, 17))
    assert data[-3] == IC
    assert server.encode_pack_addr(5, 17) in data
