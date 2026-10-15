# Verification Report: rc/v2.24.5

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Summary

Fresh verification of release candidate `ai_rc/v2.24.5` for project `pgai-agent-kanban`
against `requirements/v2.24.5-bugfix-bundle-20260919.md` — a four-item BUG bundle
(BUG-0338 refusal-surface test pollution, BUG-0339 operator duplicate cmdline pane,
BUG-0340 event-band render budget, BUG-0341 env-coupled debug-log routing test)
implemented by CODER tasks 030 through 034. Both gated runners exited 0
(`run-unit-tests.sh`: 4899 passed / 31 skipped; `run-integration-tests.sh`: 519 passed /
1 skipped), and the unit run was deliberately invoked with a `/projects/`-containing
`PGAI_AGENT_KANBAN_TEMP_DIR` — the exact condition that turned it red on v2.24.3 — so
BUG-0341's fix is verified by the runner itself rather than by inspection. Three of the four
bundled items are fully met and were exercised behaviorally, including the live install's
refusal ledger surviving a 4899-test run byte-identical (BUG-0338's hardest criterion, which
CODER could not verify directly), a live tmux session reporting exactly ten operator panes with
one shell, and a new census cross-check that fires and names `operator` when an extra pane is
seeded. The fourth, BUG-0340, is not met: the event band still renders in 11.3-12.0 s against
the operator's real logs versus its 5 s pane-refresh bound, because the unit test meant to hold
that line feeds the renderer 7500 byte-identical lines (1.9 s) where production carries 150
distinct texts (11.5 s). That gap and a two-instance quality-class lint class are filed as
follow-ups; nothing found makes this release unusable.

**Findings at a glance:** 1 gap (medium, filed BUG-0344), 6 pass, 1 pass-with-caveat, and one
quality-class lint class filed as BUG-0345. Zero bugs, zero stale assertions, zero
infrastructure failures, zero manual interventions.

## Pre-flight

- Worktree: `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260919-036-verify-2-24-5`
- `git rev-parse HEAD` = `7d4badebc0ebd9d07d16afad7e1ad4f833ea5bca` = `git rev-parse ai_rc/v2.24.5`. PASS.
- `git status --porcelain` empty. PASS.
- `git stash list` empty. PASS.
- Step 2 branch scan across all task folders: every `DONE` task whose README names a feature
  branch has that branch already deleted locally (the normal post-merge shape); zero
  non-ancestor branches, zero zero-commit ancestors. PASS.
- Step 6.1 `release-state.md`: `## Active RC` = `v2.24.5`, `## RC Opened At` =
  `2026-09-19T21:38:29+00:00`, `## RC Opened By Task` = `CM-20260919-035-open-rc-2-24-5`,
  `## Last Released` = `v2.24.4` (well-formed). `semver_gt v2.24.5 v2.24.4` (shared helper
  `team/scripts/lib/semver.sh`) returns true against `pp_last_released_version` = `v2.24.4`. PASS.

## What was shipped

`git diff ai_main..ai_rc/v2.24.5` touches 17 files, 1316 insertions / 72 deletions:

| File | Ticket |
|---|---|
| `team/pgai_agent_kanban/lib/refusal_ledger.py`, `team/scripts/lib/refusal_ledger.sh`, `team/tests/conftest.py`, `team/tests/unit/test_refusal_ledger.py`, `team/tests/unit/test_error_contract_bash.py` | CODER-030 (BUG-0338) |
| `team/scripts/lib/why_details.sh`, `team/tests/unit/test_why_details_pane.py` | CODER-031 (BUG-0338) |
| `team/tests/unit/test_dashboard_create_sh_pane_counts_match_census.py` | CODER-032 (BUG-0339) |
| `team/scripts/dashboard/event-band.sh`, `team/scripts/dashboard/lib/event_signal_patterns.sh`, `team/tests/unit/test_event_band_renderer.py` | CODER-033 (BUG-0340) |
| `team/tests/unit/test_debug_log_routing.py`, `team/scripts/lint_test_anti_patterns.py`, `docs/test-authoring-guidelines.md`, `team/tests/fixtures/fixture_abs_path_*.py`, `team/tests/unit/test_lint_abs_path_substring_assertion.py` | CODER-034 (BUG-0341) |

## Gated suites (Step 5a)

- `bash team/scripts/run-unit-tests.sh --coverage` → **`UNIT_EXIT=0`**, `4899 passed, 31 skipped in 787.46s`.
  Run deliberately with `PGAI_AGENT_KANBAN_TEMP_DIR=/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester-036`
  — a path containing `/projects/`, the exact condition BUG-0341 says turns the runner red. Output:
  `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester-036/tester/unit.log`.
- `bash team/scripts/run-integration-tests.sh --coverage` → **`INTEGRATION_EXIT=0`**,
  `519 passed, 1 skipped in 562.41s`. Output:
  `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester-036/tester/integration.log`.

Both gated runners exited 0. That is the PASS precondition, and it is satisfied — the evidence
cited is the runners' exit codes, not a pytest "N passed" line. No direct-pytest result is used
anywhere in this report as pass evidence.

## Coverage (Step 5b — informational only, never enters the verdict)

Read from the runner's own JSON at `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/unit.json`.

Field names were verified against the generated JSON before extraction (`totals` carries
`percent_statements_covered`, `percent_branches_covered`, and the blended `percent_covered`).

- **unit** — line (statement) coverage **76.36 %**; branch coverage **63.15 %**. (The `term`
  report's `^TOTAL` `Cover` column reads `73%` — that is the *blended* statement-plus-branch
  figure, not line coverage.)
- **integration** — line (statement) coverage **11.48 %**; branch coverage **9.21 %**
  (blended 10.95 %). Low by construction: the integration suite drives shell entry points as
  subprocesses, so almost no Python is imported in the measured process. Coverage never enters
  the verdict.

## Findings

### Finding 1: BUG-0340's render-budget criterion is still not met against production-shaped input

**Category:** gap
**Fix Effort:** medium (3-5 CODER tasks — the remaining cost is bash per-line work spread across
several stages; a real fix likely moves the surviving-line loop out of bash entirely)
**Systemic Risk:** medium (second RC attacking the same criterion; the class of fix — tuning the
bash loop — has now twice failed to reach the bound, which is a signal about the approach, not
just this ticket)
**Filed As:** BUG-0344 (Path C-A — autonomous follow-up filing, non-blocking; filed as BUG)

**Criterion.** `CODER-20260919-033`'s first acceptance criterion, restating
`BUG-0340-event-band-render-exceeds-refresh-budget.md`'s `## Expected`:

> One render of `team/scripts/dashboard/event-band.sh` against a fixture root whose five per-agent
> cron logs carry a production-shaped historical body and at least 7000 today-dated signal lines
> completes in under 5 seconds, measured three consecutive times with every measurement under the bound.

**Expected:** under 5.00 s (the pane's own `_DEFAULT_REFRESH`), three consecutive runs.

**Actual:** **11.27 s / 11.29 s / 12.01 s** (and, in an earlier triple, 11.71 / 11.78 / 11.33 s).
Every measurement is more than twice the bound.

**Method.** BUG-0340's own reproduction, unchanged: a throwaway kanban root whose five per-agent
cron logs are symlinks to the operator's live logs
(`$HOME/pgai_agent_kanban/logs/cron-{pm,coder,writer,tester,cm}.log`, 825.8 MB total),
run from the RC worktree at HEAD `7d4bade`, `PGAI_DASHBOARD_EVENT_BAND_READ_BOUND_LINES` at its
shipped default of 20000. 7628 candidate lines survive the read-bound prefilter; 7622 reach the
merge file.

**The RC did change the code, and the change is measurable but far too small.** Same fixture,
same host, back-to-back:

| Version | Render time (3 runs) |
|---|---|
| `ai_main` (pre-fix) | 12.73 s / 12.40 s / 13.01 s |
| `ai_rc/v2.24.5` | 12.01 s / 11.27 s / 11.29 s |

An ~8-12 % improvement against a criterion that needs a 2.4x one.

**Where the time goes.** Instrumented copy of the RC script (run outside the worktree, the tree
never modified): the per-line loop is 10.5-10.9 s of the ~11.5 s total; the two post-loop
`sort`/fold passes are 0.21 s and 0.19 s; the `tail -n 20000 | grep -E` prefilter across all five
825 MB logs is 0.10 s. Replacing the loop body with `:` drops the loop to 0.25 s, so the entire
cost is the per-line work — ~1.38 ms/line across 7622 lines. The three fixes this RC landed
(pattern reordering, held merge fd, `mapfile` from a seekable file) each remove roughly 1 s
when individually disabled; none of them addresses the bulk.

**Why the green unit test does not hold this line — the fidelity defect behind the gap.**
`team/tests/unit/test_event_band_renderer.py::test_large_pre_today_body_renders_correctly_within_time_bound`
now carries 7500 today-dated signal lines (up from 2), which satisfies the criterion's *count*.
But `_build_repetition_fixture(7500, distinct_event_count=2, stagger_seconds=1)` makes those lines
**byte-identical sweep-census lines**, and that shape dodges both of the RC's own optimizations'
failure modes:

- every line matches `EVENT_SIGNAL_PATTERNS[0]` (`sweep-census:.*skipped`), so the new inline
  fast path hits on the *first* regex and the 13-pattern classifier never runs;
- every line's `$rest` is identical, so `COLLAPSE_KEY_CACHE` hits after the first line and
  `dashboard_event_signal_collapse_key` forks once instead of per distinct text.

Production is the opposite: of 7628 surviving lines there are **150 distinct `$rest` texts**, and
the three most frequent (`remedy: If the holder is still legitimately working...` x668,
`error: lock.provenance.contended` x668, `wake(writer): ... per-project HALT present, skipping`
x609) match **none** of the fast path.

Measured directly, RC code, same 7625-line volume, only the line *diversity* differing:

| Fixture shape | Render |
|---|---|
| unit test's shape (byte-identical sweep-census lines) | **1.92 s / 2.15 s** |
| production shape (live cron logs) | **11.27-12.01 s** |

A 5-6x gap produced purely by fixture composition. This is the same failure BUG-0340 named against
the previous fixture ("the production condition that actually costs the time is mocked away"),
reproduced one iteration later in a new form: the line *count* was fixed, the line *diversity*
was not. The filed follow-up asks for both the render fix and a fixture whose today-lines are
production-diverse.


### Finding 2: BUG-0338 — refusal-ledger context field, both bindings

**Category:** pass

Every acceptance criterion of `CODER-20260919-030-refusal-ledger-context` was exercised
behaviorally in a throwaway sandbox root (never the live install):

| Criterion | Result |
|---|---|
| A record carries a `context` field valued `install` or `test`, stamped by the writer, identical from both bindings | **pass** — `- Context: install` / `- Context: test` written by `record_refusal()` and, separately, by `refusal_ledger_record` (bash), which shells out to the same CLI |
| A refusal raised during a pytest run records `test`; one from the install's own operation records `install` | **pass** — with `PYTEST_CURRENT_TEST` set, both bindings wrote `context=test`; with it unset (`env -u`), both wrote `context=install` |
| A full unit-suite run leaves the live `projects/pgai-agent-kanban/refusal-ledger.md` byte-identical | **pass** — `sha256` `d51b9f10…cf626` before the 4899-test run and `d51b9f10…cf626` after; zero test-shaped tokens (`pytest`, `Context: test`, `/tmp/pgai_kanban_tmp`) in the file. This is the criterion CODER could not verify directly; it held cleanly here |
| `read_latest` accepts a pre-context-field record without raising and reports `unknown` | **pass** — a hand-written record with no `- Context:` bullet returns `'unknown'` from Python and `context=unknown` from `refusal_ledger_read_latest` |
| `python3 team/scripts/lint_api_parity.py` reports no findings | **pass** — `ok — 19 script/model pair(s) checked, 0 violation(s)` |
| `bash -n team/scripts/lib/refusal_ledger.sh` and the refusal-ledger modules green | **pass** — `bash -n` OK; the module's tests are inside the green `UNIT_EXIT=0` run |

The parity design is worth recording because it is stronger than the criterion asked for:
`refusal_ledger.sh` computes no context at all. It shells out to the Python module's own
`record` CLI subcommand, which stamps from `PYTEST_CURRENT_TEST`, so the two bindings cannot
drift — there is one write path, not two implementations kept in sync by a lint.

The second half of the fix is `team/tests/conftest.py`'s autouse `_block_live_kanban_writes`
now also redirecting the legacy bash-only `KANBAN_ROOT` alias, which is the alias every
`project_paths.sh` function actually reads. That is the mechanism the byte-identical result
above demonstrates.

### Finding 3: BUG-0338 — why.sh refusal-surface filter

**Category:** pass

`_why_details_refusal_is_install_scoped` in `team/scripts/lib/why_details.sh` was exercised
directly through `why_details_render_discovery_skip` against four seeded ledger records:

| Case | Expected | Actual |
|---|---|---|
| `Context: install`, message names a path under the kanban root | render | rendered, all five fields |
| `Context: test`, same in-root path | skip | `no discovery/gate refusal recorded for this project` |
| no `Context:` bullet at all, message names `/tmp/pgai_kanban_tmp/tests/pytest-of-rocky/…lock` | skip | `no discovery/gate refusal recorded for this project` |
| no `Context:` bullet, in-root path, remedy carrying an ordinary *relative* path (`create projects/demo/bugs/`) | render | rendered |

The third case is the load-bearing one: it proves the path check works independently of the
context field, so a record predating the field is still caught. The fourth guards the
false-positive the CODER's own status notes catching during implementation — the token must
itself *start* with `/`, so an embedded slash in a relative-path remedy does not misfire.
A skipped record's output names neither its timestamp nor its path, as the criterion requires.
`bash -n team/scripts/lib/why_details.sh` passes.

### Finding 4: BUG-0339 — operator window pane count and the census cross-check

**Category:** pass-with-caveat

| Criterion | Result |
|---|---|
| Fresh create: `tmux list-panes` for the operator window reports exactly 10 panes, exactly one an interactive shell | **pass** — `test_dashboard_operator_window.py::test_operator_window_has_ten_panes_in_session` asserts `len(pane_lines) == 10` against a live tmux session inside a built container, and the whole module (23 tests) ran and passed inside the green gated unit run; nothing was skipped |
| A check compares create.sh's built pane count per window against the census, and refuses naming the window when an extra pane is seeded | **pass, triggered** — see below |
| That check is wired into `run-unit-tests.sh` and passes on the unmodified tree | **pass** — it is a pytest module under `team/tests/unit/`, so the gated unit runner executes it; green |
| The rows freed by the removed pane go to board and suggested-commands | **caveat** — see below |
| `docs/DASHBOARD-PANES.md` regenerated, Window 1 lists one cmdline pane, `generate_window_docs.py --check` passes | **pass** — the Window 1 table lists 10 rows with exactly one `Bottom (~15% height) — interactive cmdline shell`; `generate_window_docs --check: OK -- all surfaces match the census` |
| `bash -n team/scripts/dashboard/create.sh` | **pass** |

**Guard triggered (Step 6.5).** The new check is static analysis over `create.sh`, so it was
exercised by feeding its own resolver a mutated copy of the file with one extra
`tmux split-window` seeded into the operator window's build span — the exact shape of BUG-0339.
Baseline: no mismatches. Seeded:

```
"'operator' (variant='-'): builds 11 pane(s) but the census declares 10"
```

The guard fires and names the offending window. The tree itself was never modified; the
mutation was applied to an in-memory copy of the file's text.

**The caveat.** `team/scripts/dashboard/create.sh` is *not in this RC's diff* — neither is
`docs/DASHBOARD-PANES.md` nor the census. The operator window already built ten panes with one
shell before this RC started, which CODER-032's status records honestly. So the fourth
criterion ("the rows freed by the removed pane return to the board/suggested-commands split")
describes work that did not need doing: no pane was removed by this RC, and the three regions
already sit at their census proportions. The ticket's real deliverable — the cross-check that
prevents the drift from recurring — landed and works. Recording this as `pass-with-caveat`
rather than `gap` because the end state the criterion describes is factually true of the
shipped tree; only the mechanism (this RC removing a pane) never applied.

### Finding 5: BUG-0341 — env-coupled debug-log routing test

**Category:** pass

| Criterion | Result |
|---|---|
| The module produces the same result under a `/projects/`-containing `PGAI_AGENT_KANBAN_TEMP_DIR` and one without, both passing | **pass** |
| The project-less case asserts a `kanban_root`-relative expectation, not an absolute-path substring | **pass** — `pathlib.Path(resolved_path).relative_to(kanban_root).parts` then `assert "projects" not in …` |
| No case in the module asserts over an absolute path prefix the test does not control | **pass** — AP8 reports zero findings of that class across `team/tests/` |
| `bash team/scripts/run-unit-tests.sh --coverage` exits 0 with `PGAI_AGENT_KANBAN_TEMP_DIR` under a `/projects/` directory | **pass** — this is exactly how this verification's own unit run was invoked: `PGAI_AGENT_KANBAN_TEMP_DIR=/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester-036`, `UNIT_EXIT=0`, `4899 passed, 31 skipped`. Under v2.24.3 the same invocation produced `1 failed, 4873 passed` |
| `lint_test_anti_patterns.py` flags the shape, reports the pre-fix form when seeded, and reports zero of that class after the fix | **pass, both directions** — see below |
| The routing code under `team/` is unchanged | **pass** — the diff touches only the test, the lint, the docs, and two fixtures |

**Both directions of the new lint exercised (Step 6.5 / anti-pattern 6).** The shipped
positive fixture (`tests/fixtures/fixture_abs_path_substring_assertion.py`, carrying the exact
pre-fix assertion) and the shipped negative control
(`fixture_abs_path_prefix_controlled.py`) were copied into a throwaway tests directory and
scanned: exactly **one** finding, on the seeded pre-fix line, naming file, line, literal,
subject, and a runnable remedy; **zero** on the control. Tree-wide, AP8 reports zero.

This is the new-normal-case *and* the original-failure-mode pair the methodology requires: the
fixed test passes under both temp-dir shapes, and the pre-fix shape is still detectable.

### Finding 6: requirements-document acceptance criteria

**Category:** pass

- *Each bundled item is addressed in the implementation* — **pass**. All four bundled bugs have
  implementing work on the RC. BUG-0340's implementation is present but does not reach its own
  bound; that shortfall is Finding 1, filed as BUG-0344, not a claim that the item was skipped.
- *All Python files compile cleanly* — **pass**. `python3 -m py_compile` on all 12 changed
  `.py` files: OK.
- *All bash files pass syntax check* — **pass**. `bash -n` on all changed shell files
  (`refusal_ledger.sh`, `why_details.sh`, `event-band.sh`, `event_signal_patterns.sh`, and
  `create.sh` as the census check's subject): OK.

### Finding 7: constraint and standards checks (Steps 6.9, 6.10, 6.13, 6.14)

**Category:** pass

- **Secrets / banned patterns** — no added line in the diff matches a credential assignment
  shape; no hardcoded token, key, or password.
- **Provenance in shipped artifacts** — zero added lines under `team/scripts/` or
  `team/pgai_agent_kanban/` cite a `BUG-NNNN`, a task ID, or a framework version. Both CODERs
  who initially landed such citations reworded them in follow-up commits (`022b37a`, `c1a73fc`)
  before their merges; `lint_hygiene --classes provenance` is clean in the runner output.
- **Skip-cites-a-real-bug (Step 6.13)** — `bash team/scripts/lint_skip_bug_gate.sh` →
  `OK — all skip annotations are compliant`, `0 missing files`. No `BUG-SKIP` or
  letter-suffixed placeholder shape anywhere in `team/tests/`.
- **Prerequisite graph (Step 6.10)** — no cycles; every referenced task ID exists; the
  lifecycle is correctly bookended: `CM-20260919-035-open-rc-2-24-5` → `CODER-…-030` through
  `-034` → `TESTER-…-036` → `CM-20260919-038-release-2-24-5`, whose `## Prerequisites` names all
  six predecessors. Sequence 037 is unused for this date, which is not a collision.
- **Coding standards (Step 6.14)** — no new script was added under `team/scripts/`, so the
  `--help` obligation does not arise. No new operational value was hardcoded where an operator
  would want it configurable (the event-band read bound stays resolvable from
  `PGAI_DASHBOARD_EVENT_BAND_READ_BOUND_LINES` / `kanban.cfg`; the unit test's time bound is now
  *read from* `create.sh`'s `_DEFAULT_REFRESH` rather than duplicated as a literal, which is the
  standard being followed rather than violated). No newly duplicated `lib/` implementation.
- **Documentation discipline** — `docs/test-authoring-guidelines.md` gained the AP8 subsection
  in the same branch as the lint change, with an accurate BAD/BETTER pair and the correct
  statement that the check is quality-class and non-fatal. No tunable, env var, or config value
  was added anywhere in this RC, so no `*_example` file update was owed.

### Finding 8: Step 6.8 — synthetic release simulation

**Category:** pass

The diff touches no release-lifecycle file (`cm-release.sh`, `cm-open-rc.sh`, `cm-cancel-rc.sh`,
`team/scripts/lib/project_paths.sh`, or anything else in the open/close cycle), so this step is
skippable by its own condition. Recorded anyway because the evidence exists for free:
`tests/integration/test_cm_release_synthetic.py` ran as part of the green integration suite —
17 tests, all passed.

## Lint Findings (non-blocking)

- `test_anti_patterns` — 2 instances of class `AP3` (hardcoded `/tmp/pgai_kanban_tmp` literal),
  both at `team/tests/unit/test_why_details_pane.py` lines 462 and 489, both introduced by this
  RC's commit `61d26e7`. Reported by **both** runners (`findings-unit.json` and
  `findings-integration.json`); one class, one run, so one filing covers both suites —
  filed **BUG-0345** (Path C-A — autonomous follow-up filing, non-blocking; filed as BUG),
  severity `low` (the worst instance's, and both are the same shape). Neither runner's exit code
  was affected: `[gated-runner] test_anti_patterns: quality-class finding(s) (exit 1) —
  non-blocking, continuing.`

No other lint reported a finding. Grepping `$PGAI_PROJECT_ROOT/bugs/` for an existing `open` or
`running` bug describing this class found none (the nearest hits — BUG-0172, BUG-0191, BUG-0262,
BUG-0311, BUG-0314 — are all `done` and describe different classes), so a new bug was filed
rather than an existing one cited. **This finding does not move the recommendation:** per
Step 5c a bug filed solely to track a quality-class lint finding keeps `PASS`. The
recommendation below is elevated by Finding 1 alone.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=12d freshness=stale — Stale on both counts: the record is 12 days old against a 7-day window and was taken at v2.9.20 rather than the v2.24.5 under verification, and the failure it records (the `claude-abliterated` Dockerfile's `FROM` arity under both docker and podman, plus `podman/rocky9`'s `COPY --chmod`) was already filed by that run as BUG-0167-cold-build-claude-abliterated-failure, whose `## Status` is now `done` — cited here, never re-filed, and no cold-build lane was run during this verification.

## Autonomous criterion (Step 6.12)

**Autonomous operation criterion: Manual interventions required: none.**

| Check | Answer |
|---|---|
| (1) Any task force-promoted from WAITING to BACKLOG by hand, bypassing the wake script's prerequisite evaluation? | **NO** — this task's own promotion is stamped by the wake script ("Promoted from WAITING to BACKLOG by wake script: prerequisites satisfied"); `CM-20260919-038-release-2-24-5` still sits at `[W]`, correctly waiting on this task |
| (2) `release-state.md` edited outside `cm-open-rc.sh` / `cm-release.sh` / `cm-cancel-rc.sh`? | **NO** — all four fields are internally consistent and match the RC on disk: `Active RC` `v2.24.5`, opened `2026-09-19T21:38:29+00:00` by `CM-20260919-035-open-rc-2-24-5`, `Last Released` `v2.24.4` agreeing with `pp_last_released_version` |
| (3) Any script patched mid-build — a commit touching `team/scripts/`, `subagents/`, or other tooling while the RC was in flight? | **NO** — every commit between `ai_main` and the RC tip lands on a task feature branch and merges `--no-ff` under its ticket's name. The tooling files this RC touches (`event-band.sh`, `why_details.sh`, `refusal_ledger.sh`, `lint_test_anti_patterns.py`) are the tickets' own deliverables, not out-of-band patches. Commit times run 21:58-22:29, all inside the RC window opened at 21:38 |
| (4) Any queue file edited by hand? | **NO** — every marker derives correctly from its task's `## State`: CODER-030 through -034 all `[x]`/`DONE`, CM-035 `[x]`/`DONE`, TESTER-036 `[A]` while this agent runs, CM-038 `[W]` |

No manual intervention was required at any point, and no process defect surfaced.

## Spot-checks outside the spec (Step 6.11, time-boxed)

- **State drift** — none. `Active RC` `v2.24.5` matches a real local branch whose tip is this
  worktree's `HEAD`; `Last Released` matches the helper's answer.
- **Untested new mechanisms** — none found. Every new code path in the diff has coverage:
  `_why_details_refusal_is_install_scoped` (three new cases in `test_why_details_pane.py`),
  `_detect_context` and the seven-field record (new cases in `test_refusal_ledger.py` and
  `test_error_contract_bash.py`), AP8 (a dedicated module plus a positive and a negative
  fixture), and the census cross-check (its own module).
- **Regressions of prior gaps** — BUG-0341's regression is fixed and was re-verified under the
  exact condition that produced it. BUG-0340's gap persists and is Finding 1.
- **A behavior worth naming, not a finding:** `mapfile -t` unsets its target array before
  filling it, so the per-agent `_agent_signal_lines` reuse in `event-band.sh` cannot leak one
  agent's lines into the next agent's iteration when a later log has no candidates. Verified
  directly on bash 5.1.8 rather than assumed, because the rewrite moved that loop from a
  process substitution to a reused array.

## Caveats

- **The out-of-root path filter can hide a genuine install refusal.** By design (it is the
  ticket's second acceptance criterion), `why.sh --details` now suppresses any record whose
  surface/message/remedy names an absolute path not under `PGAI_AGENT_KANBAN_ROOT_PATH`. A real
  install refusal whose message embeds a dev-tree, worktree, or system path would therefore
  render as "no refusal recorded". I surveyed the 114 `pgai_emit_error_contract` /
  `record_refusal` call sites and found none whose message currently embeds such a path, so this
  is a latent shape rather than an observed defect — not filed, recorded here so the next
  verifier recognises it if the surface ever goes quiet unexpectedly. Relatedly,
  `why.sh` sets `KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"` at line 165, so the two roots
  cannot diverge inside the filter's process; there is no root-mismatch false negative.
- **`git stash list` was empty**, so no Stashes Found section applies.
- **Test fidelity beyond Finding 1.** The refusal-ledger, why-details, AP8 and census tests all
  exercise the real breaking condition rather than a proxy: the ledger tests write and read real
  files through both real bindings, the why-details tests drive the real `why.sh` entry point,
  and the AP8 tests scan real fixture files. The one fidelity failure in this RC is the
  event-band time-bound fixture, which is Finding 1 and BUG-0344.
- **`.previous-RUN-N` artifacts:** none existed at start; `artifacts/` was empty, so this was a
  clean fresh-start run with no prior verdict to inherit.
