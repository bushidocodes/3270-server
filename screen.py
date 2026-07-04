"""Data-driven 3270 screen model.

This module turns a screen from an *imperative pile of byte-emitting calls*
(see the original ``send_tso_logon`` / ``send_ispf_menu`` in :mod:`server`)
into *data*: a :class:`Screen` is an ordered list of :class:`Text` and
:class:`Field` items that knows how to render itself to a 3270 data stream and
how to map a client response back to named fields.

It is deliberately built on the existing, tested low-level primitives in
:mod:`server` (``encode_pack_addr``, ``field_attribute``,
``write_control_character``) so behaviour — and the exact bytes on the wire —
match the hand-written screens exactly. The declarative DTL front-end
(:mod:`dtl`) targets this model rather than emitting bytes directly.
"""

from dataclasses import dataclass, field as _dc_field
from enum import Enum
from typing import List, Optional, Dict, Tuple

from server import (
    DisplayIntensity,
    FieldType,
    IAC,
    EOR,
    SBA,
    SF,
    IC,
    encode_pack_addr,
    field_attribute,
    write_control_character,
    to_ebcdic,
)


def _display(text: str) -> bytes:
    """Encode display text to EBCDIC, replacing any character the session's code
    page can't encode with its substitute (``?``) rather than raising. Rendering a
    panel must degrade a stray non-cp037 character, not crash the whole session —
    a real host does the same (the browse path already does; see #150). All
    cp037-safe text (every bundled panel) encodes identically to strict mode, so
    this changes no bytes."""
    return to_ebcdic(text, errors="replace")


ERASE_WRITE = 0xF5
# ERASE/WRITE ALTERNATE selects the terminal's *alternate* (model-specific)
# presentation space instead of the 24x80 default — 32x80 (model 3), 43x80
# (model 4), or 27x132 (model 5). A Screen renders with this when ``alternate``
# is set (see Screen.render); buffer addresses then use the screen's real width.
ERASE_WRITE_ALTERNATE = 0x7E
# Plain Write: unlike ERASE/WRITE it does *not* clear the presentation space, so
# only the addressed positions are changed and everything else — including what
# the user has typed — stays put. Used by Screen.render_partial to patch a
# message line without repainting the panel (see the ISPF menu redisplay).
WRITE = 0xF1

# Start Field Extended: like SF (0x1D), but the attribute is expressed as a
# count followed by that many (type, value) pairs, so a field can carry colour
# and highlighting in addition to the basic 3270 field attribute. Only sent to
# terminals that negotiated the extended data stream; a mono terminal always
# gets plain SF, so its data stream is byte-for-byte unchanged.
SFE = 0x29
# Set Attribute: sets one character attribute (a type/value pair) that applies to
# the characters that follow it, *within* the current field — so a single field
# can carry mixed colour/highlight (e.g. an emphasised keyword in a line of text)
# without being split into separate fields. Only emitted on a colour render; a
# mono render just concatenates the text, so it stays byte-for-byte unchanged.
SA = 0x28
XA_BASIC = 0xC0        # pair type: the all-character / basic field attribute
XA_HIGHLIGHT = 0x41    # pair type: extended highlighting
XA_FOREGROUND = 0x42   # pair type: foreground colour
# Repeat to Address: fill the buffer from the current position up to a stop
# address with one repeated character — 4 bytes for any run length, versus one
# byte per character. Used for rule lines / fills (see Text.render). The rendered
# result is identical; only the wire stream is shorter.
RA = 0x3C
# Erase Unprotected to Address: null every *unprotected* position from the
# current position to a stop address, leaving protected text intact — the native
# "clear the input fields" order (see Screen.render_erase_input).
EUA = 0x12
# Graphic Escape: the next single byte is taken from the terminal's alternate
# graphic character set (the 3270 line-drawing / APL glyphs) instead of the base
# EBCDIC code page. Used to draw box/rule glyphs (see GraphicText and the Line
# set). Also valid as the repeat character of an RA order (see _emit_ra ``ge``).
GE = 0x08
# A run of the same character at least this long is worth compacting into an RA
# order (RA is 4 bytes, so it wins for runs of 5+).
_RA_MIN_RUN = 5


class Color(Enum):
    """3270 extended foreground colours (attribute type 0x42)."""
    DEFAULT = 0x00
    BLUE = 0xF1
    RED = 0xF2
    PINK = 0xF3
    GREEN = 0xF4
    TURQUOISE = 0xF5
    YELLOW = 0xF6
    WHITE = 0xF7


class Highlight(Enum):
    """3270 extended highlighting (attribute type 0x41)."""
    DEFAULT = 0x00
    BLINK = 0xF1
    REVERSE = 0xF2
    UNDERSCORE = 0xF4


class Line(Enum):
    """3270 line-drawing glyphs from the Graphic Escape (GE) character set.

    Each value is a code point in the terminal's alternate graphic set; emitted as
    ``GE`` (0x08) + code (see :class:`GraphicText`), the terminal draws the box
    glyph. The code points — and the Unicode box-drawing characters an emulator
    renders/reads them back as — are from x3270's ``apl2uc[]`` table
    (``Common/unicode.c``); e.g. code 0xA2 reads back as U+2500 '─'.
    """
    HORIZONTAL   = 0xA2   # ─ U+2500  light horizontal
    VERTICAL     = 0x85   # │ U+2502  light vertical
    TOP_LEFT     = 0xC5   # ┌ U+250C  down and right
    TOP_RIGHT    = 0xD5   # ┐ U+2510  down and left
    BOTTOM_LEFT  = 0xC4   # └ U+2514  up and right
    BOTTOM_RIGHT = 0xD4   # ┘ U+2518  up and left
    TEE_RIGHT    = 0xC6   # ├ U+251C  vertical and right
    TEE_LEFT     = 0xD6   # ┤ U+2524  vertical and left
    TEE_DOWN     = 0xD7   # ┬ U+252C  down and horizontal
    TEE_UP       = 0xC7   # ┴ U+2534  up and horizontal
    CROSS        = 0xD3   # ┼ U+253C  vertical and horizontal


# CUA element role → default z/OS ISPF colour. An item carries a role (from the
# DTL element it came from); on a colour terminal an item with no explicit colour
# renders in its role's colour, matching real ISPF (white title, green
# prompts/text, turquoise option keywords + column headings, white option numbers,
# blue separator rules). Applied only when rendering in colour, so mono is
# byte-for-byte unchanged.
_CUA_COLORS = {
    "title":   Color.WHITE,      # panel title (centred heading)
    "rule":    Color.BLUE,       # separator / fill lines
    "inst":    Color.WHITE,      # top/panel instructions
    "text":    Color.GREEN,      # normal text, field/status labels
    "prompt":  Color.GREEN,      # field prompt ("Option ===>")
    "field":   Color.TURQUOISE,  # unprotected entry field
    "num":     Color.WHITE,      # point-and-shoot choice number
    "name":    Color.TURQUOISE,  # choice keyword / name
    "desc":    Color.GREEN,      # choice description
    "heading": Color.TURQUOISE,  # list column heading
    "cell":    Color.TURQUOISE,  # list data cell
    "unavail": Color.BLUE,       # an unavailable (non-selectable) menu choice
}


def _role_colour(color: Optional[Color], role: Optional[str]) -> Optional[Color]:
    """The effective colour for an item: its explicit colour, else its CUA role's
    default colour, else None."""
    return color if color is not None else _CUA_COLORS.get(role)


def _emit_field_start(buf: bytearray, fa: int,
                      color: Optional[Color], highlight: Optional[Highlight]) -> None:
    """Emit a field start into ``buf``.

    With no extended attributes this is the classic ``SF`` + attribute byte —
    byte-for-byte what the mono panels have always produced. With a colour
    and/or highlight it is an ``SFE`` carrying the basic field attribute
    (type 0xC0) plus one pair per extended attribute.
    """
    pairs = []
    if color is not None and color != Color.DEFAULT:
        pairs.append((XA_FOREGROUND, color.value))
    if highlight is not None and highlight != Highlight.DEFAULT:
        pairs.append((XA_HIGHLIGHT, highlight.value))
    if not pairs:
        buf.append(SF)
        buf.append(fa)
        return
    buf.append(SFE)
    buf.append(1 + len(pairs))   # pair count includes the basic-attribute pair
    buf.append(XA_BASIC)
    buf.append(fa)
    for xa_type, xa_value in pairs:
        buf.append(xa_type)
        buf.append(xa_value)


def _emit_attr_runs(buf: bytearray, runs, base_color: Optional[Color],
                    base_highlight: Optional[Highlight]) -> None:
    """Emit a field's text as a sequence of attribute runs, each preceded by
    ``SA`` orders setting the foreground colour and highlight for the characters
    that follow. A run whose colour/highlight is ``None`` falls back to the
    field's base attribute, so a plain run re-asserts the base — that is how the
    text returns to normal after an emphasised phrase."""
    for text, run_color, run_highlight in runs:
        color = run_color if run_color is not None else base_color
        highlight = run_highlight if run_highlight is not None else base_highlight
        buf.append(SA)
        buf.append(XA_FOREGROUND)
        buf.append(color.value if color not in (None, Color.DEFAULT) else 0x00)
        buf.append(SA)
        buf.append(XA_HIGHLIGHT)
        buf.append(highlight.value if highlight not in (None, Highlight.DEFAULT) else 0x00)
        buf.extend(_display(text))


# A valid symbol name (DTL <checki type="name">): a letter or one of @ # $,
# then up to 7 more of those or digits — the ISPF/TSO name rule.
_NAME_FIRST = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz@#$")
_NAME_REST = _NAME_FIRST | set("0123456789")


def _check_failure(check: dict, value: str):
    """Return message substitutions if ``value`` fails ``check``, else ``None``.

    Mirrors a DTL ``<checki>``: ``range`` requires a number within [min, max];
    ``values`` requires membership in a fixed set (or, when ``negate``, absence
    from it); ``alpha`` requires all letters; ``name`` requires a valid symbol
    name. The returned dict feeds the check's ``checkmsg`` (e.g.
    ``{"VALUE": .., "MIN": .., "MAX": ..}``).
    """
    if check["type"] == "range":
        try:
            n = int(value)
        except ValueError:
            n = None
        if n is None or not (check["min"] <= n <= check["max"]):
            return {"VALUE": value, "MIN": check["min"], "MAX": check["max"]}
        return None
    if check["type"] == "values":
        present = value.upper() in check["values"]
        failed = present if check.get("negate") else not present
        return {"VALUE": value} if failed else None
    if check["type"] == "xlati":                       # <xlatl>/<xlati> translate
        v = value.upper() if check.get("upper") else value
        return None if v in check["values"] else {"VALUE": value}
    if check["type"] == "alpha":
        return None if (value.isascii() and value.isalpha()) else {"VALUE": value}
    if check["type"] == "name":
        ok = (1 <= len(value) <= 8 and value[0] in _NAME_FIRST
              and all(c in _NAME_REST for c in value))
        return None if ok else {"VALUE": value}
    if check["type"] == "maxlen":                     # <varclass type='char N'>
        if len(value) > check["max"]:
            return {"VALUE": value, "MAX": check["max"]}
        return None
    if check["type"] == "maxdigits":                  # <varclass type='numeric N'>
        if sum(c.isdigit() for c in value) > check["max"]:
            return {"VALUE": value, "MAX": check["max"]}
        return None
    return None  # unknown check type: treat as passing


def _emit_sba(buf: bytearray, row: int, col: int, cols: int = 80) -> None:
    buf.append(SBA)
    buf.extend(encode_pack_addr(row, col, cols))


def _emit_ra(buf: bytearray, stop: int, char_byte: int,
             cols: int = 80, rows: int = 24, ge: bool = False) -> None:
    """Repeat ``char_byte`` from the current buffer position up to (not
    including) the linear ``stop`` address.

    The 3270 buffer is circular, so a run that fills through the very last cell
    stops at address 0, not one past the end: ``stop`` is taken modulo the buffer
    size. Without this a full-width rule on the bottom row would encode a stop
    address of ``cols*rows`` (e.g. 1920 on a 24x80 screen), which real terminals
    reject as "RA address 1920 > maximum 1919".

    When ``ge`` is set the repeat character is drawn from the graphic (line-drawing)
    set: a ``GE`` byte is inserted before it, exactly where the RA order expects it
    (``RA`` addr ``GE`` char), so a full-width line-drawing rule is one 6-byte order.
    """
    stop %= cols * rows
    buf.append(RA)
    buf.extend(encode_pack_addr(stop // cols, stop % cols, cols))
    if ge:
        buf.append(GE)
    buf.append(char_byte)


@dataclass
class Text:
    """Protected, non-editable text positioned at ``(row, col)``.

    Renders as ``SBA`` + ``SF`` + attribute + EBCDIC text — the same shape the
    old ``_normal`` / ``_high`` / ``_highlighted`` helpers produced.
    """

    row: int
    col: int
    text: str
    intensity: DisplayIntensity = DisplayIntensity.NORMAL
    color: Optional[Color] = None
    highlight: Optional[Highlight] = None
    # CUA element role (e.g. "title", "num") → a default colour on colour
    # terminals. Not part of identity: two items that render alike are equal.
    role: Optional[str] = _dc_field(default=None, compare=False)
    # Optional character-level attribute runs: a list of
    # ``(text, color_or_None, highlight_or_None)`` that colour parts of this one
    # field independently via SA orders (see :meth:`rich`). ``None`` means a plain
    # field whose whole text uses the base attribute.
    runs: Optional[list] = None

    @classmethod
    def rich(cls, row, col, runs, *, intensity=DisplayIntensity.NORMAL,
             role=None, color=None, highlight=None) -> "Text":
        """Build a single protected field whose text is coloured in segments.

        ``runs`` is a list of ``(text, color)`` or ``(text, color, highlight)``;
        a ``None`` colour/highlight uses the field's base attribute. The field's
        ``text`` is the concatenation, so on a mono terminal it renders exactly
        like a plain :class:`Text` of that string."""
        norm = [(r[0], r[1], r[2] if len(r) > 2 else None) for r in runs]
        return cls(row, col, "".join(t for t, _, _ in norm), intensity=intensity,
                   role=role, color=color, highlight=highlight, runs=norm)

    def render(self, buf: bytearray, color: bool = False, cols: int = 80,
               rows: int = 24) -> None:
        _emit_sba(buf, self.row, self.col, cols)
        fa = field_attribute(display=self.intensity, protected=True)
        base_color = _role_colour(self.color, self.role) if color else None
        base_highlight = self.highlight if color else None
        _emit_field_start(buf, fa, base_color, base_highlight)
        if self.runs is not None and color:
            _emit_attr_runs(buf, self.runs, base_color, base_highlight)
        elif len(self.text) >= _RA_MIN_RUN and len(set(self.text)) == 1:
            # A long run of one character (a rule line / fill) — repeat it with a
            # single RA order instead of one byte per character. The field start
            # occupies self.col, so the run begins at self.col + 1.
            start = self.row * cols + self.col + 1
            _emit_ra(buf, start + len(self.text), _display(self.text[0])[0],
                     cols, rows)
        else:
            buf.extend(_display(self.text))


def _emit_graphic(buf: bytearray, row: int, col: int, codes: bytes,
                  cols: int = 80, rows: int = 24) -> None:
    """Emit a sequence of Graphic-Escape code points as a field's data.

    Each code is written as ``GE`` (0x08) + code so it is taken from the graphic
    (line-drawing) set rather than the EBCDIC code page. A run of one repeated code
    long enough to be worth it is compacted into a single GE'd ``RA`` order — the
    same rule-line compaction :class:`Text` applies — so a full-width border costs
    one order, not two bytes per cell. The field-attribute byte occupies
    ``(row, col)``, so the data begins at ``col + 1``.
    """
    pos = row * cols + col + 1
    i, n = 0, len(codes)
    while i < n:
        j = i
        while j < n and codes[j] == codes[i]:
            j += 1
        run = j - i
        if run >= _RA_MIN_RUN:
            _emit_ra(buf, pos + run, codes[i], cols, rows, ge=True)
        else:
            for _ in range(run):
                buf.append(GE)
                buf.append(codes[i])
        pos += run
        i = j


@dataclass
class GraphicText:
    """Protected line-drawing text from the Graphic Escape (GE) character set.

    ``codes`` is a ``bytes`` of GE code points (see :class:`Line`) — e.g.
    ``bytes([Line.HORIZONTAL.value]) * 78`` for a rule, or corner+run+corner for a
    box edge. Renders as a protected, non-editable field like :class:`Text`, but
    every glyph comes from the terminal's graphic set via a ``GE`` order (identical
    runs compact to a GE'd ``RA``). A colour/role emits ``SFE`` on a colour terminal
    and plain ``SF`` on mono, exactly like :class:`Text`.
    """

    row: int
    col: int
    codes: bytes
    intensity: DisplayIntensity = DisplayIntensity.NORMAL
    color: Optional[Color] = None
    highlight: Optional[Highlight] = None
    role: Optional[str] = _dc_field(default=None, compare=False)

    @classmethod
    def rule(cls, row, col, width, line: Line = Line.HORIZONTAL, **kw) -> "GraphicText":
        """A rule ``width`` cells wide drawn from ``line`` (default light
        horizontal). The attribute byte occupies ``col``; the rule spans columns
        ``col+1 .. col+width``."""
        return cls(row, col, bytes([line.value]) * width, **kw)

    @classmethod
    def box_top(cls, row, col, width, **kw) -> "GraphicText":
        """The top edge of a box ``width`` cells wide: ┌ + horizontals + ┐."""
        inner = max(0, width - 2)
        return cls(row, col, bytes([Line.TOP_LEFT.value])
                   + bytes([Line.HORIZONTAL.value]) * inner
                   + bytes([Line.TOP_RIGHT.value]), **kw)

    @classmethod
    def box_bottom(cls, row, col, width, **kw) -> "GraphicText":
        """The bottom edge of a box ``width`` cells wide: └ + horizontals + ┘."""
        inner = max(0, width - 2)
        return cls(row, col, bytes([Line.BOTTOM_LEFT.value])
                   + bytes([Line.HORIZONTAL.value]) * inner
                   + bytes([Line.BOTTOM_RIGHT.value]), **kw)

    def render(self, buf: bytearray, color: bool = False, cols: int = 80,
               rows: int = 24) -> None:
        _emit_sba(buf, self.row, self.col, cols)
        fa = field_attribute(display=self.intensity, protected=True)
        base_color = _role_colour(self.color, self.role) if color else None
        base_highlight = self.highlight if color else None
        _emit_field_start(buf, fa, base_color, base_highlight)
        _emit_graphic(buf, self.row, self.col, self.codes, cols, rows)


@dataclass
class Field:
    """An unprotected input field whose modified contents the client sends back.

    ``col`` is the column of the field-attribute byte; the readable data begins
    at ``col + 1`` and spans ``length`` characters. A protected terminator field
    is emitted immediately after the data so the input cannot bleed into the
    rest of the screen — exactly as the hand-written panels did. When
    ``cursor`` is set, an ``IC`` order is placed at the data start.
    """

    row: int
    col: int
    length: int
    name: Optional[str] = None
    default: str = ""
    intensity: DisplayIntensity = DisplayIntensity.NORMAL
    numeric: bool = False
    hidden: bool = False  # non-display (e.g. password) — overrides intensity
    mdt: bool = True
    cursor: bool = False
    terminator: bool = True
    color: Optional[Color] = None
    highlight: Optional[Highlight] = None
    role: Optional[str] = _dc_field(default=None, compare=False)
    # Field-level help panel (DTL <dtafld help=...>): shown when the cursor is on
    # this field and HELP is pressed. Metadata — not rendered, not part of identity.
    help: Optional[str] = _dc_field(default=None, compare=False)

    @property
    def data_addr(self) -> int:
        """Linear buffer address (row*80 + col) where this field's data starts."""
        return self.row * 80 + (self.col + 1)

    def render(self, buf: bytearray, color: bool = False, cols: int = 80,
               rows: int = 24) -> None:
        display = DisplayIntensity.NON_DISPLAY if self.hidden else self.intensity
        ftype = FieldType.NUMERIC if self.numeric else FieldType.ALPHANUMERIC
        _emit_sba(buf, self.row, self.col, cols)
        fa = field_attribute(
            display=display,
            protected=False,
            field_type=ftype,
            mdt=self.mdt,
        )
        # A hidden (password) field keeps its non-display attribute; colouring it
        # would be pointless and could fight the non-display intensity.
        _emit_field_start(
            buf, fa,
            _role_colour(self.color, self.role) if (color and not self.hidden) else None,
            self.highlight if (color and not self.hidden) else None,
        )
        buf.extend(_display(self.default.ljust(self.length)[: self.length]))
        if self.terminator:
            _emit_sba(buf, self.row, self.col + 1 + self.length, cols)
            buf.append(SF)
            buf.append(field_attribute(protected=True))
        if self.cursor:
            _emit_sba(buf, self.row, self.col + 1, cols)
            buf.append(IC)


@dataclass
class Screen:
    """An ordered collection of screen items that renders to a 3270 data stream."""

    items: List[object] = _dc_field(default_factory=list)
    title: Optional[str] = None
    # Name of this panel's help panel (DTL <panel help="...">), or None.
    help: Optional[str] = None
    # Presentation-space size (DTL <panel width=... depth=...>); a model-2 24x80
    # screen by default. Used to bounds-check element positions at load time.
    width: int = 80
    depth: int = 24
    # Render on the terminal's *alternate* (model-specific) presentation space
    # via ERASE/WRITE ALTERNATE, sizing buffer addresses by ``width``. Left False
    # for the default 24x80 space, where the panels are byte-for-byte unchanged.
    alternate: bool = False
    erase: bool = True
    reset_mdts: bool = True
    keyboard_restore: bool = True
    sound_alarm: bool = False
    # Function-key → command map (e.g. {"PF3": "EXIT"}), from a DTL <keyl>.
    # Pure metadata: it is not rendered, so it never affects the data stream.
    keylist: Dict[str, str] = _dc_field(default_factory=dict)
    # The panel's command-area input field (the ISPF "Option/Command ===>" line),
    # from a DTL <cmdarea>. None if the panel has no command area.
    command_field: Optional["Field"] = None
    # Selectable menu values → choice name, from each <choice>'s MATCH (else its
    # num). Lets the dialog validate/route a typed option against the panel's own
    # declared choices. Metadata: not rendered.
    selections: Dict[str, str] = _dc_field(default_factory=dict)
    # Screen row → the option value of the <choice> rendered on it. Lets the
    # dialog resolve a cursor position to a menu choice (point-and-shoot: put
    # the cursor on a choice and press Enter). Metadata: not rendered.
    selection_rows: Dict[int, str] = _dc_field(default_factory=dict)
    # Multi-select mark fields (DTL <selfld type=multi>): each choice has its own
    # 1-char input field the user marks (any non-blank char selects it), so more
    # than one choice can be chosen. [{"value": match, "name": choice, "addr": n}].
    # The mark Fields are also emitted as items; this records how to read them.
    selection_fields: List[dict] = _dc_field(default_factory=list)
    # Field name (upper) → {"checkmsg": id, "checks": [...]}, from a variable's
    # <varclass> validation (<checkl>/<checki>). Metadata: not rendered.
    validations: Dict[str, dict] = _dc_field(default_factory=dict)
    # Command name (upper) → {"action": str, "trunc": int}, from a DTL <cmdtbl>.
    # Lets the command line recognise named commands (with truncation).
    commands: Dict[str, dict] = _dc_field(default_factory=dict)
    # Action-bar choices from a DTL <ab>: [{"label": str, "pdc": [...]}]. The
    # choice labels are also emitted as Text items; the pull-down structure is
    # kept here for future point-and-shoot interaction.
    action_bar: List[dict] = _dc_field(default_factory=list)
    # Optional (row, col) where the cursor should be placed (an extra IC order),
    # e.g. to land it on an action-bar choice for F10/F11 keyboard navigation.
    # None leaves cursor placement to the items' own IC (or the default).
    cursor_at: Optional[Tuple[int, int]] = None

    def add(self, item) -> "Screen":
        self.items.append(item)
        return self

    def action_choice_at(self, cursor_addr: Optional[int]) -> Optional[dict]:
        """The action-bar choice the cursor is on (point-and-shoot), or ``None``.

        ``cursor_addr`` is the linear buffer address from the inbound reply; it
        is on a choice when it falls within that choice's rendered label.
        """
        if cursor_addr is None:
            return None
        row, col = divmod(cursor_addr, 80)
        for choice in self.action_bar:
            if choice.get("row") == row and \
                    choice["col"] < col <= choice["col"] + len(choice["label"]):
                return choice
        return None

    def selection_at(self, cursor_addr: Optional[int]) -> Optional[str]:
        """The option value of the menu choice the cursor's row is on, or
        ``None`` (point-and-shoot: put the cursor on a choice and press Enter).
        """
        if cursor_addr is None:
            return None
        return self.selection_rows.get(cursor_addr // 80)

    def selected_values(self, fields_by_addr: Dict[int, str]) -> List[str]:
        """For a multi-select panel (DTL ``<selfld type=multi>``), the MATCH
        values of the choices the client marked — every mark field whose returned
        value is a non-blank character. Empty when the panel has no multi-select
        field or nothing was marked."""
        return [sf["value"] for sf in self.selection_fields
                if (fields_by_addr.get(sf["addr"], "") or "").strip()]

    def lookup_command(self, typed: Optional[str]) -> Optional[str]:
        """Resolve a typed command against the command table, honouring each
        command's truncation (e.g. KEYLIST trunc=3 matches KEY/KEYL/…). Returns
        the command's action string, or ``None`` if it is not a known command.
        """
        if not typed:
            return None
        t = typed.strip().upper()
        for name, c in self.commands.items():
            if t == name:
                return c["action"]
            trunc = c.get("trunc") or 0
            if trunc and len(t) >= trunc and name.startswith(t):
                return c["action"]
        return None

    def first_validation_error(self, fields_by_addr: Dict[int, str]):
        """Validate submitted fields against their <varclass> checks.

        Returns ``(msgid, subs)`` for the first field whose value fails a check
        (``subs`` are substitution values for the message, e.g. MIN/MAX/VALUE),
        or ``None`` if everything validates. An empty field fails only if it is
        REQUIRED (DTL ``<dtafld required=yes>``); otherwise it is not checked.
        """
        addr_by_name = {
            f.name.upper(): f.data_addr
            for f in self.items
            if isinstance(f, Field) and f.name
        }
        for name, spec in self.validations.items():
            addr = addr_by_name.get(name.upper())
            if addr is None:
                continue
            value = (fields_by_addr.get(addr) or "").strip()
            if not value:
                required_msg = spec.get("required_msg")
                if required_msg:
                    return required_msg, {}
                continue
            for check in spec["checks"]:
                subs = _check_failure(check, value)
                if subs is not None:
                    # A check may name its own MSG (e.g. an <xlatl>); otherwise the
                    # field's class-level checkmsg applies.
                    return check.get("msg") or spec["checkmsg"], subs
        return None

    def command_value(self, fields_by_addr: Dict[int, str]) -> Optional[str]:
        """The text the client typed into the command area, or ``None``.

        Looks the command field up by buffer address in a parsed response so the
        session loop can read the command by role (like ISPF reading ``ZCMD``)
        rather than by a hard-coded address constant.
        """
        if self.command_field is None:
            return None
        return fields_by_addr.get(self.command_field.data_addr)

    def command_for(self, key: Optional[str]) -> Optional[str]:
        """The command bound to a function key (e.g. ``"PF3"``) by the keylist.

        Matches ISPF keylist semantics: a key press resolves to a command name
        (``EXIT``, ``HELP``, …) that the dialog then acts on. Returns ``None``
        when the key is unbound. Lookup is case-insensitive on the key name.
        """
        if not key:
            return None
        return self.keylist.get(key.upper())

    def help_for(self, cursor_addr: Optional[int]) -> Optional[str]:
        """The context-sensitive help panel for whatever the cursor is on, or
        ``None``.

        ISPF's HELP key is context-sensitive: with the cursor in a field that has
        its own ``<dtafld help=...>`` panel — or on an action-bar choice with a
        ``<abc help=...>`` — HELP shows that instead of the panel's general help.
        ``cursor_addr`` is the linear buffer address from the reply; it's "on" an
        element when it falls within that element's span.
        """
        if cursor_addr is None:
            return None
        for f in self.items:
            if isinstance(f, Field) and f.help and \
                    f.data_addr <= cursor_addr < f.data_addr + f.length:
                return f.help
        for choice in self.action_bar:
            start = choice["row"] * 80 + choice["col"]
            if choice.get("help") and start <= cursor_addr < start + len(choice["label"]):
                return choice["help"]
        return None

    def text(self, row, col, s, intensity=DisplayIntensity.NORMAL) -> "Screen":
        return self.add(Text(row, col, s, intensity))

    def field(self, row, col, length, **kw) -> "Screen":
        return self.add(Field(row, col, length, **kw))

    def render(self, color: bool = False) -> bytes:
        """Render to a 3270 data stream. When ``color`` is true, items carrying
        a colour/highlight emit Start Field Extended; otherwise (a mono terminal,
        or an item with no colour) they emit plain Start Field, so the mono data
        stream is byte-for-byte identical to before extended attributes existed.
        """
        buf = bytearray()
        if self.erase:
            buf.append(ERASE_WRITE_ALTERNATE if self.alternate else ERASE_WRITE)
        buf.extend(
            write_control_character(
                reset_mdts=self.reset_mdts,
                keyboard_restore=self.keyboard_restore,
                sound_alarm=self.sound_alarm,
            )
        )
        for item in self.items:
            item.render(buf, color=color, cols=self.width, rows=self.depth)
        if self.cursor_at is not None:
            _emit_sba(buf, self.cursor_at[0], self.cursor_at[1], self.width)
            buf.append(IC)
        buf.extend([IAC, EOR])
        return bytes(buf)

    def render_partial(self, items, color: bool = False,
                       cursor_at: Optional[Tuple[int, int]] = None) -> bytes:
        """Render a plain **Write** (0xF1) that updates only ``items``, leaving
        the rest of the presentation space — and the modified-data tags — alone.

        Unlike :meth:`render` this emits no ERASE, so the panel is *not*
        repainted and whatever the user has typed stays on screen and modified.
        Used to patch a message line or status field in place (e.g. the ISPF
        menu redisplaying "INVALID OPTION" without clobbering the typed option).
        ``cursor_at`` optionally repositions the cursor with an ``IC`` order.
        """
        buf = bytearray([WRITE])
        buf.extend(
            write_control_character(
                reset_mdts=False,           # keep the user's modified input
                keyboard_restore=True,      # unlock the keyboard for the next entry
                sound_alarm=self.sound_alarm,
            )
        )
        for item in items:
            item.render(buf, color=color, cols=self.width, rows=self.depth)
        if cursor_at is not None:
            _emit_sba(buf, cursor_at[0], cursor_at[1], self.width)
            buf.append(IC)
        buf.extend([IAC, EOR])
        return bytes(buf)

    def render_erase_input(self, cursor_at: Optional[Tuple[int, int]] = None) -> bytes:
        """Render a plain **Write** that erases every *unprotected* (input) field
        with an **EUA** order, leaving the protected text on screen intact — the
        native "clear the entry fields" operation. Pairs with :meth:`render_partial`
        to reset a form's input in place, without repainting the whole panel.

        Positions at the top of the buffer and issues EUA back to the top, so the
        erase wraps the whole presentation space (every unprotected field). The
        WCC resets the modified-data tags, so the cleared fields read as empty.
        ``cursor_at`` optionally repositions the cursor.
        """
        buf = bytearray([WRITE])
        buf.extend(
            write_control_character(
                reset_mdts=True,            # the cleared fields are no longer modified
                keyboard_restore=True,
                sound_alarm=self.sound_alarm,
            )
        )
        _emit_sba(buf, 0, 0, self.width)   # to the top of the buffer …
        buf.append(EUA)
        buf.extend(encode_pack_addr(0, 0, self.width))   # … erase around to the top
        if cursor_at is not None:
            _emit_sba(buf, cursor_at[0], cursor_at[1], self.width)
            buf.append(IC)
        buf.extend([IAC, EOR])
        return bytes(buf)

    # ── reading the response back ────────────────────────────────────────────

    def _addr_to_name(self) -> Dict[int, str]:
        return {
            f.data_addr: f.name
            for f in self.items
            if isinstance(f, Field) and f.name is not None
        }

    def field_addr(self, name: str) -> Optional[int]:
        """The linear data address of the named input field, or ``None``."""
        for f in self.items:
            if isinstance(f, Field) and f.name == name:
                return f.data_addr
        return None

    def parse(self, aid: int, fields_by_addr: Dict[int, str]) -> Tuple[int, Dict[str, str]]:
        """Map a parsed ``{addr: text}`` response onto this screen's field names.

        Addresses that match a named field become ``{name: text}``; any
        unrecognised addresses are dropped. Returns ``(aid, {name: text})``.
        """
        addr_to_name = self._addr_to_name()
        named = {
            addr_to_name[addr]: text
            for addr, text in fields_by_addr.items()
            if addr in addr_to_name
        }
        return aid, named
