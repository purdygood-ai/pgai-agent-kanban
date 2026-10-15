# Verification Report: rc/v2.15.2

**RC branch:** `ai_rc/v2.15.2` @ `285f99edfae1cd306341238ee8c6b91d89f3fa6a`
**Requirements:** `projects/pgai-agent-kanban/requirements/v2.15.2-bugfix-bundle-20260910.md`
**Bundled item:** BUG-0231-bugs-skipped-block-unscrubbed
**Worktree:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260910-045-verify-2-15-2` (detached HEAD, clean, no stashes)
**Verified:** 2026-09-10

## Summary

v2.15.2 is a single-bug bundle closing BUG-0231: a helper that scrubs internal
kanban identifiers out of the release notes' "Bugs Skipped" table existed, was
well tested, and had no production caller. The RC delivers three things — the
three duplicate copies of the scrub collapsed into one shared module
(`team/scripts/lib/cm/internal_id_scrub.py`), the helper wired into the release
pipeline through `cm_build_bugs_skipped_ship_block`, and the already-shipped
leaked identifier in `release-notes/v2.15.0.md:64` rewritten to describe the
defect without naming the identifier. Both gated runners exit 0 (unit 3749
passed / 3 skipped; integration 436 passed / 1 skipped). Every item on
BUG-0231's own `## Acceptance` list is met, and I verified each one
individually, including a negative control that proves the new wiring is load
bearing rather than decorative.

One gap remains, and it is a coverage gap rather than a broken fix. The render
is reachable from exactly one call site, guarded by the existence of a
WRITER-authored `release-notes/<version>.md` carrying a `PENDING-RELEASE`
placeholder. This project has not produced such a file in any of its last five
releases — all five read `Released By: cm-release.sh (automated)` — and the
identifier BUG-0231 cites leaked on that auto-generated path, through CM's
post-tag hand augmentation, which this RC leaves untouched. The RC also ships
no `release-notes/v2.15.2.md`, so the next release takes the unprotected path
again. Filed as BUG-0232.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

low

The RC's delivered code is correct, green, and behaviorally verified. The
residual gap is prose-only: it can produce a shipped document that names an
internal identifier a reader cannot look up. Nothing at runtime breaks, no
chain state or data is at risk, and no path is made worse than it was before
this RC. It heals on the next loop.

## Findings

### Finding 1: BUG-0231 acceptance list — all six items met

**Category:** pass

Verified item by item against the RC.

1. **A caller exists outside `release_known_issue.sh`.**
   `team/scripts/lib/cm/release_ship_policy.sh:528` calls
   `cm_build_bugs_skipped_ship_block "${_TESTER_REPORT_PATH}" "${_CM_PROJECT_ROOT}/bugs" "${_KNOWN_ISSUE_BUG_FILES[@]}"`,
   which composes `read_ship_policy_skipped_bug_pairs` and
   `build_bugs_skipped_notes_block` and sets `_BUGS_SKIPPED_NOTES_BLOCK` for
   the stamp at line 620. PASS.
2. **An integration test drives the path end to end over all six agent prefixes.**
   `team/tests/integration/test_cm_release_known_issue_carry_forward.py::test_bugs_skipped_section_is_rendered_through_the_scrub_not_hand_typed`
   builds a synthetic project under the framework temp root, runs the real
   `cm/open-rc.sh` and `cm/release.sh`, seeds a WRITER notes file whose
   hand-typed Bugs-Skipped table already leaks an identifier, and asserts
   against the file the pipeline wrote — one finding per prefix in
   `["PM","CODER","WRITER","TESTER","CM","PO"]`, each rationale carrying a
   `<PREFIX>-20260910-777-some-task` identifier, all six asserted absent from
   the output. It also asserts the hand-authored placeholder row did not
   survive. Passes standalone in 2.50 s. PASS.
3. **`grep -rEn '(CODER|WRITER|TESTER|CM|PM|PO)-[0-9]{8}-[0-9]+' release-notes/` returns nothing.**
   Run on the RC: exit 1, no output. PASS.
4. **`lint_comment_provenance.py` reports no finding under `release-notes/`.**
   Run standalone: 10 findings, all in `team/scripts/why.sh` and three test
   files, all citing `BUG-0228`. Zero under `release-notes/`. The eleventh
   finding that existed on v2.15.1 (`release-notes/v2.15.0.md:64`) is gone.
   PASS.
5. **The scrub exists in one place.** `_INTERNAL_ID_RE` and
   `_strip_internal_ids` now live only in
   `team/scripts/lib/cm/internal_id_scrub.py`; all three heredocs
   (`release_notes.sh`'s `RELNOTES_PY`, and `release_known_issue.sh`'s
   `build_known_issue_notes_block` and `build_bugs_skipped_notes_block`)
   import it, locating the directory via `dirname "${BASH_SOURCE[0]}"` and
   passing it through `PGAI_CM_INTERNAL_ID_SCRUB_DIR`. The one remaining
   independent implementation (`team/pgai_agent_kanban/cm/changelog_writer.py:939`,
   untouched since v2.4.0 and untouched by this RC) is deliberately re-affirmed
   in `internal_id_scrub.py`'s docstring, naming why it was left alone —
   which is the second half of the acceptance item's own "or" clause. PASS.
6. **Both gated runners exit 0.** `UNIT_EXIT=0`, `INTEGRATION_EXIT=0`. PASS.

### Finding 2: behavioral verification of the wiring — negative control

**Category:** pass

Principle 4 / Step 6.5. I did not take the passing test as proof the guard is
load bearing. I replaced the wiring call at `release_ship_policy.sh:528` with
`_SKIPPED_BUG_PAIRS=(); _BUGS_SKIPPED_NOTES_BLOCK=""` and re-ran the
integration test:

```
FAILED ...::test_bugs_skipped_section_is_rendered_through_the_scrub_not_hand_typed
assert 'placeholder' not in '# Release N...ues\nNone.\n'
```

The hand-authored placeholder row survived into the written notes, which is
exactly the BUG-0231 failure mode. Wiring restored via `git checkout --`;
`git status --short` empty afterwards.

I also drove `cm_build_bugs_skipped_ship_block` directly against a synthetic
report and bugs directory (under `$PGAI_AGENT_KANBAN_TEMP_DIR`, never the live
root) to exercise three guards independently of the test suite:

- a rationale reading `Deferred because CODER-20260910-041-bugs-skipped-scrub-wired already covers the area.`
  rendered as `Deferred because already covers the area.` — identifier and its
  full kebab slug both consumed;
- a `Category: pass` finding produced no row;
- a bug passed as an exclusion (the Known-Issues path's own bug file) produced
  no row, so the same bug cannot appear under both sections.

Pair count 1 of 3 findings, as expected.

### Finding 3: WRITER-043 documentation and the repaired shipped text

**Category:** pass

- `release-notes/v2.15.0.md:64` now reads "quote an internal work-item
  identifier a reader cannot look up" in place of the identifier. No
  `provenance-allowlist` marker added, per BUG-0231's instruction.
- `docs/cm-release-reference.md` gains "Bugs Skipped is rendered, not
  authored", which describes the render accurately **and states its limits
  honestly** — that an empty render leaves authored text unscrubbed, that a
  re-run over already-stamped notes does not re-render, and that
  `## Bugs Resolved` has no equivalent. That candour is what let me locate
  Finding 4 quickly; it is good documentation, not a defect.
- `RELEASE-NOTES-TEMPLATE.md` gains an HTML comment telling the author the
  section is rendered and that their text is a fallback for the
  no-qualifying-finding case only.

### Finding 4: the render is unreachable on the notes path this project ships on

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (prose-only; no runtime failure, no data or chain state at risk)
**Regression:** no (the auto-generated notes path and CM's post-tag augmentation step are untouched by this RC's diff, which names neither `team/roles/CM.md` nor `scripts/cm/finalize-release.sh`; the defect predates this RC — the leak this bundle was chartered to close occurred on that path in v2.14.1, v2.14.2, and v2.15.0)
**Integrity Damage:** no
**Filed As:** BUG-0232 (Path C-A — filed as BUG: code defect / regression / test failure)

`cm_stamp_release_notes_status` — the only thing that renders the Bugs-Skipped
table — is called from one place, `release_ship_policy.sh:620`, inside a branch
guarded by two conditions (`release-notes/<version>.md` exists on the RC branch
AND contains `PENDING-RELEASE`). When no such file exists, Step 4e logs
`No WRITER-authored release notes found ... skipping stamp (Step 11a will
auto-generate)` and returns.

Step 11a's generator (`cm_release_generate_notes`) emits `Status`, `Breaking
Changes`, `Upgrade Notes`, `Features`, `Bug Fixes`, `Deprecations`,
`Documentation`, `Other Changes`, `Known Issues` — and no `## Bugs Skipped`
section at all (`grep -n "Bugs Skipped" team/scripts/lib/cm/release_notes.sh`
returns nothing).

Yet three recent shipped notes files carry that section:

| File | Released By | Has `## Bugs Skipped` |
|---|---|---|
| `release-notes/v2.14.1.md` | cm-release.sh (automated) | no |
| `release-notes/v2.14.2.md` | cm-release.sh (automated) | yes |
| `release-notes/v2.14.3.md` | cm-release.sh (automated) | no |
| `release-notes/v2.15.0.md` | cm-release.sh (automated) | yes |
| `release-notes/v2.15.1.md` | cm-release.sh (automated) | yes |

Since the generator never writes the heading, those three sections arrived from
CM's post-tag augmentation (`team/roles/CM.md`, "Augmenting the auto-generated
notes"), which remains hand-authoring with a non-enforcing reminder as its only
protection: "run `team/scripts/lint_comment_provenance.py --prose-dirs
release-notes` and read it clean before committing the polish"
(`team/roles/CM.md:159`). The identifier BUG-0231 cites,
`release-notes/v2.15.0.md:64`, sits inside that block on that path.

The RC ships no `release-notes/v2.15.2.md`, so v2.15.2's own notes will be
auto-generated and its Bugs-Skipped block, if CM writes one, will not pass
through the scrub.

The honest framing: BUG-0231's `## Acceptance` list is fully satisfied
(Finding 1). The gap is against BUG-0231's `## Expected` sentence — "the
release pipeline renders it through the scrubbing helper rather than trusting
the author" — which the acceptance list under-specified by naming only the
existence of a caller and not the reachability of that caller on the project's
own release path.

Fix options, both in BUG-0232: render the section on the auto-generated path
too (`_BUGS_SKIPPED_NOTES_BLOCK` is already computed on every release at line
528, before Step 11a, and simply discarded when no WRITER draft exists), or
gate the augmentation commit in `scripts/cm/finalize-release.sh` with a
dual-channel refusal. `team/roles/CM.md` also needs to catch up with
`docs/cm-release-reference.md`, which already documents the split correctly.

### Finding 5: suites, structure, and the standing checks

**Category:** pass

- **Pre-flight merge verification** (Step 2a): scanned every DONE task with a
  `## Feature Branch` value; all feature branches were deleted post-merge
  (normal lifecycle), so no UNMERGED and no BUG-92 anomalies.
- **Worktree** (Step 3/3a): HEAD `285f99e` matches `ai_rc/v2.15.2`,
  `git status --short` empty, `git stash list` empty.
- **release-state.md drift** (Step 6.1): `pp_last_released_version` returns
  `v2.15.1`; `semver_gt v2.15.2 v2.15.1` true; `## Last Released` reads
  `v2.15.1`, well-formed; `ai_rc/v2.15.2` branch exists and matches
  `## Active RC`. No drift.
- **Compile / syntax** (requirements AC 2 and 3): `python3 -m py_compile` clean
  on `internal_id_scrub.py` and both changed test files; `bash -n` clean on all
  three changed shell libraries.
- **Synthetic release simulation** (Step 6.8, triggered — the diff touches
  `release_ship_policy.sh`, `release_notes.sh`, `release_known_issue.sh`):
  `tests/integration/test_cm_release_synthetic.py` — 15 passed in 32.78 s.
- **Skip-cites-real-bug** (Step 6.13): no placeholder skip IDs in live test
  code (the only `BUG-SKIP`-shaped hits are inside archived coverage logs under
  `team/tests/artifacts/`, which document the gate itself); every cited
  `BUG-NNNN` in a skip annotation resolves to a file in `bugs/`.
- **Census lint**: `lint_gate_census_compliance.py` — OK, 35 files checked, 0
  offenders, 139 anchored sites reconciled.
- **Engine size / duplication lint**: OK, 35 engine files within the 800-line
  ceiling, no cross-file function-name collisions — relevant because
  `cm_build_bugs_skipped_ship_block` was deliberately factored into
  `release_known_issue.sh` rather than inlined at the call site to stay under
  that ceiling, and the lint confirms the decomposition held.
- **Coding-standards spot-check** (Step 6.14): no new `--help`-bearing script;
  no new hardcoded operational value; no newly duplicated `lib/` operation (the
  RC removes duplication rather than adding it); one directive-8-adjacent hit,
  `internal_id_scrub.py:46`'s illustrative `"...see BUG-0011 for the earlier
  attempt"`, which is a documented example string the provenance lint exempts
  by design and which does not trip it.
- **Prereq dependency graph** (Step 6.10): acyclic, correctly bookended
  (CM-044 open -> CODER-040 -> CODER-041 -> {CODER-042, WRITER-043} ->
  TESTER-045 -> CM-047 release), every referenced task ID resolves to a real
  task folder, no sequence collisions. The unused `046` slot is the
  HUMAN-APPROVE position left unmaterialized because the bundle's
  `## Human Approval Required` is `auto` — the same shape as the prior RC
  (036/037/039).
- **Installed-layout reachability of the new module**: `install.sh:734` copies
  the whole `scripts` tree via the `cp -r "$src/."` merge at line 673, so
  `internal_id_scrub.py` ships beside the `.sh` files that read it in the
  flattened installed layout. The heredocs locate it via `BASH_SOURCE`, never a
  guessed path.

## Lint Findings (non-blocking)

- `comment_provenance` — 10 instances, all `[BUG-ID] provenance reference
  'BUG-0228' in code`, at `team/scripts/why.sh:180`, `:264`;
  `team/tests/integration/test_defer_campaign_lifecycle.py:281`;
  `team/tests/unit/test_defer_operator_verbs.py:133`, `:265`, `:287`, `:307`,
  `:372`; `team/tests/unit/test_why_details_pane.py:419`, `:486` — cites
  **BUG-0230** (open). BUG-0230 names all ten sites individually; no duplicate
  filed. The eleventh instance BUG-0230 listed for completeness
  (`release-notes/v2.15.0.md:64`) is fixed by this RC and no longer reported.

Both findings files were present and each carried exactly this one
quality-class entry. Per Step 5c these do not move the recommendation or the
systemic risk.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=3d freshness=stale — the last lane run was red on the docker/claude-abliterated and podman/claude-abliterated flavors (Dockerfile FROM arity) and podman/rocky9 (an entrypoint COPY --chmod); it cites BUG-0167, filed for the claude-abliterated pair by that lane; the record is stale because v2.9.20 is not the v2.15.2 under verification (its 3-day age is inside the 7-day window), so nothing is known about whether this tree's images build.

Reported, not re-derived and not re-filed, per Step 5d. `team/scripts/deep-verify.sh`
is present and executable, so the tree has a cold-build gate in front of its
images even though the last recorded run of it was red against a much older
tree state. This line does not move the recommendation or the systemic risk.

## Autonomous criterion

Manual interventions required: none.

1. **Any task force-promoted from WAITING to BACKLOG by hand?** NO. Every
   status transition in the v2.15.2 chain is agent-written; this task's own
   `status.md` records the wake script's automatic WAITING->BACKLOG promotion.
2. **Was `release-state.md` edited outside the CM scripts?** NO. It holds
   `Active RC: v2.15.2`, `RC Opened By Task: CM-20260910-044-open-rc-2-15-2`,
   `Last Released: v2.15.1` — the exact shape `cm-open-rc.sh` writes, and the
   file's mtime (15:05) matches the recorded open timestamp.
3. **Were any scripts patched mid-build?** NO. `git log ai_main..ai_rc/v2.15.2`
   holds exactly six commits: three feature commits and their three merges, one
   pair per ticket, all authored between 15:31 and 16:57 on 2026-09-10 and all
   within their tickets' declared scope. No out-of-band tooling commit.
4. **Were any queue files manually edited?** NO. `coder_backlog.md` and
   `tester_backlog.md` carry only canonical markers; this task is `[A]`
   (WORKING, written by the wake script when it started), everything before it
   `[x]`.

One process observation, not an intervention:
`CODER-20260910-042-bugs-skipped-integration-test` terminated `WONT-DO` with
`Needs Human: yes`, on the grounds that CODER-041 had already delivered the
integration test the ticket asked for, leaving 042's feature branch with zero
commits. I verified that claim independently rather than accepting it: the test
exists, is registered under `run-integration-tests.sh`, is parameterised over
all six prefixes, and fails when 041's wiring is removed (Finding 2). The
disposition is correct and the deliverable ships. It is worth noting only
because the decomposition produced a "wire it in" ticket and a "prove it"
ticket whose scopes overlapped, and 042's own `## Next Recommended Step` says
the same.

## Caveats

- **A scrubbed rationale can render ungrammatically.** `_strip_internal_ids`
  deletes an identifier mid-sentence without repairing what is left, so
  `Deferred because CODER-20260910-041-bugs-skipped-scrub-wired already covers
  the area.` renders as `Deferred because already covers the area.` — clean,
  but a published sentence that does not parse. Observation, not a finding: the
  scrub's contract is non-leakage, and this behavior predates the RC (it is the
  shared definition's, unchanged apart from where it lives). Recorded in
  BUG-0232's Fix section for whoever next touches the render.
- **Test-fidelity note (checklist item 2, installed tree not simulated).** The
  new integration test drives `cm/release.sh` from the dev tree, not from an
  installed root, so the flattened `scripts/lib/cm/` layout the
  `PGAI_CM_INTERNAL_ID_SCRUB_DIR` mechanism was written for is not exercised by
  any test. I closed the concern by inspection rather than execution:
  `install.sh:734` copies `scripts` wholesale through the directory-merge
  branch at line 673, and the heredocs self-locate via `BASH_SOURCE` rather
  than guessing, so both layouts resolve. Plausible-but-not-concrete, so noted
  here rather than filed.
- **A fourth scrub implementation still exists.**
  `team/pgai_agent_kanban/cm/changelog_writer.py:939` carries its own
  `_strip_internal_ids`. It is outside BUG-0231's acceptance item (which names
  the three heredoc copies), untouched since v2.4.0, untouched by this RC, and
  its exclusion is deliberately documented in `internal_id_scrub.py`'s
  docstring. Observation only.
- **Fixture prose names an internal bug ID.**
  `team/tests/integration/test_cm_release_known_issue_carry_forward.py:648`
  contains the fixture string `"the exact failure mode BUG-0231 describes."`.
  The provenance lint exempts ordinary string literals used as test data by
  design (its Exception list), and it reports no finding there. Borderline
  against the "tests describe behavior, not process history" habit, but
  explicitly permitted by the enforcing tool. Observation, not a finding.
- **Coverage** is reported below for information only. It does not enter the
  PASS precondition and is never a finding.

## Code Coverage

- Unit:        line 75%  |  branch 61%   (14784 stmts, 4462 branches)
- Integration: line 8%   |  branch 6%

Source: the `totals` objects in `$PGAI_AGENT_KANBAN_TEMP_DIR/coverage/unit.json`
and `.../integration.json`, using the `percent_statements_covered` and
`percent_branches_covered` fields confirmed against the generated files. Note
that the `term` report's blended `TOTAL` reads 72% for unit and 8% for
integration; those are statement-plus-branch figures, not line coverage.

## Suite Results

| Suite | Command | Exit | Result |
|---|---|---|---|
| Unit | `bash team/scripts/run-unit-tests.sh --coverage` | 0 | 3749 passed, 3 skipped (502 s) |
| Integration | `bash team/scripts/run-integration-tests.sh --coverage` | 0 | 436 passed, 1 skipped (395 s) |

Both gated runners — the same commands an operator or CM would run, including
the lint pre-flight — exited 0. The PASS evidence for the suites is these exit
codes, not a direct pytest line.
