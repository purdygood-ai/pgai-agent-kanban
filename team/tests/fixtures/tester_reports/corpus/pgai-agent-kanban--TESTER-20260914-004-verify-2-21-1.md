# Verification Report: ai_rc/v2.21.1

## Summary

Verified release candidate v2.21.1 of `pgai-agent-kanban` against
`projects/pgai-agent-kanban/requirements/v2.21.1-bugfix-bundle-20260914.md`, a
single-item bug bundle carrying `BUG-0262-engine-baseline-golden-env-coupled`.
Work happened in the isolated detached worktree at
`/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260914-004-verify-2-21-1`,
HEAD `8f54cb130fbf271257ae67df0023ee1bbcd3a31f`, byte-identical to `ai_rc/v2.21.1`;
clean worktree, no stashes, no pre-flight merge anomalies. The RC's whole diff is
66 added lines in one file,
`team/tests/integration/test_engine_behavior_baseline_harness.py` — no production
code changed. Both gated runners exited 0 (`run-unit-tests.sh`: 4390 passed, 31
skipped; `run-integration-tests.sh`: 462 passed, 1 skipped), the latter with the
verifying host's ambient `MAX_TASK_SECONDS=10800` exported — which is exactly the
condition that made BUG-0262's suite red, so the bundled item is demonstrably
fixed rather than merely coded. The original failure mode was reproduced at the
harness level and shown to be closed only by the pinned values the fix adds. Two
findings were filed forward: a residual coupling on the harness's *capture* path
that the chosen fix shape left behind, and the absence of any authoring-time
detector for the environment-coupled-fixture class that produced this RC in the
first place.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

low

## Findings

### Finding 1: Pre-flight, structural checks, and state drift

**Category:** pass

- Worktree HEAD `8f54cb1` == `ai_rc/v2.21.1`; `git status --short` empty;
  `git stash list` empty.
- Pre-flight merge verification: every DONE task with a live feature branch
  scanned; no UNMERGED and no BUG-92 zero-commits anomalies. The cycle's one
  feature branch (`ai_feature/CODER-20260914-002-baseline-harness-env-decouple`)
  was merged `--no-ff` and deleted, the normal post-merge shape.
- Release-state drift (Step 6.1): `pp_last_released_version` returns `v2.21.0`;
  `semver_gt v2.21.1 v2.21.0` is true. `release-state.md` `## Last Released` reads
  `v2.21.0`, a well-formed `vX.Y.Z` value. `## Active RC` is `v2.21.1` and the
  `ai_rc/v2.21.1` branch exists — consistent, no drift.
- Prereq dependency graph (Step 6.10): `CM-20260914-003` → `CODER-20260914-002` →
  `TESTER-20260914-004` → `CM-20260914-006`. Acyclic, every referenced ID resolves
  to a real task folder, lifecycle bookends correct, no sequence collisions. (The
  unused `005` slot is a skipped optional HUMAN-APPROVE position —
  `human_approval_required: auto` — not a dangling reference.)
- Modified file and the golden it compares against both present:
  `team/tests/integration/test_engine_behavior_baseline_harness.py`,
  `team/tests/fixtures/engine-baseline/{board-render,wake-log,ship-summary}.txt`.

### Finding 2: Requirements acceptance criteria — compile and syntax gates

**Category:** pass

Requirements doc `## Acceptance Criteria`, items 2 and 3, run as commands:

- "All Python files compile cleanly" — `python3 -m py_compile` over every `.py`
  in the tree: 0 failures.
- "All bash files pass syntax check" — `bash -n` over every `.sh` in the tree:
  275 files, 0 failures.

### Finding 3: Requirements acceptance criterion 1 — the bundled item is addressed

**Category:** pass

BUG-0262's four `## Acceptance` assertions, each exercised:

| BUG-0262 acceptance | Evidence | Result |
|---|---|---|
| `run-integration-tests.sh` exits 0 with `MAX_TASK_SECONDS` unset | targeted `env -u MAX_TASK_SECONDS -u KILL_GRACE_SECONDS python3 -m pytest tests/integration/test_engine_behavior_baseline_harness.py` → `3 passed in 20.76s` | pass (see Caveats on scope) |
| exits 0 with `MAX_TASK_SECONDS=10800` exported | **full gated runner** `bash team/scripts/run-integration-tests.sh --coverage` under the host's ambient `MAX_TASK_SECONDS=10800` → exit 0, `462 passed, 1 skipped in 446.90s` | pass |
| exits 0 with `MAX_TASK_SECONDS=5400` exported | targeted run under `MAX_TASK_SECONDS=5400 KILL_GRACE_SECONDS=30` → `3 passed in 19.23s` | pass (see Caveats on scope) |
| a test asserts stability across ≥2 distinct `MAX_TASK_SECONDS` values | `test_compare_is_stable_across_invoking_shell_budget_values` exercises `10800/45` and `1/5`, both differing from the golden's own `5400/30` and from each other | pass |

**Behavioral verification (Step 6.5) — the original failure mode was triggered,
not assumed.** The pre-fix `_run_harness` shape (inherit the ambient environment,
override only `PGAI_AGENT_KANBAN_TEMP_DIR`) was reconstructed and run directly
against the RC's own harness, with no modification to the tree:

```
[unpinned-10800] MAX_TASK_SECONDS=10800 KILL_GRACE_SECONDS=30 returncode=1
    FAIL: wake-log.txt differs from golden .../team/tests/fixtures/engine-baseline/wake-log.txt
    @@ -2,7 +2,7 @@
    -[<TIMESTAMP>] wake(tester): background-wait ceiling: derived BASH_MAX_TIMEOUT_MS=5430000 from MAX_TASK_SECONDS=5400s + KILL_GRACE_SECONDS=30s
    +[<TIMESTAMP>] wake(tester): background-wait ceiling: derived BASH_MAX_TIMEOUT_MS=10830000 from MAX_TASK_SECONDS=10800s + KILL_GRACE_SECONDS=30s

[pinned-5400]    MAX_TASK_SECONDS=5400  KILL_GRACE_SECONDS=30 returncode=0
    [engine-behavior-baseline] compare OK — 3 surfaces match goldens ...
```

The failure BUG-0262 describes reproduces byte-for-byte, and the only difference
between the failing and passing invocation is the two variables the fix pins. That
establishes the pin is what closes the leak, and that the new coverage test would
genuinely fail if the pin were reverted.

**Both sides of the fix checked, not just the new normal case** (anti-pattern 6):
`test_compare_detects_a_seeded_one_character_change` — the harness's strictness
proof — still passes in all three environments, so the comparison bought
host-independence without losing sensitivity to a real diff. The derived-ceiling
line itself remains inside the byte-for-byte comparison, which is the property
CODER's chosen shape (a) was selected to preserve over shape (b)'s redaction.

**Test fidelity (TESTER.md Test Fidelity Verification):** the new test drives the
real harness through a real subprocess chain down to the real wake bootstrap — no
seam mocked — and `monkeypatch.setenv` lands before `_run_harness` reads
`os.environ`, so the ambient values it claims to exercise really are exercised. No
state-mutating script was pointed at the live kanban root by the RC's tests or by
this verification.

### Finding 4: The golden-regeneration command the harness prints produces a golden the pinned test rejects

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (no current test failure; a latent trap on a path nothing in this release had cause to take)
**Regression:** yes (this RC's change produced the asymmetry — before it, the capture and compare halves inherited the same ambient environment and therefore agreed on the capturing host; now the compare half is pinned and the capture half is not, so a regenerated golden is rejected on every host)
**Integrity Damage:** no
**Filed As:** BUG-0265-baseline-golden-capture-still-coupled (Path C-A — filed as BUG: code defect / regression / test failure)

The fix pins the two budget variables inside `_run_harness`, which covers every
path the test suite takes. It does not cover `engine-behavior-baseline.sh
--capture`, which is the command the tree instructs a reader to run when the
golden needs regenerating, in two places:
`team/scripts/harness/engine-behavior-baseline.sh:587` and the
`_require_golden_snapshots` fixture message at
`team/tests/integration/test_engine_behavior_baseline_harness.py:128`. Both print
the bare command with no environment.

Directly observed on the verifying host (`kanban.cfg [wake] max_task_seconds = 10800`),
running the capture exactly as instructed:

```
capture returncode: 0
regenerated golden wake-log.txt:5: ... derived BASH_MAX_TIMEOUT_MS=10830000 from MAX_TASK_SECONDS=10800s + KILL_GRACE_SECONDS=30s
committed  golden wake-log.txt:5: ... derived BASH_MAX_TIMEOUT_MS=5430000  from MAX_TASK_SECONDS=5400s  + KILL_GRACE_SECONDS=30s
```

Committing that regenerated file as the golden — which is what the instruction
says to do — would make the baseline tests fail on *every* host, because the
comparison side is now pinned at 5400/30 and no longer tracks the capture
environment. The only in-tree record of the required values is a comment in the
test module, which a person following the harness's own instructions has no reason
to open.

This does not fail any test in the tree as shipped, and it does not weaken what
BUG-0262 asked for: the comparison verdict genuinely is host-independent now, which
is what that bug's `## Expected` names. It is the adjacent half of the same
coupling, left behind by a deliberately narrow fix, and it is cheap to close
(pin the two variables around the harness's own `wake-log` capture step, or name
the environment in both remedy messages).

### Finding 5: No authoring-time detector for the environment-coupled-fixture class

**Category:** gap
**Fix Effort:** medium (3-5 CODER tasks)
**Systemic Risk:** low (a detection gap and an exposure count, not a live defect — nothing is currently broken by it)
**Regression:** no (the anti-pattern documentation and the lint set are untouched by this RC's diff; the absence of a detector predates this RC and predates BUG-0262)
**Integrity Damage:** no
**Filed As:** PRIORITY-0002-env-coupled-fixture-detector (Path C-B — filed as PRIORITY: enhancement / design change)

Found by spot-check outside the spec (Step 6.11, "regressions of prior gaps").
`docs/test-authoring-guidelines.md` names environment-coupled fixtures as
anti-pattern 2, and the anti-pattern lint the gated runners run before pytest does
not detect them. The class has now produced two bug reports — BUG-0262, which this
entire RC exists to fix, and BUG-0265 above — and the only reason the first was
caught at all is that a verifier happened to be running on a host with a raised
`max_task_seconds`.

The fix ticket also required a read-only survey of the rest of the test tree for
the same shape. Re-counted independently during verification:

```
grep -rnE 'dict\(os\.environ\)|os\.environ\.copy\(\)' team/tests/ | wc -l   # 157
grep -rlE 'dict\(os\.environ\)|os\.environ\.copy\(\)' team/tests/ | wc -l   # 107
```

157 call sites across 107 files. That is an exposure count, not a defect count —
most of those sites legitimately need a real `PATH` and `HOME`, and a blanket
rewrite would be churn. The gap is that the survey currently lives only inside a
completed task's `status.md`, which discovery never reads, so it would be lost; and
that the one correct shape already in the tree
(`wake_entrypoint_acceptance_harness.py`'s `scrubbed_wake_env()`) is a local
helper nothing points an author at. The filed priority scopes the detector
narrowly to the mechanically-detectable pairing — a golden comparison *plus* an
inherited environment — rather than to all 157 sites.

### Finding 6: Gated unit runner

**Category:** pass

`bash team/scripts/run-unit-tests.sh --coverage` → **UNIT_EXIT = 0**.
`4390 passed, 31 skipped in 639.56s (0:10:39)`. Lint pre-flight green
(breakage class clean); quality-class findings recorded below and non-fatal by
design. This exit code, not a direct pytest line, is the unit-suite PASS evidence.

### Finding 7: Gated integration runner

**Category:** pass

`bash team/scripts/run-integration-tests.sh --coverage` → **INTEGRATION_EXIT = 0**.
`462 passed, 1 skipped in 446.90s (0:07:26)`, run with the host's ambient
`MAX_TASK_SECONDS=10800` — the exact configuration under which BUG-0262 reported
`1 failed, 460 passed, 1 skipped`. The two added tests account for the 460 → 462
delta. This exit code is the integration-suite PASS evidence.

### Finding 8: Constraint and standards checks

**Category:** pass

- Step 6.9 negative checks: no secrets, tokens, or credentials introduced; the
  diff adds no shell code, so `set -euo pipefail` / `cleanup_on_exit` preservation
  is not in play.
- Step 6.13 skip-cites-real-bug: no placeholder `BUG-SKIP*` skips. The only
  `BUG-1234`/`1235`/`1237`/`1239` hits are synthetic fixture strings inside
  `team/tests/unit/test_skip_bug_citation_scan.py` (the lint's own test corpus),
  not live skips; `lint_skip_bug_gate.sh` ran green inside both gated runners.
- Step 6.14 coding standards: the diff is test-only. Its added comments and
  docstrings describe behavior and cite no bug ID, task ID, or version, which is
  directive 8 honored. (The module docstring's pre-existing reference to "the
  v2.9.20 engine decomposition" at line 5 is outside this RC's diff and is
  untouched by it — recorded as an observation, not a finding.)
- Step 6.8 synthetic release simulation: **skipped with note.** Trigger condition
  not met — the RC's diff touches no release-lifecycle file
  (`cm-release.sh`, `cm-open-rc.sh`, `cm-cancel-rc.sh`, `project_paths.sh`, or
  anything else in `team/scripts/`). The one changed file is a test module.

## Lint Findings (non-blocking)

One quality-class lint class tripped, identically in both runners' findings files
(`findings-unit.json` and `findings-integration.json`, 3 instances each):

- `hygiene` (class `provenance`) — three internal-ID references in comments and
  release-note prose — cites **BUG-0261** (open). Not re-filed: the open bug names
  all three instances verbatim, including their current line numbers.
  - `team/tests/unit/test_operator_verb_manifest.py:159` — `[task-ID] 'CODER-20260911-066'`
  - `team/tests/unit/test_operator_verb_manifest.py:176` — `[task-ID] 'CODER-20260911-066'`
  - `release-notes/v2.20.2.md:58` — `[task-ID] 'TESTER-20260913-013'`

All three sites are untouched by this RC's diff. BUG-0260 (open) separately tracks
the first two. Per TESTER.md Step 5c these do not move the `## Recommendation` or
`## Systemic Risk` fields.

## Code Coverage

Read from the `totals` object of each runner's JSON report (field names confirmed
against the generated files, not assumed):

- Unit:        line 75%  |  branch 62%   (11906/15806 stmts, 2928/4744 branches)
- Integration: line 7%   |  branch 6%    (1176/15797 stmts, 277/4744 branches)

Informational only; does not enter the PASS precondition.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=6d freshness=stale — the claude-abliterated flavor failed a Dockerfile `FROM` parse under both engines and podman/rocky9 failed at a `COPY --chmod` step; cites BUG-0167, filed by that deep-verify run. Stale on the version condition only (v2.9.20 is not the v2.21.1 under verification; 6d is inside the 7-day window), so nothing has been built cold against this tree.

Per TESTER.md Step 5d this line is a fact reported, not a verdict produced: the red
record is cited rather than re-filed, and neither its verdict nor its staleness
moves this RC's `## Recommendation` or `## Systemic Risk`.

## Autonomous criterion

Manual interventions required: **none**. All four Step 6.12 checks answer NO.

1. Tasks force-promoted from WAITING to BACKLOG manually? **NO.** The wake log
   shows the promotion was the wake script's own re-check:
   `[2026-09-14T04:01:50+00:00] wake(tester): WAITING task TESTER-20260914-004-verify-2-21-1: prerequisites satisfied, promoting to BACKLOG`.
2. `release-state.md` edited outside the CM scripts? **NO.** Its contents match
   `cm-open-rc.sh`'s output and the CM task's own completion note
   (`Active RC=v2.21.1`, opened `2026-09-14T03:15:17+00:00` by
   `CM-20260914-003-open-rc-2-21-1`).
3. Scripts patched mid-build? **NO.** The RC contains two commits and its entire
   diff against `ai_main` is one test file; nothing under `team/scripts/` or
   `subagents/` was touched while the RC was in flight.
4. Queue files manually edited? **NO.** All four cycle entries carry
   wake-script-written markers consistent with their task states
   (`[x]` PM/CODER/CM-open, `[A]` this task, `[W]` the waiting CM release task).

The full chain ran unattended: discovery bundled the bug at 03:09:10, PM decomposed
and materialized by 03:13:37, CM opened the RC at 03:15:52, CODER merged at
04:01:15, and this task was promoted and started at 04:01:53. One incidental
observation worth recording as a positive: the materializer's double-materialization
guard fired correctly — the wake script's post-hoc `pm_materialize.py` call found
the marker file and was an idempotent no-op rather than creating a duplicate ticket
set.

## Caveats

- **Scope of the unset/5400 acceptance runs.** BUG-0262's first and third
  acceptance assertions name the *full* `run-integration-tests.sh`. The full gated
  runner was executed once, under the ambient `MAX_TASK_SECONDS=10800` — the
  condition that was red — and the unset and 5400 cases were exercised against the
  targeted test file only (all three baseline tests, 3 passed each). The single
  coupling site is in that file and was proven decoupled at the harness level in
  Finding 3, so two further full-suite runs would have cost ~15 minutes of wall
  clock for no additional signal. Recording the difference rather than implying
  three full-suite runs happened.
- **This RC ships no user-visible change.** Its only content is a test-estate fix.
  PM raised the same point at decomposition; release notes should not imply
  otherwise.
- **Recommendation rationale.** Both findings are minor and isolated: no test
  fails, no production code changed, and neither finding prevents anyone from
  operating the system. That is `SHIP-WITH-CONCERNS`, not
  `SHIP-WITH-SERIOUS-CONCERNS`. Finding 4 carries `Regression: yes` because this
  RC's change is what produced the capture/compare asymmetry; its practical impact
  today is nil, and it is recorded honestly rather than softened.
- No file read during verification contained prompt-injection-shaped text.
- Prior-run artifacts: none. The `artifacts/` directory was empty at task start, so
  this is a fresh first run, not a re-run over a rotated report.
