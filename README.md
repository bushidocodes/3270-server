# 3270 TN3270 Server

A Python implementation of a TN3270 server that presents an authentic IBM mainframe experience — complete TSO/E logon panel, RACF authentication, and the ISPF Primary Option Menu — over a standard TCP connection. Connect any real 3270 emulator and it just works.

## What you can do

1. **Connect** any TN3270 emulator (wc3270, x3270, Vista TN3270, etc.) to `localhost:2323`
2. **Log in** using a RACF userid and password — the server validates credentials and shows proper error messages for bad passwords or missing userids
3. **Navigate** the ISPF Primary Option Menu — the keyboard is fully live; type an option number and press Enter

### Logon panel

The server presents an authentic z/OS V2R5.0 TSO/E LOGON screen. Fill in your userid and password and press Enter.

![TSO/E Logon Panel](docs/screenshots/logon_panel.png)

**Built-in credentials**

| Userid | Password |
|--------|----------|
| `IBMUSER` | `SYS1` |
| `TESTUSER` | `RACF` |

The server follows z/OS RACF conventions: userids and passwords are case-insensitive (uppercased before comparison). A wrong password returns `IKJ56425I PASSWORD NOT CORRECT FOR <userid>`. A missing userid returns `IKJ56700I USERID MUST BE SPECIFIED`.

### ISPF Primary Option Menu

After a successful login you land on the ISPF Primary Option Menu. The keyboard is unlocked — you can type option numbers and press Enter. Entering `X` or pressing PF3 logs you off and returns to the TSO/E logon panel.

![ISPF Primary Option Menu](docs/screenshots/ispf_menu.png)

The full z/OS ISPF 7.1.0 menu is rendered, including the user ID, system ID (SY1), and current time in the status block. Options 0–13 and X are listed. (Sub-menus are not yet implemented — selecting any option returns you to the main menu with a short message.)

## Quick start

### Prerequisites

- Python 3.8+
- A TN3270 emulator — [wc3270](https://x3270.miraheze.org/wiki/Wc3270) (Windows) or [x3270](https://x3270.miraheze.org/wiki/X3270) (Linux/macOS) are free and work out of the box

### Run the server

```sh
python server.py
```

The server listens on port 2323 by default (no root/administrator required, unlike port 23).

### Connect with wc3270 (Windows)

```sh
wc3270 localhost:2323
```

### Connect with x3270 (Linux / macOS)

```sh
x3270 localhost:2323
```

### Connect with any emulator

Point your emulator at `localhost`, port `2323`. The server negotiates TN3270E binary mode, EOR, and terminal-type options automatically and supports IBM-3278 model 2–5 terminals.

## How it works

### TN3270 protocol

TN3270 is Telnet extended with IBM 3270 data-stream framing. The server performs the full Telnet option negotiation (BINARY, EOR, TERMINAL-TYPE) before sending any screen data.

### 3270 data stream

Screens are built with authentic 3270 orders:

| Order | Hex | Purpose |
|-------|-----|---------|
| ERASE_WRITE | `0xF5` | Clear screen and write new data |
| SBA | `0x11` | Set Buffer Address — position the write cursor |
| SF | `0x1D` | Start Field — define a protected or unprotected input field |
| IC | `0x13` | Insert Cursor — place the cursor in an input field |

The Write Control Character (WCC) sent after ERASE_WRITE uses `0x43` — the correct x3270/wc3270 bit layout (`WCC_RESET_BIT | WCC_KEYBOARD_RESTORE_BIT | WCC_RESET_MDT_BIT`) — so the keyboard unlocks immediately after every screen update.

### Field parsing

When the user presses Enter or a PF key, the emulator sends an AID byte followed by the cursor address and the contents of all modified fields. The server decodes the 12-bit packed buffer addresses and reads each field's EBCDIC text, then strips whitespace and uppercases credential fields before comparing.

## Project structure

```
server.py   — the entire TN3270 server (single file)
```

Key functions:

| Function | What it does |
|----------|-------------|
| `tn3270_negotiate` | Performs Telnet option handshake |
| `send_tso_logon` | Builds and sends the TSO/E logon panel |
| `send_ispf_menu` | Builds and sends the ISPF Primary Option Menu |
| `read_client_input` | Reads and parses an AID response from the client |
| `write_control_character` | Encodes a WCC byte with correct x3270 bit positions |
| `encode_pack_addr` | Converts (row, col) to a 12-bit 3270 buffer address |
| `handle_client` | Main session loop: logon → ISPF → logoff |

## Extending

To add real sub-menus, replace the `short_msg` response in `handle_client`'s ISPF loop with a call to a new `send_ispf_option_N` function that builds and sends the appropriate screen, then reads the user's response.

To add more users, extend the `_CREDENTIALS` dict at the top of `server.py`.

## References

- [RFC 2355 — TN3270E](https://tools.ietf.org/html/rfc2355)
- [IBM 3270 Data Stream Programming Reference](https://www.ibm.com/docs/en/zos/2.5.0?topic=reference-3270-data-stream)
- [x3270 / wc3270 emulator](https://x3270.miraheze.org/wiki/Main_Page)
- [pmattes/x3270 source (3270ds.h)](https://github.com/pmattes/x3270) — canonical WCC and field-attribute bit definitions

---

For educational and prototyping purposes.
