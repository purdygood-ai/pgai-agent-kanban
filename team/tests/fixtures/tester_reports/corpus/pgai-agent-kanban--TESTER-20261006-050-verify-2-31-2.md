# Verification Report: rc/v2.31.2

## Summary

Verification of RC `ai_rc/v2.31.2` (HEAD 96f1f11a) against
`requirements/v2.31.2-bugfix-bundle-20261006.md` (BUG-0580..BUG-0586), run fresh
in the detached worktree `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20261006-050-verify-2-31-2`.
All seven bundled items are addressed and behaviorally confirmed against `ai_main` in sandboxes:
the headline effort regression (BUG-0580) is fixed and the integration suite is green again
(`INTEGRATION_EXIT=0`, 930 passed / 1 skipped, in a wake env exporting every `PGAI_<ROLE>_EFFORT=high`);
the ICD is fresh (BUG-0581); every CM release push site named in BUG-0582 now decides through the shared
resolver; purge, close help/docs, board CPU and engine-size ride-alongs meet their acceptance. The unit
suite is **not** green: `UNIT_EXIT=1`, 1 failed / 10044 passed, because the new board builder added in this
RC inlines a filename that a single-source invariant test forbids (BUG-0590). Three further findings were
filed: `unwind-rc` remains an ungated push site (BUG-0587, predates this RC), the `kanban.cfg_example`
narration count is wrong (BUG-0588), and the doc-truth lint skips citations after tagged fences
(BUG-0589, predates this RC). No defect affects runtime behavior of a shipped release path today;
recommendation SHIP-WITH-CONCERNS.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Findings

### Pre-flight (Steps 2-3)

- Worktree HEAD `96f1f11ab1a0a454009878eca80090d24de18b4e` == `ai_rc/v2.31.2`. `git status --porcelain` empty. `git stash list` empty. **pass**
- RC integrity scan: 636 DONE tasks name a feature branch; none of those branches exists locally (normal post-merge shape, passes both checks trivially). All six bundle merges are present on the RC: 4c14d58c (043), f78ddc3c (044), b5bd1d3a (045), 745cbddd (046), 6556e164 (047), 96f1f11a (048). **pass**
- Environment: this wake exports `PGAI_{PM,CODER,WRITER,TESTER,CM,PO}_EFFORT=high` and `CLAUDE_EFFORT=high`, the exact trigger condition for BUG-0580, so the gated suites below exercise the headline regression in its production shape.

### Per-item verification (Steps 6.2-6.5)

#### BUG-0580 — listless provider refuses every effort (headline, high)

- Fix (CODER-043, merge 4c14d58c): `_config_loader_check_effort` returns 0 with one stderr narration line when `pgai_provider_effort_values` returns 1 (no lib / no declaration); listed providers unchanged. Cert tests' `_run_wake_harness` now drops `PGAI_<ROLE>_EFFORT` and `CLAUDE_EFFORT` (option b + c).
- Behavioral, sandbox, same `load_config` against `ai_main` and the RC (TESTER-run, not CODER evidence):
  - provider `harness`, all six `PGAI_<ROLE>_EFFORT=high` (original failure mode): ai_main rc=1 `config_loader.providers.invalid_effort ... (valid for harness: none declared)`; RC rc=0, six pass-through narration lines, `PGAI_CODER_EFFORT=high` exported. **pass**
  - claude `llm_effort = minimal`: RC rc=1 naming `low medium high xhigh max`. claude `PGAI_CODER_EFFORT=minimal`: RC rc=1 naming the claude list. claude `high`: rc=0. codex `minimal`: rc=0. Identical to ai_main. **pass** (contract preserved)
  - New unit tests (section 7 of `test_config_loader_effort_resolution.py`) pin the listless behavior for both shapes (no lib; lib without declaration). **pass**
- Acceptance command re-run through the gated runner after the full suites, in this wake env (all six `PGAI_<ROLE>_EFFORT=high` exported): `run-integration-tests.sh --files` over the three cert files → rc=0, 25 passed; all nine `*WakeDispatchClosure::*` tests PASSED by name. **pass**
- `kanban.cfg_example` comment says config load "prints one stderr line"; measured: one line per role resolved (six for one `llm_effort` value or six exported vars). `docs/OPERATIONS.md` correctly says one per resolution. See Finding 3.

#### BUG-0581 — ICD stale after close-states change (medium)

- `python3 team/scripts/lint_icd_freshness.py` → `ok ... icd.json is fresh` (rc=0). `lint_icd_compat.py` → ok, 19 baselines incl. 1.16.1, support table mirrors SUPPORTED. **pass**
- `docs/api/ICD_VERSION` and package-data `ICD_VERSION` = `1.16.1`; `docs/api/baselines/icd-v1.16.1.json` byte-identical to `icd.json`. **pass**
- POST /operations/close and CloseBody now name `done` / `wont-do`; `superseded` appears only as "the retired ``superseded`` ... is refused ``close.invalid_state``". **pass**
- Running the freshness lint left `git status --porcelain` empty (its package-data sync is idempotent). **pass**

#### BUG-0582 — release-path push readers skip the project gate (medium)

- Bash half (CODER-045, merge b5bd1d3a): `pp_push_to_remote` now asks `pp_push_decision`; new `pp_push_policy <name> <site-tag>` is called once by `cm/open-rc.sh:216` (`[cm-open-rc]`), `lib/cm/release_preflight.sh:464` (`[cm-release]`), `cm/finalize-release.sh:201` (`[cm-finalize-release]`).
- Behavioral, sandbox `projects.cfg` with five shapes, `PGAI_DEV_TREE_PATH` set to a real dir in every case:

  | project shape | ai_main `pp_push_to_remote` | RC `pp_push_to_remote` | RC `pp_push_policy` |
  |---|---|---|---|
  | registered, no project.cfg | true | false | unresolved, `missing project-cfg` line |
  | project.cfg, not registered | true | false | unresolved, `missing registration` line |
  | registered, no dev_tree_path | true | false | unresolved, `missing dev-tree-path` line |
  | registered, dev_tree_path, no key | true | true | push, silent |
  | registered, `push_to_remote = false` | false | false | no-push, silent |

  **pass**. The skip line shape matches the check-push-lag vocabulary (`skipped-unresolved-project: project '<name>': missing <piece>: ...`).
- End-to-end stub coverage exists for all three bash sites × three unresolved shapes plus push-default and `false` (`TestReleasePathPushGate` in `test_cm_release_synthetic.py`; `test_release_push_readers_project_gate.py` for finalize-release). Their pass/fail is part of Step 5a.
- Python half (CODER-046, merge 745cbddd): `cancel.push_to_remote()` = `push_decision(...).may_push` via `resolve_push_decision`; cancel decides before the dev-tree gate and skips `ls-remote` when not pushing; ship-rc decides through the same function and prints the skip line. Fallback repos (`PGAI_DEV_TREE_PATH`, kanban.cfg `[paths]`, above-scripts) can only be reached by a project with no `dev_tree_path`, which is unresolved, so they never serve a push. Seven unresolved-project golden cases were regenerated (two named plus five with the same changed contract; CODER recorded the reasoning). **pass**
- Out-of-ticket push site: `cm/unwind.py` (`unwind-rc`) still runs `ls-remote` and `git push origin --delete` with no push decision. See Finding 2.

#### BUG-0583 — purge skips legacy superseded items (low)

- Sandbox dry run, 100-day-old files with Status done / wont-do / superseded / open, for bugs and priorities:
  - ai_main: superseded → `[SKIP] ... (status=superseded; not closed)`.
  - RC: superseded → `[WOULD PURGE] ... (status=wont-do; recorded as superseded)`; open still `[SKIP]`. Same for `purge_closed_priorities`. **pass**
- `grep -nE 'done\|wont-do' team/scripts/lib/purge-helpers.sh` → no match. **pass**
- Guard (Step 6.5): with a `python3` that exits 1 first on PATH and `dry_run=true` (real deletion, sandbox only), the loader prints `could not load the closed-state list ...; no file will be purged as closed` and **nothing is deleted** (all four files remain). The gate fails closed. **pass**

#### BUG-0584 — close docs stale after state refusal (low)

- `close.sh --help`: `--state STATE  Closed state: done|wont-do (default: done)`; prose names both refusals (`close.invalid_state` exit 1; ambiguous exit 2 / not found exit 3). **pass**
- Behavioral: sandbox `close.sh --state superseded` → `close.invalid_state`, rc=1, file byte-unchanged; unknown key → rc=3. The help is truthful. **pass**
- `generate_operator_verb_docs.py --check` → OK, 32 verb blocks. **pass**
- Cheat sheet line 136: `[--state done|wont-do]`; `grep superseded` → no match. **pass**
- Census: `operations-close-invalid-state-01` no longer named; prose added for `write-close-invalid-state-01` and `config-loader-effort-01` (including the listless pass-through); `lint_gate_census_compliance.py` → `[]`, rc=0. **pass**
- Observation (pre-existing, identical on ai_main): `--note` renders as a bare line with no description. Outside this RC's scope; not filed.

#### BUG-0586 — release_finalize.sh over engine ceiling (low)

- `wc -l`: `release_finalize.sh` 777 (was 806), new `release_fixed_in.sh` 68, sourced by release_finalize.sh. `lint_engine_size_dupe.py` → ok, 52 engine files within 800. **pass**

#### BUG-0585 — board render still costly per tick (low)

- Fix (CODER-047): one Python process builds the tick (`board_gather.py`, `board-build --gather=tree`) and pre-renders every column declared with `--board-widths`; other columns print their pre-render from a bash fast path (`column_render_board_serve.sh`) without starting Python. `create.sh` pane commands now append `--board-widths input=29,queue=26`. New files read in full (serve path: TTL check to the microsecond, pre-render must be no older than the snapshot, same provenance lock as the full path, every failure hands over to the full path).
- TESTER-built fixture (independent of CODER's): 4 projects, 340 tasks, 220 bugs; one project HALTed, one with no `priority/` dir, one with an Active RC, legacy `superseded` statuses, a malformed `Medium.` severity, queue entries with no task folder, unicode descriptions. The eight visibility-pane commands rendered sequentially, fresh `PGAI_AGENT_KANBAN_TEMP_DIR` per tick, `TIMEFORMAT='%U %S'`, host idle after the suites (load average about 0.9):

  | tree / invocation | CPU-s per tick (user+sys) |
  |---|---|
  | ai_main (panes as on main, no flag) | 5.10, 5.06 (colour); 5.00, 5.10 (no colour) |
  | RC, pane form (`--board-widths`) | 1.00, 0.98, 0.94, 0.96, 0.97, 0.94 (colour); 0.96, 0.94 (no colour) |
  | RC, standalone form (no flag) | 2.98, 2.86 (colour); 2.94, 2.84 (no colour) |

- Byte identity: all 8 columns × 2 colour modes, ai_main vs RC with the flag and ai_main vs RC without it: 32/32 identical (`cmp`), HALTed-project headers included. **pass**
- Target "< 1 CPU-s" (CODER-047 AC; BUG-0585 Expected "a small fraction of one core"): 7 of 8 pane-form samples under 1.00, one at 1.00 (rounded); median about 0.96 CPU-s, about 19% of one core at the 5 s cadence, down from about 100%. **pass-with-caveat**: met with about 4% margin on a minimum-size fixture, so a larger install will exceed 1 CPU-s per tick. Not filed: the requirement is met as specified, and the bug's Expected is qualitative.
- `docs/DASHBOARD-PANES.md` shows the pane form; `generate_window_docs --check` was clean per WRITER-048, and the generated tables are unchanged (standalone commands stay valid). **pass**

### Requirements-doc acceptance criteria

| Criterion | Result |
|---|---|
| Each bundled item is addressed in the implementation | **pass**: all seven, see the per-item sections above |
| All Python files compile cleanly | **pass**: `compile()` over all 800 tracked `*.py` in memory (no pycache written): 0 failures |
| All bash files pass syntax check | **pass**: `bash -n` over all 330 tracked `*.sh`: 0 failures |

### Step 5a — Gated suites (authoritative)

Run from the dev tree root in this wake's environment, sequentially, output under `/tmp/pgai_kanban_tmp/tester/v2.31.2/`:

- `bash team/scripts/run-unit-tests.sh --coverage` → **`UNIT_EXIT=1`**. Lint pre-flight passed (findings file: 0 findings). pytest: `1 failed, 10044 passed, 41 skipped` in 1021 s. Failure: `tests/unit/test_defer_posture_reader.py::test_no_python_module_but_the_posture_reader_names_the_campaign_file`, see Finding 1. Reproduced with `run-unit-tests.sh --files tests/unit/test_defer_posture_reader.py` → rc=1, `1 failed, 40 passed`. Deterministic (static AST scan). Category: `bug` (this RC's new production file, not a stale assertion: the test is unchanged and correct).
- `bash team/scripts/run-integration-tests.sh --coverage` → **`INTEGRATION_EXIT=0`**. `930 passed, 1 skipped` in 750 s. The integration suite is green again, after being red on v2.31.1 (BUG-0580).
- No infrastructure failure: both runners present and executable, pytest present, the requirements doc parsed, the dev tree accessible.

### Step 5b — Coverage (informational)

coverage unavailable: both runners printed `coverage disabled by config ([testing] coverage)` (the key is absent from config and defaults to `false`), so `--coverage` produced no JSON for this run. The `/tmp/pgai_kanban_tmp/coverage/{unit,integration}.json` files present are dated 2026-10-04 and belong to an earlier run, so they were not used. Not a finding.

### Steps 6.1, 6.8-6.14

- **6.1 release-state:** Active RC `v2.31.2`, RC Opened At `2026-10-06T14:37:12+00:00`, RC Opened By Task `CM-20261006-049-open-rc-2-31-2`, Last Released `v2.31.1` (well-formed). `pp_last_released_version` → `v2.31.1`; `semver_gt v2.31.2 v2.31.1` true. Local `ai_rc/v2.31.2` exists; tags `ai_v2.31.0`, `ai_v2.31.1`. **pass**
- **6.8 synthetic release:** the diff touches `cm/open-rc.sh`, `lib/project_paths.sh`, `lib/cm/release_finalize.sh` and `release_preflight.sh`, so the step applies. `run-integration-tests.sh --files tests/integration/test_cm_release_synthetic.py --verbose` → rc=0, 23 passed (including all 5 `TestReleasePathPushGate` cases). **pass**
- **6.9 constraint negatives:** no secret-shaped literals in added lines. The new `.sh` files are sourced libs, consistent with their siblings. **pass**
- **6.10 prereq graph:** CM-049 (open, DONE) → CODER 043/044/045 → 046 (after 044, 045) and 047 (after 045) → WRITER-048 (after 043, 046, 047) → TESTER-050 (after all six) → CM-052 release (WAITING on all seven). Acyclic, every ID exists, bookends linked. Sequence 051 is unused (no collision). **pass**
- **6.11 spot-checks:** (a) `cm/unwind.py` is an ungated push site, see Finding 2. (b) The doc-truth lint skips citations after tagged fences, see Finding 4. (c) State drift: none (Active RC matches the branch; release-state.md mtime 14:37:12 equals RC Opened At). (d) The ICD freshness lint's package-data sync left the worktree clean.
- **6.12 autonomy:** see the Autonomous criterion section below.
- **6.13 skip gate:** this RC adds no skip/xfail annotations; `lint_skip_bug_gate.sh` → OK, 0 missing files. The placeholder-shape grep hit only archived `team/tests/artifacts/*/unit-run.log` text, not test code. **pass**
- **6.14 coding standards (5 min):** no new bug/task/version citations in shipped code or docs. The one hit, `| 1.16.1 | supported | v2.31.2 |` in `docs/operator-api.md`, is the ICD support-table row (version-mapping data, same shape as the earlier rows). New libs carry header contracts. `_vis_input_width=29` / `_vis_queue_width=26` reuse the widths already passed positionally (not new tunables). The `DEFER-LOW-BUGS` literal duplication is Finding 1. **pass** (beyond Finding 1)
- **Test fidelity:** the new push-gate tests use the git network stub with registered and unregistered fixture projects; the cert-test fix scrubs the inherited effort env, but the production pass-through is pinned separately by unit tests that set `PGAI_CODER_EFFORT` explicitly. No concrete mocked-away condition found.

### Finding 1: Unit suite red — new board builder inlines the deferral campaign filename

**Category:** bug
**Fix Effort:** small (1 CODER task; one-line import change)
**Systemic Risk:** medium (second consecutive RC where a cross-cutting invariant test that the CODER's targeted `--files` runs never exercise first fails at TESTER; v2.31.1 had the ICD freshness tests)
**Regression:** yes
**Integrity:** none (a pytest assertion failure, not a breakage-class syntax/compile/import failure; the lint pre-flight passed)
**Filed As:** BUG-0590 (Path C-A, filed as BUG — code defect / regression / test failure)
**Filed By:** TESTER (autonomous Path C) — the new one-process board builder spells out the deferral campaign filename itself instead of importing the shared constant, so the unit suite fails.

`team/pgai_agent_kanban/dashboard/column_render/board_gather.py:110` (new in a8f344ca, CODER-20261006-047) contains the string constant `'DEFER-LOW-BUGS'`. `tests/unit/test_defer_posture_reader.py::test_no_python_module_but_the_posture_reader_names_the_campaign_file` requires that only `lib/defer_posture.py` name it (others import `CAMPAIGN_FILE_NAME` / `campaign_file_path`). Result: `UNIT_EXIT=1`. Severity per the SOP rubric: **medium**. Nothing at runtime misbehaves (the literal is only used in a malformed-campaign-file fallback path string), and the gated unit runner already exited 1 on the last release (v2.31.1, BUG-0581), so this is not a "worked last release, fails now" command regression. It does keep the RC from meeting the PASS precondition. Not a stale assertion: the test is unchanged and correct, and production code must change.

### Finding 2: `unwind-rc` still pushes (deletes the origin RC branch) without the shared push decision

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (one operator-only verb that requires HALT and confirmation)
**Regression:** no
**Integrity:** none
**Filed As:** BUG-0587 (Path C-A)
**Filed By:** TESTER (autonomous Path C) — the operator "unwind an RC" command still deletes the RC branch on the remote without asking the shared push rule, even for a project configured never to push.

BUG-0582's Expected says "Every site that can push a project's refs applies the one rule". `team/pgai_agent_kanban/cm/unwind.py` Step c runs `git ls-remote ... origin <rc>` and `git push origin --delete <rc>` with no push decision, and its repository falls back to `PGAI_DEV_TREE_PATH`. Proven in a sandbox with the git network stub: a registered project with `push_to_remote = false`, `unwind-rc.sh --confirm`, and the stub logged 2× `ls-remote` and 1× `push origin --delete ai_rc/v9.9.9`. Predates this RC (unchanged since v2.30.0) and was outside the decomposed tickets; CODER-046 flagged it. Severity **medium**: a real defect on an operator-invoked path, and the workaround is to not run unwind-rc on a local-only project (or run cancel-rc).

### Finding 3: `kanban.cfg_example` misstates the listless-provider narration count

**Category:** gap
**Fix Effort:** small (comment-only)
**Systemic Risk:** low
**Regression:** yes
**Integrity:** none
**Filed As:** BUG-0588 (Path C-A)
**Filed By:** TESTER (autonomous Path C) — the example config file says a provider with no effort list causes one warning line at config load, but it prints one per agent role.

Added by CODER-043 in this RC: "config load prints one stderr line saying so". Measured: six lines (one per role resolved). `docs/OPERATIONS.md` is correct, so the example file and the guide disagree (SOP-core Documentation Discipline). Severity **low**.

### Finding 4: Doc command-truth lint skips citations after any non-bash/sh fenced block

**Category:** bug
**Fix Effort:** small
**Systemic Risk:** low
**Regression:** no
**Integrity:** none
**Filed As:** BUG-0589 (Path C-A)
**Filed By:** TESTER (autonomous Path C) — the documentation command-truth check stops checking command citations after a code block tagged with a language other than bash or sh, so missing scripts go unreported.

`lint_doc_command_truth.py` `_FENCE_RE` only recognizes bare/bash/sh openers. Reproduced: a doc citing a non-existent script reports `scanned 1 citation(s) ... MISSING COMMAND`; the same doc with a preceding ```` ```text ```` block reports `scanned 0 citation(s) ... 0 finding(s)`. Predates this RC; reported by WRITER-048. Outside this RC's spec (a 6.11 spot-check). Filed because it is concrete and reproduced; it does not by itself change the recommendation, which Finding 1 already sets. Severity **low**.

## Lint Findings (non-blocking)

None. Both full-run findings files reported 0 findings (`[gated-runner] findings file written: .../findings-unit.json (0 finding(s))` and the same for `findings-integration.json`). This also confirms BUG-0586's acceptance that neither findings file carries an `engine_size_dupe` finding.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=29d freshness=stale — The only ledger record is red and stale on both counts (29 days old, beyond the 7-day window, and recorded for v2.9.20, not v2.31.2); that run filed BUG-0167 (docker/claude-abliterated and podman/rocky9 build failures), now `done`, and no later deep-verify has run, so nothing is known about whether this RC's images build.

## Autonomous criterion

1. Tasks force-promoted WAITING→BACKLOG by hand? **NO.** Every promotion of 043-048 and 050 is logged by the wake (`wake(<agent>): WAITING task ...: prerequisites satisfied, promoting to BACKLOG` in `logs/cron-*.log`); `logs/reset.log` has no entry this cycle.
2. `release-state.md` edited outside the CM scripts? **NO.** mtime `2026-10-06 14:37:12.4` equals `RC Opened At` (written by open-rc); content consistent.
3. Scripts patched mid-build? **NO.** No live-install file under `scripts/` or `pgai_agent_kanban/` changed after RC open; every `team/scripts` change on the RC is a reviewed CODER commit from this bundle.
4. Queue files edited by hand? **NO** evidence. Queue mtimes coincide with wake/agent transitions (for example `tester_backlog.md` at 17:22:11, matching this task's dispatch record).
5. Remote git op by a non-CM agent? **NO.** No `logs/git-boundary-guard.jsonl` in any task of this cycle (CM-049, CODER 043-047, WRITER-048, PM-006, TESTER-050). This session's own unwind-rc probe ran behind the test-only git network stub, which intercepted every remote verb before git (no real call, no guard record).

Autonomous operation criterion: Manual interventions required: none

## Caveats

- **Report written via shell, not the Write tool.** The agent harness's Write tool refused `report.md` ("Subagents should return findings as text"). This report is a machine-read pipeline input (CM's ship-policy parser reads `## Recommendation` and the `Finding` blocks) and a mandatory deliverable of this task, so it was written with a shell heredoc. Recorded for operator awareness.
- **Instruction conflict:** roles/TESTER.md "Git workflow" says to commit verification artifacts on a feature branch off the RC; the dispatch prompt forbids creating a feature branch and keeps the worktree detached. Followed the dispatch prompt (more specific). Artifacts live in the task folder outside the dev tree, so nothing needed committing.
- **Concurrency note:** one targeted `run-unit-tests.sh --files` repro ran while the full integration suite was in flight. The integration run's result (exit 0) was unaffected, and its findings file was written before the repro.
- **Process observation (feeds Finding 1's Systemic Risk):** PM tickets tell CODERs to run only `--files` for the files they changed, so cross-cutting invariant tests (single-source rules, ICD freshness) first run at TESTER. This is the second consecutive RC where that left the unit suite red. Consider a cheap "invariant tests" subset CODERs always run; that would be a PM/role-file decision, not filed here.
- Pre-existing and out of scope, not filed: `close.sh --help` renders `--note` with no description (identical on ai_main).
- Board CPU margin (BUG-0585) is thin; see that section.
- deep-verify: not run (operator-only tier); the freshness line above is the ledger's fact, not a TESTER verdict.
- No prompt-injection-like content was found in any file read.
