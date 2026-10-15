# Verification Report: rc/v2.31.6

Branch under test: `ai_rc/v2.31.6` at `8f87fb5ce82108346f9d4c62f003540a86cbcc26` (detached-HEAD worktree `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20261007-020-verify-2-31-6`).
Baseline: `ai_main` at `aa629f15` (tag `ai_v2.31.5`).
Requirements: `requirements/v2.31.6-bugfix-bundle-20261007.md` (bundles BUG-0595, BUG-0596).
Verifier: TESTER-20261007-020-verify-2-31-6 (Agent: tester, Model: claude-opus-5-5). Fresh run; no prior report existed in `artifacts/`.

## Summary
RC v2.31.6 bundles two medium bugs, and both fixes are correct. BUG-0595 (the doc-lint command reader crashed when started by file path) is fixed by a package self-location guard. It now runs by path from any directory with PYTHONPATH unset (exit 0; the ai_main baseline still exits 1), and it binds its own tree even when a competing package is on PYTHONPATH. BUG-0596 (wake-lock integration tests were coupled to the wake's exported `[wake]` tunables) is fixed in two places: both gated runners unset all 15 `[wake]` exports before pytest, and the three affected test fixtures scrub them before layering their own environment. The loader's env-over-cfg operator override is unchanged. I ran both gated runners with `--coverage` from inside this agent wake, with `LOCK_WAIT_SECONDS=120` / `LOCK_STARVATION_FIRINGS=3` inherited, which is the exact shape that failed in v2.31.5. Both exited 0: unit 10159+5 passed, 41 skipped; integration 948 passed, 1 skipped. Lint findings were 0 for both suites. Every acceptance command in both bug files passes. The original failure modes were reproduced on ai_main and shown absent on the RC. Pre-flight, the prerequisite graph and the autonomous-operation checks are clean. No bugs, gaps or stale assertions were found.

## Recommendation
PASS

## Systemic Risk
low

## Findings

### Pre-flight (Steps 2-3)
- Step 2 RC integrity: 649 DONE task folders name a feature branch; none exists locally (all deleted after merge), so Checks 1 and 2 pass trivially. The two cycle CODER merges are on the RC: `8003e9a5` (CODER-017, feature commit `cb487c02`) and `8f87fb5c` (CODER-018, feature commit `8f90232a`). Anomalies: 0. — pass
- Step 3 worktree: `git rev-parse HEAD` == `git rev-parse ai_rc/v2.31.6` == `8f87fb5c`; `git status --porcelain` empty; `git stash list` empty. — pass

### Diff (Step 4)
`git diff ai_main..ai_rc/v2.31.6`: 10 files, +579/-3. Production: `team/pgai_agent_kanban/lint/doc_commands.py`, `team/scripts/lib/config_loader.sh`, `team/scripts/run-unit-tests.sh`, `team/scripts/run-integration-tests.sh`. Tests/fixtures: `team/tests/fixtures/scrubbed_subprocess_env.py`, three integration files, `test_config_loader_key_validation.py`, new `team/tests/unit/test_wake_env_names_scrub.py`. No file outside the two bundled bugs' scope.

### BUG-0595 — doc_commands.py by-path start-up
- `env -u PYTHONPATH python3 <RC>/team/pgai_agent_kanban/lint/doc_commands.py --help` from a scratch dir outside `team/`: exit 0. — pass
- Original failure mode (Principle: verify both): same command against an `ai_main` clone (v2.31.5): exit 1, `ModuleNotFoundError: No module named 'pgai_agent_kanban'`. Fix confirmed to change the outcome. — pass
- Fence repro doc (```text block then ```bash block with `echo real-command`) run by path: output byte-identical (`cmp`) to `1\tfalse\tfalse\tfalse\techo real-command\n`, exit 0. `-m` form gives the same output. — pass
- Binds its own tree: loaded via `runpy.run_path` with `PYTHONPATH` pointing at a competing `pgai_agent_kanban` (the live install, and separately the ai_main clone), `pgai_agent_kanban.lib.markdown_fence` resolved from the RC worktree. — pass
- `bash team/scripts/harness/doc-lint.sh --suite cmd`: `files=5 pass=1 skip=135 fail=0`, exit 0. — pass
- Constraint: module not added to `_PACKAGE_ONLY_MODULES`; `markdown_fence.py` and `doc-lint.sh` unchanged in the diff. — pass

### BUG-0596 — gated runs and sandboxed wakes inherit the wake's [wake] tunables (static and loader-level checks)
- Both runners source `team/scripts/lib/config_loader.sh` near the top (unit line 220, integration line 237), so `CONFIG_LOADER_WAKE_ENV_NAMES` (15 names) is populated when `unset "${CONFIG_LOADER_WAKE_ENV_NAMES[@]}"` executes (unit line 1454, integration line 1206). Neither runner calls `load_config` after the scrub, so nothing re-exports the names before pytest (unit pytest line 1559, integration line 1314). — pass
- Loader precedence unchanged (constraint "Do not change the loader's env-over-cfg precedence"): sandbox cfg `lock_wait_seconds = 0`, `max_tasks_per_wake = 1`; `LOCK_WAIT_SECONDS=45` set in env → `load_config` yields `LOCK_WAIT_SECONDS=45`. After `unset "${CONFIG_LOADER_WAKE_ENV_NAMES[@]}"` in the same inherited wake shell → `LOCK_WAIT_SECONDS=0 MAX_TASKS_PER_WAKE=1`. Before the scrub the inherited `MAX_TASKS_PER_WAKE=8` also beat the cfg's `1`, which supports CODER's choice to scrub all 15 [wake] exports rather than only the two lock keys. — pass
- Fixture side: `scrub_wake_tunable_env` is applied in `_wake_env` / `_base_env` / the `read_state_field` env of `test_single_truth_state_integrity.py`, `_wake_env` of `test_wake_contention_retry.py`, and `_clean_base_env` of `test_chain_lock_held_through_agent.py`, before `extra_env` is layered. — pass
- Pin tests exist as the bug's acceptance requires: `test_wake_contention_retry.py::test_inherited_lock_wait_seconds_does_not_override_the_sandbox_cfg` (parent env 120/3, sandbox 0, asserts the immediate deferral line and no "waiting up to") and `::test_lock_wait_seconds_set_for_the_wake_overrides_the_sandbox_cfg` (operator override through a real `scripts/wake/claude.sh` firing); loader-level `test_config_loader_key_validation.py::test_lock_wait_keys_environment_override_wins`; drift test `team/tests/unit/test_wake_env_names_scrub.py` (read in full: bash array, Python twin, and the loader's actual exports agree; both runners scrub before the replay banner and pytest; replay line carries `-u` for each). — pass

### Requirements-level acceptance criteria
- "All Python files compile cleanly": `compile()` over all 810 `git ls-files '*.py'` in the RC: 0 failures. — pass
- "All bash files pass syntax check": `bash -n` over all 332 `git ls-files '*.sh'`: 0 failures. — pass
- "Each bundled item is addressed": see BUG-0595 / BUG-0596 sections; final suite-level criteria recorded under Steps 5a/6.6-6.7.

### Step 6.1 — release-state.md
`## Active RC` v2.31.6, `## RC Opened At` 2026-10-07T12:42:51+00:00, `## RC Opened By Task` CM-20261007-019-open-rc-2-31-6, `## Last Released` v2.31.5 (well-formed). `pp_last_released_version pgai-agent-kanban` → v2.31.5; `semver_gt v2.31.6 v2.31.5` → true. — pass

### Step 6.8 — synthetic release simulation
Skipped: the diff touches no release-lifecycle file (`cm-release.sh`, `cm-open-rc.sh`, `cm-cancel-rc.sh`, `project_paths.sh`, or open/close-cycle scripts). `config_loader.sh` gained only a data array; the runner edits are test-harness only. The full integration suite (which includes `test_cm_release_synthetic.py`) still runs in Step 5a.

### Step 6.9 — constraint negative checks
No added line matches `api_key|token|secret|password =`. `set -euo pipefail` count unchanged in both runners; no removed `set -e`/`trap` lines in the diff. — pass

### Step 6.10 — prerequisite graph
PM-20261007-003 (DONE) → CM-20261007-019 open-rc (DONE) → CODER-20261007-017, CODER-20261007-018 (both DONE, prereq CM-019) → TESTER-20261007-020 (this task; prereqs 017, 018) → CM-20261007-022 release (WAITING; prereqs 017, 018, 020). Acyclic, every ID exists, bookends correct, no 20261007 sequence collision. — pass

### Step 6.13 — skip-cites-real-bug
No skip/xfail added in the diff. Tree-wide, every `BUG-NNNN` cited in a real `pytest.mark.skip`/`xfail`/`pytest.skip(` resolves under `bugs/`; the three non-resolving IDs (BUG-1234, BUG-1235, BUG-9872) are string literals inside the skip-gate's own fixture tests, not live annotations. `BUG-SKIP` hits are only archived lint-log text under `team/tests/artifacts/`. — pass

### Step 6.14 — coding standards spot-check
No added line cites a BUG/task/version identifier (provenance clean). No new script under `team/scripts/`. The 15-name list is a single bash canon plus a Python twin guarded by a drift test, not an unguarded duplicate. — pass

### Documentation discipline (SOP-core)
Script behaviour changed (runners now unset 15 names before pytest): both runners' header comments gained a "Wake tunables" paragraph naming every variable, and `config_loader.sh`'s usage block names the array. — pass-with-caveat: `docs/test-authoring-guidelines.md` ("What the printed line guarantees" / "What you still have to arrange") describes the replay family as "the one list of names" declared in `team/scripts/lib/gated_runner_lint.sh` and says a new name is added there; CODER-018 instead appends `CONFIG_LOADER_WAKE_ENV_NAMES` to `GATED_RUNNER_REPLAY_ENV_NAMES` inside each runner. The behaviour is identical and `test_both_runners_carry_identical_wake_scrub_statements` pins the two sites together, so nothing is broken; the guideline is now slightly incomplete about where the family is assembled. Observation only, not filed.

### Steps 5a / 6.6 / 6.7 — gated suites (authoritative)
I ran both from the worktree root with `--coverage`, sequentially, in the inherited agent-wake environment (`LOCK_WAIT_SECONDS=120`, `LOCK_STARVATION_FIRINGS=3`, `MAX_TASKS_PER_WAKE=8`, `MAX_TASK_SECONDS=21600`, `ZERO_PROGRESS_CAP=3` exported). Output is under `/tmp/pgai_kanban_tmp/tester/v2.31.6/`.
- `team/scripts/run-unit-tests.sh --coverage`: **UNIT_EXIT=0**. Lint pre-flight clean. Parallel lane `10159 passed, 41 skipped, 3 warnings in 1015.89s`; serial lane `5 passed`. Sentinel, listener and bare-/tmp checks OK. Green-ledger record at commit 8f87fb5ce821. — pass
- `team/scripts/run-integration-tests.sh --coverage`: **INTEGRATION_EXIT=0**. Lint pre-flight clean. `948 passed, 1 skipped in 720.33s`; serial lane empty. The five tests BUG-0596 named as failing in v2.31.5 are now inside the 948 passes (v2.31.5 was 941 passed + 5 failed; this RC adds 2 pin tests: 941+5+2 = 948). Green-ledger record at commit 8f87fb5ce821. — pass
- The real "Replay this exact run:" line the unit runner printed carries `env -u` for all 15 names (`MAX_TASKS_PER_WAKE` ... `LOCK_STARVATION_FIRINGS`), alongside the existing `GIT_GUARD_REAL_GIT` / `PGAI_GIT_GUARD_DEPTH`. — pass

### Coverage (Step 5b, informational)
coverage unavailable. Both runners printed `coverage disabled by config ([testing] coverage)`: the live `kanban.cfg` has no `[testing] coverage` key, so the documented default `false` applies and the `--coverage` flag did not enable it. `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/{unit,integration}.json` are from 2026-10-04 and do not describe this run, so I did not use them. Not a finding.

### BUG-0595 — bug-file acceptance, targeted runner
- `bash team/scripts/run-unit-tests.sh --files tests/unit/test_package_modules_run_by_path.py tests/unit/test_doc_commands.py tests/unit/test_bash_facing_python_clis.py`: exit 0, `173 passed`. — pass
- "`run-unit-tests.sh` (full certification run) exits 0": UNIT_EXIT=0 above. — pass

### BUG-0596 — bug-file acceptance and behavioural (Step 6.5) checks
- Acceptance 1: `LOCK_WAIT_SECONDS=120 LOCK_STARVATION_FIRINGS=3 bash team/scripts/run-integration-tests.sh --files tests/integration/test_single_truth_state_integrity.py tests/integration/test_wake_contention_retry.py tests/integration/test_chain_lock_held_through_agent.py`: exit 0, `12 passed in 120.91s` (the bug's "10 passed" plus the 2 new pins). — pass
- Acceptance 2 (a pin test exists and passes): `test_inherited_lock_wait_seconds_does_not_override_the_sandbox_cfg` passed in the targeted and full runs. — pass
- Acceptance 3 (operator override still works): `test_lock_wait_seconds_set_for_the_wake_overrides_the_sandbox_cfg` drives a real `scripts/wake/claude.sh` firing with `LOCK_WAIT_SECONDS=2` over a sandbox cfg of `0` and asserts the 2 s wait. It passed. `team/scripts/wake-batch.sh` ends in `exec "$WAKE_SCRIPT" "$@"` with no unset, and nothing outside the two runners and the test fixtures references `CONFIG_LOADER_WAKE_ENV_NAMES`, so the override reaches the provider script unchanged. Loader-level: `LOCK_WAIT_SECONDS=45` beats a cfg `0` (also pinned by `test_lock_wait_keys_environment_override_wins`). — pass
- Acceptance 4 (full certification run exits 0 from an agent wake): INTEGRATION_EXIT=0 above, run from this wake. — pass
- Original failure mode (debugging aid, not PASS evidence): on an unmodified `ai_main` clone, in a sandboxed env, direct pytest of `test_wake_contention_retry.py::test_contended_project_worked_via_retry_pass_in_same_firing` with `LOCK_WAIT_SECONDS=120` inherited fails with `subprocess.TimeoutExpired ... claude.sh --agent=writer timed out after 60 seconds`. With the wake names unset it passes (`1 passed in 11.32s`). — failure reproduced on the baseline as expected
- Fixture-side fix holds without the runner scrub (debugging aid): direct pytest of `test_wake_contention_retry.py` and `test_chain_lock_held_through_agent.py` on the RC with `LOCK_WAIT_SECONDS=120` inherited gives `7 passed`. — pass
- Drift guard: `bash team/scripts/run-unit-tests.sh --files tests/unit/test_wake_env_names_scrub.py tests/unit/test_config_loader_key_validation.py`: exit 0, `47 passed`. — pass

### Test fidelity
The pin tests exercise the real breaking condition, not a proxy. A real `claude.sh` wake runs under a real inherited `LOCK_WAIT_SECONDS`, contending with a real external lock holder, and the pre-fix shape of the same test reproduces the original 60 s hang on ai_main. No concern.

### Step 6.11 — spot-check outside the spec
- State drift: `## Active RC` v2.31.6 matches the existing local branch `ai_rc/v2.31.6`; the worktree HEAD is its tip. None found.
- Untested new mechanisms: the one new mechanism (`CONFIG_LOADER_WAKE_ENV_NAMES` plus its Python twin) is covered by `test_wake_env_names_scrub.py`, which derives the expected set from `load_config`'s source and from a live `load_config` run. None found.
- Regressions of prior gaps: BUG-0595 is the seventh instance of the "package module gains a package import without the by-path guard" class (BUG-0533, -0537, -0541, -0551, -0561, -0575, -0595, all done or running). This RC adds no new instance: the full by-path contract test passed. Observation only (see Caveats).

### Step 6.12 — Autonomous operation criterion
1. Tasks force-promoted WAITING→BACKLOG by hand? **NO.** This task's promotion text is the wake script's own ("Promoted from WAITING to BACKLOG by wake script: prerequisites satisfied"). CM-022 sits `[W]`/WAITING behind this task, as it should.
2. `release-state.md` edited outside the CM scripts? **NO.** File mtime 2026-10-07 12:42:51.55 +0000 equals `## RC Opened At` 2026-10-07T12:42:51+00:00, written by `scripts/cm/open-rc.sh` per CM-019's summary. Contents match that summary.
3. Scripts patched mid-build? **NO.** The RC carries exactly four commits: the two CODER feature commits and their `--no-ff` merges, timestamped inside those tasks' windows. CODER-018's edits to `team/scripts/` are that ticket's deliverable, not an out-of-band patch.
4. Queue files edited by hand? **NO** evidence. Every cycle entry's marker matches its task state: CODER-017/018 `[x]` DONE, CM-019 `[x]` DONE, PM-003 `[x]` DONE, TESTER-020 `[A]` WORKING, CM-022 `[W]` WAITING.
5. Non-CM remote git operation? **NO.** No `git-boundary-guard.jsonl` exists in any 20261007 task folder's `logs/`, this one included.

Autonomous operation criterion: Manual interventions required: none

## Lint Findings (non-blocking)

None. `findings-unit.json` (generated 2026-10-07T13:43:36Z) and `findings-integration.json` (generated 2026-10-07T13:56:22Z) each hold 0 findings.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=30d freshness=stale — The only ledger record is a red run 30 days old against v2.9.20, not v2.31.6, so it is stale on both age and version; that run filed its own bug, BUG-0167-cold-build-claude-abliterated-failure (now done; the `docker/claude-abliterated` image it failed on no longer exists in this tree, which ships only `docker/debian` and `docker/rocky9`), and nothing is known about whether this RC's images build until the operator runs the DEEP-VERIFY lane.

## Caveats
- **Artifact location / git workflow.** `roles/TESTER.md` ("Git workflow") says verification artifacts are committed on a feature branch off the RC and merged back. The task README names `## Feature Branch: none`, and the dispatch prompt explicitly forbids creating a feature branch. Artifacts therefore live only in the task folder's `artifacts/`, and nothing was committed to the RC. The worktree was left clean (`git status --porcelain` empty at the end).
- **Status at wake.** `status.md` read `WORKING` with no partial report and a stale "Promoted ... to BACKLOG" summary. I treated it as a fresh start. No `report.md.previous-RUN-N` exists; no prior verdict was consulted.
- **Coverage unavailable** (Step 5b): disabled by config, as above. Informational only.
- **Recurring by-path class (observation, not filed).** Seven bugs of the same class now exist. Each instance was caught by the gated unit runner and fixed in the next cycle, so the loop is healing it as designed. The recurrence comes from CODER's targeted `--files` runs not including `tests/unit/test_package_modules_run_by_path.py` when a package module gains a package import. Raising this is a judgment call for PM/operator. It is not a defect in this RC.
- **Fixture coupling outside the three files (observation, from CODER-018's notes).** About 25 other integration files write `[wake]` values into a sandbox `kanban.cfg` over a `dict(os.environ)` base without `scrub_wake_tunable_env`. Under the gated runners they are protected by the new runner scrub, and the full suite is green. Only a bare pytest run inside an agent wake could still couple them, and bare pytest is not a certification path. Not filed.
- **Guidelines doc wording (pass-with-caveat above).** `docs/test-authoring-guidelines.md` describes the replay family as declared in one list in `gated_runner_lint.sh`. The wake names are appended inside each runner instead, pinned identical by a test.
- **Negative fixture test not run as planned.** I tried to disable `scrub_wake_tunable_env` in a scratch clone to show the pin test fails without it. The permission system denied that command, and I did not retry it. CODER-018 reports performing that negative check. I substituted reproducing the original failure on the unmodified ai_main baseline, which shows the same thing from the other side.
- **Container tier.** Not invoked (operator-only, Step 5d). The Deep-Verify line above reports the ledger only.
- No prompt-injection-like content was encountered in any file read.
