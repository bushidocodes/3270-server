"""Tests for the explicit SessionContext (#352).

`to_ebcdic`/`from_ebcdic` used to read the session code page only from the
thread-local ``ds3270._session`` — concurrency-safe, but action-at-a-distance:
a render silently depended on hidden state, so a render test had to remember to
prime the thread-local. A :class:`ds3270.SessionContext` carries the code page,
colour capability, and terminal model as one explicit value: pass it to
``Screen.render(session=...)`` / ``session.run(ctx=...)`` and the render is
deterministic with no global setup. The thread-local stays as a compatibility
shim — :func:`ds3270.activate_session` / :func:`ds3270.current_session` are its
write/read halves — so unmigrated ambient readers behave exactly as before.
"""
import pytest

import ds3270
import session as session_module
from dtl import load_panel
from ds3270 import SessionContext, activate_session, current_session, to_ebcdic
from screen import SFE, Screen, Text


def _reset_thread_local():
    for attr in ("code_page", "color", "model"):
        if hasattr(ds3270._session, attr):
            delattr(ds3270._session, attr)


@pytest.fixture(autouse=True)
def _clean_thread_local():
    # These tests deliberately poke the ambient shim; start each one from the
    # true defaults and never leak state into the rest of the suite (which
    # assumes the mono/cp037 defaults).
    _reset_thread_local()
    yield
    _reset_thread_local()


class FakeTransport:
    """The two-method Transport port, in memory (see test_session.py)."""

    def __init__(self, replies=()):
        self.sent = []
        self._replies = iter(replies)

    def send(self, data):
        self.sent.append(bytes(data))

    def read_input(self):
        return next(self._replies, None)


# ── the value object itself ──────────────────────────────────────────────────

def test_context_encodes_in_its_own_code_page():
    # In cp273 (German) '@' is 0xB5; in cp037 it is 0x7C — a clean discriminator.
    assert SessionContext().encode("@") == b"\x7c"                 # default cp037
    assert SessionContext(code_page="cp273").encode("@") == b"\xb5"
    assert SessionContext(code_page="cp273").decode(b"\xb5") == "@"


def test_context_round_trip():
    ctx = SessionContext(code_page="cp273")
    for ch in "@Äöü§ABC0":
        assert ctx.decode(ctx.encode(ch)) == ch


# ── deterministic rendering: no thread-local priming ─────────────────────────

def test_render_with_explicit_session_needs_no_global_setup():
    scr = Screen().add(Text(0, 0, "@"))
    assert b"\x7c" in scr.render(session=SessionContext())
    assert b"\xb5" in scr.render(session=SessionContext(code_page="cp273"))


def test_explicit_session_wins_over_a_primed_thread_local():
    # The old failure mode inverted: even with the ambient shim primed to
    # cp273, an explicit context renders in *its* code page — the render no
    # longer depends on hidden state.
    default_render = load_panel("logon").render()      # ambient still cp037 here
    ds3270._session.code_page = "cp273"
    scr = Screen().add(Text(0, 0, "@"))
    assert b"\x7c" in scr.render(session=SessionContext(code_page="cp037"))
    assert load_panel("logon").render(session=SessionContext()) == default_render
    # and with no session the ambient shim still applies, as before
    assert b"\xb5" in scr.render()


def test_session_colour_capability_drives_extended_attributes():
    colour, mono = SessionContext(color=True), SessionContext(color=False)
    assert SFE in load_panel("logon").render(session=colour)
    assert SFE not in load_panel("logon").render(session=mono)
    # an explicit color argument still overrides the context's capability
    assert SFE not in load_panel("logon").render(color=False, session=colour)


def test_default_render_is_byte_identical_with_and_without_a_context():
    # The default context is exactly the mono/cp037 ambient default, so passing
    # one changes no bytes on any bundled panel's default render.
    assert load_panel("logon").render() == \
        load_panel("logon").render(session=SessionContext())


def test_render_partial_honours_the_session():
    scr = Screen()
    ctx = SessionContext(code_page="cp273")
    assert b"\xb5" in scr.render_partial([Text(2, 25, "@")], session=ctx)
    assert b"\x7c" in scr.render_partial([Text(2, 25, "@")],
                                         session=SessionContext())


# ── the application threads the context explicitly ───────────────────────────

def test_run_renders_every_panel_in_the_explicit_context():
    # A colour context passed to run() colours the logon panel with no ambient
    # setup — the context flows send_tso_logon -> _send_screen -> render.
    transport = FakeTransport()          # immediate disconnect after the logon
    session_module.run(transport, ctx=SessionContext(color=True))
    assert len(transport.sent) == 1
    assert transport.sent[0] == load_panel("logon").render(color=True)


def test_run_encodes_in_the_explicit_contexts_code_page():
    transport = FakeTransport([
        (0x7D, {}, 0),                   # Enter with no userid -> error message
    ])
    session_module.run(transport, ctx=SessionContext(code_page="cp273"))
    # IKJ56700I ENTER USERID - contains no code-page-sensitive characters, but
    # the whole record must equal a render made with the same explicit context.
    assert transport.sent[0] == \
        load_panel("logon").render(session=SessionContext(code_page="cp273"))


def test_send_screen_takes_an_explicit_context():
    transport = FakeTransport()
    ctx = SessionContext(code_page="cp273")
    session_module._send_screen(transport, Screen().add(Text(0, 0, "@")), ctx=ctx)
    assert b"\xb5" in transport.sent[0]


def test_update_menu_message_uses_the_context_not_the_ambient():
    ds3270._session.color = True         # ambient says colour…
    transport = FakeTransport()
    scr = load_panel("ispf", ZUSER="IBMUSER ", ZTIME="12:00")
    session_module._update_menu_message(transport, scr, "INVALID OPTION: Z",
                                        ctx=SessionContext(color=False))
    assert SFE not in transport.sent[0]  # …but the explicit mono context wins


# ── the compatibility shim ────────────────────────────────────────────────────

def test_activate_session_updates_the_ambient_shim():
    activate_session(SessionContext(code_page="cp273", color=True))
    # unmigrated ambient readers (module-level to_ebcdic) see the new page
    assert to_ebcdic("@") == b"\xb5"
    assert current_session() == SessionContext(code_page="cp273", color=True)


def test_current_session_defaults_match_a_fresh_context():
    assert current_session() == SessionContext()
