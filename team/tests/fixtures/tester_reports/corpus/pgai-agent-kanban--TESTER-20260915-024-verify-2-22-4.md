# Verification Report: ai_rc/v2.22.4

**Task:** TESTER-20260915-024-verify-2-22-4
**RC branch:** `ai_rc/v2.22.4` @ `7535df9` (8 commits ahead of `ai_main`)
**Worktree:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260915-024-verify-2-22-4` — detached HEAD, commit identity matches `ai_rc/v2.22.4`, `git status --short` empty, `git stash list` empty
**Requirements doc:** `projects/pgai-agent-kanban/requirements/v2.22.4-bugfix-bundle-20260915.md`
**Bundled items:** BUG-0281 (cleanup sandbox ignores pre-resolved exports, severity high), BUG-0282 (governance byte budgets over, severity low — hygiene ride-along)

## Summary

v2.22.4 is a two-item bug bundle and both items are done. BUG-0281's residual
sandbox hole is closed in `team/scripts/cleanup/cleanup.sh` by two new
dual-channel refusals over the pre-resolved `PGAI_PROJECT_ROOT` and
`PGAI_TASKS_DIR` exports, each carrying a census anchor and a matching
Locator Index entry; I verified the fix behaviourally with six invocations in
a throwaway root, including the original destructive failure mode (a decoy
tasks directory holding a backdated terminal task folder survives
byte-identical) and the three normal cases that must keep working. BUG-0282's
byte-budget overage is cleared by single-canoning the live-install-safety rule
into `docs/test-authoring-guidelines.md`: all seven roles now assemble under
budget and the unit runner's findings file carries zero
`governance_byte_budget` entries. Both gated runners exit 0 (unit 4530 passed
/ 31 skipped; integration 465 passed / 1 skipped) with zero quality-class lint
findings in either findings file. Two defects were found outside the bundle's
own acceptance criteria and filed via Path C: a false refusal and an
inaccurate refusal message when an export names the reconciled directory with
a trailing slash (BUG-0283), and a standing governance gap — nothing warns
that copying a linked git worktree carries its `.git` pointer, a shape that
silently moved a live feature branch during this very RC before the agent
caught and reverted it (BUG-0284). Neither costs the release its usability;
both belong to the next loop.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Findings

### Finding 1: BUG-0281 — pre-resolved export reconciliation lands and holds under the original failure mode

**Category:** pass

`team/scripts/cleanup/cleanup.sh` gains two refusals immediately after the
`_pp_project_root` / `_pp_tasks` / `_pp_req` resolution:
`cleanup.project_root_export_mismatch` and `cleanup.tasks_dir_export_mismatch`.
Each carries a `# CENSUS-ANCHOR:` marker, an inline `BLOCK-WORTHY`
classification comment, and a matching Locator Index line in
`docs/blocking-site-census.md` (`cleanup-project-root-export-mismatch-01`,
`cleanup-tasks-dir-export-mismatch-01`) — the full new-gate rule from SOP.md.
`python3 team/scripts/lint_gate_census_compliance.py` exits 0 with 0 offenders
over 36 scanned files and 161 exempted anchored sites.

Behavioural verification (Step 6.5), six invocations, all inside a throwaway
root under `$PGAI_AGENT_KANBAN_TEMP_DIR`, with a decoy root standing in for
the operator's live install and a backdated (10-day) terminal task folder
seeded in the decoy so Step 2's prune would actually take it:

| Case | Invocation | Result |
|---|---|---|
| A | `PGAI_TASKS_DIR` → decoy, both roots sandboxed, **non-dry-run** | exit 1, `cleanup.tasks_dir_export_mismatch`, decoy task folder present afterwards |
| B | `PGAI_PROJECT_ROOT` → decoy, both roots sandboxed | exit 1, `cleanup.project_root_export_mismatch` |
| C | both exports cleared (the documented sandbox form) | exit 0, Steps 0-6 every header naming the sandbox root |
| D | both exports set to the reconciled values | exit 0, Steps 0-6 naming the sandbox root — the guard does not fire on agreement |
| E | `PGAI_TASKS_DIR` = reconciled path **with trailing slash** | exit 1 — see Finding 6 |
| F | `--help` with a mismatched export | exit 0, usage printed, no filesystem action |

Case A is BUG-0281's own "Actual" reproduction re-run on this RC with
`--dry-run` removed; on `ai_rc/v2.22.3` it exited 0 and named the decoy in the
Step 2 header. Both channels of each refusal are complete: stable `error` key,
plain-language `message` naming both values, and a runnable `remedy`.

Cases C and D are the anti-pattern-6 half — the original failure mode *and*
the new normal case. Production `cleanup.sh` is cron-invoked
(`docs/OPERATIONS.md` cron stanzas) from an environment that exports neither
variable, so the guard is inert on the path it actually runs on.

Regression coverage (CODER-20260915-021) adds scenarios 4 and 5 to
`team/tests/unit/test_cleanup_root_override.py`: each seeds a decoy, points
one export at it, asserts a non-zero exit, asserts all three dual-channel
lines and both paths in stderr, asserts no `Step` header and no
`log written to:` line fired, and asserts the decoy directory snapshot is
byte-identical afterwards. The tasks-dir decoy is backdated past the 7-day
retention window so the assertion is coupled to genuinely deletable state —
the fixture proves something rather than nothing. Three pre-existing fixtures
that leaked the ambient exports into their subprocess calls were explicitly
cleared rather than left to chance.

### Finding 2: BUG-0282 — all seven roles back under their governance byte budgets

**Category:** pass

`bash team/scripts/lint_governance_byte_budgets.sh --kanban-cfg kanban.cfg_example`
on the RC, exit 0:

```
ROLE             ASSEMBLED         BUDGET   VERDICT
po                   63167          65536   under
pm                   76689          81920   under
coder                69604          69632   under
writer               68797          69632   under
tester              102300         102400   under
cm                   85578          86016   under
overwatch            73426          73728   under
```

Both previously-`OVER` roles (`coder` 70897, `tester` 104287) are now under.
The bug's second acceptance bullet also holds: `findings-unit.json` written by
this run's `run-unit-tests.sh` contains zero findings, so no
`governance_byte_budget` entry survives.

The bug's third bullet — that the lean-up preserve every substantive
instruction — holds on reading. `docs/test-authoring-guidelines.md` now owns a
`## Live-install safety` section carrying the full fenced invocation, the
"why overriding one root is not a sandbox" argument, the new "why two of the
five are cleared rather than repointed" paragraph, the `cleanup.root_mismatch`
refusal and its remedy, and the gated-runner-versus-ad-hoc scope statement.
`team/roles/CODER.md` and `team/roles/TESTER.md` each retain a runnable
command naming all five variables (`PGAI_TASKS_DIR`, `PGAI_PROJECT_ROOT`
cleared via `env -u`; `KANBAN_ROOT`, `PGAI_AGENT_KANBAN_ROOT_PATH`,
`PGAI_AGENT_KANBAN_TEMP_DIR` set), the one-sentence reason a single root
override is insufficient, and a citation to the canonical section. The chosen
home is outside every read-order manifest, so it costs no role budget —
the correct shape for this fix.

`docs/README.md`'s index line for the guidelines document was updated to name
the new section, so the docs index does not go stale.

### Finding 3: Requirements-doc acceptance criteria

**Category:** pass

- *"Each bundled item is addressed in the implementation"* — Findings 1 and 2.
- *"All Python files compile cleanly"* — the gated runners' breakage-class
  pre-flight reports `444 python file(s) ... 0 breakage-class failures`.
  `python3 -m py_compile` on both changed test files exits 0.
- *"All bash files pass syntax check"* — same pre-flight, `265 shell file(s)`,
  0 failures. `bash -n team/scripts/cleanup/cleanup.sh` exits 0.

### Finding 4: Gated test suites

**Category:** pass

Both run from the worktree root with `--coverage`. These exit codes are the
suite verdict; no direct-pytest result is quoted anywhere in this report.

- `bash team/scripts/run-unit-tests.sh --coverage` → **exit 0**, `4530 passed,
  31 skipped in 678.86s`.
- `bash team/scripts/run-integration-tests.sh --coverage` → **exit 0**,
  `465 passed, 1 skipped in 483.56s`.

Skip-cites-a-real-bug (Step 6.13): the placeholder grep returns no real hits —
every `BUG-SKIP`-shaped match is either a gated-runner log line under
`team/tests/artifacts/` or a string-literal fixture inside
`team/tests/unit/test_skip_bug_citation_scan.py`, the test *of* the gate.
The four IDs my naive extraction flagged as missing (`BUG-1234`, `BUG-1235`,
`BUG-1237`, `BUG-1239`) are all fixture payloads in that file, not skip
annotations. `lint_skip_bug_gate.sh` ran green inside both gated runners.

### Finding 5: Pre-flight, state and process checks

**Category:** pass

- **Merge verification.** All three DONE feature branches are merged — the RC
  log carries `a836268` (CODER-020), `55b3b8c` (CODER-021) and `7535df9`
  (WRITER-022) as `--no-ff` merges. The branches themselves are deleted, the
  normal post-merge shape. The full pre-flight sweep over every DONE task in
  `projects/pgai-agent-kanban/tasks/` reports zero anomalies (no UNMERGED, no
  BUG-92 zero-commit branches).
- **release-state.md.** `Active RC: v2.22.4`, `RC Opened By Task:
  CM-20260915-023-open-rc-2-22-4`, `Last Released: v2.22.3` — well-formed
  `vX.Y.Z`. `pp_last_released_version` returns `v2.22.3`; `v2.22.4` is
  strictly semver-greater. No drift.
- **Prerequisite graph.** Acyclic, every referenced ID exists, bookends
  correct (CM-open first with no prerequisites; TESTER then CM-release last).
  Sequence numbers do not collide. The CM-open bookend carries a higher
  ordinal (023) than the work tickets it precedes (020-022) — the
  materializer's standard bookend-injection numbering, not drift.
- **Coding standards (Step 6.14 spot-check).** The RC's added lines carry no
  bug ID, task ID, RC or version citation (directive 8) — grep over
  `git diff ai_main..ai_rc/v2.22.4` added lines returns nothing.
  `cleanup.sh --help` names every argument including the two new-ish
  `--trivial-only` / `--temp-only` modes (directive 6). No new hardcoded
  operational value; the two new refusals reuse the shared
  `pgai_emit_error_contract` emitter rather than open-coding a message
  (directives 1 and 3).
- **Synthetic release simulation (Step 6.8).** Skipped with note: the diff
  touches no release-lifecycle file. `cleanup.sh` lives under
  `team/scripts/` but takes no part in the RC open/close cycle.

### Finding 6: The export refusals compare exactly but say they compare containment

**Category:** bug
**Fix Effort:** small (1 CODER task)
**Systemic Risk:** low (fail-closed; the refusal never lets a destructive step run on an unsandboxed path, and its remedy line quotes the working value)
**Regression:** yes (both refusals are new in this RC; the invocation it now rejects previously proceeded)
**Integrity Damage:** no
**Filed As:** BUG-0283 (Path C-A — filed as BUG: code defect / regression / test failure)

Both new guards test exact string equality —
`[[ "$PGAI_TASKS_DIR" != "$_pp_tasks" ]]` — while the message they print
claims a containment test: *"does not sit underneath the reconciled root."*
The two do not agree in either direction. Case E above:

```
$ env -u PGAI_PROJECT_ROOT PGAI_TASKS_DIR="$R/projects/demo/tasks/" \
      KANBAN_ROOT="$R" PGAI_AGENT_KANBAN_ROOT_PATH="$R" ... --dry-run
error:   cleanup.tasks_dir_export_mismatch
message: PGAI_TASKS_DIR ('<R>/projects/demo/tasks/') does not sit underneath the reconciled root ('<R>/projects/demo/tasks'); cleanup.sh will not guess which one the caller meant.
EXIT=1
```

The quoted path *is* the reconciled directory; only the trailing slash
differs. Conversely a path genuinely underneath the reconciled root satisfies
the message's wording and is still refused, so the message reads as correct
there by accident. The smallest correct fix is to normalise the trailing
slash and reword to "does not match"; widening to a real containment test is
a deliberate behaviour change and should be chosen, not defaulted into.

Scope note: no documented invocation is affected. Every sandbox form the role
files and `docs/test-authoring-guidelines.md` show uses
`env -u PGAI_TASKS_DIR -u PGAI_PROJECT_ROOT`, which clears the variables.

### Finding 7: Nothing warns that copying a linked git worktree carries its `.git` pointer

**Category:** bug
**Fix Effort:** small (1 WRITER task; note the 28-byte and 100-byte role budget margins from Finding 8)
**Systemic Risk:** medium (a real defect that ships and is healed next iteration; it can silently rewrite the branch an in-flight RC is built on, but git's reflog makes recovery routine)
**Regression:** no (the gap pre-dates this RC — `grep -rn worktree` over `team/roles/` and `docs/test-authoring-guidelines.md` at `ai_main` returns only working-directory validation and the TESTER detached-HEAD check, exactly as on the RC)
**Integrity Damage:** no (the one observed instance was detected and reverted in the same session; `ai_rc/v2.22.4` carries no revert commit and no content was lost)
**Filed As:** BUG-0284 (Path C-A — filed as BUG)

Step 6.11 spot-check, "state drift" category. A per-task worktree's `.git` is
a one-line `gitdir:` pointer at the shared repository, so a recursive copy of
the working directory does not isolate git state — commands in the copy mutate
the original repository's refs. Reproduced directly in a throwaway repo: an
`--allow-empty` commit made entirely inside a `cp -r` copy of a linked
worktree moved `feature` in the source repository.

It is not hypothetical here.
`tasks/CODER-20260915-021-cleanup-export-regression/status.md` records that
the agent `cp -r`'d its worktree into a scratch directory and ran `git revert`
there, which "briefly moved the real feature branch's HEAD to the revert
commit before I caught it via `git status`." It recovered with
`git reset --soft`/`--mixed`. I confirmed independently that the RC is intact:
the eight-commit history carries no revert commit, the three merges are
present, and the shipped `cleanup.sh` behaves as specified across all six
behavioural cases above.

The governance already spends several hundred bytes on the analogous
inherited-pointer hazard one layer up (five environment variables that resolve
to the live install) and says nothing about this one. That asymmetry — not the
agent's mistake — is the defect.

### Finding 8: Byte-budget margins are 28 and 100 bytes

**Category:** pass-with-caveat

`coder` clears its budget by 28 bytes (69604/69632) and `tester` by 100
(102300/102400). The WRITER task flagged the same thing. This is not a defect
and not a gap — BUG-0282's acceptance is met as written — but the next
substantive sentence added to either rulebook re-trips the lint, and Finding
7's fix is a clause in exactly those two files. Whoever takes BUG-0284 should
expect to either write a very short clause or move the budget deliberately, as
BUG-0282's own Hypothesis section already framed it.

### Finding 9: Autonomous operation criterion

**Category:** pass

See the dedicated section below.

## Lint Findings (non-blocking)

None. Both gated runners wrote a findings file with zero entries:

- `${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-unit.json` — `0 finding(s)`,
  generated 2026-09-15T09:38:26+00:00, runner `run-unit-tests.sh`.
- `${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-integration.json` —
  `0 finding(s)`, generated 2026-09-15T09:47:15+00:00, runner
  `run-integration-tests.sh`.

The two `governance_byte_budget` findings BUG-0282 was filed over are gone.

## Code Coverage

Informational only; does not enter the PASS precondition or the risk rating.
Read from the JSON `totals` objects (`percent_statements_covered` and
`percent_branches_covered`), not from the blended `TOTAL` line.

- Unit:        line 75%  |  branch 62%   (15806 stmts, 4744 branches; blended `TOTAL` 72%)
- Integration: line 7%   |  branch 6%    (15797 stmts, 4744 branches)

The integration figure is low by construction — that suite drives entry
points end to end rather than sweeping modules.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=8d freshness=stale — the last cold-build run was eight days ago against v2.9.20 and both engines failed on docker/claude-abliterated (a `FROM` parse error) plus podman/rocky9 (a `COPY --chmod` step), so it is stale on both counts (8d exceeds the 7-day window, and v2.9.20 is not the v2.22.4 under verification) and the images have never been built cold against this tree; it cites BUG-0167, filed by that deep-verify run and since closed (`## Status: done`).

Reported, not re-filed and not re-derived: the record's staleness and its red
verdict are context the rating above was made with, and neither raises the
recommendation nor the systemic risk. `team/scripts/deep-verify.sh` is present
and executable, so the "images with no cold-build gate anywhere" gap does not
apply. No container build or container engine was invoked during this
verification.

## Autonomous criterion

Manual interventions required: **none**. All four checks NO.

1. **Tasks force-promoted WAITING → BACKLOG manually?** NO. Every task in the
   cycle carries the wake script's own promotion note; this task's own
   `status.md` reads "Promoted from WAITING to BACKLOG by wake script:
   prerequisites satisfied." Task-folder mtimes run monotonically through the
   chain (PM 06:55 → CODER-020 07:50 → CODER-021 08:19 → WRITER-022 09:13 →
   TESTER-024 09:14).
2. **`release-state.md` edited outside the CM scripts?** NO. The file holds
   exactly the four fields `cm-open-rc.sh` writes, with `RC Opened By Task`
   naming CM-20260915-023 and a timestamp matching that task's summary. No
   `## HALT Event` block.
3. **Scripts patched mid-build?** NO. The RC's eight commits are the three
   task merges and their content commits and nothing else. `cleanup.sh` is
   under `team/scripts/` but it is the bundle's deliverable, not a tooling
   patch applied to keep the build moving.
4. **Queue files manually edited?** NO. `tasks/queues/*_backlog.md` mtimes
   line up with the wake firings that pulled each task.

One process note that is **not** an intervention and **not** one of the four
checks, recorded because it is process-shaped: CODER-20260915-021 mutated a
real branch ref through a copied worktree and recovered it without human help.
That is Finding 7, filed as BUG-0284. It cost no manual intervention and the
chain never stopped, so the criterion still reads clean.

## Caveats

- **`## Last Released` is not compared against `pp_last_released_version`.**
  Per Step 6.1 the two are checked independently; `git tag --list 'v2.22.*'`
  inside the linked worktree returns nothing because the tags live on the
  shared repository's own refs, which is why the helper — not a tag grep — is
  the canonical reader. The helper returned `v2.22.3`.
- **The RC branch is `ai_rc/v2.22.4`, not `rc/v2.22.4`.** The requirements
  doc's `## Source Branch` field says `rc/v2.22.4`; the project's
  `branch_prefix` is `ai_`, so the real branch — and the one the RC was opened
  on and merged into — is `ai_rc/v2.22.4`. Baseline comparisons were made
  against `ai_main`. Not a finding; noting it so the branch names in this
  report are not read as drift.
- **Scenario-4/5 coupling was verified by the authoring CODER, not re-verified
  here.** CODER-021 reports that reverting the export-reconciliation commit
  makes both new cases fail. I did not re-run that bisect, because doing so
  safely would require a clone rather than a copy (Finding 7) and the guard's
  behaviour is already established directly by cases A-D. The tests assert on
  the refusal's stable error key and both quoted paths, which cannot pass
  without the guard present.
- **No prior-run artifacts existed.** `artifacts/` was empty at start; this is
  a fresh first run, not a re-run, and no `.previous-RUN-N` file was read.
- Scratch fixtures for every behavioural case were created under
  `$PGAI_AGENT_KANBAN_TEMP_DIR` via `mktemp -p` and every invocation set or
  cleared all five live-install variables. No command in this verification ran
  against the operator's kanban root.
