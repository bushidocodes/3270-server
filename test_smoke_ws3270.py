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
pytestmark = pytest.mark.skipif(
    EMULATOR is None, reason="no ws3270/s3270 emulator installed")


def _serve_one_client():
    """Listen on an ephemeral port and serve exactly one client through the real
    :func:`server.handle_client` in a background thread. Returns the port."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        try:
            conn, addr = srv.accept()
            server.handle_client(conn, addr)
        except Exception:
            pass
        finally:
            srv.close()

    threading.Thread(target=serve, daemon=True).start()
    return port


def _drive(port, actions):
    """Run the emulator against 127.0.0.1:port with a list of action commands,
    returning its combined output. A 60s hang (the #87 bug) can't wedge the run:
    the per-action Wait() timeouts bound it, and subprocess timeout is the
    backstop."""
    script = "\n".join(actions) + "\n"
    try:
        proc = subprocess.run(
            [EMULATOR, "-model", "2", f"127.0.0.1:{port}"],
            input=script, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"emulator did not finish in time (possible session hang): "
                    f"{(exc.output or '')[:500]}")
    return proc.stdout + proc.stderr


def test_ws3270_logs_in_and_navigates():
    """A real emulator negotiates, logs in, and drives the ISPF panels."""
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
