# Verification Report: rc/v2.25.0


## Summary

Verifying ai_rc/v2.25.0 (commit 4b84d9c) against requirements/v2.25.0-rest-parity-census.md:
verb census as source of truth, REST parity closure for read and action verbs, a BLOCK-WORTHY
census-walking parity gate with golden-pair fixtures, one ICD minor bump (1.7.0 -> 1.8.0), and
census-derived API/operator docs. 29 commits, 55 files (+13309/-764) over ai_main. Both gated runners exited 0; nine gap/bug findings were filed via Path C (autonomous follow-up filing — non-blocking) as BUG-0407..BUG-0415 (Path C-A: filed as BUG — code defect / regression / test failure); no stale assertions; no infrastructure failure. Recommendation SHIP-WITH-CONCERNS, systemic risk medium (three findings rated medium: the parity gate's script-without-route hole, missing read-verb golden pairs, dual-channel refusals on 4 of 24 action routes, and the ICD baseline re-freeze hazard).

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Pre-flight (Steps 1-3)

- Step 1: status.md was `WORKING` with an empty artifacts/ (wake-driver stamped); treated as a fresh start. No prior report.md/gaps.md existed.
- Step 2: scanned every task folder under projects/pgai-agent-kanban/tasks/. No `DONE` task's `## Feature Branch` still exists locally (all merged-and-deleted) -> both checks pass trivially. Two stray `ai_feature/*provenance-sweep` branches belong to `WONT-DO` tasks (CODER-20260926-017, CODER-20260927-005), not DONE ones; noted under Caveats.
- Step 3: `git rev-parse HEAD` == `git rev-parse ai_rc/v2.25.0` == 4b84d9c2166a44ca2e6a61cded9c501c888321fb; `git status --porcelain` empty; `git stash list` empty. PASS.
- Step 6.1: `pp_last_released_version pgai-agent-kanban` = v2.24.18; `semver_gt v2.25.0 v2.24.18` true. release-state.md: Active RC v2.25.0, RC Opened At 2026-09-27T09:32:35+00:00, RC Opened By Task CM-20260927-027-open-rc-2-25-0, Last Released v2.24.18 (well-formed). PASS.

## Findings

### Checklist extracted from the requirements doc (Step 4)

Deliverables: (D1) machine-readable verb census as source of truth, docs + API derive from it; (D2) parity closure — read verbs share one resolver/two renderers, action verbs accept the CLI's params and refuse dual-channel (JSON error/message/remedy) with today's auth; (D3) BLOCK-WORTHY parity lint walking the census (script<->route, schema validation, golden-pair fixture per read verb, verb absent from census fails); (D4) ICD minor bump exactly once, immutability law, ICD changelog lists added routes; (D5) API reference regenerated from the census, REST examples in operator docs verified by fixtures.
Acceptance: (A1) parity lint green on the shipped tree; each seeded gap class fails naming the verb; (A2) golden pairs for why --details, rc-metrics (Blocked populated), agent-metrics, show-queues; (A3) REST halt-after --until arms the same sentinel as the CLI, malformed version refused with error/message/remedy JSON; (A4) ICD bumped exactly once, docs regenerated, doc-truth green.
Constraints: enforcement class (parity + schema blocking, narration quality); loopback only; fixtures use the production REST entrypoint and production CLI; convergence = lint reports zero gaps on the shipped tree.

### Step 6.2 / 6.3 — structural and content review

- `team/config/operator_verbs.manifest`: 32 active rows, each with class + schema_ref; every row carries a real REST method/route (no ABSENT REST columns remain on the shipped tree). Both readers (`team/scripts/lib/operator_verb_manifest.sh`, `team/pgai_agent_kanban/lib/operator_verb_manifest.py`) parse the 11-field record. **pass** (D1).
- `team/config/operator_verb_census_exempt.txt`: 16 documented basenames, each with a reason comment. **pass**.
- New scripts `team/scripts/release-state.sh`, `team/scripts/deep-verify-status.sh`: executable, `--help` names every argument and exit code, both render through `pgai_agent_kanban.ops.read.get_release_state` / `get_deep_verify_status` — the same functions the REST handlers call. **pass** (D2 read twins, single resolver).
- `docs/api/ICD_VERSION` = `team/pgai_agent_kanban/api/ICD_VERSION` = 1.8.0; `docs/api/baselines/SUPPORTED` lists 1.0.0..1.8.0; `docs/api/baselines/icd-v1.8.0.json` is byte-identical (`cmp`) to `docs/api/icd.json`; `git diff ai_main..HEAD -- docs/api/baselines/icd-v1.7.0.json` is empty. Exactly one commit in the RC touches ICD_VERSION (9830572). **pass** (D4 / A4) — see Finding 6 for the mid-RC baseline mutation hazard.
- `docs/api/ICD_CHANGELOG.md` (new): 1.8.0 entry lists the five added routes, the additive `details` param on GET /verdicts, the halt-after envelope change, three response shapes, three body models. **pass** (D4).
- `docs/operator-api.md` banner reads ICD 1.8.0; census-derived route table present under generated markers; `docs/operator-commands.md` carries generated verb blocks for all 32 verbs. **pass** (D5).
- `docs/blocking-site-census.md`: row 34 reclassified CONVERT -> BLOCK-WORTHY citing the requirements' Enforcement Class; row 65 (sentinel leak check) CONVERT; rollup 65 sites / 36 BLOCK-WORTHY. Class matches the requirement that authorized it (SOP "Blocking Severity"). **pass**.

### Step 6.4 — executable acceptance criteria (shipped tree, worktree at 4b84d9c)

| Command | Exit | Result |
|---|---|---|
| `python3 team/scripts/lint_api_parity.py` | 0 | `census-walk — 0 violation(s)`; 22 flag-parity pairs; legacy + census-walk green (A1, convergence criterion) **pass** |
| `python3 team/scripts/lint_operator_verb_manifest.py` (on a copy of team/) | 0 | 33 verbs, 47 router routes, 0 drift **pass** |
| `python3 team/scripts/lint_icd_freshness.py` | 0 | icd.json fresh at 1.8.0 **pass** |
| `python3 team/scripts/lint_icd_compat.py` | 0 | compatible superset of 9 baselines 1.0.0-1.8.0 **pass** |
| `python3 team/scripts/generate_operator_verb_docs.py --check` | 0 | all 32 verb blocks + API reference match census, ICD, and each verb's --help (A4 "docs regenerated") **pass** |
| `python3 team/scripts/lint_doc_command_truth.py` | 0 | 711 citations / 14 docs, 0 findings; key-parity and pane-parity 0 findings (A4 "doc-truth green") **pass** |

### Step 6.5 — guard logic triggered (parity gate seeded on a copy of team/ at $PGAI_AGENT_KANBAN_TEMP_DIR/tester/seed-v2.25.0)

| Seeded gap | Gate result |
|---|---|
| halt row's route changed to `/operations/haltx` | exit 1: `[route_missing_census] route POST /operations/halt (fronting script 'halt.sh') is registered on the API but no active verb's census row declares it` — names the route/script. **pass** |
| halt row deleted (verb absent from census) | exit 1: route_missing_census + `[script_missing_census] script 'scripts/halt.sh' ...` + `[action_fixture_unknown_verb] ... 'halt'` — names verb, script, route. **pass** (D3 "verb absent from census fails") |
| halt schema_ref -> VerdictsResponse | exit 1: `[schema_mismatch] verb 'halt': ... resolves to keys [...] but census declares 'VerdictsResponse'` **pass** |
| new `scripts/frob.sh` with OPERATOR_VALID_FLAGS, no row | exit 1: `[script_missing_census] script 'scripts/frob.sh' ...` **pass** |
| new action row `frob` with no parity case | exit 1: `[action_fixture_missing] verb 'frob'` **pass** |
| parity case renamed to a verb the census lacks | exit 1: `[action_fixture_unknown_verb] ... 'frobnicate'` + `[action_fixture_missing] verb 'halt'` **pass** |
| `scripts/halt.sh` deleted, row + route kept | exit 2: `ERROR: Script not found: .../team/scripts/halt.sh / Verify the scripts directory is complete.` — fails loud naming the script path, but from the pre-existing legacy flag-parity map (present on ai_main), not the census-walk `route_missing_script` class, and without a machine key/remedy. **pass-with-caveat** |
| new `scripts/frob.sh` + active census row with ABSENT in every REST column (script without route) | exit 0 on both `lint_api_parity.py` and `lint_operator_verb_manifest.py` — **not gated**. See Finding 1. **gap** |

Every refusal above (except the exit-2 legacy path) printed the dual-channel `error: api_parity.census_gate_violation` / message / runnable remedy.

### Step 6.5 (continued) — production REST entrypoint vs production CLI on sandboxed synthetic boards

Probe script: `$PGAI_AGENT_KANBAN_TEMP_DIR/tester/probe-v2.25.0/probe.py` and `probe2.py` (real `uvicorn ... --factory` subprocess via `tests/fixtures/api_server.py` on an ephemeral loopback port, `build_synthetic_board` boards, `env -u PGAI_TASKS_DIR -u PGAI_PROJECT_ROOT`, kanban root pointed at the board). Output captured at `.../probe-v2.25.0/probe.out`.

- `GET /show?project=golden-fixture&key=<WORKING id>` stdout == `show.sh` stdout byte-for-byte. **pass**
- `GET /deep-verify-status` -> `{mode: weekly, entries: [], latest: null}`; `deep-verify-status.sh` -> `mode: weekly / latest: never-run / Ledger (0 entries)`. Same resolved data. **pass**
- `GET /verdicts?details=true` -> keys `posture, queue_tails, release_locks, verdicts, warnings`, posture populated from the board's campaign file. **pass**
- `GET /show-queues?details=true` -> `{details, project, warnings}`; `GET /metrics?format=json` -> JSON array; `GET /agent-metrics` -> `{agents, project, warnings, window}`. **pass**
- `POST /operations/release-locks {"release":"wake-nothing"}` -> 500 with `error=release_locks.not_found`, message, remedy lifted from the script. **pass** (D2 dual-channel on the new twin)
- `POST /operations/cancel-rc {"project":..}` (no key) -> 422 `cancel_rc.required_field_missing` with curl remedy; `POST /operations/cold-build-now {}` -> 422 `cold_build_now.project_required`. **pass**
- `POST /operations/halt-after {"project":"golden-fixture","until":"v99.0.0"}` -> **HTTP 422 `{"detail":[{"type":"missing","loc":["body","key"],...}]}`; no sentinel written.** `halt-after.sh --project golden-fixture --until v99.0.0` -> exit 0, writes `projects/golden-fixture/HALT-AFTER` = `rc:v99.0.0`. Same params, `until=not-a-version` -> same framework 422, no error/message/remedy. With `key:"rc"` added the REST twin behaves (the fixture sends it). See Finding 3. **bug**
- `POST /operations/halt|unhalt|defer|undefer|unwind-rc {"project":"ghost-project",...}` and `wake-now {"agent":"nobody"}` -> HTTP 500 with keys `exit_code, stderr, stdout, warnings` only; the script's dual-channel triple is present only as text inside `stderr`. `POST /operations/intake` missing fields -> framework `{"detail":[...]}`. See Finding 4. **gap**
- `GET /release-state` on a board with no release-state.md -> 422 `release-state.invalid-field` with a populated remedy; `release-state.sh` -> exit 3, same error/message, **`remedy:` empty**. See Finding 5. **bug**
- `GET /release-state?project=ghost-project` -> 404 `release_state.project_not_found`; CLI -> exit 1 `project_resolve.not_found`. Different machine keys for an unregistered project on a read verb; documented as NotApplicable in `test_verb_fixture_matrix.py`; D2's refusal-parity clause is stated for action verbs. **pass-with-caveat**

### Step 6.5 (continued) — acceptance A2 / A3 via the gated integration suite

The four golden pairs the acceptance names (why --details, rc-metrics with Blocked populated, agent-metrics per-task wall, show-queues details) and the halt-after sentinel byte-identity test all run inside `run-integration-tests.sh` (`test_why_details_rest_parity.py`, `test_rc_metrics_rest_parity.py`, `test_agent_metrics_rest_parity.py`, `test_show_queues_rest_parity.py`, `test_action_parity_fixtures.py::test_halt_after_until_arms_byte_identical_sentinel_content_over_rest_and_cli`, plus `test_golden_pair_harness.py::test_comparator_reports_a_diff_naming_the_differing_key_path_for_a_mismatched_pair` for the "CLI-vs-REST drift fails naming the path" half). Their result is read from the gated integration run below (Step 5a), never from direct pytest.

### Step 6.8 — synthetic release simulation

Skipped with note: the diff touches no release-lifecycle file (`cm/release.sh`, `cm/open-rc.sh`, `cm/cancel-rc.sh`, `lib/project_paths.sh` all unchanged; `git diff --stat ai_main..HEAD -- team/scripts/cm team/scripts/lib/project_paths.sh` is empty). The `team/scripts/` changes are the two gated runners (lint wiring + sentinel check), two new read-only scripts, `verdict-report.sh --details`, `lib/sentinel_leak_check.sh`, `lint_api_parity.py`, and the docs generator — none in the open/close cycle. `test_cm_release_synthetic.py` still executes inside the gated integration run; its line is quoted in Step 5a.

### Step 6.9 — constraint negative checks

- Loopback only: `tests/fixtures/api_server.py` binds `127.0.0.1` on an ephemeral port and refuses 8300; the probe's servers were `127.0.0.1:39133`, `:33033`. **pass**
- Production entrypoint / production CLI in fixtures: `api_server_fixture` launches `uvicorn pgai_agent_kanban.api.app:create_app --factory` as a subprocess; the golden-pair and action-parity tests invoke `bash <real script>` and the synthetic board symlinks the real `team/scripts/`. **pass**
- No secrets / no origin: `grep -rn "fetch\|push\|pull" team/tests/fixtures/action_parity_cases.py team/tests/fixtures/golden_board.py` — none. The suite's post-suite sentinel check reports the live root's HALT/HALT-AFTER/DEFER-LOW-BUGS set unchanged. **pass**
- `set -euo pipefail` present in `team/scripts/release-state.sh`, `deep-verify-status.sh`, `lib/sentinel_leak_check.sh` (`grep -l 'set -euo pipefail'`). **pass**
- Naming rule: no bug/task/version identifiers in new production code or test names (`git diff --name-only` grep); the identifiers found in `docs/operator-api.md` / `operator-commands.md` are example values (sanctioned exception), `golden_board.py`'s `CODER-20260101-00N-golden-*` and `BUG-0001-fixture-deferred-example` are fixture data. Hygiene lint (provenance / version-named-test / comment-drift) reports 0 in both runners. **pass**

### Step 6.10 — prerequisite graph

Every task ID in the 11 tickets' `## Prerequisites` resolves to an existing folder; the graph is acyclic (016 -> 017/018 -> 019/020/021/022 -> 023 -> 024 -> 025 -> 026 -> 028); bookends present: CM-20260927-027-open-rc-2-25-0 (DONE) at the start, TESTER-20260927-028 then CM-20260927-030-release-2-25-0 (WAITING on 026 + 028) at the end. Sequence numbers 016-030 contiguous, no collisions. **pass**

### Step 6.11 — spot-check outside the spec (time-boxed)

- State drift: `release-state.md` Active RC v2.25.0 matches the existing `ai_rc/v2.25.0` branch; RC Opened At equals the file's mtime to the second. None.
- Untested new mechanisms: `adapter.shell_out` stdin inheritance (flagged by CODER-022, confirmed in code, pre-existing) -> Finding 7. `generate_icd.py` re-freeze of a published baseline (flagged by CODER-025, confirmed in `git log` of the RC) -> Finding 6. `release-state.sh` refusal remedy divergence (found by probe) -> Finding 5.
- Regressions of prior gaps: BUG-0404 (integration test writes a guard record) — no record was written into this task's `logs/` by the unit run; the integration run is checked in Step 6.12. `docs/README.md` stale ICD link -> Finding 8 (pre-existing).
- Observation (not a finding): two stray local branches `ai_feature/CODER-20260926-017-provenance-sweep` and `ai_feature/CODER-20260927-005-provenance-sweep` belong to WONT-DO tasks and are not ancestors of the RC; harmless leftovers for CM/operator cleanup.

### Step 6.13 — skip-cites-real-bug

`lint_skip_bug_gate` in both runners: 0 placeholder IDs, 0 missing files. Manual grep: the only `BUG-NNNN` strings near `skip` are fixture literals inside `test_skip_bug_citation_scan.py` and `test_cm_known_issue_carry_forward_bash.py` (synthetic IDs the tests write themselves), and the two real citations BUG-0404 / BUG-0405 resolve to files. **pass**

### Step 6.14 — coding-standards spot-check

`release-state.sh`, `deep-verify-status.sh` carry `--help` naming every option and exit code; `lint_help_presence` 122/122 OK. No new hardcoded operational value noticed (ports are ephemeral, roots come from env). `parse_error_contract` was moved into `lib/error_contract.py` and reused by `reads.py` rather than duplicated. The legacy "Script not found" refusal in `lint_api_parity.py` (pre-existing, exit 2, no machine key) is the one borderline site — recorded as the pass-with-caveat in 6.5, not filed separately. **pass**

### Finding 1: Parity gate does not enforce "every script has a route"

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (the gate is the mechanism meant to make drift impossible; the hole recurs on every future verb ticket)
**Filed As:** BUG-0407 (Path C-A)

A new operator script plus an active census row with `ABSENT` in every REST column passes `lint_api_parity.py` and `lint_operator_verb_manifest.py` with 0 violations (Step 6.5 seed table, last row). Class 1 explicitly skips ABSENT rows. Deliverable 3 / acceptance A1 ("script without route ... fails naming the verb") is unmet for that direction. Every other seeded class fires naming the verb.

### Finding 2: Golden-pair fixture per read verb not delivered or gated

**Category:** gap
**Fix Effort:** medium (3-5 CODER tasks: read-parity ledger, parametrized module, gate class, seeded tests)
**Systemic Risk:** medium (parity of the four uncovered read verbs is unproven and can drift silently)
**Filed As:** BUG-0408 (Path C-A)

Read rows: 8. Golden pairs: why (+details), rc-metrics, agent-metrics, show-queues. Missing: show, verify-rc-state, release-state, deep-verify-status. The gate's fixture check (class 5) covers action rows only; CODER-023 and WRITER-026 both recorded the shortfall. A manual probe shows `show` is byte-identical and `deep-verify-status` agrees, so today's data parity is not in question — the fixture and gate are.

### Finding 3: REST halt-after requires `key`; the CLI's own parameter set is refused with a framework 422

**Category:** bug
**Fix Effort:** small (1 CODER task)
**Systemic Risk:** low (isolated to one route; fixture sends `key: "rc"` so the suite stays green)
**Filed As:** BUG-0409 (Path C-A)

`POST /operations/halt-after {"project": P, "until": "v99.0.0"}` -> `422 {"detail": [{"type": "missing", "loc": ["body", "key"]}]}`, no sentinel; `halt-after.sh --project P --until v99.0.0` -> exit 0, `HALT-AFTER` = `rc:v99.0.0`. Malformed `until` without `key` -> the same framework 422, no error/message/remedy. Acceptance A3 holds only when the caller supplies `key`. Pre-existing on ai_main (`required_keys=["project", "key"]`), not a regression, but the criterion this RC claimed is unmet as written.

### Finding 4: Twenty action routes still refuse without top-level error/message/remedy

**Category:** gap
**Fix Effort:** medium (3-5 CODER tasks; route-side lift is mechanical, the plain-stderr scripts are a separate sweep)
**Systemic Risk:** medium (Deliverable 2's dual-channel clause is what makes REST refusals machine-actionable; the split is recorded in the census itself)
**Filed As:** BUG-0410 (Path C-A)

Probe: `halt`, `unhalt`, `defer`, `undefer`, `unwind-rc` (ghost project) and `wake-now` (unknown agent) return HTTP 500 with keys `exit_code, stderr, stdout, warnings` only — the triple exists solely as text inside `stderr`; `intake` with missing fields returns `{"detail": [...]}`. Only halt-after, cold-build-now, cancel-rc, release-locks carry the triple (verified for release-locks and cancel-rc/cold-build-now refusals).

### Finding 5: release-state CLI prints an empty remedy where the REST twin's is populated

**Category:** bug
**Fix Effort:** small (1 CODER task)
**Systemic Risk:** low
**Filed As:** BUG-0411 (Path C-A)

Same missing-file refusal: REST 422 body `remedy: "(file): create .../release-state.md with the schema headings: ..."`; `release-state.sh` stderr `remedy:` (empty), exit 3. One resolver, two renderers that disagree — and no golden pair for the verb to catch it (Finding 2).

### Finding 6: ICD generator re-freezes a published baseline; the 1.7.0 file was rewritten five times mid-RC

**Category:** bug (pre-existing; Regression: no)
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (recurs on every RC that adds routes before its bump ticket; correctness depended on a CODER restoring the file by hand)
**Filed As:** BUG-0412 (Path C-A)

`git log ai_main..HEAD -- docs/api/baselines/icd-v1.7.0.json` lists c253f9d, 1d6021a, 48ffffe, ac082fc, 5980883 (mutations) and 9830572 (restoration); the shipped tree's diff for that file against ai_main is empty, so D4's immutability law holds on what ships. `generate_icd.py`'s docstring says freezing is unconditional; CODER-025 recorded the same happened in v2.23.11 / v2.23.14.

### Finding 7: `adapter.shell_out()` inherits the server's stdin

**Category:** bug (pre-existing, unexercised; Regression: no)
**Fix Effort:** small (1 CODER task)
**Systemic Risk:** low
**Filed As:** BUG-0413 (Path C-A)

`subprocess.run(argv, capture_output=True, text=True)` without `stdin=subprocess.DEVNULL` (`team/pgai_agent_kanban/api/adapter.py`, same on ai_main). The only known prompting script fronted by a route (`cm/cancel-rc.sh`) is gated at the API layer by the `yes` field, so nothing hangs today; rated medium per SOP calibration for a real but unexercised defect.

### Finding 8: `docs/README.md` links the ICD material as section 8; it is section 9

**Category:** bug (pre-existing; Regression: no)
**Fix Effort:** small
**Systemic Risk:** low
**Filed As:** BUG-0414 (Path C-A)

Line 13 anchor `#8-icd--the-published-api-contract` vs heading `## 9. ICD — the published API contract` (already section 9 on ai_main). WRITER-026 edited line 12 of the same file and recorded the link as outside its named files.

### Finding 9: Step 6.12 (5) YES — one refused remote attempt under role coder

**Category:** bug (process; boundary held)
**Fix Effort:** small
**Systemic Risk:** low (refused attempt, no ref moved, single task, ad-hoc run rather than a suite mechanism)
**Filed As:** BUG-0415 (Path C-A)

`tasks/CODER-20260927-023-action-parity-fixtures/logs/git-boundary-guard.jsonl`: `{"timestamp":"2026-09-27T13:39:04Z","role":"coder","task":"CODER-20260927-023-action-parity-fixtures","argv":["git","-C","$HOME/develop/my-project","fetch","origin"]}`. Bare `fetch origin` = `cm/cancel-rc.sh:329` / `cm/open-rc.sh:365`; `$HOME/develop/my-project` = `project.cfg_example:34`; CODER-023's progress log records scratch runs over all 24 action CLIs at that time. Rated low per the reference doc ("a refused attempt ... is low"); does not indicate a defect that recurs on every build, so it does not by itself elevate beyond SHIP-WITH-CONCERNS.

## Step 5a — gated suites (the only authoritative pass/fail signal)

Run from the worktree root with `PGAI_DEV_TREE_PATH` set to it, output teed under `$PGAI_AGENT_KANBAN_TEMP_DIR/tester/`:

| Runner | Exit | pytest line | Pre-flight |
|---|---|---|---|
| `bash team/scripts/run-unit-tests.sh --coverage` | **UNIT_EXIT=0** | `5161 passed, 43 skipped in 882.20s` | every lint OK (dockerfile_static, container_engine_free, api_parity census-walk 0, test_anti_patterns 0/581, skip_cites_real_bug, orphan_tests, operator_verb_manifest 0 drift, icd_freshness, icd_compat 9 baselines, operator_verb_docs_freshness, dashboard_window_census, changelog_freshness, doc_command_truth 0, engine_size_dupe, env_bootstrap, help_presence 122/122, hygiene 0, python_import_completeness, dashboard gates); post-suite sentinel_leak OK, scratch_sweep OK |
| `bash team/scripts/run-integration-tests.sh --coverage` | **INTEGRATION_EXIT=0** | `588 passed, 1 skipped in 756.30s` | every lint OK incl. rc_empty_commits, governance_byte_budget, bare_tmp_cleanliness, listener_cleanliness; post-suite sentinel_leak OK (live root HALT / HALT-AFTER / DEFER-LOW-BUGS set unchanged) |

Acceptance tests inside the gated integration run (all dots, no F): `test_action_parity_fixtures.py` 23 (incl. `test_halt_after_until_arms_byte_identical_sentinel_content_over_rest_and_cli`, `test_rest_action_refuses_a_malformed_parameter_like_its_cli_twin`, `test_every_census_action_verb_has_an_effect_or_refusal_parity_case`), `test_why_details_rest_parity.py` 2, `test_rc_metrics_rest_parity.py` 1 (Blocked populated), `test_agent_metrics_rest_parity.py` 1 (per-task wall), `test_show_queues_rest_parity.py` 1, `test_golden_pair_harness.py` 6 (incl. the mismatched-pair test that asserts the differing key path is named), `test_cm_release_synthetic.py` 17 (Step 6.8 reference). **A2 pass; A3 pass as fixtured (with `key: "rc"` supplied — see Finding 3 for the CLI-minimal form).**

Logs: `$PGAI_AGENT_KANBAN_TEMP_DIR/tester/unit-v2.25.0.log`, `.../integration-v2.25.0.log`, exit files `.../unit-v2.25.0.exit`, `.../integration-v2.25.0.exit`.

## Step 5b — coverage (informational only)

Source: `totals` in `$PGAI_AGENT_KANBAN_TEMP_DIR/coverage/unit.json` (written 16:11) and `.../integration.json` (written 16:24); field names verified (`percent_statements_covered_display`, `percent_branches_covered_display`; `percent_covered` is the blended figure).

| Suite | Line (statements) | Branch | Blended (`TOTAL` Cover column) |
|---|---|---|---|
| unit | 77 % | 63 % | 74 % (17673 statements, 5292 branches) |
| integration | 13 % | 10 % | 12 % |

Coverage does not enter the verdict.

## Step 5c — see `## Lint Findings (non-blocking)` below.

## Steps 6.6 / 6.7 — runner exit codes confirmed

`UNIT_EXIT=0`, `INTEGRATION_EXIT=0` (both from the runners' own exit status, captured to files). Both suites PASS. No stale-assertion, pre-existing-failure, or infrastructure condition arose.

## Step 6.12 — autonomous operation criterion

| # | Question | Answer | Evidence |
|---|---|---|---|
| 1 | Tasks force-promoted WAITING -> BACKLOG by hand? | NO | Every ticket's `progress.log` records its own `task-status.sh` transitions; queue markers `[x]` for 016-027 and `[A]` for 028 agree with each `status.md`; this task's own status carried the wake script's "Promoted from WAITING to BACKLOG by wake script" line at start. |
| 2 | `release-state.md` edited outside open-rc / release / cancel-rc? | NO | File mtime `2026-09-27 09:32:35.176` equals `## RC Opened At 2026-09-27T09:32:35+00:00` written by `cm/open-rc.sh` (CM-20260927-027). |
| 3 | Scripts patched mid-build? | NO | All 29 RC commits are the 11 tickets' feature commits and `--no-ff` merges (author identity the same for every agent commit); every `team/scripts/` change belongs to a ticket in the plan (parity gate, runners' lint wiring, sentinel check, new read scripts, docs generator). |
| 4 | Queue files hand-edited? | NO | `coder_backlog.md` 377-386, `writer_backlog.md` 93, `cm_backlog.md` 178, `tester_backlog.md` 95 carry the wake script's canonical marker shapes consistent with task states. |
| 5 | Non-CM agent invoked fetch/pull/push? | **YES** | One record: `tasks/CODER-20260927-023-action-parity-fixtures/logs/git-boundary-guard.jsonl` — role `coder`, `git -C $HOME/develop/my-project fetch origin`, refused by the guard, no ref moved. Filed as BUG-0415 (Finding 9), rated low. This task's own `logs/` holds no guard record after both suites ran (the BUG-0404 mechanism did not recur). |

Autonomous operation criterion: Manual interventions required: none. One refused boundary crossing recorded (question 5), not an intervention and not a recurring build defect.

## Step 8 — how the recommendation was reached

Suite precondition met (both runners 0). Findings include gaps and bugs (Findings 1-9), so `PASS` is excluded. None is critical: no data corruption, no broken release pipeline, no security defect, no autonomy failure that makes future cycles unreliable; the shipped tree is internally consistent (parity lint zero gaps, ICD immutable on what ships, docs regenerated, all fixtures green). The gaps are parity-completeness shortfalls against the requirements' own wording (script-without-route not gated, read-verb golden pairs absent for four verbs, dual-channel refusals on 4 of 24 action routes, halt-after's REST twin refusing the CLI's minimal form) plus pre-existing defects surfaced by the work. Recommendation: **SHIP-WITH-CONCERNS**; follow-ups BUG-0407..BUG-0415 enter the next iteration.

## Lint Findings (non-blocking)

None. `findings-unit.json` (generated 2026-09-27T16:11:58+00:00) and `findings-integration.json` (2026-09-27T16:25:01+00:00) both carry `"findings": []`.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=20d freshness=stale — last cold build failed at v2.9.20 (docker/claude-abliterated Dockerfile parse error and podman/rocky9 COPY step, the run that filed BUG-0167, now closed) and the record is stale on both counts (20d exceeds the 7-day window, and v2.9.20 is not the v2.25.0 under verification), so nothing is known about whether the three Dockerfiles this tree ships build today.

## Caveats

- **Working-directory / git-workflow override.** The wake prompt named a detached worktree and forbade a feature branch; the role file's "Git workflow" expects artifacts committed and merged into the local RC. Precedence (direct instruction on the current task) was followed: report.md, gaps.md and task-notes.md live only in the task folder; nothing was committed; the worktree remains clean at 4b84d9c with no stash. CM should not expect a TESTER merge commit on `ai_rc/v2.25.0`.
- **Requirements "Host Matrix"** (gitless install, API served from the install) was not exercised; TESTER verifies the dev tree. Out of scope for this report; no finding.
- **"Operator-auth'd exactly as today's API is"**: no auth dependency exists in the API; the new routes carry the same empty dependency set and loopback-only binding (CODER-022 tested dependency-set parity). Holds trivially; pass-with-caveat.
- **"Docs and the API both derive from" the census**: the docs do (generator); the routers are hand-written and *reconciled* against the census by the BLOCK-WORTHY gate. The convergence criterion (zero gaps) is met; pass-with-caveat on the word "derive".
- **ICD bump "applied at promotion"**: applied by CODER-025 inside the RC rather than by CM at promotion; exactly one bump, published numbers immutable on the shipped tree; pass-with-caveat.
- **Deleted-script seed**: fails loud (exit 2, "Script not found: <path>") from the pre-existing legacy flag-parity map before the census-walk's `route_missing_script` class runs; names the script path, not the verb, and carries no machine key. Recorded as pass-with-caveat; not filed.
- **Read-verb not-found keys** differ between CLI (`project_resolve.not_found`) and REST (`release_state.project_not_found` etc.); documented as NotApplicable in `test_verb_fixture_matrix.py`; D2's refusal-parity clause is stated for action verbs. Not filed.
- **Stray local branches** `ai_feature/CODER-20260926-017-provenance-sweep`, `ai_feature/CODER-20260927-005-provenance-sweep` (WONT-DO tasks, not RC ancestors) — cleanup for CM/operator; not a finding.
- **Live root sentinel**: `projects/pgai-agent-kanban/DEFER-LOW-BUGS` exists at the live root; the suites' sentinel check reports it unchanged. Operator campaign state, not a finding.
- Scratch/diagnostic artifacts: `$PGAI_AGENT_KANBAN_TEMP_DIR/tester/{seed-v2.25.0,probe-v2.25.0,file-v2.25.0}`; nothing written under bare `/tmp`.
- Prior-run artifacts: none existed (`report.md.previous-RUN-N` absent); this is the first verification of v2.25.0.
