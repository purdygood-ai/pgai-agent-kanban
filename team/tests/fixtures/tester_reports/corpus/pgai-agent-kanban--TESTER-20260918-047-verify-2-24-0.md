# Verification Report: rc/v2.24.0

## Summary

Verified release candidate `ai_rc/v2.24.0` (commit `6b49a96`) of pgai-agent-kanban
against its requirements document
`requirements/v2.24.0-rc-metrics-blocked-column.md` — the "Blocked" column for
rc-metrics. Verification ran in the detached read-only worktree at
`/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260918-047-verify-2-24-0`,
whose `HEAD` matches `ai_rc/v2.24.0` exactly, with a clean `git status` and an
empty stash list.

All seven prerequisite tasks (CODER-039 through CODER-045) are `DONE`, each
merged into the RC with a `--no-ff` merge commit; every feature branch was
deleted after merging, which passes both pre-flight checks trivially. Both
gated runners exited 0. All four deliverables of the requirements doc are
present and were exercised behaviourally end to end: the blocked-interval
ledger and its single reader, the Blocked column across window 7 / the
rc-metrics CLI / the REST twin, the no-backfill dash-plus-footer rendering for
pre-era rows, and the docs plus doc-truth lint registration. Every named
acceptance criterion was executed and passed, including the 40-minute HALT +
20-minute BLOCKED-task interval overlapping by 10 minutes merging to
`Blocked=50` with Wall unchanged, and grep-zero of the ledger writer inside
`team/scripts/cm/release.sh`.

Two defects were found, neither affecting the feature's measured output: the
wake-path ledger adapter writes a misleading "did not apply" line to the
operator wake log on every normal unhalted cycle, and a shipped docstring
carries an off-by-one column count. Both are filed via Path C (autonomous
follow-up filing — non-blocking).

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
low

## Findings

### Finding 1: Pre-flight — RC integrity

**Category:** pass

All task folders under `projects/pgai-agent-kanban/tasks/` reading
`## State: DONE` with a `## Feature Branch` other than `none` were scanned. No
feature branch from this cycle survives locally (CODER and WRITER delete after
merging), so Check 1 (ancestor of the RC) and Check 2 (unique commits) pass
trivially. The RC carries 15 commits over `ai_main`, containing all seven
expected `--no-ff` merge commits:

```
d5d4a57 Merge ai_feature/CODER-20260918-039-blocked-interval-ledger
33d6dbc Merge ai_feature/CODER-20260918-040-blocked-ledger-writers
761fe49 Merge ai_feature/CODER-20260918-041-blocked-minutes-rollup
0781b27 Merge ai_feature/CODER-20260918-042-blocked-column-dashboard
b0e93d4 Merge ai_feature/CODER-20260918-043-blocked-field-rest
6de9c90 Merge ai_feature/WRITER-20260918-044-blocked-column-docs
6b49a96 Merge ai_feature/CODER-20260918-045-blocked-metrics-verification
```

### Finding 2: Working tree identity

**Category:** pass

`git rev-parse HEAD` = `git rev-parse ai_rc/v2.24.0` = `6b49a96ae28b0f5c0ea9902473a0c19bbbd98934`.
`git status --porcelain` empty. `git stash list` empty.

### Finding 3: Gated unit suite

**Category:** pass

`bash team/scripts/run-unit-tests.sh --coverage` → **UNIT_EXIT=0**.
`4806 passed, 31 skipped in 793.38s`. Lint pre-flight passed;
`bare_tmp_cleanliness: OK`, `listener_cleanliness: OK`. Output captured at
`/tmp/pgai_kanban_tmp/tester/unit.log`.

All five new/changed test modules ran green:
`test_blocked_ledger.py` (18), `test_blocked_column_dashboard.py` (10),
`test_task_status_blocked_ledger.py` (8), `test_wake_blocked_ledger.py` (7),
`test_grep_zero_gate.py` (10), `test_metrics_csv_writer.py`.

### Finding 4: Gated integration suite

**Category:** pass

`bash team/scripts/run-integration-tests.sh --coverage` → **INTEGRATION_EXIT=0**.
`518 passed, 1 skipped in 537.70s`. Output captured at
`/tmp/pgai_kanban_tmp/tester/integration.log`.
`test_blocked_column_metrics_pipeline.py` (3) and
`test_cm_release_synthetic.py` (17) both green.

### Finding 5: Coverage (informational only)

**Category:** pass

Read from the runners' own JSON `totals` objects (field names verified against
the generated files, not assumed):

| Suite | Statement coverage | Branch coverage | Blended |
|---|---|---|---|
| unit | 77 % | 63 % | 74 % |
| integration | 11 % | 9 % | 11 % |

Coverage does not enter the verdict.

### Finding 6: Deliverable 1 — blocked-interval ledger

**Category:** pass

`team/pgai_agent_kanban/metrics/blocked_ledger.py` (new, 563 lines) read top to
bottom. Records `{version, kind, source, timestamp}` as JSON Lines at
`<kanban_root>/projects/<name>/metrics/blocked-intervals.jsonl` — a metrics
sidecar, not `release-state.md`, correctly justified in the module docstring
(release-state's four fixed fields cannot carry a lifecycle where absence is a
valid state). Atomic writes via temp-file + `os.replace`. Boundaries pair per
`(version, source)`, so a HALT and a BLOCKED task never close each other's
intervals. Both writers exist and are wired:
`team/scripts/lib/wake_blocked_ledger.sh` (HALT-skip firings, both scopes, both
the first pass and the contention retry pass) and
`team/pgai_agent_kanban/task_status.py` (BLOCKED transitions via the
status-writer — the single-truth path the requirements doc demanded). No second
bookkeeping path exists.

### Finding 7: Acceptance — 40-min HALT + 20-min BLOCKED overlapping 10 min → Blocked=50

**Category:** pass

Executed against the production reader in a throwaway sandbox:

```
start v2.24.0 halt:project            2026-09-18T09:00:00+00:00
end   v2.24.0 halt:project            2026-09-18T09:40:00+00:00
start v2.24.0 task:CODER-20260918-039 2026-09-18T09:30:00+00:00
end   v2.24.0 task:CODER-20260918-039 2026-09-18T09:50:00+00:00
read  v2.24.0 window 08:00 → 12:00  ->  {"blocked_minutes": 50.0}
```

Expected 50, actual 50.0. Wall is never touched by this path — the aggregator
computes `wall_time_minutes` from `opened_at`/`closed_at` independently and the
new field is additive. The gated integration test
`test_blocked_column_metrics_pipeline.py` proves the same number through the
full production chain (ledger → `aggregate_rc` → `append_rc_row` →
`show-metrics.sh`) with `Wall=120.0` unchanged, with no mocks anywhere in the
module.

### Finding 8: Acceptance — clean RC → Blocked=0; pre-era row → `-` plus footer

**Category:** pass

Reader: a version with no recorded boundaries returns `0.0`; an absent ledger
file returns `0.0`. Renderer, exercised against a three-row fixture CSV through
the real `show-metrics.sh`:

```
RC           Wall        Blocked      Input     Output   Cache Read   Cache Write  Tasks
v2.23.16     534               -  1,234,567    234,567    9,876,543       345,678     12
v2.24.0      120           1,234      1,000        200          300           400      8
v2.24.1      60                0         10          2            3             4      3
Blocked tracked from v2.24.0
```

Pre-era row renders a single `-` (deliberately not Wall's `--`); a clean RC
renders a real `0`; the footer prints exactly once. Removing the pre-era row
suppresses the footer entirely, as specified. A legacy CSV whose header lacks
the `blocked_minutes` column altogether — which is what today's live
`history.csv` is, 20 columns — renders every row as `-` with the footer, and
does not crash.

### Finding 9: Acceptance — window 7, CLI, and REST agree; shared formatter; column at END of row

**Category:** pass

- **Window 7 current-RC pane** (`team/scripts/dashboard/metrics.sh`): renders
  `Blocked:  1,234` beside `Elapsed`. With `blocked_minutes` absent from the
  rollup it renders `0`; with no rollup at all it renders `0`. Never blank,
  never `--`.
- **rc-metrics CLI history table** (`show-metrics.sh`): `Blocked` sits
  immediately right of `Wall`; no other column moved. Formatting goes through
  the shared `number_format` helper — `1,234` with commas, matching neighbours.
- **REST twin** (`team/pgai_agent_kanban/api/metrics_data.py`): emits
  `blocked_seconds` beside the existing `wall_seconds`, in seconds, following
  that module's established unit convention. Verified numerically: 1,234 minutes
  → `"blocked_seconds": 74040.0`. A real `0` comes through as `0.0`; an empty
  cell or absent column omits the key, matching the module's absent-key
  discipline.
- **history.csv**: `HISTORY_COLUMNS` has 21 entries with `blocked_minutes` at
  index 20 — LAST, so every existing positional consumer keeps its offsets.

### Finding 10: Acceptance — HALT spanning the open→ship edges clamps to the RC's own interval

**Category:** pass

Same ledger, re-read with a window of 09:20 → 09:45 (both edges cutting through
the recorded intervals): returns `25.0`, exactly the in-window span. An interval
falling entirely outside the window contributes nothing, and a source still open
when the file is read is clamped to `window_closed_at`.

### Finding 11: Step 6.5 — guard logic exercised by triggering its failure modes

**Category:** pass

- Double `block-start` for the same `(version, source)` → refused, nothing
  written, dual-channel error (`blocked-ledger.malformed` / message / remedy),
  exit 1.
- `block-end` with no matching open start → refused identically.
- Status-writer, BLOCKED with `--needs-human yes` → ledger gains a
  `block-start` for `task:<key>`; the subsequent transition out of BLOCKED
  appends the matching `block-end`. Verified end to end through
  `team/scripts/task-status.sh` against a synthetic kanban root.
- Status-writer, BLOCKED **without** `--needs-human yes` → nothing written, as
  documented.
- `Active RC` = `none` → nothing written by either writer.
- Metrics directory made unwritable (`chmod 0500`), then a BLOCKED transition →
  `task-status.sh` still exits 0 and `status.md` is still correctly written. The
  CONVERT-class "never gates the operation it rides beside" claim holds.

### Finding 12: Acceptance — no ship-path behaviour change (grep-zero)

**Category:** pass

`grep -nE "append_block_start|append_block_end|blocked_ledger" team/scripts/cm/release.sh`
returns zero hits, and the same grep across all of `team/scripts/cm/` returns
zero hits. No file under `team/scripts/cm/` changed in this RC at all. The gate
is also enforced mechanically by
`test_grep_zero_gate.py::test_blocked_ledger_writer_absent_from_release_decision_logic`,
which ships with the required negative proof (a synthetic release.sh-shaped
script carrying an injected call is confirmed to match, and a clean script with
unrelated "blocked" mentions is confirmed not to).

### Finding 13: Deliverable 4 — docs and doc-truth lint registration

**Category:** pass

`docs/dashboard.md` gains a `blocked`/`blocked_minutes` table row beside
`wall_time` and a full `### Blocked` subsection covering the two interval
sources, clamping and merging, the Wall-is-unchanged rule, the zero-versus-dash
distinction, and the explicit no-backfill rationale. `docs/OPERATIONS.md` gains
`blocked_minutes` last in the history.csv schema with the positional-consumer
rationale. `HOW_TO.md` gains the operator-facing paragraph.
`docs/blocking-site-census.md` gains the Locator Index entry
`team/pgai_agent_kanban/metrics/blocked_ledger.py:blocked-ledger-01 CONVERT`,
matching the in-code `CENSUS-ANCHOR`. `blocked_minutes` is registered in
`doc-lint.sh` `FEATURE_PATTERNS`; `doc-lint.sh --suite feature-greps` reports
`fail=0`, exit 0.

### Finding 14: Enforcement class — quality class throughout

**Category:** pass

The requirements doc forbids any of this gating, blocking, or altering ship
behaviour. Confirmed: the ledger CLI's refusal is annotated `CENSUS CLASS:
CONVERT` with a matching census entry; the aggregator degrades
`blocked_minutes` to `None` with a stderr warning on an unreadable ledger rather
than failing; the CSV writer writes an empty cell plus a warning rather than
fabricating a number; both writers swallow every failure. No new hard gate was
introduced.

### Finding 15: Wake-path ledger adapter logs a "did not apply" line every normal cycle

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated to one adapter function)
**Filed As:** BUG-0331 (Path C-A — filed as BUG (code defect / regression / test failure))
**Regression:** yes
**Integrity:** none

`team/scripts/lib/wake_common.sh` attempts a `block-end` stamp on every wake
cycle in which no HALT is present — once fleet-scoped per registered project,
and once per project inside the iteration loop. When nothing was ever opened
(the overwhelmingly common case), the ledger module correctly refuses the
orphaned close and exits 1, and `pgai_stamp_halt_boundary` then emits through
the wake `log()`:

```
project pgai-agent-kanban: blocked-ledger end stamp for source halt:global did not apply (best-effort, ignored)
project pgai-agent-kanban: blocked-ledger end stamp for source halt:project did not apply (best-effort, ignored)
```

Reproduced directly by sourcing the real adapter with a real (non-stubbed)
`log()` against a synthetic kanban root with `Active RC: v1.2.3`: two lines on
cycle 1, two more on cycle 2, and a third line when a still-present HALT
re-attempts `start`. The ledger contents remain correct throughout — this is
purely output.

Impact: with five non-OVERWATCH agents firing every two minutes, an open RC
produces roughly 300 such lines per hour (~7,000 per day) in
`logs/cron-*.log`, each phrased as a failure ("did not apply") for what is the
designed steady state. The wake log is the operator's primary diagnostic
surface and is already in the 130 MB range per agent.

Why it shipped: `team/tests/unit/test_wake_blocked_ledger.py` stubs
`log() { :; }` in its shell preamble, then asserts the repeat attempts are a
"silent no-op". With the log silenced the tests cannot observe the one
observable effect the production code actually has — a test-fidelity gap in the
sense of `docs/test-authoring-guidelines.md`. The two test names
(`test_repeated_start_before_matching_end_is_a_silent_no_op`,
`test_end_with_no_open_interval_is_a_silent_no_op`) assert a silence the
shipped code does not provide.

### Finding 16: `metrics_csv_writer.py` docstring states the wrong preceding-column count

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (one docstring sentence)
**Filed As:** BUG-0332 (Path C-A — filed as BUG)

`team/scripts/lib/metrics_csv_writer.py` line 24 reads "added after the other
**nineteen** columns were already a public contract". `HISTORY_COLUMNS` holds 21
entries and `HISTORY_COLUMNS.index("blocked_minutes")` is 20, so twenty columns
precede it — confirmed against the live `history.csv`, whose header has exactly
20 fields. The number in the sentence is wrong by one, in the one place a reader
goes to learn the column contract. The shipped operator docs are correct (they
state the position without a numeral); only the code docstring is wrong.

This was flagged in WRITER-20260918-044's own `status.md` as a concern for
TESTER and is confirmed here.

### Finding 17: Step 6.1 — release-state sanity

**Category:** pass

`release-state.md` holds exactly the four schema fields. `## Active RC` is
`v2.24.0`, matching the branch under test; `## RC Opened At` is a well-formed
ISO-8601 value; `## RC Opened By Task` names `CM-20260918-046-open-rc-2-24-0`;
`## Last Released` is a well-formed `v2.23.16`. The RC version is strictly
greater than the last released version, confirmed through the shared helper
(`semver_gt v2.24.0 v2.23.16` → true), never an inline string compare. The file's
mtime equals `RC Opened At` to the second, so it has not been touched since
`cm/open-rc.sh` wrote it.

### Finding 18: Step 6.8 — synthetic release simulation

**Category:** pass

The diff touches `team/scripts/lib/metrics_csv_writer.py`, which the RC-close
path invokes, so the simulation applies.
`team/tests/integration/test_cm_release_synthetic.py` ran inside the gated
integration suite: 17 tests, all passed, exercising `cm-release.sh` against a
fresh isolated temp repo without touching any real branch.

### Finding 19: Step 6.9 — constraint negative checks

**Category:** pass

No hardcoded secrets, API keys, tokens, or passwords in any new file. No
provenance identifiers (`BUG-NNNN`, task IDs, RC citations) in the new
production code, comments, log lines, or output strings. The one version literal
in shipped output — the footer `Blocked tracked from v2.24.0` — is the exact
string the requirements doc mandates as deliverable 3, and states a data fact
(when tracking began), not process history. The new `wake_blocked_ledger.sh`
carries no `set -euo pipefail`, matching every sourced sibling lib
(`wake_project.sh`, `halt_after_invoke.sh`) rather than diverging from them.

### Finding 20: Step 6.10 — prerequisite dependency graph

**Category:** pass

Sequence numbers 039–047 are consecutive and non-colliding. The lifecycle
bookends are correctly linked: `CM-20260918-046-open-rc-2-24-0` at the start,
this TESTER task at the end. Every task ID named in this task's `## Inputs` and
`## Prerequisites` resolves to a real task folder, all seven `DONE`. No cycles.
Queue markers agree with task states throughout (`[x]` for the seven `DONE`
tasks and the CM task, `[A]` for this WORKING task).

### Finding 21: Step 6.13 — skip-cites-real-bug gate

**Category:** pass

`bash team/scripts/lint_skip_bug_gate.sh` → `BUG-NNNN file-existence check: 0
missing files`, `OK — all skip annotations are compliant`, exit 0. No
placeholder shapes (`BUG-SKIP`, `BUG-<letters>`) appear in a skip annotation;
the `BUG-NNNN` strings elsewhere in the test tree are fixture data inside
synthetic bug files the tests create themselves, not citations.

### Finding 22: Step 6.14 — coding-standards spot-check

**Category:** pass-with-caveat

`blocked_ledger.py` ships a real argparse CLI whose `--help` names every
subcommand and argument. No new operational value is hardcoded where an operator
would want it configurable. One small duplication: the ledger path is built in
two places — `blocked_ledger.ledger_path_for_project()` (used by the Python
callers) and a literal `"${project_root}/metrics/blocked-intervals.jsonl"` in
`wake_blocked_ledger.sh`. The two cannot diverge today, because `pp_project_root`
returns exactly `<kanban_root>/projects/<name>` by string concatenation with no
per-project override, so both resolve identically. Recorded as a caveat rather
than a finding: it is a latent-only duplication with no current incorrect
behaviour.

### Finding 23: Test fidelity of the RC's new tests

**Category:** pass-with-caveat

The integration test drives the real ledger writer, the real `aggregate_rc`, the
real `append_rc_row`, and the real `show-metrics.sh` with no mocks or
monkeypatching anywhere in the module — it exercises the production breaking
condition rather than a proxy. The unit tests for the dashboard, the ledger, and
the status-writer are likewise behavioural. The one concrete fidelity defect is
the stubbed `log()` in `test_wake_blocked_ledger.py`, recorded as part of
Finding 15 rather than duplicated as its own filing, since it is the same defect.

## Lint Findings (non-blocking)

None. Both `${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-unit.json` and
`.../findings-integration.json` were generated by their runners and each
contains `"findings": []` (schema_version 1).

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=11d freshness=stale — the last cold-build record is stale on both counts (11 days old, past the 7-day window, and taken at v2.9.20 rather than the v2.24.0 under verification); its red verdict was already filed by that run as BUG-0167-cold-build-claude-abliterated-failure, which is now `done`, and nothing in this RC touches `docker/`.

## Autonomous criterion

**Manual interventions required: none.**

1. Were any tasks force-promoted from WAITING to BACKLOG by hand, bypassing the
   wake script's prerequisite evaluation? — **NO.** This task's own `status.md`
   carries the wake script's own promotion line ("Promoted from WAITING to
   BACKLOG by wake script: prerequisites satisfied"); no task folder shows a
   hand promotion.
2. Was `release-state.md` edited outside `cm-open-rc.sh` / `cm-release.sh` /
   `cm-cancel-rc.sh`? — **NO.** Its mtime (`2026-09-18T19:20:40`) equals the
   `## RC Opened At` value it records, so it has not been written since
   `cm/open-rc.sh` produced it.
3. Were any scripts patched mid-build — a commit touching `team/scripts/`,
   `subagents/`, or other tooling while the RC was in flight? — **NO.** Four
   commits touch `team/scripts/`, and every one of them is the ticketed
   deliverable of its own task (`doc-lint.sh` for CODER-045, the two dashboard
   scripts for CODER-042, the two wake libs for CODER-040, the two metrics libs
   for CODER-041). None is an out-of-band tooling patch.
4. Were any queue files edited by hand? — **NO.** Every cycle entry carries the
   marker its task state implies: `[x]` for the seven `DONE` tasks and the CM
   task, `[A]` for this WORKING TESTER task. No orphaned or mismatched markers.

Two CODER status notes record in-session self-service recoveries rather than
interventions: CODER-042 hit a CPU-contention flake in
`test_dashboard_cadence.py` from a concurrently-running sibling task sharing the
temp dir and re-ran clean; CODER-043 re-ran its suite in an isolated
`PGAI_AGENT_KANBAN_TEMP_DIR` for the same reason. Both are the
AGENT-RESOLVABLE shape core prescribes, not manual intervention.

## Caveats

- The v2.24.0 RC itself will report `Blocked=0` when it ships. The ledger writers
  only landed during this RC, and the RC was opened at 19:20:40 before the first
  writer merged, so no boundaries were ever stamped for it. This is the correct
  and honest result under deliverable 3's no-backfill rule, not a defect.
- The aggregator reaches into the `pgai_agent_kanban` package by inserting
  `team/` into `sys.path` at import time. This follows the existing
  `team/scripts/lib/agent_metrics.py` precedent and is self-location within one
  tree, not a dev-tree fallback, so it does not cross core's
  "live running code must never resolve anything from a repository checkout"
  boundary. Noted only because it is a new import direction in this file.
- Shell-lib duplication of the ledger path, detailed in Finding 22.
- Coverage numbers are recorded in Finding 5 for information only and had no
  bearing on this recommendation.
