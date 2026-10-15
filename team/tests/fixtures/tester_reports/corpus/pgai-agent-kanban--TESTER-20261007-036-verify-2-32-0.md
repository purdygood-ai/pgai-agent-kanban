# Verification Report: rc/v2.32.0

## Summary
This report verifies `ai_rc/v2.32.0` (HEAD fa211742; 12 `--no-ff` merges from CODER-20261007-023..033 and WRITER-20261007-034) against `requirements/v2.32.0-archive-finished-items.md`. That requirement adds the post-release archive of finished bugs, priorities, requirements and tasks, folds in the legacy requirements archive, and fixes the cleanup cron. The archive itself works and is safe. On a production-shaped copy of the live self-build project (435 bugs, 1165 task folders, 41 legacy files), the dry run and the real run moved exactly the expected 1556 items. Every pre-existing file survived byte-identical, live or archived; queue lines were conserved exactly; the next bug number was unchanged; a second run moved nothing; metrics totals were identical; and restore worked. An independent fixture in the acceptance shape matched to the item, and every guard I triggered held: unfinished, Active RC, live prerequisite, open bundle, unreadable status, count 0, invalid config, plan/apply race, restore over live, existing destination, busy lock. All the readers (show, why, restamp, prerequisite gate, GET /show, metrics, export/import), the REST route and the per-project cleanup cron were exercised and pass.

Two kinds of problem were found. **(1)** In the production topology the post-release archive never runs: CM releases inside its wake chain, which already holds the lock the archive needs, so Step 16c always skips (Finding 1, gap, medium). **(2)** Both gated runners exit 1 on 9 test failures from four test or artefact defects this RC introduced. All are low severity, with no effect on a running install (Findings 2-5).

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Gated suites (Steps 5a, 6.6, 6.7)
- `team/scripts/run-unit-tests.sh --coverage`: **UNIT_EXIT=1**. 10456 passed, 6 failed, 41 skipped (parallel lane); serial lane 5 passed. Lint pre-flight breakage class: OK.
- `team/scripts/run-integration-tests.sh --coverage`: **INTEGRATION_EXIT=1**. 954 passed, 3 failed, 1 skipped.
- Logs: `/tmp/pgai_kanban_tmp/tester/v2.32.0/{unit,integration}.log`.
- Every failure was bisected against a read-only `git archive ai_main` export (direct pytest used only as a debugging aid). Each passes on ai_main and fails on the RC, so all are this RC's. All 9 map to Findings 2-5; none is infrastructure. The suite-pass precondition for PASS is **not met**.
- Coverage (Step 5b): **coverage unavailable**. Both runners printed `coverage disabled by config ([testing] coverage)` (default `false`). The JSON under `/tmp/pgai_kanban_tmp/coverage/` is dated 2026-10-04, from an earlier run, and was not used. This is informational only, not a finding.

## Findings

### Finding 1: Post-release archive (Step 16c) never runs when CM ships from its wake

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated to the archive trigger; on-demand archive works)
**Regression:** no
**Integrity:** none
**Filed As:** BUG-0597 (Path C-A — autonomous follow-up filing, non-blocking, filed as BUG)
**Filed By:** TESTER (autonomous Path C) — the archive that should run after every release never moves anything when CM ships from its own wake, because that wake already holds the project lock the archive needs.

The requirement says Deliverable 6 runs the archive "at the end of a successful release finalize" (Intent: "after each release ships"). It also says Deliverable 5's run "takes the project's repo lock and does nothing if it cannot". Every wake chain holds `locks/repo-wake-<project>.lock` for its whole run (`team/scripts/lib/wake_driver.sh`), and CM's `cm-release.sh` runs inside it. So in an autonomous fleet Step 16c always gets `archive.repo_lock_busy` and moves nothing. Its "retried after the next release" is skipped the same way, and nothing else schedules the archive. The docs say so plainly (`docs/OPERATIONS.md` "Skipped when the repo lock is held"; `docs/cm-release-reference.md`). Reproduced in a sandbox: a parent bash took the lock via `pgai_lock_acquire`, as `wake_driver.sh` does, and its child ran `_cm_release_archive_finished_items` with the RC closed. The result was `_CM_ARCHIVE_OUTCOME=skipped-lock-busy` and no archive directory. The same helper without the lock gave `archived`. The integration tests run `release.sh` from a bare shell, so they never see this shape (a test-fidelity gap). The acceptance bullet "the finalize hook runs after a fixture release" is literally met; the deliverable's intent is not. Per the conflict policy this is rated the more conservative `gap`. Workaround: the operator runs `scripts/archive.sh --project <p>` while no wake is working the project. Hence medium, not high. Note for the operator: until this is fixed, the first archive on the self-build project (~380 bugs, ~1110 task folders per the production dry run) happens only when someone runs it.

### Finding 2: Committed docs/api/icd.json is stale for GET /show

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (a route docstring edited in a later ticket than the ICD bump can recur in any RC that touches routes across tickets)
**Regression:** yes
**Integrity:** breakage
**Filed As:** BUG-0598 (Path C-A)
**Filed By:** TESTER (autonomous Path C) — the committed API contract file was not regenerated after the show endpoint's description gained its archive paragraph, so three unit tests and the freshness lint fail.

Three unit failures:
- `test_review_cmds.py::TestIcdFreshnessGate::test_freshness_gate_exits_0`
- `test_lint_icd_freshness.py::TestIcdFreshnessPositive::test_fresh_artifact_passes`
- `api/tests/test_icd_generator.py::TestIcdWrapperCwdRegression::test_wrapper_output_matches_committed_artifact`

The cause is not a stale assertion. A fresh `generate-icd.sh` (to scratch) differs from the committed artifact in exactly one hunk: the `GET /show` description gained the archive paragraph in commit 99e60f6c (CODER-031), after commit 2b35c26a (CODER-026) had regenerated the ICD. The production docstring is correct; the artifact must be regenerated. Severity low: an API-contract document omits one paragraph, and route behaviour is unaffected.

### Finding 3: pp_run_ops sweep test fails on two new comment lines

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (two comments)
**Regression:** yes
**Integrity:** breakage
**Filed As:** BUG-0599 (Path C-A)
**Filed By:** TESTER (autonomous Path C) — two new code comments spell out the bare python module command the shared-helper sweep forbids, so that whole-tree test fails even though the code itself uses the helper.

`test_pp_run_ops_sweep.py::test_no_bare_python3_module_invocations_outside_exemptions` flags `team/scripts/lib/wake_task_id_gate.sh:46` and `team/scripts/lib/cm/release_archive.sh:28`. Both are header comments containing `python3 -m pgai_agent_kanban.archive`, added in commits c356da6d and cdaabc98. The executable lines correctly use `pp_run_ops`, which I confirmed by reading and by exercising both paths. Severity low.

### Finding 4: wake_task_id_gate.sh re-sources pp_run_ops.sh unconditionally

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (no production behaviour change today)
**Regression:** yes
**Integrity:** breakage
**Filed As:** BUG-0600 (Path C-A)
**Filed By:** TESTER (autonomous Path C) — the wake prerequisite gate now re-loads the shared python-runner helper every time it is loaded, which silently replaces any version a caller already set up, breaking two overwatch tests that rely on a stand-in.

Two failures: `test_overwatch_block_triage.py::test_transition_mutation_goes_through_transition_task_state` (`'BLOCKED' == 'DONE'`) and `::test_failed_executor_writes_leave_the_state_alone` (`CHECK_EXIT=0`, expected 1). Both pass on ai_main (39/39 for the file). The RC's new `source .../pp_run_ops.sh` in `wake_task_id_gate.sh` is reached through `check-block-triage.sh`, then `wake_task_state.sh`, then the gate, and it redefines the tests' stubbed `pp_run_ops`. I demonstrated it directly: define a stub, source the gate, and the real helper runs. The failure is real, not a stale assertion. The OVERWATCH failure-path tests can no longer exercise what they assert. Severity low, because production is unaffected while the redefinition is identical.

### Finding 5: Halt-after release integration fixture lacks pgai_agent_kanban/archive

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (hand-mirrored partial install roots in tests/harnesses break on any new sub-package import)
**Regression:** yes
**Integrity:** breakage
**Filed As:** BUG-0601 (Path C-A)
**Filed By:** TESTER (autonomous Path C) — a release integration test builds its throwaway kanban root from a hand-picked subset of the python package that lacks the new archive module, so its release crashes at the changelog step.

Three integration failures in `test_halt_after_rc_close_gate.py`: `..._matches_target`, `..._skips_past_target`, and `test_cm_release_leaves_versioned_sentinel_armed_when_target_not_yet_shipped`. Each `cm-release.sh` stops at Step 11b with `ModuleNotFoundError: No module named 'pgai_agent_kanban.archive'` and rolls back cleanly. The test's `_build_kanban_root` copies only `cm/`, `lib/`, `halt_after/`, `metrics/`, `ops/`, `task_status.py` and `env.py`. The RC's commit a910d430 (merged from CODER-032) made `cm/changelog_writer.py` import `pgai_agent_kanban.archive.layout`. I checked whether a live install is affected, because a crash at Step 11b would break the release pipeline. It is not. `install.sh` copies the whole package directory (`install_path` → `cp -r "$src/." "$dst/"`; the line-734 item loop includes `pgai_agent_kanban`). On this RC, `test_release_archive_step.py::TestArchiveThroughReleaseEntryPoints::test_release_archives_released_project_and_leaves_sibling_alone` ships a full release through `release.sh`, Step 11b included, and passes. Severity low (test-only).

### Pass findings, by requirement

- **D1 Config** — pass. `[archive] archive_count = 55` is in `kanban.cfg_example` and as a commented override in `project.cfg_example`. It is in the config_loader key registry (`archive:archive_count`) and documented. Precedence project.cfg > kanban.cfg > 55 was verified behaviourally. `0` disables archiving. An invalid value (`40x`) is refused dual-channel, naming the file, and nothing moves (exit 0, quality class).
- **D2 One module** — pass. The `pgai_agent_kanban/archive/` package has `plan`/`apply`. `scripts/archive.sh` is a flag parser reaching `run_archive` through `pp_run_ops`, with no inline Python (grep of all `.sh` diffs: none added). `POST /operations/archive` calls the same function in process; manifest row `impl: module:pgai_agent_kanban.archive.run.run_archive`. The REST dry-run stdout is byte-identical to the CLI's.
- **D3 Selection** — pass. Per-category newest-N; finished-state vocabularies; ordering by number, semver and task date/sequence. Every live-reference hold and fail-closed hold was triggered (log below).
- **D4 Layout and ledger** — pass. Same relative names under `archive/<project>/`. Queue lines are appended with markers unchanged. One six-field TSV row per item.
- **D5 Safe moves** — pass, with Finding 1 for the lock's interaction with the wake. A busy lock yields exit 3 with nothing moved. Same-filesystem moves are renames. A real cross-filesystem move goes copy, fsync, remove, with no staging litter. Queue rewrites go through `ops.write`. The run is idempotent. A forced failure stops the run without blocking the release (`test_failing_archive_is_logged_and_release_finishes_normally` passes in the integration runner).
- **D6 When it runs** — gap (Finding 1). On-demand CLI and REST pass. Cancelled RCs do not trigger it (`test_cancelled_rc_runs_no_archive` passes). It archives nothing while the RC branch exists or before release-state.md records the release (tests pass). The finalize hook runs after a fixture release (`test_release_archives_released_project_and_leaves_sibling_alone` passes).
- **D7 Readers** — pass. Prerequisite gate: archived counts as DONE, missing stays MISSING. `restamp-fixed-in.sh`, `show.sh`, `why.sh --details`, `GET /show`, and the changelog writer (its fallback is covered by `test_cm_lookup_archive_fallback.py`, which is green; I did not re-run it by hand). Metrics and token roll-ups are identical before and after. Export/import carry `ARCHIVE/` and the ledger. Live-only scanners (discovery, dashboard, PM filtering) do not import the archive (grep).
- **D8 Restore** — pass. Task restore with queue line and training log; bug restore via REST; `restore` ledger rows; refusals for not-archived, live-exists, and conflicting options.
- **D9 Legacy fold-in** — pass. `cleanup.sh` Step 3 is removed. Step 2 only reports and never deletes. 41 production legacy files migrated with ledger rows, leaving only an empty `priority/` directory.
- **D10 Cleanup cron** — pass. With no `--project` it runs once per registered project; a failing project does not stop the rest; an empty registry is a no-op. Crontab and pseudocron templates carry no project-less call that fails (`--trivial-only` / `--temp-only` / `--dry-run` iterate too). The live crontab line needs no change.
- **D11 Docs** — pass. `docs/OPERATIONS.md` §"Archive — finished work out of the live project" covers the config key, layout, ledger, restore, and what reads the archive. Registration table: ICD (`/operations/archive`, 1.17.0 plus baseline), generated operator docs (`docs/operator-commands.md` verb block), `docs/cm-release-reference.md` Step 16c, and census anchors. The ICD artifact is stale (Finding 2).
- **Acceptance "next claimed bug number unchanged"** — pass (BUG-0597 before and after on the production copy). I then filed this report's first bug under that same number in the live ledger, as expected.
- **6.1 release-state** — pass. `semver_gt v2.32.0 v2.31.6` holds, `pp_last_released_version` gives v2.31.6, `## Last Released` is well-formed, and `## Active RC` v2.32.0 matches the RC branch.
- **6.8 Synthetic release simulation** — the diff touches `cm/release.sh`, `cm/cancel-rc.sh` and `lib/cm/release_finalize.sh`. `test_cm_release_synthetic.py` ran inside the integration runner and is not among the failures; the only release-path failures are Finding 5's fixture.
- **6.9 Constraints** — pass. No secrets, no inline Python in the new bash, `set -euo pipefail` in `archive.sh`. TESTER notes honoured: every mutating command ran against a sandbox root.
- **6.10 Prerequisite graph** — pass. 16 RC tasks, no cycles, all IDs exist. CM-035 open precedes the work tasks; TESTER-036 lists all 12; CM-038 release lists 13.
- **6.13 Skip gate** — pass. No skip annotations added in this RC's tests.
- **6.14 Coding standards** — pass. `archive.sh --help` names every flag. The only ID- or version-shaped strings in added non-test code are sanctioned usage examples.

## Lint Findings (non-blocking)

- `icd_freshness` (unit and integration findings files; severity `quality`) — `docs/api/icd.json is stale` — the same defect as Finding 2; cites BUG-0598 (filed this run, Path C-A). No other lint finding in either file.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=30d freshness=stale — The last deep-verify run is red and its bug, BUG-0167-cold-build-claude-abliterated-failure (now done), was filed by that run; the record is stale on both counts (30 days old against a 7-day window, and v2.9.20 is not the RC v2.32.0), so nothing is known about whether this RC's images build.

## Autonomous criterion (Step 6.12)

1. Tasks force-promoted WAITING→BACKLOG by hand: **NO**. Every RC task's status shows wake-script promotion; no "by hand" or "manual" intervention appears in any RC task's status.md.
2. `release-state.md` edited outside the CM scripts: **NO**. Its mtime is 2026-10-07T14:27:33Z, the exact `## RC Opened At` written by CM-035's open-rc.
3. Scripts patched mid-build: **NO**. 0 files under the live install's `scripts/` changed since the RC opened. Commits touching `team/scripts/` on the RC are the RC's own CODER merges.
4. Queue files edited by hand: **NO**. Every RC task's queue marker matches its state (`[x]` DONE, `[W]` CM-038 WAITING, `[A]` this task); no invalid markers.
5. Non-CM remote git operation: **NO**. No `logs/git-boundary-guard.jsonl` in any of this cycle's task folders.

Autonomous operation criterion: Manual interventions required: none

## Caveats

- **The wake prompt overrides the working directory.** The task README's `## Working Directory` is `none`; the wake prompt named the detached worktree, which was used.
- **One real cross-filesystem move used `/dev/shm`.** It was a `mktemp`-named directory, removed immediately (0 left), because the host's only second filesystem is that tmpfs. All other scratch is under `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester-036/`.
- **One composite command was denied by the permission system.** It contained an `rm -rf` on a variable path. It was not retried; the same probe was rerun in a fresh `mktemp` directory with no deletion.
- **Sandboxes.** `restamp-fixed-in.sh` and `claim_next_bug_id` probes ran only against sandbox copies. The `.bug-claims.log` there was appended; the live ledger was untouched until the five real filings.
- **Observation, not a finding: two harness scripts copy only `cm/*.py`.** `team/scripts/harness/helloworld-flow-check.sh` and `engine-behavior-baseline.sh` seed throwaway roots that way, a pattern that predates this RC. They were not run (harness tier); noted in BUG-0601 for the fixer.
- **Observation: a restored item is eligible again.** The next archive run will re-archive it if it is still finished and older than the newest N. The spec does not ask restore to pin an item.
- **Observation: queue lines for live bugs render as `(no status.md)` in `why.sh --details`.** This is how they rendered before this RC; out of scope.
- **The production-copy sandbox read the project's `dev_tree_path` for Last Released** (read-only; it resolved to v2.31.6).

## Verification log (checkpointed during the run)

### Pre-flight (Steps 2-3)

- Worktree: /tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20261007-036-verify-2-32-0
- `git rev-parse HEAD` = `git rev-parse ai_rc/v2.32.0` = fa211742f1ecbe330e16147280b6e11dd30d06b0 — pass
- `git status --porcelain` empty — pass; `git stash list` empty — pass
- Step 2 scan: 661 DONE tasks name a feature branch; 0 of those branches exist locally (normal post-merge shape) — Check 1 and Check 2 pass trivially. RC carries 12 `--no-ff` merges, one per prerequisite task (023-034).
- Diff ai_main..ai_rc/v2.32.0: 89 files (30 added, 59 modified), +15179/-460.


### Behavioral verification

### Sandbox P — production-shaped copy of the live project
Sandbox root `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester-036/sandbox-prod-*` (a `cp -a` of `projects/pgai-agent-kanban` plus kanban.cfg, a one-project projects.cfg, its own `locks/`). Every command ran with `env -u PGAI_TASKS_DIR -u PGAI_PROJECT_ROOT KANBAN_ROOT=$S PGAI_AGENT_KANBAN_ROOT_PATH=$S PGAI_AGENT_KANBAN_TEMP_DIR=$S/tmp`, using the RC worktree's `team/scripts/archive.sh`.

- `archive.sh --project pgai-agent-kanban --dry-run` exit 0: Active RC v2.32.0, Last Released v2.31.6, count 55; would move bugs 380, priority 0, requirements 25, tasks 1110; 1488 queue lines; 365 training logs; 41 legacy files. Cross-checked independently: 435 bug files all `done` (435-55=380); 80 versioned requirements all older ones `done` (80-55=25); 1165 task folders, every one older than the newest 55 is DONE/WONT-DO (the only non-terminal tasks, TESTER-036 WORKING and CM-038 WAITING, are among the newest). Counts match.
- Real run exit 0. Ledger: 1556 rows = 380 bugs + 66 requirements (25 live + 41 legacy) + 1110 tasks.
- Integrity: every file present before the run exists byte-identical either live or under `archive/pgai-agent-kanban/` (legacy files under `archive/.../requirements/`); 0 missing, 0 altered; only new file is ARCHIVE-LEDGER.tsv. Legacy `tasks/archive/requirements/` left holding only an empty `priority/` directory.
- Queue lines: for all 9 queue files, live + archived lines equal the original multiset exactly (0 lost, 0 duplicated); no live line names an archived item; every archived line names an archived item.
- Next claimed bug number before and after: BUG-0597 both times.
- Second `archive.sh` run: exit 0, "nothing to archive", ledger unchanged (1556 rows).
- Training logs: 365 moved; 0 live logs name an archived task; 0 archived logs name a live task.
- Metrics/cost totals: `aggregate_tokens.py --all` and `metrics_aggregator.py --all` rebuilt before and after archiving: `metrics/` byte-identical; `usage/` differs only in the `shipped_at` generation timestamp (0 non-timestamp diff lines).
- `show.sh --key BUG-0166` (archived): exit 0, document emitted unchanged, stderr `archived: BUG-0166 is not in the live project; read from the archive: ...`; unknown key exit 3.
- `why.sh --details`: a live queue line naming an archived bug/task renders `done (archived)` / `DONE (archived)`.
- `restamp-fixed-in.sh --version v9.9.9 BUG-0490` (archived): `stamped: ... (archived)`, file in archive updated in place; unknown ID refused exit 2 `fixed_in_restamp.bug_not_found`.
- `check_prerequisites` (wake gate): archived prereq -> exit 0 (satisfied); never-existed prereq -> `:MISSING` exit 1; archived + live WORKING prereq -> only the WORKING one reported, exit 1.
- `--restore CM-20260926-018-open-rc-2-24-15`: folder, its queue line and its training log back live; `restore` ledger row appended.
- Refusals (each exit 1, dual-channel, ledger unchanged): restore of non-archived ID, restore of now-live ID, `--restore` + `--dry-run`, `--count -3`, `--count abc`, unknown project, missing `--project`, unknown flag, `--restore ../../etc`.

### Sandbox A — independent synthetic fixture in the acceptance shape (built by TESTER's own script, not the RC's `archive_sandbox.py`)
73 bugs (open #20/#40/#60), 80 tasks with 5 unfinished (WORKING/BACKLOG/BLOCKED) and 2 Active-RC tasks and 1 DONE prerequisite of a live BACKLOG task — all deliberately placed among the oldest 25 so the "newest 55 stay" rule cannot mask them; queue lines; training logs incl. a look-alike `<task-1-id>-extra.md`; legacy requirements + priority file; `archive_count = 55`.
- Moved exactly BUG-0001..0018; tasks 1,3,5,7,9,11,13,15,17-25 (17); held 8 with correct reasons (unfinished x5, in-active-rc x2, prerequisite-of-live-task x1); 35 queue lines and 17 training logs followed; the `-extra` log stayed live; ledger 18 bugs + 17 tasks + 1 requirements + 1 priority (legacy); legacy dir left with no files.
- Negative variant: an open bug (#3), a done bug listed in a `running` bundle (#7, canonical `- NAME (\`path\`)` line), and a bug with no `## Status` (#9) all among the oldest — all held (`unfinished`, `in-open-bundle`, `status-unreadable`). Closing the bundle releases #7.
- `archive_count = 0` (kanban.cfg), project.cfg override `0` over kanban.cfg `55`, and `--count 0`: nothing moves, no archive dir created, legacy not migrated. Invalid `archive_count = 40x`: dual-channel `archive.invalid_archive_count` naming the file, nothing moves, exit 0 (quality class).
- Race guard: plan, then reopen BUG-0002 and set task 1 WORKING, then `apply(plan)`: both dropped and stay live; re-applying the same plan object moves 0. Restore over an existing live copy refused `archive.restore_live_exists`, archive copy kept.
- Real cross-filesystem move (tmpfs `/dev/shm` -> `/tmp`, a throwaway dir removed immediately): file and directory moved by copy, source removed, no staging litter; existing destination refused `archive.destination_exists`, both copies untouched.
- REST `POST /operations/archive` (TestClient, loopback base URL): dry_run 200/exit 0 with stdout byte-identical to `archive.sh --dry-run`; conflicting options 500 `archive.conflicting_options`; empty body 422 `archive.project_required`; bad count 500 `archive.invalid_count`; apply 200; second apply "nothing to archive"; restore 200.
- Export/import: `export-project.sh` packs `demo/ARCHIVE/` (134 entries incl. ARCHIVE-LEDGER.tsv); `import-project.sh --name demo2 --register` places it at `archive/demo2/`, byte-identical (`diff -r` clean); `show.sh` finds an archived bug in the imported project.
- `cleanup.sh` with no `--project` over 3 registered projects (one missing release-state.md): runs each, `2 ok, 1 failed`, exit 1, failing project does not stop the rest; 0 task folders deleted (195 before/after, statuses aged 30 days); `--trivial-only` / `--temp-only` / `--dry-run` iterate likewise; empty registry is a logged no-op, exit 0.

### Repo lock in the production shape
A parent bash process took `locks/repo-wake-demo.lock` through `pgai_lock_acquire` (exactly as `wake_driver.sh` does for every wake chain) and, as its child, ran (a) `archive.sh --project demo` -> exit 3 `archive.repo_lock_busy`, nothing moved; (b) the release Step 16c helper `_cm_release_archive_finished_items` with the RC closed -> `[Step 16c] WARNING: archive skipped ...`, `_CM_ARCHIVE_OUTCOME=skipped-lock-busy`, helper returns 0 (release continues). (c) Same helper with no lock held -> `outcome=archived`. See Finding 1.
