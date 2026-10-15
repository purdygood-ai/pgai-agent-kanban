# Verification Report: rc/v2.23.16

**Task:** TESTER-20260918-036-verify-2-23-16
**Requirements doc:** `$HOME/pgai_agent_kanban/projects/pgai-agent-kanban/requirements/v2.23.16-bugfix-bundle-20260918.md`
**Worktree under test:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260918-036-verify-2-23-16`
**HEAD:** `21cf246d4e8211f8a22fe47f40826b0a40a13788` (== `ai_rc/v2.23.16`)
**Verified:** 2026-09-18

## Summary

Verified release candidate `ai_rc/v2.23.16` (HEAD `21cf246`) against
`requirements/v2.23.16-bugfix-bundle-20260918.md` in the detached per-task worktree. The RC is a
three-item bug bundle: BUG-0329 (the gated runners' replay line misclassified an exported variable
that also carried a second bash attribute, telling the pasting shell to unset it), BUG-0330 (the
shared `--help` idiom in twenty operator scripts could be SIGPIPE'd under `pipefail`, so a script
printed perfect help and still exited 141 about one run in a hundred), and BUG-0328 (nine
hygiene-lint `provenance` citations). All three bundled items are addressed. Both code fixes were
verified behaviorally in both directions — the original failure mode reproduced against `ai_main`
and confirmed closed on the RC, and the previously-working cases confirmed unbroken — not merely
read in the diff. Both gated runners exited 0 (`run-unit-tests.sh`: 4744 passed, 31 skipped;
`run-integration-tests.sh`: 515 passed, 1 skipped), with zero quality-class lint findings on either
suite, including a now-clean `lint_doc_command_truth`, the very check whose intermittent failure
motivated BUG-0330. The three requirements-doc acceptance criteria all pass. The build ran
autonomously with no manual intervention. No bug, gap, or stale assertion was found, so nothing was
filed via Path C (autonomous follow-up filing — non-blocking).

## Recommendation

PASS

## Systemic Risk

low

## Findings

### Finding 1: Pre-flight — RC intact, worktree clean

**Category:** pass

- `git rev-parse HEAD` = `21cf246d4e8211f8a22fe47f40826b0a40a13788` = `git rev-parse ai_rc/v2.23.16`. Commit identity confirmed (detached HEAD, as the wake script checks out).
- `git status --porcelain` — empty.
- `git stash list` — empty.
- Scanned all 590 task folders under `$PGAI_PROJECT_ROOT/tasks/`. No task with `## State: DONE` and a `## Feature Branch` other than `none` has a locally surviving branch, so Check 1 (merged into the RC) and Check 2 (contributed commits) pass trivially for every one — the normal post-merge shape.
- One stray local branch exists, `ai_feature/CODER-20260917-020-agent-metrics-avg-wall`, and is NOT an ancestor of `ai_rc/v2.23.16`. Its task is `WONT-DO`, not `DONE`, so Step 2 does not apply to it. Recorded as an observation under Caveats, not a finding.

### Finding 2: Step 6.1 — release-state.md schema and version ordering

**Category:** pass

`projects/pgai-agent-kanban/release-state.md` holds exactly the four schema fields:
`## Active RC` = `v2.23.16`, `## RC Opened At` = `2026-09-18T16:08:28+00:00`,
`## RC Opened By Task` = `CM-20260918-035-open-rc-2-23-16`, `## Last Released` = `v2.23.15`.
`## Last Released` is a well-formed `vX.Y.Z`. Version ordering checked with the shared
helper (`team/scripts/lib/semver.sh`): `pp_last_released_version pgai-agent-kanban` returns
`v2.23.15`, and `semver_gt v2.23.16 v2.23.15` succeeds. No inline string comparison used.

### Finding 3: Acceptance criterion 1 — bundled item BUG-0329 (replay export detection) is addressed

**Category:** pass

Production change: `team/scripts/lib/gated_runner_lint.sh`, `gated_runner_replay_command`.
The classifier `declare -p "$name" | grep -q '^declare -x '` was replaced by a capture of the
whole attribute run followed by a membership test for `x`:

```bash
declare_output="$(declare -p "$name" 2>/dev/null)" || declare_output=""
if [[ "$declare_output" =~ ^declare\ ([^\ ]+)\  ]] && [[ "${BASH_REMATCH[1]}" == *x* ]]; then
```

**Behavioral verification (Step 6.5) — original failure mode AND new normal case, both exercised.**
Run from the worktree root:

| Case | Declaration | RC (`21cf246`) result | Pre-RC (`ai_main`) result |
|---|---|---|---|
| A — original failure mode | `declare -rx PYTEST_ADDOPTS=--basetemp=/x` | `PYTEST_ADDOPTS=--basetemp=/x` (correct) | `-u PYTEST_ADDOPTS` (the defect) |
| B — new normal case | `export PYTEST_ADDOPTS=...` | `PYTEST_ADDOPTS=--basetemp=/x` (correct) | correct |
| C — second attribute, integer | `declare -ix PYTHONHASHSEED=0` | `PYTHONHASHSEED=0` (correct) | not re-run; same class as A |
| D — set but NOT exported | `PYTEST_ADDOPTS=...` | `-u PYTEST_ADDOPTS` (correct) | correct |
| E — unset entirely | `unset PYTEST_ADDOPTS` | `-u PYTEST_ADDOPTS` (correct) | correct |

The pre-RC reproduction was executed against the `ai_main` copy of the file, confirming the
defect was real and that the RC closes it without breaking the cases that already worked.
The fix does not over-match: `x` is the export attribute letter only, and `BASH_REMATCH[1]`
captures only the attribute token (`-x`, `-rx`, `-ix`, `--`), never the variable name, so a
name containing the letter `x` cannot be misclassified.

Regression test added at `team/tests/unit/test_gated_runner_stated_test_order.py`,
`test_gated_runner_replay_command_carries_export_with_second_attribute` — it asserts both the
positive (assignment present) and negative (`-u NAME` absent) halves.

### Finding 4: Acceptance criterion 1 — bundled item BUG-0330 (help idiom SIGPIPE) is addressed

**Category:** pass

All twenty scripts named in BUG-0330 (eighteen at-risk plus the two that did not set
`pipefail`) were converted from `grep '^#' "$0" | sed ... | head -N` to
`mapfile -t __help_lines < <(grep '^#' "$0" | sed ...)` followed by
`printf '%s\n' "${__help_lines[@]:0:N}"`.

**Behavioral verification (Step 6.5).**

- **New normal case:** each of the 20 changed scripts invoked with `--help` 100 times inside a
  throwaway sandbox root (`env -u PGAI_TASKS_DIR -u PGAI_PROJECT_ROOT KANBAN_ROOT=$S ...`) —
  **0 non-zero exits out of 2000 invocations**.
- **Original failure mode reproduced:** the same loop run against a clone of `ai_main` checked out
  in a correct tree layout gives `team/scripts/dashboard/metrics.sh` **7 non-zero exits out of
  300** (~2.3 %), matching BUG-0330's measured ~1 % rate. The harness therefore demonstrably
  catches the pre-repair shape.
- **No output regression:** `--help` stdout compared byte-for-byte between the `ai_main` clone
  and the RC for all 20 scripts — **IDENTICAL** in every case, with identical line counts
  (20/25/30/35/40/45/50/80 as each script specifies).
- **Class closed, not one instance:** `grep -rn "grep '\^#'" --include='*.sh' . | grep 'head -'`
  returns zero hits tree-wide. Every `.sh` in the repo that uses the self-header extraction lives
  under `team/scripts/`, which is exactly the directory the new sweep test scans.

New test module `team/tests/unit/test_self_header_help_pipe_safety.py` (341 lines, read in full).
Its three groups are a deterministic static check that no script pipes the extraction into
`head -N`, a repeated-invocation sweep over the discovered scripts, a **negative proof** that the
pre-repair shape still fails (5000-line synthetic header against `head -1`, asserting at least one
exit 141), and a proof that the repaired shape holds at header sizes larger than any shipped
script. Discovery is by construct, not by a hardcoded path list, and a vacuity guard asserts at
least 20 scripts are found. Live discovery confirmed: 20 scripts. Test fidelity is high — the
negative proof is exactly the anti-pattern guard Step 6.5 asks for.

### Finding 5: Acceptance criterion 1 — bundled item BUG-0328 (hygiene-lint provenance) is addressed

**Category:** pass

`CODER-20260918-034-provenance-sweep` closed `WONT-DO` on the grounds that sibling ticket 031
had already cleared the citations. **Independently re-verified, not taken on trust:**

- `python3 team/scripts/lint_hygiene.py --classes provenance` → `OK — 0 findings across 8 scan
  director(y/ies)`, exit 0.
- `grep -rn "BUG-0327" --exclude-dir=.git .` → zero hits tree-wide. All nine instances BUG-0328
  enumerated (2 in `gated_runner_lint.sh`, 1 in `run-integration-tests.sh`, 1 in
  `run-unit-tests.sh`, 5 in `test_gated_runner_stated_test_order.py`) are gone.

The bundled item is addressed in the implementation, which is what the acceptance criterion asks;
that it was addressed incidentally by a sibling ticket rather than by its own ticket does not
change the outcome. See Caveats for the process note.

### Finding 6: Acceptance criterion 2 — all Python files compile cleanly

**Category:** pass

`python3 -m py_compile` over every `.py` in the tree (excluding `.git`) exits 0. Both files the
RC changed compile individually: `team/tests/unit/test_gated_runner_stated_test_order.py`,
`team/tests/unit/test_self_header_help_pipe_safety.py`.

### Finding 7: Acceptance criterion 3 — all bash files pass syntax check

**Category:** pass

`bash -n` over every `.sh` in the tree (excluding `.git`) reports no syntax error. All 21 `.sh`
files the RC changed pass individually.

### Finding 8: Step 6.9 — constraint negative checks

**Category:** pass

- `set -euo pipefail` count is unchanged between `ai_main` and the RC in all 21 changed shell
  scripts — the repair did not weaken the strict-mode guard it was working around.
- No hardcoded secret, password, API key, or token introduced anywhere in the diff.

### Finding 9: Step 6.13 — skip-cites-a-real-bug gate

**Category:** pass

- No placeholder shapes (`BUG-SKIP`, or `skip` adjacent to `BUG-` followed by letters) in the test
  tree. The one text hit is a prose sentence in `team/tests/unit/test_skip_bug_citation_scan.py`
  describing the gate's own behavior.
- The only `BUG-NNNN` IDs appearing near skip annotations (`BUG-1234`, `BUG-1235`, `BUG-1236`) are
  synthetic fixtures inside `test_skip_bug_citation_scan.py`, the gate's own test module — not real
  skip annotations.
- `bash team/scripts/lint_skip_bug_gate.sh` → `OK — all skip annotations are compliant`,
  `BUG-NNNN file-existence check: 0 missing files`.

### Finding 10: Step 6.10 — prerequisite dependency graph

**Category:** pass

Acyclic and correctly bookended. `CM-20260918-035-open-rc-2-23-16` has no prerequisites and ran
first; 031 and 032 depend on it; 033 depends on 035 and 032; 034 depends on 035, 031, 032, 033;
036 (this task) depends on 031–034; `CM-20260918-038-release-2-23-16` (`WAITING`) depends on
031–034 and 036. Every referenced task ID resolves to an existing folder. No cycles, no collisions.

### Finding 11: Step 6.8 — synthetic release simulation not applicable

**Category:** pass

The diff touches no release-lifecycle file: not `cm-release.sh`, `cm-open-rc.sh`,
`cm-cancel-rc.sh`, nor `team/scripts/lib/project_paths.sh`, nor anything in the RC open/close
cycle. `team/scripts/lib/gated_runner_lint.sh` is under `team/scripts/` but is a test-runner
helper, not part of the release cycle. Step skipped with this note, per the step's own condition.

### Finding 12: Step 5a / 6.6 / 6.7 — gated unit runner

**Category:** pass

```
cd <worktree> && bash team/scripts/run-unit-tests.sh --coverage
UNIT_EXIT=0
================= 4744 passed, 31 skipped in 887.83s (0:14:47) =================
Unit tests PASSED
[gated-runner] bare_tmp_cleanliness: OK
[gated-runner] listener_cleanliness: OK
[gated-runner] findings file written: /tmp/pgai_kanban_tmp/lint/findings-unit.json (0 finding(s))
```

Every quality-class pre-flight lint reported clean, including the two this RC bears directly on:

- `lint_doc_command_truth: OK — 0 findings` (656 citations across 13 documents). This is the exact
  consumer that reported `HELP FAILS ... exited 141` on `ai_rc/v2.23.15` and motivated BUG-0330.
  It is now clean — independent confirmation of Finding 4 at the consumer level.
- `lint_hygiene: OK — 0 findings across 8 scan director(y/ies)`, classes `provenance`,
  `version-named-test`, `comment-drift` — independent confirmation of Finding 5.

Also clean: `lint_dockerfile_static` (3 Dockerfiles, 0 violations), `lint_test_anti_patterns`
(0 findings, 529 files), `lint_skip_bug_gate`, `lint_orphan_tests`, `lint_api_parity`,
`lint_operator_verb_manifest`, `lint_upgrade_no_temp_copy_guard`, `lint_icd_freshness`,
`lint_icd_compat`, `lint_changelog_freshness`.

### Finding 13: Step 5a / 6.6 / 6.7 — gated integration runner

**Category:** pass

```
cd <worktree> && bash team/scripts/run-integration-tests.sh --coverage
INTEGRATION_EXIT=0
================== 515 passed, 1 skipped in 619.65s (0:10:19) ==================
Integration tests PASSED
[gated-runner] bare_tmp_cleanliness: OK
[gated-runner] listener_cleanliness: OK
[gated-runner] findings file written: /tmp/pgai_kanban_tmp/lint/findings-integration.json (0 finding(s))
```

Pre-flight clean, including `check_breakage_class: OK — 475 python file(s), 270 shell file(s)
checked, 0 breakage-class failures`, `lint_rc_empty_commits: OK -- checked 1 range(s)
(ai_main..ai_rc/v2.23.16), no empty non-merge commits found`, `lint_dual_channel_pair: OK — 248
file(s) checked, 0 offenders`, and `governance_byte_budget: OK` (all seven roles under budget;
tester is closest at 102384 / 102400).

**PASS evidence is the two gated runners' exit codes**, not a pytest "N passed" line. Direct
pytest was not used as a verdict source anywhere in this verification.

### Finding 14: Step 6.12 — autonomous operation criterion

**Category:** pass

`Autonomous operation criterion: Manual interventions required: none`

| # | Question | Answer | Evidence |
|---|---|---|---|
| 1 | Tasks force-promoted WAITING→BACKLOG by hand? | **NO** | Every promotion in the RC window appears in the wake logs as the wake script's own prerequisite evaluation: `wake(coder)` promoted 2 at 16:09:16 (031, 032, right after the RC opened), `wake(writer)` promoted 1 at 16:37:39 (033, after 032 went DONE), `wake(coder)` promoted 1 at 16:43:14 (034), `wake(tester)` promoted 1 at 16:46:50 (this task). No out-of-band state change. |
| 2 | `release-state.md` edited outside the cm scripts? | **NO** | File mtime `2026-09-18 16:08` matches its own `## RC Opened At` of `2026-09-18T16:08:28+00:00` and its `## RC Opened By Task` of `CM-20260918-035-open-rc-2-23-16`. Schema holds exactly the four fields. |
| 3 | Scripts patched mid-build (out-of-band commits to `team/scripts/`, `subagents/`, tooling)? | **NO** | The RC's first-parent history is exactly three `--no-ff` merge commits, one per DONE ticket, with no direct-to-RC commit: `199637d` (031), `54d516a` (032), `21cf246` (033). Each merge's second parent is the ticket's own feature-branch commit off the merge-base `0179b08`. `ai_main` has not moved since `0179b08` at 16:01:57, before the RC opened. The `team/scripts/` edits in this RC ARE the deliverables of tickets 031/032, landed through the normal branch-and-merge law, not out-of-band patches. |
| 4 | Queue files edited by hand? | **NO** | No HALT flag present at the kanban root or the project root. No entry in `logs/activity.log` during the RC window (last entry 2026-09-03). |

### Finding 15: Step 6.11 — spot-checks outside the spec

**Category:** pass

- **State drift:** none. `## Active RC` = `v2.23.16` and the branch `ai_rc/v2.23.16` exists at the
  commit under test. `VERSION` reads `v2.23.15` on the RC, which is correct — its git history shows
  it is written by CM at release time ("Add committed VERSION for v2.23.15"), and `ai_main` carries
  the same value. `CHANGELOG.md`'s top entry is `v2.23.15`; `release-notes/v2.23.16.md` does not yet
  exist. Both are CM's release-time work, not RC drift. `lint_changelog_freshness` agrees (`ok`).
- **Untested new mechanisms:** none found. Both code changes shipped with regression tests, and the
  help-idiom test discovers its subjects by construct rather than by a hardcoded list, so a future
  script adopting or regressing the shape is covered without a test edit.
- **Regressions of prior gaps:** BUG-0330 names three previously closed bugs in the same family
  (BUG-0270, BUG-0272, BUG-0325), each of which had repaired one script while leaving the idiom in
  place. This RC closes the class rather than the instance, and adds a static check that fails on
  the pre-repair shape anywhere under `team/scripts/`. That is the correct response to a recurring
  family.
- **Per-script slice-limit audit (judgment spot-check):** for all 20 converted scripts, the new
  array slice bound is byte-identical to the `head -N` limit it replaced (20/25/30/35/40/45/50/80,
  each matching its own script). No script silently changed how much help it prints.

### Finding 16: Step 6.14 — coding-standards spot-check

**Category:** pass

- No new script was added, so the "`--help` naming every argument" directive has no new subject.
- No new comment, docstring, help line, or log line in the diff cites a bug, task, or version
  identifier — confirmed by grepping the diff's added lines and by `lint_hygiene`'s `provenance`
  class reporting zero.
- No new hardcoded operational value: the slice bounds are the pre-existing per-script limits.
- The new `docs/coding-standards.md` section ("A help path prints completely and exits 0") was read
  in full. It states the rule, shows the failing shape, shows the required shape, explains the
  SIGPIPE-under-`pipefail` mechanism accurately, gives a by-reading checkability test, and says to
  apply it whether or not the script sets `pipefail` today. The prescribed shape matches the shape
  actually implemented in all 20 scripts byte-for-byte. Documentation-layer review (Step 6.3) found
  no error that would fail the first time an agent uses the file.

## Lint Findings (non-blocking)

None. `/tmp/pgai_kanban_tmp/lint/findings-unit.json` and
`/tmp/pgai_kanban_tmp/lint/findings-integration.json` were both present and both contain
`"findings": []` (generated `2026-09-18T17:04:07+00:00` and `2026-09-18T17:15:12+00:00`
respectively, matching this run). No quality-class lint fired on either suite, so no bug was
cited and none was filed.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=11d freshness=stale — The last cold-build record is red and stale on both counts (11 days exceeds the 7-day window, and v2.9.20 is not the v2.23.16 under verification); the bug that run filed, BUG-0167-cold-build-claude-abliterated-failure, is already closed `done`, so nothing is re-filed here, and nothing is known about whether this RC's images build.

The lane was not run: TESTER runs no container build during RC verification, per Step 5d. The tree
ships three Dockerfiles (`docker/claude-abliterated`, `docker/debian`, `docker/rocky9`) and carries
`team/scripts/deep-verify.sh`, so neither Step 5d gap applies. For context only — not a substitute
for a build — the unit runner's `lint_dockerfile_static` checked all three Dockerfiles on this RC
with 0 violations.

## Coverage (informational only)

| Suite | line (statement) % | branch % | blended % |
|---|---|---|---|
| unit | 76.59 | 63.14 | 73.48 |
| integration | 9.87 | 7.97 | 9.43 |

Read from the `totals` object of `/tmp/pgai_kanban_tmp/coverage/unit.json` and
`.../integration.json`, using the field names verified against the generated files
(`percent_statements_covered`, `percent_branches_covered`, `percent_covered`). The integration
figure is low because that suite drives scripts through subprocesses that the Python coverage
harness does not instrument; it is an observation, not a finding. Coverage did not enter the
verdict.

## Autonomous criterion

Autonomous operation criterion: Manual interventions required: none. All four Step 6.12 checks
answered NO — see Finding 14 for the evidence behind each.

## Caveats

- **`CODER-20260918-034-provenance-sweep` closed `WONT-DO` with `Needs Human: yes`.** Its own
  summary states the flag was set only because SOP-core's WONT-DO procedure requires it, with no
  open question to adjudicate. I did not take that on trust: the bundled item it was to address
  (BUG-0328) was independently re-verified clean from the RC worktree (Finding 5), and the gated
  unit runner's own `lint_hygiene` pre-flight agrees. The acceptance criterion "each bundled item is
  addressed in the implementation" is met. The process note for a human reader is only that the
  ticket was written against findings a sibling ticket in the same RC had already cleared, so it had
  nothing left to do by the time it woke — an ordering artifact of bundling, not a defect, and not
  filed.
- **One stray local branch, `ai_feature/CODER-20260917-020-agent-metrics-avg-wall`, is not an
  ancestor of the RC.** Its task is `WONT-DO`, so Step 2's anomaly checks (which apply to `DONE`
  tasks) do not reach it. That task's own record says its branch carried zero commits and was
  deleted; this surviving branch is from the earlier task of the same name and is empty relative to
  its base. Noted for housekeeping only.
- **Two stale exit-code files were present in the shared scratch directory** at the start of this
  run: `/tmp/pgai_kanban_tmp/tester/unit.exit` (15:44) and `.../integration.exit` (15:55), both from
  earlier TESTER runs on a different RC. Both were moved aside to
  `*.stale-prior-run*` so that only this run's exit codes could be read; `UNIT_EXIT` and
  `INTEGRATION_EXIT` above are this run's. Recording it because a wait-for-file loop reading that
  directory will return a prior run's verdict instantly if the stale file is not cleared first.
  This is kanban scratch hygiene, not a defect in the shipped tree, so it is not filed.
- **The new sweep test scans `team/scripts/` only.** Every `.sh` in the repository that uses the
  self-header extraction happens to live there today (verified tree-wide), so there is no present
  gap; a future entry point outside that directory adopting the idiom would not be swept. Recorded
  as an observation, not a finding, since nothing concrete is currently uncovered.
- `team/tests/unit/test_gated_runner_stated_test_order.py`'s existing coverage of
  `gated_runner_replay_command` still does not exercise the exported-array (`declare -ax`) case,
  where `${!name}` yields only the first element. That shape is not used by either shipped runner
  and predates this RC; it is a plausible rather than concrete concern, so it is recorded here
  rather than filed.
