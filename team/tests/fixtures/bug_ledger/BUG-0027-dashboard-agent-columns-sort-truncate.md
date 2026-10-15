# BUG-0027-dashboard-agent-columns-sort-truncate

**Filed By:** CODER (reconstructed historical record) — restored for
CODER-20260907-002-bug-ledger-fixture after the original bug file behind
KI-1.0.0.5 was recycled/archived from the live bugs ledger following its
resolution, which is what caused the CHANGELOG writer's KI-1.0.0.5
disclosure to drop out of regeneration (the writer projects Known Issues
from live BUG-*.md files in bugs_dir, not from the checked-in CHANGELOG.md
or release-notes text, so a recycled bug file permanently drops its
disclosure on the next regeneration). The Symptom value below is taken
verbatim from the last CHANGELOG.md commit that still carried this
disclosure (`git show c2d8056:CHANGELOG.md`, "pgai-agent-kanban v1.27.0");
the Fixed In value uses the published "fixed in v1.0.0" disclosure
directly (this defect was fixed within the v1.0.0 release itself). This
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
The tmux dashboard's per-project agent columns sort task rows by ID
descending (`column-render.sh` ~line 914/963: `reverse=True`) and
truncate to `max_rows_per_project` (default 8).

## Expected
The dashboard's per-project agent columns show a stable, documented
ordering and truncation behavior for task rows.

## Actual
Task rows were sorted by ID descending and silently truncated to
`max_rows_per_project` (default 8) without a documented rationale for the
ordering.

## Reproduction
Steps to reproduce (historical, at v1.0.0):

1. Populate a project with more than 8 tasks for a single agent.
2. Render the tmux dashboard.
3. Observe the per-project agent column's row order and truncation.

## Files Involved

- `column-render.sh`

---

## Affects
v1.0.0

## Fixed In
v1.0.0

## Public ID
KI-1.0.0.5
