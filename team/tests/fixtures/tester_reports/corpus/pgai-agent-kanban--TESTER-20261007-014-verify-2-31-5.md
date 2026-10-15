# Verification Report: rc/v2.31.5

## Summary

I verified RC `ai_rc/v2.31.5` (HEAD 337b3784) against `requirements/v2.31.5-bugfix-bundle-20261007.md`, which bundles BUG-0593 (the doc-lint command reader drops bash blocks that follow a tagged fence) and BUG-0594 (a registry spacing slip hides `lock_starvation_firings` from the doc-truth lint, and the OPERATIONS `[wake]` table lacks the row). The bundle was implemented by three tickets: CODER-20261007-010, CODER-20261007-011 and WRITER-20261007-012.

Every functional acceptance criterion of both bugs is met, and I re-ran each one:
- the fence reproduction now prints exactly one command;
- `extract_blocks()` returns exactly the six previously dropped blocks on the audited docs, confirmed against an independent CommonMark pairing;
- the doc-lint cmd suite's runnable result is unchanged, with only the SKIP count rising (125 to 135);
- a harness-level probe shows the false green on ai_main is gone on the RC;
- the doc-truth lint is at 0 findings, and fault injection proves it reads both the registry line and the new table row.

Both gated runners exited non-zero, so PASS is not available:
- **`run-unit-tests.sh` (exit 1)** fails because of a regression this RC introduced. `doc_commands.py` gained a package import without the run-by-file-path self-location guard. The module's production consumer (`doc-lint.sh`, which uses `-m`) is unaffected.
- **`run-integration-tests.sh` (exit 1)** fails for a pre-existing reason. Five wake-lock tests inherit `LOCK_WAIT_SECONDS=120` from the live v2.31.4 wake's environment. The same failures occur on ai_main, and the tests pass on the RC once the variable is unset.

Both defects are filed via Path C (autonomous follow-up filing — non-blocking) as Path C-A (filed as BUG — code defect / regression / test failure). Neither is severe, so the recommendation is SHIP-WITH-CONCERNS.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Findings

### Criteria results

| # | Check (trace) | Result | Evidence |
|---|---|---|---|
| 1 | Step 2 pre-flight: DONE tasks' feature branches merged and contributing | pass | 647 DONE tasks name a feature branch; none still exists locally, which is the normal post-merge shape. The three RC merges are ca1e8362, be55dd10 and 337b3784 on ai_rc/v2.31.5 |
| 2 | Step 3 worktree identity / clean / no stash | pass | HEAD 337b3784 == ai_rc/v2.31.5; porcelain empty; stash list empty |
| 3 | BUG-0593 AC: reproduction prints exactly one command | pass | RC: `1\tfalse\tfalse\tfalse\techo real-command`, exit 0. ai_main: no output, exit 0 (original failure reproduced) |
| 4 | BUG-0593 AC: six dropped blocks returned, prior blocks unchanged | pass | old->new blocks/commands: README 2/11->3/12 (+255); HOW_TO 24/60->27/67 (+277, +736, +1017); docker.md 19/24->21/26 (+360, +721); both demo READMEs unchanged. difflib shows pure insertions, all block_skip=True, 10 commands. Counts match my independent pairing |
| 5 | BUG-0593 AC: no private `(bash\|sh)?` fence regex outside tests | pass | `grep -rn "(bash\|sh)?" team --include=*.py` minus team/tests/: no hits |
| 6 | BUG-0593 AC: doc-lint cmd result unchanged for runnable commands | pass | sandboxed: ai_main `files=5 pass=1 skip=125 fail=0` exit 0; RC `files=5 pass=1 skip=135 fail=0` exit 0; diff = 10 SKIP-BLOCK lines |
| 7 | BUG-0593 ticket AC: unit tests pin tagged / indented / 4-backtick / unlabelled cases | pass | `team/tests/unit/test_doc_commands.py` (10 cases) passed in the full run. Against the ai_main reader 9 of 10 fail; the one that passes is the preserved unlabelled rule (fidelity confirmed) |
| 8 | Step 6.5 behavioural: false-green guard through the harness | pass | doc root with an un-annotated failing bash block after an `ini` block: ai_main `pass=0 fail=0` exit 0 (false green); RC exit 1 |
| 9 | Step 6.5 synthetic edge cases vs ai_main (no regression) | pass | 9 cases (inline skip, continuation, unterminated bash, docker gate, annotation-then-prose, tilde/4-backtick bash, sh, comments): identical except `shell`-then-bash, where ai_main dropped the bash block (the bug class itself) |
| 10 | BUG-0594 AC: registry line spacing | pass | `{2,}` grep matches config_loader.sh:205; single-space grep empty; `bash -n` OK; diff is one comment line |
| 11 | BUG-0594 AC: doc-truth lint 0 findings, key counted | pass | RC exit 0, `191 key citation(s) ... against 118 registry key(s); 0 finding(s)`; ai_main exit 1 (`UNKNOWN CONFIG KEY`) |
| 12 | BUG-0594 AC: `[gated-runner] doc_command_truth: OK` | pass | unit runner pre-flight line 73 |
| 13 | Step 6.5 fault injection on lint (scratch clone) | pass | row default set to 4: `CONFIG DEFAULT MISMATCH`, exit 1. Single space restored: 117 keys, 2 `UNKNOWN CONFIG KEY`, exit 1 |
| 14 | WRITER AC: row present, default 3, `0` turns guard off, points at section, agrees with cfg example | pass | OPERATIONS.md:1176 agrees with kanban.cfg_example:442-454 and "Repo lock and contention" prose (line 2602) |
| 15 | WRITER AC: only the added row changed | pass | `git diff ai_main..rc -- docs/OPERATIONS.md` = 1 insertion |
| 16 | Requirements AC: all Python files compile | pass | py_compile on 809 tracked .py files, 0 failures |
| 17 | Requirements AC: all bash files pass syntax check | pass | `bash -n` on 332 tracked .sh files, 0 failures |
| 18 | Step 6.1 release-state sanity | pass | Active RC v2.31.5; Last Released v2.31.4 (well-formed); `semver_gt v2.31.5 v2.31.4` true via `pp_last_released_version` |
| 19 | Step 6.8 synthetic release simulation | n/a | diff touches no release-lifecycle file |
| 20 | Step 6.9 constraint negatives | pass | no secrets or provenance tokens (BUG-/task IDs/versions) in added lines; config_loader change is comment-only |
| 21 | Step 6.10 prereq graph | pass | CM-013 -> CODER-010, CODER-011 -> WRITER-012 (also on CODER-011) -> TESTER-014 -> CM-016; no cycles; all IDs exist; the 015 gap mirrors the prior cycle (no approval task, approval `auto`) |
| 22 | Step 6.13 skip-cites-real-bug | pass | no skip decorator or `pytest.skip(` call cites a BUG ID; BUG-SKIP/BUG-1234-style hits are fixtures of the gate's own tests; pre-flight `skip_cites_real_bug: OK` |
| 23 | Step 6.14 coding-standards spot-check | pass | no new scripts; docstrings describe behaviour; no provenance |
| 24 | Step 5a/6.6 `run-unit-tests.sh` exit 0 | **fail** | `UNIT_EXIT=1`; see Finding 1 |
| 25 | Step 5a/6.7 `run-integration-tests.sh` exit 0 | **fail** | `INTEGRATION_EXIT=1`; see Finding 2 |

Totals: 25 checks: 22 pass, 1 not applicable, 2 fail (two findings, both filed).

### Finding 1: doc_commands.py crashes when run by file path (unit runner red)

**Category:** bug
**Fix Effort:** small (1 CODER task)
**Systemic Risk:** medium (a recurrence of the BUG-0533 / 0537 / 0541 / 0561 / 0575 class; the package-wide contract test caught it, but the ticket's targeted `--files` run did not include that test)
**Regression:** yes
**Integrity:** breakage
**Filed As:** BUG-0595-doc-commands-crashes-by-path (Path C-A)
**Filed By:** TESTER (autonomous Path C) — the doc-lint command reader now imports the shared fence helper from its own package without the self-location guard, so starting it by its file path crashes with "module not found" and the unit runner is red.

`run-unit-tests.sh --coverage` exited 1. The lint pre-flight was clean, and pytest gave 2 failed, 10145 passed, 41 skipped (plus 5 serial passes). Both failures are `tests/unit/test_package_modules_run_by_path.py` parametrised on `pgai_agent_kanban.lint.doc_commands`:
- `test_help_by_file_path_runs_without_pythonpath`
- `test_file_load_binds_the_package_of_its_own_tree`

Both raise `ModuleNotFoundError: No module named 'pgai_agent_kanban'` at doc_commands.py:54, the import this RC added (`from pgai_agent_kanban.lib.markdown_fence import ...`). The package's other by-path modules carry an `if not __package__:` sys.path guard for this case (for example `lint/bug_filer.py:215`); this module does not.

Regression: on ai_main, `env -u PYTHONPATH python3 team/pgai_agent_kanban/lint/doc_commands.py --help` exits 0. On the RC it exits 1. A targeted `--files tests/unit/test_package_modules_run_by_path.py` reproduces the result (2 failed, 138 passed).

Impact: the production caller `doc-lint.sh` starts the module with `-m` and works (row 6), and the module's documented usage is the `-m` form, so nothing in the shipped system depends on the broken path today. That makes it medium on the "Rating a Finding" rubric rather than high, consistent with the five earlier instances of this class (all rated medium). The cost is a red gated unit runner.

### Finding 2: wake-lock integration tests inherit the live wake's LOCK_WAIT_SECONDS (integration runner red, pre-existing)

**Category:** bug (pre-existing)
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (deterministic in every agent-run verification until fixed; nothing outside the test harness is affected)
**Regression:** no
**Integrity:** breakage
**Filed As:** BUG-0596-lock-tests-inherit-wake-env (Path C-A)
**Filed By:** TESTER (autonomous Path C) — five wake-lock integration tests fail whenever the suite runs inside an agent wake, because the live wake exports its lock-wait setting and the tests' sandboxed wakes obey that inherited value instead of their own configuration.

`run-integration-tests.sh --coverage` exited 1. The lint pre-flight was clean, and pytest gave 5 failed, 941 passed, 1 skipped. The failures:
- `test_wake_contention_retry.py`: three tests
- `test_chain_lock_held_through_agent.py`: `test_lock_held_through_agent_sibling_defers_and_losing_chain_abandons`
- `test_single_truth_state_integrity.py`: `TestContendedProjectRetriedSameIteration`

Four fail with 60 s `TimeoutExpired`. The fifth fails because the contending chain logs `waiting up to 120s ... (lock_wait_seconds=120 ...)` where the test pinned `lock_wait_seconds = 0`.

Root cause, established by experiment:
- The agent environment carries `LOCK_WAIT_SECONDS=120` and `LOCK_STARVATION_FIRINGS=3`, exported by the live v2.31.4 wake's config load.
- `_config_loader_export_wake_integer` gives a set environment variable precedence over the cfg value. That precedence is the documented operator override.
- Neither the gated runner nor these tests scrub the variables.

Evidence:
- The same three files on the RC: 5 failed, 5 passed (deterministic).
- On the ai_main clone: 5 failed, 5 passed (identical, so pre-existing).
- On the RC with `env -u LOCK_WAIT_SECONDS -u LOCK_STARVATION_FIRINGS`: 10 passed, exit 0.

The same tests passed in the v2.31.4 verification because the live install then was v2.31.3, which did not export the variable. Nothing in this RC touches lock code; its only config_loader change is one comment line.

This finding does not stop this release on its own merits. But until it is fixed, every agent-run verification will report `INTEGRATION_EXIT=1`, and a PASS recommendation is impossible.

## Lint Findings (non-blocking)

None. `findings-unit.json` and `findings-integration.json` each record 0 findings for this run.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=30d freshness=stale — The latest deep-verify record is a red cold build from 30 days ago against v2.9.20, outside the 7-day window and not this RC's version; the run that produced it filed BUG-0167-cold-build-claude-abliterated-failure (now done), and nothing is known about whether the current docker/debian and docker/rocky9 images build.

## Autonomous criterion

1. Tasks force-promoted WAITING to BACKLOG by hand: **NO**. Every promotion in this cycle is logged by the wake script: CODER-010/011 at 11:08:04, WRITER-012 at 11:19:12, TESTER-014 at 11:27:04 ("prerequisites satisfied, promoting to BACKLOG").
2. `release-state.md` edited outside the CM scripts: **NO**. Its mtime (11:05:03) equals `## RC Opened At`, written by CM-20261007-013.
3. Scripts patched mid-build: **NO**. The live-install files changed at 11:04 are the v2.31.4 install, before the RC opened. Later changes are only `__pycache__` bytecode. The RC's only `team/scripts/` change is ticket CODER-011's comment line.
4. Queue files edited by hand: **NO**. Markers agree with task states (`[x]` for the DONE tickets, `[A]` for this task, `[W]` for CM-016).
5. Remote git operation by a non-CM agent: **NO**. No `git-boundary-guard.jsonl` exists in any of this cycle's seven task folders. My own version lookup skipped the fetch: `PGAI_GIT_LOCAL_ONLY=1` and role tester.

Autonomous operation criterion: Manual interventions required: none

## Caveats

- **Coverage:** unavailable. Both runners printed `coverage disabled by config ([testing] coverage)`. The JSON files under `/tmp/pgai_kanban_tmp/coverage/` are dated 2026-10-04 and do not describe this run, so I did not quote them.
- **Observations, not findings (Step 6.11, pre-existing, outside this bundle's acceptance grep):**
  - `team/pgai_agent_kanban/overwatch/intent.py:83` keeps a backtick-only fence regex (`^\s*````).
  - `team/scripts/lint_doc_command_truth.py:1333` `_KEY_FENCE_OPEN_RE` accepts only single-word info strings, so ```` ```ini title=x ```` would be misread. I measured it: across every tracked `.md` file it misses zero fence openers today, so the problem is latent.
  - `lint_changelog_freshness.py`'s bare-backtick regex classifies diff lines and is not a block reader.
  - None of these matches the BUG-0593 acceptance grep (`(bash|sh)?`), so none is filed. If a future bundle completes the "one fence reader" migration, those are the remaining sites.
- **Recurring class (Finding 1):** this is the sixth filed instance of a package module crashing when run by path (BUG-0533, 0537, 0541, 0561, 0575, now 0595). Each instance was caught by `test_package_modules_run_by_path.py` in the full TESTER run and missed by the ticket's targeted `--files` list. A structural remedy is a PM/operator decision and is not filed here, for example having CODER briefs that add a package import to a package module list that test file. I am noting it for human consideration.
- **CM-facing note (Finding 2):** this finding and Finding 1 are independent. Once BUG-0596 lands, an agent-run integration certification should be green again; until then, read `INTEGRATION_EXIT=1` alongside this report's root cause.
- **Deep-verify:** no container tier was invoked by this verification (Step 5d).
- **Process:** per the wake prompt and the never-commit-process-artifacts rule, I created no feature branch and committed nothing to the dev tree. Artifacts live in the task folder only.
- Logs and evidence: `/tmp/pgai_kanban_tmp/tester/v2.31.5/`, including:
  - full-suite logs: `unit.log`, `integration.log`, `*.exit`;
  - doc-lint outputs: `doclint-cmd-main.txt`, `doclint-cmd-rc.txt`;
  - targeted integration reruns: `int-targeted-rc.log`, `int-targeted-main.log`, `int-targeted-rc-unset.log`;
  - `byPath-targeted.log`.

## Checkpoint log
- Pre-flight (Step 2): 647 DONE tasks with a named feature branch; 0 still exist locally, so Check 1/Check 2 pass trivially. The three RC tickets' merges (ca1e8362, be55dd10, 337b3784) are on ai_rc/v2.31.5. PASS.
- Worktree (Step 3): HEAD 337b3784 == ai_rc/v2.31.5; `git status --porcelain` empty; `git stash list` empty. PASS.
- Diff (Step 4): ai_main (357e9dbf)..ai_rc/v2.31.5 touches 4 files: docs/OPERATIONS.md (+1), team/pgai_agent_kanban/lint/doc_commands.py, team/scripts/lib/config_loader.sh (1 comment line), team/tests/unit/test_doc_commands.py (new).
- BUG-0593 AC repro: RC prints exactly `1\tfalse\tfalse\tfalse\techo real-command`, exit 0; ai_main prints nothing (original failure reproduced). PASS.
- BUG-0593 AC audited docs: old->new blocks/commands README 2/11->3/12 (+255), HOW_TO 24/60->27/67 (+277,+736,+1017), docker.md 19/24->21/26 (+360,+721), demos unchanged; pure insertions, all block_skip=True; matches independent reference pairing. PASS.
- BUG-0593 AC grep `(bash|sh)?` outside tests: no hits. PASS.
- BUG-0593 AC doc-lint cmd: ai_main pass=1 skip=125 fail=0 exit 0; RC pass=1 skip=135 fail=0 exit 0; diff = 10 SKIP-BLOCK lines only. PASS.
- Step 6.5 harness guard: un-annotated failing bash after ```ini -> ai_main exit 0 (false green), RC exit 1. PASS.
- BUG-0594 ACs: {2,} grep matches line 205; single-space grep empty; bash -n OK; lint RC exit 0, 0 findings, 118 registry keys, 191 citations; ai_main exit 1. Fault injection (default 4 in row; single space restored) both caught. PASS.
- WRITER row wording agrees with kanban.cfg_example:442-454 and OPERATIONS "Repo lock and contention" prose. PASS.
- Requirements generic ACs: py_compile 809 files 0 fail; bash -n 332 files 0 fail. PASS.
- 6.1 release-state: Active RC v2.31.5; Last Released v2.31.4 well-formed; semver_gt v2.31.5 > pp_last_released_version (v2.31.4). PASS.
- 5d: tree ships docker/debian, docker/rocky9 Dockerfiles; team/scripts/deep-verify.sh present; ledger latest found=true verdict=failed-to-build v2.9.20 d37bb68 2026-09-07T06:52:06+00:00 (age 30d, stale). Cites BUG-0167 (done).
- 6.10 prereq graph: CM-013 open -> CODER-010, CODER-011 -> WRITER-012 (also on CODER-011) -> TESTER-014 -> CM-016 release. No cycles, all IDs exist. PASS.
- 6.12: guard jsonl none in all 7 cycle task folders; promotions all by wake script; release-state mtime 11:05:03 == RC Opened At; live scripts changed 11:04 (v2.31.4 install, pre-RC) + pycache only; queue markers consistent. All NO.
- 6.13: no real skip annotation cites a BUG ID; synthetic IDs are fixtures; runner pre-flight skip_cites_real_bug OK. PASS.
- 6.14: no new scripts; no provenance tokens in added lines. PASS.
- Unit pre-flight: `[gated-runner] doc_command_truth: OK`; findings-unit.json 0 findings.
- Step 5a unit: UNIT_EXIT=1. Lint pre-flight all OK (findings-unit.json 0 findings); pytest 2 failed / 10145 passed / 41 skipped (parallel) + 5 passed (serial). Failures: test_package_modules_run_by_path.py x2 [pgai_agent_kanban.lint.doc_commands], ModuleNotFoundError from the RC's new package import. Regression confirmed vs ai_main. Filed BUG-0595 (Path C-A). Targeted rerun reproduces (2 failed, 138 passed).
- Step 5b: coverage unavailable (runner printed "coverage disabled by config ([testing] coverage)"; /tmp/pgai_kanban_tmp/coverage/*.json are dated 2026-10-04, not this run).
