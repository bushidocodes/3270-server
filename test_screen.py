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
from screens import build_tso_logon, build_ispf_menu


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

def test_logon_matches_legacy_bytes():
    expected = _capture(server.send_tso_logon)
    assert build_tso_logon().render() == bytes(expected)


def test_logon_with_error_matches_legacy_bytes():
    msg = "IKJ56425I PASSWORD NOT CORRECT FOR IBMUSER"
    expected = _capture(server.send_tso_logon, msg)
    assert build_tso_logon(error_msg=msg).render() == bytes(expected)


# ── ISPF menu ────────────────────────────────────────────────────────────────

def test_ispf_matches_legacy_bytes(frozen_clock):
    expected = _capture(server.send_ispf_menu, "IBMUSER")
    got = build_ispf_menu("IBMUSER", FROZEN.strftime("%H:%M")).render()
    assert got == bytes(expected)


def test_ispf_with_short_msg_matches_legacy_bytes(frozen_clock):
    msg = "OPTION 3 NOT YET IMPLEMENTED"
    expected = _capture(server.send_ispf_menu, "IBMUSER", msg)
    got = build_ispf_menu("IBMUSER", FROZEN.strftime("%H:%M"), short_msg=msg).render()
    assert got == bytes(expected)


def test_ispf_long_userid_matches_legacy_bytes(frozen_clock):
    expected = _capture(server.send_ispf_menu, "TESTUSER")
    got = build_ispf_menu("TESTUSER", FROZEN.strftime("%H:%M")).render()
    assert got == bytes(expected)


# ── model semantics ──────────────────────────────────────────────────────────

def test_field_addr_maps_named_fields():
    s = build_tso_logon()
    assert s.field_addr("userid") == 5 * 80 + 17
    assert s.field_addr("password") == 6 * 80 + 17
    assert s.field_addr("missing") is None


def test_ispf_option_addr_matches_legacy_constant():
    s = build_ispf_menu("IBMUSER", "13:45")
    assert s.field_addr("option") == server.ISPF_OPTION_ADDR


def test_parse_maps_addresses_to_names():
    s = build_tso_logon()
    raw = {5 * 80 + 17: "IBMUSER", 6 * 80 + 17: "SYS1", 999: "ignored"}
    aid, named = s.parse(0x7D, raw)
    assert aid == 0x7D
    assert named == {"userid": "IBMUSER", "password": "SYS1"}


def test_render_starts_with_erase_write_and_wcc():
    data = build_tso_logon().render()
    assert data[0] == 0xF5  # ERASE_WRITE
    assert data[1] == 0x43  # WCC: reset + keyboard-restore + reset-MDT
    assert data[-2:] == bytes([server.IAC, server.EOR])
