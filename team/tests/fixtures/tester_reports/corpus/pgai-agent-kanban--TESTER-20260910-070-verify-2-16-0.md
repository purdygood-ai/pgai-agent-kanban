# Verification Report: rc/v2.16.0

**Task:** TESTER-20260910-070-verify-2-16-0
**Project:** pgai-agent-kanban
**RC branch:** `ai_rc/v2.16.0` @ `8881091` (worktree HEAD verified identical)
**Base:** `ai_main` @ `b43f7d4` (also the merge-base — no mid-RC commits on main)
**Requirements:** `projects/pgai-agent-kanban/requirements/v2.16.0-hygiene-lint-consolidation.md`
**Verified:** 2026-09-11

## Summary

v2.16.0 consolidates the shop's provenance/hygiene linting into one entry
point (`team/scripts/lint_hygiene.py`, three registered classes), replaces
in-file suppression markers with a checked-in line-level allowlist sidecar,
re-keys the blocking-site census Locator Index off names instead of line
numbers, adds an aggregate bug filer that writes at most one bug per hygiene
class per run, and audits the tree's grep-zero gates. I verified the worktree
identity and cleanliness, ran both gated runners to completion, ingested the
lint findings files, read the deep-verify ledger, then worked the four
deliverables and the single acceptance criterion behaviourally — seeding the
named false-positive corpus, seeding a true positive in each of the three
classes, exercising the sidecar's refusals and its content-hash drift
behaviour, shifting a censused refusal by 60 lines, and injecting an
unresolvable census pin. Every deliverable is met. Two minor defects surfaced
in the machinery around the lint rather than in the lint itself: a malformed
sidecar aborts both gated runners before pytest, and the remedy commands the
filer persists into a bug report name an ephemeral worktree that is deleted by
the time anyone reads them.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

low

## Test Suites

| Suite | Command | Exit | Result |
|---|---|---|---|
| Unit | `bash team/scripts/run-unit-tests.sh --coverage` | **0** | 3798 passed, 3 skipped in 520.56s |
| Integration | `bash team/scripts/run-integration-tests.sh --coverage` | **0** | 442 passed, 1 skipped in 421.77s |

Both gated runners exited 0. The PASS evidence cited here is the gated
runners' own exit codes, not a direct-pytest summary line. Post-run cleanliness
gates (`bare_tmp_cleanliness`, `listener_cleanliness`) reported OK on both.
The 4 skips are environment-conditional (absent docker/tmux), not deferrals;
none cites a bug ID, and the skip-cites-a-real-bug grep-gate (Step 6.13) found
no placeholder IDs and no unresolvable `BUG-NNNN` citations.

## Code Coverage

- Unit:        line 75%  |  branch 62%   (14906 stmts, 4490 branches; blended TOTAL 72%)
- Integration: line 8%   |  branch 6%    (blended TOTAL 7%)

Read from `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/{unit,integration}.json`
`totals` (`percent_statements_covered` / `percent_branches_covered`).
Informational only; coverage does not enter the recommendation.

## Findings

### Finding 1: Deliverable 1 — one hygiene lint with three classes, line-level sidecar, anchored version matcher

**Category:** pass

`team/scripts/lint_hygiene.py` exists, is executable, and `--help` names every
argument. Its registry carries exactly the three classes the deliverable
names: `provenance` (ticket/version citations in shipped code and prose),
`version-named-test`, and `comment-drift`. The lint it consolidates,
`team/scripts/lint_comment_provenance.py`, and its 1737-line test module are
deleted from the tree; no executable reference to either remains anywhere in
`team/scripts/`, `team/pgai_agent_kanban/`, or the repo-root shell scripts
(the surviving mentions are historical audit records, release notes, and
docstrings describing the retirement).

Behaviourally verified against a seeded corpus under
`$PGAI_AGENT_KANBAN_TEMP_DIR`:

- **In-file markers suppress nothing.** A file carrying
  `# provenance-allowlist: deliberately retained for context` both in a
  docstring and as a bare comment still produced both of its provenance
  findings. This is the specific failure the deliverable names ("never
  allowlist directives pasted into docstrings").
- **Suppression is line-level.**
  `--add-suppression code/inline_marker.py 2 provenance "<justification>"`
  wrote one sidecar entry (`class`, `file`, `content_hash`, `line_hint`,
  `justification`) and the re-run dropped exactly the suppressed line's
  finding, leaving the other line's finding intact.
- **A whole-file exemption cannot be expressed.** A sidecar entry omitting
  `content_hash` is refused dual-channel:
  `error: lint.hygiene-sidecar-malformed` / `... is missing required field(s):
  content_hash`. A blank `justification` is refused the same way.
- **Content-hash anchoring drifts as documented.** Editing the suppressed
  line's own text made the finding reappear (2 findings, up from 1), while
  edits elsewhere in the file do not disturb it.
- **The version matcher is anchored.** A seeded corpus containing the exact
  cases the acceptance criterion names ran clean — `qv50` and `qv50.2` as
  identifiers, an upstream `v2.3.2-note` slug, a release-notes prose file
  carrying `BUG-0010` / `PRIORITY-0002` / `v1.2.3-widget-name`, and a test
  module using `"BUG-0001-foo.md"` and a full task ID as ordinary string
  values. Result: `EXIT=0`, `0 findings`.

All 7 entries in the checked-in `team/scripts/hygiene-lint-allowlist.json`
resolve: each stored `content_hash` matches the SHA-256/12 fingerprint of the
line at its own `line_hint` in `team/scripts/harness/engine-behavior-baseline.sh`,
and each carries a non-empty justification. The lint run against the full RC
tree is clean: `lint_hygiene: OK — 0 findings across 7 scan director(y/ies)`.

Robustness spot-checks: a latin-1-encoded source file and a permission-denied
file both produce findings or clean output rather than a crash, exit 0.

### Finding 2: Deliverable 2 — census pins are line-independent and unresolvable pins are informative findings

**Category:** pass

The `CENSUS-LOCATOR-INDEX` block in `docs/blocking-site-census.md` carries 119
entries and **zero** line-number keys — every entry is `path:<name> CLASS`,
where the name is either a dual-channel `error` key or a `# CENSUS-ANCHOR:`
token. The document's old "Trade-off (documented, not hidden)" passage about
stale line numbers is replaced by a "Line independence" passage describing
`_scan_locator_resolution`.

Behaviourally verified on a scratch copy of the RC tree:

- **Line independence.** Inserting 60 lines near the top of
  `team/scripts/harness/doc-lint.sh` — shifting its two censused refusals —
  left the census lint clean:
  `lint_gate_census_compliance: OK -- 34 file(s) checked, 0 offenders`, exit 0.
  Under the retired line-number scheme this would have produced two findings.
- **Unresolvable pin is an informative, non-blocking finding.** Injecting
  `team/scripts/lint_hygiene.py:this-token-does-not-exist-99 CONVERT` into the
  index produced exactly one finding with `"kind": "unresolved_locator"`,
  `"severity": "quality"`, a cause naming all three resolution mechanisms it
  tried, and a remedy naming the two ways to fix it — with the process still
  exiting **0**.

On the unmodified RC tree the census lint reports 119 census-anchored sites
exempted, 34 files checked, 0 offenders. Anchor/index pairing is complete: the
four new anchors (`run-gated-tests-env-bootstrap-01`,
`run-gated-tests-pytest-missing-01`, `run-gated-tests-pytest-failure-01`,
`hygiene-lint-sidecar-01`) each have exactly one index entry, and the only
markers in the tree without an index entry are synthetic fixture tokens inside
the census lint's own test module.

### Finding 3: Deliverable 3 — one aggregated bug per class per run

**Category:** pass

Verified end to end against a throwaway bugs directory. Seeded 4 findings
across 3 classes (2 x `provenance`, 1 x `version-named-test`, 1 x
`comment-drift`), wrapped them in the gated-runner findings-file schema, and
ran `python3 -m pgai_agent_kanban.lint.bug_filer`:

```
class=comment-drift      outcome=filed  bug_id=BUG-0001  count=1
class=provenance         outcome=filed  bug_id=BUG-0002  count=2
class=version-named-test outcome=filed  bug_id=BUG-0003  count=1
```

Three bugs for four instances — one per class, with both provenance instances
listed inside the single provenance bug as `<path>:<line> — cause` lines in
both `## Actual` and `## Findings`, and both files named under
`## Files Involved`. The rendered report follows the project's bug template and
carries the dedup key section `## Hygiene Lint Class`.

Guard behaviour exercised in both directions:

- **Re-running dedups.** A second run over the same findings returned
  `outcome=duplicate` for all three classes with `bug_path=` empty, and no new
  file was written.
- **A closed bug does not suppress re-filing.** Flipping the provenance bug's
  `## Status` to `done` and re-running filed a fresh `BUG-0004` — the
  cite-an-*open*-bug rule, not cite-any-bug.
- **Zero findings file zero bugs.** An empty findings file returns an empty
  result list and writes nothing.

The production path is wired and demonstrably worked during this very release:
`BUG-0236-hygiene-lint-provenance.md` was filed automatically by the unit
runner during CODER-20260910-066's own pass, naming
`team/tests/unit/test_lint_gate_census_compliance.py:1216`; commit `ed50b56`
on this RC reworded that line, and the lint now runs clean over the whole tree.

The lint never blocks on a *finding*: 4 seeded findings across 3 classes still
exited 0. The one non-zero exit path is Finding 6 below.

### Finding 4: Deliverable 4 — grep-zero gates target behaviour, never truncate

**Category:** pass

Independently swept the eleven gates the audit enumerates
(`test_grep_zero_gate.py`, `test_dashboard_sys_path_live_only.py`,
`test_describe_tag_filter.py`, `test_overwatch_lock_halt_protocol.py`,
`test_pseudocron.py`, `test_changelog_writer_title_fix.py`,
`test_lock_provenance.py`, `test_harness_repo_lock_provenance.py`,
`test_pp_run_ops_sweep.py`, `lint_lib_function_dedupe.py`,
`lint_upgrade_no_temp_copy_guard.py`) for `| head`, fixed result slices, and
early breaks: no hits. The `| head` occurrences elsewhere in `team/scripts/`
(`verify-install-policy.sh`, `verify-config-loader.sh`,
`run-integration-tests.sh:452`) are first-match value extraction into a shell
variable, not grep-zero assertions, and are out of the deliverable's scope.

The five gates that are inherently identifier-targeted each carry a rationale
paragraph at their own site, and `docs/test-authoring-guidelines.md` gained the
two rules ("Target the behavior, not the name", "Read the whole subject") plus
the negative-proof requirement.

### Finding 5: Acceptance criterion — false-positive corpus clean, one aggregate bug per seeded class

**Category:** pass

The checked-in corpus (`team/tests/unit/test_lint_hygiene_corpus.py`) covers
seven false-positive shapes across all three classes at once and one seeded
true positive per class, each asserting `len(findings) == 1`. All of it runs
green inside the unit suite.

I did not rely on the checked-in corpus alone. My own corpus, built from the
four shapes the acceptance criterion names by name — `qv50`, an upstream
`v2.3.2`-style slug, release-notes prose, and fixture strings — produced
`0 findings`, and my own seeded true positives produced exactly one aggregate
bug per class as shown in Finding 3.

### Finding 6: Malformed hygiene sidecar aborts both gated runners before pytest

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (no runtime-failure risk; the failure is loud, immediate, and self-inflicted by an edit to one checked-in JSON file)
**Regression:** yes (this RC introduced both the sidecar and the bare invocation; no such stop existed before)
**Integrity Damage:** no
**Filed As:** BUG-0237 (Path C-A — filed as BUG, code defect / regression / test failure)

The v2.16.0 deliverables state "lint never blocks a task or RC." Every other
quality lint in both runners goes through `gated_runner_run_quality_lint`,
which swallows its exit code. `lint_hygiene.py` is invoked bare, under
`set -euo pipefail`, with no `|| true`:

```
team/scripts/run-unit-tests.sh:516        python3 scripts/lint_hygiene.py --findings-file "$_RU_HYGIENE_LINT_FINDINGS"
team/scripts/run-integration-tests.sh:508 python3 scripts/lint_hygiene.py --findings-file "$_RI_HYGIENE_LINT_FINDINGS"
```

It returns 2 on a malformed sidecar. Reproduced on a scratch copy of the RC
tree with one blank-justification entry appended to the checked-in allowlist:

```
$ bash team/scripts/run-unit-tests.sh
Running hygiene lint...
lint_hygiene: scanning 5 code dir(s), 1 test-tree dir(s), 1 prose dir(s); classes=['provenance', 'version-named-test', 'comment-drift']
error:   lint.hygiene-sidecar-malformed
message: sidecar .../hygiene-lint-allowlist.json entry #7 (...) has a blank justification -- every suppression must carry a non-empty reason
remedy:  fix the entry in "..." named above, or remove it, then re-run: python3 ".../lint_hygiene.py"
EXIT=2
```

Zero tests ran. No findings file was written either — the
`CLEANUP_ON_EXIT_PRE_HOOK` that writes it is installed ~140 lines later, at
`run-unit-tests.sh:656`, so the abort happens before the hook exists.

Two things make this worth filing rather than accepting as the documented
exception it is presented as. First, the requirements granted no exception.
Second, exit code 2 is already spoken for: `team/roles/TESTER.md` Step 6.6 maps
a gated-runner exit of 2 to "pytest not installed" and instructs TESTER to set
BLOCKED with that blocker text — so a typo in a suppression file would halt the
chain under a reason that is demonstrably false, sending an operator to look
for a pytest that is installed and working.

The site is honestly classified: `# CENSUS-ANCHOR: hygiene-lint-sidecar-01`
with an `AGENT-RESOLVABLE` class comment, census row 57, a Locator Index entry,
and a complete dual-channel contract. The classification is defensible for a
CODER who can fix the file in-session; it is the *exit-code channel* and the
runner-side `errexit` exposure that make it a gap rather than a clean
AGENT-RESOLVABLE refusal.

### Finding 7: Remedy commands persisted into auto-filed bugs name a deleted worktree

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (the bug report still names the file and line; only the copy-pasteable command is dead)
**Regression:** yes (both the remedy format and the filer that persists it are new in this RC)
**Integrity Damage:** no
**Filed As:** BUG-0238 (Path C-A — filed as BUG, code defect / regression / test failure)

`lint_hygiene.py` builds every remedy from `Path(__file__).resolve()` plus the
resolved sidecar and repo-root, so each command carries the absolute path of
the tree the lint ran in. In this kanban that is an ephemeral per-task
worktree. `pgai_agent_kanban.lint.bug_filer._remedy_line` copies the string
verbatim into the filed bug's `## Remedies` section, where it outlives the
directory it names.

The production instance is already on disk. `BUG-0236-hygiene-lint-provenance.md`
line 43 reads, in part:

```
... python3 "/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/CODER-20260910-066-census-anchors-line-free/team/scripts/lint_hygiene.py" --add-suppression ... --sidecar "/tmp/.../CODER-20260910-066-census-anchors-line-free/team/scripts/hygiene-lint-allowlist.json" --repo-root "/tmp/.../CODER-20260910-066-census-anchors-line-free"
```

```
$ ls -d /tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/CODER-20260910-066-census-anchors-line-free
ls: cannot access '...': No such file or directory
```

Running it yields `No such file or directory`. This cuts against the filer's
own stated purpose — one rich bug CODER can act on without re-running the lint
— and against `team/SOP-core.md` "Gates and Refusals", which requires "a
runnable `remedy` a stranger can act on".

### Finding 8: Documentation layer read in full

**Category:** pass-with-caveat

Read the full diffs of `docs/coding-standards.md` (new "One hygiene lint
enforces directive 8" section and "The allowlist sidecar" subsection),
`docs/test-authoring-guidelines.md` (version-named-test rule, grep-zero rules),
`docs/blocking-site-census.md`, and the four role-file edits. Every claim I
checked matches observed behaviour: the three-class table, the anchoring
narrowing, the five sidecar fields, the content-hash drift semantics, the
"marker comment suppresses nothing" statement, and the two version-named-test
narrowings (position and subject reappearance) are all accurate.

The commands the role files now instruct agents to run were executed as
written and work:

- `python3 team/scripts/lint_hygiene.py --classes provenance --prose-dirs release-notes`
  (CM.md) → exit 0, clean.
- `python3 team/scripts/lint_hygiene.py --add-suppression <file> <line> provenance "<justification>"`
  (CODER.md / WRITER.md) → writes a valid entry.

**Caveat.** `docs/test-authoring-guidelines.md` says each
identifier-by-necessity gate carries "a rationale paragraph beginning
`IDENTIFIER-BY-NECESSITY:`" (with a colon). Three of the five sites
(`lint_lib_function_dedupe.py:41`, `lint_upgrade_no_temp_copy_guard.py:21`,
`test_grep_zero_gate.py:31`) use an underlined section-heading form with no
colon; two (`test_pseudocron.py:300`,
`test_overwatch_lock_halt_protocol.py:357`) use the colon form. A reader
grepping for the documented spelling finds two of five. Cosmetic; not filed.

### Finding 9: Release-lifecycle and structural checks

**Category:** pass

- **Step 6.1 drift.** `pp_last_released_version pgai-agent-kanban` → `v2.15.4`;
  `semver_gt v2.16.0 v2.15.4` holds. `release-state.md` carries
  `Active RC: v2.16.0`, `RC Opened By Task: CM-20260910-069-open-rc-2-16-0`,
  and a well-formed `Last Released: v2.15.4`. The `ai_rc/v2.16.0` branch
  exists — no state drift.
- **Step 6.8 synthetic release simulation.** The RC touches release-lifecycle
  files (`team/scripts/lib/cm/*.sh`, `cm/finalize-release.sh`, `ship-rc.sh`),
  so the trigger condition holds. `tests/integration/test_cm_release_synthetic.py`
  ran inside the gated integration suite: 15 tests, all passed.
- **Step 6.9 constraint negatives.** No hardcoded secrets in the new modules.
  No bare `/tmp` literals introduced outside the framework temp root — the one
  `/tmp/some_specific_path` hit is a labelled BAD example inside the
  anti-pattern documentation.
- **Step 6.10 prereq graph.** `CM-069` (no prerequisites) → six CODER tasks and
  one WRITER task, each naming `CM-069` → `TESTER-070` → `CM-072`, which names
  all eight predecessors. No cycles, every referenced ID resolves to a real
  task folder, bookends correctly linked. The numeric suffixes are not
  chronological (CM-open is 069 while the CODER tasks are 062–067); ordering is
  driven by prerequisites, not by the suffix, so this is an ID-assignment
  quirk, not a graph defect.
- **Pre-flight merge verification.** Every DONE task in the project was scanned;
  no unmerged feature branch and no zero-commits-ahead anomaly. All seven
  prerequisite tasks are DONE with `Needs Human: no`.
- **Worktree identity.** `git rev-parse HEAD` == `git rev-parse ai_rc/v2.16.0`
  == `8881091`; `git status --short` empty; `git stash list` empty.

### Finding 10: Spot-check outside the spec

**Category:** pass-with-caveat

Time-boxed. Three observations, none rising to a finding:

1. `_FILE_WHITELIST` in `lint_hygiene.py` is keyed on **basename**, not
   repo-relative path, so any future file anywhere in the tree that happens to
   be named `changelog_writer.py`, `promote_bundled_items.py`,
   `lint_hygiene.py`, or `test_lint_hygiene*.py` inherits the exemption
   silently. The current set is small and the names are distinctive, so this is
   noted rather than filed.
2. `BUG-0236` remains `## Status: open` even though the provenance instance it
   describes was fixed on this same RC (commit `ed50b56`), and the tree now
   lints clean. Nothing in the filer or the overwatch resolution matrix closes
   a hygiene bug whose findings no longer reproduce, so the next discovery
   iteration will bundle a bug with nothing left to fix. This is the loop's
   normal open→running→done path and not a deviation from the spec — the
   deliverable asked for aggregate filing, which was delivered — so it is
   recorded here rather than filed.
3. Test-fidelity check on the RC's own new tests: `test_lint_hygiene.py` (726
   lines), `test_lint_hygiene_corpus.py` (248), `test_lint_bug_filer.py` (317),
   `test_lint_bug_filer_wiring.py` (246), and
   `test_lint_gate_census_compliance.py` (+267) all materialise fixtures under
   `tmp_path` and pass explicit `--repo-root` / `--sidecar` / bugs-dir
   arguments, so none of them touches the live install. My own ad-hoc
   reproductions were likewise confined to
   `$PGAI_AGENT_KANBAN_TEMP_DIR/projects/pgai-agent-kanban/tester-070` and a
   scratch copy of the worktree; the live `$PGAI_AGENT_KANBAN_ROOT_PATH` tree
   and `projects.cfg` were not mutated.

## Lint Findings (non-blocking)

None.

Both gated-runner findings files were written and both carry an empty
`findings` array:

- `${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-unit.json` — `"findings": []`,
  generated 2026-09-11T02:43:10+00:00 by `run-unit-tests.sh` (0 findings).
- `${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-integration.json` —
  `"findings": []`, generated 2026-09-11T02:50:31+00:00 by
  `run-integration-tests.sh` (0 findings).

An independent full-tree run of `python3 team/scripts/lint_hygiene.py` also
reported `0 findings across 7 scan director(y/ies)`, and
`python3 team/scripts/lint_gate_census_compliance.py` reported `0 offenders`.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=3d freshness=stale — the docker/claude-abliterated and podman/claude-abliterated pairs failed on a malformed Dockerfile `FROM` and podman/rocky9 failed at its `COPY --chmod` step; cites BUG-0167, filed by that deep-verify run, and the record is stale because it was taken at v2.9.20 rather than the v2.16.0 under verification (its 3-day age is inside the 7-day window).

Reported, not re-derived: the lane was not run during this verification, no
container engine was invoked, and no second bug was filed for the failure.
Per Step 5d this line does not move the recommendation or the systemic risk.

## Autonomous criterion

Manual interventions required: none.

1. **Tasks force-promoted from WAITING to BACKLOG manually?** NO. All seven
   prerequisite tasks and this one carry wake-script promotion notes; no
   hand-edited State transitions are visible in their `status.md` history.
2. **`release-state.md` edited outside the CM scripts?** NO. Its mtime
   (Sep 10 21:51) matches the `RC Opened At` timestamp written by
   `cm-open-rc.sh` (2026-09-10T21:51:18+00:00), and all four fields are in the
   canonical schema shape.
3. **Scripts patched mid-build?** NO. Every commit reachable from the RC and
   not from `ai_main` is on a merged `ai_feature/<TASK-ID>` branch. The
   merge-base equals the `ai_main` tip (`b43f7d4`), and the four commits on
   `ai_main` are the v2.15.4 release commits at 21:41:01–21:41:02, ten minutes
   *before* the RC was opened. No operator commit landed on main mid-RC.
4. **Queue files manually edited?** NO. Markers are consistent with task state
   across all four queues: `[x]` on the seven completed prerequisites and
   `CM-069`, `[A]` on this task, `[W]` on `CM-072` (waiting on this task).

## Caveats

- **Recommendation rationale.** Both filed defects are `low` systemic risk and
  `small` fix effort, neither damages integrity, and neither prevents an
  operator from running the system — Finding 6 requires someone to first
  corrupt a checked-in JSON file, and Finding 7 degrades a copy-pasteable
  command inside a report that still names the exact file and line. That is
  SHIP-WITH-CONCERNS, not SHIP-WITH-SERIOUS-CONCERNS. Both carry
  `Regression: yes` because this RC is what introduced the code paths, so the
  report as a whole reads as carrying a regression; neither regression is of
  previously-working shipped behaviour.
- **Scope of the sidecar refusal.** I verified that no *other* input makes the
  hygiene lint exit non-zero: findings (exit 0), undecodable files (exit 0,
  scanned or skipped), unreadable files (exit 0, reported as a finding). The
  sidecar is the sole non-zero path, which is what keeps Finding 6 at `low`.
- **Deep-verify.** Per Step 5d the cold-build lane was not run and no bug was
  filed for its red record; BUG-0167 (already `done`) is cited, not duplicated.
  The last lane result predates this RC's tree by seven minor versions.
- **Reproduction hygiene.** Every behavioural reproduction ran against seeded
  fixtures or a scratch copy of the worktree under
  `$PGAI_AGENT_KANBAN_TEMP_DIR`. The RC worktree itself was never modified —
  `git status --short` was empty at the start and the tree was only read.
