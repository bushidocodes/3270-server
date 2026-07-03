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

ERASE_WRITE = 0xF5

# Start Field Extended: like SF (0x1D), but the attribute is expressed as a
# count followed by that many (type, value) pairs, so a field can carry colour
# and highlighting in addition to the basic 3270 field attribute. Only sent to
# terminals that negotiated the extended data stream; a mono terminal always
# gets plain SF, so its data stream is byte-for-byte unchanged.
SFE = 0x29
XA_BASIC = 0xC0        # pair type: the all-character / basic field attribute
XA_HIGHLIGHT = 0x41    # pair type: extended highlighting
XA_FOREGROUND = 0x42   # pair type: foreground colour


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


def _check_failure(check: dict, value: str):
    """Return message substitutions if ``value`` fails ``check``, else ``None``.

    Mirrors a DTL ``<checki>``: ``range`` requires a number within [min, max];
    ``values`` requires membership in a fixed set. The returned dict feeds the
    check's ``checkmsg`` (e.g. ``{"VALUE": .., "MIN": .., "MAX": ..}``).
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
        if value.upper() not in check["values"]:
            return {"VALUE": value}
        return None
    return None  # unknown check type: treat as passing


def _emit_sba(buf: bytearray, row: int, col: int) -> None:
    buf.append(SBA)
    buf.extend(encode_pack_addr(row, col))


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

    def render(self, buf: bytearray, color: bool = False) -> None:
        _emit_sba(buf, self.row, self.col)
        fa = field_attribute(display=self.intensity, protected=True)
        _emit_field_start(buf, fa,
                          self.color if color else None,
                          self.highlight if color else None)
        buf.extend(to_ebcdic(self.text))


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

    @property
    def data_addr(self) -> int:
        """Linear buffer address (row*80 + col) where this field's data starts."""
        return self.row * 80 + (self.col + 1)

    def render(self, buf: bytearray, color: bool = False) -> None:
        display = DisplayIntensity.NON_DISPLAY if self.hidden else self.intensity
        ftype = FieldType.NUMERIC if self.numeric else FieldType.ALPHANUMERIC
        _emit_sba(buf, self.row, self.col)
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
            self.color if (color and not self.hidden) else None,
            self.highlight if (color and not self.hidden) else None,
        )
        buf.extend(to_ebcdic(self.default.ljust(self.length)[: self.length]))
        if self.terminator:
            _emit_sba(buf, self.row, self.col + 1 + self.length)
            buf.append(SF)
            buf.append(field_attribute(protected=True))
        if self.cursor:
            _emit_sba(buf, self.row, self.col + 1)
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
    # Selectable menu values → choice name, from <choice matchval=...> entries.
    # Lets the dialog validate/route a typed option against the panel's own
    # declared choices. Metadata: not rendered.
    selections: Dict[str, str] = _dc_field(default_factory=dict)
    # Screen row → the option value of the <choice> rendered on it. Lets the
    # dialog resolve a cursor position to a menu choice (point-and-shoot: put
    # the cursor on a choice and press Enter). Metadata: not rendered.
    selection_rows: Dict[int, str] = _dc_field(default_factory=dict)
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
        or ``None`` if everything validates. Empty fields are not checked.
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
                continue
            for check in spec["checks"]:
                subs = _check_failure(check, value)
                if subs is not None:
                    return spec["checkmsg"], subs
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
            buf.append(ERASE_WRITE)
        buf.extend(
            write_control_character(
                reset_mdts=self.reset_mdts,
                keyboard_restore=self.keyboard_restore,
                sound_alarm=self.sound_alarm,
            )
        )
        for item in self.items:
            item.render(buf, color=color)
        if self.cursor_at is not None:
            _emit_sba(buf, self.cursor_at[0], self.cursor_at[1])
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
