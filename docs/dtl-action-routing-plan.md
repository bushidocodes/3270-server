# Declarative panel routing (`)PROC` / `ZSEL` / `TRANS`) — design plan

Tracking issue: **#55** (DTL action / variable processing).

## Goal

Move the ISPF **option → behavior** routing out of the hardcoded `if/elif` chain in
`server.py` and into the DTL panels themselves — the way the reference does it — **without
changing what any option does**. Behaviors that are genuinely code (prompting for a member,
populating a table, an interactive shell) stay in Python; only the *routing decision* becomes
declarative.

## The authentic mechanism

Real ISPF panels route in their `)PROC` section, using `TRANS` to map the typed option
(`ZCMD`) to a **selection string**:

```
)PROC
  &ZSEL = TRANS( TRUNC(&ZCMD,'.')
      0,'PANEL(settings)'
      1,'PGM(view)'
      3,'PANEL(utility)'
      X,'EXIT'
      *,'?' )
```

A selection string is one of `PANEL(name)`, `PGM(name) [PARM(...)]`, or `CMD(...)`. DTL carries
the `)PROC` in a `<source type=proc>` block. We currently parse `<source>` as a **no-op**
(content correctly ignored), so this is a clean extension point.

## Target vocabulary (authentic)

| Selection string | Meaning | Maps to (today) |
|---|---|---|
| `PANEL(x)` | enter panel `x` (a selection sub-menu or a display panel) | `_show_submenu` / `_show_overlay` |
| `PGM(x) [PARM(...)]` | run program `x` (does real work) | `_show_view`, `_show_command_shell`, dialog-test, … |
| `CMD(...)` | run a command | (future) |
| `EXIT` | leave the panel | the existing exit path |

## Current hardcoded map (`server.py`, the ISPF primary menu)

| Option | Today | Declared target |
|---|---|---|
| 0  | `_show_overlay("settings")` | `PANEL(settings)` |
| 1  | `_show_view(viewentry, BROWSE)` | `PGM(view)` |
| 2  | `_show_view(editentry, EDIT)` | `PGM(edit)` |
| 3  | `_show_submenu("utility", 3.1=member list)` | `PANEL(utility)` |
| 4/5/9/10/12/13 | `_show_submenu(_SUBMENUS[n])` | `PANEL(foreground)` … |
| 6  | `_show_command_shell()` | `PGM(cmdshell)` |
| 7  | `_show_overlay("dlgtest", rows=…)` | `PGM(dlgtest)` |
| 11 | `_show_overlay("workplace")` | `PANEL(workplace)` |
| X  | exit | `EXIT` |

## Architecture

1. **Parse layer** — parse the `&ZSEL = TRANS(&ZCMD n,'target' …)` idiom inside
   `<source type=proc>` into `Screen.selection_targets: {option → target-string}`. Scope
   strictly to this idiom; all other `)PROC` content stays ignored (a documented limitation,
   **not** a general interpreter).
2. **Dispatch layer** — replace the `if/elif` with: `target = screen.selection_targets.get(option)`
   → parse `PANEL/PGM/CMD(name) [PARM …]` → dispatch via a **handler registry**
   `{name → existing Python behavior}`. The registry's contents *are* today's behaviors,
   refactored — not rewritten.
3. **Migration** — add the `)PROC ZSEL=TRANS(…)` block to `ispf.dtl` first, then the submenu
   panels, declaring the routing that currently lives in `server.py`.

## Incremental, non-breaking rollout

- **PR 1 — parse + verify (zero behavior change).** Parse `<source type=proc>` ZSEL/TRANS into
  `Screen.selection_targets`; add the `)PROC` block to `ispf.dtl`. Add a test asserting the
  *declared* map equals the *hardcoded* map. Server dispatch untouched. `ispf.dtl` stays
  byte-identical (a `<source>` block renders nothing). Completely safe; proves the declarative
  layer is correct against live behavior.
- **PR 2 — dispatch via registry (the risky step, gated by PR 1).** Introduce the
  `{name → handler}` registry (= refactored `if/elif`, behaviors identical). Route through
  `selection_targets` + registry. Safety net: the existing integration tests already drive real
  options end-to-end — `test_typed_option_opens_dialog_test` (7), `…_command_shell` (6),
  `…_point_and_shoot_opens_utilities` (3), `…_dotted_jump_opens_member_list` (3.1),
  `…_pf3_from_menu_logs_off`.
- **PR 3 — submenu `)PROC` recursion.** Give the submenu panels their own `)PROC` so
  `PANEL(utility)` recurses into utility's own routing (the authentic ISPF model). `_show_submenu`
  now dispatches leaves through the same `selection_targets` + `_run_selection` mechanism as the
  primary menu, instead of a hardcoded `leaves` dict.
- **PR 4+ (as needed) — `PGM PARM` passing and `CMD(...)` targets.** Add when a panel actually
  needs to pass parameters or invoke a command (nothing does yet).

## Verification strategy

PR 1 is behavior-neutral with an equivalence test (declared == hardcoded). PR 2 relies on the
existing menu integration tests plus a manual drive of options 1/3/6/7. This follows the repo's
"verify by executing the real flow, not by eyeballing" discipline.

## Risks & mitigations

- **Breaking the live ISPF menu (core product).** PR 1 is parse-only; PR 2 keeps behaviors
  byte-identical (registry = refactored branches), gated behind PR 1's equivalence test + the
  integration suite.
- **`)PROC` interpreter scope creep.** Parse *only* `ZSEL = TRANS(…)`; everything else stays
  ignored and documented.
- **IBM authenticity.** Use the real `ZSEL = TRANS(&ZCMD n,'PANEL(x)' …)` syntax, matching the
  reference (consistent with the matchval / DISPLAY / xlatl conformance work).

## Status

- [x] **PR 1** — parse `<source type=proc>` ZSEL/TRANS → `Screen.selection_targets`; `)PROC` in
  `ispf.dtl`; equivalence test asserting declared == hard-coded. Server dispatch unchanged;
  `ispf.dtl` byte-identical.
- [x] **PR 2** — handler registry `{name → behavior}` in `server.py`; the ISPF menu now
  dispatches through `screen.selection_targets` + `_run_selection` instead of the hardcoded
  `if/elif`. Behaviors unchanged (registry = refactored branches). Integration tests extended to
  cover the view (`PGM(view)`) and plain-submenu (`PANEL(foreground)`) handler paths.
- [x] **PR 3** — `_show_submenu` routes leaves through the sub-menu panel's own `)PROC`
  (`selection_targets` + `_run_selection`) instead of a hardcoded `leaves` dict; `utility.dtl`
  declares `1 → PGM(memberlist)`. Registry gains a `memberlist` handler. Behavior unchanged;
  the last hardcoded submenu routing is gone. Integration test covers the interactive
  utility → Library leaf.
- [ ] PR 4+ — `PGM PARM` passing / `CMD(...)` targets, when a panel needs them.

---

# Phase 2 — a general `)PROC` / `)INIT` interpreter

Phase 1 deliberately parses **one** idiom (`&ZSEL = TRANS(&ZCMD …)`) and treats the rest of
every `)PROC`/`)INIT` block as an inert no-op (`dtl.py:2377` `_emit_source`). That was the
right call for menu routing, but it leaves the rest of `#55` — the `<assign>` family and any
non-routing panel logic — unexpressible. Phase 2 replaces the single-idiom special-case with a
**small, bounded statement evaluator** for the ISPF panel-logic sublanguage, of which the
Phase 1 `ZSEL = TRANS` handling becomes one path.

This is explicitly **not** a general-purpose language: it is the fixed, well-documented ISPF
`)INIT`/`)PROC`/`)REINIT`/`)ABCINIT`/`)ABCPROC` grammar, nothing more. Scope is enumerated
below and anything outside it stays ignored-and-documented, exactly as today.

## Why this is the real remainder of #55

The DTL tags still marked `❌ #55` all *compile into* `)PROC`/`)INIT` statements — they are
surface syntax for the same evaluator:

| DTL tag | Compiles to | Evaluator feature needed |
|---|---|---|
| `<assignl destvar=X>` / `<assigni value= result=>` | `&X = TRANS(&src v,'r' …)` assignment | assignment + `TRANS` |
| `<source type=init>` | `)INIT` statements | assignment, control vars |
| `<source type=proc>` (beyond ZSEL) | `)PROC` statements | assignment, `VER`, `IF` |
| `<source type=reinit\|abcinit\|abcproc>` | the matching section | section dispatch |
| `<checkl>` / `<checki>` | `)PROC` `VER(&fld,…)` | `VER` validation |

So the assignment evaluator is the missing primitive; `<assignl>`/`<assigni>` are its first
consumer and close the last `❌ #55` display-relevant gap.

## The bounded grammar (what the evaluator will cover)

- **Assignment** — `&VAR = <expr>` where `<expr>` is a literal `'…'`, another `&VAR`, or a
  built-in call.
- **Built-ins** — `TRANS(source v,'r' … *,'d')` (the Phase 1 case, generalised to any destvar),
  `TRUNC(&VAR,'c'|n)` (splits into head + `.TRAIL`), `LENGTH`, `LVLINE`/`PACK` *(only if a
  panel needs them — add lazily)*.
- **Verify** — `VER(&VAR, NB | NONBLANK | NUMERIC | ALPHA | LIST v… | RANGE lo hi | PICT 'mask',
  MSG=id)` → drives field validation + a short message (reuses the `<checki>`/`<checkl>` path).
- **Conditionals** — `IF (&A = 'x') … ELSE …` over the above statement forms (no nesting beyond
  the reference's own examples initially).
- **Control variables** — `.CURSOR`, `.MSG`, `.ALARM`, `.RESP`, `.ATTR(...)` writes only
  (reads out of scope). These map onto the `Screen` mechanisms that already exist
  (`Screen.help_for` cursor, short-message overlay, alarm bit).
- **Sections** — run `)INIT` before first display, `)PROC` on Enter/PF submit, `)REINIT` on
  redisplay, `)ABCINIT`/`)ABCPROC` around action-bar pull-downs.

**Out of scope (stay ignored + documented):** `VGET`/`VPUT` profile/shared-pool I/O,
`&Z` system variables beyond the handful we already substitute, `PANELID`/`REFRESH`, arithmetic,
and any statement form with no host-display effect on a TN3270 *display* server.

## The missing primitive: a mutable variable pool

Phase 1 needs no state — it reads a static routing map. A general evaluator needs a **mutable
per-screen variable pool**:

1. `)INIT` **writes** the pool (declaratively populating what today is set Python-side before
   `_substitute`).
2. Display-time substitution **reads** the pool (today's `_substitute` / `&NAME` handling becomes
   a pool read).
3. `)PROC` **reads** modified input fields back into the pool, then evaluates — so it depends on
   the same field read-back service that `#249` introduces for `<lstfld>` input. **`)PROC`
   evaluation is gated on `#249`; `)INIT` assignment is not** (it runs before any input exists),
   so `)INIT` + `<assignl>` can land first.

## Incremental, non-breaking rollout

Every bundled panel today carries no `)PROC` beyond `ispf.dtl`/`utility.dtl`'s `ZSEL=TRANS`, so
each step below is **byte-identical** for the served panels until a panel is deliberately
converted — the same discipline as Phase 1 and the menu conversions.

- **PR A — variable pool + `)INIT` assignment (no `)PROC` dependency).** Introduce
  `Screen.vars` (the pool) and an `_eval_init` that executes `&VAR = 'literal' | &other |
  TRANS(…)` statements from `<source type=init>`, writing the pool *before* substitution.
  No bundled panel uses `)INIT` → byte-identical. Verify with a synthetic panel + a corpus
  `)INIT` example.
- **PR B — `<assignl>` / `<assigni>` → assignment (closes the `❌ #55` assign family).** Parse
  the tags into the same assignment the evaluator runs (`assignl destvar` + `assigni value→result`
  = a `TRANS` table). Reuses PR A's evaluator; adds the surface syntax. Closes the assign gap;
  corpus examples that use it now populate their target vars.
- **PR C — generalise `TRANS`/`TRUNC` + fold in ZSEL.** Rewrite `_emit_source`'s ZSEL special-case
  as `_eval_proc` producing `&ZSEL`, then read `selection_targets` from the resulting pool. Pure
  refactor — the equivalence test from Phase 1 PR 1 is the guard (declared map unchanged).
- **PR D — `VER` validation (gated by `#249`).** `)PROC` `VER(&fld,…)` and `<checkl>`/`<checki>`
  drive field validation + `.MSG`; needs the input read-back path (`#249`).
- **PR E — `IF/ELSE` + control-variable writes (`.CURSOR`/`.MSG`/`.ALARM`).** The last common
  reference constructs; add only the forms real corpus examples exercise.

## Verification strategy

Per repo discipline (verify by executing the real flow): each PR ships (a) a synthetic golden
panel exercising the new statement form, (b) any corpus example it unblocks moved from
`DTLError`/empty to a real render, and (c) for `)PROC`/`VER` paths, a live ws3270 drive of a
converted panel. A byte-identity render-SHA diff against the current baseline gates every
not-yet-converted bundled panel.

## Risks & mitigations

- **Interpreter scope creep** — the grammar above is a *closed enumeration*; anything not listed
  stays ignored-and-documented (same contract as Phase 1). New forms are added only when a real
  corpus/bundled panel needs them, never speculatively.
- **State model regression** — the pool subsumes today's ad-hoc `_substitute` inputs; PR A must
  keep substitution byte-identical for every existing panel (they populate vars Python-side; the
  pool is initially fed the same values).
- **`)PROC` needs input read-back** — explicitly sequenced *after* `#249`; `)INIT` + `<assignl>`
  (PRs A–C) have no such dependency and deliver the `#55` assign closure first.

## Status

- [ ] PR A — `Screen.vars` pool + `)INIT` assignment evaluator (no `)PROC` dep).
- [ ] PR B — `<assignl>`/`<assigni>` assignment (closes the `#55` assign family).
- [ ] PR C — generalise `TRANS`/`TRUNC`; fold Phase 1 ZSEL into `_eval_proc` (equivalence-gated).
- [ ] PR D — `VER` validation + `<checkl>`/`<checki>` (gated by `#249`).
- [ ] PR E — `IF/ELSE` + `.CURSOR`/`.MSG`/`.ALARM` control-variable writes.
