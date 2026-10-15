# Verification Report: ai_rc/v2.15.0

**Task:** TESTER-20260910-028-verify-2-15-0
**Project:** pgai-agent-kanban
**RC branch:** `ai_rc/v2.15.0` @ `07e5e7a`
**Base:** `ai_main`
**Requirements doc:** `projects/pgai-agent-kanban/requirements/v2.15.0-diagnostics-suite.md`
**Worktree verified:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260910-028-verify-2-15-0` (detached HEAD @ `07e5e7a` == `ai_rc/v2.15.0`, clean working tree, no stashes)
**Verified:** 2026-09-10

## Summary

v2.15.0 ships the diagnostics suite the requirements document asked for, and the
substance of it works. `why.sh` with no arguments now walks every registered
project in `projects.cfg` priority order and prints one verdict line each; each
line carries the verdict word, a cause, and a runnable remedy in the same
sentence; the exit codes are severity-ordered and the no-argument walk exits with
the maximum across the walk. I exercised all four verdict words plus the stalled
variant against a throwaway fixture kanban root and confirmed every documented
exit code (0/2/3/4/5) and both dual-channel refusals (`why.project_not_found`,
`why.details_requires_project`) fire as specified, that the walk is idempotent
(fixture byte-identical after three invocations), and that `--details` renders
locks with holder/age/provenance, queue tails with raw markers beside each task's
real `status.md` state, the embedded `show-queues --details` pane, the full
posture block (the four deferral lines survive byte-identical), and the most
recent refusal from the new per-project refusal ledger. The ledger itself
behaves as specified: a bash-emitted and a Python-emitted refusal produce
byte-compatible records read by the one shared reader, a second refusal replaces
the first rather than appending, an unwritable ledger path leaves the refusal's
exit status and stderr text untouched, and a malformed record refuses by name
instead of reporting a healthy "never recorded" empty state. `verify-rc-state.sh`
routes every branch name through `pp_prefix_branch` and no longer carries a
second markdown field reader. `GET /verdicts` shells out to `verdict-report.sh`
and parses only its machine-readable blocks, and the ICD freshness, ICD compat,
API parity and gate-census lints all exit 0.

Two defects stand between this and a clean pass. The unit suite is red: the
documentation ticket added `docs/operator-api.md` citations into two documents
the installer delivers, without adding `operator-api.md` to
`team/config/docs-manifest.txt`, so the citation dangles on every installed and
upgraded root and the guard that exists to catch exactly that fails twice. And
`why.sh --details` throws away the verdict's exit code — a HALTED lane and a
healthy one both come back as exit 0 — which contradicts the exit-code contract
this same release documents as "the whole contract." Both are small, isolated
fixes; neither prevents an operator from using the release. Both are filed via
Path C (autonomous follow-up filing — non-blocking).

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

medium

## Test Suites (gated runners — the authoritative verdict)

| Suite | Command | Exit | Result |
|---|---|---|---|
| Unit | `bash team/scripts/run-unit-tests.sh --coverage` | **1** | 2 failed, 3703 passed, 3 skipped (598.83s) |
| Integration | `bash team/scripts/run-integration-tests.sh --coverage` | **0** | 435 passed, 1 skipped (396.64s) |

`UNIT_EXIT == 1`, so the suite-pass precondition for a `PASS` recommendation is
not met. Both failures are the same defect and are reported as Finding 1.

Every test file this RC added ran and passed:

| File | Result |
|---|---|
| `team/tests/unit/test_why_verdict_engine.py` | 15 passed |
| `team/tests/unit/test_why_details_pane.py` | 18 passed |
| `team/tests/unit/test_refusal_ledger.py` | 21 passed |
| `team/tests/unit/test_verdict_report.py` | 11 passed |
| `team/pgai_agent_kanban/api/tests/test_cli_rest_verdict_parity.py` | 3 passed |
| `team/pgai_agent_kanban/api/tests/test_verdicts_endpoint.py` | 15 passed |
| `team/tests/integration/test_diagnostics_fixture_matrix.py` | 4 passed |
| `team/tests/integration/test_verify_rc_state_two_project_prefix_fixture.py` | 3 passed |

## Code Coverage

Informational only; does not enter the PASS precondition or the risk rating.

- Unit:        line 75%  |  branch 61%   (14784 stmts, 4462 branches)
- Integration: line 8%   |  branch 6%    (same tree; the integration suite exercises subprocesses, so in-process coverage is structurally low and always has been)

## Findings

### Finding 1: installed docs cite `docs/operator-api.md`, which the installer does not deliver — unit suite red

**Category:** bug
**Fix Effort:** small (1 CODER task — one line in a manifest)
**Systemic Risk:** medium (a real defect that ships and gets fixed next iteration: the gated unit runner is red, which is the signal CM and the next TESTER read, and an installed operator following the pointer finds nothing)
**Regression:** yes (this RC's documentation commits introduced both citations; neither exists on `ai_main`)
**Integrity Damage:** no
**Filed As:** BUG-0227 (Path C-A — filed as BUG: code defect / regression / test failure)

`bash team/scripts/run-unit-tests.sh` exits 1 with:

```
FAILED tests/unit/test_installed_governance_docs_citations_resolve.py::test_every_installed_governance_docs_citation_resolves_under_fresh_install
FAILED tests/unit/test_installed_governance_docs_citations_resolve.py::test_every_installed_governance_docs_citation_resolves_under_upgraded_root
```

Both report the same two dangling citations:

```
2 citation(s) did NOT resolve:
  - docs/operator-commands.md cites 'docs/operator-api.md', which is not installed
  - docs/OPERATIONS.md cites 'docs/operator-api.md', which is not installed
```

Traced: `docs/operator-commands.md:523` and `docs/OPERATIONS.md:414` both cite
`docs/operator-api.md`. `git show ai_main:docs/operator-commands.md | grep
operator-api.md` and the same on `docs/OPERATIONS.md` return nothing, so both
citations arrive with this RC (commits `513dcbd`, `1754de0`, `a4b82cf`).
`team/config/docs-manifest.txt` — the one list `install.sh` and
`team/scripts/upgrade.sh` both read — does not name `operator-api.md`. The
manifest's own header states the rule that was broken: "A document listed here
must never cite a `docs/` path this file omits — that would just move the
dangling pointer one hop deeper."

Note on how this got through rather than who let it through: the ticket that
added the citations (`WRITER-20260910-026-diagnostics-operator-docs`) carried the
acceptance criterion "No source code, script, test, or configuration file is
created or modified by this ticket," which forbade its author from touching the
manifest at all. The decomposition, not the authoring, is where the gap sits —
a documentation ticket that adds a `docs/` citation needs the manifest edit
inside its own scope or a companion ticket that owns it. That observation is
recorded in BUG-0227 for whoever picks it up.

### Finding 2: `why.sh --details` discards the verdict exit code

**Category:** bug
**Fix Effort:** small (1 CODER task)
**Systemic Risk:** low (the printed verdict line is correct in every case; only the process exit status is wrong, and the documented cron recipe does not use `--details`)
**Regression:** yes (the verdict exit-code contract is new in this RC, and the `--details` path never honoured it)
**Integrity Damage:** no
**Filed As:** BUG-0228 (Path C-A)

`why.sh` publishes a stable exit-code contract in its own header and in
`docs/operator-commands.md` under "Exit codes," where the cron section states
"Nothing else is needed to automate against this — the codes above are the whole
contract." Adding `--details` collapses every code to 0. Measured against a
throwaway fixture root:

```
why.sh --project beta                       -> exit 4   (BLOCKED)
why.sh --project beta --details             -> exit 0
why.sh --project beta [HALT present]        -> exit 5   (HALTED)
why.sh --project beta --details             -> exit 0
why.sh --project gamma                      -> exit 2   (IDLE)
why.sh --project gamma --details            -> exit 0
why.sh --project alpha [stalled 65m]        -> exit 3   (RUNNING, stalled)
why.sh --project alpha --details            -> exit 0
```

Cause, in `team/scripts/why.sh`: the `--details` branch runs
`_why_print_details "${_project}"; exit $?`. `_why_print_details` resolves the
verdict (it needs the `line` field) but never reads the `exit_code` field out of
`verdict_resolve_project`'s block and never sets `_WHY_LAST_EXIT_CODE`, so its
return value is only "did every section render." The verdict-only path
immediately below it does read `exit_code` and exits with it. Guard behaviour
under `--details` is otherwise intact: a section refusal still exits 1 with
nothing on stdout, which I verified against a malformed refusal ledger.

### Finding 3: acceptance criteria verified — the diagnostics suite itself

**Category:** pass

Every deliverable in the requirements document, and every per-ticket acceptance
criterion I could execute, checks out. Recorded here so the passes are on the
record alongside the two defects.

**Requirement — `why.sh`: no args = all projects in priority order; four verdict
words; cause + remedy in the sentence.** Verified behaviourally against a
fixture kanban root with four projects. `why.sh` (no args) printed one line per
project in `projects.cfg` priority order. All four words rendered from their
seeded conditions: `IDLE` from `enabled=false`, `IDLE` from an empty task tree,
`HALTED` from a project-scoped `HALT` (line quoted the HALT file's reason),
`RUNNING` from a `WORKING` task, `BLOCKED` from a `BLOCKED` task with no
`WORKING` task. Each line carried `cause:` and `remedy:` fragments naming a
runnable next step.

**Requirement — RUNNING verdicts carry progress evidence; churn never grades
healthy.** A `WORKING` task 0 minutes in state rendered
`RUNNING — task <id> is WORKING, 0m in that state`. Pinning `VERDICT_NOW_EPOCH`
65 minutes past the task's `status.md` mtime rendered
`RUNNING — task <id> has been WORKING for 65m, past the 60m stall threshold;
cause: no state transition in that window suggests churn or a hang` and exited 3,
not 0.

**Requirement — exit codes cron-watchable.** 0/2/3/4/5 confirmed individually
(above). The no-argument walk over a `RUNNING` (exit 0) and a `BLOCKED` (exit 4)
project exited 4 — the numeric maximum, matching the documented aggregation rule.
Both pre-flight refusals exited 1 dual-channel with a stable key, a
plain-language message and a runnable remedy. An unknown flag was rejected.
(The `--details` exception to this is Finding 2.)

**Requirement — `--details`: locks, queue tails with raw markers, embedded
show-queues, posture, most recent discovery skip reason.** All five sections
rendered. A seeded lock printed its path, a human-readable age and its full
provenance record (`holder=… pid=… op=… task=…`) via `lock_provenance.sh`'s own
readers. Queue tails printed `[x] <task-id>  status.md: WORKING` for a marker
that disagreed with the task's real state — both values visible, neither silently
preferred. `show-queues.sh --project <name> --details` is invoked as a
subprocess (`why.sh:194`), not re-implemented. The posture block printed the
HALT/HALT-AFTER state, the four deferral lines with their tiers unchanged, the
Active RC and the pending-bundle list. Every section prints an explicit empty
state rather than nothing. Running `--details` twice left the fixture root
byte-identical.

**Requirement — every discovery/gate refusal is dual-channel AND its most recent
instance is queryable.** Exercised directly: a bash-emitted refusal wrote a
record naming the machine key, message, remedy, timestamp and raising surface; a
second refusal replaced it (record count stayed 1, content was the newer one); a
Python-emitted refusal produced a record the bash reader parsed identically
(`surface=pyemit.py:my_refusal_site`, derived automatically from the caller's
stack frame); with the project directory made unwritable the refusal still
printed its three stderr lines unchanged and still propagated its caller's exit
status (verified at 7); a malformed ledger (header present, `Remedy` line
removed) made `--details` exit 1, name the ledger by absolute path, say
`malformed`, name the missing field, print nothing to stdout, and never emit the
healthy `no discovery/gate refusal recorded` string. `grep -nE
"environ|sys.argv|secrets|getenv|subprocess|shell"` over
`team/pgai_agent_kanban/lib/refusal_ledger.py` finds only the documented
`PGAI_PROJECT_NAME` read — no environment dump, no command line, no secrets path.
Discovery's Active-RC-in-flight skip and its four ceiling rejections all now emit
through `pgai_emit_error_contract` with the project as the fourth argument and
each carries a `CENSUS-ANCHOR` marker; `team/scripts/lint_gate_census_compliance.py`
reports 0 offenders across 35 files with 139 anchored sites resolved.

**Requirement — `verify-rc-state`: honors `branch_prefix`; shares validators with
the release-state loader.** `grep` over `team/scripts/verify-rc-state.sh` finds
every RC branch name constructed through `pp_prefix_branch` (lines 774, 817). The
one literal `rc/*` glob (line 793) is the deliberate stray-unprefixed-branch
probe and is skipped when `rc_glob` already equals the bare glob, so an
unprefixed project never double-reports. The hand-rolled `_read_md_field` Python
heredoc is deleted; the `Active RC` raw read now goes through
`pp_run_ops pgai_agent_kanban.cm.read_state_field`. The two-topology integration
fixture passes (3 tests), and the fixture-matrix test proves check (c) produces
byte-identical finding text on the prefixed and bare projects.

**Requirement — REST parity endpoint returns the same verdicts as JSON.**
`GET /verdicts` shells out to `team/scripts/verdict-report.sh` and parses its
`key=value` blocks — never `why.sh`'s prose. `test_cli_rest_verdict_parity.py`
drives both surfaces over one fixture and compares them against each other
rather than against pinned literals, so a drift in either surface fails it; 3
passed. `python3 team/scripts/lint_icd_freshness.py`, `lint_api_parity.py` and
`lint_icd_compat.py` each exit 0 against the committed `docs/api/icd.json`
(ICD 1.4.0, compatible superset of all five supported baselines). The
registered-but-no-directory case returns an "unresolved" entry at HTTP 200, and
an unregistered `?project=` returns 422 — neither is 5xx.

**Acceptance — P8's fixture matrix.** `test_diagnostics_fixture_matrix.py` ships
all four named shapes (stale-marker, invalid-ledger, churn-progress,
prefixed-project), each naming its single command in its own docstring, each
reusing rather than re-implementing the two-project fixture, each pinning time
through `VERDICT_NOW_EPOCH` rather than sleeping, and each repointing
`PGAI_AGENT_KANBAN_ROOT_PATH` at a `tmp_path` root. 4 passed under
`run-integration-tests.sh`.

**Documentation.** The four-verdict vocabulary appears in full in exactly one
place (`docs/operator-commands.md`), with `docs/OPERATIONS.md` and
`docs/operator-api.md` referencing it rather than carrying a second copy. The
exit-code table's numbers match the shipped script's header table. `why.sh` is no
longer documented as requiring `--project`. The retired `why.project_required`
refusal has no surviving reference anywhere in `docs/` or `team/`. No
forward-looking "planned/forthcoming/not yet functional" language describes any
part of this release. The WRITER commits touched only the three documents.
(The one documentation defect is Finding 1.)

### Finding 4: release-state and prereq graph — no drift

**Category:** pass

`pp_last_released_version` returns `v2.14.3`; `semver_gt v2.15.0 v2.14.3` holds.
`release-state.md` reads `Active RC: v2.15.0`, `Last Released: v2.14.3` (a
well-formed `vX.Y.Z` value, expected by schema, not drift), and the
`ai_rc/v2.15.0` branch exists in the dev tree. `bash scripts/verify-rc-state.sh
pgai-agent-kanban` reports **0 errors, 14 warnings, 73 ok**; all 14 warnings are
the pre-existing "PRIORITY backlog `[x]` … not found in any active requirements
file" informational lines, unchanged by this RC.

Prerequisite graph for the cycle: acyclic, correctly bookended
(`CM-20260910-027-open-rc-2-15-0` at the head, `TESTER-20260910-028-verify-2-15-0`
and `CM-20260910-030-release-2-15-0` at the tail), every referenced task ID
resolves to an existing task folder, sequence numbers 020–030 consistent and
non-colliding.

### Finding 5: pre-flight and hygiene checks

**Category:** pass

- Pre-flight merge verification passed: every DONE task in the cycle has its
  feature branch already deleted post-merge (`git branch --list 'ai_feature/*'`
  is empty in the dev tree), which the procedure treats as merged. No unmerged
  branch, no BUG-92 zero-commit anomaly.
- Worktree `HEAD` (`07e5e7a`) equals `ai_rc/v2.15.0`; `git status --short` empty;
  `git stash list` empty.
- Skip-cites-real-bug gate: no placeholder skip IDs in `team/tests/`; every cited
  `BUG-NNNN` resolves to a file in `bugs/`. The gated runners' own
  `skip_cites_real_bug` lint reports OK on both suites.
- Step 6.8 synthetic release simulation: the RC diff touches
  `team/scripts/verify-rc-state.sh` and `team/scripts/lib/discovery_guards.sh`,
  so the lane applies. `tests/integration/test_cm_release_synthetic.py` ran
  inside the integration suite: 15 passed.
- Coding-standards spot-check: both new operator verbs (`why.sh`,
  `verdict-report.sh`) ship a `--help` naming every flag; the stall threshold and
  the exit codes are named constants in one place (`verdict.sh`) and mirrored,
  not re-derived, in `why.sh`'s header; `why_details.sh` reuses
  `lock_provenance.sh`, `halt_state.py`, `describe_token`, `discovery_bundle.sh`
  and `discovery_state.sh` rather than re-implementing them. The one hit is the
  provenance litter recorded under Lint Findings.

## Lint Findings (non-blocking)

- `comment_provenance` — 3 instances. Two are new in this RC:
  `team/scripts/verify-rc-state.sh:54` and
  `team/tests/integration/test_verify_rc_state_two_project_prefix_fixture.py:8`,
  both citing task ID `CODER-20260910-023` in a comment/docstring — **filed
  BUG-0229 (Path C-A)**, which lists every instance by `<path>:<line>`. The
  third, `release-notes/v2.14.2.md:42` citing `TESTER-20260909-045`, is
  pre-existing and **cites BUG-0224 (open)**; no duplicate filed.
- `governance_byte_budget` — `pm over governance byte budget: assembled=76391
  bytes budget=73728 bytes`. Pre-existing (this RC changes no governance file);
  **cites BUG-0226 (open)**; no duplicate filed.

Both classes appear identically in `findings-unit.json` and
`findings-integration.json`. Per the quality-class rule these do not, on their
own, move `## Recommendation` or `## Systemic Risk`.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=3d freshness=stale — the last recorded lane run was red on the claude-abliterated flavor under both engines and on podman/rocky9, and it is stale on version (v2.9.20 is not the v2.15.0 under verification) though inside the 7-day window; it cites BUG-0167, filed by that deep-verify run and since closed, and no newer run has re-exercised the tree.

Not re-filed and not re-derived: the deep-verify run that produced this verdict
filed its own bug at the time. `deep_verify = off` in this project's
`project.cfg [release]`, so the scheduled trigger does not fire here; the
pre-release freshness check remains the gate before a public tag.

## Autonomous criterion

Manual interventions required: **none**.

1. Any task force-promoted from WAITING to BACKLOG by hand? **NO.** The status
   trail shows the wake script's own promotion note, and every cycle task
   (020–027) reached DONE through the normal path.
2. Was `release-state.md` edited outside `cm-open-rc.sh` / `cm-release.sh` /
   `cm-cancel-rc.sh`? **NO.** Its four fields are exactly what `cm-open-rc.sh`
   writes, `RC Opened By Task` names `CM-20260910-027-open-rc-2-15-0`, and
   `verify-rc-state.sh` reports 0 errors against it.
3. Any scripts patched mid-build? **NO.** Every commit on `ai_main..HEAD` is a
   ticket's own deliverable followed by its merge commit — one commit or a small
   group per ticket, in ticket order, no out-of-band tooling fix, no empty
   commit (the runner's `rc_empty_commits` lint reports OK).
4. Any queue file manually edited? **NO.** `tester_backlog.md` carries this task
   as `[A]` written by the wake script; the surrounding entries are in the normal
   appended order.

## Caveats

- **`--details` under an ambient `PGAI_PROJECT_NAME` is documented, not a
  defect.** In an agent wake shell `PGAI_PROJECT_NAME` is exported, so a bare
  `why.sh` reports one project instead of walking all of them. This is the
  framework-wide `operator_args_project` precedence and
  `docs/operator-commands.md` states it explicitly ("Falls back to
  `$PGAI_PROJECT_NAME`. Omit it entirely to walk every registered project."). All
  no-argument walk checks in this report were run with the variable unset.
- **Cosmetic double prefix in the malformed-ledger diagnostic.** The stderr line
  reads `refusal_ledger: refusal_ledger: record in <path> is missing required
  field(s): remedy -- ledger is malformed` — the adapter prefixes a message that
  already carries the prefix. It names the file and the missing field correctly,
  so nothing is lost; recorded as an observation, not filed.
- **`why.project_not_found` records no ledger entry.** The refusal fires before a
  project is resolved and passes no project argument, so `record_refusal`
  correctly no-ops rather than guessing a directory. No acceptance criterion asks
  for it; noted only so a future reader does not mistake it for a gap.
- **Integration coverage of 8% is structural, not a regression.** The integration
  suite drives scripts as subprocesses, so in-process line coverage has always
  been low for it. Coverage is informational and does not enter any verdict here.
- **The live kanban root already holds a refusal-ledger record, written before
  this verification ran, and it describes nothing about the project's lane.**
  `projects/pgai-agent-kanban/refusal-ledger.md` (mtime 2026-09-10 09:41, three
  hours before this task started) holds a record whose `surface` is
  `lock_provenance.sh:pgai_lock_acquire` and whose message names a pytest temp
  path under `/tmp/pgai_kanban_tmp/tests/pytest-of-rocky/…`. It was not written
  by this verification, and it was not written by either gated runner — the
  pytest harness's `_block_live_kanban_writes` autouse fixture redirects
  `PGAI_AGENT_KANBAN_ROOT_PATH` at a temp root, and my own two runner
  invocations left the file's mtime untouched at 09:41. It therefore came from
  an ad-hoc invocation made in a wake shell where the live root and
  `PGAI_PROJECT_NAME` were both ambient. The shipped behaviour is as specified —
  the emitter records every refusal for the project in scope, which is the point
  of the feature — but the consequence is worth stating: once v2.15.0 is
  installed, `why.sh --project pgai-agent-kanban --details` will present that
  record under `== discovery skip reason ==` as the project's most recent
  discovery/gate refusal, and it is nothing of the kind. The rendered line does
  carry the record's timestamp, so an attentive operator can see it is stale.
  Recorded as an observation, not filed: no code change is owed by any
  acceptance criterion in this release, and the record is replaced by the next
  genuine refusal. It is worth an agent-discipline note that ad-hoc script
  invocations in a wake shell now leave a durable mark on live kanban state,
  which was not true before this release.
- Prior-run artifacts: none. `artifacts/` was empty at task start, so this is a
  fresh first run with no rotated `.previous-RUN-N` files to reconcile against.

## Path C Filings

| ID | Path | Finding | Severity |
|---|---|---|---|
| BUG-0227 | `projects/pgai-agent-kanban/bugs/BUG-0227-docs-manifest-omits-operator-api.md` | Finding 1 | high |
| BUG-0228 | `projects/pgai-agent-kanban/bugs/BUG-0228-why-details-discards-verdict-exit-code.md` | Finding 2 | medium |
| BUG-0229 | `projects/pgai-agent-kanban/bugs/BUG-0229-shipped-code-cites-task-id.md` | Lint Findings (`comment_provenance`) | low |

Cited without re-filing: BUG-0224 (open, provenance in `release-notes/v2.14.2.md`),
BUG-0226 (open, pm governance byte budget), BUG-0167 (closed, the deep-verify
record's own bug).
