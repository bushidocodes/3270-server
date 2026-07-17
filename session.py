"""The TSO/ISPF application, split out of the protocol layer (#351).

This module is the *application* half of what used to be server.py: the TSO
logon flow, the ISPF Primary Option Menu loop, every ``_show_*`` panel
behaviour, and the declarative selection routing (#55). It talks to the client
exclusively through a **Transport** port — the two operations the session loops
actually perform at the seam, no more:

- ``send(data)`` — put one fully rendered 3270 record (already IAC-escaped and
  IAC-EOR-terminated by the render layer) on the wire. Full screens and the
  in-place partial menu-message Write both leave through this one call.
- ``read_input()`` — block for the next parsed AID reply as ``(aid, fields,
  cursor)``, or ``None`` on disconnect. Everything protocol-shaped lives behind
  it: Telnet/TN3270E framing, the 3270 reply codec (server._parse_3270_reply),
  RESPONSES acknowledgements, and the SYSREQ/ATTN keys. A SYSREQ-session LOGOFF
  propagates out of it as the protocol layer's session-teardown exception,
  unwinding every nested panel loop at once — this module never catches it.

The real transport (:class:`server.SocketTransport`) wraps the negotiated
socket — plain, TLS-wrapped, or a TN3270EStream — and :func:`server.handle_client`
hands it to :func:`run` once negotiation settles. A test can drive the whole
application with an in-memory fake transport instead: no sockets, no emulator
(see test_session.py). This module therefore imports the render model
(:mod:`screen`), the DTL loader (:mod:`dtl`) and the pure codec (:mod:`ds3270`),
but never ``socket``, ``ssl``, or :mod:`server`.
"""
import binascii
from datetime import datetime

from ds3270 import (
    DisplayIntensity,
    SessionContext,
    aid_to_string,
    current_session,
)
from screen import Color, Field, Highlight, Screen, Text
from dtl import load_panel, load_message_member


# Credentials — keys are uppercase userids
_CREDENTIALS = {
    "IBMUSER": "SYS1",
    "TESTUSER": "RACF",
}
# Passwords are stored and compared uppercase (default RACF behavior without MIXEDCASE option)

# Field addresses (row * width + col_after_sf) for fields the server reads back.
# The logon and ISPF menu panels always render on the *default* 24x80
# presentation space, so their addresses use that width (a panel shown on a
# wider alternate screen must use Screen.field_addr, which keys by the real
# Screen.width — see #347).
# TSO logon panel (auto-flow, panels/logon.dtl): the LOGON-parameter entry fields
# are in the left column with the SF (attribute) byte at col 15, data at col 16 —
# Userid on row 4, Password row 5, Procedure row 6. These must track logon.dtl;
# they are also what redact_fields() masks, so a stale password address would leak
# the password into the debug log.
DEFAULT_COLS = 80
LOGON_USERID_SF_COL = 15
LOGON_USERID_ROW = 4
LOGON_PASSWORD_SF_COL = 15
LOGON_PASSWORD_ROW = 5
LOGON_PROC_SF_COL = 15
LOGON_PROC_ROW = 6

LOGON_USERID_ADDR = LOGON_USERID_ROW * DEFAULT_COLS + (LOGON_USERID_SF_COL + 1)
LOGON_PASSWORD_ADDR = LOGON_PASSWORD_ROW * DEFAULT_COLS + (LOGON_PASSWORD_SF_COL + 1)
LOGON_PROC_ADDR = LOGON_PROC_ROW * DEFAULT_COLS + (LOGON_PROC_SF_COL + 1)

# ISPF menu: Option ===> input SF at col 13, data at col 14
ISPF_OPTION_SF_COL = 13
ISPF_OPTION_ROW = 2
ISPF_OPTION_ADDR = ISPF_OPTION_ROW * DEFAULT_COLS + (ISPF_OPTION_SF_COL + 1)


def redact_fields(fields):
    """Return a copy of a parsed fields dict with the password field redacted.

    handle_client() logs the parsed fields for debugging, but the dict contains
    the decoded plaintext password keyed by LOGON_PASSWORD_ADDR. Emitting it to
    stdout would leak the password on every login, defeating the safe-logging
    guard in the protocol layer
    (server.read_client_input). Mask it before logging.
    """
    return {
        k: ("***" if k == LOGON_PASSWORD_ADDR else v)
        for k, v in fields.items()
    }


def _wants_color(model) -> bool:
    """Whether to render colour for this session — a 3279-family or any
    extended-data-stream (-E) terminal (see parse_terminal_type)."""
    return bool(model is not None and model.color)


def _send_screen(transport, screen, color: bool = None, ctx: SessionContext = None):
    """Render a screen.Screen to the 3270 data stream and send it. ``ctx`` is
    the session's explicit context (#352): its code page encodes the text and
    its colour capability enables extended (colour/highlight) attributes; a
    caller that passed none gets the ambient thread-local shim, as before. An
    explicit ``color`` overrides the context's. A mono session leaves ``color``
    false, so the bytes are unchanged."""
    if ctx is None:
        ctx = current_session()
    data = screen.render(color=color, session=ctx)
    print("TX:", binascii.hexlify(data))
    transport.send(data)


def send_tso_logon(transport, error_msg: str = None, model=None, alarm=False,
                   ctx: SessionContext = None):
    """Send the z/OS TSO/E LOGON panel, rendered from panels/logon.dtl. On a
    colour terminal the panel's declared colours are emitted and a logon error
    is shown in red. ``alarm`` sounds the terminal alarm (an error message whose
    <msg> asks for it, e.g. a bad password), the way real ISPF beeps on error."""
    color = ctx.color if ctx is not None else _wants_color(model)
    screen = load_panel("logon")
    if error_msg:
        col = max(0, (80 - len(error_msg)) // 2)
        screen.add(Text(19, col, error_msg, DisplayIntensity.HIGH,
                        color=Color.RED if color else None))
        screen.sound_alarm = alarm
    _send_screen(transport, screen, color=color, ctx=ctx)
    return screen


def send_ispf_menu(transport, userid: str, short_msg: str = None,
                   ctx: SessionContext = None):
    """Send the ISPF Primary Option Menu, rendered from panels/ispf.dtl."""
    time_str = datetime.now().strftime("%H:%M")
    screen = load_panel("ispf", ZUSER=userid.ljust(8), ZTIME=time_str)
    if short_msg:
        screen.add(Text(2, 25, short_msg[:54], DisplayIntensity.HIGH))
        # A menu message is always an error (INVALID OPTION / NOT YET
        # IMPLEMENTED); real ISPF sounds the alarm on it, as the logon errors do.
        screen.sound_alarm = True
    _send_screen(transport, screen, ctx=ctx)
    return screen


# The ISPF menu message occupies row 2 from column 25 to the right edge (the
# start-field byte sits at column 25, its 54 characters of text at 26..79).
_MENU_MSG_ROW, _MENU_MSG_COL, _MENU_MSG_WIDTH = 2, 25, 54


def _update_menu_message(transport, screen, short_msg, ctx: SessionContext = None):
    """Redisplay the ISPF menu's message line *in place* with a plain Write,
    the way real ISPF does — so the option the user typed (and the rest of the
    panel) stays put instead of being repainted and cleared.

    The message text is blank-filled to a fixed width so a shorter message fully
    overwrites a longer previous one, and the cursor is returned to the command
    field. ``screen`` is the Screen from the last full render (unchanged layout),
    reused to locate the command field."""
    if ctx is None:
        ctx = current_session()
    text = (short_msg or "")[:_MENU_MSG_WIDTH].ljust(_MENU_MSG_WIDTH)
    msg_item = Text(_MENU_MSG_ROW, _MENU_MSG_COL, text, DisplayIntensity.HIGH)
    # Beep on an error message (like real ISPF), stay silent when clearing it.
    screen.sound_alarm = bool(short_msg)
    cursor_at = None
    if screen.command_field is not None:
        cf = screen.command_field
        cursor_at = (cf.row, cf.col + 1)   # the command field's data start
    data = screen.render_partial([msg_item], cursor_at=cursor_at, session=ctx)
    print("TX:", binascii.hexlify(data))
    transport.send(data)


def _dialog_vars(userid: str, model: "TerminalModel" = None):  # noqa: F821
    """The live ISPF dialog variables shown by Dialog Test (option 7), as
    ``{vname, vvalue}`` rows for the panel's ``<lstfld>`` table. These are real
    ISPF system-variable names with this session's current values — including
    ``ZTERM``, the terminal model negotiated at connect time."""
    now = datetime.now()
    term = model.term_type if model else "IBM-3278-2"
    return [
        {"vname": "ZUSER",   "vvalue": userid},
        {"vname": "ZPREFIX", "vvalue": userid},
        {"vname": "ZAPPLID", "vvalue": "ISR"},
        {"vname": "ZTIME",   "vvalue": now.strftime("%H:%M")},
        {"vname": "ZDATE",   "vvalue": now.strftime("%y/%m/%d")},
        {"vname": "ZSCREEN", "vvalue": "1"},
        {"vname": "ZTERM",   "vvalue": term},
        {"vname": "ZENVIR",  "vvalue": "ISPF 7.1"},
        {"vname": "ZKEYS",   "vvalue": "DLGTKEYS"},
    ]


def _run_tso_command(cmd: str) -> str:
    """Run a TSO command entered in the Command Shell (option 6) and return the
    response line. A small set of real commands is handled (TIME, READY); any
    other verb yields the authentic 'command not found' message TSO issues."""
    verb = cmd.split()[0].upper()
    if verb in ("TIME",):
        now = datetime.now()
        # Mirrors the real TSO TIME message (Julian date, day of week).
        return (now.strftime("IKJ56650I TIME-%I:%M:%S %p")
                + now.strftime(" DATE-%Y.%j DAY-").upper()
                + now.strftime("%A").upper())
    if verb in ("READY", "LISTBC", "PROFILE"):
        return "READY"
    return f"IKJ56500I COMMAND {verb} NOT FOUND"


# Sentinel returned by _await_action when the panel should be left (the client
# disconnected, or a PF3/PF15-style leave key was pressed).
_LEAVE = object()


def _await_action(transport, screen, ctx: SessionContext = None):
    """Send ``screen`` and read one response, handling the cases every simple
    sub-panel loop shares: a disconnect or a leave key (PF3/PF15) both yield
    :data:`_LEAVE`; PF1 with a help panel shows the help overlay and redisplays.
    Anything else is returned as ``(aid_str, fields, cursor)`` for the caller."""
    while True:
        _send_screen(transport, screen, ctx=ctx)
        result = transport.read_input()
        if result is None:
            return _LEAVE
        aid, fields, cursor = result
        aid_str = aid_to_string(aid)
        if screen.command_for(aid_str) in _LEAVE_COMMANDS:
            return _LEAVE
        if aid_str == "PF1":
            # Context-sensitive HELP: the field the cursor is on, else the panel.
            help_panel = screen.help_for(cursor) or screen.help
            if help_panel:
                _show_help(transport, help_panel, ctx=ctx)
                continue  # redisplay this panel after help
        return aid_str, fields, cursor


def _show_command_shell(transport, ctx: SessionContext = None):
    """ISPF option 6: a TSO Command Shell. Loops reading a command from the
    panel's <cmdarea>, running it, and showing the response, until the user
    presses PF3 (or PF1 for help). Enter runs the typed command and stays."""
    msg = ""
    while True:
        screen = load_panel("command", CMDMSG=msg)
        action = _await_action(transport, screen, ctx=ctx)
        if action is _LEAVE:
            return
        _aid_str, fields, _cursor = action
        cmd = (screen.command_value(fields) or "").strip()
        msg = _run_tso_command(cmd) if cmd else ""


def _library_members():
    """The ISPF panel library (ISPPLIB) as member rows for the memlist table —
    the real panels/*.dtl files, so the Library utility lists what's actually
    there. Each row is {mname, mtype, mdesc}."""
    import os
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "panels")
    desc = {
        "logon": "TSO/E logon panel",
        "ispf": "Primary Option Menu",
        "settings": "Settings (action bar)",
        "utility": "Utility Selection Panel",
        "command": "TSO Command Shell",
        "dlgtest": "Dialog Test - Variables",
        "tabtest": "Dialog Test - Table Input",
        "memlist": "Library - Member List",
        "tsohelp": "Logon help",
        "sizehelp": "Logon Size field help",
        "loglisthelp": "Log/List defaults help",
        "ispfhelp": "ISPF menu help",
        "viewentry": "View entry panel",
        "editentry": "Edit entry panel",
        "browse": "Browse frame",
        "foreground": "Foreground selection menu",
        "batch": "Batch selection menu",
        "ibmprod": "IBM Products menu",
        "sclm": "SCLM main menu",
        "workplace": "Object/Action Workplace",
        "zsystem": "z/OS System applications",
        "zuser": "z/OS User applications",
    }
    try:
        names = sorted(f[:-4] for f in os.listdir(base) if f.endswith(".dtl"))
    except OSError:
        names = []
    return [{"mname": n.upper(), "mtype": "Panel(DTL)", "mdesc": desc.get(n, "")}
            for n in names]


def _member_path(member: str):
    """Resolve a panel-library member name to its panels/<name>.dtl path, or
    None if the name is invalid or no such member exists. The name is restricted
    to ISPF member syntax (1-8 ASCII alphanumerics, leading letter), which also
    makes path traversal impossible — no dots or separators can appear."""
    import os
    if not (member and len(member) <= 8 and member.isascii()
            and member.isalnum() and member[0].isalpha()):
        return None
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "panels")
    path = os.path.join(base, f"{member.lower()}.dtl")
    return path if os.path.isfile(path) else None


def _screen_size(model):
    """The presentation-space ``(rows, cols)`` to render on for this terminal:
    the model's alternate size for models 3/4/5 (32x80, 43x80, 27x132), else the
    24x80 default that every model shares."""
    if model is None:
        return 24, 80
    return model.alt_rows, model.alt_cols


def _scroll_amount(value: str, page: int, total: int = None,
                   cursor_offset: int = None) -> int:
    """Rows to move for one scroll (PF7/PF8) press, from an ISPF ``SCROLL`` amount.

    Mirrors ISPF's ``SCROLL ===>`` field values: ``PAGE`` a full visible page,
    ``HALF`` half a page, ``MAX`` the whole set (``total`` if known, else a large
    number the caller clamps), ``CSR`` the distance from the window top to the
    cursor line (``cursor_offset``), or a literal number ``n``. An empty or
    unrecognised value defaults to ``PAGE`` — as ISPF does."""
    v = (value or "").strip().upper()
    if v.isdigit():
        return max(1, int(v))
    if v in ("HALF", "H"):
        return max(1, page // 2)
    if v in ("MAX", "M"):
        return total if total is not None else 10 ** 9
    if v in ("CSR", "CURSOR"):
        return cursor_offset if cursor_offset else page
    return page  # PAGE / P / blank / unknown


def _show_browse(transport, member: str, path: str, verb: str = "BROWSE",
                 model=None, ctx: SessionContext = None):
    """Browse a panel-library member's source (ISPF option 1 View, or option 2
    Edit with verb="EDIT"). Renders the file's lines below a header, with the
    footer rule on the last row, paging with PF7/PF8; PF3/PF15 returns to the
    entry panel. On a larger terminal (model 3/4/5) the panel is drawn on the
    alternate screen, so a taller/wider screen shows more lines per page."""
    if ctx is None:
        ctx = current_session()
    rows, cols = _screen_size(model)
    alternate = rows > 24 or cols > 80     # bigger than the 24x80 default space
    page = rows - 2                        # row 0 is the header, the last row the footer
    line_width = cols - 1                  # leave the attribute byte a column
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    top = 0
    while True:
        top = max(0, min(top, max(0, len(lines) - 1)))
        shown_end = min(top + page, len(lines))
        title = (f"{verb}    ISPF.ISPPLIB({member.upper()})".ljust(cols - 25)
                 + f"Line {top + 1:08d}")[:line_width]
        foot = (f"Lines {top + 1}-{shown_end} of {len(lines)}"
                "     PF7=Up  PF8=Down  PF3=Exit")[:line_width]
        screen = load_panel("browse")
        screen.width, screen.depth, screen.alternate = cols, rows, alternate
        # The header status line and footer rule are a space-padded status band
        # the server positions on the first/last rows (the browse panel itself is
        # a title-less key-list frame — see panels/browse.dtl).
        screen.add(Text(0, 0, title, DisplayIntensity.HIGH))
        for i, ln in enumerate(lines[top:top + page]):
            # Browsed content is arbitrary; drop any byte the session's EBCDIC
            # code page can't encode so the render can never crash.
            safe = ctx.decode(ctx.encode(ln, errors="replace"))
            screen.add(Text(1 + i, 0, safe[:line_width]))
        screen.add(Text(rows - 1, 0, foot, DisplayIntensity.HIGH))
        _send_screen(transport, screen, ctx=ctx)
        result = transport.read_input()
        if result is None:
            return
        aid, _, _ = result
        aid_str = aid_to_string(aid)
        if screen.command_for(aid_str) in _LEAVE_COMMANDS:
            return
        if aid_str in ("PF8", "PF20"):
            top += page
        elif aid_str in ("PF7", "PF19"):
            top -= page
        # any other key just redisplays the current page


def _show_view(transport, entry_panel: str = "viewentry", verb: str = "BROWSE",
               model=None, ctx: SessionContext = None):
    """ISPF option 1 (View) / option 2 (Edit): prompt for a panel-library member
    on ``entry_panel`` and open its source (as ``verb`` — BROWSE or EDIT). An
    unknown member is reported via &VIEWMSG; PF3/PF15 returns."""
    msg = ""
    while True:
        screen = load_panel(entry_panel, VIEWMSG=msg)
        action = _await_action(transport, screen, ctx=ctx)
        if action is _LEAVE:
            return
        _aid_str, fields, _cursor = action
        member = (fields.get(screen.field_addr("member")) or "").strip()
        if not member:
            continue
        path = _member_path(member)
        if path:
            _show_browse(transport, member, path, verb=verb, model=model, ctx=ctx)
            msg = ""
        else:
            msg = f"MEMBER {member.upper()} NOT FOUND"


def _show_member_list(transport, model=None, ctx: SessionContext = None):
    """Utilities -> Library (3.1): the panel-library member list, with ISPF
    point-and-shoot — put the cursor on a member row and press Enter to browse
    that member's source. PF7/PF8 page the list; PF3/PF15 returns. On a larger
    terminal (model 3/4/5) the list is drawn on the alternate screen, so more
    members are shown per page."""
    rows, cols = _screen_size(model)
    alternate = rows > 24 or cols > 80
    # The auto-flowed <lstfld> data rows start at row 5; leave the last two rows
    # for the footer and its rule. That's 17 rows on a model 2, more on a larger
    # screen.
    page = rows - 7
    members = _library_members()
    top = 0
    while True:
        top = max(0, min(top, max(0, len(members) - 1)))
        window = members[top:top + page]
        foot = (f"Member {top + 1}-{top + len(window)} of {len(members)}"
                "     PF7=Up  PF8=Down  PF3=Exit")[:cols - 1]
        # row_offset/row_total make the panel's own "ROW x TO y OF z" scroll status
        # and its "BOTTOM OF DATA" marker reflect the real window over the full set
        # (so page 1 of a multi-page list is not falsely marked BOTTOM OF DATA).
        screen = load_panel("memlist", rows=window,
                            screen_rows=rows, screen_cols=cols,
                            row_offset=top, row_total=len(members))
        screen.alternate = alternate
        screen.add(Text(rows - 2, 0, foot, DisplayIntensity.HIGH))
        screen.add(Text(rows - 1, 0, "-" * (cols - 1), DisplayIntensity.HIGH))
        # Map each rendered data row to its member (the member name renders as a
        # Text in the Name column); the cursor's row then selects the member.
        member_by_row = {}
        for m in window:
            for it in screen.items:
                if isinstance(it, Text) and it.text == m["mname"]:
                    member_by_row[it.row] = m["mname"]
                    break
        action = _await_action(transport, screen, ctx=ctx)
        if action is _LEAVE:
            return
        aid_str, _fields, cursor = action
        # PF8/PF20 page down, PF7/PF19 up, by the SCROLL amount (member lists have
        # no SCROLL field, so PAGE — the default). The offset is clamped to the set.
        amount = _scroll_amount("PAGE", page, total=len(members))
        if aid_str in ("PF8", "PF20"):
            top += amount
        elif aid_str in ("PF7", "PF19"):
            top -= amount
        elif aid_str == "PF1":   # HELP: the member cell's <lstcol help=>, else panel
            help_panel = screen.help_for(cursor) or screen.help
            if help_panel:
                _show_help(transport, help_panel, ctx=ctx)
        elif _is_cursor_select(aid_str) and cursor is not None:
            member = member_by_row.get(cursor // cols)   # width-aware row decode
            if member:
                path = _member_path(member)
                if path:
                    _show_browse(transport, member, path, model=model, ctx=ctx)
        # otherwise just redisplay the current page


def _show_dialog_test(transport, userid=None, model=None, ctx: SessionContext = None):
    """ISPF Dialog Test (option 7). Displays the session's dialog variables
    (read-only, dlgtest.dtl); PF5 opens the Table Input scratch panel, which
    exercises the ``<lstfld>`` table-input read-back (#249). Enter redisplays;
    PF3/PF15 returns to the Primary Option Menu. PF1 shows help."""
    while True:
        screen = load_panel("dlgtest", rows=_dialog_vars(userid, model))
        action = _await_action(transport, screen, ctx=ctx)
        if action is _LEAVE:
            return
        aid_str, _fields, _cursor = action
        if aid_str == "PF5":
            _show_table_input(transport, ctx=ctx)
        # Enter (or any other non-leave key) just redisplays the variables.


def _show_table_input(transport, model=None, ctx: SessionContext = None):
    """Dialog Test scratch table (tabtest.dtl): an input ``<lstfld>`` the user
    types into. On Enter the modified cells are read back *per model row* via
    :meth:`screen.Screen.read_table_rows` — despite every row's cell in a column
    sharing the column DATAVAR — and the non-blank rows are echoed via &TABMSG,
    so the round-trip is visible on the terminal. This is the served consumer
    that exercises the table-input read-back path end-to-end (#249); PF3 returns.

    The rows are re-seeded from the read-back on each Enter, so what the user
    typed persists across redisplays (the table holds its state, like TBDISPL)."""
    rows = [{"tkey": "", "tval": ""} for _ in range(4)]
    msg = ""
    while True:
        screen = load_panel("tabtest", rows=rows, TABMSG=msg)
        # Land the cursor on the first table input cell (the panel has no other
        # input), so the user types straight into the table.
        first = next((f for f in screen.items
                      if isinstance(f, Field) and f.row_index is not None), None)
        if first is not None:
            screen.cursor_at = (first.row, first.col + 1)
        _send_screen(transport, screen, ctx=ctx)
        result = transport.read_input()
        if result is None:
            return
        aid, fields, cursor = result
        aid_str = aid_to_string(aid)
        if screen.command_for(aid_str) in _LEAVE_COMMANDS:
            return
        if aid_str == "PF1":
            help_panel = screen.help_for(cursor) or screen.help
            if help_panel:
                _show_overlay(transport, help_panel, ctx=ctx)
            continue
        # Read the whole table back per row (modified cells override the rendered
        # defaults) and re-seed from it, so edits persist.
        rows = screen.read_table_rows(fields)
        # REQUIRED validation (<lstcol REQUIRED=YES MSG=id>): a modified row whose
        # required cell is blank surfaces the column's MSG and redisplays, without
        # committing — the ISPF VER(var, NONBLANK, MSG=id) behaviour.
        errors = screen.table_required_errors(fields)
        if errors:
            row_i, _dv, m = errors[0]
            msg = f"{m or 'INPUT REQUIRED'}: enter a Key on row {row_i + 1}"
            continue
        filled = [f"{r.get('tkey', '').strip()}={r.get('tval', '').strip()}"
                  for r in rows
                  if r.get("tkey", "").strip() or r.get("tval", "").strip()]
        msg = (f"Read {len(filled)} row(s): " + ", ".join(filled)) if filled \
            else "No rows entered"


def _show_submenu(transport, panel_name: str, initial=None, userid=None,
                  model=None, ctx: SessionContext = None):
    """Display a nested selection menu (e.g. option 3, Utilities) and drive it
    like the Primary Option Menu: read the option from the panel's <cmdarea> and
    route it through the panel's own )PROC (Screen.selection_targets, #55). An
    implemented leaf runs its behaviour; any other declared choice reports back
    via &SELMSG. PF3/PF15 returns; PF1 shows help.

    ``initial`` pre-selects a sub-option without displaying the menu first, so a
    dotted jump from the parent (``3.1``) lands straight on the leaf; PF3 from
    there falls back to this menu."""
    msg = ""
    pending = (initial or "").strip().upper() or None
    while True:
        screen = load_panel(panel_name, SELMSG=msg)
        if pending is not None:
            opt, pending = pending, None
        else:
            action = _await_action(transport, screen, ctx=ctx)
            if action is _LEAVE:
                return
            aid_str, fields, cursor = action
            opt = (screen.command_value(fields) or "").strip().upper()
            # Point-and-shoot: with nothing typed, Enter (or Cursor Select) on a
            # <ps> phrase sets the command variable, else on a choice row selects it.
            if not opt and _is_cursor_select(aid_str):
                ps = screen.command_point_and_shoot(cursor)
                opt = ps.strip().upper() if ps else (screen.selection_at(cursor) or "")
            if not opt:
                continue
        head = opt.split(".", 1)[0]
        tail = opt.split(".", 1)[1] if "." in opt else None
        # Only route an option the user can actually see and pick: a HIDE/HIDEX or
        # UNAVAIL choice is absent from `selections`, so its )PROC target must not
        # be reachable by typing it either.
        target = screen.selection_targets.get(head)
        if target is not None and head in screen.selections:
            # A leaf runs its behaviour; EXIT (or a nested return) falls back to
            # this menu, and a declared-but-unhandled leaf reports via &SELMSG.
            leaving = _run_selection(transport, target, tail, userid, model,
                                     ctx=ctx)
            if leaving:
                return
            elif leaving is False:
                msg = ""
            else:
                choice = screen.selections.get(head, "").strip()
                msg = f"OPTION {head} ({choice}) NOT YET IMPLEMENTED"
        elif head in screen.selections:
            msg = f"OPTION {head} ({screen.selections[head].strip()}) NOT YET IMPLEMENTED"
        else:
            msg = f"INVALID OPTION: {opt}"


def _show_help(transport, panel_name: str, ctx: SessionContext = None):
    """Display a help/tutorial panel, paging it with PF7/PF8 (and PF10/PF11, the
    ISPF PrvPage/NxtPage) when its content overflows the 24-row screen (#281).

    A help panel that fits is shown by :func:`_show_overlay` exactly as before. A
    taller one is rendered to a large virtual screen (so the flow is not clipped),
    then a window of its content lines is drawn below the fixed title, with a
    "More: - +" scroll indicator on the last row. Enter or PF3 dismisses it."""
    import dataclasses

    HELP_ROWS, HELP_COLS = 24, 80
    VIRT = 500  # a virtual depth tall enough that no bundled help panel clips

    # Measure the panel's content on a tall screen. Row 0 is the title; the
    # bottom-anchored <botinst> sits near the virtual foot — exclude both, leaving
    # the flowed body as the scrollable content.
    tall = load_panel(panel_name, screen_rows=VIRT, screen_cols=HELP_COLS)
    body = [it for it in tall.items
            if getattr(it, "row", None) is not None and 0 < it.row < VIRT - 4]
    page_h = HELP_ROWS - 2            # rows 1..HELP_ROWS-2 hold content
    if not body:
        return _show_overlay(transport, panel_name, ctx=ctx)
    content_top = min(it.row for it in body)
    content_height = max(it.row for it in body) - content_top + 1
    if content_height <= page_h:      # fits on a 24-row screen → unchanged path
        return _show_overlay(transport, panel_name, ctx=ctx)

    title = [it for it in tall.items if getattr(it, "row", None) == 0]
    max_offset = content_height - page_h
    offset = 0
    while True:
        offset = max(0, min(offset, max_offset))
        screen = Screen(width=HELP_COLS, depth=HELP_ROWS)
        screen.title = tall.title
        screen.help = tall.help
        screen.keylist = tall.keylist   # so PF3/PF15 EXIT is recognised
        for it in title:
            screen.add(it)
        for it in body:
            r = it.row - content_top - offset
            if 0 <= r < page_h:
                screen.add(dataclasses.replace(it, row=1 + r))
        more_up, more_down = offset > 0, offset + page_h < content_height
        marker = "More:" + (" -" if more_up else "") + (" +" if more_down else "")
        indicator = (f"{marker}    PF7=Up  PF8=Down  PF3=Return")[:HELP_COLS - 1]
        screen.add(Text(HELP_ROWS - 1, 0, indicator, DisplayIntensity.HIGH))
        _send_screen(transport, screen, ctx=ctx)
        result = transport.read_input()
        if result is None:
            return
        aid, _fields, _cursor = result
        aid_str = aid_to_string(aid)
        if screen.command_for(aid_str) in _LEAVE_COMMANDS or aid_str == "Enter":
            return                       # PF3/PF15 or Enter dismiss the help
        if aid_str in ("PF8", "PF20", "PF11"):     # page down / NxtPage
            offset += page_h
        elif aid_str in ("PF7", "PF19", "PF10"):   # page up / PrvPage
            offset -= page_h
        # any other key just redisplays the current page


def _show_overlay(transport, panel_name: str, rows=None, enter_returns=True,
                  ctx: SessionContext = None):
    """Display an overlay panel (help or sub-panel) and wait for the user to
    leave it. The underlying panel is re-sent by the caller's loop on return —
    mirroring ISPF's PF1 HELP and option-select behaviour.

    ``rows`` populates a ``<lstfld>`` list/table on the panel (e.g. the Dialog
    Test variable display), passed straight through to ``load_panel``.

    ``enter_returns`` controls what a bare Enter does. Help panels dismiss on
    Enter (the default); a read-only display/table panel (member list, variable
    display) sets it False so Enter just redisplays and only PF3/PF15 exits —
    the way ISPF treats those panels.
    """
    abc_idx = None  # which action-bar choice the cursor is parked on, or None
    while True:
        screen = load_panel(panel_name, rows=rows)
        if abc_idx is not None and screen.action_bar:
            ch = screen.action_bar[abc_idx % len(screen.action_bar)]
            screen.cursor_at = (ch["row"], ch["col"] + 1)  # on the choice label
        _send_screen(transport, screen, ctx=ctx)
        result = transport.read_input()
        if result is None:
            return
        aid, fields, cursor = result
        aid_str = aid_to_string(aid)
        if screen.command_for(aid_str) in _LEAVE_COMMANDS:
            return
        if aid_str == "PF1":
            # HELP inside an overlay: the action-bar choice (or field) the cursor
            # is on, else this panel's own help. (Overlays ignored PF1 before.)
            help_panel = screen.help_for(cursor) or screen.help
            if help_panel:
                _show_help(transport, help_panel, ctx=ctx)
            continue
        if screen.action_bar and aid_str in ("PF10", "PF11"):
            # F10/F11 move the cursor left/right along the action-bar choices
            # (jumping onto the bar from elsewhere), wrapping around.
            n = len(screen.action_bar)
            if abc_idx is None:
                abc_idx = 0 if aid_str == "PF11" else n - 1
            else:
                abc_idx = (abc_idx + 1) % n if aid_str == "PF11" else (abc_idx - 1) % n
            continue
        if _is_cursor_select(aid_str):
            # Point-and-shoot: Enter (or Cursor Select) with the cursor on an
            # action-bar choice opens that choice's pull-down; otherwise a plain
            # Enter dismisses the overlay (help panels) or just redisplays it
            # (display panels). Cursor Select only fires on a detectable field, so
            # off a choice it just redisplays.
            choice = screen.action_choice_at(cursor)
            if choice and choice.get("pdc"):
                action = _show_pulldown(transport, screen, choice, ctx=ctx)
                if action is None:
                    return  # client disconnected
                if _run_pdc_action(transport, screen, action, ctx=ctx):
                    return  # the action left the overlay (e.g. EXIT)
                continue
            if aid_str == "Enter" and enter_returns:
                return
            continue  # display panel: Enter stays; only PF3/PF15 exits


def _pdc_item_text(row, col, number, item, inner):
    """Build one framed pull-down item line ``| N. label |``, underlining the
    item's mnemonic letter (DTL ``<M>``) when it has one. Mono renders identically
    to the plain framed line, so only colour/extended terminals show the underline.

    An unavailable item (DTL ``<pdc unavail>``) drops to NORMAL intensity — the
    3270's only sub-high de-emphasis — and never underlines a mnemonic, matching how
    ``<choice unavail>`` greys a selection choice."""
    label = item["label"]
    t = f"{number}. {label}"
    framed = "|" + (" " + t).ljust(inner) + "|"
    intensity = DisplayIntensity.NORMAL if item.get("unavail") else DisplayIntensity.HIGH
    m = item.get("mnemonic")
    if m is not None and not item.get("unavail"):
        pos = 2 + (len(t) - len(label)) + m      # past ``| `` and the ``N. `` prefix
        if 0 <= pos < len(framed):
            runs = [(framed[:pos], None, None),
                    (framed[pos], None, Highlight.UNDERSCORE),
                    (framed[pos + 1:], None, None)]
            return Text.rich(row, col, [r for r in runs if r[0]],
                             intensity=DisplayIntensity.HIGH)
    return Text(row, col, framed, intensity)


def _show_pulldown(transport, screen, choice, ctx: SessionContext = None):
    """Overlay a choice's pull-down menu and wait for the user to act on it.

    Returns the selected pull-down item's action string when the cursor is on an
    item and Enter is pressed; ``""`` if the pull-down is closed without a
    selection; or ``None`` if the client disconnected.
    """
    pdc = choice["pdc"]
    # <pdsep> entries are non-selectable divider rows; only the real choices are
    # numbered (the numbering runs continuously across a separator).
    items = [p for p in pdc if not p.get("separator")]
    texts = [f"{n}. {p['label']}" for n, p in enumerate(items, 1)]
    inner = max((len(t) for t in texts), default=0) + 2
    top = choice["row"] + 1
    col = choice["col"]
    border = "+" + "-" * inner + "+"
    divider = "|" + "-" * inner + "|"
    screen.add(Text(top, col, border, DisplayIntensity.HIGH))
    action_by_row = {}
    help_by_row = {}
    checked_row = None       # row of the current (CHECKVAR-matched) item, if any
    first_row = None         # first *available* item, for the default cursor landing
    number = 0
    row = top
    for item in pdc:
        row += 1
        if item.get("separator"):
            screen.add(Text(row, col, divider, DisplayIntensity.HIGH))
            continue
        number += 1
        screen.add(_pdc_item_text(row, col, number, item, inner))
        if item.get("help"):
            help_by_row[row] = item["help"]
        if item.get("unavail"):
            continue          # shown (dimmed) but not selectable: no action mapping
        action_by_row[row] = item["action"]
        if first_row is None:
            first_row = row
        if item.get("checked"):
            checked_row = row
    screen.add(Text(row + 1, col, border, DisplayIntensity.HIGH))
    # Land on the current item (CHECKVAR match) if there is one, else the first
    # selectable item — never on an unavailable/greyed row.
    land = checked_row or first_row or (top + 1)
    screen.cursor_at = (land, col + 1)

    while True:
        _send_screen(transport, screen, ctx=ctx)
        result = transport.read_input()
        if result is None:
            return None
        aid, _, cursor = result
        aid_str = aid_to_string(aid)
        crow, ccol = divmod(cursor, 80) if cursor is not None else (None, None)
        on_item = crow in action_by_row and col <= ccol <= col + inner + 1
        if aid_str == "PF1":  # HELP for the item under the cursor
            if on_item and crow in help_by_row:
                _show_help(transport, help_by_row[crow], ctx=ctx)
            continue  # redisplay the pull-down either way
        if _is_cursor_select(aid_str) and on_item:
            return action_by_row[crow]
        return ""  # any other key closes the pull-down without selecting


def _run_pdc_action(transport, screen, action, ctx: SessionContext = None) -> bool:
    """Run a selected pull-down action. Returns True if it should leave the
    overlay (an EXIT-family command), False otherwise (the panel is redisplayed).

    The action is the standard DTL ``<action run=command>`` value — a bare command
    like ``exit`` or ``help``, as the guide's action-bar pull-downs use."""
    act = (action or "").strip().lower()
    if act in ("exit", "end", "return", "cancel"):
        return True
    if act == "help" and screen.help:
        _show_help(transport, screen.help, ctx=ctx)
        return False
    return False  # passthru / unknown / no selection: just redisplay


def _is_cursor_select(aid_str: str) -> bool:
    """Whether an AID means "act on the field under the cursor": Enter or the
    Cursor Select (selector-pen) key. Cursor Select means exactly "select what the
    cursor is on", which for our point-and-shoot menus is what Enter-on-a-row does,
    so the two share the cursor-selection paths (#104)."""
    return aid_str in ("Enter", "CursorSelect")


# ISPF commands that leave the current panel. A panel's <keyl> binds function
# keys (PF3/PF15) to one of these; the session loop acts on the resolved command
# rather than hard-coding key numbers.
_LEAVE_COMMANDS = {"EXIT", "END", "RETURN", "LOGOFF"}

# --- Declarative menu routing (#55) ------------------------------------------
# The ISPF primary menu's option -> behaviour routing is declared in ispf.dtl's
# )PROC (parsed into Screen.selection_targets, e.g. "1" -> "PGM(view)"). Each
# selection string's target name maps here to the Python behaviour that runs it:
# the routing decision lives in the panel, the behaviour stays in code. Nested
# selection sub-menus (foreground/batch/…) run uniformly through _show_submenu,
# so each supports the dotted jump (e.g. "9.2"). See docs/dtl-action-routing-plan.md.

def _submenu(panel):
    """A handler that opens a nested selection sub-menu panel (passing the dotted
    tail through as the sub-menu's initial option, and userid/model/ctx through so
    the sub-menu's own )PROC leaves can run)."""
    return lambda cs, tail=None, userid=None, model=None, ctx=None, **kw: \
        _show_submenu(cs, panel, initial=tail, userid=userid, model=model, ctx=ctx)


_SELECTION_HANDLERS = {
    "settings":   lambda cs, ctx=None, **kw: _show_overlay(cs, "settings", ctx=ctx),
    "workplace":  lambda cs, ctx=None, **kw: _show_overlay(cs, "workplace", ctx=ctx),
    "view":       lambda cs, model=None, ctx=None, **kw: _show_view(
                      cs, model=model, ctx=ctx),
    "edit":       lambda cs, model=None, ctx=None, **kw: _show_view(
                      cs, entry_panel="editentry", verb="EDIT", model=model, ctx=ctx),
    "cmdshell":   lambda cs, ctx=None, **kw: _show_command_shell(cs, ctx=ctx),
    "dlgtest":    lambda cs, userid=None, model=None, ctx=None, **kw: _show_dialog_test(
                      cs, userid=userid, model=model, ctx=ctx),
    # A utility sub-menu leaf: the Library list (utility.dtl's )PROC routes 1 here).
    "memberlist": lambda cs, model=None, ctx=None, **kw: _show_member_list(
                      cs, model=model, ctx=ctx),
    "utility":    _submenu("utility"),
    "foreground": _submenu("foreground"),
    "batch":      _submenu("batch"),
    "ibmprod":    _submenu("ibmprod"),
    "sclm":       _submenu("sclm"),
    "zsystem":    _submenu("zsystem"),
    "zuser":      _submenu("zuser"),
}


def _parse_selection(target):
    """Split a )PROC selection string ``KIND(name) …`` into ``(kind, name)`` —
    e.g. ``PGM(view)`` -> ``("PGM", "view")``. Anything after the first ``)``
    (a future ``PARM(...)``) is ignored for now."""
    kind, _, rest = target.partition("(")
    return kind.strip().upper(), rest.partition(")")[0].strip()


def _run_selection(transport, target, tail, userid, model, ctx: SessionContext = None):
    """Run the behaviour a )PROC selection string names. ``EXIT`` leaves ISPF;
    ``PANEL(x)``/``PGM(x)`` dispatch to the handler registered for ``x``. Returns
    True to leave, False after running a handler, or None if the target has no
    handler yet (the caller then shows a 'not implemented' message)."""
    if target.strip().upper() == "EXIT":
        return True
    _, name = _parse_selection(target)
    handler = _SELECTION_HANDLERS.get(name.lower()) if name else None
    if handler is None:
        return None
    handler(transport, tail=tail, userid=userid, model=model, ctx=ctx)
    return False

_message_catalog = None


def _messages():
    """Lazily load and cache the TSO message catalog (messages/tsomsgs.dtl)."""
    global _message_catalog
    if _message_catalog is None:
        _message_catalog = load_message_member("tsomsgs")
    return _message_catalog


def run(transport, model=None, ctx: SessionContext = None):
    """Run the TSO/ISPF application for one connection: the logon loop, then the
    ISPF Primary Option Menu loop — lifted verbatim from server.handle_client
    (#351). ``transport`` is the Transport port described in the module
    docstring; ``model`` is the negotiated TerminalModel (only its capability
    attributes are read, so a test may pass ``None`` for a plain 24x80 mono
    terminal). ``ctx`` is the explicit session context (#352) threaded through
    every panel render; a caller that passes none (an unmigrated caller, or a
    test) gets one assembled from the ambient thread-local shim — with the
    colour capability taken from ``model`` when one is given, matching what
    server.handle_client would have installed. Returns when the user logs off
    or the client disconnects."""
    if ctx is None:
        amb = current_session()
        ctx = SessionContext(
            code_page=amb.code_page,
            color=_wants_color(model) if model is not None else amb.color,
            model=model,
        )
    while True:
        # Logon loop
        error_msg = None
        error_alarm = False
        error_help = None   # help panel of the showing error <msg help=>, if any
        userid = None
        while True:
            screen = send_tso_logon(transport, error_msg, model=model,
                                    alarm=error_alarm, ctx=ctx)
            result = transport.read_input()
            if result is None:
                return
            aid, fields, cursor = result
            print(f"AID={hex(aid)}, fields={redact_fields(fields)}")

            aid_str = aid_to_string(aid)
            cmd = screen.command_for(aid_str)
            if cmd in _LEAVE_COMMANDS:
                # Keylist bound this key (PF3/PF15) to EXIT — log off.
                return
            if cmd == "HELP":
                # A showing message's own help panel (<msg help=>) wins, the way
                # ISPF routes HELP on a displayed message to its help; then
                # field-level help (cursor on a field with its own help), then
                # the panel's general help.
                help_panel = error_help or screen.help_for(cursor) or screen.help
                if help_panel:
                    _show_help(transport, help_panel, ctx=ctx)
                    continue

            # Validate fields against their <varclass> checks (e.g. SIZE range)
            # before processing the logon, as ISPF validates panel fields.
            verr = screen.first_validation_error(fields)
            if verr:
                msgid, subs = verr
                error_msg = _messages().format(msgid, **subs)
                error_alarm = _messages().alarm(msgid)
                error_help = _messages().help(msgid)
                continue

            userid_raw = fields.get(LOGON_USERID_ADDR, "").strip().upper()
            password_raw = fields.get(LOGON_PASSWORD_ADDR, "").strip().upper()

            if not userid_raw:
                error_msg = _messages().format("IKJ56700I")
                error_alarm = _messages().alarm("IKJ56700I")
                error_help = _messages().help("IKJ56700I")
                continue

            if _CREDENTIALS.get(userid_raw) != password_raw:
                error_msg = _messages().format("IKJ56425I", USERID=userid_raw)
                error_alarm = _messages().alarm("IKJ56425I")
                error_help = _messages().help("IKJ56425I")
                continue

            userid = userid_raw
            break

        # ISPF menu loop
        short_msg = None
        screen = None
        # Only repaint the whole panel when it actually changed — the first time
        # in, and after any sub-panel that overwrote the screen. A stay-on-the-
        # menu message (INVALID OPTION, …) is instead patched in place with a
        # plain Write, so the option the user typed survives the redisplay.
        needs_full_redraw = True
        while True:
            if needs_full_redraw:
                screen = send_ispf_menu(transport, userid, short_msg, ctx=ctx)
            else:
                _update_menu_message(transport, screen, short_msg, ctx=ctx)
            result = transport.read_input()
            if result is None:
                return
            aid, fields, cursor = result
            print(f"AID={hex(aid)}, fields={redact_fields(fields)}")
            # Assume the next outcome repaints the panel (a sub-panel, or exit).
            # The stay-on-the-menu message branches below flip this off so the
            # message is patched in place with a plain Write instead.
            needs_full_redraw = True

            aid_str = aid_to_string(aid)
            # Read the option from the panel's <cmdarea> (its ZCMD command
            # field), resolved by role rather than a hard-coded address.
            option = (screen.command_value(fields) or "").strip().upper()
            # Point-and-shoot: with no typed option, Enter (or Cursor Select) on a
            # <ps> phrase sets the command variable, else on a choice row picks it.
            if not option and _is_cursor_select(aid_str):
                ps = screen.command_point_and_shoot(cursor)
                option = ps.strip().upper() if ps else (screen.selection_at(cursor) or "")

            cmd = screen.command_for(aid_str)
            if option == "X" or cmd in _LEAVE_COMMANDS:
                # X, or a keylist key (PF3/PF15) bound to EXIT — back to logon
                break
            if cmd == "HELP":
                help_panel = screen.help_for(cursor) or screen.help
                if help_panel:
                    _show_help(transport, help_panel, ctx=ctx)
                    continue

            # A typed value is a menu selection, a command from the panel's
            # <cmdtbl>, or invalid. (The "X" exit choice is handled above.)
            head = option.split(".", 1)[0]
            tail = option.split(".", 1)[1] if "." in option else None
            # Route the option through the panel's declared )PROC map (#55): the
            # option's selection string (e.g. "1" -> "PGM(view)") names the
            # behaviour, and _run_selection runs the handler registered for it.
            # ISPF's TRUNC(&ZCMD,'.') routes on the head; the tail flows through.
            # Only route a visible, selectable option: a HIDE/HIDEX or UNAVAIL
            # choice is absent from `selections`, so its )PROC target must not be
            # reachable by typing it either.
            target = screen.selection_targets.get(head)
            if target is not None and head in screen.selections:
                leaving = _run_selection(transport, target, tail, userid, model,
                                         ctx=ctx)
                if leaving:
                    break
                elif leaving is False:
                    short_msg = None
                else:                        # declared, but no handler yet
                    short_msg = f"OPTION {option} NOT YET IMPLEMENTED"
                    needs_full_redraw = False
            elif option in screen.selections:
                short_msg = f"OPTION {option} NOT YET IMPLEMENTED"
                needs_full_redraw = False   # patch the message, keep the input
            elif option:
                action = screen.lookup_command(option)
                if action and action.lower().startswith("alias ") \
                        and action.split()[1].upper() in _LEAVE_COMMANDS:
                    break  # e.g. BYE -> "alias exit" leaves ISPF
                elif action:
                    short_msg = f"COMMAND {option} NOT YET IMPLEMENTED"
                    needs_full_redraw = False
                else:
                    short_msg = f"INVALID OPTION: {option}"
                    needs_full_redraw = False
            else:
                short_msg = None
                needs_full_redraw = False    # bare Enter: keep whatever was typed
