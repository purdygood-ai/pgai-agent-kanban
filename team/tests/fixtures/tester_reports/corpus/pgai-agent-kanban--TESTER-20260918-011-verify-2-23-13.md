# Verification Report: ai_rc/v2.23.13

**Task:** TESTER-20260918-011-verify-2-23-13
**Requirements doc:** `projects/pgai-agent-kanban/requirements/v2.23.13-bugfix-bundle-20260918.md`
**RC commit verified:** `2ee4213dc8e49dd6216da550628881f4de4905ad` (detached HEAD == `ai_rc/v2.23.13`)
**Worktree:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260918-011-verify-2-23-13`
**Last released:** v2.23.12 (`pp_last_released_version`); `semver_gt v2.23.13 v2.23.12` → true

## Summary

v2.23.13 is a five-item BUG bundle: the status-bar defer posture (BUG-0317), the
three-headed stale-lock fix (BUG-0318: atomic release, contention-path reaping,
why.sh STUCK-LOCK correlation), the surviving provenance citation (BUG-0319),
the hygiene lint's unscanned second test tree (BUG-0320), and the agent-metrics
wall column (BUG-0321). Every bundled item was verified behaviorally, not just
structurally: all four BUG-0317 status-bar fixtures were exercised against a
throwaway kanban root, all three BUG-0318 heads were exercised by killing and
signalling real lock holders, the widened hygiene lint was re-pointed at a
freshly-seeded third test tree, and the wall-metrics self-heal was confirmed
against the live v2.23.12 rollup. **Every bundled item is addressed and works.**

Both gated runners nevertheless exited non-zero. `run-unit-tests.sh` exited 1
with 7 failures and `run-integration-tests.sh` exited 1 with 1 failure. The
failures fall into exactly two causes, both introduced by this RC's own work:
(1) `docs/api/icd.json` was not regenerated after CODER-005 edited the
`get_verdicts` docstring the ICD generator reads, which fails three tests plus
the `icd_freshness` quality lint in both suites; and (2) CODER-004's new stale-lock
reaper legitimately changed the contention verdict for a provenance-less lock,
which invalidates two OVERWATCH tests that hold locks with a bare `flock` and no
provenance record. A third cluster — CODER-004's own new reap fixtures —
passes in isolation and under the runner's environment but fails inside the full
suite, a test-isolation defect in newly shipped test code.

Re-running the unit suite over the identical commit produced a *different* set of
nine failures rather than the same seven, which established the third cluster as
unstable rather than merely wrong and surfaced one further, pre-existing flaky
check. `pytest-randomly` is active in both certifying runners with no seed pinned
or echoed, so the authoritative pass/fail signal is not reproducible run to run —
worth an operator's attention independently of this release.

Four follow-ups are filed: BUG-0322 (the stale ICD artifact), BUG-0323 (the two
OVERWATCH lock fixtures), BUG-0324 (the cwd- and order-coupled reap fixtures), and
BUG-0325 (the pre-existing flaky documentation-truth check).

Four CODER tickets merged after the ICD artifact went stale; three of them
recorded the failures and classified them "pre-existing" against a baseline
commit *inside this same RC* rather than against `ai_main`. That rationalization
is the reason a two-command fix reached TESTER as a red suite.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Suite Results (Step 5a — the only authoritative pass/fail signal)

| Runner | Command | Exit | Result |
|---|---|---|---|
| unit | `bash team/scripts/run-unit-tests.sh --coverage` | **1** | 7 failed, 4720 passed, 31 skipped (781 s) |
| unit (confirming re-run, same commit) | same | **1** | 9 failed, 4718 passed, 31 skipped (763 s) |
| integration | `bash team/scripts/run-integration-tests.sh --coverage` | **1** | 1 failed, 514 passed, 1 skipped (557 s) |

Captured output: `/tmp/pgai_kanban_tmp/tester/unit.log`, `/tmp/pgai_kanban_tmp/tester/integration.log`.
`UNIT_EXIT=1` and `INTEGRATION_EXIT=1`, so the Step 8 suite-pass precondition for
`PASS` is not met.

**The unit suite was run a second time over the identical commit to establish
which failures are deterministic** (`/tmp/pgai_kanban_tmp/tester/unit-rerun-t011.log`,
also exit 1). It produced **9** failures, not 7. Five are in both runs; the rest
differ. A third pass over `tests/unit/` alone (without the
`pgai_agent_kanban/api/tests/` argument the runner also passes) produced only 3.

| Test | gated run 1 | gated run 2 | `tests/unit/` only |
|---|---|---|---|
| `test_icd_generator.py::...::test_wrapper_output_matches_committed_artifact` | FAIL | FAIL | not collected |
| `test_review_cmds.py::TestIcdFreshnessGate::test_freshness_gate_exits_0` | FAIL | FAIL | FAIL |
| `test_lint_icd_freshness.py::...::test_fresh_artifact_passes` | FAIL | FAIL | FAIL |
| `test_overwatch_lock_halt_protocol.py::test_repo_lock_path_matches_for_project_other_than_pgai_kanban` | FAIL | FAIL | FAIL |
| `test_lock_contention_staleness_reap.py::test_dead_pid_holder_lock_is_reaped_and_narrated` | FAIL | FAIL | pass |
| `test_lock_contention_staleness_reap.py::test_provenance_less_contended_lock_is_reaped_with_atomicity_narration` | FAIL | FAIL | pass |
| `test_lock_contention_staleness_reap.py::test_live_pid_lock_older_than_threshold_is_reaped_on_age` | FAIL | FAIL | pass |
| `test_lock_contention_staleness_reap.py::test_kill9_holder_self_recovers_on_next_acquisition` | pass | FAIL | pass |
| `test_lint_doc_command_truth.py::test_real_tree_default_exempt_list_closes_the_named_process_internal_citations` | pass | FAIL | pass |

The ICD cluster and the OVERWATCH failure are deterministic and are the two
defects this RC actually introduced. The lock-reap cluster and the
doc-command-truth check are unstable: the tree did not change between the two
gated runs, only the execution order (`--randomly-seed=3961743329` then
`--randomly-seed=2607379335`) and which test tree was in the session.

Failing tests, by cause:

*ICD artifact staleness (3 unit):*
- `pgai_agent_kanban/api/tests/test_icd_generator.py::TestIcdWrapperCwdRegression::test_wrapper_output_matches_committed_artifact`
- `tests/unit/test_review_cmds.py::TestIcdFreshnessGate::test_freshness_gate_exits_0`
- `tests/unit/test_lint_icd_freshness.py::TestIcdFreshnessPositive::test_fresh_artifact_passes`

*Stale-lock reaper changed the contention verdict for a provenance-less holder (1 unit + 1 integration):*
- `tests/unit/test_overwatch_lock_halt_protocol.py::test_repo_lock_path_matches_for_project_other_than_pgai_kanban`
- `tests/integration/test_cross_cutting.py::TestPerRepoLockContention::test_overwatch_protocol_declines_when_repo_lock_is_held`

*Order-dependent failures in this RC's own new reap fixtures (3 unit):*
- `tests/unit/test_lock_contention_staleness_reap.py::test_dead_pid_holder_lock_is_reaped_and_narrated`
- `tests/unit/test_lock_contention_staleness_reap.py::test_provenance_less_contended_lock_is_reaped_with_atomicity_narration`
- `tests/unit/test_lock_contention_staleness_reap.py::test_live_pid_lock_older_than_threshold_is_reaped_on_age`

## Coverage (Step 5b — informational only, never part of the verdict)

Source: `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/unit.json` and `.../integration.json`
(`totals` object; field names verified against the generated files).

| Suite | line % (`percent_statements_covered`) | branch % (`percent_branches_covered`) | blended (`percent_covered`) |
|---|---|---|---|
| unit | 76.59 % | 63.14 % | 73.48 % |
| integration | 9.87 % | 7.97 % | 9.43 % |

## Findings

### Finding 1: BUG-0317 — the status bar renders the resolved defer posture

**Category:** pass

All four acceptance fixtures were exercised against a throwaway kanban root
(`/tmp/pgai_kanban_tmp/deferfix-SAFyEb`), never the live install:

| Fixture | Rendered status line (NO_COLOR) |
|---|---|
| campaign file `until=v2.24.0` + 2 deferred lows | `... \| BUGS DEFERRED 2 (<=low) until v2.24.0 \| ...` |
| cfg-only posture, no campaign file | `... \| BUGS DEFERRED 2 (<=low) \| ...` |
| seeded malformed campaign file | `... \| DEFER CONFIG UNREADABLE: <path> \| ...` |
| no deferral in force | no segment |

`why.sh --project demo --details` reported `deferred_bugs=true (campaign file)`,
`deferred_bug_level=low (campaign file)`, `until=v2.24.0 (campaign file: <path>)`
for the same posture, so the bar and the resolver agree in all four states. The
defining never-lie failure fixture the bug names — a present, well-formed
campaign file with zero bar indication — does **not** reproduce.

### Finding 2: BUG-0317 was addressed by documentation, not code

**Category:** pass-with-caveat

`CODER-20260918-002` produced exactly one commit, `48ade34`, touching only
`docs/DASHBOARD-PANES.md`: it added the BUGS DEFERRED segment to the dashboard
tour's segment list and rich-mode glyph legend. The rendering itself already
existed on `ai_main` — `git log ai_main..ai_rc/v2.23.13 -- team/scripts/dashboard/`
is empty. Finding 1 confirms the rendering works, so the caveat is only that the
operator's originally-reported symptom could not be reproduced and its root cause
was therefore never established; the most likely explanation is that the operator
observed an installed copy predating the segment. Two cosmetic deviations from
the bug's own Expected text, both pre-existing and both now documented: the glyph
is `✋` rather than `💤`, and the level comparator renders `<=` rather than `≤`.

### Finding 3: BUG-0318 head 1 — lock release is atomic on every exit path

**Category:** pass

Exercised directly against `team/scripts/lib/lock_provenance.sh` in a throwaway
sandbox, with a real holder process in each case:

- Holder acquires and exits cleanly **without** calling `pgai_lock_release`
  (the shape of the HALT-detected "exiting cleanly" path the bug's addendum
  names as prime suspect) → the `pgai_lock_release_all_held` trap fires,
  narrates, and both the lock file and its provenance are gone.
- Holder receives `SIGTERM` while holding → same result, file gone.
- Holder receives `SIGKILL` while holding → the file survives with its
  provenance intact (unavoidable; a killed process runs no handler), and the
  next waiter reaps it (Finding 4).

### Finding 4: BUG-0318 head 2 — the contention-path reaper reaps and proceeds

**Category:** pass

After `kill -9` of a lock holder whose `sleep` child still held the inherited
flock, the next `pgai_lock_acquire` emitted
`error: lock.provenance.stale_reaped.dead_pid` with the holder pid and age, then
**returned 0** — the fleet self-recovers within one firing with no operator
action, which is the acceptance criterion. The same was confirmed for a
synthetic dead-pid provenance record. A live, well-provenanced, recent holder
still falls through to the unchanged `lock.provenance.contended` refusal.

### Finding 5: BUG-0318 head 3 — why.sh reports STUCK-LOCK, never IDLE/none-needed

**Category:** pass

With one `BACKLOG` task queued and a provenance-less lock backdated three hours
at the `pp_repo_lock_path` location, `why.sh --project demo --details` produced:

```
demo: STUCK-LOCK — cause: 1 task(s) are queued but ⚠️ VIOLATION: lock .../repo-wake-demo.lock
(age 3h 0m) carries no provenance record at all ...; remedy: release it:
team/scripts/release-locks.sh --release repo-wake-demo ...
```

and the `== locks ==` pane carried the `⚠️ VIOLATION` marker rather than a shrug.
The pre-fix verdict for this state was `IDLE / "remedy: none needed"`.

### Finding 6: BUG-0319 — the surviving provenance citation is gone

**Category:** pass

`grep -rn "CODER-009" --include='*.py' team/pgai_agent_kanban/` returns nothing.
`test_cli_rest_agent_metrics_parity.py:100` now reads
``# Per-task wall data (the `wall.by_agent` block): coder averages`` and the four
following comment lines preserving the deliberately-different-figures point are
unchanged.

### Finding 7: BUG-0320 — the hygiene lint now reads the second test tree

**Category:** pass

`python3 team/scripts/lint_hygiene.py --classes provenance -v` now lists 20 files
under `team/pgai_agent_kanban/api/tests/`, and the tree-wide run reports
`OK — 0 findings across 8 scan director(y/ies)` with that directory genuinely
walked. The general form was chosen over the narrow one: seeding a *third* test
tree at `team/pgai_agent_kanban/lib/tests/` in a scratch copy of the tree, with a
`CODER-20260101-001` citation in a comment, produced three scan lines for that
directory and reported the seeded citation — so a test tree added later is routed
automatically rather than silently dropped. `team/tests/unit/test_lint_hygiene.py`
gained the two regression cases the bug asked for
(`test_tests_dir_nested_under_code_surface_is_routed_not_dropped` and
`test_tests_dir_scanned_directly_as_code_target_is_not_routed`), both passing.

### Finding 8: BUG-0321 — the wall-metrics self-heal is real and now documented

**Category:** pass

The bug's own step-4 verification now succeeds against the live install:
`projects/pgai-agent-kanban/metrics/rc/v2.23.12.json` carries a `wall` block
(`cm`, `coder`, `pm`, `tester`), where every older rollup carries `None`. The
live `scripts/dashboard/agent-metrics.sh --window 10` column that showed `--` for
every agent now shows `cm 2`, `coder 30`, `pm 5`, `tester 30` (`writer` remains
`--`: no writer task in the one wall-bearing RC, which is the documented
placeholder meaning). The RC additionally documents the one-release lag and the
no-backfill decision in the module docstrings of `metrics_aggregator.py` (write
side) and `agent_metrics.py` (read side), so the condition under which the column
fills is stated rather than discovered.

### Finding 9: `docs/api/icd.json` was not regenerated after the verdict docstring changed

**Category:** bug
**Fix Effort:** small (1 CODER task)
**Systemic Risk:** medium (recurs on any RC that edits a docstring the ICD generator embeds)
**Filed As:** BUG-0322 (Path C-A — filed as BUG: code defect / regression / test failure)

Commit `f4e9e46` added `STUCK-LOCK` to `get_verdicts()`'s documented verdict
vocabulary in `team/pgai_agent_kanban/api/reads.py` and changed "four verdict
words" to "five". The ICD generator embeds that docstring verbatim as
`paths./verdicts.get.description`, and `docs/api/icd.json` is not in
`git diff --name-only ai_main..ai_rc/v2.23.13`. Regenerating to a temp path with
`bash team/scripts/generate-icd.sh --output <tmp>` and diffing against the
committed artifact yields **one** changed line — that description string.

Consequences: three unit tests fail, and the `icd_freshness` quality lint fires
in the pre-flight of BOTH runners, so `run-unit-tests.sh` and
`run-integration-tests.sh` each exit 1. Both exited 0 on v2.23.12, so this is a
regression this RC introduced, and the remedy is `bash team/scripts/generate-icd.sh`
plus a commit of `docs/api/icd.json`.

The RC's own docstring edits are correct and must stay; only the generated
artifact needs to catch up. `SOP-core.md`'s Documentation Discipline Rule already
binds this case — the generated artifact is the matching documentation artifact
for a docstring the generator reads.

### Finding 10: two OVERWATCH lock-contention tests hold their lock without provenance

**Category:** stale-assertion
**Fix Effort:** small (1 CODER task)
**Systemic Risk:** low (two fixtures, one holder-script shape, already fixed once in a sibling test)
**Filed As:** BUG-0323 (Path C-A)

**Step 7 evidence.**

- *Test file and line:* `team/tests/unit/test_overwatch_lock_halt_protocol.py:157`
  (`test_repo_lock_path_matches_for_project_other_than_pgai_kanban`) and
  `team/tests/integration/test_cross_cutting.py`
  (`TestPerRepoLockContention::test_overwatch_protocol_declines_when_repo_lock_is_held`).
- *Failing assertion, expected vs actual:* expected `'EXIT_CODE=4' in stdout`
  (sentinel 4 = contended, OVERWATCH declines); actual stdout
  `'noop ran\nEXIT_CODE=0\n'` — OVERWATCH acquired the lock and ran its repair
  function.
- *Production file and line where the behavior changed:*
  `team/scripts/lib/lock_provenance.sh`, the staleness-reap branch inside
  `pgai_lock_acquire` (around lines 525-555), which classifies a lock with no
  provenance record as stale, narrates
  `lock.provenance.stale_reaped.no_provenance`, removes it, and retries once.
  That narration appears verbatim in both failures' stderr.
- *RC commit / task ID that changed it:* `3bdddcc`,
  `CODER-20260918-004-stale-lock-reaping`.
- *Why the new value is correct per the requirements doc:* `BUG-0318` head 2
  names "no provenance at all" as one of the three independently-verifiable
  reap conditions, and its acceptance list includes "Fixture: provenance-less
  lock → reaped + ⚠️ narration naming the atomicity violation." Both fixtures
  hold their lock with a bare `exec 9>file; flock -x 9` and never write a
  provenance record, which is precisely that shape. No production holder has it:
  `team/scripts/wake/harness.sh:437`, `team/scripts/lib/wake_driver.sh` and
  `team/scripts/lib/overwatch_protocol.sh:427` all take the repo lock through
  `pgai_lock_acquire`, which always writes `holder`/`pid`/`op`/`task`. I confirmed
  separately that a live, well-provenanced holder still produces
  `lock.provenance.contended` and return 1, unchanged.
- *Recommendation:* **the tests, not the production code, need to change.** Give
  each fixture's holder a provenance record naming its own live pid — the same
  repair `CODER-20260918-004` already applied to
  `team/tests/unit/test_lock_provenance.py`'s contended-acquisition fixture in
  the same commit. Changing `lock_provenance.sh` to make these tests pass would
  undo `BUG-0318` head 2.

Caveat worth CODER's attention: these two are the only coverage that OVERWATCH
declines while an agent is mid-flight, so that protection is unverified until
they are repaired.

### Finding 11: BUG-0318 head 1 and head 3 landed with no acceptance gap; head 2's fixtures did not

**Category:** pass

Recorded for completeness alongside Findings 3-5: every acceptance bullet in
`BUG-0318` was exercised and met. The two fixtures the addendum added — a HALT
drop mid-chain while the lock is held, and `SIGTERM`/`SIGKILL` of the holder —
were both reproduced against the shipped library, and the fix does live in one
place (`pgai_lock_release_all_held`, installed as either the wake chain's
existing `CLEANUP_ON_EXIT_PRE_HOOK` or this library's own EXIT/SIGTERM/SIGINT
trap) rather than at each exit site, which is what the addendum asked for.

### Finding 12: three new reap fixtures are cwd- and order-coupled, and fail inside the gated runner

**Category:** bug
**Fix Effort:** small (1 CODER task for the fixtures; the runner-determinism question is a separate decision)
**Systemic Risk:** medium (the certifying suite's verdict is currently a property of the run, not of the tree)
**Filed As:** BUG-0324 (Path C-A)

`team/tests/unit/test_lock_contention_staleness_reap.py` — new in this RC — fails
inside `run-unit-tests.sh` and passes everywhere else I could put it: alone
(`8 passed`), alone three times while the full suite loaded the machine, alone
under the runner's own environment, and inside a full
`python3 -m pytest tests/unit/` pass that produced only the ICD and OVERWATCH
failures. Which of its fixtures fail is not stable either — three in the first
gated run (`test_dead_pid_holder_lock_is_reaped_and_narrated`,
`test_provenance_less_contended_lock_is_reaped_with_atomicity_narration`,
`test_live_pid_lock_older_than_threshold_is_reaped_on_age`) and those three plus
`test_kill9_holder_self_recovers_on_next_acquisition` in the confirming re-run
over the identical commit.

Two concrete defects were isolated, and one question was left open.

1. **The file is cwd-coupled — proven.** It resolves the library under test as
   the relative string `"scripts/lib/lock_provenance.sh"`. Its *holder*
   subprocesses are launched with `cwd=str(_repo_root())`, computed from the test
   file's own location, but the *acquirer* goes through
   `run_bash()` in `team/tests/unit/shell_harness.py`, which passes no `cwd` to
   `subprocess.run` and therefore inherits the interpreter's. Invoking pytest on
   this one file from any directory other than `<dev tree>/team` produces
   `7 failed, 1 passed`, every failure reading
   `bash: line 2: scripts/lib/lock_provenance.sh: No such file or directory`.
   `docs/test-authoring-guidelines.md` (around line 344) states this rule and
   predicts this exact symptom: "green from the repo root, red from `team/` or
   from a scratch directory, with a failure that accuses the tree."
2. **The gated suites run in a randomised order — proven.** `pytest-randomly`
   4.1.0 is installed and active: the run's header reads
   `Using --randomly-seed=3961743329` and the per-file progress lines are
   non-alphabetical. The estate already knows about this plugin and neutralises
   it where order must be stated (`team/scripts/verify-test-order-independence.sh`
   passes `-p no:randomly`, and `test_order_independence_plugin_neutralization.py`
   keeps that flag in place), but neither gated runner passes it. So the
   authoritative pass/fail signal executes in a different order on every
   invocation, which is why this file is green in one full run and red in
   another with no change to the tree.
3. **The specific failure path was not isolated (open).** In the gated run the
   `source` clearly succeeded — the reaper's own narration is in the captured
   stderr — yet `pgai_lock_acquire` returned 1 without emitting the
   `lock.provenance.contended` contract that is its only other non-zero
   contention-branch exit. Under the fixture's `set -euo pipefail`, an abort at a
   command substitution inside the bounded reap retry would produce exactly that
   shape. Which one it is is in the bug for CODER to trace.

**The shipped library is not implicated, and that was checked rather than
assumed.** Every behavior these three fixtures cover was exercised directly
against `team/scripts/lib/lock_provenance.sh` outside pytest, and all of it is
correct — see Findings 3 and 4. This is a defect in test code; it is rated `bug`
rather than `stale-assertion` because no literal this RC changed is being
asserted, and it is rated high in its own filing because the gated runner is the
only authoritative pass/fail signal this framework has and it is red.

### Finding 13: the doc-command-truth help check reports a false failure inside the full suite

**Category:** bug (pre-existing — this RC's diff does not touch the surface)
**Fix Effort:** small (1 CODER task)
**Systemic Risk:** medium (same class as Finding 12: the certifying suite's verdict is not stable)
**Filed As:** BUG-0325 (Path C-A)

`tests/unit/test_lint_doc_command_truth.py::test_real_tree_default_exempt_list_closes_the_named_process_internal_citations`
failed in the confirming re-run with
`HELP FAILS: team/scripts/dashboard/metadata.sh in: docs/OPERATIONS.md:1457`, and
passed in the first gated run over the identical commit. The tree is not at fault
and that was checked three ways: `bash team/scripts/dashboard/metadata.sh --help`
exits 0; `python3 team/scripts/lint_doc_command_truth.py` reports
`OK — 0 findings` across 653 citations in 13 documents; and
`python3 -m pytest tests/unit/test_lint_doc_command_truth.py` gives `26 passed`.

The lint probes each cited command by invoking its `--help` in a subprocess, which
makes the check sensitive to conditions the full suite creates — a probe slow
enough under load to hit a timeout, or an environment the session left behind.
`git diff --name-only ai_main..ai_rc/v2.23.13` touches none of
`lint_doc_command_truth.py`, `dashboard/metadata.sh`, `docs/OPERATIONS.md`, or
`team/config/doc-command-truth-help-exempt.txt`, so this is pre-existing rather
than a regression this RC introduced. It is filed on its own rather than folded
into BUG-0324 because it is a different test with a different cause; the two share
only the property that the suite is not reproducible.

### Finding 14: structural, constraint and standards checks

**Category:** pass

- **Acceptance criterion "All Python files compile cleanly"** — `python3 -m py_compile`
  on every `.py` in `git diff --name-only ai_main..ai_rc/v2.23.13`: clean.
- **Acceptance criterion "All bash files pass syntax check"** — `bash -n` on every
  `.sh` in the same diff: clean.
- **Documentation Discipline** — the RC adds one tunable,
  `[locks] stale_lock_reap_after_wake_periods`. It is documented in
  `kanban.cfg_example` (+37 lines) and `project.cfg_example` (+23 lines), carries
  a `config_loader.sh` Key Registry entry, and names its in-code fallback
  (`pp_stale_lock_reap_after_wake_periods()`, default 3) so removing the line
  changes nothing.
- **New gate authoring contract** — the new refusal site declares its census
  class in a comment (`# BLOCK-WORTHY: invariant-by-design ...`), emits the
  dual-channel `error`/`message`/`remedy` contract, and carries
  `# CENSUS-ANCHOR: lock-provenance-contended-01` with a matching
  `docs/blocking-site-census.md` Locator Index entry at line 1092.
  `lint_gate_census_compliance.py` reports `[]`; the dual-channel-pair lint
  findings files for both suites are `[]`.
- **Strict mode preserved** — `set -euo pipefail` present in every changed
  executable script (`why.sh:110`, `release-locks.sh:80`, `verdict-report.sh`);
  the changed files under `team/scripts/lib/` are sourced libraries, which
  correctly do not set it.
- **No secrets** — grepping the diff for added lines assigning a literal to a
  `password`/`api_key`/`secret`/`token` name: no hits.
- **Step 6.13 skip-cites-a-real-bug** — `bash team/scripts/lint_skip_bug_gate.sh`
  exits 0: `0 missing files`, no placeholder shapes.
- **Step 6.8 synthetic release simulation** — applicable (the diff touches
  `team/scripts/lib/project_paths.sh`). `tests/integration/test_cm_release_synthetic.py`
  ran inside the gated integration suite: 17 passed.
- **Step 6.1 release-state sanity** — all four fields well-formed;
  `## Active RC` is `v2.23.13`, `## Last Released` is `v2.23.12`, and
  `semver_gt v2.23.13 v2.23.12` (via `team/scripts/lib/semver.sh`, never an
  inline compare) is true.
- **Step 6.10 prerequisite graph** — acyclic; every referenced task ID exists;
  bookends correct (`CM-20260918-010-open-rc-2-23-13` at the head,
  `TESTER-20260918-011-verify-2-23-13` then `CM-20260918-013-release-2-23-13`
  at the tail); sequence numbers non-colliding (012 is unused, not duplicated).

### Finding 15: spot-checks outside the spec (Step 6.11)

**Category:** pass

- **State drift** — none. `release-state.md`'s `## Active RC` value matches the
  branch actually under verification, and the worktree HEAD equals
  `git rev-parse ai_rc/v2.23.13`.
- **Untested new mechanisms** — none found. Each new mechanism ships with
  coverage: the reaper with `test_lock_contention_staleness_reap.py` (8 fixtures),
  the STUCK-LOCK verdict with `test_why_stuck_lock_verdict.py` (419 lines, all
  passing), atomic release with `test_lock_release_atomicity.py` (253 lines, all
  passing), the config tier with 5 new cases in `test_project_paths_bash.py`, and
  the lint routing with 2 new cases in `test_lint_hygiene.py`.
- **Regressions of prior gaps** — the two gaps this RC inherits from the previous
  TESTER round (BUG-0319 and BUG-0320) are both closed and were re-verified from
  scratch rather than taken on the ticket's word (Findings 6 and 7).
- One leftover branch, `ai_feature/CODER-20260917-020-agent-metrics-avg-wall`,
  still exists locally. Its task is `WONT-DO`, so Step 2's merge checks (which
  apply to `DONE` tasks) do not cover it and it is not an anomaly — recorded as an
  observation, not a finding.

## Lint Findings (non-blocking)

- `icd_freshness` — `docs/api/icd.json` is stale: the committed artifact does not
  match a fresh regeneration from the current codebase. Fired in the pre-flight of
  **both** runners (`${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-unit.json` and
  `.../findings-integration.json`, one finding each, `severity: quality`).
  Cites **BUG-0322**, filed for the same root cause as a Step 7 `bug` (Finding 9)
  — no second bug was filed for the lint instance.

No other lint wrote a finding: `test_anti_patterns`, `skip_cites_real_bug`,
`orphan_tests`, `hygiene` (all three classes), `gate_census_compliance`,
`dual_channel_pair`, `governance_byte_budget` and the breakage-class and
Dockerfile static gates all reported OK or `[]` for both suites.

Per Step 5c this section moves nothing on its own. The recommendation is not
`PASS` because of Findings 9, 10 and 12 — real Step 7 defects on the normal tree —
not because of this section.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=11d freshness=stale — Both freshness conditions fail (the record is 11 days old against a 7-day window, and v2.9.20 is not the v2.23.13 under verification), and the last recorded verdict was red for the docker/podman `claude-abliterated` flavor and the podman `rocky9` flavor, already filed and since closed as BUG-0167-cold-build-claude-abliterated-failure (`## Status: done`) — nothing is re-filed here, and nothing is known about whether this tree's three Dockerfiles build today.

## Autonomous criterion

**Manual interventions required: none.** All four Step 6.12 checks answer NO.

1. *Were any tasks force-promoted from WAITING to BACKLOG by hand, bypassing the
   wake script's prerequisite evaluation?* **NO.** Every promotion in the debug
   logs carries the wake script's own line ("Promoted from WAITING to BACKLOG by
   wake script: prerequisites satisfied"), and no `stuck`, `zero_progress`,
   `reset to BACKLOG` or `force` event appears in any project log for 2026-09-18.
   Every one of the eight CODER tasks shows the full wake bracket
   (`wake: dispatching` → agent progress lines → `wake: task finished
   (final_state=DONE agent_exit=0)`).
2. *Was `release-state.md` edited outside `cm-open-rc.sh` / `cm-release.sh` /
   `cm-cancel-rc.sh`?* **NO.** Its mtime is 06:00, matching
   `CM-20260918-010-open-rc-2-23-13`'s open, and its `## RC Opened By Task` names
   that task. No later write.
3. *Were any scripts patched mid-build — a commit touching `team/scripts/`,
   `subagents/` or other tooling while the RC was in flight?* **NO.** Eight
   commits on the RC touch `team/scripts/`, and all eight are the assigned
   deliverables of the RC's own CODER tickets, each on its own feature branch and
   merged `--no-ff`. No out-of-band commit exists.
4. *Were any queue files edited by hand?* **NO.** Queue mtimes track wake
   transitions (`coder_backlog.md` and `tester_backlog.md` at 08:11, the TESTER
   promotion; `cm_backlog.md` at 06:01; `pm_backlog.md` at 05:59) and every entry
   carries a marker consistent with its task's own `## State`.

One process observation that is not one of the four checks, and therefore not a
YES, but which explains how a red suite reached verification: three of the four
tickets that merged after commit `f4e9e46` recorded the ICD failures in their own
status logs and classified them "pre-existing" against a baseline commit inside
this same RC (`a6a935c`) rather than against `ai_main`. Measured against the
previous release they are this RC's own regression. It is recorded in BUG-0322's
Actual and Hypothesis sections so the next iteration can decide whether the
comparison baseline deserves to be stated in `roles/CODER.md`.

## Caveats

- **`PASS` was not available regardless of judgment.** Step 8's suite-pass
  precondition requires both gated runners at exit 0; both exited 1. The
  recommendation is `SHIP-WITH-CONCERNS` rather than
  `SHIP-WITH-SERIOUS-CONCERNS` because none of the three defects affects what the
  release *does*: all five bundled bugs are fixed and were verified behaviorally,
  no runtime path is broken, `cm-release.sh` does not invoke the gated suites so
  the release pipeline is not blocked, and every defect has a small, named
  remedy. The honest reading is a working release carrying a red certification
  signal, not an unusable one.
- **Test fidelity.** Asked of the RC's new tests, the answer is good on the
  production side and poor on the fixture side. The reaper's own fixtures use real
  `flock` holders, real `kill -9`, and real subprocesses rather than mocks — the
  breaking condition is genuinely exercised. Two fixture shapes are wrong in the
  other direction: the OVERWATCH pair (Finding 10) holds a lock in a shape no
  production caller produces, and the reap trio (Finding 12) is coupled to the
  invocation rather than the tree.
- **The certifying suite is not reproducible, and that is the most important
  thing in this report that is not about v2.23.13.** Two runs of
  `run-unit-tests.sh` over the identical commit returned different failure sets
  (7 and 9), and the difference was not noise around one test: a fourth reap
  fixture and a documentation-truth check joined on the second pass.
  `pytest-randomly` 4.1.0 is active in both runners, neither passes
  `-p no:randomly`, and neither pins or echoes the seed — the two runs used
  3961743329 and 2607379335, discoverable only by reading the captured output.
  The estate already neutralises the plugin where order must be stated
  (`team/scripts/verify-test-order-independence.sh`, guarded by
  `test_order_independence_plugin_neutralization.py`); the certifying runners do
  not. This is pre-existing and outside this RC's spec, so it is recorded inside
  BUG-0324 and BUG-0325 rather than filed as a third item — but a verifier who
  runs these suites once and reports the result is reporting one sample, and an
  operator comparing two reports should not read a difference between them as a
  change in the tree.
- **No git work was performed.** `roles/TESTER.md`'s git workflow would normally
  commit these artifacts on a feature branch off the local RC, but this task's
  README sets `## Feature Branch: none` and the wake prompt explicitly forbids
  creating one; the worktree is detached at the RC commit and read-only for this
  run. The deliverables live in the task folder and in
  `projects/pgai-agent-kanban/bugs/`, which is where every consumer of them looks.
  The verification worktree is unmodified.
- **Sandbox discipline.** Every behavioral exercise in this report ran against a
  throwaway root under `$PGAI_AGENT_KANBAN_TEMP_DIR` with `PGAI_TASKS_DIR` and
  `PGAI_PROJECT_ROOT` cleared, except two deliberately read-only reads of live
  state used as production-shape evidence for Finding 8
  (`scripts/dashboard/agent-metrics.sh --window 10` and a `json.load` of
  `projects/pgai-agent-kanban/metrics/rc/*.json`). Nothing was written outside the
  task folder, the `bugs/` filings, and the temp root. The verification worktree
  was verified clean (`git status --porcelain` empty) after every suite run and
  after the ICD regeneration probe, which wrote only to `--output`.
- **`## Affects` on the three filings** is recorded as `v2.23.13` for all three:
  each defect is present in the tree about to ship and in no earlier release.

