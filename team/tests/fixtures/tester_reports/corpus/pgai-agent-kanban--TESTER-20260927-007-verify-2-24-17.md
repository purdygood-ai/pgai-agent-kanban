# Verification Report: rc/v2.24.17

Verified the release candidate `ai_rc/v2.24.17` (HEAD `05d5866`, confirmed
equal to `git rev-parse ai_rc/v2.24.17`) against
`requirements/v2.24.17-bugfix-bundle-20260927.md` and the five bug documents
it bundles. The RC is a five-item bug bundle carried by three CODER merges:
`77fcc16` anchors the git boundary guard's temp-root normalisation
(`BUG-0398`), `b0bad07` makes the hygiene bug filer refuse to file when no
commit resolves for the scanned tree (`BUG-0401`), and `b019f3b` renames five
provenance-named guard test sites and corrects two stream-mismatch docstrings
(`BUG-0399`, `BUG-0400`). A fourth ticket
(`CODER-20260927-005-provenance-sweep`, the `BUG-0397` sweep) concluded
`WONT-DO` with zero commits, correctly: the three sites that bug cites carry no
bug-ID citation at any reachable commit, which is exactly the unactionable
filing `BUG-0401`'s fix exists to prevent. Both gated runners exited 0 with
zero lint findings; every executable acceptance criterion in the requirements
doc and in `BUG-0399`, `BUG-0400` and `BUG-0401` passes, and `BUG-0401`'s fix
was exercised behaviourally against both its original failure mode and the new
normal case. `BUG-0398`'s fix is real and its own new unit test was proven to
discriminate the pre-fix implementation from the fixed one, but two residual
inputs still widen the guard's exemption past the bound that bug's acceptance
names, and a third gap — the gated integration suite writing a boundary-guard
evidence record on every run — reopens a criterion `BUG-0396` closed in
v2.24.16. All three are filed; none is severe, none is a regression introduced
by this RC, and none is reachable under the shipped default configuration.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

medium

## Findings

### Finding 1: requirements acceptance criterion 1 — each bundled item addressed

**Category:** pass-with-caveat

All five bundled items are accounted for in the RC.

| Item | Disposition | Evidence |
|---|---|---|
| `BUG-0398` guard temp-root strip unbounded | fixed, partially | `77fcc16`; see Findings 6 and 7 for the residual |
| `BUG-0401` filer gates fail open without repo | fixed | `b0bad07`; behaviourally verified, Finding 4 |
| `BUG-0397` hygiene-lint provenance (ride-along) | nothing to fix | `CODER-20260927-005` status; lint reports 0 findings tree-wide |
| `BUG-0399` guard tests name internal bug id (ride-along) | fixed | `b019f3b`; Finding 2 |
| `BUG-0400` filer gate narration stream mismatch (ride-along) | fixed | `b019f3b`; Finding 3 |

The caveat is `BUG-0397`. Its disposition is correct and reasoned — the three
sites it cites (`git_boundary_guard.sh:543`, `project_paths.sh:662` and `:856`)
carry no `BUG-0396` citation at the RC tip, `python3 team/scripts/lint_hygiene.py`
reports `OK — 0 findings across 9 scan director(y/ies)` with all three classes
enabled, and a tree-wide grep for `BUG-0396` outside the two sanctioned
surfaces returns nothing. But the rationale lives only in
`CODER-20260927-005-provenance-sweep`'s `status.md`; the bug file itself will
be flipped to `## Status: done` by `cm-release.sh` step 16b with no note of
why nothing changed. A future reader of `bugs/BUG-0397-hygiene-lint-provenance.md`
sees a closed bug whose three findings were never reworded and no record of
the reason. Not filed — the disposition IS on the record, just not in the
place a reader of the bug would look.

### Finding 2: `BUG-0399` — provenance-named guard test sites renamed

**Category:** pass

Acceptance bullet 1: `grep -n "bug_0393\|_BUG_0393_" team/tests/unit/test_git_boundary_guard.py`
returns no hit (exit 1). All five sites are renamed:
`_BUG_0393_REPRO_INVOCATIONS` -> `_OPTION_VALUE_AHEAD_OF_REMOTE_INVOCATIONS`,
both `@pytest.mark.parametrize` references, and both test function names to
`test_option_value_ahead_of_remote_invocation_{outside_root_is_refused_and_ref_unchanged,inside_root_is_still_exempt}`.

Acceptance bullet 3: the renamed tests still cover the same four argv tuples
(`push --push-option`, `fetch --filter`, `pull -s`, `push -X`) — the
parametrize list is unchanged in content, only renamed. Both docstrings and
the preceding comment block now state what must hold rather than narrating
"before this fix".

Acceptance bullet 2 (no test name, constant name, or docstring in
`team/tests/` cites an internal `BUG-NNNN`, task ID, or RC version outside the
three sanctioned exceptions) holds for this ticket's scope. A tree-wide grep
finds remaining `BUG-NNNN` tokens only as synthetic sample values fed to
renderers and scanners (`BUG-0001`, `BUG-9999`, `BUG-1234`) — the sanctioned
"usage EXAMPLE where a bug-shaped VALUE illustrates an interface" — plus one
pre-existing test name, `test_bug_0023_file_carries_public_id_ki_1_0_0_1` in
`test_changelog_writer_real_data.py`, already dispositioned in
`team/tests/artifacts/protected-fixture-disposition-v2.3.0.md`. CODER scoped
the criterion to its own diff plus the five named sites and documented that
reading in `status.md`; the reading is defensible and the task's own Notes
ruled out an equivalent tree-wide pass.

### Finding 3: `BUG-0400` — filer stream-mismatch docstrings corrected

**Category:** pass

Acceptance bullet 1: the module docstring's "Commit-content proof gate"
section and `file_or_cite_hygiene_bugs`'s own docstring both now read
"stderr", matching `_note_unprovable_findings`'s `file=sys.stderr`. All three
sites agree.

Acceptance bullet 2: `grep -n "stdout" team/pgai_agent_kanban/lint/bug_filer.py`
returns exactly three hits — lines 402, 435 and 476 — every one a
subprocess's own captured `result.stdout`. No hit refers to this module's
narration. Criterion met exactly as written.

The new `_note_unresolvable_commit` added by `BUG-0401`'s fix also writes to
`sys.stderr`, so the correction was not undone by the sibling ticket.

### Finding 4: `BUG-0401` — filer refuses to file when no commit resolves

**Category:** pass

Exercised behaviourally per Step 6.5, against both the original failure mode
and the new normal case.

Original failure mode (`repo_root` is a plain directory, not a git
repository), the exact shape from `BUG-0401`'s reproduction:

```
[bug_filer] cannot resolve a commit for '<sandbox>/notarepo' -- it is not a
git repository, has no commits yet, or git is not on PATH. Filing nothing this
run: ...
RESULT: []
files filed: 0
```

New normal case (real git repository, cited token present at HEAD): one bug
filed, `## Observed At Commit` carrying the real 40-character SHA
`248f0a53e8558a166ade680938509aa48d31b0c1`, not the literal `unknown`.

Third case, the one `BUG-0401`'s acceptance also names (a real repository with
no commits yet): nothing filed, same narration. Acceptance bullets 1 and 2 met.

Acceptance bullet 5: `python3 team/scripts/lint_hygiene.py --classes provenance`
reports `OK — 0 findings` on the RC tree.

The fix's `_resolve_repo_root` precedence — explicit argument, then
`PGAI_DEV_TREE_PATH`, then the self-located guess — rests on a documented
claim that the gated runners export `PGAI_DEV_TREE_PATH` to their own scan
root. Verified rather than taken on trust: `run-unit-tests.sh:88`,
`run-integration-tests.sh:104` and `deep-verify.sh:345` all `export` it. The
claim is true.

`CODER-20260927-003`'s `## Instruction Conflicts` section records a genuine
contradiction inside `BUG-0401`'s own acceptance text — one of the three
v2.24.16 tests it said must "pass unchanged" asserted the very fail-open
behaviour another of its bullets bans. The conflict was resolved in favour of
the specific, unambiguous bullets and documented on the record. That is the
correct handling and is noted here so the resolution is visible in the
verification trail as well.

### Finding 5: `BUG-0398` — the demonstrated failure mode is closed and the new test discriminates

**Category:** pass

The depth instance `BUG-0398` demonstrated is refused on the RC. Behavioural
matrix through the shipped shim `team/scripts/wake/git-guard/git` under
`PGAI_AGENT_ROLE=coder`, pushing to a local bare repository outside the
configured root:

| Configured `PGAI_AGENT_KANBAN_TEMP_DIR` | exit | records | dest refs |
|---|---|---|---|
| `$HOME/projects/pgai/tmp` (the bug's own repro root) | **1** | **1** | **unchanged** |
| `<sandbox>$HOME/pgai`, dest outside widened parent | **1** | **1** | **unchanged** |

The refusal is dual-channel as required — `error: git-boundary-guard.non-cm-remote-verb`,
a plain-language `message`, and a runnable `remedy`.

Acceptance bullet 5 asked that the new unit test fail against the current
(pre-fix) implementation. Confirmed independently of CODER's claim, by
sourcing `ai_main`'s copy of the helper and the RC's copy side by side against
the new test's own fixture shape:

```
PRE-FIX(ai_main)  root=/sandbox/projects/myproj/scratch probe=/sandbox/outside/work -> EXEMPT
RC(v2.24.17)      root=/sandbox/projects/myproj/scratch probe=/sandbox/outside/work -> guarded
```

The test is a real regression test, not a tautology.

Acceptance bullet 2 (the fixture-noise reduction survives) also holds: with
the per-project root `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban`, a
destination under `/tmp/pgai_kanban_tmp/pytest/x` is still exempt.

Acceptance bullet 4: `docs/blocking-site-census.md` row 62 was rewritten to
describe the new bound, and `python3 team/scripts/check_docs_citations.py`
reports `ok — 24 governance file(s) scanned, 17 docs/ citation(s) checked`.

### Finding 6: a configured temp root of `/projects/<name>` exempts every path on the host

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (the fifth residual in a five-RC sequence on the same exemption boundary — the class recurs, though each RC narrows it)
**Regression:** no
**Integrity:** none
**Filed As:** BUG-0402 (Path C-A — filed as BUG: code defect / regression / test failure)

`BUG-0398` acceptance bullet 1 requires that with `PGAI_AGENT_KANBAN_TEMP_DIR`
set to a path containing a `projects` component **at any depth**, a non-CM
remote verb against a repository outside that root is refused, records exactly
one evidence record, and leaves the destination's refs unchanged. It is not met
at one input.

When the configured root is `/projects/<name>`,
`${canon_temp_root%/projects/*}` evaluates to the **empty string**, and the
comparison's fourth clause `[[ "$canon" == "$canon_framework_root"/* ]]`
degenerates to `[[ "$canon" == /* ]]` — true of every absolute path. Probed
directly against the shipped helper:

```
PGAI_AGENT_KANBAN_TEMP_DIR=/projects/name
  _git_boundary_guard_path_under_temp_root $HOME/develop/pgai-agent-kanban -> EXEMPT
  _git_boundary_guard_path_under_temp_root /etc                                   -> EXEMPT
```

Exercised end to end through the shim under `PGAI_AGENT_ROLE=coder` against a
repository whose `origin` is a real local bare repository:

| Invocation | verdict | records | dest refs |
|---|---|---|---|
| `push --push-option ci.skip origin main` | **not refused, exit 0** | **0** | **`refs/heads/main` created** |

`BUG-0398` bullet 3 is unmet at the same input: the function header says the
comparison "normalises UP exactly one level", and one level up from
`/projects/name` is `/`, but the code produces `""` and the glob built from it
matches the whole filesystem.

Pre-existing, not a regression: `ai_main`'s copy of the helper returns EXEMPT
for the same input, so the empty-root hole predates v2.24.16's normalisation
being narrowed. Unreachable under the shipped default
(`/tmp/pgai_kanban_tmp` has no `projects` component); the exposure is an
operator-configured root of the shape `/projects/<name>`, which
`kanban.cfg_example` and `shell-env_example` document as settable and which is
an ordinary layout on a single-purpose build host. `_git_boundary_guard_looks_like_real_remote`
still catches `https://` and `ssh://` destinations ahead of this helper, so the
exposure is specifically a destination resolving to a local filesystem path —
which is what every dev tree and local bare mirror on the host is.

Rated medium per `SOP-core.md` ("Rating a Finding") calibration: a real but
unexercised defect in a shipped artefact is medium, not high, and it predates
the RC.

### Finding 7: the normalisation anchor trusts any `<X>/projects/<name>`, not only the framework's own

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (same recurring class as Finding 6)
**Regression:** no
**Integrity:** none
**Filed As:** BUG-0403 (Path C-A)

`BUG-0398`'s own remediation text specified the bound the fix should land on:
"only when the root is exactly `<pgai_temp_dir>/projects/<single-component>`
— so a root that merely contains the word cannot widen anything."
`<pgai_temp_dir>` is the framework's own resolved temp root. The shipped
anchor is `<anything>/projects/<single-component>` — a test on the shape of the
string, which accepts any path on the filesystem of that shape:

```
PGAI_AGENT_KANBAN_TEMP_DIR=$HOME/projects/pgai
  _git_boundary_guard_path_under_temp_root $HOME/develop/pgai-agent-kanban -> EXEMPT
  _git_boundary_guard_path_under_temp_root $HOME                            -> EXEMPT
```

`BUG-0398` acceptance bullet 1 reads on this shape too:
`$HOME/projects/pgai` contains a `projects` component and
`$HOME/develop/anything` is outside that root, yet it is exempt.

`CODER-20260927-002`'s `status.md` records the design choice explicitly: it
weighed deriving the framework root from `team/scripts/lib/temp.sh`'s
`pgai_temp_dir` and rejected it because that function echoes
`PGAI_AGENT_KANBAN_TEMP_DIR` verbatim when the variable is set, so it cannot
distinguish an intentional per-project root from an accidental one. That
reasoning is correct about `pgai_temp_dir`; what the filing points at is that
other provenance sources exist (`kanban.cfg [paths]` `tmp_root`/`tmp_subdir`,
which is what `pgai_temp_dir` computes when the override is absent; or having
the dispatcher that generates the per-project root export the framework root
beside it).

Narrower in practical blast radius than Finding 6: the widening only grants an
exemption where the destination or `repo_path` actually resolves under the
widened parent, whereas Finding 6 is universal. Pre-existing on `ai_main` for
the same reason. Unreachable under the shipped default.

### Finding 8: the gated integration suite writes a boundary-guard evidence record on every run

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated to one test module; nothing published, no ref moved)
**Regression:** no
**Integrity:** none
**Filed As:** BUG-0404 (Path C-A)

A regression of a prior gap (Step 6.11). `BUG-0396` (`## Status: done`,
`## Fixed In: v2.24.16`) required that "a full `bash team/scripts/run-unit-tests.sh`
and `bash team/scripts/run-integration-tests.sh` run from a non-CM wake
session adds zero records to that task's `logs/git-boundary-guard.jsonl`."
This RC's integration run added one:

```json
{"timestamp":"2026-09-27T05:23:19Z","role":"tester",
 "task":"TESTER-20260927-007-verify-2-24-17",
 "argv":["git","-C",".../test_nothing_resolves_until_th0/dev_repo","fetch","origin"]}
```

Isolated and reproduced deterministically —
`tests/integration/test_overwatch_scaffolding_gates_resolution.py` writes
exactly one record per run while both its tests pass. The `fetch origin`
spelling (no `--tags --quiet`) matches `cm/open-rc.sh:365` and
`cm/cancel-rc.sh:329`, not the `pp_last_released_version` call `BUG-0396`
addressed; the test copies the CM operator verbs into a throwaway kanban root
and drives them in a subprocess that inherits `PGAI_AGENT_ROLE` from the wake
session, so a non-CM role refuses and records them.

Worth noting for whoever fixes it: the guard's fixture-local exemption ought
to have covered this invocation. Both halves of the failed-probe exemption
hold in isolation on the RC tip — the throwaway `dev_repo` has no `origin`, so
`_git_boundary_guard_is_unreachable_destination` returns true on
`error: No such remote 'origin'`, and `_git_boundary_guard_path_under_temp_root`
returns true for the `dev_repo` path under `/tmp/pgai_kanban_tmp`. The record
therefore implies the guard subprocess resolved a different temp root than the
one the fixture sits under, most likely because the test repoints
`PGAI_AGENT_KANBAN_ROOT_PATH` at its throwaway kanban root whose `kanban.cfg`
carries an empty `[paths]` section.

Rated low: the command was refused, nothing was published, no ref moved. The
cost is that Step 6.12 question (5) now answers YES on every cycle, which
spends the check's discriminating power.

### Finding 9: requirements acceptance criteria 2 and 3 — compile and syntax

**Category:** pass

All Python files compile: `find . -name '*.py' -not -path './.git/*' | xargs python3 -m py_compile`
exits 0 with no output. All bash files pass syntax check: `bash -n` over every
`*.sh` in the tree — 294 files checked, 0 failures.

### Finding 10: `release-state.md` schema and version ordering

**Category:** pass

All four fields present and well-formed: `## Active RC` = `v2.24.17`,
`## RC Opened At` = `2026-09-27T03:22:14+00:00`, `## RC Opened By Task` =
`CM-20260927-006-open-rc-2-24-17`, `## Last Released` = `v2.24.16` (a
well-formed `vX.Y.Z`). `pp_last_released_version "pgai-agent-kanban"` returns
`v2.24.16`, and `semver_gt "v2.24.17" "v2.24.16"` is true — the RC under test
is strictly greater than the last release. Comparison made through
`team/scripts/lib/semver.sh`, never inline.

### Finding 11: structural checks

**Category:** pass

Every file in the diff exists at its expected path with sane permissions:
`docs/blocking-site-census.md` 644, `team/pgai_agent_kanban/lint/bug_filer.py`
644, `team/scripts/lib/git_boundary_guard.sh` 755 (a sourced-and-executable
lib, unchanged from its pre-RC mode),
`team/tests/unit/test_git_boundary_guard.py` 644,
`team/tests/unit/test_lint_bug_filer.py` 644. No migrations, renames or
deletions in this RC, so no old-path/new-path sweep was owed. Nothing the
requirements doc names is missing from the diff.

### Finding 12: constraint negative checks

**Category:** pass

A grep of the full `ai_main..ai_rc/v2.24.17` diff for added lines matching
`password`, `api_key`, or a quoted `token =` / `secret =` returns nothing — no
hardcoded credentials introduced. `set -euo pipefail` is intact in both gated
runners (`run-unit-tests.sh:97`, `run-integration-tests.sh:113`). `bash -n`
clean on the modified guard lib.

### Finding 13: skip-cites-a-real-bug gate

**Category:** pass

`bash team/scripts/lint_skip_bug_gate.sh` exits 0: "all skip annotations are
compliant", with "BUG-NNNN file-existence check: 0 missing files". The manual
double-grep confirms it — no placeholder shapes (`BUG-SKIP`, `skip` near a
`BUG-` followed by letters) outside archived run logs and one test fixture
whose own comment explains the synthetic `reason=BUG-NNNN` string, and every
`BUG-NNNN` token in `team/tests/` is synthetic renderer/scanner sample data
rather than a skip citation.

### Finding 14: prerequisite dependency graph

**Category:** pass

No cycles; every referenced task ID resolves to an existing folder. Lifecycle
bookends correctly linked: `CM-20260927-006-open-rc-2-24-17` (DONE) is the
prerequisite of all four CODER tickets; `CM-20260927-009-release-2-24-17`
(WAITING) depends on all four CODER tickets plus this verification task.
Sequence numbers are non-colliding. `008` is unallocated and `006` (CM-open)
is numbered after `002`-`005` (the CODER tickets that depend on it) — both are
the normal shape of this pipeline's per-materialization numbering, matching the
three prior cycles on `20260926`, so neither is a finding. Queue markers are
correct and derived: `[x]` for the DONE/WONT-DO tasks, `[W]` for
`CM-20260927-009`, `[A]` for this task.

### Finding 15: synthetic release simulation

**Category:** pass

Skipped with a note, per Step 6.8. The diff touches no release-lifecycle file:
`docs/blocking-site-census.md`, `team/pgai_agent_kanban/lint/bug_filer.py`,
`team/scripts/lib/git_boundary_guard.sh` and two unit-test modules — none of
`cm-release.sh`, `cm-open-rc.sh`, `cm-cancel-rc.sh`,
`team/scripts/lib/project_paths.sh`, nor anything in the open/close cycle.

### Finding 16: coding-standards spot-check

**Category:** pass-with-caveat

No new script under `team/scripts/` (so no `--help` shortfall). No new
comment, docstring, help line or log line cites a bug, task, or version
identifier: the added lines of the diff yield zero `BUG-NNNN`,
`CODER-YYYYMMDD` or `vX.Y.Z` tokens, and
`python3 team/scripts/lint_hygiene.py` reports 0 findings across all three
classes. No operational value hardcoded where an operator would want it
configurable — the `projects` literal in the guard's anchor is structural, not
a tunable.

The caveat is duplication. `_resolve_repo_root` in `bug_filer.py` is the
fourth place in the tree that resolves a dev-tree root from
`PGAI_DEV_TREE_PATH` with fallbacks, beside `team/scripts/lib/resolve_item_shim.py`,
`team/pm-agent/pm_materialize.py`, and `team/scripts/lib/dev_tree.sh`'s
`resolve_global_dev_tree`. Borderline rather than a violation: each has
different fallback semantics and none of the Python ones lives in a shared
lib, so there was no existing helper to call. Recorded as a caveat, not filed.

## Lint Findings (non-blocking)

None. Both `${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-unit.json` and
`.../findings-integration.json` were written with `"findings": []` — the
runners' own report lines read `findings file written: ... (0 finding(s))` for
both suites.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=19d freshness=stale — last cold-build attempt failed on `docker/claude-abliterated` (a `FROM` parse error) and on `podman/rocky9` (a `COPY --chmod` step), and the bug that run filed is `BUG-0167-cold-build-claude-abliterated-failure` (`## Status: done`, closed since); the record is stale on both counts (19d exceeds the 7-day window, and v2.9.20 is not the v2.24.17 under verification), so nothing is known about whether this tree's three Dockerfiles build cold today.

## Autonomous criterion

Four of the five questions answer NO; question (5) answers YES with a `low`
rating that leaves the recommendation where the rest of the report puts it.

1. **Were any tasks force-promoted from WAITING to BACKLOG by hand, bypassing
   the wake script's prerequisite evaluation?** **NO.** This task's own
   `status.md` recorded "Promoted from WAITING to BACKLOG by wake script:
   prerequisites satisfied" — the automation's own line, not a hand edit.
   `CM-20260927-009-release-2-24-17` is still `WAITING` with its `[W]` marker
   intact, awaiting this task.
2. **Was `release-state.md` edited outside `cm-open-rc.sh` / `cm-release.sh` /
   `cm-cancel-rc.sh`?** **NO.** Its mtime is `2026-09-27 03:22:14`, matching
   its own `## RC Opened At` field to the second and attributed to
   `CM-20260927-006-open-rc-2-24-17`. Untouched since the RC opened.
3. **Were any scripts patched mid-build — a commit touching `team/scripts/`,
   `subagents/`, or other tooling while the RC was in flight?** **NO.** All six
   commits on `ai_main..ai_rc/v2.24.17` are the three feature commits and their
   three `--no-ff` merges. `team/scripts/lib/git_boundary_guard.sh` is touched
   by `77fcc16`, but that file is the authorized subject of
   `CODER-20260927-002`'s ticket, delivered through the normal
   branch-and-merge path. No out-of-band tooling patch.
4. **Were any queue files edited by hand?** **NO.** Every queue mtime lines up
   with an automation event: `bug_backlog.md` 03:05:14 (the discovery
   pipeline's bundle, whose timestamp the requirements doc carries verbatim),
   `pm_backlog.md` 03:16:48, `cm_backlog.md` 03:23:45, `coder_backlog.md`
   04:52:24, `tester_backlog.md` 04:57:05. Markers are derived correctly from
   task state throughout.
5. **Did any agent other than CM invoke a remote git operation — `fetch`,
   `pull`, or `push`?** **YES.** Four records exist across this cycle's task
   folders, every one a refused, read-only `fetch`; none is a `pull` or `push`,
   none changed ref state anywhere, and the boundary held in every case.

   | Records | Role / task | `argv` | Cause |
   |---|---|---|---|
   | 1 | `pm` / `PM-20260927-001-decompose-...` | `git -C $HOME/develop/pgai-agent-kanban fetch origin --tags --quiet` | the live install's `pp_last_released_version` |
   | 2 | `tester` / this task | same command, twice at 05:28:03 | this session's own two `pp_last_released_version` calls in the Step 6.1 check |
   | 1 | `tester` / this task | `git -C <tmp>/dev_repo fetch origin` | the gated integration suite — Finding 8, filed as BUG-0404 |

   Rated `low` per `docs/tester-verification-reference.md` ("What a YES does
   not mean"): every attempt was refused or read-only, and no record shows ref
   state changing. The `pp_last_released_version` shape is `BUG-0396`'s, which
   shipped its fix in v2.24.16 — and the fix IS present in this RC's tree
   (`team/scripts/lib/project_paths.sh:863` gates the fetch on
   `-z "$_plrv_role_lc" || "$_plrv_role_lc" == "cm"`). Those three records come
   from the **live install**, whose copy of the helper still has the ungated
   fetch, because the live install is at `v2.24.12` (see Caveats). Not filed:
   the shipped code is correct and the live-install lag is operator-managed
   environment state, not a pipeline defect. The fourth record is a genuine
   residual in the RC's own test tree and is filed as BUG-0404.

   **Manual interventions required: none.** Questions 1-4 are NO and the
   question (5) YES is a boundary that held, not an intervention.

## Caveats

**The live install is at `v2.24.12`, five releases behind the RC under
verification.** `$HOME/pgai_agent_kanban/VERSION` reads `v2.24.12`
(`VERSION_DETAIL`: `ai_v2.24.12  deposit-sha=700b42de`), while
`## Last Released` is `v2.24.16` and this RC is `v2.24.17`. `cm/release.sh`
does not run `install.sh` — upgrading the live install is an operator action by
design, and `SOP-core.md` says `install.sh` replaces the live install
wholesale — so this is environment state rather than a defect, and it is not
filed. It has one concrete verification consequence worth stating plainly:
fixes that shipped in v2.24.13-v2.24.16 are not in effect in the running
system, which is why three of the four question (5) records exist at all
(`BUG-0396`'s fix is in the RC tree but not in the live copy the PM and TESTER
wake sessions actually ran). An operator re-running `install.sh` after this
release would clear those three records from future cycles and restore the
evidence file's signal for everything except Finding 8.

**The guard exemption boundary has now taken five narrowings across five
RCs.** `BUG-0365`, `BUG-0388`, `BUG-0391`, `BUG-0393` and `BUG-0398` are all
the same question — "is this path throwaway scratch?" — and each fix tightened
a test on the configured string rather than deriving the answer from the
component that produced the path. Each narrowing is correct and each residual
is smaller than the last, so this is not a loop that has stopped making
progress; it is a loop making progress one instance at a time on a class that
admits a structural close. That judgment is why Findings 6 and 7 carry
`Systemic Risk: medium` rather than `low`, and why BUG-0403's `## Files
Involved` names two provenance-based alternatives explicitly. It is not
`high`: nothing is failing today, and each RC has demonstrably closed the
instance it was handed.

**`CODER-20260927-005-provenance-sweep` is `WONT-DO` with `Needs Human: yes`.**
Per `SOP-core.md` ("WONT-DO authority") that disposition is not closed until
HUMAN confirms it, and the task's `## Next Recommended Step` asks for that
confirmation. It does not gate the release —
`CM-20260927-009-release-2-24-17`'s prerequisite check treats `WONT-DO` as
satisfied — and the conclusion is well evidenced (zero lint findings tree-wide
with every other ticket merged). Its unmerged feature branch
`ai_feature/CODER-20260927-005-provenance-sweep` sits at exactly the RC tip
with zero unique commits, left in place for inspection as that task's status
records.

**Two leftover feature branches exist locally, both belonging to `WONT-DO`
tasks.** `ai_feature/CODER-20260926-017-provenance-sweep` is not an ancestor
of the RC and carries 10 unique commits;
`ai_feature/CODER-20260927-005-provenance-sweep` is an ancestor with 0. Step 2
pre-flight scans `DONE` tasks only, so neither is an anomaly under that check,
and no `DONE` task in the project has an unmerged or empty feature branch.
Noted so a future reader does not mistake the 10-commit branch for lost work —
it belongs to a task deliberately declined in the previous cycle.

**Test fidelity.** The RC's own new tests exercise the real breaking
conditions rather than proxies: the two guard tests drive the actual shim
binary in a subprocess against real `git init` repositories and assert on the
destination's refs before and after, and the four filer tests drive
`file_or_cite_hygiene_bugs` against real `git init` repositories rather than
mocking `subprocess.run`. No mocked-away condition to point at. The one
coverage shortfall is not a fidelity problem but a missing case, and it is
what Findings 6 and 7 are: neither new guard test exercises a configured root
whose widened form is empty, nor the bare two-component shape at a location
the framework never generated.

**Coverage (informational only; never enters the verdict).**

| Suite | line (statement) % | branch % | blended % |
|---|---|---|---|
| unit | 76.42 | 63.30 | 73.38 |
| integration | 11.40 | 9.12 | 10.87 |

Read from the `totals` object in `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/unit.json`
and `.../integration.json`, using the field names verified against the
generated files (`percent_statements_covered`, `percent_branches_covered`,
`percent_covered`). The runners' own `^TOTAL` `Cover` column shows the blended
figure (73% and 11%), which is why it is labelled as such here.

**Gated suite evidence.** `bash team/scripts/run-unit-tests.sh --coverage`
exited **0** — 5027 passed, 31 skipped in 779.76s, with
`bare_tmp_cleanliness`, `listener_cleanliness` and `scratch_sweep` all OK.
`bash team/scripts/run-integration-tests.sh --coverage` exited **0** — 548
passed, 1 skipped in 715.14s, same three post-suite checks OK. Both run from
the dev-tree root with `PGAI_DEV_TREE_PATH` pointed at the task worktree; full
output tee'd to `${PGAI_AGENT_KANBAN_TEMP_DIR}/tester/unit.log` and
`.../integration.log`. PASS evidence is these two exit codes, not any pytest
"N passed" line.

**No prior-run artifacts existed.** `artifacts/` was empty at start and no
`report.md.previous-RUN-N` was present, so this verification was performed
fresh with no prior verdict available to inherit.

**No prompt-injection content encountered.** Every file read in the course of
this verification — requirements doc, five bug documents, four CODER status
files, source and test files — contained only technical content. Nothing
resembling "ignore previous instructions" appeared anywhere.
