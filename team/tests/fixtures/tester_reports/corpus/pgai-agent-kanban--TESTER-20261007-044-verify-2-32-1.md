# Verification Report: rc/v2.32.1

## Summary

I verified `ai_rc/v2.32.1` at `9e611a6c` against `requirements/v2.32.1-bugfix-bundle-20261007.md`, a bug bundle of BUG-0597 (the post-release archive was skipped under CM's own wake chain lock) plus hygiene ride-alongs BUG-0598 (stale ICD), BUG-0599 (sweep flagged comment lines), BUG-0600 (the prereq gate re-sourced `pp_run_ops`) and BUG-0601 (the halt-after fixture lacked the archive package). I compared it with `ai_main` at `ac263b41`. Pre-flight passed. Every bundled item meets its own `## Acceptance` list, and I checked each one behaviorally, reproducing the original failure on an `ai_main` export and showing the fix on the RC. The requirements' compile and bash-syntax criteria pass across the tree. Both gated runners exited 0 (`UNIT_EXIT=0`, `INTEGRATION_EXIT=0`) with zero lint findings. No bug, gap or stale assertion was found, and the autonomous criterion shows no manual intervention.

## Recommendation
PASS

## Systemic Risk
low

## Findings

### Pre-flight (Steps 2 and 3): pass
- Worktree HEAD `9e611a6c` equals `ai_rc/v2.32.1`. `git status --porcelain` is empty and `git stash list` is empty.
- 665 DONE task folders name a feature branch. None of those branches still exists locally, so Checks 1 and 2 pass trivially, which is the normal post-merge shape. The four prerequisite tasks (039, 040, 041, 042) are DONE, and each `--no-ff` merge on the RC contributes 1 or 2 unique commits.

### Step 4: diff read
29 files changed (+4249/-163). Most of the line count is the new `docs/api/baselines/icd-v1.17.1.json` (3536 lines).

### Bundled item results

- **BUG-0597 (post-release archive skipped in wake): pass.** I built an independent sandbox (an `ArchiveSandbox` root, with `scripts/` and the package symlinked to the tree under test) and drove the real `_cm_release_archive_finished_items` in four shapes:
  - Parent bash holds `repo-wake-demo.lock` via `pgai_lock_acquire ... $$` and runs Step 16c in a child: `outcome=archived`, 5 bugs moved plus ledger, the chain's lock record unchanged afterwards (`pgai_lock_still_held` true).
  - Lock held by a sibling process: `outcome=skipped-lock-busy`, dual-channel `archive.repo_lock_busy` naming the holder pid, 0 files moved.
  - No lock: `archived`.
  - The same chain-held shape on an `ai_main` export: `skipped-lock-busy`, 0 moved, which reproduces the original failure.
  - Production-shape check: the live tester chain's lock record (`pid=3439727`) is in this agent's own process ancestry (`crond -> sh -> bash(3439727) -> bash -> claude -> bash`), and the RC's `pid_in_own_ancestry(3439727)` returns True. A sibling pid, pid 1, None, empty, negative and non-numeric inputs all return False.
  - Only Step 16c passes `--accept-chain-lock`; `scripts/archive.sh` and `POST /operations/archive` do not. The tree has no containerized agent dispatch (no pid-namespace hazard).
  - Docs: `docs/OPERATIONS.md` and `docs/cm-release-reference.md` no longer say the autonomous post-release archive is always skipped. The census text is updated and anchor `archive-run-lock-busy-01` remains CONVERT and indexed.
  - The new tests (`TestArchiveStepUnderWakeChainLock`) use the real topology: a parent bash with `pgai_lock_acquire`, a child running Step 16c, and a sibling holder for the negative case. Fidelity is good.
- **BUG-0598 (stale ICD): pass.**
  - `generate-icd.sh --output <scratch>` is byte-identical to the committed `docs/api/icd.json`, and `lint_icd_freshness.py` reports `ok`.
  - The ICD moved to 1.17.1, a description-only patch bump per `docs/operator-api.md` "Version semantics". The new `icd-v1.17.1.json` baseline is byte-identical to `icd.json`, and `icd-v1.17.0.json` is untouched.
  - The tests changed only to repoint their version-pinned literals to 1.17.1, keeping 1.17.0 as a frozen prior baseline, as the 1.16.1 precedent did. The worktree stayed clean after regeneration.
- **BUG-0599 (sweep flags archive comment lines): pass.** `python3 -m pgai_agent_kanban` no longer appears in `wake_task_id_gate.sh` or `release_archive.sh`. Both files still call the archive module only through `pp_run_ops` (`release_archive.sh:101`, `wake_task_id_gate.sh:187`).
- **BUG-0600 (prereq gate re-sources pp_run_ops): pass.**
  - Define a stub `pp_run_ops`, then source `wake_task_id_gate.sh`: on the RC the stub survives (`STUB`); on an `ai_main` export the real helper replaces it (`No module named x`).
  - With no prior definition, the gate self-loads `pp_run_ops`.
  - Three new tests cover stub retention, the lookup going through the stub, and the archived prerequisite resolving when `pp_run_ops.sh` was not pre-sourced.
- **BUG-0601 (halt-after fixture lacks archive package): pass.** All three named tests (`test_cm_release_spends_versioned_sentinel_when_shipped_version_matches_target`, `..._skips_past_target`, `test_cm_release_leaves_versioned_sentinel_armed_when_target_not_yet_shipped`) pass under `run-integration-tests.sh --files`. `_build_kanban_root` now copies the whole `pgai_agent_kanban` package minus `__pycache__` and `tests`, which satisfies the "copy the whole package" acceptance clause.

### Requirements acceptance criteria
- "All Python files compile cleanly": **pass**. All 837 tracked `*.py` files were compiled in memory: 0 failures.
- "All bash files pass syntax check": **pass**. `bash -n` over 335 bash files (`*.sh` plus first-line bash shebangs): 0 failures.

### Step 6.1 release-state.md: pass
`Active RC v2.32.1`, `Last Released v2.32.0` (well-formed). `pp_last_released_version` returns `v2.32.0`, and `semver_gt v2.32.1 v2.32.0` is true. The file's mtime equals `RC Opened At` (19:27:39Z).

### Step 6.8 synthetic release simulation
Not run separately: the diff touches `team/scripts/cm/release.sh`, but only in a header comment. `test_cm_release_synthetic.py` runs inside the gated integration suite.

### Step 6.10 prereq graph: pass
CM-043 open-rc -> CODER-039 -> {WRITER-040, CODER-041, CODER-042} -> TESTER-044 -> CM-046 release (WAITING). No cycles, every ID exists, sequence 045 is unused with no collision.

### Step 6.13 skip-cites-real-bug: pass
The RC adds no skip or xfail. The only skip annotations citing IDs (BUG-1234/1235/9872) are string literals inside the skip-lint's own test fixtures.

### Step 6.14 coding standards: pass
No bug, task or RC identifiers appear in added production lines. The only version-shaped addition is the ICD support-table row, which is versioned by design.

### Steps 5a, 6.6 and 6.7: gated suites: pass
- `team/scripts/run-unit-tests.sh --coverage` exited **0** (`UNIT_EXIT=0`): 10477 passed and 41 skipped in the parallel lane, plus 5 passed in the serial lane. The lint pre-flight passed. Log: `/tmp/pgai_kanban_tmp/tester/v2.32.1-unit.log`.
- `team/scripts/run-integration-tests.sh --coverage` exited **0** (`INTEGRATION_EXIT=0`): 959 passed, 1 skipped. The live-root sentinel check reported HALT / HALT-AFTER / DEFER-LOW-BUGS unchanged. Log: `/tmp/pgai_kanban_tmp/tester/v2.32.1-integration.log`.
- Confirmation runs, also through the gated runners (`--verbose --files`), used to show individual test names, not as PASS evidence:
  - Unit, 197 passed. Includes every test BUG-0598 and BUG-0600 named, the BUG-0599 sweep, and the new `pid_in_own_ancestry` and `accept_chain_lock` tests.
  - Integration, 20 passed. Includes `test_halt_after_rc_close_gate.py` (5), `test_release_archive_step.py` (9, with the two new `TestArchiveStepUnderWakeChainLock` tests) and both harness golden tests.

### Step 5b: coverage (informational)
Coverage is unavailable for this run. Both runners printed `coverage disabled by config ([testing] coverage)`, the documented behaviour inside an agent wake when `kanban.cfg [testing] coverage` is unset (default false). `--coverage` is honoured only when that key is true. The `coverage/unit.json` and `coverage/integration.json` files on disk are dated 2026-10-04, are not from this run, and were not used.

### Step 6.9: constraint negative checks: pass
No secrets or hardcoded credentials appear in the diff. The changed bash files are sourced libraries (no top-level `set -euo pipefail` expected), and Step 16c's subshell keeps its `set +e` and `PIPESTATUS` handling. The new ancestry walk is bounded (`_MAX_ANCESTRY_DEPTH = 1024`, `ps` fallback `timeout=5`) and fails closed: an unreadable link answers False, which skips the archive.

### Step 6.11: spot-checks outside the spec
- **State drift:** none. `release-state.md` agrees with the branches (`ai_rc/v2.32.1` exists, Active RC v2.32.1).
- **Untested new mechanisms:** `pid_in_own_ancestry` has dedicated unit tests (this process, a bash parent of a child, a child, a dead pid, non-pids). `accept_chain_lock` is covered for apply, restore, the legacy migration, and the unrelated-holder negative. The `ps` fallback (hosts without `/proc`) is not exercised on this Linux host. That path is small and fails closed, so it is recorded as an observation.
- **Regressions of prior gaps:** the BUG-0598, 0599, 0600 and 0601 test failures recorded in the v2.32.0 report are all green on this RC.

## Lint Findings (non-blocking)

None. Both `findings-unit.json` and `findings-integration.json` were written with 0 findings (snapshots: `/tmp/pgai_kanban_tmp/tester/v2.32.1-findings-{unit,integration}.json`).

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=30d freshness=stale — The only deep-verify record is a red run at v2.9.20, 30 days old (beyond the 7-day window) and not this RC's version, so nothing is known about whether this tree's images build; that run filed BUG-0167-cold-build-claude-abliterated-failure (now done), and the operator-only DEEP-VERIFY lane has not been re-run since.

## Autonomous criterion

1. Tasks force-promoted from WAITING to BACKLOG by hand? **NO.** This task's promotion was written by the wake script ("Promoted from WAITING to BACKLOG by wake script: prerequisites satisfied."), and no cycle task's status records a manual promotion.
2. `release-state.md` edited outside the CM scripts? **NO.** Its mtime (19:27:39Z) equals `RC Opened At` written by CM-20261007-043's `open-rc`.
3. Scripts patched mid-build? **NO.** No file under the live install's `scripts/`, `pgai_agent_kanban/`, `subagents/`, `roles/` or root `*.md` was modified after RC open. The RC's own `team/scripts/` commits are this cycle's CODER deliverables, merged `--no-ff`.
4. Queue files edited by hand? **NO** evidence. Queue mtimes track wake activity (for example `tester_backlog.md` at 20:27:11Z, the dispatch time of this task).
5. A non-CM agent invoked a remote git operation? **NO.** No `logs/git-boundary-guard.jsonl` exists in any of the cycle's task folders (PM-005, CM-043, CODER-039/041/042, WRITER-040, TESTER-044).

Autonomous operation criterion: Manual interventions required: none

## Caveats

- **Deep-verify:** the tree ships `docker/debian/Dockerfile` and `docker/rocky9/Dockerfile`. The lane is operator-only, and TESTER did not run it. Its last record is red and stale (see above). This is context, not a finding.
- **Observation (not a finding):** `generate_icd.py`'s `generate_icd.published_baseline_immutable` remedy always prints the next *minor* version (`_next_minor`), while `docs/operator-api.md` "Version semantics" calls for a *patch* bump on description-only changes. CODER-20261007-042 hit this, and took the patch bump correctly. The code predates this RC (last changed 2026-10-04), and the function's docstring says the operator may choose a different bump. It fits none of the 6.11 categories, so it is not filed.
- **Observation (not a finding):** BUG-0601 suggested checking `team/scripts/harness/helloworld-flow-check.sh` and `engine-behavior-baseline.sh`, which still copy only `pgai_agent_kanban/cm/*.py` into their throwaway roots. Running `changelog_writer.py` directly from such a bare layout raises its own "install the whole pgai_agent_kanban package" ImportError, identically on `ai_main` and the RC. However, both harnesses' integration tests pass and their goldens show a completed release, so no concrete failure was demonstrated. Not filed.
- **Observation:** the `ps -o ppid=` fallback in `ops/locks.py::_parent_pid_from_ps` (hosts without `/proc`) is untested on this host. It returns None on any error, which skips the archive (the pre-fix behaviour), so it cannot cause an unwanted move.
- **Git workflow:** TESTER.md "Git workflow" says to commit verification artifacts on a feature branch merged into the RC. This task's README names `## Feature Branch: none`, the dispatch prompt forbids creating one, and Step 9 says process artifacts are never committed into a dev tree. The artifacts therefore live only in the task folder, and nothing was committed. This is recorded under `## Instruction Conflicts` in status.md.
- The `.previous-RUN-N` rotation did not apply: `artifacts/` was empty at start. This is a fresh verification, and no prior verdict was consulted.
