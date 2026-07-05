"""In-process integration tests for the TN3270 session loop.

Each test starts :func:`server.handle_client` on an ephemeral port in a
background thread and drives it over a real socket: Telnet negotiation, logon,
option dispatch (typed and point-and-shoot), and logoff. This is the automated
counterpart to the manual ws3270 checks — it locks in the Phase-3 session flow
that was previously only verified by hand on the emulator.
"""
import socket
import threading
import time

import pytest

import server

IAC, EOR = 0xFF, 0xEF
SBA, SF = 0x11, 0x1D
DO, DONT, WILL, WONT, SB, SE = 0xFD, 0xFE, 0xFB, 0xFC, 0xFA, 0xF0
BINARY, TERMINAL_TYPE, EOR_OPT = 0, 24, 25
ENTER, PF1, PF3 = 0x7D, 0xF1, 0xF3
ERASE_WRITE = 0xF5

# Field addresses the server reads (row * 80 + data col).
USERID_ADDR = 4 * 80 + 16
PASSWORD_ADDR = 5 * 80 + 16
ZCMD_ADDR = 2 * 80 + 14        # ISPF Option ===> line


def _pack(addr):
    return bytes([((addr >> 6) & 0x3F) | 0xC0, (addr & 0x3F) | 0x40])


def _reply(aid=ENTER, cursor=0, fields=None):
    """Build an inbound 3270 reply: AID, the raw 2-byte cursor address, then an
    SBA + text for each field. (The cursor address follows the AID directly, no
    SBA — that is how the server decodes it for point-and-shoot.)"""
    buf = bytearray([aid])
    buf += _pack(cursor)
    for addr, text in (fields or {}).items():
        buf += bytes([SBA]) + _pack(addr) + text.encode("cp037")
    buf += bytes([IAC, EOR])
    return bytes(buf)


def _recv_screen(sock, initial=b""):
    """Read one 3270 record terminated by IAC EOR (minus the terminator)."""
    buf = bytearray(initial)
    while not (len(buf) >= 2 and buf[-2:] == bytes([IAC, EOR])):
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("connection closed")
        buf.extend(chunk)
    return bytes(buf[:-2])


def _negotiate(sock):
    """Answer the server's Telnet negotiation and return any screen bytes that
    arrived in the same recv() past the end of negotiation."""
    buf = bytearray()
    got_binary = got_eor = got_term = False
    deadline = time.time() + 10
    while True:
        while buf and buf[0] == IAC:
            if len(buf) < 2:
                break
            cmd = buf[1]
            if cmd in (DO, DONT, WILL, WONT):
                if len(buf) < 3:
                    break
                opt = buf[2]
                if not (got_binary and got_eor and got_term):
                    reply = {DO: WILL, DONT: WONT, WILL: DO, WONT: DONT}[cmd]
                    if opt == 40:  # refuse TN3270E → exercise the basic TN3270 path
                        reply = {DO: WONT, WILL: DONT}.get(cmd, reply)
                    sock.sendall(bytes([IAC, reply, opt]))
                if opt == BINARY and cmd in (DO, WILL):
                    got_binary = True
                if opt == EOR_OPT and cmd in (DO, WILL):
                    got_eor = True
                del buf[:3]
            elif cmd == SB:
                se = buf.find(bytes([IAC, SE]), 2)
                if se == -1:
                    break
                if len(buf) >= 4 and buf[2] == TERMINAL_TYPE and buf[3] == 1:
                    sock.sendall(bytes([IAC, SB, TERMINAL_TYPE, 0]) + b"IBM-3278-2"
                                 + bytes([IAC, SE]))
                    got_term = True
                del buf[:se + 2]
            else:
                del buf[:2]
        if buf and buf[0] != IAC:
            return bytes(buf)
        if time.time() > deadline:
            raise TimeoutError("negotiation timed out")
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("closed during negotiation")
        buf.extend(chunk)


@pytest.fixture
def session():
    """A connected, negotiated client talking to an in-process server. Yields
    (sock, logon_screen_bytes)."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        try:
            conn, addr = srv.accept()
        except OSError:
            return
        try:
            server.handle_client(conn, addr)
        except Exception:
            pass
        finally:
            conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    sock.connect(("127.0.0.1", port))
    logon = _recv_screen(sock, _negotiate(sock))
    try:
        yield sock, logon
    finally:
        sock.close()
        srv.close()


def _text(data):
    return data.decode("cp037", errors="replace")


def _login(sock, userid="IBMUSER", password="SYS1"):
    sock.sendall(_reply(fields={USERID_ADDR: userid, PASSWORD_ADDR: password}))
    return _text(_recv_screen(sock))


# ── the flow ─────────────────────────────────────────────────────────────────

def test_logon_panel_is_erase_write(session):
    _sock, logon = session
    assert logon[:1] == bytes([ERASE_WRITE])
    assert "LOGON" in _text(logon)


def test_valid_login_reaches_ispf_menu(session):
    sock, _ = session
    assert "ISPF Primary Option Menu" in _login(sock)


def test_bad_password_shows_racf_message(session):
    sock, _ = session
    sock.sendall(_reply(fields={USERID_ADDR: "IBMUSER", PASSWORD_ADDR: "WRONG"}))
    assert "IKJ56425I" in _text(_recv_screen(sock))


def test_typed_option_opens_dialog_test(session):
    sock, _ = session
    _login(sock)
    sock.sendall(_reply(fields={ZCMD_ADDR: "7"}))     # type 7 on the Option line
    assert "Dialog Test - Variables" in _text(_recv_screen(sock))


def test_typed_option_opens_command_shell(session):
    sock, _ = session
    _login(sock)
    sock.sendall(_reply(fields={ZCMD_ADDR: "6"}))
    assert "ISPF Command Shell" in _text(_recv_screen(sock))


def test_typed_option_opens_view_entry(session):
    # Option 1 routes via ispf.dtl's )PROC ("1" -> PGM(view)) to the View entry
    # panel — exercises the PGM(view) -> _show_view handler in the #55 registry.
    sock, _ = session
    _login(sock)
    sock.sendall(_reply(fields={ZCMD_ADDR: "1"}))
    assert "View - Entry Panel" in _text(_recv_screen(sock))


def test_typed_option_opens_a_plain_submenu(session):
    # Option 4 ("4" -> PANEL(foreground)) exercises the PANEL(x) -> _show_submenu
    # path (the plain nested sub-menus 4/5/9/10/12/13).
    sock, _ = session
    _login(sock)
    sock.sendall(_reply(fields={ZCMD_ADDR: "4"}))
    assert "Foreground Selection Panel" in _text(_recv_screen(sock))


def test_point_and_shoot_opens_utilities(session):
    sock, _ = session
    _login(sock)
    # No typed option; cursor parked on the "3 Utilities" choice row (row 7).
    sock.sendall(_reply(cursor=7 * 80 + 5))
    assert "Utility Selection Panel" in _text(_recv_screen(sock))


def test_dotted_jump_opens_member_list(session):
    sock, _ = session
    _login(sock)
    sock.sendall(_reply(fields={ZCMD_ADDR: "3.1"}))    # jump straight to Library
    assert "Member List" in _text(_recv_screen(sock))


def test_utility_library_leaf_routes_via_submenu_proc(session):
    # Option 3 opens the Utility menu; typing 1 there routes through utility.dtl's
    # own )PROC ("1" -> PGM(memberlist)) to the Library member list (#55 PR 3).
    sock, _ = session
    _login(sock)
    sock.sendall(_reply(fields={ZCMD_ADDR: "3"}))
    assert "Utility Selection Panel" in _text(_recv_screen(sock))
    sock.sendall(_reply(fields={ZCMD_ADDR: "1"}))      # option 1 on the sub-menu
    assert "Member List" in _text(_recv_screen(sock))


def test_settings_pulldown_item_help(session):
    sock, _ = session
    _login(sock)
    sock.sendall(_reply(fields={ZCMD_ADDR: "0"}))          # open the Settings panel
    settings = _text(_recv_screen(sock))
    assert "ISPF Settings" in settings
    # Enter with the cursor on the "Log/List" action-bar choice opens its pull-down.
    sock.sendall(_reply(cursor=0 * 80 + 2))
    pulldown = _text(_recv_screen(sock))
    assert "Log Data Set defaults" in pulldown
    # PF1 with the cursor on that item shows the item's <pdc help=...> panel.
    sock.sendall(_reply(aid=PF1, cursor=2 * 80 + 2))
    assert "Log Data Set Defaults HELP" in _text(_recv_screen(sock))


def test_pf3_from_menu_logs_off(session):
    sock, _ = session
    _login(sock)
    sock.sendall(_reply(aid=PF3))
    assert "LOGON" in _text(_recv_screen(sock))        # back to the logon panel
