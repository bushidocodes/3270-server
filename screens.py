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


