"""Render the DTL examples extracted from IBM's z/OS 2.4 ISPF Dialog Tag
Language Guide and Reference (f54dt00_v2r4.pdf) through our parser.

The examples live in ``tests/dtl_examples/`` (one ``<panel>`` example per file,
extracted verbatim). They exercise our DTL subset against the *real* reference.

Two things are asserted:

1. **Robustness** — every example loads without an *unexpected* exception. A
   :class:`DTLError` is allowed: it means we recognise the markup but don't yet
   support it (e.g. auto-flow layout with no explicit ``row``/``col``, or a tag
   we haven't implemented). Any *other* exception is a real parser bug.
2. **No render regression** — the number of examples that render to a non-empty
   screen never drops below :data:`RENDER_BASELINE`. As we add features
   (auto-flow, list fields, …) more examples will render and the baseline rises.

Before the auto-flow work, **0** of the guide's panel examples rendered (the
guide universally uses implicit positioning). With the panel now an implicit
flow box (#51), **18** render; the rest still need a larger tag set (text/list
tags, list fields, …) tracked in GitHub issues. This corpus is how we measure
progress against them.

A few examples *correctly* produce an empty screen — see :data:`EXPECTED_EMPTY`
(a bare ``<panel></panel>`` has nothing to render). Those are not gaps. Separate
from them, :data:`NEEDS_INVESTIGATION` holds degenerate extraction fragments that
merely *parse* down to empty; whether empty is the right answer is still an open
question, so they are neither counted as handled nor asserted to render empty.
"""
import glob
import os

import pytest

from dtl import load_dtl, DTLError

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests", "dtl_examples")
EXAMPLE_FILES = sorted(glob.glob(os.path.join(_DIR, "*.dtl")))

# Examples whose *correct* rendering is provably an empty screen: the guide
# shows a bare ``<panel></panel>`` with no body, so there is nothing to render.
# Empty is the right answer, so these count as handled.
# :func:`test_expected_empty_examples_render_empty` pins the contract — if one
# starts emitting content, it is no longer empty-by-design and must move out.
EXPECTED_EMPTY = {
    "ex000.dtl": "<panel></panel> — empty panel, empty screen is correct",
    "ex001.dtl": "<panel name=panel1></panel> — empty body",
    "ex002.dtl": "<panel name=... depth=20 width=40></panel> — empty body",
}

# Degenerate fragments in the extracted corpus (PDF page breaks / truncation): a
# stray ``</panel>``, an unclosed ``<panel>``, or a standalone definition block.
# Our lenient parser tolerates them down to zero items, but we CANNOT claim empty
# is the correct output without re-checking the source PDF and re-extracting.
# They are open extraction questions — not counted as handled, and deliberately
# not asserted to render empty (that would bless an unverified result).
NEEDS_INVESTIGATION = {
    "ex031.dtl": "<pandef> ref-def + unclosed <panel>",
    "ex032.dtl": "stray </panel> + unclosed <panel name=panel02 ...>",
    "ex083.dtl": "<varlist>/<vardcl> defs + empty <panel> body",
    "ex124.dtl": "truncated '<PANEL'",
}

# Examples that currently render to a non-empty screen. Bump this as features
# land so the corpus can only get *more* renderable, never less. 0 before
# auto-flow; 18 once a panel became an implicit flow box (#51); 87 with implicit
# end tags + text/list tags (#52); 126 with panel-title text + nested-list
# bullets/indentation; 132 with <msg suffix> + lenient unsupported <checki>;
# 134 with <dtacol>/<divider> + tolerating a stray <vardcl> (as ISPDTLC does);
# 135 with implicit <pdc>/<abc> end tags (action-bar pull-downs). (Side-by-side
# <region dir=horiz> columns are a layout-fidelity change, like word-wrap, so
# they don't move this count — the affected examples already rendered stacked.)
RENDER_BASELINE = 137


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return load_dtl(fh.read())


@pytest.mark.parametrize("path", EXAMPLE_FILES,
                         ids=[os.path.basename(p) for p in EXAMPLE_FILES])
def test_guide_example_loads_without_unexpected_error(path):
    try:
        _load(path)
    except DTLError:
        pass  # a recognised-but-unsupported feature, not a crash


def test_corpus_is_present():
    assert len(EXAMPLE_FILES) >= 140  # the extracted panel examples


def _categorize():
    """Bucket every example: 'rendered', 'empty' (parsed, no items), or 'dtlerror'."""
    buckets = {"rendered": [], "empty": [], "dtlerror": []}
    for path in EXAMPLE_FILES:
        name = os.path.basename(path)
        try:
            scr = _load(path)
        except DTLError:
            buckets["dtlerror"].append(name)
            continue
        buckets["rendered" if len(scr.items) > 0 else "empty"].append(name)
    return buckets


def test_renderable_count_does_not_regress():
    rendered = _categorize()["rendered"]
    assert len(rendered) >= RENDER_BASELINE, (
        f"only {len(rendered)} guide examples render; expected >= {RENDER_BASELINE}"
    )


def test_no_unexpected_empty_render():
    """Every example that parses to an empty screen must be a *known* empty one.

    This is the flip side of the render baseline: it catches a regression where
    an example that used to render silently drops to zero items. Empty is only
    accounted for by :data:`EXPECTED_EMPTY` (provably empty-by-design) or
    :data:`NEEDS_INVESTIGATION` (unresolved extraction fragments). Anything else
    that renders empty is a real gap to investigate — add it to the right bucket
    with a reason, or fix the regression.
    """
    accounted = EXPECTED_EMPTY.keys() | NEEDS_INVESTIGATION.keys()
    unexpected = [n for n in _categorize()["empty"] if n not in accounted]
    assert not unexpected, (
        "these examples parsed to an empty screen but are in neither "
        "EXPECTED_EMPTY nor NEEDS_INVESTIGATION (a render regression, or a new "
        f"empty result that needs triage): {unexpected}"
    )


def test_expected_empty_examples_render_empty():
    """Pin the EXPECTED_EMPTY contract: each parses cleanly (DTLError tolerated)
    and yields zero items. If one starts rendering content, move it out of the
    set — its empty render is no longer the expected output."""
    for path in EXAMPLE_FILES:
        name = os.path.basename(path)
        if name not in EXPECTED_EMPTY:
            continue
        try:
            scr = _load(path)
        except DTLError:
            continue  # tolerated: recognised-but-unsupported still isn't a crash
        assert len(scr.items) == 0, (
            f"{name} now renders {len(scr.items)} item(s); it is no longer "
            f"empty-by-design — remove it from EXPECTED_EMPTY ({EXPECTED_EMPTY[name]})"
        )
