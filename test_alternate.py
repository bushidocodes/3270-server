"""Tests for alternate-screen (model 3/4/5) rendering.

Every 3270 model shares a 24x80 *default* space; models 3/4/5 also have a larger
*alternate* space (32x80, 43x80, 27x132) selected by ERASE/WRITE ALTERNATE. A
Screen renders there when ``alternate`` is set, sizing buffer addresses by its
``width``. The Browse panel uses this so a bigger terminal shows more lines.
"""
import server
from screen import (
    Screen, Text, Field, DisplayIntensity,
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


def test_wide_screen_field_read_back_keys_by_width():
    # #347: a field below row 0 on the 27x132 alternate screen must key its
    # read-back address by the real width. Field(5, 10, 8) renders its data at
    # 5*132 + 11 = 671, not the fixed-width 5*80 + 11 = 411.
    s = Screen(width=132, depth=27, alternate=True).add(
        Field(5, 10, 8, name="member"))
    addr = 5 * 132 + 11
    assert bytes([0x11]) + encode_pack_addr(5, 10, 132) in s.render()
    assert s.field_addr("member") == addr
    aid, named = s.parse(0x7D, {addr: "PAYROLL"})
    assert (aid, named) == (0x7D, {"member": "PAYROLL"})
    # the stale 80-column key must NOT resolve to the field
    assert s.parse(0x7D, {5 * 80 + 11: "PAYROLL"})[1] == {}


def test_wide_screen_help_for_keys_by_width():
    # #347: HELP with the cursor on a help= field of a 132-col screen must find
    # the field at its real (width-sized) address span.
    s = Screen(width=132, depth=27, alternate=True).add(
        Field(5, 10, 8, name="member", help="memhelp"))
    assert s.help_for(5 * 132 + 11) == "memhelp"        # first data byte
    assert s.help_for(5 * 132 + 11 + 7) == "memhelp"    # last data byte
    assert s.help_for(5 * 132 + 11 + 8) is None         # just past the field
    assert s.help_for(5 * 80 + 11) is None              # the stale 80-col address


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
_PF8 = bytes([0xF8, 0xFF, 0xEF])   # a PF8 reply → page down


# ── scroll amount + help paging (#281) ───────────────────────────────────────

def test_scroll_amount_interprets_ispf_values():
    # PAGE / HALF / MAX / CSR / n / blank, as ISPF's SCROLL field does.
    assert server._scroll_amount("PAGE", 20) == 20
    assert server._scroll_amount("", 20) == 20            # blank defaults to PAGE
    assert server._scroll_amount("garbage", 20) == 20     # unknown → PAGE
    assert server._scroll_amount("HALF", 20) == 10
    assert server._scroll_amount("HALF", 1) == 1          # never below 1
    assert server._scroll_amount("5", 20) == 5            # literal n
    assert server._scroll_amount("MAX", 20, total=137) == 137
    assert server._scroll_amount("CSR", 20, cursor_offset=6) == 6
    assert server._scroll_amount("CSR", 20) == 20         # no cursor → PAGE


def test_help_panel_pages_when_it_overflows():
    # #281: a help panel taller than 24 rows is paged. PF8 advances to the next
    # window; the title stays fixed and a "More:" indicator shows.
    fake = _FakeSock([_PF8, _PF3])
    server._show_help(fake, "ispfhelp")
    assert len(fake.sent) >= 2                            # paged (not one screen)
    page1 = fake.sent[0].decode("cp037", errors="replace")
    page2 = fake.sent[1].decode("cp037", errors="replace")
    assert "Primary Option Menu - HELP" in page1          # title fixed on both
    assert "Primary Option Menu - HELP" in page2
    assert "More:" in page1                                # scroll indicator
    # content moved: page 2 shows text that page 1 did not
    assert "Press PF3 at any time" in page2
    assert "Press PF3 at any time" not in page1


def test_short_help_panel_is_not_paged():
    # A help panel that fits on 24 rows is shown as a single overlay (unchanged
    # path): one screen, no "More:" indicator.
    fake = _FakeSock([_PF3])
    server._show_help(fake, "sizehelp")
    assert len(fake.sent) == 1
    only = fake.sent[0].decode("cp037", errors="replace")
    assert "More:" not in only


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


# ── the member list (Utilities → Library) on the alternate screen ────────────

def test_member_list_default_screen_pages_on_model_2():
    fake = _FakeSock([_PF3])
    server._show_member_list(fake, model=server.parse_terminal_type("IBM-3278-2"))
    screen = fake.sent[0]
    assert screen[0] == ERASE_WRITE                       # the default 24x80 space
    assert "Member 1-17 of" in screen.decode("cp037", errors="replace")  # 24 - 7/page


def test_member_list_alternate_shows_all_on_model_4():
    fake = _FakeSock([_PF3])
    server._show_member_list(fake, model=server.parse_terminal_type("IBM-3278-4"))
    screen = fake.sent[0]
    assert screen[0] == ERASE_WRITE_ALTERNATE            # the 43-row alternate screen
    n = len(server._library_members())
    # A model 4 has room for 35 members/page, so all of them show at once.
    assert f"Member 1-{n} of {n}" in screen.decode("cp037", errors="replace")


def test_member_list_model_5_uses_132_column_addressing():
    fake = _FakeSock([_PF3])
    server._show_member_list(fake, model=server.parse_terminal_type("IBM-3278-5"))
    data = fake.sent[0]
    assert data[0] == ERASE_WRITE_ALTERNATE
    # The footer sits on row 25 (27 - 2) and is addressed against the 132 width —
    # the same width the point-and-shoot cursor decode divides by.
    assert bytes([0x11]) + encode_pack_addr(25, 0, 132) in data
    assert bytes([0x11]) + encode_pack_addr(25, 0, 80) not in data
