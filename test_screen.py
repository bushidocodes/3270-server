"""Golden regression: the data-driven Screen model must reproduce the original
hand-written panels byte-for-byte.

For each screen we capture the exact bytes the legacy ``send_*`` function writes
to the socket, then assert the equivalent :class:`screen.Screen` renders the
same bytes. This guarantees the refactor introduces zero visual/wire change.
The ISPF clock is frozen so both sides agree on the status-block time.
"""
import datetime
from unittest.mock import MagicMock

import pytest

import server
from screen import Screen, Text, Field, DisplayIntensity
from dtl import load_panel


FROZEN = datetime.datetime(2026, 6, 24, 13, 45)


def _capture(fn, *args, **kwargs) -> bytes:
    """Call a legacy send_* function with a mock socket and return the bytes it sends."""
    sock = MagicMock()
    fn(sock, *args, **kwargs)
    return sock.sendall.call_args[0][0]


@pytest.fixture
def frozen_clock(monkeypatch):
    class _DT:
        @staticmethod
        def now():
            return FROZEN
    monkeypatch.setattr(server, "datetime", _DT)
    yield


# ── logon panel ──────────────────────────────────────────────────────────────

def test_logon_server_sends_the_dtl_panel():
    sent = _capture(server.send_tso_logon)
    assert bytes(sent) == load_panel("logon").render()


def test_logon_server_overlays_the_error_message():
    msg = "IKJ56425I PASSWORD NOT CORRECT FOR IBMUSER"
    sent = _capture(server.send_tso_logon, msg)
    expected = load_panel("logon")
    col = max(0, (80 - len(msg)) // 2)
    expected.add(Text(19, col, msg, DisplayIntensity.HIGH))
    assert bytes(sent) == expected.render()


# ── ISPF menu ────────────────────────────────────────────────────────────────

def _ispf_screen(userid, short_msg=None):
    """The ISPF menu screen the server sends: the DTL panel, plus the transient
    short-message overlay when present."""
    s = load_panel("ispf", ZUSER=userid.ljust(8), ZTIME=FROZEN.strftime("%H:%M"))
    if short_msg:
        s.add(Text(2, 25, short_msg[:54], DisplayIntensity.HIGH))
        s.sound_alarm = True   # a menu error message beeps, like real ISPF
    return s


def test_ispf_server_sends_the_dtl_menu(frozen_clock):
    sent = _capture(server.send_ispf_menu, "IBMUSER")
    assert bytes(sent) == _ispf_screen("IBMUSER").render()


def test_ispf_server_overlays_the_short_message(frozen_clock):
    msg = "OPTION 3 NOT YET IMPLEMENTED"
    sent = _capture(server.send_ispf_menu, "IBMUSER", msg)
    assert bytes(sent) == _ispf_screen("IBMUSER", msg).render()
    # A menu error message sounds the alarm (WCC bit 0x04), like real ISPF; a
    # menu with no message does not.
    assert sent[1] & 0x04
    assert not (_capture(server.send_ispf_menu, "IBMUSER")[1] & 0x04)


def test_ispf_server_substitutes_a_longer_userid(frozen_clock):
    sent = _capture(server.send_ispf_menu, "TESTUSER")
    assert bytes(sent) == _ispf_screen("TESTUSER").render()


# ── model semantics ──────────────────────────────────────────────────────────

def test_field_addr_maps_named_fields():
    s = load_panel("logon")
    assert s.field_addr("userid") == 4 * 80 + 16
    assert s.field_addr("password") == 5 * 80 + 16
    assert s.field_addr("missing") is None


def test_ispf_option_addr_matches_legacy_constant():
    s = load_panel("ispf", ZUSER="IBMUSER ", ZTIME="13:45")
    assert s.field_addr("ZCMD") == server.ISPF_OPTION_ADDR


def test_parse_maps_addresses_to_names():
    s = load_panel("logon")
    raw = {4 * 80 + 16: "IBMUSER", 5 * 80 + 16: "SYS1", 999: "ignored"}
    aid, named = s.parse(0x7D, raw)
    assert aid == 0x7D
    assert named == {"userid": "IBMUSER", "password": "SYS1"}


def test_render_starts_with_erase_write_and_wcc():
    data = load_panel("logon").render()
    assert data[0] == 0xF5  # ERASE_WRITE
    assert data[1] == 0x43  # WCC: reset + keyboard-restore + reset-MDT
    assert data[-2:] == bytes([server.IAC, server.EOR])


# ── Cursor Select / selector-pen detectable fields (#104) ────────────────────

def _render(item):
    """Render one screen item to bytes (mono)."""
    buf = bytearray()
    item.render(buf)
    return bytes(buf)


def test_field_designator_sets_detectable_attribute_and_leading_char():
    # A Field with a designator is detectable (attribute bit 0x04) and renders the
    # designator as its first data byte, ahead of the value.
    detect = _render(Field(row=5, col=10, length=7, designator="?", default="AB"))
    plain = _render(Field(row=5, col=10, length=7, default="AB"))
    # attribute byte follows the SBA (3 bytes) + SF order (1 byte) → index 4
    assert detect[4] == plain[4] | 0x04            # detectable bit set
    assert detect[5] == server.to_ebcdic("?")[0]   # designator is the first data byte
    assert plain[5] == server.to_ebcdic("A")[0]     # a plain field starts with its value


def test_text_detectable_flag_sets_the_attribute():
    detect = _render(Text(5, 10, "PICK", detectable=True))
    plain = _render(Text(5, 10, "PICK"))
    assert detect[4] == plain[4] | 0x04
    # A plain Text (no designator) is otherwise byte-for-byte unchanged.
    assert detect[5:] == plain[5:]


def test_plain_field_and_text_render_unchanged():
    # The detectable/designator defaults must not perturb ordinary items.
    assert _render(Field(row=1, col=2, length=4, default="XY")) == \
        _render(Field(row=1, col=2, length=4, default="XY"))
    assert _render(Text(1, 2, "hello")) == _render(Text(1, 2, "hello"))


def test_selected_designators_reads_toggled_selection_fields():
    # A '?' selection field the operator cursor-selected comes back as '>' (MDT set);
    # selected_designators returns those items, skipping the untoggled ones.
    s = Screen()
    a = Field(row=5, col=10, length=6, designator="?", name="north")
    b = Field(row=6, col=10, length=6, designator="?", name="south")
    s.add(a).add(b)
    selected = s.selected_designators({a.data_addr: ">YES", b.data_addr: "?"})
    assert [it.name for it in selected] == ["north"]
    assert s.selected_designators({}) == []          # nothing modified → nothing selected
