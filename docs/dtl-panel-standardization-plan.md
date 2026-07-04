# Panel DTL standardization — non-standard attribute audit & plan

Tracking issue: **#181** (make the bundled panels' DTL fully standard).

## Goal

The 21 bundled panels in `panels/*.dtl` are written in a **pragmatic explicit-layout dialect**
of DTL: they carry attributes that IBM's real Dialog Tag Language does not define, chiefly
absolute `row`/`col` positioning. This plan inventories every non-standard attribute, explains
why the dialect exists, and lays out a tiered path toward standard DTL — **without regressing the
byte-for-byte golden tests**.

This is an audit + design plan. No panel is changed by this document; the work is broken into
tiers that can be scheduled independently.

## Why the dialect exists (the governing constraint)

`dtl.py`'s own module docstring says it plainly: every visible element *may* carry explicit
`row`/`col`, and the bundled panels position **explicitly** so they render **byte-for-byte
identical** to the hand-built Phase-1 builders in `screens.py`. Two golden tests pin that
equality all the way down to the raw 3270 wire bytes:

- `test_dtl.py::test_logon_dtl_matches_builder` — `load_panel("logon").render() == build_tso_logon().render()`
- `test_dtl.py::test_ispf_dtl_matches_builder` — `load_panel("ispf", …).render() == build_ispf_menu(…).render()`

…and those builders are themselves pinned to the exact bytes in `test_screen.py`. Real DTL
relies on **auto-flow** layout (ISPDTLC computes positions); the docstring calls that
*"the genuinely hard part,"* and the parser only approximates it (`<panel>`/`<area>` as implicit
flow boxes). So the explicit coordinates are load-bearing, and **any** standardization must be
proven byte-identical per panel.

Concrete proof that even a "trivial" swap moves bytes — the `title=` attribute is metadata only,
while a content title adds a centered `Text` item:

```
<panel title="My Title">…   →  screen.title set, NO title item on the body
<panel>My Title…            →  adds Text(0, 36, "My Title")     # BYTE-IDENTICAL: False
```

## Inventory: every non-standard attribute

Cross-referenced against IBM's *Table 1. Tag summary* (mirrored in `docs/dtl-tags-reference.md`).
Counts are occurrences across all of `panels/*.dtl`.

| Attribute | Uses | On tag(s) | Why non-standard | Standard DTL equivalent |
|---|---|---|---|---|
| `row`, `col` | **311** | (every tag) | no DTL tag has positional attributes | auto-flow (ISPDTLC computes position) |
| `num` | 50 | `<choice>` | `CHOICE` has no `num` attribute | `SELFLD` auto-numbers its choices |
| `intensity` | 51 | `<info>`, `<selfld>` | wrong name **and** `INFO` has no intensity attribute | `INTENS=HIGH \| LOW \| NON` (and not on `INFO` at all) |
| `numcol` / `namecol` / `desccol` | 27 | `<selfld>` | not DTL attributes | `SELFLD` auto-layout |
| `numwidth` / `numintensity` | 18 | `<selfld>` | not DTL attributes | `ENTWIDTH` (selfld); intensity has no per-part attr |
| `title` | 21 | `<panel>` | the title is tag **content**, not an attribute | `<panel>Title text` (content) |
| `fill` (+ repurposed `width`) | 16 | `<info>` | not a DTL attribute | `<divider>` / `DIV=SOLID\|DASH` |
| `fldcol` | 16 | `<dtafld>` | not a DTL attribute | auto (entry after prompt; `<dtacol>` `PMTWIDTH`) |
| `cursor` | 12 | `<dtafld>`, `<cmdarea>` | field-level cursor isn't DTL | `<panel cursor=field-name>` |
| `action` | 5 | `<pdc>` | a PDC's action is a **child** tag | `<pdc>label<action run=…>` |
| `default` | 2 | `<dtafld>` | not a DTL attribute | `INIT=initial-value` |
| `trunc` | 1 | `<cmd>` | `CMD` has no `trunc` attribute | `<t>` truncation marker (landed in #118) |
| `gap` | 1 | `<ab>` | not a DTL attribute | `ABSEPCHAR` / `ABSEPSTR` (separator, not spacing) |
| `fldgap` | 1 | `<area>` | not a DTL attribute | `FLDSPACE` (on `<dtafld>`/`<dtacol>`) |

**≈ 530 non-standard attribute occurrences**, dominated by positioning (`row`/`col` ≈ 311) and the
manual selection grid (`num*` + `*col` ≈ 95).

Attributes that **are** standard DTL and need no change: `name`, `key`, `cmd`, `help`, `entwidth`,
`width`, `datavar`, `color`, `usage`, `type`, `colwidth`, `varclass`, `headline`, `msg`, `display`,
`applid`, and `action` on `<cmdact>` (where `ACTION=` is legitimate).

Every one of the 21 panels uses explicit positioning; none is currently pure-flow.

## Per-attribute issues

Each non-standard attribute is tracked as its own child issue under #181 (the `row`/`col` pair
and the `<selfld>` grid are each one issue — a single mechanism removed by a single fix).
"Blocked" means the standard equivalent is not yet implemented in the parser.

| Child | Attribute(s) | Standard equivalent | Status |
|---|---|---|---|
| #184 | `row`, `col` | auto-flow positioning | blocked on #51 |
| #185 | `fldcol` | auto field-entry column | blocked on #51, #122 |
| #186 | `num`/`numcol`/`namecol`/`desccol`/`numwidth`/`numintensity` | `<selfld>` auto-layout | blocked on #183 |
| ~~#187~~ | ~~`intensity`~~ | ~~`intens`~~ | **closed invalid — `INTENS` is not a valid `<info>` attribute** |
| #188 | `title` | panel content text | decision needed (byte-parity) |
| #189 | `fill` | `<divider>` / `DIV=` | blocked on #125 |
| #190 | `cursor` | `<panel cursor=field-name>` | blocked on #125 |
| #191 | `action` on `<pdc>` | `<action run=…>` child | ready (already implemented) |
| #192 | `default` | `init=` | ready (small parser add) |
| #193 | `trunc` on `<cmd>` | `<t>` marker | ready (implemented, #118) |
| #194 | `gap` on `<ab>` | automatic AB spacing | blocked on #126 |
| #195 | `fldgap` on `<area>` | `FLDSPACE` | blocked on #122 |

**Implementation blockers:** #51 (auto-flow) · #183 (`<selfld>` auto-layout — opened for this work) ·
#125 (`CURSOR`, `DIVIDER`) · #122 (`FLDSPACE`, `PMTWIDTH`) · #126 (AB spacing/`ABSEPCHAR`).

**Ready now (no external blocker):** #191, #192, #193 — byte-preserving migrations to constructs
verified valid on their tags.

> **Target validity reassessed.** Every child's proposed standard equivalent was checked against the
> tag's DTL attribute list (IBM Table 1). All are valid on their tag **except #187**: `INTENS` is not
> an `<info>` attribute (INFO takes only `WIDTH`/`INDENT`), so renaming `intensity`→`intens` on
> `<info>` only swaps one non-standard attribute for another. The real need — body-text emphasis on
> `<info>` — is a **semantic** change (`<hp>`, the CUA field/attribute model, or promoting titles/
> instructions to their proper tags), not a per-attribute rename; it belongs with the geometry/
> semantic work, and #187 is closed as invalid.

## Tiered plan

Each tier is independently schedulable. The rule for all of them: **verify byte-identity** — render
every panel before and after and assert the 3270 stream is unchanged (`Screen.render()` bytes), the
same discipline the golden tests already enforce for `logon`/`ispf`.

### Tier 1 — direct aliases, byte-preserving (achievable now)

Each maps 1:1 to a standard construct the parser already largely understands. Small PRs, each
verified byte-identical across all panels.

1. **`default=` → `init=`** — add `INIT` as the canonical name, keep `default` as a tolerated alias
   in the parser, migrate the 2 panel uses.
2. **`<cmd trunc=3>desc` → `<cmd altdescr="desc">KEY<t>LIST`** — use the `<t>` marker from #118 for
   truncation; the human description moves to `ALTDESCR` (parsed as metadata; command tables render
   nothing, so bytes are unaffected). 1 use (`ispf.dtl`).
3. **`<pdc action=x>` → `<pdc>label<action run=x>`** — the parser already reads a nested `<action>`;
   migrate the 5 uses. Pull-downs render nothing on the base panel, so this is metadata-only.
4. ~~**`intensity=` → `intens=`**~~ — **not Tier 1 (closed invalid, #187).** `INTENS` is not a
   valid `<info>` attribute (INFO takes only `WIDTH`/`INDENT`), so this would swap one non-standard
   `<info>` attribute for another. Body-text emphasis on `<info>` is a **semantic** fix (Tier 3 /
   the geometry-semantic work), not an attribute rename.

### Tier 2 — small parser feature, still layout-preserving

5. **`cursor=` on fields → `<panel cursor=field-name>`** — implement PANEL `CURSOR=` (name a field),
   resolve to the same field, and assert the emitted `IC` order lands on the identical address. Then
   drop the 12 field-level `cursor` attributes.
6. **`title=` decision** — either (a) keep `title=` as a **documented** panel-metadata extension
   (it is genuinely useful and cheap), or (b) make content-form titles render byte-identically to the
   attribute form first, then migrate. Pick before touching the 21 uses.
7. **`fill=`/`width` rules → `<divider>` / `DIV=`** — make the divider path emit the identical rule
   bytes, then replace the 16 `<info fill=…>` separator lines.

### Tier 3 — the structural core: eliminate explicit positioning

`row`/`col`/`fldcol`/`num*`/`gap`/`fldgap` (≈ 420 uses) exist **only** because the panels are laid
out by hand. Removing them requires the auto-flow engine to reproduce each panel's exact layout with
no coordinates — i.e. ISPDTLC-parity flow, tracked by the geometry (#125) and selection (#128) gaps.
This is the genuinely hard, possibly-partially-infeasible part. Options, in increasing ambition:

- **(b) Document the extension.** Formally record `row`/`col`/`fldcol`/`num*` as a recognized
  **explicit-position dialect** of this server's DTL (they already work and are predictable), and
  declare Tiers 1–2 the achievable standardization scope. Honest and low-risk.
- **(c) Proof-of-concept flow migration.** Convert the simplest panel (e.g. `browse.dtl`, 2 coords)
  to pure flow and prove byte-identity, to measure what full migration actually costs before
  committing to it.
- **(a) Full flow parity.** Invest in ISPDTLC-parity auto-layout, then delete coordinates panel by
  panel, each gated on the golden byte tests. Largest effort; do only if flow fidelity becomes a
  first-class goal.

## Recommendation

Land **Tier 1** as a handful of byte-verified PRs (real, provable standardization), decide the
`title=`/`cursor=` questions in **Tier 2**, and treat **Tier 3** as option **(b)** — document the
explicit-position dialect — unless/until auto-flow parity becomes a deliberate investment. That
converts the *easily-standardizable* ~60 attribute uses to real DTL and honestly scopes the ~470
positional uses that depend on a much larger layout-fidelity project.

## Verification discipline (all tiers)

For every migration PR:

1. Snapshot `load_panel(name).render()` bytes for all 21 panels before the change.
2. Apply the attribute migration + any parser alias.
3. Assert every panel's rendered bytes are **unchanged** (extend the golden-test pattern beyond
   `logon`/`ispf` to a full-corpus byte snapshot for this work).
4. Run the full suite (`pytest -q`).

See also: `docs/dtl-tags-reference.md` (per-tag standard attributes) and the attribute-coverage
issues #122–#129.
