"""Outbound Write-Structured-Field builders, behind one framing primitive (#353).

Every outbound structured field the server can send shares one wire shape:
``F3`` (the Write Structured Field command) followed by one or more structured
fields, each ``[len][len] <sfid> <parameters…>`` with a self-counting 2-byte
big-endian length. :func:`wsf` is that framing, written once; each builder is a
one-liner naming just its SFID and parameter bytes.

Like :mod:`ds3270` and :mod:`goca`, this is a dependency-free leaf module: the
builders produce **logical** bytes — the caller IAC-escapes them (the Query's
partition byte is a literal 0xFF) and IAC-EOR-terminates the record, exactly as
:func:`server.query_terminal` and :func:`server.request_reply_mode` do. The
session-level gates (which terminals these may be sent to) stay in
:mod:`server`, where the Query Reply capabilities live. All names are
re-exported from :mod:`server`, so existing call sites and tests work unchanged.
"""

WSF = 0xF3                  # Write Structured Field command (outbound)


def wsf(sfid: int, body: bytes = b"") -> bytes:
    """Frame one structured field as a Write Structured Field record:
    ``F3 [len][len] <sfid> <body…>``, where the 2-byte big-endian length counts
    itself, the id, and the body (GA23-0059 §5, "Structured Field Formats").
    ``body`` is the field's parameter bytes after the SFID. Logical bytes; the
    caller IAC-escapes and IAC-EOR-terminates."""
    length = 2 + 1 + len(body)
    return bytes([WSF, (length >> 8) & 0xFF, length & 0xFF, sfid & 0xFF]) + bytes(body)


def _u16(n: int) -> bytes:
    """A 16-bit big-endian structured-field parameter."""
    return bytes([(n >> 8) & 0xFF, n & 0xFF])


# ── Read Partition (Query / Query List) ──────────────────────────────────────
# The TERMINAL-TYPE string is only a hint. The authoritative way for a host to
# learn a terminal's real geometry and capabilities is the Query: the host sends
# a Read Partition (Query) structured field, and the terminal answers (inbound
# AID 0x88) with Query Reply structured fields (parsed in :mod:`server`).

SF_READ_PARTITION = 0x01    # structured-field id: Read Partition

# Read Partition request types (byte after the partition id).
SF_RP_QUERY = 0x02          # plain Query
SF_RP_QLIST = 0x03          # Query List
SF_RPQ_ALL = 0x80           # ...request type: return all supported QCODEs


def read_partition_query() -> bytes:
    """The plain Read Partition (Query) structured field: ``F3`` (WSF) then
    ``00 05 01 FF 02`` — length 0x0005, id 0x01 (Read Partition), partition 0xFF
    (whole device), type 0x02 (Query). Logical bytes; the caller IAC-escapes."""
    return wsf(SF_READ_PARTITION, bytes([0xFF, SF_RP_QUERY]))


def read_partition_query_list() -> bytes:
    """A Read Partition **Query List** asking the terminal to enumerate every
    QCODE it supports: ``F3`` then ``00 06 01 FF 03 80`` — type 0x03 (Query
    List), request type 0x80 (All). Logical bytes; the caller IAC-escapes. The
    reply's Summary (QCODE 0x80) lists the full capability set."""
    return wsf(SF_READ_PARTITION, bytes([0xFF, SF_RP_QLIST, SF_RPQ_ALL]))


# ── Set Reply Mode (#112) ────────────────────────────────────────────────────
# A Write Structured Field that tells the terminal which inbound reply mode to
# use for Read Modified. The mode governs whether a modified field's extended
# attributes come back with it.
SF_SET_REPLY_MODE = 0x09
RM_FIELD = 0x00             # modified fields, text only (the default)
RM_EXTENDED_FIELD = 0x01    # each field preceded by its extended attributes
RM_CHARACTER = 0x02         # as Extended Field, plus per-character SA changes
SA_ORDER = 0x28             # Set Attribute order — how those attributes ride inbound


def set_reply_mode(mode: int, attrs=()) -> bytes:
    """The Set Reply Mode structured field: ``F3`` (WSF) then ``[len][len] 09 00
    <mode> [attr-type…]`` — id 0x09, partition 0x00, the reply ``mode``, and (only
    meaningful for Character mode) the list of attribute-type codes the terminal
    should report (empty = all it supports). ``len`` counts itself. Logical bytes;
    the caller IAC-escapes and IAC-EOR-terminates it, exactly like the Query."""
    return wsf(SF_SET_REPLY_MODE, bytes([0x00, mode]) + bytes(attrs))


# ── Erase/Reset (#102) ───────────────────────────────────────────────────────
# The simplest partition-control structured field. It tears down any explicit
# partition state the host had set up and re-establishes a single **implicit**
# partition covering the whole screen, then erases it — the clean-slate a host
# issues before switching between the default and the alternate screen size. The
# one parameter byte selects which size that implicit partition uses, so this is
# the outbound SF a "split view then back to one full-screen partition" use case
# would send to collapse back to one viewport.
# (Field-format Create/Set/Destroy Partition SFs are a far larger surface; this
# one field is self-contained, and — unlike them — every x3270-family terminal
# implements it, which is what let us verify it end-to-end against ws3270.)
SF_ERASE_RESET = 0x03       # structured-field id: Erase/Reset
ER_DEFAULT = 0x00           # ...reset to an implicit partition of the DEFAULT size
ER_ALTERNATE = 0x80         # ...reset to an implicit partition of the ALTERNATE size


def erase_reset(alternate: bool = False) -> bytes:
    """The Erase/Reset structured field: ``F3`` (WSF) then ``00 04 03 <flag>`` —
    length 0x0004 (which counts the two length bytes, the id, and the flag), id
    0x03 (Erase/Reset), and a single flag byte selecting the implicit partition's
    screen size: :data:`ER_DEFAULT` (0x00) or :data:`ER_ALTERNATE` (0x80). The
    flag is the *whole* body — GA23-0059 defines only its two high-order bits and
    reserves the rest — so no other parameters follow. Logical bytes; the caller
    IAC-escapes and IAC-EOR-terminates it, exactly like the Query and Set Reply
    Mode. Kept opt-in: nothing in the bundled session sends this (see #102)."""
    return wsf(SF_ERASE_RESET, bytes([ER_ALTERNATE if alternate else ER_DEFAULT]))


# ── Explicit partition management (#307) ─────────────────────────────────────
# Erase/Reset (above) only collapses back to the single implicit partition; a real
# split screen needs the *explicit*-partition structured fields. These build the
# four outbound SFs GA23-0059 defines for that (verified against the emulator where
# it implements them — see below), each mirroring erase_reset: pure logical bytes,
# IAC-escaped and IAC-EOR-terminated by the caller. All are opt-in — nothing in the
# bundled session sends them; gate on the terminal advertising Alphanumeric
# Partitions (see :func:`server.partitions_supported`).
#
# SFIDs (GA23-0059-4 §5, "Outbound Structured Fields", ID table): note x3270/ws3270
# implement only Create Partition (0x0C) and Outbound 3270DS (0x40) — Activate/
# Destroy return "unsupported ID", so only the former two are emulator-verifiable;
# the latter two are byte-structure-verified (their format is trivial: id + PID).
SF_CREATE_PARTITION = 0x0C   # Create Partition — define an explicit partition
SF_ACTIVATE_PARTITION = 0x0E  # Activate Partition — make one the active partition
SF_DESTROY_PARTITION = 0x0D  # Destroy Partition — tear an explicit partition down
SF_OUTBOUND_3270DS = 0x40    # Outbound 3270DS — a 3270 write targeted at a partition

# Create Partition UOM (unit of measurement, high nibble of the flags byte) and
# addressing mode (low nibble). x3270 accepts UOM 0 (character cells) or 2, AM ≤ 2.
CP_UOM_CELLS = 0x00          # distances measured in character cells
CP_AM_12_14BIT = 0x00        # 12/14-bit buffer addressing

# Outbound 3270DS write command (byte 4): the SNA command the wrapped record runs.
ODS_WRITE = 0xF1             # Write
ODS_ERASE_WRITE = 0xF5      # Erase/Write
ODS_ERASE_WRITE_ALTERNATE = 0x7E  # Erase/Write Alternate
ODS_ERASE_ALL_UNPROTECTED = 0x6F  # Erase All Unprotected


def create_partition(pid: int, rows: int, cols: int,
                     viewport_row: int = 0, viewport_col: int = 0,
                     viewport_height: int = None, viewport_width: int = None,
                     window_row: int = 0, window_col: int = 0,
                     scroll_rows: int = 0) -> bytes:
    """The Create Partition structured field (SFID 0x0C): ``F3`` (WSF) then
    ``[len][len] 0C <pid> <flags> <PSH> <PSW> <RV> <CV> <HV> <WV> <RW> <CW> <RS>``.

    Field layout (GA23-0059 *Create Partition*, byte offsets within the SF, all
    16-bit values big-endian; confirmed against x3270's ``sf_create_partition``):
    byte 2 = 0x0C, byte 3 = ``pid`` (partition id 0x00–0x7E), byte 4 = flags — the
    high nibble is the unit of measurement (:data:`CP_UOM_CELLS`) and the low
    nibble the addressing mode (:data:`CP_AM_12_14BIT`), byte 5 = reserved flags
    (0), then the 16-bit fields: presentation-space ``rows``/``cols`` (6-7/8-9),
    viewport origin ``viewport_row``/``viewport_col`` (10-11/12-13), viewport
    ``viewport_height``/``viewport_width`` (14-15/16-17, defaulting to the PS size),
    window origin ``window_row``/``window_col`` (18-19/20-21), and ``scroll_rows``
    (22-23). ``len`` counts itself. Logical bytes; the caller IAC-escapes and
    IAC-EOR-terminates it, exactly like :func:`erase_reset`. Opt-in (see #307)."""
    if viewport_height is None:
        viewport_height = rows
    if viewport_width is None:
        viewport_width = cols
    flags = (CP_UOM_CELLS << 4) | CP_AM_12_14BIT
    return wsf(SF_CREATE_PARTITION,
               bytes([pid & 0xFF, flags, 0x00])
               + _u16(rows) + _u16(cols)
               + _u16(viewport_row) + _u16(viewport_col)
               + _u16(viewport_height) + _u16(viewport_width)
               + _u16(window_row) + _u16(window_col)
               + _u16(scroll_rows))


def activate_partition(pid: int) -> bytes:
    """The Activate Partition structured field (SFID 0x0E): ``F3`` then
    ``00 04 0E <pid>`` — make partition ``pid`` the active one for subsequent
    writes/reads. Length 0x0004 counts itself, the id and the pid (GA23-0059
    *Activate Partition* — a two-byte body). Logical bytes; the caller
    IAC-escapes and IAC-EOR-terminates. Opt-in (see #307)."""
    return wsf(SF_ACTIVATE_PARTITION, bytes([pid & 0xFF]))


def destroy_partition(pid: int) -> bytes:
    """The Destroy Partition structured field (SFID 0x0D): ``F3`` then
    ``00 04 0D <pid>`` — tear down explicit partition ``pid`` (Erase/Reset
    collapses *all* partitions; this removes one). Same trivial two-byte body as
    :func:`activate_partition` (GA23-0059 *Destroy Partition*). Logical bytes; the
    caller IAC-escapes and IAC-EOR-terminates. Opt-in (see #307)."""
    return wsf(SF_DESTROY_PARTITION, bytes([pid & 0xFF]))


def outbound_3270ds(pid: int, record: bytes, command: int = ODS_WRITE) -> bytes:
    """The Outbound 3270DS structured field (SFID 0x40): ``F3`` then
    ``[len][len] 40 <pid> <cmd> <record…>`` — wrap a normal 3270 write (WCC +
    orders + data, *without* its own command byte) so it paints a specific
    partition. Byte 3 is the target ``pid``, byte 4 the SNA write ``command``
    (:data:`ODS_WRITE` / :data:`ODS_ERASE_WRITE` / …), and the rest is the 3270
    data stream (GA23-0059 *Outbound 3270DS*; confirmed against x3270's
    ``sf_outbound_ds``, which passes byte 4 onward to its writer). ``len`` counts
    itself. Logical bytes; the caller IAC-escapes and IAC-EOR-terminates. Opt-in
    (see #307)."""
    return wsf(SF_OUTBOUND_3270DS, bytes([pid & 0xFF, command & 0xFF]) + bytes(record))


# ── Load Programmed Symbols (#308) ───────────────────────────────────────────
# The structured field that downloads a host-defined character set (a "programmed
# symbol set", identified by an LCID) into the terminal's Read/Write Storage. Once
# loaded, the data stream selects it per character via the character-set attribute
# (SA type 0x43 — see :func:`select_char_set`), the same alternate-set machinery
# the Graphic Escape uses. Opt-in — nothing bundled loads glyphs; gate on the
# terminal advertising Character Sets (see :func:`server.programmed_symbols_supported`).
SF_LOAD_PS = 0x06            # structured-field id: Load Programmed Symbols
# FLAGS byte (GA23-0059 *Load Programmed Symbols*): bit0 basic/extended form,
# bit1 clear unloaded slots, bit2 skip-suppress, bits3-7 the data-format TYPE.
LPS_FLAG_EXTENDED = 0x80     # extended form (parameter bytes 7+ follow byte 6)
LPS_FLAG_CLEAR = 0x40        # clear all character slots not loaded by this SF
LPS_FLAG_SKIP = 0x20         # skip-suppress the loaded characters
# TYPE (bits 3-7 of the flags byte): the dot-matrix data format.
LPS_TYPE1 = 0x01             # Type 1: 2-byte vertical slice + 8-bit horizontal slices
LPS_TYPE2 = 0x02             # Type 1 compressed
LPS_TYPE3 = 0x03             # Type 3: row loading (top to bottom)
LPS_TYPE5 = 0x05             # Type 5: column loading (left to right)
LPS_VECTOR = 0x08            # vector (outline) form
# SA (Set Attribute) character-set attribute type — selects the character set an
# LCID names for the characters that follow it (GA23-0059 extended field/char attrs).
XA_CHARSET = 0x43
CS_BASE = 0x00               # LCID of the base (default EBCDIC) character set


def load_programmed_symbols(lcid: int, start_code: int, rws: int = 0,
                            symbols: bytes = b"", *, load_type: int = LPS_TYPE1,
                            clear: bool = False, skip_suppress: bool = False,
                            ext_params: bytes = b"") -> bytes:
    """The Load Programmed Symbols structured field (SFID 0x06): ``F3`` (WSF) then
    ``[len][len] 06 <flags> <lcid> <char> <rws> [ext-params] <symbols…>``.

    Downloads a programmed symbol set (GA23-0059 *Load Programmed Symbols*). Byte
    layout: byte 2 = 0x06, byte 3 = ``flags`` — bit 0 basic/extended form (set when
    ``ext_params`` is given), bit 1 CLEAR (:data:`LPS_FLAG_CLEAR`), bit 2
    skip-suppress (:data:`LPS_FLAG_SKIP`), bits 3-7 the data-format ``load_type``
    (:data:`LPS_TYPE1`…); byte 4 = ``lcid`` (local character-set id, 0x40-0xEF, or
    0xFF to free the set's storage); byte 5 = ``start_code`` (the first code point
    loaded, 0x41-0xFE); byte 6 = ``rws`` (loadable-set RWS number). In the extended
    form the ``ext_params`` block (a self-describing ``[p-length][params…]``) follows
    byte 6. The ``symbols`` bytes are the dot-matrix definitions for consecutive
    code points from ``start_code`` — passed through verbatim, so the caller controls
    the matrix format named by ``load_type``. ``len`` counts itself. Logical bytes;
    the caller IAC-escapes and IAC-EOR-terminates it, exactly like the Query. Opt-in
    (see #308)."""
    flags = (load_type & 0x1F)
    if clear:
        flags |= LPS_FLAG_CLEAR
    if skip_suppress:
        flags |= LPS_FLAG_SKIP
    ext = b""
    if ext_params:
        flags |= LPS_FLAG_EXTENDED
        ext = bytes([len(ext_params) + 1]) + bytes(ext_params)   # P LENGTH counts itself
    return wsf(SF_LOAD_PS,
               bytes([flags, lcid & 0xFF, start_code & 0xFF, rws & 0xFF])
               + ext + bytes(symbols))


def select_char_set(lcid: int) -> bytes:
    """A Set Attribute order selecting the character set ``lcid`` names for the
    characters that follow it: ``28 43 <lcid>`` (SA, attribute type 0x43). Not a
    structured field — it rides *inside* a write's data — but it is the other half
    of the programmed-symbols surface, so it lives with
    :func:`load_programmed_symbols`. Use it in a field's data to render text from
    a set loaded by that SF; ``lcid`` :data:`CS_BASE` (0x00) restores the base
    EBCDIC set. This is the programmed-symbol analogue of the Graphic Escape's
    alternate-set selection (#308)."""
    return bytes([SA_ORDER, XA_CHARSET, lcid & 0xFF])
