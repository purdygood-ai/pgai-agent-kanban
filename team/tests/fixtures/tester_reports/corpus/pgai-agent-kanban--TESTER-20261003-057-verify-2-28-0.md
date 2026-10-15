# Verification Report: rc/v2.28.0

**Task:** TESTER-20261003-057-verify-2-28-0 · **RC commit:** 52b30be5 (`ai_rc/v2.28.0`) · **Requirements:** `requirements/v2.28.0-shared-modules-no-inline-python.md` · **Date:** 2026-10-04

## Summary
Verified RC v2.28.0 ("shared modules, no inline Python") against its requirements document. In scope: all 9 deliverables, the convergence criterion, the 7 acceptance bullets, and the "Notes for TESTER" behaviour-preservation checks. I ran both gated runners, exercised every new lint's guard with fixture trees, compared converted call sites with the `ai_main` code on status-file variants, and ran CLI-vs-REST golden pairs for the ten class-ii action verbs and for `/show` / `/show-queues` (against an `ai_main` export and a copy of the live board). The release delivers its foundation. Three shared lints are wired, quality-class, and fire correctly. The shell-row baseline fell 30 → 18 (≥12 required). The inline-Python allowlist fell 172/8605 → 145/7744. Every deny-listed duplicate family is at 0. `wake_task_state.sh` carries no heredoc. The ten class-ii routes call `ops.write` in process with byte-identical output, and the ICD was bumped exactly once.

**The unit runner is red** (1 failure, from this RC). Three findings were filed. Recommendation: ship with concerns.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Suite Results (Step 5a / 6.6–6.7)
- `team/scripts/run-unit-tests.sh --coverage` → **UNIT_EXIT=1**. Result: 1 failed, 7686 passed, 43 skipped (27m59s). The lint pre-flight passed (quality findings only). The failure is `tests/unit/test_lint_doc_command_truth.py::test_real_tree_default_exempt_list_closes_the_named_process_internal_citations` (Finding 1).
- `team/scripts/run-integration-tests.sh --coverage` → **INTEGRATION_EXIT=0**. Result: 643 passed, 1 skipped (16m55s).
- Both runners ran with `PGAI_AGENT_KANBAN_TEMP_DIR=/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester-057`. Logs: `tester/unit.log`, `tester/integration.log` under that root.

### Coverage (Step 5b, informational only)
Source: the `totals` from `coverage/unit.json` and `coverage/integration.json` (field names verified against the generated JSON).
- unit — line 80.5% (`percent_statements_covered`), branch 67.1% (`percent_branches_covered`); blended 77.6%.
- integration — line 14.2%, branch 9.9%; blended 13.2%.

## Findings

### Finding 1: Gated unit runner red — two package modules crash when run by file path
**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (the same by-path-vs-package-import trap hit CODER-044 on `agent-timing.sh` in this very RC, and v2.30.0 continues the same kind of conversion; implementing tasks certified with targeted `--files` runs, which skip this whole-tree test)
**Severity:** medium
**Regression:** yes
**Integrity:** none
**Filed As:** BUG-0533 (Path C-A)
**Filed By:** TESTER (autonomous Path C) — two Python files the docs name now crash with "module not found" when started directly, because this release made them import the shared package; the dashboard still works, but the whole-tree doc check and its unit test now fail, so the unit suite is red.

`team/pgai_agent_kanban/dashboard/scan_human_approvals.py` (line 52) and `team/pgai_agent_kanban/lib/agent_metrics.py` (line 178) now import `pgai_agent_kanban.*` (deliverables 2–3). Run as `python3 <path> --help` with no `PYTHONPATH`, each raises `ModuleNotFoundError: No module named 'pgai_agent_kanban'`; on `ai_main` both exit 0. `lint_doc_command_truth.py` runs every `.py` path a document mentions, so it reports 2 HELP FAILS. Its real-tree unit test then fails, so UNIT_EXIT=1. The actual runtime callers are fine: `human-review.sh` (sets `PYTHONPATH`) and `agent-metrics.sh` (goes through `scripts/lib/agent_metrics.py`) both produced output identical to `ai_main` in a sandbox. The doc citations (operator-api.md 611/670/1024, DASHBOARD-PANES.md 161) name the files as where the logic lives, not as commands to run. That is why I rate this medium rather than high: no operator command that worked last release fails now, but the certification suite is red and has a clear fix.

### Finding 2: show-queues with no project prints all-zero counts instead of refusing
**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated to the queue pane's project resolution)
**Severity:** medium
**Regression:** yes
**Integrity:** none
**Filed As:** BUG-0534 (Path C-A)
**Filed By:** TESTER (autonomous Path C) — the queue pane, asked for no particular project, now prints "0/0" for every agent as if the queues were empty, where it used to refuse and say a project is required.

Traces to deliverable 7 and the Enforcement Class ("behaviour of every converted call site is unchanged"). With no `--project` and no `PGAI_PROJECT_NAME`, `ai_main`'s `show-queues.sh` exits 1 ("no project specified"). The RC exits 0 printing `0/0` for every agent, and `GET /show-queues` returns the same text at 200. On a legacy single-project root whose `tasks/queues/coder_backlog.md` holds two entries, the pane still says `coder: 0/0`. That legacy layout is the one `show-multi.sh` falls back to with no `--project`. CODER-053 recorded this as deliberate design choice (4). It is filed anyway because it replaces a refusal with counts the code never read. All in-tree multi-project callers pass `--project`, so the blast radius is small.

### Finding 3: metrics_aggregator's private atomic writer was not replaced
**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (one module; the duplicates lint's exact-name deny-list is the reason it went unseen)
**Severity:** low
**Filed As:** BUG-0535 (Path C-A)
**Filed By:** TESTER (autonomous Path C) — one of the private "write a file safely" helpers this release set out to replace with the shared one was missed, and it skips the flush-to-disk step the shared writer performs.

Deliverable 2 says every copy in the Intent inventory is replaced by an import ("`_atomic_write` in 8 modules"). `ai_main` has exactly eight `def _atomic_write*` helpers. Seven were replaced. `team/scripts/lib/metrics_aggregator.py::_atomic_write_json` (temp + `os.replace`, no fsync) remains. CODER-044 counted the eight differently, treating resolution_stage's inline blocks as the eighth. Per the conflict policy I applied the more conservative reading. `lint_python_duplicates.py` matches the exact name, so it reports `atomic-write=0`.

### Finding 4: Lint ratchets and convergence criterion
**Category:** pass
- `lint_inline_python.py`: no file above its allowlist; allowlist 172 blocks/8605 lines at the lint's introduction → 145/7744 at RC head (spec's v2.26.12 measure: 131 heredocs/8,514 lines; RC measures 103 heredocs + 42 `-c`). Fixture probe: unlisted file, above-count, below-count, unquoted `<<PY`, `python3 - \` continuation and `python3 -c` all detected; `tests/` excluded.
- `lint_python_duplicates.py`: atomic-write, traversal, version-parse, iso-parse, read-section, ini-instantiation, state-regex all 0; 0 ratchet findings. Fixture probe fires all seven families plus duplicate-body.
- `lint_api_route_impl.py`: 0 findings; 14 module / 18 shell rows. Shell rows 30 → 18 across the RC (−12, criterion ≥12 met). Fixture-manifest probes fire route-not-calling, route-shells-out, cli-not-reaching, shell-rows-above-baseline, and baseline-tighten.
- `grep -cE 'python3?\b[^#]*<<' team/scripts/lib/wake_task_state.sh` → 0.
- All three are quality class (exit 0 always) and are wired in both runners, the census, and `docs/coding-standards.md`, as the requirement's Enforcement Class says.

### Finding 5: Class-ii action routes call ops.write in process; CLI/REST golden pair
**Category:** pass-with-caveat
The route-to-function and no-subprocess test covers all ten verbs (`test_operations_ops_calls.py`, with process starters patched to fail). My independent golden pair ran 12 cases (halt ×2 incl. idempotent repeat, unhalt with nothing to unhalt, halt of an unknown project, close of an unknown and a BACKLOG key, wontdo on DONE and BACKLOG, reset of WORKING, delete without/with force, halt-after with a malformed `until`), each on three identical sandbox boards: REST, RC CLI, and `ai_main` CLI. Exit code, stdout, and stderr were identical in all 12; file effects were identical in 11. Caveat: after `delete --force`, `ai_main` also removed the blank line under the queue header (its regex `^\s*-` spanned newlines); the RC keeps it. The new result is the correct layout but was not declared by CODER-048. ICD 1.12.0 → 1.13.0, one bump, changelog entry present.

### Finding 6: Converted heredoc families preserve behaviour
**Category:** pass-with-caveat
Every function in the three parity reference fixtures (`wake_task_state_heredoc_reference.sh`, `discovery_finalize_heredoc_reference.sh`, `overwatch_harness_heredoc_reference.sh`) is verbatim `ai_main` code, or the verbatim `ai_main` inline heredoc body lifted into a function. So the parity tests compare against real previous behaviour. My own old-vs-new run of `get_state` / `set_state` / `stamp_model_field` on 7 status variants (lowercase, blank lines, bold, inline `## State: X`, trailing text, wrong heading, trailing whitespace) was identical except the declared deliberate difference: inline `## State: DONE` now reads and rewrites instead of failing. The heredocs left in the deliverable-5 files (discovery bundle ordering, dispatch ledger, README field extractors, provider read, Model-dominance) fall outside the parenthetical scope the requirement names.

### Finding 7: /show and /show-queues route through ops.read
**Category:** pass
`show.sh`: 16 cases on a copy of the live board (tasks across roles and months, `--file readme`, three bugs, an unknown key) — `ai_main` CLI, RC CLI, and REST byte-identical. `show-queues --project`: identical except the wall-clock `elapsed:` line. The 101-line heredoc is gone. The no-project behaviour is Finding 2.

### Finding 8: Shared library, status and marker APIs, bash helpers, coding standard
**Category:** pass
See the verification log below: D2 modules and `projects_registry` order; `md_fields` variants; D3 CLI and read-mode guards; D4 marker verbs; D8 helper copies collapsed to one each, with `_active_rc` reading through `read_state_field`; D9 directives 12–14.

## Lint Findings (non-blocking)

- `doc_command_truth` — 2 HELP FAILS (`scan_human_approvals.py`, `agent_metrics.py` crash when run by path), unit suite only — filed BUG-0533 (Path C). The same root cause is Finding 1, which is the item that affects the recommendation.
- `inline_python` — 4 `unquoted-delimiter` instances (`team/scripts/lib/dashboard/column_render_data_multi.sh:249`, `team/scripts/verify-rc-state.sh:494`, `:633`, `:947`), in both suites; pre-existing code first surfaced by this release's lint — filed BUG-0536 (Path C).

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=26d freshness=stale — last record is red (claude-abliterated `FROM` parse error and rocky9 `COPY --chmod` step), already filed as BUG-0167 (status done, fixed in v2.9.22); stale on both counts (26d exceeds the 7-day window, and v2.9.20 is not the v2.28.0 under verification), so nothing is known about whether the images build cold against this tree.

## Autonomous criterion (Step 6.12)
1. Tasks force-promoted WAITING→BACKLOG by hand? **NO.** Promotions carry the wake script's summary; no status Summary in the 15 tasks mentions a manual or operator edit.
2. `release-state.md` edited outside the CM scripts? **NO.** File mtime 2026-10-03T20:25:58 equals `## RC Opened At` (written by CM-20261003-056's open).
3. Scripts patched mid-build? **NO.** Every commit on `ai_main..ai_rc/v2.28.0` arrives via one of the 15 task merges (no first-parent non-merge commits). No live-install source file changed after RC open (only `__pycache__` refreshes).
4. Queue files edited by hand? **NO.** Queue mtimes line up with agent transitions; no evidence otherwise.
5. Remote git operation by a non-CM agent? **NO.** No `git-boundary-guard.jsonl` exists in any of the RC's task folders.

Autonomous operation criterion: Manual interventions required: none

## Caveats
- `scripts/task-status.sh --summary` and `task_status set-section --body` accept a body containing a `## ` line, which creates a new section. This behaviour is identical on `ai_main`, so it is not from this RC. Observation only.
- `show-queues.sh --help` lists `--details` and `--kanban-root` without descriptions. Identical on `ai_main`; not from this RC.
- CODER-053 reported 4 failures in `tests/unit/test_posture_bar_truth.py` on its run of the pristine RC. In this certification run the file passed (19/19), so the failure was environment-dependent. No finding.
- The task README's `## Working Directory` reads `none`. Per the wake prompt, verification ran in the detached worktree at the RC commit, and no feature branch was created. Artifacts live only in the task folder, never in the dev tree.
- Container tier not run (operator-only). See Deep-Verify Freshness.

## Verification Log (checkpointed as work proceeded)

### Pre-flight (Steps 2–3)
- Worktree HEAD 52b30be5 == `ai_rc/v2.28.0`; `git status --porcelain` empty; `git stash list` empty — pass.
- All 15 prerequisite tasks (CODER-041..054, WRITER-055) read `DONE`; each has exactly one `--no-ff` merge commit on `ai_main..ai_rc/v2.28.0`; no DONE task's feature branch survives locally (Check 1/2 pass trivially) — pass.
- Gated suites started (Step 5a) with `PGAI_AGENT_KANBAN_TEMP_DIR=/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester-057`.

### Acceptance criteria and deliverable checks
- AC1 inline lint: no file above allowlist; 4 `unquoted-delimiter` findings (pre-existing heredocs in column_render_data_multi.sh, verify-rc-state.sh x3). Allowlist trajectory in RC: 172 blocks/8605 lines (CODER-041 baseline) -> 145/7744. Fixture probes: unlisted, above, below, unquoted all reported; `python3 - \` continuation and `python3 -c` counted; tests/ excluded.
- AC2 duplicates lint: all seven deny-list families = 0; fixture probe of all seven + duplicate-body fires. Caveat: `scripts/lib/metrics_aggregator.py::_atomic_write_json` (an ai_main atomic-write copy) survives under a non-deny-listed name.
- AC3 api-route lint: 0 findings, 14 module / 18 shell rows; shell rows 30 -> 18 over the RC (convergence >= 12 met).
- AC4 grep wake_task_state.sh heredocs = 0 — pass.
- D5 parity: old (ai_main verbatim reference) vs new wake_task_state get_state/set_state/stamp_model_field on 7 variants: identical except documented deliberate difference (inline `## State: X` now accepted).
- D6 golden pair (sandbox boards; REST vs new CLI vs ai_main CLI via git archive): 12 cases across halt/unhalt/halt-after/close/wontdo/reset/delete incl. refusals + idempotent repeat — exit, stdout, stderr identical on all 12; file effects identical on 11. `delete --force`: ai_main also swallowed the blank line under the queue header (old regex `^\s*-` spanned newlines); new keeps it. Benign, undocumented -> pass-with-caveat.
- D6 ICD: 1.12.0 -> 1.13.0, one bump, changelog entry present — pass.
- D7 show.sh: 16 cases on a copy of the live board — old CLI, new CLI, REST identical. show-queues --project: identical except wall-clock elapsed. show-queues with no project (and legacy no-projects.cfg root): ai_main exit 1 "no project specified"; RC exit 0 printing every agent `0/0` even when root tasks/queues holds entries -> BUG candidate.
- D2 lib modules present; `projects_registry` priority order verified by test with non-trivial order; `md_fields.field_value` probed on 8 variants (inline, bold, blank lines, case, CRLF, prefix heading) — correct.
- D3 task_status read/set-section/render CLI + `task-status.sh --read-state` probed: Python and bash read modes agree on 7 variants; set-section on a missing heading refuses dual-channel and writes nothing; render writes nothing. All 13 inventory State readers import task_status.
- D4 set_marker/remove_marker CLI verbs: flip, prefix-key isolation, bad marker (rc 1), missing line (rc 3) — pass. mark_backlog and discovery cache flip route through `ops render_marker`.
- D5 parity reference fixtures checked against ai_main: every reference function is verbatim ai_main code (or the verbatim inline heredoc body lifted into a function), so the parity tests compare against the real previous behaviour.
- D8 helper copies: get_val 6->1, section_header 4->1, _active_rc 3->1, read_field 3->1. `_active_rc` old-vs-new on 9 release-state variants: identical except the old awk matched a `## Active RCs` prefix heading (new is exact) — benign.
- D9 coding-standards directives 12–14 + lint table + owning-module table present.
- Registration: all three lints wired in both runners and in the census; manifest-schema lint and operator-verb-docs --check pass; ICD freshness/compat pass.
- 6.1 release-state: Active RC v2.28.0, Last Released v2.27.3; semver_gt v2.28.0 v2.27.3 true — pass.
- 6.10 prereq graph acyclic; CM-056 open -> CODER/WRITER -> TESTER-057 -> CM-059 release — pass.
- 6.13 skip-cites-real-bug: no placeholder or unresolved citation in real skip annotations (hits are lint self-test fixture strings) — pass.
- 6.14 provenance in added shipped lines: only sanctioned usage examples — pass.
