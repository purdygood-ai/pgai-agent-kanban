# Verification Report: ai_rc/v2.13.0

**Task:** TESTER-20260909-021-verify-2-13-0
**Project:** pgai-agent-kanban
**RC branch:** `ai_rc/v2.13.0` @ `6f8d96f14faf0123e0cbb4a810fbbb6c6ebfa335`
**Base:** `ai_main`
**Last Released:** v2.12.2
**Requirements doc:** `requirements/v2.13.0-posture-config-deferral-and-piggyback.SUPERSEDED-on-cancel-2026-09-09T00:37:04+00:00.md`
**Worktree:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260909-021-verify-2-13-0` (detached, clean, no stashes)
**Verified:** 2026-09-09

## Summary

This is the re-run of the v2.13.0 cycle, re-opened by `CM-20260909-020-open-rc-2-13-0`
after the first attempt was cancelled on the regression recorded as BUG-0206.
Ten prerequisite tasks (CODER-010 through CODER-018 and WRITER-019) are DONE and
all merged; pre-flight merge verification passed with no unmerged branches and no
zero-commit anomalies.

The feature itself — the deferral posture, its four `[release]` config keys, the
precedence chain, the DEFER-LOW-BUGS campaign overlay, the discovery bug-step
deferral gate and piggyback carry, the ship-time campaign spend, and the dashboard
surfaces — was verified behaviourally end to end against sandboxed throwaway roots,
not merely read. Every acceptance criterion in the requirements doc is met, and every
one of the five bugs the cancelled cycle filed (BUG-0206 through BUG-0210) is
demonstrably closed on this tree: the unit suite is green again, refusals name their
accepted sets, the cfg examples describe live keys, and PM's K=2 ride-along cap is
present and matched byte-for-byte by the bundle writer's rendered suffix.

One defect blocks a clean PASS. The gated integration runner exits 1 on
`test_engine_behavior_baseline_harness.py::test_compare_against_goldens_is_clean_on_unmodified_tree`.
The release added four intended start-up narration lines to the wake log and did not
re-capture the golden the engine-behavior regression lock compares against. The same
test passes on `ai_main`, so the direction is established: this release turned that
lock red. The narration is correct; the golden is stale. Filed as BUG-0213.

Two quality-class lint classes also fired, both non-blocking and both filed
(BUG-0214, BUG-0215).

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

medium

## Test Suites

| Suite | Gated runner | Exit | Result |
|---|---|---|---|
| Unit | `bash team/scripts/run-unit-tests.sh --coverage` | **0** | 3524 passed, 3 skipped in 388.59s |
| Integration | `bash team/scripts/run-integration-tests.sh --coverage` | **1** | 419 passed, **1 failed**, 1 skipped in 313.24s |

The suite verdict above is the exit code of the gated runners, not a direct-pytest
line. Direct pytest was used only to isolate the single failure and to establish its
direction against `ai_main`.

All ten new test files this release shipped are green:
`test_defer_posture_resolver.py` (14), `test_discovery_defer_gate.py` (21),
`test_config_loader_release_posture_keys.py` (18), `test_defer_operator_verbs.py` (18),
`test_release_campaign_spend.py` (8), `test_defer_behaviour_matrix.py` (5),
`test_defer_campaign_lifecycle.py` (2), `test_discovery_bug_deferral_piggyback.py` (3),
`test_discovery_bug_deferral_gate.py` (9), `test_defer_icon_truth.py` (3).

## Code Coverage

- Unit:        blended 71% (14255 stmts, 4366 branches; 3664 stmts missed, 558 partial branches)
- Integration: blended 7%

Coverage is informational only and does not enter the PASS precondition. The figures
are the `^TOTAL` blended statement-plus-branch numbers from the `term` reports; the
JSON reports were written to `/tmp/pgai_kanban_tmp/coverage/{unit,integration}.json`.
The integration number is low by construction — the integration suite drives shell
entry points as subprocesses, which the Python coverage instrumentation does not see.

## Findings

### Finding 1: Integration suite red — engine-behavior baseline golden not refreshed for the new loader narration

**Category:** stale-assertion
**Fix Effort:** small (1 CODER task — re-capture one fixture file)
**Systemic Risk:** medium (the shipped gated integration runner exits 1, and the regression lock it guards is disabled until refreshed; nothing about chain integrity or data is at risk)
**Regression:** yes (`test_compare_against_goldens_is_clean_on_unmodified_tree` passes on `ai_main` and fails on this RC — verified by running the file against a `git archive ai_main` extraction: `2 passed in 8.44s`)
**Integrity Damage:** no
**Filed As:** BUG-0213 (Path C-A — filed as BUG: code defect / regression / test failure)

`team/scripts/lib/config_loader.sh` now emits, on the first `load_config` of a
process, one stderr line per absent `[release]` posture key:

```
config_loader: load_config: [release] deferred_bugs absent; applying documented default 'false'.
config_loader: load_config: [release] deferred_bug_level absent; applying documented default 'low'.
config_loader: load_config: [release] defer_piggyback absent; applying documented default 'true'.
config_loader: load_config: [release] deep_verify absent; applying documented default 'weekly'.
```

That is exactly what the requirements doc asks for ("absent key = default, narrated
once per run"), and I confirmed the once-per-run guarantee holds across two
`load_config` calls in one process — four lines, not eight. The defect is the
un-refreshed half of the change: `team/tests/fixtures/engine-baseline/wake-log.txt`
still records the pre-release wake log, so the byte-identical comparison fails.

**Stale-assertion evidence (per Step 7):**

- **Test:** `team/tests/integration/test_engine_behavior_baseline_harness.py:102`
  (`test_compare_against_goldens_is_clean_on_unmodified_tree`).
- **Failing assertion:** `assert result.returncode == 0` — expected 0, actual 1.
  The harness's own diff names the four added lines and nothing else
  (`@@ -1,3 +1,7 @@`).
- **Production code change:** `team/scripts/lib/config_loader.sh`,
  `_config_loader_narrate_default_once` plus the four `[release]` key blocks in
  `load_config`.
- **RC commit / task:** `0db0193` (merge `205b734`), task
  `CODER-20260909-010-defer-posture-config-keys`.
- **Correct fix:** re-capture `team/tests/fixtures/engine-baseline/wake-log.txt` via
  the harness's capture mode, reviewing the diff to confirm the four narration lines
  are the only change. The production narration must not be removed.

**Why medium and not high.** The rubric's `high` first condition is "something that
worked in the last shipped version does not work in this one." What stopped working
is the test lock, not the product: the wake log's change is the release's own intended
output, the operator-facing behaviour is correct, and `bash team/scripts/run-unit-tests.sh`
is green. Shipping does not make the running system worse. It does leave a red gate in
the shipped tree that an operator or CM running the suite will hit, and leaves the
engine-behavior lock unable to catch the next real drift, which is why this is a real
defect at `medium` rather than hygiene at `low`. `Regression: yes` is stated honestly
because this RC's changes are what produced it.

### Finding 2: Deliverable — `[release]` config keys, defaults, and loud refusals

**Category:** pass

All four keys registered in `team/scripts/lib/config_loader.sh` and validated.
Verified behaviourally against a throwaway `kanban.cfg` under
`$PGAI_AGENT_KANBAN_TEMP_DIR` (never the live root):

| Case | Result |
|---|---|
| all four absent | rc=0; defaults false / low / true / weekly, each narrated once |
| `deferred_bugs = on` | rc=1, `config_loader.release.invalid_deferred_bugs`, remedy names `true|false` and states `'on'/'off' are not accepted spellings` |
| `deferred_bugs = off` | rc=1, same refusal |
| `deferred_bug_level = urgent` | rc=1, remedy names `low|medium|high` |
| `defer_piggyback = yes` | rc=1, remedy names `true|false` |
| `deep_verify = sometimes` | rc=1, remedy names `weekly|off (or true|false)` |
| `deep_verify = off` / `false` | rc=0, `PGAI_DEEP_VERIFY=false` |
| `deep_verify = weekly` / `true` | rc=0, `PGAI_DEEP_VERIFY=true` |
| all four set explicitly | rc=0, values exported verbatim |

Every refusal carries the full dual-channel triple (stable `error` key,
plain-language `message`, runnable `remedy`) and every refusal site carries both a
`BLOCK-WORTHY` class comment and a `CENSUS-ANCHOR` marker with a matching
Locator Index entry. This is the acceptance criterion "Loader: unknown value for any
key refuses loud with the accepted set; on/off rejected for booleans" — met.

### Finding 3: Deliverable — precedence chain, campaign overlay, and its refusals

**Category:** pass

`resolve_defer_posture` in `team/scripts/lib/defer_posture.sh`, exercised against a
sandboxed root:

| Case | Result |
|---|---|
| nothing set | `false|default`, `low|default`, `true|default`, `until=|none` |
| kanban.cfg only | values tiered `kanban.cfg` |
| project.cfg over kanban.cfg | per-key: `deferred_bugs` stayed `kanban.cfg`, `deferred_bug_level`/`defer_piggyback` moved to `project.cfg` |
| campaign file present | `deferred_bugs=true|campaign file`, `deferred_bug_level=low|campaign file`, `until=v9.9.9|campaign file`, `campaign_file=<path>`; `defer_piggyback` correctly left on `project.cfg` |
| campaign `until=9.9` | rc=1, `defer_posture.campaign_malformed_until`, `DEFER_POSTURE_LAST_ERROR_FILE` names the file |
| campaign with no `until=` line | rc=1, `defer_posture.campaign_unparseable`, error file named |
| `deferred_bugs = on` in project.cfg | rc=1, refusal names the accepted set and the offending file |
| stray `defer_piggyback = any` outside `[release]` | rc=0, not read, no refusal |

Precedence is exactly `campaign file > project.cfg > kanban.cfg > default`, one
documented line, and it holds per-key rather than per-file. The "never silently off"
rule holds: a malformed campaign file refuses rather than falling through to a tier
that might say deferral is off. `DEFER_POSTURE_LAST_ERROR_FILE` survives the call
(the implementation deliberately avoids `$(...)` around internal helpers so a subshell
cannot swallow the global) — confirmed, the variable read correctly after each refusal.

### Finding 4: Deliverable — operator verbs `defer.sh`, `undefer.sh`, `why.sh`

**Category:** pass

All three ship `--help` naming every argument (coding-standards directive 6).
Exercised against a sandboxed root:

- `defer.sh --project demo --until v9.9.9` writes `DEFER-LOW-BUGS` carrying a
  commented header and `until=v9.9.9`; narrates project, target, and path.
- `defer.sh --project demo` refuses `defer.until_required`; remedy shows the full command.
- `defer.sh --project demo --until 9.9` refuses `defer.until_malformed`, "not well-formed
  semver (expected vX.Y.Z)" — validated at write, as the doc requires.
- `defer.sh --project nope --until v9.9.9` refuses `defer.project_not_found` naming the path.
- `why.sh --project demo` with a campaign live prints each field with its source
  tier, and names the exact campaign file path.
- `undefer.sh --project demo` removes the file and says so; a second run is
  idempotent and says there was nothing to clear (exit 0 both times).
- `why.sh` on an unparseable campaign file refuses through the resolver rather than
  printing a guessed posture.

This covers the acceptance criteria "undefer verb" and "why.sh names the resolved
posture and its source tier".

### Finding 5: Deliverable — discovery deferral gate, piggyback carry, hygiene sweep

**Category:** pass

`team/scripts/lib/discovery_defer.sh` implements the founding rule correctly by
construction and by test:

- A deferred bug is never echoed to `discovery_step_bugs` as a bundle candidate, so
  it cannot open an RC. A deferred bug can never become the trigger — the trigger is
  always drawn from the kept set.
- A severity that is not one of `low|medium|high` (empty, a typo, or `critical`)
  resolves to `unparseable` and is **kept**, never silently swallowed into deferred,
  and the run names the file.
- Piggyback narration is the exact required shape:
  `piggybacking N deferred bugs (trigger: <trigger-id> <severity>)`.
- The ride-along wire format (`path\tRIDE_ALONG:<trigger-id>`) renders in the bundle
  as ` — hygiene ride-along (piggyback on <TRIGGER-ID>)`, byte-identical to the fixed
  wording `team/roles/PM.md` (line 127) specifies and keys off.
- The `<=K` consolidation cap is `team/roles/PM.md` line 129 — "At most K tickets, and
  K is 2 … a ceiling on tickets, never on bugs", grouped by area. Present and
  unambiguous; this is what the cancelled cycle filed as BUG-0208 and it is closed.
- The hygiene sweep fires only when every unhandled bug is deferred **and**
  `_disc_requirements_queue_empty` holds; ceiling-blocked and auto-invalid
  requirements are correctly not counted as pending work.

The acceptance matrix is covered by named integration tests, all passing:
`test_bundle_carries_the_trigger_and_every_deferred_low_with_ride_along_annotation`,
`test_bundle_carries_only_the_trigger_while_low_bugs_stay_open_and_uncached`,
`test_medium_bug_defers_and_opens_no_bundle`,
`test_high_severity_bug_defers_and_opens_no_bundle`,
`test_low_severity_bug_also_defers_at_level_high`,
`test_sweep_bundles_deferred_bugs_once_bug_and_requirements_queues_are_both_empty`,
`test_sweep_is_withheld_while_an_unprocessed_requirement_is_present`.
The "requirements decompose normally in every state" criterion is explicitly
asserted per matrix cell by `_assert_requirements_file_still_decomposes`.

### Finding 6: Deliverable — ship-time campaign spend

**Category:** pass

`cm_release_spend_campaign_file` is wired into `release_finalize.sh` Step 12 and
exercised directly against a sandboxed project root:

| Case | Result |
|---|---|
| no campaign file | narrates "nothing to spend", rc=0 |
| shipped v2.14.0 < until v2.20.0 | file left in place, narrates the target still in force |
| shipped v2.20.0 == until v2.20.0 | file removed, `## Campaign Spend Event` appended to release-state.md |
| shipped v2.21.5 > until v2.20.0 | file removed (latching `>=`, not equality) |
| malformed `until=2.20` | WARNs, leaves the file, returns 0 — never fails an already-tagged release |
| root-scoped `$KANBAN_ROOT/DEFER-LOW-BUGS` | untouched; only the project-scoped file is spent |

Semver ordering is the shared helper, not a string compare: `semver_gte v2.9.7 v2.17.1`
correctly returns false. The audit block mirrors the HALT-AFTER promotion shape.
This is the "until-file spend … precedence (file over cfg, resumes after spend)"
criterion — met; after removal the resolver falls straight through to the cfg tiers
with no further action, which I confirmed by re-resolving after a spend.

### Finding 7: Deliverable — dashboard surfaces and icon truth

**Category:** pass

`dashboard_defer_status_segment` and `dashboard_defer_card_ids` in
`team/scripts/dashboard/lib/defer_scope.sh`, exercised against a sandboxed two-bug
fixture (one `low`, one `high`):

| Case | Segment |
|---|---|
| deferral off | `off` (segment omitted by the renderer) |
| on, level=low | `on / count=1 / low` — only the low bug counted |
| on, level=high | `on / count=2 / high` — both counted |
| bug flipped to `## Status: done` | count drops to 1 — non-open bugs correctly excluded |
| campaign live | `on / count / low / until=v3.0.0` |
| campaign malformed | `malformed / … / <path to the offending file>` |

`dashboard_defer_card_ids` returns the compact `BUG-NNNN` form the card renderers
already display, and on `malformed` returns no ID list at all — the caller then
renders the warning glyph on every open card rather than guessing which the
unreadable posture would have held. The status bar text is
`BUGS DEFERRED n (<=level)[ until vX.Y.Z]`, with `GLYPH_DEFERRED_BUG="✋"` and
`GLYPH_DEFER_WARNING="⚠️"` centralised in `team/scripts/lib/status_glyphs.sh`.
All three dashboard callers (`status-right.sh`, `status-bottom.sh`,
`column-render.sh`) source `project_paths.sh`, which the scope library requires for
`pp_bugs_dir`; I confirmed that dependency is satisfied in every caller.

The "never-lie law" criterion is met: the count is derived by calling the same
`_disc_bug_severity` / `_disc_severity_defer_decision` primitives discovery itself
applies, not by a second copy of the rule, and the malformed path renders a warning
and a path instead of a posture. All three `test_defer_icon_truth.py` tests pass.

### Finding 8: Deliverable — documentation

**Category:** pass

`team/SOP-core.md` gains a "Deferral Posture" section with the four-key table, the
precedence line, the `level=high` consequence stated as the operator-knows-best case,
the campaign-file semantics, and the hygiene-sweep condition. `docs/operator-commands.md`
documents `defer`, `undefer`, and `why`; `docs/OPERATIONS.md` carries the campaign
walkthrough and the status-bar rendering. `kanban.cfg_example` and `project.cfg_example`
both describe all four keys with accepted values and defaults — closing BUG-0209,
which had reported the examples claiming the keys did nothing. `docs/blocking-site-census.md`
gains fourteen new Locator Index entries, all in the anchor-token form, all resolving.

I read the changed governance and documentation files fully rather than grepping them.
See Caveats for two out-of-scope sentence removals in `team/SOP-core.md` that I checked
and concluded were not content loss.

### Finding 9: Prior cycle's filed bugs are demonstrably closed

**Category:** pass

The cancelled first attempt filed BUG-0206 through BUG-0210. On this tree:
BUG-0206 (unit suite red on the emitter guard line) — unit runner exits 0;
BUG-0207 (new integration tests cite task IDs) — the provenance lint now reports one
finding and it is not in the test tree; BUG-0208 (piggyback carry has no task cap) —
`team/roles/PM.md` carries the K=2 ceiling and the bundle writer emits the matching
annotation; BUG-0209 (config examples say keys do nothing) — both examples now
document live, validated keys; BUG-0210 (resolver refusal omits accepted set) — every
resolver refusal I triggered named its accepted set. All five are `## Status: done`.

### Finding 10: Release-state and dependency-graph sanity

**Category:** pass

`## Active RC` is `v2.13.0`, opened `2026-09-09T03:14:15+00:00` by
`CM-20260909-020-open-rc-2-13-0`; the `ai_rc/v2.13.0` branch exists and the worktree
HEAD matches it exactly. `## Last Released` reads `v2.12.2`, well-formed, and
`v2.13.0 > v2.12.2` by semver. No state drift between the file and the branch.
All ten prerequisite task IDs named in the task README exist and are DONE; the
lifecycle bookends are correctly linked (CM-020 open-RC at the start, this TESTER task
at the end); sequence numbers 010–021 are consistent and non-colliding, with no cycles.

## Lint Findings (non-blocking)

Both gated runners wrote findings files. Grouped by lint class per the one-bug-per-class
rule; neither class had an open or running bug already covering it (BUG-0211 and
BUG-0212 are the only open bugs and describe different defects).

- `comment_provenance` — one instance, `team/scripts/dashboard/lib/defer_scope.sh:142`:
  a header comment uses the real internal number `BUG-0107` as its worked example of a
  slugged bug filename. New code from this release; a placeholder reads identically.
  Rolled-up severity: quality. — **filed BUG-0214** (Path C-A).
- `gate_census_compliance` — three instances, all in `team/scripts/cm/release.sh`:
  `team/scripts/cm/release.sh:171`, `team/scripts/cm/release.sh:189`, and
  `team/scripts/cm/release.sh:264`, each reported as missing a census classification
  and/or the dual-channel emitter. The refusals themselves are unchanged and two of
  them already carry both signals; what moved is their line numbers. This release
  inserted lines above them (a header rewrap and the `release_campaign_spend.sh`
  source line) while `docs/blocking-site-census.md`'s Locator Index still records them
  at 170, 188, and 261, so the anchors no longer resolve. The same lint reports
  `0 offenders` on `ai_main`. Rolled-up severity: quality. — **filed BUG-0215** (Path C-A).

Per Step 5c, neither class on its own moves `## Recommendation` or `## Systemic Risk`.
Both are recorded here and tracked; the recommendation is driven by Finding 1 alone.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=2d freshness=stale — the docker and podman claude-abliterated pairs failed on a Dockerfile FROM parse error and podman/rocky9 failed at its entrypoint COPY step; stale on the version count only (2d is inside the 7-day window, but v2.9.20 is not the v2.13.0 under verification), so nothing has been built cold against this tree; cites BUG-0167, filed for that failure and since closed.

The lane was not run during this verification and is not part of the RC path.
This project carries `deep_verify = off` in its `project.cfg [release]` section, so the
weekly scheduled trigger does not fire for it; the record above is from an on-demand or
pre-`off` run. Per Step 5d this line is reported, not re-derived, and does not on its own
move the recommendation or the systemic risk.

## Autonomous criterion

| # | Check | Answer |
|---|---|---|
| 1 | Any task force-promoted from WAITING to BACKLOG manually? | **NO** |
| 2 | `release-state.md` edited outside `cm-open-rc.sh` / `cm-release.sh` / `cm-cancel-rc.sh`? | **NO** |
| 3 | Any scripts patched mid-build? | **NO** |
| 4 | Any queue file manually edited? | **NO** |

**Autonomous operation criterion: Manual interventions required: none.**

Evidence. Every WAITING to BACKLOG transition in this cycle is attributed in
`logs/cron-coder.log` to the wake script's own prerequisite re-check (for example
`[2026-09-09T05:14:58+00:00] wake(coder): WAITING task CODER-20260909-015-discovery-defer-piggyback: prerequisites satisfied, promoting to BACKLOG`),
with no unattributed state flips. `release-state.md`'s mtime is `2026-09-09 03:14:15`,
identical to the `## RC Opened At` value `cm-open-rc.sh` wrote, so it has not been
touched since. Every commit between `ai_main` and the RC tip is either a CODER/WRITER
feature commit or a `--no-ff` merge of one of those feature branches; there is no
out-of-band commit to `team/scripts/` or `subagents/`. Queue-file mtimes track wake
firings (`coder_backlog.md` and `tester_backlog.md` both `07:35`, matching the last
coder wake in the log).

One process note that is **not** an intervention: the first v2.13.0 attempt was
cancelled and re-bundled after CM HALTed on BUG-0206. That cancel-and-rebundle is the
designed autonomous retry path, executed by CM, and is recorded in
`release-state/history/v2.13.0-cancelled-2026-09-09-03-14-15Z.json`. It is worth CM
knowing that `project.cfg` sets `max_rc_retries = 1` for this project and this cycle
has consumed that one retry.

## Caveats

- **Live-install state predating this RC.** Two artefacts sit in the operator's live
  kanban root, both mtime `2026-09-07 16:39`, two days before this cycle opened:
  `projects/pgai-agent-kanban/project.cfg` carries a stray `defer_piggyback = any`
  appended after the `[training]` section, and there is a stray regular file named `1`
  (5119 bytes) at the kanban root, of the shape a `>1` typo for `>&1` leaves behind.
  Neither is introduced by this RC and neither is currently harmful — I confirmed the
  resolver does not read the stray key, because `read_ini` scopes to `[release]` and the
  key sits under `[training]`. It is a latent trap rather than a live one: if that line
  ever ends up inside a `[release]` section, `load_config` will refuse and the affected
  wake will stop. Recorded as an observation for the operator, not filed, because it is
  kanban state rather than a defect in the release.
- **Live campaign file is armed.** `projects/pgai-agent-kanban/DEFER-LOW-BUGS` exists
  (mtime `2026-09-05`) and reads `until = v2.22.0`. It parses cleanly. The practical
  consequence of shipping v2.13.0 is that deferral goes live on this project
  immediately: `resolve_defer_posture` against the live root returns
  `deferred_bugs=true|campaign file`, `deferred_bug_level=low|campaign file`,
  `until=v2.22.0`. Low-severity bugs will stop opening RCs of their own and will ride
  along on the next above-level trigger, or wait for a hygiene sweep, until a release
  at or past v2.22.0 ships. This appears to be a deliberate operator arming rather than
  drift — it predates the feature landing — but it is a behaviour change on the very
  first wake after this release and is worth stating plainly. The three bugs this
  report files are severity high / low / medium, so BUG-0213 and BUG-0215 remain above
  a `low` deferral level and can still trigger their own RC.
- **Two governance sentences removed from `team/SOP-core.md` outside the stated scope.**
  The WRITER change deleted "Understand the full workflow; act only within your own lane
  unless instructed otherwise." and "**WAITING** is soft and automatic; **BLOCKED** is
  hard and manual, and a human must investigate it." I checked whether this is content
  loss and concluded it is not: the surviving state bullets already carry both meanings
  ("`WAITING` … auto-promoted back to BACKLOG when satisfied" and "`BLOCKED` … never
  auto-resolved, and returned to BACKLOG only by HUMAN"), and the Role Swim Lanes section
  immediately below covers the lane rule. Noted rather than filed. Worth knowing that
  `lint_governance_byte_budgets.sh` reports every role `unbudgeted`, so there was no
  budget pressure forcing the trim.
- **`team/scripts/lib/discovery_defer.sh` ships mode 644** while its sibling stages
  (`discovery_steps.sh`, `discovery_state.sh`, `defer_posture.sh`,
  `release_campaign_spend.sh`) ship 755. The file is sourced, never executed, so there is
  no functional impact — but this same inconsistency drew a dedicated fix commit for
  `release_campaign_spend.sh` in this very RC (`97eecdf`), so the convention is real and
  this one file missed it. Observation, not filed.
- **`why.sh` labels the no-campaign case `(default)` where the resolver reports tier
  `none`.** With no campaign file, `why.sh` prints `until=(none) (default)` while
  `resolve_defer_posture` returns `until=|none`. The two mean the same thing to a reader
  and no acceptance criterion is violated, but the verb is not echoing the resolver's own
  tier vocabulary verbatim. Observation.
- **The status bar renders `(<=level)` where the requirements doc writes `(<=level)` with
  the typographic less-than-or-equal sign.** The ASCII spelling is used consistently
  across the implementation, both status-bar scripts, and `docs/OPERATIONS.md`, so
  nothing disagrees with anything else. Read as a typographic rendering of the same
  thing, not a deviation.
- **Requirements doc filename.** The input doc carries a
  `.SUPERSEDED-on-cancel-2026-09-09T00:37:04+00:00.md` suffix from the first cycle's
  cancellation, and its `## Status` still reads `running`. It is present and fully
  parseable, so verification proceeded against it normally; no v2.13.0 requirements doc
  exists under any other name. Flagged so CM is not surprised by the filename when
  writing release notes.
- **Live-install safety.** Every behavioural check in this report ran against a throwaway
  root created with `mktemp -d -p "$PGAI_AGENT_KANBAN_TEMP_DIR"`. The single read against
  the live root (`resolve_defer_posture` for the campaign-file caveat above) is read-only
  by contract — the resolver never writes. No state-mutating script was pointed at the
  operator's install.
- **Pre-flight.** Worktree clean, no stashes, HEAD `6f8d96f` matches `ai_rc/v2.13.0`
  exactly. All ten DONE prerequisite tasks have had their feature branches deleted after
  merge, which is the normal post-merge shape; no unmerged branch and no BUG-92
  zero-commit anomaly.
- **Skip-cites-real-bug gate (Step 6.13).** No placeholder skip IDs in `team/tests/`
  (the only such grep hits are inside archived runner logs under
  `team/tests/artifacts/`, not in test source), and no skip annotation cites a
  `BUG-NNNN` that lacks a file in `bugs/`.
- **Synthetic release simulation (Step 6.8).** The RC touches release-lifecycle files
  (`team/scripts/cm/release.sh`, `team/scripts/lib/cm/release_finalize.sh`, and the new
  `release_campaign_spend.sh`), so the trigger condition is met.
  `team/tests/integration/test_cm_release_synthetic.py` is present and all 15 of its
  tests passed in the integration run (line 110 of the captured output).
