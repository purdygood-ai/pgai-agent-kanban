# Verification Report: rc/v2.26.5 (ai_rc/v2.26.5)

## Summary

Verified RC v2.26.5 (`ai_rc/v2.26.5` at `6a005fca`) against `requirements/v2.26.5-bugfix-bundle-20260930.md`, which bundles 16 bugs (BUG-0454..0469) delivered by 7 tasks (CODER-014..018, WRITER-019, CODER-020). Every bundled item was checked against its own `## Acceptance`. For each guard or fix I reproduced the original failure on `ai_main` (or from the bug's own reproduction) and then the fixed behavior on the RC, all in sandboxes. Both gated runners exited 0 with zero lint findings. The code changes for all 16 items meet their acceptance. Two bundle acceptance bullets about back-filling historical ledger/changelog data (from BUG-0456/BUG-0465) cannot be met by what shipped. Both are filed, along with one pre-existing status-writer data-loss bug that a CODER ticket in this cycle surfaced for TESTER. None is severe, and none is a regression introduced by this RC.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Gated Suites (Step 5a / 6.6 / 6.7)

| Runner | Exit | Result |
|---|---|---|
| `team/scripts/run-unit-tests.sh --coverage` | `UNIT_EXIT=0` | 5811 passed, 43 skipped (19m02s) |
| `team/scripts/run-integration-tests.sh --coverage` | `INTEGRATION_EXIT=0` | 638 passed, 1 skipped (16m51s) |

Output: `/tmp/pgai_kanban_tmp/tester/v2.26.5-unit.log`, `/tmp/pgai_kanban_tmp/tester/v2.26.5-integration.log`. Direct pytest was not used.

**Coverage (informational, Step 5b; from `totals` in each runner's JSON, fields verified against the generated file):** unit line 78.05% / branch 63.77% (blended 75%); integration line 12.80% / branch 10.09%.

## Findings

### Pre-flight and structure

- **Step 2 (RC intact): pass.** 498 DONE task folders name a feature branch. No `ai_feature/*` branch remains locally, so Check 1 and Check 2 pass trivially. All 7 prerequisite merges are present in `git log ai_main..ai_rc/v2.26.5`.
- **Step 3 (worktree): pass.** `git rev-parse HEAD` == `git rev-parse ai_rc/v2.26.5` == `6a005fca`. `git status --porcelain` and `git stash list` were empty at start and at end.
- **6.1 release-state: pass.** Active RC `v2.26.5`, Last Released `v2.26.4` (well-formed). `semver_gt v2.26.5 $(pp_last_released_version pgai-agent-kanban)` is true. The file's mtime 06:27 matches CM-021's open-rc.
- **Requirements AC "All Python files compile cleanly": pass.** 588 tracked `.py` files, 0 parse failures (`ast.parse`, so no bytecode was written into the worktree).
- **Requirements AC "All bash files pass syntax check": pass.** 305 tracked `.sh` files, 0 `bash -n` failures. New scripts are mode 0755.

### Bundled items (requirements AC "each bundled item is addressed")

| Bug | Delivered by | Result | Evidence |
|---|---|---|---|
| BUG-0456 / BUG-0465 Step 16b cwd shadow (code) | CODER-014 | pass | From cwd `$HOME` with no PYTHONPATH, the `ai_main` `changelog_writer.py` raises the "install the whole package" ImportError. The RC module imports cleanly. RC helpers `_cm_release_snapshot_running_bugs` + `_cm_release_stamp_fixed_in` via `pp_run_ops` on a sandbox ledger stamped only the snapshotted bug: `1 stamped, 0 already set, 0 not done, 1 earlier done bug(s) left unstamped`. The closing summary uses the real outcome. The integration test `TestFixedInWriteback` runs a full release from a shadowing cwd and passed in the gated run. |
| BUG-0456 / BUG-0465 back-fill | CODER-015 | **gap** | Finding 1 and Finding 2 |
| BUG-0461 RC-mode KI placeholder | CODER-016 | pass | The bug's own probe now returns `(True, ...)` for None→KI1 and None→KI1+KI2. It returns `(False,'')` for: removed non-placeholder, placeholder removed with nothing added, `None` under another heading, `None` replaced by prose, and `None` replaced by an already-published id. Strict-mode and CLI RC-mode tests were added. |
| BUG-0466 `[A]` sweep accepts `..` | CODER-017 | pass | Sandbox queue `..`, `../x`, `a.b`, plus one canonical id through `reconcile_stale_active`: 3 `wake.queue.malformed-task-id` refusals, no teardown/state/marker call for them, `<TASKS_ROOT>/../status.md` untouched, and the canonical id reconciled (`reset 1`). The acceptance grep over `wake_reconcile_sweeps.sh`/`wake_driver.sh` is empty. |
| BUG-0467 API port single source | CODER-018 | pass | Sandbox `kanban.cfg [api] port=8393`, `PGAI_API_PORT` unset: server bound 8393. With the pidfile removed, `status` found the orphan and `stop` killed it (`kill -0` confirms). With `PGAI_API_PORT=8394`, the server bound 8394 (lsof) and status/stop used it. A foreign listener on the resolved port was refused with `api-server.stop.target_not_api_server`, exit 1, and left alive. Loopback only. |
| BUG-0454 invoke stage over 800 lines | CODER-020 | pass | `wc -l` = 798. `lint_engine_size_dupe.py`: ok, 42 files within ceiling. Both WATCHDOG markers present. |
| BUG-0455 notes cite requirements path | CODER-020 | pass | `lint_hygiene.py`: 0 findings. The v2.26.0 notes line no longer carries the path (already corrected on `ai_main`). The generator scrub now replaces `requirements/vX.Y.Z-*` paths (probed). |
| BUG-0457 stale path citations | CODER-020 / WRITER-019 | pass | TESTER.md:252 names `release_ship_policy_report.sh`. Both test docstrings name `wake_governance_composer.sh`. |
| BUG-0458 set_state grows blank lines | CODER-020 | pass | 3× `set_state` on the bug fixture: RC keeps 1 blank line, `ai_main` grows to 4. |
| BUG-0459 `- -` in Other Changes | CODER-020 | pass | `_bullet()` strips an existing marker. Files Involved is no longer used as notes text. Tests added. |
| BUG-0460 short task refs survive scrub | CODER-020 | pass | Scrub probe: `CODER-019`/`CODER-016` → "another item". `ISO-8601`, `UTF-8`, `SHA-256`, `v2.26.4`, `CM-1234-5` untouched. |
| BUG-0462 operator-api sole access-control | WRITER-019 | pass | `grep 'sole access-control'` is empty. §8 Authentication names the Host allow-list and JSON-only rule and links §3. |
| BUG-0463 OPERATIONS unwind-rc `--confirm` | WRITER-019 | pass | Usage line carries `[--confirm]`. Prose distinguishes it from `--force`. `generate_operator_verb_docs.py --check` OK. |
| BUG-0464 OVERWATCH Transient Requeue Count | WRITER-019 | pass | Field name gone. Paragraph names `## Transient Retry` and `transient_retry_cap` (default 3, matches `kanban.cfg_example:362`). |
| BUG-0468 operator-api pidfile/stop docs | WRITER-019 | pass | Pidfile at `<kanban root>/run/api/api-server.pid` matches `api-server.sh:268-271`. The stop/status rows describe the refusal and the reused-pid case. The PGAI_API_PORT row states precedence. |
| BUG-0469 OVERWATCH HALT-first root HALT | WRITER-019 | pass | Paragraph now says the lock is acquired and held and a project-scoped `projects/<name>/HALT` is created, matching `overwatch_protocol.sh:400,430`. `check_docs_citations.py` ok. |

### Finding 1: Fixed-In back-fill for v2.26.1-v2.26.3 bugs cannot land

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated to 22 ledger records; the forward path is fixed)
**Filed As:** BUG-0470 (Path C-A)
**Filed By:** TESTER (autonomous Path C) — twenty-two bugs fixed in v2.26.1-v2.26.3 are recorded as fixed in v2.26.4, and the new back-fill script cannot correct them because it never overwrites a set value.

BUG-0456's and BUG-0465's acceptance require the bugs closed by v2.26.1/2/3 to carry their `## Fixed In`. The v2.26.4 release (before this RC) ran the old, unscoped writeback successfully and stamped `v2.26.4` onto 29 done bugs. 22 of those belong to the v2.26.1-v2.26.3 bundles. The RC's `restamp-fixed-in.sh` refuses to overwrite a set value by design, so the three per-release back-fill commands CODER-015 recorded will skip every bug. CODER-015 recorded this conflict and asked TESTER to file it. Severity medium: incorrect metadata, with a hand-edit workaround under the bug template's cm-release-error clause. Regression: no (the data predates the RC).

### Finding 2: CHANGELOG `### Fixed` is empty for every bug-bundle release

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (recurs on every release, including v2.26.5)
**Filed As:** BUG-0471 (Path C-A)
**Filed By:** TESTER (autonomous Path C) — the changelog's Fixed list has been empty since v2.24.18 because the release-notes generator only puts bugs with category bugfix under Bug Fixes, and no bundled bug uses that category.

BUG-0465's last acceptance bullet says "the next CHANGELOG regeneration lists them under `### Fixed`". `changelog_writer` builds `### Fixed` from BUG ids under the release notes' `## Bug Fixes`. `release_notes.sh` `CATEGORY_SECTION` routes only `## Category: bugfix` there, and bundled bugs use subsystem categories (`release`, `api`, `wake`, `docs`, ...). Every version from v2.24.18 through v2.26.4 therefore reads `### Fixed: None`, and a `## Fixed In` back-fill cannot change that. Pre-existing (Regression: no). Severity medium: the published changelog is incomplete, with no runtime effect.

### Finding 3: set_state / set_block delete the next section when the target section is empty

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (state-file corruption class in the wake's own writer)
**Filed As:** BUG-0472 (Path C-A)
**Filed By:** TESTER (autonomous Path C) — the bash status writers silently delete the next section of a task's status.md when the section they rewrite is empty.

This is a spot-check (6.11) next to BUG-0458's fix, and CODER-020 raised it for TESTER. Sandbox reproduction on both the RC and `ai_main`: `set_block f Summary 'new summary'` on a file with an empty `## Summary` removed `## Blockers`, and `set_state f WORKING` on an empty `## State` removed `## Summary`. Pre-existing (Regression: no). It violates SOP-core's "preserves every section it was not asked to change". Rated medium, not high: the path is latent because status.md sections are normally populated, so nothing is failing today.

### Other methodology results

- **6.8 synthetic release: pass.** The diff touches `release_finalize.sh`. `team/tests/integration/test_cm_release_synthetic.py` passed in the gated integration run (18 tests, including the new shadowing-cwd release).
- **6.9 constraint negatives: pass.** New bash scripts keep `set -euo pipefail`. No hardcoded secrets in the diff. New refusals carry census anchors (`api-server-port-unresolved-01`, `fixed-in-restamp-01`, `restamp-fixed-in-project-required-01`), and the gated census lints are green.
- **6.10 prereq graph: pass.** CM-021 open-rc → CODER-014..018, WRITER-019 → CODER-020 → TESTER-022 → CM-024 release (WAITING on this task). No cycles; all ids exist.
- **6.13 skip-cites-real-bug: pass.** No placeholder skips. The six unresolved `BUG-NNNN` strings near "skip" are fixture/assertion literals in `test_fixed_in_restamp.py` and `test_skip_bug_citation_scan.py`, not skip annotations. The gated lint pre-flight passed.
- **6.14 coding standards: pass-with-caveat.** Bug/version tokens in new lines are sanctioned example values (`v1.4.0 BUG-0101`, labelled "Example"). Caveat: `restamp-fixed-in.sh --help` prints a stray bare `  --version` line above `--help` (from the generic flag renderer plus the extra help line). It is cosmetic, and every argument is still named, so it was not filed.
- **Test fidelity: pass.** The new Step 16b tests run from a real shadowing directory with `PYTHONPATH=""` rather than mocking the import. The API-port tests use real listeners.

## Lint Findings (non-blocking)

None. Both `findings-unit.json` and `findings-integration.json` report `"findings": []`.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=23d freshness=stale — The only ledger record is 23 days old (window 7d) and for v2.9.20, not v2.26.5, so nothing is known about whether this RC's images build; that red run's claude-abliterated/rocky9 failure was filed as BUG-0167, which is now done, and no deep-verify has run since.

## Autonomous criterion

1. Tasks force-promoted WAITING→BACKLOG by hand: **NO**. The prerequisite tasks' status Summaries show wake-script promotion; no hand-promotion record exists.
2. `release-state.md` edited outside CM scripts: **NO**. The content and 06:27 mtime match CM-021's open-rc output.
3. Scripts patched mid-build outside the pipeline: **NO**. Every non-merge commit on the RC belongs to one of the 7 task merges.
4. Queue files edited by hand: **NO**. No evidence in task logs or status audit trails.
5. Non-CM remote git operation: **NO**. No `git-boundary-guard.jsonl` in any of this cycle's task `logs/` directories.

Autonomous operation criterion: Manual interventions required: none

## Caveats

- `status.md` read `WORKING` at session start (dispatcher-stamped) with an empty `artifacts/`. No partial report existed, so this was run as a fresh verification.
- Three read-only `[B]` scans that BUG-0466 listed as optional (`verify-blocked-task-guard.sh:156`, `overwatch-checks/check-blocked-tasks.sh:158`, `check-transient-api-error.sh:726`) still use the loose id regex. They only read a status path; recorded as pass-with-caveat.
- Observation: `changelog_writer.py` carries its own ID scrub, which was not extended to short task refs or requirements paths (CODER-020 note). CHANGELOG.md contains neither today, so nothing is filed.
- Artifacts live in the task folder only. The wake prompt forbids a feature branch in this detached worktree, so the role file's "commit artifacts on a feature branch" step was not performed (prompt precedence), and nothing was written into the dev tree.
- Operator follow-up outside the RC (not a finding): once BUG-0470 ships a correction mode, run the three per-release back-fills CODER-015 recorded.
