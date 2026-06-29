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
    got = load_panel("ispf", ZUSER=userid.ljust(8), ZTIME=time_str).render()
    assert got == build_ispf_menu(userid, time_str).render()


def test_ispf_dtl_other_userid_and_time():
    userid, time_str = "TESTUSER", "09:02"
    got = load_panel("ispf", ZUSER=userid.ljust(8), ZTIME=time_str).render()
    assert got == build_ispf_menu(userid, time_str).render()


# ── field names survive the round trip ───────────────────────────────────────

def test_logon_dtl_field_addresses():
    s = load_panel("logon")
    assert s.field_addr("userid") == 5 * 80 + 17
    assert s.field_addr("password") == 6 * 80 + 17
    assert s.field_addr("command") == 9 * 80 + 17


def test_ispf_dtl_option_field_and_title():
    s = load_panel("ispf", ZUSER="IBMUSER ", ZTIME="13:45")
    # The option line is now a <cmdarea>; its variable defaults to ZCMD and it is
    # recorded as the command field at the same address as before (2*80 + 14).
    assert s.field_addr("ZCMD") == 2 * 80 + 14
    assert s.command_field is not None
    assert s.command_field.data_addr == 2 * 80 + 14
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


def test_choice_matchval_defaults_to_num_and_records_selections():
    s = load_dtl(
        '<panel><selfld row="4" numcol="1" namecol="4" desccol="21">'
        '<choice num="0" name="Settings">  desc</choice>'
        '<choice num="X" name="Exit">  bye</choice>'
        '</selfld></panel>'
    )
    assert s.selections == {"0": "Settings", "X": "Exit"}


def test_choice_explicit_matchval_overrides_num():
    s = load_dtl(
        '<panel><selfld row="4" numcol="1" namecol="4" desccol="21">'
        '<choice num="1" name="View" matchval="V">  desc</choice>'
        '</selfld></panel>'
    )
    assert s.selections == {"V": "View"}        # matchval wins over num


def test_ispf_panel_selections_drive_validation():
    s = load_panel("ispf", ZUSER="IBMUSER ", ZTIME="13:45")
    # The menu declares 0-7, 9-13 and X — but not 8.
    for opt in ["0", "3", "13", "X"]:
        assert opt in s.selections
    assert "8" not in s.selections
    assert s.selections["3"] == "Utilities"


def test_substitution():
    # &NAME dialog-variable reference, matched case-insensitively.
    s = load_dtl('<panel><info row="1" col="1">Hi &who</info></panel>', WHO="BOB")
    assert s.items[0].text == "Hi BOB"


def test_substitution_terminator_and_escape():
    # A trailing '.' terminates (and is consumed by) the reference; && is literal.
    s = load_dtl(
        '<panel><info row="1" col="1">&ZUSER.X uses &&</info></panel>', ZUSER="IBMUSER"
    )
    assert s.items[0].text == "IBMUSERX uses &"


def test_substitution_unknown_left_intact():
    s = load_dtl('<panel><info row="1" col="1">keep &NOPE here</info></panel>')
    assert s.items[0].text == "keep &NOPE here"


def test_missing_required_attr_raises():
    with pytest.raises(DTLError):
        load_dtl('<panel><info col="1">x</info></panel>')


def test_choice_outside_selfld_raises():
    with pytest.raises(DTLError):
        load_dtl('<panel><choice num="0" name="A">d</choice></panel>')


# ── keylist (<keyl>/<keyi>) ──────────────────────────────────────────────────

def test_keyl_builds_keylist_and_emits_no_items():
    s = load_dtl(
        '<panel>'
        '<info row="0" col="0">hi</info>'
        '<keyl name="K">'
        '<keyi key="PF1" cmd="HELP">Help</keyi>'
        '<keyi key="PF3" cmd="EXIT">Exit</keyi>'
        '</keyl>'
        '</panel>'
    )
    # The keylist is metadata: it adds no renderable items.
    assert s.items == [Text(0, 0, "hi", DisplayIntensity.NORMAL)]
    assert s.keylist == {"PF1": "HELP", "PF3": "EXIT"}


def test_keyi_key_and_cmd_uppercased_and_resolved():
    s = load_dtl('<panel><keyl><keyi key="pf3" cmd="exit"/></keyl></panel>')
    assert s.keylist == {"PF3": "EXIT"}
    assert s.command_for("PF3") == "EXIT"
    assert s.command_for("pf3") == "EXIT"   # lookup is case-insensitive
    assert s.command_for("PF7") is None     # unbound


# ── typed variables (<varclass>/<varlist>/<vardcl>) ──────────────────────────

def test_vardcl_makes_field_numeric():
    s = load_dtl(
        '<panel>'
        '<varclass name="NUMFLD" type="numeric"/>'
        '<varlist><vardcl name="size" varclass="NUMFLD"/></varlist>'
        '<dtafld row="8" col="1" fldcol="16" datavar="size" entwidth="5">Size</dtafld>'
        '</panel>'
    )
    assert s.items[1].numeric is True   # inherited from the varclass


def test_char_varclass_leaves_field_alphanumeric():
    s = load_dtl(
        '<panel>'
        '<varclass name="C" type="char"/>'
        '<varlist><vardcl name="x" varclass="C"/></varlist>'
        '<dtafld row="1" col="1" fldcol="5" datavar="x" entwidth="4">X</dtafld>'
        '</panel>'
    )
    assert s.items[1].numeric is False


def test_explicit_numeric_attr_overrides_varclass():
    s = load_dtl(
        '<panel>'
        '<varclass name="C" type="char"/>'
        '<varlist><vardcl name="x" varclass="C"/></varlist>'
        '<dtafld row="1" col="1" fldcol="5" datavar="x" entwidth="4" numeric="yes">X</dtafld>'
        '</panel>'
    )
    assert s.items[1].numeric is True   # field attribute wins over the class


def test_vardcl_outside_varlist_raises():
    with pytest.raises(DTLError):
        load_dtl('<panel><vardcl name="x" varclass="C"/></panel>')


def test_varclass_missing_name_raises():
    with pytest.raises(DTLError):
        load_dtl('<panel><varclass type="numeric"/></panel>')


# ── command area (<cmdarea>) ─────────────────────────────────────────────────

def test_cmdarea_renders_like_dtafld_and_records_command_field():
    cmd = load_dtl(
        '<panel><cmdarea row="2" col="1" fldcol="13" entwidth="6" cursor="yes">'
        'Option ===></cmdarea></panel>'
    )
    # Same prompt + field bytes as the equivalent <dtafld> (name aside).
    fld = load_dtl(
        '<panel><dtafld row="2" col="1" fldcol="13" datavar="ZCMD" entwidth="6" '
        'cursor="yes">Option ===></dtafld></panel>'
    )
    assert cmd.render() == fld.render()
    assert cmd.command_field is cmd.items[1]
    assert cmd.command_field.name == "ZCMD"          # default ISPF command var
    assert cmd.field_addr("ZCMD") == 2 * 80 + 14


def test_cmdarea_datavar_override_and_command_value():
    s = load_dtl(
        '<panel><cmdarea row="2" col="1" fldcol="13" datavar="OPT" entwidth="6">'
        'Option ===></cmdarea></panel>'
    )
    assert s.command_field.name == "OPT"
    addr = s.command_field.data_addr
    assert s.command_value({addr: "3   "}) == "3   "
    assert s.command_value({}) is None


def test_command_value_none_without_cmdarea():
    s = load_dtl('<panel><info row="0" col="0">hi</info></panel>')
    assert s.command_field is None
    assert s.command_value({0: "x"}) is None


def test_keyi_outside_keyl_raises():
    with pytest.raises(DTLError):
        load_dtl('<panel><keyi key="PF3" cmd="EXIT"/></panel>')


def test_keyi_missing_key_raises():
    with pytest.raises(DTLError):
        load_dtl('<panel><keyl><keyi cmd="EXIT"/></keyl></panel>')


def test_panels_define_exit_keylist():
    # The shipped panels bind PF3/PF15 to EXIT so server.py resolves logoff
    # from the keylist rather than hard-coded key numbers.
    for name in ("logon", "ispf"):
        s = load_panel(name) if name == "logon" else load_panel(
            name, userid="IBMUSER ", time="13:45"
        )
        assert s.command_for("PF3") == "EXIT"
        assert s.command_for("PF15") == "EXIT"
