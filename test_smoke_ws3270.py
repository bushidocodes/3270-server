"""Real-emulator smoke test: drive the server with an actual ws3270/s3270.

Every other test uses a synthetic in-process client, which — as the Read
Partition Query hang fixed in #87 showed — can pass while the server is in fact
broken against a real terminal. This test boots the real server and connects an
actual x3270-family emulator (``ws3270``/``s3270``, the scriptable builds) to it,
exercising the full TN3270E negotiation, header framing, and session loop
end-to-end.

It is skipped automatically when no emulator is installed, so it never blocks a
machine (or CI) that doesn't have one. Where an emulator *is* present it guards
against the "works synthetically, breaks on a real terminal" class of bug — a
regression of the #87 hang, for instance, would keep the logon panel from
appearing in time and fail the assertions below.
"""
import os
import shutil
import socket
import subprocess
import tempfile
import threading

import pytest

import server


def _find_emulator():
    """Locate a scriptable x3270-family emulator (ws3270/s3270), or None."""
    for name in ("ws3270", "s3270"):
        found = shutil.which(name)
        if found:
            return found
    # Common Windows install locations for wc3270 (not usually on PATH).
    for base in (r"C:\Program Files\wc3270", r"C:\Program Files (x86)\wc3270"):
        cand = os.path.join(base, "ws3270.exe")
        if os.path.isfile(cand):
            return cand
    return None


EMULATOR = _find_emulator()


def _require_emulator():
    """The emulator path, or skip/fail. Normally a missing emulator skips the
    test; setting ``REQUIRE_EMULATOR=1`` (CI does) turns it into a failure
    instead, so a broken install is caught loudly rather than passing silently."""
    if EMULATOR:
        return EMULATOR
    if os.environ.get("REQUIRE_EMULATOR") == "1":
        pytest.fail("REQUIRE_EMULATOR=1 but no ws3270/s3270 emulator was found on PATH")
    pytest.skip("no ws3270/s3270 emulator installed")


def _serve_one_client(tls_context=None, starttls=False):
    """Listen on an ephemeral port and serve exactly one client through the real
    :func:`server.handle_client` in a background thread. Returns the port. When
    ``tls_context`` is given, the connection is served through
    :func:`server._client_thread`, which does the server-side TLS handshake
    (implicit TLS, or a negotiated START-TLS upgrade when ``starttls`` is set)."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        try:
            conn, addr = srv.accept()
            if tls_context is not None:
                server._client_thread(conn, addr, tls_context=tls_context,
                                      starttls=starttls)
            else:
                server.handle_client(conn, addr)
        except Exception:
            pass
        finally:
            srv.close()

    threading.Thread(target=serve, daemon=True).start()
    return port


def _host_arg(port, tls=False, basic=False):
    """The emulator's host argument. ``tls`` prepends the ``L:`` prefix (implicit
    TLS); ``basic`` prepends ``N:`` (no TN3270E) to force the basic-TN3270 path,
    which is the only mode in which x3270 answers a Read Partition Query."""
    prefix = ("L:" if tls else "") + ("N:" if basic else "")
    return f"{prefix}127.0.0.1:{port}"


def _drive(port, actions, model="2", tls=False, noverify=False):
    """Run the emulator (as the given ``model``) against 127.0.0.1:port with a
    list of action commands, returning its combined output. A 60s hang (the #87
    bug) can't wedge the run: the per-action Wait() timeouts bound it, and
    subprocess timeout is the backstop. ``tls`` connects via ``L:`` (implicit TLS)
    and accepts the self-signed test cert; ``noverify`` accepts the cert without
    the ``L:`` prefix — a plaintext connect that upgrades via negotiated START-TLS."""
    script = "\n".join(actions) + "\n"
    cmd = [EMULATOR, "-model", model]
    if tls or noverify:
        cmd.append("-noverifycert")
    cmd.append(_host_arg(port, tls))
    try:
        proc = subprocess.run(
            cmd, input=script, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"emulator did not finish in time (possible session hang): "
                    f"{(exc.output or '')[:500]}")
    return proc.stdout + proc.stderr


def _drive_traced(port, actions, model="2", basic=False, charset=None):
    """Like :func:`_drive`, but also captures the emulator's protocol trace and
    returns ``(output, trace_text)``. The trace records exactly what the emulator
    SENT and RCVD on the wire, so assertions on it are deterministic — unlike
    ``Ascii()`` screen dumps, which race the emulator's own screen rendering.
    ``basic`` forces the basic-TN3270 (``N:``) path; ``charset`` sets the host code
    page (``-charset``, e.g. ``"german"`` → the terminal reports CPGID 273)."""
    with tempfile.NamedTemporaryFile(
            prefix="ws3270-", suffix=".trace", delete=False) as tf:
        trace_path = tf.name
    try:
        script = "\n".join(actions) + "\n"
        cmd = [EMULATOR, "-model", model, "-trace", "-tracefile", trace_path]
        if charset:
            cmd += ["-charset", charset]
        cmd.append(_host_arg(port, basic=basic))
        try:
            proc = subprocess.run(
                cmd, input=script, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=60,
            )
        except subprocess.TimeoutExpired as exc:
            pytest.fail(f"emulator did not finish in time (possible session hang): "
                        f"{(exc.output or '')[:500]}")
        with open(trace_path, encoding="utf-8", errors="replace") as fh:
            trace = fh.read()
        return proc.stdout + proc.stderr, trace
    finally:
        os.unlink(trace_path)


def test_ws3270_logs_in_and_navigates():
    """A real emulator negotiates, logs in, and drives the ISPF panels."""
    _require_emulator()
    port = _serve_one_client()
    out = _drive(port, [
        "Wait(20,InputField)",
        "Ascii()",              # the TSO/E logon panel
        "String(IBMUSER)",
        "Tab()",
        "String(SYS1)",
        "Enter()",
        "Wait(20,Output)",
        "Ascii()",              # the ISPF Primary Option Menu
        "String(7)",            # option 7 → Dialog Test
        "Enter()",
        "Wait(10,Output)",
        "Ascii()",              # Dialog Test, which shows ZTERM
        "PF(3)",                # back to the menu
        "Wait(5,Output)",
        "PF(3)",                # exit ISPF → back to the logon panel
        "Quit()",
    ])

    # The real emulator negotiated and rendered our real panels...
    assert "z/OS V2R5.0 TSO/E LOGON" in out, out[:800]
    assert "ISPF Primary Option Menu" in out, out[:800]
    # ...and Dialog Test shows ZTERM = the device type the emulator negotiated,
    # proving the TN3270E DEVICE-TYPE (or TERMINAL-TYPE) flowed through the model.
    assert "Dialog Test" in out
    assert "ZTERM" in out


def test_ws3270_table_input_reads_rows_back():
    """A real emulator types into an input ``<lstfld>`` table and the server reads
    the modified cells back *per model row* (Screen.read_table_rows, #249): the
    Dialog Test Table Input panel (PF5) echoes the row it read as
    "Read 1 row(s): ABC=XYZ", proving the table-input read-back path works
    end-to-end against a real terminal — not just synthetically."""
    _require_emulator()
    port = _serve_one_client()
    out = _drive(port, [
        "Wait(20,InputField)",
        "String(IBMUSER)", "Tab()", "String(SYS1)", "Enter()",
        "Wait(20,Output)",       # ISPF menu
        "String(7)", "Enter()",  # option 7 → Dialog Test (variables)
        "Wait(10,Output)",
        "PF(5)",                 # → Table Input scratch panel
        "Wait(10,InputField)",   # cursor lands on the first table cell
        "String(ABC)",           # first row's Key
        "Tab()",
        "String(XYZ)",           # first row's Value
        "Enter()",               # submit → server reads the table back
        "Wait(10,Output)",
        "Ascii()",
        "PF(3)", "Wait(5,Output)",   # back to Dialog Test
        "PF(3)", "Wait(5,Output)",   # back to the menu
        "Quit()",
    ])

    assert "Dialog Test - Table Input" in out, out[-1500:]
    # The server read exactly the row the emulator typed, distinguished by row.
    assert "Read 1 row(s): ABC=XYZ" in out, out[-1500:]


def test_ws3270_table_input_caps_column_folds_to_uppercase():
    """A real emulator types *lowercase* into the Table Input Key column, which is
    a CAPS=ON <lstcol>: the server folds it to uppercase on read-back
    (Screen.read_table_rows), so the echo shows the key uppercased while the plain
    Value column keeps its case — proving CAPS=ON end-to-end (#238)."""
    _require_emulator()
    port = _serve_one_client()
    out = _drive(port, [
        "Wait(20,InputField)",
        "String(IBMUSER)", "Tab()", "String(SYS1)", "Enter()",
        "Wait(20,Output)",       # ISPF menu
        "String(7)", "Enter()",  # option 7 → Dialog Test
        "Wait(10,Output)",
        "PF(5)",                 # → Table Input scratch panel
        "Wait(10,InputField)",
        "String(abc)",           # lowercase into the CAPS=ON Key column
        "Tab()",
        "String(xyz)",           # lowercase into the plain Value column
        "Enter()",
        "Wait(10,Output)",
        "Ascii()",
        "PF(3)", "Wait(5,Output)",
        "PF(3)", "Wait(5,Output)",
        "Quit()",
    ])

    # Key folded to uppercase (CAPS=ON), Value left as typed (CAPS off).
    assert "Read 1 row(s): ABC=xyz" in out, out[-1500:]


def test_ws3270_table_input_required_column_validates():
    """A real emulator modifies a table row but leaves its REQUIRED Key cell blank:
    the server surfaces the column MSG (KEYREQ) and redisplays without committing
    (Screen.table_required_errors, #236). Filling the Key on the redisplay then
    succeeds — proving REQUIRED=YES/MSG end-to-end."""
    _require_emulator()
    port = _serve_one_client()
    out = _drive(port, [
        "Wait(20,InputField)",
        "String(IBMUSER)", "Tab()", "String(SYS1)", "Enter()",
        "Wait(20,Output)",       # ISPF menu
        "String(7)", "Enter()",  # option 7 → Dialog Test
        "Wait(10,Output)",
        "PF(5)",                 # → Table Input scratch panel
        "Wait(10,InputField)",   # cursor on the Key cell
        "Tab()",                 # skip Key (leave the required cell blank)
        "String(justval)",       # type into Value → the row is modified
        "Enter()",               # submit → required Key is blank
        "Wait(10,Output)",
        "Ascii()",               # KEYREQ message shown, nothing committed
        "String(MYKEY)",         # cursor is back on Key; fill it in
        "Enter()",               # submit again → valid
        "Wait(10,Output)",
        "Ascii()",               # the read-back echo
        "PF(3)", "Wait(5,Output)",
        "PF(3)", "Wait(5,Output)",
        "Quit()",
    ])

    # Blank required Key surfaced the column MSG...
    assert "KEYREQ" in out, out[-1800:]
    # ...and once filled, the row read back (Value preserved across the redisplay).
    assert "Read 1 row(s): MYKEY=justval" in out, out[-1800:]


def test_ws3270_member_list_pages_with_correct_scroll_status():
    """A real emulator pages the member list (Utilities → Library) with PF8: the
    panel's "ROW x TO y OF z" scroll status reflects the true window over the full
    set and "BOTTOM OF DATA" appears only on the last page (#281)."""
    _require_emulator()
    n = len(server._library_members())
    if n <= 17:
        pytest.skip("member list fits on one model-2 page; nothing to page")
    port = _serve_one_client()
    out = _drive(port, [
        "Wait(20,InputField)",
        "String(IBMUSER)", "Tab()", "String(SYS1)", "Enter()",
        "Wait(20,Output)",           # ISPF menu
        "String(3)", "Enter()",      # option 3 → Utilities
        "Wait(10,Output)",
        "String(1)", "Enter()",      # option 1 → Library member list (page 1)
        "Wait(10,Output)",
        "Ascii()",                   # page 1: ROW 1 TO 17 OF n, no BOTTOM
        "PF(8)",                     # page down
        "Wait(10,Output)",
        "Ascii()",                   # last page: ROW 18 TO n OF n + BOTTOM OF DATA
        "PF(3)", "Wait(5,Output)",
        "PF(3)", "Wait(5,Output)",
        "PF(3)", "Wait(5,Output)",
        "Quit()",
    ])

    assert "ROW 1 TO 17 OF %d" % n in out, out[-2000:]
    assert "ROW 18 TO %d OF %d" % (n, n) in out, out[-2000:]
    assert "BOTTOM OF DATA" in out, out[-2000:]


def test_ws3270_help_tutorial_pages_with_pf8():
    """A real emulator opens the Primary Option Menu help (PF1) — a tutorial taller
    than 24 rows — and pages it with PF8: the title stays fixed, a "More:" scroll
    indicator shows, and page 2 reveals text not on page 1 (#281)."""
    _require_emulator()
    port = _serve_one_client()
    out = _drive(port, [
        "Wait(20,InputField)",
        "String(IBMUSER)", "Tab()", "String(SYS1)", "Enter()",
        "Wait(20,Output)",           # ISPF menu
        "PF(1)",                     # HELP → the (multi-page) tutorial
        "Wait(10,Output)",
        "Ascii()",                   # page 1
        "PF(8)",                     # page down
        "Wait(10,Output)",
        "Ascii()",                   # page 2
        "PF(3)", "Wait(5,Output)",   # leave help
        "PF(3)", "Wait(5,Output)",   # exit ISPF
        "Quit()",
    ])

    assert "Primary Option Menu - HELP" in out, out[-2000:]
    assert "More:" in out, out[-2000:]
    # page 2 shows the closing paragraph, which is off the bottom of page 1
    assert "Press PF3 at any time" in out, out[-2000:]


def test_ws3270_model_3_browse_uses_the_alternate_screen():
    """A model-3 emulator (32 rows) browsing a member sees more lines per page —
    proving ERASE/WRITE ALTERNATE and the larger geometry work on a real
    terminal, not just synthetically."""
    _require_emulator()
    port = _serve_one_client()
    out = _drive(port, [
        "Wait(20,InputField)",
        "String(IBMUSER)", "Tab()", "String(SYS1)", "Enter()",
        "Wait(20,Output)",       # ISPF menu
        "String(1)", "Enter()",  # option 1 → View
        "Wait(10,Output)",       # View entry panel
        "String(ISPF)", "Enter()",   # browse the ISPF menu's own source
        "Wait(10,Output)",       # the Browse screen
        "Ascii()",
        "Quit()",
    ], model="3")

    # 32-row screen → 30 lines per page (row 0 header, last row footer). A model-2
    # (24-row) screen would show "Lines 1-22 of".
    assert "Lines 1-30 of" in out, out[-1200:]


def test_ws3270_sysreq_enters_the_host_session():
    """A real emulator's SysReq() sends Telnet AO; the server drops into the
    SSCP-LU host session and sends our unformatted prompt, which the emulator
    receives as SSCP-LU-DATA (it switches to SSCP mode).

    Asserted on the emulator's protocol trace, which is deterministic. We only
    assert the emulator-agnostic half of the round trip here — that SysReq maps
    to AO and the server enters the SSCP session. The rest of the SSCP behaviour
    (resume on a second SYSREQ, LOGOFF→UNBIND, COMMAND UNRECOGNIZED) is driven by
    what the *server* does and is covered deterministically by the in-process
    unit tests in test_tn3270e.py; scripting those steps through a real emulator
    depends on its SSCP-mode input timing, which differs across ws3270/s3270."""
    _require_emulator()
    port = _serve_one_client()
    _, trace = _drive_traced(port, [
        "Wait(20,InputField)",
        "String(IBMUSER)", "Tab()", "String(SYS1)", "Enter()",
        "Wait(20,Unlock)",       # login fully processed, ISPF menu drawn
        "SysReq()",              # press SYSREQ → Telnet AO → SSCP-LU session
        # A guaranteed settle (Wait(Output) can return early on a stale flag): the
        # emulator keeps pumping the socket during a timed wait, so it receives
        # and processes our SSCP-LU-DATA before Quit().
        "Wait(2,Seconds)",
        "Quit()",
    ])

    # The real emulator sent SYSREQ as Telnet AO...
    assert "SENT AO" in trace, trace[-2000:]
    # ...and received our SSCP-LU-DATA prompt (the server entered the host session).
    assert "RCVD TN3270E(SSCP-LU-DATA" in trace, trace[-2000:]


def test_ws3270_bind_image_binds_and_enables_attn():
    """With BIND-IMAGE negotiated the server sends an SNA BIND, which the real
    emulator accepts (`RCVD TN3270E(BIND-IMAGE…)`, parsed without error). Being
    bound is what lets the emulator's ATTN key reach us: `Attn()` then sends
    Telnet IP, where before binding ws3270 would only lock its keyboard. Both are
    asserted on the deterministic protocol trace."""
    _require_emulator()
    port = _serve_one_client()
    _, trace = _drive_traced(port, [
        "Wait(20,InputField)",
        "String(IBMUSER)", "Tab()", "String(SYS1)", "Enter()",
        "Wait(20,Unlock)",       # login fully processed, ISPF menu drawn
        "Attn()",                # only sends IP once the session is bound
        "Wait(5,Output)",        # the server redisplays in response
        "Quit()",
    ])

    # The emulator received and accepted our BIND (no "invalid" in the parse)...
    assert "RCVD TN3270E(BIND-IMAGE" in trace, trace[-2000:]
    assert "< BIND " in trace and "invalid" not in trace.split("< BIND", 1)[1][:120]
    # ...and, now bound, sent ATTN as Telnet IP.
    assert "SENT IP" in trace, trace[-2000:]


def test_ws3270_logs_in_over_tls(tls_cert):
    """A real emulator connects with the ``L:`` (implicit TLS) prefix to a
    TLS-wrapped server, accepts the self-signed cert (-noverifycert), and runs
    the whole negotiate/login/navigate flow over the encrypted socket — proving
    TLS end-to-end against a real terminal, not just an in-process client."""
    _require_emulator()
    certfile, keyfile = tls_cert
    port = _serve_one_client(tls_context=server.make_tls_context(certfile, keyfile))
    out = _drive(port, [
        "Wait(20,InputField)",
        "Ascii()",              # the TSO/E logon panel (over TLS)
        "String(IBMUSER)", "Tab()", "String(SYS1)", "Enter()",
        "Wait(20,Output)",
        "Ascii()",              # the ISPF Primary Option Menu (over TLS)
        "Quit()",
    ], tls=True)

    assert "z/OS V2R5.0 TSO/E LOGON" in out, out[:800]
    assert "ISPF Primary Option Menu" in out, out[:800]


def test_ws3270_invalid_option_keeps_typed_input():
    """Typing an invalid option on the ISPF menu redisplays the message *in
    place* with a plain Write, so the typed option survives — real ISPF
    behaviour. Verified two ways: the emulator's screen still shows the typed
    "ZZ" next to the message, and its trace shows the redisplay was a `Write`
    (not an `EraseWrite`, which would have repainted and cleared the field)."""
    _require_emulator()
    port = _serve_one_client()
    out, trace = _drive_traced(port, [
        "Wait(20,InputField)",
        "String(IBMUSER)", "Tab()", "String(SYS1)", "Enter()",
        "Wait(20,Unlock)",           # ISPF menu
        "String(ZZ)", "Enter()",     # an invalid option
        "Wait(20,Unlock)",           # the in-place message redisplay
        "Ascii()",
        "Quit()",
    ])

    # The typed option is still on the command line, beside the error message.
    assert "ZZ" in out and "INVALID OPTION: ZZ" in out, out[-1500:]
    # The redisplay reached the emulator as a plain Write, not an EraseWrite.
    assert "< Write(" in trace, trace[-2000:]


def test_ws3270_contention_resolution_negotiated_and_send_data_granted():
    """CONTENTION-RESOLUTION is always offered now, so a real emulator negotiates
    it on every session. The server grants the keyboard send turn (the SEND-DATA
    request flag) on every screen, so the client never has to BID and input flows
    normally: login plus a second exchange reaches Dialog Test with the keyboard
    never left locked, and no BID is ever sent.

    (The byte-level SEND-DATA flag is asserted deterministically in
    test_tn3270e.py. The emulator's *trace wording* for that flag varies by
    version — s3270 v4.4 prints "3270-DATA SEND-DATA", v4.1 does not — so here we
    assert on version-robust facts: the session flows and nobody bids.)"""
    _require_emulator()
    port = _serve_one_client()
    out, trace = _drive_traced(port, [
        "Wait(20,InputField)",
        "String(IBMUSER)", "Tab()", "String(SYS1)", "Enter()",
        "Wait(20,Unlock)",           # ISPF menu (first exchange in)
        "String(7)", "Enter()",      # option 7 → Dialog Test (second exchange)
        "Wait(20,Unlock)",
        "Ascii()",
        "Quit()",
    ])

    # The second exchange reached Dialog Test — input flowed with the function
    # active, so the keyboard was never left locked...
    assert "Dialog Test" in out, out[-1500:]
    # ...and the client never had to send a BID (we granted the send turn).
    assert "BID" not in trace, trace[-2000:]


def test_ws3270_basic_mode_answers_read_partition_query():
    """In basic TN3270 mode (the ``N:`` prefix) a real emulator answers the Read
    Partition Query List, so the server discovers the terminal's capabilities.
    This exercises the IAC-escaping fix: the query's 0xFF partition byte must be
    doubled or the emulator rejects the structured field (a "WriteStructuredField
    error") and never replies. Asserted on the emulator trace plus a full login."""
    _require_emulator()
    port = _serve_one_client()
    out, trace = _drive_traced(port, [
        "Wait(20,InputField)",
        "String(IBMUSER)", "Tab()", "String(SYS1)", "Enter()",
        "Wait(20,Unlock)",       # the ISPF menu (query didn't derail the session)
        "Ascii()",
        "Quit()",
    ], basic=True)

    # The emulator received and processed our (correctly IAC-escaped) query...
    assert "ReadPartition" in trace, trace[-2000:]
    assert "WriteStructuredField error" not in trace, trace[-2000:]
    # ...and the session still reached the ISPF menu over the basic-TN3270 path.
    assert "ISPF Primary Option Menu" in out, out[-1500:]


def test_ws3270_accepts_erase_reset_structured_field():
    """A real emulator accepts an Erase/Reset Write Structured Field (#102).

    We prepend an Erase/Reset SF (id 0x03, ALTERNATE flag) to a screen and serve
    both to a basic-TN3270 model-3 emulator. ws3270 parses the SF — its trace
    shows ``WriteStructuredField EraseReset Alternate`` — resets to an implicit
    partition of the alternate size, and then renders the screen we sent. As with
    Set Reply Mode (#112), the headless acceptance (parsed, no
    ``WriteStructuredField error``, session not dropped) is the verification: the
    partition-size effect isn't separately observable in a headless Ascii() dump.
    """
    _require_emulator()
    from screen import Screen, Text

    scr = Screen().add(Text(1, 1, "ERASE RESET OK"))
    # Frame the Erase/Reset SF exactly like the production query path: IAC-escape
    # (a no-op here — no body byte is 0xFF) and IAC-EOR-terminate, then the screen.
    record = (server._iac_escape(server.erase_reset(alternate=True))
              + bytes([0xFF, 0xEF]) + scr.render())
    port = _serve_one_screen(record)
    out, trace = _drive_traced(port, [
        "Wait(3,Output)",
        "Ascii()",
        "Wait(1,Seconds)",
        "Quit()",
    ], model="3", basic=True)

    # Deterministic: the emulator parsed and accepted our Erase/Reset SF...
    assert "EraseReset" in trace, trace[-2000:]
    assert "WriteStructuredField error" not in trace, trace[-2000:]
    # ...and still rendered the screen that followed it (session not derailed).
    assert "ERASE RESET OK" in out, out[-1500:]


def _serve_one_screen(record_bytes):
    """Serve a single hand-built 3270 record to one basic-TN3270 client, then
    half-close: the emulator renders the buffered screen and sees EOF. (Holding
    the socket open instead makes ws3270 reset and hang.) Used to exercise a
    specific data-stream feature (here: SA) without the full session loop."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        try:
            conn, _ = srv.accept()
            server.tn3270_negotiate(conn)          # client uses N: → basic mode
            conn.sendall(record_bytes)
            conn.shutdown(socket.SHUT_WR)          # FIN after the screen (data first)
        except OSError:
            pass
        finally:
            srv.close()

    threading.Thread(target=serve, daemon=True).start()
    return port


def test_ws3270_renders_character_level_set_attribute():
    """A field with mixed colour (an emphasised keyword in a line of text, via SA
    orders) renders on a real emulator: it processes our Set Attribute orders and
    shows the text. Proves the SA bytes are a valid data stream a real terminal
    accepts, not just an in-process assumption."""
    _require_emulator()
    from screen import Screen, Text, Color

    scr = Screen().add(Text.rich(
        1, 1,
        [("Press ", None), ("ENTER", Color.RED), (" to continue, ", None),
         ("PF3", Color.RED), (" to exit", None)],
        role="text"))                              # base role "text" → green
    port = _serve_one_screen(scr.render(color=True))
    out, trace = _drive_traced(port, [
        "Wait(3,Output)",
        "Ascii()",
        "Wait(1,Seconds)",
        "Quit()",
    ], basic=True)

    # The emulator processed our SA orders and rendered the line.
    assert "SetAttribute" in trace, trace[-2000:]
    assert "Press ENTER to continue, PF3 to exit" in out, out[-1500:]


def test_ws3270_renders_rule_lines_from_ra():
    """The bundled panels' rule lines / fills are emitted as RA (Repeat to
    Address) orders rather than literal character runs; the emulator processes
    them (`RepeatToAddress`) and renders the same panels — a normal login/menu
    still works, proving the compacted stream is equivalent."""
    _require_emulator()
    port = _serve_one_client()
    out, trace = _drive_traced(port, [
        "Wait(20,InputField)",
        "String(IBMUSER)", "Tab()", "String(SYS1)", "Enter()",
        "Wait(20,Unlock)",
        "Ascii()",
        "Quit()",
    ])

    assert "RepeatToAddress" in trace, trace[-2000:]      # RA on the wire
    assert "ISPF Primary Option Menu" in out, out[-1500:]  # ...and it renders


def _serve_field_then_erase(record, erase):
    """Serve one screen (``record``), read the client's reply, then send
    ``erase``; hold the socket briefly so the emulator renders both."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        try:
            conn, _ = srv.accept()
            model, _ = server.tn3270_negotiate(conn)
            sock = conn
            if model.tn3270e:
                sock = server.TN3270EStream(
                    conn, responses=model.tn3270e_responses, sysreq=model.tn3270e_sysreq,
                    bind_image=model.tn3270e_bind_image, contention=model.tn3270e_contention)
                if model.tn3270e_bind_image:
                    sock.send_bind()
            sock.sendall(record)
            server.read_record(sock)              # the client's reply (typed text)
            sock.sendall(erase)
            sock.settimeout(6)
            try:
                sock.recv(1024)                   # hold open until the emulator quits
            except OSError:
                pass
        except Exception:
            pass
        finally:
            srv.close()

    threading.Thread(target=serve, daemon=True).start()
    return port


def test_ws3270_eua_clears_input_but_keeps_protected_text():
    """EUA (Erase Unprotected to Address) clears the entry field the user typed
    into while leaving the protected label on screen — verified on a real
    emulator via its rendered screen and the `EraseUnprotected` trace."""
    _require_emulator()
    from screen import Screen, Text, Field

    scr = Screen()
    scr.add(Text(2, 1, "NAME:"))
    scr.add(Field(2, 8, 10, name="nm", cursor=True))
    port = _serve_field_then_erase(scr.render(), scr.render_erase_input(cursor_at=(2, 8)))
    out, trace = _drive_traced(port, [
        "Wait(20,Unlock)",
        "String(HELLO)", "Snap()", "Snap(Ascii,2,0,80)", "Enter()",
        "Wait(20,Unlock)",
        "Ascii(2,0,80)",
        "Quit()",
    ])

    rows = [l.replace("data:", "").rstrip() for l in out.splitlines() if "NAME:" in l]
    assert rows, out[-1500:]
    assert "HELLO" in rows[0]                      # typed into the field…
    assert "HELLO" not in rows[-1]                 # …cleared after EUA
    assert "NAME:" in rows[-1]                      # …protected label kept
    assert "EraseUnprotected" in trace, trace[-2000:]


def test_ws3270_renders_graphic_escape_line_drawing():
    """A real emulator draws a Graphic-Escape line-drawing border.

    We send a box drawn from the 3270 line-drawing glyphs (GraphicText, which
    emits GE orders / a GE'd RA for the horizontal runs). The emulator's protocol
    trace records the `GraphicEscape` order for those glyphs — deterministic proof
    it parsed and accepted the GE data stream — and its rendered screen shows the
    box-drawing characters the GE code points map to (─ U+2500, │-corners …).
    """
    _require_emulator()
    from screen import Screen, Text, GraphicText

    scr = (Screen()
           .add(GraphicText.box_top(0, 0, 40))
           .add(Text(1, 1, "GRAPHIC ESCAPE BORDER"))
           .add(GraphicText.box_bottom(2, 0, 40)))
    port = _serve_one_screen(scr.render())
    out, trace = _drive_traced(port, [
        "Wait(3,Output)",
        "Ascii()",
        "Wait(1,Seconds)",
        "Quit()",
    ], basic=True)

    # Deterministic: the emulator parsed the GE order(s) off the wire.
    assert "GraphicEscape" in trace, trace[-2000:]
    # And it rendered the horizontal run into a border. Depending on font mode the
    # line-drawing glyph shows as Unicode U+2500 '─' or ws3270's ASCII fallback
    # '-' (apla2uc[]); a 20-long run of either is unmistakably our border, not the
    # "GRAPHIC ESCAPE BORDER" label between the edges.
    assert ("─" * 20 in out) or ("-" * 20 in out), out[-1500:]


def _query_one_terminal():
    """Negotiate a real client and run the production `query_terminal` against it,
    capturing the resulting model — so a smoke test can assert what the server
    discovered from a real terminal's Query Reply. Returns (port, result, thread);
    result["model"] is filled once the client has been queried."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    result = {}

    def serve():
        conn = None
        try:
            conn, _ = srv.accept()
            model, _ = server.tn3270_negotiate(conn)
            result["model"] = server.query_terminal(conn, model)
        except Exception as exc:            # pragma: no cover - diagnostics only
            result["err"] = repr(exc)
        finally:
            if conn is not None:
                conn.close()
            srv.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    return port, result, t


def test_ws3270_charset_discovery_reports_graphic_escape():
    """Querying a real ws3270 (basic-TN3270 -E) decodes its Character Sets reply:
    the server discovers Graphic Escape support and the base/APL CGCSGIDs from the
    actual 0x85 payload — proving the parser works against a real terminal, and
    that #136's GE emission has a real capability to gate on."""
    _require_emulator()
    port, result, t = _query_one_terminal()
    _drive_traced(port, ["Wait(4,Output)", "Quit()"], basic=True)
    t.join(5)

    model = result.get("model")
    assert model is not None, result
    assert model.graphic_escape is True                  # advertises the GE set
    assert model.base_cgcsgid is not None
    assert (model.base_cgcsgid & 0xFFFF) == 37           # base CPGID 37 = CP037
    # The APL / line-drawing graphic set (CP310) GE draws from.
    assert any((cg & 0xFFFF) == 310 for (_s, _f, _l, cg) in model.char_sets), model.char_sets


def test_ws3270_german_charset_selects_cp273():
    """A German-configured ws3270 reports base CPGID 273 in its Character Sets
    reply; the server resolves that to the cp273 code page for the session —
    real discovery driving real code-page selection (not a synthetic reply)."""
    _require_emulator()
    port, result, t = _query_one_terminal()
    _drive_traced(port, ["Wait(4,Output)", "Quit()"], basic=True, charset="german")
    t.join(5)

    model = result.get("model")
    assert model is not None, result
    assert (model.base_cgcsgid & 0xFFFF) == 273           # CPGID 273 (German)
    assert server.code_page_for_model(model) == "cp273"


def test_ws3270_german_terminal_reads_cp273_encoded_text():
    """End-to-end code-page agreement: text the server encodes in cp273 is what a
    German ws3270 displays. '@' is 0x7C in cp037 but 0xB5 in cp273 — so if the
    server used the wrong page the emulator would show 'Ä' instead of '@'."""
    _require_emulator()
    from screen import Screen, Text

    # Render the screen under the German session page, exactly as handle_client
    # would once it resolved cp273 from the terminal's Character Sets reply.
    server._session.code_page = "cp273"
    try:
        record = Screen().add(Text(1, 1, "AT=@ END")).render()
    finally:
        del server._session.code_page

    port = _serve_one_screen(record)
    out, _ = _drive_traced(port, [
        "Wait(3,Output)", "Ascii()", "Wait(1,Seconds)", "Quit()",
    ], basic=True, charset="german")

    assert "AT=@ END" in out, out[-1500:]     # '@' round-tripped, not mangled to 'Ä'


def test_ws3270_ispf_menu_emphasises_keywords_via_hp():
    """The ISPF menu's <hp>-authored instruction line ("Enter X or PF3 to
    terminate ISPF.") emphasises its action keywords with SA colour runs. A real
    ws3270 processes the SetAttribute orders and renders the line — proving that
    declarative <hp> reaches the wire as #110's character-level SA mechanism."""
    _require_emulator()
    from dtl import load_panel

    scr = load_panel("ispf", ZUSER="IBMUSER ", ZTIME="13:45")
    port = _serve_one_screen(scr.render(color=True))
    out, trace = _drive_traced(port, [
        "Wait(3,Output)", "Ascii()", "Wait(1,Seconds)", "Quit()",
    ], basic=True)

    assert "SetAttribute" in trace, trace[-2000:]                 # SA on the wire
    assert "Enter X or PF3 to terminate ISPF." in out, out[-1500:]  # line intact


def test_ws3270_starttls_upgrades_a_plaintext_connection(tls_cert):
    """A real ws3270 connecting in the clear accepts the negotiated START-TLS
    upgrade and runs the whole session over TLS — logging in and reaching the ISPF
    menu — proving the in-band upgrade interoperates with a real emulator (which
    enables START-TLS by default). Contrast test_tls.py's implicit-TLS `L:` path."""
    _require_emulator()
    certfile, keyfile = tls_cert
    ctx = server.make_tls_context(certfile, keyfile)
    port = _serve_one_client(tls_context=ctx, starttls=True)
    out = _drive(port, [
        "Wait(20,InputField)",
        "String(IBMUSER)", "Tab()", "String(SYS1)", "Enter()",
        "Wait(20,Output)",
        "Ascii()",
        "Quit()",
    ], noverify=True)     # plaintext connect (no L:), accept the self-signed cert

    assert "ISPF Primary Option Menu" in out, out[-1500:]


def test_ws3270_logon_error_sounds_the_alarm():
    """A bad logon (wrong password) shows the error AND sounds the terminal alarm
    — real TSO/ISPF beeps on a logon error. tsomsgs marks those messages
    msgtype=WARNING, which sets the WCC sound-alarm bit; the emulator's trace
    records it. A *successful* logon panel carries no alarm."""
    _require_emulator()
    port = _serve_one_client()
    out, trace = _drive_traced(port, [
        "Wait(20,InputField)",
        "String(IBMUSER)", "Tab()", "String(WRONGPW)", "Enter()",
        "Wait(20,Output)",
        "Ascii()",
        "Quit()",
    ])
    assert "PASSWORD NOT CORRECT" in out, out[-1200:]
    assert "alarm" in trace, trace[-2000:]     # WCC sound-alarm bit on the error write


def test_ws3270_field_level_help_on_the_size_field():
    """Context-sensitive HELP: with the cursor in the logon Size field, PF1 shows
    that field's own help (sizehelp), not the panel's general help (tsohelp) —
    <dtafld help="sizehelp">. The server resolves the inbound cursor address to the
    field."""
    _require_emulator()
    port = _serve_one_client()
    out = _drive(port, [
        "Wait(20,InputField)",
        "MoveCursor(7,16)",     # into the Size field (row 7, data col 16)
        "PF(1)",                # HELP
        "Wait(10,Output)",
        "Ascii()",              # the field-help panel
        "PF(3)",                # return to logon
        "Wait(5,Output)",
        "Quit()",
    ])
    assert "Size Field HELP" in out, out[-1500:]     # sizehelp, not tsohelp
    assert "region size" in out                       # its body text


def test_ws3270_settings_action_bar_underlines_mnemonics():
    """The Settings panel's action bar underlines each choice's mnemonic letter
    (authored with <M>) via an SA highlight — verified on a real ws3270: its trace
    shows the SetAttribute order and the labels render."""
    _require_emulator()
    from dtl import load_panel

    scr = load_panel("settings")
    port = _serve_one_screen(scr.render(color=True))
    out, trace = _drive_traced(port, [
        "Wait(3,Output)", "Ascii()", "Wait(1,Seconds)", "Quit()",
    ], basic=True)

    assert "SetAttribute" in trace, trace[-2000:]        # mnemonic underline on the wire
    assert "Log/List" in out and "Colors" in out, out[-1500:]


def test_ws3270_help_works_inside_the_settings_overlay():
    """PF1 inside an overlay panel (the Settings action-bar panel, ISPF option 0)
    now shows its help — overlays ignored HELP entirely before. Verified on a real
    emulator: HELP from Settings brings up the help panel (ispfhelp)."""
    _require_emulator()
    port = _serve_one_client()
    out = _drive(port, [
        "Wait(20,InputField)",
        "String(IBMUSER)", "Tab()", "String(SYS1)", "Enter()",
        "Wait(20,Output)",          # ISPF menu
        "String(0)", "Enter()",     # option 0 -> Settings overlay
        "Wait(10,Output)",          # Settings panel (action bar)
        "Ascii()",
        "PF(1)",                    # HELP
        "Wait(10,Output)",
        "Ascii()",                  # the help panel
        "Quit()",
    ])
    assert "ISPF Settings" in out, out[-1500:]      # reached the Settings overlay
    assert "Menu - HELP" in out, out[-1500:]         # HELP (ispfhelp) then shown


def test_ws3270_help_works_on_a_pulldown_item():
    """PF1 with the cursor on an open pull-down item shows that item's own help
    (DTL <pdc help=...>). Verified on a real emulator: from Settings, opening the
    Log/List pull-down and pressing HELP on "Log Data Set defaults" brings up the
    loglisthelp panel."""
    _require_emulator()
    port = _serve_one_client()
    out = _drive(port, [
        "Wait(20,InputField)",
        "String(IBMUSER)", "Tab()", "String(SYS1)", "Enter()",
        "Wait(20,Output)",              # ISPF menu
        "String(0)", "Enter()",         # option 0 -> Settings overlay
        "Wait(10,Output)",              # Settings panel (action bar)
        "MoveCursor(0,2)", "Enter()",   # cursor on "Log/List" choice -> open pull-down
        "Wait(10,Output)",
        "Ascii()",                      # the open pull-down
        "MoveCursor(2,2)", "PF(1)",     # HELP on the "Log Data Set defaults" item
        "Wait(10,Output)",
        "Ascii()",                      # the item help panel
        "Quit()",
    ])
    assert "Log Data Set defaults" in out, out[-1500:]        # pull-down opened
    assert "Log Data Set Defaults HELP" in out, out[-1500:]   # item help then shown
