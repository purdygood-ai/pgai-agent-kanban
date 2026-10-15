# Verification Report: rc/v2.24.2

**Task:** TESTER-20260919-011-verify-2-24-2
**RC branch:** `ai_rc/v2.24.2` @ `eb4b2de6a338c6aa9f9cd73dcb800108f151edc0`
**Base:** `ai_main`
**Requirements:** `requirements/v2.24.2-bugfix-bundle-20260919.md`, bundling
`bugs/BUG-0333-schedule-window-redesign.md`
**Worktree:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260919-011-verify-2-24-2`
**Date:** 2026-09-19

## Summary

v2.24.2 rebuilds the tmux dashboard's schedule window (window 4) as the
three bands BUG-0333 specified: a columnar roster band with live countdowns,
a per-agent status band, and a merged signal-only event band, replacing the
five raw cron tails. 23 files changed, +2,866 / -510. Both gated runners
exited 0 (4,854 unit passed / 31 skipped; 518 integration passed / 1 skipped)
with zero lint findings in either suite, and all four doc-truth and cadence
lints are clean. The structural, census, documentation and narrow-geometry
work is genuinely good: the census is updated to three panes, the prose in
`docs/dashboard.md` and `docs/DASHBOARD-PANES.md` matches it and the lints
prove it, the roster band packs all seven entries at default geometry with no
`+N more` and falls back vertically with an honest `+6 more` at width 40, the
shared `event_signal_patterns.sh` classifier correctly drops idle-cycle
boilerplate and keeps dispatch lines in a chronologic agent-tagged merge, and
the empty-band case prints `no wake events yet today`.

Behavioral verification against **production-shaped** inputs -- the operator's
real 810 MB cron logs rather than the unit fixtures -- found two of the three
bands broken on the live install. `status-band.sh` exits 1 and silently drops
the CLEANUP and OVERWATCH rows because of a bash exit-status idiom that fires
whenever an agent's log exists without a distillable verdict line.
`event-band.sh` scans every log from byte zero and does not produce output
within 300 seconds against the real logs (~25 minutes per render,
extrapolated). A third finding records that even once it renders, 96.7% of
what it would show is one repeated housekeeping line -- the literal criterion
met, the design goal missed. All three are filed via Path C (autonomous
follow-up filing -- non-blocking) and verification ran to completion.

## Recommendation

SHIP-WITH-CONCERNS

Reasoning, stated plainly so CM can weigh it rather than take it on trust:
Findings 1 and 2 are rated `high` under `SOP-core.md` "Rating a Finding" --
functionality that should work does not, with no workaround -- and both are
regressions introduced by this RC's own work. They do **not** meet the
`SHIP-WITH-SERIOUS-CONCERNS` bar as Step 8 defines it: there is no data
corruption, no break in the release pipeline, no security defect, and no
autonomy failure. Window 4 is an operator-visibility surface; nothing in the
wake chain, the release machinery, or task state reads it, so the chain keeps
running unaffected. The honest shape of this release is "ships, with one
dashboard window degraded and two high-severity follow-ups queued", which is
Step 8 branch (3).

## Systemic Risk

medium

The MAX across findings. `medium` rather than `low` because Findings 1 and 2
are the same failure class -- a new renderer green against one-line-per-case
fixtures, then wrong against production-shaped data -- appearing twice in one
RC, so it is likely to recur in similar RCs. Not `high`: it is not a broader
framework regression, it does not break multiple components, and it is not a
class CODER has been stuck on across multiple RCs.

## Findings

### Finding 1: `status-band.sh` exits 1 and drops roster rows when an agent log has no distillable verdict

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (a bash exit-status idiom that could recur in any
sibling renderer using the same scan-and-break loop shape)
**Regression:** yes
**Integrity:** none
**Filed As:** BUG-0336-status-band-drops-roster-rows (Path C-A -- filed as
BUG: code defect / regression / test failure)

`team/scripts/dashboard/status-band.sh` prints the header and five rows
(PM, CM, TESTER, CODER, WRITER), omits `CLEANUP` and `OVERWATCH` entirely,
and exits `1` with empty stderr, whenever a roster agent's
`logs/cron-<agent>.log` exists but carries no line matching one of the four
LAST RESULT verdict shapes within its last 5000 lines.

That is the live install's ordinary state. `logs/cron-cleanup.log` is five
lines of `ERROR: no project specified; pass --project <name> ...`;
`logs/cron-overwatch.log` carries no `done: completed=N ... reason="..."` line.

Observed, fixture built from the live logs:

```
AGENT      LAST FIRED          LAST RESULT        NEXT
PM         2026-09-19 03:39:05 idle (no BACKLOG)  :40 (in 0:55)
CM         2026-09-19 03:39:03 idle (no BACKLOG)  :39 (in 0:19)
TESTER     2026-09-19 03:39:03 idle (no BACKLOG)  :39 (in 0:43)
CODER      2026-09-19 03:39:03 idle (no BACKLOG)  :39 (in 0:07)
WRITER     2026-09-19 03:39:03 idle (no BACKLOG)  :39 (in 0:31)
exit=1
```

Control -- same fixture with `cron-cleanup.log` and `cron-overwatch.log`
deleted:

```
... same five rows ...
CLEANUP    no log yet          no log yet         Sun 4am
OVERWATCH  no log yet          no log yet         Sun 3am
exit=0
```

Expected, per BUG-0333 Acceptance ("all agents in the status band") and per
the prose this RC itself shipped in `docs/DASHBOARD-PANES.md` ("instead of
dropping out of the band").

Mechanism, reproduced standalone: `_status_band_distill()`'s last statement
is a `while` loop whose body ends in
`[[ ... ]] && [[ ... ]] && break`. A `while` loop's exit status is that of
the last command executed in its body, so reaching EOF without breaking
returns 1; the call site is an unguarded simple command under
`set -euo pipefail`, which aborts the render loop mid-list.

```bash
bash -c 'set -euo pipefail
f() { local line; while IFS= read -r line; do [[ "$line" == "NEVERMATCH" ]] && break; done < <(printf "a\nb\n"); }
f
echo "unreachable"'
# prints nothing; exit status 1
```

Blast radius is wider than the two weekly agents: any agent whose log is
mid-firing, freshly created, or holding only stagger/timing lines triggers
the same abort, and every roster agent ordered after it is dropped as well.

**Test fidelity.** `team/tests/unit/test_status_band_renderer.py` covers
"log absent" (which returns early, before the loop) and four "log present
with exactly one verdict-shaped line" cases (which break out of the loop on
that line). The case between them -- log present, no verdict line -- is never
written, which is exactly why a green suite coexists with this defect.

### Finding 2: `event-band.sh` scans entire cron logs and never renders on the live install

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (same fixture-fidelity class as Finding 1; the
bounded-read pattern the sibling band already uses was not carried over)
**Regression:** yes
**Integrity:** none
**Filed As:** BUG-0337-event-band-scans-whole-log (Path C-A)

`team/scripts/dashboard/event-band.sh` walks every agent's entire
`logs/cron-<agent>.log` in a pure-bash `while read` loop, running the
thirteen-pattern classifier on every line, and only afterwards discards
everything not from today.

Live log set: 810 MB, 8,037,431 lines.

```
133859585  cron-cm.log      139885019  cron-coder.log
256819588  cron-pm.log      139354255  cron-tester.log
138836297  cron-writer.log
```

Run against a fixture root symlinking those logs:
`timeout 300 ... event-band.sh --kanban-root "$S" --no-color` -> **`exit=124`,
zero lines of stdout, empty stderr.**

Bounded benchmark for the rate: 200,000 lines of the same data ->
**37.7 s** (~5,300 lines/s), extrapolating to **~1,515 s (~25 min) per
render** for the full set.

The band is hosted as
`cadence-loop.sh --interval ${REFRESH_INTERVAL} -- event-band.sh`, so the
pane is blank for the whole of each tick. Nothing bounds the growth:
`scripts/cleanup/cleanup_logs.sh` (daily 04:30) prunes only files
`-size -1024c -mtime +1`, so these logs are never rotated, and today alone
added ~28,000 lines across the five -- about +5 s of render time per day,
permanently.

The sibling `status-band.sh` bounds its own read with
`tail -n 5000 "$log_file" | tac`. The event band does not.

**Test fidelity.** `team/tests/unit/test_event_band_renderer.py` fixtures are
a handful of lines each, so the unbounded read is invisible to the suite.

### Finding 3: the event band's signal set is 96.7% one repeated line

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks, after BUG-0337)
**Systemic Risk:** low (isolated to one renderer's presentation)
**Filed As:** PRIORITY-0004-event-band-signal-dedup (Path C-B -- filed as
PRIORITY: enhancement / design change)

BUG-0333 names `sweep-census skips with reasons` as a signal class, and
`lib/event_signal_patterns.sh` implements it as `sweep-census:.*skipped`.
The implementation is faithful. Measured against production data, the
resulting band does not achieve the goal the brief opens with ("all signal,
no filler").

Today's lines (2026-09-19 through 03:40), across the five agent logs:

| Agent | Signal-class lines | of which `sweep-census: skipped` |
|---|---|---|
| pm | 362 | 359 |
| coder | 407 | 370 |
| writer | 364 | 359 |
| tester | 368 | 360 |
| cm | 364 | 356 |
| **total** | **1865** | **1804 (96.7%)** |

All 1,804 are byte-identical apart from timestamp and agent tag. The day's
distinct events -- six dispatches, six completions, eight promotions -- are 61
lines, 3.3%.

Recorded as `gap` rather than `bug` because the code does what the brief
asked; what is missing is a design decision the brief did not make (how a
legitimately-repeating signal class should render). Path C-B per Step 10:
this asks for a structural improvement the code was never asked to provide.

### Finding 4: bundle acceptance criteria -- all three met

**Category:** pass

- "Each bundled item is addressed in the implementation" -- BUG-0333's three
  bands all exist, are census citizens, and are wired into window 4.
- "All Python files compile cleanly" -- `python3 -m py_compile` over all 10
  changed `.py` files in `ai_main..ai_rc/v2.24.2`: 0 failures.
- "All bash files pass syntax check" -- `bash -n` over all 10 changed `.sh`
  files: 0 failures.

### Finding 5: BUG-0333 acceptance criteria, per bullet

**Category:** pass-with-caveat

| Criterion | Result |
|---|---|
| Default geometry: full roster visible | **pass** -- all 7 entries on one columnar line, no `+N more`, at width 200 |
| Default geometry: all agents in the status band | **fail** -- see Finding 1 |
| Event band shows only signal-class lines | **pass** -- fixture seeding an idle cycle (re-checking / no BACKLOG / sleeping / `completed=0` / timing) plus a dispatch cycle rendered only the dispatch, promotion, task-finished and `completed=1` lines, merged chronologically across agents with `[<agent> HH:MM:SS]` tags |
| Empty event band states "no wake events yet today" | **pass** -- verified on an empty-signal fixture |
| Narrow fixture: roster falls back vertical + honest "+N" | **pass** -- at pane width 40 / height 4: `PM :40 (in 0:07)` then `+6 more`; at width 200: full columnar band, no marker |
| Doc-truth tour matches the three bands | **pass** -- `generate_window_docs.py --check` OK; `lint_doc_command_truth.py` 0 findings across 657 command citations / 168 key citations / 47 pane citations; `lint_pane_refresh_cadence.py` OK |

The `pass-with-caveat` category is for the set as a whole; the one failing
bullet is carried as Finding 1, not double-counted here.

### Finding 6: implementation-note constraints from BUG-0333

**Category:** pass

- "Filter is a grep-list in one lib (`event_signal_patterns`), shared by the
  band renderer and documented; adding a pattern is one line" -- pass.
  `event-band.sh` sources the library and carries no pattern list of its own;
  the library header documents the one-line extension rule; the `docs/`
  prose names the library.
- "Bands are census citizens (rows computed, doc-truth targets)" -- pass.
  `team/config/dashboard_windows.census` window 4 went from 6 panes to 3,
  with standalone commands for each band, and both doc-truth lints verify the
  generated blocks.
- "The countdown/roster renderer also serves window 0's tile" -- pass.
  `show-header.sh` sources `lib/roster_band.sh` and `lib/next_fire_viewport.sh`
  and calls `roster_band_fetch_entries` / `roster_band_render` on the same
  data path window 4 uses.

### Finding 7: pre-flight and RC integrity

**Category:** pass

- Step 2: every task folder whose `status.md` is `DONE` with a non-`none`
  `## Feature Branch` has had that branch deleted post-merge; `git branch`
  shows no `ai_feature/*` remaining, so both checks pass trivially. The
  cycle's eight tickets appear as `--no-ff` merges on the RC.
- Step 3: `git rev-parse HEAD` == `git rev-parse ai_rc/v2.24.2` ==
  `eb4b2de`; `git status --porcelain` empty; `git stash list` empty.
- Step 6.1: `release-state.md` holds the four-field schema;
  `## Active RC` = `v2.24.2`, `## Last Released` = `v2.24.1` (well-formed);
  `semver_gt v2.24.2 "$(pp_last_released_version pgai-agent-kanban)"` true.
  No drift.
- Step 6.13: `lint_skip_bug_gate.sh` exits 0 -- no placeholder skip IDs, 0
  missing `BUG-NNNN` files among cited skip annotations.
- Step 6.9: `set -euo pipefail` present in both new executable renderers;
  `event-band.sh` traps and removes its merge scratch file; scratch goes
  through `pgai_mktemp` under the framework temp root, never bare `/tmp`
  (and the runners' `bare_tmp_cleanliness` check confirms it).
- Step 6.14: coding-standards spot-check clean. Both new scripts answer
  `--help` with exit 0 documenting `--kanban-root` and `--no-color`; no bug,
  task, RC or version identifier appears in any new comment, docstring, help
  line or log line; the `dashboard-<name>.sh` header self-naming and the
  leading `!/usr/bin/env bash` in `--help` output are the established
  convention across all 43 scripts in that directory, not new drift.

### Finding 8: gated suites

**Category:** pass

- `bash team/scripts/run-unit-tests.sh --coverage` -> **exit 0**;
  4,854 passed, 31 skipped in 1016 s; `bare_tmp_cleanliness: OK`;
  `listener_cleanliness: OK`; findings file written with 0 findings.
- `bash team/scripts/run-integration-tests.sh --coverage` -> **exit 0**;
  518 passed, 1 skipped in 530 s; both cleanliness checks OK; 0 findings.

Output captured at `$PGAI_AGENT_KANBAN_TEMP_DIR/tester/unit.log` and
`.../integration.log`. No direct-pytest result is quoted anywhere in this
report; PASS evidence for the suites is the two gated-runner exit codes.

### Finding 9: coverage (informational only, never enters the verdict)

**Category:** pass

Read from the `totals` object of each runner's JSON under
`$PGAI_AGENT_KANBAN_TEMP_DIR/coverage/`; field names verified against the
generated files before extraction.

| Suite | line (statement) % | branch % | blended % |
|---|---|---|---|
| unit | 76% | 63% | 73% |
| integration | 11% | 9% | 11% |

### Finding 10: Step 6.8 synthetic release simulation -- skipped with note

**Category:** pass

The diff touches no release-lifecycle file: the 23 changed paths are
entirely `docs/`, `team/config/dashboard_windows.census`,
`team/scripts/dashboard/**`, `team/scripts/lib/dashboard_constants.sh`,
`team/pgai_agent_kanban/dashboard/roster_entries.py` and `team/tests/unit/**`.
No `cm-release.sh`, `cm-open-rc.sh`, `cm-cancel-rc.sh`, or
`team/scripts/lib/project_paths.sh`. Step 6.8 correctly skipped.

### Finding 11: Step 6.10 prereq dependency graph

**Category:** pass

The cycle's chain is acyclic and every referenced task ID resolves to an
existing folder: `CM-20260919-010-open-rc-2-24-2` (bookend) ->
`CODER-...-002` / `-003` -> `-004` / `-005` -> `-006` ->
`WRITER-...-007` -> `CODER-...-008` -> `CODER-...-009` ->
`TESTER-20260919-011-verify-2-24-2` (this task) ->
`CM-20260919-013-release-2-24-2` (bookend, currently `[W]`). Sequence numbers
001-013 are non-colliding; 012 is absent, which is a gap in numbering, not a
dangling reference -- no task cites it.

## Lint Findings (non-blocking)

None. `findings-unit.json` and `findings-integration.json` were both present
and both carried `"findings": []` (schema_version 1, runners
`run-unit-tests.sh` / `run-integration-tests.sh`).

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=11d freshness=stale — Stale on both counts: the record is 11 days old against a 7-day window and was taken at v2.9.20 rather than the v2.24.2 under verification, and its red verdict is already tracked by BUG-0167-cold-build-claude-abliterated-failure (status done), cited here rather than re-filed; this project sets deep_verify = off in project.cfg, so nothing has refreshed it since.

## Autonomous criterion

Step 6.12, from the task logs, the `status.md` audit trail, the queue
markers, and the wake logs at `logs/cron-*.log`.

1. Were any tasks force-promoted from WAITING to BACKLOG by hand, bypassing
   the wake script's prerequisite evaluation? -- **NO.** Every promotion in
   this cycle appears in the wake logs as
   `WAITING task <ID>: prerequisites satisfied, promoting to BACKLOG`,
   written by the wake prerequisite check: tasks 002, 003, 004, 005, 006,
   007, 008, 009 and 011.
2. Was `release-state.md` edited outside `cm-open-rc.sh` / `cm-release.sh` /
   `cm-cancel-rc.sh`? -- **NO.** `## RC Opened By Task` reads
   `CM-20260919-010-open-rc-2-24-2`, whose `status.md` is DONE and whose
   wake-log transitions bracket the `## RC Opened At` timestamp
   (`01:23:28` WORKING -> `01:24:15` opened -> `01:24:54` DONE).
3. Were any scripts patched mid-build -- a commit touching `team/scripts/`,
   `subagents/` or other tooling while the RC was in flight? -- **NO.** Every
   commit on `ai_main..ai_rc/v2.24.2` arrived through a ticketed
   `ai_feature/*` branch merged `--no-ff`; the `team/scripts/dashboard/`
   changes *are* the RC's assigned scope, not an out-of-band repair.
4. Were any queue files edited by hand? -- **NO.** Every marker matches its
   task's own `## State`: `[x]` for the eight DONE tickets plus the PM and CM
   open-RC tasks, `[A]` for this task while it runs, `[W]` for
   `CM-20260919-013-release-2-24-2` whose prerequisite is this task.

**Autonomous operation criterion: Manual interventions required: none.**

## Caveats

- **A `HALT-AFTER` sentinel is armed at project scope.**
  `projects/pgai-agent-kanban/HALT-AFTER` exists (11 bytes, written
  2026-09-19 01:43) and the PM wake log reports
  `HALT-AFTER drain in progress -- chain continues normally`. Per
  `SOP-core.md` this does not gate wakes while draining and converts only at
  RC close. Noted for CM, which owns the ship-time spend of a version-pinned
  sentinel; it is not a finding and did not affect this verification.
- **`project.cfg` carries a stray `defer_piggyback = any` line** at the end
  of the file, below the `[training]` section header, so it parses as
  `training.defer_piggyback` rather than the `[release]` key of that name.
  `SOP-core.md` accepts only `true`/`false` for that key. It is inert today --
  both suites are green and the deferral tiers read `[release]` -- and it is
  operator configuration untouched by this RC, so it is an observation rather
  than a finding under Step 6.11. Worth an operator glance.
- **`CODER-20260919-009-schedule-redesign-verify` contributed zero commits.**
  Its feature branch was merged as a no-op ("Already up to date") and
  deleted, so Step 2 Check 2 passes trivially on branch absence. Its
  `status.md` documents the reasoning and cites a precedent. This is legible
  and not a finding -- but it is worth recording that the integration
  verification pass it performed reported everything green, including the
  three band renderers run standalone against a fixture kanban root. The two
  defects in this report were found only by running those same renderers
  against **production-shaped** inputs. The gap between those two results is
  the most useful thing in this report for the next iteration.
- **Sequence number 012 is unused** in this cycle's task IDs (010 -> 011 ->
  013). No task references it; noted for completeness only.
- Coverage figures are informational and were not used in any verdict.
