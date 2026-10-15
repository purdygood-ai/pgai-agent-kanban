# Verification Report: rc/v2.31.4

Task: TESTER-20261007-007-verify-2-31-4. Agent: tester. Model: claude-opus-5-5 (Opus 5.5).
RC: `ai_rc/v2.31.4` at ef23e754aa3d7f6b9a9ddbaa2a8ef24cf062fe23, verified in a detached worktree. Base: `ai_main` at 254153ee (v2.31.3).
Spec: `requirements/v2.31.4-bugfix-bundle-20261007.md`, which bundles BUG-0591 (idle PM discovery starves CM of the repo lock) and BUG-0592 (the doc step extractor skips tagged fences). The bundled bugs' `## Acceptance` sections are the operative criteria.

## Summary

I verified that RC v2.31.4 implements both bundled bugs. BUG-0591: discovery now runs its chain-busy test before any scan and exits in seconds, with no repo lock held. It scans unlocked and takes the lock only to write. A wake contended by another wake chain waits with backoff for up to `[wake] lock_wait_seconds`. A per-role starvation guard steps PM's discovery aside. BUG-0592: the step extractor pairs every fence per CommonMark through a new shared helper. Both gated runners exited 0 (unit: 10,134 passed, 41 skipped; integration: 946 passed, 1 skipped). Targeted `--files` runs also passed (unit 161, integration 57). Independent behavioral probes used real flocks held by separate processes, a real clock, production-shape queues copied from the live project, and failure-mode runs on an `ai_main` clone. Every guard fired as specified. One out-of-scope `bug` turned up: a third copy of the old fence regex in the doc-lint harness's command reader (pre-existing, unexercised today). One quality-class lint regression turned up: a Key Registry spacing slip makes the doc-truth lint report the new `lock_starvation_firings` key as unknown. Both are filed via Path C (autonomous follow-up filing — non-blocking).

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Findings

### Finding 1: BUG-0591 fixes 1-2 — busy-chain fast exit and unlocked discovery scan

**Category:** pass

Criterion: "PM backlog empty, a CM task BACKLOG. A PM wake exits in under 10 s, logs the chain-busy idle exit before any scan, and does not hold the repo lock during it." Evidence:
- Production-shape input (the live project's five queues, 1,209 lines, 1,136 status.md files): the RC idle-exits in 619 ms. It logs `chain busy — cm_backlog task ... is WAITING`, then `chain busy, idle exit before any scan`, with no ceilings or step line before it, no lock file, and the bug untouched. Worst case, with every queue entry read and the only busy task last in the writer queue: 1,346 ms.
- Failure mode on ai_main, identical input: 19,654 ms, and it minted a bugfix bundle while the chain was busy.
- Write-phase guard: an idle chain with a bug to bundle, while another process holds the repo lock (op `wake-chain`), logs `discovery_step_bugs: repo lock not available for the discovery write; deferring to the next wake`, writes nothing, and leaves the bug open. Once the holder releases, the write happens under `repo lock held for the discovery write` and the lock is released afterwards.
- `wd_run_pm_discovery` leaves an empty fd when discovery does not re-take the lock, and `pgai_lock_release` returns 0 on an empty fd before touching any file, so another holder's record cannot be clobbered.

### Finding 2: BUG-0591 fix 3 — wait for a wake-chain holder, bounded

**Category:** pass-with-caveat

Criterion: "a CM wake started 60 s after [an 80 s hold] acquires the lock within `lock_wait_seconds` and dispatches its task." Covered end to end by `test_wake_lock_wait_starvation.py` (real claude.sh wakes, fake clock; passed in the full and targeted integration runs). Independent real-clock probes of `wlw_acquire_chain_lock`:
- wake-chain holder 8 s, wait 20 s: acquired 7 s later, in the same second the holder released. Mutual exclusion held, and the provenance reads holder=cm op=wake-chain.
- non-wake-chain holder (op `overwatch-repair`): defers immediately with "its holder is not a wake chain".
- holder outlasts a 4 s wait: gives up at 5 s; a second attempt in the same firing logs "no repo-lock wait left this firing" and takes 0 s, so the budget is shared.
- stop file appears 3 s into a 30 s wait: stops at 3 s.
- batch budget leaves 3 s with lock_wait_seconds=60: waits 3 s.

Caveat: the wait can overshoot its budget by up to about 1 s (5 s observed for a 4 s budget) because the clock has whole-second granularity. That's cosmetic, and nothing depends on sub-second precision.

### Finding 3: BUG-0591 fix 4 — starvation guard

**Category:** pass

Criterion: "With the stub holding the lock longer than `lock_wait_seconds` for 3 consecutive CM firings, the starvation warning is logged and the next PM wake skips discovery." Covered end to end by `test_wake_lock_wait_starvation.py`. My probe of the guard functions (AGENT=cm, cm task BACKLOG): counts 1 of 3 and 2 of 3, then the WARNING at 3 and again at 4. `wlw_starved_role_ready` reports YES for role=cm from the third firing on, which is what makes run_project_chain's PM branch skip discovery. The count resets when cm acquires the lock and when its task leaves BACKLOG. `LOCK_STARVATION_FIRINGS=0` writes no state and never flags. Both new keys validate as decimal-digits-only (`test_config_loader_key_validation.py`).

### Finding 4: Two roles never hold the repo lock at once

**Category:** pass

Every acquisition is still a non-blocking `pgai_lock_acquire` flock (probe A above shows the waiter acquiring only after release). The existing lock tests pass in both gated runs and in the targeted runs: test_chain_lock_held_through_agent, test_wake_contention_retry, test_wake_stop_file_stops_batch, test_single_truth_state_integrity, test_lock_acquire_waiting.

### Finding 5: BUG-0592 — step extractor pairs fences per CommonMark

**Category:** pass

- Reproduction: ai_main returns `block_fence_line` 7 with a first command of "```bash", which is the original failure. The RC returns `block_fence_line` 9 and exactly one command, `echo real-command` (line 10). The control without the text block is also correct.
- Extra shapes under one heading (ini containing a '#' line, ~~~python, ````markdown wrapping ```bash, a bare block, ```bash, ```sh, ```json): block-index 0 gives `echo first`, 1 gives `echo second`, and 2 exits 1. Bare blocks no longer count as shell blocks; that is CODER's documented design choice, and the module docstring states it.
- docker.md regression baseline: all eight steps `strangers-path-check.sh` extracts (Steps 2-9; the bug says five) produce byte-identical JSON and `--tsv` output under ai_main's and the RC's extractor.
- Live-install layout (`scripts/harness/lib/` plus `pgai_agent_kanban/` at the root, cwd `/`, no PYTHONPATH): the extractor imports the shared helper and returns the fixed result.
- Unit pins exist for text, ini, ~~~python, ~~~, and a 4-backtick fence wrapping a 3-backtick one (`tests/unit/test_doc_step_extract.py`, `tests/unit/test_markdown_fence.py`).

### Finding 6: BUG-0591 documentation — docs/OPERATIONS.md

**Category:** pass-with-caveat

I read the new "Repo lock and contention" subsection and the edited cadence and post-discovery text end to end. Every quoted log line matches what the probes emitted, and the cron stagger claims match all four crontab templates. `kanban.cfg_example` documents both new keys (Documentation Discipline is met). Caveat: the `[wake]` key table has a `lock_wait_seconds` row but no `lock_starvation_firings` row. The writer left it out deliberately because of the registry-spacing lint defect. The key is covered in prose; the missing row is tracked in BUG-0594 (see Lint Findings).

### Finding 7: Bundle criteria — Python compiles, bash passes syntax check

**Category:** pass

The gated runner's breakage-class gate checked 770 Python and 318 shell files under `team/` with 0 failures, in both runs. No `.py` or `.sh` file outside `team/` changed.

### Finding 8: Gated suites

**Category:** pass

`UNIT_EXIT=0` (`run-unit-tests.sh --coverage`: 10,134 passed, 41 skipped, plus 5 serial-lane passes) and `INTEGRATION_EXIT=0` (`run-integration-tests.sh --coverage`: 946 passed, 1 skipped). Targeted `--files` runs, as BUG-0591 requires: unit 10 files, 161 passed, exit 0; integration 7 files, 57 passed, exit 0. Logs are under `/tmp/pgai_kanban_tmp/tester/v2.31.4-*.log`.

### Finding 9: Doc-lint harness command reader still drops bash blocks after a tagged fence

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (the third copy of a duplicated fence regex; the same defect class has now surfaced one copy per RC)
**Regression:** no
**Integrity:** none
**Filed As:** BUG-0593 (Path C-A — filed as BUG: code defect / regression / test failure)
**Filed By:** TESTER (autonomous Path C) — the command reader the doc-lint harness uses still only recognises bare and bash/sh code-block openers, so a guide with any other tagged code block before a bash block silently loses that bash block's commands.

Step 6.11 spot-check (regression of a prior gap). `team/pgai_agent_kanban/lint/doc_commands.py:47` keeps `^(`{3,}|~{3,})\s*(bash|sh)?\s*$`. This RC moved the other two Markdown readers onto `pgai_agent_kanban/lib/markdown_fence.py` but not this one. On the BUG-0592 reproduction doc, `python3 -m pgai_agent_kanban.lint.doc_commands` prints nothing (expected: `echo real-command`). On doc-lint.sh's five audited docs it drops 10 commands in 6 bash blocks: README.md:255, HOW_TO.md:277/736/1017, docs/docker.md:360/721, each verified to follow a json/ini/markdown block or to be indented. All six are `doc-lint: skip` annotated today, so no runnable command is lost yet, only SKIP accounting. Severity is medium, per SOP-core "Rating a Finding": a real but unexercised defect in a shipped artefact that predates this RC.

## Lint Findings (non-blocking)

- `doc_command_truth` — `UNKNOWN CONFIG KEY: [wake] lock_starvation_firings` at kanban.cfg_example:454. config_loader.sh's Key Registry line has one space before `OPTIONAL`, so the lint's `\s{2,}` parser skips it; the key itself loads and validates correctly. This RC introduced it (the lint exits 0 on an ai_main clone). Filed BUG-0594 (Path C-A; low).
- Integration findings file: `None.` (0 findings).

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=29d freshness=stale — The last cold-build record is red, 29 days old (outside the 7-day window) and from v2.9.20, not v2.31.4; that run filed BUG-0167 (claude-abliterated FROM parse error, podman/rocky9 COPY --chmod), now done, and nothing is known about whether this RC's images build.

## Coverage (informational)

Coverage unavailable: both runners printed `coverage disabled by config ([testing] coverage)`, which is the default `false`, and the coverage JSONs under `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/` are stale (2026-10-04), so I did not use them.

## Autonomous criterion

1. Tasks force-promoted WAITING to BACKLOG by hand: NO. Each of the 7 cycle tasks has exactly one `dispatched` record; promotions carry the wake script's text.
2. release-state.md edited outside the CM scripts: NO. Its mtime (2026-10-07 03:04:44.35Z) matches open-rc.sh's recorded RC Opened At, as CM-006's summary states.
3. Scripts patched mid-build: NO. No file under the live install's scripts/, pgai_agent_kanban/, roles/ or pm-agent/ changed after the RC opened. RC commits to team/scripts/ are the tickets' own work.
4. Queue files edited by hand: NO. Markers agree with each task's status.md ([x] DONE, [W] CM-009 WAITING, [A] this task WORKING).
5. Remote git operation by a non-CM agent: NO. No `git-boundary-guard.jsonl` exists in any cycle task's logs/. push_to_remote=false.

Autonomous operation criterion: Manual interventions required: none

## Caveats

- **Steps 1/2 now wait for an idle chain.** PM-001 asked TESTER to confirm this is acceptable. BUG-0591 fix 1 mandates it. Bugs and priorities are bundled only when every role-queue task is DONE or WONT-DO and no RC is active. Before this RC a busy chain still minted the bundle, but Step 3 never queued it until the chain went idle, so when RCs open is unchanged. One side effect for operators: a long-lived BLOCKED task in any queue now also defers *minting*, not just queuing. OPERATIONS.md's operator implications describe this ("the file waits").
- **Starvation edge (observation, unexercised).** A role's count at or past the threshold keeps PM skipping discovery for as long as that role's task stays BACKLOG and the role never takes the lock. If that role's cron were removed, PM discovery would stay off until the task changes state. That's within the spec ("while that role's task is ready"), so I note it only.
- `team/scripts/wake/harness.sh` keeps its own single-attempt `run_project_chain`. It is a test-only stub provider ("NEVER be used in production"), so this is an observation, not a finding.
- The container tier was not run (operator-only, Step 5d). The extractor's end-to-end consumer, `strangers-path-check.sh`, runs only there; I verified its eight extractions directly.
- No prior `report.md` / `gaps.md` or `.previous-RUN-N` files existed; this is a first run.

---

# Verification log (checkpoints written during the run)

## Pre-flight (Steps 1-3)

- Step 1: task found in WORKING with no partial report and a wake-promotion Summary; treated as fresh start. No prior `report.md` / `gaps.md` or `.previous-RUN-N` files in artifacts/.
- Step 2: 644 DONE task folders name a feature branch; none of those branches exist locally (post-merge deletion), so Check 1 and Check 2 pass trivially. The four RC tickets each have a `--no-ff` merge commit on `ai_rc/v2.31.4` (578dcd19, 712e8c96, 25e9f203, ef23e754). Anomalies: 0.
- Step 3: worktree HEAD ef23e754aa3d7f6b9a9ddbaa2a8ef24cf062fe23 == `ai_rc/v2.31.4`; `git status --porcelain` empty; `git stash list` empty.

## Checkpoint — Step 4 / 6.2-6.4 (BUG-0592) and spot-checks

- Diff `ai_main..ai_rc/v2.31.4`: 32 files, +3091/-208, 11 commits (4 merges). All four PM tickets landed.
- BUG-0592 repro: ai_main extractor -> `block_fence_line` 7 and first cmd "```bash" (original failure reproduced); RC extractor -> `block_fence_line` 9, exactly one command `echo real-command` (line 10). PASS.
- Extra shapes (ini with '#' line, ~~~python, ````markdown wrapping ```bash, bare block, ```bash, ```sh, ```json): block-index 0 -> `echo first`, 1 -> `echo second`, 2 -> exit 1 "only 2 bash/sh fenced block(s)". PASS.
- docker.md baseline: all EIGHT steps strangers-path-check.sh extracts (Steps 2-9; the bug said five) give byte-identical JSON and --tsv output under ai_main's and the RC's extractor (docker.md unchanged by the RC). PASS.
- Live-install layout (scripts/harness/lib + pgai_agent_kanban/ at root, cwd=/ , no PYTHONPATH): extractor imports markdown_fence and returns the fixed result. PASS.
- Spot-check (6.11): `team/pgai_agent_kanban/lint/doc_commands.py:47` still carries the old bare/bash/sh-only regex. On the BUG-0592 repro doc it returns NO commands (drops `echo real-command`). On doc-lint.sh's audited docs it drops 10 commands in 6 bash blocks (README.md:255, HOW_TO.md:277/736/1017, docs/docker.md:360/721) — all six are `doc-lint: skip` annotated today, so no runnable command is lost yet. Pre-existing on ai_main.
- Doc-truth lint: RC exits 1 with `UNKNOWN CONFIG KEY: [wake] lock_starvation_firings` (kanban.cfg_example:454) because config_loader.sh's Key Registry line has one space before OPTIONAL; ai_main clone exits 0. Introduced by this RC. The gated runner classifies it quality-class, non-blocking.
- `team/scripts/wake/harness.sh` keeps its own single-attempt run_project_chain override: test-only stub provider ("NEVER be used in production") — observation, not a finding.

## Checkpoint — BUG-0591 behavioral probes (Step 6.5), sandboxed throwaway roots

Probes under /tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester-scratch/probes (roots built with team/tests/fixtures/two_project.py, env -u PGAI_TASKS_DIR -u PGAI_PROJECT_ROOT, KANBAN_ROOT/PGAI_AGENT_KANBAN_ROOT_PATH/TEMP_DIR repointed). External lock holders are separate processes taking real flocks via pgai_lock_acquire; real wall clock, no stubs.

- Busy chain, production-shape input (the live project's 5 queues, 1209 lines, + 1136 copied status.md, + one open bug): RC idle-exits in 619 ms with `chain busy — cm_backlog task ... is WAITING` then `chain busy, idle exit before any scan`, no ceilings/Step line, no lock file, bug untouched. Worst case (only busy task last in writer queue, every entry read): 1346 ms. ai_main on an identical copy: 19,654 ms and it MINTED a bundle on the busy chain (original failure mode). PASS (< 10 s criterion).
- Idle chain + bug, repo lock held by another process (op wake-chain): `discovery_step_bugs: repo lock not available for the discovery write; deferring to the next wake`, status idle, no bundle, bug `## Status` still open. After the holder released: write happened under `repo lock held for the discovery write`, bundle minted, bug -> running, lock released (no lock file). PASS (guard fires).
- Lock wait (wlw_acquire_chain_lock, AGENT=cm):
  - A wake-chain holder 8 s, lock_wait_seconds=20: waited, acquired 7 s later in the same second the holder released (mutual exclusion held), provenance holder=cm op=wake-chain. PASS.
  - B non-wake-chain holder (op overwatch-repair): immediate rc=1 "its holder is not a wake chain". PASS.
  - C holder 12 s > lock_wait_seconds=4: rc=1 after 5 s "still held after waiting"; second call same firing: "no repo-lock wait left this firing", 0 s. PASS (1 s overshoot from whole-second clock — caveat only).
  - D stop file appears 3 s into a 30 s wait: stops at 3 s "stop file present". PASS.
  - E batch budget left 3 s with lock_wait_seconds=60: "waiting up to 3s ... batch budget left=3s", gives up at 3 s. PASS.
- Starvation guard (AGENT=cm, cm task BACKLOG): 1 of 3, 2 of 3, WARNING at 3 and 4; wlw_starved_role_ready YES role=cm at >=3; reset on acquire; reset when task DONE ("no BACKLOG task"); LOCK_STARVATION_FIRINGS=0 writes no state and never flags. PASS.
- pgai_lock_release with the empty fd wd_run_pm_discovery leaves when discovery did not re-take the lock: returns 0 before touching anything (no clobber of another holder). PASS.
- docs/OPERATIONS.md (WRITER): read end to end; every log line matches what the probes emitted; cron stagger claims match all four crontab templates. `[wake]` table lists lock_wait_seconds but not lock_starvation_firings (WRITER's deliberate omission because of the registry-spacing lint defect; key is documented in prose and in kanban.cfg_example).

## Checkpoint — Steps 6.1, 6.9, 6.10, 6.12-6.14

- 6.1: release-state.md: Active RC v2.31.4, Last Released v2.31.3 (well-formed). pp_last_released_version=v2.31.3; semver_gt v2.31.4 v2.31.3 = YES. PASS.
- 6.9: no secrets/curl|sh in added lines; pm-agent.sh `set -euo pipefail` (line 71) preserved. PASS.
- 6.10: PM-001 -> CM-006 -> {CODER-002, CODER-005} ; CODER-003 <- 002 ; WRITER-004 <- 002,003 ; TESTER-007 <- 002,003,004,005 ; CM-009 <- all incl. 007. No cycles, all IDs exist, sequence 008 unused (no approval task: Human Approval Required = auto). PASS.
- 6.13: no skip/xfail annotations in tests this RC added or changed; lint_skip_bug_gate OK in runner pre-flight. PASS.
- 6.14: no BUG/task/version identifiers in added code/comment lines; new files are sourced libs (no --help needed); no new hardcoded operational value (wait/threshold are tunables; 1 s/8 s backoff constants pre-exist). The markdown_fence consolidation reduces duplication, but doc_commands.py keeps a third copy (see spot-check finding). PASS except as filed.
