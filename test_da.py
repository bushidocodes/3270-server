"""Tests for the DTL data area (<da> / <attr>) attribute-character construct.

A ``<da>`` is a free-form region; nested ``<attr>`` tags define attribute
characters (``ATTRCHAR`` + ``TYPE`` + ``COLOR``/``HILITE``/``PADC``) that, when
they appear in the area's body text, start a field of that type — ``datain`` an
unprotected input field, ``dataout``/``char`` a protected display field — the
classic ISPF )ATTR + )BODY model. The field's text runs to the next attribute
character.
"""
import pytest

from dtl import load_dtl, DTLError
from screen import Text, Field, Color, Highlight, SFE


def _panel(body):
    return load_dtl(f"<panel>{body}</panel>")


def _texts(s):
    return [i for i in s.items if isinstance(i, Text)]


def _fields(s):
    return [i for i in s.items if isinstance(i, Field)]


def test_attr_chars_delimit_typed_colored_fields():
    s = _panel(
        '<da row="4" col="2">'
        '<attr attrchar="$" type="char" color="red">'
        '<attr attrchar="|" type="dataout" color="green">'
        '<attr attrchar="#" type="datain" color="blue" padc="_">'
        "\n    $Name:  |Ada\n    $Card:  #_______\n"
        "</da>")
    texts = _texts(s)
    # Two red labels, one green output value.
    reds = [t for t in texts if t.color is Color.RED]
    greens = [t for t in texts if t.color is Color.GREEN]
    assert [t.text for t in reds] == ["Name:  ", "Card:  "]
    assert [t.text for t in greens] == ["Ada"]
    # Column alignment: the attribute char occupies its own cell; text follows.
    assert reds[0].row == 4 and reds[0].col == 2
    assert greens[0].col == 2 + len("$Name:  ")


def test_datain_is_input_field_sized_by_its_run():
    s = _panel(
        '<da row="1" col="1">'
        '<attr attrchar="#" type="datain" color="blue" padc="_">'
        "\n#________\n"
        "</da>")
    fields = _fields(s)
    assert len(fields) == 1
    f = fields[0]
    assert f.color is Color.BLUE
    assert f.length == 8          # the run of pad cells sets the width
    assert f.default == ""        # ...but the field starts empty (pad, not data)


def test_da_hilite_attribute():
    s = _panel(
        '<da row="1" col="1">'
        '<attr attrchar="$" type="char" color="yellow" hilite="reverse">'
        "\n$WARNING\n"
        "</da>")
    t = _texts(s)[0]
    assert t.color is Color.YELLOW and t.highlight is Highlight.REVERSE


def test_da_mono_vs_color():
    s = _panel(
        '<da row="1" col="1"><attr attrchar="$" type="char" color="red">'
        "\n$hello\n</da>")
    assert SFE not in s.render(color=False)
    assert SFE in s.render(color=True)


def test_multiple_attr_chars_on_one_line():
    s = _panel(
        '<da row="1" col="1">'
        '<attr attrchar="$" type="char" color="red">'
        '<attr attrchar="|" type="char" color="green">'
        "\n$A|B$C\n</da>")
    seq = [(t.text, t.color) for t in _texts(s)]
    assert seq == [("A", Color.RED), ("B", Color.GREEN), ("C", Color.RED)]


def test_attr_outside_da_is_ignored_not_fatal():
    # Panel-scope <attr> (CUA type defs) isn't modelled yet, but must not abort
    # the panel — the info line still renders.
    s = _panel('<attr attrchar="!" type="FP">'
               '<info row="1" col="1">HELLO</info>')
    assert any(t.text == "HELLO" for t in _texts(s))


def test_attr_missing_attrchar_in_da_raises():
    with pytest.raises(DTLError):
        _panel('<da row="1" col="1"><attr type="char" color="red"></da>')


def test_empty_da_body_renders_nothing():
    # The guide's ex091/ex102 define attributes but have no body — no crash.
    s = _panel(
        '<da row="4" col="2" depth="6">'
        '<attr attrchar="#" type="datain" padc="_" color="blue">'
        '<attr attrchar="$" type="char" color="red">'
        "</da>")
    assert _texts(s) == [] and _fields(s) == []
