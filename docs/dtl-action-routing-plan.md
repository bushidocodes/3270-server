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
- **PR 3+ — extend & converge.** Give the submenu panels their own `)PROC` so `PANEL(utility)`
  recurses into utility's own routing (the authentic ISPF model), then handle `PGM PARM`
  passing and `CMD(...)` targets.

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
- [ ] PR 2 — handler registry; dispatch through `selection_targets`.
- [ ] PR 3+ — submenu `)PROC`; `PGM PARM`; `CMD`.
