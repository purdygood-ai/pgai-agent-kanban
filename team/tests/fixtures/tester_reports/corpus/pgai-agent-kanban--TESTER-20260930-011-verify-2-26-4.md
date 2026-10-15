# Verification Report: rc/v2.26.4

## Summary

This report verifies `ai_rc/v2.26.4` (HEAD 676b74f440dfd250496ad06e02f6c2518eb865c6) against `requirements/v2.26.4-bugfix-bundle-20260929.md`, a bundle of seven low-severity bugs: BUG-0427, 0429, 0430, 0450, 0451, 0452 and 0453. The work was done by CODER-20260930-002…006, 008 and 009 and by WRITER-20260930-007.

Both gated runners exit 0 on the RC. Unit: 5736 passed, 43 skipped. Integration: 637 passed, 1 skipped. The bundle's compile and syntax criteria hold.

For each of the four bundled bugs that had a reproducible failure mode, the original failure was reproduced against `ai_main` code in a sandbox and shown to be prevented on the RC:

- BUG-0427: overlapping runner basetemps.
- BUG-0429: stdout leak into the finder's captured value.
- BUG-0450: intake clobber in the TOCTOU window.
- BUG-0451: stop killing an unrelated listener or a reused pid.

BUG-0430's lint is clean.

Two bundled items are only partly closed:

- The stale-worktree reconcile sweep, which is BUG-0452's own reproduction path, still accepts a `..` task id with no refusal (Finding 1). The destructive `rm -rf` is now guarded, so nothing is deleted.
- The OVERWATCH role file's HALT-first paragraph still names the fleet HALT and a lock "check" (Finding 4).

Two pre-existing api-server issues sit on the path BUG-0451 fixed and are filed as well (Findings 2 and 3). All four are filed via Path C (autonomous follow-up filing — non-blocking). None is severe, so the recommendation is SHIP-WITH-CONCERNS.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Findings

### Finding 1: reconcile_stale_active still accepts a `..` queue task id (BUG-0452 partially met)

**Category:** gap
**Severity:** medium
**Fix Effort:** small (1 CODER task)
**Systemic Risk:** medium (the same loose `[A-Za-z0-9._-]+` id regex survives at several sites after a fix that covered only some of them; the class can recur whenever a new queue reader is added)
**Filed As:** BUG-0466-reconcile-sweep-accepts-dotdot-task-id (Path C-A — filed as BUG: code defect)
**Regression:** no
**Integrity:** none
**Filed By:** TESTER (autonomous Path C) — the wake's stale-task reconcile sweep still turns a `..` queue id into a worktree path above the worktree base and a status path outside the tasks folder, without the refusal the rest of the wake now gives.

Traces to BUG-0452 Acceptance ("Malformed id is refused dual-channel") and its Reproduction ("append a queue line with id `..`; run the stale-worktree reconcile; observe the computed path"). `team/scripts/lib/wake_reconcile_sweeps.sh:147` (`reconcile_stale_active`) is unchanged and still matches `([A-Za-z0-9._-]+)`. `team/scripts/lib/wake_driver.sh:526` is also unchanged, but it is read-only.

Probe on the RC (sandbox root, liveness stubbed `absent`, state writer and teardown stubbed to print): a `- [A] ..` line produced no `wake.queue.malformed-task-id` refusal. The sweep logged "tearing down prior worktree at `<base>/worktrees/..`" and passed `<TASKS_ROOT>/../status.md` to `zero_progress_advance_or_escalate`.

The destructive outcome is prevented. The real `teardown_task_worktree ..` fails harmlessly (`git worktree remove` refuses a non-worktree), and the new `pgai_worktree_path_strictly_inside` guard refuses the only `rm -rf`, as verified in the BUG-0452 section below. The rating is medium under SOP-core's calibration for a real but unexercised defect.

### Finding 2: api-server.sh probes PGAI_API_PORT while the server binds kanban.cfg [api] port

**Category:** bug
**Severity:** medium
**Fix Effort:** small (1 CODER task)
**Systemic Risk:** low (isolated to api-server.sh / api config)
**Filed As:** BUG-0467-api-server-port-source-disagreement (Path C-A — filed as BUG: code defect)
**Regression:** no
**Integrity:** none
**Filed By:** TESTER (autonomous Path C) — the API start/stop script looks for the server on the environment-variable port (default 8300) while the server listens on the kanban.cfg port, so status and stop cannot find an orphaned server when only the kanban.cfg port is set.

This predates the RC: the `_API_PORT="${PGAI_API_PORT:-8300}"` line is unchanged from `ai_main`. It is filed because BUG-0451's stop guard runs through this port probe, and CODER-005 and CODER-008 both raised it for TESTER.

Probe: sandbox `kanban.cfg [api] port = 8393`, `PGAI_API_PORT` unset. `start` bound 127.0.0.1:8393. After the pidfile was removed, `status` said "not running" (exit 1), `stop` said "not running" (exit 0), and the server survived.

### Finding 3: docs/operator-api.md misstates the pidfile location and stop's behaviour

**Category:** bug
**Severity:** low
**Fix Effort:** small (1 WRITER task)
**Systemic Risk:** low (one doc section)
**Filed As:** BUG-0468-operator-api-pidfile-and-stop-docs-stale (Path C-A — filed as BUG: documentation defect)
**Regression:** yes
**Integrity:** none
**Filed By:** TESTER (autonomous Path C) — the operator guide says the API pidfile lives in the temp directory and that stop always sends SIGTERM, but the script keeps it under the kanban root's run/api directory and now refuses to signal anything that is not the API server.

The guide places the pidfile at `${PGAI_AGENT_KANBAN_TEMP_DIR}/api/api-server.pid`; `api-server.sh:247-250` uses `${_KANBAN_ROOT}/run/api/api-server.pid`. Probe: a pidfile at the documented path is ignored by status/stop.

The lifecycle table's `stop` row does not describe the refusal this RC added (exit 1, nothing signalled). The script's own usage text was updated, which satisfies the Documentation Discipline rule's "usage comment" artifact, but the operator guide's row is now inaccurate. `Regression: yes` reflects that the stop-row inaccuracy is new with this RC; the pidfile-path prose predates it. This RC edited the adjacent environment-table row in the same table.

### Finding 4: OVERWATCH.md HALT-first paragraph still names the fleet HALT and a lock "check" (BUG-0453 residual)

**Category:** gap
**Severity:** low
**Fix Effort:** small (1 WRITER task)
**Systemic Risk:** low (one paragraph of one role file)
**Filed As:** BUG-0469-overwatch-halt-first-paragraph-root-halt (Path C-A — filed as BUG: documentation defect)
**Regression:** no
**Integrity:** none
**Filed By:** TESTER (autonomous Path C) — the OVERWATCH role file's HALT-first paragraph still tells OVERWATCH to create the kanban-root HALT and merely check the repo lock, while the code creates the project's own HALT and takes and holds the lock.

BUG-0453 asks for "Doc and code agree". Step 2 (line 62) and line 74 now agree with `overwatch_halt_first_fix`. Line 133 (HALT-first) still says OVERWATCH "check[s] the per-repo flock" and "creates `$KANBAN_ROOT/HALT`". The code acquires and holds the lock (`pgai_lock_acquire`, ~430) and touches `$(pp_project_root "$OVERWATCH_PROJECT")/HALT` (~445). SOP-core "HALT Flags" says OVERWATCH repairs never write the fleet file.

The acceptance "a doc-truth lint or unit test pins the chosen invariant" is met for the code only (existing test (C) in `test_overwatch_lock_halt_protocol.py`). WRITER-007 recorded the paragraph as out of scope. Role-file errors like this one produce no test failure, which is why this is filed rather than left as a caveat.

## Verification Detail (per step and per bundled item)

### Step 5a / 6.6 / 6.7 — gated runners

- `bash team/scripts/run-unit-tests.sh --coverage` from the worktree, with `PGAI_DEV_TREE_PATH` set to the worktree: **UNIT_EXIT=0**, 5736 passed, 43 skipped in 1118 s. Post-suite checks OK: bare_tmp, listener, sentinel_leak and scratch_sweep. The `[pgai-sentinel]` line was printed.
- `bash team/scripts/run-integration-tests.sh --coverage`: **INTEGRATION_EXIT=0**, 637 passed, 1 skipped in 959 s. The same post-suite checks were OK. `test_defer_behaviour_matrix.py` passed 5/5 inside this run.
- Logs: `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester-011/unit.log` and `integration.log`.
- Direct pytest was used once, as recurrence evidence for BUG-0429 only and not as PASS evidence: 10 consecutive runs of `test_deep_verify_scheduled_dispatch.py` + `test_defer_behaviour_matrix.py` (the module order of the original failure) in a sandbox gave 10/10 at 14 passed.

### Step 5b — coverage (informational)

- Unit: line 78.4 %, branch 64.2 % (blended 75.2 %). Source: `totals` in `coverage/unit.json`, keys `percent_statements_covered` and `percent_branches_covered`, checked against the generated JSON.
- Integration: line 13.0 %, branch 10.3 % (blended 12.4 %).
### Pre-flight (Step 2 / Step 3)

- Step 2: 491 DONE task folders name a feature branch; none of those branches exists locally (all deleted post-merge), so every one passes both checks trivially. Cross-check: `git log --first-parent ai_main..ai_rc/v2.26.4` shows merge commits for CODER-002, -003, -004, -005, -006, WRITER-007, CODER-009. — pass
- Step 3: worktree HEAD 676b74f440dfd250496ad06e02f6c2518eb865c6 == `ai_rc/v2.26.4`; `git status --porcelain` empty; `git stash list` empty. — pass

### BUG-0450 — intake no-clobber (CODER-004)

- Diff: `team/pgai_agent_kanban/ops/write.py` replaces `Path.rename` with `os.link(tmp, target)` + unlink; `FileExistsError` → same `OpsError` as the pre-check. `intake.sh` help text and `docs/operator-commands.md` updated in the same commit (Documentation Discipline). — pass
- Behavioral (sandbox root): sequential second deposit → exit 3, first content intact. 20 parallel trials of two `intake.sh` deposits of the same `BUG-9999-x.md`: 20/20 exactly one exit 0 + one exit 3, winner's content intact, no `.deposit_intake_tmp_*` litter. — pass
- Forced race (target written between pre-check and placement by patching `Path.exists`): RC raises `OpsError ... (no clobber)`, target keeps `content-A`, no temp litter; the same harness against `ai_main`'s `write.py` returns success and the target is overwritten with `content-B` — original failure mode reproduced on main, prevented on RC. — pass

### BUG-0451 — api-server.sh PYTHONPATH and stop guard (CODER-005)

- Diff: start exports `<script package root>:<caller PYTHONPATH>` and launches `setsid python3 -m pgai_agent_kanban.api.main` without overriding it; `stop` signals only through `_signal_api_server_group`, which requires the target's `ps -o args=` to name the entrypoint, refusing dual-channel (`api-server.stop.target_not_api_server`, census anchor present); a pidfile whose pid is alive but not the API reads as stale. Help text and `docs/operator-api.md` env table updated. — pass
- Behavioral, unrelated listener (`python3 -m http.server 8391 --bind 127.0.0.1`, sandbox root, `PGAI_API_PORT=8391`): RC `status` says "not the API server ... stop will not signal it"; RC `stop` exits 1 with the dual-channel refusal and the listener SURVIVES. Same probe with `ai_main`'s script: `stop` exits 0 and the listener is KILLED (original failure reproduced). — pass
- Behavioral, stale pidfile whose pid was reused by `sleep 300`: RC `status` → "stale pidfile removed (pid N is alive but is not the API server: sleep 300)", "not running", exit 1; `stop` signals nothing, sleep SURVIVES. `ai_main`: `status` → "running (pid N)", `stop` KILLS the sleep. — pass
- Behavioral, new normal case: `start` from the dev tree with caller `PYTHONPATH=<sandbox>/extra-pp` → server `/proc/<pid>/environ` holds `PYTHONPATH=<worktree>/team:<sandbox>/extra-pp`; server answers HTTP on 127.0.0.1; tracked `stop` exits 0 and the server is gone. — pass (note: bug text said "appends"; implementation prepends its own root and preserves caller entries after it, which satisfies the acceptance "dev-tree modules importable when started via the script" and matches SOP-core's live-install/dev-tree boundary)

### BUG-0452 — task-id shape and worktree delete guard (CODER-006)

- Diff: `task_ids.sh` gains `kanban_task_id_pattern` / `kanban_task_id_is_canonical` (`^(CLAUDE-)?[A-Z]+-[0-9]{8}-[0-9]{3}-[a-z0-9-]+$`); new `wake_task_id_gate.sh` refuses malformed ids dual-channel in the pending/WAITING/BLOCKED scans and in `check_prerequisites` (CONVERT, census anchors present); `worktree.sh` gains `pgai_worktree_path_strictly_inside` (realpath -e both sides, strict descendant) in front of the only `rm -rf`, refusing dual-channel (`worktree.residue.outside-base`, BLOCK-WORTHY). — pass
- Behavioral, queue scan: backlog with `- [ ] ..`, `- [ ] ../x`, `- [B] ..`, `- [W] a.b` and one good line → three `wake.queue.malformed-task-id` refusals naming file+line, stdout carries only the good id. `check_prerequisites` with `- ..` → `wake.prerequisite.malformed-task-id` refusal, `..:MALFORMED`, exit 1. Shape check: `..`, `../x`, `a.b`, `CODER-...-Bad_slug` refused; `CODER-20260930-001-good` and legacy `CLAUDE-20260412-001-legacy` accepted. — pass
- Behavioral, delete guard: `<base>/..`, `<base>` itself and a symlink inside base pointing outside → refused by the predicate; `create_task_worktree ..` and `create_task_worktree <symlink-out>` both exit 1 with the dual-channel refusal, and nothing outside the base was deleted (sentinel files intact). A genuine orphaned residue dir inside the base is still removed and the worktree created (exit 0). — pass
- Behavioral, reconcile sweep (the bug's own Reproduction path: "append a queue line with id `..`; run the stale-worktree reconcile; observe the computed path"): see Finding 1 — `reconcile_stale_active` still matches `[A-Za-z0-9._-]+`, accepts `..` with no refusal, computes worktree path `<base>/..` and hands `<TASKS_ROOT>/../status.md` to the state writer. — gap (Finding 1)

### BUG-0427 — sibling runners' per-invocation basetemp (CODER-002, WRITER-007 docs)

- Diff: `run-gated-tests.sh` and `run-cold-build-tests.sh` claim `gated_runner_pytest_basetemp gated|cold` and release it through `CLEANUP_ON_EXIT_PRE_HOOK`; no fixed `pytest/gated` / `pytest/cold` path remains; cold runner's private trap body replaced by `lib/script_common.sh`'s shared one. `docs/test-authoring-guidelines.md` names both siblings and their `<suite>` values. — pass
- Behavioral (sandbox temp root, docker pointed at a dead socket so image tests skip, a pytest plugin via `PYTEST_ADDOPTS` that claims the basetemp, writes a marker, sleeps, and reports whether its marker survived): two overlapping RC `run-gated-tests.sh` runs held `pytest/gated-pid498484.tWlzHa` and `pytest/gated-pid499238.HtnvGO` simultaneously; both markers survived; both exit 0 (4 passed, 6 skipped); `pytest/` empty after exit. Same harness with `ai_main`'s runner: both runs used `pytest/gated`, the first run's marker was deleted by the second (`marker survived=False`), and `pytest/gated` was left behind — original failure reproduced, fixed on RC. — pass
- `run-cold-build-tests.sh` was NOT executed (Step 5d: TESTER does not run the cold-build lane); its claim/release wiring is verified by diff reading and by `test_gated_runner_per_run_basetemp.py` (python3 stand-in for pytest) which ran inside the gated unit suite. — pass-with-caveat

### BUG-0429 — discovery finder stdout contract (CODER-003)

- Diff: `_disc_find_unhandled_items` now runs its quarantine and settle phases inside `{ ... } 3>&1 1>&2`, writing validated paths only through fd 3, and drops+narrates any scan line not starting with `/`; nothing before that block writes to stdout (only `$(...)` captures). Both step guards call `_disc_scan_output_has_item_paths`, which logs every rejected non-path line verbatim. `test_defer_behaviour_matrix.py` quotes the finder's raw stdout on failure. — pass
- Behavioral (sandbox bugs dir with two eligible files, one with a fresh settle marker, one malformed name; `log()` deliberately writing to STDOUT): RC stdout = exactly the two absolute paths, 0 non-path lines, all narration (rejection, quarantine after 3 parses, settle deferral) on stderr. `ai_main`'s library under the same harness leaked `LOGLINE: ... settle guard active ...` (and, on the rejection pass, four `_disc_maybe_quarantine` lines) into stdout — the exact mechanism that makes the step guard skip the bundle. — pass
- Guard diagnostic: `_disc_scan_output_has_item_paths discovery_step_bugs "stray narration A\nstray B"` → summary line plus `rejected non-path line: 'stray narration A'` / `'stray B'`, exit 1; an absolute path → exit 0. Acceptance bullet 1 met. — pass
- Recurrence (acceptance bullet 2): the original flake's root cause was not identified (CODER-003 says so explicitly); the contract is now enforced structurally and self-diagnosing. Evidence of no recurrence: CODER-003 forward+reverse order-independence pass over the module neighbourhood, and this verification's own integration run (see Step 5a). — pass-with-caveat (mitigated + instrumented, not root-caused)

### BUG-0430 — provenance hygiene finding (CODER-009)

- `python3 team/scripts/lint_hygiene.py --classes provenance` on the RC: `OK — 0 findings across 9 scan director(y/ies)`, exit 0. Line 44 of `release-notes/v2.25.2.md` reworded (task-ID path removed); the residual quoted-stderr token is allowlisted with a specific justification (`hygiene-lint-allowlist.json`); four further release-notes provenance hits (v2.26.0, v2.26.2 ×2, v2.26.3) reworded. — pass

### BUG-0453 — OVERWATCH role file vs repo lock (WRITER-007)

- `team/roles/OVERWATCH.md` pre-flight step 2 (line 62) and the per-repo wake lock section (line 74) now state: read-only scans take no lock; `overwatch_halt_first_fix` acquires the project repo lock before acting, holds it for the fix, releases on return, defers with sentinel 4 when contended — matching `overwatch_protocol.sh` (`pgai_lock_acquire` line ~430, `return 4` line ~434, release ~460). No sentence still says OVERWATCH never takes the lock. — pass
- The HALT-first paragraph in the same file (line 133) still says OVERWATCH "check[s] the per-repo flock" and "creates `$KANBAN_ROOT/HALT`", whereas the code acquires/holds the lock and touches the project-scoped `projects/<name>/HALT` (SOP-core "HALT Flags": OVERWATCH repairs never write the fleet file). The bug's "a doc-truth lint or unit test pins the chosen invariant" is met on the code side only (existing `test_overwatch_lock_halt_protocol.py` test (C)); nothing pins the text. — gap (Finding 4)

### Bundle-level acceptance criteria

- "Each bundled item is addressed": 0427, 0429, 0430, 0450, 0451 addressed and behaviorally verified; 0452 and 0453 addressed with residual gaps (Findings 1 and 4). — pass-with-caveat
- "All Python files compile cleanly": `python3 -m compileall -q team/` exit 0. — pass
- "All bash files pass syntax check": `find team -name '*.sh'` → 302 files, `bash -n` 0 errors. — pass

### Step 6.1 — release-state sanity

`## Active RC` v2.26.4, `## RC Opened By Task` CM-20260930-010-open-rc-2-26-4, `## Last Released` v2.26.3 (well-formed). `pp_last_released_version pgai-agent-kanban` → v2.26.3; `semver_gt v2.26.4 v2.26.3` → true. — pass

### Step 6.8 — synthetic release simulation

Skipped. The diff touches no release-lifecycle file: no `cm-release.sh`, `cm-open-rc.sh`, `cm-cancel-rc.sh`, `project_paths.sh`, or open/close-cycle script.

### Step 6.9 — constraint negative checks

No secrets or hardcoded credentials in the added lines. Modified scripts keep `set -euo pipefail`. The new refusal sites carry census class comments and `CENSUS-ANCHOR` lines: `api-server-stop-target-not-api-01` (BLOCK-WORTHY), `worktree-residue-outside-base-01` (BLOCK-WORTHY), `wake-task-id-gate-malformed-queue-id-01` and `...-prerequisite-id-01` (CONVERT). Both gated runners' census lint passed. — pass

### Step 6.10 — prerequisite graph

PM-001 → CM-010 (open) → CODER-002…006 and WRITER-007 (WRITER-007 also waits on CODER-002) → CODER-008 → CODER-009 → TESTER-011 → CM-013 (release, WAITING). There are no cycles and every referenced ID exists. The bookends are linked. Sequence numbers do not collide (012 is unused). — pass

### Step 6.11 — spot-check outside the spec

- **Stricter prerequisite parsing.** Non-canonical tokens now hold a task in WAITING instead of being silently ignored. I scanned every live project's queues and task READMEs: 0 non-canonical prerequisite tokens, and 0 non-canonical ids on `[ ]/[W]/[B]/[A]` lines of any agent queue. The one hit is in `bug_backlog.md`, which the wake scans do not read. No live-data regression. — pass
- Findings 2 and 3 came from this step: pre-existing issues next to the BUG-0451 fix, reproduced and not taken from CODER notes. The state-drift check is covered by 6.1 and 6.12. — see Findings

### Step 6.13 — skip-cites-real-bug gate

No placeholder skip annotations in the RC's tests; the only `BUG-NNNN` placeholder text is inside archived lint logs under `team/tests/artifacts/`. No new skip annotation in the RC diff cites a bug ID. Both runners' lint pre-flight passed. — pass

### Step 6.14 — coding-standards spot-check

The added non-test, non-release-notes lines carry no bug, task or version citations. The one new shell file (`lib/wake_task_id_gate.sh`) is a sourced library, not a CLI, and was added to `lint_engine_size_dupe.py`'s ENGINE_FILES. There is no new hardcoded operational value. The shared helpers are reused (`kanban_task_id_pattern` is the single source of the regex, read by both bash and the python heredocs). — pass

## Lint Findings (non-blocking)

- `engine_size_dupe` — `lib/wake_driver_invoke.sh` is 809 lines against an 800-line ceiling, in both unit and integration runs. It predates the RC (809 lines on `ai_main` too). Cites BUG-0454-wake-driver-invoke-exceeds-line-ceiling (open).

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=22d freshness=stale — The record is stale on both counts: it is 22 days old against the 7-day window, and it was taken on v2.9.20, not v2.26.4. Its claude-abliterated build failure was filed by that run as BUG-0167 (now done), and nothing newer is recorded because this project's `project.cfg` sets `deep_verify = false`.

## Autonomous criterion

1. Tasks force-promoted WAITING→BACKLOG by hand? **NO.** Every cycle task's promotion note is the wake script's, and the queue markers match each task's `## State`.
2. `release-state.md` edited outside the CM scripts? **NO.** Its mtime (2026-09-30T00:22:55Z) equals `## RC Opened At`, written by CM-20260930-010.
3. Scripts patched mid-build? **NO.** No commit landed on `ai_main` during the RC. Every RC commit came from a cycle task's feature branch, merged `--no-ff`. No file under the live install's `scripts/`, `roles/` or `pm-agent/` changed after the RC opened.
4. Queue files edited by hand? **NO** evidence. The markers for all 12 cycle tasks agree with their `status.md`.
5. Remote git operation by a non-CM agent? **NO.** No cycle task folder has a `logs/git-boundary-guard.jsonl`. This task ran no fetch, pull or push.

Autonomous operation criterion: Manual interventions required: none

## Caveats

- **BUG-0429.** The root cause was not identified. The fix enforces the finder's stdout contract structurally and makes both the guard and the test failure self-diagnosing, and it neutralises the mechanism the bug describes (a stdout-writing `log()`). Recurrence evidence: 10/10 targeted reruns, one full gated integration run, and CODER-003's forward and reverse order pass. A trigger that is not a stdout leak would still surface, but now with the offending content named.
- **BUG-0451, "appends".** The bug's Expected said "start appends to PYTHONPATH". The implementation puts the script's own package root first and keeps the caller's entries after it, and it no longer reads `PGAI_DEV_TREE_PATH`. This meets the acceptance ("dev-tree modules importable when started via the script") and SOP-core's live-install/dev-tree boundary, so it is recorded as pass.
- **Observation, not a finding.** `_pid_is_api_server` matches the entrypoint module name as a word anywhere in the command line, so a process whose arguments merely mention `pgai_agent_kanban.api.main` would pass the stop guard. This is theoretical; I did not probe beyond the bug's scope.
- **Observation.** `run-unit-tests.sh --help` rewrites `${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-unit.json` (0 findings) as a side effect. This is harmless but surprising for a help invocation. The final findings files used here were copied immediately after each real run.
- **`run-cold-build-tests.sh`** was not executed (Step 5d); see the BUG-0427 section.
- **Probe hygiene.** All probes ran under `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester-011/sandbox-*` with a sandboxed kanban root. `ai_main` comparison code was extracted with `git archive`, so no checkout moved. The API and listener probes bound 127.0.0.1 on ports 8391–8393 only. The worktree was still clean at HEAD 676b74f4 after the suites and probes. No stray processes were left.
- **Git workflow.** The wake prompt says not to create a feature branch in this detached worktree, and TESTER.md says process artifacts are never committed into a dev tree. So `report.md`, `gaps.md` and the bug filings live only in the task folder and `bugs/`, and nothing was committed to the RC. This follows the wake prompt, which takes precedence, over the role file's "commit artifacts on a local feature branch" line.
