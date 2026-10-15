# Verification Report: ai_rc/v2.22.5

**Task:** TESTER-20260915-030-verify-2-22-5
**Requirements doc:** `requirements/v2.22.5-bugfix-bundle-20260915.md`
**RC branch / commit:** `ai_rc/v2.22.5` @ `86fe323b9268c5c002bbd8c5c684ed21645b64b2`
**Worktree verified:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260915-030-verify-2-22-5` (detached, HEAD matches `ai_rc/v2.22.5`, clean, no stashes)
**Date:** 2026-09-15

## Summary

v2.22.5 is a single-item bug bundle carrying BUG-0284 (a linked worktree's `.git`
is a pointer to the shared repository, so `cp -r` of a working directory isolates
no git state and history-rewriting verbs run in the copy move the real branch).
The RC ships five files across two feature merges: a new canonical passage in
`docs/test-authoring-guidelines.md` ("A copied worktree is not an isolated tree")
with three runnable safe alternatives, one-to-two-sentence citing clauses in
`team/roles/CODER.md`, `team/roles/TESTER.md` and `team/roles/WRITER.md`, and a new
two-case regression module at `team/tests/unit/test_worktree_copy_git_isolation.py`
that pins both the hazard and the documented clone alternative.

Verification covered: pre-flight merge checks over every DONE task with a live
feature branch, worktree identity and cleanliness, the full `ai_main..ai_rc/v2.22.5`
diff read end-to-end, both gated runners with `--coverage`, behavioral
re-execution of the hazard and of all three documented alternatives in a throwaway
sandbox under the framework temp root, the governance byte-budget lint named by the
bug's own acceptance list, both requirements-doc compile/syntax criteria across the
whole tree, the skip-cites-real-bug gate, the release-state drift check, the prereq
dependency graph, and the autonomous-operation criterion.

Both gated runners exited 0 with zero quality-class lint findings in either findings
file. Every acceptance criterion in the requirements doc and in BUG-0284 is met and
demonstrated by execution, not inspection. No `bug`, `gap`, or `stale-assertion`
finding was recorded, so nothing was filed via Path C (autonomous follow-up filing
— non-blocking).

## Recommendation

PASS

## Systemic Risk

low

## Findings

### Finding 1: Bundled item BUG-0284 is addressed in the implementation

**Category:** pass

Requirements-doc acceptance criterion 1 ("Each bundled item is addressed in the
implementation"). BUG-0284 carries four acceptance bullets of its own; each was
checked against the shipped tree.

- **Bullet 1 — the canon names the hazard, states `cp -r` does not isolate git
  state, and gives at least one safe alternative a stranger can run.** Met.
  `docs/test-authoring-guidelines.md` gains a sibling subsection of "Live-install
  safety" titled "A copied worktree is not an isolated tree". It shows the
  `gitdir: ...` pointer shape, states that every git command in a recursive copy
  resolves through the pointer and mutates the original repository's branches, HEAD
  and index, names the four history-rewriting verbs (`revert`, `reset`,
  `checkout -B`, `rebase`) as the destructive shape, and gives three alternatives
  as a runnable fenced block with a stated default and per-option caveats. The
  bug's `## Expected` asked for exactly these three (clone; `git worktree add`
  under the framework temp root then remove; sever the copied `.git` first) — all
  three are present.
- **Bullet 2 — `team/roles/CODER.md` and `team/roles/TESTER.md` each carry a
  clause pointing at that passage, and the byte-budget lint still reports `under`
  for all seven roles.** Met, and exceeded: `team/roles/WRITER.md` carries the
  clause too. Lint result recorded in Finding 3.
- **Bullet 3 — the reproduction run against the documented safe alternative leaves
  the source repository's branch unmoved.** Met, verified behaviorally — see
  Finding 2.
- **Bullet 4 — both gated runners still exit 0.** Met — see Finding 5.

### Finding 2: The hazard reproduces and all three documented alternatives are safe (behavioral)

**Category:** pass

Step 6.5 (trigger the failure mode; verify the guard fires) and Principle 4. The
documented rule is the whole deliverable, so the rule's factual claims were
re-executed rather than read. Everything below ran inside a throwaway repository
created with `mktemp -d -p "$PGAI_AGENT_KANBAN_TEMP_DIR"`; no live repository was
touched.

| Case | Command shape | Source branch `feature` | Verdict |
|---|---|---|---|
| Hazard | `cp -r wt copy` then `git -C copy commit --allow-empty` | `a81b7fe` -> `d26b56e` | moved — hazard reproduces exactly as documented |
| Option 1 (clone) | `git clone "$(git rev-parse --show-toplevel)" $SCRATCH/clone`, commit inside | unmoved at `a81b7fe` | safe |
| Option 2 (detached worktree) | `git worktree add --detach $SCRATCH/wt2 HEAD`, commit inside, `git worktree remove --force` | unmoved at `a81b7fe` | safe; the remove verb also succeeded |
| Option 3 (sever `.git`) | `cp -r . $SCRATCH/copy && rm -rf $SCRATCH/copy/.git` | unmoved at `a81b7fe` | safe; `git status` in the copy returns `fatal: not a git repository`, i.e. the tree is inert as documented |

The worktree's `.git` was confirmed to be a one-line pointer file
(`gitdir: <repo>/.git/worktrees/wt`), so the hazard case exercised the documented
shape rather than an accidental clone. Both halves of Anti-pattern 6 in the role
file ("synthesize the original failure mode AND the new normal case, then verify
both") are covered: the old failure mode still reproduces and each new prescribed
path is safe.

### Finding 3: Governance byte budgets remain under after the role-file additions

**Category:** pass-with-caveat

`bash team/scripts/lint_governance_byte_budgets.sh --kanban-cfg kanban.cfg_example`
exits 0 and reports `under` for all seven roles, which is BUG-0284's second
acceptance bullet verbatim:

```
po 63167/65536  pm 76689/81920  coder 69616/69632  writer 69202/69632
tester 102384/102400  cm 85578/86016  overwatch 73426/73728
```

The caveat is headroom, not correctness. `coder` and `tester` each finish with
**16 bytes** of margin. PM measured 28 (coder) and 100 (tester) bytes of headroom
before this RC and flagged the squeeze as a non-blocking concern in its
decomposition; WRITER reclaimed the space by leaning out live-install prose that
restated what the canon now carries in full, which is the right trade and left both
files under. The consequence for the next loop is that essentially no governance
edit can land in those two role files without a paired reclamation in the same
file. This is recorded, not filed: the criterion is met, the lint narrates and
never blocks, and the condition is a property of the budget configuration rather
than a defect this RC introduced.

### Finding 4: Regression module is collected, passing, and honestly constructed

**Category:** pass

`team/tests/unit/test_worktree_copy_git_isolation.py` (212 lines, new) was read
top-to-bottom per Principle 3. It collects and passes under the gated unit runner
(`tests/unit/test_worktree_copy_git_isolation.py ..` at 22% of the run). Quality
observations, all favourable:

- **The negative case is real.** `test_recursive_copy_of_linked_worktree_moves_source_branch`
  asserts the hazard still reproduces, so the positive case cannot pass vacuously
  if a future git version or a changed fixture stops producing the pointer shape.
  It additionally asserts `.git` is a file in both the worktree and the copy.
- **Assertions compare resolved commit hashes**, not exit status or log text.
- **Isolation is explicit, not ambient.** Every `git` call runs through `_git()`,
  which strips the six `GIT_DIR`/`GIT_WORK_TREE`-family redirect variables from the
  environment, so the fixture's isolation does not depend on the invoking agent's
  shell. All state lives under pytest `tmp_path`, which the parent conftest
  redirects under `$PGAI_AGENT_KANBAN_TEMP_DIR`. Test-fidelity checklist item 6
  (no state-mutating work against the live install) is satisfied by construction.
- **`git` is guarded** via `require_binary("git", ...)` in an autouse fixture, so
  the module skips rather than errors on a host without git.
- **No provenance litter.** The docstring cites `docs/test-authoring-guidelines.md`
  and the anti-pattern by name and carries no bug ID, task ID, or version. CODER
  removed an earlier `BUG-0284` docstring mention in commit `b4cbb4c` before merge;
  the provenance lint confirms 0 findings on the shipped file.

### Finding 5: Both gated runners exit 0

**Category:** pass

The authoritative suite verdict, from the same commands an operator or CM would
run, executed from the RC worktree root:

- `bash team/scripts/run-unit-tests.sh --coverage` -> **exit 0**. `4532 passed,
  31 skipped in 726.55s`. Lint pre-flight passed (`governance_byte_budget: OK`,
  `lint_skip_bug_gate: OK`, `bare_tmp_cleanliness: OK`, `listener_cleanliness: OK`).
- `bash team/scripts/run-integration-tests.sh --coverage` -> **exit 0**. `465 passed,
  1 skipped in 507.45s`. Same pre-flight checks green.

`UNIT_EXIT == 0` AND `INTEGRATION_EXIT == 0`, so the suite-pass precondition for a
PASS recommendation is satisfied on gated-runner evidence, not on a direct pytest
line. Captured output: `${PGAI_AGENT_KANBAN_TEMP_DIR}/tester/unit_output.txt` and
`${PGAI_AGENT_KANBAN_TEMP_DIR}/tester/integration_output.txt`.

### Finding 6: All Python files compile and all bash files pass syntax check

**Category:** pass

Requirements-doc acceptance criteria 2 and 3, run across the whole worktree rather
than only the diff:

- `python3 -m py_compile` over all **467** `.py` files -> exit 0, no output.
- `bash -n` over all **276** `.sh` files -> **0** failures.

### Finding 7: Pre-flight merge verification passed

**Category:** pass

Step 2a. Every task directory under `$PGAI_PROJECT_ROOT/tasks/` whose `status.md`
reads `DONE` and whose README names a `## Feature Branch` other than `none` was
scanned; every such branch has already been deleted post-merge (the normal CODER/
WRITER Procedure A Step 8 cleanup), so Check 1 and Check 2 were correctly skipped
for each and no branch was left unmerged or zero-commits-ahead. Zero anomalies.
The RC's own two feature branches (`ai_feature/WRITER-20260915-027-...`,
`ai_feature/CODER-20260915-028-...`) are present in the RC history as `--no-ff`
merge commits `d1d9b5f` and `86fe323`.

### Finding 8: No release-state drift

**Category:** pass

Step 6.1. `pp_last_released_version pgai-agent-kanban` -> `v2.22.4` (from the
`ai_`-prefixed tag line; latest tag is `ai_v2.22.4`, and no `ai_v2.22.5` tag exists
yet, which is correct for an in-flight RC). `semver_gt v2.22.5 v2.22.4` is true.
`release-state.md` reads `Active RC: v2.22.5`, `RC Opened By Task:
CM-20260915-029-open-rc-2-22-5`, `Last Released: v2.22.4` — well-formed `vX.Y.Z`,
and the branch `ai_rc/v2.22.5` it names really exists. The two values were checked
independently, not against each other.

### Finding 9: Skip-cites-real-bug gate clean

**Category:** pass

Step 6.13. The authoritative gate ran inside the unit pre-flight:
`lint_skip_bug_gate: BUG-NNNN file-existence check: 0 missing files` /
`OK — all skip annotations are compliant`. A manual grep surfaced four IDs
(`BUG-1234`, `BUG-1235`, `BUG-1237`, `BUG-1239`) with no file in `bugs/`; all four
were traced to string literals inside the fixture payloads of
`team/tests/unit/test_skip_bug_citation_scan.py` (they are the inputs that test
drives the scanner with), not real skip annotations. False positives of the crude
grep; the gate, which is the source of truth, is clean.

### Finding 10: Constraint negative checks clean

**Category:** pass

Step 6.9 / 6.14. The full RC diff carries no hardcoded secret, credential, token,
or password. No new script was added, so directive 6 (`--help` naming every
argument) has no new subject. No new operational value is hardcoded, and no
`lib/` operation is re-implemented — the RC adds prose and one test module.
Step 6.8 (synthetic release simulation) **does not apply**: the diff touches no
release-lifecycle file (`cm-release.sh`, `cm-open-rc.sh`, `cm-cancel-rc.sh`,
`project_paths.sh`, or anything else under `team/scripts/`). Recorded as skipped
with reason, not silently omitted.

### Finding 11: Prereq dependency graph is sound

**Category:** pass

Step 6.10. Five tasks, all referenced IDs resolve to real folders, no cycles:
`CM-029 (open RC, no prereq)` -> `WRITER-027` -> `CODER-028` -> `TESTER-030` ->
`CM-032 (release)`. Lifecycle bookends are correctly linked with CM-open first and
TESTER before CM-release. Sequence numbers are non-colliding; `031` is simply
unused in this plan, which is a gap in numbering, not a collision.

## Code Coverage

Informational only; does not enter the PASS precondition. Read from the
`totals` object of each runner's JSON report.

- **Unit:**        line 75%  |  branch 62%   (11906/15806 stmts, 2928/4744 branches; blended `percent_covered` 72.2%)
- **Integration:** line 7%   |  branch 6%    (1176/15797 stmts, 277/4744 branches; blended `percent_covered` 7.1%)

The integration figure measures the integration suite alone against the whole
codebase and is normal for this project's split; the unit suite carries the bulk.

## Lint Findings (non-blocking)

None.

Both findings files were written and both are empty:
`${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-unit.json` (`runner=run-unit-tests.sh`,
`generated_at=2026-09-15T11:32:16+00:00`, 0 findings) and
`${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-integration.json`
(`runner=run-integration-tests.sh`, `generated_at=2026-09-15T11:48:07+00:00`,
0 findings). Nothing to cite and nothing to file.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=8d freshness=stale — the docker+podman claude-abliterated pairs failed on a Dockerfile FROM parse error and the podman/rocky9 pair on its COPY --chmod step, tracked by BUG-0167-cold-build-claude-abliterated-failure (now `## Status: done`, so the defect was repaired in a later release than the one this record exercised); stale on both counts (8d exceeds the 7-day window, and v2.9.20 is not the v2.22.5 under verification), so nothing is known about whether this tree's three Dockerfiles build.

Read through `deep_verify_ledger_read_latest`, which returned 0. The lane was not
run by this verification and is not an RC step. Per Step 5d this is context the
rating is made with, not a finding: it does not move `## Recommendation` or
`## Systemic Risk`, and the red verdict is cited rather than re-filed.

## Autonomous criterion

Manual interventions required: none.

1. **Tasks force-promoted from WAITING to BACKLOG manually?** **NO.** Every
   promotion in this RC is attributed to the wake script in
   `logs/cron-{writer,coder,tester}.log` — e.g. `wake(tester): WAITING task
   TESTER-20260915-030-verify-2-22-5: prerequisites satisfied, promoting to
   BACKLOG` at `2026-09-15T11:07:51+00:00`, and the equivalent line for
   WRITER-027 at `10:04:39`.
2. **`release-state.md` edited outside the CM scripts?** **NO.** The file's three
   in-flight fields were written by `cm-open-rc.sh` under
   CM-20260915-029-open-rc-2-22-5, which reports exit 0 and lists the file plus
   `release-state/v2.22.5.json` as its artifacts.
3. **Scripts patched mid-build?** **NO.** The RC diff touches five files, none
   under `team/scripts/`, `subagents/`, or any other tooling path.
4. **Queue files manually edited?** **NO.** Markers for the five tasks are in the
   states the wake script writes: `[x]` for the three DONE tasks, `[A]` for this
   active TESTER task, `[W]` for the not-yet-eligible CM release task.

No HALT, HALT-AFTER, or project-scoped HALT flag is present. The one intra-RC
correction — CODER's commit `b4cbb4c` dropping a bug ID from a test docstring —
was the agent self-servicing its own provenance finding inside its own session
before merging, which is the documented agent-resolvable path, not an intervention.

## Caveats

- **Observations recorded but not filed, with the reasoning.** Two things are worth
  a reader's attention and neither is a defect against this RC's spec, so neither
  was filed via Path C:
  - *Byte-budget headroom* (Finding 3). 16 bytes on both `coder` and `tester`. The
    acceptance criterion asks for `under` and gets it; the lint narrates and never
    blocks. Filing a follow-up here would have elevated the recommendation off PASS
    over a configuration property this RC did not introduce, which would
    misrepresent the release. Flagged instead so the next governance edit to those
    two role files budgets for a paired reclamation.
  - *Automated coverage pins one of three alternatives.* The new module tests
    option 1 (clone), which the canon names "the default answer" and which is what
    CODER-028 was scoped to pin. Options 2 (detached worktree) and 3 (sever the
    copied `.git`) are documented but not covered by CI; I exercised both by hand
    this run and both left the source branch unmoved (Finding 2). This is a
    Step 6.11 "untested new mechanism" observation, below the bar for a finding
    because the bug's own acceptance bullet names the alternative in the singular.
- **A portability concern I raised and then disproved.** `git clone` does not copy
  the source repository's local `user.name`/`user.email`, so I expected the second
  test case's commit-inside-the-clone to depend on an ambient global git identity
  and to error on a host without one. Re-run with `GIT_CONFIG_GLOBAL=/dev/null
  GIT_CONFIG_SYSTEM=/dev/null` and a throwaway `HOME`, the commit still exits 0 —
  git derives an identity rather than refusing. No fidelity gap; recorded so the
  negative result is not re-investigated next cycle.
- **For CM at promotion time.** The pre-release deep-verify freshness check
  (`deep-verify-freshness-check.sh --project pgai-agent-kanban --version v2.22.5`)
  will refuse against the current ledger record: it is `failed-to-build`, 8 days
  old, and for `v2.9.20`. That is a promotion-playbook step with its own remedy
  (`cold-build-now.sh --project pgai-agent-kanban`), not a TESTER finding, and it
  does not bear on this RC's recommendation. Noted only so the refusal is expected
  rather than surprising.
- **Live-install safety.** Every behavioral reproduction in this report ran inside
  `mktemp -d -p "$PGAI_AGENT_KANBAN_TEMP_DIR"` sandboxes. No state-mutating script
  was invoked against the operator's kanban root, and no bare `/tmp` path was
  written.
- **Fresh verification.** `artifacts/` was empty at start — no prior report or
  `.previous-RUN-N` file existed for this task. Every result above comes from
  commands run during this session.
