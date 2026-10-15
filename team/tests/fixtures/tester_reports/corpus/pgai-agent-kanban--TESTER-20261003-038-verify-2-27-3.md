# Verification Report: rc/v2.27.3

## Summary

I verified RC `ai_rc/v2.27.3` (commit bbfae2ff) against `requirements/v2.27.3-bugfix-bundle-20261003.md`, a bundle of 9 bugs: BUG-0526 (headline) plus ride-alongs BUG-0518 to BUG-0525. Three tasks delivered it: CODER-034, CODER-035 and WRITER-036. Each bundled bug was exercised behaviourally on the RC and, where it was safe, reproduced against an `ai_main` clone (v2.27.2). All nine headline fixes work as their bugs specify.

The certification unit suite is **not green**: `run-unit-tests.sh --coverage` exited 1 with one failure. The container-tier gate changed `deep-verify.sh --help` without regenerating the operator command reference built from it, so the RC's own freshness test fails. That is a regression introduced by this RC. The integration suite is green.

Three smaller residuals of the container-tier and operator-window work remain:
- code remedies that print the now-gated command without its authorization
- operator docs and CM.md doing the same, including two crontab lines
- one printed "pane" line in `suggested-commands.sh`

All four findings are filed via Path C (autonomous follow-up filing — non-blocking). None makes the release unusable, so the recommendation is SHIP-WITH-CONCERNS.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Findings

### Finding 1: Gated unit suite fails — operator verb reference is stale for deep-verify

**Category:** bug
**Severity:** high
**Fix Effort:** small (1 CODER task — run the generator and commit)
**Systemic Risk:** medium (a `--help` change verified only with `--files` runs skips the whole-tree freshness test; the same slip can recur on any ticket that edits an operator verb's help text)
**Regression:** yes
**Integrity:** breakage
**Filed As:** BUG-0529 (Path C-A, filed as BUG — code defect / regression / test failure)
**Filed By:** TESTER (autonomous Path C) — the container-tier gate change edited deep-verify's help text but did not regenerate the operator command reference built from it, so the unit suite fails its own freshness check.

`UNIT_EXIT=1`: `1 failed, 6478 passed, 43 skipped`. The failing test is `tests/unit/test_operator_verb_docs_generator.py::test_checked_in_docs_are_fresh`, with `generate_operator_verb_docs --check: DRIFT -- verb='deep-verify' block in docs/operator-commands.md`. Bisect: the check is OK at `425e70ea^` and DRIFT at `425e70ea` (CODER-035's container-tier gate commit). It is OK on `ai_main`, and v2.27.2's green ledger records unit exit 0.

This is not a stale assertion. The test's literal did not change; the checked-in generated doc is out of date. The fix is to regenerate `docs/operator-commands.md` and `docs/operator-api.md`. No test change is needed.

Severity follows core's calibration ("a command that worked in the last release and exits non-zero in this one is high"). Prose for CM's weighing: the underlying defect is documentation drift with zero runtime effect, the shipped scripts behave correctly, and the fix is one generator command. Systemic Risk is rated medium rather than high because this is one test and one doc block, not a framework regression across components.

### Finding 2: Container-tier remedies printed by code omit the operator authorization

**Category:** bug
**Severity:** medium
**Fix Effort:** small (1 CODER task)
**Systemic Risk:** low (isolated to remedy strings in two scripts)
**Filed As:** BUG-0527 (Path C-A)
**Filed By:** TESTER (autonomous Path C) — the deep-verify freshness check and the suggested-commands list still tell the operator to run the container-tier command without the new authorization, so the printed remedy is itself refused.

Traces to BUG-0520 (harness gate) and the runnable-remedy half of the dual-channel contract.

- `deep-verify-freshness-check.sh` prints `remedy: Run: team/scripts/deep-verify.sh --project pgai-agent-kanban` (five remedy sites).
- I ran that remedy verbatim in a sandbox (operator shell simulated with `PGAI_AGENT_WAKE_ACTIVE=0`, engine stubs on PATH). It exits 3 with `container_tier.operator_only`, and the stubs recorded no engine call.
- `suggested-commands.sh` prints `scripts/deep-verify.sh --project <proj>` without the prefix.

The second refusal does name the correct command, so a workaround exists, which makes this medium.

### Finding 3: Operator docs and CM role file give bare container-tier commands, including crontab lines

**Category:** gap
**Severity:** medium
**Fix Effort:** small (1 WRITER task)
**Systemic Risk:** low (documentation only; caught at promotion by the freshness gate)
**Filed As:** BUG-0530 (Path C-A)
**Filed By:** TESTER (autonomous Path C) — the container tier became operator-only, but the operator guides and CM's role file still give the bare commands, including two copy-paste crontab lines that will be refused every week.

Traces to BUG-0520 and SOP-core's Documentation Discipline Rule ("TESTER records a missing example-file update as a gap"). The following still give the bare command:
- `docs/operator-commands.md:818,821,825,898,907` (898 and 907 are literal crontab lines)
- `docs/OPERATIONS.md:739,742,754`
- `docs/promotion-playbook.md:71,112`
- `docs/cm-release-reference.md:186,188`
- `team/roles/CM.md:242`, which gives CM `scripts/deep-verify.sh --project <name>` as its remedy. Inside a CM wake that is always refused.

`docs/docker.md`, CODER.md and TESTER.md were correctly updated. WRITER-036 listed most of these as out of scope; the bug's role-text fix named only CODER.md and TESTER.md.

### Finding 4: suggested-commands.sh output still describes a pane

**Category:** gap
**Severity:** low
**Fix Effort:** small (1 CODER task)
**Systemic Risk:** low
**Filed As:** BUG-0528 (Path C-A)
**Filed By:** TESTER (autonomous Path C) — the suggested-commands helper's printed instruction still says to copy a line "into the cmdline pane below" because "this pane never runs anything for you", though no such pane exists.

Traces to BUG-0518's Expected ("every operator-facing description of window 1 ... describes suggested-commands.sh as a command the operator runs from that shell"). The header comment was fixed and the output kept byte-identical, as the ticket scoped. Printed line ~95 is operator-facing and still says "pane". CODER-035 flagged it under Instruction Conflicts.

### Finding 5: BUG-0526 — runner `--help` keeps the findings file

**Category:** pass

Sandboxed seeds were `findings-<suite>.json` = `{"seed":1}`, plus a `.runid` and a green ledger. On the RC, `--help` and `-h` on both runners left all of them byte-identical (sha over the temp root) and printed no "findings file written" line (4/4). The same probe on an `ai_main` clone overwrote the seed and printed the line twice (4/4), so the original failure is reproduced. The new test `test_help_leaves_findings_file_claim_and_ledger_byte_identical` runs in the gated unit suite and passed. Full runs still claim and write: both certification runs wrote their findings files.

### Finding 6: BUG-0520 — container-tier gate, manifest, directory removal (code side)

**Category:** pass

From this wake environment, with docker/podman stubs on PATH, I invoked all four harnesses under three cases:
- `PGAI_CONTAINER_TIER` unset
- `PGAI_CONTAINER_TIER=1` inside a wake
- `PGAI_CONTAINER_TIER=yes`

All 12 exit 3 with the `container_tier.operator_only` error/message/remedy triple. The stubs saw zero engine calls, and deep-verify wrote no ledger.

- The `container_tier_authorized` truth table is correct: only an exact `1` outside a wake authorizes.
- The scrub removes the variable from child processes. `test_wake_strips_container_tier_authorization.py` passed in integration.
- No `docker/*/` glob remains in the harnesses or `tests/cold/`; the flavor list comes from `team/config/container-images.txt` (debian, rocky9).
- `docker/claude-abliterated/` is gone. Only `release-notes/` history mentions it.
- The census anchor and BLOCK-WORTHY declaration are present, matching the bug's enforcement class.
- Pass-through with authorization is covered by the stubbed unit tests (`test_container_tier_gate.py`, 20 passed). I did not invoke an authorized harness, consistent with the operator-only rule.

### Finding 7: BUG-0519 — release finalizer under the engine ceiling

**Category:** pass

`release_finalize.sh` is 790 lines (at most 800). `lint_engine_size_dupe.py` reports ok, with 43 files and no collisions. `_cm_release_print_origin_summary_lines` moved byte-identical into `lib/cm/release_origin_summary.sh`, which `cm/release.sh` sources and ENGINE_FILES lists. `test_release_summary_origin_actions.py`, `test_release_state_field_writer.py` and `test_cm_release_synthetic.py` passed in the gated runs.

### Finding 8: BUG-0522 — doc-command lint leaves no process behind

**Category:** pass

I ran the real lint over the RC tree in a sandbox: exit 0, 0 processes referencing the sandbox afterwards, and the help-sandbox directory removed. The two new process-group tests passed. The host still carries 9 orphan `tail -F | awk` pairs, all dated 2026-09-25, before the fix; operator cleanup is per the bug's notes. None is newer than the fix.

### Finding 9: BUG-0523 / BUG-0524 — skip-citation gate file:line and resolved-path dedup

**Category:** pass

In an RC clone:
- A skip citing a missing `BUG-9999` prints each citing `path:line:content`, with backslash content verbatim. This holds both directly and through `run-unit-tests.sh --files`, which exits 1 with the targeted-file-lint refusal, so the "naming file and line" promise is now true.
- `--files x.py ./x.py /abs/x.py` yields 1 finding and "1 named file(s)".

On `ai_main`, the missing-ID output names only the ID, and dedup gives 3 findings and "3 named file(s)". Both originals are reproduced.

### Finding 10: BUG-0518 (docs and header), BUG-0525

**Category:** pass

- The acceptance grep for `suggested-commands pane|row|** (middle)` over `docs/` and `HOW_TO.md` is clean.
- `generate_window_docs.py --check` reports OK.
- The Window 1 prose in DASHBOARD-PANES.md and HOW_TO.md names board, shell and legend top to bottom, consistent with `docs/dashboard.md`.
- The `suggested-commands.sh` header was rewritten.
- The exit-code summary in `test-authoring-guidelines.md` now names the `--files` per-file finding cause of exit 1, consistent with both runners' `--help`.
- `check_docs_citations.py` is ok, and `lint_hygiene.py --classes provenance` reports 0 findings.

### Finding 11: BUG-0521 — flaky-test wording

**Category:** pass-with-caveat

`grep -c 'Do not generate CPU load'` on `team/templates/project/release/BUG-TEMPLATE.md` returns 1, and the template contains no "under load". No flaky-bug renderer exists anywhere in the tree: the BUG-0497..0515 wording was hand-authored by an earlier TESTER run. So the bug's fixture-renderer acceptance has nothing to apply to, and the template is the only lever.

Caveat: `docs/test-authoring-guidelines.md:663` still says to show "the timing dependence as a verdict that tracks host load", which a reader could take as an invitation to generate load. It is not among the files the bug named, so I record it as an observation, not a finding.

### Finding 12: Requirements-doc acceptance criteria

**Category:** pass

All 615 tracked Python files compile (`compile()`), and all 310 tracked bash files pass `bash -n`. "Each bundled item is addressed": all nine are addressed (Findings 5-11), with residuals filed in Findings 2-4.

### Finding 13: Gated integration suite

**Category:** pass

`INTEGRATION_EXIT=0`: 638 passed, 1 skipped.

### Finding 14: Methodology checks (6.1, 6.8, 6.9, 6.10, 6.13, 6.14)

**Category:** pass-with-caveat

- **6.1:** `pp_last_released_version` returns v2.27.2; `semver_gt v2.27.3 v2.27.2` holds; `## Last Released` is well-formed.
- **6.8:** the diff touches `cm/release.sh` and `lib/cm/`. `test_cm_release_synthetic.py` (18 tests) passed in integration.
- **6.9:** no secrets in added lines. `set -euo pipefail` is preserved in the touched harnesses; `run-three-host-matrix.sh` keeps its pre-existing `set -uo pipefail`, unchanged from `ai_main`.
- **6.10:** the graph is acyclic and the bookends are correct: CM-037 open, then CODER-034/035 and WRITER-036 (after 035), then TESTER-038, then CM-040 release (WAITING). The one caveat is that sequence 039 was never created; it is a skipped number, not a collision.
- **6.13:** the seven `BUG-NNNN` IDs found near "skip" in tests are all fixture strings in lint tests or restamp assertion text, not real skip annotations. There are no `BUG-SKIP` placeholders outside lint fixtures.
- **6.14:** the new gate declares its census class, emits the dual-channel triple and has an anchor plus Locator Index entry. The provenance lint is clean. New libs are sourced files, so the lack of `--help` is appropriate.

Test fidelity: the gate tests use engine stubs that log, plus a real scrubbed env. The `--help` test drives the runner in a sandbox. My own reproductions used the real runners and real lint. I have no concrete fidelity concern.

## Lint Findings (non-blocking)

- `operator_verb_docs_freshness` (unit and integration findings files, one instance each): the `deep-verify` block in `docs/operator-commands.md` disagrees with `deep-verify.sh --help`. Cites BUG-0529, filed via Path C-A this run. The same root cause also fails a unit test (Finding 1), which is what elevates the recommendation; the lint finding alone would not.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=26d freshness=stale — the last cold build was red at v2.9.20, filed as BUG-0167 (now done, fixed in v2.9.22), and the record is stale on both counts (26d exceeds the 7-day window, and v2.9.20 is not the v2.27.3 under verification), so nothing is known about whether this tree's debian and rocky9 images build cold; the lane is now operator-only and was not run.

## Coverage (informational only)

- unit: line 78.31%, branch 64.24% (from `coverage/unit.json` `totals`: covered_lines/num_statements, covered_branches/num_branches)
- integration: line 12.69%, branch 9.88% (from `coverage/integration.json` `totals`)

## Autonomous criterion

1. Tasks force-promoted WAITING→BACKLOG by hand: **NO**. Promotions were by the wake script, e.g. this task's status "Promoted from WAITING to BACKLOG by wake script".
2. `release-state.md` edited outside the CM scripts: **NO**. Its mtime is 2026-10-03T18:07:06, equal to `## RC Opened At` from CM-037.
3. Scripts patched mid-build: **NO**. No live-install file (`scripts/`, `pm-agent/`, `roles/`, governance) was modified after RC open. Every `team/scripts` commit on the RC is the cycle's own CODER work, merged `--no-ff`.
4. Queue files hand-edited: **NO**. Queue mtimes line up with agent transitions (18:05 PM, 18:08 CM, 18:58 CODER, 19:12 TESTER dispatch).
5. Remote git operation by a non-CM agent: **NO**. No `git-boundary-guard.jsonl` exists in any of the cycle's task folders.

Autonomous operation criterion: Manual interventions required: none

## Caveats

- Pre-flight: no `ai_feature/*` branch survives; all three cycle merges are on the RC with unique commits. Worktree HEAD == RC, clean, no stashes. I did not modify the RC.
- No container was built, pulled or started. Every harness invocation in this verification ran against docker/podman stubs and exercised only the refusal path.
- Observation (outside spec): `team/scripts/harness/doc-lint.sh:166` has a comment saying the feature token list tracks "docker/*/Dockerfile". It is a comment, not a glob, and doc-lint is not a container harness. The engine-free static Dockerfile lint still globs `docker/*/Dockerfile`, by design.
- Operator follow-ups carried from the bundle: run `docker rmi docker.io/t2fn/claude-abliterated:latest-amd64`, and remove the 9 pre-existing help-sandbox orphan pairs (`pgrep -af 'lint_doc_command_truth/help-sandbox-'`, then `pkill -f`).
- My first `py_compile` probe failed because of a mistake in my own command (cfile=/dev/null). I re-ran it with `compile()`; that failure was never an RC result.
- Suite output: `/tmp/pgai_kanban_tmp/tester/v2.27.3/{unit,integration}.log`, plus copies of the findings and coverage JSON.

## Working log

- Pre-flight Step 2: no `ai_feature/*` branch survives locally; the three cycle tasks merged as 0503b2d3, 0e147f9f, bbfae2ff on `ai_rc/v2.27.3`, each with unique commits. PASS.
- Pre-flight Step 3: worktree HEAD bbfae2ff == `ai_rc/v2.27.3`; `git status --porcelain` empty; `git stash list` empty. PASS.
- Gated suites launched (unit then integration, `--coverage`), output under `/tmp/pgai_kanban_tmp/tester/v2.27.3/`.
- BUG-0526: sandboxed `--help`/`-h` on both RC runners leave seeded findings file, `.runid` and green ledger byte-identical, no "findings file written" line (4/4). Same probe on an `ai_main` clone (0dee373f) overwrites the seed with an empty findings doc and prints the "written" line twice (4/4) — original failure mode reproduced, fix confirmed. PASS.
- BUG-0520 (code): from this wake env (PGAI_AGENT_WAKE_ACTIVE=1) with docker/podman stubs on PATH, all four harnesses refuse with exit 3 + `container_tier.operator_only` dual-channel triple under three cases (unset, `=1` inside wake, `=yes`): 12/12, zero engine-stub calls, no deep-verify ledger written. `container_tier_authorized` truth table correct (only exact `1` outside a wake authorizes); `container_tier_scrub_env` hides the var from children. No `docker/*/` glob in the four harnesses or `team/tests/cold/`; flavors come from `team/config/container-images.txt` (debian, rocky9). `docker/claude-abliterated/` absent; only `release-notes/` history mentions it. PASS (code side).
- BUG-0519: `release_finalize.sh` 790 lines (<=800); `lint_engine_size_dupe.py` ok, no collisions; `_cm_release_print_origin_summary_lines` moved byte-identical to `lib/cm/release_origin_summary.sh`, sourced by `cm/release.sh` before the finalizer, listed in ENGINE_FILES. PASS (driving tests checked in suite run).
- BUG-0522: real `lint_doc_command_truth.py --repo-root <RC>` in a sandbox: rc 0, 0 processes referencing the sandbox afterwards, help-sandbox dir removed. Host carries 9 orphan tail/awk pairs dated 2026-09-25 (pre-fix; operator cleanup per bug notes) — none newer than the fix. PASS.
- BUG-0523: in an RC clone, missing `BUG-9999` citation prints each citing `path:line:content` (2 hits, backslash content verbatim), both via the lint directly and via `run-unit-tests.sh --files` (exit 1 with the targeted-file-lint refusal). `ai_main` prints the ID only. PASS.
- BUG-0524: `--files x.py ./x.py /abs/x.py` → 1 finding, scope "1 named file(s)" on RC; `ai_main` → 3 findings, "3 named file(s)". PASS.
- BUG-0518 docs: acceptance grep clean; `generate_window_docs.py --check` OK; DASHBOARD-PANES/HOW_TO prose names board → shell → legend. Residual: `suggested-commands.sh` printed line 95 still says "cmdline pane below -- this pane" → gap, filed BUG-0528.
- BUG-0521/0525: template carries "Do not generate CPU load" (count 1, no "under load"); no renderer exists anywhere in the tree (wording was hand-authored by an earlier TESTER run); guideline exit-code summary matches both runners' `--help`. PASS.
- BUG-0520 residual: `deep-verify-freshness-check.sh` remedies and `suggested-commands.sh` row print the bare harness command; running the freshness gate's remedy verbatim is refused (exit 3) → bug, filed BUG-0527. Operator docs + CM.md:242 still give bare commands incl. two crontab lines → gap, filed BUG-0530.
- Requirements ACs: 615 Python files compile, 310 bash files pass `bash -n`. PASS.
- UNIT_EXIT=1: 1 failed / 6478 passed / 43 skipped — `test_operator_verb_docs_generator.py::test_checked_in_docs_are_fresh` (deep-verify generated block drift, introduced by 425e70ea; v2.27.2 unit ledger exit 0) → bug, filed BUG-0529.
