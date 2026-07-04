"""A pragmatic subset of IBM's Dialog Tag Language (DTL) for defining screens.

DTL is IBM's real, ISO-SGML-based markup for ISPF panels — on z/OS you write
tagged source and run it through the ``ISPDTLC`` converter to produce panels,
messages, command tables, and keylists. This module is a small, self-contained
take on the same idea: ``load_dtl(source)`` parses DTL markup into a
:class:`screen.Screen`, which then renders to a 3270 data stream.

Relationship to authentic DTL
-----------------------------
We keep DTL's tag *names* and spirit. Positioning works both ways: every visible
element may carry explicit ``row``/``col`` (predictable, and how the bundled
panels are written), but a ``<panel>`` is also an implicit **flow box** — an
element that omits ``row``/``col`` flows down from the top (and a ``<dtafld>``
that omits ``fldcol`` gets its entry after the prompt). Explicit positions always
win, so fully-positioned panels are unaffected. This is a pragmatic take on
``ISPDTLC``'s auto-layout (the genuinely hard part); the bundled panels position
explicitly, while the conformance corpus (``tests/dtl_examples/``) exercises flow.

Like real DTL the source is SGML: files may begin with a ``<!DOCTYPE DM SYSTEM>``
prolog (tolerated and ignored), tag and attribute names are case-insensitive
(``<PANEL>`` == ``<panel>``), and boolean attributes may be minimized
(``<dtafld hidden>`` means ``hidden="yes"``).

Supported tags
--------------
``<panel name title help          root container. ``title`` → ``Screen.title``;
   width depth>``                 ``help`` names a help panel; ``width``/``depth``
                                 give the presentation-space size (default 80x24)
                                 and bound element positions at load time.
``<area row col fldgap>``        a flow box: contained elements that omit ``row``
``<region row col fldgap dir>``  flow down from this origin (one line each), and
                                 those that omit ``col`` use it. A field that omits
                                 ``fldcol`` gets its entry after the prompt
                                 (``col + len(prompt) + fldgap``). ``dir=horiz`` lays
                                 the box's children side by side instead of stacking
                                 them, and the enclosing flow resumes below the
                                 tallest column. Explicit positions always win, so
                                 non-flowed panels are unaffected.
``<info row col intensity>``     protected text (label / instruction / rule).
                                 ``fill`` + ``width`` repeats a character (rules).
``<topinst row col>``            top / panel / bottom instruction text. Render like
``<pnlinst row col>``            ``<info>`` (protected text); semantic DTL tags.
``<botinst row col>``
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
``<dtafld row col fldcol         a prompt plus an input field at ``fldcol``. The
   datavar entwidth usage         prompt is the text of a nested ``<dtafldd>`` child
   pmtloc ...>``                  (authentic DTL) or the element's own text.
                                 ``usage=out`` makes it a protected display field
                                 (the variable's value); ``pmtloc=above`` puts the
                                 prompt on the line above. See attrs below.
``<dtafldd>prompt</dtafldd>``    data-field description: the prompt for its
                                 enclosing ``<dtafld>`` or ``<cmdarea>``.
``<cmdarea row col fldcol         the command area (ISPF "Option/Command ===>"
   entwidth ...>``                line). Renders like ``<dtafld>``; ``datavar``
                                 defaults to ``ZCMD`` and the field is recorded
                                 as ``Screen.command_field``.
``<selfld row numcol namecol     a list of menu choices; each ``<choice>`` is laid
   desccol numwidth>``           out on its own row, auto-incrementing.
``<choice num name match          one menu row: number, name, description. The
   checkvar unavail>desc``        selection value (``match``, default ``num``) is
                                 recorded in ``Screen.selections`` so the dialog can
                                 validate a typed option; ``checkvar`` lands the
                                 cursor on the current choice; ``unavail`` greys a
                                 choice out and makes it unselectable.
``<keyl name>``                  a keylist: a set of function-key bindings for
                                 the panel (rendered as nothing; pure metadata).
``<keyi key cmd>desc``           one key binding: function key ``key`` (e.g.
                                 ``PF3``) invokes command ``cmd`` (e.g. ``EXIT``).
``<cmdtbl applid>``              an application command table (metadata).
``<cmd name trunc>desc``         a command; ``trunc`` is the min chars to type.
``<cmdact action>``              the command's action (e.g. ``alias exit``,
                                 ``passthru``). Recorded in ``Screen.commands``.
``<ab row col gap>``             an action bar; its ``<abc>`` choice labels are
``<abc>label</abc>``             laid out across ``row``. Each ``<abc>`` holds
``<pdc action>label</pdc>``      ``<pdc>`` pull-down choices (kept in
                                 ``Screen.action_bar`` for future interaction).
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
``<varlist>``                    container for ``<vardcl>`` declarations.
``<vardcl name varclass>``       declares variable ``name`` to be of class
                                 ``varclass``; a field's ``numeric`` is inherited
                                 from it when the field omits ``numeric``.
``<msgmbr name>``                a message member: container for ``<msg>`` entries
                                 (parsed by :func:`load_messages`, not a panel).
``<msg msgid>text``              a message; ``&NAME`` references in ``text`` are
                                 substituted at display time. See `MessageCatalog`.

``<dtafld>`` attributes: ``datavar`` (field name sent back), ``entwidth`` (field
length), ``hidden`` (non-display, e.g. password), ``numeric``, ``default``,
``cursor`` (place the cursor here), ``mdt`` (default yes), ``intensity`` (prompt).

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
# (help panels) — the label prefixes the text. <notel> (below) is the list form.
_ADMONITIONS = {
    "note": "Note:", "nt": "Note:",           # note / inline note
    "attention": "Attention:", "caution": "Caution:", "warning": "Warning:",
}
# Block tags whose text flows as protected lines (like <info>): paragraphs,
# list items (<li>/<dt>/<dd>/<pt>/<pd>/<lp>), preformatted <lines>, and the
# admonitions above. Their list containers (<ul>/<ol>/<dl>/<parml>/<notel>) are
# transparent — ignored (a plain container), except <notel>'s "Notes:" heading.
_FLOW_TEXT_TAGS = ("p", "li", "dt", "dd", "pt", "pd", "lp", "lines") + tuple(_ADMONITIONS)
# Instruction tags render as protected text like <info>: <topinst> (top),
# <pnlinst> (panel), and <botinst> (bottom) instructions.
_INSTRUCTION_TAGS = ("topinst", "pnlinst", "botinst")
_TEXT_TAGS = ("info",) + _INSTRUCTION_TAGS + _FLOW_TEXT_TAGS
_CONTENT_TAGS = _TEXT_TAGS + ("dtafld", "cmdarea", "choice")
_FIELD_TAGS = ("dtafld", "cmdarea")


def _truthy(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in ("yes", "true", "1", "on")


def _bool_attr(attrs, key, default=False):
    """Read a boolean DTL attribute, honouring SGML attribute minimization.

    ``<dtafld hidden>`` (the attribute present with no value, ``html.parser``
    reports ``None``) and ``hidden="hidden"`` both mean true, as does any of
    yes/true/1/on. An absent attribute yields ``default``.
    """
    if key not in attrs:
        return default
    value = attrs[key]
    if value is None:
        return True
    return _truthy(value) or str(value).strip().lower() == key


def _intensity(attrs, key="intensity", default=DisplayIntensity.NORMAL):
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
        self._ab = None           # active <ab> action bar being built, or None
        self._cur_abc = None      # current <abc> action-bar choice, or None
        self._cur_pdc = None      # current <pdc> pull-down choice, or None
        self._panel_title = None  # capturing the panel's title text, or None
        self._lists = []          # stack of open <ul>/<ol> ({"type", "n"})
        self._lstfld = None       # active <lstfld> table {"cols", "groups", …}
        self._lstgrp = None       # current <lstgrp> column group, or None
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
        # The panel's title text (between <panel ...> and its first child) ends
        # at the first child tag.
        if self._panel_title is not None:
            self._finalize_panel_title()
        # An inline <hp> (highlighted phrase) inside a text element does NOT close
        # it — it emphasises a phrase *within* one field. Bank the runs and return
        # before the implicit-flush below (see _begin_hp / _finalize_runs).
        if tag == "hp" and self._tag in _TEXT_TAGS:
            self._begin_hp(a)
            return
        # Implicit end tags: a new block element closes the open content element
        # (DTL omits most end tags). <dtafldd> is the exception — it's a child
        # that supplies its parent <dtafld>'s prompt, so it must not close it.
        if tag != "dtafldd" and self._tag is not None:
            self._emit_current()
        if tag in ("ul", "ol"):
            self._lists.append({"type": tag, "n": 0})
        elif tag == "notel":
            # A note list: a "Notes:" heading, then bulleted <li> note items.
            ctx = self._areas[-1] if self._areas else None
            if ctx is not None:
                self._emit_flow_lines("Notes:", ctx["row"], ctx["col"], ctx)
            self._lists.append({"type": "ul", "n": 0})
        elif tag in ("dl", "parml"):
            # A definition/parameter list carries its term-column width (tsize)
            # and break style; <dt>/<dd> (<pt>/<pd>) entries lay out against it.
            self._lists.append({
                "type": tag, "n": 0,
                "tsize": int(a["tsize"]) if "tsize" in a else self._DL_TSIZE,
                "break": a.get("break", "none").lower(),
                "pending": None,
            })
        if tag in ("panel", "help"):
            # A top-level <help> is itself a (help) panel — same flow root.
            self.screen.title = a.get("title")
            self.screen.help = a.get("help")
            if self._override_cols is not None:
                self.screen.width = self._override_cols
            elif "width" in a:
                self.screen.width = int(a["width"])
            if self._override_rows is not None:
                self.screen.depth = self._override_rows
            elif "depth" in a:
                self.screen.depth = int(a["depth"])
            # The panel itself is an implicit flow box: elements that omit
            # row/col flow down from the top. Explicit positions still win, so
            # fully-positioned panels are unaffected.
            self._areas.append(
                {"row": 0, "col": 1, "fldgap": 1, "explicit": True, "parent": None}
            )
            self._panel_title = []  # capture the title text that follows
        elif tag == "selfld":
            ctx = self._areas[-1] if self._areas else None
            # NUMCOL/NAMECOL/DESCCOL are columns *within* the selection field, so
            # they are offsets from its origin column: an explicit COL, else the
            # enclosing flow box's column (so a flowed <selfld> — e.g. a dir=horiz
            # column — shifts with the box). At the base column 1 the offset is 0,
            # so panel-level selection fields render byte-for-byte as before.
            origin = int(a["col"]) if "col" in a else (ctx["col"] if ctx else 1)
            base = origin - 1
            self._selfld = {
                "row": int(a["row"]) if "row" in a else (ctx["row"] if ctx else 0),
                "numcol": base + int(a.get("numcol", 1)),
                "namecol": base + int(a.get("namecol", 4)),
                "desccol": base + int(a.get("desccol", 21)),
                "numwidth": int(a.get("numwidth", 2)),
                "numintensity": _intensity(a, "numintensity", DisplayIntensity.HIGH),
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
                "pmtwidth": int(a["pmtwidth"]) if "pmtwidth" in a else None,
                "selwidth": int(a["selwidth"]) if "selwidth" in a
                            and str(a["selwidth"]).strip().isdigit() else None,
                "prompt_chars": [],
                "prompt_done": False,
            }
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
            self._cur_cmd = {"action": "", "trunc": int(a.get("trunc", 0))}
            self.screen.commands[name.upper()] = self._cur_cmd
        elif tag == "cmdact":
            # The command action; read on start, since DTL often omits </cmdact>.
            if self._cur_cmd is not None:
                self._cur_cmd["action"] = a.get("action", "")
        elif tag == "ab":
            self._ab = {"row": int(a.get("row", 0)), "col": int(a.get("col", 1)),
                        "gap": int(a.get("gap", 3)), "choices": []}
        elif tag == "abc":
            if self._ab is None:
                raise DTLError("<abc> outside of an <ab>")
            self._end_abc()                     # implicit end of a previous <abc>
            self._cur_abc = {"chars": [], "pdc": [], "help": self._field_help(a)}
        elif tag == "pdc":
            if self._cur_abc is None:
                raise DTLError("<pdc> outside of an <abc>")
            self._end_pdc()                     # implicit end of a previous <pdc>
            self._cur_pdc = {"chars": [], "action": a.get("action", ""),
                             "help": self._field_help(a)}
        elif tag == "action":
            # A pull-down choice's action (alternative to <pdc action=...>).
            if self._cur_pdc is not None:
                self._cur_pdc["action"] = (
                    a.get("action") or a.get("run") or a.get("cmd") or self._cur_pdc["action"]
                )
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
                "row": int(a["row"]) if "row" in a else (ctx["row"] if ctx else 0),
                "col": int(a["col"]) if "col" in a else (ctx["col"] if ctx else 1),
            }
            self._lstgrp = None
        elif tag == "lstgrp":
            if self._lstfld is None:
                raise DTLError("<lstgrp> outside of a <lstfld>")
            hv = a.get("headline")
            headline = "headline" in a and (
                hv is None or str(hv).lower() in ("yes", "true", "1", "headline")
            )
            self._lstgrp = {"heading": "", "headline": headline}
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
                "row": int(a["row"]) if "row" in a else (ctx["row"] if ctx else 0),
                "col": int(a["col"]) if "col" in a else (ctx["col"] if ctx else 1),
                "attrs": {}, "body": [],
                "ctx": None if "row" in a else ctx,   # flow only if unpositioned
            }
        elif tag == "attr":
            self._emit_attr(a)
        elif tag == "dtacol":
            # A data-column flow box: like <area>, but it also carries default
            # prompt/entry widths (PMTWIDTH/ENTWIDTH) that its <dtafld>s inherit
            # so their captions and entries line up in a column.
            parent = self._areas[-1] if self._areas else None
            row = int(a["row"]) if "row" in a else (parent["row"] if parent else 0)
            self._areas.append({
                "row": row, "row0": row, "maxbottom": row,
                "col": int(a["col"]) if "col" in a else (parent["col"] if parent else 1),
                "fldgap": int(a["fldgap"]) if "fldgap" in a
                          else (parent["fldgap"] if parent else 1),
                "dir": str(a.get("dir", "vert")).strip().lower(),
                "start_idx": len(self.screen.items),
                "explicit": "row" in a,
                "parent": parent,
                "pmtwidth": int(a["pmtwidth"]) if "pmtwidth" in a
                            else (parent.get("pmtwidth") if parent else None),
                "entwidth": int(a["entwidth"]) if "entwidth" in a
                            else (parent.get("entwidth") if parent else None),
            })
        elif tag == "divider":
            ctx = self._areas[-1] if self._areas else None
            if ctx is not None and ctx.get("dir") == "horiz" and "row" not in a:
                # Inside a horizontal flow box a divider is a vertical gutter
                # between the columns either side of it: advance the column cursor
                # (by GUTTER, else the default gap) and draw no rule.
                ctx["col"] += int(a["gutter"]) if "gutter" in a else self._HGAP
            elif ctx is not None or "row" in a:
                # A horizontal rule spanning the rest of the flow box's width.
                row = int(a["row"]) if "row" in a else ctx["row"]
                col = int(a["col"]) if "col" in a else (ctx["col"] if ctx else 1)
                if ctx is not None:
                    ctx["row"] = row + 1
                width = max(1, self.screen.width - col - 1)
                self.screen.add(Text(row, col, "-" * width))
        elif tag in ("area", "region"):
            # A flow box. With explicit row/col it is a positioned sub-box; with
            # neither it transparently continues the enclosing flow (so its
            # content flows after the parent's, and the parent resumes after it).
            # DIR=HORIZ lays the box's children left-to-right instead of stacking
            # them top-to-bottom (side-by-side region columns).
            parent = self._areas[-1] if self._areas else None
            explicit = "row" in a
            # INDENT shifts the box's content that many columns to the right of its
            # origin (a <region indent=n>), nesting cumulatively.
            base_col = int(a["col"]) if "col" in a else (parent["col"] if parent else 1)
            row = int(a["row"]) if "row" in a else (parent["row"] if parent else 0)
            self._areas.append({
                "row": row, "row0": row, "maxbottom": row,
                "col": base_col + (int(a["indent"]) if "indent" in a else 0),
                "fldgap": int(a["fldgap"]) if "fldgap" in a
                          else (parent["fldgap"] if parent else 1),
                "dir": str(a.get("dir", "vert")).strip().lower(),
                "start_idx": len(self.screen.items),
                "explicit": explicit,
                "parent": parent,
            })
        elif tag == "msgmbr":
            self._in_msgmbr = True
            self._msgmbr_name = a.get("name", "")
            self._msgmbr_width = int(a["width"]) if "width" in a else None
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
            self._tag, self._attrs, self._chars = tag, a, []
            # A new content tag closes any still-open <dtafldd> (SGML omits the
            # end tag), so the dtafldd capture state must not leak into it.
            self._in_dtafldd, self._dtafldd = False, None

    def handle_data(self, data):
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
        elif (self._selfld is not None and not self._selfld["prompt_done"]):
            # Text between <selfld ...> and its first <choice> is the field prompt.
            self._selfld["prompt_chars"].append(data)

    def handle_endtag(self, tag):
        if self._panel_title is not None:
            self._finalize_panel_title()
        # Closing an inline <hp> banks its emphasised run and keeps the enclosing
        # text element open (it is not a block child).
        if tag == "hp" and self._runs is not None:
            self._end_hp()
            return
        # A container closing flushes any open content child first (end tags are
        # omitted in DTL), while its context is still intact. The element's own
        # end tag is handled below via the normal `tag == self._tag` path.
        if self._tag is not None and tag != self._tag and tag != "dtafldd":
            self._emit_current()  # flush at the current list depth, before any pop
        if tag == "da":
            self._emit_da()
            self._da = None
            return
        if tag in ("ul", "ol", "dl", "parml", "notel") and self._lists:
            self._lists.pop()
        if tag in ("panel", "help"):
            if self._da is not None:      # a <da> with an omitted end tag
                self._emit_da()
                self._da = None
            self._areas.clear()  # drop the panel's implicit flow box
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
            self._in_cmdtbl = False
            self._cur_cmd = None
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
            self._cur_cmd = None
            return
        if tag == "varclass":
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
        if tag == "lstgrp":
            self._lstgrp = None  # the open <lstcol>, if any, was flushed above
            return
        if tag == "lstfld":
            if self._lstfld is not None:
                self._emit_lstfld()
            self._lstfld, self._lstgrp = None, None
            return
        if tag == "varlist":
            self._in_varlist = False
            return
        if tag == "msgmbr":
            self._in_msgmbr = False
            return
        if tag in ("area", "region", "dtacol"):
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
                        # box and keep the parent on its origin row.
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
        if self._panel_title is not None:
            self._finalize_panel_title()
        if self._tag is not None:
            self._emit_current()

    def _emit_current(self):
        """Emit the open content element (``self._tag``) and reset capture state.

        Called both on an explicit end tag and implicitly when the next block
        tag starts — DTL omits most end tags, so an element is closed by what
        follows it."""
        tag = self._tag
        if tag is None:
            return
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
            self._emit_listitem(a, content)
        elif tag in ("dt", "dd", "pt", "pd"):
            self._emit_defitem(tag, a, content)
        elif tag == "lines":
            self._emit_lines(a, content)
        elif tag == "lstgrp":
            if self._lstgrp is not None:
                self._lstgrp["heading"] = " ".join(content.split())
        elif tag == "lstcol":
            self._add_lstcol(a, content)
        elif tag in _TEXT_TAGS:
            self._emit_info(a, content, tag, runs=runs)
        elif tag == "dtafld":
            self._emit_dtafld(a, content)
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
        if horiz:
            self._flow_horiz(box, start_idx)
        self._tag, self._attrs, self._chars = None, None, []
        self._runs, self._hp = None, None
        self._dtafldd, self._in_dtafldd = None, False

    def handle_startendtag(self, tag, attrs):
        # Self-closing form, e.g. <dtafld .../> or <info fill="-" width="37"/>
        self.handle_starttag(tag, attrs)
        if tag in _CONTENT_TAGS:
            self.handle_endtag(tag)
        elif tag in ("ul", "ol", "dl", "parml", "notel"):  # empty list; pop it
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
        """Resolve an element's ``(row, col)`` and return it with the active flow
        box. Explicit ``row``/``col`` win; otherwise they flow from the enclosing
        ``<area>``/``<region>`` (the row cursor advances one line per element).
        Outside any flow box, ``row``/``col`` are required."""
        ctx = self._areas[-1] if self._areas else None
        if "row" in a:
            row = int(a["row"])
            if ctx is not None:
                ctx["row"] = row + 1
        elif ctx is not None:
            row = ctx["row"]
            ctx["row"] = row + 1
        else:
            raise DTLError(f"<{tag}> missing required attribute 'row'")
        if "col" in a:
            col = int(a["col"])
        elif ctx is not None:
            col = ctx["col"]
        else:
            raise DTLError(f"<{tag}> missing required attribute 'col'")
        if not (0 <= row < self.screen.depth):
            raise DTLError(
                f"<{tag}> row {row} outside panel depth {self.screen.depth}"
            )
        if not (0 <= col < self.screen.width):
            raise DTLError(
                f"<{tag}> col {col} outside panel width {self.screen.width}"
            )
        return row, col, ctx

    # Unordered-list bullets by nesting depth (ISPF: o, then -, then --, …).
    _BULLETS = ("o", "-", "--", "---")
    _LIST_INDENT = 4   # columns added per nesting level
    _DL_TSIZE = 10     # default <dl>/<parml> term-column width (chars)
    _HGAP = 2          # default column gap between side-by-side (dir=horiz) items

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

    def _finalize_panel_title(self):
        """Emit the panel's title text (centered on row 0) and start the flow
        below it. Called when the first child tag follows the ``<panel>``."""
        text = re.sub(r"\s+", " ", "".join(self._panel_title or [])).strip()
        self._panel_title = None
        if not text:
            return
        if self.screen.title is None:
            self.screen.title = text
        col = max(0, (self.screen.width - len(text)) // 2)
        self.screen.add(Text(0, col, text, DisplayIntensity.NORMAL))
        if self._areas and self._areas[-1]["row"] < 1:
            self._areas[-1]["row"] = 1  # flow starts below the title

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
                         role=None):
        """Word-wrap ``text`` and emit it as protected lines from ``row`` at
        ``col`` (hanging indent for continuations). Optionally place a ``marker``
        (bullet/number) on the first line. Advances the flow cursor."""
        lines = self._wrap(text, max(1, self.screen.width - (col + 1)))
        if marker is not None:
            self.screen.add(Text(row, marker_col, marker, DisplayIntensity.NORMAL))
        for i, ln in enumerate(lines):
            self.screen.add(Text(row + i, col, ln, DisplayIntensity.NORMAL, role=role))
        if ctx is not None:
            ctx["row"] = row + len(lines)

    def _emit_listitem(self, a, content):
        """Emit one <li>: a depth-based bullet/number plus the item text, flowed,
        word-wrapped with a hanging indent, one level deeper per nested list."""
        text = " ".join(content.split())
        if not text:
            return
        ctx = self._areas[-1] if self._areas else None
        row = ctx["row"] if ctx else 0
        base = ctx["col"] if ctx else 1
        depth = max(len(self._lists), 1)
        bullet_col = base + (depth - 1) * self._LIST_INDENT
        lst = self._lists[-1] if self._lists else None
        if lst and lst["type"] == "ol":
            lst["n"] += 1
            ol_depth = sum(1 for ln in self._lists if ln["type"] == "ol")
            marker = self._ol_marker(lst["n"], ol_depth)
        else:
            marker = self._BULLETS[min(depth - 1, len(self._BULLETS) - 1)]
        self._emit_flow_lines(text, row, bullet_col + self._LIST_INDENT, ctx,
                              marker=marker, marker_col=bullet_col)

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
        base = col + (depth - 1) * self._LIST_INDENT
        if tag in ("dt", "pt"):
            self.screen.add(Text(row, base, text, _intensity(a)))
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
            self.screen.add(Text(row + i, col, ln[:width], DisplayIntensity.NORMAL))
        if ctx is not None:
            ctx["row"] = row + len(lines)

    def _add_lstcol(self, a, content):
        """Record one <lstcol> in the active table: its heading text, width
        (``colwidth``, else as wide as the heading), the bound ``datavar``, its
        ``usage`` (in/out), the model ``line`` it sits on, and its group."""
        if self._lstfld is None:
            return
        heading = " ".join(content.split())
        if "colwidth" in a:
            width = int(a["colwidth"])
        else:
            width = max(len(heading), 1)
        self._lstfld["cols"].append({
            "heading": heading,
            "width": width,
            "datavar": a.get("datavar", ""),
            # A column is an input field unless it is explicitly display-only.
            "usage": "out" if a.get("usage", "").lower() == "out" else "in",
            "line": int(a.get("line", 1)),
            "align": a.get("align", "start").lower(),
            "color": self._color(a),   # DTL COLOR on a <lstcol> colours its cells
            "group": self._lstgrp,
        })

    def _emit_lstfld(self):
        """Lay out the table's column header: each <lstcol> heading at its
        computed column (left to right, ``colwidth`` + a one-column gap), with
        any <lstgrp headline=yes> heading centered over its columns' span on the
        row above. Advances the enclosing flow past the header."""
        fld = self._lstfld
        cols = fld["cols"]
        if not cols:
            return
        x = fld["col"]
        for c in cols:
            c["x"] = x
            x += c["width"] + 1  # one-column gap between columns
        row = fld["row"]
        H = DisplayIntensity.HIGH
        groups = [g for g in fld["groups"] if g["headline"] and g["heading"]]
        if groups:
            for g in groups:
                gcols = [c for c in cols if c["group"] is g]
                if not gcols:
                    continue
                start = gcols[0]["x"]
                end = gcols[-1]["x"] + gcols[-1]["width"]
                text = g["heading"][:max(1, end - start)]
                gx = start + max(0, (end - start - len(text)) // 2)
                self.screen.add(Text(row, gx, text, H, role="heading"))
            row += 1
        for c in cols:
            if c["heading"]:
                self.screen.add(Text(row, c["x"], c["heading"][:c["width"]], H,
                                     role="heading"))
        row += 1
        row = self._emit_lstfld_rows(cols, row)
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
        data = self._rows if self._rows else [None]
        for entry in data:
            if row + entry_height > self.screen.depth - 1:
                break  # leave room; don't overrun the panel
            for c in cols:
                cy = row + (c["line"] - 1)
                raw = "" if entry is None else str(entry.get(c["datavar"], ""))
                value = self._align(raw, c["width"], c["align"])
                if c["usage"] == "out":
                    self.screen.add(Text(cy, c["x"], value, DisplayIntensity.NORMAL,
                                         color=c.get("color"), role="cell"))
                else:
                    self.screen.add(Field(
                        row=cy, col=c["x"], length=c["width"],
                        name=c["datavar"] or None, default=value,
                        terminator=id(c) in last_in_ids,
                        color=c.get("color"), role="cell",
                    ))
            row += entry_height
        return row

    @staticmethod
    def _align(text, width, align):
        text = text[:width]
        if align == "end":
            return text.rjust(width)
        if align in ("center", "centre"):
            return text.center(width)
        return text  # start/left: no padding (an input field fills its own width)

    def _emit_info(self, a, content, tag="info", runs=None):
        # ``runs`` (from inline <hp>) is a list of (text, color, highlight); the
        # concatenation is the field's plain text, so mono renders identically.
        if runs is not None and not content:
            content = "".join(t for t, _, _ in runs)
        # An admonition (<note>/<warning>/…) flows as a labelled callout.
        label = _ADMONITIONS.get(tag)
        if label and content.strip():
            content = label + " " + content.strip()
        # CUA role → default colour: a fill line is a separator rule; a top/panel
        # instruction is an instruction; a high-intensity heading is the title;
        # everything else is normal text (labels/values).
        if "fill" in a:
            role = "rule"
        elif tag in _INSTRUCTION_TAGS:
            role = "inst"
        elif _intensity(a) is DisplayIntensity.HIGH:
            role = "title"
        else:
            role = "text"
        if "fill" in a:
            content = a["fill"] * int(a.get("width", 0))
            row, col, _ = self._resolve_pos(a, "info")
            self.screen.add(Text(row, col, content, _intensity(a), role=role))
            return
        if "row" in a:
            # Explicit position: emit content exactly as written (no wrap), so
            # the bundled panels stay byte-for-byte identical (mono).
            if "\n" in content:
                content = re.sub(r"\s*\n\s*", " ", content).strip()
            if not content.strip():
                return
            row, col, _ = self._resolve_pos(a, "info")
            if runs is not None:
                # A phrase inside this line is emphasised via SA runs (see #110):
                # one Text.rich field whose surround uses the element's role colour
                # and whose <hp> phrase carries its own colour/highlight. Mono
                # renders as the plain concatenation, byte-for-byte unchanged.
                self.screen.add(Text.rich(row, col, runs,
                                          intensity=_intensity(a), role=role))
            else:
                self.screen.add(Text(row, col, content, _intensity(a), role=role))
            return
        # Flowed text: normalize whitespace and word-wrap to the panel width.
        text = " ".join(content.split())
        if not text:
            return
        row, col, ctx = self._resolve_pos(a, "info")
        if self._lists:
            # A paragraph inside a list aligns with the list's item text.
            col += len(self._lists) * self._LIST_INDENT
        self._emit_flow_lines(text, row, col, ctx, role=role)

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

    def _add_field(self, a, content, tag, name):
        """Emit a prompt (if any) plus its field: an unprotected input field, or —
        for ``usage=out`` — the variable's value as protected display text. Returns
        the Field, or ``None`` for a display field."""
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
        if pmt_above and content:
            self.screen.add(Text(row, col, content, _intensity(a), role="prompt"))
            content = ""                   # caption already placed
            row += 1
            if ctx is not None:
                ctx["row"] += 1            # the field occupies a second line
        pmtwidth = ctx.get("pmtwidth") if ctx else None
        if "fldcol" in a:
            fldcol = int(a["fldcol"])
        elif pmt_above:
            fldcol = col                   # under the prompt, at the base column
        elif pmtwidth:
            fldcol = col + pmtwidth        # <dtacol>: entry at a fixed prompt column
        elif ctx is not None:
            fldcol = col + len(content) + ctx["fldgap"]  # entry flows after prompt
        else:
            fldcol = col
        # Entry width: explicit ``entwidth`` wins; otherwise fall back to the
        # variable's display length (``dispmaxlen``), the enclosing <dtacol>'s
        # default entry width, or a small default, so auto-flow guide fields that
        # size via the column or the variable still render.
        default_ew = (ctx.get("entwidth") if ctx else None) or 8
        length = int(a.get("entwidth", a.get("dispmaxlen", default_ew)))
        auto = ctx is not None and "row" not in a and "fldcol" not in a
        if fldcol + length > self.screen.width:
            if auto:
                # An auto-flowed field whose entry runs off the panel: our column
                # math only approximates ISPDTLC's (side-by-side dir=horiz columns
                # especially), so clamp it to the panel edge rather than abort the
                # whole panel — an explicit position that overflows is still an
                # author error and raises below.
                length = max(1, self.screen.width - fldcol - 1)
                if length < 1 or fldcol >= self.screen.width:
                    fldcol = max(col, self.screen.width - 2)
                    length = 1
            else:
                raise DTLError(
                    f"<{tag}> field at col {fldcol} width {length} overflows "
                    f"panel width {self.screen.width}"
                )
        if content:
            # The prompt/caption is a CUA element with its own role colour (green,
            # the field-prompt colour); DTL's COLOR on a <dtafld> colours the
            # *field*, not the caption.
            self.screen.add(Text(row, col, content, _intensity(a), role="prompt"))
        # USAGE=OUT is a display-only (output) field: show the variable's value as
        # protected text — like a list column — not an editable input box.
        if str(a.get("usage", "")).strip().lower() == "out":
            value = self._subs.get((name or "").upper()) or a.get("default", "")
            self.screen.add(Text(row, fldcol, str(value)[:length].ljust(length),
                                 _intensity(a), color=self._color(a), role="cell"))
            return None
        field = Field(
            row=row,
            col=fldcol,
            length=length,
            name=name,
            default=a.get("default", ""),
            numeric=self._resolve_numeric(a, name),
            hidden=_bool_attr(a, "hidden"),
            cursor=_bool_attr(a, "cursor"),
            mdt=_bool_attr(a, "mdt", default=True),
            # DTL COLOR= colours the entry field; else its CUA role (turquoise).
            color=self._color(a),
            role="field",
            highlight=self._hilite(a),
            help=self._field_help(a),
        )
        self.screen.add(field)
        self._attach_validation(name)
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

    def _attach_validation(self, name):
        """Attach a field's variable-class <checkl> validation to the Screen."""
        if not name:
            return
        decl = self._vardcls.get(name.upper())
        if not decl:
            return
        vc = self._varclasses.get(str(decl.get("varclass", "")).upper())
        if vc and vc.get("checks"):
            self.screen.validations[name.upper()] = {
                "checkmsg": vc.get("msg"),
                "checks": vc["checks"],
            }

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

    def _emit_vardcl(self, a):
        # A <vardcl> belongs in a <varlist>, but tolerate a stray one (some guide
        # examples begin mid-declaration) rather than aborting the whole panel —
        # a declaration with no name simply carries nothing to record.
        name = a.get("name")
        if not name:
            return
        self._vardcls[name.upper()] = {"varclass": a.get("varclass", "")}

    def _emit_dtafld(self, a, content):
        self._add_field(a, content, "dtafld", a.get("datavar"))

    def _emit_cmdarea(self, a, content):
        # The command area is ISPF's command/option line; its variable defaults
        # to the conventional ZCMD. Mark the field as the panel's command area.
        field = self._add_field(a, content, "cmdarea", a.get("datavar", "ZCMD"))
        self.screen.command_field = field

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

    def _emit_choice(self, a, content):
        sf = self._selfld
        if sf is None:
            raise DTLError("<choice> outside of a <selfld>")
        self._emit_selfld_prompt(sf)
        row = sf["row"]
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
        # The value that selects this choice. IBM's attribute is MATCH; it defaults
        # to the displayed number.
        match = a.get("match", a.get("num", "")).strip().upper()
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
            self.screen.add(Text(row, sf["numcol"], a.get("num", "").ljust(sf["numwidth"]),
                                 num_int, color=explicit, role=rnum))
        self.screen.add(Text(row, sf["namecol"], a.get("name", ""),
                             color=explicit, role=rname))
        self.screen.add(Text(row, sf["desccol"], content,
                             color=explicit, role=rdesc))
        sf["row"] = row + 1
        sf["count"] += 1
        if unavail:
            return                          # not selectable → no routing/point-and-shoot
        if match:
            self.screen.selections[match] = a.get("name", "").strip()
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
