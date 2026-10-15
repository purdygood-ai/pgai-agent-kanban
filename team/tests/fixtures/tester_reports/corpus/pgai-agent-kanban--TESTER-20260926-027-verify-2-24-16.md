# Verification Report: rc/v2.24.16

## Summary

Verified release candidate `ai_rc/v2.24.16` (commit `9f778ea`) against
`requirements/v2.24.16-bugfix-bundle-20260926.md`, a four-item BUG bundle
(`BUG-0393` guard-exemption bound, `BUG-0394` model-fallback narration,
`BUG-0395` hygiene-filer proof gate, `BUG-0396` non-CM fetch evidence noise).
Pre-flight passed: the worktree HEAD equals the RC commit, the tree is clean,
no stashes, and all four CODER feature branches were merged and deleted.
Both gated runners exit 0 -- `run-unit-tests.sh` (5022 passed, 31 skipped) and
`run-integration-tests.sh` (548 passed, 1 skipped) -- with zero quality-class
lint findings in either findings file. All four bundled items were exercised
behaviourally rather than by inspection: every residual git-guard option shape
named in `BUG-0393` is now refused with exactly one evidence record and
leaves the destination's refs untouched, fixture-local invocations remain
exempt, `pp_last_released_version` skips its fetch for every non-CM role while
returning the same answer, no "subagent default" string survives anywhere in
`team/scripts/` or `team/templates/`, and the hygiene filer's new proof gate
correctly drops an unprovable finding and files a provable one. Four findings
needing follow-up were recorded and filed via Path C (autonomous follow-up
filing -- non-blocking): two medium (the filer's gates still fail open when the
run cannot resolve a commit, evidenced by `BUG-0397` filed during this cycle
at `Observed At Commit: unknown`; and the guard's new temp-root normalisation
widening a BLOCK-WORTHY exemption for any configured temp root containing a
`projects` component) and two low (provenance in new test identifiers, and a
docstring naming the wrong output stream). The build ran autonomously with no
manual intervention on the task pipeline.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

medium

## Findings

### Finding 1: Gated unit suite

**Category:** pass

`bash team/scripts/run-unit-tests.sh --coverage` from the worktree root:
`UNIT_EXIT=0`. `5022 passed, 31 skipped in 862.83s`. Lint pre-flight green,
including `lint_skip_bug_gate: OK -- all skip annotations are compliant`, and
the three post-suite hygiene checks (`bare_tmp_cleanliness`,
`listener_cleanliness`, `scratch_sweep`) all OK. Output captured at
`/tmp/pgai_kanban_tmp/tester/unit.log`.

### Finding 2: Gated integration suite

**Category:** pass

`bash team/scripts/run-integration-tests.sh --coverage`: `INTEGRATION_EXIT=0`.
`548 passed, 1 skipped in 741.65s`. Output captured at
`/tmp/pgai_kanban_tmp/tester/integration.log`.

### Finding 3: Requirements-doc acceptance criteria

**Category:** pass

- "Each bundled item is addressed in the implementation" -- all four are, see
  Findings 4-7.
- "All Python files compile cleanly" -- `python3 -m py_compile` over every
  `.py` in `git diff --name-only ai_main..ai_rc/v2.24.16` exits 0.
- "All bash files pass syntax check" -- `bash -n` over every `.sh` in the same
  diff exits 0 for each.

### Finding 4: BUG-0393 -- the failed-probe exemption is bound to `repo_path`

**Category:** pass-with-caveat

Exercised through `team/scripts/wake/git-guard/git` under
`PGAI_AGENT_ROLE=coder`, against a repository OUTSIDE the configured temp
root whose `origin` is a real local bare repository. Every invocation the bug
named is refused with exit 1, appends exactly one evidence record, and leaves
the destination's refs unchanged:

| Invocation | exit | records | dest refs changed |
|---|---|---|---|
| `fetch origin` | 1 | 1 | no |
| `fetch --depth 5 origin` | 1 | 1 | no |
| `push -o ci.skip origin main` | 1 | 1 | no |
| `push --push-option ci.skip origin main` | 1 | 1 | no |
| `fetch --filter blob:none origin` | 1 | 1 | no |
| `pull -s ours origin main` | 1 | 1 | no |
| `pull -X theirs origin main` | 1 | 1 | no |
| `pull -m msg origin main` | 1 | 1 | no |
| `push --repo origin main` | 1 | 1 | no |
| `fetch --refmap +refs/... origin` | 1 | 1 | no |
| `pull --into-name main origin main` (option NOT on the skip list) | 1 | 1 | no |
| `fetch --negotiate-only origin` | 1 | 1 | no |
| `-C <torn-down dir> fetch origin` | 1 | 1 | n/a |

The refusal is dual-channel:
`error: git-boundary-guard.non-cm-remote-verb` / `message: role 'coder'
invoked 'git push', a remote operation only CM may run` / `remedy: ask CM to
run 'git push' ...`. The last two table rows are the load-bearing evidence
that the fix is the `repo_path` bound and not the option list: neither option
is on the skip list, and both are refused anyway.

Against a repository INSIDE the temp root, all twelve shapes remain exempt
with zero evidence records, so the fixture-noise reduction survives. Role `cm`
against the outside repository passes through with zero records.

New unit coverage is present and matches the bug's acceptance:
`test_fetch_unlisted_value_consuming_option_outside_root_still_refused` and
`test_push_unlisted_value_consuming_option_outside_root_still_refused`, plus
three others. `docs/blocking-site-census.md` row 62 was rewritten to describe
the `repo_path` bound and explicitly states the option list is "defence in
depth, not a closed set".

**Caveat:** the new `_git_boundary_guard_path_under_temp_root` helper
introduces a separate widening of the same exemption under a non-default
`PGAI_AGENT_KANBAN_TEMP_DIR` -- recorded as Finding 8, not as a failure of
this criterion.

### Finding 5: BUG-0394 -- narration and templates name the fallback that exists

**Category:** pass

- `grep -rn "subagent default" team/scripts/ team/templates/` returns no hit
  (exit 1). A whole-tree grep across `*.sh`, `*.md` and `*.py` also returns
  nothing.
- `wake_driver_prep.sh:145` now sets `model_source="provider CLI default for
  ${SUBAGENT}"`, which is what `provider_claude.sh:162` and
  `provider_codex.sh:413` print on their own when `selected_model` is empty --
  so the operator-visible wake line reads `wake(<role>): model: provider CLI
  default for <subagent>`.
- `provider_claude.sh:126` pre-flight placeholder is now
  `<provider CLI default>`; `wake_driver.sh:522`'s comment matches.
- `REQUIREMENTS-TEMPLATE.md` (lines 203, 222) and `BRIEF-TEMPLATE.md`
  (line 87) now describe the omitted-section case in `docs/OPERATIONS.md`'s
  own tier-3 terms.
- `test_wake_model_override_precedence.py`'s parametrised case was updated to
  the new literal in the same change and passes in the green suite.

### Finding 6: BUG-0395 -- the hygiene filer's commit-content proof gate

**Category:** pass-with-caveat

Exercised directly against the RC's filer with a scratch git repository:

- A finding citing `sample_test.py:5` with token `BUG-0001`, where line 5 of
  that file at HEAD contains no such token: `results=[]`, no bug file written,
  and the run narrates
  `[bug_filer] class 'provenance': 1 finding(s) did not survive the
  commit-content proof gate at <sha> ...`.
- The same finding with a token that IS present at line 5: one bug filed, with
  `## Observed At Commit` stamped to the real HEAD sha.
- `python3 team/scripts/lint_hygiene.py --classes provenance` reports
  `OK -- 0 findings` on `ai_main` and on the RC tip.
- Three new unit tests cover the gate, including the mixed
  provable/unprovable case and the file-absent-at-commit case.

**Caveat:** the gate is correct when a commit resolves, and gates nothing when
it does not -- see Finding 9, filed separately, for the path that still fires.

### Finding 7: BUG-0396 -- `pp_last_released_version` skips its fetch for non-CM roles

**Category:** pass-with-caveat

Instrumented with a PATH-local `git` stand-in that records any `fetch` and
forwards everything else, run against the real project registry (no network
reached, because the stand-in intercepts `fetch`):

| Source | role | answer | fetch attempts |
|---|---|---|---|
| RC | `coder` | v2.24.15 | 0 |
| RC | `tester` | v2.24.15 | 0 |
| RC | `CM` | v2.24.15 | 1 |
| RC | `cm` | v2.24.15 | 1 |
| RC | unset | v2.24.15 | 1 |
| RC | `cm` + `PGAI_GIT_LOCAL_ONLY=1` | v2.24.15 | 0 |
| live install (v2.24.15) | `coder` | v2.24.15 | 1 |

The answer is identical in every case, the role match is case-insensitive, CM
and an unset role still get the refresh, and `PGAI_GIT_LOCAL_ONLY` still wins
-- exactly the contract the revised header states. The last row is the pre-RC
behaviour the bug describes.

**Caveat on the bug's first acceptance bullet** ("a full `run-unit-tests.sh`
and `run-integration-tests.sh` run from a non-CM wake session adds zero
records to that task's `logs/git-boundary-guard.jsonl`"): this session's suite
run added one record --
`git -C /tmp/pgai_kanban_tmp/pytest/integration/test_nothing_resolves_until_th0/dev_repo fetch origin`,
which originates in `team/scripts/cm/cancel-rc.sh` inside an OVERWATCH lane-A
fixture. That record was written by the **live install's** guard shim, which
is still v2.24.15 (the shim on `PATH` is `$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/wake/git-guard/git`,
not the worktree's). Invoking that exact shape through the RC's shim with the
same environment produces **zero** records -- the RC exempts it. So the
criterion holds for the code under test and will be observable end-to-end only
once v2.24.16 is installed. The RC's own 8-records-per-suite
`pp_last_released_version` shape, visible in CODER-022/023/024's logs, is
already gone from CODER-025's log.

### Finding 8: Guard's temp-root normalisation widens a BLOCK-WORTHY exemption

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (the same exemption has now been narrowed or widened
in three consecutive RCs; a widening introduced while closing a widening is a
pattern worth watching, though this instance is a distinct mechanism rather
than a failure to progress on the previous one)
**Regression:** yes
**Integrity:** none
**Filed As:** BUG-0398 (Path C-A -- filed as BUG: code defect / regression /
test failure)

`_git_boundary_guard_path_under_temp_root`, new in this RC, normalises the
configured temp root upward with `${canon_temp_root%/projects/*}`. `%` strips
the shortest matching suffix, so the cut lands at the **last** `/projects/`
component at any depth, and every repository under that component's parent is
treated as fixture-local -- exempt, unrefused, unrecorded.

Demonstrated on `9f778ea` with
`PGAI_AGENT_KANBAN_TEMP_DIR=<sandbox>/projects/myproj/scratch` and a real
repository at `<sandbox>/outside/work`:

```
env ... PGAI_AGENT_ROLE=coder PGAI_AGENT_KANBAN_TEMP_DIR="$FAKE" \
    team/scripts/wake/git-guard/git -C "$S/outside/work" \
    push --push-option ci.skip origin main
-> "* [new branch]      main -> main"   # not refused
-> records=0                            # nothing recorded
-> upstream now carries refs/heads/main
```

The same command with the shipped default root is refused with one record. The
helper probed directly returns true for `<sandbox>/outside/work` and for
`<sandbox>` itself under that configuration.

The function's header claims the normalisation applies when the root "ends in
`.../projects/<name>`" and moves "up one level". Neither is true of the code:
the pattern is `*/projects/*` at any depth, and the cut is to the parent of
that component however deep it sits. This is the same header-versus-code
mismatch `BUG-0393` was filed for, in the function added to close it.

Rated medium rather than high because the shipped default root
(`/tmp/pgai_kanban_tmp`) has no `projects` component and the intended
per-project root `<framework>/projects/<name>` normalises correctly, so
nothing in this deployment exercises the broken path.
`PGAI_AGENT_KANBAN_TEMP_DIR` is operator-settable, which is why it is a defect
rather than an observation. Both callers of the helper are affected -- the
destination half of `_git_boundary_guard_is_fixture_local` as well as the new
`repo_path` half -- so the widening is not limited to the
unreachable-destination shapes.

### Finding 9: Hygiene filer still files at `Observed At Commit: unknown`

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (the same unactionable-filing class has now produced
one bug per cycle for two consecutive cycles, and each one consumes a PM
decomposition and a CODER slot)
**Regression:** no
**Integrity:** none
**Filed As:** BUG-0401 (Path C-A)

`bugs/BUG-0397-hygiene-lint-provenance.md` was filed automatically at
2026-09-27T01:07:54 -- during this cycle -- with `## Observed At Commit:
unknown` and three citations (`git_boundary_guard.sh:543`,
`project_paths.sh:662`, `project_paths.sh:856`, all naming `BUG-0396`). Those
citations exist in exactly one commit in the repository, `5a0c1f7`
(01:38:31), and were removed again at `1b7bc35` (01:40:59); they are absent
from the RC tip and from `ai_main`, and `lint_hygiene.py --classes provenance`
reports zero findings on both. At 01:07 they existed only in an agent's
uncommitted working copy -- precisely the transient case the tree-cleanliness
gate exists to drop.

Reproduced against the RC's own filer, so this is not merely an artefact of
the live install still running v2.24.15: handed a findings file whose cited
token is absent and a `repo_root` that is not a git repository,
`file_or_cite_hygiene_bugs` returns `outcome='filed'` and stamps
`## Observed At Commit: unknown`. Three helpers fail open on the same missing
input -- `_git_head_commit` returns `None` (rendered `unknown`),
`_dirty_relative_paths` returns an empty set ("nothing is dirty"), and
`_finding_provable_at_commit` takes its documented "cannot answer is not proof
it is wrong" branch. `_REPO_ROOT` self-locates as
`_LINT_HYGIENE_SCRIPT.parents[2]`, which for the installed layout resolves to
`$HOME` -- not a checkout (`git -C $HOME rev-parse HEAD` ->
`fatal: not a git repository`).

The gate ships working for the case it was written for (a resolvable commit
whose tree disagrees with the finding) and does not cover the case that
actually fired. `BUG-0395`'s first two acceptance bullets are therefore
partially met, which is why this is recorded as `gap` rather than `bug`.

### Finding 10: Provenance in new guard test identifiers and docstrings

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated to one test file; the lint gap that let it
through is noted in the filing)
**Filed As:** BUG-0399 (Path C-A)

`team/tests/unit/test_git_boundary_guard.py` introduces
`_BUG_0393_REPRO_INVOCATIONS` (line 758, referenced at 766 and 815) and two
tests named `test_bug_0393_repro_invocation_*` (lines 767, 816), whose
docstrings narrate "The residual reproduction set this fix closes" and
"Before this fix, none of these four were refused at all". `SOP-core.md`
("Naming: describe behavior, not scaffolding or provenance") forbids
provenance names and process history in docstrings, and names tests
explicitly; none of its three exceptions applies (no skip annotation, no usage
example, no external constraint). All five sites are new in this RC.

The hygiene lint's `provenance` class does not catch them -- it scans comment
and prose surfaces for `BUG-NNNN`-shaped tokens, not Python identifiers -- so
the suite is green with the violation in place. Closing that lint gap is
proposed in the filing as a scoped follow-on, not as part of the rename.

### Finding 11: Filer docstrings name stdout; the code writes stderr

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (two docstring lines in one module)
**Filed As:** BUG-0400 (Path C-A)

`team/pgai_agent_kanban/lint/bug_filer.py` line 105 ("this run's stdout says
so") and line 659 ("the run's own stdout narrates when this happens") both
name stdout; `_note_unprovable_findings` writes with `file=sys.stderr`
(line 462) and its own docstring says stderr. Confirmed by running the filer
with the streams separated: the diagnostic appears on fd 2 and not on fd 1.
The practical cost is that a caller capturing only stdout cannot tell "no
findings this run" from "every finding dropped by the gate", which is the
distinction the docstring says the narration exists to make visible.

### Finding 12: Release-state and version ordering

**Category:** pass

`release-state.md` holds all four fields, written by `cm/open-rc.sh`:
`Active RC: v2.24.16`, `RC Opened At: 2026-09-26T22:37:15+00:00`,
`RC Opened By Task: CM-20260926-026-open-rc-2-24-16`,
`Last Released: v2.24.15` (well-formed). `pp_last_released_version
pgai-agent-kanban` returns `v2.24.15`, and `semver_gt v2.24.16 v2.24.15` from
`team/scripts/lib/semver.sh` returns true -- the RC version is strictly
greater than the last released version. No drift.

### Finding 13: Structural and constraint checks

**Category:** pass

- Diff is 12 files, +731/-106, all under `docs/`, `team/scripts/`,
  `team/pgai_agent_kanban/`, `team/templates/` and `team/tests/`. No file
  expected by a bundled item is missing from it.
- `team/scripts/wake/git-guard/git` remains executable and
  `git_boundary_guard.sh` retains its mode.
- No `set -euo pipefail` was removed: neither modified library carried it on
  `ai_main` (they are sourced libraries, where `-e` would be wrong) and
  neither gained or lost it.
- Secrets scan over added lines: no hardcoded key, password, token or secret.
- Step 6.13 skip-cites-a-real-bug: the gate ran green in the unit pre-flight
  (`BUG-NNNN file-existence check: 0 missing files`), and a manual grep finds
  no skip annotation citing any `BUG-NNNN` outside the scanner's own fixture
  strings.
- Step 6.14 coding-standards spot-check: no new script under `team/scripts/`
  (so no `--help` obligation), no newly hardcoded operational value, no
  duplicated `lib/` implementation. The one hit is Finding 10.

### Finding 14: Synthetic release simulation (Step 6.8)

**Category:** pass

The diff touches `team/scripts/lib/project_paths.sh`, so the step applies.
`team/tests/integration/test_cm_release_synthetic.py` ran inside the gated
integration suite and all 17 of its cases passed
(`tests/integration/test_cm_release_synthetic.py .................`). No real
branch was touched.

### Finding 15: Prerequisite dependency graph (Step 6.10)

**Category:** pass

Acyclic, every referenced task ID exists, and the lifecycle bookends are
linked correctly: `CM-20260926-026-open-rc-2-24-16` is the sole prerequisite
of each of the four CODER tickets; `CODER-20260926-025` additionally depends
on `CODER-20260926-022` (both touch `git_boundary_guard.sh`, so the ordering
is real); `TESTER-20260926-027` depends on all four CODER tickets; and
`CM-20260926-029-release-2-24-16` depends on those four plus TESTER. Sequence
numbers are consistent and non-colliding.

### Finding 16: Test fidelity of the new coverage

**Category:** pass

The six new guard tests drive the real shim as a subprocess against real git
repositories with real bare upstreams, and assert on the destination's refs
rather than only on the guard's exit code -- they would catch a "refused but
the push went through anyway" regression, which is the condition that actually
broke. The three new filer tests build real commits and read them back with
`git show`. Nothing material is mocked away in either set. The one gap is
coverage-shaped rather than fidelity-shaped: no new test exercises
`_git_boundary_guard_path_under_temp_root`'s normalisation branch or the
filer's unresolvable-root path, which is how Findings 8 and 9 survived a green
suite; both filings name the missing case.

## Lint Findings (non-blocking)

None. `/tmp/pgai_kanban_tmp/lint/findings-unit.json` and
`/tmp/pgai_kanban_tmp/lint/findings-integration.json` were both present and
both reported `0 finding(s)`.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=19d freshness=stale — The last cold-build record predates this RC by 19 days and names v2.9.20 rather than the v2.24.16 under verification, so both freshness conditions fail; its failed-to-build verdict for docker/claude-abliterated and podman/rocky9 is already recorded in BUG-0167-cold-build-claude-abliterated-failure, cited here rather than re-filed.

## Coverage (informational only)

| Suite | line (statement) | branch | blended (TOTAL Cover column) |
|---|---|---|---|
| unit | 76.42 % | 63.29 % | 73.37 % |
| integration | 11.41 % | 9.13 % | 10.88 % |

Read from the `totals` objects in `/tmp/pgai_kanban_tmp/coverage/unit.json`
and `.../integration.json` (`percent_statements_covered`,
`percent_branches_covered`, `percent_covered`), field names verified against
the generated files. Coverage does not enter the verdict.

## Autonomous criterion

Manual interventions required: none on the task pipeline. Question (5) is YES,
rated `low`.

1. **Were any tasks force-promoted from WAITING to BACKLOG by hand?** NO. Each
   cycle task's `status.md` records the wake script's own promotion line; no
   status file contains hand-promotion language.
2. **Was `release-state.md` edited outside `cm-open-rc.sh` / `cm-release.sh` /
   `cm-cancel-rc.sh`?** NO. Its four fields match what
   `CM-20260926-026-open-rc-2-24-16` reports writing, and the accompanying
   `release-state/v2.24.16.json` was written by the same run.
3. **Were any scripts patched mid-build?** NO. `git log --since=<RC open>
   ai_main` is empty, and every commit reachable from the RC and not from
   `ai_main` is one of the four tickets' own deliverables, each on its own
   feature branch merged with `--no-ff`. The tickets legitimately modify
   `team/scripts/lib/` because that is their assigned scope.
4. **Were any queue files edited by hand?** NO. Every entry in
   `coder_backlog.md`, `pm_backlog.md`, `cm_backlog.md` and
   `tester_backlog.md` uses the canonical `- [x] TASK-ID` shape, and each
   marker matches its task's own `## State` (`[x]` for the four DONE CODER
   tasks and the DONE CM open-rc task, `[A]` for this WORKING TESTER task,
   `[W]` for the WAITING `CM-20260926-029-release-2-24-16`).
5. **Did any agent other than CM invoke a remote git operation?** YES --
   31 records across this cycle's task folders: `CODER-022` 9, `CODER-023` 8,
   `CODER-024` 8, `CODER-025` 3, `PM-003` 1, `TESTER-027` 2, `CM-026` 0.
   Reading the records rather than counting them: every one is a refused
   `fetch` under role `coder` or `tester`; there is no `pull` and no `push`,
   no record under role `unknown`, no ref state changed anywhere, and the
   boundary held in every case -- rated `low` per
   `docs/tester-verification-reference.md`. Their origin is framework code,
   not an agent reaching for a remote: 24 are `pp_last_released_version`'s
   best-effort refresh inside unit fixtures, one is the same helper against
   the real dev tree from PM's decompose task, and the remainder are
   `cm/cancel-rc.sh`'s `fetch origin` inside an OVERWATCH lane-A integration
   fixture. This is exactly the class `BUG-0396` records, and both halves of
   its fix are verified working in Finding 7 -- the records persist only
   because the guard and helper on `PATH` are still the live install's
   v2.24.15 copies. Cited to `BUG-0396` (currently `## Status: running`,
   closed by this RC) rather than re-filed; filing a duplicate for a defect
   this RC fixes would add queue noise without adding information. It does not
   elevate the recommendation.

## Caveats

- **Verification ran against the RC worktree, but the `git` shim on `PATH` is
  the live install's.** Every behavioural check in Findings 4, 7 and 9 was
  therefore run by invoking the RC's own shim and library by absolute path,
  and the live install's copies were run side by side as the pre-fix control.
  The one place this matters for a stated acceptance criterion is noted inline
  in Finding 7.
- **The CM role's `pp_last_released_version` fetch was exercised with an
  intercepting `git` stand-in, never against the real origin.** TESTER does
  not touch origin (`SOP-core.md`, "Git Repositories: Roles and Boundaries";
  `DIRECTIVES.md` 1), so the `role=cm` row in Finding 7's table proves the
  branch is taken, not that a network fetch succeeds.
- **Observation, not a finding (Step 6.11):** the branch
  `ai_feature/CODER-20260926-017-provenance-sweep` still exists locally. Its
  task ended `WONT-DO` in the previous cycle and the branch carries no unique
  work -- it points at the v2.24.15 RC tip (`0738ada`), whose content shipped.
  It is not reachable from `ai_main` (the release squashed), so
  `git branch -d` would refuse it. Prior-cycle cleanup debt, outside this RC's
  spec; noted for a human to sweep, not filed.
- **Observation, not a finding:** `bug_backlog.md` shows `BUG-0393` through
  `BUG-0396` as `[x]` while each file's own `## Status` reads `running`. Per
  `SOP-core.md` ("Derived caches are not gates") the file's own header
  governs, `running` is skipped by discovery unconditionally, and
  `cm-release.sh` Step 16b sets them `done` at ship. Consistent, not drift.
- **`BUG-0397-hygiene-lint-provenance.md` is currently `## Status: open`**, so
  discovery will bundle it and PM will decompose it into a ticket with nothing
  to fix -- the same waste `BUG-0395` documents. Whoever picks up BUG-0401
  should settle that item's disposition on the record as part of the fix.
- No `SHIP-WITH-SERIOUS-CONCERNS` condition was found: no data corruption, no
  broken release pipeline, no security defect, and no autonomy failure. The
  release is usable; the four filings enter the next iteration's bundle.
