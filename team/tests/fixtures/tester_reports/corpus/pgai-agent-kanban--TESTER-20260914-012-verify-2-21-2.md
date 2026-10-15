# Verification Report: ai_rc/v2.21.2

## Summary

Fresh verification of `ai_rc/v2.21.2` against
`requirements/v2.21.2-priority-bundle-20260914.md`, which bundles exactly one item,
`PRIORITY-0002-env-coupled-fixture-detector.md`. The RC is small and single-themed:
a shared subprocess-environment builder promoted to `team/tests/fixtures/`, both
golden-comparison harnesses converted onto it, a new quality-class lint (`AP7`)
that reports the golden-comparison-plus-inherited-environment pairing, and the
documentation and survey that give the class a remedy and a durable measurement.
Every acceptance criterion in the priority is met, and each one was exercised
rather than read: the new lint was proven to fire on a production-shaped
reintroduction and to stay silent on both negative controls and on the converted
tree, and its finding was proven to land in a gated runner's findings file. Both
gated runners exit 0, and they exit 0 identically under all three
`MAX_TASK_SECONDS` values the priority names. No prior run's artifacts were read;
every check below was re-run from scratch.

The two findings that keep this off `PASS` are about the chain around the release,
not about what the release built. The RC carries a commit a human wrote by hand
onto the release branch while the lane was halted, which is the autonomous-operation
criterion failing (Step 6.12, check 3); and one quality-class lint instance had no
bug tracking it. Both are filed via Path C (autonomous follow-up filing —
non-blocking). Nothing found makes shipping v2.21.2 worse than not shipping it.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

medium

## Verification Context

- Worktree: `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260914-012-verify-2-21-2`
- HEAD `2ea2004a5315dd43026658f5977b56b73c646a56` — identical to `ai_rc/v2.21.2`.
- `git status --short` empty; `git stash list` empty.
- Host: Rocky Linux 9.7, python3 3.12, pytest 8.4.2.
- The invoking shell carried `MAX_TASK_SECONDS=10800`, `KILL_GRACE_SECONDS=30` — the
  non-default budget that produced BUG-0262 in the first place — so the primary
  suite run was made under the configuration the class is sensitive to.
- Diff under test: `git diff ai_main...ai_rc/v2.21.2` — 15 files, +894/-31, in four
  merged feature branches plus one direct commit (see Finding 6).

## Findings

### Finding 1: Shared scrubbed-environment helper exists, is documented, and is used by both golden harnesses

**Category:** pass

Priority acceptance criterion 1. `team/tests/fixtures/scrubbed_subprocess_env.py`
(new, 120 lines, read in full) exposes
`build_scrubbed_env(home_dir=..., extra_path_dirs=..., lang=..., extra_env=...)`. It
constructs `PATH`/`HOME`/`LANG` from a fixed set and never merges `os.environ` —
verified by reading the implementation and by its own unit tests, which assert the
result's key set is exactly `{PATH, HOME, LANG}` and that a variable planted in the
test process does not appear in the result.

`team/TESTING.md` documents it in the fidelity-helper table and adds a
"three subprocess-facing helpers divide cleanly" paragraph saying which to reach
for. All three named consumers use it:

- `team/tests/integration/test_engine_behavior_baseline_harness.py` — converted.
- `team/tests/integration/test_helloworld_flow_harness.py` — converted.
- `team/tests/integration/wake_entrypoint_acceptance_harness.py` — `scrubbed_wake_env`
  is now re-expressed on top of the shared builder rather than carrying its own copy
  of the same base, which is the de-duplication the priority asked for.

Both conversions keep the case-specific values (`PGAI_AGENT_KANBAN_TEMP_DIR`, and in
the baseline harness the two pinned budget variables) in `extra_env`, and both
prepend `pathlib.Path(sys.executable).parent` so the subprocess still resolves a
3.12+ `python3` — the one real hazard of a scrubbed `PATH`, handled explicitly and
commented at the site.

### Finding 2: The AP7 quality-class lint exists and lands in both gated runners' findings files

**Category:** pass

Priority acceptance criterion 2. `team/scripts/lint_test_anti_patterns.py` gained
`_check_ap7`, three patterns, an opt-out widening (`anti-pattern-allowlist: [1267]`),
and a `--help` line naming the new check. Both runners invoke the linter through the
same quality-class wrapper:

```
team/scripts/run-unit-tests.sh:440        gated_runner_run_quality_lint "test_anti_patterns" -- python3 scripts/lint_test_anti_patterns.py
team/scripts/run-integration-tests.sh:444 gated_runner_run_quality_lint "test_anti_patterns" -- python3 scripts/lint_test_anti_patterns.py
```

The wiring was verified behaviorally, not inferred. Sourcing
`team/scripts/lib/gated_runner_lint.sh`, running the lint against a seeded tree
through `gated_runner_run_quality_lint "test_anti_patterns"`, and writing the
findings file produced:

```
[gated-runner] findings file written: .../findings-probe.json (1 finding(s))
runner: run-unit-tests.sh schema: 1
lint= test_anti_patterns severity= quality exit= 1
AP7 in detail: True
```

So an AP7 hit arrives in the findings file as a `quality`-severity
`test_anti_patterns` finding, and — as the real runs confirmed — does not move the
runner's exit code.

### Finding 3: Zero AP7 findings on the converted tree; the check fires on a reintroduction

**Category:** pass

Priority acceptance criterion 3, verified in both directions and four ways.

Zero on the shipped tree:

```
$ python3 scripts/lint_test_anti_patterns.py
lint_test_anti_patterns: OK — 0 findings across 484 file(s) (282 test, 202 runtime).
```

Fires on the seeded fixture (`fixture_golden_comparison_inherited_env.py`, copied
out of the lint-excluded `fixtures/` directory into a scanned location):

```
.../test_seeded_reintroduction.py:42: [AP7] This file compares a captured surface against a committed golden
AND builds its subprocess environment by inheriting the ambient one here ...
lint_test_anti_patterns: FAIL — 1 finding(s).
```

Fires on a **production-shaped** reintroduction — this is the check that matters,
since a fixture can be tailored to its own detector. A copy of the real, converted
`test_engine_behavior_baseline_harness.py` with its `build_scrubbed_env(` call
reverted to `dict(os.environ)` is flagged at the exact line:

```
.../test_reverted_engine_harness.py:104: [AP7] ...
```

Silent on both negative controls (inherited environment with no golden comparison;
golden comparison with an explicit environment), silent on unmodified copies of both
converted harnesses, and silent when the documented opt-out
`# anti-pattern-allowlist: 7 (justification: ...)` is present above the construct.
The unit test `team/tests/unit/test_lint_golden_env_coupling.py` locks all four cases
in, including a whole-tree regression case.

One caveat on the check's reach is recorded under **Caveats** below; it does not
affect this criterion, which the check meets for every golden-comparison shape the
tree actually contains.

### Finding 4: `docs/test-authoring-guidelines.md` anti-pattern 2 names the helper and the lint

**Category:** pass

Priority acceptance criterion 4. A new subsection, "Subprocess environments are
built from an explicit set, not inherited", sits inside anti-pattern 2 and carries
the rule, a BAD/BETTER code pair, the remedy by name
(`tests.fixtures.scrubbed_subprocess_env.build_scrubbed_env`), the detector by name
and category (`AP7`, quality-class, run by both gated runners before pytest), the
deliberate narrowness of the pairing, the "when acceptable" case, and the opt-out
marker. An author reading it has a remedy and not just a prohibition, which is
exactly what the criterion asked for.

The survey doc `docs/inherited-environment-exposure-survey.md` is new, is on
`team/config/docs-manifest.txt` and in `docs/README.md`, and its numbers reproduce
exactly against the tree:

| Claim in the doc | Re-derived here |
|---|---|
| 160 occurrences / 109 files at `a84deb7` | 160 / 109 at HEAD; 160 at `a84deb7` |
| 157 occurrences / 107 files at `7f7a37d` | 157 / 107 |

### Finding 5: Both gated runners exit 0, under all three `MAX_TASK_SECONDS` values

**Category:** pass

Priority acceptance criteria 5 and 6. The authoritative commands were run from the
worktree root; no direct-pytest result is cited as a PASS source anywhere in this
report.

| `MAX_TASK_SECONDS` in the invoking shell | `run-unit-tests.sh` | `run-integration-tests.sh` |
|---|---|---|
| `10800` (the wake shell's own value) | exit 0 — 4402 passed, 31 skipped, 702s | exit 0 — 462 passed, 1 skipped, 467s |
| unset | exit 0 — 4402 passed, 31 skipped, 611s | exit 0 — 462 passed, 1 skipped, 464s |
| `5400` | exit 0 — 4402 passed, 31 skipped, 627s | exit 0 — 462 passed, 1 skipped, 470s |

Pass and skip counts are byte-identical across all three, which is the direct
evidence that the harnesses' verdicts no longer depend on the invoking shell's
budget variables. Both lint pre-flights passed in every run (the quality-class
findings below never move the exit code).

The bundle's own two generic criteria also hold: all 449 Python files compile
(`python3 -m py_compile`, exit 0) and all 275 shell files pass `bash -n`.

### Finding 6: The RC required a manual intervention — a hand-authored commit on the release branch

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (a real defect an agent trips over; shipping this RC makes nothing worse, and the loop is a credible repair path — see the Systemic-Risk Rubric in `team/SOP.md`)
**Regression:** no (the class demonstrably predates this RC — BUG-0227 and BUG-0244 are the same omission in earlier releases, and this RC's own instance is already fixed on the branch)
**Integrity Damage:** no
**Filed As:** PRIORITY-0003-docs-manifest-authoring-guard (Path C-B — filed as PRIORITY, enhancement / design change)

Step 6.12, the autonomous operation criterion, answers:

1. Tasks force-promoted from WAITING to BACKLOG manually? **NO.**
2. `release-state.md` edited outside the CM scripts? **NO.** Its `## Active RC: v2.21.2`,
   `## Last Released: v2.21.1` and `## HALT Event` block are all CM-written and
   internally consistent.
3. Scripts or tooling files patched mid-build? **YES.** See below.
4. Queue files manually edited? **NO.** The queues are consistent with task states
   (`[A]` on this task, `[W]` on the waiting CM release task).

The intervention: `ai_rc/v2.21.2` carries commit `2ea2004`, "adds
inherited-environment-exposure-survey.md to docs-manifest" — a non-merge commit
authored and committed directly on the RC branch by the operator
(`purdygood <operator@example.invalid>`, 2026-09-14T10:46:19+00:00), touching
`team/config/docs-manifest.txt`. It is not the product of any CODER or WRITER task
and did not arrive through a feature branch.

Why it exists is on the record in three places. An earlier verification of this same
RC found the unit suite red because `docs/test-authoring-guidelines.md` cited the new
survey document and `docs-manifest.txt` did not list it, so the installer would never
deposit it; that became BUG-0266. CM read that report, applied the ship-policy matrix
and halted the lane (`release-state.md`, `## HALT Event`, "trigger 2 ... Regression:
yes"). The operator then fixed the one missing line by hand, recording it in the bug
itself: *"fixed this manually and commited during the blocked of v2.21.2."*

The fix is correct, and it was re-verified here independently rather than taken on
trust:

- `grep -qx 'inherited-environment-exposure-survey.md' team/config/docs-manifest.txt` succeeds.
- `python3 -m pytest tests/unit/test_installed_governance_docs_citations_resolve.py`
  (throwaway `PGAI_AGENT_KANBAN_ROOT_PATH`) → `6 passed`.
- Both gated runners exit 0 with the line in place.

BUG-0266 is `## Status: done` and is **not** re-filed; that would be a duplicate.
What is filed is the class behind it. A `docs/` citation can be merged without its
manifest entry and nothing notices until the full unit suite runs at RC verification
— twelve minutes, and by then the authoring agent's session is over and its branch
is deleted. That has now produced three bug reports (BUG-0227, BUG-0244, BUG-0266)
and, this time, a halted lane and a hand-authored commit on a release branch.
PRIORITY-0003 asks for the same remedy shape this very release delivered for the
environment-coupling class: catch it at authoring time, cheaply, as a quality-class
signal.

A note on the honest reading of this finding: the release itself is fine. The RC's
tree is correct, the fix is verified, and the artefact a user would install works.
What failed is the premise that the chain advances without a human. That is a process
defect, and Step 6.12 exists precisely so it is recorded rather than absorbed.

### Finding 7: A quality-class lint instance with no bug tracking it

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (hygiene; text-only, both runners exit 0 with the finding merely recorded)
**Regression:** no (the line is untouched by this RC — `git diff --name-only ai_main...ai_rc/v2.21.2 -- release-notes/` is empty and the identical line is present at `ai_main`)
**Integrity Damage:** no
**Filed As:** BUG-0267-hygiene-provenance-release-notes (Path C-A — filed as BUG, code defect / regression / test failure)

Both runners' findings files carry three `hygiene` provenance findings. Two are
already named in the `## Acceptance` of the open BUG-0260 and are cited, not
re-filed. The third — `release-notes/v2.20.2.md:58`, citing the internal task ID
`TESTER-20260913-013` and pointing a reader of the shipped tree at a task folder
that never ships — is named by no open or running bug, so one was filed. Per the
one-bug-per-lint-class rule, BUG-0267 is the single bug for the `hygiene` class on
this run and lists all three instances, directing the first two back to BUG-0260.

This finding is recorded here for completeness of the gap ledger; as a quality-class
lint finding it does not, on its own, drive the recommendation or the systemic-risk
rating. Finding 6 is what does.

### Finding 8: Structural, state and constraint checks

**Category:** pass

- **Pre-flight merge verification (Step 2a):** every DONE task's feature branch has
  already been deleted post-merge (normal lifecycle). No UNMERGED branch, no BUG-92
  zero-unique-commit anomaly.
- **Release-state drift (Step 6.1):** `## Active RC: v2.21.2` matches the existing
  `ai_rc/v2.21.2` branch. `pp_last_released_version` returns `v2.21.1` (tags carry
  the `ai_` branch prefix); `v2.21.2 > v2.21.1` under semver. `## Last Released:
  v2.21.1` is well-formed.
- **Prerequisite graph (Step 6.10):** the four prerequisites (CODER-007, CODER-008,
  CODER-009, WRITER-010) are all DONE, the CM open-RC bookend is DONE, the CM release
  bookend is WAITING. No cycles, no missing IDs, sequence numbers consistent.
- **Skip-cites-real-bug gate (Step 6.13):** the gated runners' own
  `lint_skip_bug_gate` reports `0 missing files`. The manual grep's four apparent
  hits (`BUG-1234/1235/1237/1239`) are string literals inside
  `team/tests/unit/test_skip_bug_citation_scan.py`, which is the test *of* the skip
  gate — synthetic input, not skip annotations. Not a finding.
- **Constraint negatives (Step 6.9):** no bare `/tmp` literal and no hardcoded temp
  path in the added lines; no secrets; the added Python carries no new operational
  constants beyond the documented explicit toolchain base.
- **Coding-standards spot-check (Step 6.14):** the modified script's `--help` names
  every argument including the new check; no added comment, docstring or output
  string cites a bug ID, task ID or version (the `hygiene` lint independently
  confirms zero provenance findings in this RC's new files); and the change removes
  duplication rather than adding it, by making `scrubbed_wake_env` a caller of the
  shared builder.
- **Synthetic release simulation (Step 6.8):** skipped with note — the diff touches
  no release-lifecycle file (`cm-release.sh`, `cm-open-rc.sh`, `cm-cancel-rc.sh`,
  `project_paths.sh`, or any open/close-cycle script). `team/config/docs-manifest.txt`
  is read by `install.sh` and `upgrade.sh`, not by the release lifecycle.

## Code Coverage

Informational only; does not enter the PASS precondition.

- Unit:        line 75%  |  branch 62%   (15806 stmts, 4744 branches; blended TOTAL 72%)
- Integration: line 7%   |  branch 6%    (15797 stmts, 4744 branches; blended TOTAL 7%)

Read from the `totals` object of `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/unit.json`
and `.../integration.json`, using the `percent_statements_covered` and
`percent_branches_covered` fields confirmed against the generated files rather than
assumed. The integration figure is low because that suite drives the tree through
subprocesses, which the in-process coverage instrumentation does not follow.

## Lint Findings (non-blocking)

Three findings, one lint class, identical in `findings-unit.json` and
`findings-integration.json`:

- `hygiene` — `team/tests/unit/test_operator_verb_manifest.py:159` — `[task-ID]` provenance reference `CODER-20260911-066` in code — cites BUG-0260 (open)
- `hygiene` — `team/tests/unit/test_operator_verb_manifest.py:176` — `[task-ID]` provenance reference `CODER-20260911-066` in code — cites BUG-0260 (open)
- `hygiene` — `release-notes/v2.20.2.md:58` — `[task-ID]` provenance reference `TESTER-20260913-013` in shipped release-notes text — filed BUG-0267 (Path C-A — filed as BUG, code defect / regression / test failure)

All three are `severity: quality`. Neither runner's exit code moved. None of them
originates in this RC's diff.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=7d freshness=stale — the last lane run was red, with docker/claude-abliterated and podman/claude-abliterated failing on a Dockerfile `FROM` parse error and podman/rocky9 on its `COPY --chmod` step; it cites BUG-0167, filed by that deep-verify run, and the record is stale on both counts (7d sits at the edge of the 7-day window, and v2.9.20 is not the v2.21.2 under verification), so nothing has been built cold against this tree.

Per Step 5d the lane was not run during this verification and no second bug was
filed for its result; BUG-0167 already tracks it (`## Status: done`, so the fix is
believed shipped but the ledger has not been refreshed since). This line is context
the rating is made with, not a finding: it does not move the recommendation or the
systemic risk.

## Autonomous criterion

1. Tasks force-promoted from WAITING to BACKLOG manually? **NO**
2. `release-state.md` edited outside `cm-open-rc.sh` / `cm-release.sh` / `cm-cancel-rc.sh`? **NO**
3. Scripts or tooling files patched mid-build? **YES** — commit `2ea2004` on
   `ai_rc/v2.21.2`, authored and committed directly by the operator, patching
   `team/config/docs-manifest.txt` while the lane was halted. Full account and
   disposition in Finding 6; filed as PRIORITY-0003.
4. Queue files manually edited? **NO**

`Manual interventions required: one` — check 3.

## Caveats

- **The AP7 check recognises a golden comparison by naming convention, and that is
  narrower than the class.** The golden-comparison half of the pairing is detected by
  a `golden`-named identifier assigned through a path join, or a literal `"golden"`
  path segment. Demonstrated consequence: taking the flagged production-shaped
  reintroduction from Finding 3 and renaming only its `_GOLDEN_DIR` constant to
  `_BASELINE_DIR` makes the check go silent, even though the file still compares a
  captured surface against a committed fixture directory (and still says
  "committed golden snapshots" in its own docstring) with an inherited environment.
  This is recorded as a caveat rather than a gap because the criterion is met for
  every golden-comparison shape the tree actually contains — a sweep for
  committed-fixture directory constants paired with an inherited environment finds
  no live site the check misses — and because the narrowness is deliberate,
  documented in the lint's own pattern comments, and directly responsive to the
  over-reach risk the priority named. It is worth knowing when the next
  golden-comparison harness is written: if its fixture constant is not named for a
  golden, the check will not speak up.
- **`docs/README.md` still describes `test-authoring-guidelines.md` as carrying "the
  six authoring anti-patterns"**, and `team/SOP.md` refers to "the five anti-patterns
  the suite is held to", while the linter now reports seven categories (AP7 being a
  documented sub-class of anti-pattern 2 rather than a seventh heading). The counts
  were already inconsistent with each other before this RC; nothing here made it
  worse, and no reader is steered into a wrong action. Observation only, not filed.
- **`VERSION` in the worktree reads `v2.21.1`.** That is the normal pre-release state
  for this project — the release script stamps the new version at ship time — and is
  not drift.
- **The prior run of this task left no rotated artifacts.** BUG-0266 and the
  `## HALT Event` entry both cite
  `tasks/TESTER-20260914-012-verify-2-21-2/artifacts/report.md` from an earlier
  session, but `artifacts/` was empty at the start of this run with no
  `report.md.previous-RUN-N` present, so the framework's preservation rotation did
  not happen (or the file was removed by hand). This did not affect the verification
  — a fresh run is required regardless, and that is what was performed, with no prior
  report read — but the evidence trail for that earlier run is gone. Recorded so the
  absence is not mistaken for "this task never ran before."
- **Live-install safety** was observed throughout: every ad-hoc invocation that could
  write under a kanban root was pointed at a throwaway root created under
  `$PGAI_AGENT_KANBAN_TEMP_DIR`, and all scratch output for this run lives under
  `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester-scratch/`.

## Artifacts

- `artifacts/report.md` — this file
- `artifacts/gaps.md` — the two gap entries with criterion / expected / actual / severity
- `bugs/BUG-0267-hygiene-provenance-release-notes.md` — Path C-A filing
- `priority/PRIORITY-0003-docs-manifest-authoring-guard.md` — Path C-B filing
