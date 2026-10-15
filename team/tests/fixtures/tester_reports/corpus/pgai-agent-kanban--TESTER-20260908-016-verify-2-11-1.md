# Verification Report: ai_rc/v2.11.1

**Task:** TESTER-20260908-016-verify-2-11-1
**RC branch:** `ai_rc/v2.11.1` @ `d40e7b2` (worktree HEAD verified identical)
**Requirements doc:** `projects/pgai-agent-kanban/requirements/v2.11.1-bugfix-bundle-20260908.md`
**Bundled item:** BUG-0193-verify-rc-state-ignores-branch-prefix
**Verified:** 2026-09-08
**Agent:** tester · **Model:** claude-fable-5-1

## Summary

v2.11.1 is a single-bug patch RC. It resolves BUG-0193 by routing every RC branch
name that `team/scripts/verify-rc-state.sh` Check e constructs or matches through
`pp_prefix_branch`, and adds a six-case integration test covering the prefixed and
unprefixed paths. Verification confirmed the fix behaviourally, not structurally:
the pre-fix script from `ai_main` was run against the live prefixed project and
reproduced the original `[ERROR] ... rc/v2.11.1 does not exist` with exit 1, while
the RC's script exits 0 with `[OK] Active RC v2.11.1 has matching local branch:
ai_rc/v2.11.1`. All five of BUG-0193's acceptance criteria were exercised
independently against synthetic projects in throwaway kanban roots — the prefixed
OK/ERROR/WARN arms, the extra-branch WARN arm, and the unprefixed no-regression
arms — and all pass. Both gated runners exited 0 (unit 3321 passed / 3 skipped;
integration 366 passed / 1 skipped, including the six new cases). The requirements
doc's two generic criteria also pass: 348 Python files compile and 241 shell files
pass `bash -n`. Two defects were found outside the acceptance criteria and filed
via Path C: the new prefixed glob makes Check e blind to an *unprefixed* orphan
`rc/*` branch on a prefixed project — coverage the check had before this RC
(BUG-0197, regression, low) — and two further prefix-blind RC-branch call sites
remain elsewhere in the tree, handed off by CODER as out of scope for the ticket
(BUG-0198, pre-existing, low). Neither is severe; both are hygiene the next loop
heals.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

low

## Test Suites (authoritative gated runners)

| Suite | Command | Exit | Result |
|---|---|---|---|
| Unit | `bash team/scripts/run-unit-tests.sh --coverage` | **0** | 3321 passed, 3 skipped (383.67s) |
| Integration | `bash team/scripts/run-integration-tests.sh --coverage` | **0** | 366 passed, 1 skipped (245.06s) |

PASS evidence is the gated-runner exit codes above, not a direct-pytest line. Both
runners completed their quality-class lint pre-flight (non-fatal findings recorded
below), their bare-`/tmp` residue check (OK), and their TCP-listener leak check (OK).

The RC's new test file ran inside the gated integration suite:
`tests/integration/test_verify_rc_state_check_e_branch_prefix.py ......` — six cases,
all passed. The synthetic release simulation
(`tests/integration/test_cm_release_synthetic.py`, 15 cases) is also green.

## Code Coverage

- Unit:        line 74%  |  branch 61%   (14228 stmts, 4350 branches)
- Integration: line 8%   |  branch 6%

Read from `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/{unit,integration}.json` `totals`
(`covered_lines`/`num_statements`, `covered_branches`/`num_branches`; field names
confirmed against the generated files). The blended `TOTAL` line in the captured
output reads 71% / 7% respectively — that is the statement-plus-branch figure, not
line coverage. Coverage is informational only and does not enter the recommendation.

## Findings

### Finding 1: BUG-0193 acceptance criterion 1 — prefixed project exits 0 with a prefixed OK line

**Category:** pass

Ran the RC's script against the live self-build project (`branch_prefix = ai_`,
`## Active RC` = `v2.11.1`, branch `ai_rc/v2.11.1` present). `verify-rc-state.sh` is
read-only — grepped for write operations before running it against the live root;
it performs none.

```
$ bash team/scripts/verify-rc-state.sh pgai-agent-kanban --verbose
--- Check e: release-state.md <-> git branch consistency ---
[OK]    Active RC v2.11.1 has matching local branch: ai_rc/v2.11.1
verify-rc-state: 0 errors, 14 warnings, 38 ok
$ echo $?
0
```

Exit 0 and the exact `[OK]` wording the bug's acceptance criterion names. The
14 warnings are all historical `PRIORITY backlog [x] ... already shipped` notes —
benign, unrelated, present before this RC.

**Original failure mode re-synthesized.** The pre-fix script
(`git show ai_main:team/scripts/verify-rc-state.sh`) run against the same live
project reproduces the reported defect exactly:

```
[ERROR] release-state.md Active RC=v2.11.1 but local branch rc/v2.11.1 does not exist in $HOME/develop/pgai-agent-kanban
verify-rc-state: 1 errors, 14 warnings, 32 ok   (exit 1)
```

Old case broken, new case fixed — both verified, not inferred.

### Finding 2: BUG-0193 acceptance criteria 2-4 — guard arms fire correctly (behavioral)

**Category:** pass

Six synthetic projects were built under `$PGAI_AGENT_KANBAN_TEMP_DIR` (throwaway
kanban roots and git repos, never the live install) and the RC's script run against
each. Every arm was triggered deliberately, including the failure paths:

| Case | Fixture | Expected | Observed |
|---|---|---|---|
| 1 | prefix `ai_`, Active RC `v9.9.9`, no branch | ERROR naming `ai_rc/v9.9.9`, exit 1 | `[ERROR] ... local branch ai_rc/v9.9.9 does not exist`, exit 1 |
| 2 | prefix `ai_`, Active RC `none`, stray `ai_rc/v0.0.1` | WARN naming the branch | `[WARN] ... local branch exists: ai_rc/v0.0.1`, exit 0 |
| 3 | no prefix, Active RC `v1.0.0`, branch `rc/v1.0.0` | OK, exit 0 | `[OK] Active RC v1.0.0 has matching local branch: rc/v1.0.0`, exit 0 |
| 4 | no prefix, Active RC `v9.9.9`, no branch | ERROR naming `rc/v9.9.9`, never `ai_rc/` | `[ERROR] ... local branch rc/v9.9.9 does not exist`, exit 1 |
| 5 | prefix `ai_`, Active RC `v2.0.0` + stray `ai_rc/v1.0.0` | OK + extra-branch WARN | both lines present, exit 0 |
| 6 | prefix `ai_`, Active RC `none`, no branches | OK naming the prefixed glob | `[OK] Active RC is none and no local ai_rc/* branches exist` |

Cases 3, 4 and 6 are the no-regression evidence for unprefixed projects
(`pp_prefix_branch` is identity on an empty prefix). Case 5 exercises the
extra-branch comparison the diff also changed.

### Finding 3: BUG-0193 acceptance criterion 5 — test coverage exists and runs

**Category:** pass

`team/tests/integration/test_verify_rc_state_check_e_branch_prefix.py` (284 lines,
new) covers six cases against a synthetic git repo and a temporary kanban root built
from `tmp_path`; it invokes the script by subprocess with
`PGAI_AGENT_KANBAN_ROOT_PATH` repointed at that temp root, never the live install
(test-fidelity checklist items 2 and 6 satisfied). It exercises the real bash entry
point rather than a Python proxy (item 1). Read fully; the assertions match the
behaviour independently observed in Finding 2, and the unprefixed cases are asserted
explicitly rather than left to inference — including
`assert "ai_rc/v9.9.9" not in error_lines[0]`, which is the assertion that would
catch an over-eager prefix. No bare `/tmp` paths, no provenance references.

### Finding 4: Requirements-doc criteria — compile and syntax

**Category:** pass

- `python3 -m py_compile` over all 348 tracked `.py` files: clean.
- `bash -n` over all 241 tracked `.sh` files: clean, 0 failures.
- Each bundled item addressed: the single item (BUG-0193) is addressed — see
  Findings 1-3.

### Finding 5: Check e no longer surfaces an unprefixed orphan RC branch on a prefixed project

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (a read-only advisory check loses one class of warning; nothing in the release path depends on it)
**Regression:** yes (the pre-fix script warns on this fixture; the RC's script does not)
**Integrity Damage:** no
**Filed As:** BUG-0197 (Path C-A — filed as BUG: code defect / regression / test failure)

The fix replaced Check e's `git branch --list 'rc/*'` with a single
`<prefix>rc/*` glob. On a prefixed project that glob cannot match a leftover
*unprefixed* `rc/vX.Y.Z` branch, so the check now emits a positive all-clear where
it previously warned. Verified both ways on the same synthetic fixture
(prefix `ai_`, Active RC `none`, branch `rc/v0.0.9` present):

```
RC script : [OK]    Active RC is none and no local ai_rc/* branches exist   (exit 0)
ai_main   : [WARN]  release-state.md shows Active RC=none but local branch exists: rc/v0.0.9
```

This is inside the spirit of BUG-0193 (which complained the old glob was "a false
negative in that direction and a false positive in the other") but outside its
written acceptance criteria, which named only `<prefix>rc/*` for this arm. It
matters because `verify-rc-state.sh` is the post-unwind safety net
`docs/OPERATIONS.md` points operators at, and a false all-clear there is worse than
a noisy warning. It is not hypothetical on this tree: an unprefixed `main` still
sits beside `ai_main` in the dev tree, so unprefixed leftovers do occur here. Low
severity — the branch is inert, the check is advisory, and the fix is to widen the
listing rather than to change any resolution logic.

### Finding 6: Two RC-branch call sites elsewhere in the tree remain prefix-blind

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (one inert warning and one auto-detection fallback; neither is on the release path)
**Regression:** no (both call sites are untouched by this RC's diff, which names only `verify-rc-state.sh` and one new test file; the defect predates v2.11.1)
**Integrity Damage:** no
**Filed As:** BUG-0198 (Path C-A)

Surfaced by the CODER hand-off note on `CODER-20260908-014` and confirmed by
reading both sites:

- `team/pgai_agent_kanban/ops/write.py:1325`, `_warn_if_merged()` — lists RC
  branches with a hardcoded `"rc/*"`. On a prefixed project that list is always
  empty, so the "re-running this task will produce a second merge commit" warning
  can never fire. That is the same duplicate-merge-commit class this project has
  filed before (BUG-0183, BUG-0192), and the guard against it is inert here.
- `team/scripts/cleanup-stale-artifact-only-branches.sh:200` — RC auto-detection
  hardcodes `'ai_rc/*'` with a bare `'rc/*'` fallback. It works for `ai_` and for
  unprefixed projects and fails closed (exit 3, remedy names `--rc`) for any other
  prefix, so it is the milder of the two, but it is still one project's prefix
  hardcoded in shared tooling.

Both are out of scope for the v2.11.1 ticket by its own brief; filed so the next
loop picks them up rather than losing the hand-off.

## Lint Findings (non-blocking)

- `comment_provenance` — 1064 quality-class instances across the scanned tree
  (`team/scripts`, `team/scripts/lib`, `team/scripts/cm`, `team/pgai_agent_kanban`,
  `team/tests`, `release-notes`); comments and docstrings naming internal bug/task
  IDs. **Zero instances in either file this RC touched** (checked by filtering the
  findings detail for `verify-rc-state` and
  `test_verify_rc_state_check_e_branch_prefix` — no hits). Cites **BUG-0191**
  (open) — the standing test-tree provenance backlog. No duplicate filed.
- `gate_census_compliance` — 2 quality-class instances, both reported in the unit
  runner's findings file: `team/scripts/lib/wake_common.sh:130` (snippet
  `command -v python3 ... exit 1`) and `team/scripts/run-unit-tests.sh:591` (snippet
  `exit 1`), each missing `census_classification` and `dual_channel_contract`.
  Cites **BUG-0196** (open), which names these two sites at exactly these lines and
  diagnoses them as stale `CENSUS-LOCATOR-INDEX` line numbers rather than changed
  refusals. No duplicate filed.

Both findings files were present and parsed
(`${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-unit.json`, 3 findings;
`findings-integration.json`, 1 finding — one lint class each after aggregation).
Per Step 5c these do not move the recommendation or the systemic risk.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=1d freshness=stale — the last lane run was red on the claude-abliterated flavor (a Dockerfile FROM parse error under both engines) and on podman/rocky9; it is stale because it exercised v2.9.20, not the v2.11.1 under verification, so nothing is known about whether this tree's images build; cites BUG-0167, filed by that deep-verify run and since marked done, with no lane run after the fix to confirm it.

The cold-build lane was not run during this verification, per Step 5d — no container
build was attempted and no container engine was invoked. The tree does ship
Dockerfiles (`docker/claude-abliterated`, `docker/debian`, `docker/rocky9`) and
`team/scripts/deep-verify.sh` is present, so the freshness line is mandatory and the
"images ship with no cold-build gate" gap does not apply. Per Step 5d this staleness
is context the rating is made with, not a finding: it does not raise systemic risk
and does not move the recommendation.

## Autonomous criterion

Manual interventions required: none.

1. **Tasks force-promoted from WAITING to BACKLOG manually?** NO. `TESTER-016`'s
   incoming status summary reads "Promoted from WAITING to BACKLOG by wake script:
   prerequisites satisfied"; `CM-018` is still `WAITING` with marker `[W]` awaiting
   this task.
2. **`release-state.md` edited outside the CM scripts?** NO. Its mtime (08:45) matches
   `## RC Opened At` = `2026-09-08T08:45:42+00:00` and `## RC Opened By Task` =
   `CM-20260908-015-open-rc-2-11-1`; contents are internally consistent and
   `## Last Released` = `v2.11.0` matches `pp_last_released_version`.
3. **Scripts patched mid-build?** NO. `ai_main..ai_rc/v2.11.1` contains exactly two
   commits, both authored by the CODER task's own feature-branch work at 09:03 and
   its `--no-ff` merge; no operator commit landed on the RC.
4. **Queue files manually edited?** NO. Markers agree with task states
   (`[x]` DONE, `[W]` WAITING, `[A]` active), and Check c (status.md to queue marker
   consistency) reports 0 errors on the live project.

## Additional checks

- **Pre-flight merge verification (Step 2a):** passed. No DONE task in the project
  has a live feature branch that is unmerged or zero-commits-ahead; all
  `ai_feature/*` branches are cleaned up post-merge as expected.
- **Worktree identity (Step 3):** `git rev-parse HEAD` == `git rev-parse ai_rc/v2.11.1`
  == `d40e7b24faec2af175038e07cfbfaf4bcecfe41a`; `git status --short` clean.
- **Stash check (Step 3a):** `git stash list` empty.
- **Release-state drift (Step 6.1):** `pp_last_released_version` = `v2.11.0`;
  `semver_gt v2.11.1 v2.11.0` true; `## Last Released` is well-formed `vX.Y.Z`. No drift.
- **Empty-commit check (Step 6.11):** neither RC commit is empty — the BUG-0183 /
  BUG-0192 stray-empty-commit pattern did not recur in this RC.
- **Skip-cites-real-bug (Step 6.13):** no placeholder `BUG-SKIP*` skip annotations;
  every cited `BUG-NNNN` resolves to a file in `bugs/`. The only grep hits are archived
  runner logs under `team/tests/artifacts/` that quote the lint's own output — not skips.
- **Coding standards (Step 6.14):** the diff adds no new script, no new hardcoded
  operational value, and no duplicated `lib/` operation; it removes a hardcoded
  branch-name construction in favour of the existing shared helper, which is the
  directive working as intended. The new test file's `v2.11.1` / `ai_rc/v2.11.1`
  strings are fixture data, not provenance references, and the provenance lint does
  not flag the file.
- **Prereq dependency graph (Step 6.10):** acyclic; all referenced task IDs exist;
  bookends correct (`CM-015` open, `CODER-014`, `TESTER-016`, `CM-018` release).
  Sequence number 017 is unused, and `CM-015` (open-RC) carries a higher sequence
  than `CODER-014` which depends on it — both cosmetic, neither affects promotion,
  which is prerequisite-driven.
- **Synthetic release simulation (Step 6.8):** the trigger condition is arguably not
  met (`verify-rc-state.sh` is a read-only checker and does not participate in the
  open/close cycle), but `test_cm_release_synthetic.py` ran green in the gated
  integration suite regardless — 15 cases passed.

## Caveats

- The two filed bugs are both `low`. Neither prevents an operator from using the
  release; v2.11.1 is a strict improvement over v2.11.0 for the case BUG-0193
  described, and BUG-0197 records the narrower case the fix did not cover.
- The deep-verify ledger's most recent record is red at v2.9.20 and BUG-0167 (the bug
  that record filed) is now `done`, but no lane run has happened since the fix, so
  the red record stands as the last word. Per Step 5d this is reported, not re-filed
  and not blocking; a DEEP-VERIFY run before the next public tag would replace it.
- Verification ran in the detached worktree at
  `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260908-016-verify-2-11-1`.
  Every ad-hoc script invocation that could touch kanban state used a throwaway
  `PGAI_AGENT_KANBAN_ROOT_PATH` under `$PGAI_AGENT_KANBAN_TEMP_DIR`; the only command
  pointed at the live root was the read-only `verify-rc-state.sh`, whose read-only
  nature was confirmed by inspection first.
