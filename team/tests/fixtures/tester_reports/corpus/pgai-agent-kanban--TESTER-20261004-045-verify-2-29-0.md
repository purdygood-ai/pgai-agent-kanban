# Verification Report: rc/v2.29.0 (ai_rc/v2.29.0 @ d56655cb)

Verified RC v2.29.0 against `requirements/v2.29.0-overwatch-block-triage.md`: 9 deliverables, Enforcement Class, Registration Table, Lifecycle, Host Matrix, and AT1-AT13. All 13 implementing tasks (CODER-031..042, WRITER-043) are merged `--no-ff` into the RC, which is intact and clean. The feature works as specified. Every negative case the requirement singles out (AT4c, AT6 residue with commits, AT7, AT8 invalid, AT9), plus AT2-AT5, AT8 valid, AT11-AT13 and the `block_triage=false` mode, was exercised end to end in my own sandbox roots against throwaway git repos, with production-shape Block Records from the real writer. Every one behaved as specified, and no ref was ever moved.

Four defects were found:
- **Most serious:** a new unit test makes the gated unit suite try to push every local release tag (154) of the dev tree to its real GitHub origin. The agent git-boundary guard refused all 1,848 attempts in this cycle, but nothing stops them outside a guarded non-CM wake.
- The unit runner is red (1 failure, a new module).
- Two smaller gaps: the nudge skips pre-dispatch blocks, and agents are never taught `--reason-code`.

## Recommendation
SHIP-WITH-SERIOUS-CONCERNS

## Systemic Risk
high

## Findings

### Finding 1: New unit test drives check-push-lag to push the dev tree's release tags to the real origin

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** high (a test-isolation hole into the live install and a real remote; the conftest live-write fixture does not cover `PGAI_DEV_TREE_PATH`, and the gated runner exports it)
**Regression:** yes
**Integrity:** destructive
**Severity:** high
**Filed As:** BUG-0548 (Path C-A, filed as BUG — code defect / regression / test failure)
**Filed By:** TESTER (autonomous Path C) — a new unit test runs the whole OVERWATCH sweep, so the push-lag check tries to push every local release tag of the real dev tree to the real GitHub remote whenever the unit suite runs.

Traces to: requirement "Host Matrix" ("no fixture touches the live install"); Step 6.12 (5); SOP-core "Git Repositories" (only CM touches a remote).

`team/tests/unit/test_overwatch_block_triage.py::test_sweep_runner_discovers_and_runs_block_triage` (added by CODER-037) runs `overwatch-sweep.sh` with no `--only`. That reaches `check-push-lag.sh`. With no `dev_tree_path` in the sandbox `project.cfg`, the check falls back to the inherited `PGAI_DEV_TREE_PATH`, which `run-unit-tests.sh:167` exports from `kanban.cfg` (`$HOME/develop/pgai-agent-kanban`). It treats the sandbox's absent `push_to_remote` as true, runs `ls-remote --tags origin`, and then `git push origin refs/tags/<tag>` for each tag the remote lacks.

Evidence:
- CODER-037..042 guard logs: 1,694 refused `git -C $HOME/develop/pgai-agent-kanban push origin refs/tags/ai_v2.x.y` (154 distinct tags per burst). The prior cycle had only fixture-repo pushes.
- My own mandated `run-unit-tests.sh --coverage`: 154 refused records in this task's `logs/git-boundary-guard.jsonl`.
- Reproduced in isolation with `team/tests/fixtures/git_network_stub.py` and a throwaway dev tree whose origin is a local bare repo: the stub logged `ls-remote --tags origin` then `push origin refs/tags/ai_v9.0.0` and `ai_v9.0.1`.

The integration suite made no such attempts.

Why high and destructive, argued in prose: the project is configured `push_to_remote = false` and its origin is GitHub. Outside a guarded non-CM agent wake (an operator certifying from a shell, or any `cm`-role context, where the guard lets pushes through), the certification command would publish up to 154 local-only tags. I have no evidence that this has happened; every observed attempt was refused. The fix is small (scope the test with `--only block-triage` or a sandboxed `project.cfg`, and scrub `PGAI_DEV_TREE_PATH` in the conftest fixture). CM may weigh that, but the rating is the honest one per SOP-core "Rating a Finding".

### Finding 2: Gated unit runner red — new block_stage_deferral.py fails the package-wide by-path contract test

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated to one new module)
**Severity:** medium
**Filed As:** BUG-0551 (Path C-A)
**Filed By:** TESTER (autonomous Path C) — a new OVERWATCH module carries the run-by-file-path guard but imports nothing from the package at load time, so the package-wide by-path test fails and the unit suite is red.

Traces to: requirement Acceptance ("TESTER certifies the full suites once"); Step 5a.

`run-unit-tests.sh --coverage`: UNIT_EXIT=1 (lint pre-flight passed; pytest `1 failed, 8460 passed, 43 skipped`). The failure is `test_package_modules_run_by_path.py::test_file_load_binds_the_package_of_its_own_tree[pgai_agent_kanban.overwatch.block_stage_deferral]`, `KeyError: 'pgai_agent_kanban'`. `block_stage_deferral.py` (new, CODER-040) has the `if not __package__:` guard, so the test discovers it, but all its package imports are function-local, so loading it by file never binds the package. Deterministic: 2/2 direct-pytest re-runs, used as a debugging aid only. The test is unchanged by this RC, so this is not a stale assertion. No runtime impact; the cost is a red certification suite.

### Finding 3: --reason-code is taught nowhere an agent reads; SOP-core's "four fields and no others" is now false

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (documentation-discipline lapses of this shape recur whenever a tool gains a flag)
**Severity:** medium
**Filed As:** BUG-0550 (Path C-A)
**Filed By:** TESTER (autonomous Path C) — the status-writer tool now records a Block Record and accepts a reason code, but the shared operating procedure still says it touches only four fields and no agent-facing document mentions the new flag, so agents can never ask for the operator-data action triage was built to run.

Traces to: Deliverable 1 ("so agent self-blocks are classifiable"); SOP-core Documentation Discipline Rule.

`task-status.sh --state BLOCKED` now writes `## Block Record`, DONE/WONT-DO removes it, and `--reason-code` / `--action-args` exist. Both were verified behaviorally in a sandbox, with all refusals firing dual-channel. But `team/SOP-core.md` still says "It manages four fields and no others", and `grep -rln -- "--reason-code\|agent_operator_data_action"` over `team/roles`, `team/SOP-core.md`, `subagents` and `team/read-order` finds nothing. Every agent self-block will record `agent_unclassified` (always escalates), so the AT8 `run_allowlisted_verb` path is unreachable by any agent in practice.

### Finding 4: On-BLOCK nudge does not fire for blocks the wake makes before dispatch

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (delay only; the next sweep or overwatch wake triages the block)
**Severity:** low
**Filed As:** BUG-0549 (Path C-A)
**Filed By:** TESTER (autonomous Path C) — when the wake blocks a task before the agent runs (for example a failed worktree create), the chain never sets its "blocked a task" flag, so OVERWATCH is not nudged and the block waits for the next scheduled sweep.

Traces to: Deliverable 7 ("guarded by a 'this chain blocked a task' flag").

`_chain_blocked_a_task` is set only in `wd_finalize_task`. The pre-dispatch block sites in `wake_driver_worktree.sh`, `wake_driver_prep.sh` and `wake_driver.sh` mark `[B]` and return before finalize, so no nudge fires. One of them, `worktree_create_failed`, is an actionable triage class. The nudge itself was verified for the post-agent path: it fires after lock release, runs `--only block-triage --lock-wait 60`, the woken sweep waited through a held lock and acquired it, and no final refusal line appeared.

### Finding 5: Remaining deliverables and acceptance tests

**Category:** pass

- D1 Block Record at every listed site, plus the census lint (fires on seeded .sh/.py violations, CONVERT, wired into the unit runner).
- D2 resume-branch: commits kept; `branch -D` only in fresh mode; unknown mode handled as branch.
- D3 YAML table matches the requirement row for row.
- D4/D5 envelope and escalation, AT2, AT4a/b/c, AT5, AT6 (both), AT7, AT8 (valid and invalid), AT9, second-attempt escalations, and no re-attempt of an escalated epoch.
- D6 / AT11 storm decisions.
- D8 config precedence, with bash/Python parity.
- D9 docs plus docs manifest; Registration Table entries present.
- Lifecycle: kept across promote, removed at DONE/WONT-DO.
- AT13 HALT_OVERWATCH (flag and env), lock held, and the grep guard.
- 6.1, 6.8 (`test_cm_release_synthetic.py` passed in the integration run), 6.9, 6.10, 6.13. Details are in the Verification Log below.

### Finding 6: block_triage=false gives actionable blocks no Diagnosis

**Category:** pass-with-caveat

Deliverable 8 says "Block Records and diagnoses are still written". With `false`, escalation-class blocks get `Needs Human: yes` plus the Diagnosis. Actionable blocks are skipped with only a stderr line: no Diagnosis and no actions.log row. `docs/overwatch.md` documents exactly this behavior, and I read "diagnoses" as D5's escalation diagnoses. Under the other reading this would be a gap.

### Finding 7: typed action args canonicalise a bare version

**Category:** pass-with-caveat

`--action-args ... version=2.28.0` is accepted and stored as `v2.28.0`. The stored and acted-on value always matches `^v\d+\.\d+\.\d+$`, as the module docstring states.

## Lint Findings (non-blocking)

None. `findings-unit.json` and `findings-integration.json` both have `findings: []`. The block-record census lint reported 0 findings over 468 files.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=27d freshness=stale — the last cold build (claude-abliterated FROM parse error; rocky9 COPY --chmod step) was red and filed as BUG-0167, now done; the record is stale on both counts (27d exceeds the 7-day window, and v2.9.20 is not the v2.29.0 under verification), so nothing is known about whether this tree's images build cold.

## Gated Suites

| Suite | Exit | Result | Line coverage | Branch coverage |
|---|---|---|---|---|
| `run-unit-tests.sh --coverage` | UNIT_EXIT=1 | 1 failed, 8460 passed, 43 skipped (32m07s) | 79.6% | 65.6% |
| `run-integration-tests.sh --coverage` | INTEGRATION_EXIT=0 | 649 passed, 1 skipped (17m00s) | 13.1% | 8.9% |

Coverage comes from the `totals` of `coverage/unit.json` and `coverage/integration.json` (`percent_statements_covered`, `percent_branches_covered`) and is informational only. Output is in `/tmp/pgai_kanban_tmp/tester/v2.29.0/{unit,integration}.log`. PASS is not available because UNIT_EXIT is non-zero.

## Autonomous criterion

1. Tasks force-promoted WAITING to BACKLOG by hand: NO. Promotions read "by wake script", and no cycle task carries a non-zero Zero Progress or any Transient Retry/Block Record.
2. release-state.md edited outside CM scripts: NO. Values match CM-044's `open-rc.sh` run.
3. Scripts patched mid-build: NO. First-parent history of the RC is exactly the 13 task merges.
4. Queue files edited by hand: NO evidence. Markers are well-formed and consistent with task states.
5. Non-CM agent invoked a remote git operation: YES. 1,694 refused `push` records in CODER-037..042 and 154 in this task (all from my gated unit run, not from a manual command), plus unguarded `ls-remote --tags origin` reads. None were made deliberately by an agent; all were caused by the defect in Finding 1 (BUG-0548). The guard refused every push.

Autonomous operation criterion: Manual interventions required: none. The (5) YES records a defect, not a human intervention.

## Caveats

- status.md arrived as WORKING with an empty artifacts/ (a wake-stamped dispatch), so I treated it as a fresh start. There were no prior report.md / gaps.md or .previous-RUN-N files.
- Per the dispatch prompt and TESTER.md Step 9, artifacts were not committed to any branch. TESTER.md "Git workflow" says otherwise; the two sections conflict.
- I did not demonstrate Finding 1 against the real origin. The stub reproduction and guard records are the evidence; whether any past unguarded run actually published tags is unknown.
- During an AT12 probe the woken sweep printed one intermediate `lock.provenance.contended` message while waiting. This is noise, not a refusal.

## Verification Log (append-only checkpoints written during the run)

- Step 1: Fresh start. status.md read WORKING with empty artifacts/ and a wake-promotion Summary; no prior report.md or .previous-RUN-N files present. Treated as fresh.
- Step 2 pre-flight: 585 DONE task folders with a named Feature Branch scanned; none exists locally (normal post-merge shape) -> 0 anomalies. All 13 input tasks (CODER-031..042, WRITER-043) have a `--no-ff` merge commit on ai_rc/v2.29.0, each contributing 1-4 commits.
- Step 3 working tree: HEAD d56655cb == ai_rc/v2.29.0; `git status --porcelain` empty; `git stash list` empty. PASS.
- Step 4 diff: `git diff ai_main..ai_rc/v2.29.0` = 87 files, +18880/-1831.
- Step 6.1: `pp_last_released_version pgai-agent-kanban` = v2.28.4; `semver_gt v2.29.0 v2.28.4` true. release-state.md fields well-formed (Active RC v2.29.0, Last Released v2.28.4). VERSION file still v2.28.4 on the RC (CM stamps at release). PASS.
- D1 (Block Record): `block_record_write` is called at every requirement-listed site (wake_driver.sh missing_readme; wake_driver_prep.sh invalid_assignment/invalid_effort_config/invalid_state; wake_driver_worktree.sh 8 sites; wake_driver_finalize.sh per_task_timeout; wake_lifecycle.sh transient/gate_merge_not_found|gate_merge_evidence_not_found/gate_dirty_worktree/gate_empty_commit; overwatch_protocol.sh zero-progress cap -> provider_refusal|zero_progress; check-transient-api-error.sh). Census lint `lint_block_record_sites.py`: 468 files, 0 findings on the RC; seeded bad .sh and .py sites are reported (2 findings), good sites not; always exit 0 (CONVERT, matches Enforcement Class); wired into run-unit-tests.sh. PASS.
- D1 (task_status reason codes), sandbox root: unknown code refused (rc 1, nothing written); --reason-code with non-BLOCKED refused; agent_operator_data_action without --action-args refused; malformed bugs `BUG-1` refused; prose/unallowlisted verb refused; plain BLOCKED writes agent_unclassified record (owner from ## Role); DONE removes record. PASS (caveat: version `2.28.0` without the `v` is canonicalised to `v2.28.0` and accepted — deliberate liberal reading, stored value always matches `^v\d+\.\d+\.\d+$`).
- D2 (resume branch), sandbox clone: `create_task_worktree ... branch` attaches the existing branch (2 commits kept, tip identical); works after a worktree dir was rm'd without deregistration (prune); `BRANCH` (case) accepted; unknown mode `bogus` treated as branch (keeps commits); `fresh` deletes and recreates (0 commits) — `branch -D` only in fresh. Wake reads `resume:` via block_record_field and defaults to `branch` when unreadable. PASS.
- D3 table: block-classifier.yaml rows match the requirements table (codes, actions, max attempts, cooldowns 1800 / 900x2 cap 3600 / 1800 / 300x2 cap 1200 / 300, verifications, always-escalate list). PASS.
- D4/D5 + AT2/AT4/AT6/AT7/AT8(invalid)/AT9 end-to-end, own sandbox root + throwaway git repo (`check-block-triage.sh`, real Block Record writer for inputs):
  - `--dry-run`: 9 plans narrated, 0 task files written, 9 `block-triage-dry-run` rows.
  - AT4a branch already merged -> DONE, Needs Human no, Block Record removed, refs unchanged (reflog delta is only the separately-pruned worktree's HEAD log).
  - AT4b clean merge-tree -> BACKLOG, `resume: branch`, attempts 1, Triage Note. Re-block (second refusal) -> BLOCKED, NH yes, Diagnosis.
  - AT4c conflicting merge-tree -> stays BLOCKED, NH yes, Diagnosis names `c.txt`.
  - AT2 timeout, branch 2 ahead, worktree gone -> BACKLOG, `resume: branch`, attempts 1, cooldown +30 min, Triage Note "killed at 3600s", commits-ahead snapshot, status+queue backups, action-log row. Second timeout -> NH yes + Diagnosis, verification.json entry `failed`.
  - AT6 residue with unique commit -> stays BLOCKED, NH yes, worktree + branch intact. Stale residue with no unique commits -> worktree removed (no --force), BACKLOG.
  - AT7 source_branch_missing -> stays BLOCKED, NH yes.
  - AT8 invalid (bug not done) -> stays BLOCKED, NH yes, verb not run.
  - AT9 agent_unclassified -> stays BLOCKED, NH yes.
  - Already-escalated epochs skipped on the next sweep ("never re-attempts the same block epoch"). A verified requeue followed by a different block restarts the epoch.
  - All refs byte-identical before/after. PASS.
- AT13 grep guard: `test_block_triage_code_invokes_no_git_mutation_or_reset` scans 9 triage files, with seeded-violation self-tests. `readonly_git.run_readonly_git` refuses merge / branch -D / worktree add before spawning; `run_worktree_cleanup` refuses `--force`. PASS.
- AT13 safety refusals (sandbox): HALT_OVERWATCH flag file -> exit 0, all task files + refs unchanged, `block-triage-refused` row, loop stops; HALT_OVERWATCH env var -> same; repo lock held (flock) -> same, refusal row. PASS.
- D8 `block_triage=false` (sandbox, NH yes and NH no): escalation-class tasks still get Needs Human yes + Diagnosis; actionable tasks skipped; queue, READMEs, refs, worktrees byte-identical. Resolver: absent->true, kanban false->false, project true overrides kanban false, `yes` refused dual-channel (bash rc 1; Python raises InvalidBlockTriageValue) — bash/Python parity holds. Registered in config_loader Key Registry and kanban.cfg_example (`block_triage = true`), project.cfg_example override documented. PASS-WITH-CAVEAT: actionable blocks under `false` get no Diagnosis and no actions.log row (stderr narration only); docs/overwatch.md states exactly this semantics, so I read the requirement as met.
- AT8 valid (sandbox): restamp-fixed-in ran on a done bug with empty Fixed In, idempotence re-run "0 stamped, 1 skipped", evidence `verb-restamp-fixed-in-*.txt`, task requeued. Malformed version `9.9` -> escalate, verb not run. PASS.
- AT5 (sandbox): dirty worktree -> dirty.patch + dirty.patch.sha256 (hash verified by sha256sum) + untracked.txt; commit count unchanged (9->9); worktree changes untouched; BACKLOG `resume: branch`. PASS.
- AT3 (sandbox): provider_refusal first sighting -> cooldown_until +15 min, no requeue; re-sweep inside window -> skip; after window -> requeue, next window 30 min (backoff x2). PASS.
- AT11 / D6 `block_storm decide`: none -> none; one block -> task_local; two same-code with one 3 h old -> task_local; two same-code inside 3600 s -> systemic; default systemic code (invalid_effort_config) -> systemic; `--systemic-codes none` -> task_local. wake_driver.sh: overwatch never gated; recheck_waiting_tasks/recheck_blocked_tasks moved outside the gate. PASS.
- AT12 / D7: overwatch-sweep.sh `--only bogus` -> exit 2, `--lock-wait abc` -> exit 2. Held the repo lock 4 s, started `--only block-triage --lock-wait 20`: it waited, acquired after release, triaged 9 tasks; no final "repo lock held ... left unchanged" line (one intermediate `lock.provenance.contended` probe message was printed while it waited). Nudge path `${SCRIPT_DIR}/../overwatch-sweep.sh` resolves (SCRIPT_DIR = scripts/wake). PASS for the post-agent path; see Finding 1 for pre-dispatch blocks.
- D9 docs: docs/overwatch.md covers the Block Record, classifier table, safety rules, configuration, and reading actions.log; `overwatch.md` added to team/config/docs-manifest.txt. overwatch-checks README lists check-block-triage. PASS.
- Lifecycle: promote_blocked_to_backlog preserves the record (header updated, AT2 sandbox confirmed attempts/resume survive promote); transition_task_state and task-status.sh DONE/WONT-DO remove it. check-blocked-tasks.sh is now report-only (lists BLOCKED tasks without a record). PASS.
- Step 6.9: no hardcoded secrets in added lines; no new inline-Python heredocs (inline-python-allowlist shrank by 3); BUG-/version-shaped strings in added lines are interface example values only (sanctioned). PASS.
- Step 6.10: prerequisite graph acyclic (every edge to a lower sequence), all referenced IDs exist; CM-044 open-rc opens, TESTER-045 depends on all 13, CM-047 release depends on all 13 + TESTER-045; no 046 (approval `auto`). PASS.
- Step 6.13: no skip/xfail annotations added by this RC. PASS.
- Step 6.12 (5) — YES. git-boundary-guard.jsonl records in CODER-037..042: 1,694 refused `git -C $HOME/develop/pgai-agent-kanban push origin refs/tags/ai_v2.x.y` (154 distinct real tags per burst). Prior cycle's records (CODER-20261003-003/-017) were pushes inside throwaway fixture repos only. Root cause reproduced (see Finding 3).
- Step 5a unit: `run-unit-tests.sh --coverage` UNIT_EXIT=1 — lint pre-flight passed; pytest `1 failed, 8460 passed, 43 skipped in 1927s`. Failure: test_package_modules_run_by_path.py::test_file_load_binds_the_package_of_its_own_tree[pgai_agent_kanban.overwatch.block_stage_deferral] (KeyError 'pgai_agent_kanban'); deterministic on 2/2 direct-pytest re-runs (debugging only). New module in this RC -> bug, filed BUG-0551. The same run produced 154 refused `push origin refs/tags/...` guard records in this task's logs/git-boundary-guard.jsonl (BUG-0548).
- Step 5b unit coverage (coverage/unit.json totals): line 79.6% (19094/24002 statements), branch 65.6% (4582/6990); blended 76%.
- Step 5c unit lint findings: findings-unit.json `findings: []`.
