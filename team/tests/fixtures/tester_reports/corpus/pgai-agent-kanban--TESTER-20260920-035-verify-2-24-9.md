# Verification Report: rc/v2.24.9

## Summary

Verified release candidate `ai_rc/v2.24.9` (commit `f7256ca`) against
`requirements/v2.24.9-bugfix-bundle-20260920.md`, a seven-item bug bundle. All seven
bundled items were exercised behaviorally, not merely inspected: the dashboard event
band was timed against the operator's own live logs and its output diffed row-for-row
against the pre-change tree; the new worktree-sweep liveness predicate was probed
through all five of its states including the false-positive case it was written to
eliminate; the discovery narration split was probed by capturing a narrating function's
stdout; and the three lint-shaped items were confirmed by running the lints that had
reported them. Every one of the seven is fixed. Both gated runners exited 0 with zero
quality-class lint findings.

Four findings fall outside the bundle. One is a loose end of this RC's own work: the
stage file split out to satisfy the engine size ceiling was never added to the manifest
that ceiling is measured from. Three concern the git boundary guard and are pre-existing
— they were handed forward in `CODER-20260920-027`'s status Summary, independently
reproduced here, and extended by two conditions that Summary did not carry: the guard
leaks host-degrading processes when it sits on the test suite's PATH, and its
availability check tests for a directory rather than for the shim inside it, which has
left the boundary silently unenforced on the operator's live install since 2026-09-22
02:02. The RC's own content is clean; the release is usable; the loop that produces the
next one is not healthy.

## Recommendation

SHIP-WITH-SERIOUS-CONCERNS

## Systemic Risk

high

The rating comes from Findings 9 and 10, not from anything this RC shipped. Between them
no agent other than CM can obtain a green gated unit suite inside its own dispatch, and
attempting one degrades the shared host every agent runs on. Both are `Regression: no` —
they predate this RC and do not implicate its content.

## Findings

### Finding 1: Pre-flight — the RC is intact

**Category:** pass

Step 2. Every task folder under `projects/pgai-agent-kanban/tasks/` whose `status.md`
reads `DONE` and whose `README.md` names a feature branch other than `none` was scanned.
No named feature branch still exists locally, which passes both checks trivially and is
the normal post-merge shape — CODER and WRITER delete branches after merging. Zero
anomalies.

Step 3. `git rev-parse HEAD` in the task worktree equals `git rev-parse ai_rc/v2.24.9`
(`f7256ca595a8bcc0db4d379e10156a8e69b474f3`). `git status --porcelain` is empty.
`git stash list` is empty.

### Finding 2: Release state is well-formed and the RC version leads the last release

**Category:** pass

Step 6.1. `projects/pgai-agent-kanban/release-state.md` carries all four fields:
`## Active RC` `v2.24.9`, `## RC Opened At` `2026-09-20T22:00:23+00:00`,
`## RC Opened By Task` `CM-20260920-034-open-rc-2-24-9`, `## Last Released` `v2.24.8` —
a well-formed `vX.Y.Z`, whose presence is by design and not drift.

`pp_last_released_version "pgai-agent-kanban"` returns `v2.24.8`, and
`semver_gt "v2.24.9" "v2.24.8"` returns true. The comparison was made through
`team/scripts/lib/semver.sh`, never inline.

### Finding 3: Bundle-wide acceptance criteria

**Category:** pass

The requirements doc's three criteria:

- *Each bundled item is addressed in the implementation* — Findings 4 through 8 below,
  one per item.
- *All Python files compile cleanly* — every `*.py` under `team/` passed
  `python3 -m py_compile`, exit 0.
- *All bash files pass syntax check* — every `*.sh` under `team/`, plus `install.sh`,
  passed `bash -n`, zero failures.

### Finding 4: BUG-0357 — the event band renders inside the pane's refresh interval

**Category:** pass-with-caveat

The fix repairs a collapse-key cache whose hit test, `[[ -v "CACHE[$rest]" ]]`, re-lexed
its own subscript for quote removal and therefore reported a false miss for any cached
text containing an odd number of single quotes — which production's repeated
`remedy:`/`message:` lines are full of. The cache was paying a subshell fork per
occurrence instead of per distinct identity.

Measured here against the operator's own five `logs/cron-<agent>.log` files (symlinked
into a throwaway kanban root, per the bug's own reproduction), at the shipped default
read bound of 20000, host load average 2.5:

| check | result |
|---|---|
| three consecutive renders, live logs | 4.14 s, 4.25 s, 4.07 s — all under the 5 s pane interval |
| surviving signal lines at that bound | 45,748 (pm 9337, coder 9031, writer 9222, tester 9070, cm 9088) |
| two renders, frozen 20000-line snapshot | 3.996 s, 4.062 s; byte-identical to each other |
| same frozen snapshot rendered by `ai_main` | 16.138 s; output byte-identical to the RC's |

The bug's acceptance criteria: criterion 1 (under 5 s, three consecutive) met;
criterion 2 (fixture at production diversity and volume) met — the guarding fixture now
builds 29,310 today-dated lines across 150 distinct texts, with assertion floors raised
to `>= 25000` and `>= 140`; criterion 3 (output unchanged, consecutive renders
byte-identical) met, verified both ways above; criterion 4 does not apply — the shipped
read bound is unchanged at 20000 in `kanban.cfg_example` and the variable's own
documentation.

**Caveat, not a gap.** The headroom is thin and shrinking. The bug measured 29,309
surviving lines on 2026-09-20; the same bound over the same five logs yields 45,748
today, 1.56x more in two days, and the render sits at 4.1-4.3 s against a 5 s interval.
The fixture is sized to the 29k figure, so it will clear its bound after production has
stopped clearing the real one. This is the shape the bug's own Hypothesis predicted for
any constant-factor fix; it is not a defect in the shipped change and is recorded here
rather than filed, because nothing is failing today.

### Finding 5: BUG-0358 — governance budget defaults agree with the documented values

**Category:** pass

`team/scripts/lib/config_loader.sh`'s Key Registry now reads `budget_bytes_tester
OPTIONAL 110592` and `budget_bytes_cm OPTIONAL 94208`, matching `kanban.cfg_example`
lines 459 and 486.

Verified behaviorally, not structurally: `python3 team/scripts/lint_doc_command_truth.py`
exits 0 and reports `key-parity: scanned 169 key citation(s) across 15 document(s)
against 97 registry key(s); 0 finding(s)`. The two `CONFIG DEFAULT MISMATCH` findings the
bug quoted are gone.

### Finding 6: BUG-0359 — the wake task-state library is back under the engine ceiling

**Category:** pass

`team/scripts/lib/wake_task_state.sh` is 793 lines (was 907; the ceiling is 800). The two
reconciliation sweeps moved to a new `team/scripts/lib/wake_reconcile_sweeps.sh` (289
lines), sourced by `wake_common.sh` immediately after `wake_task_state.sh` as stage 6 of
a composition table whose header was correctly renumbered from eight entries to nine.
`python3 team/scripts/lint_engine_size_dupe.py` exits 0.

The manifest loose end this split left behind is Finding 11, filed separately.

### Finding 7: BUG-0360 — worktree sweeps ask whether the owning agent is alive

**Category:** pass-with-caveat

The `pgrep -f "$task_id"` guard is replaced by a recorded-pid answer: each provider's
`provider_dispatch` calls `task_liveness_record` with the pid it just backgrounded, and
both sweeps gate on the single shared `task_liveness_is_alive`, which classifies that
record through `pgai_lock_pid_liveness_verdict` from the lock layer.

Behavioral probe of the predicate in a sandboxed root, all five states, with an unrelated
`bash -c` process whose argv contained the task id running throughout cases B and C
(confirmed visible to `pgrep -af` at the time):

| case | setup | expected | observed |
|---|---|---|---|
| A | no record | alive (undetermined → skip) | alive |
| B | no record, unrelated argv match running | alive (undetermined) | alive |
| C | record naming a dead pid, unrelated argv match still running | **not alive → sweep proceeds** | **not alive** |
| D | record naming a live pid | alive → sweep skips | alive |
| E | unparseable record | alive (conservative) | alive |

Case C is the failure mode the bug exists to eliminate, and the old guard would have
answered "alive" there. The bug's acceptance criteria: criterion 1 (owning process
identified specifically) met; criterion 2 (a regression test asserting the negative
direction) met by
`test_unrelated_process_mentioning_task_id_does_not_block_reclaim`, which creates the
worktree, writes a dead-pid record, starts a `bash -c` process mentioning the id, waits
until `pgrep -f` actually matches it, then asserts the worktree was reclaimed anyway;
criterion 3 (positive cases still hold) met by
`test_done_task_with_live_dispatch_pid_is_skipped`,
`test_working_task_worktree_is_preserved` and `test_blocked_task_worktree_is_retained`;
criterion 4 (one shared helper, not two copies) met — both sweeps call
`task_liveness_is_alive`.

**Caveat.** Criterion 5 — "on a live install running the fix, a task that reaches DONE
through the normal wake path has no entry in `git worktree list` and no `[A]` marker by
the end of the next wake cycle" — cannot be evaluated before the release ships, because
the live install does not carry the fix. It is a post-release observation for CM or the
operator, not a shortfall in the change.

### Finding 8: BUG-0362, BUG-0361, BUG-0356 — narration channel, census wording, provenance sweep

**Category:** pass

*BUG-0362 — discovery narration off the stdout data channel.* `log()` in
`wake_bootstrap.sh` now redirects `tee`'s own stdout copy to stderr, so narration reaches
the log file and the operator's terminal but never fd 1. Probed by capturing a narrating
data-producing function through `$(...)`: the capture held exactly the two data lines and
zero narration lines, while the narration landed in the log file. The consumer-side
guards are in place as defense in depth — `discovery_step_bugs`, `discovery_step_priority`
and `_disc_write_bundle` each skip a non-path line loudly, and `_disc_set_status_field`
validates `path.is_file()` before `read_text`. The audit half of the fix checks out: the
only remaining stdout writes in `discovery_steps.sh` are data (a candidate path, a
`num\tfile` pair) and one deliberate stderr re-emit.
`team/tests/integration/test_discovery_narration_channel.py` (4 tests) passes.

*BUG-0361 — census rows describe behavior.* All four of the bug's acceptance criteria:
`grep '^| 63 |' docs/blocking-site-census.md | grep -c 'BUG-[0-9]\{4\}'` → 0; row 63's
Condition cell now reads as a condition rather than a changelog entry; the row's other
four columns and the rollup line (`63 sites, 0 unclassified. 34 BLOCK-WORTHY, 27 CONVERT,
2 AGENT-RESOLVABLE.`) are unchanged; `python3 team/scripts/lint_gate_census_compliance.py`
exits 0. Row 53's version-shaped citation, which the bug left as a judgement call, was
swept in the same pass.

*BUG-0356 — provenance hygiene class.* `python3 team/scripts/lint_hygiene.py --classes
provenance` reports `OK — 0 findings across 8 scan director(y/ies)`, and the full
three-class run is also 0. The eleven `BUG-0344` citations the bug listed are gone:
`grep -c BUG-0344` over `team/scripts/dashboard/event-band.sh` and
`team/tests/unit/test_event_band_renderer.py` returns 0. `CODER-20260920-033`'s WONT-DO
is therefore correct on the facts — the sibling event-band ticket reworded those
citations as part of its own fix, leaving nothing to ship. Its `Needs Human: yes` is the
WONT-DO confirmation `SOP-core.md` requires and does not gate the release.

### Finding 9: No non-CM agent can obtain a green gated unit suite in its own session

**Category:** bug
**Fix Effort:** medium (3-5 CODER tasks — the guard's decision surface, the three test
files, and coverage for the refusal path wherever the exemption lands)
**Systemic Risk:** high (every agent, every cycle, on every install where the shim is present)
**Filed As:** BUG-0365-git-guard-refuses-fixture-local-pushes (Path C-A — filed as BUG)
**Regression:** no
**Integrity:** breakage

`wd_install_git_boundary_guard` (`team/scripts/lib/wake_guard_wiring.sh`) prepends
`team/scripts/wake/git-guard` to PATH and exports `PGAI_AGENT_ROLE=<role>` for every role
except `cm`. The guard refuses `fetch`/`pull`/`push` on the verb alone, without looking at
the destination. Eleven unit tests push into a bare repository their own fixture created
seconds earlier under `tmp_path`, and every one of them is refused.

Reproduced on this RC with the shim on PATH under `PGAI_AGENT_ROLE=coder`:
five failures in `test_cm_release_step3_state_detection.py`, three in
`test_overwatch_push_lag_bash.py`, three in `test_discovery_git_local_only_wake_context.py`
— eleven, matching exactly the count `CODER-20260920-027` recorded in its own status
Summary from a full framework-wide run ("4909 passed, 31 skipped, 11 failed — exactly
these 11, no others"). Without the shim the same eleven pass.

The full gated runner was then run once end to end under the guarded environment, for the
exit code rather than for a test selection:

| run | exit | result | wall clock |
|---|---|---|---|
| unguarded (`--coverage`) | 0 | 4959 passed, 31 skipped | 840 s |
| guarded | **1** | **42 failed**, 4908 passed, 40 skipped | 3712 s |

Eleven of the 42 are this finding. The other 31 are Finding 10. Neither alone gets a
non-CM agent to exit 0.

The consequence is not test hygiene, it is the loop's evidence chain. `run-unit-tests.sh`
exit 0 is the CODER/WRITER completion evidence and TESTER's stated precondition for
recording `PASS`. No agent but CM can produce it from inside its own dispatch, so every
non-CM agent must either work around the gate or claim completion on partial evidence.
`CODER-20260920-027` did the honest thing — recorded the shortfall in its Summary and
handed it forward — which is what let this report reach the defect rather than the
symptom.

Corroborating evidence from inside this cycle: 100 guard refusal records across four
CODER task folders, every one an `argv` of `["git","-C","<temp>/pytest/unit/<test>/local",
"push","origin","HEAD"]` under `"role":"coder"`. None targets a real dev tree or origin.

### Finding 10: The guarded suite leaks shim processes and degrades the shared host

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks — a stdin redirect in the shim, and/or process-group
teardown in the two fixture call sites)
**Systemic Risk:** high (the damage lands on the host every agent in the fleet shares)
**Filed As:** BUG-0366-git-guard-shim-processes-leak (Path C-A — filed as BUG)
**Regression:** no
**Integrity:** breakage

Running `run-unit-tests.sh` with the guard on PATH under a non-CM role leaves a growing
population of `team/scripts/wake/git-guard/git` bash processes alive. Observed live
during this verification:

| observation | value |
|---|---|
| leaked shim processes, steady state | 31-35 |
| oldest leak's age when observed | 2494 s (~41 min) |
| leaks reparented to init (`PPid: 1`) | 5 |
| host load average, 1/5/15 min | 39.82 / 38.63 / 34.69 (8 cores) |
| a leaked shim caught spinning | 48.7 % CPU, state `R` |
| load average one minute after killing every leak | 20.64 |
| same suite, guard off PATH | 840 s, exit 0, 4959 passed, zero leaks |
| same suite, guard on PATH | 3712 s (1:01:52), **exit 1**, 42 failed, 4908 passed, 40 skipped |

Thirty-one of those 42 failures are this finding, not Finding 9's push refusals: 19 in
`test_dashboard_missing_tree_fixture.py`, 11 in `test_dashboard_network_stub_fixture.py`
— the two files whose `subprocess.run(..., timeout=15)` calls reach the shim, every test
in both failing — and one collateral failure in
`test_event_band_renderer.py::test_large_pre_today_body_renders_correctly_within_time_bound`,
a render-time assertion that is sound on an idle host and cannot hold on one at load 39.
That last one is the clearest measure of the collateral damage: the leak fails a test
that has nothing to do with git. The load average fell from 39.82 to 20.64 within a
minute of killing every leak, which is what attributes it to the leak rather than to the
other agents sharing this host.

Most leaked processes are blocked in `pipe_read` on fd 0 (`/proc/<pid>/wchan`,
`/proc/<pid>/fd`), never reaching the shim's own `exec`, so the git command each was
asked to run never happens; at least one was caught spinning instead. The leaked argument vectors are read-only verbs the guard is meant to pass
straight through — `rev-parse --git-dir` against a non-repository directory, and
`tag --list`. The shim measured in isolation completes in 0.036 s, so the hang is in the
process's own lifetime, not in git.

Root cause is not established and the bug says so. The two candidate paths — a pipe stdin
that is never closed, and `subprocess.run(..., timeout=15)` killing only the immediate
child while the bash grandchild survives — are both consistent with the evidence.
`CODER-20260920-027` observed the same leak independently ("20+ such orphans ... load
climbed past 20-30 on this 8-core box") and handed it forward.

### Finding 11: The stage extracted to satisfy the size ceiling is not measured by it

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks — one manifest entry and one comment word)
**Systemic Risk:** medium (nothing enforces manifest completeness, so the next extraction can repeat it)
**Filed As:** BUG-0364-engine-manifest-misses-extracted-stage (Path C-A — filed as BUG)

`team/scripts/lib/wake_reconcile_sweeps.sh` is absent from `ENGINE_FILES` in
`team/scripts/lint_engine_size_dupe.py`, so the 800-line check never measures it. The
lint's `ok -- 35 engine file(s) within the 800-line ceiling` line counts a manifest that
does not include the new stage, and appending 600 filler lines to the file leaves the
lint green. `wake_common.sh`'s own header already enumerates the stage as number 6 of
nine; the lint's grouping comment still reads `seven lib stages` and lists seven.

BUG-0359's literal Expected is satisfied — the file that was over the ceiling is under it
— which is why this is a `gap` against the intent rather than a `bug` against the change.

### Finding 12: A BLOCK-WORTHY boundary degrades to silence when its shim file is missing

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks — the presence check, plus an install-policy assertion)
**Systemic Risk:** medium (the silent-guard-failure shape can recur at any guard wired the same way)
**Filed As:** BUG-0367-guard-path-check-tests-directory (Path C-A — filed as BUG)

`wd_install_git_boundary_guard` decides the guard is available with
`[[ -d "${SCRIPT_DIR}/git-guard" ]]`. A directory that exists but holds no `git` passes:
PATH gains an empty directory, the WARNING branch that exists to announce an uninstallable
guard does not run, and every remote verb from every non-CM role reaches the real git
unrefused. Census row 62 classifies this boundary BLOCK-WORTHY; it is currently off.

The operator's live install is in that state today. `scripts/wake/git-guard/` is empty,
directory stamped `2026-09-22 02:02` — the same minute the install was upgraded to
v2.24.8 (`VERSION` → `v2.24.8`; siblings `wake/claude.sh`, `wake/codex.sh`,
`wake/harness.sh` all stamped 02:02). The last guard record in any task folder in this
cycle is `2026-09-21T03:44:34Z`, before that upgrade.

The shipped `install.sh` is not shown to be the cause: its team-item loop's directory
branch is `cp -r "$src/." "$dst/"`, and reproducing that copy from this RC's tree does
deposit `wake/git-guard/git` with mode 755. The shim is tracked in both `ai_main`
(`c62b130`) and the RC. The missing file's origin is undetermined; the defect filed is
that the wake layer cannot tell the difference and says nothing.

## Lint Findings (non-blocking)

None. `${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-unit.json` and
`.../findings-integration.json` were both written by their runners and both carry
`"findings": []` (schema_version 1, generated 2026-09-22T04:18:02+00:00 and
2026-09-22T04:28:35+00:00). Three of this RC's seven bundled items were themselves
quality-class lint findings — `engine_size_dupe`, `doc_command_truth` key-parity, and the
hygiene `provenance` class — and all three are now clean, which is why the files are empty
rather than absent.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=14d freshness=stale — the last cold build failed on the claude-abliterated Dockerfile's FROM arity under both docker and podman and on podman/rocky9's chmod COPY, and that run filed BUG-0167-cold-build-claude-abliterated-failure (now `## Status` done); the record is stale on both counts (14d exceeds the 7-day window, and v2.9.20 is not the v2.24.9 under verification), so nothing is known about whether this tree's three images build today.

## Autonomous criterion

Step 6.12, five questions.

1. **Were any tasks force-promoted from WAITING to BACKLOG by hand?** NO. Every task
   folder's `status.md` records its own promotion; this task's own reads "Promoted from
   WAITING to BACKLOG by wake script: prerequisites satisfied." No status file carries a
   hand-forced transition.
2. **Was `release-state.md` edited outside the three CM scripts?** NO. Its mtime is
   `2026-09-20 22:00:23`, equal to its own `## RC Opened At` value, and it has not been
   touched since `cm-open-rc.sh` wrote it.
3. **Were any scripts patched mid-build?** YES, with the detail below. No commit in
   `ai_main..ai_rc/v2.24.9` patches tooling outside the RC's own subject matter. But the
   *live install* was upgraded to v2.24.8 at `2026-09-22 02:02`, while this RC was in
   flight — `VERSION` → `v2.24.8`, `VERSION_DETAIL` → `ai_v2.24.8
   deposit-sha=c62b130...`, and every file under `scripts/` and `subagents/` restamped.
   This is an operator deploying a previously shipped release, not a patch to the RC's
   tree, and it touched no task state, no queue file and no `release-state.md`. It is
   recorded rather than filed on its own because the one observable defect that followed
   it — the empty `scripts/wake/git-guard/` directory and the silence around it — is
   Finding 12, filed as BUG-0367. **Indicates a defect worth shipping as a follow-up:**
   yes, BUG-0367.
4. **Were any queue files edited by hand?** NO. Every queue marker for this cycle's tasks
   is `[x]` except this task's own `[A]`, each written by the transition that set the
   corresponding state. `coder_backlog.md` and `tester_backlog.md` mtimes (`2026-09-22
   04:00:57` and `04:01:54`) coincide with the wake cycle that promoted this task.
5. **Did any agent other than CM invoke a remote git operation?** YES, and it is the
   evidence for Finding 9 rather than a separate defect. 100 records across four CODER
   task folders — `CODER-20260920-027` (31), `-028` (21), `-029` (32), `-030` (16) —
   every one `{"role":"coder", "argv":["git","-C","<temp>/pytest/unit/<test>/local",
   "push","origin","HEAD"]}` against a pytest sandbox path created by the test that made
   the call. Every attempt was refused; none reached a remote, none moved ref state, none
   named a real dev tree or origin. That is the `low` rating the reference sets for a
   refused attempt, and on its own it would leave the recommendation where the rest of the
   report puts it. **Indicates a defect worth shipping as a follow-up:** yes — the same
   defect as Finding 9, filed as BUG-0365. No second filing was made, because one gap
   takes exactly one filing and a duplicate would misstate how many defects exist.

**Manual interventions required: one operator upgrade of the live install mid-RC
(question 3).** No intervention on the task pipeline itself.

## Caveats

- **Suite evidence and its environment.** `UNIT_EXIT` and `INTEGRATION_EXIT` were both 0,
  from `bash team/scripts/run-unit-tests.sh --coverage` (4959 passed, 31 skipped, 840 s)
  and `bash team/scripts/run-integration-tests.sh --coverage` (541 passed, 1 skipped,
  605 s), each run from the dev tree root with output teed under
  `${PGAI_AGENT_KANBAN_TEMP_DIR}/tester/`. Both runs were unguarded: this session's PATH
  carries no git-guard shim, because the live install's shim directory is empty
  (Finding 12). That is the honest environment for a `PASS` precondition today, and it is
  also the reason Finding 9 did not surface as a runner failure in Step 5a. A TESTER
  dispatched on an install whose shim is present would see `run-unit-tests.sh` exit 1 and
  could not record `PASS` at all. That was confirmed rather than inferred: one full
  `run-unit-tests.sh` under the guarded environment exited 1 with 42 failures in 3712 s.
  Its findings file carried one `changelog_freshness` quality finding, which is an
  artifact of that probe's throwaway `KANBAN_ROOT` having an empty `bugs/` directory
  (`(one or more bug fixes — bug files unavailable)`), not a property of the RC — the
  authoritative unguarded runs wrote zero findings. The probe's exit code is evidence for
  Findings 9 and 10; its lint output is not evidence about this tree.
- **Coverage (informational; never enters the verdict).** Unit: 76.36 % statements,
  63.17 % branches (blended 73.31 %). Integration: 11.45 % statements, 9.17 % branches
  (blended 10.93 %). Read from the `totals` object of
  `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/unit.json` and `.../integration.json`, whose
  field names (`percent_statements_covered`, `percent_branches_covered`,
  `percent_covered`) were verified against the generated files rather than assumed.
- **Step 6.8 skipped with a note.** The diff touches no release-lifecycle file — no
  `cm/release.sh`, `cm/open-rc.sh`, `cm/cancel-rc.sh`, or `lib/project_paths.sh`. The
  synthetic release simulation ran anyway as part of the gated integration suite
  (`tests/integration/test_cm_release_synthetic.py`, 17 tests, all passing).
- **Step 6.13 confirmed by hand.** `bash team/scripts/lint_skip_bug_gate.sh` exits 0:
  no placeholder skip IDs, and `BUG-NNNN file-existence check: 0 missing files`.
- **Step 6.10, prerequisite graph.** No cycles; every referenced task ID resolves to a
  real folder; the lifecycle bookends are correct (`CM-20260920-034-open-rc-2-24-9` at the
  head with `none` as its own prerequisite, this task plus
  `CM-20260920-037-release-2-24-9` at the tail). Sequence number 036 is unused; a gap is
  not a collision and nothing references it.
- **Step 6.14, coding-standards spot-check.** No new script under `team/scripts/` lacks a
  `--help` (the RC adds one library, not an entry point). No new comment, docstring, help
  line or log line cites a bug, task or version identifier — the hygiene lint's
  `provenance` class is clean tree-wide. No new operational value is hardcoded where an
  operator would want it configurable; the event-band read bound stays in
  `kanban.cfg [dashboard]`. No duplicated implementation:
  `dashboard_event_signal_collapse_key` delegates to its new no-fork `_var` sibling rather
  than repeating it, and both sweeps share one liveness predicate.
- **Constraint negative checks.** No hardcoded secret, key, token or password appears in
  any added line of `git diff ai_main..ai_rc/v2.24.9`.
- **One RC-internal re-run, healed by the loop.**
  `CODER-20260920-030-discovery-narration-channel` merged twice (`c942f68` on 09-21, then
  `ef19848` on 09-22). The first merge changed `log()`'s channel and left two sibling
  integration test files asserting on the old stdout behaviour; the second session pointed
  them at stderr. The wake retry mechanism healed it inside the RC without intervention,
  and the final tree is green. Recorded as evidence the loop works, not as a finding.
- **`deep_verify_ledger_read_latest` takes a positional project name, not `--project`.**
  Passing `--project pgai-agent-kanban` returns `found=false` with exit 0 — the same
  answer as "never deep-verified" — rather than refusing an unresolvable project. A
  verifier who guessed the flag form would write Form B over a real record. Pre-existing,
  outside this RC's scope, and recorded here as an observation rather than filed.
- **Prior-run artifacts.** None. The task's `artifacts/` directory was empty at start and
  no `report.md.previous-RUN-N` exists, so this is a fresh first run and no earlier
  verdict was available to re-issue.
