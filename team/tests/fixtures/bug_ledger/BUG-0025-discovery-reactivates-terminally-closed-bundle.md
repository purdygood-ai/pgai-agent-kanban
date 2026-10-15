# BUG-0025-discovery-reactivates-terminally-closed-bundle

**Filed By:** CODER (reconstructed historical record) — restored for
CODER-20260907-002-bug-ledger-fixture after the original bug file behind
KI-1.0.0.3 was recycled/archived from the live bugs ledger following its
resolution, which is what caused the CHANGELOG writer's KI-1.0.0.3
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
**Severity:** medium

## Status
done


## Category
bugfix

---

## Symptom
Discovery/PM re-activates a requirements bundle that the operator has
terminally closed, overwriting its terminal state.

## Expected
Once an operator has terminally closed a requirements bundle, discovery
and PM must never re-activate it.

## Actual
Discovery/PM re-activated a terminally-closed requirements bundle,
overwriting the operator's terminal-state decision.

## Reproduction
Steps to reproduce (historical, at v1.0.0):

1. Terminally close a requirements bundle as the operator.
2. Run the discovery pipeline.
3. Observe the bundle is re-activated, overwriting the terminal state.

## Files Involved

- discovery pipeline / PM decomposition path

---

## Affects
v1.0.0

## Fixed In
v1.23.0

## Public ID
KI-1.0.0.3
