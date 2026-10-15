# Verification Report: rc/v2.24.3

Verified `ai_rc/v2.24.3` at HEAD `e4b1966` in the detached worktree
`/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260919-020-verify-2-24-3`
against `requirements/v2.24.3-bugfix-bundle-20260919.md` and the four bundled
bug reports it names (BUG-0334, BUG-0335, BUG-0336, BUG-0337). Both gated
runners were executed with `--coverage` and both exited 0. Every bundled item
was exercised behaviorally, not merely read: the stale-lock threshold and its
reap/defer decisions were driven against a seeded lock with a live holder, a
dead recorded pid and a wiped provenance record; the chain-ownership
reverification helpers were driven through a simulated mid-chain reap; the
status band was run against a fixture reproducing the live install's log shapes
and the original failure mode was reproduced on the pre-fix tree for contrast;
and the event band was timed against symlinks to the operator's real 819 MB
cron logs. Three of the four bundled items are fully delivered. The fourth,
BUG-0337, delivers the part that mattered most — the 25-minute render is gone
and render cost no longer scales with historical log size — but misses its own
stated five-second wall-clock bound at production *today*-volume. One further
defect surfaced outside the bundle: a pre-existing unit test whose verdict
depends on the verifier's scratch directory path. Both are filed via Path C
(autonomous follow-up filing — non-blocking) and neither stops the release.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Findings

### Finding 1: BUG-0334 — derived stale-lock threshold no longer reaps a live chain

**Category:** pass

`pgai_lock_stale_age_threshold_seconds` now resolves to
`max(configured_periods x 120s, max_runtime_seconds + kill_grace_seconds + 120s)`.
Against the live `kanban.cfg` values (`max_runtime_seconds = 14400`,
`kill_grace_seconds = 30`) it returns **14550s**, not the 360s the incident
reaped at.

Behavioral verification in a throwaway sandbox root
(`env -u PGAI_TASKS_DIR -u PGAI_PROJECT_ROOT KANBAN_ROOT=$S …`), with a real
second process holding the flock:

| scenario | lock mtime age | recorded pid | result | expected |
|---|---|---|---|---|
| the filed incident, reproduced | 360s (6m0s) | alive | `lock.provenance.contended`, rc 1, **not reaped** | defer |
| threshold minus one second | 14549s | alive | `lock.provenance.contended`, rc 1, **not reaped** | defer |
| past threshold | 14551s | alive | `lock.provenance.stale_reaped.aged_out`, rc 0 | reap |
| dead recorded pid | 2s | dead | `lock.provenance.stale_reaped.dead_pid`, rc 0 | immediate reap |
| provenance wiped to zero bytes | 2s | n/a | `lock.provenance.stale_reaped.no_provenance`, rc 0 | immediate reap + violation marker |

The no-provenance case wrote the violation marker to the sandbox's own
`projects/pgai-agent-kanban/refusal-ledger.md` (verified by reading it), which
also confirms the sandbox isolated correctly — nothing reached the live install.

BUG-0334's second acceptance line ("derived threshold visible in why.sh lock
display and the reap narration — names the derivation, not just the number") is
met on both surfaces. The aged-out reap message carries
`(derivation: max-chain-budget floor enforced at 14550s (max_runtime_seconds
14400s + kill_grace_seconds 30s + 120s margin), above the configured 3 wake
period(s) x 120s/period = 360s)`, and `why_details_render_locks` prints
`stale-age threshold: 4h 2m (<same derivation>)`.

The read-only contract holds: rendering the lock pane wrote **no**
`refusal-ledger.md` into the sandbox project dir, confirming the `narrate` gate
keeps the clamp warning on `pgai_lock_acquire`'s acting path only.

### Finding 2: BUG-0334 — chain-level lock reverification at mutating phase boundaries

**Category:** pass

`pgai_lock_still_held`, `pgai_lock_reverify_or_narrate` and `pgai_lock_abandon`
behave as specified, driven directly:

- healthy path — `still_held` → yes; `reverify_or_narrate` → rc 0, silent.
- after a simulated reap (the on-disk record overwritten with a different
  holder/pid) — `still_held` → no; `reverify_or_narrate` → rc 1 plus a
  dual-channel `lock.provenance.reverify_lost` naming the new holder's four
  fields (`holder=chain-B pid=999999 op=wake-chain task=TASK-B`), the phase
  (`merge`), and stating it is stopping before mutating shared state.
- `pgai_lock_abandon` → rc 0, and the on-disk record **still reads
  `holder=chain-B`** afterwards — the new holder's record was not destroyed,
  which is the whole point of the release-shaped counterpart.

The three wiring sites read correctly at `wake_driver.sh` (agent dispatch,
placed at the top of `run_project_chain`'s per-iteration loop ahead of the four
housekeeping calls that themselves write queue-adjacent state), `process_one_task`
(the post-agent merge boundary, returning 3), and `wake_driver_finalize.sh`
(the queue-marker write). `_project_lock_lost` correctly selects
`pgai_lock_abandon` over `pgai_lock_release` at end-of-chain cleanup.
BUG-0334's acceptance item 4 (a two-process field fixture) ships as
`team/tests/integration/test_chain_lock_reverification.py` and passes in the
gated integration run.

### Finding 3: BUG-0335 — provenance hygiene findings cleared

**Category:** pass

`python3 team/scripts/lint_hygiene.py --classes provenance` reports
`OK — 0 findings across 8 scan director(y/ies)`, exit 0. The full default-class
run (`provenance`, `version-named-test`, `comment-drift`) likewise reports 0
findings. The single named instance,
`team/tests/unit/test_event_band_renderer.py:10`, no longer cites a BUG-ID; the
docstring now describes the behavior under test.

### Finding 4: BUG-0336 — status band renders every roster row

**Category:** pass

Built a fixture root reproducing the live install's shapes: five agents with
parseable `done: completed=…` verdict lines, a `cron-cleanup.log` of bare
untimestamped `ERROR:` lines, and a `cron-overwatch.log` of timestamped lines
carrying no verdict.

- RC tree: **all seven roster rows**, exit **0**. `CLEANUP` reads
  `no data yet` / `no result yet`; `OVERWATCH` reads its real parsed timestamp
  `2026-09-19 13:00:01` / `no result yet`. Never blank, never a raw log line.
- Pre-fix tree (`git archive ai_main`, same fixture, same command): **five rows**,
  `CLEANUP` and `OVERWATCH` absent, exit **1**, empty stderr — the filed symptom
  reproduced exactly.

This satisfies the "agent ordered *after* the non-distillable one" regression
condition too: `OVERWATCH` follows `CLEANUP` in roster order and both render.
The fix is a single explicit `return 0` after the distill loop, with a comment
stating bash's `while`-loop exit-status rule; the placeholder values were
already correct before the loop ended.

### Finding 5: BUG-0337 — event band read is bounded, but the render still exceeds its refresh budget

**Category:** gap
**Fix Effort:** small (1–2 CODER tasks)
**Systemic Risk:** medium (the verification shape that missed it — an acceptance
criterion checked against a fixture that mocks away the production condition —
is not specific to this band)
**Filed As:** BUG-0340-event-band-render-exceeds-refresh-budget (Path C-A —
filed as BUG: code defect / regression / test failure)

What is delivered, and verified:

- The read is genuinely bounded. `tail -n 20000 | grep -E "$(dashboard_event_signal_ere)"`
  across all five live logs (819.1 MB) costs **0.057 s total**. The 25-minute /
  `exit=124` symptom in BUG-0337 is gone.
- Render time does not grow with historical size (acceptance checkbox 2).
  7.6 MB fixture / 5270 survivors → 7.46 s; 819.1 MB fixture / 7068 survivors →
  11.60 s. Time tracks survivor count, not bytes.
- The single-pattern-list constraint (checkbox 3) holds. `event-band.sh` builds
  its pre-filter from `dashboard_event_signal_ere()`, which joins
  `EVENT_SIGNAL_PATTERNS` itself; no second list exists. Fidelity checked
  empirically over 20,000 real log lines: `grep -E` selected 1547 lines, the
  authoritative `dashboard_event_signal_is_signal` selected 1547, and the two
  sets are **identical** — the pre-filter narrows nothing the classifier would
  have kept.
- Existing behaviour preserved (checkbox 4). `no wake events yet today` still
  prints for an empty band; chronologic merge, `[<agent> HH:MM:SS]` tags, and
  boilerplate dropping all still work; big-vs-small fixture output matches
  except for the interleaving of untimestamped continuation lines, which is a
  pre-existing consequence of stamping them with `date +%H:%M:%S` and is
  unchanged by this RC.

What is not delivered: acceptance checkbox 1's wall-clock bound. Measured
against symlinks to the five live cron logs, six runs across two system-load
conditions: **11.21, 11.25, 11.29, 11.48, 11.60, 11.63, 11.92 s** — consistently
more than twice the 5 s target. That 5 s is `_DEFAULT_REFRESH` in
`team/scripts/dashboard/create.sh`, the interval `cadence-loop.sh` hosts the
band on, so the pane is permanently at least one tick stale.

The residual cost is entirely downstream of the pre-filter: ~7000 surviving
today-lines at roughly 1.5 ms each, each running the thirteen-pattern
classifier a second time, a timestamp `[[ =~ ]]`, and a
`printf … >> "$MERGE_FILE"` that reopens the merge file per line. It scales with
today's signal volume, which grows ~28,000 log lines/day across the fleet.

Test fidelity (concrete, hence a finding rather than a caveat):
`test_large_pre_today_body_renders_correctly_within_time_bound` asserts a 5 s
bound over a fixture with 300,000 **pre-today** lines and exactly **two**
today-lines. It measures the half of the fix that works and mocks away the half
that does not, which is why a green unit test coexists with a 12 s production
render.

Related but distinct: `PRIORITY-0004-event-band-signal-dedup` (open) reports
that ~97 % of the surviving lines are one repeated `sweep-census: … skipped`
sentence. Fixing that would incidentally shrink this render, but the per-line
constant factor is a defect on its own terms.

### Finding 6: a unit test's verdict depends on the verifier's scratch directory path

**Category:** bug
**Fix Effort:** small (1–2 CODER tasks)
**Systemic Risk:** medium (the environment-coupled-fixture class has now cost
more than one release candidate; `PRIORITY-0002-env-coupled-fixture-detector`
was filed against exactly this class and this instance was not caught)
**Filed As:** BUG-0341-env-coupled-debug-log-routing-test (Path C-A — filed as
BUG: code defect / regression / test failure)

`team/tests/unit/test_debug_log_routing.py::test_project_less_debug_log_routes_to_root_dir`
line 289 asserts `"/projects/" not in resolved_path` against an absolute path
rooted at `PGAI_AGENT_KANBAN_TEMP_DIR`. The production code routes correctly —
the preceding `startswith(kanban_root / "logs" / "debug")` assertion passes —
but the substring check is satisfied by the caller's own scratch prefix.

Proven by two runs of the same file at the same commit differing only in that
variable: `PGAI_AGENT_KANBAN_TEMP_DIR=/tmp/pgai_kanban_tmp/tester020clean` →
`12 passed`; `…=/tmp/pgai_kanban_tmp/projects/tester020dirty` →
`1 failed, 11 passed`.

Whole-suite effect: the first `run-unit-tests.sh --coverage` of this
verification used the scratch root the TESTER dispatch prompt names
(`/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/…`) and exited **1**
(`1 failed, 4873 passed, 31 skipped`). Re-run under a temp dir without the
substring it exited **0** (`4874 passed, 31 skipped`). The authoritative
`UNIT_EXIT` recorded below is the second run's; the first is documented here in
full so the discrepancy is not mistaken for a flake.

Pre-existing: the file is not in the `ai_main..ai_rc/v2.24.3` diff. BUG-0191 and
BUG-0314 name the file but are both `done` and describe different defects, so
this is filed rather than cited.

### Finding 7: requirements-doc acceptance criteria

**Category:** pass

- "Each bundled item is addressed in the implementation" — BUG-0334 (Findings 1
  and 2), BUG-0335 (Finding 3), BUG-0336 (Finding 4), BUG-0337 (Finding 5,
  partially: three of four checkboxes).
- "All Python files compile cleanly" — `python3 -m py_compile` over every `.py`
  in the tree, exit 0, no output.
- "All bash files pass syntax check" — `bash -n` over every `.sh` in the tree,
  zero failures.

### Finding 8: release-state, structural and constraint checks

**Category:** pass

- `release-state.md` holds exactly the four canonical fields.
  `## Active RC` = `v2.24.3`, `## RC Opened At` = `2026-09-19T15:36:24+00:00`,
  `## RC Opened By Task` = `CM-20260919-019-open-rc-2-24-3`,
  `## Last Released` = `v2.24.2` (well-formed). `pp_last_released_version` returns
  `v2.24.2` and `semver_gt v2.24.3 v2.24.2` is true — checked through the shared
  helper, never compared inline.
- Pre-flight: `git rev-parse HEAD` == `git rev-parse ai_rc/v2.24.3` (`e4b1966`),
  `git status --porcelain` empty, `git stash list` empty. No `DONE` task in
  `tasks/` names a feature branch that still exists locally and is not an
  ancestor of the RC.
- Census: both new anchors (`lock-stale-threshold-floor-01`,
  `lock-provenance-reverify-01`) are present in `docs/blocking-site-census.md`
  with Locator Index entries. `lint_gate_census_compliance.py` → `0 offenders`,
  exit 0.
- Skip-cites-a-real-bug (Step 6.13): `lint_skip_bug_gate.sh` → `0 missing files`,
  `all skip annotations are compliant`, exit 0. No `BUG-SKIP` placeholder shapes
  in the test tree.
- Documentation Discipline: `kanban.cfg_example` (new `[dashboard]
  event_band_read_bound_lines` and `[locks]` notes), `project.cfg_example`,
  `docs/DASHBOARD-PANES.md`, `docs/blocking-site-census.md` and
  `config_loader.sh`'s Key Registry all updated in the same RC as the code.
- `--help` presence: `event-band.sh --help` exits 0 and documents
  `PGAI_DASHBOARD_EVENT_BAND_READ_BOUND_LINES`; `status-band.sh --help` exits 0.
- Constraint negatives: no hardcoded secret, password, API key or token
  introduced anywhere in the diff. The two changed executables keep
  `set -euo pipefail`; the six changed library files correctly do not set it.
- Step 6.8 (synthetic release simulation): **skipped with note** — the diff
  touches no release-lifecycle file. `cm-release.sh`, `cm-open-rc.sh`,
  `cm-cancel-rc.sh` and `team/scripts/lib/project_paths.sh` are all unchanged
  between `ai_main` and `ai_rc/v2.24.3`.
- Step 6.10 (prereq graph): acyclic, every referenced task ID resolves to a real
  folder, bookends correct — `CM-20260919-019-open-rc-2-24-3` (DONE) at the
  start, `TESTER-20260919-020-verify-2-24-3` then
  `CM-20260919-022-release-2-24-3` (WAITING) at the end. No sequence collisions.

## Gated Suites

| suite | command | exit | result |
|---|---|---|---|
| unit | `bash team/scripts/run-unit-tests.sh --coverage` | **0** | 4874 passed, 31 skipped (757.52s) |
| integration | `bash team/scripts/run-integration-tests.sh --coverage` | **0** | 519 passed, 1 skipped (544.50s) |

`UNIT_EXIT=0`, `INTEGRATION_EXIT=0`. Both are the gated runners' own exit codes,
not a pytest summary line. A first unit run exited 1 on the environment-coupled
test described in Finding 6; both suites were then re-run end to end under a
scratch root free of the coupling trigger, and the table above is that clean run.
The integration suite exited 0 in both runs.

## Coverage (informational only)

| suite | statement % | branch % | blended % |
|---|---|---|---|
| unit | 76.3 | 63.1 | 73.3 |
| integration | 11.4 | 9.2 | 10.9 |

Read from `totals` in `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/{unit,integration}.json`,
field names verified against the generated files
(`percent_statements_covered`, `percent_branches_covered`, `percent_covered`).
Coverage does not enter the verdict.

## Lint Findings (non-blocking)

None. `findings-unit.json` and `findings-integration.json` both carry an empty
`findings` array (`schema_version: 1`). Independently, `lint_hygiene.py` across
all three default classes and `lint_gate_census_compliance.py` each report 0
findings.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=12d freshness=stale — the ledger's newest record is twelve days old and names v2.9.20 rather than the v2.24.3 under verification, so it fails both freshness conditions; its red verdict was already filed as BUG-0167-cold-build-claude-abliterated-failure (status `done`), and nothing is currently known about whether this tree's three Dockerfiles build.

## Autonomous criterion

Manual interventions required: none.

1. **Tasks force-promoted from WAITING to BACKLOG by hand?** NO. The one
   promotion in this cycle is this task's own, and its `status.md` records
   `Promoted from WAITING to BACKLOG by wake script: prerequisites satisfied`.
   `CM-20260919-022-release-2-24-3` is still `WAITING` on this verification.
2. **`release-state.md` edited outside the CM scripts?** NO. Its mtime is
   `15:36`, matching its own `## RC Opened At` of `2026-09-19T15:36:24+00:00`,
   i.e. written by `cm-open-rc.sh` and untouched since.
3. **Scripts patched mid-build?** NO. Every `team/scripts/` path in the diff
   (`dashboard/event-band.sh`, `dashboard/status-band.sh`,
   `dashboard/lib/event_signal_patterns.sh`, `lib/lock_provenance.sh`,
   `lib/wake_driver.sh`, `lib/wake_driver_finalize.sh`, `lib/why_details.sh`,
   `lib/config_loader.sh`) is the ticketed deliverable of a CODER task, landed
   on its own feature branch and merged `--no-ff`. No live-install script under
   `$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/` was modified during the RC window.
4. **Queue files edited by hand?** NO. `coder_backlog.md` (18:29) and
   `tester_backlog.md` (18:28) carry wake-script marker transitions only;
   `cm_backlog.md` (15:37) and `pm_backlog.md` (15:35) match the RC-open and
   decomposition wakes.

All five RC tickets ran to a terminal state unattended: four `DONE`, one
(`CODER-20260919-018-provenance-hygiene-sweep`) `WONT-DO` with a correct
justification — its target finding had already been healed by a sibling ticket's
merge, its feature branch carried zero commits ahead of the RC, and
`roles/CODER.md` PHASE 2 prescribes `WONT-DO` for exactly that shape. That is
the autonomy mechanism working, not a shortfall.

## Caveats

- **`kanban.cfg` mtime moved mid-RC (17:16:45), cause not established.** The
  file is the operator's own configuration, not a script, a queue file, a task
  state or `release-state.md`, so it maps to none of the four Step 6.12 checks
  and is recorded here as an observation rather than a finding. Its contents
  read as normal on inspection — no test-fixture values, no missing sections,
  and the `[wake]` values it holds are the ones the new stale-lock floor derives
  from. No log line names a writer at that second. Worth an operator glance;
  not evidence of anything by itself.
- **Event-band output volume.** The band prints ~7000 lines against the live
  logs. That is pre-existing behaviour (the band has no output cap) and is the
  subject of the open `PRIORITY-0004-event-band-signal-dedup`, not a finding
  against this RC.
- **Untimestamped continuation lines are stamped `TODAY` at wall-clock `now`.**
  `event-band.sh`'s `else` branch assigns `date_part="$TODAY"` and
  `time_part="$(date +%H:%M:%S)"` to any surviving line without a parseable
  leading `[ISO8601]` prefix. Within a 20,000-line tail this means a
  continuation line belonging to a *previous* day's entry renders as today's.
  Verified unchanged by this RC (the branch is identical on `ai_main`), so it is
  a pre-existing wart recorded for the record, not a regression. It is also the
  sole reason big-vs-small fixture output differs in line ordering.
- **Deep-verify lane not run.** Per Step 5d, TESTER runs no container build; the
  freshness line above is a report of the ledger's contents. The tree does ship
  `team/scripts/deep-verify.sh`, so no gap is owed there.
