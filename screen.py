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

    def render(self, buf: bytearray) -> None:
        _emit_sba(buf, self.row, self.col)
        buf.append(SF)
        buf.append(field_attribute(display=self.intensity, protected=True))
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

    @property
    def data_addr(self) -> int:
        """Linear buffer address (row*80 + col) where this field's data starts."""
        return self.row * 80 + (self.col + 1)

    def render(self, buf: bytearray) -> None:
        display = DisplayIntensity.NON_DISPLAY if self.hidden else self.intensity
        ftype = FieldType.NUMERIC if self.numeric else FieldType.ALPHANUMERIC
        _emit_sba(buf, self.row, self.col)
        buf.append(SF)
        buf.append(
            field_attribute(
                display=display,
                protected=False,
                field_type=ftype,
                mdt=self.mdt,
            )
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
    # Field name (upper) → {"checkmsg": id, "checks": [...]}, from a variable's
    # <varclass> validation (<checkl>/<checki>). Metadata: not rendered.
    validations: Dict[str, dict] = _dc_field(default_factory=dict)

    def add(self, item) -> "Screen":
        self.items.append(item)
        return self

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

    def render(self) -> bytes:
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
            item.render(buf)
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
