"""Tests for the structured-field framing primitive (#353).

The outbound Write-Structured-Field builders moved to :mod:`structured_fields`,
each a one-liner over :func:`structured_fields.wsf` — the shared ``F3`` +
self-counting 2-byte length framing they all repeated. The builders' exact
bytes are pinned by the existing tests in test_query.py (through the
:mod:`server` re-exports, which these tests also verify); here we pin the
framing primitive itself.
"""
import server
import structured_fields
from structured_fields import (
    WSF,
    activate_partition,
    create_partition,
    destroy_partition,
    erase_reset,
    load_programmed_symbols,
    outbound_3270ds,
    read_partition_query,
    read_partition_query_list,
    select_char_set,
    set_reply_mode,
    wsf,
)


# ── the framing primitive ─────────────────────────────────────────────────────

def test_wsf_frames_command_length_and_id():
    # F3, a 2-byte big-endian length that counts itself and the SFID, the SFID,
    # then the body verbatim.
    assert wsf(0x01, bytes([0xFF, 0x02])) == bytes([0xF3, 0x00, 0x05, 0x01, 0xFF, 0x02])


def test_wsf_with_an_empty_body_is_the_minimal_field():
    # Length 0x0003: the two length bytes and the id — nothing else.
    assert wsf(0x03) == bytes([WSF, 0x00, 0x03, 0x03])


def test_wsf_length_spans_both_bytes():
    # A body long enough to carry the length into the high byte: 300 body bytes
    # + 2 length bytes + 1 id byte = 0x012F.
    record = wsf(0x40, bytes(300))
    assert record[0] == WSF
    assert int.from_bytes(record[1:3], "big") == 303
    assert len(record) == 304


def test_wsf_masks_the_sfid_to_a_byte():
    assert wsf(0x1_40)[3] == 0x40


# ── every builder rides the primitive, and server re-exports it ──────────────

def test_builders_are_re_exported_from_server_unchanged():
    # Existing call sites and tests import these from server; they must be the
    # same objects, so test_query.py's byte-for-byte assertions pin the moved
    # builders too.
    for name in ("read_partition_query", "read_partition_query_list",
                 "set_reply_mode", "erase_reset", "create_partition",
                 "activate_partition", "destroy_partition", "outbound_3270ds",
                 "load_programmed_symbols", "select_char_set", "wsf", "WSF"):
        assert getattr(server, name) is getattr(structured_fields, name)


def test_every_builder_is_a_wsf_frame_with_a_self_counting_length():
    for record in (
        read_partition_query(),
        read_partition_query_list(),
        set_reply_mode(0x02, [0x41, 0x42]),
        erase_reset(),
        erase_reset(alternate=True),
        create_partition(1, 12, 40),
        activate_partition(2),
        destroy_partition(2),
        outbound_3270ds(1, b"\xc3\x11\x40\x40", command=0xF5),
        load_programmed_symbols(0x41, 0x81, symbols=bytes(16)),
    ):
        assert record[0] == WSF
        # the length counts everything after the command byte
        assert int.from_bytes(record[1:3], "big") == len(record) - 1


def test_select_char_set_is_a_plain_sa_order_not_a_wsf():
    # The one non-WSF builder in the module: SA + XA_CHARSET + lcid.
    assert select_char_set(0x41) == bytes([0x28, 0x43, 0x41])
