"""DTL parser tests.

The panels expressed in DTL markup (``panels/*.dtl``) parse to Screens that
render to the 3270 wire format. The logon panel — the most intricate, a
two-column auto-flow form — is pinned to a committed golden byte snapshot
(``panels/logon.golden``) so any accidental change to its render is caught.
"""
import pathlib

import pytest

from dtl import load_dtl, load_panel, load_messages, load_message_member, DTLError
from screen import (Screen, Text, Field, DisplayIntensity, Color, Highlight, SA,
                    Outline, GraphicText, Line)
from server import to_ebcdic


def _logon_golden() -> bytes:
    """The committed byte-for-byte snapshot of the logon panel's render — the
    regression anchor for the auto-flow two-column layout. Regenerate with
    ``load_panel("logon").render()`` written to panels/logon.golden when an
    intentional change to logon.dtl or the layout engine alters it."""
    return pathlib.Path(__file__).parent.joinpath("panels", "logon.golden").read_bytes()


# ── golden: the logon panel render is pinned ─────────────────────────────────

def test_logon_dtl_matches_golden():
    assert load_panel("logon").render() == _logon_golden()


def test_ispf_dtl_renders_the_menu():
    # The ISPF Primary Option Menu in standard auto-flow DTL (#186): a
    # <selfld type=menu> with SELCHAR option values (0, the 8 gap, X), the
    # keyword+description folded into the padded choice text, and the two-column
    # User ID/Time footer as a <region dir=horiz> of two stacked <region>s.
    s = load_panel("ispf", ZUSER="IBMUSER ", ZTIME="13:45")
    assert _ascii_snapshot(s) == "\n".join([
        "                            ISPF Primary Option Menu",
        "",
        " Option ===>  ________",
        "",
        " 0  Settings         Terminal and user parameters",
        " 1  View             Display source data or listings",
        " 2  Edit             Create or change source data",
        " 3  Utilities        Perform utility functions",
        " 4  Foreground       Interactive language processing",
        " 5  Batch            Submit job for language processing",
        " 6  Command          Enter TSO or Workstation commands",
        " 7  Dialog Test      Perform dialog testing",
        " 9  IBM Products     IBM program development products",
        " 10 SCLM             SW Configuration Library Manager",
        " 11 Workplace        ISPF Object/Action Workplace",
        " 12 z/OS System      z/OS system programmer applications",
        " 13 z/OS User        z/OS user applications",
        "",
        " X  Exit             Terminate ISPF using log/list defaults",
        " Enter X or PF3 to terminate ISPF.",
        " User ID . . : IBMUSER  Time. . . . : 13:45",
        " System ID . : SY1      ISPF Ver. . : 7.1.0",
        " " + "-" * 78,
    ])


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


def test_directive_blocks_render_nothing_even_with_nested_markup():
    # #119: <comment>/<copyr>/<compopt>/<source> are non-rendering — their content,
    # INCLUDING nested markup, must not leak as visible text.
    def texts(src):
        return [it.text for it in load_dtl(src).items if isinstance(it, Text)]

    assert texts("<panel name=p><area><p>Visible"
                 "<comment><p>HIDDEN</p><divider></comment>"
                 "<p>After</area></panel>") == ["Visible", "After"]
    # <copyr>/<compopt> are commonly coded WITHOUT an end tag (before the panel);
    # the <panel> ends the block — the directive text/markup must not render.
    assert texts("<copyr>Copyright 2026<copyr>All rights reserved"
                 "<panel name=p><area><p>Body</area></panel>") == ["Body"]
    assert texts("<compopt noprep nographic>"
                 "<panel name=p><area><p>Body</area></panel>") == ["Body"]
    # <generate> (a build-time panel/message generation directive) is likewise
    # non-rendering — its body, including nested markup, must not leak.
    assert texts("<panel name=p><area><p>Visible"
                 "<generate model=m><p>GENERATED</p></generate>"
                 "<p>After</area></panel>") == ["Visible", "After"]
    # Self-closing directives (<generate/>, <comment/>) open no content block, so
    # the following markup must still render (not be swallowed).
    assert texts("<panel name=p><area><generate/><p>Body</area></panel>") == ["Body"]
    assert texts("<panel name=p><area><comment/><p>Body</area></panel>") == ["Body"]
    # <source> still renders nothing and its ZSEL text still routes.
    s = load_dtl("<panel name=p><area><source type=proc>"
                 "&ZSEL = TRANS(&ZCMD 1,'PGM(view)')<p>SRCLEAK"
                 "</source><p>Body</area></panel>")
    assert [it.text for it in s.items if isinstance(it, Text)] == ["Body"]
    assert s.selection_targets == {"1": "PGM(view)"}


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


def test_ispf_dtl_substitutes_userid_and_time():
    # &ZUSER (padded to 8) and &ZTIME are substituted into the footer at load time.
    s = load_panel("ispf", ZUSER="TESTUSER", ZTIME="09:02")
    footer = [it.text for it in s.items if isinstance(it, Text)]
    assert "User ID . . : TESTUSER" in footer
    assert "Time. . . . : 09:02" in footer


# ── field names survive the round trip ───────────────────────────────────────

def test_logon_dtl_field_addresses():
    s = load_panel("logon")
    assert s.field_addr("userid") == 4 * 80 + 16
    assert s.field_addr("password") == 5 * 80 + 16
    assert s.field_addr("command") == 11 * 80 + 16


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
    s = load_dtl('<panel><info>hello</info></panel>')
    assert s.items == [Text(0, 1, "hello", DisplayIntensity.NORMAL)]


def test_non_cp037_text_renders_without_crashing():
    # #150: a character the code page can't encode must degrade to the substitute
    # (?), not raise UnicodeEncodeError and take down the whole render/session.
    s = load_dtl('<panel><info>Cost 5€ (— x)</info></panel>')
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
        '<topinst>Enter parameters:</topinst>'
        '<pnlinst>Press ENTER</pnlinst>'
        '<botinst>PF3=Exit</botinst>'
        '</panel>'
    )
    assert s.items[0] == Text(0, 1, "Enter parameters:", DisplayIntensity.NORMAL)
    assert s.items[1] == Text(2, 1, "Press ENTER", DisplayIntensity.NORMAL)
    assert s.items[2] == Text(22, 1, "PF3=Exit", DisplayIntensity.NORMAL)


def test_pnlinst_was_previously_dropped():
    # Regression for the dispatch bug: the parser routed on a nonexistent "paninst"
    # tag, so the real IBM <pnlinst> silently rendered nothing.
    s = load_dtl('<panel><pnlinst>Hi</pnlinst></panel>')
    assert s.items == [Text(0, 1, "Hi", DisplayIntensity.NORMAL)]


def test_instruction_tags_flow_in_area():
    s = load_dtl(
        '<panel><area>'
        '<topinst>line one</topinst><pnlinst>line two</pnlinst>'
        '</area></panel>'
    )
    # A blank line follows a TOPINST and precedes a PNLINST; the two coincide into a
    # single blank between them (row 4), so the PNLINST lands on row 5.
    assert s.items[0] == Text(0, 1, "line one", DisplayIntensity.NORMAL)
    assert s.items[1] == Text(2, 1, "line two", DisplayIntensity.NORMAL)


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


def test_logon_instruction_tags_render_their_text():
    # The logon panel uses <topinst>/<pnlinst> for some lines; their text renders.
    texts = [t.text for t in load_panel("logon").items if isinstance(t, Text)]
    assert "Enter LOGON parameters below:" in texts     # <topinst>
    assert "RACF LOGON parameters:" in texts            # <topinst>
    assert "Press ENTER to logon to TSO/E" in texts     # <pnlinst>


# ── inline <hp> (highlighted phrase) ─────────────────────────────────────────

def _hp_line():
    return load_dtl(
        '<panel><info>'
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
    plain = bytearray(); Text(it.row, it.col, it.text).render(plain, color=False)
    assert bytes(rich) == bytes(plain)
    assert SA not in bytes(rich)                      # no Set Attribute on mono


def test_hp_colour_emits_set_attribute():
    # On a colour terminal the phrase is emphasised in place via SA runs (#110).
    buf = bytearray(); _hp_line().items[0].render(buf, color=True)
    assert SA in bytes(buf)


def test_hp_highlight_via_type_attribute():
    # <hp type=...> maps to a highlight (DTL spells hp emphasis as TYPE).
    s = load_dtl(
        '<panel><info>see <hp type="uscore">HERE</hp> now</info></panel>'
    )
    assert s.items[0].runs == [
        ("see ", None, None),
        ("HERE", None, Highlight.UNDERSCORE),
        (" now", None, None),
    ]


@pytest.mark.parametrize("typ,colour", [
    ("et", Color.TURQUOISE), ("ch", Color.BLUE), ("ct", Color.YELLOW),
    ("fp", Color.GREEN), ("lef", Color.TURQUOISE), ("li", Color.WHITE),
    ("nt", Color.GREEN), ("pt", Color.BLUE), ("sac", Color.WHITE),
    ("wasl", Color.BLUE), ("wt", Color.RED),
])
def test_hp_type_names_a_cua_colour(typ, colour):
    # #218: an <hp TYPE=cua-type> paints the phrase in that CUA type's standard
    # z/OS colour (ISPF Dialog Developer's Guide Table 11), not the default role.
    s = load_dtl(f'<panel><info>see <hp type="{typ}">HERE</hp> now</info></panel>')
    assert s.items[0].runs == [
        ("see ", None, None),
        ("HERE", colour, None),
        (" now", None, None),
    ]


def test_hp_type_text_is_not_a_cua_colour():
    # TYPE=TEXT is the escape hatch (non-CUA): it names no colour, so the phrase
    # keeps the default role colour unless an explicit COLOR/HILITE is given.
    s = load_dtl('<panel><info>see <hp type="text">HERE</hp> now</info></panel>')
    # No colour/highlight/intensity → the phrase carries no emphasis, so the line
    # collapses back to a single plain Text (no rich runs).
    assert s.items[0].text == "see HERE now"
    assert s.items[0].runs is None and s.items[0].color is None


def test_hp_explicit_colour_overrides_cua_type():
    # A CUA TYPE and an explicit COLOR are mutually exclusive in valid DTL; if both
    # appear we honour COLOR (it wins over the type's default colour).
    s = load_dtl('<panel><info>see <hp type="et" color="red">HERE</hp> now'
                 '</info></panel>')
    assert s.items[0].runs[1] == ("HERE", Color.RED, None)


def test_hp_type_colour_mono_is_byte_identical():
    # Colour-only: the CUA-type colour rides an SA order a mono terminal ignores,
    # so an <hp TYPE=cua-type> renders byte-for-byte like the plain text on mono.
    typed = load_dtl('<panel><info>see <hp type="wt">HERE</hp> now</info></panel>')
    plain = load_dtl('<panel><info>see HERE now</info></panel>')
    assert typed.render(color=False) == plain.render(color=False)


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


def test_info_hp_emphasis():
    # A whole-line <hp> is CUA emphasis: high intensity (mono) + white (colour).
    s = load_dtl(
        '<panel>'
        '<info><hp>hi</hp></info>'
        '</panel>'
    )
    assert s.items[0] == Text(0, 1, "hi", DisplayIntensity.HIGH, role="emphasis")


def test_hp_intens_splits_line_into_fields():
    # #212: an <hp intens=HIGH> phrase carries a display INTENSITY, which on 3270
    # lives in the BASIC field-attribute byte (set only at an SF) — there is no
    # extended-intensity SA order. So the line must SPLIT into a separate field per
    # intensity run rather than an SA run inside one field. The inter-phrase space
    # is consumed as the next field's attribute byte (a blank cell), so the columns
    # match the single-field layout: "text" at col 1, the SF for "LOUD" lands on
    # the space at col 5 (text at col 6), and "more"'s SF on the space at col 10.
    s = load_dtl('<panel><info>text <hp intens=high>LOUD</hp> more</info></panel>')
    N, H = DisplayIntensity.NORMAL, DisplayIntensity.HIGH
    assert s.items[0] == Text(0, 1, "text", N, role="text")
    assert s.items[1] == Text(0, 6, "LOUD", H, role="text")     # intensified field
    assert s.items[2] == Text(0, 11, "more", N, role="text")


def test_hp_intens_field_attribute_is_intensified():
    # The split phrase's field really carries the intensified BASIC field attribute
    # (0x68 = protected + high intensity), even on a mono terminal — that is the
    # whole point (an SA run could not have done it).
    s = load_dtl('<panel><info>text <hp intens=high>LOUD</hp> more</info></panel>')
    loud = s.items[1]
    buf = bytearray(); loud.render(buf, color=False)
    assert loud.intensity is DisplayIntensity.HIGH
    assert 0x68 in bytes(buf)                     # SF + intensified field attribute


def test_hp_intens_non_is_non_display():
    # INTENS=NON hides the phrase (non-display field), while HIGH→HIGH and there is
    # no sub-normal 3270 level so LOW would fold to NORMAL (no split).
    s = load_dtl('<panel><info>user <hp intens=non>SECRET</hp> ok</info></panel>')
    secret = s.items[1]
    assert secret.text == "SECRET"
    assert secret.intensity is DisplayIntensity.NON_DISPLAY


def test_hp_intens_keeps_colour_on_the_split_field():
    # An <hp> with BOTH intensity and colour: the field splits (for the intensity)
    # and the phrase's field still carries its colour via an SA run (Text.rich).
    s = load_dtl('<panel><info>a <hp intens=high color=red>B</hp> c</info></panel>')
    b = s.items[1]
    assert b.intensity is DisplayIntensity.HIGH
    assert b.runs == [("B", Color.RED, None)]


def test_hp_colour_only_still_one_rich_field_unchanged():
    # Regression guard: an <hp> with NO intens (the common colour/highlight case)
    # must remain ONE Text.rich field — the intensity path must not touch it.
    s = load_dtl('<panel><info>text <hp color=red>RED</hp> more</info></panel>')
    assert len(s.items) == 1
    assert s.items[0].text == "text RED more"
    assert s.items[0].runs == [
        ("text ", None, None), ("RED", Color.RED, None), (" more", None, None),
    ]


def test_hp_intens_survives_word_wrap():
    # The split is applied per wrapped line: a multi-word intensified phrase keeps a
    # single field across its interior space (not spuriously split), and each line's
    # fields align to the plain wrap. "very loud" stays one HIGH field on row 1.
    s = load_dtl('<panel name=p width=20>'
                 '<p>please read the <hp intens=high>very loud</hp> notice now</p>'
                 '</panel>')
    N, H = DisplayIntensity.NORMAL, DisplayIntensity.HIGH
    got = [(it.row, it.col, it.text, it.intensity)
           for it in s.items if isinstance(it, Text)]
    assert got == [
        (0, 1, "please read the", N),
        (1, 1, "very loud", H),
        (1, 11, "notice", N),
        (2, 1, "now", N),
    ]


def test_hp_intense_var_resolves_intensity():
    # INTENSE=%var reads the intensity from a dialog variable (like COLOR=%var).
    s = load_dtl('<panel><info>x <hp intense="%emph">Y</hp> z</info></panel>',
                 emph="high")
    assert s.items[1] == Text(0, 3, "Y", DisplayIntensity.HIGH, role="text")


def test_dtafld_emits_prompt_then_field():
    s = load_dtl(
        '<panel><dtafld datavar="userid" '
        'entwidth="8" cursor="yes">Userid ===></dtafld></panel>'
    )
    assert s.items[0] == Text(0, 1, "Userid ===>", DisplayIntensity.NORMAL)
    fld = s.items[1]
    assert isinstance(fld, Field)
    # entry flows one column past the 11-char prompt: 1 + 11 + 1 = 13
    assert (fld.row, fld.col, fld.length, fld.name, fld.cursor) == (0, 13, 8, "userid", True)


def test_dtafld_display_no_numeric_and_init():
    # IBM's DISPLAY=NO makes a non-display field (e.g. a password); a field with
    # no DISPLAY= is shown. INIT= sets the field's initial value.
    s = load_dtl(
        '<panel>'
        '<dtafld datavar="pw" entwidth="8" display="no">P</dtafld>'
        '<dtafld datavar="sz" entwidth="5" numeric="yes" init="00150">S</dtafld>'
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
        '<dtafld datavar="a" entwidth="8" init="IKJACCNT">A</dtafld>'
        '<dtafld datavar="b" entwidth="5" default="99999">B</dtafld>'
        '</panel>'
    )
    assert s.items[1].default == "IKJACCNT"
    assert s.items[3].default == ""


def test_dtafld_prompt_from_dtafldd_child():
    # Authentic DTL: the prompt is the text of a nested <dtafldd>.
    s = load_dtl(
        '<panel><dtafld datavar="userid" entwidth="8">'
        '<dtafldd>Userid ===></dtafldd></dtafld></panel>'
    )
    assert s.items[0] == Text(0, 1, "Userid ===>", DisplayIntensity.NORMAL)
    assert isinstance(s.items[1], Field)
    assert (s.items[1].col, s.items[1].name) == (13, "userid")   # flows past the prompt


def test_dtafld_dtafldd_equivalent_to_text_shorthand():
    # The <dtafldd> child and the inline-text shorthand render identically.
    inline = load_dtl(
        '<panel><dtafld datavar="u" entwidth="8">'
        'Userid ===></dtafld></panel>'
    )
    nested = load_dtl(
        '<panel>\n'
        '  <dtafld datavar="u" entwidth="8">\n'
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
        '<dtafld datavar="author" entwidth="20">Author'
        '  <dtafldd>Last name, First name, M.I.'
        '</dtafld></panel>'
    )
    texts = [it for it in s.items if isinstance(it, Text)]
    prompt = [t for t in texts if t.col == 1]
    assert prompt and prompt[0].text.strip() == "Author" and prompt[0].role == "prompt"
    # entry flows past the 8-char "Author  " prompt to col 10; the description
    # sits past the entry's data run + terminator attr: 10 + 20 + 2 = 32.
    desc = [t for t in texts if t.col == 32]
    assert desc and desc[0].text == "Last name, First name, M.I."


def test_dtafld_deswidth_truncates_the_description():
    s = load_dtl(
        '<panel>'
        '<dtafld datavar="x" entwidth="5" deswidth="10">P'
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
        '<dtafld datavar="u" entwidth="8">'
        '  <dtafldd>Userid ===></dtafld></panel>'
    )
    texts = [it for it in s.items if isinstance(it, Text)]
    assert len(texts) == 1 and texts[0] == Text(0, 1, "Userid ===>", DisplayIntensity.NORMAL, role="prompt")


def test_dtafld_mdt_defaults_true():
    s = load_dtl('<panel><dtafld datavar="x" entwidth="4">L</dtafld></panel>')
    assert s.items[1].mdt is True


def test_selfld_lays_out_choices_on_incrementing_rows():
    s = load_dtl(
        '<panel><selfld>'
        '<choice selchar="0" name="  A">  desc-a</choice>'
        '<choice selchar="10" name="  B">  desc-b</choice>'
        '</selfld></panel>'
    )
    # choice 0 → row 0, choice 1 → row 1; the number is left-justified to width 2
    assert s.items[0] == Text(0, 1, "0 ", DisplayIntensity.HIGH)
    assert s.items[1] == Text(0, 4, "  A", DisplayIntensity.NORMAL)
    assert s.items[2] == Text(0, 21, "  desc-a", DisplayIntensity.NORMAL)
    assert s.items[3] == Text(1, 1, "10", DisplayIntensity.HIGH)


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


def test_selfld_selchar_sets_the_value_with_keyword_columns():
    # SELCHAR sets the displayed selection value; a named choice lays its keyword
    # and description out in the keyword/description columns.
    s = load_dtl(
        '<panel><selfld>'
        '<choice selchar="7" name="  A">  desc-a</choice></selfld></panel>'
    )
    assert s.items[0] == Text(0, 1, "7 ", DisplayIntensity.HIGH)
    assert s.items[2] == Text(0, 21, "  desc-a", DisplayIntensity.NORMAL)


def test_selfld_selchar_overrides_the_auto_number():
    # #128: SELCHAR is the standard way to set a choice's selection value in place
    # of the auto-assigned number — a gap (option 8 in a 1,2,8 menu) or a letter (X).
    # The choices before it still auto-number; SELCHAR drives both display and the
    # value that selects the choice (Screen.selections).
    s = load_dtl(
        '<panel name="p"><selfld type="menu">'
        '<choice>Library</choice>'
        '<choice>Data Set</choice>'
        "<choice selchar='8'>Outlist</choice>"
        "<choice selchar='X'>Exit</choice>"
        '</selfld></panel>'
    )
    shown = [it.text.strip() for it in s.items
             if isinstance(it, Text) and getattr(it, "role", None) == "num"]
    assert shown == ["1", "2", "8", "X"]              # auto 1,2 then SELCHAR 8,X
    assert set(s.selections) == {"1", "2", "8", "X"}  # each is selectable by its value


def test_selfld_fchoice_sets_the_first_choice_number():
    # #128: FCHOICE is the number of the first auto-numbered choice (default 1);
    # FCHOICE=0 numbers the choices 0..n-1 (as the ISPF primary menu does, where
    # option 0 = Settings), and each is selectable by its number.
    s = load_dtl('<panel name="p"><selfld type="menu" fchoice="0">'
                 '<choice>Alpha<choice>Beta<choice>Gamma</selfld></panel>')
    shown = [it.text.strip() for it in s.items
             if isinstance(it, Text) and getattr(it, "role", None) == "num"]
    assert shown == ["0", "1", "2"]
    assert s.selections == {"0": "Alpha", "1": "Beta", "2": "Gamma"}
    # default (no FCHOICE) still numbers from 1
    d = load_dtl('<panel name="p"><selfld type="menu">'
                 '<choice>A<choice>B</selfld></panel>')
    assert [it.text.strip() for it in d.items
            if isinstance(it, Text) and getattr(it, "role", None) == "num"] == ["1", "2"]


def test_selfld_type_multi_renders_a_mark_field_per_choice():
    # TYPE=MULTI is a multiple-selection field: each choice gets its own 1-char
    # input field to mark (in place of a number), so several can be selected.
    s = load_dtl(
        '<panel><selfld name="off" type="multi">'
        '<choice name="pat" match="P">Patent</choice>'
        '<choice name="def" match="D">Defamation</choice>'
        '</selfld></panel>'
    )
    m0, m1 = s.items[0], s.items[2]
    assert isinstance(m0, Field) and m0.row == 0 and m0.col == 1 and m0.length == 1
    assert isinstance(m1, Field) and m1.row == 1 and m1.col == 1
    # The choice NAME is the field identifier (read the mark back), not display
    # text: a multi row is just the mark + description, the description hugging
    # the mark (auto-layout keyword column).
    assert s.items[1] == Text(0, 4, "Patent", DisplayIntensity.NORMAL)
    assert not any(getattr(it, "text", None) == "pat" for it in s.items)
    # No numbered Text is emitted for a multi-select choice.
    assert not any(isinstance(it, Text) and it.role == "num" for it in s.items)


def test_selfld_type_multi_records_and_reads_selected_values():
    s = load_dtl(
        '<panel><selfld name="off" type="multi">'
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
        '<panel><selfld name="off" type="multi">'
        '<choice name="ok" match="A">Available</choice>'
        '<choice name="no" match="B" unavail>Unavailable</choice>'
        '</selfld></panel>'
    )
    marks = [it for it in s.items if isinstance(it, Field)]
    assert len(marks) == 1 and marks[0].row == 0       # only the available choice
    assert [sf["value"] for sf in s.selection_fields] == ["A"]


def test_selfld_type_single_is_unchanged():
    # The default (SINGLE) keeps the numbered layout — no mark fields.
    s = load_dtl(
        '<panel><selfld><choice selchar="1" name="A">desc</choice></selfld></panel>'
    )
    assert not any(isinstance(it, Field) for it in s.items)
    assert s.items[0] == Text(0, 1, "1 ", DisplayIntensity.HIGH)
    assert s.selection_fields == []
    assert s.selection_rows == {0: "1"}


def test_choice_hide_removes_it_when_variable_true():
    # HIDE=var removes the choice when the variable is true; the choices below it
    # move up and it is not selectable. HIDEX=var is the inverse (hide when false).
    N, H = DisplayIntensity.NORMAL, DisplayIntensity.HIGH
    src = (
        '<panel><selfld>'
        '<choice selchar="1" name="A" match="A" hide="vh">Alpha</choice>'
        '<choice selchar="2" name="B" match="B">Beta</choice>'
        '<choice selchar="3" name="C" match="C" hidex="vs">Gamma</choice>'
        '</selfld></panel>'
    )
    # vh true → A hidden; vs false → C hidden. Only B remains, at the top row.
    s = load_dtl(src, vh="1", vs="0")
    assert s.items[0] == Text(0, 1, "2 ", H)
    assert s.items[1] == Text(0, 4, "B", N)
    assert s.selections == {"B": "B"}                  # A and C not selectable
    assert s.selection_rows == {0: "B"}

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
        '<panel><selfld>'
        '<choice selchar="1" name="Open">Open'
        '<choice selchar="7" name="Secret" hide="secret">Secret op</choice>'
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
        '<panel><selfld><choice selchar="1" name="A" hide>Alpha</choice>'
        '<choice selchar="2" name="B">Beta</choice></selfld></panel>'
    )
    assert [it.text for it in s.items if it.col == 4] == ["B"]


def test_selfld_prompt_renders_above_list_by_default():
    # The text between <selfld ...> and the first <choice> is the field prompt.
    # PMTLOC defaults to ABOVE: the caption sits on the line above the choices,
    # which then flow below it.
    N, H = DisplayIntensity.NORMAL, DisplayIntensity.HIGH
    s = load_dtl(
        '<panel><selfld name="day" selwidth="20">Weekdays:'
        '<choice selchar="1" name="Mon">day1</choice>'
        '<choice selchar="2" name="Tue">day2</choice>'
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
        '<panel><selfld name="cs" pmtwidth="11" pmtloc="before">'
        'Choose one of the following'
        '<choice selchar="1" name="Civ">Civil</choice></selfld></panel>'
    )
    # caption wrapped to <= 11 columns, each on its own row from the top
    assert s.items[0] == Text(0, 1, "Choose one", N)
    assert s.items[1] == Text(1, 1, "of the", N)
    assert s.items[2] == Text(2, 1, "following", N)
    # first choice on the top row, its columns shifted right of the 11-col prompt
    assert s.items[3] == Text(0, 12, "1 ", H)         # number col 1 -> 1 + 11
    assert s.items[4] == Text(0, 15, "Civ", N)        # keyword col 4 -> 4 + 11


def test_selfld_empty_prompt_renders_nothing():
    # The bundled numbered menus have only whitespace between <selfld> and the
    # first <choice> — that must render nothing so they stay byte-identical.
    s = load_dtl(
        '<panel><selfld>\n  '
        '<choice selchar="1" name="A">desc</choice></selfld></panel>'
    )
    assert s.items[0] == Text(0, 1, "1 ", DisplayIntensity.HIGH)   # no prompt item
    assert s.items[1] == Text(0, 4, "A", DisplayIntensity.NORMAL)


def test_choice_records_selection_rows_for_point_and_shoot():
    # Each choice also records the row it renders on, so the cursor can select
    # it (point-and-shoot). selection_at(cursor) resolves a cursor address.
    s = load_dtl(
        '<panel><selfld>'
        '<choice selchar="0" name="A">  desc-a</choice>'
        '<choice selchar="3" name="B">  desc-b</choice>'
        '</selfld></panel>'
    )
    assert s.selection_rows == {0: "0", 1: "3"}
    assert s.selection_at(0 * 80 + 10) == "0"   # cursor anywhere on the choice row
    assert s.selection_at(1 * 80 + 0) == "3"
    assert s.selection_at(9 * 80 + 1) is None    # not on a choice row
    assert s.selection_at(None) is None


def test_ispf_menu_selection_rows_map_options():
    s = load_panel("ispf", ZUSER="IBMUSER ", ZTIME="13:45")
    # cursor on the Utilities line selects option 3; the Exit line selects X
    assert s.selection_at(7 * 80 + 5) == "3"
    assert s.selection_at(18 * 80 + 5) == "X"


def test_choice_match_defaults_to_the_number_and_records_selections():
    s = load_dtl(
        '<panel><selfld>'
        '<choice selchar="0" name="Settings">  desc</choice>'
        '<choice selchar="X" name="Exit">  bye</choice>'
        '</selfld></panel>'
    )
    assert s.selections == {"0": "Settings", "X": "Exit"}


def test_choice_explicit_match_overrides_the_number():
    s = load_dtl(
        '<panel><selfld>'
        '<choice selchar="1" name="View" match="V">  desc</choice>'
        '</selfld></panel>'
    )
    assert s.selections == {"V": "View"}        # MATCH wins over the number


def test_choice_checkvar_lands_cursor_on_the_current_choice():
    # <choice checkvar=var match=val>: when the variable equals a choice's MATCH,
    # that choice is current — the cursor is placed on it.
    s = load_dtl(
        '<panel><selfld>'
        '<choice selchar="1" name="New" checkvar="card" match="NEW">create'
        '<choice selchar="2" name="Old" checkvar="card" match="OLD">existing'
        '</selfld></panel>',
        CARD="OLD",
    )
    assert s.cursor_at == (1, 4)                 # second choice's row, keyword column
    assert s.selections == {"NEW": "New", "OLD": "Old"}


def test_choice_unavail_is_dimmed_and_unselectable():
    # <choice unavail>: shown but not selectable (no routing / point-and-shoot),
    # and coloured with the CUA "unavailable" role.
    from screen import Color, _role_colour
    s = load_dtl(
        '<panel><selfld>'
        '<choice selchar="1" name="Ok" match="A">available'
        '<choice selchar="2" name="No" match="B" unavail>disabled'
        '</selfld></panel>'
    )
    assert "A" in s.selections and "B" not in s.selections     # unavailable can't be picked
    assert 5 not in s.selection_rows                            # …nor point-and-shot
    dimmed = [it for it in s.items if getattr(it, "role", None) == "unavail"]
    assert len(dimmed) == 3                                     # num/name/desc of the row
    assert all(_role_colour(it.color, it.role) is Color.BLUE for it in dimmed)


# ── <ps> point-and-shoot fields (#115) ───────────────────────────────────────

def test_ps_records_point_and_shoot_rows_and_renders_text():
    # <ps var=.. value=..> inside a choice supplies the choice text and makes the
    # row point-and-shoot: cursoring on it sets the variable (see the DTL guide's
    # Figure 151). The enclosed text renders as the choice description.
    s = load_dtl(
        '<panel name=ps1 menu><selfld type=menu>'
        '<choice><ps var=zcmd value=1>Selection #1</ps>'
        '<choice><ps var=zcmd value=2>Selection #2</ps>'
        '</selfld><cmdarea></panel>'
    )
    assert s.ps_rows == {0: ("zcmd", "1"), 1: ("zcmd", "2")}
    # The point-and-shoot text is not lost — it is the choice's description.
    descs = [it for it in s.items if isinstance(it, Text) and "Selection" in it.text]
    assert [it.text for it in descs] == ["Selection #1", "Selection #2"]
    assert s.point_and_shoot_at(0 * 80 + 5) == ("zcmd", "1")
    assert s.point_and_shoot_at(1 * 80 + 3) == ("zcmd", "2")
    assert s.point_and_shoot_at(9 * 80) is None
    assert s.point_and_shoot_at(None) is None


def test_ps_value_star_uses_the_choice_number():
    # VALUE=* on a <ps> in a <choice> uses the choice's number as the value.
    s = load_dtl(
        '<panel name=ps1 menu><selfld type=menu>'
        '<choice><ps var=zcmd value=*>First</ps>'
        '<choice><ps var=zcmd value=*>Second</ps>'
        '</selfld></panel>'
    )
    assert s.ps_rows == {0: ("zcmd", "1"), 1: ("zcmd", "2")}


def test_ps_drives_the_command_line_only_for_the_command_variable():
    # command_point_and_shoot resolves a <ps> to the option line only when its VAR
    # is the panel's command variable (the <cmdarea>, defaulting to ZCMD).
    s = load_dtl(
        '<panel name=ps1 menu><selfld type=menu>'
        '<choice><ps var=zcmd value=7>Sets the command</ps>'
        '<choice><ps var=other value=9>Sets another variable</ps>'
        '</selfld><cmdarea></panel>'
    )
    assert s.command_point_and_shoot(0 * 80 + 5) == "7"
    assert s.command_point_and_shoot(1 * 80 + 5) is None   # not the command variable
    assert s.command_point_and_shoot(None) is None


def test_ps_in_body_text_preserves_the_text_and_row():
    # A <ps> is also valid inside body text (info/p/…): the text renders and its
    # row is recorded for point-and-shoot.
    s = load_dtl(
        '<panel name=p><area>'
        '<info>See <ps var=zsel value=go>the details</ps> here.</info>'
        '</area></panel>'
    )
    line = next(it for it in s.items if isinstance(it, Text) and "details" in it.text)
    assert "See the details here." == line.text
    assert s.ps_rows.get(line.row) == ("zsel", "go")


# ── <chofld> choice data fields (#115) ───────────────────────────────────────

def test_chofld_adds_an_entry_field_within_the_choice():
    # <chofld> nests an input field in a <choice> row: the text before it is the
    # choice description, the field follows it, and the text after it is the
    # field's own description on the line below (see the guide's Figure 96).
    s = load_dtl(
        '<panel name=m menu><selfld type=menu>'
        '<choice checkvar=card match=new>New Type:'
        '<chofld datavar=cardtype entwidth=9>(Permanent or Temporary)'
        '<choice checkvar=card match=renew>Renewal'
        '</selfld></panel>'
    )
    desc = next(it for it in s.items if isinstance(it, Text) and it.text == "New Type:")
    field = next(it for it in s.items if isinstance(it, Field) and it.name == "cardtype")
    assert field.length == 9
    assert field.row == desc.row and field.col > desc.col        # follows the description
    # The chofld's own description sits on the next line.
    fdesc = next(it for it in s.items if isinstance(it, Text)
                 and it.text == "(Permanent or Temporary)")
    assert fdesc.row == desc.row + 1
    # The choice below flows past the extra description line.
    renewal = next(it for it in s.items if isinstance(it, Text) and it.text == "Renewal")
    assert renewal.row == desc.row + 2


def test_chofld_autotab_recorded():
    # AUTOTAB=YES is a client cursor-advance behaviour with no 3270 data-stream
    # bit; like <dtafld>, it is recorded on the Field as metadata and does not
    # alter the rendered stream (#115).
    on = load_dtl(
        '<panel name=m menu><selfld type=menu>'
        '<choice>Opt<chofld datavar=cf entwidth=6 autotab=yes>d</selfld></panel>'
    )
    off = load_dtl(
        '<panel name=m menu><selfld type=menu>'
        '<choice>Opt<chofld datavar=cf entwidth=6>d</selfld></panel>'
    )
    field = next(it for it in on.items if isinstance(it, Field) and it.name == "cf")
    assert field.autotab is True
    # Metadata only: the rendered stream is byte-identical with/without AUTOTAB.
    assert on.render() == off.render()


def test_chofld_usage_out_is_a_display_field():
    # USAGE=OUT makes the choice data field display-only: the variable's value as
    # protected text, not an editable field.
    s = load_dtl(
        '<panel name=m menu><selfld type=menu>'
        '<choice>Status:<chofld datavar=st usage=out entwidth=6>'
        '</selfld></panel>',
        ST="OPEN",
    )
    assert not any(isinstance(it, Field) and it.name == "st" for it in s.items)
    cell = next(it for it in s.items if isinstance(it, Text) and it.text.startswith("OPEN"))
    assert cell.text == "OPEN  "                    # padded to entwidth, protected


# ── <scrfld> scrollable fields (#115) ────────────────────────────────────────

def test_scrfld_on_a_dtafld_records_metadata_and_draws_a_scale():
    # <scrfld> makes the enclosing <dtafld> horizontally scrollable: the on-screen
    # window stays the field's entwidth, DISPLEN is the (wider) logical length, and
    # a SCALE ruler is drawn below the field (see the guide's Figure 41).
    s = load_dtl(
        '<panel name=p><area><dtacol pmtwidth=6 entwidth=10>'
        '<dtafld datavar=nm>Name<scrfld displen=40 scale=nmsc>'
        '</dtacol></area></panel>'
    )
    field = next(it for it in s.items if isinstance(it, Field) and it.name == "nm")
    assert field.length == 10                        # the window, not DISPLEN
    ruler = next(it for it in s.items if isinstance(it, Text) and it.text.startswith("----+"))
    assert ruler.text == "----+----1"                # scale ruler, field-width wide
    assert ruler.row == field.row + 1 and ruler.col == field.col
    assert s.scroll_fields == [
        {"name": "nm", "displen": 40, "scroll": "on", "scale": "nmsc"}]


def test_scrfld_separator_indicator_shows_scroll_direction():
    # A SINDVAR (separator) scroll indicator renders as a run of dashes ending in a
    # '>' (data extends to the right), field-width wide.
    s = load_dtl(
        '<panel name=p><area><dtacol pmtwidth=6 entwidth=10>'
        '<dtafld datavar=nm>Name<scrfld displen=40 sindvar=si>'
        '<dtafld datavar=ad>Addr'
        '</dtacol></area></panel>'
    )
    sep = next(it for it in s.items if isinstance(it, Text) and set(it.text) <= set("->"))
    assert sep.text == "---------" + ">"            # 10 wide
    # The following field flows below the generated indicator line, not over it.
    addr = next(it for it in s.items if isinstance(it, Field) and it.name == "ad")
    assert addr.row == sep.row + 1


def test_scrfld_on_a_lstcol_draws_a_scale_under_the_heading():
    # A scrollable list column draws its scale line between the heading and the
    # data cells (see the guide's Figure 42).
    s = load_dtl(
        '<panel name=p><area><lstfld>'
        '<lstcol datavar=mon colwidth=9>Monday<scrfld displen=30 scale=monsc>'
        '</lstfld></area></panel>'
    )
    heading = next(it for it in s.items if isinstance(it, Text) and it.text == "Monday")
    ruler = next(it for it in s.items if isinstance(it, Text) and it.text.startswith("----+"))
    cell = next(it for it in s.items if isinstance(it, Field) and it.name == "mon")
    assert ruler.text == "----+----"                # colwidth (9) wide
    assert ruler.row == heading.row + 1 and cell.row == ruler.row + 1
    assert s.scroll_fields == [
        {"name": "mon", "displen": 30, "scroll": "on", "scale": "monsc"}]


def test_ispf_panel_selections_drive_validation():
    s = load_panel("ispf", ZUSER="IBMUSER ", ZTIME="13:45")
    # The menu declares 0-7, 9-13 and X — but not 8.
    for opt in ["0", "3", "13", "X"]:
        assert opt in s.selections
    assert "8" not in s.selections
    assert s.selections["3"] == "Utilities"


def test_substitution():
    # &NAME dialog-variable reference, matched case-insensitively.
    s = load_dtl('<panel><info>Hi &who</info></panel>', WHO="BOB")
    assert s.items[0].text == "Hi BOB"


def test_substitution_terminator_and_escape():
    # A trailing '.' terminates (and is consumed by) the reference; && is literal.
    s = load_dtl(
        '<panel><info>&ZUSER.X uses &&</info></panel>', ZUSER="IBMUSER"
    )
    assert s.items[0].text == "IBMUSERX uses &"


def test_substitution_unknown_left_intact():
    s = load_dtl('<panel><info>keep &NOPE here</info></panel>')
    assert s.items[0].text == "keep &NOPE here"


# ── SGML general entities (<!ENTITY name "text">) ────────────────────────────

def test_internal_entity_reference_resolved():
    # <!ENTITY name "value"> declared in the doctype internal subset; &name;
    # references in the body are replaced with the value.
    s = load_dtl(
        '<!doctype dm system [\n'
        '<!entity guar "money-back guarantee">\n'
        ']>\n'
        '<panel><info>It has our &guar;.</info></panel>'
    )
    assert s.items[0].text == "It has our money-back guarantee."


def test_external_system_entity_reference_left_intact():
    # A SYSTEM entity references a file we don't have; leave the reference as-is
    # rather than dropping or guessing it.
    s = load_dtl(
        '<!doctype dm system [\n'
        '<!entity widgets system>\n'
        ']>\n'
        '<panel><info>See &widgets; now.</info></panel>'
    )
    assert s.items[0].text == "See &widgets; now."


def test_entity_does_not_disturb_dialog_vars():
    # An entity declaration in the same source must not break &NAME dialog
    # variables (which use a '.' terminator, not ';') or && escapes.
    s = load_dtl(
        '<!entity guar "G">\n'
        '<panel><info>&guar; &who.X &&</info></panel>',
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


def test_cmdtbl_applid_recorded_and_sort_is_a_no_op():
    # #126: <cmdtbl APPLID=> is recorded (Screen.commands_applid); SORT has no
    # host-display effect (the table renders nothing — it feeds command lookup).
    s = load_dtl('<panel name="p">M<cmdarea>Option ===></cmdarea>'
                 '<cmdtbl applid="ISR" sort="yes">'
                 '<cmd name="BYE" altdescr="Leave"><cmdact action="alias exit"></cmd>'
                 '</cmdtbl></panel>')
    assert s.commands_applid == "ISR"
    assert s.commands["BYE"] == {"action": "alias exit", "trunc": 0, "descr": "Leave"}
    # SORT=yes vs no renders identically (no visual effect)
    def render(sort):
        return load_dtl(f'<panel name="p">M<cmdtbl applid="X" {sort}>'
                        '<cmd name="A"><cmdact action="passthru"></cmd>'
                        '</cmdtbl></panel>').render()
    assert render('sort="yes"') == render('sort="no"')


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
    # Command table is metadata — it doesn't add any rendered items.
    assert s.lookup_command("PANELID") == "passthru"


# ── action bars (<ab>/<abc>/<pdc>) ───────────────────────────────────────────

def test_action_bar_renders_labels_and_records_pulldowns():
    s = load_dtl(
        '<panel>'
        '<ab>'
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
        '<panel><ab>'
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
        '<panel><ab>'
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
        '<panel><ab>'
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
        '<ab>'
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
        load_dtl('<panel><ab><pdc>x</pdc></ab></panel>')


# ── small structural tags: <pdsep>, <rp>, <t>, <varsub> (#118) ───────────────

def test_pdsep_records_separator_between_pulldown_choices():
    # <PDSEP> is a divider row within an action-bar pull-down: it closes the
    # choice above it (DTL omits end tags) and lands a separator marker between
    # the pull-down choices, without itself being a selectable choice.
    s = load_dtl(
        '<panel><ab>'
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
        load_dtl('<panel><ab><pdsep></ab></panel>')


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


# ── action-bar / command attributes (#126) ──────────────────────────────────

def test_action_bar_mnemgen_auto_underlines_first_letter():
    # #126: <ab MNEMGEN=YES> auto-assigns the first letter of a choice as its
    # mnemonic (underlined) when the choice has no explicit <M>. It is opt-in:
    # absent / MNEMGEN=NO leaves bare labels byte-identical, and an explicit <M>
    # always wins over the auto-generated one.
    from screen import Highlight
    def runs(markup):
        s = load_dtl('<panel name="p">T<ab ' + markup +
                     '><abc>File<pdc>Open<action run="x"></pdc></abc></ab></panel>')
        return [t.runs for t in s.items if getattr(t, "runs", None)]
    assert runs('mnemgen="yes"') == [[("F", None, Highlight.UNDERSCORE),
                                      ("ile", None, None)]]
    assert runs('') == []                             # absent → no auto-mnemonic
    assert runs('mnemgen="no"') == []                 # NO → no auto-mnemonic
    # an explicit <M> wins (underlines the marked letter, not the first)
    s = load_dtl('<panel name="p">T<ab mnemgen="yes">'
                 '<abc>F<M>ile<pdc>Open<action run="x"></pdc></abc></ab></panel>')
    r = next(t.runs for t in s.items if getattr(t, "runs", None))
    assert r == [("F", None, None), ("i", None, Highlight.UNDERSCORE),
                 ("le", None, None)]


def test_action_bar_absepstr_draws_separator_between_choices():
    # ABSEPSTR is the string drawn between the action-bar choices; the choices lay
    # out flush against it (no default gap), so " | " gives "File | Edit".
    s = load_dtl(
        '<panel><ab absepstr=" | ">'
        '<abc>File<pdc>Open<action run=x></pdc>'
        '<abc>Edit<pdc>Cut<action run=y></pdc>'
        '</ab></panel>'
    )
    assert s.items[0] == Text(0, 1, "File", DisplayIntensity.HIGH)
    assert s.items[1] == Text(0, 5, " | ", DisplayIntensity.HIGH)   # after "File"
    assert s.items[2] == Text(0, 8, "Edit", DisplayIntensity.HIGH)  # 5 + len(" | ")
    assert [c["col"] for c in s.action_bar] == [1, 8]


def test_action_bar_absepchar_draws_separator_line_below_bar():
    # ABSEPCHAR is the character of the separator *line* under the action bar
    # (dividing the bar from the panel body): a full-width rule on the next row.
    s = load_dtl(
        '<panel><ab absepchar="=">'
        '<abc>File<pdc>Open<action run=x></pdc>'
        '</ab></panel>'
    )
    rules = [it for it in s.items if getattr(it, "role", None) == "rule"]
    assert len(rules) == 1
    assert rules[0].row == 1 and rules[0].col == 1     # row below the bar
    assert set(rules[0].text) == {"="} and len(rules[0].text) == 80 - 1 - 1


def test_action_bar_without_separators_is_byte_identical():
    # Neither attribute present → the default gap layout is unchanged (regression).
    s = load_dtl(
        '<panel><ab>'
        '<abc>Menu<pdc>Exit<action run="exit"></pdc></abc>'
        '<abc>Help<pdc>About<action run="passthru"></pdc></abc>'
        '</ab></panel>'
    )
    assert s.items[0] == Text(0, 1, "Menu", DisplayIntensity.HIGH)
    assert s.items[1] == Text(0, 8, "Help", DisplayIntensity.HIGH)   # gap of 3
    assert not [it for it in s.items if getattr(it, "role", None) == "rule"]


def test_pdc_unavail_marks_item_unavailable_by_variable():
    # UNAVAIL=var makes the pull-down item unavailable when the variable is true
    # (shown but greyed + unselectable); false/absent leaves it available.
    on = load_dtl(
        '<panel><ab><abc>View'
        '<pdc unavail=nolist>Refresh<action run=ref>'
        '</ab></panel>', nolist="1")
    off = load_dtl(
        '<panel><ab><abc>View'
        '<pdc unavail=nolist>Refresh<action run=ref>'
        '</ab></panel>', nolist="0")
    assert on.action_bar[0]["pdc"][0]["unavail"] is True
    assert "unavail" not in off.action_bar[0]["pdc"][0]   # available: key omitted


def test_pdc_checkvar_match_marks_current_item():
    # CHECKVAR=var MATCH=x flags the item as the current setting when the variable
    # equals MATCH (the pull-down analogue of <choice checkvar>); else no flag.
    s = load_dtl(
        '<panel><ab><abc>Sort'
        '<pdc checkvar=order match=NAME>By Name<action run=byname>'
        '<pdc checkvar=order match=DATE>By Date<action run=bydate>'
        '</ab></panel>', order="NAME")
    pdc = s.action_bar[0]["pdc"]
    assert pdc[0].get("checked") is True
    assert "checked" not in pdc[1]


def test_pdc_unavailable_item_is_dimmed_and_unselectable():
    # In the open pull-down, an unavailable item renders at NORMAL (dimmed)
    # intensity and cannot be selected; the cursor lands on the first available
    # item, or on the CHECKVAR-current item when one is present.
    import server
    from server import encode_pack_addr
    from screen import Screen
    pdc = [
        {"label": "Refresh", "action": "ref", "mnemonic": None, "help": None,
         "unavail": True},
        {"label": "By Name", "action": "byname", "mnemonic": None, "help": None,
         "checked": True},
        {"label": "By Date", "action": "bydate", "mnemonic": None, "help": None},
    ]
    choice = {"label": "View", "row": 0, "col": 1, "pdc": pdc}

    def reply(aid, row, col):
        return bytes([aid]) + encode_pack_addr(row, col) + bytes([0xFF, 0xEF])

    # Cursor lands on the checked "By Name" (row 3), not the greyed row.
    scr = Screen()
    server._show_pulldown(_Sock2([bytes([0xF3, 0xFF, 0xEF])]), scr, choice)
    intens = {it.row: it.intensity for it in scr.items
              if it.col == 1 and hasattr(it, "intensity")}
    assert intens[2] == DisplayIntensity.NORMAL      # unavailable Refresh: dimmed
    assert intens[3] == DisplayIntensity.HIGH        # available By Name
    assert scr.cursor_at == (3, 2)                    # on the current (checked) item
    # Enter on the greyed Refresh row (2) does not select it.
    assert server._show_pulldown(_Sock2([reply(0x7D, 2, 3)]), Screen(), choice) == ""
    # Enter on an available item selects its action.
    assert server._show_pulldown(_Sock2([reply(0x7D, 4, 3)]), Screen(), choice) == "bydate"


def test_action_setvar_and_togvar_are_modelled():
    # <action setvar=/togvar=> record the variable an action assigns/toggles so the
    # dialog can model an on/off "Settings"-style pull-down item; TYPE is captured.
    s = load_dtl(
        '<panel><ab><abc>Opts'
        '<pdc>Word wrap<action run=noop togvar=WRAP value1=OFF value2=ON>'
        '<pdc>Reset<action run=noop setvar=WRAP value=OFF type=cmd>'
        '</ab></panel>'
    )
    wrap, reset = s.action_bar[0]["pdc"]
    assert wrap["togvar"] == ("WRAP", "OFF", "ON")
    assert reset["setvar"] == ("WRAP", "OFF")
    assert reset["type"] == "cmd"


def test_keyl_records_name_and_applid():
    # <keyl name=.. applid=..> records the keylist's identity (a panel references it
    # via KEYLIST=name); the key→command bindings still populate .keylist.
    s = load_dtl(
        '<panel><keyl name=MYKEYS applid=ISR>'
        '<keyi key=PF3 cmd=EXIT>Exit</keyi>'
        '</keyl></panel>'
    )
    assert s.keylist == {"PF3": "EXIT"}
    assert s.keylist_name == "MYKEYS"
    assert s.keylist_applid == "ISR"


def test_keyi_fka_text_recorded_and_suppressed_by_fka_no():
    # A <keyi>'s content is its function-key-area label; FKA=NO suppresses it.
    s = load_dtl(
        '<panel><keyl>'
        '<keyi key=PF1 cmd=HELP>Help</keyi>'
        '<keyi key=PF3 cmd=EXIT>Exit</keyi>'
        '<keyi key=PF12 cmd=CANCEL fka=no>Cancel</keyi>'
        '</keyl></panel>'
    )
    assert s.keylist_fka == {"PF1": "Help", "PF3": "Exit"}   # PF12 (FKA=NO) omitted


def test_keyi_case_folds_the_fka_label():
    # #126: <keyi CASE=UPPER|LOWER> folds the function-key-area label; ASIS/absent
    # keeps the authored case.
    def fka(case):
        s = load_dtl(f'<panel><keyl><keyi key=PF3 cmd=EXIT case="{case}">Exit Now</keyi>'
                     '</keyl></panel>')
        return s.keylist_fka["PF3"]
    assert fka("upper") == "EXIT NOW"
    assert fka("lower") == "exit now"
    assert fka("") == "Exit Now"                     # absent → authored case kept


def test_rp_reference_phrase_is_inline_underlined_link():
    # <rp> (reference phrase — a link to another help panel) emphasises a phrase
    # in place, like <hp>: one Text.rich whose phrase run is underlined.
    s = load_dtl(
        '<panel><info>see <rp help=glospan>the glossary</rp> now</info></panel>'
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
        '<panel><info>see <rp help=g>the glossary</rp> now</info></panel>'
    )
    it = s.items[0]
    rich = bytearray(); it.render(rich, color=False)
    plain = bytearray(); Text(it.row, it.col, it.text).render(plain, color=False)
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
    # &VIEWMSG; the browse panel is a title-less key-list frame (the server
    # injects the header/footer status band — see _show_browse).
    entry = load_panel("viewentry", VIEWMSG="MEMBER FOO NOT FOUND")
    assert entry.field_addr("member") is not None
    assert entry.command_for("PF3") == "EXIT"
    assert "MEMBER FOO NOT FOUND" in [t.text for t in entry.items if isinstance(t, Text)]

    browse = load_panel("browse")
    assert browse.title == "Browse"                      # metadata only (TITLINE=NO)
    assert [t for t in browse.items if isinstance(t, Text)] == []  # no on-screen body
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
    # The command line auto-flows below the top instruction; its exact row is the
    # compiler's business, but it is a real named input field the cursor lands in.
    assert s.field_addr("ZCMD") is not None       # Command ===> input line
    zcmd = next(f for f in s.items if isinstance(f, Field) and f.name == "ZCMD")
    assert zcmd.cursor                             # <panel cursor=ZCMD>
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


# ── <lstfld> table-input read-back (#249) ────────────────────────────────────

def test_input_lstfld_tags_each_cell_with_its_model_row():
    # An input <lstfld> column renders one Field per model row; each carries its
    # model-row index (row_index) so the rows can be told apart on read-back,
    # even though every row's cell in a column shares the column DATAVAR.
    src = (
        '<panel name="t">T'
        '<lstfld><lstcol datavar="k" usage="in" colwidth="6">Key'
        '<lstcol datavar="v" usage="in" colwidth="10">Val</lstfld></panel>'
    )
    rows = [{"k": "a", "v": "1"}, {"k": "b", "v": "2"}, {"k": "c", "v": "3"}]
    s = load_dtl(src, rows=rows)
    cells = [f for f in s.items if isinstance(f, Field) and f.row_index is not None]
    # two input columns × three rows
    assert len(cells) == 6
    assert sorted({f.row_index for f in cells}) == [0, 1, 2]
    # every row index carries both column DATAVARs
    by_row = {}
    for f in cells:
        by_row.setdefault(f.row_index, set()).add(f.name)
    assert by_row == {0: {"k", "v"}, 1: {"k", "v"}, 2: {"k", "v"}}


def test_read_table_rows_keeps_rows_distinct_despite_shared_datavar():
    # Screen.parse would collapse the table (last-address-wins per DATAVAR);
    # read_table_rows uses the per-cell row_index to return one dict per row.
    src = (
        '<panel name="t">T'
        '<lstfld><lstcol datavar="k" usage="in" colwidth="6">Key'
        '<lstcol datavar="v" usage="in" colwidth="10">Val</lstfld></panel>'
    )
    rows = [{"k": "a", "v": "1"}, {"k": "b", "v": "2"}]
    s = load_dtl(src, rows=rows)
    cells = [f for f in s.items if isinstance(f, Field) and f.row_index is not None]
    # client modifies row 1's "k" and row 0's "v"; the rest are unmodified
    modified = {}
    for f in cells:
        if f.name == "k" and f.row_index == 1:
            modified[f.data_addr] = "Z"
        if f.name == "v" and f.row_index == 0:
            modified[f.data_addr] = "9"
    out = s.read_table_rows(modified)
    # one dict per displayed model row; modified cells override, others keep the
    # originally rendered value (the field default)
    assert out == [{"k": "a", "v": "9"}, {"k": "Z", "v": "2"}]
    # a plain Screen.parse can't do this — the DATAVARs collapse to one value each
    _aid, named = s.parse(0x7D, modified)
    assert set(named) <= {"k", "v"}  # only one value survives per column


def test_read_table_rows_empty_for_display_only_table():
    # A read-only (usage=out) table has no input cells, so there is nothing to
    # read back: read_table_rows returns [].
    s = load_panel("dlgtest", rows=[{"vname": "ZUSER", "vvalue": "IBMUSER"}])
    assert s.read_table_rows({}) == []


def test_lstcol_caps_on_folds_typed_value_to_uppercase_on_readback():
    # <lstcol CAPS=ON>: the input cell is marked caps, and read_table_rows folds
    # the typed value to uppercase (as ISPF's CAPS(ON) does); a plain column does
    # not (#238).
    src = (
        '<panel name="t">T'
        '<lstfld>'
        '<lstcol datavar="k" usage="in" caps="on" colwidth="6">Key'
        '<lstcol datavar="v" usage="in" colwidth="10">Val'
        '</lstfld></panel>'
    )
    s = load_dtl(src, rows=[{"k": "", "v": ""}])
    cells = {f.name: f for f in s.items
             if isinstance(f, Field) and f.row_index is not None}
    assert cells["k"].caps is True and cells["v"].caps is False
    # client types lowercase into both cells
    modified = {cells["k"].data_addr: "abc", cells["v"].data_addr: "xyz"}
    out = s.read_table_rows(modified)
    # the caps column is folded, the plain column is left as typed
    assert out == [{"k": "ABC", "v": "xyz"}]


def test_lstcol_caps_default_off():
    # CAPS is OFF by default: no fold.
    src = ('<panel name="t">T<lstfld>'
           '<lstcol datavar="k" usage="in" colwidth="6">Key</lstfld></panel>')
    s = load_dtl(src, rows=[{"k": ""}])
    cell = next(f for f in s.items
                if isinstance(f, Field) and f.row_index is not None)
    assert cell.caps is False
    assert s.read_table_rows({cell.data_addr: "abc"}) == [{"k": "abc"}]


def _required_table(rows):
    src = (
        '<panel name="t">T<lstfld>'
        '<lstcol datavar="k" usage="in" required="yes" msg="KEYREQ" colwidth="6">Key'
        '<lstcol datavar="v" usage="in" colwidth="10">Val'
        '</lstfld></panel>'
    )
    return load_dtl(src, rows=rows)


def test_lstcol_required_parses_onto_input_cell():
    # <lstcol REQUIRED=YES MSG=id> marks the cell required and carries its MSG.
    s = _required_table([{"k": "", "v": ""}])
    cells = {f.name: f for f in s.items
             if isinstance(f, Field) and f.row_index is not None}
    assert cells["k"].required is True and cells["k"].msg == "KEYREQ"
    assert cells["v"].required is False and cells["v"].msg is None


def test_required_blank_on_modified_row_is_an_error():
    # A row the user modified whose required cell is blank yields the column MSG;
    # a row left untouched is not validated (#236).
    s = _required_table([{"k": "", "v": ""}, {"k": "", "v": ""}])
    cells = [f for f in s.items if isinstance(f, Field) and f.row_index is not None]
    # user types into row 0's Value only (row 0 modified, its Key still blank);
    # row 1 is left entirely untouched
    modified = {f.data_addr: "hello"
                for f in cells if f.name == "v" and f.row_index == 0}
    errors = s.table_required_errors(modified)
    assert errors == [(0, "k", "KEYREQ")]


def test_required_satisfied_when_cell_filled():
    # The required cell is non-blank on the modified row → no error.
    s = _required_table([{"k": "", "v": ""}])
    cells = [f for f in s.items if isinstance(f, Field) and f.row_index is not None]
    modified = {f.data_addr: ("KEY1" if f.name == "k" else "val")
                for f in cells if f.row_index == 0}
    assert s.table_required_errors(modified) == []


def test_required_ignored_on_display_only_table():
    # No input cells → nothing to validate.
    s = load_panel("dlgtest", rows=[{"vname": "ZUSER", "vvalue": "X"}])
    assert s.table_required_errors({}) == []


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


def test_msg_help_pointer_and_msgtype_carried():
    cat = load_messages(
        '<msgmbr name="M">'
        '<msg msgid="H" msgtype="ACTION" help="HHELP">Do something</msg>'
        '<msg msgid="P">Plain</msg>'
        '</msgmbr>'
    )
    assert cat.help("H") == "HHELP"                      # <msg help=> carried
    assert cat.help("h") == "HHELP"                      # id lookup is case-fold
    assert cat.help("P") is None                         # no help pointer
    assert cat.help("NOSUCH") is None                    # unknown id
    assert cat.msgtype("H") == "action"                  # MSGTYPE carried, folded
    assert cat.msgtype("P") is None                      # untyped message


def test_msg_member_width_wraps_long_message():
    cat = load_messages(
        '<msgmbr name="M" width="20" ccsid="37">'
        '<msg msgid="L">The value &N is not valid in this context</msg>'
        '</msgmbr>'
    )
    assert cat.ccsid == 37                               # <msgmbr ccsid=> carried
    # Long text word-wrapped to the member WIDTH, substituting &N at the same time.
    assert cat.lines("L", N="X") == [
        "The value X is not", "valid in this", "context"]


def test_msg_lines_default_width_keeps_single_line():
    # With no <msgmbr width=>, ISPF's default width (76) applies, so a short
    # message stays one line — and the id is not prefixed (that's format()'s job).
    cat = load_messages('<msgmbr name="M"><msg msgid="P">Plain text</msg></msgmbr>')
    assert cat.width is None and cat.ccsid is None
    assert cat.lines("P") == ["Plain text"]
    assert cat.lines("NOSUCH") == ["NOSUCH"]             # unknown id echoes


def test_msg_format_asis_keeps_authored_line_breaks_and_records_location():
    # #127: FORMAT=ASIS preserves the message's authored line breaks (vs FLOW,
    # which word-wraps to WIDTH); LOCATION is recorded for the server to place it.
    cat = load_messages(
        '<msgmbr name="M" width="40">'
        '<msg msgid="A" format="asis" location="modal">Line one\nLine two\nLine three</msg>'
        '<msg msgid="F">a long flowing message that must wrap to the member width here</msg>'
        '</msgmbr>')
    assert cat.lines("A") == ["Line one", "Line two", "Line three"]   # ASIS: kept
    assert cat.location("A") == "modal"
    assert len(cat.lines("F")) > 1                       # FLOW (default) still wraps
    assert cat.location("F") is None


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


def test_panel_dimensions_do_not_change_bytes():
    # DEPTH/WIDTH set to their defaults (24/80) render identically to omitting them.
    a = load_dtl("<panel>Menu<info>hi</info></panel>")
    b = load_dtl("<panel depth='24' width='80'>Menu<info>hi</info></panel>")
    assert a.render() == b.render()


# ── help panels (<panel help=...>) ───────────────────────────────────────────

def test_panel_help_attribute_parsed():
    s = load_dtl('<panel name="p" help="phelp"><info>x</info></panel>')
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
        '<panel><area>'
        '<dtafld datavar="size" entwidth="5" help="sizehelp">Size</dtafld>'
        '<dtafld datavar="name" entwidth="8">Name</dtafld>'
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
        s = load_dtl(f'<panel><area>'
                     f'<dtafld datavar="f" entwidth="4" help="{val}">F</dtafld>'
                     f'</area></panel>')
        assert s.help_for(s.field_addr("f")) is None


def test_action_bar_choice_help_resolved_by_cursor():
    # <abc help=panel>: HELP with the cursor on that action-bar choice shows its
    # own help; a choice without HELP resolves to None (the panel help is used).
    s = load_dtl(
        '<panel><ab>'
        '<abc help="filehelp">File<pdc>Open<action run="x"></pdc>'
        '<abc>Edit<pdc>Cut<action run="y"></pdc>'
        '</ab></panel>'
    )
    file_c, edit_c = s.action_bar
    on_file = file_c["row"] * 80 + file_c["col"]
    assert s.help_for(on_file) == "filehelp"           # cursor on "File"
    assert s.help_for(on_file + 3) == "filehelp"       # within the label
    assert s.help_for(edit_c["row"] * 80 + edit_c["col"]) is None   # Edit: no help


def test_logon_size_field_has_context_help():
    lg = load_panel("logon")
    assert lg.help_for(lg.field_addr("size")) == "sizehelp"   # field help
    assert lg.help_for(lg.field_addr("userid")) is None       # falls back to panel help


def test_help_attribute_does_not_change_rendered_bytes():
    # Adding help="..." to <panel> is metadata; it does not change the render.
    a = load_dtl("<panel>Menu<info>hi</info></panel>")
    b = load_dtl("<panel help='ispfhelp'>Menu<info>hi</info></panel>")
    assert a.render() == b.render()


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


def test_headings_render_high_intensity_indented_by_level():
    # #52: <h1>-<h4> render a high-intensity heading line in the text flow;
    # sub-headings indent by level (h1→0, h2→2, h3→4, h4→6) so the hierarchy shows.
    s = load_dtl('<panel name="p">T<area>'
                 '<h1>Overview<info>Body one'
                 '<h2>Details<info>Body two'
                 '<h4>Deep</area></panel>')
    heads = [it for it in s.items if isinstance(it, Text) and it.role == "title"
             and it.text in ("Overview", "Details", "Deep")]
    by_text = {t.text: t for t in heads}
    assert by_text["Overview"].intensity is DisplayIntensity.HIGH
    assert by_text["Overview"].col == 1                    # h1: no indent
    assert by_text["Details"].col == 3                     # h2: +2
    assert by_text["Deep"].col == 7                        # h4: +6
    assert all(h.intensity is DisplayIntensity.HIGH for h in heads)


def test_heading_leading_blank_and_compact():
    # A heading gets a leading blank line (like a paragraph); COMPACT suppresses it.
    spaced = load_dtl('<panel name="p"><area><info>Intro<h2>Head</area></panel>')
    head = next(t for t in spaced.items if isinstance(t, Text) and t.text == "Head")
    intro = next(t for t in spaced.items if isinstance(t, Text) and t.text == "Intro")
    assert head.row == intro.row + 2                       # blank line before the heading
    compact = load_dtl('<panel name="p"><area><info>Intro<h2 compact>Head</area></panel>')
    head = next(t for t in compact.items if isinstance(t, Text) and t.text == "Head")
    intro = next(t for t in compact.items if isinstance(t, Text) and t.text == "Intro")
    assert head.row == intro.row + 1                       # COMPACT: no leading blank


def test_figure_width_col_frames_only_the_column():
    # #52: FIG WIDTH=COL frames the figure to the enclosing column's width (not the
    # whole page), so the rule doesn't overrun a width-constrained <region>.
    def frame_len(width_attr):
        s = load_dtl(f'<panel name="p" width="60">T<region width="20">'
                     f'<fig frame="rule" {width_attr}><p>X</fig></region></panel>')
        rules = [t for t in s.items if isinstance(t, Text) and set(t.text) == {"-"}]
        return len(rules[0].text)
    assert frame_len('width="col"') == 20            # framed to the column width
    assert frame_len('width="page"') > 20            # PAGE spans the page
    assert frame_len('') == frame_len('width="page"')  # PAGE is the default


def test_figure_caption_renders_below_the_figure():
    # #52: <figcap> renders the caption line beneath the figure body.
    s = load_dtl('<panel name="p">T<area><fig frame="rule">'
                 '<p>Diagram<figcap>Figure 1. Widget</fig></area></panel>')
    texts = [it.text for it in s.items if isinstance(it, Text)]
    assert "Figure 1. Widget" in texts
    # the caption sits below the figure body
    cap = next(t for t in s.items if isinstance(t, Text) and t.text == "Figure 1. Widget")
    body = next(t for t in s.items if isinstance(t, Text) and t.text == "Diagram")
    assert cap.row > body.row


def test_paragraph_intense_conditionally_intensifies():
    # #123: <p INTENSE=varname> renders the paragraph high-intensity when the named
    # dialog variable resolves to a non-blank value, else normal.
    hi = load_dtl('<panel name="p" width="40"><area>'
                  '<p intense="flag">Alert</p></area></panel>', flag="Y")
    lo = load_dtl('<panel name="p" width="40"><area>'
                  '<p intense="flag">Alert</p></area></panel>')  # flag unset
    h = next(t for t in hi.items if isinstance(t, Text) and t.text == "Alert")
    l = next(t for t in lo.items if isinstance(t, Text) and t.text == "Alert")
    assert h.intensity is DisplayIntensity.HIGH
    assert l.intensity is DisplayIntensity.NORMAL


def test_paragraph_offset_hanging_indents_continuation_lines():
    # #123: <p OFFSET=n> is a hanging indent — the first line stays at the margin,
    # the second and following lines shift n columns right.
    s = load_dtl('<panel name="p" width="30"><area>'
                 '<p offset="4">This is a long paragraph that wraps onto several '
                 'lines here</p></area></panel>')
    lines = sorted((t.row, t.col) for t in s.items if isinstance(t, Text))
    assert lines[0][1] == 1                          # first line at the base column
    assert all(c == 1 + 4 for _, c in lines[1:])     # continuations offset by 4


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


def test_list_indent_shifts_items_and_markers_right():
    # INDENT=n on a <ul> shifts the whole list — bullet and item text — n columns
    # to the right of the flow margin (#123).
    s = load_dtl('<panel name="p"><ul indent=6><li>apple<li>pear</ul></panel>')
    N = DisplayIntensity.NORMAL
    assert s.items == [
        Text(0, 7, "o", N), Text(0, 11, "apple", N),
        Text(1, 7, "o", N), Text(1, 11, "pear", N),
    ]


def test_list_space_sets_item_text_indentation():
    # SPACE=YES on the list narrows the marker-to-text gap to 3 columns (default
    # 4), inherited by every <li> that does not carry its own SPACE (#123).
    s = load_dtl('<panel name="p"><ul space=yes><li>apple<li>pear</ul></panel>')
    N = DisplayIntensity.NORMAL
    assert s.items == [
        Text(0, 1, "o", N), Text(0, 4, "apple", N),
        Text(1, 1, "o", N), Text(1, 4, "pear", N),
    ]


def test_list_text_renders_a_heading_above_the_items():
    # TEXT= gives the list a heading line (then a blank) above its items (#123).
    s = load_dtl("<panel name=\"p\"><ul text='Choose:'><li>apple</ul></panel>")
    N = DisplayIntensity.NORMAL
    assert s.items == [
        Text(0, 1, "Choose:", N),               # heading on its own line
        Text(2, 1, "o", N), Text(2, 5, "apple", N),   # blank line at row 1
    ]


def test_paragraph_indent_shifts_the_whole_paragraph_right():
    # INDENT=n on a <p> shifts the flowed paragraph n columns right (#123). The
    # first paragraph flows at the margin; the blank-before spacing puts the
    # second on row 2.
    s = load_dtl('<panel name="p"><p>flush<p indent=8>shifted</p></panel>')
    N = DisplayIntensity.NORMAL
    assert s.items == [
        Text(0, 1, "flush", N),
        Text(2, 9, "shifted", N),
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


def test_lines_xmp_lp_noskip_suppresses_the_leading_blank():
    # #210: <lines>/<xmp>/<lp> take a leading blank line before them (ISPDTLC block
    # spacing); NOSKIP — their only attribute — suppresses it.
    def rowof(src, needle):
        s = load_dtl(src)
        return next(t.row for t in s.items if isinstance(t, Text) and needle in t.text)
    for tag in ("lines", "xmp"):
        spaced = rowof(f'<panel name=p><area><info>Intro<{tag}>Body</{tag}></area></panel>',
                       "Body")
        noskip = rowof(f'<panel name=p><area><info>Intro<{tag} noskip>Body</{tag}></area></panel>',
                       "Body")
        assert noskip == spaced - 1                  # NOSKIP removes the blank line
    # <lp> (list part / implied paragraph) renders as flowed text within a list.
    s = load_dtl('<panel name=p><area><ul><li>one<lp>a note</ul></area></panel>')
    assert any(isinstance(t, Text) and t.text == "a note" for t in s.items)


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


def test_note_type_colours_the_heading():
    # #218: a <note TYPE=cua-type> paints its "Note:" heading run in that CUA
    # type's standard colour (WT → red), leaving the body run uncoloured.
    s = load_dtl('<panel width="50"><area><info>'
                 '<note type="wt">Mind the gap between here and there.</note>'
                 '</info></area></panel>')
    head = next(t for t in s.items
                if getattr(t, "runs", None) and t.runs[0][0].startswith("Note:"))
    assert head.runs[0] == ("Note: ", Color.RED, None)
    assert head.runs[1][1] is None                       # body run stays default


def test_note_explicit_color_text_and_indent():
    # #116: <note COLOR=> paints the heading run explicitly; TEXT= overrides the
    # heading label; INDENT shifts the whole block right.
    s = load_dtl('<panel name="p" width="60">T<area>'
                 '<note color="green" text="Tip:" indent="4">Be careful</note></area></panel>')
    head = next(t for t in s.items
                if getattr(t, "runs", None) and t.runs[0][0].startswith("Tip:"))
    assert head.runs[0] == ("Tip: ", Color.GREEN, None)   # COLOR + TEXT applied
    assert head.col == 1 + 4                               # INDENT shifts the block


def test_nt_type_colours_the_hung_heading():
    # <nt> hangs its body under the heading; TYPE colours just the heading Text
    # (SAC → white), not the body lines.
    s = load_dtl('<panel width="30"><area><info>'
                 '<nt type="sac">Mind the gap here please.</nt>'
                 '</info></area></panel>')
    head = next(t for t in s.items if getattr(t, "text", "") == "Note:")
    assert head.color == Color.WHITE


def test_notel_type_colours_the_heading():
    # <notel TYPE=cua-type> colours its "Notes:" heading (ET → turquoise).
    s = load_dtl('<panel width="50"><area><info><notel type="et">'
                 '<li>First point.<li>Second point.</notel>'
                 '</info></area></panel>')
    head = next(t for t in s.items if getattr(t, "text", "") == "Notes:")
    assert head.color == Color.TURQUOISE


def test_note_type_colour_mono_is_byte_identical():
    # Colour-only: adding a CUA TYPE to a <note>/<nt> does not change the mono
    # data stream (the colour rides an SA order mono ignores).
    base = '<panel width="40"><area><info>{}</info></area></panel>'
    typed = load_dtl(base.format('<note type="ct">Save your work often here.</note>'))
    plain = load_dtl(base.format('<note>Save your work often here.</note>'))
    assert typed.render(color=False) == plain.render(color=False)


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


def test_list_field_div_draws_a_divider_after_each_model_row():
    # #67: <lstfld DIV=DASH> draws a divider line after each model row set.
    s = load_dtl('<panel name="p">T<area><lstfld div="dash">'
                 '<lstcol datavar="a" colwidth="6" usage="out">A</lstfld></area></panel>',
                 rows=[{"a": "1"}, {"a": "2"}])
    rules = [t for t in s.items if isinstance(t, Text) and set(t.text) == {"-"}]
    assert len(rules) == 2                            # one divider per model row


def test_list_field_scrollvar_adds_command_line_scroll_field():
    # #239: <lstfld scrollvar=> puts a "Scroll ===>" amount field at the right of
    # the command line and shortens the command field so they don't overlap.
    s = load_dtl(
        '<panel name="p" width="76">L<area>'
        '<lstfld scrollvar=zscroll scrcaps=on scrvhelp=scrhelp>'
        '<lstcol datavar=a colwidth=8 usage=out>Item</lstfld></area>'
        '<cmdarea entwidth="60">Command ===></cmdarea></panel>',
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
        '</area><cmdarea entwidth="20">Cmd ===></cmdarea></panel>',
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


def test_dtafld_outline_draws_box_lines():
    # #122: OUTLINE=NONE|L|R|O|U|BOX on a <dtafld> draws the 3270 field-outlining
    # lines — emitted as an SFE pair (type 0xC2) on an extended terminal, and
    # nothing on a mono terminal (byte-identical there).
    def field(kw):
        s = load_dtl(f'<panel name=p width=40><area>'
                     f'<dtafld entwidth=5 outline={kw}>N</area></panel>')
        return next(it for it in s.items if isinstance(it, Field))

    assert field("box").outline is Outline.BOX
    assert field("l").outline is Outline.LEFT
    assert field("").outline is None                 # no OUTLINE → none

    # The 0xC2 outlining pair reaches the wire on an extended (colour) render...
    f = field("box")
    ext = bytearray(); f.render(ext, color=True)
    assert bytes([0xC2, Outline.BOX.value]) in bytes(ext)
    # ...but a mono render carries no outlining (byte-for-byte unchanged).
    mono = bytearray(); f.render(mono, color=False)
    assert 0xC2 not in bytes(mono)


def test_da_attr_applies_rendering_attributes():
    # #124: <attr> data-area attribute characters carry INTENS/NUMERIC/PAD/
    # OUTLINE/JUST onto the field the character starts (datain → input field,
    # dataout → protected text).
    s = load_dtl(
        "<panel name=p width=40><area>"
        "<da name=area depth=3>"
        "<attr attrchar='%' type=datain numeric=on pad=nulls outline=box intens=high>"
        "<attr attrchar='~' type=datain intens=non>"
        "<attr attrchar='@' type=dataout just=right>"
        "%_____ ~____ @Total"
        "</da></area></panel>")
    fields = [it for it in s.items if isinstance(it, Field)]
    f0 = fields[0]                                    # the '%' input field
    assert f0.numeric and f0.pad == "\x00" and f0.outline is Outline.BOX
    assert f0.intensity is DisplayIntensity.HIGH
    assert fields[1].hidden is True                  # '~' INTENS=NON → non-display
    # '@' dataout JUST=right right-justifies its text within the run width.
    tot = next(it for it in s.items if isinstance(it, Text) and it.text.strip() == "Total")
    assert tot.text == "Total"                       # (fills its own width; single word)


def test_da_attr_records_non_rendering_attributes():
    # #124: attributes with no TN3270 display effect are still recognised (not
    # ignored) — CAPS/SKIP/GE/PAS/CKBOX/ATTN parse without error or leaking.
    s = load_dtl(
        "<panel name=p width=40><area><da name=a depth=2>"
        "<attr attrchar='#' type=datain caps=on skip=on ge=off pas=yes ckbox=off attn=off>"
        "#____"
        "</da></area></panel>")
    assert any(isinstance(it, Field) for it in s.items)   # field still rendered


def test_da_depth_reserves_height_and_div_closes_it():
    # #125: <da DEPTH=n> reserves a fixed height (the flow resumes DEPTH rows below
    # its top); DIV=SOLID/DASH draws a closing rule. DEPTH=* / absent → body height.
    s = load_dtl('<panel name="p" width="40">T<area>'
                 '<da depth="5" div="solid"><attr attrchar="$" type="char">Body $x</da>'
                 '<info>After</info></area></panel>')
    after = next(t for t in s.items if isinstance(t, Text) and t.text == "After")
    rules = [t for t in s.items if isinstance(t, Text) and set(t.text) == {"-"}]
    assert len(rules) == 1
    body_row = next(t.row for t in s.items if isinstance(t, Text) and "Body" in t.text)
    assert rules[0].row == body_row + 5          # divider below the reserved height
    assert after.row == rules[0].row + 1
    # default: no reserved height, no divider (byte-identical shape)
    d = load_dtl('<panel name="p" width="40">T<area>'
                 '<da><attr attrchar="$" type="char">Body $x</da>'
                 '<info>After</info></area></panel>')
    assert not [t for t in d.items if isinstance(t, Text) and set(t.text) == {"-"}]


def test_dtafld_pad_fills_entry_and_dtacol_default():
    # #122: PAD/PADC set the fill character for an empty <dtafld> entry, with the
    # same rules as <lstcol>. A <dtacol> PAD is the column default; a field's own
    # PAD overrides it. Padless fields keep the space fill (byte-identical).
    def fields(markup, **subs):
        s = load_dtl('<panel name=p width=40><area>' + markup
                     + '</area></panel>', **subs)
        return [it for it in s.items if isinstance(it, Field)]

    f = fields('<dtafld entwidth=5 pad=".">Name')[0]
    assert f.pad == "."
    buf = bytearray(); f.render(buf)
    assert to_ebcdic(".....") in bytes(buf)                      # empty entry padded

    assert fields('<dtafld entwidth=4 pad=NULLS>X')[0].pad == "\x00"
    assert fields('<dtafld entwidth=4 pad=USER>X')[0].pad is None
    assert fields('<dtafld entwidth=4>X')[0].pad is None          # no PAD → space
    assert fields('<dtafld entwidth=4 pad="." padc="_">X')[0].pad == "_"  # PADC wins
    assert fields('<dtafld entwidth=4 pad="%p">X', p="@")[0].pad == "@"   # %varname

    # <dtacol> PAD is the default; a field's own PAD overrides it.
    cols = fields('<dtacol pad="#"><dtafld entwidth=3>A'
                  '<dtafld entwidth=3 pad="*">B')
    assert cols[0].pad == "#"                                     # inherited
    assert cols[1].pad == "*"                                     # overridden


def test_list_column_pad_fills_empty_input_cell():
    # #234: PAD/PADC set the fill character for an empty input cell. PADC wins over
    # PAD; NULLS → a null fill; USER (profile pad, unavailable here) → the default
    # space fill; %varname is resolved; a padless column stays space-filled.
    def field(attrs, **subs):
        s = load_dtl('<panel name="p"><area><lstfld>'
                     f'<lstcol datavar=a colwidth=5 {attrs}>A'
                     '</lstfld></area></panel>', **subs)
        return next(it for it in s.items if isinstance(it, Field))

    # A literal pad character fills the whole empty 5-wide cell on the wire.
    f = field('pad="."')
    assert f.pad == "."
    buf = bytearray(); f.render(buf)
    assert to_ebcdic(".....") in bytes(buf)        # displayed pad run

    assert field('pad=NULLS').pad == "\x00"        # NULLS → null fill
    assert field('pad=USER').pad is None           # profile pad unavailable
    assert field('').pad is None                   # no PAD → default (space)

    # PADC overrides PAD when both are given.
    assert field('pad="." padc="*"').pad == "*"

    # %varname resolves against the dialog variables.
    assert field('pad="%mypad"', mypad="#").pad == "#"


def test_list_column_pad_is_byte_identical_without_the_attribute():
    # A <lstcol> with no PAD renders exactly as before (space fill), so the change
    # is additive for every existing table.
    markup = ('<panel name="p"><area><lstfld>'
              '<lstcol datavar=a colwidth=5>A'
              '<lstcol datavar=b colwidth=5>B'
              '</lstfld></area></panel>')
    data = load_dtl(markup).render()
    # An empty input cell is space-filled: a run of EBCDIC spaces inside the field.
    assert to_ebcdic("     ") in data


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


def test_lstfld_out_of_scope_attributes_are_accepted_and_ignored():
    # #240: the codegen/DBCS-only <lstfld>/<lstcol>/<lstgrp> attributes have no
    # host-display effect in this server (we emit no )ATTR/)MODEL/)PROC). They are
    # accepted and ignored — a panel that uses them loads and renders identically
    # to one that omits them, rather than being rejected.
    plain = (
        '<panel name="p">T<lstfld>'
        '<lstgrp>G<lstcol datavar="a" colwidth="6">A</lstgrp>'
        '</lstfld></panel>'
    )
    decorated = (
        '<panel name="p">T'
        '<lstfld rules="both" rows="scan" attrchange="new" vardcl="yes">'
        '<lstgrp>G'
        '<lstcol datavar="a" colwidth="6" clear="a" coltype="ee" pas="on"'
        ' csrgrp="1" attrchange="new" vardcl="yes">A'
        '</lstgrp></lstfld></panel>'
    )
    rows = [{"a": "1"}, {"a": "2"}]
    a = load_dtl(plain, rows=rows)
    b = load_dtl(decorated, rows=rows)
    # accepted (no exception) and no rendering difference: byte-for-byte identical
    assert b.render() == a.render()


def test_choice_divider_solid_draws_a_rule_and_separates_choices():
    # #53: <chdiv type=solid> draws a dashed rule across the choice area between
    # groups of choices, pushing the following choices down a row.
    s = load_dtl(
        '<panel name="p">M<selfld type="menu">Pick'
        '<choice>One<action run="a">'
        '<chdiv type="solid">'
        '<choice selchar="X">Exit<action run="exit" type="exit">'
        '</selfld></panel>'
    )
    rules = [t for t in s.items if isinstance(t, Text) and set(t.text) == {"-"}]
    assert len(rules) == 1
    rule = rules[0]
    ones = [t for t in s.items if isinstance(t, Text) and t.text == "One"]
    exits = [t for t in s.items if isinstance(t, Text) and t.text == "Exit"]
    # the rule sits between the two choices
    assert ones[0].row < rule.row < exits[0].row


def test_choice_divider_default_is_a_blank_separator():
    # A bare <chdiv> (TYPE=NONE default, no text) is a blank separator: it draws
    # nothing but advances the choice flow, so the next choice drops a row.
    with_div = load_dtl(
        '<panel name="p">M<selfld type="menu">Pick'
        '<choice>One<action run="a"><chdiv>'
        '<choice selchar="X">Exit<action run="exit" type="exit"></selfld></panel>'
    )
    without = load_dtl(
        '<panel name="p">M<selfld type="menu">Pick'
        '<choice>One<action run="a">'
        '<choice selchar="X">Exit<action run="exit" type="exit"></selfld></panel>'
    )
    assert not [t for t in with_div.items if isinstance(t, Text) and set(t.text) == {"-"}]
    exit_with = next(t for t in with_div.items if isinstance(t, Text) and t.text == "Exit")
    exit_without = next(t for t in without.items if isinstance(t, Text) and t.text == "Exit")
    assert exit_with.row == exit_without.row + 1   # the divider consumed one row


def test_choice_divider_text_writes_a_caption():
    # <chdiv type=text> (or a bare divider with text) writes the caption line.
    s = load_dtl(
        '<panel name="p">M<selfld type="menu">Pick'
        '<choice>One<action run="a">'
        '<chdiv type="text">More options'
        '<choice selchar="X">Exit<action run="exit" type="exit"></selfld></panel>'
    )
    assert any(isinstance(t, Text) and t.text == "More options" for t in s.items)


def test_lstvar_is_accepted_with_no_display_effect():
    # #53: <lstvar> declares a non-displayed model variable in a <lstfld>. In this
    # display server it has no rendering — a table with an <lstvar> renders
    # byte-for-byte like one without (accepted, not rejected).
    with_var = (
        '<panel name="p">T<lstfld>'
        '<lstvar datavar="hid" line="1">'
        '<lstcol datavar="a" colwidth="6">A</lstfld></panel>'
    )
    without = (
        '<panel name="p">T<lstfld>'
        '<lstcol datavar="a" colwidth="6">A</lstfld></panel>'
    )
    rows = [{"a": "1", "hid": "9"}, {"a": "2", "hid": "8"}]
    assert load_dtl(with_var, rows=rows).render() == load_dtl(without, rows=rows).render()


def test_grphdr_renders_high_intensity_group_heading():
    # #53: <grphdr> renders a high-intensity heading line above a field group,
    # with a leading blank line (COMPACT suppresses it).
    s = load_dtl('<panel name="p">T<area><info>Intro'
                 '<grphdr>Options'
                 '<dtafld datavar="x" entwidth="4">Field</area></panel>')
    hdr = next(t for t in s.items if isinstance(t, Text) and t.text == "Options")
    intro = next(t for t in s.items if isinstance(t, Text) and t.text == "Intro")
    assert hdr.intensity is DisplayIntensity.HIGH and hdr.role == "heading"
    assert hdr.row == intro.row + 2                 # leading blank before the header
    compact = load_dtl('<panel name="p"><area><info>Intro'
                       '<grphdr compact>Options</area></panel>')
    hc = next(t for t in compact.items if isinstance(t, Text) and t.text == "Options")
    ic = next(t for t in compact.items if isinstance(t, Text) and t.text == "Intro")
    assert hc.row == ic.row + 1                      # COMPACT: no leading blank


def test_grphdr_format_justifies_within_width():
    # FORMAT positions the heading within WIDTH: END right-justifies it.
    s = load_dtl('<panel name="p" width="30">T<area>'
                 '<grphdr format="end" width="10">End</area></panel>')
    hdr = next(t for t in s.items if isinstance(t, Text) and t.text == "End")
    assert hdr.col == 1 + (10 - len("End"))          # right-justified within width 10


def test_grphdr_headline_draws_a_dashed_rule_around_the_text():
    # HEADLINE=YES wraps the heading text in a dashed rule.
    s = load_dtl('<panel name="p" width="30">T<area>'
                 '<grphdr headline="yes" format="center">Group</area></panel>')
    rule = next(t for t in s.items if isinstance(t, Text)
                and "Group" in t.text and "-" in t.text)
    assert rule.text.startswith("-") and rule.text.endswith("-")
    assert " Group " in rule.text
    assert rule.intensity is DisplayIntensity.HIGH


def test_grphdr_fmtwidth_justifies_within_its_own_field():
    # FMTWIDTH is the field the heading is FORMAT-justified within (vs WIDTH).
    s = load_dtl('<panel name="p" width="40">T<area>'
                 '<grphdr format="end" fmtwidth="8" width="30">Hi</area></panel>')
    hdr = next(t for t in s.items if isinstance(t, Text) and t.text == "Hi")
    assert hdr.col == 1 + (8 - len("Hi"))            # right-justified within FMTWIDTH


def test_choice_divider_gutter_insets_the_rule():
    # #53: <chdiv> GUTTER=n insets the divider rule n characters at each end.
    def rule(markup):
        s = load_dtl('<panel name="p" width="40">M<selfld type="menu">Pick'
                     '<choice>One<action run="a">' + markup +
                     '<choice selchar="X">Exit<action run="exit" type="exit"></selfld></panel>')
        return next(t for t in s.items if isinstance(t, Text) and set(t.text) == {"-"})
    full = rule('<chdiv type="solid">')
    inset = rule('<chdiv type="solid" gutter="3">')
    assert inset.col == full.col + 3
    assert len(inset.text) == len(full.text) - 6


def test_grphdr_div_draws_dividers_at_divloc():
    # DIV=SOLID with DIVLOC=BOTH draws a dashed rule before and after the heading.
    s = load_dtl('<panel name="p" width="20">T<area>'
                 '<grphdr div="solid" divloc="both">Hdr</area></panel>')
    rules = [t for t in s.items if isinstance(t, Text) and set(t.text) == {"-"}]
    hdr = next(t for t in s.items if isinstance(t, Text) and t.text == "Hdr")
    assert len(rules) == 2
    assert rules[0].row < hdr.row < rules[1].row


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


def test_list_field_paged_window_status_and_bottom_marker():
    # #281: when a table is a *slice* of a larger set, row_offset/row_total make the
    # "ROW x TO y OF z" status reflect the real window, and "BOTTOM OF DATA" is
    # drawn only when the last row of the full set is on screen.
    src = ('<panel name="p" width="60">Members<area><lstfld>'
           '<lstcol datavar=a colwidth=6>A</lstfld></area></panel>')
    # a middle/first page of 3 rows out of 10, starting at offset 0
    page1 = load_dtl(src, rows=[{"a": str(i)} for i in range(3)],
                     row_offset=0, row_total=10)
    row0 = [t for t in page1.items if getattr(t, "row", None) == 0]
    assert any(getattr(t, "text", "") == "ROW 1 TO 3 OF 10" for t in row0)
    assert not any("BOTTOM OF DATA" in getattr(t, "text", "") for t in page1.items)
    # the last page (offset 7, showing rows 8-10) reaches the end
    page_last = load_dtl(src, rows=[{"a": str(i)} for i in range(7, 10)],
                         row_offset=7, row_total=10)
    row0 = [t for t in page_last.items if getattr(t, "row", None) == 0]
    assert any(getattr(t, "text", "") == "ROW 8 TO 10 OF 10" for t in row0)
    assert any("BOTTOM OF DATA" in getattr(t, "text", "") for t in page_last.items)


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
                  '<divider/><lstfld>'
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


def test_panel_title_kept_when_row0_is_free():
    # With nothing else on row 0, the centered content title still renders.
    s = load_dtl('<panel name="p" width="20">My Title'
                 '<info>body</info></panel>')
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
    s = load_dtl('<panel name="p">Title<info>Trailing')
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
        "",                                              # blank before the selection field
        " Choose one of the following  Check valid branches",
        " __  1.  New                   _ North Branch",
        "     2.  Renewal               _ South Branch",
        "     3.  Replacement           _ East Branch",
        "                               _ West Branch",
        "",                                              # blank before the command area
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
        "",                                          # blank before the command area
        "   ________",
    ])
    assert _ascii_snapshot(s) == expected


# Admonition reference figures (ATTENTION/CAUTION/WARNING, NOTE/NT/NOTEL). These
# help panels omit WIDTH, so we render at the DTL default (76) — the reference
# figures were displayed narrower, so the wrap points differ but the admonition
# *format* matches. The runtime F-key area is ISPF chrome, not markup.

def test_dl_headers_reference_figure_snapshot():
    """DDHD-reference Figure 102 (Prefix Help): <dthd>/<ddhd> render a heading
    row — term heading at the margin, description heading at TSIZE — followed by a
    blank line before the items. (Our list items are tight, as everywhere; the
    figure's inter-item blanks are ISPDTLC list spacing, not part of #120.)"""
    src = ("<!DOCTYPE DM SYSTEM>\n<HELP NAME=ddhd DEPTH=18>Prefix Help\n<AREA>\n<INFO>\n"
           "<P>The following list defines each of the valid prefixes.\n"
           "<DL TSIZE=12>\n"
           "<DTHD>Prefix\n<DDHD>Meaning\n"
           "<DT>AU\n<DD>Automotive\n<DT>HB\n<DD>Health and beauty\n"
           "<DT>LG\n<DD>Lawn and garden\n<DT>SG\n<DD>Sporting goods\n"
           "</DL>\n</INFO>\n</AREA>\n</HELP>")
    assert _ascii_snapshot(load_dtl(src)) == "\n".join([
        "                                  Prefix Help",
        "",                                          # title/body separator
        " The following list defines each of the valid prefixes.",
        "",                                          # ISPDTLC blank line before the list
        " Prefix      Meaning",                      # dthd @ base, ddhd @ base+tsize
        "",                                          # blank between heading and items
        " AU          Automotive",
        " HB          Health and beauty",
        " LG          Lawn and garden",
        " SG          Sporting goods",
    ])


def test_textline_builds_a_zoned_panel_title():
    # #117: <textline>/<textseg> replace the panel title. A segment with EXPAND is
    # the centre pivot — segments before it are left-justified, after it right-
    # justified (the classic ISPF time / title / date line).
    s = load_dtl(
        "<panel name=t width=80><textline>"
        "<textseg>10:30"
        "<textseg expand=both>My Panel Title"
        "<textseg>2026/07/05"
        "</textline><topinst>Intro</panel>")
    at = {it.text: it.col for it in s.items if isinstance(it, Text)}
    assert at["10:30"] == 1                                   # left zone at the margin
    assert at["My Panel Title"] == (80 - len("My Panel Title")) // 2   # centred
    assert at["2026/07/05"] == 80 - 1 - len("2026/07/05")     # right-justified
    assert s.title == "10:30 My Panel Title 2026/07/05"       # accumulated metadata
    # The body flows below the title line.
    assert next(it.row for it in s.items if it.text == "Intro") >= 1


def test_ga_reserves_a_region_framed_by_dividers():
    # #117: <ga> reserves DEPTH lines for a graphic area, framed by an optional
    # DIV divider before and after. The graphic itself (GDDM, #102) can't render
    # on a text terminal, so the region is blank; only the DIV rules draw.
    s = load_dtl("<panel name=g width=50><area>"
                 "<p>Above"
                 "<ga name=chart depth=3 div=solid>"
                 "<p>Below</area></panel>")
    rules = sorted(it.row for it in s.items
                   if isinstance(it, Text) and set(it.text.strip()) == {"-"})
    assert len(rules) == 2                       # a divider before and after
    assert rules[1] - rules[0] == 4              # depth 3 + the leading divider row
    below = next(it.row for it in s.items if it.text == "Below")
    assert below > rules[1]                      # body flows past the graphic area

    # DIV=NONE (default) reserves the space silently — no rule drawn.
    s2 = load_dtl("<panel name=g width=50><area><p>A"
                  "<ga name=c depth=2><p>B</area></panel>")
    assert not any(set(it.text.strip()) == {"-"} for it in s2.items
                   if isinstance(it, Text))
    assert next(it.row for it in s2.items if it.text == "B") \
        >= next(it.row for it in s2.items if it.text == "A") + 3   # 2 reserved + flow


def test_pandef_supplies_panel_defaults():
    # #117: <pandef id=…> defines reusable defaults; a <panel pandef=id> inherits
    # them (HELP/DEPTH/WIDTH/…), with the panel's own attributes taking precedence.
    s = load_dtl("<pandef id=printdef help=prnthlp depth=20 width=70>"
                 "<panel name=panel01 pandef=printdef>A Panel"
                 "<area><p>Body</area></panel>")
    assert s.width == 70                          # inherited
    assert s.depth == 20                          # inherited
    assert s.help == "prnthlp"                    # inherited
    assert s.title == "A Panel"

    # The panel's own attribute wins over the pandef default.
    s2 = load_dtl("<pandef id=d width=70 depth=20>"
                  "<panel name=p pandef=d width=40>Own"
                  "<area><p>x</area></panel>")
    assert s2.width == 40                          # panel own overrides pandef


def test_helpdef_supplies_help_panel_defaults():
    # #54: <helpdef id=…> is the help-panel analogue of <pandef> — a <help HELPDEF=id>
    # inherits its shared defaults (HELP/WIDTH/DEPTH/…), the help panel's own winning.
    s = load_dtl("<helpdef id=hd help=exthlp width=50 depth=12>"
                 "<help name=h1 helpdef=hd>Help Title"
                 "<area><p>Help body</area></help>")
    assert s.width == 50                           # inherited
    assert s.depth == 12                           # inherited
    assert s.help == "exthlp"                      # inherited extended-help panel
    assert s.title == "Help Title"

    # The help panel's own attribute wins over the helpdef default.
    s2 = load_dtl("<helpdef id=hd width=50 depth=12>"
                  "<help name=h2 helpdef=hd width=40>Own"
                  "<area><p>x</area></help>")
    assert s2.width == 40                          # help own overrides helpdef

    # A <helpdef> renders nothing on its own (it only supplies defaults).
    s3 = load_dtl("<helpdef id=hd width=50 depth=12>")
    assert s3.items == []

    # #54: the FULL default set is inherited — KEYLIST/WINDOW/WINTITLE too, not just
    # the geometry — and the help panel's own attribute still overrides.
    s4 = load_dtl('<helpdef id=hd keylist=HKEYS window=yes wintitle="Help Win">'
                  '<help name=h4 helpdef=hd>Help<area><p>x</area></help>')
    assert s4.keylist_ref == "HKEYS"                # inherited key-list reference
    assert s4.window is True                        # inherited pop-up flag
    assert s4.window_title == "Help Win"            # inherited window title
    s5 = load_dtl('<helpdef id=hd window=yes wintitle="Def">'
                  '<help name=h5 helpdef=hd window=no wintitle="Own">Help'
                  '<area><p>x</area></help>')
    assert s5.window is False and s5.window_title == "Own"   # help own overrides


def test_textline_no_expand_centres_the_whole_line():
    # With no EXPAND the accumulated segments centre as the panel title.
    s = load_dtl("<panel name=t width=40><textline>"
                 "<textseg>Alpha<textseg>Beta</textline><p>Body</panel>")
    title = next(it for it in s.items if it.text == "AlphaBeta")
    assert title.row == 0
    assert title.col == (40 - len("AlphaBeta")) // 2
    assert s.title == "AlphaBeta"


def test_textseg_width_pads_the_segment():
    # #117: TEXTSEG WIDTH reserves a fixed field for the segment (padded), so the
    # following segment starts at a fixed column regardless of the text length.
    s = load_dtl("<panel name=t width=40><textline>"
                 "<textseg width=10>Hi<textseg>End</textline><p>Body</panel>")
    # the composed title pads "Hi" to width 10 before "End"
    assert s.title == "Hi        End"


def test_info_indent_shifts_content_and_clears_after():
    # #123: <info indent=n> shifts the whole information region (its text, nested
    # paragraphs, and lists) right by n columns; a sibling block after </info>
    # returns to the box column.
    s = load_dtl('<panel name=p width=50><area>'
                 '<info indent=5><p>Indented para<ul><li>Bullet</ul></info>'
                 '<p>Outside</area></panel>')
    at = {it.text.strip(): it.col for it in s.items
          if isinstance(it, Text) and it.text.strip()}
    assert at["Indented para"] == 6          # 1 + 5
    assert at["o"] == 6                       # the bullet marker shifts too
    assert at["Bullet"] == 10                 # bullet text (marker + 4)
    assert at["Outside"] == 1                 # sibling after </info> back at margin

    # A plain <info> (no indent) is unchanged — the common bundled case.
    plain = load_dtl('<panel name=p width=50><area><info><p>Plain</info></area></panel>')
    assert next(it.col for it in plain.items
                if isinstance(it, Text) and it.text.strip() == "Plain") == 1


def test_notel_space_sets_item_indent():
    # #123: SPACE sets a note-list item's text indentation — YES → 3 columns,
    # NO/absent → the default 4. An <li> SPACE overrides the enclosing <notel>.
    def col(markup):
        s = load_dtl('<panel name=p width=50><area><info>' + markup
                     + '</info></area></panel>')
        return next(it.col for it in s.items
                    if isinstance(it, Text) and it.text.startswith("Alpha"))

    default = col('<notel><li>Alpha</notel>')
    assert col('<notel space=yes><li>Alpha</notel>') == default - 1   # 4 → 3
    assert col('<notel><li space=yes>Alpha</notel>') == default - 1   # li overrides
    assert col('<ul><li>Alpha</ul>') == default                      # other lists unchanged


def test_dl_format_positions_term_within_tsize():
    # #123: <dl format=> places the DT term within its TSIZE column (START left,
    # CENTER centred, END right); the description column stays at base+TSIZE.
    def cols(fmt):
        s = load_dtl(f'<panel name=p width=40><area><info><dl tsize=8 format={fmt}>'
                     '<dt>AP<dd>Apple</dl></info></area></panel>')
        term = next(it.col for it in s.items if isinstance(it, Text) and it.text == "AP")
        desc = next(it.col for it in s.items
                    if isinstance(it, Text) and it.text.strip() == "Apple")
        return term, desc

    start_t, start_d = cols("start")
    center_t, _ = cols("center")
    end_t, end_d = cols("end")
    assert start_t < center_t < end_t                    # term shifts right
    assert end_t + len("AP") <= end_d                    # END term stays within TSIZE
    assert start_d == end_d                              # description column unmoved


def test_dl_leading_blank_and_noskip():
    # #123/#210: ISPDTLC inserts a blank line before a definition list; NOSKIP
    # (or COMPACT) suppresses it.
    def rows(attrs):
        s = load_dtl('<panel name=p width=40><area><info><p>Intro'
                     f'<dl tsize=6 {attrs}><dt>A<dd>Apple</dl></info></area></panel>')
        return [ln.rstrip() for ln in _ascii_snapshot(s).split("\n")]

    plain = rows("")
    intro = next(i for i, ln in enumerate(plain) if "Intro" in ln)
    assert plain[intro + 1].strip() == ""              # blank line before the list
    assert "A" in plain[intro + 2]                     # then the first term

    nos = rows("noskip")
    i2 = next(i for i, ln in enumerate(nos) if "Intro" in ln)
    assert "A" in nos[i2 + 1]                           # no blank — term immediately after


# #210: every ISPDTLC block element takes a leading blank line ("skip") before it,
# which NOSKIP and COMPACT suppress. The DTL Guide documents the skip in prose (the
# guide's compressed ASCII figures omit it); e.g. UL/OL/SL: "The conversion utility
# adds a blank line before the first item in the list"; NOTE/NT/NOTEL/LINES/XMP/
# FIG/LP: NOSKIP "causes the blank line normally placed before the … to be skipped".
# Each case here follows the tag's first line of body content with a marker word, so
# the assertions probe the row immediately after the intro paragraph.
# (tag → template, marker word on the block's FIRST rendered line). We probe the
# row just above that marker: blank by default, non-blank once NOSKIP/COMPACT drop
# the skip. <lp> is valid only inside a list, so its <ul> parent (which takes its
# own separate leading skip) precedes it; the marker "Body" is the LP's own line.
_BLOCK_SKIP_CASES = {
    "lines": ("<lines %s>Body</lines>", "Body"),
    "xmp":   ("<xmp %s>Body</xmp>", "Body"),
    "lp":    ("<ul compact><li>Item<lp %s>Body</ul>", "Body"),
    "ul":    ("<ul %s><li>Body</ul>", "Body"),
    "ol":    ("<ol %s><li>Body</ol>", "Body"),
    "sl":    ("<sl %s><li>Body</sl>", "Body"),
    "note":  ("<note %s>Body</note>", "Body"),
    "nt":    ("<nt %s>Body</nt>", "Body"),
    "notel": ("<notel %s><li>Body</notel>", "Notes:"),   # heading is the first line
    "fig":   ("<fig %s frame=none><p>Body</fig>", "Body"),
}


@pytest.mark.parametrize("tag,tpl,marker", [(k, v[0], v[1])
                                            for k, v in _BLOCK_SKIP_CASES.items()])
def test_block_element_leading_skip_and_noskip_compact(tag, tpl, marker):
    # The modifier lands on the block whose skip we probe (for <lp>, on the <lp>).
    def row_above_block(mod):
        src = ('<panel name=p width=40><area><info><p>Intro'
               + (tpl % mod) + '</info></area></panel>')
        lines = [ln.rstrip() for ln in _ascii_snapshot(load_dtl(src)).split("\n")]
        i = next(k for k, ln in enumerate(lines) if marker in ln)
        return lines[i - 1].strip()

    # By default the block's first rendered line is preceded by a blank line;
    # NOSKIP/COMPACT suppress it, butting the block against the line above.
    assert row_above_block("") == "", f"{tag}: expected a leading blank before the block"
    for mod in ("noskip", "compact"):
        assert row_above_block(mod) != "", f"{tag}: {mod} should suppress the leading skip"


def test_dl_multicolumn_tsize_lays_terms_side_by_side():
    # #120: TSIZE='w1 w2 …' gives multiple definition-term COLUMNS; one <dt> per
    # width lays them side by side, with the <dd> past every term column.
    s = load_dtl("<panel name=p width=50><area><info><dl tsize=\"6 6\">"
                 "<dt>Alpha<dt>Beta<dd>An entry.</dl></info></area></panel>")
    at = {it.text: it.col for it in s.items if isinstance(it, Text)}
    assert at["Alpha"] == 1                       # column 0 at the margin
    assert at["Beta"] == 1 + 6 + 1                # column 1 past width 6 + a gap
    assert at["An entry."] == 1 + 6 + 6 + 1       # description past both columns + gap
    rows = {it.text: it.row for it in s.items if isinstance(it, Text)}
    assert rows["Alpha"] == rows["Beta"] == rows["An entry."]   # share the entry row


def test_dl_vertical_dividers_between_columns():
    # #120: <dtdiv>/<ptdiv> draw a vertical `|` between term columns; <dthdiv>
    # between heading columns — in the gap before the following column.
    s = load_dtl("<panel name=p width=50><area><info><dl tsize=\"6 6\">"
                 "<dthd>Code<dthdiv><dthd>Name<ddhd>Meaning"
                 "<dt>AP<dtdiv><dt>App<dd>Appliances"
                 "</dl></info></area></panel>")
    bars = [(it.row, it.col) for it in s.items
            if isinstance(it, Text) and it.text == "|"]
    assert len(bars) == 2                        # one header divider, one term divider
    gap_col = 1 + 6                              # base + width of column 0
    assert all(c == gap_col for _, c in bars)    # both sit in the column-0/1 gap
    hdr_row = next(it.row for it in s.items if it.text == "Code")
    item_row = next(it.row for it in s.items if it.text == "AP")
    assert sorted(r for r, _ in bars) == sorted({hdr_row, item_row})


def test_dl_dtseg_stacks_term_segments():
    # #120: <dtseg> adds an extra line of the definition term, stacked directly
    # under the term text in its column; the description flows alongside.
    s = load_dtl("<panel name=p width=50><area><info><dl tsize=8>"
                 "<dt>LOCATE<dtseg>LOC or<dtseg>L"
                 "<dd>Positions the display.</dl></info></area></panel>")
    at = {it.text: (it.row, it.col) for it in s.items if isinstance(it, Text)}
    r0 = at["LOCATE"][0]
    assert at["LOC or"] == (r0 + 1, at["LOCATE"][1])   # stacked in the term column
    assert at["L"] == (r0 + 2, at["LOCATE"][1])
    assert at["Positions the display."][0] == r0        # description alongside the term


def test_dl_indent_shifts_the_whole_list():
    # #123: <dl indent=n> indents the whole definition list from the left margin —
    # headers, terms, descriptions, and dividers all shift right by n columns.
    def at(markup):
        s = load_dtl('<panel name=p width=44><area><info>' + markup
                     + '</info></area></panel>')
        return {it.text.strip(): it.col for it in s.items
                if isinstance(it, Text) and it.text.strip()}

    body = ('<dl tsize=6{I}><dthd>Code<ddhd>Name'
            '<dt>AP<dd>Apple</dl>')
    plain = at(body.format(I=""))
    shifted = at(body.format(I=" indent=6"))
    for key in ("Code", "Name", "AP", "Apple"):
        assert shifted[key] == plain[key] + 6            # every element shifts by 6


def test_dl_divend_draws_an_end_rule_and_split_moves_long_desc():
    # #123: DL/PARML DIVEND=YES draws a dashed rule across the list as it closes;
    # a term too long for its TSIZE column splits its description onto the next line.
    from screen import Text
    s = load_dtl('<panel name="p" width="30">T<area>'
                 '<dl tsize="6" divend="yes"><dt>A<dd>Apple<dt>B<dd>Berry</dl>'
                 '<p>After</area></panel>')
    rules = [t for t in s.items if isinstance(t, Text) and set(t.text) == {"-"}]
    after = next(t for t in s.items if isinstance(t, Text) and t.text == "After")
    assert len(rules) == 1                            # the end rule
    assert rules[0].row < after.row                   # the list closed above "After"
    # no DIVEND → no end rule
    plain = load_dtl('<panel name="p" width="30">T<area>'
                     '<dl tsize="6"><dt>A<dd>Apple</dl></area></panel>')
    assert not [t for t in plain.items if isinstance(t, Text) and set(t.text) == {"-"}]
    # a term wider than TSIZE puts its description on the following line (split)
    split = load_dtl('<panel name="p" width="40">T<area>'
                     '<dl tsize="4"><dt>LONGTERM<dd>the description</dl></area></panel>')
    term = next(t for t in split.items if isinstance(t, Text) and t.text == "LONGTERM")
    desc = next(t for t in split.items if isinstance(t, Text) and "description" in t.text)
    assert desc.row > term.row                        # description split to next line


def test_dl_list_divider_types():
    # #120: <dldiv> draws a horizontal divider across a definition list. TYPE=NONE
    # (default) is a blank spacer; SOLID/DASH a dashed rule; TEXT lays out the
    # divider text (FORMAT positions it). <pldiv> is the <parml> equivalent.
    def rows(markup):
        s = load_dtl('<panel name=p width=40><area><info><dl tsize=6>'
                     + markup + '</dl></info></area></panel>')
        return _ascii_snapshot(s).split("\n")

    dash = rows('<dt>A<dd>Apple<dldiv type=dash><dt>B<dd>Berry')
    rule = next(ln for ln in dash if set(ln.strip()) == {"-"})   # a dashed rule row
    assert len(rule.strip()) > 5

    text = rows('<dt>A<dd>Apple<dldiv type=text format=center>More<dt>B<dd>Berry')
    assert any(ln.strip() == "More" for ln in text)              # divider text shown

    # TYPE=NONE (default) draws nothing — a blank spacer between the two entries.
    none = rows('<dt>A<dd>Apple<dldiv><dt>B<dd>Berry')
    assert not any(set(ln.strip()) == {"-"} for ln in none)      # no rule drawn
    a_row = next(i for i, ln in enumerate(none) if "Apple" in ln)
    assert none[a_row + 1].strip() == ""                         # blank spacer row


def test_dl_list_divider_gutter_insets_the_rule():
    # #120: GUTTER=n insets the divider rule n characters at each end (GAP=YES is
    # the n=1 shorthand); absent, the rule spans the list full-width.
    from screen import Text
    def rule(markup):
        s = load_dtl('<panel name=p width=40><area><info><dl tsize=6>'
                     + markup + '</dl></info></area></panel>')
        return next(t for t in s.items if isinstance(t, Text) and set(t.text) == {"-"})
    full = rule('<dt>A<dd>x<dldiv type=solid><dt>B<dd>y')
    inset = rule('<dt>A<dd>x<dldiv type=solid gutter=4><dt>B<dd>y')
    gap = rule('<dt>A<dd>x<dldiv type=solid gap=yes><dt>B<dd>y')
    assert inset.col == full.col + 4                    # start inset by the gutter
    assert len(inset.text) == len(full.text) - 8       # both ends inset by 4
    assert gap.col == full.col + 1                      # GAP=YES → gutter 1


def test_dl_headers_compact_suppresses_blank():
    # COMPACT on the <dl> drops the blank line between the heading and the items.
    src = ("<panel name=p width=40><area><info>"
           "<dl tsize=8 compact><dthd>Code<ddhd>Name"
           "<dt>AP<dd>Appliances</dl></info></area></panel>")
    lines = _ascii_snapshot(load_dtl(src)).split("\n")
    hdr = next(i for i, ln in enumerate(lines) if "Code" in ln)
    assert "Name" in lines[hdr]                      # heading row
    assert "AP" in lines[hdr + 1]                    # item immediately follows (no blank)


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
        "",                                          # title/body separator
        " The DELETE command erases the specified file from storage.",
        " CAUTION:",
        " Issuing the DELETE command permanently removes the file from storage. There is",
        " no possibility of recovery.",
        "",                                          # blank before the closing paragraph
        " You can exit from the DELETE operation by pressing F12.",
    ])


def test_nt_reference_figure_snapshot():
    """NT-reference Figure 141: "Note:" then the body hung indented under the text.

    The nested <p> ("If the librarian ...") hangs under the note body too, at the
    same indent as the first paragraph — matching the reference figure (#219).

    A leading blank line precedes the note: the DTL Guide NT reference states
    NOSKIP "causes the note to be formatted without creating a blank line before
    the note", so ISPDTLC inserts that blank by default (#210). The guide's ASCII
    Figure 141 is compressed and omits inter-block blanks — the P reference proves
    it: its source says "Notice the line skip between the paragraphs" yet Figure
    143 shows none. We follow the documented spacing model, not the compressed art."""
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
        "",                                          # title/body separator
        " This entry screen allows you to locate a desired book or periodical by",
        " entering the title in the entry field.",
        "",                                          # leading skip before the note (#210)
        " Note: If the item you are trying to locate is not in stock and you would like",
        "       to reserve it, please see the librarian at the front desk.",
        "",                                          # blank before the nested paragraph
        "       If the librarian is not there, please do not yell for help. This is a",
        "       library!",                           # nested <p> hangs under the note
    ])


def test_nt_nested_block_hangs_and_boundary_clears():
    # #219: a nested <p> inside <nt> flows at the note's hanging indent, and a
    # sibling <p> after </nt> returns to the enclosing box column.
    s = load_dtl(
        '<panel name="p" width="60"><area>'
        '<nt>Reserve it at the desk.'
        '<p>Come back later if nobody is there.'
        '</nt>'
        '<p>This paragraph is outside the note.'
        '</area></panel>')
    at = {it.text: it.col for it in s.items if isinstance(it, Text)}
    body_col = at["Reserve it at the desk."]              # note body (hung past "Note: ")
    assert at["Note:"] == 1                               # heading at the margin
    assert body_col > 1
    assert at["Come back later if nobody is there."] == body_col   # nested <p> hangs
    assert at["This paragraph is outside the note."] == 1          # sibling back at margin


def test_notel_reference_figure_snapshot():
    """NOTEL-reference Figure 140: "Notes:" + a blank line + numbered items.

    A leading blank line precedes the note list: the DTL Guide NOTEL reference
    states NOSKIP "causes the list to format without creating a blank line before
    the first line of the list", so ISPDTLC inserts that blank by default (#210).
    (Figure 140's compressed ASCII omits it, like the other reference figures.)"""
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
        "",                                          # title/body separator
        " This entry screen allows you to locate a desired book or periodical by",
        " entering the title in the entry field.",
        "",                                          # leading skip before the note list (#210)
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
        "",                                          # blank before the command area
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
        "",                                          # blank before the command area
        "   ________",
    ])


def test_nested_unordered_lists_matches_guide_figure():
    # IBM DTL Guide "Nested Unordered Lists" figure: a centered title, then o/-/--
    # bullets by depth with increasing indentation. (Verbatim guide source.)
    # Each UL (outer and every nested one) takes a leading blank line: the DTL
    # Guide UL reference states "The conversion utility adds a blank line before
    # the first item in the list", suppressed only by COMPACT/NOSKIP (#210). None
    # of these lists is COMPACT, so all three get the skip — corroborated by the OL
    # reference (Figure 142), which codes its nested list <OL COMPACT> precisely to
    # suppress that otherwise-present blank.
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
        # row 1 blank: leading skip before the outer <ul> (#210)
        Text(2, 1, "o", N),  Text(2, 5, "First level, first item", N),
        Text(3, 1, "o", N),  Text(3, 5, "First level, second item", N),
        # row 4 blank: leading skip before the second-level <ul>
        Text(5, 5, "-", N),  Text(5, 9, "Second level, first item", N),
        Text(6, 5, "-", N),  Text(6, 9, "Second level, second item", N),
        # row 7 blank: leading skip before the third-level <ul>
        Text(8, 9, "--", N), Text(8, 13, "Third level, only item", N),
        Text(9, 1, "o", N),  Text(9, 5, "Back to the first level", N),
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
    # The title/body separator (a blank below the title) puts the first paragraph
    # on row 2. The outer <OL> then takes its guide-mandated leading blank ("The
    # conversion utility adds a blank line before the first item in the list");
    # the inner <OL COMPACT> takes none (COMPACT suppresses it — the reason the
    # guide codes it COMPACT), and the trailing <p> takes its own leading blank
    # (#210).
    assert s.items == [
        Text(0, 20, "Widget Assembly Help", N),
        Text(2, 1, "To assemble your new Widget, you should:", N),
        # row 3 blank: leading skip before the outer <ol> (#210)
        Text(4, 1, "1.", N),
        Text(4, 5, "Attach the gizmo flexure component to the main", N),
        Text(5, 5, "steering mechanism of the doohickey.", N),
        # no blank before the nested <ol compact> (COMPACT suppresses the skip)
        Text(6, 5, "a.", N),
        Text(6, 9, "If slot A fits snugly on retaining pin B, proceed", N),
        Text(7, 9, "to step 2.", N),
        Text(8, 5, "b.", N),
        Text(8, 9, "If slot A does not fit snugly on retaining pin B,", N),
        Text(9, 9, "throw the Widget away and buy a new one.", N),
        Text(10, 1, "2.", N),
        Text(10, 5, "Use a screwdriver to turn the power drive unit on.", N),
        Text(11, 1, "3.", N),
        Text(11, 5, "Stand back and watch the fun!", N),
        # A <p> after the list items gets its guide-mandated leading blank.
        Text(13, 5, "Wake up the kids and call the neighbors, they won't", N),
        Text(14, 5, "want to miss it!", N),
    ]


def test_implicit_end_does_not_break_explicitly_closed_panels():
    # A panel with explicit </info></panel> renders the same as one that relies
    # on the implicit-end logic (DTL routinely omits end tags).
    explicit = load_dtl("<panel>Menu<info>hi</info></panel>")
    implicit = load_dtl("<panel>Menu<info>hi")
    assert explicit.render() == implicit.render()


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


# ── flow layout (<area>/<region>) ────────────────────────────────────────────

def test_area_flows_rows_and_derives_the_entry_column():
    s = load_dtl(
        '<panel><area>'
        '<dtafld datavar="userid" entwidth="8">Userid   ===></dtafld>'
        '<dtafld datavar="pw" entwidth="8">Password ===></dtafld>'
        '</area></panel>'
    )
    # Prompts at col 1 on flowing rows 5, 6; entries after the 13-char prompt
    # plus the default 1-col gap → col 15.
    assert s.items[0] == Text(0, 1, "Userid   ===>", DisplayIntensity.NORMAL)
    assert (s.items[1].row, s.items[1].col, s.items[1].name) == (0, 15, "userid")
    assert s.items[2] == Text(1, 1, "Password ===>", DisplayIntensity.NORMAL)
    assert (s.items[3].row, s.items[3].col, s.items[3].name) == (1, 15, "pw")


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


def test_dtacol_defaults_outline_deswidth_for_child_fields():
    # #122: a <dtacol> defaults OUTLINE / DESWIDTH for its <dtafld>s; each field's
    # own value overrides the column default.
    from screen import Outline, Text
    s = load_dtl('<panel name="p" width="60"><area>'
                 '<dtacol outline="box" deswidth="4" pmtwidth="6" entwidth="5">'
                 '<dtafld datavar="a">A<dtafldd>LongDescription</dtafldd></dtafld>'
                 '<dtafld datavar="b" outline="none">B</dtafld>'
                 '</dtacol></area></panel>')
    a = next(f for f in s.items if isinstance(f, Field) and f.name == "a")
    b = next(f for f in s.items if isinstance(f, Field) and f.name == "b")
    assert a.outline is Outline.BOX                     # inherited from the column
    assert b.outline is Outline.NONE                    # field own overrides
    # DESWIDTH=4 truncates the inherited description
    assert any(isinstance(t, Text) and t.text == "Long" for t in s.items)


def test_dtafld_autotab_recorded_as_metadata():
    # #122: AUTOTAB=YES is recorded on the field (a client autotab behaviour with no
    # 3270 data-stream bit); it does not change the rendered stream.
    on = load_dtl('<panel name="p"><area>'
                  '<dtafld datavar="a" entwidth="4" autotab="yes">A</dtafld></area></panel>')
    off = load_dtl('<panel name="p"><area>'
                   '<dtafld datavar="a" entwidth="4">A</dtafld></area></panel>')
    fa = next(f for f in on.items if isinstance(f, Field))
    fb = next(f for f in off.items if isinstance(f, Field))
    assert fa.autotab is True and fb.autotab is False
    assert on.render() == off.render()                  # metadata only, no stream change


# ── <dtafld> USAGE / PMTLOC (#122) ───────────────────────────────────────────

def test_dtafld_usage_out_is_a_protected_display_field():
    # usage=out renders the variable's value as protected text — no input field.
    s = load_dtl(
        '<panel><area>'
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
        '<panel><area>'
        '<dtafld datavar="x" usage="in" entwidth="8">Name</dtafld>'
        '</area></panel>'
    )
    assert [i for i in s.items if isinstance(i, Field)]         # still editable


def test_dtafld_cua_leader_dots_and_output_colon():
    # PMTFMT=CUA (default): a prompt shorter than PMTWIDTH is padded with CUA
    # leader dots; USAGE=out ends the prompt with a colon (the DTAFLD figure).
    s = load_dtl(
        '<panel><area><dtacol pmtwidth="12">'
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
    ispf = load_dtl('<panel><area><dtafld datavar="x" '
                    'pmtwidth="12" pmtfmt="ispf">Name</dtafld></area></panel>')
    assert any(t.text == "Name".ljust(8) + "===>"       # rightmost 4 bytes
               for t in ispf.items if isinstance(t, Text))
    none = load_dtl('<panel><area><dtafld datavar="x" '
                    'pmtwidth="12" pmtfmt="none">Name</dtafld></area></panel>')
    assert any(t.text == "Name" for t in none.items if isinstance(t, Text))


def test_size_attributes_tolerate_star_and_list_forms():
    # Hardening sweep: size attributes the docs allow as * / ** / quoted-list were
    # parsed with a bare int() and crashed. They must now fall back gracefully
    # (MSGMBR WIDTH, LSTCOL COLWIDTH, SELFLD ENTWIDTH).
    load_dtl('<msgmbr name="m" width="*"><msg msgid="X1">hi</msg></msgmbr>')
    load_dtl('<panel><selfld entwidth="2 2"><choice>A</selfld></panel>')
    # a LSTCOL with COLWIDTH=* falls back to the heading width (no crash)
    hdr = load_dtl('<panel><lstfld>'
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
        '<panel><area>'
        '<dtafld datavar="t" entwidth="20" pmtloc="above">Title</dtafld>'
        '<dtafld datavar="u" entwidth="8">Next</dtafld>'
        '</area></panel>'
    )
    prompt = next(t for t in s.items if isinstance(t, Text) and t.text == "Title")
    fld = next(f for f in s.items if isinstance(f, Field) and f.name == "t")
    assert prompt.row == 0 and fld.row == 1 and fld.col == 1    # prompt above, field below
    nxt = next(f for f in s.items if isinstance(f, Field) and f.name == "u")
    assert nxt.row == 2                                         # flow advanced past 2 rows


def test_divider_draws_a_rule_across_the_flow():
    s = load_dtl(
        '<panel><area><info>above</info><divider>'
        '<info>below</info></area></panel>'
    )
    texts = [i for i in s.items if isinstance(i, Text)]
    assert texts[0] == Text(0, 1, "above", DisplayIntensity.NORMAL)
    rule = texts[1]
    assert rule.row == 1 and rule.col == 1 and set(rule.text) == {"-"}
    assert texts[2] == Text(2, 1, "below", DisplayIntensity.NORMAL)  # flow resumed


def test_divider_type_none_is_a_blank_spacer():
    # TYPE=NONE/BLANK draws no rule but still consumes a row (a blank divider),
    # whereas the default/SOLID divider draws a rule.
    for dtype in ("none", "blank"):
        s = load_dtl(
            f'<panel><area><info>above</info>'
            f'<divider type="{dtype}"><info>below</info></area></panel>'
        )
        texts = [i for i in s.items if isinstance(i, Text)]
        assert not any(set(t.text) == {"-"} for t in texts)          # no rule
        assert texts[-1] == Text(2, 1, "below", DisplayIntensity.NORMAL)  # row consumed


def test_divider_type_dash_matches_the_no_type_default():
    # #125: TYPE=DASH is the historical hyphen rule — identical to a plain <divider>
    # (which is why bundled panels stay byte-for-byte unchanged).
    plain = load_dtl('<panel><area><divider></area></panel>')
    dash = load_dtl('<panel><area><divider type="dash"></area></panel>')
    assert plain.items == dash.items
    assert plain.items[0] == Text(0, 1, "-" * 78, role="rule")


def test_divider_type_solid_is_a_ge_line():
    # #125: TYPE=SOLID draws an unbroken GE line (── from the graphic set),
    # visibly distinct from DASH's hyphens.
    s = load_dtl('<panel><area><divider type="solid"></area></panel>')
    gts = [i for i in s.items if isinstance(i, GraphicText)]
    assert len(gts) == 1
    assert set(gts[0].codes) == {Line.HORIZONTAL.value}   # all solid-line glyphs
    assert not any(isinstance(i, Text) and set(i.text) == {"-"} for i in s.items)


def test_divider_type_text_lays_out_its_text_positioned_by_format():
    # #125: TYPE=TEXT renders the divider's own text, placed within the span by
    # FORMAT (START/CENTER/END) — not a rule of dashes.
    start = load_dtl('<panel><area><divider type="text">Options</divider></area></panel>')
    assert start.items[0] == Text(0, 1, "Options", role="rule")
    centre = load_dtl(
        '<panel><area><divider type="text" format="center">Options</divider></area></panel>')
    # 78-wide span, 7-char text → centred at 1 + (78-7)//2.
    assert centre.items[0] == Text(0, 1 + (78 - 7) // 2, "Options", role="rule")
    end = load_dtl(
        '<panel><area><divider type="text" format="end">Options</divider></area></panel>')
    assert end.items[0] == Text(0, 1 + (78 - 7), "Options", role="rule")


def test_region_grpbox_frames_content_in_a_box_border():
    # #125: <region GRPBOX=YES> draws a GE box border around its content, with the
    # content inset past the left border and one row below the top border.
    s = load_dtl(
        '<panel><region grpbox="yes"><info>hello</info>'
        '<info>world</info></region></panel>'
    )
    texts = [i for i in s.items if isinstance(i, Text)]
    assert texts[0] == Text(1, 3, "hello", DisplayIntensity.NORMAL)   # inset + below top
    assert texts[1] == Text(2, 3, "world", DisplayIntensity.NORMAL)
    gts = [i for i in s.items if isinstance(i, GraphicText)]
    # A top edge, a bottom edge, and left+right verticals on each content row.
    top = next(g for g in gts if g.row == 0)
    assert top.codes[0] == Line.TOP_LEFT.value and top.codes[-1] == Line.TOP_RIGHT.value
    bottom = next(g for g in gts if g.codes[0] == Line.BOTTOM_LEFT.value)
    assert bottom.row == 3                                            # below both rows
    verticals = [g for g in gts if g.codes == bytes([Line.VERTICAL.value])]
    assert {g.row for g in verticals} == {1, 2}                       # a side per content row
    # Left edge at the box origin (col 1), right edge one short of the top's width.
    assert min(g.col for g in verticals) == 1
    assert max(g.col for g in verticals) == 1 + len(top.codes) - 1


def test_region_grpbox_title_sits_on_the_top_edge():
    # #125: the region's leading text becomes the group-box title, laid on the top
    # border between ┌─ and ─┐ (a Text field flanked by two GraphicText segments).
    s = load_dtl(
        '<panel><region grpbox="yes">Terminal Settings'
        '<info>value</info></region></panel>'
    )
    title = next(i for i in s.items
                 if isinstance(i, Text) and "Terminal Settings" in i.text)
    assert title.row == 0                                             # on the top edge
    top_segs = [i for i in s.items if isinstance(i, GraphicText) and i.row == 0]
    assert any(g.codes[0] == Line.TOP_LEFT.value for g in top_segs)   # ┌─ segment
    assert any(g.codes[-1] == Line.TOP_RIGHT.value for g in top_segs)  # ─┐ segment


def test_region_without_grpbox_is_unchanged():
    # Byte-identity guard: a plain <region> draws no border and does not inset.
    s = load_dtl('<panel><region><info>x</info></region></panel>')
    assert s.items == [Text(0, 1, "x", DisplayIntensity.NORMAL)]


def test_panel_records_window_and_keylist_metadata():
    # #125: KEYLIST/WINDOW/WINTITLE/CURSOR are recorded on the Screen as metadata
    # (they do not change the rendered field stream).
    s = load_dtl(
        '<panel keylist="ISRLIST" window="yes" wintitle="Pop-Up" cursor="zcmd">'
        '<area><dtafld name="zcmd">Cmd</dtafld></area></panel>'
    )
    assert s.keylist_ref == "ISRLIST"          # panel KEYLIST= reference (vs a <keyl> NAME)
    assert s.window is True
    assert s.window_title == "Pop-Up"
    assert s.cursor_field == "zcmd"


def test_panel_tmargin_and_bmargin_reserve_top_and_bottom_rows():
    # #125: TMARGIN shifts the whole panel (title + body) down n rows; BMARGIN keeps
    # content out of the last n rows. Both default to 0 (byte-identical when absent).
    plain = load_dtl('<panel name="p" width="40">Title<area><info>Body</area></panel>')
    assert next(t.row for t in plain.items if isinstance(t, Text) and t.text == "Title") == 0

    shifted = load_dtl('<panel name="p" width="40" tmargin="3">Title'
                       '<area><info>Body</area></panel>')
    assert next(t.row for t in shifted.items if isinstance(t, Text) and t.text == "Title") == 3
    assert next(t.row for t in shifted.items if isinstance(t, Text) and t.text == "Body") == 4

    # BMARGIN=3 on a depth-10 panel keeps content at/below row depth-1-bmargin = 6
    tall = load_dtl('<panel name="p" width="40" depth="10" bmargin="3">T<area>'
                    + "".join(f"<info>L{i}" for i in range(20)) + "</area></panel>")
    assert max(t.row for t in tall.items if isinstance(t, Text)) <= 10 - 1 - 3


def test_area_div_draws_a_closing_divider():
    # #125: <area>/<region> DIV=SOLID/DASH draws a dashed rule as the box's last
    # line; TEXT writes the divider text (FORMAT positions it); NONE (default)
    # draws nothing (byte-identical).
    s = load_dtl('<panel name="p" width="30">T<area div="solid"><info>Body</info></area>'
                 '<info>After</info></panel>')
    rules = [t for t in s.items if isinstance(t, Text) and set(t.text) == {"-"}]
    body = next(t for t in s.items if isinstance(t, Text) and t.text == "Body")
    after = next(t for t in s.items if isinstance(t, Text) and t.text == "After")
    assert len(rules) == 1
    assert body.row < rules[0].row < after.row       # divider closes the area
    # TEXT divider
    txt = load_dtl('<panel name="p" width="30">T<area div="text" text="-- end --">'
                   '<info>X</info></area></panel>')
    assert any(isinstance(t, Text) and t.text == "-- end --" for t in txt.items)
    # default (no DIV) → no rule
    plain = load_dtl('<panel name="p" width="30">T<area><info>Body</info></area></panel>')
    assert not [t for t in plain.items if isinstance(t, Text) and set(t.text) == {"-"}]


def test_regions_lay_out_side_by_side_columns():
    s = load_dtl(
        '<panel>'
        '<region dir="horiz">'
        '<region><info>left1</info><info>left2</info></region>'
        '<region><info>right1</info><info>right2</info></region>'
        '</region>'
        '</panel>'
    )
    assert s.items[0] == Text(0, 1, "left1", DisplayIntensity.NORMAL)
    assert s.items[1] == Text(1, 1, "left2", DisplayIntensity.NORMAL)
    assert s.items[2] == Text(0, 8, "right1", DisplayIntensity.NORMAL)
    assert s.items[3] == Text(1, 8, "right2", DisplayIntensity.NORMAL)


def test_horiz_region_flows_fields_side_by_side():
    # <region dir=horiz> lays its children left-to-right (rather than stacking
    # them) — the guide's implicit layout for a row of related fields (City /
    # State / Zip). The enclosing flow then resumes on the row *below* them.
    s = load_dtl(
        '<panel><area>'
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
        '<panel><area>'
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
        '<panel><area>'
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
        '<panel><selfld><choice selchar="1" name="Aaa">desc</choice></selfld></panel>'
    )
    assert base.items[0] == Text(0, 1, "1 ", H)     # classic columns from the origin:
    assert base.items[1] == Text(0, 4, "Aaa", N)    #   num@1, name@4, desc@21
    assert base.items[2] == Text(0, 21, "desc", N)

    shifted = load_dtl(
        '<panel><region indent="29">'
        '<selfld><choice selchar="1" name="Aaa">desc</choice></selfld>'
        '</region></panel>'
    )
    assert shifted.items[0] == Text(0, 30, "1 ", H)   # each shifted by the indent (29)
    assert shifted.items[1] == Text(0, 33, "Aaa", N)
    assert shifted.items[2] == Text(0, 50, "desc", N)


def test_selfld_as_horiz_column_shifts_right():
    # Two <selfld>s in side-by-side dir=horiz regions no longer overlap: the
    # right one's choices shift to its column instead of pinning to column 1.
    s = load_dtl(
        '<panel><area><region dir="horiz">'
        '<region><selfld><choice selchar="1" name="Mon">day</choice></selfld></region>'
        '<region><selfld><choice selchar="1" name="Nine">am</choice></selfld></region>'
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
        '<panel><area>'
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
        load_dtl('<info>x</info>')


def test_area_flow_is_transparent():
    # A plain (no row/col) <area>/<region> transparently continues the panel's
    # flow: wrapping content in one does not change the rendered bytes.
    plain = load_dtl("<panel>Menu<info>one</info><info>two</info></panel>")
    boxed = load_dtl("<panel>Menu<area><info>one</info><info>two</info></area></panel>")
    assert plain.render() == boxed.render()


# ── SGML conformance (DOCTYPE, case-insensitivity, attribute minimization) ───

def test_doctype_prolog_is_tolerated():
    s = load_dtl(
        '<!DOCTYPE DM SYSTEM>\n'
        '<panel><info>hi</info></panel>'
    )
    assert s.items == [Text(0, 1, "hi", DisplayIntensity.NORMAL)]


def test_tag_and_attribute_names_are_case_insensitive():
    s = load_dtl('<PANEL><INFO><HP>hi</HP></INFO></PANEL>')
    assert s.items == [Text(0, 1, "hi", DisplayIntensity.HIGH, role="emphasis")]


def test_boolean_attribute_minimization():
    # <dtafld cursor> (no value) means cursor="yes"; <... numeric> likewise.
    s = load_dtl(
        '<panel>'
        '<dtafld datavar="pw" entwidth="8" cursor>P</dtafld>'
        '<dtafld datavar="sz" entwidth="5" numeric>S</dtafld>'
        '</panel>'
    )
    assert s.items[1].cursor is True
    assert s.items[3].numeric is True


def test_shipped_panels_have_doctype_and_render():
    # Every shipped panel carries the DOCTYPE prolog and renders without error.
    panels = pathlib.Path(__file__).parent / "panels"
    dtls = sorted(panels.glob("*.dtl"))
    assert dtls                                      # the panel library is present
    for p in dtls:
        assert p.read_text(encoding="utf-8").lstrip().startswith("<!DOCTYPE"), p.name
        assert load_panel(p.stem).render()           # renders to non-empty bytes


def test_missing_required_attr_raises():
    # A still-required attribute (keyi's key) raises; note <info>/<dtafld> no
    # longer require row/col — they auto-flow inside a panel (see flow tests).
    with pytest.raises(DTLError):
        load_dtl('<panel><keyl><keyi cmd="HELP"/></keyl></panel>')


def test_choice_outside_selfld_raises():
    with pytest.raises(DTLError):
        load_dtl('<panel><choice selchar="0" name="A">d</choice></panel>')


# ── keylist (<keyl>/<keyi>) ──────────────────────────────────────────────────

def test_keyl_builds_keylist_and_emits_no_items():
    s = load_dtl(
        '<panel>'
        '<info>hi</info>'
        '<keyl name="K">'
        '<keyi key="PF1" cmd="HELP">Help</keyi>'
        '<keyi key="PF3" cmd="EXIT">Exit</keyi>'
        '</keyl>'
        '</panel>'
    )
    # The keylist is metadata: it adds no renderable items.
    assert s.items == [Text(0, 1, "hi", DisplayIntensity.NORMAL)]
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
        '<dtafld datavar="size" entwidth="5">Size</dtafld>'
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
        '<dtafld datavar="sz" entwidth="5">Size</dtafld>'
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
        '<dtafld datavar="flag" entwidth="3">F</dtafld>'
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
        '<dtafld datavar="f" entwidth="20">F</dtafld>'
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
        '<area><dtafld datavar="f" entwidth="30">F</dtafld></area>'
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


def test_varclass_dbcs_mixed_ebcdic_cap_length():
    # DBCS/MIXED/EBCDIC/ANY are character kinds like CHAR: the size caps the
    # input length (previously only CHAR's size was enforced — #129).
    for kind in ("dbcs", "mixed", "ebcdic", "any"):
        s, addr = _varclass_panel(f'<varclass name="C" type="{kind} 4" msg="LN">')
        assert s.validations["F"]["checks"] == [{"type": "maxlen", "max": 4}]
        assert s.first_validation_error({addr: "ABCD"}) is None
        msgid, subs = s.first_validation_error({addr: "ABCDE"})
        assert msgid == "LN" and subs == {"VALUE": "ABCDE", "MAX": 4}


def test_varclass_numeric_fractional_precision_is_enforced():
    # TYPE="numeric total frac" is a fixed-point decimal: cap both the total and
    # the fractional digit counts, and require the value to be numeric (#129).
    s, addr = _varclass_panel('<varclass name="C" type="numeric 5 2" msg="ND">')
    assert s.validations["F"]["checks"] == [{"type": "decimal", "total": 5, "frac": 2}]
    assert s.first_validation_error({addr: "123.45"}) is None      # 5 total, 2 frac
    assert s.first_validation_error({addr: "12"}) is None          # integer allowed
    assert s.first_validation_error({addr: "1.234"})[0] == "ND"    # too many fractional
    assert s.first_validation_error({addr: "1234.56"})[0] == "ND"  # too many total
    assert s.first_validation_error({addr: "abc"})[0] == "ND"      # not a number


def test_varclass_datetime_classes_enforce_a_format():
    # The IDATE/STDDATE/JDATE/JSTD/ITIME/STDTIME classes each require the value to
    # match that ISPF date/time format exactly (#129).
    cases = {
        "idate":   ("06/07/26",   "6/7/26"),
        "stddate": ("2026/07/06", "26/07/06"),
        "jdate":   ("26.187",     "26/187"),
        "jstd":    ("2026.187",   "26.187"),
        "itime":   ("12:30",      "12:30:00"),
        "stdtime": ("12:30:00",   "12:30"),
    }
    for kind, (good, bad) in cases.items():
        s, addr = _varclass_panel(f'<varclass name="C" type="{kind}" msg="DT">')
        assert s.validations["F"]["checks"][0]["type"] == "pattern"
        assert s.first_validation_error({addr: good}) is None, kind
        assert s.first_validation_error({addr: bad}) == ("DT", {"VALUE": bad}), kind


def test_varclass_vmask_caps_length():
    # VMASK's edit mask isn't modelled, but its length is enforced so the TYPE
    # isn't silently dropped (#129).
    s, addr = _varclass_panel('<varclass name="C" type="vmask 9" msg="VM">')
    assert s.validations["F"]["checks"] == [{"type": "maxlen", "max": 9}]
    assert s.first_validation_error({addr: "123456789"}) is None
    assert s.first_validation_error({addr: "1234567890"})[0] == "VM"


def test_varclass_symbolic_size_type_does_not_crash():
    # A '%varname size' TYPE has a symbolic (non-numeric) size: it loads without
    # an enforceable length cap rather than crashing (#129).
    s, addr = _varclass_panel('<varclass name="C" type="%len 8" msg="PC">')
    assert s.first_validation_error({addr: "anything"}) is None


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
        '<panel><dtafld datavar="month" entwidth="3">M</dtafld></panel>'
    )
    addr = s.field_addr("month")
    assert s.first_validation_error({addr: "NOV"}) is None                    # valid
    assert s.first_validation_error({addr: "dec"}) is None                    # case-insensitive
    assert s.first_validation_error({addr: "XYZ"}) == ("ABCD003", {"VALUE": "XYZ"})
    assert s.first_validation_error({addr: ""}) is None                       # empty skipped


def test_xlatl_xlati_translates_internal_and_external_values():
    # #114: <xlatl>/<xlati value=internal>external maps a variable's internal value
    # to its displayed form (usage=out) and a typed value back to internal.
    src = (
        '<varclass name="onoff" type="char 8">'
        '  <xlatl msg="ABC001">'
        '    <xlati value="1">Enabled<xlati value="0">Disabled'
        '  </xlatl>'
        '</varclass>'
        '<varlist><vardcl name="state" varclass="onoff"/></varlist>'
        '<panel name=p width=40><area>'
        '<dtafld datavar="state" usage=out>Status</dtafld>'
        '</area></panel>')
    # usage=out: the internal value 1 displays as its external form "Enabled".
    s = load_dtl(src, state="1")
    cells = [it.text.strip() for it in s.items
             if isinstance(it, Text) and getattr(it, "role", None) == "cell"]
    assert "Enabled" in cells
    assert "1" not in cells                                    # raw internal not shown
    # read-back: a typed external value maps to its internal form.
    assert s.internal_value("state", "Enabled") == "1"
    assert s.internal_value("state", "Disabled") == "0"
    assert s.internal_value("state", "Other") == "Other"      # untranslated passes through


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
        '<panel><dtafld datavar="pay" entwidth="9">P</dtafld></panel>'
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
        '<panel><dtafld datavar="cmd" entwidth="6">C</dtafld></panel>'
    )
    addr = s.field_addr("cmd")
    assert s.first_validation_error({addr: "list"}) is None          # case-insensitive
    assert s.first_validation_error({addr: "EDIT"}) is None
    assert s.first_validation_error({addr: "nope"}) == ("BADM", {"VALUE": "nope"})


def test_assignl_assigni_maps_a_field_value_to_its_assigned_result():
    # #55: <dtafld><assignl destvar=X><assigni value=v result=r> is an assignment
    # list — on submit the field's value is looked up and the matching RESULT is
    # assigned to the destination variable X (an ISPF )PROC assignment).
    s = load_dtl(
        '<panel name=p width=50><area>'
        '<dtafld datavar="room" entwidth="6" pmtwidth="15">Room type'
        '  <assignl destvar="rmtype">'
        '    <assigni value="SINGLE" result="1">'
        '    <assigni value="DOUBLE" result="2">'
        '  </assignl>'
        '  <dtafldd>(Single or Double)'
        '</area></panel>'
    )
    # The list is recorded on the field, keyed by the submitted field's name.
    assert s.assignments["ROOM"] == {
        "destvar": "rmtype", "map": {"SINGLE": "1", "DOUBLE": "2"}}
    # Read-back: a submitted value resolves to (destvar, result); matching is
    # case-insensitive and tolerates the field's blank padding.
    assert s.assigned_value("room", "SINGLE") == ("rmtype", "1")
    assert s.assigned_value("room", "double") == ("rmtype", "2")
    assert s.assigned_value("room", "  DOUBLE  ") == ("rmtype", "2")
    # A value matching no <assigni>, and a field with no list, both assign nothing.
    assert s.assigned_value("room", "KING") is None
    assert s.assigned_value("other", "SINGLE") is None
    # The <assignl> does not swallow the trailing <dtafldd> description or the prompt.
    texts = [it.text for it in s.items if isinstance(it, Text)]
    assert any("Room type" in t for t in texts)
    assert any("(Single or Double)" in t for t in texts)


def test_assignl_renders_nothing_and_leaves_a_plain_field_intact():
    # An assignment list is )PROC metadata: it adds no on-screen items, so a field
    # carrying one lays out exactly like a plain <dtafld> (byte-identical render).
    with_list = load_dtl(
        '<panel name=p width=40><area>'
        '<dtafld datavar="f" entwidth="4">Pick'
        '<assignl destvar="d"><assigni value="A" result="1"></assignl>'
        '</area></panel>')
    without = load_dtl(
        '<panel name=p width=40><area>'
        '<dtafld datavar="f" entwidth="4">Pick'
        '</area></panel>')
    assert with_list.render() == without.render()
    # But the mapping is still recorded (a VALUE with no RESULT assigns "").
    assert with_list.assigned_value("f", "A") == ("d", "1")


def test_assignl_from_the_reference_corpus_example():
    # The verbatim guide example (ex089.dtl): a room-type field whose value is
    # mapped to a numeric room-type code in rmtype.
    src = (pathlib.Path(__file__).parent / "tests" / "dtl_examples"
           / "ex089.dtl").read_text()
    s = load_dtl(src)
    assert s.assigned_value("room", "SINGLE") == ("rmtype", "1")
    assert s.assigned_value("room", "DOUBLE") == ("rmtype", "2")


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
    # A type we don't enforce yet (e.g. DSNAME) still loads without failing the
    # panel and adds no validation — leniency preserved for the unimplemented set.
    s, addr = _check_panel('<checki type="dsname"></checki>')
    assert s.validations.get("F", {}).get("checks", []) == []
    assert s.first_validation_error({addr: "anything!"}) is None   # no check enforced


def test_checki_num_hex_len_pict_validate_input():
    # #62: NUM (all digits), HEX (hex digits), LEN (length op), PICT (mask) are
    # enforced on submit, failing with the checkl MSG.
    s, addr = _check_panel('<checki type="num"></checki>')
    assert s.first_validation_error({addr: "1234"}) is None        # numeric passes
    assert s.first_validation_error({addr: "12x4"}) is not None     # non-numeric fails

    s, addr = _check_panel('<checki type="hex"></checki>')
    assert s.first_validation_error({addr: "1AF0"}) is None
    assert s.first_validation_error({addr: "1AG0"}) is not None     # G is not hex

    s, addr = _check_panel('<checki type="len" parm1="EQ" parm2="4"></checki>')
    assert s.first_validation_error({addr: "abcd"}) is None
    assert s.first_validation_error({addr: "abc"}) is not None      # wrong length

    s, addr = _check_panel('<checki type="pict" parm2="A99"></checki>')
    assert s.first_validation_error({addr: "X12"}) is None          # alpha + 2 digits
    assert s.first_validation_error({addr: "XY2"}) is not None      # 2nd char not a digit
    assert s.first_validation_error({addr: "X123"}) is not None     # wrong length


def test_required_field_rejects_empty_input():
    # IBM REQUIRED=YES: the field must be non-empty on submit; MSG names the error.
    s = load_dtl(
        '<panel>'
        '<dtafld datavar="name" entwidth="8"'
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
        '<dtafld datavar="pw" entwidth="8"'
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
        '<dtafld datavar="sz" entwidth="5" required="yes">Size</dtafld>'
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
    # And the referenced message formats with the range substitutions.
    cat = load_message_member("tsomsgs")
    assert cat.format("TSO001", VALUE="99999", MIN=0, MAX=32768) == \
        "TSO001 SIZE 99999 IS NOT IN THE RANGE 0 TO 32768"


def test_char_varclass_leaves_field_alphanumeric():
    s = load_dtl(
        '<panel>'
        '<varclass name="C" type="char"/>'
        '<varlist><vardcl name="x" varclass="C"/></varlist>'
        '<dtafld datavar="x" entwidth="4">X</dtafld>'
        '</panel>'
    )
    assert s.items[1].numeric is False


def test_explicit_numeric_attr_overrides_varclass():
    s = load_dtl(
        '<panel>'
        '<varclass name="C" type="char"/>'
        '<varlist><vardcl name="x" varclass="C"/></varlist>'
        '<dtafld datavar="x" entwidth="4" numeric="yes">X</dtafld>'
        '</panel>'
    )
    assert s.items[1].numeric is True   # field attribute wins over the class


def test_vardcl_outside_varlist_is_tolerated():
    # A stray <vardcl> (some guide examples begin mid-declaration) is ignored
    # rather than aborting the panel: the panel still renders its body.
    s = load_dtl('<panel><vardcl name="x" varclass="C"/>'
                 '<info>HELLO</info></panel>')
    assert any(getattr(i, "text", None) == "HELLO" for i in s.items)


def test_varclass_missing_name_raises():
    with pytest.raises(DTLError):
        load_dtl('<panel><varclass type="numeric"/></panel>')


# ── command area (<cmdarea>) ─────────────────────────────────────────────────

def test_cmdarea_renders_like_dtafld_and_records_command_field():
    cmd = load_dtl(
        '<panel><cmdarea entwidth="6" cursor="yes">'
        'Option ===></cmdarea></panel>'
    )
    # Same prompt + field bytes as the equivalent <dtafld> (name aside).
    fld = load_dtl(
        '<panel><dtafld datavar="ZCMD" entwidth="6" '
        'cursor="yes">Option ===></dtafld></panel>'
    )
    assert cmd.render() == fld.render()
    assert cmd.command_field is cmd.items[1]
    assert cmd.command_field.name == "ZCMD"          # default ISPF command var
    assert cmd.field_addr("ZCMD") == 0 * 80 + 14


def test_cmdarea_datavar_override_and_command_value():
    s = load_dtl(
        '<panel><cmdarea datavar="OPT" entwidth="6">'
        'Option ===></cmdarea></panel>'
    )
    assert s.command_field.name == "OPT"
    addr = s.command_field.data_addr
    assert s.command_value({addr: "3   "}) == "3   "
    assert s.command_value({}) is None


def test_command_value_none_without_cmdarea():
    s = load_dtl('<panel><info>hi</info></panel>')
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
