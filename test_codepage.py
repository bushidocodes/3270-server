"""Tests for per-session EBCDIC code-page selection.

`to_ebcdic` / `from_ebcdic` default to cp037 (US) — so the bundled panels and the
whole existing data stream are byte-for-byte unchanged — but honour a per-session
code page recorded on the thread-local `server._session`. The session page is
chosen from the terminal's discovered base character set (CPGID, see #137): a
German ws3270 reports CPGID 273, so its session encodes/decodes in cp273.
"""
import pytest

import server
from server import to_ebcdic, from_ebcdic, code_page_for_model, TerminalModel


@pytest.fixture(autouse=True)
def _clean_session_code_page():
    # _session is thread-local and shared across tests on this thread; make sure a
    # test that sets a code page can't leak it into the next one (which would break
    # the default-cp037 assumptions everywhere else).
    yield
    if hasattr(server._session, "code_page"):
        del server._session.code_page


def _model(cgcsgid):
    return TerminalModel(term_type="IBM-3279-2-E", model=2, alt_rows=24, alt_cols=80,
                         base_cgcsgid=cgcsgid)


# ── to_ebcdic / from_ebcdic default to cp037 ─────────────────────────────────

def test_default_is_cp037():
    # No session page set → US cp037, unchanged from before.
    assert to_ebcdic("@") == b"\x7c"
    assert from_ebcdic(b"\x7c") == "@"


def test_default_round_trip():
    s = "HELLO WORLD 123"
    assert from_ebcdic(to_ebcdic(s)) == s


# ── the session code page is honoured ────────────────────────────────────────

def test_session_code_page_changes_encoding():
    # In cp273 (German) '@' is 0xB5, where 0x7C is 'Ä' — a clean discriminator.
    server._session.code_page = "cp273"
    assert to_ebcdic("@") == b"\xb5"
    assert from_ebcdic(b"\xb5") == "@"
    assert from_ebcdic(b"\x7c") == "§"      # 0x7C is '@' in cp037 but '§' in cp273


def test_session_code_page_round_trip():
    server._session.code_page = "cp273"
    for ch in "@Äöü§ABC0":
        assert from_ebcdic(to_ebcdic(ch)) == ch


def test_explicit_code_page_overrides_session():
    # The explicit argument wins over the session default (enables per-field use).
    server._session.code_page = "cp273"
    assert to_ebcdic("@", "cp037") == b"\x7c"
    assert from_ebcdic(b"\x7c", "cp037") == "@"


# ── choosing the page from the discovered character set ──────────────────────

def test_code_page_for_model_us():
    assert code_page_for_model(_model(0x02B90025)) == "cp037"     # CPGID 37


def test_code_page_for_model_german():
    assert code_page_for_model(_model(0x02B90111)) == "cp273"     # CPGID 273


def test_code_page_for_model_unknown_cpgid_falls_back():
    # French (CPGID 297) has no Python codec here → default rather than a crash.
    assert code_page_for_model(_model(0x02B90129)) == "cp037"


def test_code_page_for_model_no_discovery_falls_back():
    assert code_page_for_model(_model(None)) == "cp037"
    assert code_page_for_model(None) == "cp037"
