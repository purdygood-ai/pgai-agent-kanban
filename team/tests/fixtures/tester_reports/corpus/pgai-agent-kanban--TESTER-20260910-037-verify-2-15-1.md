# Verification Report: ai_rc/v2.15.1

**Task:** TESTER-20260910-037-verify-2-15-1
**RC branch:** `ai_rc/v2.15.1` @ `0dc08aada5e6e6ace1988b6d46f4f15e9720db39`
**Baseline:** `ai_main`
**Requirements doc:** `projects/pgai-agent-kanban/requirements/v2.15.1-bugfix-bundle-20260910.md`
**Worktree:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260910-037-verify-2-15-1` (detached HEAD, clean, no stashes)
**Date:** 2026-09-10

## Summary

Verified the v2.15.1 bug bundle (BUG-0227, BUG-0228, BUG-0224, BUG-0225,
BUG-0226, BUG-0229) against `ai_rc/v2.15.1`. Pre-flight passed: the worktree
HEAD matches the RC branch, the tree is clean, there are no stashes, and no
DONE task's feature branch is unmerged or carries zero unique commits. Both
gated runners exit 0 — `run-unit-tests.sh` 3735 passed / 3 skipped, 
`run-integration-tests.sh` 435 passed / 1 skipped. Every acceptance criterion
in the requirements doc's own list is met (all bundled items addressed, all
changed Python compiles, all changed bash passes `bash -n`).

Four of the six bundled bugs are fully closed and behaviourally verified:
BUG-0227 (the docs manifest now delivers `operator-api.md`, all six citation
tests pass, and the manifest's closure rule still holds), BUG-0228 (`why.sh
--details` now exits with the verdict's own code — confirmed by hand across all
five verdicts plus the section-refusal case), BUG-0225 (the internal-ID scrub
now consumes the whole task-ID slug, with no stranded fragment and the normal
case undisturbed), and BUG-0226 (the PM governance byte-budget finding is gone
from both runners' findings files).

Two gaps remain, both in the provenance-hygiene class the bundle was partly
chartered to close. This RC added ten new `comment_provenance` violations of
its own by citing `BUG-0228` in comments and test docstrings, taking the tree
from 3 findings at v2.15.0 to 11 at v2.15.1. And BUG-0224's structural half
landed as an unwired helper: `build_bugs_skipped_notes_block` exists and is
well tested, but nothing in the release pipeline calls it, so CM still
hand-authors the `## Bugs Skipped` block with no scrub — and the already-shipped
`release-notes/v2.15.0.md:64` leaks a task ID by exactly the mechanism BUG-0224
described. Neither gap affects any runtime path, corrupts state, or risks data.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

low

## Test Suites

| Suite | Command | Exit | Result |
|---|---|---|---|
| Unit | `bash team/scripts/run-unit-tests.sh --coverage` | **0** | 3735 passed, 3 skipped in 518.80s |
| Integration | `bash team/scripts/run-integration-tests.sh --coverage` | **0** | 435 passed, 1 skipped in 405.81s |

Both gated runners — the same commands an operator or CM would run, including
the breakage-class lint pre-flight — exited 0. That is the PASS evidence for the
suites; no direct-pytest result is quoted anywhere in this report as a suite
verdict.

## Code Coverage

- Unit:        line 75%  |  branch 61%   (14784 stmts, 4462 branches; blended TOTAL 72%)
- Integration: line 8%   |  branch 6%    (blended TOTAL 8%)

Source: `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/unit.json` and
`.../integration.json` `totals` objects (`percent_statements_covered_display`,
`percent_branches_covered_display`). Informational only; coverage does not enter
the recommendation.

## Findings

### Finding 1: BUG-0227 — docs manifest now delivers operator-api.md

**Category:** pass

- `grep -q '^operator-api\.md$' team/config/docs-manifest.txt` — succeeds.
- `team/tests/unit/test_installed_governance_docs_citations_resolve.py` —
  6 passed, 0 failed inside the gated unit run (`...... [76%]`). Both the
  fresh-install and upgraded-root variants are green, which is the criterion
  "after a fresh `install.sh` … and the same after the `upgrade.sh` route".
- `bash team/scripts/run-unit-tests.sh` exits 0 (this was the red suite the bug
  was filed against).
- Closure rule re-checked independently: the only `docs/` path
  `docs/operator-api.md` itself cites is `docs/operator-commands.md`, which the
  manifest already names. The dangling pointer did not move one hop deeper.

### Finding 2: BUG-0228 — why.sh --details now exits with the verdict's own code

**Category:** pass

Behavioral verification (Step 6.5), run by hand against a throwaway fixture
kanban root under `$PGAI_AGENT_KANBAN_TEMP_DIR` (never the live install), with
`PGAI_AGENT_KANBAN_ROOT_PATH` repointed for every invocation:

| Fixture state | `why.sh --project X` | `why.sh --project X --details` |
|---|---|---|
| BLOCKED task | 4 | **4** |
| project `HALT` present | 5 | **5** |
| no tasks (IDLE) | 2 | **2** |
| WORKING task, fresh mtime | 0 | **0** |
| WORKING task, mtime 3h old (stalled) | 3 | **3** |
| malformed `DEFER-LOW-BUGS` (section refusal) | — | **1**, stdout empty (0 bytes) |

All five verdicts now agree, and the refusal case still wins at exit 1 with
nothing printed — the exact failure mode the bug's Reproduction step 7 recorded
as `0` now reports `4`. The bug's third acceptance criterion (a unit test
pinning at least HALTED and RUNNING-stalled) is met by five new tests in
`team/tests/unit/test_why_details_pane.py`, plus one guarding that a section
refusal still exits 1.

Implementation reviewed: `_why_print_details` now parses `exit_code=` out of
`verdict_resolve_project`'s block, refuses (`return 1`) if the field is absent,
and stashes it in `_WHY_LAST_EXIT_CODE`; the caller does
`_why_print_details "${_project}" || exit 1` then `exit "${_WHY_LAST_EXIT_CODE}"`.
`team/scripts/lib/verdict.sh` sets `exit_code` on every one of its five
resolution paths, so the new "field absent" refusal is unreachable in practice
and is a belt-and-braces guard rather than a live behaviour change.

### Finding 3: BUG-0225 — internal-ID scrub consumes the whole task-ID slug

**Category:** pass

Behavioral verification against the bug's own reproduction, from the worktree's
`team/` directory with scratch fixtures under `$PGAI_AGENT_KANBAN_TEMP_DIR`:

```
Symptom text  : "The importer crashes on a malformed header (Source Task: TESTER-20260909-045-verify-2-14-1). More prose."
Rendered      : "- The importer crashes on a malformed header (Source Task: ). (internal-only, not yet disclosed) — No workaround available."
Public ID     : "KI-2026.09 (see CODER-20260910-011-provenance-test-prose-pin)"
Rendered      : "- **KI-2026.09 (see )** — A disclosed pre-existing defect in the importer. — No workaround available."
Normal case   : a bug whose Symptom carries no identifier renders unchanged.
```

No `-verify-2-14-1` fragment survives, which was the whole defect. The residual
`(Source Task: )` / `(see )` shape is what the bug's own Expected section
explicitly permits ("or, better, drops the emptied parenthetical entirely — but
in no case leaves a hyphen-leading fragment"). Both copies of the pattern were
fixed (`release_known_issue.sh` and the pre-existing `release_notes.sh` copy),
and both now have positive assertions on the surviving text via
`_STRANDED_HYPHEN_FRAGMENT_RE`, closing the "the test passes against mangled
output" hole the bug identified. See Finding 6 for the duplication caveat.

### Finding 4: BUG-0226 — PM governance byte budget retuned

**Category:** pass

`kanban.cfg_example` `budget_bytes_pm` moved 73728 → 81920 with a comment
recording the re-baseline against the 76391-byte assembled pack. Verified
end-to-end rather than by inspection: `findings-unit.json` and
`findings-integration.json` now carry **one** finding each (`comment_provenance`)
and no `governance_byte_budget` entry at all — it was present on every run
before this RC. Both runners still exit 0, so the check remains quality-class.

The bug's criterion "`kanban.cfg` and `kanban.cfg_example` agree" is
not applicable as written: the dev tree ships no `kanban.cfg`, and the gated
runners invoke the lint with an explicit
`--kanban-cfg "${REPO_ROOT}/kanban.cfg_example"`, so `kanban.cfg_example` is the
value actually in effect at verification time. The live install's
`$HOME/pgai_agent_kanban/kanban.cfg` has no `[governance]` section at all,
so there is nothing there to disagree with.

### Finding 5: BUG-0229 — the two named provenance sites are clean

**Category:** pass

- `grep -nE 'CODER-[0-9]{8}-[0-9]+' team/scripts/verify-rc-state.sh
  team/tests/integration/test_verify_rc_state_two_project_prefix_fixture.py`
  returns nothing.
- Neither path appears in the `comment_provenance` finding list.
- Both rewrites are genuine rewrites, not deletions: the script's header now
  reads `Branch-prefix / field-validation coverage summary:` and keeps its
  full a–f enumeration; the fixture docstring still states what the two-project
  shape proves.
- No `provenance-allowlist` marker was added to either file.

BUG-0229's own acceptance is met. The class regressed elsewhere — see Finding 7.

### Finding 6: three copies of the internal-ID scrub now exist

**Category:** pass-with-caveat

`_INTERNAL_ID_RE` / `_strip_internal_ids` now appears at
`team/scripts/lib/cm/release_notes.sh:282`,
`team/scripts/lib/cm/release_known_issue.sh:179`, and — new in this RC —
`team/scripts/lib/cm/release_known_issue.sh:273` inside
`build_bugs_skipped_notes_block`. BUG-0225's Fix section named this outcome in
advance: "Fix both in the same change, or extract the pair into one shared
source both heredocs read **so a third copy cannot appear**." The RC took the
first option and then added the third copy in the same change. All three copies
are currently identical and all three are tested, so nothing is wrong today;
the caveat is that the next fix to this pattern must now land in three places.
Recorded as a code-hygiene note inside BUG-0231 rather than as a separate
filing.

### Finding 7: this RC added ten new comment-provenance violations

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (comment-only; no runtime path, no state, no data; both gated runners exit 0)
**Regression:** yes (all ten sites arrive with this RC's commits; the class grew from 3 findings at ai_v2.15.0 to 11 at ai_rc/v2.15.1)
**Integrity Damage:** no
**Filed As:** BUG-0230 (Path C-A — filed as BUG — code defect / regression / test failure)

`python3 team/scripts/lint_comment_provenance.py` exits 1 with 11 findings. Ten
are new in this RC and every one cites `BUG-0228` in a comment or docstring:

```
team/scripts/why.sh:180                                     [BUG-ID] 'BUG-0228'
team/scripts/why.sh:264                                     [BUG-ID] 'BUG-0228'
team/tests/integration/test_defer_campaign_lifecycle.py:281 [BUG-ID] 'BUG-0228'
team/tests/unit/test_defer_operator_verbs.py:133            [BUG-ID] 'BUG-0228'
team/tests/unit/test_defer_operator_verbs.py:265            [BUG-ID] 'BUG-0228'
team/tests/unit/test_defer_operator_verbs.py:287            [BUG-ID] 'BUG-0228'
team/tests/unit/test_defer_operator_verbs.py:307            [BUG-ID] 'BUG-0228'
team/tests/unit/test_defer_operator_verbs.py:372            [BUG-ID] 'BUG-0228'
team/tests/unit/test_why_details_pane.py:419                [BUG-ID] 'BUG-0228'
team/tests/unit/test_why_details_pane.py:486                [BUG-ID] 'BUG-0228'
```

`git diff ai_main..ai_rc/v2.15.1 | grep -c "^+.*BUG-0228"` reports 12 added
lines carrying the identifier. None of the citations is load-bearing: in each
case the surrounding prose already states the behaviour completely and the
identifier can be deleted without losing meaning.

This is categorised as a Step-7 `bug` rather than left in the non-blocking lint
section because it is a regression, introduced by this RC, in the exact defect
class one of the RC's own bundled items (BUG-0229) was chartered to shrink. The
finding is nevertheless low risk in every dimension that matters for shipping.

### Finding 8: BUG-0224's structural half landed as an unwired helper

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (published-document hygiene; no runtime path, no state, no data)
**Regression:** no (the offending text at `release-notes/v2.15.0.md:64` is on `ai_main` and `git diff --stat ai_main..ai_rc/v2.15.1 -- release-notes/v2.15.0.md` is empty; the unwired-helper shape is a shortfall in this RC's fix, not a behaviour this RC broke)
**Integrity Damage:** no
**Filed As:** BUG-0231 (Path C-A)

BUG-0224 asked for two things. The text repair to `release-notes/v2.14.2.md`
landed and is clean. The durable half — "the `## Bugs Skipped` authoring path
cannot emit a kanban task ID again" — landed as
`build_bugs_skipped_notes_block` in
`team/scripts/lib/cm/release_known_issue.sh`, correct and covered by eight new
unit tests including one parameterised over all six agent prefixes. But nothing
calls it: `grep -rn "build_bugs_skipped_notes_block" --include=*.sh .` finds no
caller outside its own definition file, and `cm-release.sh` still leaves the
block to CM to type by hand with no scrub and no check in front of it.

The consequence is already observable. `release-notes/v2.15.0.md:64` — shipped,
on `ai_main` — reads `… cite task ID \`CODER-20260910-023\` — provenance
litter …`, a published document naming a kanban task folder no reader can open.
It got there exactly the way BUG-0224 predicted: CM copied a TESTER finding
summary into the Bugs-Skipped block, and that summary was itself about a leaked
identifier. Third release running (v2.14.1, v2.14.2, v2.15.0).

Three of BUG-0224's five acceptance criteria therefore fail on this RC:

- criterion 1 — `lint_comment_provenance.py` exits 0 with zero findings: **FAIL** (exit 1, 11 findings)
- criterion 2 — `grep -rEn '(CODER|WRITER|TESTER|CM|PM|PO)-[0-9]{8}-[0-9]+' release-notes/` returns nothing: **FAIL** (one hit, `release-notes/v2.15.0.md:64`)
- criterion 3 — no `provenance-allowlist` marker added to `release-notes/v2.14.2.md`: **PASS**
- criterion 4 — neither runner's findings file contains a `comment_provenance` finding: **FAIL**
- criterion 5 — a test drives the authoring path with a task ID for each of the six prefixes: **PASS**

### Finding 9: requirements-doc acceptance criteria

**Category:** pass

- "Each bundled item is addressed in the implementation" — all six items have
  landed changes on the RC. Four are fully closed (Findings 1–5); BUG-0224 is
  partially closed (Finding 8) and BUG-0229 is closed for its own named sites
  while the class regressed elsewhere (Finding 7).
- "All Python files compile cleanly" — `python3 -m py_compile` over every `.py`
  in `git diff --name-only ai_main..ai_rc/v2.15.1`: clean. The gated runners'
  breakage-class pre-flight compiles the whole tree and passed.
- "All bash files pass syntax check" — `bash -n` over every `.sh` in the diff:
  clean.

### Finding 10: release-state drift check (Step 6.1)

**Category:** pass

- `pp_last_released_version pgai-agent-kanban` → `v2.15.0`;
  `semver_gt v2.15.1 v2.15.0` is true.
- `release-state.md`: `Active RC: v2.15.1`, `RC Opened At:
  2026-09-10T12:35:16+00:00`, `RC Opened By Task:
  CM-20260910-036-open-rc-2-15-1`, `Last Released: v2.15.0` (well-formed
  `vX.Y.Z`).
- `ai_rc/v2.15.1` exists locally, so `Active RC` is not pointing at a branch
  that does not exist.
- No kanban-root or project-scoped `HALT` / `HALT-AFTER` flag present.

### Finding 11: skip-cites-real-bug gate (Step 6.13)

**Category:** pass

No placeholder skip IDs (`BUG-SKIP`, `skip.*BUG-[A-Za-z]`) in `team/tests/`
outside archived coverage logs under `team/tests/artifacts/`, and no cited
`BUG-NNNN` in a skip annotation resolves to a missing file in `bugs/`. The
runners' own `lint_skip_bug_gate.sh` pre-flight also passed inside both green
runs.

### Finding 12: prereq dependency graph (Step 6.10)

**Category:** pass

`CM-20260910-036-open-rc-2-15-1` (DONE) →
`PM-20260910-004-decompose-v2-15-1-bugfix-bundle-20260910` (DONE) →
`CODER-20260910-031/032/033/035` + `WRITER-20260910-034` (all DONE) →
`TESTER-20260910-037-verify-2-15-1` (this task) →
`CM-20260910-039-release-2-15-1`. No cycles, every referenced task ID exists,
sequence numbers are consistent and non-colliding, and the lifecycle bookends
are present at both ends.

## Lint Findings (non-blocking)

- `comment_provenance` — 11 instances on the RC tree (10 new `BUG-0228`
  citations in `why.sh` and three test files; 1 pre-existing task-ID citation in
  `release-notes/v2.15.0.md:64`). Both runners record it and exit 0; the
  standalone lint exits 1. — filed **BUG-0230** (Path C-A). One bug for the one
  lint class, with every instance listed inside it by path and line; the
  structural cause of the eleventh instance is filed separately as BUG-0231
  because it is a pipeline defect, not a lint instance.

No other quality-class findings. `governance_byte_budget`, present on every run
since at least v2.14.1, is absent from both findings files for the first time —
see Finding 4.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=3d freshness=stale — the last lane run was red on the claude-abliterated flavor under both engines and on podman/rocky9, and it is stale on the version count (v2.9.20 is not the v2.15.1 under verification) though within the 7-day window; cites BUG-0167, filed by that deep-verify run and since closed.

Read through `deep_verify_ledger_read_latest` from
`team/scripts/lib/deep_verify_ledger.sh` in the RC worktree — note that the
live kanban root has no copy of that library, so the reader must be sourced
from the worktree. The tree ships three Dockerfiles
(`docker/claude-abliterated`, `docker/debian`, `docker/rocky9`), so this
section is mandatory. No cold-build lane was run by this verification, per
Step 5d. This line does not move the recommendation or the systemic risk.

## Autonomous criterion

Manual interventions required: none.

1. Tasks force-promoted from WAITING to BACKLOG by hand? **NO.** Every
   promotion in `logs/cron-coder.log`, `cron-writer.log` and `cron-tester.log`
   is a `wake(<agent>): re-check complete: promoted N task(s) from WAITING to
   BACKLOG` line written by the wake script, including the four promoted at
   12:36:15 after the RC opened.
2. `release-state.md` edited outside `cm-open-rc.sh` / `cm-release.sh` /
   `cm-cancel-rc.sh`? **NO.** The four fields are internally consistent and
   `RC Opened By Task` names the real, DONE CM task.
3. Scripts patched mid-build? **NO.** Every commit on the RC is a CODER/WRITER
   feature-branch merge for one of the five decomposed tickets; no out-of-band
   commit touches `team/scripts/` or `subagents/`.
4. Queue files manually edited? **NO.** `tester_backlog.md` carries this task
   at `[A]`, written by the wake script; no marker is inconsistent with its
   task's state.

## Caveats

- **CM: scrub this report's finding text before pasting it into the release
  notes.** This report names `BUG-0228`, `CODER-20260910-023` and several task
  IDs in the course of describing Findings 7 and 8. The `## Bugs Skipped` block
  is still hand-authored with no scrub in front of it (Finding 8), so copying
  a finding summary verbatim is exactly how `release-notes/v2.15.0.md:64` was
  produced. Describe the defects in reader-facing terms and name only
  `BUG-NNNN` identifiers, which the lint's prose surface exempts. Running
  `python3 team/scripts/lint_comment_provenance.py` over the tree after the
  notes are written is the cheap confirmation.
- Behavioral reproductions in Findings 2 and 3 were run with
  `PGAI_AGENT_KANBAN_ROOT_PATH` repointed at throwaway roots created with
  `mktemp -d -p "$PGAI_AGENT_KANBAN_TEMP_DIR"`. The live install was never a
  target of any state-mutating invocation.
- `docs/operator-commands.md`'s "Exit codes" table was not amended by this RC.
  It did not need to be — the table always described the contract, and the code
  now honours it — but a reader looking for an explicit statement that
  `--details` shares the table will not find one. Not filed; noted only.
- The deep-verify record is three days old and four minor versions behind. That
  is context for the reader, not a finding, and per Step 5d it does not move the
  rating.
