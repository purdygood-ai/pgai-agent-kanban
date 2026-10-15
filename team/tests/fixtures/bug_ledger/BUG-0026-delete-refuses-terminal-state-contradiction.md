# BUG-0026-delete-refuses-terminal-state-contradiction

**Filed By:** CODER (reconstructed historical record) — restored for
CODER-20260907-002-bug-ledger-fixture after the original bug file behind
KI-1.0.0.4 was recycled/archived from the live bugs ledger following its
resolution, which is what caused the CHANGELOG writer's KI-1.0.0.4
disclosure to drop out of regeneration (the writer projects Known Issues
from live BUG-*.md files in bugs_dir, not from the checked-in CHANGELOG.md
or release-notes text, so a recycled bug file permanently drops its
disclosure on the next regeneration). The Symptom text below is taken
verbatim (byte-for-byte, including the mid-sentence truncation at "— i.e."
that the published render itself carries) from the last CHANGELOG.md
commit that still carried this disclosure (`git show
c2d8056:CHANGELOG.md`, "pgai-agent-kanban v1.27.0"); the Fixed In value
uses the published "fixed in v1.23.0" disclosure directly, since no more
precise internal fix version is on record for this reconstruction. This
file is a reconstruction of lost source data for test fixture purposes,
not a newly observed defect.
**Date:** 2026-09-07
**Severity:** low

## Status
done


## Category
bugfix

---

## Symptom
`delete.sh` refuses to delete an intake item that IS in a terminal state, and its refusal message contradicts itself: The item's state is `wont-do`; the guard says that is "not a terminal state"; the remedy text says to move it to "DONE or WONT-DO first" — i.e.

## Expected
`delete.sh` deletes an intake item whose state is already terminal
(DONE or WONT-DO) without a self-contradictory refusal message.

## Actual
`delete.sh` refused to delete an item already in the `wont-do` terminal
state, and its own refusal message asserted that `wont-do` is "not a
terminal state" while its remedy text told the operator to move the item
to "DONE or WONT-DO first" — the state it was already in.

## Reproduction
Steps to reproduce (historical, at v1.0.0):

1. Move an intake item to the `wont-do` state.
2. Run `delete.sh` against that item.
3. Observe the refusal message's internal contradiction.

## Files Involved

- `delete.sh`

---

## Affects
v1.0.0

## Fixed In
v1.23.0

## Public ID
KI-1.0.0.4
