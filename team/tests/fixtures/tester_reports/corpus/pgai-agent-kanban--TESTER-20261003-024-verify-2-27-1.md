# Verification Report: rc/v2.27.1

## Summary

Verified RC `ai_rc/v2.27.1` (HEAD `a898029c`) against `requirements/v2.27.1-bugfix-bundle-20261003.md`, a 27-bug bundle:

- three CM release-script defects: BUG-0491 (cancel-rc state reader), BUG-0492 (GitHub release ignoring `push_to_remote`), BUG-0493 (release-state rewrites dropping appended event blocks);
- two hygiene ride-alongs: BUG-0494 (release summary text in local mode), BUG-0495 (operator window layout);
- twenty quarantined timing- or order-dependent tests (BUG-0496 to BUG-0515);
- two certification-tooling environment bugs (BUG-0516, BUG-0517).

The bundle was implemented by 12 tasks, all merged with `--no-ff`. Both gated runners exited 0: unit 6410 passed and 43 skipped, integration 638 passed and 1 skipped, nothing deselected.

The original failure modes of BUG-0491, BUG-0493 and BUG-0516 were reproduced (on `ai_main`, or in the old environment shape) and shown fixed on the RC. BUG-0495's geometry was measured on a private tmux socket. All 20 un-quarantined tests passed 20 of 20 iterations idle and 10 of 10 with CPU spinners on every core. The full order-independence sweep found no order coupling.

One acceptance gap was found and filed: hand-written docs still describe the removed suggested-commands pane (BUG-0518). One quality-class lint finding was filed (BUG-0519); it does not affect the recommendation.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
low

## Findings

### Pre-flight and structure

- **Step 2, RC intact:** pass.
  - All 12 input tasks are `DONE`.
  - Each `ai_feature/<task>` branch is deleted, which is the normal state after a merge.
  - Each task has a `--no-ff` merge commit on `ai_rc/v2.27.1` that contributes at least one unique commit (`git rev-list --count M^1..M^2` gave 1/1/2/1/2/3/1/2/2/3/5/1).
- **Step 3, worktree:** pass.
  - `git rev-parse HEAD` equals `ai_rc/v2.27.1` (`a898029c`).
  - `git status --porcelain` and `git stash list` were empty at the start and again at the end.
- **Requirements AC "All Python files compile cleanly":** pass. All 610 tracked `.py` files parse with `ast.parse`, 0 failures.
- **Requirements AC "All bash files pass syntax check":** pass. All 307 tracked `.sh` files pass `bash -n`, 0 failures.
- **Requirements AC "Each bundled item is addressed":** pass-with-caveat. All 27 items are implemented (per-item results below). The caveat is BUG-0495's docs criterion (Finding 1).

### Step 5a / 6.6 / 6.7 — Gated suites

- `team/scripts/run-unit-tests.sh --coverage`: **UNIT_EXIT=0**.
  - 6410 passed, 43 skipped, 0 deselected, in 21m59s.
  - The lint pre-flight passed, and the green ledger recorded exit 0 at `a898029cb86f`.
- `team/scripts/run-integration-tests.sh --coverage`: **INTEGRATION_EXIT=0**. 638 passed, 1 skipped, 0 deselected, in 15m25s.
- Both runs had `PGAI_AGENT_ROLE=tester` and the wake git-boundary guard first on `PATH`.
  - No `git-boundary-guard.jsonl` was written to this task's `logs/`.
  - So every fixture-local push in the suites ran without a guard refusal, including the new release-summary and GitHub-release tests.
- Output is in `/tmp/pgai_kanban_tmp/tester/v2.27.1-024/` (`unit.log`, `integ.log`, `*.exit`).

### Step 5b — Coverage (informational only)

- Unit: line 78.35% (15399/19654), branch 64.30% (3665/5700).
- Integration: line 12.70% (2495/19642), branch 9.89% (564/5700).
- Source: the `totals` object in `coverage/unit.json` and `coverage/integration.json` (`covered_lines`/`num_statements`, `covered_branches`/`num_branches`).

### Bundled items — CM release-script fixes

- **BUG-0491 (cancel-rc state reader):** pass.
  - `grep -n "grep -m1 '^## State'" team/scripts/cm/cancel-rc.sh` returns nothing. The state is now read through `task_status.py --read-state`, the canonical reader that uses the writer's own section regex.
  - Behavioural probe: a sandbox root (`env -i`, throwaway kanban root, local-only project) with three tasks in DONE, WONT-DO and BACKLOG.
    - On `ai_main` (the original failure mode), all three tasks are listed as pending, each with an empty state `()`.
    - On the RC, only `CODER-3-live (BACKLOG)` is listed.
  - The CODER test `test_cancel_rc_skips_finished_tasks.py` also covers hand-edited forms (a blank line under the heading, lowercase, trailing spaces) and an empty state. It passed in the gated unit run.
- **BUG-0492 (GitHub release ignores push_to_remote):** pass.
  - `finalize-release.sh` Step 5 is now gated on `_CM_PUSH_TO_REMOTE` before any `gh` call, including `gh auth status`. When skipped, it prints `[push_to_remote=false] skipping GitHub release ...`. `bash -n` is OK.
  - `test_finalize_release_github_release_gate.py` puts a `gh` stub on PATH, so the real CLI is never called. It asserts both modes, and both passed in the gated unit run:
    - local-only: zero `release create` calls, the skip line is printed, and origin is untouched;
    - pushing: exactly one `release create` for the version, and the tag reaches the fixture origin.
- **BUG-0493 (release-state rewrites drop events):** pass.
  - `grep -rn 'cat > "$RELEASE_STATE"' team/scripts` returns nothing.
  - All three writers (`open-rc.sh`, `release_finalize.sh` Step 12, `cancel-rc.sh`) now call `write_rc_state.py release-state`. That writer updates the four fields in place and writes atomically (temp file plus `os.replace`, file mode preserved).
  - Same sandbox probe, with a fixture holding a `## HALT Event` block and a `## Operator Override` block:
    - On `ai_main`, both blocks are deleted.
    - On the RC, both are kept, and only the three RC field values change.
  - The writer exercised directly:
    - Simulated open and then release changed only the named value lines. The event tail stayed byte-identical (`cmp`), including a heading-shaped `## Active RC` line inside an event, which was correctly not treated as a field.
    - An empty value, a multi-line value and a call naming no field were each refused with exit 2, and the file was not written.
  - The CODER lifecycle test (open, release, open, cancel through the real entry points) passed in the gated unit run.
- **BUG-0494 (release summary claims origin push in local mode):** pass.
  - The unconditional "best-effort pushed to origin" and "deleted from origin" lines are gone. `_cm_release_print_origin_summary_lines` now prints from the outcomes recorded in Steps 10, 11 and 14.
  - Step 14 changed from a `( ... ) || true` subshell to a brace group that saves and restores errexit, so its recorded outcomes reach the summary. I read the whole block: every variable it uses under `set -u` is set earlier, and `set +e` covers every command in it.
  - `test_release_summary_origin_actions.py` drives the real finalizer against a bare-repo origin in both push modes. It passed in the gated unit run.
- **Step 6.8, synthetic release:** pass. It applies because the diff touches `open-rc.sh`, `cancel-rc.sh` and `release_finalize.sh`. `tests/integration/test_cm_release_synthetic.py` passed 18 of 18 in the gated integration run.

### Bundled items — operator window (BUG-0495)

- **Geometry:** pass.
  - Method: I sourced `team/scripts/dashboard/lib/board_window.sh` with `tmux` pinned to a private `-L` socket and built both windows at each size. `create.sh` calls this same builder for both windows (lines 1129 and 1252).
  - At 160x48:
    - Panes 0-7 have widths 18/23/21/18/18/19/18/18, identical in visibility and operator.
    - The legend is 160x1 at top 47 in both windows.
    - The operator shell is pane 8: 160x7 (15% of 48, rounded down, is 7), at top 39, directly under the 38-row board. The legend is pane 9.
    - Visibility has 9 panes and operator has 10.
  - At 237x60:
    - Panes 0-7 have widths 27/33/32/27/27/30/27/27 in both windows.
    - The legend is 237x1 at top 59 in both windows.
    - The operator shell is 237x9 (15% of 60 is 9), at top 49.
- **Census and lint:** pass.
  - Census window 1 rows 8 and 9 are now the shell and the legend.
  - `generate_window_docs.py --check` is part of the passing gated run, and the cadence lint and census tests pass.
- **Docs:** gap, see Finding 1.

### Bundled items — un-quarantined flaky tests (BUG-0496 to BUG-0515)

- **Quarantine marks removed:** pass.
  - On `ai_main`, six affected files carried `mark.quarantine`, plus `test_runner_quarantine_marker.py`.
  - On the RC, none of the six carries the mark. Only `test_runner_quarantine_marker.py`'s own fixture strings mention it.
- **Selected in certification:** pass. Neither gated run reports any deselected test. The integration log shows `test_chain_lock_reverification.py` and `test_dashboard_activation_hook_lifecycle.py` running.
- **Repeated runs:** pass.
  - Each iteration ran all 20 node IDs together in one pytest invocation with `-p no:randomly` (`flake_loop_024.sh`).
  - Idle: 20 of 20 iterations gave `20 passed` (about 45s each).
  - Under load: 10 of 10 iterations, with one CPU spinner per core (8), gave `20 passed` (about 235s each).
  - The only warning was the event-band test's own non-gating render-time narration (19.4s under load).
  - This direct-pytest loop is behavioural evidence only. The suite verdict comes from the gated exit codes above.
- **Order independence:** pass.
  - `team/scripts/verify-test-order-independence.sh --target unit` reported `VERDICT: PASS -- no order-coupling findings`.
  - It ran 6463 tests in forward and reversed order and found no verdict differences.
  - This covers the four order-dependent items, BUG-0501, BUG-0502, BUG-0506 and BUG-0507.
- **Fidelity review:** pass; no assertions were weakened.
  - The cadence tests moved from wall-clock windows to a virtual clock: a `sleep` test double records the interval the loop asks for. They now assert exact requested intervals and exact render counts instead of wall-clock gap bands (12 asserts removed, 16 added).
  - The hook-lifecycle and event-band tests moved from fixed timeouts to the shared stall guard `tests/fixtures/process_progress.py`, which fails on no progress rather than on elapsed time. Their assertions are unchanged.
  - The chain-lock and local-only tests only gained asserts.
- **Production changes in this group:** pass; read and exercised.
  - `dashboard/lib/cadence.sh`: the activation flag is now reset before the render rather than after it, and the signal handler kills the pending sleep. This closes a race where an activation signal could be lost.
  - `dashboard/lib/pane_readiness.sh`: `dashboard_wait_all_armed` now uses a stall bound (time since the last pane armed) instead of a total-elapsed bound.
  - Step 6.5 check of that guard with a 2s bound:
    - a pane that never arms is refused: `rc=1`, and the stuck label is named;
    - four labels arming one every 1.5s (6s in total) are accepted: `rc=0`.

### Bundled items — certification tooling

- **BUG-0516 (order sweep's guard refuses fixture pushes):** pass. The guard was live (role `tester`) for all of these checks.
  - The bug's step-2 shape (basetemp outside the inner temp root) gives `5 failed` and 5 guard records, which reproduces the original failure.
  - The fixed `_voi_run` shape (basetemp inside the pass's temp root) gives `68 passed` across `test_cm_release_step3_state_detection.py`, `test_overwatch_push_lag_bash.py` and `test_git_boundary_guard.py`, with no guard record.
  - The new `test_order_independence_guarded_fixture_push.py` passed in the gated unit run.
  - The full sweep, run from this non-CM wake, exited 0 and left this task's `logs/` with no `git-boundary-guard.jsonl`. This is acceptance item 3.
- **BUG-0517 (AP7 env coupling in acceptance-policy test):** pass.
  - `python3 scripts/lint_test_anti_patterns.py`, run from `team/` as the runner does, reports `OK -- 0 findings across 637 file(s)`.
  - `test_lint_golden_env_coupling.py` passed in the gated unit run.

### Finding 1: Hand-written operator-window docs still describe the removed suggested-commands pane

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated documentation drift; no runtime effect)
**Filed As:** BUG-0518 (Path C-A: filed as a BUG — code defect / regression / test failure)
**Filed By:** TESTER (autonomous Path C) — the operator dashboard window no longer has a suggested-commands pane, but three documents and one script header still tell operators it is there.

**Criterion:** BUG-0495 acceptance requires that "the census, cadence lint and window docs agree with the new layout".

**Expected:** every operator-facing description of window 1 names, top to bottom, the eight-column board, the full-width shell and the legend.

**Actual:** five places still describe the removed pane:
- `docs/DASHBOARD-PANES.md:35` describes "three stacked regions: board (top), suggested-commands (middle), and cmdline (bottom)". The generated table directly below it lists the shell and the legend instead.
- `docs/DASHBOARD-PANES.md:52` says "The suggested-commands pane is strictly read-only...".
- `docs/DASHBOARD-PANES.md:147` refers to window 1's "suggested-commands row".
- `HOW_TO.md:1035` says "with a suggested-commands pane below it and an interactive shell at the bottom".
- The header comment of `team/scripts/dashboard/suggested-commands.sh` still calls the script a pane.

**Why it was missed:** the WRITER ticket was scoped to `docs/dashboard.md` and `docs/OPERATIONS.md` only. It updated both correctly and recorded these other locations in its status as out-of-scope follow-ups. The generated table, the census and the code are all correct; only this prose is stale.

## Lint Findings (non-blocking)

- `engine_size_dupe` (severity `quality`, exit 3; reported in both `findings-unit.json` and `findings-integration.json`): `lib/cm/release_finalize.sh` is 846 lines, over the 800-line ceiling. It was 763 lines on `ai_main` and grew with this RC's BUG-0493 and BUG-0494 work. No open bug covered it, so I filed BUG-0519 (Path C-A). Per Step 5c, this does not change the recommendation or the systemic risk.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=26d freshness=stale — the last cold-build record is 26 days old (outside the 7-day window) and was taken at v2.9.20 rather than v2.27.1; that red run filed BUG-0167 (claude-abliterated Dockerfile parse error, since marked done, fixed in v2.9.22), so nothing is currently known about whether this RC's images build.

## Other methodology steps

- **6.1, release-state:** pass.
  - The file holds exactly the four schema fields. `## Active RC` is `v2.27.1` and `## Last Released` is `v2.27.0`, which is well formed.
  - `pp_last_released_version pgai-agent-kanban` returns `v2.27.0`, and `semver_gt v2.27.1 v2.27.0` is true.
  - The file's mtime (05:42:05.44) equals `## RC Opened At`, so open-rc wrote it.
- **6.9, constraint negative checks:** pass.
  - The scripts that call the new writers keep `set -euo pipefail`, and the Python writer runs no shell commands.
  - No secrets appear in the diff.
- **6.10, prereq graph:** pass.
  - There are no cycles, and every referenced task exists.
  - CM-023 (open-RC) is the root of all 12 work tickets. TESTER-024 depends on all 12. CM-026 (release) depends on all 12 plus TESTER-024 and is `WAITING`.
  - Sequence numbers 011-024 and 026 do not collide. No task 025 exists; approval is `auto`, so none is needed.
- **6.11, spot-checks:** pass.
  - No state drift: Active RC `v2.27.1` matches the existing `ai_rc/v2.27.1` branch.
  - Each new mechanism has dedicated tests in the gated runs: `task_status --read-state`, `write_rc_state.py release-state` and `board_window.sh`.
  - No regression of BUG-0339 (the earlier operator-window duplicate-pane bug): the window has exactly one shell pane.
  - Observation only, not a finding: `cancel-rc.sh` picks candidate tasks with `grep -q "$VERSION"`. The dots are unescaped, so `v2.27.1` would also match a README naming `v2.27.10`. This predates the RC and was not changed by it.
- **6.13, skip-cites-real-bug:** pass.
  - There are no placeholder `BUG-SKIP` shapes.
  - Every unresolved `BUG-NNNN` near a skip is either a fixture string inside `test_skip_bug_citation_scan.py` or `test_fixed_in_restamp.py`, or a historical artifact. None is a real skip annotation.
- **6.14, coding standards:** pass.
  - The new CLI modes have `--help` that names every argument (`task-status.sh --read-state`, `write_rc_state.py release-state`).
  - The added lines contain no bug, task or version provenance; the only task-ID-shaped strings are fixture data values.
  - New operational values are named constants (`PGAI_OPERATOR_SHELL_PCT`, `PGAI_OPERATOR_SHELL_MIN_ROWS`).

## Autonomous criterion

1. Were any tasks force-promoted from WAITING to BACKLOG by hand? **NO.** Every promotion line in the cycle's status files came from the wake script, and the queue markers match each task's state.
2. Was `release-state.md` edited outside the CM scripts? **NO.** Its mtime equals `RC Opened At` (05:42:05), which open-rc wrote.
3. Were any scripts patched mid-build? **NO.**
   - Every commit on the RC belongs to a task merge.
   - Nothing in the live install's `scripts/`, `subagents/`, `roles/`, `pgai_agent_kanban/` or `pm-agent/` is newer than the RC open.
   - `logs/activity.log` has no entry since 2026-09-03.
4. Were any queue files edited by hand? **NO.** The markers are consistent with each task's `## State`, and there is no evidence of a hand edit.
5. Did any non-CM agent invoke a remote git verb? **YES, explained.**
   - `CODER-20261003-017-local-only-flakes/logs/git-boundary-guard.jsonl` holds 18 records from 09:25Z. Each is a `git -C <pytest basetemp>/.../local push origin ...` made by a test fixture pushing to a fixture-local bare repository during that task's `verify-test-order-independence.sh` sweep. The guard refused all of them, and no real remote was contacted.
   - This is exactly BUG-0516's symptom. It happened before the BUG-0516 fix (CODER-020) merged at 12:26Z, and that fix is in this RC.
   - It is not a defect beyond the bundled bug, so nothing new is filed.
   - No other task folder in the cycle has a guard record, including this one, which ran the full sweep after the fix.

Autonomous operation criterion: Manual interventions required: none. The single Q5 YES is the guard refusing fixture-local pushes; bundled BUG-0516 caused it and this RC fixes it.

## Caveats

- deep-verify: the latest record is red and stale. This is context only; see the section above.
- The flake re-runs were 20 idle iterations plus 10 loaded iterations of all 20 tests together, not 20 separate per-test loops each also under load. Together with the full-order gated runs and the order-independence sweep, I judged this sufficient evidence for the "20 passes, including under host load" criterion.
- The BUG-0495 geometry check exercised the shared builder directly on a private socket, not the full `create.sh`, which needs a live kanban root and launches renderers. `create.sh` calls that builder for both windows, and the CODER geometry test covers the same assertions.
- For the original BUG-0495 failure mode on `ai_main` (divergent widths), I relied on the bug report. I did not re-measure it.

## Progress Log (checkpoints)

- Step 1: read task README, status and requirements (27 bundled bugs, 3 acceptance criteria).
- Steps 2 and 3: pass.
- Gated unit: exit 0. Gated integration: exit 0.
- Behavioural probes done: 0491, 0493, 0495, 0516, 0517 and pane arming.
- Flake loops: 20 idle + 10 loaded, all passing.
- Order-independence sweep: PASS.
- Filed BUG-0518 (gap) and BUG-0519 (quality lint). Wrote gaps.md.
