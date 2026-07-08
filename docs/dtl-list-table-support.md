# DTL list/table fields — coverage summary

*Closure record for the umbrella [#53](https://github.com/bushidocodes/3270-server/issues/53),
"Support DTL list/table fields (`<lstfld>`/`<lstcol>`/`<lstgrp>`)".*

The list/table capability is now supported end-to-end. #53 was split into focused
child issues as the work landed; this is the map of what each delivered.

## Delivered

| Area | Tag(s) / attribute(s) | Where | Issue |
|---|---|---|---|
| Model data rows | `<lstfld>` populated from `rows=` | `load_dtl` / `_emit_lstfld_rows` | [#67](https://github.com/bushidocodes/3270-server/issues/67) |
| Columns + headings | `<lstcol>` DATAVAR/COLWIDTH/USAGE/ALIGN/LINE/FORMAT/COLOR/INTENS/HILITE/TEXT/PAD/OUTLINE/DISPLAY | `_add_lstcol` / `_emit_lstfld` | [#122](https://github.com/bushidocodes/3270-server/issues/122) |
| Group headings | `<lstgrp>` HEADLINE + ALIGN, nesting | `_emit_lstfld` | [#123](https://github.com/bushidocodes/3270-server/issues/123) |
| Table-input read-back | per-row cell identity; `Screen.read_table_rows` + a served consumer | `screen.py` / `server._show_table_input` | [#249](https://github.com/bushidocodes/3270-server/issues/249) |
| Uppercase input column | `<lstcol CAPS=ON>` folded on read-back | `screen.read_table_rows` | [#238](https://github.com/bushidocodes/3270-server/issues/238) |
| Per-row required input | `<lstcol REQUIRED=YES MSG=id>` validated on submit | `screen.table_required_errors` | [#236](https://github.com/bushidocodes/3270-server/issues/236) |
| Scrolling / paging | offset-aware `ROW x TO y OF z` + `BOTTOM OF DATA`; PF7/PF8 + SCROLL amount | `dtl.py` / `server._show_member_list` | [#281](https://github.com/bushidocodes/3270-server/issues/281) |
| Choice divider | `<chdiv>` SOLID/DASH rule, TEXT caption, NONE spacer | `_emit_chdiv` | this PR |

## Accepted but with no display effect

- **`<lstvar>`** — declares a *non-displayed* model variable in a `<lstfld>`. There
  is no column to render in a display server, so it is accepted and produces no
  output (a table with an `<lstvar>` renders byte-for-byte like one without).
- The **codegen-only / DBCS-only** `<lstfld>`/`<lstcol>`/`<lstgrp>` attributes
  (`RULES`, `ROWS=SCAN`, `ATTRCHANGE`, `VARDCL`, `CLEAR`, `COLTYPE`, `PAS`/`CSRGRP`,
  the DBCS `OUTLINE`) — see
  [dtl-lstfld-out-of-scope.md](dtl-lstfld-out-of-scope.md)
  ([#240](https://github.com/bushidocodes/3270-server/issues/240)) and
  [dbcs-support-decision.md](dbcs-support-decision.md)
  ([#135](https://github.com/bushidocodes/3270-server/issues/135)).

## Verification

The server-visible pieces were verified on a **real ws3270**: table-input read-back
(#249), CAPS folding (#238), REQUIRED/MSG (#236), and member-list + help paging
(#281). The pure-rendering tags (`<lstcol>`/`<lstgrp>`/`<chdiv>`) are pinned by
exact render assertions in `test_dtl.py`, and the guide corpus in
`test_dtl_examples.py` exercises the reference `<lstfld>`/`<chdiv>` examples.
