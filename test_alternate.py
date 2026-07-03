"""Tests for alternate-screen (model 3/4/5) rendering.

Every 3270 model shares a 24x80 *default* space; models 3/4/5 also have a larger
*alternate* space (32x80, 43x80, 27x132) selected by ERASE/WRITE ALTERNATE. A
Screen renders there when ``alternate`` is set, sizing buffer addresses by its
``width``. The Browse panel uses this so a bigger terminal shows more lines.
"""
import server
from screen import (
    Screen, Text, DisplayIntensity,
    ERASE_WRITE, ERASE_WRITE_ALTERNATE, encode_pack_addr,
)


# ── the screen model ─────────────────────────────────────────────────────────

def test_default_screen_uses_erase_write():
    assert Screen().add(Text(1, 0, "X")).render()[0] == ERASE_WRITE


def test_alternate_screen_uses_erase_write_alternate():
    s = Screen(width=80, depth=32, alternate=True).add(Text(1, 0, "X"))
    assert s.render()[0] == ERASE_WRITE_ALTERNATE


def test_wide_screen_addresses_by_width():
    # A Text at col 100 exists only on a wide (132-col) screen; its SBA must
    # encode row*132+col, not the default row*80+col.
    s = Screen(width=132, depth=27, alternate=True).add(Text(5, 100, "Z"))
    data = s.render()
    assert bytes([0x11]) + encode_pack_addr(5, 100, 132) in data
    assert bytes([0x11]) + encode_pack_addr(5, 100, 80) not in data


def test_default_width_render_is_unchanged():
    # width=80 must go through exactly the old address math (byte-for-byte).
    mono = Screen().add(Text(3, 7, "hello", DisplayIntensity.HIGH)).render()
    assert mono[0] == ERASE_WRITE
    assert bytes([0x11]) + encode_pack_addr(3, 7) in mono


# ── model → screen size ──────────────────────────────────────────────────────

def test_screen_size_by_model():
    size = lambda t: server._screen_size(server.parse_terminal_type(t))
    assert size("IBM-3278-2") == (24, 80)
    assert size("IBM-3278-3") == (32, 80)
    assert size("IBM-3279-4-E") == (43, 80)
    assert size("IBM-3278-5") == (27, 132)
    assert server._screen_size(None) == (24, 80)


# ── Browse on the alternate screen ───────────────────────────────────────────

class _FakeSock:
    """Captures sent records; replies with a canned inbound record, then EOF."""

    def __init__(self, replies):
        self.sent = []
        self._replies = iter(replies)

    def sendall(self, data):
        self.sent.append(data)

    def recv(self, _n):
        return next(self._replies, b"")   # b"" ends the read (disconnect)

    def settimeout(self, _t):
        pass

    def close(self):
        pass


_PF3 = bytes([0xF3, 0xFF, 0xEF])   # a PF3 reply → Browse's keylist EXITs


def test_browse_alternate_shows_more_lines_on_model_3():
    # ispf.dtl has 54 lines; a model 3 (32 rows) shows 30 per page (32 - header
    # - footer), on the alternate screen.
    path = server._member_path("ispf")
    fake = _FakeSock([_PF3])
    server._show_browse(fake, "ispf", path,
                        model=server.parse_terminal_type("IBM-3278-3"))
    screen = fake.sent[0]
    assert screen[0] == ERASE_WRITE_ALTERNATE
    assert "Lines 1-30 of" in screen.decode("cp037", errors="replace")


def test_browse_default_screen_on_model_2():
    path = server._member_path("ispf")
    fake = _FakeSock([_PF3])
    server._show_browse(fake, "ispf", path,
                        model=server.parse_terminal_type("IBM-3278-2"))
    screen = fake.sent[0]
    assert screen[0] == ERASE_WRITE           # the default space, not alternate
    assert "Lines 1-22 of" in screen.decode("cp037", errors="replace")


def test_browse_footer_on_the_last_row():
    # The footer rule sits on the last row of whatever screen we render on.
    path = server._member_path("ispf")
    fake = _FakeSock([_PF3])
    server._show_browse(fake, "ispf", path,
                        model=server.parse_terminal_type("IBM-3278-4"))  # 43 rows
    data = fake.sent[0]
    # SBA to (row 42, col 0) — the last row of a 43-row screen — precedes the footer.
    assert bytes([0x11]) + encode_pack_addr(42, 0, 80) in data
    assert "Lines 1-41 of" in data.decode("cp037", errors="replace")  # 43 - 2
