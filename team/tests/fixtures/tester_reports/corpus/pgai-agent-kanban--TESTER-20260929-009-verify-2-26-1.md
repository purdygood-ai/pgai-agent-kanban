# Verification Report: ai_rc/v2.26.1

## Summary

Verified RC `ai_rc/v2.26.1` (HEAD 0422f8aa79f534f2ce2269b750bd392ecd00346a; `ai_main` at d5f58be) against `requirements/v2.26.1-bugfix-bundle-20260929.md`, a seven-item BUG bundle (BUG-0431 through BUG-0437) whose per-item `## Acceptance` sections are the testable spec. The RC is `ai_main` plus six CODER merges (34 files, +4386/-591). Both gated runners exited 0 (`run-unit-tests.sh --coverage`: UNIT_EXIT=0, 5477 passed / 43 skipped; `run-integration-tests.sh --coverage`: INTEGRATION_EXIT=0, 631 passed / 1 skipped). Every python file compiles and every bash file passes `bash -n`. Each of the seven fixes was exercised in a throwaway sandbox by triggering its original failure mode and its new normal case; all seven behaved as their bug's `## Expected` describes. The five autonomous-operation checks are all NO. Two quality-class lint findings (engine-size, hygiene/provenance) were ingested and filed or cited per Step 5c; they do not enter the verdict. No `bug`, `gap`, or `stale-assertion` finding was recorded.

Gloss of internal tokens used below: `Path C` (autonomous follow-up filing — non-blocking); `Path C-A` (filed as BUG — code defect / regression / test failure); `Path C-B` (filed as PRIORITY — enhancement / design change); `BLOCKED` (verification could not complete — infrastructure failure; NOT "found a blocker").

## Recommendation
PASS

## Systemic Risk
low

## Suite results (Steps 5a, 6.6, 6.7)

| Runner | Exit | pytest line | Lint pre-flight | Post-suite cleanliness |
|---|---|---|---|---|
| `team/scripts/run-unit-tests.sh --coverage` | **UNIT_EXIT=0** | 5477 passed, 43 skipped in 1066.80s | breakage class clean; 2 quality classes (see Lint Findings) | bare_tmp, listener, sentinel_leak, scratch_sweep all OK |
| `team/scripts/run-integration-tests.sh --coverage` | **INTEGRATION_EXIT=0** | 631 passed, 1 skipped in 886.16s | same 2 quality classes | all OK |

Run sequentially from the dev-tree root (`PGAI_DEV_TREE_PATH` = the task worktree) with output tee'd to `/tmp/pgai_kanban_tmp/tester/unit-v2.26.1.log` and `.../integration-v2.26.1.log`; exit codes captured in `.../suite-exits-v2.26.1.txt`. The PASS evidence is the two runner exit codes, not the pytest lines. The unit suite includes the RC's new real-clock watchdog module (60 s deadline) and the lock race modules; all green.

## Coverage (Step 5b — informational only)

From `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/{unit,integration}.json` `totals` (keys verified: `percent_statements_covered`, `percent_branches_covered`, `percent_covered`):

- unit: line 77.51 % (14375/18546 statements), branch 63.61 % (3459/5438 branches); blended 74.36 %.
- integration: line 13.31 % (2467/18537), branch 10.32 % (561/5438); blended 12.63 %.

## Findings

### Finding 1: Requirements AC — every bundled item is addressed in the implementation

**Category:** pass

Each of the seven BUG items maps to a merged CODER task whose diff lands the fix named in the bug's `## Files Involved`: BUG-0431 → CODER-002 (`metrics_rollup_refresh.sh`, finalize call site); BUG-0432 and BUG-0433 → CODER-003 (two test files); BUG-0434 → CODER-004 (`provider_claude.sh`, `provider_codex.sh`, finalize routing, chain stop); BUG-0435 → CODER-005 (`wake_driver_invoke.sh` job-control launch and group kill, `wake_driver_finalize.sh` drain); BUG-0436 → CODER-006 (`lock_provenance.sh` classifier and refresh, `release-locks.sh --force`, config examples); BUG-0437 → CODER-007 (`lock_provenance.sh` staged-inode acquire, unlink-only release, freshness window). Behavioral evidence per item is in Findings 8–14.

### Finding 2: Requirements AC — all Python files compile cleanly

**Category:** pass

`find . -name '*.py' -not -path './.git/*' | xargs python3 -m py_compile` over the whole worktree: exit 0. The 14 changed `.py` files were also compiled individually: all ok.

### Finding 3: Requirements AC — all bash files pass syntax check

**Category:** pass

`bash -n` over every `.sh` in the worktree: 0 failures. The 16 changed `.sh` files were also checked individually: all ok. Executable bits: every changed script under `team/scripts/` is `-rwxr-xr-x`, including the new `team/scripts/lib/metrics_rollup_refresh.sh`.

### Finding 4: Pre-flight — RC intact, worktree clean (Steps 2, 3)

**Category:** pass

`git rev-parse HEAD` == `git rev-parse ai_rc/v2.26.1` (0422f8a). `git status --porcelain` empty. `git stash list` empty. 467 DONE task folders with a non-`none` feature branch scanned; none of those branches still exist locally (normal post-merge shape), so Checks 1 and 2 pass trivially. The two surviving `ai_feature/*` branches (CODER-20260926-017, CODER-20260927-005) belong to WONT-DO tasks, outside Step 2's DONE scope. `merge-base(ai_main, ai_rc/v2.26.1)` = `ai_main` tip, so the RC contains exactly the six `--no-ff` merges (3367de8, b16f18e, 940b6f8, 5e30f94, baae8de, 0422f8a).

### Finding 5: release-state.md sanity (Step 6.1)

**Category:** pass

`release-state.md`: Active RC v2.26.1, RC Opened At 2026-09-29T04:47:31+00:00, RC Opened By Task CM-20260929-008-open-rc-2-26-1, Last Released v2.26.0 (well-formed). `semver_gt v2.26.1 v2.26.0` → true (via `team/scripts/lib/semver.sh`); `pp_last_released_version pgai-agent-kanban` → v2.26.0. Committed `VERSION` reads v2.26.0 (CM updates it at release).

### Finding 6: Structural and content review (Steps 6.2, 6.3)

**Category:** pass

New files read fully: `team/scripts/lib/metrics_rollup_refresh.sh` (155 lines; sourced from `wake_driver.sh` ahead of the finalize stage; its three inputs `task_dir`, `KANBAN_ROOT`, `_CURRENT_PROJECT` are all set where `wd_finalize_task` runs — `_CURRENT_PROJECT` at `wake_driver.sh:374`, `KANBAN_ROOT` exported by `wake_bootstrap.sh:84`), and the five new test modules. Modified files read at the changed regions: `lock_provenance.sh` (+856/−? — header rewritten for the staged-inode invariant, truncate-free release, orphan-win retry, held-lock refresh, and the two-condition reap rule; `_pgai_lock_resolve_chain_budget` now adds `max_task_seconds`), `provider_claude.sh` (five-function interface, memo files under `pgai_temp_dir`), `provider_codex.sh` (always-no fifth function), `wake_driver_invoke.sh` (`set -m` launch, group TERM/KILL, poll-loop lock refresh), `wake_driver_finalize.sh` (`wd_drain_provider_process_group` first in `wd_finalize_task`; `requeue_after_dispatch_refusal`; rollup refresh before final-state resolution), `wake_driver.sh` (three new locals, chain stop on refusal), `release-locks.sh` (`--force` signals, exit 5), `config_loader.sh`, `verdict.sh`, `why_details.sh`. Wiring the new code reads through bash dynamic scope was confirmed to exist under the exact names used (`_project_lock_file`/`_project_lock_fd` at `wake_driver.sh:396-427`; `transition_task_state` in `wake_task_transition.sh:111`; `set_block` in `wake_task_state.sh:219`).

Documentation-layer files: `kanban.cfg_example` ([wake] and [locks] comments now state the group kill, the finalize wait, the refresh, and the new floor `max_runtime + max_task + kill_grace + 120`), `project.cfg_example`, `docs/OPERATIONS.md` (rollup refresh), `docs/operator-commands.md` and `docs/operator-api.md` (`--force` semantics, exit 5; `--help` transcript regenerated and matches the script's `_USAGE_TEXT`), `docs/blocking-site-census.md` (rows 67–68 and the three new anchors; rollup 68 sites). Same-commit documentation for every tunable or behavior change: satisfied.

### Finding 7: Executable acceptance criteria — BUG-0432 and BUG-0433 (Step 6.4)

**Category:** pass

BUG-0432 (`tests/unit/test_discovery_git_local_only_wake_context.py`): `PGAI_GIT_LOCAL_ONLY=1` → 4 passed; `env -u PGAI_GIT_LOCAL_ONLY` → 4 passed; `PGAI_GIT_LOCAL_ONLY=` → 4 passed. The gated unit run (which inherits the wake's `PGAI_GIT_LOCAL_ONLY=1`) lists no failure in this module. BUG-0433 (`tests/unit/test_event_band_renderer.py::test_large_pre_today_body_renders_correctly_and_narrates_render_time`): 5/5 at ambient load, then 10/10 with seven busy-loop CPUs on the 8-core host — runs 1–6 rendered in 9.58–12.02 s (over the 5 s budget), each narrated `OVER` with a `RenderTimeOverBudgetWarning`, and still passed; runs 7–10 rendered in 4.2–4.95 s. The guard's failure mode (over budget) was therefore triggered and confirmed non-gating. Caveat: the bug's criterion names a 1-minute load average ≥ 5; my loadavg peaked at 4.09 during the timed runs because the average lags the contention, but the 2–3× render slowdown shows the contention was real, and the CODER task's own `artifacts/render-test-under-load.log` records 10/10 at loadavg 5.09–9.31.

### Finding 8: BUG-0431 — rollup counts the CM release task (Step 6.5)

**Category:** pass-with-caveat

Sandbox (synthetic kanban root, dev-tree aggregator): open-rc task with `tokens.json`, release task without; `metrics_aggregator.py --rc v1.4.2` → `tasks.by_agent == {cm: 1}`. Release task's `tokens.json` (5000 in / 900 out / 300 s) written; `metrics_rollup_refresh_for_task <release_task_dir> <root> probe` under `set -euo pipefail` → rc 0, rollup now `{cm: 2}`, `tokens.by_agent.cm` input 6000 / output 1100 / invocations 2, `wall.by_agent.cm` 390 s over 2 tasks. Second refresh: byte-identical. A failing `python3` on PATH → one `[metrics] WARNING … exited with code 7` line, rc 0 under strict mode, rollup bytes unchanged. The finalize call site runs once per task before final-state resolution and never touches `final_state`. Caveat (from the bug's third acceptance line and CODER-002's summary): the live install's wake driver finalizing this RC's own release task predates the fix, so `metrics/rc/v2.26.1.json` will still read `cm == 1`; the first RC finalized after v2.26.1 is installed is the first that can show `Avg Tasks (/RC) = 2.0`. `history.csv` keeps finalize-time figures by design.

### Finding 9: BUG-0432 / BUG-0433 — gated unit suite no longer depends on wake env or host load (Step 6.5)

**Category:** pass

Covered by Finding 7 and by UNIT_EXIT=0 from a TESTER wake that exports `PGAI_GIT_LOCAL_ONLY=1`. The v2.26.0 verification could not reach PASS for exactly these two tests; both are now green under the conditions that failed them.

### Finding 10: BUG-0434 — CLI too old for the model is an infrastructure refusal (Step 6.5)

**Category:** pass

Sandbox with a fake `claude` on PATH (reports 2.1.276; prints the real `unrecognized_model` / "does not support this model; version 2.1.280 or newer is required" pair on stderr, a zero-usage JSON error on stdout, exit 1), the real `provider_claude.sh` sourced the way `wake/claude.sh` does. (1) Preflight with no memo: rc 0. (2) Real `provider_dispatch` for TASK-PROBE-1: `PROVIDER_AGENT_EXIT_CODE=1`, `provider_dispatch_refused` → true, log carries `error: provider-claude-cli-too-old-for-model`, a message naming 2.1.276, the model and 2.1.280, and `remedy: claude update && claude --version`; memo `claude-opus-5-5<TAB>2.1.280`. (3) Next-wake preflight with the same old CLI: rc 1 with the same dual-channel key (row 68). (4) Preflight for a different model: rc 0 (memo is per model). (5) Fake CLI at 2.1.284: rc 0 and the memo is cleared. (6) A 529 overloaded error never matches the signature. (7) A subsequent successful dispatch of the same task clears the stale per-task memo. The finalize routing (`requeue_after_dispatch_refusal`: BACKLOG, `## Zero Progress` untouched, Needs Human unchanged) and the token-capture `NOT STARTED` line are exercised by `test_provider_cli_too_old_refusal.py` (18 tests, green in the gated run), which runs the real functions against the same fake CLI. The wake names the signature itself; OVERWATCH's transient classifier was deliberately left unchanged (a non-transient 400 does not belong on the auto-requeue list), which satisfies the bug's "or the wake" wording.

### Finding 11: BUG-0435 — watchdog kills the whole provider process group (Step 6.5)

**Category:** pass-with-caveat

Sandbox running the real watchdog block (extracted between `WATCHDOG-INVOKE-BEGIN/END`) and the real `wd_drain_provider_process_group` against a stub that installs `trap '' TERM`, double-forks a `sleep 600` grandchild, and blocks in a `sleep 600` foreground, with `MAX_TASK_SECONDS=6 KILL_GRACE_SECONDS=3`: log shows "sending SIGTERM to provider process group <pgid>", "grace period (3s) expired; … sending SIGKILL to the group", "provider process group <pgid> gone 3s after the 6s deadline; finalizing"; `_task_killed=true`, exit 124, `_provider_group_sigkilled=true`, 0 group members after the drain, and `pgrep -af <marker>` empty — no orphan. `test_wake_watchdog_process_group_kill.py` (10 tests, real 60 s clock, hostile and cooperative stubs) is green in the gated run, and the finalize drain is structurally first in `wd_finalize_task` (before any state write, marker case, or worktree teardown). Caveats, both already recorded by CODER-005: in the hostile case the wrapper subshell dies with the group, so its post-provider bookkeeping (exit-code file, `tokens.json`) does not run — the timeout still resolves to 124 and the task still finalizes; and a descendant that starts its own session (`setsid`) is outside the group and would escape the PGID kill — not the shape the bug names, noted for human consideration.

### Finding 12: BUG-0436 — a live lock holder is never reaped on age (Step 6.5)

**Category:** pass

Sandbox against the RC's `lock_provenance.sh` with the shipped-default wake values (`MAX_RUNTIME_SECONDS=14400 MAX_TASK_SECONDS=5400 KILL_GRACE_SECONDS=30`; `pgai_lock_chain_budget_floor_seconds` → 19950, i.e. the new `max_task_seconds` addend is live). A real `pgai_lock_acquire` holder (pid alive), lock file backdated 10 h (> floor): contender rc 1, stderr keys `lock.stale_threshold.floor_enforced`, `lock.provenance.holder_overdue` (once), `lock.provenance.contended`; the file, its inode and its record (holder A's pid) untouched; holder still alive. `kill -9` the holder: next acquire → `lock.provenance.stale_reaped.dead_pid`, reacquired, released cleanly (no file left). `release-locks.sh --release NAME --force` against a live holder signals it and never unlinks or swaps its file (unit tests `test_force_signals_a_cooperative_live_holder…` and `test_force_never_unlinks_a_live_holder_that_ignores_sigterm`, exit 5 with `release_locks.holder_not_released`; the REST twin test was rewritten to a real subprocess holder). The poll-loop refresh (`_wd_refresh_project_lock_mtime` every `_PGAI_LOCK_HELD_REFRESH_INTERVAL_SECONDS`=60) is exercised on a real clock by `test_watchdog_loop_refreshes_the_held_lock_mtime_while_the_provider_runs`. The `aged_out` reap key no longer exists in the library (`test_no_age_alone_reap_key_survives_in_the_lock_library`).

### Finding 13: BUG-0437 — provenance appears atomically; release/acquire race closed (Step 6.5)

**Category:** pass

Sandbox: an empty file at the lock path whose flock is held by a foreign process and which is fresh (< 5 s): contender rc 1, contention narration names the freshness window, file untouched (size 0). Same file backdated 10 s: reaped (`lock.provenance.stale_reaped.no_provenance`) and acquired. A reader polling the lock path during 150 acquire/release pairs: 450 successful reads, 0 empty, 0 torn (every read was a complete four-field record). Two concurrent loopers, 40 attempts each on one path: 59 holds, 0 overlapping hold intervals, 0 reap events, 0 `release_not_owner` narrations, 0 `*.tmp.*` staging litter. `test_lock_provenance_appears_atomically.py` (injected 0.2 s post-flock/post-unlink delays, orphan-inode win retried) is green in the gated run; CODER-007's status records that the same harness against the pre-fix library produced 4 holder overlaps and 4 reaps of a held lock.

### Finding 14: Synthetic release simulation (Step 6.8)

**Category:** pass

The diff touches no release-lifecycle file (`cm/release.sh`, `cm/open-rc.sh`, `cm/cancel-rc.sh`, `lib/project_paths.sh` all unchanged); the new finalize-stage code is the wake driver's, not the release path's. Step 6.8 is therefore not required; recorded for completeness: `tests/integration/test_cm_release_synthetic.py` ran inside the gated integration suite — 17 passed.

### Finding 15: Constraint negative checks (Step 6.9)

**Category:** pass

No hardcoded secrets in the diff. `set -euo pipefail` preserved in `release-locks.sh`; `lock_provenance.sh`'s trap/finally sweep unchanged. No `BUG-NNNN`, task ID, RC or framework-version citation on any added line of shipped code, config examples, or docs; the only ID-shaped strings added under `team/tests/` are fixture task IDs (`CODER-20260101-04x-fixture-*`, `CODER-20260517-002-some-work`) used as example values, a sanctioned exception. Every new refusal site carries a census anchor and a class comment (`provider-claude-01/02` BLOCK-WORTHY, `lock-holder-overdue-01` CONVERT, `release-locks-holder-not-released-01/02` BLOCK-WORTHY) and the gated census/dual-channel/runnable-remedy lints report 0 offenders.

### Finding 16: Prerequisite dependency graph (Step 6.10)

**Category:** pass

CM-20260929-008 (open-rc, prereqs none) → CODER-002/003/004 (prereq CM-008) → CODER-005 (CM-008, CODER-004) → CODER-006 (CM-008, CODER-005) → CODER-007 (CM-008, CODER-006) → TESTER-009 (all six CODERs) → CM-20260929-011 (all six CODERs + TESTER-009). No cycles; every referenced ID exists; all six CODERs DONE; CM-011 WAITING on this task. Sequence 010 is unused (no task folder) — a gap in numbering, not a collision.

### Finding 17: Spot-check outside the spec (Step 6.11)

**Category:** pass

State drift: none found. CM-008's open-rc summary observed "no v2.26.x tag exists"; the tag is `ai_v2.26.0` on d5f58be — tags carry the project's `ai_` prefix, so the observation was a false alarm and `pp_last_released_version` agrees with the tag. Untested new mechanisms: `run_project_chain`'s four-line break on `LAST_TASK_DISPATCH_REFUSED` (CODER-004 notes it is covered by `bash -n` and reading; the flag it reads is set by the tested finalize function) and `pgai_lock_acquire`'s attempt-exhaustion branch (`_PGAI_LOCK_ACQUIRE_MAX_ATTEMPTS`=4, declines as contended) have no direct test — both are short, read correct, and are recorded as observations, not findings. Regressions of prior gaps: the v2.26.0 verification's two suite failures (BUG-0432/0433) are fixed; `wake_driver_invoke.sh` has re-crossed the 800-line ceiling that BUG-0350 once fixed — a quality-class lint finding, handled under Lint Findings.

### Finding 18: Skip-cites-real-bug grep-gate (Step 6.13)

**Category:** pass

No `BUG-SKIP` or `skip … BUG-<letters>` shapes in `team/tests/`. The only `BUG-NNNN` strings near `skip` are fixture literals inside `tests/unit/test_skip_bug_citation_scan.py` (the lint's own test data, `BUG-1234`/`BUG-1235`) and a historical disposition table under `tests/artifacts/` — none is a live skip annotation. The gated `lint_skip_bug_gate` pre-flight passed in both runs.

### Finding 19: Coding-standards spot-check (Step 6.14)

**Category:** pass-with-caveat

New lib `metrics_rollup_refresh.sh` is a sourced library (help-presence lint: 0 offenders, 1 exempt). Four new lock constants are hardcoded with a stated rationale for not exposing a key (`_PGAI_LOCK_UNPROVENANCED_REAP_MIN_AGE_SECONDS`=5, `_PGAI_LOCK_ACQUIRE_MAX_ATTEMPTS`=4, `_PGAI_LOCK_HELD_REFRESH_INTERVAL_SECONDS`=60, margin 120) — borderline against "operational value an operator would want configurable", accepted given the reasoning in each header. Caveat: two existing test modules still describe the removed two-step release in prose — `tests/unit/test_lock_release_atomicity.py` lines 8–9, 20–22, 51–53 ("the two-step release (clear provenance, then remove the lock file)", "a seeded crash between sidecar removal and lock removal") and `tests/unit/test_lock_ownership_reverification.py` line 29 ("release truncates and removes whatever currently sits at the path"). The tests themselves still assert real behavior (an unflocked empty file at the path is recovered on the next acquisition; abandon never touches the new holder's file) and pass; only the stated mechanism is stale. CODER-007 flagged this in its status. Left as a caveat for the next lock-area ticket; not filed, because no assertion is wrong and the hygiene lint's comment-drift class did not fire on it.

### Finding 20: Test fidelity

**Category:** pass

The RC's new tests source the real production files and run real functions: `provider_dispatch` against a fake CLI on PATH (not a reimplementation), the real watchdog block on a real clock against a double-forking stub, real `pgai_lock_acquire` holders in subprocesses (never the test's own pid), the real `release-locks.sh` and the real aggregator CLI. No mocked-away breaking condition was found. Every process a test starts is recorded and killed on every exit path; the gated runs left no strays (listener/scratch/bare-tmp checks OK).

## Lint Findings (non-blocking)

From `${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-unit.json` (3 findings, generated 2026-09-29T09:05:52+00:00) and `findings-integration.json` (3 findings, 09:21:07+00:00); the two files carry the same two classes. The runners' own aggregate filer created no new bug file during either run (no `bugs/BUG-*.md` newer than 08:45 before my filings).

- `engine_size_dupe` — 2 engine files over the 800-line ceiling: `lib/wake_driver_invoke.sh` 827 lines (new in this RC: 800 at 940b6f8/5e30f94, 827 after merge baae8de, CODER-006; a recurrence of the file's earlier overflow fixed under BUG-0350) — **filed BUG-0454** (Path C-A, low); `lib/cm/release_ship_policy.sh` 844 lines (pre-existing) — cites BUG-0428 (running).
- `hygiene` / class `provenance` — `release-notes/v2.26.0.md:33` cites `requirements/v2.26.0-agent-metrics-v2.md` (pre-existing on `ai_main`, not covered by any open bug) — **filed BUG-0455** (Path C-A, low); `release-notes/v2.25.2.md:44` cites task ID `tester-012` (pre-existing) — cites BUG-0430 (running).

Both bugs were filed solely to track quality-class lint findings; per Step 5c they do not move `## Recommendation` or `## Systemic Risk`.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=22d freshness=stale — stale on both counts (22 days is outside the 7-day window and v2.9.20 is not v2.26.1); that run's own bug for the claude-abliterated and podman/rocky9 build failures is BUG-0167, already closed, and this project's `project.cfg` sets `deep_verify = false`, so nothing is known about whether the three Dockerfiles build at this RC.

## Autonomous criterion (Step 6.12)

Autonomous operation criterion: Manual interventions required: none.

1. Tasks force-promoted from WAITING to BACKLOG by hand: **NO** — every cycle task's status shows the wake's own promotion text; queue markers agree with states (`[x]` for the six CODERs and CM-008, `[A]` for this task, `[W]` for CM-011).
2. `release-state.md` edited outside the CM scripts: **NO** — its mtime (04:47:31) equals the `RC Opened At` written by `cm-open-rc.sh` via CM-008, and the four-field body is canonical.
3. Scripts patched mid-build: **NO** — every commit on the RC after d5f58be is a CODER feature commit or its `--no-ff` merge, each tied to a bundled ticket; no tooling change outside those tickets.
4. Queue files edited by hand: **NO** — all queue edits carry wake-script shapes and timestamps consistent with the agents' own transitions; the queues directory's last edit (00:23:05) is the v2.26.0-cycle operator reset recorded in `logs/reset.log`, before this RC opened.
5. Non-CM remote git operation: **NO** — no `logs/git-boundary-guard.jsonl` exists in any of the eight cycle task folders (CM-008, PM-001, CODER-002..007, TESTER-009).

The one operator action in `logs/reset.log` today (CODER-20260928-015 reset at 00:23:05) belongs to the v2.26.0 cycle and is the subject of BUG-0434, fixed in this RC.

## Caveats

- BUG-0431's third acceptance line (`agent-metrics.sh --last 1` showing CM 2.0 right after a release) cannot be observed until the RC after this one ships under the installed fix; `metrics/rc/v2.26.1.json` itself will still read `cm == 1`. The next TESTER should check `metrics/rc/v2.26.1.json` is refreshed to `cm == 2` once the live driver carries the change and the following RC closes.
- BUG-0435 known limits (Finding 11): hostile providers lose the wrapper's bookkeeping; a `setsid`'d descendant escapes the group kill.
- Stale two-step-release prose in two lock test modules (Finding 19).
- CM-008's open-rc summary also noted that the canonical rewrite of `release-state.md` dropped a `## Campaign Spend Event` block written by `release_campaign_spend.sh` at the v2.26.0 ship. `SOP-core.md` defines the file as exactly four fields, so the rewrite is by design; noted for human consideration only, outside this RC's spec.
- Sequence number 010 in the 20260929 task series is unused.
- The worktree was verified in detached HEAD per the wake prompt; the task README's `## Feature Branch` is `none` and the wake prompt forbids creating one, so verification artifacts live only in this task folder and were not committed into the dev tree (the role file's "Git workflow" section describes a feature-branch commit; the more specific task instruction wins).
- Scratch and sandbox output is under `/tmp/pgai_kanban_tmp/tester/` (suite logs, `lock-sandbox.out`, `cli-probe2.out`, `bug0433-runs.log`, sandbox roots).
