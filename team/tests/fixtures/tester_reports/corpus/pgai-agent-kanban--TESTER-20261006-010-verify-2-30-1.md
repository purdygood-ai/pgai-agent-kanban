# Verification Report: rc/v2.30.1

(Branch on disk: `ai_rc/v2.30.1`, project branch prefix `ai_`. RC tip `3de8d49d`. Base `ai_main`.)

## Summary

Verified RC v2.30.1 (`ai_rc/v2.30.1` @ 3de8d49d) against the bug bundle `requirements/v2.30.1-bugfix-bundle-20261006.md`: five headline bugs (BUG-0558, BUG-0560, BUG-0561, BUG-0564, BUG-0565) and seven hygiene ride-alongs (BUG-0555, BUG-0556, BUG-0557, BUG-0559, BUG-0562, BUG-0563, BUG-0566). Each bundled bug's own Acceptance section was the spec. All 12 items are addressed and meet their acceptance. Where a fix prevents something, the original failure was reproduced against `ai_main` extracted to scratch and shown fixed on the RC (BUG-0555, 0556, 0564, 0565). The bundle's compile and bash-syntax criteria pass across the whole tree. Integration gated runner: exit 0 (855 passed). Unit gated runner: exit 1, with exactly one failure: a repo-wide invariant test broken by four bare `python3 -m` calls that this RC's inline-Python port introduced (Finding 1, BUG-0567, medium). Runtime behaviour at those call sites is intact, but the red unit runner means this RC cannot be `PASS`. One quality-class lint (`engine_size_dupe`) was filed as BUG-0568 and does not affect the recommendation.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Pre-flight (Steps 2-3)

- Worktree `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20261006-010-verify-2-30-1`: `git rev-parse HEAD` = `3de8d49d0cc05285f9b3dece041025932702ecc4` = `git rev-parse ai_rc/v2.30.1`. `git status --porcelain` empty. `git stash list` empty. PASS.
- Step 2 merged-branch check: local refs are only `ai_main`, `ai_rc/v2.30.1`, `main`, `last_run_ai_main`; every cycle feature branch was deleted after merge, so both checks pass trivially. All seven cycle merges (c0bf75eb, 809ca506, aa347f7c, 0de5e51d, 38bb2245, 017cfd20, 3de8d49d) are in `git log ai_main..ai_rc/v2.30.1`. PASS.

## Findings

(appended as verification proceeds)

### Bundle-level acceptance criteria

- "All Python files compile cleanly": `ast.parse` over all 774 git-tracked `*.py` on the RC — 0 failures. **pass**
- "All bash files pass syntax check": `bash -n` over all 320 git-tracked `*.sh` — 0 failures. **pass**
- "Each bundled item is addressed": see the per-item checks below.

### Headline items

- **BUG-0558 (xdist collection determinism)** — `test_pid_liveness_verdict` now parametrizes on `{LIVE}` / `{LIVE}\r` tokens filled inside the test. Ran `python3 -m pytest tests/unit/test_ops_locks.py -n 3 -p no:randomly`: 82 passed, no "Different tests were collected". Two separate `--collect-only` processes produced byte-identical sorted ID lists (82 IDs); no ID contains a PID; held / CR-stale / dead 999999999 / 0 / -1 / abc / overflow cases all present. Suite-level acceptance (parallel lane reaches pytest) is evidenced by the gated unit run below. **pass**
- **BUG-0560 (ICD version step)** — `docs/api/ICD_VERSION` = `team/pgai_agent_kanban/api/ICD_VERSION` = `1.15.0`; `icd.json` `info.version` 1.15.0; `cmp docs/api/icd.json docs/api/baselines/icd-v1.15.0.json` identical; `1.15.0` is the last line of `SUPPORTED`; all 16 older baselines (incl. icd-v1.14.0.json) byte-identical to `ai_main`; `ICD_CHANGELOG.md` diff is additions only with one new `## 1.15.0` entry naming `GET /dashboard/{pane}`; `lint_icd_compat.py` exit 0 (compatible superset of 17 baselines, support table mirrors SUPPORTED); `lint_icd_freshness.py` exit 0; `generate_operator_verb_docs.py --check` exit 0; `docs/operator-api.md` support-table row `| 1.15.0 | supported | v2.30.1 |` and `docs/README.md` cites ICD 1.15.0. Pinned tests: covered by the gated unit run. **pass**
- **BUG-0561 (run-by-path guards)** — with `PYTHONPATH` unset from a neutral cwd, `python3 <file> --help` exits 0 with no traceback for `ops/verdict.py`, `dashboard/column_render/cells.py` and `dashboard/column_render/__main__.py` (the last prints argparse usage; the first two have no `__main__` block and exit 0 silently — the docs cite both as code references, not commands, so this meets the bug's Expected). `lint_doc_command_truth.py` exit 0, "0 findings". **pass**
- **BUG-0564 (empty release-state field reads neighbour)** — probe file from the bug: `read_release_state_field(path, "RC Opened By Task")` on the RC refuses `release-state.invalid-field` with message `RC Opened By Task=<empty> [release-state.empty-value]`; on `ai_main` (extracted to scratch) it returns `'v1.1.0'` (original failure reproduced). Empty `## Active RC` now refuses naming `Active RC=<empty> [release-state.empty-value]`; base named the RC Opened At timestamp. Populated file reads identically through `read_release_state_field`, `read_state_field`, `read_state_section`. Bash `_active_rc` on a sandbox root: empty Active RC followed by a version-shaped `## RC Opened At v9.9.9` returns the none value on the RC, `v9.9.9` on base; populated returns `v3.4.5` on both. `cancel.py` reads Last Released through `read_state_section` and leaves an empty value as written with a warning. **pass-with-caveat** — the loader keeps the umbrella key `release-state.invalid-field` and carries the per-field `release-state.empty-value` only inside the message; the bug asked for an empty-value refusal "dual-channel". The fault is distinguishable and correct, so this is a caveat, not a gap.
- **BUG-0565 (dashboard board panes render empty)** — Independent probe on a synthetic golden board (`tests/fixtures/golden_board.build_synthetic_board`, CODER queue BACKLOG/WAITING/WORKING/BLOCKED/DONE/WONT-DO + one open bug), FastAPI `TestClient` against the RC app: `GET /dashboard/queue?column_type=coder` → 200, 6 rows (WONT-DO included), stdout byte-identical to `column-render.sh queue none --kanban-root <root> --all-projects --column-type coder`; same parity for `column_type=Coder`, `input/bugs`, `queue/cm`. Refusal: `queue` with no param, `input` with no param, `queue?column_type=bugs`, `queue?column_type=%20%20%20` → 422 `{"error":"dashboard_pane.column_required", message, remedy (runnable curl), warnings}`. Other panes (`metrics`, `status-window`) ignore `column_type` with a warning and unchanged stdout. Original failure mode on `ai_main` (extracted to scratch, same board): every column-pane call returns 200 `"(empty)\n"`. Refusal site carries a `BLOCK-WORTHY` class comment, `CENSUS-ANCHOR: reads-dashboard-column-required-01`, and a Locator Index line. `docs/operator-api.md` rows and paragraph match the handler. **pass**

### Hygiene ride-alongs

- **BUG-0555 (bundle close counts negative mentions)** — Replayed the bug's scratch reproduction verbatim. RC: `promoted: BUG-9001-fixed.md`, `carried (no DONE task claims it): BUG-9002-unticketed.md`, BUG-9002 stays `running`. Base: both promoted to `done` (original failure reproduced). Constraint check: ran the RC's `promote_bundle()` on scratch copies of this release's own bundle + its 12 bug files against the live `tasks/` directory (read-only): `12 promoted, 0 carried`, so the new claim rule still closes every item this release ships; live bug files untouched. **pass**
- **BUG-0556 (carried items never re-bundled)** — Scratch project with five bugs: open; running in a `done` bundle (carried); running in a `running` bundle (in flight); running in both; running in no bundle. RC `get_unbundled_bugs` → `['BUG-9001-open', 'BUG-9002-carried']` and cache renders the carried item `[ ]`; base → `['BUG-9001-open']` with the carried item `[x]` (original stranding reproduced). `test_carried_bundle_item_rebundled.py` drives the real bash `discovery_run_pipeline` → real `promote_bundled_items.py` → real second discovery pass. Post-ship report wording now says the item stays `running` and the next discovery pass offers it, which matches the observed behaviour. On the live root, 0 bugs and 0 priorities currently qualify as carried, so shipping this causes no rebundling flood. BUG-0553 (acceptance line 3) is already `done` on the live root, so that line is moot. **pass**
- **BUG-0562 (route-impl shell rows remain)** — `lint_api_route_impl.py`: `32 verb(s), 29 module row(s), 0 shell row(s), 3 launcher(s) (3 impl shell, baseline 3); 0 finding(s)`, exit 0. `api-route-impl-baseline.txt` (`shell_rows 3`) equals `--emit-baseline`. `verify-rc-state` ported to `ops.rc_state.verify_rc_state` (manifest `module:`), `GET /verify-rc-state` in process, `test_verify_rc_state_route_cli_parity.py` uses `assert_route_cli_parity`. `deep-verify` classified as a documented launcher in `api-route-impl-launchers.txt` (the bug allows this); the stated reason checks out: `deep-verify.sh` reaches `deep_verify.schedule` and `deep_verify.classify` through `pp_run_ops` and launches `run-cold-build-tests.sh`. CLI byte-parity: ran base (`ai_main`) and RC `verify-rc-state.sh pgai-agent-kanban` (plain and `--verbose`) on a copied mirror of the live project root: stdout (131 / 438 lines), stderr and exit code (0) byte-identical. **pass**
- **BUG-0563 (inline-Python target)** — `lint_inline_python.py`: `51 block(s) (46 heredoc, 5 python3 -c), 3385 python line(s); 0 finding(s)` (target ≤ 52 / ≤ 3,400). Allowlist sums 51 / 3385 and its data lines equal `--emit-allowlist`. Byte-parity spot-check beyond `verify-rc-state`: ran `discovery_run_pipeline` (whose `discovery_eligibility.sh`, `discovery_items.sh`, `discovery_guards.sh`, `discovery_state.sh` blocks were ported) on two copies of the live project mirror with Active RC set to none, one wired to `ai_main` and one to the RC: identical stderr narration across ~415 lines (timestamps and dated IDs normalised), identical decision (both queue PM for `v2.31.0-ship-posture-always.md`), identical idle/blocked outcomes over two passes. **pass** (see Caveats for a measured slowdown of the discovery pass, which is outside TESTER's scope)
- **BUG-0566 (verdict duplicates INI line splitter)** — `lib/ini.split_lines` is public; `ops/verdict.py` calls `ini.split_lines` at 4 sites and `_awk_records` is gone. `lint_python_duplicates.py`: `0 ratchet finding(s)`, exit 0. Named tests covered by the gated unit run. **pass**
- **BUG-0557 (stream-pane test picks wrong pid)** — `_stream_pid()` now selects the loop child with `os.getpgid(pid) == pid` (the `setsid`-started stream is the only group leader). New `test_stream_selector_returns_only_the_stream_during_loop_start_up` samples during start-up. No serial/skip/quarantine marker; commit `2012e236` touches only the test file. Passed in the full gated parallel lane (only one unit failure in the run, below) and in a targeted gated `--files` run. **pass**
- **BUG-0559 (read-route probe stale subprocess setup)** — commit `09b59caf` changes only `team/pgai_agent_kanban/api/tests/test_bug0041_acceptance.py` (no production change). Targeted gated run (`--verbose --files`): `test_unknown_query_param_produces_named_warning[status|test-report|rejected|verify-rc-state]` and `test_clean_call_returns_empty_warnings[...]` all PASSED. **pass**

Targeted gated run used as evidence for the named tests above (not as suite PASS evidence): `run-unit-tests.sh --verbose --files` over 15 files (test_dashboard_pane_visibility, test_bug0041_acceptance, test_review_cmds, test_lint_icd_compat, test_package_modules_run_by_path, test_lint_doc_command_truth, test_cm_state_fields, test_cm_cancel, test_ops_verdict, test_lib_ini, test_promote_bundled_items_carries_unfixed, test_carried_bundle_item_rebundled, test_verify_rc_state_route_cli_parity, test_board_classifier_module, test_release_state_bash_readers): exit 0, `650 passed, 2 skipped` (skips: TestReadRouteProbe `[approvals]`, `[traces]`, unrelated).

### Gated suites (Step 5a / 6.6 / 6.7)

- `team/scripts/run-unit-tests.sh --coverage` → **UNIT_EXIT=1**. Parallel lane: `1 failed, 9630 passed, 41 skipped` in 797 s; serial lane `5 passed`; `Lane exit codes: 1 0 -> combined exit code 1`. The parallel lane collected and ran, which confirms BUG-0558's suite-level acceptance ("reaches pytest execution in the parallel lane"). The single failure is Finding 1.

### Finding 1: pp_run_ops sweep invariant broken by four new bare python3 -m invocations

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (the class can recur: CODER tickets are limited to targeted `--files` runs, so repo-wide invariant tests only run at TESTER time)
**Regression:** yes
**Integrity:** none
**Filed As:** BUG-0567 (Path C-A)

`tests/unit/test_pp_run_ops_sweep.py::test_no_bare_python3_module_invocations_outside_exemptions` fails in the gated unit run:

```
AssertionError: Sweep invariant violated — 4 bare 'python3 -m pgai_agent_kanban' invocation(s) found outside the exemption list:
    team/scripts/api-server.sh:296:if ! _API_PORT="$(python3 -m pgai_agent_kanban.api.config port)" ...
    team/scripts/api-server.sh:303:        "Run the resolver to see its error: ... python3 -m pgai_agent_kanban.api.config port"
    team/scripts/dashboard/lib/halt_scope.sh:268:    PYTHONPATH="$_dhd_token_team" python3 -m pgai_agent_kanban.halt_after.describe describe "$_dhd_event" ...
    team/scripts/lib/cm/release_halt_after_spend.sh:94:  desc="$(PYTHONPATH="${kanban_root}..." python3 -m pgai_agent_kanban.halt_after.describe \
```

None of these lines exist on `ai_main` (the base had `python3 -c` / heredoc forms there, which the sweep does not flag). They come from commit `b65fc861` ("Move one-line and short inline Python out of thirteen scripts", CODER-20261006-007), which turned inline Python into `python3 -m` calls instead of `pp_run_ops` calls. CODER-007's ticket constraint said "bash reaches Python through pp_run_ops", and its status records the deviation as Instruction Conflict 4, but the sweep test was not run. Runtime behaviour is intact: every site sets `PYTHONPATH` to its own package root, so the calls resolve. The defect is that the RC breaks an existing repo-wide invariant and turns the gated unit runner red. That alone denies a `PASS`. Rated medium per SOP-core "Rating a Finding": incorrect, but nothing at runtime is broken. Fix: route the three call sites through `pp_run_ops` (the remedy string at api-server.sh:303 should match), or, if explicit `PYTHONPATH` is deliberate, add each file to `_EXEMPT_SUFFIXES` in the test with a rationale comment.

- `team/scripts/run-integration-tests.sh --coverage` → **INTEGRATION_EXIT=0**. Parallel lane `855 passed, 1 skipped` in 484 s; serial lane collected 0 (exit 5, combined as 0); `bare_tmp_cleanliness: OK`, `listener_cleanliness: OK`, `sentinel_leak: OK`; "Integration tests PASSED". Covers the end-to-end cancel-rc tests named by CODER-20261006-006 for BUG-0564.
- PASS precondition (Step 8): not met, because `UNIT_EXIT=1`.

### Coverage (Step 5b, informational only)

Read from each runner's coverage JSON `totals` (field names checked against the generated file: `percent_statements_covered`, `percent_branches_covered`, `percent_covered`).
- unit: line 79.55 %, branch 65.55 % (blended 76.39 %).
- integration: line 13.10 %, branch 8.91 % (blended 12.15 %). Integration mostly exercises scripts by subprocess, which in-process coverage does not measure.

### Step 6 methodology results

- **6.1 release-state.md** — Active RC `v2.30.1`, RC Opened At `2026-10-06T00:37:13+00:00`, RC Opened By Task `CM-20261006-009-open-rc-2-30-1`, Last Released `v2.30.0` (well-formed; read through the RC's own validating loader). `pp_last_released_version` = v2.30.0; `semver_gt v2.30.1 v2.30.0` true. **pass**
- **6.2 / 6.3 structural and content** — 13 added files (icd-v1.15.0 baseline; modules `discovery/{__init__,eligibility,items}.py`, `halt_after/describe.py`, `lib/{bundle_items,json_field}.py`, `lint/doc_commands.py`, `ops/rc_state.py`, `scripts/lib/gated_findings.py`; three test files) and 73 modified. New modules answer `--help` by path (covered by `test_package_modules_run_by_path.py`, green). WRITER prose (`docs/operator-api.md` /dashboard rows and paragraph, /verify-rc-state in-process wording, deep-verify launcher row, ICD 1.15.0 support row; `docs/README.md` ICD 1.15.0) read in context and matches the shipped code. **pass**
- **6.4 executable criteria** — all per-item commands above run; results recorded per item. **pass** (except the unit runner, Finding 1)
- **6.5 failure modes** — original failure reproduced on `ai_main` and shown fixed on the RC for BUG-0555 (negative mention closes item), BUG-0556 (carried item stranded), BUG-0564 (empty field reads neighbour, in Python and bash `_active_rc`), and BUG-0565 (pane returns `(empty)`). The refusal guard for BUG-0565 fired for missing, blank, unknown and cross-pane column. **pass**
- **6.8 synthetic release simulation** — the diff touches `team/scripts/cm/promote_bundled_items.py` (cm-release Step 16b), `team/scripts/cm/{open-rc,release}.sh` (inline-Python ports) and `team/scripts/lib/cm/*`. `test_cm_release_synthetic.py` is in the integration suite, which passed. Also simulated Step 16b directly on a scratch copy of this bundle: 12/12 promoted. **pass**
- **6.9 constraint negative checks** — added lines grepped for bug, task, RC and version citations: only sanctioned exceptions (the ICD support-table interface value, fixture/example IDs in tests). No hardcoded secrets. No modified `.sh` changed its `set -euo pipefail` count. The ICD freeze constraint on CODER-007 holds (`icd.json` == frozen 1.15.0 baseline). **pass**
- **6.10 prereq graph** — 11 cycle tasks, every prerequisite exists, no cycles; CM-009 open-rc at the start; TESTER-010 then CM-012 release at the end (sequence 011 unused). **pass**
- **6.11 spot-checks** — (a) State drift: none. Live release-state matches the RC branch; the live queue lines for this cycle are canonical and match each status; `verify-rc-state` (RC code, mirror copy) reported 0 errors, and none of its 113 warnings names a cycle task. (b) Untested mechanisms: `lib/bundle_items.py` has no direct unit test but is exercised end to end by `test_carried_bundle_item_rebundled.py` and by TESTER's multi-bundle probe (observation only). (c) Regressions of prior gaps: the run-by-path class (BUG-0533 → BUG-0561) is clean; `lint_doc_command_truth` 0 findings and the run-by-path test is green. **pass**
- **6.13 skip-cites-real-bug** — no placeholder skip IDs. Every `BUG-NNNN` hit near "skip" is a fixture string or log-text assertion, not a skip annotation. `lint_skip_bug_gate`: "0 missing files". **pass**
- **6.14 coding standards** — the one hit is Finding 1 (new bare `python3 -m` instead of `pp_run_ops`, contrary to the modular-code standard the ticket restated). JSON-field port parity: the old `python3 -c` reads and the new `pgai_agent_kanban.lib.json_field` calls in `wake_driver_invoke.sh` return byte-identical output for 11 edge cases. No new script without `--help`.

## Lint Findings (non-blocking)

- `engine_size_dupe` — `team/scripts/lib/wake_driver_invoke.sh` is 805 lines, over the 800-line engine ceiling (was 798 on `ai_main`). Present in both `findings-unit.json` and `findings-integration.json`. No open bug covered it (BUG-0350 and BUG-0454 are `done`), so filed BUG-0568 (Path C-A — filed as BUG — code defect / regression / test failure), severity low. On its own this does not change the recommendation.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=28d freshness=stale — The last cold-build record is red and stale on both counts: 28 days old (window 7 days) and for v2.9.20, not v2.30.1. That run filed BUG-0167 (cold-build claude-abliterated failure, now `done`), and nothing is known about whether the current images build; the operator owns re-running the lane.

## Autonomous criterion

1. Tasks force-promoted WAITING → BACKLOG by hand? **NO** — the only promotion lines are the wake script's own ("Promoted from WAITING to BACKLOG by wake script").
2. `release-state.md` edited outside the CM scripts? **NO** — its mtime (`2026-10-06 00:37:13.3`) equals the `RC Opened At` written by CM-009, and nothing has written it since.
3. Scripts patched mid-build? **NO** — no file under the live install's `scripts/`, `pgai_agent_kanban/`, `pm-agent/`, `roles/` or `SOP-core.md` changed after RC open. RC commits touching `team/scripts/` are this cycle's ticketed deliverables in the dev tree, not tooling patches.
4. Queue files hand-edited? **NO** — cycle queue lines are canonical `- [m] ID` entries with markers matching each status.md.
5. Non-CM remote git operation? **NO** — no `git-boundary-guard.jsonl` in any cycle task's `logs/`.

Autonomous operation criterion: Manual interventions required: none

## Caveats

- **Discovery pass slowdown (observation; performance is outside TESTER scope and no requirement states a performance criterion).** On copies of the live project mirror (1,081 task folders), full `discovery_run_pipeline` passes took 26 s and 53 s with `ai_main` scripts against 66 s and 175 s with the RC's (log-timestamp measurement, on a host also running the unit suite, so absolute numbers are noisy). The likely cause is that `_disc_task_terminal` reads each task's `## State` with `pp_run_ops pgai_agent_kanban.discovery.items task-state` (measured 0.139 s per call) instead of a bare heredoc (0.040 s per call), and discovery calls it once per task. Decisions and narration were byte-identical. Recorded for human consideration; not filed.
- **BUG-0564 error key.** The validating loader keeps the umbrella error key `release-state.invalid-field` and names `release-state.empty-value` per field in the message, as CODER-006 chose deliberately to preserve REST/CLI parity. Correct and distinguishable, so pass-with-caveat.
- **BUG-0561.** `ops/verdict.py` and `column_render/cells.py` exit 0 silently on `--help` by path (no `__main__`). The docs cite them as code references, not commands, so this is acceptable.
- **Census prose.** `reads-dashboard-column-required-01` has its class comment, anchor and Locator Index line (the CODER contract), but no prose paragraph like other API anchors. CODER-003 and WRITER-008 both noted it. Observation only.
- **Stale prose flagged by CODER-007 and left by WRITER-008 as out of ticket scope:** `docs/cm-release-reference.md` (~line 206, claim rule and carried items) and `team/SOP-core.md` line 103 ("`running` ... skipped unconditionally", now inexact because carried `running` items are candidates). The SOP-core line is governance prose; CM or an operator should decide whether a WRITER ticket is wanted. Not filed by TESTER, because neither is a criterion of this bundle.
- **Frozen ICD description.** The 1.15.0 ICD route description of `GET /verify-rc-state` still describes a script pass-through, while the route now runs in process (same response). Noted by CODER-007 and WRITER-008 for the next ICD version step.
- Probes ran in sandboxes under `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester-010/` (copied mirrors, never the live root). One read-only query ran the RC's `bundle_items.carried_item_names` against the live `requirements/` directory to confirm 0 live items would be re-offered. The worktree stayed clean (`git status --porcelain` empty) throughout.
- Gated-run logs: `/tmp/pgai_kanban_tmp/tester/v2.30.1-unit.log`, `v2.30.1-integration.log`, and their `.exit`, `findings-*.json` and `coverage-*.json` copies.
