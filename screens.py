"""The TSO/E logon panel and ISPF Primary Option Menu, expressed as data.

These builders return :class:`screen.Screen` objects that render byte-for-byte
identically to the original hand-written ``send_tso_logon`` /
``send_ispf_menu`` functions in :mod:`server` (see ``test_screen.py`` for the
golden regression that guarantees this). They are the in-code equivalent of the
``panels/*.dtl`` declarative sources; :mod:`dtl` produces the same objects.
"""

from screen import Screen, Text, Field, DisplayIntensity

HIGH = DisplayIntensity.HIGH
NORMAL = DisplayIntensity.NORMAL
HIGHLIGHTED = DisplayIntensity.HIGHLIGHTED

# ISPF Primary Option Menu rows: (number, name, description)
ISPF_OPTIONS = [
    ("0", "Settings      ", "Terminal and user parameters"),
    ("1", "View          ", "Display source data or listings"),
    ("2", "Edit          ", "Create or change source data"),
    ("3", "Utilities     ", "Perform utility functions"),
    ("4", "Foreground    ", "Interactive language processing"),
    ("5", "Batch         ", "Submit job for language processing"),
    ("6", "Command       ", "Enter TSO or Workstation commands"),
    ("7", "Dialog Test   ", "Perform dialog testing"),
    ("9", "IBM Products  ", "IBM program development products"),
    ("10", "SCLM          ", "SW Configuration Library Manager"),
    ("11", "Workplace     ", "ISPF Object/Action Workplace"),
    ("12", "z/OS System   ", "z/OS system programmer applications"),
    ("13", "z/OS User     ", "z/OS user applications"),
]


def build_tso_logon(error_msg: str = None) -> Screen:
    """The authentic z/OS V2R5.0 TSO/E LOGON panel."""
    s = Screen(title="z/OS V2R5.0 TSO/E LOGON")

    title = "-" * 8 + "  z/OS V2R5.0 TSO/E LOGON  " + "-" * 8
    s.text(0, (80 - len(title)) // 2, title, HIGH)

    s.text(2, 1, "Enter LOGON parameters below:")
    s.text(2, 42, "RACF LOGON parameters:")
    s.text(3, 1, "-" * 37)
    s.text(3, 42, "-" * 37)

    s.text(5, 1, "Userid   ===>")
    s.field(5, 16, 8, name="userid", cursor=True)

    s.text(6, 1, "Password ===>")
    s.field(6, 16, 8, name="password", hidden=True)

    s.text(7, 1, "Procedure===>")
    s.field(7, 16, 8, name="procedure", default="IKJACCNT")
    s.text(7, 42, "Acct Nmbr    ===>")
    s.field(7, 60, 8, name="acct")

    s.text(8, 1, "Size     ===>")
    s.field(8, 16, 5, name="size", default="00150", numeric=True)
    s.text(8, 42, "Perform      ===>")
    s.field(8, 60, 8, name="perform", numeric=True)

    s.text(9, 1, "Command  ===>")
    s.field(9, 16, 62, name="command")

    s.text(11, 1, "PDS/E Dsname ===>")
    s.field(11, 19, 59, name="dsname")

    s.text(12, 42, "Mail      ===> Yes")
    s.text(13, 42, "Reconnect ===> Auto")
    s.text(14, 42, "OIDcard   ===> None")

    s.text(15, 1, "*** Authorized users only. Unauthorized access is prohibited. ***", HIGHLIGHTED)
    s.text(16, 1, "Press ENTER to logon to TSO/E")
    s.text(17, 1, "PF1=HELP   PF3=LOGOFF")
    s.text(21, 1, "ENTER AN END COMMAND TO LOGOFF")

    # Transient overlay is appended last (the DTL-backed server composes it the
    # same way). Stream order is irrelevant to the display — 3270 buffer
    # addresses are absolute — but appending keeps both paths byte-identical.
    if error_msg:
        s.text(19, max(0, (80 - len(error_msg)) // 2), error_msg, HIGH)
    return s


def build_ispf_menu(userid: str, time_str: str, short_msg: str = None) -> Screen:
    """The authentic ISPF 7.1.0 Primary Option Menu.

    ``time_str`` is passed in (rather than read from the clock) so rendering is
    deterministic and testable; the server supplies ``datetime.now()``.
    """
    s = Screen(title="ISPF Primary Option Menu")

    inner = " ISPF Primary Option Menu "  # 26 chars
    pad = (79 - len(inner)) // 2
    border = "-" * pad + inner + "-" * (79 - pad - len(inner))
    s.text(0, 0, border, HIGH)

    s.text(2, 1, "Option ===>")
    s.field(2, 13, 6, name="option", cursor=True)

    for i, (num, name, desc) in enumerate(ISPF_OPTIONS):
        row = 4 + i
        s.text(row, 1, f"{num:<2}", HIGH)
        s.text(row, 4, f"  {name}")
        s.text(row, 21, f"  {desc}")

    s.text(18, 1, "X ", HIGH)
    s.text(18, 4, "  Exit          ")
    s.text(18, 21, "  Terminate ISPF using log/list defaults")

    s.text(20, 1, "Enter X or PF3 to terminate ISPF.")

    s.text(21, 1, f"User ID . . :  {userid:<8}")
    s.text(21, 41, f"Time. . . . :  {time_str}")
    s.text(22, 1, "System ID . :  SY1     ")
    s.text(22, 41, "ISPF Ver. . :  7.1.0   ")
    s.text(23, 0, "-" * 79, HIGH)

    # Transient overlay appended last — see the note in build_tso_logon.
    if short_msg:
        s.text(2, 25, short_msg[:54], HIGH)
    return s
