"""DTL parser tests.

The headline assertion: the panels expressed in DTL markup
(``panels/*.dtl``) parse to Screens that render *byte-for-byte identically* to
the hand-built Phase 1 equivalents in :mod:`screens`. That chains back to the
Phase 1 golden test (which ties those builders to the original server output),
so DTL → Screen → bytes is proven equal to the live wire format.
"""
import pytest

from dtl import load_dtl, load_panel, load_messages, load_message_member, DTLError
from screen import Screen, Text, Field, DisplayIntensity, Color, Highlight, SA
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


def test_non_cp037_text_renders_without_crashing():
    # #150: a character the code page can't encode must degrade to the substitute
    # (?), not raise UnicodeEncodeError and take down the whole render/session.
    s = load_dtl('<panel><info row="1" col="1">Cost 5€ (— x)</info></panel>')
    data = s.render()                                   # must not raise
    shown = data.decode("cp037", errors="replace")
    assert "Cost 5? (? x)" in shown                     # euro / em-dash -> ?


def test_non_cp037_field_default_renders_without_crashing():
    from screen import Field
    buf = bytearray()
    Field(0, 0, 5, default="a€b").render(buf)      # must not raise
    assert b"\x6f" in bytes(buf)                         # '?' (cp037 substitute)


def test_instruction_tags_render_like_info():
    # <pnlinst> (panel instruction) and <botinst> (bottom instruction) are the IBM
    # spec tags; both render as positioned protected text, like <topinst>/<info>.
    s = load_dtl(
        '<panel>'
        '<topinst row="2" col="1">Enter parameters:</topinst>'
        '<pnlinst row="16" col="1" intensity="high">Press ENTER</pnlinst>'
        '<botinst row="23" col="1">PF3=Exit</botinst>'
        '</panel>'
    )
    assert s.items[0] == Text(2, 1, "Enter parameters:", DisplayIntensity.NORMAL)
    assert s.items[1] == Text(16, 1, "Press ENTER", DisplayIntensity.HIGH)
    assert s.items[2] == Text(23, 1, "PF3=Exit", DisplayIntensity.NORMAL)


def test_pnlinst_was_previously_dropped():
    # Regression for the dispatch bug: the parser routed on a nonexistent "paninst"
    # tag, so the real IBM <pnlinst> silently rendered nothing.
    s = load_dtl('<panel><pnlinst row="2" col="1">Hi</pnlinst></panel>')
    assert s.items == [Text(2, 1, "Hi", DisplayIntensity.NORMAL)]


def test_instruction_tags_flow_in_area():
    s = load_dtl(
        '<panel><area row="3" col="1">'
        '<topinst>line one</topinst><pnlinst>line two</pnlinst>'
        '</area></panel>'
    )
    assert s.items[0] == Text(3, 1, "line one", DisplayIntensity.NORMAL)
    assert s.items[1] == Text(4, 1, "line two", DisplayIntensity.NORMAL)


def test_logon_instruction_tags_byte_identical():
    # The logon panel uses <topinst>/<pnlinst> for some lines; bytes unchanged.
    assert load_panel("logon").render() == build_tso_logon().render()


# ── inline <hp> (highlighted phrase) ─────────────────────────────────────────

def _hp_line():
    return load_dtl(
        '<panel><info row="1" col="1">'
        'Enter <hp color="turq">X</hp> or <hp color="turq">PF3</hp> to exit'
        '</info></panel>'
    )


def test_hp_produces_one_rich_field():
    # An inline <hp> does not split the line into separate fields: the whole line
    # is a single Text.rich whose text is the concatenation, with the phrase(s)
    # captured as coloured runs.
    s = _hp_line()
    assert len(s.items) == 1
    it = s.items[0]
    assert it.text == "Enter X or PF3 to exit"
    assert it.runs == [
        ("Enter ", None, None),
        ("X", Color.TURQUOISE, None),
        (" or ", None, None),
        ("PF3", Color.TURQUOISE, None),
        (" to exit", None, None),
    ]


def test_hp_mono_is_byte_identical_to_plain_text():
    # Mono renders the concatenation exactly like a plain Text — so <hp> is safe
    # to add to a bundled panel without changing the mono data stream.
    it = _hp_line().items[0]
    rich = bytearray(); it.render(rich, color=False)
    plain = bytearray(); Text(1, 1, it.text).render(plain, color=False)
    assert bytes(rich) == bytes(plain)
    assert SA not in bytes(rich)                      # no Set Attribute on mono


def test_hp_colour_emits_set_attribute():
    # On a colour terminal the phrase is emphasised in place via SA runs (#110).
    buf = bytearray(); _hp_line().items[0].render(buf, color=True)
    assert SA in bytes(buf)


def test_hp_highlight_via_type_attribute():
    # <hp type=...> maps to a highlight (DTL spells hp emphasis as TYPE).
    s = load_dtl(
        '<panel><info row="2" col="1">see <hp type="uscore">HERE</hp> now</info></panel>'
    )
    assert s.items[0].runs == [
        ("see ", None, None),
        ("HERE", None, Highlight.UNDERSCORE),
        (" now", None, None),
    ]


def test_hp_surrounding_text_keeps_the_element_role():
    # The field's role colour still applies to the non-<hp> text: an <info> line is
    # role "text" (green), so only the phrase overrides to its own colour.
    it = _hp_line().items[0]
    assert it.role == "text"          # base role preserved
    assert it.color is None           # field base uses the role colour, not turq


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


def test_choice_records_selection_rows_for_point_and_shoot():
    # Each choice also records the row it renders on, so the cursor can select
    # it (point-and-shoot). selection_at(cursor) resolves a cursor address.
    s = load_dtl(
        '<panel><selfld row="4" numcol="1" namecol="4" desccol="21">'
        '<choice num="0" name="A">  desc-a</choice>'
        '<choice num="3" name="B">  desc-b</choice>'
        '</selfld></panel>'
    )
    assert s.selection_rows == {4: "0", 5: "3"}
    assert s.selection_at(4 * 80 + 10) == "0"   # cursor anywhere on row 4 -> "0"
    assert s.selection_at(5 * 80 + 0) == "3"
    assert s.selection_at(9 * 80 + 1) is None    # not on a choice row
    assert s.selection_at(None) is None


def test_ispf_menu_selection_rows_map_options():
    s = load_panel("ispf", ZUSER="IBMUSER ", ZTIME="13:45")
    # cursor on the Utilities line selects option 3; the Exit line selects X
    assert s.selection_at(7 * 80 + 5) == "3"
    assert s.selection_at(18 * 80 + 5) == "X"


def test_choice_match_defaults_to_num_and_records_selections():
    s = load_dtl(
        '<panel><selfld row="4" numcol="1" namecol="4" desccol="21">'
        '<choice num="0" name="Settings">  desc</choice>'
        '<choice num="X" name="Exit">  bye</choice>'
        '</selfld></panel>'
    )
    assert s.selections == {"0": "Settings", "X": "Exit"}


def test_choice_explicit_match_overrides_num():
    s = load_dtl(
        '<panel><selfld row="4" numcol="1" namecol="4" desccol="21">'
        '<choice num="1" name="View" match="V">  desc</choice>'
        '</selfld></panel>'
    )
    assert s.selections == {"V": "View"}        # MATCH wins over num


def test_choice_checkvar_lands_cursor_on_the_current_choice():
    # <choice checkvar=var match=val>: when the variable equals a choice's MATCH,
    # that choice is current — the cursor is placed on it.
    s = load_dtl(
        '<panel><selfld row="4" numcol="1" namecol="4" desccol="21">'
        '<choice num="1" name="New" checkvar="card" match="NEW">create'
        '<choice num="2" name="Old" checkvar="card" match="OLD">existing'
        '</selfld></panel>',
        CARD="OLD",
    )
    assert s.cursor_at == (5, 4)                 # second choice's row, namecol
    assert s.selections == {"NEW": "New", "OLD": "Old"}


def test_choice_unavail_is_dimmed_and_unselectable():
    # <choice unavail>: shown but not selectable (no routing / point-and-shoot),
    # and coloured with the CUA "unavailable" role.
    from screen import Color, _role_colour
    s = load_dtl(
        '<panel><selfld row="4" numcol="1" namecol="4" desccol="21">'
        '<choice num="1" name="Ok" match="A">available'
        '<choice num="2" name="No" match="B" unavail>disabled'
        '</selfld></panel>'
    )
    assert "A" in s.selections and "B" not in s.selections     # unavailable can't be picked
    assert 5 not in s.selection_rows                            # …nor point-and-shot
    dimmed = [it for it in s.items if getattr(it, "role", None) == "unavail"]
    assert len(dimmed) == 3                                     # num/name/desc of the row
    assert all(_role_colour(it.color, it.role) is Color.BLUE for it in dimmed)


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


# ── SGML general entities (<!ENTITY name "text">) ────────────────────────────

def test_internal_entity_reference_resolved():
    # <!ENTITY name "value"> declared in the doctype internal subset; &name;
    # references in the body are replaced with the value.
    s = load_dtl(
        '<!doctype dm system [\n'
        '<!entity guar "money-back guarantee">\n'
        ']>\n'
        '<panel><info row="1" col="1">It has our &guar;.</info></panel>'
    )
    assert s.items[0].text == "It has our money-back guarantee."


def test_external_system_entity_reference_left_intact():
    # A SYSTEM entity references a file we don't have; leave the reference as-is
    # rather than dropping or guessing it.
    s = load_dtl(
        '<!doctype dm system [\n'
        '<!entity widgets system>\n'
        ']>\n'
        '<panel><info row="1" col="1">See &widgets; now.</info></panel>'
    )
    assert s.items[0].text == "See &widgets; now."


def test_entity_does_not_disturb_dialog_vars():
    # An entity declaration in the same source must not break &NAME dialog
    # variables (which use a '.' terminator, not ';') or && escapes.
    s = load_dtl(
        '<!entity guar "G">\n'
        '<panel><info row="1" col="1">&guar; &who.X &&</info></panel>',
        WHO="BOB",
    )
    assert s.items[0].text == "G BOBX &"


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
        {"label": "Menu", "row": 0, "col": 1, "mnemonic": None,
         "pdc": [{"label": "Exit", "action": "exit", "mnemonic": None}]},
        {"label": "Help", "row": 0, "col": 8, "mnemonic": None,
         "pdc": [{"label": "About", "action": "passthru", "mnemonic": None}]},
    ]


def test_action_bar_implicit_pdc_and_abc_end_tags():
    # DTL omits most end tags: a new <pdc>/<abc> (or </ab>) closes the previous.
    # The <action run=...> child gives each pull-down its command, and <M> (the
    # mnemonic marker) is transparent to the label text.
    s = load_dtl(
        '<panel><ab row="0" col="1">'
        '<abc>File'
        '<pdc><M>Add<action run=add>'
        '<pdc><M>Delete<action run=delete>'
        '<abc>View'
        '<pdc checkvar=sorttype match=N><M>Name<action run=name>'
        '</ab></panel>'
    )
    assert s.action_bar == [
        {"label": "File", "row": 0, "col": 1, "mnemonic": None,
         "pdc": [{"label": "Add", "action": "add", "mnemonic": 0},
                 {"label": "Delete", "action": "delete", "mnemonic": 0}]},
        {"label": "View", "row": 0, "col": 8, "mnemonic": None,
         "pdc": [{"label": "Name", "action": "name", "mnemonic": 0}]},
    ]


def test_action_bar_mnemonic_is_recorded_and_underlined():
    # <M> marks the shortcut letter; it's recorded by offset and rendered with an
    # underscore highlight (mono is byte-identical to a plain high-intensity label).
    s = load_dtl(
        '<panel><ab row="0" col="1">'
        '<abc><M>File<pdc action="x">Open</pdc>'
        '<abc>E<M>xit<pdc action="alias exit">Leave</pdc>'
        '</ab></panel>'
    )
    assert [c["mnemonic"] for c in s.action_bar] == [0, 1]   # File->F, Exit->x
    file_label = s.items[0]
    assert file_label.text == "File"                          # label text unchanged
    mono = bytearray(); file_label.render(mono, color=False)
    plain = bytearray(); Text(0, 1, "File", DisplayIntensity.HIGH).render(plain)
    assert bytes(mono) == bytes(plain)                        # mono byte-identical
    col = bytearray(); file_label.render(col, color=True)
    assert SA in bytes(col)                                   # colour: mnemonic SA


def test_settings_action_bar_underlines_its_mnemonics():
    s = load_panel("settings")
    assert [c["mnemonic"] for c in s.action_bar] == [0, 0, 0, 0]   # first letters
    assert SA in s.render(color=True) and SA not in s.render(color=False)


def test_pulldown_item_underlines_its_mnemonic():
    # A pull-down item with a mnemonic renders "| N. label |" with the mnemonic
    # letter underlined; mono is byte-identical to the plain framed line.
    from server import _pdc_item_text
    item = {"label": "Delete", "action": "delete", "mnemonic": 2}   # 'l' in Delete
    rich = _pdc_item_text(3, 5, 1, item, inner=11)                   # len("1. Delete")+2
    assert rich.text == "| 1. Delete |"
    # "| " (2) + "1. " (3) + offset 2 -> the 'l'
    assert rich.text[2 + 3 + 2] == "l"
    mono = bytearray(); rich.render(mono, color=False)
    plain = bytearray(); Text(3, 5, "| 1. Delete |", DisplayIntensity.HIGH).render(plain)
    assert bytes(mono) == bytes(plain)                             # mono unchanged
    col = bytearray(); rich.render(col, color=True)
    assert SA in bytes(col)                                        # colour: underline

    plain_item = {"label": "Open", "action": "x", "mnemonic": None}
    assert _pdc_item_text(3, 5, 1, plain_item, 8).runs is None    # no mnemonic -> plain


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


def test_cursor_at_emits_ic_order():
    from server import SBA, IC, IAC, EOR, encode_pack_addr
    s = Screen(items=[Text(0, 0, "x")])
    base = s.render()
    s.cursor_at = (0, 5)
    out = s.render()
    assert out != base                       # adds the cursor placement
    assert out.endswith(
        bytes([SBA]) + encode_pack_addr(0, 5) + bytes([IC, IAC, EOR])
    )


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


@pytest.mark.parametrize("name,n_choices", [
    ("foreground", 6), ("batch", 5), ("ibmprod", 5),
    ("sclm", 5), ("zsystem", 5), ("zuser", 4),
])
def test_remaining_option_submenus_load(name, n_choices):
    # Options 4/5/9/10/12/13 open nested selection sub-menus: each has its own
    # ZCMD Option line, the expected number of choices, and PF3=EXIT.
    s = load_panel(name, SELMSG="")
    assert s.field_addr("ZCMD") == 2 * 80 + 14
    assert len(s.selections) == n_choices
    assert s.command_for("PF3") == "EXIT"


def test_edit_entry_and_workplace_panels_load():
    edit = load_panel("editentry", VIEWMSG="MEMBER FOO NOT FOUND")
    assert edit.field_addr("member") is not None
    assert edit.command_for("PF3") == "EXIT"
    assert "MEMBER FOO NOT FOUND" in [t.text for t in edit.items if isinstance(t, Text)]

    wp = load_panel("workplace")
    assert wp.command_for("PF3") == "EXIT"
    assert any("Workplace" in t.text for t in wp.items if isinstance(t, Text))


def test_view_entry_and_browse_panels_load():
    # Option 1 (View): the entry panel has a `member` input field and surfaces
    # &VIEWMSG; the browse panel frames &BRTITLE/&BRFOOT with PF3=EXIT.
    entry = load_panel("viewentry", VIEWMSG="MEMBER FOO NOT FOUND")
    assert entry.field_addr("member") is not None
    assert entry.command_for("PF3") == "EXIT"
    assert "MEMBER FOO NOT FOUND" in [t.text for t in entry.items if isinstance(t, Text)]

    browse = load_panel("browse", BRTITLE="BROWSE  ISPF.ISPPLIB(LOGON)", BRFOOT="PF3=Exit")
    titles = [t.text for t in browse.items if isinstance(t, Text)]
    assert "BROWSE  ISPF.ISPPLIB(LOGON)" in titles
    assert browse.command_for("PF3") == "EXIT"


def test_member_list_panel_renders_supplied_members():
    # Utilities -> Library (3.1): the memlist <lstfld> lays out the supplied
    # panel-library members as protected name/type/description rows.
    rows = [{"mname": "LOGON", "mtype": "Panel(DTL)", "mdesc": "TSO/E logon panel"},
            {"mname": "ISPF",  "mtype": "Panel(DTL)", "mdesc": "Primary Option Menu"}]
    s = load_panel("memlist", rows=rows)
    texts = [t.text for t in s.items if isinstance(t, Text)]
    assert "Name" in texts and "Type" in texts and "Description" in texts
    assert "LOGON" in texts and "TSO/E logon panel" in texts
    assert "ISPF" in texts and "Primary Option Menu" in texts
    # The paging footer is placed by the server on the last row (see the
    # alternate-screen tests), not by the panel itself.
    assert not [f for f in s.items if isinstance(f, Field)]   # display-only
    assert s.command_for("PF3") == "EXIT"


def test_utility_submenu_lists_choices_and_message():
    # Option 3 (Utilities): a nested selection menu — its own Option ===> line
    # and a <selfld> of choices, with the server's message surfaced via &SELMSG.
    s = load_panel("utility", SELMSG="OPTION 4 (Dslist) NOT YET IMPLEMENTED")
    assert s.field_addr("ZCMD") == 2 * 80 + 14   # its own Option ===> line
    assert s.command_for("PF3") == "EXIT"
    for opt in ["1", "2", "3", "4", "5", "8"]:
        assert opt in s.selections
    assert s.selections["4"] == "Dslist"
    texts = [t.text for t in s.items if isinstance(t, Text)]
    assert "OPTION 4 (Dslist) NOT YET IMPLEMENTED" in texts


def test_command_shell_panel_shows_response_message():
    # Option 6 (Command): the command line is a ZCMD <cmdarea>, and the server's
    # response is substituted into the panel via &CMDMSG.
    s = load_panel("command", CMDMSG="IKJ56500I COMMAND FOO NOT FOUND")
    assert s.field_addr("ZCMD") == 4 * 80 + 14   # Command ===> input line
    assert s.command_for("PF3") == "EXIT"
    texts = [t.text for t in s.items if isinstance(t, Text)]
    assert "IKJ56500I COMMAND FOO NOT FOUND" in texts


def test_dialog_test_panel_renders_variable_rows():
    # Option 7 (Dialog Test): the <lstfld> table lays out the supplied session
    # variables as protected name/value rows, and PF3 returns.
    rows = [{"vname": "ZUSER", "vvalue": "IBMUSER"},
            {"vname": "ZTIME", "vvalue": "09:41"}]
    s = load_panel("dlgtest", rows=rows)
    texts = {(t.row, t.col): t.text for t in s.items if isinstance(t, Text)}
    # column headings, then one row per variable (display columns -> Text)
    assert "Variable" in texts.values() and "Value" in texts.values()
    assert "ZUSER" in texts.values() and "IBMUSER" in texts.values()
    assert "ZTIME" in texts.values() and "09:41" in texts.values()
    # the values are display-only: no input Field is emitted for the table
    assert not [f for f in s.items if isinstance(f, Field)]
    assert s.command_for("PF3") == "EXIT"


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


def test_msg_alarm_defaults_from_msgtype_and_overrides():
    cat = load_messages(
        '<msgmbr name="M">'
        '<msg msgid="W" msgtype="WARNING">warn</msg>'
        '<msg msgid="A" msgtype="ACTION">act</msg>'
        '<msg msgid="I" msgtype="INFO">info</msg>'
        '<msg msgid="Q" msgtype="CRITICAL" alarm="no">quiet</msg>'
        '<msg msgid="B">bare</msg>'
        '</msgmbr>'
    )
    assert cat.alarm("W") and cat.alarm("A")            # warning/action alarm
    assert not cat.alarm("I")                            # info does not
    assert not cat.alarm("Q")                            # explicit alarm=no wins
    assert not cat.alarm("B")                            # no type → no alarm
    assert not cat.alarm("NOSUCH")                       # unknown id


def test_msg_short_message_and_member_width():
    cat = load_messages(
        '<msgmbr name="M" width="70">'
        '<msg msgid="L" smsg="Short &N">Long form for &N</msg>'
        '<msg msgid="P">Plain</msg>'
        '</msgmbr>'
    )
    assert cat.width == 70
    assert cat.short("L", N="X") == "Short X"            # smsg used, substituted
    assert cat.short("P") == "P Plain"                   # falls back to long form


def test_shipped_tso_error_messages_sound_the_alarm():
    cat = load_message_member("tsomsgs")
    for mid in ("IKJ56425I", "IKJ56700I", "TSO001"):
        assert cat.alarm(mid), mid                       # logon errors beep


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
    for name in ("tsohelp", "ispfhelp", "sizehelp"):
        s = load_panel(name)
        assert s.command_for("PF3") == "EXIT"   # PF3 returns from help


def test_field_help_is_recorded_and_resolved_by_cursor():
    # <dtafld help=panel> records a field-level help panel; Screen.help_for maps a
    # cursor within the field's span to it, and to None elsewhere.
    s = load_dtl(
        '<panel><area row="3" col="1">'
        '<dtafld datavar="size" fldcol="16" entwidth="5" help="sizehelp">Size</dtafld>'
        '<dtafld datavar="name" fldcol="16" entwidth="8">Name</dtafld>'
        '</area></panel>'
    )
    size = s.field_addr("size")
    assert s.help_for(size) == "sizehelp"          # cursor at the field start
    assert s.help_for(size + 4) == "sizehelp"      # within the field span
    assert s.help_for(size + 40) is None           # outside it
    assert s.help_for(s.field_addr("name")) is None  # a field with no help
    assert s.help_for(None) is None


def test_field_help_non_panel_values_are_not_field_help():
    # HELP=NO/YES, a *message id, or a %varname don't name a help panel.
    for val in ("no", "yes", "*ISRZ001", "%dynhelp"):
        s = load_dtl(f'<panel><area row="1" col="1">'
                     f'<dtafld datavar="f" fldcol="10" entwidth="4" help="{val}">F</dtafld>'
                     f'</area></panel>')
        assert s.help_for(s.field_addr("f")) is None


def test_logon_size_field_has_context_help_bytes_unchanged():
    lg = load_panel("logon")
    assert lg.help_for(lg.field_addr("size")) == "sizehelp"   # field help
    assert lg.help_for(lg.field_addr("userid")) is None       # falls back to panel help
    assert lg.render() == build_tso_logon().render()          # help= is metadata


def test_help_attribute_does_not_change_rendered_bytes():
    # Adding help="..." to <panel> is metadata; the logon panel still matches.
    assert load_panel("logon").render() == build_tso_logon().render()


# ── implicit end tags + text tags (<p>/<li>/<dt>/…) ──────────────────────────

def test_paragraphs_flow_without_end_tags():
    # DTL omits end tags; each <p> closes the previous one and flows on its line.
    s = load_dtl('<panel name="p"><p>First para.<p>Second para.</panel>')
    assert s.items[0] == Text(0, 1, "First para.", DisplayIntensity.NORMAL)
    assert s.items[1] == Text(1, 1, "Second para.", DisplayIntensity.NORMAL)


def test_multiline_paragraph_collapses_to_one_line():
    s = load_dtl('<panel name="p"><info><p>line one\n   line two</info></panel>')
    assert s.items == [Text(0, 1, "line one line two", DisplayIntensity.NORMAL)]


def test_list_items_flow_with_bullets():
    s = load_dtl('<panel name="p"><ul><li>apple<li>pear<li>plum</ul></panel>')
    # Each <li> renders a bullet ("o" at level 1) + the item text, flowed.
    assert s.items == [
        Text(0, 1, "o", DisplayIntensity.NORMAL), Text(0, 5, "apple", DisplayIntensity.NORMAL),
        Text(1, 1, "o", DisplayIntensity.NORMAL), Text(1, 5, "pear", DisplayIntensity.NORMAL),
        Text(2, 1, "o", DisplayIntensity.NORMAL), Text(2, 5, "plum", DisplayIntensity.NORMAL),
    ]


def test_ordered_list_numbers_items():
    s = load_dtl('<panel name="p"><ol><li>one<li>two</ol></panel>')
    assert s.items == [
        Text(0, 1, "1.", DisplayIntensity.NORMAL), Text(0, 5, "one", DisplayIntensity.NORMAL),
        Text(1, 1, "2.", DisplayIntensity.NORMAL), Text(1, 5, "two", DisplayIntensity.NORMAL),
    ]


def test_definition_list_pairs_term_and_description_on_one_line():
    # <dl> default break=none: each <dt> term and its <dd> description share a
    # line, the description in a column tsize (default 10) to the right.
    s = load_dtl(
        '<panel name="p"><dl><dt>AP<dd>Appliances<dt>AU<dd>Automotive</dl></panel>'
    )
    N = DisplayIntensity.NORMAL
    assert s.items == [
        Text(0, 1, "AP", N), Text(0, 11, "Appliances", N),
        Text(1, 1, "AU", N), Text(1, 11, "Automotive", N),
    ]


def test_definition_list_tsize_sets_description_column():
    s = load_dtl('<panel name="p"><dl tsize=4><dt>X<dd>ex</dl></panel>')
    N = DisplayIntensity.NORMAL
    assert s.items == [Text(0, 1, "X", N), Text(0, 5, "ex", N)]


def test_definition_list_break_all_puts_description_on_next_line():
    # break=all: the description always starts on the line after the term,
    # indented to the term column (tsize).
    s = load_dtl(
        '<panel name="p"><dl tsize=6 break=all><dt>Cash<dd>We accept it</dl></panel>'
    )
    N = DisplayIntensity.NORMAL
    assert s.items == [Text(0, 1, "Cash", N), Text(1, 7, "We accept it", N)]


def test_parameter_list_wraps_long_description_under_column():
    # <parml>/<pt>/<pd> behaves like <dl>; a long description wraps with a
    # hanging indent aligned under the description column.
    s = load_dtl(
        '<panel name="p" width=30><parml><pt>78<pd>seventh and eighth digits</parml></panel>'
    )
    N = DisplayIntensity.NORMAL
    assert s.items == [
        Text(0, 1, "78", N),
        Text(0, 11, "seventh and eighth", N),
        Text(1, 11, "digits", N),
    ]


def test_lines_preserves_authored_line_breaks():
    # <lines> is preformatted: authored line breaks (and a blank interior line)
    # are preserved, unlike <p> which collapses whitespace and word-wraps.
    s = load_dtl(
        '<panel name="p"><info><lines>\n'
        '  Roses are red\n'
        '  Violets are blue\n'
        '\n'
        '  Sugar is sweet\n'
        '</lines></info></panel>'
    )
    N = DisplayIntensity.NORMAL
    assert s.items == [
        Text(0, 1, "Roses are red", N),
        Text(1, 1, "Violets are blue", N),
        Text(2, 1, "", N),
        Text(3, 1, "Sugar is sweet", N),
    ]


def test_lines_preserves_internal_spacing_and_truncates_to_width():
    # Common source indentation is stripped, but spacing *within* a line is
    # kept (so columns line up); lines wider than the panel are truncated.
    s = load_dtl(
        '<panel name="p" width=12><info><lines>\n'
        '    col1  col2\n'
        '    a     b\n'
        '</lines></info></panel>'
    )
    N = DisplayIntensity.NORMAL
    assert s.items == [
        Text(0, 1, "col1  col2", N),   # width 12 -> 10 usable cols, fits exactly
        Text(1, 1, "a     b", N),
    ]


# ── help-panel admonitions (<note>/<nt>/<warning>/…, <notel>) ────────────────

def test_note_renders_as_a_labelled_callout():
    # <note> was previously dropped entirely; it now flows as "Note: <body>".
    s = load_dtl(
        '<panel width="50"><area><info>'
        '<p>Pick a widget.<note>If it is out of stock, use the Back Order panel.'
        '</info></area></panel>'
    )
    texts = [t.text for t in s.items if isinstance(t, Text)]
    assert any(t.startswith("Note: If it is out of stock") for t in texts)


@pytest.mark.parametrize("tag,label", [
    ("warning", "Warning:"), ("caution", "Caution:"),
    ("attention", "Attention:"), ("nt", "Note:"),
])
def test_admonition_labels(tag, label):
    s = load_dtl(f'<panel width="50"><area><info><{tag}>Mind the gap.'
                 f'</info></area></panel>')
    texts = [t.text for t in s.items if isinstance(t, Text)]
    assert any(t.startswith(label) and "Mind the gap." in t for t in texts)


def test_inline_note_keeps_following_paragraph():
    # <nt>text<p>more</nt>: the note flows labelled, the nested <p> flows after it.
    s = load_dtl(
        '<panel width="50"><area><info>'
        '<nt>Out of stock.<p>Arrives in three days.</nt>'
        '<p>Order below.</info></area></panel>'
    )
    texts = [t.text for t in s.items if isinstance(t, Text)]
    assert any(t.startswith("Note: Out of stock.") for t in texts)
    assert any("three days" in t for t in texts)
    assert any("Order below." in t for t in texts)


def test_note_list_renders_heading_and_bulleted_items():
    s = load_dtl(
        '<panel width="50"><area><info><notel>'
        '<li>First note.<li>Second note.</notel></info></area></panel>'
    )
    texts = [t.text for t in s.items if isinstance(t, Text)]
    assert "Notes:" in texts
    assert any("First note." in t for t in texts)
    assert any("Second note." in t for t in texts)


# ── list/table fields (<lstfld>/<lstcol>/<lstgrp>) ───────────────────────────

def test_list_field_columns_laid_out_with_headings():
    # Columns flow left to right by colwidth plus a one-column gap; their
    # headings render on one row, with an (empty) input model row beneath.
    s = load_dtl(
        '<panel name="p"><area>'
        '<lstfld><lstcol datavar=a colwidth=5>Mon<lstcol datavar=b colwidth=5>Tue</lstfld>'
        '</area></panel>'
    )
    H = DisplayIntensity.HIGH
    assert s.items == [
        Text(0, 1, "Mon", H), Text(0, 7, "Tue", H),
        Field(row=1, col=1, length=5, name="a", terminator=False),
        Field(row=1, col=7, length=5, name="b", terminator=True),
    ]


def test_list_field_group_heading_centered_over_columns():
    # A <lstgrp headline=yes> heading is centered over its columns' span, on the
    # row above the column headings.
    s = load_dtl(
        '<panel name="p"><area>'
        '<lstfld><lstgrp headline=yes>Wk'
        '<lstcol datavar=a colwidth=5>Mon<lstcol datavar=b colwidth=5>Tue'
        '</lstgrp></lstfld>'
        '</area></panel>'
    )
    H = DisplayIntensity.HIGH
    assert s.items == [
        Text(0, 5, "Wk", H),                       # centered over cols 1..12
        Text(1, 1, "Mon", H), Text(1, 7, "Tue", H),
        Field(row=2, col=1, length=5, name="a", terminator=False),
        Field(row=2, col=7, length=5, name="b", terminator=True),
    ]


def test_list_field_display_column_and_data_rows():
    # usage=out renders protected text; input columns are pre-filled from the
    # supplied rows; one model entry per row.
    s = load_dtl(
        '<panel name="p"><area>'
        '<lstfld><lstcol datavar=t colwidth=4 usage=out>Time'
        '<lstcol datavar=who colwidth=6>Who</lstfld>'
        '</area></panel>',
        rows=[{"t": "8:00", "who": "Acme"}, {"t": "9:00", "who": "Globex"}],
    )
    H, N = DisplayIntensity.HIGH, DisplayIntensity.NORMAL
    assert s.items == [
        Text(0, 1, "Time", H), Text(0, 6, "Who", H),
        Text(1, 1, "8:00", N),  Field(row=1, col=6, length=6, name="who", default="Acme", terminator=True),
        Text(2, 1, "9:00", N),  Field(row=2, col=6, length=6, name="who", default="Globex", terminator=True),
    ]


def test_list_field_line_attribute_stacks_columns():
    # line=2 puts a column on the second row of each model entry; the entry is
    # two rows tall, and each line's rightmost input field gets a terminator.
    s = load_dtl(
        '<panel name="p"><area>'
        '<lstfld><lstcol datavar=a colwidth=5 line=1>A'
        '<lstcol datavar=b colwidth=5 line=2>B</lstfld>'
        '</area></panel>'
    )
    H = DisplayIntensity.HIGH
    assert s.items == [
        Text(0, 1, "A", H), Text(0, 7, "B", H),
        Field(row=1, col=1, length=5, name="a", terminator=True),
        Field(row=2, col=7, length=5, name="b", terminator=True),
    ]


def test_list_column_outside_list_field_raises():
    with pytest.raises(DTLError):
        load_dtl('<panel name="p"><lstcol colwidth=5>X</panel>')


def test_panel_title_text_centered():
    s = load_dtl('<panel name="p" width="20">My Title<info>body</info></panel>')
    assert s.title == "My Title"
    assert s.items[0] == Text(0, (20 - len("My Title")) // 2, "My Title", DisplayIntensity.NORMAL)
    assert s.items[1] == Text(1, 1, "body", DisplayIntensity.NORMAL)   # flows below title


def test_nested_unordered_lists_matches_guide_figure():
    # IBM DTL Guide "Nested Unordered Lists" figure: a centered title, then o/-/--
    # bullets by depth with increasing indentation. (Verbatim guide source.)
    s = load_dtl(
        '<!doctype dm system>\n'
        '<panel name=ulists width=42>Nested Unordered Lists\n'
        '<area>\n'
        '<info width=40><ul><li>First level, first item<li>First level, second item'
        '<ul><li>Second level, first item<li>Second level, second item'
        '<ul><li>Third level, only item</ul></ul>'
        '<li>Back to the first level</ul></info>\n'
        '</area>\n'
        '</panel>'
    )
    N = DisplayIntensity.NORMAL
    assert s.items == [
        Text(0, 10, "Nested Unordered Lists", N),
        Text(1, 1, "o", N),  Text(1, 5, "First level, first item", N),
        Text(2, 1, "o", N),  Text(2, 5, "First level, second item", N),
        Text(3, 5, "-", N),  Text(3, 9, "Second level, first item", N),
        Text(4, 5, "-", N),  Text(4, 9, "Second level, second item", N),
        Text(5, 9, "--", N), Text(5, 13, "Third level, only item", N),
        Text(6, 1, "o", N),  Text(6, 5, "Back to the first level", N),
    ]


def test_widget_help_matches_guide_figure():
    # IBM DTL Guide "Widget Assembly Help" figure: a <help> panel with a
    # centered title, a paragraph, an ordered list (1./2./3.) with a nested
    # lettered list (a./b.), word-wrapped to width 60 with a hanging indent,
    # and a trailing paragraph aligned under the list. (Verbatim guide source.)
    s = load_dtl(
        '<!DOCTYPE DM SYSTEM>\n'
        '<HELP NAME=ol DEPTH=22 WIDTH=60>Widget Assembly Help\n'
        '<AREA>\n<INFO>\n'
        '<P>To assemble your new Widget, you should:<OL><LI>Attach the gizmo '
        'flexure component to the\nmain steering mechanism of the doohickey.'
        '<OL COMPACT><LI>If slot A fits snugly on retaining\npin B, proceed to '
        'step 2.\n<LI>If slot A does not fit snugly on\nretaining pin B, throw '
        'the Widget away\nand buy a new one.</OL><LI>Use a screwdriver to turn '
        'the power drive unit on.\n<LI>Stand back and watch the fun!\n'
        '<P>Wake up the kids and call the neighbors, they won\'t\n'
        'want to miss it!</OL></INFO>\n</AREA>\n</HELP>'
    )
    N = DisplayIntensity.NORMAL
    assert s.title == "Widget Assembly Help"
    assert s.items == [
        Text(0, 20, "Widget Assembly Help", N),
        Text(1, 1, "To assemble your new Widget, you should:", N),
        Text(2, 1, "1.", N),
        Text(2, 5, "Attach the gizmo flexure component to the main", N),
        Text(3, 5, "steering mechanism of the doohickey.", N),
        Text(4, 5, "a.", N),
        Text(4, 9, "If slot A fits snugly on retaining pin B, proceed", N),
        Text(5, 9, "to step 2.", N),
        Text(6, 5, "b.", N),
        Text(6, 9, "If slot A does not fit snugly on retaining pin B,", N),
        Text(7, 9, "throw the Widget away and buy a new one.", N),
        Text(8, 1, "2.", N),
        Text(8, 5, "Use a screwdriver to turn the power drive unit on.", N),
        Text(9, 1, "3.", N),
        Text(9, 5, "Stand back and watch the fun!", N),
        Text(10, 5, "Wake up the kids and call the neighbors, they won't", N),
        Text(11, 5, "want to miss it!", N),
    ]


def test_implicit_end_does_not_break_explicitly_closed_panels():
    # Bundled-panel style (explicit </info>) is unaffected by implicit-end logic.
    assert load_panel("logon").render() == build_tso_logon().render()


# ── auto-flow: a panel is an implicit flow box (no row/col needed) ───────────

def test_panel_autoflows_unpositioned_elements():
    # With no row/col anywhere, content flows down from the top of the panel.
    s = load_dtl(
        '<panel name="p">'
        '<info>first line</info>'
        '<info>second line</info>'
        '<dtafld datavar="u" entwidth="8">Userid</dtafld>'
        '</panel>'
    )
    assert s.items[0] == Text(0, 1, "first line", DisplayIntensity.NORMAL)
    assert s.items[1] == Text(1, 1, "second line", DisplayIntensity.NORMAL)
    assert s.items[2] == Text(2, 1, "Userid", DisplayIntensity.NORMAL)   # prompt
    fld = s.items[3]
    assert (fld.row, fld.col, fld.name) == (2, 1 + len("Userid") + 1, "u")  # entry after prompt


def test_unpositioned_area_is_transparent_to_flow():
    # An <area> with no row/col continues the panel flow; the panel resumes after.
    s = load_dtl(
        '<panel name="p">'
        '<info>before</info>'
        '<area><info>inside-a</info><info>inside-b</info></area>'
        '<info>after</info>'
        '</panel>'
    )
    rows = [(i.row, i.text) for i in s.items]
    assert rows == [(0, "before"), (1, "inside-a"), (2, "inside-b"), (3, "after")]


def test_explicit_position_still_wins_under_autoflow():
    # Explicit row/col overrides the flow and the flow resumes after it.
    s = load_dtl(
        '<panel name="p">'
        '<info>flowed0</info>'
        '<info row="10" col="5">explicit</info>'
        '<info>flowed11</info>'
        '</panel>'
    )
    assert s.items[0] == Text(0, 1, "flowed0", DisplayIntensity.NORMAL)
    assert s.items[1] == Text(10, 5, "explicit", DisplayIntensity.NORMAL)
    assert s.items[2] == Text(11, 1, "flowed11", DisplayIntensity.NORMAL)


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


def test_dtacol_aligns_entries_at_a_fixed_prompt_column():
    # <dtacol pmtwidth=20>: each field's entry starts at col + pmtwidth,
    # regardless of caption length (the DTL data-column layout).
    s = load_dtl(
        '<panel name="books1">Book Title Search'
        '<area><dtacol pmtwidth="20">'
        '<dtafld entwidth="40" datavar="author">Author</dtafld>'
        '<dtafld entwidth="10" datavar="catnum">Catalog number</dtafld>'
        '</dtacol></area></panel>'
    )
    fields = [i for i in s.items if isinstance(i, Field)]
    assert [(f.col, f.length, f.name) for f in fields] == [(21, 40, "author"),
                                                           (21, 10, "catnum")]


def test_dtacol_supplies_default_entry_width():
    s = load_dtl(
        '<panel><area><dtacol pmtwidth="12" entwidth="25">'
        '<dtafld datavar="name">Name</dtafld>'          # no entwidth → inherits 25
        '</dtacol></area></panel>'
    )
    field = next(i for i in s.items if isinstance(i, Field))
    assert field.length == 25 and field.col == 13


# ── <dtafld> USAGE / PMTLOC (#122) ───────────────────────────────────────────

def test_dtafld_usage_out_is_a_protected_display_field():
    # usage=out renders the variable's value as protected text — no input field.
    s = load_dtl(
        '<panel><area row="3" col="1">'
        '<dtafld datavar="curdate" usage="out" entwidth="8">Date</dtafld>'
        '</area></panel>',
        CURDATE="07/03/26",
    )
    assert not [i for i in s.items if isinstance(i, Field)]     # display-only
    texts = [t.text for t in s.items if isinstance(t, Text)]
    assert "Date" in texts                                      # the prompt
    assert any(t.strip() == "07/03/26" for t in texts)         # the value shown


def test_dtafld_usage_in_stays_an_input_field():
    s = load_dtl(
        '<panel><area row="3" col="1">'
        '<dtafld datavar="x" usage="in" entwidth="8">Name</dtafld>'
        '</area></panel>'
    )
    assert [i for i in s.items if isinstance(i, Field)]         # still editable


def test_dtafld_pmtloc_above_puts_prompt_on_the_line_above():
    s = load_dtl(
        '<panel><area row="5" col="1">'
        '<dtafld datavar="t" entwidth="20" pmtloc="above">Title</dtafld>'
        '<dtafld datavar="u" entwidth="8">Next</dtafld>'
        '</area></panel>'
    )
    prompt = next(t for t in s.items if isinstance(t, Text) and t.text == "Title")
    fld = next(f for f in s.items if isinstance(f, Field) and f.name == "t")
    assert prompt.row == 5 and fld.row == 6 and fld.col == 1    # prompt above, field below
    nxt = next(f for f in s.items if isinstance(f, Field) and f.name == "u")
    assert nxt.row == 7                                         # flow advanced past 2 rows


def test_divider_draws_a_rule_across_the_flow():
    s = load_dtl(
        '<panel><area row="4" col="1"><info>above</info><divider>'
        '<info>below</info></area></panel>'
    )
    texts = [i for i in s.items if isinstance(i, Text)]
    assert texts[0] == Text(4, 1, "above", DisplayIntensity.NORMAL)
    rule = texts[1]
    assert rule.row == 5 and rule.col == 1 and set(rule.text) == {"-"}
    assert texts[2] == Text(6, 1, "below", DisplayIntensity.NORMAL)  # flow resumed


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


def test_region_indent_shifts_content_right_and_nests():
    # <region indent=n> flows its content n columns right of the origin; nested
    # indents stack, and the parent flow resumes at its own column afterwards.
    s = load_dtl(
        '<panel><area col="1">'
        '<info>flush</info>'
        '<region indent="4"><info>indented</info>'
        '<region indent="3"><info>deeper</info></region></region>'
        '<info>after</info>'
        '</area></panel>'
    )
    N = DisplayIntensity.NORMAL
    assert s.items[0] == Text(0, 1, "flush", N)
    assert s.items[1] == Text(1, 5, "indented", N)     # col 1 + 4
    assert s.items[2] == Text(2, 8, "deeper", N)       # col 1 + 4 + 3
    assert s.items[3] == Text(3, 1, "after", N)        # parent flow resumes at col 1


def test_missing_row_outside_any_flow_context_raises():
    # With no <panel> (hence no implicit flow box) and no <area>, an <info>
    # without a row has nowhere to flow, so it still raises.
    with pytest.raises(DTLError):
        load_dtl('<info col="1">x</info>')


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
    # A still-required attribute (keyi's key) raises; note <info>/<dtafld> no
    # longer require row/col — they auto-flow inside a panel (see flow tests).
    with pytest.raises(DTLError):
        load_dtl('<panel><keyl><keyi cmd="HELP"/></keyl></panel>')


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
        '  <checkl msg="M001"><checki type="range">0 100</checki></checkl>'
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
        '  <checkl msg="M2"><checki type="values">YES NO</checki></checkl>'
        '</varclass>'
        '<varlist><vardcl name="flag" varclass="YN"/></varlist>'
        '<dtafld row="1" col="1" fldcol="10" datavar="flag" entwidth="3">F</dtafld>'
        '</panel>'
    )
    addr = s.field_addr("flag")
    assert s.first_validation_error({addr: "yes"}) is None       # case-insensitive
    assert s.first_validation_error({addr: "maybe"})[0] == "M2"


def _check_panel(checki):
    s = load_dtl(
        '<panel>'
        '<varclass name="C"><checkl msg="M">' + checki + '</checkl></varclass>'
        '<varlist><vardcl name="f" varclass="C"/></varlist>'
        '<dtafld row="1" col="1" fldcol="10" datavar="f" entwidth="20">F</dtafld>'
        '</panel>'
    )
    return s, s.field_addr("f")


def test_values_via_parm_attributes():
    # The guide's attribute-driven form: type=values parm1=EQ parm2='v1 v2'.
    s, addr = _check_panel('<checki type=values parm1=EQ parm2="SINGLE DOUBLE">')
    assert s.validations["F"]["checks"] == [
        {"type": "values", "values": ["SINGLE", "DOUBLE"], "negate": False}]
    assert s.first_validation_error({addr: "single"}) is None      # case-insensitive
    assert s.first_validation_error({addr: "triple"})[0] == "M"


def test_range_via_parm_attributes():
    # type=range parm1=low-bound parm2=high-bound (the guide's attribute form).
    s, addr = _check_panel("<checki type=range parm1=0 parm2=100>")
    assert s.validations["F"]["checks"] == [{"type": "range", "min": 0, "max": 100}]
    assert s.first_validation_error({addr: "50"}) is None
    assert s.first_validation_error({addr: "999"})[0] == "M"


def test_values_parm1_ne_excludes_the_set():
    # parm1=NE inverts the check: the value must NOT be one of the listed ones.
    s, addr = _check_panel("<checki type=values parm1=NE parm2='Y N'>")
    assert s.first_validation_error({addr: "Y"})[0] == "M"          # forbidden
    assert s.first_validation_error({addr: "MAYBE"}) is None        # allowed


def test_alpha_check_requires_letters():
    s, addr = _check_panel("<checki type=alpha>")
    assert s.first_validation_error({addr: "ABCdef"}) is None
    assert s.first_validation_error({addr: "AB12"})[0] == "M"       # digits rejected
    assert s.first_validation_error({addr: ""}) is None             # empty skipped


def test_name_check_requires_a_valid_symbol():
    s, addr = _check_panel("<checki type=name>")
    assert s.first_validation_error({addr: "MYVAR1"}) is None
    assert s.first_validation_error({addr: "@DD$"}) is None         # @ # $ allowed
    assert s.first_validation_error({addr: "1BAD"})[0] == "M"       # can't start w/ digit
    assert s.first_validation_error({addr: "TOOLONGXX"})[0] == "M"  # > 8 chars


def test_checkl_outside_varclass_raises():
    with pytest.raises(DTLError):
        load_dtl('<panel><checkl><checki type="range">0 1</checki></checkl></panel>')


# ── <varclass> TYPE forms + the IBM MSG attribute (#129) ─────────────────────

def _varclass_panel(vc):
    s = load_dtl(
        '<panel>' + vc +
        '<varlist><vardcl name="f" varclass="C"/></varlist>'
        '<area><dtafld datavar="f" row="1" col="1" entwidth="30">F</dtafld></area>'
        '</panel>'
    )
    return s, s.field_addr("f")


def test_varclass_char_length_is_enforced():
    # type="char N" caps the input length (previously the N was parsed and ignored).
    s, addr = _varclass_panel('<varclass name="C" type="char 8" msg="M1">')
    assert s.first_validation_error({addr: "SHORT"}) is None
    msgid, subs = s.first_validation_error({addr: "TOOLONGVALUE"})
    assert msgid == "M1" and subs == {"VALUE": "TOOLONGVALUE", "MAX": 8}


def test_varclass_numeric_precision_is_enforced():
    # type="numeric N" makes the field numeric and caps the digit count.
    s, addr = _varclass_panel('<varclass name="C" type="numeric 3" msg="M2">')
    fld = next(i for i in s.items if isinstance(i, Field))
    assert fld.numeric is True
    assert s.first_validation_error({addr: "12"}) is None
    assert s.first_validation_error({addr: "12345"})[0] == "M2"


def test_checkl_reads_ibm_msg_attribute():
    # IBM's attribute is MSG (we used to read a non-IBM "checkmsg").
    s, addr = _varclass_panel(
        '<varclass name="C"><checkl msg="TSO7">'
        '<checki type="range">0 9</checki></checkl></varclass>')
    assert s.validations["F"]["checkmsg"] == "TSO7"
    assert s.first_validation_error({addr: "99"})[0] == "TSO7"


def test_class_msg_is_the_fallback_for_type_checks():
    # A <varclass msg=> with no <checkl> still carries a message for its TYPE check;
    # a <checkl msg=> overrides it for the combined checks.
    s, addr = _varclass_panel('<varclass name="C" type="char 3" msg="CLS">')
    assert s.first_validation_error({addr: "TOOLONG"})[0] == "CLS"
    s2, addr2 = _varclass_panel(
        '<varclass name="C" type="char 4" msg="CLS">'
        '<checkl msg="CHK"><checki type="alpha"></checkl></varclass>')
    assert s2.validations["F"]["checkmsg"] == "CHK"                  # checkl MSG wins
    assert s2.first_validation_error({addr2: "ABCDE"})[0] == "CHK"   # TYPE maxlen(4)
    assert s2.first_validation_error({addr2: "AB12"})[0] == "CHK"    # checkl alpha


def test_checki_unsupported_type_is_still_lenient():
    # A type we don't enforce yet (e.g. picture) still loads without failing the
    # panel and adds no validation — leniency preserved for the unimplemented set.
    s, addr = _check_panel('<checki type="picture">AAA</checki>')
    assert s.validations.get("F", {}).get("checks", []) == []
    assert s.first_validation_error({addr: "anything!"}) is None   # no check enforced


def test_msg_suffix_forms_id_from_member_name():
    cat = load_messages(
        '<msgmbr name="ABCD00">'
        '<msg suffix="1">First message<msg suffix="2">Second message'
        '</msgmbr>'
    )
    assert cat.format("ABCD001") == "ABCD001 First message"
    assert cat.format("ABCD002") == "ABCD002 Second message"


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


def test_vardcl_outside_varlist_is_tolerated():
    # A stray <vardcl> (some guide examples begin mid-declaration) is ignored
    # rather than aborting the panel: the panel still renders its body.
    s = load_dtl('<panel><vardcl name="x" varclass="C"/>'
                 '<info row="1" col="1">HELLO</info></panel>')
    assert any(getattr(i, "text", None) == "HELLO" for i in s.items)


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
