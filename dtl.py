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
``<dtafld row col fldcol         a prompt (the element text) plus an unprotected
   datavar entwidth ...>``       input field at ``fldcol``. See attrs below.
``<selfld row numcol namecol     a list of menu choices; each ``<choice>`` is laid
   desccol numwidth>``           out on its own row, auto-incrementing.
``<choice num name>desc``        one menu row: number, name, description.

``<dtafld>`` attributes: ``datavar`` (field name sent back), ``entwidth`` (field
length), ``hidden`` (non-display, e.g. password), ``numeric``, ``default``,
``cursor`` (place the cursor here), ``mdt`` (default yes), ``intensity`` (prompt).

Variable substitution: ``${name}`` tokens in the source are replaced from the
keyword arguments to :func:`load_dtl` before parsing (e.g. the live user id and
time on the ISPF status line).
"""

from html.parser import HTMLParser
from string import Template

from screen import Screen, Text, Field, DisplayIntensity

_INTENSITY = {
    "normal": DisplayIntensity.NORMAL,
    "high": DisplayIntensity.HIGH,
    "highlighted": DisplayIntensity.HIGHLIGHTED,
}

_CONTENT_TAGS = ("info", "dtafld", "choice")


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
        elif tag in _CONTENT_TAGS:
            self._tag, self._attrs, self._chars = tag, a, []

    def handle_data(self, data):
        if self._tag is not None:
            self._chars.append(data)

    def handle_endtag(self, tag):
        if tag == "selfld":
            self._selfld = None
            return
        if tag != self._tag:
            return
        content = "".join(self._chars)
        a = self._attrs
        if tag == "info":
            self._emit_info(a, content)
        elif tag == "dtafld":
            self._emit_dtafld(a, content)
        elif tag == "choice":
            self._emit_choice(a, content)
        self._tag, self._attrs, self._chars = None, None, []

    def handle_startendtag(self, tag, attrs):
        # Self-closing form, e.g. <dtafld .../> or <info fill="-" width="37"/>
        self.handle_starttag(tag, attrs)
        if tag in _CONTENT_TAGS:
            self.handle_endtag(tag)
        elif tag == "selfld":  # a self-closing selfld has no choices; close it
            self._selfld = None

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

    def _emit_dtafld(self, a, content):
        row = self._req_int(a, "row", "dtafld")
        col = self._req_int(a, "col", "dtafld")
        fldcol = int(a.get("fldcol", col))
        if content:
            self.screen.add(Text(row, col, content, _intensity(a)))
        self.screen.add(Field(
            row=row,
            col=fldcol,
            length=self._req_int(a, "entwidth", "dtafld"),
            name=a.get("datavar"),
            default=a.get("default", ""),
            numeric=_truthy(a.get("numeric")),
            hidden=_truthy(a.get("hidden")),
            cursor=_truthy(a.get("cursor")),
            mdt=_truthy(a.get("mdt"), default=True),
        ))

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


def load_dtl(source: str, **subs) -> Screen:
    """Parse DTL markup into a :class:`screen.Screen`.

    ``subs`` provides values for ``${name}`` tokens in the source (e.g.
    ``userid``, ``time``) before parsing.
    """
    if subs:
        source = Template(source).safe_substitute(**subs)
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
