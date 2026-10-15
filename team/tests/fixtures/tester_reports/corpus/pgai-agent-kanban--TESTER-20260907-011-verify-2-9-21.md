# Verification Report: ai_rc/v2.9.21

**Task:** TESTER-20260907-011-verify-2-9-21
**Requirements doc:** `projects/pgai-agent-kanban/requirements/v2.9.21-bugfix-bundle-20260907.md`
**Worktree:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260907-011-verify-2-9-21`
**RC commit:** `443bdd68527dbc32dc0a8a00f94c493c760594e0` (matches `ai_rc/v2.9.21`)
**Base:** `ai_main` — 44 files changed, 8706 insertions, 108 deletions
**Verified:** 2026-09-07, fresh run (artifacts/ was empty at start; no prior report consulted)

## Summary

RC v2.9.21 bundles five bug items. Three of them (BUG-0162, BUG-0163,
BUG-0164) are test-fidelity defects that made the unit suite depend on state
outside the tree and let a unit test silently edit a tracked file; one
(BUG-0165) is a provenance violation in shipped release notes; and one
(BUG-0166) restores the DEEP-VERIFY tier — the cold-build lane, its three
operator verbs, the per-project ledger, the classifier, the bug filer, the
scheduler, and the governance and operator documentation that describe them —
into a lineage that had lost them.

All five bundled items are addressed, and each was verified behaviourally
rather than structurally: the changelog render guard was made to fail by
mutating a fixture disclosure; the provenance remedy-block confinement was
confirmed by running the full unit suite against a clean worktree and checking
that `release-notes/v2.9.20.md` was byte-identical afterwards; the restored
lane's three verbs were exercised against the live ledger, including the
promotion gate's refusal path, the scheduled self-gate, and the
unregistered-project refusal, with the ledger confirmed unmutated. Both gated
runners exit 0 with zero quality-class lint findings. The 8-task prerequisite
chain merged cleanly with no pre-flight anomalies, and the build ran without
manual intervention.

Two defects were found, both minor, both filed via Path C (autonomous
follow-up filing — non-blocking): the restored lane's failure-detail string
repeats each failing image once per log occurrence instead of once per pair
(BUG-0168), and a pre-existing doc-lint suite demands documentation for a
container flavour the tree does not ship (BUG-0169). Neither affects the
release's usability.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

low

## Test Suites (authoritative gated runners)

| Runner | Exit | Result |
|---|---|---|
| `bash team/scripts/run-unit-tests.sh --coverage` | **0** | 3150 passed, 3 skipped in 380.32 s |
| `bash team/scripts/run-integration-tests.sh --coverage` | **0** | 337 passed, 1 skipped in 210.32 s |

`UNIT_EXIT == 0` AND `INTEGRATION_EXIT == 0`. Both runners' lint pre-flights
were green — 16 checks on the unit runner, 12 on the integration runner, all
`OK`, including `test_anti_patterns`, `skip_cites_real_bug`,
`changelog_freshness`, `comment_provenance`, `bare_tmp_cleanliness` and
`listener_cleanliness`. The suite verdict cited here is the gated runners' exit
codes, not a direct-pytest line.

`git status --short` was empty both before and after each runner, and
`release-notes/v2.9.20.md` kept md5 `109066b29caf44fa96e0e0e97b6fd400`
throughout — the BUG-0164 behaviour, exercised end to end.

Also run, outside the gated runners:

- `team/tests/integration/test_cm_release_synthetic.py` — 15 passed (Step 6.8;
  the diff touches `team/scripts/lib/project_paths.sh` and
  `team/scripts/lib/pp_run_ops.sh`, so the release-lifecycle trigger applies).
- Whole-tree `python3 -m py_compile` over every `.py`: clean.
- Whole-tree `bash -n` over every `.sh`: 0 failures.
- `doc-lint.sh` suites `cmd` (pass=8 fail=0), `links` (pass=53 fail=0),
  `fossil-greps` (fail=0), `feature-greps` (**fail=1** — see Finding 2).
- `lint_gate_census_compliance.py` — OK, 31 files checked, 0 offenders, 84
  anchored sites reconciled.

## Code Coverage

- Unit:        line 74.2 % | branch 60.9 %  (10492/14136 stmts, 2630/4316 branches)
- Integration: line 7.6 %  | branch 6.0 %   (1070/14136 stmts, 259/4316 branches)

Read from the JSON `totals` objects at
`/tmp/pgai_kanban_tmp/coverage/{unit,integration}.json`; field names confirmed
against the generated files (`covered_lines` / `num_statements`,
`covered_branches` / `num_branches`). Note the `term` report's `TOTAL` column
reads 71 % for the unit suite — that is the blended statement-plus-branch
figure, not line coverage. Informational only; coverage does not enter the PASS
precondition.

## Findings

### Finding 1: Cold-build failure detail repeats each failing pair once per log occurrence

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (human-readable output only; verdict, ledger-record count and cross-run duplicate detection are all correct)
**Regression:** no — the affected files are byte-identical to their `bba1238` originals (verified with `git show bba1238:<path> | diff -`), the defect is inherent to that upstream code, and nothing in this lineage behaved differently before: there was no lane at all on `ai_main`. Shipping this RC does not make any previously-working behaviour worse.
**Integrity Damage:** no
**Filed As:** BUG-0168-deep-verify-detail-repeats-pairs (Path C-A — filed as BUG: code defect / regression / test failure)

`pgai_agent_kanban.deep_verify.classify.extract_failing_pairs()` returns one
`FailingPair` per regex match over the lane's whole captured output, with no
de-duplication. pytest echoes a failure message several times per failing test
(traceback body, captured-stdout section, short summary), so one failing pair
yields several matches.

The real run recorded on this project's ledger (2026-09-07T06:52:06+00:00,
v2.9.20 @ d37bb68) found three distinct failing pairs and recorded eighteen
entries — each pair exactly six times, six being the number of
`(engine, flavor)` pairs the sweep evaluated. The same duplicated string is
reused in three operator-facing places: the ledger `- Detail:` field (one
~2.5 KB line), the auto-filed `BUG-0167` (eighteen-flavour `Filed By` sentence,
eighteen `## Symptom` bullets, eighteen `## Deep-Verify Pairs` lines), and the
promotion gate's dual-channel refusal, whose ~2.3 KB `message:` pushes its
`remedy:` line off the first screen — the specific thing the dual-channel
contract exists to prevent.

Reproduced deterministically without a container engine:

```
classify_cold_build_result(1, msg * 3).detail.count("docker/rocky9")  ->  3
```

Expected 1. `_extract_built_pairs()` and `_extract_skip_reasons()` share the
shape, so green and all-skipped sweeps will duplicate the same way.
`bug_filer._render_bug_content()` consumes the same un-deduplicated list;
`file_or_cite_bug()`'s `frozenset` normalisation is already correct, which is
why cross-run duplicate-bug detection still works.

### Finding 2: doc-lint `feature-greps` demands documentation for an image flavour the tree does not ship

**Category:** pre-existing-bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (a lint suite outside both gated runners; costs signal, not correctness)
**Regression:** no — `team/scripts/harness/doc-lint.sh` is untouched by this RC (`git diff --name-only ai_main..ai_rc/v2.9.21 -- team/scripts/harness/` is empty) and the `ubi9` token is already present on `ai_main` at line 167.
**Integrity Damage:** no
**Filed As:** BUG-0169-doc-lint-ubi9-token-undocumented (Path C-A)

`bash team/scripts/harness/doc-lint.sh --suite feature-greps` reports
`fail=1`: no audited doc contains `ubi9`. The tree ships
`docker/claude-abliterated`, `docker/debian` and `docker/rocky9` — there is no
`ubi9` flavour anywhere in the tree, so the demand cannot be honestly
satisfied. The other three doc-lint suites are clean. Not collected by either
gated runner, so the suite verdict is unaffected.

### Finding 3: BUG-0162 — unit tests no longer read the operator's live bug ledger

**Category:** pass

The five previously-red tests are green. A committed fixture ledger exists at
`team/tests/fixtures/bug_ledger/` (BUG-0023..BUG-0027, the records behind
KI-1.0.0.1..5). A tree-wide scan for tests resolving a path into
`$PGAI_AGENT_KANBAN_ROOT_PATH/projects/*/bugs/` returns nothing. The gated
unit runner exits 0 against a live kanban root whose `bugs/` directory does not
contain those records — which is precisely the condition that made the suite
red on every release.

### Finding 4: BUG-0163 — CHANGELOG.md is a faithful render of the real ledger, and a guard now enforces it

**Category:** pass

- `python3 team/scripts/lint_changelog_freshness.py --changelog CHANGELOG.md`
  exits 0 with no `--bugs-dir` override — i.e. against the project's real
  ledger. The `changelog_freshness` pre-flight is `OK` on both gated runners
  and both findings files are empty.
- `git diff --stat ai_main..ai_rc/v2.9.21 -- CHANGELOG.md` is empty. The
  inverted fix that BUG-0163 was filed against (committing a changelog rendered
  from fixtures) has not recurred.
- The render-level guard was verified by triggering it, not by reading it. In a
  scratch copy outside the worktree, changing one sentence in
  `team/tests/fixtures/bug_ledger/BUG-0023-*.md` made
  `TestWriterRenderReproducesPublishedDisclosures::test_render_reproduces_published_ki_1_0_0_disclosures`
  and `TestFixtureDisclosureProvenance::test_fixture_disclosure_text_matches_published_render`
  fail, naming `KI-1.0.0.1` and printing the rendered-vs-published diff. Both
  are green on the unmutated tree.
- `team/tests/fixtures/published_ki_1_0_0_disclosures.json` reproduces the
  five disclosure sentences from `c2d8056:CHANGELOG.md` byte-for-byte
  (compared directly).

Caveat on one acceptance criterion — see Caveats, item 1.

### Finding 5: BUG-0164 — the unit suite no longer edits a tracked file

**Category:** pass

`team/tests/unit/test_lint_comment_provenance.py` now pins `--prose-dirs` to a
scratch directory alongside `--code-dirs` at every capture site, and a new
regression guard `test_remedy_block_confines_repair_to_the_fixture` plants a
synthetic violation in a tracked file inside a throwaway repo tree and asserts
its bytes are unchanged after the capture-and-execute path runs.

Verified by running the real thing: `git status --short` was empty after a full
`run-unit-tests.sh --coverage`, and `release-notes/v2.9.20.md` held md5
`109066b29caf44fa96e0e0e97b6fd400` before and after. The cross-runner verdict
drift described in the bug is also gone — `comment_provenance` reports `OK` on
both runners on the same tree.

### Finding 6: BUG-0165 — shipped release notes carry no internal identifier

**Category:** pass

`release-notes/v2.9.20.md:33` now reads *"The individual issues found during
this release's verification are tracked on the project's internal bug ledger."*
`grep -nE '(TESTER|CODER|WRITER|PM|CM)-[0-9]{8}' release-notes/*.md` returns
nothing, no allowlist marker was used, and `comment_provenance` reports `OK`
with zero findings on both gated runners. The one-line diff touches nothing
else in the historical notes.

### Finding 7: BUG-0166 — the DEEP-VERIFY tier is restored and works

**Category:** pass

Ported code fidelity: all six `team/pgai_agent_kanban/deep_verify/*.py` modules,
both shell readers, and three of the four lane verbs are **byte-identical** to
their `bba1238` originals (`git show bba1238:<path> | diff -`).
`run-cold-build-tests.sh` and `team/tests/cold/test_docker_cold_build.py` differ
only by the deliberate removal of the pinned-CLI-version pre-flight and its two
dependent fixtures — `docker/cli-versions.env` and the matching Dockerfile
`ARG` guards do not exist on this lineage, and the omission is documented in
both files and in the census. All six scripts are mode 755, pass `bash -n`, and
all Python compiles.

Each acceptance criterion from BUG-0166, exercised:

- `deep-verify.sh --help`, `cold-build-now.sh --help`,
  `deep-verify-freshness-check.sh --help` and `run-cold-build-tests.sh --help`
  all exit 0 and name every flag they accept, with exit-code contracts stated.
- A real `deep-verify.sh --project pgai-agent-kanban` run (performed by
  CODER-20260907-006 against live docker and podman) appended exactly one
  ledger record carrying timestamp, version, commit, verdict and per-pair
  detail.
- `deep_verify_ledger_read_latest "pgai-agent-kanban"` returns 0 with
  `found=true` and a parseable record, so this report writes Form A rather than
  Form B.

Guard logic triggered rather than assumed (Step 6.5):

| Guard | Trigger | Result |
|---|---|---|
| Promotion gate refuses a non-green lane | `deep-verify-freshness-check.sh --project pgai-agent-kanban --version v2.9.21` | exit 1, `error: freshness_gate.failed_to_build`, plain message, runnable remedy |
| Promotion gate refuses on the recorded version too | same, `--version v2.9.20` | exit 1, same refusal — greenness is checked before version |
| Unregistered project refused, nothing written | `deep-verify.sh --project no-such-project-xyz` | exit 1, `error: deep_verify.unregistered_project`, remedy names `add-project.sh`; ledger md5 unchanged |
| Scheduled self-gate costs nothing when not due | `deep-verify.sh --scheduled --project pgai-agent-kanban` | exit 0, one narration line naming the off-hours window and the clock; no lane, no engine, no ledger write |
| Per-project switch honours `off` | sandbox kanban root, `[release] deep_verify = off` | `pp_deep_verify_mode` -> `off`; window override `05:00-05:30` honoured; absent -> `weekly` / `03:00-03:59`; `bogus` -> exit 1, nothing on stdout, stderr names the accepted vocabulary |

The ledger was byte-identical (md5 `0a9f07d70d684602da14ddf39566ad9c`) before
and after all of the above.

### Finding 8: BUG-0166 documentation half — governance and operator docs restored

**Category:** pass

- `team/SOP.md` carries "The DEEP-VERIFY tier", "The three triggers", "The
  per-project switch", "A stale green is a weaker claim than a fresh one", "No
  silent skips", and the sibling "Unusable build blocks; an unavailable build
  environment does not".
- `team/roles/TESTER.md` carries Step 5d with the Form A/B/C schemas and the
  matching `## Deep-Verify Freshness` entry in the Step 9 field list and report
  template. (This report is written against those forms.)
- `docs/operator-commands.md` documents all three verbs, every flag, exit
  codes, ledger destination and the per-project switch. Every documented flag
  was checked against the shipped `--help`.
- `docs/promotion-playbook.md` carries step 0 and the per-refusal walkthrough.
- `docs/blocking-site-census.md` carries 11 Locator Index entries. Seven were
  spot-checked: every one points at the exact refusal line, with its
  `# CENSUS-ANCHOR:` marker on the line immediately above. The census lint
  passes with 0 offenders. The line-number index form (rather than token form)
  is the form this tree's `_CENSUS_LOCATOR_LINE_RE` actually parses, is
  documented as such in the census, and follows the existing
  `lint_engine_size_dupe.py` precedent. The count is 11 rather than
  `bba1238`'s 13 because the two pinned-CLI-version anchors were deliberately
  not carried — consistent with the code.

### Finding 9: Pre-flight, structural and constraint checks

**Category:** pass

- **Pre-flight merge verification:** every task directory scanned; all 26 DONE
  tasks with a named feature branch have had that branch deleted post-merge
  (normal lifecycle), so both checks are skipped for them per the role file. No
  UNMERGED and no BUG-92 zero-commit anomalies. Worktree HEAD `443bdd6` equals
  `ai_rc/v2.9.21`, `git status --short` empty, `git stash list` empty.
- **release-state.md (Step 6.1):** `Active RC: v2.9.21` with a matching
  `ai_rc/v2.9.21` branch; `RC Opened By Task: CM-20260907-010-open-rc-2-9-21`;
  `Last Released: v2.9.20`, well-formed. `pp_last_released_version` returns
  `v2.9.20` and `semver_gt v2.9.21 v2.9.20` holds. No drift.
- **Prereq graph (Step 6.10):** all 11 referenced task IDs resolve to real task
  folders; no cycles; the CM-open bookend at 010 and the CM-release bookend at
  013 depend on TESTER-011, which depends on the eight implementation tasks;
  sequence numbers non-colliding.
- **Skip-cites-real-bug (Step 6.13):** zero placeholder skip IDs and zero cited
  bug IDs anywhere under `team/tests/` (the only `BUG-NNNN` literal hits are
  inside archived coverage logs under `team/tests/artifacts/`). The gated
  runners' own `skip_cites_real_bug` pre-flight is `OK` on both.
- **Constraint negatives (Step 6.9):** all four new scripts carry
  `set -euo pipefail`; no hardcoded credentials in the diff; no bare `/tmp/`
  literal added by the diff.
- **Coding-standards spot-check (Step 6.14):** all four new scripts ship a
  `--help` that names every flag and states exit codes; the freshness window is
  operator-overridable via `PGAI_DEEP_VERIFY_FRESHNESS_WINDOW_DAYS` rather than
  hardcoded; the scheduled trigger's two knobs are read through new
  `pp_*` helpers in the existing `project_paths.sh` rather than reimplemented.
  Two nits recorded under Caveats.

## Lint Findings (non-blocking)

None.

`/tmp/pgai_kanban_tmp/lint/findings-unit.json` and
`/tmp/pgai_kanban_tmp/lint/findings-integration.json` were both written by this
run's gated runners and both contain `"findings": []`. Every quality-class
pre-flight reported `OK` on both runners. Nothing to cite and nothing to file
under Step 5c.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=0d freshness=stale — the lane ran today against a mid-RC tree state and three of six engine/flavor pairs failed to build (both engines on claude-abliterated, whose Dockerfile line 3 reads `FROM FROM`, plus podman/rocky9 at its `COPY --chmod` step); cites BUG-0167, filed by that deep-verify run; the record is stale only on the version axis (it names v2.9.20, not the v2.9.21 under verification), not on age.

Per Step 5d this is a fact reported, not a verdict produced: the red record is
cited rather than re-filed, and neither it nor its staleness moves this RC's
`## Recommendation` or `## Systemic Risk`. Its downstream consequence is noted
under Caveats.

## Autonomous criterion

Manual interventions required: **none**.

1. **Any task force-promoted from WAITING to BACKLOG by hand?** NO. One task,
   `CODER-20260907-003-changelog-fixture-render-guard`, was reset by the wake
   script's own stale-active reconciliation
   (`[2026-09-07T05:37:14+00:00] wake(coder): stale-active: ... has no active
   process, resetting to [ ]/BACKLOG`) after its first session ended without
   writing a terminal state. The framework tore down the stale worktree, re-ran
   the task, and the second session verified the already-merged work and closed
   DONE. That is autonomous recovery working as designed, not a human edit.
2. **`release-state.md` edited outside the CM scripts?** NO. Its mtime is
   `2026-09-07 04:46:14`, matching `cm-open-rc.sh`'s own timestamp in the file.
3. **Scripts patched mid-build?** NO. Every commit between `ai_main` and the RC
   head is either a feature-branch commit or its `--no-ff` merge; there are no
   direct commits to the RC branch.
4. **Queue files manually edited?** NO. All five queue files carry markers
   consistent with their tasks' states, and their mtimes align with wake
   firings.

No `HALT`, `HALT-AFTER` or `SUBSTRATE_BROKEN` flag exists at either scope. All
eight implementation tasks closed DONE through the wake script
(`finished task ... with state DONE` in the cron logs).

## Caveats

1. **BUG-0162's byte-identity acceptance criterion is satisfied at the
   disclosure level, not the whole-file level.** The criterion asks that every
   fixture record under `team/tests/fixtures/bug_ledger/` be byte-identical to
   its `c2d8056` original. Those bug files were never in the dev tree — they
   are kanban process state, and `git show c2d8056:projects/.../bugs/<file>`
   resolves to nothing in this repository, so whole-file byte-identity is not
   verifiable from git and the criterion as written cannot be met. The
   implementation did the strongest available thing: it byte-compares each
   fixture's rendered disclosure sentence against
   `git show c2d8056:CHANGELOG.md` (`TestFixtureDisclosureProvenance`), which
   is the property BUG-0163 actually cared about, and I confirmed the five
   sentences match byte-for-byte. Recorded as a caveat rather than a gap
   because the substance is met and the shortfall is in the criterion.

2. **The v1.0.0 Known Issues disclosures are absent from the current
   CHANGELOG.md.** `grep '^KI-1.0.0' CHANGELOG.md` returns nothing, where
   `c2d8056` carried five lines. This is pre-existing and out of this RC's
   scope: `CHANGELOG.md` is untouched by the RC, the live bug ledger no longer
   carries BUG-0023..0027, and the freshness gate confirms the artifact is a
   faithful render of the ledger as it stands. BUG-0163's acceptance criterion
   explicitly permits this case ("or the lines are absent because the real
   ledger no longer carries those records"). Worth an operator's attention
   nonetheless: published disclosures currently vanish from the changelog when
   their source records leave the ledger.

3. **Downstream consequence of the red deep-verify record, for CM.**
   `docs/promotion-playbook.md` step 0 is a *public-promotion* gate, not part
   of `cm-release.sh` — I confirmed nothing in `team/scripts/` or
   `team/roles/CM.md` invokes `deep-verify-freshness-check.sh`, and this project
   runs `push_to_remote = false`. So the internal release of v2.9.21 is not
   gated on the lane. But an operator who later promotes publicly will be
   refused until BUG-0167's Dockerfile defects are fixed and the lane is re-run
   green for the version being promoted. That is the gate doing its job; it is
   recorded here so the refusal is not a surprise.

4. **`deep_verify = off` in `projects/pgai-agent-kanban/project.cfg` is not in
   the `[release]` section.** It sits at the end of the file, after
   `[training]`, so `pp_deep_verify_mode` reads it as absent and resolves to
   the `weekly` default — which is why the scheduled dry run narrated "outside
   off-hours window" rather than "switched off". The reader is correct: in a
   sandbox with the key under `[release]` it returned `off` correctly. This is
   kanban process state, not part of the shipped tree, and its mtime
   (2026-09-03) predates this RC, so it is an observation rather than a
   finding. If the operator intended the tier off for this project, the key
   needs to move under `[release]`.

5. **Two `--help` nits.** `deep-verify.sh`'s `--scheduled` row and
   `deep-verify-freshness-check.sh`'s `--version` row carry no inline
   description, unlike every other row in the same tables. Both flags are named
   (so `help_presence` passes and coding-standards directive 6 is satisfied)
   and both are fully described in the prose immediately below. Inherited
   byte-identically from `bba1238`. Cosmetic; not filed.

6. **Bug-ID references in test docstrings.** Several new tests name the bug
   that motivated them in a docstring or assertion message. The provenance lint
   does not scan `team/tests/`, so this is not a lint finding, and the practice
   is long-established here: `ai_main` already carries 632 such references
   across 65 test files, and this RC adds about 42 more. Noted as an
   observation on a pre-existing convention, not filed as a defect against this
   RC.

7. **Test fidelity.** The RC's own tests exercise real strict-mode bash
   (`bash -n` plus real `--help` invocations of the shipped scripts), a real
   installed-shape check (`test_deep_verify_installed_shape.py`), real
   multi-project fixtures in the scheduled-dispatch tests, and — for the
   confinement guard — a throwaway repo tree rather than a mock. The one
   fidelity gap I can point at concretely is the classifier's fixtures never
   containing a repeated failure message, which is exactly the blind spot
   Finding 1 fell through; it is called out in BUG-0168's acceptance criteria.
   No state-mutating script was run against the live install: the sandbox
   exercises used a throwaway `KANBAN_ROOT` under
   `$PGAI_AGENT_KANBAN_TEMP_DIR`, and the three live-root invocations
   (`deep-verify-freshness-check.sh`, the unregistered-project refusal, and the
   not-due `--scheduled` dry run) are all read-only or no-ops, verified by an
   unchanged ledger md5.

8. **No prompt-injection content encountered.** Nothing in the RC's diff, the
   requirements doc, or the bundled bug files contained instruction-shaped text
   directed at the reading agent.

## Path C filings

| ID | Path | Severity | Finding |
|---|---|---|---|
| BUG-0168-deep-verify-detail-repeats-pairs | `projects/pgai-agent-kanban/bugs/` | low | Finding 1 |
| BUG-0169-doc-lint-ubi9-token-undocumented | `projects/pgai-agent-kanban/bugs/` | low | Finding 2 |

Both filenames pass discovery's `ledger_filename_re("BUG")` gate with no
defect classification. Cited-not-filed: **BUG-0167** (open), which the
deep-verify run filed for the Dockerfile build failures.

## Recommendation rationale

Both gated runners exited 0, all five bundled items are addressed and
behaviourally verified, the autonomous criterion is clean, and no lint findings
exist. That satisfies every element of the pass-verdict checklist except one:
two Step-7 defect findings were recorded and filed. Per Step 8's decision tree,
any filed `bug` or `pre-existing-bug` finding elevates the recommendation off
PASS, and neither of these is severe — one garbles operator-facing text in a
newly-restored lane, the other is a pre-existing red lint suite outside both
gated runners. Neither prevents anyone from operating the system. That is
`SHIP-WITH-CONCERNS` with `Systemic Risk: low`.
