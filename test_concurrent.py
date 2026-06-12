"""
Concurrent load test: 10 simultaneous TN3270 clients each complete a full
login → ISPF menu → logoff cycle and report pass/fail.
"""
import socket
import threading
import time

IAC, EOR, DO, DONT, WILL, WONT, SB, SE = 0xFF, 0xEF, 0xFD, 0xFE, 0xFB, 0xFC, 0xFA, 0xF0
BINARY, TERMINAL_TYPE, EOR_OPT = 0, 24, 25

HOST, PORT = "localhost", 2323
NUM_CLIENTS = 10
TIMEOUT = 30

CREDENTIALS = [("IBMUSER", "SYS1"), ("TESTUSER", "RACF")]


def recv_until_eor(sock):
    buf = bytearray()
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("Connection closed")
        buf.extend(chunk)
        if buf[-2:] == bytes([IAC, EOR]):
            return bytes(buf[:-2])


def negotiate(sock):
    """Minimal TN3270 negotiation: agree to BINARY + EOR + TERMINAL-TYPE."""
    sock.sendall(bytes([
        IAC, WILL, BINARY,
        IAC, DO,   BINARY,
        IAC, WILL, EOR_OPT,
        IAC, DO,   EOR_OPT,
        IAC, DO,   TERMINAL_TYPE,
    ]))

    buf = bytearray()
    got_binary = got_eor = got_term_req = False
    deadline = time.time() + TIMEOUT

    while not (got_binary and got_eor and got_term_req):
        if time.time() > deadline:
            raise TimeoutError("Negotiation timed out")
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("Closed during negotiation")
        buf.extend(chunk)

        i = 0
        while i < len(buf):
            if buf[i] != IAC:
                i += 1
                continue
            if i + 1 >= len(buf):
                break
            cmd = buf[i + 1]
            if cmd in (DO, DONT, WILL, WONT):
                if i + 2 >= len(buf):
                    break
                opt = buf[i + 2]
                if cmd == DO:
                    sock.sendall(bytes([IAC, WILL, opt]))
                elif cmd == DONT:
                    sock.sendall(bytes([IAC, WONT, opt]))
                elif cmd == WILL:
                    sock.sendall(bytes([IAC, DO, opt]))
                elif cmd == WONT:
                    sock.sendall(bytes([IAC, DONT, opt]))
                if opt == BINARY and cmd in (DO, WILL):
                    got_binary = True
                if opt == EOR_OPT and cmd in (DO, WILL):
                    got_eor = True
                i += 3
            elif cmd == SB:
                se = buf.find(bytes([IAC, SE]), i + 2)
                if se == -1:
                    break
                opt = buf[i + 2]
                if opt == TERMINAL_TYPE and i + 3 < len(buf) and buf[i + 3] == 1:
                    # Server is asking for our terminal type
                    term = b"IBM-3278-2"
                    sock.sendall(bytes([IAC, SB, TERMINAL_TYPE, 0]) + term + bytes([IAC, SE]))
                    got_term_req = True
                elif opt == TERMINAL_TYPE and i + 3 < len(buf) and buf[i + 3] == 0:
                    got_term_req = True
                i = se + 2
            else:
                i += 2

        buf = buf[i:]  # consume processed bytes


def build_aid(userid, password):
    """Build a minimal Enter AID response with userid + password fields."""
    # Field addresses from server.py
    # Userid field: row 5, col 17 (SF at col 16, data at col 17) → addr = 5*80+17 = 417
    # Password field: row 6, col 17 → addr = 6*80+17 = 497
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
    buf.extend(pack_addr(417))

    # Userid field
    buf.append(SBA)
    buf.extend(pack_addr(417))
    buf.extend(to_ebcdic(userid))

    # Password field
    buf.append(SBA)
    buf.extend(pack_addr(497))
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

            negotiate(sock)
            steps.append("negotiated")

            data = recv_until_eor(sock)
            assert 0xF5 in data, "Expected ERASE_WRITE in logon panel"
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


if __name__ == "__main__":
    exit(main())
