# Verification Report: rc/v2.28.2

## Summary

Verified RC `ai_rc/v2.28.2` (commit 380378c8, 7 commits over `ai_main` 375b053a, 19 files) against `requirements/v2.28.2-bugfix-bundle-20261004.md`, which bundles three bugs:
- BUG-0537: seven package-module CLIs crash when run by file path.
- BUG-0538: the GET /show-queues ICD description still says a no-project request "reads 0/0".
- BUG-0539: the dashboard docs describe a hidden-window slowdown that no longer exists.

All three are fixed and every acceptance criterion in each bug passes. Each fix was confirmed behaviourally, not only by grep: the by-path crash was reproduced on `ai_main` and shown fixed on the RC; the documented `show_queues.no_project` refusal was observed from the live route in a sandbox; the new cadence prose was checked claim by claim against `cadence.sh`. Both gated runners exited 0 (unit: 7800 passed, 43 skipped; integration: 646 passed, 1 skipped), with zero lint findings.

Two follow-ups were filed via Path C (autonomous follow-up filing — non-blocking), as Path C-A (filed as BUG — code defect / regression / test failure):
- BUG-0540 (low): the ICD bump left `docs/operator-api.md`'s support table without a 1.13.1 row.
- BUG-0541 (medium, pre-existing): a spot-check found the by-path fix again covered only a hand-picked list. Three more modules crash as their own usage text describes, two of them regressed since v2.27.3.

Neither defect affects the running system. The release is usable as shipped.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Findings

### Finding 1: BUG-0537 — seven package-module CLIs run by file path again

**Category:** pass

All seven modules (`deep_verify/ledger.py`, `lib/refusal_ledger.py`, `metrics/blocked_ledger.py`, `overwatch/ledger.py`, `overwatch/resolution_matrix.py`, `reports/agent_timing.py`, `dashboard/queue_details.py`) were run as `python3 <file> --help` from `/` with PYTHONPATH unset:
- RC: 7/7 exit 0 with no traceback.
- Original failure synthesized on a `git archive ai_main team` copy: 7/7 exit 1 with `ModuleNotFoundError`.

The guard binds the module's own tree even when a competing tree is on PYTHONPATH (`runpy.run_path` probe). A package-member import leaves `sys.path` unchanged. Real subcommands work by path. `run-unit-tests.sh --files tests/unit/test_package_modules_run_by_path.py` exits 0 (30 passed). No dev-tree `sys.path` entry was introduced.

Test fidelity: the extended test runs a real subprocess with a scrubbed env and a foreign cwd. That is the real breaking condition, not a mocked proxy.

### Finding 2: `dashboard/queue_details.py --help` prints a scan, not help

**Category:** pass-with-caveat

`queue_details.py` has no argparse, so `--help` is read as a tasks-root path. It prints an empty JSON scan and exits 0. BUG-0537's criterion (exit 0, no traceback) is met, and this is the same behaviour as the last good release, as the implementing CODER also noted. Not filed.

### Finding 3: BUG-0538 — ICD description of GET /show-queues corrected (1.13.0 -> 1.13.1)

**Category:** pass

- `grep "else read 0/0"` over reads.py and icd.json: empty.
- The icd.json description names `show_queues.no_project`.
- `lint_icd_freshness.py`: ok. `lint_icd_compat.py`: ok (15 baselines). `generate_operator_verb_docs.py --check`: OK.
- `icd.json` is byte-identical to `baselines/icd-v1.13.1.json`. Both `ICD_VERSION` copies read `1.13.1`.
- Apart from `info.version` and that one description, the ICD surface is identical to ai_main's, which is correct for a patch bump.

Behaviour: a sandboxed `TestClient(create_app(...))` with PGAI_PROJECT_NAME unset returns 500, exit_code 1, empty stdout, and `error:   show_queues.no_project` in stderr, exactly as now documented. With a server project it returns 200.

### Finding 4: ICD support table in docs/operator-api.md lacks the 1.13.1 row

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks; one WRITER doc edit plus an optional mirror check)
**Systemic Risk:** low (isolated documentation omission)
**Regression:** yes
**Integrity:** none
**Filed As:** BUG-0540-icd-support-table-missing-1-13-1 (Path C-A)
**Filed By:** TESTER (autonomous Path C) — the API guide's table of supported contract versions stops at 1.13.0, although this release publishes and supports contract 1.13.1.

BUG-0538 expected the patch bump "that docs/operator-api.md prescribes". Step 4 of that procedure is "Add the version's row to the support table", and the table is documented as mirroring `docs/api/baselines/SUPPORTED`. `SUPPORTED` gained `1.13.1`; the table did not. `diff <(grep -oE '^\| [0-9]+\.[0-9]+\.[0-9]+ \|' docs/operator-api.md | tr -d '| ') <(grep -vE '^\s*(#|$)' docs/api/baselines/SUPPORTED)` prints `> 1.13.1`. No lint detects the drift. The implementing CODER flagged it as a WRITER follow-up, and no ticket picked it up.

There is also an unresolved conflict between `ICD_CHANGELOG.md` ("description-only edits ... are not recorded") and procedure step 4 (a changelog entry per bump). Severity low: the contract, baselines and gates are all correct.

### Finding 5: BUG-0539 — dashboard refresh-cadence docs match shipped behaviour

**Category:** pass

- `grep -nE 'slow(s|ed)? to 10x|times slower'` over both docs: empty. No broader "10x"/"ten times" residue.
- Both docs state that a detached session is hidden and renders nothing (dashboard.md:113, :196; OPERATIONS.md:1283, :1489).
- `check_docs_citations.py`: ok. `lint_doc_command_truth.py`: 0 findings. Provenance lint: 0 findings.

Every new claim matches the code:
- Visibility is `#{session_attached} #{window_active}` (`dashboard_pane_visible`).
- A hidden pane gets no render after its arrival render (`dashboard_cadence_run_loop`).
- Stream panes are paused with SIGSTOP/SIGCONT (`dashboard_cadence_run_stream`).
- The activation hooks are `session-window-changed` and `client-attached` (`lib/activation_hook.sh:64`).
- The status line uses `status-interval ${REFRESH_INTERVAL}` (`create.sh:920`).

### Finding 6: Requirements-level criteria — compile and syntax

**Category:** pass

All 661 tracked `.py` files compile (in-memory `compile()`, no .pyc written). All 316 tracked `.sh` files plus 1 extensionless bash script pass `bash -n`.

### Finding 7: Gated runners

**Category:** pass

- `run-unit-tests.sh --coverage`: UNIT_EXIT=0. 7800 passed, 43 skipped (26:52). Lint pre-flight: 0 findings.
- `run-integration-tests.sh --coverage`: INTEGRATION_EXIT=0. 646 passed, 1 skipped (15:28). Lint pre-flight: 0 findings.

The output is at `/tmp/pgai_kanban_tmp/tester/TESTER-20261004-014/{unit,integration}.out`. PASS evidence is these exit codes, not pytest lines.

Coverage (informational only; from the `totals` object, with field names checked against the generated JSON):

| Suite | Line (statements) | Branch | Blended |
|---|---|---|---|
| Unit | 80.34% | 66.57% | 77.29% |
| Integration | 14.13% | 9.94% | 13.20% |

### Finding 8: By-path CLI fix again applied to a hand-picked subset; three more modules crash as their usage describes

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (the same class has now been fixed partially in two consecutive RCs and will recur until the test discovers modules itself)
**Regression:** no
**Integrity:** none
**Filed As:** BUG-0541-more-package-clis-crash-by-path (Path C-A)
**Filed By:** TESTER (autonomous Path C) — the run-by-file-path fix has now been applied to a hand-picked list of modules twice, and three more package modules still crash with "module not found" when run the way their own usage text describes.

This came from the Step 6.11 spot-check (untested mechanism / recurrence of a prior gap). I swept every package module with `__main__` and a top-level `pgai_agent_kanban` import. 15 still crash by path with PYTHONPATH unset. Three of them are BUG-0537's defect:
- `deep_verify/schedule.py` and `deep_verify/freshness.py` printed their usage by path on v2.27.3 (exit 2). They regressed in v2.28.0 when a private ISO parser became a package import.
- `reports/project_summary.py`'s docstring advertises `python3 team/pgai_agent_kanban/reports/project_summary.py [options]`. It crashes, and did on v2.27.3 too.

`test_package_modules_run_by_path.py` pins a literal list of 10 modules, although its docstring now claims coverage of every module with its own CLI.

Not introduced by this RC (`Regression: no`), and not exercised in production: deep-verify scripts use `pp_run_ops`, and `project-summary.sh` sets PYTHONPATH. The `-m` form is the workaround, so per the "Rating a Finding" calibration it is rated medium. BUG-0537's acceptance named the seven modules explicitly, and those pass. This finding is outside that list, not a failure of it.

### Finding 9: Steps 6.1, 6.9, 6.10, 6.13, 6.14

**Category:** pass

- 6.1: release-state.md is well-formed (Active RC v2.28.2, Last Released v2.28.1). `semver_gt v2.28.2 $(pp_last_released_version pgai-agent-kanban)` is true.
- 6.9: no secrets, home paths or dev-tree paths in added lines.
- 6.10: the prerequisite graph is acyclic, with correct bookends (CM-013 open, CM-016 release). Sequence 015 is unused, with no collision.
- 6.13: no real skip cites a missing bug. The unresolved IDs are synthetic fixture strings.
- 6.14: no provenance IDs in new comments or docstrings. The guard's 10-fold repetition is inherent: it must run before any package import, so it cannot move to `lib/`.
- 6.8: skipped. The diff touches no release-lifecycle file.

## Lint Findings (non-blocking)

None. `findings-unit.json` and `findings-integration.json` were both written with 0 findings.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=27d freshness=stale — the last lane run is 27 days old (outside the 7-day window) and was for v2.9.20 rather than v2.28.2, so nothing is known about whether this RC's images build; that red run filed its own bug, BUG-0167-cold-build-claude-abliterated-failure (now done).

## Autonomous criterion

1. Tasks force-promoted WAITING to BACKLOG by hand? **NO.** Promotions were recorded by the wake script ("Promoted from WAITING to BACKLOG by wake script: prerequisites satisfied"), and the queue markers match task states.
2. release-state.md edited outside the CM scripts? **NO.** The file's mtime (10:22:16) equals `## RC Opened At`, written by CM-20261004-013. Nothing later.
3. Scripts patched mid-build? **NO.** No RC commit touches `team/scripts/` or `subagents/`, and ai_main has not moved since the RC opened.
4. Queue files edited by hand? **NO.** No evidence. Every v2.28.2 marker matches its task's `## State`.
5. Remote git operation by a non-CM agent? **NO.** No `git-boundary-guard.jsonl` exists in any task folder of this cycle.

Autonomous operation criterion: Manual interventions required: none

## Caveats

- Observation (not filed; the RC did not touch it): `docs/OPERATIONS.md:1287` says "Every window carries the same two-line tmux status bar", but `team/scripts/dashboard/create.sh:918` sets `status 3`, and its own comments describe three lines. WRITER-20261004-012 flagged this too. It predates the RC and fits none of the Step 6.11 categories, so it is recorded here for a future documentation pass.
- Observation (not filed; outside this RC's diff): `team/scripts/project-summary.sh` resolves its module through `resolve_dashboard_helper` with a `$PGAI_DEV_TREE_PATH` fallback. SOP-core ("Git Repositories: Roles and Boundaries") prohibits dev-tree fallbacks for live code. The live-install path is probed first, so it only matters when the live copy is missing. It is worth an OVERWATCH or TESTER look in a cycle that touches that script.
- The CODER-011 `## Stale Literal Risks` entries (`"minor"`, `"dry_run"`, `"state"`, `"/approvals"`, `"project"`) are generic substrings. None failed in either suite.
- The task README's `## Working Directory` reads `none`. Per the wake prompt, verification ran in the detached worktree `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20261004-014-verify-2-28-2` at 380378c8. Per the wake prompt, no feature branch was created, so artifacts live only in the task folder. That worktree stayed clean (`git status --porcelain` empty) after every check and both runners.
- The container tier was not run. TESTER never runs it, per Step 5d. The tree ships Dockerfiles and `team/scripts/deep-verify.sh` exists. See Deep-Verify Freshness.

## Pre-flight (Steps 2-3)

- Step 3: `git rev-parse HEAD` = `git rev-parse ai_rc/v2.28.2` = 380378c88e5e689104d9c5e5aa013556533ce1f2. `git status --porcelain` is empty, as is `git stash list`. PASS.
- Step 2: 566 DONE task folders name a feature branch. None of those branches exists locally (deleted after merge, the normal shape), so Checks 1 and 2 pass trivially. 0 anomalies. PASS.

## Checklist (Step 4)

Deliverables / acceptance criteria / constraints extracted from the requirements doc and the three bundled bugs:

- R-AC1 each bundled item addressed; R-AC2 all Python files compile; R-AC3 all bash files pass syntax check.
- BUG-0537: seven modules run by path with `--help` from `/`, PYTHONPATH unset, exit 0, no traceback; `run-unit-tests.sh --files tests/unit/test_package_modules_run_by_path.py` exits 0; constraint: no `sys.path` entry pointing at a dev tree.
- BUG-0538: `else read 0/0` gone from reads.py and icd.json; icd.json /show-queues description names `show_queues.no_project`; `lint_icd_freshness.py`, `lint_icd_compat.py`, `generate_operator_verb_docs.py --check` exit 0; Expected: ICD_VERSION patch bump per the `docs/operator-api.md` procedure, frozen 1.13.1 baseline.
- BUG-0539: `grep -nE 'slow(s|ed)? to 10x|times slower'` empty in both docs; both docs state a detached session is hidden and renders nothing; `check_docs_citations.py`, `lint_doc_command_truth.py`, `lint_hygiene.py --classes provenance` clean.

## Progress notes (checkpoint)

- R-AC2: 661 tracked `.py` files compiled in memory (no .pyc written): 0 failures. PASS.
- R-AC3: 316 tracked `.sh` plus 1 extensionless bash script pass `bash -n`: 0 failures. PASS.
- BUG-0537 behavioural (Step 6.5, both cases): original failure mode reproduced on `git archive ai_main team`: all 7 modules exit 1 with `Traceback`. On the RC: all 7 exit 0, no traceback. With a competing tree (`ai_main` archive) on PYTHONPATH, `runpy.run_path` of the RC's `deep_verify/ledger.py` and `reports/agent_timing.py` resolved `pgai_agent_kanban` from the module's own tree. A package-member import of all 7 left `sys.path` byte-identical. Real by-path subcommands (`deep_verify/ledger.py latest <missing>` gives `{"found": false}` rc 0; `agent_timing.py --days 1` rc 0) work. PASS.
- BUG-0538 ACs: grep empty (rc 1); description contains `show_queues.no_project` (2 occurrences); `lint_icd_freshness` ok; `lint_icd_compat` ok (15 baselines incl. 1.13.1); `generate_operator_verb_docs --check` OK (32 verbs). icd.json == baselines/icd-v1.13.1.json byte-identical; docs/ and package-data ICD_VERSION both `1.13.1`; apart from version and that one description, the icd.json surface is identical to ai_main's. Behavioural: sandboxed `TestClient(create_app(...))` with PGAI_PROJECT_NAME unset gives `GET /show-queues` -> 500, exit_code 1, stdout `''`, stderr `error:   show_queues.no_project`; with PGAI_PROJECT_NAME=alpha -> 200, exit_code 0. The published description now matches runtime. PASS.
- BUG-0539 ACs: grep empty (rc 1); both docs state detached = hidden (dashboard.md:113, :196; OPERATIONS.md:1283, :1489); check_docs_citations ok; lint_doc_command_truth 0 findings; provenance lint 0 findings. Prose claims cross-checked against code: `dashboard_pane_visible` (`#{session_attached} #{window_active}`), the hidden loop renders nothing (`dashboard_cadence_run_loop`), stream SIGSTOP/SIGCONT (`dashboard_cadence_run_stream`), activation hooks `session-window-changed client-attached` (`lib/activation_hook.sh:64`), status line `status-interval ${REFRESH_INTERVAL}` (create.sh:920). PASS.
- GAP: `docs/operator-api.md` §9 support table has no `1.13.1` row although `docs/api/baselines/SUPPORTED` now lists it (details in Findings).
- Step 6.1: release-state.md Active RC v2.28.2, Last Released v2.28.1 (well-formed). `pp_last_released_version` = v2.28.1; `semver_gt v2.28.2 v2.28.1` true. PASS.
- Step 5d: tree ships `docker/debian/Dockerfile` and `docker/rocky9/Dockerfile`, and `team/scripts/deep-verify.sh` exists. Ledger reader returns found=true, verdict failed-to-build, v2.9.20, from 2026-09-07.
- Step 6.9: added lines (excluding the frozen baseline JSON) contain no `$HOME/`, dev-tree path, `PGAI_DEV_TREE_PATH`, or credential-shaped strings. The new guard resolves `Path(__file__).resolve().parents[2]` (own tree), confirmed behaviourally above. PASS.
- Step 6.10: CM-20261004-013 (open, DONE) -> CODER-010, CODER-011, WRITER-012 (each prereq CM-013, all DONE) -> TESTER-014 (prereqs the three) -> CM-20261004-016 (release, WAITING, prereqs the three + TESTER-014). No cycles; every referenced ID exists; sequence 015 is unused and referenced nowhere (no collision). PASS.
- Step 6.12 evidence: no `git-boundary-guard.jsonl` in any task folder of this cycle; release-state.md mtime 10:22:16 equals `## RC Opened At` written by CM-013; RC base = ai_main tip 375b053a, and ai_main has not moved; no RC commit touches `team/scripts/` or `subagents/`; queue markers match task states.
- Step 6.13: no real skip cites a missing bug. Placeholder and unresolved IDs (BUG-SKIP-synthetic-placeholder, BUG-1234/1235/9872, BUG-0001/0003/0004) are synthetic fixture strings inside lint/restamp tests, not skip annotations. PASS.
- Step 6.14: no new comment, docstring or log line cites a bug, task or version ID. The guard block is repeated verbatim in 10 modules; it cannot live in `lib/` because it must run before any package import can resolve, so the duplication is not a violation. PASS.
- Step 6.11 spot-check (untested mechanism / prior-gap recurrence): a sweep of every package module with `__main__` and a top-level package import found 15 more that crash by path. Two (`deep_verify/schedule.py`, `deep_verify/freshness.py`) printed usage by path on v2.27.3 and regressed in v2.28.0. One (`reports/project_summary.py`) advertises by-path usage in its docstring. This is the third RC fixing a hand-picked subset of the same class. Filed BUG-0541 (see Findings).
