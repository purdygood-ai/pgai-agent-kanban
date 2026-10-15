# Verification Report: rc/v2.23.12

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Summary

`ai_rc/v2.23.12` was verified at commit `b722dd2` in a detached, clean worktree
whose HEAD matches the RC tip. The RC is a seven-item bug bundle. Both gated
runners exited 0 (unit 4697 passed / 31 skipped; integration 515 passed / 1
skipped), both lint findings files are empty, the worktree is clean after each
run, and every changed Python and shell file compiles and parses. Five of the
seven bundled items are demonstrably fixed, and four of those five were checked
by triggering the failure they prevent rather than by reading the code: the
widened provenance pattern was proved against short-form, long-form, multi-
segment and overlong citation shapes; `create.sh --help` was proved to name
every flag and to survive the pipe-closed-early case that once produced a
SIGPIPE exit; the new pane-refresh census lint was proved to fail on a seeded
bare-command pane with its dual-channel contract intact. Three gaps are filed
via Path C (autonomous follow-up filing — non-blocking): one of the five
provenance citations the sweep was given still ships, the lint's scan surfaces
omit an entire second test tree that holds 67 unseen citations, and the
bundled item `BUG-0316` will be closed as done with its reported symptom still
reproducing on the operator's dashboard. None of the three prevents the release
from being used; all three are cheap to pick up in the next loop.

## Findings

### Finding 1: Provenance lint now catches the short-form task-ID citation (BUG-0312)

**Category:** pass

Behavioral verification, not structural. A synthetic file carrying four
citation shapes was scanned with
`python3 team/scripts/lint_hygiene.py --classes provenance --code-dirs <probe>`:

- `CODER-009` (short form) — FLAGGED `[task-ID]`, with a runnable remedy.
- `CODER-20260917-009` (long form) — still FLAGGED (no regression).
- `CODER-001-002` (multi-segment fixture name) — correctly NOT flagged.
- `CODER-123456` (overlong digit run) — correctly NOT flagged.

The widened pattern is `\b(?:CODER|WRITER|TESTER|CM|PM|PO)-(?:\d{8}-\d+|\d{1,4}(?!-\d))\b`;
the `(?!-\d)` guard is the collision protection BUG-0312 asked for and it
demonstrably works.

### Finding 2: Tree-wide provenance sweep is clean on the scanned surfaces (BUG-0310)

**Category:** pass

`python3 team/scripts/lint_hygiene.py --classes provenance` from the RC
worktree root: `OK — 0 findings across 8 scan director(y/ies)`. The two
`BUG-0307` citations BUG-0310 recorded at `team/tests/unit/test_agent_metrics.py:442`
and `:518` are gone (`grep -rn "BUG-0307" --include='*.py' team/` returns
nothing), and four of BUG-0311's five `CODER-009` citations were reworded to
describe behavior (diff on `team/tests/unit/test_agent_metrics.py`).

### Finding 3: One of BUG-0311's five provenance citations still ships

**Category:** gap
**Fix Effort:** small (1 CODER task)
**Systemic Risk:** low (one comment line in one test file)
**Filed As:** BUG-0319 (Path C-A — filed as BUG: code defect / regression / test failure)

BUG-0311 named five lines. Four were fixed on this RC. The fifth is
untouched and still ships at RC commit `b722dd2`:

```
team/pgai_agent_kanban/api/tests/test_cli_rest_agent_metrics_parity.py:100
    # Per-task wall data (ticket CODER-009's field): coder averages
```

`git diff --stat ai_main..ai_rc/v2.23.12` does not list that file at all.
The tree-wide lint reports zero findings because the file sits in the lint's
blind spot (Finding 4), so the green lint run does not contradict this.

### Finding 4: The hygiene lint's scan surfaces omit the API test tree

**Category:** gap
**Fix Effort:** medium (widen the surface, then sweep what it surfaces)
**Systemic Risk:** medium (the gate reports green over an unscanned real test tree)
**Filed As:** BUG-0320 (Path C-A — filed as BUG)

`team/pgai_agent_kanban/api/tests/` is scanned by neither surface: the code
surface excludes any path segment named `tests`
(`_CODE_EXCLUDE_SEGMENTS = {"tests", "test", "fixtures", "__pycache__"}`) and
the test-tree surface defaults to `team/tests/` alone. Scanning that directory
explicitly:

```
python3 team/scripts/lint_hygiene.py --classes provenance \
    --code-dirs team/pgai_agent_kanban/api/tests --test-tree-dirs --prose-dirs --config-dirs
→ lint_hygiene: 67 finding(s)
```

This is why Finding 3's citation survived a sweep whose own acceptance was a
green tree-wide run. Pre-existing (the exclusion predates this RC), but it is
the mechanism that let this RC's own sweep ticket report done with work
outstanding.

### Finding 5: create.sh --help now names every flag (BUG-0313)

**Category:** pass

`bash team/scripts/dashboard/create.sh --help` exits 0 and prints the full
`Usage:`/`Flags:`/`Environment variables`/`Requirements` block. Both of the
bug's own reproduction commands now invert:

- `--help | grep -c -- "--print-schedule-floors"` → `2` (was `0`).
- `--help | grep -c -- "--no-tmux"` → `2` (was `0`).

The compounding defect BUG-0313 flagged — the fixed 30-line header slice that
hid every flag — was also fixed: the help branch now locates the literal
`Usage:` marker instead of counting lines, so a later header edit cannot push
the block back out of reach.

### Finding 6: The changelog-freshness test no longer writes into the checkout (BUG-0314)

**Category:** pass

`regenerate()`, `_regenerate_to_temp()`, `_diff_is_rc_tolerable()` and
`check_freshness()` all gained an optional `extra_notes_dir` /
`extra_release_notes_dir` overlay argument, defaulting to `None` so production
behavior is unchanged. `test_rc_mode_tolerates_new_release_section_addition`
now writes its synthetic `v99.0.0.md` under `tmp_path / "extra_release_notes"`
and passes that directory in. Every remaining `write_text`/`mkdir` call in the
module targets `tmp_path`; no write reaches `_RELEASE_NOTES_DIR`. Because
nothing is written into the tree, the deterministic half of the bug (a `kill -9`
mid-test leaving `release-notes/v99.0.0.md` untracked, which would BLOCK the
next verification's Step 3 pre-flight) is structurally impossible rather than
merely tidied up.

### Finding 7: Schedule panes are cadence-wrapped and a census lint enforces it (BUG-0315)

**Category:** pass

The five `SCHED_TAIL_*_CMD` variables now route `cron-tail.sh` through
`cadence-loop.sh --interval ${REFRESH_INTERVAL} --`, the same wrap every other
renderer column uses.

The new check `team/scripts/dashboard/lint_pane_refresh_cadence.py` was
exercised against a seeded failure, not just the happy path. Against the real
`create.sh`: `OK -- every pane is loop-wrapped or registered` (exit 0). Against
a copy with one schedule pane reverted to a bare `cron-tail.sh` invocation it
exits 1 and emits the dual-channel contract:

```
error:   dashboard.pane-refresh-cadence-violation
message: 1 dashboard pane(s) are neither cadence-loop.sh-wrapped nor registered ... 'schedule.1'
remedy:  wrap the pane's command through cadence-loop.sh ...
```

The refusal site is registered in `docs/blocking-site-census.md` as
`lint-pane-refresh-cadence-violation-01 CONVERT`, and
`team/tests/unit/test_dashboard_pane_refresh_cadence.py` asserts the lint
passes against the checked-in tree, so the check is wired into the gated unit
suite rather than left as an unrun script.

Not verified: BUG-0315's first two acceptance lines ("panes update across two
wake firings without recreate", "capture-pane shows no prompt") need a live
tmux dashboard against the operator's running session. TESTER does not start
one. The static evidence (the wrap is present, the census lint fires on its
absence) is what could be verified here.

### Finding 8: BUG-0316 is bundled into this RC but nothing in the RC addresses it

**Category:** gap
**Fix Effort:** small (1 CODER task, mostly verification)
**Systemic Risk:** low (one dashboard column; self-healing by design)
**Filed As:** BUG-0321 (Path C-A — filed as BUG)

`CODER-20260917-020-agent-metrics-avg-wall` closed `WONT-DO`, so `v2.23.12`
contains no change for `BUG-0316`. The `WONT-DO` reasoning checks out and this
finding does not dispute it: `compute_agent_averages()` in
`team/pgai_agent_kanban/lib/agent_metrics.py` does pool each agent's
`wall.by_agent` `{seconds, task_count}` across the window and divide once
(verified by reading the function), the CLI and REST readers share it, and the
unit and parity tests for it pass.

What is not addressed is the reported symptom. Against the live install the
column still renders `--` for every agent, because every RC rollup in the
10-RC window was written before the aggregator emitted the field
(`json.load(...).get("wall")` is `None` for `v2.23.9`, `v2.23.10`, `v2.23.11`).
`cm-release.sh` Step 16b will set `BUG-0316`'s `## Status` to `done` when this
RC ships, closing a reported behavior that still reproduces, with no open item
left to prompt a re-check and no observation yet of the first wall-bearing
rollup. That is what `BUG-0321` records.

Against the requirements doc's first acceptance criterion — "Each bundled item
is addressed in the implementation" — six of seven bundled items are addressed
in the implementation and the seventh is addressed by investigation only.

### Finding 9: Release-state and version ordering are consistent

**Category:** pass

`release-state.md` holds all four fields with well-formed values:
`Active RC: v2.23.12`, `RC Opened At: 2026-09-17T23:20:48+00:00`,
`RC Opened By Task: CM-20260917-022-open-rc-2-23-12`, `Last Released: v2.23.11`.
`pp_last_released_version "pgai-agent-kanban"` returns `v2.23.11`, and
`semver_gt "v2.23.12" "v2.23.11"` succeeds (shared helper from
`team/scripts/lib/semver.sh`, never an inline compare). `ai_rc/v2.23.12` exists
locally and the file's mtime (`23:20:48`) matches its own `RC Opened At` value
to the second, consistent with `open-rc.sh` being the last writer.

### Finding 10: Requirements acceptance criteria 2 and 3

**Category:** pass

- "All Python files compile cleanly" — `python3 -m py_compile` over every `.py`
  file in `git diff --name-only ai_main..ai_rc/v2.23.12` exits 0. The tree-wide
  equivalent runs inside both gated runners' breakage-class pre-flight, which
  passed.
- "All bash files pass syntax check" — `bash -n team/scripts/dashboard/create.sh`
  (the only changed shell file) exits 0; the tree-wide `bash -n` sweep in the
  runners' pre-flight passed.

### Finding 11: Constraint negative checks

**Category:** pass

- `set -euo pipefail` is still present at `team/scripts/dashboard/create.sh:145`,
  which matters here because the `--help` branch was rewritten under it.
- No credential-shaped literal appears in any changed file
  (`grep -nEi "(api[_-]?key|password|secret|token)\s*=\s*['\"][A-Za-z0-9_/+-]{12,}"`
  over the diff's file list returns nothing).
- The new script answers `--help` and names its one argument
  (`lint_pane_refresh_cadence.py [-h] [--create-sh PATH]`), and it imports
  `emit_error_contract` from the shared lib rather than re-implementing the
  dual-channel shape.

### Finding 12: The rewritten --help branch keeps its SIGPIPE behavior

**Category:** pass-with-caveat

A previous RC fixed a non-deterministic exit-141 in this exact branch, so the
original failure mode was re-synthesized rather than assumed. `--help` alone
exits 0 on five consecutive runs, and `--help | head -1` produces its line
cleanly on five consecutive runs — the pipe-closed-early case that produced the
SIGPIPE. The unknown-argument branch still prints its one-line usage to stderr,
now including `--print-schedule-floors`.

Caveat: the new branch prints "the two-line script description" as the first two
header lines, and the first of those is the shebang with its `#` stripped, so
`--help` output opens with the literal `!/usr/bin/env bash`. Cosmetic, and
inherited from the previous implementation (which passed the same `grep '^#'`
output through), so it is not a regression — but the rewrite chose that slice
deliberately and could have skipped the shebang while it was there.

### Finding 13: Prerequisite graph and RC bookends

**Category:** pass

No cycles; every referenced task ID resolves to an existing task folder. The
lifecycle bookends are linked correctly: `CM-20260917-022-open-rc-2-23-12`
(DONE) is a prerequisite of all five CODER tasks;
`CODER-20260917-021-provenance-sweep` correctly depends on the three CODER
tasks whose output it sweeps; this TESTER task depends on all five; and
`CM-20260917-025-release-2-23-12` sits at `[W]` (WAITING) in `cm_backlog.md`,
waiting on this verification. Queue markers match task states in every case
(`[x]` for the four DONE plus the one WONT-DO, `[A]` for this task).
Sequence numbers do not collide; `024` is unused, which is a gap in the
sequence, not a collision.

### Finding 14: Both gated suites exited 0

**Category:** pass

Run from the RC worktree root with `--coverage`, output teed under
`$PGAI_AGENT_KANBAN_TEMP_DIR/tester/`:

| Runner | Exit | Result |
|---|---|---|
| `bash team/scripts/run-unit-tests.sh --coverage` | `UNIT_EXIT=0` | 4697 passed, 31 skipped in 864.10s |
| `bash team/scripts/run-integration-tests.sh --coverage` | `INTEGRATION_EXIT=0` | 515 passed, 1 skipped in 564.09s |

The PASS evidence here is the two runner exit codes, not a pytest summary line;
both runners ran their full lint pre-flight (breakage class and quality class)
ahead of pytest and neither aborted.

Two further checks ride on these runs:

- **Step 6.8, synthetic release simulation.** The diff touches
  `team/pgai_agent_kanban/cm/changelog_writer.py`, which `cm-release.sh` calls,
  so the simulation was not skipped:
  `tests/integration/test_cm_release_synthetic.py` — 17 tests, all passed,
  inside the green integration run.
- **`BUG-0314`'s own acceptance, behaviorally.** `git status --porcelain` in
  the worktree is empty immediately after each full run. The intermittent
  failure BUG-0314 described was a stray `v99.0.0` release-notes file visible
  to other tests mid-run; neither suite produced a changelog-freshness failure
  and neither left residue behind.

### Finding 15: Coverage (informational only)

**Category:** pass

Read from the `totals` object of each runner's JSON, with the field names
checked against the generated file rather than recalled:

| Suite | Line (statement) | Branch | Blended (`Cover` column) |
|---|---|---|---|
| unit | 77 % (12652/16519) | 63 % (3137/4968) | 73 % |
| integration | 10 % (1629/16510) | 8 % (396/4968) | 9 % |

Coverage is recorded, not judged: it enters neither the recommendation nor the
systemic-risk rating.

## Lint Findings (non-blocking)

None. Both `$PGAI_AGENT_KANBAN_TEMP_DIR/lint/findings-unit.json` and
`.../findings-integration.json` were written by their runs and each records
`"findings": []`.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=10d freshness=stale — Both conditions for freshness fail: the record is 10 days old against a 7-day window, and it was taken at v2.9.20 rather than the v2.23.12 under verification; its `failed-to-build` detail names the `docker/claude-abliterated` FROM-argument parse error and the `podman/rocky9` COPY step, which are recorded in BUG-0167 (`## Status: done`, `## Fixed In: v2.9.22`), so the red verdict predates its own fix and nothing has re-run the lane since to replace it.

## Autonomous criterion

Step 6.12, four checks:

1. **Tasks force-promoted from WAITING to BACKLOG by hand?** NO. Every task in
   the cycle carries the wake script's own promotion text; this task's
   `status.md` reads "Promoted from WAITING to BACKLOG by wake script:
   prerequisites satisfied." `CM-20260917-025-release-2-23-12` is still `[W]`,
   correctly waiting on this verification rather than having been advanced.
2. **`release-state.md` edited outside the CM scripts?** NO. Its mtime
   (`2026-09-17 23:20:48`) equals its own `## RC Opened At` value to the second,
   and the file has not been touched since `open-rc.sh` wrote it.
3. **Scripts patched mid-build?** NO for the dev tree. The only commits between
   `ai_main` and `ai_rc/v2.23.12` are the four CODER feature commits and their
   four `--no-ff` merges; `ai_main` has not moved since the `v2.23.11` release
   commits. See Caveats for a live-install refresh that is not a dev-tree patch
   but is worth recording.
4. **Queue files edited by hand?** NO. Every marker matches its task's own
   `## State` (`[x]` for the four DONE tasks and the one WONT-DO, `[A]` for
   this task, `[W]` for the pending CM release), and the seven bundled bug
   files are all `[x]` with `## Status: running`, which is the state the
   discovery pipeline sets.

`Autonomous operation criterion: Manual interventions required: none.`

## Caveats

- **A live-install refresh landed mid-RC.** 354 files under
  `$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/` share an mtime of
  `2026-09-18 02:30`, and `VERSION` was rewritten at `02:30:18` holding
  `v2.23.11` — an `install.sh` refresh of the operator's live install to the
  last released version, run while this RC's CODER work was in flight. It is
  not a Step 6.12 intervention: it touched no task state, no queue file, no
  `release-state.md`, and no file under `team/scripts/` in any dev tree, and
  `install.sh` replacing the live install wholesale is its documented behavior.
  It is recorded because it changed the substrate the running agents execute
  from, mid-cycle, and because it is the same install-sync lag that Finding 8
  turns on. No entry was appended to `logs/activity.log` for it; whether a live
  install refresh is a Directive-3 host-level change is an operator question,
  not a verification result, so it is noted here rather than filed.
- **Published release notes for two prior versions were reworded on this RC.**
  `release-notes/v2.22.4.md` and `release-notes/v2.23.11.md` had internal
  identifiers removed from their Known Issues / Bugs Skipped prose by the
  provenance sweep. This edits the shipped notes of versions already tagged
  (the tags themselves are untouched), and `CHANGELOG.md` remains fresh —
  `python3 team/scripts/lint_changelog_freshness.py` exits 0 with "is fresh",
  so no artifact drift resulted. Recorded as an observation about scope, not a
  defect.
- **Two of `BUG-0315`'s four acceptance lines were not exercised.** "All
  window-4 panes update across two wake firings without recreate" and
  "capture-pane shows no prompt/echoed command in any pane" require a live tmux
  dashboard session; TESTER does not start one against the operator's install.
  The static and lint-level evidence is in Finding 7. The fourth line (the
  next-fire header showing all roster rows) is covered by the existing
  schedule-roster tests inside the green unit suite.
- **Path C bugs were filed while the integration suite was running.** BUG-0314
  warns that the changelog-freshness module reads the live bugs directory, so a
  filing made mid-run can perturb a suite. The three filings here landed after
  the unit suite (which holds that module) had finished, and the integration
  run they overlapped exited 0, so nothing was perturbed — but the sequencing
  was luck rather than design and is called out for the next run.
