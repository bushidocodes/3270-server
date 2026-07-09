"""Byte-structure tests for the minimal GOCA graphics-order builder (#309).

Verification is byte-structure only, as the issue scopes it: GOCA vector graphics
target a graphics presentation space whose 3270 carriage is device-specific and
unverifiable on the emulators this repo tests against, so we assert the drawing-order
bytes against the GOCA reference's syntax tables rather than driving a real terminal.
"""
import struct

import goca


def test_fixed_one_byte_order():
    # No-Operation is the sole fixed 1-byte order: just its order code.
    assert goca.nop() == bytes([0x00])


def test_fixed_two_byte_attribute_orders():
    # Set Color / Line Type / Line Width / Mix are order code + one data byte.
    assert goca.set_color(goca.COLOR_RED) == bytes([0x0A, 0x02])
    assert goca.set_line_type(goca.LINE_SOLID) == bytes([0x18, 0x07])
    assert goca.set_line_width(3) == bytes([0x19, 0x03])
    assert goca.set_mix(0x02) == bytes([0x0C, 0x02])


def test_set_current_position_is_a_long_order_with_one_sbin_point():
    # GSCP: 21 <len=04> <Xg SBIN> <Yg SBIN>.
    assert goca.set_current_position(10, 20) == bytes([0x21, 0x04]) + \
        struct.pack(">hh", 10, 20)


def test_coordinates_are_signed_16_bit_big_endian():
    # Negative coordinates use two's-complement SBIN.
    assert goca.set_current_position(-1, -2) == bytes([0x21, 0x04, 0xFF, 0xFF,
                                                       0xFF, 0xFE])


def test_line_encodes_each_point_and_length_is_a_multiple_of_four():
    sf = goca.line([(0, 0), (100, 50)])
    assert sf[0] == 0xC1
    assert sf[1] == 8 and sf[1] % 4 == 0          # length counts operand data only
    assert sf[2:] == struct.pack(">hhhh", 0, 0, 100, 50)


def test_line_polyline_multiple_segments():
    sf = goca.line([(0, 0), (10, 0), (10, 10)])
    assert sf[1] == 12                             # three points × 4 bytes
    assert sf[2:] == struct.pack(">hhhhhh", 0, 0, 10, 0, 10, 10)


def test_line_requires_two_points():
    import pytest
    with pytest.raises(ValueError):
        goca.line([(0, 0)])


def test_box_has_reserved_field_then_two_corner_points():
    # GBOX: C0 <len=0x0A> <RES=0000> <x0><y0><x1><y1>.
    sf = goca.box(0, 0, 100, 50)
    assert sf[:2] == bytes([0xC0, 0x0A])
    assert sf[2:4] == b"\x00\x00"                  # reserved
    assert sf[4:] == struct.pack(">hhhh", 0, 0, 100, 50)


def test_begin_area_flags_and_end_area():
    # GBAR: 68 <len=01> <flags>; RES1 always set, BOUNDARY optional, INSIDE optional.
    assert goca.begin_area() == bytes([0x68, 0x01, 0x80 | 0x40])   # RES1 | BOUNDARY
    assert goca.begin_area(boundary=False) == bytes([0x68, 0x01, 0x80])
    assert goca.begin_area(inside_nonzero=True) == bytes([0x68, 0x01,
                                                          0x80 | 0x40 | 0x20])
    # GEAR: 60 <len=00> — empty operand.
    assert goca.end_area() == bytes([0x60, 0x00])


def test_character_string_origin_then_code_points():
    # GCHST: C3 <len> <Xg><Yg> <code points…>.
    sf = goca.character_string(5, 5, b"AB")
    assert sf[0] == 0xC3
    assert sf[1] == 6                              # 4 coord bytes + 2 chars
    assert sf[2:6] == struct.pack(">hh", 5, 5)
    assert sf[6:] == b"AB"


def test_full_arc_centre_and_multiplier():
    # GFARC: C7 <len=06> <Xg><Yg> <MH> <MFR>.
    sf = goca.full_arc(50, 50, mult_int=2, mult_frac=0)
    assert sf[:2] == bytes([0xC7, 0x06])
    assert sf[2:6] == struct.pack(">hh", 50, 50)
    assert sf[6:] == bytes([0x02, 0x00])


def test_segment_concatenates_orders():
    orders = [goca.set_color(goca.COLOR_BLUE), goca.nop()]
    assert goca.segment(orders) == b"".join(orders)


def test_filled_rectangle_is_color_then_area_bracket():
    # A filled rectangle: Set Color, Begin Area, Box, End Area — in that order.
    seg = goca.filled_rectangle(0, 0, 10, 10, color=goca.COLOR_GREEN)
    assert seg == (goca.set_color(goca.COLOR_GREEN)
                   + goca.begin_area(boundary=True)
                   + goca.box(0, 0, 10, 10)
                   + goca.end_area())


def test_polyline_sets_color_and_line_type_then_draws():
    seg = goca.polyline([(0, 0), (5, 5)], color=goca.COLOR_RED,
                        line_type=goca.LINE_DASH_DOT)
    assert seg == (goca.set_color(goca.COLOR_RED)
                   + goca.set_line_type(goca.LINE_DASH_DOT)
                   + goca.line([(0, 0), (5, 5)]))


def test_long_order_operand_length_is_bounded():
    import pytest
    with pytest.raises(ValueError):
        goca.character_string(0, 0, b"x" * 300)    # operand > 255 bytes
