"""Tests for 3270 extended colour / highlighting attributes.

Colour is opt-in per screen item and emitted only when a screen is rendered
with ``color=True`` (a colour-capable terminal). A mono render — and any item
without a colour — emits the classic Start Field (0x1D), so existing panels are
byte-for-byte unchanged. A colour render emits Start Field Extended (0x29)
carrying the basic field attribute plus colour/highlight pairs.
"""
import server
from screen import (
    Screen, Text, Field, Color, Highlight,
    SF, SFE, XA_BASIC, XA_FOREGROUND, XA_HIGHLIGHT,
)
from dtl import load_panel, load_dtl


def load_panel_src(src):
    """Parse a bare DTL panel body string into a Screen (test helper)."""
    return load_dtl(f"<panel>{src}</panel>")


def _render(item, color=False):
    buf = bytearray()
    item.render(buf, color=color)
    return bytes(buf)


# ── the screen model ─────────────────────────────────────────────────────────

def test_mono_text_uses_plain_sf():
    # No colour anywhere: classic SF, whether or not colour is enabled.
    plain = Text(1, 0, "HI")
    assert SFE not in _render(plain, color=False)
    assert SF in _render(plain, color=False)
    assert _render(plain, color=True) == _render(plain, color=False)


def test_colored_text_is_mono_on_mono_terminal():
    """A coloured item still renders identically to an uncoloured one when the
    terminal is mono — this is what keeps the bundled panels byte-identical."""
    colored = Text(1, 0, "HI", color=Color.TURQUOISE)
    plain = Text(1, 0, "HI")
    assert _render(colored, color=False) == _render(plain, color=False)


def test_colored_text_emits_sfe_pair():
    out = _render(Text(1, 0, "HI", color=Color.TURQUOISE), color=True)
    i = out.index(SFE)
    # SFE, count=2 (basic + foreground), C0 <fa>, 42 <turquoise>
    assert out[i + 1] == 2
    assert out[i + 2] == XA_BASIC
    assert out[i + 4] == XA_FOREGROUND
    assert out[i + 5] == Color.TURQUOISE.value


def test_highlight_and_color_together_three_pairs():
    out = _render(Text(1, 0, "X", color=Color.RED, highlight=Highlight.REVERSE),
                  color=True)
    i = out.index(SFE)
    assert out[i + 1] == 3          # basic + foreground + highlight
    body = out[i + 2:i + 8]
    assert (XA_FOREGROUND, Color.RED.value) in list(zip(body[0::2], body[1::2]))
    assert (XA_HIGHLIGHT, Highlight.REVERSE.value) in list(zip(body[0::2], body[1::2]))


def test_default_color_does_not_force_sfe():
    # Color.DEFAULT means "no explicit colour" — should not emit SFE.
    assert SFE not in _render(Text(1, 0, "HI", color=Color.DEFAULT), color=True)


def test_field_color_and_hidden_never_colored():
    fld = Field(1, 0, 8, name="u", color=Color.WHITE)
    assert SFE in _render(fld, color=True)
    # A hidden (password) field keeps its non-display attribute, never coloured.
    hidden = Field(1, 0, 8, name="p", hidden=True, color=Color.WHITE)
    assert SFE not in _render(hidden, color=True)


def test_screen_render_threads_color_flag():
    scr = Screen().text(1, 0, "A").add(Text(2, 0, "B", color=Color.GREEN))
    assert SFE not in scr.render(color=False)
    assert SFE in scr.render(color=True)


# ── DTL colour attributes ────────────────────────────────────────────────────

def test_dtl_color_attribute_parsed():
    s = load_panel_src('<info row="1" col="1" color="turquoise">HELLO</info>')
    text = next(i for i in s.items if isinstance(i, Text))
    assert text.color is Color.TURQUOISE
    assert SFE in s.render(color=True)
    assert SFE not in s.render(color=False)


def test_dtl_fldcolor_differs_from_prompt():
    s = load_panel_src(
        '<dtafld row="1" col="1" fldcol="20" datavar="u" entwidth="8"'
        ' color="turquoise" fldcolor="white">Name ===></dtafld>')
    prompt = next(i for i in s.items if isinstance(i, Text))
    field = next(i for i in s.items if isinstance(i, Field))
    assert prompt.color is Color.TURQUOISE
    assert field.color is Color.WHITE


# ── logon panel end-to-end ───────────────────────────────────────────────────

def test_logon_panel_mono_vs_color():
    assert SFE not in load_panel("logon").render(color=False)
    assert SFE in load_panel("logon").render(color=True)


class _FakeSocket:
    def __init__(self):
        self.sent = bytearray()

    def sendall(self, data):
        self.sent += data


def test_send_tso_logon_colors_for_color_terminal():
    color_model = server.parse_terminal_type("IBM-3279-2")   # colour
    mono_model = server.parse_terminal_type("IBM-3278-2")    # mono

    sock = _FakeSocket()
    server.send_tso_logon(sock, model=color_model)
    assert SFE in sock.sent

    sock = _FakeSocket()
    server.send_tso_logon(sock, model=mono_model)
    assert SFE not in sock.sent


def test_logon_error_is_red_on_color_terminal():
    color_model = server.parse_terminal_type("IBM-3279-2")
    sock = _FakeSocket()
    server.send_tso_logon(sock, error_msg="IKJ56425I PASSWORD NOT CORRECT",
                          model=color_model)
    # The red error field renders as SFE ... 42 F2 (foreground red).
    assert bytes([XA_FOREGROUND, Color.RED.value]) in sock.sent
