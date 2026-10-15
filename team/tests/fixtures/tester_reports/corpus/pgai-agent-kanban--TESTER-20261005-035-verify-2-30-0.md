# Verification Report: rc/v2.30.0

## Summary
Verified release candidate `ai_rc/v2.30.0` (HEAD 83fcb6c1, 59 commits, 198 files, +36,537/-12,803 over `ai_main`) against `requirements/v2.30.0-shared-modules-phase-2.md`, in the isolated detached worktree `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20261005-035-verify-2-30-0`. Pre-flight was clean: HEAD equals the RC, no dirty files, no stashes, and all 15 input tasks are DONE with one `--no-ff` merge each.

The functional work largely does what the requirements ask. Independent behavioral probes on sandbox roots confirmed:
- remove-project / unwind-rc / cancel-rc / ship-rc REST guards, each failure mode triggered;
- `/board` vs `column-render.sh` agreement, WONT-DO included;
- `/costs` byte-equal to `cost-report.sh`, and to the ai_main script;
- the read routes byte-equal to their CLIs;
- the lock live-holder refusal;
- file-mode-preserving writes.

But the gated **unit runner exits 1 and runs zero of its 9,591 parallel tests**. A new test puts the process PID in its parametrize IDs, so pytest-xdist aborts at collection on every run. That abort hid 8 more RC-introduced unit failures, which a diagnostic replay surfaced:
- a stale test setup;
- the ICD contract changed under a frozen published version;
- new modules that crash when run by path (a recurrence of the BUG-0533 class).

The gated integration runner passed (855 passed, 1 skipped). Both Convergence Criterion targets are unmet, as PM and CODER-033 had pre-recorded. Two pre-existing defects in delivered scope were also confirmed and filed.

## Recommendation
SHIP-WITH-SERIOUS-CONCERNS

## Systemic Risk
high

## Findings

### Finding 1: Gated unit runner aborts at collection; no parallel unit test runs

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks; test-only change)
**Systemic Risk:** high (the one authoritative unit signal is dead on every run, and it masked 8 other RC regressions across four components; no full certification had run on this RC before TESTER)
**Regression:** yes
**Integrity:** breakage
**Filed As:** BUG-0558 (Path C-A)
**Filed By:** TESTER (autonomous Path C) — a new lock test puts the test process's own PID into its test names, so every parallel worker sees different test names and the gated unit runner refuses to run any of its 9,591 parallel tests.

`team/scripts/run-unit-tests.sh --coverage` -> **UNIT_EXIT=1**. The parallel lane fails at collection with `Different tests were collected between gw0 and gw1` (and gw2). Cause: `tests/unit/test_ops_locks.py:632-635` parametrizes `test_pid_liveness_verdict` with `str(os.getpid())` and `f"{os.getpid()}\r"`, introduced by commit 10908304 (CODER-20261005-023-ops-locks). Reproduced minimally with `pytest tests/unit/test_ops_locks.py -n 2`. The file passes 82/82 when run alone without xdist. The serial lane ran 5 tests (passed). The v2.30.0 green ledger held no certification run before this one: CODER tickets used `--files` runs only.

Severity is critical under SOP-core "Rating a Finding" (verification cannot run at all, for the unit lane). The fix is a few lines of test code (keep the PID out of the parametrize IDs), so a stop under ship policy should be short. That argument belongs here in prose. The rating itself stays unsoftened.

### Finding 2: Read-route probe test still assumes /test-report and /rejected start a script

**Category:** stale-assertion
**Fix Effort:** small (1 CODER task; test-only)
**Systemic Risk:** low (isolated to one test file)
**Filed As:** BUG-0559 (Path C-A)
**Filed By:** TESTER (autonomous Path C) — a read-route test still expects the test-report and rejected routes to start a script, but this release made them run in process, so the test now points them at the wrong folder and fails; the test, not the product, needs updating.

Found by the diagnostic replay (debugging aid; not PASS evidence). Four tests fail: `TestReadRouteProbe::test_unknown_query_param_produces_named_warning[test-report|rejected]` and `::test_clean_call_returns_empty_warnings[test-report|rejected]`.

Step 7 evidence:
- **Test lines:** `team/pgai_agent_kanban/api/tests/test_bug0041_acceptance.py:905` and `:954`. Specs at `:754` and `:772` declare `uses_subprocess=True`; `_IN_PROCESS_READ_ROUTES` (`:720`) lists only `/show`, so `_make_client_for_route` roots the API at `team/`.
- **Expected vs actual:** expected HTTP 200; actual HTTP 500. The bodies are `show-test-report.sh: tasks directory not found: <tree>/team/projects/acceptance-test-proj/tasks` and `projects/ directory not found under <tree>/team`.
- **Production change:** `team/pgai_agent_kanban/api/reads.py:489-490` and `:946-947` now call `ops_read.render_test_report` / `ops_read.render_rejected` on `request.app.state.api_cfg.kanban_root`. Commit 5b9a6052, task CODER-20261005-031-ops-read-routes. That is Deliverable 7, so the new behaviour is required.
- **Production confirmed correct:** on a sandbox copy of the real project, REST stdout is byte-identical to the CLI.
- **Baseline:** the four tests pass on ai_main.

The **test** must be updated next iteration, not the production code.

### Finding 3: API contract document changed under its frozen published version 1.14.0

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (11 commits from many port tickets regenerated the ICD with no version step; the same omission will recur in any RC that touches routes)
**Filed As:** BUG-0560 (Path C-A)
**Filed By:** TESTER (autonomous Path C) — this release rewrote the published API contract document but kept its version number at 1.14.0, so the contract now differs from the frozen 1.14.0 copy that clients were told to rely on, and two unit tests fail.

`docs/api/icd.json` is 210,721 bytes and declares `1.14.0`. The frozen `docs/api/baselines/icd-v1.14.0.json`, published with v2.28.3, is 204,043 bytes. On ai_main the two are byte-identical. Failing tests (diagnostic replay):
- `test_review_cmds.py::TestIcdLifecycle::test_icd_v1_14_0_baseline_byte_identical_to_icd_json`
- `test_lint_icd_compat.py::TestIcdCompatPositive::test_baseline_byte_equality`

Both pass on ai_main. This is not a stale assertion: the right fix is an ICD version bump plus a new baseline. Overwriting the published baseline or editing the test would be wrong.

### Finding 4: New package modules crash when run by file path (BUG-0533 class recurs)

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (regression of a prior gap: BUG-0533 fixed this class in v2.28.x and it returned with three new modules)
**Filed As:** BUG-0561 (Path C-A)
**Filed By:** TESTER (autonomous Path C) — three Python files added in this release crash with "module not found" when started directly by their file path, which the documents cite them by, so two unit tests and the document-truth check fail again in the same way a previous release already fixed.

The affected modules are `dashboard/column_render/__main__.py:25`, `dashboard/column_render/cells.py:37` and `ops/verdict.py:127`. Each imports `pgai_agent_kanban` with no self-location guard. Three unit tests fail (diagnostic replay):
- `test_package_modules_run_by_path.py` (2 cases)
- `test_lint_doc_command_truth.py::test_real_tree_default_exempt_list_closes_the_named_process_internal_citations`

All three pass on ai_main. The quality-class `doc_command_truth` lint findings (3) are the same root cause and are listed inside this bug. Production rendering is unaffected, because scripts use `pp_run_ops`.

### Finding 5: Convergence Criterion unmet — two non-launcher impl: shell rows remain

**Category:** gap
**Fix Effort:** medium (3-5 CODER tasks; deep-verify's port must stub the container engine)
**Systemic Risk:** low
**Filed As:** BUG-0562 (Path C-A)
**Filed By:** TESTER (autonomous Path C) — two operator verbs, deep-verify and verify-rc-state, still run as shell scripts behind their REST routes, so the release missed its goal that every non-launcher verb has one shared Python implementation.

`lint_api_route_impl.py`: `32 verb(s), 28 module row(s), 2 shell row(s), 2 launcher(s) (4 impl shell, baseline 4); 0 finding(s)`. The remaining rows are `deep-verify` and `verify-rc-state`. Launchers documented in `team/config/api-route-impl-launchers.txt` with reasons: `wake-now`, `cold-build-now`. `GET /verify-rc-state` still calls `subprocess.run`. PM-20261005-003 and CODER-20261005-033 pre-recorded this as next-cycle work, because no deliverable covered it.

### Finding 6: Convergence Criterion unmet — inline Python reduction short of 60 %

**Category:** gap
**Fix Effort:** large (architectural, likely spanning RCs)
**Systemic Risk:** low
**Filed As:** BUG-0563 (Path C-A)
**Filed By:** TESTER (autonomous Path C) — the release removed less than half the inline Python blocks it promised to retire, so the 60 percent reduction target in its requirements was not reached.

`lint_inline_python.py`: 105 blocks / 4,602 lines, and the allowlist sums to the same. Against the 131 / ~8,500 baseline that is -19.8 % blocks and -45.9 % lines; the target needs <= 52 and <= 3,400. Pre-recorded by PM-003 and CODER-033 as unreachable within the listed deliverables. The heredocs the deliverables named (unwind-rc, cost-report, column_render_*) are genuinely gone.

### Finding 7: Empty release-state field is read as the next field's value (pre-existing)

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated reader defect; predates this RC)
**Filed As:** BUG-0564 (Path C-A)
**Filed By:** TESTER (autonomous Path C) — when a release-state.md field is present but left empty, the shared reader silently returns the value of the next field instead of reporting the field as empty.

Probe on both RC and ai_main: `read_release_state_field(f, "RC Opened By Task")` returns `'v1.1.0'` (the Last Released value) for an empty field. An empty `## Active RC` is refused as `invalid-field` and names the neighbour's timestamp. The new bounded `read_state_section` correctly returns `''`. This traces to Deliverable 2 (the one reader). It contradicts SOP-core "Release State Fields": present-but-empty is a distinct fault. Reported for this lane by CODER-033 and reproduced here. Regression: no.

### Finding 8: GET /dashboard/{input,queue} never renders board items (pre-existing)

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (predates this RC; `GET /board` carries the same data)
**Filed As:** BUG-0565 (Path C-A)
**Filed By:** TESTER (autonomous Path C) — the REST dashboard panes for the board columns never show any items, because the route calls the column renderer without saying which column to draw.

The test board had 4 CODER items listed by `/board`. On it, `/dashboard/queue` and `/dashboard/input` return `(empty)` with exit 0 on the RC; on ai_main they exit 1 with a JSONDecodeError. The route runs `column-render.sh <pane> none --all-projects` with no `--column-type`. Deliverable 5 asked `/dashboard/{pane}` to use the shared classifier; it reaches it only through this subprocess and renders nothing. Reported for this lane by CODER-028 and reproduced here. Regression: no.

## Acceptance Criteria and Deliverable Results

These are verification results, not filed findings. Headings deliberately do not begin with "Finding".

### AC1 — every ported verb: route calls the module with no subprocess; CLI reaches it via pp_run_ops — pass-with-caveat
14 manifest rows flipped shell -> module. Each one has a test file that calls `assert_route_cli_parity` / `assert_route_reaches_function` / `assert_cli_reaches_function` for that verb, and `lint_api_route_impl.py` shows 28 module rows with 0 findings. Read routes (`/status`, `/test-report`, `/readme`, `/rejected`, `/verdicts`, `/metrics`, `/costs`) call `ops.read` / `ops.verdict` / `lib.metrics` in process. Caveat: the unit-lane tests carrying this assertion were not executed by the gated runner (Finding 1). In the diagnostic replay they pass, apart from Findings 2-4.

### AC2 — heredoc grep totals 0 — pass-with-caveat
`grep -cE 'python3?\b[^#]*<<' unwind-rc.sh cost-report.sh column_render_*.sh` -> 0. Caveat: on ai_main the grep already read 0 for cost-report.sh and column_render_*.sh, because the openers sit on continuation lines. I therefore checked the substance too: heredoc openers fell from 10/1/2/1/1 to 0, and the files shrank, for example unwind-rc.sh from 1,534 to 182 lines.

### AC3 — remove-project / unwind-rc REST guards — pass
13/13 independent probes pass; each refusal case left the sandbox byte-identical.
- remove-project without force unregisters only.
- An Active RC refuses with or without force.
- unwind-rc refuses a missing, false or wrong confirm (422), and refuses force without confirm.
- An Active-RC mismatch is refused unless force=true.
- force never bypasses the HALT requirement.

### AC4 — /board and column-render.sh agree, WONT-DO included — pass
My own board (WRITER queue, one task per state, plus PRIORITIES intake) gave identical item sets in single-project mode, `--all-projects` queue mode and input mode, with each row coloured by its `/board` status. WONT-DO is `wont-do` on both surfaces. The decision is documented in `board/classifier.py` and `docs/dashboard.md`.

### AC5 — /costs text equals cost-report.sh on a fixture usage tree — pass
Byte-identical across 6 scopes (3 RC, 1 month, 2 day) on a sandbox copy of the real usage tree plus 80 real `tokens.json` files. The RC script is also byte-identical to the ai_main script, in text and CSV.

### AC6 — targeted tests pass with --files; TESTER certifies the full suites — fail
The full unit certification failed (Findings 1-4). The integration certification passed.

### Deliverables 1-7 — pass (behavior), with the gaps above
- **D1:** lock live-holder refused on CLI and REST; dead holder released; modes preserved by set-version-ceiling and switch-provider.
- **D2:** no bypassing readers remain (see Finding 7 for the reader itself).
- **D3:** confirm and force are separate on cancel-rc, unwind-rc and ship-rc.
- **D4:** `lib/verdict.sh` and `_parse_verdict_report_blocks` are removed.
- **D5:** the classifier moved and WONT-DO was decided (see Finding 8).
- **D6:** `metrics_data.py` and `render_costs.py` moved into `lib/metrics`.
- **D7:** REST output equals CLI output for test-report, readme and rejected; status differs only in the live elapsed-seconds counter.

### Gated runner exit codes
- `UNIT_EXIT=1` (lint pre-flight passed with quality findings; pytest parallel lane collection error).
- `INTEGRATION_EXIT=0` (855 passed, 1 skipped).
- Coverage: unavailable. `kanban.cfg [testing] coverage` disabled collection ("coverage disabled by config"). The JSON at `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/` is dated 2026-10-04 and stale, so it is not reported.

### Other methodology steps
- **6.1:** release-state well-formed; v2.30.0 > v2.29.2 via `semver_gt`. Pass.
- **6.8:** synthetic release test (16 cases) passed inside the integration run. Pass.
- **6.10:** prerequisite graph is acyclic with correct bookends. Pass.
- **6.13:** no placeholder skips. Pass.
- **6.14:** observation only (module CLIs reject `--help`; operators use wrapper scripts that answer it).
- **Documentation discipline:** no new environment variables or config keys. Pass.

## Lint Findings (non-blocking)

- `doc_command_truth` (3 instances; unit findings file) — `column_render/cells.py` and `ops/verdict.py` fail `--help` by path when cited in `docs/OPERATIONS.md:1453`, `docs/operator-commands.md:584` and `docs/operator-api.md:858` — filed BUG-0561 (Path C-A). Same root cause as Finding 4, where the related unit test also fails.
- `python_duplicates` (2 instances, one duplicated pair; unit and integration findings files) — `lib/ini.py:243 _split_lines` and `ops/verdict.py:390 _awk_records` share a normalised body — filed BUG-0566 (Path C-A).

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=28d freshness=stale — The only ledger record is a red run from v2.9.20, 28 days old: past the 7-day window and not this RC's version, so nothing is known about whether this tree's images build. That run's failure was filed as BUG-0167 (claude-abliterated FROM parse error) and is cited here, not re-filed.

## Autonomous criterion

1. Tasks force-promoted WAITING -> BACKLOG by hand: **NO**. Every RC task's promotion note reads "by wake script".
2. release-state.md edited outside the CM scripts: **NO**. Its mtime (11:37:10Z) equals `RC Opened At`, written by CM-20261005-034.
3. Scripts patched mid-build: **NO**. The RC's first-parent line has no direct non-merge commits. The live install is unchanged since RC open apart from `.pyc` caches.
4. Queue files edited by hand: **NO**. Every RC task's marker matches its `status.md` state.
5. A non-CM agent invoked fetch/pull/push: **NO**. There is no `git-boundary-guard.jsonl` in any RC task folder.

Autonomous operation criterion: Manual interventions required: none.

Process observation (not a manual intervention): no full-suite certification ran during the RC. Every CODER ticket ran targeted `--files` runs, so Finding 1 went unseen from CODER-023's merge until TESTER. Finding 1 records this.

## Caveats

- **Working-tree instruction conflict:** the wake prompt required a detached worktree and forbade a feature branch, while roles/TESTER.md "Git workflow" says to commit artifacts on a feature branch. I followed the wake prompt; artifacts live only in the task folder.
- **Diagnostic replay (not PASS evidence):** the unit parallel lane was replayed by direct pytest with `tests/unit/test_ops_locks.py` excluded. Result: 9 failed, 9,459 passed, 41 skipped; the excluded file alone passed 82/82. The 9 failures (Findings 2-4) all pass on an ai_main extract (139 passed, 2 skipped across the same test selections). This replay only located failures. The verdict input stays UNIT_EXIT=1.
- **Observation, unfiled (outside v2.30.0 scope; Step 6.11 demotes it):** `aggregate_tokens.py`'s daily roll-up writes 0 for every token count in `totals` and `by_agent` while `by_model` has real counts. Every day/month cost report therefore prints "Token totals: 0 tokens". The behaviour is identical on ai_main, and the file is unchanged since v1.0.0.
- **Observation, unfiled (pre-existing, guard still holds):** `POST /operations/ship-rc` with a boolean `confirm` is refused by FastAPI's validation list rather than the dual-channel envelope (the `ShipRcBody.confirm` type is unchanged from ai_main).
- **No prompt-injection content** was encountered in any file read.
- Probe scripts and logs: `/tmp/pgai_kanban_tmp/tester/035/` (`probe/`, `unit.log`, `integration.log`, `diag-unit-*.log`, findings JSON copies).

## Appendix — Verification checkpoint log (written during the run)

### Progress Log (checkpoint)

- Step 1: Fresh start (artifacts empty; status WORKING was set by wake driver at dispatch, no prior report).
- Step 2 pre-flight: 609 DONE tasks with feature branches scanned; 0 exist locally (normal post-merge shape) -> both checks pass trivially. All 15 input tasks are DONE and each has exactly one `--no-ff` merge commit on `ai_main..ai_rc/v2.30.0`.
- Step 3: worktree HEAD 83fcb6c1 == `git rev-parse ai_rc/v2.30.0`; `git status --porcelain` empty; `git stash list` empty.
- Step 4: diff `ai_main..ai_rc/v2.30.0`: 59 commits, 198 files changed, +36537/-12803.
- Step 5a: gated runners launched (unit then integration, `--coverage`), output under /tmp/pgai_kanban_tmp/tester/035/.
- AC2 (heredoc grep): ran `grep -cE 'python3?\b[^#]*<<' unwind-rc.sh cost-report.sh column_render_*.sh` -> total 0. Substance check: heredoc openers (`<<'PY'`, `<<'PY_EOF'`) fell from 10/1/2/1/1 (unwind-rc/cost-report/cells/data_multi/frame on ai_main) to 0 each; files shrank 1534->182, 1280->105, 758->74, 736->194, 457->48 lines. PASS-with-caveat: the literal grep already returned 0 on ai_main for cost-report.sh and column_render_*.sh (openers sit on continuation lines), so it is a weak oracle; substance verified separately.
- Convergence (route-impl): `lint_api_route_impl.py` -> "32 verb(s), 28 module row(s), 2 shell row(s), 2 launcher(s) (4 impl shell, baseline 4)". Non-launcher shell rows remaining: `deep-verify`, `verify-rc-state`. Criterion requires zero except wake-now and documented launchers -> NOT MET (gap).
- Convergence (inline python): `lint_inline_python.py` -> 105 blocks / 4,602 lines; allowlist sums to 105/4602. Versus baseline 131/~8,500: -19.8% blocks, -45.9% lines; 60% target needs <=52 blocks and <=3,400 lines -> NOT MET (gap). PM-003 and CODER-033 both pre-recorded this as unreachable within the listed deliverables.
- AC3 (behavioral, independent probe `/tmp/pgai_kanban_tmp/tester/035/probe/ac3_probe.py`, sandbox root + throwaway git repo, in-process TestClient): 13/13 PASS. remove-project: no-force unregisters only (dir kept); Active RC refuses with and without force (`remove_project.active_rc`, tree byte-identical); force without confirm 422; force+confirm with no RC deletes. unwind-rc: missing/false/wrong-string confirm and force-without-confirm all 422 with tree unchanged and RC branch intact; confirm=true + mismatched Active RC refused (exit 1) with nothing changed; confirm+force bypasses the mismatch and unwinds; matching Active RC unwinds and sets Active RC none; force does not bypass the HALT requirement.
- AC4 (behavioral, independent probe `ac4_probe.py`, own board: WRITER queue with one task per state + PRIORITIES intake with open/wont-do/done/running): `/board` reports WONT-DO as `wont-do`; `column-render.sh` single-project queue mode, `--all-projects` queue mode and `--all-projects` input (priorities) mode each list exactly the `/board` items, every row in the SGR colour of its `/board` status (done and wont-do both green 0;32). PASS. Deliverable 5's "decide one, document it" is met: `board/classifier.py` module docstring and `docs/dashboard.md` "Board status and WONT-DO" both state `wont-do` on both surfaces.
- AC5 (behavioral, independent probes `ac5_probe.sh`/`ac5b_probe.sh`; sandbox copy of the real project's `usage/` tree plus 80 real task `tokens.json` files from 2026-10-04/05): `GET /costs` stdout byte-identical to RC `cost-report.sh` for `--rc v2.29.2`, `--rc v2.10.0`, `--rc v9.9.9` (no data), `--month 2026-10`, `--day 2026-10-05`, `--day 2026-10-04`; exit codes 0/0. Preservation: RC `cost-report.sh` byte-identical to ai_main's `cost-report.sh` (extracted with `git archive`) on every scope, and `--csv` month output identical. PASS.
- Step 5a UNIT: `run-unit-tests.sh --coverage` -> UNIT_EXIT=1 (20:44:31 -> 20:46:15). Cause: parallel lane (9,591 items) aborted at xdist collection, "Different tests were collected between gw0 and gw1/gw2" — `tests/unit/test_ops_locks.py::test_pid_liveness_verdict` parametrizes on `os.getpid()` (lines 633-634), so each worker's test IDs differ. Zero parallel-lane unit tests executed; serial lane 5 passed. Introduced by commit 10908304 (CODER-20261005-023-ops-locks) on this RC. Green ledger `v2.30.0/ledger.jsonl` holds only this run: no full-suite certification had run on the RC before TESTER. -> BUG (regression, suite breakage).
- AC1 coverage map: 14 manifest rows flipped shell->module on this RC (add-project, agent-metrics, cancel-rc, create-project, defer, rc-metrics, release-locks, remove-project, set-version-ceiling, ship-rc, switch-provider, undefer, unwind-rc, why); each has a test file calling `assert_route_cli_parity`/`assert_route_reaches_function`/`assert_cli_reaches_function` naming it (test_ops_projects, test_lib_metrics, test_cm_cancel_rc, test_ops_defer_undefer, test_ops_locks, test_ops_version_ceiling_switch_provider, test_cm_ship_rc, test_cm_unwind_rc, test_ops_verdict). `lint_api_route_impl.py`: 28 module rows, 0 findings. Whether those tests pass depends on the suite run (see above).
- /dashboard/{input,queue}: probe `dash2_probe.py` — on a board where `/board` lists 4 CODER items, `GET /dashboard/queue` and `/dashboard/input` return `(empty)` exit 0 on the RC; on ai_main the same requests exit 1 (JSONDecodeError). Route passes no `--column-type` to `column-render.sh --all-projects`. Pre-existing (reported by CODER-028 for this lane, not filed). Deliverable 5 asked `/dashboard/{pane}` to use the shared classifier; it reaches it only via subprocess and renders nothing.
- Deliverable 1 behavioral (probe `d1_probe.sh`): release-locks against a lock whose recorded pid is a live process holding the flock -> CLI rc=4 and REST 500/exit 4, both `release_locks.holder_alive`, lock file byte-identical, holder alive; dead-pid lock released (file emptied) rc=0; GET /release-locks listing equals CLI listing content. set-version-ceiling (CLI and REST) and switch-provider (CLI and REST) preserve file modes 640/620/600/604 across atomic replace. PASS.
- Step 6.1: release-state.md Active RC v2.30.0, Last Released v2.29.2 (well-formed); `pp_last_released_version` = v2.29.2; `semver_gt v2.30.0 v2.29.2` true. PASS.
- Step 5d: deep-verify ledger reader returned found=true, verdict failed-to-build, v2.9.20, 2026-09-07 (age 28d) -> stale on age and version. Red record cited (BUG-0167), not re-filed.
- Step 6.10: graph acyclic; CM-034 open -> CODER-019 scaffold -> ports -> CODER-033 convergence -> TESTER-035 -> CM-037; all 18 IDs exist; sequence gap 036 matches prior RCs (011, 017). PASS.
- Step 6.12: (1) no non-wake promotions; (2) release-state.md mtime 11:37:10 == RC Opened At (CM-034); (3) no direct non-merge commits on the RC first-parent line, live install unchanged since RC open except .pyc caches; (4) queue markers consistent with every task state; (5) no git-boundary-guard.jsonl in any RC task folder. All NO.
- Step 6.13: no placeholder skip shapes in RC tests (hits only in archived logs / lint self-test fixtures); changed tests add no bug-deferral skips. PASS.
- Step 5a INTEGRATION: `run-integration-tests.sh --coverage` -> INTEGRATION_EXIT=0; parallel lane 855 passed, 1 skipped (496.6s); serial lane none selected (exit 5, treated as pass by the runner); sentinel/listener/bare-tmp checks OK.
- Deliverable 2: grep of release-state headings across team/scripts, team/pgai_agent_kanban, team/pm-agent (excluding tests, read_state_field.py, write_rc_state.py) finds only docstrings/comments/help text; remaining bash consumers call `release_state_section`/`get_release_state_field`. `lib/verdict.sh`, `api/metrics_data.py`, `dashboard/render_costs.py` removed; `_parse_verdict_report_blocks` gone. PASS.
- Deliverables 4/6/7 structural: why.sh, verdict-report.sh -> `pp_run_ops pgai_agent_kanban.ops resolve_verdicts`; GET /verdicts -> `ops.verdict`; metrics-report.sh, cost-report.sh, show-metrics.sh, dashboard/costs.sh reach `lib.metrics` with no heredocs; kanban-status.sh/show-test-report.sh/show-readme.sh/list-rejected.sh -> `pp_run_ops pgai_agent_kanban.ops get_status|get_test_report|get_task_readme|list_rejected`; reads.py routes call `ops.read.*`. PASS.
- Deliverable 7 behavioral (probe `d7_probe.sh`, sandbox copy of the real project's tasks/bugs/priority/requirements/release-state): REST stdout == CLI stdout for /test-report (153 lines), /projects/{p}/tasks/{k}/readme (72 lines), readme 404/exit-1 case, /rejected; /status differs from kanban-status.sh only in the live elapsed-seconds counter. RC CLI == ai_main CLI for all except the kanban-status header version, which the RC resolves (`v2.29.2` via repo VERSION) where the ai_main extract (no VERSION file in a `git archive` of team/) printed `unknown` — sandbox artifact, not a finding. PASS.
- read_state_field empty-field (CODER-033 hand-off, probe): a present-but-empty `## RC Opened By Task` is returned as the NEXT field's value (`'v1.1.0'` from Last Released) by `read_release_state_field` on both RC and ai_main; an empty `## Active RC` is refused as `invalid-field` naming the neighbour's value rather than as an empty value. The new bounded `read_state_section` returns `''` correctly. Pre-existing -> BUG (filed below).
- Deliverable 3 behavioral (probe `d3_probe.py` + AC3 probe): cancel-rc REST without confirmation -> 422 `cancel_rc.confirmation_required`, and with only `force: true` -> the same 422 (force never confirms); ship-rc without confirm -> 422 `ship_rc.confirm_required`, wrong phrase -> 422 `ship_rc.confirm_mismatch`; tree byte-identical in every case. Integration suite (test_cm_cancel_rc.py, test_cm_ship_rc.py, test_cm_unwind_rc.py with golden CLI fixtures) passed inside INTEGRATION_EXIT=0. PASS.
- Documentation discipline: no environment variable or config key is newly introduced by the RC diff; kanban.cfg_example change is comment-only (points at ops/verdict.py). PASS.
- Step 6.14: new provenance-shaped IDs in the diff are synthetic test-fixture values only; hygiene lint 0 findings. Module CLIs `python3 -m pgai_agent_kanban.{cm,ops,lib.metrics}` reject `--help` as an unknown verb (operators reach them through scripts that do answer `--help`; help-presence lint 0 offenders) -> observation only.
- Step 6.8: diff touches release-lifecycle files (cm/cancel-rc.sh, cm/finalize.sh, cm/open-doc.sh, cm/release.sh, lib/project_paths.sh) -> `tests/integration/test_cm_release_synthetic.py` (16 tests, none skipped/quarantined) ran inside the gated integration run, which reported 855 passed / 0 failed. PASS.
