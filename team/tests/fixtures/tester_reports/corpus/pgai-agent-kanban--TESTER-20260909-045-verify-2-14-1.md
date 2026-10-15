# Verification Report: ai_rc/v2.14.1

**Task:** TESTER-20260909-045-verify-2-14-1
**RC branch / commit:** `ai_rc/v2.14.1` @ `23504c47682e425993fff2c9c2786d1642ec8413`
**Base:** `ai_main` @ `e55cc3e` (last released `v2.14.0`)
**Requirements doc:** `projects/pgai-agent-kanban/requirements/v2.14.1-bugfix-bundle-20260909.md`
**Bundled item:** BUG-0217-readme-config-section-stale
**Worktree:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260909-045-verify-2-14-1` (detached HEAD, clean, no stashes)
**Verified:** 2026-09-09

## Summary

v2.14.1 is a single-item documentation bundle: BUG-0217, the stale
`## Configuration` section in `team/README.md`. The RC's entire source delta
against `ai_main` is that one file (+12/-8 lines, +1603 bytes). Every
acceptance criterion the bug and the WRITER ticket set is met, including the
operator-facing half — a fully sandboxed `install.sh` + `upgrade.sh` run
confirms the deposited `$KANBAN_ROOT/README.md` now carries the corrected
text rather than overwriting a correct README with a stale one. The
integration suite is green. The unit suite is **not**: the README's growth
pushed the PM role's assembled governance reading pack 1440 bytes past its
configured byte budget, and the test that enforces those budgets fails.
That failure was introduced by this RC — the same lint reports every role
`under` on `ai_main` — and is filed as BUG-0220. It has no runtime effect
(the budget lint narrates and exits 0; nothing truncates an over-budget
pack), but it leaves the authoritative gated unit runner red. The release
itself is usable; the defect is one config value and its test.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

high

Rated against the Systemic-Risk Rubric in `team/SOP.md`. It meets the first
`high` condition literally and by evidence: **this RC introduces a
regression** — `bash team/scripts/run-unit-tests.sh` exited 0 on the last
shipped version and exits 1 on this one, and the cause is this RC's own
change. It meets none of the other three conditions: chain integrity is not
at risk, no data is at risk, and the loop is healing (the fix is one config
key, derivable mechanically from the headroom policy already written into
`kanban.cfg_example`). The rating is written honestly rather than softened;
the per-finding `Fix Effort`, `Regression`, and `Integrity Damage` fields
below are what the ship-policy matrix reads alongside it.

## Findings

### Finding 1: BUG-0217 content criteria — team/README.md Configuration section

**Category:** pass

Every acceptance assertion from BUG-0217 and from the WRITER ticket, run
against the RC worktree:

| Check | Expected | Actual |
|---|---|---|
| `grep -c 'five configuration files' team/README.md` | 0 | 0 |
| `grep -c 'team/templates/kanban.cfg.example' team/README.md` | 0 | 0 |
| Table rows for `shell-env`, `claude-env`, `secrets` | present | present (lines 177, 178, 179) |
| `env` / `bashrc` rows describe them as legacy only | yes | yes (lines 182, 183: "Legacy compatibility only — superseded by …") |
| Every template path named resolves to a real file | all 6 | `kanban.cfg_example`, `shell-env_example`, `claude-env_example`, `secrets_example`, `projects.cfg_example`, `project.cfg_example` — all present at tree root |
| No second full statement of the four-concerns law | pointer only | one line (185) pointing at `HOW_TO.md` section 1.2; that section exists at `HOW_TO.md:79` under exactly that title |
| `docs/OPERATIONS.md` referenced by the closing paragraph exists | yes | yes |
| `git diff --name-only ai_main..HEAD` | `team/README.md` only | `team/README.md` only |

The section was read in full, not grepped. The rewritten table lists four
current operator-owned root files plus `projects.cfg` and per-project
`project.cfg`, then `env` and `bashrc` as legacy — the same surface
`docs/OPERATIONS.md` "Configuration File System" and `HOW_TO.md` section 1.2
describe. The closing paragraph was brought along in the same pass (the
ticket's stated intended scope) and now describes what `install.sh`
auto-seeds and what is opt-in, instead of the `config.cfg` -> `kanban.cfg`
migration history.

### Finding 2: BUG-0217 operator-facing half — the deposit (behavioral, Step 6.5)

**Category:** pass

The half of BUG-0217 with operator consequence is that `install.sh` and
`team/scripts/upgrade.sh` deposit `team/README.md` into `$KANBAN_ROOT/README.md`
and `README.md` is not in `upgrade.sh`'s `PROTECTED_FILES`, so an upgrade was
replacing correct operator-facing text with stale text. Both halves of the
guard were exercised, per the anti-pattern "verify only the new normal case":

- **Original failure mode reproduced:** `git show ai_main:team/README.md | grep -c 'five configuration files'` -> `1`. The pre-fix tree does deposit the stale text.
- **New normal case verified:** sandboxed `install.sh` then `upgrade.sh --force --no-backup --wake-tier none` into a throwaway root created with `mktemp -d -p "$PGAI_AGENT_KANBAN_TEMP_DIR"` (never the live root; `HOME` and `CLAUDE_CONFIG_DIR` redirected into the sandbox). Both exited 0. On the deposited `$SB/root/README.md`: `grep -c 'five configuration files'` -> **0**, `grep -c 'team/templates/kanban.cfg.example'` -> **0**, and the `shell-env` / `claude-env` / `secrets` rows are present at lines 177-179.

### Finding 3: PM governance byte budget exceeded — unit suite red

**Category:** bug
**Fix Effort:** small (1 CODER task — one config key and its comment)
**Systemic Risk:** high (regression: the authoritative unit runner exited 0 on v2.14.0 and exits 1 here; see the Systemic-Risk Rubric in `team/SOP.md`)
**Regression:** yes (introduced by this RC's own change; the identical lint reports every role `under` on `ai_main`)
**Integrity Damage:** no (no queue, lockfile, task state, or data is touched; the budget lint narrates and exits 0, and nothing truncates an over-budget governance pack)
**Filed As:** BUG-0220-pm-governance-budget-over (Path C-A — filed as BUG — code defect / regression / test failure)

`bash team/scripts/run-unit-tests.sh --coverage` exits **1**:

```
FAILED tests/unit/test_composer_governance_byte_budget.py::test_every_role_governance_assembly_stays_under_configured_budget
AssertionError: role(s) not under their configured governance budget: pm (assembled=75168 budget=73728 verdict=OVER)
1 failed, 3594 passed, 3 skipped in 449.27s
```

`team/read-order/pm.txt` names `README.md` as one of the five files in PM's
governance pack. This RC grew that file by 1603 bytes; PM's assembled total
went from 73565 (163 bytes under its 73728-byte budget) to 75168 (1440 over).
Confirmed both directions:

```
# RC:      bash team/scripts/lint_governance_byte_budgets.sh --kanban-cfg kanban.cfg_example
pm  75168  73728  OVER      (all six other roles under)
# ai_main (git archive extracted to a sandbox under the framework temp root):
pm  73565  73728  under     (all seven roles under)
```

Not classified `stale-assertion`: the literal the test rests on is
`budget_bytes_pm` in `kanban.cfg_example`, which this RC did **not** change,
and the correct fix is to re-baseline that config value per the HEADROOM
POLICY already documented above the key — not to edit a test literal. Per
Step 7 rule 5, ambiguity defaults to real-failure; this is not even
ambiguous.

Impact is bounded and worth stating precisely. The lint itself is
narrating-only (`exit 0` regardless of verdict, by design — see the
`[governance]` comment block in `kanban.cfg_example`), and
`_wd_build_governance_line` in `team/scripts/lib/wake_driver_invoke.sh`
performs no truncation or refusal, so no agent's runtime behaviour changes
and the shipped release is fully usable. What is damaged is the
certification signal: the gated unit runner is the one authoritative
pass/fail source, and while it is red no subsequent RC can earn a PASS on
the strength of the suites, and the next genuine unit failure arrives beside
a known-red test.

Third occurrence of the class (BUG-0174, BUG-0199, both `done`), each closed
by re-baselining rather than by catching growth at authoring time.
BUG-0220 names the mechanical remedy (`budget_bytes_pm = 81920`, derived from
the file's own 4KB-block rule) and flags that `overwatch` now has only 1316
bytes of headroom.

### Finding 4: Integration suite

**Category:** pass

`bash team/scripts/run-integration-tests.sh --coverage` exited **0** —
`424 passed, 1 skipped in 385.49s`. Post-suite cleanliness gates both OK
(`bare_tmp_cleanliness`, `listener_cleanliness`).

### Finding 5: Pre-flight merge verification (Step 2a)

**Category:** pass

No DONE task in `projects/pgai-agent-kanban/tasks/` has a live feature branch
(`git branch --list 'feature/*'` and `ai_feature/*` are both empty) — normal
post-merge cleanup per CODER/WRITER Procedure A Step 8. Check 1 and Check 2
skip every task on the documented missing-branch rule. The cycle's only
authoring task, `WRITER-20260909-043-readme-config-section`, is merged:
`23504c4` is the `--no-ff` merge of its branch, and its content commit
`3b2e9e6` carries the README change.

### Finding 6: Worktree and RC identity (Steps 3, 3a)

**Category:** pass

`git rev-parse HEAD` = `git rev-parse ai_rc/v2.14.1` =
`23504c47682e425993fff2c9c2786d1642ec8413`. `git status --short` empty.
`git stash list` empty.

### Finding 7: release-state.md drift check (Step 6.1)

**Category:** pass

`pp_last_released_version pgai-agent-kanban` -> `v2.14.0`;
`semver_gt v2.14.1 v2.14.0` -> true. `release-state.md` holds
`Active RC: v2.14.1`, `RC Opened By Task: CM-20260909-044-open-rc-2-14-1`,
and a well-formed `## Last Released: v2.14.0`. The branch `ai_rc/v2.14.1`
named by `Active RC` exists. No drift.

### Finding 8: RC commit hygiene (Step 6.11 spot-check)

**Category:** pass

Two commits above `ai_main`: one content commit and its `--no-ff` merge.
Neither is empty (`git show --name-only` non-empty for the content commit);
the RC does not repeat the stray-empty-commit pattern of BUG-0219. No
secrets, tokens, or credentials appear anywhere in the diff.

### Finding 9: Skip-cites-real-bug gate (Step 6.13)

**Category:** pass

No placeholder skip IDs and no dangling `BUG-NNNN` citations. The only hits
for the placeholder pattern are inside archived runner logs under
`team/tests/artifacts/coverage-*/`, which contain the literal string
`BUG-NNNN` as lint narration, not skip annotations. Every cited ID resolves
to a file in `bugs/` (zero `MISSING:` lines).

### Finding 10: Prerequisite dependency graph (Step 6.10)

**Category:** pass

`CM-20260909-044-open-rc-2-14-1` (none) -> `WRITER-20260909-043-readme-config-section`
-> `TESTER-20260909-045-verify-2-14-1` -> `CM-20260909-047-release-2-14-1`
(depends on both WRITER-043 and TESTER-045). Acyclic, every referenced task
ID exists on disk, lifecycle bookends correct, sequence numbers 043/044/045/047
non-colliding. Queue markers agree with task state (`[A]` for this task,
`[W]` for the waiting release task).

### Finding 11: Constraint and coding-standards checks (Steps 6.8, 6.9, 6.14)

**Category:** pass

- Step 6.8 (synthetic release simulation) — **skipped with note**: the diff touches no release-lifecycle file. `git diff --name-only ai_main..HEAD` is `team/README.md` alone; no `cm-release.sh`, `cm-open-rc.sh`, `cm-cancel-rc.sh`, `project_paths.sh`, or any `team/scripts/` path.
- Step 6.9 — no forbidden references introduced; the diff is prose in one markdown file, no scripts changed, so no `set -euo pipefail` / trap / query-parameterization surface is touched.
- Step 6.14 — no new or modified script, no new production string, no new hardcoded operational value, no duplicated `lib/` implementation. The RC's one changed file names no bug ID, task ID, or version-bundle slug (directive 8 clean).

## Code Coverage

Read from the `totals` object of the runner-written JSON reports
(`percent_covered` / `percent_branches_covered` field names confirmed against
the generated files, not assumed).

- Unit:        line 74.3%  |  branch 61.1%   (14255 stmts, 4366 branches)
- Integration: line 7.8%   |  branch 6.0%

Informational only; coverage does not enter the PASS precondition and is not
a finding. (For reference, the blended `^TOTAL` figures in the captured
output are 71% unit and 7% integration — the blended number is why the two
labeled values above are reported separately.)

## Lint Findings (non-blocking)

- `comment_provenance` — `team/scripts/lib/wake_bootstrap.sh:359`: `[bare-release-slug]` provenance reference `v2.14.0-provider-runtime-hardening` in a shipped code comment — cites BUG-0218-new-wake-code-cites-release-slug (open). One instance, reported identically by both gated runners; no duplicate filed. The line is untouched by this RC (the diff is `team/README.md` only), so it is carried from v2.14.0, which is exactly what BUG-0218 already describes.

No other quality-class findings: `findings-unit.json` and
`findings-integration.json` each contain exactly this one finding.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=2d freshness=stale — the last lane run is 2 days old, inside the 7-day window, but it exercised v2.9.20 rather than the v2.14.1 under verification, so it says nothing about this tree; its `claude-abliterated` Dockerfile `FROM` parse failure and `rocky9` COPY failure were filed by that run as BUG-0167-cold-build-claude-abliterated-failure, since closed (`## Status: done`), and are not re-filed here.

## Autonomous criterion

Manual interventions required: none.

1. **Tasks force-promoted from WAITING to BACKLOG by hand?** NO. This task's `status.md` records the wake script's own promotion ("Promoted from WAITING to BACKLOG by wake script: prerequisites satisfied"); no other task in the cycle shows an out-of-band state edit.
2. **`release-state.md` edited outside the CM scripts?** NO. Its mtime (2026-09-09 14:33) matches `RC Opened At: 2026-09-09T14:33:09+00:00` and the CM-044 task's own DONE summary reporting `cm-open-rc.sh` exit 0.
3. **Scripts patched mid-build?** NO. The RC contains exactly two commits above `ai_main`, both from WRITER-043, touching only `team/README.md`. Nothing under `team/scripts/` or `subagents/` was committed while the RC was in flight.
4. **Queue files hand-edited?** NO. Queue mtimes form a consistent automated sequence across the cycle (bug_backlog 14:27 bundling -> pm_backlog 14:31 -> cm_backlog 14:33 RC open -> writer_backlog 14:38 -> tester_backlog 14:39), each matching the corresponding agent's recorded activity.

## Caveats

- **The live install root is many releases behind the tree it is verifying.** `$PGAI_AGENT_KANBAN_ROOT_PATH/VERSION` reads `v2.9.19` while the dev tree ships v2.14.x, so the governance and role files the wake layer loads at runtime are v2.9.19 text. One concrete consequence hit during this run: `roles/TESTER.md:470` and `roles/PM.md:88` on the installed root still source helpers through `$PGAI_AGENT_KANBAN_ROOT_PATH/team/scripts/lib/...`, a directory an installed root does not have, so the ledger and project-paths helpers fail with `command not found` (PM recorded the same failure in its own DONE summary this cycle). **This is not a defect in the RC** — the RC's own `team/roles/*.md` contain no `team/`-prefixed source lines at all; BUG-0187 fixed that and the fix is present in the shipped tree. It is drift between the operator's installed root and the dev tree, healed by upgrading the install, and no bug is filed against the tree for it. Recorded here because it makes several role-file command lines unrunnable verbatim for every agent in the current chain.
- **`overwatch` is one paragraph from the same failure as Finding 3**: 72412 assembled against a 73728 budget, 1316 bytes of headroom. Noted inside BUG-0220 so a fix that re-baselines only `pm` does not leave the next governance edit to trip the same test through a different role. Observation, not a separate finding.
- **Test fidelity**: the RC's change is prose in one markdown file with no test surface of its own, so the fidelity checklist has no subject here. The test that failed (`test_composer_governance_byte_budget.py`) exercises the real shipped lint against the real checked-in `kanban.cfg_example` rather than a fixture, which is the high-fidelity shape — it is why the growth was caught at all.
- **Prior-run artifacts**: none. The task's `artifacts/` directory was empty at start (fresh run); no `.previous-RUN-N` files exist, so nothing in this report is inherited from an earlier verdict.
