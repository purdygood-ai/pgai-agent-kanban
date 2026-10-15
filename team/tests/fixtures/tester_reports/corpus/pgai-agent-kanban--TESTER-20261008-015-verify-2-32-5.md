# Verification Report: rc/v2.32.5

## Summary
Verified `ai_rc/v2.32.5` (commit `35f385ada6bfc3f7693f943bfcece320889f6862`, four CODER merges on top of `ai_main` v2.32.4) against `requirements/v2.32.5-bugfix-bundle-20261008.md`. That document bundles BUG-0616, BUG-0617, BUG-0615 and BUG-0618; each bug's own `## Acceptance` section served as the testable criteria. All four fixes work.

For each bug, the original reproduction fails on `ai_main` and passes on the RC, using production-shaped inputs: the real shipped release notes, the real `metrics/history.csv`, and the real v2.32.4 TESTER report. Both gated runners exited 1:
- Two failures come from a real defect this RC introduced. The Status reader now crashes when run by file path (BUG-0619, medium).
- Two failures are tests that asserted the exact Lane A behavior BUG-0617 removed (stale assertions BUG-0620 and BUG-0621).

No defect affects the release pipeline, Step 4e stamping, or data integrity. Recommendation: ship and track the follow-ups.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Findings

### Per-criterion results (pass)

| # | Criterion (source) | Check run | Result |
|---|---|---|---|
| P1 | Pre-flight Step 2: every DONE task's branch merged into the RC (TESTER.md Step 2) | Scanned 35 DONE task folders that name a feature branch; none still exists locally (deleted after merge). The cycle's four CODER branches each show a `--no-ff` merge plus one unique commit on the RC | pass |
| P2 | Pre-flight Step 3: worktree at the RC, clean, no stash | `git rev-parse HEAD` = `ai_rc/v2.32.5` = 35f385ad; `git status --porcelain` empty; `git stash list` empty | pass |
| P3 | Requirements AC "All Python files compile cleanly" | `py_compile` on all 848 tracked `.py` files (pycache redirected to scratch): 0 failures | pass |
| P4 | Requirements AC "All bash files pass syntax check" | `bash -n` on all 334 tracked `.sh` files: 0 failures | pass |
| P5 | BUG-0616: a quoted `` `<!--` `` (unclosed, or paired with a later `-->`) above `## Status` is text; the placeholder is found and stamped | The bug's own reproduction via `cm_release_notes_status_has_placeholder` and `cm_stamp_release_notes_status`. ai_main: `absent`, stamp exit 1, `PENDING-RELEASE` survives. RC: `present`, stamped `FUNCTIONAL`, exit 0. Same for a bare mid-line `<!--` with a `-->` in a later section | pass |
| P6 | BUG-0616: the trigger-9 guard is still a real stop when the placeholder genuinely survives | Only `## Status` inside a comment: RC warns, falls back to a whole-file search, answers `present`, and the stamp then fails (exit 1), so the post-stamp guard is reached. Covered end to end by `test_step_4e_still_halts_when_the_placeholder_survives_below_a_quoted_opener` and `test_step_4e_stops_when_the_only_status_line_sits_inside_a_comment_above_a_placeholder` (green in the unit run) | pass |
| P7 | BUG-0616: no regression on real notes | Old (ai_main) and new readers compared on all 129 shipped `release-notes/*.md` with a placeholder injected under `## Status`: 0 differences in `status_section_has_placeholder` or `stamp_status_placeholder`. The shipped template stamps correctly | pass |
| P8 | BUG-0616: single shared comment scanner (Fix suggestion) | `team/pgai_agent_kanban/lib/html_comments.py` is new and read in full; `project_summary._strip_html_comments` and `notes_status_section` both use it | pass |
| P9 | BUG-0617: a failed cancel-rc leaves the bundle doc and HALT in place, and the errors name the failure and the skipped steps | Own driver with stubbed RC steps, 4 scenarios on both trees. ai_main: bundle deleted and HALT cleared in all 3 failure scenarios. RC: both kept; errors are "`cancel-rc.sh ... FAILED: LOCKED`" plus "later steps not run because the RC cancel failed: delete bundle doc, clear ship-policy HALT; ... left in place" | pass |
| P10 | BUG-0617: a failed unwind stops everything after it, cancel-rc included | RC: only the unwind stub was called; bundle doc and HALT kept; errors name every skipped step | pass |
| P11 | BUG-0617: when both RC steps succeed, the bundle is deleted and the HALT removed (unchanged) | RC: all 4 steps completed, bundle gone, HALT gone. `test_overwatch_lane_a_drill.py` (real scripts) green in the integration run | pass |
| P12 | BUG-0617: actions.log line names the failed step | Observed in the integration fixture's actions.log: "RC cancel FAILED, later steps not run (bundle doc and ship-policy HALT left in place)" | pass |
| P13 | BUG-0615: reading the unmigrated file already shows the blocked minutes | `show-metrics.sh --history-csv` on a copy of the production `metrics/history.csv` (20-name header; 136 short rows, 61 rows with a 21st field). ai_main: Blocked `-` for v2.32.3. RC: `836` for v2.32.3, `0` for clean runs, `-` for rows written before the column existed | pass |
| P14 | BUG-0615: after one append the header has 21 names; existing rows unchanged | `metrics_csv_writer.py --csv-path <copy> --rollup-json <rollup>`: one stderr line naming the added column; header 21 names; mode 640 kept; every prior row byte-identical (`diff`); no scratch file left; the new row's `12.5` renders as `12` | pass |
| P15 | BUG-0615: a header that is not a prefix warns and the append still succeeds | Non-prefix header and over-long header: WARNING, row appended, exit 0, header untouched. Empty file: full header written. Duplicate RC on a short-header file: skipped, exit 0 | pass |
| P16 | BUG-0615: concurrency of the new lock-and-reopen path (6.11 spot check of a new mechanism) | 5 trials × 6 concurrent writers on a short-header production copy: every trial ends with a 21-name header, all 6 rows present (204 lines = 198 + 6), no scratch file | pass |
| P17 | BUG-0615: `GET /metrics` reads the widened data | `test_metrics_route_reads_the_unmigrated_file` and `test_after_one_append_every_surface_reads_the_same_values` green in the unit run. Every history.csv reader routes through `read_history_csv` (`rows.py:152`, `history.py:309/408`). The `api/metrics_data.py` path named in the bug does not exist on the RC; `/metrics` is served by `show_metrics` / `rows` | pass-with-caveat (the REST surface is verified by tests only, not driven by hand) |
| P18 | BUG-0618: no Rationale cell starts with `- `, contains "Traces to" or "another item" | `read_ship_policy_skipped_bug_pairs` + `build_bugs_skipped_notes_block` on the real v2.32.4 TESTER report. ai_main reproduces the shipped garbled cells exactly. RC: "Introduced by this release and found during its verification, rated fix effort small and systemic risk medium; deferred to a follow-up release." and "Predates this release ...", both matching each finding's own Regression / Fix Effort / Systemic Risk | pass |
| P19 | 6.1 release-state sanity | Active RC v2.32.5; `pp_last_released_version` = v2.32.4; `semver_gt v2.32.5 v2.32.4` true; `## Last Released` well-formed. The trailing `## HALT Event` block is CM's documented audit append (`release_preflight.sh:175`) from v2.32.3 | pass |
| P20 | 6.9 constraint negatives | No secrets or tokens in added lines. Bug and task identifiers in added lines appear only as fixture values in tests (the sanctioned "value illustrates an interface" exception) | pass |
| P21 | 6.10 prerequisite graph | No cycles; every referenced task exists; CM-014 (open) → CODER-010..013 → TESTER-015 → CM-017 (release); 013 also depends on 010. Sequence 016 is unused (a gap, not a collision) | pass |
| P22 | 6.13 skip-cites-real-bug | No skip or xfail added by the RC; `lint_skip_bug_gate` OK in both runners | pass |
| P23 | 6.14 coding standards | No new scripts. The RC removes a duplicated comment scanner rather than adding one. Help text is updated for the new `no-section` CLI output and the writer's migration | pass |
| P24 | Documentation discipline | Module docstrings and `--help` updated for every behavior change (`notes_status_section`, `metrics_csv_writer`, `release_known_issue.sh` usage comments, `execute_lane_a_drill` docstring). No tunable or env var added, so no `*_example` change is owed | pass |

### Finding 1: Release-notes Status reader crashes when run by file path

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (the "package CLI crashes by path" class recurs whenever a module gains a package import; filed before as BUG-0533, BUG-0537, BUG-0541, BUG-0561, BUG-0575, BUG-0595)
**Filed As:** BUG-0619 (Path C-A)
**Regression:** yes
**Integrity:** breakage

- Traces to: requirements AC "Each bundled item is addressed" (the BUG-0616 fix introduced it) and TESTER.md Step 5a (gated runners must exit 0).
- Location: `team/pgai_agent_kanban/cm/notes_status_section.py:81`. The new top-level `from pgai_agent_kanban.lib.html_comments import comment_spans` has no `if not __package__:` self-location guard, although the module has its own CLI.
- Actual: `python3 team/pgai_agent_kanban/cm/notes_status_section.py --help` exits 1 with `ModuleNotFoundError: No module named 'pgai_agent_kanban'`. `test_package_modules_run_by_path.py` fails 2 cases (`test_help_by_file_path_runs_without_pythonpath[...notes_status_section]` and `test_file_load_binds_the_package_of_its_own_tree[...notes_status_section]`). Both pass on ai_main.
- Why medium, not high: the defect is real and this RC introduced it, so `Regression: yes`. The one production caller (`cm_release_notes_status_has_placeholder` via `pp_run_ops ... -m`) and the stamp heredoc (package import) both still work: I drove Step 4e's check and stamp on the RC and both behaved correctly. Nothing in the tree invokes the module by path. That fits the rubric's medium: "incorrect, but a workaround exists or nothing depends on the broken path yet". A reader who weighs the calibration line "a command that worked in the last release and exits non-zero in this one is high" more heavily could argue for high. I did not rate it so, because the documented invocation is `python3 -m` and still works. The rating is not softened to reach a ship outcome; CM and the operator can weigh the argument here.

### Finding 2: Lane A wiring unit test asserts the drill clears the HALT after a failed unwind

**Category:** stale-assertion
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated to one test)
**Filed As:** BUG-0620 (Path C-A)
**Regression:** no
**Integrity:** none

- Test file and line: `team/tests/unit/test_overwatch_lane_classification.py:384` (`assert not fx["halt_path"].is_file()`); line 385 (bundle document deleted) would fail next.
- Expected vs actual: expected the HALT absent; actual, the HALT is present (`assert not True`).
- Production change: `team/pgai_agent_kanban/overwatch/resolution_stage.py:825-837`. A failed unwind-rc now returns before cancel-rc, the bundle deletion and the HALT clear. RC commit 49d47e70, task CODER-20261008-011-lane-a-stop-on-failed-cancel.
- Consistent with requirements: BUG-0617 Expected says "If unwind-rc or cancel-rc fails, the bundle document stays in place and the ship-policy HALT stays." The test's docstring states the old premise (no scripts/ tree, so the RC steps error "while the two steps that need no external script ... still complete").
- Recommendation: update the test, not the production code. The test passes on ai_main.

### Finding 3: Fresh-project resolution integration test passed only through the Lane A defect

**Category:** stale-assertion
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated to one test)
**Filed As:** BUG-0621 (Path C-A)
**Regression:** no
**Integrity:** none

- Test file and line: `team/tests/integration/test_overwatch_scaffolding_gates_resolution.py:427` (`assert not halt_path.exists()`).
- Expected vs actual: expected the HALT absent; actual, the HALT is present.
- Production change: `team/pgai_agent_kanban/overwatch/resolution_stage.py:842-854` (a failed cancel-rc now stops the drill). RC commit 49d47e70, task CODER-20261008-011-lane-a-stop-on-failed-cancel.
- Evidence the old pass went through the defect: in this fixture `cancel-rc.sh` fails on both trees (`create-project.sh`'s default `push_to_remote=true` with no `origin`: "fatal: 'origin' does not appear to be a git repository"). On ai_main the drill cleared the HALT anyway (actions.log: "1 action(s) reported errors"). On the RC it correctly leaves the HALT in place.
- Consistent with requirements: BUG-0617 Expected, as in Finding 2.
- Recommendation: update the test. Preferably give the fixture `push_to_remote = false`, as `test_overwatch_lane_a_drill.py` does, so the test still proves what its name says.

## Gated suites (Step 5a, 6.6, 6.7)

| Runner | Exit | Result |
|---|---|---|
| `team/scripts/run-unit-tests.sh --coverage` | `UNIT_EXIT=1` | parallel lane: 3 failed, 10621 passed, 41 skipped (19m24s); serial lane: 5 passed. Lint pre-flight passed (1 quality finding). Failures: Finding 1 (2 cases), Finding 2 |
| `team/scripts/run-integration-tests.sh --coverage` | `INTEGRATION_EXIT=1` | parallel lane: 1 failed, 958 passed, 1 skipped (13m35s); serial lane: 0 selected (exit 5, combined to 1). Lint pre-flight passed (1 quality finding). Failure: Finding 3 |

Output is under `/tmp/pgai_kanban_tmp/tester/v2.32.5/` (`unit.log`, `integration.log`, `*.exit`). Direct pytest was used only as a debugging aid after the runners failed: to confirm all four failing tests pass on an `ai_main` clone, and to capture the integration fixture's actions.log. No PASS evidence is drawn from it.

**Coverage (Step 5b, informational):** coverage unavailable. Both runners printed `coverage disabled by config ([testing] coverage)`, since kanban.cfg `[testing] coverage` is absent and defaults to `false`, and `--coverage` did not override it. The `coverage/*.json` files present date from 2026-10-04 and were not produced by this run, so they were not used.

## Lint Findings (non-blocking)

- `inline_python` — `team/scripts/lib/cm/release_known_issue.sh` has 6 blocks / 396 inline Python lines, above its allowlist entry of 6 / 380 (the same site in both `findings-unit.json` and `findings-integration.json`; ai_main has 0 findings) — filed BUG-0622 (Path C-A)

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=31d freshness=stale — The latest deep-verify record is 31 days old, outside the 7-day window and for v2.9.20 rather than v2.32.5, and it failed to build docker/claude-abliterated and podman/rocky9; that run filed BUG-0167 (now done and archived), and since project.cfg sets deep_verify = false nothing is known about whether this RC's images build.

## Autonomous criterion (Step 6.12)

1. Tasks force-promoted from WAITING to BACKLOG by hand: **NO**. CODER status files show only wake-script transitions; no manual-intervention notes.
2. `release-state.md` edited outside the CM scripts: **NO**. Last written 2026-10-08T16:57:17Z, matching `open-rc.log` for CM-20261008-014.
3. Scripts patched mid-build: **NO**. No file under the live install's `scripts/`, `pgai_agent_kanban/`, `roles/` or `subagents/` changed after RC open. RC commits are only the four CODER commits and their merges.
4. Queue files edited by hand: **NO**. Queue mtimes match wake dispatch times.
5. Remote git operation by a non-CM agent: **NO**. No `git-boundary-guard.jsonl` exists in any of this cycle's task folders (PM-002, CM-014, CODER-010..013, TESTER-015).

Autonomous operation criterion: Manual interventions required: none

## Caveats

- **Fenced code above `## Status` (pre-existing, improved).** A `<!--` at the start of a line inside a fenced code block above `## Status` still hides the section, because the shared scanner does not model fenced code blocks. On the RC this fails closed: the check warns and answers `present`, the stamp cannot find the placeholder, and trigger-9 halts. On ai_main it silently answered `absent`. Fenced code is outside BUG-0616's stated scope (prose and inline code), so this is not a finding.
- **Partial cancel leaves a HALT with no active RC.** In the integration fixture of Finding 3, `cancel-rc.sh` resets `## Active RC` to `none` before failing on its origin step. With this RC's (correct, per BUG-0617) all-or-nothing gate, the HALT is then left in place, and the next sweep declines because no RC is active. An operator must remove the HALT. This is fail-closed and matches BUG-0617's Expected; noted for operator awareness.
- **Observation, not a finding (outside the RC's diff).** `deep_verify_ledger_read_latest` given an invalid project name (a path, or `Bad_Name`) prints `found=false` and returns 0 rather than refusing. A miswired caller could therefore write a Form B "never run" line for a project that has a ledger. Recorded for human consideration only.
- **Observation, not a finding (pre-existing).** `show-metrics.sh --help` prints its shebang as `!/usr/bin/env bash` on its first line; the same on ai_main.
- **No commit.** Per the dispatch instructions (detached worktree, no feature branch, `## Feature Branch: none`), this report and the other artifacts live only in the task folder and are not committed to any branch.
- **Process context.** CODER-010 and CODER-011 verified with targeted `--files` runs only. Findings 1-3 would have surfaced in a full gated run; the role files place that run with TESTER, so this is context, not a finding.
