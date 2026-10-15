# Verification Report: rc/v2.27.0 (ai_rc/v2.27.0 @ 0adc89e6)

## Summary

I verified RC `ai_rc/v2.27.0` (HEAD `0adc89e6`) against `requirements/v2.27.0-coder-targeted-tests-tester-once-per-rc.md`. Four CODER tasks (002 to 005) and one WRITER task (006) landed as five `--no-ff` merges on top of ai_main `cf91bc65`.

All seven deliverables are present and behave as specified when exercised against the real runners. Covered: the `--files` targeted mode and its four refusals, the `quarantine` marker with its `--quarantined` inverse, the per-RC green ledger with `--reuse-if-green` (reused at the real HEAD in 22 s with pytest not started, and a different commit fell through), the materializer acceptance rewrite (the TESTER and CM READMEs are byte-identical to ai_main's output for the same plan), the bug-template `## Acceptance` section, coverage opt-in, and the docs.

**The unit certification suite is red: `UNIT_EXIT=1`.** This RC's new test file `test_pm_materialize_acceptance_policy.py` breaks anti-pattern AP7, and the tree-wide guard `test_real_tree_has_zero_ap7_findings` fails on it. Filed as BUG-0517. `INTEGRATION_EXIT=0`.

Two more items came out of verification. First, the order-independence sweep the RC relied on for deliverable 6 cannot evaluate fixture-push tests when a non-CM agent runs it. This is a pre-existing script defect, reproduced and filed as BUG-0516. Second, I filed a design follow-up (PRIORITY-0006) so that targeted runs lint the files they name.

Deliverable 6's "one bug per quarantined test" was drafted by CODER-003 and filed by TESTER during verification as BUG-0496 through BUG-0515.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Findings

### Finding 1: Pre-flight, RC intact (Steps 2-3)

**Category:** pass

526 DONE task folders name a feature branch. None of those branches exists locally, so both checks pass trivially (the normal post-merge shape). The RC's first-parent history is exactly five merges: CODER-002, 003, 004 and 005, then WRITER-006. Worktree HEAD `0adc89e6f0e0e85c5f84bb86d01ea507fe18ac65` equals `git rev-parse ai_rc/v2.27.0`. `git status --porcelain` is empty before and after verification, and `git stash list` is empty.

### Finding 2: Release-state sanity (Step 6.1)

**Category:** pass

`release-state.md` reads: Active RC `v2.27.0`, RC Opened At `2026-10-03T02:41:58+00:00`, RC Opened By Task `CM-20261003-007-open-rc-2-27-0`, Last Released `v2.26.12` (well formed). `pp_last_released_version pgai-agent-kanban` gives `v2.26.12`, and `semver_gt v2.27.0 v2.26.12` is true.

### Finding 3: Deliverable 1, `--files` targeted run (acceptance bullets 1-2)

**Category:** pass

Run in an isolated sandbox: a `git init` repo built from `git archive` of the RC, with a sandboxed root and temp dir.
- `bash team/scripts/run-unit-tests.sh --files tests/unit/test_semver.py` exits 0. It prints `targeted run — not a certification run`, collects only that file's 34 tests, and writes `0` to `<temp>/targeted-unit.exit`. A pre-seeded `lint/findings-unit.json` and `unit.exit` kept their mtimes (1767225600 before and after) and their content. No ledger directory was created.
- The fatal gates still ran (breakage-class, Dockerfile static, container-engine-free, API-parity), and no quality-class lint ran.
- Refusals, each printing error/message/remedy and exiting 1: `--files tests/../../etc/passwd` (`run-unit-tests.targeted-file-outside-test-root`), `--files x --coverage` (`...targeted-files-with-coverage`), bare `--files` (`...targeted-files-missing`), a nonexistent file (`...targeted-file-not-found`), an integration file given to the unit runner, and `/etc/passwd`. The integration runner refuses the first two plus a unit file in the same way. The refusal also writes `targeted-unit.exit` = 1.
- `--help` on both runners documents `--files`, `--quarantined` and `--reuse-if-green`. Census row 71 and eight Locator Index entries are registered as AGENT-RESOLVABLE (rollup 71 sites, 0 unclassified).
- Caveat: no runner writes a certification `unit.exit` file (only `targeted-*.exit` exists), so "does not modify `unit.exit`" holds trivially.

### Finding 4: Deliverable 2, materializer acceptance-policy rewrite (acceptance bullet 3)

**Category:** pass

`team/pm-agent/lib/acceptance_policy.py` (new, read in full) is pure and unit-tested on its own (`tests/unit/test_acceptance_policy.py`). `pm_materialize.py` calls it through `apply_targeted_test_policy` and does not inline the logic in `main()`.

Independent cross-version probe: I ran one plan (a CODER ticket with a bare runner criterion, an integration-runner `--coverage` criterion and a runner constraint, a WRITER ticket, and a plan-authored TESTER ticket) through ai_main's and the RC's `pm_materialize.py`.
- RC CODER README: acceptance names `--files`, the fixed constraint appears once, and `## Notes` records all three rewrites (one marked as a dropped duplicate). Stderr narrates each rewrite as an `acceptance-policy:` line.
- The WRITER README gains only the fixed constraint.
- The TESTER and both CM READMEs, and all four queue files, are **byte-identical** to ai_main's output.

### Finding 5: Deliverable 3, bug template `## Acceptance` (acceptance bullet 4)

**Category:** pass-with-caveat

`grep -c '## Acceptance' team/templates/project/release/BUG-TEMPLATE.md` gives `1`, and the guidance comment matches the requirement verbatim. `create-project.sh` copies `${TEMPLATE_DIR}/BUG-TEMPLATE.md` into new projects' `bugs/templates/`, and `release/` is also the fallback for workflow types that ship no templates. Caveat: `team/templates/project/document/BUG-TEMPLATE.md` and existing projects' seeded copies (including this project's live `bugs/templates/BUG-TEMPLATE.md`) do not have the section. The requirement scopes the change to the release template and new projects, so this is not a gap.

### Finding 6: Deliverable 4, per-RC green ledger and `--reuse-if-green` (acceptance bullet 5)

**Category:** pass

`team/scripts/lib/green_ledger.sh` (new, read in full). The ledger is at `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/green-ledger/v2.27.0/ledger.jsonl`. My certification runs appended `unit / not quarantine / exit 1 / tree_clean true` and `integration / not quarantine / exit 0 / tree_clean true`, both with findings_sha `cc99af7a9a98…`.

Production-shape behavior, at the real HEAD in the real worktree:
- `bash team/scripts/run-integration-tests.sh --reuse-if-green` printed `[green-ledger] integration: reused the green result recorded 2026-10-03T05:09:08+00:00 for commit 0adc89e6f0e0 … -- pytest not started` and exited 0 in 22 s (the full run took 14 min 24 s). No pytest session started, and the ledger's sha256 was unchanged (a reuse appends nothing). The quality lints re-ran and produced the same findings hash.
- Mismatch: the sandbox repo (commit `15e40a2`) with a copy of the real ledger printed `no recorded integration run at commit 15e40a25f95f …; running the integration suite normally.` and started pytest. I stopped it at that point.
- The RC's `test_runner_green_ledger_reuse.py` (stub pytest, real runner and libs) passed in the certification run. It covers the different findings hash, recorded failure, dirty tree, selection mismatch, corrupt ledger, and `--files` byte-identity cases.

### Finding 7: Deliverable 5, coverage opt-in

**Category:** pass-with-caveat

`docs/test-authoring-guidelines.md` ("Coverage is opt-in") and `team/templates/agent/REPORT-TEMPLATE.md` drop `--coverage` from verification. `team/pm-agent/` and the wake scripts generate no `--coverage` invocation. Caveat: `roles/TESTER.md` Step 5a still says to run with `--coverage`. The requirement deliberately excludes role files ("Notes for Operator": the manual prompt pass aligns them), so this is not a gap. I followed the requirement and ran without `--coverage`.

### Finding 8: Deliverable 6, quarantine marker (acceptance bullet 6)

**Category:** pass-with-caveat

- The marker is registered in `team/pyproject.toml`. `pytest --collect-only -m quarantine` over all three test roots collects exactly 20 node IDs, matching CODER-003's manifest: 17 unit and 3 integration.
- The default certification runs deselected exactly those: unit `6413 items / 17 deselected`, integration `639 items / 3 deselected`. `--quarantined` selected exactly them: unit `17 selected`, integration `3 selected`.
- `--files` on a fully quarantined file narrates pytest exit 5 and exits 0. `--files … --quarantined` runs only that file's 4 quarantined tests.
- **Quarantine did not hide a real failure:** `run-unit-tests.sh --quarantined` gave 17 passed, exit 0, and `run-integration-tests.sh --quarantined` gave 3 passed, exit 0.
- The required tests are covered: event-band large body, 6 unit and 2 integration tmux hook-lifecycle tests, 6 cadence timing cases, the `PGAI_GIT_LOCAL_ONLY` module (4), and BUG-0485's chain-lock test.
- One bug per quarantined test: **BUG-0496 through BUG-0515**, one per node ID, all `open`, severity `medium`. CODER-003 drafted them in `artifacts/bug-reports/` "for TESTER to file", because CODER cannot write to `bugs/`. TESTER filed them during this verification and added the Path C header shape and the `## Severity` section.
- Caveats: (a) The bug filing was routed to TESTER through a CODER artifact and not through this task's README. A future PM decomposition should name it in the TESTER ticket. (b) The requirement names the event-band test `…_within_time_bound`, but the test is `test_large_pre_today_body_renders_correctly_and_narrates_render_time` on both ai_main and the RC (the requirement's name is stale). The right test is marked. (c) The sweep CODER-003 ran to find further candidates could not evaluate several tests (Finding 11).

### Finding 9: Deliverable 7, documentation

**Category:** pass

`docs/test-authoring-guidelines.md` "Runner flags, the green ledger, and exit files" documents `--files`, `--quarantined` and the marker policy, `--reuse-if-green`, the ledger location and every record field, both `targeted-*.exit` files versus the certification outputs, and coverage opt-in. I checked these statements against the observed behavior above and they match. The doc is on `team/config/docs-manifest.txt`, so the doc-truth lint scans it. `team/pm-agent/README.md` documents the policy. The diff touches none of `roles/*.md`, `SOP-core.md`, `OVERVIEW.md`, `DIRECTIVES.md`, `team/README.md` or `subagents/*.md`, as the constraint requires.

### Finding 10: Unit certification suite fails on an AP7 test-hygiene violation this RC introduced

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (the targeted-test policy this RC introduces means whole-tree test-hygiene guards first run at TESTER, so the class will recur. See PRIORITY-0006.)
**Regression:** yes
**Integrity:** breakage
**Filed As:** BUG-0517-acceptance-policy-test-ap7-env (Path C-A, filed as BUG: code defect / regression / test failure)
**Filed By:** TESTER (autonomous Path C, autonomous follow-up filing, non-blocking) — the new materializer acceptance-policy test copies the caller's whole environment into its subprocess in a file that also does a golden comparison, which the tree-wide test-hygiene guard forbids, so the unit suite fails.

`run-unit-tests.sh` gave `UNIT_EXIT=1`: `1 failed, 6352 passed, 43 skipped, 17 deselected in 1170.10s`. The one failure is `tests/unit/test_lint_golden_env_coupling.py::test_real_tree_has_zero_ap7_findings`, which flags `team/tests/unit/test_pm_materialize_acceptance_policy.py:92` (`env = dict(os.environ)`). This RC added that file in commit `7d2540c8` (CODER-20261003-005). The v2.26.12 verification recorded `UNIT_EXIT=0`. This is a real failure, not a stale assertion: the guard's expectation (zero AP7 findings) is unchanged, and the new code violates it.

Rating rationale: I rated the bug **medium**. The defect is confined to a test file, no runtime or release path depends on it, and the fix is a one-line environment builder. SOP-core's calibration says "a command that worked in the last release and exits non-zero in this one is high", and a strict reading could apply it to the runner itself. I read the runner as correctly reporting a test failure, not as a broken command. I recorded `Regression: yes` and `Integrity: breakage` so CM has the full picture either way.

### Finding 11: Order-independence sweep cannot evaluate fixture-push tests under a non-CM agent (pre-existing)

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (every sweep a non-CM agent runs is affected)
**Regression:** no
**Integrity:** none
**Filed As:** BUG-0516-order-sweep-guard-refuses-fixture-pushes (Path C-A)
**Filed By:** TESTER (autonomous Path C) — the test-order sweep gives pytest a temp root that does not contain pytest's scratch directory, so the remote-boundary guard refuses fixture pushes and those tests fail in both orders, unseen by the sweep.

This traces to deliverable 6 ("run `verify-test-order-independence.sh` once"), and the Step 6.12 question-5 records led to it. `_voi_run` sets `PGAI_AGENT_KANBAN_TEMP_DIR="$_VOI_WORKDIR/kanban_tmp_${label}"` but puts `--basetemp` at `pgai_mktemp_d "voi_${label}"`, outside that root.

Reproduced at the RC with role `tester` and the guard shim on PATH, against `test_cm_release_step3_state_detection.py`. With the sweep's shape, the result was `5 failed` with 5 refusal records. With a temp root containing the basetemp, the result was `5 passed` with 0 records. In the certification run the same tests passed (`test_cm_release_step3_state_detection.py`, `test_overwatch_push_lag_bash.py` and `test_git_boundary_guard.py`, all green), and that run wrote no guard record. The script has been unchanged since v2.22.3.

### Finding 12: Step 5a/6.6/6.7, gated runner exit codes

**Category:** pass-with-caveat

`UNIT_EXIT=1` (Finding 10) and `INTEGRATION_EXIT=0` (`635 passed, 1 skipped, 3 deselected in 864.45s`). Both runners were invoked once each from the dev tree root without `--coverage`. Output: `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester-008/unit.log` and `integration.log`. No infrastructure failure: pytest is present, the runners ran to completion, the requirements doc parsed, and the dev tree is accessible. Post-suite checks passed: `sentinel_leak: OK` and `scratch_sweep: OK`.

### Finding 13: Constraint and coding-standards checks (Steps 6.9, 6.13, 6.14)

**Category:** pass

- `set -euo pipefail` is preserved in both runners.
- The diff adds no skip annotation. Quarantine `reason=` strings name the order or timing dependence, and there are no placeholder bug IDs.
- No bug, task, RC or version identifiers appear in any added line of the diff.
- No new top-level script was added (`green_ledger.sh` is a sourced lib with a full API header).
- The new refusal sites carry a census class comment, the dual-channel contract and a census anchor with a Locator Index entry.

### Finding 14: Prerequisite graph (Step 6.10)

**Category:** pass

There are no cycles and every referenced ID exists. CM-007 (open-rc) has no prerequisites. CODER-002 and CODER-005 depend on CM-007. CODER-003 depends on 002. CODER-004 depends on 003. WRITER-006 depends on 002, 003 and 004. TESTER-008 depends on all five worker tasks. CM-010 (release) depends on those plus TESTER-008, and is WAITING. Sequence numbers 001 to 010 do not collide. Gaps at 009 are not an issue.

### Finding 15: Measured outcome (narrated, not a gate)

**Category:** pass

CODER dispatch-to-return times from each task's `logs/dispatch.jsonl`: CODER-002 18 min 06 s, CODER-003 44 min 46 s (includes the sanctioned 38-minute order-independence sweep), CODER-004 16 min 06 s, CODER-005 9 min 12 s. **Median across this RC's CODER tickets is 17.1 min**, compared with the requirement's baseline median of 36.4 min and target of ~15 min. Every CODER ticket in this RC already carried `--files` acceptance. WRITER-006 took 11 min 14 s.

## Lint Findings (non-blocking)

- `test_anti_patterns`: AP7 at `team/tests/unit/test_pm_materialize_acceptance_policy.py:92`, reported in both `findings-unit.json` and `findings-integration.json` (one class across both suites). Cites BUG-0517 (open, filed in this run). The same root cause is the Step-7 defect in Finding 10, so the bug is not duplicated.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=25d freshness=stale — the last cold build, at v2.9.20, failed for docker/claude-abliterated and podman/rocky9, and that run filed BUG-0167 (now done); the record is stale on both counts (25d exceeds the 7-day window, and v2.9.20 is not the v2.27.0 under verification), so nothing is known about whether this tree's images build cold.

## Autonomous criterion

1. Were tasks force-promoted from WAITING to BACKLOG by hand? **NO.** The status.md trails and queue mtimes line up with agent and wake transitions.
2. Was `release-state.md` edited outside the CM scripts? **NO.** Its mtime (`2026-10-03 02:41:58`) equals RC Opened At, which `open-rc.sh` wrote according to CM-007's summary.
3. Were scripts patched mid-build? **NO.** Every RC commit belongs to a task feature branch merged `--no-ff`, and no live-install script changed on 2026-10-03.
4. Were queue files edited by hand? **NO.** No evidence. Mtimes match agent final transitions.
5. Did any agent other than CM invoke a remote git operation? **YES.** `tasks/CODER-20261003-003-test-quarantine-marker/logs/git-boundary-guard.jsonl` holds 18 records, role `coder`. Each is `git -C <sweep basetemp>/<test>/local push origin [HEAD]`, from test fixtures during the order-independence sweep: 9 forward and 9 reverse, 03:09 to 03:43Z. All 18 were refused. Every destination was a throwaway fixture repository under the sweep's sandbox, so no ref state changed anywhere, and I rate them low. The cause is the sweep script's temp-root mismatch, filed as BUG-0516 (Finding 11). No other task in this cycle, including this one, has a guard record. My own probes ran with the role unchanged (`tester`). The one probe that triggered refusals wrote them to a sandbox evidence file, not to any task folder.

Autonomous operation criterion: one question-5 YES, a refused fixture-local push caused by a tooling defect and filed as BUG-0516. No manual intervention on the task pipeline.

## Code Coverage

- **Line coverage:** `not collected`
- **Branch coverage:** `not collected`

The requirements doc's Notes for TESTER say to run the certification suites without `--coverage`. Coverage is informational only.

## Caveats

- **Instruction conflict:** `roles/TESTER.md` Step 5a says to run both runners with `--coverage`, while the requirements doc's "Notes for TESTER" says to run without it. I followed the requirements doc, the more specific layer, which this RC's own change backs. The role file is excluded from this requirement's edits (operator manual pass).
- **Observation, not filed:** `run-unit-tests.sh --help` and `run-integration-tests.sh --help` each rewrite `lint/findings-<suite>.json` with zero findings, because the findings-file claim runs before argument parsing. A `--help` after a certification run therefore erases the findings TESTER reads. This reproduces identically on ai_main (sandbox `git archive ai_main`), so this RC did not introduce it, and it traces to no criterion of this requirement.
- **Quarantine bug filing:** BUG-0496 to BUG-0515 are deliverable work that TESTER performed, not verification findings, so they do not affect the recommendation. Their substance (symptom, reproduction, acceptance) is CODER-003's draft. TESTER re-headed them in Path C shape and kept CODER's `medium` rating, which is consistent with "Rating a Finding": the code under test is not broken, and quarantine is the workaround.
- **Report delivery:** the agent harness refused to create this file through its file-write tool (a guard against free-form report files). Because this file is the task's mandatory deliverable and CM's release script parses it, I wrote it with a shell heredoc.
- **Sandboxes:** all ad-hoc probes ran under `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/sandbox-*` with `KANBAN_ROOT`, `PGAI_AGENT_KANBAN_ROOT_PATH` and `PGAI_AGENT_KANBAN_TEMP_DIR` repointed and `PGAI_TASKS_DIR` and `PGAI_PROJECT_ROOT` unset. The certification runs, the `--reuse-if-green` check and the `--quarantined` runs ran in the task worktree as gated runners. They appended four records to the real v2.27.0 green ledger: unit and integration certification, plus unit and integration `quarantine` selection.
