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
"""
import glob
import os

import pytest

from dtl import load_dtl, DTLError

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests", "dtl_examples")
EXAMPLE_FILES = sorted(glob.glob(os.path.join(_DIR, "*.dtl")))

# Examples that currently render to a non-empty screen. Bump this as features
# land so the corpus can only get *more* renderable, never less. 0 before
# auto-flow; 18 once a panel became an implicit flow box (#51); 87 with implicit
# end tags + text/list tags (#52); 126 with panel-title text + nested-list
# bullets/indentation; 132 with <msg suffix> + lenient unsupported <checki>;
# 134 with <dtacol>/<divider> + tolerating a stray <vardcl> (as ISPDTLC does);
# 135 with implicit <pdc>/<abc> end tags (action-bar pull-downs).
RENDER_BASELINE = 135


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


def test_renderable_count_does_not_regress():
    renderable = 0
    for path in EXAMPLE_FILES:
        try:
            if len(_load(path).items) > 0:
                renderable += 1
        except DTLError:
            pass
    assert renderable >= RENDER_BASELINE, (
        f"only {renderable} guide examples render; expected >= {RENDER_BASELINE}"
    )
