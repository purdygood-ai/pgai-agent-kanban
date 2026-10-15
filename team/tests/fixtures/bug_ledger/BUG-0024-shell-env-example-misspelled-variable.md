# BUG-0024-shell-env-example-misspelled-variable

**Filed By:** CODER (reconstructed historical record) — restored for
CODER-20260907-002-bug-ledger-fixture after the original bug file behind
KI-1.0.0.2 was recycled/archived from the live bugs ledger following its
resolution, which is what caused the CHANGELOG writer's KI-1.0.0.2
disclosure to drop out of regeneration (the writer projects Known Issues
from live BUG-*.md files in bugs_dir, not from the checked-in CHANGELOG.md
or release-notes text, so a recycled bug file permanently drops its
disclosure on the next regeneration). The Symptom / Affects / Public ID
values below are taken verbatim from the last CHANGELOG.md commit that
still carried this disclosure (`git show c2d8056:CHANGELOG.md`,
"pgai-agent-kanban v1.27.0"); the Fixed In value uses the published
"fixed in v1.23.0" disclosure directly, since no more precise internal fix
version is on record for this reconstruction. This file is a
reconstruction of lost source data for test fixture purposes, not a newly
observed defect.
**Date:** 2026-09-07
**Severity:** low

## Status
done


## Category
docs

---

## Symptom
`shell-env_example` line 34 references a misspelled variable in the
commented-out convenience export: `$PGA_AGENT_KANBAN_ROOT_PATH` (missing
the `I`) instead of `$PGAI_AGENT_KANBAN_ROOT_PATH` (the correct variable,
defined one line above on line 33).

## Expected
The commented-out convenience export in `shell-env_example` references the
correctly spelled `$PGAI_AGENT_KANBAN_ROOT_PATH` variable.

## Actual
The commented-out line reads `$PGA_AGENT_KANBAN_ROOT_PATH`, a misspelling
missing the `I`, so an operator who uncomments it for convenience gets an
unset/empty variable instead of the intended value.

## Reproduction
Steps to reproduce (historical, at v1.0.0):

1. Open `shell-env_example` and inspect line 34.
2. Compare against the correctly spelled variable defined on line 33.

## Files Involved

- `shell-env_example`

---

## Affects
v1.0.0

## Fixed In
v1.23.0

## Public ID
KI-1.0.0.2
