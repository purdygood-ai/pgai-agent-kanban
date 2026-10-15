# Verification Report: rc/v2.31.0

## Summary

I verified RC `ai_rc/v2.31.0` (HEAD `ae488ce2`, seven task merges on top of `ai_main` `b081aa0e`) against `requirements/v2.31.0-ship-posture-always.md`, which adds the per-project ship posture (`matrix|always`), the remote-lane guard, the Auto-Ship Event record, `cm/release.sh --decide-only`, and posture visibility in `kanban-status.sh` and `GET /status`.

The feature works as specified. I tested it end to end on sandbox kanban roots with the real v2.29.0 and v2.30.0 TESTER reports, as the requirements' "Notes for TESTER" ask:
- Under `matrix` and with the key absent, the release script's output is identical to `ai_main` on every fixture.
- Under `always`, triggers 2 and 8 ship and leave the specified records. Trigger 3 is covered by the RC's own integration tests, which passed.
- Triggers 1 and the unrecognized-state row still HALT.
- `--decide-only` writes nothing, and its exit code matches the full run every time.
- The integration runner is green.

**The unit runner is red** (`UNIT_EXIT=1`, 6 failed / 9908 passed). All six failures are regressions from this RC, from three defects in new code; the same test files pass on `ai_main`. I found one more low-severity defect by hand. All four are filed via Path C (autonomous follow-up filing — non-blocking), and none of them affects the live release path. The recommendation is SHIP-WITH-CONCERNS.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Findings

### Finding 1: New auto-ship notes module crashes when run by file path (2 unit failures)

**Category:** bug
**Fix Effort:** small (1 CODER task: add the `if not __package__:` self-location guard)
**Systemic Risk:** medium (a recurrence of the BUG-0533 / BUG-0561 class; the contract test caught it, but CODER `--files` runs do not include that test)
**Filed As:** BUG-0575 (Path C-A, filed as BUG — code defect / regression / test failure)
**Filed By:** TESTER (autonomous Path C) — the new release-notes helper that writes the auto-ship line crashes with "module not found" when started by its file path, so two unit tests fail and the gated unit runner is red.

`team/pgai_agent_kanban/cm/auto_ship_notes.py` (new, CODER-027) has a `__main__` block and a module-level `from pgai_agent_kanban.lib.atomic_write import atomic_write` (line 58), and no self-location guard. `env -u PYTHONPATH python3 .../auto_ship_notes.py --help` fails with `ModuleNotFoundError: No module named 'pgai_agent_kanban'`. Failing in the gated unit run:
- `tests/unit/test_package_modules_run_by_path.py::test_help_by_file_path_runs_without_pythonpath[pgai_agent_kanban.cm.auto_ship_notes]`
- `tests/unit/test_package_modules_run_by_path.py::test_file_load_binds_the_package_of_its_own_tree[pgai_agent_kanban.cm.auto_ship_notes]`

The file passes in full on `ai_main`. The release path is unaffected, because it reaches the module through `pp_run_ops` (`python3 -m`). The sibling new module `cm/stop_bug_files.py` runs by path fine.

### Finding 2: Ship-posture remedy text contains a bare `python3 -m pgai_agent_kanban` (1 unit failure)

**Category:** bug
**Fix Effort:** small (1 CODER task)
**Systemic Risk:** low (one remedy string)
**Filed As:** BUG-0576 (Path C-A)
**Filed By:** TESTER (autonomous Path C) — the ship-posture stage's hand-recovery message spells out a bare "python3 -m pgai_agent_kanban" command, which the repository-wide rule against bare package invocations forbids, so a unit test fails.

`team/scripts/lib/cm/release_ship_posture.sh:312` (the WARNING remedy in `cm_ship_posture_stamp_notes`) trips `tests/unit/test_pp_run_ops_sweep.py::test_no_bare_python3_module_invocations_outside_exemptions`. The test passes on `ai_main`.

### Finding 3: release_finalize.sh calls a ship-posture function it does not declare (3 unit failures)

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks: the Dependencies header plus the standalone harnesses, or a tolerant call)
**Systemic Risk:** low (the production `cm/release.sh` sources the posture stage; only standalone drivers of the stage break)
**Filed As:** BUG-0577 (Path C-A)
**Filed By:** TESTER (autonomous Path C) — the release finalization stage now calls a ship-posture function it does not list as a dependency, so any caller that loads the stage as documented dies with "command not found" after the tag has already been made, and three unit tests fail.

`release_finalize.sh:498` calls `cm_release_append_auto_ship_events`, which `release_ship_posture.sh` defines (CODER-026). The stage's Dependencies header (lines 72-92) does not list it. `tests/unit/test_release_summary_origin_actions.py` drives the stage on its own, as the header documents, and dies at Step 12 with exit 127 (`command not found`) after tagging. All three of its tests fail:
- `TestLocalOnlyReleaseSummary::test_summary_claims_no_origin_push_or_delete_and_says_local_only`
- `TestLocalOnlyReleaseSummary::test_origin_is_untouched_by_a_local_only_release`
- `TestRemoteBackedReleaseSummary::test_summary_reports_tag_pushed_and_rc_deleted_from_origin`

The file passes on `ai_main`. This is not a stale-assertion: no literal changed. The production stage acquired an undeclared dependency.

### Finding 4: --decide-only override refusal's remedy hardcodes the project name

**Category:** bug
**Fix Effort:** small (1 CODER task)
**Systemic Risk:** low (one remedy string; the refusal itself is correct and writes nothing)
**Filed As:** BUG-0578 (Path C-A)
**Filed By:** TESTER (autonomous Path C) — when the release decider refuses an override flag, its suggested fix always names the kanban's own project instead of the project the operator asked about, so following it checks the wrong project.

`team/scripts/cm/release.sh:281`. Reproduced: `release.sh --project other-proj --decide-only --force --reason x` prints the remedy `Run the decider alone: release.sh --project pgai-agent-kanban --decide-only` and exits 1 with nothing written. WRITER-030 flagged this in its status; I confirmed it by running it.

### Finding 5: Config key, resolvers and remote-lane guard (Deliverables 1-2)

**Category:** pass

- `kanban.cfg_example` ships `ship_posture = matrix` and a commented `ship_posture_confirm_remote`, with documentation. `project.cfg_example` carries the per-project override.
- Both keys are registered in `config_loader.sh` `_CONFIG_LOADER_KNOWN_KEYS`.
- The bash resolver `pp_ship_posture` (project_paths.sh) and the Python resolver `resolve_ship_posture` (resolution_matrix.py, beside `resolve_max_rc_retries`) gave identical answers and identical warning text on 9 sandbox cases plus kanban.cfg inheritance:
  - Unrecognized values (`alwasy`, `Always`) resolve to matrix with one warning line naming the file and the raw value.
  - `push_to_remote = true` plus `always` without confirm resolves to matrix with one warning. Confirm `false` does the same. Confirm `true` gives `always`.
  - Absent or `matrix` gives matrix with no warning.

### Finding 6: Posture in the release script (Deliverable 3) — matrix unchanged; always ships 2/3/8 only

**Category:** pass

I drove the real `cm/open-rc.sh` and `cm/release.sh` from the `ai_main` clone and from the RC, on sandbox lanes (push_to_remote=false). The fixtures were the real v2.29.0 and v2.30.0 TESTER reports (with their real filed-bug files copied), PASS, trigger 8 (three NON-FUNCTIONAL notes), TESTER BLOCKED and TESTER WORKING.

- **matrix / absent:** RC equals ai_main on exit code, HALT key and decision. Normalized stdout and stderr show 0 differing lines on all 12 fixture × posture runs; the raw diffs are only sandbox paths and SHAs.
- **always, v2.29.0 and v2.30.0 reports (trigger 2):**
  - exit 0, `SHIP-NON-FUNCTIONAL`, no HALT file;
  - one `## Auto-Ship Event` with Timestamp, Version, `Trigger: 2`, `Error Key: cm-release.trigger-2.systemic-risk-high`, `Ship Posture: always` and the TESTER task;
  - the release notes' `## Known Issues` opens with `Auto-shipped under ship_posture=always (would have halted: trigger 2 — cm-release.trigger-2.systemic-risk-high)`, then the non-functional warning naming the high-risk finding's bug by substance.
- **always, trigger 8:** exactly one pattern bug filed (in the sandbox), the event carries `Filed Bug`, ships FUNCTIONAL with the trigger-8 line.
- **always, trigger 8 + trigger 2 together (6.11 spot-check):** two events in firing order, two notes lines, decide-only `trigger=8,2`.
- **always, trigger 1 and the unrecognized state:** HALT with the same keys as matrix, and no event.
- Trigger 3, the mechanical triggers 4/5/9/10/11 and the no-row-matched row under always are covered by `tests/integration/test_cm_release_ship_posture.py` and `test_cm_release_decide_only.py` (48 collected). Both are inside the green integration run.
- `read_state_field.py --validate` exits 0 on release-state.md files carrying one and two Auto-Ship Event blocks.
- The `write_rc_state.py auto-ship-event` guards fire (exit 2, nothing written) for trigger 0, a multi-line value, and a missing file.

### Finding 7: --decide-only (Deliverable 4)

**Category:** pass

On 18 sandbox lanes (6 fixtures × 3 postures), `--decide-only` left the kanban-root file hashes, git refs, HEAD and tree, symbolic ref and `git status` byte-identical. It printed exactly the `ship-policy: decision=… posture=… trigger=… notes_status=…` line, followed on HALT by `error:` (the HALT key), `message:` and `remedy:`. It exited 3 on every lane where the full run HALTed and 0 on every lane where it shipped, with the same decision and notes status. The override flags are refused (see Finding 4 for the remedy text). Census anchors `release-decide-only-override-refused-01` and `release-decide-only-halt-01` are in the Locator Index as BLOCK-WORTHY.

### Finding 8: Visibility (Deliverable 5)

**Category:** pass

- `kanban-status.sh --project <p>` prints `Ship posture: always|matrix`.
- `GET /status` (FastAPI TestClient on the sandbox root) returns `ship_posture` with the same value for the local-always, remote-unconfirmed and absent projects. The guard warning appears on stderr in both surfaces for remote-unconfirmed.
- Both read `ops.read.get_status` → `resolve_ship_posture`, so they share one function.
- The ICD moved to 1.16.0 with a frozen baseline.
- The release-notes line is verified in Finding 6.

### Finding 9: OVERWATCH (Deliverable 6)

**Category:** pass

`resolution-matrix.yaml` is unchanged (no new rows). `tests/unit/test_ship_policy_halt_row_on_always_lane.py` asserts that an always lane leaves no HALT file for the `ship-policy-halt` row on triggers 2/3/8, and that the yaml declares exactly one row. It passed in the gated unit run.

### Finding 10: Out-of-Scope constraint

**Category:** pass

No `roles/`, `SOP-core.md`, `DIRECTIVES.md`, `OVERVIEW.md` or subagent prompt file is in the diff.

### Finding 11: Gated suites (acceptance: "TESTER certifies the full suites once, through the parallel unit lane")

**Category:** bug (Findings 1-3 carry the filings)

- `UNIT_EXIT=1`: 6 failed, 9908 passed, 41 skipped. The parallel lane collected (gw0/gw1), so the BUG-0558 collection abort is gone.
- `INTEGRATION_EXIT=0`: 901 passed, 1 skipped.
- The six unit failures are exactly Findings 1 (2), 2 (1) and 3 (3). Direct pytest (a debugging aid, not PASS evidence) of the same three files on the `ai_main` clone gives 138 passed.

### Finding 12: Documentation sufficiency

**Category:** pass-with-caveat

The ship-posture keys are documented in `kanban.cfg_example`, `project.cfg_example`, `docs/OPERATIONS.md`, `docs/operator-commands.md`, `docs/cm-release-reference.md`, `release.sh --help` and the census rows. That satisfies the Documentation Discipline rule's matching artifacts. Two caveats, neither filed:
1. `HOW_TO.md` §1.9 "Keys in kanban.cfg that the loader's registry does not declare" lists `max_rc_retries` and `deep_verify_offhours_window` but not `ship_posture` / `ship_posture_confirm_remote`, which have the same shape.
2. `docs/README.md:31` still calls `cm-release-reference.md` "nine passages". It was already stale before this RC and now has eleven.

Both were noted by WRITER-030.

## Lint Findings (non-blocking)

- `python_duplicates` — two stale allowlist entries (`team/config/python-duplicates-allowlist.txt` lines 16 and 19, `_make_envelope` in `api/reads.py` and `api/routers/operations.py`). They stopped matching once CODER-029 changed `reads.py::_make_envelope`. Reported identically in `findings-unit.json` and `findings-integration.json`. No open bug matched — filed BUG-0579 (Path C-A, quality-class aggregate, low).

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=29d freshness=stale — The only ledger record is a red run from v2.9.20, 29 days old: it is outside the 7-day window and not this RC's version, so nothing is known about whether this tree's images build. That run's failure was filed as BUG-0167 (claude-abliterated FROM parse error; now done), which is cited here, not re-filed.

## Coverage (informational)

Coverage unavailable for both suites: each runner printed `coverage disabled by config ([testing] coverage)`. The JSON under `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/` dates from 2026-10-04 and belongs to an earlier run, so I did not read it.

## Autonomous criterion

1. Tasks force-promoted WAITING → BACKLOG by hand: **NO**. The cycle tasks' status trails show wake-script promotion only.
2. `release-state.md` edited outside the CM scripts: **NO**. Its mtime (2026-10-06 07:17:07 UTC) equals `## RC Opened At`, the moment `cm-open-rc` wrote it.
3. Scripts patched mid-build: **NO**. Every first-parent commit on `ai_main..ai_rc/v2.31.0` is a task merge, with no stray non-merge commits.
4. Queue files edited by hand: **NO**. No evidence; every cycle task's marker matches its `## State`.
5. Remote git operation by a non-CM agent: **NO** for the build. No `git-boundary-guard.jsonl` record exists in any cycle task (PM-004, CM-031, CODER-024..029, WRITER-030, CM-034). Self-report: this TESTER task has one record (2026-10-06T10:36:09Z). In my first sandbox harness attempt, the synthetic-repo fixture helper tried to push its initial commit into a throwaway bare repository under my scratch directory. That directory sat outside the sandbox's `PGAI_AGENT_KANBAN_TEMP_DIR`, so the guard refused the push. No real remote was involved and nothing left the host. I re-ran with the fixtures under the sandbox temp root, the guard's documented fixture-local exemption, and did not impersonate a role.

Autonomous operation criterion: Manual interventions required: none.

## Caveats

- Ship policy for this very release: the project's live `project.cfg` has no `ship_posture` key, so CM-034 runs under `matrix`. With `SHIP-WITH-CONCERNS` / medium, the matrix row is SHIP-KNOWN-BUGS.
- The requirement says "Targeted tests per touched file pass with `--files`". The CODER statuses report that, and I did not repeat it. The full runs above supersede it, and the three failing files were not among any CODER's `--files` lists.
- Findings 1 and 3 share a pattern: the full unit suite was not run before the merge. The v2.30.0 cycle had the same gap (BUG-0558 context). I noted it here rather than filing a priority, because nothing in this requirement asks for a process change.
- I did not commit to the dev tree. Artifacts live in the task folder, as the wake prompt instructed: no feature branch, detached worktree.
- deep-verify: the lane is operator-only and was not run; the freshness line above is read from the ledger.

## Verification log (checkpoints written during the run)


- Step 1: fresh start (artifacts/ empty; no prior report to rotate).
- Step 2 pre-flight: PASS. 626 DONE tasks name a feature branch; none exist locally (normal post-merge shape), so Check 1 / Check 2 pass trivially. The seven cycle tasks (CODER-024..029, WRITER-030) each have a merge commit on the RC.
- Step 3 working tree: PASS. `git rev-parse HEAD` = `git rev-parse ai_rc/v2.31.0` = ae488ce24e67155e4e94496313065a29442dbb43; `git status --porcelain` empty; `git stash list` empty.
- Step 5a: gated suites started (unit then integration, --coverage).
- Step 4: diff ai_main..ai_rc/v2.31.0 = 54 files, +9167/-275. Deliverable map: config (kanban.cfg_example, project.cfg_example, project_paths.sh pp_ship_posture, resolution_matrix.py resolve_ship_posture, config_loader.sh registry); posture (lib/cm/release_ship_posture.sh new, release_ship_policy.sh 4c/4d); event writer (cm/write_rc_state.py auto-ship-event); decider (lib/cm/release_decide_only.sh new, release.sh, release_preflight.sh); visibility (cm/auto_ship_notes.py, release_notes.sh, ops/read.py, api/reads.py, kanban-status.sh, ICD 1.16.0); OVERWATCH test; docs.
- Step 6.5 independent sandbox harness (scratch, real v2.29.0 and v2.30.0 TESTER reports copied verbatim with their real filed bug files; synthetic pass / trigger-8 / BLOCKED / WORKING lanes). Drove real cm/open-rc.sh + cm/release.sh from BOTH ai_main (clone at b081aa0e) and the RC under posture absent / matrix (+ always on RC), and `--decide-only` on an identical lane before a full run:
  - matrix & absent: RC == ai_main on exit code, HALT key, decision and normalized stdout+stderr (0 differing lines after normalizing sandbox paths/SHAs/timestamps) for all 6 fixtures x 2 postures. 
  - always: real v2.29.0 and v2.30.0 reports -> exit 0, SHIP-NON-FUNCTIONAL, no HALT file, one `## Auto-Ship Event` (trigger 2, cm-release.trigger-2.systemic-risk-high, Ship Posture always, TESTER task), notes `## Known Issues` opens with `Auto-shipped under ship_posture=always (would have halted: trigger 2 — cm-release.trigger-2.systemic-risk-high)` plus the non-functional warning naming the high-risk bug by substance.
  - always trigger 8: one pattern bug filed (BUG-0001-last-three-releases-non-functional.md in sandbox), event carries `Filed Bug: BUG-0001`, ships FUNCTIONAL with the trigger-8 auto-ship line.
  - always trigger 1 (BLOCKED) and unrecognized state (WORKING): still HALT with the same keys.
  - `--decide-only`: every run left kanban root file hashes, git refs, HEAD/tree, symbolic ref and status unchanged; exit 3 on every full-run HALT and 0 on every full-run ship; decision line matches the full run.
- Resolvers (bash pp_ship_posture vs Python resolve_ship_posture, sandbox root, 9 cases + kanban.cfg inheritance): identical results and identical warning text after prefix. local always -> always; push_to_remote=true + always + no confirm -> matrix + 1 warning; confirm=true -> always; confirm=false -> matrix + 1 warning; `alwasy` / `Always` -> matrix + 1 warning naming file and raw value; absent / matrix -> matrix, no warning; kanban.cfg always inherited by a local project -> always, by a remote project w/o confirm -> matrix + warning naming kanban.cfg as the confirm target.
- release-state.md: `read_state_field.py --validate` exits 0 with one and with two appended `## Auto-Ship Event` blocks. Writer guards fire: trigger 0 refused, multi-line value refused, missing file refused (exit 2, nothing created).
- Visibility: `kanban-status.sh --project <p>` prints `Ship posture: always|matrix`; `GET /status` (FastAPI TestClient on the sandbox root) returns `ship_posture` with the same value for local-always / remote-unconfirmed / absent, warning on stderr for remote-unconfirmed in both. Both come from ops.read.get_status -> resolve_ship_posture.
- Census: release-decide-only-override-refused-01 and release-decide-only-halt-01 present in docs/blocking-site-census.md Locator Index as BLOCK-WORTHY.
- Out of Scope constraint: no roles/, SOP-core.md, DIRECTIVES/OVERVIEW or subagent prompt files in the diff. resolution-matrix.yaml unchanged (no new OVERWATCH rows).
- Step 6.1: pp_last_released_version = v2.30.3; semver_gt v2.31.0 v2.30.3 true; live release-state.md `--validate` exit 0 (Active RC v2.31.0, Last Released v2.30.3 well-formed).
- Step 6.10: graph OK. CM-031 open-rc (no prereqs) -> CODER-024..029 / WRITER-030 (each names CM-031) -> TESTER-032 (names all seven) -> CM-034 release (WAITING; names all seven + TESTER-032). No cycles; every ID resolves.
- Step 6.13: no new skip/xfail annotations in the diff; `BUG-SKIP` hits are pre-existing lint-test data strings, not annotations.
- CANDIDATE FINDING (A): new `team/pgai_agent_kanban/cm/auto_ship_notes.py` crashes when run by file path (`python3 .../auto_ship_notes.py --help` -> ModuleNotFoundError: pgai_agent_kanban; no `if not __package__:` self-location guard). Direct pytest (debugging aid) on tests/unit/test_package_modules_run_by_path.py -k auto_ship_notes: 2 failed. Expect gated unit runner non-zero.
- CANDIDATE FINDING (B): `cm-release.decide-only.override-flag-refused` remedy hardcodes `--project pgai-agent-kanban` (team/scripts/cm/release.sh:281). Reproduced: `release.sh --project other-proj --decide-only --force --reason x` -> remedy `Run the decider alone: release.sh --project pgai-agent-kanban --decide-only`; exit 1, nothing written.
