# Verification Report: ai_rc/v2.26.2

## Summary

Fresh verification of `ai_rc/v2.26.2` (commit 5d1bdec, 53 files / +5768 -708 against `ai_main` e192a96) against `requirements/v2.26.2-bugfix-bundle-20260929.md`, a discovery-pipeline bundle of eight bugs: BUG-0428 (ship-policy lib over the engine ceiling), BUG-0438 (ship-policy fails open on value formatting), BUG-0439 (Fix Effort grep matches wrong text), BUG-0440 (equal runtime limits defeat preemption), BUG-0448 (watchdog counts iterations), BUG-0441 (stop file stops one project), BUG-0442 (task-state writes not atomic), BUG-0443 (config loader accepts anything). Every bundled item is addressed and was exercised behaviorally against the RC's own function bodies with the `ai_main` copy reproducing the original failure on the same input; Python compiles; 301 bash files pass `bash -n`; the engine-size lint reports 0 oversized files; both gated runners exited 0 with coverage. Two low-severity follow-ups were filed via Path C-A: stale prose citations left by this release's function moves (BUG-0457) and a pre-existing cosmetic blank-line growth in `set_state`/`set_block` (BUG-0458). No regression, no serious defect, no autonomy failure.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
low

## Pre-flight (Steps 1-3)

- Step 1: status was WORKING with empty artifacts/ and logs/ (wake-stamped); treated as fresh start. No prior report.md / gaps.md existed.
- Step 2: every DONE task with Release Version v2.26.2 and a non-none Feature Branch (CODER-012..017, 019, WRITER-018) has had its branch deleted post-merge (passes both checks trivially); each has a `--no-ff` merge commit on ai_rc/v2.26.2 (cd37e3e, 0877a32, 102e668, 26a3824, 531176b, 6ff45e0, b4a9e8e, 5d1bdec). CM-020 and PM-002 are `Feature Branch: none`.
- Step 3: `git rev-parse HEAD` == `git rev-parse ai_rc/v2.26.2` == 5d1bdec30e1b390843de55742390808bd3833773; `git status --porcelain` empty; `git stash list` empty. ai_main at e192a96.

## Findings

Legend: `Path C` (autonomous follow-up filing — non-blocking); `Path C-A` (filed as BUG — code defect / regression / test failure); `Path C-B` (filed as PRIORITY — enhancement / design change). Every finding below is categorized `pass`, `pass-with-caveat`, `gap`, `bug`, or `stale-assertion`.

### Finding 1: Stale file-path citations left by this release's function moves

**Category:** gap
**Fix Effort:** small (1-2 CODER/WRITER tasks — three one-token prose edits)
**Systemic Risk:** low (documentation drift; no caller or test affected)
**Filed As:** BUG-0457-stale-path-citations-after-split (Path C-A)
**Filed By:** TESTER (autonomous Path C) — three prose citations still name the file a function lived in before this release moved it, so a reader following the role file or the two test docstrings is sent to a file that no longer defines the function.

Traces to: SOP-core "Documentation Discipline" applied to BUG-0428's split and CODER-019's split; Step 6.3 documentation-layer review. `team/roles/TESTER.md:252` still says `read_ship_policy_finding_dimensions` lives in `team/scripts/lib/cm/release_ship_policy.sh` (it is now defined only in `release_ship_policy_report.sh`; the parent sources it, so it resolves); `team/tests/unit/test_wake_composer_governance_manifest.py:5` and `team/tests/unit/test_composer_manifest_isolation.py:15` still say `_wd_build_governance_line` lives in `wake_driver_invoke.sh` (now `wake_governance_composer.sh`, self-sourced by the invoke stage; both tests pass). CODER-012 recorded the TESTER.md line as a possible stale assertion and left it for WRITER; the bundle's WRITER ticket was scoped to `team/roles/CM.md` only. One filing for three instances: same cause (functions moved to new lib stages without a prose sweep), same fix.

### Finding 2: set_state / set_block grow one blank line per call (pre-existing)

**Category:** bug (pre-existing — not a regression of this RC)
**Fix Effort:** small (1 CODER task)
**Systemic Risk:** low (cosmetic; the wake path's normalize pass masks it)
**Filed As:** BUG-0458-set-state-grows-blank-lines (Path C-A)
**Filed By:** TESTER (autonomous Path C) — every call to the bash status writers set_state and set_block adds one extra blank line under the section they rewrite, so a status.md that is written several times without a normalize pass slowly fills with empty lines.
**Regression:** no
**Integrity:** none

Raised by CODER-016's status Summary; confirmed by probe: three back-to-back `set_state` calls on a fixture with one blank line under `## State` leave five, on `ai_rc/v2.26.2` AND on the `ai_main` copy of `wake_task_state.sh` (identical output), so the atomic-write rewrite neither introduced nor fixed it. `grep -li "blank line" bugs/` found no existing filing. Rated low per SOP "Rating a Finding" (cosmetic; workaround exists — `normalize_status_file`, which the wake prep/finalize stages already call).

### Finding 3: BUG-0428 — ship-policy lib under the engine ceiling

**Category:** pass

- `wc -l team/scripts/lib/cm/release_ship_policy.sh` = 764; sibling `release_ship_policy_report.sh` = 548 (new, 0755, own double-sourcing guard; `release_ship_policy.sh` sources it below its guard).
- `cd team && python3 scripts/lint_engine_size_dupe.py` -> `ok -- 41 engine file(s) within the 800-line ceiling, no cross-file function-name collisions found`, exit 0.
- Sourcing `release_ship_policy.sh` alone resolves `cm_ship_policy`, `read_ship_policy_finding_dimensions`, `cm_release_apply_ship_policy` (probe in Finding 4 used exactly that). Both gated runners' findings files carry no `engine_size_dupe` entry (Step 5c).

### Finding 4: BUG-0438 — ship-policy values normalized; unparseable floors to SHIP-WITH-CONCERNS + filed bug

**Category:** pass

Probe (`read_ship_policy_report_values` + `cm_ship_policy` sourced from the RC; fixtures under `$PGAI_AGENT_KANBAN_TEMP_DIR/tester/shippolicy/`):

| Report fixture | normalized recommendation / risk | cm_ship_policy (DONE, task found) |
|---|---|---|
| `**SHIP-WITH-SERIOUS-CONCERNS**` (bold) | ship-with-serious-concerns / low | SHIP-NON-FUNCTIONAL |
| `**Recommendation:** **SHIP-WITH-SERIOUS-CONCERNS**` + `**Systemic Risk:** Low` | ship-with-serious-concerns / low | SHIP-NON-FUNCTIONAL |
| `High — dashboard breaks on load` with a high finding (Regression no / Integrity none) | pass / high | SHIP-KNOWN-ISSUE (Trigger-2 row reached) |
| template HTML comment kept above `SHIP-WITH-CONCERNS` / `medium` | ship-with-concerns / medium | SHIP-KNOWN-BUGS |
| `SHIPP-WITH-CONCERNZ` | unparseable / low | SHIP-KNOWN-BUGS (never HALT, never FUNCTIONAL) |
| unedited template placeholder `PASS \| SHIP-WITH-CONCERNS \| ...` | unparseable / unparseable | SHIP-KNOWN-BUGS |
| no `## Recommendation` section at all | none / none | SHIP-KNOWN-BUGS |

Contrast on the same fixtures with the `ai_main` copy of the lib: bold -> SHIP-FUNCTIONAL, garbled -> SHIP-FUNCTIONAL (the original fail-open). The filed-bug path (`_cm_ship_policy_file_input_bug` -> `pgai_agent_kanban.cm.ship_policy_bug_filer`, BUG-TEMPLATE shape, atomic ID claim, dedup on `kind|field|source_path`, never fails the release) is covered end-to-end through the real `cm-open-rc.sh` + `cm-release.sh` by the three new cases in `team/tests/integration/test_ship_policy_acceptance.py` (garbled -> KNOWN-BUGS + bug quoting raw text and report path; bold serious -> NON-FUNCTIONAL; decorated README Role/Version -> task still found + one lookup bug per field) and by `test_ship_policy_bug_filer.py` (10 tests); both ran green inside the gated runners. `read_state_field.py` skips single- and multi-line `<!-- -->` blocks and inline comments (diff read; HTML-comment fixture above exercises it). `team/roles/CM.md` ship-policy passage (WRITER-018) now matches the code: normalization rule, the two KNOWN-BUGS rows, HALT rows read state only, lookup-mismatch outcome; the old "closing that gap is a code change" sentence is gone; diff confined to lines 79-111. `docs/blocking-site-census.md` row 12 reworded accordingly.

### Finding 5: BUG-0439 — has_large_fix_effort anchored to each finding's own Fix Effort line

**Category:** pass

Fixture with all-small findings whose prose contains "the Fix Effort is not large", the legend "small / medium / large", "larger", and a mid-sentence "Fix Effort: large" -> `has_large_fix_effort=no`; a SHIP-WITH-SERIOUS-CONCERNS report on it -> SHIP-NON-FUNCTIONAL (was HALT via Trigger 3 under the old whole-report `grep -qi "Fix Effort.*large"`, which matched the same fixture). A finding whose own line reads `**Fix Effort:** large (architectural)` -> `yes` -> HALT (unchanged matrix behavior for a true large finding, as the ticket constrained).

### Finding 6: BUG-0440 — per-task timeout capped by remaining project and batch budgets; >= nesting warning

**Category:** pass

- `wd_effective_task_timeout` (new `wake_task_budget.sh`, 140 lines, read fully): `120 120 60 120 60` -> 60; `21600 21600 21000 21600 21000` -> 600; `0 0 0 0 0` -> 0 (all disabled); exhausted budget clamps to 1; non-numeric ceiling reads as "no bound".
- `wd_chain_task_timeout_budget` with a mocked `_wd_epoch_now` 60 s into a 120/120/120 batch sets `_task_timeout_budget_seconds=60`; `run_project_chain` calls it before BOTH `process_one_task` dispatch sites (diff read).
- `wd_warn_runtime_bound_nesting demo 120 120 120` logs two WARNINGs, each ending "the runtime ceilings must nest task < project <= batch"; `100 120 120` logs nothing; the `ai_main` logic is silent on equality (the bug). The invariant appears in `config_loader.sh` comments and `kanban.cfg_example` for all three keys.
- **Real clock (the ticket's AC):** ceilings 120/120/120, a 100 s provider dispatched with the batch clock already 60 s old -> armed 60 s ("capped from max_task_seconds=120s by the remaining project/batch budget" in the log line), `timeout fired after 60s (limit 60s)`, batch elapsed at return 122 s = cap + 2 s grace. Finalize now reports the armed limit (`_provider_timeout_seconds`) in its drain line and BLOCKED reason (diff read; the `exceeded max_task_seconds (` prefix other readers grep is preserved).

### Finding 7: BUG-0448 — watchdog compares wall-clock against an absolute deadline

**Category:** pass

The WATCHDOG-INVOKE block (extracted between its markers exactly as the unit tests do, with the RC's `wake_task_budget.sh` for `_wd_epoch_now`) run on the real clock while the unit suite was executing concurrently (load avg 2.16): a 30 s limit against a `sleep 300` provider fired at **30 s** (`task probe: timeout fired after 30s (limit 30s); sending SIGTERM ...`), block returned after 31 s including a 2 s grace; exit 124, `_task_killed=true`. The lock-refresh cadence kept firing every 10 s of wall-clock inside the deadline loop (constraint: refresh must survive the deadline-driven loop). Unit coverage: `test_wake_task_timeout_budget.py` (mocked clock file, real-clock within-2-seconds test, iteration-count-free assertion, budget wiring before every dispatch) — 16 tests, ran green inside the gated unit run.

### Finding 8: BUG-0441 — stop file stops every batch, every project, every agent; never unlinked

**Category:** pass

- `wake_stop_file_present` (new, `wake_common.sh`) read at four boundaries (diff read): between tasks in `run_project_chain` (the old `rm -f "$STOP_FILE"` is gone — `grep -rn 'rm -f "$STOP_FILE"' team/scripts` is empty), before every project including the first, before the contention retry pass (ahead of the budget check so the log names the operator's stop), and at the top of each retry iteration.
- `kanban.cfg_example` stop_file block states scope (all agents, all projects), effect, the check points, and removal ("yours alone. No wake ever deletes or renames the file"); HOW_TO.md table row + "Stop the wake batches" paragraph and the claude.sh/codex.sh headers corrected from the one-shot self-delete wording.
- End to end: `team/tests/integration/test_wake_stop_file_stops_batch.py` (new, 563 lines) drives the REAL `scripts/wake/claude.sh --agent=...` for a writer batch and a tester batch concurrently over four registered document-workflow projects with a stub provider; test 1 touches the file mid-task and asserts both batches stop at their next boundary and the file persists; test 2 asserts a batch started while the file exists dispatches nothing until the operator removes it. Ran green inside the gated integration run (see Suites). CODER-015 also exercised the prevention by swapping the two libs back to the pre-fix versions and observing the test fail ("was removed by a wake batch").

### Finding 9: BUG-0442 — atomic status.md and queue writes; rollback failure refuses loud

**Category:** pass

- New `atomic_write.sh` (137 lines, read fully): same-directory `mktemp` scratch (`.<name>.tmp.XXXXXX`, mode copied from the target or umask default), producer run as an `if` condition so `set -e` still reaches cleanup, `mv -f` commit, scratch removed on every failure path. Every writer in `wake_task_state.sh` (`ensure_status_file`, `normalize_status_file`, `set_state`, `set_block`, `stamp_model_field`, `mark_backlog`, both `promote_*`) routes through it (diff read); `queue_backup` removed from `wake_task_transition.sh`; status rollback goes through the same helper; `wake.transition.rollback-failed` refusal (BLOCK-WORTHY comment, `CENSUS-ANCHOR: atomic-transition-02`, Locator Index entry present); SIGTERM/SIGINT/SIGHUP trapped only for the window between the two writes.
- **Probe (sandbox root):** a reader loop `cat`-ing `status.md` while `set_state` toggled WORKING/BACKLOG 300 times: 3,850 reads, **0 empty, 0 partial**; same over `mark_backlog` on the queue file: 4,024 reads, 0/0; no scratch litter. The same reader over the `ai_main` `set_state`: 2,780 reads, **9 empty / 9 partial** — the probe is sensitive and the old code exhibits the bug.
- **Forced rollback failure:** `mark_backlog` stubbed to make the task directory read-only and fail -> `transition_task_state` returns 1 with `error: wake.transition.rollback-failed`, a message naming both files, a `remedy` that is the runnable `cp -- '<snapshot>' '<status.md>'`, the snapshot retained (1 file), and `## State` left reading WORKING — the log never claims "rolled back". `lint_gate_census_compliance: OK -- 36 file(s) checked, 0 offenders` in the unit pre-flight.

### Finding 10: BUG-0443 — kanban.cfg validation

**Category:** pass

Probe (`load_config` sourced from the RC in an `env -i` shell against sandbox cfg files) and `bash team/scripts/verify-config-loader.sh` -> `46 / 46 ... VERDICT: PASS`, exit 0:

- `max_runtime_seconds = 300 # five min` and `max_task_seconds = 100 ; inline semi` -> 300 / 100 (the `ai_main` loader yields `'300 # five min'` and `((: 300 # five min: syntax error` in arithmetic — the original failure).
- CRLF-saved file incl. `dashboard_status_glyph = #` -> every key loads; the `#` glyph value survives.
- `max_runtime_seconds = 300s` -> `error: config_loader.wake.not_an_integer`, message naming key and value, remedy `sed -i 's/^[[:space:]]*max_runtime_seconds[[:space:]]*=.*/max_runtime_seconds = 300/' "<cfg>"`, rc=1; env override `MAX_TASK_SECONDS=90m` -> same key, remedy `export MAX_TASK_SECONDS=90`.
- `max_task_per_wake = 3` -> `WARNING: [wake] max_task_per_wake ... Nearest known key: max_tasks_per_wake.`; `[wakeup]` -> `WARNING: section [wakeup] ... Nearest known section: [wake].`; rc=0 (never refuses).
- `agent_lock_timeout_seconds` removed from `kanban.cfg_example`, the loader registry, HOW_TO.md and docs/OPERATIONS.md (diff read; `grep` of the RC finds no consumer). Census anchor `config-loader-wake-integer-01` + Locator Index entry present. **Upgrade check on the live install (read-only):** every live `[wake]` value is decimal digits, so the new refusal will not stop a wake after upgrade; see Caveats for the one WARNING the live file will draw.

### Finding 11: CODER-019 — combined-tree verification and the two extra lib splits

**Category:** pass

`python3 -m compileall -q team/` exit 0; `find team -name '*.sh' -exec bash -n {} +` exit 0 over 301 files; engine-size lint 0 oversized (Finding 3). The two functions moved by CODER-019 were checked byte-for-byte: `setup_project_directories`, `get_workflow_type_from_readme`, `get_source_branch_from_readme` (`ai_main` `wake_driver.sh` -> `wake_driver_task_setup.sh`) and `_wd_build_governance_line` (`ai_main` `wake_driver_invoke.sh` -> `wake_governance_composer.sh`) are VERBATIM; both new stages are 0755, registered in the lint's `ENGINE_FILES`, and `wake_driver_invoke.sh` self-sources the composer via `BASH_SOURCE` so tests that source the invoke stage alone still resolve it. `docs/read-order-manifests.md` and the seven `read-order/*.txt` header comments name the new composer file. No test was edited by this ticket.

### Finding 12: Requirements-doc criteria and bundle bookkeeping

**Category:** pass

All eight bundled items addressed (Findings 3-10); Python compiles; bash syntax clean. `bug_backlog.md` reads `[x]` for BUG-0428, 0438, 0439, 0440, 0441, 0442, 0443, 0448; each bug file's `## Status` is `running` (CM flips to `done` at release). `release-state/v2.26.2.json` cargo lists exactly those eight.

### Finding 13: Constraint negative checks (Step 6.9) and skip gate (Step 6.13)

**Category:** pass

Added non-test lines of the diff (census doc excluded, which documents anchors) contain no `BUG-NNNN`, task-ID, or `v2.2x.y` citation (naming rule). `set -euo pipefail` retained in `verify-config-loader.sh`; `set -e` retained at the same point in `claude.sh`/`codex.sh` (one comment line shifted it 308 -> 309). No secret-shaped literals added. Skip gate: `lint_skip_bug_gate: OK — all skip annotations are compliant` in both pre-flights; my own grep's only `BUG-` hits near `skip` are the skip-gate lint's own test fixtures (`test_skip_bug_citation_scan.py`, BUG-1234/1235) and an artifact table (BUG-0079), not skip annotations.

### Finding 14: Prerequisite graph (Step 6.10) and release-state sanity (Step 6.1)

**Category:** pass

37 prerequisite edges across the v2.26.2 task set, 0 missing folders, no cycles (CM-020 open at the head; every CODER/WRITER ticket depends on it; 019 depends on 012-018; TESTER-021 on 012-019). `release-state.md`: `Active RC v2.26.2`, `RC Opened At 2026-09-29T09:57:49+00:00`, `RC Opened By Task CM-20260929-020-open-rc-2-26-2`, `Last Released v2.26.1` (well-formed); `pp_last_released_version pgai-agent-kanban` = v2.26.1 and `semver_gt v2.26.2 v2.26.1` is true. `VERSION` in the tree still reads v2.26.1 — by design; `release.sh` writes it at tag time.

### Finding 15: Synthetic release simulation (Step 6.8)

**Category:** pass

The diff touches `team/scripts/lib/cm/*`, so the simulation was required: `python3 -m pytest -p no:randomly tests/integration/test_cm_release_synthetic.py` under a throwaway `KANBAN_ROOT`/`PGAI_AGENT_KANBAN_ROOT_PATH`/`PGAI_AGENT_KANBAN_TEMP_DIR` with `PGAI_TASKS_DIR`/`PGAI_PROJECT_ROOT` cleared -> **17 passed in 63.49s**. (Debugging-aid evidence only for this step; the suite verdict is the gated runners below.)

### Finding 16: Coding-standards spot-check (Step 6.14) and test fidelity

**Category:** pass-with-caveat

`lint_help_presence: OK — 122 script(s) checked, 0 offenders`; the one new CLI (`ship_policy_bug_filer.py`) uses argparse with a described usage. No new hardcoded operational value (the refresh cadence, grace, ceilings all come from config). No duplicated lib implementation (the filer reuses `pgai_agent_kanban.lib.bug_filing`; the atomic helper is one file used by every writer). Test fidelity: the new tests exercise the real production function bodies (awk/marker extraction of the shipped shell, real `claude.sh` entry point for the stop file, real `cm-release.sh` for ship policy, a real-clock watchdog case) rather than sanitized proxies. Caveat (not a finding): `wake_driver_finalize.sh` and `wake_task_state.sh` sit at exactly 800 lines and `wake_driver_invoke.sh` at 778, so the next edit to any of them trips the engine-size lint again (CODER-019 flagged this; a durable decomposition is a follow-up candidate, not a defect).


## Suites (Steps 5a, 6.6, 6.7)

Run from the worktree root with `PGAI_DEV_TREE_PATH` pinned to it, sequentially, output teed under `$PGAI_AGENT_KANBAN_TEMP_DIR/tester/` (copies in this task's `artifacts/`). Exit files were verified to post-date the launch — the shared tester scratch directory held same-named exit files from a run on 2026-09-22, which were ignored.

- `bash team/scripts/run-unit-tests.sh --coverage` -> **UNIT_EXIT=0** — lint pre-flight all OK (dockerfile static, container-engine-free, api-parity, anti-patterns, skip-bug gate, orphan tests, operator-verb manifest, ICD freshness/compat, changelog freshness, doc-command truth, engine-size, env bootstrap, help presence, import completeness, dashboard gates, halt-scope writers, raw-marker read, status-writer bypass, dual-channel pair, runnable remedy, census compliance); pytest `5612 passed, 43 skipped in 1288.85s`.
- `bash team/scripts/run-integration-tests.sh --coverage` -> **INTEGRATION_EXIT=0** — pytest `636 passed, 1 skipped in 1048.70s`.
- Every test file this RC added or modified is green inside those runs: unit — test_cm_ship_policy_bash 33, test_cm_state_fields 37, test_config_loader_key_validation 20, test_ini_parser_bash 31, test_ship_policy_bug_filer 10, test_wake_task_state_atomic_writes 10, test_wake_task_timeout_budget 28, test_wake_task_state_transition 18, test_wake_watchdog_process_group_kill 10, test_lock_live_holder_never_reaped_by_age 16, test_wake_telemetry_safeguard_retry 22, test_zero_progress_cycle_cap 7, test_transient_retry_record 4, test_reconcile_stale_active_terminal_marker 10, test_coder_completion_merge_gate_bash 12, test_coder_completion_empty_commit_gate_bash 3, test_wake_composer_governance_manifest 14, test_composer_manifest_isolation 3; integration — test_ship_policy_acceptance 10, test_wake_stop_file_stops_batch 2, test_single_truth_state_integrity 5, test_cm_release_synthetic 17. 0 failed, 0 errors, 0 skipped among them. The 43 + 1 skips are pre-existing tree-wide skips outside RC-touched files.
- Neither findings file carries an `engine_size_dupe` entry.

All tests passed.

## Coverage (Step 5b, informational only)

From the `totals` object of the runner-generated JSON (field names verified against the real files: `percent_statements_covered`, `percent_branches_covered`, `percent_covered` = blended):

- unit: line 77.6 %, branch 63.7 % (blended 74.5 %) — `/tmp/pgai_kanban_tmp/coverage/unit.json`, written 16:01:58Z by this run.
- integration: line 13.3 %, branch 10.3 % (blended 12.6 %) — `/tmp/pgai_kanban_tmp/coverage/integration.json`, written 16:20:04Z by this run.

Coverage does not enter the verdict.

## Lint Findings (non-blocking)

Both `findings-unit.json` (generated 16:02:06Z) and `findings-integration.json` (16:20:08Z) carry the same two quality-class findings, one lint class (`hygiene`, class `provenance`):

- `hygiene` / `provenance` — `release-notes/v2.26.0.md:33` cites the internal requirements-document path `requirements/v2.26.0-agent-metrics-v2.md` — cites BUG-0455-release-notes-cite-requirements-path (open; filed by the v2.26.1 verification for exactly this instance).
- `hygiene` / `provenance` — `release-notes/v2.25.2.md:44` cites the task-ID token `tester-012` — cites BUG-0430-hygiene-lint-provenance (running; pre-existing, already in a bundle).

Both instances predate this RC (neither file is in the diff). One class, both already filed: nothing new filed; no effect on the recommendation or systemic risk.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=22d freshness=stale — the last cold-build run failed to build docker/claude-abliterated and podman/rocky9 at v2.9.20 (that run's bug, BUG-0167, is already `done`), and the record is stale on both counts (22d exceeds the 7-day window and v2.9.20 is not the v2.26.2 under verification), so nothing is known about whether this tree's three Dockerfiles build cold today.

## Autonomous Operation Criterion (Step 6.12)

1. Force-promoted WAITING -> BACKLOG by hand? **NO** — `logs/cron-coder.log`, `cron-writer.log`, `cron-tester.log` record every promotion for CODER-013/014/015/016/017/019, WRITER-018 and TESTER-021 as `wake(<agent>): WAITING task ...: prerequisites satisfied, promoting to BACKLOG`.
2. `release-state.md` edited outside the CM scripts? **NO** — mtime 09:57 equals `RC Opened At` written by `cm/open-rc.sh` (CM-020); `release-state/v2.26.2.json` written by the same run.
3. Scripts patched mid-build outside task work? **NO** — every commit on `ai_main..ai_rc/v2.26.2` is a task's own feature commit or its `--no-ff` merge (eight merge commits, one per task), all by the same agent author between 10:08Z and 15:34Z.
4. Queue files edited by hand? **NO** — the nine v2.26.2 entries read `[x]` matching `## State: DONE` in each folder; this task reads `[A]` matching WORKING; markers are consistent with states.
5. Non-CM remote git operation? **NO** — no `logs/git-boundary-guard.jsonl` exists in any v2.26.2 task folder (CM-020, PM-002, CODER-012..017, 019, WRITER-018, TESTER-021). This session ran no remote verb and never changed `PGAI_AGENT_ROLE`.

Autonomous operation criterion: Manual interventions required: none.

## Caveats

- **Operator note for upgrade (outside the dev tree, not a finding):** the live install's `kanban.cfg` still carries `agent_lock_timeout_seconds = 3600` under `[chain]` (line 73; the key was dead and this release drops it from the registry), so after v2.26.2 is installed every wake will print one `config_loader: load_config: WARNING: [chain] agent_lock_timeout_seconds ... is not a known kanban.cfg key` until the line is removed. Its first line still reads `# kanban.cfg_example` (a comment; harmless). Every live `[wake]` integer value is decimal digits, so the new refusal will NOT stop a wake.
- Three engine files sit at or near the 800-line ceiling after this RC (`wake_driver_finalize.sh` 800, `wake_task_state.sh` 800, `wake_driver_invoke.sh` 778); the next edit to any of them trips `lint_engine_size_dupe` (quality class) again. A durable decomposition is a follow-up candidate, not a defect.
- `docs/OPERATIONS.md:1043` cites a `team/templates/kanban.cfg.example` that does not exist, and `[chain] cron_stagger_minutes` is read by nothing (both observed by CODER-017, pre-existing, outside this bundle's spec; recorded here for a future sweep, not filed).
- CODER-015 left two scratch directories under `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/` (`CODER-20260929-015-scratch`, `scratch-stopfile-rerun`) because a session hook refused their removal; temp litter only.
- CODER-019 noted `git merge -F -` is not accepted by this host's git; the merge was redone with `-m`. No effect on the RC.
- Git workflow: the role file's "Git workflow" section (commit artifacts on a feature branch off the RC, merge `--no-ff`) conflicts with the wake prompt ("do NOT create a feature branch") and the task README (`Feature Branch: none`, `Git Repo: none`); the prompt and README were followed, the worktree was left untouched at 5d1bdec with a clean tree, and artifacts live only in this task folder. Recorded in `status.md ## Instruction Conflicts`.
- `deep-verify: ` line above reflects the ledger only; TESTER ran no container build.
- Recommendation rationale: `SHIP-WITH-CONCERNS` rather than `PASS` solely because two low-severity Step-7 findings were filed (Findings 1 and 2); neither is a regression of this RC, neither affects runtime behavior, and both heal in the next iteration. Both runners are green, so CM's matrix reads DONE / low / SHIP-WITH-CONCERNS -> `KNOWN-BUGS` with BUG-0457 and BUG-0458 listed.
