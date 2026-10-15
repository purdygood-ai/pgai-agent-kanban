# Verification Report: rc/v2.29.1

## Summary

I verified `ai_rc/v2.29.1` at `6130794d466dd89494c449be4562e69d8c577842` against `requirements/v2.29.1-bugfix-bundle-20261005.md`, a discovery bundle of ten bugs (BUG-0542 to BUG-0551). The checklist combined each bundled bug's own `## Acceptance` with the bundle's three generic criteria.

Pre-flight passed. Both gated suites ran through the RC's new entry point `run-gated-suites.sh`, which ran the two runners concurrently in a 3-worker budget and finished in 18m53s:

- **Unit:** `UNIT_EXIT=1`. One deterministic failure, a regression this RC introduced (Finding 1).
- **Integration:** `INTEGRATION_EXIT=0`, 650 passed, 1 skipped.

Results across the ten bundled items:

- **Eight pass**, with behavioral checks: original failure modes reproduced on `ai_main`, differential and mutation tests, and production-shape renders.
- **BUG-0545 passes with a caveat** (Finding 4).
- **BUG-0550 was not addressed at all.** PM deliberately left it unticketed (Finding 2).

A spot-check also found a pre-existing pipe hazard in the dashboard's bugs-column renderer, which this RC's new serial marker hides (Finding 3). I filed three follow-ups via Path C (autonomous follow-up filing — non-blocking).

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Findings

### Finding 1: Gated unit suite is red — a new comment trips the package-wide pp_run_ops sweep test

**Category:** bug
**Fix Effort:** small (1 CODER task — reword one comment line)
**Systemic Risk:** medium (the same class recurs: a whole-tree invariant test that CODER's targeted `--files` runs never exercise; the unit suite was also red on v2.27.0, v2.27.3, v2.28.0 and v2.29.0)
**Regression:** yes
**Integrity:** breakage
**Filed As:** BUG-0552 (Path C-A (filed as BUG — code defect / regression / test failure))
**Filed By:** TESTER (autonomous Path C) — a comment added for the new status-line loop quotes the forbidden bare package-invocation form, so a package-wide test that scans every line fails and the unit suite is red.

`run-unit-tests.sh` (via `run-gated-suites.sh`, `--workers 2`) ran two lanes:

- **Parallel lane:** `1 failed, 8729 passed, 43 skipped` in 1016 s.
- **Serial lane:** `5 passed`.
- **Combined:** `Lane exit codes: 1 0 -> combined exit code 1`.

```
FAILED tests/unit/test_pp_run_ops_sweep.py::test_no_bare_python3_module_invocations_outside_exemptions
E   ... 1 bare 'python3 -m pgai_agent_kanban' invocation(s) found outside the exemption list:
E       team/scripts/dashboard/status-bottom.sh:153:# cwd-independent "python3 -m pgai_agent_kanban.*" runner used below.
```

- **Origin of the line.** Commit `a20e9f36` (CODER-20261005-007, the BUG-0544 work) added it. The test file is unchanged by this RC.
- **Deterministic.** It is a static scan. Re-running the test directly (debugging aid only) gives `1 failed, 3 passed` on the RC and `4 passed` on an `ai_main` extract (`a3b988b4`).
- **Not a stale assertion.** The test's invariant did not change; new production text violates it.
- **Runtime impact.** None, because the line is a comment. The defect is a red certification suite.
- **Why `medium`, not `high`.** Each recurrence of this class is a distinct small fix rather than one fix failing repeatedly. The class itself recurs because CODER's targeted runs never execute package-wide invariant tests. That process gap is worth an operator look, but I have no evidence the loop cannot heal it.

### Finding 2: BUG-0550 (reason code untaught to agents) was bundled but not addressed

**Category:** gap
**Fix Effort:** small (a governance-text edit; under the operator's policy this is a manual hand-patch, not a CODER task)
**Systemic Risk:** medium (will recur: PM skips governance-only items by policy, and cm-release Step 16b marks every bundled item `done`)
**Filed As:** BUG-0553 (Path C-A)
**Filed By:** TESTER (autonomous Path C) — the bundle promised to document the status tool's block reason code for agents, but no change landed, so agents still cannot request the operator-data triage action.

- **Criterion.** Bundle criterion "Each bundled item is addressed in the implementation", plus BUG-0550's Acceptance: SOP-core names `## Block Record` and `--reason-code`/`--action-args`, and every self-blocking role's read-order documents explain them.
- **Actual on the RC.**
  - `team/SOP-core.md:61` still reads "**It manages four fields and no others.**"
  - `grep -rln -- "--reason-code\|agent_operator_data_action" team/roles team/SOP-core.md subagents team/read-order` returns nothing (exit 1).
  - No commit on the RC touches SOP-core, roles or read-order.
- **Cause.** PM-20261005-001 deliberately did not ticket BUG-0550, following the operator's standing instruction that governance text is hand-patched. PM recorded the conflict and predicted this report.
- **Why a new bug.** BUG-0550 is still `running` and will be auto-closed at release without a fix. BUG-0553 carries it forward so it is not silently lost. It notes that the operator must hand-apply it or exempt it from auto-close.

### Finding 3: Dashboard bugs-column renderer can exit 141 (SIGPIPE under pipefail); the new serial marker hides the symptom

**Category:** bug (pre-existing — not introduced by this RC)
**Fix Effort:** small
**Systemic Risk:** low
**Filed As:** BUG-0554 (Path C-A)
**Filed By:** TESTER (autonomous Path C) — the bugs-column renderer pipes into `head -n1` under `pipefail` and can die with exit 141; this RC moved the one test that caught it into the serial lane instead of fixing the cause.

- **What CODER-006 saw.** In its parallel-safety sweep (BUG-0545 #3), `test_defer_card_glyph_placement.py::...::test_no_color_mode_renders_the_id_alone_with_no_glyph_at_all` failed once. The `column-render.sh` subprocess exited 141 beside xdist workers. CODER marked the test `serial`, as BUG-0545 instructs, and flagged a suspected production cause.
- **The cause I confirmed.**
  - `team/scripts/lib/dashboard/column_render_defer.sh` (unchanged since v2.13.0) runs four `printf '%s\n' "$out" | head -n1 | cut ...` pipelines under `column-render.sh`'s `set -euo pipefail`.
  - The same pipeline over an output larger than the pipe buffer exits **141** deterministically (pure bash, no load).
  - 2,000 small-output iterations on an idle host did not fail, so the realistic case is a load-dependent race.
  - The tree already documents this exact hazard in `column-render.sh`'s `--help` handler.
- **Why it matters.** A transient dashboard pane render failure only, rated low. But the serial marker removes the test's ability to catch it, so it is worth fixing at the source.

### Finding 4: BUG-0545 (gated suites run serial with coverage) — met

**Category:** pass-with-caveat

| Acceptance item | Result |
|---|---|
| `requirements-test.txt` contains `pytest-xdist>=3.8` | pass |
| Both Dockerfiles install via `-r requirements-test.txt` and name no pytest package (grep), parse cleanly | pass. Nothing after the new `WORKDIR /opt/requirements` depends on a relative path. The unquoted `pytest>=7` redirection in rocky9 is gone. |
| `workers=1` -> argv has no `-n` | pass (planner: workers 0 and 1 give one lane, no `-n`) |
| Total workers never exceed `workers` | pass. Planner: `-n W` equals the budget. `--requested-workers 9` with budget 3 gives `-n 3`, and 2 gives `-n 2`. Entry point: unit `--workers 2` plus integration `--workers 1` makes 3. |
| xdist absent -> narrated serial line, never a failure | pass ("Parallel workers: none -- ... pytest-xdist is not importable ...; running serially in one process.") |
| Same tests collected | pass. The lanes partition the suite: serial lane `5 passed, 8773 deselected` plus parallel lane 8,773 gives 8,778 (= durations `test_count`). |
| `run-gated-suites.sh` writes `unit.exit`, `integration.exit`, both findings files | pass (observed this run). The failing-integration-fixture case is covered by `test_one_failing_integration_test_gives_integration_one_unit_zero_and_a_nonzero_run`, which runs the real runners. |
| `--coverage` gate | pass, in all 12 config x env x wake cases. `coverage=false` alone gives the narrated "coverage disabled by config" line. `PGAI_TESTING_COVERAGE=1` collects only when `PGAI_AGENT_WAKE_ACTIVE` is not `1`. `coverage=true` collects. Malformed `workers` / `coverage` values are refused dual-channel. The wake bootstrap unsets the variable. |
| `serial` marker registered; crontab weekly coverage line | pass (`team/pyproject.toml`, all four crontab templates) |
| Wall time (not a gate) | Both suites concurrently: **18m53s** wall (unit 1070 s of test time at 2 workers; integration 1099 s at 1 worker). Previously about 46 min sequentially. My own ad-hoc probes overlapped the first ~10 minutes and added CPU contention. |

Serial-marked tests from the one-time parallel-safety sweep, recorded here as BUG-0545 #3 asks:

- `tests/integration/test_defer_card_glyph_placement.py::TestDeferredCardGlyphIsATrailingSuffix::test_no_color_mode_renders_the_id_alone_with_no_glyph_at_all`: exit 141 beside xdist workers, passes alone. See Finding 3.
- The remaining `serial` markers (`test_run_gated_suites.py` x2, `test_runner_parallel_lanes.py` x2) are tests that start xdist workers of their own; they were written serial, not found by the sweep.

Caveat: the target of "about 15 min" is approached but not met at the default budget. The integration suite, on 1 worker, is now the long pole at about 18 min.

### Finding 5: BUG-0548 (sweep test pushes live tags) — fixed

**Category:** pass-with-caveat

- **Original failure reproduced on `ai_main`.** Using the network stub and a throwaway dev tree with a local-only tag, `test_sweep_runner_discovers_and_runs_block_triage` logged `git -C <tmp>/dev ls-remote --tags origin` and then `push origin refs/tags/ai_v9.0.0`.
- **Same setup on the RC.** No network call.
- **Guard exercised directly.** `check-push-lag.sh` with a project config naming no `dev_tree_path` and an inherited `PGAI_DEV_TREE_PATH` prints `... names no dev_tree_path; ... skipping`, exits 0 and makes no network call.
- **Isolation.** `conftest.py` now removes `PGAI_DEV_TREE_PATH`. The new `test_overwatch_sweep_stays_off_remotes.py` asserts the full sweep makes no push or ls-remote through the stub.
- **Guard log.** This task's `logs/git-boundary-guard.jsonl` holds no record from either gated runner. Its one record is my own probe (Autonomous criterion, question 5).

Caveat: the wake guard records only `fetch`/`pull`/`push`, so "no ls-remote record" in the guard log is vacuously true. The `ls-remote` half is proven only by the stub-based regression test, which does assert it.

### Finding 6: BUG-0551 (block_stage_deferral by-path guard) — fixed

**Category:** pass

- **Change.** Package imports moved to module level below the self-location guard.
- **Result.** `test_package_modules_run_by_path.py -k block_stage_deferral`: RC `3 passed`; `ai_main` `1 failed, 2 passed` (KeyError reproduced).
- **Full run.** The full file passed inside the gated unit run.

### Finding 7: BUG-0542 (REST image split diverges from harness) — fixed

**Category:** pass

- **Differential test.** I compared `split_image_entry` against real bash `IFS=$', \t' read -r -a` over 1,715 distinct inputs (the bug's list plus 3,000 random strings over `a b , space tab`): 0 mismatches.
- **Route probe** (both `/operations/deep-verify` and `/operations/cold-build-now`, dry-run and real, `subprocess.Popen` patched):
  - `"debian,,rocky9"`, `[",debian"]` and `""` give 422 `container_tier.unknown_image` with a remedy; Popen is not called.
  - `"debian"`, `"debian,"` and `"all"` give 200.
  - A whitespace-only entry now gives 200, aligned with the harness (which lists the images and builds nothing). The module docstring states this.
- **Parity test.** The shipped test drives the harness's own `container_tier_select_images`.

### Finding 8: BUG-0543 (package CLI help exits non-zero) — fixed

**Category:** pass

All five modules (`deep_verify/{bug_filer,freshness,schedule}.py`, `lint/bug_filer.py`, `ops/resolve.py`), run from `/` with `PYTHONPATH` unset:

- `--help` and `-h` exit 0, print full usage naming every positional argument, and print no traceback.
- `python3 -m ... --help` exits 0.
- No-argument exits are unchanged (2/2/2/2/1).
- `_HELP_USAGE_ERROR_EXIT` is removed.
- `ai_main` reproduces `--help` exits of 2/2/2/2/1.

### Finding 9: BUG-0544 (status line reruns every second) — fixed

**Category:** pass-with-caveat

- **Status format.** `status-format[2]` is now `#{@pgai_status_bottom}`, filled by `status-bottom-loop.sh`. Nothing in the format forks.
- **Cadence test.** `test_two_attached_clients_cost_one_render_per_interval` (private `tmux -L`, two pty clients, 60 s, at most 13 renders counted by process start) ran and passed in the integration suite (66 s). tmux is present, so it was not skipped.
- **Byte-identical renders.** RC and `ai_main` renders matched (both with the helper resolvable) for HALT GLOBAL, project HALT, HALT-AFTER `coder` / `rc:vX.Y.Z` / invalid token, and global HALT plus project HALT-AFTER, in NO_COLOR and rich modes, global and drill bars.
- **Lint.** `lint_tmux_format_jobs.py` exits 0 on the shipped tree, and exits 1 with a dual-channel message on a seeded `#(echo hi)` in status-right, `pane-border-format` and `window-status-format`.

Caveat (not filed — quality lint precision): the lint also flags `##(`, which tmux renders as a literal `#(` and is not a job. No shipped code uses it. The same commit introduced Finding 1's comment.

### Finding 10: BUG-0546 (runners do not record slowest tests) — fixed

**Category:** pass

- **This run.** It wrote `lint/durations-unit.json` (25 tests, `total_seconds` 1070.188, `test_count` 8778) and `lint/durations-integration.json` (`total_seconds` 1098.979, `test_count` 651). Copies are in this task's `artifacts/`.
- **Shipped end-to-end tests** cover the 1.2 s fixture test being named, a `--files` run writing no file, and exit-code neutrality.
- **Template.** `REPORT-TEMPLATE.md` gains "Slowest tests".

### Finding 11: BUG-0547 (taskbar omits project deferral) — fixed

**Category:** pass

- **Fixture** (A with `DEFER-LOW-BUGS until=v2.33.0` and two open low bugs; B with none):
  - `DEFER A <=low until v2.33.0 (2 waiting)`, nothing for B.
  - With zero bugs: `(0 waiting)`.
  - Malformed campaign file: `DEFER CONFIG UNREADABLE: <path>`.
  - kanban.cfg deferral: `DEFER GLOBAL <=low (0 waiting)`.
- **Production shape** (RC script, live root, read-only): `v2.28.4 | PM:auto | DEFER pgai-agent-kanban <=low until v2.33.0 (0 waiting) | ...`. Zero open low bugs confirmed by counting `bugs/`.
- **HALT/HALT-AFTER** segments are identical to `ai_main` (Finding 9).

### Finding 12: BUG-0549 (nudge skips pre-dispatch blocks) — fixed

**Category:** pass

- **Flag coverage.** All 13 `LAST_TASK_FINAL_STATE="BLOCKED"` sites in `wake_driver{,_prep,_worktree}.sh` now set `_chain_blocked_a_task=true`. The one remaining `mark_backlog ... "B"` without that state is the stuck-in-WORKING marker, which is not a BLOCKED transition.
- **Tests.** `test_on_block_overwatch_trigger.py`: 10 passed.
- **Mutation check.** Removing the flag at the two `worktree_create_failed` sites in a scratch copy makes `test_pre_dispatch_block_nudges_once_after_the_lock_release` fail, so the test catches the regression.

### Finding 13: Bundle generic criteria — Python compiles, bash syntax

**Category:** pass

712 `.py` files `py_compile` cleanly; 321 `.sh` files pass `bash -n`. 0 failures.

### Finding 14: Methodology checks (6.1, 6.3, 6.8-6.10, 6.13, 6.14)

**Category:** pass-with-caveat

- **6.1.** `pp_last_released_version` = `v2.29.0`; `semver_gt v2.29.1 v2.29.0` is true. `release-state.md` is well-formed. Its mtime equals the RC-open time (03:55:43), so it has not been edited since.
- **6.3.** WRITER docs (`tester-verification-reference.md`, `test-authoring-guidelines.md`, `dashboard.md`, `DASHBOARD-PANES.md`, `REPORT-TEMPLATE.md`) match the observed behavior. Caveat: the reference tells TESTER to write `not collected` for config-disabled coverage, while the live role file's Step 5b fallback word is `coverage unavailable`. The live role file's Step 5a still names the two runners separately. The bug notes the operator updates the role file separately, so this is not filed.
- **6.8.** No release-lifecycle file (`cm/*`, `project_paths.sh`) changed. `test_cm_release_synthetic.py` nonetheless passed inside the green integration run.
- **6.9.** No secret-shaped strings in the diff. New entry scripts use `set -euo pipefail`. `status-bottom-loop.sh` uses `set -uo pipefail` (a long-lived loop; observation only).
- **6.10.** The prerequisite graph is acyclic and every ID exists. CM-009 (open RC) is first; TESTER-010 then CM-012 (release) are last.
- **6.13.** No placeholder skips. Every `BUG-NNNN` in a skip context is a synthetic fixture string inside the skip-gate lint's own tests.
- **6.14.** Every new entry point answers `--help` with exit 0 (`run-gated-suites.sh`, `status-bottom-loop.sh`, `gated_suites.py`, `gated_runner_lanes.py`, `gated_runner_durations.py`, `status_line`). Bug-shaped IDs in added code are fixture values only.

## Gated Suites (Step 5a)

| Suite | Runner | Exit | Result |
|---|---|---|---|
| unit | `run-unit-tests.sh --workers 2 --no-scratch-sweep --coverage` (via `run-gated-suites.sh --coverage --out-dir ...`) | **1** | parallel lane 1 failed / 8729 passed / 43 skipped; serial lane 5 passed |
| integration | `run-integration-tests.sh --workers 1 --no-scratch-sweep --coverage` | **0** | 650 passed / 1 skipped |

The PASS evidence source is the two `.exit` files, `unit.exit` = 1 and `integration.exit` = 0. Each holds its own runner's exit code. The entry point exited 1. Logs are in `/tmp/pgai_kanban_tmp/tester/TESTER-20261005-010/{unit,integration}.log`.

## Code Coverage

Not collected: both runners printed `coverage disabled by config ([testing] coverage)`. Live `kanban.cfg` has no `[testing]` key, so the default is `false`, and inside a wake `PGAI_TESTING_COVERAGE` has no effect. The JSONs under `/tmp/pgai_kanban_tmp/coverage/` date from 2026-10-04 and belong to an earlier run, so they are not reported.

## Slowest tests

Informational only.

**Unit** (`total_seconds` 1070.188, `test_count` 8778, workers 2):

1. `pgai_agent_kanban.api.tests.test_fidelity.TestAdapterFidelity::test_fidelity_reset_after_close_no_flake_100x`: 92.27 s
2. `tests.unit.test_wake_watchdog_process_group_kill::test_sigterm_ignoring_provider_tree_is_sigkilled_and_gone_before_finalize`: 65.48 s
3. `tests.unit.test_self_header_help_pipe_safety::test_covered_scripts_help_exits_zero_across_repeated_invocations`: 32.31 s
4. `tests.unit.test_run_gated_suites::test_real_runners_write_the_same_evidence_through_the_entry_point`: 21.19 s
5. `tests.unit.test_dashboard_pane_visibility::test_attach_renders_the_active_window_and_a_switch_moves_rendering_with_it`: 18.96 s
6. `tests.unit.test_dashboard_pane_visibility::test_detached_dashboard_renders_nothing_after_its_arrival_renders`: 9.68 s
7. `tests.unit.test_runner_parallel_lanes::test_failing_serial_test_yields_one_nonzero_exit_and_one_findings_file[run-unit-tests.sh]`: 8.45 s
8. `tests.unit.test_runner_parallel_lanes::test_passing_parallel_run_with_empty_serial_lane_exits_zero`: 8.29 s
9. `tests.unit.test_wake_task_state_atomic_writes::test_concurrent_reader_never_observes_empty_or_truncated_file`: 8.27 s
10. `tests.unit.test_dashboard_network_stub_fixture::test_create_sh_no_tmux_full_refresh_makes_zero_network_calls`: 8.16 s

**Integration** (`total_seconds` 1098.979, `test_count` 651, workers 1):

1. `tests.integration.test_dashboard_status_line_cadence::test_two_attached_clients_cost_one_render_per_interval`: 66.38 s
2. `tests.integration.test_chain_lock_held_through_agent::test_lock_held_through_agent_sibling_defers_and_losing_chain_abandons`: 35.42 s
3. `tests.integration.test_provider_runtime_integration::test_identity_header_present_for_firing_with_and_without_work`: 15.95 s
4. `tests.integration.test_wake_stop_file_stops_batch::test_batch_started_while_stop_file_exists_dispatches_nothing_until_operator_removes_it`: 15.57 s
5. `tests.integration.test_overwatch_wake_driven_fixtures.TestCounterKeyingWakeDriven::test_same_cargo_increments_and_different_cargo_starts_fresh_family`: 15.35 s
6. `tests.integration.test_chain_lock_reverification::test_reaped_chain_stops_before_dispatch_second_chain_reaches_the_task`: 13.69 s
7. `tests.integration.test_engine_behavior_baseline_harness::test_compare_is_stable_across_invoking_shell_budget_values`: 11.79 s
8. `tests.integration.test_git_boundary_guard_wiring::test_non_cm_dispatch_puts_shim_first_on_path_and_exports_guard_variables`: 11.78 s
9. `tests.integration.test_engine_behavior_baseline_harness::test_capture_is_stable_across_invoking_shell_budget_values`: 11.62 s
10. `tests.integration.test_provider_runtime_integration::test_safeguard_refusal_retries_once_and_task_reaches_normal_terminal_state`: 11.56 s

**Change since the previous RC:** no previous record, because this is the first RC with durations files. The previous RC (v2.29.0) ran its suites serially with coverage on. Copies of both files are saved in `artifacts/durations-{unit,integration}.json` for the next RC.

## Lint Findings (non-blocking)

None. `lint/findings-unit.json` and `lint/findings-integration.json` were both written by this run (09:32:45Z and 09:32:59Z) with 0 findings each.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=28d freshness=stale — The last deep-verify run was red (its failure is recorded in BUG-0167, now done), and the record is stale on both counts: 28 days exceeds the 7-day window and v2.9.20 is not v2.29.1. This RC rewrites the Python-install step of both Dockerfiles, so nothing is known about whether these images build.

## Autonomous criterion

1. **Tasks force-promoted WAITING -> BACKLOG by hand?** NO. Every cycle promotion is a wake-script line (`wake(<role>): WAITING task ...: prerequisites satisfied, promoting to BACKLOG`, 03:58 to 09:12).
2. **`release-state.md` edited outside the CM scripts?** NO. Its mtime equals CM-009's open (03:55:43), and the content matches CM-009's verified state.
3. **Scripts patched mid-build?** NO. No file under the live install's `scripts/`, `roles/`, `subagents/`, `pm-agent/`, `read-order/`, `SOP-core.md` or `kanban.cfg` changed after 03:55. RC commits touching `team/scripts/` are the cycle's own CODER task merges.
4. **Queue files edited by hand?** NO. Every cycle entry uses a canonical marker, consistent with each task's `## State`.
5. **Remote git operation by a non-CM agent?** YES, self-reported and low. No cycle task folder (PM-001, CODER-002 to 007, WRITER-008, CM-009) holds a guard record. This task's `logs/git-boundary-guard.jsonl` holds one record: `{"timestamp":"2026-10-05T09:16:25Z","role":"tester","task":"TESTER-20261005-010-verify-2-29-1","argv":["git","-C","/tmp/pgai_kanban_tmp/sandbox-PFYjCf/dev","push","origin","refs/tags/ai_v9.0.0"]}`.
   - **Source.** My own reproduction of BUG-0548's original failure, running the extracted `ai_main` tree's pre-fix test. On that first attempt I passed the network stub's return value wrongly, so the stub was not on PATH.
   - **Effect.** The guard refused the push. Its target was a throwaway local bare repo under the temp dir, so no ref moved anywhere real.
   - **Disposition.** The behavior is exactly BUG-0548, so I cite BUG-0548 (`running`, fixed by this RC) rather than file a duplicate. It indicates no defect in the RC.

Autonomous operation criterion: Manual interventions required: none (one self-reported verifier-probe boundary record, above).

## Caveats

- **Operator: run deep-verify.** Both Dockerfiles changed and the deep-verify ledger is stale and red. Running the DEEP-VERIFY lane before relying on these images is advisable. TESTER never runs the container tier.
- **Recurring red unit suite.** It was red in 5 of the last 11 RCs (v2.27.0, v2.27.3, v2.28.0, v2.29.0, v2.29.1), each time from a whole-tree invariant test that CODER's targeted `--files` runs do not reach. Finding 1's systemic-risk rating is `medium` because each instance is a distinct small fix. The pattern may still deserve an operator decision, for example adding a fast whole-tree invariant subset to CODER's completion check. I did not file that as a priority because it is a process design choice, not a defect in this RC.
- **Live install version.** The live `VERSION` reads `v2.28.4` while `## Last Released` is `v2.29.0`; the live install appears not to have been re-installed since v2.28.4. Observation only (Step 6.11 state drift; outside this RC's spec).
- **CPU contention.** My ad-hoc probes overlapped the first ~10 minutes of the gated run, adding contention. The 18m53s wall time is an upper bound for this configuration.
- **Scratch.** Under `/tmp/pgai_kanban_tmp/tester/TESTER-20261005-010/` and `/tmp/pgai_kanban_tmp/sandbox-*`. No dev-tree or live-install file was modified. The `ai_main` comparison tree was a `git archive` extract with no shared `.git`.
