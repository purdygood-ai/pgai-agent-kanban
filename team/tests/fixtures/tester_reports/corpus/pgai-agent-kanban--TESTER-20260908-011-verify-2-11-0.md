# Verification Report: rc/v2.11.0

**Task:** TESTER-20260908-011-verify-2-11-0
**RC branch:** `ai_rc/v2.11.0` @ `ac4f15a` (worktree checked out detached at that commit; tree clean, no stashes)
**Baseline:** `ai_main` — 34 files changed, +6469 / -404
**Requirements doc:** `projects/pgai-agent-kanban/requirements/v2.11.0-single-truth-state-integrity.md`
**Verified:** 2026-09-08

## Summary

v2.11.0 collapses three competing records of task state — queue markers,
`status.md`, and `release-state.md` — into one truth (`status.md`) with
everything else a rendering of it. All six deliverables in the requirements
document are implemented, and all five acceptance fixtures were verified twice
over: once by the RC's own integration suite, and once independently by this
verification, exercising each guard against the failure mode it is supposed to
prevent rather than reading the code that implements it. Both gated runners
exited 0 (3321 unit tests passed / 3 skipped; 360 integration tests passed / 1
skipped). Pre-flight merge verification passed, the prerequisite graph is
acyclic with every reference resolving, no empty commits are present in the RC
range, and the build ran end to end with no manual intervention.

Four follow-ups are filed. Two are pre-existing defects the spot-check
surfaced (`verify-rc-state.sh` ignores `branch_prefix`; the generated queue
legend omits `[B]`), one is a corner of deliverable 4 the conversion did not
reach (a test-only wake provider still takes the shared repo lock raw, and its
truncating open wipes a live holder's provenance record), and one is a
quality-class lint the RC's own line-shifts tripped (two census Locator Index
entries went stale). None prevents the release from being operated; all four go
to the next iteration.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

low

## Test Suites (authoritative — gated runners)

| Suite | Command | Exit | Result |
|---|---|---|---|
| Unit | `bash team/scripts/run-unit-tests.sh --coverage` | **0** | 3321 passed, 3 skipped (391 s) |
| Integration | `bash team/scripts/run-integration-tests.sh --coverage` | **0** | 360 passed, 1 skipped (243 s) |

`UNIT_EXIT == 0 AND INTEGRATION_EXIT == 0`. Both verdicts come from the gated
runners — the same commands an operator or CM would run, lint pre-flight
included — not from a direct `pytest` invocation.

## Code Coverage

- Unit:        line 74% | branch 61%   (14228 stmts, 4350 branches; blended 71%)
- Integration: line 8%  | branch 6%    (blended 7%)

Read from the `totals` objects in `/tmp/pgai_kanban_tmp/coverage/{unit,integration}.json`
(`percent_statements_covered` / `percent_branches_covered`). Informational only;
coverage does not enter the PASS precondition and is never a finding.

## Findings

### Finding 1: All six requirements deliverables implemented and behaviourally verified

**Category:** pass

Each deliverable was exercised against its own failure mode (Step 6.5), not
merely read.

**D1 — discovery/idle checks read `status.md`, never raw queue markers, enforced.**
`_disc_chain_idle` and `_disc_pm_ticket_in_flight` (`team/scripts/lib/discovery_guards.sh`)
now resolve every backlog entry through the new `_disc_task_terminal`, which
reads that task's own `## State`. The rewrite also fixes a second, independent
bug the marker read was hiding: the old gate inspected only the *tail* entry of
each backlog, so an earlier still-WORKING task was invisible. Driven directly
against a synthetic kanban root, all four arms behave correctly:

| Fixture | Result |
|---|---|
| stale `[b]` marker over a `DONE` status.md | gate OPEN — discovery proceeds |
| `[x]` marker over a `WORKING` status.md | gate CLOSED, log names the task and its state |
| `[x]` marker over an unparseable status.md | gate CLOSED (conservative) |
| backlog names a task with no folder | gate OPEN, logged as a distinct NOTE |

The "enforced" half is real: `team/scripts/lint_raw_marker_read.py` (new,
wired into `run-unit-tests.sh` as a CONVERT-class quality lint) reports OK on
the current tree, and when the pre-fix `\[([^]]*)\]` capture shape was
reintroduced into a copy of `discovery_guards.sh`, the lint caught it and
printed a remedy naming `_disc_task_terminal` as the pattern to follow.

**D2 — atomic transitions through one write path.** `transition_task_state`
(`team/scripts/lib/wake_task_transition.sh`, new) owns the sole state→marker
table and snapshots both surfaces before writing either. Exercised:

- queue entry missing → refuses `wake.transition.marker-write-failed`,
  **rolls `## State` back**; both surfaces byte-identical to their pre-call
  values (verified by comparison, not by inspection).
- invalid target state → refuses `wake.transition.invalid-state`, touches
  nothing.
- missing `status.md` → refuses `wake.transition.status-missing`.
- happy path → `## State` = DONE and marker `[x]` together, exit 0.

Both named callers use it: the watchdog (`reconcile_stale_active`,
`wake_task_state.sh`) and self-correction (`handle_working_after_exit`,
`wake_lifecycle.sh`), each via the shared `zero_progress_advance_or_escalate`.

**D3 — one validating release-state loader, shared by both readers.**
`RELEASE_STATE_SCHEMA` in `team/pgai_agent_kanban/cm/read_state_field.py` names
all four fields, their accepted shapes, and whether the heading may be absent;
`load_release_state` raises through the shared dual-channel contract. The
`--validate` CLI is what `verify-rc-state.sh` Check A now calls (via
`pp_run_ops`), so the two cannot disagree — the script's own duplicate
`_is_valid_iso8601` helper and its four hand-rolled format branches were
deleted in this RC. Run against the live file, Check A reports OK on all four
fields. The `NONE` fixture the requirements name refuses exactly as specified:

```
Active RC	ERROR	'## Active RC' is 'NONE', which is not one of: 'none' or vX.Y.Z — remedy: set '## Active RC' to one of: 'none' or vX.Y.Z, then re-run
```

**D4 — lock provenance required.** `pgai_lock_acquire`
(`team/scripts/lib/lock_provenance.sh`, new) refuses with return 2 and the
`lock.provenance.incomplete` contract when *any* of holder/pid/op/task is
blank — verified one field at a time, all four — and takes no lock (the lock
file was never created). A fully provenanced acquire writes the four-line
record; a contending acquire declines with return 1 and names the holder
verbatim; release clears the record. See Finding 3 for the one call site not
yet converted.

**D5 — zero-progress cycle cap.** Driven three consecutive times against a
synthetic orphaned task with the default cap of 3:

```
cycle 1 → BACKLOG, marker [ ], Needs Human: no, count 1/3
cycle 2 → BACKLOG, marker [ ], Needs Human: no, count 2/3
cycle 3 → BLOCKED, marker [B], Needs Human: yes,  count 3/3
```

At the cap, `## Blockers` carries the full dual-channel block keyed
`wake.reconcile.zero-progress-cap` with a runnable remedy. The counter is
durable (`## Zero Progress` in `status.md`), survives BACKLOG promotion, is
reset only by `wd_finalize_task.sh` when the task moved under its own power,
and is a genuinely separate counter from `## Transient Retry`. The cap is
configurable end to end: `kanban.cfg [wake] zero_progress_cap` → `config_loader.sh`
→ `ZERO_PROGRESS_CAP` → `zero_progress_cap()`, documented in
`kanban.cfg_example`. The requirement's "covers the exit-0 turn-end variant"
is met — `handle_working_after_exit`'s non-transient branch routes through the
same shared core.

**D6 — contention retry.** `wake_common_run` collects projects whose
`run_project_chain` returned 3 (repo-lock contention) into `_CONTENDED_PROJECTS`
and gives each exactly one more attempt after every project has had its
ordinary turn — never a loop, never a sleep-poll, never a blocking flock. The
retry pass re-evaluates the per-project HALT fast-exit fresh, re-exports the
per-project environment, and respects the batch runtime budget both before the
pass and between projects within it. Verified end to end by the RC's own
real-subprocess integration coverage (see Finding 7).

---

### Finding 2: `verify-rc-state.sh` Check e ignores `branch_prefix` and always errors on prefixed projects

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (a read-only operator diagnostic; no automated gate consumes its exit code, and no chain behaviour depends on it)
**Regression:** no (`_check_e` is not in this RC's diff at all — `git diff ai_main..ai_rc/v2.11.0 -- team/scripts/verify-rc-state.sh` touches only Check A and the deleted `_is_valid_iso8601` helper; the defect predates this release)
**Integrity Damage:** no
**Filed As:** BUG-0193 (Path C-A — filed as BUG — code defect / regression / test failure)

Running the checker against the live project during Step 6.11:

```
--- Check e: release-state.md <-> git branch consistency ---
[ERROR] release-state.md Active RC=v2.11.0 but local branch rc/v2.11.0 does not exist in $HOME/develop/pgai-agent-kanban

verify-rc-state: 1 errors, 14 warnings, 36 ok    (exit 1)
```

The branch exists — as `ai_rc/v2.11.0`, because the project declares
`branch_prefix = ai_`. Check e builds `local rc_branch="rc/${active_rc}"` and
globs `git branch --list 'rc/*'`, applying no prefix, even though the script
already sources `lib/project_paths.sh` and so has `pp_prefix_branch` in
process. The `Active RC = none` arm is blind in the opposite direction: an
orphaned `ai_rc/*` branch would never be reported.

This is worth more than its systemic risk suggests because `docs/OPERATIONS.md`
tells operators to run this exact command after unwinding an RC and states
"After a clean cancellation, `verify-rc-state.sh` should exit 0." That success
condition is unreachable on any prefixed project, which teaches operators to
ignore the exit code of the one tool documented as the post-unwind safety net.

Note that this RC's own change to the *same script* is correct and verified:
Check A now delegates every field-format judgement to the shared validator and
reports OK on all four fields.

---

### Finding 3: A wake provider still takes the shared repo lock without provenance — and its truncating open destroys the live holder's record

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (`harness.sh` is a test-only stub provider, explicitly never active in a live install, and its tests run against sandboxed temp roots; flock exclusion itself is unaffected)
**Regression:** no (`team/scripts/wake/harness.sh` is not in this RC's diff; the acquisition shape predates the release — what is new is the "ALWAYS" claim it does not satisfy)
**Integrity Damage:** no (the destroyed record is diagnostic provenance, not task state or queue state; exclusion and correctness rest entirely on flock, which is unaffected)
**Filed As:** BUG-0194 (Path C-A)

Deliverable 4 says provenance is *always* populated. Three of the four
acquisition sites were converted (`wake_driver.sh`, `overwatch_lib.sh`,
`overwatch_protocol.sh`). The fourth was not:

```bash
# team/scripts/wake/harness.sh
  _lock_file="$(pp_repo_lock_path "$project_name")" || return 0
  local _lock_fd
  exec {_lock_fd}>"$_lock_file"
  if ! flock -n "$_lock_fd"; then
```

Two consequences, the second concrete and reproduced. `exec {fd}>PATH` opens
with `O_TRUNC`, and it does so *before* `flock -n` is evaluated — so a
correctly-declined acquisition still wipes the incumbent's record. This is
precisely the failure `lock_provenance.sh`'s own header says its append-mode
open exists to prevent. Demonstrated by replaying the harness's exact shape
against a provenanced lock:

```
provenance before harness-style attempt:
holder=wake-driver
pid=668522
op=wake-chain:proj
task=none
harness-style acquire correctly declined (contended)
provenance AFTER harness-style attempt:
(end)
```

The only other raw `flock` in `team/scripts/` is
`temp_purge.sh:196` (`flock -n "$lock_file" true`), which is a liveness probe,
not an acquisition, and is correctly out of scope.

---

### Finding 4: Generated queue-file marker legend omits `[B]`, contradicting the vocabulary this RC canonised

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (documentation drift in a generator; no runtime effect)
**Regression:** no (`team/scripts/create-project.sh` is not in this RC's diff; the legend predates the release — this RC is what makes the omission newly inconsistent, by canonising the five-marker table)
**Integrity Damage:** no
**Filed As:** BUG-0195 (Path C-A)

The WRITER deliverable states the full marker vocabulary in `team/SOP-core.md`
as five values — `[ ]`, `[W]`, `[A]`, `[B]`, `[x]` — matching
`transition_task_state`'s derivation table exactly. `create-project.sh` stamps
a legend into the header of every queue file it creates, and that legend names
only four; `[B]` is absent. Every existing project's queue files carry the same
four-value header, having been generated by this path.

The omission is newly load-bearing: v2.11.0's zero-progress ceiling
deliberately parks a task at `[B]` + `Needs Human: yes`, so `[B]` is now
written on a documented escalation path but explained nowhere a queue reader
looks.

---

### Finding 5: Zero-progress escalation leaves a stale `## Summary` from the previous cycle

**Category:** pass-with-caveat

When `zero_progress_advance_or_escalate` escalates at the cap it writes
`## Blockers` and `## Needs Human` but does not refresh `## Summary`, which
still holds the below-cap wording from the prior cycle. Observed on the
verified fixture:

```
## State
BLOCKED

## Summary
Reset to BACKLOG by wake reconciliation: orphaned WORKING task (cycle 2/3).

## Blockers
error:   wake.reconcile.zero-progress-cap
message: This task made zero observable progress 3 consecutive times (ceiling 3) ...
```

The authoritative fields are all correct and the `## Blockers` block tells the
whole story with a runnable remedy, so a reader is not misled about state — but
the summary line a dashboard is most likely to surface says "Reset to BACKLOG"
about a task that was just blocked. Cosmetic; recorded as a caveat rather than
filed, since no criterion requires `## Summary` content and filing it would
inflate the follow-up queue over a wording nit.

---

### Finding 6: Pre-flight, merge, and structural integrity

**Category:** pass

- **Pre-flight merge verification** passed: no DONE task's feature branch is
  unmerged, and no BUG-92 zero-commits-ahead anomaly.
- **Worktree identity:** `git rev-parse HEAD` == `git rev-parse ai_rc/v2.11.0`
  == `ac4f15a`; `git status --short` empty; `git stash list` empty.
- **Empty commits:** none. Every non-merge commit in `ai_main..ai_rc/v2.11.0`
  has a tree differing from its parent — the BUG-0183 / BUG-0192 pattern that
  recurred through the v2.10.x line does not recur here.
- **Structural:** all new files present; `lock_provenance.sh` and
  `wake_task_transition.sh` are mode 755; `bash -n` clean on all 14 changed
  shell files; `py_compile` clean on all changed Python.
- **Prerequisite graph:** 12 tasks, no cycles, every referenced task folder
  exists, bookends correct (CM-open first, TESTER then CM-release last).
- **`release-state.md` (Step 6.1):** `Active RC = v2.11.0`, `RC Opened At`
  well-formed ISO-8601, `RC Opened By Task` names the CM task,
  `Last Released = v2.10.4` well-formed. `pp_last_released_version` returns
  `v2.10.4` from git tags and `semver_gt v2.11.0 v2.10.4` holds. No drift.
- **Constraint negatives (Step 6.9):** no hardcoded credential-shaped
  assignments and no bare-`/tmp` literals in the RC's added lines.
- **Skip-cites-real-bug (Step 6.13):** no placeholder skip IDs and no skip
  annotation citing a non-existent bug. (`grep` matches under
  `team/tests/artifacts/*.log` are archived runner logs, not skip
  annotations.)
- **Coding standards (Step 6.14):** the new lint ships a `--help` naming every
  argument; no bug/task ID provenance in any of the four new/heavily-modified
  non-test files.
- **Synthetic release simulation (Step 6.8):** triggered, since
  `read_state_field.py` participates in the release lifecycle.
  `test_cm_release_synthetic.py` — 15 passed.

---

### Finding 7: Test fidelity of the RC's own new coverage

**Category:** pass

The acceptance suite is not a sanitized proxy. `test_wake_contention_retry.py`
and `test_single_truth_state_integrity.py`'s fifth fixture drive a real
`bash scripts/wake/claude.sh --agent=writer` subprocess against a real
simulated installed root, with a genuine background process holding the real
repo flock — so strict-mode bash, the installed tree layout, and multi-project
iteration are all exercised for real rather than simulated in Python
(fidelity checklist items 1, 2, 3 and 6). The five acceptance fixtures share
one kanban tree by design, so a regression breaking the compound guarantee
trips even when each function still passes in isolation. Re-run standalone in a
throwaway root: 9 passed in 17 s.

## Lint Findings (non-blocking)

- `comment_provenance` — 1064 quality-class findings, every one under
  `team/tests/` (832 in `unit/`, 231 in `integration/`, 1 in `artifacts/`);
  zero in `team/scripts/`, `team/pgai_agent_kanban/`, or `release-notes/`.
  **Cites BUG-0191 (open)** — no duplicate filed. Note for the fixer: 22 of
  these instances are in test files this RC added, so the backlog BUG-0191
  describes grew from 1043 to 1064 across this release rather than shrinking.
- `gate_census_compliance` — 2 quality-class findings
  (`team/scripts/lib/wake_common.sh:130`, `team/scripts/run-unit-tests.sh:591`),
  both caused by this RC shifting those refusals' line numbers while their
  line-number-keyed entries in `docs/blocking-site-census.md`'s Locator Index
  stayed at 117 and 579. The refusal code itself is unchanged. BUG-0180 covered
  this lint class previously but is `done`, so no open bug applies —
  **filed BUG-0196 (Path C-A)**, one bug for the class with both instances
  listed inside it.

Both classes are quality-class: they print, land in the findings file, and the
runner continues. Neither moves `## Recommendation` or `## Systemic Risk`.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=1d freshness=stale — the last lane run went red on the claude-abliterated `FROM` arity and the podman/rocky9 `COPY --chmod=+x` step, both since repaired in the tree (the RC now carries a single-argument `FROM` and `--chmod=755`) and both tracked by BUG-0167, which shipped as done; the record is stale because v2.9.20 is not the v2.11.0 under verification, so no lane has built these images cold since the fix landed.

## Autonomous criterion

Manual interventions required: none.

1. **Tasks force-promoted from WAITING to BACKLOG by hand?** NO. All ten tasks
   in the cycle read `State: DONE`, `Needs Human: no`; status.md mtimes track
   the wake firings that wrote them, and the wake logs record no BLOCKED task
   re-checks resolving anything.
2. **`release-state.md` edited outside the CM scripts?** NO. Its mtime
   (02:26) matches `CM-20260908-010-open-rc-2-11-0`'s own completion window,
   and its contents are exactly what `cm-open-rc.sh` writes.
3. **Scripts patched mid-build?** NO. Every commit in
   `ai_main..ai_rc/v2.11.0` is a task commit or its `--no-ff` merge, in
   prerequisite order, timestamped inside its own task's window.
4. **Queue files hand-edited?** NO. Queue mtimes correspond to wake
   transitions; markers agree with each task's `status.md` (`[x]` for the eight
   DONE tasks, `[A]` for this in-flight TESTER task).

## Caveats

- **Redundant cap resolution.** `zero_progress_cap()` checks the
  `ZERO_PROGRESS_CAP` env var first and falls back to reading
  `kanban.cfg [wake] zero_progress_cap`, but `config_loader.sh` now
  unconditionally exports that variable (defaulting to 3), so the file-reading
  branch is unreachable on any wake path. Harmless — both paths read the same
  key and yield the same value — and it keeps the function correct when called
  standalone in tests. Observation only.
- **Contention return-code collapse.** `run_project_chain` treats *any*
  non-zero `pgai_lock_acquire` return as contention and returns 3, so a
  provenance refusal (return 2, a caller bug) would be silently scheduled for
  the contention retry rather than reported as the programming error it is. It
  cannot fire today: the call site passes `$AGENT`, `$$`, a literal op, and the
  literal `"none"`, all non-blank in every wake path. Observation only.
- **Census Locator Index is position-keyed.** ~90 of its entries are
  `path:NNN CLASS` rather than the token form `team/SOP.md` prescribes, so any
  release inserting lines above a censused refusal silently invalidates that
  entry — which is exactly what happened here. BUG-0196 records the two live
  instances and recommends the token conversion; the broader index-wide
  migration is a larger piece of work worth an operator's decision rather than
  a TESTER filing.
- **Live install carries a status-writer tool the shipped tree does not.**
  `scripts/task-status.sh` exists in the operator's install (v2.9.19) and the
  installed `SOP.md` / `roles/TESTER.md` teach it as the canonical write path
  for task state, but neither `team/scripts/task-status.sh` nor
  `team/pgai_agent_kanban/task_status.py` is present at `ai_rc/v2.11.0`, at
  `ai_main`, or at any of the tags v2.9.19 / v2.9.20 / v2.10.0 / v2.10.4. The
  RC's own `team/roles/` files do not reference the tool, so a fresh install of
  v2.11.0 would be internally consistent. Not attributable to this RC — it
  neither added nor removed the file — and recorded here as an environment
  observation for the operator rather than filed as a finding, since it does
  not trace to this release's diff.
- **`## Last Released` vs. `pp_last_released_version`.** Checked
  independently per Step 6.1, not against each other. Both read `v2.10.4`;
  this is noted for the record, not as a comparison the check performs.

## Verdict

Six deliverables implemented, five acceptance fixtures verified independently
against their own failure modes, both gated runners green, the build autonomous
end to end. Four follow-ups filed — one pre-existing operator-diagnostic bug,
one unconverted lock site in a test-only provider, one generator documentation
drift, one census-index staleness this RC's line-shifts introduced. All are
minor and isolated; none prevents users from operating the resulting release.

**SHIP-WITH-CONCERNS.**
