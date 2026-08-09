# Coverage Gate, Disposition Finalize, and Counts/Runtime Report — v2.3.0

## Purpose

This is the RC's end-of-release verification gate (Goals 4-5 / Acceptance
3-5 of `requirements/v2.3.0-provenance-sweep-and-test-consolidation.md`,
implemented by CODER-20260806-013). It is the evidence the operator reviews
at the human-approval gate required by that requirements document. It
answers three questions directly, with primary data (not summaries of
primary data):

1. Is final coverage for both gated suites >= the committed baseline
   (`team/tests/artifacts/coverage-baseline-v2.3.0/`), on both percentage
   AND covered-line-set? (Acceptance 3)
2. Is the protected-fixture disposition artifact
   (`team/tests/artifacts/protected-fixture-disposition-v2.3.0.md`)
   fully resolved, with a green spot-run of five named fixtures? (Acceptance
   4)
3. What are the final test counts and suite runtimes, reported as an
   outcome (not a pass/fail gate)? (Acceptance 5)

Measured at commit `f6319628d2b097812432e6552e7d6deba163579d`, the tip of
`ai_rc/v2.3.0` immediately before this task's own commit, after all of
CODER-20260806-007 through -012 (provenance-lint-guard,
consolidate-unit-{release-cm,dashboard,discovery-infra,remaining},
consolidate-integration) and the two upstream provenance-sweep
tasks (WRITER-20260806-006, CODER extending the lint to release-summary
prose) had already merged into `ai_rc/v2.3.0`.

## How this was produced

Both gated suites were run from this exact worktree, from their real entry
points, with the `--coverage` flag — the identical invocation the baseline
artifact used:

```bash
bash team/scripts/run-unit-tests.sh --coverage
bash team/scripts/run-integration-tests.sh --coverage
```

The unit suite was run **three times** (to check for run-to-run
nondeterminism before trusting any per-file diff against the baseline);
all three runs produced identical `2898 passed, 3 skipped`, exit 0, and
identical `coverage.py` totals (`9934` covered lines, `2500` covered
branches). The two most recent runs (`--randomly-seed=3848628360` and
`--randomly-seed=2637770193` — pytest-randomly uses a different seed each
invocation) additionally produced **byte-identical** `executed_lines` /
`executed_branches` sets for every file in the report, including the six
files flagged below as showing an apparent baseline-vs-final diff. This
rules out test-execution-order nondeterminism as the explanation for that
diff (see "Investigating the per-file line/branch diff" below). The third
run's raw JSON (`--randomly-seed=2637770193`) is the one committed here as
`unit-coverage.json` / `unit-run.log`; the other two runs were transient
and are not preserved (their console logs showed identical pass/skip/exit
results to the committed run, differing only in wall-clock runtime and
random seed).

The integration suite was run once (`integration-coverage.json` /
`integration-run.log`); its own gated run already exits 0 on the first
attempt and is not order-flaky in the way described below (the six flagged
production files include only two integration-side files, both cleanly
resolved — see below).

## Suite exit status (required: both suites must exit 0)

| Suite | Exit code | Result |
|---|---|---|
| Unit (`run-unit-tests.sh --coverage`) | 0 | PASS |
| Integration (`run-integration-tests.sh --coverage`) | 0 | PASS |

Both suites exited 0, including every pre-flight lint gate (test
anti-pattern, skip-cites-real-bug, orphan-test, API-parity, upgrade
no-temp-copy-guard, ICD freshness/compat, CHANGELOG freshness,
lib-function-dedupe, env-bootstrap, help-presence, comment-provenance,
import-completeness for unit; the matching subset for integration) and
each runner's post-suite cleanliness checks (bare-`/tmp` residue,
framework-rooted TCP listener leaks) — see `unit-run.log` /
`integration-run.log` for the full captured output.

## 1. Coverage compare — percentage (Acceptance 3, part 1)

### Unit suite

| Metric | Baseline | Final | Delta |
|---|---|---|---|
| Line coverage | 70.66% (9934/13448) | 70.66% (9934/13448) | **+0.00 pct pts** |
| Branch coverage | 60.27% (2500/4148) | 60.27% (2500/4148) | **+0.00 pct pts** |

### Integration suite

| Metric | Baseline | Final | Delta |
|---|---|---|---|
| Line coverage | 7.55% (1070/13448) | 7.55% (1070/13448) | **+0.00 pct pts** |
| Branch coverage | 6.24% (259/4148) | 6.24% (259/4148) | **+0.00 pct pts** |

Both suites' aggregate line and branch coverage are **exactly equal** to
baseline (not merely "greater or equal" — the total covered-line and
covered-branch counts are identical to the last digit in both suites).
`>= baseline` holds for both metrics, both suites.

## 2. Coverage compare — covered-line-set diff (Acceptance 3, part 2)

The gate is "no line covered before may become uncovered." A per-file diff
of `executed_lines` / `executed_branches` between the baseline JSON and the
final JSON (`coverage_compare.py`, included in this directory; full output
in `raw-coverage-diff-report.md`) initially flags **six files** as having
lines/branches present in baseline's `executed_lines`/`executed_branches`
but absent from final's, for both suites:

- `pgai_agent_kanban/cm/write_rc_state.py`
- `pgai_agent_kanban/dashboard/scan_attention.py`
- `pgai_agent_kanban/ops/write.py`
- `pm-agent/pm_materialize.py`
- `scripts/lib/metrics_aggregator.py`
- `scripts/lib/metrics_csv_writer.py`

No other file in either suite's 88-file report shows any diff.

### Investigating the per-file line/branch diff

These six files are **exactly** the production files whose in-code comments
were reworded by this RC's provenance sweep (Goal 2 of the requirements
doc) between the baseline commit
(`a795c2b4d32a98f13e9f45c524a137e427619a2a`) and this commit — confirmed by
`git diff a795c2b4d32a98f13e9f45c524a137e427619a2a..HEAD --name-only`
against the non-test, non-release-notes portion of the tree. Each edit
deletes (or, in one `pm_materialize.py` hunk, adds) a comment/docstring
line — e.g. `write_rc_state.py`'s docstring dropped a
`# provenance-allowlist: remediation-pending` line — which shifts every
line below the edit point up (or down) by a fixed offset within that file.
`coverage.py` counts these specific comment-adjacent physical source lines
as statement lines (they are string-literal continuation lines inside
argparse `description=` calls and module docstrings that coverage.py's
line-number bookkeeping tracks individually), so the exact same executed
statement is reported at a different line number after the shift — a
false-positive "regression" in a naive line-number diff.

`coverage_shift_resolve.py` (included in this directory; full output in
`line-shift-resolution-proof.txt`) proves this precisely, without relying
on judgment calls: for each of the six files, it builds a line-number
correspondence between the baseline-commit and final-commit versions of
the file using `difflib.SequenceMatcher` restricted to **unchanged**
regions (lines whose content is identical in both commits — i.e.
untouched by the provenance-sweep edit), then re-checks every
baseline-covered-but-final-missing line/branch against its mapped final
line number.

Result — **zero unresolved hits** across all six files, both suites, both
lines and branches:

| Suite | File | Missing lines (raw) | Resolved via shift map | Missing branches (raw) | Resolved via shift map |
|---|---|---|---|---|---|
| Unit | `write_rc_state.py` | 32 | 32/32 | 11 | 11/11 |
| Unit | `scan_attention.py` | 130 | 130/130 | 121 | 121/121 |
| Unit | `ops/write.py` | 26 | 26/26 | 14 | 14/14 |
| Unit | `pm_materialize.py` | 263 | 263/263 | 233 | 233/233 |
| Unit | `metrics_aggregator.py` | 1 | 1/1 | 1 | 1/1 |
| Unit | `metrics_csv_writer.py` | 35 | 35/35 | 13 | 13/13 |
| Integration | `write_rc_state.py` | 19 | 19/19 | 4 | 4/4 |
| Integration | `ops/write.py` | 21 | 21/21 | 13 | 13/13 |
| Integration | (other 4 files) | 0 | n/a | 0 | n/a |

Every single line and branch coverage.py reported as "missing" from the
final measurement maps, via an unchanged-line correspondence to the exact
same physical statement, to a line the final measurement DOES report as
covered. There is no line or branch anywhere in either suite's measurement
that is genuinely covered in baseline and genuinely uncovered in final.

**Conclusion: the covered-line-set diff shows ZERO regressions.** The raw
per-file diff is fully and precisely explained by provenance-sweep
comment-line shifts, corroborated independently by the 3x unit-suite
reproducibility check (byte-identical `executed_lines`/`executed_branches`
for these exact six files across two separately-seeded full-suite runs).
No test file's coverage promise was weakened by consolidation.

## 3. Protected-fixture disposition artifact (Acceptance 4)

`team/tests/artifacts/protected-fixture-disposition-v2.3.0.md` — the
inventory of every named golden fixture (Section A, six categories) and
every bug-born test (Section B) that must survive consolidation
recognizably or as an accounted-for named parameterization.

- **82 disposition table rows** across Sections A and B.
- **Zero `Disposition: pending` rows.** (`grep -n "Disposition: pending"
  protected-fixture-disposition-v2.3.0.md` returns only the legend
  sentence at line 20 describing the initial value semantics — no data
  row.) Every row was resolved by CODER-20260806-008 through -012 to
  either `survives-as-itself` or `survives-as-named-parameterization
  <name>`.
- The artifact's own "Screened Out" appendix documents every
  bug-shaped-identifier file that was reviewed and classified as
  fixture data rather than a bug-born test, per SOP.md Anti-pattern 6 —
  so the exclusion set is auditable, not silent.

This satisfies Required Output item 3 and Acceptance 4's disposition
half.

### Spot-run of five named protected fixtures (Acceptance 4)

The disposition artifact's own "Spot-check readiness" section (added by
CODER-20260806-012) recommends one fixture from each of the five
distinct protected-fixture categories (A1-A5) as the spot-check set. Ran
from `team/` with the real dev-tree root, matching the gated suites'
invocation convention:

```bash
python3 -m pytest \
  "tests/integration/test_rc_lifecycle.py::TestFidelityGate::test_fidelity_gate_halts_when_main_diverges_mid_rc" \
  "tests/integration/test_cancel_rc_frees_version.py::TestCancelRCFreesVersion::test_reopen_same_version_after_cancel_succeeds" \
  "pgai_agent_kanban/api/tests/test_cors.py::test_cors_non_loopback_origin_blocked" \
  "tests/unit/test_changelog_writer.py::TestDisclosureScope::test_bug_affecting_published_version_is_disclosed" \
  "tests/unit/test_resolution_matrix.py::test_golden_facts_match_cancel_and_rebundle" \
  -v -p no:randomly
```

Result (full output in `spot-check-run.log`):

```
tests/integration/test_rc_lifecycle.py::TestFidelityGate::test_fidelity_gate_halts_when_main_diverges_mid_rc PASSED [ 20%]
tests/integration/test_cancel_rc_frees_version.py::TestCancelRCFreesVersion::test_reopen_same_version_after_cancel_succeeds PASSED [ 40%]
pgai_agent_kanban/api/tests/test_cors.py::test_cors_non_loopback_origin_blocked PASSED [ 60%]
tests/unit/test_changelog_writer.py::TestDisclosureScope::test_bug_affecting_published_version_is_disclosed PASSED [ 80%]
tests/unit/test_resolution_matrix.py::test_golden_facts_match_cancel_and_rebundle PASSED [100%]

============================== 5 passed in 3.23s ===============================
```

All five spot-checked fixtures are green.

## 4. Final counts and suite runtimes (Acceptance 5)

Per the task's own constraint: **count is reported as an outcome, not
restated as a pass/fail gate.**

| Suite | Passed | Skipped | Failed | Collected | Wall-clock runtime (real) |
|---|---|---|---|---|---|
| Unit — baseline | 2897 | 3 | 0 | 2900 | 6m24.807s |
| Unit — final | 2898 | 3 | 0 | 2901 | 5m44s (344.24s) |
| Integration — baseline | 283 | 1 | 0 | 284 | 3m20.982s |
| Integration — final | 283 | 1 | 0 | 284 | 3m25s (205.33s) |
| **Total — baseline** | **3180** | **4** | **0** | **3184** | **9m45.789s** |
| **Total — final** | **3181** | **4** | **0** | **3185** | **9m9.57s (549.57s)** |

(Final runtimes are the `real` values reported by each `run-*.sh` wrapper's
own `time`-wrapped invocation, from the committed run's log — same
methodology the baseline used. The unit suite's runtime varied across its
three repeated runs in this measurement session — 6m59s, 6m00s, 5m44s —
consistent with ordinary host-load variance between separate pytest
invocations; all three ran the identical 2898/3/0 test set. Two of the
three unit runs are not separately committed; see "How this was produced"
above.)

**Observed outcome:** final collected-item count is **+1 relative to
baseline** (2901 vs 2900 unit; integration unchanged at 284), not the
"~2/3" reduction the requirements doc's Summary section describes as an
*expected* outcome. This RC's consolidation tickets (008-012) each
independently verified `pytest --collect-only` case-count PARITY per file
they touched (documented in their own disposition notes in
`protected-fixture-disposition-v2.3.0.md`) — i.e. parametrize-consolidation
folds N sibling *functions* into one table-driven function while
preserving all N *cases* as distinct collected pytest items, so
function-count and file-count fell substantially (individual tickets
report file-count and function-count reductions in their own commit
messages / disposition notes) while collected-item count, which pytest
reports and this table measures, stayed essentially flat by design. Other
work also landed on this RC in the same window and touches the unit test
tree outside the consolidation tickets' scope (for example
CODER-20260806-007 added 4 new test functions to
`test_lint_comment_provenance.py` while extending the comment-provenance
lint to release-summary prose) — this report did not fully bisect every
contributing ticket's exact per-file delta against the +1 net figure, so
it states the net outcome as measured rather than asserting a precise
causal breakdown it has not verified. Per this task's own constraint, the
count is reported as a factual outcome, not evaluated as pass/fail —
coverage (Section 1-2 above) is the actual gate, and it holds regardless
of which direction the count moved.

## Files in this directory

| File | Contents |
|---|---|
| `README.md` | This report — the primary human-approval-review artifact. |
| `unit-coverage.json` | Final `coverage.py` JSON report for the unit suite (line+branch, per-file). |
| `integration-coverage.json` | Final `coverage.py` JSON report for the integration suite (line+branch, per-file). |
| `unit-run.log` | Full captured stdout/stderr of the committed `run-unit-tests.sh --coverage` run. |
| `integration-run.log` | Full captured stdout/stderr of `run-integration-tests.sh --coverage`. |
| `spot-check-run.log` | Full pytest output of the 5-fixture spot-run. |
| `coverage_compare.py` | The raw baseline-vs-final percentage + line/branch-set diff tool used to produce `raw-coverage-diff-report.md`. |
| `raw-coverage-diff-report.md` | Full unfiltered output of `coverage_compare.py` for both suites (before the line-shift investigation). |
| `coverage_shift_resolve.py` | The difflib-based line-shift resolution tool used to prove the six-file diff is a false positive. |
| `line-shift-resolution-proof.txt` | Full output of `coverage_shift_resolve.py` for both suites — the "RESULT: ... zero unresolved hits" conclusion. |

## Design choices not explicitly sanctioned by the brief, documented for the operator

1. The requirements doc's Acceptance 3 says "the covered-line-set diff
   must show no regressions." It does not specify a methodology for
   distinguishing a genuine regression from a line-number-shift artifact.
   I built the difflib-based shift-resolution tool (`coverage_shift_resolve.py`)
   specifically because the raw diff (`coverage_compare.py`) is a naive
   line-number set comparison that cannot tell the two apart on its own,
   and this task's own constraint says "if any regression exists, report
   it and do not paper over it" — I judged that silently omitting the
   six-file diff from this report (because "it's probably just the
   provenance sweep") would itself be papering over a genuine-looking
   signal without evidence, so I built the proof instead of asserting it.
2. I ran the unit suite three times (not the minimum two the runner
   requires) before trusting any per-file diff, specifically to rule out
   pytest-randomly test-order effects as an alternative explanation
   before reaching for the line-shift hypothesis. This cost roughly six
   extra minutes of suite runtime but was the fastest way to eliminate a
   plausible confound with direct evidence rather than assertion.
3. I did not attempt to reconcile the "~2/3 test count" language in the
   requirements doc's Summary with the observed +1 outcome beyond noting
   it factually in Section 4 above — this task's own constraint
   explicitly forbids treating count as a gate, and the individual
   consolidation tickets (008-012) already document their own
   function/file-count reductions in their commit messages, which this
   report does not re-aggregate (that aggregation is release-notes/CM
   territory, not this gate's job).
