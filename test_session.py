"""Drive the TSO/ISPF application against a fake transport — no sockets (#351).

session.py talks to the client only through the two-method Transport port
(``send`` one rendered 3270 record / ``read_input`` one parsed AID reply), so
the whole application — logon screen, logon validation, the ISPF Primary
Option Menu, option dispatch, and exit — can be exercised entirely in memory:
no sockets, no Telnet negotiation, no emulator. These tests are the payoff of
the #351 split; ``socket.socket`` is stubbed out to *prove* nothing on the
application side ever touches the network.
"""
import socket as socket_module

import pytest

import session
from dtl import load_panel
from ds3270 import to_ebcdic
from session import ISPF_OPTION_ADDR, LOGON_PASSWORD_ADDR, LOGON_USERID_ADDR

ENTER, PF3 = 0x7D, 0xF3
ERASE_WRITE = 0xF5   # a full-screen repaint starts with Erase/Write
WRITE = 0xF1         # an in-place partial update starts with a plain Write


class FakeTransport:
    """The Transport port, in memory: records every 3270 record the application
    sends and answers ``read_input()`` from a script of already-parsed replies
    (``(aid, fields, cursor)`` tuples). An exhausted script reads as a client
    disconnect (``None``), which ends the session the same way a closed socket
    does."""

    def __init__(self, replies):
        self.sent = []
        self._replies = iter(replies)

    def send(self, data):
        self.sent.append(bytes(data))

    def read_input(self):
        return next(self._replies, None)


@pytest.fixture(autouse=True)
def _mono_session(monkeypatch):
    """Pin the thread-local session context to the mono/cp037 defaults so
    renders are deterministic regardless of test order."""
    monkeypatch.setattr(session._session, "color", False, raising=False)
    monkeypatch.setattr(session._session, "code_page", "cp037", raising=False)


def _forbid_sockets(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("the application tried to create a socket")
    monkeypatch.setattr(socket_module, "socket", _boom)


def test_full_flow_logon_menu_option_exit_without_sockets(monkeypatch):
    """The issue's scripted flow: logon screen -> valid logon -> main menu ->
    pick option 6 (Command Shell) -> PF3 back to the menu -> X to exit — driven
    end-to-end with socket creation forbidden."""
    _forbid_sockets(monkeypatch)
    transport = FakeTransport([
        (ENTER, {LOGON_USERID_ADDR: "IBMUSER",
                 LOGON_PASSWORD_ADDR: "SYS1"}, 0),   # valid logon
        (ENTER, {ISPF_OPTION_ADDR: "6"}, 0),         # menu: pick Command Shell
        (PF3, {}, 0),                                # shell: PF3 -> EXIT
        (ENTER, {ISPF_OPTION_ADDR: "X"}, 0),         # menu: X leaves ISPF
    ])
    session.run(transport, model=None)               # script end = disconnect

    # logon -> menu -> command shell -> menu again -> logon again
    assert len(transport.sent) == 5
    logon = load_panel("logon").render()
    assert transport.sent[0] == logon                          # the logon panel
    assert to_ebcdic("IBMUSER") in transport.sent[1]           # menu, ZUSER shown
    assert to_ebcdic("ISPF Command Shell") in transport.sent[2]
    assert to_ebcdic("IBMUSER") in transport.sent[3]           # back on the menu
    assert transport.sent[4] == logon                          # X: logged off
    # every screen in the flow was a full Erase/Write repaint
    assert all(rec[0] == ERASE_WRITE for rec in transport.sent)


def test_bad_password_redisplays_logon_with_the_tso_error():
    transport = FakeTransport([
        (ENTER, {LOGON_USERID_ADDR: "IBMUSER",
                 LOGON_PASSWORD_ADDR: "WRONG"}, 0),
    ])
    session.run(transport)
    assert len(transport.sent) == 2                  # logon, then the redisplay
    expected = session._messages().format("IKJ56425I", USERID="IBMUSER")
    assert to_ebcdic(expected) in transport.sent[1]
    assert to_ebcdic(expected) not in transport.sent[0]


def test_invalid_menu_option_is_patched_in_place():
    """A stay-on-the-menu error is a partial Write (the typed option survives),
    not a full repaint — the same behaviour the emulator smoke test locks in,
    reproduced here with no I/O at all."""
    transport = FakeTransport([
        (ENTER, {LOGON_USERID_ADDR: "IBMUSER",
                 LOGON_PASSWORD_ADDR: "SYS1"}, 0),
        (ENTER, {ISPF_OPTION_ADDR: "ZZ"}, 0),        # not a menu option
    ])
    session.run(transport)
    assert len(transport.sent) == 3                  # logon, menu, message patch
    patch = transport.sent[2]
    assert patch[0] == WRITE                         # in-place, not Erase/Write
    assert to_ebcdic("INVALID OPTION: ZZ") in patch


def test_session_module_never_imports_the_network():
    """The application module must stay drivable with no protocol stack: it
    may import the render model, the DTL loader and the pure codec — never
    socket, ssl, or server."""
    import inspect
    src = inspect.getsource(session)
    assert "import socket" not in src
    assert "import ssl" not in src
    assert "import server" not in src
    assert not hasattr(session, "socket") and not hasattr(session, "ssl")
