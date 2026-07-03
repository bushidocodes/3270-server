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


def _serve_one_client(tls_context=None):
    """Listen on an ephemeral port and serve exactly one client through the real
    :func:`server.handle_client` in a background thread. Returns the port. When
    ``tls_context`` is given, the connection is served through
    :func:`server._client_thread`, which does the server-side TLS handshake."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        try:
            conn, addr = srv.accept()
            if tls_context is not None:
                server._client_thread(conn, addr, tls_context=tls_context)
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


def _drive(port, actions, model="2", tls=False):
    """Run the emulator (as the given ``model``) against 127.0.0.1:port with a
    list of action commands, returning its combined output. A 60s hang (the #87
    bug) can't wedge the run: the per-action Wait() timeouts bound it, and
    subprocess timeout is the backstop. ``tls`` connects via ``L:`` and accepts
    the self-signed test cert (``-noverifycert``)."""
    script = "\n".join(actions) + "\n"
    cmd = [EMULATOR, "-model", model]
    if tls:
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


def _drive_traced(port, actions, model="2", basic=False):
    """Like :func:`_drive`, but also captures the emulator's protocol trace and
    returns ``(output, trace_text)``. The trace records exactly what the emulator
    SENT and RCVD on the wire, so assertions on it are deterministic — unlike
    ``Ascii()`` screen dumps, which race the emulator's own screen rendering.
    ``basic`` forces the basic-TN3270 (``N:``) path."""
    with tempfile.NamedTemporaryFile(
            prefix="ws3270-", suffix=".trace", delete=False) as tf:
        trace_path = tf.name
    try:
        script = "\n".join(actions) + "\n"
        try:
            proc = subprocess.run(
                [EMULATOR, "-model", model, "-trace", "-tracefile", trace_path,
                 _host_arg(port, basic=basic)],
                input=script, capture_output=True, text=True,
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
