"""The golden outbound byte-stream corpus (#354).

The ws3270 smoke tests are the fidelity backbone, but they skip when no
emulator is installed — so without one, end-to-end byte fidelity went
unchecked. This module builds the same outbound records the smoke-verified
server puts on the wire, entirely in memory (no sockets, no emulator), so the
**pure** suite can assert them byte-for-byte against a recorded snapshot
(``golden/corpus.txt``) on every run. The emulator tests verify the *emulator*;
the golden corpus guards the *bytes*.

The corpus has two halves:

- **Panels** — every ``panels/*.dtl`` member's full render, mono and colour
  (``panel:<name>:mono`` / ``:color``): the DTL parser, the flow layout, and
  the render codec, end to end.
- **Session transcripts** — every record :func:`session.run` sends during two
  scripted flows driven over an in-memory Transport with a pinned clock and an
  explicit :class:`ds3270.SessionContext` (#352). The mono flow exercises both
  logon error messages (with their alarm WCCs), the full-repaint menu, the
  in-place partial-Write menu message, and the Command Shell round trip; the
  colour flow exercises SFE/CUA colour on the logon, menu, and a ``<lstfld>``
  table panel (Dialog Test). ``SocketTransport`` sends these records verbatim
  (TN3270E only prepends its 5-byte header), so they are exactly the wire bytes
  a real terminal receives.

Regenerating (only when a byte change is *intended*):

    python golden_corpus.py --write

A change to a panel, the layout engine, or the codec that alters the stream is
supposed to show up here — verify it against a real emulator first (run the
ws3270 smoke suite, and eyeball the panel on a real terminal if it moved), then
regenerate and commit the new ``golden/corpus.txt`` with the change. Byte
identity is a safety check, not a gate: a deliberate, fidelity-improving change
just re-records. The corpus deliberately avoids locale-dependent text (e.g. the
TSO ``TIME`` message's day name), so a regeneration is reproducible anywhere.
"""
import os
import sys
from datetime import datetime

import session as session_module
from ds3270 import SessionContext
from dtl import load_panel

CORPUS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "golden", "corpus.txt")

ENTER, PF3 = 0x7D, 0xF3


class _FixedDateTime(datetime):
    """A ``datetime`` whose ``now()`` is pinned, so the ZTIME/ZDATE dialog
    variables render identically on every run."""

    @classmethod
    def now(cls, tz=None):
        return cls(2026, 1, 1, 12, 34, 56)


class _Transport:
    """The two-method Transport port, in memory: records every outbound 3270
    record and answers ``read_input()`` from a script of parsed replies. An
    exhausted script reads as a client disconnect (see test_session.py)."""

    def __init__(self, replies):
        self.sent = []
        self._replies = iter(replies)

    def send(self, data):
        self.sent.append(bytes(data))

    def read_input(self):
        return next(self._replies, None)


def _panel_names():
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "panels")
    return sorted(f[:-4] for f in os.listdir(base) if f.endswith(".dtl"))


def _panel_records():
    """Every bundled panel's default render, mono and colour."""
    records = {}
    for name in _panel_names():
        records[f"panel:{name}:mono"] = load_panel(name).render(
            session=SessionContext())
        records[f"panel:{name}:color"] = load_panel(name).render(
            session=SessionContext(color=True))
    return records


def _command_addr():
    """The Command Shell's ``<cmdarea>`` input address, derived from the panel
    (not hard-coded) so the script tracks command.dtl."""
    screen = load_panel("command")
    return screen.command_field.data_addr(screen.width)


def _run_scripted(replies, ctx):
    """Drive :func:`session.run` over the scripted replies with a pinned clock,
    returning every outbound record."""
    transport = _Transport(replies)
    real_datetime = session_module.datetime
    session_module.datetime = _FixedDateTime
    try:
        session_module.run(transport, ctx=ctx)
    finally:
        session_module.datetime = real_datetime
    return transport.sent


def _session_records():
    """The mono session transcript: logon errors, login, menu, an invalid
    option patched in place with a plain Write, the Command Shell, and logoff."""
    replies = [
        (ENTER, {}, 0),                                        # no userid
        (ENTER, {session_module.LOGON_USERID_ADDR: "IBMUSER",  # bad password
                 session_module.LOGON_PASSWORD_ADDR: "WRONG"}, 0),
        (ENTER, {session_module.LOGON_USERID_ADDR: "IBMUSER",  # valid logon
                 session_module.LOGON_PASSWORD_ADDR: "SYS1"}, 0),
        (ENTER, {session_module.ISPF_OPTION_ADDR: "ZZ"}, 0),   # invalid option
        (ENTER, {session_module.ISPF_OPTION_ADDR: "6"}, 0),    # Command Shell
        (ENTER, {_command_addr(): "HELLO"}, 0),                # unknown command
        (PF3, {}, 0),                                          # leave the shell
        (ENTER, {session_module.ISPF_OPTION_ADDR: "X"}, 0),    # leave ISPF
    ]
    steps = ["logon", "logon-msg-enter-userid", "logon-msg-password-not-valid",
             "menu", "menu-invalid-option-partial-write", "command-shell",
             "command-shell-not-found", "menu-after-shell", "logon-after-exit"]
    sent = _run_scripted(replies, SessionContext())
    assert len(sent) == len(steps), \
        f"mono flow sent {len(sent)} records, expected {len(steps)}"
    return {f"session:mono:{i:02d}-{step}": record
            for i, (step, record) in enumerate(zip(steps, sent))}


def _session_color_records():
    """The colour session transcript: the same wire, with extended (SFE/SA)
    attributes — logon, menu, and the Dialog Test ``<lstfld>`` table."""
    replies = [
        (ENTER, {session_module.LOGON_USERID_ADDR: "IBMUSER",
                 session_module.LOGON_PASSWORD_ADDR: "SYS1"}, 0),
        (ENTER, {session_module.ISPF_OPTION_ADDR: "7"}, 0),    # Dialog Test
        (PF3, {}, 0),                                          # leave it
        (ENTER, {session_module.ISPF_OPTION_ADDR: "X"}, 0),    # leave ISPF
    ]
    steps = ["logon", "menu", "dialog-test", "menu-after-dialog-test",
             "logon-after-exit"]
    sent = _run_scripted(replies, SessionContext(color=True))
    assert len(sent) == len(steps), \
        f"colour flow sent {len(sent)} records, expected {len(steps)}"
    return {f"session:color:{i:02d}-{step}": record
            for i, (step, record) in enumerate(zip(steps, sent))}


def build_corpus() -> dict:
    """Build the whole corpus live: ``{record name: bytes}``."""
    records = {}
    records.update(_panel_records())
    records.update(_session_records())
    records.update(_session_color_records())
    return records


def load_corpus(path: str = CORPUS_PATH) -> dict:
    """Load the recorded corpus: one ``<name> <hex>`` line per record."""
    records = {}
    with open(path, encoding="ascii") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name, _, hexed = line.partition(" ")
            records[name] = bytes.fromhex(hexed)
    return records


def write_corpus(path: str = CORPUS_PATH) -> dict:
    """Record the live corpus to ``path`` (one name + hex line per record,
    sorted, LF-terminated so the file is identical on every platform)."""
    records = build_corpus()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="ascii", newline="\n") as fh:
        fh.write("# Golden outbound byte-stream corpus (#354). One record per "
                 "line: <name> <hex>.\n"
                 "# Regenerate deliberately with: python golden_corpus.py "
                 "--write  (see golden_corpus.py).\n")
        for name in sorted(records):
            fh.write(f"{name} {records[name].hex()}\n")
    return records


if __name__ == "__main__":
    if "--write" in sys.argv[1:]:
        written = write_corpus()
        print(f"recorded {len(written)} records to {CORPUS_PATH}")
    else:
        recorded = load_corpus()
        live = build_corpus()
        stale = sorted(set(recorded) ^ set(live)) + sorted(
            n for n in recorded if n in live and recorded[n] != live[n])
        if stale:
            print("stale records:", *stale, sep="\n  ")
            sys.exit(1)
        print(f"corpus up to date ({len(live)} records)")
