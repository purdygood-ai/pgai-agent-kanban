# Verification Report: rc/v2.13.0

**Task:** TESTER-20260908-053-verify-2-13-0
**RC branch:** `ai_rc/v2.13.0` @ `7cd5f60`
**Baseline:** `ai_main` (v2.12.1)
**Requirements:** `requirements/v2.13.0-posture-config-deferral-and-piggyback.md`
**Worktree:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260908-053-verify-2-13-0` (detached HEAD, clean, no stashes)
**Verified:** 2026-09-08

## Summary

v2.13.0 makes bug deferral a configuration posture. Four `[release]` keys
(`deferred_bugs`, `deferred_bug_level`, `defer_piggyback`, `deep_verify`) are
registered and validated, a four-tier resolver answers "what is the posture and
where did it come from" (campaign file > project.cfg > kanban.cfg > default),
a `DEFER-LOW-BUGS` campaign file overlays the posture until a named release and
is spent by CM at ship, discovery holds deferred bugs back from opening an RC
and carries them onto a bundle a higher-severity bug already triggered, and the
dashboard, status bar and a new `why.sh` verb report the resolved posture.

Verification covered the full release methodology: pre-flight merge check, both
gated runners, coverage, lint findings ingestion, deep-verify freshness, and
Steps 6.1 through 6.14. The functional core of the release is genuinely good --
every precedence tier, every refusal, the arm/spend/undefer lifecycle, the
severity matrix, the status-bar text and the card glyphs were exercised
behaviorally in a throwaway sandbox root and all behaved exactly as specified.

Two things stop this being a clean pass. First, the unit gated runner exits 1
on this tree: a one-line `command -v` availability guard added to
`config_loader.sh` is misread by the dual-channel-pair lint as a malformed
emitter call, and the test that asserts the tree produces zero lint findings
fails. The same lint reports zero offenders on `ai_main`, so this is a
regression this RC introduces in its own certification gate. Second, the
piggyback deliverable shipped only half of what the requirements specify: the
carry and its narration work, but the "consolidated, <=K CODER tasks, default
K=2, grouped by area" cap does not exist anywhere, and PM's role file -- which
two shipped documents claim consolidates on the ride-along annotation -- was
never told the annotation exists.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

high

The rating is carried by Finding 1 alone, on the rubric's first `high`
condition: something that worked in the last shipped version does not work in
this one. `bash team/scripts/run-unit-tests.sh` exits 0 on `ai_main` and exits 1
on this RC. Nothing else in the report reaches `high`; no finding damages chain
integrity or data, and every fix is small. The release itself is usable -- the
feature it ships works, integration is green, and the synthetic release
simulation passes -- which is why the recommendation is SHIP-WITH-CONCERNS and
not SHIP-WITH-SERIOUS-CONCERNS. CM reads the risk, regression and fix-effort
fields and applies its own policy.

## Test Suites

| Suite | Gated runner | Exit | Result |
|---|---|---|---|
| Unit | `bash team/scripts/run-unit-tests.sh --coverage` | **1** | 3509 passed, **1 failed**, 3 skipped (412.75s) |
| Integration | `bash team/scripts/run-integration-tests.sh --coverage` | **0** | 433 passed, 1 skipped (321.31s) |

The single unit failure is `tests/unit/test_lint_dual_channel_pair.py::test_real_tree_produces_zero_findings`
(Finding 1). No other test in either suite failed. The PASS evidence quoted here
is the gated-runner exit code, not a direct-pytest line.

## Code Coverage

- Unit:        line 74%  |  branch 61%   (14233 stmts, 4356 branches; blended 71%)
- Integration: line 8%   |  branch 6%    (blended 7%)

Read from `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/{unit,integration}.json`
(`totals.percent_statements_covered` and `totals.percent_branches_covered`,
field names confirmed against the generated files). Informational only.

## Findings

### Finding 1: unit gated runner exits 1 -- dual-channel lint misreads a `command -v` guard as an emitter call

**Category:** bug
**Fix Effort:** small (1 CODER task)
**Systemic Risk:** high (regression: the suite passed at v2.12.1 and fails at v2.13.0)
**Regression:** yes (verified against the baseline -- see evidence below)
**Integrity Damage:** no
**Filed As:** BUG-0206 (Path C-A -- filed as BUG -- code defect / regression / test failure)

`lint_dual_channel_pair` reports exactly one offender on the RC tree:

```
team/scripts/lib/config_loader.sh:153 - pgai_emit_error_contract call is missing the remedy field(s)
```

Line 153 is not a call. It is the bootstrap guard this RC added:

```bash
153: if ! command -v pgai_emit_error_contract >/dev/null 2>&1; then
154:     # shellcheck source=error_contract.sh
155:     source "$(dirname "${BASH_SOURCE[0]}")/error_contract.sh"
156: fi
```

The four real emitter calls in the same file (lines 290, 314, 336, 366) each
supply error, message and remedy correctly -- I read all four. The lint's bash
call-site scanner has no skip for `command -v` / `type` / `declare -F`
operands, so it reads the identifier as an invocation with one argument.

Baseline evidence (this is what makes `Regression: yes` a verified value rather
than a default): `git archive ai_main | tar -x -C <tmpdir>` then
`python3 team/scripts/lint_dual_channel_pair.py` in that tree prints
`OK - 209 file(s) checked, 0 offenders` and exits 0. `config_loader.sh` on
`ai_main` contains no occurrence of `pgai_emit_error_contract` at all. The lint
and its test are byte-identical between the two trees -- `git diff --stat
ai_main..ai_rc/v2.13.0 -- team/scripts/lint_dual_channel_pair.py
team/tests/unit/test_lint_dual_channel_pair.py` is empty.

Note the internal inconsistency this exposes: `defer_low_bugs.sh`, added in the
same RC, sources `error_contract.sh` unconditionally -- commit `5529852`
("Source error_contract.sh unconditionally in defer_low_bugs.sh") changed it to
do so -- while `config_loader.sh` kept the guarded form. Either shape should be
allowed; the lint should not be the thing that decides.

### Finding 2: piggyback carry ships without the specified task cap, and PM was never told the annotation exists

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks, or 1 WRITER task if the cap lands in PM.md)
**Systemic Risk:** medium (a real defect that ships and the next iteration fixes; nothing regresses)
**Regression:** yes (this RC's changes produced the incomplete implementation; the capability did not exist before, so nothing that previously worked has stopped)
**Integrity Damage:** no
**Filed As:** BUG-0208 (Path C-A)

The requirements doc specifies the carry twice -- Deliverables: "carry ALL
deferred bugs along (consolidated, <=K CODER tasks, default K=2, grouped by
area)"; Acceptance: "deferred lows ride iff piggyback=true (consolidated <=K
tasks, narrated)".

What shipped and works (verified by reading `discovery_steps.sh` and the new
integration fixtures): the carry itself, the per-item ride-along annotation
written by `discovery_state.sh`, and the narration
`discovery_step_bugs: piggybacking N deferred bugs (trigger: BUG-XXXX <severity>)`
at `discovery_steps.sh:323`, matching the requirements string exactly.

What did not ship: any K. `grep -rn 'K=2\|piggyback_max\|max_consolidat' team/ docs/`
returns nothing; there is no config key, no constant, no default of 2, and no
grouping-by-area logic. `git diff --stat ai_main..ai_rc/v2.13.0 -- team/roles/`
is empty, and `grep -n 'ride-along\|piggyback' team/roles/PM.md` finds nothing --
yet `team/scripts/lib/discovery_state.sh:629` states "PM (team/roles/PM.md)
parses this exact string to recognise a ride-along and consolidate it into a
hygiene ticket rather than a headline one" and `team/SOP-core.md:285` states
"annotated as a ride-along on the trigger so PM consolidates it". Two shipped
documents describe a PM behavior that PM's role file does not define.

The practical consequence: nothing bounds how many CODER tickets a carried
backlog decomposes into. A trigger carrying fifteen deferred lows can produce
fifteen headline tickets -- the outcome the cap was specified to prevent.

### Finding 3: shipped config templates and loader header state the new keys are not read for behavior

**Category:** bug
**Fix Effort:** small (1 WRITER task)
**Systemic Risk:** low (documentation that steers an operator away from a working feature; nothing breaks)
**Regression:** yes (the false statements are introduced by this RC)
**Integrity Damage:** no
**Filed As:** BUG-0209 (Path C-A)

`kanban.cfg_example` and `project.cfg_example` both carry, in the `[release]`
deferral block: "This ticket registers the keys and their validation only --
nothing reads them for behavior yet." `project.cfg_example` adds "a
project.cfg-vs-kanban.cfg precedence resolver (mirroring max_rc_retries above)
is later work." `team/scripts/lib/config_loader.sh:169-175` repeats both claims.

All of it is false in the tree that ships it. The resolver is
`team/scripts/lib/defer_low_bugs.sh`, present in the same release; discovery
(`discovery_steps.sh:271`), the CM spend stage, the dashboard renderers and
`why.sh` all read the resolved posture. `team/SOP-core.md` documents the
behavior correctly, so the templates contradict the governance document beside
them. These are first-ticket comments that later tickets in the same release
invalidated and nobody revised.

### Finding 4: the refusal an operator actually meets does not name the accepted set

**Category:** gap
**Fix Effort:** small (1 CODER task)
**Systemic Risk:** low (hygiene -- the refusal is loud and names the file and the bad value; only the vocabulary is missing)
**Regression:** yes (introduced by this RC's new resolver)
**Integrity Damage:** no
**Filed As:** BUG-0210 (Path C-A)

The requirements state the doctrine twice -- Deliverables: "loader refuses
unknown values loud, naming the accepted set -- BUG-0107 doctrine"; Acceptance:
"Loader: unknown value for any key refuses loud with the accepted set".

`config_loader.sh` honours it. Observed:

```
remedy:  Set [release] deferred_bugs to one of: true|false in <cfg> ('on'/'off' are not accepted spellings).
```

`defer_low_bugs.sh` -- the path every runtime consumer takes -- does not.
Observed in a sandbox root with `defer_piggyback = maybe` in `project.cfg`:

```
error:   defer_low_bugs.invalid_release_value
message: [release] defer_piggyback='maybe' in <cfg> is not a recognized value.
remedy:  Fix or remove [release] defer_piggyback in <cfg> so it inherits kanban.cfg or the built-in default.
```

The refusal is loud, dual-channel and never falls through to a lower tier -- the
important half of the doctrine holds. Only the accepted set is absent. The two
paths also duplicate the validators (`_defer_low_bugs_validate_bool` /
`_defer_low_bugs_validate_level` re-implement `_config_loader_validate_release_bool` /
`_config_loader_validate_deferred_bug_level`), which is how they drifted.

### Finding 5: deferral posture resolver -- four-tier precedence

**Category:** pass

Exercised in a throwaway sandbox root (`PGAI_AGENT_KANBAN_ROOT_PATH` and
`KANBAN_ROOT` repointed per the live-install safety rule; the operator's kanban
root was never written to). Every tier resolves and reports its own source:

| Setup | `deferred_bugs` | source | `deferred_bug_level` | source |
|---|---|---|---|---|
| nothing set | false | default | low | default |
| kanban.cfg only | true | kanban.cfg | medium | kanban.cfg |
| + project.cfg override | true | kanban.cfg | high | project.cfg |
| + project-scoped campaign file | true | **campaign file** | high | project.cfg |

The campaign file overrides `deferred_bugs` only and leaves the other two keys
resolving through their own tiers, exactly as the requirements and
`team/SOP-core.md` say. `defer_until=v2.20.0` / `defer_until_source=campaign file`
appear only when a file is live.

### Finding 6: malformed-flag handling -- never silently off

**Category:** pass

Three unparseable shapes were triggered and all three refuse loud, non-zero,
without falling through to a lower tier or resolving to "deferral off":

- `until=2.20` -> `defer_low_bugs.campaign_file_malformed_until`, exit 1.
- empty file -> `defer_low_bugs.campaign_file_unparseable`, exit 1.
- `defer.sh --until 2.20` -> `defer.malformed_until`, exit 1, nothing written to disk.

`on` for a boolean is refused (`defer_low_bugs.invalid_release_value`, exit 1);
`urgent` for the severity enum is refused the same way. All refusals carry the
full error/message/remedy triple.

One behavior worth recording without categorising it as a finding: an invalid
value in `kanban.cfg` that is shadowed by a valid `project.cfg` value for the
same key is never reached and never refused. That is a defensible reading of
"the value is never used", and it matches how `max_rc_retries` behaves.

### Finding 7: campaign-file lifecycle -- arm, explain, spend, clear

**Category:** pass

- `defer.sh --project demo --until 2.20.0` normalises the leading `v`, writes
  `until=v2.20.0`, and prints the path it wrote.
- `why.sh --project demo` names every value and its tier, and the campaign
  file's path alongside the `until` target.
- `cm_release_spend_campaign_file` at ship of `v2.19.0` leaves the file in place
  ("still in force"); at `v2.20.0` it removes the file and appends a
  `## Campaign Spend Event` block to `release-state.md`; at `v3.0.0` against an
  `until=v2.20.0` file it also spends -- the latching `>=` works, so a skipped
  target does not strand the file.
- A malformed file at ship time is narrated as a finding, left in place for a
  later ship, and never fails the release (exit 0).
- `undefer.sh` removes the file and is idempotent (second run exits 0 with
  "nothing to clear").
- The root-scoped `$KANBAN_ROOT/DEFER-LOW-BUGS` is deliberately never spent by a
  single project's release, and `docs/operator-commands.md:388` plus
  `team/SOP-core.md:279` both say so. Consistent and documented.

### Finding 8: discovery severity matrix and the never-lie surface contract

**Category:** pass

With three open bugs in a sandbox project (`BUG-0001` low, `BUG-0002` low,
`BUG-0003` medium), `dashboard_defer_scope` -- which wraps the same
`_disc_deferred_bug_summary` discovery itself uses, so the bar cannot disagree
with the pipeline by construction -- returns:

| Posture | held back |
|---|---|
| `deferred_bugs=true`, level `low` | BUG-0001, BUG-0002 |
| `deferred_bugs=true`, level `medium` | BUG-0001, BUG-0002, BUG-0003 |
| `deferred_bugs=true`, level `high` | all three (nothing can trigger an RC) |
| `deferred_bugs=false` | none |

Status bar, `NO_COLOR`: `BUGS DEFERRED 2 (<=low)` rendered with the real U+2264
character, and with a campaign file live `BUGS DEFERRED 2 (<=low) until v2.20.0`.
Both match the requirements string exactly. With an unparseable flag the segment
becomes `DEFER CONFIG <path>` (with `GLYPH_WARNING` in colour mode) -- the count
is never rendered from a posture that could not be resolved.

Card glyphs, colour mode: the raised-hand glyph on BUG-0001 and BUG-0002 only,
none on BUG-0003; with an unparseable campaign file every open card carries the
warning glyph instead of a guessed deferred/not-deferred icon. Icon truth holds.

### Finding 9: pre-flight merge verification

**Category:** pass

All 13 RC-cycle tasks (PM-005, CODER-041..050, WRITER-051, CM-052) are DONE with
`Needs Human: no`. Every feature branch has been deleted post-merge (normal
CODER/WRITER lifecycle), so Checks 1 and 2 are skipped for each per the role
file's branch-lifecycle note. No unmerged branch, no BUG-92 zero-commit anomaly,
no empty commits in the RC (`git log --no-merges` + per-commit stat check).

### Finding 10: release-state and version drift (Step 6.1)

**Category:** pass

`## Active RC: v2.13.0`, opened `2026-09-08T18:47:13+00:00` by
`CM-20260908-052-open-rc-2-13-0`. `## Last Released: v2.12.1`, well-formed.
`v2.13.0 > v2.12.1` by semver. `ai_rc/v2.13.0` exists and the worktree HEAD
matches it. No drift.

### Finding 11: release-lifecycle simulation (Step 6.8)

**Category:** pass

The RC touches `team/scripts/cm/release.sh` and
`team/scripts/lib/cm/release_finalize.sh`, so the trigger condition applies.
`python3 -m pytest tests/integration/test_cm_release_synthetic.py -q` ->
15 passed in 30.95s.

### Finding 12: gate census and skip-cites-real-bug (Steps 6.9, 6.13)

**Category:** pass

`python3 team/scripts/lint_gate_census_compliance.py` -> `OK - 32 file(s)
checked, 0 offenders` (113 census-anchored sites exempted). Every new refusal in
this RC carries a `# CENSUS-ANCHOR:` marker, a class comment and a matching
Locator Index line -- `docs/blocking-site-census.md` was updated in the same
release.

Skip gate: no placeholder `BUG-SKIP-*` IDs in `team/tests/` (the only grep hits
are archived coverage run logs under `team/tests/artifacts/`, not live skips),
and every `BUG-NNNN` cited in a skip annotation resolves to a real file in
`bugs/`.

### Finding 13: coding-standards spot-check (Step 6.14)

**Category:** pass-with-caveat

The three new operator verbs all ship a `--help` that exits 0 and describes
their behavior. One caveat: `defer.sh --help` renders its own flag as a bare
`  --until` in the Options block, because
`operator_args_render_help_for_flags` only has built-in descriptions for the
eight canonical flags and falls back to printing the flag name alone for
anything else. The value and its meaning are described in the trailing prose
("The --until flag names the release the deferral lifts at (vX.Y.Z)"), so
directive 6 is satisfied in substance. Recorded as an observation about the
shared helper, not filed.

Provenance (directive 8) is a real miss and is covered separately -- see the
Lint Findings section and gaps.md Gap 5.

## Lint Findings (non-blocking)

Ingested from `${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-unit.json` (2
findings) and `findings-integration.json` (2 findings). Both files carry the
same two lint classes; grouped by class per the one-bug-per-lint-class rule.

- `comment_provenance` -- two task-ID provenance references in a new integration
  test (`team/tests/integration/test_operator_commands.py:346` ->
  `CODER-20260908-042`; `:1822` -> `CODER-20260908-043`) -- filed BUG-0207
  (Path C-A).
- `dual_channel_pair` -- `team/scripts/lib/config_loader.sh:153`,
  `pgai_emit_error_contract` call missing the `remedy` field -- filed BUG-0206
  (Path C-A). This class is not merely a lint signal here: the same finding is
  what turns the unit suite red, so it is also carried as Finding 1 above rather
  than laundered into this section.

Separately, and cited rather than re-filed: the "this ticket" / "a later ticket"
/ `v2.13.0-...` bundle-name pointers left in newly shipped production comments
(`defer_low_bugs.sh:32,37`, `discovery_defer.sh:8-9`, `why.sh:22-23,177`,
`discovery_state.sh:26,624`, `discovery_steps.sh:179,200`,
`config_loader.sh:97,169,172,174`) are exactly the class **BUG-0202 (open)**
describes -- a shipped library explaining a design choice by pointing at an
internal work ticket, which the provenance lint does not currently recognise.
Two commits in this RC (`c94b7cf`, `effffaa`) show CODER already scrubbing some
of these by hand, which is why the remainder is a lint-coverage problem rather
than a new defect class.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=1d freshness=stale — the claude-abliterated flavor failed its Dockerfile parse under both engines and podman/rocky9 failed at its COPY step; the record is recent (1d, inside the 7-day window) but speaks for v2.9.20, not the v2.13.0 under verification, so nothing is known about whether this tree's images build; cites BUG-0167, filed for that failure and since closed.

## Autonomous criterion

Manual interventions required: none.

1. Tasks force-promoted from WAITING to BACKLOG by hand? **NO.** All 13
   RC-cycle tasks reached DONE with `Needs Human: no`; the only promotion
   recorded in this task's own status.md is the wake script's own
   ("Promoted from WAITING to BACKLOG by wake script: prerequisites satisfied").
2. `release-state.md` edited outside the CM scripts? **NO.** Its contents are
   exactly what `cm-open-rc.sh` writes, and its mtime (18:47) matches the RC
   open timestamp recorded inside it.
3. Scripts patched mid-build? **NO.** `git log --since=2026-09-08T18:00 ai_main`
   shows only the four v2.12.1 release commits, all predating the RC open. Every
   commit between `ai_main` and the RC head is RC feature work on a feature
   branch merged with `--no-ff`, authored across 18:57-23:11.
4. Queue files manually edited? **NO.** Queue mtimes track wake-script activity
   through the cycle; no queue shows an edit inconsistent with the task
   transitions recorded in the task folders.

## Caveats

- The unit suite was run once. The failure is deterministic (it is a static lint
  over the tree, not a timing-sensitive test) and was reproduced directly by
  invoking `lint_dual_channel_pair.py` outside pytest, so it is not a flake.
- Integration coverage reads 8% line / 6% branch. That is normal for this
  project -- the integration suite drives bash entry points as subprocesses, so
  Python-side instrumentation sees almost nothing. Informational only; it is not
  a finding.
- `GLYPH_APPROVAL` and `GLYPH_DEFERRED_BUG` are the same raised-hand character,
  so the status bar can show the same glyph for two unrelated segments. The
  requirements asked for that glyph for deferred bugs specifically and each
  segment carries its own label, so this is recorded as an observation, not a
  finding.
- `status-right.sh` renders the install version as `v?` in a sandbox root where
  `status-bottom.sh` renders `v2.12.1`. This is outside the RC's diff and
  unrelated to deferral; noted only so a future reader does not attribute it to
  this release.
- Behavioral checks were run against throwaway roots created with
  `mktemp -d -p "$PGAI_AGENT_KANBAN_TEMP_DIR"`. The operator's live kanban root
  was read but never written to.
