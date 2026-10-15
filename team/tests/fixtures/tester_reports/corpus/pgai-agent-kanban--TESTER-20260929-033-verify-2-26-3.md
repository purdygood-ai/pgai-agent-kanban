# Verification Report: rc/v2.26.3

**Date:** 2026-09-29
**Tester:** TESTER (agent: tester, model: claude-fable-5-1), task TESTER-20260929-033-verify-2-26-3
**Scope:** `ai_rc/v2.26.3` at commit `6ea7e0c8711dfaf825291b9d42c31407979fa39a` (19 commits, 39 files over `ai_main` 3982103) against `requirements/v2.26.3-bugfix-bundle-20260929.md` — seven bundled bugs: BUG-0444, BUG-0445, BUG-0446, BUG-0447, BUG-0449, BUG-0425, BUG-0426.

## Summary

Verified the v2.26.3 bug bundle on a detached worktree at the RC commit. Pre-flight was clean (483 DONE tasks' feature branches all merged-and-deleted, HEAD equals the RC, no dirty files, no stashes). Every bundled bug's acceptance was exercised behaviorally in a sandboxed root, replaying each bug's own reproduction (the original failure mode) and the new normal case: the Host/JSON gates (35 assertions via TestClient plus the literal `curl -H 'Host: evil.example'` repro against a live uvicorn on loopback), the OVERWATCH transient check (six ledger/batch-log scenarios), the `unwind-rc` and `remove-project` confirm/force ladders (API argv capture and `unwind-rc.sh` over a closed stdin), the six stale files through the real `upgrade.sh`, the restored `KI-1.23.0.1` line, and the red-suite sentinel bracket. Both gated runners were run with `--coverage`; their exit codes are the PASS precondition (runner section below). Four gaps were found — one pre-existing gate defect CODER-028 flagged and three documentation staleness items — none affecting runtime behavior; each is filed via Path C (autonomous follow-up filing — non-blocking) as BUG-0461..BUG-0464.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
low

## Acceptance Criteria

Requirements-doc criteria plus each bundled bug's `## Acceptance` block.

| # | Criterion | Result | Evidence |
|---|-----------|--------|----------|
| R1 | Each bundled item is addressed in the implementation | PASS | Findings 1–7; every bug's acceptance exercised |
| R2 | All Python files compile cleanly | PASS | `python3 -m compileall -q team/` exit 0, no output |
| R3 | All bash files pass syntax check | PASS | `bash -n` over 303 `*.sh` files in the tree: 0 failures; `install.sh` ok |
| B0444 | Foreign-Host request returns 400; loopback requests unchanged; tests added | PASS | Finding 1 |
| B0445 | Synthetic 529 is detected and handled per the whitelist; test added | PASS | Finding 2 |
| B0446 | Default call unregisters without deleting files and respects the Active-RC guard; force requires confirm; tests cover both | PASS | Finding 3 |
| B0447 | Non-force call with correct confirm succeeds and still enforces the mismatch check; force without confirm is refused; tests added | PASS | Finding 4 |
| B0449 | Fresh upgrade over a v2.25.2 install leaves none of the six files; retired-files lint flags them if present; API test suite green | PASS | Finding 5 (suite: runner section) |
| B0425 | `KI-1.23.0.1` published once with the `06171cf^:1428` text; BUG-0417 re-issued `KI-1.23.0.2`; freshness lint exits 0 | PASS | Finding 6 |
| B0426 | Red gated run still prints the sentinel line and records a `sentinel_leak` finding; exit code unchanged; both runners exit 0 on a green tree | PASS | Finding 7 (green-tree half: runner section) |

## Constraint Verification

| Constraint | Status | Notes |
|------------|--------|-------|
| `tester_operation: verify-rc`, `target_version: v2.26.3` | MET | RC branch `ai_rc/v2.26.3`; `semver_gt v2.26.3 v2.26.2` true via `team/scripts/lib/semver.sh` |
| No origin operations by TESTER | MET | No `fetch`/`pull`/`push`; `logs/git-boundary-guard.jsonl` absent for this task |
| Scratch under `$PGAI_AGENT_KANBAN_TEMP_DIR` | MET | All probes under `/tmp/pgai_kanban_tmp/...`; every state-mutating probe on a `mktemp -d` root with `env -u PGAI_TASKS_DIR -u PGAI_PROJECT_ROOT` |
| Documentation Discipline Rule (SOP-core) | MET WITH GAPS | Script usage comments, manifest, ICD, census and `operator-api.md` updated; two operator docs stale (Findings 9, 10) |

## Findings

### Finding 1: BUG-0444 — loopback Host allow-list and JSON-only mutations

**Category:** pass

`team/pgai_agent_kanban/api/app.py` stacks `LoopbackHostMiddleware` (outermost, HTTP 400 `api.host_not_loopback`) and `JsonMutationMiddleware` (innermost, HTTP 415 `api.content_type_not_json`) around every route; `starlette>=0.27` declared in `requirements.txt`; census anchors `app-host-not-loopback-01` / `app-content-type-not-json-01` with Locator Index rows; `lint_gate_census_compliance.py` exit 0.

Behavioral (sandboxed TestClient, `api_probe.py`, 35/35): literal repro `POST /operations/wake-now` with `Host: evil.example` → 400, dual-channel body, `subprocess.run` never called; `Host` values `10.0.0.5:8300`, `127.0.0.1.evil.example`, `localhost.evil.example`, `127.0.0.1:8300:evil`, `0.0.0.0`, empty → 400; `127.0.0.1[:port]`, `localhost`, `LOCALHOST:5`, `[::1][:port]`, bare `::1`, `127.255.255.254:9` → 200; foreign-Host form POST refused by the Host gate first; loopback form POST / `text/plain` / `application/problem+json` / bodyless DELETE → 415 with a runnable curl remedy; `application/json; charset=utf-8` reaches the route (422 `halt.project_required` proves routing); loopback CORS preflight still answered. Production shape: a live uvicorn on `127.0.0.1:<ephemeral>` in a sandbox root — `curl -H 'Host: evil.example' -X POST .../operations/wake-now -d '{}'` → HTTP 400 (the bug's exact command, previously 200); form POST → 415; JSON POST → 422 from the route; `Host: localhost:<port>` → 200.

Test fidelity: `test_request_trust_gates.py` (23 tests) uses ASGI TestClient with per-request `Host` headers; the framework-wide autouse fixture `_loopback_test_client_host` only changes the *default* `base_url`, so the production allow-list is what every test runs under. No allowance for a test host exists in the app (grep confirmed).

### Finding 2: BUG-0445 — OVERWATCH transient check reads the dispatched batch log

**Category:** pass

`wd_invoke_task` (`wake_driver_invoke.sh`) appends `dispatched`/`returned` records with the batch-log path and byte span to `tasks/<id>/logs/dispatch.jsonl` via `dispatch_record_append`; `check-transient-api-error.sh::_ctae_log_tail` resolves the span via `dispatch_record_latest` and no longer reads `tasks/<id>/logs/*.log`. Sourcing chain verified: `wake_bootstrap.sh:136` sources `overwatch_protocol.sh`, `wake_common.sh:104` sources `wake_bootstrap.sh`, both `wake/claude.sh` and `wake/codex.sh` source `wake_common.sh` before `wake_driver.sh` (which sources `wake_driver_invoke.sh`) — so the ledger writer is in scope on both provider paths, not only in the unit-test harness that sources the lib itself.

Behavioral (`ow_probe.py`, sandbox root, real check + real `dispatch_record_*`, 6/6): (1) original failure mode — 529 only in the task's own `logs/progress.log`, no ledger → narrated "no dispatch record ... skipping", task stays BLOCKED, not counted handled; (2) 529 inside the recorded span → requeued to BACKLOG; (3) 529 in the same batch log but *after* the task's `returned` offset (another task's output) → "no transient signature in batch log span", not attributed; (4) open span (`dispatched` only) → runs to EOF, detected; (5) recorded batch log deleted → "no longer exists; skipping"; (6) torn JSON line before a good record → good record still used. `team/roles/OVERWATCH.md` row 9 names the log read.

### Finding 3: BUG-0446 — remove-project follows the script's safety ladder

**Category:** pass

Behavioral (`api_probe.py`, argv captured from the patched adapter): `{"project":"p1"}` → script spawned as `remove-project.sh --project p1` (safe mode, no `--force`); `force:true` without `confirm` → 422 `remove_project.confirm_required`, nothing spawned; wrong confirm → 422 `remove_project.confirm_mismatch`; `confirm:"remove-project p1"` → spawned with `--force`; `force` + `dry_run` → 200 preview, nothing spawned; 422 bodies carry `error/message/remedy/warnings` with a curl remedy. Retired anchor `operations-remove-project-force-01` removed from code and census; new `operations-remove-project-confirm-01` indexed. Tests: `TestRemoveProjectSafetyLadder` runs the real script against a sandbox root for the default and Active-RC-refused rungs.

### Finding 4: BUG-0447 — unwind-rc confirm separated from force

**Category:** pass

API (`api_probe.py`): no confirm → 422 `unwind_rc.confirm_required`; `force:true` alone (the old bypass) → 422, nothing spawned; trailing-space and case-folded confirm → 422 `confirm_mismatch`; exact confirm → argv `--project p1 --key v1.2.3 --confirm` (no `--force`, no `--dry-run`); confirm+force → both flags; `dry_run` without confirm → 200 preview naming `--confirm`, nothing spawned (the doc/behavior mismatch the bug named is resolved by correcting the docstring: `--dry-run` is never forwarded).

Script (sandbox root with a registered, halted project and a throwaway git dev tree; stdin `/dev/null`): (A) no `--confirm` → exit 1 after "There is NO undo..." and before "Step a: Backup" — the original EOF-at-prompt failure; (B) `--confirm` with `Active RC` v8.8.8 vs `--key v9.9.9` → exit 1 "ERROR: Active RC mismatch." — confirm bypasses no check; (C) `--confirm` with a matching Active RC → passes the prompt, prints `Flags: --confirm (confirmation prompt answered non-interactively)`, exit 0; (D) `--help` names `--confirm`; (E) `--force` alone still skips the prompt at script level (documented, for scripted callers; the API never sends `--force` without `--confirm`). `operator_verbs.manifest` flags column updated; `lint_api_parity.py` and `lint_operator_verb_manifest.py` exit 0. ICD bumped 1.10.0 → 1.11.0 (additive), `icd-v1.11.0.json` frozen, `SUPPORTED` extended, `lint_icd_compat.py` / `lint_icd_freshness.py` exit 0.

### Finding 5: BUG-0449 — six stale files retired on upgrade

**Category:** pass-with-caveat

`team/templates/retired-files.txt` lists the six paths under a dated block; `retired_files_lint.sh` gains `lint_retired_files_present` and an optional `<live-root>` argument. Behavioral: manifest lint exit 0; sandbox root seeded with the six → root scan exit 1 naming all six; clean root → 0; missing root → 2; three args → 2. Real `upgrade.sh` (via the test module's sandbox builder, `upgrade_probe.py`): scan 1 before, upgrade exit 0, six `[upgrade] Retired: <rel> -> retired/<ts>/<rel>` lines, summary "Retired: 6 file(s)", none left at root, all six under `retired/<ts>/` at their relative paths, scan 0 after. Read-only observation: the live install (`VERSION` v2.25.2) still carries all six — expected until it is upgraded to this release.

Caveat: the bug's `## Expected` also asked for manifest-diff-driven removal of *any* file the tree no longer ships; CODER-027 declined that with a stated reason (the deposit records no inventory to diff against; the allow-list keeps the protected-set guarantee). The bug's `## Acceptance` bullets are all met by the allow-list approach; the broader design remains unbuilt and is noted here, not filed.

### Finding 6: BUG-0425 — KI-1.23.0.1 restored to its first published meaning

**Category:** pass

`grep -c 'KI-1.23.0.1' CHANGELOG.md` → 1 (line 1503, v1.27.0 `### Fixed`), byte-identical (`cmp`) to `git show 06171cf^:CHANGELOG.md | sed -n 1428p`; the v1.23.0 `### Known Issues` line now renders BUG-0417 as `KI-1.23.0.2`; live `BUG-0417` `## Public ID` reads `KI-1.23.0.2`. `lint_changelog_freshness.py --bugs-dir <copy of bugs/>` in strict mode → exit 0 "fresh" (run against a scratch copy of the ledger because the writer persists Public IDs; the live ledger's mtime did not move). The runners' `changelog_freshness` pre-flight is in the runner section.

### Finding 7: BUG-0426 — post-suite checks bracket a red pytest run

**Category:** pass

Diff read in full: in both runners the three report-only checks (`bare_tmp_cleanliness`, `listener_cleanliness`, `sentinel_leak`) now sit between `PYTEST_EXIT=$?` and the verdict block; the verdict block (`exit 1` / `exit "$PYTEST_EXIT"`, census anchor `run-unit-tests-pytest-failure-01` retained) follows them; the scratch-tests sweep stays after the green verdict. Behavioral: `test_gated_runner_findings_on_failure.py::test_sentinel_removal_reported_on_pytest_failure` (both runners) copies the real runner, arms a HALT on a throwaway root, plants a failing test plus a HALT-removing test, and asserts exit 1, `[pgai-sentinel] REMOVED: <path>`, `sentinel_leak: quality-class finding(s)` on stderr, exactly one `sentinel_leak` finding in the findings file, and the HALT left removed — the bug's reproduction, run against a byte copy of the production runner text with the real lib; green-run cases assert "set unchanged by the suite" prints. Its result is part of the gated unit run below. Pre-existing and unchanged: the integration runner's pytest-failure exit still carries no census anchor (CODER-029 note; observation only).

### Finding 8: RC-mode changelog freshness gate rejects the first KI on a `None` placeholder section

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated to one predicate; a withdrawn patch with tests already exists in CODER-028's artifacts)
**Filed As:** BUG-0461 (Path C-A: filed as BUG — code defect / regression / test failure)

Pre-existing (tolerance predicate dates from commit 9e00a48, v1.23.0); raised by CODER-028 for TESTER to verify. Function-level probe on 6ea7e0c: `_diff_is_rc_tolerable(None-placeholder → KI-.1)` → `(False, '')`; `(KI-.1 → KI-.1 + KI-.2)` → `(True, 'post-CHANGELOG-commit KI additions tolerated')`. Rated `medium` per the rubric (incorrect, workaround exists: commit the regenerated artifact); practical effect inside the runners is a quality-class finding, argued in prose rather than by lowering the number.

### Finding 9: `docs/operator-api.md` sections 1 and 8 contradict the new trust model

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (one document)
**Filed As:** BUG-0462 (Path C-A)

Line 99 "Loopback binding is the sole access-control mechanism" and line 1982 "Anyone who can reach the loopback socket can call every endpoint" are now false given Section 3's Host allow-list and JSON-only rule (Finding 1). WRITER-030 recorded leaving them as out of scope. Documentation only.

### Finding 10: `docs/OPERATIONS.md` unwind-rc usage line lacks `--confirm`

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low
**Filed As:** BUG-0463 (Path C-A)

`docs/OPERATIONS.md:2076` shows `[--dry-run] [--force]` only; the `--force` paragraph (~2053) does not mention `--confirm`. The script header, `--help`, manifest, generated `operator-commands.md` block and `operator-api.md` were updated (Finding 4). Documentation Discipline Rule gap; documentation only.

### Finding 11: `team/roles/OVERWATCH.md:114` describes a retired field and ceiling

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low
**Filed As:** BUG-0464 (Path C-A)

Pre-existing; the RC edited row 9 of the same file (Finding 2) and left the Check 9 paragraph saying "increment `## Transient Requeue Count`" with a two-requeue ceiling, while the code writes `## Transient Retry` under `transient_retry_cap` (default 3). Documentation only.

### Finding 12: Step 6.14 coding-standards spot-check

**Category:** pass-with-caveat

No new script under `team/scripts/`; new lib functions carry usage comments; `retired_files_lint.sh` standalone entry prints usage and exits 2 on bad arity. Caveat: `api/app.py::_refusal_response` duplicates the four-key shape of `routers/operations.py::_refusal_response` (CODER-024 explains the signature mismatch: a middleware has no warnings list). The router helper is not in `lib/`, so this is borderline under the standard's "newly duplicated implementation of something already in `lib/`"; recorded, not filed. Also caveat: `HOW_TO.md`'s retirement paragraph does not mention the lint's new live-root scan (the script's own usage comment does).

### Finding 13: Steps 6.1, 6.2, 6.9, 6.10, 6.13

**Category:** pass

6.1: `release-state.md` holds the four fields; `Active RC` v2.26.3, `Last Released` v2.26.2 well-formed; `semver_gt v2.26.3 v2.26.2` true. 6.2: every file in the diff exists at the RC; new test modules present; runners executable; no expected path missing. 6.9: no hardcoded secrets in the diff; `set -euo pipefail` preserved in touched scripts; the API adapter still runs scripts with `stdin=DEVNULL` (asserted by the new tests). 6.10: prerequisite graph acyclic — CM-032 (open) → six CODER + WRITER-030 (030 after 024/026; 026 after 024) → CODER-031 → TESTER-033 → CM-035 (release, `[W]` waiting on this task); sequence numbers 024–035 non-colliding; every referenced ID exists. 6.13: no `BUG-SKIP`/letter-suffixed placeholders; the only `BUG-NNNN` strings near `skip` are string-literal fixtures inside `test_skip_bug_citation_scan.py` (the citation-scan's own test), not skip annotations.

### Finding 14: Step 6.8 synthetic release simulation

**Category:** pass

Skipped as a separate run with a note: the diff touches none of `cm-release.sh`, `cm-open-rc.sh`, `cm-cancel-rc.sh`, `project_paths.sh`; `unwind-rc.sh` is a recovery verb, not the open/close cycle. The two gated runners are in `team/scripts/` and CM invokes them at release time, so `team/tests/integration/test_cm_release_synthetic.py`'s result inside the integration run is cited in the runner section instead.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=22d freshness=stale — stale on both counts (22 days exceeds the 7-day window and v2.9.20 is not v2.26.3); that run's red verdict (claude-abliterated `FROM` parse error, podman/rocky9 `COPY --chmod`) was filed as BUG-0167, now `done`, and nothing is known about whether the images build at this RC.

## Autonomous criterion (Step 6.12)

1. Tasks force-promoted from WAITING to BACKLOG by hand: **NO** — every cycle task's status carries the wake script's "Promoted from WAITING to BACKLOG by wake script: prerequisites satisfied" line; queue markers consistent (`[x]` for the eight DONE tasks, `[A]` for this task, `[W]` for CM-035).
2. `release-state.md` edited outside the CM scripts: **NO** — mtime `2026-09-29 16:49:22.248` equals `## RC Opened At` written by `open-rc.sh` under CM-032; content is the four-field schema.
3. Scripts patched mid-build: **NO** — the commits touching `team/scripts/` (b5b48ea…f069b58, 17:01–22:16 UTC, all after the 16:49 RC open) are the tickets' own deliverables merged `--no-ff` from their feature branches, each named in its task's artifacts.
4. Queue files edited by hand: **NO** — no anomalous markers; cycle entries match task states.
5. Non-CM remote git operation: **NO** — no `logs/git-boundary-guard.jsonl` exists in any of the ten cycle task folders (PM-003, CM-032, 024–031); `push_to_remote = false`.

Autonomous operation criterion: Manual interventions required: none.

Process note (not a YES): CODER-028 ran `test_lint_changelog_freshness.py` once outside the sandbox recipe, and the changelog writer rewrote the live `bugs/BUG-0417` `## Public ID` from `KI-1.23.0.1` to `KI-1.23.0.2` (mtime 20:55:52 UTC). It was written by the production writer, is the exact end state BUG-0425 asked for, and CODER-028 disclosed it in full; it is an agent-caused live write, not a hand edit, and no further live file moved.

## Caveats

- Commit `6ce9b36` carries author name `Claude` where the other 18 carry `purdygood`; same e-mail, same session shape. Cosmetic git-identity variance, not an intervention.
- The live install at `$HOME/pgai_agent_kanban` reports `VERSION` v2.25.2 while `## Last Released` is v2.26.2 and CM-032 observed no `v2.26.*` tags in the dev tree; none of this is an RC input, recorded for the operator as CM-032 did.
- TESTER.md "Git workflow" asks for artifacts on a feature branch merged into the RC; the wake prompt forbids creating a branch and Step 9 forbids committing process artifacts into the dev tree. Followed the wake prompt and Step 9; nothing committed, artifacts in this task folder. Recorded in `status.md`.
- Sandboxes used by the probes remain under `/tmp/pgai_kanban_tmp/sandbox-*` (framework temp root; not deleted, no bare `/tmp` entries).
- Probe scripts and captured outputs: `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester-033/` (`api_probe.py/.out`, `ow_probe.py/.out`, `upgrade_probe.py`, `rcmode_probe.py`, `full.diff`, lint outputs).

## Gated runner results (Steps 5a, 6.6, 6.7) — appended after the background runs completed

Both runners were invoked from the worktree root with `PGAI_DEV_TREE_PATH` pinned to the worktree, sequentially in one background job, output tee'd to `/tmp/pgai_kanban_tmp/tester/unit-033.log` and `integration-033.log`.

| Runner | Command | Exit | pytest summary | Sentinel line |
|---|---|---|---|---|
| unit | `bash team/scripts/run-unit-tests.sh --coverage` | **UNIT_EXIT = 0** | `5681 passed, 43 skipped in 1268.20s (0:21:08)` — "Unit tests PASSED" | `[pgai-sentinel] live kanban root $HOME/pgai_agent_kanban: HALT / HALT-AFTER / DEFER-LOW-BUGS set unchanged by the suite.` |
| integration | `bash team/scripts/run-integration-tests.sh --coverage` | **INTEGRATION_EXIT = 0** | `636 passed, 1 skipped in 1042.78s (0:17:22)` — "Integration tests PASSED" | same line printed |

Pre-flight narration (unit): dockerfile_static OK (3 Dockerfiles, 0 violations), container_engine_free OK, api_parity ok (22 pairs, census-walk 0), test_anti_patterns OK (607 files), skip_cites_real_bug OK (0 missing files), orphan_tests ok (393 files), operator_verb_manifest ok (32 verbs, 47 routes, 0 drift), upgrade_no_temp_copy_guard ok, icd_freshness ok, icd_compat ok (12 baselines incl. 1.11.0), operator_verb_docs_freshness OK, dashboard_window_census OK, changelog_freshness ok, doc_command_truth 0 findings. The only non-OK pre-flight line on both runners is `engine_size_dupe: quality-class finding(s) (exit 3) — non-blocking, continuing.`

Modules this RC added or changed all ran green inside the gated unit run: `test_request_trust_gates.py`, `test_operations_action_twins.py`, `test_overwatch_transient_api_error_bash.py`, `test_wake_dispatch_ledger.py`, `test_upgrade_retirement_behavioral.py`, `test_gated_runner_findings_on_failure.py` (10 passed — includes the red-suite sentinel case for both runners, Finding 7). `tests/integration/test_cm_release_synthetic.py` passed (17 dots) inside the gated integration run (Finding 14).

**PASS precondition (Step 8):** `run-unit-tests.sh` exited 0 and `run-integration-tests.sh` exited 0 — satisfied. The recommendation stays `SHIP-WITH-CONCERNS` solely because Step-7 findings 8–11 (one bug, three gaps) were filed; no suite failure, no stale assertion, no Step 6.12 intervention.

## Lint Findings (non-blocking)

Read from `/tmp/pgai_kanban_tmp/lint/findings-unit.json` (generated 2026-09-29T23:29:46Z, 5 findings) and `findings-integration.json` (23:47:49Z, 5 findings — the same five). Two lint classes; every instance cited against an existing open/running bug, nothing filed. None of these change `## Recommendation` or `## Systemic Risk`.

- `engine_size_dupe` — `lib/wake_driver_invoke.sh` 809 lines (ceiling 800); 778 at `ai_main`, grew in merge baf6b83 (CODER-025's ledger block) — cites BUG-0454 (open; filed at v2.26.1 verification when the file was 827, same file, same lint; the 809 count is the current instance of the same overage)
- `hygiene` / class `provenance` — four instances, both suites:
  - `release-notes/v2.25.2.md:44` — `[task-ID] provenance reference 'tester-012'` — cites BUG-0430 (running)
  - `release-notes/v2.26.0.md:33` — `[unshipped-requirements-path] 'requirements/v2.26.0-agent-metrics-v2.md'` — cites BUG-0455 (open)
  - `release-notes/v2.26.2.md:40` — `[task-ID] 'CODER-019'` — cites BUG-0460 (open; CM-filed, names both `CODER-019` and `CODER-016` in that file's Bugs Skipped table)
  - `release-notes/v2.26.2.md:41` — `[task-ID] 'CODER-016'` — cites BUG-0460 (open)

## Coverage (Step 5b, informational only — never enters the verdict)

Field names verified against the generated JSON (`totals` carries `covered_lines`, `num_statements`, `covered_branches`, `num_branches`, `percent_covered`, `percent_statements_covered`, `percent_branches_covered`).

| Suite | Line % | Branch % | Blended (`percent_covered`) |
|---|---|---|---|
| unit (`/tmp/pgai_kanban_tmp/coverage/unit.json`) | 78.15 % (14909/19078 statements) | 63.98 % (3511/5488 branches) | 74.98 % |
| integration (`/tmp/pgai_kanban_tmp/coverage/integration.json`) | 12.97 % (2474/19069) | 10.26 % (563/5488) | 12.37 % |

## Final decision trail (Step 8)

- [x] `run-unit-tests.sh` exited 0 (lint pre-flight and pytest both passed).
- [x] `run-integration-tests.sh` exited 0.
- [ ] No `bug`, `gap`, or `stale-assertion` recorded in Step 7 — **not satisfied**: Finding 8 (`bug`, pre-existing), Findings 9–11 (`gap`, documentation).
- [x] No Step 6.12 intervention indicates a defect.
- [x] PASS evidence cited is the runners' exit codes, not a pytest line.

Tree branch (3): bugs and gaps found, none severe (no data corruption, no broken pipeline, no security defect — the security fix itself verified green, no autonomy failure) → **SHIP-WITH-CONCERNS**. Systemic Risk MAX across findings = `low`.
