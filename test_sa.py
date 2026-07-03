"""Tests for character-level Set Attribute (SA, 0x28).

A field's colour/highlight normally comes from its field start (SF/SFE) and
applies to the whole field. An SA order sets a character attribute mid-field, so
one field can carry mixed colour — an emphasised keyword inside a line of text —
without being split into separate fields. SA is emitted only on a colour render;
a mono render just concatenates the text, so it stays byte-for-byte unchanged.
"""
from screen import (
    Screen, Text, Color, Highlight,
    SA, SFE, SF, XA_FOREGROUND, XA_HIGHLIGHT,
)


def _render(item, color=False):
    buf = bytearray()
    item.render(buf, color=color)
    return bytes(buf)


# ── the rich-text model ──────────────────────────────────────────────────────

def test_rich_builds_text_from_runs():
    t = Text.rich(1, 0, [("Press ", None), ("ENTER", Color.RED), (" now", None)])
    assert t.text == "Press ENTER now"
    assert t.runs == [("Press ", None, None), ("ENTER", Color.RED, None),
                      (" now", None, None)]


def test_rich_mono_is_byte_identical_to_plain():
    runs = [("Enter ", None), ("X", Color.WHITE), (" or ", None),
            ("PF3", Color.WHITE), (" to exit", None)]
    rich = Text.rich(1, 1, runs, role="inst")
    plain = Text(1, 1, "Enter X or PF3 to exit", role="inst")
    # No SA on a mono terminal — the whole point of keeping panels unchanged.
    assert SA not in _render(rich, color=False)
    assert _render(rich, color=False) == _render(plain, color=False)


# ── the colour render ────────────────────────────────────────────────────────

def test_rich_colour_emits_sa_per_run():
    rich = Text.rich(1, 0, [("A", Color.GREEN), ("B", Color.RED)])
    out = _render(rich, color=True)
    assert SA in out
    # Each run sets its own foreground with an SA order.
    assert bytes([SA, XA_FOREGROUND, Color.GREEN.value]) in out
    assert bytes([SA, XA_FOREGROUND, Color.RED.value]) in out


def test_plain_run_falls_back_to_the_field_base_colour():
    # A run with color=None re-asserts the field's base (role) colour, so text
    # returns to normal after an emphasised phrase — here the base is GREEN
    # (role "text"), the emphasis RED.
    rich = Text.rich(1, 0, [("x", None), ("Y", Color.RED), ("z", None)], role="text")
    out = _render(rich, color=True)
    green = Color.GREEN.value
    # The two plain runs both emit SA foreground = base green; the middle is red.
    assert out.count(bytes([SA, XA_FOREGROUND, green])) == 2
    assert bytes([SA, XA_FOREGROUND, Color.RED.value]) in out


def test_rich_highlight_run():
    rich = Text.rich(1, 0, [("hi", Color.TURQUOISE, Highlight.REVERSE)])
    out = _render(rich, color=True)
    assert bytes([SA, XA_HIGHLIGHT, Highlight.REVERSE.value]) in out
    assert bytes([SA, XA_FOREGROUND, Color.TURQUOISE.value]) in out


def test_rich_field_still_starts_with_sfe_base():
    # The field still opens with SF/SFE for its base attribute; SA refines it.
    rich = Text.rich(2, 5, [("a", Color.RED)], role="text")
    out = _render(rich, color=True)
    assert SFE in out            # base attribute for the field (role colour)
    assert out.index(SFE) < out.index(SA)   # field start precedes the runs


def test_rich_screen_render_threads_color():
    scr = Screen().add(Text.rich(1, 0, [("a", Color.RED), ("b", None)]))
    assert SA not in scr.render(color=False)
    assert SA in scr.render(color=True)
