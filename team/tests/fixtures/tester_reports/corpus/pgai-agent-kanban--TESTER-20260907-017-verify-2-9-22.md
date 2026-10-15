# Verification Report: rc/v2.9.22

**Task:** TESTER-20260907-017-verify-2-9-22
**RC branch:** `ai_rc/v2.9.22` @ `ff1fb38`
**Base:** `ai_main` @ `c8ea7f3` (v2.9.21)
**Worktree verified:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260907-017-verify-2-9-22` (detached HEAD, clean, no stashes)
**Requirements doc:** `projects/pgai-agent-kanban/requirements/v2.9.22-bugfix-bundle-20260907.md`
**Date:** 2026-09-07

Path C glossary, first use in this report:
`Path C` (autonomous follow-up filing — non-blocking),
`Path C-A` (filed as BUG — code defect / regression / test failure),
`Path C-B` (filed as PRIORITY — enhancement / design change),
`BLOCKED` (verification could not complete — infrastructure failure; NOT "found a blocker").

## Summary

v2.9.22 is a single-bug bundle closing BUG-0167, the DEEP-VERIFY cold-build
failure recorded against v2.9.20. It ships two tickets. `CODER-20260907-014`
repaired the two shipped Dockerfile defects behind BUG-0167's three distinct
failing engine/flavor pairs — the duplicated `FROM FROM` keyword in
`docker/claude-abliterated/Dockerfile` and the symbolic `COPY --chmod=+x` in
that file and in `docker/rocky9/Dockerfile`. `CODER-20260907-015` added
`team/scripts/lint_dockerfile_static.py`, an engine-free static Dockerfile lint,
wired it into `run-unit-tests.sh` as a second fatal (BLOCK-WORTHY) pre-flight
check, covered it with 26 unit tests and four fixtures, and registered it as
census row 54 with a matching anchor and Locator Index entry.

Verification ran to completion. Pre-flight merge verification passed. Both
gated runners exited 0 (unit: 3176 passed / 3 skipped; integration: 337 passed /
1 skipped) with zero quality-class lint findings in either findings file. Every
acceptance criterion from the requirements doc and from both bundled tickets was
executed and passed. The new guard was verified behaviorally in both directions,
including end-to-end through the runner: it exits 0 on the fixed tree, and it
exits 1 — naming file, line, and a runnable remedy, through the dual-channel
error contract, before pytest ever starts — on a reconstruction of the pre-fix
tree. Two findings came out of the standards and outside-the-spec spot-checks;
neither is a defect in what the RC set out to do, and both are filed via Path C.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

low

Neither finding puts the chain or any data at risk, and neither makes anything
worse than the last shipped version. The higher of the two (BUG-0171) is a
config-placement defect in live kanban state that causes a recurring wasted cost
and a silently-unhonoured operator setting; it is `medium` on its own line and
does not lift the report-wide rating above `low` for the RC itself, because the
RC's shipped tree is not implicated in it. Recorded honestly rather than
rounded: the RC's own deliverables are clean.

## Findings

### Finding 1: BUG-0167's three failing engine/flavor pairs are resolved in the shipped Dockerfiles

**Category:** pass

Traces to: requirements doc acceptance criterion "Each bundled item is addressed
in the implementation"; `CODER-20260907-014` acceptance criteria 1, 2 and 5.

BUG-0167's eighteen duplicated lines describe three distinct failures:
`docker/claude-abliterated` (FROM parse error), `podman/claude-abliterated` (the
same parse error in podman's wording), and `podman/rocky9` (the
`COPY --chmod` step). Executed:

```
$ grep -c '^FROM' docker/claude-abliterated/Dockerfile
1
$ grep -n '^FROM' docker/*/Dockerfile
docker/claude-abliterated/Dockerfile:3:FROM t2fn/claude-abliterated:latest-amd64
docker/debian/Dockerfile:3:FROM node:20-bookworm AS nodejs
docker/debian/Dockerfile:6:FROM python:3.12-bookworm
docker/rocky9/Dockerfile:3:FROM rockylinux:9
$ grep -rn -- '--chmod' docker/*/Dockerfile
docker/claude-abliterated/Dockerfile:62:COPY --chmod=755 docker/entrypoint.sh /entrypoint.sh
docker/debian/Dockerfile:80:COPY --chmod=755 docker/entrypoint.sh /entrypoint.sh
docker/rocky9/Dockerfile:65:COPY --chmod=755 docker/entrypoint.sh /entrypoint.sh
$ grep -rn -- '--chmod=[^0-7]' docker/*/Dockerfile
(no matches)
```

The base image reference `t2fn/claude-abliterated:latest-amd64` is preserved
byte-for-byte, as the ticket's constraint required. `docker/debian/Dockerfile`
is untouched and remains the in-tree precedent all three flavors now match. The
ticket's own commit `97adca7` touches exactly the two files its `Required
Output` names and nothing else.

Engine-level confirmation is CODER's, recorded in its completion notes:
`docker build --check` exited 0 with no warnings for all three Dockerfiles, and
`podman build --no-cache -f docker/rocky9/Dockerfile .` completed a full image
build. TESTER did not re-run these — `team/roles/TESTER.md` Step 5d forbids
invoking a container engine during RC verification.

### Finding 2: the new static lint fires on exactly the defect class BUG-0167 records — verified in both directions

**Category:** pass

Traces to: `CODER-20260907-015` acceptance criteria 1 and 2; TESTER.md Step 6.5
(trigger the failure mode) and anti-pattern 6 (verify the original failure mode
AND the new normal case).

**Clean direction.** Against the repository's real `docker/` tree, run as a
subprocess with `PATH` set to a directory containing no binaries at all (so
neither `docker` nor `podman` is resolvable):

```
$ env PATH=<empty-dir> /usr/bin/python3 team/scripts/lint_dockerfile_static.py --docker-dir <repo>/docker
lint_dockerfile_static: OK -- 3 Dockerfile(s) checked, 0 violations.
exit=0
```

The engine-free claim is real, not asserted: the lint reads text and never
probes for an engine.

**Failure direction, against the actual pre-fix tree — not a synthetic proxy.**
Reconstructed `ai_main`'s `docker/` via `git archive` and pointed the lint at it:

```
exit=1, 3 violations:
  .../claude-abliterated/Dockerfile:3  -- malformed FROM instruction (found 2 argument(s) after the keyword ...)
  .../claude-abliterated/Dockerfile:62 -- COPY --chmod=+x is not an octal mode ...
  .../rocky9/Dockerfile:65             -- COPY --chmod=+x is not an octal mode ...
```

All three of BUG-0167's distinct real defects are caught, each named by file and
line, each with its own runnable remedy line. This is the regression direction
the ticket required, demonstrated against the genuine historical content rather
than only against fixtures.

**Fixtures.** `bad_from` -> exit 1 naming line 6; `bad_chmod` -> exit 1 naming
line 8; `clean` -> exit 0; `multistage_clean` (two well-formed `FROM` lines, one
with `AS`) -> exit 0. Multi-stage Dockerfiles are correctly unaffected.

### Finding 3: the runner wiring is genuinely fatal and aborts before pytest

**Category:** pass

Traces to: `CODER-20260907-015` acceptance criterion 5; TESTER.md Step 6.5 and
anti-pattern 1 (structural checks do not prove a guard works).

Presence of the call site was not accepted as proof. A throwaway copy of the RC
tree was made under the framework temp root, the original `FROM FROM` defect was
re-injected into `docker/claude-abliterated/Dockerfile`, and the real gated
runner was executed against it with `PGAI_AGENT_KANBAN_ROOT_PATH` repointed at a
temp root (live-install safety):

```
FAULT_UNIT_EXIT=1
... lint_dockerfile_static: FAIL -- 1 violation(s) found ...
error:   run-unit-tests.dockerfile-static-lint
message: The engine-free Dockerfile static lint found a malformed FROM instruction or a non-octal COPY/ADD --chmod mode in a docker/*/Dockerfile; ...
remedy:  Scroll up to the Cause(s) list printed by lint_dockerfile_static.py above, fix each file:line it names, then re-run bash "team/scripts/run-unit-tests.sh".
```

`grep -c 'passed\|failed'` on the captured output returns 0 — pytest never ran.
The refusal carries all three dual-channel fields with a stable machine key, and
the remedy is runnable by a stranger. The guard is real.

### Finding 4: New-gate rule compliance — class comment, error contract, census anchor and index

**Category:** pass

Traces to: `CODER-20260907-015` acceptance criterion 4 and its New-gate-rule
constraint; `team/SOP.md` "New-gate rule (for every future gate)".

- Classification declared in a code comment at the refusal:
  `run-unit-tests.sh:235-240` declares BLOCK-WORTHY and argues it from
  `team/SOP.md`'s "Unusable build blocks; an unavailable build environment does
  not". The reasoning is sound — the two checked shapes cannot be reduced to an
  image by any engine this project ships, so this is doctrine's verdict 2, not
  verdict 3.
- Dual-channel emitter called with all three fields populated (shown above).
- `# CENSUS-ANCHOR: dockerfile-static-lint-01` at `run-unit-tests.sh:234`;
  token unique within the file.
- Locator Index line `team/scripts/run-unit-tests.sh:245 BLOCK-WORTHY` added,
  and line 245 is precisely the `exit 1` the anchor names. Two pre-existing
  entries were correctly renumbered (209->210, 505->541) for the inserted block.
- Census row 54 added; rollup updated 53->54 sites and 27->28 BLOCK-WORTHY.
- `python3 team/scripts/lint_gate_census_compliance.py` exits 0, 0 offenders,
  no half-authored-anchor finding.

### Finding 5: supporting lints and test registration

**Category:** pass

Traces to: `CODER-20260907-015` acceptance criteria 3 and 6.

```
lint_help_presence:          exit 0 — 78 scripts checked, 0 offenders
lint_gate_census_compliance: exit 0 — 32 files checked, 0 offenders, FINDINGS_JSON: []
lint_orphan_tests:           exit 0 — 197 test files covered, no orphans
python3 -m pytest tests/unit/test_lint_dockerfile_static.py  -> 26 passed
```

`--help` names every flag the parser accepts (`--docker-dir`, `--verbose/-v`,
`-h`). Test fidelity is good: the module invokes the real script through
`subprocess` under a restricted `PATH` for the engine-free claim, uses real
fixture files rather than mocks for both negative cases, and parametrizes the
octal/non-octal and FROM-arity boundaries.

### Finding 6: requirements-doc global acceptance criteria

**Category:** pass

```
All Python files compile cleanly  — py_compile over every *.py in the tree: exit 0
All bash files pass syntax check  — bash -n over every *.sh in the tree: 0 failures
```

### Finding 7: gated test suites

**Category:** pass

Authoritative verdict, from the gated runners (not from direct pytest):

```
bash team/scripts/run-unit-tests.sh --coverage         -> UNIT_EXIT=0
    3176 passed, 3 skipped in 390.82s
bash team/scripts/run-integration-tests.sh --coverage  -> INTEGRATION_EXIT=0
    337 passed, 1 skipped in 213.45s
```

Both runners reported `bare_tmp_cleanliness: OK` and
`listener_cleanliness: OK`, and each wrote a findings file containing 0
findings. No test failures of any category — no `bug`, no `stale-assertion`,
no `pre-existing-bug`.

### Finding 8: new lint's docstring cites an internal framework version

**Category:** bug
**Fix Effort:** small (1 CODER task — delete four words from one line)
**Systemic Risk:** low (hygiene; no behavior is affected, no test is affected)
**Regression:** yes (the line was added by this RC's commit `8d19391`; it did not exist in v2.9.21)
**Integrity Damage:** no
**Filed As:** BUG-0170 (Path C-A)

`docs/coding-standards.md` directive 8 forbids citing internal framework
versions in comments, docstrings, help text, log lines or output strings.
`team/scripts/lint_dockerfile_static.py:10` reads:

```
that broke the DEEP-VERIFY cold-build lane at v2.9.20:
```

`v2.9.20` is cited purely as provenance and is covered by none of directive 8's
three exceptions. A scan of every added line in the whole RC diff for bug IDs,
task IDs, priority IDs and version literals produced this one hit and nothing
else — the new call-site block in `run-unit-tests.sh`, the lint's `--help` text,
its runtime output, the new test module and all four fixtures are clean.

The lint's own runtime output already phrases the same idea without the version
("...the two shapes that previously broke the DEEP-VERIFY cold-build lane
undetected"), so the correct wording is already present in the file.

It escaped `lint_comment_provenance.py` because that lint scopes its version
rules to version-bundle names and to a version paired with a "Goal N" phrase,
and does not flag a bare `vX.Y.Z` in prose. That narrowness reads as deliberate
false-positive avoidance, so BUG-0170 asks only for the literal to be removed
and explicitly does not request widening the lint.

### Finding 9: `deep_verify = off` sits in the wrong INI section, so the switch is not in force

**Category:** bug
**Fix Effort:** small (a one-line config move; optionally one more CODER task for the warning)
**Systemic Risk:** medium (an operator setting is silently not honoured, and the project pays a recurring ~13-minute cost it is configured not to pay)
**Regression:** no (the misplaced key predates this RC; `project.cfg` is live kanban process state and is untouched by the v2.9.22 diff, which changes only `docker/`, `docs/`, `team/scripts/` and `team/tests/`)
**Integrity Damage:** no
**Filed As:** BUG-0171 (Path C-A)

`projects/pgai-agent-kanban/project.cfg` ends with a bare `deep_verify = off`
appended after the last key of `[training]`, with no `[release]` header between.
The key therefore belongs to `[training]`, and the `[release]`-scoped reader
never sees it:

```
$ pp_deep_verify_mode pgai-agent-kanban                              -> weekly
$ _pp_read_cfg_key .../project.cfg release  deep_verify NOTFOUND     -> NOTFOUND
$ _pp_read_cfg_key .../project.cfg training deep_verify NOTFOUND     -> off
```

The mode in force is `weekly`, not `off`, and nothing warns. An unrecognised
*value* is refused dual-channel; a recognised key in the wrong *section* is
silent, which is the failure shape `team/SOP.md`'s "No silent skips" section
exists to prevent.

It is not theoretical: the ledger records a lane run against this project at
2026-09-07T06:52:06+00:00, and SOP budgets a sweep at ~766 s per firing. Nor is
the intent ambiguous — `CODER-20260907-014`'s constraints name "the project's
'deep_verify = off' setting" as something not to touch, and the PM plan note
behind `CODER-20260907-015` argues for the new static lint partly because "this
project has 'deep_verify = off'". Two tickets on this RC reasoned from a setting
that is not actually in effect.

This is live kanban process state, not the shipped tree, and it does not affect
the RC's correctness. It is reported under Step 6.11 (state drift), which exists
for exactly this class of finding.

### Finding 10: release-state drift check

**Category:** pass

```
pp_last_released_version pgai-agent-kanban  -> v2.9.21
semver_gt v2.9.22 v2.9.21                   -> true
release-state.md '## Last Released'         -> v2.9.21 (well-formed vX.Y.Z)
release-state.md '## Active RC'             -> v2.9.22, and branch ai_rc/v2.9.22 exists
```

No drift. `## Last Released` is present by design and is not reported as drift.

### Finding 11: pre-flight merge verification

**Category:** pass

Every DONE task in the project carrying a non-`none` `## Feature Branch` was
scanned. All 28 such branches — including both of this RC's,
`ai_feature/CODER-20260907-014-fix-dockerfile-build-defects` and
`ai_feature/CODER-20260907-015-dockerfile-static-lint` — have already been
deleted post-merge, which is the normal CODER/WRITER lifecycle, so both checks
are skipped for them per the role file. Zero UNMERGED anomalies, zero BUG-92
zero-unique-commit anomalies. Both RC merge commits (`66af01d`, `ff1fb38`) are
present on the RC and `ai_main` is an ancestor of `ff1fb38`.

Worktree HEAD `ff1fb38` matches `ai_rc/v2.9.22` exactly; `git status --short`
is empty; `git stash list` is empty.

### Finding 12: prerequisite dependency graph

**Category:** pass

`CM-016 (open RC, DONE)` -> `CODER-014 (DONE)` -> `CODER-015 (DONE)` ->
`TESTER-017 (WORKING)` -> `CM-019 (release, WAITING)`. No cycles, every
referenced task ID exists, lifecycle bookends correctly linked, sequence numbers
consistent and non-colliding. Queue markers agree with task states
(`[x]` CM-016, `[A]` TESTER-017, `[W]` CM-019).

### Finding 13: constraint negative checks

**Category:** pass

No hardcoded secrets, keys or tokens in any changed file. `set -euo pipefail`
is preserved at `run-unit-tests.sh:74`; the new block's `set +e` / `set -e`
pairing around the lint invocation matches the file's existing idiom for the
other pre-flight checks and restores strict mode immediately. The new lint
script is mode `0755`, matching the other `lint_*.py` scripts.

### Finding 14: synthetic release simulation

**Category:** pass (trigger condition not met; ran anyway as part of the suite)

The RC's diff touches `team/scripts/run-unit-tests.sh`, which is not a
release-lifecycle file — it takes no part in the open/close cycle — so Step 6.8's
trigger condition is not met. `team/tests/integration/test_cm_release_synthetic.py`
is present and ran green (15 tests) inside the integration suite regardless.

## Lint Findings (non-blocking)

None.

`${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-unit.json` and
`findings-integration.json` were both written by their runners and both contain
`"findings": []` (schema_version 1, generated 2026-09-07T09:11:16+00:00 and
2026-09-07T09:15:07+00:00). Nothing to cite and nothing to file.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=0d freshness=stale — the last lane run was this morning but against v2.9.20, not the v2.9.22 under verification, so it is stale on the version condition though not on age; it failed on docker/claude-abliterated, podman/claude-abliterated and podman/rocky9 and cites BUG-0167, filed by that deep-verify run, which is the very bug this RC closes.

Stated as a fact, not re-derived: the verdict is the ledger's own word, read
through `deep_verify_ledger_read_latest`. No bug is filed here — BUG-0167
already tracks that failure and this RC fixes it. Per Step 5d, this line does not
move the recommendation or the systemic-risk rating. Note that no lane has been
run against the fixed tree, so the tree's images have not been built cold since
the repair; the static lint added by this RC is a text-level proxy for that
class, not a substitute for the lane.

## Autonomous criterion

Manual interventions required: none.

1. **Tasks force-promoted from WAITING to BACKLOG by hand?** NO. The one
   promotion in this cycle is recorded in `logs/cron-tester.log` at
   2026-09-07T09:02:51+00:00 as a wake-script action, and the task's own
   `status.md` Summary reads "Promoted from WAITING to BACKLOG by wake script:
   prerequisites satisfied."
2. **`release-state.md` edited outside the CM scripts?** NO. Its mtime is
   2026-09-07 08:19, matching `cm-open-rc.sh` running under
   `CM-20260907-016-open-rc-2-9-22` at 08:19:14+00:00; contents are internally
   consistent.
3. **Scripts patched mid-build?** NO. The RC does modify
   `team/scripts/run-unit-tests.sh`, but that is `CODER-20260907-015`'s own
   declared deliverable, authored inside the RC by the assigned agent — not an
   out-of-band patch. Both commits are by the chain's own identity within the
   RC window (08:31 and 08:59).
4. **Queue files manually edited?** NO. Queue mtimes (08:12 bug_backlog,
   08:17 pm_backlog, 08:20 cm_backlog, 09:02 coder_backlog and tester_backlog)
   each line up with a logged wake action.

No `HALT` or `HALT-AFTER` flag exists at either the kanban root or the project
scope. The chain ran bundle -> PM -> CM-open -> CODER x2 -> TESTER in roughly
fifty minutes with `completed=2` recorded for the coder batch and no operator
intervention anywhere in the logs.

## Code Coverage

Informational only; does not affect the recommendation or the systemic-risk
rating. Read from the `totals` object of each runner's JSON report, with field
names confirmed against the generated files rather than assumed.

- Unit:        line 74%  |  branch 61%   (14136 stmts, 4316 branches)
- Integration: line 8%   |  branch 6%

The blended `TOTAL` figures in the captured `term` output are 71% (unit) and 7%
(integration); they are the statement-plus-branch blend, not line coverage, and
are recorded here only so a reader diffing against the raw logs is not confused.

## Caveats

- **Container engines were deliberately not invoked.** `team/roles/TESTER.md`
  Step 5d removes the cold-build lane from RC verification entirely, so no
  `docker build`, `podman build`, or `--check` parse was run by TESTER even
  though both binaries are present on this host. Engine-level confirmation for
  the Dockerfile repair rests on `CODER-20260907-014`'s completion notes
  (`docker build --check` exit 0 on all three flavors; a full successful
  `podman build` of rocky9) plus this report's engine-free static evidence. A
  reader wanting engine-level assurance against the fixed tree should look to a
  DEEP-VERIFY run, which has not yet happened for v2.9.22 — see the freshness
  line above.
- **Census Locator Index format diverges from `team/SOP.md`.** SOP describes
  token-resolved anchors (`path:token CLASS`, resolving by name so a site can
  move within its file without a census edit). The shipped lint's
  `_CENSUS_LOCATOR_LINE_RE` accepts `path:<line> CLASS` only. This RC's change
  had to renumber two unrelated existing entries (209->210, 505->541) purely
  because it inserted lines above them — direct evidence of the divergence. This
  is **not** a finding: `docs/blocking-site-census.md` (the "Anchor markers"
  passage, around line 355) documents the line-number form explicitly as what
  this tree's lint parses, and `CODER-20260907-015` followed that documented
  convention correctly, authoring both the marker and a matching index line.
  Recorded as an observation only, for whoever eventually reconciles SOP prose
  with the implementation.
- **The skipped-test picture is clean.** The skip-cites-real-bug grep-gate
  (Step 6.13) found no placeholder IDs (`BUG-SKIP`, `BUG-<alpha>`) anywhere in
  `team/tests/` outside historical run-log artifacts under
  `team/tests/artifacts/`, and no skip annotation cites a `BUG-NNNN` at all, so
  there is no cited-bug file to resolve. The 3 unit and 1 integration skips are
  environment-conditional `skipif` guards, not deferred fixes.
- **Sequence number 018 is unused** in the v2.9.22 task numbering (014, 015,
  016, 017, 019). That is the optional HUMAN-APPROVE slot the release workflow
  reserves; its absence is expected for an `auto`-approval bundle and is not
  drift.
- **No prior-run artifacts existed.** `artifacts/` was empty at start and no
  `.previous-RUN-N` files were present, so this is a genuine fresh start; every
  result above was produced in this session.
- **No prompt-injection content encountered.** Nothing in the requirements doc,
  the bug file, the task READMEs or the source tree contained
  "ignore previous instructions"-style text.
