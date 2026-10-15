# Verification Report: rc/v2.23.15

## Summary

Verified release candidate `ai_rc/v2.23.15` (commit `29680e2`) of pgai-agent-kanban
against `requirements/v2.23.15-bugfix-bundle-20260918.md`, a two-item BUG bundle:
`BUG-0326` (`refusal_ledger_record_best_effort` aborts its `set -e` caller when the
ledger write fails) and `BUG-0327` (the gated runners' `Replay this exact run:` line
carries only the pytest argv, so a paste replays under the operator's ambient
environment). Verification ran in the detached read-only worktree at
`/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260918-028-verify-2-23-15`,
HEAD verified equal to `ai_rc/v2.23.15`. Both fixes were exercised behaviorally in
both directions — the original failure mode reproduced against `ai_main` code and
shown healed on the RC. Both gated runners were run with `--coverage`.

(Report written incrementally; sections below are appended as evidence accumulates.)

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Gated Suite Results

| Suite | Command | Exit | Result |
|---|---|---|---|
| unit | `bash team/scripts/run-unit-tests.sh --coverage` | `UNIT_EXIT=0` | 4738 passed, 31 skipped in 927.05s |
| integration | `bash team/scripts/run-integration-tests.sh --coverage` | `INTEGRATION_EXIT=0` | 515 passed, 1 skipped in 638.94s |

Both gated runners exited 0, lint pre-flight included. Full output captured at
`/tmp/pgai_kanban_tmp/tester/unit.log` and `/tmp/pgai_kanban_tmp/tester/integration.log`.
No test failed, so no `stale-assertion` or `pre-existing-bug` categorisation was
needed. The suite-pass precondition for `PASS` is therefore met; the recommendation
is `SHIP-WITH-CONCERNS` on the strength of the two Step-7 findings below, not on any
suite result.

## Coverage (informational only — never enters the verdict)

Read from the `totals` object of each runner's JSON, field names verified against
the generated files rather than assumed.

| Suite | Line (statement) % | Branch % | Blended (`term` `TOTAL`) |
|---|---|---|---|
| unit | 76.59 | 63.14 | 73.48 |
| integration | 9.87 | 7.97 | 9.43 |

## Findings

### Finding 1: replay-line environment parity misses attributed exports

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated to one helper function; no shipped runner triggers it today)
**Filed As:** BUG-0329 (Path C-A — filed as BUG)

`gated_runner_replay_command` in `team/scripts/lib/gated_runner_lint.sh` classifies
each family variable as exported-or-not by matching `declare -p`'s output against the
anchored literal `^declare -x `. Bash prints every attribute a variable carries in
that line, so an exported variable that is also read-only, integer, array, or
case-mapped prints `declare -rx` / `declare -ix` / `declare -ax` / `declare -lx` —
none of which match. The helper then classifies it as not-exported and emits
`env -u NAME`, telling the pasting shell to **unset** a variable the runner had
exported and pytest actually saw. That is the same class of environment-parity break
BUG-0327 was filed to close.

Observed directly on the RC:

```
$ bash -c 'source team/scripts/lib/gated_runner_lint.sh
           declare -rx PYTEST_ADDOPTS=--basetemp=/x
           gated_runner_replay_command "PYTEST_HERE"'
env -u PYTEST_ADDOPTS -u PYTHONHASHSEED ... PYTEST_HERE
      ^^^^^^^^^^^^^^^ exported by the caller, forced absent in the replay
```

Swapping `declare -rx` for a plain `export` produces the correct
`PYTEST_ADDOPTS=--basetemp=/x` assignment, isolating the attribute as the entire
cause. Bash's own output confirms the shapes the pattern misses:
`declare -rx A="1"`, `declare -ix B="2"`, against the single matching
`declare -x C="3"`.

**Not currently exercised.** Neither shipped runner declares any of the nine family
names with an extra attribute, so every replay line either runner prints today is
correct — verified end to end by pasting the unit runner's own line into a sabotaged
shell (see the Step 6.5 evidence below). It is rated `medium` in the filing under
`SOP-core.md`'s calibration for a real but unexercised defect in a shipped artefact:
nothing is failing today and not using it is itself the workaround. What makes it
worth filing rather than noting is that the RC documents this helper as the single
place a *new* runner variable gets registered ("add its name here once -- both
runners call `gated_runner_replay_command`, so one edit covers both"), so the next
variable added as `declare -ix`, or the next runner written against the helper,
silently reintroduces the defect the RC just closed.

This finding did not exist before this RC: the helper is new in v2.23.15.

### Finding 2: shared `--help` idiom exits 141 intermittently under `pipefail`

**Category:** bug
**Fix Effort:** medium (3-5 CODER tasks — eighteen call sites, or one convention plus a lint)
**Systemic Risk:** medium (demonstrably recurs: three closed bugs in this family already, one script each)
**Filed As:** BUG-0330 (Path C-A — filed as BUG)

The `doc_command_truth` quality lint fired two findings in the unit runner's
pre-flight, both against `team/scripts/dashboard/metrics.sh --help` exiting **141**
(128+13, SIGPIPE) while printing its help text completely and correctly.

Root cause, confirmed by direct exercise rather than inference:

```bash
# team/scripts/dashboard/metrics.sh:62, under set -euo pipefail (line 36)
grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -40
```

`metrics.sh` has 100 lines beginning with `#`. `head -40` closes the pipe after 40
lines, `grep` and `sed` take SIGPIPE mid-write, `pipefail` promotes 141 to the
pipeline's status, and `set -e` terminates the script before the `exit 0` on the
next line. Whether it fires is a scheduling race, which is why it reads as
"not reproducible" when checked once:

```
$ for i in $(seq 1 200); do bash team/scripts/dashboard/metrics.sh --help >/dev/null 2>&1; echo $?; done | sort | uniq -c
    198 0
      2 141
```

**Eighteen shipped scripts carry the idiom in its failing configuration** — `head -N`
below the file's own `^#` line count, with `pipefail` set. The filing tabulates all
eighteen with both numbers. Two further scripts carry the idiom without `pipefail`
and are not currently at risk.

This is a Step 6.11 spot-check finding in the "regressions of prior gaps" category,
not merely a lint complaint: `BUG-0270` (`pseudocron --help` never returns),
`BUG-0272` (`dashboard-create --help` SIGPIPE) and `BUG-0325` (this same lint
reported flaky against `dashboard/metadata.sh`) are all closed `done`, each having
repaired exactly one script while leaving the idiom in place. The correct reading is
that the check is honest and the scripts are wrong — `BUG-0325` closed on the
hypothesis that the *check* was flaky, which is why the family keeps returning.

It predates this RC — `metrics.sh:62` is untouched by the v2.23.15 diff — and the
help text is always printed correctly, so only the exit status is wrong and only
about 1 % of the time. Nothing functional depends on it; the cost is process noise
that has now consumed three bugfix cycles.

### Finding 3: BUG-0326 fixed and behaviorally verified

**Category:** pass

`refusal_ledger_record_best_effort` is now status-neutral under a `set -e` caller.
Evidence in the Step 6.5 section below: the original failure mode reproduces against
`ai_main`'s helper (exit 1, caller dead before its markers) and is healed on the RC
(`HELPER_RC=0`, caller continues, exit 0), end to end through the real caller
`pgai_emit_error_contract` both with and without `KANBAN_ROOT`. Mutation testing in
an isolated clone confirms the new regression test actually guards the fix.

### Finding 4: BUG-0327 fixed and behaviorally verified

**Category:** pass

Both runners now build their `Replay this exact run:` line through
`gated_runner_replay_command`. The unit runner's own printed line, pasted into a
shell exporting four sabotage values, reproduced the runner's environment exactly —
`KANBAN_ROOT` and `PYTHONHASHSEED` forced absent, `PYTEST_ADDOPTS` and
`PGAI_AGENT_KANBAN_ROOT_PATH` pinned to the runner's values, no sabotage value
reaching the child. The integration runner prints the same shape. Values containing
spaces and `$(...)` round-trip through `printf %q` without executing on paste.

### Finding 5: requirements-doc acceptance criteria

**Category:** pass

All three criteria met — see the acceptance-criteria table in the evidence log
below. 496 Python files compile clean; 281 bash files pass `bash -n`; both bundled
items are addressed and behaviorally verified.

## Lint Findings (non-blocking)

- `doc_command_truth` (severity `quality`) — 2 findings, both
  `team/scripts/dashboard/metrics.sh --help` exiting 141 while printing correct help
  text, cited from `docs/OPERATIONS.md:1648` and `docs/dashboard.md:185`. Grepped
  `projects/pgai-agent-kanban/bugs/` first: the three prior bugs in this family
  (`BUG-0270`, `BUG-0272`, `BUG-0325`) all read `## Status` `done`, so no open bug
  matched — **filed BUG-0330 (Path C-A)**. The runners' own aggregate filer did not
  file it; it files the hygiene-lint classes only (`BUG-0328`, hygiene `provenance`,
  filed by `hygiene-lint-aggregate-filer` before this run and unrelated to this RC's
  diff).
- No lint findings for the integration suite: `findings-integration.json` is present
  with an empty `findings` array.

Because the underlying defect is a genuine script defect and a regression of a
previously-closed gap rather than a style complaint, it is also carried as Step-7
Finding 2 above and does contribute to the recommendation. It is recorded here as
well so the lint-to-bug trail is complete.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=11d freshness=stale — Both freshness conditions fail (11 days exceeds the 7-day window, and v2.9.20 is not the v2.23.15 under verification), and the last recorded cold build was red on docker/claude-abliterated, podman/claude-abliterated and podman/rocky9, already filed and closed by that run as BUG-0167 (`## Status` `done`); nothing is currently known about whether this tree's images build, because `project.cfg` sets `[release] deep_verify = off`.

The tree ships three Dockerfiles (`docker/claude-abliterated`, `docker/debian`,
`docker/rocky9`) and does carry `team/scripts/deep-verify.sh`, so neither of Step
5d's two gap-earning faults applies. The lane was not run by TESTER, as Step 5d
requires; the line above is read through
`deep_verify_ledger_read_latest` in `scripts/lib/deep_verify_ledger.sh`, which
returned 0.

## Autonomous criterion

`Autonomous operation criterion: Manual interventions required: none`. All four
Step 6.12 checks answer NO; the table is in the evidence log below.

## Caveats

- **`git_stash`/pre-flight:** clean. The one surviving `ai_feature/*` branch,
  `ai_feature/CODER-20260917-020-agent-metrics-avg-wall`, belongs to a `WONT-DO`
  task and is therefore outside Step 2's `DONE`-task scan. It is not an ancestor of
  the RC. Flagged as an observation, not a finding: nothing in the governance stack
  requires a `WONT-DO` task's branch to be deleted, but it will keep appearing in
  future pre-flight scans.
- **Test coverage of the new helper's own guarantee.** The new tests cover the
  plain-export and never-exported classification paths and prove end-to-end parity
  across two shells. They do not cover the attributed-export path — which is exactly
  where Finding 1 lives — and they do not assert that a *runner's actually printed*
  line has parity (the static test only greps for the helper call). The behavioral
  coverage that exists is high-fidelity: real bash children, a real failing ledger
  write, two genuinely separate processes. Noted so the follow-up for BUG-0329 lands
  a test in the right place.
- **Self-inflicted state touch, disclosed.** One early probe of
  `pgai_emit_error_contract` was run with the inherited environment before the
  sandboxed procedure was applied, which caused one record to be written to the live
  `projects/pgai-agent-kanban/refusal-ledger.md`. That file is most-recent-only by
  design — every write replaces its entire content — so the effect is that the
  previous record was replaced by a `probe.key` record, and the next real refusal
  replaces it again. No other live state was touched; every subsequent probe used a
  throwaway root, and the two mutation tests ran in a real `git clone`, never in the
  worktree.
- **Operator-config observation, outside the RC.** `projects/pgai-agent-kanban/project.cfg`
  ends with a bare `defer_piggyback = any` line after the `[training]` header, so it
  parses into `[training]` rather than `[release]`, and `any` is not one of the
  `true`/`false` values `SOP-core.md` accepts for that key. This is per-install
  operator configuration, not version-controlled and not in the RC diff, so it is an
  observation for the operator rather than a verification finding against v2.23.15.
- **Step 6.8 skipped with a note** — the diff touches no release-lifecycle file.
- **`DEFER-LOW-BUGS` is armed** (`until=v2.24.0`). Both findings are rated `medium`,
  above the deferral level, so neither is affected by it. That is PM and CM's call,
  not TESTER's; recorded only so the queue behaviour is not a surprise.

---

## Evidence log (appended as collected)

### Step 2 — Pre-flight (RC intact)

- Scanned every task folder under `projects/pgai-agent-kanban/tasks/`. Every task
  whose `status.md` reads `DONE` and whose README names a `## Feature Branch` other
  than `none` has had that branch deleted locally after merge — the normal
  post-merge shape, which passes Check 1 and Check 2 trivially.
- The only surviving `ai_feature/*` branch is
  `ai_feature/CODER-20260917-020-agent-metrics-avg-wall`, whose task is `WONT-DO`,
  not `DONE`, so Check 1 does not apply to it. Recorded as an observation.
- Result: **pass**.

### Step 3 — Working tree

```
git rev-parse HEAD          -> 29680e2d3536289a322fe5e1ef34b7a997cbba36
git rev-parse ai_rc/v2.23.15 -> 29680e2d3536289a322fe5e1ef34b7a997cbba36
git status --porcelain      -> (empty)
git stash list              -> (empty)
```

Result: **pass**.

### Step 4 — Diff against `ai_main`

`git diff --stat ai_main..ai_rc/v2.23.15` — 7 files, +376 / -14:

| File | Kind |
|---|---|
| `team/scripts/lib/refusal_ledger.sh` | modified — BUG-0326 fix |
| `team/scripts/lib/gated_runner_lint.sh` | modified — new `gated_runner_replay_command` helper + `GATED_RUNNER_REPLAY_ENV_NAMES` |
| `team/scripts/run-unit-tests.sh` | modified — replay line + usage comment |
| `team/scripts/run-integration-tests.sh` | modified — replay line + usage comment |
| `team/tests/unit/test_refusal_ledger_best_effort_status_neutral.py` | new — 3 regression tests |
| `team/tests/unit/test_gated_runner_stated_test_order.py` | modified — 2 new tests |
| `docs/test-authoring-guidelines.md` | modified — replay-promise prose |

Six commits, all authored by the autonomous chain between 15:09 and 15:26 on
2026-09-18, three feature-branch merges with `--no-ff`. No commit outside the
three prerequisite tasks.

### Step 6.1 — `release-state.md` sanity

```
  Active RC            v2.23.15
  RC Opened At         2026-09-18T14:46:23+00:00
  RC Opened By Task    CM-20260918-027-open-rc-2-23-15
  Last Released        v2.23.14
```

All four fields present and well-formed. `pp_last_released_version "pgai-agent-kanban"`
returns `v2.23.14`; `semver_gt v2.23.15 v2.23.14` returns true. Result: **pass**.

### Step 6.5 — Behavioral verification, BUG-0326 (both directions)

Original failure mode, reproduced against `ai_main`'s helper (staged into a scratch
copy of `team/scripts/lib/`, sandboxed root, `KANBAN_ROOT` pointed at a
non-existent directory so `refusal_ledger_record` takes its own refusal branch):

```
set -euo pipefail; source <ai_main copy>/refusal_ledger.sh
refusal_ledger_record_best_effort 'k' 'm' 'r' 'demo-project' 'surf'
echo 'AFTER_CALL_REACHED'
-> OLD_EXIT=1, "AFTER_CALL_REACHED" never printed
```

Same script against the RC's helper:

```
-> HELPER_RC=0
   AFTER_CALL_REACHED
   NEW_EXIT=0
```

End-to-end through the real caller (`pgai_emit_error_contract`), RC tree, both
with and without `KANBAN_ROOT`: the three contract lines print, `AFTER_EMIT
reached` prints, exit 0 in both cases. Result: **pass**.

### Step 6.5 — Behavioral verification, BUG-0327 (production-shape input)

The replay line the gated unit runner actually printed for *this* verification run
was captured from its output and pasted into a deliberately sabotaged shell:

```
env KANBAN_ROOT=operator-sabotage PYTHONHASHSEED=13 \
    PYTEST_ADDOPTS=operator-junk PGAI_AGENT_KANBAN_ROOT_PATH=/operator/sabotage \
    bash -c '<the runner's own printed replay line, pytest argv swapped for a probe>'
```

Probe observed: `(None, None, '--basetemp=/tmp/pgai_kanban_tmp/pytest/unit',
'$HOME/pgai_agent_kanban')` — `KANBAN_ROOT` and `PYTHONHASHSEED` forced
absent exactly as the runner had them, `PYTEST_ADDOPTS` and
`PGAI_AGENT_KANBAN_ROOT_PATH` pinned to the runner's own values, none of the four
sabotage values reaching the child. This is the exact operator-visible failure
BUG-0327 describes, and it no longer occurs. Result: **pass**.

Three further helper edge cases exercised directly:

- A family name exported into the runner shell and then reassigned is carried as an
  assignment holding the *reassigned* value (correct parity) — **pass**.
- A value containing spaces and `$(...)` round-trips exactly through `printf %q` and
  does not execute on paste (no `PWNED` file created) — **pass**.
- A family name exported **with an additional attribute** (`declare -rx`) is
  misclassified as not-exported and emitted as `env -u NAME` — **bug**, Finding 1
  below.

### Acceptance criteria from the requirements doc

| Criterion | Result |
|---|---|
| Each bundled item is addressed in the implementation | **pass** — BUG-0326 and BUG-0327 both fixed and behaviorally verified above |
| All Python files compile cleanly | **pass** — `python3 -m py_compile` over all 496 `.py` files in the tree, exit 0 |
| All bash files pass syntax check | **pass** — `bash -n` over all 281 `.sh`/`.bash` files, zero failures |

### Step 6.9 — Constraint negative checks

- `set -euo pipefail` present in `run-unit-tests.sh`, `run-integration-tests.sh`,
  and `gated_runner_lint.sh`. `refusal_ledger.sh` is a sourced library and
  correctly does not impose shell options on its callers. **pass**.
- No bug ID, task ID, RC identifier, or version citation appears anywhere in the
  RC's added lines (`git diff ai_main..ai_rc/v2.23.15 | grep '^+'` against the
  provenance patterns) — the naming rule holds. **pass**.
- No hardcoded secrets or credentials in the diff. **pass**.

### Step 6.10 — Prerequisite dependency graph

```
CM-20260918-027-open-rc-2-23-15        DONE     (no prereqs)
  -> CODER-20260918-024-...            DONE
  -> CODER-20260918-025-...            DONE
       -> WRITER-20260918-026-...      DONE
            -> TESTER-20260918-028-... WORKING
                 -> CM-20260918-030-release-2-23-15  WAITING
```

Acyclic, every referenced task ID resolves to a real folder, lifecycle bookends
present at both ends. **pass**.

### Step 6.8 — Synthetic release simulation

Skipped with a note: the diff touches no release-lifecycle file. The seven changed
paths are two shell libraries, the two gated test runners, two unit-test modules,
and one document — none of them `cm-release.sh`, `cm-open-rc.sh`, `cm-cancel-rc.sh`,
`project_paths.sh`, or any other participant in the RC open/close cycle.

### Step 6.13 — Skip-cites-real-bug grep-gate

`lint_skip_bug_gate.sh` ran green in the unit runner's pre-flight
(`[gated-runner] skip_cites_real_bug: OK`). The standing confirmation grep found no
placeholder shapes; the only `BUG-NNNN`-near-`skip` hits in the tree are literal
fixture strings inside `team/tests/unit/test_skip_bug_citation_scan.py`, which is
the scanner's own test data, not a live skip annotation. **pass**.

### Step 6.12 — Autonomous operation criterion

| Check | Answer |
|---|---|
| (1) Tasks force-promoted WAITING -> BACKLOG by hand? | **NO** — this task's own promotion is stamped "Promoted from WAITING to BACKLOG by wake script: prerequisites satisfied" |
| (2) `release-state.md` edited outside `cm-open-rc.sh` / `cm-release.sh` / `cm-cancel-rc.sh`? | **NO** — all four fields consistent with `CM-20260918-027-open-rc-2-23-15` at 14:46:23 |
| (3) Scripts patched mid-build while the RC was in flight? | **NO** — the commits touching `team/scripts/` ARE the two CODER tickets' own deliverables, merged from their feature branches; no out-of-band tooling edit |
| (4) Queue files edited by hand? | **NO** — every marker in all seven `*_backlog.md` files matches its task's `## State`, in the shapes the wake reconciliation writes |

`Autonomous operation criterion: Manual interventions required: none`.

### Test fidelity — mutation testing in an isolated clone

Both regression suites were checked for whether they actually guard their fix, by
cloning the RC into `$PGAI_AGENT_KANBAN_TEMP_DIR/tester/t028/mut1` (a real `git
clone`, not a worktree copy) and reverting each fix to its `ai_main` shape there:

- Reverting `team/scripts/lib/refusal_ledger.sh` →
  `test_refusal_ledger_best_effort_status_neutral.py` fails 2 of 3, with exactly the
  pre-fix signature (`returncode=1`, empty stdout, the caller dead before its
  markers). The third case (the no-project early-return path) legitimately passes
  either way. **The regression test guards the fix.**
- Reverting `gated_runner_lint.sh` and both runners →
  `test_gated_runner_stated_test_order.py` fails all 3 new cases: both static
  `..._builds_replay_line_through_shared_env_parity_helper` parametrisations and
  the behavioral `..._forces_family_var_absence_across_shells`. **The regression
  tests guard the fix.**

Neither test mocks away the breaking condition: the refusal-ledger tests run a real
`set -euo pipefail` bash child against a real failing ledger write, and the replay
test stages two genuinely separate subprocesses so the "operator shell" ambient
environment is real rather than simulated in-process. (Direct pytest here is a
debugging aid only; it is not quoted as PASS evidence anywhere in this report.)

### Step 6.14 — Coding-standards spot-check

Against `docs/coding-standards.md`, five minutes, not an exhaustive audit:

- **Directive 1/3 (one implementation, shared in `lib/`)** — the replay-line
  builder is a single function in `team/scripts/lib/gated_runner_lint.sh` that
  both runners call, over one declared name list, rather than two copies. **pass**.
- **Directive 6 (`--help` on every entry point)** — no new script or CLI entry
  point was added; the change is one library function and two echo lines.
  Not applicable.
- **Directive 5 (no hardcoded operational values)** —
  `GATED_RUNNER_REPLAY_ENV_NAMES` is the set of variable *names* the runners
  themselves set, not an operator-tunable value; hardcoding it in the library the
  runners share is correct, and the comment above it states the single-edit
  extension path. **pass**.
- **Directive 8 (no provenance in shipped strings)** — no bug ID, task ID, or
  version citation in any added comment, docstring, help line, or output string.
  The hygiene lint's `provenance` class reported 0 findings on this tree.
  **pass**.
- **Documentation Discipline Rule** — the script-behavior artifact for a script
  change is that script's usage comment, and commit `c5753a0` carries both
  runners' updated usage comments in the same commit as the code. The prose in
  `docs/test-authoring-guidelines.md` landed in the same RC via
  `WRITER-20260918-026`. **pass**.
