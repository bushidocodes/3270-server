import socket
import binascii
from datetime import datetime
from enum import Enum


def to_ebcdic(s: str) -> bytes:
    return s.encode("cp037")  # US EBCDIC


def encode_pack_addr(row: int, col: int, cols=80) -> bytes:
    """Encodes a 12-bit 3270 presentation space address from row/col"""
    addr = row * cols + col
    if addr < 0 or addr >= 0x1000:
        raise ValueError("Address out of range")
    hi_chunk = (addr >> 6) & 0b0011_1111
    lo_chunk = addr & 0b0011_1111
    hi = hi_chunk | 0b1100_0000
    lo = lo_chunk | 0b0100_0000
    return bytes([hi, lo])


def write_control_character(
    reset_mdts: bool = True,
    sound_alarm: bool = False,
    keyboard_restore: bool = False,
    start_printer: bool = False,
) -> bytes:
    # WCC bit layout per x3270/wc3270 source (3270ds.h):
    #   0x40 = WCC_RESET_BIT      (always set for normal SNA/LU2 writes)
    #   0x08 = WCC_START_PRINTER_BIT
    #   0x04 = WCC_SOUND_ALARM_BIT
    #   0x02 = WCC_KEYBOARD_RESTORE_BIT  ← unlocks keyboard after AID
    #   0x01 = WCC_RESET_MDT_BIT         ← clears all MDT flags
    wcc = 0x40  # WCC_RESET_BIT: always include for LU2 mode
    if reset_mdts:
        wcc |= 0x01
    if sound_alarm:
        wcc |= 0x04
    if start_printer:
        wcc |= 0x08
    if keyboard_restore:
        wcc |= 0x02
    return bytes([wcc])


class DisplayIntensity(Enum):
    NORMAL = 0
    HIGH = 1
    HIGHLIGHTED = 2
    NON_DISPLAY = 3


class FieldType(Enum):
    ALPHANUMERIC = 0
    NUMERIC = 1


_FA_BASE = 0x40  # bit 6: marks byte as a field attribute (valid FA range 0x40-0x7F)


def field_attribute(
    display: DisplayIntensity = DisplayIntensity.NORMAL,
    protected: bool = True,
    field_type: FieldType = FieldType.ALPHANUMERIC,
    mdt: bool = False,
) -> int:
    attr = _FA_BASE
    if display == DisplayIntensity.HIGH:
        attr |= 0x08          # FA_INT_HIGH_SEL (bits 3-2 = 10)
    elif display == DisplayIntensity.HIGHLIGHTED:
        attr |= 0x04          # FA_INT_NORM_SEL (bits 3-2 = 01)
    elif display == DisplayIntensity.NON_DISPLAY:
        attr |= 0x0C          # FA_INT_ZERO_NSEL (bits 3-2 = 11)
    if protected:
        attr |= 0x20          # FA_PROTECT
    if field_type == FieldType.NUMERIC:
        attr |= 0x10          # FA_NUMERIC
    if mdt:
        attr |= 0x01          # FA_MDT
    return attr


IAC = 0xFF
EOR = 0xEF
SBA = 0x11
SF = 0x1D
IC = 0x13


def _sba(buf: bytearray, row: int, col: int):
    buf.append(SBA)
    buf.extend(encode_pack_addr(row, col))


def _sba_sf(
    buf: bytearray,
    row: int,
    col: int,
    protected: bool = True,
    display: DisplayIntensity = DisplayIntensity.NORMAL,
    field_type: FieldType = FieldType.ALPHANUMERIC,
    mdt: bool = False,
):
    buf.append(SBA)
    buf.extend(encode_pack_addr(row, col))
    buf.append(SF)
    buf.append(field_attribute(display=display, protected=protected, field_type=field_type, mdt=mdt))


def _text(buf: bytearray, s: str):
    buf.extend(to_ebcdic(s))


def _high(buf: bytearray, row: int, col: int, s: str):
    """Write high-intensity protected text at position."""
    _sba_sf(buf, row, col, protected=True, display=DisplayIntensity.HIGH)
    _text(buf, s)


def _normal(buf: bytearray, row: int, col: int, s: str):
    """Write normal-intensity protected text at position."""
    _sba_sf(buf, row, col, protected=True, display=DisplayIntensity.NORMAL)
    _text(buf, s)


def _highlighted(buf: bytearray, row: int, col: int, s: str):
    """Write highlighted (selected-normal) protected text at position."""
    _sba_sf(buf, row, col, protected=True, display=DisplayIntensity.HIGHLIGHTED)
    _text(buf, s)


# Credentials — keys are uppercase userids
_CREDENTIALS = {
    "IBMUSER": "SYS1",
    "TESTUSER": "RACF",
}
# Passwords are stored and compared uppercase (default RACF behavior without MIXEDCASE option)

# Field addresses (row * 80 + col_after_sf) for fields the server reads back
# TSO logon panel: input fields start at col 17, SF is at col 16
LOGON_USERID_SF_COL = 16
LOGON_USERID_ROW = 5
LOGON_PASSWORD_SF_COL = 16
LOGON_PASSWORD_ROW = 6
LOGON_PROC_SF_COL = 16
LOGON_PROC_ROW = 7

LOGON_USERID_ADDR = LOGON_USERID_ROW * 80 + (LOGON_USERID_SF_COL + 1)
LOGON_PASSWORD_ADDR = LOGON_PASSWORD_ROW * 80 + (LOGON_PASSWORD_SF_COL + 1)
LOGON_PROC_ADDR = LOGON_PROC_ROW * 80 + (LOGON_PROC_SF_COL + 1)

# ISPF menu: Option ===> input SF at col 13, data at col 14
ISPF_OPTION_SF_COL = 13
ISPF_OPTION_ROW = 2
ISPF_OPTION_ADDR = ISPF_OPTION_ROW * 80 + (ISPF_OPTION_SF_COL + 1)


def send_tso_logon(client_socket, error_msg: str = None):
    """Send authentic z/OS TSO/E LOGON panel."""
    buf = bytearray()
    buf.append(0xF5)  # ERASE_WRITE
    buf.extend(write_control_character(reset_mdts=True, keyboard_restore=True))

    # Row 0: title bar (centered in 80 cols)
    title = "-" * 8 + "  z/OS V2R5.0 TSO/E LOGON  " + "-" * 8
    title_col = (80 - len(title)) // 2
    _high(buf, 0, title_col, title)

    # Row 2: column headers
    _normal(buf, 2, 1, "Enter LOGON parameters below:")
    _normal(buf, 2, 42, "RACF LOGON parameters:")

    # Row 3: separator line
    _normal(buf, 3, 1, "-" * 37)
    _normal(buf, 3, 42, "-" * 37)

    # Row 5: Userid
    _normal(buf, 5, 1, "Userid   ===>")
    _sba_sf(buf, 5, LOGON_USERID_SF_COL, protected=False, mdt=True)
    _text(buf, " " * 8)
    _sba_sf(buf, 5, LOGON_USERID_SF_COL + 9, protected=True)  # field terminator

    # Insert cursor in userid field
    buf.append(SBA)
    buf.extend(encode_pack_addr(5, LOGON_USERID_SF_COL + 1))
    buf.append(IC)

    # Row 6: Password
    _normal(buf, 6, 1, "Password ===>")
    _sba_sf(buf, 6, LOGON_PASSWORD_SF_COL, protected=False, display=DisplayIntensity.NON_DISPLAY, mdt=True)
    _text(buf, " " * 8)
    _sba_sf(buf, 6, LOGON_PASSWORD_SF_COL + 9, protected=True)

    # Row 7: Procedure
    _normal(buf, 7, 1, "Procedure===>")
    _sba_sf(buf, 7, LOGON_PROC_SF_COL, protected=False, mdt=True)
    _text(buf, "IKJACCNT")
    _sba_sf(buf, 7, LOGON_PROC_SF_COL + 9, protected=True)

    _normal(buf, 7, 42, "Acct Nmbr    ===>")
    _sba_sf(buf, 7, 60, protected=False, mdt=True)
    _text(buf, " " * 8)
    _sba_sf(buf, 7, 69, protected=True)

    # Row 8: Size
    _normal(buf, 8, 1, "Size     ===>")
    _sba_sf(buf, 8, 16, protected=False, field_type=FieldType.NUMERIC, mdt=True)
    _text(buf, "00150")
    _sba_sf(buf, 8, 22, protected=True)

    _normal(buf, 8, 42, "Perform      ===>")
    _sba_sf(buf, 8, 60, protected=False, field_type=FieldType.NUMERIC, mdt=True)
    _text(buf, " " * 8)
    _sba_sf(buf, 8, 69, protected=True)

    # Row 9: Command
    _normal(buf, 9, 1, "Command  ===>")
    _sba_sf(buf, 9, 16, protected=False, mdt=True)
    _text(buf, " " * 62)
    _sba_sf(buf, 9, 79, protected=True)

    # Row 11: Reconnect (right column)
    _normal(buf, 11, 1, "PDS/E Dsname ===>")
    _sba_sf(buf, 11, 19, protected=False, mdt=True)
    _text(buf, " " * 59)
    _sba_sf(buf, 11, 79, protected=True)

    # Row 12: Mail notify
    _normal(buf, 12, 42, "Mail      ===> Yes")
    _normal(buf, 13, 42, "Reconnect ===> Auto")

    # Row 14: Sysout class
    _normal(buf, 14, 42, "OIDcard   ===> None")

    # Row 15: access notice (highlighted intensity — FA_INT_NORM_SEL)
    _highlighted(buf, 15, 1, "*** Authorized users only. Unauthorized access is prohibited. ***")

    # Row 16: Enter/PF key hints
    _normal(buf, 16, 1, "Press ENTER to logon to TSO/E")
    _normal(buf, 17, 1, "PF1=HELP   PF3=LOGOFF")

    # Row 19: error message (high intensity, centered)
    if error_msg:
        err_col = max(0, (80 - len(error_msg)) // 2)
        _high(buf, 19, err_col, error_msg)

    # Row 21: bottom message
    _normal(buf, 21, 1, "ENTER AN END COMMAND TO LOGOFF")

    buf.extend([IAC, EOR])
    print("TX:", binascii.hexlify(buf))
    client_socket.sendall(buf)


_ISPF_OPTIONS = [
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


def send_ispf_menu(client_socket, userid: str, short_msg: str = None):
    """Send authentic ISPF Primary Option Menu."""
    buf = bytearray()
    buf.append(0xF5)  # ERASE_WRITE
    buf.extend(write_control_character(reset_mdts=True, keyboard_restore=True))

    now = datetime.now()
    time_str = now.strftime("%H:%M")

    # Row 0: title border (SF at col 0, text fills cols 1-79)
    inner = " ISPF Primary Option Menu "   # 26 chars
    pad = (79 - len(inner)) // 2           # 26 dashes each side
    border = "-" * pad + inner + "-" * (79 - pad - len(inner))
    _high(buf, 0, 0, border)

    # Row 2: Option ===> label + unprotected input field
    _normal(buf, 2, 1, "Option ===>")
    _sba(buf, 2, ISPF_OPTION_SF_COL)
    buf.append(SF)
    buf.append(field_attribute(protected=False, mdt=True))
    _text(buf, " " * 6)
    _sba_sf(buf, 2, ISPF_OPTION_SF_COL + 7, protected=True)

    # Position cursor in the option field using a separate SBA+IC after the
    # terminator — same pattern as the working logon panel (IC immediately
    # after an SF attr byte causes wc3270 to show X SYSTEM keyboard lock).
    buf.append(SBA)
    buf.extend(encode_pack_addr(ISPF_OPTION_ROW, ISPF_OPTION_SF_COL + 1))
    buf.append(IC)

    if short_msg:
        _high(buf, 2, 25, short_msg[:54])

    # Rows 4-16: single-column option list (avoids any cross-column text bleed)
    # Col layout: SF+num at col 1, SF+name at col 4, SF+desc at col 21
    for i, (num, name, desc) in enumerate(_ISPF_OPTIONS):
        row = 4 + i
        _sba_sf(buf, row, 1, protected=True, display=DisplayIntensity.HIGH)
        _text(buf, f"{num:<2}")
        _normal(buf, row, 4, f"  {name}")   # name is 14 chars + leading "  " = 16 total
        _normal(buf, row, 21, f"  {desc}")

    # Row 18: X / exit option
    _sba_sf(buf, 18, 1, protected=True, display=DisplayIntensity.HIGH)
    _text(buf, "X ")
    _normal(buf, 18, 4, "  Exit          ")
    _normal(buf, 18, 21, "  Terminate ISPF using log/list defaults")

    # Row 20: PF key hints
    _normal(buf, 20, 1, "Enter X or PF3 to terminate ISPF.")

    # Row 21-22: status block
    _normal(buf, 21, 1, f"User ID . . :  {userid:<8}")
    _normal(buf, 21, 41, f"Time. . . . :  {time_str}")
    _normal(buf, 22, 1, "System ID . :  SY1     ")
    _normal(buf, 22, 41, "ISPF Ver. . :  7.1.0   ")

    # Row 23: bottom border
    _high(buf, 23, 0, "-" * 79)

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


MAX_BUFFER_SIZE = 65536  # 64 KiB; a legitimate 3270 data stream is at most a few KB


def read_client_input(client_socket):
    buffer = bytearray()
    while True:
        data = client_socket.recv(1024)
        if not data:
            return None
        buffer.extend(data)
        if len(buffer) > MAX_BUFFER_SIZE:
            print(f"WARNING: client buffer exceeded {MAX_BUFFER_SIZE} bytes; closing connection")
            return None
        if len(buffer) >= 2 and buffer[-2:] == bytes([IAC, EOR]):
            break

    # Strip IAC EOR
    buffer = buffer[:-2]

    if not buffer:
        return None
    aid = buffer[0]
    # Log only the AID (not the raw bytes) to avoid leaking password data in logs
    print(f"RX: {len(buffer)} bytes, AID: {aid_to_string(aid)}")

    SBA_ORD = 0x11
    SF_ORD = 0x1D
    results = {}
    i = 1
    while i < len(buffer):
        if buffer[i] == SBA_ORD and i + 2 < len(buffer):
            addr_hi, addr_lo = buffer[i + 1], buffer[i + 2]
            addr = ((addr_hi & 0x3F) << 6) | (addr_lo & 0x3F)
            i += 3
            field_bytes = bytearray()
            while i < len(buffer) and buffer[i] not in (SBA_ORD, SF_ORD):
                field_bytes.append(buffer[i])
                i += 1
            field_text = field_bytes.decode("cp037").strip()
            if field_text:
                results[addr] = field_text
        else:
            i += 1

    return aid, results


def tn3270_negotiate(client_socket):
    DONT = 254
    DO = 253
    WONT = 252
    WILL = 251
    SB = 250
    SE = 240

    BINARY = 0
    TERMINAL_TYPE = 24
    EOR_OPT = 25

    got_binary = False
    got_eor = False
    got_term = False

    negot = bytearray()
    negot.extend([IAC, WILL, BINARY])
    negot.extend([IAC, DO, BINARY])
    negot.extend([IAC, WILL, EOR_OPT])
    negot.extend([IAC, DO, EOR_OPT])
    negot.extend([IAC, WILL, TERMINAL_TYPE])
    negot.extend([IAC, DO, TERMINAL_TYPE])
    negot.extend([IAC, SB, TERMINAL_TYPE, 1, IAC, SE])

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

            if i + 1 >= len(buffer):
                break  # IAC at recv boundary; wait for next recv
            cmd = buffer[i + 1]

            if cmd in (DO, DONT, WILL, WONT):
                if i + 2 >= len(buffer):
                    break  # incomplete 3-byte command; wait for more data
                opt = buffer[i + 2]
                if cmd == DO:
                    client_socket.sendall(bytes([IAC, WILL, opt]))
                elif cmd == DONT:
                    client_socket.sendall(bytes([IAC, WONT, opt]))
                elif cmd == WILL:
                    client_socket.sendall(bytes([IAC, DO, opt]))
                elif cmd == WONT:
                    client_socket.sendall(bytes([IAC, DONT, opt]))

                if opt == BINARY and cmd in (DO, WILL):
                    got_binary = True
                if opt == EOR_OPT and cmd in (DO, WILL):
                    got_eor = True

                i += 3
                continue

            if cmd == SB:
                if i + 3 >= len(buffer):
                    break  # incomplete SB sequence; wait for more data
                opt = buffer[i + 2]
                if opt == TERMINAL_TYPE:
                    subopt = buffer[i + 3]
                    if subopt == 1:  # SEND
                        term = b"IBM-3278-2"
                        reply = bytes([IAC, SB, TERMINAL_TYPE, 0]) + term + bytes([IAC, SE])
                        print("TX:", binascii.hexlify(reply))
                        client_socket.sendall(reply)
                    elif subopt == 0:  # IS
                        term_type = buffer[i + 4 : buffer.index(IAC, i + 4)].decode(errors="ignore")
                        print("Client terminal type:", term_type)
                        got_term = True

                se_pos = buffer.find(bytes([IAC, SE]), i + 3)
                if se_pos != -1:
                    i = se_pos + 2
                else:
                    break
                continue
            else:
                print("Unknown IAC command:", cmd)

            i += 2

    print("Negotiation complete: binary={}, eor={}, term={}".format(got_binary, got_eor, got_term))


def handle_client(client_socket, addr):
    print(f"Connection from {addr}")
    tn3270_negotiate(client_socket)
    client_socket.settimeout(600)

    while True:
        # Logon loop
        error_msg = None
        userid = None
        while True:
            send_tso_logon(client_socket, error_msg)
            result = read_client_input(client_socket)
            if result is None:
                return
            aid, fields = result
            print(f"AID={hex(aid)}, fields={fields}")

            aid_str = aid_to_string(aid)
            if aid_str in ("PF3", "PF15"):
                # Logoff
                return

            userid_raw = fields.get(LOGON_USERID_ADDR, "").strip().upper()
            password_raw = fields.get(LOGON_PASSWORD_ADDR, "").strip().upper()

            if not userid_raw:
                error_msg = "IKJ56700I USERID MUST BE SPECIFIED"
                continue

            if _CREDENTIALS.get(userid_raw) != password_raw:
                error_msg = f"IKJ56425I PASSWORD NOT CORRECT FOR {userid_raw}"
                continue

            userid = userid_raw
            break

        # ISPF menu loop
        short_msg = None
        while True:
            send_ispf_menu(client_socket, userid, short_msg)
            result = read_client_input(client_socket)
            if result is None:
                return
            aid, fields = result
            print(f"AID={hex(aid)}, fields={fields}")

            aid_str = aid_to_string(aid)
            option = fields.get(ISPF_OPTION_ADDR, "").strip().upper()

            if option == "X" or aid_str in ("PF3", "PF15"):
                # Logoff — back to logon panel
                break

            valid_opts = {"0", "1", "2", "3", "4", "5", "6", "7", "9", "10", "11", "12", "13"}
            if option in valid_opts:
                short_msg = f"OPTION {option} NOT YET IMPLEMENTED"
            elif option:
                short_msg = f"INVALID OPTION: {option}"
            else:
                short_msg = None


def run_tn3270_server(host="0.0.0.0", port=2323):
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
            except Exception as e:
                print(f"Error handling client {addr}: {e}")
            finally:
                client_socket.close()


if __name__ == "__main__":
    run_tn3270_server()
