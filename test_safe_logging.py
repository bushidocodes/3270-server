"""
Tests verifying that read_client_input logs safely — no raw hex dump of the
received buffer, which would expose password field contents (PR #13 / issue #11).

The old code called:  print("RX:", binascii.hexlify(buffer))
                      print(f"AID: {aid_to_string(aid)}")

The fixed code prints: print(f"RX: {len(buffer)} bytes, AID: {aid_to_string(aid)}")
"""
import binascii
import sys
from unittest.mock import MagicMock

sys.path.insert(0, ".")
from server import read_client_input  # noqa: E402

IAC = 0xFF
EOR = 0xEF

AID_ENTER = 0x7D
AID_PF1   = 0xF1
AID_PA1   = 0x6C


def _sock(payload: bytes) -> MagicMock:
    """Mock socket whose recv() returns payload + IAC EOR in a single call."""
    mock = MagicMock()
    mock.recv.return_value = payload + bytes([IAC, EOR])
    return mock


# ── No raw-hex leakage ─────────────────────────────────────────────────────────

def test_raw_hex_not_in_output(capsys):
    """binascii.hexlify output must never appear — it would expose password bytes."""
    password_bytes = "secret".encode("cp037")
    payload = bytes([AID_ENTER, 0x11, 0x50, 0x60]) + password_bytes
    read_client_input(_sock(payload))
    out = capsys.readouterr().out
    full_frame = payload + bytes([IAC, EOR])
    assert binascii.hexlify(full_frame).decode() not in out
    assert binascii.hexlify(payload).decode() not in out


def test_password_bytes_not_hex_printed(capsys):
    """The EBCDIC bytes of a password field must not appear as a hex substring."""
    password_bytes = "hunter2".encode("cp037")
    payload = bytes([AID_ENTER, 0x11, 0x40, 0x40]) + password_bytes
    read_client_input(_sock(payload))
    out = capsys.readouterr().out
    assert binascii.hexlify(password_bytes).decode() not in out


# ── Safe summary format ────────────────────────────────────────────────────────

def test_safe_summary_line_present(capsys):
    """The 'RX: N bytes, AID: <name>' summary must appear in stdout."""
    read_client_input(_sock(bytes([AID_ENTER])))
    out = capsys.readouterr().out
    assert "RX:" in out
    assert "bytes" in out
    assert "AID:" in out


def test_byte_count_matches_payload_length(capsys):
    """Logged byte count must equal len(payload) — not including the IAC EOR frame."""
    extra = bytes([0x11, 0x40, 0x40, 0xC1, 0xC2])
    payload = bytes([AID_ENTER]) + extra
    read_client_input(_sock(payload))
    out = capsys.readouterr().out
    assert f"RX: {len(payload)} bytes" in out


def test_byte_count_excludes_iac_eor(capsys):
    """IAC EOR framing bytes must be subtracted — they are not 3270 payload."""
    payload = bytes([AID_PF1])
    read_client_input(_sock(payload))
    out = capsys.readouterr().out
    assert "RX: 1 bytes" in out
    assert "RX: 3 bytes" not in out  # 3 would wrongly include IAC + EOR


# ── AID name decoding ──────────────────────────────────────────────────────────

def test_aid_enter_decoded_by_name(capsys):
    """AID 0x7D must be logged as 'Enter', not as a numeric byte value."""
    read_client_input(_sock(bytes([AID_ENTER])))
    out = capsys.readouterr().out
    assert "Enter" in out


def test_aid_pf1_decoded_by_name(capsys):
    """AID 0xF1 must be logged as 'PF1'."""
    read_client_input(_sock(bytes([AID_PF1])))
    out = capsys.readouterr().out
    assert "PF1" in out


def test_aid_pa1_decoded_by_name(capsys):
    """AID 0x6C must be logged as 'PA1'."""
    read_client_input(_sock(bytes([AID_PA1])))
    out = capsys.readouterr().out
    assert "PA1" in out


def test_unknown_aid_no_buffer_dump(capsys):
    """An unknown AID must not cause the buffer to be hex-dumped as a fallback."""
    unknown_aid = 0x01
    payload = bytes([unknown_aid, 0xAA, 0xBB, 0xCC])
    read_client_input(_sock(payload))
    out = capsys.readouterr().out
    assert binascii.hexlify(payload).decode() not in out
    assert "RX:" in out
    assert "AID:" in out
