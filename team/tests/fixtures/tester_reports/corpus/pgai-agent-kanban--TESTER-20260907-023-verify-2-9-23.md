# Verification Report: rc/v2.9.23

**Project:** pgai-agent-kanban
**RC branch:** `ai_rc/v2.9.23` @ `e07cbd5` (worktree detached HEAD, clean, no stashes)
**Baseline:** `ai_main`
**Requirements:** `projects/pgai-agent-kanban/requirements/v2.9.23-bugfix-bundle-20260907.md` (one bundled item: BUG-0171)
**Task:** TESTER-20260907-023-verify-2-9-23
**Verified:** 2026-09-07

## Summary

v2.9.23 is a single-bug RC closing BUG-0171 in both of its parts. Part 1 is an
edit to live kanban process state, deliberately outside the RC diff:
`projects/pgai-agent-kanban/project.cfg`'s `deep_verify = off` key was
relocated from a trailing headerless line (which INI section-assignment put
under `[training]`, where nothing reads it) to under `[release]`, so the
operator's recorded opt-out is now actually in force. Part 2 is a dev-tree
change making the same mistake loud next time: both `[release]`-scoped
deep-verify readers in `team/scripts/lib/project_paths.sh` now scan the rest
of the config file before falling back to their documented default, and emit
an advisory stderr warning naming the section the key landed in, `[release]`,
the default applied, and the remedy. The RC also carries a correctness fix the
new warning would otherwise have caused — `deep-verify.sh`'s `--scheduled`
path captured the resolver with `2>&1`, which would have folded warning text
into the resolved mode string — plus seven new unit tests and matching
documentation in `team/SOP.md` and `docs/operator-commands.md`.

Every acceptance criterion in BUG-0171 and in the CODER ticket passes, verified
by execution rather than inspection: the resolver returns `off` on the live
config, the misplaced-key warning fires on a synthetic misplaced key for both
keys and stays silent in all four negative cases, the two acceptance tests fail
when the change is neutralised and pass when restored, the scheduled deep-verify
invocation narrates its skip and appends no ledger record, and both gated
runners exit 0. Two hygiene findings were raised outside the RC's criteria and
filed via Path C (autonomous follow-up filing — non-blocking): internal ticket
IDs left in three new test docstrings (this RC's own regression), and a
recurrence of the release-notes provenance class that BUG-0165 closed one
release ago. Neither affects behavior.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

low

## Suite Results

| Suite | Command (authoritative) | Exit | Result |
|---|---|---|---|
| Unit | `bash team/scripts/run-unit-tests.sh --coverage` | **0** | 3183 passed, 3 skipped (387 s) |
| Integration | `bash team/scripts/run-integration-tests.sh --coverage` | **0** | 337 passed, 1 skipped (211 s) |

Both gated runners exited 0, including their lint pre-flights. The PASS
evidence cited here is the gated-runner exit code, not a direct-pytest
"N passed" line. Direct pytest was used only for the fail-on-revert
demonstration in Finding 5 and the release-lifecycle simulation in Finding 9.

## Code Coverage

Source: the `totals` object of each runner's JSON report; key names confirmed
against the generated files before extraction.

- Unit:        line 74.2% (10492/14136 stmts)  |  branch 60.9% (2630/4316)
- Integration: line 7.6% (1070/14136 stmts)    |  branch 6.0% (259/4316)

Informational only; coverage does not enter the PASS precondition and is not a
finding.

## Findings

### Finding 1: BUG-0171 part 1 — `deep_verify = off` relocated into `[release]` and now in force

**Category:** pass

The live config's key is at `project.cfg:62`, between the `[release]` header
(line 60) and the next section header `[debug]` (line 67), beside
`max_rc_retries = 1`. No `deep_verify` occurrence survives after the last
`[training]` key. Resolver output:

```
$ bash -c 'source team/scripts/lib/project_paths.sh; pp_deep_verify_mode pgai-agent-kanban'
off       (exit 0, stderr empty)
```

The intent recorded in the file and the value in force now agree, which is the
whole of what BUG-0171 asked for. The constraint "relocate only" holds: every
key under `[project]`, `[versioning]`, `[release]`, `[debug]` and `[training]`
keeps its section and value, and the file is otherwise unreformatted.

### Finding 2: BUG-0171 part 2 — misplaced-key warning fires correctly for both keys

**Category:** pass

Behavioral verification against synthetic configs under a scratch `KANBAN_ROOT`
(`/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester-scratch/kroot`), one
case per branch. The positive case, both keys under `[training]` and neither
under `[release]`:

```
pp_deep_verify_mode fixtureproj              -> stdout "weekly", exit 0
  stderr: project_paths.sh: pp_deep_verify_mode: 'deep_verify' found in
          [training], not [release], for project 'fixtureproj' in <path> —
          only [release] deep_verify is honored; defaulting to 'weekly'.
          Move the key under [release] to activate it.
pp_deep_verify_offhours_window fixtureproj   -> stdout "03:00-03:59", exit 0
  stderr: (the same warning, naming deep_verify_offhours_window and its section)
```

The warning names the section found, `[release]`, the default applied, and the
remedy. stdout carries the default alone, and the exit code is 0 — a warning,
not a refusal, exactly as the bug required and as the census constraint
demands.

Negative cases, all verified to emit **no** warning (this is the constraint
"warn only for a key genuinely absent from `[release]`"):

| Fixture | Config shape | stdout | stderr |
|---|---|---|---|
| `both` | key under `[release]` **and** `[training]` | `off` | empty |
| `absent` | key in no section at all | `weekly` | empty |
| `commented` | `# deep_verify = weekly` / `; deep_verify = off` under `[training]` | `weekly` | empty |
| live project | key correctly under `[release]` | `off` | empty |

Two further cases warn, and both are correct: `eoftrail` reproduces the
original bug's exact shape (a headerless `deep_verify = off` after two blank
lines at EOF) and warns; `blankrelease` (`deep_verify =` blank under
`[release]`, real value under `[training]`) warns, which is right — the blank
means the operator's value is still not in force.

The `_pp_find_key_section` helper is advisory only and never changes which
section a value resolves from; `read_ini`'s `[release]`-only read stays
authoritative, and `team/scripts/lib/ini_parser.sh` is untouched.

### Finding 3: scheduled deep-verify now skips this project at no cost

**Category:** pass

```
$ bash team/scripts/deep-verify.sh --scheduled
deep-verify: project=pgai-agent-kanban scheduled run skipped (deep_verify=off)
exit 0
```

The ledger `projects/pgai-agent-kanban/deep-verify-ledger.md` is byte-identical
before and after (md5 `0a9f07d7…` both sides), and no container build process
was started. This is the observable payoff of part 1: the ~13-minute weekly
sweep is no longer being paid against the operator's recorded intent.

### Finding 4: `deep-verify.sh` stdout/stderr split preserves both the warning and the refusal

**Category:** pass

This is the guard the RC added alongside the warning, and it is the one most
worth triggering rather than reading. The `--scheduled` path previously
captured `pp_deep_verify_mode` with `2>&1`; with the new warning that would
fold warning prose into `_dv_mode` for any project with a misplaced key.
Verified end to end against a synthetic registered project:

- **Misplaced key, success path.** stdout carried only the narration
  (`deep-verify: project=synthproj scheduled run not due (outside off-hours
  window …)`) — proving `_dv_mode` resolved cleanly to `weekly` and was not
  corrupted — while the resolver's warning was relayed verbatim on stderr.
  Exit 0.
- **Invalid value (`deep_verify = maybe`), failure path.** The dual-channel
  refusal survived the refactor intact, with the resolver's full diagnostic
  text preserved in the `message` field:
  ```
  error:   deep_verify.invalid_schedule_mode
  message: project 'synthproj' has an unrecognized project.cfg [release]
           deep_verify value: … (expected 'weekly' or 'off'; blank/absent
           defaults to 'weekly')
  remedy:  Set project.cfg [release] deep_verify to 'weekly' or 'off' …
  ```
  Exit 1.
- **No temp-file leak.** `find /tmp/pgai_kanban_tmp -name '*deep_verify_mode_stderr*'`
  returns nothing after both runs; the `pgai_mktemp` file is removed on both
  branches.

`team/scripts/lib/temp.sh` is sourced at `deep-verify.sh:150`, well before the
`--scheduled` block, so `pgai_mktemp` is always in scope — and the live
`--scheduled` run in Finding 3 exercised that path for real.

### Finding 5: the new unit tests fail when the change is reverted

**Category:** pass

Seven tests were added to `team/tests/unit/test_project_paths_bash.py`
covering present-in-`[release]`, absent-everywhere, misplaced-elsewhere and
release-wins-even-if-duplicated, for both keys. To confirm they are not
vacuous, `_pp_find_key_section` was neutralised in a scratch copy of the
resolver (early `return 0`) and the acceptance tests re-run:

```
FAILED …::test_deep_verify_offhours_window_misplaced_section_warns_and_defaults
FAILED …::test_deep_verify_mode_misplaced_section_warns_and_defaults_weekly
2 failed, 38 deselected
```

The worktree was restored immediately (`git status --short` clean) and the same
selection re-run green: `7 passed, 33 deselected`.

### Finding 6: documentation matches the shipped code

**Category:** pass

`team/SOP.md` ("The per-project switch") and `docs/operator-commands.md` ("The
scheduled trigger's per-project switch") were read in full against the code.
Both state that the two keys are read from `[release]` and no other section,
that an INI key belongs to the nearest header above it, that the resolver warns
on stderr naming the found section / `[release]` / the default / the remedy,
and that the signal is advisory — nothing refuses, nothing exits non-zero, no
new blocking site. Every one of those claims is true of the code as merged.
Neither document claims a Python-side warning, which is correct: `resolve_mode()`
in `team/pgai_agent_kanban/deep_verify/schedule.py` receives an
already-extracted string and cannot see sections. PM anticipated this in the
ticket notes and CODER recorded the decision, so it is a documented scope
choice, not a parity gap.

### Finding 7: constraint negative checks

**Category:** pass

- No new refusal site: the diff adds no `CENSUS-ANCHOR` marker and no Locator
  Index entry, matching the "warning, not refusal" constraint.
- `python3 team/scripts/lint_gate_census_compliance.py` → exit 0, 32 files
  checked, 0 offenders, 85 anchored sites reconciled.
- `bash -n` clean on `project_paths.sh` and `deep-verify.sh`; `py_compile`
  clean on the new test module. `set -euo pipefail` preserved at
  `deep-verify.sh:111`.
- `team/scripts/lib/ini_parser.sh` untouched, as required.
- Skip-cites-real-bug grep-gate (Step 6.13): no `BUG-SKIP` or non-numeric
  placeholder skip IDs anywhere under `team/tests/`; the only textual matches
  are archived runner logs under `team/tests/artifacts/`, not skip annotations.
  No `MISSING:` bug file.

### Finding 8: release-state drift check

**Category:** pass

`pp_last_released_version pgai-agent-kanban` → `v2.9.22`; `semver_gt v2.9.23
v2.9.22` holds. `release-state.md` reads `Active RC: v2.9.23`, opened
2026-09-07T09:41:17+00:00 by `CM-20260907-022-open-rc-2-9-23`, `Last Released:
v2.9.22` (well-formed `vX.Y.Z`). The `ai_rc/v2.9.23` branch exists and matches
the worktree HEAD. No drift.

### Finding 9: release-lifecycle simulation

**Category:** pass

Trigger condition met — the diff touches `team/scripts/lib/project_paths.sh`
and `team/scripts/deep-verify.sh`.
`python3 -m pytest tests/integration/test_cm_release_synthetic.py` → 15 passed.

### Finding 10: prerequisite dependency graph

**Category:** pass

`CM-022-open-rc → CODER-020 → WRITER-021 → TESTER-023 → CM-025-release`.
Acyclic, every referenced task ID exists, lifecycle bookends correct (CM-open
first, TESTER then CM-release last), sequence numbers non-colliding. The gap at
`024` is the absent HUMAN-APPROVE slot for an `auto`-approval bundle, not a
dangling reference. All prerequisites are DONE.

### Finding 11: internal ticket IDs left in the new test docstrings

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (hygiene only — no behavior wrong, no test weakened, no workflow impaired)
**Regression:** yes (introduced by this RC, commit `c19e84a`)
**Integrity Damage:** no
**Filed As:** BUG-0172 (Path C-A — filed as BUG — code defect / regression / test failure)

Three `BUG-0171` citations ship in `team/tests/unit/test_project_paths_bash.py`
— line 354 (section comment) and lines 394 and 462 (the two
acceptance-criterion docstrings). `team/SOP.md` "Test Authoring Guidelines"
(anti-pattern 2, "When acceptable") forbids this without qualification:
"Never — in names, docstrings, OR comments." The one carve-out is a skip
annotation citing an open follow-up bug; these are not skip annotations.

The RC's author did make a provenance cleanup pass — commit `0fc2d3c`,
"Reword provenance references out of comments" — and the production files it
covered are clean at HEAD (a grep for bug- and task-shaped IDs returns nothing
in `project_paths.sh` or `deep-verify.sh`). The pass simply did not reach the
test module, and nothing in the gate chain would have told it to:
`lint_comment_provenance.py` scans `team/scripts`, `team/scripts/lib`,
`team/scripts/cm`, `team/pgai_agent_kanban` and `release-notes`, but not
`team/tests`; and `lint_test_anti_patterns.py`'s anti-pattern 6 matches test
function *names* only, never docstrings or comments. Both runners therefore
exit 0 with the violation present. The bug asks for the reword and offers the
detection widening as optional, scope-flagged hardening.

### Finding 12: shipped v2.9.22 release notes carry a garbled duplicated change list

**Category:** pre-existing-bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (human-readable output only; verdicts, ledger records and duplicate detection are all correct)
**Regression:** no — predates this RC. `release-notes/v2.9.22.md` was written on `ai_main` during the v2.9.22 release (commits `15a8471` / `d87eb1a`) and is untouched by this RC's five-file diff.
**Integrity Damage:** no
**Filed As:** cites existing **BUG-0168** (open) — not re-filed

Spot-check (Step 6.11, "regressions of prior gaps"). `release-notes/v2.9.22.md`
line 27 ships a single `## Other Changes` bullet repeating
`docker/claude-abliterated/Dockerfile` twelve times and
`docker/rocky9/Dockerfile` six times, with a stray leading `- -`.

Traced to source: the release-notes generator reads a bundled bug's substance
through `get_bug_substance` in `team/scripts/lib/cm/release_notes.sh`, and
BUG-0167's `## Files Involved` (lines 82-99) is itself 18 duplicated lines —
six repetitions per distinct engine/flavor pair. That 18-fold duplication is
precisely the defect BUG-0168 describes and is still open for. BUG-0168
enumerates three operator-facing surfaces the duplicated string reaches; the
shipped release notes are a fourth it does not name. Cited rather than
re-filed: a second bug would split one fix across two tickets and misstate how
many defects exist.

### Finding 13: autonomous operation criterion

**Category:** pass

See the dedicated section below. All four checks answer NO.

## Lint Findings (non-blocking)

Both findings files were ingested (`${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-unit.json`
and `findings-integration.json`). Both contain exactly one finding, and it is
the same finding in both — one lint class, one instance, one bug.

- `comment_provenance` — `release-notes/v2.9.22.md:31` carries the task-ID
  reference `TESTER-20260907-017`; shipped prose must describe behavior, not
  internal IDs — **filed BUG-0173 (Path C-A — filed as BUG)**.

No open or running bug covered it. BUG-0165 filed the identical class against
`release-notes/v2.9.20.md:33` and is `done` — closed in v2.9.21 by rewriting
that one line while leaving the producers untouched, which is exactly why the
class returned one release later. BUG-0173 is therefore scoped at the two
producers that can emit a task ID (the generator's NON-FUNCTIONAL branch in
`team/scripts/lib/cm/release_notes.sh`, currently latent; and CM's
post-generation polish step, commit `d87eb1a`, which produced this instance),
not only at the offending line.

Per Step 5c this finding is recorded here and does **not** elevate
`## Recommendation` or `## Systemic Risk` on its own. The recommendation is
SHIP-WITH-CONCERNS on the strength of Finding 11, which is a Step-7 defect
finding, not a lint signal.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=0d freshness=stale — three engine/flavor pairs failed to build cold against the v2.9.20 tree and the lane filed BUG-0167 for them (since fixed and shipped in v2.9.22); the record is inside the 7-day window but names v2.9.20, not the v2.9.23 under verification, so nothing has been built cold against this tree.

The lane was not run during this verification, by design — TESTER runs no
container build and starts no container engine. Note that this project's
scheduled trigger is now genuinely `off` (Findings 1 and 3), which is the
intended state and also means the ledger will not refresh itself weekly; a
green record for a promotion must come from the on-demand verb
(`team/scripts/deep-verify.sh --project pgai-agent-kanban`) or the pre-release
freshness check's remedy. This is reported so the rating is made knowing it; it
does not move the recommendation or the systemic risk.

## Autonomous criterion

Manual interventions required: none.

1. Tasks force-promoted from WAITING to BACKLOG by hand? **NO.** Every v2.9.23
   task's `status.md` shows the normal wake-script promotion; queue markers are
   consistent with each task's state (`[x]` for the four DONE, `[A]` for this
   task, `[W]` for the waiting CM release task).
2. `release-state.md` edited outside the CM scripts? **NO.** Its contents match
   what `cm-open-rc.sh` wrote, and `CM-20260907-022`'s status records the
   script exiting 0 and writing those exact values.
3. Scripts patched mid-build? **NO.** The RC's five commits are the two feature
   merges and their three authored commits, all inside the RC window
   (09:47-10:13 UTC on 2026-09-07), all by the chain author. No tooling commit
   landed on `ai_main` while the RC was in flight.
4. Queue files manually edited? **NO.** All seven backlog files carry
   well-formed entries with markers matching task state; no orphaned or
   hand-flipped marker found.

## Caveats

- **Part 1 of the fix is deliberately not in the RC diff.** The relocated
  `deep_verify = off` key lives in live kanban process state
  (`$HOME/pgai_agent_kanban/projects/pgai-agent-kanban/project.cfg`),
  which has no copy in the dev tree. Its absence from the release squash is
  not evidence of an incomplete ticket — it was verified directly by running
  the resolver (Finding 1). PM flagged this in the decomposition and it is
  restated here so CM does not read the five-file diff as the whole delivery.
- **Skip-count difference from CODER's run.** CODER recorded 3174 passed / 12
  skipped for the unit suite; this run recorded 3183 passed / 3 skipped. The
  total is 3186 either way — nine environment-conditional tests that skipped in
  CODER's session executed and passed in this one. No test disappeared; not a
  finding.
- **Minor duplication in the warning strings (observation, not a finding).**
  Each resolver's warning message hardcodes its own default (`'weekly'`,
  `'03:00-03:59'`) as a literal, duplicating the value the fallback below it
  echoes. Two literals per function that must be changed together if a default
  ever moves. Arguable rather than wrong — recorded as an observation only.
- **Prior-run artifacts.** The artifacts directory was empty at task start
  (fresh-start conditions); no `.previous-RUN-N` file exists and none was
  consulted. Every result in this report comes from commands run in this
  session.
- Scratch fixtures for this verification were written under
  `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester-scratch/`, never bare
  `/tmp`. The RC worktree was left clean (`git status --short` empty).
