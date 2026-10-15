# Verification Report: ai_rc/v2.24.10

## Summary

Verified release candidate `ai_rc/v2.24.10` (commit `37e1d1b`) of project
`pgai-agent-kanban` against its requirements document
`requirements/v2.24.10-bugfix-bundle-20260922.md` — a five-item BUG bundle
(BUG-0363 git-guard self-recursion storm, BUG-0365 the guard refusing
fixture-local pushes, BUG-0366 the guard shim leaking processes, BUG-0367 the
guard presence check testing the wrong thing, BUG-0364 an extracted stage missing
from the engine-size manifest) plus the two test-authoring contracts WRITER-006
recorded. Work landed as six `--no-ff` merges; a seventh ticket (CODER-007) was a
verification-only run that correctly produced no commit. Both gated runners exit 0
(unit 4980 passed / 30 skipped; integration 541 passed / 1 skipped), and every one
of the five bugs' own acceptance criteria was exercised behaviorally rather than
inferred from the code — including the criterion this RC exists to satisfy, that
`run-unit-tests.sh` exits 0 under `PGAI_AGENT_ROLE=coder` with the shim on PATH,
which it now does in 850 s with zero leaked processes where v2.24.9 exited 1 in
3712 s with 42 failures. Four findings were recorded, none severe: one bundled item
the RC did not address (`critical` missing from the deferral ranker's severity
vocabulary), three internal bug identifiers shipped in new comments, the hygiene
lint's structural inability to read the guard shim that let them through, and a
role-file cross-reference left one contract short. Each is filed via Path C-A
(filed as BUG — code defect / regression / test failure).

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Filed This Run

| ID | Category | Severity | Subject |
|---|---|---|---|
| BUG-0370-guard-shim-comments-cite-bug-id | bug | low | three `BUG-0366` citations in new shipped comments |
| BUG-0371-hygiene-lint-misses-extensionless-scripts | bug | medium | `lint_hygiene.py` collects by suffix, so the guard shim is never scanned |
| BUG-0372-severity-rank-omits-critical | gap | medium | `_disc_severity_rank` accepts only `low\|medium\|high` |
| BUG-0373-role-file-contract-count-stale | gap | low | `team/roles/TESTER.md:399` says four contracts; there are five |
| BUG-0374-guard-lib-hardcodes-temp-root-literal | lint class (Step 5c) | low | `test_anti_patterns`/AP3, four instances, aggregate filing |

Filed By: TESTER (autonomous Path C) — the release does what its five bug reports
asked for and every gated suite is green; what is left over is one warning the
bundle named and never fixed, some bookkeeping that leaked into shipped comments,
the blind spot in the checker that should have caught it, and a pointer in a role
file that now undercounts the document it points at.

---

## Verification log (appended as work proceeded)

### Step 2 — Pre-flight: RC integrity

Scanned every task folder under `projects/pgai-agent-kanban/tasks/`. Every task
whose `status.md` reads `DONE` and whose README names a `## Feature Branch`
other than `none` has had that branch deleted locally after merge, which passes
both pre-flight checks trivially (CODER/WRITER delete branches after merging).
Local branches present: `ai_main`, `ai_rc/v2.24.10`, `last_run_ai_main`, `main`
— no stranded feature branch. No anomaly. **Category: pass**

### Step 3 — Working tree

```
git rev-parse HEAD            -> 37e1d1b332d49cd9b2ddb3544d43d438fd2c1eec
git rev-parse ai_rc/v2.24.10  -> 37e1d1b332d49cd9b2ddb3544d43d438fd2c1eec
git status --porcelain        -> (empty)
git stash list                -> (empty)
```

Detached HEAD at the RC commit, clean tree, no stashes. **Category: pass**

### Step 4 — The diff

`git diff --stat ai_main..ai_rc/v2.24.10`: 14 files, +1617 / -67. Six `--no-ff`
merges on the RC (CODER-002, -003, -004, -005, -008, WRITER-006); CODER-007 was
a verification-only ticket whose branch sat at the source-branch tip and was
deleted with `-d`, so it contributed no commit by design.

Changed: `docs/blocking-site-census.md`, `docs/test-authoring-guidelines.md`,
`team/scripts/lib/git_boundary_guard.sh`, `team/scripts/lib/wake_guard_wiring.sh`,
`team/scripts/lint_engine_size_dupe.py`, `team/scripts/verify-install-policy.sh`,
`team/scripts/wake/git-guard/git`, `team/tests/fixtures/dashboard_render_fixture.py`,
`team/tests/fixtures/git_network_stub.py`, and five test files.

### Step 6.1 — `release-state.md`

```
  ## Active RC          v2.24.10
  ## RC Opened At       2026-09-23T00:25:27+00:00
  ## RC Opened By Task  CM-20260923-009-open-rc-2-24-10
  ## Last Released      v2.24.9
```

All four fields present and well-formed. `semver_gt v2.24.10 v2.24.8` and
`semver_gt v2.24.10 v2.24.9` both true via `team/scripts/lib/semver.sh` (never an
inline compare), so the RC version is strictly greater than
`pp_last_released_version "pgai-agent-kanban"` (which returns `v2.24.8`).
`## Last Released` is a well-formed `vX.Y.Z`, so per Step 6.1 it is not a
finding. The `v2.24.9`-vs-`v2.24.8` divergence is recorded under Caveats as an
observation, not a finding: it is operator-origin and predates this RC.
**Category: pass**

### Step 6.2 / 6.3 — Structural and content review

Every deliverable named by the five bundled bug files exists and carries the
change each bug asked for. New and heavily-modified files were read in full
(`team/scripts/wake/git-guard/git`, the 252-line addition to
`team/scripts/lib/git_boundary_guard.sh`, the new `run_dashboard_script` helper,
the `verify-install-policy.sh` CHECK 7 block, the 73-line addition to
`docs/test-authoring-guidelines.md`); modified regions elsewhere were grepped
and read in context.

Documentation-layer accuracy was checked claim-by-claim against the shipped code
rather than accepted on its face. Four load-bearing claims in the new authoring
prose were each traced to the implementation: that the destination check tracks
`-C` alone (`_git_boundary_guard_find_repo_path` — confirmed, and
`--git-dir`/`--work-tree` are deliberately untracked); that a URL scheme or
scp-like syntax is never exempt (`_git_boundary_guard_looks_like_real_remote` —
confirmed and exercised); that the guard fails open with the role or evidence
sink unset (confirmed in `git_boundary_guard_enforce`); and that pytest's
`tmp_path` lives under the throwaway root (confirmed — `run-unit-tests.sh:350`
exports `--basetemp=${PGAI_AGENT_KANBAN_TEMP_DIR}/pytest/unit` via
`PYTEST_ADDOPTS`, and `team/tests/conftest.py`'s `pytest_configure` does the same
whenever `PGAI_AGENT_KANBAN_TEMP_DIR` is set). **Category: pass**, with one
documentation gap recorded as Finding 4 below.

### Step 6.4 / 6.5 — Acceptance criteria exercised, failure modes triggered

Every bundled bug's own `## Acceptance` was executed, and each guard was
exercised by attempting the thing it prevents rather than inferred from code.

**BUG-0363 (shim self-recursion).** With `team/scripts/wake/git-guard` ahead of
the real git on PATH under `PGAI_AGENT_ROLE=coder`:

| probe | expected | actual |
|---|---|---|
| `git -C <worktree> rev-parse --abbrev-ref HEAD` (allowed) | passes through, exit 0 | `HEAD`, exit 0 |
| `git -C <worktree> fetch origin` (refused) | dual-channel refusal, exit 1, one evidence record | `git-boundary-guard.non-cm-remote-verb` + message + remedy, exit 1, 1 record |
| `git -C <worktree> tag --list 'ai_v2.24.*'` (probe-requiring) | passes through | passes through |
| `PGAI_GIT_GUARD_DEPTH=1` seeded re-entry on a refused verb | policy skipped, real git reached, nothing recorded | real git reached, no evidence file created |
| `GIT_GUARD_REAL_GIT=<stand-in>` | stand-in invoked verbatim | `FAKE-GIT-CALLED args=status --short` |
| descendant / self-invocation watchdog | zero second shim | 0 live `git-guard/git` processes at every sample point |

The grep-zero rider holds in substance: the only occurrence of the literal name
`git` as a token in the shim body is `PATH="$_gbg_filtered_path" command -v git`
at line 132, a lookup on a PATH from which every entry resolving to the shim's
own directory has been removed — not an invocation, and provably unable to
resolve back to the shim. The caller-side unquoted-glob rider was re-audited:
every `tag --list` / `branch --list` call site in the tree quotes its pattern.
**Category: pass**

**BUG-0366 (shim process leak).** Verified with a control, per the
"synthesize the original failure mode as well" rule. A stand-in real git that
sleeps 120 s was placed on PATH behind the shim, and the shim was invoked as a
genuine grandchild of a Python caller whose `subprocess.run(..., timeout=4)`
fires:

| shim under test | outcome |
|---|---|
| `ai_main` (pre-fix) | orphan survives — `sleep 120`, `PPid 1` — exactly the reported signature |
| `ai_rc/v2.24.10` | no survivor; the whole chain dies with the caller |

`setpriv` is present on this host (`/usr/bin/setpriv`), so the
`--pdeathsig KILL` arm is live; the code's documented fallback to a plain `exec`
when `setpriv` is absent was verified by reading, not executed. The two
dashboard fixture files that carried 30 of BUG-0366's failures were then run
under the guard: **38 passed**, zero leaked `git-guard/git` processes (verified
by a `/proc` walk matching `argv[0]` ending in `bash` and `argv[1]` ending in
`git-guard/git`, not a `ps | grep`), load average flat at 2.3 throughout.
**Category: pass**

**BUG-0365 (guard refuses fixture-local pushes).** Full destination matrix, with
`PGAI_AGENT_KANBAN_TEMP_DIR` narrowed to a sandbox subdirectory so "inside" and
"outside" are unambiguous:

| case | expected | actual |
|---|---|---|
| push to remote **name** resolving under the throwaway root | allow | allowed, nothing recorded |
| push to raw **path** under the throwaway root | allow | allowed, nothing recorded |
| push to a **relative** path resolved through `-C` under the root | allow | allowed, nothing recorded |
| `fetch` from a fixture-local remote | allow | allowed, nothing recorded |
| push to a raw path **outside** the root | refuse | refused + recorded |
| push to a remote **name** resolving outside the root | refuse | refused + recorded |
| push to `https://example.invalid/x.git` | refuse | refused + recorded |
| push to `user@host.invalid:repo.git` | refuse | refused + recorded |
| bare `git push` with no destination token | refuse | refused + recorded |
| push to an unconfigured remote name | refuse | refused + recorded |
| **`git -C <the real dev tree> push origin HEAD`** | refuse | refused + recorded |

The last row is the constraint BUG-0365 attached to any fix — a real
`git push origin` from a non-CM role must still be refused — and it holds. The
three named fixture files were then run under the guard in the bug's own literal
reproduction shape (direct pytest, no `GIT_GUARD_REAL_GIT`): **13 passed**, zero
refusal records, where the bug measured 11 failures. **Category: pass**

**BUG-0367 (guard presence check).** `wd_install_git_boundary_guard` exercised
across six states:

| state | WARNING | PATH extended | `GIT_GUARD_REAL_GIT` pin |
|---|---|---|---|
| directory present, shim file absent | yes | no | unset |
| shim present and executable | no | yes | `/usr/bin/git` |
| shim present, not executable | yes | no | unset |
| directory absent entirely | yes | no | unset |
| shim present, operator pin preset | no | yes | preset value preserved |
| role `cm` | no | no | unset |

The first row is the defect BUG-0367 filed, and it now warns and leaves PATH
alone. The secondary ask — that a failed deposit be detectable after the fact —
landed as CHECK 7 in `team/scripts/verify-install-policy.sh`; its positive half
was verified independently by running a real, fully isolated
`install.sh --no-system-cron` into a throwaway `HOME`/`KANBAN_ROOT`, which
deposited `scripts/wake/git-guard/git` mode 755. **Category: pass**, with one
caveat: the WARNING's wording still reads "shim directory not found at ..." when
the directory in fact exists and only the file is missing. CODER-005's task
constraint stated the wording must not change, so this is compliant with the
ticket as written and is recorded as `pass-with-caveat`, not a gap.

**BUG-0364 (engine manifest misses extracted stage).** `ENGINE_FILES` now
carries `lib/wake_reconcile_sweeps.sh` and `lib/wake_blocked_ledger.sh`; the
group comment reads "nine lib stages" and the list holds nine. The lint reports
`37 engine file(s)` (was 35). Behavioral proof that the file is now *measured*,
which is the whole point of the bug: padding
`team/scripts/lib/wake_reconcile_sweeps.sh` from 289 to 989 lines made the lint
name it as oversized (it stayed green before the fix); the padding was reverted
and `git status --porcelain` is empty again. `lib/semver.sh` and
`lib/halt_after_invoke.sh` remain out of the manifest, correctly — both are
shared across more than one engine. The shipped count moved 35→37 rather than
the AC's literal 35→36; CODER-008 documented the divergence and the composition
table confirms two stages were missing, so the AC's intent is met and its
arithmetic was simply written against the bug's narrower narrative.
**Category: pass-with-caveat**

### Step 6.9 — Constraint negative checks

No hardcoded secret shape introduced by the diff. No new bare-`/tmp` path
(`gated-runner: bare_tmp_cleanliness: OK` independently confirms this at suite
level). `team/scripts/verify-install-policy.sh` retains `set -euo pipefail`; the
shim carries `set -u`; the two sourced libraries carry no `set -` line, which is
the correct pre-existing convention for a library that must not alter its
caller's shell options. **Category: pass**

### Step 6.10 — Prerequisite dependency graph

Acyclic, every referenced task ID resolves to a real folder, no dangling
prerequisite. Bookends correct: `CM-20260923-009-open-rc-2-24-10` at the head,
this TESTER task, then `CM-20260923-012-release-2-24-10` waiting in
`cm_backlog.md` as `[W]`. Sequence number 011 is unused — the optional approval
slot, which `## Human Approval Required: auto` skips — and no number collides.
**Category: pass**

### Step 6.13 — Skip-cites-a-real-bug

`bash team/scripts/lint_skip_bug_gate.sh` → `OK — all skip annotations are
compliant`, `0 missing files`. An independent grep for placeholder shapes
(`BUG-SKIP`, `skip` near `BUG-<letters>`) found hits only inside archived run
logs under `team/tests/artifacts/`, not in test source. **Category: pass**

### Step 6.8 — Synthetic release simulation

Skipped with a note, per the step's own condition: the diff touches no
release-lifecycle file (`cm/release.sh`, `cm/open-rc.sh`, `cm/cancel-rc.sh`,
`lib/project_paths.sh`, or any open/close-cycle script). The changed scripts sit
in the wake-dispatch and lint surfaces.
`team/tests/integration/test_cm_release_synthetic.py` runs inside the gated
integration suite regardless.

### Step 5a / 6.6 / 6.7 — The gated suites (the authoritative pass/fail signal)

Both runners were run with `--coverage` from the dev-tree root in the worktree,
output teed under `$PGAI_AGENT_KANBAN_TEMP_DIR/tester/`.

| suite | command | exit | result | wall clock |
|---|---|---|---|---|
| unit | `bash team/scripts/run-unit-tests.sh --coverage` | **`UNIT_EXIT=0`** | 4980 passed, 30 skipped, 0 failed | 855 s |
| integration | `bash team/scripts/run-integration-tests.sh --coverage` | **`INTEGRATION_EXIT=0`** | 541 passed, 1 skipped, 0 failed | 591 s |

Both lint pre-flights passed as part of those exit codes
(`check_breakage_class: OK — 501 python file(s), 280 shell file(s) checked, 0
breakage-class failures`; `lint_dockerfile_static: OK`;
`lint_changelog_freshness: ok — CHANGELOG.md is fresh (RC mode: no differences)`;
`gated-runner: bare_tmp_cleanliness / listener_cleanliness / scratch_sweep: OK`).
The PASS evidence cited here is those two exit codes, never a pytest "N passed"
line. No test failed, so there is no stale-assertion candidate to adjudicate and
no pre-existing failure to bisect. **Category: pass**

### The decisive acceptance criterion — the suite green *under the guard*

BUG-0365's `## Expected` states the criterion this whole RC exists to satisfy:
*"`bash team/scripts/run-unit-tests.sh` exits 0 under `PGAI_AGENT_ROLE=coder` with
the shim on PATH."* Before this RC it exited 1 with 42 failures and took 3712 s.
Run independently here, with the RC's own shim ahead of git on PATH, role `coder`,
and a throwaway `KANBAN_ROOT`/`PGAI_AGENT_KANBAN_ROOT_PATH`/temp root:

| run | exit | result | wall clock | leaked shims | load avg |
|---|---|---|---|---|---|
| this RC, guarded | **0** | 4970 passed, 40 skipped, 0 failed | 850 s | 0 | 1.9–2.3 throughout |
| v2.24.9, guarded (BUG-0366's measurement) | 1 | 42 failed, 4908 passed, 40 skipped | 3712 s | 31–35 | 39.82 |

The guard recorded 8 refusals during the run, all of the same shape:
`git -C <tmp>/pytest/unit/<test>/dev fetch origin --tags --quiet`, from
`team/scripts/lib/project_paths.sh`'s best-effort fetch inside
`test_pp_last_released_version_bash.py`'s fixtures. These are correct refusals,
not a gap in the exemption: the destination is the bare remote name `origin`,
which those fixtures never configure, and an unresolvable remote name is
documented as conservatively not exempted. `project_paths.sh`'s own header
documents the fetch as best-effort and tolerates its failure; all of that file's
tests pass. Zero refusals were recorded for any fixture-local push, which is the
exemption working silently as designed. **Category: pass**

### Step 5b — Coverage (informational only; never enters the verdict)

Read from the `totals` object of each runner's JSON, with the field names verified
against the generated files rather than recalled:

| suite | line (statement) % | branch % |
|---|---|---|
| unit | 76 % (`percent_statements_covered_display`) | 63 % (`percent_branches_covered_display`) |
| integration | 11 % | 9 % |

For completeness, the blended statement-plus-branch figure the `term` report's
`^TOTAL` line shows is 73 % (unit) and 11 % (integration) — reported separately so
it is not mistaken for line coverage. The integration figures are low by
construction: that suite drives shell entry points as subprocesses, which the
Python coverage instrumentation in-process does not observe. Coverage affects
nothing in this report.

### Step 6.11 — Spot-check outside the spec (time-boxed)

Three flags, each traced to something concrete, plus the observations recorded
under Caveats. The two that became findings are Finding 3 (an untested new
mechanism: the hygiene lint cannot read the guard shim) and Finding 1 (state the
requirements document itself named and nothing addressed). The third flag — the
missing `ai_v2.24.9` tag — is demoted to an observation under Caveats: it is
operator-origin, predates this RC, and fits none of the three categories as a
defect in this RC's work.

### Step 6.12 — Autonomous operation criterion

| # | question | answer | evidence |
|---|---|---|---|
| 1 | Tasks force-promoted from WAITING to BACKLOG by hand? | **NO** | every promotion in this cycle is a wake-script log line: `wake(coder): WAITING task CODER-20260923-002-git-guard-recursion: prerequisites satisfied, promoting to BACKLOG` (00:27:15) and the same shape for -003/-004 (00:48:02), -005 (01:18:42), -007 (02:03:41), -008 (03:02:37), `wake(writer)` for WRITER-006 (03:08:39), `wake(tester)` for this task (03:14:51) |
| 2 | `release-state.md` edited outside the three CM scripts? | **NO** | mtime `2026-09-23 00:25`, matching its own `## RC Opened At 2026-09-23T00:25:27+00:00` and `## RC Opened By Task CM-20260923-009-open-rc-2-24-10`; no later modification |
| 3 | Scripts patched mid-build? | **NO** | the five commits touching `team/scripts/` are this RC's own ticketed work, each on its task's feature branch and merged `--no-ff`; the *live install* — the running tooling — was not patched: the only file under `$PGAI_AGENT_KANBAN_ROOT_PATH` modified since the RC opened is `kanban.cfg` (see Caveats), which is an operator-owned configuration file, not a script under `team/scripts/` or `subagents/` |
| 4 | Queue files hand-edited? | **NO** | every entry for this cycle carries a marker consistent with its task's own `## State` (`[x]` for the six DONE tickets and CM-open, `[A]` for this task while WORKING, `[W]` for `CM-20260923-012-release-2-24-10`); a scan of every queue file found no malformed marker |
| 5 | Any agent other than CM invoked `fetch`, `pull` or `push`? | **NO** | answered from the file, as the step requires: no `logs/git-boundary-guard.jsonl` exists in any task folder of this cycle — PM-001, CM-009, CODER-002/-003/-004/-005/-007/-008, WRITER-006, or this task |

`Autonomous operation criterion: Manual interventions required: none`

One qualification on question 5, stated because the answer would read the same if
the boundary were simply switched off — which is the ambiguity BUG-0367 filed
against this exact evidence channel. The operator's live install still has an
empty `scripts/wake/git-guard/` directory, so the shim is not deposited there and
the pre-fix `[[ -d ... ]]` check (this RC's fix is not installed yet) prepends an
empty directory to PATH without warning. In this very TESTER session,
`PATH` begins with `$HOME/pgai_agent_kanban/scripts/wake/git-guard` while
`command -v git` resolves to `/usr/bin/git`. So the cycle's NO is a correct
reading of the evidence channel, and that channel is currently uninformative. The
RC's own fix is what makes this state announce itself next time, and CHECK 7
confirms a fresh install deposits the shim; details under Caveats.

For completeness: refusal records DO exist under
`$PGAI_AGENT_KANBAN_TEMP_DIR/sandbox-*/` from this verification's own deliberate
probes (18 records across several sandbox evidence files). They are TESTER's
probes, not an agent's dispatch, they were written to sandbox paths rather than any
task's `logs/`, and they are not a question-5 YES.

### Step 6.14 — Coding-standards spot-check

No new script under `team/scripts/` (so no missing `--help`);
`verify-install-policy.sh --help` still works after the CHECK 7 addition. No newly
hardcoded timeout or sleep. No duplicated implementation of something already in
`lib/`. `lint_gate_census_compliance.py`, `lint_dual_channel_pair.py`,
`lint_runnable_remedy.py` and `check_docs_citations.py` all report `ok`. The one
hit is provenance in new comments — Findings 2 and 3.

## Findings

### Finding 1: `critical` is absent from the deferral ranker's severity vocabulary

**Category:** gap
**Fix Effort:** small (1 CODER task)
**Systemic Risk:** medium (recurs on every `critical` bug filed, and the level
vocabulary is shared with `resolve_defer_posture`)
**Filed As:** BUG-0372-severity-rank-omits-critical (Path C-A — filed as BUG, code
defect / regression / test failure)

Traces to the requirements document's `## Acceptance Criteria` #1, "Each bundled
item is addressed in the implementation." The FIRST entry in the same document's
`## Bundled Items` list is the discovery WARNING that
`BUG-0363-git-guard-self-recursion-storm.md`'s severity field "is missing or
unparseable ('critical')". Nothing in this RC addressed it.

`_disc_severity_rank` (`team/scripts/lib/discovery_defer.sh:167`) ranks only
`low|medium|high` and returns 1 for anything else, so `critical` reaches
`_disc_severity_defer_decision`'s `unparseable` branch in the same column as the
string `bogus`. Measured:

```
low        -> rank=1          decision=deferred
medium     -> rank=2          decision=kept
high       -> rank=3          decision=kept
critical   -> rank=UNRANKED   decision=unparseable
CRITICAL   -> rank=UNRANKED   decision=unparseable
bogus      -> rank=UNRANKED   decision=unparseable
```

`critical` is a first-class severity everywhere else: `SOP-core.md` ("Rating a
Finding") defines it as the top rung, and `bugs/templates/BUG-TEMPLATE.md:7`
offers it first on the `**Severity:**` line. `BUG-0363`'s file is correct
(`**Severity:** critical` at line 12); the ranker is not.

Rated `medium` rather than `high` because the outcome is accidentally right — an
`unparseable` severity is kept as a candidate and eligible to be the bundling
trigger, which is what a critical bug deserves, so nothing was mis-bundled. What
is wrong is the narration (an operator is told a correctly authored emergency
report is malformed, in the message that prints the value it just read) and the
robustness (the right answer is reached through the error path). It predates this
RC by a wide margin — `git log -S'_disc_severity_rank'` puts the function's
introduction at `356b53c`, "Release v2.13.0" — so it is not a regression.

An honest note on scope, since a reader could reasonably disagree: the three
narration lines in `## Bundled Items` are log entries the discovery pipeline
appended, not bug files PM decomposed into tickets, so one reading is that AC #1
never meant them. I record this as a gap rather than dropping it because the
underlying condition is real and independently verifiable, and because a bundle
listing a warning as an item it does not then address is exactly the kind of thing
that is easier to fix than to keep re-reading.

### Finding 2: three internal bug identifiers shipped in new source comments

**Category:** bug
**Fix Effort:** small (1 CODER task; three lines)
**Systemic Risk:** low (isolated to two files; no runtime effect)
**Filed As:** BUG-0370-guard-shim-comments-cite-bug-id (Path C-A — filed as BUG)

`SOP-core.md` ("Naming: describe behavior, not scaffolding or provenance") states
that no internal bug identifier may reach a shipped artifact, and none of its
three sanctioned exceptions applies here. Three lines added by this RC cite
`BUG-0366`:

| file | line |
|---|---|
| `team/scripts/wake/git-guard/git` | 29 |
| `team/scripts/wake/git-guard/git` | 146 |
| `team/tests/fixtures/dashboard_render_fixture.py` | 121 |

`git diff ai_main..ai_rc/v2.24.10 -U0 | grep '^+' | grep -E 'BUG-[0-9]{4}'` returns
exactly these three and nothing else. Each already carries the behavioral sentence
beside it, so the citation is a removable parenthetical. The RC's own CODER ran a
sweep for precisely this (commit `d29042c`, "Reword BUG-ID citations in new
comments/messages to describe behavior") that did not reach them — which is
Finding 3's subject.

### Finding 3: the hygiene lint structurally cannot read the guard shim

**Category:** bug
**Fix Effort:** small-to-medium (2–3 CODER tasks: widen the selector, then sweep
the citations it surfaces, including 13 pre-existing `BUG-0112` lines)
**Systemic Risk:** medium (a stated project-wide law is unenforceable on a shipped
file, and will be for any future extensionless entry point)
**Filed As:** BUG-0371-hygiene-lint-misses-extensionless-scripts (Path C-A — filed
as BUG)

`lint_hygiene.py` builds its code surface by file-name suffix —
`_collect_files(code_targets, (".py", ".sh"))` at line 1624, and `_collect_files`
walks with `scan_dir.rglob(f"*{suffix}")` at line 604. `team/scripts/wake/git-guard/git`
sits under `team/scripts`, which IS a default scan directory, and `rglob` does
descend into `wake/git-guard/` — only the suffix filter excludes it. That
filename cannot change: being found as `git` on PATH is the entire mechanism.

Established causally rather than by reading:

```
python3 team/scripts/lint_hygiene.py --classes provenance   -> OK, 0 findings
grep -n 'BUG-0366' team/scripts/wake/git-guard/git          -> lines 29, 146
cp team/scripts/wake/git-guard/git team/scripts/lib/probe_shim.sh
python3 team/scripts/lint_hygiene.py --classes provenance   -> reports both lines
rm team/scripts/lib/probe_shim.sh                           -> back to 0 findings
```

The identical bytes are scanned under a `.sh` name and skipped under the name
`git`. A second half of the same blind spot: `_TEST_TREE_EXCLUDE_SEGMENTS` (line
507) excludes any path with a `fixtures` component, so `team/tests/fixtures/*.py`
— shared long-lived helper modules, not generated data — is scanned by neither
surface, which is why Finding 2's third line also went unseen.

Filed separately from Finding 2 because TESTER Step 10 requires one filing per
gap even where two share a root cause, and because the fixes are different changes
in different files. They are best sequenced Finding 2 first, then Finding 3, so
the lint does not go red in between.

### Finding 4: role file says "four contracts"; the document it cites now has five

**Category:** gap
**Fix Effort:** small (1 WRITER task; one line)
**Systemic Risk:** low (one cross-reference)
**Filed As:** BUG-0373-role-file-contract-count-stale (Path C-A — filed as BUG)

`SOP-core.md`'s Documentation Discipline Rule makes the matching documentation
artifact part of done. WRITER-006 added a fifth contract to
`docs/test-authoring-guidelines.md` and updated that document's own two counts
(lines 20 and 366), but `team/roles/TESTER.md:399` still reads "**The four
contracts for a test that reaches outside its own process**" and enumerates only
the original four — omitting the subprocess-ownership contract, which is the one
this RC's own BUG-0366 work needed. `grep -rn "four contracts\|five contracts"
team/roles/ docs/` returns exactly those three lines, so the drift is confined to
this one cross-reference.

Self-reported, not discovered: WRITER-20260923-006's `status.md` names it and
names the ticket's diff-scope constraint as the reason it was left ("a one-line
follow-up for the next loop"). Respecting that constraint was correct; this
finding is the follow-up made explicit rather than carried on trust. No gate
catches it — `check_docs_citations.py` reports `ok`, because it verifies that
cited documents and sections exist, not that a count inside a sentence still
matches.

### Findings recorded as pass / pass-with-caveat

- **BUG-0363, shim self-recursion** — `pass`. Allowed, refused, and
  probe-requiring commands all reach the correct verdict with the shim on PATH;
  `GIT_GUARD_REAL_GIT` honored; the `PGAI_GIT_GUARD_DEPTH` sentinel skips policy on
  a seeded re-entry; zero self-invocations and zero live shim processes at every
  sample point; caller-side glob audit confirms every `tag --list` / `branch
  --list` site quotes its pattern.
- **BUG-0365, fixture-local destinations** — `pass`. Eleven-case destination
  matrix correct in both directions, including the constraint the bug attached to
  any fix: `git -C <real dev tree> push origin HEAD` from role `coder` is still
  refused and recorded. The three named fixture files pass under the guard in the
  bug's own literal reproduction shape (13 passed, zero refusal records, where the
  bug measured 11 failures), and the full gated unit suite exits 0 under the
  guard.
- **BUG-0366, shim process leak** — `pass`. Verified with a control: the
  `ai_main` shim leaves an orphaned `sleep 120` at `PPid 1` under the same
  harness, the RC shim leaves none. The 38 tests in the two dashboard fixture
  files pass under the guard where 30 failed before, with zero leaked shims and a
  flat load average.
- **BUG-0367, guard presence check** — `pass-with-caveat`. All six wiring states
  behave correctly and CHECK 7's positive half was verified by a real isolated
  install. The caveat is wording: the WARNING still says "shim directory not found
  at ..." when the directory exists and only the file is missing. CODER-005's task
  constraint said the wording must not change, so the ticket was implemented as
  written; the sentence is now slightly inaccurate in the case it was extended to
  cover. Not filed — changing it would contradict the constraint that authorised
  the work, and a reader who follows the path in the message finds the empty
  directory immediately.
- **BUG-0364, engine manifest** — `pass-with-caveat`. Both missing stages
  registered, comment and list agree at nine, lint count 35 → 37, and the padding
  probe proves the stage is now measured. The caveat is that the shipped count
  moved 35 → 37 where AC #1 said "one more engine file"; CODER-008 documented the
  divergence and the composition table confirms two stages were missing, so the
  AC's intent is met and only its arithmetic — written against the bug's narrower
  narrative — undercounts. Not filed.
- **WRITER-006's two authoring contracts** — `pass`. Four load-bearing claims in
  the new prose were each traced to the shipped implementation (see Step 6.3). The
  one shortfall is Finding 4.

## Lint Findings (non-blocking)

- `test_anti_patterns` — check `AP3`, hardcoded `/tmp/pgai_kanban_tmp` literal at
  `team/scripts/lib/git_boundary_guard.sh` lines 40, 96, 404 (comments) and 435
  (executable: `local temp_root="${PGAI_AGENT_KANBAN_TEMP_DIR:-/tmp/pgai_kanban_tmp}"`).
  Reported identically by both runners; both still exited 0, as the class's CONVERT
  classification intends. Grepped `bugs/` first — the one prior bug in this class,
  `BUG-0345-test-anti-pattern-hardcoded-temp-literal`, has `## Status: done` and
  describes different sites, so nothing was available to cite — filed
  BUG-0374-guard-lib-hardcodes-temp-root-literal (Path C-A), one bug for the class
  for this run, listing all four instances with file, line and message, at the
  worst instance's severity (`low`).

Per Step 5c this section moves nothing on its own: it does not feed the Step 8
decision tree, does not raise `## Systemic Risk`, and BUG-0374 does not elevate
the recommendation. The recommendation below is `SHIP-WITH-CONCERNS` because of
Findings 1–4, not because of this section.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=15d freshness=stale — Stale on both counts: the record is 15 days old against a 7-day window and was taken at v2.9.20 rather than the v2.24.10 under verification; the bug that run filed, BUG-0167-cold-build-claude-abliterated-failure, has since been fixed and closed (`## Status: done`), and this RC's `lint_dockerfile_static` pre-flight reports all 3 Dockerfiles clean, so nothing is known about whether the images build today and no bug describing the tree is owed.

Read through `deep_verify_ledger_read_latest` in
`$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/lib/deep_verify_ledger.sh` (reader exit 0,
so Form A, not Form C); the verdict word is the ledger's own. No container build,
container engine, `run-cold-build-tests.sh` or `deep-verify.sh` was invoked by
this verification. `team/scripts/deep-verify.sh` exists and is executable, and the
tree ships 3 Dockerfiles, so neither of Step 5d's two gap faults applies.

## Caveats

**The `4980/30` vs `4970/40` count difference is the sandbox root, not the guard —
measured, not inferred.** The authoritative unguarded run reported 4980 passed /
30 skipped; the guarded run reported 4970 / 40. Because those two runs differed in
PATH, role, kanban root and temp root at once, a third run was made to isolate the
variable: the same throwaway `KANBAN_ROOT`/`PGAI_AGENT_KANBAN_ROOT_PATH`/temp root
as the guarded run, with the guard directory *off* PATH.

| run | kanban root | guard on PATH | exit | result |
|---|---|---|---|---|
| authoritative | live install | no (empty dir) | 0 | 4980 passed, 30 skipped |
| guarded | throwaway sandbox | yes | 0 | 4970 passed, 40 skipped |
| control | throwaway sandbox | **no** | 0 | 4970 passed, 40 skipped |

The control matches the guarded run exactly, so the ten tests that skip do so
because the kanban root is a throwaway directory with no installed tree, and the
guard changes no test's outcome. This also matches CODER-007's own controlled pair,
which measured 4970 / 40 both ways inside one sandbox.

**The `lint_changelog_freshness` FAIL in the two sandbox runs is the same
artifact, and `CHANGELOG.md` is fresh on the RC.** Both sandbox runs reported
`lint_changelog_freshness: FAIL — CHANGELOG.md is stale`; the authoritative run
reported `ok — CHANGELOG.md is fresh (RC mode: no differences)`. The cause is
visible in the finding's own remedy line, which points `--bugs-dir` at
`<sandbox>/projects/pgai-agent-kanban/bugs` — a directory that does not exist — so
the regeneration could not read any bug file and emitted the placeholder
`(one or more bug fixes — bug files unavailable)`. The guard-off control reproduces
it, which rules the guard out. This is not an RC finding and was not filed: a
gated runner pointed at a kanban root with no project tree is an unsupported
configuration, not a defect.

**The live install's git boundary guard is not enforcing anything right now.**
`ls -la $PGAI_AGENT_KANBAN_ROOT_PATH/scripts/wake/git-guard/` is empty (directory
created 2026-09-22 02:02, no `git` file inside), which is the live-state half
BUG-0367 documented. In this TESTER session `PATH` begins with that empty
directory while `command -v git` resolves to `/usr/bin/git`, and the pre-fix
`[[ -d ... ]]` check logged no warning. This is the *live install*, not the RC:
a real isolated `install.sh --no-system-cron` from this worktree deposits
`scripts/wake/git-guard/git` mode 755, verified here, and the RC's own fix makes
the empty-directory case announce itself. So CM's install of v2.24.10 is expected
to heal it — but until that install runs, no non-CM role on this host is guarded,
and question 5 of the autonomous criterion cannot distinguish a clean cycle from an
absent boundary. Flagged for the operator rather than filed, because the RC already
carries both halves of the fix.

**`ai_v2.24.9` was never tagged.** `release-state.md` reads
`## Last Released v2.24.9` and `ai_main` carries `VERSION` `v2.24.9`, a
`release-notes/v2.24.9.md`, and a CHANGELOG entry — all added by commit `fa8a362`,
"Updated release note with manual merge", authored by the operator. No
`ai_v2.24.9` tag exists (`git tag --list 'ai_*' | sort -V | tail` ends at
`ai_v2.24.8`), which is why `pp_last_released_version` returns `v2.24.8` while
`release-state.md` says `v2.24.9`. Per Step 6.1 only a *malformed*
`## Last Released` is a finding and `v2.24.9` is well-formed, so this is recorded
as an observation: it is operator-origin, predates this RC, and the RC-greater-than
check passes against either value. Worth an operator's attention only because the
v2.24.9 tag will stay permanently absent from the tag history once v2.24.10 ships.

**`kanban.cfg` was modified at 00:40, inside the RC window.** It is the only file
under `$PGAI_AGENT_KANBAN_ROOT_PATH` with an mtime after the RC opened. It is an
operator-owned configuration file, not a script under `team/scripts/` or
`subagents/`, so it is not a question-3 YES under the autonomous criterion's own
definition. It is not version-controlled, so what changed cannot be determined
from here; recorded for completeness rather than as a finding.

**`team/scripts/verify-install-policy.sh`'s header numbering is one ahead of its
runtime labels.** The new entry is item `8.` in the header comment list but prints
as `=== Check 7: ...` at runtime, because the header list has always enumerated
policies rather than checks (its items 2 and 3 both land inside runtime "Check 2").
Pre-existing style that this addition follows rather than introduces; not filed.

**The fixture-local exemption's default root includes the worktree tree.** The
exemption keys on `PGAI_AGENT_KANBAN_TEMP_DIR`, whose default
`/tmp/pgai_kanban_tmp` is also where per-task worktrees live, so a non-CM role
could `git push` into a worktree path unrefused — and a worktree shares its object
store and refs with the real dev tree. This is not a widening of the boundary the
guard exists to enforce: that boundary is about origin, and agents already write to
local branches by committing and merging in their own worktree, which the guard has
never touched. Recorded so the surface is on the record rather than discovered
later. Not filed.

**Skip-conditioned coverage that was read rather than executed.** The shim's
documented fallback to a plain `exec` when `setpriv` is unavailable was verified by
reading; `setpriv` is present on this host (`/usr/bin/setpriv`), so the fallback
branch could not be exercised without removing a system binary, which is out of
scope for a verification run.

**Test fidelity.** The RC's new coverage exercises the real breaking conditions
rather than proxies: `test_shim_hand_off_dies_when_its_caller_is_killed_by_a_timeout`
drives a real caller kill, `test_process_watchdog_sees_no_second_shim_and_bounded_descendants`
counts real descendants, and the six destination cases in
`test_git_boundary_guard.py` resolve real remotes and real paths. CODER-003
reported one contention-dependent timing flake in the watchdog test under full-suite
load; it passed in all three full-suite runs made here (4980/30, 4970/40, 4970/40,
zero failures across all three), so no flake is filed, but a future intermittent
failure in that test has a prior report behind it.

**Independent re-verification.** `artifacts/` held no prior `report.md` or
`gaps.md` and no `.previous-RUN-N` file when this run began, so this is a fresh
first run rather than a re-queue. Every suite was re-run and every criterion
re-evaluated here; nothing in this report is carried from another agent's summary.
Where a prerequisite task's `status.md` claimed a measurement, the measurement was
re-taken (the guarded suite, the install deposit, the lint counts, the padding
probe).
