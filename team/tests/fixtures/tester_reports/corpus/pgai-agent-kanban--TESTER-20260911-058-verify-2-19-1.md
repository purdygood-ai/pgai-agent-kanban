# Verification Report: ai_rc/v2.19.1

## Summary

Verified release candidate v2.19.1 (worktree detached at `d7a9e15`, byte-identical to
`ai_rc/v2.19.1`) against `projects/pgai-agent-kanban/requirements/v2.19.1-bugfix-bundle-20260911.md`.
The bundle carries exactly one item, BUG-0244: `docs/OPERATIONS.md` cites `docs/dashboard.md`
three times, but the document was absent from `team/config/docs-manifest.txt`, so neither
`install.sh` nor `team/scripts/upgrade.sh` delivered it — the pointer resolved in a repository
checkout and dangled on every installed or upgraded root, and the two docs-reachability unit
tests failed accordingly. The RC's entire diff against `ai_main` is one inserted line adding
`dashboard.md` to the manifest. Verification exercised the fix behaviorally rather than
structurally: a fresh `install.sh` into a throwaway root now delivers `docs/dashboard.md`, an
`upgrade.sh` run over a root installed from the pre-fix `ai_main` tree delivers it too, and the
same pre-fix tree installed on its own does **not** deliver it — so the manifest line is
demonstrably the thing doing the work. The manifest's transitive-closure rule was re-checked
independently with the test's own regex (zero outbound `docs/` citations from `docs/dashboard.md`),
and an independent BFS over the freshly installed root resolved all 26 `docs/` citations reachable
from the installed governance files. Both gated runners exited 0. No `bug`, `gap`, or
`stale-assertion` findings were recorded.

## Recommendation

PASS

## Systemic Risk

low

## Findings

### Finding 1: Pre-flight — worktree identity, cleanliness, stashes, merge state

**Category:** pass

`git rev-parse HEAD` = `d7a9e152b4e348384b846491afbdccd7b0bdba78` = `git rev-parse ai_rc/v2.19.1`.
`git status --short` empty; `git stash list` empty. Pre-flight merge verification scanned every
DONE task under `projects/pgai-agent-kanban/tasks/`: no DONE task retains a live feature branch
(CODER deleted `ai_feature/CODER-20260911-056-docs-manifest-dashboard-doc` after merging), so
Check 1 and Check 2 had no live branch to evaluate and reported zero anomalies. The RC's only
two commits are `d32c190` (the manifest line) and `d7a9e15` (the `--no-ff` merge).

### Finding 2: BUG-0244 acceptance item 1 — gated unit runner exits 0 and the module reports 6 passed

**Category:** pass

`bash team/scripts/run-unit-tests.sh --coverage` → **exit 0**, `4020 passed, 3 skipped in 607.21s`.
Every lint pre-flight reported OK (`test_anti_patterns`, `skip_cites_real_bug`, `orphan_tests`,
`api_parity`, `icd_freshness`, `icd_compat`, `changelog_freshness`, `help_presence`,
`python_import_completeness`, `dual_channel_pair`, `gate_census_compliance`, `bare_tmp_cleanliness`,
`listener_cleanliness`, and the rest). The module named in the bug appears in the run as
`tests/unit/test_installed_governance_docs_citations_resolve.py ......  [ 71%]` — six dots, six
passed, zero failed, which is the bug's acceptance criterion met inside the authoritative runner
rather than under a bare pytest invocation.

### Finding 3: BUG-0244 acceptance item 2 — both delivery routes produce the file, and the fix is load-bearing

**Category:** pass

Run against throwaway roots under `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/tester-058/`,
with `PGAI_AGENT_KANBAN_ROOT_PATH` repointed per the live-install safety rule; the operator's live
root was never a target of any of the three runs.

1. **Fresh install (RC tree).** `env PGAI_AGENT_KANBAN_ROOT_PATH=<fresh-root> bash install.sh --force
   --no-system-cron --stamp-version v2.19.1-tester` → exit 0. `<fresh-root>/docs/` contains 11 files
   plus `audits/`, including `dashboard.md` (8171 bytes).
2. **Failure mode reproduced (pre-fix tree).** `git archive ai_main | tar -x` into a scratch tree
   (confirmed: no `dashboard.md` line in its manifest), then installed into `<old-root>` → exit 0,
   and `<old-root>/docs/dashboard.md` is **absent**. This is the guard-triggering half of Step 6.5:
   the same installer, the same command, one manifest line different, and the document does not
   arrive. The fix is the cause of the delivery, not a coincidence of it.
3. **Upgrade over the pre-fix root.** `env PGAI_AGENT_KANBAN_ROOT_PATH=<old-root> bash
   team/scripts/upgrade.sh --force --no-backup --dev-tree <rc-worktree> --stamp-version v2.19.1-tester`
   → exit 0, log line `[upgrade] Upgraded: docs/dashboard.md`, and the file is present afterwards.

### Finding 4: BUG-0244 acceptance item 3 — the manifest's transitive-closure rule holds

**Category:** pass

The manifest header's second rule is that a listed document must not itself cite a `docs/` path the
manifest omits. Checked with the test module's own pattern, read out of the source rather than
recalled (`_DOCS_CITATION_RE = re.compile(r"docs/[A-Za-z0-9._/-]+\.md")`):
`grep -oE 'docs/[A-Za-z0-9._/-]+\.md' docs/dashboard.md` returns nothing, so `dashboard.md` adds no
new closure edges and one manifest line was the complete change.

Two independent confirmations beyond the criterion, both against the freshly installed root rather
than the checkout:

- A BFS from the installed seed set (`README.md`, `DIRECTIVES.md`, `OVERVIEW.md`, `SOP-core.md`,
  `roles/*.md`) re-scanning every document it resolves: 23 files scanned, 26 citations checked,
  **zero unresolved**.
- The non-transitive root-variable dimension applied by hand to the newly delivered document: all
  three command paths it names (`scripts/dashboard/cadence-loop.sh`, `scripts/dashboard/metrics.sh`,
  `scripts/dashboard/show-metrics.sh`) exist under the fresh root. The delivered document is usable,
  not merely present.

### Finding 5: BUG-0244 acceptance item 4 and the fix-direction constraint

**Category:** pass

`grep -n 'dashboard.md' team/config/docs-manifest.txt` → `32:dashboard.md`.
`grep -c 'docs/dashboard.md' docs/OPERATIONS.md` → `3`. The repair took the sanctioned direction —
deliver the cited document — and did not remove the cross-reference. The new line matches the
manifest's conventions exactly: path relative to `docs/`, one per line, no inline comment, no prose,
placed directly beneath `OPERATIONS.md`, the document that cites it.

### Finding 6: Requirements-doc acceptance criteria

**Category:** pass

- *Each bundled item is addressed in the implementation* — the bundle names one item, BUG-0244; all
  four of its own acceptance items are verified above.
- *All Python files compile cleanly* — `py_compile` over all 423 `.py` files in the tree: exit 0, no
  output.
- *All bash files pass syntax check* — `bash -n` over all 269 `.sh` files: zero failures.

### Finding 7: Integration suite

**Category:** pass

`bash team/scripts/run-integration-tests.sh --coverage` → **exit 0**, `449 passed, 1 skipped in
431.69s`. All lint pre-flights OK, including `bare_tmp_cleanliness` and `listener_cleanliness`.

### Finding 8: Release-state drift check (Step 6.1)

**Category:** pass

`pp_last_released_version pgai-agent-kanban` → `v2.19.0`; `semver_gt v2.19.1 v2.19.0` is true.
`release-state.md` holds `Active RC: v2.19.1`, `RC Opened At: 2026-09-11T21:25:11+00:00`,
`RC Opened By Task: CM-20260911-057-open-rc-2-19-1`, `Last Released: v2.19.0` — well-formed
`vX.Y.Z`, and the named RC branch `ai_rc/v2.19.1` exists. No drift. No HALT flag at either scope.

### Finding 9: Skip-cites-real-bug gate (Step 6.13)

**Category:** pass

The gated runners' own `skip_cites_real_bug` lint reported OK in both suites. Confirmed
independently: no skip annotation anywhere under `team/tests/` cites a `BUG-NNNN` ID, and the only
grep hits for the placeholder pattern are inside archived runner logs under
`team/tests/artifacts/coverage-*/` — captured output describing the lint itself, not skip
annotations in test code.

### Finding 10: Prerequisite dependency graph (Step 6.10)

**Category:** pass

`CM-20260911-057-open-rc-2-19-1` (DONE, no prereqs) → `CODER-20260911-056-docs-manifest-dashboard-doc`
(DONE) → `TESTER-20260911-058-verify-2-19-1` (WORKING) → `CM-20260911-060-release-2-19-1` (WAITING,
prereqs: the CODER and TESTER tasks). Acyclic, every referenced task directory exists, lifecycle
bookends correctly linked.

### Finding 11: Coding-standards spot-check (Step 6.14)

**Category:** pass

The RC's whole diff is a single data line in a configuration manifest: no new script (so directive 6
`--help` does not apply), no new production string, no hardcoded operational value, no duplicated
`lib/` implementation. Directive 8 holds — the added line names no bug, task, or version identifier.

## Lint Findings (non-blocking)

Both runners wrote a findings file; the two files carry byte-identical content — 8 quality-class
findings across 2 lint classes, no breakage-class finding in either.

- `hygiene` (class `provenance`, 3 instances) — disposition per instance:
  - `release-notes/v2.19.0.md:37` — `[task-ID]` reference `TESTER-20260911-053` in a shipped release
    note, pointing the reader at a kanban task folder — **filed BUG-0248 (Path C-A: filed as BUG —
    code defect / regression / test failure)**. New since the previous verification run; the file
    was written when v2.19.0 shipped, after that run's findings were produced, so no open bug named
    it.
  - `team/tests/unit/test_posture_bar_truth.py:20` — `[task-ID]` reference `CODER-20260911-049` —
    cites **BUG-0245 (open)**.
  - `release-notes/v2.16.0.md:42` — `[task-ID]` reference `TESTER-20260910-070` — cites
    **BUG-0242 (open)**.
- `governance_byte_budget` (5 instances: `coder` 71756/69632, `writer` 70846/69632, `tester`
  103565/102400, `cm` 86775/86016, `overwatch` 76042/73728) — cites **BUG-0246 (open)**, which
  records these five instances with these exact byte counts. Not re-filed.

Every finding is dispositioned; none is silently ignored and none blocks. Per Step 5c these
findings, and the bug filed solely to track one of them, do not move `## Recommendation` or
`## Systemic Risk`.

## Code Coverage

Read from the runners' JSON `totals` objects (field names confirmed against a generated file, not
assumed): `percent_statements_covered` for line, `percent_branches_covered` for branch.

- Unit:        line 75%  |  branch 62%   (15453 stmts, 4640 branches; blended `TOTAL` row reads 72%)
- Integration: line 8%   |  branch 6%    (15444 stmts, 4640 branches; blended `TOTAL` row reads 7%)

Informational only; coverage does not enter the PASS precondition.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=4d freshness=stale — the claude-abliterated flavor failed a Dockerfile parse under both engines and podman/rocky9 failed at a COPY step; that run filed BUG-0167 (since closed), and the record is stale because v2.9.20 is not the v2.19.1 under verification, so nothing has built these images cold against this tree.

## Autonomous criterion

Manual interventions required: none.

1. Tasks force-promoted from WAITING to BACKLOG by hand? **NO.** This task's own status carries the
   wake script's promotion note ("Promoted from WAITING to BACKLOG by wake script: prerequisites
   satisfied"); `CM-20260911-060-release-2-19-1` is still `WAITING` with its queue marker `[W]`,
   which is exactly what an unpromoted task looks like.
2. `release-state.md` edited outside the CM scripts? **NO.** All four fields are consistent with a
   `cm-open-rc.sh` write at 21:25:11 naming `CM-20260911-057-open-rc-2-19-1`, and `Last Released`
   matches the `ai_v2.19.0` tag that `cm-release.sh` created.
3. Scripts patched mid-build? **NO.** The RC's two commits touch `team/config/docs-manifest.txt`
   only; nothing under `team/scripts/` or `subagents/` was committed while the RC was in flight, and
   `ai_main` received no commit after the v2.19.0 release commits at 21:14.
4. Queue files manually edited? **NO.** Queue mtimes track wake activity through the cycle
   (`cm_backlog.md` 21:25:43 at RC open, `coder_backlog.md` 21:48:58 at CODER completion,
   `tester_backlog.md` 21:49:52 at TESTER enqueue), and the markers are the framework's own
   vocabulary (`[x]` done, `[W]` waiting, `[A]` active).

## Caveats

- **Suite runtime.** The unit suite with `--coverage` takes just over 10 minutes on this host and
  the integration suite just over 7. Two earlier attempts at the unit run were killed by this
  session's own command-timeout and by a `pkill` cleanup after two runs were accidentally started
  concurrently; the results reported above come from a single clean, uninterrupted run of each
  suite, launched detached and read from its own captured output. No partial run contributed to any
  verdict here.
- **Observation (kanban state, not the RC).** `projects/pgai-agent-kanban/bugs/` holds 7 stale
  `.claim-NNNN.lock` sentinels from historical filings (0136, 0137, 0160, 0172, 0173, 0211, 0212)
  plus 3 `.orphan` variants. They are harmless today — `claim_next_bug_id` allocates the next free
  slot and is far past those numbers, and this run's own claim released cleanly — so this is
  recorded as an observation rather than a finding.
- **Observation (host state, not the RC).** The operator's live install at
  `$HOME/pgai_agent_kanban` is at `v2.9.19` and has no `docs/` directory at all, so the
  document this RC makes deliverable will only appear there after the install is upgraded. That is
  pre-existing deployment state outside the RC's working tree, and the RC's own delivery routes were
  verified against throwaway roots instead.
- **Step 6.8 (synthetic release simulation) not triggered.** The RC's diff touches no
  release-lifecycle file — no `cm-release.sh`, `cm-open-rc.sh`, `cm-cancel-rc.sh`,
  `lib/project_paths.sh`, or any other `team/scripts/` path. Skipped per the step's own trigger
  condition.
- **Test fidelity.** The two tests that caught BUG-0244 build a simulated installed root rather
  than reading the checkout, which is precisely the condition the defect lived in; this run
  additionally exercised the real `install.sh` and `upgrade.sh` against throwaway roots, so the
  fidelity question the checklist asks ("installed tree simulated, not running from the dev tree
  directly") is answered by both the suite and by direct execution.
