# Verification Report: ai_rc/v2.32.4

## Summary

I verified RC v2.32.4 (`ai_rc/v2.32.4` @ dd196680) against
`requirements/v2.32.4-bugfix-bundle-20261008.md`. The bundle has six bugs: BUG-0610,
BUG-0613 and BUG-0614 are the headline items, and BUG-0608, BUG-0609 and BUG-0611 are
hygiene ride-alongs. Four tasks delivered them: CODER-002, CODER-003, CODER-004 and
WRITER-005. For each bug, I reproduced the original failure on ai_main and then
confirmed the RC fixes it. All six fixes work as specified. Both gated runners exited
0, and the requirements doc's compile and syntax criteria pass. The checks found two
defects, both filed via Path C (autonomous follow-up filing — non-blocking) as
Path C-A (filed as BUG — code defect / regression / test failure):

- **BUG-0616.** The new release-notes Status-section reader added by this RC can
  fail open. It treats a `<!--` quoted above `## Status` as a real comment opener,
  so Step 4e skips the stamp and ships the notes with `PENDING-RELEASE` still in
  place. Rated medium. This is a regression on an edge-case input.
- **BUG-0617.** The OVERWATCH Lane A drill still runs its destructive steps after a
  failed RC cancel. This predates the RC; CODER-003 asked TESTER to file it.
  Rated medium.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Pre-flight (Steps 2-3)

- Step 2 RC integrity: scanned every task folder; 675 DONE tasks name a feature
  branch, none of those branches still exists locally (normal post-merge shape),
  so Check 1 and Check 2 pass trivially. All four prerequisite merges
  (69cafac1, 698f8e6a, 8a7184c2, dd196680) are on `ai_main..ai_rc/v2.32.4`. **pass**
- Step 3 worktree: `git rev-parse HEAD` = `git rev-parse ai_rc/v2.32.4` =
  dd1966807f26805005bbb06b4a251660489b5bce; `git status --porcelain` empty;
  `git stash list` empty. **pass**

## Diff checklist (Step 4)

12 files, +1238/-96. Deliverables -> bundled item:
- BUG-0610 / BUG-0613: `team/pgai_agent_kanban/cm/notes_status_section.py` (new),
  `team/scripts/lib/cm/release_known_issue.sh`, `team/scripts/lib/cm/release_ship_policy.sh`,
  `team/config/inline-python-allowlist.txt`, `team/tests/unit/test_release_notes_status_section_scope.py` (new)
- BUG-0614: `team/pgai_agent_kanban/overwatch/resolution_stage.py`,
  `team/tests/unit/test_overwatch_cancel_and_rebundle_drill.py` (new)
- BUG-0608: `team/pgai_agent_kanban/reports/project_summary.py`,
  `team/tests/unit/test_project_summary_symptom_comments.py`
- BUG-0609: `team/tests/unit/test_release_notes_bugs_skipped_render.py`,
  `team/tests/unit/test_release_notes_auto_ship_line.py`
- BUG-0611: `docs/OPERATIONS.md`

Every changed file maps to a bundled item; no file in the diff is unaccounted for.

## Behavioral checks (Step 6.4 / 6.5) — checkpoint notes

- BUG-0610 original failure: an unmodified copy of `RELEASE-NOTES-TEMPLATE.md`
  stamped with ai_main's `cm_stamp_release_notes_status` -> WARNING + exit 1,
  placeholder left. Same input on the RC -> exit 0, `## Status` / guidance comment
  kept / `KNOWN-BUGS`. Template unchanged by the RC (constraint held). **pass**
- BUG-0613 original failure: pre-stamp v2.32.3 draft stamped with a Bugs Skipped
  row quoting `"PENDING-RELEASE placeholder not found"`: Status = KNOWN-BUGS on both
  trees; the old whole-file guard (ai_main) reports the placeholder survived
  (the v2.32.3 halt); the RC's `cm_release_notes_status_has_placeholder` reports
  absent (no halt). Negative: an unstamped placeholder under `## Status` with the
  same row elsewhere -> guard reports present (still fires). Stamping the
  reconstructed v2.32.3 draft on the RC reproduces the shipped v2.32.3 notes
  byte-for-byte (no-comment behavior unchanged). **pass**
- Edge probe (new reader): a quoted `` `<!--` `` in prose ABOVE `## Status`
  makes the new Status-section reader treat the rest of the file as a comment,
  so the pre-stamp check answers "absent" and Step 4e takes the already-complete
  path. Driven through the real `cm_release_apply_ship_policy` on the sandbox
  lane (`tests/fixtures/ship_policy_sandbox.py`): rc=0, no HALT, notes keep
  `## Status` = `PENDING-RELEASE`. Control (same notes without the quote) stamps
  KNOWN-BUGS. ai_main stamps the quoted-opener input correctly. -> Finding 1.

- BUG-0614 negative control: the RC's new `test_overwatch_cancel_and_rebundle_drill.py`
  run against ai_main's `resolution_stage.py` -> 5 failed / 3 passed; ai_main's
  summary line reads exactly the v2.32.3 incident shape ("...; 1 action(s) reported
  errors"). On the RC -> 8 passed. Fidelity: the tests stub `_run_cm_cancel_rc`;
  the real v2.32.3 actions.log line `cm-cancel-rc.sh ... FAILED: LOCKED)` proves the
  real script's LOCKED outcome reaches the same `ok=False` branch, so the stub is not
  hiding the breaking condition. Retry ceiling: `attempt` is derived from
  release-state records written at RC open (`cm/bundle_family.compute_next_attempt`),
  not from ledger entries, so a repeated failed cancel does not consume retries. **pass**
- BUG-0608: the bug's two reproduction files plus a bare mid-line opener, a
  line-start template comment, and a copy of live BUG-0438 — ai_main mangles/truncates
  the quoted-opener values; the RC returns the full original lines; template-comment
  and comment-only cases unchanged. Independent corpus comparison
  (`ai_v2.32.2` reader, the last release before comment skipping, vs the RC) over
  copies of all 541 live bug files, every heading plus the standard fields (7043
  reads): 0 differences on the 526 files with no line-start comment; 9 differences
  only in files with real comments (intended skips). **pass**
- BUG-0609 mutation check (on a scratch `git archive` of the RC, not the worktree):
  unmodified generator -> new test passes; `"Bugs Skipped"` removed from
  `SECTION_ORDER` -> fails; render branch disabled -> fails. Contrast: ai_main's old
  test still passes with the section removed (confirms it was vacuous).
  `grep -n RELNOTES_PY team/tests/unit/test_release_notes_*.py` -> no output. **pass**
- BUG-0611: `grep -n 'bugs/BUG-TEMPLATE.md' docs/OPERATIONS.md` -> no match; the
  passage now names `projects/<name>/bugs/templates/BUG-TEMPLATE.md` (exists in all
  live projects), correctly states the template has no `## Resolved By`
  (grep confirms on both shipped templates and the project copy), cites
  `team/pm-agent/lib/bug_scanner.py`, and its "present and non-empty" claim matches
  `_extract_resolved_by`. Both accepted forms still stated. **pass**
- Requirements AC "All Python files compile cleanly": 844 tracked `.py` files
  compiled, 0 failures; `py_compile` on the 8 touched Python files OK. **pass**
- Requirements AC "All bash files pass syntax check": `bash -n` on 334 tracked
  `.sh` files, 0 failures. **pass**

## Methodology checks (Step 6) — checkpoint notes

- 6.1 release-state: `## Active RC` v2.32.4, `## RC Opened At` 2026-10-08T14:57:46+00:00,
  `## RC Opened By Task` CM-20261008-006-open-rc-2-32-4, `## Last Released` v2.32.3
  (well-formed). `pp_last_released_version` = v2.32.3; `semver_gt v2.32.4 v2.32.3`
  true. The appended `## HALT Event` (v2.32.3 trigger-9) is the documented CM audit
  append, not drift. **pass**
- 6.2 structural: all 12 diff files present; new module and new tests in expected
  packages; `RELEASE-NOTES-TEMPLATE.md` intentionally unchanged (CODER-002 constraint). **pass**
- 6.8 synthetic release: the diff touches release-cycle libs
  (`release_known_issue.sh`, `release_ship_policy.sh`), so
  `team/tests/integration/test_cm_release_synthetic.py` applies; it runs inside the
  gated integration suite (result recorded under Gated suites).
- 6.9 constraint negatives on the diff: no removed `set -`/`trap` lines; no
  secret-shaped additions; no hardcoded live-install or bare `/tmp` paths added. **pass**
- 6.10 prereq graph: PM-001 -> CM-006 (open) -> CODER-002/003/004 + WRITER-005 ->
  TESTER-007 -> CM-009 (release, WAITING on all five). No cycles; every ID exists;
  bookends correct; sequence numbers unique (008 unused, no collision). **pass**
- 6.12 autonomous criterion: see section below.
- 6.13 skip gate: the diff adds no skip/xfail annotation; no `BUG-SKIP`-style
  placeholder in any test file (hits only in archived coverage logs). **pass**
- 6.14 coding standards: new module has argparse `--help`; no bug/task/version
  identifiers in added production lines; test-side BUG-shaped values are fixtures
  (sanctioned). The RC adds two independent HTML-comment scanners
  (`notes_status_section._segments` and `project_summary._strip_html_comments`)
  with different rules for quoted openers — the root of Finding 1, recorded there
  rather than as a separate finding.
- 6.11 spot-check: CODER-003's status asked TESTER to file the Lane A
  cancel-then-continue shape. Exercised (not just read): `execute_lane_a_drill` with
  `_run_unwind_rc` and `_run_cm_cancel_rc` stubbed to return LOCKED on a throwaway
  root -> bundle doc deleted, ship-policy HALT cleared, `## Active RC` still set.
  Identical on ai_main (pre-existing). -> Finding 2.

## Gated suites (Step 5a / 6.6 / 6.7)

Both suites were run from the dev tree root with `--coverage`, one after the other.
Output is under `/tmp/pgai_kanban_tmp/tester/`.

- `bash team/scripts/run-unit-tests.sh --coverage` -> **UNIT_EXIT=0**. Lint pre-flight
  clean. Parallel lane: 10563 passed, 41 skipped, 3 warnings. Serial lane: 5 passed.
  "Unit tests PASSED".
- `bash team/scripts/run-integration-tests.sh --coverage` -> **INTEGRATION_EXIT=0**.
  Parallel lane: 959 passed, 1 skipped. The serial lane selected 0 tests (pytest
  exit 5); the runner combines that to 0 by design. "Integration tests PASSED".
- Post-suite hygiene: `bare_tmp_cleanliness`, `listener_cleanliness`,
  `sentinel_leak` and `scratch_sweep` all OK on both runs.
- Step 6.8 synthetic release: `test_cm_release_synthetic.py` ran inside the green
  integration suite, and I also ran it targeted
  (`run-integration-tests.sh --files tests/integration/test_cm_release_synthetic.py`).
  Result: 23 passed, exit 0. **pass**

All tests passed. The PASS evidence for the suites is these two runner exit codes,
not a pytest line.

## Coverage (Step 5b, informational)

Coverage unavailable for both suites. Each runner printed "coverage disabled by
config ([testing] coverage)", so no coverage JSON was produced. This is not a finding.

## Findings

### Finding 1: Status-section reader fails open on a quoted comment opener above ## Status

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (this RC's own BUG-0608 fix is the same quoted-`<!--` defect class, reappearing in a sibling reader written in parallel; it could recur in any reader with its own comment scanner)
**Filed As:** BUG-0616 (Path C-A)
**Regression:** yes
**Integrity:** none

- Traces to: requirements AC "each bundled item is addressed" (BUG-0610 / BUG-0613),
  and the CODER-002 constraint "the post-stamp guard stays a real stop when the
  placeholder genuinely survives under ## Status".
- Location: `team/pgai_agent_kanban/cm/notes_status_section.py` (`_segments`,
  `_visible_lines`, `status_sections`). It feeds
  `cm_release_notes_status_has_placeholder` (`team/scripts/lib/cm/release_known_issue.sh`)
  and Step 4e in `team/scripts/lib/cm/release_ship_policy.sh`.
- Expected: a `<!--` quoted in prose or inline code above `## Status` is text. The
  placeholder is stamped, or the trigger-9 guard halts.
- Actual: I drove the real `cm_release_apply_ship_policy` on the sandbox lane
  (`tests/fixtures/ship_policy_sandbox.py`) with notes whose header line quotes
  `` `<!--` `` above `## Status` / `PENDING-RELEASE`. Result: exit 0, no HALT, and
  Step 4e printed "already complete, continuing". The committed notes still read
  `PENDING-RELEASE`. The control (the same notes without the quote) stamps KNOWN-BUGS.
  On ai_main the quoted-opener input stamps correctly. No later step checks for the
  placeholder.
- Why medium and not high: the release command still works for every notes file
  that does not quote `<!--` above `## Status`. The template puts `## Status`
  directly after the title block, so normal drafts never reach this path. No
  current notes file has this shape, and the workaround is simple: do not quote the
  opener above Status, or correct the Status value by hand. Under SOP-core
  "Rating a Finding" that is "incorrect, but a workaround exists / nothing depends
  on it yet". It is still this RC's regression (`Regression: yes`), and it bypasses
  a BLOCK-WORTHY guard, so I recorded it as a bug rather than a caveat. CM should
  weigh this paragraph. The rating was not softened to reach a ship outcome.

### Finding 2: Lane A drill deletes the bundle doc and clears the HALT after a failed RC cancel

**Category:** bug (pre-existing-bug)
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated to one pre-existing code path; the sibling drill was fixed in this RC)
**Filed As:** BUG-0617 (Path C-A)
**Regression:** no
**Integrity:** none

- Traces to: the CODER-003 ticket Notes ("if CODER confirms the same defect there,
  record it in status.md for TESTER to file"). CODER-003 recorded it from code
  reading only. This also falls under the Step 6.11 spot-check category
  "regressions of prior gaps", since it is the same class as BUG-0614.
- Location: `team/pgai_agent_kanban/overwatch/resolution_stage.py`
  `execute_lane_a_drill`.
- Expected: when unwind-rc or cancel-rc fails, the bundle doc and the ship-policy
  HALT are left in place.
- Actual: I set up a throwaway kanban root and stubbed both `_run_unwind_rc` and
  `_run_cm_cancel_rc` to return LOCKED. The bundle doc was deleted and the HALT
  removed, while `## Active RC` stayed set. ai_main behaves identically.
- Rated medium as a real but unexercised defect. Lane A runs only when
  `overwatch_resolve` is enabled, and it is off by default for this project.

### Per-criterion results (pass)

| Criterion | Result |
|---|---|
| Requirements AC: each bundled item addressed: BUG-0610 (template stamps; multi-line comment; template unchanged) | pass |
| BUG-0613 (guard and pre-check scoped to `## Status`; still halts on a genuine survivor; concrete Status with the word elsewhere takes the already-complete path) | pass (edge case: Finding 1) |
| BUG-0614 (failed cancel reverts nothing, HALT kept, errors and actions.log say so; successful cancel unchanged) | pass |
| BUG-0608 (quoted opener read in full; corpus of 526 comment-free files: 0 differences) | pass |
| BUG-0609 (test targets the generator; fails under two mutations; no RELNOTES_PY mention) | pass |
| BUG-0611 (no nonexistent path; field definition stated in place; both forms kept) | pass |
| Requirements AC: all Python files compile (844 / 0 failures) | pass |
| Requirements AC: all bash files pass `bash -n` (334 / 0 failures) | pass |
| Both gated runners exit 0 | pass |

## Lint Findings (non-blocking)

None. Both `/tmp/pgai_kanban_tmp/lint/findings-unit.json` and
`/tmp/pgai_kanban_tmp/lint/findings-integration.json` exist and contain 0 findings.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=31d freshness=stale — The last deep-verify run was 31 days ago, against v2.9.20 rather than v2.32.4, so it is stale on both age and version, and it failed to build; its claude-abliterated failures were filed as BUG-0167 (now done), and I found no open bug naming its podman/rocky9 `COPY --chmod` failure, so nothing is known about whether the current images build.

## Autonomous criterion (Step 6.12)

1. Tasks force-promoted WAITING->BACKLOG by hand? **NO.** The only promotion on
   record was made by the wake script; queue markers match each task's state.
2. `release-state.md` edited outside the cm scripts? **NO.** Its mtime
   (2026-10-08 14:57:46.62Z) equals `## RC Opened At`, which open-rc wrote. The
   v2.32.3 `## HALT Event` block predates this RC and is the documented CM audit append.
3. Scripts patched mid-build? **NO.** No live-install file under `scripts/`,
   `pm-agent/`, `roles/` or `subagents/` changed after RC open. The RC's own
   `team/scripts/` changes came through CODER feature-branch merges.
4. Queue files edited by hand? **NO.** All markers are well-formed and consistent
   with each task's `## State`.
5. Remote git operations by a non-CM agent? **NO.** No
   `logs/git-boundary-guard.jsonl` exists in any of this cycle's task folders.

Autonomous operation criterion: Manual interventions required: none

## Caveats

- Coverage is disabled by `kanban.cfg [testing] coverage`, so no coverage numbers
  were recorded.
- shellcheck is not installed on the host and was not run (CODER-002 noted the same).
- Observation (CODER-003, verified): when the cancel keeps failing, OVERWATCH writes
  one pending ledger entry per sweep. I checked that this does not consume the retry
  ceiling: `attempt` comes from release-state records written at RC open
  (`cm/bundle_family.compute_next_attempt`), not from ledger entries.
- Observation (CODER-002 design note): a notes file with no `## Status` heading but
  the word elsewhere now halts with `trigger-9.notes-missing-status-heading` instead
  of `trigger-9.stamp-step-failed`. Both are BLOCK-WORTHY trigger-9 stops; the new
  key names the real fault. A test covers it.
- Observation (CODER-004 design note): line-start comment blocks in bug files may
  still span a heading. Existing tests pin this, and it matches markdown rendering.
  Only mid-line and quoted openers are bounded to their paragraph.
- Observation (WRITER-005): the "canonical path" Resolved By form names only a
  CODER task ID, but WRITER tasks also close bugs. This predates the RC and was not
  in scope, so I did not file it.
- Observation (state, pre-RC): the operator restored BUG-0603..0607 to `done` after
  the v2.32.3 incident, but BUG-0607 has no `## Fixed In` value. This is live-install
  state outside this RC.
- The v2.32.4 release notes describe the BUG-0608 fix and may quote `<!--`. That
  matters for Finding 1 only if the quote sits above `## Status`, which the template
  layout does not do.
- Fidelity: the BUG-0614 tests stub the cancel subprocess. The real incident log
  (`cm-cancel-rc.sh ... FAILED: LOCKED`) shows the real script reaches the same
  `ok=False` branch, so I have no concrete fidelity concern.
- Probes ran in scratch only: `git archive` extracts of ai_main, `ai_v2.32.2` and
  the RC under `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester-007-scratch`,
  with throwaway kanban roots for state-mutating probes. The worktree stayed clean
  throughout (`git status --porcelain` empty at the end).
