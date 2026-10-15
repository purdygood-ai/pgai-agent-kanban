# Verification Report: ai_rc/v2.24.8

## Summary

Verification of release candidate `ai_rc/v2.24.8` (commit `e13cdbd`) for project
pgai-agent-kanban against
`requirements/v2.24.8-bugfix-bundle-20260920.md` — a discovery-pipeline bug
bundle carrying twelve bundled items (BUG-0344 through BUG-0355, including three
hygiene ride-alongs). Pre-flight, structural, content, behavioral and
constraint checks were run inside the detached read-only worktree at
`/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260920-024-verify-2-24-8`.
Both gated runners were executed with `--coverage` and both exited 0.

Verdict: every one of the twelve bundled items is addressed in the implementation, both
gated runners exit 0, and every Python and bash file in the diff compiles and parses —
the requirements doc's three acceptance criteria are met. Sixteen findings: eleven `pass`,
one `pass-with-caveat`, three `gap`, one `bug`. Five follow-ups filed via Path C
(autonomous follow-up filing — non-blocking) as BUG-0357 through BUG-0361; none is severe
enough to make the release unusable, so the recommendation is `SHIP-WITH-CONCERNS`. The
release pipeline works, no data is at risk, no security defect was found, and the build
ran unattended from PM materialization to this verification with zero manual
interventions.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Findings

### Finding 1: Event band still exceeds the 5 s pane-refresh budget at the shipped read bound against the operator's live logs

**Category:** gap
**Fix Effort:** medium (3-5 CODER tasks)
**Systemic Risk:** low (isolated to one dashboard renderer)
**Filed As:** BUG-0357 (Path C-A — filed as BUG)
**Traces to:** bundled item BUG-0344-event-band-render-budget-unmet, "Expected: one render
completes inside the pane's own refresh interval (5 s by default) against production-shaped
input".

Reproduced the bug's own harness: a throwaway kanban root whose five per-agent logs are
symlinks to the operator's live `logs/cron-*.log` (818 MB total), rendering with
`--no-color` from the RC worktree.

| tree | read bound | surviving signal lines | wall clock |
|---|---|---|---|
| `ai_main` (v2.24.7) | 20000 (shipped default) | 29309 | 147.84 s / 141.61 s |
| `ai_rc/v2.24.8` | 20000 (shipped default) | 29309 | 23.04 / 21.68 / 21.33 / 21.17 / 21.37 / 21.26 s |
| `ai_rc/v2.24.8` | 5200 (~7.6k surviving, the volume BUG-0344 measured) | 5575 rows out | **4.03 s** |

The fix is real and large — roughly a 6.8x speedup over `ai_main` on identical input, and it
meets the 5 s budget at the workload the bug measured (7,628 surviving lines → 4.03 s). It does
not meet the budget at today's production volume: the operator's logs have grown since the bug
was filed, 20,000-line tail now yields 29,309 surviving signal lines instead of 7,628, and the
render takes ~21 s against a 5 s pane refresh.

Not a regression — `ai_main` takes ~145 s on the same input. Rated `medium` per
`SOP-core.md` ("Rating a Finding"): the band still renders correctly and the pane simply
refreshes behind itself, so a workaround exists (lower
`PGAI_DASHBOARD_EVENT_BAND_READ_BOUND_LINES`; 5200 measured green).

### Finding 2: Event band continuation lines inherit their parent's timestamp; render is deterministic — pass

**Category:** pass
**Traces to:** bundled item BUG-0347-event-band-stamps-untimed-lines-now.

Fixture: one agent log with a timestamped parent (`08:15:42`), two bare
`error:` / `message:` continuation lines, and a second timestamped parent (`09:30:11`).

- RC render: all three rows of the first block carry `[pm 08:15:42]`, adjacent, in source
  order; the second parent follows at `[pm 09:30:11]`. No row carries a timestamp absent
  from the input.
- Two consecutive RC renders one second apart are **byte-identical** (`cmp` on full stdout).
- Original failure mode synthesized against `ai_main`: the two continuation lines are stamped
  with the render clock (`[pm 21:15:32]`), sort to the bottom away from their parent, and the
  two renders **differ** (`21:15:32` vs `21:15:34`). Both halves of Step 6.5 confirmed.

### Finding 3: Git boundary guard refuses remote verbs behind leading global options — pass

**Category:** pass
**Traces to:** bundled item BUG-0349-git-guard-bypassed-by-global-opts.

Exercised the shim on PATH in a throwaway root with an isolated repo and evidence sink:

- Under `PGAI_AGENT_ROLE=coder`: `git -C <dir> fetch`, `git -c a.b=c push`,
  `git --no-pager pull`, and `git --git-dir=<d> fetch` each exit 1 with the dual-channel
  block (`error: git-boundary-guard.non-cm-remote-verb` / `message: role 'coder' invoked
  'git <verb>' …` / `remedy: ask CM to run …`).
- The evidence file gained exactly four JSON-Lines records, one per refusal, each `argv`
  preserving the full invocation including the global options.
- Under `PGAI_AGENT_ROLE=cm` the same four pass through to the real git with its own exit
  status preserved (0 for the two that had a repo, 128 with git's own
  `fatal: not a git repository` for the two that did not) and the evidence file stayed at
  four records.
- `git -C <dir> status --short` under role `coder` passes through untouched (exit 0).
- Original failure mode on `ai_main`: `git -C <dir> fetch` under role `coder` exits **0**
  (bypassed) while bare `git fetch` is refused — the exact bypass the bug describes.

### Finding 4: Terminal-task worktrees are reclaimed by the reconciliation sweep — pass

**Category:** pass
**Traces to:** bundled item BUG-0353-completed-task-worktrees-not-torn-down.

`reconcile_terminal_worktrees` (new in `team/scripts/lib/wake_task_state.sh`) is wired at
three call sites in `team/scripts/lib/wake_driver.sh` (lines 503, 640, 728). Exercised in a
throwaway git repo with four registered worktrees under an isolated worktree base and four
synthetic task folders:

| task state | worktree after sweep |
|---|---|
| DONE | removed |
| WONT-DO | removed |
| WORKING | retained |
| BLOCKED | retained (documented conservative choice — preserves evidence) |

A second invocation is a clean no-op returning 0 (idempotency criterion). The sweep is
scoped by `case "$wt_path" in "${wt_base}"/*)`, so a sibling project's worktrees and the
canonical dev tree are structurally out of reach; this was confirmed inadvertently when a
leaked `PP_dev_tree_path` pointed the first run at the real dev tree and the base filter
excluded every real worktree.

### Finding 5: Post-suite scratch sweep survives read-only leftovers and cannot set the runner verdict — pass

**Category:** pass
**Traces to:** bundled item BUG-0354-integration-runner-red-on-clean-suite.

- With `0555` directories seeded under `${TEMP}/tests/`, RC `pgai_temp_cleanup_tests`
  removes the `tests/` subtree completely and returns 0 under `set -euo pipefail`.
- Original failure mode on `ai_main`: the same fixture aborts the calling script
  (`rm: cannot remove …: Permission denied`, outer exit 1, the script never reaches its
  final line).
- The runner now calls the sweep through `gated_runner_run_quality_lint "scratch_sweep"`.
  Injecting a sweep that fails for a reason `chmod` cannot fix prints
  `[gated-runner] scratch_sweep: quality-class finding(s) (exit 1) — non-blocking,
  continuing.` and the wrapper returns 0, so the verdict stays pytest's.
- Both gated runners' live output confirms the ordering change: `scratch_sweep: OK` is
  printed before the findings file is written, on both suites.

### Finding 6: Hygiene bug filer's tree-cleanliness gate suppresses dirty-site findings and still files committed ones — pass

**Category:** pass
**Traces to:** bundled item BUG-0346-hygiene-lint-files-phantom-bugs.

Drove `file_or_cite_hygiene_bugs` against two throwaway git repositories with an identical
synthetic `provenance` finding:

| repo | finding's file state | result | `bugs/` contents |
|---|---|---|---|
| committed-site | committed at HEAD | `[('provenance','filed','BUG-0001',1)]` | `BUG-0001-hygiene-lint-provenance.md` |
| dirty-site | violation only in an uncommitted edit | `[]` | empty |

Both halves of the acceptance criterion confirmed in one run: the real case still files
(no regression), the transient case files nothing. The filed bug carries the new
`## Observed At Commit` section with the repository's real HEAD hash.
`python3 team/scripts/lint_hygiene.py --classes provenance` against the RC reports
`OK — 0 findings across 8 scan director(y/ies)`, confirming the scanner itself is unchanged,
and no `hygiene` entry appears in either runner's findings file this run.

### Finding 7: OVERWATCH backlog is scaffolded on fresh create, upgraded installs, and self-healed at wake — pass

**Category:** pass
**Traces to:** bundled item BUG-0355-overwatch-backlog-not-scaffolded.

- `team/scripts/lib/overwatch_backlog_seed.sh` is the single source of the seeded text, sourced
  by all three call sites (`create-project.sh:102`, `wake_bootstrap.sh:145`,
  `migrate_v2.24.7_to_v2.24.8.sh:64`).
- `team/scripts/lib/wake_driver.sh:428` recreates a missing `overwatch_backlog.md` and continues
  into the resolution stage rather than taking the "backlog not found, skipping" exit — the
  branch that would have swallowed a HALT the overwatch carve-out let through.
- Upgrade migration exercised in an isolated two-project registry:
  `--dry-run` reports `[would-seed] demo` / `[skip] alpha` and writes nothing; the real run
  creates `demo`'s file with the shared header text and leaves `alpha`'s pre-existing content
  byte-for-byte; a third run reports `[skip]` for both (idempotent).
- The auto-runner selects it correctly: `run-migrations.sh --from v2.24.7 --to v2.24.8 --dry-run`
  reports `applying 1 migration(s) selected for (v2.24.7, v2.24.8]`.
- `migrate_v2.24.7_to_v2.24.8.sh --help` names every argument.

### Finding 8: Deferred-bug glyph renders as a suffix and the deferral posture reaches the status bar — pass

**Category:** pass
**Traces to:** bundled item BUG-0348-defer-glyph-placement.

`defer_card_glyph_prefix` is renamed to `defer_card_glyph_suffix` at the single shared glyph
site and returns `" <glyph>"` instead of `"<glyph> "`; all three consumers
(`column_render_cells.sh`, `column_render_data_multi.sh`, `column_render_frame.sh`) append it
after the id, and `column_render_frame.sh` no longer folds the glyph width into `tag_vis`, so
every card's id starts at the same column. `docs/OPERATIONS.md` was updated in the same change
and now describes the suffix placement with an example.

Status-bar half, rendered live against the operator's install (which has
`projects/pgai-agent-kanban/DEFER-LOW-BUGS` armed with `until=v2.27.0`):

```
global:       v2.24.6 | PM:auto | BUGS DEFERRED 1 (<=low) until v2.27.0 | Sun 2026-09-20T21:21:32
per-project:  v2.24.6 | pgai-agent-kanban:v2.24.7 | workflow:release | PM:auto | BUGS DEFERRED 1 (<=low) until v2.27.0 | ...
status-right: v2.24.6  PM:auto  HALT:off  BUGS DEFERRED 1 (<=low) until v2.27.0  ...
```

The segment is present on all three bars, in the same shape as the neighbouring segments, and
covers none of them.

### Finding 9: Hardcoded scratch-root literals removed from the why-details test module — pass

**Category:** pass
**Traces to:** bundled item BUG-0345-test-anti-pattern-hardcoded-temp-literal.

Both literals in `team/tests/unit/test_why_details_pane.py` are now built from the
`pgai_tests_temp_dir` fixture rather than spelled out, with a comment explaining the path is
inert message text. `python3 team/scripts/lint_test_anti_patterns.py` reports
`OK — 0 findings across 554 file(s) (342 test, 212 runtime)` and exits 0. `lint_test_anti_patterns.py`
itself is unchanged in the diff, as the ticket's constraint required.

### Finding 10: Engine-size ceiling cleared for the named file, but a different engine file crossed it in the same RC

**Category:** pass-with-caveat
**Traces to:** bundled item BUG-0350-wake-driver-invoke-over-engine-ceiling.

Met: `team/scripts/lib/wake_driver_invoke.sh` is 798 lines on the RC (845 on `ai_main`), the
guard wiring having been extracted into the new single-concern stage
`team/scripts/lib/wake_guard_wiring.sh` (77 lines) — the first and highest-fidelity of the
three directions the bug offered, not the allow-list shortcut it warned against.
`run-unit-tests.sh` exits 0. Both exported variables are documented in full in the new stage's
own header, which `wake_driver_invoke.sh:644` points at. The guard-wiring tests
(`test_wake_guard_wiring.py`, `test_git_boundary_guard_wiring.py`) pass.

Caveat: the bug's second acceptance bullet — "`lint_engine_size_dupe.py` reports no oversized
engine file" — is not met on the RC as a whole. `team/scripts/lib/wake_task_state.sh` went from
794 lines on `ai_main` to 907 on the RC (the `reconcile_terminal_worktrees` addition from
CODER-20260920-018), and is now the file the lint names. This is a quality-class reading, not
breakage, and it is recorded under `## Lint Findings (non-blocking)` and filed as BUG-0359 (Path C-A — filed as BUG).

### Finding 11: Engine-size check demoted to advisory; duplication half stays hard — pass

**Category:** pass
**Traces to:** bundled item BUG-0351-engine-size-heuristic-hard-gates-suite.

`lint_engine_size_dupe.py`'s `main()` now splits its nonzero codes — `1` for a duplicate-function
collision (real damage, hard), `3` for a size-only violation (advisory, CONVERT) — and
`docs/blocking-site-census.md` row 53 was updated to state that split. The test module's
docstring names the enforcement class it implements, its real-tree size test narrates instead
of asserting, and its real-tree exit-code test accepts `0` or `3` while still rejecting `1`.

Exercised directly:

| invocation | exit |
|---|---|
| `--scripts-dir team/tests/fixtures/engine_size_dupe/duplicate_function` | 1 (hard) |
| `--scripts-dir team/tests/fixtures/engine_size_dupe/oversized_engine` | 3 (advisory) |
| `--scripts-dir team/scripts` (the real tree, one file over the ceiling) | 3 |

The RC demonstrates the fix live rather than only in fixtures: a real engine file
(`wake_task_state.sh`, 907 lines) is over the ceiling on this very RC, the lint recorded it as
a quality finding in both runners' findings files, and **both runners still exited 0** — exactly
the behaviour the bug asked for, and the behaviour that failed the suite at v2.24.7.
`python3 team/scripts/lint_gate_census_compliance.py` exits 0 (`36 file(s) checked, 0 offenders`).

### Finding 12: Governance budgets raised but the loader's Key Registry defaults were not

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated to one config key pair)
**Filed As:** BUG-0358 (Path C-A — filed as BUG: code defect / regression / test failure)
**Traces to:** bundled item BUG-0352-governance-byte-budget-over-tester-cm.

Met: the prose trim landed (`team/SOP-core.md`'s origin-boundary paragraphs collapse to one
paragraph plus a pointer at `git_boundary_guard.sh`'s header; `roles/TESTER.md` Step 6.12's
rating guidance collapses to a pointer at `docs/tester-verification-reference.md`), the trimmed
text is accurate against the implementation, Step 6.12 still names `git-boundary-guard.jsonl`
as its evidence source concretely, and `kanban.cfg_example` carries the raised values with the
reason in the inline comment. Measured:

| role | assembled | main budget | RC budget |
|---|---|---|---|
| tester | 103415 | 102400 → **OVER** | 110592 → under |
| cm | 86118 | 86016 → **OVER** | 94208 → under |

Not met: `team/scripts/lib/config_loader.sh`'s Key Registry still documents
`budget_bytes_tester 102400` and `budget_bytes_cm 86016`. The `doc_command_truth` key-parity
lint fires twice on this exact disagreement (see `## Lint Findings`), and the practical
consequence is larger than the lint: an install whose `kanban.cfg` does not set the two keys
resolves them from the loader's default, so the governance-budget lint would report `tester`
and `cm` over budget again on that install. The bug's own acceptance bullet — "No
`governance_byte_budget` entry appears in either runner's findings file on the following
release's verification run" — holds on this run only because the operator's `kanban.cfg` has no
`[governance]` section at all and every role therefore reports `unbudgeted`.

### Finding 13: The live-process guard the new worktree sweep depends on returned a false positive on every cycle it ran

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (recurs on every cycle; the sweep it gates is the framework's own self-healing path)
**Filed As:** BUG-0360 (Path C-A — filed as BUG)
**Traces to:** bundled item BUG-0353's acceptance bullet 5 ("leftovers from earlier cycles are
cleaned up by the reconciliation sweep rather than requiring an operator to run
`git worktree prune` by hand"), and Step 6.11 "state drift".

`reconcile_terminal_worktrees` skips any worktree whose `task_id` matches
`pgrep -f "$task_id"`, mirroring `reconcile_stale_active`'s own guard. Observed live:

- `CODER-20260920-018-task-worktree-teardown` has read `## State: DONE` since roughly
  17:26 on 2026-09-20, yet its `coder_backlog.md` marker is still `[A]` and its worktree is
  still registered in the dev tree.
- `logs/cron-coder.log` carries **61** occurrences of
  `stale-active: CODER-20260920-018-task-worktree-teardown has active process, keeping [A]`
  and **zero** occurrences of the `no active process` branch, from 17:26 through 20:38.
- No process matches that id now (checked from a script that does not itself contain the
  literal, to avoid the self-match this very guard's own test module documents).
- The match surface is wide by construction: `team/scripts/lib/provider_claude.sh:182` invokes
  `claude … -p "$prompt"`, so the entire dispatch prompt — which embeds the task id and the
  task's worktree path — sits in the provider process's `argv` and is visible to `pgrep -f`.

The two DONE tasks CODER-20260920-018's own status note predicted the new sweep would reclaim
(`CODER-20260920-006`, `TESTER-20260920-008`) are still registered. The live install is v2.24.6
and carries no sweep at all, so their survival is not itself evidence against the RC — but the
guard that has been answering "active process" for a finished task on 61 consecutive cycles is
the same guard the new sweep inherits, and while it answers that way the sweep cannot reclaim
the exact leftover it was written for.

### Finding 14: A shipped census row cites an internal bug identifier

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (one table cell in one document)
**Filed As:** BUG-0361 (Path C-A — filed as BUG)
**Traces to:** the `SOP-core.md` constraint "Naming: describe behavior, not scaffolding or
provenance" — "let no internal bug identifier, task ID, RC, or framework-version citation reach
a shipped artifact".

`docs/blocking-site-census.md` row 63, added by this RC, opens its condition cell with
`BUG-0354: a read-only leftover directory under …`. None of the three sanctioned exceptions
applies: it is not a usage example where a bug-shaped value illustrates an interface, not a skip
annotation citing an open follow-up, and not an external constraint. The same RC's
`docs/OPERATIONS.md` change uses `BUG-0348 ✋` legitimately — there the identifier is an example
*value* illustrating the card format.

No lint catches it: `lint_hygiene.py`'s prose surface defaults to `release-notes/` and does not
scan `docs/`, which is why a clean provenance run coexists with this citation.

### Finding 15: Gated suites, coverage, and the remaining methodology steps — pass

**Category:** pass

- **Pre-flight (Step 2).** All twelve prerequisite tasks read `## State: DONE`; every one of
  their `ai_feature/…` branches is absent locally, the normal post-merge shape, so both
  merge checks pass trivially. All twelve merge commits are present in
  `git log ai_main..ai_rc/v2.24.8` (29 commits, single author).
- **Working tree (Step 3).** `git rev-parse HEAD` = `git rev-parse ai_rc/v2.24.8` =
  `e13cdbd32a6ed079752914f1090b8cd62335b4c2`; `git status --porcelain` empty;
  `git stash list` empty.
- **Step 5a.** `team/scripts/run-unit-tests.sh --coverage` → `UNIT_EXIT=0`
  (4958 passed, 31 skipped, 838.84s). `team/scripts/run-integration-tests.sh --coverage` →
  `INTEGRATION_EXIT=0` (537 passed, 1 skipped, 586.79s). Both runners also reported
  `bare_tmp_cleanliness: OK`, `listener_cleanliness: OK`, `scratch_sweep: OK`.
- **Step 6.1.** `release-state.md` holds all four fields; `## Active RC` is `v2.24.8`,
  `## Last Released` is a well-formed `v2.24.7`, and `semver_gt v2.24.8 v2.24.7` (via
  `team/scripts/lib/semver.sh` against `pp_last_released_version`) is true.
- **Step 6.8.** Skipped with a note: the diff touches no release-lifecycle file
  (`cm-release.sh`, `cm-open-rc.sh`, `cm-cancel-rc.sh`, `lib/project_paths.sh`, `scripts/cm/`).
  `tests/integration/test_cm_release_synthetic.py` ran green in the integration suite regardless.
- **Step 6.9.** No hardcoded credential shape in the added lines. The two new sourced libraries
  carry no `set -euo pipefail`, which matches the house convention for sourced libs (72 of 79
  files under `team/scripts/lib/` omit it); the new executable migration script has it.
- **Step 6.10.** Prerequisite graph over the fourteen cycle tasks: no cycles, every referenced
  task folder exists, no sequence-number collisions, bookends linked
  (`CM-…-023-open-rc-2-24-8` → twelve work tasks → `TESTER-…-024-verify-2-24-8` →
  `CM-…-026-release-2-24-8`, currently `[W]`).
- **Step 6.13.** `team/scripts/lint_skip_bug_gate.sh` exits 0 —
  `0 missing files`, no placeholder shapes, all skip annotations compliant. Independent greps
  for `BUG-SKIP` and for skip-adjacent `BUG-NNNN` citations found only synthetic fixture IDs
  inside the gate's own test modules.
- **Step 6.14.** New script `migrate_v2.24.7_to_v2.24.8.sh` has a `--help` naming every
  argument. The new shim-adjacent file `wake_guard_wiring.sh` is a sourced library with no
  usage surface. No newly duplicated `lib/` implementation; no new hardcoded operational value.
  The one coding-standards hit is Finding 14.

### Finding 16: The requirements document's own three acceptance criteria — pass

**Category:** pass
**Traces to:** `requirements/v2.24.8-bugfix-bundle-20260920.md` `## Acceptance Criteria`.

- **"Each bundled item is addressed in the implementation."** All twelve are. Nine are met
  in full (Findings 2-9, 11); one is met with a caveat (Finding 10); two are addressed but
  incompletely (Findings 1 and 12). Every bundled item's own work landed on the RC as a
  `--no-ff` merge commit.
- **"All Python files compile cleanly."** All 17 changed `.py` files compile under
  `python3 -m py_compile`.
- **"All bash files pass syntax check."** All 17 changed `.sh` files plus the extensionless
  guard shim `team/scripts/wake/git-guard/git` pass `bash -n`.

## Test fidelity

The new test modules exercise real mechanisms rather than proxies:
`test_reconcile_terminal_worktrees.py` drives the real function against a real git repository
and real linked worktrees; `test_temp_sweep_permissions.py` seeds genuine `0555` directories;
`test_lint_bug_filer.py` drives real `git status --porcelain` against throwaway repositories.
Independent re-derivation of all three outside pytest reproduced their results.

One fidelity concern is concrete enough to be Finding 13 rather than a caveat: the live-process
guard in `reconcile_terminal_worktrees` is exercised in the unit test by a process the test
spawns with `exec -a` so `pgrep -f` sees it. That proves the guard fires when it should; it does
not exercise the production condition where `pgrep -f` matches an *unrelated* process because
the dispatch prompt carrying the task id sits in a provider process's `argv`.

## Lint Findings (non-blocking)

- `doc_command_truth` — key-parity: two `[governance]` keys documented in `kanban.cfg_example`
  disagree with `team/scripts/lib/config_loader.sh`'s Key Registry default —
  `budget_bytes_tester` (documented 110592, loader 102400, `kanban.cfg_example:459`) and
  `budget_bytes_cm` (documented 94208, loader 86016, `kanban.cfg_example:486`). Worst instance
  severity: quality. Unit suite only; the integration findings file carries no instance.
  — filed BUG-0358 (Path C-A), the same filing as Finding 12.
- `engine_size_dupe` — `lib/wake_task_state.sh` is 907 lines against the 800-line ceiling
  (`ai_main`: 794). Fired on both suites, one instance each. Worst instance severity: quality.
  Grepped `bugs/` first: BUG-0350 is `running` but names `lib/wake_driver_invoke.sh`, a
  different file this RC brought back under the ceiling, so it does not describe this finding.
  — filed BUG-0359 (Path C-A).

Neither entry changes `## Recommendation` or `## Systemic Risk` on its own; Finding 12's
elevation comes from the unconfigured-install consequence, not from the lint line.

## Coverage (informational only)

| suite | line % (statements) | branch % | blended (`TOTAL` Cover column) |
|---|---|---|---|
| unit | 76 % (12940/16945 statements) | 63 % (3229/5112 branches) | 73 % |
| integration | 11 % | 9 % | 11 % |

Read from `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/unit.json` and `.../integration.json`, field
names verified against the generated files (`percent_statements_covered`,
`percent_branches_covered`, `percent_covered`). Coverage did not enter the verdict.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=13d freshness=stale — Stale on both counts: the record is 13 days old against a 7-day window and was taken at v2.9.20 rather than the v2.24.8 under verification; the run that produced it filed BUG-0167-cold-build-claude-abliterated-failure (now `done`) for the `claude-abliterated` Dockerfile's malformed `FROM` and podman/rocky9's `COPY --chmod`, and nothing is known about whether this tree's images build today.

## Autonomous criterion

| # | Question | Answer |
|---|---|---|
| 1 | Tasks force-promoted WAITING → BACKLOG by hand? | NO |
| 2 | `release-state.md` edited outside the CM scripts? | NO |
| 3 | Scripts patched mid-build (a commit touching tooling while the RC was in flight)? | NO |
| 4 | Queue files edited by hand? | NO |
| 5 | A non-CM agent invoked a remote git operation? | NO |

`Autonomous operation criterion: Manual interventions required: none.`

Evidence. (1) Every WAITING→BACKLOG transition in `logs/cron-*.log` carries the wake script's
own `prerequisites satisfied, promoting to BACKLOG` line, and every dispatch its
`transitioned to WORKING [A]` line; four of the twelve tasks were promoted this way and the
rest were pulled straight from BACKLOG. (2) `release-state.md`'s mtime is 13:22 on 2026-09-20,
matching `## RC Opened At 2026-09-20T13:22:27+00:00` and `## RC Opened By Task
CM-20260920-023-open-rc-2-24-8`; it has not been touched since. (3) `git log --since` on
`ai_main` returns nothing after the RC opened; all 29 commits are on the RC branch and are the
cycle's own deliverables. (4) Queue markers for all fourteen cycle tasks use the canonical
shapes and mtimes track wake activity.

(5) is recorded as NO with a stated limitation: the operator's install is **v2.24.6** and
carries no `scripts/wake/git-guard/`, no `scripts/lib/git_boundary_guard.sh`, and no
`scripts/lib/wake_guard_wiring.sh`, so no `git-boundary-guard.jsonl` can exist anywhere under
`tasks/` — and `find` confirms none does. The answer is therefore "no record exists" rather than
"the guard observed no attempt". This is a property of the install, not a defect in the RC, and
it resolves itself once v2.24.7-or-later is installed.

One process observation that is **not** an intervention and is recorded here for completeness:
`CODER-20260920-018-task-worktree-teardown` finished without its chain's finalize stage running
(the lock-loss reverification path that bundled item BUG-0353 documents), leaving its queue
marker at `[A]` and its worktree registered. Nothing was force-corrected by hand — the drift is
still on disk, which is precisely why it is reportable. It is carried as Finding 13.

## Caveats

- **The engine-size lint's own fix is demonstrated by the RC's own regression.** It is worth
  stating plainly that the RC both introduced a new over-ceiling engine file and shipped the
  change that keeps such a file from failing the suite. Those are the right two outcomes in the
  wrong order for comfort, but the census class is unambiguous — `SOP-core.md` ("Blocking
  Severity: only real damage blocks") makes size heuristics CONVERT by nature — so the suite
  exiting 0 on a 907-line file is correct behaviour, not a gate that was tuned away.
- **`pgai_temp_cleanup_tests`' dangerous-root guards are unreachable through the environment.**
  `pgai_temp_dir()` normalizes an empty value and `/` to `/tmp/pgai_kanban_tmp` before the
  guards see them, so only the literal `/tmp` reaches the refusal (verified: exit 1). The
  guards are correct defence-in-depth against a future resolver change; this is an observation
  about a pre-existing resolver, not a finding against this RC, and `pgai_temp_dir` is untouched
  by the diff.
- **BUG-0356-hygiene-lint-provenance is `open` in `bugs/`** and lists eleven `BUG-0344`
  provenance citations in `event-band.sh` and `test_event_band_renderer.py`. Every one of them
  is gone from the RC (commit `c5f4492` reworded them) and `lint_hygiene.py --classes
  provenance` is clean, so the bug describes a working-copy state that no longer exists — the
  exact class of phantom filing bundled item BUG-0346 was raised to stop. It predates the gate
  landing. TESTER does not modify filed bugs beyond factual corrections, so it is noted here for
  PM's triage rather than edited.
- **The event-band measurements are host-load sensitive.** They were taken on a host carrying
  a load average of 1.0-2.4 with other agents active. The `ai_main` baseline and the RC runs
  were interleaved against the identical fixture to keep the comparison fair, and the RC's
  margin (roughly 7x) is far outside any plausible noise band.
