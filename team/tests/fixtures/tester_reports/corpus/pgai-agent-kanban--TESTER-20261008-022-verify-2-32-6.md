# Verification Report: rc/v2.32.6

## Summary
Verified RC `ai_rc/v2.32.6` (HEAD 2dbeff50, three --no-ff merges over ai_main d93134c2) against `requirements/v2.32.6-bugfix-bundle-20261008.md` and its four bundled items: BUG-0619 (release-notes Status reader crashes when run by path), BUG-0620 and BUG-0621 (two stale OVERWATCH Lane A test assertions), and BUG-0622 (inline-Python lint over its allowlist). All four are fixed, and every acceptance criterion in the bug files and CODER tickets passes. I re-ran each one, triggered the original failure mode where one applied, and ran an old-vs-new differential over all 92 real TESTER reports in the install. The gated unit runner exited 0. The gated integration runner exited 1 on a single wall-clock budget assertion in a PM-wake test that this RC does not touch. That test passes in isolation on both the RC and ai_main with the same timing, so the failure predates this RC. A spot-check also found a pre-existing heading-depth gap in the Bugs Skipped reader this RC relocated. Both are filed via Path C (autonomous follow-up filing — non-blocking). Neither affects the bundle's deliverables.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Findings

### Requirements and acceptance-criteria results (pass)

| # | Criterion (source) | Evidence | Result |
|---|---|---|---|
| P1 | Requirements AC: each bundled item addressed | BUG-0619 -> 0c12b5cc; BUG-0620/0621 -> e21fddb7; BUG-0622 -> ce73f56a; each verified below | pass |
| P2 | Requirements AC: all Python files compile | `python3 -m py_compile` on all 849 tracked .py: 0 failures | pass |
| P3 | Requirements AC: all bash files pass syntax check | `bash -n` on all 334 tracked .sh: 0 failures | pass |
| P4 | BUG-0619: by-path `--help` exits 0, PYTHONPATH unset, cwd outside tree | rc=0, no traceback. Original failure reproduced on an ai_main copy: rc=1 `ModuleNotFoundError: No module named 'pgai_agent_kanban'` | pass |
| P5 | BUG-0619: test_package_modules_run_by_path.py exits 0 incl. both notes_status_section cases | gated `--files` run green; collection lists help-by-path, file-load-binds-own-tree and package-member cases for notes_status_section | pass |
| P6 | BUG-0619: test_release_notes_status_section_scope.py still exits 0 | gated `--files` run green | pass |
| P7 | BUG-0619 constraint: existing callers keep working | `has-placeholder` by path and by `-m` both return present/absent correctly; package-member import leaves sys.path unchanged; by-path load from a second copy binds that copy's own package (no cross-tree fallback) | pass |
| P8 | BUG-0620: unit test asserts EXECUTED, one Lane A Decision block, HALT present, bundle doc present | Read in full (lines 349-385); also asserts HALT byte-identical and Active RC unchanged. Negative check in a scratch clone: with resolution_stage.py from 49d47e70^ the test fails at line 383; restored, passes | pass |
| P9 | BUG-0620: test_overwatch_lane_classification.py and test_overwatch_lane_a_drill_stops_on_failed_rc_step.py exit 0 | gated `--files` run green | pass |
| P10 | BUG-0621: integration test exits 0 with no cancel-rc auto-fix-error (preferred option) | Fixture stages `push_to_remote = false` (fails loud unless exactly one live `true` line); asserts no unwind/cancel auto-fix-error lines. Negative check: staging disabled -> fails at line 472 on the `cancel-rc.sh ... origin` error line; restored, 2 passed | pass |
| P11 | BUG-0621: test_overwatch_lane_a_drill.py still exits 0 | gated integration `--files` run green (44 passed across 4 files) | pass |
| P12 | BUG-0622: `lint_inline_python.py` reports 0 findings | `310 file(s) scanned ... 2931 python line(s); 0 finding(s)`; allowlist entry lowered 6/380 -> 5/290, not raised | pass |
| P13 | BUG-0622: rendered rationale unchanged | Old heredoc extracted from ai_main and byte-compared (stdout, stderr, exit code) against the new `read_ship_policy_skipped_bug_pairs` over all 92 real TESTER reports in the install (100 rows): 0 differences; 5 synthetic argument shapes (exclusions, absolute/relative paths, missing report, missing bugs dir): identical | pass |
| P14 | BUG-0622: render + carry-forward tests, by-path test incl. new module, bash -n, py_compile | gated `--files` runs green (275 unit tests over the 6 named unit files); 3 by-path cases for skipped_bug_pairs collected and passing | pass |
| P15 | BUG-0622 constraint: "never fails" contract kept | Sole caller `cm_build_bugs_skipped_ship_block` consumes it via process substitution, so a non-zero python exit cannot abort the release; missing report / bugs dir still print nothing and exit 0 | pass |
| P16 | BUG-0622 constraint: Documentation Discipline | Header, usage and Dependencies comments in release_known_issue.sh name the new module | pass |
| P17 | 6.1 release-state sanity | Active RC v2.32.6; `pp_last_released_version` = v2.32.5; `semver_gt v2.32.6 v2.32.5` true; `## Last Released` well-formed. The trailing `## HALT Event` block is CM's designed audit append (`append_halt_event_to_release_state`, release_preflight.sh) from v2.32.3, not drift | pass |
| P18 | 6.8 synthetic release simulation (diff touches release_known_issue.sh, sourced by cm-release.sh) | `test_cm_release_synthetic.py` green in the gated integration `--files` run and in the full run | pass |
| P19 | 6.9 constraint negatives | No secrets, no bug/task/version identifiers in added lines; touched shell lib had no `set -euo pipefail` before or after (sourced library) | pass |
| P20 | 6.10 prerequisite graph | No cycles; all referenced tasks exist; CM-021 (open) -> CODER-018, CODER-019, CODER-020 (also after 018) -> TESTER-022 -> CM-024 (release, WAITING). Sequence 023 unused (gap, not collision) | pass |
| P21 | 6.13 skip-cites-real-bug | No skip/xfail added by the RC; `lint_skip_bug_gate` OK in both runners | pass |
| P22 | 6.14 coding standards | New CLI has argparse `--help` naming every argument; no hardcoded operational values; `lint_python_duplicates` 0 ratchet findings; bug-file resolver shared (made public as `bug_file_for_id`) rather than copied | pass-with-caveat: see Finding 2 (second finding-block reader in `cm/` with different heading semantics) |

### Finding 1: Gated integration runner red on a load-sensitive wall-clock assertion (pre-existing)

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (could recur on any RC whose certification run meets host load)
**Filed As:** BUG-0624 (Path C-A, filed as BUG — test failure)
**Regression:** no
**Integrity:** none

- `run-integration-tests.sh --coverage` INTEGRATION_EXIT=1: 1 failed / 958 passed / 1 skipped (15m56s). The lint pre-flight passed (findings-integration.json: 0 findings); the failure came from pytest.
- Failing test: `tests/integration/test_pm_wake_discovery_lock_scope.py::test_busy_chain_pm_wake_exits_fast_logs_busy_exit_first_and_never_holds_the_lock`, line 245: `AssertionError: busy-chain PM wake took 12.9s` / `assert 12.867894924012944 < 10`. Every behavioural assertion before it passed.
- Why it predates this RC: the 7 changed files do not touch the PM wake path, the test file is unchanged since v2.31.4, and the v2.32.4 certification run passed it. Isolated gated reruns pass 2/2 on the RC and 2/2 on ai_main d93134c2. The busy test's call time is 5.99/5.70/5.89 s on the RC and 6.47/5.85/5.80 s on ai_main, so the timing is the same on both. Host: 8 cores, load average about 4-5.
- Category is `pre-existing-bug` under Step 5a. It is recorded as `bug` here because Step 7 has no separate pre-existing label. SOP-core severity: medium (the gated runner turns red, and an isolated rerun is the workaround). Not a regression.

### Finding 2: Bugs Skipped reader ignores level-4-or-deeper Finding headings (pre-existing)

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated to one reader; no real report has used the shape yet)
**Filed As:** BUG-0623 (Path C-A, filed as BUG — code defect)
**Regression:** no
**Integrity:** none

- Found in the Step 6.11 spot-check (an untested mechanism in a module this RC added). `skipped_bug_pairs._FINDING_HEADING_RE` opens a finding only at exactly `###`. `roles/TESTER.md` Step 9 sanctions `#### Finding` and deeper, and `stop_bug_files.finding_blocks` and `read_ship_policy_finding_dimensions` both read that shape.
- Demonstrated in a sandbox: a report whose only finding sits at `#### Finding 1:` under `### Step 6.4` gives 0 Bugs Skipped rows. `resolve_stop_bug_files` returns that bug and the ship-policy parser reads `no none` from it. Changing only the heading to `###` produces the row.
- This RC ported the regex byte-for-byte from the ai_main heredoc (P13), so the behaviour is not a regression. A check of all 92 real reports found no `####` findings, so the gap has not yet affected shipped notes. SOP-core severity: medium (a real but unexercised defect).
- This report keeps its own findings at `###` so every reader sees them.

## Lint Findings (non-blocking)

None. (`findings-unit.json`: 0 findings; `findings-integration.json`: 0 findings.)

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=31d freshness=stale — The latest deep-verify record is 31 days old, outside the 7-day window, and is for v2.9.20 rather than v2.32.6. That run failed to build docker/claude-abliterated and podman/rocky9 and filed BUG-0167 (now done, archived under archive/pgai-agent-kanban/bugs/). The project sets deep_verify = false, so nothing is known about whether this RC's images build.

## Autonomous criterion

1. Tasks force-promoted WAITING -> BACKLOG by hand? **NO.** All four promotions (CODER-018/019 at 18:59, CODER-020 at 19:04, TESTER-022 at 19:27) are `wake(<agent>): ... prerequisites satisfied, promoting to BACKLOG` lines from the wake script.
2. `release-state.md` edited outside the CM verbs? **NO.** Its mtime is 18:57:15Z, which equals `## RC Opened At` (cm-open-rc); there has been no write since.
3. Scripts patched mid-build? **NO.** No live-install file under scripts/, roles/, pm-agent/ or SOP-core.md has changed since the RC opened. The one RC commit touching `team/scripts/` (ce73f56a, release_known_issue.sh) is the ticketed BUG-0622 deliverable, merged through the task pipeline.
4. Queue files edited by hand? **NO.** Markers match each task's `## State` (CODER-018/019/020 `[x]`, CM-021 `[x]`, CM-024 `[W]`, TESTER-022 `[A]`), and no log suggests a manual edit.
5. Remote git operation by a non-CM agent? **NO.** No `git-boundary-guard.jsonl` exists in any task folder for this cycle.

Autonomous operation criterion: Manual interventions required: none

## Coverage (informational)

coverage unavailable: both runners printed `coverage disabled by config ([testing] coverage)`. The `coverage/unit.json` and `integration.json` on disk date from 2026-10-04 and were not produced by this run.

## Caveats

- Suite counts: unit 10627 passed + 5 serial passed, 41 skipped (19m25s), UNIT_EXIT=0. Integration 958 passed, 1 failed, 1 skipped (15m56s), INTEGRATION_EXIT=1. The PASS precondition is not met, so the recommendation is SHIP-WITH-CONCERNS. Both filed items predate this RC, and every v2.32.6 bundle item verified as met.
- Direct pytest was used only for diagnosis: the negative checks in a scratch clone and timing measurements of the failing test. None of it is cited as suite evidence.
- Status was `WORKING` at session start (set by the wake driver at dispatch) with no partial report, so this was treated as a fresh start. No prior-run report or gaps file existed in artifacts/. The v2.32.5 report was consulted only as history, and its BUG-0167 citation was re-verified from the archive.
- Git workflow: the dispatch prompt forbids a feature branch in this detached worktree, and Step 9 says process artifacts are never committed into a dev tree. All artifacts are in the task folder; nothing was committed to the RC.
- Each CODER status flagged a cosmetic README inconsistency: Required Output names `rc/v2.32.6` while Source Branch names `ai_rc/v2.32.6`. All merges correctly landed on `ai_rc/v2.32.6`. This is an observation, not a finding.
- No file read during verification contained prompt-injection text.

## Verification Log (checkpointed during the run)

## Pre-flight
- Step 2 (RC intact): PASS. 35 DONE tasks name a feature branch; none still exists locally (deleted after merge), so both checks pass trivially. The three bundle work commits 0c12b5cc, e21fddb7, ce73f56a are on `ai_main..ai_rc/v2.32.6` via --no-ff merges d4ac4aa1, 7b326611, 2dbeff50.
- Step 3 (worktree): PASS. `git rev-parse HEAD` == `git rev-parse ai_rc/v2.32.6` == 2dbeff50; `git status --porcelain` empty; `git stash list` empty.

## Checkpoint — criteria evidence gathered so far (findings consolidated in ## Findings at the end)
- BUG-0619: by-path `--help` from outside tree, PYTHONPATH unset: rc=0, no traceback (ai_main copy: rc=1 ModuleNotFoundError — original failure reproduced). `has-placeholder` by path and by `-m` both give present/absent correctly. Package-member import leaves sys.path unchanged. By-path load from a second copy binds that copy's own package.
- BUG-0620: unit test asserts EXECUTED, one Lane A Decision block, Active RC unchanged, HALT present and byte-identical, one bundle doc. Negative check in a scratch clone: with resolution_stage.py from 49d47e70^ the test fails at line 383 (HALT gone); restored, passes.
- BUG-0621: integration test stages push_to_remote=false (preferred option) and asserts no unwind/cancel auto-fix-error lines. Negative check: with staging disabled, it fails at line 472 on the cancel-rc.sh origin error line; restored, 2 passed.
- BUG-0622: `lint_inline_python.py` 0 findings (allowlist lowered 6/380 -> 5/290, not raised). New module output byte-identical (stdout, stderr, exit) to the ai_main heredoc over 92 real TESTER reports (100 rows) and 5 synthetic argument shapes.
- Requirements AC 2/3: py_compile 849/849 tracked .py OK; bash -n 334/334 tracked .sh OK.
- Step 5a unit: `run-unit-tests.sh --coverage` UNIT_EXIT=0 (10627 passed + 5 serial passed, 41 skipped, 19m25s); findings-unit.json 0 findings. Coverage disabled by config.
- Spot-check 6.11: filed BUG-0623-skipped-pairs-drops-deeper-findings (pre-existing; level-4 Finding headings dropped from Bugs Skipped).
