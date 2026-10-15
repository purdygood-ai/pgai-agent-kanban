# Verification Report: ai_rc/v2.26.0

## Summary

This report verifies RC `ai_rc/v2.26.0` (HEAD e54118b) against `requirements/v2.26.0-agent-metrics-v2.md`. The RC covers agent-metrics rounding truth, a shipped-only denominator, totals columns, `--all` / `--since` / `--last` spans, a REST twin with a golden pair, and a window-8 split pane. All four deliverables and all four acceptance criteria are met. I confirmed this with my own sandbox fixtures, hand-computed, on both the CLI wrapper and the REST route. The integration runner is green (631 passed).

The unit runner exits 1: 5 failures, 5414 passed. All 5 fail identically on a fresh clone of `ai_main`, so they are pre-existing and environment- or live-state-coupled, not caused by this RC.

Two further concerns are outside the RC's code:
- On production data, the CM row still reads 1.3 tasks/RC, not the ~2.0 the Intent calls true. The per-RC rollup never counts the CM release task (a pre-existing aggregator timing defect).
- One cycle task needed an operator reset after the provider CLI was too old for the configured model.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Findings

### Finding 1: Rounding truth and shipped-only denominator (Deliverable 1, Acceptance 1)

**Category:** pass

Independent fixture `acc`: 5 shipped RCs, each with 2 CM tasks and 1,234,567 CM tokens, plus 1 cancelled RC carrying 999,999,999 CM tokens and 50 coder tasks. Output of `agent-metrics.sh --project acc`:
`cm  5.0  1,234,567.0  2.0  6,172,835  10`, footer `window: 5 shipped RCs (1 cancelled excluded)`.
With the cancelled rollup removed, every figure is identical and the footer reads `(0 cancelled excluded)`. Averages always render one decimal. `round_one_decimal` rounds half away from zero: 2.25->2.3, 2.35->2.4, -2.25->-2.3. Shipped status is read from the rollup's own `outcome`. All 156 live rollups carry `outcome: shipped`, which is sourced from `release-state/<v>.json`, so this is gitless-safe.

### Finding 2: Totals columns (Deliverable 2, Acceptance 2)

**Category:** pass-with-caveat

`Total Tokens` and `Total Tasks` sit to the right of the averages. They are comma-formatted via the shared `number_format.format_int` and equal hand sums on every fixture (acc 6,172,835 / 10; big `--all` 40,820 / 67). Caveat: `history.csv` gained no fields. The deliverable says "gains ANY new fields at END of row", and the totals are derived at read time, so nothing needed adding. `metrics_csv_writer.py` is untouched.

### Finding 3: Full-history spans and split pane (Deliverable 3, Acceptance 3 CLI side, Acceptance 4)

**Category:** pass-with-caveat

40-RC fixture `big` (v0.1.0..v0.40.0, plus a cancelled v0.20.5). CLI `--all`, `--since v0.30.0` and `--last 25` each equal my Decimal hand computation on every column. For example, `--all`: cm 20.4 / 1,020.5 / 1.7 / 40,820 / 67; tester `--` / 143.5 / 1.0 / 5,740 / 40. The footer's excluded count is 1 exactly when the span's range covers v0.20.5.

The split pane was built: census W8 now has 2 panes (left = default last 10, right = `--all`). `create.sh` splits `-h -l 50%`. The suggested-commands row lists `scripts/dashboard/agent-metrics.sh --all [--project <proj>]`. DASHBOARD-PANES.md and dashboard.md state "The split form was built", which satisfies the docs clause.

Caveat (ambiguity, not a gap): Acceptance 4 says "Window 8 default view unchanged except decimals + new columns", but Deliverable 3 invites a split pane, which necessarily adds a pane to window 8. The left pane is the old view plus decimals and columns, so I read the two as consistent. The docs also note the ~95-column table wraps in a half pane on terminals narrower than about 190 columns.

### Finding 4: REST twin and golden pair (Deliverable 4, Acceptance 3 REST side)

**Category:** pass

In-process TestClient against my sandbox root. `/agent-metrics?project=acc|big` with `all=true`, `since=v0.30.0` and `last=25` returned the same numbers, `span`, `shipped_count` and `excluded_count` as the CLI and my hand computation. The RC's own `test_agent_metrics_rest_parity.py` uses a real uvicorn server and a hand computation on a 40-RC history with 3 cancelled RCs, and it passed inside the green integration run. ICD bumped 1.9.0->1.10.0 with a frozen baseline, SUPPORTED, and the operator-api support table updated. The response schema is untyped, so the change from integers to one-decimal numbers is a disclosed value change (ICD_CHANGELOG), not a compat break.

### Finding 5: Span guards fire (Step 6.5)

**Category:** pass

Wrapper: `--all --last 3` and `--window 5 --since v0.1.0` are refused as mutually exclusive. `--last 0`, `--last abc`, bare `--last`, `--since 1.2`, `--since v1.2.3.4` and `--window -1` each exit 1 with a named `ERROR:` line. REST: `all=true&last=3`, `window=5&since=...`, `all=true&window=10`, `last=0`, `window=0` and `since=1.2` each return 422 with a detail naming the parameter. `all=false` falls back to the default window, as documented.

### Finding 6: CM row still under-reports on production data (Intent; Step 6.11 production-shape spot-check)

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated to metrics accuracy)
**Filed As:** BUG-0431 (Path C-A, filed as BUG)
**Regression:** no
**Integrity:** none

The Intent names CM's true value as 2 tasks/RC (open-rc + release). On the live install after this RC's code, `agent-metrics.sh` shows CM `Avg Tasks (/RC)` = 1.3 (last 10) and 1.1 (all). Each sampled shipped RC (v2.24.18, v2.25.0, v2.25.2) has two DONE CM tasks with tokens.json, but the rollup records `tasks.by_agent.cm = 1`. Each rollup was written 2-11 minutes before the release task's tokens.json. The RC fixed the display arithmetic it was asked to fix. The Intent's headline number stays wrong for a reason the Intent did not identify, in `metrics_aggregator.py`, which this RC does not touch. The fixture-based acceptance criteria all pass. This is recorded as a gap against the Intent, not against any acceptance line.

### Finding 7: Gated unit runner exits 1 - git local-only test inherits wake env (Step 5a / 6.6)

**Category:** bug (pre-existing)
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (fails every unit run launched from an agent wake, so no RC can PASS until fixed)
**Filed As:** BUG-0432 (Path C-A, filed as BUG)
**Regression:** no
**Integrity:** none

`tests/unit/test_discovery_git_local_only_wake_context.py::test_flag_unset_still_falls_through_to_ls_remote` fails. The wake exports `PGAI_GIT_LOCAL_ONLY=1` (wiring shipped in v2.25.1), and the test never unsets it. It fails identically on a fresh clone of ai_main (023f45a). Under `env -u PGAI_GIT_LOCAL_ONLY` the module passes 3/3.

### Finding 8: Gated unit runner exits 1 - event-band wall-clock bound (Step 5a / 6.6)

**Category:** bug (pre-existing)
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (load-dependent red on the gated runner)
**Filed As:** BUG-0433 (Path C-A, filed as BUG)
**Regression:** no
**Integrity:** none

`tests/unit/test_event_band_renderer.py::test_large_pre_today_body_renders_correctly_within_time_bound` took 10.21s in the runner, then 6.77s and 5.39s in isolation, against a 5.0s bound, at host load average 5-7 on 8 cores. It fails identically on the ai_main clone. This RC does not touch `event-band.sh`.

### Finding 9: Gated unit runner exits 1 - changelog freshness vs live bugs dir (Step 5a / 6.6)

**Category:** bug (pre-existing)
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (tracked by an open bug)
**Filed As:** BUG-0425 (existing, open; cited, not re-filed)
**Regression:** no
**Integrity:** none

3 x `tests/unit/test_lint_changelog_freshness.py` fail (`test_fresh_artifact_exits_0`, `test_fresh_artifact_prints_ok_message`, `test_rc_mode_tolerates_new_release_section_addition`). The committed CHANGELOG `KI-1.23.0.1` lines 1458/1541 differ from a regeneration against the live bugs dir (BUG-0417 flipped to done at v2.25.2 finalize). These fail identically on the ai_main clone. BUG-0425 names these exact lines, and its acceptance requires `lint_changelog_freshness.py` to exit 0.

### Finding 10: Manual intervention - CODER task BLOCKED on provider CLI too old for configured model (Step 6.12)

**Category:** bug (pre-existing process defect surfaced this cycle)
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (recurs on every future model/CLI version mismatch)
**Filed As:** BUG-0434 (Path C-A, filed as BUG)
**Regression:** no
**Integrity:** none

CODER-20260928-015 was dispatched 3 times at 2026-09-28T11:28. Each run died in about 3s with `API Error: 400 Claude Code 2.1.276 does not support this model; version 2.1.280 or newer is required`. The wake's claude model-preflight is a logged no-op, so the zero-progress cap (3/3) moved the task to BLOCKED / Needs Human, and OVERWATCH's transient-error check skipped it. An operator reset it at 2026-09-29T00:23:05 (`logs/reset.log`). The CLI was then 2.1.284, and the task finished DONE. The cause (host CLI version) is external, which is a permitted kind of human touch. The pipeline's inability to recognize it is a defect that will recur, so it is filed. The CLI upgrade has no `logs/activity.log` line; it was probably an operator action outside any task and is noted in Caveats.

### Finding 11: Release-state, constraints, prerequisites, skip gate, coding standards (Steps 6.1, 6.8, 6.9, 6.10, 6.13, 6.14)

**Category:** pass

- 6.1: `pp_last_released_version` = v2.25.2; `semver_gt v2.26.0 v2.25.2` is true; release-state fields are well-formed.
- 6.8: skipped. The diff touches no release-lifecycle file.
- 6.9: no secrets in added lines. `agent-metrics.sh` keeps `set -euo pipefail` after argument parsing, as before.
- 6.10: prerequisite graph is acyclic and bookended (CM-021 open-rc -> CODER 015..019 -> WRITER 020 -> TESTER 022 -> CM-024 release).
- 6.13: no skip annotations in changed tests.
- 6.14: no bug, task or version identifiers in added code or config lines. `--help` covers every new flag (wrapper and Python CLI). The default window of 10 appears in three twins, as it did before this RC.

## Test Results (Step 5a / 5b)

- `team/scripts/run-unit-tests.sh --coverage`: **UNIT_EXIT=1**. 5 failed, 5414 passed, 42 skipped (20m28s). All 5 are pre-existing (Findings 7-9).
- `team/scripts/run-integration-tests.sh --coverage`: **INTEGRATION_EXIT=0**. 631 passed, 1 skipped (20m11s).
- Coverage (informational, from `totals` in `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/*.json`; field names verified against the JSON first):
  - unit: line 77.50% (14362/18531), branch 63.60% (3456/5434)
  - integration: line 13.32% (2467/18522), branch 10.32% (561/5434)
- Logs: `/tmp/pgai_kanban_tmp/tester/v2.26.0-unit.log`, `/tmp/pgai_kanban_tmp/tester/v2.26.0-integration.log`

## Lint Findings (non-blocking)

The same three classes appear in both findings-unit.json and findings-integration.json:
- `changelog_freshness`: committed CHANGELOG `KI-1.23.0.1` status clause is stale against the live bugs dir. Cites BUG-0425 (open).
- `engine_size_dupe`: `lib/cm/release_ship_policy.sh` is 844 lines against an 800-line ceiling. Cites BUG-0428 (open).
- `hygiene` (provenance): `release-notes/v2.25.2.md:44` task-ID token `tester-012`. Cites BUG-0430 (open).

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=21d freshness=stale — The only deep-verify record is 21 days old and for v2.9.20, not v2.26.0 (both conditions fail). Its red result (claude-abliterated FROM parse error, rocky9 COPY --chmod under podman) was filed by that run as BUG-0167, now done, so nothing is known about whether this RC's images build.

## Autonomous criterion

1. Tasks force-promoted WAITING->BACKLOG by hand? **NO.** The wake logs show every promotion made by the wake prerequisite check. CODER-015's BLOCKED->BACKLOG return was an operator reset via the reset tool (`logs/reset.log`), which is the sanctioned HUMAN-only unblock path. It is recorded under Finding 10.
2. release-state.md edited outside open-rc/release/cancel-rc? **NO.** CM-021 opened the RC with `scripts/cm/open-rc.sh` (exit 0). The field values match.
3. Scripts patched mid-build? **NO.** No commits on ai_main since the RC opened. Every RC commit is a CODER/WRITER feature commit or --no-ff merge of this cycle's own deliverables.
4. Queue files hand-edited? **NO** evidence. Marker corrections in the wake logs were made by wake reconciliation.
5. Non-CM remote git operation? **NO.** No `git-boundary-guard.jsonl` exists in any cycle task folder (PM-002, CODER-015..019, WRITER-020, CM-021, TESTER-022). My own probes ran no remote verb and never set PGAI_AGENT_ROLE.

Autonomous operation criterion: Manual interventions required: 1. The operator reset CODER-20260928-015 after a provider-CLI/model version mismatch, an external cause the pipeline could not recognize (BUG-0434). A root HALT was also present at RC open. HALT pause/resume is a permitted human touch.

## Caveats

- Anything not attributable to this RC's code is rated `Regression: no`. The rating is not softened: the gated unit runner did not exit 0, and that alone bars PASS.
- Observations (not findings; no filing):
  - A rollup file with a malformed version stem (for example `v1.0.3a.json`) is still selected as shipped and sorts oldest. No such file exists in production (0 of 156).
  - The wrapper refuses `--all --all` as "mutually exclusive".
  - `--since 0.30.0` (no leading v) echoes the bound without the `v` in the header and footer.
  - `number_format.py --one-decimal 2.25` prints `2.2` (half-even). This is documented: callers must round with `round_one_decimal` first, and the CLI does.
  - The new always-on `--all` pane surfaces a historical malformed agent key `- agent: tester` from the v2.5.2 rollup.
  - Two leftover local branches (`ai_feature/CODER-20260926-017-provenance-sweep`, `ai_feature/CODER-20260927-005-provenance-sweep`) belong to WONT-DO tasks.
  - The provider CLI went from 2.1.276 to 2.1.284 between 2026-09-28 11:28 and 2026-09-29 00:24 with no `logs/activity.log` line (Directive 3). It was probably an operator action outside any task.
- I did not start the tmux dashboard (to avoid colliding with the live session). The window-8 split is verified via the census, the create.sh diff, and the green census/cadence unit tests.
- Direct pytest was used only to bisect the 5 unit failures (RC vs an ai_main clone) and to time the event-band test. It is never used as PASS evidence.
- Per SOP, the task README's `## Working Directory: none` was superseded by the wake-named worktree.

## Verification log (checkpoints written during the run)

- Pre-flight (Steps 2-3): worktree HEAD e54118b == ai_rc/v2.26.0; `git status --porcelain` and `git stash list` are empty. Step 2 found no DONE-task feature branch left locally (all merged and deleted). The 6 v2.26.0 CODER/WRITER merges are --no-ff merge commits on the RC.
- Production-shape CLI run (live data, read-only): default, `--all`, `--since v2.25.0` and `--last 3` all render one-decimal averages, comma totals and footers. CM reads 1.3 / 1.1. Root cause traced to rollup timing (Finding 6).
- Independent sandbox fixtures: acceptance 1-3 verified on the CLI wrapper and REST (Findings 1-5).
- 6.12 traced CODER-015's BLOCKED episode to a CLI/model mismatch and a no-op preflight (Finding 10).
- Unit runner: 5 failures, all reproduced on an ai_main clone; causes traced (Findings 7-9). Integration runner green.
