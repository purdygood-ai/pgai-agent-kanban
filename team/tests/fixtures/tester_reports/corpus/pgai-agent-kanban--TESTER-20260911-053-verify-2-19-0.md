# Verification Report: ai_rc/v2.19.0

## Summary

Verified release candidate `ai_rc/v2.19.0` (commit `636149e`) against
`projects/pgai-agent-kanban/requirements/v2.19.0-dashboard-v2.md` — the
"dashboard v2" release: a census-driven window set, a single-source semantic
palette, an agent-metrics CLI with a REST twin, visibility-aware refresh
cadence, pane signal safety with an all-armed readiness handshake, shared
per-tick snapshots for twin views, a posture bar that renders every active
flag at once, and a status-writer tool as the only sanctioned mutator of task
`status.md`. Work was done in the isolated detached worktree at
`/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260911-053-verify-2-19-0`
(HEAD matches `ai_rc/v2.19.0`, clean tree, no stashes). Pre-flight merge
verification passed: all twelve DONE prerequisite tasks merged, no live
feature branches, no zero-commit anomalies, and all twelve first-parent
commits on the RC are feature-branch merges with no direct commits.

The RC's functional deliverables all verify. Every acceptance criterion in the
requirements doc was exercised, most of them behaviourally rather than
structurally: the census lint is green end to end, the palette refuses a
missing key loudly in both bash and Python, the posture bar renders a global
HALT, a project HALT-AFTER and a non-zero deferral count simultaneously with
each scope named, the snapshot cache proves twin parity by construction
(second render served from cache, byte-identical stdout, stale cache
recomputes), the status-writer tool refuses bad input dual-channel while
writing nothing and preserves every untouched section byte-for-byte, and the
real-tmux 30-window-switch storm fixture passes with zero pane deaths.

One defect stops the release from being clean: the RC added three
`docs/dashboard.md` citations to `docs/OPERATIONS.md` without adding that
document to the docs manifest, so it is not delivered to an installed or
upgraded root. That dangles the pointer for anyone reading the installed tree
and fails two unit tests, which makes the gated unit runner exit 1 and rules
out a PASS recommendation. One further presentation defect was found in the
new agent-metrics pane. Both are filed; neither prevents an operator from
using the release.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

medium

## Suite Results (authoritative — gated runners)

| Runner | Exit | Result |
|---|---|---|
| `bash team/scripts/run-unit-tests.sh --coverage` | **1** | 2 failed, 4018 passed, 3 skipped (617s). Every lint pre-flight passed; the two failures are pytest failures, both the same root cause (Finding 1). |
| `bash team/scripts/run-integration-tests.sh --coverage` | **0** | 449 passed, 1 skipped (440s). |

`UNIT_EXIT == 1`, so the suite-pass precondition for a PASS recommendation is
not met. Captured output:
`/tmp/pgai_kanban_tmp/tester/unit_output.txt`,
`/tmp/pgai_kanban_tmp/tester/integration_output.txt`.

## Code Coverage

- Unit:        line 75%  |  branch 62%   (15453 stmts, 4640 branches; blended `TOTAL` 72%)
- Integration: line  8%  |  branch  6%   (15444 stmts, 4640 branches; blended 7%)

Informational only; coverage does not enter the recommendation.

## Findings

### Finding 1: `docs/OPERATIONS.md` cites `docs/dashboard.md`, which the installer does not deliver

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (a real defect that ships and is fixed next iteration — the shipped installed root carries a dangling doc pointer and the gated unit suite is red)
**Regression:** yes (the three citations do not exist on `ai_main`; this RC's visibility-aware-cadence documentation work added them)
**Integrity Damage:** no
**Filed As:** BUG-0244 (Path C-A — filed as BUG: code defect / regression / test failure)

Both failing unit tests report the same cause:

```
FAILED tests/unit/test_installed_governance_docs_citations_resolve.py::test_every_installed_governance_docs_citation_resolves_under_fresh_install
FAILED tests/unit/test_installed_governance_docs_citations_resolve.py::test_every_installed_governance_docs_citation_resolves_under_upgraded_root

  Checked 12 distinct docs/ citation(s) for resolution under the installed root.
  1 citation(s) did NOT resolve:
    - docs/OPERATIONS.md cites 'docs/dashboard.md', which is not installed
```

`team/config/docs-manifest.txt` is the single list both `install.sh` and
`team/scripts/upgrade.sh` read, and it does not name `dashboard.md`.
`docs/OPERATIONS.md` — which IS installed — now carries three references to
`docs/dashboard.md` (the window-0 refresh paragraph, the "Refresh loops:
`cadence-loop.sh`, not `watch`" section, and the "Dashboard W6 (Metrics)"
section). `git show ai_main:docs/OPERATIONS.md | grep -c 'docs/dashboard.md'`
returns 0, confirming the citations are new in this RC. The manifest's own
header states the contract this violates: "Whoever adds a new docs/ citation
to a role file must add its path here in the same change, or the citation
dangles on an installed or upgraded root."

Consequence for an operator: on a real installed kanban root, the operations
guide sends the reader to a document that is not there. Consequence for the
chain: the gated unit runner exits 1, so the RC has no green suite verdict.

Likely one-line fix (add `dashboard.md` to the manifest), with the manifest's
transitive-closure rule to check alongside it. Details, reproduction, and
acceptance assertions are in BUG-0244.

### Finding 2: agent-metrics "Avg Wall" column is the RC's wall time, not the agent's, and carries no units

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (hygiene — a label and a missing unit on one pane; the arithmetic and the CLI/REST parity are correct)
**Regression:** yes (the pane is new in this RC; the defect did not exist before because the surface did not exist)
**Integrity Damage:** no
**Filed As:** BUG-0247 (Path C-A — filed as BUG)

Running the new pane against the live kanban root:

```
Agent          Avg Wall   Avg Tokens  Avg Tasks
cm                  132    1,023,430          1
coder               132   79,063,440          3
pm                  132    2,457,143          1
tester              132   11,444,382          1
writer              142    4,460,051          1
```

Four agents show the identical `132`. The figure is not per-agent: the per-RC
rollups (`projects/<name>/metrics/rc/v*.json`) carry one RC-level
`wall_time_minutes` and no per-agent wall time at all, so what is averaged is
the elapsed time of the RCs each agent appeared in. `writer` differs only
because it appeared in a different subset. The column header is bare
`Avg Wall` with no unit, and `docs/DASHBOARD-PANES.md` window 8 repeats the
"per agent" framing with no caveat.

The computation is correct and the semantics ARE documented — in
`agent-metrics.sh --help`, in the shared module's docstring, and in
`docs/operator-api.md`. None of those is in front of the operator looking at
the pane, which is where the requirements doc places the bar ("honest
icons"). Recorded as a defect of the rendered surface, not of the arithmetic;
the fix is a header/footnote and a docs sentence, and must leave the numbers
(and the CLI/REST parity fixture) untouched.

### Finding 3: Census-driven window set — census lint green, derived surfaces match

**Category:** pass

`python3 team/scripts/dashboard/generate_window_docs.py --check` exits 0:
"all surfaces match the census." The census
(`team/config/dashboard_windows.census`) declares 15 window records (16 with
the drill window's second variant) and their pane templates;
`team/scripts/dashboard/window-census.sh --list` reads it through the shared
bash reader and reports the window set in creation order. Every window the
requirements deliverable names is present: window 0 (`main`, the overview),
`operator` (board columns + `suggested-commands` + an interactive cmdline
pane, 10 panes), the `visibility` board twin, `attention`, `schedule`,
`rc-metrics`, `agent-metrics`, `logs`, `terminal`, `human-review`, and the
`drill-N` template in both its `release` and `document` variants. Three
windows beyond the named list (`git`, `metadata`, `debug-logs`,
`training-logs`) are carried forward from the previous board and are not
excluded by the deliverable.

Naming note, not a finding: the deliverable's phrase "0 overview, main board"
maps onto a census whose window 0 is named `main` and whose board twin is
named `visibility`. The census is the single source the docs, tests, and
`create.sh` all derive from, so no drift is possible; only the prose naming
differs from the requirement's shorthand.

### Finding 4: Palette single-source — missing key refuses loudly, no hardcodes

**Category:** pass

Guard exercised in both languages (Step 6.5). Bash:
`dashboard_semantic_color no_such_key` returns 1 and emits the full
dual-channel contract —
`error: dashboard-palette.key-missing` / a message naming the key and the
palette file / a runnable remedy. Python:
`resolve_semantic_color('no_such_key', True)` raises `PaletteKeyError`
carrying the same three fields. A real key resolves (
`dashboard_semantic_tmux_fg halt_active` → `#[fg=red]`). The
`lint_dashboard_hardcode_colors` lint reports both
`team/scripts/dashboard` and `team/scripts/lib/dashboard` single-source
clean, and it is registered in `run-unit-tests.sh`'s pre-flight.

### Finding 5: Posture bar renders every active flag simultaneously, scopes named

**Category:** pass

Exercised against a sandbox kanban root (never the live root), with a global
`HALT`, a project `HALT-AFTER` holding `rc:v9.9.9`, and two open low-severity
bugs under a project configured for deferral:

```
v2.18.1 | demo:v0.0.0 | workflow:release | PM:auto | [HALT GLOBAL] | [HALT-AFTER:demo until v9.9.9] | BUGS DEFERRED 2 (<=low) | Fri 2026-09-11T20:52:44
```

All three flags render at once, each naming its scope (`GLOBAL` literal, the
project by name), and the version-pinned sentinel renders its target through
the shared describer (`until v9.9.9`). `status-right.sh` agrees. A deliberately
malformed deferral config renders `DEFER CONFIG UNREADABLE: <path>` rather
than a silent zero — loud, as intended.

Observation, not a finding: within ONE scope, a `HALT` suppresses that same
scope's `HALT-AFTER` segment, because `halt_state.py` resolves exactly one
state per scope (`halted` OR `draining`). Cross-scope simultaneity — which is
what the deliverable and the shipped fixtures are about — is fully honoured.
The single-scope behaviour predates this RC and is defensible (a hard halt at
a scope makes that scope's soft sentinel moot), but it is worth an operator
knowing.

### Finding 6: Signal safety — traps first, handshake before return, hook lifecycle

**Category:** pass

- Trap-first-line: `dashboard_pane_wrap_cmd`
  (`team/scripts/dashboard/lib/pane_readiness.sh`) prepends
  `trap '' <signal>` as the pane's first executed statement, before any
  `env_bootstrap.sh` sourcing, and every pane in `create.sh` is dispatched
  through `dashboard_send_armed_keys`.
- All-armed handshake: `create.sh` polls `dashboard_wait_all_armed` over the
  exact set of pane labels it armed and refuses dual-channel
  (`dashboard_create.pane_arm_timeout`) rather than returning early.
- Session-scoped hook removed by kill: `kill.sh` calls
  `dashboard_remove_activation_hook` mirroring `create.sh`'s install.
- Sender tolerates unarmed panes: `activation-hook-sender.sh` skips any pane
  that has not reported armed and narrates the skip naming the pane.
- **The fixture the deliverable names passes for real.**
  `team/tests/integration/test_dashboard_activation_hook_lifecycle.py::test_thirty_immediate_window_switches_leave_every_pane_alive`
  runs `create.sh` on a private-socket tmux server (tmux 3.2a present on this
  host), performs 30 immediate `select-window` calls with no warm-up sleep,
  and asserts pane identity equality before/after plus `pane_dead=0` for every
  pane. It passed in the gated integration run.

### Finding 7: Visibility-aware cadence, stagger jitter, refresh-on-activate

**Category:** pass

`PGAI_DASHBOARD_HIDDEN_MULTIPLIER=10`,
`PGAI_DASHBOARD_JITTER_MAX_SECONDS=3`, and
`PGAI_DASHBOARD_HIDDEN_CPU_BUDGET_RATIO=0.5` are named constants in
`team/scripts/lib/dashboard_constants.sh` — no per-pane magic numbers. The
"CPU sample: hidden-window total under stated budget" acceptance criterion is
implemented as a real measurement in
`team/tests/unit/test_dashboard_cadence.py`: a same-run, same-host baseline
arm (state forced `active`) versus hidden arm, CPU read from
`/proc/<pid>/stat` fields 14-17 so renderer children are counted, asserted
against the named ratio constant. It passed in the gated unit run. The
derivation of the budget (measured ratio ~0.163-0.168 against an asserted
ceiling of 0.5) is recorded in the constant's own comment.

`cadence-loop.sh` and `activation-hook-sender.sh` deliberately use
`set -uo pipefail` without `-e`: `errexit` in a long-lived pane loop would
kill the pane on a transient renderer failure, which is precisely the failure
mode this RC exists to prevent. Recorded as correct, not as a strict-mode
omission.

### Finding 8: Twin-view shared per-tick snapshots

**Category:** pass

Exercised directly (Step 6.5), with a counting generator:

- First render — cache miss: generator invoked once, output returned, the
  miss narrated **to stderr only** (`[dashboard-snapshot] probe: no fresh
  snapshot (missing, unreadable, or older than 5s) -- rendering directly`).
- Second render within TTL — cache hit: generator NOT re-invoked (call count
  stays 1), stdout byte-identical to the miss path, stderr empty.
- Third render with `ttl=0` — treated as stale: generator re-invoked (call
  count 2), fresh output returned.

Parity between twins is therefore by construction (same cached bytes), and
because narration never touches stdout, existing render goldens are
unaffected. Writes are atomic (temp file in the same directory plus `mv -f`).
TTL resolution honours the env override, then `kanban.cfg`, then a 5s default.

### Finding 9: Status-writer tool is the only sanctioned status mutator

**Category:** pass

Refusals exercised (all write nothing, all dual-channel, all exit 1):

| Attempt | Error key |
|---|---|
| `--state NOPE` | `task-status.invalid-state` |
| unknown project | `task-status.missing-status-file` |
| unknown task key | `task-status.missing-status-file` |
| `--summary` against a status.md with no `## Summary` section | `task-status.missing-section` |

Happy path exercised in a sandbox root: `--state WORKING --summary "new
summary"` produced a two-line diff — `## State` and `## Summary` only — with
every other section preserved byte-for-byte. `--help` names every flag and
states the six-value vocabulary. The bypass lint
(`team/scripts/lint_status_writer_bypass.py`) reports 75 files clean, and the
governance half of the deliverable landed: `team/SOP-core.md` gained the
"Writing task state" canon and the role files cite it instead of describing a
hand-edit.

Note: the tool resolves its root through `OpsContext.from_env()`, where the
legacy `KANBAN_ROOT` takes precedence over `PGAI_AGENT_KANBAN_ROOT_PATH`. That
is the documented precedence, but it means overriding only
`PGAI_AGENT_KANBAN_ROOT_PATH` for a sandbox run silently still targets the
live root when `KANBAN_ROOT` is exported (as it is in the wake environment).
Not a defect — recorded so the next verifier sets both.

### Finding 10: Agent-metrics CLI / REST parity and ICD

**Category:** pass-with-caveat

The CLI renders thousands-separated integers through the shared
`number_format.format_int`; the averaging arithmetic lives once in
`team/pgai_agent_kanban/lib/agent_metrics.py` and is imported (not
re-implemented) by both the CLI adapter and `GET /agent-metrics`. The ICD is
at 1.5.0 on both `docs/api/ICD_VERSION` and the package copy,
`docs/api/baselines/icd-v1.5.0.json` is added and listed in `SUPPORTED`, and
both `lint_icd_freshness` and `lint_icd_compat` pass (current ICD is a
compatible superset of all six baselines). The parity fixture
(`team/pgai_agent_kanban/api/tests/test_cli_rest_agent_metrics_parity.py`)
passed in the gated unit run.

Caveat is Finding 2: the numbers are right and identical across the twins; the
wall column's label is what misleads.

### Finding 11: Structural, constraint, and standards checks

**Category:** pass

- `bash -n` clean on every changed `.sh` file in the RC diff.
- Every new script is executable and ships `--help`; `lint_help_presence`
  reports 84 scripts, 0 offenders.
- `lint_gate_census_compliance` OK — 35 files checked, 0 offenders, 124
  anchored sites exempted. New refusal sites in this RC carry their census
  class inline (e.g. `BLOCK-WORTHY` above the palette key-missing refusal,
  `AGENT-RESOLVABLE` above the status-writer required-flag refusal) with
  `CENSUS-ANCHOR` markers.
- `lint_dual_channel_pair` OK — 235 files, 0 offenders.
- Skip-cites-real-bug (Step 6.13): no placeholder skip IDs in `team/tests/`,
  and every cited `BUG-NNNN` resolves to a real file. `lint_skip_bug_gate`
  agrees (0 missing files).
- Step 6.1 drift check: `pp_last_released_version` = `v2.18.1` (tag
  `ai_v2.18.1`), `semver_gt v2.19.0 v2.18.1` is true; `release-state.md`
  `## Active RC` is `v2.19.0`, matching the branch under test, and
  `## Last Released` is a well-formed `v2.18.1`. `VERSION` still reads
  `v2.18.1` — expected, CM bumps it at ship time.
- Step 6.8 (synthetic release simulation): **skipped with note** — the RC's
  diff touches no release-lifecycle file (`cm-release.sh`, `cm-open-rc.sh`,
  `cm-cancel-rc.sh`, `project_paths.sh`). The existing
  `tests/integration/test_cm_release_synthetic.py` nonetheless ran green in
  the gated integration suite.
- Step 6.10 (prereq graph): all 12 prerequisite task IDs resolve to real task
  folders, all DONE, no cycles, sequence numbers 040-051 contiguous and
  non-colliding, with the CM-open bookend at 052 and this TESTER task at 053.

## Lint Findings (non-blocking)

Ingested from `${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-unit.json` and
`findings-integration.json`. Both files carry the identical seven findings
across two lint classes; grouped by class, one bug per class per run.

- `hygiene` — 2 instances, `severity: quality`.
  - `team/tests/unit/test_posture_bar_truth.py:20` — provenance: a task-ID
    citation in a file this RC added — **filed BUG-0245 (Path C-A)**.
  - `release-notes/v2.16.0.md:42` — provenance in a shipped release note —
    **cites BUG-0242 (open)**; not re-filed.
- `governance_byte_budget` — 5 instances, `severity: quality`: `coder`
  (71756/69632), `writer` (70846/69632), `tester` (103565/102400), `cm`
  (86775/86016), `overwatch` (76042/73728) — **filed BUG-0246 (Path C-A)**.

Neither class moved a gated runner's exit code, and per Step 5c neither
elevates `## Recommendation` nor raises `## Systemic Risk` on its own. The
recommendation and risk above come from Findings 1 and 2.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=4d freshness=stale — the last lane run was red on the docker/podman claude-abliterated flavor (a Dockerfile FROM parse error) and the podman/rocky9 COPY --chmod step; it is stale on version rather than age (4d is inside the 7-day window, but v2.9.20 is not the v2.19.0 under verification), and the bugs that run filed (BUG-0166, BUG-0167) are all closed with no lane re-run since, so nothing is known about whether this tree's images build.

Read through `deep_verify_ledger_read_latest`; the verdict is the ledger's own
word, not a re-derivation. No bug filed here — a red record is cited, never
re-filed, and the lane was not run during this verification (Step 5d: the RC
runs the fast suites only).

## Autonomous criterion

Manual interventions required: none.

1. Tasks force-promoted from WAITING to BACKLOG manually? **NO.** All twelve
   prerequisites reached DONE through the wake path; this TESTER task's own
   status records the wake script's automatic WAITING→BACKLOG promotion.
2. `release-state.md` edited outside the CM scripts? **NO.** It carries the
   in-flight shape `cm-open-rc.sh` writes (`Active RC: v2.19.0`, opened
   2026-09-11T12:21:11Z by `CM-20260911-052-open-rc-2-19-0`) plus the
   `## Last Released: v2.18.1` the last release wrote.
3. Scripts patched mid-build? **NO.** All 51 commits are authored by the same
   agent identity, and every one of the 12 first-parent commits on the RC is a
   merge of a named feature branch — no direct commits landed on the RC
   branch. Changes under `team/scripts/` are the ticketed work itself.
4. Queue files manually edited? **NO.** All twelve prerequisite entries carry
   `[x]` in `coder_backlog.md` / `writer_backlog.md`; this task carries `[A]`
   in `tester_backlog.md`. No HALT or HALT-AFTER sentinel exists at either
   scope.

## Caveats

- **The unit suite is red for exactly one reason.** 4018 of 4020 unit tests
  pass and every lint pre-flight is green; both failures are the single
  missing docs-manifest line in Finding 1. CM should read the red unit verdict
  as that one defect, not as broad instability.
- **Same-scope HALT/HALT-AFTER rendering.** See the observation under
  Finding 5: one scope resolves to one state, so a scope's own `HALT` hides
  its own `HALT-AFTER` segment. Cross-scope simultaneity is correct and
  fixture-covered. Pre-existing, not filed.
- **Integration coverage is 8% line / 6% branch** against the same module set
  the unit suite measures at 75%/62%. Informational only, never a finding; the
  integration suite is a lifecycle suite, not a module suite.
- **Sandbox discipline.** Every behavioural probe in this report ran against a
  throwaway root under `$PGAI_AGENT_KANBAN_TEMP_DIR` with BOTH `KANBAN_ROOT`
  and `PGAI_AGENT_KANBAN_ROOT_PATH` repointed; the live install was written to
  only by this task's own artifacts and its four Path C bug filings. The
  worktree was verified clean (`git status --short` empty) after every probe.
- **Prior-run artifacts.** None. `artifacts/` was empty at start; no
  `.previous-RUN-N` file existed, so no prior verdict was available to
  inherit and none was consulted. Every check in this report was run fresh.

## Path C filings (autonomous follow-up filing — non-blocking)

| ID | Path | Severity | Covers |
|---|---|---|---|
| BUG-0244 | `bugs/BUG-0244-docs-dashboard-not-installed.md` | medium | Finding 1 — `docs/dashboard.md` missing from the docs manifest; 2 unit failures |
| BUG-0245 | `bugs/BUG-0245-hygiene-provenance-new-test.md` | low | `hygiene` lint class — provenance citation in a new test file |
| BUG-0246 | `bugs/BUG-0246-governance-byte-budget-over.md` | low | `governance_byte_budget` lint class — 5 roles over budget |
| BUG-0247 | `bugs/BUG-0247-agent-metrics-wall-column-label.md` | low | Finding 2 — agent-metrics wall column label and units |
