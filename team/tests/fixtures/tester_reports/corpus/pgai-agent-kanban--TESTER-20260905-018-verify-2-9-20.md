# Verification Report: ai_rc/v2.9.20

**Task:** TESTER-20260905-018-verify-2-9-20
**Project:** pgai-agent-kanban
**RC branch:** `ai_rc/v2.9.20` @ `009ba622cc5b502bb97efb1a4c50a72972ae6416`
**Base branch:** `ai_main` @ `92dae1b`
**Requirements:** `projects/pgai-agent-kanban/requirements/v2.9.20-engine-decomposition.md`
**Worktree:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260905-018-verify-2-9-20` (detached, clean, no stashes)
**Date:** 2026-09-05

## Summary

v2.9.20 is a behavior-preserving decomposition of five engine monoliths into
thin drivers over single-concern lib stages, plus a new engine-size/duplication
lint. Judged against its own requirements document, the work is good: every
named engine file is now a driver under 300 lines, every extracted stage is
under the 800-line ceiling, `wake/codex.sh` is a 297-line shim over a shared
wake driver with per-provider libs behind a four-function interface, the
boilerplate collapse onto `lib/script_common.sh` leaves exactly one definition
of `cleanup_on_exit`, `ansi_code`, and the usage helper, the new lint is wired
quality-class into both gated runners and fires correctly on both seeded
fixtures while staying quiet on the clean tree, and the behavior-baseline
harness reports all three captured operator surfaces byte-identical to goldens
that were captured before the decomposition began.

Verification nonetheless found a defect that sits above the requirements
document. `ai_rc/v2.9.20` was branched from an `ai_main` that had been
re-created at the v2.4.0 commit, so the RC does not contain the last shipped
release: `ai_v2.9.19` is not an ancestor of the RC, and the RC tree is 228
files and ~97.7k lines behind the tag the operator is actually running.
Tagging v2.9.20 from this RC would publish a tree missing everything shipped
in v2.4.1 ... v2.9.19 -- including `team/scripts/deep-verify.sh`,
`team/scripts/lib/deep_verify_ledger.sh`, and `team/scripts/task-status.sh`,
all of which the live SOP and role files require. The decomposition itself was
therefore applied to files 400-900 lines smaller than the ones the requirements
document sizes, which is independent confirmation that the base was wrong.

Separately, the gated unit runner exits 1 on five pre-existing test failures
whose fixtures read the operator's live kanban bug ledger rather than committed
fixture data. Those five fail identically on the base branch and are not this
RC's doing.

## Recommendation

SHIP-WITH-SERIOUS-CONCERNS

## Systemic Risk

high

## Suite Results

| Suite | Gated runner | Exit | Result |
|---|---|---|---|
| Unit | `bash team/scripts/run-unit-tests.sh --coverage` | **1** | 5 failed, 3054 passed, 3 skipped (322 s). All 16 quality-class lint pre-flights OK; the failures are pytest failures -- see Finding 3. |
| Integration | `bash team/scripts/run-integration-tests.sh --coverage` | **0** | 299 passed, 1 skipped (222 s). Bare-`/tmp` and listener cleanliness checks OK. |

`UNIT_EXIT == 1`, so the suite-pass precondition for a `PASS` recommendation is
not met independently of the findings below.

## Code Coverage

- Unit:        line 74%  |  branch 60%   (13622 stmts, 4178 branches; blended `TOTAL` 71%)
- Integration: line 8%   |  branch 6%    (blended `TOTAL` 7%)

Source: `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/unit.json` and
`integration.json`, `totals` object (`percent_statements_covered`,
`percent_branches_covered`). Informational only.

## Findings

### Finding 1: RC is branched from a stale `ai_main` and would ship a five-release rollback

**Category:** bug
**Fix Effort:** large (the RC must be re-based onto the real lane tip and the decomposition re-applied against the v2.9.19-sized files; plus a new RC-open guard)
**Systemic Risk:** high (shipping this RC makes the product materially worse and orphans the lane's real history -- see the Systemic-Risk Rubric in SOP.md)
**Regression:** yes (functionality present in the last shipped version, v2.9.19, is absent from this RC's tree)
**Integrity Damage:** yes (squashing this RC onto `ai_main` and tagging makes the v2.4.1 ... v2.9.19 lineage unreachable from the release lane, and an operator upgrading to v2.9.20 loses the running system's scripts)
**Filed As:** BUG-0161 (Path C-A -- filed as BUG -- code defect / regression / test failure)

Evidence, all from `$HOME/develop/pgai-agent-kanban`:

- `git merge-base --is-ancestor ai_v2.9.19 ai_rc/v2.9.20` exits **1** -- the
  last shipped tag is not in the RC.
- `git reflog show ai_main` shows two entries only:
  `92dae1b ... commit: Regenerate CHANGELOG.md (drift detected by OVERWATCH sweep)`
  and `3515d93 ... branch: Created from HEAD`, where `3515d93` is the
  `pgai-agent-kanban v2.4.0` commit dated 2026-08-09. `ai_main` was created at
  v2.4.0, not at the lane tip.
- `git branch --contains ai_v2.9.19` prints `last_run_ai_main` only. That
  branch (`228f078`, "Polish release notes for ai_v2.9.19") is the real tip.
- `git diff --shortstat ai_v2.9.19 ai_rc/v2.9.20` ->
  `529 files changed, 20402 insertions(+), 97744 deletions(-)`.
  Tree sizes: `ai_main` 696 files, `ai_v2.9.19` 924, RC 733.
- The live install is running the branch the RC does not descend from:
  `$HOME/pgai_agent_kanban/VERSION` is `v2.9.19` and `VERSION_DETAIL` is
  `ai_v2.9.19-1-g228f078  deposit-sha=228f0780...`.
- Files present at `ai_v2.9.19` and in the live install but absent from the RC
  include `team/scripts/deep-verify.sh`,
  `team/scripts/lib/deep_verify_ledger.sh`, and `team/scripts/task-status.sh`
  -- the DEEP-VERIFY umbrella verb, its ledger reader, and the canonical
  status-writer tool that `SOP.md` and every role file name as the taught path.
- The requirements document independently confirms the mismatch. It sizes the
  monoliths at `cm/release.sh ~3.9k`, `discovery.sh ~3.2k`,
  `wake_common.sh ~3.1k`, `column-render.sh ~2.5k`, wake providers `~2k each`.
  Measured on the RC's actual base:

  | file | `ai_main` (RC base) | `ai_v2.9.19` | requirements |
  |---|---|---|---|
  | `team/scripts/cm/release.sh` | 3010 | 3903 | ~3.9k |
  | `team/scripts/lib/discovery.sh` | 2524 | 3224 | ~3.2k |
  | `team/scripts/lib/wake_common.sh` | 2573 | 3097 | ~3.1k |
  | `team/scripts/dashboard/column-render.sh` | 2102 | 2536 | ~2.5k |
  | `team/scripts/wake/claude.sh` | 2029 | 2010 | ~2k |
  | `team/scripts/wake/codex.sh` | 1942 | 1923 | ~2k |

  The requirements were written against `ai_v2.9.19`; the work was done against
  a tree five releases older.

The defect's *cause* predates this RC -- `ai_main` was already at v2.4.0 before
`CM-20260905-017-open-rc-2-9-20` opened the RC at 05:48:17Z, and the only other
commit on `ai_main` (an OVERWATCH CHANGELOG regeneration at 05:30:02Z) also
predates it. `Regression` is nevertheless `yes`, per the Step 7 rule: the
release this RC would produce lacks behavior that works in v2.9.19.

Nothing in the RC's own diff caused this and no CODER or WRITER task in the
cycle did anything wrong; the base branch was wrong before any of them started.

### Finding 2: tree ships Dockerfiles but carries no `team/scripts/deep-verify.sh`

**Category:** gap
**Fix Effort:** small (the file exists at `ai_v2.9.19`; it returns as soon as Finding 1's re-base lands)
**Systemic Risk:** medium (container images would ship with no cold-build gate anywhere in front of them, and the pre-release freshness check has no verb to point an operator at)
**Regression:** yes (`team/scripts/deep-verify.sh` and `team/scripts/lib/deep_verify_ledger.sh` both exist at `ai_v2.9.19`)
**Integrity Damage:** no
**Filed As:** BUG-0161 (Path C-A) -- same defect, same root cause; deliberately not filed twice

`ls docker/*/Dockerfile` in the RC lists three flavors
(`docker/claude-abliterated`, `docker/debian`, `docker/rocky9`), so the
deep-verify step applies to this tree. `test -f team/scripts/deep-verify.sh`
fails. Per `roles/TESTER.md` Step 5d ("The one case that does produce a Step-7
finding"), that is a `gap` finding. It is recorded here rather than filed as a
separate bug because it is one symptom of Finding 1, not an independent defect:
the runner is not missing from the codebase, it is missing from this branch.

### Finding 3: five unit tests read the operator's live bug ledger (gated unit runner exits 1)

**Category:** bug (pre-existing -- fails identically on the base branch)
**Fix Effort:** small (move the required bug files into `team/tests/fixtures/` and repoint `_REAL_BUGS_DIR`)
**Systemic Risk:** low (test-only; no production code path is implicated)
**Regression:** no -- established: `git diff ai_main..ai_rc/v2.9.20` touches neither test file nor `CHANGELOG.md` nor `team/pgai_agent_kanban/`, and a `git archive ai_main` export of the base branch reproduces exactly the same five failures (`5 failed, 26 passed, 1 skipped`)
**Integrity Damage:** no
**Filed As:** BUG-0162 (Path C-A)

Failing tests:

```
tests/unit/test_changelog_writer_real_data.py::TestRealDataChangelog::test_bug_0023_file_carries_public_id_ki_1_0_0_1
tests/unit/test_changelog_writer_real_data.py::TestRealDataChangelog::test_v1_0_0_entry_carries_ki_1_0_0_1_with_tmux_symptom
tests/unit/test_changelog_writer_real_data.py::TestRealDataChangelog::test_v1_0_0_entry_carries_correct_affects_fixed_in_disclosure
tests/unit/test_lint_changelog_freshness.py::TestChangelogContentGates::test_ki_1_0_0_1_renders_under_v1_0_0
tests/unit/test_lint_changelog_freshness.py::TestRCModeTolerance::test_rc_mode_tolerates_post_rc_bug_entry
```

Root assertion: `Expected exactly one BUG-0023-*.md in
$HOME/pgai_agent_kanban/projects/pgai-agent-kanban/bugs, found 0: []`.
`_REAL_BUGS_DIR` points at the operator's live kanban ledger, which currently
holds only `README.md` and `templates/`, so `changelog_writer` regenerates a
v1.0.0 entry whose Known Issues reads `None` and every `KI-1.0.0.1` assertion
fails. This is SOP Anti-pattern 2 (environment-coupled fixtures): the suite's
verdict depends on state outside the tree under test.

Classified `bug`, not `stale-assertion`: the asserted literal was not changed
by this RC's CODER work -- no production code in the RC produces the mismatch,
and the trace runs to an absent external file rather than to a changed source
line.

### Finding 4: five engine monoliths are now thin drivers over sized stages

**Category:** pass

Measured with `wc -l` in the RC worktree. Drivers, all under the 300-line
deliverable: `cm/release.sh` 299, `dashboard/column-render.sh` 299,
`wake/claude.sh` 297, `wake/codex.sh` 297, `lib/discovery.sh` 233,
`lib/wake_common.sh` 218. All 26 extracted stages are under the 800-line
ceiling; the largest is `lib/dashboard/column_render_data.sh` at 799,
then `lib/wake_task_state.sh` 787 and `lib/cm/release_finalize.sh` 771.

Acceptance criterion "`wake/codex.sh` deleted or <300-line shim" -- met at 297
lines, composing the shared `lib/wake_driver*.sh` pipeline.

### Finding 5: one wake driver with a four-function provider interface

**Category:** pass

`lib/wake_driver.sh` (565) plus `_prep` (263), `_worktree` (391), `_invoke`
(550), `_finalize` (433) form the shared pipeline; `lib/provider_claude.sh`
(230) and `lib/provider_codex.sh` (498) each define exactly the four interface
functions `provider_dispatch`, `provider_env_preflight`,
`provider_model_preflight`, `provider_parse_output`. Both `wake/claude.sh` and
`wake/codex.sh` compose the same driver. The deliberate same-name pairing is
the documented allow-list entry in the new lint, not an accident.

### Finding 6: boilerplate exists exactly once

**Category:** pass

`grep -rnE '^\s*(function\s+)?(cleanup_on_exit|ansi_code|usage|_usage|print_usage_and_exit)\s*\(\)' team/scripts --include='*.sh'`
returns three lines, all in `team/scripts/lib/script_common.sh`
(`ansi_code` 60, `cleanup_on_exit` 79, `print_usage_and_exit` 86). No other
definition survives anywhere under `team/scripts`.

### Finding 7: duplicate-function check returns zero, and the result survives a widened scan

**Category:** pass

`python3 team/scripts/lint_engine_size_dupe.py` reports
`no cross-file function-name collisions found` across the tree. The check is
reachability-aware: it builds the `source` graph and reports a collision only
when two definitions can land in one bash process, which is why the many
same-named local helpers in never-co-sourced scripts (`fail`, `warn`,
`get_val`, `section_header`) are correctly not reported.

Independently re-run with the lint's `_FUNC_DEF_RE` widened from
`^([a-z][a-z0-9_]*)\(\)` to `^([A-Za-z_][A-Za-z0-9_]*)\(\)`, so
underscore-prefixed and capitalised definitions are also scanned: still zero
collisions. The near-misses were checked by hand and are all sound --
`log()` in `lib/discovery.sh` is guarded by `if ! declare -F log`, and
`_run_release_hook` in `ship-rc.sh` versus `lib/cm/release_finalize.sh` never
share a process because `ship-rc.sh` does not source `cm/release.sh`.

### Finding 8: the new lint fires on both seeded fixtures and is quiet on the clean tree

**Category:** pass

Behavioral verification, Step 6.5:

- `--scripts-dir team/tests/fixtures/engine_size_dupe/oversized_engine` (a
  900-line `lib/discovery.sh`) -> exit 1,
  `FAIL -- 1 engine file(s) exceed the 800-line ceiling`.
- `--scripts-dir team/tests/fixtures/engine_size_dupe/duplicate_function` ->
  exit 1, `seeded_shared_helper() -- defined in: lib/stage_a.sh, lib/stage_b.sh`.
- Clean tree -> exit 0, `ok -- 32 engine file(s) within the 800-line ceiling`.

Both refusals emit the full dual-channel contract (`error:` / `message:` /
`remedy:`), the site carries `# CENSUS-ANCHOR: engine-size-dupe-01`, and the
census-compliance lint reports 0 offenders -- the `SOP.md` new-gate rule is
satisfied. The lint is wired into both gated runners through
`gated_runner_run_quality_lint "engine_size_dupe"`, i.e. quality-class and
non-fatal, as the deliverable requires.

### Finding 9: the three captured operator surfaces are byte-identical to pre-decomposition goldens

**Category:** pass

`bash team/scripts/harness/engine-behavior-baseline.sh --compare <fresh-dir>`
-> exit 0, `compare OK -- 3 surfaces match goldens`. The goldens
(`team/tests/fixtures/engine-baseline/{board-render,ship-summary,wake-log}.txt`)
were committed once, by the cycle's first ticket
(`436f8f6`, CODER-20260905-002), before any decomposition landed, and no later
commit on the RC touches them -- so the comparison is a real
before-versus-after, not a snapshot of the new behavior.

The guard was exercised: appending one line to the captured
`board-render.txt` and re-running `--compare` produces exit 1 and a unified
diff naming the seeded line. The harness does not re-capture over existing
output, so a seeded drift genuinely survives to the diff.

### Finding 10: the release-lifecycle synthetic simulation passes

**Category:** pass

The RC's diff touches `cm/release.sh` and the CM lib stages, so Step 6.8
applies. `python3 -m pytest tests/integration/test_cm_release_synthetic.py -q`
-> `15 passed in 27.55s`.

### Finding 11: syntax and compile clean across the diff

**Category:** pass

`bash -n` over every changed `*.sh` (0 failures) and `python3 -m py_compile`
over every changed `*.py` (0 failures).

### Finding 12: board-render golden is a thin surface for a 2.1k-line decomposition

**Category:** pass-with-caveat

`team/tests/fixtures/engine-baseline/board-render.txt` is three lines (one
column header, two queue rows). `dashboard/column-render.sh` was 2102 lines
before the split and is now three stages totalling 1948 lines
(`column_render_data.sh` 799, `column_render_cells.sh` 704,
`column_render_frame.sh` 445), so the byte-identical claim for the board-render
surface rests on a very narrow capture -- no wide frame, no priority cap, no
status-window path, no rich cell states. The `ship-summary` (25 lines, real
`cm/release.sh` ceremony through a synthetic repo) and `wake-log` (14 lines,
real wake substrate with the LLM stubbed) surfaces are substantially stronger.
The dashboard stages retain their own unit coverage
(`test_dashboard_column_render_priority_cap.py` and siblings pass), which is
why this is a caveat and not a gap. Worth deepening the board-render golden
when the harness is next touched.

## Lint Findings (non-blocking)

None. Both findings files were written and both are empty:
`${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-unit.json` (`run-unit-tests.sh`,
0 findings) and `findings-integration.json` (`run-integration-tests.sh`,
0 findings). All sixteen quality-class lints -- anti-pattern, skip-bug gate,
orphan-test, API-parity, upgrade no-temp-copy, ICD freshness, ICD compat,
CHANGELOG freshness, engine-size/dupe, env-bootstrap, help-presence,
comment-provenance, import-completeness, dashboard network/git gate,
dual-channel pair, census compliance -- reported OK on both runners.

## Deep-Verify Freshness

deep-verify: found=false — this project has never been deep-verified; no lane result exists, so nothing whatever is known about whether its three image flavors build, and the RC does not even carry `team/scripts/deep-verify.sh` to run one (Finding 2).

## Autonomous criterion

Manual interventions required: none.

1. Any task force-promoted from WAITING to BACKLOG manually? **NO.** Task
   `status.md` files record wake-script promotions ("Promoted from WAITING to
   BACKLOG by wake script: prerequisites satisfied"); no hand-edited state
   transitions found.
2. `release-state.md` edited outside the CM scripts? **NO.** The file holds
   the canonical four fields written by `cm-open-rc.sh` (`Active RC v2.9.20`,
   `RC Opened At 2026-09-05T05:48:17+00:00`, `RC Opened By Task
   CM-20260905-017-open-rc-2-9-20`, `Last Released v2.9.19`).
3. Scripts patched mid-build? **NO.** Every commit between `ai_main` and the
   RC tip is either a cycle feature commit or its `--no-ff` merge, in ticket
   order from 06:02Z to 14:08Z; nothing lands on the RC from outside the
   cycle's own tasks.
4. Queue files manually edited? **NO.** `tasks/queues/tester_backlog.md`
   carries the single expected `[A]` entry for this task.

One out-of-band observation that is not one of the four checks but belongs on
the record: the `ai_main` reflog shows the branch was *created* at the v2.4.0
commit (`branch: Created from HEAD`), an action taken outside
`cm-open-rc.sh` / `cm-release.sh` / `cm-cancel-rc.sh`. That action predates
this build and is the root cause of Finding 1. The build itself ran
autonomously and correctly on top of it.

## Caveats

- The suite-pass precondition fails on `UNIT_EXIT == 1`, but the cause is
  entirely Finding 3's environment-coupled fixtures. Every test that exercises
  this RC's own changes passes.
- The RC's own decomposition work is sound on its own terms. If Finding 1 is
  resolved by re-basing onto `last_run_ai_main`, the decomposition commits will
  not cherry-pick cleanly -- they were authored against files 400-900 lines
  smaller than their `ai_v2.9.19` counterparts -- so the current RC is best
  treated as a reference implementation to redo rather than as a patch series
  to replay.
- The live kanban bug ledger (`projects/pgai-agent-kanban/bugs/`) was empty at
  verification time, holding only `README.md` and `templates/`. Bug IDs were
  nonetheless claimed at 0161 and 0162 by `claim_next_bug_id`, so the ledger's
  counter state survives independently of the files. The empty ledger is what
  Finding 3's tests trip over.
- Pre-flight merge verification passed: every DONE task's feature branch has
  been deleted post-merge (normal lifecycle), so no Check 1 or Check 2 anomaly
  applies. Worktree HEAD matches `ai_rc/v2.9.20`, the tree is clean, and
  `git stash list` is empty.
- Release-state drift check: `## Last Released` is `v2.9.19`, well-formed, and
  the tag `ai_v2.9.19` exists; `v2.9.20 > v2.9.19` by semver. No drift in the
  file itself -- the drift is in the git branch topology, reported as Finding 1.
