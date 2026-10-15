# BUG-0023-tmux-dashboard-size-missing

**Filed By:** CODER (reconstructed historical record) — restored for
CODER-20260907-002-bug-ledger-fixture after the original BUG-0023 file was
recycled/archived from the live bugs ledger following its resolution,
which is what caused the CHANGELOG writer's KI-1.0.0.1 disclosure to drop
out of regeneration (the writer projects Known Issues from live BUG-*.md
files in bugs_dir, not from the checked-in CHANGELOG.md or release-notes
text, so a recycled bug file permanently drops its disclosure on the next
regeneration). The Symptom / Affects / Fixed In / Public ID values below
are taken verbatim from the last CHANGELOG.md commit that still carried
this disclosure (`git show c2d8056:CHANGELOG.md`, "pgai-agent-kanban
v1.27.0"). This file is a reconstruction of lost source data for test
fixture purposes, not a newly observed defect.
**Date:** 2026-09-07
**Severity:** medium

## Status
done


## Category
bugfix

---

## Symptom
`scripts/dashboard/create.sh` fails on Ubuntu 24.04 with `size missing`
emitted by tmux, leaving the dashboard partially constructed (window 0
never splits past one pane).

## Expected
The dashboard creation script builds the full multi-pane tmux layout on
Ubuntu 24.04 (and other supported platforms) without error.

## Actual
`tmux split-window` (or an equivalent pane-construction call) emitted
`size missing` on Ubuntu 24.04's tmux 3.4, aborting `create.sh` after the
first pane. This is the origin of the public disclosure KI-1.0.0.1 at
v1.0.0.

## Reproduction
Steps to reproduce (historical, at v1.0.0):

1. On an Ubuntu 24.04 host running tmux 3.4, run
   `scripts/dashboard/create.sh`.
2. Observe `size missing` from tmux and window 0 left with a single pane
   instead of the full layout.

## Files Involved

- `scripts/dashboard/create.sh`

## Hypothesis (optional)
tmux 3.4 on Ubuntu 24.04 changed pane-sizing behavior for a split
invocation the script relied on without an explicit size argument.

---

## Affects
v1.0.0

## Fixed In
v1.16.2

## Public ID
KI-1.0.0.1
