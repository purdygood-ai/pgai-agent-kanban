# Verification Report: rc/v2.23.10

**Date:** 2026-09-17
**Tester:** TESTER (agent: tester, model: claude-fable-5-1)
**Scope:** `ai_rc/v2.23.10` at commit `ca59e3e` (3 CODER merges) verified against
`requirements/v2.23.10-bugfix-bundle-20260917.md` and its single bundled item,
`bugs/BUG-0306-schedule-next-fire-truncation.md`.
**Worktree:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260917-006-verify-2-23-10`

---

## Executive Summary

v2.23.10 is a one-item bug bundle: the next-fire schedule roster rendered taller
than the two panes that display it, so rows scrolled off the top with no signal
anything had been cut. The RC fixes it in three layers — the roster is now read
from the parsed crontab instead of a hardcoded six-name tuple, both renderers
fit their rows to the hosting tmux pane's queryable height and narrate any drop
with a `+N more` marker, and `create.sh` floors both panes at creation time to
roster length plus each renderer's reserved-row count. Both gated runners are
green. I exercised the guard behaviorally in real tmux panes at seven different
heights in both display homes: every row is either visible or counted, and at
each pane's creation-time floor the full seven-row roster renders untruncated.
Two gaps remain, both minor and both filed: window 4 still sorts soonest-first
rather than pinning `pm` to the top row (the bug's acceptance asked for `pm`
first; PM flagged the conflict at decomposition and no ticket changed the sort),
and the creation-time pane floors ship with no automated coverage. Neither
threatens the release. Recommendation: SHIP-WITH-CONCERNS.

---

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

low

---

## Test Suites (the authoritative pass/fail signal)

Both run with `--coverage` from the dev-tree root inside the RC worktree; output
teed under `/tmp/pgai_kanban_tmp/tester/`.

| Runner | Exit | Result |
|---|---|---|
| `bash team/scripts/run-unit-tests.sh --coverage` | `UNIT_EXIT=0` | PASS — 4656 passed, 31 skipped in 770.42s; lint pre-flight clean; `bare_tmp_cleanliness: OK`, `listener_cleanliness: OK` |
| `bash team/scripts/run-integration-tests.sh --coverage` | `INTEGRATION_EXIT=0` | PASS — 515 passed, 1 skipped in 592.86s; lint pre-flight clean; both cleanliness checks OK |

The RC's own new module was collected and passed inside the gated run:
`tests/unit/test_schedule_roster_completeness.py ........` (8 tests, 8 passed).

PASS evidence is the two runner exit codes above, not a pytest summary line.
No direct-pytest invocation was used as a verdict source anywhere in this run.

### Coverage (informational only — never enters the verdict)

Extracted from the `totals` object of each runner's JSON, field names verified
against the generated files before extraction.

| Suite | line % | branch % | blended |
|---|---|---|---|
| unit (`/tmp/pgai_kanban_tmp/coverage/unit.json`) | 76.55 % | 62.99 % | 73.42 % |
| integration (`/tmp/pgai_kanban_tmp/coverage/integration.json`) | 9.88 % | 8.00 % | 9.45 % |

---

## Acceptance Criteria

### From the requirements doc

| # | Criterion | Result | Notes |
|---|---|---|---|
| 1 | Each bundled item is addressed in the implementation | PASS (with the two gaps below) | BUG-0306's Fix items 1 and 2 fully implemented and behaviorally verified; item 3 partially (renderers covered, allotment not) |
| 2 | All Python files compile cleanly | PASS | `python3 -m compileall -q .` exit 0 across the tree; `py_compile` on the new test module exit 0 |
| 3 | All bash files pass syntax check | PASS | `bash -n` over every `*.sh` in the tree (`.git` excluded): zero failures; the six changed shell files checked individually |

### From the bundled item (`BUG-0306`)

| # | Criterion | Result | Notes |
|---|---|---|---|
| A1 | Window 0 tile and window 4 header each show all 7 current rows | PASS | Verified in real tmux panes at each home's creation-time floor — see Finding 2 |
| A1b | ... `pm` first | PARTIAL | Window 0 yes (crontab line order); window 4 no (soonest-first sort retained) — Finding 4, BUG-0308 |
| A2 | Synthetic extra row: overflow marker appears; no silent loss | PASS | Verified with a PATH-shimmed 8-entry crontab and in real short panes — see Findings 2 and 3 |
| A3 | Doc-truth tour matches the as-built row list | PASS-WITH-CAVEAT | Census-generated tables regenerate byte-identical; the tour carries no enumerated row list to drift — Finding 6 |

---

## Constraint Verification

| Constraint | Status | Notes |
|---|---|---|
| `tester_operation: verify-rc` | MET | Release-workflow methodology run end to end |
| `target_version: v2.23.10` | MET | `v2.23.10` strictly greater than `pp_last_released_version "pgai-agent-kanban"` = `v2.23.9`, compared via `semver_gt` from `team/scripts/lib/semver.sh` — never an inline string compare |
| `set -euo pipefail` preserved in changed scripts | MET | Present in all four changed executables (`next-cron-firings.sh`, `show-header.sh`, `show-multi.sh`, `create.sh`) |
| No hardcoded secrets introduced | MET | Case-insensitive scan of the added lines for `api_key` / `password` / `secret` / `token =`: zero hits |
| No provenance in shipped artifacts (`SOP-core.md` naming rule) | MET | Scan of the added lines for `BUG-NNNN` / task IDs / version citations: zero hits. Commit `cac7e93` deliberately reworded the viewport comments off the filing bug ID |
| Cleanup trap preserved in `next-cron-firings.sh` | MET | The `trap "rm -f ..." EXIT` over the two parser temp files is intact and unmoved by the diff |
| Temp-file convention | MET | All scratch under `/tmp/pgai_kanban_tmp/...`; both runners' `bare_tmp_cleanliness` checks OK |

---

## Findings

### Finding 1: Pre-flight — RC intact and worktree clean

**Category:** pass

- `git rev-parse HEAD` = `git rev-parse ai_rc/v2.23.10` = `ca59e3e4b5680526855aa01635a442da35acafcd`.
- `git status --porcelain` empty; `git stash list` empty.
- All three prerequisite CODER tasks read `## State: DONE`. All three feature
  branches are absent locally (`git branch --list 'ai_feature/*'` returns zero),
  the normal post-merge shape, so Step-2 checks 1 and 2 pass trivially. No
  `ai_feature/*` branch exists anywhere in the repo, so no DONE task anywhere in
  the project has an unmerged branch.
- `git log --oneline ai_main..ai_rc/v2.23.10` shows the expected three
  `--no-ff` merge commits plus their four content commits, nothing else.
- Diff scope: 9 files, +794/−23 — `docs/DASHBOARD-PANES.md`,
  `team/config/dashboard_windows.census`, `create.sh`,
  `lib/next_fire_viewport.sh` (new), `next-cron-firings.sh`, `show-header.sh`,
  `show-multi.sh`, `lib/dashboard_constants.sh`,
  `team/tests/unit/test_schedule_roster_completeness.py` (new).

### Finding 2: The viewport-fit guard fires correctly — every row visible or counted

**Category:** pass

Step 6.5 behavioral verification. The guard exists to *prevent* silent row loss,
so I triggered the loss rather than reading the code for it. Real tmux panes on a
private socket under `$PGAI_AGENT_KANBAN_TEMP_DIR`, not the stubbed `tmux` the
RC's own tests use; the installed roster is 7 agents
(`dashboard_schedule_roster_count` → `7`, matching the live crontab).

**Window 4 — `next-cron-firings.sh`** (`SCHED_FIRINGS_MIN_H` = 7 + 2 = 9):

| pane height | rows shown | marker | shown + dropped |
|---|---|---|---|
| no tmux (piped) | 7 | none | 7 ✓ |
| 40 | 7 | none | 7 ✓ |
| 10 | 7 | none | 7 ✓ |
| **9 (the floor)** | **7** | **none** | **7 ✓** |
| 8 | 5 | `+2 more` | 7 ✓ |
| 5 | 2 | `+5 more` | 7 ✓ |
| 3 | 0 | `+7 more` | 7 ✓ |

**Window 0 — `show-multi.sh --mode header`** (delegates to `show-header.sh`;
`projects.cfg` holds exactly one project, so this delegation is the live path;
`HEADER_MIN_H` = 7 + 3 = 10):

| pane height | rows shown | marker | shown + dropped |
|---|---|---|---|
| 40 | 7 (`pm` first) | none | 7 ✓ |
| **10 (the floor)** | **7 (`pm` first)** | **none** | **7 ✓** |
| 9 | 5 | `+2 more` | 7 ✓ |
| 6 | 2 | `+5 more` | 7 ✓ |
| 4 | 0 | `+7 more` | 7 ✓ |

Every case reconciles exactly. At each home's creation-time floor the full roster
renders with no marker, which is the point of the floors. Truncation drops from
the bottom, so the top rows — the ones BUG-0306 reported losing — always survive.
`OVERWATCH` now appears in window 0, which it never did before: the stale
`CRON_AGENTS=("pm" "coder" "writer" "tester" "cm" "cleanup")` tuple in
`show-header.sh` is gone, replaced by first-seen key order captured live from
`cron_firings.py`'s JSON.

I also confirmed the "cannot query" path does not truncate to a guess: with
`TMUX_PANE` unset the budget is empty and `next_fire_fit_lines` prints every line
unchanged.

### Finding 3: The roster count is derived from the schedule source, not hardcoded

**Category:** pass

`dashboard_schedule_roster_count()` exercised directly against a PATH-shimmed
`crontab` so the synthetic-8th-entry case from BUG-0306's acceptance could be
reached without touching the host crontab:

| crontab fed to the helper | result |
|---|---|
| the real installed crontab (7 agent entries) | `7` |
| shimmed, 8 entries (synthetic `extra`) | `8` |
| shimmed, empty | `0` |
| real crontab, `cron_parser.py` path missing | `0` |
| shimmed `crontab -l` exiting non-zero | `0` |

The degradation cases all return `0` rather than the pre-OVERWATCH literal five,
exactly as the function's contract comment states. No literal roster count
survives anywhere: `create.sh` derives `SCHEDULE_ROSTER_COUNT` once, and
`show-multi.sh`'s multi-project block now iterates `result.items()` instead of its
own six-name tuple. That multi-project block is a single joined text line rather
than a row list, so no viewport fit applies to it and none is owed.

I also verified the sourcing order that makes this work: `create.sh` sources
`lib/dashboard_constants.sh` at line 170 and first calls the helper at line 490.

### Finding 4: Window 4 renders soonest-first, not `pm`-first

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated to one pane's sort order; every row is visible)
**Filed As:** BUG-0308 (Path C-A: filed as BUG — code defect / regression / test failure)

BUG-0306's acceptance criterion 1 reads "Window 0 tile and window 4 header each
show all 7 current rows, **pm first**". Window 0 satisfies both halves. Window 4
satisfies the visibility half but keeps its pre-existing soonest-first sort, so
its top row is whichever agent is nearest its next stagger slot and changes every
few seconds:

```
$ bash team/scripts/dashboard/next-cron-firings.sh --no-color
=== Next Agent Wake (incl. stagger) ===

WRITER   in 0:01
TESTER   in 0:13
PM       in 0:25
CODER    in 0:37
CM       in 0:49
OVERWATCH Thu 3am
CLEANUP  Sun 4am
```

The criterion is genuinely ambiguous and I am recording the ambiguity rather than
resolving it. "Top row first" in the bug's Expected section reads as *do not lose
the top rows*, which is satisfied; "pm first" in Acceptance reads as an ordering
promise, which is not. The ambiguity is not mine alone: the decomposition already
named it — `tasks/PM-20260917-001-.../status.md` records "The bug's acceptance
'pm first' changes next-cron-firings.sh's current soonest-first sort to a fixed
roster order; the plan states the required end state and leaves the ordering
mechanism to CODER." No CODER ticket then changed the sort, and
`next-cron-firings.sh`'s file header has documented soonest-first since before
this RC. Categorized `gap` per the conservative default.

One test-fidelity note, since it would otherwise look like coverage:
`test_schedule_roster_completeness.py::TestFullRosterRendersEveryRow::test_next_cron_firings_renders_seven_rows_pm_first`
does assert `pm` first for window 4, but reaches that order by pinning
`date +%s` to an epoch whose within-the-minute second is 59, at which instant
pm's unstaggered remaining time happens to be smallest. The assertion passes
while production ordering stays soonest-first. The module docstring is explicit
about the mechanism, so this is a scope question rather than a concealed test —
but it means the suite does not currently pin the ordering criterion either way.

### Finding 5: The creation-time pane floors ship with no automated coverage

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (behavior is correct today; the risk is future regression, and the runtime marker means any regression is visible rather than silent)
**Filed As:** BUG-0309 (Path C-A)

Step 6.11 "untested new mechanisms". Three new production mechanisms have zero
test references:

```
$ grep -rn "dashboard_schedule_roster_count" --include='*.py' team/tests/     -> no hits
$ grep -rn "HEADER_MIN_H\|SCHED_FIRINGS_MIN_H" --include='*.py' team/tests/   -> no hits
$ grep -rn "PGAI_SCHEDULE_.*_RESERVED_ROWS" --include='*.py' team/tests/      -> no hits
```

The shipped test module covers the *renderers* (runtime viewport fit, with a
stubbed tmux pane height). It never covers the *allotment* — the creation-time
floors whose whole purpose is to make the runtime marker unnecessary on a
freshly-built dashboard. That is BUG-0306's Fix item 2, and Fix item 3 asked for
coverage of "both homes". Delete both floor guards from `create.sh` and
`run-unit-tests.sh` still exits 0.

The behavior is correct — I verified all of it by hand (Findings 2 and 3), and the
CODER's own pre-DONE check is recorded in
`tasks/CODER-20260917-003-schedule-pane-allotment/status.md`. It is simply
unpinned. BUG-0309 carries the concrete assertions worth encoding and notes that
the team test tree's autouse `_sandbox_crontab` fixture already provides the
crontab-shim mechanism a test would need.

### Finding 6: Doc truth is in sync; the tour carries no row list to drift

**Category:** pass-with-caveat

`python3 team/scripts/dashboard/generate_window_docs.py` re-run against the RC
reports all three targets "already up to date" and leaves the tree clean
(`git status --porcelain` empty afterwards), so `create.sh`'s window-order
comment, `docs/DASHBOARD-PANES.md` and `docs/dashboard.md` all match
`team/config/dashboard_windows.census` byte-for-byte. The census pane-0 text for
both windows 0 and 4 was updated to state the roster-sized floor, and the
generated tables carry it.

Caveat, so the PASS is not read as stronger than it is: BUG-0306 acceptance 3
asks that the "doc-truth tour matches the as-built row list", but
`docs/dashboard.md`'s window-0 and window-4 entries never enumerate the roster
rows — window 4 is described only as "when the agents fire next". So there was no
stale row list to correct and the criterion is satisfied vacuously rather than by
a change. Nothing to fix; recorded so a later reader does not assume the tour was
audited against a list it does not contain.

### Finding 7: New-file close reading — `lib/next_fire_viewport.sh`

**Category:** pass

Read top to bottom (new file, 69 lines, Principle 3). Source guard present and
idempotent. `next_fire_pane_height` queries only through `$TMUX_PANE` plus a
`command -v tmux` check and validates the result against `^[0-9]+$` before
echoing — no `tput lines` terminal-size guess, which is what makes the piped and
`--no-tmux` paths print in full instead of truncating to something wrong.
`next_fire_fit_lines` treats an empty or non-numeric budget as "unknowable" and
prints everything; the `show = budget - 1` arithmetic reserves exactly one row
for the marker so total output never exceeds the budget; `dropped` is computed
from `total - show`, so the marker count and the visible rows always reconcile to
the roster size, which is the invariant Finding 2 confirms empirically at seven
heights. `(( show < 0 )) && show=0` covers `budget=0`. The reserved-row accounting
in both callers matches the named constants (`next-cron-firings.sh` base 2, +2
more only while the HALT footer prints; `show-header.sh` 3), and those constants
carry comments stating they must stay equal to the callers' own accounting.

### Finding 8: Autonomous operation criterion — no manual intervention

**Category:** pass

See the dedicated section below.

### Finding 9: Skip-cites-a-real-bug gate

**Category:** pass

`bash team/scripts/lint_skip_bug_gate.sh` exit 0 — "all skip annotations are
compliant", 0 missing `BUG-NNNN` files. Independent grep for placeholder shapes
(`BUG-SKIP`) across `team/tests/`: zero hits. The RC's new test module adds no
skips.

### Finding 10: Coding-standards spot-check

**Category:** pass

Five minutes against `docs/coding-standards.md`. `next_fire_viewport.sh` is a
sourced library with no executable entry point, so no `--help` is owed; the two
callers' existing `--help` handlers are unchanged. No bug, task, RC or version
identifier reaches any shipped comment, docstring, log line or output string (the
scan under Constraint Verification). The two new reserved-row values are named
constants in `lib/dashboard_constants.sh` with inline documentation, consistent
with the file's existing entries, and they are internal accounting rather than
operator tunables, so no `*_example` update is owed under the Documentation
Discipline Rule. The two viewport helpers are new behavior with no existing
`lib/` equivalent, so no duplicate implementation; the gated `lint_lib_function_dedupe`
pre-flight agrees.

### Finding 11: Release-state sanity and prerequisite graph

**Category:** pass

`release-state.md` holds all four fields, well-formed: `Active RC` `v2.23.10`
(consistent with the actual `ai_rc/v2.23.10` branch — no state drift),
`RC Opened At` `2026-09-17T00:42:12+00:00`, `RC Opened By Task`
`CM-20260917-005-open-rc-2-23-10`, `Last Released` `v2.23.9`.
`## Last Released` is written by `cm/release.sh` by design and is not reported as
drift. Version answers came from `pp_last_released_version`, and the
greater-than check used `semver_gt`.

Prerequisite graph over the seven cycle tasks: acyclic, every referenced task ID
resolves to an existing folder, bookends correctly linked — `CM-...-005-open-rc`
with no prerequisites at the head, this TESTER task gated on all three CODER
tickets, `CM-...-008-release` gated on the three CODER tickets plus TESTER at the
tail. Queue markers all render their task's own state correctly: `[x]` on the five
finished tasks, `[A]` on this one, `[W]` on the waiting release task.

---

## Lint Findings (non-blocking)

None.

Both findings files were present and empty:
`/tmp/pgai_kanban_tmp/lint/findings-unit.json` and `.../findings-integration.json`
each carry `"findings": []` with `schema_version: 1`, generated at
`2026-09-17T01:59:08+00:00` and `2026-09-17T02:09:28+00:00` respectively.

---

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=9d freshness=stale — Stale on both counts: the record is 9 days old against the 7-day window and it was taken at v2.9.20, not the v2.23.10 under verification; its red verdict was the `claude-abliterated` Dockerfile `FROM`-arity failure plus a `podman/rocky9` `COPY --chmod` failure, already filed and closed as BUG-0167 (`## Status: done`), so nothing is re-filed here.

Read through `deep_verify_ledger_read_latest` in
`$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/lib/deep_verify_ledger.sh` (reader exit 0);
the ledger markdown was not parsed by hand and the verdict was not re-derived.
The cold-build lane was not run — that is CM's DEEP-VERIFY tier. The tree does
ship Dockerfiles (`docker/claude-abliterated/`, `docker/debian/`, `docker/rocky9/`),
so this section is mandatory, and `team/scripts/deep-verify.sh` is present and
executable, so neither Step-5d gap fault applies. Staleness is context, not a
finding: it raises no systemic risk and does not move the recommendation.

---

## Autonomous criterion

`Autonomous operation criterion: Manual interventions required: none`

| # | Check | Answer |
|---|---|---|
| 1 | Any task force-promoted from WAITING to BACKLOG by hand, bypassing the wake prerequisite evaluation? | NO |
| 2 | `release-state.md` edited outside `cm-open-rc.sh` / `cm-release.sh` / `cm-cancel-rc.sh`? | NO |
| 3 | Any script patched mid-build — a commit touching `team/scripts/`, `subagents/` or other tooling while the RC was in flight? | NO (in the sense the check means: every `team/scripts/` change in this RC *is* the RC's assigned deliverable, landed through the three CODER feature-branch merges, not an out-of-band repair) |
| 4 | Any queue file edited by hand? | NO |

Evidence: all five completed cycle tasks read `## State: DONE` with
`## Needs Human: no`, `## Blockers: none` and `## Zero Progress` at
`count: 0 / limit: 3` — nothing was retried, so nothing needed rescuing.
`CM-20260917-005`'s summary records `scripts/cm/open-rc.sh` exiting 0 as the sole
writer of the four `release-state.md` fields, whose current values match that
record exactly. The RC's git history contains only the expected three `--no-ff`
merge commits and their four content commits — no stray fixup or tooling commit.
Every queue marker matches its task's own `## State`, the shape the wake
reconciliation writes. Three prerequisite-chained CODER tickets ran in sequence
without a hand promotion between them.

Two observations, neither an intervention: the cycle's task sequence numbers skip
`007` (the slot the materializer reserves for the optional HUMAN-APPROVE task,
unused because the bundle sets `Human Approval Required: auto`), and
`PM-20260917-001`'s `## Temp Litter` section reports one bare-`/tmp` entry,
`/tmp/pgai-agent-kanban-project.tar.gz`, from the PM run. Litter is a hygiene
report, not a failure, and both gated runners' own `bare_tmp_cleanliness` checks
passed for this RC.

---

## Steps skipped, and why

- **Step 6.8 (synthetic release simulation)** — skipped by rule. The diff touches
  no release-lifecycle file: no `cm-release.sh`, `cm-open-rc.sh`,
  `cm-cancel-rc.sh`, `team/scripts/lib/project_paths.sh`, or anything in the
  open/close cycle. Nine changed files, all dashboard rendering, census/docs, or
  tests.
- **Cold-build / deep-verify lane** — not run, per Step 5d. Its freshness is
  reported above from the ledger; TESTER starts no container engine.

---

## Caveats

- **Window-0 geometry is over-subscribed on the short-terminal fallback path, and
  the HEADER pane wins.** Raising `HEADER_H`'s floor to 10 means `create.sh`'s
  window-0 left column asks tmux for more rows than the window has whenever
  `MAIN_HEIGHT` is below 26 (at `MAIN_HEIGHT=24`: 10 + 8 + 5 = 23 requested
  against 22 available). Pre-RC the same arithmetic only over-subscribed at
  `MAIN_HEIGHT=17`. This is not reachable on a normal run — `create.sh` creates
  the session explicitly at 220x50, where the sum fits exactly — and is reachable
  only through the `MAIN_HEIGHT=${MAIN_HEIGHT:-24}` guard when the tmux query
  returns empty. I reproduced that case in a real 24-row tmux window: tmux
  honored the HEADER pane's full 10 rows and absorbed the 1-row shortfall in the
  LOGS pane (6 instead of 8), so the full roster still rendered and the schedule
  fix held. Recorded as an observation rather than a finding — it fits none of
  Step 6.11's three categories and costs a sibling pane two rows on a path that
  requires a failed tmux query.
- **The RC's own tests stub tmux entirely**, answering only
  `display-message`, so the suite never exercises a real
  `tmux display-message -p -t "$TMUX_PANE" '#{pane_height}'`. That is a
  reasonable determinism choice and the docstring justifies it, but it means the
  suite alone would not catch a change in how tmux reports pane height. I closed
  that specific hole by hand in Finding 2 (real panes, real tmux 3.2a, seven
  heights across both homes) and separately confirmed that
  `tmux resize-pane -y 10` does yield `#{pane_height}` = 10, so the floors are
  exactly sufficient rather than off by one. No concrete fidelity defect to file.
- **`BUG-0306`'s `## Status` is still `running`.** Expected at this point:
  `cm-release.sh` Step 16b sets the bundle and each bundled item to `done` after
  the tag. Not a finding; noted so CM can confirm the transition happens.
- **The live install carries three `scripts/dashboard/lib/` files absent from the
  RC** (`column_snapshot.sh`, `defer_posture.sh`, `pane_cadence.sh`) — leftovers
  from older versions, since `install.sh`'s `install_path` merges and never
  deletes. Pre-existing, unrelated to this RC, out of scope; mentioned only
  because I checked that path while confirming the new
  `lib/next_fire_viewport.sh` will ship (it will — `install.sh`'s team-item loop
  copies `scripts` as a directory tree with `cp -r "$src/." "$dst/"`, so the new
  file merges in, and both callers resolve it through `${SCRIPT_DIR}/lib/`, which
  is a valid path in the installed layout).
- **Two open bugs precede this RC and are untouched by it:** BUG-0307
  (`agent-metrics-avg-wall-and-tasks`, `open`) and BUG-0306 itself (`running`,
  this bundle). Nothing in this RC regressed either.
