# Verification Report: rc/v2.23.9

## Summary

Verified release candidate `ai_rc/v2.23.9` of pgai-agent-kanban against its
requirements document `requirements/v2.23.9-bugfix-bundle-20260916.md` (a
single-item BUG bundle carrying `BUG-0305-hygiene-lint-provenance`).
Verification ran in the isolated detached worktree at
`/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260916-047-verify-2-23-9`
at commit `eb1605cc76f447dcbabe5aa629663654154347cd`, which matches
`git rev-parse ai_rc/v2.23.9` exactly. The RC's entire content versus `ai_main`
is one file, three insertions and two deletions: two docstrings in
`team/tests/unit/test_overwatch_empty_files_report_scope_bash.py` reworded from
literal internal bug identifiers to the generic `BUG-NNNN` placeholder shape.
Both gated runners were executed with `--coverage` and both exited 0. The
bundle's three acceptance criteria were each executed and each passed. The guard
this RC heals -- the hygiene lint's `provenance` class -- was verified
behaviorally by replaying the pre-RC file content through the lint and
confirming it still fires, then confirming the RC content is silent. No bug,
gap, or stale assertion was found. Recommendation: `PASS`.

## Recommendation

PASS

## Systemic Risk

low

## Findings

### Finding 1: Pre-flight -- RC intact, no unmerged DONE-task branches

**Category:** pass

Scanned every task folder under
`$HOME/pgai_agent_kanban/projects/pgai-agent-kanban/tasks/`. Every folder
whose `status.md` reads `## State: DONE` and whose `README.md` names a
`## Feature Branch` other than `none` was checked for local branch existence:
zero such branches exist locally, which passes both Step 2 checks trivially --
CODER and WRITER delete feature branches after merging, so absence is the normal
post-merge shape. The one contributing task,
`CODER-20260916-045-provenance-lint-sweep`, names
`ai_feature/CODER-20260916-045-provenance-lint-sweep`; that branch is gone and
its commit `74d451f` is present on the RC under merge commit `eb1605c`, so its
work demonstrably landed.

### Finding 2: Working tree identity and cleanliness

**Category:** pass

- `git rev-parse HEAD` -> `eb1605cc76f447dcbabe5aa629663654154347cd`
- `git rev-parse ai_rc/v2.23.9` -> `eb1605cc76f447dcbabe5aa629663654154347cd` (equal)
- `git status --porcelain` -> empty
- `git stash list` -> empty

### Finding 3: `release-state.md` schema and version ordering (Step 6.1)

**Category:** pass

`release-state.md` holds all four fields: `## Active RC` = `v2.23.9`,
`## RC Opened At` = `2026-09-16T19:39:28+00:00`, `## RC Opened By Task` =
`CM-20260916-046-open-rc-2-23-9`, `## Last Released` = `v2.23.8` (well-formed
`vX.Y.Z`). `pp_last_released_version "pgai-agent-kanban"` returns `v2.23.8`, and
`semver_gt "v2.23.9" "v2.23.8"` from `team/scripts/lib/semver.sh` returns true --
the RC under test is strictly greater than the last released version. `Active RC`
is consistent with the actual branch state: `ai_rc/v2.23.9` exists locally and no
other `ai_rc/*` branch does.

### Finding 4: Bundle acceptance criterion 1 -- "Each bundled item is addressed"

**Category:** pass

The bundle carries exactly one item, `BUG-0305-hygiene-lint-provenance`, which
reported 2 `provenance`-class instances at
`team/tests/unit/test_overwatch_empty_files_report_scope_bash.py:155` and `:249`.
Ran from the worktree root:

```
python3 team/scripts/lint_hygiene.py --classes provenance
  -> lint_hygiene: scanning 5 code dir(s), 1 test-tree dir(s), 1 prose dir(s),
     1 config dir(s); classes=['provenance']
  -> lint_hygiene: OK -- 0 findings across 8 scan director(y/ies).
  -> exit 0
```

The full lint across all three registered classes is also clean:

```
python3 team/scripts/lint_hygiene.py
  -> classes=['provenance', 'version-named-test', 'comment-drift']
  -> lint_hygiene: OK -- 0 findings across 8 scan director(y/ies).
```

The CODER ticket's stated scope was the *class*, not the named file list, and the
class is at zero tree-wide, so the sweep was performed as specified rather than
as a transcription of the bug's snapshot list.

### Finding 5: Bundle acceptance criterion 2 -- "All Python files compile cleanly"

**Category:** pass

`find . -name "*.py" -not -path "./.git/*" | xargs -n50 python3 -m py_compile`
produced no output and exited 0 across the whole tree.

### Finding 6: Bundle acceptance criterion 3 -- "All bash files pass syntax check"

**Category:** pass

`bash -n` over every `*.sh` in the tree (excluding `.git`) produced zero
failures.

### Finding 7: Behavioral verification of the healed guard (Step 6.5)

**Category:** pass

Principle 4 and anti-pattern 6 require synthesizing the ORIGINAL failure mode as
well as the new normal case. Both were exercised. Two scratch trees were built
under `PGAI_AGENT_KANBAN_TEMP_DIR` (the worktree itself was never modified;
`git status --porcelain` remained empty throughout): one holding
`git show ai_main:team/tests/unit/test_overwatch_empty_files_report_scope_bash.py`
(the pre-RC content), one holding the same path from `ai_rc/v2.23.9`. Each was
scanned with the scan surface narrowed to that directory alone, so the shared
allowlist sidecar could not mask the result:

```
# PRE-RC content (the original failure mode)
lint_hygiene: 2 finding(s) (non-blocking; ...)
  1. reword pre/t.py:155 ... citing the BUG-ID ...
  2. reword pre/t.py:249 ... citing the BUG-ID ...

# RC content (the new normal case)
lint_hygiene: OK -- 0 findings across 1 scan director(y/ies).
```

The guard fires on exactly the two lines `BUG-0305` named and is silent on the
shipped content. Structural inspection alone would not have distinguished "the
docstring was edited" from "the lint no longer fires".

### Finding 8: The reword did not change test behavior

**Category:** pass

The diff is docstring-only -- read in full, it changes two triple-quoted
docstring bodies and nothing else. The fixture filename literals the docstrings
narrate (`BUG-0001-something.md` at the file's line 164,
`BUG-0005-something.md.empty.orphan` at line 262) are unchanged in the executable
code, which is the correct outcome: a fixture filename is a usage value, not a
provenance citation, and the lint correctly never flagged those lines. The file's
four tests ran and passed inside the gated unit run
(`tests/unit/test_overwatch_empty_files_report_scope_bash.py ....  [ 95%]`).

### Finding 9: Gated suites (Steps 5a, 6.6, 6.7)

**Category:** pass

Both runners were executed from the dev-tree root with `--coverage`, output teed
to `PGAI_AGENT_KANBAN_TEMP_DIR/tester/`:

| Runner | Exit | Result |
|---|---|---|
| `team/scripts/run-unit-tests.sh --coverage` | `UNIT_EXIT=0` | 4648 passed, 31 skipped in 890.15s |
| `team/scripts/run-integration-tests.sh --coverage` | `INTEGRATION_EXIT=0` | 515 passed, 1 skipped in 579.37s |

Every lint pre-flight in both runners reported OK, including
`test_anti_patterns`, `skip_cites_real_bug`, `orphan_tests`, `api_parity`,
`operator_verb_manifest`, `icd_freshness`, `icd_compat`, `changelog_freshness`,
`doc_command_truth`, `help_presence`, `hygiene`, `python_import_completeness`,
`dockerfile_static` (3 Dockerfiles, 0 violations), and the post-run
`bare_tmp_cleanliness` and `listener_cleanliness` checks. The PASS evidence cited
here is the two runner exit codes, not a pytest "N passed" line.

### Finding 10: Constraint negative checks (Step 6.9)

**Category:** pass

- The CODER ticket's "reword in preference to suppressing" constraint holds:
  `team/scripts/hygiene-lint-allowlist.json` is NOT in the RC diff, so no new
  suppression was added to reach zero findings.
- The diff introduces no secrets, tokens, or credentials -- it touches docstring
  prose only.
- No `set -euo pipefail`, cleanup trap, or parameterized-query site is touched by
  the RC.

### Finding 11: Skip-cites-real-bug gate (Step 6.13)

**Category:** pass

`bash team/scripts/lint_skip_bug_gate.sh` exited 0: "checking for placeholder
skip IDs ... BUG-NNNN file-existence check: 0 missing files ... OK -- all skip
annotations are compliant." Independent greps for placeholder shapes (`BUG-SKIP`,
`skip` near `BUG-<letters>`) found only two non-violating classes of hit:
archived runner logs under `team/tests/artifacts/`, and the gate's own test
fixtures in `team/tests/unit/test_skip_bug_citation_scan.py` (synthetic IDs
`BUG-1234` through `BUG-1239`, which are the subject under test rather than
citations).

### Finding 12: Prerequisite dependency graph (Step 6.10)

**Category:** pass

The v2.23.9 lifecycle is correctly bookended and acyclic:

```
PM-20260916-007-decompose-...  (prereq: none, DONE)
CM-20260916-046-open-rc-2-23-9 (prereq: none, DONE)
  +- CODER-20260916-045-provenance-lint-sweep (prereq: CM-046, DONE)
       +- TESTER-20260916-047-verify-2-23-9    (prereq: CODER-045, WORKING)
            +- CM-20260916-049-release-2-23-9  (prereq: CODER-045 + TESTER-047, WAITING)
```

No cycles; every referenced task ID resolves to an existing folder; no sequence
collisions. The CODER ticket carrying a lower sequence number (045) than the
open-RC bookend it depends on (046) matches the established materialization order
in this project -- the immediately prior cycle used the same shape (CODER-039 /
WRITER-040 under CM-041-open-rc-2-23-8) -- so it is not drift.

### Finding 13: Synthetic release simulation (Step 6.8)

**Category:** pass (skipped by rule, with note)

Skipped as the step directs. The RC diff touches exactly one path,
`team/tests/unit/test_overwatch_empty_files_report_scope_bash.py`. No
release-lifecycle file -- `cm-release.sh`, `cm-open-rc.sh`, `cm-cancel-rc.sh`,
`team/scripts/lib/project_paths.sh`, or anything in `team/scripts/` in the
open/close cycle -- is in the diff, so the trigger condition for this step is not
met. `team/tests/integration/test_cm_release_synthetic.py` nonetheless ran as
part of the green integration suite.

### Finding 14: Coding-standards and documentation-discipline spot-check (Step 6.14)

**Category:** pass

No new script, no new `--help` surface, no new operational value, and no newly
duplicated `lib/` implementation is introduced -- the RC changes prose in an
existing test module only. The Documentation Discipline Rule creates no
obligation here: the change adds or alters no tunable, environment variable,
configuration value, script behavior, or team convention, so no `*_example`,
README, or SOP update is owed. `lint_doc_command_truth`,
`lint_changelog_freshness`, and `lint_icd_freshness` all reported OK inside the
gated run.

### Finding 15: Test fidelity of the change

**Category:** pass

The RC's change is prose in a test module; the behavior it narrates (quarantine
of empty bug-report files, scope-bounded pruning of claim-lock residue) is
exercised by the module's four tests through a real `bash` subprocess sourcing
the real `overwatch_check_empty_files` implementation against a real temp kanban
tree -- not a mocked proxy. The breaking condition the module guards is therefore
genuinely exercised. No fidelity concern is raised.

## Lint Findings (non-blocking)

None. `PGAI_AGENT_KANBAN_TEMP_DIR/lint/findings-unit.json` and
`.../findings-integration.json` were both written by their runners and both
contain `"findings": []` (schema_version 1, generated 2026-09-16T20:02:54+00:00
and 2026-09-16T20:13:03+00:00 respectively).

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=9d freshness=stale — Stale on both counts: the record is 9 days old against the 7-day window and was taken at v2.9.20 rather than the v2.23.9 under verification; its two named causes (a three-token FROM in docker/claude-abliterated/Dockerfile and a COPY --chmod=+x in docker/rocky9/Dockerfile) are no longer present in this tree and the bug that run filed, BUG-0167-cold-build-claude-abliterated-failure, is closed done, so the red verdict is cited as history and nothing is re-filed; this project's project.cfg sets [release] deep_verify = off, so the lane is deliberately dark here and CM owns any decision to run it.

## Autonomous criterion

Step 6.12 -- **Manual interventions required: none.**

| # | Check | Answer |
|---|---|---|
| 1 | Any task force-promoted from WAITING to BACKLOG by hand? | NO |
| 2 | Was `release-state.md` edited outside `cm-open-rc.sh` / `cm-release.sh` / `cm-cancel-rc.sh`? | NO |
| 3 | Were any scripts patched mid-build (a commit touching `team/scripts/`, `subagents/`, or other tooling while the RC was in flight)? | NO |
| 4 | Were any queue files edited by hand? | NO |

Evidence. (1) `TESTER-20260916-047`'s own promotion is stamped in its `status.md`
as "Promoted from WAITING to BACKLOG by wake script: prerequisites satisfied",
and the wake log at `logs/cron-tester.log` carries the matching automated entry;
no other task in the cycle sat in WAITING. (2) `release-state.md`'s mtime is
`2026-09-16 19:39:28.728` -- identical to the `## RC Opened At` value it contains,
i.e. the single write by `cm-open-rc.sh`, with no later touch. (3) The RC carries
exactly two commits versus `ai_main` (`74d451f` and its merge `eb1605c`), and the
only file either touches is
`team/tests/unit/test_overwatch_empty_files_report_scope_bash.py`; no
`team/scripts/` or `subagents/` path is in the diff. (4) Queue-file mtimes form an
unbroken automated sequence matching the wake log -- `bug_backlog.md` 19:34:09
(discovery bundling), `pm_backlog.md` 19:38:21 (materialization), `cm_backlog.md`
19:40:11, `coder_backlog.md` 19:45:37, `tester_backlog.md` 19:45:54 -- each within
seconds of the corresponding `wake(...)` log line, with no out-of-band edit.

The full chain -- discovery bundling at 19:34:09, PM queue at 19:35:36, PM
materialize at 19:38:21, CM open-RC at 19:39:28, CODER merge at 19:44:13, TESTER
worktree at 19:45:54 -- ran unattended end to end.

## Caveats

- **Coverage (informational only; never enters the verdict).** Read from the
  runners' JSON `totals` objects after verifying the field names against the
  generated files. Unit suite: line (statement) coverage **77 %**, branch
  coverage **63 %**, blended **73 %**. Integration suite: line (statement)
  coverage **10 %**, branch coverage **8 %**, blended **9 %**. The low
  integration figure is structural -- that suite exercises shell entry points, so
  the Python package it measures is largely untouched by it -- and is an
  observation, not a finding.
- **Observation, not a finding: a stray `defer_piggyback = any` in this install's
  `projects/pgai-agent-kanban/project.cfg`.** The key sits at the end of the
  file, after the `[training]` heading, so it is `[training]`-scoped rather than
  `[release]`-scoped, and its value is not one of the accepted booleans. This is
  inert by design, not a defect: `team/scripts/lib/config_loader.sh` documents the
  case explicitly at lines 531-535 ("read_ini's section scoping means a same-named
  key sitting in some OTHER section of the cfg (e.g. a stray defer_piggyback under
  [training]) is never read here and never triggers a refusal"), and the
  `[release]`-scoped lookup correctly falls through to the `true` default. It is
  install-local kanban state, not version-controlled tree content, so it is
  outside this RC's diff entirely. Recorded here so an operator who did not intend
  to leave it there can tidy it.
- **The RC is unusually small** -- one file, three insertions, two deletions,
  entirely within a docstring. Nothing about that is wrong; it is simply worth CM
  knowing that the shipped surface area of v2.23.9 is a prose correction in a test
  module and carries no executable-behavior change.
- **Requirements and bundled-item status headers are both `running`**, which is
  the correct in-flight state. `cm-release.sh` Step 16b flips the bundle and
  `BUG-0305-hygiene-lint-provenance` to `done` at ship time; TESTER does not touch
  them.
