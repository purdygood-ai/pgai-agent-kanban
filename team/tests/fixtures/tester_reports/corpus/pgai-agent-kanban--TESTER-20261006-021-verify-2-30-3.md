# Verification Report: ai_rc/v2.30.3

## Summary

Verified RC `ai_rc/v2.30.3` (HEAD `2738942b01343e7445142acba928da4ec6746798`) against
`requirements/v2.30.3-bugfix-bundle-20261006.md`. That is a one-item bug bundle carrying BUG-0569:
`pgai_halt_after_run` forced errexit back on just before returning a non-zero "not yet drained"
code, so `cm/release.sh` (running `set -euo pipefail`) died silently at Step 12c, after tagging,
whenever a HALT-AFTER sentinel was armed but not yet due. The single implementing task is
CODER-20261006-019-halt-after-errexit-safe (feature commit `75886a7a`, merge `2738942b`). It
replaced the `set +e … set -e` bracket with an `|| PGAI_HALT_AFTER_EXIT=$?` capture, simplified the
caller in `release_halt_after_spend.sh` the same way, and added one release-level integration
test plus a new unit test file. Both gated runners exited 0 with zero lint findings. The original
failure was reproduced on `ai_main` and shown fixed on the RC, both at function level and end to
end through the real `cm/release.sh`. The new tests fail on pre-fix code. One low-severity gap:
the bug's "bare call under set -e" acceptance line cannot be met in bash, and the shipped usage
comment still calls a bare call "safe" under `set -e`. Filed as BUG-0570.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
low

## Findings

### Finding 1: Bundle AC1 / BUG-0569 — not-yet-due HALT-AFTER no longer aborts cm/release.sh at Step 12c

**Category:** pass

Evidence (Step 6.5: original failure mode and new normal case, both exercised):
- Function level, in a throwaway root with a stub `halt_after` evaluator (bytecode caching disabled), calling `cm_release_spend_halt_after_sentinel` under `set -euo pipefail`:
  - `ai_main` copies of the two libs: stub exit 1/2/3/5 → shell exits 1/2/3/5 immediately after the `[Step 12c] Checking for a HALT-AFTER sentinel…` header. The bug reproduces.
  - RC copies: stub exit 1/2/3/5 → the evaluator's output is narrated, the function returns, the next statement runs, errexit is still on, shell exit 0.
  - Stub exit 0 (promote) on both: the "HALT-AFTER sentinel spent at RC close: coder …" narration is unchanged, errexit on, exit 0. Behavior is preserved.
- End to end: the full gated integration suite (exit 0) includes the new `test_cm_release_leaves_versioned_sentinel_armed_when_target_not_yet_shipped` (ships v0.1.0 with `rc:v0.9.0` through the real `cm/release.sh` and real evaluator; asserts exit 0, `[Step 13c]` reached, HALT-AFTER kept, no HALT at project or root scope).
- Negative proof (gated runner `run-integration-tests.sh --files tests/integration/test_halt_after_rc_close_gate.py` in a separate `git clone` at `2738942b`, with only the two shell libs reverted to `ai_main`, and a sandboxed root/temp dir): exit 1, `1 failed, 4 passed`. The new test fails at `returncode == 0` and the release output ends at `[Step 12c] Checking for a HALT-AFTER sentinel to spend at RC close...`. This is exactly the bug's signature. The 4 pre-existing tests in the file pass on both trees.

### Finding 2: BUG-0569 acceptance — existing promote-path and wake-loop HALT-AFTER tests still pass

**Category:** pass

`test_halt_after_rc_close_gate.py` (2 promote-path release tests + 2 wake-loop tests, unchanged) passed within the full gated integration run (exit 0). `test_halt_after.py` and `test_halt_after_rc_gate.py` passed within the full gated unit run (exit 0). The wake-loop caller is unchanged: `_check_halt_after_for_project` (`wake_project.sh:167`, bare call) is only invoked from `if` conditions (`wake_common.sh:324`, `:423`). Run B6 below confirms that shape behaves the same on old and new code (errexit on, else-branch taken).

### Finding 3: BUG-0569 acceptance bullet 3 — "bare call under set -euo pipefail returns 1 without terminating the shell" is unmeetable; shipped comment overclaims

**Category:** gap
**Fix Effort:** small (1 CODER task — comment wording, optional pinning test)
**Systemic Risk:** low (isolated to one function's usage comment; no current caller affected)
**Filed As:** BUG-0570 (Path C-A — filed as BUG)
**Filed By:** TESTER (autonomous Path C) — the HALT-AFTER wrapper's usage comment calls an uncaptured call "safe" under set -e, but a not-yet-due answer still ends a set -e script, and the acceptance line asking for that cannot be met.

What the criterion says: under `set -euo pipefail`, a *bare* call to `pgai_halt_after_run` whose evaluator exits 1 returns 1 without terminating the shell, and errexit afterwards matches what it was before.

What the implementation does, from wrapper-level runs against a stub evaluator exiting 1 (old = `ai_main` libs, new = RC libs):

| Run | Call shape | old | new |
|---|---|---|---|
| B1 | bare call under `set -e` | shell exit 1 | shell exit 1 |
| B2 | bare call after `set +e` | shell exit 1 (errexit forced back on) | continues, rc=1, errexit=off |
| B3 | `pgai_halt_after_run … \|\| rc=$?` under `set -e` | continues, rc=1, errexit=on | continues, rc=1, errexit=on |
| B4 | `if pgai_halt_after_run …` | not-promoted, errexit=on | not-promoted, errexit=on |
| B5 | caller's own `set +e; call; rc=$?; set -e` (the pre-fix release shape) | shell exit 1 | continues, rc=1, errexit=on |
| B6 | bare call inside a function run from `if` (wake-loop shape) | continues, else-branch | continues, else-branch |

Why it is unclear/unmet: the two halves of the criterion are mutually exclusive in bash. The caller's errexit acts on the call's own return status, so no function body can make an uncaptured non-zero call survive while leaving errexit on (B1 is identical on old and new). The RC meets the achievable intent (B2, B5 fixed; B3, B4, B6 preserved), and CODER-019 recorded the conflict in its `## Instruction Conflicts`. CODER-019's own ticket asked for the header to call the function "safe as a bare call under set -e", so this is not a CODER deviation. The concrete residue is in the shipped artifact: the header's "Errexit contract" says the function is "safe as a bare call statement under set -e". It qualifies that a sentence later, but the leading clause invites a future caller to write an uncaptured call in a `set -e` script and recreate the original silent exit. Per the Conflict policy, a contradicted criterion is recorded as a gap. The follow-up is a comment-only rewording plus an optional test pinning the documented limit. Severity low under "Rating a Finding": cosmetic/minor, nothing failing.

### Finding 4: Bundle AC2 — all Python files compile cleanly

**Category:** pass

`python3 -m py_compile` over all 776 tracked `*.py` files at `2738942b` → 0 failures (bytecode redirected to scratch; worktree left clean).

### Finding 5: Bundle AC3 — all bash files pass syntax check

**Category:** pass

`bash -n` over all 321 tracked `*.sh` files → 0 failures. Both touched libs are mode `100755`.

### Finding 6: Gated suites (Steps 5a, 6.6, 6.7)

**Category:** pass

- `team/scripts/run-unit-tests.sh --coverage` → **UNIT_EXIT=0**. Parallel lane: 9641 passed, 41 skipped. Serial lane: 5 passed. Post-suite checks bare_tmp_cleanliness, listener_cleanliness, sentinel_leak, and scratch_sweep all OK. The green ledger recorded exit 0 at `2738942b0134`.
- `team/scripts/run-integration-tests.sh --coverage` → **INTEGRATION_EXIT=0**. 860 passed, 1 skipped; serial lane exit 5 (nothing collected), combined 0. Same post-suite checks OK. Green ledger exit 0.
- Logs: `/tmp/pgai_kanban_tmp/tester/v2.30.3/unit.log`, `integration.log`. The PASS evidence is these two exit codes only.
- Coverage (Step 5b, informational): **coverage unavailable**. Both runners logged `coverage disabled by config ([testing] coverage)` (the documented default `false`). The `coverage/*.json` files on disk are dated 2026-10-04, from another run, and were not used.

### Finding 7: New unit tests discriminate the fix (test fidelity)

**Category:** pass-with-caveat

`run-unit-tests.sh --files tests/unit/test_halt_after_invoke.py` in the clone: on RC code exit 0, 10 passed. On pre-fix libs exit 1, 4 failed (all four `test_run_with_errexit_off_returns_evaluator_code_and_keeps_errexit_off[1|2|3|5]`), 6 passed. Caveat: the `|| rc=$?`-captured "errexit on" cases and the if-checked-function case also pass on pre-fix code (consistent with B3/B6), so on their own they do not catch the regression. The errexit-off cases and the release-level integration test do. The integration test uses no mocks (real `cm/release.sh`, real evaluator), which is good fidelity.

### Finding 8: Pre-flight, release-state, structure, constraints, prereq graph, skip gate, coding standards (Steps 2, 3, 6.1, 6.2, 6.9, 6.10, 6.13, 6.14)

**Category:** pass

Details in the checkpoint log below. In short: HEAD == RC, clean, no stashes; v2.30.3 > v2.30.2 by `semver_gt`; no provenance identifiers, inline Python, or secrets in added lines; `set -euo pipefail` kept in `cm/release.sh`; linear graph PM-003 → CM-020 → CODER-019 → TESTER-021 → CM-023; no skips in changed tests; no new script, no hardcoded tunables, no duplicated lib logic. Step 6.8 (synthetic release simulation): the diff touches `team/scripts/lib/cm/release_halt_after_spend.sh`, which is on the release path. `test_cm_release_synthetic.py` ran inside the green gated integration suite, and the new test itself drives a full synthetic `cm/release.sh` ship against an isolated temp repo.

### Finding 9: Spot-checks outside the spec (Step 6.11)

**Category:** pass

- State drift: `Active RC` v2.30.3 matches the existing local `ai_rc/v2.30.3`. `release-state.md` mtime (06:12:27.5Z) matches CM-020's recorded open time.
- Regressions of prior gaps: the previous RC's only gap (its Finding 10, pre-existing Step 12c abort) is BUG-0569, which this RC fixes (Finding 1).
- Untested new mechanisms: none. The diff adds no new mechanism beyond the tested capture change.

## Lint Findings (non-blocking)

None. `findings-unit.json` and `findings-integration.json` each held 0 findings (snapshots in `/tmp/pgai_kanban_tmp/tester/v2.30.3/`).

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=28d freshness=stale — The only ledger record is 28 days old (outside the 7-day window) and was for v2.9.20 rather than v2.30.3, and that run already filed its failed-to-build result as BUG-0167 (now done), so nothing is known about whether the current docker/debian and docker/rocky9 images build.

## Autonomous criterion

1. Tasks force-promoted WAITING → BACKLOG by hand? **NO.** The cycle's promotions read "Promoted from WAITING to BACKLOG by wake script: prerequisites satisfied"; CM-023 is correctly `[W]` behind this task.
2. `release-state.md` edited outside the CM scripts? **NO.** Its mtime matches CM-020's `open-rc.sh` run and its fields match that task's summary.
3. Scripts patched mid-build? **NO.** The RC carries only CODER-019's ticketed commit plus its merge. No file under the live install's `scripts/` or `roles/` changed since the RC opened.
4. Queue files edited by hand? **NO** evidence. Queue mtimes line up with wake/dispatch times, and markers are consistent with each task's `## State`.
5. Remote git operation by a non-CM agent? **NO.** No `git-boundary-guard.jsonl` exists in any 20261006 task folder.

Autonomous operation criterion: Manual interventions required: none

## Caveats

- **Direct pytest was denied** by the session's permission system when I tried it as a debugging aid for the negative proof. I did not attempt it again. I used the sanctioned gated runner with `--files` in a separate clone, sandboxed (`KANBAN_ROOT`/`PGAI_AGENT_KANBAN_ROOT_PATH`/`PGAI_AGENT_KANBAN_TEMP_DIR` pointed at a throwaway root, `PGAI_TASKS_DIR`/`PGAI_PROJECT_ROOT` unset). The real green ledger's checksum was unchanged afterwards (`0717c7c1…`).
- **Harness artefact, not an RC finding:** my first function-level pass gave a misleading exit-0 result because rewriting a same-size stub within one second let Python reuse stale bytecode. I re-ran everything with `PYTHONDONTWRITEBYTECODE=1` and a cleared `__pycache__`; the results above are from the clean run. CODER's unit tests use a fresh `tmp_path` per test and are not exposed to this.
- **Observation (outside spec, pre-existing, not a finding):** `cm-release-hook.*` hook stubs accumulate in the shared temp root `/tmp/pgai_kanban_tmp/`. There were 673 before this run and 678 after, so the gated suites added 5 (4 during the unit run, 1 during integration). Their contents are test-fixture hooks (`exit 1`, `echo notified`, `echo on-rc-branch`). Likely sources: tests that call `cm_resolve_and_enforce_hook` directly without removing the materialized temp file, and `cm/release.sh`'s pre-tag step, which calls `_run_release_hook` bare under `set -e`, so a failing pre-tag hook exits before the `rm -f "$CM_RESOLVED_HOOK_PATH"` cleanup. Per SOP "Temporary File Convention", litter is a hygiene report, not a failure. It fits none of the three 6.11 categories, so I did not file it.
- **Cosmetic observation:** `cm/release.sh` output labels two different steps `[Step 12c]` ("Running pre-tag hook" and "Checking for a HALT-AFTER sentinel"). This predates the RC and is not in the diff.
- `VERSION` on the RC reads `v2.30.2`. This is expected: CM stamps it at release (Step 11d).
- Container tier not run (operator-only, Step 5d). Nothing in this RC touches Dockerfiles.

## Checkpoint log (verification steps as executed)

- **6.1 release-state:** `## Active RC` v2.30.3, `## Last Released` v2.30.2 (well-formed). `pp_last_released_version pgai-agent-kanban` → `v2.30.2`; `semver_gt v2.30.3 v2.30.2` → true. `VERSION` on the RC reads `v2.30.2`, which is expected: `release_notes.sh` Step 11d stamps it at release time (history: "Add committed VERSION for v2.30.2" etc.). Result: pass.
- **6.2 structural:** All 4 diff files present. Both touched shell libs are mode `100755` in the index and on disk. New unit test file `team/tests/unit/test_halt_after_invoke.py` exists. Result: pass.
- **Bundle AC "All Python files compile cleanly":** `python3 -m py_compile` over all 776 tracked `*.py` → 0 failures (pycache redirected to scratch). Result: pass.
- **Bundle AC "All bash files pass syntax check":** `bash -n` over all 321 tracked `*.sh` → 0 failures. Result: pass.
- **6.9 constraint negatives:** Added lines carry no bug/task/RC/version identifiers, no new inline Python or bare `python3 -m pgai_agent_kanban`, and no secrets (the one regex hit is `halt_after_token=`, a fixture parameter). `cm/release.sh` still runs `set -euo pipefail` (line 365). Neither touched function contains a `set -e`/`set +e` toggle now. Result: pass.
- **6.10 prereq graph:** PM-003 → CM-020 (open-rc) → CODER-019 → TESTER-021 → CM-023 (release; prereqs CODER-019 + TESTER-021). No cycles, all IDs exist, bookends correct, sequence numbers distinct. Result: pass.
- **6.12 autonomous:** see `## Autonomous criterion` below.
- **6.13 skip gate:** Neither changed test file has a skip marker (the only `skip` hits are the word "skips" in a test name and docstring). Result: pass.
