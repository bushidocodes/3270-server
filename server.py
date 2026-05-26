import socket
import binascii
from enum import Enum


def to_ebcdic(s: str) -> bytes:
    return s.encode("cp037")  # US EBCDIC


def encode_pack_addr(row: int, col: int, cols=80) -> bytes:
    """Encodes a 12-bit 3270 presentation space address from row/col"""

    # Width was pre-negotiated with client, usually 80 or 132
    addr = row * cols + col

    # Validate fits in 12 bits
    if addr < 0 or addr >= 0x1000:
        raise ValueError("Address out of range")

    hi_chunk = (addr >> 6) & 0b0011_1111
    lo_chunk = addr & 0b0011_1111

    hi_zone_bits = 0b1100_0000  # bits 7-6 are 11
    lo_zone_bits = 0b0100_0000  # bits 7-6 are 01

    # encoded as a packed 12-bit value called a presentation space address
    hi = hi_chunk | hi_zone_bits
    lo = lo_chunk | lo_zone_bits
    return bytes([hi, lo])


def write_control_character(
    reset_mdts: bool = True,
    sound_alarm: bool = False,
    keyboard_restore: bool = False,
    start_printer: bool = False,
) -> bytes:
    wcc = 0x00
    if reset_mdts:
        wcc |= 0x40
    if sound_alarm:
        wcc |= 0x20
    if start_printer:
        wcc |= 0x08
    if keyboard_restore:
        wcc |= 0x10
    return bytes([wcc])


class DisplayIntensity(Enum):
    NORMAL = 0
    HIGH = 1
    HIGHLIGHTED = 2
    NON_DISPLAY = 3


class FieldType(Enum):
    ALPHANUMERIC = 0
    NUMERIC = 1


def field_attribute(
    display: DisplayIntensity = DisplayIntensity.NORMAL,
    protected: bool = True,
    field_type: FieldType = FieldType.ALPHANUMERIC,
    mdt: bool = False,
) -> int:
    """Returns a field attribute byte for a 3270 field"""
    attr = 0x00
    if display == DisplayIntensity.HIGH:
        attr |= 0b0100_0000  # High intensity
    elif display == DisplayIntensity.HIGHLIGHTED:
        attr |= 0b1000_0000  # Highlighted
    elif display == DisplayIntensity.NON_DISPLAY:
        attr |= 0b1100_0000  # Non-display (bits 7-6 set to 11)
    if protected:
        attr |= 0b0010_0000  # Protected field
    if field_type == FieldType.NUMERIC:
        attr |= 0b0001_0000  # Numeric field
    if mdt:
        attr |= 0b0000_0001  # Modified Data Tag
    return attr


def send_logon_panel(client_socket):
    IAC = 0xFF  # Telnet Interpret As Command
    EOR = 0xEF  # Telnet End of Record

    ERASE_WRITE = 0xF5
    SBA = 0x11  # Set Buffer Address
    IC = 0x13  # Insert Cursor
    SF = 0x1D  # Start Field

    buf = bytearray()

    # 1. Clear screen
    # Every Write (0xF1) or Erase/Write (0xF5) should be followed by a WCC byte
    buf.append(ERASE_WRITE)
    buf.extend(write_control_character(reset_mdts=True, keyboard_restore=True))

    # 2. Moves cursor to row 0, col 0 and writes title
    buf.extend([SBA])
    buf.extend(encode_pack_addr(0, 0))
    buf.extend([SF, field_attribute(protected=True)])
    buf.extend(to_ebcdic("Welcome to MVS/TSO - LOGON"))

    # 3. Moves cursor to row row 4, col 0 and writes USERID prompt
    buf.extend([SBA])
    buf.extend(encode_pack_addr(4, 0))
    buf.extend([SF, field_attribute(protected=True)])
    buf.extend(to_ebcdic("USERID:"))

    # 4. Moves cursor to row 4, col 10 and creates a USERID input field of length 8
    buf.extend([SBA])
    buf.extend(encode_pack_addr(4, 10))
    buf.extend(
        [SF, field_attribute(protected=False, mdt=True)]
    )  # Start Field, unprotected
    buf.extend(to_ebcdic(" " * 8))
    buf.extend([SBA])  # Terminate previous field
    buf.extend(encode_pack_addr(4, 19))
    buf.extend([SF, field_attribute(protected=True)])

    # 5. Moves cursor to row 6, col 0 and writes PASSWORD prompt
    buf.extend([SBA])
    buf.extend(encode_pack_addr(6, 0))
    buf.extend([SF, field_attribute(protected=True)])
    buf.extend(to_ebcdic("PASSWORD:"))

    # 6. Moves cursor to row 6, col 10 and creates a PASSWORD input field of length 8
    buf.extend([SBA])
    buf.extend(encode_pack_addr(6, 10))
    buf.extend(
        [
            SF,
            field_attribute(
                protected=False, display=DisplayIntensity.NON_DISPLAY, mdt=True
            ),
        ]
    )  # Start Field, unprotected, non-display
    buf.extend(to_ebcdic(" " * 8))
    buf.extend([SBA])  # Terminate previous field
    buf.extend(encode_pack_addr(6, 19))
    buf.extend([SF, field_attribute(protected=True)])  # Start Field, protected

    # 7. Appends telnet record terminator IAC EOR
    buf.extend([IAC, EOR])

    print("TX:", binascii.hexlify(buf))
    client_socket.sendall(buf)


def aid_to_string(aid: int):
    aid_codes = {
        0x60: "No AID",
        0x7D: "Enter",
        0x6D: "Clear",
        0x6C: "PA1",
        0x6E: "PA2",
        0x6B: "PA3",
        0xF1: "PF1",
        0xF2: "PF2",
        0xF3: "PF3",
        0xF4: "PF4",
        0xF5: "PF5",
        0xF6: "PF6",
        0xF7: "PF7",
        0xF8: "PF8",
        0xF9: "PF9",
        0x7A: "PF10",
        0x7B: "PF11",
        0x7C: "PF12",
        0xC1: "PF13",
        0xC2: "PF14",
        0xC3: "PF15",
        0xC4: "PF16",
        0xC5: "PF17",
        0xC6: "PF18",
        0xC7: "PF19",
        0xC8: "PF20",
        0xC9: "PF21",
        0x4A: "PF22",
        0x4B: "PF23",
        0x4C: "PF24",
    }
    return aid_codes.get(aid, f"Unknown AID {hex(aid)}")


def read_client_input(client_socket):
    IAC = 0xFF
    EOR = 0xEF

    buffer = bytearray()
    while True:
        data = client_socket.recv(1024)
        if not data:
            return None
        buffer.extend(data)

        # Look for IAC EOR terminator
        if len(buffer) >= 2 and buffer[-2:] == bytes([IAC, EOR]):
            break

    print("RX:", binascii.hexlify(buffer))

    # Strip off IAC EOR
    buffer = buffer[:-2]

    # First byte is Attention Identifier (AID).
    # This is the key the user pressed to submit the form, e.g. Enter, PF1, etc.
    aid = buffer[0]
    print(f"AID: {aid_to_string(aid)}")

    # The rest is field data with SBA orders
    SBA = 0x11
    SF = 0x1D
    results = {}
    i = 1
    while i < len(buffer):
        if buffer[i] == SBA and i + 2 < len(buffer):
            # Parse packed address
            addr_hi, addr_lo = buffer[i + 1], buffer[i + 2]
            addr = ((addr_hi & 0x3F) << 6) | (addr_lo & 0x3F)
            i += 3

            # Try to read field data until next SBA or SF
            field_bytes = bytearray()
            while i < len(buffer) and buffer[i] not in (SBA, SF):
                field_bytes.append(buffer[i])
                i += 1
            field_text = field_bytes.decode("cp037").strip()
            if field_text:
                results[addr] = field_text
        else:
            i += 1

    return aid, results


def tn3270_negotiate(client_socket):
    IAC = 255
    DONT = 254
    DO = 253
    WONT = 252
    WILL = 251
    SB = 250
    SE = 240

    # Telnet option codes
    BINARY = 0
    TERMINAL_TYPE = 24
    EOR = 25

    # Track negotiation state
    got_binary = False
    got_eor = False
    got_term = False

    # Step 1: advertise what we want
    # Advertise options in a single packet
    negot = bytearray()
    negot.extend([IAC, WILL, BINARY])
    negot.extend([IAC, DO, BINARY])
    negot.extend([IAC, WILL, EOR])
    negot.extend([IAC, DO, EOR])
    negot.extend([IAC, WILL, TERMINAL_TYPE])
    negot.extend([IAC, DO, TERMINAL_TYPE])

    negot.extend(
        [IAC, SB, TERMINAL_TYPE, 1, IAC, SE]
    )  # IAC SB TERMINAL-TYPE SEND IAC SE

    print("TX:", binascii.hexlify(negot))

    client_socket.sendall(negot)

    buffer = bytearray()
    client_socket.settimeout(60.0)

    while not (got_binary and got_eor and got_term):
        data = client_socket.recv(1024)
        if not data:
            break
        buffer.extend(data)
        print("RX:", binascii.hexlify(data))

        i = 0
        while i < len(buffer):
            if buffer[i] != IAC:
                i += 1
                continue

            cmd = buffer[i + 1]

            # Option negotiation
            if cmd in (DO, DONT, WILL, WONT):
                opt = buffer[i + 2]
                if cmd == DO:
                    client_socket.sendall(bytes([IAC, WILL, opt]))
                elif cmd == DONT:
                    client_socket.sendall(bytes([IAC, WONT, opt]))
                elif cmd == WILL:
                    client_socket.sendall(bytes([IAC, DO, opt]))
                elif cmd == WONT:
                    client_socket.sendall(bytes([IAC, DONT, opt]))

                # Track what we got
                if opt == BINARY and cmd in (DO, WILL):
                    got_binary = True
                if opt == EOR and cmd in (DO, WILL):
                    got_eor = True

                i += 3
                continue

            # Subnegotiation
            if cmd == SB:
                opt = buffer[i + 2]
                if opt == TERMINAL_TYPE:
                    subopt = buffer[i + 3]
                    if subopt == 1:  # SEND
                        # Respond with terminal type
                        term = b"IBM-3278-2"
                        reply = (
                            bytes([IAC, SB, TERMINAL_TYPE, 0]) + term + bytes([IAC, SE])
                        )
                        print("TX:", binascii.hexlify(reply))
                        client_socket.sendall(reply)
                    elif subopt == 0:  # IS
                        # Client told us its type
                        term_type = buffer[i + 4 : buffer.index(IAC, i + 4)].decode(
                            errors="ignore"
                        )
                        print("Client terminal type:", term_type)
                        got_term = True

                # Skip to SE
                se_pos = buffer.find(bytes([IAC, SE]), i + 3)
                if se_pos != -1:
                    i = se_pos + 2
                else:
                    break
                continue
            else:
                print("Unknown IAC command:", cmd)

            i += 2

    print(
        "Negotiation complete: binary={}, eor={}, term={}".format(
            got_binary, got_eor, got_term
        )
    )


def handle_client(client_socket, addr):
    print(f"Connection from {addr}")
    tn3270_negotiate(client_socket)
    send_logon_panel(client_socket)

    # Set 10 minute timeout for input
    client_socket.settimeout(600)
    # Wait for user to type and press Enter
    try:
        user_input = read_client_input(client_socket)
        if user_input:
            aid, fields = user_input
            print(f"AID={hex(aid)}, fields={fields}")

            # Example: fetch USERID and PASSWORD by their field start addresses
            for addr, text in fields.items():
                print(f"Field at {addr}: {text}")
    except TimeoutError:
        print("Client input timed out after 10 minutes.")

    client_socket.close()


def run_tn3270_server(host="0.0.0.0", port=23):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind((host, port))
        server_socket.listen(1)
        print(f"TN3270 server listening on {host}:{port}")
        while True:
            client_socket, addr = server_socket.accept()
            try:
                handle_client(client_socket, addr)
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                print(f"Client {addr} disconnected unexpectedly")
            finally:
                client_socket.close()


if __name__ == "__main__":
    run_tn3270_server()
