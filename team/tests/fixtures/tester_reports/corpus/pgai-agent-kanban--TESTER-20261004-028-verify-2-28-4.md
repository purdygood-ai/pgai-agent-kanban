# Verification Report: rc/v2.28.4

## Summary
I verified RC `ai_rc/v2.28.4` (HEAD ff234a52b8ac734ff74e26fb72fa61f1ae93f5cf) against
`requirements/v2.28.4-bugfix-bundle-20261004.md`. That bundle carries BUG-0541
(package CLI modules crash when run by file path; the regression test should
derive its module set) and BUG-0540 (the ICD support table is missing 1.13.1;
hygiene ride-along). Three tasks landed on the RC: CODER-20261004-024 (merge
4d813144), CODER-20261004-025 (merge 1024f530) and WRITER-20261004-026 (merge
ff234a52).

Every acceptance criterion in both bundled bugs and in the requirements doc is
met, and I exercised each one. Both gated runners exited 0: unit 7908 passed /
43 skipped, integration 646 passed / 1 skipped. Neither produced any lint
findings.

The Step 6.14 coding-standards spot-check found one pre-existing problem. Five
package CLIs exit non-zero on `--help`, and the RC's new test pins those codes
as expected. I filed it as BUG-0543 via Path C-A (autonomous follow-up filing,
recorded as a BUG; it does not block). Under Step 8, any filed bug sets the
recommendation to at least SHIP-WITH-CONCERNS.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
low

## Findings

### Finding 1: Pre-flight — RC intact, worktree clean (Steps 2-3)

**Category:** pass

- Step 2: I scanned 572 DONE task folders with a named feature branch. Only
  four local branches exist (ai_main, ai_rc/v2.28.4, last_run_ai_main, main).
  Every task feature branch was deleted after merge, so Check 1 and Check 2
  pass trivially. The three v2.28.4 merges are on the RC.
- Step 3: `git rev-parse HEAD` equals `git rev-parse ai_rc/v2.28.4` (ff234a52).
  `git status --porcelain` is empty and `git stash list` is empty. I re-checked
  after the suites ran: still clean, same HEAD.

### Finding 2: Gated suites (Steps 5a, 6.6, 6.7)

**Category:** pass

- `bash team/scripts/run-unit-tests.sh --coverage`: **UNIT_EXIT=0**. 7908
  passed, 43 skipped (1731.75s), "Unit tests PASSED". The green ledger
  recorded exit 0 at ff234a52.
- `bash team/scripts/run-integration-tests.sh --coverage`: **INTEGRATION_EXIT=0**.
  646 passed, 1 skipped (1033.64s), "Integration tests PASSED".
- PASS evidence is these two runner exit codes, not a pytest line. Logs:
  `/tmp/pgai_kanban_tmp/tester/TESTER-20261004-028/unit.log` and `integration.log`.
- Both new or changed test files ran green in the certification run:
  `test_lint_icd_compat.py` and `test_package_modules_run_by_path.py`.
- In the pre-flight, `lint_icd_compat` reported both "compatible superset of
  all 16 baselines" and "support table ... mirrors SUPPORTED".
- Coverage is informational only (from the `totals` objects; I checked the
  keys on the generated JSON):
  - unit: line (statements) 80.27%, branch 66.33% (blended 77.16%);
  - integration: line (statements) 14.09%, branch 9.91% (blended 13.16%).

### Finding 3: BUG-0541 AC1 — named modules run by path

**Category:** pass

I ran these from `/` with `PYTHONPATH` unset and a sandboxed kanban root:

- `deep_verify/schedule.py` prints its usage line and exits 2.
- `deep_verify/freshness.py` prints its usage line and exits 2.
- `reports/project_summary.py --help` exits 0.
- None of them prints a traceback.

The original failure reproduces on base `ai_main` (a45a3e9e): schedule.py,
freshness.py, project_summary.py and task_status.py all exit 1 there with
`No module named 'pgai_agent_kanban'`. The `-m` forms of the three modules
behave the same as the by-path forms (2/2/0).

### Finding 4: BUG-0541 AC2 — sweep finds only allow-listed modules

**Category:** pass

I ran the sweep from the bug's Reproduction step 4: every
`team/pgai_agent_kanban/**/*.py` outside `tests/` that has a `__main__` block
and a top-level package import, run as `--help` from `/`. That is 26 modules.
25 run with no traceback. The only `No module named` hit is `ops/write.py`.
It is on `_PACKAGE_ONLY_MODULES`, and its text uses only
`python3 -m pgai_agent_kanban.ops` forms.

### Finding 5: BUG-0541 AC3 — test goes red without the guard (behavioural, Step 6.5)

**Category:** pass

I ran this in an isolated clone at ff234a52, not in the verified worktree.

- Baseline: `run-unit-tests.sh --files tests/unit/test_package_modules_run_by_path.py`
  exits 0 (88 passed).
- I removed the guard from `deep_verify/freshness.py`, which is a different
  module from the CODER's own evidence. The run exits 1 with 2 failed:
  `test_help_by_file_path_runs_without_pythonpath[...freshness]` and
  `test_file_load_binds_the_package_of_its_own_tree[...freshness]`.
- I added a new unguarded, non-allow-listed CLI module,
  `reports/tester_probe_cli.py`. Discovery picked it up automatically, and the
  run exits 1 with 2 failed.

### Finding 6: BUG-0541 AC4/AC5 — targeted run green, no dev-tree sys.path

**Category:** pass

- AC4: the targeted runner exits 0 (Finding 5 baseline), and the full unit
  suite also exits 0.
- AC5: the only added `sys.path` code is the own-tree guard
  `pathlib.Path(__file__).resolve().parents[N]`, in 14 modules.
  `task_status.py` correctly uses `parents[1]` because it is a top-level
  module. No added line references a dev tree, a worktree or `PGAI_DEV_TREE*`.
  Every guarded module imports `pathlib`.

### Finding 7: BUG-0540 AC1/AC2 — support table mirrors SUPPORTED

**Category:** pass

- The AC1 `diff <(grep ... docs/operator-api.md) <(grep ... SUPPORTED)` prints
  nothing.
- The row reads `| 1.13.1 | supported | v2.28.2 |`. `ICD_VERSION` is 1.13.0 at
  tag ai_v2.28.1 and 1.13.1 at ai_v2.28.2, so the Since value is right.
- The WRITER also added `| 1.14.0 | supported | v2.28.3 |`. That is correct:
  1.14.0 is in SUPPORTED and `ICD_VERSION` is 1.14.0 at ai_v2.28.3.

### Finding 8: BUG-0540 AC3 — changelog rule and bump procedure agree

**Category:** pass

- `ICD_CHANGELOG.md` now says "One entry per bump, patch bumps included" and
  gives a single rule for description-only patch bumps.
- Step 4 of `operator-api.md` points to that rule instead of restating it.
- No surviving text says description-only edits are not recorded.
- The new 1.13.1 entry matches a structural diff of `icd-v1.13.0.json`
  against `icd-v1.13.1.json`: only `info.version` and the `GET /show-queues`
  description changed.

### Finding 9: BUG-0540 AC4 — doc lints clean

**Category:** pass

Each of these exits 0:
- `check_docs_citations.py`
- `lint_doc_command_truth.py`
- `generate_operator_verb_docs.py --check`
- `lint_icd_compat.py`

### Finding 10: Support-table mirror check fires on drift (behavioural, Step 6.5)

**Category:** pass

I fed synthetic copies of `operator-api.md` to `lint_icd_compat.py` via
`--support-doc`. Every drift kind fired, and the two allowed cases stayed
quiet:

| Synthetic change | Exit | What it reported |
|---|---|---|
| 1.13.1 row removed | 1 | `missing` |
| 9.9.9 row added | 1 | `extra`, correct line number |
| 1.0.0 marked `retired` | 1 | `retired`, correct line 2350 |
| heading renamed | 1 | `no support table found` |
| retired row whose version is absent from SUPPORTED | 0 | nothing (correct) |
| version written as `` `v1.13.1` `` | 0 | nothing (normalized correctly) |

The runners call it through `gated_runner_run_quality_lint`, which always
returns 0. That makes it CONVERT, which matches the bug's "quality-class
(non-blocking)" authorization and census row 33. No hard gate was added
without authorization.

### Finding 11: Requirements-doc ACs — compile and syntax

**Category:** pass

- 663 tracked `.py` files compiled in memory, with 0 failures.
- 316 tracked `.sh` files pass `bash -n`, with 0 failures.

### Finding 12: Step 6 methodology checks

**Category:** pass

- 6.1: `pp_last_released_version` returns v2.28.3, and `semver_gt v2.28.4
  v2.28.3` is true. `## Last Released` is well-formed.
- 6.2: all 20 files in the diff exist and the runners are executable.
- 6.3: I read the new and changed regions in full, including the doc-layer
  edits.
- 6.8: the diff touches no release-lifecycle files, so I skipped the synthetic
  release simulation.
- 6.9: no secret-shaped literals were added. The version-shaped strings that
  were added are docstring usage examples only, which is a sanctioned
  exception.
- 6.10: the prerequisite graph is CM-027 → CODER-024/025 → WRITER-026 →
  TESTER-028 → CM-030. It has no cycles, and every ID exists. Sequence 029 is
  unused, the same as the approval slot in earlier cycles (015, 022).
- 6.13: there are no real placeholder skips. Six unresolved `BUG-` IDs appear
  near "skip", but all are synthetic fixture strings inside lint tests or
  assertion text, not skip annotations.

### Finding 13: Five package CLIs answer `--help` with a usage error, and the new test pins it

**Category:** bug (pre-existing)
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated to five internal helper CLIs and one test table)
**Filed As:** BUG-0543 (Path C-A — filed as BUG)
**Filed By:** TESTER (autonomous Path C) — five package command-line helpers exit with a usage error instead of printing their help and exiting 0, and the new test records that as expected.

Five modules exit non-zero when asked for `--help`:

| Module | `--help` exit |
|---|---|
| `deep_verify/bug_filer.py` | 2 |
| `deep_verify/freshness.py` | 2 |
| `deep_verify/schedule.py` | 2 |
| `lint/bug_filer.py` | 2 |
| `ops/resolve.py` | 1 |

`ops/resolve.py` prints only `resolve_item: PROJECT_ROOT and KEY arguments are
required`, with no usage text. This breaks `docs/coding-standards.md`
directive 6 ("every script and CLI entry point ships a `--help` that describes
every argument") and its "A help path prints completely and exits 0" section.

It is pre-existing: the same codes come back on `ai_main` via `python3 -m`.
What this RC adds is that `test_package_modules_run_by_path.py` now records the
five codes as expected in `_HELP_USAGE_ERROR_EXIT`. The CODER disclosed this.
Nothing in the running system calls `--help` on these helpers, because shell
wrappers call them with positional arguments. That makes severity `low` under
"Rating a Finding". The no-argument exit codes (2/2/2/2/1) must stay
unchanged; BUG-0543 spells this out.

## Lint Findings (non-blocking)

None. `findings-unit.json` and `findings-integration.json` both held 0
findings (`schema_version` 1).

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=27d freshness=stale — The latest deep-verify record is 27 days old (outside the 7-day window) and was taken against v2.9.20 rather than v2.28.4, its failed-to-build result for docker/podman claude-abliterated and podman/rocky9 was already filed by that run as BUG-0167 (now done), and nothing is known about whether this RC's images build.

## Autonomous criterion

1. Were any tasks force-promoted by hand from WAITING to BACKLOG? **NO.** The
   batch logs show the wake script promoted WRITER-026 (14:19:03) and
   TESTER-028 (14:27:03) with "prerequisites satisfied".
2. Was `release-state.md` edited outside the CM scripts? **NO.** Its mtime
   (13:47:05) matches CM-027's `open-rc.sh` run, and the per-RC
   `v2.28.4.json` was written in the same second.
3. Were scripts patched mid-build? **NO.** No live-install file under
   `scripts/`, `pm-agent/`, `roles/` or the root `*.md` changed after the RC
   opened. The only `team/scripts/` change on the RC is CODER-025's scoped
   deliverable, `lint_icd_compat.py`.
4. Were queue files edited by hand? **NO.** The queue markers match task
   states (`[x]` DONE, `[A]` this task, `[W]` CM-030), and the mtimes line up
   with wake activity.
5. Did a non-CM agent run a remote git operation? **NO.** None of this
   cycle's task folders (PM-004, CM-027, CODER-024/025, WRITER-026,
   TESTER-028, CM-030) contains a `logs/git-boundary-guard.jsonl`.

Autonomous operation criterion: Manual interventions required: none

## Caveats

- **Why the recommendation is not PASS.** It is SHIP-WITH-CONCERNS only
  because of Finding 13, a pre-existing low-severity coding-standards bug that
  the RC's new test now pins. Every bundled acceptance criterion passes and
  both suites are green.
- **Allow-list rationale is inconsistent (observation, not filed).**
  `ops/write.py` was allow-listed as package-only because
  `ops/__init__.py` imports it eagerly, so a by-path run would load a second
  copy. `ops/__init__.py` (line 46) imports `ops/resolve.py` eagerly too, yet
  `resolve.py` was given the guard instead. A by-path run of `resolve.py`
  passes all three test properties, and nothing breaks today.
- **Discovery coverage note (CODER disclosure, confirmed by reading).** The
  test discovers modules that carry the guard as well as CLI modules.
  Removing the guard from a non-CLI module such as `lib/agent_metrics.py`
  therefore drops it from the test instead of failing it. This is outside
  BUG-0541's acceptance, which covers only `__main__` plus package-import
  modules.
- **Bump procedure step 5 is incomplete (WRITER disclosure, observation).**
  Step 5 in `operator-api.md` lists what to commit together but leaves out the
  support-table row. The new mirror check catches that omission on the next
  gated run.
- **Pre-existing manifest-reader gap (CODER disclosure, not exercised).**
  `check_compat()` reads every non-blank line of SUPPORTED as a version, so a
  `#` comment line would be reported as a missing baseline. SUPPORTED has no
  comment lines today, and the RC does not change this code.
- **Deep-verify is off for this project.** `project.cfg` sets
  `[release] deep_verify = false` for this project, which is why the ledger is
  stale. TESTER never runs the container tier. This is for the operator.
- **No commit to the dev tree.** I committed nothing. The wake prompt forbids
  creating a feature branch, and Step 9 forbids committing process artifacts
  into a dev tree. The artifacts live in the task folder only.
- **Earlier checkpoint text.** The checkpoint sections written during the run
  are folded into the findings above. A copy is kept at
  `/tmp/pgai_kanban_tmp/tester/TESTER-20261004-028/report.checkpoints.md`.
