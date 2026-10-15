# Verification Report: rc/v2.32.9

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Summary

I verified RC `ai_rc/v2.32.9` (HEAD 293d6168) against `requirements/v2.32.9-bugfix-bundle-20261008.md`. The bundle covers BUG-0626 (the SHIP-KNOWN-ISSUE resolver used its own level-3-only heading rule; medium), BUG-0627 (a nested finding heading dropped the outer block; low) and BUG-0628 (the ship-policy heredoc was over its inline-Python allowlist; low). They were implemented by CODER-20261008-036, CODER-20261008-037 and WRITER-20261008-038. Pre-flight was clean. Both gated runners exited 0: unit 10658+5 passed, integration 959 passed, 0 lint findings. Every acceptance criterion of the three bundled bugs and the three tickets was met with run evidence. The original failures reproduce on an ai_main export and are fixed on the RC: all four readers agree on all seven heading shapes. Reader outputs over all 94 install TESTER reports are unchanged, and a stressed variant changes only the one report that uses `####` headings, which is the intended fix. Inline-Python lint is clean, and the new tests fail on the old code. The spot-check found two pre-existing defects of the same reader-disagreement class, both filed via Path C (autonomous follow-up filing — non-blocking) as Path C-A (filed as BUG — code defect): BUG-0629 (OVERWATCH reads TESTER reports with its own heading rule, which makes this RC's new "every reader" text untrue) and BUG-0630 (field-line rules still differ, so a SHIP-KNOWN-ISSUE bug can be missing from both release-notes sections). Both are medium. Neither makes the release unusable, so the recommendation is SHIP-WITH-CONCERNS.

## Findings

### Pre-flight (Steps 2-3)

- Step 2: 27 DONE tasks name a feature branch; none exists locally (deleted after merge), so both checks pass trivially. The three bundle commits (28052d91, 32942c13, 2aca5ca9) are on the RC via --no-ff merges d879688e, 2cae22b8, 293d6168. — **pass**
- Step 3: worktree HEAD 293d6168 == `git rev-parse ai_rc/v2.32.9`; `git status --porcelain` empty; `git stash list` empty. — **pass**

### Bundle acceptance criteria (requirements doc)

- AC "All Python files compile cleanly": in-memory `compile()` over all 849 tracked `*.py` files on the RC: 0 failures; `python3 -m py_compile` on the 5 touched Python files: ok. — **pass**
- AC "All bash files pass syntax check": `bash -n` over all 334 tracked `*.sh` files: 0 failures (both touched shell libs included). — **pass**
- AC "Each bundled item is addressed": see per-bug sections below.

### BUG-0626 / BUG-0627 behavioral reproduction (Step 6.5)

Sandbox under `$PGAI_AGENT_KANBAN_TEMP_DIR/tester/v2.32.9/sandbox-*`, env per the bug reports' Reproduction step 3 (`env -u PGAI_TASKS_DIR -u PGAI_PROJECT_ROOT -u PYTHONPATH KANBAN_ROOT=$S ...`). Each report carries one finding with Systemic Risk high, Regression no, Integrity none, Fix Effort small, Filed As BUG-0003; bugs dir holds BUG-0003-x3.md. Run against the RC worktree and against a `git archive ai_main` export (148531c2) as the before-baseline. Columns: dims / `cm_ship_policy done high ship-with-concerns no yes <dims>` / `read_ship_policy_known_issue_bug_files` line count / `stop_bug_files ... systemic-risk-high` line count / Bugs Skipped rows naming BUG-0003.

| Heading | ai_main (before) | RC (after) |
|---|---|---|
| `### Finding 1:` | no/none, SHIP-KNOWN-ISSUE, KI=1, stop=1, BS=1 | no/none, SHIP-KNOWN-ISSUE, KI=1, stop=1, BS=1 |
| `#### Finding 1:` | no/none, SHIP-KNOWN-ISSUE, **KI=0**, stop=1, BS=1 | no/none, SHIP-KNOWN-ISSUE, KI=1, stop=1, BS=1 |
| `##### Finding 1:` | no/none, SHIP-KNOWN-ISSUE, **KI=0**, stop=1, BS=1 | no/none, SHIP-KNOWN-ISSUE, KI=1, stop=1, BS=1 |
| `###Finding 1:` | unknown/unknown, HALT, **KI=1**, stop=0, BS=0 | unknown/unknown, HALT, KI=0, stop=0, BS=0 |
| `### Findings summary` | unknown/unknown, HALT, KI=0, stop=0, BS=0 | unknown/unknown, HALT, KI=0, stop=0, BS=0 |
| `## Findings` | unknown/unknown, HALT, KI=0, stop=0, BS=0 | unknown/unknown, HALT, KI=0, stop=0, BS=0 |
| nested (`### Finding 1: outer` + `#### Finding 2: inner detail`) | no/none, SHIP-KNOWN-ISSUE, KI=1, **stop=0, BS=0** | no/none, SHIP-KNOWN-ISSUE, KI=1, stop=1, BS=1 |

Original failure modes reproduce on ai_main (bold cells: reader disagreement); on the RC all four readers agree on every one of the seven shapes. BUG-0626 Reproduction step 3 (`#### Finding 1: grouped`) prints `SHIP-KNOWN-ISSUE` then `1` on the RC (was `0`), and the `###` variant still prints `SHIP-KNOWN-ISSUE` then `1`. — **pass** (BUG-0626 AC1-2, BUG-0627 AC1)

### Static and regression acceptance checks (Step 6.4)

- BUG-0626 AC3: `grep -n 'Finding' team/scripts/lib/cm/release_known_issue.sh` returns only comment lines 144-147; no heading regex of its own (the old `(?im)^###\s*Finding\b.*$` and `^##\s+\S` cut are removed; the heredoc imports `pgai_agent_kanban.cm.stop_bug_files.finding_blocks` via `PYTHONPATH="$(pp_pythonpath)"`). — **pass**
- BUG-0628 AC1 / CODER-037 AC3: `python3 team/scripts/lint_inline_python.py` on the RC: `310 file(s) scanned ... 0 finding(s)`. On ai_main the same command prints `[above] team/scripts/lib/cm/release_ship_policy_report.sh has 2 block(s) / 357 python line(s), above its allowlist entry of 2 / 350`. Allowlist entries were tightened, never raised: release_known_issue.sh 5/290 -> 5/283, release_ship_policy_report.sh 2/350 -> 2/329; no `below` finding, so the entries match the new counts exactly. — **pass**
- Install-wide unchanged-output constraint (BUG-0626 AC4, BUG-0627 AC3, BUG-0628 AC2): every TESTER report in the install (94 files, `projects/*/tasks/TESTER-*/artifacts/report.md*`, all projects, including `.previous-RUN-N`) run through all six reader outputs — `read_ship_policy_finding_dimensions`, `read_ship_policy_report_values`, `read_ship_policy_known_issue_bug_files`, `stop_bug_files` systemic-risk-high and fix-effort-large, `read_ship_policy_skipped_bug_pairs` — on ai_main vs the RC, in the sandbox env. `diff` of the 660-line outputs: identical. — **pass**
- Stressed variant of the same: every report copied with every `Systemic Risk:` line forced to `high` (so the resolver and stop list engage on every finding). The only difference across all six outputs is the resolver on `tasks/TESTER-20261008-028-verify-2-32-7/artifacts/report.md` — the one install report that uses `#### Finding` headings — which now resolves BUG-0625 where ai_main resolved nothing. That is the intended fix; no level-3-only report changed. — **pass**

### Test fidelity (Principle 4 / "Test fidelity verification")

The RC's three changed test files were copied into the ai_main export and run there (direct pytest, sandboxed env — a fidelity aid only, never PASS evidence): `9 failed, 104 passed`. The failing tests are exactly the new or extended ones (the four-reader agreement cases for `####`, `#####`, `###Finding`, and nested; the three nested-shape tests in `test_stop_bug_files.py`; the step-grouped resolver test). They exercise the real readers through bash `source` of the libraries, not mocks, so they catch the original failure mode. — **pass**

### WRITER deliverable: TESTER.md Step 9 paragraph (Step 6.3)

Read in full on the RC (one-line diff). Block start (level 3-6, whole word `Finding`) and extent (next heading no deeper than its own, or next finding heading at any depth; nested `####` under `###` closes the outer block) match the `finding_heading_level` docstring word for word in substance. The examples are correct against the shared rule (`### Finding N:` and `#### Finding N:` open; `### Findings summary`, `###Finding 1:`, level-1/2 do not — confirmed by the repro table above). It names all four bundle readers (ship-policy parser, Bugs Skipped, the trigger-2/3 stop list, the SHIP-KNOWN-ISSUE Known Issues resolver). `grep -cE 'BUG-[0-9]{4}|v2\.32\.' team/roles/TESTER.md` = 0 on both ai_main and RC. — **pass**, with the exception recorded as Finding 1 below (its universal claim "Every reader of the report's findings splits it by this one rule" is not true of the tree).

### Finding 1: OVERWATCH's TESTER-report parser is a fifth finding reader with its own heading rule, contradicting the RC's "every reader" text

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (the same class — a heading-rule fix that names a fixed set of readers and misses one — has now recurred across consecutive RCs)
**Severity:** medium
**Filed As:** BUG-0629 (Path C-A — filed as BUG: code defect)

Traces to: BUG-0626 Expected ("Every reader that decides 'a finding block starts here' in a TESTER report uses `stop_bug_files.finding_heading_level` ... The docstrings and role text that say every reader shares the rule are then true") and WRITER-038 AC1 ("The Step 9 paragraph's statement of which readers share the heading rule is true").

The RC's text now states a universal property: `team/roles/TESTER.md` Step 9 — "Every reader of the report's findings splits it by this one rule, so all of them see the same blocks"; `team/pgai_agent_kanban/cm/stop_bug_files.py` module docstring — "Every reader of a TESTER report's findings splits through finding_blocks ... So all four split any report into the same blocks, for every heading shape and every nesting." That is false. `team/pgai_agent_kanban/overwatch/resolution_matrix.py:152` `parse_tester_report_findings` (called from `resolution_stage.py:1346` and `_fact_findings_all_filed`) reads TESTER reports with its own `^#{2,4}\s*Finding\s+\d+\s*:` and runs each block to the next finding header with no shallower-heading cut.

Measured on the RC (sandbox; report with one finding, `**Filed As:** none`):

| Heading | shared `finding_blocks` | OVERWATCH findings | OVERWATCH `findings_all_filed` |
|---|---|---|---|
| `### Finding 1:` / `#### Finding 1:` | 1 | 1 | False |
| `##### Finding 1:` / `###### Finding 1:` | 1 | **0** | **True** (vacuous) |
| `## Finding 1:` / `###Finding 1:` | 0 | **1** | False |
| `### Finding: no number` | 1 | **0** | **True** (vacuous) |

Extent: for `### Finding 1: a` / `**Filed As:** BUG-0001` / `### Step 7` / `**Filed As:** none`, the shared splitter's block holds only `BUG-0001`; OVERWATCH reads `filed_as = none` from the step's prose. Over the 94 real TESTER reports in the install, 10 are split into a different number of findings by the two parsers (e.g. this project's `TESTER-20261008-028-verify-2-32-7/artifacts/report.md`: 3 vs 1). An unfiled finding under a deeper heading turning `findings_all_filed` vacuously True is fail-open against that fact's own documented fail-closed intent. The parser is unchanged from ai_main (code predates the RC); the overclaiming text is this RC's. Calibration: real defect in a shipped artefact on a path consulted only in OVERWATCH HALT resolution, so medium, not high.

### Methodology steps 6.1, 6.9, 6.10

- 6.1 release-state.md: `## Active RC` v2.32.9, `## RC Opened At` 2026-10-08T23:17:14+00:00, `## RC Opened By Task` CM-20261008-039-open-rc-2-32-9, `## Last Released` v2.32.8 (well-formed). `pp_last_released_version pgai-agent-kanban` = v2.32.8; `semver_gt v2.32.9 v2.32.8` true. The trailing `## HALT Event` block (a v2.32.3 trigger-9 halt from 2026-10-08T00:37:43Z) is the documented append-only CM record (`roles/CM.md` lines 11 and 220), not drift. — **pass**
- 6.9 constraints: the one new PYTHONPATH use (`release_known_issue.sh`, `PYTHONPATH="$(pp_pythonpath)"`) resolves through `pp_run_ops.sh`'s own-tree root; no `sys.path` insert, no dev-tree fallback added anywhere in the diff. Allowlist entries only tightened. Neither touched lib carried `set -e` before or after (sourced libraries; unchanged). The resolver's new failure path (`return 1` when `pp_pythonpath` cannot locate its tree, or a non-zero exit if the import fails) is consumed at `release_ship_policy.sh:510` via `mapfile < <(...)`, so it degrades to "Known-issue bug(s) resolved: 0" with stderr narration instead of aborting — the same degradation as the sibling readers. Bug-shaped strings added by the diff (`BUG-0003`, `BUG-0021`, `rc/v0.0.0`) are all test-fixture values (sanctioned exception). — **pass**
- 6.10 prereq graph: CM-039 (open RC, DONE) <- CODER-036 <- CODER-037 <- WRITER-038 <- TESTER-040 (this) <- CM-042 (release, WAITING on 036/037/038/040). No cycles, every referenced ID exists, bookends linked; sequence 041 unused, consistent with requirements `Human Approval Required: auto` (no HUMAN-APPROVE task). — **pass**

### Finding 2: The four report readers still read a finding's field lines by different rules, so a SHIP-KNOWN-ISSUE bug can be named in neither Known Issues nor Bugs Skipped

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (same reader-disagreement class as the bundle; recurs until field reading is shared like block splitting now is)
**Severity:** medium
**Filed As:** BUG-0630 (Path C-A — filed as BUG: code defect)

Found in the Step 6.11 spot-check ("regressions of prior gaps"), probing BUG-0626's Expected outcome ("A ... high-risk finding that makes the matrix return SHIP-KNOWN-ISSUE is resolved to its bug file") with field shapes rather than heading shapes. CODER-036 noted the stricter field parsing in its status and left it out of scope by design; this RC's spec covered block boundaries only, so this is not a gap in the RC's deliverables. It is identical on ai_main (Regression: no).

Sandbox, `### Finding 1:` finding with Systemic Risk high, Regression no, Integrity none, Fix Effort small:

| Field-line shape | dims / decision | KI resolver | stop list | Bugs Skipped |
|---|---|---|---|---|
| `**Label:** value`, `**Filed As:** BUG-0003` | no none / SHIP-KNOWN-ISSUE | 1 | 1 | 1 |
| plain `Label: value` (no bold) | no none / SHIP-KNOWN-ISSUE | **0** | 1 | **0** |
| ``**Filed As:** `BUG-0003` `` | no none / SHIP-KNOWN-ISSUE | **0** | 1 | **0** |
| `**Filed As:** see BUG-0003` | no none / SHIP-KNOWN-ISSUE | **0** | 1 | **0** |

In those rows the bug appears in neither release-notes section, although the release ships *because of* it. The ship-policy reader tolerates missing bold markers (its header: "the bold markers are tolerated either way"); the resolver (`release_known_issue.sh` heredoc `field_value`, bold-only, `re.match` on `BUG-`) and `skipped_bug_pairs._field_value` do not. A real install report already uses the cite shape (`my-app/tasks/TESTER-20260903-006-verify-0-26-4/artifacts/report.md:250`, `**Filed As:** cites the existing BUG-0058-...`); under the stressed comparison it is the one report where the stop list and the resolver disagree. Rated medium: incorrect release-notes output on a reachable path, with a workaround (TESTER writes canonical `**Label:** BUG-NNNN` fields).

### Methodology steps 6.11, 6.13, 6.14

- 6.11 spot-check (time-boxed): (a) **state drift** — Active RC v2.32.9 matches the existing `ai_rc/v2.32.9` branch and CM-039's record; none. (b) **regressions of prior gaps** — the bundle's own class (report readers disagreeing) was probed beyond the four named readers and beyond heading shapes: produced Finding 1 (a fifth reader, OVERWATCH) and Finding 2 (field-line rules). (c) **untested new mechanisms** — the resolver's new import/`return 1` path is reached only when `pp_pythonpath` cannot self-locate; the call site degrades safely (6.9). Observation only (not a finding): under the documented nested-finding rule, lines after a closed nested finding and before the next heading as shallow as the outer finding belong to no block; this is documented beside `finding_heading_level` and TESTER.md tells authors to write fields above any nested finding heading, and it matches both pre-RC splitters.
- 6.13 skip-cites-real-bug: no placeholder skip in real test code (the `BUG-SKIP-synthetic-placeholder` and `pytest.skip("BUG-1234 ...")` hits are fixture strings inside the lint gate's own tests); no `@pytest.mark.skip/xfail` or `pytest.skip(` call cites a BUG id; the RC adds no skip. The runner's `lint_skip_bug_gate.sh` result is in Step 5a. — **pass**
- 6.14 coding standards (5 min): no new script (no `--help` obligation); no new hardcoded operational value; the RC *removes* a duplicated implementation (the heredoc's own `finding_blocks`/`finding_section`) in favour of the shared module; no bug/task/version identifier in shipped code or prose. — **pass-with-caveat**: cosmetic line-wrapping in the `release_ship_policy_report.sh` header Dependencies comment (module name split across lines as `pgai_agent_kanban.cm.` / `stop_bug_files's`, and a stranded `#   PYTHONPATH for` line). Not filed; cosmetic only.
- 6.8 synthetic release simulation: the diff touches `team/scripts/lib/cm/` libraries sourced by `cm/release.sh` (Step 4d), so this applies; `team/tests/integration/test_cm_release_synthetic.py` runs inside the integration gated runner — result recorded under Step 5a.

### Autonomous criterion (Step 6.12)

1. Tasks force-promoted WAITING -> BACKLOG by hand? **NO** — every cycle task (PM-006, CM-039, CODER-036, CODER-037, WRITER-038, TESTER-040) has wake-script `dispatched`/`returned exit_code 0` records in `logs/dispatch.jsonl`; this task's own status read "Promoted from WAITING to BACKLOG by wake script".
2. `release-state.md` edited outside the CM scripts? **NO** — mtime 2026-10-08 23:17:14 equals CM-039's open-rc.sh run (RC Opened At 23:17:14); the `## HALT Event` block is the documented CM append from an earlier release.
3. Scripts patched mid-build? **NO** — the RC carries only the three bundle commits plus their merges; no file under the live install's `scripts/`, `roles/`, `pm-agent/` or `pgai_agent_kanban/` changed after 23:17:14.
4. Queue files edited by hand? **NO** — queue mtimes (cm 23:18:31, coder 23:42:04, writer 23:48:23, tester 23:48:54) coincide with wake transitions of this cycle's tasks.
5. Non-CM remote git operation? **NO** — no `git-boundary-guard.jsonl` exists in any cycle task's `logs/`.

Autonomous operation criterion: Manual interventions required: none

### Gated suites (Steps 5a, 5b, 6.6, 6.7, 6.8)

Both runners were run from the RC worktree root with `--coverage`, sequentially, output teed to `$PGAI_AGENT_KANBAN_TEMP_DIR/tester/v2.32.9/{unit,integration}.log`.

- `bash team/scripts/run-unit-tests.sh --coverage` -> **UNIT_EXIT=0**. Lint pre-flight clean, including `lint_inline_python ... 0 finding(s)`. Parallel lane `10658 passed, 41 skipped`; serial lane `5 passed`; `Lane exit codes: 0 0 -> combined exit code 0`; `Unit tests PASSED`; green ledger recorded exit 0 at commit 293d6168990c. — **pass**
- `bash team/scripts/run-integration-tests.sh --coverage` -> **INTEGRATION_EXIT=0**. Parallel lane `959 passed, 1 skipped`; serial lane exit 5 (no serial-marked tests, which the runner documents as a pass); `combined exit code 0`; `Integration tests PASSED`. — **pass**
- 6.8 synthetic release simulation: `team/tests/integration/test_cm_release_synthetic.py` (19 tests, not quarantined) ran in the integration lane and passed (it appears in the durations list, e.g. `TestVerifyBeforeDestroy::test_rigged_pre_tag_hook_leaves_world_untouched_then_fix_and_rerun_ships` 11.20s). `test_cm_release_known_issue_carry_forward.py` (6 tests, which exercise the Step 4d resolver end to end) also ran and passed. — **pass**
- Coverage (5b, informational only): both runners printed `coverage disabled by config ([testing] coverage)`, so neither collected. The `coverage/unit.json` and `integration.json` files present are from 2026-10-04 and do not describe this RC. **Coverage unavailable.** This is not a finding.
- The per-ticket targeted runs named in the bug ACs (`--files ...`) are subsumed by the full certification runs above. Every named test file is in the unit suite, which exited 0.

## Lint Findings (non-blocking)

None. `findings-unit.json` (generated 2026-10-09T00:08:47+00:00) and `findings-integration.json` (generated 2026-10-09T00:20:36+00:00) both contain `"findings": []`. The `inline_python` `above` instance that BUG-0628 tracked is gone.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=31d freshness=stale — The latest ledger record is a red run on v2.9.20 (docker/podman `claude-abliterated` FROM parse error and podman/rocky9 `COPY --chmod`), already filed by that run as BUG-0167-cold-build-claude-abliterated-failure (archived, status done, Fixed In v2.9.22); it is stale because it is 31 days old (window 7) and names v2.9.20 rather than v2.32.9, so nothing is known about whether this RC's images build.

## Caveats

- Recommendation basis: both gated runners exited 0, and every bundled item's acceptance criteria were met with run evidence (BUG-0626, BUG-0627, BUG-0628; CODER-036, CODER-037, WRITER-038). The recommendation rises from PASS only because of Findings 1 and 2. Both are pre-existing reader disagreements of the same class as the bundle. Finding 1 also covers the overclaiming text that this RC added. Neither breaks this RC's own deliverables.
- Instruction conflict (recorded in status): the role file's "Git workflow" section says to commit verification artifacts on a feature branch off the RC. The dispatch prompt for this task says not to create a feature branch, and the role file's own Step 9 says process artifacts are never committed into a dev tree. I followed the dispatch prompt. The artifacts live only in the task folder, and the worktree was left clean (`git status --porcelain` empty at the end).
- "Every TESTER report in the install" was taken as `projects/*/tasks/TESTER-*/artifacts/report.md*` (94 files). CODER-037 compared 493 files with a wider glob. Both comparisons found no change.
- Direct pytest was used once, as a fidelity aid (the RC's tests run against ai_main code, 9 failed as expected). It is not cited as PASS evidence.
- The deep-verify lane is operator-only and was not run. The latest ledger record is stale (see Deep-Verify Freshness). Whether this RC's images build is unknown.
- Report prose quotes heading shapes such as `### Finding 1:` inside backticks within table cells and list items. Only the real `### Finding N:` headings in this report start finding blocks.
