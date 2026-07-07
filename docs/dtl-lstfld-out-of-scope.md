# `<lstfld>` / `<lstcol>` / `<lstgrp>` attributes that are out of scope

*Decision record for [#240](https://github.com/bushidocodes/3270-server/issues/240)
(split from the list/table umbrella [#53](https://github.com/bushidocodes/3270-server/issues/53)).*

This is a **TN3270 display server**: it renders DTL panels straight to a 3270 data
stream. It does **not** emit ISPF panel-definition sections — there is no generated
`)ATTR`, `)MODEL`, `)PROC`, or `)INIT`. So a `<lstfld>` / `<lstcol>` / `<lstgrp>`
attribute whose *only* effect in real ISPF is to tune those generated sections has
**no host-display effect here** and is intentionally a no-op.

The parser **accepts and ignores** these attributes (it does not reject unknown
keywords), so a panel that uses them loads and renders — the attribute simply does
nothing. What *does* render (COLOR/INTENS/HILITE, COLWIDTH, USAGE, ALIGN, LINE,
FORMAT, TEXT, PAD, OUTLINE box lines, and — after
[#238](https://github.com/bushidocodes/3270-server/issues/238) /
[#236](https://github.com/bushidocodes/3270-server/issues/236) — CAPS and
REQUIRED/MSG) is covered by the umbrella and its siblings.

## Attributes with no display effect in this server

| Element | Attribute | Why it is a no-op here |
|---|---|---|
| LSTFLD | **`RULES`** | *"only visible on double-byte character support terminals."* Belongs with DBCS support — [#135](https://github.com/bushidocodes/3270-server/issues/135). |
| LSTCOL | **`OUTLINE`** (DBCS box) | Same double-byte-only visibility caveat as `RULES`; the single-byte OUTLINE box lines we *do* draw are unaffected. DBCS — [#135](https://github.com/bushidocodes/3270-server/issues/135). |
| LSTFLD | **`ROWS=SCAN`** | Adds `ROWS(SCAN)` to the generated `)MODEL` line (TBSARG pre-selected rows). We populate the model rows directly from the `rows=` argument, so there is no `)MODEL` to tune. |
| LSTFLD / LSTCOL | **`ATTRCHANGE`** | Controls how the ISPDTLC compiler *shares* `)ATTR` entries. We emit no `)ATTR`. |
| LSTFLD / LSTCOL | **`VARDCL`** | A compile-time check that the dialog variable is declared. There is no compile step. |
| LSTCOL | **`CLEAR`** | Adds `CLEAR(var)` to the generated `)MODEL`. No `)MODEL` here. |
| LSTCOL | **`COLTYPE`** | Selects the `)ATTR` `TYPE` code. We apply COLOR/INTENS directly; the specialised EE/VOI/LID types have no plain-3270 rendering. |
| LSTCOL | **`PAS` / `CSRGRP`** | Point-and-shoot `)ATTR`/`)INIT` codegen. The server already does point-and-shoot by mapping the cursor row to the model row (see `_show_member_list`; also [#115](https://github.com/bushidocodes/3270-server/issues/115)), so the generated attributes are not needed. |

## Decision

**No implementation is planned** for the attributes above unless/until the server
grows either (a) an ISPF panel-definition **codegen** path (which would give
`)ATTR`/`)MODEL`/`)PROC` something to tune), or (b) **DBCS** support
([#135](https://github.com/bushidocodes/3270-server/issues/135), for `RULES` and the
double-byte `OUTLINE`). This record exists so the coverage gap is *documented as a
deliberate scope boundary*, not silently dropped.

Accepting-and-ignoring (rather than rejecting) these keywords is verified by
`test_dtl.py::test_lstfld_out_of_scope_attributes_are_accepted_and_ignored`.
