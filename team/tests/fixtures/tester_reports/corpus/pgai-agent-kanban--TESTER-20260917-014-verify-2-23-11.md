# Verification Report: rc/v2.23.11

## Summary

Verified release candidate `ai_rc/v2.23.11` (HEAD `f371e14`) of pgai-agent-kanban
against `requirements/v2.23.11-bugfix-bundle-20260917.md` — a three-item BUG
bundle (BUG-0307 agent-metrics mis-aggregation, BUG-0308 window-4 roster order,
BUG-0309 untested schedule pane floors) decomposed into four CODER tickets
(009, 010, 011, 012). Pre-flight was clean: HEAD matches `ai_rc/v2.23.11`, the
worktree is clean, no stashes, and every DONE task's feature branch was deleted
after merging (all four merges are present on the RC as `--no-ff` commits). Both
gated runners exited 0 with zero lint findings. All three bundled bugs were
verified behaviorally, not structurally: per-agent wall aggregation was exercised
against a hand-computed fixture task tree, the rendered table was checked against
those hand-computed values on both the new and the legacy rollup schema, window-4
roster ordering was observed stable across 78 seconds spanning a minute boundary,
and every new guard was mutation-tested in an isolated clone (reverting the sort
and hardcoding each pane floor each make the new tests fail). All 489 Python files
compile and all 281 bash files pass syntax check. Four findings were recorded —
none of them affecting the fixes themselves: five task-ID provenance citations
that reached shipped test source, the hygiene lint's task-ID pattern that let them
through, a new `create.sh` flag missing from the script's usage block, and a
pre-existing intermittent full-suite flake CODER reported and no bug tracked.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Findings

Glossary for the classification tokens used below: `Path C` (autonomous
follow-up filing — non-blocking), `Path C-A` (filed as BUG — code defect /
regression / test failure), `Path C-B` (filed as PRIORITY — enhancement /
design change), `BLOCKED` (verification could not complete — infrastructure
failure; NOT "found a blocker").

### Finding 1: Pre-flight — RC integrity

**Category:** pass

`git rev-parse HEAD` = `f371e1497587354961b66c19157a4b7ffe93cd93` =
`git rev-parse ai_rc/v2.23.11`. `git status --porcelain` empty.
`git stash list` empty. Scanned every task folder under
`projects/pgai-agent-kanban/tasks/`: no DONE task names a `## Feature Branch`
that still exists locally, which is the normal post-merge shape (CODER deletes
after merging). All four cycle feature branches are present on the RC as
`--no-ff` merge commits: `fcb4452` (009), `d53dbae` (010), `5c556e5` (011),
`f371e14` (012). No branch failed Check 1 or Check 2.

### Finding 2: `release-state.md` schema and version ordering

**Category:** pass

Four fields present and well-formed: `## Active RC` = `v2.23.11`,
`## RC Opened At` = `2026-09-17T02:34:58+00:00`, `## RC Opened By Task` =
`CM-20260917-013-open-rc-2-23-11`, `## Last Released` = `v2.23.10`.
`pp_last_released_version "pgai-agent-kanban"` returns `v2.23.10`, and
`semver_gt "v2.23.11" "v2.23.10"` (from `team/scripts/lib/semver.sh`) succeeds —
the RC under test is strictly greater than the last released version. No inline
string comparison was used.

### Finding 3: Gated suites

**Category:** pass

Both runners were executed from the worktree root with `--coverage`, output tee'd
under `$PGAI_AGENT_KANBAN_TEMP_DIR/tester/`:

- `bash team/scripts/run-unit-tests.sh --coverage` → `UNIT_EXIT=0`
  (`4678 passed, 31 skipped in 835.39s`, `Unit tests PASSED`,
  `bare_tmp_cleanliness: OK`, `listener_cleanliness: OK`).
- `bash team/scripts/run-integration-tests.sh --coverage` → `INTEGRATION_EXIT=0`
  (`515 passed, 1 skipped in 575.18s`, `Integration tests PASSED`,
  `bare_tmp_cleanliness: OK`, `listener_cleanliness: OK`).

The PASS evidence for the suite precondition is these two exit codes, not any
pytest summary line. Direct pytest was used later only as a debugging aid for
mutation experiments in an isolated clone, and no pytest exit code is quoted as
a verdict anywhere in this report.

Coverage (informational only, never part of the verdict). Field names were read
from the generated JSON rather than assumed:

| Suite | statement % | branch % | blended % |
|---|---|---|---|
| unit | 76.59 | 63.13 | 73.48 |
| integration | 9.87 | 7.97 | 9.43 |

Source: `percent_statements_covered`, `percent_branches_covered`, and
`percent_covered` in the `totals` object of
`/tmp/pgai_kanban_tmp/coverage/unit.json` and `.../integration.json`.

### Finding 4: BUG-0307 — per-agent wall aggregation (CODER-009)

**Category:** pass

Behavioral, not structural. Built a fixture kanban root with a task tree whose
`tokens.json` files carry known `elapsed_seconds`, then ran
`metrics_aggregator.aggregate_rc()` over it.

Fixture: coder 600 + 1200 + 1800 s over 3 tasks; tester 300 + 900 s over 2;
cm 120 s over 1; pm two tasks, one omitting `elapsed_seconds` and one carrying
the string `"abc"`.

Result:

```
tasks.by_agent: {"cm": 1, "coder": 3, "pm": 2, "tester": 2}
wall.by_agent : {"cm": {"seconds": 120, "task_count": 1},
                 "coder": {"seconds": 3600, "task_count": 3},
                 "tester": {"seconds": 1200, "task_count": 2}}
```

Every figure equals the hand-computed sum and count. `pm` is absent from
`wall.by_agent` entirely — neither a zero entry nor an exception — while still
present in `tasks.by_agent` with 2 tasks, which is the required split. The
top-level `wall_time_minutes` and the `tasks.by_agent`/`tokens.by_agent` shapes
are untouched by the diff. Real rollups written before this change
(`projects/pgai-agent-kanban/metrics/rc/*.json`, e.g. `v2.9.9.json`, whose key
set carries no `wall`) load through the readers without error — exercised below
in Finding 5. `_extract_elapsed_seconds()` rejects `bool` explicitly, which
matters because `json.loads` returns `True`/`False` as an `int` subclass.

### Finding 5: BUG-0307 — averaging denominators and rendering (CODER-010)

**Category:** pass

`compute_agent_averages()` over the Finding 4 rollup returns per-agent
`avg_wall_minutes` of coder 20.0, tester 10.0, cm 2.0, and `null` for pm —
matching 3600/3/60, 1200/2/60 and 120/1/60 exactly. No two agents share a value.
Rendering the same data through `team/scripts/lib/agent_metrics.py`'s
`render_project_block()`:

```
Agent         Avg Wall (min/task)  Avg Tokens (/RC)  Avg Tasks (/RC)
cm                              2               150                1
coder                          20               450                3
pm                             --               300                2
tester                         10               300                2
```

Every averaged column header names its own denominator, and the chosen
denominator for Avg Tasks (per invocation RC) is documented in the module
docstring, in `docs/operator-api.md`, and in `docs/DASHBOARD-PANES.md`.

The legacy contract was exercised two ways. Stripping the `wall` key from the
same rollup renders `--` in the wall column for all four agents with no
exception. Rendering the operator's real rollup directory — ten RCs, none of
which has the field — likewise gives `--` for cm/coder/pm/tester/writer while
the token and task columns still populate. This is the specific defect BUG-0307
reported (four agents printing an identical impossible 151 min): the column can
no longer print a wrong shared number, because the RC-level `wall_time_minutes`
is no longer read at all.

CLI/REST parity: `team/pgai_agent_kanban/api/tests/test_cli_rest_agent_metrics_parity.py`
passes (4 tests), and its fixture was strengthened so coder (30 min/task) and
writer (45 min/task) share their invocation RCs but must render different
figures — a regression to the RC-level join would make them equal and fail the
assertion. `rc-metrics.sh` and `show-metrics.sh` are byte-unchanged in the diff,
per the constraint that rc-metrics is already correct.

The ICD artifact is consistent: running `bash team/scripts/generate-icd.sh`
leaves `git status --porcelain` empty, so the committed `docs/api/icd.json` and
the re-frozen `docs/api/baselines/icd-v1.7.0.json` match a fresh generation.
Editing the `1.7.0` baseline is the documented behavior of the generator
("Regeneration also re-freezes the current-version baseline … whether or not the
regeneration accompanied a version bump"), and the change is description prose
only — no path, field, or enum was removed, so the additive-only promise holds.

### Finding 6: BUG-0308 — window-4 roster ordering (CODER-011)

**Category:** pass

No crontab is installed in this environment (`crontab -l` → "no crontab for
rocky"), so the renderer was exercised against a PATH-shimmed fixture crontab
carrying the seven documented roster entries, the same mechanism the suite's own
`_sandbox_crontab` fixture uses.

Ordering is stable and pm-first. Six runs spanning 78 seconds — deliberately
crossing a minute boundary so both the even-minute (pm/cm/tester) and
odd-minute (coder/writer) stagger groups took their turn at being soonest —
produced the identical row order every time:

```
PM CM TESTER CODER WRITER OVERWATCH CLEANUP
```

Under the previous soonest-first sort the top row would have changed to CODER
during odd minutes. It never moved. All seven roster entries render; no row is
lost.

Guard exercised by triggering the failure mode, in an isolated clone of the RC
(not the verification worktree): re-inserting `int_entries.sort(key=lambda x:
x[1])` makes `test_next_cron_firings_renders_seven_rows_pm_first` and
`test_next_cron_firings_pm_first_stable_across_real_time` both fail, with the
first reporting `['TESTER','CM','PM','CODER','WRITER','OVERWATCH','CLEANUP']`
against the expected roster order. Reverting the mutation returns the module to
16/16 green. The new assertions therefore do not depend on the contrived
within-the-minute-second-59 epoch that BUG-0308 identified as the reason the old
test passed vacuously; one of the two new tests uses no clock stub at all.

Documentation landed in the same commit: `next-cron-firings.sh`'s file header no
longer claims rows are sorted ascending by time-remaining, states the new
ordering, and states the truncation order explicitly (bottom-drop over a
roster-ordered list drops the weekly entries first, then works back up through
CM/TESTER/WRITER/CODER, regardless of which fires soonest). The same truncation
statement appears in `docs/DASHBOARD-PANES.md` and in
`team/config/dashboard_windows.census`'s window-4 pane-0 description. The
window-0 HEADER tile is untouched, as required.

### Finding 7: BUG-0309 — schedule pane floor coverage (CODER-012)

**Category:** pass

All of BUG-0309's hand-verified assertions are now pinned, and I re-verified each
by hand against the production code rather than reading the tests alone.

Roster count helper, against PATH-shimmed fixture crontabs:

| Input | `dashboard_schedule_roster_count` |
|---|---|
| seven current agent entries | 7 |
| plus a synthetic eighth entry | 8 |
| empty crontab | 0 |
| `crontab -l` exiting non-zero | 0 |
| missing `cron_parser.py` path | 0 |

The two creation-time floors, read through the new `--print-schedule-floors`
diagnostic path:

```
7-entry crontab:  SCHEDULE_ROSTER_COUNT=7  HEADER_MIN_H=10  SCHED_FIRINGS_MIN_H=9
8-entry crontab:  SCHEDULE_ROSTER_COUNT=8  HEADER_MIN_H=11  SCHED_FIRINGS_MIN_H=10
```

These match BUG-0309's stated expectations (`HEADER_MIN_H` = 7 + 3,
`SCHED_FIRINGS_MIN_H` = 7 + 2) and track the eighth entry, confirming the
pre-OVERWATCH hardcoded-five assumption is gone.

Guard exercised by triggering the failure mode, twice, in the isolated clone.
Replacing `SCHEDULE_ROSTER_COUNT` with a literal `5` in `HEADER_MIN_H` fails
`TestScheduleCreationFloors::test_floors_match_roster_count_plus_reserved_rows`
and `::test_floors_track_a_synthetic_eighth_roster_entry` (`assert 8 == (8 + 3)`).
The same mutation applied to `SCHED_FIRINGS_MIN_H` fails the same two tests
(`assert 7 == (7 + 2)`). Both reverted cleanly to 16/16 green. This is the exact
regression BUG-0309 said would pass unnoticed on the previous tree, and it no
longer does. The floor assertions are computed from roster count plus each
renderer's reserved-row constant, never hardcoded, as the constraint required.

`grep -rn "dashboard_schedule_roster_count" team/tests/` and
`grep -rn "HEADER_MIN_H\|SCHED_FIRINGS_MIN_H" team/tests/` each return hits.
The production change is confined to hoisting the two floor computations to a
single site and adding the diagnostic flag; the resize blocks now only clamp up
to them, which removes the previous duplicated arithmetic.

### Finding 8: Requirements-doc acceptance criteria

**Category:** pass

- *Each bundled item is addressed in the implementation* — BUG-0307 by
  Findings 4 and 5, BUG-0308 by Finding 6, BUG-0309 by Finding 7.
- *All Python files compile cleanly* — 489 `*.py` files, `python3 -m py_compile`
  across all of them, zero failures.
- *All bash files pass syntax check* — 281 `*.sh` files, `bash -n` on each, zero
  failures.

### Finding 9: Constraint negative checks and skip-gate

**Category:** pass

`bash team/scripts/lint_skip_bug_gate.sh` exits 0: no placeholder skip IDs, and
`BUG-NNNN file-existence check: 0 missing files`. A manual grep for `BUG-SKIP`
across `team/tests/` and `team/pgai_agent_kanban/` returns nothing, and the RC
diff adds no new skip annotation. No hardcoded secret, credential, or banned
pattern appears in the added lines. The RC's shell changes preserve their
existing `set -euo pipefail` headers.

### Finding 10: Prerequisite dependency graph

**Category:** pass

`CM-013` (open RC) → `CODER-009` and `CODER-011`; `CODER-009` → `CODER-010`;
`CODER-011` → `CODER-012`; all four CODER tasks → `TESTER-014` (this task);
those four plus `TESTER-014` → `CM-016` (release). No cycles. Every referenced
task ID resolves to an existing folder. Lifecycle bookends are correctly linked:
CM-open at the start, TESTER plus CM-release at the end, with `CM-016` currently
`WAITING` behind this task. Queue markers match states exactly — the four CODER
entries are `[x]`, `CM-013` is `[x]`, `CM-016` is `[W]`, and `TESTER-014` is
`[A]`. The sequence number 015 is unused in this cycle; that is a gap in
numbering, not a collision, and the two `-002-` suffixes today belong to
different agents (`CODER-20260917-002` and `PM-20260917-002`), so no task ID
collides.

### Finding 11: Synthetic release simulation

**Category:** pass-with-caveat

Skipped by rule, with a note. Step 6.8 applies only when the diff touches
release-lifecycle files. The RC diff touches `team/scripts/dashboard/` and
`team/scripts/lib/` only; `cm-release.sh`, `cm-open-rc.sh`, `cm-cancel-rc.sh`,
and `team/scripts/lib/project_paths.sh` are all unchanged, and nothing in the
open/close cycle is modified. `team/tests/integration/test_cm_release_synthetic.py`
was therefore not run separately — it ran as part of the green integration
suite in Finding 3.

### Finding 12: Internal task IDs cited in shipped test source

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (five comment lines in two test modules; no runtime effect)
**Filed As:** BUG-0311 (Path C-A — filed as BUG)

Five comment/docstring lines added by this RC cite the internal work ticket that
produced the code instead of describing the behavior:

```
team/pgai_agent_kanban/api/tests/test_cli_rest_agent_metrics_parity.py:100
team/tests/unit/test_agent_metrics.py:135
team/tests/unit/test_agent_metrics.py:234
team/tests/unit/test_agent_metrics.py:354
team/tests/unit/test_agent_metrics.py:407
```

all of the shape `ticket CODER-009's field`. `SOP-core.md` ("Naming: describe
behavior, not scaffolding or provenance") states the rule directly: "let no
internal bug identifier, task ID, RC, or framework-version citation reach a
shipped artifact — git history holds the 'why', the artifact the meaning." None
of the five fits one of the three sanctioned exceptions. `git grep -n "CODER-009"
ai_main -- '*.py'` returns nothing, so all five are introduced by this RC.

This traces to a constraint, not to scope creep: `docs/coding-standards.md`
compliance is a Step 6.14 check, and the naming rule is core law binding every
name and comment an agent authors.

Worth recording that the rule was otherwise honoured in this cycle — commit
`d526b9e` reworded a different batch of citations, and the two `BUG-0307`
references that `BUG-0310` recorded at `test_agent_metrics.py:442` and `:518`
were reworded before the merge. Only the abbreviated shape survived, for the
reason in Finding 13.

### Finding 13: The provenance lint does not recognise short-form task IDs

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (the gate will keep passing this class of citation on every future RC, so Finding 12 recurs by default)
**Filed As:** BUG-0312 (Path C-A)

`team/scripts/lint_hygiene.py`'s `provenance` class matches task IDs with

```python
_TASK_ID = re.compile(r"\b(?:CODER|WRITER|TESTER|CM|PM|PO)-\d{8}-\d+\b", re.IGNORECASE)
```

The mandatory eight-digit date segment means only the full
`<ROLE>-YYYYMMDD-NNN` spelling is a violation; the abbreviated `<ROLE>-NNN`
spelling passes. The sibling patterns in the same list do not carry that
restriction — `_BUG_ID` is `\bBUG-\d+\b` and `_PRIORITY_ID` is
`\bPRIORITY-\d+\b` — and neither the lint's own remedy text nor `SOP-core.md`
qualifies the rule by spelling.

Demonstrated by triggering the difference rather than inferring it. In an
isolated clone of the RC, `python3 team/scripts/lint_hygiene.py --classes
provenance` reports `OK — 0 findings` while five short-form citations are
present. Changing exactly one of them from `CODER-009` to `CODER-20260917-009`
and re-running the identical command reports it:

```
team/tests/unit/test_agent_metrics.py:234: [task-ID] provenance reference
'CODER-20260917-009' in code — comments and output must describe behavior,
not internal IDs
```

The only difference between the silent and the reported run is the date segment.
This is the mechanism that let Finding 12 ship with a green quality pre-flight
and empty `findings-unit.json` / `findings-integration.json`.

Rated `medium` on systemic risk rather than `low` because it is not RC-specific:
until the pattern is widened, any agent writing the natural shorthand produces an
undetected violation, and the loop has no other check that would catch it.
Rated `medium` and not `high` because nothing is broken at runtime and the
existing long-form detection still works.

### Finding 14: `create.sh --print-schedule-floors` is absent from the script's usage block

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (one flag on one script; the flag itself works)
**Filed As:** BUG-0313 (Path C-A)

CODER-012's constraints required that "if a production signature or tunable
changes to enable testing, its usage comment and any example file are updated in
the same commit", and `SOP-core.md`'s Documentation Discipline Rule names "that
script's README or usage comment" as the matching artifact for a script-behavior
change. The new `--print-schedule-floors` flag was added to the parser and to the
unknown-argument error string at line 249, but not to the header comment's
`Usage:`/`Flags:` block at lines 126-134 — which is the block `-h|--help` prints.

```
$ bash team/scripts/dashboard/create.sh --help | grep -c "print-schedule-floors"
0
```

A compounding pre-existing condition is worth naming for whoever fixes it: the
`-h|--help` branch prints only the first 30 lines of the header comment
(`"${__help_lines[@]:0:30}"`) and the usage block sits around line 126, so
`--help` today names none of the flags — `--no-tmux`, `--kanban-root` and
`--session` are equally invisible. Updating the header block is necessary but
not sufficient to make the flag discoverable through `--help`. The pre-existing
half is recorded in BUG-0313 as context; the part this RC owns is the omitted
header entry.

Categorised `gap` rather than `bug` because the flag itself behaves correctly —
what is unmet is a documentation constraint the ticket carried explicitly.

### Finding 15: Pre-existing intermittent full-suite failures, untracked

**Category:** bug (pre-existing)
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (it degrades the one authoritative pass/fail signal every future verification depends on, and it recurs on any tree carrying this module)
**Filed As:** BUG-0314 (Path C-A)

`CODER-20260917-012-schedule-floor-coverage`'s `status.md` records, and
explicitly hands to TESTER, an intermittent full-suite failure of four to six
tests unrelated to its own work — concentrated in
`team/tests/unit/test_lint_changelog_freshness.py`, once in
`test_debug_log_routing.py` — green in isolation, with the symptom of a stray
`v99.0.0` CHANGELOG section. A grep of `projects/pgai-agent-kanban/bugs/` for
`changelog_freshness`, `v99.0.0`, `intermittent` and `flake` finds only BUG-0162
and BUG-0163, both `done` and both describing different defects. Nothing tracked
it, so it is filed now rather than silently carried.

**I did not reproduce it.** Three full unit runs during this verification were
green: the gated `run-unit-tests.sh --coverage` (4678 passed, 31 skipped, exit
0), and a direct `pytest tests/unit -p no:randomly` reproduction attempt (4289
passed, 29 skipped). The direct run is a debugging aid and is not quoted as PASS
evidence anywhere; Finding 3 carries the verdict.

What I did establish is the mechanism, by reading the module rather than by
inference from the symptom.
`test_rc_mode_tolerates_new_release_section_addition` writes a real
`release-notes/v99.0.0.md` into the checked-out repository root — not into
`tmp_path`:

```python
_DEV_TREE_ROOT = _TEAM_DIR.parent              # the checked-out repository root
_RELEASE_NOTES_DIR = _DEV_TREE_ROOT / "release-notes"
synthetic_notes = _RELEASE_NOTES_DIR / "v99.0.0.md"
```

The file is removed in a `finally`, and its own docstring says "This file must
never appear in the real release-notes directory." That teardown covers the happy
path only. While the test runs, any other test in the session that regenerates or
compares `CHANGELOG.md` against `release-notes/` genuinely sees a `## v99.0.0`
section — which is the reported symptom, and which explains why an isolated
`PGAI_AGENT_KANBAN_TEMP_DIR` does not help (the leak is in the source tree, not
the temp root). And a `finally` does not run on SIGKILL or a hard timeout, so an
interrupted suite leaves the checkout dirty; in this framework that has a second
cost, because TESTER's Step 3 pre-flight sets `BLOCKED` on a non-empty
`git status --porcelain`.

The same module resolves `_REAL_BUGS_DIR` to the operator's live bug ledger, so
its inputs change whenever anything files a bug — including a TESTER Path C
filing made while a suite is running.

Pre-existing, not a regression:
`git diff --stat ai_main..ai_rc/v2.23.11 -- team/tests/unit/test_lint_changelog_freshness.py`
is empty. Recorded as `pre-existing` per Step 5a and filed because it was not
already tracked. It does not reflect on the four CODER tickets in this bundle.

This is also the one test-fidelity concern I can point at concretely, in the
sense of the "Test fidelity verification" section: the tests covering this RC's
own changes exercise real breaking conditions rather than sanitized proxies —
the roster-order and pane-floor guards were each confirmed by mutation, and the
metrics tests assert that two agents sharing invocation RCs render different wall
figures — but the suite they run inside has a cross-test coupling that can make
any of their verdicts unreliable in a given run.

## Lint Findings (non-blocking)

None. Both `${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-unit.json` and
`.../findings-integration.json` were written by their runners and each records
`"findings": []` (`schema_version: 1`). `python3 team/scripts/lint_hygiene.py
--classes provenance` was also run directly and reports `OK — 0 findings across
8 scan director(y/ies)`.

That last result is itself the subject of Finding 13: the class is green because
its task-ID pattern does not match the citations that are present, not because
none are present. That is recorded as a Step 7 `gap` on the normal tree and filed
as BUG-0312, not laundered into this section — the distinction matters, because
findings in this section move nothing, and Finding 13 does elevate the
recommendation.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=10d freshness=stale — Both freshness conditions fail: the record is 10 days old against a 7-day window and it was taken at v2.9.20 rather than the v2.23.11 under verification, and the run that produced this red verdict filed BUG-0167 (now `done`) for the `docker/claude-abliterated` and `podman/rocky9` failures, so nothing is re-filed here.

Read through `deep_verify_ledger_read_latest` in
`$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/lib/deep_verify_ledger.sh`, which returned
0; the verdict token is copied from the ledger verbatim and not re-derived. The
cold-build lane was not run — `team/scripts/deep-verify.sh` and
`run-cold-build-tests.sh` were not invoked, no container engine was started, and
the tree does ship `docker/claude-abliterated/Dockerfile`,
`docker/debian/Dockerfile` and `docker/rocky9/Dockerfile`, so this section is
mandatory. `team/scripts/deep-verify.sh` is present and executable, so neither of
Step 5d's two gap faults applies. Staleness raises no systemic risk and does not
affect the recommendation.

## Autonomous criterion

Manual interventions required: none. All four checks answer NO.

1. **Were any tasks force-promoted from WAITING to BACKLOG by hand?** NO. All
   four promotions appear in the wake log as the script's own work:
   `wake(coder): WAITING task CODER-20260917-009-…: prerequisites satisfied,
   promoting to BACKLOG` at 02:36:15, the same for `-011` at 02:36:15, `-010` at
   02:56:08, and `-012` at 04:30:23 — each after its prerequisite completed. No
   `status.md` in the cycle carries a hand-forced state, and every one of the
   five ends with `Blockers: none` and `Needs Human: no`.
2. **Was `release-state.md` edited outside the CM scripts?** NO. Its mtime is
   `2026-09-17 02:34:58.530529241 +0000`, identical to the `## RC Opened At`
   value `open-rc.sh` wrote, and it has not been touched since. `## Last
   Released` still reads `v2.23.10`, which is correct for an RC that has not
   shipped.
3. **Were any scripts patched mid-build?** NO. Every commit touching
   `team/scripts/` on the RC is a ticket deliverable inside its own CODER feature
   commit: `b51d3d9` (metrics_aggregator.py), `01c5ef5` (agent-metrics.sh,
   lib/agent_metrics.py), `ad918cb` (next-cron-firings.sh), `b4c941e`
   (create.sh). Nothing under `subagents/` changed. `git log --since=
   "2026-09-17T02:30:00" ai_main` is empty, so no out-of-band commit landed on
   the main branch while the RC was in flight.
4. **Were any queue files edited by hand?** NO. Markers match states exactly
   (four `[x]` CODER entries, `[x]` CM-013, `[W]` CM-016, `[A]` TESTER-014), and
   the mtimes track agent completions rather than an operator session —
   `coder_backlog.md` at 06:54:33, ninety seconds after the final merge at
   06:52:56, and `tester_backlog.md` at 10:25:35, this task's own wake.

`$PGAI_AGENT_KANBAN_ROOT_PATH/logs/activity.log` records no entry for
2026-09-17, so no host-level software change accompanied this build.

## Caveats

- **The Avg Wall column will read `--` for every agent on the operator's real
  dashboard until new rollups accumulate.** This is correct behavior, not a
  shortfall: every existing rollup under
  `projects/pgai-agent-kanban/metrics/rc/` predates the `wall.by_agent` field,
  and CODER-010's constraint required exactly this ("a window containing only
  rollups written before ticket 1's field existed must still render, with the
  wall column showing the placeholder rather than a wrong number"). The operator
  should expect the column to stay empty for roughly one window of RCs rather
  than to light up on install. Verified by rendering the real rollup directory
  through the new reader.
- **`BUG-0310` is open but its recorded instances no longer exist.** It is a
  hygiene-lint aggregate bug naming two `BUG-0307` citations at
  `team/tests/unit/test_agent_metrics.py:442` and `:518`; both were reworded
  before the merge, and a grep of the RC for `BUG-0307`, `BUG-0308` and
  `BUG-0309` across `team/tests/`, `team/pgai_agent_kanban/` and `team/scripts/`
  returns nothing. Its `## Status` is still `open`, so discovery will keep
  treating it as eligible for re-bundling. Recorded as an observation for CM
  rather than a finding — ledger status is not TESTER's to change, and nothing
  about the RC is wrong.
- **Task sequence number 015 is unused in this cycle** (`CM-013`, `TESTER-014`,
  `CM-016`). A gap in numbering, not a collision; noted only because Step 6.10
  asks about sequence consistency.
- **Two `-002-` task IDs exist for today** — `CODER-20260917-002-wake-roster-render`
  and `PM-20260917-002-decompose-v2-23-11-bugfix-bundle-2026091`. Sequence
  numbers are per-agent in practice and the full task IDs are distinct, so no
  folder or queue entry collides.
- **No crontab is installed in this environment.** `crontab -l` reports "no
  crontab for rocky", so window-4 ordering could not be observed against the
  operator's own installed schedule as BUG-0308's reproduction steps describe.
  Everything in Findings 6 and 7 was exercised against a PATH-shimmed fixture
  crontab carrying the seven documented roster entries, which is the same
  mechanism the suite's `_sandbox_crontab` fixture uses. The conclusion — order
  is derived from crontab position rather than time-remaining — does not depend
  on which crontab is installed, but a reader should know the real one was not
  available.
- **All mutation experiments ran in an isolated clone**, created with
  `git clone --no-hardlinks --branch ai_rc/v2.23.11`, never in the verification
  worktree and never by copying it. The worktree's `git status --porcelain` is
  empty at the end of this run, as it was at the start, and HEAD is unmoved at
  `f371e14`.
- **Prior-run artifacts:** none. `artifacts/` was empty at the start of this run
  and no `report.md.previous-RUN-N` exists, so this is a first run and no earlier
  verdict was consulted or carried forward.
