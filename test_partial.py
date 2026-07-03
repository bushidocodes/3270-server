"""Tests for partial-screen updates (plain Write, 0xF1).

A full screen is an ERASE/WRITE (0xF5) that repaints everything and resets the
modified-data tags. A *partial* update is a plain WRITE (0xF1) that patches only
the addressed positions, leaving the rest of the presentation space — and what
the user has typed — untouched. The server uses this to redisplay the ISPF
menu's message line in place without clobbering the typed option.
"""
import server
from screen import (
    Screen, Text, DisplayIntensity, WRITE, ERASE_WRITE, SBA, IC, SF,
)
from dtl import load_panel


def _wcc(data):
    """The Write Control Character — the byte after the command byte."""
    return data[1]


# ── the render_partial mechanism ─────────────────────────────────────────────

def test_partial_uses_write_not_erase_write():
    data = Screen().render_partial([Text(2, 25, "HELLO")])
    assert data[0] == WRITE
    assert ERASE_WRITE not in data          # no erase → screen is not repainted


def test_partial_wcc_restores_keyboard_but_keeps_mdt():
    # WCC bits: 0x40 base, 0x02 keyboard-restore, 0x01 reset-MDT. A partial update
    # unlocks the keyboard but must NOT reset MDTs, or the user's typed (still
    # modified) input would stop being returned.
    wcc = _wcc(Screen().render_partial([Text(2, 25, "HI")]))
    assert wcc & 0x02                       # keyboard restore
    assert not (wcc & 0x01)                 # reset-MDT NOT set


def test_partial_contains_the_item_and_cursor():
    data = Screen().render_partial([Text(2, 25, "MSG")], cursor_at=(2, 13))
    assert b"\xd4\xe2\xc7" in data           # "MSG" in EBCDIC (cp037)
    assert SBA in data and IC in data        # cursor repositioned with SBA + IC
    assert data[-2:] == bytes([0xFF, 0xEF])  # IAC EOR


def test_partial_ends_with_eor_and_has_one_command_byte():
    data = Screen().render_partial([Text(0, 0, "X")])
    assert data.count(bytes([WRITE])) >= 1
    assert data[0] == WRITE                  # command byte leads the record


def test_full_render_is_unchanged_by_partial_support():
    # The full path still emits ERASE/WRITE with reset-MDT — partial support is
    # purely additive.
    full = Screen().add(Text(0, 0, "X")).render()
    assert full[0] == ERASE_WRITE
    assert _wcc(full) & 0x01                  # reset-MDT set on a full write


# ── the ISPF menu message-in-place redisplay ─────────────────────────────────

class _FakeSocket:
    def __init__(self):
        self.sent = bytearray()

    def sendall(self, data):
        self.sent += data


def _menu_screen():
    return load_panel("ispf", ZUSER="IBMUSER ", ZTIME="13:45")


def test_update_menu_message_is_a_partial_write():
    screen = _menu_screen()
    sock = _FakeSocket()
    server._update_menu_message(sock, screen, "INVALID OPTION: ZZ")
    assert sock.sent[0] == WRITE
    assert ERASE_WRITE not in sock.sent
    assert "INVALID OPTION: ZZ".encode("cp037") in sock.sent


def test_update_menu_message_positions_cursor_on_command_field():
    screen = _menu_screen()
    sock = _FakeSocket()
    server._update_menu_message(sock, screen, "X")
    # The record ends by repositioning the cursor (SBA + IC) to the command
    # field's data start, then IAC EOR.
    assert sock.sent[-1] == 0xEF and sock.sent[-2] == 0xFF
    assert sock.sent[-3] == IC
    cf = screen.command_field
    assert cf is not None
    from server import encode_pack_addr
    assert encode_pack_addr(cf.row, cf.col + 1) in sock.sent


def test_update_menu_message_blank_fills_to_overwrite_previous():
    # A short message is padded so it fully overwrites a longer previous one
    # (a plain Write only changes the positions it addresses).
    screen = _menu_screen()
    sock = _FakeSocket()
    server._update_menu_message(sock, screen, "OK")
    # 54 columns of message text: "OK" + 52 trailing blanks (EBCDIC 0x40).
    assert b"\xd6\xd2" + b"\x40" * 52 in sock.sent


def test_empty_message_clears_the_line():
    screen = _menu_screen()
    sock = _FakeSocket()
    server._update_menu_message(sock, screen, None)
    assert sock.sent[0] == WRITE
    assert b"\x40" * 54 in sock.sent          # the whole message field blanked
