"""A pragmatic subset of IBM's Dialog Tag Language (DTL) for defining screens.

DTL is IBM's real, ISO-SGML-based markup for ISPF panels — on z/OS you write
tagged source and run it through the ``ISPDTLC`` converter to produce panels,
messages, command tables, and keylists. This module is a small, self-contained
take on the same idea: ``load_dtl(source)`` parses DTL markup into a
:class:`screen.Screen`, which then renders to a 3270 data stream.

Relationship to authentic DTL
-----------------------------
We keep DTL's tag *names* and spirit. A ``<panel>`` is a **flow box**: every
element flows down from the top (and a ``<dtafld>``'s entry field follows its
prompt), the way real DTL relies on ``ISPDTLC`` to auto-lay-out. There is no
explicit ``row``/``col`` positioning — the bundled panels are all auto-flow.
This is a pragmatic take on ``ISPDTLC``'s auto-layout (the genuinely hard part),
exercised by both the bundled panels and the conformance corpus
(``tests/dtl_examples/``).

Like real DTL the source is SGML: files may begin with a ``<!DOCTYPE DM SYSTEM>``
prolog (tolerated and ignored), tag and attribute names are case-insensitive
(``<PANEL>`` == ``<panel>``), and boolean attributes may be minimized
(``<dtafld numeric>`` means ``numeric="yes"``).

Supported tags
--------------
``<panel name help>Title``      root container. The panel's content text is its
   ``width depth titline``        title (``panel-title-text`` → ``Screen.title``,
                                 centered on row 0 when that row is free;
                                 ``titline=no`` keeps it metadata-only, no line);
                                 ``help`` names a help panel; ``width``/``depth``
                                 give the presentation-space size (default 80x24)
                                 and bound element positions at load time.
``<area row col>``               a flow box: contained elements that omit ``row``
``<region row col width dir>``   flow down from this origin (one line each), and
                                 those that omit ``col`` use it. A field's entry
                                 flows one column after its
                                 prompt. ``dir=horiz`` lays the box's children side
                                 by side instead of stacking them, and the enclosing
                                 flow resumes below the tallest column; ``width=n``
                                 fixes a column's width.
``<info row col>``               protected text (label / instruction). A whole-line
                                 ``<hp>`` is CUA emphasis (high intensity + white);
                                 a horizontal rule is a ``<divider>``.
``<topinst row col>``            top / panel / bottom instruction text. Render like
``<pnlinst row col>``            ``<info>`` (protected text); semantic DTL tags. A
``<botinst row col>``            flowed ``<botinst>`` anchors at the panel foot.
``<p>`` ``<lines>`` ``<dt>`` ``<dd>``  flowed text: paragraphs and items each render
``<pt>`` ``<pd>``                as protected lines, word-wrapped to the panel
                                 width with a hanging indent. DTL omits end tags,
                                 so a block element is closed by the next block tag.
``<ul>`` ``<ol>`` ``<li>``         a list: each ``<li>`` flows as a bullet/number plus
                                 its (wrapped) text, indented one level per nesting.
                                 ``<ul>`` → ``o``/``-``/``--`` by depth; ``<ol>`` →
                                 ``1.`` then nested ``a.`` then ``i.`` (CUA style).
``<help name width depth>``      a top-level help panel — same flow root as
                                 ``<panel>`` (title text, width/depth, flow box).
Panel title: the text after ``<panel ...>``/``<help ...>`` (before its first child)
renders centered on row 0, with the body flowing beneath it.
``<dtafld row col datavar         a prompt plus an input field that follows it. The
   entwidth usage                 prompt is the text of a nested ``<dtafldd>`` child
   pmtloc ...>``                  (authentic DTL) or the element's own text.
                                 ``usage=out`` makes it a protected display field
                                 (the variable's value); ``pmtloc=above`` puts the
                                 prompt on the line above. See attrs below.
``<dtafldd>text</dtafldd>``      data-field description: a trailing description
                                 (after the entry, sized by ``deswidth``) when the
                                 field has its own prompt text, else it stands in
                                 as the prompt.
``<cmdarea row col                the command area (ISPF "Option/Command ===>"
   entwidth ...>``                line). Renders like ``<dtafld>``; ``datavar``
                                 defaults to ``ZCMD`` and the field is recorded
                                 as ``Screen.command_field``.
``<selfld row col type>``        a list of menu choices; each ``<choice>`` is laid
                                 out on its own row, auto-incrementing.
``<choice selchar name match      one menu row: number, name, description. The
   checkvar unavail>desc``        selection value (``match``, default the
                                 auto-number or ``selchar``) is recorded in
                                 ``Screen.selections`` so the dialog can
                                 validate a typed option; ``checkvar`` lands the
                                 cursor on the current choice; ``unavail`` greys a
                                 choice out and makes it unselectable.
``<keyl name>``                  a keylist: a set of function-key bindings for
                                 the panel (rendered as nothing; pure metadata).
``<keyi key cmd>desc``           one key binding: function key ``key`` (e.g.
                                 ``PF3``) invokes command ``cmd`` (e.g. ``EXIT``).
``<cmdtbl applid>``              an application command table (metadata).
``<cmd name altdescr>ex<t>tra``  a command; a ``<t>`` truncation point within the
                                 external name marks the min chars to type;
                                 ``altdescr`` is the command's description (metadata).
``<cmdact action>``              the command's action (e.g. ``alias exit``,
                                 ``passthru``). Recorded in ``Screen.commands``.
``<ab row col gap>``             an action bar; its ``<abc>`` choice labels are
``<abc>label</abc>``             laid out across ``row``. Each ``<abc>`` holds
``<pdc>label<action run>``       ``<pdc>`` pull-down choices (kept in
``<pdsep>``                      ``Screen.action_bar`` for future interaction); a
                                 ``<pdsep>`` is a divider row between them.
``<varclass name type msg>``     a variable class: ``type="char N"`` caps input
                                 length, ``type="numeric N"`` makes fields numeric
                                 (capping digits). May contain a ``<checkl>``.
``<checkl msg>``                 a validity-check list; ``msg`` names the message
                                 shown when a check fails (falls back to the
                                 ``<varclass>``'s ``msg``).
``<checki type>min max``         a check item: ``type="range"`` (``min max`` text),
``<checki type>v1 v2 ...``       ``type="values"`` (allowed values, as text or via
``<checki type parm1 parm2>``    ``parm1=EQ|NE parm2='v1 v2'``), ``type="alpha"``
                                 (letters), or ``type="name"`` (a valid symbol). A
                                 field's input is validated against its class's
                                 checks. Other types (``picture`` …) stay lenient.
``<xlatl msg format>``           a translate list on a ``<varclass>``: its
``<xlati value>external``        ``<xlati>`` items name the values a field may be
                                 typed as (``format=upper`` matches case-insensitively);
                                 an input that is not one fails with the ``msg``.
                                 ``<lit>`` wraps an external with literal spacing.
``<varlist>``                    container for ``<vardcl>`` declarations.
``<vardcl name varclass>``       declares variable ``name`` to be of class
                                 ``varclass``; a field's ``numeric`` is inherited
                                 from it when the field omits ``numeric``.
``<msgmbr name>``                a message member: container for ``<msg>`` entries
                                 (parsed by :func:`load_messages`, not a panel).
``<msg msgid>text``              a message; ``&NAME`` references (or a nested
``<varsub var>``                 ``<varsub var=NAME>`` tag) in ``text`` are
                                 substituted at display time. See `MessageCatalog`.
Inline in body text: ``<hp>``/``<rp>`` emphasise a phrase within a text element
(``<rp>`` — a reference phrase / help-panel link — renders underlined by default).

``<dtafld>`` attributes: ``datavar`` (field name sent back), ``entwidth`` (field
length), ``display`` (``display=no`` is non-display, e.g. password), ``numeric``,
``init`` (initial value),
``required`` (``required=yes`` must be non-empty on submit; ``msg`` names the error),
``deswidth`` (width of a trailing ``<dtafldd>`` description),
``cursor`` (place the cursor here), ``mdt`` (default yes), ``intens`` (prompt).

Variable substitution: dialog-variable references are written ISPF-style with a
leading ``&`` (e.g. ``&ZUSER``, ``&ZTIME``) and resolved from the keyword
arguments to :func:`load_dtl` before parsing. A reference is a name of 1–8
characters; an optional trailing ``.`` terminates it (and is consumed), so
``&ZUSER.X`` substitutes ``ZUSER`` followed by a literal ``X``. ``&&`` is a
literal ampersand. Names are matched case-insensitively (ISPF convention is
uppercase). An undefined reference is left untouched rather than blanked.
"""

import re
from html.parser import HTMLParser

from screen import (Screen, Text, Field, DisplayIntensity, Color, Highlight,
                    Outline, GraphicText, Line)

# An ISPF dialog-variable reference in panel source: ``&&`` (escaped literal
# ampersand) or ``&NAME`` with an optional terminating ``.``. A name is 1–8
# characters: a letter or one of @ # $ followed by up to 7 alphanumerics/@#$.
_DIALOG_VAR_RE = re.compile(r"&&|&([A-Za-z@#$][A-Za-z0-9@#$]{0,7})\.?")


def _substitute(source: str, variables: dict) -> str:
    """Resolve ``&NAME`` dialog-variable references against ``variables``.

    ``&&`` collapses to a single ``&``; a known ``&NAME`` (case-insensitive) is
    replaced by its value and any trailing ``.`` consumed; an unknown reference
    is left verbatim (including its terminator).
    """
    upper = {k.upper(): "" if v is None else str(v) for k, v in variables.items()}

    def repl(match):
        if match.group(0) == "&&":
            return "&"
        name = match.group(1).upper()
        return upper.get(name, match.group(0))

    return _DIALOG_VAR_RE.sub(repl, source)


# An internal SGML general-entity declaration with a literal value, e.g.
# ``<!ENTITY guar "money-back guarantee">`` — and the matching ``&guar;``
# reference. Parameter entities (``<!ENTITY % …>``) and external/SYSTEM
# entities reference files we don't have, so they're left unresolved.
_ENTITY_DECL_RE = re.compile(
    r"""<!\s*ENTITY\s+([A-Za-z][\w.-]*)\s+(?:"([^"]*)"|'([^']*)')\s*>""",
    re.IGNORECASE,
)
_ENTITY_REF_RE = re.compile(r"&([A-Za-z][\w.-]*);")


def _resolve_entities(source: str) -> str:
    """Resolve internal SGML general entities: capture ``<!ENTITY name "text">``
    declarations (wherever they appear, including a ``<!doctype … [ … ]>``
    internal subset), drop the declarations, and replace ``&name;`` references
    with their text. References to entities we didn't capture (external/SYSTEM,
    parameter, or undeclared) are left verbatim."""
    entities = {}
    for m in _ENTITY_DECL_RE.finditer(source):
        entities[m.group(1).lower()] = m.group(2) if m.group(2) is not None else m.group(3)
    if not entities:
        return source
    source = _ENTITY_DECL_RE.sub("", source)
    return _ENTITY_REF_RE.sub(
        lambda m: entities.get(m.group(1).lower(), m.group(0)), source
    )


_INTENSITY = {
    "normal": DisplayIntensity.NORMAL,
    "high": DisplayIntensity.HIGH,
    "highlighted": DisplayIntensity.HIGHLIGHTED,
}

# Shown when a REQUIRED=YES field is left blank and neither the field nor its
# variable class names a MSG — a stand-in for ISPF's own system message.
_REQUIRED_DEFAULT_MSG = "Enter required field"

# ISPF option routing in a )PROC: `&ZSEL = TRANS( TRUNC(&ZCMD,'.') 0,'PANEL(x)' …)`.
# _ZSEL_TRANS_OPEN_RE finds the `TRANS(`; _balanced_parens then takes exactly its
# body (so anything after the TRANS — a second statement/assignment — can't leak in).
# _ZSEL_PAIR_RE picks each `option,'selection-string'` pair; the option is a digit run
# or a single word-boundaried letter, which skips the source expression
# (TRUNC(&ZCMD,'.')) and the `*,'?'` default without matching them.
_ZSEL_TRANS_OPEN_RE = re.compile(r"ZSEL\s*=\s*TRANS\s*\(", re.IGNORECASE)
_ZSEL_PAIR_RE = re.compile(r"\b(\d+|[A-Z])\s*,\s*'([^']*)'")


def _balanced_parens(s, open_idx):
    """The text inside the balanced parentheses whose opening ``(`` is at
    ``s[open_idx]`` (the opener excluded). Falls back to the rest of the string if
    the parentheses never close."""
    depth = 0
    for i in range(open_idx, len(s)):
        if s[i] == "(":
            depth += 1
        elif s[i] == ")":
            depth -= 1
            if depth == 0:
                return s[open_idx + 1:i]
    return s[open_idx + 1:]

# DTL COLOR / HILITE attribute values → the screen model's enums. These are real
# DTL attributes (COLOR=WHITE|RED|BLUE|GREEN|PINK|YELLOW|TURQ|%var, HILITE=USCORE|
# BLINK|REVERSE) carried by the CUA element tags that accept them (<dtafld>,
# <selfld>, <lstcol>, <hp>, <note>/<notel>/<nt>, <attr>). The canonical keywords
# are the DTL ones; a few friendly aliases are tolerated. Colour is emitted only
# to colour-capable terminals (Screen.render(color=True)); a mono terminal
# ignores it, so panels stay byte-identical there.
_COLORS = {
    "white": Color.WHITE,
    "red": Color.RED,
    "blue": Color.BLUE,
    "green": Color.GREEN,
    "pink": Color.PINK,
    "yellow": Color.YELLOW,
    "turq": Color.TURQUOISE,
    # tolerated aliases
    "turquoise": Color.TURQUOISE,
    "cyan": Color.TURQUOISE,
    "magenta": Color.PINK,
}

_HIGHLIGHTS = {
    "uscore": Highlight.UNDERSCORE,
    "blink": Highlight.BLINK,
    "reverse": Highlight.REVERSE,
    # tolerated aliases
    "underscore": Highlight.UNDERSCORE,
    "rvideo": Highlight.REVERSE,
}

# A text tag's TYPE= (on <hp>/<note>/<notel>/<nt>) names a CUA *attribute type*,
# each of which ISPF renders in a fixed colour. These are the authoritative
# defaults from the z/OS ISPF Dialog Developer's Guide, Table 11 "CUA TYPE default
# keyword values" (COLOR column) — the same colours ISPF paints CUA-typed text.
# TYPE=TEXT is deliberately absent: it is the non-CUA escape hatch that instead
# enables the explicit COLOR/INTENS/HILITE attributes (which we already honour).
#
# COLOUR ONLY. Table 11 also fixes each type's HIGH/LOW intensity (ET/CH/CT/WT are
# HIGH, the rest LOW), but we map *only* the colour: colour rides an SA order that
# a mono terminal ignores, so panels stay byte-identical; an intensity lives in the
# basic field-attribute byte, so honouring it would change mono output. (#218)
_CUA_TYPE_COLORS = {
    "et":   Color.TURQUOISE,  # emphasized text
    "ch":   Color.BLUE,       # column heading
    "ct":   Color.YELLOW,     # caution text
    "fp":   Color.GREEN,      # field prompt
    "lef":  Color.TURQUOISE,  # leading (entry) field
    "li":   Color.WHITE,      # list item
    "nt":   Color.GREEN,      # normal text
    "pt":   Color.BLUE,       # panel title
    "sac":  Color.WHITE,      # select-available choice
    "wasl": Color.BLUE,       # work-area separator line
    "wt":   Color.RED,        # warning text
}

# DTL OUTLINE=NONE | L | R | O | U | BOX → the 3270 field-outlining lines.
_OUTLINES = {
    "none": Outline.NONE, "l": Outline.LEFT, "r": Outline.RIGHT,
    "o": Outline.OVER, "u": Outline.UNDER, "box": Outline.BOX,
}

# Each DTL element is tagged with a CUA "role" (see screen._CUA_COLORS), so a
# colour terminal renders it in the standard z/OS colour for that kind of element
# unless it carries an explicit COLOR.

# Admonition tags: a note/callout that flows as a labelled block within body text
# (help panels). ATTENTION/WARNING/NOTE prefix the text inline; CAUTION puts its
# uppercase heading on its own line with the emphasised body beneath (see
# _emit_info). <notel> (below) is the list form.
_ADMONITIONS = {
    "note": "Note:", "nt": "Note:",           # note / inline note
    "attention": "Attention:", "caution": "CAUTION:", "warning": "Warning:",
}
# Block tags whose text flows as protected lines (like <info>): paragraphs,
# list items (<li>/<dt>/<dd>/<pt>/<pd>/<lp>), preformatted <lines>/<xmp>, and the
# admonitions above. Their list containers (<ul>/<ol>/<sl>/<dl>/<parml>/<notel>)
# are transparent — ignored (a plain container), except <notel>'s "Notes:"
# heading. A <sl> (simple list) marks its <li>s without a bullet (see below).
_FLOW_TEXT_TAGS = ("p", "li", "dt", "dd", "pt", "pd", "lp", "lines", "xmp") + tuple(_ADMONITIONS)
# Instruction tags render as protected text like <info>: <topinst> (top),
# <pnlinst> (panel), and <botinst> (bottom) instructions.
_INSTRUCTION_TAGS = ("topinst", "pnlinst", "botinst")
# Help-panel heading tags: <h1> (major) through <h4> (minor). They render as a
# high-intensity heading line in the text flow, sub-headings indented by level.
_HEADING_TAGS = ("h1", "h2", "h3", "h4")
_TEXT_TAGS = ("info",) + _INSTRUCTION_TAGS + _FLOW_TEXT_TAGS + _HEADING_TAGS
# ISPDTLC inserts a blank line BEFORE a flowed paragraph (<p>), a labelled
# paragraph (<lp>), or a panel instruction; COMPACT/NOSKIP suppress it (#210). A
# TOPINST instead gets a blank line AFTER it. See the P/LP/TOPINST tag references.
# (The other block elements — <lines>/<xmp>, <ul>/<ol>/<sl>/<notel>, <note>/<nt>,
# <dl>/<parml>, <fig> — take the same leading skip at their own emit sites.)
_BLANK_BEFORE_TAGS = ("p", "lp", "pnlinst")
_CONTENT_TAGS = _TEXT_TAGS + ("dtafld", "cmdarea", "choice", "chdiv", "figcap",
                              "grphdr", "dthd", "ddhd", "dldiv", "pldiv",
                              "textseg", "dtseg", "ptseg")
_FIELD_TAGS = ("dtafld", "cmdarea")


def _truthy(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in ("yes", "true", "1", "on")


def _bool_attr(attrs, key, default=False):
    """Read a boolean DTL attribute, honouring SGML attribute minimization.

    ``<dtafld numeric>`` (the attribute present with no value, ``html.parser``
    reports ``None``) and ``numeric="numeric"`` both mean true, as does any of
    yes/true/1/on. An absent attribute yields ``default``.
    """
    if key not in attrs:
        return default
    value = attrs[key]
    if value is None:
        return True
    return _truthy(value) or str(value).strip().lower() == key


def _intensity(attrs, key="intens", default=DisplayIntensity.NORMAL):
    # The standard DTL attribute is INTENS (valid on field/selection elements);
    # the non-standard ``intensity`` is no longer read (emphasis is <hp>).
    return _INTENSITY.get(str(attrs.get(key, "")).lower(), default)


def _resolve_color(value, subs):
    """Map a DTL COLOR value to a :class:`Color`, or None if absent/unknown.

    A ``%name`` value is a dialog-variable reference: its colour comes from the
    substitution ``subs`` (the same dict that resolves ``&NAME`` references),
    mirroring DTL's ``COLOR=%varname``.
    """
    v = str(value or "").strip()
    if v.startswith("%"):
        v = str((subs or {}).get(v[1:].upper(), ""))
    return _COLORS.get(v.strip().lower())


class DTLError(ValueError):
    """Raised when DTL markup is malformed (missing required attribute, etc.)."""


class _DTLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.screen = Screen()
        self._tag = None          # current content-bearing tag, or None
        self._attrs = None
        self._chars = []
        self._runs = None         # inline <hp> mixed-content runs, or None
        self._hp = None           # the open <hp>'s (color, highlight, intensity), or None
        self._selfld = None       # active <selfld> layout state, or None
        self._in_dtafldd = False  # capturing a <dtafldd> prompt child?
        self._dtafldd = None      # captured <dtafldd> prompt text, or None
        self._pending_ps = None   # open <ps>'s (var, value) awaiting its row, or None
        self._pending_chofld = None   # open <chofld>'s attrs (a choice's entry field)
        self._chofld_choicetext = None  # the choice text captured before a <chofld>
        self._pending_scrfld = None   # a <scrfld> awaiting its <dtafld>/<lstcol>
        self._assignl = None          # open <assignl> {"destvar", "pairs"} collecting <assigni>s
        self._pending_assignl = None  # a finished <assignl> awaiting its <dtafld>
        self._keylist = None      # active <keyl> bindings dict, or None
        self._keylist_name = None  # <keyl name=...> (the list's name), or None
        self._keylist_applid = None  # <keyl applid=...> (its application id), or None
        self._keylist_help = None  # <keyl help=...> (keylist help panel), or None
        self._keylist_action = None  # <keyl action=UPDATE|DELETE> (codegen), or None
        self._keyi = None         # open <keyi> awaiting its FKA-text content, or None
        self._varclasses = {}     # <varclass> name (upper) → {"numeric", "checks", "msg"}
        self._vardcls = {}        # <vardcl> name (upper) → {"varclass": name}
        self._cur_varclass = None # name of the <varclass> currently being defined
        self._checkl = None       # active <checkl> {"msg", "checks"} or None
        self._in_varlist = False  # inside a <varlist>?
        self._in_msgmbr = False   # inside a <msgmbr>?
        self._msgmbr_name = ""    # current <msgmbr name=...> (for <msg suffix>)
        self._msgmbr_width = None  # <msgmbr width=...>, or None
        self._msgmbr_ccsid = None  # <msgmbr ccsid=...>, or None
        self.messages = {}        # <msg> msgid (upper) → message text
        self._msg_attrs = {}      # <msg> msgid (upper) → {alarm, msgtype, smsg, help}
        self._areas = []          # stack of <area>/<region> flow contexts
        self._in_cmdtbl = False   # inside a <cmdtbl>?
        self._cur_cmd = None      # current <cmd> dict awaiting its <cmdact>
        self._cmd_chars = None    # current <cmd>'s captured external-name text, or None
        self._cmd_tpos = None     # offset of a <t> truncation point within it, or None
        self._ab = None           # active <ab> action bar being built, or None
        self._cur_abc = None      # current <abc> action-bar choice, or None
        self._cur_pdc = None      # current <pdc> pull-down choice, or None
        self._panel_title = None  # capturing the panel's title text, or None
        self._textline = None     # <textline> segments [(text, expand)], or None
        self._pandefs = {}        # <pandef id> → default attrs for <panel pandef=id>
        self._helpdefs = {}       # <helpdef id> → default attrs for <help helpdef=id>
        self._skip = None         # inside a non-rendering block [tag, chars, attrs]
                                  # — <comment>/<copyr>/<compopt>/<source>
        self._title_item = None   # the centered title Text (retracted on collision)
        self._title_rule = None   # the action-bar separator rule (retracted on collision)
        self._titline = True      # <panel titline=no> suppresses the on-screen title line
        self._tmargin = 0         # <panel/help TMARGIN=n> top margin (rows before content)
        self._bmargin = 0         # <panel/help BMARGIN=n> bottom margin (rows reserved)
        self._panel_cursor = None # <panel cursor=field-name> places the cursor at that field
        self._grpbox_pending = None  # a <region GRPBOX> whose title text is being captured
        self._lists = []          # stack of open <ul>/<ol> ({"type", "n"})
        self._note_hang = None    # hanging-indent col of an open <nt>, so its
                                  # nested blocks flow under the note body (#219)
        self._info_indent = 0     # <info indent=n>: extra columns its content is
                                  # shifted right (cleared at </info> / box close)
        self._lstfld = None       # active <lstfld> table {"cols", "groups", …}
        self._lstgrp = None       # innermost open <lstgrp> column group, or None
        self._lstgrp_stack = []   # open <lstgrp> groups, outermost first (nesting)
        self._scroll = None       # <lstfld scrollvar=> config for the command line
        self._xlatl = None        # active <xlatl> {"msg", "upper", "items"} or None
        self._rows = None         # data rows for the list field (datavar→value)
        self._subs = {}           # &NAME/%NAME substitution values (for COLOR=%var)
        self._da = None           # active <da> data area {row, col, attrs, body}
        # Presentation-size overrides (rows/cols): when set, they win over the
        # panel's declared/default size, so a panel can be laid out on a larger
        # alternate screen (e.g. a member list showing more rows on a model 3/4).
        self._override_rows = None
        self._override_cols = None
        # Paged-window position of ``_rows`` within the full table (see load_dtl):
        # the offset of the first supplied row and the full row count, driving the
        # ROW x TO y OF z status and the BOTTOM OF DATA marker. Defaults describe an
        # unpaged table (offset 0, total == len(rows)).
        self._row_offset = 0
        self._row_total = None

    # ── colour / highlight attributes ────────────────────────────────────────

    def _color(self, a):
        """The Color for a tag's COLOR= attribute (honouring %var), or None."""
        return _resolve_color(a.get("color"), self._subs)

    def _text_colour(self, a):
        """The heading/phrase colour for a CUA text tag (<hp>/<note>/<notel>/<nt>):
        its explicit COLOR= if any, else the standard CUA colour named by TYPE=
        (ET/CH/…; see _CUA_TYPE_COLORS). TYPE=TEXT and unknown TYPEs contribute
        nothing (COLOR alone, or None). Applied only where a tag legitimately reads
        TYPE as a CUA attribute type — not folded into _color, since other tags use
        TYPE for unrelated meanings (e.g. <divider type=dash>). #218"""
        return self._color(a) or _CUA_TYPE_COLORS.get(
            str(a.get("type", "")).strip().lower())

    def _hilite(self, a):
        """The Highlight for a tag's HILITE= attribute, or None."""
        return _HIGHLIGHTS.get(str(a.get("hilite", "")).strip().lower())

    def _outline(self, a):
        """The Outline for a tag's OUTLINE= attribute (NONE|L|R|O|U|BOX), or None.
        Field outlining draws the box line(s) around a field on an extended
        terminal; a mono terminal is unaffected."""
        return _OUTLINES.get(str(a.get("outline", "")).strip().lower())

    # ── inline <hp> (highlighted phrase) mixed content ───────────────────────

    @staticmethod
    def _message_attrs(a) -> dict:
        """Presentation attributes of a <msg>. ALARM defaults from MSGTYPE:
        WARNING/ACTION/CRITICAL messages sound the alarm, INFO does not (an
        explicit ALARM=YES/NO overrides). SMSG is the short-message text; HELP
        names the help panel the user reaches (PF1) while the message shows."""
        msgtype = str(a.get("msgtype", "")).strip().lower()
        if "alarm" in a:
            alarm = _truthy(a.get("alarm"))
        else:
            alarm = msgtype in ("warning", "action", "critical")
        return {"alarm": alarm, "msgtype": msgtype or None,
                "smsg": a.get("smsg"), "help": a.get("help"),
                # FORMAT=ASIS keeps the message's authored line breaks; FLOW (the
                # default) word-wraps to the member WIDTH (see MessageCatalog.lines).
                "format": str(a.get("format", "")).strip().lower() or None,
                # LOCATION (AREA/MODAL/MODELESS) is where the dialog shows the
                # message — a message area or a pop-up window. Recorded so the
                # server can place it; not a rendering effect here. #127.
                "location": str(a.get("location", "")).strip().lower() or None}

    def _hp_hilite(self, a):
        """The Highlight for an <hp> phrase: its HILITE= or the DTL TYPE= (both
        mapped through the highlight table), or None."""
        return (self._hilite(a)
                or _HIGHLIGHTS.get(str(a.get("type", "")).strip().lower()))

    def _hp_intensity(self, a):
        """The DisplayIntensity an <hp> phrase forces via INTENS=HIGH|LOW|NON (or
        INTENSE=%varname, resolved from a dialog variable like other %var attrs),
        or None when neither is present. 3270 has no *sub-normal* level, so
        LOW→NORMAL (documented); HIGH→HIGH, NON→NON_DISPLAY.

        Unlike colour/highlight (which ride an SA order inside one field), an
        intensity lives only in the BASIC field-attribute byte, set at a field
        start (SF). So a phrase that changes it can't be an SA run — it forces the
        enclosing line to SPLIT into separate fields (see _emit_flow_runs_intens).
        A value that maps to None (or plain NORMAL from LOW) needs no split; the
        common colour/highlight <hp> is untouched."""
        raw = a.get("intense", a.get("intens"))
        if raw is None:
            return None
        v = str(raw).strip()
        if v.startswith("%"):                     # INTENSE=%var → dialog variable
            v = str((self._subs or {}).get(v[1:].upper(), ""))
        return {
            "high": DisplayIntensity.HIGH,
            "low": DisplayIntensity.NORMAL,
            "non": DisplayIntensity.NON_DISPLAY,
        }.get(v.strip().lower())

    def _begin_hp(self, a):
        """Start an inline <hp> run: bank the text captured so far as a plain run,
        then capture the phrase as an emphasised run. The enclosing text element
        becomes a mixed-content Text.rich field (see _finalize_runs). Each run is
        ``(text, color, highlight, intensity)``; a plain run's emphasis is all
        None. INTENSITY (unlike colour/highlight) can't ride an SA order, so it is
        carried separately and, when non-normal, splits the line (see _emit_info /
        _emit_flow_runs_intens)."""
        if self._runs is None:
            self._runs = []
        self._runs.append(("".join(self._chars), None, None, None))
        self._chars = []
        self._hp = (self._text_colour(a), self._hp_hilite(a), self._hp_intensity(a))

    def _end_hp(self):
        """Close the open <hp>: bank its text as an emphasised run."""
        color, hilite, intensity = self._hp
        self._runs.append(("".join(self._chars), color, hilite, intensity))
        self._chars = []
        self._hp = None

    def _finalize_runs(self):
        """Bank the trailing text and return the mixed-content runs (dropping empty
        ones), or None when the element carried no inline <hp>."""
        if self._runs is None:
            return None
        if self._hp is not None:        # tolerate an <hp> left open at flush
            self._end_hp()
        else:
            self._runs.append(("".join(self._chars), None, None, None))
        self._chars = []
        return [r for r in self._runs if r[0]] or None


    # ── SGML event handling ──────────────────────────────────────────────────

    def handle_starttag(self, tag, attrs):
        a = {k: v for k, v in attrs}
        # Inside a non-rendering block (<comment>/<copyr>/<compopt>/<source>):
        # suppress all nested markup (only raw text is accumulated). A <panel>/
        # <help> can't be inside such a block — since these directives are often
        # coded WITHOUT an end tag (before the panel), the panel ends the block;
        # any other tag (a sibling directive or nested markup) is dropped.
        if self._skip is not None:
            if tag not in ("panel", "help"):
                return
            self._close_skip()
            # fall through to process the <panel>/<help>
        # The panel's title text (between <panel ...> and its first child) ends
        # at the first child tag (which we pass so an <ab> can push the title
        # below the action bar).
        if self._panel_title is not None:
            self._finalize_panel_title(tag)
        # A group box's title (the text between <region GRPBOX> and its first child)
        # ends at that first child tag — bank it and stop capturing (#125).
        if self._grpbox_pending is not None:
            self._finalize_grpbox_title()
        # Inline/annotating tags (<hp>/<ps>/<scrfld>/…) and non-rendering
        # directive blocks dispatch BEFORE the implicit flush below: they do not
        # close the open content element. A handler returns True when it consumed
        # the tag (an <hp>/<rp> outside a text element declines and falls through
        # to the ordinary block handling).
        inline = self._START_INLINE.get(tag)
        if inline is not None and inline(self, tag, a):
            return
        # Implicit end tags: a new block element closes the open content element
        # (DTL omits most end tags). <dtafldd> (a field's prompt/description) and
        # <lit> (a literal run inside e.g. an <xlati> external) are exceptions —
        # they are inline children that must not close their parent.
        if tag not in ("dtafldd", "lit") and self._tag is not None:
            self._emit_current()
        if tag in ("ul", "ol", "sl"):
            # <ul>/<ol> mark each item with a bullet/number; <sl> (simple list)
            # indents its items with no marker (see _emit_listitem). TEXT= gives
            # the list a heading line above its items; INDENT shifts the whole list
            # right; SPACE sets the item-text indentation (YES → 3 cols, else 4),
            # inherited by every <li> that does not carry its own SPACE (#123).
            # ISPDTLC also inserts a leading blank line before the list, ahead of any
            # heading (COMPACT/NOSKIP suppress it — #210).
            self._skip_blank_before(a)
            ctx = self._areas[-1] if self._areas else None
            indent = self._opt_int(a.get("indent"), 0)
            heading = str(a.get("text", "")).strip()
            if ctx is not None and heading:
                self.screen.add(Text(ctx["row"], ctx["col"] + indent, heading,
                                     role="text"))
                ctx["row"] += 2               # heading + a blank line before the items
            self._lists.append({"type": tag, "n": 0,
                                "indent": indent,
                                "space": self._space_indent(a)})
        elif tag == "notel":
            # A note list: a "Notes:" heading (TEXT= override, INTENS/COLOR/HILITE
            # style it), a blank line, then NUMBERED <li> items (1. 2. …).
            # ISPDTLC inserts a leading blank line before the heading (COMPACT/
            # NOSKIP suppress it — #210).
            self._skip_blank_before(a)
            ctx = self._areas[-1] if self._areas else None
            if ctx is not None:
                heading = (a.get("text") or "Notes:").strip()
                indent = self._opt_int(a.get("indent"), 0)
                self.screen.add(Text(ctx["row"], ctx["col"] + indent, heading,
                                     _intensity(a, "intens"), color=self._text_colour(a),
                                     highlight=self._hilite(a), role="text"))
                ctx["row"] += 2               # heading + blank line before the items
            # SPACE sets the item-text indentation: YES → 3 columns, else 4.
            self._lists.append({"type": "ol", "n": 0,
                                "space": self._space_indent(a)})
        elif tag in ("dl", "parml"):
            # A definition/parameter list carries its term-column width (tsize)
            # and break style; <dt>/<dd> (<pt>/<pd>) entries lay out against it.
            # ISPDTLC inserts a blank line before the list (COMPACT/NOSKIP suppress).
            self._skip_blank_before(a)
            # TSIZE='n' | 's1 s2 … sn' → one width per definition-term COLUMN; a
            # multi-column list codes one <dt> per width (see _emit_defitem).
            tsizes = [int(p) for p in str(a.get("tsize", "")).split() if p.isdigit()] \
                or [self._DL_TSIZE]
            self._lists.append({
                "type": tag, "n": 0,
                "tsizes": tsizes,
                "tsize": tsizes[0],           # first-column width (single-column paths)
                "col": 0,                     # current term-column index in the entry
                "seg_row": None,              # next <dtseg> stacking row for this column
                "break": a.get("break", "none").lower(),
                "compact": _bool_attr(a, "compact"),  # no blank after a <ddhd> header
                "indent": self._opt_int(a.get("indent"), 0),  # shift the list right
                # FORMAT positions the DT term within its TSIZE column.
                "format": str(a.get("format", "start")).strip().lower(),
                # DIVEND=YES draws a dashed rule across the list when it closes.
                "divend": _bool_attr(a, "divend"),
                "pending": None,
            })
        elif tag == "textline":
            # <textline> builds the panel/help title from its <textseg> segments,
            # replacing the tag's own title text (see _emit_textline). The empty
            # title captured before it was just flushed to nothing above.
            self._textline = []
        elif tag == "pandef":
            # <pandef id=…> defines reusable panel defaults (HELP/DEPTH/WIDTH/
            # KEYLIST/…) applied to any <panel PANDEF=id>. It renders nothing.
            pid = str(a.get("id", "")).strip().lower()
            if pid:
                self._pandefs[pid] = {k: v for k, v in a.items() if k != "id"}
        elif tag == "helpdef":
            # <helpdef id=…> is the help-panel analogue of <pandef>: shared help
            # defaults (HELP/DEPTH/WIDTH/KEYLIST/…) inherited by any <help HELPDEF=id>.
            # It renders nothing (#54).
            hid = str(a.get("id", "")).strip().lower()
            if hid:
                self._helpdefs[hid] = {k: v for k, v in a.items() if k != "id"}
        elif tag in ("dtdiv", "dthdiv", "ptdiv"):
            # A vertical `|` between definition-term (or -heading) columns; the
            # preceding <dt>/<dthd> was flushed just above, so its column state is set.
            self._emit_defdiv(tag)
        if tag in ("panel", "help"):
            # A <panel PANDEF=id> / <help HELPDEF=id> inherits the named default
            # block's attributes — the panel's own attributes win (setdefault fills
            # only what it omits). A panel carries PANDEF, a help panel HELPDEF (#54);
            # in practice only one is present, so applying both is harmless.
            for defaults in (self._pandefs.get(str(a.get("pandef", "")).strip().lower()),
                             self._helpdefs.get(str(a.get("helpdef", "")).strip().lower())):
                if defaults:
                    for k, v in defaults.items():
                        a.setdefault(k, v)
            # A top-level <help> is itself a (help) panel — same flow root. The
            # title is the panel's content text (panel-title-text), captured into
            # screen.title by _finalize_panel_title — not an attribute.
            self.screen.help = a.get("help")
            # TITLINE=NO keeps the title as metadata but suppresses its on-screen
            # line (default YES); see _finalize_panel_title.
            self._titline = _bool_attr(a, "titline", default=True)
            # PANEL CURSOR=field-name names the field the cursor starts in; the
            # replacement for the non-standard field-level cursor= (resolved in
            # close(), once every field has been emitted).
            self._panel_cursor = a.get("cursor")
            # Window/key-list metadata (#125). KEYLIST names the panel's key-list;
            # WINDOW=YES marks it a pop-up; WINTITLE is the pop-up's title; CURSOR is
            # the start field. None of these change the rendered field stream — they
            # are recorded on the Screen so the server/dialog can act on them (frame a
            # window, activate a key-list, …). Reached both directly and via
            # <pandef>/<helpdef> inheritance (the setdefault above), so honouring them
            # here covers both paths.
            if "keylist" in a:
                self.screen.keylist_ref = a.get("keylist")
            if "window" in a:
                self.screen.window = _bool_attr(a, "window", default=False)
            if "wintitle" in a:
                self.screen.window_title = a.get("wintitle")
            if a.get("cursor"):
                self.screen.cursor_field = a.get("cursor")
            # Panel classification / codepage metadata (#125, #117). MENU (a
            # selection menu), ACTBAR (force an action-bar area), CCSID (codepage)
            # and EXPAND=xy (the two field-expansion characters) have no host-display
            # effect on this single-byte text server — recorded so the dialog/
            # compiler can act on them. IMAP (image map) is GUI-only (dropped).
            if "menu" in a:
                self.screen.menu = _bool_attr(a, "menu", default=True)
            if "actbar" in a:
                self.screen.actbar = _bool_attr(a, "actbar", default=True)
            if a.get("ccsid"):
                self.screen.ccsid = a.get("ccsid")
            if a.get("expand"):
                self.screen.expand = a.get("expand")
            if self._override_cols is not None:
                self.screen.width = self._override_cols
            elif "width" in a:
                w = self._panel_dim(a["width"], self._WIDTH_MIN, self._WIDTH_MAX)
                if w is not None:
                    self.screen.width = w
            if self._override_rows is not None:
                self.screen.depth = self._override_rows
            elif "depth" in a:
                d = self._panel_dim(a["depth"], self._DEPTH_MIN, self._DEPTH_MAX)
                if d is not None:
                    self.screen.depth = d
            # TMARGIN/BMARGIN reserve rows at the top/bottom of the panel: the whole
            # panel (title + body) starts TMARGIN rows down, and content is kept out
            # of the last BMARGIN rows. Both default to 0, so an unmarked panel is
            # byte-for-byte unchanged. #125.
            self._tmargin = self._opt_int(a.get("tmargin"), 0) or 0
            self._bmargin = self._opt_int(a.get("bmargin"), 0) or 0
            # The panel itself is the root flow box: every element flows down
            # from the top (or from the top margin).
            self._areas.append(
                {"row": self._tmargin, "col": 1, "fldgap": 1, "explicit": True,
                 "parent": None}
            )
            self._panel_title = []  # capture the title text that follows
        elif tag == "selfld":
            ctx = self._areas[-1] if self._areas else None
            # ISPDTLC block spacing: a flowed selection field gets a leading blank
            # line (like a paragraph), then counts as content for the next block.
            self._skip_blank_before(a)
            if ctx is not None:
                ctx["had_content"] = True
            # The choice columns are offsets within the selection field, measured
            # from its origin column — the enclosing flow box's column (so a flowed
            # <selfld>, e.g. a dir=horiz column, shifts with the box). The number
            # sits at the origin, the keyword one gap past a 2-wide number, the
            # description one gap past that.
            origin = ctx["col"] if ctx else 1
            base = origin - 1
            self._selfld = {
                "row": ctx["row"] if ctx else 0,
                "numcol": base + 1,
                "namecol": base + 4,
                "desccol": base + 21,
                "numwidth": 2,
                # Auto-layout: a keyword-less <choice> puts its description at the
                # keyword column (right after the number) rather than the far
                # description column.
                "auto_cols": True,
                "numintensity": DisplayIntensity.HIGH,
                # DTL COLOR on a <selfld> colours its choices; a <choice> may
                # override with its own COLOR.
                "color": self._color(a),
                "ctx": ctx,
                "start_idx": len(self.screen.items),
                # TYPE=MULTI is a multiple-selection field: each choice gets its
                # own 1-char mark field (instead of a number the user types on a
                # command line), so several choices can be selected at once. SINGLE
                # (the default), MENU, MODEL and TUTOR keep the numbered layout.
                "multi": str(a.get("type", "single")).strip().lower() == "multi",
                "name": (a.get("name") or "").strip(),
                "count": 0,
                # FCHOICE is the number assigned to the first auto-numbered choice
                # (default 1); FCHOICE=0 numbers the choices 0..n-1, as the ISPF
                # primary menu does (option 0 = Settings). #128.
                "fchoice": self._opt_int(a.get("fchoice"), 1),
                # The field-prompt text (between <selfld ...> and the first
                # <choice>) — a caption above the list (PMTLOC=ABOVE, default) or
                # beside it (PMTLOC=BEFORE). Captured here, emitted before the first
                # choice. Empty (the bundled numbered menus) → nothing rendered.
                "origin": origin,
                "pmtloc": str(a.get("pmtloc", "above")).strip().lower(),
                "pmtwidth": self._opt_int(a.get("pmtwidth")),
                # SELWIDTH sizes the selection entry; absent → the enclosing
                # <dtacol>'s SELWIDTH default (#122).
                "selwidth": (self._opt_int(a["selwidth"]) if "selwidth" in a
                             else (ctx.get("selwidth") if ctx else None)),
                # PAD/PADC fill the selection entry; OUTLINE draws box lines around
                # it (applied to the field the user types into — the single-select
                # input field or each MULTI mark field). None → the plain defaults.
                "pad": self._pad_char(a),
                "outline": self._outline(a),
                # Multi-column choice grid (#128): CHOICECOLS columns, each
                # CHOICEDEPTH rows deep (choices fill down each column in turn —
                # column-major; row-major when no depth is given). CWIDTHS='w1 w2..'
                # sets each column's stride. SELFMT=START|END aligns the selection
                # entry within the selection width. DEPTH/EXTEND size the field.
                "choicecols": self._opt_int(a.get("choicecols"), 1) or 1,
                "choicedepth": self._opt_int(a.get("choicedepth")),
                "cwidths": [int(w) for w in str(a.get("cwidths", "")).split()
                            if w.isdigit()],
                "selfmt": str(a.get("selfmt", "start")).strip().lower(),
                "seldepth": (self._opt_int(a["depth"])
                             if "depth" in a and str(a["depth"]).strip() != "*"
                             else None),
                "extend": str(a.get("extend", "off")).strip().lower(),
                "field_row0": None,     # the row the first choice lands on
                "grid_maxrow": None,
                "prompt_chars": [],
                "prompt_done": False,
            }
            sf = self._selfld
            # A standard single-choice field (TYPE=SINGLE, the default; not
            # MENU/MODEL/TUTOR/MULTI) whose choices are auto-numbered (no explicit
            # NUM) and which has no explicit grid follows the CHOICE reference
            # figure: a selection input field precedes the first choice, and each
            # choice is numbered "N." (number + period). Decided on the first
            # choice (its NUM tells us). Explicit NUM / columns keep the fixed grid.
            sf["single_eligible"] = (sf["auto_cols"] and not sf["multi"]
                                     and sf["choicecols"] <= 1
                                     and str(a.get("type", "single")).strip().lower()
                                     == "single")
            # ENTWIDTH is 2 | n | 'e1 e2...en'; we take a single width (the list
            # form falls back to the default 2).
            sf["entwidth"] = self._opt_int(a.get("entwidth"), 2)
            sf["auto_single"] = False
            sf["period"] = False
        elif tag == "dtafldd":
            # The authentic data-field description (prompt) child of a field.
            if self._tag in _FIELD_TAGS:
                self._in_dtafldd, self._dtafldd = True, []
        elif tag == "keyl":
            # NAME identifies the keylist (referenced by <panel keylist=name>);
            # APPLID is the application it belongs to. Both are metadata recorded
            # on the Screen so the dialog can name/scope its keylist.
            self._keylist = {}
            self._keylist_name = a.get("name")
            self._keylist_applid = a.get("applid")
            # HELP names the keylist's help panel; ACTION=UPDATE|DELETE is a
            # keylist-table maintenance directive (codegen only). Both metadata.
            self._keylist_help = a.get("help")
            self._keylist_action = a.get("action")
        elif tag == "keyi":
            self._emit_keyi(a)
        elif tag == "cmdtbl":
            self._in_cmdtbl = True
            # APPLID scopes the command table to an application (metadata). SORT is
            # a compile-time ordering of the table with no host-display effect (the
            # table renders nothing — it only feeds command recognition). #126.
            if a.get("applid"):
                self.screen.commands_applid = a.get("applid")
        elif tag == "cmd":
            if not self._in_cmdtbl:
                raise DTLError("<cmd> outside of a <cmdtbl>")
            name = a.get("name")
            if not name:
                raise DTLError("<cmd> missing required attribute 'name'")
            self._finalize_cmd_trunc()   # close a previous <cmd> whose end tag was omitted
            # Truncation comes from a <t> marker in the external name (standard DTL);
            # ALTDESCR is the command's human description (metadata). trunc starts 0
            # and a nested <t> sets it (see _finalize_cmd_trunc).
            self._cur_cmd = {"action": "", "trunc": 0, "descr": a.get("altdescr", "")}
            self.screen.commands[name.upper()] = self._cur_cmd
            # Capture the command's external-name text so a nested <t> can mark its
            # truncation point.
            self._cmd_chars, self._cmd_tpos = [], None
        elif tag == "cmdact":
            # The command action; read on start, since DTL often omits </cmdact>.
            if self._cur_cmd is not None:
                self._cur_cmd["action"] = a.get("action", "")
        elif tag == "t":
            # A truncation point inside a <cmd> external name: the text before it is
            # the minimum abbreviation the user must type (<cmd>CANC<t>EL → trunc 4).
            if self._cmd_chars is not None:
                self._cmd_tpos = len("".join(self._cmd_chars).strip())
        elif tag == "ab":
            # The action bar sits on the top row; its choices are separated by a
            # fixed gap (the non-standard per-bar gap= attribute has been removed).
            # ABSEPSTR is the string drawn between the choices (IBM's default is two
            # blanks; we keep the wider gap when it is absent so bundled panels stay
            # byte-identical). ABSEPCHAR is the character of the separator *line*
            # ISPF draws on the row below the bar — both default to nothing shown.
            self._ab = {"row": 0, "col": 1, "gap": 3, "choices": [],
                        "absepstr": a.get("absepstr"),
                        "absepchar": a.get("absepchar"),
                        # MNEMGEN=YES auto-assigns the first letter of a choice as
                        # its mnemonic when it carries no explicit <M>. Treated as
                        # opt-in (absent → off) so existing bare-label action bars
                        # stay byte-identical; MNEMGEN=NO is also off. #126.
                        "mnemgen": str(a.get("mnemgen", "")).strip().lower()
                        == "yes"}
        elif tag == "abc":
            if self._ab is None:
                raise DTLError("<abc> outside of an <ab>")
            self._end_abc()                     # implicit end of a previous <abc>
            # PDCVAR names the )PROC variable that receives the selected pull-down
            # choice number — a dialog-variable concern with no host-display effect;
            # recorded on the choice model.
            self._cur_abc = {"chars": [], "pdc": [], "help": self._field_help(a),
                             "pdcvar": a.get("pdcvar")}
        elif tag == "pdc":
            if self._cur_abc is None:
                raise DTLError("<pdc> outside of an <abc>")
            self._end_pdc()                     # implicit end of a previous <pdc>
            # A pull-down item can be conditionally unavailable (shown but not
            # selectable), mirroring <choice unavail>: UNAVAIL=var greys it when the
            # variable is true. CHECKVAR=var MATCH=x marks it the *current* setting
            # (a "> " current-choice indicator) when the variable equals MATCH — the
            # pull-down analogue of <choice checkvar> landing on the current choice.
            unavail = "unavail" in a and self._var_truthy(a.get("unavail"))
            checkvar = a.get("checkvar")
            match = str(a.get("match", "")).strip().upper()
            checked = bool(checkvar) and \
                self._subs.get(str(checkvar).strip().upper(), "").strip().upper() == match
            # ACC1-3 are GUI keyboard accelerators (e.g. Ctrl+key) shown beside the
            # item in a GUI client; a text 3270 terminal has no accelerator display,
            # so they are recorded (GUI-only) but not rendered.
            acc = [a.get(k) for k in ("acc1", "acc2", "acc3") if a.get(k)]
            self._cur_pdc = {"chars": [], "action": "",
                             "help": self._field_help(a),
                             "unavail": unavail, "checked": checked,
                             "acc": acc}
        elif tag == "action":
            # A pull-down choice's command: <pdc>label<action run=cmd>. RUN= names
            # the command the choice runs. SETVAR/TOGVAR model the variable an action
            # assigns/toggles (an ISPF "Settings"-style on/off pull-down item): SETVAR
            # sets VAR to VALUE; TOGVAR flips VAR between VALUE1 and VALUE2. TYPE is
            # the action kind (CMD | PGM | PANEL | EXIT), defaulting to CMD.
            if self._cur_pdc is not None:
                self._cur_pdc["action"] = a.get("run") or self._cur_pdc["action"]
                if a.get("type"):
                    self._cur_pdc["type"] = str(a["type"]).strip().lower()
                if a.get("parm"):
                    self._cur_pdc["parm"] = a["parm"]
                if a.get("setvar"):
                    self._cur_pdc["setvar"] = (a["setvar"], a.get("value", "1"))
                if a.get("togvar"):
                    self._cur_pdc["togvar"] = (
                        a["togvar"], a.get("value1", "0"), a.get("value2", "1"))
                # APPLCMD/NEWAPPL/MODE/LANG and the other ACTION forms are ISPF
                # SELECT/command-dispatch semantics (how/where the command runs) —
                # no host-display effect. Recorded so the action model is lossless.
                for k in ("applcmd", "newappl", "mode", "lang"):
                    if a.get(k):
                        self._cur_pdc[k] = a[k]
        elif tag == "pdsep":
            # A separator line within an action-bar pull-down: close the choice
            # above it (DTL omits end tags) and record a divider row between the
            # pull-down choices. Rendered by _show_pulldown when the menu opens.
            if self._cur_abc is None:
                raise DTLError("<pdsep> outside of an <abc>")
            self._end_pdc()
            self._cur_abc["pdc"].append({"separator": True})
        elif tag == "m":
            # <M> marks the mnemonic character of an action-bar choice or pull-down
            # item — the shortcut letter ISPF shows highlighted. Record where it
            # falls in the label text being captured (offset in the raw chars).
            if self._cur_pdc is not None:
                self._cur_pdc["mnemonic"] = len("".join(self._cur_pdc["chars"]))
            elif self._cur_abc is not None:
                self._cur_abc["mnemonic"] = len("".join(self._cur_abc["chars"]))
        elif tag == "varclass":
            self._emit_varclass(a)
        elif tag == "checkl":
            if self._cur_varclass is None:
                raise DTLError("<checkl> outside of a <varclass>")
            self._checkl = {"msg": a.get("msg"), "checks": []}
        elif tag == "checki":
            if self._checkl is None:
                raise DTLError("<checki> outside of a <checkl>")
            self._tag, self._attrs, self._chars = "checki", a, []
        elif tag == "xlatl":
            if self._cur_varclass is None:
                raise DTLError("<xlatl> outside of a <varclass>")
            self._xlatl = {
                "msg": a.get("msg"),
                "upper": str(a.get("format", "")).strip().lower() == "upper",
                "items": [],
                "pairs": [],     # (internal value, external/displayed) translations
            }
        elif tag == "xlati":
            if self._xlatl is None:
                raise DTLError("<xlati> outside of an <xlatl>")
            self._tag, self._attrs, self._chars = "xlati", a, []
        elif tag == "varlist":
            self._in_varlist = True
        elif tag == "vardcl":
            self._emit_vardcl(a)
        elif tag == "lstfld":
            # A scrollable list/table: <lstcol> columns, optionally grouped under
            # a <lstgrp> heading. We render the static column header structure
            # (group headings + column headings); model data rows are populated
            # by the table service at runtime, so there are none to lay out here.
            ctx = self._areas[-1] if self._areas else None
            self._lstfld = {
                "cols": [], "groups": [], "ctx": ctx,
                "row": ctx["row"] if ctx else 0,
                "col": ctx["col"] if ctx else 1,
                "div": a.get("div", "none"),   # divider after each model set (raw)
            }
            self._lstgrp = None
            self._lstgrp_stack = []
            # SCROLLVAR puts a "Scroll ===>" amount field on the command line; the
            # <cmdarea> (coded after the list) picks this up when it renders.
            if a.get("scrollvar"):
                self._scroll = {
                    "var": a["scrollvar"],
                    "help": self._field_help(a and {"help": a.get("scrvhelp", "")}),
                    "tab": str(a.get("scrolltab", "")).strip().lower() == "yes",
                    "caps": str(a.get("scrcaps", "")).strip().lower() == "on",
                }
        elif tag == "lstgrp":
            if self._lstfld is None:
                raise DTLError("<lstgrp> outside of a <lstfld>")
            hv = a.get("headline")
            # HEADLINE=YES|DASH both draw the group's dashed rule (under NOGRAPHIC
            # they are identical); NO / absent draws the heading text alone.
            headline = "headline" in a and (
                hv is None or str(hv).lower() in ("yes", "dash", "true", "1", "headline")
            )
            align = a.get("align", "center").lower()
            # <lstgrp> nests: a group may contain child groups for a second heading
            # row. Track the open groups on a stack so a column binds to the
            # innermost group and each group records its parent and nesting depth.
            parent = self._lstgrp_stack[-1] if self._lstgrp_stack else None
            self._lstgrp = {"heading": "", "headline": headline, "align": align,
                            "parent": parent, "depth": parent["depth"] + 1 if parent else 1}
            self._lstgrp_stack.append(self._lstgrp)
            self._lstfld["groups"].append(self._lstgrp)
            self._tag, self._attrs, self._chars = "lstgrp", a, []  # capture heading
        elif tag == "lstcol":
            if self._lstfld is None:
                raise DTLError("<lstcol> outside of a <lstfld>")
            self._tag, self._attrs, self._chars = "lstcol", a, []  # capture heading
        elif tag == "da":
            # A data area: a free-form region whose body text carries inline
            # attribute characters (defined by nested <attr>) that start colour/
            # type fields, like the classic ISPF )ATTR + )BODY model.
            ctx = self._areas[-1] if self._areas else None
            self._da = {
                "row": ctx["row"] if ctx else 0,
                "col": ctx["col"] if ctx else 1,
                "attrs": {}, "body": [],
                "ctx": ctx,
                # DEPTH reserves a fixed height (the flow resumes DEPTH rows below
                # the area's top); WIDTH constrains the DIV span; DIV draws a closing
                # divider (SOLID/DASH rule, BLANK spacer, TEXT caption, FORMAT-placed).
                # DEPTH=* / absent → the body's own height. #125.
                "depth": (self._opt_int(a["depth"])
                          if "depth" in a and str(a["depth"]).strip() != "*" else None),
                "width": self._opt_int(a.get("width")),
                "div": str(a.get("div", "none")).strip().lower(),
                "divtext": " ".join(str(a.get("text", "")).split()),
                "divformat": str(a.get("format", "start")).strip().lower(),
                # EXTEND=ON|FORCE fills the remaining panel depth (like DEPTH=*).
                "extend": str(a.get("extend", "off")).strip().lower(),
            }
            # SCROLL/SCROLLVAR record the dynamic area's scroll intent (the body is
            # rendered statically here; LVLINE/USERMOD/DATAMOD/SHADOW are dynamic-
            # area / GDDM concerns with no static-render effect). #125.
            self.screen.dynamic_areas.append({
                "name": a.get("name"),
                "scroll": str(a.get("scroll", "off")).strip().lower(),
                "scrollvar": a.get("scrollvar"),
            })
        elif tag == "attr":
            self._emit_attr(a)
        elif tag == "dtacol":
            # A data-column flow box: like <area>, but it also carries default
            # prompt/entry widths (PMTWIDTH/ENTWIDTH) that its <dtafld>s inherit
            # so their captions and entries line up in a column.
            parent = self._areas[-1] if self._areas else None
            row = parent["row"] if parent else 0
            self._areas.append({
                "row": row, "row0": row, "maxbottom": row,
                "col": parent["col"] if parent else 1,
                # FLDSPACE sets the gap between a child field's prompt and its entry
                # (the flow's fldgap); absent → inherit the parent box's gap (#122).
                "fldgap": (int(a["fldspace"]) if "fldspace" in a
                           else (parent["fldgap"] if parent else 1)),
                "dir": str(a.get("dir", "vert")).strip().lower(),
                "start_idx": len(self.screen.items),
                "explicit": False,
                "parent": parent,
                "pmtwidth": (self._opt_int(a["pmtwidth"]) if "pmtwidth" in a
                             else (parent.get("pmtwidth") if parent else None)),
                "entwidth": (self._opt_int(a["entwidth"]) if "entwidth" in a
                             else (parent.get("entwidth") if parent else None)),
                # SELWIDTH defaults the selection-entry width of nested <selfld>s;
                # PMTLOC (BEFORE/ABOVE), REQUIRED, VARCLASS and CAPS default the
                # matching attribute of each child <dtafld>/<selfld>, the child's own
                # value overriding. All inherit through nested columns (#122).
                "selwidth": (self._opt_int(a["selwidth"]) if "selwidth" in a
                             else (parent.get("selwidth") if parent else None)),
                "pmtloc": (str(a["pmtloc"]).strip().lower() if "pmtloc" in a
                           else (parent.get("pmtloc") if parent else None)),
                "required": (str(a["required"]).strip().lower() if "required" in a
                             else (parent.get("required") if parent else None)),
                "varclass": (str(a["varclass"]) if "varclass" in a
                             else (parent.get("varclass") if parent else None)),
                "caps": (str(a["caps"]).strip().lower() if "caps" in a
                         else (parent.get("caps") if parent else None)),
                # PAD/PADC default the column's <dtafld> fill character; a field's
                # own PAD/PADC overrides it (see _add_field).
                "pad": self._pad_char(a) or (parent.get("pad") if parent else None),
                # OUTLINE (box lines) and DESWIDTH (description width) also default
                # the column's <dtafld>s, each field's own value overriding (#122).
                "outline": self._outline(a) or (parent.get("outline") if parent else None),
                "deswidth": (str(a["deswidth"]).strip() if "deswidth" in a
                             else (parent.get("deswidth") if parent else None)),
                # PMTFMT (CUA leader dots / ISPF / NONE / END) defaults the column's
                # <dtafld> prompt formatting; a field's own PMTFMT overrides it.
                "pmtfmt": (a["pmtfmt"] if "pmtfmt" in a
                           else (parent.get("pmtfmt") if parent else None)),
            })
        elif tag == "divider":
            ctx = self._areas[-1] if self._areas else None
            if ctx is not None and ctx.get("dir") == "horiz" and "row" not in a:
                # Inside a horizontal flow box a divider is a vertical gutter
                # between the columns either side of it: advance the column cursor
                # (by GUTTER, else the default gap) and draw no rule.
                ctx["col"] += int(a["gutter"]) if "gutter" in a else self._HGAP
            elif ctx is not None:
                # A horizontal rule spanning the rest of the flow box's width.
                row = ctx["row"]
                col = ctx["col"] if ctx else 1
                ctx["row"] = row + 1
                # TYPE=NONE/BLANK is a blank spacer (consumes the row but draws no
                # rule). Any other TYPE draws a rule; DASH/SOLID/TEXT differ in look,
                # and TEXT lays out the divider's own text — which follows the start
                # tag — so the rule is *deferred*: we fix its position now and emit it
                # at the flush (like a captured content element, see _emit_divider).
                if str(a.get("type", "dash")).strip().lower() not in ("none", "blank"):
                    if ctx.get("width"):
                        width = ctx["width"]          # span the box's fixed width
                    else:
                        width = max(1, self.screen.width - col - 1)
                    a["_row"], a["_col"], a["_width"] = row, col, width
                    self._tag, self._attrs, self._chars = "divider", a, []
                    self._in_dtafldd, self._dtafldd = False, None
        elif tag == "ga":
            self._emit_ga(a)
        elif tag in ("area", "region"):
            # A flow box that transparently continues the enclosing flow: its
            # content flows after the parent's, and the parent resumes after it.
            # DIR=HORIZ lays the box's children left-to-right instead of stacking
            # them top-to-bottom (side-by-side region columns).
            parent = self._areas[-1] if self._areas else None
            explicit = False
            # INDENT shifts the box's content that many columns to the right of its
            # origin (a <region indent=n>), nesting cumulatively.
            base_col = parent["col"] if parent else 1
            # MARGINW insets an <area>'s content horizontally (an AREA-only margin;
            # measured from the borderless origin, so the CUA default collapses to 0
            # — this text server draws no area border for the margin to sit inside).
            marginw = int(a["marginw"]) if (tag == "area" and "marginw" in a) else 0
            indent = base_col + (int(a["indent"]) if "indent" in a else 0) + marginw
            # MARGIND reserves blank rows above (and, at close, below) an <area>'s
            # content — again 0 by default with no border.
            margind = int(a["margind"]) if (tag == "area" and "margind" in a) else 0
            row = (parent["row"] if parent else 0) + margind
            # <region GRPBOX=YES> frames its content in a group box: a GE box border
            # (like the pull-down / other borders) with an optional title on the top
            # edge (#125). The border is drawn at the box's close (once its content
            # extent is known); here we just reserve the top-border row and inset the
            # content one column past the left border. Only regions (not areas) can be
            # group boxes, and only when GRPBOX is on — a plain box is unchanged.
            grpbox = tag == "region" and _bool_attr(a, "grpbox", default=False)
            box = {
                "row": row, "row0": row, "maxbottom": row,
                "col": indent,
                "fldgap": parent["fldgap"] if parent else 1,
                "dir": str(a.get("dir", "vert")).strip().lower(),
                "start_idx": len(self.screen.items),
                "explicit": explicit,
                "parent": parent,
                # WIDTH=n fixes the box's column width: a rule inside it spans
                # exactly WIDTH, and a horiz sibling starts WIDTH+gap to its right
                # regardless of the box's actual content (so a full-width divider
                # inside a left column doesn't shove the right column off-screen).
                "width": self._opt_int(a.get("width")) if "width" in a else None,
                # DIV draws a divider as the box's last line when it closes: SOLID/
                # DASH a dashed rule, BLANK a spacer, TEXT the divider text (FORMAT
                # positioned). NONE (default) draws nothing. #125.
                "div": str(a.get("div", "none")).strip().lower(),
                "divtext": " ".join(str(a.get("text", "")).split()),
                "divformat": str(a.get("format", "start")).strip().lower(),
                # DEPTH=n reserves a fixed height: the box occupies at least n rows
                # (the parent resumes DEPTH rows below its start), padding with blank
                # rows when the content is shorter. DEPTH=* / absent → the content's
                # own height (unchanged). #125.
                "depth": (self._opt_int(a["depth"])
                          if "depth" in a and str(a["depth"]).strip() != "*"
                          else None),
                # EXTEND=ON|FORCE grows the box to fill the remaining panel depth
                # (its bottom edge reaches the last usable row); OFF (default) uses
                # the content's own height. #125.
                "extend": str(a.get("extend", "off")).strip().lower(),
                # MARGIND also reserves blank rows below the content (see close).
                "margind": margind,
                # A box that transparently continues the parent's flow inherits its
                # content state, so the first paragraph below a panel title still
                # gets the CUA title/body separator. An explicitly-positioned box
                # starts fresh.
                "had_content": bool(parent and not explicit
                                    and parent.get("had_content")),
            }
            if grpbox:
                box["grpbox"] = True
                box["gb_row0"] = row                     # the top-border row
                box["gb_col"] = indent                   # border's left column
                box["gb_width"] = self._opt_int(a.get("grpwidth"))  # GRPWIDTH, or None
                # GRPBXVAR/GRPBXMAT conditionally draw the box: the border shows only
                # when the named dialog variable's value matches GRPBXMAT (default
                # "1"), exactly like CHOICE's CHECKVAR/MATCH. When the value is known
                # (a substitution is supplied) and does not match, the box is not
                # framed — the content flows as a plain region. LOCATION=TITLE routes
                # the group heading to the panel-title line instead of the box edge.
                box["gb_var"] = a.get("grpbxvar")
                box["gb_match"] = str(a.get("grpbxmat", "1"))
                box["gb_location"] = str(a.get("location", "default")).strip().lower()
                box["gb_title_chars"] = []
                box["gb_title"] = ""
                box["row"] = box["row0"] = box["maxbottom"] = row + 1  # content below top
                box["col"] = indent + 2                  # inset past │ + a pad column
                self._grpbox_pending = box               # capture the group-box title
            self._areas.append(box)
        elif tag == "fig":
            # A figure: a flow sub-box, optionally framed by a horizontal rule
            # (FRAME=RULE, the default) above and below its content, with a
            # <figcap> caption line beneath. Its children (<p>, lists, <xmp>, …)
            # flow through the box like an <area>.
            # ISPDTLC inserts a leading blank line before the figure (COMPACT/
            # NOSKIP suppress it — #210); it advances the parent flow cursor before
            # we snapshot the figure's origin row below.
            self._skip_blank_before(a)
            parent = self._areas[-1] if self._areas else None
            col = parent["col"] if parent else 1
            row = parent["row"] if parent else 0
            frame = str(a.get("frame", "rule")).strip().lower() != "none"
            # WIDTH=PAGE (default) frames to the page width; WIDTH=COL frames only
            # the enclosing column's width (a figure inside a width-constrained
            # <region>), so the rule doesn't overrun the column.
            pw = parent.get("width") if parent else None
            if str(a.get("width", "page")).strip().lower() == "col" and pw:
                width = max(1, pw)
            else:
                width = max(1, self.screen.width - col - 1)
            if frame:                                  # top rule
                self.screen.add(Text(row, col, "-" * width))
                row += 1
            self._areas.append({
                "row": row, "row0": row, "maxbottom": row, "col": col,
                "fldgap": parent["fldgap"] if parent else 1, "dir": "vert",
                "start_idx": len(self.screen.items), "explicit": False,
                "parent": parent, "fig": True, "frame": frame,
                "fig_col": col, "fig_width": width, "caption": None,
            })
        elif tag == "msgmbr":
            self._in_msgmbr = True
            self._msgmbr_name = a.get("name", "")
            self._msgmbr_width = self._opt_int(a.get("width"))
            self._msgmbr_ccsid = self._opt_int(a.get("ccsid"))
        elif tag == "msg":
            if not self._in_msgmbr:
                raise DTLError("<msg> outside of a <msgmbr>")
            if "msgid" not in a:
                # ISPF forms the id from the member name + a per-message suffix
                # (e.g. <msgmbr name=abcd00><msg suffix=1> → abcd001).
                if "suffix" in a and self._msgmbr_name:
                    a["msgid"] = self._msgmbr_name + a["suffix"]
                else:
                    raise DTLError("<msg> missing required attribute 'msgid'")
            self._tag, self._attrs, self._chars = "msg", a, []
        elif tag in _CONTENT_TAGS:
            if tag == "info":
                # <info indent=n> shifts its whole content right; the flow picks
                # this up in _resolve_pos until the matching </info> (or box end).
                self._info_indent = self._opt_int(a.get("indent"), 0)
            self._tag, self._attrs, self._chars = tag, a, []
            # A new content tag closes any still-open <dtafldd> (SGML omits the
            # end tag), so the dtafldd capture state must not leak into it.
            self._in_dtafldd, self._dtafldd = False, None

    # ── start handlers: inline / annotating tags ─────────────────────────────
    # Dispatched from handle_starttag via _START_INLINE, BEFORE the implicit
    # flush — these tags do not close the open content element. Each returns
    # True when it consumed the tag; False falls through to block handling.

    def _inline_start_skip(self, tag, a):
        # Non-rendering blocks. <comment> (a comment), <copyr> (copyright),
        # <compopt> (ISPDTLC compiler options) and <generate> (a build-time
        # directive that generates panels/messages from a model) have no
        # host-display effect in this display server, so their content is
        # dropped. <source> ()INIT/)PROC logic also renders nothing, but its raw
        # text is kept for the ZSEL selection routing (see _close_skip). #119.
        self._skip = [tag, [], a]
        return True

    def _inline_start_hp(self, tag, a):
        # An inline <hp> (highlighted phrase) inside a text element does NOT close
        # it — it emphasises a phrase *within* one field. Bank the runs and return
        # before the implicit flush (see _begin_hp / _finalize_runs). A <rp>
        # (reference phrase — a hypertext link to another help panel) is the same
        # kind of inline emphasis; with no explicit emphasis it renders underlined,
        # the CUA point-and-shoot link style.
        if not (self._tag in _TEXT_TAGS or self._tag == "divider"):
            return False
        self._begin_hp(a)
        if tag == "rp" and self._hp == (None, None, None):
            self._hp = (None, Highlight.UNDERSCORE, None)
        return True

    def _inline_start_varsub(self, tag, a):
        # <varsub var=NAME> substitutes a dialog variable inside message text: emit
        # an ISPF ``&NAME.`` reference into the text being captured, resolved at
        # display time (MessageCatalog.format) exactly like a literal &NAME would be.
        var = a.get("var")
        if var:
            self.handle_data(f"&{var}.")
        return True

    def _inline_start_ps(self, tag, a):
        # <ps> (point-and-shoot): an inline phrase whose text the user can select by
        # cursor — placing the cursor on it and pressing Enter sets VAR to VALUE
        # (before )PROC). Like <hp>/<rp> it does NOT close its parent and its text
        # stays part of the parent's content; the (var, value) is banked here and
        # mapped to the parent's row when the parent is emitted (_emit_current).
        # VALUE=* on a <ps> in a <choice> means "the choice's number" (resolved in
        # _emit_choice). The point-and-shoot text is color-emphasised on real ISPF
        # colour terminals; in host/mono it renders like the surrounding text.
        var = a.get("var")
        if var:
            self._pending_ps = (var, str(a.get("value", "")))
            # CSRGRP (cursor group) and DEPTH (rows the phrase spans) have no
            # host-display effect on a text terminal; record them as metadata.
            if "csrgrp" in a or "depth" in a:
                self.screen.ps_meta.append(
                    {"var": var, "csrgrp": a.get("csrgrp"),
                     "depth": self._opt_int(a.get("depth"))})
        return True

    def _inline_start_chofld(self, tag, a):
        # <chofld> (choice data field): an input field within a <choice> row. The
        # text captured before it is the choice description; the text after it is the
        # field's own description. Both are banked and laid out when the choice is
        # emitted (_emit_choice); like <dtafldd> it does not close its parent.
        if self._tag == "choice":
            self._chofld_choicetext = "".join(self._chars)
            self._chars = []
            self._pending_chofld = a
        return True

    def _inline_start_scrfld(self, tag, a):
        # <scrfld> (scrollable field): annotates the enclosing <dtafld>/<lstcol>,
        # making it horizontally scrollable — DISPLEN is the field's logical data
        # length (wider than the on-screen window, which stays the field's
        # entwidth/colwidth), and the indicator attributes name scroll-status
        # variables. It does not close its parent (attached when the field/column is
        # emitted); finalise an open <dtafldd> capture first so a description isn't
        # swallowed.
        if self._in_dtafldd and isinstance(self._dtafldd, list):
            self._in_dtafldd, self._dtafldd = False, "".join(self._dtafldd)
        self._pending_scrfld = a
        return True

    def _inline_start_assignl(self, tag, a):
        # <assignl>/<assigni> (assignment list): a value→result table attached to
        # the enclosing <dtafld>. Like <scrfld> it annotates the field without
        # closing it — each <assigni value=v result=r> adds a mapping; the finished
        # list is attached when the <dtafld> is emitted (_attach_assignl). It is the
        # surface syntax for an ISPF )PROC `&destvar = TRANS(&field v,'r' …)`
        # assignment (see #55, docs/dtl-action-routing-plan.md Phase 2 PR B).
        # Finalise an open <dtafldd> capture first so a description isn't swallowed.
        if self._in_dtafldd and isinstance(self._dtafldd, list):
            self._in_dtafldd, self._dtafldd = False, "".join(self._dtafldd)
        self._assignl = {"destvar": a.get("destvar"), "pairs": []}
        self._pending_assignl = self._assignl
        return True

    def _inline_start_assigni(self, tag, a):
        # A VALUE with no RESULT assigns the empty string (ISPF's TRANS default);
        # a stray <assigni> outside an <assignl> carries nowhere, so drop it.
        if self._assignl is not None and a.get("value") is not None:
            self._assignl["pairs"].append((a.get("value"), a.get("result", "")))
        return True

    def _close_skip(self):
        """Leave the current non-rendering block. A <source>'s accumulated text is
        handed to _emit_source (ZSEL routing); the rest is discarded."""
        tag, chars, attrs = self._skip
        self._skip = None
        if tag == "source":
            self._emit_source(attrs, "".join(chars))

    def handle_data(self, data):
        if self._skip is not None:
            self._skip[1].append(data)   # accumulate the block's raw text
            return
        if self._panel_title is not None:
            self._panel_title.append(data)
        elif self._in_dtafldd and self._dtafldd is not None:
            self._dtafldd.append(data)
        elif self._cur_pdc is not None:
            self._cur_pdc["chars"].append(data)
        elif self._cur_abc is not None:
            self._cur_abc["chars"].append(data)
        elif self._keyi is not None:
            self._keyi["chars"].append(data)   # a <keyi>'s FKA-text
        elif self._da is not None:
            self._da["body"].append(data)
        elif self._grpbox_pending is not None:
            # Text between <region GRPBOX> and its first child is the group-box title.
            self._grpbox_pending["gb_title_chars"].append(data)
        elif self._tag is not None:
            self._chars.append(data)
        elif self._cmd_chars is not None:
            # A <cmd>'s external-name text (between it and its <cmdact>/end tag),
            # captured so a nested <t> can locate the truncation point.
            self._cmd_chars.append(data)
        elif (self._selfld is not None and not self._selfld["prompt_done"]):
            # Text between <selfld ...> and its first <choice> is the field prompt.
            self._selfld["prompt_chars"].append(data)

    def handle_endtag(self, tag):
        # End of a non-rendering block on its explicit end tag. <source> feeds its
        # accumulated raw text to _emit_source (ZSEL routing); the others are
        # dropped. A non-matching end tag inside the block is ignored.
        if self._skip is not None:
            if tag == self._skip[0]:
                self._close_skip()
            return
        if self._panel_title is not None:
            self._finalize_panel_title()
        # Inline/annotating end tags dispatch BEFORE the implicit flush below:
        # they must not close the enclosing content element. A handler returns
        # True when it consumed the tag (an </hp>/</rp> with no open runs
        # declines and falls through to the ordinary block handling).
        inline = self._END_INLINE.get(tag)
        if inline is not None and inline(self, tag):
            return
        # A container closing flushes any open content child first (end tags are
        # omitted in DTL), while its context is still intact. The element's own
        # end tag is handled below via the normal `tag == self._tag` path.
        if self._tag is not None and tag != self._tag and tag not in ("dtafldd", "lit"):
            self._emit_current()  # flush at the current list depth, before any pop
        if tag in ("nt", "note"):
            # Flush the note's own text if no nested child already did, then end the
            # hanging indent so a following sibling block flows at the box column (#219).
            if self._tag == tag:
                self._emit_current()
            self._note_hang = None
            return
        if tag == "info":
            # The info's own text (if any) was flushed above; end its indent so a
            # following sibling flows at the box column again (#123).
            if self._tag == tag:
                self._emit_current()
            self._info_indent = 0
            return
        if tag == "textline":
            self._emit_textline()
            return
        if tag == "da":
            self._emit_da()
            self._da = None
            return
        if tag in ("ul", "ol", "sl", "dl", "parml", "notel") and self._lists:
            lst = self._lists[-1]
            # <dl>/<parml DIVEND=YES>: a dashed rule spanning the list as it closes.
            if lst.get("divend") and self._areas:
                ctx = self._areas[-1]
                col = ctx["col"] + lst.get("indent", 0)
                span = max(1, self.screen.width - col - 1)
                self.screen.add(Text(ctx["row"], col, "-" * span,
                                     DisplayIntensity.NORMAL, role="rule"))
                ctx["row"] += 1
            self._lists.pop()
        if tag in ("panel", "help"):
            if self._da is not None:      # a <da> with an omitted end tag
                self._emit_da()
                self._da = None
            while self._areas and self._areas[-1].get("fig"):
                self._close_fig()         # a <fig> whose </fig> was omitted
            self._close_open_grpboxes()   # frame any <region GRPBOX> left open (#125)
            self._retract_title_if_collision()
            self._areas.clear()  # drop the panel's implicit flow box
            self._info_indent = 0
            return
        if tag == "selfld":
            # Advance the enclosing flow past the choices just laid out.
            sf = self._selfld
            if sf:
                self._emit_selfld_prompt(sf)   # a prompt-only selfld still shows it
            if sf and sf.get("choicecols", 1) > 1 and sf.get("grid_maxrow") is not None:
                # A multi-column grid tracked its deepest row; resume below it. #128.
                sf["row"] = sf["grid_maxrow"] + 1
            if sf and sf.get("field_row0") is not None:
                # DEPTH reserves a fixed height for the field; EXTEND fills to the
                # panel foot. Both measured from the field's first row. #128.
                if sf.get("seldepth"):
                    sf["row"] = max(sf["row"], sf["field_row0"] + sf["seldepth"])
                if sf.get("extend") in ("on", "force"):
                    sf["row"] = max(sf["row"], self.screen.depth - self._bmargin)
            if sf and sf.get("ctx") is not None:
                ctx = sf["ctx"]
                if ctx.get("dir") == "horiz":
                    self._flow_horiz(ctx, sf.get("start_idx", len(self.screen.items)))
                else:
                    ctx["row"] = sf["row"]
            self._selfld = None
            return
        if tag == "dtafldd":
            if self._in_dtafldd:
                self._in_dtafldd, self._dtafldd = False, "".join(self._dtafldd)
            return
        if tag == "keyi":
            self._finalize_keyi()
            return
        if tag == "keyl":
            self._finalize_keyi()       # flush the last <keyi> (its end tag is omitted)
            self.screen.keylist = self._keylist or {}
            self.screen.keylist_name = self._keylist_name
            self.screen.keylist_applid = self._keylist_applid
            self.screen.keylist_help = self._keylist_help
            self.screen.keylist_action = self._keylist_action
            self._keylist = self._keylist_name = self._keylist_applid = None
            self._keylist_help = self._keylist_action = None
            return
        if tag == "cmdtbl":
            self._finalize_cmd_trunc()
            self._in_cmdtbl = False
            self._cur_cmd = None
            self._cmd_chars = self._cmd_tpos = None
            return
        if tag == "pdc":
            self._end_pdc()
            return
        if tag == "abc":
            self._end_abc()
            return
        if tag == "ab":
            self._end_abc()                 # close any open <abc>/<pdc> (implicit)
            if self._ab is not None:
                self._emit_action_bar(self._ab)
            self._ab = None
            return
        if tag == "cmd":
            self._finalize_cmd_trunc()
            self._cur_cmd = None
            self._cmd_chars = self._cmd_tpos = None
            return
        if tag == "varclass":
            # An <xlatl format=upper> marks the whole class case-insensitive, even
            # when it is written after the <xlatl> that lists the translations — so
            # apply the class's upper flag to every xlati check now that all its
            # <xlatl>s are closed (order-independent matching).
            vc = self._varclasses.get(self._cur_varclass)
            if vc and vc.get("upper"):
                for c in vc["checks"]:
                    if c.get("type") == "xlati" and not c["upper"]:
                        c["upper"] = True
                        c["values"] = [v.upper() for v in c["values"]]
            self._cur_varclass = None
            return
        if tag == "checkl":
            if self._checkl is not None and self._cur_varclass in self._varclasses:
                vc = self._varclasses[self._cur_varclass]
                vc["checks"].extend(self._checkl["checks"])
                # The <checkl>'s own MSG names the failure message; fall back to the
                # class-level <varclass msg=> (which also covers TYPE-derived checks).
                vc["msg"] = self._checkl["msg"] or vc.get("msg")
            self._checkl = None
            return
        if tag == "xlatl":
            self._end_xlatl()
            return
        if tag == "lstgrp":
            # Pop back to the enclosing group (nested <lstgrp>); the open <lstcol>,
            # if any, was flushed above and bound to this group.
            if self._lstgrp_stack:
                self._lstgrp_stack.pop()
            self._lstgrp = self._lstgrp_stack[-1] if self._lstgrp_stack else None
            return
        if tag == "lstfld":
            if self._lstfld is not None:
                self._emit_lstfld()
            self._lstfld, self._lstgrp = None, None
            self._lstgrp_stack = []
            return
        if tag == "varlist":
            self._in_varlist = False
            return
        if tag == "msgmbr":
            self._in_msgmbr = False
            return
        if tag == "fig":
            self._close_fig()
            return
        if tag in ("area", "region", "dtacol"):
            self._info_indent = 0    # an <info> can't outlive its enclosing box
            if self._areas:
                ctx = self._areas.pop()
                # MARGIND (an <area> depth margin) reserves blank rows below the
                # content as well as above it.
                if ctx.get("margind"):
                    ctx["row"] += ctx["margind"]
                # DEPTH=n reserves a fixed height: pad the box out to n rows so the
                # parent flow resumes DEPTH rows below the box's start.
                if ctx.get("depth"):
                    floor = ctx["row0"] + ctx["depth"]
                    if ctx["row"] < floor:
                        ctx["row"] = floor
                    ctx["maxbottom"] = max(ctx.get("maxbottom", ctx["row"]), ctx["row"])
                # EXTEND=ON|FORCE grows the box to the last usable panel row (kept
                # out of the bottom margin), so the flow after it resumes at the foot.
                if ctx.get("extend") in ("on", "force"):
                    floor = self.screen.depth - self._bmargin
                    if ctx["row"] < floor:
                        ctx["row"] = floor
                    ctx["maxbottom"] = max(ctx.get("maxbottom", ctx["row"]), ctx["row"])
                # DIV draws a divider as the box's last line (SOLID/DASH a rule,
                # BLANK a spacer, TEXT the divider text), advancing the box cursor.
                # With DEPTH, the box was padded first, so the rule sits at the
                # reserved bottom edge.
                if ctx.get("div") not in (None, "none", ""):
                    self._emit_area_div(ctx)
                if ctx.get("grpbox"):
                    # A title-only group box may still be capturing; bank it first,
                    # then frame the content and drop the flow below the bottom edge.
                    if self._grpbox_pending is ctx:
                        self._finalize_grpbox_title()
                    self._draw_grpbox(ctx)
                parent = ctx.get("parent")
                if parent is not None and not ctx.get("explicit"):
                    # A horizontal child spans down to its tallest column; a
                    # vertical one down to its row cursor.
                    child_bottom = (ctx.get("maxbottom", ctx["row"])
                                    if ctx.get("dir") == "horiz" else ctx["row"])
                    if parent.get("dir") == "horiz":
                        # Side-by-side: advance the parent's column past this child
                        # box and keep the parent on its origin row. A WIDTH-capped
                        # box advances by its declared width (so its content, e.g. a
                        # full-width rule, can't shove the next column off-screen);
                        # otherwise fall back to the box's actual right extent.
                        if ctx.get("width"):
                            parent["col"] = ctx["col"] + ctx["width"] + self._HGAP
                        else:
                            _, right = self._box_extent(ctx["start_idx"])
                            if right is not None:
                                parent["col"] = right + self._HGAP
                        parent["maxbottom"] = max(parent.get("maxbottom", parent["row0"]),
                                                  child_bottom)
                        parent["row"] = parent["row0"]
                    else:
                        parent["row"] = child_bottom  # resume flow below the box
            return
        if tag != self._tag:
            return
        self._emit_current()

    # ── end handlers: inline / annotating tags ───────────────────────────────
    # Dispatched from handle_endtag via _END_INLINE, BEFORE the implicit flush.
    # Each returns True when it consumed the tag.

    def _inline_end_hp(self, tag):
        # Closing an inline <hp>/<rp> banks its emphasised run and keeps the
        # enclosing text element open (it is not a block child).
        if self._runs is None:
            return False
        self._end_hp()
        return True

    def _inline_end_assignl(self, tag):
        # </assignl>: closes the assignment list (no more <assigni> items) but keeps
        # it pending for the enclosing <dtafld> to attach; the field it annotates
        # stays open, so this must consume the tag before the implicit flush.
        self._assignl = None
        return True

    def _inline_end_noop(self, tag):
        # </ps>, </chofld>, </scrfld>, </assigni>: inline/annotating children handled
        # on their start tag. They must not close the enclosing content element (the
        # choice/field/text they sit inside stays open), so a stray end tag is a no-op.
        # <varsub> likewise is an empty tag whose text was injected on the start tag —
        # a (rare) explicit </varsub> must not prematurely close the enclosing <msg>.
        return True

    # ── tag-handler registries ───────────────────────────────────────────────
    # {tag -> handler} dispatch tables for handle_starttag / handle_endtag.
    # The *_INLINE registries hold the inline/annotating tags dispatched before
    # the implicit flush (handlers return True when they consume the tag).
    # Values are plain functions (class-body references), called with an
    # explicit ``self`` — the same registry pattern as server._SELECTION_HANDLERS.

    _START_INLINE = {
        "comment": _inline_start_skip,
        "copyr": _inline_start_skip,
        "compopt": _inline_start_skip,
        "generate": _inline_start_skip,
        "source": _inline_start_skip,
        "hp": _inline_start_hp,
        "rp": _inline_start_hp,
        "varsub": _inline_start_varsub,
        "ps": _inline_start_ps,
        "chofld": _inline_start_chofld,
        "scrfld": _inline_start_scrfld,
        "assignl": _inline_start_assignl,
        "assigni": _inline_start_assigni,
    }

    _END_INLINE = {
        "hp": _inline_end_hp,
        "rp": _inline_end_hp,
        "assignl": _inline_end_assignl,
        "ps": _inline_end_noop,
        "chofld": _inline_end_noop,
        "scrfld": _inline_end_noop,
        "assigni": _inline_end_noop,
        "varsub": _inline_end_noop,
    }

    def close(self):
        """Flush at end-of-input. DTL routinely omits end tags, so a panel can
        reach EOF with its title still being captured (a title-only panel, e.g.
        ``<panel>Widgets`` with no body or ``</panel>``) or with an open content
        element — finalise them exactly as the matching end tag would have."""
        super().close()
        if self._skip is not None:        # a block whose end tag was omitted at EOF
            self._close_skip()
        if self._panel_title is not None:
            self._finalize_panel_title()
        if self._tag is not None:
            self._emit_current()
        while self._areas and self._areas[-1].get("fig"):
            self._close_fig()             # a <fig> whose </fig> was omitted
        self._close_open_grpboxes()       # frame any <region GRPBOX> left open (#125)
        self._retract_title_if_collision()
        self._place_panel_cursor()

    def _close_open_grpboxes(self):
        """Draw the border for any <region GRPBOX> still open at panel/EOF close
        (DTL routinely omits end tags). Innermost first; the flow is being torn
        down, so only the framing matters, not resuming a parent."""
        for box in reversed(self._areas):
            if box.get("grpbox") and not box.get("gb_drawn"):
                if self._grpbox_pending is box:
                    self._finalize_grpbox_title()
                box["gb_drawn"] = True
                self._draw_grpbox(box)

    def _place_panel_cursor(self):
        """Honour <panel cursor=field-name>: put the cursor in the named field.

        The standard replacement for the non-standard field-level cursor= — a
        panel names one of its fields and the cursor starts there. Matched
        case-insensitively against the field's NAME (its datavar)."""
        if not self._panel_cursor:
            return
        want = self._panel_cursor.strip().upper()
        for it in self.screen.items:
            if isinstance(it, Field) and (it.name or "").upper() == want:
                it.cursor = True
                return

    def _emit_current(self):
        """Emit the open content element (``self._tag``) and reset capture state.

        Called both on an explicit end tag and implicitly when the next block
        tag starts — DTL omits most end tags, so an element is closed by what
        follows it."""
        tag = self._tag
        if tag is None:
            return
        # An implicitly-closed <dtafldd> (no </dtafldd> — closed by the tag that
        # follows) is still an open list here; finalize it to a string so it can
        # serve as the field's prompt or its trailing description.
        if isinstance(self._dtafldd, list):
            self._dtafldd = "".join(self._dtafldd)
        # If the active flow box lays out horizontally, note where its next child's
        # items begin so we can advance its column cursor past them afterwards.
        # Choices are excluded — they belong to a <selfld>, flowed at its close.
        box = self._areas[-1] if self._areas else None
        horiz = (box is not None and box.get("dir") == "horiz"
                 and self._selfld is None and "row" not in (self._attrs or {}))
        start_idx = len(self.screen.items)
        # Inline <hp> runs (only text tags can carry them, see _begin_hp).
        runs = self._finalize_runs()
        # A <dtafldd> child, if present, supplies the prompt; otherwise the
        # element's own text is the prompt (a convenient shorthand).
        content = self._dtafldd if isinstance(self._dtafldd, str) else "".join(self._chars)
        a = self._attrs
        if tag == "li":
            self._emit_listitem(a, content, runs)
        elif tag in ("dt", "dd", "pt", "pd"):
            self._emit_defitem(tag, a, content)
        elif tag in ("dtseg", "ptseg"):
            self._emit_dtseg(tag, content)
        elif tag in ("dthd", "ddhd"):
            self._emit_defhead(tag, a, content)
        elif tag in ("dldiv", "pldiv"):
            self._emit_listdiv(a, content)
        elif tag == "divider":
            self._emit_divider(a, content, runs)
        elif tag == "textseg":
            if self._textline is not None:
                seg = " ".join(content.split())
                width = self._opt_int(a.get("width"))
                if width:                     # WIDTH reserves space for the segment
                    seg = seg[:width].ljust(width)
                self._textline.append((seg, str(a.get("expand", "")).strip().lower()))
        elif tag in ("lines", "xmp"):
            # <xmp> (example) is preformatted like <lines>: authored line breaks
            # and interior spacing are significant.
            self._emit_lines(a, content)
        elif tag == "lstgrp":
            if self._lstgrp is not None:
                self._lstgrp["heading"] = " ".join(content.split())
        elif tag == "lstcol":
            self._add_lstcol(a, content)
        elif tag in ("note", "nt"):
            self._emit_note(tag, a, content)
        elif tag in _HEADING_TAGS:
            self._emit_heading(tag, a, content, runs=runs)
        elif tag == "grphdr":
            self._emit_grphdr(a, content, runs=runs)
        elif tag in _TEXT_TAGS:
            self._emit_info(a, content, tag, runs=runs)
        elif tag == "dtafld":
            # A <dtafld> row is prompt + entry + description. The inline text is
            # the prompt and a nested <dtafldd> is the trailing description; but
            # when the field has *only* a <dtafldd> (no inline text), it stands in
            # as the prompt (the shorthand the bundled panels use).
            inline = "".join(self._chars)
            dtafldd = self._dtafldd if isinstance(self._dtafldd, str) else None
            if dtafldd is not None and inline.strip():
                prompt, description = inline, dtafldd
            else:
                prompt, description = content, None
            self._emit_dtafld(a, prompt, description)
        elif tag == "cmdarea":
            self._emit_cmdarea(a, content)
        elif tag == "choice":
            self._emit_choice(a, content)
        elif tag == "chdiv":
            self._emit_chdiv(a, content)
        elif tag == "msg":
            mid = a["msgid"].upper()
            self.messages[mid] = content.strip()
            self._msg_attrs[mid] = self._message_attrs(a)
        elif tag == "checki":
            self._emit_checki(a, content)
        elif tag == "xlati":
            self._emit_xlati(a, content)
        elif tag == "figcap":
            self._store_figcap(content)
        if horiz:
            self._flow_horiz(box, start_idx)
        # A <ps> phrase inside this element makes its row point-and-shoot: map the
        # row the element rendered on to the phrase's (var, value). _emit_choice may
        # have resolved a VALUE=* to the choice number by now.
        if self._pending_ps is not None:
            rows = [it.row for it in self.screen.items[start_idx:]
                    if hasattr(it, "row")]
            if rows:
                self.screen.ps_rows[min(rows)] = self._pending_ps
            self._pending_ps = None
        self._tag, self._attrs, self._chars = None, None, []
        self._runs, self._hp = None, None
        self._dtafldd, self._in_dtafldd = None, False

    def handle_startendtag(self, tag, attrs):
        # Self-closing form, e.g. <dtafld .../> or <divider/>
        self.handle_starttag(tag, attrs)
        if tag in ("comment", "copyr", "compopt", "generate", "source"):
            # A self-closing non-rendering directive (<generate/>, <comment/>, …)
            # opened a skip block with no content; close it so the following markup
            # is not swallowed. #119.
            self._close_skip()
        elif tag in _CONTENT_TAGS:
            self.handle_endtag(tag)
        elif tag in ("ul", "ol", "sl", "dl", "parml", "notel"):  # empty list; pop it
            self.handle_endtag(tag)
        elif tag == "dtafldd":  # empty prompt
            self.handle_endtag(tag)
        elif tag == "selfld":  # a self-closing selfld has no choices; close it
            self._selfld = None
        elif tag == "keyl":  # a self-closing keylist has no items; close it
            self.handle_endtag(tag)
        elif tag == "keyi":  # a self-closing key item has no FKA-text; close it
            self.handle_endtag(tag)
        elif tag == "msg":  # a self-closing msg has empty text
            self.handle_endtag(tag)
        elif tag == "checki":  # a self-closing checki carries params in attrs
            self.handle_endtag(tag)
        elif tag == "xlati":  # a self-closing xlati (no external text)
            self.handle_endtag(tag)
        elif tag == "assignl":  # a self-closing (empty) assignment list; close it
            self.handle_endtag(tag)
        elif tag == "varclass":  # a self-closing varclass has no checks; close it
            self.handle_endtag(tag)
        elif tag in ("lstfld", "lstgrp", "lstcol"):  # self-closing list elements
            self.handle_endtag(tag)
        elif tag == "varlist":  # a self-closing varlist declares nothing; close it
            self._in_varlist = False
        elif tag == "msgmbr":  # a self-closing msgmbr declares nothing; close it
            self._in_msgmbr = False
        elif tag == "cmdtbl":  # a self-closing command table is empty; close it
            self.handle_endtag(tag)
        elif tag == "cmd":  # a self-closing cmd (action only via cmdact); close it
            self.handle_endtag(tag)
        elif tag in ("ab", "abc", "pdc"):  # self-closing action-bar elements
            self.handle_endtag(tag)
        elif tag in ("area", "region"):  # a self-closing flow box has no content
            self.handle_endtag(tag)

    # ── element → model ──────────────────────────────────────────────────────

    @staticmethod
    def _req_int(attrs, key, tag):
        if key not in attrs:
            raise DTLError(f"<{tag}> missing required attribute '{key}'")
        return int(attrs[key])

    def _resolve_pos(self, a, tag):
        """Resolve an element's ``(row, col)`` from the enclosing flow box — the
        row cursor advances one line per element — and return it with that box.
        Every element auto-flows; there is no explicit positioning."""
        ctx = self._areas[-1] if self._areas else None
        if ctx is None:
            raise DTLError(f"<{tag}> outside any flow box")
        row = ctx["row"]
        ctx["row"] = row + 1
        # Inside an open <nt>, a nested block (a following <p>, list, …) hangs at
        # the note's body indent, not back at the enclosing box column (#219). An
        # <info indent=n> shifts its whole content that many columns to the right.
        col = (self._note_hang if self._note_hang is not None
               else ctx["col"]) + self._info_indent
        if row >= self.screen.depth - self._bmargin:
            # An auto-flowed element ran past the panel bottom (a tall panel plus
            # our block spacing, or into the BMARGIN reserve); clamp to the last
            # usable row rather than abort the panel, as the column clamp below does
            # for the horizontal overflow.
            row = self.screen.depth - 1 - self._bmargin
        if col >= self.screen.width:
            # An auto-flowed column ran off the panel — our side-by-side
            # (dir=horiz) column math only approximates ISPDTLC's, so clamp to the
            # edge rather than abort the whole panel.
            col = max(0, self.screen.width - 2)
        ctx["had_content"] = True   # real content — a later block skips before it
        return row, col, ctx

    # Unordered-list bullets by nesting depth (ISPF: o, then -, then --, …).
    _BULLETS = ("o", "-", "--", "---")
    _LIST_INDENT = 4   # columns added per nesting level
    _DL_TSIZE = 10     # default <dl>/<parml> term-column width (chars)
    _DL_GAP = 1        # gap between multi-column definition terms / before the desc
    _HGAP = 2          # default column gap between side-by-side (dir=horiz) items
    # <panel> WIDTH/DEPTH validation bounds (z/OS ISPF DTL Guide, PANEL tag).
    _WIDTH_MIN, _WIDTH_MAX = 16, 160
    _DEPTH_MIN, _DEPTH_MAX = 5, 62

    @staticmethod
    def _panel_dim(value, lo, hi):
        """A validated <panel> WIDTH/DEPTH: the integer if it is in ``[lo, hi]``,
        else ``None`` (keep the default). FIT, ``%varname`` and any out-of-range
        or non-numeric value fall back to the default, as ISPDTLC does (it warns
        and uses the default rather than the out-of-range value)."""
        v = str(value).strip().upper()
        if v == "FIT" or v.startswith("%"):
            return None
        try:
            n = int(v)
        except ValueError:
            return None
        return n if lo <= n <= hi else None

    @staticmethod
    def _opt_int(value, default=None):
        """``int(value)`` or ``default`` when absent / non-numeric — e.g. a size
        attribute's ``*`` / ``**`` / ``FIT`` / ``%varname`` / quoted-list form,
        which the docs allow but we do not compute (fall back rather than crash)."""
        try:
            return int(str(value).strip())
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _prompt_width(value, prompt_len, avail):
        """Resolve a PMTWIDTH ``n | * | **`` value: ``*`` = the prompt-text length,
        ``**`` = the maximum available width, ``n`` = that many bytes. Non-numeric
        junk falls back to the prompt length. (DTAFLD/DTACOL/SELFLD PMTWIDTH.)"""
        v = str(value).strip()
        if v == "*":
            return prompt_len
        if v == "**":
            return avail
        if v.startswith("%"):
            return prompt_len
        try:
            return int(v)
        except ValueError:
            return prompt_len

    @staticmethod
    def _format_prompt(prompt, pmtwidth, usage_out, pmtfmt):
        """Format a data-field prompt to PMTWIDTH per PMTFMT (z/OS DTL, DTAFLD):
        ``cua`` (default) fills the leader with CUA dots, ``ispf`` puts ``===>`` in
        the rightmost 4 bytes, ``end`` right-justifies, ``none`` left-justifies.
        USAGE=OUT makes the last prompt byte a colon."""
        pmtfmt = (pmtfmt or "cua").strip().lower()
        p = prompt
        if pmtwidth and pmtwidth > len(prompt):
            if pmtfmt == "cua":
                p = prompt + "".join(
                    "." if (j - len(prompt)) % 2 else " "
                    for j in range(len(prompt), pmtwidth))
            elif pmtfmt == "ispf":
                p = (prompt.ljust(pmtwidth - 4) + "===>"
                     if pmtwidth - len(prompt) >= 4 else prompt.ljust(pmtwidth))
            elif pmtfmt == "end":
                p = prompt.rjust(pmtwidth)
            # else "none": no leader characters added (prompt unchanged)
        if usage_out and prompt:               # trailing colon for an output field
            p = (p[:pmtwidth - 1] + ":") if pmtwidth and len(p) >= pmtwidth else p + ":"
        return p

    def _box_extent(self, start_idx):
        """The ``(bottom, right)`` extent of the screen items added since
        ``start_idx`` — ``bottom`` is one past the deepest row, ``right`` one past
        the rightmost column. Returns ``(None, None)`` if nothing was added. Used
        to flow horizontal boxes and resume a parent below a child box."""
        bottom = right = None
        for it in self.screen.items[start_idx:]:
            text = getattr(it, "text", None)
            if text is not None:               # a Text (possibly multi-line)
                lines = text.split("\n")
                height, wide = len(lines), max((len(ln) for ln in lines), default=0)
            else:                              # a Field
                height, wide = 1, getattr(it, "length", 0)
            b, r = it.row + height, it.col + wide
            bottom = b if bottom is None else max(bottom, b)
            right = r if right is None else max(right, r)
        return bottom, right

    def _flow_horiz(self, box, start_idx):
        """After a child was laid into a horizontal flow box, advance the box's
        column cursor past it (so the next sibling sits to its right) and reset the
        row cursor to the box's origin. The tallest child is remembered so the
        enclosing flow resumes below the whole row of columns."""
        bottom, right = self._box_extent(start_idx)
        if right is not None:
            box["col"] = right + self._HGAP
        if bottom is not None:
            box["maxbottom"] = max(box.get("maxbottom", box["row0"]), bottom)
        box["row"] = box["row0"]

    def _finalize_panel_title(self, next_tag=None):
        """Emit the panel's title text (centered) and start the flow below it.
        Called when the first child tag follows the ``<panel>``.

        With an action bar (``next_tag == "ab"``) the bar takes row 0, so CUA draws
        a separator rule on row 1 and centers the title on row 2, with the body
        flowing below a blank line. Otherwise the title is centered on row 0.

        With ``TITLINE=NO`` the title is kept as ``Screen.title`` metadata only —
        no on-screen line — and the body flows from the top (the line is free)."""
        text = re.sub(r"\s+", " ", "".join(self._panel_title or [])).strip()
        self._panel_title = None
        if not text:
            return
        if self.screen.title is None:
            self.screen.title = text
        if not self._titline:            # TITLINE=NO: metadata only, no title line
            return
        if next_tag == "ab":
            rule = Text(1 + self._tmargin, 0, "-" * max(1, self.screen.width - 1))
            self.screen.add(rule)
            self._title_rule = rule
            title_row, flow_row = 2 + self._tmargin, 4 + self._tmargin
        else:
            title_row, flow_row = self._tmargin, 1 + self._tmargin
        col = max(0, (self.screen.width - len(text)) // 2)
        item = Text(title_row, col, text, DisplayIntensity.NORMAL)
        self.screen.add(item)
        self._title_item = item
        if self._areas and self._areas[-1]["row"] < flow_row:
            self._areas[-1]["row"] = flow_row  # flow starts below the title
        # The title is content in the panel box, so the first flowed paragraph
        # skips a blank line below it — the CUA title/body separator.
        if self._areas:
            self._areas[-1]["had_content"] = True

    def _emit_textline(self):
        """Emit the panel/help title built from a <textline>'s <textseg> segments.

        Segments accumulate left to right. A segment with EXPAND acts as a pivot
        (the classic ISPF title line: time on the left, title centred via
        EXPAND=BOTH, date on the right); segments before it are left-justified and
        those after are right-justified. With no EXPAND the whole line is centred
        as the panel title (per the reference)."""
        segs, self._textline = self._textline or [], None
        if not segs:
            return
        row = 2 if any(getattr(it, "row", None) == 0 for it in self.screen.items) \
            else 0                              # below an action bar if row 0 is taken
        width = self.screen.width
        pivot = next((i for i, (_, e) in enumerate(segs) if e), None)
        if pivot is None:                       # no EXPAND → centre the whole line
            text = "".join(t for t, _ in segs)
            if self.screen.title is None:
                self.screen.title = text
            if self._titline and text:
                col = max(0, (width - len(text)) // 2)
                self.screen.add(Text(row, col, text, DisplayIntensity.NORMAL))
        else:
            left = "".join(t for t, _ in segs[:pivot])
            centre = segs[pivot][0]
            right = "".join(t for t, _ in segs[pivot + 1:])
            if self.screen.title is None:
                self.screen.title = " ".join(x for x in (left, centre, right) if x)
            if self._titline:
                if left:
                    self.screen.add(Text(row, 1, left, DisplayIntensity.NORMAL))
                if centre:
                    col = max(0, (width - len(centre)) // 2)
                    self.screen.add(Text(row, col, centre, DisplayIntensity.NORMAL))
                if right:
                    self.screen.add(Text(row, max(0, width - 1 - len(right)), right,
                                         DisplayIntensity.NORMAL))
        # Flow the body below the title line.
        if self._areas and self._areas[-1]["row"] <= row:
            self._areas[-1]["row"] = row + 1
            self._areas[-1]["had_content"] = True

    def _emit_ga(self, a):
        """Reserve a graphic area (<ga>): DEPTH lines framed by an optional DIV
        divider before and after. The graphic data itself (the NAME dialog
        variable) is GDDM/image content a TN3270 text terminal cannot display
        (see #102), so the reserved region renders blank; only the DIV rules draw."""
        ctx = self._areas[-1] if self._areas else None
        if ctx is None:
            return
        col = ctx["col"]
        width = self._opt_int(a.get("width")) or max(1, self.screen.width - col - 1)
        extend = str(a.get("extend", "off")).strip().lower() in ("on", "force")
        if str(a.get("depth", "")).strip() == "*" or (extend and "depth" not in a):
            # DEPTH=* or EXTEND=ON|FORCE → fill the remaining panel depth. #117.
            depth = max(1, self.screen.depth - ctx["row"] - 2)
        else:
            depth = max(1, self._opt_int(a.get("depth"), 1) or 1)
        div = str(a.get("div", "none")).strip().lower()
        row = ctx["row"]
        if div not in ("none", ""):
            self._ga_divider(row, col, width, div, a)  # divider before
            row += 1
        row += depth                                   # reserve the graphic region
        if div not in ("none", ""):
            self._ga_divider(row, col, width, div, a)  # divider after
            row += 1
        ctx["row"] = row
        ctx["had_content"] = True

    def _ga_divider(self, row, col, width, div, a):
        """One <ga> DIV line: BLANK → nothing; SOLID/DASH → a dashed rule; TEXT →
        the divider-text positioned by FORMAT within the width."""
        if div == "blank":
            return
        if div == "text":
            text = " ".join(str(a.get("text", "")).split())[:width]
            fmt = str(a.get("format", "start")).strip().lower()
            off = (width - len(text)) if fmt == "end" \
                else (width - len(text)) // 2 if fmt == "center" else 0
            self.screen.add(Text(row, col + max(0, off), text, role="rule"))
        else:                                          # solid / dash → dashed rule
            self.screen.add(Text(row, col, "-" * width, role="rule"))

    def _retract_title_if_collision(self):
        """Drop an auto title (and its action-bar separator rule) that collides
        with an explicit element on the same row. The bundled panels draw their own
        title rule / title, so the auto ones must not duplicate them — this keeps
        those panels byte-identical to before the content-title form. A ``status``
        element (an <lstfld> "ROW x OF y" on the title line's right) is not a title
        and doesn't count as a collision."""
        for attr in ("_title_item", "_title_rule"):
            item = getattr(self, attr, None)
            setattr(self, attr, None)
            if item is not None and any(
                it is not item and getattr(it, "row", None) == item.row
                and getattr(it, "role", None) != "status"
                for it in self.screen.items
            ):
                self.screen.items.remove(item)

    def _store_figcap(self, content):
        """Capture a <figcap>'s text for the enclosing <fig>; it is rendered below
        the figure (after the bottom rule) at </fig>, not inline where it appears."""
        text = " ".join(content.split())
        for box in reversed(self._areas):
            if box.get("fig"):
                box["caption"] = text
                return

    def _close_fig(self):
        """Close the innermost open <fig>: draw the bottom rule (FRAME=RULE), emit
        the <figcap> caption beneath it, and resume the parent flow below both."""
        if not self._areas or not self._areas[-1].get("fig"):
            return
        box = self._areas.pop()
        row, col, width = box["row"], box["fig_col"], box["fig_width"]
        if box["frame"]:                                # bottom rule
            self.screen.add(Text(row, col, "-" * width))
            row += 1
        if box["caption"]:                              # caption line(s) beneath
            lines = self._wrap(box["caption"], width)
            for i, ln in enumerate(lines):
                self.screen.add(Text(row + i, col, ln, DisplayIntensity.NORMAL))
            row += len(lines)
        parent = box.get("parent")
        if parent is not None and not box.get("explicit"):
            parent["row"] = row                         # parent resumes below the figure

    def _wrap(self, text, width):
        """Greedy word-wrap ``text`` into lines no wider than ``width``."""
        words, lines, cur = text.split(), [], ""
        for w in words:
            if not cur:
                cur = w
            elif len(cur) + 1 + len(w) <= width:
                cur += " " + w
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines or [""]

    @staticmethod
    def _roman(n):
        vals = ((10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"))
        out = ""
        for v, s in vals:
            while n >= v:
                out += s; n -= v
        return out

    def _ol_marker(self, n, ol_depth):
        """Ordered-list marker: arabic at level 1, lowercase alpha at level 2,
        lowercase roman at level 3 (CUA-style nested numbering)."""
        if ol_depth == 2:
            return chr(ord("a") + (n - 1) % 26) + "."
        if ol_depth >= 3:
            return self._roman(n) + "."
        return f"{n}."

    def _emit_flow_lines(self, text, row, col, ctx, marker=None, marker_col=None,
                         role=None, intensity=DisplayIntensity.NORMAL, offset=0):
        """Word-wrap ``text`` and emit it as protected lines from ``row`` at
        ``col`` (hanging indent for continuations). Optionally place a ``marker``
        (bullet/number) on the first line. ``offset`` indents the second and all
        following lines that many extra columns (the DTL ``<p OFFSET=n>`` hanging
        indent). Advances the flow cursor."""
        lines = self._wrap(text, max(1, self.screen.width - (col + offset + 1)))
        if marker is not None:
            self.screen.add(Text(row, marker_col, marker, DisplayIntensity.NORMAL,
                                 role=role))
        for i, ln in enumerate(lines):
            self.screen.add(Text(row + i, col + (offset if i else 0), ln,
                                 intensity, role=role))
        if ctx is not None:
            ctx["row"] = row + len(lines)

    @staticmethod
    def _wrap_runs(runs, width):
        """Word-wrap mixed-emphasis <hp> ``runs`` [(text, color, hilite)] into
        lines, each a list of runs. Whitespace is collapsed to single spaces (as
        the plain flow path does); each character — including the spaces inside a
        highlighted phrase — keeps its run's emphasis, so a phrase survives a wrap
        boundary with its highlight/colour intact."""
        chars, prev_space = [], True         # (char, color, hilite), ws-collapsed;
        for text, color, hilite in runs:     # a collapsed space keeps its run's emph
            for ch in text:
                if ch.isspace():
                    if not prev_space:
                        chars.append((" ", color, hilite))
                    prev_space = True
                else:
                    chars.append((ch, color, hilite))
                    prev_space = False
        while chars and chars[-1][0] == " ":
            chars.pop()
        # Split into words, each with the emphasis of the space that followed it
        # (that space becomes the joiner if the next word stays on the same line).
        words, cur = [], []                  # each: (word_chars, (color, hilite)|None)
        for ch, color, hilite in chars:
            if ch == " ":
                words.append((cur, (color, hilite))); cur = []
            else:
                cur.append((ch, color, hilite))
        if cur:
            words.append((cur, None))
        lines, line, w, prev_sp = [], [], 0, None   # greedy pack words into width
        for word, sp in words:
            if not line:
                line, w = list(word), len(word)
            elif w + 1 + len(word) <= width:
                sc, sh = prev_sp if prev_sp else (None, None)
                line.append((" ", sc, sh)); line.extend(word); w += 1 + len(word)
            else:
                lines.append(line); line, w = list(word), len(word)
            prev_sp = sp
        if line:
            lines.append(line)
        out = []                             # coalesce each line's chars into runs
        for ln in lines:
            packed = []
            for ch, color, hilite in ln:
                if packed and packed[-1][1] == color and packed[-1][2] == hilite:
                    packed[-1] = (packed[-1][0] + ch, color, hilite)
                else:
                    packed.append((ch, color, hilite))
            out.append(packed)
        return out

    def _emit_flow_runs(self, runs, row, col, ctx, role):
        """Emit word-wrapped <hp> runs as protected lines: a line carrying any
        emphasis becomes a Text.rich (SA colour/highlight per phrase), otherwise a
        plain Text. Mono renders either as the plain text, so it is byte-identical
        to the non-<hp> flow path."""
        lines = self._wrap_runs(runs, max(1, self.screen.width - (col + 1)))
        for i, line_runs in enumerate(lines):
            if any(c is not None or h is not None for _, c, h in line_runs):
                self.screen.add(Text.rich(row + i, col, line_runs,
                                          intensity=DisplayIntensity.NORMAL, role=role))
            else:
                self.screen.add(Text(row + i, col, "".join(t for t, _, _ in line_runs),
                                     DisplayIntensity.NORMAL, role=role))
        if ctx is not None:
            ctx["row"] = row + len(lines)

    @staticmethod
    def _wrap_runs_intens(runs, width):
        """Word-wrap INTENS-bearing <hp> ``runs`` [(text, color, hilite, intens)]
        into lines, each a FLAT list of ``(char, color, hilite, intens)`` (unlike
        _wrap_runs it does not coalesce — the per-field split at emit needs the
        char-level intensity). Whitespace is collapsed to single spaces exactly as
        the plain flow path, and a wrap boundary drops its joining space, so the
        wrapped text matches _wrap byte-for-byte."""
        chars, prev_space = [], True         # collapse runs of whitespace to one space
        for text, color, hilite, intens in runs:
            for ch in text:
                if ch.isspace():
                    if not prev_space:
                        chars.append((" ", color, hilite, intens))
                    prev_space = True
                else:
                    chars.append((ch, color, hilite, intens))
                    prev_space = False
        while chars and chars[-1][0] == " ":
            chars.pop()
        words, cur = [], []                  # split into words + the space after each
        for c in chars:
            if c[0] == " ":
                words.append((cur, c)); cur = []
            else:
                cur.append(c)
        if cur:
            words.append((cur, None))
        # Greedy pack words to width. The joiner between two words on a line is the
        # space that FOLLOWED the previous word (prev_sp) — it keeps that space's
        # own emphasis/intensity, so a same-intensity phrase's interior space is not
        # spuriously demoted (and thus not split into two fields). A wrap drops it.
        lines, line, w, prev_sp = [], [], 0, None
        for word, sp in words:
            if not line:
                line, w = list(word), len(word)
            elif w + 1 + len(word) <= width:
                line.append(prev_sp if prev_sp else (" ", None, None, None))
                line.extend(word); w += 1 + len(word)
            else:                            # wrap: the joining space is dropped
                lines.append(line); line, w = list(word), len(word)
            prev_sp = sp
        if line:
            lines.append(line)
        return lines or [[]]

    def _emit_flow_runs_intens(self, runs, row, col, ctx, role):
        """Emit <hp> runs when a phrase carries a non-normal INTENS (HIGH / NON).

        3270 has NO extended-intensity SA order — display intensity lives in the
        BASIC field-attribute byte, set only at a field start (SF). So a mid-line
        intensity change can't be an SA run inside one field; the line must be
        SPLIT into a separate protected field per intensity run, each with its own
        SF (and thus its own DisplayIntensity). The inter-phrase space is CONSUMED
        as the next field's attribute byte — which itself displays as a blank — so
        the visible column layout is identical to the single-field version; only
        now the phrase's field carries the intensified / non-display attribute.
        Colour/highlight WITHIN a split segment still ride SA runs (Text.rich).

        Word-wrap is honoured (each wrapped line is split independently). A caveat:
        an intensity change that is NOT on a word boundary (no space to reuse for
        the SF byte) can't be split without shifting a column — such mid-word
        <hp intens=…> is vanishingly rare in ISPF panels and is left as-is."""
        lines = self._wrap_runs_intens(runs, max(1, self.screen.width - (col + 1)))
        for i, line in enumerate(lines):
            self._emit_intens_segments(line, row + i, col, role)
        if ctx is not None:
            ctx["row"] = row + len(lines)

    def _emit_intens_segments(self, line, row, col, role):
        """Split one collapsed line of ``(char, color, hilite, intens)`` into a
        field per intensity run and add them. Each field's SF attribute byte sits
        one column left of its first character — on the inter-phrase space that the
        grouping trims away — so the SF (a blank cell) lands exactly where that
        space was and the columns match the single-field layout. LOW/None collapse
        to NORMAL (no distinct 3270 level), so only HIGH / NON_DISPLAY actually
        break the line."""
        if not line:
            return
        N = DisplayIntensity.NORMAL
        def eff(ii):                          # LOW/None/unknown → NORMAL
            return ii if ii in (DisplayIntensity.HIGH,
                                DisplayIntensity.NON_DISPLAY) else N
        segs, cur, cur_int = [], [], None     # group consecutive same-intensity chars
        for j, (ch, cc, hh, ii) in enumerate(line):
            e = eff(ii)
            if cur and e is cur_int:
                cur.append((j, ch, cc, hh))
            else:
                if cur:
                    segs.append((cur, cur_int))
                cur, cur_int = [(j, ch, cc, hh)], e
        if cur:
            segs.append((cur, cur_int))
        for grp, intens in segs:
            # Trim the boundary spaces: those cells become the adjacent fields' SF
            # attribute bytes (which render blank), keeping the columns aligned.
            k0, k1 = 0, len(grp)
            while k0 < k1 and grp[k0][1] == " ":
                k0 += 1
            while k1 > k0 and grp[k1 - 1][1] == " ":
                k1 -= 1
            kept = grp[k0:k1]
            if not kept:
                continue
            # single-field column of char index jj is col+1+jj; its SF is one left.
            sf_col = col + kept[0][0]
            sub = []                          # coalesce into (text, color, hilite)
            for _, ch, cc, hh in kept:
                if sub and sub[-1][1] == cc and sub[-1][2] == hh:
                    sub[-1] = (sub[-1][0] + ch, cc, hh)
                else:
                    sub.append((ch, cc, hh))
            if any(cc is not None or hh is not None for _, cc, hh in sub):
                self.screen.add(Text.rich(row, sf_col, sub,
                                          intensity=intens, role=role))
            else:
                self.screen.add(Text(row, sf_col, "".join(t for t, _, _ in sub),
                                     intens, role=role))

    def _emit_listitem(self, a, content, runs=None):
        """Emit one <li>: a depth-based bullet/number plus the item text, flowed,
        word-wrapped with a hanging indent, one level deeper per nested list. An
        inline <hp> phrase banks its text into ``runs``, leaving ``content`` empty —
        use the runs' concatenation so the item is not dropped."""
        if runs is not None:
            content = "".join(t for t, *_ in runs)
        text = " ".join(content.split())
        if not text:
            return
        ctx = self._areas[-1] if self._areas else None
        row = ctx["row"] if ctx else 0
        # A list nested in an open <nt> hangs under the note body, like a <p> (#219);
        # an enclosing <info indent=n> shifts it right by n columns.
        base = (self._note_hang if self._note_hang is not None
                else (ctx["col"] if ctx else 1)) + self._info_indent
        depth = max(len(self._lists), 1)
        lst = self._lists[-1] if self._lists else None
        # INDENT on the list shifts its items (marker included) right (#123).
        bullet_col = base + (depth - 1) * self._LIST_INDENT \
            + (lst.get("indent", 0) if lst else 0)
        if lst and lst["type"] == "sl":
            marker = None                       # simple list: indented, no bullet
        elif lst and lst["type"] == "ol":
            lst["n"] += 1
            ol_depth = sum(1 for ln in self._lists if ln["type"] == "ol")
            marker = self._ol_marker(lst["n"], ol_depth)
        else:
            marker = self._BULLETS[min(depth - 1, len(self._BULLETS) - 1)]
        # A list item is normal information-region text (CUA green), like a <p>;
        # without a role it would fall back to the base protected-field colour.
        # SPACE on the <li> overrides the list's item-text indentation (else the
        # enclosing list's SPACE, else the default 4 — e.g. a <notel space=yes>).
        indent = (self._space_indent(a) if "space" in a
                  else (lst.get("space", self._LIST_INDENT) if lst
                        else self._LIST_INDENT))
        self._emit_flow_lines(text, row, bullet_col + indent, ctx,
                              marker=marker, marker_col=bullet_col, role="text")

    def _space_indent(self, a):
        """The list item-text indentation from a SPACE attribute: SPACE=YES → 3
        columns, SPACE=NO / absent → the default 4 (per the NOTEL/LI reference)."""
        return 3 if str(a.get("space", "")).strip().lower() == "yes" \
            else self._LIST_INDENT

    def _emit_defitem(self, tag, a, content):
        """Emit one definition-list entry. A term (<dt>/<pt>) sits at the list
        margin; its description (<dd>/<pd>) is laid out in a column ``tsize``
        chars to the right — on the term's own line when ``break`` is
        ``none``/``fit`` and the term fits, otherwise on the following line."""
        text = " ".join(content.split())
        if not text:
            return
        # Find the enclosing definition list (it carries tsizes/break/pending).
        dl = next((ln for ln in reversed(self._lists)
                   if ln["type"] in ("dl", "parml")), None)
        tsizes = dl["tsizes"] if dl else [self._DL_TSIZE]
        brk = dl["break"] if dl else "none"
        depth = max(len(self._lists), 1)
        row, col, ctx = self._resolve_pos(a, tag)  # advances the flow one line
        base = col + (depth - 1) * self._LIST_INDENT + (dl["indent"] if dl else 0)
        # The description column sits past every term column (+1-col gaps between).
        desc_col = base + sum(tsizes) + (len(tsizes) - 1) * self._DL_GAP
        if tag in ("dt", "pt"):
            ci = min(dl["col"] if dl else 0, len(tsizes) - 1)
            col_x = base + sum(tsizes[:ci]) + ci * self._DL_GAP
            width = tsizes[ci]
            if dl is not None and ci == 0:
                dl["entry_row"] = row       # the first line of a new entry
            # FORMAT positions the term within its column (START left default,
            # CENTER, END); a term wider than its column spills (per BREAK).
            fmt = dl["format"] if dl else "start"
            self.screen.add(Text(row, col_x + self._fmt_offset(len(text), width, fmt),
                                 text, _intensity(a)))
            if dl is not None:
                dl["col"] = ci + 1
                dl["base"] = base
                # <dtseg>s for THIS column stack on the lines below the term.
                dl["seg"] = {"row": row + 1, "x": col_x}
                dl["pending"] = {"desc_col": desc_col}
            # A term that fits its column shares its row with the next column /
            # the description; a spilled or break=all term takes its own line.
            if brk != "all" and len(text) < width and ctx is not None:
                ctx["row"] = row
            return
        # Description: flows at the current flow row (which a fitting term rewound
        # to its own line; break=all / a spilled term left on the next line).
        desc_col = dl["pending"]["desc_col"] if dl and dl["pending"] else desc_col
        seg_bottom = dl["seg"]["row"] if dl and dl.get("seg") else None
        if dl is not None:
            dl["pending"] = None
            dl["col"] = 0                   # next <dt> starts a new entry's column 0
            dl["seg"] = None
        self._emit_flow_lines(text, row, desc_col, ctx)
        # Resume flow below the term-segment stack if it runs past the description.
        if ctx is not None and seg_bottom is not None and ctx["row"] < seg_bottom:
            ctx["row"] = seg_bottom

    def _emit_defdiv(self, tag):
        """A vertical `|` divider between definition-term columns: <dtdiv>/<ptdiv>
        between <dt>/<pt> columns, <dthdiv> between <dthd> columns. It sits in the
        one-column gap before the current column (the previous <dt>/<dthd> having
        advanced the column cursor), on that row."""
        dl = next((ln for ln in reversed(self._lists)
                   if ln["type"] in ("dl", "parml")), None)
        if dl is None or "base" not in dl:
            return
        ci = dl["col"]                    # the column the next <dt>/<dthd> will use
        if ci < 1:
            return                        # no preceding column to divide from
        tsizes = dl["tsizes"]
        gap_x = dl["base"] + sum(tsizes[:ci]) + (ci - 1) * self._DL_GAP
        if tag == "dthdiv":
            row = dl["hdr"]["row"] if dl.get("hdr") else None
        else:
            row = dl.get("entry_row")
        if row is not None:
            self.screen.add(Text(row, gap_x, "|", role="rule"))

    def _emit_dtseg(self, tag, content):
        """A <dtseg>/<ptseg> term segment: an additional line of the current
        definition term, stacked directly under the term text in its column."""
        text = " ".join(content.split())
        dl = next((ln for ln in reversed(self._lists)
                   if ln["type"] in ("dl", "parml")), None)
        if dl is None or not dl.get("seg") or not text:
            return
        seg = dl["seg"]
        self.screen.add(Text(seg["row"], seg["x"], text, DisplayIntensity.NORMAL))
        seg["row"] += 1

    def _emit_defhead(self, tag, a, content):
        """Emit a definition-list column heading. ``<dthd>`` is the term-column
        heading (at the list margin); ``<ddhd>`` is the description-column heading
        (``tsize`` chars to the right, on the same row, paired after its <dthd>).
        Per the reference, a blank line separates the heading from the list items
        unless the enclosing <dl> carries COMPACT."""
        text = " ".join(content.split())
        if not text:
            return
        dl = next((ln for ln in reversed(self._lists)
                   if ln["type"] in ("dl", "parml")), None)
        tsizes = dl["tsizes"] if dl else [self._DL_TSIZE]
        depth = max(len(self._lists), 1)
        row, col, ctx = self._resolve_pos(a, tag)   # advances the flow one line
        base = col + (depth - 1) * self._LIST_INDENT + (dl["indent"] if dl else 0)
        desc_col = base + sum(tsizes) + (len(tsizes) - 1) * self._DL_GAP
        if tag == "dthd":
            # A term-column heading — like a <dt>, one per TSIZE column.
            ci = min(dl["col"] if dl else 0, len(tsizes) - 1)
            col_x = base + sum(tsizes[:ci]) + ci * self._DL_GAP
            self.screen.add(Text(row, col_x, text, _intensity(a), role="heading"))
            if ctx is not None:
                ctx["row"] = row                 # paired <ddhd>/next <dthd> share it
            if dl is not None:
                dl["col"] = ci + 1
                dl["base"] = base
                dl["hdr"] = {"row": row}         # header row (for <dthdiv>)
                dl["pending"] = {"desc_col": desc_col}
            return
        # <ddhd>: the description-column heading, then a blank line before the
        # items (COMPACT on the <dl> suppresses that blank).
        desc_col = dl["pending"]["desc_col"] if dl and dl["pending"] else desc_col
        if dl is not None:
            dl["pending"] = None
            dl["col"] = 0                        # next <dt> starts a new entry
        self.screen.add(Text(row, desc_col, text, _intensity(a), role="heading"))
        if ctx is not None and not (dl and dl.get("compact")):
            ctx["row"] = row + 2                     # heading row + one blank line

    def _emit_area_div(self, ctx):
        """Draw an ``<area>``/``<region> DIV=…>`` divider as the box's closing line
        at the current flow row, then advance the box cursor past it. SOLID/DASH →
        a dashed rule spanning the box; BLANK → an empty spacer; TEXT → the divider
        text (FORMAT positions it within the span). #125."""
        div = ctx["div"]
        row, col = ctx["row"], ctx["col"]
        span = ctx.get("width") or max(1, self.screen.width - col - 1)
        if div == "text":
            t = (ctx.get("divtext") or "")[:span]
            fmt = ctx.get("divformat", "start")
            off = (max(0, (span - len(t)) // 2) if fmt == "center"
                   else max(0, span - len(t)) if fmt == "end" else 0)
            if t:
                self.screen.add(Text(row, col + off, t, DisplayIntensity.NORMAL,
                                     role="rule"))
        elif div != "blank":                          # solid / dash → a dashed rule
            self.screen.add(Text(row, col, "-" * span, DisplayIntensity.NORMAL,
                                 role="rule"))
        ctx["row"] = row + 1
        ctx["maxbottom"] = max(ctx.get("maxbottom", row), ctx["row"])

    def _emit_listdiv(self, a, content):
        """Emit a definition/parameter-list divider (<dldiv>/<pldiv>): a horizontal
        rule across the list. TYPE=NONE (default) is a blank spacer row; SOLID/DASH
        draw a dashed rule (a text terminal is NOGRAPHIC, so SOLID falls back to
        dashes); TYPE=TEXT lays the divider-text out, positioned by FORMAT. GAP=YES
        leaves a one-character gap at each end."""
        row, col, ctx = self._resolve_pos(a, "dldiv")   # advances the flow one row
        dl = next((ln for ln in reversed(self._lists)
                   if ln["type"] in ("dl", "parml")), None)
        col += dl["indent"] if dl else 0                 # align with the list <INDENT>
        typ = str(a.get("type", "none")).strip().lower()
        if typ in ("none", "blank"):
            return                                       # a blank spacer, no rule
        span = max(1, self.screen.width - col - 1)
        if ctx is not None and ctx.get("width"):
            span = ctx["width"]
        start = col
        # GUTTER=n insets the rule n characters at each end; GAP=YES is the n=1
        # shorthand. Absent (neither) leaves the rule full-width (unchanged).
        gutter = self._opt_int(a.get("gutter"))
        if gutter is None:
            gutter = 1 if _bool_attr(a, "gap") else 0
        if gutter > 0:
            start, span = col + gutter, max(1, span - 2 * gutter)
        if typ == "text":
            text = " ".join(content.split())[:span]
            fmt = str(a.get("format", "start")).strip().lower()
            if fmt == "end":
                off = span - len(text)
            elif fmt == "center":
                off = (span - len(text)) // 2
            else:
                off = 0
            self.screen.add(Text(row, start + off, text, role="rule"))
        else:                                            # solid / dash → dashed rule
            self.screen.add(Text(row, start, "-" * span, role="rule"))

    @staticmethod
    def _collapse_runs(runs, width):
        """Whitespace-collapse mixed-content ``runs`` (4-tuples from ``<hp>``) into
        ``(text, color, highlight)`` 3-tuples for :meth:`Text.rich`, matching the
        ``" ".join(text.split())`` normalisation the plain path uses, and truncating
        the total to ``width`` characters. Runs of blanks (within or across pieces)
        become a single space; leading/trailing blanks are dropped."""
        out = []
        used = 0
        pending_space = False
        started = False
        for text, color, hilite, _ in runs:
            parts = text.split()
            has_lead = text[:1].isspace()
            has_trail = text[-1:].isspace()
            piece = ""
            if started and (pending_space or has_lead) and parts:
                piece += " "
            piece += " ".join(parts)
            pending_space = has_trail if parts else (pending_space or has_lead)
            if parts:
                started = True
            if not piece:
                continue
            piece = piece[: max(0, width - used)]
            if not piece:
                break
            used += len(piece)
            out.append((piece, color, hilite))
        return out or [("", None, None)]

    def _emit_divider(self, a, content, runs=None):
        """Emit an <area>/<region> <divider>, honouring TYPE and its divider-text.

        The position was fixed when the tag opened (``a["_row"]/_col/_width``); the
        row was already consumed. TYPE selects the look (#125):

        * DASH — a hyphen rule. This is also the no-TYPE default, so a plain
          ``<divider>`` (every bundled panel) renders byte-for-byte as before.
        * SOLID — a solid GE line (── like the group-box / other borders); the
          terminal draws it from the graphic set, so it reads as an unbroken rule.
        * TEXT — the divider's own text, positioned within the span by FORMAT
          (START/CENTER/END). Empty text falls back to nothing (just the spacer row).
          Inline ``<hp>`` in the text is kept as a coloured/emphasised run (mono
          renders identically to the plain text).

        GAP=YES leaves a one-character gap at each end of the rule/text. NOENDATTR
        suppresses the trailing field-attribute byte a *field* would carry; a
        divider is protected display text (no such attribute), so it has no effect
        here and is accepted as a no-op.
        """
        row, col, span = a["_row"], a["_col"], a["_width"]
        typ = str(a.get("type", "dash")).strip().lower()
        start = col
        if _bool_attr(a, "gap"):                         # 1-char gap at each end
            start, span = col + 1, max(1, span - 2)
        if typ == "text":
            # Inline <hp> in the divider text builds runs; the plain concatenation
            # is what positions the text (FORMAT) and what a mono terminal shows.
            text = (" ".join("".join(r[0] for r in runs).split()) if runs
                    else " ".join(content.split()))[:span]
            if not text:
                return
            fmt = str(a.get("format", "start")).strip().lower()
            if fmt == "end":
                off = span - len(text)
            elif fmt == "center":
                off = (span - len(text)) // 2
            else:
                off = 0
            at = start + max(0, off)
            if runs and any(r[1] or r[2] for r in runs):
                # Keep the <hp> colour/highlight runs across the (whitespace-
                # collapsed) text; rebuild the runs against the collapsed string.
                self.screen.add(Text.rich(row, at, self._collapse_runs(runs, span),
                                          role="rule"))
            else:
                self.screen.add(Text(row, at, text, role="rule"))
        elif typ == "solid":
            # A GE solid line — an unbroken rule, distinct from DASH's hyphens.
            self.screen.add(GraphicText.rule(row, start, span, role="rule"))
        else:                                            # dash (and the default)
            self.screen.add(Text(row, start, "-" * span, role="rule"))

    def _finalize_grpbox_title(self):
        """Bank the group-box title captured since <region GRPBOX> and stop
        capturing (called at the first child tag, or at the region's close)."""
        box = self._grpbox_pending
        self._grpbox_pending = None
        if box is not None:
            box["gb_title"] = " ".join("".join(box["gb_title_chars"]).split())

    def _draw_grpbox(self, ctx):
        """Frame a closing <region GRPBOX>'s content in a GE box border (#125).

        The content was flowed inset one column past the (reserved) left border and
        one row below the (reserved) top border; now that its extent is known we draw
        the four edges with :class:`GraphicText` (the same graphic set the pull-down
        and other borders use). GRPWIDTH fixes the width; otherwise the box is sized
        to the content. The title, if any, sits on the top edge (``┌── title ──┐``).
        ``ctx`` is advanced to the row just below the bottom edge so the parent flow
        resumes there.
        """
        gb_col = ctx["gb_col"]
        top = ctx["gb_row0"]
        title = " ".join((ctx.get("gb_title") or "").split())
        # GRPBXVAR/GRPBXMAT: when the controlling variable's value is known and does
        # not match GRPBXMAT, don't frame the box — leave the content as a plain
        # region (its rows already flowed in place).
        var = ctx.get("gb_var")
        if var:
            val = self._subs.get(str(var).upper())
            if val is not None and str(val) != ctx.get("gb_match", "1"):
                ctx["row"] = ctx["maxbottom"] = self._box_extent(ctx["start_idx"])[0] \
                    or ctx["row"]
                return
        # LOCATION=TITLE: show the group heading as the panel title, not on the edge.
        if title and ctx.get("gb_location") == "title":
            if self.screen.title is None:
                self.screen.title = title
            title = ""
        # Content extent: rows gb_row0+1 .. bottom-1, last text column = right.
        bottom, right = self._box_extent(ctx["start_idx"])
        if bottom is None:                     # empty group box — a 1-row-tall frame
            bottom = top + 2
            right = ctx["col"]
        # Width: GRPWIDTH if given, else sized to the content (+ a right pad column;
        # the box spans visual columns gb_col+1 .. gb_col+width and content data ends
        # at `right`, so right - gb_col + 2 leaves one pad column before ┐). A title
        # needs len+7 columns (┌─ + a space + title + a space + ─┐ and the two
        # field-attribute gaps); without GRPWIDTH the box grows to fit it, else the
        # title is truncated to what GRPWIDTH allows.
        if ctx.get("gb_width"):
            width = ctx["gb_width"]
            title = title[:max(0, width - 7)]
        else:
            width = max(right - gb_col + 2, (len(title) + 7) if title else 0, 4)
        width = max(width, 4)                  # room for ┌┐ + a cell
        for r in range(top + 1, bottom):       # left / right vertical edges
            self.screen.add(GraphicText(r, gb_col, bytes([Line.VERTICAL.value]),
                                        role="rule"))
            self.screen.add(GraphicText(r, gb_col + width - 1,
                                        bytes([Line.VERTICAL.value]), role="rule"))
        for it in self._grpbox_top(top, gb_col, width, title):
            self.screen.add(it)                # top edge (with the title)
        self.screen.add(GraphicText.box_bottom(bottom, gb_col, width, role="rule"))
        ctx["row"] = ctx["maxbottom"] = bottom + 1   # parent resumes below the box

    def _grpbox_top(self, row, col, width, title):
        """The top edge of a group box ``width`` cells wide (visual columns
        ``col+1 .. col+width``). Without a title it is a plain ``┌────┐``. With one
        the border is split into three adjacent fields — ``┌─`` + the title + ``──┐``
        — so the heading sits in a clearing on the top edge (the field-attribute byte
        between the fields reads as a blank, blending with the title's padding). The
        caller guarantees ``title`` fits (``len ≤ width - 7``)."""
        if not title or width < 8:             # no room to inset a title → plain edge
            return [GraphicText.box_top(row, col, width, role="rule")]
        label = " " + title + " "
        lead = 2                               # ┌─ before the title
        items = [GraphicText(row, col,
                             bytes([Line.TOP_LEFT.value])
                             + bytes([Line.HORIZONTAL.value]) * (lead - 1),
                             role="rule")]
        title_col = col + lead + 1             # attr byte follows ┌─'s last glyph
        items.append(Text(row, title_col, label, role="rule"))
        seg_c = title_col + 1 + len(label)     # attr byte follows the title's last char
        fill = (col + width) - (seg_c + 1)     # horizontals before the ┐ at col+width
        items.append(GraphicText(row, seg_c,
                                 bytes([Line.HORIZONTAL.value]) * max(0, fill)
                                 + bytes([Line.TOP_RIGHT.value]),
                                 role="rule"))
        return items

    def _emit_lines(self, a, content):
        """Emit a <lines> block: preformatted text whose authored line breaks
        are preserved (unlike <p>, which collapses whitespace and word-wraps).
        Blank framing lines are dropped, the common source indentation removed,
        and each line truncated to the panel width."""
        raw = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        while raw and not raw[0].strip():
            raw.pop(0)
        while raw and not raw[-1].strip():
            raw.pop()
        if not raw:
            return
        indents = [len(ln) - len(ln.lstrip()) for ln in raw if ln.strip()]
        cut = min(indents) if indents else 0
        lines = [ln[cut:].rstrip() for ln in raw]
        # ISPDTLC block spacing: a preformatted <lines>/<xmp> block is preceded by
        # a leading blank line (COMPACT/NOSKIP suppress it — #210).
        self._skip_blank_before(a)
        row, col, ctx = self._resolve_pos(a, "lines")
        width = max(1, self.screen.width - (col + 1))
        for i, ln in enumerate(lines):
            # Preformatted lines are normal information-region text (CUA green);
            # without a role they fall back to the base protected-field colour.
            self.screen.add(Text(row + i, col, ln[:width], DisplayIntensity.NORMAL,
                                 role="text"))
        if ctx is not None:
            ctx["row"] = row + len(lines)

    def _add_lstcol(self, a, content):
        """Record one <lstcol> in the active table: its heading text, width
        (``colwidth``, else as wide as the heading), the bound ``datavar``, its
        ``usage`` (in/out), the model ``line`` it sits on, and its group."""
        if self._lstfld is None:
            return
        heading = " ".join(content.split())
        width = self._opt_int(a.get("colwidth"))
        if width is None:                      # absent / '*' → as wide as the heading
            width = max(len(heading), 1)
        self._lstfld["cols"].append({
            "heading": heading,
            "width": width,                    # data width (COLWIDTH)
            # Column formatting width: the greater of COLWIDTH and the heading, so
            # a heading wider than the data is not truncated (e.g. "MI" over a
            # 1-wide column, "(Y or N)" over a 1-wide input).
            "fmt": max(width, len(heading), 1),
            "datavar": a.get("datavar", ""),
            # A column is an input field unless it is explicitly display-only.
            "usage": "out" if a.get("usage", "").lower() == "out" else "in",
            # CAPS=OFF|ON (default OFF): an ON input column is uppercase-input —
            # ISPF folds it to uppercase; we fold the typed value on read-back.
            "caps": str(a.get("caps", "")).strip().lower() == "on",
            # REQUIRED=YES + MSG=id: the cell must be non-blank on a modified row
            # (ISPF's VER(var, NONBLANK, MSG=id)); validated on read-back.
            "required": str(a.get("required", "")).strip().lower() == "yes",
            "msg": (a.get("msg") or "").strip() or None,
            "autotab": a.get("autotab", "").lower() == "yes",
            "line": int(a.get("line", 1)),
            "align": a.get("align", "start").lower(),
            # FORMAT positions the shorter of (heading, data) within the column
            # formatting width: START (default) left, CENTER centred, END right.
            "format": str(a.get("format", "start")).strip().lower(),
            # DTL COLOR / INTENS / HILITE on a <lstcol> style its cells, exactly as
            # on a <dtafld>: colour, intensity (HIGH / LOW→normal / NON→non-display)
            # and highlight (underscore / blink / reverse).
            "color": self._color(a),
            "intensity": self._cell_intensity(a.get("intens")),
            "highlight": self._hilite(a),
            "outline": self._outline(a),   # OUTLINE box lines on the cells
            # DISPLAY=NO is a non-display column (a password-style hidden cell); the
            # heading still shows. YES is the default.
            "display_no": str(a.get("display", "yes")).strip().lower() == "no",
            # NOENDATTR drops this column's trailing attribute byte (tightening the
            # gutter); ignored for the last column on a model line (see _emit_lstfld).
            "noendattr": _bool_attr(a, "noendattr"),
            # POSITION pins the column: it is the location of the attribute byte
            # preceding the data, so the data starts at POSITION+1. Absent/invalid →
            # normal left-to-right flow.
            "position": self._opt_int(a.get("position")),
            # HELP=panel-name attaches field-level (cursor-sensitive) help to the
            # column's cells, like <dtafld help=>.
            "help": self._field_help(a),
            # PAD/PADC: the fill character for an empty input cell (None → the
            # conventional space fill, keeping padless columns byte-identical).
            "pad": self._pad_char(a),
            # COLSPACE adds extra blank columns after this column (widening the
            # gutter before the next column); 0 (default) keeps the CUA gutter.
            "colspace": self._opt_int(a.get("colspace"), 0) or 0,
            "group": self._lstgrp,
        })
        # TEXT: a short description rendered beside each data cell. TEXTLOC picks
        # the side (default AFTER), TEXTLEN reserves a formatting area (default: the
        # text length), TEXTFMT justifies within it (ignored if the text overflows
        # the area, per the reference). TEXTSKIP is cursor-skip behaviour only.
        text = (a.get("text") or "").strip()
        col = self._lstfld["cols"][-1]
        tl = str(a.get("textlen", "")).strip()
        textlen = int(tl) if tl.isdigit() else 0
        col["text"] = text
        col["textloc"] = "before" if str(a.get("textloc", "")).strip().lower() \
            == "before" else "after"
        col["textfmt"] = str(a.get("textfmt", "start")).strip().lower()
        col["text_area"] = max(textlen, len(text)) if text else 0
        # A <scrfld> nested in this column makes it horizontally scrollable: its
        # data (DISPLEN) is wider than the on-screen COLWIDTH window, and ISPF
        # generates a scale/indicator line under the column heading (see Figure 42
        # in the DTL guide, rendered in _emit_lstfld).
        scr = self._pending_scrfld
        self._pending_scrfld = None
        if scr is not None:
            col["scrfld"] = scr
            self.screen.scroll_fields.append({
                "name": col["datavar"],
                "displen": self._opt_int(scr.get("displen")),
                "scroll": str(scr.get("scroll", "on")).strip().lower(),
                **{k: scr[k] for k in self._SCRFLD_INDICATORS if k in scr},
            })

    @staticmethod
    def _fmt_offset(inner, fmt, mode):
        """FORMAT offset of an ``inner``-wide item (heading or data cell) within
        the ``fmt``-wide column: START 0, CENTER floor(slack/2), END slack."""
        slack = max(0, fmt - inner)
        if mode == "center":
            return slack // 2
        if mode == "end":
            return slack
        return 0

    def _pad_char(self, a):
        """Resolve PAD/PADC (on a <lstcol>, <dtafld>, or <dtacol>) to the fill
        character for an empty input field, or None to keep the default (space)
        fill. Per the reference, when both are given PADC wins. NULLS → a null
        fill; USER (the ISPF profile pad character, which this display server does
        not carry) → the default; %varname is resolved against the dialog
        variables; any other value's first character is the literal pad."""
        raw = a.get("padc") if a.get("padc") is not None else a.get("pad")
        if raw is None:
            return None
        val = str(raw).strip()
        if val.startswith("%"):                      # %varname → its value
            val = str(self._subs.get(val[1:].upper(), "")).strip()
        kw = val.lower()
        if kw in ("", "user"):                        # profile pad unavailable
            return None
        if kw == "nulls":
            return "\x00"
        return val[0]

    @staticmethod
    def _cell_intensity(value):
        """A <lstcol>/<dtafld> INTENS value → the screen intensity. 3270 has no
        sub-normal level, so LOW maps to NORMAL; NON is non-display."""
        return {
            "high": DisplayIntensity.HIGH,
            "low": DisplayIntensity.NORMAL,
            "non": DisplayIntensity.NON_DISPLAY,
        }.get(str(value or "").strip().lower(), DisplayIntensity.NORMAL)

    def _add_group_heading(self, g, start, span, row, ncols):
        """Draw a <lstgrp> heading over its column span at ``(row, start)``.
        ALIGN: START left, END right, CENTER (default) centres over multiple
        columns but left-justifies over a single column. HEADLINE draws a dashed
        rule around (padding out) the heading text."""
        H = DisplayIntensity.HIGH
        align = g["align"]
        if align == "start" or (align != "end" and ncols == 1):
            just = "start"
        elif align == "end":
            just = "end"
        else:
            just = "center"
        if g["headline"]:                  # dashed rule around the heading
            inner = f" {g['heading']} " if g["heading"] else "-"
            pad = max(0, span - len(inner))
            if just == "start":
                text = (inner + "-" * pad)[:span]
            elif just == "end":
                text = ("-" * pad + inner)[:span]
            else:
                text = ("-" * (pad // 2) + inner + "-" * (pad - pad // 2))[:span]
            self.screen.add(Text(row, start, text, H, role="heading"))
        else:
            text = g["heading"][:span]
            if just == "start":
                off = 0
            elif just == "end":
                off = max(0, span - len(text))
            else:
                off = max(0, (span - len(text)) // 2)
            self.screen.add(Text(row, start + off, text, H, role="heading"))

    def _emit_lstfld(self):
        """Lay out the table's column header: each <lstcol> heading at its computed
        column (left to right, ``colwidth`` + the CUA attribute-byte gutter), with
        <lstgrp> group headings stacked by nesting depth above, and column headings
        stacked by LINE. Advances the enclosing flow past the header."""
        fld = self._lstfld
        cols = fld["cols"]
        if not cols:
            return
        # NOENDATTR drops a column's trailing attribute byte, but is ignored for the
        # last column on each model line (which needs it to bound the field).
        last_on_line = {}
        for i, c in enumerate(cols):
            last_on_line[c["line"]] = i
        x = fld["col"]
        for i, c in enumerate(cols):
            # Each column reserves its formatting width plus the CUA attribute-byte
            # space: OUT (or IN/BOTH with AUTOTAB) → +2 (lead+trail attr); a plain
            # input column (AUTOTAB=NO) → +3 (lead+trail attr + a trailing blank).
            gutter = 2 if c["usage"] == "out" or c["autotab"] else 3
            if c.get("noendattr") and last_on_line[c["line"]] != i:
                gutter -= 1                        # trailing attribute byte suppressed
            gutter += c.get("colspace", 0)         # COLSPACE widens the gutter
            # POSITION pins the column start (attribute byte at POSITION → data at
            # POSITION+1); otherwise it flows after the previous column.
            base = c["position"] + 1 if c.get("position") is not None else x
            tw = c.get("text_area", 0)
            if c.get("text") and c["textloc"] == "before":
                c["text_x"] = base                 # description, then the data cell
                c["x"] = base + tw + 1
                x = c["x"] + c["fmt"] + gutter
            elif c.get("text"):                    # data cell, then the description
                c["x"] = base
                c["text_x"] = base + c["fmt"] + 1
                x = c["text_x"] + tw + gutter
            else:
                c["x"] = base
                c["text_x"] = None
                x = base + c["fmt"] + gutter
        row = fld["row"]
        H = DisplayIntensity.HIGH
        # Group headings stack by nesting depth: a depth-1 group heads the top row,
        # each nested <lstgrp> level the row below it, spanning the leaf columns
        # beneath it (a parent group covers all columns under its child groups).
        def _under(col, g):
            grp = col["group"]
            while grp is not None:
                if grp is g:
                    return True
                grp = grp["parent"]
            return False
        max_depth = max((g["depth"] for g in fld["groups"]), default=0)
        for g in fld["groups"]:
            if not (g["heading"] or g["headline"]):
                continue
            gcols = [c for c in cols if _under(c, g)]
            if not gcols:
                continue
            start = min(c["x"] for c in gcols)
            span = max(1, max(c["x"] + c["fmt"] for c in gcols) - start)
            self._add_group_heading(g, start, span, row + g["depth"] - 1, len(gcols))
        row += max_depth
        # Column headings, stacked by LINE: a column on model line n heads on the
        # nth row of the heading block, matching its data-row placement below.
        head_lines = max((c["line"] for c in cols if c["heading"]), default=0)
        for c in cols:
            if c["heading"]:
                hoff = self._fmt_offset(len(c["heading"]), c["fmt"], c["format"])
                self.screen.add(Text(row + c["line"] - 1, c["x"] + hoff,
                                     c["heading"][:c["fmt"]], H, role="heading"))
        row += head_lines
        # A scrollable column (a nested <scrfld>) shows a scale/separator line
        # between its heading and the data — the on-screen window is COLWIDTH but the
        # data scrolls wider (DISPLEN). See Figure 42 in the DTL guide.
        if any(c.get("scrfld") for c in cols):
            for c in cols:
                scr = c.get("scrfld")
                if scr is None:
                    continue
                text = (self._scale_ruler(c["fmt"]) if "scale" in scr
                        else ("-" * (c["fmt"] - 1) + ">") if c["fmt"] >= 1 else "")
                if text:
                    self.screen.add(Text(row, c["x"], text,
                                         DisplayIntensity.NORMAL, role="prompt"))
            row += 1
        row = self._emit_lstfld_rows(cols, row)
        # ISPF puts a "ROW x TO y OF z" scroll status on the title line's right —
        # but only if that region is free (a bundled panel's full-width title rule
        # occupies it and carries its own scroll footer).
        if self._rows:
            offset = self._row_offset
            total = self._row_total if self._row_total is not None else len(self._rows)
            shown = fld.get("shown", 0)
            status = f"ROW {offset + 1} TO {offset + shown} OF {total}"
            sx = max(0, self.screen.width - len(status) - 1)
            busy = any(getattr(it, "row", None) == 0 and hasattr(it, "text")
                       and it.col < sx + len(status) and it.col + len(it.text) > sx
                       for it in self.screen.items)
            if not busy:
                self.screen.add(Text(0, sx, status, DisplayIntensity.HIGH,
                                     role="status"))
        if fld["ctx"] is not None:
            fld["ctx"]["row"] = row

    def _emit_lstfld_rows(self, cols, row):
        """Lay out the table's model rows below the header. Each data row (or a
        single empty template when none is supplied) renders every column on its
        ``line``: a display column (usage=out) as protected text, an input
        column as an unprotected field, pre-filled from the row's ``datavar``.
        Rows are capped to the panel depth. Returns the next free row."""
        entry_height = max((c["line"] for c in cols), default=1)
        # The rightmost input column on each model line needs an explicit
        # terminator; interior fields are bounded by the next column's start.
        last_in = {}
        for c in cols:
            if c["usage"] == "in":
                last_in[c["line"]] = c
        last_in_ids = {id(c) for c in last_in.values()}
        # DIV draws a divider as the last line of each model set. Under NOGRAPHIC
        # (a text terminal) SOLID and DASH both fall back to a rule of dashes;
        # BLANK is a spacer line; any other value is that literal character (or
        # string) replicated to the available width.
        div = self._lstfld.get("div", "none")
        divkey = div.lower()
        if divkey in ("none", ""):
            div_fill = None       # no divider
        elif divkey == "blank":
            div_fill = ""         # a blank spacer row
        elif divkey in ("solid", "dash"):
            div_fill = "-"
        else:
            div_fill = div        # literal char/string, case preserved
        div_rows = 0 if div_fill is None else 1
        data = self._rows if self._rows else [None]
        clipped = False
        shown = 0
        for row_index, entry in enumerate(data):
            if row + entry_height + div_rows > self.screen.depth - 1:
                clipped = True
                break  # leave room; don't overrun the panel
            if entry is not None:
                shown += 1
            for c in cols:
                cy = row + (c["line"] - 1)
                raw = "" if entry is None else str(entry.get(c["datavar"], ""))
                value = self._align(raw, c["width"], c["align"])
                intensity = c.get("intensity", DisplayIntensity.NORMAL)
                # INTENS=NON or DISPLAY=NO make the data cell non-display.
                hidden = (intensity is DisplayIntensity.NON_DISPLAY
                          or c.get("display_no"))
                # FORMAT shifts the data cell within the column width (the cell's
                # own contents are still justified by ALIGN, per the reference).
                cx = c["x"] + self._fmt_offset(c["width"], c["fmt"],
                                               c.get("format", "start"))
                if c["usage"] == "out":
                    self.screen.add(Text(cy, cx, value,
                                         DisplayIntensity.NON_DISPLAY if hidden
                                         else intensity,
                                         color=c.get("color"),
                                         highlight=c.get("highlight"), role="cell",
                                         help=c.get("help"), outline=c.get("outline")))
                else:
                    self.screen.add(Field(
                        row=cy, col=cx, length=c["width"],
                        name=c["datavar"] or None, default=value,
                        terminator=id(c) in last_in_ids,
                        # INTENS=NON → a non-display input cell (Field.hidden);
                        # otherwise carry HIGH/normal intensity onto the field.
                        intensity=DisplayIntensity.NORMAL if hidden else intensity,
                        hidden=hidden,
                        color=c.get("color"), highlight=c.get("highlight"),
                        role="cell", help=c.get("help"), pad=c.get("pad"),
                        outline=c.get("outline"),
                        # Record which model row this input cell is on, so the
                        # server can read the table back per row despite every row's
                        # cell in this column sharing the DATAVAR (Screen.read_table_rows).
                        row_index=row_index,
                        # CAPS=ON folds the typed value to uppercase on read-back.
                        caps=c.get("caps", False),
                        # REQUIRED=YES/MSG: non-blank validation on a modified row.
                        required=c.get("required", False), msg=c.get("msg"),
                    ))
                # TEXT description beside the cell, justified within its area
                # (TEXTFMT); unformatted when the text overflows the reserved area.
                if c.get("text") and c.get("text_x") is not None:
                    t, area = c["text"], c["text_area"]
                    if area <= len(t) or c["textfmt"] == "start":
                        off = 0
                    elif c["textfmt"] == "end":
                        off = area - len(t)
                    else:
                        off = (area - len(t)) // 2
                    self.screen.add(Text(cy, c["text_x"] + off, t,
                                         DisplayIntensity.NORMAL, role="text"))
            if div_fill:   # None (no divider) or "" (blank spacer) draw nothing
                col0 = cols[0]["x"]
                span = max(1, self.screen.width - col0 - 1)
                line = (div_fill * (span // len(div_fill) + 1))[:span]
                self.screen.add(Text(row + entry_height, col0, line,
                                     DisplayIntensity.NORMAL, role="rule"))
            row += entry_height + div_rows
        # When the end of the *full* table is on screen (rows supplied, not clipped
        # by the panel depth, and the last row of the full set is in this window),
        # ISPF draws a "BOTTOM OF DATA" line spanning the table. A middle page of a
        # paged table does not reach the bottom, so it gets no marker (#281).
        total = self._row_total if self._row_total is not None else len(self._rows or [])
        at_bottom = self._row_offset + shown >= total
        if self._rows is not None and not clipped and at_bottom and cols \
                and row < self.screen.depth - 1:
            col0 = cols[0]["x"]
            footer = " BOTTOM OF DATA "
            w = max(len(footer), self.screen.width - col0 - 1)
            pad = w - len(footer)
            line = ("*" * (pad // 2) + footer + "*" * (pad - pad // 2))[:w]
            self.screen.add(Text(row, col0, line, DisplayIntensity.HIGH,
                                 role="heading"))
            row += 1
        self._lstfld["shown"] = shown
        return row

    @staticmethod
    def _align(text, width, align):
        text = text[:width]
        if align == "end":
            return text.rjust(width)
        if align in ("center", "centre"):
            return text.center(width)
        return text  # start/left: no padding (an input field fills its own width)

    def _row_occupied(self, row):
        """Whether any screen item sits on ``row`` — used to decide if a block's
        leading blank line is needed (skip it when the row above is already blank)."""
        return any(getattr(it, "row", None) == row for it in self.screen.items)

    def _skip_blank_before(self, a, space_suppresses=False):
        """ISPDTLC block spacing: insert a leading blank line before a flowed block
        element (paragraph, panel instruction, command area, selection field,
        definition list). Added only when the box already holds content (so the
        first block gets none) and the row above is not already blank (so an
        existing gap isn't doubled). An explicit ``row``, COMPACT, or NOSKIP
        suppresses it. Advances the flow row cursor.

        ``space_suppresses`` maps a ``<p SPACE=NO>`` to the same suppression: on a
        paragraph SPACE=NO|YES governs the preceding blank (YES, the default, keeps
        it). Not passed for list tags, where SPACE instead sets the item indent."""
        space_no = space_suppresses and \
            str(a.get("space", "")).strip().lower() == "no"
        ctx = self._areas[-1] if self._areas else None
        if (ctx is not None and "row" not in a and not _bool_attr(a, "compact")
                and not space_no
                and not _bool_attr(a, "noskip")
                and ctx.get("had_content") and ctx["row"] >= 1
                and ctx["row"] + 1 < self.screen.depth   # don't push off the panel
                and self._row_occupied(ctx["row"] - 1)):
            ctx["row"] += 1

    def _emit_heading(self, tag, a, content, runs=None):
        """Render a DTL help-panel heading (``<h1>``–``<h4>``) as a high-intensity
        heading line in the text flow. A leading blank line precedes it (COMPACT
        suppresses it, its only attribute); ``<h2>``/``<h3>``/``<h4>`` indent by
        level so the heading hierarchy reads on a text terminal. Nested ``<hp>``
        runs collapse to plain text (the whole line is already emphasised). #52."""
        if runs is not None and not content:
            content = "".join(t for t, *_ in runs)
        text = " ".join(content.split())
        if not text:
            return
        if not _bool_attr(a, "compact"):
            self._skip_blank_before(a)
        row, col, ctx = self._resolve_pos(a, "info")
        if self._lists:
            col += len(self._lists) * self._LIST_INDENT
        col += (int(tag[1]) - 1) * 2      # h1→0, h2→2, h3→4, h4→6 columns of indent
        self._emit_flow_lines(text, row, col, ctx, role="title",
                              intensity=DisplayIntensity.HIGH)

    def _emit_grphdr(self, a, content, runs=None):
        """Render a ``<grphdr>`` (group header): a high-intensity heading line above
        a group of data fields in an area/region. FORMAT justifies the heading
        within WIDTH (START left, CENTER centred, END right, NONE = unformatted
        left); HEADLINE=YES wraps it in a dashed rule; DIV (SOLID/DASH a rule,
        BLANK a spacer) draws a divider BEFORE/AFTER/BOTH per DIVLOC; INDENT shifts
        it right; COMPACT suppresses the leading blank line. #53."""
        if runs is not None and not content:
            content = "".join(t for t, *_ in runs)
        text = " ".join(content.split())
        headline = _bool_attr(a, "headline")
        if not text and not headline:
            return
        if not _bool_attr(a, "compact"):
            self._skip_blank_before(a)
        row, col, ctx = self._resolve_pos(a, "grphdr")
        col += self._opt_int(a.get("indent"), 0)
        width = self._opt_int(a.get("width")) or max(1, self.screen.width - col - 1)
        # FMTWIDTH is the field the heading text is FORMAT-justified within (defaults
        # to WIDTH). STRIP trims the heading's surrounding blanks — already done by
        # the whitespace-normalising split() above, so it is inherently honoured.
        fmtwidth = self._opt_int(a.get("fmtwidth")) or width
        fmt = str(a.get("format", "start")).strip().lower()
        div = str(a.get("div", "none")).strip().lower()
        divloc = str(a.get("divloc", "after")).strip().lower()
        H, N = DisplayIntensity.HIGH, DisplayIntensity.NORMAL

        def _divider(r):
            if div in ("none", ""):
                return 0
            if div != "blank":                       # solid / dash → a dashed rule
                self.screen.add(Text(r, col, ("-" * width)[:width], N, role="rule"))
            return 1                                  # blank → an empty spacer row

        r = row
        if divloc in ("before", "both"):
            r += _divider(r)
        if headline:                                 # dashed rule around the heading
            inner = f" {text} " if text else "-"
            pad = max(0, width - len(inner))
            if fmt == "center":
                line = ("-" * (pad // 2) + inner + "-" * (pad - pad // 2))[:width]
            elif fmt == "end":
                line = ("-" * pad + inner)[:width]
            else:
                line = (inner + "-" * pad)[:width]
            self.screen.add(Text(r, col, line, H, role="heading"))
        else:
            t = text[:fmtwidth]
            off = (max(0, (fmtwidth - len(t)) // 2) if fmt == "center"
                   else max(0, fmtwidth - len(t)) if fmt == "end" else 0)
            self.screen.add(Text(r, col + off, t, H, role="heading"))
        r += 1
        if divloc in ("after", "both"):
            r += _divider(r)
        if ctx is not None:
            ctx["row"] = r

    def _emit_info(self, a, content, tag="info", runs=None):
        # ``runs`` (from inline <hp>) is a list of (text, color, highlight); the
        # concatenation is the field's plain text, so mono renders identically.
        if runs is not None and not content:
            content = "".join(t for t, *_ in runs)
        # An admonition (<note>/<warning>/…) flows as a labelled callout. CAUTION
        # is special: heading on its own line + emphasised body (handled below).
        label = _ADMONITIONS.get(tag)
        caution = tag == "caution"
        if label and content.strip() and not caution:
            content = label + " " + content.strip()
        # CUA role → default colour: a top/panel instruction is an instruction;
        # a high-intensity heading is the title; everything else is normal text
        # (labels/values). A horizontal rule is a <divider>, not an <info>.
        if tag in _INSTRUCTION_TAGS:
            role = "inst"
        elif _intensity(a) is DisplayIntensity.HIGH:
            role = "title"
        else:
            role = "text"
        # A whole line that is a single <hp> (its entire content emphasised, e.g.
        # <info><hp>&SELMSG</hp></info>) is a high-intensity white line — CUA
        # emphasis on both mono (intensified field) and colour (white). A <hp>
        # *phrase within* a line keeps its SA colour/highlight run (mixed → >1 run).
        whole_hp = runs is not None and len(runs) == 1
        if whole_hp:
            role = "emphasis"
            # A whole-line <hp> renders as one plain emphasised Text; honour an
            # explicit INTENS (e.g. NON → non-display) but default to CUA HIGH.
            emph = runs[0][3] or DisplayIntensity.HIGH
            runs = None
        else:
            emph = _intensity(a)
        # Flowed text: normalize whitespace and word-wrap to the panel width.
        text = " ".join(content.split())
        if not text:
            return
        # ISPDTLC block spacing: a blank line precedes a flowed paragraph or panel
        # instruction (see _skip_blank_before for the exact rule; COMPACT
        # suppresses it).
        if tag in _BLANK_BEFORE_TAGS:
            # On a <p>, SPACE=NO suppresses the preceding blank (like COMPACT);
            # SPACE=YES (default) keeps it. Only <p> carries SPACE here.
            self._skip_blank_before(a, space_suppresses=(tag == "p"))
        row, col, ctx = self._resolve_pos(a, "info")
        if self._lists:
            # A paragraph inside a list aligns with the list's item text.
            col += len(self._lists) * self._LIST_INDENT
        p_offset = 0
        if tag == "p":
            # A <p> may carry INDENT=n to shift the whole paragraph right (#123).
            col += self._opt_int(a.get("indent"), 0)
            # OFFSET=n is a hanging indent: the 2nd+ lines shift n columns right.
            p_offset = self._opt_int(a.get("offset"), 0) or 0
            # INTENSE=varname conditionally intensifies the paragraph: HIGH when the
            # named dialog variable resolves to a non-blank value, else normal.
            iv = a.get("intense")
            if iv and str(self._subs.get(str(iv).strip().upper(), "")).strip():
                emph = DisplayIntensity.HIGH
        if tag == "botinst" and ctx is not None:
            # A bottom instruction anchors near the foot of the panel (leaving the
            # last row free, as ISPF keeps for the key area), dropping below the
            # body if the flow already reaches that far.
            lines = self._wrap(text, max(1, self.screen.width - col - 1))
            row = max(row, self.screen.depth - 1 - len(lines))
            for i, ln in enumerate(lines):
                self.screen.add(Text(row + i, col, ln, DisplayIntensity.NORMAL,
                                     role=role))
            ctx["row"] = row + len(lines)
            return
        if caution:
            # CAUTION: uppercase heading on its own line, then the emphasised
            # (high-intensity) body beneath it (per the CAUTION reference).
            self.screen.add(Text(row, col, label, DisplayIntensity.HIGH, role=role))
            if ctx is not None:
                ctx["row"] = row + 1
            self._emit_flow_lines(text, row + 1, col, ctx, role=role,
                                  intensity=DisplayIntensity.HIGH)
            return
        if runs is not None:
            # A phrase carrying non-normal INTENS (HIGH/NON) can't ride an SA run,
            # so the line is split into separate fields (one SF per intensity run);
            # otherwise keep the single-field colour/highlight SA path (byte-for-byte
            # unchanged — the common <hp> case). See _hp_intensity / #212.
            if any(r[3] in (DisplayIntensity.HIGH, DisplayIntensity.NON_DISPLAY)
                   for r in runs):
                self._emit_flow_runs_intens(runs, row, col, ctx, role)
            else:
                # Flowed text with inline <hp>: keep each phrase's colour/highlight
                # across the word-wrap (SA runs per line), dropping the unused 4th
                # (intensity) field so the existing 3-tuple path is untouched.
                self._emit_flow_runs([(t, c, h) for t, c, h, _ in runs],
                                     row, col, ctx, role)
            return
        self._emit_flow_lines(text, row, col, ctx, role=role, intensity=emph,
                              offset=p_offset)
        # A TOPINST is followed by a blank line (COMPACT suppresses it).
        if tag == "topinst" and ctx is not None and not _bool_attr(a, "compact"):
            ctx["row"] += 1

    def _emit_note(self, tag, a, content):
        """Render a <note>/<nt>. The heading (``TEXT=`` override, else ``Note:``)
        begins the note; ``INDENT`` shifts the block; ``INTENS``/``COLOR``/
        ``HILITE`` style the heading. ``<note>`` is a single paragraph wrapped to
        the left margin; ``<nt>`` hangs its body indented under the text (aligned
        past the heading), and may carry nested paragraphs."""
        text = " ".join(content.split())
        # ISPDTLC block spacing: an admonition (<note>/<nt>) is preceded by a
        # leading blank line (COMPACT/NOSKIP suppress it — #210).
        self._skip_blank_before(a)
        row, col, ctx = self._resolve_pos(a, tag)
        if self._lists:
            col += len(self._lists) * self._LIST_INDENT
        col += self._opt_int(a.get("indent"), 0)
        heading = (a.get("text") or "Note:").strip()
        h_int = _intensity(a, "intens")
        h_col, h_hil = self._text_colour(a), self._hilite(a)
        head = heading + " "
        if tag == "nt":
            # Heading on the first line; body wrapped and hung under the text.
            self.screen.add(Text(row, col, heading, h_int, color=h_col,
                                 highlight=h_hil, role="text"))
            body_col = col + len(head)
            lines = self._wrap(text, max(1, self.screen.width - body_col - 1)) \
                if text else []
            for i, ln in enumerate(lines):
                self.screen.add(Text(row + i, body_col, ln, DisplayIntensity.NORMAL,
                                     role="text"))
            if ctx is not None:
                ctx["row"] = row + max(len(lines), 1)
            # Nested blocks (a following <p>, list, …) up to </nt> hang here too.
            self._note_hang = body_col
            return
        # <note>: heading inline, body wrapped to the left margin.
        lines = self._wrap((head + text).strip(),
                           max(1, self.screen.width - col - 1))
        for i, ln in enumerate(lines):
            if i == 0 and (h_col or h_hil):   # colour just the heading run
                runs = [(ln[:len(head)], h_col, h_hil), (ln[len(head):], None, None)]
                self.screen.add(Text.rich(row, col, [r for r in runs if r[0]],
                                          intensity=h_int, role="text"))
            else:
                self.screen.add(Text(row + i, col, ln, DisplayIntensity.NORMAL,
                                     role="text"))
        if ctx is not None:
            ctx["row"] = row + len(lines)

    # ── data area (<da> / <attr>) ────────────────────────────────────────────

    def _emit_attr(self, a):
        """Register one <attr> attribute-character definition on the active <da>.

        Mirrors DTL's ``<ATTR ATTRCHAR=x TYPE=... COLOR=... HILITE=... PADC=...>``:
        the character ``x`` appearing in the data area's body starts a field of
        that type/colour (``datain`` → input, ``dataout``/``char`` → protected).
        """
        if self._da is None:
            # Panel-scope <attr> (a CUA )ATTR-style type definition, e.g.
            # TYPE=FP/NEF/NT) applies to the panel body, which we don't yet model
            # by attribute character — ignore it rather than aborting the panel.
            return
        ch = a.get("attrchar")
        if not ch:
            raise DTLError("<attr> missing required attribute 'attrchar'")

        def _flag(name, *values):
            return str(a.get(name, "")).strip().lower() in (values or ("on",))
        self._da["attrs"][ch] = {
            "type": str(a.get("type", "char")).strip().lower(),
            "color": self._color(a),
            "hilite": self._hilite(a),
            # INTENS HIGH/LOW→normal/NON→non-display (same as a <lstcol> cell).
            "intens": self._cell_intensity(a.get("intens")),
            "padc": (a.get("padc") or " ")[:1],
            # Rendering-effect attributes, applied to the field the char starts:
            "outline": self._outline(a),                 # OUTLINE=L|R|O|U|BOX
            "numeric": _flag("numeric"),                 # NUMERIC=ON → numeric field
            "pad": self._pad_char(a),                    # PAD/PADC empty-cell fill
            "just": str(a.get("just", "asis")).strip().lower(),   # ASIS|LEFT|RIGHT
            "skip": _flag("skip"),          # SKIP=ON → autoskip (client auto-advance)
            "caps": _flag("caps", "on", "in", "out"),     # CAPS → upper-fold input
            # By design, no distinct display effect on this single-byte text server:
            # GE (graphic escape) and FORMAT=DBCS/MIX are DBCS (deferred, #135);
            # PAS/CSRGRP are point-and-shoot (tracked #115); CKBOX (GUI checkbox) and
            # CUADYN (GUI dynamic CUA type) are GUI-only; ATTN (attention field) has
            # no separate render. All are parsed and recorded.
            "ge": _flag("ge"),
            "pas": _flag("pas"),
            "csrgrp": a.get("csrgrp"),
            "ckbox": _flag("ckbox"),
            "cuadyn": a.get("cuadyn"),
            "attn": _flag("attn"),
            "format": str(a.get("format", "")).strip().lower(),   # EBCDIC|DBCS|MIX
        }

    def _emit_da(self):
        """Render the active data area's body. Its lines (dedented, blank edges
        trimmed) are laid out from the area's origin; within a line, each
        attribute character starts a field whose text runs to the next attribute
        character. A mono render is unaffected by any colours (as everywhere)."""
        da = self._da
        if da is None:
            return
        lines = "".join(da["body"]).split("\n")
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines:
            return
        indent = min((len(ln) - len(ln.lstrip(" ")) for ln in lines if ln.strip()),
                     default=0)
        for i, ln in enumerate(lines):
            self._emit_da_line(ln[indent:], da["row"] + i, da["col"], da["attrs"])
        row = da["row"] + len(lines)
        # DEPTH reserves a fixed height: pad the area out so the flow resumes DEPTH
        # rows below its top even if the body is shorter.
        if da.get("depth"):
            row = max(row, da["row"] + da["depth"])
        # EXTEND=ON|FORCE grows the area to the last usable panel row (#125).
        if da.get("extend") in ("on", "force"):
            row = max(row, self.screen.depth - self._bmargin)
        # DIV draws a closing divider spanning the area (or its WIDTH).
        if da.get("div") not in (None, "none", ""):
            div, col = da["div"], da["col"]
            span = da.get("width") or max(1, self.screen.width - col - 1)
            if div == "text":
                t = (da.get("divtext") or "")[:span]
                fmt = da.get("divformat", "start")
                off = (max(0, (span - len(t)) // 2) if fmt == "center"
                       else max(0, span - len(t)) if fmt == "end" else 0)
                if t:
                    self.screen.add(Text(row, col + off, t, DisplayIntensity.NORMAL,
                                         role="rule"))
            elif div != "blank":
                self.screen.add(Text(row, col, "-" * span, DisplayIntensity.NORMAL,
                                     role="rule"))
            row += 1
        if da.get("ctx") is not None:      # advance the enclosing flow past it
            da["ctx"]["row"] = row

    def _emit_da_line(self, line, row, base, attrs):
        """Lay out one data-area line: attribute characters delimit fields; the
        run after each starts at that column (the attribute byte occupies the
        character cell, exactly as on a real 3270)."""
        n = len(line)
        idx = 0
        while idx < n:
            j = idx + 1
            while j < n and line[j] not in attrs:
                j += 1
            if line[idx] in attrs:
                self._emit_da_field(attrs[line[idx]], row, base + idx, line[idx + 1:j])
            elif line[idx:j].strip():
                # Literal text with no governing attribute char (unusual): plain.
                self.screen.add(Text(row, base + idx, line[idx:j]))
            idx = j

    def _emit_da_field(self, spec, row, col, content):
        hidden = spec["intens"] is DisplayIntensity.NON_DISPLAY
        if spec["type"] == "datain":
            # The run after the attribute char is the field's extent (pad-char
            # placeholders in the source), not initial data — so it sets the
            # width and the field starts empty. INTENS/NUMERIC/PAD/OUTLINE from
            # the <attr> apply to the input field (SKIP/CAPS are input behaviour).
            self.screen.add(Field(
                row=row, col=col, length=max(1, len(content)), default="",
                intensity=DisplayIntensity.NORMAL if hidden else spec["intens"],
                hidden=hidden, numeric=spec["numeric"], pad=spec["pad"],
                color=spec["color"], highlight=spec["hilite"],
                outline=spec["outline"],
                # CAPS=ON|IN|OUT folds typed input to upper (recorded on the field
                # like a <lstcol CAPS=ON> cell); SKIP=ON is an autoskip/auto-advance
                # field, carried as the client autotab behaviour (no distinct 3270
                # attribute bit, same as <dtafld AUTOTAB>).
                caps=spec["caps"],
                autotab=spec["skip"],
            ))
        else:  # dataout / char / text → protected display field
            # JUST right/left-justifies the display text within its run width.
            text = content
            if spec["just"] == "right":
                text = content.strip().rjust(len(content))
            elif spec["just"] == "left":
                text = content.strip().ljust(len(content))
            self.screen.add(Text(row, col, text, spec["intens"],
                                 color=spec["color"], highlight=spec["hilite"],
                                 outline=spec["outline"]))

    def _add_field(self, a, content, tag, name, description=None):
        """Emit a prompt (if any) plus its field: an unprotected input field, or —
        for ``usage=out`` — the variable's value as protected display text. A
        trailing ``<dtafldd>`` ``description`` renders after the entry, sized by
        DESWIDTH. Returns the Field, or ``None`` for a display field."""
        # A flowed field (omitted end tag) captures the newline + indentation of
        # the following line into its caption; collapse it, as <info> does, so the
        # prompt is clean. Single-line captions are untouched (byte-identical).
        if "\n" in content:
            content = re.sub(r"\s*\n\s*", " ", content).strip()
        row, col, ctx = self._resolve_pos(a, tag)
        # PMTLOC=ABOVE puts the prompt on the line above the field (the default,
        # BEFORE, is beside it). Emit the caption now and drop the field to the
        # next line at the base column.
        pmtloc = str(a.get("pmtloc", (ctx.get("pmtloc") if ctx else "") or "")
                     ).strip().lower()
        pmt_above = pmtloc == "above"
        usage_out = str(a.get("usage", "")).strip().lower() == "out"
        # A field's own PMTWIDTH (n | * | **) overrides the enclosing <dtacol>'s.
        avail = max(1, self.screen.width - col - 2)
        if "pmtwidth" in a:
            pmtwidth = self._prompt_width(a["pmtwidth"], len(content), avail)
        else:
            pmtwidth = ctx.get("pmtwidth") if ctx else None
        # The prompt is filled with CUA leader dots to PMTWIDTH (PMTFMT=CUA, the
        # default) and an output field's prompt ends with a colon. Above-prompts
        # sit on their own line, so they take the colon but no leader fill.
        prompt_text = self._format_prompt(
            content, None if pmt_above else pmtwidth, usage_out,
            a.get("pmtfmt", (ctx.get("pmtfmt") if ctx else None))) if content else ""
        if pmt_above and content:
            self.screen.add(Text(row, col, prompt_text, _intensity(a), role="prompt"))
            content = prompt_text = ""     # caption already placed
            row += 1
            if ctx is not None:
                ctx["row"] += 1            # the field occupies a second line
        if pmt_above:
            fldcol = col                   # under the prompt, at the base column
        elif pmtwidth:
            # Entry at the fixed prompt column, past the prompt's own trailing
            # attribute byte (so the field's leading attribute doesn't overwrite
            # the last prompt char — the CUA colon / final leader dot).
            fldcol = col + pmtwidth + 1
        elif ctx is not None:
            fldcol = col + len(prompt_text) + ctx["fldgap"]  # entry flows after prompt
        else:
            fldcol = col
        # Entry width: explicit ``entwidth`` wins; otherwise fall back to the
        # variable's display length (``dispmaxlen``), the enclosing <dtacol>'s
        # default entry width, or a small default, so auto-flow guide fields that
        # size via the column or the variable still render.
        default_ew = (ctx.get("entwidth") if ctx else None) or 8
        length = int(a.get("entwidth", a.get("dispmaxlen", default_ew)))
        if fldcol + length > self.screen.width:
            # An auto-flowed field whose entry runs off the panel: our column math
            # only approximates ISPDTLC's (side-by-side dir=horiz columns
            # especially), so clamp it to the panel edge rather than abort the
            # whole panel.
            length = max(1, self.screen.width - fldcol - 1)
            if length < 1 or fldcol >= self.screen.width:
                fldcol = max(col, self.screen.width - 2)
                length = 1
        if prompt_text:
            # The prompt/caption is a CUA element with its own role colour (green,
            # the field-prompt colour); DTL's COLOR on a <dtafld> colours the
            # *field*, not the caption.
            self.screen.add(Text(row, col, prompt_text, _intensity(a), role="prompt"))
        # A trailing <dtafldd> description renders after the entry (past the
        # field's data byte run and its terminator attribute), truncated to
        # DESWIDTH when the author sizes it (DESWIDTH=* keeps the full text).
        if description and description.strip():
            desc = " ".join(description.split())
            # DESWIDTH sizes the description; a field's own value overrides the
            # enclosing <dtacol>'s default (#122).
            dw = str(a.get("deswidth", (ctx.get("deswidth") if ctx else "") or "")).strip()
            if dw.isdigit():
                desc = desc[: int(dw)]
            desc_col = fldcol + length + 2      # attr + data + terminator attr
            desc = desc[: max(0, self.screen.width - desc_col)]
            if desc:
                self.screen.add(Text(row, desc_col, desc, _intensity(a),
                                     role="prompt"))
        # Record the field's external→internal translation map (<xlatl>/<xlati>)
        # so a typed value can be read back to its internal form (Screen.internal_value).
        if name:
            vc = self._field_varclass(name)
            if vc and vc.get("xlati_in"):
                self.screen.translations[name.upper()] = vc["xlati_in"]
        # Attach a nested <assignl>/<assigni> value→result assignment list, keyed by
        # this field's name (Screen.assigned_value reads it back on submit). #55.
        self._attach_assignl(name)
        # USAGE=OUT is a display-only (output) field: show the variable's value as
        # protected text — like a list column — not an editable input box.
        if str(a.get("usage", "")).strip().lower() == "out":
            value = self._subs.get((name or "").upper()) or a.get("init", "")
            # Translate the internal value to its displayed form (<xlatl>/<xlati>).
            value = self._translate_out(name, value)
            self.screen.add(Text(row, fldcol, str(value)[:length].ljust(length),
                                 _intensity(a), color=self._color(a), role="cell",
                                 outline=self._outline(a)))
            self._attach_scrfld(name, row, fldcol, length, ctx)
            return None
        field = Field(
            row=row,
            col=fldcol,
            length=length,
            name=name,
            default=a.get("init", ""),
            numeric=self._resolve_numeric(a, name),
            # IBM's DISPLAY=NO is a non-display field (e.g. a password); DISPLAY
            # defaults to YES (shown).
            hidden=str(a.get("display", "yes")).strip().lower() == "no",
            cursor=_bool_attr(a, "cursor"),
            mdt=_bool_attr(a, "mdt", default=True),
            # DTL COLOR= colours the entry field; else its CUA role (turquoise).
            color=self._color(a),
            role="field",
            highlight=self._hilite(a),
            # OUTLINE box lines; a field's own value wins over the <dtacol>'s default.
            outline=self._outline(a) or (ctx.get("outline") if ctx else None),
            help=self._field_help(a),
            # PAD/PADC fill an empty entry; a field's own PAD wins over the
            # enclosing <dtacol>'s default (None → the conventional space fill).
            pad=self._pad_char(a) or (ctx.get("pad") if ctx else None),
            # AUTOTAB=YES: the cursor auto-advances to the next field when this one
            # fills. There is no TN3270 field-attribute bit for it (it is a client
            # autotab behaviour), so it is recorded as metadata (see Field.autotab).
            autotab=_bool_attr(a, "autotab"),
            # CAPS=ON folds typed input to upper; a field's own value wins over the
            # enclosing <dtacol>'s CAPS default (#122).
            caps=(_bool_attr(a, "caps") if "caps" in a
                  else (ctx.get("caps") == "on" if ctx and ctx.get("caps") else False)),
        )
        self.screen.add(field)
        self._attach_validation(name, a, ctx)
        self._attach_scrfld(name, row, fldcol, length, ctx)
        return field

    # ── <scrfld> scrollable-field indicators ────────────────────────────────

    @staticmethod
    def _scale_ruler(width):
        """The ISPF scale ruler for a ``width``-wide scrollable window:
        ``----+----1----+----2…`` (a ``+`` every 5 columns, the tens digit every
        10). Shown below/above a scrollable field via the SCRFLD SCALE variable."""
        out = []
        for i in range(1, max(0, width) + 1):
            out.append(str((i // 10) % 10) if i % 10 == 0
                       else "+" if i % 5 == 0 else "-")
        return "".join(out)

    # SCRFLD attributes naming an on-panel scroll-indicator variable.
    _SCRFLD_INDICATORS = ("scale", "sindvar", "indvar", "lindvar", "rindvar",
                          "lcolind", "rcolind")

    def _attach_scrfld(self, name, row, col, width, ctx):
        """Attach a pending <scrfld> to the <dtafld> field just emitted at
        ``(row, col)`` with on-screen ``width``. The field's data can be longer than
        the window (DISPLEN); ISPF scrolls it horizontally. Records the scroll
        metadata and, when a scroll-indicator variable is specified, generates the
        indicator line (a SCALE ruler, else a separator of dashes) below the field
        (FLDSPOS=BELOW, the default) or above it, advancing the flow past it."""
        a = self._pending_scrfld
        self._pending_scrfld = None
        if a is None:
            return
        self.screen.scroll_fields.append({
            "name": name,
            "displen": self._opt_int(a.get("displen")),
            "scroll": str(a.get("scroll", "on")).strip().lower(),
            **{k: a[k] for k in self._SCRFLD_INDICATORS if k in a},
        })
        if not any(k in a for k in self._SCRFLD_INDICATORS):
            return                                  # no indicator variable → no line
        text = (self._scale_ruler(width) if "scale" in a
                else ("-" * (width - 1) + ">") if width >= 1 else "")
        below = str(a.get("fldspos", "below")).strip().lower() != "above"
        irow = row + 1 if below else row - 1
        if 0 <= irow < self.screen.depth and text:
            self.screen.add(Text(irow, col, text, DisplayIntensity.NORMAL,
                                 role="prompt"))
            if below and ctx is not None:
                ctx["row"] += 1                     # the indicator occupies a line

    def _attach_assignl(self, name):
        """Attach a pending <assignl> value→result table to the <dtafld> named
        ``name`` just emitted. Records it on the Screen keyed by the field name so
        Screen.assigned_value can, on submit, look the field's value up and return
        the (destvar, result) ISPF's )PROC assignment would set. Keys are
        uppercased for case-insensitive token matching (the corpus fields are
        FORMAT=upper). A list with no DESTVAR carries nowhere and is dropped."""
        al = self._pending_assignl
        self._pending_assignl = None
        if al is None or not name or not al.get("destvar"):
            return
        mapping = {}
        for value, result in al["pairs"]:
            mapping.setdefault(str(value).strip().upper(), result)
        if mapping:
            self.screen.assignments[name.upper()] = {
                "destvar": al["destvar"], "map": mapping,
            }

    @staticmethod
    def _field_help(a):
        """The field-level help *panel name* from HELP=, or None. HELP can also be
        NO/YES, a *message id, or a %varname — none of which name a help panel, so
        those aren't field help here."""
        h = str(a.get("help", "")).strip()
        if not h or h.lower() in ("no", "yes") or h.startswith(("*", "%")):
            return None
        return h

    def _attach_validation(self, name, a, ctx=None):
        """Attach a field's validation to the Screen: its variable-class <checkl>
        checks and/or IBM's REQUIRED=YES (the field must be non-empty on submit).
        A field with no explicit variable class / REQUIRED inherits the enclosing
        <dtacol>'s VARCLASS / REQUIRED defaults (#122)."""
        if not name:
            return
        # Variable class: the field's own VARCLASS attribute, else its <vardcl>
        # declaration, else the enclosing <dtacol>'s default VARCLASS (#122).
        vcname = a.get("varclass")
        if not vcname:
            decl = self._vardcls.get(name.upper())
            vcname = decl.get("varclass") if decl else None
        if not vcname and ctx:
            vcname = ctx.get("varclass")
        vc = self._varclasses.get(str(vcname or "").upper()) if vcname else None
        checks = vc["checks"] if (vc and vc.get("checks")) else []
        if "required" in a:
            required = _bool_attr(a, "required")
        elif ctx and ctx.get("required") is not None:
            required = ctx.get("required") == "yes"
        else:
            required = False
        if not checks and not required:
            return
        entry = {"checkmsg": (vc.get("msg") if vc else None), "checks": checks}
        if required:
            # REQUIRED's message: the field's own MSG, else the class MSG, else a
            # stand-in for ISPF's system "required field" message. (format() echoes
            # an id it doesn't know, so the default renders as plain text.)
            entry["required_msg"] = (a.get("msg") or (vc.get("msg") if vc else None)
                                     or _REQUIRED_DEFAULT_MSG)
        self.screen.validations[name.upper()] = entry

    def _resolve_numeric(self, a, name):
        """Whether the field is numeric. An explicit ``numeric`` attribute wins;
        otherwise inherit it from the variable's declared ``<varclass>``."""
        if "numeric" in a:
            return _bool_attr(a, "numeric")
        return self._declared_numeric(name)

    def _declared_numeric(self, name):
        if not name:
            return False
        decl = self._vardcls.get(name.upper())
        if not decl:
            return False
        vc = self._varclasses.get(str(decl.get("varclass", "")).upper())
        return bool(vc and vc.get("numeric"))

    def _field_varclass(self, name):
        """The <varclass> a field's variable is declared with, or None."""
        decl = self._vardcls.get(name.upper()) if name else None
        return (self._varclasses.get(str(decl.get("varclass", "")).upper())
                if decl else None)

    def _translate_out(self, name, value):
        """Map a stored internal value to its displayed (external) form via the
        variable's <xlatl>/<xlati> translations — e.g. internal "1" → "Enabled".
        Untranslated values (and fields with no translate list) pass through."""
        vc = self._field_varclass(name)
        out = vc.get("xlati_out") if vc else None
        return out.get(str(value).upper(), value) if out else value

    # IBM date/time <varclass> TYPE classes → the exact character pattern ISPF
    # produces for that system variable. IDATE/STDDATE are Gregorian (2- vs
    # 4-digit year); JDATE/JSTD are Julian (year + day-of-year 001-366);
    # ITIME/STDTIME are HH:MM(:SS). We model the class as a shape check — the
    # value must match the format — rather than validating the calendar date.
    _DATETIME_PATTERNS = {
        "idate":   r"\d{2}/\d{2}/\d{2}",       # YY/MM/DD
        "stddate": r"\d{4}/\d{2}/\d{2}",       # YYYY/MM/DD
        "jdate":   r"\d{2}\.\d{3}",            # YY.DDD
        "jstd":    r"\d{4}\.\d{3}",            # YYYY.DDD
        "itime":   r"\d{2}:\d{2}",             # HH:MM
        "stdtime": r"\d{2}:\d{2}:\d{2}",       # HH:MM:SS
    }

    def _emit_varclass(self, a):
        name = a.get("name")
        if not name:
            raise DTLError("<varclass> missing required attribute 'name'")
        # DTL TYPE is a kind plus, for the sized kinds, a maximum length — e.g.
        # "char 8", "dbcs 4", "numeric 5 2". IBM's full set:
        #   CHAR/DBCS/MIXED/EBCDIC/ANY/'%var' <size>  — a length cap
        #   NUMERIC <total-digits> [<fractional-digits>]  — digit / precision cap
        #   IDATE/STDDATE/JDATE/JSTD/ITIME/STDTIME  — a fixed date/time format
        #   VMASK <size>  — an edit mask (length modelled, mask editing is not)
        # We derive numeric-vs-not and translate the TYPE into validation checks
        # the field validator enforces at submit time (#129). The raw kind is kept
        # on the class so nothing is silently dropped, even when unenforced.
        parts = str(a.get("type", "char")).strip().lower().split()
        kind = parts[0] if parts else "char"
        # The size is the first numeric operand after the kind (VMASK/CHAR/… take
        # it as a length; %varname sizes are symbolic and stay unenforced).
        size = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        numeric = kind in ("numeric", "num")
        checks = []
        if numeric:
            # NUMERIC's operands are total-digits then (optionally) fractional-
            # digits. With a non-zero fractional count the value is a fixed-point
            # decimal: cap both the total and fractional digit counts. Without one
            # it is a plain integer — the historical maxdigits cap.
            frac = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
            if size is not None and frac > 0:
                checks.append({"type": "decimal", "total": size, "frac": frac})
            elif size is not None:
                checks.append({"type": "maxdigits", "max": size})
        elif kind in ("char", "dbcs", "mixed", "ebcdic", "any", "vmask"):
            # All the character kinds cap the input length. (DBCS counts double-
            # byte characters; we model the declared character count, which is what
            # a user types.) VMASK's mask editing isn't modelled — only its length.
            if size is not None:
                checks.append({"type": "maxlen", "max": size})
        elif kind in self._DATETIME_PATTERNS:
            # A date/time class: the value must match that format exactly.
            checks.append({"type": "pattern",
                           "regex": self._DATETIME_PATTERNS[kind]})
        self._cur_varclass = name.upper()
        self._varclasses[self._cur_varclass] = {
            "numeric": numeric,
            "kind": kind,                 # raw TYPE kind (recorded even if unenforced)
            "checks": checks,
            "msg": a.get("msg"),          # class-level MSG (IBM's attribute name)
        }

    def _emit_checki(self, a, content):
        """A <checki> validity-check item. The value list / range may be given as
        element text (``v1 v2 …`` / ``min max``) or — the form the guide uses — via
        attributes: ``type=values parm1=EQ|NE parm2='v1 v2'`` (EQ = must be one of;
        NE = must not be). ``alpha`` / ``name`` are character-class checks.

        Enforced on submit: RANGE, VALUES, ALPHA/ALPHAB, NAME/NAMEF, NUM, HEX, LEN,
        PICT, BIT, IPADDR4, the date/time formats (IDATE/STDDATE/JDATE/JSTD/ITIME/
        STDTIME), and the data-set-name family (DSNAME/DSNAMEQ/DSNAMEPQ, the F/M
        member variants, FILEID). Left lenient by design: DBCS/MIX (deferred, #135);
        LISTV/LISTVX/ENUM (require a runtime dialog %varlist a static display server
        does not hold); INCLUDE (a composite include-check); EBCDIC (every byte on a
        single-byte terminal is EBCDIC-representable) — all recognised so the panel
        loads, none rejecting input."""
        ctype = str(a.get("type", "")).strip().lower()
        words = content.split()
        parm2 = a.get("parm2")
        if ctype == "range":
            if parm2 is not None:                     # parm1=low-bound parm2=high
                lo, hi = int(a.get("parm1")), int(parm2)
            elif len(words) == 2:
                lo, hi = int(words[0]), int(words[1])
            else:
                raise DTLError('<checki type="range"> needs "min max" or parm1/parm2')
            self._checkl["checks"].append({"type": "range", "min": lo, "max": hi})
        elif ctype == "values":
            vals = parm2.split() if parm2 is not None else words
            negate = str(a.get("parm1", "EQ")).strip().upper() == "NE"
            self._checkl["checks"].append(
                {"type": "values", "values": [v.upper() for v in vals],
                 "negate": negate}
            )
        elif ctype in ("alpha", "alphab"):
            self._checkl["checks"].append({"type": "alpha"})
        elif ctype in ("name", "namef"):
            self._checkl["checks"].append({"type": "name"})
        elif ctype == "num":                          # all-numeric (optional sign)
            self._checkl["checks"].append({"type": "num"})
        elif ctype == "hex":                          # hexadecimal digits
            self._checkl["checks"].append({"type": "hex"})
        elif ctype == "len":                          # length op: PARM1 op, PARM2 len
            op = str(a.get("parm1", "EQ")).strip().upper()
            raw = a.get("parm2") if a.get("parm2") is not None else \
                (words[-1] if words else None)
            ln = self._opt_int(raw)
            if ln is not None:
                self._checkl["checks"].append({"type": "len", "op": op, "len": ln})
        elif ctype in ("pict", "picture"):            # picture-string mask (PARM2)
            mask = a.get("parm2") if a.get("parm2") is not None else \
                (words[0] if words else "")
            if mask:
                self._checkl["checks"].append({"type": "pict", "mask": str(mask)})
        elif ctype in self._DATETIME_PATTERNS:        # IDATE/STDDATE/JDATE/JSTD/…
            # A date/time check: the value must match that system format exactly
            # (shape check — the calendar validity is not modelled). Same patterns
            # the date/time <varclass> classes use (#129).
            self._checkl["checks"].append(
                {"type": "pattern", "regex": self._DATETIME_PATTERNS[ctype]})
        elif ctype == "bit":                          # BIT: binary digits only
            self._checkl["checks"].append({"type": "bit"})
        elif ctype in ("ipaddr4", "ipaddr"):          # IPADDR4: dotted-quad IPv4
            self._checkl["checks"].append({"type": "ipaddr4"})
        elif ctype in ("dsname", "dsnameq", "dsnamepq"):   # data set name (no member)
            self._checkl["checks"].append({"type": "dsname"})
        elif ctype in ("dsnamef",):                   # DSNAMEF: optional member
            self._checkl["checks"].append({"type": "dsname", "member": True})
        elif ctype in ("dsnamefm", "dsnamem"):        # DSNAMEFM/M: member required
            self._checkl["checks"].append(
                {"type": "dsname", "member": True, "member_required": True})
        elif ctype == "fileid":                       # FILEID: a data set + optional member
            self._checkl["checks"].append({"type": "dsname", "member": True})

    def _emit_xlati(self, a, content):
        """One ``<xlati value=internal>external`` translation item. The external
        (the form the user types and sees) is the element text — which may be a
        ``<lit>`` literal run; ``value=`` is the internal form it maps to/from."""
        external = " ".join(content.split())
        if external:
            self._xlatl["items"].append(external)
            if a.get("value") is not None:
                self._xlatl["pairs"].append((a.get("value"), external))

    def _end_xlatl(self):
        """Close an ``<xlatl>`` translate list. ``FORMAT=upper`` marks the class as
        uppercased. An ``<xlatl>`` that lists ``<xlati>`` translations restricts
        valid input to those external values (a typed value must translate) — added
        as an ``xlati`` validation check that fails with the ``<xlatl>``'s MSG."""
        xl = self._xlatl
        self._xlatl = None
        if xl is None or self._cur_varclass not in self._varclasses:
            return
        vc = self._varclasses[self._cur_varclass]
        if xl["upper"]:
            vc["upper"] = True
        if xl["items"]:
            upper = xl["upper"] or vc.get("upper", False)
            values = [v.upper() for v in xl["items"]] if upper else list(xl["items"])
            vc["checks"].append({
                "type": "xlati",
                "values": values,
                "upper": upper,
                "msg": xl["msg"] or vc.get("msg"),
            })
        # The internal↔external translation maps: OUT maps a stored internal value
        # to its displayed form; IN maps a typed external form back to the internal
        # value (uppercased key when the class/list is FORMAT=upper).
        if xl["pairs"]:
            upper = xl["upper"] or vc.get("upper", False)
            out = vc.setdefault("xlati_out", {})
            inn = vc.setdefault("xlati_in", {})
            for internal, external in xl["pairs"]:
                out.setdefault(str(internal).upper(), external)
                inn.setdefault(external.upper() if upper else external, internal)

    def _emit_source(self, a, content):
        """A )PROC/)INIT source block. It renders nothing. When it assigns the
        selection variable ``&ZSEL = TRANS(&ZCMD n,'target' ...)`` — ISPF's option
        routing — record each option's selection string in ``selection_targets`` so
        the server can dispatch a menu choice declaratively (see #55). Only this one
        idiom is recognised; any other proc content is ignored."""
        if "ZSEL" not in content.upper():
            return
        m = _ZSEL_TRANS_OPEN_RE.search(content)
        if not m:
            return
        body = _balanced_parens(content, m.end() - 1)  # from the TRANS '('
        # Each `option,'selection-string'` pair. The option is a run of digits or a
        # single (word-boundaried) letter — which skips the TRANS source expression
        # (e.g. TRUNC(&ZCMD,'.')) and the `*,'?'` default without matching them.
        # ISPF's TRANS returns the first match, so the first declaration wins.
        for opt, target in _ZSEL_PAIR_RE.findall(body):
            self.screen.selection_targets.setdefault(opt.upper(), target.strip())

    def _emit_vardcl(self, a):
        # A <vardcl> belongs in a <varlist>, but tolerate a stray one (some guide
        # examples begin mid-declaration) rather than aborting the whole panel —
        # a declaration with no name simply carries nothing to record.
        name = a.get("name")
        if not name:
            return
        self._vardcls[name.upper()] = {"varclass": a.get("varclass", "")}

    def _emit_dtafld(self, a, content, description=None):
        self._add_field(a, content, "dtafld", a.get("datavar"),
                        description=description)

    def _emit_cmdarea(self, a, content):
        # The command area is ISPF's command/option line; its variable defaults
        # to the conventional ZCMD. A flowed command area gets a leading blank line
        # (the title/body separator), like a paragraph.
        self._skip_blank_before(a)
        # PMTTEXT=NO suppresses the command-prompt text ("Option ===>"): the field
        # is emitted bare (default YES keeps the prompt, so byte-identical when absent).
        if not _bool_attr(a, "pmttext", default=True):
            content = ""
        field = self._add_field(a, content, "cmdarea", a.get("datavar", "ZCMD"))
        # CMDLEN=MAX extends the command entry to the panel's right edge (DEFAULT,
        # the ISPF-sized field, is what _add_field already produced). CMDLOC=ASIS
        # keeps the coded position (our placement is already as-coded) and is
        # recorded on the field; DEFAULT is the ISPF-repositioned default.
        if field is not None:
            if str(a.get("cmdlen", "default")).strip().lower() == "max":
                field.length = max(field.length, self.screen.width - field.col - 1)
            field.cmdloc = str(a.get("cmdloc", "default")).strip().lower()
            # CAPS: the command line folds typed input to upper by default (ISPF
            # caps-on); the server already upper-folds the command for dispatch, so
            # CAPS=ON is faithful. Record CAPS=OFF (case-preserving) for callers.
            field.caps = _bool_attr(a, "caps", default=True)
        self.screen.command_field = field
        if self._scroll and field is not None:
            self._emit_scroll_field(field)

    def _emit_scroll_field(self, cmd_field):
        """Render the <lstfld scrollvar=> "Scroll ===>" amount field at the right of
        the command line, and shorten the command field so it does not overlap.
        Per the reference, the scroll entry is only added if the command field can
        still be at least 8 bytes wide."""
        SWIDTH = 4                                  # scroll amount (PAGE/HALF/n...)
        label = "Scroll ===>"
        sfield_col = self.screen.width - SWIDTH - 1     # 1-col right margin
        slabel_col = sfield_col - len(label) - 1        # label + a space
        # Room check: the command field must keep >= 8 bytes to its left.
        if slabel_col - 1 - cmd_field.col < 8:
            return
        row = cmd_field.row
        # Clamp the command field so its data ends before the scroll label.
        cmd_field.length = min(cmd_field.length, slabel_col - 1 - cmd_field.col)
        self.screen.add(Text(row, slabel_col, label, DisplayIntensity.NORMAL,
                             role="prompt"))
        value = str(self._subs.get(self._scroll["var"].upper(), "PAGE"))
        if self._scroll["caps"]:
            value = value.upper()
        self.screen.add(Field(
            row=row, col=sfield_col, length=SWIDTH,
            name=self._scroll["var"], default=value[:SWIDTH],
            terminator=True, role="field", help=self._scroll["help"],
        ))

    def _emit_selfld_prompt(self, sf):
        """Emit the selection field's caption (the text before its first
        ``<choice>``), once, before the choices are laid out. PMTLOC=ABOVE (the
        default) puts it on the line(s) above the list; PMTLOC=BEFORE puts it to
        the list's left, shifting the choice columns right past it. An empty prompt
        (the bundled numbered menus) renders nothing, so they stay byte-identical."""
        if sf["prompt_done"]:
            return
        sf["prompt_done"] = True
        text = re.sub(r"\s+", " ", "".join(sf["prompt_chars"])).strip()
        if not text:
            return
        col = sf["origin"]
        if sf["pmtloc"] == "before":
            # Caption to the left, wrapped into its PMTWIDTH column; the choices
            # start past it on the same first row.
            width = sf["pmtwidth"] or (len(text) + 1)
            for i, line in enumerate(self._wrap(text, max(1, width))):
                self.screen.add(Text(sf["row"] + i, col, line, role="prompt"))
            shift = col + width - sf["numcol"]
            if shift > 0:
                sf["numcol"] += shift
                sf["namecol"] += shift
                sf["desccol"] += shift
        else:
            # Caption above the list, wrapped to the selection width; the choices
            # then flow below it.
            width = sf["selwidth"] or sf["pmtwidth"] or (self.screen.width - col - 1)
            lines = self._wrap(text, max(1, width))
            for i, line in enumerate(lines):
                self.screen.add(Text(sf["row"] + i, col, line, role="prompt"))
            sf["row"] += len(lines)

    def _var_truthy(self, val):
        """Truthiness of a HIDE/UNAVAIL-style condition. A bare attribute (no
        value) is always true; otherwise the value names a dialog variable —
        false only when that variable is empty or ``0`` (a literal ``0``/``no``/
        ``off`` also counts as false if it isn't a known variable)."""
        if val is None or str(val).strip() == "":
            return True                         # bare attribute → unconditional
        key = str(val).strip()
        raw = self._subs.get(key.upper())
        if raw is None:                         # not a known variable → boolean literal
            return key.lower() not in ("0", "no", "off", "false")
        return str(raw).strip() not in ("", "0")

    def _choice_hidden(self, a):
        """Whether a <choice> is hidden: HIDE=var removes it when the variable is
        true; HIDEX=var removes it when the variable is false (the inverse)."""
        if "hide" in a and self._var_truthy(a["hide"]):
            return True
        if "hidex" in a and not self._var_truthy(a["hidex"]):
            return True
        return False

    def _choice_grid_pos(self, sf):
        """The (row, x-offset) of the next choice in a CHOICECOLS-wide grid (#128).

        Choices fill down each column in turn when CHOICEDEPTH is given (column-
        major, as ISPF lays them out); with no depth they fill across the row
        (row-major), which needs no total count. CWIDTHS sets each column's stride;
        with none, the columns are spread evenly across the field's width."""
        idx = sf["count"]
        cols = sf["choicecols"]
        depth = sf.get("choicedepth")
        if depth:
            cidx, ridx = divmod(idx, depth)      # column-major (fill down columns)
        else:
            ridx, cidx = divmod(idx, cols)       # row-major (fill across rows)
        cidx = min(cidx, cols - 1)
        widths = sf.get("cwidths") or []
        if widths:
            gx = sum(widths[:cidx])
        else:
            stride = max(1, (self.screen.width - sf["origin"] - 1) // cols)
            gx = cidx * stride
        return sf["field_row0"] + ridx, gx

    def _emit_choice(self, a, content):
        sf = self._selfld
        if sf is None:
            raise DTLError("<choice> outside of a <selfld>")
        self._emit_selfld_prompt(sf)
        # A <chofld> nested in this choice split the captured text: the part banked
        # before it is the choice description; `content` (the part after) is the
        # field's own description. Consume both now (even for a HIDE-hidden choice,
        # so the pending state never leaks into the next choice).
        chofld = self._pending_chofld
        self._pending_chofld = None
        chofld_desc = ""
        if chofld is not None:
            chofld_desc, content = content, (self._chofld_choicetext or "")
        self._chofld_choicetext = None
        # HIDE/HIDEX conditionally remove the choice from the list (dynamic panels
        # show a variable subset). A hidden choice renders nothing, consumes no
        # row, and isn't selectable — the choices below it move up.
        if self._choice_hidden(a):
            return
        if sf.get("field_row0") is None:
            sf["field_row0"] = sf["row"]          # first choice → the field's top row
        # A multi-column choice grid places each choice at its (row, x-offset) in the
        # CHOICECOLS-wide grid; a single-column field keeps the flowing row (gx=0, so
        # the layout is byte-identical). #128.
        if sf.get("choicecols", 1) > 1:
            row, gx = self._choice_grid_pos(sf)
        else:
            row, gx = sf["row"], 0
        # Auto-number: standard DTL numbers the choices 1..n, so a <choice> that
        # omits its selection value takes the running position (single-select only —
        # a MULTI field marks choices with an input field instead of numbering them).
        # SELCHAR is the standard way to override that value (a menu number/letter
        # placed in front of the choice, e.g. option 8 in a 1..5,8 menu, or X):
        # its 'char(s),n' form gives the char(s); the trailing ,n (HIDE sizing) is
        # unused here.
        sel = a.get("selchar")
        disp = sel.split(",")[0].strip() if sel is not None else None
        auto_num = disp is None and not sf.get("multi")
        num = str(sf["count"] + sf.get("fchoice", 1)) if auto_num else (disp or "")
        # A <ps value=*> inside a choice uses the choice's number (or SELCHAR value)
        # as its point-and-shoot value — resolve it now that the number is known.
        if self._pending_ps is not None and self._pending_ps[1] == "*":
            self._pending_ps = (self._pending_ps[0], num)
        # On the first choice, a column-less single-choice field whose choices are
        # auto-numbered switches to the reference figure layout: a selection input
        # field before the first choice, and "N." (number + period) numbering.
        if sf["count"] == 0 and sf.get("single_eligible") and auto_num:
            sf["auto_single"] = True
            sf["period"] = True
            base, ew = sf["origin"] - 1, sf["entwidth"]
            sf["inputcol"] = base                 # selection input field (attr byte)
            sf["numcol"] = base + 1 + ew + 2      # input field + gap
            sf["desccol"] = sf["numcol"] + 4      # "N." + gap
        # UNAVAIL: the choice is shown but can't be selected. 3270 has intensity
        # (normal / intensified-high / non-display) but no *sub-normal* dim level,
        # so it's de-emphasised by dropping the number from the usual high to
        # normal intensity (dimmer on mono too) and, on a colour terminal, the CUA
        # "unavailable" blue.
        unavail = _bool_attr(a, "unavail")
        # An explicit COLOR (the choice's, else the <selfld>'s) colours the whole
        # row; otherwise each part takes its CUA role colour — as on real ISPF the
        # number, keyword, and description are white, turquoise, and green.
        explicit = self._color(a) or sf.get("color")
        rnum, rname, rdesc = ("unavail", "unavail", "unavail") if unavail \
            else ("num", "name", "desc")
        num_int = DisplayIntensity.NORMAL if unavail else sf["numintensity"]
        # The value that selects this choice: IBM's MATCH attribute, defaulting
        # to the displayed (auto-)number.
        match = a.get("match", num).strip().upper()
        mark = None
        if sf.get("multi") and not unavail:
            # Multiple-selection: a 1-char input field the user marks, in place of
            # the number. Its modified value (any non-blank char) selects the choice.
            mark = Field(
                row=row, col=sf["numcol"] + gx, length=1,
                name=(a.get("name") or f'{sf["name"]}{sf["count"]}') or None,
                color=explicit, role="field",
                pad=sf.get("pad"), outline=sf.get("outline"),
            )
            self.screen.add(mark)
        else:
            if sf.get("auto_single") and sf["count"] == 0:
                # A standard single-choice field has one selection input field
                # before the first choice; the user types the chosen number into
                # it. Its name is the SELFLD's NAME (per the CHOICE reference).
                self.screen.add(Field(
                    row=row, col=sf["inputcol"] + gx, length=sf["entwidth"],
                    name=sf["name"] or None, color=explicit, role="field",
                    pad=sf.get("pad"), outline=sf.get("outline")))
            # SINGLE choices are numbered "N." (number + period); a MENU/explicit
            # field uses the bare number padded to numwidth. SELFMT=END right-
            # justifies the number within the selection column (START, the default,
            # left-justifies it — byte-identical). #128.
            if sf.get("period"):
                num_text = num + "."
            elif sf.get("selfmt") == "end":
                num_text = num.rjust(sf["numwidth"])
            else:
                num_text = num.ljust(sf["numwidth"])
            self.screen.add(Text(row, sf["numcol"] + gx, num_text,
                                 num_int, color=explicit, role=rnum))
        # A MULTI choice's NAME is the field identifier (used to read the mark
        # back), not display text — the row is just the mark + description. A
        # single/menu choice shows its keyword.
        name = a.get("name", "")
        show_name = bool(name) and not sf.get("multi")
        if show_name:
            self.screen.add(Text(row, sf["namecol"] + gx, name, color=explicit,
                                 role=rname))
        # Description column: a standard single-choice, or any choice with no
        # visible keyword (auto grid), hugs the number/mark; a keyworded grid uses
        # the far description column.
        if sf.get("auto_single"):
            desccol = sf["desccol"]
        elif sf.get("auto_cols") and not show_name:
            desccol = sf["namecol"]
        else:
            desccol = sf["desccol"]
        desccol += gx                            # grid column x-offset (0 single-col)
        # A flowed choice (text on the line after <choice>, as the <ps>/<chofld>
        # guide examples code it) captures a leading newline + indentation; drop it.
        # Deliberate leading spaces on the *same* line (which position the
        # description) and the internal keyword/description gap are both preserved.
        desc_text = re.sub(r"^\s*\n\s*", "", content) if "\n" in content else content
        desc_text = desc_text.rstrip()
        self.screen.add(Text(row, desccol, desc_text, color=explicit, role=rdesc))
        # A <chofld> lays out an input field just past the description, with its own
        # description on the line below (see Figure 96 in the DTL guide).
        extra = 0
        if chofld is not None:
            extra = self._emit_chofld(chofld, chofld_desc, row, desccol,
                                      len(desc_text), explicit)
        if sf.get("choicecols", 1) > 1:
            # In grid mode the next choice computes its own (row, column); just track
            # the deepest row used so the flow resumes below the whole grid.
            sf["grid_maxrow"] = max(sf.get("grid_maxrow") or row, row + extra)
        else:
            sf["row"] = row + 1 + extra
        sf["count"] += 1
        if unavail:
            return                          # not selectable → no routing/point-and-shoot
        if match:
            # The selection value carries the choice's keyword (the server names it
            # in an "OPTION n (keyword) not implemented" message). A folded menu
            # choice has no NAME — its keyword is the text before the description's
            # 2-space gap, e.g. "Data Set" in "Data Set   Allocate ...".
            keyword = a.get("name", "").strip()
            if not keyword and content:
                keyword = re.split(r"\s{2,}", content.strip(), 1)[0]
            self.screen.selections[match] = keyword
            if mark is not None:
                # Record how to read this multi-select mark field back.
                self.screen.selection_fields.append(
                    {"value": match, "name": a.get("name", "").strip(),
                     "addr": mark.data_addr(self.screen.width)}
                )
            else:
                # Single-select: remember which row this choice renders on, so the
                # cursor can select it (point-and-shoot).
                self.screen.selection_rows[row] = match
            # CHECKVAR names the variable holding the current selection; when its
            # value equals this choice's MATCH, the choice is current — land the
            # cursor on it so the user sees (and can re-select) the current choice.
            checkvar = a.get("checkvar")
            if checkvar and self._subs.get(checkvar.strip().upper(), "").strip().upper() == match:
                self.screen.cursor_at = (row, mark.col if mark is not None
                                         else sf["namecol"] + gx)

    def _emit_chdiv(self, a, content):
        """Render a ``<chdiv>`` (choice divider) within a ``<selfld>``: a line that
        separates groups of choices. ``TYPE=SOLID``/``DASH`` draw a dashed rule
        across the choice area; ``TYPE=TEXT`` (or any divider text) writes the
        caption, justified by ``FORMAT``; ``TYPE=NONE`` (the default, no text) is a
        blank separator row. Advances the choice flow past the divider."""
        sf = self._selfld
        if sf is None:
            raise DTLError("<chdiv> outside of a <selfld>")
        # A divider before any choice still shows the field prompt first.
        self._emit_selfld_prompt(sf)
        row = sf["row"]
        start = sf.get("inputcol")
        if start is None:
            start = sf["numcol"]
        width = sf.get("selwidth") or max(1, self.screen.width - start - 1)
        # GUTTER=n insets the divider n characters at each end.
        gutter = self._opt_int(a.get("gutter"), 0) or 0
        if gutter > 0:
            start, width = start + gutter, max(1, width - 2 * gutter)
        typ = str(a.get("type", "none")).strip().lower()
        text = " ".join(content.split())
        N = DisplayIntensity.NORMAL
        if typ in ("solid", "dash"):
            self.screen.add(Text(row, start, ("-" * width)[:width], N, role="rule"))
        elif text:                       # TYPE=TEXT / bare text: the caption, justified
            t = text[:width]
            fmt = str(a.get("format", "start")).strip().lower()
            off = (max(0, (width - len(t)) // 2) if fmt == "center"
                   else max(0, width - len(t)) if fmt == "end" else 0)
            self.screen.add(Text(row, start + off, t, N, role="rule"))
        # TYPE=NONE with no text → a blank separator row (draw nothing).
        sf["row"] = row + 1

    def _emit_chofld(self, a, description, row, desccol, desclen, color):
        """Lay out a <chofld> (choice data field): an input (or output) field just
        past the choice description, with the field's own description — if any — on
        the line below, indented to the description column (per Figure 96 in the DTL
        guide). Returns the number of extra rows the description consumed (0 or 1)."""
        length = int(a.get("entwidth", 8))
        # FLDSPACE sets the gap between the choice description and the entry field
        # (default 1); ALIGN positions the field's own description under it (#115).
        gap = self._opt_int(a.get("fldspace"), 1) or 1
        fldcol = desccol + desclen + gap
        if fldcol + length + 1 >= self.screen.width:   # clamp to the panel edge
            length = max(1, self.screen.width - fldcol - 2)
        name = a.get("datavar")
        if name:                                    # honour the variable's <xlatl> map
            vc = self._field_varclass(name)
            if vc and vc.get("xlati_in"):
                self.screen.translations[name.upper()] = vc["xlati_in"]
        # USAGE=OUT is a display-only field (the variable's value as protected text);
        # otherwise an editable entry the user types into.
        if str(a.get("usage", "")).strip().lower() == "out":
            value = self._translate_out(name, self._subs.get((name or "").upper())
                                        or a.get("init", ""))
            self.screen.add(Text(row, fldcol, str(value)[:length].ljust(length),
                                 color=self._color(a) or color, role="cell",
                                 outline=self._outline(a)))
        else:
            self.screen.add(Field(
                row=row, col=fldcol, length=length, name=name or None,
                default=a.get("init", ""),
                numeric=self._resolve_numeric(a, name),
                hidden=str(a.get("display", "yes")).strip().lower() == "no",
                color=self._color(a) or color, role="field",
                highlight=self._hilite(a), outline=self._outline(a),
                help=self._field_help(a), pad=self._pad_char(a),
                # AUTOTAB=YES: cursor auto-advance on fill (client behaviour, no
                # 3270 data-stream bit) — recorded as metadata (#115, as on <dtafld>).
                autotab=_bool_attr(a, "autotab"),
            ))
            self._attach_validation(name, a)
        desc = " ".join((description or "").split())
        if desc:
            # The description sits below the entry, indented to the description
            # column (Figure 96). ALIGN=CENTER|END positions it within the entry
            # field's width (START, the default, keeps it at desccol — byte-identical).
            align = str(a.get("align", "start")).strip().lower()
            off = 0
            if align in ("center", "end") and length > len(desc):
                span = length - len(desc)
                off = span // 2 if align == "center" else span
            self.screen.add(Text(row + 1, desccol + off, desc, color=color,
                                 role="desc"))
            return 1
        return 0

    def _end_pdc(self):
        """Finalise the open <pdc> onto its <abc>. DTL omits most end tags, so a
        pull-down is also closed by the next <pdc> or by </abc> (not only </pdc>)."""
        if self._cur_pdc is not None and self._cur_abc is not None:
            raw = "".join(self._cur_pdc["chars"])
            label = raw.strip()
            mnem = self._cur_pdc.get("mnemonic")
            if mnem is not None:               # re-base the offset onto the label
                mnem -= len(raw) - len(raw.lstrip())
                mnem = mnem if 0 <= mnem < len(label) else None
            item = {
                "label": label, "action": self._cur_pdc["action"], "mnemonic": mnem,
                "help": self._cur_pdc.get("help"),
            }
            # Optional behaviours are added only when present, so ordinary items keep
            # their four-key shape (and the action-bar model stays compact).
            for key in ("unavail", "checked", "type", "parm", "setvar", "togvar",
                        "acc", "applcmd", "newappl", "mode", "lang"):
                val = self._cur_pdc.get(key)
                if val:
                    item[key] = val
            self._cur_abc["pdc"].append(item)
        self._cur_pdc = None

    def _end_abc(self):
        """Finalise the open <abc> (and its last <pdc>) onto the action bar —
        closed by the next <abc> or by </ab>, not only </abc>."""
        self._end_pdc()
        if self._cur_abc is not None and self._ab is not None:
            raw = "".join(self._cur_abc["chars"])
            label = raw.strip()
            mnem = self._cur_abc.get("mnemonic")
            if mnem is not None:               # re-base the offset onto the label
                mnem -= len(raw) - len(raw.lstrip())
                mnem = mnem if 0 <= mnem < len(label) else None
            choice = {
                "label": label, "pdc": self._cur_abc["pdc"], "mnemonic": mnem,
                "help": self._cur_abc.get("help"),
            }
            if self._cur_abc.get("pdcvar"):
                choice["pdcvar"] = self._cur_abc["pdcvar"]
            self._ab["choices"].append(choice)
        self._cur_abc = None

    def _emit_action_bar(self, ab):
        """Lay the action-bar choice labels out across the bar's row (high
        intensity) and record the choices + their pull-downs on the Screen. Each
        choice keeps its ``row``/``col`` so the server can map a cursor onto it
        for point-and-shoot."""
        col = ab["col"]
        sep = ab.get("absepstr")            # explicit between-choice separator string
        choices = ab["choices"]
        for i, choice in enumerate(choices):
            label = choice["label"]
            choice["row"], choice["col"] = ab["row"], col
            m = choice.get("mnemonic")
            # MNEMGEN=YES: a choice with no explicit <M> mnemonic gets its first
            # letter underlined as the auto-generated shortcut.
            if m is None and ab.get("mnemgen") and label:
                m = 0
            if m is not None and 0 <= m < len(label):
                # Underline the mnemonic letter (the shortcut), as ISPF does. Mono
                # renders the concatenation identically to a plain Text.
                runs = [(label[:m], None, None),
                        (label[m], None, Highlight.UNDERSCORE),
                        (label[m + 1:], None, None)]
                self.screen.add(Text.rich(ab["row"], col, [r for r in runs if r[0]],
                                          intensity=DisplayIntensity.HIGH))
            else:
                self.screen.add(Text(ab["row"], col, label, DisplayIntensity.HIGH))
            col += len(label)
            if i < len(choices) - 1:
                if sep is not None:
                    # ABSEPSTR: draw the separator string in the bar between choices
                    # (e.g. " | "), then resume laying out at its far edge.
                    self.screen.add(Text(ab["row"], col, sep, DisplayIntensity.HIGH))
                    col += len(sep)
                else:
                    col += ab["gap"]        # default: a plain gap of blanks
        # ABSEPCHAR draws the separator *line* below the action bar (the horizontal
        # rule dividing the bar from the panel body), spanning the panel width.
        sepchar = ab.get("absepchar")
        if sepchar:
            width = max(1, self.screen.width - ab["col"] - 1)
            self.screen.add(Text(ab["row"] + 1, ab["col"], sepchar[0] * width,
                                 role="rule"))
        self.screen.action_bar = choices

    def _finalize_cmd_trunc(self):
        """Apply a captured <t> truncation point to the current <cmd>: the number
        of characters before it is the command's minimum abbreviation (``trunc``).
        A <t> wins over any TRUNC= attribute; with no <t> the command is unchanged."""
        if self._cur_cmd is not None and self._cmd_tpos is not None:
            self._cur_cmd["trunc"] = self._cmd_tpos

    def _emit_keyi(self, a):
        if self._keylist is None:
            raise DTLError("<keyi> outside of a <keyl>")
        self._finalize_keyi()          # close a previous <keyi> (DTL omits end tags)
        key = a.get("key")
        if not key:
            raise DTLError("<keyi> missing required attribute 'key'")
        # ``cmd`` is the command the key invokes (ISPF allows ``action`` too);
        # the key is case-insensitive. CASE=UPPER (the default) folds the command
        # to upper; CASE=MIXED preserves its authored case. PARM is a parameter
        # string carried with the command — appended so the resolved command is
        # ``CMD PARM`` (command dispatch splits the verb off, so an empty PARM is
        # byte-identical to none).
        cmd = a.get("cmd", a.get("action", ""))
        if str(a.get("case", "upper")).strip().lower() != "mixed":
            cmd = cmd.upper()
        parm = str(a.get("parm", "")).strip()
        self._keylist[key.upper()] = (cmd + " " + parm).strip() if parm else cmd
        # FKA=NO|YES|LONG|SHORT governs whether the key appears in the function-key
        # area; the element content is the FKA display text. Capture it (unless
        # FKA=NO suppresses it) so the FKA line can be labelled.
        self._keyi = {"key": key.upper(),
                      "show": str(a.get("fka", "yes")).strip().lower() != "no",
                      # CASE folds the FKA label (UPPER/LOWER; ASIS/MIXED keep it).
                      "case": str(a.get("case", "")).strip().lower(),
                      "chars": []}

    def _finalize_keyi(self):
        """Bank an open <keyi>'s FKA-text onto the Screen and clear it. The text is
        the key's function-key-area label (e.g. ``F3=Exit``); FKA=NO suppresses it."""
        ki = self._keyi
        self._keyi = None
        if ki is None:
            return
        text = "".join(ki["chars"]).strip()
        if ki.get("case") == "upper":
            text = text.upper()
        elif ki.get("case") == "lower":
            text = text.lower()
        if ki["show"] and text:
            self.screen.keylist_fka[ki["key"]] = text


def load_dtl(source: str, rows=None, screen_rows=None, screen_cols=None,
             row_offset=0, row_total=None, **subs) -> Screen:
    """Parse DTL markup into a :class:`screen.Screen`.

    ``subs`` provides values for ``&NAME`` dialog-variable references in the
    source (e.g. ``ZUSER``, ``ZTIME``) before parsing. ``rows`` populates a
    ``<lstfld>`` list/table: a sequence of ``{datavar: value}`` mappings, one
    per model row (when omitted, a single empty model row is laid out).
    ``screen_rows``/``screen_cols`` override the panel's presentation size (so a
    list panel can lay out more rows on a larger alternate screen).

    ``row_offset``/``row_total`` describe the paged window when ``rows`` is a
    *slice* of a larger table (see the server's table pagers, #281):
    ``row_offset`` is the index of the first supplied row within the full set,
    and ``row_total`` the full row count. They drive the ``ROW x TO y OF z``
    scroll status and the ``BOTTOM OF DATA`` marker (drawn only when the last row
    of the full set is on screen). The defaults (``0`` / ``len(rows)``) describe
    an unpaged table, so a single-page render is byte-for-byte unchanged.
    """
    source = _resolve_entities(source)
    source = _substitute(source, subs)
    parser = _DTLParser()
    parser._rows = rows
    parser._subs = {k.upper(): v for k, v in (subs or {}).items()}
    parser._override_rows = screen_rows
    parser._override_cols = screen_cols
    parser._row_offset = row_offset
    parser._row_total = row_total
    parser.feed(source)
    parser.close()
    return parser.screen


def load_panel(name: str, directory: str = None, rows=None,
               screen_rows=None, screen_cols=None, row_offset=0,
               row_total=None, **subs) -> Screen:
    """Load and parse ``<directory>/<name>.dtl``.

    ``directory`` defaults to the ``panels`` folder next to this module, so the
    panels resolve regardless of the process's current working directory.
    ``rows`` populates a ``<lstfld>`` list/table (see :func:`load_dtl`);
    ``screen_rows``/``screen_cols`` override the presentation size;
    ``row_offset``/``row_total`` describe the paged window (see :func:`load_dtl`).
    """
    import os
    if directory is None:
        directory = os.path.join(os.path.dirname(os.path.abspath(__file__)), "panels")
    path = os.path.join(directory, f"{name}.dtl")
    with open(path, "r", encoding="utf-8") as fh:
        return load_dtl(fh.read(), rows=rows, screen_rows=screen_rows,
                        screen_cols=screen_cols, row_offset=row_offset,
                        row_total=row_total, **subs)


class MessageCatalog:
    """Messages parsed from a DTL ``<msgmbr>``, looked up by id.

    Mirrors how ISPF keeps messages in a message library (ISPMLIB), separate
    from panels: :meth:`format` returns the displayable ``"<id> <text>"`` with
    any ``&NAME`` references in the text substituted at display time. Each message
    also carries presentation attributes (see :meth:`alarm` / :meth:`short`).
    """

    # ISPF's default long-message field width when a <msgmbr> declares none;
    # the DTL Guide documents WIDTH=76|68 with 76 as the default.
    DEFAULT_WIDTH = 76

    def __init__(self, messages: dict, attrs: dict = None, width: int = None,
                 ccsid: int = None):
        self.messages = messages
        self.attrs = attrs or {}
        self.width = width          # <msgmbr width=>, or None
        self.ccsid = ccsid          # <msgmbr ccsid=> (metadata), or None

    def format(self, msgid: str, **subs) -> str:
        text = self.messages.get(msgid.upper())
        if text is None:
            return msgid
        return f"{msgid} {_substitute(text, subs)}".rstrip()

    def alarm(self, msgid: str) -> bool:
        """Whether displaying this message should sound the terminal alarm
        (<msg alarm=> / its MSGTYPE default). Unknown ids don't alarm."""
        return bool(self.attrs.get(msgid.upper(), {}).get("alarm"))

    def msgtype(self, msgid: str):
        """The <msg msgtype=> (info|warning|action|critical) lower-cased, or
        None. Carried so a caller can colour/badge a message by severity."""
        return self.attrs.get(msgid.upper(), {}).get("msgtype")

    def short(self, msgid: str, **subs) -> str:
        """The short-message text (<msg smsg=>) if present, else the long form."""
        smsg = self.attrs.get(msgid.upper(), {}).get("smsg")
        if smsg is None:
            return self.format(msgid, **subs)
        return _substitute(smsg, subs)

    def help(self, msgid: str):
        """The help-panel name a user reaches (PF1) while this message shows
        (<msg help=>), or None. Lets the server offer message-specific help,
        the way ISPF routes HELP on a displayed message to its help panel."""
        return self.attrs.get(msgid.upper(), {}).get("help")

    def location(self, msgid: str):
        """Where the dialog shows this message (<msg LOCATION=AREA|MODAL|MODELESS>),
        lower-cased, or None. Recorded so the server can place the message in a
        message area or a pop-up window (#127)."""
        return self.attrs.get(msgid.upper(), {}).get("location")

    def lines(self, msgid: str, **subs):
        """The (substituted) long message text word-wrapped to the member's
        WIDTH (<msgmbr width=>, else the ISPF default 76), as a list of display
        lines. Honours WIDTH the way DTL formats a long message that overflows
        its field; a message within the width stays a single line. FORMAT=ASIS
        (<msg format=asis>) instead keeps the message's authored line breaks."""
        text = self.messages.get(msgid.upper())
        if text is None:
            return [msgid]
        text = _substitute(text, subs)
        width = self.width or self.DEFAULT_WIDTH
        # FORMAT=ASIS: preserve the authored line breaks (each source line is a
        # display line), rather than reflowing the words to WIDTH.
        if self.attrs.get(msgid.upper(), {}).get("format") == "asis":
            return [ln.rstrip() for ln in text.splitlines()] or [""]
        words, out, cur = text.split(), [], ""
        for w in words:
            if not cur:
                cur = w
            elif len(cur) + 1 + len(w) <= width:
                cur += " " + w
            else:
                out.append(cur)
                cur = w
        if cur:
            out.append(cur)
        return out or [""]


def load_messages(source: str) -> MessageCatalog:
    """Parse a DTL message member (``<msgmbr>``/``<msg>``) into a catalog.

    The message text is left unsubstituted here; ``&NAME`` references are
    resolved per-message at display time by :meth:`MessageCatalog.format`.
    """
    parser = _DTLParser()
    parser.feed(source)
    parser.close()
    return MessageCatalog(parser.messages, parser._msg_attrs,
                          parser._msgmbr_width, parser._msgmbr_ccsid)


def load_message_member(name: str, directory: str = None) -> MessageCatalog:
    """Load and parse ``<directory>/<name>.dtl`` as a message member.

    ``directory`` defaults to the ``messages`` folder next to this module —
    a small nod to ISPF keeping messages (ISPMLIB) apart from panels (ISPPLIB).
    """
    import os
    if directory is None:
        directory = os.path.join(os.path.dirname(os.path.abspath(__file__)), "messages")
    path = os.path.join(directory, f"{name}.dtl")
    with open(path, "r", encoding="utf-8") as fh:
        return load_messages(fh.read())
