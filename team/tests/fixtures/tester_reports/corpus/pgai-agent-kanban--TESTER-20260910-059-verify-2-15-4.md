# Verification Report: ai_rc/v2.15.4

**Task:** TESTER-20260910-059-verify-2-15-4
**Requirements doc:** `projects/pgai-agent-kanban/requirements/v2.15.4-bugfix-bundle-20260910.md`
**RC branch / commit:** `ai_rc/v2.15.4` @ `da3b654831f1ee89bdb08a16a0979f3971269e02`
**Worktree verified:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260910-059-verify-2-15-4` (detached HEAD, clean, no stashes)
**Baseline compared against:** `ai_main`
**Verified:** 2026-09-10, Rocky Linux 9.7, python3 3.12.12, bash 5.1

## Summary

v2.15.4 is a two-item bug bundle against the release-notes "Bugs Skipped" table:
BUG-0235 (rendered table cells were not table-safe — the severity cell carried the
whole `## Severity` section body, uppercased, newlines and `---` rules intact, and no
cell was sanitised for `|`) and BUG-0233 (the new end-to-end test for the
auto-generated notes path stayed green when the rendered block stopped reaching the
generator, because it asserted only on the heading and on the absence of a leaked
identifier). The RC's whole diff is three files, 138 insertions / 5 deletions:
`team/scripts/lib/cm/release_known_issue.sh` (a `first_token` severity read plus a
`table_cell` normaliser applied to all four cells), the integration test's new
positive content assertion, and three new unit tests. Both gated runners exited 0.
Both bugs' acceptance lists were verified behaviourally, each by reproducing the exact
negative control the bug report specifies in an isolated scratch copy of the tree: the
three new unit tests all fail against the reverted library, and all six parametrized
auto-generate cases fail when the tenth positional argument to
`cm_release_generate_notes` is emptied — failing on the new content assertion, not the
heading. The fix was additionally exercised against production-shape input: all 74
real bug files in the project's ledger now render as exactly 74 single-line, four-cell
rows with a valid one-word severity token, and a bare-value bug file renders
byte-identically to the pre-fix code. No bugs, gaps, or stale assertions were found.
One pre-existing quality-class lint finding (10 `comment_provenance` instances, all in
files this RC did not touch) is already tracked by open BUG-0230 and is cited rather
than re-filed.

## Recommendation

PASS

## Systemic Risk

low

## Suites (authoritative gated runners)

| Runner | Exit | Result |
|---|---|---|
| `bash team/scripts/run-unit-tests.sh --coverage` | **0** | 3773 passed, 3 skipped in 482.49s |
| `bash team/scripts/run-integration-tests.sh --coverage` | **0** | 442 passed, 1 skipped in 419.94s |

`UNIT_EXIT == 0` AND `INTEGRATION_EXIT == 0` — the suite-pass precondition is met, and
the PASS evidence cited here is the gated runners' exit codes, not a direct pytest
line. Both runners' lint pre-flights (breakage class) passed; `lint_skip_bug_gate`
reported `OK — all skip annotations are compliant` and `0 missing files`. Captured
output: `/tmp/pgai_kanban_tmp/tester/unit_output.txt`,
`/tmp/pgai_kanban_tmp/tester/integration_output.txt`.

## Code Coverage

Read from the JSON `totals` objects (`/tmp/pgai_kanban_tmp/coverage/unit.json`,
`.../integration.json`); field names confirmed against the generated files.

- Unit:        line 75%  |  branch 61%   (14793 stmts, 4462 branches; blended `percent_covered` 72%)
- Integration: line 8%   |  branch 6%    (14784 stmts, 4462 branches; blended `percent_covered` 8%)

Informational only. Coverage does not enter the PASS precondition, the recommendation,
or the systemic-risk rating.

## Findings

### Finding 1: Requirements acceptance criterion 1 — each bundled item is addressed

**Category:** pass

Both bundled items are addressed by exactly one commit each on the RC, and each
commit's subject names the behaviour rather than the ticket:

- BUG-0235 → `c5b08ec` "Normalise every Bugs-Skipped table cell to one pipe-safe line"
  (`team/scripts/lib/cm/release_known_issue.sh` + 3 new unit tests).
- BUG-0233 → `1b668a7` "Add positive assertion on rendered Bugs-Skipped substance to
  auto-generated notes test" (`team/tests/integration/test_cm_release_known_issue_carry_forward.py`).

Diff scope is exactly three files and nothing else:

```
team/scripts/lib/cm/release_known_issue.sh
team/tests/integration/test_cm_release_known_issue_carry_forward.py
team/tests/unit/test_cm_known_issue_carry_forward_bash.py
```

### Finding 2: Requirements acceptance criterion 2 — all Python files compile cleanly

**Category:** pass

`python3 -m py_compile` over all 392 tracked `.py` files: clean, exit 0.

### Finding 3: Requirements acceptance criterion 3 — all bash files pass syntax check

**Category:** pass

`bash -n` over all 256 tracked `.sh` files: zero syntax failures.

### Finding 4: BUG-0235 acceptance — multi-line severity renders one line, severity word only

**Category:** pass

Behavioural, on the real production shape. Rendering a row from a bug file whose
`## Severity` section holds the value, a blank line, an explanatory paragraph and a
`---` rule (this project's prevailing convention — the report measured 33 of 72 files
in that shape) now produces exactly one line whose severity cell is a single word.

Rendered live from the actual ledger's BUG-0233 and BUG-0235 files (each of which has
a multi-line `## Severity`):

```
| The end-to-end test for the auto-generated release notes stays green when the rendered bugs-skipped table stops reaching the generator. | internal-only | MEDIUM | Deferred to the next iteration. |
| The rendered bugs-skipped table puts multi-line prose inside a single table cell, which breaks the row. | internal-only | MEDIUM | Deferred to the next iteration. |
```

Against the pre-fix library the same input produces the reported defect — the severity
cell begins `MEDIUM THE RATING IS EXPLAINED...` and the row continues over further
lines ending `... --- |`.

### Finding 5: BUG-0235 acceptance — a `|` in the source Symptom still yields four cells

**Category:** pass

Rendering `BUG-0182-harness-help-cites-ticket.md` (one of the three real files with a
`|` in the first sentence of its `## Symptom`) yields one line that splits into exactly
four cells, with the literal pipe backslash-escaped (`\|`) rather than dropped or
substituted. The same holds for a synthesised file carrying a `|` in the Public ID cell
and for a caller-supplied rationale carrying both a `|` and a newline — all four cells
are normalised, satisfying the ticket constraint "normalise every cell, not only the
one the report opened on":

```
| A defect where the value \| carries a pipe and the sentence ends here. | KI-00\|09 | HIGH | Deferred because the pipe \| character and a newline both appear in this rationale. |
```

### Finding 6: BUG-0235 acceptance — N pairs of multi-line-severity files give N + 2 lines

**Category:** pass

`test_bugs_skipped_ship_block_has_n_plus_2_lines_for_multiline_severity_pairs` covers
this with N=3 and passes. Confirmed independently at production scale: rendering all
**74** bug files in `projects/pgai-agent-kanban/bugs/` produces exactly **74** lines,
every one splitting into exactly 4 cells (escaped-pipe-aware split), with every
severity cell drawn from the expected vocabulary — `HIGH` 11, `LOW` 41, `MEDIUM` 21,
`UNSPECIFIED` 1. Zero anomalies. Every row carries exactly 5 unescaped `|` delimiters.

### Finding 7: BUG-0235 acceptance — new unit tests fail if the normalisation is reverted

**Category:** pass

Negative control run in an isolated `cp -a` copy of the worktree (the RC worktree was
never modified; `git status --short` on it is empty before and after). Replacing
`team/scripts/lib/cm/release_known_issue.sh` with its `ai_main` (pre-fix) content and
re-running the unit file:

```
3 failed, 58 passed in 3.05s
FAILED test_bugs_skipped_ship_block_has_n_plus_2_lines_for_multiline_severity_pairs
FAILED test_bugs_skipped_block_splits_into_four_cells_when_symptom_carries_a_pipe
FAILED test_bugs_skipped_block_flattens_multiline_severity_to_one_line_word
```

The first failure's message is the reported defect verbatim — `assert 2 == 1`, with the
severity cell reading `MEDIUM THE RATING IS EXPLAINED... ONTO A SECOND PHYSICAL LINE,
WHICH A TABLE CELL MUST NEVER ABSORB. --- |`. All three new tests are therefore real
regression guards, not tautologies. On the unmodified RC the same file is 61 passed.

### Finding 8: BUG-0235 constraint — bare-value bug files render byte-identically

**Category:** pass

A bug file in the template's bare-value shape rendered through the pre-fix library and
through the RC library produces byte-identical output (`cmp -s` equal):

```
| A plain defect with a single-sentence symptom. | KI-0009 | MEDIUM | Deferred: low impact. |
```

Corroborated from the other direction by Finding 7: the 58 pre-existing tests in the
unit file pass unchanged both with and without the fix, so no existing expectation was
adjusted to accommodate the change.

### Finding 9: BUG-0235 constraint — `build_known_issue_notes_block` checked for the same exposure

**Category:** pass

Verified independently of CODER's completion note. `build_known_issue_notes_block`
(`team/scripts/lib/cm/release_known_issue.sh`, from line 300) assembles a bullet list,
never a table, and never calls `parse_section(text, "Severity")` — its only reads are
`Symptom` (via `first_sentence`, which already flattens whitespace) and `Public ID`. It
does not share the defect, so leaving it unchanged is correct, and the finding is
recorded as the ticket's constraint required.

### Finding 10: BUG-0233 acceptance — six cases fail under the block-never-arrives control

**Category:** pass

The finer negative control, run in the same isolated scratch copy: with the tenth
positional argument to `cm_release_generate_notes`
(`team/scripts/lib/cm/release_notes.sh`) changed from `"${_BUGS_SKIPPED_NOTES_BLOCK:-}"`
to `""` — section still emitted, only the value's arrival removed —

```
6 failed, 5 deselected in 13.20s
FAILED ...auto_generated_notes_path[CODER] [WRITER] [TESTER] [PM] [CM] [PO]
```

Every failure is on the new assertion and names rendered content, not the heading:
`assert 'A defect quoting task another item inline in its own text.' in '...## Bugs
Skipped\nNone\n...'`. This is the exact shape BUG-0233 measured as passing (`6 passed,
5 deselected`) before the fix, so the hole is closed.

### Finding 11: BUG-0233 acceptance — unmodified tree passes all six cases

**Category:** pass

On the RC worktree with no modifications, `pytest -k auto_generated_notes_path -v`
reports 6 PASSED / 5 deselected, one per agent prefix (CODER, WRITER, TESTER, CM, PM,
PO). The existing three assertions were kept; the new one is additive.

### Finding 12: BUG-0233 acceptance — diff touches only the test file; `release_notes.sh` unmodified

**Category:** pass

`git diff --quiet ai_main..ai_rc/v2.15.4 -- team/scripts/lib/cm/release_notes.sh`
returns 0 — the subject of the assertion is unchanged by the RC, so the temporary
control CODER applied was fully reverted. The RC worktree is clean
(`git status --short` empty), and the negative controls in this verification were run
on a scratch copy, never on the RC tree.

### Finding 13: Internal-ID scrub still applies after normalisation (BUG-0231 regression control)

**Category:** pass

The normalisation composes with the scrub rather than replacing it, and the scrub runs
first. None of the 74 rendered production rows contains an agent-prefixed task ID, a
`BUG-NNNN`, or a `PRIORITY-NNNN`. A rationale deliberately seeded with a task ID, a
pipe, and a newline renders as
`Deferred per another item finding \| with a pipe and a newline.` — scrubbed, escaped,
and flattened.

### Finding 14: Step 6.1 — release-state drift check

**Category:** pass

`pp_last_released_version pgai-agent-kanban` = `v2.15.3`; `semver_gt v2.15.4 v2.15.3`
holds. `release-state.md` shows `Active RC: v2.15.4`, `RC Opened At
2026-09-10T20:03:17+00:00`, `RC Opened By Task CM-20260910-058-open-rc-2-15-4`, and a
well-formed `## Last Released: v2.15.3`. `ai_rc/v2.15.4` exists locally — Active RC and
branch state agree. No drift.

### Finding 15: Step 6.8 — synthetic release simulation

**Category:** pass

Trigger condition met (the diff touches `team/scripts/lib/cm/release_known_issue.sh`, a
release-lifecycle participant). `tests/integration/test_cm_release_synthetic.py`:
15 passed in 30.94s.

### Finding 16: Step 6.9 / 6.13 / 6.14 — constraint, skip-gate, and standards checks

**Category:** pass

- No secret-shaped strings (`password`, `api_key`, `secret`, `token =`) anywhere in the
  RC diff.
- Skip-cites-real-bug: no placeholder skip IDs in `team/tests/` (the only grep hits are
  archived runner logs under `team/tests/artifacts/` quoting the gate's own output);
  no skip annotation cites a `BUG-NNNN`, so there is no missing bug file. The gated
  runner's own `lint_skip_bug_gate` agrees: `0 missing files`, `OK`.
- Standards spot-check: the RC adds no new script and no new `--help` surface; the new
  comments describe behaviour and name no bug, task, or version identifier; the
  severity vocabulary and the `unspecified` fallback are not hardcoded anew, and the
  new helpers live beside the code they serve rather than duplicating a `lib/`
  primitive. `lint_test_anti_patterns.py` exit 0 (0 findings / 434 files) and
  `lint_orphan_tests.py` exit 0 (256 test files covered, no orphans).

### Finding 17: Step 6.10 — prereq dependency graph

**Category:** pass

The v2.15.4 task set is `PM-20260910-007` (DONE), `CM-20260910-058-open-rc-2-15-4`
(DONE), `CODER-20260910-056` (DONE), `CODER-20260910-057` (DONE),
`TESTER-20260910-059` (WORKING — this task), `CM-20260910-061-release-2-15-4`
(WAITING). No cycles; every referenced prerequisite task ID exists; the lifecycle
bookends are correctly linked (CM-open first, TESTER + CM-release last, CM-release
gated on both CODERs and TESTER). Sequence numbers 056–059 and 061 do not collide —
060 belongs to an earlier dated task (`CODER-20260907-060`), consistent with a
project-global counter. Queue markers agree with task states: `[x]` on the four DONE
tasks, `[A]` on this WORKING task, `[W]` on the WAITING CM release task.

### Finding 18: Step 6.11 — spot-checks outside the spec

**Category:** pass

- **State drift:** none (Finding 14). The canonical dev tree at
  `$HOME/develop/pgai-agent-kanban` is parked on `ai_main` and clean.
- **Untested new mechanisms:** the two new helpers (`first_token`, `table_cell`) are
  both covered by tests that demonstrably fail without them (Finding 7).
- **Regressions of prior gaps:** the BUG-0219 stray-empty-commit pattern is absent —
  both non-merge commits on the RC carry real diffs. The BUG-0231 scrub is intact
  (Finding 13). `VERSION` still reads `v2.15.3` and `CHANGELOG.md` is unstamped, which
  is the expected pre-release state (CM writes both at release time).

## Lint Findings (non-blocking)

- `comment_provenance` — `lint_comment_provenance` exits 1 with 10 findings, every one
  a `[BUG-ID]` reference to `BUG-0228` in a comment or docstring:
  `team/scripts/why.sh:180`, `team/scripts/why.sh:264`,
  `team/tests/integration/test_defer_campaign_lifecycle.py:281`,
  `team/tests/unit/test_defer_operator_verbs.py:133`, `:265`, `:287`, `:307`, `:372`,
  `team/tests/unit/test_why_details_pane.py:419`, `:486`. All ten are pre-existing and
  none is in a file this RC touched — the RC's own three files are clean.
  **Cites BUG-0230 (open)**, whose instance list is exactly these ten. No duplicate
  filed. Recorded in both runners' findings files
  (`/tmp/pgai_kanban_tmp/lint/findings-unit.json`, `.../findings-integration.json`),
  severity `quality`, non-fatal — both gated runners still exited 0.

Per SOP, a quality-class finding carries zero runtime-failure risk, does not feed the
Step 8 decision tree, and does not raise `## Systemic Risk`. The recommendation stays
`PASS`.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=3d freshness=stale — the docker/podman claude-abliterated pairs failed on a Dockerfile `FROM` parse error and podman/rocky9 on a `COPY --chmod` step; cites BUG-0167, filed by that deep-verify run itself and since closed, and the record is stale on the version count alone (3d is inside the 7-day window, but v2.9.20 is not the v2.15.4 under verification), so nothing has been built cold against this tree.

The lane was not run during this verification, per Step 5d — no container build was
attempted and no container engine was invoked. `team/scripts/deep-verify.sh` is present
in the tree, so the images are not shipping with no cold-build gate anywhere in front
of them; the red record is cited, not re-filed, and it does not move the
recommendation or the systemic risk of this RC.

## Autonomous criterion

| # | Check | Answer |
|---|---|---|
| 1 | Any task force-promoted from WAITING to BACKLOG manually? | **NO** |
| 2 | `release-state.md` edited outside the `cm-*` scripts? | **NO** |
| 3 | Any scripts patched mid-build? | **NO** |
| 4 | Any queue file manually edited? | **NO** |

**Manual interventions required: none.**

Evidence: the wake log shows both WAITING→BACKLOG promotions performed by
`wake(coder)` ("prerequisites satisfied, promoting to BACKLOG"), each followed by
worktree creation, a WORKING `[A]` transition, a model stamp, and a clean
`finished ... with state DONE` plus worktree teardown and canonical-tree restore. No
completion-gate refusal appears for either CODER task. `release-state.md`'s mtime
(`20:03:17.009`) is the same instant as its own `RC Opened At` field, i.e. it was
written once by `cm-open-rc.sh` and not since. Queue and status mtimes form a single
monotonic chain matching the log (19:56 bundle → 19:57 PM → 20:03 RC open → 20:33
CODER-056 → 21:08 CODER-057 → 21:08 TESTER promoted). All four RC commits are the
expected author and fall inside the RC window; the one file the RC changes under
`team/scripts/` is the ticket's own deliverable, not a tooling patch. No `HALT` or
`HALT-AFTER` flag exists at either scope.

One observation, not an intervention: the PM wake at 20:01:44 logged
`[pm-materialize] Plan already materialized (idempotent no-op)`. That is the
double-materialization guard doing its job — exactly one ticket set exists for
v2.15.4, with no duplicates — so it is evidence of a working guard rather than of
manual repair.

## Caveats

- **Fresh verification.** The artifacts directory was empty at task start (no
  `.previous-RUN-N` files), so this is a first run; nothing was inherited from a prior
  report.
- **Negative controls were run off-tree.** Both reverts (the pre-fix library, and the
  emptied tenth argument to `cm_release_generate_notes`) were applied to a `cp -a`
  copy of the worktree under `/tmp/pgai_kanban_tmp/tester/negctl`, never to the RC
  worktree, which stayed clean throughout. Ad-hoc pytest invocations each ran with
  `PGAI_AGENT_KANBAN_ROOT_PATH` repointed at a fresh `mktemp -d` root under the
  framework temp root, per Live-Install Safety.
- **Test fidelity.** The new unit tests drive the real bash block builder through the
  shell harness (`source` + function call), not a Python re-implementation, and the
  integration test asserts on the notes file the pipeline actually wrote. The unit
  fixture's `_append_multiline_severity_section` deliberately wraps its paragraph onto
  a second physical line, because a single-line paragraph would be collapsed by the
  same whitespace flattening and would mask the exposure. No shared primitive is
  stubbed in the new tests. No fidelity gap found.
- **Observation (out of scope, pre-existing).** The substance cell can still be very
  long: rendering `BUG-0182` produces a single valid four-cell row whose first cell
  runs to several hundred characters, because `first_sentence` searches for
  `[.!?]` and that bug's `## Symptom` opens with a colon and a fenced code block. The
  row is well-formed and this RC's acceptance criteria are met; the readability of an
  over-long substance cell is `first_sentence` behaviour that predates this RC and lies
  outside both bundled items. Recorded here for the operator, not filed, and not
  counted as a finding.
- **Role-file path note (informational).** Step 5d's reader path
  (`$PGAI_AGENT_KANBAN_ROOT_PATH/team/scripts/lib/deep_verify_ledger.sh`) does not
  exist in an install root, where the `team/` layer is flattened away; the reader is at
  `$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/lib/deep_verify_ledger.sh` and was sourced from
  there successfully. This is a documentation-path nuance in the role file, not a defect
  in the RC under verification, and BUG-0187 already tracks role files citing `team/`
  paths.
