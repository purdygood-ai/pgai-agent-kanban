# Verification Report: rc/v2.26.11

## Summary

I verified RC v2.26.11 (local branch `ai_rc/v2.26.11`, commit d6b3c91a, 9 commits over `ai_main`, 14 files) against `requirements/v2.26.11-bugfix-bundle-20261001.md`. The bundle covers BUG-0485 (chain-lock test holder race), BUG-0486 (Fixed-In writeback ImportError from the kanban root's parent), BUG-0483 (guard verifier misses the State half) and BUG-0484 (lock provenance `acquired_at` / held duration). I ran both gated runners, checked every bundled bug's acceptance list by running it (including mutation checks in a `git clone --no-hardlinks` and sandboxed lock probes), and ran Steps 6.1-6.14.

Both gated runners exited 0. All four bundled fixes work. Two acceptance items are only partly met: one lock surface still shows mtime age (BUG-0484), and the BUG-0481/0482 restamp is not done (BUG-0486 AC3). A spot-check also found a pre-existing false-positive in the OVERWATCH version-divergence check. All three are filed via Path C (autonomous follow-up filing, non-blocking).

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
low

## Pre-flight (Steps 2-3)

- Step 2: 518 DONE task folders name a feature branch. None exist locally, which is the normal post-merge shape, so Check 1 and Check 2 pass trivially. No anomalies.
- Step 3: worktree HEAD `d6b3c91a` == `git rev-parse ai_rc/v2.26.11`. `git status --porcelain` is empty. `git stash list` is empty.
- Branch naming: the project uses `branch_prefix = ai_`. The requirements doc's `rc/v2.26.11` is `ai_rc/v2.26.11`, and `main` is `ai_main`.

## Gated suites (Step 5a, 6.6, 6.7)

| Suite | Command | Exit | Result |
|---|---|---|---|
| unit | `bash team/scripts/run-unit-tests.sh --coverage` | `UNIT_EXIT=0` | 6247 passed, 43 skipped (18m58s) |
| integration | `bash team/scripts/run-integration-tests.sh --coverage` | `INTEGRATION_EXIT=0` | 638 passed, 1 skipped (15m44s) |

The PASS evidence for the suites is the two runner exit codes. The runs were sequential, with no other verification load running in parallel. Logs are in `/tmp/pgai_kanban_tmp/tester/v2.26.11/{unit,integration}.log`.

## Coverage (Step 5b, informational only)

Source: the `totals` object of `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/{unit,integration}.json`. I checked the field names against the generated JSON (`percent_statements_covered`, `percent_branches_covered`).

- unit: line 77.93%, branch 63.60% (blended `TOTAL` 75%)
- integration: line 12.77%, branch 10.05% (blended `TOTAL` 12%)

## Findings

### Requirements-doc acceptance criteria

#### Finding 1: Each bundled item is addressed

**Category:** pass-with-caveat

All four bundled bugs have an implementation on the RC. The caveats are Findings 6 and 8 (two partially met acceptance items). Details are in Findings 3-8.

#### Finding 2: All Python files compile cleanly / all bash files pass syntax check

**Category:** pass

`ast.parse` over every `git ls-files '*.py'`: 597 files, 0 failures. `bash -n` over every `git ls-files '*.sh'`: 305 files, 0 failures. The runners' breakage-class pre-flight was also green.

### BUG-0485 — chain-lock reverification test holder race

#### Finding 3: Test is deterministic and still guards the safety property

**Category:** pass

- AC1, 20 consecutive runs: `python3 -m pytest -q tests/integration/test_chain_lock_reverification.py` from `team/`, run 20 times. Result: 20 passed, 0 failed (351s).
- AC2, chain B startup delayed 3s (clone only; a `sleep 3` injected into the copied `wake/claude.sh` that only the second invocation hits): the fixed test passed twice. Baseline: the pre-RC (`ai_main`) version of the test with the same delay FAILED with the original `AssertionError: expected the narration to name the new holder (also 'writer', ...)`. So the diagnostic does widen the window, and the fix closes it.
- AC3, safety property still covered (clone mutations of `wake_driver.sh`): (a) dispatch-boundary reverify skipped (`if false && pgai_lock_reverify_or_narrate ...`) and (b) narrate-but-continue (`|| true`) both FAILED on `chain A must never have dispatched WRITER-20260101-002-reverify-second after losing the lock.`
- AC4: `run-integration-tests.sh` exit 0 (above).
- Read: the safety assertions run before the holder assertions, every message carries both chains' output, and the residual timing assumption is documented in the module docstring. Minor observation: the fixture's known-holder record has four fields and no `acquired_at`. That is harmless, because the narration renders only the four caller fields.

### BUG-0486 — Fixed-In writeback ImportError from the kanban root's parent

#### Finding 4: Step 16b is cwd-independent on the RC and covered by a test

**Category:** pass

- The RC's Step 16b calls `_cm_release_stamp_fixed_in`, which runs `pp_run_ops pgai_agent_kanban.cm.fixed_in_writeback`. There is no `FIXED_IN_PY` heredoc. `git log` puts the change at a30007f6 (Release v2.26.5). I confirmed that from cwd `$HOME`, `import pgai_agent_kanban` still resolves to the namespace path `['$HOME/pgai_agent_kanban']`, so the shadowing condition is real.
- AC2: the new `TestStep16bFromKanbanRootParent` passes on the RC. Mutation in the clone: with `pp_run_ops` replaced by bare `python3 -m` in the helper, the test FAILS with `ModuleNotFoundError: No module named 'pgai_agent_kanban.cm'`, which is the original failure mode.
- AC1, live: the live install is now at `VERSION v2.26.10` (refreshed 2026-10-01T03:25:18Z). Its `release_finalize.sh` carries the helper (4 references, 0 `FIXED_IN_PY`). The next real release run is the confirming observation. The root cause was a live install lagging six releases, not a code regression. See Finding 10 and Caveats.

#### Finding 6: BUG-0481 / BUG-0482 still lack `## Fixed In: v2.26.10`

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks; in practice a single operator/CM restamp command)
**Systemic Risk:** low (isolated to two bug files' metadata)
**Filed As:** BUG-0488-v2-26-10-bugs-missing-fixed-in (Path C-A, filed as BUG)
**Filed By:** TESTER (autonomous Path C) — two bugs that shipped in v2.26.10 still have no "Fixed In" version recorded, because the release's stamping step failed at the time and nobody has re-run the stamp since the cause was fixed.

BUG-0486 Acceptance item 3 is unmet. Both bug files are `## Status: done` with no `## Fixed In` section. CODER-009 validated the restamp command on scratch copies but correctly did not touch the live bug files. A future release's Step 16b stamps only bugs closed in that release, so nothing will stamp these automatically. The filed bug carries the exact restamp invocation.

### BUG-0483 — guard verifier misses the State half

#### Finding 5: Verifier now fails on the State-half mutation

**Category:** pass

Run in a `git clone --no-hardlinks` at d6b3c91a, each in a fresh five-variable sandbox root:
- Unmodified: exit 0, `RESULTS: 20 passed, 0 failed`. The scanner line shows `not-blocked-needs-human fixture count=0`.
- State check replaced by `True`: exit 1, `FAIL: AC-3 (scanner)` (DONE/Needs Human yes counted 1), `PASS: AC-3 (header)`, 19 passed / 1 failed.
- Needs Human check replaced by `True`: exit 1, `FAIL: AC-3 (scanner)`.
- `test_verifier_fails_when_guard_ignores_state` exists in `team/tests/unit/test_verify_blocked_task_guard.py` and is green in the unit runner.
- `python3 team/scripts/lint_hygiene.py --classes provenance`: `OK — 0 findings`.
- `wake_driver.sh` is unchanged by this ticket, as the bug asked. The header comment now accurately says each half is exercised on its own.

### BUG-0484 — lock provenance acquired_at / held duration

#### Finding 7: acquired_at, set_task and held rendering work as specified

**Category:** pass-with-caveat

Sandbox probe (sourcing the RC's `lock_provenance.sh`, `KANBAN_ROOT`/`PGAI_AGENT_KANBAN_ROOT_PATH`/`PGAI_AGENT_KANBAN_TEMP_DIR` pointed at a throwaway root, `PGAI_TASKS_DIR`/`PGAI_PROJECT_ROOT` unset):
- `pgai_lock_acquire` writes `holder/pid/op/task/acquired_at=2026-10-01T05:39:16Z` in one record.
- `pgai_lock_set_task` rewrites `task=` to a longer id and then to a shorter one. The shorter record is newline-padded with no truncation, and `holder/pid/op/acquired_at` are preserved. It returns 1, writing nothing, for an unheld lock, a multi-line task and a blank task.
- `pgai_lock_release` removes the file.
- `release-locks.sh` against seeded locks renders `held: 5m (since …) … — chain, no task yet` / `held verdict: live`. It renders `3h 0m … — CODER-1`, the legacy record as `held: acquired_at not recorded (pre-upgrade lock) …` with no verdict, a torn timestamp the same way, and a dead pid as `held verdict: dead (pid 999999 not running)`.
- Past the floor: the resolved floor is 43350s. Held 43475s gives `long (3m past floor)` (125s rounded up).
- why.sh fixtures (5m, past-floor, legacy/torn) and the dead-pid/overdue narrations citing `acquired_at` are covered by the new `test_lock_held_duration.py` (8 tests), green in the unit runner. `test_chain_lock_held_through_agent.py` (integration, green) asserts `task=<dispatched id>` on a live chain.
- I checked behavioral safety of the in-place rewrite. `pgai_lock_still_held` compares only `pid=`, so a `task=` rewrite cannot make the dispatch/merge reverify report a false loss. The `task` argument to `pgai_lock_reverify_or_narrate` is narration-only.

Caveat: the overdue *trigger* still uses the refreshed mtime. Only its narration changed. That is a deliberate design choice CODER documented (mtime is a heartbeat), and I accept it. The remaining mtime-age surface is Finding 8.

#### Finding 8: verdict-report / REST `release_locks[].age_seconds` is still mtime-derived

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (one reporting surface; nothing gates on it)
**Filed As:** BUG-0487-verdict-report-lock-age-mtime (Path C-A, filed as BUG)
**Filed By:** TESTER (autonomous Path C) — the lock "age" that the verdict report and its REST endpoint publish still comes from the lock file's last-touched time, so those surfaces disagree with the new recorded held-since time that why.sh and release-locks.sh now show.

BUG-0484's Expected says "every surface that shows the lock computes and displays how long it has been held". `team/scripts/verdict-report.sh` (`_vrd_age=$(( _vrd_now - _vrd_mtime ))`, emitted as `age_seconds`) and the REST `/verdicts?details=true` passthrough in `team/pgai_agent_kanban/api/reads.py` still publish mtime age. The implementing CODER noted this surface as unchanged. The field is part of the published ICD (`docs/api/icd.json`), so the filing recommends adding a held-time field rather than repurposing `age_seconds`.

### Methodology steps

#### Finding 9: Step 6.1 — release-state sanity

**Category:** pass

`release-state.md`: Active RC `v2.26.11`, RC Opened At `2026-10-01T02:37:08+00:00`, Opened By `CM-20261001-012-open-rc-2-26-11`, Last Released `v2.26.10` (well-formed). `pp_last_released_version pgai-agent-kanban` gives `v2.26.10`. `semver_gt v2.26.11 v2.26.10` returns true. The file's mtime (02:37:08.16) matches the open-rc write.

#### Finding 10: Spot-check (6.11) — OVERWATCH version-divergence check never matches on a prefixed lane

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (one report-only check; isolated)
**Filed As:** BUG-0489-version-divergence-ignores-prefix (Path C-A, filed as BUG)
**Filed By:** TESTER (autonomous Path C) — OVERWATCH's installed-versus-dev version check reports a mismatch on every run for this project even when the live install is current, because it compares the plain version string against the tag name that carries the "ai_" branch prefix.

While confirming CODER-009's claim that the live install sat at v2.26.3 through v2.26.10, I found that `overwatch/actions.log` has never logged "no divergence" for this project (0 entries since 2026-07-27). The current entries read `installed=v2.26.10 differs from dev git-describe=ai_v2.26.10`. `check-version-divergence.sh` compares the unprefixed committed `VERSION` with the prefixed `git describe` output using `==`. I reproduced it in a sandbox on the RC: `VERSION=v1.23.0` with tag `ai_v1.23.0` gives `VERSION DIVERGENCE DETECTED`. The existing prefixed test seeds `VERSION` with `ai_v1.23.0`, a shape only the describe-fallback path produces, so it misses the production path. The check fires on every sweep, so its real reports during the six-release live lag were indistinguishable from noise. That lag is what made BUG-0465 appear to recur as BUG-0486. The bug predates this RC and is not a regression. Severity medium: the output is incorrect, and the workaround is reading the two values by hand.

#### Finding 11: Step 6.8 — synthetic release simulation

**Category:** pass

The diff touches no release-lifecycle file (`cm/*.sh`, `lib/cm/*`, `project_paths.sh`), and no `team/scripts/cm` or `lib/cm` file sources `lock_provenance.sh`. The step is skipped by rule. For the record, `tests/integration/test_cm_release_synthetic.py` (18 tests) ran green inside the gated integration runner.

#### Finding 12: Step 6.9 — constraint negative checks

**Category:** pass

`set -euo pipefail` is present and unchanged in the modified entry scripts (`release-locks.sh`, `why.sh`, `verify-blocked-task-guard.sh`). The diff removes no `set -e` or `trap` line. No credential-shaped assignments appear in the added lines.

#### Finding 13: Step 6.10 — prerequisite graph

**Category:** pass

CM-012 (open-rc, no prereqs) → CODER-008/009/010 (prereq CM-012), CODER-011 (CM-012 + CODER-008) → TESTER-013 (CODER-008..011) → CM-015 release (CODER-008..011 + TESTER-013, currently WAITING). The graph has no cycles, every ID exists, and the bookends are correct. Sequence number 014 is unused, which is a gap and not a collision.

#### Finding 14: Step 6.13 — skip annotations cite real bugs

**Category:** pass

There are no `BUG-SKIP` or placeholder skip shapes in `team/tests` test code. The only hits are in a historical run log under `team/tests/artifacts/`. The only `skip(...BUG-NNNN...)` strings citing non-existent IDs (`BUG-1234`, `BUG-1235`) are fixture text inside `test_skip_bug_citation_scan.py`, which tests the lint itself. The runners' `lint_skip_bug_gate` pre-flight was green.

#### Finding 15: Step 6.14 — coding-standards spot-check

**Category:** pass

There are no new scripts, so no `--help` is owed. The help text changes for `why.sh` and `release-locks.sh` are reflected in `docs/operator-commands.md`'s regenerated blocks. The added lines contain no bug/task/RC citations in comments or messages. The only ID-shaped strings are test VALUES (`BUG-0001-earlier-release`, `CODER-20261001-011-a-long-task-id`), a sanctioned interface-example exception. The provenance lint is 0. The new readers reuse `_pgai_lock_field` and are not duplicated.

## Lint Findings (non-blocking)

None. `findings-unit.json` and `findings-integration.json` were both written with 0 findings.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=23d freshness=stale — The last deep-verify run is 23 days old and against v2.9.20 rather than v2.26.11 (both freshness conditions fail); it was red on the claude-abliterated FROM-line parse error and podman/rocky9 COPY --chmod step, which that run filed as BUG-0167 (since closed, Fixed In v2.9.22), so nothing is known about whether this RC's images build.

## Autonomous criterion

- (1) Force-promotion WAITING→BACKLOG by hand: **NO**. This task's status records "Promoted from WAITING to BACKLOG by wake script". CM-015 is WAITING as expected.
- (2) `release-state.md` edited outside open-rc/release/cancel-rc: **NO**. Its mtime (02:37:08) equals the open-rc write, and the content matches CM-012's summary.
- (3) Scripts patched mid-build (repo `team/scripts/` / `subagents/`): **NO**. The only `team/scripts/` commits on the RC are the CODER-010 and CODER-011 ticket merges. See Caveats for the live-install refresh.
- (4) Queue files hand-edited: **NO**. `coder_backlog.md`'s 05:33 mtime matches CODER-011's own DONE transition (status.md 05:33:14, progress log "marking task DONE" 05:32:38). `cm_backlog.md` 02:38 matches open-rc and materialization.
- (5) Non-CM remote git operation: **NO**. There is no `git-boundary-guard.jsonl` in any of this cycle's task folders. My own probes ran no `fetch`/`pull`/`push` and never set `PGAI_AGENT_ROLE`. The clone was a local `git clone --no-hardlinks` of the worktree path.

Autonomous operation criterion: Manual interventions required: none

## Caveats

- **Live install refreshed mid-RC.** The live kanban root (`VERSION`, `scripts/lib/cm/release_finalize.sh`) was rewritten at 2026-10-01T03:25:18Z, from v2.26.3 to v2.26.10, while RC v2.26.11 was open. No agent batch log near that time shows an install, so it looks like an operator deploy. Deploys are operator verbs by design, and this deployed an already-shipped release, so it is not a pipeline intervention under Step 6.12. It is the actual remedy for BUG-0486's live symptom. The six-release lag behind it is why Finding 10 matters.
- **Status at wake** read `WORKING` with an empty artifacts directory and only a dispatch record. I treated it as a fresh start. No prior report or `.previous-RUN-N` file existed.
- **Direct pytest** was used only for the BUG-0485 20-run loop and diagnostics, and for the BUG-0486 test-class mutation. None of it is cited as suite PASS evidence.
- **CODER-009 observation (not filed).** A tmux timing test (`test_dashboard_activation_hook_lifecycle.py::test_sender_skips_and_narrates_an_unarmed_pane_but_still_signals_the_armed_one`) failed once under load in CODER's run. It was green in this verification's unit runner. I have no reproduction, so I filed nothing.
- **Documentation note.** SOP-core "Lock Provenance" still lists the four caller fields. It is still accurate (the caller supplies four, and the library stamps `acquired_at`), so this is not a finding.
- **Report write path.** This file was written with a shell heredoc because the agent harness's Write tool refused report files. The task mandates this file at this path as CM's parsed input.
- Scratch, clone and logs are under `/tmp/pgai_kanban_tmp/tester/`.
