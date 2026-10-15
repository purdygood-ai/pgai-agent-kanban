# Verification Report: ai_rc/v2.23.1

**Task:** TESTER-20260915-054-verify-2-23-1
**RC branch:** `ai_rc/v2.23.1` @ `c99b620`
**Base:** `ai_main`
**Requirements doc:** `projects/pgai-agent-kanban/requirements/v2.23.1-bugfix-bundle-20260915.md`
**Worktree:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260915-054-verify-2-23-1` (detached HEAD at `c99b620`, clean, no stashes)
**Verified:** 2026-09-15

## Summary

v2.23.1 is a six-item bug bundle (BUG-0283, BUG-0285, BUG-0286, BUG-0287,
BUG-0288, BUG-0289). Every bundled item was traced into the diff and exercised
against its own `## Acceptance` section. The four behavioural fixes were
re-verified by hand in throwaway sandboxes — not only the new normal case but
the original failure mode and the decoy/negative cases each fix must not
weaken. Both gated runners exit 0: 4612 passed / 31 skipped (unit), 510 passed
/ 1 skipped (integration). No test failures of any category — no bugs, no
stale assertions, no pre-existing failures.

Two things are not clean, and neither is in the code the bundle set out to fix.
First, the BUG-0286 fix changed Lane A's shipped behaviour from "assume step
(a) ran" to "decline until step (a) ran" and introduced a new operator-run verb
to perform step (a) — but `team/roles/OVERWATCH.md` still tells its reader the
drill "assumes (a) already ran", and `docs/operator-commands.md`, which calls
itself "the command surface in one place", never names the verb. That is a
documentation-truth gap against behaviour this RC shipped, and it is the same
shape as BUG-0287, which this very RC is fixing. Second, the release that swept
eleven provenance citations clean introduced seven new ones, six of them in
files its own tickets created or modified; the `provenance` hygiene class is
red again at the moment of ship. That second one is quality-class and
non-blocking by design, but it has now happened on nine consecutive releases,
so the loop meant to heal it is not converging.

Both are filed. Nothing found here makes the release unusable: the RC's four
functional fixes work, the suites are green, and the refusal introduced by
BUG-0286 carries a runnable remedy that recovers an operator who meets it.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

low

## Suite Results

Authoritative gated-runner exit codes (the commands an operator or CM would run):

- `bash team/scripts/run-unit-tests.sh --coverage` — **exit 0** — 4612 passed, 31 skipped, 788.99s
- `bash team/scripts/run-integration-tests.sh --coverage` — **exit 0** — 510 passed, 1 skipped, 568.76s

Both lint pre-flights passed; the quality-class lints ran and their findings are
in "Lint Findings (non-blocking)" below. No failing tests to categorize.

Static checks on the RC's changed files: `bash -n` clean on
`overwatch-attach-fix-rewrite.sh`, `run-migrations.sh`, `upgrade.sh`,
`cleanup/cleanup.sh`, `cm/release.sh`, `lib/verdict.sh`; `python3 -m py_compile`
clean on `fix_rewrite.py`, `resolution_stage.py`, `scan_attention.py`.

## Code Coverage

Informational only; does not enter the PASS precondition.

- Unit:        line 78%  |  branch 64%   (16410 stmts, 4928 branches)
- Integration: line 10%  |  branch  8%   (16401 stmts, 4928 branches)

Read from `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/unit.json` and
`integration.json`; field names confirmed against the generated files
(`covered_lines`/`num_statements`, `covered_branches`/`num_branches`). The
blended `^TOTAL` figures those runs print are 74% and 9% respectively — the
blended number is not the line number, which is why both are stated separately.

## Findings

### Finding 1: BUG-0283 — cleanup.sh export-reconciliation accepts a trailing slash, and its message now describes the test it performs

**Category:** pass

The fix takes options 1 and 3 from the bug's `## Expected` together: both
comparisons strip a single trailing slash (`${PGAI_PROJECT_ROOT%/}`,
`${PGAI_TASKS_DIR%/}`) and both messages now read "does not match the
reconciled root" instead of "does not sit underneath". The variables are
normalised after the check so the rest of the script sees one spelling.

Verified behaviourally in a throwaway root under
`$PGAI_AGENT_KANBAN_TEMP_DIR` (never the live install), all four cases:

| Case | Result |
|---|---|
| `PGAI_TASKS_DIR` = reconciled tasks dir **with trailing slash** | exit 0, Step 2 header names the sandbox — the bug's exact repro, now passing |
| `PGAI_PROJECT_ROOT` = reconciled project root **with trailing slash** | exit 0, same |
| Decoy `PGAI_TASKS_DIR` pointing outside the sandbox | exit 1, `cleanup.tasks_dir_export_mismatch`, remedy quotes the correct value — BUG-0281's guard intact |
| `PGAI_TASKS_DIR` naming a **sub-path** of the reconciled dir | exit 1 — containment deliberately still not implemented, per the bug's own note that option 2 is a wider change |

Regression coverage was added rather than promised: `test_cleanup_root_override.py`
gains four scenarios (`..._with_trailing_slash_matches_reconciled_directory` and
`..._naming_a_subdirectory_still_refuses`, for both variables), and the BUG-0281
decoy scenarios are unmodified and still pass.

### Finding 2: BUG-0285 — the single provenance instance it named is clean

**Category:** pass

`team/tests/unit/test_worktree_copy_git_isolation.py:101` carries no
`BUG-0284` citation. The file is byte-identical between `ai_main` and the RC,
so the site was already clean before this RC opened — the bug was fixed
upstream and rides this bundle only to have its status closed.

### Finding 3: BUG-0286 — Lane A's Fix-rewrite step is wired, via the precondition design the bug's acceptance allows

**Category:** pass

The bug's `## Acceptance` offered two designs. The RC takes the second: the
deterministic drill never performs the judgment call itself, it *declines*
steps (b)-(e) until an attributed rewrite is already on the blamed bug.

- `_run_lane_a()` now resolves the blamed bug file and calls
  `has_attributed_rewrite()`; on a miss it writes an OVERWATCH Decision event,
  logs, and returns `DECLINED` with the dual-channel text built by
  `_format_lane_a_rewrite_missing()` (`overwatch.lane-a-rewrite-missing`).
  No ledger entry is written, so the decline does not consume the
  single-attempt guard — a correct and non-obvious detail.
- The refusal carries an inline `BLOCK-WORTHY` class comment, a
  `# CENSUS-ANCHOR: lane-a-rewrite-precondition-01` marker, and a matching
  `CENSUS-LOCATOR-INDEX` entry with the same class. The census lint reported
  no findings this run.
- The shipped non-test call site is real: `fix_rewrite.py` gains a CLI and
  `team/scripts/overwatch-attach-fix-rewrite.sh` wraps it through `pp_run_ops`.
  Exercised end-to-end in a sandbox: the append lands with the `FORBIDDEN`
  wording, the original `## Fix` preserved, a dated backup written; an
  unresolvable `--bug-id` exits 1 with a message naming the directory
  searched; omitting `--ground-truth-ref` exits 1 on the fail-loud
  precondition.
- The acceptance's test constraint is honoured. `TestLaneARewritePrecondition`
  supplies no rewrite and asserts the whole negative surface: RC branch still
  present, `Active RC` untouched, bundle doc present, HALT still set, bug file
  byte-identical, no Lane A ledger entry, Decision event still written. Run
  directly: 1 passed. `TestLaneAFullPlaybookWithFixRewriteAndRedecompose`
  performs step (a) by invoking the real wrapper script as a subprocess — not
  by calling `rewrite_bug_fix_section()` from the test body, which is exactly
  what the bug forbade — then drives (b)-(e) through `run_resolution_stage()`
  alone and (f) through a real PM wake.

The unit-tier fixture in `test_overwatch_lane_classification.py` writes the
rewrite block directly rather than through the CLI; its docstring says so and
names the integration test that covers the real call path. That is a
reasonable tier split, not a fidelity dodge.

### Finding 4: BUG-0287 — the migration runner is wired into the upgrade path and the README no longer contradicts it

**Category:** pass

- `team/scripts/migrations/run-migrations.sh` is new: destination-keyed
  selection over `(--from, --to]`, ascending, reusing `semver.sh`,
  `argparse.sh` and `operator_args.sh` rather than re-implementing them.
- `upgrade.sh` phase 2 captures the pre-stamp VERSION, reads the post-stamp
  VERSION, and invokes the runner between them. The three pass-over cases
  (nothing selected, `X == X`, non-semver stamped identity) are handled
  explicitly and narrated.
- Verified behaviourally: `(v2.22.5, v2.23.0]` selects the one migration;
  `(v2.23.0, v2.23.1]` selects nothing and exits 0; `--from 2.22` and a
  missing `--to` both refuse with a usage error and exit 1. In a sandbox
  containing two synthetic migrations where the first fails, the runner
  reports the failing script, exits 2, and **does not run the second** — the
  abort-on-first-failure contract holds.
- Doc acceptance met: `grep` for "no migrations exist", "there are no
  scripts", "not implemented in this release", "Auto-runner — deferred"
  returns nothing in `team/scripts/migrations/README.md`. The README now
  documents the runner, its exit codes, and the by-hand procedure.
  `docs/OPERATIONS.md` names the runnable command for seeding an install
  without upgrading it.
- Test acceptance met: `test_migration_runner_upgrade_wiring.py` (new, 462
  lines) covers first-application seeding, byte-identical second application,
  operator-set-value preservation, the selection rule, fan-out scope, usage
  errors, and four cases driving `upgrade.sh --phase2` itself against a
  sandboxed root.

### Finding 5: BUG-0288 — the Lane B remedy now states the rule the check implements

**Category:** pass

The remedy is reworded to "whose declaration text is byte-for-byte identical
to `--reason` — not merely containing it as a substring alongside other
words". The check (`_cm_release_lane_b_intent_verified`) is unchanged, which
is correct: the bug offered either half and relaxing an approval-by-design
invariant is the wider change.

The new `test_cm_release_lane_b_intent_remedy_agreement.py` is the strongest
test in this RC. It extracts both the check function and the remedy string
**verbatim from the live `release.sh` source** and exercises them together in
one test: the remedy must state the exact rule, a containment-only entry must
still be refused, and a byte-equal entry must be admitted. Editing either half
alone fails the suite, which is precisely the drift the bug described.

`docs/OPERATIONS.md` now separates the classifier's identifier-containment
test from the release engine's whole-string equality test and explains why
they never disagree on the automated path — the "should be stated so an
operator can tell them apart" note in the bug's Files Involved.

### Finding 6: BUG-0289 — all ten provenance instances it named are clean

**Category:** pass

`verdict.sh:284,409`, `scan_attention.py:830,1018`, `fix_rewrite.py:53`,
`resolution_stage.py:471,473,649`, `test_overwatch_lane_a_drill.py:81`,
`test_overwatch_lane_b_ship.py:10` — every one reworded to describe behaviour.
`release-notes/v2.23.0.md` also drops the task-ID attribution in its Known
Issues line. The lint reports none of the ten. (Seven *new* instances appeared
elsewhere in the same RC — see Finding 7 and Lint Findings.)

### Finding 7: Lane A's new decline behaviour is absent from OVERWATCH.md and operator-commands.md

**Category:** gap
**Fix Effort:** small (1-2 CODER/WRITER tasks)
**Systemic Risk:** low (documentation truth on an opt-in autonomous path; no runtime behaviour is wrong)
**Regression:** yes (this RC changed the behaviour and left both documents describing the old one)
**Integrity Damage:** no
**Filed As:** BUG-0290-lane-a-rewrite-docs-untruth (Path C-A — filed as BUG: code defect / regression / test failure)

`git diff --stat ai_main..ai_rc/v2.23.1 -- team/roles/OVERWATCH.md` is empty.
Line 45 still reads, of steps (b)-(e): "it assumes (a) already ran against the
same bug." After this RC the drill does not assume — it checks and declines.
The role file never mentions the precondition, the error key, or the verb that
clears it, so the agent whose own role file this is cannot learn from it that a
Lane A block will now sit HALTed until step (a) is attached out-of-band.

`grep -n "overwatch-attach-fix-rewrite" docs/operator-commands.md` returns
nothing. That document opens by declaring itself "the command surface in one
place… the page a stranger uses to operate the system". Tree-wide, the verb is
named only in code comments, `docs/blocking-site-census.md`, the refusal's own
remedy string, and two test files.

This is the same shape as BUG-0287 — a script something now depends on that no
operator-facing document names — reintroduced in the RC that fixes BUG-0287.

Bounded consequence: the refusal's remedy is genuinely runnable and substitutes
`--project`, `--kanban-root` and `--bug-id` for the caller, so an operator who
*reaches* the refusal recovers from it. What is lost is upstream discovery — a
Lane A halt can sit with nobody aware an action is owed. Rated `low` systemic
risk on that basis, and because `overwatch_resolve` defaults to `false`, so
most installs never reach Lane A at all.

### Finding 8: the migration runner's failure path and the upgrade's abort path have no suite coverage

**Category:** pass-with-caveat

`run-migrations.sh`'s exit-2 abort and `upgrade.sh`'s "migration runner failed"
branch are both classified BLOCK-WORTHY in prose and neither is exercised by
any test — `grep -rn "migration runner failed" team/tests/` returns nothing,
and no test asserts a runner return code of 2. BUG-0287's acceptance asked only
for the apply-and-no-op path, which is covered thoroughly, so this is not a gap
against the criterion.

It is recorded as a caveat rather than a finding because TESTER exercised both
behaviours by hand this run: a sandbox with a deliberately failing migration
produced the named error, exit 2, and correctly skipped the later script. The
mechanism works; only the regression coverage for it is missing. Worth folding
into a future test-hardening pass, not worth a bug on its own.

### Finding 9: pre-flight, structural and constraint checks

**Category:** pass

- Worktree HEAD `c99b620` equals `ai_rc/v2.23.1`; `git status --short` empty;
  `git stash list` empty.
- Pre-flight merge verification: every DONE task's feature branch is either
  already deleted post-merge (the normal shape) or an ancestor of the RC with
  unique commits. Zero anomalies; no BUG-92 pattern.
- `release-state.md`: `Active RC` = `v2.23.1` matches the branch under test;
  `RC Opened By Task` = `CM-20260915-053-open-rc-2-23-1`, which exists and is
  DONE; `## Last Released` = `v2.23.0`, well-formed. `pp_last_released_version`
  independently returns `v2.23.0`, and `v2.23.1 > v2.23.0` by semver. No drift.
- Prerequisite graph for the cycle is acyclic, every referenced task ID exists,
  and the CM-open bookend is a prerequisite of all six work tickets.
- Constraint negatives: no hardcoded secret-shaped assignment anywhere in the
  diff; no bare `/tmp/` literal added; `set -euo pipefail` present in both new
  scripts.
- Skip-cites-real-bug gate: `lint_skip_bug_gate.sh` exits 0. The four
  `BUG-123x` IDs a raw grep surfaces are synthetic fixture literals inside
  `test_skip_bug_citation_scan.py`, the lint's own test — confirmed by reading
  the file, not assumed.
- Coding-standards spot-check: both new scripts ship a `--help` naming every
  flag; both reuse `lib/` primitives rather than duplicating them; no new
  hardcoded operational value. The one directive-8 miss is the provenance
  class below. (Cosmetic: `overwatch-attach-fix-rewrite.sh --help` renders its
  usage line as `usage: fix_rewrite.py` because it delegates straight to
  argparse. Noted, not filed.)
- Synthetic release simulation (Step 6.8, triggered because `cm/release.sh` is
  in the diff): `test_cm_release_synthetic.py` is present with 13 tests and
  passed inside the green integration run.

## Lint Findings (non-blocking)

Both gated runners wrote identical findings files, 7 findings each, all one
class. Grouped by lint class per the one-bug-per-class rule:

- `hygiene` / `provenance` (7 instances, severity `quality`) — comments and
  docstrings added by this RC cite a bug id or task id instead of describing
  behaviour — **filed BUG-0291-hygiene-lint-provenance (Path C-A)**.
  Instances, `<path>:<line>` — token:
  - `team/scripts/overwatch-attach-fix-rewrite.sh:26` — `BUG-0163`
  - `team/tests/integration/test_overwatch_scaffolding_gates_resolution.py:244` — `CODER-20260915-047`
  - `team/tests/unit/test_cleanup_root_override.py:50` — `BUG-0283`
  - `team/tests/unit/test_cleanup_root_override.py:417` — `BUG-0283`
  - `team/tests/unit/test_cleanup_root_override.py:425` — `BUG-0283`
  - `team/tests/unit/test_cleanup_root_override.py:444` — `BUG-0283`
  - `team/tests/unit/test_overwatch_lane_classification.py:222` — `CODER-20260915-047`

Why a new bug rather than a citation of an open one: BUG-0285 and BUG-0289 are
`running`, which is why the runners' own aggregate filer suppressed its filing.
Both are bundled into this RC, so `cm-release.sh` Step 16b flips them to `done`
at ship — citing them would leave these seven sites tracked by nothing the
moment the release lands.

Per `roles/TESTER.md` Step 5c these findings do not move `## Recommendation` or
`## Systemic Risk` on their own. The recommendation here is driven by Finding 7,
not by this section.

A structural observation about the class, filed separately as
**PRIORITY-0004-provenance-sweep-convergence (Path C-B — filed as PRIORITY:
enhancement / design change)**: this is the ninth consecutive release in which
the class is swept clean and re-broken by the same RC's own diff
(BUG-0236, 0245, 0260, 0261, 0267, 0274, 0278, 0285, 0289, now 0291). The
sweep ticket is scoped to the list of lines the previous verification named,
so sites created by sibling tickets in the same RC are out of its scope by
construction — including, this time, sites in a file created by a ticket the
sweep declared as its own prerequisite. The loop that is supposed to heal this
class is not converging, and that is a process question rather than a defect in
any one line.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=8d freshness=stale — the docker and podman claude-abliterated pairs failed on a Dockerfile `FROM` parse error and podman/rocky9 on a `COPY --chmod` step; cites BUG-0167-cold-build-claude-abliterated-failure, filed by that deep-verify run and since closed (`## Status: done`), so the recorded red predates its own fix; stale on both counts (8d exceeds the 7-day window, and v2.9.20 is not the v2.23.1 under verification), so nothing is known about whether this tree's images build.

Per `roles/TESTER.md` Step 5d this line is reported, not re-derived: no
container build was run during RC verification, the red record is cited rather
than re-filed, and neither the verdict nor its age moves `## Recommendation` or
`## Systemic Risk`.

## Autonomous criterion

Manual interventions required: none.

1. Any task force-promoted from WAITING to BACKLOG by hand? **NO.** All six
   work tasks and the CM-open task are DONE with `Needs Human: no`; the wake
   log records this TESTER task's own promotion as the wake script's, and no
   status.md carries an out-of-band edit.
2. Was `release-state.md` edited outside the CM scripts? **NO.** All four
   fields are internally consistent and `RC Opened By Task` names a real DONE
   CM task.
3. Were scripts patched mid-build? **NO.** Every commit on the RC belongs to a
   named feature-branch merge for one of the six tickets; the RC empty-commit
   lint confirms no empty non-merge commits across `ai_main..ai_rc/v2.23.1`.
4. Were queue files manually edited? **NO.** The tester queue shows this task
   at `[A]` written by the wake script; no hand-flipped markers found.

## Caveats

- `bash team/scripts/run-migrations.sh --from … --to …` with no `--project`
  resolves every registered project from the ambient kanban root. TESTER's own
  selection probe was run with `--dry-run` for that reason. Anyone exercising
  this verb for real outside an upgrade should pass `--project` or point
  `PGAI_AGENT_KANBAN_ROOT_PATH` at a throwaway root first. This is documented
  behaviour, not a defect — noted because the failure mode is quiet.
- Two prior runs' stale `unit_exit.txt` / `integration_exit.txt` files sat in
  the shared tester temp directory and briefly read as this run's results. The
  exit codes reported above were taken only after confirming both runner
  processes had actually exited; the shared temp path is a re-use hazard for
  any agent reading those filenames without checking mtimes.
- No `.previous-RUN-N` artifacts existed in this task's `artifacts/` directory
  at start: this is a first run, verified fresh from the RC with no prior
  report consulted.
- Integration-suite coverage reads 10% line / 8% branch. That is the expected
  shape for a suite that drives shipped scripts as subprocesses — the child
  process's execution is not instrumented by the parent's coverage run. It is
  reported as an observation, never as a finding.

## Artifacts

- `artifacts/report.md` — this file
- `artifacts/gaps.md` — the two gaps, with criterion / expected / actual / severity
- `projects/pgai-agent-kanban/bugs/BUG-0290-lane-a-rewrite-docs-untruth.md` (Path C-A)
- `projects/pgai-agent-kanban/bugs/BUG-0291-hygiene-lint-provenance.md` (Path C-A)
- `projects/pgai-agent-kanban/priority/PRIORITY-0004-provenance-sweep-convergence.md` (Path C-B)
