# Verification Report: rc/v2.32.2

## Summary
I verified RC v2.32.2 (ai_rc/v2.32.2 at 549a7cd1) against requirements/v2.32.2-bugfix-bundle-20261007.md. The bundle has one item, BUG-0602: the generated release notes' Bug Fixes bullets quoted each bug's proposed `## Fix` text, and `e.g.` truncated them. Two tasks addressed it. CODER-20261007-047 rewrote `get_bug_substance` in the RELNOTES_PY heredoc so bullets come from the bug's `## Symptom` lead sentence. WRITER-20261007-048 updated the release-notes and bug templates to match. All acceptance criteria (the requirements doc's 3, CODER-047's 5, WRITER-048's 4) are met. Both gated runners exited 0: unit 10486+5 passed, 41 skipped; integration 959 passed, 1 skipped. I exercised the behavior with the shipped generator from ai_main and from the RC against the real BUG-0597..0602 files, synthetic guard cases, and the full 441-file bug corpus. The old generator reproduces BUG-0602 exactly; the RC generator fixes it. Spot-checks outside the spec found one small regression and one pre-existing gap, both low severity. The new template's guidance comment shows up as a bug's summary in `project-summary.sh`. Existing projects' seeded bug template is never refreshed, so the new guidance doesn't reach this project's filers. I also filed two quality-class lint findings (non-blocking) for release_notes.sh growth. Recommendation: SHIP-WITH-CONCERNS.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
low

## Gated Suites (Step 5a, 6.6, 6.7)

- `team/scripts/run-unit-tests.sh --coverage`: **UNIT_EXIT=0**. Parallel lane 10486 passed, 41 skipped. Serial lane 5 passed. Lint pre-flight passed. Output: /tmp/pgai_kanban_tmp/tester/TESTER-050/unit.log
- `team/scripts/run-integration-tests.sh --coverage`: **INTEGRATION_EXIT=0**. 959 passed, 1 skipped. Output: /tmp/pgai_kanban_tmp/tester/TESTER-050/integration.log
- Run window: 2026-10-07T21:44:18Z to 22:12:56Z, from the worktree at the RC commit. The green-ledger recorded exit 0 at 549a7cd13447 for both suites.
- PASS evidence is the two runner exit codes. The direct pytest run below is criterion evidence only.

## Coverage (Step 5b, informational)
Coverage unavailable for this run. Both runners printed "coverage disabled by config ([testing] coverage)", because kanban.cfg [testing] coverage is absent and defaults to false, which overrides --coverage. The coverage JSONs under /tmp/pgai_kanban_tmp/coverage/ are dated 2026-10-04, are not from this RC, and are not reported.

## Acceptance Criteria Results

Requirements doc:
- [pass] Each bundled item is addressed. BUG-0602's Expected ("Each Bug Fixes bullet states the defect that was fixed ... with no internal IDs") is met through CODER-047's criteria (below) and the behavioral runs. See the BUG-0602 bullet caveat under Caveats.
- [pass] All Python files compile. 837 tracked .py files compile with `compile()` (no bytecode written), 0 failures. The extracted RELNOTES_PY heredoc also compiles.
- [pass] All bash files pass syntax check. 335 tracked bash files (*.sh plus extensionless bash-shebang scripts) pass `bash -n`, 0 failures.

CODER-20261007-047:
- [pass] Symptom-sourced bullet; no Fix-unique text anywhere; Fix-only bug renders the generic fallback. Verified by tests and by my own runs on real and synthetic bugs.
- [pass] Abbreviations (`e.g.`, `i.e.`, `etc.`, plus `vs.`, `cf.`) do not end a sentence; parentheses and backticks stay balanced. Unit tests cover this (parametrized), and I reproduced it independently, including through CHANGELOG regeneration.
- [pass] Inline fixtures model the BUG-0597..0602 shapes (lead ':' plus fence/list, fence-first, IDs inside fences). The bullets are single-line, with no triple-backtick, no trailing ':', and zero internal IDs.
- [pass] Test files updated. content_described has no Fix-preferred assertion and its "Covers" list was rewritten. bug_fixes_section keys Bug Fixes and CHANGELOG `### Fixed` on Symptom text. Whole-file no-ID and lint_hygiene checks pass.
- [pass] `bash -n team/scripts/lib/cm/release_notes.sh` exit 0. The criterion's pytest command (from team/) gives 37 passed.
- Constraints: [pass] scope limited to the bug path (`_first_sentence`, `_substance_from_section`, `_parse_section`, `get_priority_substance` unchanged); ID scrub intact (PRIORITY-/task IDs scrubbed, KI-1.2.3.4 preserved, verified); fixtures built inline in tmp_path with no live-root reads (grep); no IDs or version citations in comments, docstrings, or test names (all ID-shaped hits in added lines are fixture values, the sanctioned exception).

WRITER-20261007-048:
- [pass] RELEASE-NOTES-TEMPLATE.md Bug Fixes guidance (top comment and Bugs Resolved intro) now says Symptom. The two remaining `Symptom/Fix` hits (lines 60, 160) are Known Issues guidance, which is out of scope under the ticket's constraint.
- [pass] BUG-TEMPLATE.md Symptom carries the opening-sentence guidance. `grep "^## "` heading text is identical before and after (line numbers shift by the 9 added comment lines only). RELEASE-NOTES-TEMPLATE.md headings are also unchanged.
- [pass] No internal IDs or version citations in the added template lines (grep of added lines: none).
- [pass] Committed as 116384b9 and merged --no-ff as 549a7cd1 (two parents). Feature branch deleted.

## Findings

### Finding 1: Project summary report shows the bug template's guidance comment as a bug's summary

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (one report's one-liner; cosmetic)
**Filed As:** BUG-0603-project-summary-shows-template-comment (Path C-A, filed as BUG)
**Filed By:** TESTER (autonomous Path C) — the project summary report shows a bug's template guidance comment as its one-line summary, because the new bug template puts an HTML comment first in the Symptom section and the report does not skip comments.

This RC's BUG-TEMPLATE.md opens `## Symptom` with a nine-line HTML guidance comment. `team/pgai_agent_kanban/reports/project_summary.py` `_extract_field` takes the first non-blank line after the heading without stripping comments. Reproduced through `team/scripts/project-summary.sh --project demo --all` in a sandbox kanban root. The same Symptom text written from the ai_main template lists as "The dashboard froze after a resize.", and from the RC template as "<!-- The opening sentence of this section becomes this bug's bullet in the". The other Symptom readers (release-notes generator, release_known_issue.sh, changelog_writer.py) strip comments, so release notes, Known Issues, and CHANGELOG are unaffected. Severity low (rubric: cosmetic). This RC introduced the exposure; the comment-blind reader predates it. Reach today is limited to new projects and filers copying the shipped template directly, because of Finding 2.

### Finding 2: Existing projects' seeded bug template is never refreshed, so the new filer guidance does not reach them

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (guidance-only; pre-existing design)
**Filed As:** BUG-0604-seeded-bug-template-never-refreshed (Path C-A, filed as BUG)
**Filed By:** TESTER (autonomous Path C) — an existing project's copy of the bug template is never updated after creation, so filers keep using an outdated template that lacks newer guidance and sections.

BUG-0602's Files Involved names `bugs/templates/BUG-TEMPLATE.md`, and WRITER-048's Goal is that "bug filers should know their Symptom's opening sentence is what the shipped release notes will say". `_TEMPLATE_COPIES` in `team/pgai_agent_kanban/ops/projects.py` is written only by `create_project`, and install.sh refreshes only `$KANBAN_ROOT/templates/`. The live project's `bugs/templates/BUG-TEMPLATE.md` (dated Aug 30) already lacks the `## Acceptance` section ai_main's source carries, and will not receive this RC's Symptom guidance. TESTER's Path C filing reference points filers at that copy. Severity low. Pre-existing (not introduced by this RC). The WRITER ticket's scope was correctly limited to the source templates, so this is not a WRITER defect.

## Lint Findings (non-blocking)

- `engine_size_dupe` — `team/scripts/lib/cm/release_notes.sh` is 869 lines, over the 800-line engine ceiling (743 on ai_main; this RC's heredoc growth). One instance, in both suites' findings files. Filed BUG-0605-release-notes-engine-over-size-ceiling (Path C-A).
- `inline_python` — `team/scripts/lib/cm/release_notes.sh:220` has 467 inline Python lines vs. an allowlist entry of 341 (ai_main: 0 findings). One instance, in both suites' findings files. Filed BUG-0606-release-notes-inline-python-over-allowlist (Path C-A).

These two do not change the Recommendation or Systemic Risk (Step 5c).

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=30d freshness=stale — The latest ledger record is 30 days old (outside the 7-day window) and verified v2.9.20 rather than v2.32.2; it failed to build the since-removed claude-abliterated image (the bug that run filed is BUG-0167, now done) and podman/rocky9 at a COPY --chmod step, so nothing is known about whether this RC's images build.

## Autonomous criterion (Step 6.12)

1. Tasks force-promoted from WAITING to BACKLOG by hand: NO. This task's promotion is recorded as "by wake script: prerequisites satisfied". Queue markers match each task's state (CM-049 [x], CODER-047 [x], WRITER-048 [x], PM-006 [x], TESTER-050 [A], CM-052 [W]).
2. release-state.md edited outside the CM scripts: NO. Its mtime (21:22:10Z) equals `## RC Opened At` written by CM-20261007-049. Fields are well-formed, `## Last Released` v2.32.1, and `semver_gt v2.32.2 v2.32.1` holds (`pp_last_released_version` = v2.32.1).
3. Scripts patched mid-build: NO. The only commit touching team/scripts during the RC is 8abd66a4, the CODER ticket's own deliverable.
4. Queue files edited by hand: NO evidence. Marker shapes are canonical, and no reset or activity-log entries exist for 2026-10-07.
5. Non-CM remote git operation: NO. No `git-boundary-guard.jsonl` exists in any RC task folder (PM-006, CM-049, CODER-047, WRITER-048, TESTER-050).

Autonomous operation criterion: Manual interventions required: none

## Other methodology steps

- 6.8 Synthetic release simulation: skipped. The diff touches no release-lifecycle open/close files (release_notes.sh is a notes-generation stage, not open/close). The integration suite, which includes the synthetic release tests, passed.
- 6.10 Prereq graph: acyclic. CM-049 (open) -> CODER-047, WRITER-048 -> TESTER-050 -> CM-052 (release, also depends on CODER/WRITER). All IDs exist. Sequence 051 is unused in every project (a numbering gap, not a collision).
- 6.13 Skip-cites-real-bug: the changed test files have no skip or xfail annotations. The lint_skip_bug_gate pre-flight passed in both runners.
- 6.14 Coding standards: no new scripts. The new helpers are local to the heredoc, which follows the file's documented pattern (no import path from the heredoc). Size and inline-Python growth are captured by the two lint filings above. No hardcoded operator-tunable values.

### Pre-flight (Steps 2-3)

- Step 2: scanned every task folder under projects/pgai-agent-kanban/tasks/. 667 DONE tasks name a feature branch; none of those branches still exist locally (normal post-merge shape), so Check 1 and Check 2 pass trivially. Anomalies: 0. The two RC tasks' feature commits (8abd66a4, 8153747f for CODER-047; 116384b9 for WRITER-048) are ancestors of ai_rc/v2.32.2 via --no-ff merges 5642e89a and 549a7cd1.
- Step 3: worktree HEAD 549a7cd1 == ai_rc/v2.32.2 (549a7cd1). `git status --porcelain` empty. `git stash list` empty.

### Diff and checklist (Step 4)

`git diff ai_main..ai_rc/v2.32.2`: 5 files, +466/-67. team/scripts/lib/cm/release_notes.sh (RELNOTES_PY heredoc: new bug-path helpers `_parse_section_outside_fences`, `_lead_paragraph`, `_is_abbreviation_end`, `_lead_sentence`; `get_bug_substance` and `get_item_title` docstrings), team/templates/project/release/BUG-TEMPLATE.md, team/templates/project/release/RELEASE-NOTES-TEMPLATE.md, team/tests/unit/test_release_notes_content_described.py, team/tests/unit/test_release_notes_bug_fixes_section.py. Nothing outside the two tickets' declared scope changed. `_first_sentence`, `_substance_from_section`, `_parse_section` and `get_priority_substance` are untouched (priority/H1 paths preserved).

Checklist sources: requirements doc (3 criteria: bundled item addressed; Python compiles; bash syntax), BUG-0602 Expected, CODER-047 criteria (5) and constraints, WRITER-048 criteria (4) and constraints.

### Behavioral verification (Step 6.5)

- Original failure mode, production-shape input: extracted the RELNOTES_PY heredoc from ai_main and from the RC and ran each (sandboxed output root under the framework temp dir) against copies of the real BUG-0597..BUG-0602 files. ai_main reproduces BUG-0602's Symptom byte-for-byte (menu-of-options bullet, `(e.g.` truncation, imperative Fix text). The RC renders six `Fixed: <Symptom lead sentence>` bullets: no Fix text, no fence content, no trailing ':', no BUG-/PRIORITY-/task IDs, balanced parentheses and backticks.
- Guard cases (synthetic): a bug filed from the RC's own BUG-TEMPLATE.md (multi-line HTML guidance comment) renders `Fixed: What was observed.` (comment stripped; `_HTML_COMMENT_RE` is DOTALL). Fix-only bug renders the generic fallback sentence. A fenced `## Expected` line inside Symptom no longer ends the section. `e.g.`, `i.e.`, `etc.`, `vs.`, `cf.` do not end a sentence; `?` does. PRIORITY-/task IDs scrubbed, KI-1.2.3.4 preserved. The old generator fails each of these cases.
- Downstream: changelog_writer regenerated CHANGELOG `### Fixed` from the RC's notes; bullets including `e.g.`/`i.e.`/`etc.` pass through verbatim (fixed_other_lines are rendered as-is). The out-of-scope `changelog_writer._first_sentence` abbreviation weakness (CODER-047 Notes: "TESTER files it separately if it reproduces") does NOT reproduce on the Bug Fixes path; it is used only for `## Summary` and ledger titles. Not filed.
- lint_hygiene (provenance, version-named-test, comment-drift) on the generated release-notes dir: 0 findings.
- Corpus survey (production shape): RC `get_bug_substance` over all 441 live BUG files: 384 clean single-sentence bullets, 23 generic fallback (all lack a `## Symptom` heading; operator-authored Expected/Fix-only shapes; old generator quoted their Fix text, the RC falls back per the PM decision), 13 lead-in-shaped (e.g. "... line 183 reads."), 21 single sentences over 300 chars. 0 internal-ID leaks.
- Spot-check (6.11, untested consumer): other `## Symptom` consumers. release_known_issue.sh and changelog_writer.py strip HTML comments (DOTALL). `team/pgai_agent_kanban/reports/project_summary.py` `_extract_field` returns the first non-blank line after `## Symptom` without stripping comments. Reproduced through `team/scripts/project-summary.sh` in a sandbox root: a bug written from the ai_main template lists as "The dashboard froze after a resize."; the same bug written from the RC template lists as "<!-- The opening sentence of this section becomes this bug's bullet in the". -> Finding (bug).
- Seeded template reach: `create_project` (team/pgai_agent_kanban/ops/projects.py `_TEMPLATE_COPIES`) is the only writer of `projects/<name>/bugs/templates/BUG-TEMPLATE.md`; install.sh refreshes `$KANBAN_ROOT/templates/` only. The live project's seeded copy (mtime Aug 30) already lacks the `## Acceptance` section ai_main's source carries, and will not receive this RC's Symptom guidance on upgrade. -> Finding (gap).

## Caveats

- The v2.32.2 release notes' own bullet for BUG-0602 will read: "Fixed: The `## Bug Fixes` section of the generated `release-notes/v2.32.1.md` (committed on ai_main and tagged `ai_v2.32.1`) contains." This complies with the CODER criteria (Symptom-sourced, no Fix text, no trailing ':', no IDs) but is thin. BUG-0602's Symptom opens with a lead-in followed by a code block, which is exactly the shape the new template guidance tells filers to avoid. I recorded it as pass-with-caveat, not a gap, because the criteria accept a lead-sentence rule. CM/WRITER may want to polish that line at release (Step 8b allows WRITER polish). Across the live corpus, 13 of 441 bugs have a similar lead-in shape.
- Pass-with-caveat: the generic fallback wording "(no Symptom/Fix text recorded in the bug report)" is now inaccurate for Fix-only bugs, which do have Fix text. The CODER criterion explicitly required the existing sentence, so I did not file it. 23 of 441 live bugs (all without a `## Symptom` heading) render this fallback; the old generator quoted their Fix text.
- Observation: for bugs without a `## Symptom` heading, `## Expected` would be a better source than the generic sentence. That is an enhancement outside this RC and was not filed.
- The CODER ticket asked TESTER to file changelog_writer's `_first_sentence` abbreviation weakness if it reproduces. It does not reproduce on the Bug Fixes -> CHANGELOG `### Fixed` path (bullets pass through verbatim; verified with an `e.g.`/`i.e.`/`etc.` bullet), so I filed nothing.
- No artifacts were committed to the RC. The dispatch prompt forbids a feature branch for this task, and the role file says process artifacts never enter a dev tree. The role file's "Git workflow" paragraph (commit report on a feature branch) conflicts with both; I followed the higher-precedence dispatch prompt.
- Coverage unavailable (config-disabled). This is informational and not a finding.
