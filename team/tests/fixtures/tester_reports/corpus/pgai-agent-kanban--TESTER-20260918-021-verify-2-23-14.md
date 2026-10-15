# Verification Report: rc/v2.23.14

Verified the release candidate `ai_rc/v2.23.14` (commit `788281c`) against
`requirements/v2.23.14-bugfix-bundle-20260918.md` — a four-item BUG bundle
(BUG-0322 ICD artifact staleness, BUG-0323 OVERWATCH lock fixtures,
BUG-0324 lock-reap fixture cwd coupling + gated-runner failure,
BUG-0325 doc-command-truth `--help` probe instability), implemented by
CODER tickets 014-018 with 019 as the integration close. Work performed in
the detached worktree at
`/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260918-021-verify-2-23-14`.

Both gated runners were run with `--coverage`: `run-integration-tests.sh` exited
0 (515 passed, 1 skipped) and `run-unit-tests.sh` exited 1 (3 failed, 4729
passed, 31 skipped), so the suite-pass precondition for `PASS` is not met. Four
of the five heads the bundle was opened for are fixed and were verified
behaviorally by triggering their original failure modes; the fifth — BUG-0324's
third, previously unexplained head — is still live and is now root-caused to an
unguarded "best effort" refusal-ledger write that kills its `set -e` caller. Two
follow-ups filed via Path C (autonomous follow-up filing — non-blocking).

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Findings

Per-step results follow; the two defect findings with their Category, Fix
Effort, Systemic Risk and Filed As fields are in "Defect Findings and Path C
Filings" below.

### Pre-flight (Steps 2-3)

**Category:** pass

- `git rev-parse HEAD` = `788281c2e741108661a41f4e7a1d19fff03bbf1f` = `git rev-parse ai_rc/v2.23.14`.
- `git status --porcelain` empty; `git stash list` empty.
- Scanned every task folder under `projects/pgai-agent-kanban/tasks/`: every
  `DONE` task naming a `## Feature Branch` other than `none` has had that branch
  deleted locally (the normal post-merge shape), so Check 1 and Check 2 pass
  trivially for all of them. No unmerged or zero-commit branch anomaly.

### Step 6.1 — release-state.md sanity

**Category:** pass

`projects/pgai-agent-kanban/release-state.md` holds all four fields:
`## Active RC` = `v2.23.14`, `## RC Opened At` = `2026-09-18T09:14:35+00:00`,
`## RC Opened By Task` = `CM-20260918-020-open-rc-2-23-14`,
`## Last Released` = `v2.23.13` (well-formed `vX.Y.Z`).
`pp_last_released_version pgai-agent-kanban` returns `v2.23.13`;
`semver_gt v2.23.14 v2.23.13` → true (shared helper, not string compare).

### Step 5a — Gated suites

**Category:** gap (unit suite did not pass)

| Runner | Command | Exit |
|---|---|---|
| unit | `bash team/scripts/run-unit-tests.sh --coverage` | `UNIT_EXIT=1` — 3 failed, 4729 passed, 31 skipped (862.69s) |
| integration | `bash team/scripts/run-integration-tests.sh --coverage` | `INTEGRATION_EXIT=0` — 515 passed, 1 skipped (630.89s) |

Captured output: `/tmp/pgai_kanban_tmp/tester/unit-run1.log`,
`/tmp/pgai_kanban_tmp/tester/integration-run1.log`.

Every pre-flight lint in both runners reported OK (breakage-class gate 473 py +
270 sh, Dockerfile static lint, anti-pattern lint, skip-cites-real-bug gate,
orphan-test, API-parity, operator-verb-manifest, ICD freshness, ICD compat,
dashboard window census, CHANGELOG freshness, doc-truth, engine-size,
env-bootstrap, help-presence, hygiene, import-completeness, dashboard
network-git, dashboard colors, HALT-scope-writers, raw-marker-read,
status-writer-bypass, dual-channel pair, runnable-remedy, census-compliance,
governance byte-budget, docs-citation manifest). The unit runner's non-zero
exit comes from pytest, not from the lint pre-flight.

The three failures are all in `team/tests/unit/test_lock_contention_staleness_reap.py`:
`test_dead_pid_holder_lock_is_reaped_and_narrated`,
`test_provenance_less_contended_lock_is_reaped_with_atomicity_narration`,
`test_live_pid_lock_older_than_threshold_is_reaped_on_age`.
Full detail under Finding 1.

### Step 5b — Coverage (informational only)

- unit: line (statement) 76.59 %, branch 63.14 % (blended `TOTAL` column: 73.48 %)
- integration: line (statement) 9.87 %, branch 7.97 % (blended: 9.43 %)

Read from the runners' own JSON at `/tmp/pgai_kanban_tmp/coverage/unit.json` and
`.../integration.json`, field names verified against the generated files
(`percent_statements_covered`, `percent_branches_covered`, `percent_covered`).

### Step 5c — see `## Lint Findings (non-blocking)` below.

### Step 6.2 / 6.3 — Structural and content review of the diff

**Category:** pass

`git diff --name-status ai_main..ai_rc/v2.23.14` — 11 modified files, 1 new:

- `docs/api/icd.json`, `docs/api/baselines/icd-v1.7.0.json` — the single
  `GET /verdicts` description string picking up `STUCK-LOCK` (BUG-0322).
  Regenerating the 1.7.0 baseline alongside the artifact matches the documented
  convention in `team/tests/unit/test_lint_icd_compat.py` ("icd-v1.7.0.json was
  frozen as a byte-identical copy" at this ICD version), and the compat rules
  compare paths/fields/enums, not descriptions.
- `team/tests/unit/test_overwatch_lock_halt_protocol.py`,
  `team/tests/integration/test_cross_cutting.py` — holders now take the lock via
  `pgai_lock_acquire` with their own live pid (BUG-0323).
- `team/tests/unit/test_lock_contention_staleness_reap.py`,
  `team/tests/unit/shell_harness.py` — `_LOCK_PROVENANCE_SH` resolved from the
  test file's own location and `run_bash()` given a self-located default `cwd`
  (BUG-0324 head 1).
- `team/scripts/run-unit-tests.sh`, `team/scripts/run-integration-tests.sh`,
  `team/tests/unit/test_gated_runner_stated_test_order.py` (new, 225 lines),
  `docs/test-authoring-guidelines.md` — `-p no:randomly` in both runners plus a
  printed `Test order:`/`Replay this exact run:` pair (BUG-0324 head 2).
- `team/scripts/lint_doc_command_truth.py`,
  `team/tests/unit/test_lint_doc_command_truth.py` — explicit sandbox `cwd` for
  the `--help` probe, `DEFAULT_HELP_TIMEOUT_SECONDS = 30.0` replacing the bare
  15 s literal, captured child stdout/stderr in the finding text (BUG-0325).

New file read in full; modified files read at the changed regions in context.
No documentation-layer defect found: the runner usage comments, the
`docs/test-authoring-guidelines.md` paragraph and the harness docstrings all
describe the behavior actually implemented.

### Step 6.4 / 6.5 — Acceptance criteria and failure-mode triggering

**Criterion "Each bundled item is addressed"**

- **BUG-0322 — pass (behaviorally verified).** `bash team/scripts/generate-icd.sh
  --output <scratch>` then `diff` against the committed `docs/api/icd.json`:
  byte-identical. The runner's own `lint_icd_freshness` reports `ok — ... is
  fresh`; `lint_icd_compat` reports a compatible superset of all 8 baselines.
  The three tests the bug named pass individually and in the full run.
- **BUG-0323 — pass (behaviorally verified).**
  `test_repo_lock_path_matches_for_project_other_than_pgai_kanban` passes
  standalone and in the gated unit run;
  `TestPerRepoLockContention::test_overwatch_protocol_declines_when_repo_lock_is_held`
  passes inside the green gated integration run. The fixtures now hold through
  `pgai_lock_acquire` with a live pid, so the reaper correctly does not reap them
  and the sentinel-4 contention path is reached.
- **BUG-0324 head 1 (cwd coupling) — pass (original failure mode triggered).**
  The bug's own reproduction step 5 (run the file with pytest from a directory
  that is not `team/`) previously gave `7 failed, 1 passed` with
  `bash: line 2: scripts/lib/lock_provenance.sh: No such file or directory`.
  Re-run from a scratch sandbox directory: `8 passed`.
- **BUG-0324 head 2 (randomised suite order) — pass (behaviorally verified).**
  The gated unit run's pytest header now reads
  `plugins: anyio-4.13.0, cov-7.1.0, web3-6.20.4` with no `randomly-4.1.0` and no
  `Using --randomly-seed=` line, and both runners print `Test order: stated
  (pytest-randomly neutralized via -p no:randomly).` plus a replayable command.
- **BUG-0324 head 3 (the unexplained gated-runner failure) — NOT addressed.**
  See Finding 1.
- **BUG-0325 — pass (original failure mode triggered).** `lint_doc_command_truth.py
  --repo-root <RC>` run from a foreign working directory reports `OK — 0
  findings` (654 citations, 13 documents, 3 process-internal exemptions), and the
  test the bug named passes both standalone and in the gated run. The probe now
  pins the child's `cwd` to the same fresh sandbox it already pinned the child's
  kanban-root env vars to, and the timeout moved 15 s → 30 s at a single named
  constant.

**Criterion "All Python files compile cleanly" — pass.** `python3 -m py_compile`
over every `*.py` in the tree: 495 files, 0 failures.

**Criterion "All bash files pass syntax check" — pass.** `bash -n` over every
`*.sh` in the tree: 281 files, 0 failures.

### Step 6.8 — Synthetic release simulation

**Category:** pass (skipped by rule, then covered anyway)

The diff touches no release-lifecycle file (`cm-release.sh`, `cm-open-rc.sh`,
`cm-cancel-rc.sh`, `project_paths.sh`), so this step is skipped by its own
condition. `tests/integration/test_cm_release_synthetic.py` nevertheless ran
green inside the gated integration suite (5 passed).

### Step 6.9 — Constraint negative checks

**Category:** pass

`set -euo pipefail` and the cleanup traps in both gated runners are intact (the
diff only inserts comment blocks, two `echo` lines and the `PYTEST_ARGS` entry).
No hardcoded secret, token or credential appears in the diff. No bare `/tmp`
path is introduced. The dual-channel-pair, runnable-remedy and census-compliance
lints all report 0 offenders.

### Step 6.10 — Prereq dependency graph

**Category:** pass

`CM-20260918-020-open-rc-2-23-14` opens the RC; CODER 014-018 each carry one
bundled item (018 also depends on nothing later); `CODER-20260918-019` depends on
the open-RC task plus all five fixes; this TESTER task depends on 014-019. No
cycle, every referenced task ID resolves to an existing folder, sequence numbers
do not collide.

### Step 6.12 — Autonomous operation criterion

**Category:** pass

1. Tasks force-promoted from WAITING to BACKLOG by hand? **NO** — every
   prerequisite task's `## Zero Progress` reads `count: 0`, and the promotions
   recorded in the task summaries are the wake script's own.
2. `release-state.md` edited outside the cm scripts? **NO** — the file holds
   exactly the four fields `cm-open-rc.sh` writes, with `## RC Opened By Task`
   naming `CM-20260918-020-open-rc-2-23-14`.
3. Scripts patched mid-build? **NO** — `team/scripts/run-unit-tests.sh`,
   `run-integration-tests.sh` and `lint_doc_command_truth.py` were changed, but
   as the authorised deliverables of tickets 017 and 018 on their own feature
   branches, not as out-of-band patches.
4. Queue files hand-edited? **NO** — queue mtimes track wake-script activity and
   every entry follows the `- [x] TASK-ID — description` shape.

`Autonomous operation criterion: Manual interventions required: none`

### Step 6.13 — Skip-cites-real-bug grep-gate

**Category:** pass

No placeholder skip shape (`BUG-SKIP`, `skip` near `BUG-<letters>`) exists in
`team/tests/`, and no skip annotation in the RC's tests cites a `BUG-NNNN` at
all. The runner's own `lint_skip_bug_gate` reported
`OK — all skip annotations are compliant` (0 missing files).

### Step 6.14 — Coding-standards spot-check

**Category:** pass

The diff adds no new script (so the `--help` requirement is not engaged),
introduces no new hardcoded operational value — it removes one, replacing the
bare `15.0` help-probe timeout with the module-scope
`DEFAULT_HELP_TIMEOUT_SECONDS` that both the argparse default and the docstring
point at — and duplicates nothing already in `lib/`. Grepping every added line
in `team/` and `docs/` for bug IDs, task IDs and version citations returns
nothing, and the hygiene lint (`provenance`, `version-named-test`,
`comment-drift`) reports 0 findings.

### Step 6.11 — Spot-checks outside the spec

**Category:** pass-with-caveat

- *State drift:* `## Active RC` is `v2.23.14` and the local branch
  `ai_rc/v2.23.14` exists at the commit under test. `VERSION` still reads
  `v2.23.13` and `CHANGELOG.md`'s newest entry is `v2.23.13` — correct for an
  open RC (CM writes both at release), not drift.
- *Untested new mechanisms:* the new `-p no:randomly` policy is covered by the
  new `team/tests/unit/test_gated_runner_stated_test_order.py` (static assertions
  on both runners plus a behavioral two-run order-stability check), and the new
  `run_bash(cwd=...)` default is exercised by every existing caller.
- *Regressions of prior gaps:* BUG-0324's third, explicitly-unresolved head is
  still live on this RC — Finding 1.

---

## Defect Findings and Path C Filings

### Finding 1: the gated unit runner exits 1 — a "best effort" refusal-ledger write kills its `set -e` caller

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks — one guarded call, plus a regression test)
**Systemic Risk:** medium (the helper sits on every refusal path in the framework, so the same abort can recur wherever a caller runs under `set -e` and the ledger write fails)
**Filed As:** BUG-0326 (Path C-A — filed as BUG: code defect / regression / test failure)

`bash team/scripts/run-unit-tests.sh --coverage` exits 1 with three failures in
`team/tests/unit/test_lock_contention_staleness_reap.py`
(`test_dead_pid_holder_lock_is_reaped_and_narrated`,
`test_provenance_less_contended_lock_is_reaped_with_atomicity_narration`,
`test_live_pid_lock_older_than_threshold_is_reaped_on_age`; a run in an
instrumented clone of the same commit also failed
`test_kill9_holder_self_recovers_on_next_acquisition`, 4 failures). This is the
third head of BUG-0324 — the one CODER-20260918-016 and -017 explicitly left
out of scope and CODER-20260918-019 recorded as "unexplained". It is now
root-caused.

**Mechanism, proven.** `refusal_ledger_record_best_effort`
(`team/scripts/lib/refusal_ledger.sh:144-149`) calls `refusal_ledger_record ...
>/dev/null 2>&1` as an unguarded simple command and only then `return 0`. The
redirection hides the output, not the exit status, so under the caller's
`set -euo pipefail` the shell exits at that line and the documented "Always
returns 0; never prints" contract is not honoured.
`pgai_emit_error_contract` (`team/scripts/lib/error_contract.sh:117-133`) calls
it on every emission when a project context resolves, so the abort rides on
every refusal site. In these tests the victim is `pgai_lock_acquire`: it
correctly classifies the holder as stale, emits the
`lock.provenance.stale_reaped.*` contract, and is then killed before its
reap-and-retry can return 0 — which is exactly the "reap narration on stderr,
then silent exit 1, no `lock.provenance.contended` message" shape BUG-0324
could not explain.

**Why it only fires under the gated runner.** `refusal_ledger_record` refuses
when `[[ ! -d "${KANBAN_ROOT}" ]]` (its own deliberate "never fabricate the
kanban root" guard). `run-unit-tests.sh:547` assigns `KANBAN_ROOT="$TEAM_ROOT"`
**without `export`**, so the pytest process and every `run_bash()` child run with
no `KANBAN_ROOT`, while an operator shell that sourced `shell-env` exports it.

**Evidence.**

- Minimal reproduction, ~2 s, from `<dev tree>/team`:
  `env -u KANBAN_ROOT PGAI_PROJECT_NAME=pgai-agent-kanban python3 -m pytest -p no:randomly -q tests/unit/test_lock_contention_staleness_reap.py`
  → `4 failed, 4 passed`; the identical command with `KANBAN_ROOT` exported →
  `8 passed`.
- Contract helper in isolation, no pytest: a script doing
  `set -euo pipefail; source error_contract.sh; pgai_emit_error_contract k m r; echo "AFTER_EMIT reached"`
  run under `env -u KANBAN_ROOT PGAI_PROJECT_NAME=pgai-agent-kanban` prints the
  three contract lines, never prints `AFTER_EMIT reached`, and exits 1. With
  `KANBAN_ROOT` set it prints `AFTER_EMIT reached` and exits 0.
- `env -u KANBAN_ROOT bash -c 'source scripts/lib/refusal_ledger.sh; set +e; refusal_ledger_record pgai-agent-kanban k m r s >/dev/null 2>&1; echo $?'`
  → `1`.
- A clone of the RC at the same commit, with the fixtures' acquirer scripts run
  under `set -euxo pipefail`, reproduced the failure under the gated runner and
  its xtrace ends exactly at `+ refusal_ledger_record_best_effort ...`, followed
  by the EXIT trap's `pgai_lock_release_all_held`.

**The production library is not implicated.** Every reap behavior these
fixtures cover was exercised by hand against the shipped
`team/scripts/lib/lock_provenance.sh` with `KANBAN_ROOT` present: a dead-pid
record is reaped and the acquisition returns 0, and a live well-provenanced
holder is still refused with `lock.provenance.contended`.

**Consequence for this RC.** The suite-pass precondition for `PASS` is not met:
`UNIT_EXIT=1`. It also means the bundle's criterion "each bundled item is
addressed" is met for BUG-0322, BUG-0323 and BUG-0325, and met in two of three
heads for BUG-0324. Note that `cm-release.sh` Step 16b will set BUG-0324's
`## Status` to `done` when this RC ships; BUG-0326 is what carries the
unresolved head forward, and it names the one-line fix.

**Regression:** no
**Integrity:** breakage

(The two fields above are recorded even though this finding's Systemic Risk is
`medium`, because the reader would otherwise have to infer them: the failing
tests failed identically on v2.23.13 before this RC opened, and the unguarded
call in `refusal_ledger_record_best_effort` is older than either.)

### Finding 2: the runners' "Replay this exact run" line does not reproduce the run

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated to the two runners' output)
**Filed As:** BUG-0327 (Path C-A — filed as BUG: code defect / regression / test failure)

CODER-20260918-017 added, to both runners, a `Test order: stated ...` line and a
`Replay this exact run: (cd ... && python3 -m pytest ...)` line, and both
runners' usage comments now promise "A failing run is reproduced by re-running
this exact command; no seed capture is needed."

Run verbatim, it does not reproduce. The gated unit run on commit `788281c`
exited 1 with three failures; its own printed replay command, pasted into an
operator shell on the same commit, exited 0 — `4732 passed, 31 skipped`
(`/tmp/pgai_kanban_tmp/tester/t021-replay.log`). The line carries the pytest
argv only, and omits every environment variable the runner applies —
`PYTEST_ADDOPTS=--basetemp=...`, `PYTHONHASHSEED=0`, `PGAI_DEV_TREE_PATH`,
`PGAI_PROJECT_TEMP_SUBTREE`, the crontab seam, and (decisively)
`KANBAN_ROOT`, which line 547 sets without exporting. Adding `env -u
KANBAN_ROOT` to the printed command reproduces the runner's failures exactly,
which is what makes this a defect in the line rather than a flaky suite.

The cost is already visible in this cycle's record: two CODER tickets re-ran
this command, saw green, and recorded the failure as "not reproducible
standalone".

The new `team/tests/unit/test_gated_runner_stated_test_order.py` asserts the
line is printed and carries the pytest invocation; it cannot catch this,
because the assertion is about the text, not about whether the text reproduces
the run.

## Lint Findings (non-blocking)

None. Both runners wrote an empty findings file —
`/tmp/pgai_kanban_tmp/lint/findings-unit.json` (0 findings, generated
2026-09-18T13:37:27+00:00) and `/tmp/pgai_kanban_tmp/lint/findings-integration.json`
(0 findings, generated 2026-09-18T13:48:31+00:00). Every quality-class lint in
both pre-flights printed OK, including the hygiene lint's three classes
(`provenance`, `version-named-test`, `comment-drift`).

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=11d freshness=stale — the last cold-build record is 11 days old and was taken at v2.9.20 rather than the v2.23.14 under verification, so it is stale on both counts; its `failed-to-build` verdict is already carried by BUG-0167 (`docker/claude-abliterated` and `podman/rocky9` pairs, status `done`), and nothing is known about whether this tree's images build today.

Read through `deep_verify_ledger_read_latest` in
`$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/lib/deep_verify_ledger.sh` (return 0).
The tree ships three Dockerfiles (`docker/claude-abliterated`, `docker/debian`,
`docker/rocky9`) and `team/scripts/deep-verify.sh` is present and executable, so
neither Step 5d gap applies. No cold-build lane was run by TESTER.

## Autonomous criterion

Autonomous operation criterion: Manual interventions required: none

1. Tasks force-promoted from WAITING to BACKLOG by hand — NO.
2. `release-state.md` edited outside `cm-open-rc.sh`/`cm-release.sh`/`cm-cancel-rc.sh` — NO.
3. Scripts patched mid-build — NO (the changes to `team/scripts/` are tickets
   017's and 018's own authorised deliverables, each on its own feature branch
   with a `--no-ff` merge).
4. Queue files edited by hand — NO.

Every commit on `ai_main..ai_rc/v2.23.14` is one implementation commit per
ticket plus its `--no-ff` merge, in ticket order, with no out-of-band commit.
`CODER-20260918-019` merged zero commits (a verification-only closing ticket);
its status records that explicitly and the branch was deleted, which matches
the established shape for that ticket type.

## Caveats

- `CODER-20260918-019`'s own acceptance criterion 1 ("`run-unit-tests.sh` exits
  0, run twice") was not met, and its status says so under
  `## Instruction Conflicts` rather than hiding it. Its verdict on the failures
  ("real, deterministic under full-suite load, proven not a library defect, not
  reproducible standalone") matches what I observed; the part it could not
  supply — why — is Finding 1.
- `CODER-20260918-019` also saw a single failure of
  `tests/unit/test_dashboard_cadence.py::test_switching_to_hidden_window_produces_an_immediate_render`
  (a 1.5 s wall-clock assertion) in one of its two runs. It did not recur in any
  of my three full unit passes (gated run, direct replay, instrumented clone),
  so I have no reproduction to file a bug from. Recorded here so a future
  verifier that sees it again has the prior sighting.
- The unit suite has now exited non-zero on two consecutive release candidates
  (v2.23.13 and v2.23.14) for the same test file. This RC halved the failure
  count and fixed four of the five heads it was opened for; the remaining head
  is now root-caused down to a one-line change. I judge the loop to be making
  real progress rather than stuck, which is why Finding 1 is rated
  `Systemic Risk: medium` and not `high` — but a third RC shipping with a red
  certifying runner would be evidence of the opposite, and should be rated
  accordingly.
- Test fidelity: the fixtures covering this RC's changes exercise the real
  condition rather than a proxy — the lock fixtures take real `flock`s in real
  subprocesses, the doc-truth test runs the lint over the real tree, and the
  ICD test compares a real regeneration against the committed artifact. No
  mocked-away breaking condition found. The one fidelity gap is the one Finding
  2 names: the new order-policy test asserts on the runner's printed text rather
  than on the reproducibility the text claims.
- Verification ran against the detached worktree at
  `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260918-021-verify-2-23-14`.
  Diagnostic runs used a separate `git clone` under
  `/tmp/pgai_kanban_tmp/tester/clone-t021`; no file in the RC worktree was
  modified (`git status --porcelain` is still empty).
