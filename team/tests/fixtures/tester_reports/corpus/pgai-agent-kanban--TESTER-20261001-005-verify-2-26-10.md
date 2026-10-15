# Verification Report: rc/v2.26.10 (ai_rc/v2.26.10)

## Summary

This report verifies RC v2.26.10 (`ai_rc/v2.26.10` at `768abde216fa51cded01e93eff092554f09ef371`) against `requirements/v2.26.10-bugfix-bundle-20261001.md`. The bundle carries two bugs:

- **BUG-0481:** provenance hygiene-lint findings in `team/scripts/verify-blocked-task-guard.sh`.
- **BUG-0482:** the blocked-task guard verifier does not exercise the guard's Needs Human check.

Both are addressed and both of their explicit acceptance checklists are met. I verified this behaviorally, not just by reading the diff:

- **Needs Human mutation:** in a throwaway clone, replacing the guard's Needs Human check with `True` now makes the verifier exit 1 naming `AC-3 (scanner)`. The same mutation against the pre-RC verifier exits 0, which reproduces the original bug.
- **Unit test:** the new unit test fails against the pre-RC verifier and passes against the RC.
- **Provenance lint:** reports 0 findings. The two suppressions are hash-bound, and a planted site is still caught.
- **Syntax checks:** all 596 Python files parse and all 306 bash files pass `bash -n`.

Two findings keep this off PASS:

1. **The other half of the guard is still untested (BUG-0483).** The verifier still does not exercise the State-BLOCKED half of the guard's predicate. Its new header comment claims both halves are exercised.
2. **The integration runner exited 1 (BUG-0485).** It failed on one intermittent, pre-existing timing race in a lock test the RC does not touch. That test passes 5/5 in isolation on both the RC and `ai_main`.

The unit gated runner exited 0.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Findings

### Pre-flight (Steps 1-3)

- **Task state:** `status.md` read WORKING (stamped by the wake script) with an empty `artifacts/`, so this was a fresh start. No prior report existed, and nothing was reused.
- **Step 2, merge ancestry:** 514 DONE tasks name a feature branch. None of those branches still exists locally, so Check 1 and Check 2 pass trivially. The cycle's two CODER branches were merged `--no-ff` (a4e0cc38, 768abde2) and deleted. **pass**
- **Step 3, worktree:** `git rev-parse HEAD` = `git rev-parse ai_rc/v2.26.10` = 768abde2. `git status --porcelain` is empty and `git stash list` is empty. **pass**
- **Step 4, diff vs `ai_main` (e5a5826d):** 3 files changed. `team/scripts/hygiene-lint-allowlist.json` (+14), `team/scripts/verify-blocked-task-guard.sh` (+31/-19), `team/tests/unit/test_verify_blocked_task_guard.py` (+43/-6). The only deliverables come from CODER-20261001-002 (6704d1dd) and CODER-20261001-003 (970abff5).

### Bundle acceptance criteria (requirements doc)

- **"Each bundled item is addressed":** pass for both items, detailed below. The BUG-0482 caveat is Finding 1.
- **"All Python files compile cleanly":** `ast.parse` over all 596 tracked `*.py` files, 0 failures. **pass**
- **"All bash files pass syntax check":** `bash -n` over all 306 tracked `*.sh` files plus files with a bash shebang, 0 failures. **pass**

### BUG-0482: guard verifier exercises the Needs Human check

Every verifier run below used a sandbox root (`env -u PGAI_TASKS_DIR -u PGAI_PROJECT_ROOT`, with `KANBAN_ROOT`, `PGAI_AGENT_KANBAN_ROOT_PATH` and `PGAI_AGENT_KANBAN_TEMP_DIR` pointed at `mktemp -d` under `$PGAI_AGENT_KANBAN_TEMP_DIR`). Every mutation ran in a `git clone --no-hardlinks` clone, never the worktree.

| Check | Command / mutation | Result | Category |
|---|---|---|---|
| Unmodified RC tree | verifier on 768abde2 | exit 0, `RESULTS: 20 passed, 0 failed`, scanner counts 1/0/0 | pass |
| Needs Human check replaced by `True` (the bug's mutation) | `sed 's/nh_m.group(2).strip().lower() == "yes"/True/'` on the RC clone | exit 1, `RESULTS: 19 passed, 1 failed`, `FAIL: AC-3 (scanner)`, counts 1/1/0 | pass |
| Original failure mode | same mutation on an `ai_main` clone (pre-RC verifier) | exit 0, `20 passed, 0 failed`, so the bug is reproduced and the fix is real | pass |
| New unit test is discriminating | `test_verify_blocked_task_guard.py` (direct pytest, diagnostic only) vs the RC verifier, then vs the `ai_main` verifier | 4 passed / `test_verifier_fails_when_guard_ignores_needs_human` FAILED | pass |
| `bash -n` on verifier; `py_compile` on test | as stated | 0 / 0 | pass |
| Constraint: `wake_driver.sh` not modified | `git diff --quiet ai_main..rc -- team/scripts/lib/wake_driver.sh` | unchanged | pass |
| Constraint: header documents the changed AC-3 | read the diff | AC-1/AC-2/AC-3 bullets rewritten | pass-with-caveat (see Finding 1) |
| Optional AC-1/AC-2 mirror retirement | CODER Summary | kept, with the reason recorded (`test_cm_section_readers.py` depends on `count_blocked_nh`) | pass (optional per README) |

#### Finding 1: the guard verifier still does not exercise the State-BLOCKED half of the guard predicate

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (the same class of guard-test fidelity gap has come up in consecutive RCs: BUG-0480, BUG-0482, this one)
**Regression:** no
**Integrity:** none
**Filed As:** BUG-0483-guard-verifier-misses-state-check (Path C-A, filed as BUG: code defect / regression / test failure)
**Filed By:** TESTER (autonomous Path C) — the blocked-task guard verifier still passes when the guard stops checking that a task is BLOCKED, even though its header now says both halves of the guard's rule are tested.

The mutation: in an RC clone, `sed -i 's/state_m.group(2).strip().upper() == "BLOCKED"/True/' team/scripts/lib/wake_driver.sh`. Then the verifier in a sandbox root prints `driver scanner: blocked fixture count=1  blocked-no-human fixture count=0  resolved fixture count=0` and `RESULTS: 20 passed, 0 failed`, and exits **0**.

The three fixture states (BLOCKED/yes, BLOCKED/no, DONE/no) never include a non-BLOCKED task with Needs Human yes. So the State check is never the deciding factor.

This RC's new header comment says the runs are there "so both halves of the guard's predicate (State BLOCKED and Needs Human yes) are exercised". That is untrue for the State half. BUG-0482's `## Expected` asks that "the verifier should exercise both halves". BUG-0482's own `## Acceptance` checklist covers only the Needs Human half, and that checklist is fully met. So this finding is a partially met criterion plus an inaccurate claim in a deliverable. It does not mean the Needs Human fix failed. The shipped guard is correct, and nothing fails at runtime.

Severity: low (rubric: not exercised, nothing failing). This predates the RC as a coverage gap (`Regression: no`). The inaccurate claim is new text.

### BUG-0481: provenance hygiene findings cleared

| Check | Result | Category |
|---|---|---|
| `python3 team/scripts/lint_hygiene.py --classes provenance` on the RC | `OK — 0 findings`, exit 0 | pass |
| Full hygiene lint (all three classes) on the RC | `OK — 0 findings` | pass |
| Suppression is hash-bound, not blanket | in a clone, changed suppressed literal `...-001-...` to `...-003-...`: lint reports `verify-blocked-task-guard.sh:462 [task-ID] provenance reference 'CODER-20000101-003'` | pass |
| Lint class not weakened | planted `CODER-20990101-007-something` comment in a clone's `team/scripts/lib/semver.sh`: reported at `semver.sh:126`. `lint_hygiene.py` is not in the RC diff | pass |
| Suppressions carry specific justifications | 2 new sidecar entries (hashes d2aeda8248b5, db655affecfb), each with a fixture-specific justification tied to the scanner's canonical-id filter | pass |
| Allowlist JSON valid | `json.load` ok | pass |
| Verifier still green after the rewording | covered in the BUG-0482 table above (exit 0, 20/0) | pass |

Observation (not a finding): CODER-20261001-003's Summary says the prerequisite ticket introduced the `CODER-${SCAN_FIXTURE_DATE}-NNN` interpolation that let the ids step around the lint. Git shows that interpolation was already on `ai_main` (it shipped in v2.26.9, 6c8605ab). The BUG-0481 literals at lines 455/456 existed at its observed commit 297d905d. The CODER's action (literal ids plus deliberate sidecar suppression) is correct and allowed by its README. Only the history attribution in the status record is inaccurate.

### Step 5a / 6.6 / 6.7: gated suites

Both runners were run with `--coverage` from the worktree root. Output is at `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester/{unit,integration}.log`.

- **`UNIT_EXIT=0`:** `6237 passed, 43 skipped in 1193.30s`. "Unit tests PASSED". **pass**
- **`INTEGRATION_EXIT=1`:** `1 failed, 637 passed, 1 skipped in 1198.92s`. The lint pre-flight passed (0 findings). The failure is in pytest. **Finding 2.**

#### Finding 2: integration runner red on an intermittent lock-test timing race (pre-existing)

**Category:** bug (Step 5a category: pre-existing-bug)
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (an intermittently red gated runner can push any future RC off PASS without a real defect)
**Regression:** no
**Integrity:** none
**Filed As:** BUG-0485-chain-lock-test-holder-race (Path C-A)
**Filed By:** TESTER (autonomous Path C) — an integration test for the wake chain's lost-lock stop fails intermittently because it assumes the second chain is still holding the lock at the exact moment the first chain checks, which is a timing race, not a product defect.

- **The failure:** `FAILED tests/integration/test_chain_lock_reverification.py::test_reaped_chain_stops_before_dispatch_second_chain_reaches_the_task`, at `test_chain_lock_reverification.py:342`, `assert f"holder={_AGENT}" in chain_a_output`.
- **What chain A printed:** `error:   lock.provenance.reverify_lost` with `... it is now held by (no provenance recorded). Stopping this chain`.
- **What still passed:** every earlier assertion. Chain B finished A2, chain A exited 0, chain A never dispatched A2, and the `reverify_lost` narration is present. The safety behavior is correct. Only the holder name in the narration differs.
- **Why "(no provenance recorded)":** `pgai_lock_describe_provenance` prints it whenever the lock path is empty or absent. That is the case if chain B has not acquired the lock yet (the test unlinks it) or has already released it (`pgai_lock_release` removes the file) when chain A reverifies, after its fixed 2s pause.
- **Pre-existing:** the RC diff touches none of the test, `lock_provenance.sh`, or the wake chain. Run alone, the test passed 5/5 on the RC clone and 5/5 on the `ai_main` clone. Those direct pytest runs are diagnostic only, not PASS evidence. No open bug covered it; grepping `bugs/` turned up only BUG-0416, which is closed and unrelated.
- **Severity:** medium (rubric: incorrect, with a workaround: re-run). Not high: nothing that worked in v2.26.9 stops working here.

### Step 5b: coverage (informational only)

From the runners' JSON `totals` (field names checked against the generated files):

- **Unit:** line 77.93% (`percent_statements_covered`), branch 63.60% (`percent_branches_covered`).
- **Integration:** line 12.77%, branch 10.05%.

### Step 6.1: release-state sanity

`## Active RC v2.26.10` and `## Last Released v2.26.9` are well-formed. `pp_last_released_version pgai-agent-kanban` returns `v2.26.9`, and `semver_gt v2.26.10 v2.26.9` is true (shared helper). **pass**

### Step 6.2 / 6.3: structural and content review

All 3 changed files exist, and the verifier stays executable (mode 100755). I read the modified regions in context. `set -euo pipefail` is kept in the verifier (line 55), and `--help` works. The new test docstrings and the test name describe behavior, not provenance. **pass.** The header-comment inaccuracy is Finding 1.

### Step 6.5: guard failure modes

Covered above. The Needs Human guard fires (exit 1), the original failure mode reproduces on `ai_main`, and the State-half mutation is not caught (Finding 1).

### Step 6.8: synthetic release simulation

Skipped. The diff touches no release-lifecycle files.

### Step 6.9: constraint negative checks

There are no hardcoded secrets in the changed files. `wake_driver.sh` is unchanged, as the CODER-002 constraint requires. The only new task-ID-shaped literals are the two deliberately suppressed fixture ids. **pass**

### Step 6.10: prerequisite graph

The chain is PM-20261001-001, then CM-20261001-004 (open RC), CODER-20261001-002, CODER-20261001-003 (which also needs CM-004 and CODER-002), TESTER-20261001-005 (needs both CODERs), and CM-20261001-007 (release; needs both CODERs and TESTER, currently WAITING). There are no cycles, every ID exists, and the bookends are linked. Sequence 006 is unused, which fits `Human Approval Required: auto` (no approval task). **pass**

### Step 6.11: spot-check outside the spec

- **State drift:** `Active RC v2.26.10` matches the existing `ai_rc/v2.26.10` branch, and the release-state.md mtime (00:27:07) equals `RC Opened At`. No drift.
- **Untested new mechanism:** Finding 1 (State half).
- **Regressions of prior gaps:** the BUG-0482 mutation is now caught. No regression.

### Step 6.12: autonomous criterion

See `## Autonomous criterion` below.

### Step 6.13: skip-cites-real-bug gate

There are no placeholder shapes in tests. Six `BUG-NNNN` hits sit near "skip" text (`test_fixed_in_restamp.py`, `test_skip_bug_citation_scan.py`, one artifacts table). On reading, all are assertion strings or lint-fixture data, not skip annotations. Both runners' pre-flight (including `lint_skip_bug_gate.sh`) passed. **pass**

### Step 6.14: coding-standards spot-check

There is no new script, and the verifier's `--help` exists. No new hardcoded operational value or duplicated lib logic. The only identifier-shaped literals are the suppressed fixture ids. **pass**

### Test fidelity

The new test mutates a real copy of the shipped driver and runs the real verifier in a sandbox. There is no mock in the path, so fidelity is high. Its blind spot (the State half) is Finding 1.

## Lint Findings (non-blocking)

None. `findings-unit.json` (generated 2026-10-01T01:43:53Z) and `findings-integration.json` (generated 2026-10-01T02:04:28Z) both hold `"findings": []`.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=23d freshness=stale — the last recorded cold build failed (claude-abliterated FROM parse error and a rocky9 podman COPY step), and the bug that run filed, BUG-0167-cold-build-claude-abliterated-failure, is closed (done); the record is stale on both counts (23d exceeds the 7-day window, and v2.9.20 is not the v2.26.10 under verification), so nothing current is known about whether this tree's images build.

## Autonomous criterion

1. Tasks force-promoted WAITING to BACKLOG by hand? **NO.** TESTER's own status reads "Promoted from WAITING to BACKLOG by wake script". The dispatch ledgers show normal wake dispatch/return for PM, CM, CODER x2, TESTER.
2. `release-state.md` edited outside the CM scripts? **NO.** Its mtime 2026-10-01T00:27:07Z equals `RC Opened At` and falls inside CM-004's dispatch window (00:26:06 to 00:28:24).
3. Scripts patched mid-build? **NO.** The RC commits touch only this cycle's deliverables. No live-install files under `scripts/`, `roles/`, or `SOP-core.md` changed after RC open.
4. Queue files edited by hand? **NO.** No evidence. The cycle's markers (`[x]`, `[W]`, `[A]`) match each task's `## State`.
5. Remote git operation by a non-CM agent? **NO.** No `git-boundary-guard.jsonl` exists in any of this cycle's task `logs/`.

Autonomous operation criterion: Manual interventions required: none

## Caveats

- **Direct pytest:** used only diagnostically, on the guard test and the flaky lock test, never as PASS evidence. The suite verdicts are the gated runners' exit codes.
- **New operator bug during this run:** an operator filed `BUG-0484-lock-provenance-acquired-at` (a lock-provenance enhancement) at 02:03Z. It is unrelated to Finding 2 and not a pipeline intervention. Because of it, my second filing took ID 0485.
- **Artifacts not committed:** they live in the task folder only. The wake prompt for this run put the worktree in detached HEAD and forbade creating a feature branch, which overrides the role file's "commit artifacts on a feature branch" step.
- **Leftover scratch:** CODER-20261001-002 left a scratch clone at `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/mutation-clone`. It is under the temp convention and is not a finding. My clones and sandboxes are under `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester/`.
