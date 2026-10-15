# Verification Report: ai_rc/v2.13.1

**Task:** TESTER-20260909-029-verify-2-13-1
**Project:** pgai-agent-kanban
**RC branch:** `ai_rc/v2.13.1` @ `1e141fb` (worktree in detached HEAD at that commit)
**Base:** `ai_main`
**Requirements doc:** `projects/pgai-agent-kanban/requirements/v2.13.1-bugfix-bundle-20260909.md`
**Worktree:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260909-029-verify-2-13-1`

## Summary

v2.13.1 is a five-item bug bundle: refresh the engine-baseline wake-log golden
(BUG-0213), convert the three `cm/release.sh` census Locator Index entries from
line numbers to named anchors (BUG-0215), route the gated runners' RC-branch
detection through a shared prefix helper so the shell and Python halves agree on
an unconfigured install (BUG-0211), and clear shipped comments that point at the
unshipped `requirements/` directory or cite a real internal bug number, plus the
lint patterns that keep them out (BUG-0212, BUG-0214). Verification read the full
`ai_main..ai_rc/v2.13.1` diff (46 files, +531/-157), ran both gated runners with
coverage, executed every acceptance criterion of all five bundled bug reports as
a command, and behaviorally exercised the two pieces of guard logic this RC
touched (the census anchor's line-shift immunity and the new provenance lint
patterns) by triggering their failure modes rather than reading the code. Both
gated runners exit 0 and both findings files are empty. Every acceptance
criterion of every bundled item passes. One pre-existing defect unrelated to the
bundle surfaced in the Step 6.11 spot-check and is filed as BUG-0216; it is why
the recommendation is SHIP-WITH-CONCERNS rather than PASS.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

low

## Findings

### Finding 1: BUG-0213 — engine-baseline wake-log golden refreshed

**Category:** pass

The golden at `team/tests/fixtures/engine-baseline/wake-log.txt` now carries the
four `config_loader: load_config: [release] ... applying documented default ...`
narration lines, and `git diff ai_main..ai_rc/v2.13.1` on that file contains
those four added lines and nothing else (acceptance 3). The gated integration
runner exits 0 (acceptance 1) and
`tests/integration/test_engine_behavior_baseline_harness.py` reports `..` — 2
passed (acceptance 2). The gated unit runner exits 0 (acceptance 4).
`team/scripts/lib/config_loader.sh` is untouched by the diff, so the narration
itself was not weakened to make the lock green.

### Finding 2: BUG-0215 — `cm/release.sh` census entries converted to named anchors

**Category:** pass

`python3 team/scripts/lint_gate_census_compliance.py` reports
`OK -- 34 file(s) checked, 0 offenders.` with 111 anchored sites exempted
(acceptance 1). All three Locator Index entries for `team/scripts/cm/release.sh`
are now `path:token CLASS` form — `operator-force-reason-required-01`,
`operator-force-wake-context-01`, `release-git-step-failure-01` — and each token
resolves to a real marker in the file (acceptance 2). The `git_step` refusal,
which previously carried neither a class comment nor an emitter call and was
exempt only through its stale line number, now carries a `BLOCK-WORTHY` class
comment, a `pgai_emit_error_contract` call with all three fields populated, and
its own anchor. The unit runner exits 0 and
`${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-unit.json` contains
`"findings": []` — no `gate_census_compliance` entries (acceptance 3).

**Behavioral verification (Step 6.5).** Structural checks alone would not show
that the anchors actually buy line-movement immunity, so the failure mode was
triggered in a throwaway `git archive` copy of the RC:

- Inserting five filler lines near the top of `cm/release.sh` moved the three
  `exit 1` sites from 171/189/264 to 176/194/269. The census lint stayed at
  `0 offenders` — the exact edit shape that produced BUG-0215 no longer produces
  a finding.
- Deleting the new `# CENSUS-ANCHOR: release-git-step-failure-01` marker made the
  lint report that site again (`team/scripts/cm/release.sh:274 -- refusal site is
  missing a call to the dual-channel error-contract emitter`), confirming the
  exemption is the marker's doing and not an unconditional pass.
- Restoring the marker and deleting its Locator Index line produced the same
  finding — the token only exempts when both halves are present, as SOP.md
  requires.

### Finding 3: BUG-0211 — gated-runner RC-branch prefix fallback

**Category:** pass

`pp_rc_branch_prefix()` was added to `team/scripts/lib/project_paths.sh` with
`PP_RC_BRANCH_PREFIX_DEFAULT="ai_"`, and both runners now call it with
`${PGAI_PROJECT_NAME:-pgai-agent-kanban}` instead of reading
`${TEAM_ROOT}/projects/pgai-agent-kanban/project.cfg` by hardcoded path
(acceptance 2). Resolution was verified against six sandbox kanban roots, each
built under `$PGAI_AGENT_KANBAN_TEMP_DIR` with `PGAI_AGENT_KANBAN_ROOT_PATH`
repointed, never against the live install:

| Sandbox root | `pp_rc_branch_prefix` |
|---|---|
| empty root, no `projects.cfg` | `ai_` |
| project dir present, no `project.cfg` | `ai_` |
| `project.cfg` present, no `branch_prefix` key | `ai_` |
| `branch_prefix = ""` | `` (empty) |
| `branch_prefix =` (bare empty) | `` (empty) |
| `branch_prefix = zz_` | `zz_` |

The assembled regex was then matched against branch names, reproducing the whole
runner block:

| Root | Regex | `ai_rc/v2.13.1` | `rc/v2.13.1` | other |
|---|---|---|---|---|
| no `project.cfg` | `^(rc/\|ai_rc/)` | MATCH | MATCH | `feature/x` NO-MATCH |
| `branch_prefix = ""` | `^(rc/)` | NO-MATCH | MATCH | — |
| `branch_prefix = zz_` | `^(rc/\|zz_rc/)` | NO-MATCH | MATCH (`rc/v1.0.0`), MATCH (`zz_rc/v1.0.0`) | — |

That is acceptance 1 (unconfigured install still enters RC mode for an
`ai_rc/*` HEAD, agreeing with `lint_changelog_freshness._resolve_branch_prefix()`,
which returns `'ai_'` under the same roots) and acceptance 3 (bare `rc/` always
matches; a deliberately empty configured prefix gets `rc/` only).

The production shape was exercised too, not just the sandbox: in this detached-HEAD
worktree `git symbolic-ref --short HEAD` is empty, the `--points-at HEAD` fallback
resolves `ai_rc/v2.13.1`, the regex matches, and the unit run's CHANGELOG gate
reports `lint_changelog_freshness: ok — CHANGELOG.md is fresh` with
`changelog_freshness: OK`. Both gated runners exit 0 (acceptance 4).

### Finding 4: BUG-0212 — shipped comments no longer point at unshipped `requirements/`

**Category:** pass

`grep -rn 'requirements/v[0-9]' team/scripts/ team/pgai_agent_kanban/ install.sh`
returns nothing outside `team/tests/` (acceptance 1). The 38 rewritten headers
were sampled (`lib/wake_task_transition.sh`, `lib/discovery_bundle.sh`,
`lib/discovery.sh`, `lib/cm/release_known_issue.sh`, `lint_engine_size_dupe.py`,
`lint_lib_function_dedupe.py`, `lint_governance_byte_budgets.sh`) and each states
its reason inline — the shared per-file line ceiling, the split-out, the
invariant — without naming an internal release or a document the reader cannot
open (acceptance 2). `python3 team/scripts/lint_comment_provenance.py` reports
`OK — 0 findings across 569 file(s)` and exits 0, and the diff adds no new
`# provenance-allowlist:` marker anywhere (acceptance 4). Both gated runners exit
0 (acceptance 5).

**Behavioral verification of the two new lint patterns (acceptance 3, Step 6.5).**
Rather than trusting the added unit cases, three files were planted in a
throwaway directory and scanned with `--code-dirs`:

| Planted comment | Result |
|---|---|
| `# Rationale lives in the plan (see requirements/v9.9.9-widget-split.md).` | exit 1, finding — correct |
| `# Rationale recorded in docs/audits/v9.9.9-widget-split.md, which ships.` | exit 0, no finding — correct |
| `# Split out during v9.9.9-widget-split to stay under the line ceiling.` | exit 1, finding — correct |

The eight added unit cases in `team/tests/unit/test_lint_comment_provenance.py`
cover the same two patterns plus the `.md`-filename-value, the
`match this pattern` format-description, the release-notes prose exemption for
the bare slug (but not for the path form), and the `team/tests/` Exception 7
label set. Read in full; they assert the behavior, not the implementation.

### Finding 5: BUG-0214 — `defer_scope.sh` no longer cites a real bug number

**Category:** pass

`grep -n 'BUG-0107' team/scripts/dashboard/lib/defer_scope.sh` returns nothing
(acceptance 2). `python3 team/scripts/lint_comment_provenance.py` exits 0 with
`OK` and 0 offenders (acceptance 1). The comment is a rewording, not a deletion —
it still explains that a bug filed as `BUG-NNNN-some-title.md` renders as the
card `BUG-NNNN` and therefore why the deferred-ID set has to speak the compact
form (acceptance 3). The unit runner exits 0 (acceptance 4).

### Finding 6: requirements-doc acceptance criteria

**Category:** pass

- "Each bundled item is addressed in the implementation" — Findings 1-5 above;
  every acceptance criterion of all five bundled bug reports was executed as a
  command and passed.
- "All Python files compile cleanly" — `py_compile` on every changed `.py`, then
  `python3 -m compileall -q team/pgai_agent_kanban team/scripts` — OK.
- "All bash files pass syntax check" — `bash -n` on every changed `.sh` (26
  files) and on every `.sh` under `team/scripts/` — 0 failures.

### Finding 7: census anchor markers that no Locator Index entry names

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (inventory-accuracy only; no runtime behavior is affected, and every affected refusal still refuses correctly)
**Regression:** no (`ai_main` carries 21 unpaired markers; this RC reduced the count to 19 and introduced none of them)
**Integrity Damage:** no
**Filed As:** BUG-0216 (Path C-A — filed as BUG; code defect / regression / test failure)

Found in the Step 6.11 spot-check under the "regressions of prior gaps" category
(the BUG-0196 -> BUG-0215 lineage). 19 `# CENSUS-ANCHOR: <token>` markers in the
shipped tree have no matching `path:token CLASS` line in the census's fenced
`CENSUS-LOCATOR-INDEX` block. Two of them sit in files the lint scans:
`team/scripts/run-unit-tests.sh:dockerfile-static-lint-01` (line 234) and
`team/scripts/lint_engine_size_dupe.py:engine-size-dupe-01` (line 723).
`team/SOP.md` ("Writing a compliant anchor") states that neither half inventories
a site on its own, that "the lint reports each half-authored anchor as its own
finding", and that the lint "reconciles the census against the tree in both
directions". The shipped `lint_gate_census_compliance.py` implements only one
finding kind — missing class comment and/or missing dual-channel emitter — and
uses the parsed Locator Index solely to build an exemption set
(`_resolve_anchor_marker_lines()` returns the empty set when a file has no token
entries). There is no marker-to-index reconciliation code path, so the lint
prints `0 offenders` on a tree carrying two half-authored anchors in scanned
files. Several of the 19 are narrated in census prose (`engine-size-dupe-01` at
`docs/blocking-site-census.md:374`, `dockerfile-static-lint-01` in the row-54
table at line 152) but prose is not the machine-read index.

This does not affect this RC's deliverables and does not weaken any refusal: all
19 sites still refuse, and the two in scanned files pass the lint today on their
own inline class comment and emitter call. What is lost is inventory truth — a
reader or a gate-authoring agent following SOP.md reads a marker as "inventoried
and classified", and for these sites the index makes no claim.

## Lint Findings (non-blocking)

None. `${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-unit.json` and
`findings-integration.json` were both written by this run and both contain
`"findings": []`. All 18 unit-side and 13 integration-side quality-class gates
reported `OK`.

## Test Suites

Authoritative verdict from the gated runners, run from the worktree root:

| Suite | Command | Exit | Result |
|---|---|---|---|
| Unit | `bash team/scripts/run-unit-tests.sh --coverage` | **0** | 3532 passed, 3 skipped in 442.73s |
| Integration | `bash team/scripts/run-integration-tests.sh --coverage` | **0** | 420 passed, 1 skipped in 335.40s |

`UNIT_EXIT == 0` AND `INTEGRATION_EXIT == 0`. The PASS evidence cited here is the
gated runners' exit codes, not a direct `pytest` "N passed" line. Direct pytest
was not used to establish any verdict.

Step 6.8 (synthetic release simulation) applies — the diff touches
`team/scripts/cm/release.sh`, five files under `team/scripts/lib/cm/`, and
`team/scripts/lib/project_paths.sh`. `tests/integration/test_cm_release_synthetic.py`
ran inside the gated integration suite and reported 15 passed.

## Code Coverage

Read from the `totals` object of the runner-written JSON reports (field names
confirmed against the generated files, not assumed).

- Unit:        line 74%  |  branch 61%   (14255 stmts, 4366 branches; blended `TOTAL` 71%)
- Integration: line 8%   |  branch 6%    (blended `TOTAL` 7%)

Informational only. Coverage plays no part in the recommendation, the systemic
risk, or the suite-pass precondition.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=2d freshness=stale — the docker/claude-abliterated, podman/claude-abliterated and podman/rocky9 pairs failed at their Dockerfile FROM/COPY steps; cites BUG-0167, filed by that deep-verify run and since closed as done, so the last recorded lane result predates its own fix and no lane has run against the v2.13.1 tree.

## Autonomous criterion (Step 6.12)

1. Any task force-promoted from WAITING to BACKLOG manually? **NO.**
   `TESTER-20260909-029` records "Promoted from WAITING to BACKLOG by wake script:
   prerequisites satisfied"; the four CODER tasks and CM-028 show normal
   wake-script transitions.
2. `release-state.md` edited outside `cm-open-rc.sh` / `cm-release.sh` /
   `cm-cancel-rc.sh`? **NO.** `## Active RC: v2.13.1`, `## RC Opened By Task:
   CM-20260909-028-open-rc-2-13-1`, `## Last Released: v2.13.0`, all consistent
   with CM-028's own status summary.
3. Scripts patched mid-build? **NO.** All eight commits on the RC are CODER
   feature-branch merges and their single implementation commits; there is no
   out-of-band tooling commit.
4. Queue files manually edited? **NO.** `coder_backlog.md` carries `[x]` for
   024-027, `tester_backlog.md` carries `[A]` for this task — the ordinary
   markers.

Autonomous operation criterion: Manual interventions required: none.

## Other checks

- **Pre-flight (Step 2a).** No DONE task retains a live feature branch (CODER and
  WRITER delete after merge, per procedure), so Check 1 and Check 2 had nothing to
  evaluate and produced no anomaly. Worktree HEAD `1e141fb` equals
  `ai_rc/v2.13.1`; `git status --short` empty; `git stash list` empty.
- **Step 6.1 release-state drift.** `pp_last_released_version` returns `v2.13.0`;
  `semver_gt v2.13.1 v2.13.0` is true; `## Last Released` is a well-formed
  `vX.Y.Z`; `## Active RC: v2.13.1` and the `ai_rc/v2.13.1` branch both exist. No
  drift.
- **Step 6.10 prereq graph.** `CM-20260909-028-open-rc-2-13-1` ->
  `CODER-20260909-{024,025,026,027}` -> `TESTER-20260909-029` ->
  `CM-20260909-031-release-2-13-1`. All referenced IDs exist, sequence numbers
  024-031 do not collide, no cycles, bookends present.
- **Step 6.13 skip-cites-real-bug.** No `BUG-SKIP` or alphabetic-suffix
  placeholder in a skip annotation, and no cited `BUG-NNNN` missing from `bugs/`.
  The only literal `BUG-NNNN` hits are inside archived runner logs under
  `team/tests/artifacts/`, which are captured output, not skip annotations. The
  gated `skip_cites_real_bug` lint also reported OK in both suites.
- **Step 6.14 coding-standards spot-check.** No new script was added (the change
  is a new library function), so directive 6's `--help` requirement has no new
  subject. `comment_provenance` and `help_presence` gates both OK. The one
  hardcoded value added, `PP_RC_BRANCH_PREFIX_DEFAULT="ai_"`, is the fix's whole
  point — it exists to mirror `lint_changelog_freshness._DEFAULT_BRANCH_PREFIX` —
  and is documented as such at its definition.
- **Test fidelity.** The BUG-0211 fix is shell logic behind `set -euo pipefail`,
  and it was exercised through the real gated runners under the real detached-HEAD
  worktree shape rather than only through a Python-level proxy. Every ad-hoc
  reproduction in this run repointed `PGAI_AGENT_KANBAN_ROOT_PATH` and
  `KANBAN_ROOT` at a fresh `mktemp -d -p "$PGAI_AGENT_KANBAN_TEMP_DIR"` root; no
  state-mutating command ran against the live install.

## Caveats

These are observations, not findings. None is categorized, none is filed, and
none affects the recommendation.

- **The runners now delete `KANBAN_ROOT` from the environment.** Both gated
  runners set `KANBAN_ROOT="$TEAM_ROOT"` before calling `pp_rc_branch_prefix`
  (which requires it) and then `unset KANBAN_ROOT` on the cleanup line. On this
  install `KANBAN_ROOT` is already exported with the same value, so the net effect
  is that the variable is removed for the rest of the runner and for the pytest
  subprocess rather than restored. Nothing after that point in either runner reads
  it (the two later mentions are comment text), and the only consumer in the tree,
  `team/pgai_agent_kanban/ops/context.py:82`, treats it as a legacy fallback
  behind `PGAI_AGENT_KANBAN_ROOT_PATH`, which stays set. Both suites are green, so
  there is no observed effect; saving and restoring the prior value would be
  tidier than unsetting it.
- **The Python half of the branch-prefix pair is still project-hardcoded.**
  `lint_changelog_freshness._resolve_branch_prefix()` reads
  `projects/<_DEFAULT_PROJECT_NAME>/project.cfg` with `_DEFAULT_PROJECT_NAME =
  "pgai-agent-kanban"`, while the shell half now resolves the project from
  `PGAI_PROJECT_NAME`. The two therefore agree on the kanban self-project and on
  any unconfigured install — which is all BUG-0211's acceptance criteria ask for,
  and all of it verified above — but for some other project configured with a
  non-`ai_` prefix the Python half would still answer for the kanban project. Out
  of scope for this bundle; noted so the remaining asymmetry is on the record.
- **Two engine files sit exactly at the 800-line ceiling.**
  `team/scripts/lib/cm/release_finalize.sh` and
  `team/scripts/lib/cm/release_ship_policy.sh` are both exactly 800 lines after
  this RC's comment rewrites (CODER-027 caught and corrected its own 801-line
  overshoot). `lint_engine_size_dupe` is green, but neither file has a single line
  of headroom left.
- **90 Locator Index entries still use the line-number form**, 47 of them in files
  the census lint scans — the fragility BUG-0196 and BUG-0215 each hit once. It is
  recorded as related context inside BUG-0216 rather than as a finding of its own,
  because BUG-0215 already named it and this RC's own edits to
  `run-unit-tests.sh` and `run-integration-tests.sh` (which carry nine such
  entries between them) happened not to disturb them.
- **A comment line in `team/scripts/lib/discovery.sh` was not re-wrapped** after
  the header rewrite and now runs well past the surrounding column width
  ("...cross-file dependencies. Source order does not matter for bash function
  resolution"). Cosmetic.
