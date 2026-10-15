# Verification Report: ai_rc/v2.24.1

Fresh verification of release candidate v2.24.1 (project `pgai-agent-kanban`)
against `requirements/v2.24.1-bugfix-bundle-20260918.md`, a two-item BUG
bundle (BUG-0331, BUG-0332). Verified in the isolated detached worktree at
`/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260918-053-verify-2-24-1`,
HEAD `15a2027` confirmed identical to `ai_rc/v2.24.1`. The RC carries four
commits (two CODER merges) touching five files: the blocked-interval ledger
module, its bash wake adapter, the history.csv writer docstring, and the two
covering unit-test files. Both gated runners were run with `--coverage` and
both exited 0. The RC's headline fix — silencing the blocked-ledger adapter's
designed no-op while keeping genuine write failures loud — was verified
behaviorally by reproducing BUG-0331's own reproduction steps against the RC
code in a throwaway sandbox root, then exercising four genuine-failure and
no-op scenarios in both directions. No bug, gap, or stale assertion was found.

## Recommendation

PASS

## Systemic Risk

low

## Findings

### Finding 1: Pre-flight — RC intact, worktree clean

**Category:** pass

`git rev-parse HEAD` = `git rev-parse ai_rc/v2.24.1` = `15a20278973aa5b6d4c3a8e8d936c0ef38689481`.
`git status --porcelain` empty; `git stash list` empty. Scanned all task
folders under `projects/pgai-agent-kanban/tasks/`: every task whose
`## State` is `DONE` and whose `## Feature Branch` is not `none` has a branch
that no longer exists locally (the normal post-merge shape, which passes both
pre-flight checks trivially). The one surviving `ai_feature/*` branch
(`CODER-20260917-020-agent-metrics-avg-wall`) belongs to a `WONT-DO` task and
is out of scope for both checks. Zero anomalies.

### Finding 2: Acceptance criterion — "Each bundled item is addressed in the implementation"

**Category:** pass

Both bundled items are addressed, and each was checked against its own bug
file rather than against the CODER summary.

**BUG-0331 (blocked-ledger no-op logs a failure).** The ledger module
`team/pgai_agent_kanban/metrics/blocked_ledger.py` gained
`ERROR_NO_OP = "blocked-ledger.no-op"`; `append_block_start` and
`append_block_end` now raise that key (instead of `blocked-ledger.malformed`)
for the two "boundary already in the requested state" refusals.
`team/scripts/lib/wake_blocked_ledger.sh`'s `pgai_stamp_halt_boundary`
captures the CLI's stderr, extracts the dual-channel machine key, and
suppresses the wake-log line only when the key is `blocked-ledger.no-op`.
Behavioral evidence is Finding 3.

**BUG-0332 (history.csv writer docstring column miscount).** The wrong
numeral was removed rather than corrected, matching the bug's stated second
acceptable outcome ("states the position without a numeral the way the
shipped operator documentation does"). Line 24 of
`team/scripts/lib/metrics_csv_writer.py` now reads "added after the other
columns were already a public contract".
`grep -n "nineteen\|twentieth\|nineteenth"` over the writer and its test file
returns nothing. The probe from the bug report still prints `21 20`
(21 columns, `blocked_minutes` at index 20), confirming the code was always
correct and only the prose was wrong. Four test docstrings in
`team/tests/unit/test_metrics_csv_writer.py` carrying the same miscount were
corrected in the same commit; no executable line changed (every assertion
still derives its boundary from `_PRE_BLOCKED_COLUMNS` or a negative index).
The single remaining tree-wide hit for the phrase is
`release-notes/v2.24.0.md:34`, which quotes the original bug report verbatim
as a historical Known Issues record and is correctly left alone.

### Finding 3: Behavioral verification (Step 6.5) — guard fires in both directions

**Category:** pass

BUG-0331's reproduction steps were replayed verbatim against the RC code in a
throwaway sandbox root (`mktemp -d -p $PGAI_AGENT_KANBAN_TEMP_DIR`, with
`PGAI_TASKS_DIR`/`PGAI_PROJECT_ROOT` cleared and `KANBAN_ROOT`,
`PGAI_AGENT_KANBAN_ROOT_PATH`, `PGAI_AGENT_KANBAN_TEMP_DIR` repointed), with a
real capturing `log()` rather than a stub, against a synthetic project whose
`## Active RC` is a version:

- `pgai_stamp_fleet_halt_boundary "halt:global" end` +
  `pgai_stamp_halt_boundary demo "halt:project" end`, run twice — **zero**
  wake-log lines, no ledger file created. This is the exact scenario that
  produced the two "did not apply" lines per cycle the bug reports; the
  original failure mode is gone.
- First `start` — writes, silent. Two further `start` calls with one already
  open — **zero** log lines; exactly one `block-start` record in the ledger.
- Matching `end` — writes, silent. Two further orphaned `end` calls — **zero**
  log lines.
- **Genuine failure A**, metrics directory chmod `0555`: exactly **one**
  wake-log line, function still returns 0.
- **Genuine failure B**, ledger file corrupted to `not json at all`: exactly
  **one** wake-log line, function still returns 0.
- CLI dual-channel contract inspected directly: an orphaned `end` emits
  `error:   blocked-ledger.no-op` / `message:` / `remedy:` and exits 1. The
  adapter's `sed -n 's/^error:[[:space:]]*//p'` correctly tolerates the
  contract's multi-space alignment.

Both halves of Anti-Pattern 6 are satisfied: the original failure mode is
silent and the new normal case (a real fault) still speaks. The "no key at
all" path (a failure before the CLI can emit its contract) falls through to
the logging branch by construction, which is the safe default.

### Finding 4: Acceptance criterion — "All Python files compile cleanly"

**Category:** pass

`python3 -m compileall -q -x '(\.git|__pycache__)' .` over the whole worktree
exited 0 (504 `.py` files). The two changed Python files compile individually
as well. Generated `__pycache__` directories were removed afterwards;
`git status --porcelain` re-confirmed empty.

### Finding 5: Acceptance criterion — "All bash files pass syntax check"

**Category:** pass

`bash -n` over all 282 `.sh` files in the worktree: 0 failures.

### Finding 6: Gated suites (Steps 5a, 6.6, 6.7)

**Category:** pass

Run from the worktree root, output teed under
`$PGAI_AGENT_KANBAN_TEMP_DIR/tester/`:

- `bash team/scripts/run-unit-tests.sh --coverage` → **`UNIT_EXIT=0`**,
  `4806 passed, 31 skipped in 802.45s`, `Unit tests PASSED`,
  `bare_tmp_cleanliness: OK`, `listener_cleanliness: OK`.
- `bash team/scripts/run-integration-tests.sh --coverage` →
  **`INTEGRATION_EXIT=0`**, `518 passed, 1 skipped in 530.50s`,
  `Integration tests PASSED`, both cleanliness gates OK.

The PASS evidence cited here is the two runner exit codes, not a pytest
"N passed" line. Direct pytest was not used at any point in this verification.

Note on the prior-session record: CODER-20260918-050's `status.md` reports a
full-suite run with 9 failures it attributed to resource contention, and
CODER-20260918-051 reports a later clean run. This verification's own run of
both gated runners on the final RC commit was clean, which is the
authoritative signal.

### Finding 7: `release-state.md` sanity (Step 6.1)

**Category:** pass

Four fields present and well-formed: `## Active RC` = `v2.24.1`,
`## RC Opened At` = `2026-09-18T23:54:29+00:00`, `## RC Opened By Task` =
`CM-20260918-052-open-rc-2-24-1`, `## Last Released` = `v2.24.0` (well-formed
`vX.Y.Z`; its presence is by design, not drift). `pp_last_released_version
"pgai-agent-kanban"` returns `v2.24.0`, and `semver_gt "v2.24.1" "v2.24.0"`
from `team/scripts/lib/semver.sh` returns true — the RC version is strictly
greater than the last released version. No inline version comparison was
performed. The per-RC state file `release-state/v2.24.1.json` is consistent:
`attempt 1`, `outcome in_progress`, `closed_at null`, cargo exactly the two
bundled bug files.

### Finding 8: Constraint negative checks (Step 6.9) and skip-bug gate (Step 6.13)

**Category:** pass

No hardcoded secret-shaped assignment in any added line
(`password|api_key|secret|token` followed by `=`): zero hits. No `curl|bash`
pattern introduced. `lint_skip_bug_gate.sh` run standalone: "OK — all skip
annotations are compliant", 0 missing `BUG-NNNN` files. No placeholder skip
shapes (`BUG-SKIP`, `skip` near `BUG-<letters>`) outside archived run logs and
one test fixture that deliberately contains the literal string `BUG-NNNN`.

### Finding 9: Coding-standards spot-check (Step 6.14)

**Category:** pass

No added line carries a bug ID, task ID, RC identifier, or version citation —
`grep -E '^\+' | grep -iE 'BUG-[0-9]|TASK-|CODER-2026|v2\.2[0-9]|ticket'` over
the full RC diff returns zero hits, which matters here because the RC's whole
subject matter is two numbered bugs. No new script was added (so no `--help`
obligation), no new operational value was hardcoded, and no `lib/`
implementation was duplicated. The new machine error key is defined once as a
module constant and referenced by name at both raise sites.

Documentation discipline: the changed behavior is script behavior, and the
matching artifact is `wake_blocked_ledger.sh`'s own header usage comment,
which was updated in the same commit to describe the silent-no-op / loud-fault
split. `docs/blocking-site-census.md` needs no edit — the census anchor
`team/pgai_agent_kanban/metrics/blocked_ledger.py:blocked-ledger-01 CONVERT`
is unchanged and correctly still classifies the site; no new refusal site was
authored, only a new key value at two pre-existing ones. No operator-facing
doc, `*_example` file, or ICD entry describes the suppressed log line or the
ledger error keys, so none was owed.

### Finding 10: Prerequisite dependency graph (Step 6.10)

**Category:** pass

Five tasks, acyclic, correctly bookended:
`CM-20260918-052-open-rc-2-24-1` (no prerequisites) → `CODER-20260918-050`
and `CODER-20260918-051` (both gated on the open) → `TESTER-20260918-053`
(gated on both CODERs) → `CM-20260918-055-release-2-24-1` (gated on both
CODERs plus TESTER). Every referenced task ID resolves to an existing task
folder. Sequence numbers 050, 051, 052, 053, 055 are non-colliding; 054 is
unused, consistent with no approval task being materialized on an
`auto`-approval bundle.

### Finding 11: Synthetic release simulation (Step 6.8)

**Category:** pass

The RC diff touches no release-lifecycle file — not `cm-release.sh`,
`cm-open-rc.sh`, `cm-cancel-rc.sh`, or `team/scripts/lib/project_paths.sh` —
so this step was not required. It is nonetheless satisfied:
`tests/integration/test_cm_release_synthetic.py` ran as part of the gated
integration suite and all of its tests passed.

### Finding 12: Spot-checks outside the spec (Step 6.11)

**Category:** pass

- **State drift:** none. `Active RC` = `v2.24.1` and the branch
  `ai_rc/v2.24.1` exists locally; no HALT or HALT-AFTER file at either the
  kanban root or the project root; queue markers match task states exactly
  (`[x]` for the two DONE CODER tasks and the DONE CM open, `[A]` for this
  running TESTER task, `[W]` for the waiting CM release task). The live
  `projects/pgai-agent-kanban/metrics/` directory contains no
  `blocked-intervals.jsonl` — consistent with BUG-0331's own report that every
  stamp attempt was a correct no-op, not drift.
- **Untested new mechanisms:** the new `ERROR_NO_OP` key is not asserted by
  value anywhere in `team/tests/unit/test_blocked_ledger.py`; its two existing
  pairing-rule tests assert only that `LedgerError` is raised. Demoted to an
  observation rather than a finding, because the key is covered behaviorally
  one layer up: reverting it to `blocked-ledger.malformed` would make both
  silent-no-op assertions in `test_wake_blocked_ledger.py` fail, since the
  adapter would log again. Noted under Caveats as a cheap future hardening.
- **Regressions of prior gaps:** the four `blocked-ledger.malformed`
  assertions CODER's `## Stale Literal Risks` flagged were re-checked
  independently. All four (lines 206, 225, 240, 255) exercise
  `parse_records()`/`blocked_minutes()` replay-time refusals — a different
  code path from the two explicit-request refusals this RC retargeted — so
  they are correctly not stale, and the green suite confirms it. No
  stale-assertion finding is owed.

### Finding 13: Test fidelity

**Category:** pass

The RC's test change is itself a fidelity repair: `_LIB_SOURCE_PREAMBLE`'s
`log() { :; }` stub — named in BUG-0331 as the reason the two existing
"silent no-op" tests could not see the defect they claimed to cover — is
replaced by a real capturing `log()` that echoes a `WAKE-LOG:`-tagged line to
stdout. The tests now assert on the real observable (the tag's absence in the
no-op cases, exactly one tagged line naming both project and source in the
unwritable-directory case) rather than on a mocked-away condition. The tests
drive the real bash adapter sourcing the real libs against a real ledger file
under `tmp_path`, not a proxy. My independent sandbox replay (Finding 3)
reached the same conclusions through a different harness.

## Lint Findings (non-blocking)

None. `/tmp/pgai_kanban_tmp/lint/findings-unit.json` (`schema_version 1`,
runner `run-unit-tests.sh`) and `/tmp/pgai_kanban_tmp/lint/findings-integration.json`
(runner `run-integration-tests.sh`) were both written by this run's gated
runners and both contain an empty `findings` array — `0 finding(s)` per each
runner's own closing line.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=11d freshness=stale — Stale on both counts: the record is 11 days old against a 7-day window and was taken at v2.9.20 rather than the v2.24.1 under verification; the run that produced it filed BUG-0167-cold-build-claude-abliterated-failure (docker and podman `claude-abliterated`, plus podman `rocky9`), which now reads `## Status: done`, so the red verdict is cited here and not re-filed.

Read through `deep_verify_ledger_read_latest` in
`$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/lib/deep_verify_ledger.sh` (return code
0). The cold-build lane was not run by this verification, per Step 5d. The
tree ships three Dockerfiles (`docker/claude-abliterated`, `docker/debian`,
`docker/rocky9`) and `team/scripts/deep-verify.sh` is present and executable,
so neither of Step 5d's two gap-earning faults applies. Per Step 5d this line
is context, not a finding: it does not change the recommendation or the
systemic risk.

## Coverage (informational only)

Extracted from the `totals` object of each runner's JSON after verifying the
field names against the generated files.

| Suite | Line (statement) % | Branch % | Blended % |
|---|---|---|---|
| unit | 76.69 | 63.45 | 73.62 |
| integration | 11.48 | 9.22 | 10.96 |

`scripts/lib/metrics_csv_writer.py` sits at 70% (unit) / 64% (integration).
Coverage did not enter the verdict.

## Autonomous criterion

Manual interventions required: none.

1. **Tasks force-promoted from WAITING to BACKLOG by hand, bypassing the wake
   script's prerequisite evaluation?** NO. Every RC task's `status.md` carries
   the wake script's own promotion note and a `## Zero Progress` block reading
   `reason_code: none / count: 0 / limit: 3`, meaning no task ever stalled and
   no retry counter was touched.
2. **`release-state.md` edited outside `cm-open-rc.sh` / `cm-release.sh` /
   `cm-cancel-rc.sh`?** NO. Its four fields exactly match what
   `CM-20260918-052`'s recorded `open-rc.sh` run (exit 0) wrote, including
   `Last Released: v2.24.0` left unchanged, and the matching
   `release-state/v2.24.1.json` was written by the same run.
3. **Scripts patched mid-build — a commit touching `team/scripts/`,
   `subagents/`, or other tooling while the RC was in flight?** NO. The RC was
   opened at 2026-09-18T23:54:29+00:00; the newest commit on `ai_main` is
   `4856b7a` at 23:46:18, before the open. The only commits touching
   `team/scripts/` during the RC are the two CODER feature-branch commits
   merged `--no-ff` through the normal pipeline (`3e7371b`, `15a2027`) —
   sanctioned ticket work, not an out-of-band patch.
4. **Queue files edited by hand?** NO. Every marker for the five RC tasks is
   the one its own state implies, in the canonical `- [x] TASK-ID` shape, in
   materialization order within each agent's own backlog file. No entry was
   added, removed, or reordered.

One documented `## Instruction Conflicts` entry exists, on
CODER-20260918-051: the task README's `## Git Repo` field reads `none` while
the same README names a Source Branch and Feature Branch and a `release`
workflow type. CODER followed the more specific invocation instruction and
recorded the discrepancy rather than blocking — the correct handling under
`SOP-core.md` "Instruction Precedence". The same `## Git Repo: none` /
`## Working Directory: none` shape appears on this TESTER task's README and
was likewise overridden by the invocation. This is a materializer field-fill
quirk that predates the RC, is not a defect this RC introduced, and required
no manual intervention on the pipeline; it is not filed.

## Caveats

- The deep-verify record for this project is both stale and red (see above).
  Nothing is known about whether this tree's images build at v2.24.1. Per
  Step 5d this raises no systemic risk and is not a finding, but CM may want
  the DEEP-VERIFY lane run before or shortly after this release, since the
  last known image state is a `failed-to-build` from many versions back.
- Cheap future hardening, not owed by any criterion: add one assertion to
  `team/tests/unit/test_blocked_ledger.py` pinning
  `exc.error == "blocked-ledger.no-op"` for the duplicate-`block-start` and
  orphaned-`block-end` refusals. The key is now a cross-module contract the
  bash adapter keys on by string value, and the only thing pinning it today is
  an indirect bash-layer assertion.
- A genuine ledger write failure still logs the same generic line it always
  did ("did not apply (best-effort, ignored)") without the CLI's now-captured
  `message` and `remedy` text. That is unchanged pre-existing behavior, not a
  regression, and the captured stderr makes surfacing it a one-line change
  should an operator ever want it.
- Prior-run artifacts: none. `artifacts/` was empty at start and no
  `report.md.previous-RUN-N` exists, so this is a clean first run for this
  task. No prior verdict was read or reused.
