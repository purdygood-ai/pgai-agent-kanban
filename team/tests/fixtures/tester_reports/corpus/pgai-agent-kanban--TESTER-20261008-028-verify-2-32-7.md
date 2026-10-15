# Verification Report: rc/v2.32.7

## Summary

Verified ai_rc/v2.32.7 (HEAD 770fda54, detached worktree) against `requirements/v2.32.7-bugfix-bundle-20261008.md`. The bundle contains BUG-0623 (the release-notes Bugs Skipped table drops findings written under level-4-or-deeper `Finding` headings) and BUG-0624 (a busy-chain PM wake integration test that failed intermittently on a 10-second wall-clock budget). The RC diff against ai_main touches 3 files: `team/pgai_agent_kanban/cm/skipped_bug_pairs.py`, `team/tests/integration/test_pm_wake_discovery_lock_scope.py` and `team/tests/unit/test_release_notes_bugs_skipped_render.py`. It consists of two CODER commits, each merged with `--no-ff`.

Both gated runners exited 0. All three requirements-doc acceptance criteria are met, and every acceptance bullet in both bug files is met. Both fixes were exercised behaviourally:
- **BUG-0623:** the original failure reproduces on ai_main and is fixed on the RC. Old and new output are identical across all 91 real reports in the install.
- **BUG-0624:** four mutants were run. The rewritten test catches a scan-step call that the ai_main test missed.

One low-severity bug was found and filed: two unusual heading shapes are still split differently by the Bugs Skipped reader and the ship-policy reader, and the new module docstring claims the two agree.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
low

## Findings

### Step 2 — Pre-flight: RC intact
**Category:** pass
33 DONE task folders name a feature branch. None of those branches exists locally any more, which is the normal post-merge shape, so Checks 1 and 2 pass trivially. No anomalies.

### Step 3 — Working tree
**Category:** pass
`git rev-parse HEAD` = `git rev-parse ai_rc/v2.32.7` = 770fda54eab9300ecc0d65b2f5edc6a18a4a1004. `git status --porcelain` is empty, and `git stash list` is empty. Both were re-checked at the end of verification.

### Step 5a / 6.6 / 6.7 — Gated suites
**Category:** pass
- `bash team/scripts/run-unit-tests.sh --coverage` gave **UNIT_EXIT=0**. Parallel lane: 10629 passed, 41 skipped. Serial lane: 5 passed. Lint pre-flight was clean.
- `bash team/scripts/run-integration-tests.sh --coverage` gave **INTEGRATION_EXIT=0**. 959 passed, 1 skipped. That count includes the rewritten `test_busy_chain_pm_wake_exits_fast_logs_busy_exit_first_and_never_holds_the_lock`, which passed under 3-worker xdist load.
- I ran the two suites one after the other (unit 21:03–21:22Z, integration 21:22–21:37Z). Logs are under `/tmp/pgai_kanban_tmp/tester/TESTER-20261008-028/`.

### Step 5b — Coverage (informational)
Coverage unavailable. Both runners printed "coverage disabled by config ([testing] coverage)". `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/{unit,integration}.json` date from 2026-10-04 and were not produced by this run, so I did not quote them.

### Requirements AC1 — Each bundled item addressed
**Category:** pass
Both items are addressed. The per-item findings follow.

#### Finding A: BUG-0623 — Bugs Skipped reads level-4+ findings
**Category:** pass
- **Original failure mode, on ai_main:** I used the bug's own reproduction report (a `#### Finding 1:` under a `### Step 6.4` heading, sandboxed). `read_ship_policy_skipped_bug_pairs` printed 0 rows.
- **On the RC:** the same report prints `bugs/BUG-0001-alpha.md<TAB>Predates this release ... deferred to a follow-up release.`, byte-identical to the level-3 variant.
- **Acceptance 1** (level-4 row equals level-3 row): pass, by the run above and by the new unit test `test_level_four_finding_under_step_heading_renders_its_level_three_row`.
- **Acceptance 2** (a `### Step` heading ends a preceding `#### Finding`, matching `stop_bug_files.finding_blocks`): pass. The new module calls `stop_bug_files.finding_blocks` directly, and `test_shallower_step_heading_ends_a_level_four_finding_block` covers the case.
- **Acceptance 3:** `bash team/scripts/run-unit-tests.sh --files team/tests/unit/test_release_notes_bugs_skipped_render.py team/tests/unit/test_cm_known_issue_carry_forward_bash.py` exit 0, 71 passed. For "existing level-3 reports render byte-identically", I ran the ai_main module and the RC module over all 91 `report.md*` files under `projects/*/tasks/*/artifacts/`. 50 produce rows, and 0 differ.
- **Additional level-5 probe:** a `#####` finding gives a row on the RC and none on ai_main.

#### Finding B: BUG-0624 — busy-chain PM wake proved by behaviour, not wall-clock time
**Category:** pass
- **Acceptance 1** (no elapsed-time assertion): pass. `time`, `_BUSY_WAKE_BUDGET_SECONDS` and `elapsed` are removed. "Fast" is now asserted as the exact record sequence `discovery_enter`, `chain_check wake=FREE probe=ACQUIRED`, `discovery_return status=idle`. The probe now wraps the three scan steps, the write-phase gate and both lock takers. A `probe_missing` tripwire fails the test if any wrapped function disappears.
- **Acceptance 2:** `bash team/scripts/run-integration-tests.sh --files team/tests/integration/test_pm_wake_discovery_lock_scope.py` exit 0, 2 passed.
- **Acceptance 3** (a deliberately slow busy-exit path still fails). I ran mutation tests in `git clone`s of the RC with sandboxed environments, editing `team/scripts/lib/discovery.sh` only:
  - Silent `discovery_step_bugs` before the busy check: **FAIL** (caught: the lock is held at `chain_check`).
  - Discovery's own repo-lock take before the busy check: **FAIL**.
  - Silent `discovery_step_bugs` call after the busy check that returns before the write gate: **FAIL** on the *new* exact-sequence assertion. The record was `[..., 'chain_check wake=FREE probe=ACQUIRED', 'discovery_step_bugs wake=FREE', 'discovery_return status=idle']`. **The ai_main version of the test PASSES this mutant**, so the rewrite strictly strengthens the guard.
  - A wrapped function (`_disc_take_own_write_lock`) renamed throughout production: **FAIL** on the `probe_missing` assertion in both tests.
  - Unmutated control: 2 passed.

### Requirements AC2 — All Python files compile cleanly
**Category:** pass
All 849 git-tracked `.py` files compiled with 0 failures.

### Requirements AC3 — All bash files pass syntax check
**Category:** pass
`bash -n` over 335 git-tracked `.sh` files and bash/sh-shebang files: 0 failures.

### Step 6.1 — release-state.md
**Category:** pass
`pp_last_released_version pgai-agent-kanban` = v2.32.6, and `semver_gt v2.32.7 v2.32.6` is true. `## Last Released` reads `v2.32.6`, which is well-formed. `## Active RC` reads `v2.32.7` and matches the existing `ai_rc/v2.32.7` branch. The file also carries a `## HALT Event` block from 2026-10-08T00:37Z. That block is written by `cm_halt` by design (`team/scripts/cm/release.sh:239`), so it is not drift.

### Step 6.8 — Synthetic release simulation
Skipped. The diff touches no release-lifecycle file (no `cm-release.sh`, `cm-open-rc.sh`, `cm-cancel-rc.sh`, `project_paths.sh`, or open/close-cycle script under `team/scripts/`).

### Step 6.9 — Constraint negative checks
**Category:** pass
The added lines contain no secrets and no bare `/tmp` paths. The new tests use `tmp_path`, and the bash in the probe is test-fixture-only and appended to a fixture copy.

### Step 6.10 — Prerequisite graph
**Category:** pass
The graph is acyclic and every task ID exists:
- CM-20261008-027-open-rc-2-32-7 (DONE) comes first.
- CODER-025 and CODER-026 each depend on CM-027.
- TESTER-028 depends on CODER-025 and CODER-026.
- CM-20261008-030-release-2-32-7 (WAITING) depends on CODER-025, CODER-026 and TESTER-028.

Sequence numbers do not collide. Queue markers match task states.

### Step 6.11 — Spot-check outside the spec

#### Finding 1: Report readers still split two heading shapes differently; new docstring overclaims agreement
**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (no real report uses either shape; isolated to corner-case heading parsing)
**Regression:** yes
**Integrity:** none
**Filed As:** BUG-0625 (Path C-A, filed as BUG — code defect)

**Filed By:** TESTER (autonomous Path C) — the Bugs Skipped reader and the ship-policy reader still disagree on which headings start a finding for two unusual heading shapes, and the module docstring says they agree.

The RC routes `skipped_bug_pairs` through `stop_bug_files.finding_blocks`, which matches headings with `^(#{1,6})\s+…` and `startswith("finding")`. The ship-policy parser (`release_ship_policy_report.sh` heredoc) uses its own `(?im)^(#{3,})\s*Finding\b`. I measured both on the RC in a sandbox:
- `###Finding 1:` (no space after the hashes): ai_main Bugs Skipped gives 1 row. RC Bugs Skipped gives **0 rows**. Ship-policy dims read the block (`no none`). This is a narrow regression of exactly BUG-0623's symptom class.
- `### Findings summary`: RC Bugs Skipped gives 1 row. Ship-policy reports "zero finding blocks … unknown unknown".

The new docstring says the shared splitter makes "the Bugs Skipped table, the Known Issues stop list and the ship-policy matrix agree". The ship-policy matrix does not use that splitter. Severity is low: none of the 91 real reports uses either shape, and the report template uses `### Finding N:`. Traces to: BUG-0623 Symptom ("one report is read three ways") and Expected (one rule for all readers).

### Step 6.12 — Autonomous operation
**Category:** pass
See "Autonomous criterion" below.

### Step 6.13 — Skip-cites-real-bug gate
**Category:** pass
There are no `BUG-SKIP` placeholders. The only `BUG-NNNN` ids that appear next to `skip` are string-literal fixtures inside lint tests (`test_skip_bug_citation_scan.py`, `test_lint_named_file_mode.py`) and assertion strings, not real skip annotations. The runner's `lint_skip_bug_gate` passed in both suites.

### Step 6.14 — Coding-standards spot-check
**Category:** pass
- No new scripts.
- The added test fixtures use `BUG-0001`/`0002`/`0003` as example *values*, which is the sanctioned exception.
- No provenance ids appear in names, comments or docstrings.
- The RC removes a duplicated block reader rather than adding one.

## Lint Findings (non-blocking)

None. Both `${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-unit.json` (generated 21:22:59Z) and `findings-integration.json` (generated 21:37:20Z) contain `"findings": []`.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=31d freshness=stale — The latest deep-verify record is 31 days old, outside the 7-day window, and is for v2.9.20 rather than v2.32.7. That run failed to build docker/claude-abliterated and podman/rocky9, and it filed BUG-0167 (now done, archived at archive/pgai-agent-kanban/bugs/BUG-0167-cold-build-claude-abliterated-failure.md). This project sets deep_verify = false, so nothing is known about whether this RC's images build.

## Autonomous criterion

1. Tasks force-promoted from WAITING to BACKLOG by hand: **NO**. This task's status records promotion "by wake script: prerequisites satisfied". The CODER-025, CODER-026 and CM-027 dispatch logs show wake-driver dispatch/return pairs (exit 0) and contain no manual-promotion traces.
2. `release-state.md` edited outside the CM scripts: **NO**. `## RC Opened By Task` reads CM-20261008-027-open-rc-2-32-7, which matches that task.
3. Scripts patched mid-build: **NO**. The only commits on the RC are the two CODER deliverables (20:42Z and 20:53Z) and their merges, all inside the 20:38Z coder batch. None touches `team/scripts/`.
4. Queue files hand-edited: **NO**. Markers are consistent with task states (`[x]` for 025, 026 and 027; `[A]` for 028; `[W]` for 030).
5. Remote git operation by a non-CM agent: **NO**. No `git-boundary-guard.jsonl` exists in any task folder created this cycle.

Autonomous operation criterion: Manual interventions required: none

## Caveats

- **Test fidelity (non-concrete).** The BUG-0624 probe proves that no *step function*, write gate or lock taker ran after the busy check. A future change that inlined a scan helper (for example `_disc_find_unhandled_items`) directly into `discovery_run_pipeline` would not be recorded. The docstring claims "no scan step is called", which is accurate. I note this as a limit, not a defect.
- **Ship-policy parser heading rule.** The ship-policy parser's own rule predates this RC. BUG-0625 asks for the readers to be unified rather than for this RC to be reverted.
- **Reading this report.** The `### Step …` headings above are not finding blocks for the ship-policy parser. Only `#### Finding …` headings open blocks.
- **Report-writing note.** I wrote this report through a shell heredoc because the agent harness's Write tool refused the file name. The content is unaffected.
