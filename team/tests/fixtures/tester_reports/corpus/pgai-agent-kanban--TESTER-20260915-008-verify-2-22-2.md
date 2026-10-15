# Verification Report: ai_rc/v2.22.2

**Task:** TESTER-20260915-008-verify-2-22-2
**Agent:** tester | **Model:** claude-fable-5-1
**RC branch:** `ai_rc/v2.22.2` @ `f926ecd98179ee347007dd6648857fd2bea35711`
**Requirements doc:** `projects/pgai-agent-kanban/requirements/v2.22.2-bugfix-bundle-20260915.md`
**Bundled item:** BUG-0276-doc-truth-residual-findings.md
**Worktree:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260915-008-verify-2-22-2` (detached HEAD, clean, no stashes)

## Summary

v2.22.2 is a single-bug bundle: close the eighteen documentation-versus-tree
disagreements BUG-0276 recorded when the doc-truth lint was first wired into the
unit gate. All eighteen are closed on this RC, verified by reading the lint's own
output inside the gated runner and by parsing the runner's machine-readable
findings file. Five tickets merged cleanly (002 cleanup `--help`, 003
process-internal HELP FAILS exempt-list, 004 OPERATIONS.md config-key
corrections, 005 cfg_example key coverage, 006 whole-tree zero sweep — the last
legitimately contributing zero commits, which its own README names as the
expected outcome). Both gated runners exit 0. Behavioral verification of the new
`--help` guard passes in a fully sandboxed root, including the negative cases.

Two defects were found outside the bundle's scope, both in
`team/scripts/cleanup/cleanup.sh` — the file this RC modified — and both
pre-existing rather than introduced here.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

high

The `high` comes from one finding (Finding 6) whose `Regression` is explicitly
`no`, whose `Integrity Damage` is explicitly `no`, and whose `Fix Effort` is
`small`. Per the ship-policy summary in `SOP.md`, that row ships as a deferred
known issue (`KNOWN-BUGS`), not a HALT. The rating is written honestly rather
than softened, per the Systemic-Risk Rubric's standing instruction.

## Test Suites (gated runners -- authoritative)

| Suite | Command | Exit | Result |
|---|---|---|---|
| Unit | `bash team/scripts/run-unit-tests.sh --coverage` | **0** | 4519 passed, 31 skipped, 0 failed (725.82s) |
| Integration | `bash team/scripts/run-integration-tests.sh --coverage` | **0** | 465 passed, 1 skipped, 0 failed (472.57s) |

Every pre-flight lint gate reported OK on the unit runner, including
`doc_command_truth`, `help_presence`, `test_anti_patterns`,
`skip_cites_real_bug`, `orphan_tests`, `api_parity`, `icd_freshness`,
`changelog_freshness`, and `gate_census_compliance`.

## Code Coverage

- Unit:        line 75%  |  branch 62%   (15806 stmts, 4744 branches; blended `percent_covered` 72%)
- Integration: line 7.4%  |  branch 5.8%   (15797 stmts, 4744 branches; blended `percent_covered` 7.1%)

Coverage is informational only and does not enter the recommendation.

## Findings

### Finding 1: BUG-0276 acceptance -- doc-truth lint reports zero findings

**Category:** pass

BUG-0276's first acceptance criterion is that
`python3 team/scripts/lint_doc_command_truth.py` reports zero HELP FAILS, zero
UNKNOWN CONFIG KEY, and zero CONFIG DEFAULT MISMATCH findings. Run inside the
gated unit runner against this RC's exact tree:

```
lint_doc_command_truth: scanned 640 citation(s) across 13 document(s); 0 finding(s) (3 process-internal citation(s) exempted from the --help check).
lint_doc_command_truth: key-parity: scanned 166 key citation(s) across 15 document(s) against 94 registry key(s); 0 finding(s) (0 loader key(s) no scanned document mentions).
lint_doc_command_truth: pane-parity: scanned 44 window/pane citation(s) across 13 document(s) against 15 census window id(s) (16 row(s)); 0 finding(s) (0 census window(s) no scanned document describes).
lint_doc_command_truth: OK -- 0 findings.
```

All four original groups are closed: 5 HELP FAILS -> 0 (2 fixed by a real
`--help` on `cleanup.sh`, 3 closed by two reasoned exempt-list entries), 1
UNKNOWN CONFIG KEY -> 0, 2 CONFIG DEFAULT MISMATCH -> 0, 10 UNDOCUMENTED CONFIG
KEY -> 0. The UNDOCUMENTED direction reaches zero outright, so the "or each
remaining key is covered by a recorded exclusion" alternative in the acceptance
is not exercised and is not owed.

### Finding 2: BUG-0276 acceptance -- gated runner still exits 0 with no doc_command_truth entry

**Category:** pass

`bash team/scripts/run-unit-tests.sh` exits 0, and
`${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-unit.json` (generated
2026-09-15T03:41:16+00:00 by this run) carries 26 findings, all of class
`hygiene` and none of class `doc_command_truth`. Verified by parsing the JSON,
not by reading prose.

### Finding 3: BUG-0276 acceptance -- the new `--help` names every argument and `lint_help_presence` still passes

**Category:** pass

`lint_help_presence: OK -- 116 script(s) checked, 1 exempt, 0 offenders.`
`cleanup.sh --help` names `--project`, `--dry-run`, `--trivial-only`,
`--temp-only`, and `--help` -- the complete set of flags the argument loop
accepts (coding-standards directive 6). Behavioral verification, run with both
`KANBAN_ROOT` and `PGAI_AGENT_KANBAN_ROOT_PATH` redirected into a throwaway
sandbox under the framework temp root:

- `cleanup.sh --help` -> exit 0; sandbox tree byte-identical before and after
  (md5 of the full recursive file manifest unchanged); usage on stdout, stderr
  empty.
- `cleanup.sh -h` -> exit 0, same usage.
- `cleanup.sh --bogus` -> exit 1, `ERROR: unknown argument: --bogus` (the
  pre-existing refusal, unchanged -- the original failure mode still fires).
- `cleanup.sh --help --bogus` -> exit 1. The `--help` short-circuit does not
  swallow a bad invocation into a usage print with exit 0.

Both halves of the Step 6.5 requirement are exercised: the new normal case and
the original failure mode.

### Finding 4: Documentation corrections match the Key Registry

**Category:** pass

Each corrected configuration statement was checked against the authoritative
registry rather than against the ticket text:

| Statement | Doc now says | `config_loader.sh` Key Registry |
|---|---|---|
| `[wake] max_tasks_per_wake` | default `5`, moved out of `[chain]` into its own `[wake]` block | line 116 `max_tasks_per_wake OPTIONAL 5`; line 445 reads `[wake]` |
| `[paths] dev_tree_path` | default `""`, no conventional default | line 37 `dev_tree_path OPTIONAL ""` |
| `[dashboard] max_rows_per_project` | `kanban.cfg_example` corrected 13 -> 8 | line 75 `max_rows_per_project OPTIONAL 8` |

Internal consistency of the prose was checked too: the sentence changed from
"three sections" to "four sections" and four `**[section]**` blocks now follow
it. `kanban.cfg_example` carries a real `[wake]` section at line 220 with
`max_tasks_per_wake = 5`, so the doc's new section name resolves to something
that exists.

The ten previously-undocumented `project.cfg` keys were each read back through
the production INI reader and match their registry defaults (`[hooks]` six keys
empty/`false`, `[project] temp_dir = tmp.my-project` consistent with the
template's own `project_name = my-project`, `[versioning]
max_priorities_per_run = 1`, `[training] reasoning_trace = false`,
`training_agents` empty).

The two exempt-list entries were read in full against the citations they close.
`docs/OPERATIONS.md:831` cites `wake_bootstrap.sh` inside a prose bullet
explaining what re-sources `shell-env`/`claude-env` on the wake path;
`docs/docker.md:281` cites `docker/entrypoint.sh` in a "what ships in docker/"
reference table and `:397` in a build-context note. None reads as a runnable
operator command line, so the process-internal classification is accurate and
the exemption does not blunt the check. The exempt-list loader is narrow by
construction (lookup runs strictly after resolution succeeds) and is covered by
five new unit tests including three negative cases.


### Finding 5: Both gated test suites green

**Category:** pass

`UNIT_EXIT == 0` and `INTEGRATION_EXIT == 0`, which is the suite-pass
precondition. Unit: 4519 passed, 31 skipped, 0 failed. Integration: 465 passed,
1 skipped, 0 failed. Both runners were invoked as the role file specifies —
`bash team/scripts/run-unit-tests.sh --coverage` and
`bash team/scripts/run-integration-tests.sh --coverage` from the worktree root,
so the anti-pattern lint pre-flight ran ahead of pytest in both. No direct
`python3 -m pytest` result is cited anywhere as PASS evidence.

The integration runner's `rc_empty_commits` gate reported OK, independently
confirming this RC carries no empty commits despite CODER-20260915-006
legitimately contributing zero.

`tests/integration/test_cm_release_synthetic.py` ran as part of the integration
suite (15 passed) even though Step 6.8's trigger condition is not met — see the
Caveats section.

### Finding 6: cleanup.sh ignores the sandbox override the role files instruct agents to use

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** high (a destructive path runs against the live install when the caller believes it is sandboxed; see the Systemic-Risk Rubric in team/SOP.md, "Data is at risk")
**Regression:** no (the `TEAM_ROOT` / `KANBAN_ROOT` split at cleanup.sh:75 and the `KANBAN_ROOT` requirement at project_paths.sh:202 are both untouched by this RC's diff — `git diff ai_main..ai_rc/v2.22.2 -- team/scripts/cleanup/cleanup.sh` adds only the `--help` branch)
**Integrity Damage:** no (the one observed live run deleted nothing: `logs_deleted: 0`, `task_folders_deleted: 0`; the 41 requirements were moved into `tasks/archive/`, not destroyed, and 599 purged temp items were kanban-owned ephemera)
**Filed As:** BUG-0279 (Path C-A — filed as BUG: code defect / regression / test failure)

`team/scripts/cleanup/cleanup.sh` reads two different root variables and never
reconciles them. `TEAM_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-...}"` (line 75)
governs only the Step 0 guard at line 543. `KANBAN_ROOT` — required by
`project_paths.sh:202`, with no `PGAI_AGENT_KANBAN_ROOT_PATH` fallback — governs
every destructive step: the Step 0 scan directory (line 367), Step 1 log
pruning, Step 2 terminal task-folder deletion, Step 3 requirements archival,
Step 5 debug-log rotation, and the summary log's own write path.

The Live-Install Safety procedure in `roles/TESTER.md` and the matching guidance
in `roles/CODER.md` name **only** `PGAI_AGENT_KANBAN_ROOT_PATH`. So an agent
following the documented sandbox instruction exactly is not sandboxed at all.

Reproduced here, deterministically, with `--dry-run` so nothing was destroyed:

- **Case A** (`PGAI_AGENT_KANBAN_ROOT_PATH` = throwaway, `KANBAN_ROOT` = live,
  exactly the shell state a wake firing leaves behind): the script reported
  `Step 1: prune old log files from $HOME/pgai_agent_kanban/logs/agents`,
  `Step 2: prune terminal task folders from …/tasks`,
  `Step 3: archive shipped requirements from …/requirements`, and wrote its
  summary log into the live `logs/agents/`. Exit 0.
- **Case B** (`KANBAN_ROOT` unset entirely): fails closed —
  `project_paths.sh: KANBAN_ROOT is not set`, exit 1. The script is safe when
  the variable is absent and unsafe only when it is present and disagrees.

This is not hypothetical, and it fired inside this very build. At
2026-09-15T01:56:45, while CODER-20260915-002 was manually testing the `--help`
flag this RC adds, a live non-dry-run cleanup ran against
`$HOME/pgai_agent_kanban`. Its log (`logs/agents/cleanup-20260915-015644.log`)
opens by naming the sandbox root — `projects/ directory not found under
/tmp/pgai_kanban_tmp/scratch-help-test-dummy` — and then archives 41
requirements, rotates 3 debug logs, and purges 599 temp items from the live
install. Nothing was lost, and the CODER disclosed it in its own status summary
rather than hiding it. But the mechanism that let a self-described sandboxed
invocation act on the operator's real install is the defect, and Step 2's
terminal task-folder deletion (default 7-day retention) is unrecoverable kanban
state reachable by the same path.

Rated `high` per the rubric's "Data is at risk — a destructive path that can run
when it should not." The rating is honest, not a HALT request: `Regression: no`,
`Integrity Damage: no`, and `Fix Effort: small` are all explicit, which is the
ship-policy row that ships this as a deferred known issue.

### Finding 7: cleanup.sh `--dry-run` reports a log line where the trivial-log count belongs

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (hygiene — a preview path prints an unreadable number; nothing is deleted incorrectly)
**Regression:** no (`purge_trivial_logs` at cleanup.sh:361-391 and its caller at 543-547 are untouched by this RC's diff)
**Integrity Damage:** no
**Filed As:** BUG-0280 (Path C-A)

`purge_trivial_logs()` returns its count by `echo "$count"` (line 390) and is
called in command substitution (line 544), but its dry-run branch also calls
`log "[DRY-RUN] trivial-purge would delete: $logfile"` (line 378), and `log()`
writes to stdout. The substitution captures both, so the summary prints:

```
cleanup: trivial_logs_deleted:  [2026-09-15T03:33:55+00:00] cleanup: [DRY-RUN] trivial-purge would delete: /…/root/logs/agents/coder-batch-20260911-000000.log
```

where `3` was expected. Reproduced in a fully sandboxed root (both variables
redirected) seeded with three matching `*-batch-*.log` files; the files were
correctly left in place, only the reported number is wrong. The non-dry-run path
is unaffected because its `else` branch calls `rm -f` without logging per file —
which is why the defect has survived: only the preview is wrong, and only when
it has something to preview.

This is the command-substitution capture-contamination class named explicitly in
the Test Fidelity Verification checklist in `roles/TESTER.md` (item 4).

### Finding 8: Prerequisite dependency graph

**Category:** pass

No cycles. Every referenced task ID resolves to an existing task folder.
Bookends are correct: `CM-20260915-007-open-rc-2-22-2` (DONE) opens, the five
feature tasks depend on it, `CODER-20260915-006` depends on the other four,
`TESTER-20260915-008` depends on all five, and `CM-20260915-010-release-2-22-2`
(WAITING) depends on all five plus TESTER. Sequence numbers are monotonic and
non-colliding (009 is unused, which is not a collision).

### Finding 9: Pre-flight merge verification

**Category:** pass

Worktree `HEAD` (`f926ecd98179ee347007dd6648857fd2bea35711`) equals
`git rev-parse ai_rc/v2.22.2`. `git status --short` clean. `git stash list`
empty. All five DONE feature branches were deleted after merge (normal
post-merge cleanup), so both merge checks correctly skip them; zero anomalies.
The RC's merge commits for tickets 002, 003, 004 and 005 are all present in
`ai_main..ai_rc/v2.22.2`, and ticket 006's branch was identical to the RC tip by
design.

### Finding 10: Constraint negative checks

**Category:** pass

`set -euo pipefail` still present at `cleanup.sh:111`, after the config-sourcing
bootstrap and before the argument loop — the `--help` addition did not move or
weaken it. No credential-shaped strings introduced anywhere in the diff
(`password`, `api_key`, `secret`, `token`, private-key headers — zero hits
outside `secrets_example`). All changed Python compiles (`python3 -m py_compile`
on all four touched/added `.py` files) and `bash -n team/scripts/cleanup/cleanup.sh`
passes. The skip-cites-real-bug gate reports `0 missing files` and
`all skip annotations are compliant`; the only grep hits for placeholder IDs are
synthetic fixture strings inside `team/tests/unit/test_skip_bug_citation_scan.py`
(the test of the gate itself) and archived coverage run logs — not real skips.

### Finding 11: No new provenance debt introduced by this RC

**Category:** pass

The hygiene lint's 26 provenance findings were checked line by line against
`git blame`. Every one of them — including the five in files this RC modified
(`lint_doc_command_truth.py:60`, `test_lint_doc_command_truth.py:536/548/625/650`)
— originates in commit `fe8ad4c` ("Release v2.22.1"), not in any of this RC's
commits. CODER-20260915-003 additionally reworded six provenance citations it
had introduced during authoring, and the current run confirms
`lint_doc_command_truth.py:168` (present when BUG-0278 was filed) is gone. This
RC's net contribution to the provenance class is zero.

### Finding 12: The key-parity discrimination proof survived its live subject being fixed

**Category:** pass-with-caveat

BUG-0276 noted that `docs/OPERATIONS.md:930` was being kept as the live proof
that the key-parity check still discriminates after BUG-0268's false-positive
fix. This RC fixes that doc line, which removes the live proof. Spot-checked
whether the discrimination is still covered: it is, by synthetic-fixture tests in
`team/tests/unit/test_lint_doc_command_truth_keys_panes.py` —
`test_unknown_key_citation_is_flagged`,
`test_default_mismatch_and_unknown_key_are_distinct`,
`test_removing_unknown_key_seed_clears_its_finding`, and
`test_genuinely_unknown_key_flags_but_real_project_cfg_key_does_not` (which
asserts an invented `[project]` key flags while a real one does not). The caveat
is only that the tree no longer carries a standing live instance, so a future
regression would be caught by the fixtures alone. That is the normal and correct
end state, not a gap.

## Lint Findings (non-blocking)

- `hygiene` — 26 `provenance` instances across the scanned tree (identical set in
  both the unit and integration findings files; one lint class, one group, one
  bug). Every instance predates this RC (see Finding 11). Cites **BUG-0274**
  (open) and **BUG-0278** (open), which together already track this exact class;
  no duplicate filed, per the cite-an-open-bug-or-file-one rule.

No other quality-class findings. `findings-unit.json` (2026-09-15T03:41:16+00:00)
and `findings-integration.json` (2026-09-15T03:49:38+00:00) each carry 26
findings, all `lint: hygiene`, `severity: quality`, and zero
`lint: doc_command_truth` entries.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=7d freshness=stale — the last recorded lane run was red on the claude-abliterated flavor under both engines and on podman/rocky9, and it is stale on version (v2.9.20 is not the v2.22.2 under verification); cites BUG-0167-cold-build-claude-abliterated-failure, which is now `done`, and the `FROM t2fn/claude-abliterated:latest-amd64` line the record failed on is a valid single-argument FROM in this tree, so the recorded red is very likely already repaired but has not been re-exercised.

## Autonomous criterion

All four checks answer **NO**. Manual interventions required: none.

1. **Tasks force-promoted from WAITING to BACKLOG manually?** NO.
   `logs/reset.log`'s most recent entries are 2026-09-14T10:54 (the prior RC);
   nothing in this RC's window. Every task's `status.md` Summary is
   agent-authored work product.
2. **`release-state.md` edited outside the CM scripts?** NO. Its mtime is
   2026-09-15 01:48, which is exactly `## RC Opened At`
   (2026-09-15T01:48:28+00:00) — the `cm-open-rc.sh` write. Contents are
   internally consistent: `Active RC: v2.22.2`, `RC Opened By Task:
   CM-20260915-007-open-rc-2-22-2`, `Last Released: v2.22.1`.
3. **Scripts patched mid-build?** NO. `git log --since=2026-09-15T01:40 ai_main`
   is empty — no commit landed on the prefixed main branch while the RC was in
   flight. Every commit in `ai_main..ai_rc/v2.22.2` is a ticket commit or its
   merge, in ticket order, between 01:52 and 02:53.
4. **Queue files manually edited?** NO. Queue mtimes track wake-script activity
   (`cm_backlog.md` 01:49, `writer_backlog.md` 01:54, `coder_backlog.md` and
   `tester_backlog.md` 03:26 — the promotion of this task).

One process observation that is **not** an intervention, recorded because it is
the subject of Finding 6: CODER-20260915-002 ran a live cleanup pass against the
operator's kanban root while testing its own ticket, and disclosed it in its
status summary. That was an agent action inside its own session, not a human
reaching into the chain, so it does not flip any of the four answers. It is
filed as BUG-0279 because the mechanism that permitted it is a code defect, not
an agent mistake.

## Caveats

- **Step 6.8 (synthetic release simulation) skipped by trigger condition.** The
  diff touches no release-lifecycle file (`cm-release.sh`, `cm-open-rc.sh`,
  `cm-cancel-rc.sh`, `project_paths.sh`, or any open/close-cycle participant).
  `tests/integration/test_cm_release_synthetic.py` nonetheless ran green (15
  passed) as an ordinary member of the integration suite.
- **This verification wrote one file into the operator's live kanban root.** The
  Case A probe for Finding 6 was run with `--dry-run` specifically so it would
  destroy nothing, and it destroyed nothing (`logs_deleted: 0`,
  `task_folders_deleted: 0`, `requirements_archived: 0`, `temp_items_purged: 0`).
  But cleanup writes its summary log unconditionally, so an 8.7 MB
  `logs/agents/cleanup-20260915-033216.log` now exists in the live root. It is a
  normal cleanup run log recording a no-op preview, and the ordinary retention
  sweep will age it out. Disclosed rather than quietly removed. Every other
  behavioral probe in this verification ran with both `KANBAN_ROOT` and
  `PGAI_AGENT_KANBAN_ROOT_PATH` redirected into throwaway directories under the
  framework temp root.
- **BUG-0280 is severity `low` and a deferral is in force.** The project carries
  `DEFER-LOW-BUGS` with `until = v2.23.0`, and `project.cfg` sets
  `defer_piggyback = any`. Because BUG-0279 is `high`, it will trigger a bundle
  and BUG-0280 will ride along rather than wait. No action is owed; noted so the
  next reader is not surprised to see a low bug bundle during a deferral.
- **Deep-verify has not run in 7 days and its last record is red and eight
  versions behind.** Per Step 5d this is context the rating is made with, not a
  finding, and it neither raises systemic risk nor moves the recommendation. It
  does mean the pre-release freshness check will refuse promotion if CM runs it;
  the remedy is the on-demand verb, and that is CM's call, not TESTER's.
- **`project.cfg_example [project] temp_dir` is now an active line reading
  `tmp.my-project`.** That is the loader default resolved against the template's
  own placeholder `project_name = my-project`, so an unedited copy behaves
  identically — the claim CODER-20260915-005 made is correct. The residual
  sharp edge is that an operator who copies the template and changes
  `project_name` but not `temp_dir` gets a temp directory named after the
  placeholder. Recorded as an observation, not a finding: every other value in
  that file follows the same placeholder convention.
- **`team/tests/unit/test_cfg_example_key_coverage.py`'s module docstring cites
  "roles/TESTER.md 'Test Authoring Guidelines'".** That section is canonically in
  `team/SOP.md`, not the role file. Cosmetic; observation only.
- **`cleanup.sh --help` does not list its own `-h` alias.** The alias works and
  the four real flags are all named, so coding-standards directive 6 is
  satisfied. Observation only.
