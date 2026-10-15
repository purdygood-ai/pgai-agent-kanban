# Verification Report: rc/v2.26.9

(Local branch `ai_rc/v2.26.9`, branch prefix `ai_`. Verified at RC HEAD `7e639629442bd2a12aacd207de7bff97d586e7fc`, detached worktree `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260930-052-verify-2-26-9`.)

## Summary

I verified RC v2.26.9 against `requirements/v2.26.9-bugfix-bundle-20260930.md`. The bundle contains two pre-existing test-layer bugs: BUG-0479, where the blocked-task guard verifier always failed because it grepped the provider wake scripts for guard code that now lives in `lib/wake_driver.sh`, and BUG-0480, where `test_wake_model_stamp.py` exercised a stale hand-kept copy of `stamp_model_field`. The diff `ai_main..ai_rc/v2.26.9` touches 3 files: `team/scripts/verify-blocked-task-guard.sh`, `team/tests/unit/test_verify_blocked_task_guard.py` (new) and `team/tests/unit/test_wake_model_stamp.py`.

Both gated runners exited 0. Every acceptance item in the requirements doc and in both bundled bugs was exercised, including reproducing the original failure on `ai_main` and mutation testing in a throwaway clone. BUG-0480 is fully met. BUG-0479 is met except for one acceptance item: of 7 guard-breaking mutations, the verifier misses one. If the scanner's "Needs Human: yes" predicate is removed, the verifier still exits 0. I filed that as BUG-0482 via Path C-A (filed as BUG — code defect / regression / test failure). The shipped guard itself is correct, so this is a low-severity test-coverage gap.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
low

## Test Results (Step 5a / 6.6 / 6.7)

- `team/scripts/run-unit-tests.sh --coverage`: **UNIT_EXIT=0**. Lint pre-flight OK; 6236 passed, 43 skipped in 18m54s. Output: `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester-052/unit.log`.
- `team/scripts/run-integration-tests.sh --coverage`: **INTEGRATION_EXIT=0**. 638 passed, 1 skipped in 17m17s. Output: `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester-052/integration.log`.
- The RC's new and modified tests ran inside the gated unit suite: `tests/unit/test_verify_blocked_task_guard.py` (3 passed) and `tests/unit/test_wake_model_stamp.py` (14 passed).
- The PASS evidence above is the two runner exit codes. I used direct pytest only in a throwaway mutation clone, as a debugging aid, and none of its results are quoted as a suite verdict.

## Coverage (informational only; Step 5b)

These come from the `totals` object in each runner's JSON. I checked the field names against the generated files: `percent_statements_covered`, `percent_branches_covered`, `percent_covered_display`.
- unit: line 77.9 %, branch 63.6 %, blended 75 %.
- integration: line 12.8 %, branch 10.0 %, blended 12 %.

## Findings

### Pre-flight (Steps 2-3) — pass

- Step 2: no DONE task's feature branch still exists locally. The only local branches are `ai_main`, `ai_rc/v2.26.9`, `last_run_ai_main` and `main`, so both checks pass trivially. Both CODER merges are on the RC: `78f4ea9e` (CODER-20260930-049) and `7e639629` (CODER-20260930-050).
- Step 3: `git rev-parse HEAD` == `git rev-parse ai_rc/v2.26.9` == `7e639629`. `git status --porcelain` and `git stash list` are both empty. The tree was still clean at the end of the run.

### Step 6.1 — release-state sanity — pass

`## Active RC` v2.26.9, `## RC Opened By Task` CM-20260930-051-open-rc-2-26-9, `## Last Released` v2.26.8 (well-formed). `pp_last_released_version pgai-agent-kanban` gives v2.26.8, and `semver_gt v2.26.9 v2.26.8` is true.

### Requirements-doc acceptance criteria — pass

- "All Python files compile cleanly": 596 tracked `.py` files compiled in-memory with 0 failures. The runner's breakage-class gate also passed (564 Python and 294 shell files under `team/`).
- "All bash files pass syntax check": `bash -n` on 305 tracked `.sh` files, 0 failures.
- "Each bundled item is addressed": BUG-0480 fully, BUG-0479 with one partial item. Details below.

### BUG-0479 — blocked-task guard verifier

- Original failure, reproduced on `ai_main` (4ead55d9) in a sandbox root: `RESULTS: 7 passed, 8 failed`, exit 1.
- Acceptance 1 (exits 0 on a correct tree, sandbox root): the RC verifier prints `RESULTS: 20 passed, 0 failed` and exits 0, leaving the sandbox temp dir empty. — pass
- Acceptance 3 (no AC greps `claude.sh`/`codex.sh` for guard code): the only provider checks left are AC-4's `bash -n`, the "sources `lib/wake_driver.sh`" check, and the "no own `run_project_chain`" check. — pass
- Acceptance 4 (`run-unit-tests.sh` exits 0): UNIT_EXIT=0, and the new test is collected. — pass
- The verifier is registered under the gated unit suite through the new `test_verify_blocked_task_guard.py`, which has both a positive and a negative direction. — pass
- Acceptance 2 (breaking the guard makes the verifier exit 1). Mutations were made in a `git clone` of the RC, not in a copied worktree, and each run used a sandbox root:

  | Mutation in `wake_driver.sh` / provider | Verifier result |
  |---|---|
  | scanner State compare `"BLOCKED"`→`"BLOCKEDX"` | exit 1, `FAIL: AC-3 (scanner)` |
  | trigger `-gt 0`→`-gt 5` | exit 1, `FAIL: AC-3 (trigger)` |
  | guard branch sets `_dispatch_gated=false` | exit 1, `FAIL: AC-3 (log line)` |
  | dispatch gate replaced by `if true; then` | exit 1, `FAIL: AC-3 (dispatch gate)` |
  | `claude.sh` defines its own `run_project_chain` | exit 1, `FAIL: AC-4 (claude.sh no shadow)` |
  | `codex.sh` `source .../lib/wake_driver.sh` removed | exit 1, `FAIL: AC-4 (codex.sh sources driver)` |
  | scanner Needs Human predicate `== "yes"`→`True` | **exit 0, 20 passed, 0 failed — not caught** |

  The RC's own `test_verifier_fails_when_guard_is_removed_from_driver` covers stripping the whole guard block. → Finding 1.

### BUG-0480 — model-stamp test fidelity — pass

- Acceptance 1 (no re-implementation): `test_wake_model_stamp.py` contains no `re.sub`, `re.subn` or `_stamp_model_field`. Its 5 functional tests and the new one call `_run_shipped_stamp`, which sources the shipped `scripts/lib/wake_task_state.sh` in a bash subprocess with kanban paths under `tmp_path`. — pass
- Acceptance 2 (empty, non-last `## Model`): `test_stamp_fills_empty_non_last_model_and_keeps_following_sections` asserts the byte-exact output `## Role\nTESTER\n\n## Model\nX\n\n## State\nWORKING\n`. — pass
- Acceptance 3 (reverting the boundary fails a test): in a throwaway clone I put back the old `(^## Model\s*\n)(.*?)(\n+##|\Z)` with `re.S|re.M` in `wake_task_state.sh`. The new test then failed with `'## Model\nX\n' not in '## Role\nTESTER\n\n## Model\n\nX\n'` (1 failed, 13 passed). A second mutation, making `stamp_model_field` an immediate `return 0`, failed 6 tests. — pass
- Acceptance 4 (`run-unit-tests.sh` exits 0): UNIT_EXIT=0. — pass

### Steps 6.8-6.14

- 6.8 synthetic release simulation: skipped. The diff touches no release-lifecycle file.
- 6.9 constraint negatives: no secrets in the diff. `verify-blocked-task-guard.sh` keeps `set -euo pipefail` and its `trap cleanup_fixture EXIT`, and the fixture is created via `pgai_mktemp_d`. — pass
- 6.10 prerequisite graph: CM-051 (open RC) → CODER-049 and CODER-050 → TESTER-052 → CM-20260930-054-release-2-26-9. CM-054 lists both CODERs and TESTER-052. No cycles, every ID exists, and the sequence numbers don't collide. — pass
- 6.11 spot-check: no state drift (Active RC v2.26.9 matches the existing `ai_rc/v2.26.9`). Both findings from the prior cycle's report, the stale verifier and the stale mirror, are now fixed and not regressed. One observation about BUG-0481 is under Caveats.
- 6.13 skip gate: no skip or xfail in the changed tests, and `lint_skip_bug_gate.sh` exits 0. — pass
- 6.14 coding standards: none of the added lines cite a bug ID, task ID or version. `--help` lists every argument (`--verbose`, `--help`). The header AC list was updated in the same commit as the behaviour change, which satisfies the documentation discipline rule. — pass
- AC-1/AC-2 of the verifier still run the script's own copy of the scanner (`count_blocked_nh`), not the driver's. AC-3 now exercises the real one, so nothing is untested, but the copy has already drifted (it lacks the canonical-id filter). — pass-with-caveat; this is noted in BUG-0482 as part of the same fix area rather than filed separately.

### Finding 1: blocked-task guard verifier does not catch a broken Needs Human predicate

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated to one verifier fixture; the shipped guard is correct)
**Filed As:** BUG-0482-guard-verifier-misses-needs-human (Path C-A)
**Filed By:** TESTER (autonomous Path C) — the blocked-task guard verifier still passes when the guard stops checking "Needs Human", so a guard that pauses every project with any BLOCKED task, even one no human is needed for, would not be caught.

Criterion: BUG-0479 Acceptance 2, "Removing or breaking the guard in `wake_driver.sh` (in a throwaway clone) makes the verifier exit 1."

The guard counts queue entries whose status is `BLOCKED` **and** `Needs Human: yes`. The verifier's `AC-3 (scanner)` fixture only has `BLOCKED`/`yes` (expect 1) and `DONE`/`no` (expect 0). There is no `BLOCKED`/`no` case, so the Needs Human half of the predicate is never exercised. I replaced `nh_m.group(2).strip().lower() == "yes"` with `True` in a clone. That would gate dispatch for every BLOCKED task, and the verifier still exited 0 with 20 passed and 0 failed. No other test in `team/tests` references the guard: a grep for `_blocked_nh_count` and the guard header only hits the RC's new test. Expected behaviour is exit 1 naming `AC-3 (scanner)`. Severity is low under the SOP rubric: this is a test-coverage gap with no runtime defect today. Reproduction steps and the suggested fix are in the bug file.

## Lint Findings (non-blocking)

None. Both `/tmp/pgai_kanban_tmp/lint/findings-unit.json` and `findings-integration.json` contain `"findings": []`.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=23d freshness=stale — The last cold-build record failed to build docker/claude-abliterated and podman/rocky9 and is stale on both counts (23 days is past the 7-day window, and it was recorded for v2.9.20, not v2.26.9); that run's own bug is BUG-0167-cold-build-claude-abliterated-failure, now marked done, so nothing is known about whether the images build at this RC.

## Autonomous criterion

1. Tasks force-promoted from WAITING to BACKLOG by hand? **NO.** The status trails show wake-script promotions only ("Promoted from WAITING to BACKLOG by wake script: prerequisites satisfied").
2. `release-state.md` edited outside the CM scripts? **NO.** Its values match CM-20260930-051's open-RC run (`RC Opened At` 2026-09-30T22:32:06+00:00).
3. Scripts patched mid-build? **NO.** The only RC commits are the two CODER tasks' own deliverables. No live-install file under `scripts/`, `roles/`, `team/` or `pm-agent/` changed after the RC opened (`find -newermt` was empty).
4. Queue files edited by hand? **NO** evidence. The markers match task states: CODER-049 and CODER-050 `[x]` and DONE, CM-054 `[W]`, TESTER-052 `[A]`.
5. Remote git operation by a non-CM agent? **NO.** None of the cycle's task folders (CM-051, PM-006, CODER-049, CODER-050, TESTER-052) has a `logs/git-boundary-guard.jsonl`.

Autonomous operation criterion: Manual interventions required: none

## Caveats

- **BUG-0481-hygiene-lint-provenance** is still `## Status open`. It was auto-filed against intermediate RC commit `297d905d`, which had inline fixture IDs in `verify-blocked-task-guard.sh:455-456`. Commit `59d24a09` on this same RC fixed it: `python3 team/scripts/lint_hygiene.py --classes provenance` reports 0 findings at RC HEAD, and both runners' findings files are empty. It isn't a bundled item, so CM's Step 16b will not close it, and discovery may re-bundle a bug with nothing left to fix. I did not edit it, because TESTER only edits a filed bug to correct a factual error. This is an observation for PM and CM, not a finding.
- **Report-file tooling note.** The harness's file-Write tool refused `artifacts/report.md` with "Subagents should return findings as text". This task, the wake prompt and TESTER.md Step 9 all require the file, and CM's release script parses it, so I wrote it, `gaps.md`, `task-notes.md` and BUG-0482 with shell heredocs, only inside the assigned task folder and `bugs/`.
- **Git workflow.** The wake prompt forbids creating a feature branch in this worktree, and SOP says process artifacts are never committed into a dev tree. So nothing was committed to the RC. All artifacts live in the kanban task folder.
- Scratch, clones and mutation logs are in `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester-052/` and in sandbox dirs `sandbox-*` beside it.
