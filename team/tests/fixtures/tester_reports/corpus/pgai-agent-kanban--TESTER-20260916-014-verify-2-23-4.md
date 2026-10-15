# Verification Report: ai_rc/v2.23.4

## Summary

Verified release candidate `ai_rc/v2.23.4` (HEAD `9bd522e`, detached worktree at
`/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260916-014-verify-2-23-4`)
against `projects/pgai-agent-kanban/requirements/v2.23.4-bugfix-bundle-20260916.md`.
The bundle carries a single item, BUG-0297 (severity `low`): v2.23.3 closed the
code-writing lane's `bugs/`-directory write path at the prompt level but shipped
no test pinning that prose, so a future role-file trim could delete the only
barrier with both gated runners still exiting 0. The RC's entire diff against
`ai_main` is one new file, `team/tests/unit/test_coder_role_forbids_bugs_directory_writes.py`
(123 lines, 3 tests). Pre-flight merge verification passed; the worktree is clean
and stash-free; the version ordering is sound. Both gated runners exited 0
(unit: 4627 passed / 31 skipped in 761.94s; integration: 510 passed / 1 skipped
in 628.34s) with every quality-class lint OK and zero entries in both findings
files. The RC's guard logic was verified behaviorally in both directions against
sandboxed copies of the role file, not merely by observing the green suite. The
requirements doc's own two mechanical criteria (Python compiles, bash parses)
were re-run independently and are clean. No bug, gap, or stale-assertion finding
was recorded, so nothing was filed via Path C (autonomous follow-up filing —
non-blocking).

## Recommendation

PASS

## Systemic Risk

low

## Findings

### Finding 1: Deliverable exists and is the only change on the RC

**Category:** pass

`git diff --name-only ai_main...ai_rc/v2.23.4` returns exactly one path:
`team/tests/unit/test_coder_role_forbids_bugs_directory_writes.py`. Two commits
sit on the RC beyond `ai_main`: `68656a7` ("Pin the code-writing lane's
bugs/-directory write boundary with a test") and the `--no-ff` merge `9bd522e`.
No production script, role file, or governance document was touched — consistent
with BUG-0297's `## Fix`, which asks for an assertion that pins existing prose
rather than any change to the prose itself. `team/roles/CODER.md:138` still
carries the boundary sentence verbatim.

### Finding 2: BUG-0297 acceptance bullet 1 — the pin fails on removal, passes on presence, in both demonstrated directions

**Category:** pass

BUG-0297 asks for a test in `team/tests/` that "fails when the
never-write-into-`bugs/` boundary is removed from `team/roles/CODER.md`, and
passes with it present. Demonstrate both directions." This was verified
behaviorally (Step 6.5), independently of the shipped module's own
`pytest.raises` wrapper, by reconstructing the layout the module resolves
(`pathlib.Path(__file__).resolve().parents[2] / "roles" / "CODER.md"`) inside a
sandbox under `$PGAI_AGENT_KANBAN_TEMP_DIR` and swapping the role file underneath
the unmodified test:

- **Negative direction** (role file with the boundary sentence stripped):
  `3 failed` — `test_shipped_role_file_states_the_boundary`,
  `test_assertion_is_position_independent`, and
  `test_assertion_fails_once_the_boundary_sentence_is_removed` all raised, and the
  failure message named the missing phrase and a remedy. The pin is a real gate,
  not a tautology that passes against anything.
- **Positive direction** (shipped role file, unmodified): `3 passed`.

The negative case was produced by editing a copy under the framework temp root;
the shipped `team/roles/CODER.md` was never modified.

### Finding 3: The chosen match phrase balances reword-tolerance against deletion-detection as specified

**Category:** pass-with-caveat

BUG-0297's `## Fix` asks the assertion to "match on a stable phrase rather than
the whole paragraph, so ordinary rewording does not produce a false failure while
deletion does." PM's handoff explicitly asked TESTER to read the chosen phrase
rather than trust the green runner. The module pins
``Never write into a project's `bugs/` directory.`` — the bolded lead sentence
that states the prohibition. Two probes against sandboxed copies:

- Rewriting all four explanatory sentences that follow the lead sentence (who may
  file, what to do with a mid-task defect, the `bug_backlog.md` exemption), leaving
  the lead sentence intact: `3 passed`. Ordinary paragraph rewording does not
  produce a false failure, as specified.
- Replacing the lead sentence with a semantically equivalent reword
  (`The code-writing lane never writes into a project's \`bugs/\` directory.`):
  `3 failed`.

The second probe is the documented cost of a literal-phrase pin, and the module's
docstring names it as a deliberate choice rather than an oversight. It is recorded
as a caveat, not a gap: the acceptance criterion asked for deletion-detection, and
a WRITER who rewords the rule itself gets an explicit, actionable failure rather
than a silent pass. See `## Caveats` for the one wording nit in the assertion's
remedy text.

### Finding 4: BUG-0297 acceptance bullet 2 — both gated runners exit 0

**Category:** pass

Run from the RC worktree root, with `--coverage`:

- `bash team/scripts/run-unit-tests.sh --coverage` → **UNIT_EXIT = 0**
  (`4627 passed, 31 skipped in 761.94s`). The new module appears in the run as
  `tests/unit/test_coder_role_forbids_bugs_directory_writes.py ...` (3 passed).
- `bash team/scripts/run-integration-tests.sh --coverage` → **INTEGRATION_EXIT = 0**
  (`510 passed, 1 skipped in 628.34s`).

Every quality-class lint pre-flight reported OK in both runs: breakage-class gate
(463 python / 269 shell files, 0 failures), Dockerfile static lint, test
anti-pattern lint (0 findings across 518 files), skip-cites-real-bug gate,
orphan-test lint (318 test files covered, no orphans — the new module is covered),
API-parity, operator-verb-manifest, upgrade no-temp-copy guard, ICD freshness,
ICD compat, dashboard window census, CHANGELOG freshness, doc-truth lint,
engine-size/dupe lint, bare-`/tmp` cleanliness, and listener cleanliness. The PASS
evidence cited here is the gated-runner exit codes, not a direct-pytest "N passed"
line.

### Finding 5: BUG-0297 acceptance bullet 3 — no new gate introduced

**Category:** pass

The deliverable is an ordinary pytest module. It introduces no refusal site, no
`exit`/`sys.exit`, no `# CENSUS-ANCHOR:` marker, and no entry in
`docs/blocking-site-census.md`; it therefore incurs none of the SOP's new-gate
obligations (census class comment, dual-channel error contract, Locator Index
entry). The bug's stronger runtime-guard variant (a wake-teardown diff of
`projects/<name>/bugs/` across a CODER session) was explicitly out of scope and
was correctly not built.

### Finding 6: Requirements acceptance criterion — all Python files compile cleanly

**Category:** pass

`find . -name '*.py' -not -path './.git/*' | xargs python3 -m py_compile` over the
whole RC worktree: exit 0, empty stderr.

### Finding 7: Requirements acceptance criterion — all bash files pass syntax check

**Category:** pass

`bash -n` over every `*.sh` in the RC worktree (269 shell files by the runner's own
count): zero syntax failures, empty error file. The gated runner's breakage-class
gate independently confirms the same result.

### Finding 8: Pre-flight merge verification

**Category:** pass

Scanned all task directories under `projects/pgai-agent-kanban/tasks/` for
`## State: DONE` with a `## Feature Branch` other than `none`. Every such branch
has already been deleted post-merge (normal CODER/WRITER Procedure A Step 8
cleanup), so both Check 1 (ancestor of RC) and Check 2 (BUG-92 zero-commits-ahead)
correctly skip. Script output: `Pre-flight merge verification passed.` No unmerged
branch and no BUG-92 anomaly.

### Finding 9: Working tree and stash state

**Category:** pass

`git rev-parse HEAD` = `9bd522e6cffda8ea10c1e1804286474a59951aa3`, identical to
`git rev-parse ai_rc/v2.23.4`. `git status --short` empty before and after all
verification work. `git stash list` empty.

### Finding 10: Step 6.1 — release-state.md drift check

**Category:** pass

`pp_last_released_version pgai-agent-kanban` → `v2.23.3` (from git tags).
`semver_gt v2.23.4 v2.23.3` → true, so the RC version is strictly greater than
Last Released. `release-state.md` `## Last Released` reads `v2.23.3`, a well-formed
`vX.Y.Z` value (its presence is by design and is not drift). `## Active RC` is
`v2.23.4` and the branch `ai_rc/v2.23.4` exists — no state drift. No `HALT`,
`HALT-AFTER`, `DEFER-LOW-BUGS`, or `SUBSTRATE_BROKEN` flag at either the kanban or
the project scope.

### Finding 11: Step 6.8 — synthetic release simulation not triggered

**Category:** pass

The RC diff touches no release-lifecycle file (`cm-release.sh`, `cm-open-rc.sh`,
`cm-cancel-rc.sh`, `team/scripts/lib/project_paths.sh`, or anything else under
`team/scripts/`). Trigger condition unmet; step skipped with note, as the procedure
specifies.

### Finding 12: Step 6.10 — prerequisite dependency graph

**Category:** pass

The v2.23.4 lifecycle is a clean chain with no cycles and no missing task IDs:
`CM-20260916-013-open-rc-2-23-4` (prereqs: none, DONE) →
`CODER-20260916-012-pin-coder-bugs-boundary` (DONE) →
`TESTER-20260916-014-verify-2-23-4` (WORKING, this task) →
`CM-20260916-016-release-2-23-4` (WAITING; prereqs CODER-012 and TESTER-014).
Bookends are correct — CM-open at the start, TESTER then CM-release at the end.
No HUMAN-APPROVE task, consistent with the bundle's `## Human Approval Required: auto`.
Sequence numbers are consistent and non-colliding.

### Finding 13: Step 6.13 — skip-cites-real-bug gate

**Category:** pass

The authoritative check, `lint_skip_bug_gate.sh`, ran inside both gated runners and
reported `BUG-NNNN file-existence check: 0 missing files` and
`OK — all skip annotations are compliant`. Running the role file's illustrative
grep by hand produces four apparent `MISSING:` lines (`BUG-1234`, `BUG-1235`,
`BUG-1237`, `BUG-1239`); all four are string fixtures inside
`team/tests/unit/test_skip_bug_citation_scan.py`, the lint's own test module, and
the real gate correctly excludes them. This is a known limitation of the hand-grep,
not a defect in the tree — recorded as an observation under `## Caveats`.

### Finding 14: Step 6.14 — coding-standards spot-check

**Category:** pass

The RC's only new file was checked against the directives most likely to be
missed: no new script and therefore no `--help` obligation (directive 6); no bug
ID, task ID, RC name, or framework version anywhere in its docstrings, comments,
or assertion messages (directive 8) — the module's only version-shaped token is
none, and its only `/tmp` mention is prose explaining that no bare `/tmp` path is
touched; no hardcoded operational value an operator would want to change
(directive 5); no duplicated `lib/` implementation (directives 1 and 3). The
module writes its temporary copies through pytest's `tmp_path`, which the parent
conftest redirects under `$PGAI_AGENT_KANBAN_TEMP_DIR`, satisfying the
temp-file-hygiene rule and the Live-Install Safety boundary (`team/roles/` is
never written).

### Finding 15: Step 6.11 — spot-check outside the spec

**Category:** pass

No state drift (release-state consistent with the branch and the git tags; the
per-RC state file `release-state/v2.23.4.json` exists and matches). No untested
new mechanism — the RC's only addition is itself a test, and the orphan-test lint
confirms it is collected by a registered root. No sign of a returning prior gap:
BUG-0297's own precursor (BUG-0295's fourth acceptance bullet) is the thing this
RC closes, and the boundary sentence it depends on is present at
`team/roles/CODER.md:138`. `bug_backlog.md` carries BUG-0297 as `[x]` and the bug
file's `## Status` is `running`, which is the correct in-flight shape for an item
bundled into an open RC.

## Code Coverage

- Unit:        line 77%  |  branch 63%   (16489 stmts, 4950 branches; 12622 covered lines, 3118 covered branches)
- Integration: line 10%  |  branch 8%    (16480 stmts, 4950 branches)

Source: the `totals` objects in `$PGAI_AGENT_KANBAN_TEMP_DIR/coverage/unit.json`
and `.../integration.json`, using the `percent_statements_covered` and
`percent_branches_covered` fields confirmed by reading the generated files. The
blended `TOTAL` figures printed by the `term` report (73% unit, 9% integration)
are deliberately not quoted as line coverage. Coverage is informational only and
does not enter the PASS precondition.

## Lint Findings (non-blocking)

None.

Both findings files were present and empty:
`$PGAI_AGENT_KANBAN_TEMP_DIR/lint/findings-unit.json` (`"findings": []`,
generated 2026-09-16T07:58:29+00:00) and `.../findings-integration.json`
(`"findings": []`, generated 2026-09-16T08:09:17+00:00). No quality-class lint
tripped in either suite, so nothing was cited and nothing was filed.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=9d freshness=stale — the docker/claude-abliterated, podman/claude-abliterated and podman/rocky9 pairs failed cold-build at v2.9.20; cites BUG-0167-cold-build-claude-abliterated-failure, filed by that deep-verify run and since closed, and the record is stale on both counts (9d exceeds the 7-day window, and v2.9.20 is not the v2.23.4 under verification), so nothing is known about whether this tree's images build.

The lane was not run during this verification, per Step 5d — no container build was
attempted and no container engine was invoked. `team/scripts/deep-verify.sh` and
`team/scripts/run-cold-build-tests.sh` both exist and are executable, so the
"images ship with no cold-build gate anywhere" gap finding does not apply. This
project's `project.cfg [release]` sets `deep_verify = off`, which disables only the
weekly scheduled trigger; the on-demand verb
(`team/scripts/deep-verify.sh --project pgai-agent-kanban`) and the pre-release
freshness check are unaffected. Per Step 5d the red record is cited, never re-filed,
and neither its verdict nor its staleness moves this RC's `## Recommendation` or
`## Systemic Risk`.

## Autonomous criterion

Manual interventions required: none.

1. **Tasks force-promoted from WAITING to BACKLOG manually?** NO. The wake log
   records the automatic promotion:
   `[2026-09-16T07:33:51+00:00] wake(tester): WAITING task TESTER-20260916-014-verify-2-23-4: prerequisites satisfied, promoting to BACKLOG`,
   followed by the scripted worktree creation and `transitioned to WORKING [A]`.
2. **`release-state.md` edited outside the CM scripts?** NO. Its mtime is
   2026-09-16 07:05, matching `## RC Opened At: 2026-09-16T07:05:11+00:00` and the
   `cm-open-rc.sh` run recorded by CM-20260916-013. No later modification.
3. **Scripts patched mid-build?** NO. The only commits on the RC beyond `ai_main`
   are `68656a7` (the new test file) and its merge `9bd522e`. Nothing under
   `team/scripts/`, `subagents/`, or any other tooling path was touched while the
   RC was in flight.
4. **Queue files manually edited?** NO. Every `tasks/queues/*_backlog.md` mtime
   falls inside the cycle's automated windows (bug 07:00, pm 07:04, cm 07:06,
   coder 07:32, tester 07:33), each matching a wake-script write recorded in the
   cron logs. No out-of-band edit.

The build ran from discovery bundling through this verification without human
intervention.

## Caveats

- **The assertion's remedy text is slightly looser than the assertion itself.**
  On failure the module prints "Restore a sentence stating the code-writing lane
  never writes into a project's `bugs/` directory." A WRITER who follows that
  remedy literally, by authoring an equivalent sentence in different words, would
  still fail the test — the assertion requires the exact phrase
  ``Never write into a project's `bugs/` directory.`` This is cosmetic and
  arguable rather than a defect: the message names the required phrase verbatim in
  its first clause, immediately above the remedy sentence, so a reader has the
  exact string in front of them. Recorded as an observation, not a finding, and
  nothing was filed for it.
- **The role file's illustrative skip-gate grep over-reports.** Step 6.13's
  hand-grep flags four fixture strings inside the lint's own test module as
  missing bugs. The authoritative gate (`lint_skip_bug_gate.sh`, run inside both
  gated runners) reports zero missing files. Noted so a future TESTER does not
  re-derive the same false positive as a finding.
- **RC scope is one test file.** The RC carries no production-code change, so the
  usual regression surface for this release is limited to the test suite itself.
  Both suites are green, and the new module's three tests are self-contained
  content assertions over a governance file with no runtime coupling.
- **The deep-verify record is both red and stale.** BUG-0167, the bug that run
  filed, is now `## Status: done`, meaning the Dockerfile defects it named were
  fixed but no deep-verify run has confirmed that since. Per Step 5d this is context
  the rating was made with, not a finding, and TESTER files nothing for it. An
  operator who wants a current answer before promoting can run
  `team/scripts/deep-verify.sh --project pgai-agent-kanban` on demand; the
  pre-release freshness check will refuse a public tag on this record in any case.

## Path C filings

None. Zero `bug`, `gap`, and `stale-assertion` findings were recorded, so no Path C
(autonomous follow-up filing — non-blocking) filing was required and no `gaps.md`
was written.
