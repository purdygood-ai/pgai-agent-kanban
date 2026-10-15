# Verification Report: rc/v2.23.5

## Summary

RC `ai_rc/v2.23.5` (HEAD `fdc1aff`) was verified against
`requirements/v2.23.5-bugfix-bundle-20260916.md`, whose single bundled item is
`BUG-0298-dashboard-snapshot-producer-missing` (four fix heads plus a doc-truth
head). The RC carries four merges — three CODER, one WRITER — touching eight
files: `team/scripts/dashboard/{cadence-loop.sh,activation-hook-sender.sh}`,
`team/scripts/dashboard/lib/{cadence.sh,snapshot.sh}`, two test modules, and
`docs/{dashboard.md,DASHBOARD-PANES.md}`. All four fix heads were verified
behaviourally in throwaway sandboxes (not by reading alone): pane render stderr
is routed to a per-pane debug log and never reaches the display stream; routine
TTL-expiry misses are demoted to a debug tag while missing/unreadable misses and
failed cache writes stay at normal volume; the activation-hook sender emits zero
display bytes across a 30-switch storm and narrates only into per-pane logs; and
census-identified streaming panes are excluded from signalling entirely. The
gated unit suite is green. The gated integration suite exits 1 on three tests —
two from a golden fixture this RC's own message-text change left stale, one from
a fixture that inherits `PGAI_AGENT_WAKE_ACTIVE` from the ambient wake
environment. Neither failure is in production code; both are test-estate
defects, filed via Path C (autonomous follow-up filing — non-blocking). Two
further findings were recorded: the stale-literal pre-flight check that should
have caught the golden does not scan fixture files, and a shipped test assertion
message carries an internal bug identifier.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

medium

## Suite Results (Step 5a)

Both gated runners were run from the dev-tree root
`/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260916-022-verify-2-23-5`,
output captured under `$PGAI_AGENT_KANBAN_TEMP_DIR/tester/`.

- `bash team/scripts/run-unit-tests.sh --coverage` → **UNIT_EXIT = 0**
  (`4630 passed, 31 skipped in 825.45s`); bare-/tmp and listener cleanliness
  checks OK.
- `bash team/scripts/run-integration-tests.sh --coverage` → **INTEGRATION_EXIT = 1**
  (`3 failed, 507 passed, 1 skipped in 558.73s`).

The three failures are Findings 3 and 4 below. The PASS precondition is not met,
so the recommendation is at minimum SHIP-WITH-CONCERNS.

## Coverage (Step 5b — informational only)

Read from the runners' own JSON at `$PGAI_AGENT_KANBAN_TEMP_DIR/coverage/`.

- unit: blended statement+branch coverage 73 % (`TOTAL` line; both runners pass
  `--cov-branch`, so the single `Cover` figure is not a line-only number).
- integration: blended statement+branch coverage 9 % — the integration lane
  exercises shell entry points through subprocesses, which Python coverage does
  not attribute, so the low figure is expected and is not a finding.

Coverage did not enter the verdict.

## Findings

### Finding 1: pre-flight — the RC is intact

**Category:** pass

`git rev-parse HEAD` = `git rev-parse ai_rc/v2.23.5` = `fdc1aff49b6d…`.
`git status --porcelain` empty; `git stash list` empty. Every task folder whose
`status.md` reads `DONE` and whose README names a feature branch was scanned:
zero `ai_feature/*` branches exist locally (`git branch --list 'ai_feature/*'`
returns nothing), so every branch passes both merge checks trivially — the
normal post-merge shape, since CODER and WRITER delete branches after merging.
The four prerequisite tasks (CODER-017, CODER-018, CODER-019, WRITER-020) are
each `DONE` with `Needs Human: no`, and each names its merge commit in its own
`status.md`; all four merge commits are present in
`git log ai_main..ai_rc/v2.23.5`.

### Finding 2: gated unit suite green, no lint findings

**Category:** pass

`UNIT_EXIT = 0`. The lint pre-flight passed and the findings file
`$PGAI_AGENT_KANBAN_TEMP_DIR/lint/findings-unit.json` holds 0 findings, as does
`findings-integration.json`.

### Finding 3: the engine-behaviour baseline golden was not refreshed for this RC's new narration text

**Category:** stale-assertion
**Fix Effort:** small (1 CODER task)
**Systemic Risk:** medium (second occurrence of this class; the advisory
pre-flight meant to catch it has a blind spot — Finding 5)
**Filed As:** BUG-0299-engine-baseline-golden-narration-text (Path C-A — filed
as BUG: code defect / regression / test failure)
**Regression:** yes
**Integrity:** breakage

Two integration tests fail:

- `tests/integration/test_engine_behavior_baseline_harness.py::test_compare_against_goldens_is_clean_on_unmodified_tree`
  (line 161)
- `tests/integration/test_engine_behavior_baseline_harness.py::test_compare_is_stable_across_invoking_shell_budget_values`
  (line 301)

Both fail on the same one-line diff against the recorded golden
`team/tests/fixtures/engine-baseline/board-render.txt:2`:

```
-[dashboard-snapshot] column-render CODER: no fresh snapshot (missing, unreadable, or older than 5s) -- rendering directly
+[dashboard-snapshot] column-render CODER: no fresh snapshot (missing or unreadable) -- rendering directly
```

**Step 7 stale-assertion evidence.** The expected literal is the golden's line 2,
quoted above. The production line that produces the actual value is
`team/scripts/dashboard/lib/snapshot.sh:285`, changed by commit `51109c5`
("Keep dashboard pane render narration off the display surface",
CODER-20260916-017) — the same commit that split the miss reason into
`dashboard_snapshot_read` exit codes 1 and 2 so a routine TTL expiry could be
demoted. The new wording is required by the requirements doc's bundled item
(fix head 2: demote the routine first-miss-per-tick narration "so logs stay
signal not metronome"): the old sentence enumerated staleness, which is now a
separate, separately-tagged line. `team/scripts/harness/engine-behavior-baseline.sh:281`
captures the board render with `2>&1`, so the golden legitimately contains the
stderr narration and simply needs recapturing. Production code is correct; the
golden is the thing that must change, and updating it is the correct fix for the
next iteration.

**Why the filed bug is rated severity `high` with `Regression: yes`.**
`run-integration-tests.sh` exited 0 on the immediately preceding release
(`TESTER-20260916-014-verify-2-23-4`, `510 passed, 1 skipped`) and exits 1 here,
and this RC's own work caused the divergence. Per `SOP-core.md` ("Rating a
Finding", calibration), a command that worked in the last release and exits
non-zero in this one is `high` with `Regression: yes`; the rating was not
softened to reach a preferred ship outcome. It is recorded as
`Systemic Risk: medium` rather than `high` because the breakage is confined to
one recorded fixture line in the test estate — no shipped behaviour is wrong and
the fix is a single recapture — but a reader weighing this release should know
the suite is red for a reason this RC created.

### Finding 4: the operator-force ship fixture inherits `PGAI_AGENT_WAKE_ACTIVE` from the wake environment

**Category:** bug (pre-existing — confirmed by diff analysis; both the
production path and the fixture predate this RC)
**Fix Effort:** small (1 CODER task)
**Systemic Risk:** medium (any suite run from inside an agent wake goes red for
a reason unrelated to the tree under test)
**Filed As:** BUG-0300-ship-policy-fixture-inherits-wake-flag (Path C-A)

`tests/integration/test_ship_policy_acceptance.py::TestOperatorForceShipsOverriddenTrigger::test_unforced_run_halts_then_forced_run_ships_and_writes_both_override_records`
fails at line 866. The green half of the fixture invokes `cm/release.sh --force
--reason …` expecting a ship, and gets:

```
error:   cm-release.operator-force.agent-wake-refused
message: --force was passed while PGAI_AGENT_WAKE_ACTIVE=1 — an agent wake is
         running this invocation, and the operator override is operator-only.
```

The refusal is correct behaviour. The defect is the fixture's environment
handling: `_run_cm_script` in `tests/integration/test_cm_release_synthetic.py:260`
builds the subprocess environment as `dict(os.environ)` and scrubs only
`PGAI_DEV_TREE_PATH`, so whatever the ambient shell carries is handed to the
script under test. `PGAI_AGENT_WAKE_ACTIVE=1` is exported into the TESTER agent's
own environment by the wake layer (confirmed with `env` in this session), so the
"an operator, not a wake" precondition the test depends on cannot hold when the
suite is run the way the kanban runs it. The sibling test three functions above
sets `extra_env={"PGAI_AGENT_WAKE_ACTIVE": "1"}` explicitly to exercise the
refusal — which is only meaningful if the base environment does *not* carry it,
so the two tests contradict each other under a wake.

**Proof it is environment coupling and not this RC:**
`env -u PGAI_AGENT_WAKE_ACTIVE python -m pytest
tests/integration/test_ship_policy_acceptance.py::TestOperatorForceShipsOverriddenTrigger
-x -q` → `1 passed in 4.07s`. `git log ai_main --
tests/integration/test_ship_policy_acceptance.py` shows no change since the
v2.21.0 release and `git log -S"agent-wake-refused" -- team/scripts/cm/release.sh`
none since v2.12.0; this RC's diff touches neither file. (Direct pytest is used
here only as the Step-7 tracing aid it is sanctioned to be, after the gated
runner had already failed — no pytest result is quoted as PASS evidence anywhere
in this report.)

### Finding 5: the stale-literal pre-flight does not scan golden fixture files

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (the same blind spot will pass the next message-text
change the same way)
**Filed As:** BUG-0301-stale-literal-check-skips-golden-fixtures (Path C-A)

All three CODER tasks in this RC ran
`team/scripts/coder-stale-literal-check.sh --diff ai_rc/v2.23.5...HEAD` and each
recorded `## Stale Literal Risks: (none)` in its `status.md` — while the changed
literal sat verbatim in `team/tests/fixtures/engine-baseline/board-render.txt`
and took the integration suite red within the hour. The cause is the check's
search shape: `literal_grep_tests` in `team/scripts/lib/literal-extraction.sh`
matches a literal only where it appears in an *assertion context* (`==`, `!=`,
`assert`, `expected`, `assertEqual`, …). A recorded golden is a bare data file
with no assertion syntax, so no golden can ever match, and the check reports a
clean bill for exactly the class of change — an operator-visible message string —
that goldens most often capture. The advisory output is false-negative by
construction for recorded-output fixtures.

### Finding 6: a shipped test assertion message cites an internal bug identifier

**Category:** bug
**Fix Effort:** small (1 CODER task)
**Systemic Risk:** low (isolated to one line)
**Filed As:** BUG-0302-test-assertion-cites-internal-bug-id (Path C-A)

`team/tests/unit/test_dashboard_activation_hook_lifecycle.py:382`, added by this
RC:

```python
    assert result.stdout == "", (
        f"the sender must never write skip/signal narration to its own stdout "
        f"(BUG-0298 ADDENDUM) -- got: {result.stdout!r}"
    )
```

`SOP-core.md` ("Naming: describe behavior, not scaffolding or provenance")
forbids an internal bug identifier reaching a shipped artifact outside three
sanctioned exceptions — a usage example, a skip annotation citing an OPEN
follow-up bug, and an external constraint. A failure message is none of the
three: it is text an operator or agent reads at runtime, and it states process
history rather than the behaviour under test (which the rest of the same message
already states well). The hygiene lint does not catch it: on its test-tree
surface a bug-shaped token inside an ordinary string literal is exempt by design
(`team/scripts/lint_hygiene.py`, "Four scan surfaces"), an exemption written for
bug IDs used as test *data*, which this is not.
`python3 team/scripts/lint_hygiene.py --classes provenance` is clean.

### Finding 7: fix head 1 — render stderr never reaches the pane display

**Category:** pass

Exercised, not inferred. In a throwaway sandbox root (`env -u PGAI_TASKS_DIR -u
PGAI_PROJECT_ROOT KANBAN_ROOT=$S PGAI_AGENT_KANBAN_ROOT_PATH=$S
PGAI_AGENT_KANBAN_TEMP_DIR=$S/tmp`), `cadence-loop.sh --interval 1 --
<renderer>` was run for four ticks with **stdout and stderr merged** — the single
surface a tmux pane actually paints. Result: four `RENDERED-COLUMN-LINE` rows and
zero `dashboard-snapshot` bytes on the display stream, with the narration present
in `$S/tmp/dashboard/logs/unknown-pane.log`. The merged-stream capture matters: a
`stderr=DEVNULL` capture passes against the pre-fix code too, which is the trap
CODER-019's own test notes call out.

Guard behaviour was checked at its boundary as well: the loop resolves the log
target once and probes it with `: >>"$path"`, falling back to `/dev/null` when
the probe fails, so an unwritable temp root loses the narration and never the
columns; and the pane-label sanitiser maps a non-tmux identity to `unknown-pane`
(observed above) rather than producing `.` or `..`.

### Finding 8: fix head 2 — narration volume is split by miss reason

**Category:** pass

`dashboard_snapshot_read` exit codes were observed directly: fresh → `0`,
present-but-past-TTL → `2`, missing → `1`. Driving `dashboard_snapshot_render`
through all four states in the sandbox produced exactly one line each, all on
stderr, with stdout byte-identical in every path:

```
[dashboard-snapshot] column-render K1: no fresh snapshot (missing or unreadable) -- rendering directly
[dashboard-snapshot-debug] column-render K1: snapshot older than 1s -- rendering directly (routine expiry)
[dashboard-snapshot] column-render K2: failed to write snapshot for twin readers
```

The write-failure line (produced by chmod-ing the snapshot directory read-only)
is new behaviour this RC adds — the previous code swallowed a failed cache
refresh with `|| true`. The routine-expiry case is the one demoted to the
`-debug` tag, which is the head's stated intent.

**Twin parity and cache disposability** (the bundled item's second acceptance
bullet): three successive renders of a key whose renderer emits a fresh random
value returned byte-identical output (`DATA-11500` three times), proving the
cache-hit path rather than two scans coincidentally agreeing; deleting the
snapshot directory mid-session left the next render working and the display
clean.

### Finding 9: fix heads 3 and 4 — sender narration and census-scoped signalling

**Category:** pass

A private tmux server (socket inside the sandbox) was started with a four-pane
`main` window matching the census layout, and
`activation-hook-sender.sh --session … --window main --kanban-root $S` was run
with no arm markers present. Results:

- sender **stdout 0 bytes, stderr 0 bytes** — nothing exists for `run-shell` to
  paint onto whichever pane the operator switched to.
- `main.0.log`, `main.2.log`, `main.3.log` each received the
  `activation-hook-sender: skipping pane …: not armed yet…` line.
- `main.1` — the census's `logs.sh` streaming pane — produced **no log file and
  no narration at all**, i.e. it was excluded from the target set before the arm
  check, which is head 4's "not signal targets at all".

**Window-switch storm** (the addendum's acceptance): 30 consecutive sender
invocations produced **0 bytes** of combined stdout+stderr.

**The dual-label arm check was exercised too.** Creating the marker under the
bare window-name key (`armed/<session>/main`, the convention `create.sh` uses for
its single-pane windows) caused pane `main.0` to be signalled — observed by the
pane dying under `USR1`'s default disposition in the un-trapped sandbox pane,
which is positive proof the signal was delivered rather than skipped. Reading
`create.sh` confirms the scope of that fallback is exactly right: of the 50
`dashboard_send_armed_keys` call sites only four use a bare window name
(`attention`, `metadata`, `agent-metrics`, `logs`), and all four are single-pane
windows, i.e. pane index 0.

**The exclusion list was checked against the census rather than taken on trust.**
`_sender_is_streaming_script` hardcodes `logs.sh|cron-tail.sh|debug-logs.sh`;
every other census pane script resolves to a `cadence-loop.sh`-wrapped command in
`create.sh`, and every pane — including the one-shot `suggested-commands.sh`
reference pane and the interactive shells — is dispatched through
`dashboard_send_armed_keys`, so no pane is left permanently unarmed and narrating
after this change. The hardcoded triple is a drift risk rather than a defect
today (see Caveats).

### Finding 10: documentation matches the shipped behaviour

**Category:** pass

`docs/dashboard.md` ("Shared render snapshots", "Where pane narration goes") and
`docs/DASHBOARD-PANES.md` ("Pane debug logs") were read in full and checked claim
by claim against the merged code and the sandbox runs above: the log path shape
`<temp-root>/dashboard/logs/<window-name>.<pane-index>.log`, the
`unknown-pane.log` fallback outside tmux, the underscore substitution for unsafe
filename characters, the create-on-loop-start and die-with-the-session lifecycle,
the "cannot write the log → still renders" fallback, the three streaming scripts
excluded from signalling, and the narration reference table — all four table rows
match the exact strings `snapshot.sh` and `activation-hook-sender.sh` now emit.
The doc correctly retires the bug report's superseded "no producer exists"
diagnosis by documenting the cooperative cache instead, which is what the
doc-truth head asked for, and the added sections disturb no CENSUS-GENERATED
block.

### Finding 11: requirements acceptance criteria

**Category:** pass

- "All Python files compile cleanly" — `git ls-files '*.py' | xargs -n50 python3
  -m py_compile` → clean, exit 0.
- "All bash files pass syntax check" — `bash -n` over all 280 tracked `*.sh`
  files → 0 failures.
- "Each bundled item is addressed in the implementation" — the bundled item's
  four fix heads plus its doc-truth acceptance bullet are each verified above
  (Findings 7-10). The item is addressed.

### Finding 12: release-state and version ordering

**Category:** pass

`release-state.md` holds the four-field schema: `Active RC v2.23.5`, `RC Opened
At 2026-09-16T12:10:25+00:00`, `RC Opened By Task CM-20260916-021-open-rc-2-23-5`,
`Last Released v2.23.4` — the last of those written by `cm/release.sh` by design,
not drift. `semver_gt "v2.23.5" "v2.23.4"` (from `team/scripts/lib/semver.sh`,
never an inline compare) is true, and `pp_last_released_version
"pgai-agent-kanban"` returns `v2.23.4`. The `ai_rc/v2.23.5` branch exists and
matches the recorded Active RC — no state drift.

### Finding 13: skip-cites-a-real-bug gate

**Category:** pass

No placeholder skip shapes (`BUG-SKIP`, `skip` near `BUG-<letters>`) exist in
`team/tests/`. `bash team/scripts/lint_skip_bug_gate.sh` → "OK — all skip
annotations are compliant", 0 missing bug files.

### Finding 14: synthetic release simulation not applicable

**Category:** pass

Step 6.8 applies only when the diff touches release-lifecycle files. This RC's
diff touches none of `cm/release.sh`, `cm/open-rc.sh`, `cm/cancel-rc.sh`,
`team/scripts/lib/project_paths.sh`, or the open/close cycle — it is confined to
`team/scripts/dashboard/`, two test modules and two docs. Skipped with this note.
(`test_cm_release_synthetic.py` ran inside the gated integration suite and passed
regardless.)

## Lint Findings (non-blocking)

None. `$PGAI_AGENT_KANBAN_TEMP_DIR/lint/findings-unit.json` and
`findings-integration.json` were both written by their runners and each holds
0 findings.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=9d freshness=stale — the docker/claude-abliterated, podman/claude-abliterated and podman/rocky9 pairs failed cold-build at v2.9.20 and that run filed BUG-0167-cold-build-claude-abliterated-failure (since closed); the record is stale on both counts — 9 days exceeds the 7-day window, and v2.9.20 is not the v2.23.5 under verification — so nothing is currently known about whether this tree's images build.

## Autonomous criterion (Step 6.12)

Autonomous operation criterion: Manual interventions required: none.

1. **Tasks force-promoted from WAITING to BACKLOG by hand?** NO. The only
   promotion recorded in this task's own `status.md` names the wake script
   ("Promoted from WAITING to BACKLOG by wake script: prerequisites satisfied"),
   and every prerequisite reached `DONE` under its own agent.
2. **`release-state.md` edited outside the CM scripts?** NO. Its mtime
   (`2026-09-16 12:10:25.669`) is identical to the `RC Opened At` value it
   carries, i.e. untouched since `cm-open-rc.sh` wrote it, and
   `CM-20260916-021-open-rc-2-23-5` is `DONE`.
3. **Scripts patched mid-build?** NO. The four commits on the RC are the tickets'
   own deliverables (`team/scripts/dashboard/*` is the subject of the bundled
   item), each landing as a `--no-ff` merge of its named feature branch, in
   ticket order, between 12:22 and 13:00 — no out-of-scope tooling commit appears
   in `git log ai_main..ai_rc/v2.23.5`.
4. **Queue files edited by hand?** NO. Every line in `tasks/queues/*.md` matches
   the canonical `- [<marker>] TASK-ID` shape with a legal marker; this task's own
   entry reads `[A]` and `CM-20260916-024-release-2-23-5` reads `[W]`, which is
   what the wake reconciliation writes.

No intervention was found, so none indicates a defect.

## Caveats

- **The exclusion list is a hardcoded triple, not a census-derived property.**
  `_sender_is_streaming_script` names `logs.sh`, `cron-tail.sh` and
  `debug-logs.sh` literally, while fix head 4 asks the sender to consult the pane
  census and signal only panes that run the pane loop. Today the two agree —
  verified script by script against `create.sh` — so this is not a finding; but
  the census carries no "runs under cadence-loop" attribute, so a future pane
  added outside the loop would silently rejoin the signal target set. A census
  field would make the property checkable rather than remembered.
- **Unarmed cadence panes narrate once per window switch, not once per session.**
  The addendum's acceptance bullet ("skips logged once per non-loop pane per
  session at most") is met for non-loop panes, which now narrate not at all. A
  pane that is under `cadence-loop.sh` but not yet armed still logs a skip on
  every switch while it remains unarmed; in production that window is the short
  interval before `create.sh`'s all-armed handshake completes, so it is bounded in
  practice. Recorded as an observation, not a finding.
- **A bare `/tmp` path literal appears in new test code.**
  `team/tests/unit/test_dashboard_activation_hook_lifecycle.py` passes
  `--kanban-root /tmp/kanban-root-unused` in three places. Its own comments
  explain the value is never read from disk (the sender resolves through
  `PGAI_AGENT_KANBAN_TEMP_DIR`, which the test sets), and the gated runners'
  `bare_tmp_cleanliness` check — which looks for residue actually created — is
  green. Noted because the tree ran a whole ticket on eradicating `/tmp`
  literals; the project's own gate accepts this one.
- **Test fidelity.** The two cadence-loop regression tests capture the renderer's
  stdout and stderr merged, which is the real breaking condition (a tmux pane has
  one display surface) rather than a sanitised proxy; the sender storm test drives
  the real script against a real private tmux server. No mocked-away condition was
  identified. The one fidelity concern found is Finding 4's ambient-environment
  inheritance, filed as a bug rather than left here.
- **Prior-run artifacts.** None existed for this task; `artifacts/` was empty at
  start. No prior verdict was reused; every suite and probe in this report was run
  fresh in this session.
