# Verification Report: rc/v2.30.2

## Summary

This report verifies `ai_rc/v2.30.2` (commit 048cdd7e) against `requirements/v2.30.2-bugfix-bundle-20261006.md`. The bundle has two items. BUG-0567 is the regression: four bare `python3 -m pgai_agent_kanban` calls broke the `pp_run_ops` sweep test and turned the gated unit runner red. BUG-0568 is the hygiene ride-along: `wake_driver_invoke.sh` was at 805 lines, over the 800-line engine ceiling.

Both bugs are fixed, and the fixes behave as specified. Both gated runners exit 0 with zero lint findings. A before/after harness built from `ai_main` and the RC shows the API port resolution, the dashboard HALT-AFTER describer, and the release-spend narration are byte-identical. The only difference is the remedy string, which was meant to change and now runs as pasted. Both guards fire when their original defect is reintroduced.

A Step 6.11 spot-check of the HALT-AFTER spend path confirmed a defect that predates this RC. When a HALT-AFTER sentinel that is not yet due is armed at ship time, `cm/release.sh` aborts with exit 1 right after tagging. I reproduced this through the real `cm/release.sh` and filed it via Path C-A as BUG-0569 (medium, Regression: no). No sentinel is armed in the live kanban now, so the v2.30.2 ship itself is not exposed.

Agent: tester. Model: claude-opus-5-5.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Gated Suites (Step 5a / 6.6 / 6.7)

| Suite | Command | Exit | Result |
|---|---|---|---|
| unit | `team/scripts/run-unit-tests.sh --coverage` | **UNIT_EXIT=0** | lint pre-flight OK; parallel lane 9631 passed, 41 skipped; serial lane 5 passed |
| integration | `team/scripts/run-integration-tests.sh --coverage` | **INTEGRATION_EXIT=0** | lint pre-flight OK (incl. engine_size_dupe OK: 44 files within ceiling); parallel lane 859 passed, 1 skipped; serial lane exit 5 (none collected), combined 0 per the runner's lane rule |

Both ran from the worktree root with `PGAI_DEV_TREE_PATH` set to the worktree, at 05:18:57Z–05:43:16Z. Output: `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester-016/unit.log` and `int.log`. Green-ledger entries were recorded at commit 048cdd7e3266. Direct pytest was used only once, for a behavioural probe in a throwaway clone (see Findings), and is not cited as suite evidence.

**Coverage (Step 5b, informational):** coverage unavailable. Both runners printed "coverage disabled by config ([testing] coverage)", so neither wrote a coverage JSON for this run.

## Findings

### Bundle acceptance criteria

#### Finding 1: AC1 — each bundled item is addressed

**Category:** pass

BUG-0567: all four sites now go through `pp_run_ops`, and no exemption was added. `test_pp_run_ops_sweep.py` is unchanged from `ai_main`, so the sweep was not weakened. `grep -rnE 'python3[[:space:]]+-m[[:space:]]+pgai_agent_kanban' team/scripts` hits only `lib/pp_run_ops.sh` (a comment) and `generate-icd.sh`, and both are in the existing `_EXEMPT_SUFFIXES`. The sweep test passes as part of the green unit runner, and also on its own via `run-unit-tests.sh --files tests/unit/test_pp_run_ops_sweep.py` (exit 0, 4 passed).

BUG-0568: `wc -l team/scripts/lib/wake_driver_invoke.sh` = 767 (was 805). The classifier `_wd_is_safeguard_refusal` moved verbatim into the new stage `team/scripts/lib/wake_safeguard_refusal.sh` (66 lines, mode 755). That stage is registered in `ENGINE_FILES`, and `wake_driver_invoke.sh` sources it itself. The ceiling and allow-list are unchanged. Neither runner's findings file has an `engine_size_dupe` entry (both files have 0 findings). `lint_engine_size_dupe.py` exits 0 on the RC.

#### Finding 2: AC2 — all Python files compile cleanly

**Category:** pass

All 775 tracked `.py` files compiled in memory with 0 failures.

#### Finding 3: AC3 — all bash files pass syntax check

**Category:** pass

All 322 tracked bash files (`*.sh` plus files with a bash/sh shebang) pass `bash -n` with 0 failures.

### Bundled bugs' own acceptance lines (Step 6.4 / 6.5)

#### Finding 4: BUG-0567 behaviour preserved (port, describer text, exit codes)

**Category:** pass

I built installed-layout sandbox roots from `git archive ai_main team` and from the RC, and ran each under `env -i` from a foreign working directory.
- Port resolution: a listener held the port so `api-server.sh status` would name the port it probed. The probed port is identical on both trees for the kanban.cfg port, for the kanban.cfg port with a decoy `pgai_agent_kanban` package first on PYTHONPATH, and for the `PGAI_API_PORT` override.
- Malformed `kanban.cfg`: both trees exit 1 with `api-server.port.unresolved`.
- Remedy text: this is the only output that differs, and the change was intended. The RC remedy now names `bash <root>/scripts/lib/pp_run_ops.sh pgai_agent_kanban.api.config port`. Pasted as printed, it reproduces the `MissingSectionHeaderError` (exit 1), and after the config is repaired it prints `8411` (exit 0).
- `dashboard_halt_describe_event`, for tokens `rc:v2.9.15`, `coder`, `rc` and empty, each with and without the decoy: byte-identical output and rc=0 on both trees.
- `cm_release_spend_halt_after_sentinel` under `set -euo pipefail`, with the evaluator stubbed to promote, for version-pinned and event-pinned tokens with and without the decoy: byte-identical narration and rc=0 on both trees.

The CODER-013 targeted list (`test_api_server_lifecycle_bash.py`, `test_bash_facing_python_clis.py`, `test_posture_bar_truth.py`) passes: exit 0, 72 passed. The new integration test `test_pp_run_ops_routed_sites.py` exercises the real breaking conditions (installed layout, foreign cwd, decoy on PYTHONPATH), not a sanitized stand-in. It passes in the suite and in a targeted run.

#### Finding 5: BUG-0567 guard fires on the original failure mode

**Category:** pass

In a `git clone` of the RC, I put the bare `python3 -m pgai_agent_kanban.halt_after.describe` call back at `halt_scope.sh:279`. `test_pp_run_ops_sweep.py` then fails and names that line (1 failed, 3 passed). With the file restored, all 4 pass.

#### Finding 6: BUG-0568 behaviour preserved and guard confirmed

**Category:** pass

- The `_wd_is_safeguard_refusal` body taken from `ai_main`'s `wake_driver_invoke.sh` is byte-identical (`cmp`) to the body in the new stage.
- Sourcing `wake_driver_invoke.sh` on its own in a sandbox defines `_wd_is_safeguard_refusal`, `_wd_build_governance_line` and `pp_run_ops`. Six probe strings classify as expected.
- The only production loader is `wake_driver.sh:141`. `install.sh` installs the whole `team/scripts` tree, so the new stage ships.
- The token-capture `json_field` reads are not touched by the diff.
- The CODER-014 targeted list plus `test_wake_telemetry_safeguard_retry.py` passes: exit 0, 102 passed.
- Guard: `lint_engine_size_dupe.py` on the `ai_main` tree reports `lib/wake_driver_invoke.sh -- 805 lines (ceiling: 800)` (exit 3), and on the RC tree it exits 0.

### Methodology steps

#### Finding 7: Pre-flight, worktree, release-state, prerequisite graph (Steps 2, 3, 6.1, 6.10)

**Category:** pass

- Step 2: 618 DONE tasks name a feature branch, and none of those branches exists locally (deleted after merge), so the checks pass trivially.
- Step 3: HEAD 048cdd7e equals `ai_rc/v2.30.2`. The checkout is clean and there are no stashes.
- Step 6.1: `pp_last_released_version` = v2.30.1, and `semver_gt v2.30.2 v2.30.1` is true. `## Last Released` is well-formed.
- Step 6.10: CM-015 → CODER-013/014 → TESTER-016 → CM-018. No cycles and no collisions.

#### Finding 8: Synthetic release simulation (Step 6.8)

**Category:** pass

The diff touches `team/scripts/lib/cm/release_halt_after_spend.sh`, which runs in the release close cycle. `run-integration-tests.sh --files tests/integration/test_cm_release_synthetic.py tests/integration/test_halt_after_rc_close_gate.py tests/integration/test_pp_run_ops_routed_sites.py` exits 0 with 26 passed. All three files also pass inside the green certification run.

#### Finding 9: Constraint, skip-gate and coding-standards checks (Steps 6.9, 6.13, 6.14)

**Category:** pass

- Naming and provenance: the added lines carry no bug, task, RC or version citations, and nothing that looks like a secret.
- Skip gate: `lint_skip_bug_gate` is OK in both runner pre-flights. The only new skip is a guarded-binary `narrate_binary_skip`, not a deferral.
- Coding standards: the new stage is a sourced library with a documented header (no `--help` owed), and the classifier still has exactly one definition in the tree.

#### Finding 10: Pre-existing — release.sh aborts after tagging when a not-yet-due HALT-AFTER is armed

**Category:** bug (pre-existing; Step 6.11 spot-check, untested mechanism)
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (release-pipeline path; recurs on every ship while a not-yet-due sentinel is armed)
**Regression:** no
**Integrity:** none
**Filed As:** BUG-0569-halt-after-errexit-aborts-release (Path C-A — filed as BUG: code defect)
**Filed By:** TESTER (autonomous Path C) — when a HALT-AFTER sentinel is armed but not yet due at ship time, the release script dies right after tagging, because the HALT-AFTER evaluator wrapper turns "exit on error" back on just before returning its non-zero "not yet" answer.

`pgai_halt_after_run` in `team/scripts/lib/halt_after_invoke.sh` (lines 46–55) runs `set -e` immediately before `return "$PGAI_HALT_AFTER_EXIT"`. The caller, `cm_release_spend_halt_after_sentinel`, wraps the call in `set +e ... set -e`, but that cannot help: the callee has already switched errexit back on when it returns non-zero. A `set -e` shell such as `cm/release.sh` therefore exits on the spot, and the documented "not yet drained → return 0" branch never runs.

Evidence:
1. I called the real functions under `set -euo pipefail` with a stub evaluator that exits 1. The shell exits 1 after printing only the Step 12c header, on both `ai_main` and the RC.
2. A scratch probe in a throwaway clone drove the real `cm/release.sh` through `test_halt_after_rc_close_gate.py`'s `_ship_one_release`, shipping v0.1.0 with `rc:v0.9.0` armed. Exit code 1. The last step printed was 12c. The tag had already been created (Step 13a), and release-state had been updated (Step 12). Steps 13c, 13d, 14, 15, 16 and 16b never ran, so bundle and bug statuses stayed `running` and nothing was stamped Fixed In. HALT-AFTER remained.

`halt_after_invoke.sh` is unchanged by this RC. CODER-013 noticed the issue and left it out of scope; I reproduced it before filing. Existing release-level tests cover only the promote path.

Not exercised by this ship: no HALT-AFTER is armed at root or project scope in the live kanban. Workaround: do not arm a HALT-AFTER for a later version while an earlier release is shipping. Medium under SOP-core's calibration (a real defect that is not exercised today, has a workaround, and predates the RC). It is not `high`.

#### Finding 11: Autonomous operation criterion (Step 6.12)

**Category:** pass

All five questions answer NO (detail under "Autonomous criterion" below).

## Lint Findings (non-blocking)

None. `findings-unit.json` and `findings-integration.json` from this run both contain 0 findings (generated 05:33:44Z and 05:43:16Z).

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=28d freshness=stale — The last deep-verify record is a red run on v2.9.20, 28 days old, so it is stale on both the 7-day window and the version (not v2.30.2). Its failures (docker/claude-abliterated parse errors, podman/rocky9 COPY --chmod) were filed by that run as BUG-0167, now done, and nothing is known yet about whether the current images build.

## Autonomous criterion

1. Tasks force-promoted WAITING→BACKLOG by hand: **NO**. This task's promotion was recorded by the wake script ("Promoted from WAITING to BACKLOG by wake script: prerequisites satisfied").
2. `release-state.md` edited outside cm open/release/cancel: **NO**. The file's mtime (04:42:04.827Z) matches CM-015's open-rc run, and its fields match what that task recorded.
3. Scripts patched mid-build: **NO**. The only RC commits under `team/scripts/` are the two tickets' own deliverables (f8c64b26, ae25181e, bf4c1345), made inside their task windows. No file under the live install's `scripts/` or `pgai_agent_kanban/` was modified after RC open.
4. Queue files edited by hand: **NO**. Queue mtimes line up with wake and dispatch events, and there are no stray markers.
5. Remote git operation by a non-CM agent: **NO**. None of this cycle's task folders (CM-015, PM-002, CODER-013, CODER-014, TESTER-016, CM-018) contains a `git-boundary-guard.jsonl`.

Autonomous operation criterion: Manual interventions required: none

## Caveats

- Coverage numbers are not available because coverage is disabled by `[testing] coverage` config. This does not affect the verdict.
- My first before/after harness pass used a scrubbed PATH that resolved `/usr/bin/python3` (3.9), which the package does not support, so both trees failed identically. That was a harness defect and not a finding. I re-ran with the suite's Python 3.12, and those are the results reported above.
- Targeted `--files` runs and probes used a separate `PGAI_AGENT_KANBAN_TEMP_DIR` (`.../tester-016/tgt-tmp`), so they could not overwrite the certification run's findings files.
- CODER-013's Summary also notes an edge case it documented rather than fixed. With the evaluator stubbed, a kanban root with no package would now be described rather than printed raw, because `pp_run_ops` appends its own tree to PYTHONPATH. The real flow cannot reach this: the evaluator must import and promote before the describer runs. I did not file it.
- Scratch, harnesses and logs are under `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester-016/`.

## Verification Log (incremental checkpoints, as written during the run)


- Step 1: fresh start (no prior report.md/gaps.md in artifacts/). Status set WORKING.
- Step 2 pre-flight: 618 DONE tasks with a named feature branch scanned; none of their branches exist locally (post-merge deletion), so Check 1 and Check 2 pass trivially. No anomaly.
- Step 3: worktree HEAD 048cdd7e == ai_rc/v2.30.2; `git status --porcelain` empty; `git stash list` empty.
- Step 4: `git diff ai_main..ai_rc/v2.30.2` = 7 files (+394/-60), commits f8c64b26, ae25181e (CODER-013, merge 8fdfefec) and bf4c1345 (CODER-014, merge 048cdd7e). New files read in full: team/scripts/lib/wake_safeguard_refusal.sh, team/tests/integration/test_pp_run_ops_routed_sites.py.
- Checklist. Deliverables: BUG-0567 four sites fixed (api-server.sh x2, halt_scope.sh, release_halt_after_spend.sh); BUG-0568 wake_driver_invoke.sh <= 800 lines. Acceptance: (AC1) each bundled item addressed; (AC2) all Python compiles; (AC3) all bash passes syntax check; plus each bug's own Acceptance lines. Constraints (from tickets/bugs): sweep not weakened, behaviour (port, describe text, exit codes, token capture) unchanged, no ceiling/allow-list change, no new inline Python, naming rule.
- Bundle AC2: 775 tracked .py files compiled in memory, 0 failures. Bundle AC3: 322 tracked bash files (`*.sh` + bash/sh shebang), `bash -n` 0 failures.
- Structural: wc -l wake_driver_invoke.sh = 767 (was 805 on v2.30.1 / ai_main); new stage wake_safeguard_refusal.sh 66 lines, mode 755, in ENGINE_FILES. `grep -rnE 'python3[[:space:]]+-m[[:space:]]+pgai_agent_kanban' team/scripts` hits only pp_run_ops.sh (comment) and generate-icd.sh, both in the unchanged _EXEMPT_SUFFIXES. test_pp_run_ops_sweep.py unchanged vs ai_main (sweep not weakened).
- Targeted acceptance commands (isolated PGAI_AGENT_KANBAN_TEMP_DIR so the background certification run's findings files are untouched): `run-unit-tests.sh --files tests/unit/test_pp_run_ops_sweep.py` exit 0 (4 passed); CODER-014 list + test_wake_telemetry_safeguard_retry.py exit 0 (102 passed); CODER-013 list (api_server_lifecycle_bash, bash_facing_python_clis, posture_bar_truth) exit 0 (72 passed). `lint_engine_size_dupe.py` default args exit 0 (44 engine files in ceiling, no collisions). These are supporting evidence only, not PASS evidence.
- Behavioural before/after (Step 6.5): installed-layout sandbox roots built from `git archive ai_main team` and from the RC, run with `env -i` from a foreign cwd. api-server.sh status with a listener holding the port: cfg port, cfg port + decoy package first on PYTHONPATH, and PGAI_API_PORT override all probe the expected port on BOTH trees (identical). Malformed kanban.cfg: exit 1 + `api-server.port.unresolved` on both trees; only the remedy text differs (intended change). The RC remedy, pasted as printed, reproduces the MissingSectionHeaderError (exit 1) and prints 8411 (exit 0) once repaired. dashboard_halt_describe_event for `rc:v2.9.15`, `coder`, `rc`, empty, each with/without decoy: byte-identical output and rc=0 on both trees ("until v2.9.15", "coder", "rc", ""). cm_release_spend_halt_after_sentinel under `set -euo pipefail` (evaluator stubbed to promote) for version-pinned and event-pinned tokens, with/without decoy: byte-identical narration and rc=0 on both trees. Harness: /tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester-016/harness-0567.sh, harness-port.sh; outputs behaviour-main.txt / behaviour-rc.txt.
- Guard failure modes: (a) in a `git clone` of the RC, reintroducing `python3 -m pgai_agent_kanban.halt_after.describe` at halt_scope.sh:279 makes test_pp_run_ops_sweep fail naming that line (1 failed, 3 passed); restored, 4 passed. (b) lint_engine_size_dupe on the ai_main tree reports `lib/wake_driver_invoke.sh -- 805 lines (ceiling: 800)` (exit 3); on the RC tree exit 0.
- BUG-0568 behaviour: `_wd_is_safeguard_refusal` body extracted from ai_main's wake_driver_invoke.sh and from the new wake_safeguard_refusal.sh is byte-identical (cmp). Sourcing wake_driver_invoke.sh on its own in a sandbox defines `_wd_is_safeguard_refusal`, `_wd_build_governance_line`, `pp_run_ops`; classifier outputs on 6 probe strings as expected (safety/content-policy -> 0; network/429 exclusions, plain decline, empty -> 1). The only loader is wake_driver.sh:141 (plus unit tests sourcing the stage); install.sh installs the whole team/scripts tree, so the new stage ships. Token-capture json_field reads untouched by the diff.
- Spot-check (6.11, untested mechanism, flagged in CODER-013's Summary): reproduced. `pgai_halt_after_run` (team/scripts/lib/halt_after_invoke.sh:46-55) runs `set -e` immediately before `return "$PGAI_HALT_AFTER_EXIT"`. Its caller in cm_release_spend_halt_after_sentinel wraps the call in `set +e ... set -e`, but the callee has already turned errexit back on when it returns non-zero, so a `set -e` shell exits. Evidence: (1) the real functions under `set -euo pipefail`, with a stub evaluator returning 1, give shell exit 1 after printing only the Step 12c header, on both ai_main and the RC. (2) A scratch probe in a throwaway clone drove the real cm/release.sh through `_ship_one_release` (from test_halt_after_rc_close_gate.py) with sentinel `rc:v0.9.0` while shipping v0.1.0. release.sh exited 1. The last step printed was 12c. The tag had already been created (Step 13a) and release-state updated (Step 12). Steps 13c/13d/14/15/16/16b did not run, and HALT-AFTER remained. halt_after_invoke.sh is unchanged by this RC (last touched by the v2.20.0 release), so this predates the RC. No existing bug in bugs/ references it. Filed below as a finding.
- 6.1: release-state.md has Active RC v2.30.2, RC Opened At 2026-10-06T04:42:04+00:00, RC Opened By Task CM-20261006-015-open-rc-2-30-2, Last Released v2.30.1 (well-formed). `pp_last_released_version pgai-agent-kanban` = v2.30.1; `semver_gt v2.30.2 v2.30.1` true. File mtime 04:42:04.827 matches the open-rc run. Pass.
- 5d: tree ships docker/debian/Dockerfile and docker/rocky9/Dockerfile; team/scripts/deep-verify.sh present. Ledger read via `deep_verify_ledger_read_latest pgai-agent-kanban` (rc=0): found=true, failed-to-build, v2.9.20, d37bb68, 2026-09-07T06:52:06+00:00. The red run's own bug is BUG-0167 (status done); the failing image dir docker/claude-abliterated is no longer in the tree. Not re-filed (Step 5d).
- 6.9: added lines contain no bug/task/RC/version citations and nothing secret-shaped. The changed shell files are sourced libs (no top-level `set -euo pipefail` expected); api-server.sh keeps its existing strict-mode header. Pass.
- 6.10: CM-015 (open, DONE) -> CODER-013, CODER-014 (DONE) -> TESTER-016 (this) -> CM-018 (release, WAITING). No cycles, all IDs exist, no sequence collisions. Pass.
- 6.13: the RC's only new test skip path is `narrate_binary_skip` (a guarded-binary skip, not a deferral). Repo-wide placeholder-shape hits are only historical runner logs echoing the literal `BUG-NNNN`. The unresolved BUG IDs a broad grep finds are fixture strings inside other tests (test_skip_bug_citation_scan.py, test_fixed_in_restamp.py, etc.), not skip annotations, and none is touched by this RC. The authoritative gate (lint_skip_bug_gate) runs in the runner pre-flight; see Step 5a. Pass.
- 6.14: the new lib stage is a sourced library, not an operator-run script (no `--help` owed). It has a header documenting its public entry point and dependencies. No duplicated lib implementation (moved verbatim, not copied: `grep` finds exactly one definition). Pass.
- 6.12 evidence: no git-boundary-guard.jsonl in any of this cycle's task folders (CM-015, PM-002, CODER-013, CODER-014, TESTER-016, CM-018). release-state.md last written at the open-rc moment. Nothing under the live install's scripts/ or pgai_agent_kanban/ was modified since RC open. The RC commits are exactly the two tickets' deliverables, made inside their task windows. Queue file mtimes line up with wake/dispatch events. This task's WAITING->BACKLOG promotion was recorded by the wake script.
- Step 5a unit: `team/scripts/run-unit-tests.sh --coverage` from the dev tree root (PGAI_DEV_TREE_PATH = worktree), started 05:18:57Z. **UNIT_EXIT=0.** Lint pre-flight OK (anti-patterns 0 findings across 744 files; lint_skip_bug_gate OK, 0 missing). Lane 1 parallel: 9631 passed, 41 skipped. Lane 2 serial: 5 passed. Findings file: 0 findings. Coverage: "coverage disabled by config ([testing] coverage)".
