# Verification Report: rc/v2.26.8

## Summary

I verified ai_rc/v2.26.8 (commit b006be1e) against `requirements/v2.26.8-bugfix-bundle-20260930.md`. The bundle covers BUG-0475, BUG-0476, BUG-0477 and BUG-0478. All four concern markdown section readers and writers that used the old regex shape `(^## H\s*\n)(.*?)(\n+##|\Z)` with `re.S`, which lets an empty section body swallow the next section. For each bundled bug I replayed its reproduction on ai_main (c76f4a60, v2.26.7), where the failure must appear, and on the RC, where it must not. I ran each bug's acceptance greps, checked that populated values still come out right, and ran a mutation test to prove the census test now covers the code that ships. Both gated runners exited 0. All four bundled items are fixed on the RC. I filed two pre-existing test/verifier defects via Path C (autonomous follow-up filing — non-blocking). Both are outside the bundle's named sites and neither was introduced by this RC.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
low

## Test Results (Step 5a / 6.6 / 6.7)

- `team/scripts/run-unit-tests.sh --coverage`: **UNIT_EXIT=0** (lint pre-flight OK; 6232 passed, 43 skipped, 19m53s).
- `team/scripts/run-integration-tests.sh --coverage`: **INTEGRATION_EXIT=0** (638 passed, 1 skipped, 16m06s).
- Captured output: `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester-046/{unit,integ}.log`.
- The PASS evidence here is the two runner exit codes. Direct pytest was used only as a debugging aid in the BUG-0478 mutation test, and none of its results are quoted as a suite verdict.

## Coverage (informational only; Step 5b)

Figures are from the `totals` object in each runner's JSON (field names checked against the generated file):
- unit: line 77.9 % (`percent_statements_covered`), branch 63.6 % (`percent_branches_covered`); blended 75 %.
- integration: line 12.8 %, branch 10.0 %; blended 12 %.

## Findings

### Pre-flight (Steps 1-3) — pass

- Step 1: status read WORKING, but artifacts/ was empty and there was no progress log, so there was nothing to resume and I treated this as a fresh start. No prior `report.md`/`gaps.md` existed.
- Step 2: I scanned 510 DONE task folders that name a feature branch. None of those branches still exists locally, so Checks 1 and 2 pass trivially (the normal post-merge shape). The five CODER tasks (040-044) show as `--no-ff` merge commits on the RC.
- Step 3: HEAD b006be1e == `git rev-parse ai_rc/v2.26.8`; `git status --porcelain` is empty; `git stash list` is empty.

### 6.1 release-state.md — pass

Active RC v2.26.8, Last Released v2.26.7 (well-formed). `pp_last_released_version pgai-agent-kanban` = v2.26.7, and `semver_gt v2.26.8 v2.26.7` is true.

### Bundle acceptance criteria (requirements doc) — pass

- "Each bundled item is addressed": pass. See the four per-item findings below.
- "All Python files compile cleanly": pass. `compile()` over all 595 tracked `*.py` files: 0 failures.
- "All bash files pass syntax check": pass. `bash -n` over all 305 tracked `*.sh` files: 0 failures.

### BUG-0475 (transient requeue writer) — pass

I extracted the requeue heredoc from `check-transient-api-error.sh` at each ref and ran it on the bug's fixture plus variants, in a sandbox under the kanban temp root.
- Empty, non-last `## Labels`: ai_main drops `## Blockers`. The RC keeps all six sections, with Labels = `TRANSIENT` and Blockers = `none`.
- Empty `## Summary` / `## Blockers` / `## State`: ai_main deletes the following section each time. The RC removes nothing.
- Populated `EXISTING-LABEL`: ends as `EXISTING-LABEL TRANSIENT` on both refs.
- Second `replace_section` (the ceiling-escalation heredoc) with an empty `## Needs Human`: ai_main loses `## Blocked Reason` and re-appends it at the end. The RC fills `yes` in place and keeps section order.
- New tests in `test_overwatch_transient_api_error_bash.py` run the shipped heredoc.

### BUG-0476 (OVERWATCH / README / harness section writers) — pass-with-caveat

- `transient_retry_write`, `zero_progress_record` and `_update_readme_feature_branch` were each run on an empty, non-last target, with the shipped bash sourced or lifted in a sandbox. ai_main loses `## Zero Progress`, `## Needs Human` and `## Workflow Type` respectively. The RC loses nothing and fills the target in place.
- Acceptance grep `grep -rnE '\\s\*\\n\)\(\.\*\?\)' team/scripts/lib` prints nothing. The harness.sh stub writers were aligned too.
- **Caveat:** the criterion says "Populated targets are rewritten exactly as before", but RC output is not byte-identical to ai_main. ai_main added one blank line before the next heading on every populated rewrite, so the file grew each time. The RC re-emits the original separator and does not grow the file. That matches the bug's Expected clause ("never ... alters another section"), so I treat it as a fix, not a regression. Record contents are identical.
- New tests in `test_ops_section_writers.py` and `test_lib_section_writers_empty_body.py` run the shipped functions; I read both files and neither uses a hand-kept copy.

### BUG-0477 (CM and verifier section readers) — pass

- `open-doc.sh` Last Released, empty and non-last: ai_main reads `## RC Opened At\nnone`. The RC reads empty, which the shell maps to `none`. Populated `v1.2.3` and absent (`none`) behave the same on both refs.
- `finalize.sh read_md_field`, empty `## Artifact Name` followed by `## Output Formats`: ai_main reads `## Output Formats\nmarkdown`. The RC reads empty. Populated values and the stop at a `---` rule are unchanged.
- Target Version scans (`finalize.sh` heredoc and `promote_bundled_items.find_requirement_by_version`): given an empty `## Target Version` followed by a section whose body is `v9.9.9`, ai_main **false-matches** the doc. The RC does not match. Populated values match on both refs. The bug report assumed this misread had no practical effect; this case shows it did.
- `verify-blocked-task-guard.sh`: the State/Needs Human regexes are now byte-identical to `wake_driver.sh:539/544`. The script's AC-1/AC-2 checks pass.
- Acceptance grep over `team/scripts/cm` and the verifier script prints nothing.

### BUG-0478 (census test mirror) — pass

`test_census_closure.py` now lifts the census heredoc out of `close_intake_on_finalize_report` at test time. Mutation test in a throwaway `git clone` of the RC: I reverted the shipped `## Inputs` reader at `wake_lifecycle.sh:601` to the old shape (still exposing group 2), and exactly one test went red, `test_census_empty_inputs_does_not_count_task_citing_path_in_later_section`. With the shipped code restored, all 28 tests are green. The tests now fail when the shipped reader regresses.

### 6.8 Synthetic release simulation — pass

The diff touches `team/scripts/cm/`, so this step applies. `tests/integration/test_cm_release_synthetic.py` ran inside the green integration runner, with all 18 cases passing.

### 6.9 Constraint negative checks — pass

`set -euo pipefail` is still present in every modified top-level bash script. The new regexes are linear (each body iteration starts with `\n`), so they carry no catastrophic-backtracking risk. No secrets were introduced.

### 6.10 Prereq graph — pass

CM-045 (open-rc) → CODER-040/041/043/044 → CODER-042 (after 040 and 041) → TESTER-046 → CM-048 (release; prereqs are 040-044 plus 046). No cycles, and every referenced ID exists. Sequence number 047 is not used by any task in this project, which is a gap in numbering but not a collision.

### 6.13 Skip gate — pass

The RC adds no skip or xfail annotations, and no `BUG-SKIP` placeholders appear.

### 6.14 Coding standards — pass

No bug, task or version identifiers were added to shipped code or comments. The `BUG-0001` strings in new tests are example values in fixtures, which the naming rule allows.

#### Finding 1: verify-blocked-task-guard.sh always fails its AC-3/AC-4 checks (pre-existing)

**Category:** bug (pre-existing-bug)
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated verification script; not wired into the gated suites)
**Filed As:** BUG-0479-blocked-guard-verifier-stale-checks (Path C-A — filed as BUG: code defect / regression / test failure)
**Regression:** no
**Integrity:** none
**Filed By:** TESTER (autonomous Path C) — the blocked-task guard verifier script always reports 8 failed checks, because it still searches the two provider wake scripts for guard code that has since moved into the shared wake driver library.

I ran the script in a sandbox root. It exits 1 with `RESULTS: 7 passed, 8 failed`. All eight failures are AC-3/AC-4 greps of `team/scripts/wake/claude.sh` and `codex.sh` for guard code that now lives in `team/scripts/lib/wake_driver.sh`. A fresh clone of ai_main produces the same eight failures, so this RC did not introduce them. CODER-20260930-043 recorded it as out of scope and handed it to TESTER to file. It touches a file this RC modified, but the failing checks are not the lines the RC changed. Severity is medium under SOP "Rating a Finding": the defect is real in a shipped artefact but nothing exercises it.

#### Finding 2: test_wake_model_stamp.py tests a stale hand-kept copy of stamp_model_field (pre-existing)

**Category:** bug (pre-existing-bug; Step 6.11 spot-check — same class as a prior gap)
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (the shipped writer has direct coverage in test_wake_task_state_section_separator.py)
**Filed As:** BUG-0480-model-stamp-test-mirror-stale (Path C-A)
**Regression:** no
**Integrity:** none
**Filed By:** TESTER (autonomous Path C) — the model-stamp unit test checks its own hand-copied version of the status-file model writer, and that copy still uses the old section boundary that deletes the next section, so it no longer matches the shipped writer.

`_stamp_model_field` in the test (about lines 72-110) uses the old `(^## Model\s*\n)(.*?)(\n+##|\Z)` shape. The shipped writer at `wake_task_state.sh:278` uses the section-body boundary. On `## Role|TESTER||## Model||## State|WORKING`, the test copy deletes `## State` and the shipped regex keeps it. This is the same test-fidelity gap BUG-0478 fixed for the census, and the RC's sweep did not reach this file. It predates the RC. Severity is low.

## Lint Findings (non-blocking)

None. Both `/tmp/pgai_kanban_tmp/lint/findings-unit.json` (runner run-unit-tests.sh, 21:48:58Z) and `findings-integration.json` (22:05:33Z) have an empty `findings` array.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=23d freshness=stale — The only ledger record is a red cold build of v2.9.20 (docker/podman claude-abliterated FROM parse error and podman/rocky9 COPY --chmod), which that run filed as BUG-0167-cold-build-claude-abliterated-failure (now done); it is stale both because it is 23 days old (window 7) and because it is not v2.26.8, so nothing is known about whether this RC's images build.

## Autonomous criterion (Step 6.12)

1. Tasks force-promoted from WAITING to BACKLOG by hand? **NO.** This task's own promotion is logged as "by wake script: prerequisites satisfied", and no other RC task status shows a manual promotion.
2. release-state.md edited outside the CM scripts? **NO.** The file's mtime (18:47) matches the time recorded in `## RC Opened At` (2026-09-30T18:47:09+00:00), which CM-045's open-rc.sh wrote.
3. Scripts patched mid-build? **NO.** The only commits on ai_main..ai_rc/v2.26.8 are the five CODER commits and their `--no-ff` merges (19:17-21:24Z), which are the RC's own deliverables. There are no out-of-band tooling commits.
4. Queue files edited by hand? **NO.** I found no evidence of it; task summaries attribute queue markers to wake transitions.
5. Remote git operation by a non-CM agent? **NO.** No `git-boundary-guard.jsonl` exists in the logs/ of any RC task folder (PM-005, CM-045, CODER-040..044, TESTER-046).

Autonomous operation criterion: Manual interventions required: none

## Caveats

- Why SHIP-WITH-CONCERNS rather than PASS: every bundled fix verified clean and both runners are green. But Step 8 says any bug filed during verification sets at minimum SHIP-WITH-CONCERNS, and I filed two. Both are pre-existing (`Regression: no` in substance), low systemic risk, and neither touches code the RC changed. A reader weighing the release should see this as "RC is clean; two old test/verifier defects filed for the next loop."
- Observation (not a finding): `team/pgai_agent_kanban/task_status.py` module docstring (about line 24) still describes the section regex as `(^## <heading>\s*\n)(.*?)(\n+##|\Z)`, while the code (`_section_pattern`, about line 275) uses the new boundary. The RC did not touch this file. It is comment drift of the kind the hygiene lint's `comment-drift` class targets, which reported no findings this run.
- Observation: `team/pm-agent/lib/bug_scanner.py:131` `_RESOLVED_BY_RE` still has the old `\s*\n(.*?)` shape, but its `(?=^##|\Z)` lookahead under `re.M` makes an empty body read as empty (checked), so it behaves correctly. Readers in `wake_driver_task_setup.sh`, `wake_lifecycle.sh:747`, `wake_readme_meta.sh`, `retire-empty-bundles.sh`, `project_md.py` and `scan_attention.py` consume only the heading line's whitespace and also read empty correctly.
- Observation: CODER-20260930-043's `## Artifacts` lists paths inside this TESTER task's worktree (`/tmp/.../worktrees/TESTER-20260930-046-verify-2-26-8/...`), which will be torn down. This does not affect the RC.
- Git workflow: the wake prompt says to work in a detached-HEAD RC worktree and not to create a feature branch, and SOP forbids committing process artifacts into a dev tree. So this report, gaps.md and task-notes.md live only in the task folder; nothing was committed to the RC. See status.md Instruction Conflicts.
- Tooling note: the Write tool refused to create this file under a generic "subagents return findings as text" rule. This report is a mandatory deliverable (TESTER.md Step 9 MANDATORY DELIVERABLE OVERRIDE) and CM's release script parses it, so I wrote it with a shell heredoc.
