"""
Test that malformed client input does NOT crash the TN3270 server (issue #4).

Verifies:
  1. Server survives a lone IAC byte (recv boundary – buffer[i+1] OOB)
  2. Server survives IAC DO with no option byte (buffer[i+2] OOB)
  3. Server survives IAC SB with only 2 bytes (buffer[i+3] OOB)
  4. Server survives a bare IAC EOR → empty AID buffer (buffer[0] OOB)
  5. After EVERY bad client the server is still alive (accepts a new connection)
"""
import socket
import sys
import threading
import time

# ── constants ──────────────────────────────────────────────────────────────────
IAC  = 0xFF
EOR  = 0xEF   # Telnet EOR option value
DO   = 0xFD
WILL = 0xFB
SB   = 0xFA
BINARY = 0x00
TERMINAL_TYPE = 0x18
EOR_OPT = 0x19

TEST_PORT = 13270  # non-standard to avoid collisions

# ── server bootstrap ───────────────────────────────────────────────────────────
sys.path.insert(0, ".")
from server import run_tn3270_server   # noqa: E402

_server_thread = threading.Thread(
    target=run_tn3270_server,
    kwargs={"host": "127.0.0.1", "port": TEST_PORT},
    daemon=True,
)
_server_thread.start()
time.sleep(0.5)   # let server bind

# ── helpers ────────────────────────────────────────────────────────────────────
def _open_and_drain(timeout=3):
    """Connect, drain the server's opening negotiation bytes, return socket."""
    s = socket.socket()
    s.settimeout(timeout)
    s.connect(("127.0.0.1", TEST_PORT))
    try:
        s.recv(4096)   # absorb WILL BINARY, WILL EOR, DO TERMINAL_TYPE
    except socket.timeout:
        pass
    return s


def server_alive(timeout=3):
    """Return True if the server still accepts a fresh TCP connection."""
    try:
        s = socket.socket()
        s.settimeout(timeout)
        s.connect(("127.0.0.1", TEST_PORT))
        data = s.recv(4096)
        s.close()
        return len(data) > 0
    except Exception:
        return False


PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_failures = []

def check(name, ok):
    tag = PASS if ok else FAIL
    print(f"  [{tag}] {name}")
    if not ok:
        _failures.append(name)


# ── test cases ─────────────────────────────────────────────────────────────────

def case_lone_iac():
    """Single IAC byte – i+1 would have been out of bounds."""
    s = _open_and_drain()
    s.sendall(bytes([IAC]))
    s.close()
    time.sleep(0.3)

def case_iac_do_truncated():
    """IAC DO with no option – i+2 would have been out of bounds."""
    s = _open_and_drain()
    s.sendall(bytes([IAC, DO]))
    s.close()
    time.sleep(0.3)

def case_iac_sb_truncated():
    """IAC SB with only one byte after – i+3 would have been out of bounds."""
    s = _open_and_drain()
    s.sendall(bytes([IAC, SB, TERMINAL_TYPE]))   # missing 4th byte
    s.close()
    time.sleep(0.3)

def case_bare_iac_eor():
    """Just IAC EOR – strips to empty buffer, buffer[0] would have crashed."""
    s = _open_and_drain()
    s.sendall(bytes([IAC, EOR]))
    s.close()
    time.sleep(0.3)


CASES = [
    ("Lone IAC byte (recv boundary)",              case_lone_iac),
    ("IAC DO without option byte",                 case_iac_do_truncated),
    ("IAC SB with truncated payload",              case_iac_sb_truncated),
    ("Bare IAC EOR -> empty AID buffer",            case_bare_iac_eor),
]


# ── main ───────────────────────────────────────────────────────────────────────
def main():
    print(f"\nTN3270 server robustness test  (port {TEST_PORT})\n")

    print("Pre-flight: server is accepting connections …")
    check("server alive at start", server_alive())
    print()

    for label, fn in CASES:
        print(f"Case: {label}")
        fn()
        alive = server_alive()
        check("server still alive after bad client", alive)
        print()

    if _failures:
        print(f"\n{len(_failures)} test(s) FAILED: {_failures}")
        return 1

    print("All tests passed.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
