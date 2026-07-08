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

After a successful login you land on the ISPF Primary Option Menu. The keyboard is unlocked — you can type option numbers and press Enter. Entering `X` or pressing PF3 logs you off and returns to the TSO/E logon panel. Pressing **PF1** on either panel shows a help screen (PF3 returns).

![ISPF Primary Option Menu](docs/screenshots/ispf_menu.png)

The full z/OS ISPF 7.1.0 menu is rendered, including the user ID, system ID (SY1), and current time in the status block. Options 0–13 and X are listed, and **every option opens a real panel** — each driven from its own `panels/*.dtl`. PF3 always steps back one level; typing a dotted path like `3.1` or `9.2` jumps straight to a sub-option:

| Option | Panel | What it does |
|--------|-------|--------------|
| `0` Settings | `settings.dtl` | Sub-panel with an **action bar** across the top — F10/F11 walk the choices, Enter opens a choice's pull-down (point-and-shoot). |
| `1` View | `viewentry.dtl` → `browse.dtl` | Prompts for a panel-library member, then **browses its actual DTL source**, paging with PF7/PF8. Unknown member → `MEMBER xxx NOT FOUND`. |
| `2` Edit | `editentry.dtl` → `browse.dtl` | Like View, but opens the member in an `EDIT` frame (display-only in this demo). |
| `3` Utilities | `utility.dtl` | A **nested sub-menu** with its own Option line. `3.1` (Library) lists the real `ISPF.ISPPLIB` members in a `<lstfld>` table. |
| `4` Foreground / `5` Batch | `foreground.dtl` / `batch.dtl` | Language-processing selection sub-menus (Assembler, COBOL, PL/I, …). |
| `6` Command | `command.dtl` | A **TSO Command Shell**: `TIME` returns the live TSO time message; any other verb gets the authentic `IKJ56500I COMMAND xxx NOT FOUND`. |
| `7` Dialog Test | `dlgtest.dtl` | Lists the session's live ISPF dialog variables (`ZUSER`, `ZTIME`, `ZDATE`, …) in a `<lstfld>` table. |
| `9` IBM Products | `ibmprod.dtl` | Selection sub-menu (ISMF, SDSF, RACF, HCD, DFSORT). |
| `10` SCLM | `sclm.dtl` | Software Configuration and Library Manager sub-menu. |
| `11` Workplace | `workplace.dtl` | Informational panel describing the ISPF Object/Action Workplace. |
| `12` z/OS System / `13` z/OS User | `zsystem.dtl` / `zuser.dtl` | System-programmer and user application sub-menus. |

Within a sub-menu, selecting a leaf that isn't implemented reports it via the panel's message line. Pressing **PF1** on most panels shows a help screen.

#### Live sub-panels

Option `7` (Dialog Test) — a `<lstfld>` table populated from the running session:

```
--------------------------- Dialog Test - Variables ----------------------------
  ISPF dialog variables and their current values for this session.

                      Session Variables
   Variable   Value
   ZUSER      IBMUSER
   ZPREFIX    IBMUSER
   ZAPPLID    ISR
   ZTIME      20:31
   ZDATE      26/06/30
   ZSCREEN    1
   ZENVIR     ISPF 7.1
   ZKEYS      DLGTKEYS
```

Option `6` (Command) — after typing `TIME`:

```
------------------------------ ISPF Command Shell ------------------------------
  Enter a TSO command and press Enter; its response appears below.

  Command ===> TIME

  IKJ56650I TIME-08:31:45 PM DATE-2026.181 DAY-TUESDAY
```

Option `1` (View) browsing the ISPF menu's own source (`1` → member `ISPF`):

```
BROWSE    ISPF.ISPPLIB(ISPF)                          Line 00000001
<!DOCTYPE DM SYSTEM>
<!-- ISPF 7.1.0 Primary Option Menu, in Dialog Tag Language (DTL subset).
     Renders byte-for-byte identically to screens.build_ispf_menu().
     &ZUSER (padded to 8) and &ZTIME are ISPF dialog variables substituted at
     load time; the transient short message (row 2) is injected at runtime. -->
<panel name="ispfmenu" title="ISPF Primary Option Menu" help="ispfhelp">
  ...
Lines 1-22 of 54     PF7=Up  PF8=Down  PF3=Exit
```

## Quick start

### Prerequisites

- Python 3.8+
- A TN3270 emulator — [wc3270](https://x3270.miraheze.org/wiki/Wc3270) (Windows) or [x3270](https://x3270.miraheze.org/wiki/X3270) (Linux/macOS) are free and work out of the box

### Run the server

```sh
python server.py
```

The server listens on port 2323 by default (no root/administrator required, unlike port 23). `python server.py --help` lists the options: `--host`, `--port`, `--certfile`/`--keyfile`, and `--starttls` (each also settable via the `TN3270_HOST`, `TN3270_PORT`, `TN3270_CERTFILE`, `TN3270_KEYFILE`, `TN3270_STARTTLS` environment variables).

### Run over TLS (secure 3270)

Pass a PEM certificate to serve **implicit TLS** — the whole connection is encrypted from the first byte, the way an x3270-family emulator connects when the host is given the `L:` prefix. Credentials typed on the logon panel then never cross the wire in the clear.

```sh
# a throwaway self-signed cert for local testing
openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 365 -nodes -subj "/CN=localhost"
python server.py --certfile cert.pem --keyfile key.pem
```

Then connect with the `L:` prefix, e.g. `wc3270 L:localhost:2323` (add `-noverifycert` for a self-signed cert). Without `--certfile` the server is plaintext, exactly as before.

Alternatively, add `--starttls` to use **negotiated START-TLS** (the `START_TLS` Telnet option): the session begins in the clear on the normal port and is upgraded to TLS in-band, so no separate TLS port or `L:` prefix is needed — an emulator (which enables START-TLS by default) connects plaintext and the server offers the upgrade. A client that declines still gets a plaintext session.

```sh
python server.py --certfile cert.pem --keyfile key.pem --starttls
# connect in the clear; the upgrade is automatic:  wc3270 -noverifycert localhost:2323
```

### Connect with wc3270 (Windows)

```sh
wc3270 localhost:2323
```

### Connect with x3270 (Linux / macOS)

```sh
x3270 localhost:2323
```

### Connect with any emulator

Point your emulator at `localhost`, port `2323`. The server negotiates **TN3270E (RFC 2355)** when the emulator supports it — the TN3270E option, a `DEVICE-TYPE`/`FUNCTIONS` sub-negotiation, and a 5-byte data header on every record — and otherwise falls back to **basic TN3270 (RFC 1576)** (BINARY, EOR, TERMINAL-TYPE). Either way it detects the terminal model your emulator reports (3278/3279, models 2–5) and records it (exposed as the `ZTERM` dialog variable under option 7). If your emulator advertises the extended data stream (an `-E` terminal type), the server then sends a **Read Partition (Query)** and folds the terminal's own **Query Reply** — its real usable area, colour, and highlighting support — back into what it knows about the session (authoritative where the type string is only a hint, e.g. `IBM-DYNAMIC`). Most panels are drawn on the 24×80 default presentation space that every model shares, so they render identically everywhere. On a **model 3/4/5** terminal, the scrollable **Browse** panel (View/Edit, option 1/2) instead renders on the terminal's larger *alternate* screen (32×80, 43×80, or 27×132) via `ERASE/WRITE ALTERNATE`, so more of a member's source is visible per page (30/41/25 lines instead of 22). If the emulator negotiates the **`RESPONSES`** function, the server requests an acknowledgement for each screen it sends and consumes the client's positive/negative response messages. If it negotiates **`BIND-IMAGE`**, the server sends an SNA **BIND** image before the first screen (binding the LU-LU session); this is what lets the **ATTN** key (Telnet Interrupt Process, which x3270 only sends on a *bound* session) reach the server, where it redisplays the current panel. If it negotiates the **`SYSREQ`** function, pressing **SYSREQ** suspends the ISPF (LU-LU) session and drops into an **SSCP-LU** host session — the server sends an unformatted prompt, `LOGOFF` there ends the whole session (the server sends `UNBIND`), and a second SYSREQ resumes and redisplays the panel. If it negotiates **`CONTENTION-RESOLUTION`**, the server sets the `SEND-DATA` request flag on every screen, granting the keyboard send permission so the client never has to bid before returning input. (The remaining optional functions — DATA-STREAM-CTL/SCS — are printer-oriented and unimplemented, and only Browse uses the alternate screen so far.)

## How it works

### TN3270 protocol

TN3270 is Telnet extended with IBM 3270 data-stream framing. The server performs the full Telnet option negotiation (BINARY, EOR, and either TN3270E or TERMINAL-TYPE) before sending any screen data.

**TN3270E (RFC 2355)** is offered first (Telnet option 40). If the client accepts, the server drives the `DEVICE-TYPE` exchange (it sends `SEND DEVICE-TYPE`, the client `REQUEST`s a device type, the server replies `IS` with an assigned device name) and negotiates `FUNCTIONS` (it supports **`BIND-IMAGE`**, **`RESPONSES`**, **`SYSREQ`**, and **`CONTENTION-RESOLUTION`**, agreeing the intersection of those with the client's request) — after which **every 3270 record carries a 5-byte data header** (`DATA-TYPE`, `REQUEST-FLAG`, `RESPONSE-FLAG`, 2-byte `SEQ-NUMBER`). That framing is handled by wrapping the session socket in `TN3270EStream`: it prepends the header on send (and, when `RESPONSES` is active, sets `RESPONSE-FLAG = ALWAYS` under an incrementing sequence and consumes the client's inbound `RESPONSE` messages), while `read_record` strips the header on receive — so the screen code is unchanged. When **`BIND-IMAGE`** is negotiated the server first sends a `DATA-TYPE BIND-IMAGE` message carrying a synthetic SNA BIND (RFC 2355 requires a BIND before any 3270-DATA, and a *bound* session is what makes the client's ATTN key send its `IP`). When **`CONTENTION-RESOLUTION`** is negotiated the server sets the `SEND-DATA` bit in each 3270-DATA record's `REQUEST-FLAG`, granting the half-duplex send turn to the client with every screen so it never has to send a `BID` and wait — matching the server's send-a-screen-then-read model. The **`SYSREQ`** and **ATTN** keys arrive out-of-band as Telnet `AO` and `IP` commands between records; `next_event` surfaces them to the session loop, which runs a small **SSCP-LU** host session (`DATA-TYPE` `SSCP-LU-DATA`/`UNBIND`) for SYSREQ. If the client refuses TN3270E, the server falls back to the basic TERMINAL-TYPE exchange.

**Line mode (NVT).** A client that refuses the 3270 binary framing — a plain line-mode telnet client that never negotiates `BINARY`/`EOR`, detected either by that refusal or by plain ASCII arriving mid-negotiation — can't carry a 3270 data stream. Rather than hang the negotiation, the server serves it a minimal ASCII **TSO `READY`** command loop (`run_nvt_session`): `TIME`, `HELP`, `ISPF` (which explains a full-screen 3270 terminal is required), and `LOGOFF` to disconnect; any other verb gets the authentic `IKJ56500I COMMAND xxx NOT FOUND`. So a non-3270 client gets something intelligible instead of nothing. A genuine 3270 emulator always negotiates the binary framing and so is never routed here.

When a certificate is configured (`--certfile`), the accepted socket is wrapped in **implicit TLS** (`ssl.SSLContext(PROTOCOL_TLS_SERVER)`) in the per-client thread before any 3270 bytes flow — so a slow or hostile client can't stall the accept loop — after which the entire negotiation and session run over the encrypted socket unchanged (the 3270 code only ever calls `recv`/`sendall`). This matches the `L:` connect prefix used by x3270-family emulators. With `--starttls` the same context instead drives a **negotiated START-TLS** upgrade (`_offer_starttls`): the server sends `IAC DO START-TLS`, a willing client answers `WILL` + `SB START-TLS FOLLOWS SE`, the server replies in kind and runs the handshake in place — the client's plaintext replies are read one byte at a time so no TLS bytes are ever swallowed, and a `WONT` leaves a working plaintext session. Plaintext remains the default.

### 3270 data stream

Screens are built with authentic 3270 orders:

| Order | Hex | Purpose |
|-------|-----|---------|
| ERASE_WRITE | `0xF5` | Clear screen and write new data (24×80 default space) |
| ERASE_WRITE_ALTERNATE | `0x7E` | Like ERASE_WRITE, but select the model's larger *alternate* space (models 3/4/5) |
| WRITE | `0xF1` | Plain write — patch part of the screen without erasing (see partial updates below) |
| SBA | `0x11` | Set Buffer Address — position the write cursor |
| SF | `0x1D` | Start Field — define a protected or unprotected input field |
| SFE | `0x29` | Start Field Extended — a field carrying colour / highlighting (colour terminals only) |
| SA | `0x28` | Set Attribute — change colour/highlight for the following characters *within* a field (colour terminals only) |
| RA | `0x3C` | Repeat to Address — fill a run of one character (rule lines / fills) in 4 bytes instead of one per character |
| EUA | `0x12` | Erase Unprotected to Address — null the input fields, leaving protected text (see clearing input below) |
| IC | `0x13` | Insert Cursor — place the cursor in an input field |

The Write Control Character (WCC) sent after ERASE_WRITE uses `0x43` — the correct x3270/wc3270 bit layout (`WCC_RESET_BIT | WCC_KEYBOARD_RESTORE_BIT | WCC_RESET_MDT_BIT`) — so the keyboard unlocks immediately after every screen update.

**Compact fills (RA).** A rule line or fill is a run of one repeated character. Instead of one byte per character, `Text.render` emits a single **Repeat to Address** (`RA`, `0x3C`) order for any run of 5+ identical characters — 4 bytes regardless of length. The rendered result is identical (verified against ws3270, which processes `RepeatToAddress`); only the wire stream is shorter. Because both the DTL parser and the `screens.py` builders render through the same code, the panels stay byte-for-byte equal to each other.

**Clearing input (EUA).** `Screen.render_erase_input()` sends a plain Write with an **Erase Unprotected to Address** (`EUA`, `0x12`) order that nulls every *unprotected* (input) field while leaving the protected text on screen — the native "clear the entry fields" operation. It pairs with `render_partial` to reset a form's input in place without repainting the panel (verified on ws3270: a typed field clears while its label stays).

**Character-level colour.** A field's colour normally comes from its field start (SF/SFE) and applies to the whole field. `Text.rich(...)` builds a single field whose text is coloured in segments — an emphasised keyword inside a line of normal text — by emitting **Set Attribute** (`SA`, `0x28`) orders between the runs, so it needn't be split into separate fields. Like SFE, SA is emitted only on a colour render; a mono terminal just gets the concatenated text, byte-for-byte unchanged. (Verified against ws3270, which renders the runs in their individual colours.)

**Partial updates.** A full screen is an ERASE/WRITE that repaints everything and resets the modified-data tags. When only a message line changes, the server instead sends a plain **WRITE** (`0xF1`, via `Screen.render_partial`) that patches just the addressed positions with a WCC of `0x42` (keyboard-restore but **not** reset-MDT). Nothing else is repainted and the modified tags are left alone, so what the user has typed stays on screen and modified — the way real ISPF redisplays an "INVALID OPTION" message without clearing the command line. The ISPF Primary Option Menu uses this for its message line.

### Colour and highlighting (extended attributes)

DTL's **`COLOR`** attribute (`WHITE | RED | BLUE | GREEN | PINK | YELLOW | TURQ`, or `%varname` to take the colour from a dialog variable) and **`HILITE`** attribute (`USCORE | BLINK | REVERSE`) are carried by the CUA element tags that accept them — `<dtafld>` (the entry field), `<selfld>` (its choices), `<lstcol>` (its cells), and others (`<hp>`, `<note>`/`<notel>`/`<nt>`). Plain text tags such as `<info>`/`<topinst>` are *not* COLOR-bearing — they take their colour from their CUA element type.

On the wire these become **extended attributes**, carried by a Start Field Extended (`0x29`) order — a pair count followed by the basic field attribute plus one `type`/`value` pair per extended attribute (foreground colour is type `0x42`, highlighting `0x41`). Rendering is **gated on the terminal**: a screen emits extended attributes only when the connected terminal is colour-capable (a 3279-family device, or any extended-data-stream `-E` terminal — which modern emulators report and colour-display). A mono terminal — or an item with no colour — emits the classic Start Field (`0x1D`), so those data streams are **byte-for-byte unchanged**.

Beyond explicit `COLOR`, every panel element is coloured by its **CUA role**, matching a real z/OS ISPF panel: the panel title is white, separator rules blue, field prompts and normal text green, unprotected entry fields turquoise, and a menu choice's number, keyword, and description are white, turquoise, and green. Each DTL element carries a role (`screen._CUA_COLORS` maps role → colour); an element's own `COLOR` overrides its role's default. So on a colour terminal the whole session — the Primary Option Menu, the sub-panels, the logon panel — comes up in ISPF colours, while a mono terminal renders identically to before.

DTL's other colour mechanism, the **data area** (`<da>` with nested `<attr>`), is also supported. An `<attr>` defines an attribute character — `<attr attrchar="$" type="char|dataout|datain" color="RED" hilite="..." padc="_">` — and wherever that character appears in the `<da>`'s body text it starts a field of that type (a `datain` char an unprotected input field sized by the run of pad cells that follow it, a `char`/`dataout` char a protected display field), coloured accordingly. This is the DTL form of the classic ISPF `)ATTR` + `)BODY` model, and is how the guide's `ex091`/`ex102` colour their dynamic areas.

### Query / Query Reply (structured fields)

The TERMINAL-TYPE string is only a hint. In **basic TN3270** the authoritative way to learn a terminal's real geometry and capabilities is the 3270 **Query**: the host sends a Write Structured Field carrying a Read Partition request, and the terminal answers with an inbound **AID `0x88`** followed by Query Reply structured fields describing its usable area, colour, highlighting, and more. The server sends a **Query List (All)** (`F3 00 06 01 FF 03 80`), asking the terminal to enumerate its whole capability set; the reply's **Summary** (QCODE `0x80`) lists every QCODE it supports — so capabilities a terminal advertises only there (e.g. highlighting) are discovered even when no standalone reply is returned. `parse_query_reply` records the full QCODE set (`TerminalModel.query_caps`), the usable area, and — authoritatively — colour support, so a terminal that answers but does *not* report colour overrides the type-string guess. The query is sent to extended (`-E`) basic-TN3270 terminals only, waiting briefly for the reply so a terminal that ignores it can't stall the session.

The WSF stream is **IAC-escaped** before sending: its partition byte is `0xFF`, which the Telnet layer would otherwise read as an IAC command — an unescaped query is silently rejected by the terminal (`WriteStructuredField error`) and never answered, which is why the query never actually worked against a real terminal before. The reply is likewise un-escaped on receive (`read_record` collapses `IAC IAC` → `0xFF`), since Query Replies are full of `0xFF` colour values. Under **TN3270E** the Query is skipped — the `DEVICE-TYPE` sub-negotiation has already identified the terminal, and real TN3270E emulators (ws3270) don't answer a Read Partition Query, so sending one would only delay the first screen.

### Field parsing

When the user presses Enter or a PF key, the emulator sends an AID byte followed by the cursor address and the contents of all modified fields. The server decodes the 12-bit packed buffer addresses and reads each field's EBCDIC text, then strips whitespace and uppercases credential fields before comparing.

### Reply modes (reading extended attributes back)

By default a terminal returns modified fields in **Field** reply mode — text only. A terminal that advertises **Reply Modes** in its Query Reply (`QR_REPLY_MODES`, `0x88`) can be switched to a richer inbound mode with a **Set Reply Mode** Write Structured Field (`F3 00 05 09 00 <mode>`): **Extended Field** mode (`0x01`) prefixes each modified field with its extended attributes, and **Character** mode (`0x02`) additionally reports per-character attribute changes. The attributes ride inbound as **Set Attribute** (`SA`, `0x28`) orders — the same order used outbound by `Text.rich` — so `request_reply_mode` switches capable terminals to Character mode after the Query and `_parse_3270_reply` consumes each `SA type/value` triple as an attribute (recorded per field) instead of mistaking it for field text. Only basic-TN3270 `-E` terminals answer the Query, so under **TN3270E** the capability is never learned and the session stays in Field mode — where no `SA` orders appear and field text is read exactly as before. (Verified against ws3270: the Set Reply Mode WSF is accepted and a submitted field parses cleanly under Character mode.)

### Erase/Reset (partition control)

Beyond the Query, the server can build one **partition-control** Write Structured Field: **Erase/Reset** (`F3 00 04 03 <flag>`, id `0x03`). It tears down any explicit partition state and re-establishes a single **implicit** partition covering the whole screen, then erases it — the clean slate a host issues before switching between the default and the alternate screen size. The one flag byte selects that implicit partition's size: `0x00` (default) or `0x80` (alternate). `erase_reset(alternate=…)` returns the logical bytes; the caller IAC-escapes and IAC-EOR-terminates them exactly like the Query. This is the concrete, self-contained partition SF called for by #102 (a "collapse a split view back to one full-screen partition" primitive). It is **opt-in**: nothing in the bundled session sends it, so no panel changes behaviour. Verified against ws3270 — the emulator parses and accepts it (`WriteStructuredField EraseReset Alternate` in its trace, no `WriteStructuredField error`) and renders the screen that follows.

### Explicit partitions (split screen)

The larger explicit-partition surface Erase/Reset deliberately left (#307) is now built too — the SFs a real split screen needs: **Create Partition** (`create_partition`, id `0x0C` — define a partition's presentation-space size, viewport origin/size, window origin and scrolling), **Activate Partition** (`activate_partition`, id `0x0E`), **Destroy Partition** (`destroy_partition`, id `0x0D`), and **Outbound 3270DS** (`outbound_3270ds`, id `0x40` — wrap a normal 3270 write so it paints a specific partition). Each returns logical bytes the caller IAC-escapes and IAC-EOR-terminates, exactly like Erase/Reset, and each is **opt-in** — the bundled session never partitions. `partitions_supported(model)` gates on the terminal advertising **Alphanumeric Partitions** (`QR_ALPHA_PART`, `0x84`) in its Query Reply; a standard AID reply carries no inline partition id, so the originating partition is the host's active partition context (`inbound_partition`, GA23-0059 INPID). The two SFs x3270/ws3270 implements — Create Partition (partition 0) and Outbound 3270DS — are verified end-to-end against ws3270 (parsed with no `WriteStructuredField error`, the wrapped screen renders); Activate/Destroy are byte-structure-verified (the emulator doesn't implement them, and their format is a trivial id + partition-id body).

### Load Programmed Symbols and vector graphics

Two further #102 follow-ups round out the outbound structured-field surface, both **opt-in** and byte-structure-verified (neither is observable on the text emulators this repo tests against, so — as the issues scope it — verification is the builder's byte layout, not a real-terminal render):

- **Load Programmed Symbols** (`load_programmed_symbols`, id `0x06`, #308) downloads a host-defined character set (a programmed symbol set, identified by an LCID) into the terminal's Read/Write Storage; `select_char_set(lcid)` then references it from a field's data via a **Set Attribute** char-set order (`28 43 <lcid>`), the same alternate-set machinery the Graphic Escape uses. `programmed_symbols_supported(model)` gates on the terminal advertising **Character Sets** (`QR_CHARSETS`, `0x85`).
- **GDDM-style vector graphics** (`goca.py`, #309) builds a minimal subset of the **GOCA** drawing orders — Set Color/Line Type, Set Current Position, Line, Box, Begin/End Area (filled), Character String, Full Arc — enough to draw line segments and a filled rectangle. These are the graphics-segment payload; the 3270 carriage that delivers a segment to a graphics presentation space is device-specific and out of scope (see the module docstring).

## Project structure

```
server.py       — TN3270 protocol: negotiation, session loop, the 3270 primitives
screen.py       — Screen/Field model: renders to a 3270 data stream, parses responses
screens.py      — the two panels built as Screen objects (the in-code reference)
dtl.py          — Dialog Tag Language parser: load_panel() → Screen, load_message_member() → MessageCatalog
panels/         — the screens authored declaratively (ISPF ISPPLIB)
  logon.dtl       z/OS TSO/E LOGON panel
  ispf.dtl        ISPF Primary Option Menu
  tsohelp.dtl     PF1 help for the logon panel
  ispfhelp.dtl    PF1 help for the ISPF menu
  settings.dtl    ISPF Settings sub-panel (option 0; has an action bar)
  viewentry.dtl   View entry panel (option 1; prompts for a member)
  browse.dtl      Browse frame (option 1; shows a member's source)
  utility.dtl     Utility Selection sub-menu (option 3)
  memlist.dtl     Library member list (option 3.1; a <lstfld> table)
  command.dtl     TSO Command Shell (option 6)
  dlgtest.dtl     Dialog Test variable display (option 7; a <lstfld> table)
  editentry.dtl   Edit entry panel (option 2)
  foreground.dtl  Foreground language-processing sub-menu (option 4)
  batch.dtl       Batch language-processing sub-menu (option 5)
  ibmprod.dtl     IBM Products sub-menu (option 9)
  sclm.dtl        SCLM sub-menu (option 10)
  workplace.dtl   Object/Action Workplace info panel (option 11)
  zsystem.dtl     z/OS System programmer sub-menu (option 12)
  zuser.dtl       z/OS User applications sub-menu (option 13)
messages/       — message members, kept apart from panels as on z/OS (ISPMLIB vs ISPPLIB)
  tsomsgs.dtl     TSO/E logon messages (IKJ56425I, IKJ56700I)
```

Screens are **data, not code**. `server.py` no longer hand-assembles bytes; `send_tso_logon`
and `send_ispf_menu` call `dtl.load_panel("logon" | "ispf")`, which parses the `.dtl` source
into a `Screen` that renders itself to the 3270 data stream.

Key functions:

| Function | What it does |
|----------|-------------|
| `tn3270_negotiate` | Performs the Telnet option handshake and returns the negotiated `TerminalModel` |
| `parse_terminal_type` | Classifies a TERMINAL-TYPE string (e.g. `IBM-3279-4-E`) into a `TerminalModel` (model 2–5, size, colour) |
| `query_terminal` | Sends a Read Partition Query List (IAC-escaped) to an extended terminal and folds its Query Reply (real size, colour, advertised QCODEs) into the `TerminalModel` |
| `parse_query_reply` | Parses an inbound Query Reply record (AID `0x88`) into a capabilities dict |
| `request_reply_mode` | Sends a Set Reply Mode WSF to a reply-modes-capable terminal so modified fields carry their extended attributes (`SA` orders) inbound |
| `erase_reset` | Builds an Erase/Reset WSF (`F3 00 04 03 <flag>`) that resets to one implicit partition of the default or alternate size — an opt-in partition-control primitive (#102), unused by the bundled session |
| `create_partition` / `activate_partition` / `destroy_partition` / `outbound_3270ds` | Build the explicit-partition WSFs for a split screen (ids `0x0C`/`0x0E`/`0x0D`/`0x40`) — opt-in (#307), gated on `partitions_supported` (`QR_ALPHA_PART`) |
| `load_programmed_symbols` / `select_char_set` | Build a Load PS WSF (id `0x06`) that downloads a programmed symbol set, and the `SA` char-set order that references it — opt-in (#308), gated on `programmed_symbols_supported` (`QR_CHARSETS`) |
| `goca.*` | Build minimal GOCA vector drawing orders (line, box, filled area, arc, text) — byte-structure builders for GDDM-style graphics (#309) |
| `dtl.load_panel` | Parses a `panels/*.dtl` source into a `Screen` |
| `screen.Screen.render` | Renders a `Screen` to a 3270 data stream |
| `screen.Screen.parse` | Maps a client response onto named fields |
| `read_record` | Reads one IAC-EOR-terminated 3270 record (shared by AID replies and Query replies) |
| `read_client_input` | Reads and parses an AID response from the client |
| `encode_pack_addr` | Converts (row, col) to a 12-bit 3270 buffer address |
| `handle_client` | Main session loop: logon → ISPF → logoff |

### Declarative screens (DTL)

Panels are written in a pragmatic subset of IBM's **Dialog Tag Language** — the ISO-SGML
markup ISPF panels are defined in (compiled on z/OS via the `ISPDTLC` utility). A field looks
like:

```sgml
<dtafld row="5" col="1" fldcol="16" datavar="userid" entwidth="8" cursor="yes">Userid   ===></dtafld>
```

Supported tags: `<panel>`, `<info>` (text/instructions, with `fill`+`width` rules),
`<dtafld>` (prompt + input field), `<cmdarea>` (the ISPF "Option/Command ===>" line, bound to
`ZCMD`), `<selfld>`/`<choice>` (menu lists; each `<choice>`'s `match` — IBM's attribute, else its
`num` — registers a selectable value the server validates against,
`<choice checkvar>` lands the cursor on the current choice, `<choice hide/hidex>` conditionally
removes a choice, `<selfld type=multi>` is a multiple-selection field —
each choice gets a 1-char mark field, read back via `Screen.selected_values` — and the selection
field's prompt caption is placed above the list or, with `pmtloc=before`, to its left),
`<keyl>`/`<keyi>` (a keylist binding function keys to
commands), `<cmdtbl>`/`<cmd>`/`<cmdact>` (an application command table — the command line
recognizes named commands, with truncation), `<varclass>`/`<varlist>`/`<vardcl>` (typed variable
declarations — a field inherits `numeric` from its class, and a class's `<checkl>`/`<checki>`
range/value checks validate the field's input, e.g. the logon SIZE field), `<ab>`/`<abc>`/`<pdc>`
(an action bar with pull-down choices — put the cursor on a choice and press Enter to open its
pull-down, point-and-shoot style; or use **F10/F11** to step the cursor left/right across the
choices), `<topinst>`/`<paninst>`
(instruction text), `<lstfld>`/`<lstcol>`/`<lstgrp>` (a scrollable list/table — the column
headings are laid out left-to-right by `colwidth`, with a `<lstgrp headline=yes>` group heading
centered over its columns; below them, model rows render each column as a protected display
(`usage=out`) or an editable input field, stacked by `line=N`, populated from data passed as
`load_panel(..., rows=[{datavar: value}, …])`), and
`<area>`/`<region>` (flow boxes — see below). ISPF
dialog variables are referenced `&`-style — `&ZUSER`, `&ZTIME` — and substituted at load time
(e.g. the live user id and clock on the ISPF status line); `&&` is a literal ampersand and a
trailing `.` terminates a reference. A field element (`<dtafld>`, `<selfld>`, `<lstcol>`, …) may
carry DTL's `COLOR` (`WHITE|RED|BLUE|GREEN|PINK|YELLOW|TURQ`, or `%varname`) and/or `HILITE`
(`USCORE|BLINK|REVERSE`); these emit as extended attributes on colour terminals and are ignored on
mono ones. Messages live separately in a `<msgmbr>` (see `messages/`).
As in real DTL the source is SGML: files may open with a `<!DOCTYPE DM SYSTEM>` prolog,
tag/attribute names are case-insensitive, and boolean attributes may be minimized
(`<dtafld numeric>`).

Placement is normally explicit (`row`/`col`), but an `<area>`/`<region>` flow box lets contained
elements omit positions: they flow down one line each from the box's origin, and a field that
omits `fldcol` gets its entry after the prompt (`col + len(prompt) + fldgap`). A **`<dtacol>`** is a
flow box that also carries default column widths — `pmtwidth` fixes the prompt column so every
field's entry lines up at `col + pmtwidth`, and `entwidth` supplies a default field width — and a
**`<divider>`** draws a horizontal rule across the box. A box with **`dir=horiz`** lays its children
*side by side* (left-to-right) instead of stacking them, so a row of related fields (City / State /
Zip) or two `<region>` columns sit together; the enclosing flow then resumes below the tallest
column, and a `<divider gutter=n>` between horizontal columns is a vertical gutter (spacing, no
rule). Explicit positions still win, so this is opt-in — the bundled panels remain byte-for-byte
identical. (Authentic DTL auto-flows the whole document with ISPDTLC's exact column math; ours is a
close approximation — an auto-flowed field that would run past the panel edge is clamped rather than
rejected.)

Variable declarations (`<varclass>`/`<varlist>`/`<vardcl>`) are optional, as in `ISPDTLC`: a stray
or missing `<vardcl>` is tolerated (the converter issues a suppressible warning) rather than
aborting the panel, so a source file that references variables without declaring them still renders.

A `<keyl>` is pure metadata — it renders nothing — but the server reads it to resolve function
keys to commands the way ISPF does, e.g. PF3 → `EXIT`, instead of hard-coding key numbers:

```sgml
<keyl name="ISPFKEYS">
  <keyi key="PF3" cmd="EXIT">Exit</keyi>
</keyl>
```

### Tag reference and coverage

[`docs/dtl-tags-reference.md`](docs/dtl-tags-reference.md) mirrors IBM's DTL *Table 1. Tag summary*
(all 96 tags, with end-tag rules, attributes and their values, and nesting) and annotates each with
this parser's support status, judged on **attribute coverage** — **5 fully supported, 42 partial
(render but ignore some attributes), 49 unimplemented** — linking every gap to its tracking issue.

### Conformance corpus

`tests/dtl_examples/` holds the `<panel>` examples extracted verbatim from IBM's
[z/OS 2.4 ISPF DTL Guide](https://www.ibm.com/docs/en/SSLTBW_2.4.0/pdf/f54dt00_v2r4.pdf), and
`test_dtl_examples.py` renders each through our parser. It's a yardstick for how close the subset
is to the real reference. A panel is an implicit **flow box** (elements that omit `row`/`col` flow
down from the top), the parser honours DTL's **omitted end tags**, and text/list tags
(`<p>`/`<li>`/`<dt>`/…) flow as protected lines — together taking the renderable count from **0 to
87** of 145. The rest still need more tags (list fields, action processing) tracked in the repo's
issues, and the test ratchets a non-regressing renderable count as features land.

### Real-emulator smoke test

Most tests drive the server with a synthetic in-process client — fast, but a synthetic client can
pass while the server is broken against a real terminal (the Read Partition Query hang was exactly
that). `test_smoke_ws3270.py` boots the real server and connects an actual `ws3270`/`s3270`
emulator to it, logging in and navigating the ISPF panels, so the full TN3270E negotiation, header
framing, and session loop are exercised end-to-end. It skips automatically when no emulator is
installed, so it never blocks a machine that doesn't have one.

## Extending

To change a screen, **edit its `.dtl` file** — no Python changes needed. To add a new screen,
write a `panels/<name>.dtl` and call `load_panel("<name>")`.

To add real sub-menus, replace the `short_msg` response in `handle_client`'s ISPF loop with a
call that loads and sends a new panel, then reads the user's response.

To add more users, extend the `_CREDENTIALS` dict at the top of `server.py`.

## References

- [RFC 1576 — TN3270 Current Practices](https://tools.ietf.org/html/rfc1576) — the basic (non-E) negotiation, used as the fallback
- [RFC 2355 — TN3270E](https://tools.ietf.org/html/rfc2355) — the extended protocol (option, DEVICE-TYPE/FUNCTIONS, 5-byte header) this server negotiates
- [IBM 3270 Data Stream Programming Reference](https://www.ibm.com/docs/en/zos/2.5.0?topic=reference-3270-data-stream)
- [IBM ISPF Dialog Tag Language Guide and Reference](https://www.ibm.com/docs/en/SSLTBW_2.4.0/pdf/f54dt00_v2r4.pdf) — the SGML format the `panels/*.dtl` syntax is modeled on
- [x3270 / wc3270 emulator](https://x3270.miraheze.org/wiki/Main_Page)
- [pmattes/x3270 source (3270ds.h)](https://github.com/pmattes/x3270) — canonical WCC and field-attribute bit definitions

---

For educational and prototyping purposes.
