# Verification Report: ai_rc/v2.23.0

## Summary

Verified release candidate `ai_rc/v2.23.0` ("OVERWATCH block resolution")
against `projects/pgai-agent-kanban/requirements/v2.23.0-overwatch-block-resolution.md`
in the isolated worktree at
`/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260915-044-verify-2-23-0`
(HEAD `84f68ceb11a7748c30f88a5b7d231d0929bde020`, identical to `ai_rc/v2.23.0`,
clean tree, no stashes). Pre-flight merge verification passed: all ten
prerequisite tasks are DONE, their feature branches were deleted after merge
(normal cleanup), and the RC carries 30 commits beyond `ai_main` with one merge
commit per task. Both gated runners exited 0. Verification exercised the
release's guards behaviourally rather than structurally: the config accessor's
fail-safe and its refusal, the INTENT.md parser's fail-closed grammar, the
upgrade migration's idempotency and non-destructiveness, the Lane B
force-admission guard in all three of its outcomes, and the four states of the
`why.sh` HALT surface. Four findings resulted, all filed via Path C
(autonomous follow-up filing — non-blocking). The most substantial is that
Lane A's first playbook step — rewriting the originating bug's Fix section —
has no caller in the shipped code path, so the lane recycles a release
candidate without removing the ambiguity that caused it. The feature ships
dark (`overwatch_resolve` defaults to false everywhere), so nothing in this
release changes behaviour for any project until an operator opts in.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

medium

## Findings

### Finding 1: Lane A never performs step (a), the Fix rewrite

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (a real defect in the release's headline lane that
ships and is healed next iteration; it does not make anything worse than
today's behaviour, because the lane is off by default and every action it does
take is reversible)
**Regression:** no (the whole lane is new in this RC; nothing that worked in
v2.22.5 stopped working)
**Integrity Damage:** no (the drill's actions are the documented, reversible
cancel playbook, and the single-attempt guard bounds the retry to one)
**Filed As:** BUG-0286 (Path C-A — filed as BUG — code defect / regression /
test failure)

The playbook's six steps are (a) rewrite the blamed bug's `## Fix`, (b)
unwind-rc, (c) cancel-rc, (d) delete the bundle doc, (e) clear the ship-policy
HALT, (f) let discovery re-decompose. Steps (b)–(e) are implemented and run.
Step (a) is unreachable: `rewrite_bug_fix_section()` in
`team/pgai_agent_kanban/overwatch/fix_rewrite.py` has no caller outside two
test modules. `resolution_stage.py` states the gap rather than closing it —
step (a) "is a separate call site's scope ... and is assumed to have already
run" — and no such call site exists.

The assumption also cannot be satisfied at runtime. `run_resolution_stage` is
invoked as a subprocess by `run_overwatch_resolution_stage()`
(`team/scripts/lib/wake_project.sh:282`, called from
`team/scripts/lib/wake_driver.sh:414`) on every OVERWATCH firing, ahead of any
agent reasoning. By the time a reasoning tier could act, the RC is already
unwound, cancelled, its bundle deleted and its HALT cleared.

Test-fidelity note: `TestLaneAFullPlaybookWithFixRewriteAndRedecompose` in
`team/tests/integration/test_overwatch_lane_a_drill.py` calls
`rewrite_bug_fix_section()` itself, in the test body, before invoking
`run_resolution_stage()`, then asserts the rewrite is present. The assertion is
true of the test, not of the shipped path, so a green suite cannot catch this.
This is the "test mocks away the exact condition that breaks in production"
pattern from the Test Fidelity checklist.

Likely live consequence with the key on: attempt 1 recycles the RC, the same
ambiguous Fix is re-decomposed, the new RC HALTs on the same finding, the
single-attempt guard fires and the block escalates to Lane C — one release
cycle spent to reach where Lane C would have started.

### Finding 2: the v2.23.0 migration is never invoked, and its README still says no migrations exist

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (hygiene/discoverability; the key fails safe to `false`
when absent, which is the same posture the seeded comment documents)
**Regression:** no (new in this RC)
**Integrity Damage:** no
**Filed As:** BUG-0287 (Path C-A)

`team/scripts/migrations/migrate_v2.22.5_to_v2.23.0.sh` behaves correctly when
run by hand — verified behaviourally in a sandbox root: it seeds the commented
block at the end of `[release]`, a second run is a byte-identical no-op, and a
project whose operator had already set `overwatch_resolve = true` is left
untouched. But `install.sh` contains no migration step
(`grep -rn -i "migrat" install.sh` is empty), no other script calls it, and
`team/scripts/migrations/README.md` still carries "Manual operator procedure
(v1.0.0 — no migrations exist)", "At v1.0.0 there are no scripts, so this step
is a **no-op**", and an "Auto-runner — deferred" section promising integration
into `upgrade.sh` "when the first real migration is written" — which is what
this release did. No operator-facing document names the command.

### Finding 3: the Lane B refusal's remedy describes a rule the check does not implement

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (the machine-to-machine Lane B path is unaffected —
`_run_lane_b()` passes the matched declaration verbatim, so the two always
agree; this misleads only a human reading the refusal)
**Regression:** no (the refusal branch is new in this RC)
**Integrity Damage:** no
**Filed As:** BUG-0288 (Path C-A)

`team/scripts/cm/release.sh` refuses an unmatched Lane B override with the
remedy "Add (or correct) a dated entry in the project's INTENT.md whose text
**contains** `--reason` verbatim", while `_cm_release_lane_b_intent_verified`
tests `entry.declaration == reason`. Reproduced in a sandbox kanban root: an
entry reading `PREFIX — <reason> — trailing note.` with
`--reason "<reason>"` is refused, and the byte-equal entry is admitted. A
refusal whose remedy does not clear it is the failure the dual-channel error
contract exists to prevent.

### Finding 4: ten provenance hygiene findings introduced by this RC

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (quality-class lint; zero runtime-failure risk)
**Regression:** no (all ten sites are new lines added by this RC, but nothing
previously working is affected)
**Integrity Damage:** no
**Filed As:** BUG-0289 (Path C-A)

Recorded in full in the Lint Findings section below. Filed as one bug for the
lint class, per the one-bug-per-lint-class-per-run rule, with every instance
listed as `<path>:<line>`. Per Step 5c this finding does not on its own move
the recommendation or the systemic risk.

### Finding 5: pre-flight merge verification

**Category:** pass

Ten prerequisite tasks, all `## State: DONE`. Every feature branch
(`ai_feature/CODER-20260915-033…042`, `ai_feature/WRITER-20260915-041`) is
absent from `refs/heads`, the normal post-merge cleanup, so Checks 1 and 2 are
skipped per the role file. `git log --oneline ai_main..ai_rc/v2.23.0` shows 30
commits including exactly one `Merge ai_feature/<task> into ai_rc/v2.23.0`
commit per prerequisite task.

### Finding 6: the `overwatch_resolve` accessor is fail-safe, precedence-correct, and refuses a typo

**Category:** pass

Exercised `pp_overwatch_resolve` in a throwaway kanban root, six cases:
key absent from both files → `false`; project `true` → `true`; project `false`
→ `false`; commented-out only → `false`; kanban `true` with project silent →
`true`; kanban `true` with project `false` → `false` (project wins). A typo
(`yes`) is never guessed into either boolean — it returns 1 and emits the
dual-channel contract naming the key, the value, the file, and a runnable
remedy. The refusal carries `CENSUS-ANCHOR: overwatch-resolve-invalid-01` and
the matching `BLOCK-WORTHY` Locator Index line was added to
`docs/blocking-site-census.md` in the same change, as was
`overwatch-lane-b-intent-unmatched-01` for the release-script site.

### Finding 7: the INTENT.md ledger ships with zero live entries and its parser is fail-closed

**Category:** pass

`parse_entries()` on the shipped `INTENT-TEMPLATE.md` returns `[]` — the
worked example stays inert because it is fenced and sits above `## Entries`.
Adding one real bullet yields exactly one entry. A file mixing an uncalendared
date (`2026-13-40`), a dateless bullet, an empty declaration and a wrapped
(continuation-line) entry alongside one well-formed entry yields only the
well-formed one: malformed entries are dropped whole, never truncated. A
missing file returns `[]` rather than raising. Both workflow skeletons
(`release/`, `document/`) carry the template, and `create-project.sh` copies it
to `INTENT.md`; an unrecognised workflow falls back to the `release` template
dir, so the copy cannot fail for want of a template.

### Finding 8: the upgrade migration is additive, idempotent and never flips an operator's value

**Category:** pass

Behaviourally verified in a sandbox root with two registered projects: one
with no `overwatch_resolve` line, one with an operator-set
`overwatch_resolve = true`. Run 1 seeded the first and skipped the second; run
2 skipped both; the seeded file was byte-identical between runs and the
operator's file byte-identical to its pre-migration copy. After migration the
seeded project resolves to `false` and the operator's project still resolves
to `true`. (Finding 2 is about the script never being invoked, not about what
it does.)

### Finding 9: Lane B cannot force a ship without a declared entry

**Category:** pass

The guard was exercised against its failure mode, in a throwaway root, with
`PGAI_AGENT_WAKE_ACTIVE=1`:

1. `--force --reason <text> --overwatch-lane-b-ship` against an `INTENT.md`
   with zero live entries → refused,
   `cm-release.overwatch-lane-b.intent-not-matched`, exit 1.
2. `--force --reason <text>` with no Lane B flag → refused,
   `cm-release.operator-force.agent-wake-refused` — the operator-only override
   is still unreachable from a wake.
3. The same Lane B invocation after adding a live entry whose declaration is
   the reason → admitted, with the admission narrated to stderr, then the run
   proceeded into ordinary release work and stopped on the sandbox's missing
   `dev_tree_path`.

"No entry, no force — EVER" holds, and the flag alone proves nothing: the
release script re-reads the live ledger through the same parser the classifier
uses.

### Finding 10: both live surfaces report a resolution, and neither over-reports

**Category:** pass

`verdict_resolve_project` was driven through four states in a sandbox root
holding a CM ship-policy HALT and an `Active RC` of v2.23.0:

- marker present → `HALTED`, cause `OVERWATCH resolving (lane A, attempt 1) for v2.23.0`
- marker absent, Decision event present and version-matched → `HALTED`, cause
  `OVERWATCH Decision recorded (lane C, v2.23.0): …`
- marker present but malformed (missing `version`) → falls through to the
  Decision event rather than crashing or half-reporting
- Decision event whose `Version` no longer matches `Active RC` → falls back to
  the bare HALT-file reason, so a decision about a since-shipped RC is never
  presented as the explanation of a later halt

The verdict word and exit code are unchanged throughout. The attention window
(`scan_attention.py`) reads the same two files with an independently written
reader, so the two surfaces cannot disagree. The `## OVERWATCH Decision` event
carries Timestamp, Version, Lane, Evidence, Verbs Run and Outcome — the full
set the requirements doc asks for — and is appended atomically.

### Finding 11: with the key off, the stage behaves exactly as before

**Category:** pass

`run_resolution_stage()` consults `resolve_overwatch_resolve()` only after the
pre-existing matrix has declined to HALT-hard and only below the retry
ceiling; a config-read error is caught and treated as `False`. With the flag
false the function returns through the pre-existing decline narration, writing
no Decision event and no marker. The shipped `kanban.cfg_example` and
`project.cfg_example` carry the key commented out with its accepted values and
default documented, and `config_loader.sh`'s key registry records it.

### Finding 12: grep-zero for direct state edits

**Category:** pass

`team/tests/unit/test_overwatch_resolution_no_direct_state_edits.py` scans
every write-mode `open()` in `resolution_stage.py` and allows only the two
reviewed local targets (`tmp_path`, `log_file`); a second differently-shaped
check refuses write-mode opens on named state paths; and a third splices a
deliberate violation into a copy of the source to prove the check is
load-bearing. All mutations in the module route through operator-verb
subprocesses (`unwind-rc.sh`, `cancel-rc.sh`, `cm/release.sh`), the
`pgai_agent_kanban.ops.write` layer, or the module's own atomic append-only
writers.

### Finding 13: release-state and version drift

**Category:** pass

`pp_last_released_version` returns `v2.22.5`; `semver_gt v2.23.0 v2.22.5` is
true. `release-state.md` names `Active RC: v2.23.0`, opened by
`CM-20260915-043-open-rc-2-23-0`, and its `## Last Released` field is a
well-formed `v2.22.5`. The `ai_rc/v2.23.0` branch exists and matches the
worktree HEAD.

### Finding 14: skip-cites-real-bug

**Category:** pass

No placeholder skip ids. The four `BUG-1234/1235/1237/1239` ids that a naive
grep surfaces are string literals inside
`team/tests/unit/test_skip_bug_citation_scan.py`'s own fixtures, not skip
annotations. The gated runners' `skip_cites_real_bug` lint reported OK.

### Finding 15: prerequisite dependency graph

**Category:** pass

No cycles; every referenced task id exists; the lifecycle bookends are correct
(`CM-20260915-043-open-rc-2-23-0` DONE at the head,
`CM-20260915-046-release-2-23-0` `[W]` at the tail with this task among its
prerequisites). Sequence numbers do not collide; the unused 045 slot is a gap
in numbering, not a collision.

## Test Results

- `bash team/scripts/run-unit-tests.sh --coverage` → **exit 0**.
  `4606 passed, 31 skipped in 759.07s (0:12:39)`. Every gated lint reported OK
  except the quality-class `hygiene` lint (10 findings, non-blocking).
- `bash team/scripts/run-integration-tests.sh --coverage` → **exit 0**.
  `496 passed, 1 skipped in 587.86s (0:09:47)`. Same 10 quality-class findings.

Both gated-runner exit codes are the suite verdict; the PASS evidence cited
here is those exit codes, not a direct pytest line. (A direct
`python3 -m pytest` run of the seven new/changed unit modules — 171 tests, all
passing — was used only as a debugging aid and establishes nothing on its own.)

## Code Coverage

- Unit:        line 77%  |  branch 63%   (16361 stmts, 4920 branches)
- Integration: line 10%  |  branch  8%   (16352 stmts, 4920 branches)

Read from `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/unit.json` and
`integration.json` (`totals.covered_lines / num_statements` and
`covered_branches / num_branches`; field names confirmed against the generated
files). The blended `TOTAL` figures on the terminal reports are 73% and 10%
respectively — informational only, and not an input to the recommendation.

## Lint Findings (non-blocking)

Ten findings, one lint class, identical in both runners' findings files
(`${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-unit.json`,
`findings-integration.json`). Filed as one bug for the class per the
one-bug-per-lint-class-per-run rule.

- `hygiene` (class `provenance`) — `team/scripts/lib/verdict.sh:284` cites the unshipped requirements-document path — filed BUG-0289 (Path C-A)
- `hygiene` (class `provenance`) — `team/scripts/lib/verdict.sh:409` cites the unshipped requirements-document path — filed BUG-0289
- `hygiene` (class `provenance`) — `team/pgai_agent_kanban/dashboard/scan_attention.py:830` cites the unshipped requirements-document path — filed BUG-0289
- `hygiene` (class `provenance`) — `team/pgai_agent_kanban/dashboard/scan_attention.py:1018` cites the unshipped requirements-document path — filed BUG-0289
- `hygiene` (class `provenance`) — `team/pgai_agent_kanban/overwatch/fix_rewrite.py:53` cites a bug id — filed BUG-0289
- `hygiene` (class `provenance`) — `team/pgai_agent_kanban/overwatch/resolution_stage.py:471` cites a task id — filed BUG-0289
- `hygiene` (class `provenance`) — `team/pgai_agent_kanban/overwatch/resolution_stage.py:473` cites a task id — filed BUG-0289
- `hygiene` (class `provenance`) — `team/pgai_agent_kanban/overwatch/resolution_stage.py:649` cites a task id — filed BUG-0289
- `hygiene` (class `provenance`) — `team/tests/integration/test_overwatch_lane_a_drill.py:81` cites a bug id — filed BUG-0289
- `hygiene` (class `provenance`) — `team/tests/integration/test_overwatch_lane_b_ship.py:10` cites a task id — filed BUG-0289

Open sibling: BUG-0285 (`## Status: open`) tracks one instance of the same
lint class from a prior run, in a file this RC did not touch.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=8d freshness=stale — the last recorded lane run was red on the docker/claude-abliterated, podman/claude-abliterated and podman/rocky9 pairs; cites BUG-0167-cold-build-claude-abliterated-failure, filed by that deep-verify run and since closed, and the record is stale on both counts (8d exceeds the 7-day window, and v2.9.20 is not the v2.23.0 under verification), so nothing is known about whether this tree's images build.

## Autonomous criterion

1. Were any tasks force-promoted from WAITING to BACKLOG manually? **NO** —
   every prerequisite task's `status.md` records the wake script's own
   promotion, and this task's own summary records the same.
2. Was `release-state.md` edited outside the CM scripts? **NO** — the file
   carries only the four schema fields, with `Active RC: v2.23.0` and the
   opening task id written by `cm-open-rc.sh`.
3. Were any scripts patched mid-build? **NO** — every commit on the RC belongs
   to one of the ten prerequisite tasks' merges, and every script change is
   inside a task's own deliverable.
4. Were any queue files manually edited? **NO** — the coder/writer/cm/tester
   queue entries for this cycle carry the ordinary `[x]`/`[W]` markers written
   by the wake script.

Manual interventions required: none. No `HALT` at either scope; no prerequisite
task carries `Needs Human: yes`.

## Caveats

- **The feature ships dark.** Every finding above about Lane A and Lane B
  describes behaviour that is unreachable until an operator uncomments
  `[release] overwatch_resolve` and sets it to `true` in a `project.cfg` or
  `kanban.cfg`. With the key at its shipped value every project behaves
  exactly as it did in v2.22.5. This is what keeps Finding 1 at
  SHIP-WITH-CONCERNS rather than SHIP-WITH-SERIOUS-CONCERNS: the release does
  not make anything worse for anyone today.
- **Lane B's match test is very strict by design.** The classifier requires
  every identifier-shaped token in the block's evidence — including the RC
  branch, the base branch and the target version the stage adds itself — to
  appear verbatim in a single declaration. `docs/OPERATIONS.md` documents this
  honestly and tells the operator to copy identifiers out of the TESTER report.
  Noted as an observation, not a finding: it is the fail-closed direction.
- **The OVERWATCH governance byte budget was re-baselined** from 73728 to
  81920 in this RC to accommodate the role file's growth; the runner reports
  `overwatch 77535 81920 under`. A budget raised to fit its content is a
  judgement call rather than a defect, and it was made explicitly in its own
  commit. Observation only.
- **Stale claim-lock litter** exists in the project's `bugs/` directory
  (`.claim-0136.lock` and six others) from earlier runs of the bug-id claim
  helper. It predates this RC and did not interfere with this run's four
  claims, whose locks were released. Observation only; not filed.
- Verification ran in the detached worktree only. No push, no branch creation,
  no writes to the dev tree. Every behavioural probe that could mutate kanban
  state ran against a throwaway root created under
  `$PGAI_AGENT_KANBAN_TEMP_DIR`, never the live install.
