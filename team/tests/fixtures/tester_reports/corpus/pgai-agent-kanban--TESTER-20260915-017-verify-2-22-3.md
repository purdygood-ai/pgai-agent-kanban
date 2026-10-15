# Verification Report: rc/v2.22.3

**Task:** TESTER-20260915-017-verify-2-22-3
**RC branch:** `ai_rc/v2.22.3` @ `5bfe4fe`
**Requirements doc:** `projects/pgai-agent-kanban/requirements/v2.22.3-bugfix-bundle-20260915.md`
**Worktree:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260915-017-verify-2-22-3` (detached HEAD at the RC commit, clean, no stashes)
**Verified:** 2026-09-15

## Summary

This verification covers release candidate v2.22.3 — a six-item bug bundle whose
front-of-bundle item (BUG-0279) is a live-install-safety defect in
`team/scripts/cleanup/cleanup.sh`, with five hygiene ride-alongs. All six bundled
items were verified behaviorally, not structurally: each destructive or preventive
change was exercised against the failure mode it exists to stop, inside a throwaway
temp root, with the pre-RC `ai_main` code run under the identical environment as a
negative control. Both gated runners exited 0 — unit 4528 passed / 31 skipped in
12m02s, integration 465 passed / 1 skipped in 8m02s. All six bundled items pass.
One gap was found outside the bundled items but squarely inside the class the bundle
set out to close: the sandbox seam `cleanup.sh` now reconciles covers two root
variables, and a third pre-resolved export (`PGAI_TASKS_DIR`) still silently outranks
both, so the destructive task-folder prune can still be aimed at the operator's live
install by an invocation that looks sandboxed. That defect predates this RC and is
narrowed — not widened — by it. Two quality-class lint findings (the CODER and TESTER
governance byte budgets, both crossed by this RC's documentation growth) are recorded
non-blocking. Both findings are filed via Path C-A.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Findings

### Pre-flight — worktree identity, cleanliness, merge verification

**Category:** pass

`git rev-parse HEAD` = `5bfe4fe4d09ed9963588af9c046a56d8fe912232` = `git rev-parse ai_rc/v2.22.3`.
`git status --short` empty. `git stash list` empty. Pre-flight merge scan over every
DONE task in `projects/pgai-agent-kanban/tasks/`: zero UNMERGED anomalies, zero
BUG-92 zero-commit anomalies. All CODER/WRITER feature branches for this RC were
deleted post-merge (normal lifecycle); 12 commits on `ai_main..ai_rc/v2.22.3`.

### BUG-0279 — cleanup.sh root override (CODER-20260915-011)

**Category:** pass

Behaviorally verified in a fully sandboxed temp root, with an `ai_main` negative control.

- **Case A (both roots set, disagreeing):** exits 1 with the dual-channel contract —
  `error: cleanup.root_mismatch`, a message quoting both values, and a runnable remedy.
  The decoy "live" root was byte-identical afterwards (`find`-snapshot diff empty).
- **Case B (`KANBAN_ROOT` unset, only `PGAI_AGENT_KANBAN_ROOT_PATH` set):** exit 0, and
  every Step 0–6 header, the archive paths, and the summary-log write path named the
  sandbox root. Zero occurrences of the live root or the decoy in the output. Decoy
  unchanged.
- **Negative control — `ai_main`'s `cleanup.sh` under the identical Case-A environment:**
  exit 0, `Step 1: prune old log files from <decoy>/logs/agents`, `Step 2: prune terminal
  task folders from <decoy>/projects/demo/tasks`. The original failure mode reproduces
  on the pre-RC tree and is closed on the RC.
- Census compliance: `# CENSUS-ANCHOR: cleanup-root-mismatch-01` sits directly above the
  `exit 1`, carries an inline `BLOCK-WORTHY` class comment, and the matching Locator Index
  line `team/scripts/cleanup/cleanup.sh:cleanup-root-mismatch-01 BLOCK-WORTHY` was added to
  `docs/blocking-site-census.md` in the same change. `lint_gate_census_compliance` reports 0 offenders.
- Regression coverage: `team/tests/unit/test_cleanup_root_override.py` (4 tests) exercises
  disagree/dry-run, disagree/non-dry-run, derive-when-unset, and both-unset-fails-closed,
  through real strict-mode bash with decoy roots and a repointed `HOME`.

### BUG-0280 — cleanup dry-run trivial-purge count (CODER-20260915-014)

**Category:** pass

- RC, `--dry-run`: `trivial_logs_deleted:  3` — a plain integer, with the three
  `[DRY-RUN] trivial-purge would delete: <path>` narration lines in the Step 0 section
  where they belong.
- RC, non-dry-run: `trivial_logs_deleted:  3` and the three seeded logs are gone from the
  sandbox afterwards (the production path still works).
- Negative control on `ai_main`: `trivial_logs_deleted:  [2026-09-15T…] cleanup: [DRY-RUN]
  trivial-purge would delete: …` — the capture-contamination reproduces pre-RC.
- The fix removes the command substitution entirely, reporting through the
  `TRIVIAL_LOGS_DELETED` global (the same shape `TEMP_ITEMS_PURGED` already used).
- Regression coverage: `team/tests/unit/test_cleanup_dryrun_trivial_purge_count.py` (2 tests).

### BUG-0275 — seeded test-order shuffle reproducibility (CODER-20260915-014)

**Category:** pass

Measured in the RC worktree with `pytest-randomly` 4.1.0 importable, invoking the seam the
way an operator would — no `-p no:randomly`, no other flag:

- Two runs of `PGAI_TEST_ORDER=shuffle:1234 python3 -m pytest tests/unit/test_pseudocron.py
  --collect-only -q` produce byte-identical collection order (48 node IDs).
- `shuffle:9999` produces a different order over the same set of node IDs.
- `PGAI_TEST_ORDER=reverse` is stable across runs and equals `sorted(nodeids)` reversed
  exactly (verified in Python, not shell collation) — matching the new documented
  canonicalise-then-reorder semantics.
- The harness's own `-p no:randomly` passes are preserved in
  `team/scripts/verify-test-order-independence.sh`, and its header comment was updated to
  state what "reverse" now means.
- Regression coverage: `team/tests/unit/test_order_independence_seeded_shuffle_reproducible.py`
  (2 tests, both `skipif`-guarded on `pytest_randomly` being importable).

### BUG-0277 — key-parity findings name the wrong registry (CODER-20260915-013)

**Category:** pass

All four message sites verified end-to-end by synthesizing each finding kind:

- `UNDOCUMENTED CONFIG KEY` — injected a synthetic `[project] tester_probe_key` into a temp
  copy of `project_paths.sh` and ran with `--project-key-registry`. Cause line names that
  registry file, not `config_loader.sh`.
- `UNKNOWN CONFIG KEY` — a probe doc citing `[project] tester_nonexistent_key` produces
  "which neither team/scripts/lib/config_loader.sh's nor team/scripts/lib/project_paths.sh's
  Key Registry declares." Both registries named, as the fix requires.
- `CONFIG DEFAULT MISMATCH` — a probe doc claiming `workflow_type = totally-wrong-default`
  produces "Update the documented default to match team/scripts/lib/project_paths.sh's Key
  Registry" — the registry that actually declares the key.
- Dual-channel summary — `message` reads "disagree with team/scripts/lib/config_loader.sh's
  or team/scripts/lib/project_paths.sh's Key Registry".

The attribution is carried on the parsed entry (`RegistryKey.source_display`, stamped at
parse time), which is the shape the bug's Fix section asked for. On the shipped tree the
lint reports 0 key-parity findings, so there is no live misattribution left to observe.

### BUG-0274 / BUG-0278 — provenance references in shipped code and release notes (CODER-20260915-013, WRITER-20260915-015)

**Category:** pass

`python3 team/scripts/lint_hygiene.py --classes provenance` reports
`OK — 0 findings across 8 scan directories` on the RC (the last verification measured 25
on `ai_rc/v2.22.1`). Per-file check of all ten named files: zero `BUG-[0-9]{4}` and zero
task-ID references in the eight code/test files; the two release-notes files retain
`BUG-NNNN` citations, which is their legitimate content — the provenance class flagged
their *task-ID* references, and those are gone. No
`team/scripts/hygiene-lint-allowlist.json` entry and no suppression marker was added
(the bundle explicitly forbade that remedy) — that file is untouched by the RC diff.

### Live-install sandbox is still defeated by a stale `PGAI_TASKS_DIR`

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (a real defect that ships and is fixed on the next loop; the RC is strictly safer than its predecessor, not worse)
**Regression:** no (the precedence of `PGAI_TASKS_DIR` over root-derived paths predates this RC and is untouched by its diff; this RC narrows the hole rather than opening it)
**Integrity Damage:** yes (Step 2 deletes terminal task folders — unrecoverable kanban state — and this path can still run against the live install from an invocation the agent believes is sandboxed)
**Filed As:** BUG-0281-cleanup-sandbox-ignores-tasks-dir (Path C-A — filed as BUG — code defect / regression / test failure)

BUG-0279's preferred remedy was stated as "a single override sandboxes the whole run" and
its rationale as "a safety seam that depends on the reader remembering a second variable
name is the seam that already failed once." The reconciliation shipped in this RC covers
`KANBAN_ROOT` and `PGAI_AGENT_KANBAN_ROOT_PATH` only. `PGAI_TASKS_DIR` and
`PGAI_PROJECT_ROOT` are exported by the wake bootstrap as fully-resolved live-install paths
and still outrank both roots, with no reconciliation and no refusal.

Reproduced in the sandbox (never against the live root):

```
env -u PGAI_PROJECT_ROOT PGAI_TASKS_DIR="$DECOY/projects/demo/tasks" \
    KANBAN_ROOT="$R" PGAI_AGENT_KANBAN_ROOT_PATH="$R" PGAI_AGENT_KANBAN_TEMP_DIR="$SB/temp" \
    bash team/scripts/cleanup/cleanup.sh --project demo --dry-run
→ Step 2: prune terminal task folders from <DECOY>/projects/demo/tasks
   Step 3: archive shipped requirements from <R>/projects/demo/requirements
```

Control with `PGAI_TASKS_DIR` cleared: Step 2 names `<R>`. So an invocation that sets both
roots to a throwaway path — the exact shape the newly-written role text shows — still
prunes task folders from whatever `PGAI_TASKS_DIR` points at, silently, with no refusal.
The RC's own documentation states this correctly and warns about it; the code does not
enforce it. The new `test_cleanup_root_override.py` covers the two-root cases only and does
not exercise the `PGAI_TASKS_DIR` precedence case.

This is not a regression and does not make the release worse — the two-root hole that
fired during the v2.22.2 build is genuinely closed. It is the residual third of the same
class, recorded so the loop closes it.

### Requirements-doc acceptance criteria

**Category:** pass

- "Each bundled item is addressed in the implementation" — all six bundled items verified
  above, each behaviorally.
- "All Python files compile cleanly" — `python3 -m compileall -q team install.sh` exit 0;
  `py_compile` on each of the 11 changed `.py` files passes; the gated runner's
  breakage-class gate reports 0 failures across 444 Python files.
- "All bash files pass syntax check" — `bash -n` passes on all 4 changed `.sh` files; the
  breakage-class gate reports 0 failures across 265 shell files.

### Step 6.10 — prerequisite dependency graph

**Category:** pass

CM-20260915-016 (open RC) → CODER-011 / CODER-013 / WRITER-015, CODER-011 → WRITER-012 and
CODER-014, all five → TESTER-017 → CM-20260915-019 (release). Acyclic; every referenced
task ID resolves to an existing task folder; bookends correct. Sequence number 018 is unused
(the HUMAN-APPROVE slot, skipped because the requirements doc sets
`Human Approval Required: auto`) — a gap in the numbering, not a collision.

### Step 6.13 — skip-cites-real-bug

**Category:** pass

`lint_skip_bug_gate.sh` (gated-runner pre-flight) reports 0 missing files and no
placeholder IDs. The independent verification-time grep surfaces `BUG-1234/1235/1237/1239`,
all of which are string literals inside `team/tests/unit/test_skip_bug_citation_scan.py`'s
own fixtures, not live skip annotations.

### Step 6.8 — synthetic release simulation

**Category:** pass (not triggered)

The RC diff touches no release-lifecycle file — no `cm-release.sh`, `cm-open-rc.sh`,
`cm-cancel-rc.sh`, or `team/scripts/lib/project_paths.sh`. Skipped per the trigger
condition.

### Step 6.1 — release-state drift

**Category:** pass

`Active RC: v2.22.3` matches the branch under verification; `RC Opened By Task:
CM-20260915-016-open-rc-2-22-3` exists and is DONE; `Last Released: v2.22.2` is well-formed
`vX.Y.Z`; the RC version is strictly greater. The `VERSION` file still reads `v2.22.2` —
expected, `cm-release.sh` bumps it at ship time.

### Step 6.14 — coding-standards spot-check

**Category:** pass

No new scripts were added, so no `--help` obligation. Zero new provenance strings (hygiene
lint 0 findings). No new hardcoded operational values — the reconciliation reads existing
env names only. The root-fallback pattern duplicated from `lib/projects.sh:140` has no
shared `lib/` helper to reuse today, so it is not a directive-1/3 violation.

## Test Suites

Both verdicts come from the gated runners, which enforce the breakage-class gate and
every quality-class lint pre-flight before pytest. No direct-pytest result is cited as
PASS evidence anywhere in this report.

- `bash team/scripts/run-unit-tests.sh --coverage` → **exit 0**. 4528 passed, 31 skipped,
  722.33 s. Post-suite bare-/tmp and TCP-listener cleanliness checks both OK.
- `bash team/scripts/run-integration-tests.sh --coverage` → **exit 0**. 465 passed,
  1 skipped, 482.10 s. Post-suite cleanliness checks both OK.

No test failures of any category — no `bug`, no `stale-assertion`, no `pre-existing-bug`.

## Code Coverage

Read from the `totals` object of the pytest-cov JSON reports (field names confirmed
against the generated files, not assumed).

- Unit:        line 75%  |  branch 62%   (15806 stmts, 4744 branches; blended 72%)
- Integration: line 7%   |  branch 6%    (15797 stmts, 4744 branches; blended 7%)

Informational only. Coverage does not enter the PASS precondition, the recommendation,
or the systemic-risk rating.

## Lint Findings (non-blocking)

Both runners' findings files were ingested. One distinct `lint` class across the two
files, so one bug, per the one-bug-per-lint-class-per-run rule.

- `governance_byte_budget` — two instances, both `severity: quality`, present in both
  `findings-unit.json` and `findings-integration.json`: `coder over governance byte
  budget: assembled=70897 bytes budget=69632 bytes` and `tester over governance byte
  budget: assembled=104287 bytes budget=102400 bytes`. Neither instance carries a
  file/line (the lint is per-role, not per-site); the role name is the locator. No open
  or running bug in `projects/pgai-agent-kanban/bugs/` covered the class — the five
  historical ones (BUG-0174, BUG-0199, BUG-0220, BUG-0226, BUG-0246) are all `done` —
  so one was filed. Rolled-up severity `low` (the highest among the aggregated
  instances). Filed **BUG-0282-governance-byte-budgets-over** (Path C-A).

Both roles were under budget on `ai_main` (coder 69053, tester 101751) and crossed on
this RC, from the live-install-safety prose added to the two role files. The lint
narrates and always exits 0 by design, so this moved neither gated-runner exit code.
Per Step 5c, a bug filed solely to track a quality-class lint finding does not on its
own elevate `## Recommendation` or raise `## Systemic Risk`; the elevation here comes
from the separate `gap` finding.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=7d freshness=stale — the docker and podman claude-abliterated pairs failed on a Dockerfile FROM-arity error and podman/rocky9 on a COPY --chmod step; stale because v2.9.20 is not the v2.22.3 under verification (the record's age is exactly at the 7-day window edge), so no image in this tree has been built cold; cites BUG-0167-cold-build-claude-abliterated-failure, filed by that deep-verify run and since closed, with no lane run after it to confirm the fix.

The lane was not run during this verification — per Step 5d it is not part of the RC
path. This line is a fact reported from the ledger, not a verdict produced here; it
does not move `## Recommendation` or `## Systemic Risk`.

## Autonomous criterion

Manual interventions required: none.

1. Were any tasks force-promoted from WAITING to BACKLOG manually? **NO.** Every
   prerequisite task (CODER-011, WRITER-012, CODER-013, CODER-014, WRITER-015,
   CM-016) reads `State: DONE`, `Needs Human: no`. The wake logs for the RC window
   record `no BLOCKED tasks to re-check` on every firing.
2. Was `release-state.md` edited outside `cm-open-rc.sh` / `cm-release.sh` /
   `cm-cancel-rc.sh`? **NO.** `Active RC: v2.22.3`, `RC Opened At:
   2026-09-15T04:06:27+00:00`, `RC Opened By Task: CM-20260915-016-open-rc-2-22-3` —
   internally consistent, and the timestamp falls inside that task's CM wake window.
3. Were any scripts patched mid-build? **NO.** All 12 commits on
   `ai_main..ai_rc/v2.22.3` are ticket work merged through the normal feature-branch
   path. The edits to `team/scripts/` (`cleanup.sh`, `lint_doc_command_truth.py`,
   `lint_gate_census_compliance.py`, `dashboard/create.sh`, `run-unit-tests.sh`) are
   each the deliverable of a named bundled item, not out-of-band repair.
4. Were any queue files manually edited? **NO.** `tasks/queues/tester_backlog.md`
   shows the standard marker progression, with `[A]` on this task and `[x]` on every
   prior verification.

All five completion gates passed first time — no refusal, no retry, no artifact
rotation:

```
04:11:13 writer: WRITER-20260915-015 PASS — merged into ai_rc/v2.22.3, worktree clean
04:47:45 coder:  CODER-20260915-011  PASS
05:28:31 coder:  CODER-20260915-013  PASS
06:05:12 coder:  CODER-20260915-014  PASS
06:15:04 writer: WRITER-20260915-012 PASS
```

The build ran from RC open to verification without manual intervention.

## Caveats

- **`team/TESTING.md` was not updated alongside the three other live-install-safety
  passages.** Line 98 still teaches the single-variable sandbox
  (`PGAI_AGENT_KANBAN_ROOT_PATH` only) while `team/roles/CODER.md`,
  `team/roles/TESTER.md`, and `docs/test-authoring-guidelines.md` were all rewritten to
  name five. This is an observation, not a finding: the three scripts TESTING.md names
  (`create-project.sh`, `add-project.sh`, `remove-project.sh`) each force
  `KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"` at the top of the file and read neither
  pre-resolved export, so the single-variable instruction is *correct for exactly the
  scripts it names*. TESTING.md does not name `cleanup.sh`, and `cleanup.sh` now refuses
  when the two roots disagree — so following the stale passage produces a refusal, never
  a destructive run. Worth folding into BUG-0281's fix for consistency, not worth its own
  filing.
- **The `## Deep-Verify Freshness` record is red and 7 days old.** Recorded, per Step 5d,
  so the rating above is made with that knowledge. It is not re-filed and does not move
  the recommendation — the bug that lane filed (BUG-0167) is closed, but no lane run since
  has confirmed it, so nothing is currently known about whether this tree's images build.
- **`docs/blocking-site-census.md` gained a Locator Index line but no numbered
  inventory-table row for the new refusal.** Checked against existing practice rather
  than assumed: the Locator Index carries 223 entries against 60 numbered table rows, so
  the table is a curated subset and the rollup line (`Rollup: 60 sites, 0 unclassified.`)
  still reconciles. `lint_gate_census_compliance` reports 0 offenders. Not a finding.
- **Stray file `1` in the kanban root** (`$HOME/pgai_agent_kanban/1`, 5119 bytes,
  dated 2026-09-07) is a copy of `project.cfg` left by a `> 1` redirect typo in an earlier
  session. It predates this RC by eight days, lives in kanban process state rather than the
  dev tree, and is unrelated to anything this RC changed. Noted for operator housekeeping
  only.
- **Test fidelity.** The RC's new suites are high-fidelity: `test_cleanup_root_override.py`
  drives real strict-mode bash through `shell_harness.run_bash`, seeds a decoy "live" root
  and asserts it is byte-identical afterwards, and repoints `HOME` for the both-unset case
  so a broken fail-closed guard still cannot reach the real install. The one fidelity gap
  is the one recorded as the `gap` finding: no test exercises the `PGAI_TASKS_DIR`
  precedence case, which is why that hole survived the ticket.
- **Live-install safety of this verification run.** Every `cleanup.sh` invocation in this
  report ran with all five variables pointed inside a `mktemp -d -p
  "$PGAI_AGENT_KANBAN_TEMP_DIR"` sandbox, against seeded fixture roots. No command in this
  run wrote to, deleted from, or archived anything under the operator's live kanban root.
