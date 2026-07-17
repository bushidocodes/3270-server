"""
Concurrent load test: 10 simultaneous TN3270 clients each complete a full
login → ISPF menu → logoff cycle and report pass/fail.
"""
import socket
import threading
import time

IAC, EOR, DO, DONT, WILL, WONT, SB, SE = 0xFF, 0xEF, 0xFD, 0xFE, 0xFB, 0xFC, 0xFA, 0xF0
BINARY, TERMINAL_TYPE, EOR_OPT = 0, 24, 25

HOST, PORT = "127.0.0.1", 13271
NUM_CLIENTS = 10
TIMEOUT = 30

CREDENTIALS = [("IBMUSER", "SYS1"), ("TESTUSER", "RACF")]


def recv_until_eor(sock, initial=b""):
    """Read one 3270 record terminated by IAC EOR.

    `initial` seeds the buffer with any bytes that negotiate() already read past
    the end of negotiation (the start of the screen), so nothing is lost.
    """
    buf = bytearray(initial)
    while not (len(buf) >= 2 and buf[-2:] == bytes([IAC, EOR])):
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("Connection closed")
        buf.extend(chunk)
    return bytes(buf[:-2])


def negotiate(sock):
    """Minimal TN3270 negotiation: agree to BINARY + EOR + TERMINAL-TYPE.

    Drains *all* of the server's Telnet option replies — including the ones it
    sends after the essentials are agreed — and stops exactly at the start of
    the 3270 data stream (the first non-IAC byte). This prevents leftover
    negotiation bytes from sitting ahead of the first screen.

    Returns any screen bytes read in the same recv() as the trailing
    negotiation, so the caller can prepend them:
        data = recv_until_eor(sock, negotiate(sock))

    Replies to options are sent only until the essentials are agreed; trailing
    confirmations are then drained silently, so no extra option replies reach
    the server's input stream after negotiation completes.
    """
    sock.sendall(bytes([
        IAC, WILL, BINARY,
        IAC, DO,   BINARY,
        IAC, WILL, EOR_OPT,
        IAC, DO,   EOR_OPT,
        IAC, DO,   TERMINAL_TYPE,
    ]))

    buf = bytearray()
    got_binary = got_eor = got_term = False
    deadline = time.time() + TIMEOUT

    while True:
        # Consume every complete Telnet command at the front of the buffer.
        while buf and buf[0] == IAC:
            if len(buf) < 2:
                break  # partial command; need more bytes
            cmd = buf[1]
            if cmd in (DO, DONT, WILL, WONT):
                if len(buf) < 3:
                    break
                opt = buf[2]
                if not (got_binary and got_eor and got_term):
                    reply = {DO: WILL, DONT: WONT, WILL: DO, WONT: DONT}[cmd]
                    if opt == 40:  # refuse TN3270E → exercise the basic TN3270 path
                        reply = {DO: WONT, WILL: DONT}.get(cmd, reply)
                    sock.sendall(bytes([IAC, reply, opt]))
                if opt == BINARY and cmd in (DO, WILL):
                    got_binary = True
                if opt == EOR_OPT and cmd in (DO, WILL):
                    got_eor = True
                del buf[:3]
            elif cmd == SB:
                se = buf.find(bytes([IAC, SE]), 2)
                if se == -1:
                    break  # incomplete subnegotiation
                if len(buf) >= 4 and buf[2] == TERMINAL_TYPE and buf[3] == 1:
                    # SEND request → reply with our terminal type (IS)
                    sock.sendall(bytes([IAC, SB, TERMINAL_TYPE, 0]) + b"IBM-3278-2" + bytes([IAC, SE]))
                    got_term = True
                del buf[:se + 2]
            else:
                del buf[:2]  # other two-byte command

        # A non-IAC byte at the front means we've reached the 3270 data stream.
        if buf and buf[0] != IAC:
            return bytes(buf)

        if time.time() > deadline:
            raise TimeoutError("Negotiation timed out")
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("Closed during negotiation")
        buf.extend(chunk)


def build_aid(userid, password):
    """Build a minimal Enter AID response with userid + password fields."""
    # Field addresses from panels/logon.dtl (auto-flow two-column form)
    # Userid field: row 4, col 16 (SF at col 15, data at col 16) → addr = 4*80+16 = 336
    # Password field: row 5, col 16 → addr = 5*80+16 = 416
    # Address encoding: same as server's encode_pack_addr
    def pack_addr(addr):
        hi = ((addr >> 6) & 0x3F) | 0xC0
        lo = (addr & 0x3F) | 0x40
        return bytes([hi, lo])

    def to_ebcdic(s):
        return s.encode("cp037")

    SBA, IC = 0x11, 0x13
    buf = bytearray()
    buf.append(0x7D)  # Enter AID

    # Cursor position (required after AID)
    buf.append(SBA)
    buf.extend(pack_addr(336))

    # Userid field
    buf.append(SBA)
    buf.extend(pack_addr(336))
    buf.extend(to_ebcdic(userid))

    # Password field
    buf.append(SBA)
    buf.extend(pack_addr(416))
    buf.extend(to_ebcdic(password))

    buf.extend([IAC, EOR])
    return bytes(buf)


def build_pf3():
    """PF3 AID to log off."""
    SBA = 0x11
    def pack_addr(addr):
        hi = ((addr >> 6) & 0x3F) | 0xC0
        lo = (addr & 0x3F) | 0x40
        return bytes([hi, lo])
    buf = bytearray([0xF3])  # PF3 AID
    buf.append(SBA)
    buf.extend(pack_addr(0))
    buf.extend([IAC, EOR])
    return bytes(buf)


def run_client(client_id, results):
    userid, password = CREDENTIALS[client_id % len(CREDENTIALS)]
    steps = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(TIMEOUT)
            sock.connect((HOST, PORT))
            steps.append("connected")

            leftover = negotiate(sock)
            steps.append("negotiated")

            data = recv_until_eor(sock, leftover)
            assert data[:1] == bytes([0xF5]), (
                f"logon panel must start at ERASE_WRITE, not leftover negotiation "
                f"(got {data[:4].hex()})"
            )
            steps.append(f"logon_panel({len(data)}b)")

            sock.sendall(build_aid(userid, password))
            steps.append("aid_sent")

            data = recv_until_eor(sock)
            decoded = data.decode("cp037", errors="replace")
            assert "ISPF" in decoded, f"Expected ISPF menu, got: {decoded[:80]!r}"
            steps.append(f"ispf_menu({len(data)}b)")

            sock.sendall(build_pf3())
            steps.append("pf3_sent")

            data = recv_until_eor(sock)
            steps.append(f"logon_again({len(data)}b)")

        results[client_id] = ("PASS", None, steps)
    except Exception as e:
        results[client_id] = ("FAIL", str(e), steps)


def main():
    results = {}
    threads = []
    start = time.time()

    for i in range(NUM_CLIENTS):
        t = threading.Thread(target=run_client, args=(i, results))
        threads.append(t)

    # Launch all at once
    for t in threads:
        t.start()

    for t in threads:
        t.join(timeout=TIMEOUT + 5)

    elapsed = time.time() - start
    print(f"\n{'='*50}")
    print(f"Results ({NUM_CLIENTS} clients in {elapsed:.2f}s):")
    print(f"{'='*50}")
    passed = failed = 0
    for i in range(NUM_CLIENTS):
        status, err, steps = results.get(i, ("TIMEOUT", "thread never returned", []))
        mark = "PASS" if status == "PASS" else "FAIL"
        creds = CREDENTIALS[i % len(CREDENTIALS)]
        step_str = " -> ".join(steps) if steps else "(no steps)"
        print(f"  [{mark}] client-{i:02d} ({creds[0]}): {step_str}" + (f" !! {err}" if err else ""))
        if status == "PASS":
            passed += 1
        else:
            failed += 1
    print(f"{'='*50}")
    print(f"  PASSED: {passed}/{NUM_CLIENTS}  FAILED: {failed}/{NUM_CLIENTS}")
    print(f"{'='*50}\n")
    return failed


# --- pytest collection (issue #369) ---
import pytest
from server import run_tn3270_server


@pytest.fixture(scope="module", autouse=True)
def _start_server_for_helpers():
    """Start a dedicated server so helpers/tests do not require an external process."""
    t = threading.Thread(
        target=run_tn3270_server,
        kwargs={"host": HOST, "port": PORT},
        daemon=True,
    )
    t.start()
    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            s = socket.create_connection((HOST, PORT), timeout=0.2)
            s.close()
            break
        except OSError:
            time.sleep(0.05)
    else:
        pytest.fail("concurrent-test server failed to bind")
    yield


def test_concurrent_logins():
    """10 concurrent clients complete login -> ISPF menu (was script-only main())."""
    results = {}
    threads = []
    for i in range(NUM_CLIENTS):
        th = threading.Thread(target=run_client, args=(i, results), daemon=True)
        threads.append(th)
        th.start()
    for th in threads:
        th.join(TIMEOUT + 5)
    assert len(results) == NUM_CLIENTS, f"incomplete results: {results}"
    failures = {
        k: v for k, v in results.items() if not (isinstance(v, tuple) and v[0] == "PASS")
    }
    assert not failures, f"client failures: {failures}"
