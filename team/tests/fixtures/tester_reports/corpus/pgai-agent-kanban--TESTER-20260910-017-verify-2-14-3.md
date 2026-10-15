# Verification Report: ai_rc/v2.14.3

**Task:** TESTER-20260910-017-verify-2-14-3
**Agent:** tester
**Model:** claude-fable-5-1
**RC branch / commit:** `ai_rc/v2.14.3` @ `83752e2e9c5c09d893a4439921c83eb928374af9`
**Worktree:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260910-017-verify-2-14-3` (detached HEAD, clean, no stashes)
**Requirements doc:** `projects/pgai-agent-kanban/requirements/v2.14.3-bugfix-bundle-20260910.md`
**Verified:** 2026-09-10

## Summary

v2.14.3 is a three-item bug bundle. All five prerequisite tasks
(CODER-011/012/013/015, WRITER-014) are DONE and merged into the RC; the RC
carries ten commits and touches twenty files. Both gated runners exit 0 --
`run-unit-tests.sh` 3610 passed / 3 skipped, `run-integration-tests.sh` 424
passed / 1 skipped -- which is the first green unit certification since v2.14.1
and is the headline result of BUG-0221's fix. BUG-0221 (provenance unit tests
scanning the live `release-notes/` directory) and BUG-0223 (comments citing the
removed `team/SOP.md`) are fully addressed and verified behaviourally.
BUG-0222 is addressed for everything it named -- `release-notes/v2.14.1.md` is
clean and the Known-Issues emitter is hardened with new tests -- but two of its
five acceptance criteria still fail on this tree, because an identical
task-ID leak is live one file over in `release-notes/v2.14.2.md:42`, a
pre-existing line the RC did not touch. Behavioural probing of the new
Known-Issues guard also found that it strips only the numeric head of a task
identifier and strands the trailing slug in the rendered bullet. Both were
filed via Path C-A; neither is severe. Recommendation: SHIP-WITH-CONCERNS.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

low

## Suite Results (authoritative -- gated runners)

| Runner | Exit | Result |
|---|---|---|
| `bash team/scripts/run-unit-tests.sh --coverage` | **0** | 3610 passed, 3 skipped in 465.77s |
| `bash team/scripts/run-integration-tests.sh --coverage` | **0** | 424 passed, 1 skipped in 383.56s |

`UNIT_EXIT == 0` AND `INTEGRATION_EXIT == 0`. Both runners' lint pre-flights
passed (breakage class clean; two quality-class findings recorded, see below).
Post-run cleanliness gates green in both: `bare_tmp_cleanliness: OK`,
`listener_cleanliness: OK`.

Captured output: `$PGAI_AGENT_KANBAN_TEMP_DIR/tester/unit_output.txt`,
`$PGAI_AGENT_KANBAN_TEMP_DIR/tester/integration_output.txt`.

## Code Coverage

Source: `$PGAI_AGENT_KANBAN_TEMP_DIR/coverage/{unit,integration}.json`,
`totals` object (field names confirmed against the generated files, not assumed).

- Unit:        line 74%  |  branch 61%   (10591/14255 stmts, 2666/4366 branches; blended `percent_covered` 71%)
- Integration: line 8%   |  branch 6%    (1107/14255 stmts, 261/4366 branches; blended `percent_covered` 7%)

Informational only. Does not enter the PASS precondition, the recommendation,
or the systemic-risk rating.

## Findings

### Finding 1: BUG-0221 -- provenance unit tests no longer inherit the repository's own release-notes verdict

**Category:** pass

The three named cases (`test_docs_audits_path_not_flagged`,
`test_bare_release_slug_filename_value_not_flagged`,
`test_bare_release_slug_format_description_not_flagged`) now pass
`--prose-dirs <own scratch dir>` alongside `--code-dirs`, and CODER-011 applied
the same pin to five sibling cases in the same file that had the same exposure.

Verified:

1. **Acceptance item 1 satisfied in the natural state, no plant required.** The
   RC tree *currently carries* a live provenance finding under the default prose
   root (`release-notes/v2.14.2.md:42`, see Finding 3). All eight cases in
   `TestCommentProvenanceUnshippedPathAndBareSlug` pass anyway, and the full
   unit runner exits 0. This is exactly the condition the bug said would turn
   them red, present and harmless.
2. **Acceptance item 2 -- not neutered.** Behavioural probe of the pinned
   invocation shape, run four ways against scratch fixtures:
   - clean code + clean prose -> exit 0
   - violating code + clean prose -> exit **1**
   - clean code + violating prose -> exit **1**
   - clean code + clean prose, with the real tree's live finding present -> the
     real `release-notes/` path appears nowhere in the output (grep count 0)

   The pin isolates without disabling detection in either surface.
3. **Acceptance items 3 and 4** -- both gated runners exit 0. See Suite Results.
4. **Acceptance item 5** (the sweep) -- delivered by CODER-012 as
   `tasks/CODER-20260910-012-lint-verdict-assert-audit/artifacts/lint-verdict-assert-audit.md`
   (15.7 KB): method, the exact grep commands, 20 enumerated call sites across
   14 files, each dispositioned "deliberately default" with a reason, plus the
   negative findings for the seven files with no hits. Every enumerated site now
   carries a "Deliberately exercises the real ..." comment; I confirmed the
   comments are present in the diff for `test_lint_api_parity.py`,
   `test_lint_help_presence.py` and `test_lint_gate_census_compliance.py`.

`team/tests/unit/test_lint_comment_provenance.py`: 55 passed.

### Finding 2: BUG-0223 -- dangling `team/SOP.md` citations repointed at files that exist

**Category:** pass

All three references are gone and each replacement resolves to a real file *and*
a real heading inside it:

```
$ grep -rn "team/SOP\.md" --include='*.py' --include='*.sh' --include='*.md' team/ docs/ | grep -v '^team/tests/'
(no output, exit 1)
$ grep -rn "team/SOP\.md" ... team/ docs/          # including tests
(no output)
```

| Site | New target | Target exists | Heading exists |
|---|---|---|---|
| `lint_gate_census_compliance.py:47` | `team/SOP-core.md` "Gates and Refusals" | yes | yes (line 207) |
| `lint_gate_census_compliance.py:514` | `team/SOP-core.md` "Gates and Refusals" | yes | yes |
| `lib/cm/release_campaign_spend.sh:30` | `docs/cm-release-reference.md` "HALT-AFTER in full" | yes | yes (line 107) |

The third retarget is also *substantively* correct, not merely resolvable: the
comment cites the latching `>=` reasoning, and `docs/cm-release-reference.md`
line 124 carries "The `rc:vX.Y.Z` drain is **monotonic and latching** ... compares
it with semver `>=`, not string equality."  The pre-existing third instance the
bug asked to sweep in the same pass was included.

### Finding 3: BUG-0222 -- the task-ID leak is fixed in the file it named, but the same leak is live in `release-notes/v2.14.2.md`

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (quality-class lint; both gated runners exit 0 with the finding recorded; no shipped code path affected)
**Regression:** no (the offending line is on `ai_main`, added by commits `b84c3c4` / `7f8ff78`; `git diff ai_main..ai_rc/v2.14.3 -- release-notes/v2.14.2.md` is empty)
**Integrity Damage:** no
**Filed As:** BUG-0224 (Path C-A -- filed as BUG: code defect / regression / test failure)

What the RC delivered against BUG-0222, all verified:

- `grep -c "TESTER-2026" release-notes/v2.14.1.md` -> **0** (AC 2 met).
- `grep -c "provenance-allowlist" release-notes/v2.14.1.md` -> **0** (AC 3 met --
  the escape hatch the bug forbade was not used; WRITER-014 rewrote the Known
  Issues text to name BUG-0220 in reader-actionable terms instead).
- AC 5 (the writing path cannot emit a task ID into Known Issues again) --
  delivered by CODER-013 as `_strip_internal_ids` in
  `team/scripts/lib/cm/release_known_issue.sh`, with 7 new unit cases
  (6-way parametrised over the agent prefixes plus a `## Public ID` case).
  `team/tests/unit/test_cm_known_issue_carry_forward_bash.py`: 26 passed.

What still fails:

- **AC 1** -- `python3 team/scripts/lint_comment_provenance.py` exits **1**, not 0:
  `release-notes/v2.14.2.md:42: [task-ID] provenance reference 'TESTER-20260909-045' in code`.
- **AC 4** -- both `findings-unit.json` and `findings-integration.json` still
  carry a `comment_provenance` finding.

The offending line is in a hand-authored `## Bugs Skipped` block that quotes the
previous cycle's TESTER findings verbatim -- and one of those findings was itself
about a leaked task ID, so quoting it re-leaked the identifier. `grep -rn "Bugs
Skipped" team/` finds no generator: that block passes through no scrub at all,
unlike the Known-Issues and bundled-item blocks. So BUG-0222's generator
hardening is real but landed one surface short of where the recurrence actually
came from.

This is filed fresh rather than cited against BUG-0222 deliberately: BUG-0222 is
`running` and `cm-release.sh` Step 16b will set it to `done` when v2.14.3 ships,
at which point the live defect would have no open bug at all.

### Finding 4: the new internal-ID scrub removes only the head of a task identifier and strands the slug

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (rendered release-note text only; no state, no data, no shipped code path)
**Regression:** yes (the defective scrub was added to `release_known_issue.sh` by this RC, commit `276e0c4`; the identical pattern pre-exists at `release_notes.sh:269`, which this RC copied it from)
**Integrity Damage:** no
**Filed As:** BUG-0225 (Path C-A)

Step 6.5 behavioural probe of the guard CODER-013 added. Driving
`build_known_issue_notes_block` with a bug whose Symptom prose quotes a task ID:

```
input : The importer crashes on a malformed header (Source Task: TESTER-20260909-045-verify-2-14-1).
output: - The importer crashes on a malformed header (Source Task: -verify-2-14-1). (internal-only, not yet disclosed) -- No workaround available.
```

The `## Public ID` path is the same:
`- **KI-2026.09 (see -verify-2-14-1)** -- ...`.

Cause is the pattern's right boundary -- `\b` after `\d+` is satisfied by the
hyphen that starts the slug, so the match ends at `TESTER-20260909-045`:

```python
r"|\b(?:CODER|WRITER|TESTER|CM|PM|PO)-\d{8}-\d+\b"
```

Confirmed directly in Python: `'Source Task: TESTER-20260909-045-verify-2-14-1.'`
-> `'Source Task: -verify-2-14-1.'`. The `BUG-NNNN` / `PRIORITY-NNNN` alternative
is unaffected (`'BUG-0222 mentioned'` -> `' mentioned'`).

Both halves of the anti-pattern-6 check were run: the original failure mode
(task ID emitted whole) is genuinely prevented, and the normal case (a bug with
no internal ID in its Symptom) renders correctly and unchanged.

The new tests do not catch it because they assert only `task_id not in line` and
`prefix + "-20260910" not in line` -- both true of the mangled output. Nothing
asserts the surviving sentence is clean. This is the Test Fidelity checklist's
item 4 pattern: the assertion omits the inconvenient half of the behaviour.

### Finding 5: pre-flight, structural and constraint checks

**Category:** pass

- **Merge pre-flight:** every DONE task's feature branch is either merged or
  already deleted post-merge (all five of this RC's branches are deleted, the
  normal shape). No UNMERGED anomalies, no BUG-92 zero-commit anomalies.
- **Worktree:** `git rev-parse HEAD` == `git rev-parse ai_rc/v2.14.3`;
  `git status --short` empty; `git stash list` empty.
- **Requirements AC 2/3** (all Python compiles, all bash passes syntax check):
  `python3 -m py_compile` clean on all three changed `.py` files; `bash -n`
  clean on both changed `.sh` files. The gated runners' breakage-class
  pre-flight independently confirms this tree-wide.
- **Step 6.8 synthetic release simulation** (triggered -- the RC touches
  `team/scripts/lib/cm/`): `tests/integration/test_cm_release_synthetic.py`
  15 passed, run under a sandboxed `PGAI_AGENT_KANBAN_ROOT_PATH`.
- **Step 6.13 skip-cites-real-bug:** no placeholder `BUG-SKIP` / `BUG-<alpha>`
  skip annotations; no cited `BUG-NNNN` missing from `bugs/`. (The `grep`
  matches under `team/tests/artifacts/coverage-*/` are archived runner logs
  containing the literal string `BUG-NNNN`, not skip annotations.)
- **Step 6.1 drift:** `pp_last_released_version` -> `v2.14.2`;
  `semver_gt v2.14.3 v2.14.2` true; `release-state.md` `## Last Released` is
  `v2.14.2`, well-formed; `## Active RC` is `v2.14.3` and the branch exists.
- **Step 6.10 prereq graph:** no cycles; all referenced task IDs exist;
  bookends correct (CM-016 open-rc -> CODER-011/012/013 -> WRITER-014 ->
  CODER-015 -> TESTER-017 -> CM-019 release, currently `[W]`); sequence numbers
  consistent and non-colliding.
- **Step 6.14 coding standards spot-check:** no new scripts (no `--help`
  obligation); no new production string cites a bug/task/version ID -- the
  provenance lint's code surface is clean and its single finding is in prose;
  no new hardcoded operational value. One duplication note under Caveats.

## Lint Findings (non-blocking)

Ingested from `$PGAI_AGENT_KANBAN_TEMP_DIR/lint/findings-unit.json` (2 findings)
and `findings-integration.json` (2 findings) -- the same two classes in both,
grouped to two per the one-bug-per-lint-class rule.

- `comment_provenance` -- `release-notes/v2.14.2.md:42`: `[task-ID]` provenance
  reference `'TESTER-20260909-045'` in prose. 1 instance, severity `quality`. --
  filed BUG-0224 (Path C-A). Same filing as Finding 3; no open or running bug
  covered this file (BUG-0222 is `running` but names `v2.14.1.md` and closes
  when this RC ships).
- `governance_byte_budget` -- `pm over governance byte budget: assembled=76391
  bytes budget=73728 bytes`. 1 instance, severity `quality`, no file/line
  (coarse pass/fail-shaped lint). -- filed BUG-0226 (Path C-A). BUG-0220 covered
  this overage but its `## Status` is `done` (it was closed on the gate-demotion
  remedy, not on the number), so no open or running bug tracked it.

Per Step 5c neither of these, on its own, moves `## Recommendation` off `PASS`
or raises `## Systemic Risk`. The recommendation above is driven by Findings 3
and 4, which are Step-7 defect findings.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=3d freshness=stale -- the docker/claude-abliterated and podman/claude-abliterated pairs failed on a Dockerfile FROM parse error and podman/rocky9 failed at its `COPY --chmod=+x docker/entrypoint.sh` step; the record is inside the 7-day window but names v2.9.20, not the v2.14.3 under verification, so nothing is known about whether this tree's three Dockerfiles build cold; cites BUG-0167, filed by that deep-verify run.

The lane was not run during this verification, per Step 5d. Per Step 5d the red
record is cited, never re-filed, and neither its verdict nor its staleness moves
the recommendation or the systemic-risk rating above.

## Autonomous criterion

| # | Check | Answer |
|---|---|---|
| 1 | Any task force-promoted from WAITING to BACKLOG manually? | **NO** |
| 2 | `release-state.md` edited outside the cm-* scripts? | **NO** |
| 3 | Any scripts patched mid-build while the RC was in flight? | **NO** |
| 4 | Any queue file manually edited? | **NO** |

**Manual interventions required: none.**

Evidence: all ten RC commits carry the automation identity and fall in an
unbroken 05:57->07:43 sequence on 2026-09-10, matching the task order; no empty
commits on the RC (every non-merge commit touches at least one file);
`release-state.md` fields are exactly the shape `cm-open-rc.sh` writes, naming
`CM-20260910-016-open-rc-2-14-3`; queue file mtimes track the wake script's own
writes (`coder_backlog.md` 07:39, `writer_backlog.md` 07:44,
`tester_backlog.md` 07:45) with no out-of-band edits; `logs/activity.log`
records no host-level change since 2026-09-03; no `HALT` or `HALT-AFTER` flag
exists at either scope. The `[pm-materialize] Remove ... to force
re-materialization` lines in `cron-pm.log` are that tool's own advisory output,
not evidence of an intervention.

## Caveats

- **Observation (not a finding): the audit's disposition leaves the BUG-0221
  class reachable elsewhere.** CODER-012 correctly enumerated 20 real-tree
  assertion sites and dispositioned all 20 as "deliberately default," which is
  within what BUG-0221 acceptance item 5 asked for ("record the result"). But at
  least one of them -- `test_lint_help_presence.py::test_real_tree_produces_zero_offenders`,
  which asserts `main(...) == 0` over the real `team/scripts/` tree -- has the
  same *shape* as the defect just repaired: a quality-class lint's
  whole-repository verdict deciding the unit runner's exit code. It is green
  today and the audit's reasoning for keeping it (a live regression guard on
  shipped state) is defensible, so this is recorded as an observation rather
  than filed. Worth a design decision in a future cycle about whether "live
  regression guard" and "quality-class advisory" can be the same check.
- **Observation: duplicated scrub implementation.** `_INTERNAL_ID_RE` /
  `_strip_internal_ids` now exist in two files (`release_notes.sh:269` and
  `release_known_issue.sh`), against coding-standards directives 1 and 3. The
  new copy carries an explicit comment justifying it (both are extracted
  heredocs with no shared Python import path), which is a reasoned call rather
  than an oversight -- but Finding 4 is the concrete cost: one wrong regex now
  has two homes and the fix must land twice.
- **Observation: stale claim locks in `bugs/`.** Seven `.claim-NNNN.lock` files
  (0136, 0137, 0160, 0172, 0173, 0211, 0212) and three
  `.claim-NNNN.lock.empty.orphan` files persist from earlier runs. They did not
  affect this run's ID allocation -- `claim_next_bug_id` correctly returned 0224,
  0225, 0226 -- so this is hygiene, not a defect, and it predates this RC.
- **Unit suite is green for the first time since v2.14.1.** The previous two
  verification runs recorded `UNIT_EXIT == 1`. That is the substantive win in
  this RC and it is worth reading the SHIP-WITH-CONCERNS recommendation against:
  the two findings are both cosmetic, both small, and neither touches a code
  path a user or agent executes.
- Direct `python3 -m pytest` was used only as a failure-analysis and
  confirmation aid (targeted files and the synthetic-release integration file).
  The PASS evidence quoted above is the gated runners' exit codes, not a pytest
  "N passed" line.
- All ad-hoc invocations that could write under a kanban root were run with
  `PGAI_AGENT_KANBAN_ROOT_PATH` repointed at a throwaway `mktemp -d -p
  "$PGAI_AGENT_KANBAN_TEMP_DIR"` root. The live install was not mutated by any
  verification command.
