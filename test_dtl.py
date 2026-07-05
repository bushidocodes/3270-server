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


def test_source_proc_zsel_parses_selection_targets():
    # A )PROC `&ZSEL = TRANS(&ZCMD n,'target' ...)` records each option's selection
    # string; the source expression, the blank pair and the `*` default are skipped,
    # and the block renders nothing.
    s = load_dtl(
        "<panel>Menu<area><info>body</info>"
        "<source type=proc>"
        "  &ZSEL = TRANS( TRUNC(&ZCMD,'.')"
        "      0,'PANEL(settings)'"
        "      6,'PGM(cmdshell)'"
        "      X,'EXIT'"
        "      ' ',' '"
        "      *,'?' )"
        "</source></area></panel>"
    )
    assert s.selection_targets == {
        "0": "PANEL(settings)", "6": "PGM(cmdshell)", "X": "EXIT",
    }
    assert [it for it in s.items if isinstance(it, Text) and "ZSEL" in it.text] == []


def _zsel_targets(proc):
    s = load_dtl(f'<panel>M<area><info>x</info><source type=proc>{proc}</source>'
                 '</area></panel>')
    return dict(s.selection_targets)


def test_zsel_ignores_content_after_the_trans_body():
    # The TRANS(...) body is taken by balanced parens, so a second statement after
    # it (e.g. another assignment) can't leak a spurious option in.
    assert _zsel_targets(
        "&ZSEL = TRANS(&ZCMD 1,'PGM(a)') &X = TRANS(&Y 2,'BOGUS')"
    ) == {"1": "PGM(a)"}


def test_zsel_first_declaration_of_an_option_wins():
    # ISPF's TRANS returns the first match, so a duplicated option keeps the first.
    assert _zsel_targets("&ZSEL = TRANS(&ZCMD 1,'PGM(a)' 1,'PGM(b)')") == {"1": "PGM(a)"}


def test_zsel_selection_string_keeps_a_comma_inside_parm():
    assert _zsel_targets("&ZSEL = TRANS(&ZCMD 2,'PGM(x) PARM(A,B)')") == {
        "2": "PGM(x) PARM(A,B)"}


def test_ispf_menu_routing_is_declared_in_the_panel():
    # PR 1 of #55: the ISPF primary menu's option->target routing now lives in
    # ispf.dtl's )PROC. This asserts the declared map equals the routing the server
    # currently hard-codes, so the later dispatch switch is provably equivalent.
    s = load_panel("ispf")
    assert s.selection_targets == {
        "0": "PANEL(settings)",   "1": "PGM(view)",       "2": "PGM(edit)",
        "3": "PANEL(utility)",    "4": "PANEL(foreground)", "5": "PANEL(batch)",
        "6": "PGM(cmdshell)",     "7": "PGM(dlgtest)",    "9": "PANEL(ibmprod)",
        "10": "PANEL(sclm)",      "11": "PANEL(workplace)", "12": "PANEL(zsystem)",
        "13": "PANEL(zuser)",     "X": "EXIT",
    }


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
    # A blank line follows a TOPINST and precedes a PNLINST; the two coincide into a
    # single blank between them (row 4), so the PNLINST lands on row 5.
    assert s.items[0] == Text(3, 1, "line one", DisplayIntensity.NORMAL)
    assert s.items[1] == Text(5, 1, "line two", DisplayIntensity.NORMAL)


def test_botinst_anchors_at_the_panel_foot():
    # A <botinst> is a *bottom* instruction: it renders near the foot of the panel
    # (leaving the last row free), not inline after the body like <topinst>.
    s = load_dtl(
        '<panel><area>'
        '<topinst>near the top</topinst>'
        '<botinst>To exit, press F3.</botinst>'
        '</area></panel>'
    )
    top = next(it for it in s.items if isinstance(it, Text) and "top" in it.text)
    bot = next(it for it in s.items if isinstance(it, Text) and "F3" in it.text)
    assert bot.row == s.depth - 2          # one line, anchored above the last row
    assert bot.row > top.row + 1           # pushed to the foot, not stacked below top


def test_botinst_drops_below_a_body_that_reaches_the_foot():
    # If the body already flows past the anchor row, the bottom instruction sits
    # below it rather than overlapping.
    body = "".join(f"<info>body line {r}</info>" for r in range(23))
    s = load_dtl(f"<panel><area>{body}<botinst>foot</botinst></area></panel>")
    bot = next(it for it in s.items if isinstance(it, Text) and it.text == "foot")
    last_body = max(it.row for it in s.items if isinstance(it, Text) and it.text != "foot")
    assert bot.row >= last_body            # below the body, no overlap


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


def test_hp_flow_wrapped_keeps_highlight_across_lines():
    # #208: an <hp> inside flowed (word-wrapped) <p> text keeps its highlight on
    # every line the phrase spans — previously the flow path dropped the runs and
    # rendered plain text. Spaces *inside* the phrase stay highlighted too.
    s = load_dtl('<panel name="p" width="20">'
                 '<p>Please read the <hp hilite="reverse">important safety notice</hp>'
                 ' before use.</panel>')
    N = DisplayIntensity.NORMAL
    R = Highlight.REVERSE
    # r0: plain; r1: whole phrase span REVERSE (incl. interior space); r2: tail.
    assert s.items[0] == Text(0, 1, "Please read the", N)
    assert s.items[1].runs == [("important safety", None, R)]
    assert s.items[2].runs == [("notice", None, R), (" before use.", None, None)]


def test_hp_flow_wrapped_mono_is_byte_identical_to_plain_wrap():
    # Mono renders each wrapped line as its plain text, so adding <hp> to flowed
    # body text does not change the mono data stream.
    src_hp = ('<panel name="p" width="20">'
              '<p>Please read the <hp hilite="reverse">important safety notice</hp>'
              ' before use.</panel>')
    src_plain = ('<panel name="p" width="20">'
                 '<p>Please read the important safety notice before use.</panel>')
    hp = load_dtl(src_hp).render(color=False)
    plain = load_dtl(src_plain).render(color=False)
    assert hp == plain


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


def test_dtafld_display_no_numeric_and_init():
    # IBM's DISPLAY=NO makes a non-display field (e.g. a password); a field with
    # no DISPLAY= is shown. INIT= sets the field's initial value.
    s = load_dtl(
        '<panel>'
        '<dtafld row="6" col="1" fldcol="16" datavar="pw" entwidth="8" display="no">P</dtafld>'
        '<dtafld row="8" col="1" fldcol="16" datavar="sz" entwidth="5" numeric="yes" init="00150">S</dtafld>'
        '</panel>'
    )
    pw = s.items[1]
    assert pw.hidden and not pw.numeric
    sz = s.items[3]
    assert not sz.hidden and sz.numeric and sz.default == "00150"


def test_dtafld_init_sets_initial_value():
    # INIT= is the DTL attribute for a field's initial value. The non-standard
    # `default=` is NOT read (no legacy alias) — it is silently ignored.
    s = load_dtl(
        '<panel>'
        '<dtafld row="6" col="1" fldcol="16" datavar="a" entwidth="8" init="IKJACCNT">A</dtafld>'
        '<dtafld row="8" col="1" fldcol="16" datavar="b" entwidth="5" default="99999">B</dtafld>'
        '</panel>'
    )
    assert s.items[1].default == "IKJACCNT"
    assert s.items[3].default == ""


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


def test_dtafld_inline_prompt_plus_dtafldd_description():
    # A <dtafld> row is prompt + entry + description: the inline text is the
    # prompt, and a nested <dtafldd> is the trailing description (past the entry).
    s = load_dtl(
        '<panel>'
        '<dtafld row="5" col="1" fldcol="16" datavar="author" entwidth="20">Author'
        '  <dtafldd>Last name, First name, M.I.'
        '</dtafld></panel>'
    )
    texts = [it for it in s.items if isinstance(it, Text)]
    prompt = [t for t in texts if t.col == 1]
    assert prompt and prompt[0].text.strip() == "Author" and prompt[0].role == "prompt"
    # description sits past the entry's data run + terminator attr: 16 + 20 + 2.
    desc = [t for t in texts if t.col == 38]
    assert desc and desc[0].text == "Last name, First name, M.I."


def test_dtafld_deswidth_truncates_the_description():
    s = load_dtl(
        '<panel>'
        '<dtafld row="5" col="1" fldcol="10" datavar="x" entwidth="5" deswidth="10">P'
        '  <dtafldd>0123456789ABCDEF'
        '</dtafld></panel>'
    )
    desc = [t for t in s.items if isinstance(t, Text) and t.col > 10]
    assert desc and desc[0].text == "0123456789"      # sized to DESWIDTH=10


def test_dtafld_sole_dtafldd_is_still_the_prompt():
    # With no inline text, a <dtafldd> stands in as the prompt and adds no
    # trailing description (the shorthand the bundled panels use).
    s = load_dtl(
        '<panel>'
        '<dtafld row="5" col="1" fldcol="16" datavar="u" entwidth="8">'
        '  <dtafldd>Userid ===></dtafld></panel>'
    )
    texts = [it for it in s.items if isinstance(it, Text)]
    assert len(texts) == 1 and texts[0] == Text(5, 1, "Userid ===>", DisplayIntensity.NORMAL, role="prompt")


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


def test_selfld_single_choice_auto_layout_matches_reference():
    # #183: a column-less single-choice field whose choices omit NUM auto-lays out
    # per the CHOICE reference figure — a selection input field before the first
    # choice, then each choice numbered "N." (number + period), then its text.
    s = load_dtl(
        '<panel name="p"><selfld name="dest">Where:'
        '<choice>London<choice>Madrid<choice>Paris</selfld></panel>'
    )
    N, H = DisplayIntensity.NORMAL, DisplayIntensity.HIGH
    # one selection input field, before the first choice, named after the SELFLD
    fields = [it for it in s.items if isinstance(it, Field)]
    assert len(fields) == 1 and fields[0].row == 1 and fields[0].name == "dest"
    # numbers carry a period; descriptions follow
    assert Text(1, 5, "1.", H) in s.items and Text(1, 9, "London", N) in s.items
    assert Text(2, 5, "2.", H) in s.items and Text(3, 5, "3.", H) in s.items
    # the typed numbers are the selectable values
    assert set(s.selections) == {"1", "2", "3"}


def test_selfld_explicit_num_still_wins_and_grid_is_unchanged():
    # An explicit NUM (and explicit columns) keep the fixed grid — the bundled
    # panels rely on this, so it must stay byte-for-byte as before.
    s = load_dtl(
        '<panel><selfld row="4" numcol="1" namecol="4" desccol="21">'
        '<choice num="7" name="  A">  desc-a</choice></selfld></panel>'
    )
    assert s.items[0] == Text(4, 1, "7 ", DisplayIntensity.HIGH)
    assert s.items[2] == Text(4, 21, "  desc-a", DisplayIntensity.NORMAL)


def test_selfld_type_multi_renders_a_mark_field_per_choice():
    # TYPE=MULTI is a multiple-selection field: each choice gets its own 1-char
    # input field to mark (in place of a number), so several can be selected.
    s = load_dtl(
        '<panel><selfld name="off" type="multi" row="4" namecol="4" desccol="21">'
        '<choice name="pat" match="P">Patent</choice>'
        '<choice name="def" match="D">Defamation</choice>'
        '</selfld></panel>'
    )
    m0, m1 = s.items[0], s.items[2]
    assert isinstance(m0, Field) and m0.row == 4 and m0.col == 1 and m0.length == 1
    assert isinstance(m1, Field) and m1.row == 5 and m1.col == 1
    # The choice NAME is the field identifier (read the mark back), not display
    # text: a multi row is just the mark + description.
    assert s.items[1] == Text(4, 21, "Patent", DisplayIntensity.NORMAL)
    assert not any(getattr(it, "text", None) == "pat" for it in s.items)
    # No numbered Text is emitted for a multi-select choice.
    assert not any(isinstance(it, Text) and it.role == "num" for it in s.items)


def test_selfld_type_multi_records_and_reads_selected_values():
    s = load_dtl(
        '<panel><selfld name="off" type="multi" row="4">'
        '<choice name="pat" match="P">Patent</choice>'
        '<choice name="def" match="D">Defamation</choice>'
        '<choice name="fra" match="F">Fraud</choice>'
        '</selfld></panel>'
    )
    assert [sf["value"] for sf in s.selection_fields] == ["P", "D", "F"]
    addrs = {sf["value"]: sf["addr"] for sf in s.selection_fields}
    # The user marks Patent and Fraud (any non-blank char), leaves Defamation blank.
    marked = {addrs["P"]: "/", addrs["D"]: " ", addrs["F"]: "S"}
    assert s.selected_values(marked) == ["P", "F"]
    assert s.selected_values({}) == []                 # nothing marked


def test_selfld_type_multi_unavail_choice_has_no_mark_field():
    # An unavailable choice can't be selected, so it gets no mark field and is
    # not recorded as readable.
    s = load_dtl(
        '<panel><selfld name="off" type="multi" row="4">'
        '<choice name="ok" match="A">Available</choice>'
        '<choice name="no" match="B" unavail>Unavailable</choice>'
        '</selfld></panel>'
    )
    marks = [it for it in s.items if isinstance(it, Field)]
    assert len(marks) == 1 and marks[0].row == 4       # only the available choice
    assert [sf["value"] for sf in s.selection_fields] == ["A"]


def test_selfld_type_single_is_unchanged():
    # The default (SINGLE) keeps the numbered layout — no mark fields.
    s = load_dtl(
        '<panel><selfld row="4"><choice num="1" name="A">desc</choice></selfld></panel>'
    )
    assert not any(isinstance(it, Field) for it in s.items)
    assert s.items[0] == Text(4, 1, "1 ", DisplayIntensity.HIGH)
    assert s.selection_fields == []
    assert s.selection_rows == {4: "1"}


def test_choice_hide_removes_it_when_variable_true():
    # HIDE=var removes the choice when the variable is true; the choices below it
    # move up and it is not selectable. HIDEX=var is the inverse (hide when false).
    N, H = DisplayIntensity.NORMAL, DisplayIntensity.HIGH
    src = (
        '<panel><selfld row="4" numcol="1" namecol="4" desccol="21">'
        '<choice num="1" name="A" match="A" hide="vh">Alpha</choice>'
        '<choice num="2" name="B" match="B">Beta</choice>'
        '<choice num="3" name="C" match="C" hidex="vs">Gamma</choice>'
        '</selfld></panel>'
    )
    # vh true → A hidden; vs false → C hidden. Only B remains, at the top row.
    s = load_dtl(src, vh="1", vs="0")
    assert s.items[0] == Text(4, 1, "2 ", H)
    assert s.items[1] == Text(4, 4, "B", N)
    assert s.selections == {"B": "B"}                  # A and C not selectable
    assert s.selection_rows == {4: "B"}

    # vh false → A shown; vs true → C shown. All three render on successive rows.
    s2 = load_dtl(src, vh="0", vs="1")
    assert [it.text for it in s2.items if it.col == 4] == ["A", "B", "C"]
    assert set(s2.selections) == {"A", "B", "C"}


def test_hidden_choice_stays_out_of_selections_even_when_proc_routes_it():
    # A HIDE choice is dropped from `selections`; if the panel's )PROC still lists
    # its option, that target remains in selection_targets — so the server must
    # route only options that are in `selections` (it checks `head in selections`).
    # This asserts the data precondition that lets the gate block a hidden option.
    s = load_dtl(
        '<panel><selfld row="4" numcol="1" namecol="4" desccol="21">'
        '<choice num="1" name="Open">Open'
        '<choice num="7" name="Secret" hide="secret">Secret op</choice>'
        '</selfld>'
        "<source type=proc>&ZSEL = TRANS(&ZCMD 1,'PANEL(a)' 7,'PGM(secret)')</source>"
        '</panel>',
        secret="1",                                    # choice 7 hidden
    )
    assert "7" not in s.selections                     # hidden -> not selectable
    assert "7" in s.selection_targets                  # but its )PROC target lingers
    assert "1" in s.selections and "1" in s.selection_targets


def test_choice_bare_hide_always_removes_it():
    s = load_dtl(
        '<panel><selfld row="4"><choice num="1" name="A" hide>Alpha</choice>'
        '<choice num="2" name="B">Beta</choice></selfld></panel>'
    )
    assert [it.text for it in s.items if it.col == 4] == ["B"]


def test_selfld_prompt_renders_above_list_by_default():
    # The text between <selfld ...> and the first <choice> is the field prompt.
    # PMTLOC defaults to ABOVE: the caption sits on the line above the choices,
    # which then flow below it.
    N, H = DisplayIntensity.NORMAL, DisplayIntensity.HIGH
    s = load_dtl(
        '<panel><selfld name="day" selwidth="20">Weekdays:'
        '<choice num="1" name="Mon">day1</choice>'
        '<choice num="2" name="Tue">day2</choice>'
        '</selfld></panel>'
    )
    assert s.items[0] == Text(0, 1, "Weekdays:", N)   # caption on the first row
    assert s.items[1] == Text(1, 1, "1 ", H)          # choices pushed down one row
    assert s.items[4] == Text(2, 1, "2 ", H)


def test_selfld_prompt_before_wraps_and_shifts_choices():
    # PMTLOC=BEFORE puts the caption to the list's left, wrapped into its PMTWIDTH
    # column; the choice columns shift right past it and the first choice shares
    # the caption's top row.
    N, H = DisplayIntensity.NORMAL, DisplayIntensity.HIGH
    s = load_dtl(
        '<panel><selfld name="cs" pmtwidth="11" pmtloc="before" '
        'numcol="1" namecol="4" desccol="21">Choose one of the following'
        '<choice num="1" name="Civ">Civil</choice></selfld></panel>'
    )
    # caption wrapped to <= 11 columns, each on its own row from the top
    assert s.items[0] == Text(0, 1, "Choose one", N)
    assert s.items[1] == Text(1, 1, "of the", N)
    assert s.items[2] == Text(2, 1, "following", N)
    # first choice on the top row, its columns shifted right of the 11-col prompt
    assert s.items[3] == Text(0, 12, "1 ", H)         # numcol 1 -> 1 + 11
    assert s.items[4] == Text(0, 15, "Civ", N)        # namecol 4 -> 4 + 11


def test_selfld_empty_prompt_renders_nothing():
    # The bundled numbered menus have only whitespace between <selfld> and the
    # first <choice> — that must render nothing so they stay byte-identical.
    s = load_dtl(
        '<panel><selfld row="4" numcol="1" namecol="4" desccol="21">\n  '
        '<choice num="1" name="A">desc</choice></selfld></panel>'
    )
    assert s.items[0] == Text(4, 1, "1 ", DisplayIntensity.HIGH)   # no prompt item
    assert s.items[1] == Text(4, 4, "A", DisplayIntensity.NORMAL)


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
        '<cmd name="KEYLIST" altdescr="Keys">KEY<t>LIST<cmdact action="passthru"></cmd>'
        '<cmd name="BYE">Leave<cmdact action="alias exit"></cmd>'
        '</cmdtbl>'
        '</panel>'
    )


def test_cmdtbl_parses_commands_and_actions():
    s = _cmd_panel()
    assert s.commands["PANELID"]["action"] == "passthru"
    assert s.commands["KEYLIST"]["trunc"] == 3       # from the <t> marker (KEY|LIST)
    assert s.commands["KEYLIST"]["descr"] == "Keys"  # ALTDESCR (metadata)
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
        '<abc>Menu<pdc>Exit<action run="exit"></pdc></abc>'
        '<abc>Help<pdc>About<action run="passthru"></pdc></abc>'
        '</ab>'
        '</panel>'
    )
    # Labels laid out across row 0: Menu at col 1, Help at col 1+4+3 = 8.
    assert s.items[0] == Text(0, 1, "Menu", DisplayIntensity.HIGH)
    assert s.items[1] == Text(0, 8, "Help", DisplayIntensity.HIGH)
    # Pull-down structure + rendered position preserved for interaction.
    assert s.action_bar == [
        {"label": "Menu", "row": 0, "col": 1, "mnemonic": None, "help": None,
         "pdc": [{"label": "Exit", "action": "exit", "mnemonic": None, "help": None}]},
        {"label": "Help", "row": 0, "col": 8, "mnemonic": None, "help": None,
         "pdc": [{"label": "About", "action": "passthru", "mnemonic": None, "help": None}]},
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
        {"label": "File", "row": 0, "col": 1, "mnemonic": None, "help": None,
         "pdc": [{"label": "Add", "action": "add", "mnemonic": 0, "help": None},
                 {"label": "Delete", "action": "delete", "mnemonic": 0, "help": None}]},
        {"label": "View", "row": 0, "col": 8, "mnemonic": None, "help": None,
         "pdc": [{"label": "Name", "action": "name", "mnemonic": 0, "help": None}]},
    ]


def test_pulldown_item_records_its_help_panel():
    # <pdc help=...> records a per-item help panel (like <dtafld help=>), resolved
    # by name; NO/YES/*/% are field-help sentinels, not panel names, so they don't
    # count as a help panel here.
    s = load_dtl(
        '<panel><ab row="0" col="1">'
        '<abc>Log/List'
        '<pdc help="loglisthelp">Log Data Set defaults<action run="passthru"></pdc>'
        '<pdc>Keylist settings<action run="passthru"></pdc>'
        '</abc></ab></panel>'
    )
    pdc = s.action_bar[0]["pdc"]
    assert pdc[0]["help"] == "loglisthelp"
    assert pdc[1]["help"] is None          # no help attribute


def test_settings_pulldown_item_carries_help():
    s = load_panel("settings")
    loglist = s.action_bar[0]["pdc"]
    assert loglist[0]["label"] == "Log Data Set defaults"
    assert loglist[0]["help"] == "loglisthelp"


def test_action_bar_mnemonic_is_recorded_and_underlined():
    # <M> marks the shortcut letter; it's recorded by offset and rendered with an
    # underscore highlight (mono is byte-identical to a plain high-intensity label).
    s = load_dtl(
        '<panel><ab row="0" col="1">'
        '<abc><M>File<pdc>Open<action run="x"></pdc>'
        '<abc>E<M>xit<pdc>Leave<action run="exit"></pdc>'
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
        '<abc>Menu<pdc>A<action run="x"></pdc></abc>'
        '<abc>Help<pdc>B<action run="y"></pdc></abc>'
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


# ── small structural tags: <pdsep>, <rp>, <t>, <varsub> (#118) ───────────────

def test_pdsep_records_separator_between_pulldown_choices():
    # <PDSEP> is a divider row within an action-bar pull-down: it closes the
    # choice above it (DTL omits end tags) and lands a separator marker between
    # the pull-down choices, without itself being a selectable choice.
    s = load_dtl(
        '<panel><ab row="0" col="1">'
        '<abc>File'
        '<pdc>Add<action run=add><pdsep>'
        '<pdc>Exit<action run=exit>'
        '</abc></ab></panel>'
    )
    assert s.action_bar[0]["pdc"] == [
        {"label": "Add", "action": "add", "mnemonic": None, "help": None},
        {"separator": True},
        {"label": "Exit", "action": "exit", "mnemonic": None, "help": None},
    ]


def test_pdsep_outside_abc_raises():
    with pytest.raises(DTLError):
        load_dtl('<panel><ab row="0" col="1"><pdsep></ab></panel>')


def test_pdsep_renders_as_divider_row_in_open_pulldown():
    # When the pull-down opens, the separator is a non-selectable divider row; the
    # real choices keep a continuous 1..N numbering across it (and the renderer
    # does not KeyError on the label-less separator entry).
    import server
    from screen import Screen
    choice = {"label": "File", "row": 0, "col": 1, "pdc": [
        {"label": "Add", "action": "add", "mnemonic": None, "help": None},
        {"separator": True},
        {"label": "Exit", "action": "exit", "mnemonic": None, "help": None},
    ]}
    scr = Screen()
    # A PF3 reply closes the pull-down after it is laid out and sent once.
    server._show_pulldown(_Sock2([bytes([0xF3, 0xFF, 0xEF])]), scr, choice)
    by_row = {it.row: it.text for it in scr.items if it.col == 1}
    # top border (0+1), items and the divider between them, bottom border.
    assert by_row[2] == "| 1. Add  |"   # first choice numbered 1
    assert by_row[3] == "|---------|"   # separator: a divider row, not a choice
    assert by_row[4] == "| 2. Exit |"   # numbering continues past the separator


class _Sock2:
    """A fake client socket: replies with canned inbound records, then EOF."""
    def __init__(self, replies):
        self.sent = []
        self._replies = iter(replies)
    def sendall(self, data): self.sent.append(data)
    def recv(self, _n): return next(self._replies, b"")
    def settimeout(self, _t): pass
    def close(self): pass


def test_rp_reference_phrase_is_inline_underlined_link():
    # <rp> (reference phrase — a link to another help panel) emphasises a phrase
    # in place, like <hp>: one Text.rich whose phrase run is underlined.
    s = load_dtl(
        '<panel><info row="2" col="1">see <rp help=glospan>the glossary</rp> now</info></panel>'
    )
    assert len(s.items) == 1
    assert s.items[0].runs == [
        ("see ", None, None),
        ("the glossary", None, Highlight.UNDERSCORE),
        (" now", None, None),
    ]


def test_rp_mono_is_byte_identical_to_plain_text():
    # A reference phrase is safe on a mono terminal: the underline is colour-only,
    # so the data stream matches the plain concatenated line byte-for-byte.
    s = load_dtl(
        '<panel><info row="2" col="1">see <rp help=g>the glossary</rp> now</info></panel>'
    )
    it = s.items[0]
    rich = bytearray(); it.render(rich, color=False)
    plain = bytearray(); Text(2, 1, it.text).render(plain, color=False)
    assert bytes(rich) == bytes(plain)
    assert SA not in bytes(rich)


def test_cmd_t_marks_truncation_point():
    # <t> inside a <cmd> external name marks where truncation is allowed: the
    # characters before it are the minimum abbreviation the user must type.
    s = load_dtl(
        '<panel><cmdtbl>'
        '<cmd name=CANCEL>CANC<t>EL<cmdact action=cancel></cmd>'
        '<cmd name=FIND>FIND<cmdact action=find></cmd>'
        '</cmdtbl></panel>'
    )
    assert s.commands["CANCEL"]["trunc"] == 4
    assert s.commands["FIND"]["trunc"] == 0        # no <t> -> not truncatable
    assert sorted(s.commands["CANCEL"]) == ["action", "descr", "trunc"]  # no capture leaks
    assert s.commands["CANCEL"]["descr"] == ""     # no ALTDESCR given
    assert s.lookup_command("CANC") == "cancel"    # abbreviation matches
    assert s.lookup_command("CAN") is None         # below the truncation point


def test_varsub_substitutes_variable_in_message_text():
    # <varsub var=NAME> inside <msg> text emits an &NAME. reference, resolved at
    # display time by MessageCatalog.format — exactly like a literal &NAME.
    cat = load_messages(
        '<msgmbr name=LIB00>'
        '<msg msgid=LIB001>Found <varsub var=count> entries in <varsub var=lib>.</msg>'
        '</msgmbr>'
    )
    assert cat.format("LIB001", COUNT="7", LIB="SYS1") == \
        "LIB001 Found 7 entries in SYS1."


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


def test_panel_dimensions_validate_bounds_and_fit():
    # Per the PANEL reference: WIDTH is 16..160, DEPTH is 5..62; an out-of-range,
    # FIT, %varname or non-numeric value falls back to the default (as ISPDTLC
    # warns + uses the default) rather than crashing or using the bad value.
    assert load_dtl('<panel width="16" depth="5"></panel>').width == 16      # min ok
    assert load_dtl('<panel width="160" depth="62"></panel>').depth == 62    # max ok
    assert load_dtl('<panel width="15"></panel>').width == 80                # below min
    assert load_dtl('<panel width="161"></panel>').width == 80               # above max
    assert load_dtl('<panel depth="4"></panel>').depth == 24                 # below min
    assert load_dtl('<panel depth="63"></panel>').depth == 24                # above max
    # FIT / %varname / non-numeric no longer raise; they keep the default.
    assert load_dtl('<panel width="FIT" depth="FIT"></panel>').width == 80
    assert load_dtl('<panel width="%wvar"></panel>').width == 80
    assert load_dtl('<panel width="wide"></panel>').width == 80


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


def test_action_bar_choice_help_resolved_by_cursor():
    # <abc help=panel>: HELP with the cursor on that action-bar choice shows its
    # own help; a choice without HELP resolves to None (the panel help is used).
    s = load_dtl(
        '<panel><ab row="0" col="1">'
        '<abc help="filehelp">File<pdc>Open<action run="x"></pdc>'
        '<abc>Edit<pdc>Cut<action run="y"></pdc>'
        '</ab></panel>'
    )
    file_c, edit_c = s.action_bar
    on_file = file_c["row"] * 80 + file_c["col"]
    assert s.help_for(on_file) == "filehelp"           # cursor on "File"
    assert s.help_for(on_file + 3) == "filehelp"       # within the label
    assert s.help_for(edit_c["row"] * 80 + edit_c["col"]) is None   # Edit: no help


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
    # DTL omits end tags; each <p> closes the previous one. ISPDTLC inserts a blank
    # line before each paragraph (the first has no title above it here), so the
    # second paragraph lands two rows below the first.
    s = load_dtl('<panel name="p"><p>First para.<p>Second para.</panel>')
    assert s.items[0] == Text(0, 1, "First para.", DisplayIntensity.NORMAL)
    assert s.items[1] == Text(2, 1, "Second para.", DisplayIntensity.NORMAL)


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


def test_simple_list_indents_items_without_a_bullet():
    # <sl> (simple list) flows each <li> indented one level, with NO marker
    # (unlike <ul>'s bullet). The `compact` attribute is accepted but has no
    # visual effect in our line-based render.
    s = load_dtl('<panel name="p"><sl compact><li>Faith<li>Hope<li>Charity</sl></panel>')
    assert s.items == [
        Text(0, 5, "Faith", DisplayIntensity.NORMAL),
        Text(1, 5, "Hope", DisplayIntensity.NORMAL),
        Text(2, 5, "Charity", DisplayIntensity.NORMAL),
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


def test_xmp_renders_preformatted_like_lines():
    # #206: <xmp> (example) is preformatted like <lines> — authored line breaks
    # and interior spacing are significant; the common source indent is stripped.
    s = load_dtl(
        '<panel name="p" width="40"><info><xmp>\n'
        '  A = 1\n'
        '    B = 22\n'
        '  C = 333\n'
        '</xmp></info></panel>'
    )
    N = DisplayIntensity.NORMAL
    assert s.items == [
        Text(0, 1, "A = 1", N),
        Text(1, 1, "  B = 22", N),   # interior indent kept (common indent stripped)
        Text(2, 1, "C = 333", N),
    ]


def test_fig_frames_content_with_rules_and_caption():
    # #207: <fig> flows its content as a figure; FRAME=RULE (default) draws a
    # horizontal rule above and below, and <figcap> renders a caption beneath.
    s = load_dtl(
        '<panel name="p" width="24">'
        '<fig><p>Assemble it.<figcap>Fig 1. Assembly.</figcap></fig>'
        '</panel>'
    )
    N = DisplayIntensity.NORMAL
    rule = "-" * (24 - 1 - 1)
    assert s.items == [
        Text(0, 1, rule, N),               # top rule
        Text(1, 1, "Assemble it.", N),     # flowed content
        Text(2, 1, rule, N),               # bottom rule
        Text(3, 1, "Fig 1. Assembly.", N), # caption beneath
    ]


def test_fig_frame_none_omits_the_rules():
    s = load_dtl('<panel name="p" width="24">'
                 '<fig frame="none"><p>Bare.<figcap>Cap.</figcap></fig></panel>')
    N = DisplayIntensity.NORMAL
    assert s.items == [Text(0, 1, "Bare.", N), Text(1, 1, "Cap.", N)]


def test_content_after_fig_resumes_below_it():
    # The enclosing flow resumes on the row after the whole figure (rules +
    # content + caption).
    s = load_dtl('<panel name="p" width="24">'
                 '<fig><p>In figure.</fig><p>After figure.</panel>')
    after = [it for it in s.items if it.text == "After figure."]
    assert after and after[0].row == 3   # top rule(0) + content(1) + bottom rule(2)


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
    ("warning", "Warning:"), ("attention", "Attention:"),
])
def test_admonition_labels(tag, label):
    # ATTENTION / WARNING prefix the body inline (the reference figures).
    s = load_dtl(f'<panel width="50"><area><info><{tag}>Mind the gap.'
                 f'</info></area></panel>')
    texts = [t.text for t in s.items if isinstance(t, Text)]
    assert any(t.startswith(label) and "Mind the gap." in t for t in texts)


def test_note_wraps_to_margin_and_nt_hangs_and_text_overrides_heading():
    # #116: <note> is a single paragraph, "Note:" inline, wrapping to the margin.
    s = load_dtl('<panel width="50"><area><info>'
                 '<note>Mind the gap between the platform and the train.</note>'
                 '</info></area></panel>')
    assert any(t.text.startswith("Note: Mind the gap") for t in s.items
               if isinstance(t, Text))
    # <nt> hangs its body under the text: heading and body are separate Texts.
    nt = load_dtl('<panel width="30"><area><info>'
                  '<nt>Mind the gap here please.</nt></info></area></panel>')
    head = next(t for t in nt.items if getattr(t, "text", "") == "Note:")
    body = [t for t in nt.items if t.col == head.col + 6]      # hung past "Note: "
    assert body and body[0].row == head.row                    # first line shares the row
    # TEXT= replaces the heading.
    tip = load_dtl('<panel width="40"><area><info>'
                   '<note text="Tip:">Save often.</note></info></area></panel>')
    assert any(t.text.startswith("Tip: Save often") for t in tip.items
               if isinstance(t, Text))


def test_caution_heading_on_own_line_and_emphasized():
    # Unlike ATTENTION/WARNING, the CAUTION reference puts "CAUTION:" (uppercase)
    # on its own line with the emphasised (high-intensity) body beneath it.
    s = load_dtl('<panel width="50"><area><info>'
                 '<p>The DELETE command erases the file.'
                 '<p><caution>Issuing DELETE permanently removes the file.</caution>'
                 '</info></area></panel>')
    heading = next(t for t in s.items if getattr(t, "text", "") == "CAUTION:")
    assert heading.intensity == DisplayIntensity.HIGH
    body = [t for t in s.items if t.row > heading.row
            and getattr(t, "text", "").startswith("Issuing")]
    assert body and all(t.intensity == DisplayIntensity.HIGH for t in body)


def test_inline_note_keeps_following_paragraph():
    # <nt>text<p>more</nt>: the note flows labelled (heading + hung body), the
    # nested <p> flows after it, and the trailing <p> renders too.
    s = load_dtl(
        '<panel width="50"><area><info>'
        '<nt>Out of stock.<p>Arrives in three days.</nt>'
        '<p>Order below.</info></area></panel>'
    )
    texts = [t.text for t in s.items if isinstance(t, Text)]
    assert "Note:" in texts and any("Out of stock." in t for t in texts)
    assert any("three days" in t for t in texts)
    assert any("Order below." in t for t in texts)


def test_note_list_renders_heading_and_numbered_items():
    # #116: NOTEL is a "Notes:" heading, a blank line, then NUMBERED items (1. 2.)
    # — the reference figure, not the bulleted form we had before.
    s = load_dtl(
        '<panel width="50"><area><info><notel>'
        '<li>First note.<li>Second note.</notel></info></area></panel>'
    )
    texts = [t.text for t in s.items if isinstance(t, Text)]
    assert "Notes:" in texts
    assert "1." in texts and "2." in texts               # numbered, not bulleted
    assert "o" not in texts                              # no ul bullet
    assert any("First note." in t for t in texts)


# ── list/table fields (<lstfld>/<lstcol>/<lstgrp>) ───────────────────────────

def test_list_field_columns_laid_out_with_headings():
    # Columns flow left to right by colwidth plus the CUA attribute-byte gutter
    # (an input column reserves +3: lead attr, trail attr, trailing blank); their
    # headings render on one row, with an (empty) input model row beneath.
    s = load_dtl(
        '<panel name="p"><area>'
        '<lstfld><lstcol datavar=a colwidth=5>Mon<lstcol datavar=b colwidth=5>Tue</lstfld>'
        '</area></panel>'
    )
    H = DisplayIntensity.HIGH
    assert s.items == [
        Text(0, 1, "Mon", H), Text(0, 9, "Tue", H),
        Field(row=1, col=1, length=5, name="a", terminator=False),
        Field(row=1, col=9, length=5, name="b", terminator=True),
    ]


def test_list_field_group_headline_dashes_over_columns():
    # A <lstgrp headline=yes> heading is centered over its columns' span and padded
    # with a dashed rule ("--- Wk ----"), on the row above the column headings.
    s = load_dtl(
        '<panel name="p"><area>'
        '<lstfld><lstgrp headline=yes>Wk'
        '<lstcol datavar=a colwidth=5>Mon<lstcol datavar=b colwidth=5>Tue'
        '</lstgrp></lstfld>'
        '</area></panel>'
    )
    H = DisplayIntensity.HIGH
    assert s.items == [
        Text(0, 1, "---- Wk -----", H),             # dashed rule over cols 1..13
        Text(1, 1, "Mon", H), Text(1, 9, "Tue", H),
        Field(row=2, col=1, length=5, name="a", terminator=False),
        Field(row=2, col=9, length=5, name="b", terminator=True),
    ]


def test_list_column_heading_not_truncated_and_all_groups_shown():
    # #53: a heading wider than COLWIDTH sets the column formatting width (so "MI"
    # over a 1-wide column isn't clipped to "M"); every group with a heading shows
    # on the group row, not only HEADLINE=yes ones.
    s = load_dtl(
        '<panel name="p" width="60"><area><lstfld>'
        '<lstgrp headline=yes>Subscriber'
        '<lstcol datavar=n colwidth=1>MI<lstcol datavar=l colwidth=12>Last</lstgrp>'
        '<lstgrp>Phone<lstcol datavar=p colwidth=12>Number</lstgrp>'
        '</lstfld></area></panel>'
    )
    texts = [t.text for t in s.items if isinstance(t, Text)]
    assert "MI" in texts                              # heading kept full (colwidth 1)
    assert any(t.startswith("-") and "Subscriber" in t for t in texts)  # headline dashes
    assert "Phone" in texts                           # headline=no group still shown


def test_list_group_heading_alignment():
    # #221 follow-up: LSTGRP ALIGN. The default (CENTER) centres a heading over
    # MULTIPLE columns but LEFT-justifies it over a SINGLE column, so a one-column
    # group heading sits directly above its column (not floated to the centre).
    def gpos(markup):
        s = load_dtl('<panel name="p" width="60"><area><lstfld>'
                     + markup + '</lstfld></area></panel>')
        return {t.text: t.col for t in s.items
                if getattr(t, "row", None) == 0 and hasattr(t, "text")}

    one = '<lstgrp>Phone<lstcol datavar=p colwidth=12>Number</lstgrp>'
    # Single column: Phone (default center) is left-justified over col x=1.
    assert gpos(one) == {"Phone": 1}
    # ALIGN=CENTER explicitly is still left-justified over a single column.
    assert gpos(one.replace("<lstgrp>", "<lstgrp align=center>")) == {"Phone": 1}
    # ALIGN=END right-justifies within the 12-wide column span (1 + 12 - 5 = 8).
    assert gpos(one.replace("<lstgrp>", "<lstgrp align=end>")) == {"Phone": 8}

    two = ('<lstgrp>Wk<lstcol datavar=a colwidth=5>Mon'
           '<lstcol datavar=b colwidth=5>Tue</lstgrp>')
    # Multiple columns: Wk (default) is centred over the two-column span.
    assert gpos(two) == {"Wk": 6}
    # ALIGN=START left-justifies even over multiple columns.
    assert gpos(two.replace("<lstgrp>", "<lstgrp align=start>")) == {"Wk": 1}


def test_list_column_intens_and_hilite_style_cells():
    # #233: INTENS (HIGH / LOW→normal / NON→non-display) and HILITE
    # (USCORE/BLINK/REVERSE) on a <lstcol> style its cells, like a <dtafld>.
    s = load_dtl(
        '<panel name="p"><area><lstfld>'
        '<lstcol datavar=a colwidth=6 usage=out intens=high>A'
        '<lstcol datavar=b colwidth=6 usage=out intens=low hilite=uscore>B'
        '<lstcol datavar=c colwidth=6 usage=out intens=non>C'
        '<lstcol datavar=d colwidth=6 intens=high hilite=reverse>D'
        '</lstfld></area></panel>',
        rows=[{"a": "aa", "b": "bb", "c": "cc", "d": "dd"}])
    cells = {it.text if isinstance(it, Text) else it.name: it
             for it in s.items if getattr(it, "role", None) == "cell"}
    H, N, NON = (DisplayIntensity.HIGH, DisplayIntensity.NORMAL,
                 DisplayIntensity.NON_DISPLAY)
    # Output cells carry intensity + highlight directly.
    assert cells["aa"].intensity is H
    assert cells["bb"].intensity is N and cells["bb"].highlight is Highlight.UNDERSCORE
    assert cells["cc"].intensity is NON                      # INTENS=NON → non-display
    # An input cell: INTENS/HILITE thread onto the Field.
    assert cells["d"].intensity is H and cells["d"].highlight is Highlight.REVERSE
    assert cells["d"].hidden is False
    # A plain column (no INTENS/HILITE) is unchanged: normal, no highlight.
    plain = load_dtl('<panel name="p"><area><lstfld>'
                     '<lstcol datavar=x colwidth=4 usage=out>X</lstfld></area></panel>',
                     rows=[{"x": "1"}])
    cell = next(it for it in plain.items if getattr(it, "role", None) == "cell"
                and isinstance(it, Text))
    assert cell.intensity is N and cell.highlight is None


def test_list_field_scrollvar_adds_command_line_scroll_field():
    # #239: <lstfld scrollvar=> puts a "Scroll ===>" amount field at the right of
    # the command line and shortens the command field so they don't overlap.
    s = load_dtl(
        '<panel name="p" width="76">L<area>'
        '<lstfld scrollvar=zscroll scrcaps=on scrvhelp=scrhelp>'
        '<lstcol datavar=a colwidth=8 usage=out>Item</lstfld></area>'
        '<cmdarea row="20" col="1" entwidth="60">Command ===></cmdarea></panel>',
        rows=[{"a": "one"}], ZSCROLL="page")
    scroll = next(it for it in s.items
                  if isinstance(it, Field) and it.name == "zscroll")
    assert scroll.col == 76 - 4 - 1 and scroll.length == 4     # right-aligned, 4 wide
    assert scroll.default == "PAGE"                            # SCRCAPS uppercased
    assert scroll.help == "scrhelp"                            # SCRVHELP
    assert any(isinstance(it, Text) and it.text == "Scroll ===>" for it in s.items)
    # The command field is clamped so it ends before the scroll label.
    cmd = s.command_field
    assert cmd.col + cmd.length <= scroll.col - len("Scroll ===>") - 1

    # Too little room: the scroll entry is suppressed (needs >= 8 command bytes).
    narrow = load_dtl(
        '<panel name="p" width="30">L<area>'
        '<lstfld scrollvar=zs><lstcol datavar=a colwidth=4 usage=out>I</lstfld>'
        '</area><cmdarea row="20" col="1" entwidth="20">Cmd ===></cmdarea></panel>',
        rows=[{"a": "x"}])
    assert not any(isinstance(it, Field) and it.name == "zs" for it in narrow.items)


def test_list_column_help_is_cursor_sensitive():
    # #237: HELP=panel on a <lstcol> attaches field-level help to its cells, so
    # HELP with the cursor on a cell resolves that panel — for both an output cell
    # (Text) and an input cell (Field). A column without HELP falls through.
    s = load_dtl(
        '<panel name="p" help="panelhelp"><area><lstfld>'
        '<lstcol datavar=u colwidth=8 usage=out help=userhelp>User'
        '<lstcol datavar=amt colwidth=6 help=amthelp>Amount'
        '<lstcol datavar=x colwidth=4 usage=out>Plain'
        '</lstfld></area></panel>',
        rows=[{"u": "IBMUSER", "amt": "100", "x": "z"}])
    cell = {(it.text if isinstance(it, Text) else it.name): it
            for it in s.items if getattr(it, "role", None) == "cell"}
    # help_for takes a buffer address inside the cell's data span.
    assert s.help_for(cell["IBMUSER"].data_addr + 1) == "userhelp"   # output cell
    assert s.help_for(cell["amt"].data_addr + 1) == "amthelp"        # input cell
    assert s.help_for(cell["z"].data_addr + 1) is None               # no column help


def test_list_column_position_pins_column():
    # #231: POSITION=n pins a column — n is the attribute byte before the data, so
    # the data starts at n+1. Columns on different model lines pinned to the same
    # POSITION align vertically; subsequent columns flow after the pinned one.
    s = load_dtl(
        '<panel name="p" width="60"><area><lstfld>'
        '<lstcol datavar=a colwidth=6 usage=out line=1 position=20>Top'
        '<lstcol datavar=b colwidth=6 usage=out line=2 position=20>Bot'
        '</lstfld></area></panel>', rows=[{"a": "AA", "b": "BB"}])
    at = {(it.text if isinstance(it, Text) else it.name): (it.row, it.col)
          for it in s.items if getattr(it, "role", None) in ("heading", "cell")}
    assert at["Top"] == (0, 21) and at["Bot"] == (1, 21)     # POSITION+1, stacked by line
    assert at["AA"] == (2, 21) and at["BB"] == (3, 21)       # cells aligned under

    # A pinned column shifts the flow: the next (unpinned) column starts after it.
    s2 = load_dtl(
        '<panel name="p" width="60"><area><lstfld>'
        '<lstcol datavar=a colwidth=4 usage=out position=10>A'
        '<lstcol datavar=b colwidth=4 usage=out>B'
        '</lstfld></area></panel>')
    heads = {it.text: it.col for it in s2.items
             if getattr(it, "role", None) == "heading" and it.text in ("A", "B")}
    assert heads["A"] == 11                                   # 10 + 1
    assert heads["B"] == 11 + 4 + 2                           # flows after A (+2 gutter)


def test_list_column_noendattr_tightens_gutter():
    # #232: NOENDATTR drops a column's trailing attribute byte, so the next column
    # starts one position earlier — except it is ignored for the last column on a
    # model line (which needs the trailing attribute to bound the field).
    def heads(markup):
        s = load_dtl('<panel name="p"><area><lstfld>' + markup
                     + '</lstfld></area></panel>')
        return {it.text: it.col for it in s.items
                if getattr(it, "role", None) == "heading"}

    normal = ('<lstcol datavar=a colwidth=4 usage=out>A'
              '<lstcol datavar=b colwidth=4 usage=out>B'
              '<lstcol datavar=c colwidth=4 usage=out>C')
    assert heads(normal) == {"A": 1, "B": 7, "C": 13}    # +2 gutter each

    # A and B suppress their trailing attr (+2→+1); C is last-on-line so its own
    # NOENDATTR would be ignored (here it has none).
    noend = ('<lstcol datavar=a colwidth=4 usage=out noendattr>A'
             '<lstcol datavar=b colwidth=4 usage=out noendattr>B'
             '<lstcol datavar=c colwidth=4 usage=out>C')
    assert heads(noend) == {"A": 1, "B": 6, "C": 11}     # +1 gutter after A, B

    # NOENDATTR on the LAST column is ignored — layout unchanged from normal.
    last = ('<lstcol datavar=a colwidth=4 usage=out>A'
            '<lstcol datavar=b colwidth=4 usage=out>B'
            '<lstcol datavar=c colwidth=4 usage=out noendattr>C')
    assert heads(last) == {"A": 1, "B": 7, "C": 13}


def test_list_column_display_no_is_non_display():
    # #235: DISPLAY=NO is a non-display (password-style) column — the data cell is
    # hidden, but the column keeps its position and its heading still shows.
    s = load_dtl(
        '<panel name="p"><area><lstfld>'
        '<lstcol datavar=u colwidth=8 usage=out>User'
        '<lstcol datavar=pw colwidth=8 usage=out display=no>Pass'   # hidden output
        '<lstcol datavar=s colwidth=6 display=no>Secret'            # hidden input
        '</lstfld></area></panel>',
        rows=[{"u": "IBMUSER", "pw": "SYS1", "s": "x"}])
    cells = {(it.text if isinstance(it, Text) else it.name): it
             for it in s.items if getattr(it, "role", None) == "cell"}
    assert cells["IBMUSER"].intensity is DisplayIntensity.NORMAL     # shown
    assert cells["SYS1"].intensity is DisplayIntensity.NON_DISPLAY   # hidden output
    assert cells["s"].hidden is True                                 # hidden input
    # Headings are still visible over the non-display columns.
    heads = {it.text for it in s.items if getattr(it, "role", None) == "heading"}
    assert {"User", "Pass", "Secret"} <= heads


def test_list_column_format_positions_heading_and_data():
    # #230: FORMAT positions the shorter of (heading, data) within the column
    # formatting width. It does NOT touch the cell contents (that's ALIGN).
    def hd(colwidth, heading, fmt, cell="x"):
        s = load_dtl(
            f'<panel name="p" width="40"><area><lstfld>'
            f'<lstcol datavar=a colwidth={colwidth} usage=out format={fmt}>{heading}'
            f'</lstfld></area></panel>', rows=[{"a": cell}])
        head = next(it.col for it in s.items
                    if isinstance(it, Text) and it.text == heading)
        data = next(it.col for it in s.items
                    if isinstance(it, Text) and it.text.strip() == cell)
        return head, data

    # Heading (2) shorter than colwidth (8): the heading moves within the column;
    # the data cell (fills the width) stays put at col 1.
    assert hd(8, "ID", "start") == (1, 1)
    assert hd(8, "ID", "center") == (4, 1)               # (8-2)//2 = 3 → 1+3
    assert hd(8, "ID", "end") == (7, 1)                  # 8-2 = 6 → 1+6
    # Heading (11) longer than colwidth (2): the heading fills the column (stays at
    # col 1); the data cell is centred/right-justified under it.
    assert hd(2, "Description", "start") == (1, 1)
    assert hd(2, "Description", "center") == (1, 5)      # (11-2)//2 = 4 → 1+4
    assert hd(2, "Description", "end") == (1, 10)        # 11-2 = 9 → 1+9


def test_list_column_text_description_before_and_after():
    # #229: <lstcol> TEXT renders a description beside each data cell. TEXTLOC picks
    # the side (default AFTER); the description flows in the column so the next
    # column clears it.
    s = load_dtl(
        '<panel name="p" width="50"><area><lstfld>'
        '<lstcol datavar=amt colwidth=6 usage=out text=USD>Amount'
        '<lstcol datavar=qty colwidth=4 usage=out text="(ea)" textloc=before>Qty'
        '</lstfld></area></panel>',
        rows=[{"amt": "100", "qty": "3"}])
    pos = {it.text: it.col for it in s.items
           if isinstance(it, Text) and it.row == 1}   # first data row
    assert pos["100"] == 1                              # amt cell
    assert pos["USD"] == 8                              # AFTER: past the 6-wide cell (+1)
    assert pos["(ea)"] == 13                            # BEFORE the qty cell
    assert pos["3"] == 18                               # qty cell, past its description
    # The description repeats on every model row.
    s2 = load_dtl(
        '<panel name="p" width="50"><area><lstfld>'
        '<lstcol datavar=amt colwidth=6 usage=out text=USD>Amount'
        '</lstfld></area></panel>',
        rows=[{"amt": "100"}, {"amt": "25"}])
    assert [it.row for it in s2.items if getattr(it, "text", "") == "USD"] == [1, 2]


def test_list_column_text_len_and_fmt_justify():
    # TEXTLEN reserves a formatting area; TEXTFMT justifies the text within it.
    def text_col(fmt):
        s = load_dtl(
            f'<panel name="p" width="50"><area><lstfld>'
            f'<lstcol datavar=a colwidth=5 usage=out text=hi textlen=10 textfmt={fmt}>A'
            f'</lstfld></area></panel>', rows=[{"a": "x"}])
        return next(it.col for it in s.items
                    if isinstance(it, Text) and it.text == "hi")
    # Cell at col 1 (fmt 5) → text area starts at 1+5+1 = 7, width 10 (cols 7..16).
    assert text_col("start") == 7                        # left
    assert text_col("center") == 11                      # (10-2)//2 = 4 → 7+4
    assert text_col("end") == 15                         # 10-2 = 8 → 7+8


def test_list_column_intens_non_input_cell_is_hidden():
    # INTENS=NON on an INPUT column makes the field non-display (Field.hidden),
    # so the render suppresses its data/colour like a password field.
    s = load_dtl('<panel name="p"><area><lstfld>'
                 '<lstcol datavar=pw colwidth=8 intens=non>Secret'
                 '</lstfld></area></panel>', rows=[{"pw": "hunter2"}])
    fld = next(it for it in s.items
               if isinstance(it, Field) and it.role == "cell")
    assert fld.hidden is True


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
        Text(0, 1, "Time", H), Text(0, 7, "Who", H),   # Time is usage=out → +2 gutter
        Text(1, 1, "8:00", N),  Field(row=1, col=7, length=6, name="who", default="Acme", terminator=True),
        Text(2, 1, "9:00", N),  Field(row=2, col=7, length=6, name="who", default="Globex", terminator=True),
        Text(3, 1, "*" * 31 + " BOTTOM OF DATA " + "*" * 31, H, role="heading"),
        Text(0, 64, "ROW 1 TO 2 OF 2", H, role="status"),   # scroll status, right of title row
    ]


def test_list_field_bottom_of_data_only_when_not_clipped():
    # #220: "BOTTOM OF DATA" is drawn when the end of the table is on screen; when
    # the rows are clipped by the panel depth (more data on the next page) it is
    # suppressed, so a paginated table doesn't claim a false end.
    src = ('<panel name="p" depth="8"><area><lstfld>'
           '<lstcol datavar=a colwidth=4>A</lstfld></area></panel>')
    fits = load_dtl(src, rows=[{"a": "1"}, {"a": "2"}])
    assert any("BOTTOM OF DATA" in getattr(t, "text", "") for t in fits.items)
    clipped = load_dtl(src, rows=[{"a": str(i)} for i in range(20)])  # > depth
    assert not any("BOTTOM OF DATA" in getattr(t, "text", "") for t in clipped.items)


def test_list_field_row_status_shows_and_is_suppressed_under_a_full_width_title():
    # #220: a "ROW 1 TO y OF z" scroll status renders on the title line's right for
    # a table with data. It is suppressed when that region is already occupied (a
    # full-width title rule) so it doesn't overwrite it — and it must not cause the
    # panel's content title to be retracted.
    s = load_dtl('<panel name="p" width="60">Roster<area><lstfld>'
                 '<lstcol datavar=a colwidth=4>A</lstfld></area></panel>',
                 rows=[{"a": "1"}, {"a": "2"}, {"a": "3"}])
    row0 = [t for t in s.items if getattr(t, "row", None) == 0 and hasattr(t, "text")]
    assert any(t.text == "Roster" for t in row0)              # title kept
    assert any(t.text == "ROW 1 TO 3 OF 3" for t in row0)     # status present
    # A full-width element on row 0 (a title rule) suppresses the status.
    s2 = load_dtl('<panel name="p" width="60"><area>'
                  '<info row="0" col="0" fill="-" width="59"/><lstfld>'
                  '<lstcol datavar=a colwidth=4>A</lstfld></area></panel>',
                  rows=[{"a": "1"}])
    assert not any("ROW " in getattr(t, "text", "") for t in s2.items)


def test_list_field_line_attribute_stacks_columns():
    # #222: line=2 puts a column on the second row of each model entry — and the
    # column HEADING stacks to match (A on the first heading row, B on the second),
    # so the heading block is two rows tall, then the two-row data entry below.
    s = load_dtl(
        '<panel name="p"><area>'
        '<lstfld><lstcol datavar=a colwidth=5 line=1>A'
        '<lstcol datavar=b colwidth=5 line=2>B</lstfld>'
        '</area></panel>'
    )
    H = DisplayIntensity.HIGH
    assert s.items == [
        Text(0, 1, "A", H), Text(1, 9, "B", H),        # headings stacked by line
        Field(row=2, col=1, length=5, name="a", terminator=True),
        Field(row=3, col=9, length=5, name="b", terminator=True),
    ]


def test_list_field_div_divider_after_each_model_set():
    # #221: LSTFLD DIV=NONE|BLANK|SOLID|DASH|char draws a divider as the last line
    # of each model set. Under NOGRAPHIC (a text terminal) SOLID and DASH both
    # render as a dashed rule; BLANK is a spacer row; a literal char/string is
    # replicated to the panel width with its case preserved.
    src = ('<panel name="p" width="40"><area><lstfld div=%s>'
           '<lstcol datavar=a colwidth=6 usage=out>Name'
           '<lstcol datavar=b colwidth=3 usage=out>Age</lstfld></area></panel>')
    rows = [{"a": "Pete", "b": "41"}, {"a": "Sally", "b": "39"}]

    solid = load_dtl(src % "solid", rows=rows)
    rules = [t for t in solid.items if getattr(t, "role", None) == "rule"]
    assert [r.row for r in rules] == [2, 4]                 # after each of 2 entries
    assert all(set(r.text) == {"-"} and len(r.text) == 38 for r in rules)

    # DASH renders identically to SOLID on a text terminal.
    dash = load_dtl(src % "dash", rows=rows)
    assert [t.text for t in dash.items if getattr(t, "role", None) == "rule"] \
        == [r.text for r in rules]

    # BLANK reserves a spacer row (the next entry is pushed down) but draws nothing.
    blank = load_dtl(src % "blank", rows=rows)
    assert not [t for t in blank.items if getattr(t, "role", None) == "rule"]
    assert [t.row for t in blank.items if getattr(t, "text", "") == "Sally"] == [3]

    # A literal character is replicated to the width, case preserved.
    star = load_dtl(src % '"="', rows=rows)
    star_rules = [t for t in star.items if getattr(t, "role", None) == "rule"]
    assert all(set(r.text) == {"="} for r in star_rules)

    # NONE (the default) draws no divider.
    none = load_dtl(src % "none", rows=rows)
    assert not [t for t in none.items if getattr(t, "role", None) == "rule"]


def test_list_column_outside_list_field_raises():
    with pytest.raises(DTLError):
        load_dtl('<panel name="p"><lstcol colwidth=5>X</panel>')


def test_panel_title_text_centered():
    s = load_dtl('<panel name="p" width="20">My Title<info>body</info></panel>')
    assert s.title == "My Title"
    assert s.items[0] == Text(0, (20 - len("My Title")) // 2, "My Title", DisplayIntensity.NORMAL)
    assert s.items[1] == Text(1, 1, "body", DisplayIntensity.NORMAL)   # flows below title


def test_panel_title_retracted_when_row0_is_occupied():
    # #188: a content title normally renders centered on row 0, but the bundled
    # panels draw their own title rule / action bar there. When an explicit
    # element already occupies row 0, the auto centered title is retracted so the
    # standard content form is byte-identical to the old metadata-only title=.
    s = load_dtl('<panel name="p" width="79">ISPF Settings'
                 '<info row="0" col="0">----- ISPF Settings -----</info></panel>')
    assert s.title == "ISPF Settings"                      # metadata kept
    assert s.items == [Text(0, 0, "----- ISPF Settings -----", DisplayIntensity.NORMAL)]
    assert all(it.text != "ISPF Settings" for it in s.items)   # no centered title item


def test_panel_title_kept_when_row0_is_free():
    # With nothing else on row 0, the centered content title still renders.
    s = load_dtl('<panel name="p" width="20">My Title'
                 '<info row="2" col="1">body</info></panel>')
    assert s.items[0] == Text(0, (20 - len("My Title")) // 2, "My Title",
                              DisplayIntensity.NORMAL)


def test_panel_titline_no_keeps_title_metadata_without_a_line():
    # #204: TITLINE=NO suppresses the on-screen title line — the title is kept as
    # Screen.title metadata only, and the body flows from row 0 (the freed line).
    s = load_dtl('<panel name="p" titline="no" width="20">My Title'
                 '<info>body</info></panel>')
    assert s.title == "My Title"                               # metadata kept
    assert all(getattr(it, "text", "") != "My Title" for it in s.items)  # no title line
    assert s.items == [Text(0, 1, "body", DisplayIntensity.NORMAL)]      # body at row 0


def test_title_only_panel_renders_its_title_at_eof():
    # DTL omits end tags: a panel whose only content is its title, with no <body>
    # and no </panel> (as in guide examples ex004/ex078), still renders the title.
    # It was previously lost because the title was finalised only by a following
    # child tag or an end tag, neither of which a title-only panel has.
    s = load_dtl('<panel name="widget22" width="40">Widgets')
    assert s.title == "Widgets"
    assert s.items == [Text(0, (40 - len("Widgets")) // 2, "Widgets",
                            DisplayIntensity.NORMAL)]


def test_open_content_element_is_flushed_at_eof():
    # An open content element with no end tag and no </panel> is flushed at EOF.
    s = load_dtl('<panel name="p">Title<info row="3" col="1">Trailing')
    assert [i.text for i in s.items] == ["Title", "Trailing"]


def _ascii_snapshot(screen):
    """Render a Screen's items to a plain ASCII grid (fields shown as ``_`` runs,
    one char past their attribute byte), for a readable layout snapshot. Each row
    is right-trimmed; trailing blank rows are dropped."""
    width = screen.width or 80
    rows = {}
    maxr = 0
    for it in screen.items:
        r = getattr(it, "row", None)
        if r is None:
            continue
        maxr = max(maxr, r)
        line = rows.setdefault(r, [" "] * width)
        if isinstance(it, Field):
            for k in range(it.length):
                c = it.col + 1 + k
                if 0 <= c < width:
                    line[c] = "_"
        else:
            for k, ch in enumerate(it.text):
                c = it.col + k
                if ch != "\n" and 0 <= c < width:
                    line[c] = ch
    out = ["".join(rows.get(r, [])).rstrip() for r in range(maxr + 1)]
    return "\n".join(out)


# Verbatim CHOICE-reference "Figure 1" (Library Card Registration) markup.
_CHOICE_FIGURE_SRC = (
    "<!DOCTYPE DM SYSTEM(\n"
    "  <!entity sampvar1 system>\n"
    "  <!entity sampabc system>)>\n"
    "&sampvar1;\n\n"
    "<PANEL NAME=choice1 KEYLIST=keylxmp>Library Card Registration\n"
    "<AB>\n&sampabc;\n</AB>\n"
    "<TOPINST>Type in patron's name and card number (if applicable).\n"
    "<TOPINST>Then select an action bar choice.\n"
    "<AREA>\n"
    "  <DTAFLD DATAVAR=curdate PMTWIDTH=12 ENTWIDTH=8 USAGE=out>Date\n"
    "  <DTAFLD DATAVAR=cardno PMTWIDTH=12 ENTWIDTH=7 DESWIDTH=25>Card No\n"
    "    <DTAFLDD>(A 7-digit number)\n"
    "  <DTAFLD DATAVAR=name PMTWIDTH=12 ENTWIDTH=25 DESWIDTH=25>Name\n"
    "    <DTAFLDD>(Last, First, M.I.)\n"
    "  <DTAFLD DATAVAR=address PMTWIDTH=12 ENTWIDTH=25>Address\n"
    "  <DIVIDER>\n"
    "  <REGION DIR=horiz>\n"
    "  <SELFLD NAME=cardsel PMTWIDTH=30 SELWIDTH=38>Choose\n"
    "  one of the following\n"
    "    <CHOICE CHECKVAR=card MATCH=new>New\n"
    "    <CHOICE CHECKVAR=card MATCH=renew>Renewal\n"
    "    <CHOICE CHECKVAR=card MATCH=replace>Replacement\n"
    "  </SELFLD>\n"
    "  <SELFLD TYPE=multi PMTWIDTH=30 SELWIDTH=25>Check valid branches\n"
    "    <CHOICE NAME=north HELP=nthhlp CHECKVAR=nth>North Branch\n"
    "    <CHOICE NAME=south HELP=sthhlp CHECKVAR=sth>South Branch\n"
    "    <CHOICE NAME=east HELP=esthlp CHECKVAR=est>East Branch\n"
    "    <CHOICE NAME=west HELP=wsthlp CHECKVAR=wst>West Branch\n"
    "  </SELFLD>\n"
    "  </REGION>\n"
    "</AREA>\n"
    "<CMDAREA>Enter a command\n"
    "</PANEL>\n"
)


def test_choice_reference_figure_snapshot():
    """Layout snapshot of the CHOICE-reference Figure 1 (Library Card
    Registration). Pins the auto-flowed body: <dtafld> prompt+entry rows, a
    <divider> rule, and two side-by-side <selfld>s in a horizontal <region> — a
    single-choice list (``__  1.  New``) beside a multiple-choice list
    (``_ North Branch``, mark + description, the NAME not shown).

    The panel has an <AB> action bar, so the title is centered on row 2 below the
    CUA separator rule (row 1), as in the figure.

    Honest deltas from the IBM figure (documented, not asserted):
      * The "File  Search  Help" action-bar *labels* come from the external
        ``&sampabc;`` entity, which we cannot resolve — so the bar row is blank
        (the separator rule and the title-below-it still render).
      * The runtime F-key area (``F1=Help ...``) is ISPF chrome, not in the markup.
      * A +1 left-margin column from our field-attribute-byte convention.
    """
    expected = "\n".join([
        "",                                              # blank action-bar row (no labels)
        "-" * 79,                                        # CUA separator rule
        "                           Library Card Registration",   # title on row 2
        "",
        " Type in patron's name and card number (if applicable).",
        "",                                              # blank after each TOPINST tag
        " Then select an action bar choice.",
        "",
        " Date . . . :",                                 # PMTFMT=CUA dots; USAGE=out colon
        " Card No . .   _______ (A 7-digit number)",
        " Name . . . .  _________________________ (Last, First, M.I.)",
        " Address . .   _________________________",
        " " + "-" * 78,
        " Choose one of the following  Check valid branches",
        " __  1.  New                   _ North Branch",
        "     2.  Renewal               _ South Branch",
        "     3.  Replacement           _ East Branch",
        "                               _ West Branch",
        " Enter a command  ________",
    ])
    assert _ascii_snapshot(load_dtl(_CHOICE_FIGURE_SRC)) == expected


# Verbatim PANEL-reference "Figure 1" (Dream Vacation Guide) markup.
_PANEL_FIGURE_SRC = (
    "<!DOCTYPE DM SYSTEM>\n\n"
    "<VARCLASS NAME=selcls TYPE='CHAR 2'>\n"
    "<VARLIST>\n"
    "  <VARDCL NAME=loc  VARCLASS=selcls>\n"
    "  <VARDCL NAME=mode VARCLASS=selcls>\n"
    "</VARLIST>\n\n"
    "<PANEL NAME=panel HELP=trvlhlp KEYLIST=keylxmp\n"
    "  DEPTH=22 WIDTH=60>Dream Vacation Guide\n"
    "<AB>\n"
    "  <ABC>File\n"
    "    <PDC>Add Entry\n        <ACTION RUN=add>\n"
    "    <PDC>Delete Entry\n        <ACTION RUN=delete>\n"
    "    <PDC>Update Entry\n        <ACTION RUN=update>\n"
    "    <PDC>Exit\n        <ACTION RUN=exit>\n"
    "  <ABC>Help\n"
    "    <PDC>Extended Help...\n        <ACTION RUN=exhelp>\n"
    "    <PDC>Keys Help...\n        <ACTION RUN=keyshelp>\n"
    "</AB>\n"
    "<TOPINST>Choose one of the following exotic locations and\n"
    "your preferred mode of travel, then press Enter.\n"
    "<AREA>\n"
    "  <REGION DIR=horiz>\n"
    "  <SELFLD NAME=loc PMTWIDTH=23 SELWIDTH=25>Exotic Location:\n"
    "    <CHOICE>Athens, GA\n"
    "    <CHOICE>Berlin, CT\n"
    "    <CHOICE>Cairo, IL\n"
    "    <CHOICE>Lizard Lick, NC\n"
    "    <CHOICE>Paris, TX\n"
    "    <CHOICE>Rome, NY\n"
    "    <CHOICE>Venice, FL\n"
    "  </SELFLD>\n"
    "  <DIVIDER>\n"
    "  <SELFLD NAME=mode PMTWIDTH=25 SELWIDTH=25>Travel Mode:\n"
    "    <CHOICE>Boxcar\n"
    "    <CHOICE>Hitchhike\n"
    "    <CHOICE>Mule\n"
    "  </SELFLD>\n"
    "  </REGION>\n"
    "</AREA>\n"
    "<CMDAREA>\n"
    "</PANEL>\n"
)


def test_panel_reference_figure_snapshot():
    """Layout snapshot of the PANEL-reference Figure 1 (Dream Vacation Guide).
    Pins: validated WIDTH=60/DEPTH=22, an inline <AB> action bar ("File Help",
    labels rendered — unlike the external-entity bar in the CHOICE figure) with a
    CUA separator rule beneath it and the title centered below (action bar row 0,
    rule row 1, title row 2, blank, body), a <topinst>, and two side-by-side
    single-choice <selfld>s in a horizontal <region>, each auto-numbered "N.",
    plus the <cmdarea> input field.

    Honest deltas from the IBM figure (documented, not asserted):
      * The empty <CMDAREA> renders just the input field; ISPF supplies a default
        "Command ===>" prompt we don't add.
      * The runtime F-key area (F1=Help ...) is ISPF chrome, not in the markup.
      * A +1 left-margin column from our field-attribute-byte convention.
    """
    s = load_dtl(_PANEL_FIGURE_SRC)
    assert (s.width, s.depth) == (60, 22)               # validated dimensions
    assert [c["label"] for c in s.action_bar] == ["File", "Help"]
    assert s.title == "Dream Vacation Guide"
    expected = "\n".join([
        " File   Help",
        "-" * 59,                                        # separator rule under the bar
        "                    Dream Vacation Guide",       # title centered on row 2
        "",
        " Choose one of the following exotic locations and your",
        " preferred mode of travel, then press Enter.",
        "",                                          # blank line after the TOPINST
        " Exotic Location:           Travel Mode:",
        " __  1.  Athens, GA         __  1.  Boxcar",
        "     2.  Berlin, CT             2.  Hitchhike",
        "     3.  Cairo, IL              3.  Mule",
        "     4.  Lizard Lick, NC",
        "     5.  Paris, TX",
        "     6.  Rome, NY",
        "     7.  Venice, FL",
        "   ________",
    ])
    assert _ascii_snapshot(s) == expected


# Admonition reference figures (ATTENTION/CAUTION/WARNING, NOTE/NT/NOTEL). These
# help panels omit WIDTH, so we render at the DTL default (76) — the reference
# figures were displayed narrower, so the wrap points differ but the admonition
# *format* matches. The runtime F-key area is ISPF chrome, not markup.

def test_caution_reference_figure_snapshot():
    """CAUTION-reference Figure 1: "CAUTION:" on its own line, emphasised body."""
    src = ("<!DOCTYPE DM SYSTEM>\n<HELP NAME=caution DEPTH=20>Help for DELETE Command\n"
           "<AREA>\n<INFO>\n"
           "<P>The DELETE command erases the specified file from storage.\n"
           "<P><CAUTION>Issuing the DELETE command permanently removes the file "
           "from storage. There is no possibility of recovery.</CAUTION>\n"
           "<P>You can exit from the DELETE operation by pressing F12.\n"
           "</INFO>\n</AREA>\n</HELP>")
    assert _ascii_snapshot(load_dtl(src)) == "\n".join([
        "                            Help for DELETE Command",
        " The DELETE command erases the specified file from storage.",
        " CAUTION:",
        " Issuing the DELETE command permanently removes the file from storage. There is",
        " no possibility of recovery.",
        "",                                          # blank before the closing paragraph
        " You can exit from the DELETE operation by pressing F12.",
    ])


def test_nt_reference_figure_snapshot():
    """NT-reference Figure 1: "Note:" then the body hung indented under the text.

    Delta: the nested <p> ("If the librarian ...") flows at the left margin; the
    figure hangs it under the note too (tracked separately)."""
    src = ("<!DOCTYPE DM SYSTEM>\n<HELP NAME=nt DEPTH=20>Book / Periodical Search Help\n"
           "<AREA>\n<INFO>\n"
           "<P>This entry screen allows you to locate a desired book or periodical "
           "by entering the title in the entry field.\n"
           "<NT>If the item you are trying to locate is not in stock and you would "
           "like to reserve it, please see the librarian at the front desk.\n"
           "<P>If the librarian is not there, please do not yell for help.  "
           "This is a library!\n</NT>\n</INFO>\n</AREA>\n</HELP>")
    assert _ascii_snapshot(load_dtl(src)) == "\n".join([
        "                         Book / Periodical Search Help",
        " This entry screen allows you to locate a desired book or periodical by",
        " entering the title in the entry field.",
        " Note: If the item you are trying to locate is not in stock and you would like",
        "       to reserve it, please see the librarian at the front desk.",
        "",                                          # blank before the nested paragraph
        " If the librarian is not there, please do not yell for help. This is a library!",
    ])


def test_notel_reference_figure_snapshot():
    """NOTEL-reference Figure 1: "Notes:" + a blank line + numbered items."""
    src = ("<!DOCTYPE DM SYSTEM>\n<HELP NAME=notel DEPTH=20>Book / Periodical Search Help\n"
           "<AREA>\n<INFO>\n"
           "<P>This entry screen allows you to locate a desired book or periodical "
           "by entering the title in the entry field.\n"
           "<NOTEL>\n"
           "<LI>If the item you are trying to locate is not in stock and you would "
           "like to reserve it, please see the librarian at the front desk.\n"
           "<LI>If the librarian is not there, please do not yell for help.\n"
           "<P>This is a library!\n</NOTEL>\n</INFO>\n</AREA>\n</HELP>")
    assert _ascii_snapshot(load_dtl(src)) == "\n".join([
        "                         Book / Periodical Search Help",
        " This entry screen allows you to locate a desired book or periodical by",
        " entering the title in the entry field.",
        " Notes:",
        "",
        " 1.  If the item you are trying to locate is not in stock and you would like to",
        "     reserve it, please see the librarian at the front desk.",
        " 2.  If the librarian is not there, please do not yell for help.",
        "",                                          # blank before the nested paragraph (Figure 145)
        "     This is a library!",
    ])


def test_lstfld_reference_figure_snapshot():
    """LSTFLD/LSTCOL reference Figure 1 (Subscriber List): a grouped table — the
    HEADLINE=yes group is a dashed rule around its centered heading; a single-column
    group (Phone, Approved) is left-justified over its column per the LSTGRP ALIGN
    default; column headings are not truncated to COLWIDTH; the data/model rows, then
    a "BOTTOM OF DATA" line spanning the table.

    Each column reserves the CUA attribute-byte gutter (#221): an output column
    +2 (lead+trail attr), a non-autotab input column +3 (lead+trail attr + a
    trailing blank), per the LSTCOL COLSPACE definition. Deltas from the IBM
    figure (documented): input/BOTH cells show as blank fields here (the snapshot
    renders fields as underscores); the F-key area is ISPF chrome."""
    src = ("<PANEL NAME=lstcola WIDTH=76>Subscriber List\n"
           "<TOPINST>Enter phone number and approved indicator for each person.\n"
           "<AREA>\n  <LSTFLD>\n"
           "    <LSTGRP HEADLINE=yes>Subscriber Name\n"
           "      <LSTCOL DATAVAR=xfname USAGE=out COLWIDTH=15>First Name\n"
           "      <LSTCOL DATAVAR=xlname USAGE=out COLWIDTH=15>Last Name\n"
           "      <LSTCOL DATAVAR=xmid   USAGE=out COLWIDTH=1>MI\n"
           "    </LSTGRP>\n"
           "    <LSTGRP>Phone\n"
           "      <LSTCOL DATAVAR=xphone COLWIDTH=12>Number\n"
           "    </LSTGRP>\n"
           "    <LSTGRP>Approved\n"
           "      <LSTCOL DATAVAR=xapp USAGE=in REQUIRED=yes COLWIDTH=1>(Y or N)\n"
           "    </LSTGRP>\n  </LSTFLD>\n</AREA>\n<CMDAREA>\n</PANEL>")
    rows = [{"xfname": "Pete", "xlname": "Moss", "xmid": "P"},
            {"xfname": "Sally", "xlname": "Forth", "xmid": "N"},
            {"xfname": "Melba", "xlname": "Toast", "xmid": "T"}]
    assert _ascii_snapshot(load_dtl(src, rows=rows)) == "\n".join([
        "                              Subscriber List               ROW 1 TO 3 OF 3",
        " Enter phone number and approved indicator for each person.",
        "",                                          # blank line after the TOPINST
        " --------- Subscriber Name ----------  Phone          Approved",
        " First Name       Last Name        MI  Number         (Y or N)",
        " Pete             Moss             P    ____________   _",
        " Sally            Forth            N    ____________   _",
        " Melba            Toast            T    ____________   _",
        " " + "*" * 29 + " BOTTOM OF DATA " + "*" * 29,   # table end reached
        "   ________",
    ])


def test_lstgrp_nested_groups_reference_figure_snapshot():
    """LSTGRP reference Figure 140 (Class Roster): nested <lstgrp> groups produce
    stacked heading rows (#222). "Student Name" (HEADLINE) spans Last/First/M on
    the top row; the child groups Last/First/M and Year sit on the second row; the
    directly-nested Sem 1/Sem 2 column headings fall on the column-heading row
    below (blank under Student Name / Class). "Class" and its single child "Year"
    are left-justified over the one column; "Grade" (HEADLINE) spans Sem 1/Sem 2."""
    src = ("<PANEL NAME=lstgrp WIDTH=66>Class Roster<AREA><LSTFLD>"
           "<LSTGRP HEADLINE=yes>Student Name"
           "<LSTGRP>Last<LSTCOL DATAVAR=xlname USAGE=out COLWIDTH=12></LSTGRP>"
           "<LSTGRP>First<LSTCOL DATAVAR=xfname USAGE=out COLWIDTH=12></LSTGRP>"
           "<LSTGRP>M<LSTCOL DATAVAR=xmid USAGE=out COLWIDTH=1></LSTGRP>"
           "</LSTGRP>"
           "<LSTGRP>Class<LSTGRP>Year"
           "<LSTCOL DATAVAR=xyear USAGE=out COLWIDTH=9></LSTGRP></LSTGRP>"
           "<LSTGRP HEADLINE=yes>Grade"
           "<LSTCOL DATAVAR=sem1 COLWIDTH=2>Sem 1<LSTCOL DATAVAR=sem2 COLWIDTH=2>Sem 2"
           "</LSTGRP></LSTFLD></AREA><CMDAREA></PANEL>")
    rows = [{"xlname": "Duff", "xfname": "Dean", "xmid": "T", "xyear": "Junior"},
            {"xlname": "Gillihan", "xfname": "Dana", "xmid": "L", "xyear": "Freshman"}]
    assert _ascii_snapshot(load_dtl(src, rows=rows)) == "\n".join([
        "                           Class Roster           ROW 1 TO 2 OF 2",
        " ------- Student Name --------  Class      --- Grade ---",
        " Last          First         M  Year",
        "                                           Sem 1   Sem 2",
        " Duff          Dean          T  Junior      __      __",
        " Gillihan      Dana          L  Freshman    __      __",
        " " + "*" * 24 + " BOTTOM OF DATA " + "*" * 24,
        "   ________",
    ])


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
        # A <p> after the list items gets its guide-mandated leading blank (as the
        # nested <p> does in NOTEL Figure 145), so it lands on row 11.
        Text(11, 5, "Wake up the kids and call the neighbors, they won't", N),
        Text(12, 5, "want to miss it!", N),
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
    # <dtacol pmtwidth=20>: each field's entry starts at col + pmtwidth (+1 for the
    # prompt's trailing attribute byte), regardless of caption length.
    s = load_dtl(
        '<panel name="books1">Book Title Search'
        '<area><dtacol pmtwidth="20">'
        '<dtafld entwidth="40" datavar="author">Author</dtafld>'
        '<dtafld entwidth="10" datavar="catnum">Catalog number</dtafld>'
        '</dtacol></area></panel>'
    )
    fields = [i for i in s.items if isinstance(i, Field)]
    assert [(f.col, f.length, f.name) for f in fields] == [(22, 40, "author"),
                                                           (22, 10, "catnum")]


def test_dtacol_supplies_default_entry_width():
    s = load_dtl(
        '<panel><area><dtacol pmtwidth="12" entwidth="25">'
        '<dtafld datavar="name">Name</dtafld>'          # no entwidth → inherits 25
        '</dtacol></area></panel>'
    )
    field = next(i for i in s.items if isinstance(i, Field))
    assert field.length == 25 and field.col == 14      # col 1 + pmtwidth 12 + 1


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
    assert "Date:" in texts                # output field prompt ends with a colon
    assert any(t.strip() == "07/03/26" for t in texts)         # the value shown


def test_dtafld_usage_in_stays_an_input_field():
    s = load_dtl(
        '<panel><area row="3" col="1">'
        '<dtafld datavar="x" usage="in" entwidth="8">Name</dtafld>'
        '</area></panel>'
    )
    assert [i for i in s.items if isinstance(i, Field)]         # still editable


def test_dtafld_cua_leader_dots_and_output_colon():
    # PMTFMT=CUA (default): a prompt shorter than PMTWIDTH is padded with CUA
    # leader dots; USAGE=out ends the prompt with a colon (the DTAFLD figure).
    s = load_dtl(
        '<panel><area row="3" col="1"><dtacol pmtwidth="12">'
        '<dtafld datavar="curdate" usage="out" entwidth="8">Date</dtafld>'
        '<dtafld datavar="namevar" entwidth="25">Name</dtafld>'
        '<dtafld datavar="passvar" entwidth="8">Password</dtafld>'
        '</dtacol></area></panel>'
    )
    prompts = [t.text for t in s.items if isinstance(t, Text) and t.role == "prompt"]
    assert "Date . . . :" in prompts        # output field: dots + colon
    assert "Name . . . ." in prompts        # input field: dots, no colon
    assert "Password . ." in prompts        # 8-char prompt fills to 12


def test_dtafld_pmtfmt_ispf_and_none():
    # PMTFMT=ISPF puts "===>" in the rightmost 4 bytes; NONE adds no leaders.
    ispf = load_dtl('<panel><area row="1" col="1"><dtafld datavar="x" '
                    'pmtwidth="12" pmtfmt="ispf">Name</dtafld></area></panel>')
    assert any(t.text == "Name".ljust(8) + "===>"       # rightmost 4 bytes
               for t in ispf.items if isinstance(t, Text))
    none = load_dtl('<panel><area row="1" col="1"><dtafld datavar="x" '
                    'pmtwidth="12" pmtfmt="none">Name</dtafld></area></panel>')
    assert any(t.text == "Name" for t in none.items if isinstance(t, Text))


def test_size_attributes_tolerate_star_and_list_forms():
    # Hardening sweep: size attributes the docs allow as * / ** / quoted-list were
    # parsed with a bare int() and crashed. They must now fall back gracefully
    # (INFO fill WIDTH, MSGMBR WIDTH, LSTCOL COLWIDTH, SELFLD ENTWIDTH).
    load_dtl('<panel><info row="1" col="1" fill="-" width="*"/></panel>')
    load_dtl('<msgmbr name="m" width="*"><msg msgid="X1">hi</msg></msgmbr>')
    load_dtl('<panel><selfld row="1" col="1" entwidth="2 2"><choice>A</selfld></panel>')
    # a LSTCOL with COLWIDTH=* falls back to the heading width (no crash)
    hdr = load_dtl('<panel><lstfld row="1" col="1">'
                   '<lstcol colwidth="*" datavar="x">Heading</lstcol></lstfld></panel>')
    assert any(getattr(t, "text", "") == "Heading" for t in hdr.items)


def test_dtafld_star_widths_do_not_crash():
    # PMTWIDTH=n|*|** and DESWIDTH=n|* are valid DTL; the '*'/'**' forms must not
    # raise (they were parsed with a bare int() before).
    for w in ("*", "**"):
        load_dtl(f'<panel><dtacol row=1 col=1 pmtwidth="{w}">'
                 f'<dtafld datavar=x>P</dtafld></dtacol></panel>')
    s = load_dtl('<panel><dtafld row=1 col=1 deswidth="*" datavar=x>P'
                 '<dtafldd>a long description</dtafldd></dtafld></panel>')
    assert any(getattr(t, "text", "") == "a long description" for t in s.items)


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


def test_divider_type_none_is_a_blank_spacer():
    # TYPE=NONE/BLANK draws no rule but still consumes a row (a blank divider),
    # whereas the default/SOLID divider draws a rule.
    for dtype in ("none", "blank"):
        s = load_dtl(
            f'<panel><area row="4" col="1"><info>above</info>'
            f'<divider type="{dtype}"><info>below</info></area></panel>'
        )
        texts = [i for i in s.items if isinstance(i, Text)]
        assert not any(set(t.text) == {"-"} for t in texts)          # no rule
        assert texts[-1] == Text(6, 1, "below", DisplayIntensity.NORMAL)  # row consumed


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


def test_horiz_region_flows_fields_side_by_side():
    # <region dir=horiz> lays its children left-to-right (rather than stacking
    # them) — the guide's implicit layout for a row of related fields (City /
    # State / Zip). The enclosing flow then resumes on the row *below* them.
    s = load_dtl(
        '<panel><area col="1">'
        '<dtafld datavar="name" entwidth="10">Name'
        '<region dir="horiz">'
        '  <dtafld datavar="city" entwidth="8">City'
        '  <dtafld datavar="stat" entwidth="2">State'
        '</region>'
        '<dtafld datavar="after" entwidth="4">After'
        '</area></panel>'
    )
    prompts = {it.text.strip(): it for it in s.items if isinstance(it, Text)}
    fields = {f.name: f for f in s.items if isinstance(f, Field)}
    # Name flows on row 0; City and State share the next row, side by side.
    assert prompts["Name"].row == 0
    assert prompts["City"].row == prompts["State"].row == 1
    assert prompts["State"].col > fields["city"].col   # State sits to City's right
    # Flow resumes one row below the horizontal row of columns.
    assert prompts["After"].row == 2
    assert prompts["After"].col == 1                    # back at the box column


def test_horiz_region_stacks_child_regions_and_resumes_below_tallest():
    # Two vertical <region>s inside a dir=horiz box become side-by-side columns;
    # a <divider gutter=n> between them is a vertical gutter (no rule). The flow
    # resumes below whichever column is taller.
    s = load_dtl(
        '<panel><area col="1">'
        '<region dir="horiz">'
        '  <region><info>a1</info><info>a2</info></region>'
        '  <divider gutter="6">'
        '  <region><info>b1</info><info>b2</info><info>b3</info></region>'
        '</region>'
        '<info>tail</info>'
        '</area></panel>'
    )
    by_text = {it.text: it for it in s.items if isinstance(it, Text)}
    assert by_text["a1"].row == 0 and by_text["a1"].col == 1
    assert by_text["b1"].row == 0 and by_text["b1"].col > by_text["a1"].col
    # No divider rule was drawn (gutter is spacing only).
    assert not any(isinstance(it, Text) and set(it.text) == {"-"} for it in s.items)
    # The right column is 3 rows tall (b1..b3 on rows 0..2), so the flow resumes
    # on row 3 — below the taller of the two columns.
    assert by_text["tail"].row == 3 and by_text["tail"].col == 1


def test_horiz_region_default_vert_is_unchanged():
    # Without dir=horiz a region still stacks its children vertically (default).
    s = load_dtl(
        '<panel><area col="1">'
        '<region><info>x1</info><info>x2</info></region>'
        '<info>y</info>'
        '</area></panel>'
    )
    N = DisplayIntensity.NORMAL
    assert s.items[0] == Text(0, 1, "x1", N)
    assert s.items[1] == Text(1, 1, "x2", N)
    assert s.items[2] == Text(2, 1, "y", N)


def test_selfld_choice_columns_relative_to_enclosing_box():
    # NUMCOL/NAMECOL/DESCCOL are columns *within* the selection field, so a
    # <selfld> flowed inside a box at column C lays its choices relative to C —
    # which is what lets a <selfld> work as a dir=horiz column (#161). At the base
    # column 1 the offset is 0, so panel-level selection fields are unchanged.
    N, H = DisplayIntensity.NORMAL, DisplayIntensity.HIGH
    base = load_dtl(
        '<panel><selfld row="4"><choice num="1" name="Aaa">desc</choice></selfld></panel>'
    )
    assert base.items[0] == Text(4, 1, "1 ", H)     # classic absolute columns:
    assert base.items[1] == Text(4, 4, "Aaa", N)    #   num@1, name@4, desc@21
    assert base.items[2] == Text(4, 21, "desc", N)

    shifted = load_dtl(
        '<panel><region col="30">'
        '<selfld row="4"><choice num="1" name="Aaa">desc</choice></selfld>'
        '</region></panel>'
    )
    assert shifted.items[0] == Text(4, 30, "1 ", H)   # each shifted by (30 - 1)
    assert shifted.items[1] == Text(4, 33, "Aaa", N)
    assert shifted.items[2] == Text(4, 50, "desc", N)


def test_selfld_explicit_col_shifts_choice_columns():
    # An explicit COL on the <selfld> itself is the origin the choice columns
    # offset from (previously COL was ignored and the columns were absolute).
    N, H = DisplayIntensity.NORMAL, DisplayIntensity.HIGH
    s = load_dtl(
        '<panel><selfld row="4" col="30" namecol="4" desccol="21">'
        '<choice num="1" name="Aaa">desc</choice></selfld></panel>'
    )
    assert s.items[0] == Text(4, 30, "1 ", H)
    assert s.items[1] == Text(4, 33, "Aaa", N)
    assert s.items[2] == Text(4, 50, "desc", N)


def test_selfld_as_horiz_column_shifts_right():
    # Two <selfld>s in side-by-side dir=horiz regions no longer overlap: the
    # right one's choices shift to its column instead of pinning to column 1.
    s = load_dtl(
        '<panel><area col="1"><region dir="horiz">'
        '<region><selfld row="1"><choice num="1" name="Mon">day</choice></selfld></region>'
        '<region><selfld row="1"><choice num="1" name="Nine">am</choice></selfld></region>'
        '</region></area></panel>'
    )
    left = [it for it in s.items if isinstance(it, Text) and it.text.strip() == "Mon"][0]
    right = [it for it in s.items if isinstance(it, Text) and it.text.strip() == "Nine"][0]
    assert left.col == 4                 # left column at the base namecol
    assert right.col > left.col          # right column sits to its right


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
    # <dtafld cursor> (no value) means cursor="yes"; <... numeric> likewise.
    s = load_dtl(
        '<panel>'
        '<dtafld row="6" col="1" fldcol="16" datavar="pw" entwidth="8" cursor>P</dtafld>'
        '<dtafld row="8" col="1" fldcol="16" datavar="sz" entwidth="5" numeric>S</dtafld>'
        '</panel>'
    )
    assert s.items[1].cursor is True
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


def test_xlatl_xlati_restricts_input_to_its_translations():
    # <xlatl><xlati value=internal>external>: a field of the class must be typed as
    # one of the external values; FORMAT=upper makes the match case-insensitive and
    # the <xlatl>'s own MSG names the failure.
    s = load_dtl(
        '<varclass name="monthcls" type="char 3">'
        '  <xlatl format=upper></xlatl>'
        '  <xlatl msg="ABCD003">'
        '    <xlati value="11">NOV<xlati value="12">DEC'
        '  </xlatl>'
        '</varclass>'
        '<varlist><vardcl name="month" varclass="monthcls"/></varlist>'
        '<panel><dtafld row="5" col="1" fldcol="16" datavar="month" entwidth="3">M</dtafld></panel>'
    )
    addr = s.field_addr("month")
    assert s.first_validation_error({addr: "NOV"}) is None                    # valid
    assert s.first_validation_error({addr: "dec"}) is None                    # case-insensitive
    assert s.first_validation_error({addr: "XYZ"}) == ("ABCD003", {"VALUE": "XYZ"})
    assert s.first_validation_error({addr: ""}) is None                       # empty skipped


def test_xlati_lit_external_preserves_literal_and_uses_own_message():
    # A <lit> external keeps its interior spacing; the xlatl MSG applies to the
    # xlati check independently of the class-level checkmsg.
    s = load_dtl(
        '<varclass name="cc" type="char 9" msg="CLASSMSG">'
        '  <xlatl msg="XLMSG">'
        '    <xlati value="1"><lit>V I S T A</lit><xlati value="2">CASH'
        '  </xlatl>'
        '</varclass>'
        '<varlist><vardcl name="pay" varclass="cc"/></varlist>'
        '<panel><dtafld row="5" col="1" fldcol="16" datavar="pay" entwidth="9">P</dtafld></panel>'
    )
    addr = s.field_addr("pay")
    assert s.first_validation_error({addr: "V I S T A"}) is None              # literal external
    assert s.first_validation_error({addr: "CASH"}) is None
    assert s.first_validation_error({addr: "OTHER"}) == ("XLMSG", {"VALUE": "OTHER"})  # xlatl MSG, not CLASSMSG


def test_xlatl_format_upper_is_order_independent():
    # <xlatl format=upper> makes the class case-insensitive even when it appears
    # AFTER the <xlatl> that lists the translations (the flag is applied to every
    # xlati check once the <varclass> closes).
    s = load_dtl(
        '<varclass name="CC">'
        '<xlatl msg="BADM"><xlati value="1">List<xlati value="2">Edit</xlatl>'
        '<xlatl format=upper></xlatl>'
        '</varclass>'
        '<varlist><vardcl name="cmd" varclass="CC"/></varlist>'
        '<panel><dtafld row="2" col="2" fldcol="20" datavar="cmd" entwidth="6">C</dtafld></panel>'
    )
    addr = s.field_addr("cmd")
    assert s.first_validation_error({addr: "list"}) is None          # case-insensitive
    assert s.first_validation_error({addr: "EDIT"}) is None
    assert s.first_validation_error({addr: "nope"}) == ("BADM", {"VALUE": "nope"})


def test_render_drops_items_past_the_panel_depth():
    # Flowed content that overruns the panel depth is dropped, not wrapped onto row
    # 0: the render buffer is depth*width and 3270 addressing wraps, so a row >=
    # depth would otherwise corrupt the top of the screen.
    from screen import Screen, _display
    s = Screen(width=30, depth=6)
    s.add(Text(0, 0, "TOPLINE"))
    s.add(Text(6, 0, "OFFPANEL"))            # row 6 is off a depth-6 panel (0-5)
    out = s.render()
    assert _display("TOPLINE") in out
    assert _display("OFFPANEL") not in out   # dropped, not wrapped to row 0


def test_checki_unsupported_type_is_still_lenient():
    # A type we don't enforce yet (e.g. picture) still loads without failing the
    # panel and adds no validation — leniency preserved for the unimplemented set.
    s, addr = _check_panel('<checki type="picture">AAA</checki>')
    assert s.validations.get("F", {}).get("checks", []) == []
    assert s.first_validation_error({addr: "anything!"}) is None   # no check enforced


def test_required_field_rejects_empty_input():
    # IBM REQUIRED=YES: the field must be non-empty on submit; MSG names the error.
    s = load_dtl(
        '<panel>'
        '<dtafld row="6" col="1" fldcol="16" datavar="name" entwidth="8"'
        ' required="yes" msg="ORDB000">Name</dtafld>'
        '</panel>'
    )
    addr = s.field_addr("name")
    assert s.first_validation_error({addr: ""}) == ("ORDB000", {})    # blank rejected
    assert s.first_validation_error({addr: "  "}) == ("ORDB000", {})  # whitespace = blank
    assert s.first_validation_error({addr: "SMITH"}) is None          # supplied -> ok


def test_required_without_msg_uses_default_message():
    # REQUIRED (minimized) with no field/class MSG falls back to a system stand-in.
    s = load_dtl(
        '<panel>'
        '<dtafld row="6" col="1" fldcol="16" datavar="pw" entwidth="8"'
        ' required display="no">Password</dtafld>'
        '</panel>'
    )
    addr = s.field_addr("pw")
    assert s.first_validation_error({addr: ""}) == ("Enter required field", {})


def test_required_combines_with_varclass_checks():
    # A field that is both REQUIRED and range-checked: blank -> the class MSG (no
    # field MSG given), out-of-range -> the same MSG, a valid value -> ok.
    s = load_dtl(
        '<panel>'
        '<varclass name="SZ" type="numeric">'
        '  <checkl msg="M001"><checki type="range">0 100</checki></checkl>'
        '</varclass>'
        '<varlist><vardcl name="sz" varclass="SZ"/></varlist>'
        '<dtafld row="8" col="1" fldcol="16" datavar="sz" entwidth="5" required="yes">Size</dtafld>'
        '</panel>'
    )
    addr = s.field_addr("sz")
    assert s.first_validation_error({addr: ""})[0] == "M001"      # required -> class MSG
    assert s.first_validation_error({addr: "999"})[0] == "M001"   # range still enforced
    assert s.first_validation_error({addr: "50"}) is None         # valid


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
