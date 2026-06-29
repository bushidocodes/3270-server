"""A pragmatic subset of IBM's Dialog Tag Language (DTL) for defining screens.

DTL is IBM's real, ISO-SGML-based markup for ISPF panels — on z/OS you write
tagged source and run it through the ``ISPDTLC`` converter to produce panels,
messages, command tables, and keylists. This module is a small, self-contained
take on the same idea: ``load_dtl(source)`` parses DTL markup into a
:class:`screen.Screen`, which then renders to a 3270 data stream.

Relationship to authentic DTL
-----------------------------
We keep DTL's tag *names* and spirit but make two deliberate simplifications:

* **Explicit positioning.** Authentic DTL computes row/column automatically from
  the document structure (the genuinely hard part of ``ISPDTLC``). Here every
  visible element carries explicit ``row``/``col`` attributes, so the markup maps
  directly and predictably onto the field-oriented 3270 data stream.
* **Decoupled prompt + entry.** A ``<dtafld>`` still bundles a prompt with its
  input field, but the entry's column is given explicitly (``fldcol``) rather
  than flowed after the prompt.

Supported tags
--------------
``<panel name title>``           root container; ``title`` → ``Screen.title``
``<info row col intensity>``     protected text (label / instruction / rule).
                                 ``fill`` + ``width`` repeats a character (rules).
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
``<choice num name>desc``        one menu row: number, name, description.
``<keyl name>``                  a keylist: a set of function-key bindings for
                                 the panel (rendered as nothing; pure metadata).
``<keyi key cmd>desc``           one key binding: function key ``key`` (e.g.
                                 ``PF3``) invokes command ``cmd`` (e.g. ``EXIT``).

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

_CONTENT_TAGS = ("info", "dtafld", "cmdarea", "choice")
_FIELD_TAGS = ("dtafld", "cmdarea")


def _truthy(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in ("yes", "true", "1", "on")


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

    # ── SGML event handling ──────────────────────────────────────────────────

    def handle_starttag(self, tag, attrs):
        a = {k: v for k, v in attrs}
        if tag == "panel":
            self.screen.title = a.get("title")
        elif tag == "selfld":
            self._selfld = {
                "row": self._req_int(a, "row", tag),
                "numcol": int(a.get("numcol", 1)),
                "namecol": int(a.get("namecol", 4)),
                "desccol": int(a.get("desccol", 21)),
                "numwidth": int(a.get("numwidth", 2)),
                "numintensity": _intensity(a, "numintensity", DisplayIntensity.HIGH),
            }
        elif tag == "dtafldd":
            # The authentic data-field description (prompt) child of a field.
            if self._tag in _FIELD_TAGS:
                self._in_dtafldd, self._dtafldd = True, []
        elif tag == "keyl":
            self._keylist = {}
        elif tag == "keyi":
            self._emit_keyi(a)
        elif tag in _CONTENT_TAGS:
            self._tag, self._attrs, self._chars = tag, a, []
            self._dtafldd = None

    def handle_data(self, data):
        if self._in_dtafldd:
            self._dtafldd.append(data)
        elif self._tag is not None:
            self._chars.append(data)

    def handle_endtag(self, tag):
        if tag == "selfld":
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
        if tag != self._tag:
            return
        # A <dtafldd> child, if present, supplies the prompt; otherwise the
        # element's own text is the prompt (a convenient shorthand).
        content = self._dtafldd if isinstance(self._dtafldd, str) else "".join(self._chars)
        a = self._attrs
        if tag == "info":
            self._emit_info(a, content)
        elif tag == "dtafld":
            self._emit_dtafld(a, content)
        elif tag == "cmdarea":
            self._emit_cmdarea(a, content)
        elif tag == "choice":
            self._emit_choice(a, content)
        self._tag, self._attrs, self._chars, self._dtafldd = None, None, [], None

    def handle_startendtag(self, tag, attrs):
        # Self-closing form, e.g. <dtafld .../> or <info fill="-" width="37"/>
        self.handle_starttag(tag, attrs)
        if tag in _CONTENT_TAGS:
            self.handle_endtag(tag)
        elif tag == "dtafldd":  # empty prompt
            self.handle_endtag(tag)
        elif tag == "selfld":  # a self-closing selfld has no choices; close it
            self._selfld = None
        elif tag == "keyl":  # a self-closing keylist has no items; close it
            self.handle_endtag(tag)

    # ── element → model ──────────────────────────────────────────────────────

    @staticmethod
    def _req_int(attrs, key, tag):
        if key not in attrs:
            raise DTLError(f"<{tag}> missing required attribute '{key}'")
        return int(attrs[key])

    def _emit_info(self, a, content):
        if "fill" in a:
            content = a["fill"] * int(a.get("width", 0))
        self.screen.add(
            Text(self._req_int(a, "row", "info"), self._req_int(a, "col", "info"),
                 content, _intensity(a))
        )

    def _add_field(self, a, content, tag, name):
        """Emit a prompt (if any) plus an unprotected input field; return it."""
        row = self._req_int(a, "row", tag)
        col = self._req_int(a, "col", tag)
        fldcol = int(a.get("fldcol", col))
        if content:
            self.screen.add(Text(row, col, content, _intensity(a)))
        field = Field(
            row=row,
            col=fldcol,
            length=self._req_int(a, "entwidth", tag),
            name=name,
            default=a.get("default", ""),
            numeric=_truthy(a.get("numeric")),
            hidden=_truthy(a.get("hidden")),
            cursor=_truthy(a.get("cursor")),
            mdt=_truthy(a.get("mdt"), default=True),
        )
        self.screen.add(field)
        return field

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
