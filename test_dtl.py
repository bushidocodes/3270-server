"""DTL parser tests.

The headline assertion: the panels expressed in DTL markup
(``panels/*.dtl``) parse to Screens that render *byte-for-byte identically* to
the hand-built Phase 1 equivalents in :mod:`screens`. That chains back to the
Phase 1 golden test (which ties those builders to the original server output),
so DTL → Screen → bytes is proven equal to the live wire format.
"""
import pytest

from dtl import load_dtl, load_panel, load_messages, load_message_member, DTLError
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


def test_topinst_and_paninst_render_like_info():
    s = load_dtl(
        '<panel>'
        '<topinst row="2" col="1">Enter parameters:</topinst>'
        '<paninst row="16" col="1" intensity="high">Press ENTER</paninst>'
        '</panel>'
    )
    assert s.items[0] == Text(2, 1, "Enter parameters:", DisplayIntensity.NORMAL)
    assert s.items[1] == Text(16, 1, "Press ENTER", DisplayIntensity.HIGH)


def test_instruction_tags_flow_in_area():
    s = load_dtl(
        '<panel><area row="3" col="1">'
        '<topinst>line one</topinst><paninst>line two</paninst>'
        '</area></panel>'
    )
    assert s.items[0] == Text(3, 1, "line one", DisplayIntensity.NORMAL)
    assert s.items[1] == Text(4, 1, "line two", DisplayIntensity.NORMAL)


def test_logon_instruction_tags_byte_identical():
    # The logon panel now uses <topinst>/<paninst> for some lines; bytes unchanged.
    assert load_panel("logon").render() == build_tso_logon().render()


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


# ── command tables (<cmdtbl>/<cmd>/<cmdact>) ─────────────────────────────────

def _cmd_panel():
    return load_dtl(
        '<panel>'
        '<cmdtbl applid="ISR">'
        '<cmd name="PANELID">Toggle<cmdact action="passthru"></cmd>'
        '<cmd name="KEYLIST" trunc="3">Keys<cmdact action="passthru"></cmd>'
        '<cmd name="BYE">Leave<cmdact action="alias exit"></cmd>'
        '</cmdtbl>'
        '</panel>'
    )


def test_cmdtbl_parses_commands_and_actions():
    s = _cmd_panel()
    assert s.commands["PANELID"]["action"] == "passthru"
    assert s.commands["KEYLIST"]["trunc"] == 3
    assert s.commands["BYE"]["action"] == "alias exit"


def test_lookup_command_with_truncation():
    s = _cmd_panel()
    assert s.lookup_command("PANELID") == "passthru"
    assert s.lookup_command("panelid") == "passthru"     # case-insensitive
    assert s.lookup_command("KEY") == "passthru"         # trunc=3 → KEY matches
    assert s.lookup_command("KEYL") == "passthru"
    assert s.lookup_command("KE") is None                # below truncation
    assert s.lookup_command("PAN") is None               # PANELID has no trunc
    assert s.lookup_command("ZZZ") is None               # unknown


def test_cmd_outside_cmdtbl_raises():
    with pytest.raises(DTLError):
        load_dtl('<panel><cmd name="X"><cmdact action="passthru"></cmd></panel>')


def test_ispf_panel_has_command_table():
    s = load_panel("ispf", ZUSER="IBMUSER ", ZTIME="13:45")
    assert s.lookup_command("BYE") == "alias exit"
    assert s.lookup_command("KEY") == "passthru"
    # Command table is metadata — the menu still renders identically.
    assert s.render() == build_ispf_menu("IBMUSER", "13:45").render()


# ── action bars (<ab>/<abc>/<pdc>) ───────────────────────────────────────────

def test_action_bar_renders_labels_and_records_pulldowns():
    s = load_dtl(
        '<panel>'
        '<ab row="0" col="1" gap="3">'
        '<abc>Menu<pdc action="exit">Exit</pdc></abc>'
        '<abc>Help<pdc action="passthru">About</pdc></abc>'
        '</ab>'
        '</panel>'
    )
    # Labels laid out across row 0: Menu at col 1, Help at col 1+4+3 = 8.
    assert s.items[0] == Text(0, 1, "Menu", DisplayIntensity.HIGH)
    assert s.items[1] == Text(0, 8, "Help", DisplayIntensity.HIGH)
    # Pull-down structure + rendered position preserved for interaction.
    assert s.action_bar == [
        {"label": "Menu", "row": 0, "col": 1,
         "pdc": [{"label": "Exit", "action": "exit"}]},
        {"label": "Help", "row": 0, "col": 8,
         "pdc": [{"label": "About", "action": "passthru"}]},
    ]


def test_action_choice_at_maps_cursor_to_choice():
    s = load_dtl(
        '<panel>'
        '<ab row="0" col="1" gap="3">'
        '<abc>Menu<pdc action="x">A</pdc></abc>'
        '<abc>Help<pdc action="y">B</pdc></abc>'
        '</ab>'
        '</panel>'
    )
    # "Menu" label is rendered at cols 2..5 (attr at col 1); "Help" at cols 9..12.
    assert s.action_choice_at(0 * 80 + 3)["label"] == "Menu"
    assert s.action_choice_at(0 * 80 + 9)["label"] == "Help"
    assert s.action_choice_at(0 * 80 + 1) is None    # on the attribute byte
    assert s.action_choice_at(0 * 80 + 40) is None    # not on a choice
    assert s.action_choice_at(None) is None


def test_abc_outside_ab_raises():
    with pytest.raises(DTLError):
        load_dtl('<panel><abc>Menu</abc></panel>')


def test_pdc_outside_abc_raises():
    with pytest.raises(DTLError):
        load_dtl('<panel><ab row="0" col="1"><pdc>x</pdc></ab></panel>')


def test_settings_panel_has_action_bar():
    s = load_panel("settings")
    labels = [c["label"] for c in s.action_bar]
    assert "Log/List" in labels and "Help" in labels
    assert s.command_for("PF3") == "EXIT"   # PF3 returns to the menu


# ── message members (<msgmbr>/<msg>) ─────────────────────────────────────────

def test_msg_member_parses_and_formats_with_substitution():
    cat = load_messages(
        '<msgmbr name="IKJ564">'
        '<msg msgid="IKJ56425I">PASSWORD NOT CORRECT FOR &USERID</msg>'
        '<msg msgid="IKJ56700I">USERID MUST BE SPECIFIED</msg>'
        '</msgmbr>'
    )
    assert cat.format("IKJ56425I", USERID="IBMUSER") == \
        "IKJ56425I PASSWORD NOT CORRECT FOR IBMUSER"
    assert cat.format("IKJ56700I") == "IKJ56700I USERID MUST BE SPECIFIED"
    assert cat.format("NOSUCH") == "NOSUCH"     # unknown id -> just the id


def test_shipped_tso_messages_match_legacy_strings():
    # The DTL message member must reproduce the exact strings server.py used to
    # hard-code, so the rendered logon-with-error screen is unchanged.
    cat = load_message_member("tsomsgs")
    assert cat.format("IKJ56425I", USERID="IBMUSER") == \
        "IKJ56425I PASSWORD NOT CORRECT FOR IBMUSER"
    assert cat.format("IKJ56700I") == "IKJ56700I USERID MUST BE SPECIFIED"


def test_msg_outside_msgmbr_raises():
    with pytest.raises(DTLError):
        load_dtl('<msg msgid="X">hi</msg>')


def test_msg_missing_msgid_raises():
    with pytest.raises(DTLError):
        load_dtl('<msgmbr><msg>hi</msg></msgmbr>')


# ── panel dimensions + bounds checking (<panel width depth>) ─────────────────

def test_panel_dimensions_default_and_explicit():
    assert load_dtl('<panel></panel>').width == 80
    assert load_dtl('<panel></panel>').depth == 24
    s = load_dtl('<panel width="132" depth="43"></panel>')
    assert (s.width, s.depth) == (132, 43)


def test_row_beyond_depth_raises():
    with pytest.raises(DTLError):
        load_dtl('<panel><info row="24" col="0">off-screen</info></panel>')


def test_col_beyond_width_raises():
    with pytest.raises(DTLError):
        load_dtl('<panel><info row="0" col="80">off-screen</info></panel>')


def test_position_valid_within_declared_size():
    # row 30 is illegal on a 24-deep panel but fine when depth is declared 43.
    s = load_dtl('<panel depth="43"><info row="30" col="0">ok</info></panel>')
    assert s.items[0].row == 30


def test_field_overflowing_width_raises():
    with pytest.raises(DTLError):
        load_dtl(
            '<panel><dtafld row="0" col="0" fldcol="70" datavar="x" entwidth="20">'
            'P</dtafld></panel>'
        )


def test_panel_dimensions_do_not_change_bytes():
    assert load_panel("logon").render() == build_tso_logon().render()


# ── help panels (<panel help=...>) ───────────────────────────────────────────

def test_panel_help_attribute_parsed():
    s = load_dtl('<panel name="p" help="phelp"><info row="0" col="0">x</info></panel>')
    assert s.help == "phelp"
    assert load_dtl('<panel name="p"></panel>').help is None


def test_main_panels_reference_their_help_panels():
    assert load_panel("logon").help == "tsohelp"
    assert load_panel("ispf", ZUSER="IBMUSER ", ZTIME="13:45").help == "ispfhelp"


def test_help_panels_load_and_return_on_pf3():
    for name in ("tsohelp", "ispfhelp"):
        s = load_panel(name)
        assert s.command_for("PF3") == "EXIT"   # PF3 returns from help


def test_help_attribute_does_not_change_rendered_bytes():
    # Adding help="..." to <panel> is metadata; the logon panel still matches.
    assert load_panel("logon").render() == build_tso_logon().render()


# ── flow layout (<area>/<region>) ────────────────────────────────────────────

def test_area_flows_rows_and_derives_fldcol():
    s = load_dtl(
        '<panel><area row="5" col="1" fldgap="2">'
        '<dtafld datavar="userid" entwidth="8">Userid   ===></dtafld>'
        '<dtafld datavar="pw" entwidth="8">Password ===></dtafld>'
        '</area></panel>'
    )
    # Prompts at col 1 on flowing rows 5, 6; entries after the 13-char prompt
    # plus fldgap=2 → col 16.
    assert s.items[0] == Text(5, 1, "Userid   ===>", DisplayIntensity.NORMAL)
    assert (s.items[1].row, s.items[1].col, s.items[1].name) == (5, 16, "userid")
    assert s.items[2] == Text(6, 1, "Password ===>", DisplayIntensity.NORMAL)
    assert (s.items[3].row, s.items[3].col, s.items[3].name) == (6, 16, "pw")


def test_area_explicit_position_wins_and_continues_flow():
    s = load_dtl(
        '<panel><area row="5" col="1">'
        '<info>first</info>'              # flows to row 5
        '<info row="9">jump</info>'       # explicit row 9
        '<info>after</info>'              # flow continues at row 10
        '</area></panel>'
    )
    assert s.items[0] == Text(5, 1, "first", DisplayIntensity.NORMAL)
    assert s.items[1] == Text(9, 1, "jump", DisplayIntensity.NORMAL)
    assert s.items[2] == Text(10, 1, "after", DisplayIntensity.NORMAL)


def test_regions_lay_out_side_by_side_columns():
    s = load_dtl(
        '<panel>'
        '<region row="2" col="1"><info>left1</info><info>left2</info></region>'
        '<region row="2" col="40"><info>right1</info><info>right2</info></region>'
        '</panel>'
    )
    assert s.items[0] == Text(2, 1, "left1", DisplayIntensity.NORMAL)
    assert s.items[1] == Text(3, 1, "left2", DisplayIntensity.NORMAL)
    assert s.items[2] == Text(2, 40, "right1", DisplayIntensity.NORMAL)
    assert s.items[3] == Text(3, 40, "right2", DisplayIntensity.NORMAL)


def test_missing_row_outside_area_still_raises():
    with pytest.raises(DTLError):
        load_dtl('<panel><info col="1">x</info></panel>')


def test_logon_area_flow_is_byte_identical():
    # The <area> wrapping in logon.dtl must not change the rendered bytes.
    assert load_panel("logon").render() == build_tso_logon().render()


# ── SGML conformance (DOCTYPE, case-insensitivity, attribute minimization) ───

def test_doctype_prolog_is_tolerated():
    s = load_dtl(
        '<!DOCTYPE DM SYSTEM>\n'
        '<panel><info row="1" col="2">hi</info></panel>'
    )
    assert s.items == [Text(1, 2, "hi", DisplayIntensity.NORMAL)]


def test_tag_and_attribute_names_are_case_insensitive():
    s = load_dtl('<PANEL><INFO ROW="1" COL="2" INTENSITY="high">hi</INFO></PANEL>')
    assert s.items == [Text(1, 2, "hi", DisplayIntensity.HIGH)]


def test_boolean_attribute_minimization():
    # <dtafld hidden> (no value) means hidden="yes"; <... numeric> likewise.
    s = load_dtl(
        '<panel>'
        '<dtafld row="6" col="1" fldcol="16" datavar="pw" entwidth="8" hidden>P</dtafld>'
        '<dtafld row="8" col="1" fldcol="16" datavar="sz" entwidth="5" numeric>S</dtafld>'
        '</panel>'
    )
    assert s.items[1].hidden is True
    assert s.items[3].numeric is True


def test_shipped_panels_have_doctype_and_stay_byte_identical():
    # The DOCTYPE prolog added to the .dtl files must not change the bytes.
    assert load_panel("logon").render() == build_tso_logon().render()


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


def _range_panel():
    return load_dtl(
        '<panel>'
        '<varclass name="SZ" type="numeric">'
        '  <checkl checkmsg="M001"><checki type="range">0 100</checki></checkl>'
        '</varclass>'
        '<varlist><vardcl name="sz" varclass="SZ"/></varlist>'
        '<dtafld row="8" col="1" fldcol="16" datavar="sz" entwidth="5">Size</dtafld>'
        '</panel>'
    )


def test_checkl_attaches_range_validation_to_field():
    s = _range_panel()
    assert s.items[1].numeric is True              # still numeric
    assert "SZ" in s.validations
    assert s.validations["SZ"]["checkmsg"] == "M001"
    assert s.validations["SZ"]["checks"] == [{"type": "range", "min": 0, "max": 100}]


def test_range_validation_passes_and_fails():
    s = _range_panel()
    addr = s.field_addr("sz")
    assert s.first_validation_error({addr: "50"}) is None       # in range
    assert s.first_validation_error({addr: ""}) is None         # empty skipped
    msgid, subs = s.first_validation_error({addr: "999"})       # out of range
    assert msgid == "M001"
    assert subs == {"VALUE": "999", "MIN": 0, "MAX": 100}
    msgid, _ = s.first_validation_error({addr: "abc"})          # not numeric
    assert msgid == "M001"


def test_values_check():
    s = load_dtl(
        '<panel>'
        '<varclass name="YN" type="char">'
        '  <checkl checkmsg="M2"><checki type="values">YES NO</checki></checkl>'
        '</varclass>'
        '<varlist><vardcl name="flag" varclass="YN"/></varlist>'
        '<dtafld row="1" col="1" fldcol="10" datavar="flag" entwidth="3">F</dtafld>'
        '</panel>'
    )
    addr = s.field_addr("flag")
    assert s.first_validation_error({addr: "yes"}) is None       # case-insensitive
    assert s.first_validation_error({addr: "maybe"})[0] == "M2"


def test_checkl_outside_varclass_raises():
    with pytest.raises(DTLError):
        load_dtl('<panel><checkl><checki type="range">0 1</checki></checkl></panel>')


def test_checki_bad_type_raises():
    with pytest.raises(DTLError):
        load_dtl(
            '<panel><varclass name="C"><checkl>'
            '<checki type="bogus">x</checki></checkl></varclass></panel>'
        )


def test_logon_size_validation_and_byte_identity():
    s = load_panel("logon")
    assert "SIZE" in s.validations
    assert s.validations["SIZE"]["checks"][0]["max"] == 32768
    # The validation/check tags are metadata — logon still renders identically.
    assert s.render() == build_tso_logon().render()
    # And the referenced message formats with the range substitutions.
    cat = load_message_member("tsomsgs")
    assert cat.format("TSO001", VALUE="99999", MIN=0, MAX=32768) == \
        "TSO001 SIZE 99999 IS NOT IN THE RANGE 0 TO 32768"


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
