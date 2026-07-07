# DBCS (double-byte / CJK) support — deferral decision

*Decision record for [#135](https://github.com/bushidocodes/3270-server/issues/135)
(carved out of the code-pages / DBCS / Graphic-Escape umbrella
[#101](https://github.com/bushidocodes/3270-server/issues/101)).*

## Status: **deferred — not implemented**

DBCS (double-byte, e.g. CJK) support is **intentionally not implemented**, because
it **cannot be verified** on the toolchain this project builds and tests against.
This is a deliberate application of the repo's core discipline — *verify by a real
emulator, never eyeball* — not an oversight.

## What DBCS would require

- **Shift-Out / Shift-In** framing: `SO` (0x0E) enters a double-byte subfield within
  a field, `SI` (0x0F) leaves it. The server would have to emit correct SO/SI
  framing and **even-length** double-byte runs.
- **A DBCS code page** (a CJK CCSID) for the double-byte data, layered on the
  per-session single-byte code page — which is itself
  [#134](https://github.com/bushidocodes/3270-server/issues/134) and a prerequisite.
- **DBCS-aware order handling**: SBA/RA/EUA and Graphic Escape all special-case
  double-byte data on the emulator side (x3270 `ctlr.c` carries `/* XXX: DBCS? */`
  for GE), so the server must match that framing.
- **Query Reply character-set discovery**: confirm the terminal is DBCS-capable
  (the character-sets Query Reply) *before* sending any double-byte data.

## Why it cannot be verified here

Confirming DBCS end-to-end needs **both** a DBCS-capable emulator build **and** CJK
fonts. The local `ws3270` / CI `s3270` setup renders **single-byte Latin only**:

- there is no DBCS-capable emulator on this machine to drive in a smoke test, and
- even if there were, the screen-scrape (`Ascii()`) and protocol-trace assertions
  the smoke tests rely on could not distinguish correct from incorrect double-byte
  rendering without CJK glyph support.

Per the repo rule, DBCS must **not** be implemented blind (an unverifiable feature
that "looks right" is exactly what the discipline exists to prevent — a plausible
but wrong data stream would pass an eyeball and fail a real DBCS terminal).

## What already exists (single-byte, verified)

- **Graphic Escape** (single-byte alternate character set, box/rule glyphs) is done
  and verified.
- **Per-session / per-field code page** parameterization is
  [#134](https://github.com/bushidocodes/3270-server/issues/134) (single-byte).
- `<attr FORMAT=DBCS>` and `<checki TYPE=DBCS>` are **parsed and recorded** but have
  no TN3270 display effect (noted as such in the DTL tags reference).

## Decision & the bar for revisiting

**No implementation is planned** until a DBCS-capable verification path exists — a
DBCS emulator build plus CJK fonts wired into the smoke-test harness — **or** the
verification requirement is explicitly waived by the maintainer. All bundled panels
are English single-byte, so this is niche (as #101 noted). When that path exists,
this record is the starting checklist (SO/SI, a DBCS CCSID over #134, DBCS-aware
orders, and the character-sets Query Reply gate).
