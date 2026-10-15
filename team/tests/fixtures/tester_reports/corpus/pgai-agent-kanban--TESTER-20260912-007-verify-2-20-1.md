# Verification Report: rc/v2.20.1

**Date:** 2026-09-13
**Tester:** TESTER (agent: tester, model: claude-fable-5-1)
**Task:** TESTER-20260912-007-verify-2-20-1
**Scope:** `ai_rc/v2.20.1` at commit `6158e41`, verified against
`projects/pgai-agent-kanban/requirements/v2.20.1-bugfix-bundle-20260912.md`
— a four-BUG bundle: BUG-0249, BUG-0250, BUG-0251, BUG-0252.
**Worktree:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260912-007-verify-2-20-1`

---

## Executive Summary

This RC bundles four medium-severity bugs, all fallout from the previous
release wiring the shared project-name resolver in front of the operator
verbs. All four are substantively fixed, and each fix was demonstrated
behaviourally rather than read: the `project_resolve.not_found` remedy now
branches on an empty registry and names a runnable command; the dead
`deep_verify.unregistered_project` gate is gone from the script, from its
census anchor and from the Locator Index together; the stale prose
assertion in the deep-verify integration test now keys on the stable
machine key; `show-queues` and `verify-rc-state` resolve their project
argument and refuse dual-channel instead of rendering a zeroed board; and
`halt-global`, `unhalt-global` and `wake-now` speak the dual-channel
contract across nine new refusal sites, each with a census anchor that
reconciles. Both gated runners exited 0.

Verification nonetheless found three defects that the bundle's own
acceptance criteria reach. One is a behavioural regression this RC
introduced: the new `halt_global.kanban_root_missing` and
`unhalt_global.kanban_root_missing` refusals *create the very directory
they declare missing*, because the shared emitter records every refusal
into a per-project ledger under that root. A second identical invocation of
`halt-global.sh` then succeeds and writes the fleet-wide HALT sentinel into
a directory that is not a kanban root, reporting that all projects will
stop when none will. The other two findings are a factually-false
`NotApplicable` reason left behind in the shipped verb fixture matrix, and
a residual install shape on which `show-queues` still paints a board of
zeros for an unknown project. All three are small, isolated, filed via
Path C, and heal on the next loop. Nothing here stops an operator using the
system.

**Recommendation: SHIP-WITH-CONCERNS.**

---

## Verification Environment and Pre-Flight

| Check | Result |
|---|---|
| Worktree `HEAD` == `ai_rc/v2.20.1` | PASS — both `6158e41` |
| `git status --short` clean | PASS — empty |
| `git stash list` empty | PASS — empty |
| Pre-flight merge verification (Step 2a) | PASS — 0 anomalies. Every DONE feature branch from this cycle was already deleted post-merge (the normal CODER/WRITER Procedure A Step 8 cleanup), so Checks 1 and 2 correctly skip; 0 live feature branches remained to check |
| `release-state.md` drift (Step 6.1) | PASS — `Active RC: v2.20.1` with branch `ai_rc/v2.20.1` present; `Last Released: v2.20.0` is well-formed `vX.Y.Z`; highest tag on `ai_main` is `ai_v2.20.0`; `v2.20.1 > v2.20.0` by semver |
| Prereq dependency graph (Step 6.10) | PASS — acyclic; all referenced task IDs exist; bookends correct (CM-20260912-006 open-rc → 3 CODER + 1 WRITER → TESTER-20260912-007 → CM-20260912-009 release); queue markers consistent (`[x]` for each DONE task, `[A]` for this one, `[W]` for the waiting CM release) |

RC diff scope: 14 files, +731 / −103 — `docs/blocking-site-census.md`,
`docs/operator-commands.md`, six scripts, five test files. Eight commits
(four work + four `--no-ff` merges), none empty.

---

## Acceptance Criteria

### Requirements-doc criteria

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | Each bundled item is addressed in the implementation | PASS — with the two shortfalls recorded as Findings 2 and 3 | Per-bug tables below |
| 2 | All Python files compile cleanly | PASS | `python3 -m py_compile` over all five changed test files, exit 0; the gated runners' breakage-class pre-flight also passed |
| 3 | All bash files pass syntax check | PASS | `bash -n` over all six changed scripts, exit 0 |

### BUG-0249 — stale prose assertion in the deep-verify integration test

| # | Acceptance | Result | Evidence |
|---|---|---|---|
| 1 | `run-integration-tests.sh` exits 0 on a tree carrying the fix | PASS | `INTEGRATION_EXIT=0`, 455 passed / 1 skipped |
| 2 | The test still asserts non-zero exit, all three dual-channel fields, and no ledger record | PASS | `team/tests/integration/test_deep_verify_run.py` — the RC changes line 318 only; the four surrounding assertions are byte-identical |
| 3 | The refusal assertion keys on the machine `error:` key, not on prose | PASS | `assert "not registered" in result.stderr` → `assert "error:   project_resolve.not_found" in result.stderr` |

### BUG-0250 — `project_resolve.not_found` remedy, and the dead census anchor

| # | Acceptance | Result | Evidence |
|---|---|---|---|
| 1 | An unregistered `--project` against a root with an empty `projects.cfg` prints a remedy naming a runnable command, never a bare `(registered: )` | PASS | Live run against a scratch root: `remedy: No project is registered in <root>/projects.cfg. Register one first: team/scripts/add-project.sh --project nonexistent-project.` exit 1 |
| 2 | The same verb against a root with registered projects still names them | PASS | Against the live kanban root: `remedy: Check the project name against projects.cfg (registered: pgai-agent-kanban).` |
| 3 | A unit test covers both branches of the remedy text | PASS | Two new tests in `team/tests/unit/test_project_paths_bash.py`, both asserting `"(registered: )" not in stderr`, one per branch |
| 4 | The census no longer indexes a refusal that cannot fire | PASS | The `deep_verify.unregistered_project` block, its `# CENSUS-ANCHOR: deep-verify-01` marker and its Locator Index line were removed in one commit; the tier paragraph's marker count was corrected eleven → ten; `grep -rn deep-verify-01` now matches only the unrelated `config-loader-deep-verify-01`; `lint_gate_census_compliance.py` exits 0 with 0 offenders over 35 files |

### BUG-0251 — verbs without dual-channel refusals

| # | Acceptance | Result | Evidence |
|---|---|---|---|
| 1 | Each of the five named verbs emits `error:` / `message:` / `remedy:` on its argument-validation refusals, with a stable machine key | PASS-WITH-CAVEAT | All five demonstrated live (below). Caveat: the shared `operator_args_validate_known` undeclared-flag refusal is still a plain stderr line — for every verb in the tree, including ones the matrix already treats as compliant. Pre-existing and untouched here; see Spot Checks |
| 2 | `_DUAL_CHANNEL_SCENARIOS` carries a real `DualChannelCase` (not `NotApplicable`) for each of the five, and the matrix's dual-channel test passes | PASS | All five flipped; `test_verb_fixture_matrix.py` green inside `UNIT_EXIT=0` |
| 3 | No refusal printed to an operator contains an angle-bracket placeholder; `verify-rc-state.sh`'s `ERROR: <project> is required.` is gone | PASS | `grep -n 'ERROR: <project>' team/scripts/verify-rc-state.sh` → no match. The live run emits `verify_rc_state.project_required`. The RC also swept `<project>` → `PROJECT` through the header comment and usage text |
| 4 | Each newly added refusal carries its census classification comment, and each BLOCK-WORTHY one carries a `# CENSUS-ANCHOR:` marker with a matching Locator Index entry | PASS | All 14 new anchors checked individually: marker present exactly once in its file and index entry present exactly once, for every one. Census lint: 0 offenders |

Live refusal evidence — every one exits non-zero with all three fields present:

```
halt-global.sh       halt_global.kanban_root_missing, halt_global.sentinel_create_failed
unhalt-global.sh     unhalt_global.kanban_root_missing, unhalt_global.sentinel_remove_failed
wake-now.sh          wake_now.agent_required, wake_now.agent_value_required,
                     wake_now.agent_all_mutually_exclusive,
                     wake_now.unexpected_positional_argument, wake_now.unknown_agent
verify-rc-state.sh   verify_rc_state.project_required,
                     verify_rc_state.unexpected_positional_argument,
                     project_resolve.not_found
show-queues.sh       project_resolve.not_found
```

All nine `wake-now` / `halt-global` / `unhalt-global` sites were triggered
against scratch roots under
`/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester-scratch`, including
the two sentinel-failure sites, reached by making the scratch root
read-only. No global `HALT` was ever created at the live kanban root.

### BUG-0252 — `show-queues` renders zeros for an unknown project

| # | Acceptance | Result | Evidence |
|---|---|---|---|
| 1 | `show-queues.sh --project <unambiguous-prefix>` renders the matching project's real counts | PASS | `--project pgai` renders a board identical to `--project pgai-agent-kanban` (pm 37/37, coder 158/158, writer 41/41, tester 36/37, cm 73/74) |
| 2 | `show-queues.sh --project <unregistered-name>` exits non-zero with a dual-channel refusal and renders no board | PASS on a root with a `projects.cfg`; **GAP** on a root with none | `--project totally-not-a-project` against the live root → refusal, empty stdout, exit 1. Against a root with no `projects.cfg` → zeroed board, exit 0. See Finding 3 |
| 3 | `verify-rc-state.sh <unambiguous-prefix>` runs against the resolved project | PASS | `verify-rc-state.sh pgai` → `verify-rc-state: 0 errors, 14 warnings, 93 ok`, exit 0 |
| 4 | A test covers each of exact / prefix / unknown for both verbs | PASS | `test_show_queues_human_queue.py` (+4 tests, incl. both absent-`--project` modes) and `test_verify_rc_state_two_project_prefix_fixture.py` (+2 tests) |

---

## Constraint Verification

| Constraint | Status | Notes |
|---|---|---|
| `tester_operation: verify-rc` | MET | RC verified against its requirements doc; report produced |
| `target_version: v2.20.1` | MET | Worktree pinned to `ai_rc/v2.20.1` @ `6158e41` |
| `set -euo pipefail` preserved in changed scripts | MET | Present in all six changed executables. Absent only from the sourced library `lib/project_paths.sh`, which is correct for a library and unchanged in that respect by this RC |
| No hardcoded secrets introduced | MET | `grep -inE '^\+.*(password\|api[_-]?key\|secret\|token)\s*='` over the RC diff → no matches |
| Coding standards, directive 8 (provenance) | MET | No task ID, bug ID or version token appears in any added line of the RC diff |
| Skip-cites-real-bug gate (Step 6.13) | MET | No placeholder `BUG-SKIP` or `skip.*BUG-[A-Za-z]` annotation anywhere in `team/tests/`; no skip annotation cites a `BUG-NNNN` at all, so no ID needs resolving. The only literal `BUG-NNNN` strings are inside archived run logs under `team/tests/artifacts/`, which are not skip annotations. `lint_skip_bug_gate.sh` also passed inside both green gated runners |

---

## Test Suites

Authoritative gated runners, run from the worktree root. These exit codes
are the suite verdict; no direct `pytest` invocation was used as a PASS
source.

| Suite | Command | Exit | Result |
|---|---|---|---|
| Unit | `bash team/scripts/run-unit-tests.sh --coverage` | **0** | 4330 passed, 31 skipped, 614.96 s |
| Integration | `bash team/scripts/run-integration-tests.sh --coverage` | **0** | 455 passed, 1 skipped, 412.85 s |

Both breakage-class pre-flights passed. Both runs reported
`bare_tmp_cleanliness: OK` and `listener_cleanliness: OK`. Both wrote a
quality-class findings file (11 findings each), ingested below.

`UNIT_EXIT == 0` AND `INTEGRATION_EXIT == 0`, so the suite-pass
precondition for a PASS recommendation is satisfied; the recommendation is
elevated by the Step-7 findings, not by the suites.

---

## Code Coverage

Source: the `totals` object of
`${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/unit.json` and
`.../integration.json`. Field names were confirmed by reading the generated
files, not assumed.

- **Unit:** line 75% | branch 62% (15721 statements, 4712 branches; the
  blended `percent_covered` figure is 72%)
- **Integration:** line 7% | branch 6% (blended 7%) — expected shape, since
  the integration suite drives the framework through subprocesses that the
  in-process coverage instrument does not see

Informational only. Coverage does not enter the PASS precondition and does
not affect the systemic-risk rating.

---

## Findings

### Finding 1: the global-halt "root does not exist" refusal creates the root, and the next identical run halts a directory that is not a kanban root

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (a real defect that ships and gets fixed next iteration; it cannot reach the autonomous chain, which never invokes these verbs with a bad root, but it makes a fleet-wide stop signal report success while doing nothing)
**Regression:** yes (this RC introduced it; on `ai_main` both guards used a bare `printf ... >&2` and created nothing)
**Integrity Damage:** no (no existing state is lost or corrupted — the sentinel and the fabricated tree are new paths outside any real kanban root)
**Filed As:** BUG-0256 (Path C-A — filed as BUG: code defect / regression / test failure)

This RC converted `halt-global.sh` and `unhalt-global.sh` to
`pgai_emit_error_contract`. That emitter also records each refusal into
`<KANBAN_ROOT>/projects/<project>/refusal-ledger.md`, creating the parent
directories on the way. Two of the four converted sites fire *because*
`KANBAN_ROOT` does not exist — so emitting the refusal materialises the
directory the refusal just declared absent.

Observed, with `PGAI_PROJECT_NAME` set (which the framework's own wake
scripts export):

```
$ PGAI_AGENT_KANBAN_ROOT_PATH=/tmp/.../absent-B bash team/scripts/halt-global.sh
error:   halt_global.kanban_root_missing
message: halt-global.sh: KANBAN_ROOT does not exist or is not a directory: /tmp/.../absent-B.
remedy:  Set PGAI_AGENT_KANBAN_ROOT_PATH to an existing kanban root, or create /tmp/.../absent-B first.
   (exit 1)

$ find /tmp/.../absent-B
/tmp/.../absent-B
/tmp/.../absent-B/projects
/tmp/.../absent-B/projects/pgai-agent-kanban
/tmp/.../absent-B/projects/pgai-agent-kanban/refusal-ledger.md

$ PGAI_AGENT_KANBAN_ROOT_PATH=/tmp/.../absent-B bash team/scripts/halt-global.sh
global HALT set: /tmp/.../absent-B/HALT — all projects will stop at next wake
   (exit 0)
```

The second invocation is byte-identical to the first with an unchanged
environment. It reports the fleet-wide stop as set, in a directory the
first invocation said was not a kanban root. No chain agent reads that
sentinel; the real kanban root has no `HALT` and the chain keeps running.
`unhalt-global.sh` shares the first half; its second-invocation
consequence is milder ("no global HALT was set", exit 0, against a
directory it invented).

Negative control: with `env -u PGAI_PROJECT_NAME`, the ledger write is
skipped by design, nothing is created, and both invocations refuse
identically. The defect is therefore environment-dependent — and the
environment in which it bites is the framework's own.

Both the original failure mode and the new normal case were exercised: the
four other converted sites (`halt_global.sentinel_create_failed`,
`unhalt_global.sentinel_remove_failed`, and the `unhalt-global` root guard
against a truly absent root) all behave correctly, and the nine `wake-now`
and `verify-rc-state` conversions are unaffected because their refusals do
not fire on the absence of the root.

### Finding 2: the verb fixture matrix records a REST-parity exemption reason that this RC made false, and its docstring now contradicts its own table

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (hygiene; confined to one test file's recorded prose, no behaviour affected)
**Regression:** yes (the reason text was true before this RC and was falsified by this RC's own dual-channel conversion)
**Integrity Damage:** no
**Filed As:** BUG-0257 (Path C-A)

`team/tests/unit/test_verb_fixture_matrix.py` exists so that every
exemption carries "a named, verified reason rather than a silent omission"
— its own words. Two of those reasons are now wrong.
`_REST_PARITY_SCENARIOS` still gives `show-queues` and `verify-rc-state`
the `_NO_REST_PARITY_NO_KEY` reason, which asserts the verb "has no
CLI-reachable dual-channel refusal to compare (see the dual-channel
column's own reason for this verb)". Both clauses were falsified by this
same RC: both verbs gained a CLI-reachable `project_resolve.not_found`
refusal, and both `_DUAL_CHANNEL_SCENARIOS` entries were flipped from
`NotApplicable` to a real `DualChannelCase`, so there is no longer a
dual-channel "reason" to go and read.

The RC introduced `_NO_REST_PARITY_OUT_OF_SCOPE` for exactly this
situation and applied it to `halt-global`, `unhalt-global` and `wake-now`,
but did not backfill the two read verbs. The module docstring compounds it,
stating that "the three that take no `--project` flag at all (halt-global,
unhalt-global, wake-now) remain `NotApplicable` in
`_REST_PARITY_SCENARIOS` only" — an exhaustive-sounding claim, when five
verbs are still `NotApplicable` there.

No test fails: the matrix asserts that a reason is present, not that it is
true, so the inaccuracy is invisible to the suite. The implementing task
recorded the first half honestly in its own completion note; it is filed
here so it is tracked in the ledger rather than left in a task folder.

### Finding 3: `show-queues` still renders a board of zeros for an unknown project on a kanban root that has no `projects.cfg`

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (an install with no project registry is already uninitialised; the healthy path is fixed)
**Regression:** no (the behaviour predates this RC — it is BUG-0252's original defect surviving on one path, via `pp_resolve_project_name`'s documented pass-through when `projects.cfg` is absent, which this RC did not change)
**Integrity Damage:** no
**Filed As:** BUG-0258 (Path C-A)

BUG-0252's acceptance criterion 2 is written without qualification:
`show-queues.sh --project <unregistered-name>` must exit non-zero with a
dual-channel refusal and render no board. On a root that has a
`projects.cfg` it now does. On a root with none, `pp_resolve_project_name`
passes the candidate through unchanged by documented design, and the
original defect survives intact:

```
$ PGAI_AGENT_KANBAN_ROOT_PATH=/tmp/.../nocfg bash team/scripts/dashboard/show-queues.sh \
    --kanban-root /tmp/.../nocfg --project bogus-name
Active provider: claude
pm:     0/0
coder:  0/0
writer: 0/0
tester: 0/0
cm:     0/0
   (exit 0)
```

The same invocation against a root whose `projects.cfg` exists but is empty
refuses correctly with `project_resolve.not_found` and exit 1. So the
behaviour turns on whether the registry file is present, not on whether the
name is real. `verify-rc-state.sh` does not share the gap — its own
`verify_rc_state.project_directory_missing` backstop refuses on that path,
which is exactly why the RC added it.

PM's decomposition named this residual in advance, so it is a consciously
scoped-out remainder rather than a miss. It is filed because BUG-0252 will
be closed by this release and nothing would otherwise track the remaining
path.

### Finding 4: the four bundled bugs' behavioural fixes, verified by triggering each original failure mode

**Category:** pass

Every bug in the bundle was verified by reproducing its original symptom
against this RC and confirming the new behaviour, not by reading the diff.
The empty-registry remedy, the registered-names remedy, the prefix and
exact-name resolution on both read verbs, the unknown-name refusals, the
removal of the dead `deep-verify-01` gate and its index entry, and all nine
new dual-channel refusal sites were each exercised directly. Details are in
the four per-bug acceptance tables above.

### Finding 5: census reconciliation and gate-authoring compliance for 14 new anchors

**Category:** pass

`lint_gate_census_compliance.py` exits 0 with 0 offenders across 35 files
and 150 exempted anchored sites. Each of the 14 anchors this RC adds was
also checked by hand: marker present exactly once in its own file, Locator
Index entry present exactly once, classes agreeing. The removed
`deep-verify-01` anchor and its index line went in the same change, as the
new-gate rule requires.

### Finding 6: structural and constraint checks

**Category:** pass

All six changed scripts pass `bash -n` and retain `set -euo pipefail`; all
five changed Python files pass `py_compile`; no secrets, no provenance
tokens, and no placeholder skip annotations were introduced. The
documentation-layer change (`docs/operator-commands.md`) was read in full
and matches observed behaviour on both verbs, including the exit-code
lines.

---

## Lint Findings (non-blocking)

Both gated runners wrote a findings file; both contain the same 11
quality-class findings across two lint classes. Every finding cites an
already-`open` bug, so nothing was re-filed (the cite-an-open-bug-or-file-one
rule, one bug per lint class per run).

- `hygiene` — 5 instances, all `severity: quality`, all provenance
  references to internal task IDs in shipped text, none in a file this RC
  touched — cites **BUG-0245 (open)** for the class. Instances:
  - `team/tests/unit/test_operator_verb_manifest.py:155` — task-ID reference `CODER-20260911-066`
  - `team/tests/unit/test_operator_verb_manifest.py:172` — task-ID reference `CODER-20260911-066`
  - `team/tests/unit/test_posture_bar_truth.py:20` — task-ID reference `CODER-20260911-049`
  - `release-notes/v2.16.0.md:42` — task-ID reference `TESTER-20260910-070` (also tracked by **BUG-0242, open**)
  - `release-notes/v2.19.0.md:37` — task-ID reference `TESTER-20260911-053` (also tracked by **BUG-0248, open**)
- `governance_byte_budget` — 6 instances, all `severity: quality`, no file
  or line (the lint is role-scoped) — cites **BUG-0246 (open)** for the
  class. Instances:
  - `po over governance byte budget: assembled=65540 bytes budget=65536 bytes` (newly over since BUG-0246 was filed; the class is already tracked, so it is cited here rather than filed separately)
  - `coder over governance byte budget: assembled=72840 bytes budget=69632 bytes`
  - `writer over governance byte budget: assembled=71170 bytes budget=69632 bytes`
  - `tester over governance byte budget: assembled=103889 bytes budget=102400 bytes`
  - `cm over governance byte budget: assembled=89547 bytes budget=86016 bytes`
  - `overwatch over governance byte budget: assembled=76366 bytes budget=73728 bytes`

These findings are non-blocking and do not, on their own, move the
`## Recommendation` or `## Systemic Risk` values above.

---

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=5d freshness=stale — the last cold-build sweep was red (the claude-abliterated flavor failed a Dockerfile FROM parse under both engines, and podman/rocky9 failed at a COPY --chmod step); it cites BUG-0167, filed by that deep-verify run itself and since closed, and the record is stale because it names v2.9.20 rather than the v2.20.1 under verification, so nothing whatever is known about whether this tree's images build.

The lane was not run during this verification, per Step 5d — the RC runs
the fast suites only. The record is cited, never re-filed.

---

## Autonomous Criterion (Step 6.12)

| # | Check | Answer |
|---|---|---|
| 1 | Were any tasks force-promoted from WAITING to BACKLOG manually? | **NO** — this task's own promotion is recorded as "Promoted from WAITING to BACKLOG by wake script: prerequisites satisfied" |
| 2 | Was `release-state.md` edited outside `cm-open-rc.sh` / `cm-release.sh` / `cm-cancel-rc.sh`? | **NO** — its fields match exactly what CM-20260912-006 records `cm/open-rc.sh` having written at 2026-09-12T23:54:16+00:00 |
| 3 | Were any scripts patched mid-build? | **NO** — the RC carries exactly four work commits and four `--no-ff` merges, one pair per ticket, all inside the RC window (00:27–02:19 on 2026-09-13); no out-of-band commit touches `team/scripts/` or `subagents/`, and no commit is empty |
| 4 | Were any queue files manually edited? | **NO** — every marker for this cycle matches the state its task's `status.md` reports |

**Autonomous operation criterion: Manual interventions required: none.**

---

## Spot Checks Beyond the Spec (Step 6.11)

- **Undeclared-flag refusals are still plain stderr on every verb.**
  `operator_args_validate_known` prints `<script>: unknown argument: --FLAG`
  with no machine key. `team/scripts/lib/operator_args.sh` is untouched by
  this RC, and the behaviour is identical on verbs the matrix already
  treats as dual-channel-compliant (`halt.sh --bogus-flag` refuses the same
  way). Framework-wide and pre-existing rather than a shortfall of this RC,
  so it is recorded as an observation, not a finding.
- **`verify-rc-state.sh --help` prints two `Usage:` lines** — the generic
  `Usage: verify-rc-state.sh [OPTIONS]` from
  `operator_args_render_help_for_flags`, then the script's own
  `Usage: verify-rc-state.sh PROJECT [--verbose]`. Pre-existing structure;
  this RC only corrected the placeholder inside the second line.
  Observation.
- **`VERSION` still reads `v2.20.0` and `CHANGELOG.md` has no `v2.20.1`
  section on the RC.** Confirmed normal, not drift: the `ai_v2.20.0` and
  `ai_v2.19.0` tags each carry their own version in `VERSION`, so the bump
  is written by `cm-release.sh` at ship time.
- **A stray directory named `1` sits at the kanban root**
  (`$HOME/pgai_agent_kanban/1`) — almost certainly a shell-redirect
  typo from an earlier session. Outside the dev tree and unrelated to this
  RC. Observation only.
- **`test_verb_fixture_matrix.py` docstring points at "this ticket's own
  completion note"** — a kanban artifact a reader of the shipped tree
  cannot open. It cites no ID, so the hygiene lint does not flag it.
  Borderline against coding-standards directive 8; noted, not filed.
- **The `halt-global` / `unhalt-global` fixture makes its scratch root
  read-only and restores it with an `atexit` hook.** The pattern is
  documented in the fixture and the post-suite cleanliness checks passed,
  but it is worth remembering that it would not exercise the intended
  refusal if the suite ever ran as root. Observation.

---

## Caveats

- The four bundled bug files (BUG-0249 … BUG-0252) are at
  `## Status: running` and are expected to be flipped to `done` by
  `cm-release.sh` Step 16b when this RC ships. This verification did not
  modify them.
- Findings 1 and 3 were exercised entirely against scratch roots under
  `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester-scratch`. No
  global `HALT` was ever created at the live kanban root, and no live
  kanban state was mutated by this verification.
- PM's decomposition recorded, before the work began, that four further
  verbs (`switch-provider`, `create-project`, `add-project`,
  `remove-project`) share BUG-0251's defect but sit outside the
  requirements doc's list, and deliberately deferred them to a separate
  operator-authored requirement. That scoping was honoured and is not
  counted as a shortfall here.
- `artifacts/` was empty at the start of this run, so no prior report or
  gaps file influenced any verdict here. Every check was run fresh against
  the RC.

---

## Recommendation

SHIP-WITH-CONCERNS

**Rationale:** Both gated runners exited 0, satisfying the suite-pass
precondition, and every acceptance criterion of all four bundled bugs is
met or met-with-caveat, each demonstrated by triggering the original
failure mode. Three defect findings were recorded and filed via Path C, so
the recommendation cannot be PASS. The most serious, Finding 1, is a
behavioural regression this RC introduced, but it fires only on an
invocation that is already misconfigured (`PGAI_AGENT_KANBAN_ROOT_PATH`
naming a directory that does not exist), it is never on the autonomous
chain's path, and the fix is small. Findings 2 and 3 are low-severity
accuracy and edge-case shortfalls. Nothing found prevents an operator from
operating the system, so the release is usable and all three defects heal
on the next loop — which is the case for SHIP-WITH-CONCERNS rather than
SHIP-WITH-SERIOUS-CONCERNS.

---

## Systemic Risk

medium
