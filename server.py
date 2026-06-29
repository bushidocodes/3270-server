import socket
import binascii
import threading
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


# The low-level building blocks above (encode_pack_addr, field_attribute,
# write_control_character, the order constants) are consumed by screen.py, which
# provides the Screen/Field model that the panels render through. Screens are now
# authored declaratively in panels/*.dtl and loaded via dtl.load_panel() — see
# send_tso_logon / send_ispf_menu below.


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


def redact_fields(fields):
    """Return a copy of a parsed fields dict with the password field redacted.

    handle_client() logs the parsed fields for debugging, but the dict contains
    the decoded plaintext password keyed by LOGON_PASSWORD_ADDR. Emitting it to
    stdout would leak the password on every login, defeating the safe-logging
    guard in read_client_input(). Mask it before logging.
    """
    return {
        k: ("***" if k == LOGON_PASSWORD_ADDR else v)
        for k, v in fields.items()
    }


def _send_screen(client_socket, screen):
    """Render a screen.Screen to the 3270 data stream and send it."""
    data = screen.render()
    print("TX:", binascii.hexlify(data))
    client_socket.sendall(data)


def send_tso_logon(client_socket, error_msg: str = None):
    """Send the z/OS TSO/E LOGON panel, rendered from panels/logon.dtl."""
    # Imported lazily: screen.py imports primitives from this module, so a
    # top-level import here would create a circular import at load time.
    from dtl import load_panel
    from screen import Text

    screen = load_panel("logon")
    if error_msg:
        col = max(0, (80 - len(error_msg)) // 2)
        screen.add(Text(19, col, error_msg, DisplayIntensity.HIGH))
    _send_screen(client_socket, screen)
    return screen


def send_ispf_menu(client_socket, userid: str, short_msg: str = None):
    """Send the ISPF Primary Option Menu, rendered from panels/ispf.dtl."""
    from dtl import load_panel
    from screen import Text

    time_str = datetime.now().strftime("%H:%M")
    screen = load_panel("ispf", ZUSER=userid.ljust(8), ZTIME=time_str)
    if short_msg:
        screen.add(Text(2, 25, short_msg[:54], DisplayIntensity.HIGH))
    _send_screen(client_socket, screen)
    return screen


def _show_overlay(client_socket, panel_name: str):
    """Display an overlay panel (help or sub-panel) and wait for the user to
    leave it (PF3/PF15/Enter). The underlying panel is re-sent by the caller's
    loop on return — mirroring ISPF's PF1 HELP and option-select behaviour.
    """
    from dtl import load_panel

    while True:
        screen = load_panel(panel_name)
        _send_screen(client_socket, screen)
        result = read_client_input(client_socket)
        if result is None:
            return
        aid, _ = result
        aid_str = aid_to_string(aid)
        if aid_str == "Enter" or screen.command_for(aid_str) in _LEAVE_COMMANDS:
            return


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


# ISPF commands that leave the current panel. A panel's <keyl> binds function
# keys (PF3/PF15) to one of these; the session loop acts on the resolved command
# rather than hard-coding key numbers.
_LEAVE_COMMANDS = {"EXIT", "END", "RETURN", "LOGOFF"}

_message_catalog = None


def _messages():
    """Lazily load and cache the TSO message catalog (messages/tsomsgs.dtl)."""
    global _message_catalog
    if _message_catalog is None:
        from dtl import load_message_member  # lazy: avoid circular import
        _message_catalog = load_message_member("tsomsgs")
    return _message_catalog


def handle_client(client_socket, addr):
    print(f"Connection from {addr}")
    tn3270_negotiate(client_socket)
    client_socket.settimeout(600)

    while True:
        # Logon loop
        error_msg = None
        userid = None
        while True:
            screen = send_tso_logon(client_socket, error_msg)
            result = read_client_input(client_socket)
            if result is None:
                return
            aid, fields = result
            print(f"AID={hex(aid)}, fields={redact_fields(fields)}")

            aid_str = aid_to_string(aid)
            cmd = screen.command_for(aid_str)
            if cmd in _LEAVE_COMMANDS:
                # Keylist bound this key (PF3/PF15) to EXIT — log off.
                return
            if cmd == "HELP" and screen.help:
                _show_overlay(client_socket, screen.help)
                continue

            # Validate fields against their <varclass> checks (e.g. SIZE range)
            # before processing the logon, as ISPF validates panel fields.
            verr = screen.first_validation_error(fields)
            if verr:
                msgid, subs = verr
                error_msg = _messages().format(msgid, **subs)
                continue

            userid_raw = fields.get(LOGON_USERID_ADDR, "").strip().upper()
            password_raw = fields.get(LOGON_PASSWORD_ADDR, "").strip().upper()

            if not userid_raw:
                error_msg = _messages().format("IKJ56700I")
                continue

            if _CREDENTIALS.get(userid_raw) != password_raw:
                error_msg = _messages().format("IKJ56425I", USERID=userid_raw)
                continue

            userid = userid_raw
            break

        # ISPF menu loop
        short_msg = None
        while True:
            screen = send_ispf_menu(client_socket, userid, short_msg)
            result = read_client_input(client_socket)
            if result is None:
                return
            aid, fields = result
            print(f"AID={hex(aid)}, fields={redact_fields(fields)}")

            aid_str = aid_to_string(aid)
            # Read the option from the panel's <cmdarea> (its ZCMD command
            # field), resolved by role rather than a hard-coded address.
            option = (screen.command_value(fields) or "").strip().upper()

            cmd = screen.command_for(aid_str)
            if option == "X" or cmd in _LEAVE_COMMANDS:
                # X, or a keylist key (PF3/PF15) bound to EXIT — back to logon
                break
            if cmd == "HELP" and screen.help:
                _show_overlay(client_socket, screen.help)
                continue

            # A typed value is a menu selection, a command from the panel's
            # <cmdtbl>, or invalid. (The "X" exit choice is handled above.)
            if option == "0":
                # Option 0 (Settings) opens a real sub-panel (with an action bar).
                _show_overlay(client_socket, "settings")
                short_msg = None
            elif option in screen.selections:
                short_msg = f"OPTION {option} NOT YET IMPLEMENTED"
            elif option:
                action = screen.lookup_command(option)
                if action and action.lower().startswith("alias ") \
                        and action.split()[1].upper() in _LEAVE_COMMANDS:
                    break  # e.g. BYE -> "alias exit" leaves ISPF
                elif action:
                    short_msg = f"COMMAND {option} NOT YET IMPLEMENTED"
                else:
                    short_msg = f"INVALID OPTION: {option}"
            else:
                short_msg = None


def _client_thread(client_socket, addr):
    try:
        handle_client(client_socket, addr)
    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
        print(f"Client {addr} disconnected unexpectedly")
    except Exception as e:
        print(f"Error handling client {addr}: {e}")
    finally:
        client_socket.close()


def run_tn3270_server(host="127.0.0.1", port=2323):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind((host, port))
        server_socket.listen(socket.SOMAXCONN)
        print(f"TN3270 server listening on {host}:{port}")
        while True:
            client_socket, addr = server_socket.accept()
            threading.Thread(target=_client_thread, args=(client_socket, addr), daemon=True).start()


if __name__ == "__main__":
    run_tn3270_server()
