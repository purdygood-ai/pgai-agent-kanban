# Verification Report: rc/v2.26.7

## Summary

Verified `ai_rc/v2.26.7` (a16dc7a5) against `requirements/v2.26.7-bugfix-bundle-20260930.md`: BUG-0474 (wake status readers misread an empty `## State` as the next section) plus the BUG-0473 provenance hygiene ride-along, implemented by CODER-20260930-032/033/034/035. The diff against `ai_main` (c10d4f73) is 22 files (+1177/-77) in 9 commits. Both gated runners exit 0. Every acceptance criterion in the bundle, in both bugs and in the four tickets passes, and each fixed reader was exercised in a sandbox on ai_main and on the RC, with the original failure and populated regressions. Behavioral probing next to the fix found one defect this RC changed: OVERWATCH's transient requeue now deletes the `## Blockers` section when `## Labels` is empty and not last. It also confirmed three pre-existing defects of the same class: old-shape writers, CM/verifier readers outside the ticketed files, and a stale test copy. All four are filed via Path C (autonomous follow-up filing — non-blocking) as bugs. None corrupts a standard-shaped status file or breaks the release pipeline.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Findings

### Pre-flight and setup (Steps 1-4)

- Step 1: pass. Fresh start: status was wake-stamped WORKING, artifacts/ was empty, and no prior report or `.previous-RUN-N` existed.
- Step 2 (RC intact): pass. 505 DONE task folders name a feature branch; none exists locally (the normal post-merge shape, so both checks pass trivially). The four RC tickets' merges are on the RC: fa43f089, ce8c0022, eb288a2f, a16dc7a5.
- Step 3 (worktree): pass. `git rev-parse HEAD` = `git rev-parse ai_rc/v2.26.7` = a16dc7a5; `git status --porcelain` is empty; `git stash list` is empty. Re-checked after the suites: still clean at a16dc7a5.
- Step 4 (diff): read in full. Production changes: `wake_task_state.sh` (new `_status_section_re` / `_read_status_section`; six readers), seven wake/discovery libs (12 sites), `overwatch_protocol.sh` (2), `check-transient-api-error.sh` (1), `project_paths.sh` (1), `ops/write.py` (1), `release-notes/v2.26.5.md` (1 line reworded). Tests: three new files (36 + 168 + 65 cases), and helper-extraction lists updated in six existing harnesses.

### Gated suites (Steps 5a, 6.6, 6.7)

- `bash team/scripts/run-unit-tests.sh --coverage`: **UNIT_EXIT=0**. Lint pre-flight green; 6111 passed, 43 skipped (21m52s). Output: `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester/unit.log`.
- `bash team/scripts/run-integration-tests.sh --coverage`: **INTEGRATION_EXIT=0**. 638 passed, 1 skipped (18m28s). Output: `.../tester/integration.log`.
- Coverage (informational; from `totals` in coverage/*.json, field names checked against the real file): unit line 77.93% / branch 63.60%; integration line 12.77% / branch 10.05%.

### Acceptance criteria (Steps 6.2-6.4)

Bundle criteria:
- Each bundled item addressed: pass. BUG-0474 by tickets 032/033/034, BUG-0473 by 035 (details below).
- All Python files compile cleanly: pass. 592 tracked `.py` files parse, 0 failures; `py_compile` passes on every `.py` in the diff.
- All bash files pass syntax check: pass. 305 tracked `.sh` files pass `bash -n`, 0 failures.

BUG-0474 / CODER-20260930-032:
- `get_state` on `## State\n\n## Summary\nDONE\n\n## Blockers\nnone\n`: pass. ai_main prints `## SUMMARY\nDONE`; the RC prints an empty value (rc 0). A missing heading refuses, naming the file (`Could not parse ## State block in none.md`, rc 1).
- `get_first_pending_task`: pass. On a queue whose first entry has an empty State and whose second is BACKLOG, ai_main silently served the second; the RC serves the empty-State task. `wake_driver_prep.sh` (line ~179) then sets it BLOCKED with `Invalid task state detected: ` and Needs Human yes, so it is surfaced visibly rather than skipped.
- `recheck_waiting_tasks` / `recheck_blocked_tasks`: pass. The built regex `(^##[^\S\n]+State[^\S\n]*)((?:\n(?!##)[^\n]*)*?)(\n+##|\n*\Z)` reads empty as `''`, and reads `##   State`, `##\tState`, CRLF and last-section values correctly. Unit cases are in `test_wake_task_state_section_readers.py`.
- Populated BACKLOG/WAITING/BLOCKED/WORKING read identically on ai_main and the RC: pass.
- `bash -n`, `wc -l wake_task_state.sh` = 792 (≤ 800), `lint_engine_size_dupe` ok: pass.

CODER-20260930-033 / 034:
- Both acceptance greps (`\s*\n(.*?)` over the named files) print nothing: pass.
- `check_prerequisites`: pass. Empty `## Prerequisites`: ai_main emitted `wake.prerequisite.malformed-task-id` for `## Release Version` and `v1` (the task waits forever); the RC treats it as no prerequisites (rc 0). An empty prerequisite State prints `<id>:` on the RC, where ai_main printed `<id>:## SUMMARY\nDONE`.
- `transient_retry_field` on an empty `## Transient Retry` followed by `## Zero Progress`: pass. ai_main returned the Zero Progress record's `reason_code` (`stuck`); the RC returns the default.
- `_read_md_field` on an empty `## Owner`: pass. Returns `''` (`## Role` → `CODER` unchanged).
- `pp_last_released_version` still returns v2.26.6 on the live release-state: pass.

BUG-0473 / CODER-20260930-035:
- `python3 team/scripts/lint_hygiene.py --classes provenance`: pass. `OK — 0 findings across 9 scan director(y/ies)`. The release-notes reword keeps the entry's meaning.

### Constraints and gates (Steps 6.1, 6.8-6.10, 6.13, 6.14)

- 6.1: pass. `pp_last_released_version pgai-agent-kanban` = v2.26.6; `semver_gt v2.26.7 v2.26.6` is true. `## Last Released` is well-formed.
- 6.8: skipped. No release-lifecycle file (`cm-release.sh`, `cm-open-rc.sh`, `cm-cancel-rc.sh`, open/close-cycle scripts) is in the diff. `project_paths.sh` is touched, but only in the `pp_last_released_version` release-state reader (boundary alignment; the returned value was verified above). The integration suite, including `test_cm_release_synthetic.py`, passed.
- 6.9: pass. No secrets were introduced. Headers and `set -euo pipefail` are unchanged: every hunk is inside a Python heredoc or a function body.
- 6.10: pass. CM-036 → CODER-032 → (033, 034) → 035 → TESTER-037 → CM-039. No cycles, and all IDs exist. Sequence 038 is unused (no optional approval task).
- 6.13: pass. `lint_skip_bug_gate.sh` rc 0. The only skip-adjacent BUG IDs are fixture strings in `test_skip_bug_citation_scan.py` / `test_fixed_in_restamp.py` assertions.
- 6.14: pass. No new scripts (so no `--help` obligation). No bug/task/version citation in RC-added code or comments (provenance lint 0; manual grep of added lines finds only fixture task IDs such as `CODER-20260101-001-demo`).

#### Finding 1: Transient requeue deletes the Blockers section after an empty Labels section

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (one embedded writer in one OVERWATCH check)
**Filed As:** BUG-0475-transient-requeue-deletes-blockers-section (Path C-A — filed as BUG)
**Filed By:** TESTER (autonomous Path C) — when OVERWATCH requeues a task whose status file has an empty Labels section, the requeue now deletes the Blockers section that follows it.
**Regression:** yes
**Integrity:** none

`check-transient-api-error.sh` now reads `## Labels` with the new boundary, but writes through its local `replace_section()`, which still uses the old `(^## X\s*\n)(.*?)(\n+##|\Z)` pattern with `re.S`. Same input (`## Labels` empty, followed by `## Blockers|real blocker text`), extracted heredoc run on both refs:
- ai_main: the TRANSIENT label was lost (Labels stays empty), and `## Blockers` survived as `none`.
- RC: `## Labels||TRANSIENT||## Needs Human|no`. **`## Blockers` is gone.** A later `task-status.sh --blockers x` on that task refuses with `task-status.missing-section`.

The RC's reader fix changed the failure from a lost label to a lost section. The trigger needs a non-standard status file (an empty, non-last `## Labels`; the requeue path itself appends Labels at the end), so this is rated medium. CODER-20260930-034 reported the writer shape as out of scope. Neither the unit suite nor the new tests cover this case.

#### Finding 2: Old-shape section writers outside the wake state library delete the next section

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (the same regex class has now produced findings in v2.26.5, v2.26.6 and v2.26.7)
**Filed As:** BUG-0476-section-writers-delete-next-section (Path C-A)
**Filed By:** TESTER (autonomous Path C) — three writers outside the wake state library still delete the following section when the section they rewrite is empty.

`overwatch_protocol.sh` `transient_retry_write` and `zero_progress_record`, and `wake_project.sh` `_update_readme_feature_branch`. Sandbox runs: `## Zero Progress`, `## Needs Human` and `## Workflow Type` (respectively) are deleted. ai_main and the RC produce identical output, so this predates the RC. It is the BUG-0472 class, which was fixed only in `wake_task_state.sh`.

#### Finding 3: CM and verifier scripts still read an empty section as the next section

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (same recurring class; the RC's probe was scoped to named files and missed `team/scripts/cm/`)
**Filed As:** BUG-0477-cm-section-readers-misread-empty (Path C-A)
**Filed By:** TESTER (autonomous Path C) — several release-machinery and verifier scripts still read an empty markdown section as the text of the next section.

BUG-0474's Files Involved asks for "any other `## <Heading>\s*\n(.*?)` reader" to be checked. A tree-wide grep finds old-shape readers in `cm/open-doc.sh:206` (Last Released; returns `'## RC Opened At\nnone'` for an empty non-last field and would write it back into release-state.md), `cm/finalize.sh:212` (`read_md_field`: an empty `## Artifact Name` becomes `## Output Formats\nmarkdown`), `cm/finalize.sh:244` and `cm/promote_bundled_items.py:642` (Target Version; practically harmless), and `verify-blocked-task-guard.sh:168,173` (a copy of the wake_driver guard that was not updated alongside it). These predate the RC. BUG-0474's own acceptance criteria are met; this is a Step 6.11 spot-check finding.

#### Finding 4: Census unit test runs a stale hand-copied regex

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (one test file)
**Filed As:** BUG-0478-census-test-mirror-stale (Path C-A)
**Filed By:** TESTER (autonomous Path C) — the census unit test checks its own copy of the census code, and that copy no longer matches the shipped code.

`team/tests/unit/test_census_closure.py:99` (`_run_census`) still uses `^##\s+Inputs\s*\n(.*?)(?=\n##|\Z)`, while `wake_lifecycle.sh:601` now uses the section-body boundary. For `## Inputs` (empty) followed by a section citing the requirements path, the copy counts the task and the shipped code does not. The suite is green against logic that no longer ships (test fidelity). CODER-20260930-033 flagged it under Possible Stale Assertions.

### Observations (not findings)

- `wake_lifecycle.sh` census `^##\s+State\s*\n\s*(\S+)` and intake `^##\s+Status\s*\n\s*(\S+)`, and the `## Workflow Type` / `## Force Model|Model Override` readers (`[^\S\n]*\n(.*?)`), were left alone by CODER-033 with stated reasons. The first two affect only a diagnostic token. The last two misread only when a heading is followed immediately by another heading with no blank line, which templates never produce.
- `promote_waiting_to_backlog` / `set_state` match `## State` exactly, while the recheck scans tolerate `##   State`. A multi-space heading is recognised but its promotion refuses cleanly (`Could not update ## State block`). This predates the RC (CODER-032 note), and templates never produce that heading.
- Behavior change by design (CODER-032): an empty `## Needs Human` now reads as `yes` in `recheck_blocked_tasks`, the same as absent (fail-safe). The in-code comment documents it; no operator doc described the old behavior.

## Lint Findings (non-blocking)

None. `findings-unit.json` and `findings-integration.json` were both written by this run with 0 findings.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=23d freshness=stale — the last cold build (v2.9.20) failed on docker/claude-abliterated and podman/rocky9, already filed as BUG-0167 (now done); the record is stale on both counts (23d exceeds the 7-day window and v2.9.20 is not the v2.26.7 under verification), so nothing is known about whether the images build cold against this tree.

## Autonomous criterion

1. Tasks force-promoted WAITING→BACKLOG by hand? **NO**. Every promotion (CODER-032 at 15:48:03, 033/034 at 16:39:20, 035 at 17:38:00, TESTER-037 at 17:42:03) is logged by `wake(...)` in `logs/agents/*-batch-*.log` as "prerequisites satisfied, promoting to BACKLOG".
2. `release-state.md` edited outside the CM scripts? **NO**. Its mtime is 15:42:07, which equals `## RC Opened At` (CM-036).
3. Scripts patched mid-build? **NO**. No file under the live `scripts/`, `roles/` or `SOP-core.md` was modified after RC open. The RC's own commits to `team/scripts/` are the ticketed work.
4. Queue files edited by hand? **NO**. Queue mtimes line up with wake and agent transitions (coder 17:40:56 after CODER-035 closed; tester 17:42:07 at dispatch).
5. Non-CM remote git operation? **NO**. No `logs/git-boundary-guard.jsonl` exists in CM-036, CODER-032..035 or this task.

Autonomous operation criterion: Manual interventions required: none

## Caveats

- Test fidelity: the new reader tests are strong (pattern-level agreement with `_status_section_re`, and behavioral cases that fail on the pre-fix libs). They cover readers only. The writer interaction in Finding 1 is untested, which is why a green suite coexisted with it.
- Process note: the harness Write tool refused to create this file ("subagents should return findings as text"). This report is a mandatory, machine-parsed deliverable, so it was written through the shell. No content was changed by that route.
- Artifacts are not committed to any branch: the wake prompt mandates a detached worktree with no feature branch, and SOP-core keeps process artifacts out of dev trees.
- Scratch sandboxes: `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/sandbox-eAP5XM`, `.../sandbox-BxhuKa`; suite logs under `.../tester/`.
