# Verification Report: ai_rc/v2.14.2

## Summary

Verified release candidate `ai_rc/v2.14.2` (commit
`acb939523890daec0b18dd4063c11883e9d7370b`) against
`projects/pgai-agent-kanban/requirements/v2.14.2-bugfix-bundle-20260910.md`, a
four-item bug bundle (BUG-0220, BUG-0216, BUG-0218, BUG-0219). Verification ran
in the isolated detached worktree at
`/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260910-008-verify-2-14-2`,
which matched `ai_rc/v2.14.2` exactly, was clean, and carried no stashes.
All four bundled items were verified structurally and behaviorally -- every guard
this RC touched was exercised by triggering the failure mode it prevents, not by
reading the code. All four fixes are correct. The integration gated runner exited
0 (424 passed, 1 skipped). The unit gated runner exited 1 on three failures in
`tests/unit/test_lint_comment_provenance.py`; those three failures were
reproduced identically against `ai_main` (tagged `ai_v2.14.1`), so they predate
this RC and are not a regression. Their cause -- a quality-class lint finding
reaching the certification exit code through a test that scans the real
repository instead of its own fixture -- is itself the pattern this RC's new
blocking-severity doctrine forbids, and is filed. Three bugs were filed via Path
C-A; none is a defect in this RC's own delivered work, and one (BUG-0223) is a
comment-only dangling doc pointer this RC introduced.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

medium

## Verification Scope

- **RC branch / commit:** `ai_rc/v2.14.2` @ `acb9395`
- **Baseline:** `ai_main` @ `9615fe6`, tagged `ai_v2.14.1` (also the merge-base)
- **Diff:** 14 files, +785 / -99, across 12 commits (5 feature merges + 1 follow-up)
- **Requirements doc:** `requirements/v2.14.2-bugfix-bundle-20260910.md`

### Pre-flight

| Check | Result |
|---|---|
| Worktree HEAD == `ai_rc/v2.14.2` | pass (`acb9395` both sides) |
| `git status --short` clean | pass (empty) |
| `git stash list` empty | pass (empty) |
| DONE-task feature branches merged into RC (Check 1) | pass -- all this cycle's feature branches already deleted post-merge (normal), zero live branches to check |
| Zero-commits-ahead / BUG-92 anomaly (Check 2) | pass -- no anomalies |

### Test suites (authoritative gated runners)

| Runner | Exit | Result |
|---|---|---|
| `bash team/scripts/run-unit-tests.sh --coverage` | **1** | 3 failed, 3600 passed, 3 skipped (498 s) |
| `bash team/scripts/run-integration-tests.sh --coverage` | **0** | 424 passed, 1 skipped (366 s) |

`UNIT_EXIT == 1`, so the PASS precondition in Step 8 is not satisfied and the
recommendation cannot be PASS regardless of everything else.

## Code Coverage

- Unit:        line 74%  |  branch 61%   (14255 stmts, 4366 branches; blended 71%)
- Integration: line 8%   |  branch 6%    (blended 7%)

Source: `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/unit.json` and
`integration.json`, `totals` object, field names confirmed against the generated
files (`percent_statements_covered`, `percent_branches_covered`). Informational
only; does not enter the recommendation.

## Findings

### Finding 1: BUG-0220 -- governance byte budget demoted from hard gate to quality-class narration

**Category:** pass

Verified against every acceptance line in the bug.

- **Over-budget produces narration, not failure.** The `pm` role is currently
  over budget on the shipped tree. The unit runner printed, verbatim:

  ```
  Running governance byte-budget check...
  ROLE             ASSEMBLED         BUDGET   VERDICT
  po                   62811          65536   under
  pm                   76391          73728   OVER
  lint_governance_byte_budgets: pm over governance byte budget: assembled=76391 bytes budget=73728 bytes
  coder                69217          69632   under
  writer               68152          69632   under
  tester              101060         102400   under
  cm                   82780          86016   under
  overwatch            73635          73728   under
  [gated-runner] governance_byte_budget: OK
  ```

  The check reports `OK` to the runner while over budget. This is BUG-0220's
  acceptance line 1 and line 5 satisfied together: v2.14.1's README growth (the
  original HALT trigger) is still over budget, and it now narrates green.
- **Findings-file entry, quality class.** Both `findings-unit.json` and
  `findings-integration.json` carry
  `{"lint": "governance_byte_budget", "severity": "quality", "role": "pm",
  "assembled_bytes": 76391, "budget_bytes": 73728, ...}`.
- **`test_composer_governance_byte_budget` no longer fails on the threshold.**
  It passed (2 cases) while printing the same OVER row, and its own liveness case
  pins `coder` to a 1-byte budget, asserts the verdict is `OVER`, and asserts the
  process still returns 0.
- **Under-budget is silent.** `test_lint_governance_byte_budgets.py:310` asserts
  no `lint_governance_byte_budgets:` narration line appears when nothing is over.
  Confirmed in the real run: only the `pm` row emitted a narration line; the six
  under-budget roles emitted none.
- **Doctrine paragraph present, and cited by the check.** `team/SOP-core.md` gains
  a `## Blocking Severity: only real damage blocks` section, and both
  `team/scripts/lint_governance_byte_budgets.sh` (two sites) and the wiring in
  `run-unit-tests.sh` / `run-integration-tests.sh` cite it by name in their own
  comments.
- **No remaining size-threshold hard gate.** Grepped `team/tests/` for assertions
  on byte/size thresholds of governance or docs: the only hits are the byte-budget
  suite's own assertions on table text and finding fields, none of which fails on
  a threshold. Grepped both gated runners: the only byte/budget references are the
  quality-lint wiring block.

### Finding 2: BUG-0216 -- census anchor marker-to-index reconciliation

**Category:** pass

Verified behaviorally by triggering both new finding kinds, in throwaway copies of
the RC tree extracted with `git archive` (the RC worktree itself was never
modified).

- **Baseline is clean.** `python3 team/scripts/lint_gate_census_compliance.py`
  prints `OK -- 35 file(s) checked, 0 offenders.` and exits 0 (BUG-0216
  acceptance 4).
- **Set difference is empty** (BUG-0216 acceptance 3). A strict scan of every
  `^\s*#\s*CENSUS-ANCHOR:\s*<token>\s*$` line under `team/` finds 43 markers
  across 21 files; every one is named by a `path:token CLASS` line in the
  `CENSUS-LOCATOR-INDEX` fenced block. The RC adds exactly the 19 previously
  missing entries the bug enumerated.
- **`unindexed_anchor` fires** (BUG-0216 acceptance 1). Planted
  `# CENSUS-ANCHOR: tester-probe-99` above a synthetic `exit 3` in the scanned
  file `team/scripts/run-unit-tests.sh`. The lint produced:

  ```
  kind:    unindexed_anchor
  cause:   team/scripts/run-unit-tests.sh:7 -- CENSUS-ANCHOR token 'tester-probe-99'
           has no matching 'path:token CLASS' line in docs/blocking-site-census.md's
           CENSUS-LOCATOR-INDEX
  remedy:  Add 'team/scripts/run-unit-tests.sh:tester-probe-99 <CLASS>' to the
           CENSUS-LOCATOR-INDEX fenced block ...
  ```

- **`anchor_class_mismatch` fires.** Changed the index entry
  `team/scripts/lint_engine_size_dupe.py:engine-size-dupe-01` from `CONVERT` to
  `BLOCK-WORTHY` while the site's own inline comment still declares `CONVERT`.
  The lint produced exactly one finding, `kind: anchor_class_mismatch`, with
  `index_class: BLOCK-WORTHY`, `site_class: CONVERT`, and a remedy naming both.
- **No false positive** (BUG-0216 acceptance 2). The unmodified tree, carrying 43
  correctly-paired markers, produces zero findings.
- **Quality-class posture preserved.** The lint exited 0 in every one of the three
  runs above, including the two that produced findings.
- **Doc and lint agree on the finding set** (BUG-0216 acceptance 5).
  `team/roles/CODER.md` now enumerates exactly three findings --
  `missing_signal`, `unindexed_anchor`, `anchor_class_mismatch` -- matching
  `_FINDING_KIND_*` in the script. The bug's acceptance text names `team/SOP.md`,
  which no longer exists in the shipped tree (removed at v2.10.0); the equivalent
  shipped text is `team/SOP-core.md` "Authoring a new gate" plus CODER.md "Writing
  a compliant anchor", and neither now claims five findings or a both-directions
  reconciliation. CODER.md states plainly that the mirror direction (an index
  entry no marker names) is reported by nothing. Doc and implementation agree.
- **`bash -n` / `py_compile` clean** on every changed file, and
  `python3 -m compileall team/scripts team/pgai_agent_kanban` exits 0.

### Finding 3: BUG-0218 -- release-bundle slug removed from the wake bootstrap comment

**Category:** pass-with-caveat

The named instance is fixed; two of the bug's four acceptance lines remain unmet
for a reason external to this RC.

- `grep -c "v2.14.0-provider-runtime-hardening" team/scripts/lib/wake_bootstrap.sh`
  returns **0** (acceptance 2, pass).
- `team/scripts/lib/wake_bootstrap.sh` contains no `provenance-allowlist` marker
  (acceptance 3, pass). The rewrite deletes the parenthetical and lets the
  sentence stand, exactly as the bug's hand-off suggested.
- `python3 team/scripts/lint_comment_provenance.py` **does not** exit 0
  (acceptance 1, unmet) and both findings files **do** still contain one
  `comment_provenance` finding (acceptance 4, unmet). The residual finding is a
  different instance in a different file -- `release-notes/v2.14.1.md:33` --
  which is on `ai_main` and was written during the v2.14.1 release, after
  BUG-0218 was filed. It is filed as BUG-0222 and dispositioned under Lint
  Findings below.

**Caveat, not a gap:** the deliverable BUG-0218 asked CODER for was delivered
exactly. The two unmet lines are unmet because the bug's acceptance was written
as a whole-tree assertion and the tree acquired a new, unrelated instance in the
interval. No finding is charged against this RC's work here.

### Finding 4: BUG-0219 -- demo scratch-repo snippets guarded

**Category:** pass

- `grep -rn "git commit --allow-empty" team/demos/` returns nothing; every git
  command in both blocks is now bound with `git -C <path>`, so no command in the
  snippet can act on the reader's current directory.
- **Original failure mode triggered.** Created a real git repo, created
  `<repo>/nested/pgai-helloworld-src` inside it, and ran the shipped guard
  (`git -C <target> rev-parse --git-dir`). It **refused**; the outer repo's commit
  count stayed at 0. The exact accident that produced the stray empty commit on
  `ai_rc/v2.14.0` is now prevented.
- **New normal case verified.** Against a fresh directory outside any repo, the
  block created the repo and the single empty commit (`rev-list --count HEAD` = 1).
- **Re-run is safe.** Running the block a second time against the repo it just
  created refuses and leaves the commit count at 1 -- no duplicate commit. This
  preserves the idempotence the old helloworld snippet got from its
  `git rev-parse --verify -q HEAD ||` guard, which the rewrite dropped.
- **This RC's own branch is clean.**
  `python3 team/scripts/lint_rc_empty_commits.py` prints
  `OK -- checked 1 range(s) (ai_main..ai_rc/v2.14.2), no empty non-merge commits
  found.` and exits 0. All 12 commits in the range change files or are merges.
- The bug's "leave `ai_rc/v2.14.0` alone" constraint is honoured -- no history
  rewrite anywhere in the diff.
- The `<!-- doc-lint: skip -->` annotations added above the two multi-line blocks
  are accepted: `test_helloworld_flow_harness.py` and the doc-lint integration
  cases all pass.

### Finding 5: unit gated runner exits 1 -- provenance-lint tests scan the live release-notes directory

**Category:** bug (pre-existing)
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium
**Regression:** no (reproduced identically against `ai_main`, tagged `ai_v2.14.1`, the last shipped version and this RC's merge-base)
**Integrity Damage:** no
**Filed As:** BUG-0221 (Path C -- autonomous follow-up filing, non-blocking; Path C-A -- filed as BUG)

Three cases in
`team/tests/unit/test_lint_comment_provenance.py::TestCommentProvenanceUnshippedPathAndBareSlug`
fail:

```
test_docs_audits_path_not_flagged                       (line 649)
test_bare_release_slug_filename_value_not_flagged       (line 691)
test_bare_release_slug_format_description_not_flagged   (line 713)
```

Each writes a clean fixture into a scratch directory and calls
`_lint.main(["--code-dirs", str(code_dir)])`, then asserts the result is 0.
`--code-dirs` overrides only the *code* scan roots; the *prose* root stays at its
default, which resolves to the repository's own `release-notes/` directory. All
three failures report the same single cause, unrelated to their fixtures:

```
release-notes/v2.14.1.md:33: [task-ID] provenance reference
'TESTER-20260909-045' in code
```

So the assertion is not testing what its docstring claims; it is testing "the
repository currently has no provenance findings anywhere."

**Regression evidence.** `git archive ai_main | tar -x` into a scratch tree and
running the same three cases produces `3 failed, 5 passed`. `ai_main` is tagged
`ai_v2.14.1` and is this RC's merge-base, so the failure is present in the last
shipped version. This RC touches neither
`team/tests/unit/test_lint_comment_provenance.py` nor
`team/scripts/lint_comment_provenance.py` (empty diff for both), and
`release-notes/v2.14.1.md` is unchanged between `ai_main` and the RC.

**Why this matters beyond the exit code.** `comment_provenance` is a
quality-class lint. Both gated runners correctly record its finding with
`severity: quality` and neither runner fails on it -- the integration runner
exits 0 with the finding present. The same finding nevertheless reaches the
certification exit code through a unit test. That is a quality-class advisory
escalated into a hard gate, which is precisely what this RC's own new
`team/SOP-core.md` "Blocking Severity: only real damage blocks" paragraph names
as itself a bug. It also means no RC can record a PASS verdict until it is fixed.

**On the Integrity Damage value.** This is suite breakage: the authoritative unit
certification command exits 1. Under `team/SOP-core.md`'s three-value `Integrity`
field, suite breakage reads `breakage`. Under this role file's yes/no
`Integrity Damage` definition -- queue corruption, a violated lockfile invariant,
silently-lost task state, a destructive path that can run when it should not, or
data the system cannot reconstruct -- it is `no`: nothing is corrupted, no state
is lost, and the shipped artefact is byte-identical either way. Both readings are
stated here so CM can apply whichever its own matrix takes; the value written in
the parsed field is the one this role file defines.

### Finding 6: `release-notes/v2.14.1.md:33` cites an internal task ID

**Category:** bug (pre-existing)
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low
**Regression:** no (the file is on `ai_main`, added by the v2.14.1 release commits `9218ac1` / `9d83d8b`; unchanged by this RC)
**Integrity Damage:** no
**Filed As:** BUG-0222 (Path C-A)

The shipped v2.14.1 release notes end their Known Issues block with
`(See TESTER report for filed bug IDs: TESTER-20260909-045-verify-2-14-1)` -- a
kanban task folder a reader holding the release cannot open. This is the single
`comment_provenance` finding in both runners' findings files, and it is the root
cause of Finding 5's symptom (though not of Finding 5's defect, which is the test
isolation). Recurrence of a class fixed twice before, at BUG-0165 and BUG-0173,
both `done`; no `open` or `running` bug covered it at filing time.

### Finding 7: two new comments cite `team/SOP.md`, a file removed from the tree at v2.10.0

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low
**Regression:** yes (both lines are additions in this RC's diff)
**Integrity Damage:** no
**Filed As:** BUG-0223 (Path C-A)

`team/scripts/lint_gate_census_compliance.py:47` and `:514`, both added by this
RC, explain the anchor-reconciliation direction by citing `team/SOP.md`. That
file does not exist in the shipped tree -- `git cat-file -e ai_main:team/SOP.md`
fails, `ls team/*.md` lists only `DIRECTIVES.md OVERVIEW.md README.md
SOP-core.md TESTING.md`, and `install.sh` line 734 delivers `SOP-core.md` with no
entry for `SOP.md`. The same RC cites `team/SOP-core.md` correctly in
`lint_governance_byte_budgets.sh` and in both gated runners, so this is an
isolated miss rather than a pattern. One pre-existing sibling instance exists at
`team/scripts/lib/cm/release_campaign_spend.sh:30` and is named in the filing for
the same sweep.

Comment-only: no runtime effect, no operator-visible output.

### Finding 8: requirements-doc acceptance criteria

**Category:** pass

| Criterion | Result |
|---|---|
| Each bundled item is addressed in the implementation | pass -- Findings 1-4; BUG-0218 with the caveat recorded there |
| All Python files compile cleanly | pass -- `python3 -m compileall -q team/scripts team/pgai_agent_kanban` exit 0; each changed `.py` individually `py_compile`-clean |
| All bash files pass syntax check | pass -- `bash -n` clean on all four changed `.sh` files |

### Finding 9: structural, drift and dependency checks

**Category:** pass

- **Step 6.1 release-state sanity.** `projects/pgai-agent-kanban/release-state.md`
  reads `Active RC: v2.14.2`, `RC Opened At: 2026-09-10T02:39:38+00:00`,
  `RC Opened By Task: CM-20260910-007-open-rc-2-14-2`, `Last Released: v2.14.1`.
  `## Last Released` is well-formed `vX.Y.Z`. `v2.14.2` is strictly greater than
  `v2.14.1` by semver. The named `ai_rc/v2.14.2` branch exists and matches the
  worktree -- no `Active RC` / branch-state drift. The file's mtime (02:39)
  matches its own `RC Opened At` value to the minute, consistent with
  `cm-open-rc.sh` having written it.
- **Step 6.8 synthetic release simulation.** The diff touches no
  release-lifecycle file (`cm-release.sh`, `cm-open-rc.sh`, `cm-cancel-rc.sh`,
  `lib/project_paths.sh`); the trigger condition is not met. Run anyway as part of
  the integration suite: `tests/integration/test_cm_release_synthetic.py` --
  15 passed.
- **Step 6.10 prereq graph.** Nine tasks in the cycle. `PM-001` -> `CM-007`
  (open RC) -> `WRITER-002`, `CODER-003`, `CODER-004`, `CODER-005`, `WRITER-006`
  (plan sequence 1-5, no inter-dependencies) -> `TESTER-008` -> `CM-010`
  (release). `CM-010`'s `## Prerequisites` names all five work tasks plus
  `TESTER-008`; every referenced task ID resolves to an existing folder. No
  cycles, no colliding sequence numbers. Sequence 009 is unused -- the
  HUMAN-APPROVE slot, unallocated because the bundle carries
  `Human Approval Required: auto`. Bookends correct.
- **Step 6.13 skip-cites-real-bug.** No placeholder skip IDs. No
  `pytest.skip` / `mark.skip` annotation in `team/tests/` cites a `BUG-NNNN`, so
  the file-existence sweep has nothing to resolve and reports nothing missing.
  The only `BUG-NNNN` literal hits are inside archived runner logs under
  `team/tests/artifacts/coverage-*-v2.3.0/`, which are fixture data.
- **Step 6.14 coding-standards spot-check.** `--help` on both touched lints names
  every argument, including the newly added `--findings-file` on
  `lint_governance_byte_budgets.sh` (added to the usage block, the header comment,
  and the schema documentation in the same change). Budgets stay in `kanban.cfg`
  as operator-tunable config rather than moving into code. No newly duplicated
  implementation of an existing `lib/` operation. The one directive-8 hit is
  Finding 7.
- **Step 6.11 spot-check.** No state drift found in the RC or the project's kanban
  state. See Caveats for one observation about the operator's live install, which
  is deliberately not charged as a finding.

## Lint Findings (non-blocking)

- `comment_provenance` -- one instance: `release-notes/v2.14.1.md:33`, a
  `[task-ID]` provenance reference `TESTER-20260909-045` in the shipped v2.14.1
  release notes. Present in both `findings-unit.json` and
  `findings-integration.json`, `severity: quality`. No `open` or `running` bug
  covered it (BUG-0165 and BUG-0173 cover the same class but are `done`), so
  **filed BUG-0222 (Path C-A)**.
- `governance_byte_budget` -- one instance: `pm over governance byte budget:
  assembled=76391 bytes budget=73728 bytes`, `severity: quality`. This is the
  intended new behaviour delivered by BUG-0220 (Finding 1), not a defect: the
  check is now advisory by design, budgets are operator-tunable config, and the
  bug that authorized the demotion says explicitly that no bug is required to
  change a budget. No filing. **Cites BUG-0220 (running)**, the item that shipped
  this narration in this RC.

Neither lint finding elevates the recommendation or the systemic risk on its own.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=2d freshness=stale -- the docker/claude-abliterated and podman/claude-abliterated pairs failed on a Dockerfile FROM parse error and podman/rocky9 failed at its COPY --chmod step; stale because v2.9.20 is not the v2.14.2 under verification (the 2-day age is inside the 7-day window), so nothing has been built cold against this tree; cites BUG-0167, filed by that deep-verify run.

## Autonomous criterion

| # | Check | Answer |
|---|---|---|
| 1 | Any task force-promoted from WAITING to BACKLOG manually? | **NO** -- every task's status.md carries the wake-script promotion note; no hand-edited State field found |
| 2 | `release-state.md` edited outside `cm-open-rc.sh` / `cm-release.sh` / `cm-cancel-rc.sh`? | **NO** -- file mtime 02:39 matches its own `RC Opened At: 02:39:38`, the only write in this cycle |
| 3 | Any script patched mid-build? | **NO** -- all 12 commits in `ai_main..ai_rc/v2.14.2` are task feature-branch commits or their merges, each authored by the standard agent identity; every tooling change under `team/scripts/` belongs to a decomposed ticket and arrived through its own feature branch |
| 4 | Any queue file manually edited? | **NO** -- queue mtimes (`coder_backlog.md` 04:35, `tester_backlog.md` 04:57, `writer_backlog.md` 04:57, `cm_backlog.md` 02:40) all fall inside wake-script activity windows; markers are consistent with task states |

**Autonomous operation criterion: Manual interventions required: none.**

Notably, this RC carries no stray empty commit -- the recurrence BUG-0219 was
filed against did not repeat on this cycle.

## Caveats

- **The recommendation call, stated plainly.** The only thing keeping this off
  PASS is a red unit runner whose three failures predate the RC, produce no
  runtime effect, and are an advisory finding escalated into a hard gate. Every
  line of work this RC was asked to deliver is verified correct and behaviorally
  exercised. The case for escalating to SHIP-WITH-SERIOUS-CONCERNS is real and
  worth stating: while BUG-0221 stands, *no* future RC can record a PASS verdict,
  which degrades the verification signal on every subsequent cycle. The case
  against -- and the reason the recommendation is SHIP-WITH-CONCERNS -- is that
  the shipped artefact is unaffected, no data or state is at risk, the defect is
  small and isolated to one test class's fixture isolation, the previous release
  already shipped with the identical failure, and escalating over a pre-existing
  advisory-turned-gate would repeat the exact over-blocking mistake this RC
  exists to correct. CM has both readings and may weigh them differently.
- **Fixing BUG-0222 alone would green the suite but not fix BUG-0221.** Removing
  the task ID from `release-notes/v2.14.1.md` makes the three tests pass today.
  They would go red again the next time anything under the default prose root
  carries an internal identifier. Both bugs are filed separately for that reason;
  closing only the release-note one would be a false fix.
- **Test fidelity.** BUG-0221 is a concrete instance of the Test Fidelity
  checklist's sandbox concern: a unit test reaching outside its fixture into the
  real repository. It is recorded as a finding, not merely a caveat. No other
  fidelity gap surfaced -- the RC's own new tests
  (`test_lint_gate_census_compliance.py`, `test_lint_governance_byte_budgets.py`)
  plant their fixtures in scratch trees, and I reproduced their guards against
  throwaway `git archive` copies rather than against the RC worktree.
- **Live-install observation (not a finding).** The operator's kanban root at
  `$HOME/pgai_agent_kanban` is at a Sep 5 install: it carries a 2385-line
  `SOP.md`, has no `SOP-core.md`, and has no `read-order/` directory, while the
  dev tree at v2.14.2 ships a 313-line `SOP-core.md` plus per-role read-order
  manifests. This is the normal, expected gap between the running kanban and the
  tree it is building -- the install is not upgraded on every release -- and it is
  not a defect in this RC. It is recorded only because it explains why BUG-0216's
  acceptance line 5 names a `team/SOP.md` that no longer exists in the tree, and
  because the blocking-severity doctrine this RC adds to `SOP-core.md` will not
  reach any running agent until the operator upgrades the install.
- **Cost.** Unit suite 498 s, integration suite 366 s. No container build was
  attempted or started at any point, per Step 5d.
