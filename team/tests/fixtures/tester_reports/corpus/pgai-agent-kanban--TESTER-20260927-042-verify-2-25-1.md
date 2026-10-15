# Verification Report: rc/v2.25.1

_RUN-2 (fresh verification). Prior-run artifacts `report.md.previous-RUN-1` / `gaps.md.previous-RUN-1` were rotated by the wake script and were not used as input; every check below was re-run in this session. Worktree: `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260927-042-verify-2-25-1` detached at `14f307eb342f091824b51337cea41ccff3319829` == `ai_rc/v2.25.1`._

## Summary

Fresh RUN-2 verification of `ai_rc/v2.25.1` (RUN-1 was discarded mid-finalize by a lock-loss; see Findings 12–13). The RC bundles eleven bug fixes (BUG-0405–0415). All eleven verify: the parity gate now refuses an active verb with no REST twin and every read verb has a golden pair (Findings 1–2); `POST /operations/halt-after` takes the CLI's minimal form and all 24 action routes refuse dual-channel (Findings 3–4, exercised over the real uvicorn entrypoint on a synthetic board); the ICD generator refuses to re-freeze a published baseline and `--refreeze` overrides it (Finding 5); fronted scripts no longer inherit the server's stdin (Finding 6); non-CM version lookups no longer spend a guard record and the temp-root resolver rejects filesystem-root spellings (Findings 7–8); release-state refusals are byte-identical CLI↔REST (Finding 9); the docs index links the ICD section (Finding 10); the CM scripts skip the pre-flight fetch under a non-CM role (Finding 11). Structural checks pass (559 `.py` compile, 297 `.sh` `bash -n`), the synthetic release simulation passes, and the prereq graph is sound. HOWEVER the gated unit runner is RED (`UNIT_EXIT=1`): three `test_lint_changelog_freshness.py` tests fail because regenerating `CHANGELOG.md` from the live `bugs/` dir reissues the published `KI-1.23.0.1` to a newly-filed bug (BUG-0420, open) — the verification lane's own bug-filing reds the build gate, and a published Known-Issue identifier would silently change meaning on release-time regeneration. That fails the suite-pass precondition and, with the recurring chain-lock-loss rework (BUG-0422, new), drives the recommendation and a high systemic-risk rating.

## Recommendation

SHIP-WITH-SERIOUS-CONCERNS

## Systemic Risk

high

## Findings

### Pre-flight (Steps 2–3) — pass

- HEAD `14f307e` == `ai_rc/v2.25.1`; `git status --porcelain` empty; `git stash list` empty.
- Every task with `## State: DONE` and a named feature branch: branch absent locally (deleted post-merge) — passes both checks trivially. Two lingering `ai_feature/CODER-2026092[67]-0{17,05}-provenance-sweep` branches belong to WONT-DO tasks (exempt).
- Diff `ai_main..ai_rc/v2.25.1`: 51 files, 22 non-merge commits, 10 `--no-ff` merges (one per input task 031–040).

### Requirements acceptance criteria

- "All Python files compile cleanly": `python3 -m py_compile` over all 559 tracked `.py` files — 0 failures. **pass**
- "All bash files pass syntax check": `bash -n` over all 297 tracked `.sh` files — 0 failures. **pass**
- "Each bundled item is addressed": per-bug behavioural findings below.

### Bundled items — behavioural verification (Step 6.4 / 6.5)

Every probe ran in this session against the worktree at `14f307e`; scratch copies/clones under `$PGAI_AGENT_KANBAN_TEMP_DIR/projects/pgai-agent-kanban/tester/`. HTTP probes used the production entrypoint (`uvicorn pgai_agent_kanban.api.app:create_app --factory` via `tests/fixtures/api_server.py`) over `build_synthetic_board` (project `golden-fixture`); output captured in `.../tester/http-probe-run2.out`.

#### Finding 1: BUG-0407 — parity gate refuses an active verb with no REST twin — **pass**
- Shipped tree: `python3 team/scripts/lint_api_parity.py` exit 0 (census-walk 0 violations).
- Seed (copy of `team/`, `scripts/frob.sh` + active row with all REST columns `ABSENT`): exit 1, `[verb_missing_route] verb 'frob': census row is active for script 'scripts/frob.sh' but declares no REST twin`, dual-channel `api_parity.census_gate_violation` + remedy.
- Same row as `planned`: exit 0. Half-declared twin (`GET` + `ABSENT` route): exit 1 naming `frob`.
- `lint_operator_verb_manifest.py` still reports 0 drift on an active-ABSENT row (CODER-031 noted this as out of scope; the parity gate now catches it, so no gap).

#### Finding 2: BUG-0408 — read verbs have golden pairs and the gate demands one — **pass**
- `team/tests/fixtures/read_parity_cases.py` (new, 550 lines) + `team/tests/integration/test_read_parity_fixtures.py`; census has 8 active read rows (`show`, `show-queues`, `why`, `verify-rc-state`, `rc-metrics`, `agent-metrics`, `release-state`, `deep-verify-status`).
- Seed: renaming the `why` case → exit 1 with `[read_fixture_missing] verb 'why'` AND `[read_fixture_unknown_verb] ... 'why-removed'`. Appending an active routed read row `probe-read` with no case → exit 1 `[read_fixture_missing] verb 'probe-read'`.
- `grep -ci golden lint_api_parity.py` = 9 (was 0).

#### Finding 3: BUG-0409 — `POST /operations/halt-after` accepts the CLI's minimal form — **pass**
- `{"project": P, "until": "v99.0.0"}` → HTTP 200; `projects/P/HALT-AFTER` = `rc:v99.0.0\n`, byte-identical to what `halt-after.sh --project P --until v99.0.0` wrote on the same board.
- `{"project": P, "until": "not-a-version"}` (no `key`) → HTTP 500 with top-level `error=halt-after.until_malformed`, message, remedy; no sentinel written.
- `{"until": "v99.0.0"}` (no project) → HTTP 422 `error=halt_after.project_required` + message + runnable curl remedy; no `detail` key.
- Old normal case (`key: "rc"` explicit) still arms the sentinel.

#### Finding 4: BUG-0410 — every action route refuses dual-channel — **pass**
- `awk` over the manifest: all 24 action rows `DualChannelShellEnvelope`.
- Ghost-project refusals on `halt`/`unhalt`/`undefer` → HTTP 500 with top-level `error=project_resolve.not_found`; `wake-now {"agent":"nobody"}` → `wake_now.unknown_agent`; `release-locks` → `release_locks.not_found`.
- Sweep: 19 action routes POSTed with `{}` (excluding `halt-global`/`unhalt-global` (no params) and the spawn-capable `wake-now`/`deep-verify`/`cold-build-now`) — all 19 return HTTP 422 with non-empty `error`/`message`/`remedy` and no `detail` key (`halt.project_required` … `ship_rc.project_required`; `cancel-rc` keeps its pre-existing `cancel_rc.required_field_missing` key — observation only).
- API-layer gates also dual-channel: `reset.selector_required`, `remove_project.force_required`, `close.key_required`, `intake.filename_required`.

#### Finding 5: BUG-0412 — published ICD baseline is never re-frozen by regeneration — **pass**
- Scratch clone (`git clone` of the worktree, detached at the RC, `ai_main` recreated). Untouched: `generate-icd.sh` exit 0, tree unchanged.
- Schema-visible change (a `Field(description=…)` in `reads.py`) at published `ICD_VERSION=1.9.0` (listed in `SUPPORTED`): `generate-icd.sh` exit 1, `error: generate_icd.published_baseline_immutable`, message naming `icd-v1.9.0.json`, remedy printing the bump step (`printf '1.10.0\n' > docs/api/ICD_VERSION && bash team/scripts/generate-icd.sh`); `icd.json` rewritten, `icd-v1.9.0.json` sha unchanged.
- `--refreeze` at the same state: exit 0 and the published baseline IS rewritten (explicit override, as documented).
- Bump to `1.10.0`: exit 0, `icd-v1.10.0.json` created == `icd.json`; all ten older baselines byte-identical to git.
- Rule e (`lint_icd_compat.py`): mutating `icd-v1.8.0.json` → exit 1 naming the file, "differs from its copy on ai_main", restore command in remedy. Runner wires it via `gated_runner_run_quality_lint "icd_compat"` (quality class, runner exit unaffected) — consistent with census row 33 CONVERT.
- Shipped tree: `lint_icd_compat` ok (10 baselines), `lint_icd_freshness` ok, `docs/api/icd.json == baselines/icd-v1.9.0.json`, both `ICD_VERSION` files `1.9.0`.

#### Finding 6: BUG-0413 — fronted scripts never inherit the server's stdin — **pass**
- With fd 0 replaced by a never-written pipe (the TTY hazard made real): `adapter.shell_out(<script with read -r>, {})` returns in 0.00 s, `exit_code=3`, stderr carries the script's own message (`read rc=1`). The same stub under inherited stdin hangs (3 s timeout hit) — original failure mode reproduced.
- `adapter.py` `subprocess.run(..., stdin=subprocess.DEVNULL)`; `routers/operations.py` `_spawn_detached` and the intake route's inline `subprocess.run` both pass `stdin=subprocess.DEVNULL` (`ai_main` adapter had 0 occurrences). New `api/tests/test_stdin_closed.py` (5 tests; CODER's summary says 6 — count mismatch is cosmetic).

#### Finding 7: BUG-0405 — non-CM version lookup never spends a guard record — **pass**
- `wd_install_git_boundary_guard` exports `PGAI_GIT_LOCAL_ONLY=1` for `coder`/`tester`/`pm`/`writer`; for `cm` it is neither set nor cleared (pre-set value survives — matches the header's "never touched for cm").
- Lagging-install simulation: copy of `team/scripts/lib/` with the `_plrv_role_lc` condition removed. Old shape, no flag, role coder → `v2.25.0` and **1** refusal record (`fetch origin --tags --quiet`) = the original failure mode. Old shape + flag → `v2.25.0`, 0 records. New shape, no flag, coder → 0 records (role check).
- Guard still refuses a genuine non-CM `git fetch` with the flag set: dual-channel `git-boundary-guard.non-cm-remote-verb`, exactly one record (throwaway evidence file — see Autonomous criterion).
- `discovery_guards.sh` `_disc_git_tag_exists` honours the same flag (comment updated). Tests: `test_wake_guard_wiring.py` (9), `test_non_cm_version_lookup_never_reaches_origin.py` (3).

#### Finding 8: BUG-0406 — temp-root resolver contract tested; filesystem-root spellings rejected — **pass**
- `kanban.cfg` `tmp_root=/`,`tmp_subdir=/` → `pgai_configured_temp_root` = `/tmp/pgai_kanban_tmp` (was `///`); `tmp_root=/tmp`,`tmp_subdir=..` → fallback; tier-1 override `///` → `pgai_temp_dir` falls back; bare `/`+empty → fallback; `PGAI_AGENT_KANBAN_TEMP_DIR=/projects/name` ignored by `pgai_configured_temp_root` (returns the configured root) while `pgai_temp_dir` honours it.
- New `team/tests/unit/test_temp_root_resolution.py` (11 tests). One quality-lint hit at line 63 (AP3 literal) — see Lint Findings, cites BUG-0421.

#### Finding 9: BUG-0411 — release-state refusal parity CLI vs REST — **pass**
- Three malformed boards (`Active RC=NONE`, missing headings, missing file): `release-state.sh --project P` exit 3 with an `error:/message:/remedy:` block whose parsed triple `==` the `GET /release-state?project=P` HTTP 422 body's `error`/`message`/`remedy` exactly; remedy non-empty in all three. One renderer (`error_contract.dual_channel_from_text`) with shared key/remedy constants in `ops/read.py`; new `test_release_state_refusal_parity.py`.
- The filed symptom (empty remedy) did not reproduce for CODER-039 either; the change makes parity structural. Recorded as pass.

#### Finding 10: BUG-0414 — docs index links the ICD section — **pass**
- `docs/README.md:13` → `operator-api.md#9-icd--the-published-api-contract`; heading `## 9. ICD — the published API contract` → GitHub anchor `9-icd--the-published-api-contract` (computed); no `§ 8`/`#8-icd` remains; ICD cited as 1.9.0.

#### Finding 11: BUG-0415 — CM scripts skip the pre-flight fetch under a non-CM role — **pass (code + test evidence; not run by hand)**
- `cancel-rc.sh` and `open-rc.sh`: `elif pp_agent_role_is_non_cm; then echo "PGAI_AGENT_ROLE=… — skipping git fetch origin …"`; helper: `coder`/`TESTER` → skip, `cm`/`CM`/unset/empty → fetch.
- New `team/tests/integration/test_cm_scripts_skip_fetch_under_non_cm_role.py` (3 tests) drives both scripts through the real shim; covered by the gated integration runner. Per `team/roles/CODER.md:138` (this RC) a non-CM role never runs these scripts by hand, so no manual probe was made.
- Noted by CODER-039 and confirmed by reading: later `push`/`ls-remote` calls in those scripts are untouched (scoped by the ticket's constraint) — observation, not a gap against BUG-0415's constraint.

### Step 6.1 — release-state sanity — pass
`release-state.md`: Active RC `v2.25.1`, RC Opened At `2026-09-27T17:09:38+00:00`, RC Opened By Task `CM-20260927-041-open-rc-2-25-1`, Last Released `v2.25.0` (well-formed). `pp_last_released_version pgai-agent-kanban` (local-only) → `v2.25.0`; `semver_gt v2.25.1 v2.25.0` true. File mtime equals the RC-open timestamp.

### Step 6.3 — documentation-layer review — pass
`docs/operator-api.md`: halt-after row/notes now say `key` optional (defaults to `rc`); "Refusals on every action route" paragraph describes `<verb>.refused` fallback and `<verb>.<field>_required` 422 shape and explains why the generated table marks no POST field required; findings list gains the missing-route and read-golden-pair bullets; section 9 rewritten around baseline immutability with `--refreeze`; 1.9.0 support row. `docs/api/ICD_CHANGELOG.md` 1.9.0 entry additive, above untouched 1.8.0. `docs/blocking-site-census.md` rows 33/34 extended; eight new BLOCK-WORTHY anchors listed in the Locator Index; `lint_gate_census_compliance.py` → `[]`. Manifest header now permits ABSENT REST columns on planned rows only. `team/roles/CODER.md:138` adds the "never run CM scripts by hand" sentence. `generate_operator_verb_docs.py --check` OK (32 verbs). Stale `17 POST /operations/* routes` count in section 6.1 remains (census has 24) — already filed as BUG-0417 (open), cited, not re-filed.

### Step 6.8 — synthetic release simulation — pass
Diff touches `team/scripts/cm/open-rc.sh` and `cm/cancel-rc.sh`; `team/tests/integration/test_cm_release_synthetic.py` → 17 passed (direct pytest, debugging aid; also inside the gated integration run).

### Step 6.9 — constraint negative checks — pass
No secrets/tokens in added lines (only OpenAPI descriptions matched the grep); `set -euo pipefail` retained in every changed executable script (the three `lib/*.sh` files are sourced libraries); no hardcoded `ai_main` in new production code (prefix read via new `pgai_agent_kanban.lib.branch_prefix`).

### Step 6.10 — prerequisite graph — pass
Bookends: `CM-20260927-041-open-rc-2-25-1` (no prereqs) → 031…040 (each prereq on 041 and on its ordered predecessor) → `TESTER-20260927-042` (prereqs 031–040) → `CM-20260927-044-release-2-25-1` (prereqs 031–040 + 042). Every referenced ID exists; no cycles; sequence numbers 031–044 monotonic, no collisions.

### Step 6.13 — skip-cites-real-bug — pass
`lint_skip_bug_gate.sh` OK (0 missing files); no `BUG-SKIP` placeholders. `BUG-1234`/`BUG-1235` appear only as the lint's own fixtures inside `test_skip_bug_citation_scan.py`.

### Step 6.14 — coding-standards spot-check — pass
No bug/task/version identifiers in new production code or new tests; `branch_prefix.py` is a shared lib (replaces a private copy in `lint_changelog_freshness.py`), not a CLI; `--refreeze` and the baseline policy documented in `generate-icd.sh` usage; new tunable `PGAI_GIT_LOCAL_ONLY` export documented in the wiring header, `project_paths.sh` and `discovery_guards.sh`.


### Step 5a — gated runners (authoritative pass/fail)

Both run from the worktree root with `--coverage`; output teed under `$PGAI_AGENT_KANBAN_TEMP_DIR/projects/pgai-agent-kanban/tester/`.

- **`run-unit-tests.sh --coverage` → `UNIT_EXIT=1`** (17m10s): **3 failed, 5264 passed, 42 skipped**. All three failures are in `team/tests/unit/test_lint_changelog_freshness.py` (`TestChangelogFreshnessPositive::test_fresh_artifact_exits_0`, `::test_fresh_artifact_prints_ok_message`, `TestRCModeTolerance::test_rc_mode_tolerates_new_release_section_addition`). See Finding 12.
- **`run-integration-tests.sh --coverage` → `INTEGRATION_EXIT=0`** (15m44s): **619 passed, 1 skipped**. But its post-suite `sentinel_leak` check flagged that the suite removed the live root `HALT` — see Finding 14.

#### Finding 12: gated unit runner is RED — changelog Public-ID collision (BUG-0420) surfaced as a hard suite failure — **bug (serious)**

**Category:** bug
**Fix Effort:** small (1–2 CODER tasks, or a one-field data correction by CM/operator)
**Systemic Risk:** high (a normal verification-lane action — filing a Path C bug — reds the gated unit runner; the autonomous loop's own output breaks its build gate)
**Filed As:** cites BUG-0420 (open, Path C from RUN-1) — the changelog generator reuses a published Public ID; not re-filed (Step 5c "cite an open bug, or file one")
**Regression:** no
**Integrity:** breakage

The three `test_lint_changelog_freshness.py` failures and the runner's own `changelog_freshness` lint pre-flight finding are one phenomenon: `lint_changelog_freshness.py` (and the three env-coupled unit tests that invoke it against `_LIVE_KANBAN_ROOT`) regenerate `CHANGELOG.md` from the LIVE `bugs/` directory and diff it against the committed file. The live `bugs/` dir now contains `BUG-0417-operator-api-stale-route-count.md` (filed by RUN-1) carrying `## Affects: v1.23.0` and a persisted `## Public ID: KI-1.23.0.1`. The committed `CHANGELOG.md:1428` already publishes `KI-1.23.0.1` for a different, resolved defect ("testing-only intake items are closed by the machinery…"). The regeneration therefore reissues `KI-1.23.0.1` to BUG-0417, the committed file is no longer byte-fresh, and the gate exits 1. First divergent lines captured in `findings-unit.json`.

Why this is `bug`/high and not a quality-only lint note:
- It fails the **gated unit runner** (`UNIT_EXIT=1`), which is the authoritative pass/fail signal. PASS is therefore impossible (Step 8 suite-pass precondition).
- The trigger is the verification lane's own normal output: RUN-1 filing a Path C bug (BUG-0417) with a version-shaped `## Affects` reds the next run's gated suite. That an autonomous verification action breaks the build gate is an autonomy-reliability defect.
- On CHANGELOG regeneration at release, a **published** Known-Issue identifier (`KI-1.23.0.1`) silently changes meaning — a published-artifact integrity hazard. CM-20260927-044's own status already flags awareness of BUG-0420.

Not the RC's shipped code: `lint_changelog_freshness.py`, `changelog_writer`, and the committed `CHANGELOG.md` on `ai_rc/v2.25.1` are correct; the redness is entirely driven by live `bugs/`-directory data (BUG-0417's mis-set `Affects`/`Public ID`) plus the pre-existing writer ID-reuse behaviour (BUG-0420). BUG-0417's `## Affects: v1.23.0` is itself questionable — a currently-stale doc count is not a v1.23.0 published Known Issue — and correcting or clearing that field (or fixing BUG-0420's generator so it never reuses a published ID) turns the gate green. This is a data/tooling fault an operator or CM can resolve in one edit; it is not a defect in the eleven bundled fixes, all of which verify (Findings 1–11).

The runner also emitted three warnings that pre-date this cycle (`BUG-0393`, `BUG-0395`, `BUG-0401` carry multi-version `## Affects` values the generator cannot map to a single release — "no Public ID assigned"); these are non-fatal warnings, not the cause of the exit-1.

#### Finding 13: repo chain-lock lost mid-run forces rework — **bug**

**Category:** bug
**Fix Effort:** medium (3–5 CODER tasks — lock lifecycle across the subagent invocation)
**Systemic Risk:** medium (recurred 3× in one cycle; self-heals via re-dispatch, bounded by the zero-progress cap)
**Filed As:** BUG-0422 (Path C-A) — cites BUG-0416 (open) as the downstream marker-heal symptom
**Regression:** no
**Integrity:** none

Three chains lost the per-project repo lock mid-finalize this cycle (CODER-037 at 00:43Z and 01:20Z; TESTER-042 RUN-1 at 02:00:52Z), each stopping before finalize and discarding completed work: CODER-037 needed a second session after its merge (`af43f59`) already landed; TESTER-042 RUN-1's complete verification (report written, SHIP-WITH-CONCERNS) was discarded and re-dispatched as this RUN-2. A CM chain acquired the same lock cleanly at 01:59:30Z while TESTER RUN-1 still believed it held it. `lock_provenance.sh` and `wake_driver.sh` are byte-identical across `ai_main`, the RC, and the live install (not in the RC diff), so this is pre-existing, not a v2.25.1 regression. Full detail, files, and acceptance in BUG-0422.

### Step 5b — coverage (informational only)

- Unit: line 74.2% / branch (from `percent_branches_covered`) — `percent_covered=74.2%` blended-per-`totals`; `percent_branches_covered` reported in `unit.json`. num_statements 18177.
- Integration: line/blended `percent_covered=12.7%`, branch `percent_branches_covered=10.5%` (`integration.json`, num_statements 18168). Low because the integration suite exercises a narrow slice of the package; informational only.
Coverage never enters the verdict.

### Step 5c — lint findings (ingested; non-blocking)

`findings-unit.json` (2 findings) and `findings-integration.json` (3 findings).
- `test_anti_patterns` (severity quality) — `team/tests/unit/test_temp_root_resolution.py:63` [AP3] hardcoded `/tmp/pgai_kanban_tmp` literal — cites **BUG-0421** (open, Path C from RUN-1).
- `changelog_freshness` (severity quality) — CHANGELOG stale vs regeneration; this is the SAME phenomenon as Finding 12. Because it also fails the gated unit runner (exit 1), it is treated as a Step-7 `bug` (Finding 12), not laundered into this non-blocking section — cites **BUG-0420** (open).

### Step 5d — deep-verify freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=20d freshness=stale — last cold-build attempt failed at v2.9.20 (a `docker/claude-abliterated` Dockerfile `FROM` parse error and a `docker/rocky9` COPY step); stale on both counts (20d exceeds the 7-day window, and v2.9.20 is not the v2.25.1 under verification), so nothing is known about whether this tree's images build cold. The failing run owns its own bug; TESTER does not re-file (this project's `deep_verify=false`, and the lane is CM's DEEP-VERIFY tier). Not a finding, not a block.

#### Finding 14: gated integration suite removed the live fleet HALT — **bug**

**Category:** bug
**Fix Effort:** medium (find and pin the escaping fixture to its synthetic board)
**Systemic Risk:** high (running the mandated gated suite silently disarms the operator's fleet stop-everything control; a gated run mutating live control-plane state)
**Filed As:** BUG-0423 (Path C-A)
**Regression:** no
**Integrity:** none

Step 5a's `run-integration-tests.sh` post-suite `sentinel_leak` check reported `REMOVED: $HOME/pgai_agent_kanban/HALT` — a fleet `HALT` present before the suite (empty, armed 02:05:16Z by an unattributed writer) was gone after it, confirmed by `ls` (no such file). A gated integration fixture resolved `PGAI_AGENT_KANBAN_ROOT_PATH`/`KANBAN_ROOT` to the live install instead of its synthetic board and cleared the fleet HALT. The `sentinel_leak` check is CONVERT/quality (runner still exits 0) and report-only — it caught and narrated the mutation but did not prevent or restore it. Not attributable to this RC's own new test code (the RC diff's only test changes are per-project HALT-AFTER parity fixtures on synthetic boards, verified in Findings 3–4); the escaping fixture pre-dates v2.25.1. Full detail and safe reproduction in BUG-0423.

**Consequence I caused, disclosed:** by running the sanctioned Step-5a gated integration suite I cleared the live fleet `HALT`. I did NOT restore it — SOP-core reserves the fleet HALT to operator writers ("only the operator ever removes one"; equally, only an operator re-arms it), and it is outside my write boundary. **An operator must decide whether to re-arm `$HOME/pgai_agent_kanban/HALT`.** With it gone, the CM wake (which was exiting "HALT flag detected" from 02:06Z) will resume on its next firing.

## Lint Findings (non-blocking)

- `test_anti_patterns` (unit + integration) — `team/tests/unit/test_temp_root_resolution.py:63` [AP3] hardcoded `/tmp/pgai_kanban_tmp` literal (one instance) — cites BUG-0421 (open).
- `changelog_freshness` (unit + integration) — reclassified to Step-7 Finding 12 (bug) because it fails the gated unit runner; cites BUG-0420 (open). NOT a "moves nothing" finding this run — it reds the unit suite.
- `sentinel_leak` (integration) — the suite removed the live root `HALT`; reclassified to Step-7 Finding 14 (bug) because it is a genuine live-install mutation, not a "moves nothing" note — filed BUG-0423 (Path C-A).

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=20d freshness=stale — last cold-build attempt failed at v2.9.20; stale on both age (>7d) and version (not v2.25.1); the failing run owns its own bug, not re-filed here.

## Autonomous criterion (Step 6.12)

Manual interventions on the task pipeline: **none.** All five questions answer NO from evidence:
1. Force-promote WAITING→BACKLOG by hand — NO. All promotions were the wake prerequisite check (cron-tester/cm logs).
2. `release-state.md` edited outside cm-open/release/cancel — NO. mtime = RC-open time (2026-09-27T17:09:38Z), written by `CM-20260927-041-open-rc-2-25-1`; `## Last Released` present by design.
3. Scripts under `team/scripts/`/`subagents/` patched mid-RC — NO. Every `team/scripts/*` change is inside a bundled task's own feature-branch merge; no hand patch.
4. Queue files hand-edited — NO. Markers are wake-written; the two stale `[A]` entries (CODER-037, WRITER-038) are the BUG-0416 sweep defect, not a hand edit.
5. Non-CM remote git op — NO from the task folders: no `git-boundary-guard.jsonl` exists in any v2.25.1 cycle task folder. **Self-disclosure:** during the BUG-0405 probe THIS RUN-2 session ran exactly one `git fetch origin --tags --quiet` under its own `tester` role (not impersonating CM) with the evidence file redirected to a throwaway path; the boundary guard REFUSED it (`git-boundary-guard.non-cm-remote-verb`, rc=1, one record in the throwaway file, no ref moved). This is the healthy refused-attempt path (low); it wrote nothing into the task folder. RUN-1 separately disclosed a different slip — it impersonated `PGAI_AGENT_ROLE=cm` and a real read-only fetch ran — filed as BUG-0418 (open); RUN-2 deliberately did not repeat that.

**Autonomy-health observations (not manual interventions, but loop-instability this cycle):** (a) the chain-lock-loss rework (Finding 13 / BUG-0422) discarded a completed CODER merge-finalize and this task's own RUN-1 report; (b) filing a Path C bug reds the gated unit runner (Finding 12 / BUG-0420). Both are why `## Systemic Risk` is `high`.

## Caveats

- **Prior-run artifacts:** `report.md.previous-RUN-1` / `gaps.md.previous-RUN-1` are read-only history from the discarded RUN-1; not used as input. This RUN-2 re-ran both suites and every probe from scratch.
- **Prior-run Path C filings re-confirmed, not duplicated:** BUG-0416 (medium, DONE task keeps `[A]`), BUG-0417 (low, stale "17 routes"), BUG-0419 (medium, shared pytest basetemp), BUG-0420 (medium→ impact high this run, Public-ID collision), BUG-0421 (low, AP3 literal) are all `## Status: open` and independently re-verified true this run; cited rather than re-filed (Step 5c).
- **Root HALT flag present:** a fleet-scoped `HALT` (empty, mtime 2026-09-28T02:05:16Z) appeared at the kanban root during this run; from 02:06Z the CM wake exits "HALT flag detected." SOP-core reserves the fleet-scoped file to three operator writers; I could not attribute it to a specific writer from the logs. In-flight work (this task) is untouched by HALT, so verification continued. Flagged for operator awareness; not traced to the RC's requirements, so recorded as an observation only.
- **Integration coverage 12.7%** as read mid-run is a partial/stale JSON; the authoritative integration number is recorded above after the run completed.
- **BUG-0417 `## Affects: v1.23.0`** looks mis-assigned (a currently-stale doc count is not a v1.23.0 published Known Issue); correcting or clearing it — or fixing BUG-0420's generator — is what turns the changelog gate green. Noted for CM/operator; TESTER does not edit filed bugs beyond factual correction.
