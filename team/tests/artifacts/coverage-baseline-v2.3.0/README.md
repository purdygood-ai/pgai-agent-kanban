# Coverage Baseline — v2.3.0 (pre-test-consolidation)

## Purpose

This directory is the RC's coverage safety rail (Goal 1 / Acceptance 1
of `requirements/v2.3.0-provenance-sweep-and-test-consolidation.md`).
It records line and branch coverage for both gated suites, measured
against the tree as it stood **before any test file in this RC was
edited**. Every later coverage claim in the v2.3.0 test-consolidation
work must measure against this baseline — as a percentage AND as the
covered-line-set (no line covered here may become uncovered later;
additions are welcome).

Measured at commit `a795c2b4d32a98f13e9f45c524a137e427619a2a` ("Add
committed VERSION for v2.2.4"), the tip of `ai_rc/v2.3.0` at the time
this task ran, with zero test-file edits applied. This is the sole
commit this task adds to the branch, so git history trivially proves
the ordering guarantee: this commit contains no changes to any file
under a test directory, and no test-file-editing commit exists in the
RC's history before it.

## How this was produced

Both gated suites were run from this exact worktree, from their real
entry points, with the `--coverage` flag (wired to `pytest-cov` /
`coverage.py`, `--cov-branch` enabled):

```bash
bash team/scripts/run-unit-tests.sh --coverage
bash team/scripts/run-integration-tests.sh --coverage
```

Each run's full captured stdout/stderr (including the pre-flight lint
gate chain, the per-file term coverage table, the pytest summary line,
and the post-suite cleanliness checks) is preserved verbatim in
`unit-run.log` and `integration-run.log`. Each run's `coverage.py` JSON
report (produced by `--cov-report=json`) is preserved verbatim in
`unit-coverage.json` and `integration-coverage.json` — these are the
authoritative per-file **covered-line and covered-branch sets**
(`files.<path>.executed_lines`, `files.<path>.executed_branches`,
`files.<path>.missing_lines`, `files.<path>.missing_branches`) that a
later coverage-regression-diff task should read against, rather than
re-deriving coverage from scratch. No file under this artifact
directory was hand-edited from the tool output except this README.

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
each runner's post-suite cleanliness checks (bare-/tmp residue,
framework-rooted TCP listener leaks).

## Pre-consolidation test counts and runtimes

| Suite | Passed | Skipped | Failed | Collected | Wall-clock runtime (real) |
|---|---|---|---|---|---|
| Unit | 2897 | 3 | 0 | 2900 | 6m24.807s |
| Integration | 283 | 1 | 0 | 284 | 3m20.982s |
| **Total** | **3180** | **4** | **0** | **3184** | **9m45.789s** |

(Runtimes are the `real` value from wrapping each invocation in the
`time` builtin around the full script, i.e. including the pre-flight
lint chain, not pytest collection time alone.)

## Coverage summary (line + branch, per coverage.py)

### Unit suite (`unit-coverage.json`)

| Metric | Value |
|---|---|
| Statements | 13,448 |
| Covered lines | 9,934 |
| Missing lines | 3,514 |
| Excluded lines | 10 |
| **Line coverage** | **70.66%** (displayed "71%") |
| Branches | 4,148 |
| Covered branches | 2,500 |
| Missing branches | 1,648 |
| Partial branches | 528 |
| **Branch coverage** | **60.27%** (displayed "60%") |
| Files measured | 88 |

### Integration suite (`integration-coverage.json`)

| Metric | Value |
|---|---|
| Statements | 13,448 |
| Covered lines | 1,070 |
| Missing lines | 12,378 |
| Excluded lines | 10 |
| **Line coverage** | **7.55%** (displayed "8%") |
| Branches | 4,148 |
| Covered branches | 259 |
| Missing branches | 3,889 |
| Partial branches | 123 |
| **Branch coverage** | **6.24%** (displayed "6%") |
| Files measured | 88 |

Both suites measure the same `--cov` targets
(`pgai_agent_kanban`, `pm-agent`, `scripts/lib`) so file sets are
directly comparable between the two JSON reports; the integration
suite's much lower percentage is expected — it exercises a narrow
slice of end-to-end lifecycle paths rather than the broad unit-level
surface, and the two suites are gated (and measured) separately per
the task's own constraint. A combined-coverage view (the union of both
suites' `executed_lines`/`executed_branches` per file) is left to
whichever later task performs the end-of-release coverage compare —
the raw JSON in this directory is a faithful, undiluted source for
that computation, deliberately not pre-merged here so no union/merge
logic (and its own risk of a translation bug) is introduced by a
measurement-only task.

## Per-file covered-line sets

The authoritative per-file covered-line and covered-branch sets are in
`unit-coverage.json` and `integration-coverage.json`, under
`files.<relative-path>.executed_lines` / `.executed_branches`
(and the corresponding `.missing_lines` / `.missing_branches`). These
are `coverage.py` format-3 JSON reports (`meta.format: 3`,
`meta.version: "7.14.3"`, `meta.branch_coverage: true`). A future
coverage-regression check should diff `executed_lines` per file between
this baseline and the post-consolidation measurement — a line present
in the baseline's `executed_lines` that is absent from the final
measurement's `executed_lines` (and not simply removed from the file
entirely) is a regression per Acceptance 3 of the parent requirements
document.

## Files in this directory

| File | Contents |
|---|---|
| `README.md` | This summary. |
| `unit-coverage.json` | Raw `coverage.py` JSON report for the unit suite (line+branch, per-file). |
| `integration-coverage.json` | Raw `coverage.py` JSON report for the integration suite (line+branch, per-file). |
| `unit-run.log` | Full captured stdout/stderr of `run-unit-tests.sh --coverage`, including the per-file term coverage table, lint gate output, and pytest summary. |
| `integration-run.log` | Full captured stdout/stderr of `run-integration-tests.sh --coverage`, including lint gate output and pytest summary. |
