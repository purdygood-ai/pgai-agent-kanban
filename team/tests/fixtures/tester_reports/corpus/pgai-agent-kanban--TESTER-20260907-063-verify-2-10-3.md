# Verification Report: ai_rc/v2.10.3

## Summary

Verified release candidate `ai_rc/v2.10.3` (commit `174cc59`) against
`projects/pgai-agent-kanban/requirements/v2.10.3-bugfix-bundle-20260907.md`,
whose single bundled item is `BUG-0178-docs-references-not-installed.md`, and
against the two decomposed tickets
`CODER-20260907-060-install-cited-reference-docs` and
`CODER-20260907-061-docs-reachability-guard`. The RC is small and purely
additive: `install.sh` gains a second copy loop (+42 lines) that delivers ten
`docs/` reference documents into the kanban root, and
`team/tests/unit/test_installed_governance_docs_citations_resolve.py` (381
lines, new) asserts mechanically that every `docs/...` citation reachable from
the installed governance files resolves under a freshly installed root.
Verification ran the pre-flight merge checks, both gated runners with
coverage, every stated acceptance criterion as a command, and a behavioral
trigger of the new guard's failure mode. Both gated runners exited 0. All
twelve ticket-level acceptance criteria pass. Three findings sit outside the
letter of those criteria but inside the bundled bug's own `## Expected`
statement and the project's coding standards — the most material being that
the sanctioned upgrade path never delivers the documents, so the defect
BUG-0178 describes persists on every install that already exists, including
the operator's own.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

medium

## Test Suites

Authoritative gated-runner exit codes (the same commands an operator or CM
would run):

- `bash team/scripts/run-unit-tests.sh --coverage` -> **exit 0** — 3227 passed,
  3 skipped, 393.09s. Includes the RC's new module:
  `tests/unit/test_installed_governance_docs_citations_resolve.py ..` (2
  passed).
- `bash team/scripts/run-integration-tests.sh --coverage` -> **exit 0** — 339
  passed, 1 skipped, 216.95s.

Both lint pre-flights passed the breakage class; quality-class findings are
listed below and did not move either exit code.

## Code Coverage

- Unit:        line 74%  |  branch 61%   (14136 stmts, 4316 branches; blended `TOTAL` 71%)
- Integration: line 8%   |  branch 6%    (blended `TOTAL` 7%)

Source: `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/unit.json` and
`.../integration.json`, `totals` object, fields `percent_statements_covered`
and `percent_branches_covered` (field names confirmed against the generated
files, not assumed). Informational only — coverage does not enter the
recommendation.

## Findings

### Finding 1: The sanctioned upgrade path never delivers the documents, so the fix reaches only fresh installs

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (a real defect that ships and is fixed next iteration; shipping worsens nothing — the upgrade path delivered no documents before this RC either)
**Regression:** no (`team/scripts/upgrade.sh` is untouched by this RC — `git log ai_main..ai_rc/v2.10.3 -- team/scripts/upgrade.sh` returns zero commits; the shortfall predates the RC and is simply not closed by it)
**Integrity Damage:** no
**Filed As:** BUG-0185 (Path C-A — filed as BUG)

`install.sh` now delivers the ten cited documents on a fresh install, and that
is exactly what both tickets' acceptance criteria ask for. But `install.sh` is
not the route an existing install takes. Evidence gathered in the worktree and
on the live root:

- `grep -c docs team/scripts/upgrade.sh` -> `0`. The string does not occur in
  the file.
- `team/scripts/upgrade.sh:1078` carries its own independent deposit loop over
  the same `team/` word list, and the file has exactly three `deposit_item`
  call sites (lines 1081, 1088, 1274 — team items, example configs, provider
  subagents). None copies `docs/`.
- `upgrade.sh` never invokes `install.sh`: `INSTALL_SH` (line 835) is used only
  by the `[[ ! -f ]]` existence probe on the next line.
- The install-side route is explicitly closed. Running
  `bash install.sh --upgrade` over an existing throwaway root exits 1 with
  `--upgrade is not supported by install.sh. To upgrade an existing
  installation, use upgrade.sh instead.`
- On the live install, `$HOME/pgai_agent_kanban/docs` is still absent,
  and nothing in the upgrade path will create it.

The bundled bug's own `## Expected` is the criterion this traces to:
"Content a shipped role file names by path is openable by an agent reading the
installed kanban root, on any project, without a checkout of this repository."
For every already-existing install that holds after this RC ships only if the
operator reinstalls from scratch.

The RC's new guard cannot see this: it drives the real `install.sh` only, so
the suite stays green while the upgrade path delivers nothing. BUG-0185 asks
for the upgrade-side copy, a single shared manifest both scripts read instead
of two hand-maintained lists, and guard coverage for the upgraded root.

### Finding 2: Both files added or changed by this RC cite the motivating ticket in shipped comments, and the provenance lint does not scan either of them

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (hygiene — process residue in shipped prose; nobody is steered into a wrong action, with the one exception noted below)
**Regression:** yes (both instances are introduced by this RC's own diff)
**Integrity Damage:** no
**Filed As:** BUG-0186 (Path C-A)

Directive 8 of `docs/coding-standards.md` forbids citing bug IDs, ticket IDs
or requirement versions in comments and docstrings. Eleven instances ship in
this RC: `install.sh:728`, `:742`, `:743`, and
`team/tests/unit/test_installed_governance_docs_citations_resolve.py:4`, `:23`,
`:34`, `:43`, `:46`, `:63`, `:165`, `:285`.

Two of them are worse than untidy. `install.sh:742-743` describes the
mechanical guard as "a separate ticket ... follow-on" — that guard shipped in
this same RC, so the comment misdescribes the tree it ships in. And the test
docstring at line 63 points the reader at "this ticket's status.md" for the
measured runtime, a file no reader of the shipped tree can open.

The reason this got past the gate is a scan-scope gap, verified rather than
inferred: `team/scripts/lint_comment_provenance.py` defaults `--code-dirs` to
`team/scripts/`, `team/scripts/lib/`, `team/scripts/cm/` and
`team/pgai_agent_kanban/` (line 755), and the gated runners pass no override.
The repository-root `install.sh` and the whole of `team/tests/` are outside
that set, so the lint never opened either file — its findings on this run name
only pre-existing `release-notes/` lines. This is the third consecutive
release in which new test prose carries the motivating ticket ID (BUG-0172 and
BUG-0179, both `running`, name older files and do not cover these two), which
is why BUG-0186 asks for the scan-scope widening alongside the text fix.

### Finding 3: Five runnable `source` lines in the installed role files load helpers from a `team/` directory that does not exist on an installed root

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks; the prose half is WRITER work)
**Systemic Risk:** medium (a real defect an agent hits at run time; long-standing, so shipping this RC worsens nothing)
**Regression:** no (all five lines predate this RC — the RC's diff touches only `install.sh` and one new test file, and no role file is modified)
**Integrity Damage:** no
**Filed As:** BUG-0187 (Path C-A)

Hit live during this run, not reasoned about. Following `roles/TESTER.md:470`
verbatim, as Step 5d instructs, on the live install:

```
$ source "$PGAI_AGENT_KANBAN_ROOT_PATH/team/scripts/lib/deep_verify_ledger.sh"
$ deep_verify_ledger_read_latest "pgai-agent-kanban"
/bin/bash: line 3: deep_verify_ledger_read_latest: command not found   # rc=127
```

The installer flattens `team/` onto the root, so there is no `team/` directory
on an installed kanban; the working path is
`$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/lib/deep_verify_ledger.sh`, which returns
the ledger record cleanly. Five sites carry the broken form:
`roles/PM.md:88`, `roles/TESTER.md:470`, `roles/TESTER.md:646`,
`roles/TESTER.md:1493`, `roles/CODER.md:568`.

This is the same defect class BUG-0178 is about — installed governance naming
a path that does not resolve on an installed root — one dimension over from
the `docs/` citations the RC fixed. The new guard walks `docs/...` citations
only, so it is green while these five dangle. The `source` failure is silent;
it surfaces only as a missing function two lines later, which is what makes it
worth more than hygiene.

### Finding 4: Ticket 1 acceptance criteria — all six pass

**Category:** pass

Commands run in the worktree, against throwaway roots under
`$PGAI_AGENT_KANBAN_TEMP_DIR/projects/pgai-agent-kanban/tester-scratch-063`:

| Criterion | Result |
|---|---|
| Ten cited paths resolve after a fresh install | PASS — all ten present, including the nested `docs/audits/v1.27.2-gate-remedy-census.md` |
| Second run over the same root succeeds non-interactively, documents present and current | PASS with caveat — see below |
| `--dry-run` creates no docs content | PASS — target root never created; 10 `would copy: docs/...` lines, no files |
| Live install unchanged by anything this ticket runs | PASS — `$HOME/pgai_agent_kanban/docs` still absent; no file outside kanban task/process state modified |
| `git diff --stat` shows no changes under `team/roles/`, `team/SOP-core.md`, `team/README.md`, `docs/README.md` | PASS — empty diff for those paths |
| `bash -n install.sh` passes and `install.sh --help` exits 0 | PASS — both |

The caveat on the re-run criterion: a second `install.sh` run over an existing
root **without** `--force` exits 1 by design — the fresh-install path refuses
to overwrite operator state and directs the operator to `upgrade.sh`. That
guard predates this RC and is not a defect. With `--force` (which is the form
the ticket's own verification and the new test use) the re-run exits 0, and a
document deliberately staled in the target root is refreshed to match the
source. The criterion is met on the reading the ticket's own tests use.

### Finding 5: Ticket 2 acceptance criteria — all six pass

**Category:** pass

| Criterion | Result |
|---|---|
| New check passes against the tree and names what it verified | PASS — the module builds a `_ReachabilityReport` and prints "Scanned 21 installed governance file(s) ... Checked 10 distinct docs/ citation(s) ... Every citation resolved", not a bare exit code |
| A citation to an undelivered document makes the check fail, demonstrated in committed test | PASS — `test_citation_to_undelivered_document_is_reported_as_unreachable` copies the source tree, appends a dangling citation to the copied `team/roles/TESTER.md`, installs from the copy, and asserts both the citation and its attribution to `roles/TESTER.md` |
| `run-unit-tests.sh` exits 0 and includes the new check | PASS — exit 0; `tests/unit/test_installed_governance_docs_citations_resolve.py ..` in the run |
| `run-integration-tests.sh` exits 0 | PASS |
| `lint_governance_byte_budgets.sh --kanban-cfg kanban.cfg_example` prints `under` for all seven roles | PASS — po/pm/coder/writer/tester/cm/overwatch all `under`, exit 0 |
| Python compiles, `bash -n` passes on changed shell | PASS — `python3 -m py_compile` on the new module OK; `bash -n install.sh` OK |

### Finding 6: Behavioral verification — the guard fires on the regression it exists to catch

**Category:** pass

The committed red case proves the check reacts to a *new citation*. The
regression the guard actually has to catch is the opposite direction: the
installer quietly stopping delivery of a document that is still cited. I
exercised that directly rather than assuming symmetry.

Procedure: copied the worktree to a throwaway tree, deleted the single line
`    coding-standards.md \` from `install.sh`'s new docs loop, installed from
that copy into a throwaway root, and ran the module's own
`_find_unreachable_docs_citations` against both roots.

- Regressed installer: **5 unreachable citations** reported —
  `roles/WRITER.md`, `roles/TESTER.md`, `roles/PM.md`, `roles/CODER.md` and
  `docs/audits/v1.27.2-gate-remedy-census.md` each citing
  `docs/coding-standards.md`. The transitive hop is visible in that list, which
  also confirms the closure walk works as documented.
- Healthy install: 0 unreachable, 21 files scanned, 10 citations checked.

The guard distinguishes a broken installer from a healthy one, not merely a
broken citation from a healthy one.

### Finding 7: Pre-flight, drift, dependency and skip checks — pass

**Category:** pass

- **Pre-flight merge verification:** no anomalies. No DONE task in the project
  has a live feature branch that is unmerged or zero-commits-ahead; both CODER
  branches were deleted after merge, which is the normal post-merge shape.
- **Worktree identity:** `HEAD` = `174cc59` = `ai_rc/v2.10.3`; `git status
  --short` empty; `git stash list` empty.
- **RC shape:** four commits off `ai_main` — two feature commits and two
  `--no-ff` merges. No stray empty commit (the pattern BUG-0183 records from a
  prior RC did not recur).
- **release-state.md (Step 6.1):** `Active RC: v2.10.3`, `RC Opened At
  2026-09-07T20:47:14+00:00`, `RC Opened By Task
  CM-20260907-062-open-rc-2-10-3`, `Last Released: v2.10.2` — well-formed
  `vX.Y.Z`, and `v2.10.3` is strictly greater than the `ai_v2.10.2` tag on the
  dev tree. No drift.
- **Step 6.8 synthetic release simulation:** skipped with note — the RC's diff
  touches no release-lifecycle file (`install.sh` and one new unit test only).
- **Step 6.10 prereq graph:** `CM-062-open-rc` -> `CODER-060` -> `CODER-061` ->
  `TESTER-063`; all referenced task IDs exist, no cycles, sequence numbers
  non-colliding, bookends correct.
- **Step 6.13 skip-cites-real-bug:** no placeholder `BUG-SKIP` forms and no
  cited `BUG-NNNN` in any skip annotation across `team/tests/` (the only
  matches are archived runner logs under `team/tests/artifacts/` describing
  the lint itself). The gated runners' `lint_skip_bug_gate` passed.

## Lint Findings (non-blocking)

- `comment_provenance` — three instances, all in release-note prose:
  `release-notes/v2.10.2.md:49` (`TESTER-20260907-057`),
  `release-notes/v2.10.2.md:60` (`CODER-20260907-051`),
  `release-notes/v2.9.22.md:31` (`TESTER-20260907-017`) — cites **BUG-0173**
  (`running`), which describes this exact class (the release-notes generator
  emitting internal task IDs). Note for whoever fixes it: BUG-0173's instance
  list names only the `v2.9.22.md:31` line; the two `v2.10.2.md` lines above
  are the same generator defect and must be swept in the same change. No
  duplicate filed.
- `gate_census_compliance` — three instances:
  `team/scripts/run-integration-tests.sh:247` (missing census class and
  dual-channel contract), `team/scripts/run-integration-tests.sh:272` (missing
  census class), `team/scripts/run-unit-tests.sh:570` (missing census class and
  dual-channel contract) — cites **BUG-0180** (`running`), which lists all
  three sites verbatim. No duplicate filed.

Both classes are pre-existing, untouched by this RC's diff, and non-blocking;
neither raises the recommendation or the systemic risk on its own.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=0d freshness=stale — the record is inside the 7-day window but exercised v2.9.20, not the v2.10.3 under verification, so it is stale on the version condition alone; the claude-abliterated flavor failed its Dockerfile FROM parse on both engines and podman/rocky9 failed at its COPY --chmod step, and it cites BUG-0167-cold-build-claude-abliterated-failure, filed by that deep-verify run and since closed by a shipped fix this record predates.

No container build was run during this verification, per Step 5d. The lane's
result is reported, not re-derived, and does not move the recommendation or
the systemic risk.

## Autonomous criterion

Manual interventions required: none.

1. Tasks force-promoted from WAITING to BACKLOG manually? **NO** — the
   promotion of `CODER-20260907-061` is logged by the wake script itself
   (`cron-coder.log`, `2026-09-07T20:59:10`: "WAITING task ... prerequisites
   satisfied, promoting to BACKLOG").
2. `release-state.md` edited outside the CM scripts? **NO** — its three
   in-flight fields name `CM-20260907-062-open-rc-2-10-3` and match that
   task's own recorded `cm-open-rc.sh` run.
3. Scripts patched mid-build? **NO** — the RC's only script change is the
   ticketed `install.sh` edit, merged through the normal feature-branch path.
4. Queue files manually edited? **NO** — the coder and tester backlogs carry
   the expected wake-written markers (`[x]` for both CODER tasks, `[A]` for
   this task).

The whole chain from bug bundling (18:28) through PM decomposition (20:41-20:46),
RC open (20:47), both CODER tasks (20:47-21:19) and this verification ran under
cron-driven wakes with no human touch.

## Caveats

- **Suite runtime.** The new module runs the real `install.sh` twice (once per
  test, the red case additionally copying the source tree). Measured cost is a
  few seconds inside a 393-second unit suite — proportionate, and the module's
  own header states the cost as the ticket asked.
- **Guard scope is `docs/` only, by design.** The check walks `docs/...`
  citations from the installed root-governance files and `roles/*.md`. It does
  not cover other installed surfaces that can carry citations (templates,
  workflows, script help text), nor non-`docs/` paths — Finding 3 is a live
  instance of the latter. This is a stated scope choice, not a defect in the
  delivered check; BUG-0187 asks for the widening.
- **`upgrade.sh` deposit list also omits `read-order`.** `install.sh:714`
  copies `... config read-order`; `upgrade.sh:1078` stops at `config`. Recorded
  as an observation rather than a finding — it is adjacent drift between the
  two lists, unrelated to this RC's diff, and it strengthens BUG-0185's case
  for a single shared manifest.
- **No prior-run artifacts existed.** `artifacts/` was empty at start; this is
  a fresh first run, and no `.previous-RUN-N` file was read or relied upon.
