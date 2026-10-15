# Verification Report: rc/v2.27.2

## Summary
I verified RC `ai_rc/v2.27.2` (commit bf0336700600a91e727098cb8a38d33eac569619)
against `requirements/v2.27.2-priority-bundle-20261003.md`. The bundle holds
one item, PRIORITY-0006: a targeted `--files` run applies the per-file
test-authoring lints (the anti-pattern lint and the skip-cites-a-real-bug
gate) to exactly the files it names. Three tasks delivered it:
CODER-20261003-027 (named-file mode on both lints), CODER-20261003-028
(runner wiring, exit verdict, census row 72) and WRITER-20261003-029
(guideline section).

Every PRIORITY-0006 and ticket acceptance criterion is met. I checked them
end to end with the real runners on a scratch clone, using sandboxed roots.
The AP7 check used the actual v2.27.0 offender file as a production-shape
input. Both gated runners exited 0.

I filed four small follow-ups via Path C (autonomous follow-up filing —
non-blocking), all as Path C-A (filed as BUG — code defect / regression /
test failure):
- three low-severity accuracy and consistency defects in this RC's work;
- one medium-severity defect that predates the RC: `--help` overwrites the
  certification findings file.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Gated suites (Step 5a / 6.6 / 6.7)

Both runners were run from the dev-tree root with `--coverage`. Output is
under `/tmp/pgai_kanban_tmp/tester/v2.27.2/`.

- `UNIT_EXIT=0`: `run-unit-tests.sh --coverage`, 6447 passed, 43 skipped,
  1 warning (27m13s). Lint pre-flight passed.
- `INTEGRATION_EXIT=0`: `run-integration-tests.sh --coverage`, 638 passed,
  1 skipped (17m24s). Lint pre-flight passed.
- The suite-pass precondition holds. This evidence is the runners' exit
  codes, not direct pytest.

**Coverage (informational only, from `totals` in each runner's JSON; field
names verified against the generated file):**
- unit: line (statements) 78.31%, branch 64.24% (blended `TOTAL` 75%).
- integration: line 12.69%, branch 9.88%.

## Findings

### Finding 1: Pre-flight: RC intact (Step 2)
**Category:** pass

541 DONE tasks name a feature branch. None exists locally, which is the
normal post-merge shape, so Check 1 and Check 2 pass trivially. The three
RC feature commits (62aa5366, 02f9bd68, 4e165b17) each landed through a
`--no-ff` merge (966c3963, 511c5cd0, bf033670).

### Finding 2: Worktree identity and cleanliness (Step 3)
**Category:** pass

`git rev-parse HEAD` equals `git rev-parse ai_rc/v2.27.2` (bf033670).
`git status --porcelain` and `git stash list` are both empty. The
porcelain was re-checked after both suites: still empty.

### Finding 3: Release-state sanity (Step 6.1)
**Category:** pass

`pp_last_released_version pgai-agent-kanban` returns v2.27.1, and
`semver_gt v2.27.2 v2.27.1` is true. `## Active RC` reads v2.27.2 and
`## Last Released` reads v2.27.1, both well-formed.

### Finding 4: Bundle AC — every Python file compiles
**Category:** pass

All 612 tracked `*.py` files go through `compile()` with 0 SyntaxError.

### Finding 5: Bundle AC — every bash file passes the syntax check
**Category:** pass

All 307 tracked `*.sh` files pass `bash -n` with 0 failures.

### Finding 6: PRIORITY-0006 AC1 — an AP7 fixture via `run-unit-tests.sh --files` prints the finding with file and line
**Category:** pass

I recovered the real v2.27.0 offender
(`git show 4ce0ea62:team/tests/unit/test_pm_materialize_acceptance_policy.py`)
into a scratch clone and ran the real runner with `--files` on it. Output:
`.../test_probe_prod_ap7.py:92: [AP7] ...`, where line 92 is
`env = dict(os.environ)`. Both tests passed, and the run then exited 1
through the dual-channel contract
`error: run-unit-tests.targeted-file-lint-findings` (message and remedy
present; the remedy re-runs only the reporting lint). `targeted-unit.exit`
holds `1`.

### Finding 7: PRIORITY-0006 AC2 — a clean targeted run prints no finding and run time grows by at most a few seconds
**Category:** pass-with-caveat

`--files tests/unit/test_semver.py` printed `[targeted-lint] test_anti_patterns: OK`
and `[targeted-lint] skip_cites_real_bug: OK`, then 34 passed, exit 0.

I timed three alternating rounds of ai_main's runner against the RC's
runner on the same file. ai_main took 26.0 s, 16.5 s and 12.1 s; the RC
took 22.0 s, 13.6 s and 26.6 s.

Caveat: the host was under heavy load (load average 15, with the full
suite running), so wall-time is noise-dominated. Timed in isolation, the
two lints add 0.085 s + 0.142 s, well within "a few seconds".

### Finding 8: PRIORITY-0006 AC3 — the findings file and green ledger are byte-identical across targeted runs
**Category:** pass

In the sandbox temp root I seeded `lint/findings-unit.json`, its `.runid`,
`lint/findings-integration.json`, `unit.exit`, and
`projects/pgai-agent-kanban/green-ledger/v2.27.2/ledger.jsonl`. I took
sha256 of each, then ran five targeted runs: unit with findings, unit
clean, three skip-gate cases, and integration with and without findings.
The hashes matched after each run. No targeted run created a `lint/`
directory or a ledger in a fresh sandbox.

### Finding 9: PRIORITY-0006 AC4 and ticket ACs — `run-unit-tests.sh --files <this RC's test files>` exits 0
**Category:** pass

`--files tests/unit/test_lint_named_file_mode.py tests/unit/test_runner_targeted_file_lints.py tests/unit/test_runner_targeted_files.py`
gave both lints OK and 68 passed, exit 0.

### Finding 10: CODER-028 AC — the integration runner has parity
**Category:** pass

`run-integration-tests.sh --files` on an AP7 file under
`tests/integration/` printed `...:10: [AP7]`, 1 passed, and exited 1 with
`run-integration-tests.targeted-file-lint-findings`
(`targeted-integration.exit` = 1). On a clean file it printed both OK
lines and exited 0. Certification files stayed byte-identical.

### Finding 11: Guard behavior, triggered (Step 6.5)
**Category:** pass

I triggered each guard on the failure path:
- skip citing a non-existent `BUG-9999` (with a real bugs dir present) → exit 1;
- skip citing an existing `BUG-0001` → exit 0;
- `BUG-SKIP` placeholder → exit 1, naming `file:4`;
- pytest failure plus an AP7 finding in the same file → pytest's own
  failure verdict (exit 1), with the lint finding still printed;
- `lint_test_anti_patterns.py --files` combined with `--tests-dir` → exit 2;
- a missing path → exit 2;
- a named clean file beside an AP7 sibling → exit 0 (the sibling is not
  reported);
- a duplicate spelling → counted once;
- `lint_skip_bug_gate.sh --files` combined with `--tests-dir`, empty
  `--files`, and a missing path → each refused with exit 2 and an
  `error`/`message`/`remedy` triple.

The whole-tree default modes of both lints are clean on the RC: 0
anti-pattern findings across 639 files, and the skip gate reports 0
missing files.

### Finding 12: WRITER-029 ACs — the guideline section
**Category:** pass

The `--files` section (`docs/test-authoring-guidelines.md:625-654`) says
which two checks run, over named files only. It says findings print and
make the run exit non-zero. It says whole-tree guards and the rest of the
fleet are skipped, and that the findings file and ledger are never
written. The flags it cites (`--files` on both runners and both lints)
match each script's `--help`. `grep -nE 'BUG-[0-9]{4}|PRIORITY-[0-9]{4}'`
over the section returns nothing. The exit-files subsection (696-704)
agrees with the behavior.

### Finding 13: Constraints (Step 6.9) and census
**Category:** pass

`set -euo pipefail` is kept in both runners and the skip gate. No secrets
appear in added lines. The new refusal sites carry census anchors with
Locator Index entries: `run-unit-tests-targeted-files-05` and
`run-integration-tests-targeted-files-05` (AGENT-RESOLVABLE, row 72), and
`lint-skip-bug-gate-files-usage-01` (CONVERT).

The new runner verdict is AGENT-RESOLVABLE and confined to targeted runs.
Certification runs keep warn-and-continue. That matches PRIORITY-0006's
"exit code reflects them as the ticket's own acceptance requires" as PM
read it: it is not a new hard gate on certification.

### Finding 14: Prerequisite graph (Step 6.10)
**Category:** pass

The chain runs CM-20261003-030 (open) → CODER-027 → CODER-028 →
WRITER-029 → TESTER-031 → CM-20261003-033 (release, WAITING on all four).
There are no cycles and every ID exists.

### Finding 15: Skip-cites-real-bug grep-gate (Step 6.13)
**Category:** pass

The `BUG-SKIP` and synthetic `BUG-12xx`/`BUG-98xx` hits in tests are
string-literal fixtures, not skip calls (the sanctioned example-value
exception). The structural whole-tree gate passes with 0 missing files.

### Finding 16: Skip-gate "missing bug file" findings do not name file and line, though the docs and runner message say every finding does
**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated to one gate's output and two prose claims)
**Filed As:** BUG-0523-skip-gate-missing-id-no-file-line (Path C-A)

On a `--files` run, a skip citing a non-existent `BUG-NNNN` produces
`BUG-9999: no matching file found in <bugs-dir>`, with no path and no
line. `docs/test-authoring-guidelines.md:645` ("Each prints its findings
in full, naming file and line") and both runners' new
`targeted-file-lint-findings` message ("Each finding is printed above,
naming file and line") overclaim. The gate's output shape predates the
RC (ai_main is identical); the new promise is this RC's.

### Finding 17: `lint_skip_bug_gate.sh --files` de-duplicates by raw path text
**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (only direct invocation with two spellings of one path; the runners pass normalized absolute paths)
**Filed As:** BUG-0524-skip-gate-files-dedup-raw-path (Path C-A)

`--files x.py ./x.py` reported the same placeholder finding twice
("2 finding(s)"). The script's own comment promises a path named twice is
checked once. The anti-pattern lint resolves paths and counts it once.

### Finding 18: The guideline's exit-code summary was not updated for the targeted-run lint exit
**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (one prose line; the `--files` section and `--help` are correct)
**Filed As:** BUG-0525-guidelines-exit-code-summary-stale (Path C-A)

`docs/test-authoring-guidelines.md:588` still says exit 1 means "one or
more failed (or the lint pre-flight failed)". Both runners' `--help` now
list the `--files` per-file lint finding as a cause of exit 1. This is a
Documentation Discipline gap. WRITER-029 flagged the line as outside its
scope.

### Finding 19: Runner `--help` overwrites the certification findings file (predates this RC)
**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (any agent that checks runner options between a certification run and TESTER's read silently erases quality findings; WRITER tickets routinely run `--help`)
**Filed As:** BUG-0526-runner-help-overwrites-findings-file (Path C-A)

I seeded `findings-unit.json` and `findings-integration.json` and ran
`--help` on each runner. Both files were replaced with
`{"findings": [], ...}`, and "findings file written" printed twice.
ai_main 712135d2 (v2.27.1) behaves the same, so this RC did not introduce
it. WRITER-029 reported it after its AC told it to run `--help`. Found
under Step 6.11 (an untested mechanism, adjacent to PRIORITY-0006 AC3's
no-touch guarantee).

## Lint Findings (non-blocking)

- `engine_size_dupe` (unit and integration, same instance):
  `lib/cm/release_finalize.sh` is 846 lines against the 800-line engine
  ceiling. Cites BUG-0519-release-finalize-over-engine-ceiling (open).

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=26d freshness=stale — the last cold build (v2.9.20) failed for docker/podman claude-abliterated and podman/rocky9, already filed as BUG-0167-cold-build-claude-abliterated-failure; the record is stale on both counts (26d exceeds the 7-day window, and v2.9.20 is not the v2.27.2 under verification), so nothing is known about whether the images build cold against this tree.

## Autonomous criterion

1. Tasks force-promoted from WAITING to BACKLOG by hand? **NO.** Status
   summaries show "Promoted from WAITING to BACKLOG by wake script".
2. `release-state.md` edited outside the CM scripts? **NO.** Its mtime
   (16:12) matches CM-20261003-030's RC open at 16:12:05.
3. Scripts patched mid-build? **NO.** No live-install file under
   `scripts/`, `roles/` or `pm-agent/` is newer than the RC open. The only
   RC commits touching `team/scripts/` are this RC's own CODER deliverables.
4. Queue files edited by hand? **NO.** Queue mtimes coincide with wake
   dispatch and agent transitions.
5. Remote git operation by any non-CM agent? **NO.** No
   `logs/git-boundary-guard.jsonl` exists in the PM-003, CM-030, CODER-027,
   CODER-028, WRITER-029 or TESTER-031 task folders.

Autonomous operation criterion: Manual interventions required: none

## Caveats

- Step 6.8 (synthetic release simulation) was skipped. The diff touches
  test runners and lints, not release-lifecycle files.
- Behavioral probes ran in a `git clone` of the worktree under
  `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester031/`, with
  sandboxed `KANBAN_ROOT`, `PGAI_AGENT_KANBAN_ROOT_PATH` and
  `PGAI_AGENT_KANBAN_TEMP_DIR`. The task worktree was never written to.
- Test fidelity: the 20 new runner tests run the real runner inside a
  fixture tree whose fatal gates are stubs. My end-to-end runs with the
  real tree's gates confirm the same behavior, so this is not a concern.
- The wake prompt forbids a feature branch, so verification artifacts are
  in the task folder only and nothing was committed to the RC.
- Early in the session I ran `run-unit-tests.sh --help` against the
  default temp root, which overwrote `/tmp/pgai_kanban_tmp/lint/findings-unit.json`
  (Finding 19). The certification run that followed rewrote it, so the
  findings cited above come from that run.
