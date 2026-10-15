# Verification Report: rc/v2.24.6

Verified `ai_rc/v2.24.6` (HEAD `d764c1f`) against
`requirements/v2.24.6-bugfix-bundle-20260919.md`, a two-item BUG bundle. Both gated
runners are green. The RC's single code change — tab-escaping the event band's
collapse-key scratch column — was verified behaviorally, not structurally: the bug's
own reproduction fixture produces the reported failure from an `ai_main` checkout
(one garbled row, merged `x7` count, one identity dropped) and the correct result
from the RC (two rows, `x3` and `x4`, full text, tab preserved), the shipped
regression test fails when run against the pre-fix renderer, the escaping is
bijective across the literal-backslash edge case, and tab-free output is
byte-identical to `ai_main`. The bundle's second item, `BUG-0342`, had nothing to fix
— the provenance findings it names have never existed in this repository's committed
history — which surfaced a recurring framework defect in the autonomous hygiene-lint
filer. A separate spot-check of the modified script found an untested, pre-existing
code path that assigns the render's own wall clock to un-timestamped log lines. Both
are filed via Path C-A (filed as BUG — code defect / regression / test failure).

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

medium

## Findings

### Finding 1: BUG-0343 tab-safe collapse identity — fix delivered and behaviorally correct

**Category:** pass

The reported defect reproduces exactly from `ai_main` and is gone on the RC. Run
against the bug report's own reproduction fixture (3 tab-bearing ALPHA occurrences,
4 tab-bearing BETA occurrences, both `sweep-census` skips, diverging only after the
embedded tab), in a throwaway root per the live-install-safety convention:

`ai_main` (pre-fix) — one row, garbled, BETA silently dropped:

```
[pm 09:01:00] — /p/req/ALPHA.md: finalize=tag — not report	wake(pm): sweep-census: skipped	— /p/req/ALPHA.md: finalize=tag — not report (x7, first seen 09:01:00)
```

`ai_rc/v2.24.6` — two rows, correct counts, tab preserved in the displayed text:

```
[pm 09:01:00] wake(pm): sweep-census: skipped	— /p/req/ALPHA.md: finalize=tag — not report (x3, first seen 09:01:00)
[pm 10:01:00] wake(pm): sweep-census: skipped	— /p/req/BETA.md: finalize=report — other reason (x4, first seen 10:01:00)
```

Four further checks, per Step 6.5 and anti-pattern 6 (verify the original failure
mode *and* the new normal case):

- **The shipped regression test is genuine.** Copying the RC's
  `team/tests/unit/test_event_band_renderer.py` into a clone checked out at `ai_main`
  and running
  `test_tab_bearing_signal_lines_keep_distinct_identity_and_print_full_text_once`
  fails with `assert 1 == 2` and the pre-fix garbled row in the message. The same
  test passes inside the green gated unit suite on the RC.
- **The escaping is bijective at the boundary that matters.** A line carrying a real
  tab and a line carrying the two-character sequence `\t` at the same position stay
  two distinct identities (`x2` and `x3` respectively), each printed with its own
  original text. Escaping backslash before tab is what makes that hold.
- **The tab-free path is untouched.** A two-identity tab-free `sweep-census` fixture
  renders byte-identically on the RC and on `ai_main`.
- **No operator-visible change on real input.** Against a frozen snapshot of the
  operator's live `logs/cron-*.log`, RC and `ai_main` produce the same row set
  (2236 rows, set-identical), at the same cost (10461 ms vs 10437 ms).

The change is 17 lines in `team/scripts/dashboard/event-band.sh`, all inside the
collapse-key assignment, plus a 76-line test addition. `lib/event_signal_patterns.sh`
is unmodified, as the bug's constraint required.

### Finding 2: Requirements acceptance criteria 2 and 3

**Category:** pass

- "All Python files compile cleanly" — every `.py` in the tree compiled under
  `python3 -m py_compile`, exit 0.
- "All bash files pass syntax check" — 286 `.sh` files, `bash -n` clean on all.

### Finding 3: Gated suites

**Category:** pass

- `team/scripts/run-unit-tests.sh --coverage` -> `UNIT_EXIT=0`
  (4900 passed, 31 skipped, 961.36 s). `bare_tmp_cleanliness: OK`,
  `listener_cleanliness: OK`.
- `team/scripts/run-integration-tests.sh --coverage` -> `INTEGRATION_EXIT=0`
  (519 passed, 1 skipped, 554.81 s). Same two cleanliness gates OK.

Coverage (informational only, never part of the verdict), from each runner's
`totals` object in `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/<suite>.json`:

| Suite | line % | branch % |
|---|---|---|
| unit | 76.36 | 63.15 |
| integration | 11.48 | 9.21 |

### Finding 4: Bundled item BUG-0342 had no defect to fix, and the filer that produced it files from uncommitted worktree state

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (the same filing path has produced two unreproducible bugs
in two consecutive release cycles, and each one ended its cycle on `Needs Human: yes`)
**Filed As:** BUG-0346 (Path C-A — filed as BUG)

The bundle's acceptance criterion "Each bundled item is addressed in the
implementation" is met in outcome but not in substance for `BUG-0342`. That bug names
three `provenance` hygiene findings citing `PRIORITY-0004` at
`team/tests/unit/test_event_band_renderer.py:412,419,456`. Verified independently of
CODER's account:

- `python3 team/scripts/lint_hygiene.py --classes provenance` on the RC:
  `OK — 0 findings across 8 scan director(y/ies)`. The full three-class run is also
  clean.
- `git log --all -S"PRIORITY-0004" -- team/tests/unit/test_event_band_renderer.py`
  returns **no commits**. The cited string has never been in that file on any branch.
- The only `PRIORITY-0004` occurrences in the tree are a documentation pointer in
  `team/config/default_palette.txt:8` and a fixture *value* in
  `team/tests/unit/test_lint_hygiene.py:244` — neither is a provenance citation and
  neither is at the cited lines.

The cause is upstream of this RC. Both gated runners call `lint_bug_filer_file`
(`team/scripts/lib/lint_bug_filer.sh` -> `team/pgai_agent_kanban/lint/bug_filer.py`)
on their own findings file at the end of every run, and the runners are executed by
CODER and WRITER inside feature worktrees carrying uncommitted edits. Nothing in that
path consults a commit, a branch, or `git status`, so a finding in a line that is
later reworked or discarded still becomes a permanent project bug.

The cost is a full wasted cycle per occurrence, and it has now happened twice in a
row:

| Phantom bug | Cited site | Ticket produced | Outcome |
|---|---|---|---|
| BUG-0335 | `test_event_band_renderer.py:10` citing `BUG-0333` | CODER-20260919-018 | `WONT-DO`, `Needs Human: yes` |
| BUG-0342 | `test_event_band_renderer.py:412,419,456` citing `PRIORITY-0004` | CODER-20260919-040 | `WONT-DO`, `Needs Human: yes` |

`git log --all -S"BUG-0333" -- team/tests/unit/test_event_band_renderer.py` is empty
too, so BUG-0335's site is equally unreproducible.

CODER-20260919-040's own handling was correct — it swept, found nothing, changed
nothing, and closed `WONT-DO` with the reason stated, exactly as `SOP-core.md`
"WONT-DO authority" requires. The finding is against the filer, not against CODER.

### Finding 5: The event band displays un-timestamped log lines with the render's own clock

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (fourth defect in this one script across four consecutive
RCs — BUG-0337, BUG-0340, BUG-0343, BUG-0344 — and this branch has never had a test)
**Filed As:** BUG-0347 (Path C-A — filed as BUG)

Found under Step 6.11, category "untested new mechanisms": a code path in the file
this RC modifies that carries no coverage. Pre-existing — reproduces identically from
`ai_main`, and this RC's diff does not touch the branch.

`team/scripts/dashboard/event-band.sh` assigns `date +%H:%M:%S` to any candidate line
without a leading `[ISO8601]` prefix — the bare `error:` / `message:` / `remedy:`
continuation lines of a dual-channel refusal, which the wake log emits unstamped
under their own timestamped parent. The branch's comment justifies this with "it
still sorts with its neighbors since it is read in file order", which line 378
contradicts:

```bash
sort -t "$(printf '\t')" -k1,1 "$MERGE_FILE" > "$SORTED_FILE"
```

Because the synthesized key is the render's wall clock, every affected row sorts to
the end of the stream as one block, detached from the parent line that gives it
meaning, carrying a false time, and moving on every refresh.

Measured against a frozen snapshot of the operator's live logs: 2236 rows rendered,
of which **2235** carried a synthesized timestamp clustered in the ten seconds the
render itself occupied (`00:27:50`-`00:27:59`); none of those times appears anywhere
in the input (`grep -c "00:27:50" "$S"/logs/*.log` -> 0 on every file). Exactly one
row — the collapsed `sweep-census` row at `00:00:12` — carried a real source
timestamp. Two consecutive renders of that identical static input differed from line
2 onward.

No fixture in `team/tests/unit/test_event_band_renderer.py` feeds the renderer an
un-timestamped line; the eleven tests in that module all supply
`[ISO8601]`-prefixed input.

Rated `medium` rather than `high` because the raw per-agent log panes
(`cron-tail.sh`, the logs window) remain available and unaffected, which the band's
own `--help` names as the forensic path — a genuine workaround under `SOP-core.md`
"Rating a Finding".

### Finding 6: Pre-flight, worktree, and release-state integrity

**Category:** pass

- Step 2: every task folder reading `## State: DONE` with a `## Feature Branch` other
  than `none` was scanned; no such branch survives locally, which passes both checks
  trivially (CODER and WRITER delete branches after merging). The one surviving
  `ai_feature/*` branch belongs to `CODER-20260919-040`, whose state is `WONT-DO`, not
  `DONE`, so Step 2 does not apply to it; it sits at the RC tip with zero unique
  commits, consistent with that task's "nothing to ship" disposition.
- Step 3: `git rev-parse HEAD` = `git rev-parse ai_rc/v2.24.6` = `d764c1f`;
  `git status --porcelain` empty; `git stash list` empty.
- Step 6.1: `release-state.md` holds all four fields. `## Active RC` `v2.24.6`,
  `## RC Opened At` `2026-09-19T23:18:17+00:00`, `## RC Opened By Task`
  `CM-20260919-041-open-rc-2-24-6`, `## Last Released` `v2.24.5` — well-formed.
  `pp_last_released_version "pgai-agent-kanban"` returns `v2.24.5` and
  `semver_gt v2.24.6 v2.24.5` is true (shared helper, never an inline compare).
- Step 6.10: prerequisite graph is acyclic and correctly bookended — CM-041 (open RC,
  DONE) -> CODER-039 (DONE) and CODER-040 (WONT-DO) -> TESTER-042 -> CM-044 (release,
  WAITING). Every referenced task ID exists. Sequence number 043 is unused; a gap, not
  a collision.

### Finding 7: Constraint and standards checks

**Category:** pass

- Step 6.9: `set -euo pipefail` present at line 60 of `event-band.sh` on both the RC
  and `ai_main`, unchanged. No credential-shaped strings introduced by the diff.
- Step 6.13: `team/scripts/lint_skip_bug_gate.sh` exits 0 —
  "all skip annotations are compliant", 0 missing `BUG-NNNN` files. No placeholder
  shapes in live test source (the `BUG-NNNN` literals that grep turns up are inside
  archived coverage-baseline run logs and inside the test *for* the scanner).
- Step 6.14: the diff adds no new script, no new tunable, no hardcoded operational
  value, and no duplicate of an existing `lib/` helper. `lint_hygiene.py` reports 0
  findings across all three classes, so neither the new comment block nor the new test
  cites a bug, task, or version identifier. No documentation artifact is owed — the
  change makes the code match prose `docs/DASHBOARD-PANES.md` already ships.
- Step 6.8 skipped with a note: the diff touches no release-lifecycle file
  (`cm-release.sh`, `cm-open-rc.sh`, `cm-cancel-rc.sh`, `project_paths.sh`, or the
  open/close cycle), so the synthetic release simulation does not apply.

## Lint Findings (non-blocking)

- `test_anti_patterns` — 2 x `[AP3]` hardcoded `/tmp/pgai_kanban_tmp` literal at
  `team/tests/unit/test_why_details_pane.py:462` and `:489`; reported identically by
  both suites, `severity: quality`, neither runner's exit code affected — cites
  BUG-0345 (open; `## Status: running`). Not re-filed: BUG-0345 already names this
  class, this file, and these two lines.

No other lint fired. Both findings files were present and parseable.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=12d freshness=stale — stale on both counts, the record being 12 days old against a 7-day window and recorded at v2.9.20 rather than the v2.24.6 under verification; its red verdict was already reported by BUG-0167 (now done), and nothing is known about whether this tree's images build today.

## Autonomous criterion

Manual interventions required: none.

1. **Were any tasks force-promoted from WAITING to BACKLOG by hand, bypassing the
   wake script's prerequisite evaluation?** NO. This task's own `status.md` records
   "Promoted from WAITING to BACKLOG by wake script: prerequisites satisfied"; no task
   folder in the cycle carries a hand-written state transition.
2. **Was `release-state.md` edited outside `cm-open-rc.sh` / `cm-release.sh` /
   `cm-cancel-rc.sh`?** NO. Its mtime is `2026-09-19 23:18`, matching its own
   `## RC Opened At` of `23:18:17` written by CM-20260919-041; no later modification.
3. **Were any scripts patched mid-build — a commit touching `team/scripts/`,
   `subagents/`, or other tooling while the RC was in flight?** NO out-of-band patch.
   The only two commits on the RC are `261fc1d` (the ticketed CODER-039 fix) and
   `d764c1f` (its `--no-ff` merge). The former does touch `team/scripts/`, but it *is*
   the build, not an intervention in it.
4. **Were any queue files edited by hand?** NO. Every queue mtime in
   `tasks/queues/` falls inside the cycle's agent activity window and matches an
   automated transition (`cm_backlog` 23:18 at RC open, `pm_backlog` 23:16,
   `coder_backlog` 23:53 at CODER-040's close, `tester_backlog` 23:52 at this task's
   dispatch).

One process observation that is *not* an intervention: CODER-20260919-040 closed
`WONT-DO` with `Needs Human: yes`, which `SOP-core.md` "WONT-DO authority" requires of
every WONT-DO. The chain did not stall — the wake script advanced to this task — but
the cycle does end holding an open human-confirmation request for a ticket that never
had work in it. That is the downstream symptom Finding 4 files against.

## Caveats

- **Report-time state.** `## Active RC` is `v2.24.6` and the branch exists; no state
  drift between `release-state.md` and the repository.
- **Prior-run artifacts.** The artifacts directory was empty at start; no
  `report.md.previous-RUN-N` existed. This verification re-ran every suite and
  re-derived every criterion from scratch.
- **`--help` cosmetic quirk, pre-existing, not a finding.**
  `event-band.sh --help` emits `!/usr/bin/env bash` as its first line — the shebang
  with its `#` stripped along with the rest of the header comment block. Identical on
  `ai_main`, traces to no criterion, and is recorded here as an observation only.
- **Render budget.** The band takes ~10.5 s against the operator's live logs, over the
  5 s pane refresh interval. That is open BUG-0344's subject, not a new finding; this
  RC neither improved nor worsened it (10461 ms RC vs 10437 ms `ai_main` on identical
  frozen input, within run-to-run noise).
- **Test fidelity.** The new regression test drives the real `event-band.sh` as a
  subprocess against a real on-disk log fixture and asserts on exact output lines
  rather than substrings; nothing about the breaking condition — the literal tab, the
  delimited scratch row, the `read` splitting — is mocked. It was independently shown
  to fail against the pre-fix renderer, which is the strongest available evidence that
  it exercises the real condition.
- **Sandboxing.** Every ad-hoc render was run under a throwaway root
  (`mktemp -d -p "$PGAI_AGENT_KANBAN_TEMP_DIR"`, `PGAI_TASKS_DIR` and
  `PGAI_PROJECT_ROOT` cleared) per `roles/TESTER.md` "Live-install safety". The
  `ai_main` comparison used a `git clone`, never a `cp -r` of a worktree. Live logs
  were read only through copies.
