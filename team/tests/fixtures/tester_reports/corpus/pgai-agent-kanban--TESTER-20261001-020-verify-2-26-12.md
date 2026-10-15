# Verification Report: rc/v2.26.12

## Summary

I verified RC `ai_rc/v2.26.12` (HEAD 87d83cc3) against `requirements/v2.26.12-bugfix-bundle-20261001.md`, which bundles BUG-0489 (version-divergence check ignores the branch prefix), BUG-0487 (verdict report and REST publish an mtime age as the lock age) and BUG-0488 (BUG-0481/0482 are missing `## Fixed In`). Three CODER tasks landed: 016, 017 and 018, each one work commit plus a `--no-ff` merge. Both gated runners exited 0. Every bundled code fix was exercised in a sandbox, covering both the original failure mode and the fixed case. All behaved as specified. One gap remains: BUG-0488's primary acceptance is a data action on the live bug files, the restamp of BUG-0481/0482. The RC shipped the code follow-up but did not perform that action, so both files still lack `## Fixed In`. This gap is filed via Path C (autonomous follow-up filing, non-blocking) as BUG-0490.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
low

## Findings

### Pre-flight and environment

#### Finding 1: RC integrity pre-flight (Step 2)

**Category:** pass

I scanned every DONE task under `projects/pgai-agent-kanban/tasks/` that names a feature branch. None of those branches still exists locally; all were deleted after merge, which passes both checks trivially. The cycle's three CODER merges are on the RC: d60d33d9 (016, work commit 7011200b), 96c93007 (017, work commit 2fc11b73), 87d83cc3 (018, work commit d199393c).

#### Finding 2: Worktree identity and cleanliness (Step 3)

**Category:** pass

`git rev-parse HEAD` = `git rev-parse ai_rc/v2.26.12` = 87d83cc3f37e7fde1295ea22a08f7ab27991b7dd. `git status --porcelain` is empty, and so is `git stash list`.

#### Finding 3: release-state sanity (Step 6.1)

**Category:** pass

`## Active RC` v2.26.12, `## Last Released` v2.26.11 (well-formed). `pp_last_released_version pgai-agent-kanban` returns v2.26.11, and `semver_gt v2.26.12 v2.26.11` is true. `## RC Opened By Task` CM-20261001-019-open-rc-2-26-12 matches the cycle's open-RC task.

### Gated suites (Steps 5a, 6.6, 6.7)

#### Finding 4: run-unit-tests.sh --coverage

**Category:** pass

`UNIT_EXIT=0`. Lint pre-flight OK, sentinel_leak OK, scratch_sweep OK. Output: `/tmp/pgai_kanban_tmp/tester/TESTER-20261001-020/unit.log`.

#### Finding 5: run-integration-tests.sh --coverage

**Category:** pass

`INTEGRATION_EXIT=0`. Lint pre-flight OK, sentinel_leak OK. Output: `/tmp/pgai_kanban_tmp/tester/TESTER-20261001-020/integration.log`.

Coverage is informational only (Step 5b), taken from the `totals` object in `/tmp/pgai_kanban_tmp/coverage/{unit,integration}.json`. Field names were checked against the generated files.
- unit: line 78.27% (15216/19441 statements), branch 64.13% (3600/5614).
- integration: line 12.75% (2478/19429 statements), branch 10.05% (564/5614).

### BUG-0489: version-divergence check ignores the branch prefix

#### Finding 6: Committed unprefixed VERSION plus prefixed tag reads as no divergence

**Category:** pass

I ran a sandbox per the bug's reproduction: a git repo with committed `VERSION` v1.23.0 and annotated tag `ai_v1.23.0`, `branch_prefix = ai_`, VERSION stamped through `version_stamp.sh stamp_version_files`, and the check run with KANBAN_ROOT, PGAI_AGENT_KANBAN_ROOT_PATH and TEMP_DIR pointed at the sandbox. The RC logged `version-match ... installed=v1.23.0 matches dev=ai_v1.23.0 (branch_prefix='ai_' ignored); no divergence`. The same probe on an `ai_main` export reproduces the original defect: `report:version-divergence installed=v1.23.0 differs from dev git-describe=ai_v1.23.0`.

#### Finding 7: Real version difference still reported; empty-prefix lane unchanged

**Category:** pass

On the RC:
- `v1.22.0` + `ai_v1.23.0` (ai_): divergence, and the report names both raw values.
- `v1.23.0` + `v1.23.0` (empty prefix): clean.
- `v1.22.0` + `v1.23.0` (empty prefix): divergence.
- `ai_v1.23.0` + `ai_v1.23.0` (ai_, the describe-fallback shape): clean.

ai_main gives the same results for all four, so there is no regression. An invalid `branch_prefix` narrates `could not be read; comparing without branch-prefix normalization` to stderr and stays report-only (exit 0).

#### Finding 8: Production-shape probe

**Category:** pass

I ran the RC check with the live install's `VERSION` (v2.26.10) and the live dev tree `$HOME/develop/pgai-agent-kanban`, with every write confined to a sandbox root. It reported `comparing installed=v2.26.10 with dev=v2.26.11 ... DIVERGENCE DETECTED`. That is the correct real signal: the live install lags the shipped v2.26.11. It is not a finding against the RC. Header comment and test additions (production-shape cases beside the existing prefixed cases in `test_describe_tag_filter.py`) are present as required.

### BUG-0487: verdict-report and REST lock age from mtime

#### Finding 9: verdict-report.sh --details publishes held_seconds from acquired_at

**Category:** pass

In a sandbox, I seeded a lock with `acquired_at` 3h ago and touched it so the mtime was fresh. The result was `held_seconds=10801` and `age_seconds=1`. A record without `acquired_at` gave `held_seconds=none`, and a future `acquired_at` (clock skew) gave `held_seconds=0`. `why.sh --details` on the same sandbox renders `held: 3h 0m`, `held: acquired_at not recorded (pre-upgrade lock)` and `held: 0s`, so the two surfaces agree.

#### Finding 10: GET /verdicts?details=true carries the same value

**Category:** pass

I used a FastAPI TestClient against the RC app with the same sandbox and a loopback base_url. The response was `repo-wake-demo.lock held_seconds=10801 age_seconds=1` and `repo-legacy-demo.lock held_seconds=null`. The first attempt with the default `testserver` host was refused dual-channel (`api.host_not_loopback`), which is correct guard behavior.

#### Finding 11: ICD contract handled as an additive minor bump

**Category:** pass

`age_seconds` is unchanged and is now documented as refresh age. `held_seconds` was added. Both ICD_VERSION files read 1.12.0, `SUPPORTED` lists 1.12.0, `docs/api/icd.json` is JSON-equal to the frozen `baselines/icd-v1.12.0.json`, no earlier baseline changed, and `ICD_CHANGELOG.md` and `docs/operator-api.md` are updated in the same commit (Documentation Discipline).

#### Finding 12: STUCK-LOCK wording decision recorded and correct

**Category:** pass

The `verdict.sh` header records the decision to keep the mtime heartbeat as the staleness clock. I triggered the guard with a stale lock (mtime 20h ago, `acquired_at` 30h ago). It renders `has not been refreshed for 20h 0m (held 30h 0m)`. Without `acquired_at` it renders `(acquired_at not recorded (pre-upgrade lock))`. Neither form calls the mtime age "held".

### BUG-0488: v2.26.10 bugs missing Fixed In

#### Finding 13: Step 16b narration names done bugs lacking Fixed In

**Category:** pass

I ran `fixed_in_writeback stamp` from the RC on a scratch copy of the live bugs/ directory. It printed `Done bug without ## Fixed In:` for BUG-0481 and BUG-0482 only (2 of 328 bug files), and the result line stayed last with exit 0. The bugs/ tree hash was unchanged, so the narration is read-only. After the restamp on the copy, the narration printed nothing. A done bug whose `## Fixed In` holds only a template comment is also named. The finalizer prefixes each line with `[Step 16b]` and still parses the final `Fixed-In writeback:` line. The `restamp-fixed-in.sh` that the narration cites exists in both the RC and the live install.

#### Finding 14: BUG-0481 and BUG-0482 still lack ## Fixed In on the live tree

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks; in practice one CM or operator command, since CODER cannot write live bug files)
**Systemic Risk:** low (isolated data gap on two bug files)
**Filed As:** BUG-0490-fixed-in-restamp-not-run (Path C-A, filed as BUG)
**Filed By:** TESTER (autonomous Path C) — the two v2.26.10 bug files still have no Fixed In version because the restamp that BUG-0488 asked for was never run, even though this release closes BUG-0488.

Criterion: BUG-0488 acceptance items 1 and 2 ("BUG-0481 and BUG-0482 each contain `## Fixed In` followed by `v2.26.10`"; the restamp re-run is idempotent), together with the bundle criterion "Each bundled item is addressed in the implementation". Expected: both files carry `## Fixed In` / `v2.26.10`. Actual: `grep -A2 '^## Fixed In'` on both live files prints nothing, and `## Status` is `done`. CODER-018 correctly did not write live data, which is outside its boundary. It shipped the optional narration instead and recorded the exact restamp command. On a scratch copy the command gives `2 stamped, 0 skipped`, then `0 stamped, 2 skipped`. This release's Step 13c will mark BUG-0488 `done` with its acceptance unmet, so BUG-0490 carries the data action forward. Severity is low: the effect is the CHANGELOG/Fixed In metadata of two already-fixed bugs, and nothing at runtime.

### Constraints, process, and spot-checks

#### Finding 15: Bundle acceptance (Python compiles, bash syntax)

**Category:** pass

All 597 tracked `.py` files parse (`ast.parse`) and all 305 tracked `.sh` files pass `bash -n`, with no failures. The changed shell files keep their prior `set -euo pipefail` state (present in verdict-report.sh and release_finalize.sh both before and after; absent by design in sourced libs both before and after). No secrets appear in the added lines.

#### Finding 16: Prereq dependency graph (Step 6.10)

**Category:** pass

CM-20261001-019 (open RC) is followed by CODER 016, 017 and 018, each depending only on CM-019. TESTER-020 depends on all three, and CM-20261001-022 (release) is `[W]` waiting. No cycles, every ID exists, and there are no sequence collisions.

#### Finding 17: Skip-cites-real-bug gate (Step 6.13)

**Category:** pass

The only skip markers added are two `skipif` guards on script-file existence in `test_describe_tag_filter.py`. Neither defers a fix and neither cites a bug ID. There are no `BUG-SKIP` placeholders.

#### Finding 18: Coding standards and provenance (Step 6.14)

**Category:** pass

The BUG/version-shaped strings in added lines are test fixture values (`BUG-0031-done-unstamped` and similar), usage examples in comments (`e.g. "v2.26.10"`), and the ICD support-table row `| 1.12.0 | supported | v2.26.12 |`. All are sanctioned shapes. No new script was added without `--help`. Both gated runners report 0 lint findings.

## Lint Findings (non-blocking)

None. `/tmp/pgai_kanban_tmp/lint/findings-unit.json` and `findings-integration.json` both have `"findings": []`.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=24d freshness=stale — the last cold build failed on the claude-abliterated FROM line and the rocky9 podman COPY --chmod step, already filed as BUG-0167-cold-build-claude-abliterated-failure (now done), and the record is stale on both counts (24d exceeds the 7-day window, and v2.9.20 is not the v2.26.12 under verification), so the images have not been built cold against this tree.

## Autonomous criterion

1. Tasks force-promoted from WAITING to BACKLOG by hand: NO. This task's status records promotion "by wake script: prerequisites satisfied", and no cycle task status mentions a manual promotion.
2. `release-state.md` edited outside the CM scripts: NO. Its fields match CM-20261001-019's open of v2.26.12.
3. Scripts patched mid-build: NO. The RC carries exactly three CODER work commits plus their merges (06:56 to 08:22 UTC), each the scoped deliverable of its ticket.
4. Queue files edited by hand: NO evidence. The markers for cycle tasks are consistent with task states.
5. Remote git operation by a non-CM agent: NO. No `git-boundary-guard.jsonl` exists in any cycle task's `logs/` (PM-003, CM-019, CODER-016/017/018, TESTER-020).

Autonomous operation criterion: Manual interventions required: none

## Caveats

- Observation (not a finding, predates this RC): `team/roles/CM.md` line 250 describes the Step 13c promotion summary (`Summary: N promoted, ...`) as "the `[Step 16b]` block" and does not mention the new `Done bug without ## Fixed In:` narration lines. The line was last changed in the v2.26.2 release commit. CODER-018 suggested a WRITER follow-up. Not filed.
- The wake prompt forbade creating a feature branch. Verification artifacts therefore live only in the task folder and are not committed to the RC, which departs from the role file's "Git workflow" section.
- The live install VERSION (v2.26.10) lags the last shipped release (v2.26.11). With BUG-0489 fixed, the version-divergence check will report this as a real divergence instead of noise. This is an operator deploy matter.
- Test fidelity: the new BUG-0489 tests stamp VERSION through the real `version_stamp.sh` (production identity path 1), and the BUG-0487 tests use real lock records and a real subprocess call to verdict-report.sh. I found no concrete mocked-away condition.

## Working log (checkpoints written during verification)
(Report in progress; written incrementally.)

### Pre-flight (Steps 2-3)

- Step 2: scanned every DONE task under projects/pgai-agent-kanban/tasks/ naming a feature branch. No such branch still exists locally (all deleted post-merge; passes trivially). The three cycle CODER merges are present on ai_rc/v2.26.12: d60d33d9 (016), 96c93007 (017), 87d83cc3 (018), each with one unique work commit. Result: pass.
- Step 3: worktree HEAD 87d83cc3 == ai_rc/v2.26.12; `git status --porcelain` empty; `git stash list` empty. Result: pass.

### Checkpoint notes (behavioral verification, in progress)

- BUG-0489 sandbox matrix on RC: v1.23.0+ai_v1.23.0/ai_ -> no divergence; v1.22.0+ai_v1.23.0/ai_ -> divergence; v1.23.0+v1.23.0/'' -> clean; ai_v1.23.0+ai_v1.23.0/ai_ -> clean; v1.22.0+v1.23.0/'' -> divergence. ai_main export reproduces the original false divergence for case 1. Pass.
- BUG-0487 sandbox: verdict-report held_seconds=10801/age_seconds=1 (3h-old acquired_at, fresh mtime); legacy -> none; REST /verdicts details -> held 10801 / null; why.sh shows "3h 0m" and "acquired_at not recorded". STUCK-LOCK wording "has not been refreshed for 20h 0m (held 30h 0m)". Pass.
- BUG-0488 narration on scratch copy of live bugs/: names BUG-0481, BUG-0482 only; read-only (tree hash unchanged); silent after restamp. Live BUG-0481/0482 still lack ## Fixed In -> gap.
- Bundle criteria: 597 .py parse, 305 .sh bash -n, 0 failures.
