# Verification Report: rc/v2.10.2

**Task:** TESTER-20260907-057-verify-2-10-2
**Requirements doc:** `projects/pgai-agent-kanban/requirements/v2.10.2-priority-bundle-20260907.md`
**Bundled item:** `PRIORITY-0001-helloworld-behavior-fixtures-unrunnable.md`
**RC branch / commit:** `ai_rc/v2.10.2` @ `652bb7f1773266d8a5ebec00ed14946ca2d19ca6`
**Worktree:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260907-057-verify-2-10-2` (detached HEAD, clean, no stashes)
**Verified:** 2026-09-07

## Summary

RC v2.10.2 is a single-item PRIORITY bundle closing PRIORITY-0001: the
`team/demos/helloworld-demo` flow could not be exercised on a stock install
because it required a `pgai-helloworld` project registered in the operator's
live `projects.cfg`, which is why the v2.10.0 acceptance criterion "agent
behavior fixtures unchanged on the helloworld flow" went unmeasured twice. The
RC delivers `team/scripts/harness/helloworld-flow-check.sh`, which stands up a
throwaway self-registering kanban root under `$PGAI_AGENT_KANBAN_TEMP_DIR` and
drives the real decompose → CODER → verify → ship flow through the production
`create-project.sh`, `intake.sh`, `pm_materialize.py`, `cm/open-rc.sh` and
`cm/release.sh`; a `--capture` / `--compare` contract with three committed
golden outcome files; one integration-test module that runs the harness as a
real subprocess and asserts against the golden, including a seed-detect-revert
cycle proving the check can fail; a resolution of the two-harness overlap with
`engine-behavior-baseline.sh` recorded in both headers; and a `team/TESTING.md`
section naming the check, its question, its command, and — honestly — the limit
of what a green run proves.

All seven of PRIORITY-0001's acceptance criteria are met, and all three of the
bundle's own. Every one was verified by running the thing, not by reading it:
the harness was executed five times from this worktree, the live install was
snapshotted before and after, the golden compare was seeded with a change and
watched to fail, and both gated runners were re-run from scratch rather than
cited from the CODER verification record.

Four defects were found. None is in the RC's own deliverable's behaviour, and
none prevents the release from working. Two are pre-existing defects in the
shipped release engine and the ticket materializer that the new harness is what
exposed — which is a strong endorsement of the deliverable, and is exactly the
class of thing PRIORITY-0001 said an end-to-end run would surface and a snapshot
comparison would not. Two are hygiene defects this RC introduced. All four are
filed via Path C (autonomous follow-up filing — non-blocking).

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

medium

## Suite Results (authoritative — gated runners, re-run for this report)

Both runners were executed fresh from the verification worktree at `652bb7f`.
The exit codes below are the suite verdict; no direct-pytest result is quoted
anywhere in this report.

| Runner | Command | Exit | Result |
|---|---|---|---|
| Unit | `bash team/scripts/run-unit-tests.sh --coverage` | **0** | 3225 passed, 3 skipped in 397.67s |
| Integration | `bash team/scripts/run-integration-tests.sh --coverage` | **0** | 339 passed, 1 skipped in 219.38s |

The RC's new module `tests/integration/test_helloworld_flow_harness.py` ran and
passed (2 tests) inside the integration run. Both runners reported
`bare_tmp_cleanliness: OK` and `listener_cleanliness: OK`.

Note on the captured-output files: a stale `unit_exit.txt` from an earlier
session's run was present in the shared temp directory at the start of this
task. It was ignored; the numbers above come from this run's own files, checked
by mtime.

## Code Coverage

Informational only. Does not enter the PASS precondition and is not a finding.

- Unit:        line 74.2%  |  branch 60.9%   (14136 stmts, 4316 branches)
- Integration: line 7.6%   |  branch 6.0%

Source: the `totals` object of `$PGAI_AGENT_KANBAN_TEMP_DIR/coverage/unit.json`
and `integration.json`, fields `percent_statements_covered` and
`percent_branches_covered` (field names confirmed against the generated files,
not assumed).

## Acceptance Criteria — PRIORITY-0001

| # | Criterion | Verdict | Evidence |
|---|---|---|---|
| 1 | Flow runs to completion on a stock install with no manual `projects.cfg` edit, from a single documented command | **pass** | `bash team/scripts/harness/helloworld-flow-check.sh --compare <dir>` — exit 0, all of decompose/verify/ship PASS, `compare OK — 3 outcomes match goldens`. `projects.cfg` still registers exactly 1 project. Run five times total. |
| 2 | The run produces decompose, verify and ship outcomes in a stable, diffable artefact | **pass** | `--capture` writes `decompose.txt` / `verify.txt` / `ship.txt`; each is 3-8 lines of `key: value`, readable in a review. |
| 3 | A golden copy is committed, and a test (or verb) compares a fresh run against it and fails on any difference | **pass** | Golden at `team/tests/fixtures/helloworld-flow/`. Two consecutive `--capture` runs into different directories were byte-identical (`diff -r` silent). Seeding `ai_v0.1.0` → `ai_v0.1.1` into a captured `ship.txt` made `--compare` exit **1** and print the diff hunk naming `ship.txt`; reverting the seed returned it to exit 0. `test_helloworld_flow_harness.py` runs the same compare inside the suite. |
| 4 | The run touches no live kanban state; `PGAI_AGENT_KANBAN_ROOT_PATH` redirected under `$PGAI_AGENT_KANBAN_TEMP_DIR`; operator's `projects.cfg` byte-identical before and after | **pass** | `sha256sum projects.cfg` identical before/after (`11db7a38…6e36`). `find $KANBAN_ROOT -type f -newermt '-6 minutes'` returned nothing outside this task's own logs. Live task count 85 → 85. Dev-tree worktree HEAD, commit count (40) and dirty count (0) all unchanged. |
| 5 | `team/TESTING.md` names the check, says what question it answers, gives the command | **pass** | New section "Which check answers \"did agent behaviour change\"" — names the path, the one-sentence question, the exact command, the 0/1/2 exit meanings, and the golden's location. |
| 6 | The `engine-behavior-baseline.sh` overlap is resolved either way, and the resolution is stated in the harness's own header | **pass** | Resolved as KEPT-not-superseded. `engine-behavior-baseline.sh` gains a `RELATIONSHIP TO helloworld-flow-check.sh` section; `helloworld-flow-check.sh` gains the mirror section; `TESTING.md` states the same. Both name the other by path and neither claims the other's question. |
| 7 | Both gated runners exit 0 with the new work in place | **pass** | See Suite Results above — re-run independently for this report. |

## Acceptance Criteria — bundle document

| Criterion | Verdict | Evidence |
|---|---|---|
| Each bundled item is addressed in the implementation | **pass** | PRIORITY-0001's full scope is covered — see the seven rows above. |
| All Python files compile cleanly | **pass** | `python3 -m py_compile team/tests/integration/test_helloworld_flow_harness.py` (the RC's only changed `.py`) exit 0; both suites collected without collection errors. |
| All bash files pass syntax check | **pass** | `bash -n` exit 0 on all three changed `.sh` files: `helloworld-flow-check.sh`, `lib/throwaway-root.sh`, `engine-behavior-baseline.sh`. |

## Findings

### Finding 1: `cm-release.sh` Step 13c can never find `promote_bundled_items.py`

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (a real defect an agent hits on every release, but it predates this RC by three releases, fails loudly rather than silently, and leaves state unchanged rather than corrupted — see the Systemic-Risk Rubric in team/SOP.md)
**Regression:** no (introduced by the v2.9.20 engine decomposition, commit `58702c4`, which is the entire history of `team/scripts/lib/cm/release_finalize.sh`; this RC's diff touches no file under `team/scripts/cm/` or `team/scripts/lib/`)
**Integrity Damage:** no (the step prints ERROR and WARNING and advances nothing; no state is lost or corrupted)
**Filed As:** BUG-0181 (Path C-A — filed as BUG: code defect / regression / test failure)

`team/scripts/lib/cm/release_finalize.sh:469` computes

```bash
_cm_promote_script="$(dirname "${BASH_SOURCE[0]}")/promote_bundled_items.py"
```

`BASH_SOURCE[0]` names the library stage, so the lookup resolves to
`team/scripts/lib/cm/promote_bundled_items.py`. That directory holds five shell
files and no Python. The helper has been at `team/scripts/cm/promote_bundled_items.py`
since v1.0.0 and never moved. Every release therefore prints, at Step 13c:

```
ERROR: promote_bundled_items.py not found at .../team/scripts/cm/../lib/cm/promote_bundled_items.py
WARNING: bundled item ## Status fields NOT promoted. Run promote_bundled_items.py manually.
```

and exits 0 anyway, leaving the bundle file and every item it bundled at
`## Status: running` — contradicting both `team/SOP.md` and the bundle files'
own `## Notes`.

Found by running the new harness, which drives the real `cm/release.sh`. This is
precisely the class of defect PRIORITY-0001 predicted an end-to-end run would
surface. `grep -rl promote_bundled_items team/tests/` returns nothing: the path
has no test coverage at all.

Currently masked on this host because the installed tree still carries the
pre-decomposition monolithic `scripts/cm/release.sh` (199 KB, 2026-09-05) with
the helper beside it. The first install of a post-v2.9.20 tree activates it.

### Finding 2: an explicit `--team-root` is silently overridden by ambient env, and did write into the live install

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (a real defect that already caused ten spurious task folders in the operator's live install; shipping this RC does not worsen it, and this RC in fact adds a local guard against it)
**Regression:** no (the "environment variables always win" precedence is stated in `team/pm-agent/lib/config.py`'s module docstring and is long-standing; this RC's diff touches nothing under `team/pm-agent/`)
**Integrity Damage:** no (the contamination was additive and fully contained — verified: no queue file references any of the stray IDs, `projects.cfg` is unchanged, and the ten folders are quarantined outside the live tasks tree)
**Filed As:** BUG-0184 (Path C-A)

`get_config(kanban_root=X)` applies the CLI override to `KANBAN_ROOT` *after*
the env-wins merge, so `PGAI_TASKS_DIR` keeps whatever the environment says
while `KANBAN_ROOT` reports the override — an internally inconsistent config
object. `pm_materialize.py:4571` then materializes into that env-supplied path.
Every agent wake session exports `PGAI_TASKS_DIR` pointing at the live install,
so this is the normal case, not a corner case.

Reproduced directly during this verification against a sandbox root:

```
env -u PGAI_TASKS_DIR  → PGAI_TASKS_DIR = <sandbox>/tasks          (flag honoured)
PGAI_TASKS_DIR=<live>  → PGAI_TASKS_DIR = <live>                    (flag ignored)
                         KANBAN_ROOT    = <sandbox>                 (flag honoured)
```

This is what wrote ten task folders into the operator's live install during
`CODER-20260907-051`. That agent caught it, quarantined the folders to
`$PGAI_AGENT_KANBAN_TEMP_DIR/quarantine/live-install-contamination-CODER-20260907-051/`,
and added a `run_isolated()` (`env -i` plus an allowlist) to the harness. I
re-verified the containment independently: the live tasks tree holds none of the
stray IDs, and no queue file references them. The workaround protects the
harness only; any other caller reaching for `--team-root` still inherits the
defect, which is why it is filed rather than considered closed.

### Finding 3: the shipped `--help` output cites the authoring ticket

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (hygiene; nobody is steered into a wrong action, the text is merely incomplete)
**Regression:** yes (this RC introduced the header text)
**Integrity Damage:** no
**Filed As:** BUG-0182 (Path C-A)

`bash team/scripts/harness/helloworld-flow-check.sh --help` prints 192 lines,
five of which defer their explanation to documents that exist only in the kanban
task tree: "in this ticket's constraints" (×2), "per this ticket's own guidance",
"per this ticket's constraint", and "see this task's status.md Summary for the
recorded scope decision". The script builds `_USAGE_TEXT` by self-reading its own
header, so header prose is runtime output.

`docs/coding-standards.md` directive 8 requires help text to describe behavior
and never process history; directive 6 makes `--help` the operator contract. The
sentences involved are load-bearing — they carry the determinism rationale and
the normalizer-reuse rationale — and are incomplete without a document not in the
release.

`lint_comment_provenance.py` does not catch these: its patterns match explicit
identifiers (`BUG-\d+`, `PRIORITY-\d+`, the task-ID shape, version bundles) and
these phrases name none. The same authoring session self-corrected three
`PRIORITY-0001` citations the lint *did* flag; the un-identified prose survived
because nothing mechanical looks for it. The bug proposes closing that blind spot
as well as rewording the five lines.

### Finding 4: a stray empty commit shipped in the RC's history

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (the commit changes no file; the cost is to provenance, not to behaviour)
**Regression:** yes (created during this RC's WRITER leg)
**Integrity Damage:** no (git history is intact; nothing was lost or overwritten)
**Filed As:** BUG-0183 (Path C-A)

`44a7fad05c9edcb6dd9ec433107c7416c5e0d802`, "initial empty commit", sits on
`ai_feature/WRITER-20260907-054-testing-md-behavior-check` between that branch's
real commit and its merge base. Its tree is byte-identical to its parent's. Its
message is verbatim the scratch-repo boilerplate from
`team/demos/helloworld-demo/README.md:48`. It was committed at 19:51:27, 72
seconds before the branch's declared commit `2294490` at 19:52:39 — inside the
WRITER session, whose `status.md` names only `2294490`.

Two candidate sources were ruled out rather than assumed:

- **The harness library.** `harness_init_scratch_repo` runs
  `git config --local user.email harness@pgai.local` before committing. For a
  linked worktree, `--local` writes to `$HOME/develop/pgai-agent-kanban/.git/config`,
  which `git config --show-origin --get user.email` shows still holds the
  operator identity. That function never ran against this repository.
- **The README block as written.** It opens with
  `mkdir -p ~/pgai-helloworld-src && cd ~/pgai-helloworld-src`, and
  `~/pgai-helloworld-src` does not exist on this host, so the block was not run
  as a unit.

What remains is the `git commit --allow-empty` line executed on its own with the
dev-tree worktree as cwd — an ad-hoc state-mutating command against the live
development tree, which is the discipline `roles/TESTER.md` § Live-Install Safety
and `team/TESTING.md` fidelity mode 6 exist to prevent. The RC's diff against
`ai_main` is unaffected and remains exactly the nine intended file changes.

### Finding 5: pre-flight, structure, guards and constraints

**Category:** pass

- Worktree HEAD `652bb7f` matches `ai_rc/v2.10.2`; `git status --short` empty;
  `git stash list` empty.
- Pre-flight merge verification (TESTER.md Step 2a) passed. Every DONE task's
  feature branch was deleted post-merge, which the procedure treats as merged.
- All six new/changed deliverable files exist; both new `.sh` files are mode
  0755.
- Exit-code contract exercised directly: `--help` → 0, missing `--capture`
  argument → 2, unknown flag → 2, clean compare → 0, seeded compare → 1.
- Second and third consecutive invocations succeeded with no manual cleanup of
  the prior throwaway root.
- Skip-cites-real-bug gate (Step 6.13): no placeholder `BUG-SKIP` IDs and no
  unresolvable cited bug IDs in `team/tests/`. The only grep hits are historical
  coverage logs under `team/tests/artifacts/` quoting the gate's own text.
- Step 6.1 drift: `pp_last_released_version` → `v2.10.1`; `semver_gt v2.10.2
  v2.10.1` true; `release-state.md` `## Last Released` is a well-formed
  `v2.10.1`; `## Active RC` is `v2.10.2` and `ai_rc/v2.10.2` exists. No drift.
- Step 6.10 dependency graph: linear and acyclic — CM-056 (open-rc) → 051 → 052
  → 053 → 054 → 055 → TESTER-057 → CM-059 (release). Every referenced task ID
  exists; bookends correct; no sequence collisions.
- Step 6.8 synthetic release simulation: **skipped with note.** The RC's diff
  touches no release-lifecycle file — only `team/scripts/harness/**`,
  `team/tests/**`, `team/TESTING.md` and the demo README. (The RC's harness
  *invokes* `cm/open-rc.sh` and `cm/release.sh` but modifies neither.) For the
  record, `tests/integration/test_cm_release_synthetic.py` ran green as part of
  the integration suite.

### Finding 6: test fidelity of the RC's new test module

**Category:** pass-with-caveat

`test_helloworld_flow_harness.py` is unusually good on the fidelity checklist:
it mocks no seam, invokes the harness as a real subprocess, drives real
production scripts, and carries an autouse fixture that snapshots the live
`projects.cfg` as raw bytes at module-import time — before the suite's own
autouse redirection fixture fires — so the assertion is against the operator's
real file rather than a redirected one. The seed-detect-revert test is genuine
evidence the check can fail.

The caveat is stated by the RC itself rather than hidden, which is why it is a
caveat and not a gap: the harness invokes no model and performs the CODER and
TESTER legs directly, so a green run proves the engine still turns the crank the
same way and does **not** prove a role file still tells its agent what it needs.
`team/TESTING.md` says exactly this, in bold. That is the honest scoping
PRIORITY-0001 asked for, not an overclaim.

## Lint Findings (non-blocking)

Two quality-class lint classes tripped across the two findings files
(`findings-unit.json`: 4 instances; `findings-integration.json`: 1 instance).
Both classes are already tracked by open bugs, so no duplicate was filed.

- `comment_provenance` — `release-notes/v2.9.22.md:31` carries the task-ID
  reference `TESTER-20260907-017` in shipped prose. Pre-existing; in a file this
  RC does not touch. Appears in both the unit and integration findings files
  (one class, one disposition). — cites **BUG-0173** (open)
- `gate_census_compliance` — three refusal sites in the shipped test runners lack
  an inline census-classification comment and, at two of them, the dual-channel
  emitter call: `team/scripts/run-integration-tests.sh:247`,
  `team/scripts/run-integration-tests.sh:272`, `team/scripts/run-unit-tests.sh:570`.
  Pre-existing; none of those files are in this RC's diff. — cites **BUG-0180**
  (open)

Neither class raises `## Systemic Risk` and neither, on its own, moves the
recommendation.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=0d freshness=stale — the docker and podman claude-abliterated pairs failed on a Dockerfile FROM parse error and podman/rocky9 failed on a COPY --chmod step; cites BUG-0167, filed by that deep-verify run. The record is same-day but stale on version — it speaks for v2.9.20, not the v2.10.2 under verification — so nothing is known about whether this tree's images build.

Reported, not re-derived, and not re-filed: TESTER runs no container build during RC verification. Note for context, not as a finding: BUG-0167 is now marked `done`, so the failure the record names may already be repaired in tree; only a fresh DEEP-VERIFY run can say. This project's `project.cfg` sets `[release] deep_verify = off`, so no scheduled run will refresh it — a pre-release on-demand run (`team/scripts/deep-verify.sh --project pgai-agent-kanban`) is the only path to a current answer before the public tag.

## Autonomous criterion

Manual interventions required: **none.**

1. **Any task force-promoted from WAITING to BACKLOG manually?** NO. Every task
   in the chain carries the wake script's own promotion note ("Promoted from
   WAITING to BACKLOG by wake script: prerequisites satisfied"), and the
   dependency graph resolves in exactly the declared order.
2. **`release-state.md` edited outside the CM scripts?** NO. Its mtime is
   `2026-09-07 18:36`, matching its own `## RC Opened At: 2026-09-07T18:36:16+00:00`
   written by `cm-open-rc.sh`. No later write.
3. **Scripts patched mid-build?** NO. The RC's only `team/scripts/` changes are
   the deliverable itself, committed on declared feature branches and merged
   `--no-ff` through the normal flow.
4. **Queue files manually edited?** NO. All queue mtimes fall inside agent
   activity windows, and no queue references any of the quarantined stray task
   IDs.

Worth recording as a positive: the one thing that went wrong in this build — the
live-install contamination during CODER-051 — was detected, contained,
quarantined and fixed by the agent within its own session, with no human
involvement and no chain stoppage. That is the autonomy criterion working, not
failing. The underlying cause is nonetheless a real defect and is filed as
BUG-0184.

## Caveats

- **The RC's deliverable found two pre-existing defects on its first real
  outing.** Findings 1 and 2 were both surfaced by running the new harness.
  Neither would have been visible to the snapshot-comparison approach
  PRIORITY-0001 was filed against. Whatever CM decides about the findings, the
  deliverable demonstrably does the job it was commissioned for.
- **The stray-commit and contamination findings share a root theme.** Both are
  cases of a state-mutating operation reaching the live tree when it was meant
  for a throwaway one. The RC's own work is the strongest existing mitigation
  (`run_isolated()`, the live-`projects.cfg` autouse fixture); the two filings
  extend that discipline outward.
- **`/tmp/pgai_kanban_tmp/pgai-agent-kanban` bug directory hygiene.** Five stale
  claim lock files (`.claim-0136`, `-0137`, `-0160`, `-0172`, `-0173`) sit in
  `projects/pgai-agent-kanban/bugs/` from earlier sessions that did not release
  them. They do not block ID allocation — this run's four claims (0181-0184)
  were acquired and released cleanly — but they are litter. Recorded as an
  observation, not filed.
- **The quarantine directory consumed task-ID sequence numbers 060-074** in the
  live project's numbering space without those folders existing in `tasks/`. A
  future materialization could reissue those IDs. Harmless while the quarantine
  stays outside `tasks/`; worth knowing before anyone restores it. Observation,
  not a finding.
- **A stale `unit_exit.txt` from a prior session was present** in the shared
  temp directory when this run started, reading `UNIT_EXIT=0` at a timestamp two
  hours old. It was identified by mtime and ignored. Noted because trusting it
  would have produced a report that never ran the suite.
- **`team/tests/fixtures/helloworld-flow/` golden re-verified against a live
  capture**, not merely assumed: `diff -r <fresh-capture> <golden>` was silent.
- **`TEMP_ROOT="${PGAI_AGENT_KANBAN_TEMP_DIR:-/tmp/pgai_kanban_tmp}"** at
  `helloworld-flow-check.sh:225` is a literal temp path, but it is the documented
  default and the identical form already used by `engine-behavior-baseline.sh`,
  `release_notes.sh` and `check-changelog-drift.sh`. Consistent with tree
  convention; not a finding.
