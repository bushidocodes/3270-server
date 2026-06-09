"""
Unit tests for server.py pure functions:
  - encode_pack_addr      12-bit 3270 address encoding
  - write_control_character  WCC byte assembly from flags
  - field_attribute          field-attribute byte from display/type/mdt flags
  - aid_to_string            AID byte → human-readable name
  - read_client_input        SBA field-parsing logic (not the I/O loop)
"""
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, ".")
from server import (
    DisplayIntensity,
    EOR,
    FieldType,
    IAC,
    aid_to_string,
    encode_pack_addr,
    field_attribute,
    read_client_input,
    write_control_character,
)

SBA = 0x11
AID_ENTER = 0x7D


# ── helpers ────────────────────────────────────────────────────────────────────

def _sock(payload: bytes) -> MagicMock:
    """Mock socket whose recv() returns payload + IAC EOR in one call."""
    mock = MagicMock()
    mock.recv.return_value = payload + bytes([IAC, EOR])
    return mock


def _sba_bytes(linear_addr: int) -> bytes:
    """Return the 3-byte SBA command (ordinal + encoded address) for a linear address."""
    row, col = divmod(linear_addr, 80)
    return bytes([SBA]) + encode_pack_addr(row, col)


# ── encode_pack_addr ───────────────────────────────────────────────────────────

def test_pack_addr_origin():
    """(0, 0) → addr 0 encodes to hi=0xC0, lo=0x40."""
    assert encode_pack_addr(0, 0) == bytes([0xC0, 0x40])


def test_pack_addr_first_column():
    """(0, 1) → only lo_chunk changes from the origin."""
    assert encode_pack_addr(0, 1) == bytes([0xC0, 0x41])


def test_pack_addr_second_row():
    """(1, 0) → addr=80; hi_chunk=1, lo_chunk=16."""
    # addr=80: hi_chunk=(80>>6)=1, lo_chunk=(80&0x3F)=16
    # hi=0xC1, lo=0x50
    assert encode_pack_addr(1, 0) == bytes([0xC1, 0x50])


def test_pack_addr_last_standard_cell():
    """(23, 79) → last cell of a 24×80 screen round-trips correctly."""
    hi, lo = encode_pack_addr(23, 79)
    decoded = ((hi & 0x3F) << 6) | (lo & 0x3F)
    assert decoded == 23 * 80 + 79


def test_pack_addr_round_trip():
    """Encoded address always decodes back to the original linear address."""
    for row in range(24):
        for col in range(80):
            hi, lo = encode_pack_addr(row, col)
            assert ((hi & 0x3F) << 6) | (lo & 0x3F) == row * 80 + col


def test_pack_addr_custom_cols():
    """cols parameter is used in the linear address calculation."""
    hi, lo = encode_pack_addr(1, 0, cols=132)
    assert ((hi & 0x3F) << 6) | (lo & 0x3F) == 132


def test_pack_addr_raises_on_negative():
    with pytest.raises(ValueError):
        encode_pack_addr(-1, 0)


def test_pack_addr_raises_on_overflow():
    """addr ≥ 0x1000 (4096) must raise — 100 rows × 80 = 8000."""
    with pytest.raises(ValueError):
        encode_pack_addr(100, 0)


# ── write_control_character ────────────────────────────────────────────────────

def test_wcc_default():
    """Default: RESET_BIT (0x40) + RESET_MDT (0x01) → 0x41."""
    assert write_control_character() == bytes([0x41])


def test_wcc_all_false():
    """No optional flags → only the always-set RESET_BIT → 0x40."""
    assert write_control_character(reset_mdts=False) == bytes([0x40])


def test_wcc_keyboard_restore():
    assert write_control_character(reset_mdts=False, keyboard_restore=True) == bytes([0x42])


def test_wcc_sound_alarm():
    assert write_control_character(reset_mdts=False, sound_alarm=True) == bytes([0x44])


def test_wcc_start_printer():
    assert write_control_character(reset_mdts=False, start_printer=True) == bytes([0x48])


def test_wcc_all_flags():
    """All flags: 0x40 | 0x01 | 0x02 | 0x04 | 0x08 = 0x4F."""
    assert write_control_character(
        reset_mdts=True, sound_alarm=True, keyboard_restore=True, start_printer=True
    ) == bytes([0x4F])


def test_wcc_returns_single_byte():
    assert len(write_control_character()) == 1


# ── field_attribute ────────────────────────────────────────────────────────────

def test_fa_default():
    """Default: NORMAL intensity, protected, alphanumeric, no MDT → 0x20."""
    assert field_attribute() == 0x20


def test_fa_non_display_protected():
    """NON_DISPLAY + protected → 0xE0 (standard password-field attribute)."""
    assert field_attribute(display=DisplayIntensity.NON_DISPLAY, protected=True) == 0xE0


def test_fa_high_protected():
    """HIGH intensity + protected → 0x60."""
    assert field_attribute(display=DisplayIntensity.HIGH, protected=True) == 0x60


def test_fa_highlighted_protected():
    """HIGHLIGHTED + protected → 0xA0."""
    assert field_attribute(display=DisplayIntensity.HIGHLIGHTED, protected=True) == 0xA0


def test_fa_unprotected_input():
    """Normal unprotected editable field → 0x00."""
    assert field_attribute(display=DisplayIntensity.NORMAL, protected=False) == 0x00


def test_fa_mdt_set():
    """MDT bit set on default protected field → 0x21."""
    assert field_attribute(mdt=True) == 0x21


def test_fa_numeric():
    """Numeric type on protected field → 0x30."""
    assert field_attribute(field_type=FieldType.NUMERIC) == 0x30


def test_fa_unprotected_numeric_mdt():
    """Unprotected numeric with MDT → 0x11."""
    assert field_attribute(protected=False, field_type=FieldType.NUMERIC, mdt=True) == 0x11


# ── aid_to_string ──────────────────────────────────────────────────────────────

def test_aid_enter():
    assert aid_to_string(0x7D) == "Enter"


def test_aid_clear():
    assert aid_to_string(0x6D) == "Clear"


def test_aid_no_aid():
    assert aid_to_string(0x60) == "No AID"


def test_aid_pa_keys():
    assert aid_to_string(0x6C) == "PA1"
    assert aid_to_string(0x6E) == "PA2"
    assert aid_to_string(0x6B) == "PA3"


def test_aid_pf1_through_pf9():
    """PF1–PF9 occupy the contiguous range 0xF1–0xF9."""
    for i, byte in enumerate(range(0xF1, 0xFA), start=1):
        assert aid_to_string(byte) == f"PF{i}"


def test_aid_pf10_through_pf12():
    assert aid_to_string(0x7A) == "PF10"
    assert aid_to_string(0x7B) == "PF11"
    assert aid_to_string(0x7C) == "PF12"


def test_aid_pf13_through_pf21():
    """PF13–PF21 occupy the contiguous range 0xC1–0xC9."""
    for i, byte in enumerate(range(0xC1, 0xCA), start=13):
        assert aid_to_string(byte) == f"PF{i}"


def test_aid_pf22_through_pf24():
    assert aid_to_string(0x4A) == "PF22"
    assert aid_to_string(0x4B) == "PF23"
    assert aid_to_string(0x4C) == "PF24"


def test_aid_unknown_returns_hex_name():
    assert aid_to_string(0x01) == "Unknown AID 0x1"
    assert aid_to_string(0x00) == "Unknown AID 0x0"


# ── read_client_input: field-parsing ──────────────────────────────────────────

def test_parse_aid_only_returns_empty_fields():
    """Just an AID byte with no SBA data → empty field dict."""
    aid, fields = read_client_input(_sock(bytes([AID_ENTER])))
    assert aid == AID_ENTER
    assert fields == {}


def test_parse_single_field():
    """AID + SBA + encoded addr + EBCDIC text → correct (aid, {addr: text})."""
    addr = 5 * 80 + 17  # row 5, col 17
    payload = bytes([AID_ENTER]) + _sba_bytes(addr) + "IBMUSER".encode("cp037")
    aid, fields = read_client_input(_sock(payload))
    assert aid == AID_ENTER
    assert fields == {addr: "IBMUSER"}


def test_parse_multiple_fields():
    """Two consecutive SBA-delimited fields are both decoded."""
    addr1 = 5 * 80 + 17   # userid
    addr2 = 6 * 80 + 17   # password
    payload = (
        bytes([AID_ENTER])
        + _sba_bytes(addr1) + "IBMUSER".encode("cp037")
        + _sba_bytes(addr2) + "SYS1".encode("cp037")
    )
    _, fields = read_client_input(_sock(payload))
    assert fields[addr1] == "IBMUSER"
    assert fields[addr2] == "SYS1"


def test_parse_whitespace_only_field_excluded():
    """A field containing only spaces is stripped and omitted from the result."""
    addr = 5 * 80 + 17
    payload = bytes([AID_ENTER]) + _sba_bytes(addr) + "        ".encode("cp037")
    _, fields = read_client_input(_sock(payload))
    assert fields == {}


def test_parse_truncated_sba_at_buffer_end_skipped():
    """SBA with no following address bytes is skipped, not a crash."""
    payload = bytes([AID_ENTER, SBA])  # SBA has no addr bytes
    result = read_client_input(_sock(payload))
    assert result is not None
    _, fields = result
    assert fields == {}


def test_parse_field_terminated_by_sf_ordinal():
    """A field that ends at an SF ordinal (0x1D) is collected correctly."""
    from server import SF
    addr = 5 * 80 + 17
    payload = (
        bytes([AID_ENTER])
        + _sba_bytes(addr)
        + "HELLO".encode("cp037")
        + bytes([SF, 0x20])  # SF terminates the field
    )
    _, fields = read_client_input(_sock(payload))
    assert fields[addr] == "HELLO"
