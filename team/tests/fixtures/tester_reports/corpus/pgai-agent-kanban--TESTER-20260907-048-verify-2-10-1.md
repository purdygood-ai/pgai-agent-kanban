# Verification Report: ai_rc/v2.10.1

**RC commit:** `0442094aae46c5e883596bba2b4f05044e126855`
**Requirements doc:** `projects/pgai-agent-kanban/requirements/v2.10.1-bugfix-bundle-20260907.md`
**Worktree:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260907-048-verify-2-10-1` (detached HEAD at the RC commit, clean, no stashes)
**Verified:** 2026-09-07

## Summary

v2.10.1 is a three-item bug bundle: BUG-0174 (the TESTER and CM governance
reading stacks were over their configured byte budgets, which made the unit
suite red), BUG-0175 (four documents disagreed about whether
`release-state.md` carries a `## Last Released` field), and BUG-0177 (a gated
runner whose pytest phase failed never rewrote its quality-class findings
file, so a verifier could read a different worktree's results as if they
described the RC under test). All three are fixed, and each was verified
behaviorally rather than by reading the diff: the byte-budget lint now prints
`under` for all seven roles and the composer fixture passes; the shipped
Step 6.1 procedure run against this project's live `release-state.md`
produces no drift finding and no document in the tree still carries the old
three-field claim; and the findings-file fix was exercised by seeding a stale
file, forcing a failing pytest phase, and confirming the file was replaced --
then re-run against the pre-fix `ai_main` runners, where the same assertions
fail, proving the new test has teeth. Both gated runners exit 0 (unit 3225
passed / 3 skipped; integration 337 passed / 1 skipped), every bundle
acceptance criterion holds, and no manual intervention shows anywhere in the
build. Two defects were found outside the bundle's own scope, both created or
widened by this RC's work: the three documents this release relocated
governance content into are never installed to a kanban root, so the role
files' pointers to them dangle for any agent not working inside a checkout of
this repository; and the new regression test names the bug ticket that
motivated it throughout its docstrings, which the project's own test-authoring
rule forbids without exception. Both are filed; neither prevents the release
from being used.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

medium

## Findings

### Finding 1: BUG-0174 — every role's governance stack is now under budget

**Category:** pass

`bash team/scripts/lint_governance_byte_budgets.sh --kanban-cfg kanban.cfg_example`
exits 0 and prints `under` for all seven roles, with no `OVER` row:

```
ROLE             ASSEMBLED         BUDGET   VERDICT
po                   55722          57344   under
pm                   65535          65536   under
coder                61013          61440   under
writer               60763          61440   under
tester               92002          92160   under
cm                   71531          71680   under
overwatch            66546          66560   under
```

`team/roles/TESTER.md` is 50247 bytes (ticket target 50405 or less, was 70836);
`team/roles/CM.md` is 29776 (target 29925 or less, was 40785). The two roles that
were `OVER` on `ai_rc/v2.10.0` are the two that moved. The bug's central
acceptance criterion — `run-unit-tests.sh` exits 0 with
`test_every_role_governance_assembly_stays_under_configured_budget` passing and
no assertion weakened — holds: the unit runner exits 0 and
`git diff ai_main..ai_rc/v2.10.1 -- team/tests/unit/test_composer_governance_byte_budget.py kanban.cfg_example`
is empty, so neither the measurement nor the budget was touched.

Content review of both trimmed role files confirms no procedure was lost.
`team/roles/TESTER.md` still carries Steps 1 through 11 as its own headings,
the Step 9 report template, all three deep-verify line forms, the Path C
decision rule and filename shapes, and the state vocabulary.
`team/roles/CM.md` still carries the ship-policy matrix in full, the five
operations, the HALT authority section, the approval gate, release notes and
hooks. Every relocated block is named by path from the role file at the point
it used to sit (`docs/cm-release-reference.md` at nine sites in CM.md;
`docs/tester-verification-reference.md` and `docs/test-authoring-guidelines.md`
at nine sites in TESTER.md). See Finding 6 for the one way that
naming-by-path falls short.

### Finding 2: BUG-0175 — one release-state.md schema, stated the same everywhere

**Category:** pass

Every document in the tree that enumerates the schema now names the same four
fields (`## Active RC`, `## RC Opened At`, `## RC Opened By Task`,
`## Last Released`): `team/SOP-core.md:215`, `team/roles/TESTER.md:162`,
`team/roles/PM.md:21`, `team/roles/PO.md:35`, `team/roles/CM.md:54`,
`docs/OPERATIONS.md:319`, and `team/pm-agent/VERIFICATION-METHODOLOGY.md:57`.
The bug named the first three; the fix reached two more (`PO.md` and
`VERIFICATION-METHODOLOGY.md`) that carried the same wrong claim, which is the
right scope for an acceptance criterion reading "every document that states
the schema."

The bug's acceptance criteria, run:

- ``grep -rn 'no `Last Released`' team/ docs/`` — no match (exit 1).
- The canonical statement landed in `team/SOP-core.md`, the one file all seven
  read-order manifests name, at net-zero bytes (25091 before and after) — so
  the correction is reachable by TESTER and PM, which was the whole failure
  mode, and no role's budget moved.
- Walking the shipped Step 6.1 against this project's live `release-state.md`
  produces no drift finding: `pp_last_released_version pgai-agent-kanban`
  returns `v2.10.0`, `semver_gt v2.10.1 v2.10.0` succeeds, and the file's
  `## Last Released` value `v2.10.0` is a well-formed `vX.Y.Z`. The old check,
  whose passing condition was the field's absence, is gone.

### Finding 3: BUG-0177 — the findings file now describes the run that wrote it

**Category:** pass

The fix registers the findings-file write as a `CLEANUP_ON_EXIT_PRE_HOOK` on
each runner's own EXIT trap, set immediately after the last quality-class lint.
`trap cleanup_on_exit EXIT` is installed at `run-unit-tests.sh:79` and
`trap pytest_cleanup_on_exit EXIT` at `run-integration-tests.sh:110`, both
before the hook is registered, so the hook genuinely fires on every exit path
from that point forward. This was the bug report's own ranked-cheapest option.

Behavioral verification (Step 6.5), not structural:

1. **The new normal case.** `python3 -m pytest tests/unit/test_gated_runner_findings_on_failure.py`
   against the RC tree — 4 passed. The failure-path test seeds a
   `findings-*.json` bearing `generated_at: 2020-01-01T00:00:00+00:00`, forces
   a deliberate pytest failure in a throwaway fixture repo running the real
   runner script as a bash subprocess, and asserts the file was replaced, the
   stale finding is gone, and the `findings file written:` narration line
   printed.
2. **The original failure mode.** I rebuilt the same fixture with `ai_main`'s
   pre-fix `run-unit-tests.sh`, `run-integration-tests.sh` and
   `script_common.sh` copied in, and re-ran the identical test file:
   `2 failed, 2 passed` — both failure-path parametrisations fail with
   `assert '2020-01-01T00:00:00+00:00' != '2020-01-01T00:00:00+00:00'`, i.e.
   the seeded stale file survives untouched, exactly as BUG-0177 describes.
   The success-path tests still pass there, confirming the test discriminates
   the fix rather than the fixture.
3. **In production shape.** My own two runs of this verification wrote current
   findings files: `findings-unit.json` `generated_at 2026-09-07T18:11:19+00:00`
   with four findings, `findings-integration.json` `2026-09-07T18:15:18+00:00`
   with one, both naming this worktree's paths. Before the fix, the file on
   disk at the start of this run was from 17:54 and belonged to another task.

Exit statuses are unchanged, which the tests assert directly (`returncode == 1`
on failure, `0` on success), and both runners still pass `bash -n`.

### Finding 4: bundle acceptance criteria

**Category:** pass

- *Each bundled item is addressed in the implementation* — Findings 1, 2 and 3.
- *All Python files compile cleanly* — `python3 -m compileall -q .` exits 0
  with no output.
- *All bash files pass syntax check* — `bash -n` over all 237 `*.sh` files
  outside `.git/`: 0 failures.

### Finding 5: gated test suites

**Category:** pass

Authoritative exit codes, from the gated runners (not direct pytest):

- `bash team/scripts/run-unit-tests.sh --coverage` — **UNIT_EXIT = 0**;
  3225 passed, 3 skipped in 407.53s.
- `bash team/scripts/run-integration-tests.sh --coverage` — **INTEGRATION_EXIT = 0**;
  337 passed, 1 skipped in 216.17s.

The integration suite includes `test_cm_release_synthetic.py`, which is the
Step 6.8 synthetic release simulation. That step's trigger condition fires
here — this RC modifies `team/scripts/lib/script_common.sh`, whose
`cleanup_on_exit` is sourced by 20+ scripts including `ship-rc.sh` and
`close.sh` — so the change to the shared trap is exercised against a fresh
isolated temp repo, and passes. The new `CLEANUP_ON_EXIT_PRE_HOOK` is read as
`${CLEANUP_ON_EXIT_PRE_HOOK:-}` and is never exported by either runner, so no
sourcing script inherits a hook it did not set.

### Finding 6: relocated governance documents are not installed to a kanban root

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (the relocation pattern will be reused, and nothing in the tree checks the property)
**Regression:** yes (this RC moved required governance out of an installed file into a file the installer does not copy)
**Integrity Damage:** no
**Filed As:** BUG-0178 (Path C — autonomous follow-up filing, non-blocking)

This RC moved roughly 21 KB out of `team/roles/TESTER.md` and 11 KB out of
`team/roles/CM.md` into `docs/tester-verification-reference.md`,
`docs/test-authoring-guidelines.md` and `docs/cm-release-reference.md`. None of
the three is reachable from an installed kanban root. `install.sh:714` copies
exactly `README.md DIRECTIVES.md OVERVIEW.md SOP-core.md roles scripts pm-agent
workflows pgai_agent_kanban halt_after templates demos config read-order` out
of `team/`; `docs/` is a repo-root directory and `grep -n docs install.sh`
returns nothing. The live install confirms it: `ls -d
$HOME/pgai_agent_kanban/docs` -> *No such file or directory*.

The consequence is that an agent reading the installed governance stack meets
"the full text is in `docs/tester-verification-reference.md`" and cannot open
that path — unless it happens to be working inside a worktree of this
repository, which is true for the kanban self-build lane and false for every
other registered project. The relocated material includes the full text of the
Anti-Patterns list, which `team/roles/TESTER.md` itself declares a PROTECTED
SURFACE, and the Path C bug and priority document shapes TESTER is required to
reproduce.

`WRITER-20260907-044`'s README consciously overrode BUG-0174's last acceptance
bullet ("anything removed from a role file lands in a document that some
role's read-order manifest names") for a sound reason — no role has 10-20 KB of
budget headroom — and substituted "a named pointer from the role file is what
keeps the content reachable." It is that substitute that does not hold, and it
fails on the installer rather than on the manifest, a consideration the ticket
never had in view. Recorded as a `gap` because the bug's own acceptance
criterion is only partially met, not as a criticism of the decomposition's
reasoning.

The class is pre-existing — `team/roles/TESTER.md` already cited
`docs/coding-standards.md` and `team/roles/CM.md` already cited
`docs/OPERATIONS.md`, `docs/operator-commands.md` and
`docs/promotion-playbook.md`, all equally uninstalled — but this release is
what raised the stakes from supplementary reading to relocated governance.
`Regression: yes` reflects that widening.

### Finding 7: the new regression test names the bug ticket that motivated it

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated to one test file's prose; no behavior depends on it)
**Regression:** yes (the file was added by this RC, commit `1231aac`)
**Integrity Damage:** no
**Filed As:** BUG-0179 (Path C)

`team/tests/unit/test_gated_runner_findings_on_failure.py` cites `BUG-0177` and
the authoring task's own process history at eight sites in its docstrings:
lines 4, 10, 12, 18, 20, 198, 226 and 230 (`Regression guard for BUG-0177`,
`THE BUG`, `Before this task's fix`, `See bugs/BUG-0177-...md`, `THE FIX`,
`shape BUG-0177 describes`, `BUG-0177 regression guard`, `before this task's
fix`). `docs/test-authoring-guidelines.md`, Anti-pattern 6, states the rule and
its only exception in the same tree: *"When acceptable: never — in names,
docstrings, OR comments... The single exception is a skip annotation citing an
OPEN follow-up bug."* None of the eight is a skip annotation, and
`team/roles/TESTER.md` Step 9 binds the same boundary.

Nothing catches it mechanically:
`team/scripts/lint_comment_provenance.py` declares in its own Scope section
that it excludes test files, which is why the RC author's provenance cleanup
pass (commit `974fd11`) fixed the production comments the lint flags and left
this file untouched — they were working from the lint, and the lint is silent
here.

BUG-0172 (`## Status: open`) records the first instance of this class in a
different file, but its acceptance criterion greps only
`test_project_paths_bash.py`, so closing it will not clean this file. Filed
separately for that reason rather than cited as a duplicate.

### Finding 8: pre-flight, working tree, and state drift

**Category:** pass

- **Pre-flight merge verification** — every DONE task's feature branch is
  already deleted (normal post-merge cleanup by CODER and WRITER), so Checks 1
  and 2 are trivially satisfied; the scan reported no anomaly. The RC's history
  shows all four feature merges as `--no-ff` merge commits (`b3cd3c1`,
  `0289b2c`, `c0a5e29`, `0442094`), plus `CODER-20260907-046`'s deliberate
  no-op (a verification-only closing ticket, the same shape as the
  `CODER-20260907-037` precedent).
- **Working tree** — `git rev-parse HEAD` equals `git rev-parse ai_rc/v2.10.1`;
  `git status --short` empty before and after the verification;
  `git stash list` empty.
- **Step 6.1 drift** — `Active RC: v2.10.1` matches an existing
  `ai_rc/v2.10.1` branch; `## Last Released` is a well-formed `v2.10.0`;
  `v2.10.1 > v2.10.0` under `semver_gt`. No drift. The tree's `VERSION` file
  reads `v2.10.0`, which is correct in flight — it is written by the release
  step, one commit per shipped version ("Add committed VERSION for v2.10.0").
- **Step 6.13 skip-cites-real-bug** — no placeholder skip IDs in
  `team/tests/`; the only matches for the placeholder grep are inside archived
  runner logs under `team/tests/artifacts/`, which are the gate's own recorded
  output, not skips. No cited `BUG-NNNN` resolves to a missing file.
- **Step 6.10 prereq graph** — acyclic and correctly bookended:
  `CM-20260907-047-open-rc-2-10-1` (no prerequisites) -> the four work tickets ->
  `CODER-20260907-046` -> `TESTER-20260907-048` -> `CM-20260907-050-release-2-10-1`,
  whose prerequisites include this task. All referenced task IDs exist, and
  sequence numbers 042-050 do not collide.
- **Step 6.9 constraint negatives** — `set -euo pipefail` is preserved at
  `run-unit-tests.sh:74` and `run-integration-tests.sh:90`; the `set -e` /
  `set +e` pairs around pytest are unchanged from `ai_main` except in line
  number. No secrets or banned patterns introduced.

### Finding 9: test fidelity of the RC's new test

**Category:** pass-with-caveat

`test_gated_runner_findings_on_failure.py` scores well against the fidelity
checklist: it drives the real runner as a bash subprocess (real strict-mode
bash, checklist item 1), copies the runner and the whole `scripts/lib/`
byte-for-byte rather than reimplementing them, points
`PGAI_AGENT_KANBAN_ROOT_PATH` at a fresh empty directory so nothing touches the
live install (item 6), and verifies both the failure path and the pre-existing
success path (Anti-pattern 6 in TESTER's own list). I confirmed its liveness
independently rather than trusting the claim — see Finding 3.

The caveat: every lint the runner shells out to is stubbed to `exit 0`, so the
test asserts the findings array is empty. That is the right scope for this
test, but it means no test covers the case the bug actually described — a
failing run that *has* real quality-class findings to record. The stubs make
the write mechanism observable and the content untested. Noted rather than
filed: the mechanism is what BUG-0177 was about, and
`gated_runner_write_findings_file` has its own coverage elsewhere.

### Finding 10: autonomous operation criterion

**Category:** pass

See the Autonomous criterion section below. All four checks are NO.

## Lint Findings (non-blocking)

Ingested from `/tmp/pgai_kanban_tmp/lint/findings-unit.json`
(`generated_at 2026-09-07T18:11:19+00:00`, 4 findings) and
`/tmp/pgai_kanban_tmp/lint/findings-integration.json`
(`generated_at 2026-09-07T18:15:18+00:00`, 1 finding). Both files are this
run's own — which is itself the first downstream confirmation of the BUG-0177
fix. Two distinct lint classes, one bug each.

- `comment_provenance` — one instance, `release-notes/v2.9.22.md:31`: a
  `TESTER-20260907-017` task-ID reference in the `## Known Issues` prose of a
  shipped release note. Reported by both runners. — **cites BUG-0173 (open)**,
  which records this exact instance and path; no duplicate filed.
- `gate_census_compliance` — three instances, all refusal sites in the two
  gated runners missing a census classification comment and (at two of them) a
  dual-channel error-contract call: `team/scripts/run-integration-tests.sh:247`
  (`exit 2`, pytest absent), `team/scripts/run-integration-tests.sh:272`
  (`exit 1`, breakage-class gate — emitter present, class comment missing), and
  `team/scripts/run-unit-tests.sh:570` (`exit 1`, pytest failed). All three
  sites exist unchanged on `ai_main`; this RC moved only their line numbers.
  No open or running bug covers the class. — **filed BUG-0180 (Path C)**.

Neither entry changes `## Recommendation` or `## Systemic Risk`; per Step 5c a
bug filed solely to track a quality-class lint finding is non-blocking. The
recommendation above is driven by Findings 6 and 7, which are Step-7 defect
findings, not by anything in this section.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=0d freshness=stale — the docker/claude-abliterated and podman/claude-abliterated pairs failed on a Dockerfile FROM parse error and podman/rocky9 failed at its entrypoint COPY step; the record is same-day but exercised v2.9.20 rather than the v2.10.1 under verification, so nothing is known about whether this tree's images build; cites BUG-0167, filed by that deep-verify run.

## Code Coverage

Read from the JSON `totals` objects written by both runners (field names
verified against the generated files, not assumed).

- Unit:        line 74%  |  branch 61%   (14136 stmts, 4316 branches; blended `TOTAL` 71%)
- Integration: line 8%   |  branch 6%    (same tree; blended `TOTAL` 7%)

Informational only — coverage does not enter the recommendation or the
systemic-risk rating.

## Autonomous criterion

Manual interventions required: none.

1. **Tasks force-promoted from WAITING to BACKLOG by hand?** NO. The six task
   `status.md` files for this RC carry monotonically increasing mtimes in
   pipeline order (16:21:51 -> 16:52:39 -> 17:13:23 -> 17:32:04 -> 17:43:18 ->
   18:02:01), each consistent with a wake-script promotion after its
   prerequisites completed. This task's own status records "Promoted from
   WAITING to BACKLOG by wake script: prerequisites satisfied."
2. **`release-state.md` edited outside the CM scripts?** NO. Its mtime is
   `2026-09-07 16:21:17`, matching its own `## RC Opened At` value
   (`2026-09-07T16:21:17+00:00`) to the second — i.e. untouched since
   `cm-open-rc.sh` wrote it.
3. **Scripts patched mid-build?** NO. Every commit touching `team/scripts/` on
   this RC belongs to `CODER-20260907-042`, which is the ticket whose whole
   deliverable is that change. No out-of-band tooling commit appears in
   `git log ai_main..ai_rc/v2.10.1`.
4. **Queue files hand-edited?** NO. `tester_backlog.md` shows this task marked
   `[A]` (active) in normal sequence after six prior verify tasks marked `[x]`;
   no out-of-order or hand-shaped entries.

Two `## Instruction Conflicts` entries were recorded, both by
`WRITER-20260907-044`, and both are the healthy kind: the agent flagged that
its README overrode BUG-0174's manifest bullet, and that the README named a
PROTECTED SURFACE as a relocation candidate, then resolved each in the open
rather than silently. Neither is a manual intervention.

## Caveats

- **The installed governance stack and the RC tree have diverged on the
  per-finding field set.** The installed `roles/CM.md` (v2.9-era) ship-policy
  matrix reads `Regression` and `Integrity Damage` off the TESTER report and
  HALTs a `high` finding whose `Regression` value is missing. The RC tree's
  `team/roles/CM.md` and `team/roles/TESTER.md` no longer mention either field
  — that change landed in v2.10.0, not this RC, and the two files are
  internally consistent with each other. This report writes all six fields on
  every defect finding, which satisfies both readers. Recorded as an
  observation, not a finding: it is pre-existing and out of this bundle's
  scope.
- **Findings-file provenance.** The findings files this report ingests are
  demonstrably this run's own. That was not knowable on the previous RC, which
  is precisely what BUG-0177 was about; the fix changes what a verification
  report can honestly claim, not just what a script writes.
- **`docs/blocking-site-census.md` was not reconciled by hand.** I read the
  census lint's findings rather than re-deriving the inventory. Three sites it
  names are unanchored (see Lint Findings, BUG-0180); whether the census
  document is otherwise complete was not audited, and is outside this RC's
  scope.

## Artifacts

- `artifacts/report.md` (this file)
- `artifacts/gaps.md`
- Path C filings: `projects/pgai-agent-kanban/bugs/BUG-0178-docs-references-not-installed.md`,
  `projects/pgai-agent-kanban/bugs/BUG-0179-new-test-docstrings-cite-bug-id.md`,
  `projects/pgai-agent-kanban/bugs/BUG-0180-gated-runner-census-signals-missing.md`
