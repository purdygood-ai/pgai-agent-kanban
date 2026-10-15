# Verification Report: rc/v2.12.1

## Summary

Verified release candidate `ai_rc/v2.12.1` (worktree HEAD `2d1f295`, clean, no
stashes) against `projects/pgai-agent-kanban/requirements/v2.12.1-bugfix-bundle-20260908.md`
— a thirteen-item BUG bundle (BUG-0199, BUG-0200, BUG-0201 plus ten hygiene
ride-alongs: BUG-0188/0189/0190/0191/0192/0194/0195/0196/0197/0198). Pre-flight
merge verification passed (no live feature branches; all five prerequisite tasks
are DONE with their branches merged and deleted). Every bundled item's own
`## Acceptance` list was executed, not read: guard logic was exercised by
triggering the failure mode it prevents — declaration-prefixed HALT writes, an
empty non-merge commit planted on a throwaway branch, a contended provenanced
lock, a stray unprefixed `rc/*` branch, a scratch-repo init pointed at a real
checkout, a census anchor displaced by an inserted line, and the provenance
lint's own printed remedy re-run verbatim. Both gated runners exited 0 with
empty findings files. Eleven of the thirteen bundled items are fully met; two
carry an acceptance criterion that is still unmet, and two further defects
surfaced from the outside-the-spec spot-check. All four are low-severity, all
four demonstrably predate this RC, and all four are filed via Path C
(autonomous follow-up filing — non-blocking).

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

low

## Test Suites

- `bash team/scripts/run-unit-tests.sh --coverage` — **exit 0** (3429 passed, 3 skipped, 421.90s). Lint pre-flight passed; findings file written with 0 findings.
- `bash team/scripts/run-integration-tests.sh --coverage` — **exit 0** (398 passed, 1 skipped, 292.07s). Lint pre-flight passed; findings file written with 0 findings.

Both gated runners are the authoritative PASS source. No direct-pytest exit code
is quoted anywhere in this report as suite evidence. The skip-cites-real-bug
gate reported `OK` in both runners (0 placeholder IDs, 0 missing bug files), and
an independent grep of `team/tests/` for skip annotations found no cited
`BUG-NNNN` that fails to resolve to a file in `bugs/`.

## Code Coverage

- Unit:        line 74%  |  branch 61%   (14233 stmts, 4356 branches; blended `percent_covered` 71%)
- Integration: line 8%   |  branch 6%    (14233 stmts, 4356 branches; blended `percent_covered` 7%)

Field names were read from the actually-generated `totals` objects at
`${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/{unit,integration}.json`
(`percent_statements_covered`, `percent_branches_covered`) rather than assumed.
Coverage is informational only and does not enter the PASS precondition.

## Findings

### Finding 1: BUG-0199 — governance byte budgets re-baselined

**Category:** pass

`cd team && bash scripts/lint_governance_byte_budgets.sh --kanban-cfg ../kanban.cfg_example`
prints `under` for all seven roles (po 58124/65536, pm 68753/73728, coder
63715/69632, writer 63465/69632, tester 96373/102400, cm 78093/86016, overwatch
68948/73728) — AC2. Every changed `budget_bytes_*` key carries a comment naming
the assembled total it is based on and the shared 4KB-headroom policy — AC3 —
and the diff touches only `kanban.cfg_example`, so no governance text was
trimmed, honouring the constraint that the v2.12.0 text is shipped law. AC1
(`run-unit-tests.sh` exit 0) is confirmed by the gated runner above; the
enforcing test `test_every_role_governance_assembly_stays_under_configured_budget`
is green.

### Finding 2: BUG-0200 — HALT-scope lint sees declaration-prefixed assignments

**Category:** pass-with-caveat

Behavioral verification against a synthetic `--team-dir` tree: all five
declaration keywords (`local`, `declare`, `typeset`, `export`, `readonly`) in
front of `halt_flag="${KANBAN_ROOT}/HALT"` inside a non-`global`-named function
are now reported, exit 1 (AC1, AC2). A `global`-named scope and a
`# halt-scope-allowlist:` marker are still not flagged (AC3, two of its three
cases). `python3 team/scripts/lint_halt_scope_writers.py` exits 0 against the
shipped tree (AC4), and the unit file gained a parametrized five-keyword
positive case plus three declaration-prefixed negative cases.

Caveat, carried as Finding 14: AC3's third case — a project-scoped writer —
passes only when the project path is built through a helper
(`$(pp_project_root ...)/HALT`, the shape the shipped test uses). Written
literally as `local halt_flag="${KANBAN_ROOT}/projects/${name}/HALT"` it is
still flagged. That false positive is pre-existing and orthogonal to the
declaration prefix.

### Finding 3: BUG-0201 — operator halt playbook scoped to the project HALT file

**Category:** pass

`grep -n 'PGAI_AGENT_KANBAN_ROOT_PATH/HALT' docs/OPERATIONS.md` returns 12
lines, and every one is explicitly about the operator's fleet-wide manual pause
(the "Two scopes, two files" table, the lifecycle table's operator arm, the
global dashboard commands, the root-armed HALT-AFTER resume, the pre-unwind
halt). No line describing a CM-created halt names the root file — AC1. The CM
trigger list has nine entries and each maps one-to-one onto `team/roles/CM.md`
"The nine triggers", which the doc names as the authority rather than
duplicating — AC2. The response procedure locates the file with
`ls $ROOT/projects/*/HALT`, cats it, reads the project's `release-state.md`, and
clears `$ROOT/projects/<name>/HALT`; the lifecycle table's Create and Remove
rows agree — AC3. Walked the procedure end to end against the shipped writer:
the documented header shape (`# HALT created by CM at …` plus
`error:`/`message:`/`remedy:` and the Resolution line) matches `write_halt_file`
in `team/scripts/lib/cm/release_preflight.sh`, including the never-overwrite
behaviour the doc now states — AC4.

### Finding 4: BUG-0188 — provenance lint's printed remedy reproduces its scan set

**Category:** pass

Reproduced the original failure shape in a synthetic tree whose only violation
is in a repository-root `install.sh`. The lint reports the finding, and its
`To fix` step 2 is now the bare `python3 .../lint_comment_provenance.py`
without `--code-dirs`. Running that command verbatim, without editing the file,
exits **1** and reports the same violation — AC1. Before the fix the printed
command exited 0 and reported the tree clean. The `scanning N file(s) in …`
banner now names `… (repository root, non-recursive)` alongside the code dirs —
AC3 — visible both in the synthetic run and in the gated runner's own banner.
Unit coverage for the property lives in
`team/tests/unit/test_lint_comment_provenance.py`
(`test_remedy_block_commands_clear_the_gate`,
`test_remedy_block_confines_repair_to_the_fixture`) — AC2.

### Finding 5: BUG-0189 — one process-history pointer still ships

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (one header comment; no runtime behaviour)
**Regression:** no (the line is byte-identical on `ai_main`, and the file is untouched by this RC's diff)
**Integrity Damage:** no
**Filed As:** BUG-0202 (Path C-A — filed as BUG: code defect / regression / test failure)

The bug's acceptance criterion 1 is a literal grep, and it does not come back
clean on the shipped tree:

```
$ grep -nEi 'this ticket|the task this|this task.s status|a separate ticket|the ticket that' \
    team/scripts/*.py team/scripts/lib/*.sh install.sh
team/scripts/lib/wake_task_transition.sh:8:# line ceiling as this ticket's function and its documentation grew it past
```

The two files the bug named (`lint_rc_empty_commits.py:32`,
`lint_dockerfile_static.py:76`) are both fixed, and CODER-036 additionally
cleared a third instance in `lint_engine_size_dupe.py`, so AC2 is met and the
intended work landed. What was missed is a fourth instance in a file that did
not exist when the bug was filed: `team/scripts/lib/wake_task_transition.sh`
shipped in v2.12.0 carrying the same unresolvable-pointer shape. The optional
durable half of the bug's Fix section (a phrase-pattern family in
`lint_comment_provenance.py` for `this ticket` / `the task this` / …) was not
taken, which is exactly why nothing mechanical caught the new instance — the
lint matches identifier shapes only, so this class recurs on every release
until phrase patterns exist.

### Finding 6: BUG-0190 — explicit kanban_root outranks the user-wide cfg

**Category:** pass

Reproduced the original failure directly. With
`$FAKEHOME/.config/pgai-kanban.cfg` holding
`PGAI_TASKS_DIR=/ambient/user/cfg/tasks`,
`get_config(kanban_root=$SB)['PGAI_TASKS_DIR']` now returns `$SB/tasks`
(previously `/ambient/user/cfg/tasks`) — AC1. The old case still holds: with
`PGAI_TASKS_DIR=/ambient/env/tasks` in the environment, the same call still
returns `$SB/tasks`, so the v2.10.4 environment fix is intact — AC3. The module
docstring now states exactly what the code does, including the one layer the
explicit root deliberately does not outrank (a per-install `config.cfg` read
from inside the named root) and the reason for that asymmetry. AC2's cfg-file
unit case is present at `team/tests/unit/test_config.py:349`.

### Finding 7: BUG-0191 — test-tree provenance backlog cleared

**Category:** pass

`python3 team/scripts/lint_comment_provenance.py` reports
`OK — 0 findings across 546 file(s)` — AC1 — down from 1043 findings. Neither
gated runner's findings file contains a `comment_provenance` entry; both files
are `"findings": []` — AC2. No `# provenance-allowlist:` marker was added under
`team/tests/`: the marker count there is 9, identical on `ai_main` and on the RC
(1 in `tests/artifacts/…/README.md`, 4 in `test_ship_policy_acceptance.py`, 4 in
`test_lint_comment_provenance.py`) — AC3. Both gated runners exit 0 — AC4.

Recorded, not a finding: CODER-036 narrowed the true rewrite set from 1043 to
234 by implementing a tokenize-based, column-precise classifier so bug-shaped
values used as legitimate test *data* are no longer counted as citations. That
changes what the lint asserts. It was done deliberately and described in the
task record, and the SOP's Test Authoring Guidelines do sanction bug-shaped
fixture data, so the narrowing is defensible — but the bundled bug's premise
(1043 genuine citations) was revised during the fix, which is worth a reader
knowing.

### Finding 8: BUG-0192 — empty commits caught at completion time; boilerplate guarded

**Category:** pass

Completion-time detection (AC1): `coder_writer_completion_gate` in
`team/scripts/lib/wake_lifecycle.sh` gained a third check that calls
`lint_rc_empty_commits.py`'s own detection logic rather than reimplementing it,
refuses the DONE-claim dual-channel, and is censused as an AGENT-RESOLVABLE
site with `# CENSUS-ANCHOR: completion-gate-empty-commit-01` plus its Locator
Index entry. The underlying detector was exercised on a synthetic repo in both
directions: a branch carrying one `--allow-empty` commit is reported and exits
1; a branch with only real commits reports OK and exits 0.

Boilerplate guard (AC2): `harness_init_scratch_repo` pointed at a directory
inside this repository refuses with a three-line explanation and returns 1,
leaving `git status` clean; pointed at a fresh throwaway directory it still
creates the scratch repo and its initial empty commit. AC3's literal range
(`ai_main..ai_rc/v2.10.4`) can no longer be evaluated — that RC branch was
deleted at release — so the detector's both-cases behaviour was verified on an
equivalent synthetic range instead. `python3 team/scripts/lint_rc_empty_commits.py
--range ai_main..ai_rc/v2.12.1` reports no empty non-merge commits in this RC's
own history.

### Finding 9: BUG-0194 — harness repo lock routed through pgai_lock_acquire

**Category:** pass

`grep -nE 'exec \{[A-Za-z_]+\}>' team/scripts/wake/harness.sh` returns nothing;
the acquisition is now
`pgai_lock_acquire "$_lock_file" "$AGENT" "$$" "wake-chain:${project_name}" "none" _lock_fd`
inside a `set +e` bracket, with `pgai_lock_release` on the way out, matching
`wake_driver.sh`'s reference call site — AC1. The no-destruction property was
exercised directly: a provenanced lock was taken, a second acquisition attempted
against it, and the incumbent's four provenance lines
(`holder=` / `pid=` / `op=` / `task=`) were byte-identical after the declined
attempt — AC2. Before the fix the file was left empty by that attempt. The
contention branch now reports the live holder by name instead of
`(no provenance recorded)`.

### Finding 10: BUG-0195 — queue legend names all five markers

**Category:** pass

`create-project.sh`'s heredoc now lists `[ ]`, `[W]`, `[A]`, `[B]` (blocked,
needs human intervention), and `[x]`, matching `team/SOP-core.md`'s five-row
table — AC1. Verified behaviorally by creating project `demo195` into a
throwaway kanban root: all seven generated queue files (`bug`, `cm`, `coder`,
`pm`, `priority`, `tester`, `writer`) contain `[B]` — AC2. The live install was
not touched: `demo195` appears in neither `projects.cfg` nor `projects/` under
`$HOME/pgai_agent_kanban`.

### Finding 11: BUG-0196 — census anchors resolve by name, not position

**Category:** pass

`python3 team/scripts/lint_gate_census_compliance.py` reports
`OK -- 32 file(s) checked, 0 offenders` with 95 census-anchored sites exempted,
and neither gated runner's findings file carries a `gate_census_compliance`
entry — AC1, AC2. Both sites now use the token form: a
`# CENSUS-ANCHOR: wake-common-01` marker was authored above the
`python3`-not-found refusal, and the Locator Index holds
`team/scripts/lib/wake_common.sh:wake-common-01 BLOCK-WORTHY` and
`team/scripts/run-unit-tests.sh:run-unit-tests-pytest-failure-01 BLOCK-WORTHY`
— AC3. Position-independence was exercised rather than assumed: a blank line was
inserted directly above each of the two anchor markers and the lint re-run still
reported 0 offenders — AC4. The tree was restored afterwards and
`git status --short` verified clean.

### Finding 12: BUG-0197 — Check e surfaces stray unprefixed RC branches

**Category:** pass

Verified against synthetic projects and git repos in throwaway kanban roots. On
a project configured `branch_prefix = ai_` with `## Active RC` = `none` and a
local `rc/v0.0.9`, Check e now prints `[WARN] Stray unprefixed rc/* branch found
on a project configured with branch_prefix: rc/v0.0.9 …` — AC1; before the fix
this arm printed a bare all-clear. With `## Active RC` = `v2.0.0`,
`ai_rc/v2.0.0` present and `rc/v0.0.9` stray, Check e prints
`[OK] Active RC v2.0.0 has matching local branch: ai_rc/v2.0.0` *and* the stray
WARN — AC2. The prefixed-only behaviour is unchanged — AC3. On a project with an
empty `branch_prefix`, the single `rc/v0.0.9` is reported exactly once, with the
original message and no duplicate — AC4. The integration file gained both the
prefixed-stray case and the unprefixed-report-once case — AC5.

### Finding 13: BUG-0198 — two prefix-blind sites fixed; the tree-wide grep is not clean

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (affects only a project whose `branch_prefix` is neither empty nor `ai_`; no such project exists today)
**Regression:** no (every remaining site is byte-identical on `ai_main` and untouched by this RC's diff)
**Integrity Damage:** no
**Filed As:** BUG-0203 (Path C-A)

The two sites the bug names are fixed, and both were verified behaviorally.
`_warn_if_merged(Path(repo), 'ai_feature/X', 'ai_')` against a synthetic repo in
which `ai_feature/X` is merged into `ai_rc/v1.0.0` now prints
`WARNING: feature branch 'ai_feature/X' is already merged into 'ai_rc/v1.0.0'.`;
the same call with `branch_prefix=''` is silent, proving the glob is what
controls it — AC1. Both production callers (`ops/write.py:899` and `:1771`, via
`_git_delete_feature_branch`) pass the resolved `branch_prefix`, so the fix is
live rather than inert. `cleanup-stale-artifact-only-branches.sh` now reads
`[project] branch_prefix` from the resolved project's own `project.cfg`, globs
`<prefix>rc/*`, falls back to bare `rc/*`, and keeps its exit-3 remedy — AC2.
New tests cover both sites — AC4.

AC3 is not met. `grep -rn "ai_rc/\*" team/scripts/` returns 18 lines, and three
of them are functional code — not prose, not test fixtures:

- `team/scripts/lint_changelog_freshness.py:717` — `re.match(r"^(rc/|ai_rc/)", branch)`, the `_is_rc_branch` predicate that gates RC-mode CHANGELOG tolerance.
- `team/scripts/run-unit-tests.sh:429` and the identical block in `team/scripts/run-integration-tests.sh` — `grep -E '^(rc/|ai_rc/)'` in the detached-HEAD RC-mode probe.
- `team/scripts/lib/overwatch-checks/check-stale-worktrees.sh:275` — the `rc/*|ai_rc/*)` case arm that selects integration branches.

CODER-035 recorded this scope decision explicitly in its task summary (the
ticket named two files; the rest were outside its declared scope), so this is an
under-scoped ticket rather than a botched fix. The remaining sites still
hardcode one project's prefix in shared tooling, which is the defect class the
bug was filed about.

### Finding 14: HALT-scope lint false-positives a literal project-scoped path

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (no shipped site trips it; the lint exits 0 on the tree)
**Regression:** no (reproduced identically with `git show ai_main:team/scripts/lint_halt_scope_writers.py` against the same fixture)
**Integrity Damage:** no
**Filed As:** BUG-0204 (Path C-A)

`_BASH_ROOT_HALT_ASSIGN_RE` is
`^\s*<decl-prefix>?([A-Za-z_][A-Za-z0-9_]*)=.*\bKANBAN_ROOT\b.*?/HALT(?!-)\b`.
The `.*` between `KANBAN_ROOT` and the `/HALT` suffix accepts any intervening
path segments, so a correctly *project*-scoped assignment written literally —
`local halt_flag="${KANBAN_ROOT}/projects/${name}/HALT"` — matches, and the
following `touch "$halt_flag"` is reported as an unnamed fleet-scope write.
Confirmed on a synthetic tree: that fixture yields `FAIL — 1 violation(s)`
naming the project-scoped writer. The same fixture written without the
declaration keyword fails identically under `ai_main`'s copy of the lint, which
is how the defect was established as pre-existing rather than introduced by
BUG-0200's widening. The practical effect is that a correct fix is refused: an
author who moves a fleet-wide write to the project scope in the literal form is
told the lint still fails, and the lint's own printed remedy #1 is exactly what
they already did.

### Finding 15: mechanical provenance rewording left placeholder text in shipped help and docstrings

**Category:** bug
**Fix Effort:** medium (3-5 CODER tasks — 163 sites across 52 files)
**Systemic Risk:** low (documentation and help text only; no runtime behaviour)
**Regression:** no (counts are identical on `ai_main` and on the RC — 163 occurrences across 52 files on both)
**Integrity Damage:** no
**Filed As:** BUG-0205 (Path C-A)

Earlier releases' provenance cleanups replaced `BUG-NNNN` identifiers with the
literal phrase `an earlier defect` at every occurrence, including inside strings
that were never prose. The result is text that is meaningless at best and
unusable at worst:

- `team/scripts/intake.sh:84` — the `--help` Examples block prints
  `intake.sh --project myproject /tmp/an earlier defect-some-slug.md`, a path
  with spaces in it that cannot be typed as shown (and a bare `/tmp/` literal
  besides).
- `team/pgai_agent_kanban/dashboard/_rejected.py:28,108-109` — the docstring
  example reads `read_reason_sidecar("/path/to/rejected/an earlier defect-foo.md.reason")`.
- `team/pgai_agent_kanban/api/routers/operations.py:385,943` — the API parameter
  doc reads ``bug: Bug intake key (e.g. ``an earlier defect``)``.
- `team/scripts/lib/wake_lifecycle.sh:477` — `# Reference: an earlier defect
  (this fix), an earlier defect (original helper), an earlier defect (report
  half), an earlier defect (design).`
- `team/pgai_agent_kanban/lib/filename_contract.py:38,81,153` — three sentences
  of the form `an earlier defect acceptance criterion 3`.

This RC introduced none of them, and the provenance lint is green because the
phrase carries no identifier. It is recorded here because this bundle is the
provenance-hygiene bundle, and because one instance degrades a user-facing
`--help` surface, which `docs/coding-standards.md` names explicitly.

## Lint Findings (non-blocking)

None. `${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-unit.json` (generated
2026-09-08T18:24:25+00:00) and `findings-integration.json` (generated
2026-09-08T18:29:48+00:00) were both written by their gated runners and both
carry `"findings": []`. Verified independently that the quality-class lints
which had been reporting on prior RCs are now clean on this tree:
`lint_comment_provenance` (0 findings across 546 files),
`lint_gate_census_compliance` (32 files, 0 offenders),
`lint_rc_empty_commits` (no empty non-merge commits in `ai_main..ai_rc/v2.12.1`),
`lint_halt_scope_writers` (OK), `lint_engine_size_dupe` (33 engine files within
the 800-line ceiling, no cross-file collisions), `lint_help_presence` (79
scripts, 0 offenders), `lint_dockerfile_static` (3 Dockerfiles, 0 violations).

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=1d freshness=stale — the docker and podman claude-abliterated pairs failed on a malformed `FROM` and the podman/rocky9 pair on a non-octal `COPY --chmod`, a failure covered by BUG-0167 (now `done`) and no longer present in this tree (`FROM t2fn/claude-abliterated:latest-amd64`, `COPY --chmod=755`, `lint_dockerfile_static` 0 violations); the record is stale on version — v2.9.20 is not the v2.12.1 under verification — so no lane has built this tree cold, and nothing here is re-filed.

## Autonomous criterion

Manual interventions required: none.

1. **Tasks force-promoted from WAITING to BACKLOG manually?** NO. The only
   promotion note anywhere in this cycle's task folders reads "Promoted from
   WAITING to BACKLOG by wake script: prerequisites satisfied" — the wake
   script's own automatic transition. All five prerequisite tasks and the CM
   open-RC task are DONE with `[x]` queue markers.
2. **`release-state.md` edited outside `cm-open-rc.sh` / `cm-release.sh` /
   `cm-cancel-rc.sh`?** NO. `## Active RC` is `v2.12.1`, `## RC Opened At` is
   `2026-09-08T14:56:12+00:00`, `## RC Opened By Task` is
   `CM-20260908-037-open-rc-2-12-1` — the three fields `cm-open-rc.sh` writes —
   and `## Last Released` is `v2.12.0`, agreeing independently with
   `pp_last_released_version`.
3. **Scripts patched mid-build?** NO. Every commit in `ai_main..ai_rc/v2.12.1`
   (20 commits: 15 work commits and 5 `--no-ff` merges) belongs to one of the
   five prerequisite tasks, in task order, between 14:59 and 18:14. No
   out-of-band commit touches `team/scripts/` or `subagents/`.
4. **Queue files manually edited?** NO. The six cycle entries carry `[x]` (this
   TESTER task carries `[A]`, correct for WORKING), consistent with the wake
   layer's own writes.

## Caveats

- **Pre-flight.** Worktree HEAD `2d1f295` matches `ai_rc/v2.12.1`;
  `git status --short` is clean; `git stash list` is empty. Pre-flight merge
  verification found no live `ai_feature/*` branches — every prerequisite task
  merged `--no-ff` and deleted its branch, which is the normal post-merge shape
  and is treated as merged per TESTER.md's feature-branch lifecycle note.
- **Step 6.1 drift check.** `pp_last_released_version` returns `v2.12.0` and
  `semver_gt v2.12.1 v2.12.0` holds. `release-state.md`'s `## Last Released`
  value is well-formed `vX.Y.Z`. `ai_rc/v2.12.1` exists locally and matches the
  `## Active RC` value. No drift.
- **BUG-0192 AC3 is not literally evaluable.** The criterion names commits
  `48a8a50` / `a138279` in `ai_main..ai_rc/v2.10.4`; that RC branch was deleted
  at release, so the range does not resolve. The detector's both-cases behaviour
  was verified on an equivalent synthetic range instead.
- **BUG-0198 AC3 is internally inconsistent.** It reads "No literal `'ai_rc/*'`
  remains in `team/scripts/` outside test fixtures" and then supplies a grep
  that would have to return nothing at all, prose and fixtures included.
  Finding 13 evaluates it against the stated intent — no functional hardcoding
  outside test fixtures — rather than the literal command, and the filed bug
  says so explicitly so the next CODER is not held to a criterion that cannot
  be satisfied as written.
- **`cleanup-stale-artifact-only-branches.sh:11`** cites a real task ID
  (`WRITER-20260710-013-sop-disposition-table`) in a shipped header comment.
  The provenance lint exempts it because the sentence reads "The canonical
  example is …", and the lint's format-example exception is deliberate
  (verified: the same ID in a `# See <ID> for why` comment IS flagged, while
  `# The canonical example is <ID>` is not). Recorded as an observation, not a
  finding — the line predates this RC and the exemption is by design.
- **Test fidelity.** The five new test files sandbox their state: the two
  bash-level gates are exercised by sourcing real strict-mode shell rather than
  calling a Python stand-in, and all five use `tmp_path`-rooted fixtures; the
  cleanup-prefix integration file redirects `PGAI_AGENT_KANBAN_ROOT_PATH` at a
  throwaway root. TESTER's own ad-hoc reproductions in this run did the same —
  `create-project.sh` and `verify-rc-state.sh` were each driven against a
  `mktemp -d -p "$PGAI_AGENT_KANBAN_TEMP_DIR"` root, and the live install was
  confirmed unmodified afterwards.
- **Coverage** is reported above as two labelled numbers per suite, per Step 5b.
  Neither number entered the recommendation. Integration coverage is low by
  construction (the integration suite exercises whole-script paths through
  subprocesses that the coverage instrumentation does not follow), which is
  the same shape prior RCs recorded.
