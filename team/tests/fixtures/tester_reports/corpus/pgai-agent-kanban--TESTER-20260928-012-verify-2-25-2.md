# Verification Report: rc/v2.25.2

Verdict: SHIP-WITH-CONCERNS. Both gated runners exited 0 on commit 8de4336; all nine bundled items are implemented and behaviourally verified (each guard's original failure mode reproduced against ai_main and shown closed on the RC); four low-severity follow-ups filed via Path C (autonomous follow-up filing — non-blocking): BUG-0425 (one-time restore of a published changelog line the writer fix cannot undo), BUG-0426 and BUG-0427 (two pre-existing runner gaps adjacent to BUG-0423/BUG-0419), BUG-0429 (one non-reproducible integration test failure in a secondary run); one quality-class lint finding filed as BUG-0428. No serious defect; no regression introduced by this RC.

## Summary

Verifying `ai_rc/v2.25.2` at commit `8de43366fec342618a1ffcf5c907b6cfe648cd9c` (branched from `ai_main` at `4cddf24`) against `requirements/v2.25.2-bugfix-bundle-20260928.md`: a nine-bug bundle (BUG-0416, 0419, 0420, 0422, 0423, 0424 primary; BUG-0417, 0418, 0421 hygiene ride-alongs), implemented by CODER-002..007, 009 and WRITER-008, 010. Worktree: `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260928-012-verify-2-25-2` (detached HEAD == RC commit; `git status --porcelain` empty; no stash).

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
low

## Findings

### Step 1-3 pre-flight

- Requirements doc read end to end; three acceptance criteria (each bundled item addressed; all Python compiles; all bash passes syntax check) plus each bug file's own `## Acceptance` list used as the per-item criteria.
- HEAD `8de4336` == `ai_rc/v2.25.2`; porcelain clean; stash list empty. **pass**
- Step 2 RC-integrity scan: DONE tasks CODER-002..007, WRITER-008, WRITER-010 all name a feature branch that no longer exists locally (deleted after merge) -> both checks pass trivially; each task's merge commit (0ad45de, 44e25eb, f099cb2, 315a744, 8e83e71, 97528d0, 1c3e0fa, 0f865ac) is on the RC. CODER-009's branch `ai_feature/CODER-20260928-009-hygiene-test-allowlist` exists at `8de4336` (== RC HEAD, ancestor, zero unique commits) because the task was re-dispatched after its first run merged (see Finding on process, below); the merge 8de4336 carries its one-file change. **pass** (Check 2 not applicable: the task is not DONE at verification time; its first-run work is verifiably on the RC).
- Step 6.1: `pp_last_released_version pgai-agent-kanban` = `v2.25.1`; `semver_gt v2.25.2 v2.25.1` true. `release-state.md` holds Active RC `v2.25.2`, RC Opened At `2026-09-28T03:20:43+00:00`, RC Opened By Task `CM-20260928-011-open-rc-2-25-2`, Last Released `v2.25.1` — all well-formed. **pass**
- Requirements AC 2/3: `python3 -m py_compile` on every changed .py and `compileall` over team/pgai_agent_kanban, team/scripts, team/pm-agent: OK; `bash -n` on every changed .sh and every team/scripts/**/*.sh: 0 failures. **pass**

### Step 4 — diff and checklist

`git diff ai_main..ai_rc/v2.25.2`: 35 files, +3814/-297. Production: `wake_reconcile_sweeps.sh`, `wake_task_state.sh`, `wake_driver.sh` (BUG-0416/0422); `lock_provenance.sh` (BUG-0422); `gated_runner_lint.sh`, `run-unit-tests.sh`, `run-integration-tests.sh` (BUG-0419); `cm/changelog_writer.py`, new `lib/public_id.py`, `lint_changelog_freshness.py`, `BUG-TEMPLATE.md` (BUG-0420); `api/tests/conftest.py`, `tests/fixtures/api_server.py` (BUG-0423); `cm/release_ship_policy.sh` (BUG-0424); `test_temp_root_resolution.py` marker (BUG-0421); docs `operator-api.md`, `tester-verification-reference.md`, `team/roles/TESTER.md`, `test-authoring-guidelines.md`, `blocking-site-census.md` (BUG-0417/0418/0419/0424 doc halves). New tests: `test_reconcile_stale_active_terminal_marker.py`, `test_gated_runner_per_run_basetemp.py`, `test_changelog_writer_public_id_spent.py`, `test_lock_release_ownership.py`, `test_chain_lock_held_through_agent.py` (integration), `test_ship_policy_acceptance.py` (integration). Every changed file maps to a bundled item; no unexplained file in the diff. New files read fully; modified files read at the changed hunks.

### Step 6.4 / 6.5 — per-item acceptance and behavioral probes

#### BUG-0424 (ship-policy parser, level-4 finding headings) — **pass**

Sourced `team/scripts/lib/cm/release_ship_policy.sh` from the RC and ran `read_ship_policy_finding_dimensions` over six synthetic reports (scratch under `$PGAI_AGENT_KANBAN_TEMP_DIR/projects/pgai-agent-kanban/tester-012-probes/`):
- `### Finding` / `#### Finding` / `##### Finding` with `Regression: no`, `Integrity: none` -> `no none` at all three levels, no narration.
- Level-4 finding nested under `### Step 5a` with a following `### Step 6` sub-section carrying its own stray `**Regression:** no` -> `yes breakage` (the finding's own values; the block ends at the next heading no deeper than its own, so the sibling section's prose is not misattributed).
- Report with fields but no `Finding` heading -> `unknown unknown` on stdout AND stderr `zero finding blocks in <path>: no heading whose text begins with "Finding" at level 3 or deeper; regression/integrity defaulted to unknown`.
- `## Finding 1:` (level 2) -> not a block; same zero-block narration.
- `cm_ship_policy done high ship-with-concerns no yes no none` -> `SHIP-KNOWN-ISSUE`; with `unknown unknown` -> `HALT` (decision table unchanged).
Acceptance bullets 1-3 met (bullet 3, `roles/TESTER.md` states the shape as the parsed contract: present in `team/roles/TESTER.md` diff, "The finding heading and these `**Field:** value` lines are the shape the release script parses, not a style hint").

#### BUG-0421 (AP3 allowlist marker) — **pass**

`python3 team/scripts/lint_test_anti_patterns.py` -> `OK — 0 findings across 591 file(s)`. The marker sits 4 lines above `_LAST_RESORT` with a justification; the assertions on the fallback and the filesystem-root rejection are unchanged (diff is comment-only on that file). Findings-file check deferred to Step 5c.

#### BUG-0417 (route count) — **pass**

`grep -n 'the 17\|All 17' docs/operator-api.md` -> no match; census has 24 action rows; `check_docs_citations.py` rc=0; `lint_doc_command_truth.py` -> `OK — 0 findings`; `generate_operator_verb_docs.py --check` -> OK (32 verb blocks match census/ICD/--help). Section 6.1 now states the property count-free and cites Section 5 and the manifest.

#### BUG-0418 (role impersonation doc) — **pass** (bullets 1-2); bullet 3 answered in Step 6.12 (NO on question 5; no probe in this run set `PGAI_AGENT_ROLE`; CM-only paths were not run by hand).

`team/roles/TESTER.md` "Live-install safety" gained "A role override is not a sandbox either" naming `PGAI_AGENT_ROLE=cm`/unset as forbidden and pointing at `team/tests/fixtures/git_network_stub.py` and `test_non_cm_version_lookup_never_reaches_origin.py`; `docs/tester-verification-reference.md` gained "A YES the file never shows".

### Step 6.10 — prerequisite graph — **pass**

12 tasks (001, 002-012, 014; 013 unallocated — the optional approval bookend, `Human Approval Required: auto`). No cycles; every referenced ID exists; CM-011 open-rc has no prerequisites and is a prerequisite of every worker; TESTER-012 depends on all nine workers; CM-014 release depends on all nine plus TESTER-012. CODER-005 additionally depends on CODER-002 (as PM planned); WRITER-008 on CODER-003/007; WRITER-010 on WRITER-008.

### Step 6.13 — skip-cites-real-bug — **pass**

`lint_skip_bug_gate.sh` rc=0. The only `BUG-NNNN` tokens near `skip` are `BUG-1234`/`BUG-1235` inside string-literal fixtures of `test_skip_bug_citation_scan.py` (the lint's own self-test), not skip annotations. No placeholder shapes.

#### BUG-0416 (terminal task under `[A]` heals to `[x]`) — **pass**

Scratch-root probe (`tester-012-probes/b0416/probe.sh`): real function bodies extracted from the RC's `wake_task_state.sh`, `wake_reconcile_sweeps.sh`, `wake_task_transition.sh` (same awk extraction the RC's own tests use), real `overwatch_protocol.sh`/`error_contract.sh`/`temp.sh`/`lock_provenance.sh`/`worktree.sh`, `KANBAN_ROOT`/`BACKLOG`/`TASKS_ROOT` pointed at a throwaway root, one `reconcile_stale_active` call per case:
- (A) DONE + `[A]` + no liveness record -> marker `[x]`, state stays DONE, log `probe-task-A is DONE in status.md; corrected marker [A] -> [x]`, no zero-progress entry. (acceptance bullet 1)
- (B) WORKING + `[A]` + record naming a live pid -> kept `[A]`, log `has active process, keeping [A]`. (bullet 2)
- (C) WORKING + `[A]` + no record -> log names the absent record path and the state, reset to `[ ]`/BACKLOG via zero-progress cycle 1/3 — the decision the sweep header records. (bullet 3)
- (D) DONE + `[A]` + record naming a dead pid -> `[x]`, not `[ ]` (state branch wins over the orphan-reset branch).
- **Original failure mode reproduced** with the same probe against the `ai_main` copies of the three libs: case (A) logs `has active process, keeping [A]` and the marker stays `[A]`; case (C) likewise kept forever. The RC changes exactly that.
- Bullet 4 (the two v2.25.1 live entries read `[x]` after the fixed sweep runs against the live install) cannot be exercised by TESTER: the live install still runs the v2.25.1 sweep (`cmp` of live `scripts/lib/wake_reconcile_sweeps.sh` against `ai_main` is identical) and only `install.sh` at release replaces it. Observed instead: `CODER-20260927-037-icd-contract-bump` is still logged `has active process, keeping [A]` by every coder wake today (10:0x lines in `logs/cron-coder.log`), i.e. the live defect persists until this RC ships. Recorded as **pass-with-caveat** on that bullet; no code gap.

#### BUG-0422 (repo lock held through a losing chain's exit) — **pass**

Probe (`tester-012-probes/b0422/probe.sh`): process A acquires the lock through `pgai_lock_acquire`; the file is unlinked (the reaper's action); process B acquires a new inode at the path and holds it; A then calls `pgai_lock_release` exactly as the exit sweep does.
- RC `lock_provenance.sh`: A narrates dual-channel `lock.provenance.release_not_owner` ("… the path now holds holder=holderB …"), returns 0, B's file survives with B's inode and provenance, and a third process C is **contended** (`B still holds`).
- **Original failure mode reproduced** with `ai_main`'s `lock_provenance.sh`: A's release unlinks B's file (`FILE GONE`) and C **acquires while B is live** — the hand-down chain BUG-0422 describes.
- `process_one_task`'s `set +e` before `return 3` (wake_driver.sh) plus the errexit bracket in the caller is covered by the RC's integration test `test_chain_lock_held_through_agent.py` (three real wake chains, losing chain must abandon, winner's finalize completes); ran green under direct pytest as a debugging aid (verdict deferred to the gated integration runner, Step 5a). Census row 66 added for the new CONVERT site; `docs/blocking-site-census.md` rollup updated to 66 sites / 28 CONVERT.
- Live evidence of the pre-fix behaviour this cycle: `logs/cron-coder.log` 10:04:07 `lock.provenance.reverify_lost` for CODER-20260928-009 (lock taken by tester pid 1841877 while the coder chain's subagent was still finishing), followed by the exit sweep releasing the path — the same hand-down on the unshipped live install. Not a regression of the RC; it is the defect the RC fixes.

#### BUG-0419 (per-invocation pytest basetemp) — **pass**

- Helper probe: two calls of `gated_runner_pytest_basetemp unit` -> `pytest/unit-TESTER-20260928-012-verify-2-25-2.KKiUro` and `.AlfcWL` (distinct, task-scoped); with `PGAI_TASK_ID='probe/task id!'` -> `integration-probe_task_id_.Xq4XN0` (scope sanitized); `gated_runner_pytest_basetemp_cleanup` removed all three including one with a read-only leftover; empty argument is a no-op rc 0; a path outside the temp root (`/etc`) is refused by `pgai_temp_cleanup` ("refusing to remove '/etc'") and left intact.
- My own Step 5a unit run's replay line names `--basetemp=/tmp/pgai_kanban_tmp/pytest/unit-TESTER-20260928-012-verify-2-25-2.H02CKm` and that directory existed during the run (acceptance bullet 2). Removal on exit is checked after the runner finishes (below).
- Acceptance bullet 1 (two simultaneous invocations of the same runner both exit 0) was exercised by CODER-003 (`tasks/CODER-20260928-003-per-run-pytest-basetemp/logs/concurrent-acceptance.log`, unit-A/B and integ-A/B logs, distinct basetemps, rc 0 each); this verification did not repeat a 4-runner concurrent run. Recorded as pass on the mechanism (distinct directories by construction via `mktemp -d`) with the concurrent-run evidence cited from the CODER task's own logs.
- Runner headers and `docs/test-authoring-guidelines.md` "Every invocation gets its own basetemp" describe the shape (Documentation Discipline: satisfied).

#### BUG-0423 (fixtures pinned to synthetic board) — partial, **pass** so far

- `pytest --setup-plan` on `pgai_agent_kanban/api/tests/test_adapter.py` with the RC conftest: 11 `SETUP F _block_live_kanban_writes` and 11 `_sandbox_crontab`; with the `ai_main` conftest temporarily substituted (then restored; tree clean): 0 of each — the API package's safety net now applies.
- `tests/fixtures/api_server.py` pins `KANBAN_ROOT` and clears `PGAI_TASKS_DIR`/`PGAI_PROJECT_ROOT`/`PGAI_PROJECT_NAME`; `test_action_parity_fixtures.py` gains the ambient-root HALT guard test.
- Acceptance bullet 1 (integration runner with a fleet HALT armed on a throwaway root leaves it byte-identical, sentinel_leak zero differences) is exercised below as a separate throwaway-root runner invocation after Step 5a.

#### BUG-0420 (published Public ID is spent) — code **pass**; live-instance acceptance bullet 1 **gap** (see Finding 1)

- Seeded reproduction (`tester-012-probes/b0420-seed/probe.py`, scratch repo with `release-notes/PUBLISHED`=v1.0.0, a `CHANGELOG.md` publishing `KI-1.0.0.1 — An older defect …` with no surviving bug file, and one new bug against v1.0.0 with no Public ID): RC writer renders `KI-1.0.0.1` (published line byte-identical) **and** `KI-1.0.0.2` for the new bug, persists `KI-1.0.0.2` into the bug file, and is a fixed point on the second regeneration. **Original failure mode reproduced** with the `ai_main` package: the new bug is handed `KI-1.0.0.1` and the published line is rewritten. (acceptance bullet 2)
- Live tree: `python3 -m pgai_agent_kanban.cm.changelog_writer <worktree> <copy of live bugs/>` -> byte-identical to the committed `CHANGELOG.md`, and no bug file in the copy was written. Unit runner's `changelog_freshness` pre-flight: OK (bullet 3 is checked again by the integration runner's pre-flight in Step 5a).
- Bullet 1 as written — "leaves line 1428's `KI-1.23.0.1 — resolved (…)` entry byte-identical and assigns BUG-0417 a Public ID not already present" — is **not met and cannot be met by the writer alone on this tree**: commit `06171cf` ("Update CHANGELOG.md for v2.25.1", tagged `ai_v2.25.1`, on `ai_main`) already replaced that published line with BUG-0417's text, so the artifact now publishes `KI-1.23.0.1` for BUG-0417 (lines 1443 and 1526) and BUG-0417's file persists `KI-1.23.0.1`. The RC's writer correctly treats the *current* published line as the truth (it is byte-identical on regeneration). The original meaning of `KI-1.23.0.1` survives only in git history (`git show 06171cf^:CHANGELOG.md` line 1428). Restoring it is a data repair of the published artifact — after which the RC's writer will, by its own design, keep the restored line verbatim and reissue BUG-0417 a fresh `KI-1.23.0.2`. Filed as a gap (Finding 1). `Regression: no` (published at v2.25.1, before this RC).

#### BUG-0421 findings-file check — **pass**

`findings-unit.json` carries exactly one finding, `lint: engine_size_dupe`; no `test_anti_patterns` entry (acceptance bullet 2, unit half; integration half in Step 5c).

### Step 5a — gated suites

- `bash team/scripts/run-unit-tests.sh --coverage` from the worktree root, output at `/tmp/pgai_kanban_tmp/tester/unit-2.25.2.log`: **`UNIT_EXIT=0`** — lint pre-flight passed (every fatal-class lint OK; one quality-class finding, `engine_size_dupe`, exit 3, non-blocking), pytest `5319 passed, 43 skipped in 978.91s`, post-suite `bare_tmp_cleanliness` OK, `listener_cleanliness` OK, `[pgai-sentinel] live kanban root $HOME/pgai_agent_kanban: HALT / HALT-AFTER / DEFER-LOW-BUGS set unchanged by the suite`, `sentinel_leak` OK, `scratch_sweep` OK. The run's per-invocation basetemp `pytest/unit-TESTER-20260928-012-verify-2-25-2.H02CKm` existed during the run and was gone after exit (BUG-0419 acceptance bullet 3).
- `bash team/scripts/run-integration-tests.sh --coverage`: (recorded below when complete)

### Step 5b — coverage (informational)

- Unit (`${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/unit.json`, `totals`; field names verified: `percent_statements_covered`, `percent_branches_covered`, `percent_covered`): line 77.46 % (14162/18284 statements), branch 63.52 % (3407/5364), blended 74.29 % (the `^TOTAL` `Cover` column reads 74 %).
- Integration: (recorded below)

- `bash team/scripts/run-integration-tests.sh --coverage` from the worktree root, output at `/tmp/pgai_kanban_tmp/tester/integration-2.25.2.log`: **`INTEGRATION_EXIT=0`** — breakage-class gate OK (534 python / 286 shell files), every fatal-class lint OK (`test_anti_patterns` OK, `skip_cites_real_bug` OK, `changelog_freshness` OK, `rc_empty_commits` OK, …), one quality-class finding (`engine_size_dupe`, exit 3, non-blocking), pytest `623 passed, 1 skipped in 880.75s`, `bare_tmp_cleanliness` OK, `listener_cleanliness` OK, `[pgai-sentinel] … set unchanged by the suite`, `sentinel_leak` OK, `scratch_sweep` OK. Per-invocation basetemp `pytest/integration-TESTER-20260928-012-verify-2-25-2.6gziCO` removed on exit.
- Both suites were run strictly sequentially and no other pytest session was live on the host during either (checked with `ps`). All tests passed; no `bug`, `stale-assertion` or `pre-existing-bug` category applies to a test result.
- Integration coverage (`coverage/integration.json` `totals`): line 13.31 % (2433/18275), branch 10.46 % (561/5364), blended 12.67 % — informational only (integration tests exercise bash entry points, not the Python package, so the Python-only figure is low by design).

### Step 5c — lint findings (non-blocking) — see `## Lint Findings (non-blocking)` below

`findings-unit.json` (1 finding) and `findings-integration.json` (1 finding): both `lint: engine_size_dupe`, `severity: quality`, exit 3, `lib/cm/release_ship_policy.sh -- 844 lines (ceiling: 800)`. No open or running bug in `bugs/` described it (BUG-0424 is the fix that produced the growth, not the size finding) -> filed one bug for the class covering both suites, BUG-0428 (Path C-A). No `test_anti_patterns` entry in either file (BUG-0421 acceptance bullet 2 met).

### Steps 6.6 / 6.7 — runner exit codes

`UNIT_EXIT=0`, `INTEGRATION_EXIT=0`. Both suites PASS; the PASS evidence cited is the gated runners' exit codes.

### Step 6.8 — synthetic release simulation

The diff touches `team/scripts/lib/cm/release_ship_policy.sh` (sourced by `cm-release.sh`), so the simulation applies. `team/tests/integration/test_cm_release_synthetic.py` ran inside the gated integration suite (exit 0) and, as a debugging aid, under direct pytest with the seven other new/changed modules (105 passed): `cm-release.sh` completes against a fresh isolated temp repo. The RC's own `test_ship_policy_acceptance.py` additionally drives the real `release.sh` through a level-4 known-issue ship (no narration) and a zero-block report (HALT on Trigger 2 with the Step 4d `Finding reader:` narration naming the report). **pass**

### Step 6.9 — constraint negative checks — **pass**

No secret-shaped literal in the added lines (grep for api-key/secret/password/token assignments: only prose hits such as "takes the repo lock"). `set -euo pipefail` present in both runners (RC line 121 / 138, same as `ai_main`); the changed `lib/*.sh` files are sourced libraries that by design set no shell options of their own (unchanged from `ai_main`). Cleanup traps preserved (`trap` count 13 / 23 in the two runners). Every changed `.sh` passes `bash -n`; every changed `.py` compiles.

### Step 6.11 — spot-check outside the spec (time-boxed)

- **State drift:** `release-state.md` Active RC `v2.25.2` matches the existing local branch `ai_rc/v2.25.2`; `release-state/v2.25.2.json` attempt 1, 9 cargo items, `in_progress`. CODER-009's queue marker read `[A]` while its state was `DONE` between 10:03:29 and 10:04:14 and again between 10:09:02 and 10:10:18 — the live install healed both through its own completion gate at 10:10:18 (marker now `[x]`). No drift remains. The RC's own `ai_feature/CODER-20260928-009-…` branch was deleted by the second chain at 10:08:14; the worktree teardown at 10:10:19 is logged. **observation, no finding**
- **Untested new mechanisms:** `_pgai_lock_path_verdict`'s `unknown` fallback (no `/proc`, pid-of-record comparison) has no direct fixture — it cannot be reached on this host; noted as an observation, not a finding. Everything else new (`task_liveness_verdict`, `_sweep_correct_terminal_marker`, `gated_runner_pytest_basetemp*`, `read_published_changelog`/`_next_public_id`/`_kept_published_lines`, `_build_collision_causes`, the level-aware heading parser, the API conftest re-export) is exercised by the RC's new tests and by the probes above.
- **Regressions of prior gaps:** BUG-0421's AP3 finding is gone from both findings files; BUG-0423's `sentinel_leak` is OK in both live-root runs; the unit runner's timing-sensitive `test_large_pre_today_body_renders_correctly_within_time_bound` (flagged by CODER-009 as host-load-sensitive, 3 of 4 runs) passed in this run.
- **Two concrete gaps found here** (each traces to a bundled bug's "Expected"): both runners exit on pytest failure before the `sentinel_leak` check (Finding 2, BUG-0426); the sibling runners `run-gated-tests.sh` / `run-cold-build-tests.sh` keep a fixed shared basetemp (Finding 3, BUG-0427).
- Observations for human consideration (outside the spec, not findings): (a) every worker README in this cycle carries `## Git Repo: none` next to `## Workflow Type: release` and real branches — three agents (CODER-002, CODER-009, WRITER-010) each spent effort reconciling it and recorded it under `## Instruction Conflicts`; the materializer's template is the likely source. (b) CODER-007 notes that a report with finding blocks but none rated `high` still reads `unknown unknown` silently — by design, since the dimensions are only consulted on a high-risk finding. (c) CODER-005 notes a narrow pre-existing two-step-release window (a waiter that opened the old inode before `rm` and flocks it after close) unrelated to this defect.

### Step 6.12 — autonomous operation criterion

1. Force-promoted WAITING->BACKLOG by hand? **NO.** `logs/cron-tester.log` 10:03:51 `WAITING task TESTER-20260928-012-verify-2-25-2: prerequisites satisfied, promoting to BACKLOG` (wake script); worker tasks were materialized WAITING on CM-011 and promoted by the wake prerequisite check (PM-001 summary).
2. `release-state.md` edited outside `open-rc`/`release`/`cancel-rc`? **NO.** File mtime `2026-09-28 03:20:43.60` equals `## RC Opened At` `2026-09-28T03:20:43+00:00` written by `scripts/cm/open-rc.sh` (CM-011); no later write.
3. Scripts patched mid-build? **NO.** No file under the live install's `scripts/`, `roles/`, `pm-agent/`, `pgai_agent_kanban/` has an mtime after the RC opened; the live `scripts/lib/wake_reconcile_sweeps.sh` is byte-identical to `ai_main`'s. Every commit touching `team/scripts/` on the RC is a ticket's own deliverable on its feature branch, merged `--no-ff`.
4. Queue files edited by hand? **NO.** Queue mtimes (`coder_backlog.md` 10:04, `tester_backlog.md` 10:03, `writer_backlog.md` 09:43, `cm_backlog.md` 03:24, `pm_backlog.md` 03:18) each coincide with a logged wake transition; every marker change for the twelve tasks is narrated in the cron logs.
5. Non-CM remote git operation? **NO.** No `git-boundary-guard.jsonl` exists in any of the cycle's task folders (`tasks/*20260928*/logs/`). Self-report per `docs/tester-verification-reference.md` "A YES the file never shows": this verification session ran no `fetch`, `pull` or `push`, never set or unset `PGAI_AGENT_ROLE`, and ran no CM-only path by hand (BUG-0418 acceptance bullet 3 met).

**Autonomous operation criterion: Manual interventions required: none.** One autonomous re-dispatch occurred: the live (unshipped, v2.25.1-code) wake driver lost the repo lock under CODER-009 at 10:04:07 after the subagent had marked DONE, the next wake's stale-active sweep reset the DONE task to BACKLOG and re-dispatched it, and the second session verified its merge was already on the RC and completed DONE through the completion gate at 10:10:18 (~6 minutes). That is the pre-existing BUG-0416/BUG-0422 shape this RC fixes, reproduced on the install that does not yet carry the fix; not a defect in the RC and not a manual intervention.

### Step 6.14 — coding-standards spot-check — **pass**

No new script under `team/scripts/` (no new `--help` surface). Added non-test lines carry no bug, task, RC or version identifier (grep over the diff excluding `tests/`: none); test additions cite only sanctioned example-shaped values (`BUG-0510`, `BUG-0006`, `CODER-20260101-001-example` as fixture inputs). The new basetemp helper and the shared Public-ID reader (`lib/public_id.py`) de-duplicate rather than duplicate existing implementations. The temp root and ceiling values are read from configuration/lint manifests, not hardcoded. CODER `## Stale Literal Risks` sections were read (advisory): each names generic literals (`unparseable`, `unknown`, `Replay this exact run:`, `KI-1.0.0.2`, `unknown unknown`) with the CODER's own trace showing the hits are unrelated — consistent with the green suites.

#### BUG-0423 acceptance bullet 1 — HALT-armed throwaway-root run — **pass-with-caveat**

A second `run-integration-tests.sh` (no `--coverage`) against a throwaway copy of the kanban root (`rsync` of the live root minus `logs/ tasks/ locks/ secrets usage/ metrics/`, its own temp dir, every wake-exported root name pinned to it and the pre-resolved `PGAI_TASKS_DIR`/`PGAI_PROJECT_ROOT`/`PGAI_PROJECT_NAME` cleared) with a fleet `HALT` of known bytes armed at its root: `HALT` sha256 `60914c1a…f4b4b0` before and after — byte-identical; no `HALT-AFTER` and no project-scope sentinel appeared; the live root's sentinel set stayed empty and untouched. **Caveat:** that run exited 1 on one unrelated test (`test_defer_behaviour_matrix.py::…::test_bundle_carries_the_trigger_and_every_deferred_low_with_ride_along_annotation`, 622 passed), which — because both runners exit before their post-suite checks on a pytest failure (Finding 2) — meant the `[pgai-sentinel]`/`sentinel_leak` line was never printed for that run; the byte-identity of the file is the direct evidence instead. The failing test pins its own root, passes in isolation six times including under the failed run's exact environment, and is untouched by the RC (Finding 4, BUG-0429). The authoritative Step 5a runs on the live root printed `set unchanged by the suite` for both suites.

### Follow-up items (numbered finding blocks)

### Finding 1: published `KI-1.23.0.1` line was rewritten at v2.25.1; one-time restore needed

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks — one edit of the published artifact plus one regeneration)
**Systemic Risk:** low (isolated to one published identifier)
**Filed As:** BUG-0425 (Path C-A — filed as BUG — code defect / regression / test failure)
**Regression:** no
**Integrity:** none

BUG-0420 acceptance bullet 1 is not met on this tree and cannot be met by the writer alone: commit `06171cf` (tagged `ai_v2.25.1`) already published `KI-1.23.0.1` with BUG-0417's text. The RC's writer is correct (seeded reproduction: `KI-1.0.0.2` issued, published line kept; `ai_main` writer reissues `.1`) and treats the current artifact as spent truth. Restoring the original line (`git show 06171cf^:CHANGELOG.md` line 1428) once will make the RC's own writer reissue BUG-0417 `KI-1.23.0.2` and keep the restored line verbatim.

### Finding 2: both gated runners skip the `sentinel_leak` check when pytest fails

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low
**Filed As:** BUG-0426 (Path C-A)
**Regression:** no
**Integrity:** none

`run-unit-tests.sh` line 1001 / `run-integration-tests.sh` line 799 exit before the sentinel check at lines 1081 / 879 (unchanged from `ai_main`). Observed live in this task's HALT-armed run and, last cycle, as the unattributed 02:05:16Z HALT arming. Traces to BUG-0423 "Expected". CONVERT class must be preserved by the fix.

### Finding 3: `run-gated-tests.sh` and `run-cold-build-tests.sh` still pin a fixed shared basetemp

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low
**Filed As:** BUG-0427 (Path C-A)
**Regression:** no
**Integrity:** none

The two sibling runners keep `pytest/gated` and `pytest/cold` (lines 163-168 / 199-204), the collision shape BUG-0419 describes; the per-invocation helper now exists and can be reused. Named by CODER-003 as outside its scope.

### Finding 4: non-deterministic failure in `test_defer_behaviour_matrix.py` (one full run of two)

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low
**Filed As:** BUG-0429 (Path C-A)
**Regression:** no
**Integrity:** none

Failed once in the secondary throwaway-root run with discovery's `no valid item paths after filtering` skip; passed in the authoritative run and in six isolated reruns including the failed run's exact environment. Pre-existing test (v2.13.0) and lib (v2.24.13), untouched by the RC; cause not determined within the time box — filed with the evidence and the diagnostic path, per "ambiguity defaults to bug".

## Lint Findings (non-blocking)

- `engine_size_dupe` — `lib/cm/release_ship_policy.sh -- 844 lines (ceiling: 800)`, severity `quality`, exit 3, recorded by both `findings-unit.json` and `findings-integration.json` (one class, both suites listed) — no open bug cited it; filed BUG-0428 (Path C-A). Introduced by CODER-007's +44 lines closing BUG-0424; CONVERT class. Does not change `## Recommendation` or `## Systemic Risk`.
- No other entry in either findings file; no `test_anti_patterns` entry (BUG-0421 closed).

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=21d freshness=stale — the record is 21 days old against a 7-day window and is for v2.9.20, not v2.25.2, so both freshness conditions fail; the run that produced it filed BUG-0167 (`## Status` done), which is cited here rather than re-filed, and `project.cfg` sets `deep_verify = false`, so no newer record exists for this tree (which ships `docker/claude-abliterated`, `docker/debian`, `docker/rocky9` Dockerfiles and carries `team/scripts/deep-verify.sh`).

## Autonomous Criterion

Manual interventions required: none (five NO answers in Step 6.12 above; one autonomous re-dispatch of CODER-009 by the unshipped live driver, self-healed, is the pre-existing defect this RC fixes).

## Caveats

- **This report may be rotated by the live install before CM reads it.** My own wake chain (tester pid 1841877) lost the per-project repo lock during this task: its lock fd points at a deleted inode (`/proc/1841877/fd/12 -> …/repo-wake-pgai-agent-kanban.lock (deleted)`), the coder chain's exit sweep having released the path at ~10:04-10:10 (the BUG-0422 hand-down, on the v2.25.1 code the live install still runs). At finalize the live driver will most likely log `chain stopping before finalize/merge-verification — repo lock ownership lost`, and the next tester wake's stale-active sweep (old code) may reset this DONE task to BACKLOG, rotating this file to `report.md.previous-RUN-1` and re-running the verification as RUN-2 — exactly the loop TESTER-20260927-042 went through. If that happens, this RUN-1 report is complete and its verdict stands on commit `8de4336`; RUN-2 should reach the same verdict on the same commit. Nothing in TESTER's lane can prevent it (locks and queues are never touched by hand).
- Verification is pinned to `8de4336`. CODER-009's autonomous second session did not add a commit; the RC tip was unchanged from the start to the end of this verification.
- The `ini_parser.sh: No such file` lines in the BUG-0422 probe output are an artifact of copying `lock_provenance.sh` out of its lib directory for the `ai_main`/RC comparison (its self-source of the config parser fails); they affect only the stale-threshold read, not the acquire/release path under test.
- BUG-0416 acceptance bullet 4 (the two v2.25.1 live `[A]` entries healed by the fixed sweep "against the live install") and BUG-0419's four-runner concurrent run were not repeated by TESTER: the first can only happen after `install.sh` at release; the second is on disk in CODER-003's task logs and the mechanism was probed directly.
- Direct pytest results quoted anywhere above (105 passed; six isolated reruns) are debugging aids; PASS evidence is `UNIT_EXIT=0` and `INTEGRATION_EXIT=0`.
- Observations outside the spec for human consideration (not findings): worker READMEs carry `## Git Repo: none` beside `## Workflow Type: release` (three agents reconciled it independently); the role file's "Git workflow" clause (commit artifacts on a feature branch) conflicts with the wake prompt and Step 9 — followed the prompt, no branch created, artifacts in the task folder only.
- Scratch and probe output: `/tmp/pgai_kanban_tmp/tester/{unit,integration}-2.25.2.log`, `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester-012-probes/` (b0416, b0420-seed, b0420-live, b0422, halt-root, synthetic reports). No bare `/tmp` entry created.
