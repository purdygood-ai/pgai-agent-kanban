# Verification Report: rc/v2.31.1 (ai_rc/v2.31.1 @ 939b2843)

## Summary

This report verifies RC v2.31.1 against `requirements/v2.31.1-bugfix-bundle-20261006.md`, a bundle of ten bugs: three headline items (BUG-0571, BUG-0575, BUG-0577) and seven hygiene ride-alongs (BUG-0570, 0572, 0573, 0574, 0576, 0578, 0579). Four tasks implemented it: CODER-035, 036 and 037, and WRITER-038. The bundle doc's own criteria are "each bundled item addressed", "all Python compiles" and "all bash passes syntax". I used each bundled bug's `## Acceptance` block as the effective per-item criteria.

**All ten items are addressed, and each acceptance check I re-ran by hand passed.** I built my own probes for each headline fix and reproduced the original failure on `ai_main` before confirming the fix on the RC:

- **Push gate:** five scenarios, each run against both check-push-lag and push-watchdog.
- **By-path module run:** `auto_ship_notes.py --help` exits 0 by file path.
- **Finalize dependency header:** cross-checked mechanically against the functions finalize actually calls.

The ride-along checks:

- **Bare-call errexit (0570):** behaviour matches the reworded comment.
- **Board snapshot (0572):** byte-identical to `ai_main` on 16 renders, with 1 gather per tick, including 8 concurrent columns.
- **Closed states (0573):** `close.sh` refuses `superseded`; board and API read it as `wont-do`.
- **Effort validation (0574):** the claude/codex matrix behaves as specified.
- **Pasteable remedy (0576):** the printed WARNING remedy works when pasted.
- **Decide-only remedy (0578):** names the project in all three variants and writes nothing.
- **Duplicate allowlist (0579):** the duplicate lint reports 0 findings.

All 794 Python files compile and all 326 shell files pass `bash -n`.

**Both gated runners are red, and both because of this RC.**

- **Unit** (`UNIT_EXIT=1`; 3 failed, 9984 passed): the generated API contract `docs/api/icd.json` was not regenerated after the close-route docstrings changed.
- **Integration** (`INTEGRATION_EXIT=1`; 9 failed, 898 passed): the new per-provider effort check refuses every value for a provider with no declared list. Inside an agent wake, which exports `PGAI_<ROLE>_EFFORT=high`, that breaks the test-only `harness` provider used by the testing-only certification tests. This is the first red integration run in the last nine RC certifications.

Neither failure affects production code paths: the claude and codex providers both declare their lists, and the ICD is a document. I also found four partial-coverage gaps (release-path push readers, purge, stale help/docs, board CPU). All seven issues are filed via Path C (autonomous follow-up filing — non-blocking) as BUG-0580 through BUG-0586.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Findings

### Finding 1: Integration gated runner red — effort validation refuses every value for a provider with no declared list

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (one root cause; a future provider lib without a list, or another env-coupled test, would hit the same path)
**Regression:** yes
**Integrity:** none
**Filed As:** BUG-0580-listless-provider-refuses-every-effort (Path C-A, filed as BUG — code defect / regression / test failure)
**Filed By:** TESTER (autonomous Path C) — the new per-provider effort check refuses every effort value for a provider that declares no list, so inside an agent wake the test-only harness provider cannot load its config and nine integration tests fail.

**Failures.** `run-integration-tests.sh --coverage` exited 1: 9 failed, 898 passed, 1 skipped. The failures are the three `*WakeDispatchClosure` tests in each of `tests/integration/test_cert_{one,two,three}_ticket_testing_only_pipeline.py`. Each one fails at `wake/harness.sh exited 1` with `config_loader.providers.invalid_effort — reasoning-effort value 'high' supplied by PGAI_PM_EFFORT is not accepted by the active provider 'harness' (valid for harness: none declared)`.

**Cause.** CODER-037's effort fix validates against `PROVIDER_EFFORT_VALUES` read from `scripts/lib/provider_<name>.sh`. Only claude and codex declare one, so for any other provider the list is empty and every value is refused. The wake exports `PGAI_<ROLE>_EFFORT=high` (from the live `llm_effort = high`), and the harness subprocesses inherit it.

**Evidence.**
- `run-integration-tests.sh --files tests/integration/test_cert_one_ticket_testing_only_pipeline.py` with those variables unset: 11 passed, exit 0. That was a debugging run, not PASS evidence.
- The refusal key `config_loader.providers.invalid_effort` is new on this RC.
- `ai_main`'s union list accepted `high` for every provider.
- v2.31.0's certification (TESTER-20261006-032, same wake environment) had `INTEGRATION_EXIT=0`.

**Rating.** SOP "Rating a Finding" says that a command that worked in the last release and exits non-zero in this one is `high`, so the bug is filed at Severity `high`.

I rate Systemic Risk `medium`, not `high`, for three reasons:
- the defect is one root cause, confined to a test-only provider (`harness.sh` says it "should NEVER be used in production") plus an env-coupled fixture;
- production providers are unaffected: I verified claude/minimal refused, claude/high exporting `PGAI_CODER_EFFORT=high`, and codex/minimal accepted;
- no other framework component is broken.

The operator should weigh that the integration runner will stay red in every in-wake certification until BUG-0580 lands. Each future TESTER will report these nine by name and file nothing new for them, but they add noise to the verdict.

### Finding 2: Unit gated runner red — docs/api/icd.json stale after the close-route docstrings changed

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (a CODER deferred the regeneration to CM, which has no regeneration step; the same hand-off would recur)
**Regression:** yes
**Integrity:** none
**Filed As:** BUG-0581-icd-artifact-stale-after-close-states (Path C-A)
**Filed By:** TESTER (autonomous Path C) — the published API contract file was not regenerated after this release changed the close endpoint's documented states, so it still lists the retired "superseded" state as valid and three freshness tests fail.

`run-unit-tests.sh --coverage` exited 1: 3 failed, 9984 passed, 41 skipped. The three failures:
- `tests/unit/test_review_cmds.py::TestIcdFreshnessGate::test_freshness_gate_exits_0`
- `pgai_agent_kanban/api/tests/test_icd_generator.py::TestIcdWrapperCwdRegression::test_wrapper_output_matches_committed_artifact` (generated 212946 bytes vs committed 212809)
- `tests/unit/test_lint_icd_freshness.py::TestIcdFreshnessPositive::test_fresh_artifact_passes`

**Evidence.** I regenerated to scratch and diffed against the committed artifact. The `CloseBody` and `post_close` descriptions still list `superseded` as a valid close state, which the RC now refuses. On a `git archive ai_main` extract, the committed ICD is byte-identical to a fresh regeneration, so this RC introduced the drift. CODER-037 noted the stale ICD "for CM", but `team/scripts/cm/` and `team/scripts/lib/cm/` contain no ICD regeneration.

**Not a stale-assertion:** no test literal changed. The artifact is what is stale.

### Finding 3: BUG-0571's rule is not applied to the release-path push_to_remote readers

**Category:** gap
**Fix Effort:** medium (3-5 CODER tasks; golden ship/cancel contracts change)
**Systemic Risk:** medium (the same "default to push for an unresolved project" shape that produced the 154-push incident)
**Regression:** no
**Integrity:** none
**Filed As:** BUG-0582-release-push-readers-skip-project-gate (Path C-A)
**Filed By:** TESTER (autonomous Path C) — the release-path push decisions still default to pushing for a project that has no project.cfg or is not registered, and ship-rc can take its repository from an inherited dev-tree variable.

BUG-0571 Files Involved says the other `push_to_remote` readers "must agree on the same rule; one shared reader preferred". I confirmed by reading the code:
- `pp_push_to_remote` echoes `true` when no cfg exists and never checks registration. It is used by `cm/open-rc.sh`, `cm/finalize-release.sh` and `lib/cm/release_preflight.sh`.
- `cm/cancel.py push_to_remote()` returns `True` with no cfg. It is used by cancel-rc and `cm/ship.py`.
- `cm/ship.py` falls back to `PGAI_DEV_TREE_PATH` / kanban.cfg for its repository.
- The golden `ship-without-project-cfg` case pins the push.

CODER-035 recorded this under Instruction Conflicts and deliberately left it, because the task Goal scoped the work to non-release sites. It is a pre-existing gap that predates this RC, not a defect it introduced. It is exercised only when a CM release runs against a half-registered project.

### Finding 4: Purge helpers still treat a legacy `superseded` status as not closed

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated; zero live bug/priority files carry the value)
**Filed As:** BUG-0583-purge-skips-legacy-superseded-items (Path C-A)
**Filed By:** TESTER (autonomous Path C) — the old-file purge still treats a bug or priority whose status is the retired "superseded" as not closed, so it would never be cleaned up.

BUG-0573 Expected says closed "everywhere: board column, API /board, dashboard colour, discovery, archive". `purge-helpers.sh` `purge_closed_bugs` / `purge_closed_priorities` keep their own `done|wont-do` lists. A dry run on a sandbox printed `[SKIP] ... (status=superseded; not closed)` for the superseded file, while the done and wont-do files were selected. The enumerated acceptance scope (close.sh, classifier, discovery) is met. This is the remainder.

### Finding 5: close.sh help, quick-reference cheat sheet and census prose still describe the retired close behaviour

**Category:** bug
**Fix Effort:** small (1-2 CODER/WRITER tasks)
**Systemic Risk:** low (documentation only)
**Filed As:** BUG-0584-close-docs-stale-after-state-refusal (Path C-A)
**Filed By:** TESTER (autonomous Path C) — close.sh's own help text, the quick-reference cheat sheet and the blocking-site census prose still describe the old close behaviour, so operators reading them get the wrong picture.

Four places are stale (Documentation Discipline):
- `close.sh --help` still says it "refuses only when the key cannot be resolved", but it now also refuses an invalid `--state`. WRITER-038 flagged this as a code help-text handoff.
- `--state` is labelled "Task state: BACKLOG|WAITING|...". This label predates the RC.
- `docs/quickref/pgai-agent-kanban-cheatsheet.html:136` still offers `superseded`. CODER-037 listed it for WRITER, but WRITER-038's scope named five other docs.
- `docs/blocking-site-census.md:1658` prose names the removed `operations-close-invalid-state-01` anchor and has no prose for the two new anchors.

### Finding 6: Board columns still cost about half a core per visible window

**Category:** gap
**Fix Effort:** medium (3-5 CODER tasks; structural render change)
**Systemic Risk:** low (dashboard CPU only)
**Filed As:** BUG-0585-board-render-still-costly-per-tick (Path C-A)
**Filed By:** TESTER (autonomous Path C) — sharing one board snapshot per tick cut the eight board columns' work by only about a quarter, because starting a bash and a python process for every column still dominates.

BUG-0572's counted acceptance is met (Finding 13). Its Expected, "a visible board window costs a small fraction of one core", is not. One full 8-column tick on my 440-task / 216-bug fixture, rendered in sequence with cold caches, cost:
- `ai_main`: 5.69 and 5.72 CPU-s;
- RC: 4.21 and 4.15 CPU-s.

That is about a 27% reduction. Measured with single sequential renders; no load generated. CODER-037 reported the same caveat.

### Finding 7: BUG-0571 — out-of-band push gate (check-push-lag, push-watchdog)

**Category:** pass

I built an independent probe: my own `git` network-verb stub ahead of `git` on PATH, `env -i`, and a sandbox kanban root with a real local dev tree holding an unpushed tag.

check-push-lag:

| Case | Network calls | Result |
|---|---|---|
| Registered, `dev_tree_path`, no `push_to_remote` key | 2 (`ls-remote`, `push origin refs/tags/v1.0.0`) | default push unchanged |
| `push_to_remote = false` | 1 `ls-remote`, 0 push | `staged-by-design` in the action log |
| Legacy `PROJECT.cfg` with quoted `"false"` | 1 `ls-remote`, 0 push | `staged-by-design` |
| (a) no project.cfg | 0 | one skip line, `missing project-cfg` |
| (b) not registered | 0 | one skip line, `missing registration` |
| (c) no `dev_tree_path`, only `PGAI_DEV_TREE_PATH` set | 0 | one skip line, `missing dev-tree-path` |

In cases (a) to (c) the check exits 0 and writes nothing to the action log.

push-watchdog ran the same matrix: default pushes `main` and tags, `false` makes 0 calls, and (a) to (c) make 0 calls with one `skipped-unresolved-project` line each in `cm-push-watchdog.log`.

Original failure mode on `ai_main`: the unregistered case pushed the tag. The old watchdog exited 1 at library lookup, so it was dead before this RC; nothing schedules it (no cron entry, no caller).

The resolver fails closed when `KANBAN_ROOT` is unset or the resolver errors (read in `push_decision.sh`). The `docs/overwatch.md` section matches the code and the printed skip line verbatim.

### Finding 8: BUG-0575 — auto_ship_notes.py runs by file path

**Category:** pass

`env -u PYTHONPATH python3 <tree>/team/pgai_agent_kanban/cm/auto_ship_notes.py --help` from a scratch cwd exits 0, and `python3 -m pgai_agent_kanban.cm.auto_ship_notes --help` from `team/` exits 0. On `ai_main` the same by-path command raises `ModuleNotFoundError`. `lib/push_decision.py` (new this RC) also runs by path. The by-path contract tests are in the green part of the unit run.

### Finding 9: BUG-0577 — finalize declares its cross-stage callees

**Category:** pass

I mechanically cross-checked every function defined anywhere under `team/scripts/` and called from `release_finalize.sh` but not defined in it against its Dependencies header. All 13 are declared, including `cm_release_append_auto_ship_events`. The only unlisted hit was the word "cleanup" inside echo strings, a false positive. `test_release_summary_origin_actions.py`, `test_cm_release_ship_posture.py` and `test_cm_release_synthetic.py` were in the green parts of the runs.

### Finding 10: BUG-0576 — posture WARNING remedy is a pasteable by-path command

**Category:** pass

I triggered the WARNING with two recorded events and a missing notes file. The remedy printed was `python3 <KANBAN_ROOT>/pgai_agent_kanban/cm/auto_ship_notes.py <notes> --stop 2 tester.key.x --stop 3 tester.key.y`. Pasted from an unrelated cwd with `PYTHONPATH` unset, it exited 0 and placed both lines under `## Known Issues`. The path exists in the live install layout, and the sweep test passed in the unit run.

### Finding 11: BUG-0570 — errexit contract comment

**Category:** pass

The comment no longer calls a bare call safe and names the three capture forms. Behaviour with a stub evaluator exiting 1 under `set -euo pipefail` matches the comment:
- bare call: the shell exits 1;
- `|| rc=$?`: execution continues with `rc=1` and errexit still on;
- `if` form: execution continues with errexit still on.

### Finding 12: BUG-0578 — decide-only remedy names the requested project

**Category:** pass

On a sandbox root:

| Invocation | Remedy names |
|---|---|
| `--project other-proj --decide-only --force --reason x` | `--project other-proj` |
| no project | the `PROJECT_NAME` placeholder, with an instruction |
| `PGAI_PROJECT_NAME=envproj` | `--project envproj` |

All three exit 1. A file listing before and after showed nothing written.

### Finding 13: BUG-0572 — one board gather per tick, output unchanged

**Category:** pass

I built my own fixture: 4 projects including one disabled, one with no intake dirs, one with no queues, stale markers, queue entries without task folders, and unicode descriptions; 440 tasks and 216 bugs.

- All 8 `--all-projects` columns, in both colour modes, are byte-identical between `ai_main` and the RC, on both the build path and the read path. Content is real (populated rows).
- Gathers: 1 per colour mode across 16 RC renders. Three further reads within the TTL added none.
- A stale snapshot (mtime 10 minutes back), a corrupt snapshot (truncated JSON), and a stale snapshot behind a dead-holder lock file each triggered exactly one rebuild, with identical output.
- 8 columns launched concurrently made 1 gather, and all outputs were identical to `ai_main`.

CODER-037's tmux-socket measurement (8 gathers in 63 s, one per tick) is in its artifacts. I did not re-run the tmux form; my sequential and concurrent probes exercise the same mechanism without a tmux server. See Finding 6 for the CPU outcome.

### Finding 14: BUG-0573 — superseded retired; closed states shared

**Category:** pass

- **close.sh:** `--state superseded` exits 1 with the dual-channel `close.invalid_state`. The remedy names the real project and key (`--state wont-do --note 'superseded by another item'`), and the file and its backlog marker are unchanged. `--state bogus` exits 1. `wont-do` and `done` both close. `close_item(..., 'superseded')` raises the same contract.
- **Shared list:** close.sh, ops/write.py, the classifier, discovery, bundle_items and bug_scanner import `CLOSED_STATES` / `canonical_state` from `lib/terminal_states.py`. Grep shows no second list in those paths; the two remaining lists are in purge-helpers (Finding 4) and legend-render's fixed keyword colouring.
- **Rendering:** `test_board_column_render_parity.py` asserts a fixture `superseded` bug is `wont-do` on the real GET /board client and green in both column-render panes. That test drives the real API and the real script, and it is in the green part of the unit run.
- Remainders are filed as Findings 4 and 5.

### Finding 15: BUG-0574 — per-provider effort validation

**Category:** pass

Sandbox `load_config` results:

| Provider | Value | Result |
|---|---|---|
| claude | cfg `minimal` | fails with `config_loader.providers.invalid_effort` naming "valid for claude: low medium high xhigh max"; runnable `sed` remedy |
| claude | env `PGAI_CODER_EFFORT=minimal` | fails the same way; remedy names the variable |
| claude | `high` | passes and exports `PGAI_CODER_EFFORT=high` |
| codex | `minimal` | passes |

The live kanban.cfg (`claude`, `high`) stays valid. `kanban.cfg_example` and `docs/OPERATIONS.md` were updated. The census anchor and index line exist.

Observation, outside the spec: codex now refuses `max`, which the old union list accepted. I could not confirm codex's CLI vocabulary offline, so I am not filing it.

The regression this change caused in the test harness is Finding 1.

### Finding 16: BUG-0579 — duplicate allowlist

**Category:** pass

`lint_python_duplicates.py` reports 0 ratchet findings (exit 0). Neither `findings-unit.json` nor `findings-integration.json` carries a `python_duplicates` entry.

### Finding 17: Requirements-doc criteria — compile and syntax

**Category:** pass

All 794 tracked `*.py` files compile, with 0 failures. All 326 tracked `*.sh` files pass `bash -n`, with 0 failures. Both new libraries (`push_decision.sh`, `column_render_board.sh`) are mode 100755.

### Finding 18: Pre-flight, release state, prereq graph, constraints

**Category:** pass

- **Step 2:** 630 DONE tasks name a feature branch; none of those branches exists locally, so every one passes trivially. All 15 of this cycle's feature and merge commits are ancestors of `ai_rc/v2.31.1`.
- **Step 3:** HEAD equals `ai_rc/v2.31.1` (939b2843). `git status --porcelain` is empty and there are no stashes.
- **6.1:** `release-state.md` reads Active RC v2.31.1 and Last Released v2.31.0 (well-formed). `semver_gt v2.31.1 v2.31.0` is true via `team/scripts/lib/semver.sh`, using `pp_last_released_version`.
- **6.8:** the diff touches release-lifecycle files (`cm/release.sh`, `lib/cm/release_finalize.sh`, `lib/cm/release_ship_posture.sh`). `test_cm_release_synthetic.py` passed inside the integration run (not among the 9 failures).
- **6.9:** no secrets in added lines. `set -euo pipefail` is preserved in close.sh, push-watchdog.sh, release.sh and column-render.sh.
- **6.10:** the chain is CM-039 open, then CODER-035 and 036, then 037, then WRITER-038, then TESTER-040, then CM-042 release (WAITING). There are no cycles and every referenced ID exists.
- **6.13:** no skip annotations were added.
- **6.14:** the only BUG-NNNN tokens in added code are fixture values in tests (the sanctioned example-value case). There are no task or version citations in added code or docs.

## Gated Suites

- `UNIT_EXIT=1`: 3 failed, 9984 passed, 41 skipped (837 s). All three failures are Finding 2.
- `INTEGRATION_EXIT=1`: 9 failed, 898 passed, 1 skipped (600 s). All nine failures are Finding 1.
- Both runners were invoked as `team/scripts/<runner> --coverage` from the dev-tree root. Logs: `/tmp/pgai_kanban_tmp/tester/TESTER-20261006-040/{unit,integration}.log`.
- **Coverage unavailable.** Both runners printed "coverage disabled by config ([testing] coverage)". The `coverage/*.json` files on disk are dated 2026-10-04, belong to an earlier run, and were not used.

## Lint Findings (non-blocking)

- `icd_freshness` (unit and integration): `docs/api/icd.json is stale`. Cites BUG-0581-icd-artifact-stale-after-close-states (filed this run; also a Step-7 defect, Finding 2).
- `engine_size_dupe` (unit and integration): `lib/cm/release_finalize.sh -- 806 lines (ceiling: 800)`. The file was 794 on `ai_main`; this RC's header additions pushed it over. Filed as BUG-0586-release-finalize-exceeds-engine-ceiling (Path C-A, one bug per lint class).

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=29d freshness=stale — The record is stale on both counts (29 days old against the 7-day window, and recorded for v2.9.20, not v2.31.1); its red result was filed as BUG-0167-cold-build-claude-abliterated-failure (now done), and nothing is known about whether this RC's images build.

## Autonomous criterion

1. Tasks force-promoted from WAITING to BACKLOG by hand: **NO**. Each of CODER-035, 036, 037 and WRITER-038 was dispatched exactly once, after its prerequisites merged: 11:43 after the 11:41 RC open, 12:02, 12:12 after the 036 merge at 12:10, and 13:14 after the 037 merge at 13:05. This task's status records promotion by the wake script.
2. `release-state.md` edited outside the CM scripts: **NO**. Its mtime equals the RC open (2026-10-06T11:41:05Z, CM-039).
3. Scripts patched mid-build: **NO**. No file in the live install (`scripts/`, `pgai_agent_kanban/`, `roles/`, `SOP-core.md`) changed after the RC opened. Every RC commit belongs to one of the four tasks.
4. Queue files edited by hand: **NO**. Queue mtimes match agent transitions, and markers are consistent with task states.
5. Remote git operation by a non-CM agent: **NO**. No `git-boundary-guard.jsonl` exists in any of this cycle's task folders.

Autonomous operation criterion: Manual interventions required: none.

## Caveats

- **Integration severity is argued in Finding 1.** I filed BUG-0580 at Severity `high` per the SOP calibration rule. I kept the finding's Systemic Risk at `medium` because the failure needs a test-only provider plus an inherited environment, and the shipped claude and codex paths behave correctly. An operator who reads it as a broader regression may weigh it differently.
- **Pattern across releases.** Gated-suite regressions from changes validated only with `--files` have now appeared in 7 of the last 10 RCs (unit red in 6 of the previous 9). CM's trigger 8 owns cross-release patterns; I note it here without raising this report's risk on its own account.
- **Untested new mechanism.** The board snapshot's lock-contention and wait path (`column_render_board.sh` lines 176-201) has no test; `test_dashboard_board_snapshot.py` is sequential. My 8-way concurrent probe exercised it, and it behaved correctly: 1 gather, identical output.
- **Lock-noise observation.** In that concurrent run, the columns that lost the build race printed full `lock.provenance.contended` and `lock.stale_threshold.floor_enforced` error triples on stderr. The dashboard routes renderer stderr to per-pane debug logs (`dashboard/logs/`), not to the pane, so this is log noise only; I am not filing it.
- **Stale-literal hints.** CODER-037 listed six possible stale assertions and updated them in the same task; none failed.
- **Instruction conflicts.**
  - TESTER.md "Git workflow" says to commit artifacts on a feature branch off the RC, but the dispatch prompt forbids creating a branch. I followed the dispatch prompt, so the artifacts live only in the task folder.
  - The task README says Working Directory `none`. I used the dispatch prompt's worktree.
- **Re-run invariant.** No `report.md.previous-RUN-N` existed. This is a fresh verification.
