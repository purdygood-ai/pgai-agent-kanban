# Verification Report: rc/v2.15.3

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

medium

## Summary

RC `ai_rc/v2.15.3` at commit `3f654cd` was verified against
`requirements/v2.15.3-bugfix-bundle-20260910.md` — a single-item bundle
carrying `BUG-0232-bugs-skipped-render-misses-autogen.md` — and against the
acceptance criteria of the four tickets it decomposed into (CODER-048 scrub
prose repair, CODER-049 render on the auto-generate notes path, CODER-050
end-to-end coverage of that path, WRITER-051 document truth). The RC
delivers what it was chartered to deliver, and I confirmed it behaviorally
rather than structurally: the internal-identifier scrub now substitutes a
neutral noun phrase instead of deleting the token, leaving publishable
prose; the auto-generated release notes carry a `## Bugs Skipped` section
fed from the pre-built, pre-scrubbed block, with an explicit `None` empty
state and no stray table artefacts; driving the real chain (bug files →
`build_bugs_skipped_notes_block` → the extracted generator) with task
identifiers of all six agent prefixes produced notes that contain none of
them; and the WRITER-draft path cannot double-render, because the
generator's `git ls-tree HEAD` guard skips the whole block once the squash
has carried the notes file onto main. Both gated runners exited 0 (unit
3770 passed / 3 skipped; integration 442 passed / 1 skipped). Three findings are filed as follow-ups, none of which
affects this release's usability: the new end-to-end test stays green when
the rendered table stops arriving at the generator (BUG-0233); the rendered
table's severity cell is filled with the whole body of a bug file's severity
section, newlines included, which breaks the row for the 33 of 72 bug files
written that way (BUG-0235 — this release's own notes are clean because its
three new bug files were authored in the template's bare-value shape); and
one release-notes generation step is narrated under two different step
numbers (BUG-0234, pre-existing). Systemic risk is `medium`, carried by
BUG-0235: a real defect that ships and gets fixed next iteration, damaging a
published document's formatting and nothing else.

## Scope Verified

- Worktree: `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260910-053-verify-2-15-3`,
  detached at `3f654cdae9abe5a3f7d65f514bcd5a64859ef906` == `ai_rc/v2.15.3`.
- Requirements doc: `projects/pgai-agent-kanban/requirements/v2.15.3-bugfix-bundle-20260910.md`.
  Its own acceptance criteria are generic (each bundled item addressed, Python
  compiles, bash syntax clean); the substantive criteria are BUG-0232's five
  `## Acceptance` items and the four tickets' criteria.
- RC diff vs `ai_main`: 9 files, 706 insertions, 30 deletions, 8 commits
  (4 ticket commits + 4 `--no-ff` merges).

## Pre-flight

- HEAD matches the RC branch; `git status --short` clean; `git stash list` empty.
- Merge verification over every DONE task with a `## Feature Branch` value:
  no anomalies. All four cycle feature branches were deleted after merge
  (the normal post-merge shape), and all four merge commits are on the RC.
- `release-state.md`: `Active RC: v2.15.3`, `Last Released: v2.15.2`
  (well-formed). `pp_last_released_version` returns `v2.15.2`;
  `semver_gt v2.15.3 v2.15.2` is true. No drift.
- Prerequisite graph: acyclic, every referenced task ID exists, bookends
  present (CM-052 open RC → four tickets → TESTER-053 → CM-055 release,
  currently WAITING). No sequence collisions.
- All changed `.py` files compile (`python3 -m py_compile`); the changed
  `.sh` file passes `bash -n`.

## Test Suites (authoritative: gated runners)

| Runner | Exit | Result |
|---|---|---|
| `bash team/scripts/run-unit-tests.sh --coverage` | **0** | 3770 passed, 3 skipped (481 s) |
| `bash team/scripts/run-integration-tests.sh --coverage` | **0** | 442 passed, 1 skipped (405 s) |

`UNIT_EXIT == 0` AND `INTEGRATION_EXIT == 0`. The three unit skips and one
integration skip are environment-conditional container-engine skips,
narrated by their own helpers; none defers a fix, and the skip-cites-real-bug
gate has nothing to resolve (no test in `team/tests/` carries a skip
annotation citing a `BUG-NNNN`; the only `BUG-NNNN`-shaped grep hits are
literal placeholder strings inside archived coverage run logs under
`team/tests/artifacts/`).

Step 6.8 (synthetic release simulation) applies — the diff touches
`team/scripts/lib/cm/release_notes.sh` — and
`tests/integration/test_cm_release_synthetic.py` ran green inside the gated
integration suite (15 cases).

## Code Coverage

- Unit:        line 75%  |  branch 61%   (14793 stmts, 4462 branches; blended `TOTAL` 72%)
- Integration: line 8%   |  branch 6%    (14784 stmts, 4462 branches; blended `TOTAL` 8%)

Informational only; read from `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/unit.json`
and `integration.json` (`percent_statements_covered` / `percent_branches_covered`).
`team/scripts/lib/cm/internal_id_scrub.py`, the module this RC repaired,
reports 100% in the unit run.

## Findings

### Finding 1: the auto-generate notes path renders the scrubbed Bugs-Skipped table

**Category:** pass

BUG-0232 `## Acceptance` item 1, and CODER-049's criteria 1-3. Verified
behaviorally, not by grep alone:

- `grep -c "Bugs Skipped" team/scripts/lib/cm/release_notes.sh` → 3 hits
  (comment, `SECTION_ORDER`, render branch).
- Extracted the real `RELNOTES_PY` heredoc (lines 209-545) and drove it with
  the production argv contract in a throwaway git repo under the framework
  temp root. With a non-empty block the section renders the block verbatim —
  header, separator, rows — between `## Other Changes` and `## Known Issues`,
  exactly where the documents say it sits. With an empty block the section
  renders `## Bugs Skipped` / `None`: no table header, no separator, no
  dangling blank, byte-stable for a no-findings release (CODER-049 criterion 3).
- End-to-end over the real chain: wrote six synthetic bug files whose
  `## Symptom` text carried a task identifier of each agent prefix, built the
  block through `build_bugs_skipped_notes_block` (sourced from
  `release_known_issue.sh`), fed the result to the generator, then ran
  `grep -rEn '(CODER|WRITER|TESTER|CM|PM|PO)-[0-9]{8}-[0-9]+'` over the
  written notes file: no output. Each row read
  `| The widget failed to open while another item was in flight. | internal-only | MEDIUM | Deferred because another item already covers the area. |`
  — scrubbed and grammatical (CODER-049 criterion 2).

### Finding 2: the scrub leaves readable prose and the identifier never survives

**Category:** pass

CODER-048's criteria 1-3 and the closing paragraph of BUG-0232's `## Fix`.
`_strip_internal_ids` now substitutes `_ID_REPLACEMENT_TEXT` (`"another
item"`) rather than deleting, and the choice is documented at the definition
with its reasoning, as the ticket required. Exercised directly:

| Input | Output |
|---|---|
| `Deferred because CODER-20260101-001-some-slug already covers the area.` | `Deferred because another item already covers the area.` |
| `CODER-20260101-001-some-slug` | `another item` |
| `See (PM-20260910-006-decompose-...-20260910) for context.` | `See (another item) for context.` |
| `Source Task: WRITER-20260910-051-cm-augmentation-doc-truth` | `Source Task: another item` |
| `No identifier here at all.` | unchanged |

The worked example from the bug report produces exactly the expected
sentence. `_INTERNAL_ID_RE` is unchanged (byte-identical in the diff), no
second copy of the scrub was created, and the three importing heredocs are
untouched. The new `team/tests/unit/test_internal_id_scrub.py` covers all
five required sentence positions with exact-string assertions plus both
token families and all six prefixes.

### Finding 3: the WRITER-draft path is unaffected and cannot double-render

**Category:** pass

CODER-049 criterion 4. Step 4e is untouched in the diff. The ordering in
`team/scripts/cm/release.sh` is ship policy (321) → squash to main (325) →
generate notes (330), and the generator opens with an idempotency guard that
skips its entire generation block when `git ls-tree --name-only HEAD
release-notes/<version>.md` returns non-empty. I verified that predicate
empirically in a throwaway repo: non-empty output once the notes file is
committed to HEAD, empty output when absent. So on the draft path the
generator does not run at all and exactly one `## Bugs Skipped` section can
exist; on the no-draft path it runs. The draft-path end-to-end test
(case (d), which asserts the scrubbed substance is present) passed inside
the green integration suite.

### Finding 4: the two documents agree with each other and with the code

**Category:** pass

WRITER-051's six criteria and BUG-0232 `## Acceptance` item 4. Read both
revised passages in full. `team/roles/CM.md` now states that `## Bugs
Skipped` is rendered on **both** notes paths, tells the agent never to type
or append that table, and names what the agent does author (Summary, What
Shipped, Bugs Resolved, Known Issues outside a KNOWN-ISSUE decision).
`docs/cm-release-reference.md` makes the same claim and adds the per-path
empty-state split. No surviving sentence in either document asserts the
block is hand-authored or that the render is draft-path-only. Both match
the code I exercised in Findings 1 and 3 (section between Other Changes and
Known Issues; literal `None` when empty; render-once per path). The lint
reminder is still described as advice, not a gate — which is true: nothing
in the release path refuses on its output. `RELEASE-NOTES-TEMPLATE.md`
(out of the ticket's scope) already says the section is rendered, so the
three surfaces are consistent. `grep -rEn
'(CODER|WRITER|TESTER|CM|PM|PO)-[0-9]{8}-[0-9]+' release-notes/
team/roles/CM.md docs/cm-release-reference.md` returns nothing.

### Finding 5: the new end-to-end test passes when the rendered table stops arriving

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks — one assertion in one test)
**Systemic Risk:** low (test-coverage hygiene; no runtime effect, no state at risk, nothing shipped is wrong today)
**Regression:** no (the coverage shortfall predates this RC — before it, the auto-generate notes path had no end-to-end test at all; `git show ai_main:team/tests/integration/test_cm_release_known_issue_carry_forward.py` contains no auto-generate case. This RC narrowed the shortfall and did not close it; no behavior that worked in v2.15.2 stopped working)
**Integrity Damage:** no
**Integrity:** none
**Filed As:** BUG-0233 (Path C-A — filed as BUG — code defect / regression / test failure)

The acceptance item this test answers is met as literally written, and its
control holds: removing the render wiring (the `SECTION_ORDER` entry and the
`elif section == "Bugs Skipped":` branch in `release_notes.sh`) makes all six
parametrized cases fail on `assert '## Bugs Skipped' in ...`. That matches
what the authoring ticket recorded, so its completion note is true.

The shortfall is one level deeper. The test's three assertions are the
`KNOWN-BUGS` status, the presence of the `## Bugs Skipped` heading, and the
absence of its own prefix. None observes the rendered rows. I kept the
section and removed only the value's arrival — changed the tenth positional
argument of the `cm_release_generate_notes` invocation from
`"${_BUGS_SKIPPED_NOTES_BLOCK:-}"` to `""`, simulating the global being
renamed, cleared, or computed in another shell — and all six cases passed
(`6 passed, 5 deselected in 14.75s`), with the notes file carrying
`## Bugs Skipped` / `None` and the report's six findings unrepresented.
That second shape is the one that recurs: the defect this release closed was
never a missing section, it was a value computed and then discarded before
it reached the file.

Nothing else covers the arrival. The new unit module does assert
pass-through of rows/header/separator, but it hands the block to the
generator itself as an argv value, so it proves the generator renders what
it is given, not that the pipeline still gives it anything. The
WRITER-draft sibling test does not have this hole — it asserts the scrubbed
substance sentence is present (line 717); the auto-generate case omitted the
equivalent assertion. The worktree was restored after both controls and is
byte-identical to its pre-experiment state.

### Finding 6: one release-notes generation step is narrated under two step numbers

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks — three strings)
**Systemic Risk:** low (operator-facing narration only)
**Regression:** no (pre-existing; the `[Step 8]` strings at `release_notes.sh:124` and `:141` are untouched by this RC's diff and ship identically in v2.15.2)
**Integrity Damage:** no
**Integrity:** none
**Filed As:** BUG-0234 (Path C-A)

`team/scripts/lib/cm/release_notes.sh` prints `[Step 8]` at lines 124 and
141 and `[Step 11a]` at line 553 for the same step, while
`release_ship_policy.sh` calls it Step 11a at lines 505, 590 and 602 —
including the operator-facing `skipping stamp (Step 11a will auto-generate)`
message. A release log therefore reads `[Step 4e] ... (Step 11a will
auto-generate)` followed immediately by `[Step 8] Generating release
notes ...`. This is the code-side wart WRITER-051 recorded in its completion
note: it deliberately named the function rather than a step number in
`team/roles/CM.md`, because either number would have put a wrong fact in a
governance file. That judgement was correct, and filing the wart is the
follow-up it asked for.

### Finding 7: rendered Bugs-Skipped cells are not normalized for a markdown table

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks — normalize the cell in one heredoc, plus a unit test)
**Systemic Risk:** medium (a real defect that ships and is fixed next iteration: it damages a published document's formatting only, and this release's own notes are clean because its bug files were authored in the template's bare-value shape)
**Regression:** no (the row-assembly code is untouched by this RC — the same extraction shipped in v2.15.2 and was already reachable through the Step 4e stamp. What this RC changes is reach, not behavior: the table now renders on the path every release takes. Stated plainly so CM can weigh it: no code path behaves differently than it did in v2.15.2, but a latent fragility moved from a path this project never uses to one it always uses)
**Integrity Damage:** no
**Integrity:** none
**Filed As:** BUG-0235 (Path C-A)

Found by driving the real render with real inputs — this report and the two
bugs it files — rather than with a synthetic fixture:

```
cm_build_bugs_skipped_ship_block <this report> <project bugs dir>
```

The severity cell came back as `MEDIUM THE SHIPPED BEHAVIOR IS CORRECT TODAY —
I VERIFIED THE RENDERED, SCRUBBED TABLE REACHES AUTO-GENERATED NOTES END TO
END, ...` and ran on for six more lines, ending in the file's `---` rule. The
cause is in `build_bugs_skipped_notes_block`
(`team/scripts/lib/cm/release_known_issue.sh`, heredoc around lines 435-455):
`desc` is flattened through `first_sentence`, but `severity` is
`parse_section(text, "Severity").strip()`, and `parse_section` returns the
whole section body up to the next `##` heading. A bug file that records why it
chose its rating therefore emits that paragraph into the cell, uppercased,
newlines and all — and the table row ends at the first newline.

This is not a rare input shape: **33 of the 72 bug files with a `## Severity`
section carry more than one line in it**, including every TESTER-filed bug
from BUG-0219 onward. A milder case of the same gap: no cell is sanitized for
`|`, and three current bug files (BUG-0182, BUG-0195, BUG-0202) carry one
inside the first sentence of their `## Symptom`, which is the substance cell.

**What I did about this release's own notes.** v2.15.3 takes the
auto-generate path and this report carries findings, so the table will render
into its notes. I authored BUG-0233, BUG-0234 and BUG-0235 with a bare value
under `## Severity` — the shape `bugs/templates/BUG-TEMPLATE.md` specifies —
and moved each rating's justification into its own `## Severity Rationale`
section. Re-running the render then produced exactly header + separator + one
line per row, every cell single-line. So this release publishes a clean table;
the fragility is filed for the loop rather than left to surface in whichever
future release happens to cite a conventionally-authored bug.

## Lint Findings (non-blocking)

- `comment_provenance` — `lint_comment_provenance` FAIL, 10 findings: the
  internal identifier `BUG-0228` cited in comments in `team/scripts/why.sh`
  (180, 264), `team/tests/integration/test_defer_campaign_lifecycle.py:281`,
  `team/tests/unit/test_defer_operator_verbs.py` (133, 265, 287, 307, 372)
  and `team/tests/unit/test_why_details_pane.py` (419, 486) — cites
  **BUG-0230** (open), which lists exactly these ten sites. No duplicate
  filed. None of the ten files is touched by this RC, and the lint reports
  no finding against any file this RC changed or under `release-notes/`.

Both findings files were present; the same single quality-class finding
appears in each (`findings-unit.json`, `findings-integration.json`), one
group after aggregation by lint class.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=3d freshness=stale — the last recorded lane run was red on the claude-abliterated flavor under both engines and on podman/rocky9, and it is stale on version (v2.9.20 is not the v2.15.3 under verification) though within the 7-day window; it cites BUG-0167, filed by that deep-verify run and since closed (`## Status: done`), so nothing is re-filed here and no cold build was run during this verification.

## Autonomous criterion

1. Tasks force-promoted from WAITING to BACKLOG by hand? **NO.** All five
   cycle tasks (CM-052, CODER-048/049/050, WRITER-051) show DONE with
   ordinary agent-written summaries; this TESTER task's own promotion note
   was written by the wake script.
2. `release-state.md` edited outside the CM scripts? **NO.** Its four fields
   are exactly what `cm-open-rc.sh` writes, with `RC Opened By Task:
   CM-20260910-052-open-rc-2-15-3` and `Last Released: v2.15.2` consistent
   with the git tags.
3. Scripts patched mid-build? **NO** in the sense the check means. Every
   change under `team/scripts/` on this RC traces to CODER-048 and CODER-049,
   the tickets chartered to make it; there is no out-of-band tooling commit,
   and all eight RC commits carry the agents' own subjects and timestamps
   inside the RC window (18:13-19:18 on the day the RC opened at 17:42).
4. Queue files manually edited? **NO** evidence. The four completed tickets
   carry `[x]`, this task carries `[A]`, and the entry shapes match what the
   wake script writes.

Manual interventions required: none.

## Caveats and observations

- **The scrub trades information for grammar, by design.** Substituting
  `"another item"` makes sentences parse but flattens distinctions: `See
  BUG-0001 and BUG-0002 for the two earlier attempts.` renders as `See
  another item and another item for the two earlier attempts.`, and an
  adjectival use (`the BUG-0011 cleanup`) renders `the another item
  cleanup`. CODER-048 recorded both, and the ticket's Goal permits either
  readable prose *or* a visible stand-in, so this is inside the criterion.
  Worth knowing when the next reader wonders why a published rationale names
  nothing at all.
- **The scrub's stand-in reads oddly when the removed token led the
  sentence.** A finding body that opens `BUG-0232 ## Acceptance item 2 is
  met ...` renders as `another item ## Acceptance item 2 is met ...` in the
  notes. Inside the criterion (the token never survives, and the sentence
  still parses), but worth knowing when authoring text the render will quote:
  I reworded Finding 5's opening sentence for exactly this reason.
- **A `## Public ID` holding an internal identifier would now render
  `another item` rather than falling through to `internal-only`**
  (`release_known_issue.sh:448-449`). Not reachable in practice — the field
  holds `KI-<version>.<counter>` by template and every real file matches —
  and strictly better than the previous behavior (which emptied the cell).
  Observation only, no filing.
- **This release will itself take the fixed path.** There is no
  `release-notes/v2.15.3.md` on the RC, so Step 4e will skip and the
  generator will write the notes, rendering this report's two findings
  through the scrub. That makes v2.15.3 the first release to exercise the
  repaired path in production.
- **Two spellings of the integrity field, deliberately.** The installed
  kanban root this verification ran under is `v2.9.19`, and its
  `roles/TESTER.md` — the procedure I was invoked with — specifies
  `**Integrity Damage:** yes|no`. The role file this RC ships
  (`team/roles/TESTER.md:247`) and the ship-policy parser it ships
  (`release_ship_policy.sh`, `field_value(section, "Integrity")`) both use
  `**Integrity:** breakage|destructive|none` instead. The shipped tree is
  internally consistent; the divergence is version skew between the live
  install and the dev tree, not a defect in this RC, so nothing is filed for
  it. Each `bug` finding above carries both labels so the report parses
  under either contract. It makes no difference to the decision here: the
  parser consults only findings whose own `Systemic Risk` is `high`, and
  neither of mine is.
- The two negative controls in Finding 5 were run with
  `PGAI_AGENT_KANBAN_ROOT_PATH` repointed at throwaway temp roots under the
  framework temp root; no command in this verification ran against the live
  kanban root. The worktree is clean and byte-identical to
  `ai_rc/v2.15.3` at exit.

## Artifacts

- `artifacts/report.md` (this file)
- `artifacts/gaps.md`
- Path C filings: `bugs/BUG-0233-autogen-notes-test-passes-without-block.md`,
  `bugs/BUG-0234-release-notes-step-number-narration.md`,
  `bugs/BUG-0235-bugs-skipped-cells-not-table-safe.md`
- Captured runner output: `${PGAI_AGENT_KANBAN_TEMP_DIR}/tester/unit_output.txt`,
  `${PGAI_AGENT_KANBAN_TEMP_DIR}/tester/integration_output.txt`
