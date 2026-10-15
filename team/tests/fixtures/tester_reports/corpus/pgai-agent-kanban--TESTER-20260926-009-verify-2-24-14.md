# Verification Report: rc/v2.24.14

## Summary

Verified release candidate `ai_rc/v2.24.14` of pgai-agent-kanban against
`requirements/v2.24.14-bugfix-bundle-20260926.md` — a six-item BUG bundle
(BUG-0383 through BUG-0388) implemented by five CODER tickets and one WRITER
ticket. Verification ran in the detached read-only worktree at
`/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260926-009-verify-2-24-14`
(HEAD `c6376af`, identical to `ai_rc/v2.24.14`). Work covered: RC-intact
pre-flight, release-state sanity, the full `ai_main..ai_rc/v2.24.14` diff
(19 files, +1273/-86), per-bug acceptance-criteria execution, behavioral
failure-mode probes against all three guard-shaped changes, both gated
runners, the lint findings files, the deep-verify ledger, and the autonomous
operation criterion.

Five of the six bundled items are implemented correctly and verified
behaviorally. The sixth — BUG-0388's git-boundary-guard noise reduction —
achieves its stated goal but introduces a regression that lets a whole class
of non-CM remote git invocations bypass the guard silently, demonstrated
directly against both the RC and the `ai_main` baseline.

## Recommendation
SHIP-WITH-SERIOUS-CONCERNS

## Systemic Risk
high

## Verification Environment

- Worktree: `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260926-009-verify-2-24-14`
- `git rev-parse HEAD` = `c6376afcbc00b3f642e2d29022bac90b70f21162`
- `git rev-parse ai_rc/v2.24.14` = `c6376afcbc00b3f642e2d29022bac90b70f21162` (match)
- `git status --porcelain` — empty; `git stash list` — empty
- Requirements doc: `projects/pgai-agent-kanban/requirements/v2.24.14-bugfix-bundle-20260926.md`
- Ambient session held `GIT_GUARD_REAL_GIT=/usr/bin/git` with the guard shim
  first on `PATH`, i.e. exactly the environment BUG-0383 is about.

## Pre-flight (Step 2 / Step 3)

Two `ai_feature/*` branches survive locally,
`CODER-20260923-038-dashboard-pane-race` (task folder no longer present) and
`CODER-20260923-041-provenance-lint-sweep` (`## State: WONT-DO`). Neither
belongs to a task whose `status.md` reads `DONE`, so neither is a Step 2
anomaly. All six of this cycle's feature branches are absent locally (the
normal post-merge shape) and all six merge commits are present on the RC:
`2dd0c90`, `52c6029`, `19752dd`, `356b334`, `522c269`, `c6376af`.
13 commits on `ai_main..ai_rc/v2.24.14`. **Category: pass.**

## Release state (Step 6.1)

`release-state.md` holds the four canonical fields. `## Active RC` = `v2.24.14`,
`## RC Opened At` = `2026-09-26T01:02:17+00:00`, `## RC Opened By Task` =
`CM-20260926-008-open-rc-2-24-14`, `## Last Released` = `v2.24.13`
(well-formed). `pp_last_released_version "pgai-agent-kanban"` returns
`v2.24.13`, and `semver_gt v2.24.14 v2.24.13` succeeds, so the RC version is
strictly greater than the last release. **Category: pass.**

## Gated suites (Step 5a / 6.6 / 6.7)

Both runners invoked with `--coverage` from the dev-tree root inside the
guarded agent session; output teed to `/tmp/pgai_kanban_tmp/tester/unit.log`
and `.../integration.log`.

- `UNIT_EXIT = 0` — `4981 passed, 31 skipped in 770.52s`. Every lint
  pre-flight green, including `lint_skip_bug_gate`, `lint_hygiene`
  (0 findings), `lint_gate_census_compliance`, `runnable_remedy`,
  `docs_citation_manifest`, and the governance byte-budget check.
- `INTEGRATION_EXIT = 0` — `548 passed, 1 skipped in 693.56s`. Lint
  pre-flight green; findings file empty. `test_cm_release_synthetic.py` (17
  tests) and `test_dashboard_activation_hook_lifecycle.py` (2 tests, including
  the new overlap test) both green.

**Procedural note on the first attempt.** The first unit-runner invocation was
launched inside the agent harness's own process group and was SIGTERM'd at
roughly 8 % of collection; the wrapper recorded exit 1 while the `setsid`-
detached pytest child survived as an orphan. That exit code was discarded, the
orphan terminated, and the runner re-launched fully detached via `setsid nohup`
so the runner's own exit code is authoritative. The `0` above is from the
complete, uninterrupted run. This is a property of the verification harness,
not of the RC.

## Coverage (Step 5b — informational only)

Read from `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/unit.json`, field names
verified against the generated file before extraction.

- Unit: **statement/line coverage 76 %**, **branch coverage 63 %**
  (blended `percent_covered` 73 %; 12965/16971 statements, 3238/5122 branches).
- Integration: **statement/line coverage 11 %**, **branch coverage 9 %**
  (blended 11 %). The integration suite exercises subprocess entry points, so
  in-process coverage instrumentation sees very little of the tree; this is the
  suite's normal shape here, not a change.

Coverage did not enter the verdict.

## Lint Findings (non-blocking)

None. `${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-unit.json` and
`.../findings-integration.json` were both present with `"findings": []`
(`schema_version: 1`).

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=18d freshness=stale — Stale on both counts: the record is 18 days old (outside the 7-day window) and names v2.9.20 rather than the v2.24.14 under verification; the failure it records (the `claude-abliterated` Dockerfile's `FROM` arity under both docker and podman, plus a `podman/rocky9` `COPY --chmod` step) was already filed and closed as BUG-0167 (`## Status: done`), so nothing is re-filed here and nothing is known about whether this RC's images build.

## Findings

### Finding 1: BUG-0388's unreachable-destination exemption lets a whole class of non-CM remote git invocations bypass the boundary guard

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** high (the git boundary guard is framework-wide infrastructure every non-CM agent in every project lane runs behind, and the bypass is silent — no refusal, no evidence record)
**Regression:** yes
**Integrity:** destructive
**Filed As:** BUG-0391 (Path C-A — filed as BUG: code defect / regression / test failure)

`CODER-20260926-006-guard-evidence-fixture-noise` (commit `494f9d8`, merge
`522c269`) added `_git_boundary_guard_is_unreachable_destination` to
`team/scripts/lib/git_boundary_guard.sh` and changed
`_git_boundary_guard_is_fixture_local` so that a **failed** `git remote get-url
<destination>` probe returns "fixture-local, exempt" whenever the probe output
contains `no such remote` or `cannot change to`.

The destination token the probe is run against comes from
`_git_boundary_guard_find_destination`, which returns *the first
non-option-shaped argument after the subcommand*. For any `fetch`/`pull`/`push`
invocation where an option consumes a separate value ahead of the remote name
— `--depth N`, `--deepen N`, `-o <opt>` / `--server-option <opt>`, `-j N`,
`--upload-pack <cmd>`, `--receive-pack <cmd>`, `--shallow-since <date>`,
`--negotiation-tip <ref>` — that value is returned as the destination. It is
not a configured remote, so the probe fails with `No such remote`, and the new
code now reads that as "nothing can be reached" and **exempts the whole
invocation** — including the real, configured remote that is actually present
later in the same argv.

Before this RC, exactly that case was the documented safety property. The
header of `_git_boundary_guard_find_destination` still says so, verbatim, and
is now false:

```
# An option here that happens to consume a value this scan does not
# know about ... can at worst make this function return that value's own
# text as a false "destination" -- harmless, because a value that does not
# look like a real path or resolvable remote name simply fails the
# fixture-local check below and falls through to the ordinary refusal, per
# this guard's widen-inspection-never-widen-refusal design.
```

**Reproduction, run directly (not inferred).** A repository at
`$HOME/.claude/tester-probe-2-24-14/work` (outside
`$PGAI_AGENT_KANBAN_TEMP_DIR`) with `origin` configured to a bare repo at
`$HOME/.claude/tester-probe-2-24-14/upstream.git`, invoked through the
shim as `PGAI_AGENT_ROLE=coder`:

| argv | RC `ai_rc/v2.24.14` shim | `ai_main` shim |
|---|---|---|
| `git -C <repo> fetch origin` | refusal, exit 1, 1 record | refusal, exit 1, 1 record |
| `git -C <repo> fetch --depth 5 origin` | **no refusal, passed to real git, 0 records** | refusal, exit 1, 1 record |
| `git -C <repo> push -o ci.skip origin main` | **no refusal, real git ran the push** (`fatal: the receiving end does not support push options`), 0 records | refusal, exit 1, 1 record |

The third row is the decisive one: a non-CM role's `push` actually reached the
destination repository and was answered by its receive end. Nothing about that
invocation was refused and nothing was recorded, so TESTER Step 6.12
question (5) could not see it either.

**Integrity rationale.** Rated `destructive` rather than `none` because the
failed path *is* the guard's ref-mutating-operation refusal path: the defect's
own failure mode is an unblocked, unrecorded `push` from a role the boundary
exists to stop. No ref was actually moved during verification (the probe's
upstream rejected the push option), and `Regression: yes` because `ai_main`
refuses both shapes. A reader who judges `none` more apt should note that the
`Severity: high` rating and the reproduction above are unchanged either way.

**Why the suites stay green.** The ticket's own new coverage,
`team/tests/unit/test_git_boundary_guard.py::test_fetch_from_repo_with_configured_remote_outside_root_still_refused`,
exercises only `fetch origin --tags --quiet` — options *after* the remote name,
which the destination scan handles correctly. No test exercises an option that
consumes a separate value ahead of the remote name, which is precisely the
condition the change broke. This is a test-fidelity gap on the same defect, not
a separate finding.

**Suggested shape of the fix** (for CODER, not applied here): scope the
exemption to destinations whose *repository* is under the temp root, and/or
narrow `_git_boundary_guard_find_destination` to skip the known value-consuming
options so a false destination can no longer be produced; then restore the
header's stated invariant and cover both option shapes.

### Finding 2: BUG-0383 acceptance criterion 2 is unmet — the guard test module is still not self-sufficient

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated to one test module; the authoritative gated runner is green)
**Filed As:** BUG-0392 (Path C-A — filed as BUG: code defect / regression / test failure)

BUG-0383 lists four acceptance criteria. Three are met and verified:

- `bash team/scripts/run-unit-tests.sh --coverage`, invoked from inside this
  agent wake session with `GIT_GUARD_REAL_GIT=/usr/bin/git` exported and the
  guard shim first on `PATH`, **exits 0** (4981 passed, 31 skipped). Met.
- A test pins the invariant: `team/tests/unit/test_gated_runner_guard_env_scrub.py`
  adds four tests, including one that actually executes each runner's scrub
  statement and asserts both variables are clear, and one asserting both
  runners scrub the identical set. Met.
- `bash team/scripts/run-integration-tests.sh --coverage` — see the integration
  result below. Met.

Criterion 2 — "`python3 -m pytest tests/unit/test_git_boundary_guard.py`
passes 36/36 both with **and without** those two variables set in the ambient
environment" — is **not** met. Measured in the RC worktree:

```
env GIT_GUARD_REAL_GIT=/usr/bin/git PGAI_GIT_GUARD_DEPTH=1 \
    python3 -m pytest tests/unit/test_git_boundary_guard.py -q -p no:cacheprovider -p no:randomly
  -> 25 failed, 14 passed

env -u GIT_GUARD_REAL_GIT -u PGAI_GIT_GUARD_DEPTH \
    python3 -m pytest tests/unit/test_git_boundary_guard.py -q -p no:cacheprovider -p no:randomly
  -> 39 passed
```

The remedy chosen was the runner-level scrub only; the bug's "Files Involved"
also named per-test fixture clearing inside the module ("alternatively (or
additionally) ... makes the module self-sufficient rather than dependent on the
runner"), and that half was not done.

**Not a regression.** The same module on `ai_main` (archived to
`/tmp/pgai_kanban_tmp/tester/maintree-2414`) reports `25 failed, 11 passed`
with the two variables set — the identical failure count. The RC did not make
direct-pytest behaviour worse; it left this criterion unaddressed. Without the
variables the RC is strictly better: `39 passed` versus `ai_main`'s
`1 failed, 35 passed`.

Impact is confined to ad-hoc debugging: an agent or operator who runs that one
module directly inside a guarded session sees failures that say nothing about
the code. The workaround is in the runner's own replay line, which now prints
`-u GIT_GUARD_REAL_GIT -u PGAI_GIT_GUARD_DEPTH` explicitly.

### Finding 3: BUG-0384 — dashboard switch-storm pane death, fixed and verified

**Category:** pass

`CODER-20260926-003-dashboard-pane-race` (commit `9507ec4`, merge `19752dd`)
identified and closed a real ordering defect. The operator-shell pane and the
terminal window's interactive pane each end their first command in a bare
`exec bash` / `exec bash -l`, which this codebase already knew (from its own
`/proc/<pid>/status` `SigIgn` finding) drops the ignore trap the wrapped
preamble installed. The armed marker, however, was touched by the pre-`exec`
command, so `create.sh`'s all-armed handshake and every later
session-window-changed hook firing could observe "armed" while the process that
actually stays resident was unprotected.

The fix adds `dashboard_pane_wrap_cmd_no_marker` (trap install only, no marker
touch) in `team/scripts/dashboard/lib/pane_readiness.sh`, a `--defer-marker`
mode on `dashboard_send_armed_keys`, and folds the marker touch into the SAME
typed re-arm line as the trap reinstall at both call sites — so the marker's
existence can never outrun the resident process's own trap.

Acceptance criteria, each checked:

- 25 consecutive isolated runs — exceeded. `tasks/CODER-20260926-003-dashboard-pane-race/logs/switch-storm-25-consecutive-runs.log`
  records 30/30 `=== run N/30 ===` / `run N passed` pairs, zero failures,
  ending `ALL 30 RUNS PASSED`. Counted independently: 30 run markers, 30
  `passed` lines, 0 `failed`.
- Pane-id set unchanged, no new ids — the existing assertion in
  `test_thirty_immediate_window_switches_leave_every_pane_alive` is untouched
  (switch count, absent warm-up sleep, and set comparison all preserved, as the
  bug required).
- Per-pane work idempotent under overlap, covered by a test rather than prose —
  `team/tests/integration/test_dashboard_activation_hook_lifecycle.py::test_signaling_the_same_pane_twice_in_overlap_leaves_it_alive`
  added; it fires real overlapping `activation-hook-sender.sh` invocations
  against one window.
- No load generator, no multi-session spawn — `grep -nE 'yes|dd |stress|while
  true'` over the diff finds none; the new test works within one session in a
  private socket.
- `run-integration-tests.sh --coverage` exit 0 — see the suite result.

I did not independently replay the 30-run storm loop. BUG-0384's reproduction
contract explicitly bounds host load (a prior attempt at this fix pinned every
core and triggered a provider throttle), the defect is non-deterministic, and
the named test runs once inside the gated integration suite that is the
authoritative signal. CODER's log is the evidence for the 25-run clause; that
is recorded here as the basis rather than presented as my own measurement.

### Finding 4: BUG-0385 — bundle close no longer promotes unfixed items, verified behaviorally

**Category:** pass

`CODER-20260926-004-bundle-close-carry-unfixed` (commit `747b37a`, merge
`356b334`) gates the `## Bundled Items` enumeration path in
`team/scripts/cm/promote_bundled_items.py` on a DONE task README reference, the
same safe bound the task-scanning derivation path already used, and extends the
identifier vocabulary to `PRIORITY-NNNN` alongside `BUG-NNNN`.

Exercised directly with a synthetic mixed bundle under
`/tmp/pgai_kanban_tmp/tester/promoprobe-2414` — two items at `## Status:
running`, one referenced by a `DONE` task README, one referenced only by a
`WONT-DO` task README:

```
  promoted (running -> done): BUG-9001-fixed-item.md
  carried (no DONE task references it): BUG-9002-withdrawn-item.md
  Bundled items: 1 promoted, 0 skipped (not running), 0 missing, 1 carried (unfixed)
--- Step 16b post-ship report: bugs still at 'running' for v9.9.9 ---
  Carried (not closed): 1 item(s)
    - BUG-9002-withdrawn-item.md
  promoted (running -> done): v9.9.9-bugfix-bundle-probe.md
exit=0
```

Resulting on-disk statuses: fixed item `done`, withdrawn item `running`
(unchanged from its prior value), bundle requirements file `done`. All five of
BUG-0385's acceptance criteria met, including the mixed-bundle unit test
(`team/tests/unit/test_promote_bundled_items_carries_unfixed.py`, five tests
covering mixed bundle, unchanged-fixed-item behaviour, the PRIORITY path,
a missing `tasks_dir` carrying everything rather than promoting blind, and
re-run idempotence).

**State-drift check on this very RC.** Because the gate is new, I confirmed
v2.24.14's own bundle will still close: every one of the six enumerated items is
named in a task README whose `status.md` reads `DONE` — BUG-0383 by
`CODER-20260926-002`, BUG-0384 by `-003`, BUG-0385 by `-004`, BUG-0387 by
`WRITER-20260926-005`, BUG-0388 by `-006`, BUG-0386 by `-007`. Nothing in this
bundle will be carried by accident.

### Finding 5: BUG-0386 — the hygiene lint is now held to its own rule, verified behaviorally

**Category:** pass

`CODER-20260926-007-lint-self-provenance-sweep` (commits `aeb1aac`, `29e602a`,
merge `c6376af`) removes `lint_hygiene.py` from its own `_FILE_WHITELIST`,
retains its three documented worked-example tokens as individual sidecar
allowlist entries with justifications, and reworded the offending docstring line
to drop the real ledger identifier.

- `git grep -nE 'BUG-[0-9]{4}' -- team/scripts/lint_hygiene.py` on the RC
  returns three hits, all worked examples: `BUG-9999` (line 140), `BUG-9001`
  (line 170), and `assert bug_id == "BUG-0001"` (line 905, a usage example of a
  test assertion). No real ledger citation remains.
- The behavioural statement is preserved: lines 183-184 now read "(a separate
  bug-backlog item already tracks them by name for a follow-on ticket to reword
  or allowlist)".
- The whole-file exclusion is narrowed to nothing, with a new "Self-scanning"
  note beside `_FILE_WHITELIST` explaining why the basename is deliberately
  absent, plus a matching module-docstring update.
- `python3 scripts/lint_hygiene.py --classes provenance` reports `OK — 0
  findings across 9 scan director(y/ies)` tree-wide.
- `team/scripts/hygiene-lint-allowlist.json` parses (88 entries, up from 85).

**Guard fired when triggered.** BUG-0386's reproduction step 3 was the "probe
comment is invisible" proof. Replayed against a copy of the RC tree at
`/tmp/pgai_kanban_tmp/tester/hygprobe-2414`: appending
`# BUG-0377 probe citation of a real ledger identifier` to
`team/scripts/lint_hygiene.py` now produces

```
  - team/scripts/lint_hygiene.py:2155: [BUG-ID] provenance reference 'BUG-0377' in code
```

with a runnable remedy. The self-blindness the bug describes is gone.

### Finding 6: BUG-0387 — the halt-trigger count claim is gone from the troubleshooting doc

**Category:** pass

`WRITER-20260926-005-halt-trigger-count-docs` (commit `f55eed7`, merge
`2dd0c90`) replaced `docs/operator-troubleshooting.md` line 206's "one of the
eight enumerated in `docs/OPERATIONS.md`" with "one of those enumerated in
`docs/OPERATIONS.md`", and line 448's "the eighth HALT trigger" with "one of
CM's autonomous HALT triggers". Both edits take the bug's own advice — point at
the enumerated list without restating its length, so the sentence cannot go
stale again.

- `grep -rn 'eight' docs/` surfaces no remaining claim about the number of HALT
  triggers (the remaining hits are the dashboard's eight board columns, the
  workflow engine's eight hook stubs, and a tmux window-index example).
- `docs/OPERATIONS.md` still says nine in all three places; the sentence still
  routes the reader to that enumerated list.
- Unit suite exit 0, including the doc-truth and citation checks.

### Finding 7: BUG-0388 — the fixture-noise reduction itself works as specified

**Category:** pass-with-caveat

The mechanism BUG-0388 asked for behaves correctly. Probed directly against both
the RC shim and an `ai_main` copy, with a fixture repository under
`$PGAI_AGENT_KANBAN_TEMP_DIR`:

| case | RC shim | `ai_main` shim |
|---|---|---|
| no `origin` configured, `fetch origin --tags --quiet` | pass-through, 0 records | refusal, 1 record |
| `-C <dir>` already torn down | pass-through, 0 records | refusal, 1 record |
| `origin` resolving under the temp root (BUG-0388 criterion 3) | pass-through, 0 records | pass-through, 0 records |

The exit code on the first two changes from 1 (guard refusal) to 128 (real git's
own diagnostic), which is exactly the stated premise — real git fails
identically without touching a network — and production call sites for these are
best-effort (`2>/dev/null || true`).

**Caveat 1 — criterion 1 is not verifiable from this environment.** "A full
`run-unit-tests.sh` and `run-integration-tests.sh` under a non-CM role leave
zero records in the task's `logs/git-boundary-guard.jsonl`" cannot be checked
here: the shim on `PATH` during a wake session is the *live install's*
(`$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/wake/git-guard/git`), which still carries
`ai_main`'s guard until this release is installed. This cycle's task folders
show the pre-fix shape accordingly — 11 records for `CODER-20260926-002`, 3 for
`-003`, 19 for `-004`, 8 for `WRITER-20260926-005`, 8 for `-007` — all naming
fixture repositories under the temp root. Notably `CODER-20260926-006`, the
fixing ticket, has no evidence file at all. The mechanism probe above is the
substitute evidence; the criterion should be re-checked on the first cycle after
v2.24.14 is installed.

**Caveat 2 — one record class survives the fix by design.** My own task folder
and `PM-20260926-001`'s each hold one record of the shape
`git -C $HOME/develop/pgai-agent-kanban fetch origin --tags --quiet` —
production best-effort code running against the *real* dev tree, where `origin`
resolves to a genuine remote outside the temp root. That is a correct refusal
and will keep being recorded after v2.24.14 ships, so Step 6.12 question (5)
will continue to answer YES on cycles where any non-CM role calls
`pp_last_released_version` and friends. BUG-0388's framing ("a healthy cycle is
supposed to leave none") is therefore only partly achieved. Recorded as a
caveat, not a separate finding: the records are correct behaviour, and the
residual signal problem is a smaller version of the one BUG-0388 already
describes.

The regression this ticket introduced is Finding 1, filed separately.

### Finding 8: Requirements-doc acceptance criteria

**Category:** pass

The bundle's own three criteria:

- *Each bundled item is addressed in the implementation* — all six are addressed
  (Findings 2-7). Five are fully met; BUG-0383 is met on three of four criteria
  (Finding 2).
- *All Python files compile cleanly* — `python3 -m py_compile` on every changed
  `.py` file (`promote_bundled_items.py`, `lint_hygiene.py`, and the five test
  modules) succeeds. The gated runners' own breakage-class pre-flight is green.
- *All bash files pass syntax check* — `bash -n` passes on all seven changed
  shell files (`dashboard/create.sh`, `dashboard/lib/pane_readiness.sh`,
  `lib/gated_runner_lint.sh`, `lib/git_boundary_guard.sh`,
  `lib/wake_guard_wiring.sh`, `run-unit-tests.sh`, `run-integration-tests.sh`).

### Finding 9: Constraint and standards spot-checks (Steps 6.9, 6.10, 6.13, 6.14)

**Category:** pass

- **6.9 negative checks** — `set -euo pipefail` still present in every changed
  runner and in `dashboard/create.sh`; no hardcoded secret, token or credential
  in the diff; `hygiene-lint-allowlist.json` parses; every added `BUG-`/task-ID
  token in the diff is in the fictitious example range (`BUG-9001`..`BUG-9107`,
  `PRIORITY-9104`/`-9105`, `CODER-20260926-10x`) or a documented pattern
  example, consistent with the naming rule's usage-example exception, and
  `lint_hygiene` confirms 0 provenance findings tree-wide.
- **6.10 prerequisite graph** — acyclic; `CM-20260926-008-open-rc-2-24-14` is the
  sole root for all five implementation tickets; `-007` correctly depends on the
  five preceding tickets; `TESTER-...-009` depends on all six; `CM-...-011`
  depends on all six plus TESTER. Every referenced task ID resolves to an
  existing folder. Sequence numbers 001-011, no collisions.
- **6.13 skip-cites-a-real-bug** — `lint_skip_bug_gate` green in the runner
  pre-flight ("0 missing files", "all skip annotations are compliant"). Manual
  confirmation: the only `BUG-NNNN`-in-skip-annotation hits in the test tree are
  inside `team/tests/unit/test_skip_bug_citation_scan.py`'s own synthetic
  corpus, which is the gate's fixture data. No placeholder shape
  (`BUG-SKIP`, `BUG-<letters>`) appears in a real skip.
- **6.14 coding standards** — no new script under `team/scripts/` (so no
  missing `--help`); no new comment, docstring, help line or log line citing a
  real bug, task or version identifier; no new operational value hardcoded where
  an operator would want it configurable (the two scrubbed variable names are
  part of the guard's own contract, documented in both runners and in
  `GATED_RUNNER_REPLAY_ENV_NAMES`). One borderline observation, not a finding:
  `dashboard_pane_wrap_cmd_no_marker` duplicates `dashboard_pane_wrap_cmd`'s
  `printf` of the trap preamble rather than factoring it out; at two lines with
  a long header explaining why the two must stay distinguishable, that is a
  defensible call.

### Finding 10: Synthetic release simulation (Step 6.8)

**Category:** pass

The diff touches a release-lifecycle file (`team/scripts/cm/promote_bundled_items.py`,
the cm-release Step 16b helper), so the step applies.
`team/tests/integration/test_cm_release_synthetic.py` ran green inside the gated
integration suite (8 tests passed), and the neighbouring release-mechanics
integration modules — `test_cm_release_known_issue_carry_forward.py`,
`test_cm_release_operator_force_override.py` — also passed. No real branch was
touched: the whole worktree remained clean throughout verification
(`git status --porcelain` empty at the end).

## Autonomous criterion (Step 6.12)

1. **Tasks force-promoted from WAITING to BACKLOG by hand?** NO. The only
   promotion record in this cycle is `TESTER-20260926-009`'s own
   `## Summary`: "Promoted from WAITING to BACKLOG by wake script: prerequisites
   satisfied."
2. **`release-state.md` edited outside the CM scripts?** NO. Its mtime is
   `2026-09-26 01:02:17`, identical to the `## RC Opened At` value it carries,
   i.e. the single write by `cm-open-rc.sh`.
3. **Scripts patched mid-build?** NO. All 13 commits on
   `ai_main..ai_rc/v2.24.14` are ticket commits and their `--no-ff` merges; the
   changes under `team/scripts/` are the ticket deliverables themselves, not
   out-of-band tooling patches.
4. **Queue files hand-edited?** NO. Markers match states throughout —
   `[A] TESTER-20260926-009-verify-2-24-14` for the WORKING verification task,
   `[W] CM-20260926-011-release-2-24-14` for the WAITING release task, `[x]` for
   the six completed tickets.
5. **A non-CM agent invoked a remote git operation?** **YES**, read from the
   evidence files rather than inferred. Records exist in six of this cycle's
   task folders: `CODER-20260926-002` (11), `-003` (3), `-004` (19),
   `WRITER-20260926-005` (8), `CODER-20260926-007` (8),
   `PM-20260926-001` (1), and `TESTER-20260926-009` (1).
   `CODER-20260926-006` has none.

   Every record carries a named role (none are `unknown`), every one is a
   refused attempt, and none changed ref state: 57 of the 58 are
   `fetch origin --tags --quiet` against pytest fixture repositories under
   `$PGAI_AGENT_KANBAN_TEMP_DIR`, and the two remaining
   (`PM-20260926-001`, `TESTER-20260926-009`) are the same best-effort tag fetch
   against the real dev tree at `$HOME/develop/pgai-agent-kanban`, issued
   by production code that degrades cleanly when origin is absent. **Rated
   `low`** per the reference's rule (refused, read-only, no ref moved), so it
   does not move the recommendation, which Finding 1 sets.

   The fixture-repository class is exactly what BUG-0388 describes and what this
   RC fixes; those records are pre-fix because the live install still runs
   `ai_main`'s guard (Finding 7, caveat 1). The two real-dev-tree records are
   correct refusals that will persist after the fix ships (Finding 7, caveat 2).

**Autonomous operation criterion: Manual interventions required: none.** The
question (5) YES is a guarded, refused boundary attempt, not a manual
intervention; no human touched a `status.md`, a queue file, `release-state.md`,
or a script during the build.

## Recommendation rationale (Step 8)

The five-item `PASS` checklist fails on one line: a `bug` finding was recorded
in Step 7. Both gated runners exited 0, no Step 6.12 intervention indicates a
defect, and the lint findings files are empty — so this would otherwise be a
clean `PASS`.

Finding 1 is not a minor or isolated defect. It is a silent bypass of the one
mechanism enforcing the framework's "CM is the only toucher of any remote"
boundary, it was introduced by this RC (the `ai_main` baseline refuses both
argv shapes), and it is invisible to the evidence file that verification itself
reads to answer whether the boundary held. A demonstrated non-CM `push` reaching
its destination unrefused and unrecorded meets the
`SHIP-WITH-SERIOUS-CONCERNS` bar — "a security defect" and "an autonomy failure
making future cycles unreliable", since Step 6.12 question (5) can no longer be
trusted to see a crossing in that class.

Against that: no production call site in the tree uses an option that consumes
a separate value on a `fetch`/`pull`/`push`, so nothing is crossing the boundary
today; the fix is small and localised; and every other item in the bundle landed
correctly. CM may reasonably decide this ships as a known issue with the
follow-up bundled next — that is CM's policy call, not a softening of the
rating. The recommendation is informational input, not a veto, and BUG-0391
enters the next iteration's bundle either way.

## Caveats

- **The first unit-runner invocation was killed by the verification harness**,
  not by the RC. Details under "Gated suites"; the reported exit 0 is from a
  complete detached re-run.
- **BUG-0388's criterion 1 is not checkable from a pre-install environment.**
  The wake session's `PATH` carries the *live install's* guard shim, which is
  still `ai_main`'s until v2.24.14 is installed, so a gated-suite run here
  necessarily still writes fixture records (my own task folder went from 1 record
  to 12 across the two suites). The mechanism was verified directly instead
  (Finding 7). Worth re-checking on the first cycle after install.
- **Deep-verify has never been run against anything close to this RC.** The
  newest ledger record is 18 days old and names v2.9.20; nothing is known about
  whether this tree's three Dockerfiles build. Not a finding, and it does not
  move the recommendation — recorded so the gap is visible rather than assumed
  green.
- **Two surviving `ai_feature/*` branches** predate this cycle
  (`CODER-20260923-038-dashboard-pane-race`, whose task folder is gone, and
  `CODER-20260923-041-provenance-lint-sweep`, `WONT-DO`). Neither is a Step 2
  anomaly, but both are unmerged branches carrying 8 and 17 unique commits
  respectively, sitting in the repository with no owning open task. An
  observation for an operator, outside this RC's spec.
- **Filed-bug slugs run slightly long.** BUG-0391's and BUG-0392's slugs are six
  and seven words against Step 10's 3-to-5-word guidance. Both satisfy the
  shared filename contract in
  `team/pgai_agent_kanban/lib/ledger_filename_contract.py`, and the names match
  what `claim_next_bug_id` recorded in `.bug-claims.log`, so they were kept
  rather than renamed out of sync with the claim log.
- **Test fidelity.** The RC's new coverage exercises real subprocesses and real
  tmux sessions rather than mocks in every case I read, with one concrete
  exception folded into Finding 1: the boundary-still-refused test asserts only
  the argv shape that still works. `test_gated_runner_guard_env_scrub.py` mixes
  two pattern-scan tests (scrub statement precedes the pytest launch; both
  runners scrub the same set) with two that actually execute — an acceptable
  balance, since the ordering property it scans for is not otherwise observable.
