# Verification Report: rc/v2.32.8

## Summary

I verified RC ai_rc/v2.32.8 (058a0367) against `requirements/v2.32.8-bugfix-bundle-20261008.md`. The RC bundles one bug, BUG-0625: the three report readers disagreed about which headings start a finding. The work is CODER-20261008-031, two commits (16765591, merged `--no-ff` as 058a0367) across six files. The gated runners pass (`UNIT_EXIT=0`, `INTEGRATION_EXIT=0`). Every BUG-0625 and CODER-031 acceptance criterion is met: I checked them by running the gated and targeted runners, by an independent before/after matrix over eight heading shapes, and by a byte-identical comparison of reader output over all 215 TESTER reports in the install.

Two concerns are filed via Path C (autonomous follow-up filing, non-blocking), both pre-existing in code:

- A fourth finding-heading reader, the SHIP-KNOWN-ISSUE bug resolver, still uses its own level-3-only rule. The docstrings this RC added say every reader shares one rule, which is not true for it (BUG-0626, medium).
- A nested finding heading makes the stop-list splitter drop the outer finding (BUG-0627, low).

One quality-class lint finding (`inline_python`) is filed as BUG-0628 and does not affect the verdict.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Findings

### Finding 1: Gated suites pass

**Category:** pass

- `bash team/scripts/run-unit-tests.sh --coverage` from the worktree root: `UNIT_EXIT=0` (10652 passed, 41 skipped; lint pre-flight green, with one quality finding, see Lint Findings).
- `bash team/scripts/run-integration-tests.sh --coverage`: `INTEGRATION_EXIT=0` (959 passed, 1 skipped).
- Logs: `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester/unit-033.log`, `integration-033.log`; exit codes in `exits-033.txt`. Green-ledger records were written for commit 058a0367e3e1.

### Finding 2: The three named readers agree on all six acceptance heading shapes

**Category:** pass

- Criterion: BUG-0625 Acceptance 1 and CODER-031 AC1.
- `test_all_three_report_readers_agree_on_each_heading_shape` exists in `team/tests/unit/test_release_notes_bugs_skipped_render.py` with all six parameter IDs (`### Finding 1:`, `#### Finding 1:`, `##### Finding 1:`, `###Finding 1:`, `### Findings summary`, `## Findings`). It drives the real libs (`read_ship_policy_skipped_bug_pairs`, `read_ship_policy_finding_dimensions` via bash, and `resolve_stop_bug_files`), so nothing is mocked away.
- Independent sandbox matrix (ai_main export vs RC export, eight shapes: the six plus `####### Finding 1:` and `### Finding1:`):
  - On the RC, Bugs Skipped, the stop list and ship-policy dims agree on all eight. Only `###`/`####`/`##### Finding` open a block.
  - On ai_main they disagreed on four shapes (`###Finding`, `### Findings summary`, seven hashes, `Finding1`). The fix changes those, matching the bug's Actual table.

### Finding 3: Reproduction steps 3-5 now agree and match the documented rule

**Category:** pass

- Criterion: CODER-031 AC2.
- RC behaviour, with the `c_nospace.md` fixture (`###Finding 1: nospace`): Bugs Skipped prints nothing and dims prints `unknown unknown` with the "zero finding blocks" stderr line. Both readers skip it.
- `### Findings summary` variant: both skip it.
- This matches `finding_heading_level`'s docstring, which says both shapes deliberately open no block.

### Finding 4: Docstring agreement and the single-regex grep

**Category:** pass-with-caveat

- Criterion: CODER-031 AC3 and BUG-0625 Acceptance 2.
- The `skipped_bug_pairs.py` module docstring now names exactly what is shared: the Bugs Skipped table and the stop list share `finding_blocks`, and ship-policy shares `finding_heading_level`. That is true for the three readers it names.
- `grep` on `team/scripts/lib/cm/release_ship_policy_report.sh` finds no finding-heading regex. The only heading-shaped regex left is `finding_section`'s level-aware cut, `(?m)^#{1,%d}\s+\S` at line 349, which the CODER constraints permit.
- Caveat: two other docstrings and one role-file sentence added by this RC claim more than the code delivers. Finding 8 covers them.

### Finding 5: Output unchanged over every existing report

**Category:** pass

- Criterion: CODER-031 AC4 and Constraints 2, and BUG-0625 Acceptance 3.
- `bash team/scripts/run-unit-tests.sh --files team/tests/unit/test_release_notes_bugs_skipped_render.py team/tests/unit/test_stop_bug_files.py team/tests/unit/test_cm_ship_policy_bash.py team/tests/unit/test_cm_known_issue_carry_forward_bash.py` exits 0 (193 passed). It is a superset of BUG-0625's single-file command.
- Before (ai_main export) and after (RC export), over all 215 `TESTER-*/artifacts/report.md*` files under the kanban root, I captured stdout of six calls per report:
  - `read_ship_policy_skipped_bug_pairs`
  - `read_ship_policy_finding_dimensions`
  - `read_ship_policy_report_values`
  - stop list `systemic-risk-high`
  - stop list `fix-effort-large`
  - `read_ship_policy_known_issue_bug_files`
- Result: `diff -rq` is clean, so all outputs are byte-identical. This project's own `tasks/` holds 10 of those reports, and they are included.

### Finding 6: Compile and syntax checks, bundle-level criteria

**Category:** pass

- Criterion: CODER-031 AC5, and bundle AC2 and AC3.
- `python3 -m py_compile` succeeds on `stop_bug_files.py`, `skipped_bug_pairs.py` and both changed test files.
- `bash -n` exits 0 on `release_ship_policy_report.sh` and `release_known_issue.sh`.
- Whole tree: all 849 tracked `.py` files compile and all 334 tracked `.sh` files parse. The runner's breakage-class gate also reports 0 failures.

### Finding 7: Merge and constraints

**Category:** pass

- Criterion: CODER-031 AC6 and Constraints.
- Merge: 058a0367 has parents 470cfcf7 (ai_main) and 16765591 (feature), a `--no-ff` merge.
- Live-runtime import rule: the heredoc imports `finding_heading_level` under `PYTHONPATH="$(pp_pythonpath)"`. The diff adds no `sys.path` or `PYTHONPATH` edits and no dev-tree fallback. Dims mode now carries the same import dependency that values mode already had, and the Step 4d caller is unchanged.
- `finding_section` and `field_value` are unchanged.
- Naming rule: no bug, task or version tokens in added lines. Fixtures use the placeholders `rc/v9.9.9`, `BUG-0003` and `BUG-0001`.
- Documentation Discipline: the header comments and docstrings in all three changed modules, and `team/roles/TESTER.md`, change in the same commit.

### Finding 8: The SHIP-KNOWN-ISSUE resolver keeps its own finding-heading rule, so new "every reader" docstrings are untrue

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (the same reader-drift class has recurred across consecutive RCs)
**Regression:** no
**Integrity:** none
**Filed As:** BUG-0626 (Path C-A, filed as BUG: code defect)

- Traces to: CODER-031 Goal, "Leave one rule in the tree and make all three readers use it", and BUG-0625 Expected (one heading rule; the docstrings true).
- Location: `team/scripts/lib/cm/release_known_issue.sh` around line 160. `read_ship_policy_known_issue_bug_files` uses `(?im)^###\s*Finding\b.*$`, which matches level 3 only, and a level-2-only section cut.
- Effect: a `#### Finding` high-risk finding with Regression no, Integrity none and Fix Effort small makes dims read `no none`, and `cm_ship_policy done high ship-with-concerns no yes no none` returns `SHIP-KNOWN-ISSUE`. This resolver then returns 0 bug files, while `stop_bug_files` returns 1. So a known-issue ship names no bug in Known Issues and stamps no carry-forward marker. I reproduced this verbatim from the filed bug's steps. One real install report (TESTER-028, v2.32.7) already uses `#### Finding` headings.
- New text that overclaims:
  - `stop_bug_files.py` module docstring: "every reader of a TESTER report's findings uses it".
  - `release_ship_policy_report.sh` Dependencies: "so no report reader can disagree".
  - `team/roles/TESTER.md` Step 9: "shares with the release notes' Bugs Skipped and Known Issues readers".
- Pre-existing in code (ai_main behaves identically). The CODER left the resolver alone because its README scoped the file read-only, and flagged it in their Summary. Severity medium: incorrect on a real shape, with a workaround (level-3 headings).

### Finding 9: A nested finding heading drops the outer finding in the stop-list splitter

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated to one function; no install report uses the shape)
**Regression:** no
**Integrity:** none
**Filed As:** BUG-0627 (Path C-A, filed as BUG: code defect)

- Traces to: BUG-0625 Expected, "any report is split into the same blocks by all three", and Step 6.11 (an untested mechanism).
- Location: `team/pgai_agent_kanban/cm/stop_bug_files.py` `finding_blocks`. When a deeper finding heading opens inside an open finding, `body` is reset without the outer block being appended.
- Effect: for `### Finding 1` (high risk) followed by `#### Finding 2`, Bugs Skipped and the stop list miss the outer finding, while dims (`no none`) and the SHIP-KNOWN-ISSUE resolver read it. `finding_blocks` returns one block, the inner one.
- Pre-existing (ai_main identical). 0 of 215 install reports nest findings. Severity low. The CODER also flagged this in their Summary.

## Lint Findings (non-blocking)

- `inline_python`: `team/scripts/lib/cm/release_ship_policy_report.sh:256` has 2 blocks / 357 Python lines, above its allowlist entry of 2 / 350. It is reported in both `findings-unit.json` and `findings-integration.json` (one instance, one class). This RC introduced it, because the heredoc's `finding_blocks` grew. No open bug matched: BUG-0606 and BUG-0622 are `done` and concern other files. Filed BUG-0628 (Path C-A). The hygiene lint classes (provenance, version-named-test, comment-drift) report 0 findings.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=31d freshness=stale — The latest deep-verify record is 31 days old, outside the 7-day window, and is for v2.9.20 rather than v2.32.8. That run failed to build docker/claude-abliterated and podman/rocky9 and filed BUG-0167, now done and archived at archive/pgai-agent-kanban/bugs/BUG-0167-cold-build-claude-abliterated-failure.md. This project sets deep_verify = false, so nothing is known about whether this RC's images build.

## Autonomous criterion

1. Tasks force-promoted from WAITING to BACKLOG by hand: **NO**. This task's status records promotion "by wake script: prerequisites satisfied". The CODER-031 dispatch log shows a normal dispatch/return pair (22:03:41Z to 22:20:08Z).
2. `release-state.md` edited outside the CM scripts: **NO**. Its mtime of 22:02:03 equals `## RC Opened At` (cm-open-rc, CM-032). The `## HALT Event` section is written by `release_preflight.sh` by design.
3. Scripts patched mid-build: **NO**. The only commits on the RC are the CODER-031 work itself (16765591 at 22:13, 058a0367 at 22:17), inside the CODER dispatch window.
4. Queue files edited by hand: **NO**. All markers are canonical (`- [x]`, `- [W]`, `- [A]`), and mtimes line up with the agent events.
5. Remote git operation by a non-CM agent: **NO**. No `git-boundary-guard.jsonl` exists in any task folder.

Autonomous operation criterion: Manual interventions required: none

## Caveats

- Coverage: `coverage unavailable`. Both runners logged "coverage disabled by config ([testing] coverage)". The `coverage/*.json` files under the temp root date from 2026-10-04 and are not from this run. Coverage never enters the verdict.
- Pre-flight: of 28 DONE tasks with named feature branches, none of the branches still exist locally (the normal post-merge shape), so Checks 1 and 2 pass. Worktree HEAD == ai_rc/v2.32.8 == 058a0367; status and stash were empty before and after both suites.
- 6.1: `semver_gt v2.32.8 v2.32.7` is true, and `pp_last_released_version` returns v2.32.7.
- 6.8: skipped. The diff touches no release-lifecycle open/close script.
- 6.10: the prerequisite chain PM-005 → CM-032 → CODER-031 → TESTER-033 → CM-035 has no cycles and every ID exists.
- 6.13: the skip gate is clean. The only `BUG-NNNN` IDs near `skip` in tests are string fixtures inside the skip-lint's own tests.
- 6.14: no new scripts, hardcoded paths or tunables, or duplicated lib code in the diff, apart from the `finding_blocks` splitter overlap BUG-0628 suggests consolidating.
- Lane note: CODER-031 edited `team/roles/TESTER.md` (one sentence in Step 9), which is normally WRITER's file. The CODER disclosed this as a Documentation Discipline obligation. The edit is accurate except for the overclaim in Finding 8.
- Artifacts are not committed to any git branch. The wake prompt provisions a detached HEAD and forbids a feature branch, and the artifacts live in the task folder, not the dev tree.
- Prior-run files: none existed in `artifacts/` at start, so this was a fresh run.
