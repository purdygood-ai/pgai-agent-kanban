# Verification Report: ai_rc/v2.20.2

**Task:** TESTER-20260913-013-verify-2-20-2
**RC branch / commit:** `ai_rc/v2.20.2` @ `7b00afefc0831eb07e19d8cd83565e3b8d09eb87`
**Baseline compared against:** `ai_main`
**Requirements doc:** `projects/pgai-agent-kanban/requirements/v2.20.2-bugfix-bundle-20260913.md`
**Worktree:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260913-013-verify-2-20-2` (detached HEAD, clean, no stashes)
**Verified:** 2026-09-13

## Summary

v2.20.2 is a fourteen-item bug bundle. Every bundled item was verified individually
against its own `## Acceptance` section, not merely against the diff: the two
behavioural fixes (BUG-0256 refusal-ledger root fabrication, BUG-0258 show-queues on a
registry-less root) were exercised by triggering their original failure modes and
confirming the guards now fire, and their new-normal cases were re-checked so the fix
did not break the working path. The remaining twelve — a step-number narration
inconsistency, a manifest-lint path-naming defect, a canned-reason remedy, a stale
REST-parity exemption, a mislabelled dashboard column, a hygiene-lint blind spot over
`team/config/`, a five-role governance byte-budget overrun, and five provenance-litter
sites across code, tests and shipped release notes — were each checked by running the
lint or grep the bug's own acceptance criterion names. Both gated runners exited 0
(unit 4344 passed / 31 skipped; integration 455 passed / 1 skipped). Thirteen of the
fourteen items are fully closed. One acceptance criterion of BUG-0246 (the governance
trim) is not met: the trim deleted the census lint's finding-name documentation from
`team/roles/CODER.md` and delegated it to a document that carries only half of it, so
two of the four finding names the lint emits are now documented nowhere in the tree.
That, plus two residual quality-class hygiene findings whose only trackers close with
this RC, are the report's findings; both are filed via Path C and neither prevents the
release from being usable.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

low

## Suite Results (gated runners — the authoritative PASS signal)

| Runner | Command | Exit | Result |
|---|---|---|---|
| Unit | `bash team/scripts/run-unit-tests.sh --coverage` | **0** | 4344 passed, 31 skipped in 621.70s |
| Integration | `bash team/scripts/run-integration-tests.sh --coverage` | **0** | 455 passed, 1 skipped in 408.40s |

Both runners exited 0, so the suite-pass precondition for a PASS recommendation is
satisfied on the test side; the recommendation is elevated by the Step-7 findings below,
not by a suite failure. Captured output:
`/tmp/pgai_kanban_tmp/tester/TESTER-20260913-013/unit_output.txt`,
`/tmp/pgai_kanban_tmp/tester/TESTER-20260913-013/integration_output.txt`.

## Code Coverage

*(informational only — never a finding, never a PASS precondition)*

- Unit: line 75% | branch 62% (15725 stmts, 4714 branches; blended `TOTAL` 72%)
- Integration: line 7% | branch 6% (15716 stmts, 4714 branches; blended `TOTAL` 7%)

Read from the JSON `totals` objects at `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/unit.json`
and `.../integration.json`, using the field names confirmed against the generated files
(`percent_statements_covered`, `percent_branches_covered`) rather than the blended
`percent_covered` the `TOTAL` row shows.

## Pre-flight

| Check | Result |
|---|---|
| Worktree HEAD == `ai_rc/v2.20.2` | PASS (`7b00afe` both) |
| `git status --short` clean | PASS (empty) |
| `git stash list` empty | PASS (empty) |
| Merge verification (Check 1 / Check 2) | PASS — 0 live feature branches; all ten DONE task branches deleted post-merge (normal cleanup, both checks skipped per role file) |
| All ten prerequisite tasks DONE | PASS |
| Release-state drift (Step 6.1) | PASS — `pp_last_released_version` = `v2.20.1`, `semver_gt v2.20.2 v2.20.1` true; `## Active RC` = `v2.20.2` and `ai_rc/v2.20.2` exists; `## Last Released` = `v2.20.1` (well-formed) |
| Prereq dependency graph (Step 6.10) | PASS — CM-open(012) -> CODER 002-009 + WRITER 010-011 -> TESTER 013 -> CM-release(015); no cycles, every referenced ID exists |
| Requirements-doc criteria 2 and 3 | PASS — all 12 changed `.py` files compile under `py_compile`; all 9 changed `.sh` files pass `bash -n` |
| Secrets scan over the diff | PASS — no added key/password/secret/token literals |

## Findings

*(Path C — autonomous follow-up filing, non-blocking. Path C-A — filed as BUG: code
defect / regression / test failure. Path C-B — filed as PRIORITY: enhancement /
design change.)*

### Bundled item verification

| Bug | Acceptance verified how | Result |
|---|---|---|
| BUG-0230 | `grep -rnE 'BUG-[0-9]{4}'` over the four named files; hygiene lint | **pass-with-caveat** |
| BUG-0234 | `grep -n '\[Step 8\]\|\[Step 11a\]' team/scripts/lib/cm/release_notes.sh`; cross-ref grep | **pass** |
| BUG-0236 | `grep -n 'CODER-20260910-066' team/tests/unit/test_lint_gate_census_compliance.py` | **pass** |
| BUG-0242 | provenance grep over `release-notes/v2.16.0.md`; hygiene lint | **pass** |
| BUG-0245 | `grep -n 'CODER-2026' team/tests/unit/test_posture_bar_truth.py` | **pass** |
| BUG-0246 | `lint_governance_byte_budgets.sh --kanban-cfg kanban.cfg_example` | **gap** (criterion 3) |
| BUG-0247 | rendered `agent-metrics.sh` output; `docs/DASHBOARD-PANES.md`; `docs/operator-api.md` | **pass** |
| BUG-0248 | provenance grep over `release-notes/v2.19.0.md` and over the newest notes file | **pass** |
| BUG-0253 | ran the lint with and without `--manifest` against a drifted copy | **pass-with-caveat** |
| BUG-0254 | planted a task ID in a manifest copy and scanned the config surface | **pass** |
| BUG-0255 | read both rewritten remedies; `lint_runnable_remedy.py` (0 offenders) | **pass** |
| BUG-0256 | ran `halt-global.sh` / `unhalt-global.sh` twice each on an absent root | **pass** |
| BUG-0257 | read `_REST_PARITY_SCENARIOS` / `_DUAL_CHANNEL_SCENARIOS` and the docstring | **pass** |
| BUG-0258 | ran `show-queues.sh` on a registry-less root, with and without `--project` | **pass** |

### Finding 1: the governance trim deleted census-lint finding documentation and delegated it to a document that does not carry it

**Category:** gap
**Fix Effort:** small (1 WRITER task)
**Systemic Risk:** low (documentation-only; a CODER authoring a new gate can still read the lint's own output, which names the finding kind)
**Regression:** yes (this RC's `WRITER-20260913-011` trim removed the text; the enumeration was present on `ai_main`)
**Integrity Damage:** no
**Filed As:** BUG-0259 (Path C-A)

BUG-0246's third acceptance criterion is *"No governance rule is lost in the trim:
every directive removed from one file is either still stated in exactly one canonical
place or was a duplicate of a statement that remains."* One removal fails it.

On `ai_main`, `team/roles/CODER.md` enumerated the census lint's findings:

```
`team/scripts/lint_gate_census_compliance.py` enforces this, and produces exactly three findings:

- `missing_signal` — a candidate refusal site ... whose window carries no census classification comment, no dual-channel emitter call, or neither.
- `unindexed_anchor` — a `# CENSUS-ANCHOR: <token>` marker whose token no `path:token CLASS` line in the Locator Index names.
- `anchor_class_mismatch` — a marker whose Locator Index entry names a different class than the site's own inline comment declares.
```

The RC replaces that with a pointer:

```
`team/scripts/lint_gate_census_compliance.py` enforces this. Everything it reports is quality-class — the lint always exits 0 whatever it finds — and each finding's name, meaning, and fix are in `docs/blocking-site-census.md`, the file you are already opening to add the index entry.
```

The pointer's claim is false for half the vocabulary. The lint declares four finding
kinds (`team/scripts/lint_gate_census_compliance.py:271-274`): `missing_signal`,
`unindexed_anchor`, `anchor_class_mismatch`, `unresolved_locator`.
`docs/blocking-site-census.md` names only `unindexed_anchor` and `unresolved_locator`
(line 451). Verified across the whole tree:

```
$ grep -rln 'missing_signal' --include=*.md .          # (no output)
$ grep -rln 'anchor_class_mismatch' --include=*.md .   # (no output)
$ grep -rln 'unresolved_locator' --include=*.md .
docs/blocking-site-census.md
```

So `missing_signal` — the most common of the four, the one a new gate trips when it
omits its class comment or its emitter — and `anchor_class_mismatch` are documented in
no markdown file in the tree. The pre-trim text was also already stale ("exactly three
findings" for a four-kind lint), so the correct repair is to document all four in
`docs/blocking-site-census.md` rather than to revert the trim.

No governance *rule* was lost — requirements 1-3 of the gate-authoring contract are
intact in `CODER.md`, and the two moved passages spot-checked did land in one canonical
place each (`CODER.md`'s intro now states the origin-is-CM's rule that the deleted "Git
operates locally" section carried; `roles/CM.md:83` carries verbatim the
`Regression`/`Integrity`/`Fix Effort: large` clearing rule that `SOP-core.md` dropped).
What was lost is reference content the trim promised was somewhere else.

### Finding 2: two quality-class hygiene provenance findings survive, and both bugs that name them close with this RC

**Category:** bug
**Fix Effort:** small (1 CODER task — two comment rewords)
**Systemic Risk:** low (comment-only; no shipped code path changes behaviour, both gated runners exit 0)
**Regression:** no (the two sites predate this RC — `team/tests/unit/test_operator_verb_manifest.py` carried them at `ai_main`, and this RC's diff does not touch those lines)
**Integrity Damage:** no
**Filed As:** BUG-0260 (Path C-A)

Both gated runners' findings files carry the same two instances, and no more:

```
team/tests/unit/test_operator_verb_manifest.py:159  [task-ID] 'CODER-20260911-066'
team/tests/unit/test_operator_verb_manifest.py:176  [task-ID] 'CODER-20260911-066'
```

These are listed here rather than in "Lint Findings (non-blocking)" alone because of
what happens when this RC ships. BUG-0254's symptom section names exactly these two
instances and attributes them to *"the open BUG-0245"*; BUG-0245's own acceptance
criteria name only `team/tests/unit/test_posture_bar_truth.py:20`, which this RC did
fix. Both BUG-0245 and BUG-0254 are in this bundle, so `cm-release.sh` Step 16b sets
both to `done` — leaving two live findings with no open tracker. Per the
cite-an-open-bug-or-file-one rule (TESTER.md Step 5c), and its explicit "never skip a
filing on the grounds that some earlier run already filed something," a new bug is
filed for the `hygiene` lint class, listing both instances.

Recommendation impact: on its own a bug filed *solely* to track a quality-class lint
finding does not elevate the recommendation. This one is recorded as a Step-7 finding
because the defect is the tracking gap (two instances about to become untracked), not
merely the lint signal. Finding 1 elevates the recommendation independently.

### Finding 3 (behavioural): BUG-0256 — refusal-ledger no longer fabricates a missing kanban root

**Category:** pass

The original failure mode was triggered directly, not inferred. With
`PGAI_PROJECT_NAME` set — the environment in which the defect bit — `halt-global.sh`
was run twice in a row against a non-existent `KANBAN_ROOT`:

```
run 1: error: halt_global.kanban_root_missing   exit=1
       find <root>  ->  No such file or directory      (previously: root + projects/<name>/refusal-ledger.md)
run 2: error: halt_global.kanban_root_missing   exit=1   (previously: "global HALT set: <root>/HALT", exit 0)
       find <root>  ->  No such file or directory
```

`unhalt-global.sh` behaves identically (`unhalt_global.kanban_root_missing`, exit 1
twice, no tree created). Every acceptance criterion of BUG-0256 is met, and the
dual-channel `error`/`message`/`remedy` fields and exit codes are unchanged.

The new-normal case was verified too, because a fix that suppressed the side effect
everywhere would break ledger recording: with `KANBAN_ROOT` pointing at a directory
that *does* exist, `refusal_ledger_record` still returns 0 and still creates
`projects/<name>/refusal-ledger.md` beneath it. Only the root itself is guarded, which
is what both the bash guard (`team/scripts/lib/refusal_ledger.sh`) and the Python
writer (`team/pgai_agent_kanban/lib/refusal_ledger.py`, `os.path.isdir` early return)
document. The regression test the bug asked for exists and covers the
two-invocations-in-a-row case with and without `PGAI_PROJECT_NAME`
(`team/tests/unit/test_verb_fixture_matrix.py`).

### Finding 4 (behavioural): BUG-0258 — show-queues refuses on a registry-less root

**Category:** pass

Exercised on a scratch kanban root deliberately created with no `projects.cfg`:

```
--project bogus-name   -> error: show_queues.project_directory_missing   exit=1   (no board)
no --project           -> full board renders, exit 0
no --project --details -> renders, exit 0
```

and, for the contrast the bug drew, the same invocation against a root whose
`projects.cfg` exists but is empty still refuses at the resolver
(`project_resolve.not_found`, exit 1) — the pre-existing path is unchanged. A valid
`--project` against the live kanban root still renders the real board. All three
acceptance criteria are met; the third (a test covering the registry-less root) is
`team/tests/unit/test_show_queues_human_queue.py` (+54 lines in this RC).

The new refusal site is a correctly authored gate: it declares `BLOCK-WORTHY` in an
inline comment, emits through `pgai_emit_error_contract` with all three fields, carries
`# CENSUS-ANCHOR: show-queues-project-directory-missing-01`, and adds the matching
Locator Index line
`team/scripts/dashboard/show-queues.sh:show-queues-project-directory-missing-01 BLOCK-WORTHY`
in the same change — both halves authored together, as the new-gate rule requires.

### Evidence for the clean rows

- **BUG-0234.** `release_notes.sh` now narrates `[Step 11a]` at lines 124, 141 and 553
  — one number, matching the three `Step 11a` cross-references in
  `release_ship_policy.sh` (505, 590, 602). The only surviving `[Step 8]` in
  `team/scripts/` is `ship-rc.sh:709` ("Deleting $RC_BRANCH on origin"), a different
  verb with its own step sequence, so nothing in the release path contradicts anything
  else in it.
- **BUG-0247.** The pane now renders `Avg Wall (min)` and prints the footnote
  *"Avg Wall (min) is the RC's own elapsed time, averaged over each agent's invoked RCs
  -- not that agent's own working time."* directly under the table.
  `docs/DASHBOARD-PANES.md` window 8 and `docs/operator-api.md`'s `GET /agent-metrics`
  row carry the same clarification; the REST field name `avg_wall_minutes` is unchanged
  and the numbers are unchanged.
- **BUG-0254.** The hygiene lint gained a config surface (`--config-dirs`, default
  `team/config/`, scanning `.manifest`/`.census`). Planting
  `# planted probe: added by CODER-20260913-999 ...` into a copy of
  `operator_verbs.manifest` produces a `provenance` finding naming the file and line;
  the unmodified copy produces none.
  `grep -rnE 'ticket [0-9]+ \(|CODER-2026|WRITER-2026|TESTER-2026|PM-2026' team/config/`
  returns nothing. The gated runners' hygiene comment and the hands-free demo fixture
  were both updated for the new surface (the fixture pins `--config-dirs` to an empty
  scratch dir so it cannot sweep the real tree in).
- **BUG-0255.** Both operator-force remedies now name what the operator must supply
  instead of supplying it — `--reason "write your reason here"` with the sentence
  *"substituting your own one-line justification for the placeholder text (it is
  recorded verbatim in release-state.md)"*. The canned string
  `operator override: shipping despite the ship-policy matrix stop` appears nowhere in
  `team/scripts/`, `python3 team/scripts/lint_runnable_remedy.py` reports 0 offenders
  over 240 files, and the message/remedy contradiction is gone. The bug's milder second
  half was also fixed: `defer.sh` and `halt-after.sh` no longer hardcode
  `pgai-agent-kanban` or a version literal in their examples.
- **BUG-0257.** `show-queues` and `verify-rc-state` now use
  `_NO_REST_PARITY_OUT_OF_SCOPE` in `_REST_PARITY_SCENARIOS`. The four verbs still on
  `_NO_REST_PARITY_NO_KEY` (`switch-provider`, `create-project`, `add-project`,
  `remove-project`) are all `NotApplicable` in `_DUAL_CHANNEL_SCENARIOS`, so no reason
  string claims a verb lacks a CLI-reachable dual-channel refusal while the
  dual-channel dict carries a real case for it. The docstring now says all five verbs
  remain `NotApplicable` in the REST-parity column, which matches the dictionary.
- **BUG-0242 / BUG-0248.** Both release notes keep their known-issue disclosures and
  drop the unreachable task-folder pointers. A provenance grep over the entire
  `release-notes/` directory, including the newest file (`v2.20.1.md`), returns nothing
  — BUG-0248's fourth criterion.
- **BUG-0246 (criteria 1 and 2).** All seven roles are now under budget:
  po 63167/65536, pm 76689/81920, coder 69053/69632, writer 68797/69632,
  tester 101516/102400, cm 85578/86016, overwatch 73426/73728. No
  `governance_byte_budget` entry appears in either runner's findings file.

### Caveat rows

- **BUG-0230 — pass-with-caveat.** All ten provenance citations are gone and the
  surrounding explanations survive. The bug's second acceptance criterion as literally
  written (`grep -rnE 'BUG-[0-9]{4}'` over four files returns nothing) still returns
  three hits in `team/tests/integration/test_defer_campaign_lifecycle.py` (168, 268,
  332) — but every one is `bug_id="BUG-0001"`, synthetic fixture *data* the test plants
  to build a bug file, not a comment citing provenance. Directive 8's first sanctioned
  exception ("usage EXAMPLES where a bug- or version-shaped VALUE illustrates an
  interface") covers them and the hygiene lint agrees, flagging none. Intent met; the
  literal grep is over-broad. Not filed.
- **BUG-0253 — pass-with-caveat.** All four acceptance criteria are met: with
  `--manifest PATH` against a drifted copy, both the `message` and the remedy's re-run
  command name `PATH`; with no flag, the default is named and no `--manifest` is added;
  and `team/tests/unit/test_operator_verb_manifest.py` asserts both shapes. The
  residual: the remedy's *leading* clause still reads "update
  `team/config/operator_verbs.manifest` (or the script/router it disagrees with)" even
  when a different file was scanned, so one clause of the sentence still names a file
  the run never read. It is cosmetic beside the fixed half — the re-run command, which
  is the actionable part, is correct — and no acceptance criterion covers it. Recorded
  here, not filed.

## Lint Findings (non-blocking)

- `hygiene` — 2 `provenance` instances, both
  `team/tests/unit/test_operator_verb_manifest.py` (lines 159 and 176), both citing the
  task ID `CODER-20260911-066` — **filed BUG-0260 (Path C-A)**. One bug for the lint
  class, both instances listed inside it, severity rolled up to the highest (`low`).
  No open or running bug tracks these two sites past this release: BUG-0245 and
  BUG-0254 both name them but both close with this RC (see Finding 2).

Both findings files were present and carried nothing else:
`${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-unit.json` (2 findings),
`${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-integration.json` (2 findings, byte-identical
instances). No `governance_byte_budget`, `comment_provenance`, `runnable_remedy`,
`operator_verb_manifest`, `census_compliance`, `skip_bug_gate`, `help_presence`,
`dual_channel_pair` or `orphan_tests` finding appeared in either file.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=6d freshness=stale — the last lane run was red, failing the docker/claude-abliterated and podman/claude-abliterated pairs on a Dockerfile `FROM` parse error and podman/rocky9 on a `COPY --chmod` step; it cites BUG-0167, filed by that deep-verify run and since closed, and the record is stale on the version count alone (6d is inside the 7-day window, but v2.9.20 is not the v2.20.2 under verification), so nothing has been built cold against this tree.

Per TESTER.md Step 5d the red record is cited, never re-filed, and neither its verdict
nor its staleness moves this RC's recommendation or systemic risk.
`team/scripts/deep-verify.sh` is present in the tree, so the images are not shipping
with no cold-build lane in front of them — the lane simply has not run since v2.9.20.

## Autonomous criterion (Step 6.12)

`Autonomous operation criterion: Manual interventions required: none`

| # | Check | Answer | Evidence |
|---|---|---|---|
| 1 | Tasks force-promoted WAITING -> BACKLOG by hand? | NO | Every one of the eleven RC task `status.md` files is a clean wake-written transition; no `manual` / `force-promoted` / `hand-edit` string in any of them |
| 2 | `release-state.md` edited outside the CM scripts? | NO | mtime `2026-09-13 03:03`, matching `## RC Opened At 2026-09-13T03:03:25+00:00`; untouched since `cm-open-rc.sh` |
| 3 | Scripts patched mid-build? | NO | All 24 commits on the RC are task feature-branch commits and their `--no-ff` merges, timestamps running 03:33 -> 08:19 in ticket order; no out-of-band commit |
| 4 | Queue files edited by hand? | NO | Queue mtimes align with wake firings (`tester_backlog.md` / `writer_backlog.md` at 08:20, this task's own wake); `cron-coder.log` shows only routine "no BLOCKED tasks to re-check" lines, no stuck-task or zero-progress reset |

No `HALT` file exists at either scope. The build ran PM materialization -> CM-open ->
eight CODER tasks -> two WRITER tasks -> TESTER without intervention.

## Caveats

- **Behavioural verification was applied to both guard-shaped fixes, per Principle 4.**
  BUG-0256 and BUG-0258 are both "prevent something" fixes, so both were exercised by
  attempting the thing they prevent, and both had their new-normal path re-checked so
  the fix is not a blanket suppression. All ad-hoc invocations ran against throwaway
  roots under `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester013/scratch`, never
  the live install (`roles/TESTER.md`, "Live-Install Safety"). The only commands pointed
  at the live kanban root were the two read-only renderers (`show-queues.sh`,
  `agent-metrics.sh`), which write nothing.
- **The governance-trim review was a spot-check, not an exhaustive audit.**
  `WRITER-20260913-011` rewrites 149 lines of `team/SOP-core.md` and trims four role
  files. The `SOP-core.md` diff was read in full and the two passages that were moved
  rather than condensed were traced (the per-role "Does NOT" lists -> each role's own
  file; the `Regression`/`Integrity`/`Fix Effort` clearing rule -> `roles/CM.md:83`);
  both landed. Finding 1 is the one delegation found that did not. The `CODER.md` diff
  was read in full; the `CM.md` and `OVERWATCH.md` diffs were not read line by line — a
  further lost-delegation of the same shape in those two files would not have been
  caught. That is the one dimension of this RC that is verified by sampling rather than
  exhaustively.
- **`team/scripts/lib/refusal_ledger.sh` carries no `set -euo pipefail`.** This is
  correct — it is a sourced library, and strict mode there would leak into every caller
  — and it is unchanged by this RC. Noted only so the Step 6.9 preservation check reads
  as deliberate rather than skipped.
- **`BUG-0244` and three `CODER-20260101-00N` tokens appear as added lines in the diff.**
  Both are sanctioned: the bug ID is a known-issues reference in
  `release-notes/v2.19.0.md`, which is the durable reader-resolvable form BUG-0242 and
  BUG-0248 explicitly endorse, and the `CODER-20260101-00N` tokens are fixture *values*
  planted by `team/tests/unit/test_lint_hygiene.py` to exercise the new config surface,
  covered by Directive 8's usage-example exception. The hygiene lint flags neither.
- **The `agent-metrics` footnote renders flush against the last table row**, with no
  blank line between them. Cosmetic; the bug asked for a footnote and got one. Not a
  finding.
- **Step 6.8 (synthetic release simulation).** The RC's diff touches release-lifecycle
  files (`team/scripts/cm/release.sh`, `team/scripts/lib/cm/release_notes.sh`), so the
  trigger condition is met. `team/tests/integration/test_cm_release_synthetic.py` is
  present and ran inside the gated integration suite (exit 0) rather than being invoked
  separately, since a direct pytest run is not a valid verdict source.
- **Step 6.13 (skip-cites-real-bug).** No skip annotation in `team/tests/` cites a bug
  ID at all, and the only `BUG-NNNN`-shaped hits are inside archived coverage-baseline
  run logs under `team/tests/artifacts/`, which are recorded output rather than skip
  annotations. Nothing to resolve; the gated runners' own `lint_skip_bug_gate.sh`
  pre-flight also passed.
