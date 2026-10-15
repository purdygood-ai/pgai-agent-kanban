# Verification Report: rc/v2.32.10

## Summary

I verified RC `ai_rc/v2.32.10` (HEAD 8a46d0f7, branched from `ai_main` 1b4dba6b) against `requirements/v2.32.10-bugfix-bundle-20261009.md`, a discovery bundle of BUG-0629 (OVERWATCH's TESTER-report finding parser used its own heading rule) and BUG-0630 (the four release-path report readers each read finding field lines by a different rule). Three merges landed: CODER-20261009-002, CODER-20261009-003 and WRITER-20261009-004, touching 10 files.

Both gated runners exited 0: unit 10709 passed / 41 skipped, integration 959 passed / 1 skipped. Every acceptance bullet in both bundled bugs was re-run and met:
- BUG-0629's reproduction now prints `1 1 False`.
- 94 installed reports split identically under OVERWATCH's parser and the shared splitter.
- BUG-0630's reproduction now prints `SHIP-KNOWN-ISSUE`, `1`, `1` for every looser field shape. On an `ai_main` export it prints `0`/`0`, which is the original failure, reproduced.
- 93 installed reports give byte-identical output from all readers on the RC and on ai_main.

One defect was found and filed. OVERWATCH's parser still reads its Filed As and Regression lines with its own stricter pattern, so it misreads annotated or backticked values. A new docstring sentence says every reader shares the field rule. The behavior is pre-existing (identical on ai_main), but the overclaiming sentence is new. A second item is lint-only: the unit runner's sentinel-leak check reported a `HALT-AFTER` that appeared on the live project root during the run. Its origin is undetermined, most likely an operator arm. It is filed as a lint-class bug and does not move the recommendation.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
low

## Findings

Per-step results (pass / pass-with-caveat). The one defect finding follows as `### Finding 1`.

- **Step 2, pre-flight (pass).** 26 DONE tasks name a feature branch. All are deleted locally, which is the normal post-merge shape, so Check 1 and Check 2 pass trivially. The RC log shows the three `--no-ff` merges.
- **Step 3, working tree (pass).** `git rev-parse HEAD` == `git rev-parse ai_rc/v2.32.10` == 8a46d0f7. `git status --porcelain` is empty and `git stash list` is empty.
- **Step 5a / 6.6 / 6.7, gated suites (pass).** Both run from the worktree root with `--coverage`:
  - `run-unit-tests.sh`: UNIT_EXIT=0, 10709 passed, 41 skipped, 16m21s.
  - `run-integration-tests.sh`: INTEGRATION_EXIT=0, 959 passed, 1 skipped, 11m38s.
  - Logs are under `/tmp/pgai_kanban_tmp/tester/TESTER-20261009-006/`.
- **Step 5b, coverage (informational).** Coverage unavailable. Both runners printed `coverage disabled by config ([testing] coverage)` (the key is absent, so the default is false). The JSON files under `/tmp/pgai_kanban_tmp/coverage/` date from 2026-10-04 and are stale, so I did not use them.
- **Requirements AC "each bundled item is addressed" (pass).** See BUG-0629 and BUG-0630 below.
- **Requirements AC "All Python files compile cleanly" (pass).** All 849 tracked `.py` files pass `py_compile`.
- **Requirements AC "All bash files pass syntax check" (pass).** All 335 tracked shell files (`*.sh` plus bash/sh shebang files) pass `bash -n`.
- **BUG-0629 acceptance (pass).**
  - All 9 heading shapes agree: `parse_tester_report_findings` returns a finding exactly where `finding_blocks` returns a block. The shapes are `###`, `####`, `#####` and `###### Finding 1:`, `###Finding 1:`, `### Findings summary`, `## Findings`, `## Finding 1:` and `### Finding: no number`.
  - The nested `### Finding 1` + `#### Finding 2` case gives `['none', 'BUG-0002']`.
  - Reproduction step 2 prints `1 1 False`, and step 4 prints `1 1 False`.
  - Block extent: `### Step 7` / `**Filed As:** none` after a finding leaves that finding's filed_as as `BUG-0001`.
  - Install-wide, 94 reports and 876 blocks give 0 differing counts or bodies.
  - `grep -n Finding resolution_matrix.py` finds no heading regex. `_FINDING_HEADER_RE` is gone, and only the title-label trim and docstrings remain.
  - TESTER.md Step 9 and the `stop_bug_files.py` module docstring name all five block readers. A grep of `team/` found no sixth reader of report findings, so the "every reader splits by this one rule" sentences are true.
- **BUG-0630 acceptance (pass-with-caveat).**
  - Field-line shapes tested: bold, plain, backticked id, `see BUG-0003`, `cites the existing BUG-0003-x3 (open)`, and lowercase labels with `bug-0003`. For each, `read_ship_policy_finding_dimensions` reads `no none`, `cm_ship_policy` returns `SHIP-KNOWN-ISSUE`, the known-issue resolver prints 1 and Bugs Skipped prints 1. The stop-list CLI (systemic-risk-high) prints 1.
  - A no-colon `**Label** value` shape is skipped by all four readers, and the matrix falls to `HALT`.
  - Original failure mode on an `ai_main` export: every non-bold shape gives `SHIP-KNOWN-ISSUE 0 0`. Worse, the no-colon shape gives `SHIP-KNOWN-ISSUE 0 0` on ai_main, so the old matrix decided from a line the resolver could not read.
  - Install-wide, 93 reports (my own excluded) were run through dims, values, the known-issue resolver, Bugs Skipped and the stop list for both causes. RC and ai_main outputs are byte-identical.
  - Caveat: the matrix now needs a colon after a field label (CODER-003's documented design choice). A colon-less label line on a high-risk finding now reads `unknown` and stops rather than ships, which is the fail-closed direction. No installed report is affected.
- **Inline-python allowlist constraint (pass).** `lint_inline_python.py --emit-allowlist` measures `release_known_issue.sh 5 248` and `release_ship_policy_report.sh 2 317`, which equal the lowered entries. The lint reports 0 findings.
- **Step 6.1, release state (pass).** `semver_gt v2.32.10 v2.32.9` holds, using `pp_last_released_version`. `## Last Released` is `v2.32.9`, which is well-formed. The appended historical `## HALT Event` block for v2.32.3 is written by CM's designed halt helper (`release_preflight.sh`), so it is an observation, not drift.
- **Step 6.3, documentation layer (pass, apart from Finding 1).**
  - TESTER.md Step 9's new sentences match the code: five block readers, four field readers, and "OVERWATCH's parser shares the block split only".
  - `release_known_issue.sh`, `release_ship_policy_report.sh` and the `skipped_bug_pairs.py` comments and docstrings are accurate.
  - `docs/cm-release-reference.md` and `team/roles/CM.md` describe Filed As generically and remain true.
- **Step 6.5 / Anti-pattern 6, guard behavior (pass).** Both bug fixes were exercised in the original failure shape (on an ai_main export) and in the new normal shape (on the RC). See above.
- **Step 6.8, synthetic release simulation (skip).** The diff touches no open/close-cycle file (`cm-release.sh`, `open-rc`, `cancel-rc`, `project_paths.sh`). It touches only the release-notes and ship-policy readers under `team/scripts/lib/cm/`, which the integration suite covers.
- **Step 6.9, constraint negatives (pass).** No secrets, tokens or passwords appear in added lines. Both shell files still pass `bash -n`.
- **Step 6.10, prereq graph (pass).**
  - The graph is acyclic and every ID exists.
  - Bookends are correct: CM-005 open-rc comes first, and TESTER-006 then CM-008 release come last.
  - Sequence 007 is unused. This is cosmetic: the materializer reserved it, and PM noted it.
- **Step 6.11, spot-check (pass-with-caveat).** It surfaced Finding 1. I also checked the title-label trim `_FINDING_TITLE_LABEL_RE`: a heading like `Finding—x` with an attached dash yields an empty title. That is cosmetic and only used for display, so it is an observation.
- **Step 6.13, skip-cites-real-bug (pass).** The RC adds no new skip or xfail. The runner's `lint_skip_bug_gate` pre-flight passed.
- **Step 6.14, coding standards (pass).**
  - No new script, so no `--help` is owed.
  - Added lines carry no bug, task or version provenance tokens. The `BUG-0001`…`BUG-0058` literals in the diff are test-fixture values, a sanctioned example use.
  - No new duplicate of a `lib/` helper. The change removes three duplicate field readers.
- **Test fidelity (pass).** The new cross-reader tests drive the real bash libraries through `run_bash` and the real Python modules, with no mocks of the field or block readers.

### Finding 1: OVERWATCH's finding parser reads Filed As and Regression with its own pattern, despite a new "every reader shares" docstring

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated to OVERWATCH's resolution stage; pre-existing behaviour)
**Regression:** no
**Integrity:** none
**Filed As:** BUG-0631 (Path C-A: filed as BUG — code defect)

Path C means autonomous follow-up filing, which is non-blocking.

The defect is in `parse_tester_report_findings` (`team/pgai_agent_kanban/overwatch/resolution_matrix.py`). It now splits blocks with the shared `finding_sections`, which is correct. Inside each block it still reads fields with `_FILED_AS_RE` and `_REGRESSION_RE`, which strip only `*`. The release path's shared reader (`stop_bug_files.field_value` / `field_first_word` / `filed_as_bug_id`) also strips backticks, allows any whitespace between label words, and reads a rating as its first word.

Measured on the RC, with identical results on an ai_main export:
- `**Regression:** Yes (introduced here)` reads as `None` to OVERWATCH and `yes` to the release path. Lane A's `find_regression_source_finding` needs `regression is True`.
- `` **Regression:** `yes` `` reads as `None` and `yes`.
- `` **Filed As:** `none` `` and `Filed As: none (not filed)` both count as filed (`findings_all_filed` is True). This is fail-open: that fact feeds the only shipped resolution-matrix row, `findings_all_filed: true` → CANCEL-AND-REBUNDLE.

In the install, 8 real findings, all in my-app TESTER reports from 2026-09-03, carry an annotated `**Regression:** yes|no (...)` line that OVERWATCH reads as None. `kanban.cfg` has `overwatch_resolve = true`, so this path is live.

The new `field_value` docstring sentence, "the one field-line rule every reader of a TESTER report's finding fields shares", is not true of the tree. The module docstring and TESTER.md Step 9 state the exception correctly. The behavior was explicitly outside BUG-0629's scope ("the callers' field reading can stay"), so this is not a failure of either bundled fix. It is a remaining divergence, and one new overclaiming sentence, worth folding into the next iteration.

Reproduction: see BUG-0631, step 2. It prints `None True yes`, and I confirmed that on the RC.

## Lint Findings (non-blocking)

- `sentinel_leak` (unit runner, quality class): `[pgai-sentinel] ADDED: $HOME/pgai_agent_kanban/projects/pgai-agent-kanban/HALT-AFTER`. The file holds `rc:v2.32.10`. A bare `rc` was written between 01:28:04Z and 01:33:04Z, during the unit run, and the live CM wake versioned it at 01:33:04Z. Its origin is undetermined. The bare `rc` token is the default of the operator CLI `halt-after.sh`, and no unit test writes it outside a tmp board, but no log records the writer. Filed as BUG-0632 (Path C-A). I did not touch the file. Only the operator removes sentinels.
- Integration runner: 0 findings (`findings-integration.json`, generated 2026-10-09T01:49:27Z).

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=31d freshness=stale — The last deep-verify run was red (docker/podman claude-abliterated FROM parse error and podman/rocky9 COPY --chmod), which that run filed as BUG-0167 (since closed, fixed in v2.9.22); the record is stale on both counts: 31 days exceeds the 7-day window and v2.9.20 is not the RC under test (v2.32.10).

## Autonomous criterion

1. Tasks force-promoted WAITING→BACKLOG by hand: **NO**. Each promotion carries the wake script's "prerequisites satisfied" summary, and every queue-file mtime lines up with an agent transition.
2. `release-state.md` edited outside cm open/release/cancel: **NO**. Its mtime, 00:42:19Z, equals `RC Opened At`, which `open-rc.sh` wrote for CM-005.
3. Scripts patched mid-build: **NO**. No live-install file under `scripts/`, `roles/` or `team/` changed after the RC opened. The RC's own `team/scripts/lib/cm/` edits are the scoped CODER-003 deliverable named in BUG-0630, not tooling patched in flight.
4. Queue files edited by hand: **NO**. Markers match `status.md` for every cycle task, and the `[A]` on this task is the wake driver's.
5. Remote git op by a non-CM agent: **NO**. No `git-boundary-guard.jsonl` exists in any of this cycle's task folders.

Autonomous operation criterion: Manual interventions required: none. One possible operator action, a HALT-AFTER arm during verification, is noted under Lint Findings and Caveats. It is a permitted operator control, not a pipeline intervention.

## Caveats

- **A live `HALT-AFTER` (`rc:v2.32.10`) is armed on `projects/pgai-agent-kanban/`.** Once v2.32.10 ships and the RC closes, it promotes to `HALT` and pauses this project's lane. If the operator armed it, that is intended. If not, the operator should remove it by hand (BUG-0632). TESTER did not modify it.
- Coverage was disabled by config for both runs, so no line or branch numbers were recorded.
- My own install-wide sweeps read live report and bug files read-only, with the sandboxed environment (`env -u PGAI_TASKS_DIR -u PGAI_PROJECT_ROOT`, a temporary `PGAI_AGENT_KANBAN_ROOT_PATH` and temp dir). The ai_main comparison used a `git archive ai_main team` export inside the sandbox.
- The project ships Dockerfiles (`docker/debian`, `docker/rocky9`), so the Deep-Verify line above is mandatory. Per the role file, TESTER ran no container lane.
