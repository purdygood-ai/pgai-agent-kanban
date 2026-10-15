# Verification Report: rc/v2.24.4

Verified the release candidate `ai_rc/v2.24.4` (commit `3eb79da`) against its
requirements document `requirements/v2.24.4-priority-bundle-20260919.md` and
the single item that bundle carries, `PRIORITY-0004-event-band-signal-dedup`.
The RC adds per-pattern collapse metadata to the dashboard's shared event
signal library, folds repeated collapse identities in the event band into
bounded rows, and documents the rule in the window-4 prose. Both gated
runners were run with `--coverage` from the worktree root and both exited 0.
Every acceptance criterion in the priority item and in the bundle was
exercised, including the behavioral ones, against a production-shaped
fixture built for this verification (1,800 byte-identical sweep-census lines
round-robined across five agent logs, interleaved with 60 distinct events) and
against an `ai_main` baseline render of the same fixture for differential
comparison. Seven criteria pass. One defect was found by failure-mode probing
that the RC's own test fixtures do not cover — a signal-class line containing
a literal tab garbles the folded row and merges two distinct collapse
identities into one, silently dropping the second event — and one process
constraint was found violated, self-disclosed by the CODER that did it.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

low

## Findings

### Finding 1: Bounded row count against a production-shaped repetition fixture

**Category:** pass

`PRIORITY-0004` acceptance criterion 1. Built a fixture matching the shape the
priority measured in production: 1,800 byte-identical
`sweep-census: skipped — .../v2.24.1-bugfix-bundle-20260918.md: finalize=tag — not report`
lines spread across all five agent logs (360 each), plus 60 distinct signal
events (20 dispatches, 20 completions, 20 promotions) and 2,500 lines of
idle-cycle boilerplate. Rendered with a throwaway kanban root per the
live-install safety rule.

| Render | Total rows | sweep-census rows | Distinct-event rows |
|---|---|---|---|
| `ai_main` baseline | 1860 | 1800 | 60 |
| `ai_rc/v2.24.4` | 61 | 1 | 60 |

Every one of the 60 distinct events renders; the repetition costs exactly one
row. Render time 1.6 s wall clock.

### Finding 2: The repeated line's occurrence count is stated in the output

**Category:** pass

`PRIORITY-0004` acceptance criterion 2. The folded row from Finding 1 reads:

```
[pm 00:00:00] wake(pm): sweep-census: skipped — $HOME/.../v2.24.1-bugfix-bundle-20260918.md: finalize=tag — not report (x1800, first seen 00:00:00)
```

The count is the whole-day total across every agent, not a per-agent or
per-run count, and the first-seen time and agent are the earliest occurrence
in the merged stream. A reader can tell it happened 1,800 times.

### Finding 3: No other signal class changes its rendering

**Category:** pass

`PRIORITY-0004` acceptance criterion 3, verified differentially rather than by
inspection. Extracted `ai_main`'s `team/scripts` into a scratch tree, rendered
the identical fixture through both versions, and diffed the non-sweep-census
rows: byte-for-byte identical, zero differences across all 60 rows. No
distinct-event row carries a fold suffix.

Separately probed the inverse failure mode: a non-collapsing class repeated
25 times per agent (`promoted 3 task(s) from WAITING to BACKLOG`, five agents,
125 occurrences) renders all 125 occurrences, confirming that absence of
collapse metadata means "render every occurrence" and that the fold is opt-in
per pattern rather than a default.

### Finding 4: Adding a new pattern remains a single-line edit

**Category:** pass

`PRIORITY-0004` acceptance criterion 4 and goal 3, verified behaviorally. Took
an RC-tip copy of `team/scripts`, added exactly one line
(`'brand-new-class: fired'`) to `EVENT_SIGNAL_PATTERNS` and nothing else —
no renderer edit, no collapse-metadata entry — and rendered a fixture of 12
occurrences. All 12 rendered, in chronologic order, with no fold suffix, exit
0. The new collapse metadata lives in `EVENT_SIGNAL_COLLAPSE_KEYS` beside the
pattern array in the same library, keyed by the literal pattern string, and
`event-band.sh` reaches it only through the
`dashboard_event_signal_collapse_key` accessor — no per-class special case
leaks into the renderer.

### Finding 5: Doc-truth and census lints clean

**Category:** pass

`PRIORITY-0004` acceptance criterion 5, both commands run from the worktree
root:

- `python3 team/scripts/dashboard/generate_window_docs.py --check` → exit 0,
  "all surfaces match the census."
- `python3 team/scripts/lint_doc_command_truth.py` → exit 0, 0 findings across
  all three passes (657 citations / 13 documents; key-parity 169 citations;
  pane-parity 47 window/pane citations).

The window-4 prose in `docs/DASHBOARD-PANES.md` and `docs/dashboard.md` was
read in full against the implemented behavior. The shipped example row shape
(`(x37, first seen 06:02:11)`) matches what the renderer actually emits, and
the "hides lines" promise below the pane table was correctly widened to "hides
and folds lines" so the collapse is stated as a property of the view only. One
prose claim is contradicted by Finding 8 — see there.

### Finding 6: Both gated runners exit 0

**Category:** pass

`PRIORITY-0004` acceptance criterion 6, plus the Step 8 suite precondition.
Run with `--coverage` from the worktree root:

- `bash team/scripts/run-unit-tests.sh --coverage` → `UNIT_EXIT=0`;
  4883 passed, 31 skipped, 808.79 s. Lint pre-flight passed, bare-`/tmp`
  residue check OK, listener-leak check OK.
- `bash team/scripts/run-integration-tests.sh --coverage` → `INTEGRATION_EXIT=0`;
  519 passed, 1 skipped, 543.39 s. Same pre-flight and post-run checks OK.

PASS evidence here is the two runner exit codes, not a pytest line. Direct
pytest against the two changed test modules does not run outside the runner's
environment (`ModuleNotFoundError: No module named 'tests'`), which is
expected and is not a finding — the gated runners are the authoritative
signal and both are green.

Coverage, informational only and not part of the verdict: unit suite 76 %
statements / 63 % branches (73 % blended); integration suite 11 % statements /
9 % branches (11 % blended). Extracted from the `totals` object in
`/tmp/pgai_kanban_tmp/coverage/{unit,integration}.json`, field names verified
against the generated files.

### Finding 7: Bundle-level criteria met

**Category:** pass

The bundle document's own three criteria:

- *Each bundled item is addressed* — `PRIORITY-0004` is the only bundled item;
  its three goals and six acceptance criteria are covered by Findings 1–6, and
  its scope list (`event-band.sh`, `lib/event_signal_patterns.sh`, both doc
  files, both test modules) matches the RC diff exactly, with nothing outside
  it touched. The two named out-of-scope items were respected: the wake driver
  still logs what it logged, and `sweep-census` remains a signal class.
- *All Python files compile cleanly* — `python3 -m compileall -q team docs`
  exits 0; both changed test modules compile individually.
- *All bash files pass syntax check* — `bash -n` exits 0 on both changed
  shell files.

Constraint negative checks (Step 6.9): `set -euo pipefail` preserved at
`event-band.sh:60`; no secret-shaped assignments in either changed script; no
provenance tokens (bug ID, task ID, RC or version citation) in any changed
script, test, or document — `python3 team/scripts/lint_hygiene.py --classes provenance`
reports 0 findings across 8 directories. Skip-cite gate (Step 6.13):
`bash team/scripts/lint_skip_bug_gate.sh` → "OK — all skip annotations are
compliant", 0 missing files, and no placeholder skip shapes in the RC's tests.
Help presence (Step 6.14): `event-band.sh --help` exits 0 and prints the new
collapse paragraph.

### Finding 8: A tab in a signal line garbles the folded row and merges distinct collapse identities

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated to one renderer's scratch-file format)
**Filed As:** BUG-0343 (Path C-A — filed as BUG: code defect / regression / test failure)

`event-band.sh` writes five tab-separated scratch columns
(`sort_key`, `agent`, `display_time`, `collapse_key`, `rest`) and reads them
back with `IFS=$'\t' read`. The `collapse_key` column is new in this RC and
holds arbitrary captured log text. When a signal line contains a literal tab,
the collapse key itself contains a tab, the column stops being one `read`
field, and everything after the embedded tab shifts into `rest` — the field
the band prints.

Two consequences, found by probing the failure mode rather than by the RC's
own fixtures, which use no tab-bearing line:

1. The row is garbled. Rendered output places the tail of the collapse key
   first, then the whole original line, then the fold suffix.
2. **A distinct event is silently dropped.** The identity truncates at the
   first embedded tab, so two sweep-census events differing only after the tab
   collapse to one identity. Reproduction: 3 ALPHA occurrences + 4 BETA
   occurrences, both tab-bearing, render as **one** row carrying `(x7, ...)`,
   with BETA absent entirely — 2 rows with `(x3, ...)` and `(x4, ...)` expected.

This is a regression against `ai_main`, confirmed differentially: the same
tab-bearing fixture renders correctly and in full on the pre-RC renderer,
because `rest` was then the last column and `read`'s overflow-into-last-field
behavior kept it intact. Inserting a second free-form column ahead of it broke
that property.

It contradicts `PRIORITY-0004` goal 2 ("No signal class is silently dropped")
and the prose this RC shipped in `docs/DASHBOARD-PANES.md` ("a different
bundle or a different stated reason is a different identity and gets its own
row").

Rated **medium**, not high, against `SOP-core.md` ("Rating a Finding") and its
calibration note: the triggering input does not occur in production today.
Nineteen lines across the five live `logs/cron-*.log` files contain tabs (pm 0,
coder 13, writer 2, tester 4, cm 0 — all git-status-shaped output), and none of
them are signal-class lines, so nothing currently depends on the broken path.
The band is also a view-only surface: nothing in the release pipeline, the wake
chain, or task state reads it, so a defect here cannot corrupt state or stop
the chain. Full reproduction steps are in the filed bug.

### Finding 9: A non-CM agent ran a remote git operation during the build

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (one self-disclosed instance; no state affected)
**Filed As:** PRIORITY-0005 (Path C-B — filed as PRIORITY: enhancement / design change)

`SOP-core.md` ("Git Repositories: Roles and Boundaries") states that CM is the
only toucher of any remote and that no other agent pushes, pulls, or fetches.
Task `CODER-20260919-023-collapse-key-metadata` ran `git fetch --all` in its
task worktree during this build and recorded it in its own `status.md`
Summary, citing DIRECTIVES rule 5.

Impact verified independently, and it matches the agent's account: the RC
branch, `ai_main`, and the local `origin/main` remote-tracking ref all sit
where the pipeline put them, and `git reflog show origin/main` records the ref
moving only "by push", never by a fetch. No release state, queue, or task
state was affected. Nothing was damaged.

Recorded as a `gap` against the constraint rather than as a defect in the RC's
own work, because the constraint was not held and the report should say so
plainly. The substance of the follow-up is not the instance but that the
boundary is enforced by nothing and detected by nothing — it surfaced only
because the agent volunteered it, and Step 6.12's four checks do not cover a
remote git operation. `git fetch` is the benign end of a range the same
unenforced rule includes `git push` in.

### Finding 10: A single occurrence of a collapsing class still carries a fold suffix

**Category:** pass-with-caveat

A sweep-census line occurring exactly once renders as
`... not report (x1, first seen 11:00:00)`, where the first-seen time is the
row's own timestamp. This is consistent with the shipped prose ("carrying the
occurrence total and that first-seen time") and violates no acceptance
criterion — criterion 3 exempts the collapsing class itself — so it is not a
gap. Noted because `PRIORITY-0004` observes that a sweep-census skip "is a
real signal the first time it appears", and on a quiet day the band now shows
that first appearance with redundant `(x1, first seen <its own time>)`
decoration. A future iteration may prefer to suppress the suffix at count 1.

### Finding 11: The `--help` slice length is a hand-maintained magic number

**Category:** pass-with-caveat

`event-band.sh`'s help path slices its own header comment to a literal line
count, bumped from 48 to 58 in this RC to keep the new collapse paragraph
visible. Verified working: `--help` exits 0 and prints 58 lines ending on a
complete sentence, with the collapse paragraph present at lines 22–29 (header
is 164 comment lines, so the slice is doing real work). The constant must be
re-tuned by hand on every header edit or help output silently truncates. The
status note records that sibling renderers in the same directory each carry
their own independently-set constant, so this is a pre-existing directory-wide
pattern rather than something this RC introduced, and it is correct as shipped.

## Lint Findings (non-blocking)

None. Both findings files were present and empty:
`/tmp/pgai_kanban_tmp/lint/findings-unit.json` and
`/tmp/pgai_kanban_tmp/lint/findings-integration.json`, each
`{"findings": [], "schema_version": 1}`.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=12d freshness=stale — Stale on both counts: the record is 12 days old against a 7-day window and was taken at v2.9.20, not the v2.24.4 under verification; the run that produced it filed BUG-0167-cold-build-claude-abliterated-failure (now `done`), cited here rather than re-filed, and nothing is known about whether this RC's images build.

## Autonomous criterion

Step 6.12, all four checks answered from task `status.md` files, git commit
authors and timestamps, and file mtimes.

1. **Any task force-promoted from WAITING to BACKLOG by hand?** NO. Every
   queue-file mtime falls on a wake-script cycle boundary; the promotion note
   in this task's own `status.md` is the wake script's own text.
2. **`release-state.md` edited outside the CM scripts?** NO. Its mtime is
   `2026-09-19 19:29:44`, matching `## RC Opened At` to the second and the
   `open-rc.sh` invocation `CM-20260919-026-open-rc-2-24-4` recorded. All four
   fields are well-formed; `## Last Released` is `v2.24.3`, and `v2.24.4` is
   strictly greater, confirmed with `semver_gt` from
   `team/scripts/lib/semver.sh` against `pp_last_released_version`, never an
   inline compare.
3. **Any scripts patched mid-build?** NO. The six commits between `ai_main`
   and the RC tip are three ticketed feature commits and their three `--no-ff`
   merges, in prerequisite order, all within `PRIORITY-0004`'s declared scope.
   No out-of-band tooling commit.
4. **Any queue files edited by hand?** NO.

**Manual interventions required: none.** Finding 9 records a swim-lane
deviation by a task agent, not a human intervention on the pipeline, and it
falls outside all four checks — which is itself the substance of PRIORITY-0005.

## Caveats

- **Pre-flight.** Worktree HEAD `3eb79da8c58e788b45a5621dbd405733530cebb5`
  equals `ai_rc/v2.24.4`; `git status --porcelain` empty; `git stash list`
  empty. Scanned all 636 task folders: every `DONE` task naming a feature
  branch has had that branch deleted post-merge, which passes both Step 2
  checks trivially. No HALT flag at project or root scope.
- **Step 6.8 skipped with note.** The diff touches no release-lifecycle file
  (`cm-release.sh`, `cm-open-rc.sh`, `cm-cancel-rc.sh`,
  `team/scripts/lib/project_paths.sh`, or the open/close cycle), so the
  synthetic release simulation does not apply.
- **Step 6.10.** Prerequisite graph is acyclic and complete: CM-026 opens the
  RC, CODER-023 → CODER-024 → WRITER-025 run in declared order, TESTER-027
  closes. Every referenced task ID resolves to a real folder in `DONE` state.
  Sequence numbers 023–027 are consistent and non-colliding.
- **Test fidelity.** The RC's own fixtures are unusually good — production
  volumes (1,800 repetitions), five-agent round-robin interleaving so
  identical lines are almost never adjacent, and a 50-vs-5,000 stress pair
  proving the row count does not scale with repetition. Nothing is mocked away
  that matters, and the fixture profile matches the live measurement the
  priority cited. The one fidelity shortfall is the input alphabet rather than
  the volume: no fixture carries a line with a literal tab, which is exactly
  where Finding 8 lives. A regression test for it belongs in
  `team/tests/unit/test_event_band_renderer.py`.
- **Observation, not a finding.** `COLLAPSE_KEY_CACHE` in `event-band.sh`
  memoizes collapse identities keyed on a line's post-timestamp text, while
  the identity itself is computed from the full line. That is sound for every
  collapse regex that captures only post-timestamp text — which is the
  library's documented contract and true of the one entry that exists today —
  but a future entry capturing anything from the timestamp or agent prefix
  would silently read a wrong cached identity. Worth a comment or an assertion
  when a second collapse key is added; nothing is wrong today.
- **Scratch artifacts.** Runner output at
  `/tmp/pgai_kanban_tmp/tester/{unit,integration}.log`, fixture renders and the
  `ai_main` differential baseline under `/tmp/pgai_kanban_tmp/tester/`, and the
  throwaway roots at `/tmp/pgai_kanban_tmp/sandbox-*`. Every render ran with a
  throwaway `PGAI_AGENT_KANBAN_ROOT_PATH` and cleared `PGAI_TASKS_DIR` /
  `PGAI_PROJECT_ROOT` per the live-install safety rule; the renderer's own
  temp files were cleaned up by its `EXIT` trap in every run.
