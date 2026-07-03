"""Tests for 3270 extended colour / highlighting attributes.

Colour is opt-in per screen item and emitted only when a screen is rendered
with ``color=True`` (a colour-capable terminal). A mono render — and any item
without a colour — emits the classic Start Field (0x1D), so existing panels are
byte-for-byte unchanged. A colour render emits Start Field Extended (0x29)
carrying the basic field attribute plus colour/highlight pairs.
"""
import server
from screen import (
    Screen, Text, Field, Color, Highlight, _role_colour,
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


# ── DTL COLOR attribute (a real DTL attribute on field elements) ─────────────

def test_dtl_dtafld_color_is_on_the_field_not_the_prompt():
    # DTL's COLOR on a <dtafld> colours the entry field; the caption/prompt is a
    # CUA element with its own (default) colour.
    s = load_panel_src(
        '<dtafld row="1" col="1" fldcol="20" datavar="u" entwidth="8"'
        ' color="turq">Name ===></dtafld>')
    prompt = next(i for i in s.items if isinstance(i, Text))
    field = next(i for i in s.items if isinstance(i, Field))
    assert prompt.color is None
    assert field.color is Color.TURQUOISE
    assert SFE in s.render(color=True)
    assert SFE not in s.render(color=False)


def test_dtl_color_keywords():
    for kw, want in [("white", Color.WHITE), ("red", Color.RED), ("blue", Color.BLUE),
                     ("green", Color.GREEN), ("pink", Color.PINK),
                     ("yellow", Color.YELLOW), ("turq", Color.TURQUOISE)]:
        s = load_panel_src(
            f'<dtafld row="1" col="1" fldcol="10" datavar="u" color="{kw}">X</dtafld>')
        field = next(i for i in s.items if isinstance(i, Field))
        assert field.color is want, kw


def test_dtl_color_percent_variable():
    # COLOR=%VAR takes its value from a dialog variable (like &VAR substitution).
    s = load_dtl('<panel><dtafld row="1" col="1" fldcol="10" datavar="u"'
                 ' color="%HILITEC">X</dtafld></panel>', HILITEC="red")
    field = next(i for i in s.items if isinstance(i, Field))
    assert field.color is Color.RED


def test_dtl_info_takes_cua_colour_not_explicit_color():
    # <info> is not COLOR-bearing: a stray color= is ignored. But on a colour
    # terminal it still renders in its CUA role colour (normal text → green),
    # never the explicit red.
    s = load_panel_src('<info row="1" col="1" color="red">HELLO</info>')
    data = s.render(color=True)
    assert bytes([XA_FOREGROUND, Color.GREEN.value]) in data     # CUA green applied
    assert bytes([XA_FOREGROUND, Color.RED.value]) not in data   # explicit red ignored
    assert SFE not in s.render(color=False)                      # mono unchanged


def test_dtl_hilite_attribute():
    s = load_panel_src(
        '<dtafld row="1" col="1" fldcol="10" datavar="u"'
        ' color="green" hilite="reverse">X</dtafld>')
    field = next(i for i in s.items if isinstance(i, Field))
    assert field.highlight is Highlight.REVERSE


def test_dtl_selfld_colors_choices():
    s = load_panel_src(
        '<selfld row="3" numcol="2" namecol="5" desccol="10" color="turq">'
        '<choice num="1" name="A">Alpha</choice></selfld>')
    texts = [i for i in s.items if isinstance(i, Text)]
    assert texts and all(t.color is Color.TURQUOISE for t in texts)


def test_dtl_lstcol_colors_cells():
    s = load_dtl(
        '<panel><lstfld row="2" col="1">'
        '<lstcol datavar="v" usage="out" colwidth="6" color="yellow">Val</lstcol>'
        '</lstfld></panel>', rows=[{"v": "HI"}])
    cell = next(i for i in s.items if isinstance(i, Text) and i.text.strip() == "HI")
    assert cell.color is Color.YELLOW


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


# ── CUA element-role colouring (matches real z/OS ISPF) ──────────────────────

def test_cua_menu_colours_match_zos():
    """The ISPF menu is coloured by CUA element role, matching a real z/OS
    Primary Option Menu: white title/numbers, turquoise keywords/entry, green
    prompt/descriptions/text, blue separator rules."""
    s = load_panel("ispf", ZUSER="IBMUSER ", ZTIME="13:45")
    by_role = {}
    for it in s.items:
        r = getattr(it, "role", None)
        if r and r not in by_role:
            by_role[r] = _role_colour(it.color, it.role)
    assert by_role["title"] is Color.WHITE
    assert by_role["prompt"] is Color.GREEN
    assert by_role["field"] is Color.TURQUOISE
    assert by_role["num"] is Color.WHITE
    assert by_role["name"] is Color.TURQUOISE
    assert by_role["desc"] is Color.GREEN
    assert by_role["text"] is Color.GREEN
    assert by_role["rule"] is Color.BLUE


def test_ispf_menu_mono_is_byte_identical_colour_adds_sfe():
    s = load_panel("ispf", ZUSER="IBMUSER ", ZTIME="13:45")
    assert SFE not in s.render(color=False)   # mono: unchanged, no extended attrs
    assert SFE in s.render(color=True)         # colour terminal: CUA colours emit SFE


def test_explicit_color_overrides_cua_role():
    # A logon entry field carries an explicit COLOR=WHITE, overriding the field
    # role's turquoise default.
    s = load_panel("logon")
    field = next(i for i in s.items if isinstance(i, Field) and i.name == "userid")
    assert field.role == "field"
    assert _role_colour(field.color, field.role) is Color.WHITE   # explicit wins
