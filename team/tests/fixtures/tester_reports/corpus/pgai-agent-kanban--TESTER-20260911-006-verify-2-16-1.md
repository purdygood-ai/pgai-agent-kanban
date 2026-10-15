# Verification Report: ai_rc/v2.16.1

## Summary

Verified release candidate `ai_rc/v2.16.1` (HEAD `54b00c7`, worktree
`/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260911-006-verify-2-16-1`,
detached at the RC commit) against
`projects/pgai-agent-kanban/requirements/v2.16.1-bugfix-bundle-20260911.md`.
The RC is a two-item BUG bundle: BUG-0237 (a malformed hygiene allowlist sidecar
killed both gated runners before pytest ran) and BUG-0238 (hygiene-lint remedy
commands persisted into auto-filed bugs named the ephemeral worktree they were
generated in). Six commits across three tasks changed 6 files: 130 lines of
`team/scripts/lint_hygiene.py`, 3 lines of
`team/pgai_agent_kanban/lint/bug_filer.py`, two documentation files, and 234
lines of new unit tests.

Both headline fixes work, and I verified both by triggering the failure modes
rather than by reading the diff. Every one of the six malformed-sidecar shapes
now exits 0 with a `SIDECAR-MALFORMED` finding recorded and no suppression
honoured from the broken file; the bare lint invocation the gated runners make
survives `set -euo pipefail` against a full copy of the RC tree carrying a
blank-justification entry; and a bug filed by the aggregate filer from one
throwaway checkout carries a remedy command that runs correctly from a second,
independent checkout. Both gated runners exit 0 (3805 passed / 3 skipped unit;
442 passed / 1 skipped integration). The documentation ticket's census edits are
accurate and internally consistent, including the rollup arithmetic.

Two defects were found, both narrow and both in the seam where the two fixes
meet. The `hygiene-sidecar` finding class this release introduced is routed into
the aggregate bug filer without being covered by the sibling ticket's
path-relativization, so the bug it writes names a deleted directory and carries
a reproduction command the lint rejects — the same defect BUG-0238 was filed to
kill, reappearing through a path that fix did not cover. A live instance is
already sitting `open` in this project's `bugs/` directory. Separately, one
comment in `lint_hygiene.py` still describes the exit path the release deleted.
Both are filed via Path C-A. Neither prevents the release from being operated,
and both leave v2.16.1 strictly better than v2.16.0, where the same condition
killed verification outright.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

medium

## Findings

### Finding 1: malformed-sidecar abort converted to a recorded finding (BUG-0237)

**Category:** pass

Verified behaviorally, not structurally. Six malformed-sidecar shapes were
exercised against the RC's own `lint_hygiene.py`: blank `justification`,
invalid JSON, non-list top level, non-object entry, missing `content_hash`, and
an unreadable file (`chmod 000`). Every one produced **exit 0**, a
`SIDECAR-MALFORMED` finding in the findings file naming the sidecar, the
offending entry index, and the reason it was refused, **and** left a real
provenance finding reported that the sidecar's own valid entry would have
suppressed. That is all three behaviors the ticket's constraints required.

The production-shape reproduction was run too, because the defect was an
`errexit` chain-kill and only strict-mode bash surfaces that class. A full copy
of the RC tree was made under `$PGAI_AGENT_KANBAN_TEMP_DIR` (the live worktree
was not mutated), one blank-justification entry was appended to its real
`team/scripts/hygiene-lint-allowlist.json`, and the gated runners' exact
invocation was run under `set -euo pipefail` from the copy's `team/` directory:

```
before lint
SURVIVED errexit: lint exit=0
```

9 findings were written to the findings file, one of them the
`SIDECAR-MALFORMED` record; all 7 of the tree's live suppressions were dropped
for that run, which is the "no suppression from the broken file is honoured"
requirement demonstrated against the real allowlist rather than a fixture.

Exit 2 was confirmed to still mean only "pytest unavailable" from a runner's
point of view: the lint's remaining `return 2` sites are CLI-argument validation
(`--classes`, `--add-suppression`) that the runners' fixed invocation cannot
reach.

`python3 team/scripts/lint_gate_census_compliance.py` exits 0 with zero
findings — the anchor marker and its Locator Index line were removed together,
which is what the two-way reconciliation requires.

### Finding 2: remedy commands survive the tree that generated them (BUG-0238)

**Category:** pass

Reproduced the report's own five-step scenario in production geometry: a
throwaway checkout `P` holding `team/scripts/lint_hygiene.py` at its real
repo-relative location, scanned with `--repo-root .`, findings wrapped into the
gated-runner schema and run through `pgai_agent_kanban.lint.bug_filer`.

`grep -c "$P"` against the filed `BUG-*-hygiene-lint-provenance.md` returns
**0**. The persisted remedy reads

```
python3 "team/scripts/lint_hygiene.py" --add-suppression "code/dirty.py" 2 provenance "..." --sidecar "team/scripts/hygiene-lint-allowlist.json" --repo-root "."
```

and the `## Remedies` section is now prefixed with the run-from-the-repository-root
instruction. Running a generated `--add-suppression` command end-to-end wrote a
sidecar entry naming the intended file, line, and class.

The interactive path was checked separately, from the dev-tree root: the lint
still prints a usable remedy block whose commands are runnable as printed.

### Finding 3: a `hygiene-sidecar` finding files a bug nobody can act on

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (a real defect an agent hits, but shipping worsens nothing — see the rubric reasoning below)
**Regression:** yes (the failing shape does not predate this RC; the `hygiene-sidecar` finding class, and therefore the bug it files, is new in v2.16.1)
**Integrity Damage:** no
**Filed As:** BUG-0240 (Path C — autonomous follow-up filing, non-blocking)

The new `SIDECAR-MALFORMED` finding carries `class="hygiene-sidecar"` and flows
through the gated runners' findings file into the aggregate bug filer
(`run-unit-tests.sh:654`, `run-integration-tests.sh:586`). The bug the filer
writes fails BUG-0238's own acceptance item 1, and fails it through a path that
ticket's fix did not cover.

`_finding_cause()` returns the raw `_SidecarError` message verbatim for this
label, and that message names the *resolved* sidecar path absolutely.
`_finding_line()` in the filer renders `cause` into both `## Actual` and
`## Findings`. Measured against a throwaway checkout `P`:

- `BUG-*-hygiene-lint-provenance.md` → `grep -c "$P"` = **0** (the fix works)
- `BUG-*-hygiene-lint-hygiene-sidecar.md` → `grep -c "$P"` = **2** (it does not)

Second half of the same defect: the filer's `## Reproduction` line is built as
``python3 team/scripts/lint_hygiene.py --classes <cls>``, and `hygiene-sidecar`
is not a registered scan class:

```
$ python3 team/scripts/lint_hygiene.py --classes hygiene-sidecar
lint_hygiene: unknown --classes value(s) ['hygiene-sidecar']; registered classes are ['provenance', 'version-named-test', 'comment-drift']
EXIT=2
```

The one command the bug hands its reader exits 2 and reproduces nothing.

**This is not hypothetical.** `projects/pgai-agent-kanban/bugs/BUG-0239-hygiene-lint-hygiene-sidecar.md`
was auto-filed at 2026-09-11T03:34, during CODER-20260911-002's own gated-runner
reproduction. Its `## Actual` names
`/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/scratch_sidecar_repro/...`,
which no longer exists (`ls` confirms). It is `## Status: open`, so discovery
will bundle it into a future RC and PM will decompose an unactionable ticket for
a sidecar entry that never existed in the shipped tree. That secondary
consequence — an agent's scratch reproduction depositing a bug into the live
pipeline — is recorded inside BUG-0240 rather than split into its own filing,
because it has the same single root cause.

CODER-20260911-003's completion note declares this residual honestly and
correctly scopes it out of its own ticket ("a SIDECAR-MALFORMED finding's
'cause'/'token' fields still embed the sidecar's absolute path via the
pre-existing `_SidecarError` message"). That disclosure is why this is a clean
follow-up rather than a process failure; it does not make the defect go away.

**Rubric reasoning for `medium` rather than `high`.** Before this RC the same
condition killed both gated runners outright and filed nothing at all, so
shipping v2.16.1 does not move the world backwards on this axis — it moves it
forward, imperfectly. Chain integrity is not at risk: nothing is corrupted and
no state can be silently lost; the cost is one unactionable bug in a future
bundle, which the loop closes by closing it. The fix is small. `Regression` is
recorded as `yes` because the observable defect is introduced by this RC's new
code path, and per the TESTER field definition that is the honest value even
though the underlying `_SidecarError` message text predates the release.

### Finding 4: `lint_hygiene.py` still comments an exit path this release deleted

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (documentation that lags the code; no runtime effect)
**Regression:** yes (the comment became false in this RC — the path it names was removed here)
**Integrity Damage:** no
**Filed As:** BUG-0241 (Path C)

The comment above `main()`'s final `return 0`:

```python
    # Quality-class lint: findings are reported, never a reason to fail a
    # build. Always exit 0 here -- the only non-zero exit is the
    # sidecar-malformed path above.
    return 0
```

There is no sidecar-malformed path above it any more. The module docstring's
"Exit codes" section was rewritten correctly in the same commit and now says the
opposite, so the file contradicts itself. `docs/coding-standards.md` and
`docs/blocking-site-census.md` are both correct; this comment is the only site
missed, and CODER-20260911-002's `## Inputs` named it explicitly as a site the
ticket had to reconcile.

The hygiene lint's own `comment-drift` class cannot catch this — that class is
deliberately narrowed to argument-count claims against an adjacent `def`, as its
docstring states.

### Finding 5: documentation ticket (WRITER-20260911-004)

**Category:** pass

Read against the tree rather than against the brief, which is what the ticket
required. CODER-002 removed the refusal outright, so the WRITER removed census
row 57 and its narrative paragraph rather than restating them, folded the
sidecar case into row 31's trigger column, and rewrote
`docs/coding-standards.md`'s "It never blocks" paragraph to say
"without exception" with the malformed sidecar described as one of the findings
rather than a case apart. All three statements match the shipped code.

Rollup arithmetic checked: "56 sites, 0 unclassified. 28 BLOCK-WORTHY, 26
CONVERT, 2 AGENT-RESOLVABLE" — 28+26+2 = 56, and the previous rollup was 57 with
3 AGENT-RESOLVABLE. The fenced `CENSUS-LOCATOR-INDEX` block was edited only by
the CODER ticket (one line removed); `git diff` for the WRITER commit touches
only `.md` files.

### Finding 6: gated test suites

**Category:** pass

Both authoritative gated runners were run from the worktree root with
`--coverage`:

- `bash team/scripts/run-unit-tests.sh --coverage` → **UNIT_EXIT=0**, 3805
  passed, 3 skipped, 497.98s
- `bash team/scripts/run-integration-tests.sh --coverage` → **INTEGRATION_EXIT=0**,
  442 passed, 1 skipped, 401.28s

The PASS evidence here is the gated runners' exit codes, not a direct pytest
line. No test failures, no stale assertions, no pre-existing failures.

### Finding 7: new test coverage for the RC's changes

**Category:** pass

`team/tests/unit/test_lint_hygiene.py` replaces the stale exit-2 assertion with
seven tests covering every malformed-sidecar shape plus the
no-suppression-from-a-broken-file rule.
`team/tests/unit/test_lint_bug_filer.py` adds
`test_filed_bug_remedies_omit_the_scan_root_and_run_from_a_fresh_checkout`,
which invokes the real lint through `subprocess` against a synthetic checkout,
files through the real filer, asserts the scan root is absent, then executes the
persisted remedy from a **second independent checkout** and checks the sidecar
entry it writes.

On the Test Fidelity checklist: this is a genuinely high-fidelity test. It
exercises the real script through a subprocess rather than mocking it, it
simulates the installed layout (`team/scripts/lint_hygiene.py` at its real
repo-relative location, so the default-repo-root computation is the real one),
and the second-checkout step proves portability rather than mere relativeness —
a test that re-ran the command in the generating tree would have passed against
the pre-fix code. Both CODER notes record hand-confirmation that the new tests
fail when the fix is reverted.

### Finding 8: structural, compile, and constraint checks

**Category:** pass

- Every `.py` in the tree compiles (`python3 -m py_compile`, exit 0) — the
  requirements doc's second acceptance item.
- Every `.sh` in the tree passes `bash -n` — the third acceptance item.
- `python3 team/scripts/lint_gate_census_compliance.py` → exit 0, 0 offenders,
  34 files checked, 118 anchored sites reconciled.
- No secrets, credentials, or banned patterns introduced by the diff.
- The only provenance-shaped tokens in the added lines are two `BUG-0042`
  occurrences inside deliberate test-fixture strings (synthetic violations the
  lint is meant to detect); the live hygiene lint does not flag them.

### Finding 9: pre-flight, drift, prerequisite graph

**Category:** pass

- Pre-flight merge verification passed: every DONE task with a live feature
  branch is an ancestor of the RC with unique commits; the three tasks in this
  RC deleted their branches post-merge, which is normal.
- Worktree HEAD `54b00c7` == `ai_rc/v2.16.1`; `git status --short` clean;
  `git stash list` empty.
- `pp_last_released_version` → `v2.16.0`; `semver_gt v2.16.1 v2.16.0` holds.
  `release-state.md` `## Last Released` is `v2.16.0`, well-formed; `## Active RC`
  is `v2.16.1` and the branch exists. No drift.
- Prerequisite graph is acyclic, every referenced task ID resolves to a real
  folder, sequence numbers 001–008 do not collide, and the lifecycle bookends
  are correct (CM-open first, TESTER then CM-release last).
- Skip-cites-real-bug gate (Step 6.13): no placeholder skip IDs and no cited
  `BUG-NNNN` missing from `bugs/`. The only grep hits are the lint's own
  narration text inside archived run logs under `team/tests/artifacts/`.
- Synthetic release simulation (Step 6.8): skipped with note — the diff touches
  no release-lifecycle file (`cm-release.sh`, `cm-open-rc.sh`, `cm-cancel-rc.sh`,
  `project_paths.sh`, or any open/close-cycle script).

## Code Coverage

- Unit:        line 75%  |  branch 62%   (14906 stmts, 4490 branches; blended `TOTAL` reads 72%)
- Integration: line 8%   |  branch 6%    (blended `TOTAL` reads 7%)

Read from the `totals` object of `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/unit.json`
and `integration.json` (`percent_statements_covered_display` /
`percent_branches_covered_display`, field names confirmed against the generated
files). Informational only; coverage does not enter the PASS precondition and is
not a finding.

## Lint Findings (non-blocking)

- `hygiene` (`provenance`) — `release-notes/v2.16.0.md:42` cites the task ID
  `TESTER-20260910-070` in shipped prose — cites BUG-0236 (open). Present in
  both `findings-unit.json` and `findings-integration.json`; one finding per
  file, one lint class, one existing bug. The aggregate filer correctly cited
  BUG-0236 during this verification run rather than filing a duplicate — no new
  bug was created by either runner.

No other quality-class findings in either file.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=3d freshness=stale — the last lane run is only 3 days old and so inside the 7-day window, but it exercised v2.9.20 rather than the v2.16.1 under verification, so it is stale on the version condition alone; its red verdict was the `claude-abliterated` FROM-arity parse error and the `rocky9` non-octal `COPY --chmod` under podman, already tracked by BUG-0167 (filed by that deep-verify run, now `## Status: done`), and nothing has been built cold against this tree.

## Autonomous criterion

Manual interventions required: none.

1. **Tasks force-promoted from WAITING to BACKLOG by hand?** NO. Every
   prerequisite-gated task carries the wake script's own promotion note; the
   TESTER task's own status reads "Promoted from WAITING to BACKLOG by wake
   script: prerequisites satisfied."
2. **`release-state.md` edited outside the CM scripts?** NO. Its four fields
   match what CM-20260911-005's completion note records `open-rc.sh` as having
   written (Active RC `v2.16.1`, opened `2026-09-11T03:19:27+00:00`, by task
   `CM-20260911-005-open-rc-2-16-1`, Last Released `v2.16.0`).
3. **Scripts patched mid-build?** NO. `ai_main` has not moved since the RC was
   opened — its newest commit `d25e9a6` is timestamped 03:09:19, ten minutes
   before the 03:19:27 RC open. Every commit on the RC is a task's own merge.
4. **Queue files manually edited?** NO. `tester_backlog.md` shows the normal
   marker progression with this task at `[A]`.

## Caveats

- **Deferral posture.** PM's completion note flagged that both bundled items are
  `## Severity: low` while `projects/pgai-agent-kanban/DEFER-LOW-BUGS` reads
  `until = v2.22.0`, making this RC entirely deferred-class work. I checked
  this rather than repeating it: `resolve_defer_posture` returns
  `deferred_bugs=true|campaign file`, `deferred_bug_level=low|campaign file`,
  and `team/scripts/lib/discovery_defer.sh` documents an explicit
  requirements-queue-empty path — "is there nothing else left in the
  requirements queue for the chain to do, so the hygiene sweep may bundle the
  litter?" — under which deferred low bugs are bundled by design. This is the
  designed hygiene-sweep behavior, not a defect. Observation, not a finding.
- **`_add_suppression`'s `return 2` on a corrupt sidecar.** The census narrative
  added by this release says the script's only remaining non-zero exit is "a bad
  CLI argument at the call site, which no authored content can trigger."
  Strictly, `_add_suppression` also returns 2 when the checked-in sidecar is
  unreadable or malformed JSON, which *is* authored content. It is unreachable
  from either gated runner (nothing invokes `--add-suppression` automatically),
  and the function's own docstring frames it as operator-facing argument
  validation. The census lint agrees — 0 findings. Noted as prose imprecision,
  demoted to an observation rather than a finding.
- **`BUG-0239` should be closed by whoever fixes BUG-0240.** It is not a defect
  report about the shipped tree; it is an artifact of a scratch reproduction. It
  is currently `open` and eligible for bundling. This is stated in BUG-0240's
  `## Files Involved` and `## Acceptance` so the fixing CODER cannot miss it.
- **Scratch hygiene.** Every reproduction in this report ran under
  `$PGAI_AGENT_KANBAN_TEMP_DIR`. The live worktree was never mutated —
  `git status --short` was clean at the start and remains clean. No
  state-mutating script was pointed at the live `$PGAI_AGENT_KANBAN_ROOT_PATH`.
