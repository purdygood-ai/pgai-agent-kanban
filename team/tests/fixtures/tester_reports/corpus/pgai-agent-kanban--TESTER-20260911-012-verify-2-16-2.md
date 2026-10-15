# Verification Report: ai_rc/v2.16.2

**Task:** TESTER-20260911-012-verify-2-16-2
**Requirements doc:** `projects/pgai-agent-kanban/requirements/v2.16.2-bugfix-bundle-20260911.md`
**RC branch / commit:** `ai_rc/v2.16.2` @ `a929226` (worktree HEAD matches, detached, clean, no stashes)
**Baseline compared against:** `ai_main` @ `54bfcee` (v2.16.1)
**Verified:** 2026-09-11, from the isolated worktree at
`/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260911-012-verify-2-16-2`

## Summary

v2.16.2 is a two-item bug bundle (BUG-0240, BUG-0241) touching four files: the
hygiene lint (`team/scripts/lint_hygiene.py`), the aggregate hygiene bug filer
(`team/pgai_agent_kanban/lint/bug_filer.py`), and their two unit-test modules.
Verification covered pre-flight merge checks, both gated test suites, every
acceptance criterion of both CODER tickets and of the requirements doc, and
behavioral exercise of both fixes — including reverting each fix in a throwaway
copy of the tree to confirm the new tests are real regression guards rather than
assertions that pass either way. Both gated runners exit 0. Every acceptance
criterion is met. No `bug`, `gap`, or `stale-assertion` finding was recorded.
One pre-existing quality-class lint finding (a task ID cited in
`release-notes/v2.16.0.md`) is carried in the non-blocking section and cites the
open bug that already tracks it.

## Recommendation

PASS

## Systemic Risk

low

## Findings

### Finding 1: Pre-flight — merge verification, worktree identity, stash check

**Category:** pass

- Every DONE task with a live `## Feature Branch` was checked: no UNMERGED and
  no BUG-92 (zero-unique-commits) anomaly. Both of this RC's feature branches
  (`ai_feature/CODER-20260911-009-...`, `ai_feature/CODER-20260911-010-...`) were
  deleted after merge, which the procedure treats as merged.
- `git rev-parse HEAD` = `a929226a66f5277ad73ae27a0992f82b043006af` =
  `git rev-parse ai_rc/v2.16.2`.
- `git status --short` empty; `git stash list` empty.
- RC history (`ai_main..ai_rc/v2.16.2`): `f15ad11` + merge `678a915`
  (CODER-009), `0451aa5` + merge `a929226` (CODER-010). Nothing else landed on
  the RC, and `ai_main` has not moved since the v2.16.1 release commits.

### Finding 2: Gated unit suite

**Category:** pass

`bash team/scripts/run-unit-tests.sh --coverage` -> **exit 0**
(`3806 passed, 3 skipped in 519.10s`). Lint pre-flight green: breakage-class
checks, skip-cites-real-bug gate, help-presence, import-completeness,
bare-/tmp cleanliness, listener cleanliness all OK.

### Finding 3: Gated integration suite

**Category:** pass

`bash team/scripts/run-integration-tests.sh --coverage` -> **exit 0**
(`442 passed, 1 skipped in 416.13s`). Includes
`tests/integration/test_cm_release_synthetic.py` (15 tests, all passing).

### Finding 4: Requirements AC — "each bundled item is addressed"

**Category:** pass

**BUG-0240 part 1 (absolute scan-root path persisted into a filed bug's cause).**
Exercised from a throwaway scan root under
`/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester_scratch_v2162`, against
**all six** refusal shapes the sidecar loader can raise. Every one exits 0 and
records a repo-relative cause with zero occurrences of the scan root:

| refusal shape | recorded cause (excerpt) | scan-root leaks |
|---|---|---|
| blank justification | `sidecar team/scripts/hygiene-lint-allowlist.json entry #0 (file='code/dirty.py', ...) has a blank justification ...` | 0 |
| invalid JSON | `sidecar team/scripts/hygiene-lint-allowlist.json is not valid JSON: ...` | 0 |
| non-list top level | `sidecar team/scripts/hygiene-lint-allowlist.json must be a JSON array of entries; found a top-level dict` | 0 |
| non-object entry | `sidecar team/scripts/hygiene-lint-allowlist.json entry #0 is not a JSON object` | 0 |
| missing required field | `sidecar team/scripts/hygiene-lint-allowlist.json entry #0 is missing required field(s): content_hash` | 0 |
| unreadable file | `sidecar team/scripts/hygiene-lint-allowlist.json could not be read: [Errno 13] Permission denied: 'team/scripts/hygiene-lint-allowlist.json'` | 0 |

The unreadable case is the interesting one: the underlying `OSError` embeds the
path a second time, and the fix's replace-all covers both copies. The finding's
identity is unchanged (`class=hygiene-sidecar`, `label=SIDECAR-MALFORMED`,
`lineno=0`), as the ticket required.

Also exercised in the production-shaped variant the synthetic test does not
cover: invoking the lint from a *different* working directory with an absolute
`--repo-root` and an absolute `--sidecar` still records the repo-relative cause
(0 leaks).

**BUG-0240 part 2 (unrunnable `## Reproduction` command).** A bug filed by
`pgai_agent_kanban.lint.bug_filer` from the scratch scan root for the
`hygiene-sidecar` finding contains **0** occurrences of the scan root across
every section, and renders:

```
1. Run `python3 team/scripts/lint_hygiene.py` and observe the finding(s) named above -- the 'hygiene-sidecar' check runs on every invocation, whatever --classes selects.
```

Run from a repo root carrying the finding, that command **exits 0** and its
output names the finding (the sidecar file and the blank-justification reason
appear twice in stdout). The pre-fix command
(`--classes hygiene-sidecar`) exits 2. The three registered classes keep their
narrowed commands (`--classes provenance`, `--classes version-named-test`,
`--classes comment-drift`), and each **exits 0** run from the dev-tree root.

**BUG-0241 (stale exit-contract comment).**
`grep -n "sidecar-malformed path above" team/scripts/lint_hygiene.py` -> no hits
(exit 1). The replacement comment above `main()`'s final `return 0` now names
only CLI-argument validation as the non-zero exit and explicitly points at the
module docstring's "Exit codes" section (lines 207-218), which it agrees with.
Confirmed behaviorally: with findings present, the lint exits 0; the only
non-zero exit observed was 2 for an unknown `--classes` value.

### Finding 5: Requirements AC — Python compiles / bash syntax

**Category:** pass

- `py_compile` over the whole tree: **399 Python files, 0 failures**.
- `bash -n` over the whole tree: **257 shell files, 0 failures**.

### Finding 6: Behavioral guard check — do the new tests actually fail pre-fix?

**Category:** pass

Both fixes were reverted in a throwaway copy of `team/`
(`/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester_revert_a`, the live
tree untouched, `PGAI_AGENT_KANBAN_ROOT_PATH` repointed at a fresh temp root)
and the new/extended tests re-run against the pre-fix code:

| test | pre-fix result |
|---|---|
| `test_lint_hygiene.py::test_blank_justification_is_reported_as_a_finding_not_an_abort` | FAILED |
| `test_lint_hygiene.py::test_invalid_json_sidecar_is_reported_as_a_finding` | FAILED |
| `test_lint_hygiene.py::test_missing_content_hash_sidecar_is_reported_as_a_finding` | FAILED |
| `test_lint_hygiene.py::test_unreadable_sidecar_is_reported_as_a_finding` | FAILED |
| `test_lint_bug_filer.py::test_filed_bug_for_an_unregistered_class_omits_the_scan_root_and_runs` | FAILED |

Observed pre-fix messages match the CODER completion notes: the hygiene
assertions fail with the scan-root path contained in the cause, and the filer
test fails on the rendered `--classes hygiene-sidecar` line. All five are real
guards, not assertions that pass either way.

### Finding 7: New mechanism — live registry introspection in the filer

**Category:** pass-with-caveat

`_registered_lint_classes()` loads `team/scripts/lint_hygiene.py` by
self-locating from `bug_filer.py`'s own path
(`Path(__file__).resolve().parents[2] / "scripts" / "lint_hygiene.py"`) and
calls `registered_class_names()` — no second copy of the registry, which is
what the ticket's constraint asked for. Verified directly: returns
`['provenance', 'version-named-test', 'comment-drift']`; renders narrowed
commands for those three and the class-free command for `hygiene-sidecar` and
for any other unregistered token.

Caveat (not a defect, no filing): the failure fallback — script missing or
unloadable -> `None` -> always render the class-free command — is correct and I
exercised it by hand (repointing `_LINT_HYGIENE_SCRIPT` at a nonexistent path
yields `None` and a runnable class-free command, no exception), but it has no
unit test of its own. Also, the class-free sentence asserts that "the '<cls>'
check runs on every invocation"; that is true of `hygiene-sidecar` (the only
unregistered class that reaches the filer today) but would be inaccurate prose
if a future non-scan class were added. Both are hygiene-grade, and the
self-location matches the installed layout (`install.sh` copies `team/scripts`
-> `$ROOT/scripts` and `team/pgai_agent_kanban` -> `$ROOT/pgai_agent_kanban`, so
`parents[2]/scripts` resolves in an install too).

### Finding 8: BUG-0239 closure (CODER-010 acceptance item 5)

**Category:** pass

`projects/pgai-agent-kanban/bugs/BUG-0239-hygiene-lint-hygiene-sidecar.md` now
reads `## Status: done` with exactly one added line — "This report captured a
reproduction performed in a scratch tree, not a defect in the shipped tree." —
and every other section byte-identical to the auto-filed original (its
`## Actual` still carries the deleted scratch path, which is the evidence the
constraint said to preserve). It can no longer be bundled into a future RC.

### Finding 9: Step 6.1 — release-state drift

**Category:** pass

`pp_last_released_version pgai-agent-kanban` -> `v2.16.1`; `semver_gt v2.16.2
v2.16.1` true. `release-state.md` holds `Active RC: v2.16.2`, opened
2026-09-11T05:05:15+00:00 by `CM-20260911-011-open-rc-2-16-2`, `Last Released:
v2.16.1` (well-formed `vX.Y.Z`). The `ai_rc/v2.16.2` branch exists and matches.
`VERSION` still reads `v2.16.1` — expected, CM writes it at release time.

### Finding 10: Step 6.10 — prerequisite graph

**Category:** pass

Five tasks, no cycles, all referenced IDs exist, bookends correct:
CM-011 (open RC) -> CODER-009 -> CODER-010 -> TESTER-012 -> CM-014 (release,
prereqs CODER-009, CODER-010, TESTER-012). Sequence numbers non-colliding.

### Finding 11: Step 6.13 — skip-cites-real-bug gate

**Category:** pass

No placeholder skip IDs (`BUG-SKIP`, `skip ... BUG-<alpha>`) in `team/tests/`;
the only textual hits are inside archived coverage run logs under
`team/tests/artifacts/`, not skip annotations. No skip annotation cites a
`BUG-NNNN`, so there is nothing to resolve against `bugs/`. The gated runners'
own `lint_skip_bug_gate` reported OK.

### Finding 12: Step 6.8 — synthetic release simulation

**Category:** pass (not triggered, run anyway)

The RC diff touches no release-lifecycle file (`cm-release.sh`,
`cm-open-rc.sh`, `cm-cancel-rc.sh`, `lib/project_paths.sh`), so the trigger
condition does not fire. `team/tests/integration/test_cm_release_synthetic.py`
nonetheless ran inside the gated integration suite and passed (15 tests).

## Code Coverage

Read from the JSON `totals` objects written by the two gated runs (field names
confirmed against the generated files, not assumed).

- Unit:        line 75% (`percent_statements_covered`, 11202/14928 stmts) | branch 62% (2768/4496 branches) — blended `TOTAL` line reads 72%
- Integration: line 8% (1176/14919 stmts) | branch 6% (277/4496 branches) — blended `TOTAL` line reads 7%

The two changed production modules are well covered by the unit suite:
`pgai_agent_kanban/lint/bug_filer.py` 82%, and the hygiene-lint sidecar paths
are exercised by the six sidecar tests. Informational only — coverage did not
enter the recommendation.

## Lint Findings (non-blocking)

- `hygiene` (class `provenance`) — `release-notes/v2.16.0.md:42` cites the task
  ID `TESTER-20260910-070` in shipped prose — **cites BUG-0236 (open)**. The
  identical finding appears in both `findings-unit.json` and
  `findings-integration.json` (one instance, one class, one bug). It is
  pre-existing: the file is not in this RC's diff, and the previous RC's
  verification dispositioned the same finding the same way. No duplicate filed;
  the aggregate filer likewise wrote no new bug during either run (newest file
  in `bugs/` is still BUG-0239 at 05:38, before these runs).

No other quality-class findings in either file.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=3d freshness=stale — the docker/podman claude-abliterated pairs and podman/rocky9 failed in the last recorded run against a v2.9.20 tree, so nothing is known about whether this RC's images build; that run filed BUG-0167 (cold-build-claude-abliterated-failure), which is now marked done, and the record is stale on version (v2.9.20 is not the v2.16.2 under verification) though inside the 7-day window.

Per Step 5d this line is reported, not re-derived: no container build was run,
no bug was filed for it, and it does not move the recommendation or the
systemic-risk rating.

## Autonomous criterion

Manual interventions required: **none**.

1. Tasks force-promoted WAITING -> BACKLOG by hand? **NO** — this task's own
   status records "Promoted from WAITING to BACKLOG by wake script:
   prerequisites satisfied"; every other task shows agent-written transitions.
2. `release-state.md` edited outside the CM scripts? **NO** — its contents match
   `cm-open-rc.sh`'s output exactly as recorded in CM-20260911-011's summary.
3. Scripts patched mid-build? **NO** — the only commits between `ai_main` and
   the RC tip are the two ticket commits and their two merges, authored by the
   standard agent identity at 05:29 and 05:37/05:55; `ai_main` has not moved
   since the 04:55 v2.16.1 release commits. `team/scripts/lint_hygiene.py` is
   modified *by the ticket*, which is the deliverable, not an intervention.
4. Queue files hand-edited? **NO** — markers are wake-script-consistent
   (`[A]` on this task in `tester_backlog.md`, `[W]` on the waiting
   `CM-20260911-014-release-2-16-2`, `[x]` on the completed PM and CM entries).

## Caveats

- **Process-history narration in two new comment lines (observation, not a
  finding, not filed).** `team/pgai_agent_kanban/lint/bug_filer.py:107` ("the
  drift the ticket that added this check was written to avoid") and
  `team/tests/unit/test_lint_bug_filer.py:180` ("the production instance this
  ticket closes") narrate ticket history rather than behavior. They cite no
  bug/task/version identifier, so they satisfy the enforced half of
  coding-standards directive 8 and the hygiene lint's `provenance` class passes
  them; and the idiom is pre-existing and widespread (36 occurrences of "this
  ticket" across `team/**/*.py` and `team/**/*.sh`). Recorded here for the
  operator rather than filed as a defect, since filing the two new instances
  while 34 identical pre-existing ones stand would be noise rather than a fix.
- The terminal-facing remedy block still prints the lint script by absolute
  path (`python3 "/tmp/.../team/scripts/lint_hygiene.py"`). That is by design —
  `_finding_remedy` / `_format_remedy_block` were explicitly out of this
  ticket's scope, the *persisted* remedy in the findings file and in filed bugs
  is repo-relative, and the terminal reader benefits from the absolute path.
  Not a finding.
- `BUG-0236` (the open bug this run's lint finding cites) names a different
  instance (`team/tests/unit/test_lint_gate_census_compliance.py:1216`) that no
  longer trips; the class's only live instance today is the release-notes one.
  The bug remains an accurate tracker for the class and no action is owed by
  this verification.
- The live kanban install at `$HOME/pgai_agent_kanban` predates
  `lint_hygiene.py` / `lint/bug_filer.py` (neither file is present there). That
  is install staleness, not an RC defect — `install.sh` copies `scripts` and
  `pgai_agent_kanban` wholesale, so a fresh install of this RC carries both with
  the sibling layout the filer's self-location depends on.
- All ad-hoc reproductions ran from throwaway roots under
  `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/...` with
  `PGAI_AGENT_KANBAN_ROOT_PATH` repointed at a fresh temp directory. Nothing was
  filed into, or run against, the live project `bugs/` directory.
