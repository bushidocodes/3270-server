"""Golden byte-stream tests (#354): the codec, ungated from the emulator.

The ws3270 smoke tests skip when no emulator is installed, so on an
emulator-less machine end-to-end byte fidelity used to go unchecked. These
tests rebuild the whole outbound corpus in memory — every bundled panel's mono
and colour render, plus two scripted session transcripts (see
:mod:`golden_corpus`) — and assert each record byte-for-byte against the
snapshot recorded in ``golden/corpus.txt`` from a smoke-verified build. No
sockets, no emulator: the emulator tests verify the *emulator*, this file
guards the *bytes* on every run.

A failure here means the wire actually changed. If that change is intended
(and verified against a real emulator — run the ws3270 smoke suite), re-record
with ``python golden_corpus.py --write`` and commit the fixture with the code.
"""
import pytest

import golden_corpus

_RECORDED = golden_corpus.load_corpus()
_LIVE = golden_corpus.build_corpus()

_REGENERATE = ("if this byte change is intended and emulator-verified, "
               "re-record with: python golden_corpus.py --write")


def test_corpus_covers_exactly_the_live_records():
    """A new panel (or a new/removed transcript step) must be recorded; a
    deleted one must be un-recorded — the fixture tracks the corpus exactly."""
    missing = sorted(set(_LIVE) - set(_RECORDED))
    extra = sorted(set(_RECORDED) - set(_LIVE))
    assert not missing and not extra, (
        f"golden/corpus.txt is out of step with the live corpus "
        f"(missing={missing}, extra={extra}); {_REGENERATE}")


@pytest.mark.parametrize("name", sorted(_RECORDED))
def test_record_matches_golden(name):
    assert name in _LIVE, f"{name} is recorded but no longer built; {_REGENERATE}"
    recorded, live = _RECORDED[name], _LIVE[name]
    if live != recorded:
        offset = next((i for i, (a, b) in enumerate(zip(recorded, live))
                       if a != b), min(len(recorded), len(live)))
        pytest.fail(
            f"{name}: outbound bytes changed at offset {offset} "
            f"(recorded {len(recorded)} bytes: "
            f"…{recorded[max(0, offset - 4):offset + 8].hex()}…, "
            f"live {len(live)} bytes: "
            f"…{live[max(0, offset - 4):offset + 8].hex()}…); {_REGENERATE}")


def test_every_record_is_a_framed_3270_record():
    """Sanity on the corpus itself: every record is non-empty and IAC-EOR
    terminated (the framing every outbound 3270 record shares)."""
    for name, record in _RECORDED.items():
        assert record and record[-2:] == b"\xff\xef", name
