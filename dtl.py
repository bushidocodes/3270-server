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
``<region row col fldgap>``      flow down from this origin (one line each), and
                                 those that omit ``col`` use it. A field that omits
                                 ``fldcol`` gets its entry after the prompt
                                 (``col + len(prompt) + fldgap``). Explicit
                                 positions always win, so non-flowed panels are
                                 unaffected.
``<info row col intensity>``     protected text (label / instruction / rule).
                                 ``fill`` + ``width`` repeats a character (rules).
``<topinst row col>``            top instruction / panel instruction text. Render
``<paninst row col>``            like ``<info>`` (protected text); semantic DTL tags.
``<p>`` ``<lines>`` ``<dt>`` ``<dd>``  flowed text: paragraphs and items each render
``<pt>`` ``<pd>``                as a protected line. DTL omits end tags, so a block
                                 element is closed by whatever block tag follows it.
``<ul>`` ``<ol>`` ``<li>``         a list: each ``<li>`` flows as a bullet plus its
                                 text, indented one level per nesting (``<ul>`` →
                                 ``o``/``-``/``--`` by depth; ``<ol>`` → ``1.``, ``2.``…).
Panel title: the text after ``<panel ...>`` (before its first child) renders
centered on row 0, with the body flowing beneath it.
``<dtafld row col fldcol         a prompt plus an unprotected input field at
   datavar entwidth ...>``       ``fldcol``. The prompt is the text of a nested
                                 ``<dtafldd>`` child (authentic DTL) or, as a
                                 shorthand, the element's own text. See attrs below.
``<dtafldd>prompt</dtafldd>``    data-field description: the prompt for its
                                 enclosing ``<dtafld>`` or ``<cmdarea>``.
``<cmdarea row col fldcol         the command area (ISPF "Option/Command ===>"
   entwidth ...>``                line). Renders like ``<dtafld>``; ``datavar``
                                 defaults to ``ZCMD`` and the field is recorded
                                 as ``Screen.command_field``.
``<selfld row numcol namecol     a list of menu choices; each ``<choice>`` is laid
   desccol numwidth>``           out on its own row, auto-incrementing.
``<choice num name                one menu row: number, name, description. The
   matchval>desc``                selection value (``matchval``, default ``num``)
                                 is recorded in ``Screen.selections`` so the
                                 dialog can validate a typed option.
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
``<varclass name type>``         a variable class: ``type="numeric"`` makes its
                                 variables numeric-only. May contain a ``<checkl>``.
``<checkl checkmsg>``            a validity-check list; ``checkmsg`` names the
                                 message shown when a check fails.
``<checki type>min max``         a check item: ``type="range"`` (``min max`` text)
``<checki type>v1 v2 ...``       or ``type="values"`` (allowed values). A field's
                                 input is validated against its class's checks.
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

from screen import Screen, Text, Field, DisplayIntensity

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

_INTENSITY = {
    "normal": DisplayIntensity.NORMAL,
    "high": DisplayIntensity.HIGH,
    "highlighted": DisplayIntensity.HIGHLIGHTED,
}

# Block tags whose text flows as protected lines (like <info>): paragraphs,
# list items (<li>/<dt>/<dd>/<pt>/<pd>/<lp>), and preformatted <lines>. Their
# list containers (<ul>/<ol>/<dl>/<parml>/<sl>) are transparent — ignored.
_FLOW_TEXT_TAGS = ("p", "li", "dt", "dd", "pt", "pd", "lp", "lines")
_TEXT_TAGS = ("info", "topinst", "paninst") + _FLOW_TEXT_TAGS
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


class DTLError(ValueError):
    """Raised when DTL markup is malformed (missing required attribute, etc.)."""


class _DTLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.screen = Screen()
        self._tag = None          # current content-bearing tag, or None
        self._attrs = None
        self._chars = []
        self._selfld = None       # active <selfld> layout state, or None
        self._in_dtafldd = False  # capturing a <dtafldd> prompt child?
        self._dtafldd = None      # captured <dtafldd> prompt text, or None
        self._keylist = None      # active <keyl> bindings dict, or None
        self._varclasses = {}     # <varclass> name (upper) → {"numeric", "checks", "checkmsg"}
        self._vardcls = {}        # <vardcl> name (upper) → {"varclass": name}
        self._cur_varclass = None # name of the <varclass> currently being defined
        self._checkl = None       # active <checkl> {"checkmsg", "checks"} or None
        self._in_varlist = False  # inside a <varlist>?
        self._in_msgmbr = False   # inside a <msgmbr>?
        self.messages = {}        # <msg> msgid (upper) → message text
        self._areas = []          # stack of <area>/<region> flow contexts
        self._in_cmdtbl = False   # inside a <cmdtbl>?
        self._cur_cmd = None      # current <cmd> dict awaiting its <cmdact>
        self._ab = None           # active <ab> action bar being built, or None
        self._cur_abc = None      # current <abc> action-bar choice, or None
        self._cur_pdc = None      # current <pdc> pull-down choice, or None
        self._panel_title = None  # capturing the panel's title text, or None
        self._lists = []          # stack of open <ul>/<ol> ({"type", "n"})

    # ── SGML event handling ──────────────────────────────────────────────────

    def handle_starttag(self, tag, attrs):
        a = {k: v for k, v in attrs}
        # The panel's title text (between <panel ...> and its first child) ends
        # at the first child tag.
        if self._panel_title is not None:
            self._finalize_panel_title()
        # Implicit end tags: a new block element closes the open content element
        # (DTL omits most end tags). <dtafldd> is the exception — it's a child
        # that supplies its parent <dtafld>'s prompt, so it must not close it.
        if tag != "dtafldd" and self._tag is not None:
            self._emit_current()
        if tag in ("ul", "ol"):
            self._lists.append({"type": tag, "n": 0})
        if tag == "panel":
            self.screen.title = a.get("title")
            self.screen.help = a.get("help")
            if "width" in a:
                self.screen.width = int(a["width"])
            if "depth" in a:
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
            self._selfld = {
                "row": int(a["row"]) if "row" in a else (ctx["row"] if ctx else 0),
                "numcol": int(a.get("numcol", 1)),
                "namecol": int(a.get("namecol", 4)),
                "desccol": int(a.get("desccol", 21)),
                "numwidth": int(a.get("numwidth", 2)),
                "numintensity": _intensity(a, "numintensity", DisplayIntensity.HIGH),
                "ctx": ctx,
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
            self._cur_abc = {"chars": [], "pdc": []}
        elif tag == "pdc":
            if self._cur_abc is None:
                raise DTLError("<pdc> outside of an <abc>")
            self._cur_pdc = {"chars": [], "action": a.get("action", "")}
        elif tag == "action":
            # A pull-down choice's action (alternative to <pdc action=...>).
            if self._cur_pdc is not None:
                self._cur_pdc["action"] = (
                    a.get("action") or a.get("run") or a.get("cmd") or self._cur_pdc["action"]
                )
        elif tag == "varclass":
            self._emit_varclass(a)
        elif tag == "checkl":
            if self._cur_varclass is None:
                raise DTLError("<checkl> outside of a <varclass>")
            self._checkl = {"checkmsg": a.get("checkmsg"), "checks": []}
        elif tag == "checki":
            if self._checkl is None:
                raise DTLError("<checki> outside of a <checkl>")
            self._tag, self._attrs, self._chars = "checki", a, []
        elif tag == "varlist":
            self._in_varlist = True
        elif tag == "vardcl":
            self._emit_vardcl(a)
        elif tag in ("area", "region"):
            # A flow box. With explicit row/col it is a positioned sub-box; with
            # neither it transparently continues the enclosing flow (so its
            # content flows after the parent's, and the parent resumes after it).
            parent = self._areas[-1] if self._areas else None
            explicit = "row" in a
            self._areas.append({
                "row": int(a["row"]) if "row" in a else (parent["row"] if parent else 0),
                "col": int(a["col"]) if "col" in a else (parent["col"] if parent else 1),
                "fldgap": int(a["fldgap"]) if "fldgap" in a
                          else (parent["fldgap"] if parent else 1),
                "explicit": explicit,
                "parent": parent,
            })
        elif tag == "msgmbr":
            self._in_msgmbr = True
        elif tag == "msg":
            if not self._in_msgmbr:
                raise DTLError("<msg> outside of a <msgmbr>")
            if "msgid" not in a:
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
        elif self._tag is not None:
            self._chars.append(data)

    def handle_endtag(self, tag):
        if self._panel_title is not None:
            self._finalize_panel_title()
        # A container closing flushes any open content child first (end tags are
        # omitted in DTL), while its context is still intact. The element's own
        # end tag is handled below via the normal `tag == self._tag` path.
        if self._tag is not None and tag != self._tag and tag != "dtafldd":
            self._emit_current()  # flush at the current list depth, before any pop
        if tag in ("ul", "ol") and self._lists:
            self._lists.pop()
        if tag == "panel":
            self._areas.clear()  # drop the panel's implicit flow box
            return
        if tag == "selfld":
            # Advance the enclosing flow past the choices just laid out.
            if self._selfld and self._selfld.get("ctx") is not None:
                self._selfld["ctx"]["row"] = self._selfld["row"]
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
            if self._cur_pdc is not None and self._cur_abc is not None:
                self._cur_abc["pdc"].append({
                    "label": "".join(self._cur_pdc["chars"]).strip(),
                    "action": self._cur_pdc["action"],
                })
            self._cur_pdc = None
            return
        if tag == "abc":
            if self._cur_abc is not None and self._ab is not None:
                self._ab["choices"].append({
                    "label": "".join(self._cur_abc["chars"]).strip(),
                    "pdc": self._cur_abc["pdc"],
                })
            self._cur_abc = None
            return
        if tag == "ab":
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
                vc["checkmsg"] = self._checkl["checkmsg"]
            self._checkl = None
            return
        if tag == "varlist":
            self._in_varlist = False
            return
        if tag == "msgmbr":
            self._in_msgmbr = False
            return
        if tag in ("area", "region"):
            if self._areas:
                ctx = self._areas.pop()
                parent = ctx.get("parent")
                if parent is not None and not ctx.get("explicit"):
                    parent["row"] = ctx["row"]  # resume flow after a transparent box
            return
        if tag != self._tag:
            return
        self._emit_current()

    def _emit_current(self):
        """Emit the open content element (``self._tag``) and reset capture state.

        Called both on an explicit end tag and implicitly when the next block
        tag starts — DTL omits most end tags, so an element is closed by what
        follows it."""
        tag = self._tag
        if tag is None:
            return
        # A <dtafldd> child, if present, supplies the prompt; otherwise the
        # element's own text is the prompt (a convenient shorthand).
        content = self._dtafldd if isinstance(self._dtafldd, str) else "".join(self._chars)
        a = self._attrs
        if tag == "li":
            self._emit_listitem(a, content)
        elif tag in _TEXT_TAGS:
            self._emit_info(a, content)
        elif tag == "dtafld":
            self._emit_dtafld(a, content)
        elif tag == "cmdarea":
            self._emit_cmdarea(a, content)
        elif tag == "choice":
            self._emit_choice(a, content)
        elif tag == "msg":
            self.messages[a["msgid"].upper()] = content.strip()
        elif tag == "checki":
            self._emit_checki(a, content)
        self._tag, self._attrs, self._chars = None, None, []
        self._dtafldd, self._in_dtafldd = None, False

    def handle_startendtag(self, tag, attrs):
        # Self-closing form, e.g. <dtafld .../> or <info fill="-" width="37"/>
        self.handle_starttag(tag, attrs)
        if tag in _CONTENT_TAGS:
            self.handle_endtag(tag)
        elif tag in ("ul", "ol"):  # a self-closing list is empty; pop it
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

    def _emit_listitem(self, a, content):
        """Emit one <li>: a depth-based bullet plus the item text, flowed and
        indented one level per enclosing <ul>/<ol>."""
        text = re.sub(r"\s*\n\s*", " ", content).strip()
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
            bullet = f"{lst['n']}."
        else:
            bullet = self._BULLETS[min(depth - 1, len(self._BULLETS) - 1)]
        self.screen.add(Text(row, bullet_col, bullet, DisplayIntensity.NORMAL))
        self.screen.add(Text(row, bullet_col + self._LIST_INDENT, text,
                             DisplayIntensity.NORMAL))
        if ctx:
            ctx["row"] = row + 1

    def _emit_info(self, a, content):
        if "fill" in a:
            content = a["fill"] * int(a.get("width", 0))
        elif "\n" in content:
            # Multi-line flowed text (paragraphs wrap in real DTL): collapse the
            # line breaks and surrounding whitespace to single spaces so it
            # renders on one line. Single-line content is left byte-for-byte
            # untouched, so the bundled panels' exact spacing is preserved.
            content = re.sub(r"\s*\n\s*", " ", content).strip()
            if not content:
                return
        elif not content.strip():
            return  # empty text element: render nothing, don't consume a line
        row, col, _ = self._resolve_pos(a, "info")
        self.screen.add(Text(row, col, content, _intensity(a)))

    def _add_field(self, a, content, tag, name):
        """Emit a prompt (if any) plus an unprotected input field; return it."""
        row, col, ctx = self._resolve_pos(a, tag)
        if "fldcol" in a:
            fldcol = int(a["fldcol"])
        elif ctx is not None:
            fldcol = col + len(content) + ctx["fldgap"]  # entry flows after prompt
        else:
            fldcol = col
        # Entry width: explicit ``entwidth`` wins; otherwise fall back to the
        # variable's display length (``dispmaxlen``) or a small default, so
        # auto-flow guide fields that size via the variable still render.
        length = int(a.get("entwidth", a.get("dispmaxlen", 8)))
        if fldcol + length > self.screen.width:
            raise DTLError(
                f"<{tag}> field at col {fldcol} width {length} overflows "
                f"panel width {self.screen.width}"
            )
        if content:
            self.screen.add(Text(row, col, content, _intensity(a)))
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
        )
        self.screen.add(field)
        self._attach_validation(name)
        return field

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
                "checkmsg": vc.get("checkmsg"),
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
        # Authentic DTL types include CHAR/HEX/BIN/NUMERIC/…; the subset only
        # needs to know whether the class makes its fields numeric-only.
        vtype = str(a.get("type", "char")).strip().lower()
        self._cur_varclass = name.upper()
        self._varclasses[self._cur_varclass] = {
            "numeric": vtype in ("numeric", "num"),
            "checks": [],
            "checkmsg": None,
        }

    def _emit_checki(self, a, content):
        """A <checki> validity-check item: ``type="range"`` with ``min max`` text,
        or ``type="values"`` with a space-separated list of allowed values."""
        ctype = str(a.get("type", "")).strip().lower()
        words = content.split()
        if ctype == "range":
            if len(words) != 2:
                raise DTLError('<checki type="range"> needs "min max"')
            self._checkl["checks"].append(
                {"type": "range", "min": int(words[0]), "max": int(words[1])}
            )
        elif ctype == "values":
            self._checkl["checks"].append(
                {"type": "values", "values": [w.upper() for w in words]}
            )
        else:
            raise DTLError(f"<checki> unknown type {ctype!r}")

    def _emit_vardcl(self, a):
        if not self._in_varlist:
            raise DTLError("<vardcl> outside of a <varlist>")
        name = a.get("name")
        if not name:
            raise DTLError("<vardcl> missing required attribute 'name'")
        self._vardcls[name.upper()] = {"varclass": a.get("varclass", "")}

    def _emit_dtafld(self, a, content):
        self._add_field(a, content, "dtafld", a.get("datavar"))

    def _emit_cmdarea(self, a, content):
        # The command area is ISPF's command/option line; its variable defaults
        # to the conventional ZCMD. Mark the field as the panel's command area.
        field = self._add_field(a, content, "cmdarea", a.get("datavar", "ZCMD"))
        self.screen.command_field = field

    def _emit_choice(self, a, content):
        sf = self._selfld
        if sf is None:
            raise DTLError("<choice> outside of a <selfld>")
        row = sf["row"]
        self.screen.add(Text(row, sf["numcol"], a.get("num", "").ljust(sf["numwidth"]),
                             sf["numintensity"]))
        self.screen.add(Text(row, sf["namecol"], a.get("name", "")))
        self.screen.add(Text(row, sf["desccol"], content))
        sf["row"] = row + 1
        # Record the selection value the user types to pick this choice. It
        # defaults to the displayed number; an explicit ``matchval`` overrides.
        matchval = a.get("matchval", a.get("num", "")).strip().upper()
        if matchval:
            self.screen.selections[matchval] = a.get("name", "").strip()

    def _emit_action_bar(self, ab):
        """Lay the action-bar choice labels out across the bar's row (high
        intensity) and record the choices + their pull-downs on the Screen. Each
        choice keeps its ``row``/``col`` so the server can map a cursor onto it
        for point-and-shoot."""
        col = ab["col"]
        for choice in ab["choices"]:
            label = choice["label"]
            choice["row"], choice["col"] = ab["row"], col
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


def load_dtl(source: str, **subs) -> Screen:
    """Parse DTL markup into a :class:`screen.Screen`.

    ``subs`` provides values for ``&NAME`` dialog-variable references in the
    source (e.g. ``ZUSER``, ``ZTIME``) before parsing.
    """
    source = _substitute(source, subs)
    parser = _DTLParser()
    parser.feed(source)
    parser.close()
    return parser.screen


def load_panel(name: str, directory: str = None, **subs) -> Screen:
    """Load and parse ``<directory>/<name>.dtl``.

    ``directory`` defaults to the ``panels`` folder next to this module, so the
    panels resolve regardless of the process's current working directory.
    """
    import os
    if directory is None:
        directory = os.path.join(os.path.dirname(os.path.abspath(__file__)), "panels")
    path = os.path.join(directory, f"{name}.dtl")
    with open(path, "r", encoding="utf-8") as fh:
        return load_dtl(fh.read(), **subs)


class MessageCatalog:
    """Messages parsed from a DTL ``<msgmbr>``, looked up by id.

    Mirrors how ISPF keeps messages in a message library (ISPMLIB), separate
    from panels: :meth:`format` returns the displayable ``"<id> <text>"`` with
    any ``&NAME`` references in the text substituted at display time.
    """

    def __init__(self, messages: dict):
        self.messages = messages

    def format(self, msgid: str, **subs) -> str:
        text = self.messages.get(msgid.upper())
        if text is None:
            return msgid
        return f"{msgid} {_substitute(text, subs)}".rstrip()


def load_messages(source: str) -> MessageCatalog:
    """Parse a DTL message member (``<msgmbr>``/``<msg>``) into a catalog.

    The message text is left unsubstituted here; ``&NAME`` references are
    resolved per-message at display time by :meth:`MessageCatalog.format`.
    """
    parser = _DTLParser()
    parser.feed(source)
    parser.close()
    return MessageCatalog(parser.messages)


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
