# Dialog Tag Language (DTL) tag reference

An alphabetic summary of the z/OS ISPF **Dialog Tag Language (DTL)** tags, with this server's parser (`dtl.py`) support status for each. It mirrors IBM's [Dialog Tag Language (DTL) tags](https://www.ibm.com/docs/en/zos/3.2.0?topic=reference-dialog-tag-language-dtl-tags) *Table 1. Tag summary* (the authoritative source; full attribute value syntax lives there).

**Support:** ✅ 44 supported · 🟡 3 partial · ❌ 49 unimplemented (of 96 tags). Unimplemented tags link to their tracking issue.

Legend: **End tag** — whether a closing tag is required. *Italic* entries in **Attributes** are the tag's text content. `(body text tags)` abbreviates the recurring help-body set (ATTENTION, CAUTION, DD, FIG, INFO, LI, LINES, LP, NT, PD, WARNING, XMP).

| Tag | End tag | Attributes | Nested tags | Used within | Parser support |
|-----|:-------:|------------|-------------|-------------|----------------|
| **AB** | Yes | MNEMGEN, ABSEPSTR, ABSEPCHAR | ABC | PANEL | ✅ Supported |
| **ABC** | No | HELP, PDCVAR, *choice-description-text* | COMMENT, M, PDC, PDSEP, SOURCE | AB | ✅ Supported |
| **ACTION** | No | RUN, PARM, APPLCMD, TYPE, NEWAPPL, NEWWINDOW, PASSLIB, NEWPOOL, SUSPEND, SCRNAME, NOCHECK, ADDPOP, OPT, MODE, LANG, BARRIER, NEST, SETVAR, VALUE, TOGVAR, VALUE1, VALUE2 | — | CHOICE, PDC | ✅ Supported |
| **AREA** | Yes | MARGINW, MARGIND, INDENT, DEPTH, EXTEND, DIV, DIVWIDTH, FORMAT, TEXT, WIDTH, DIR | COMMENT, DA, DIVIDER, DTACOL, DTAFLD, GA, GENERATE, GRPHDR, INFO, LSTFLD, PNLINST, REGION, SELFLD, SOURCE | HELP, PANEL | ✅ Supported |
| **ASSIGNI** | No | VALUE, RESULT | — | ASSIGNL | ❌ #55 |
| **ASSIGNL** | Yes | DESTVAR | ASSIGNI | DTAFLD | ❌ #55 |
| **ATTENTION** | Yes | *text* | DL, FIG, HP, LINES, NOTE, NOTEL, NT, OL, P, PARML, PS, RP, SL, UL, XMP | LI, LP, P | ❌ #116 |
| **ATTR** | No | ATTRCHAR, TYPE, INTENS, CAPS, JUST, PAD, PADC, SKIP, GE, COLOR, HILITE, NUMERIC, FORMAT, OUTLINE, PAS, CKBOX, CUADYN, CSRGRP, ATTN | — | DA | ✅ Supported |
| **BOTINST** | No | COMPACT, *instruction-text* | HP, PS, RP | PANEL | ❌ #117 |
| **CAUTION** | Yes | *text* | DL, FIG, HP, LINES, NOTE, NOTEL, NT, OL, P, PARML, PS, RP, SL, UL, XMP | LI, LP, P | ❌ #116 |
| **CHDIV** | No | TYPE, GUTTER, FORMAT, *divider-text* | HP | SELFLD, CHOICE | ❌ #53 |
| **CHECKI** | No | TYPE (RANGE, ALPHA, CHARS, VALUES, PICT, NAME, DSNAME, IDATE, …), PARM1–3 | — | CHECKL | 🟡 Partial — only RANGE and VALUES implemented — #62 |
| **CHECKL** | Yes | MSG | CHECKI | VARCLASS | ✅ Supported |
| **CHOFLD** | No | DATAVAR, VARCLASS, HELP, USAGE, REQUIRED, MSG, AUTOTAB, ENTWIDTH, FLDSPACE, ALIGN, DISPLAY, NOENDATTR, PAD, PADC, OUTLINE, PSVAR, PSVAL, PAS, EXPAND, ATTRCHANGE, INIT, IMAPNAME, PLACE, ATTRCHAR, CAPS, *choice-description-text* | ACTION, COMMENT, HP, PS, RP, SOURCE | CHOICE | ❌ #115 |
| **CHOICE** | No | NAME, HELP, CHECKVAR, MATCH, NOMATCH, AUTOTAB, SELCHAR, PAD, PADC, OUTLINE, HIDE, HIDEX, UNAVAIL, UNAVAILMAT, TRUNC, AUTOSEL, *choice-description-text* | ACTION, CHOFLD, COMMENT, HP, PS, RP, SOURCE | SELFLD | ✅ Supported |
| **CMD** | No | NAME, ALTDESCR, *external-command-name* | CMDACT, T | CMDTBL | ✅ Supported |
| **CMDACT** | No | ACTION | — | CMD | ✅ Supported |
| **CMDAREA** | No | HELP, PMTLOC, NOINIT, PAD, PADC, OUTLINE, NAME, ENTWIDTH, PMTTEXT, CMDLOC, CMDLEN, AUTOTAB, SCROLLVAR, SCRVHELP, SCROLLTAB, SCRCAPS, PSBUTTON, PSVAR, PSVAL, IMAPNAME, PLACE, CAPS, NOJUMP, VARDCL, *command-prompt-text* | HP | PANEL | ✅ Supported |
| **CMDTBL** | Yes | APPLID, SORT | CMD | — | ✅ Supported |
| **COMMENT** | No | TYPE (END, INIT, PROC, REINIT, HELP, …), *comment-text* | — | ABC, AREA, CHOICE, DA, DTACOL, DTAFLD, HELP, LSTCOL, LSTFLD, LSTGRP, MSGMBR, PANEL, PDC, REGION, SELFLD | ❌ #119 |
| **COMPOPT** | No | REPLACE, SCREEN, DBCS, PANEL, ACTBAR, GUI, … (compiler flags), *national-language* | None | — | ❌ #119 |
| **COPYR** | No | *copyright-text* | — | — | ❌ #119 |
| **DA** | Yes | NAME, EXTEND, LVLINE, SCROLL, USERMOD, DATAMOD, DEPTH, WIDTH, SHADOW, DIV, FORMAT, TEXT, SCROLLVAR, SCRVHELP, SCROLLTAB, SCRCAPS, INITATTR, HELP | ATTR, COMMENT, SOURCE | AREA, PANEL, REGION | ✅ Supported |
| **DD** | No | *definition-description* | DL, FIG, HP, LINES, NOTE, NOTEL, NT, OL, P, PARML, PS, RP, SL, UL, XMP | DL | ✅ Supported |
| **DDHD** | No | *definition-description-header* | HP, PS, RP | DL | ❌ #120 |
| **DIVIDER** | No | TYPE, GAP, GUTTER, NOENDATTR, FORMAT, *divider-text* | HP | AREA, DTACOL, PANEL, REGION | ✅ Supported |
| **DL** | Yes | TSIZE, BREAK, COMPACT, NOSKIP, INDENT, FORMAT, DIVEND, SPLIT | DD, DDHD, DLDIV, DT, DTHD, DTDIV, DTHDIV | ATTENTION, CAUTION, DD, FIG, INFO, LI, LINES, LP, NT, PD, WARNING, XMP | ✅ Supported |
| **DLDIV** | No | TYPE, GAP, GUTTER, FORMAT, *divider-text* | HP | DL | ❌ #120 |
| **DT** | No | FORMAT, NOSKIP, SPLIT, *definition-term* | DTSEG, HP, PS, RP | DL | ✅ Supported |
| **DTACOL** | Yes | PMTWIDTH, ENTWIDTH, DESWIDTH, SELWIDTH, FLDSPACE, PAD, PADC, OUTLINE, PMTFMT, AUTOTAB, ATTRCHANGE, PMTLOC, DBALIGN, VARCLASS, REQUIRED, CAPS, VARDCL | COMMENT, DIVIDER, DTAFLD, GRPHDR, SELFLD, SOURCE | AREA, PANEL, REGION | ✅ Supported |
| **DTAFLD** | No | NAME, DATAVAR, VARCLASS, HELP, USAGE, REQUIRED, MSG, AUTOTAB, ENTWIDTH, PMTWIDTH, DESWIDTH, ALIGN, PMTLOC, DISPLAY, PAD, OUTLINE, PMTFMT, PSVAR, PSVAL, PAS, CSRGRP, EXPAND, INIT, DEPTH, IMAPNAME, DBALIGN, FLDTYPE, COLOR, INTENS, HILITE, ATTRCHAR, CAPS, AUTOTYPE, VARDCL, *prompt-text* (~40) | ASSIGNL, COMMENT, DTAFLDD, HP, PS, RP, SOURCE, SCRFLD | AREA, DTACOL, PANEL, REGION | ✅ Supported |
| **DTAFLDD** | No | *description* | HP, PS, RP | DTAFLD | ✅ Supported |
| **DTDIV** | No | — | — | DL | ❌ #120 |
| **DTHD** | No | *definition-term-header* | HP, PS, RP | DL | ❌ #120 |
| **DTHDIV** | No | — | — | DL | ❌ #120 |
| **DTSEG** | No | — | — | DT | ❌ #120 |
| **FIG** | Yes | FRAME, WIDTH, NOSKIP, *figure-content* | DL, FIGCAP, HP, NOTE, NOTEL, NT, OL, P, PARML, PS, RP, SL, UL, XMP | ATTENTION, CAUTION, DD, INFO, LI, LP, NT, PD, WARNING | ❌ #52 |
| **FIGCAP** | No | *figure-caption-text* | HP, PS, RP | FIG | ❌ #52 |
| **GA** | No | NAME, EXTEND, DEPTH, WIDTH, DIV, FORMAT, TEXT, LVLINE | — | AREA, PANEL, REGION | ❌ #117 |
| **GENERATE** | Yes | SUBSTITUTE | ATTR, COMMENT, SOURCE | AREA, HELP, PANEL, REGION | ❌ #119 |
| **GRPHDR** | No | FORMAT, WIDTH, FMTWIDTH, INDENT, HEADLINE, DIV, DIVLOC, COMPACT, STRIP, *group-heading-text* | HP, PS, RP | AREA, DTACOL, PANEL, REGION | ❌ #53 |
| **HELP** | Yes | NAME, HELP, HELPDEF, WIDTH, DEPTH, CCSID, TUTOR, KEYLIST, APPLID, EXPAND, WINTITLE, APPTITLE, MERGESAREA, MSGLINE, IMAPNAME, ZUP, ZCONT, *help-panel-title* | AREA, COMMENT, DIVIDER, GENERATE, HP, INFO, REGION, SOURCE, TEXTLINE | — | ✅ Supported |
| **HELPDEF** | No | ID, HELP, WIDTH, DEPTH, CCSID, KEYLIST, APPLID, EXPAND, WINTITLE, APPTITLE, MERGESAREA, IMAPNAME | — | — | ❌ #54 |
| **H1** | No | COMPACT, *heading-text* | — | INFO | ❌ #52 |
| **H2/H3/H4** | No | COMPACT, *heading-text* | HP, PS, RP | INFO | ❌ #52 |
| **HP** | Yes | TYPE, COLOR, INTENS, HILITE, INTENSE, *phrase-to-be-highlighted* | — | (most text-bearing tags) | ❌ #111 |
| **INFO** | Yes | WIDTH, INDENT | DIVIDER, DL, FIG, Hn, LINES, NOTE, NOTEL, NT, OL, P, PARML, SL, SOURCE, UL, XMP | AREA, HELP, PANEL, REGION | ✅ Supported |
| **KEYI** | No | KEY, CMD, CASE, FKA, PARM, *FKA-text* | — | KEYL | ✅ Supported |
| **KEYL** | Yes | NAME, HELP, ACTION, APPLID | KEYI | — | ✅ Supported |
| **LI** | No | SPACE, NOSKIP, *item-text* | (body text tags) | NOTEL, OL, SL, UL | ✅ Supported |
| **LINES** | Yes | NOSKIP, *text* | DL, HP, NOTE, NOTEL, NT, OL, P, PARML, PS, RP, SL, UL, XMP | ATTENTION, CAUTION, DD, INFO, LI, LP, NT, PD, WARNING | ✅ Supported |
| **LIT** | Yes | *literal-display-value* | — | XLATI | ❌ #114 |
| **LP** | No | NOSKIP, *implied-paragraph* | (body text tags) | NOTEL, OL, SL, UL | ✅ Supported |
| **LSTCOL** | No | DATAVAR, VARCLASS, HELP, USAGE, REQUIRED, MSG, COLWIDTH, ALIGN, AUTOTAB, LINE, POSITION, FORMAT, TEXT, PAD, OUTLINE, PAS, CSRGRP, COLTYPE, COLOR, INTENS, HILITE, CAPS, DISPLAY, VARDCL, *column-heading* | COMMENT, HP, PS, RP, SOURCE, SCRFLD | LSTFLD, LSTGRP | ✅ Supported |
| **LSTFLD** | Yes | RULES, ROWS, DIV, SCROLLVAR, SCRVHELP, SCROLLTAB, SCRCAPS, ATTRCHANGE, VARDCL | COMMENT, LSTCOL, LSTGRP, LSTVAR, SOURCE | AREA, PANEL, REGION | 🟡 Partial — model rows/scroll not modelled — #67 |
| **LSTGRP** | Yes | HEADLINE, ALIGN, *column-group-heading* | COMMENT, HP, LSTCOL, LSTGRP, LSTVAR, PS, RP, SOURCE | LSTFLD, LSTGRP | ✅ Supported |
| **LSTVAR** | No | DATAVAR, LINE, *column-heading* | COMMENT, HP, PS, RP, SOURCE | LSTFLD, LSTGRP | ❌ #53 |
| **M** | No | *mnemonic-character* | — | ABC, PDC | ❌ #118 |
| **MSG** | No | SUFFIX, HELP, MSGTYPE, LOCATION, DISP, ALARM, ABBREV, FORMAT, SMSG, *message-text* | VARSUB | MSGMBR | ✅ Supported |
| **MSGMBR** | Yes | NAME, CCSID, WIDTH | COMMENT, MSG | — | ✅ Supported |
| **NOTE** | No | NOSKIP, INDENT, TYPE, COLOR, INTENS, HILITE, TEXT, *note-text* | HP, PS, RP | (body text tags) | ❌ #116 |
| **NOTEL** | Yes | COMPACT, NOSKIP, SPACE, INDENT, TYPE, COLOR, INTENS, HILITE, TEXT | LI, LP | (body text tags) | ❌ #116 |
| **NT** | Yes | NOSKIP, INDENT, TYPE, COLOR, INTENS, HILITE, TEXT, *note-text* | DL, FIG, HP, LINES, OL, P, PARML, PS, RP, SL, UL, XMP | (body text tags) | ❌ #116 |
| **OL** | Yes | COMPACT, NOSKIP, SPACE, INDENT, TEXT | LI, LP | (body text tags) | ✅ Supported |
| **P** | No | COMPACT, INTENSE, INDENT, OFFSET, SPACE, *paragraph-text* | ATTENTION, CAUTION, HP, PS, RP, WARNING | (body text tags) | ✅ Supported |
| **PANDEF** | No | ID, HELP, DEPTH, WIDTH, KEYLIST, CCSID, WINDOW, WINTITLE, APPTITLE, PAD, OUTLINE, EXPAND, MERGESAREA, ENTKEYTEXT, IMAPNAME, TMARGIN, BMARGIN | — | — | ❌ #117 |
| **PANEL** | Yes | NAME, HELP, PANDEF, DEPTH, WIDTH, KEYLIST, CURSOR, CCSID, MENU, PRIME, TUTOR, WINDOW, WINTITLE, PAD, OUTLINE, EXPAND, MSGLINE, TITLINE, CMDLINE, TYPE, ACTBAR, ENTKEYTEXT, IMAPNAME, TMARGIN, BMARGIN, ERRORCHECK, ZUP, ZCONT, *panel-title-text* (~45) | AB, AREA, BOTINST, CMDAREA, COMMENT, DA, DIVIDER, DTACOL, DTAFLD, GA, GENERATE, GRPHDR, HP, INFO, LSTFLD, PNLINST, REGION, SELFLD, SOURCE, TEXTLINE, TOPINST | — | ✅ Supported |
| **PARML** | Yes | TSIZE, BREAK, COMPACT, SKIP, INDENT, FORMAT, DIVEND, SPLIT | PLDIV, PT, PTDIV, PD | (body text tags) | ✅ Supported |
| **PD** | No | *parameter-description* | (body text tags) | PARML | ✅ Supported |
| **PDC** | No | HELP, UNAVAIL, CHECKVAR, MATCH, ACC1, ACC2, ACC3, *pull-down-description-text* | ACTION, COMMENT, M, SOURCE | ABC | ✅ Supported |
| **PDSEP** | No | — | — | PDC | ❌ #118 |
| **PLDIV** | No | TYPE, GAP, GUTTER, FORMAT, *divider-text* | HP | PARML | ❌ #120 |
| **PNLINST** | No | COMPACT, *instruction-text* | HP, PS, RP | AREA, REGION, PANEL | ❌ #113 (bug: parser has `paninst`) |
| **PS** | Yes | VAR, VALUE, CSRGRP, DEPTH, IMAPNAME, PLACE, *point-and-shoot-text* | — | (most text-bearing tags) | ❌ #115 |
| **PT** | No | FORMAT, NOSKIP, SPLIT, *parameter-term* | HP, PS, PTSEG, RP | PARML | ✅ Supported |
| **PTDIV** | No | — | — | PARML | ❌ #120 |
| **PTSEG** | No | — | — | PT | ❌ #120 |
| **REGION** | Yes | DIR, INDENT, WIDTH, DEPTH, EXTEND, ALIGN, GRPBOX, GRPWIDTH, GRPBXVAR, GRPBXMAT, LOCATION, *group-box-title* | COMMENT, DA, DIVIDER, DTACOL, DTAFLD, GA, GENERATE, GRPHDR, INFO, LSTFLD, PNLINST, REGION, SELFLD, SOURCE | AREA, HELP, PANEL, REGION | ✅ Supported |
| **RP** | Yes | HELP, *reference-phrase* | — | (most text-bearing tags) | ❌ #118 |
| **SCRFLD** | Yes | DISPLEN, INDVAR, INDVAL, LINDVAR, RINDVAR, SINDVAR, LCOLIND, RCOLIND, SCALE, SCROLL, FLDSPOS | COMMENT, SOURCE | DTAFLD, LSTCOL | ❌ #115 |
| **SELFLD** | Yes | NAME, HELP, TYPE, PMTLOC, PMTWIDTH, SELWIDTH, ENTWIDTH, REQUIRED, MSG, FCHOICE, AUTOTAB, DEPTH, TRAIL, CHOICECOLS, PAD, OUTLINE, SELMSG, INIT, VERIFY, SELFMT, CHKBOX, CSRGRP, LISTTYPE, DBALIGN, NOSEL, FLDTYPE, COLOR, INTENS, HILITE, VARDCL, *field-prompt-text* | CHDIV, CHOICE, COMMENT, HP, PS, RP, SOURCE | AREA, DTACOL, PANEL, REGION | ✅ Supported |
| **SL** | Yes | COMPACT, NOSKIP, SPACE, INDENT, TEXT | LI, LP | (body text tags) | 🟡 Partial — transparent container; LI renders, SL packing not modelled |
| **SOURCE** | Yes | TYPE (PROC, INIT, REINIT, ABCINIT, ABCPROC), *text* | — | ABC, AREA, CHOICE, DA, DTACOL, DTAFLD, HELP, LSTCOL, LSTFLD, LSTGRP, PANEL, PDC, REGION, SELFLD | ❌ #119 |
| **T** | No | — | — | CMD | ❌ #118 |
| **TEXTLINE** | Yes | — | DTAFLD, TEXTSEG | HELP, PANEL | ❌ #117 |
| **TEXTSEG** | No | EXPAND, WIDTH, *text* | HP | TEXTLINE | ❌ #117 |
| **TOPINST** | No | COMPACT, *instruction-text* | HP, PS, RP | PANEL | ✅ Supported |
| **UL** | Yes | COMPACT, NOSKIP, SPACE, INDENT, TEXT | LI, LP | (body text tags) | ✅ Supported |
| **VARCLASS** | No | NAME, TYPE, MSG | CHECKL, XLATL | — | ✅ Supported |
| **VARDCL** | No | NAME, VARCLASS | — | VARLIST | ✅ Supported |
| **VARLIST** | Yes | — | VARDCL | — | ✅ Supported |
| **VARSUB** | No | VAR | — | MSG | ❌ #118 |
| **WARNING** | Yes | *text* | DL, FIG, HP, LINES, NOTE, NOTEL, NT, OL, P, PARML, PS, RP, SL, UL, XMP | LI, LP, P | ❌ #116 |
| **XLATI** | No | VALUE, *displayed-value* | LIT | XLATL | ❌ #114 |
| **XLATL** | Yes | FORMAT, TRUNC, MSG | XLATI | VARCLASS | ❌ #114 |
| **XMP** | Yes | NOSKIP, *text* | DL, HP, NOTE, NOTEL, NT, OL, P, PARML, PS, RP, SL, UL | (body text tags) | ❌ #52 |

## Notes

- **"Supported" means the tag renders**, not full attribute coverage. Several supported tags accept dozens of attributes (e.g. `DTAFLD` ~40, `PANEL` ~45, `SELFLD` ~30) of which only a subset is implemented.
- Tracking issues by area: value translation [#114](https://github.com/bushidocodes/3270-server/issues/114); field types `PS`/`CHOFLD`/`SCRFLD` [#115](https://github.com/bushidocodes/3270-server/issues/115); admonitions/notes [#116](https://github.com/bushidocodes/3270-server/issues/116); panel-structure [#117](https://github.com/bushidocodes/3270-server/issues/117); small structural tags [#118](https://github.com/bushidocodes/3270-server/issues/118); build/document directives [#119](https://github.com/bushidocodes/3270-server/issues/119); `DL`/`PARML` sub-elements [#120](https://github.com/bushidocodes/3270-server/issues/120); the `PNLINST` bug [#113](https://github.com/bushidocodes/3270-server/issues/113). Pre-existing: `<hp>` [#111](https://github.com/bushidocodes/3270-server/issues/111), body/text tags [#52](https://github.com/bushidocodes/3270-server/issues/52), list/table fields [#53](https://github.com/bushidocodes/3270-server/issues/53), help/`helpdef` [#54](https://github.com/bushidocodes/3270-server/issues/54), assignments [#55](https://github.com/bushidocodes/3270-server/issues/55), `checki` types [#62](https://github.com/bushidocodes/3270-server/issues/62), `lstfld` rows [#67](https://github.com/bushidocodes/3270-server/issues/67).
