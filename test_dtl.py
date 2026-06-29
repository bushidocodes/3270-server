"""DTL parser tests.

The headline assertion: the panels expressed in DTL markup
(``panels/*.dtl``) parse to Screens that render *byte-for-byte identically* to
the hand-built Phase 1 equivalents in :mod:`screens`. That chains back to the
Phase 1 golden test (which ties those builders to the original server output),
so DTL → Screen → bytes is proven equal to the live wire format.
"""
import pytest

from dtl import load_dtl, load_panel, DTLError
from screen import Screen, Text, Field, DisplayIntensity
from screens import build_tso_logon, build_ispf_menu


# ── golden: DTL panels == Phase 1 builders ───────────────────────────────────

def test_logon_dtl_matches_builder():
    assert load_panel("logon").render() == build_tso_logon().render()


def test_ispf_dtl_matches_builder():
    userid, time_str = "IBMUSER", "13:45"
    got = load_panel("ispf", userid=userid.ljust(8), time=time_str).render()
    assert got == build_ispf_menu(userid, time_str).render()


def test_ispf_dtl_other_userid_and_time():
    userid, time_str = "TESTUSER", "09:02"
    got = load_panel("ispf", userid=userid.ljust(8), time=time_str).render()
    assert got == build_ispf_menu(userid, time_str).render()


# ── field names survive the round trip ───────────────────────────────────────

def test_logon_dtl_field_addresses():
    s = load_panel("logon")
    assert s.field_addr("userid") == 5 * 80 + 17
    assert s.field_addr("password") == 6 * 80 + 17
    assert s.field_addr("command") == 9 * 80 + 17


def test_ispf_dtl_option_field_and_title():
    s = load_panel("ispf", userid="IBMUSER ", time="13:45")
    assert s.field_addr("option") == 2 * 80 + 14
    assert s.title == "ISPF Primary Option Menu"


# ── parser unit behaviour ────────────────────────────────────────────────────

def test_info_basic():
    s = load_dtl('<panel><info row="1" col="2">hello</info></panel>')
    assert s.items == [Text(1, 2, "hello", DisplayIntensity.NORMAL)]


def test_info_intensity_and_fill():
    s = load_dtl(
        '<panel>'
        '<info row="0" col="0" intensity="high">hi</info>'
        '<info row="1" col="0" fill="-" width="5"/>'
        '</panel>'
    )
    assert s.items[0] == Text(0, 0, "hi", DisplayIntensity.HIGH)
    assert s.items[1] == Text(1, 0, "-----", DisplayIntensity.NORMAL)


def test_dtafld_emits_prompt_then_field():
    s = load_dtl(
        '<panel><dtafld row="5" col="1" fldcol="16" datavar="userid" '
        'entwidth="8" cursor="yes">Userid ===></dtafld></panel>'
    )
    assert s.items[0] == Text(5, 1, "Userid ===>", DisplayIntensity.NORMAL)
    fld = s.items[1]
    assert isinstance(fld, Field)
    assert (fld.row, fld.col, fld.length, fld.name, fld.cursor) == (5, 16, 8, "userid", True)


def test_dtafld_hidden_and_numeric_and_default():
    s = load_dtl(
        '<panel>'
        '<dtafld row="6" col="1" fldcol="16" datavar="pw" entwidth="8" hidden="yes">P</dtafld>'
        '<dtafld row="8" col="1" fldcol="16" datavar="sz" entwidth="5" numeric="yes" default="00150">S</dtafld>'
        '</panel>'
    )
    pw = s.items[1]
    assert pw.hidden and not pw.numeric
    sz = s.items[3]
    assert sz.numeric and sz.default == "00150"


def test_dtafld_prompt_from_dtafldd_child():
    # Authentic DTL: the prompt is the text of a nested <dtafldd>.
    s = load_dtl(
        '<panel><dtafld row="5" col="1" fldcol="16" datavar="userid" entwidth="8">'
        '<dtafldd>Userid ===></dtafldd></dtafld></panel>'
    )
    assert s.items[0] == Text(5, 1, "Userid ===>", DisplayIntensity.NORMAL)
    assert isinstance(s.items[1], Field)
    assert (s.items[1].col, s.items[1].name) == (16, "userid")


def test_dtafld_dtafldd_equivalent_to_text_shorthand():
    # The <dtafldd> child and the inline-text shorthand render identically.
    inline = load_dtl(
        '<panel><dtafld row="5" col="1" fldcol="16" datavar="u" entwidth="8">'
        'Userid ===></dtafld></panel>'
    )
    nested = load_dtl(
        '<panel>\n'
        '  <dtafld row="5" col="1" fldcol="16" datavar="u" entwidth="8">\n'
        '    <dtafldd>Userid ===></dtafldd>\n'
        '  </dtafld>\n'
        '</panel>'
    )
    assert inline.render() == nested.render()


def test_dtafld_mdt_defaults_true():
    s = load_dtl('<panel><dtafld row="1" col="1" datavar="x" entwidth="4">L</dtafld></panel>')
    assert s.items[1].mdt is True


def test_selfld_lays_out_choices_on_incrementing_rows():
    s = load_dtl(
        '<panel><selfld row="4" numcol="1" namecol="4" desccol="21">'
        '<choice num="0" name="  A">  desc-a</choice>'
        '<choice num="10" name="  B">  desc-b</choice>'
        '</selfld></panel>'
    )
    # choice 0 → row 4, choice 1 → row 5; num is left-justified to numwidth (2)
    assert s.items[0] == Text(4, 1, "0 ", DisplayIntensity.HIGH)
    assert s.items[1] == Text(4, 4, "  A", DisplayIntensity.NORMAL)
    assert s.items[2] == Text(4, 21, "  desc-a", DisplayIntensity.NORMAL)
    assert s.items[3] == Text(5, 1, "10", DisplayIntensity.HIGH)


def test_substitution():
    s = load_dtl('<panel><info row="1" col="1">Hi ${who}</info></panel>', who="BOB")
    assert s.items[0].text == "Hi BOB"


def test_missing_required_attr_raises():
    with pytest.raises(DTLError):
        load_dtl('<panel><info col="1">x</info></panel>')


def test_choice_outside_selfld_raises():
    with pytest.raises(DTLError):
        load_dtl('<panel><choice num="0" name="A">d</choice></panel>')
