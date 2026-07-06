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

from screen import Screen, Text, Field, DisplayIntensity, Color, Highlight

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
_TEXT_TAGS = ("info",) + _INSTRUCTION_TAGS + _FLOW_TEXT_TAGS
# ISPDTLC inserts a blank line BEFORE a flowed paragraph or panel instruction (and
# before a bottom instruction, which we anchor separately); COMPACT suppresses it.
# A TOPINST instead gets a blank line AFTER it. See the P/TOPINST tag references.
_BLANK_BEFORE_TAGS = ("p", "pnlinst")
_CONTENT_TAGS = _TEXT_TAGS + ("dtafld", "cmdarea", "choice", "figcap",
                              "dthd", "ddhd", "dldiv", "pldiv", "textseg")
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
        self._hp = None           # the open <hp>'s (color, highlight), or None
        self._selfld = None       # active <selfld> layout state, or None
        self._in_dtafldd = False  # capturing a <dtafldd> prompt child?
        self._dtafldd = None      # captured <dtafldd> prompt text, or None
        self._keylist = None      # active <keyl> bindings dict, or None
        self._varclasses = {}     # <varclass> name (upper) → {"numeric", "checks", "msg"}
        self._vardcls = {}        # <vardcl> name (upper) → {"varclass": name}
        self._cur_varclass = None # name of the <varclass> currently being defined
        self._checkl = None       # active <checkl> {"msg", "checks"} or None
        self._in_varlist = False  # inside a <varlist>?
        self._in_msgmbr = False   # inside a <msgmbr>?
        self._msgmbr_name = ""    # current <msgmbr name=...> (for <msg suffix>)
        self._msgmbr_width = None  # <msgmbr width=...>, or None
        self.messages = {}        # <msg> msgid (upper) → message text
        self._msg_attrs = {}      # <msg> msgid (upper) → {alarm, msgtype, smsg}
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
        self._skip = None         # inside a non-rendering block [tag, chars, attrs]
                                  # — <comment>/<copyr>/<compopt>/<source>
        self._title_item = None   # the centered title Text (retracted on collision)
        self._title_rule = None   # the action-bar separator rule (retracted on collision)
        self._titline = True      # <panel titline=no> suppresses the on-screen title line
        self._panel_cursor = None # <panel cursor=field-name> places the cursor at that field
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

    # ── colour / highlight attributes ────────────────────────────────────────

    def _color(self, a):
        """The Color for a tag's COLOR= attribute (honouring %var), or None."""
        return _resolve_color(a.get("color"), self._subs)

    def _hilite(self, a):
        """The Highlight for a tag's HILITE= attribute, or None."""
        return _HIGHLIGHTS.get(str(a.get("hilite", "")).strip().lower())

    # ── inline <hp> (highlighted phrase) mixed content ───────────────────────

    @staticmethod
    def _message_attrs(a) -> dict:
        """Presentation attributes of a <msg>. ALARM defaults from MSGTYPE:
        WARNING/ACTION/CRITICAL messages sound the alarm, INFO does not (an
        explicit ALARM=YES/NO overrides). SMSG is the short-message text."""
        msgtype = str(a.get("msgtype", "")).strip().lower()
        if "alarm" in a:
            alarm = _truthy(a.get("alarm"))
        else:
            alarm = msgtype in ("warning", "action", "critical")
        return {"alarm": alarm, "msgtype": msgtype or None, "smsg": a.get("smsg")}

    def _hp_hilite(self, a):
        """The Highlight for an <hp> phrase: its HILITE= or the DTL TYPE= (both
        mapped through the highlight table), or None."""
        return (self._hilite(a)
                or _HIGHLIGHTS.get(str(a.get("type", "")).strip().lower()))

    def _begin_hp(self, a):
        """Start an inline <hp> run: bank the text captured so far as a plain run,
        then capture the phrase as an emphasised run. The enclosing text element
        becomes a mixed-content Text.rich field (see _finalize_runs)."""
        if self._runs is None:
            self._runs = []
        self._runs.append(("".join(self._chars), None, None))
        self._chars = []
        self._hp = (self._color(a), self._hp_hilite(a))

    def _end_hp(self):
        """Close the open <hp>: bank its text as an emphasised run."""
        color, hilite = self._hp
        self._runs.append(("".join(self._chars), color, hilite))
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
            self._runs.append(("".join(self._chars), None, None))
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
        if tag in ("comment", "copyr", "compopt", "source"):
            # Non-rendering blocks: <comment>/<copyr>/<compopt> are dropped;
            # <source> ()INIT/)PROC logic renders nothing but its raw text is kept
            # for the ZSEL selection routing (see _close_skip).
            self._skip = [tag, [], a]
            return
        # An inline <hp> (highlighted phrase) inside a text element does NOT close
        # it — it emphasises a phrase *within* one field. Bank the runs and return
        # before the implicit-flush below (see _begin_hp / _finalize_runs). A <rp>
        # (reference phrase — a hypertext link to another help panel) is the same
        # kind of inline emphasis; with no explicit emphasis it renders underlined,
        # the CUA point-and-shoot link style.
        if tag in ("hp", "rp") and self._tag in _TEXT_TAGS:
            self._begin_hp(a)
            if tag == "rp" and self._hp == (None, None):
                self._hp = (None, Highlight.UNDERSCORE)
            return
        # <varsub var=NAME> substitutes a dialog variable inside message text: emit
        # an ISPF ``&NAME.`` reference into the text being captured, resolved at
        # display time (MessageCatalog.format) exactly like a literal &NAME would be.
        if tag == "varsub":
            var = a.get("var")
            if var:
                self.handle_data(f"&{var}.")
            return
        # Implicit end tags: a new block element closes the open content element
        # (DTL omits most end tags). <dtafldd> (a field's prompt/description) and
        # <lit> (a literal run inside e.g. an <xlati> external) are exceptions —
        # they are inline children that must not close their parent.
        if tag not in ("dtafldd", "lit") and self._tag is not None:
            self._emit_current()
        if tag in ("ul", "ol", "sl"):
            # <ul>/<ol> mark each item with a bullet/number; <sl> (simple list)
            # indents its items with no marker (see _emit_listitem).
            self._lists.append({"type": tag, "n": 0})
        elif tag == "notel":
            # A note list: a "Notes:" heading (TEXT= override, INTENS/COLOR/HILITE
            # style it), a blank line, then NUMBERED <li> items (1. 2. …).
            ctx = self._areas[-1] if self._areas else None
            if ctx is not None:
                heading = (a.get("text") or "Notes:").strip()
                indent = self._opt_int(a.get("indent"), 0)
                self.screen.add(Text(ctx["row"], ctx["col"] + indent, heading,
                                     _intensity(a, "intens"), color=self._color(a),
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
            self._lists.append({
                "type": tag, "n": 0,
                "tsize": int(a["tsize"]) if "tsize" in a else self._DL_TSIZE,
                "break": a.get("break", "none").lower(),
                "compact": _bool_attr(a, "compact"),  # no blank after a <ddhd> header
                "indent": self._opt_int(a.get("indent"), 0),  # shift the list right
                # FORMAT positions the DT term within its TSIZE column.
                "format": str(a.get("format", "start")).strip().lower(),
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
        if tag in ("panel", "help"):
            # A <panel PANDEF=id> inherits the named <pandef>'s defaults — the
            # panel's own attributes win (setdefault fills only what it omits).
            pd = self._pandefs.get(str(a.get("pandef", "")).strip().lower())
            if pd:
                for k, v in pd.items():
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
            # The panel itself is the root flow box: every element flows down
            # from the top.
            self._areas.append(
                {"row": 0, "col": 1, "fldgap": 1, "explicit": True, "parent": None}
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
                # The field-prompt text (between <selfld ...> and the first
                # <choice>) — a caption above the list (PMTLOC=ABOVE, default) or
                # beside it (PMTLOC=BEFORE). Captured here, emitted before the first
                # choice. Empty (the bundled numbered menus) → nothing rendered.
                "origin": origin,
                "pmtloc": str(a.get("pmtloc", "above")).strip().lower(),
                "pmtwidth": self._opt_int(a.get("pmtwidth")),
                "selwidth": self._opt_int(a.get("selwidth")),
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
            self._keylist = {}
        elif tag == "keyi":
            self._emit_keyi(a)
        elif tag == "cmdtbl":
            self._in_cmdtbl = True
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
            self._ab = {"row": 0, "col": 1, "gap": 3, "choices": []}
        elif tag == "abc":
            if self._ab is None:
                raise DTLError("<abc> outside of an <ab>")
            self._end_abc()                     # implicit end of a previous <abc>
            self._cur_abc = {"chars": [], "pdc": [], "help": self._field_help(a)}
        elif tag == "pdc":
            if self._cur_abc is None:
                raise DTLError("<pdc> outside of an <abc>")
            self._end_pdc()                     # implicit end of a previous <pdc>
            self._cur_pdc = {"chars": [], "action": "",
                             "help": self._field_help(a)}
        elif tag == "action":
            # A pull-down choice's command: <pdc>label<action run=cmd>. RUN= is
            # the standard DTL attribute naming the command the choice runs.
            if self._cur_pdc is not None:
                self._cur_pdc["action"] = a.get("run") or self._cur_pdc["action"]
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
            }
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
                "fldgap": parent["fldgap"] if parent else 1,
                "dir": str(a.get("dir", "vert")).strip().lower(),
                "start_idx": len(self.screen.items),
                "explicit": False,
                "parent": parent,
                "pmtwidth": (self._opt_int(a["pmtwidth"]) if "pmtwidth" in a
                             else (parent.get("pmtwidth") if parent else None)),
                "entwidth": (self._opt_int(a["entwidth"]) if "entwidth" in a
                             else (parent.get("entwidth") if parent else None)),
                # PAD/PADC default the column's <dtafld> fill character; a field's
                # own PAD/PADC overrides it (see _add_field).
                "pad": self._pad_char(a) or (parent.get("pad") if parent else None),
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
                if ctx is not None:
                    ctx["row"] = row + 1
                # TYPE=NONE/BLANK is a blank spacer (consumes the row but draws no
                # rule); SOLID (the default) / DASH draw a horizontal rule.
                if str(a.get("type", "solid")).strip().lower() not in ("none", "blank"):
                    if ctx is not None and ctx.get("width"):
                        width = ctx["width"]          # span the box's fixed width
                    else:
                        width = max(1, self.screen.width - col - 1)
                    # A CUA rule (role=rule → blue on a colour terminal, mono
                    # unchanged). The standard replacement for the non-standard
                    # <info fill=->.
                    self.screen.add(Text(row, col, "-" * width, role="rule"))
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
            row = parent["row"] if parent else 0
            self._areas.append({
                "row": row, "row0": row, "maxbottom": row,
                "col": base_col + (int(a["indent"]) if "indent" in a else 0),
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
                # A box that transparently continues the parent's flow inherits its
                # content state, so the first paragraph below a panel title still
                # gets the CUA title/body separator. An explicitly-positioned box
                # starts fresh.
                "had_content": bool(parent and not explicit
                                    and parent.get("had_content")),
            })
        elif tag == "fig":
            # A figure: a flow sub-box, optionally framed by a horizontal rule
            # (FRAME=RULE, the default) above and below its content, with a
            # <figcap> caption line beneath. Its children (<p>, lists, <xmp>, …)
            # flow through the box like an <area>.
            parent = self._areas[-1] if self._areas else None
            col = parent["col"] if parent else 1
            row = parent["row"] if parent else 0
            frame = str(a.get("frame", "rule")).strip().lower() != "none"
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
        elif self._da is not None:
            self._da["body"].append(data)
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
        # Closing an inline <hp>/<rp> banks its emphasised run and keeps the
        # enclosing text element open (it is not a block child).
        if tag in ("hp", "rp") and self._runs is not None:
            self._end_hp()
            return
        # <varsub> is an empty tag: its text was injected on the start tag, so a
        # (rare) explicit </varsub> is a no-op — return before the implicit flush
        # below, which would otherwise prematurely close the enclosing <msg>.
        if tag == "varsub":
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
            self._lists.pop()
        if tag in ("panel", "help"):
            if self._da is not None:      # a <da> with an omitted end tag
                self._emit_da()
                self._da = None
            while self._areas and self._areas[-1].get("fig"):
                self._close_fig()         # a <fig> whose </fig> was omitted
            self._retract_title_if_collision()
            self._areas.clear()  # drop the panel's implicit flow box
            self._info_indent = 0
            return
        if tag == "selfld":
            # Advance the enclosing flow past the choices just laid out.
            sf = self._selfld
            if sf:
                self._emit_selfld_prompt(sf)   # a prompt-only selfld still shows it
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
        if tag == "keyl":
            self.screen.keylist = self._keylist or {}
            self._keylist = None
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
        self._retract_title_if_collision()
        self._place_panel_cursor()

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
        elif tag in ("dthd", "ddhd"):
            self._emit_defhead(tag, a, content)
        elif tag in ("dldiv", "pldiv"):
            self._emit_listdiv(a, content)
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
        self._tag, self._attrs, self._chars = None, None, []
        self._runs, self._hp = None, None
        self._dtafldd, self._in_dtafldd = None, False

    def handle_startendtag(self, tag, attrs):
        # Self-closing form, e.g. <dtafld .../> or <divider/>
        self.handle_starttag(tag, attrs)
        if tag in _CONTENT_TAGS:
            self.handle_endtag(tag)
        elif tag in ("ul", "ol", "sl", "dl", "parml", "notel"):  # empty list; pop it
            self.handle_endtag(tag)
        elif tag == "dtafldd":  # empty prompt
            self.handle_endtag(tag)
        elif tag == "selfld":  # a self-closing selfld has no choices; close it
            self._selfld = None
        elif tag == "keyl":  # a self-closing keylist has no items; close it
            self.handle_endtag(tag)
        elif tag == "msg":  # a self-closing msg has empty text
            self.handle_endtag(tag)
        elif tag == "checki":  # a self-closing checki carries params in attrs
            self.handle_endtag(tag)
        elif tag == "xlati":  # a self-closing xlati (no external text)
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
        if row >= self.screen.depth:
            # An auto-flowed element ran past the panel bottom (a tall panel plus
            # our block spacing); clamp to the last row rather than abort the panel,
            # as the column clamp below does for the horizontal overflow.
            row = self.screen.depth - 1
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
            rule = Text(1, 0, "-" * max(1, self.screen.width - 1))
            self.screen.add(rule)
            self._title_rule = rule
            title_row, flow_row = 2, 4    # bar(0), rule(1), title(2), blank(3), body(4)
        else:
            title_row, flow_row = 0, 1
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
        if str(a.get("depth", "")).strip() == "*":     # remaining panel depth
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
                         role=None, intensity=DisplayIntensity.NORMAL):
        """Word-wrap ``text`` and emit it as protected lines from ``row`` at
        ``col`` (hanging indent for continuations). Optionally place a ``marker``
        (bullet/number) on the first line. Advances the flow cursor."""
        lines = self._wrap(text, max(1, self.screen.width - (col + 1)))
        if marker is not None:
            self.screen.add(Text(row, marker_col, marker, DisplayIntensity.NORMAL,
                                 role=role))
        for i, ln in enumerate(lines):
            self.screen.add(Text(row + i, col, ln, intensity, role=role))
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

    def _emit_listitem(self, a, content, runs=None):
        """Emit one <li>: a depth-based bullet/number plus the item text, flowed,
        word-wrapped with a hanging indent, one level deeper per nested list. An
        inline <hp> phrase banks its text into ``runs``, leaving ``content`` empty —
        use the runs' concatenation so the item is not dropped."""
        if runs is not None:
            content = "".join(t for t, _, _ in runs)
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
        bullet_col = base + (depth - 1) * self._LIST_INDENT
        lst = self._lists[-1] if self._lists else None
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
        # Find the enclosing definition list (it carries tsize/break/pending).
        dl = next((ln for ln in reversed(self._lists)
                   if ln["type"] in ("dl", "parml")), None)
        tsize = dl["tsize"] if dl else self._DL_TSIZE
        brk = dl["break"] if dl else "none"
        depth = max(len(self._lists), 1)
        row, col, ctx = self._resolve_pos(a, tag)  # advances the flow one line
        base = col + (depth - 1) * self._LIST_INDENT + (dl["indent"] if dl else 0)
        if tag in ("dt", "pt"):
            # FORMAT positions the term within its TSIZE column (START left, the
            # default; CENTER centred; END right). A term wider than TSIZE gets no
            # offset (it spills into the description area, per BREAK).
            fmt = dl["format"] if dl else "start"
            term_col = base + self._fmt_offset(len(text), tsize, fmt)
            self.screen.add(Text(row, term_col, text, _intensity(a)))
            # Decide where this term's description goes. With break=none/fit a
            # short term shares its line; rewind the flow cursor so the next
            # <dd> lands on the same row.
            same_line = brk != "all" and len(text) < tsize
            if same_line and ctx is not None:
                ctx["row"] = row
            if dl is not None:
                dl["pending"] = {"desc_col": base + tsize}
            return
        # Description.
        desc_col = dl["pending"]["desc_col"] if dl and dl["pending"] else base + tsize
        if dl is not None:
            dl["pending"] = None
        self._emit_flow_lines(text, row, desc_col, ctx)

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
        tsize = dl["tsize"] if dl else self._DL_TSIZE
        depth = max(len(self._lists), 1)
        row, col, ctx = self._resolve_pos(a, tag)   # advances the flow one line
        base = col + (depth - 1) * self._LIST_INDENT + (dl["indent"] if dl else 0)
        if tag == "dthd":
            self.screen.add(Text(row, base, text, _intensity(a), role="heading"))
            # The paired <ddhd> shares this row (rewind, like a <dt>'s <dd>).
            if ctx is not None:
                ctx["row"] = row
            if dl is not None:
                dl["pending"] = {"desc_col": base + tsize}
            return
        # <ddhd>: the description-column heading, then a blank line before the
        # items (COMPACT on the <dl> suppresses that blank).
        desc_col = dl["pending"]["desc_col"] if dl and dl["pending"] else base + tsize
        if dl is not None:
            dl["pending"] = None
        self.screen.add(Text(row, desc_col, text, _intensity(a), role="heading"))
        if ctx is not None and not (dl and dl.get("compact")):
            ctx["row"] = row + 2                     # heading row + one blank line

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
        if _bool_attr(a, "gap"):                         # 1-char gap at each end
            start, span = col + 1, max(1, span - 2)
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
        row = self._emit_lstfld_rows(cols, row)
        # ISPF puts a "ROW x TO y OF z" scroll status on the title line's right —
        # but only if that region is free (a bundled panel's full-width title rule
        # occupies it and carries its own scroll footer).
        if self._rows:
            status = f"ROW 1 TO {fld.get('shown', 0)} OF {len(self._rows)}"
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
        for entry in data:
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
                                         help=c.get("help")))
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
        # When the end of the table is on screen (rows supplied, not clipped by a
        # deeper page), ISPF draws a "BOTTOM OF DATA" line spanning the table.
        if self._rows is not None and not clipped and cols \
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

    def _skip_blank_before(self, a):
        """ISPDTLC block spacing: insert a leading blank line before a flowed block
        element (paragraph, panel instruction, command area, selection field,
        definition list). Added only when the box already holds content (so the
        first block gets none) and the row above is not already blank (so an
        existing gap isn't doubled). An explicit ``row``, COMPACT, or NOSKIP
        suppresses it. Advances the flow row cursor."""
        ctx = self._areas[-1] if self._areas else None
        if (ctx is not None and "row" not in a and not _bool_attr(a, "compact")
                and not _bool_attr(a, "noskip")
                and ctx.get("had_content") and ctx["row"] >= 1
                and ctx["row"] + 1 < self.screen.depth   # don't push off the panel
                and self._row_occupied(ctx["row"] - 1)):
            ctx["row"] += 1

    def _emit_info(self, a, content, tag="info", runs=None):
        # ``runs`` (from inline <hp>) is a list of (text, color, highlight); the
        # concatenation is the field's plain text, so mono renders identically.
        if runs is not None and not content:
            content = "".join(t for t, _, _ in runs)
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
            runs = None                       # render as a plain emphasised Text
            emph = DisplayIntensity.HIGH
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
            self._skip_blank_before(a)
        row, col, ctx = self._resolve_pos(a, "info")
        if self._lists:
            # A paragraph inside a list aligns with the list's item text.
            col += len(self._lists) * self._LIST_INDENT
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
            # Flowed text with inline <hp>: keep each phrase's colour/highlight
            # across the word-wrap (SA runs per line), instead of dropping to plain.
            self._emit_flow_runs(runs, row, col, ctx, role)
            return
        self._emit_flow_lines(text, row, col, ctx, role=role, intensity=emph)
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
        row, col, ctx = self._resolve_pos(a, tag)
        if self._lists:
            col += len(self._lists) * self._LIST_INDENT
        col += self._opt_int(a.get("indent"), 0)
        heading = (a.get("text") or "Note:").strip()
        h_int = _intensity(a, "intens")
        h_col, h_hil = self._color(a), self._hilite(a)
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
        self._da["attrs"][ch] = {
            "type": str(a.get("type", "char")).strip().lower(),
            "color": self._color(a),
            "hilite": self._hilite(a),
            "intens": _intensity(a, "intens", DisplayIntensity.NORMAL),
            "padc": (a.get("padc") or " ")[:1],
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
        if da.get("ctx") is not None:      # advance the enclosing flow past it
            da["ctx"]["row"] = da["row"] + len(lines)

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
        if spec["type"] == "datain":
            # The run after the attribute char is the field's extent (pad-char
            # placeholders in the source), not initial data — so it sets the
            # width and the field starts empty.
            self.screen.add(Field(
                row=row, col=col, length=max(1, len(content)), default="",
                color=spec["color"], highlight=spec["hilite"],
            ))
        else:  # dataout / char / text → protected display field
            self.screen.add(Text(row, col, content, spec["intens"],
                                 color=spec["color"], highlight=spec["hilite"]))

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
        pmt_above = str(a.get("pmtloc", "")).strip().lower() == "above"
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
            a.get("pmtfmt")) if content else ""
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
            dw = str(a.get("deswidth", "")).strip()
            if dw.isdigit():
                desc = desc[: int(dw)]
            desc_col = fldcol + length + 2      # attr + data + terminator attr
            desc = desc[: max(0, self.screen.width - desc_col)]
            if desc:
                self.screen.add(Text(row, desc_col, desc, _intensity(a),
                                     role="prompt"))
        # USAGE=OUT is a display-only (output) field: show the variable's value as
        # protected text — like a list column — not an editable input box.
        if str(a.get("usage", "")).strip().lower() == "out":
            value = self._subs.get((name or "").upper()) or a.get("init", "")
            self.screen.add(Text(row, fldcol, str(value)[:length].ljust(length),
                                 _intensity(a), color=self._color(a), role="cell"))
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
            help=self._field_help(a),
            # PAD/PADC fill an empty entry; a field's own PAD wins over the
            # enclosing <dtacol>'s default (None → the conventional space fill).
            pad=self._pad_char(a) or (ctx.get("pad") if ctx else None),
        )
        self.screen.add(field)
        self._attach_validation(name, a)
        return field

    @staticmethod
    def _field_help(a):
        """The field-level help *panel name* from HELP=, or None. HELP can also be
        NO/YES, a *message id, or a %varname — none of which name a help panel, so
        those aren't field help here."""
        h = str(a.get("help", "")).strip()
        if not h or h.lower() in ("no", "yes") or h.startswith(("*", "%")):
            return None
        return h

    def _attach_validation(self, name, a):
        """Attach a field's validation to the Screen: its variable-class <checkl>
        checks and/or IBM's REQUIRED=YES (the field must be non-empty on submit)."""
        if not name:
            return
        decl = self._vardcls.get(name.upper())
        vc = (self._varclasses.get(str(decl.get("varclass", "")).upper())
              if decl else None)
        checks = vc["checks"] if (vc and vc.get("checks")) else []
        required = _bool_attr(a, "required")
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

    def _emit_varclass(self, a):
        name = a.get("name")
        if not name:
            raise DTLError("<varclass> missing required attribute 'name'")
        # DTL TYPE is a kind plus (for CHAR/NUMERIC) a size, e.g. "char 8" or
        # "numeric 5". We derive numeric-vs-not and enforce the size: CHAR caps the
        # input length, NUMERIC caps the number of digits. Other kinds (DBCS, date
        # /time, VMASK, …) are recognised but not enforced (#129).
        parts = str(a.get("type", "char")).strip().lower().split()
        kind = parts[0] if parts else "char"
        size = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        numeric = kind in ("numeric", "num")
        checks = []
        if size is not None:
            if numeric:
                checks.append({"type": "maxdigits", "max": size})
            elif kind == "char":
                checks.append({"type": "maxlen", "max": size})
        self._cur_varclass = name.upper()
        self._varclasses[self._cur_varclass] = {
            "numeric": numeric,
            "checks": checks,
            "msg": a.get("msg"),          # class-level MSG (IBM's attribute name)
        }

    def _emit_checki(self, a, content):
        """A <checki> validity-check item. The value list / range may be given as
        element text (``v1 v2 …`` / ``min max``) or — the form the guide uses — via
        attributes: ``type=values parm1=EQ|NE parm2='v1 v2'`` (EQ = must be one of;
        NE = must not be). ``alpha`` / ``name`` are character-class checks. Other
        authentic types (``picture`` …) stay lenient (recognised but unenforced),
        so a panel using them still loads and renders."""
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
        elif ctype == "name":
            self._checkl["checks"].append({"type": "name"})

    def _emit_xlati(self, a, content):
        """One ``<xlati value=internal>external`` translation item. The external
        (the form the user types and sees) is the value; the element text — which
        may be a ``<lit>`` literal run — supplies it."""
        external = " ".join(content.split())
        if external:
            self._xlatl["items"].append(external)

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
        field = self._add_field(a, content, "cmdarea", a.get("datavar", "ZCMD"))
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

    def _emit_choice(self, a, content):
        sf = self._selfld
        if sf is None:
            raise DTLError("<choice> outside of a <selfld>")
        self._emit_selfld_prompt(sf)
        # HIDE/HIDEX conditionally remove the choice from the list (dynamic panels
        # show a variable subset). A hidden choice renders nothing, consumes no
        # row, and isn't selectable — the choices below it move up.
        if self._choice_hidden(a):
            return
        row = sf["row"]
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
        num = str(sf["count"] + 1) if auto_num else (disp or "")
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
                row=row, col=sf["numcol"], length=1,
                name=(a.get("name") or f'{sf["name"]}{sf["count"]}') or None,
                color=explicit, role="field",
            )
            self.screen.add(mark)
        else:
            if sf.get("auto_single") and sf["count"] == 0:
                # A standard single-choice field has one selection input field
                # before the first choice; the user types the chosen number into
                # it. Its name is the SELFLD's NAME (per the CHOICE reference).
                self.screen.add(Field(
                    row=row, col=sf["inputcol"], length=sf["entwidth"],
                    name=sf["name"] or None, color=explicit, role="field"))
            # SINGLE choices are numbered "N." (number + period); a MENU/explicit
            # field uses the bare number padded to numwidth.
            num_text = num + "." if sf.get("period") else num.ljust(sf["numwidth"])
            self.screen.add(Text(row, sf["numcol"], num_text,
                                 num_int, color=explicit, role=rnum))
        # A MULTI choice's NAME is the field identifier (used to read the mark
        # back), not display text — the row is just the mark + description. A
        # single/menu choice shows its keyword.
        name = a.get("name", "")
        show_name = bool(name) and not sf.get("multi")
        if show_name:
            self.screen.add(Text(row, sf["namecol"], name, color=explicit, role=rname))
        # Description column: a standard single-choice, or any choice with no
        # visible keyword (auto grid), hugs the number/mark; a keyworded grid uses
        # the far description column.
        if sf.get("auto_single"):
            desccol = sf["desccol"]
        elif sf.get("auto_cols") and not show_name:
            desccol = sf["namecol"]
        else:
            desccol = sf["desccol"]
        self.screen.add(Text(row, desccol, content.rstrip(), color=explicit, role=rdesc))
        sf["row"] = row + 1
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
                     "addr": mark.data_addr}
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
                self.screen.cursor_at = (row, mark.col if mark is not None else sf["namecol"])

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
            self._cur_abc["pdc"].append({
                "label": label, "action": self._cur_pdc["action"], "mnemonic": mnem,
                "help": self._cur_pdc.get("help"),
            })
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
            self._ab["choices"].append({
                "label": label, "pdc": self._cur_abc["pdc"], "mnemonic": mnem,
                "help": self._cur_abc.get("help"),
            })
        self._cur_abc = None

    def _emit_action_bar(self, ab):
        """Lay the action-bar choice labels out across the bar's row (high
        intensity) and record the choices + their pull-downs on the Screen. Each
        choice keeps its ``row``/``col`` so the server can map a cursor onto it
        for point-and-shoot."""
        col = ab["col"]
        for choice in ab["choices"]:
            label = choice["label"]
            choice["row"], choice["col"] = ab["row"], col
            m = choice.get("mnemonic")
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
            col += len(label) + ab["gap"]
        self.screen.action_bar = ab["choices"]

    def _finalize_cmd_trunc(self):
        """Apply a captured <t> truncation point to the current <cmd>: the number
        of characters before it is the command's minimum abbreviation (``trunc``).
        A <t> wins over any TRUNC= attribute; with no <t> the command is unchanged."""
        if self._cur_cmd is not None and self._cmd_tpos is not None:
            self._cur_cmd["trunc"] = self._cmd_tpos

    def _emit_keyi(self, a):
        if self._keylist is None:
            raise DTLError("<keyi> outside of a <keyl>")
        key = a.get("key")
        if not key:
            raise DTLError("<keyi> missing required attribute 'key'")
        # ``cmd`` is the command the key invokes (ISPF allows ``action`` too);
        # both key and command are case-insensitive, stored uppercase.
        cmd = a.get("cmd", a.get("action", ""))
        self._keylist[key.upper()] = cmd.upper()


def load_dtl(source: str, rows=None, screen_rows=None, screen_cols=None,
             **subs) -> Screen:
    """Parse DTL markup into a :class:`screen.Screen`.

    ``subs`` provides values for ``&NAME`` dialog-variable references in the
    source (e.g. ``ZUSER``, ``ZTIME``) before parsing. ``rows`` populates a
    ``<lstfld>`` list/table: a sequence of ``{datavar: value}`` mappings, one
    per model row (when omitted, a single empty model row is laid out).
    ``screen_rows``/``screen_cols`` override the panel's presentation size (so a
    list panel can lay out more rows on a larger alternate screen).
    """
    source = _resolve_entities(source)
    source = _substitute(source, subs)
    parser = _DTLParser()
    parser._rows = rows
    parser._subs = {k.upper(): v for k, v in (subs or {}).items()}
    parser._override_rows = screen_rows
    parser._override_cols = screen_cols
    parser.feed(source)
    parser.close()
    return parser.screen


def load_panel(name: str, directory: str = None, rows=None,
               screen_rows=None, screen_cols=None, **subs) -> Screen:
    """Load and parse ``<directory>/<name>.dtl``.

    ``directory`` defaults to the ``panels`` folder next to this module, so the
    panels resolve regardless of the process's current working directory.
    ``rows`` populates a ``<lstfld>`` list/table (see :func:`load_dtl`);
    ``screen_rows``/``screen_cols`` override the presentation size.
    """
    import os
    if directory is None:
        directory = os.path.join(os.path.dirname(os.path.abspath(__file__)), "panels")
    path = os.path.join(directory, f"{name}.dtl")
    with open(path, "r", encoding="utf-8") as fh:
        return load_dtl(fh.read(), rows=rows, screen_rows=screen_rows,
                        screen_cols=screen_cols, **subs)


class MessageCatalog:
    """Messages parsed from a DTL ``<msgmbr>``, looked up by id.

    Mirrors how ISPF keeps messages in a message library (ISPMLIB), separate
    from panels: :meth:`format` returns the displayable ``"<id> <text>"`` with
    any ``&NAME`` references in the text substituted at display time. Each message
    also carries presentation attributes (see :meth:`alarm` / :meth:`short`).
    """

    def __init__(self, messages: dict, attrs: dict = None, width: int = None):
        self.messages = messages
        self.attrs = attrs or {}
        self.width = width          # <msgmbr width=>, or None

    def format(self, msgid: str, **subs) -> str:
        text = self.messages.get(msgid.upper())
        if text is None:
            return msgid
        return f"{msgid} {_substitute(text, subs)}".rstrip()

    def alarm(self, msgid: str) -> bool:
        """Whether displaying this message should sound the terminal alarm
        (<msg alarm=> / its MSGTYPE default). Unknown ids don't alarm."""
        return bool(self.attrs.get(msgid.upper(), {}).get("alarm"))

    def short(self, msgid: str, **subs) -> str:
        """The short-message text (<msg smsg=>) if present, else the long form."""
        smsg = self.attrs.get(msgid.upper(), {}).get("smsg")
        if smsg is None:
            return self.format(msgid, **subs)
        return _substitute(smsg, subs)


def load_messages(source: str) -> MessageCatalog:
    """Parse a DTL message member (``<msgmbr>``/``<msg>``) into a catalog.

    The message text is left unsubstituted here; ``&NAME`` references are
    resolved per-message at display time by :meth:`MessageCatalog.format`.
    """
    parser = _DTLParser()
    parser.feed(source)
    parser.close()
    return MessageCatalog(parser.messages, parser._msg_attrs, parser._msgmbr_width)


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
