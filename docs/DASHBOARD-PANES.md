# Dashboard panes — script and standalone-command reference

Every visible region of the tmux dashboard is populated by a single shell script (or Python module) under `$KANBAN_ROOT/scripts/dashboard/`. Each of those scripts also runs as a standalone CLI tool that prints the same content to your terminal and exits. This document maps every window and pane in the dashboard to the script that populates it and the exact command you can run from any shell to view that pane's output directly — useful for debugging a pane, scripting against its data, or inspecting kanban state without launching tmux.

The window numbers, names, and pane tables below are generated from `team/config/dashboard_windows.census` — the machine-readable single source of truth for the dashboard layout — by `team/scripts/dashboard/generate_window_docs.py`. `team/scripts/dashboard/create.sh` is what actually builds the session; the census describes it, and `generate_window_docs.py --check` (run on every RC via `team/scripts/run-unit-tests.sh`) refuses when this document disagrees with the census. Window numbers are 0-based tmux window indices (`tmux list-windows` — no `base-index` override exists in this tree), not 1-based ordinals. Do not hand-edit the text between a `CENSUS-GENERATED` marker pair; run `python3 team/scripts/dashboard/generate_window_docs.py` from the repo root and commit the result instead. The rc-metrics window (window 7) has its own deeper column reference in [dashboard.md](dashboard.md) — this document does not duplicate that detail.

## Required environment

Most commands need only `PGAI_AGENT_KANBAN_ROOT_PATH`. Window 5 (git) additionally requires `PGAI_DEV_TREE_PATH`. Set both before running any standalone command:

```bash
export PGAI_AGENT_KANBAN_ROOT_PATH=$HOME/pgai_agent_kanban
export PGAI_DEV_TREE_PATH=$HOME/develop/pgai-agent-kanban
```

All script paths in the tables below are relative to `$KANBAN_ROOT/scripts/dashboard/`. Run them from there, or prepend the full path.

## Window 0 — main

The overview window. Header on top, progress in the middle, and a live log tail on the bottom, with the per-project queues column on the right. Cron/schedule content lives in its own [schedule window](#window-4--schedule) rather than a pane here.

<!-- CENSUS-GENERATED:BEGIN window=0 variant=- -->
| Pane position | Populating script | Standalone command |
|---|---|---|
| Top (HEADER, ~11%, floored to fit the full cron roster) | `show-multi.sh` | `show-multi.sh --mode header --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |
| Bottom (LOGS, ~40%) | `logs.sh` | `logs.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH --stdout` |
| Middle (PROGRESS, full width of left column) | `show-multi.sh` | `show-multi.sh --mode progress --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |
| Right (QUEUES, ~25% of window, full height) | `show-multi.sh` | `show-multi.sh --mode queues --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |
<!-- CENSUS-GENERATED:END window=0 variant=- -->

`show-multi.sh` dispatches to `show-header.sh`, `show-queues.sh`, or `show-progress.sh` per project according to the `--mode` argument. In tmux, the bottom logs pane runs a blocking `tail -F`; the `--stdout` flag prints the last 30 lines of each cron log file once and exits.

## Window 1 — operator

The container operator's whole workspace, in three stacked regions: **board** (top), **cmdline** (middle), and **legend** (bottom). The board is window 2's eight columns at the same widths. The cmdline region is a full-width interactive shell, 15% of the window height (floor, minimum 3 rows), opened at the kanban root with the kanban shell-env sourced. The legend is window 2's one-row colour legend bar. Inside a container attach there is no "just open another terminal" — this window is meant to be the one screen an operator watches the board and runs commands (`show.sh`, `reset.sh`, `wontdo.sh`, `intake.sh`) from. For a reminder of the common commands, run `scripts/dashboard/suggested-commands.sh` in the shell.

<!-- CENSUS-GENERATED:BEGIN window=1 variant=- -->
| Pane position | Populating script | Standalone command |
|---|---|---|
| Top-left, board input col 0 of 3 (BUGS) | `column-render.sh` | `column-render.sh input none none 13 29 --label BUGS --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH --all-projects` |
| Top-left, board input col 1 of 3 (PRIORITIES) | `column-render.sh` | `column-render.sh input none none 13 29 --label PRIORITIES --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH --all-projects` |
| Top-left, board input col 2 of 3 (REQUIREMENTS) | `column-render.sh` | `column-render.sh input none none 13 29 --label REQUIREMENTS --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH --all-projects` |
| Top-right, board queue col 0 of 5 (PM) | `column-render.sh` | `column-render.sh queue none 13 26 --label PM --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH --all-projects` |
| Top-right, board queue col 1 of 5 (CODER) | `column-render.sh` | `column-render.sh queue none 13 26 --label CODER --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH --all-projects` |
| Top-right, board queue col 2 of 5 (WRITER) | `column-render.sh` | `column-render.sh queue none 13 26 --label WRITER --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH --all-projects` |
| Top-right, board queue col 3 of 5 (TESTER) | `column-render.sh` | `column-render.sh queue none 13 26 --label TESTER --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH --all-projects` |
| Top-right, board queue col 4 of 5 (CM) | `column-render.sh` | `column-render.sh queue none 13 26 --label CM --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH --all-projects` |
| Middle (15% height, floor, min 3 rows, full width, between board and legend) — interactive cmdline shell | (interactive bash, kanban shell-env sourced) | N/A -- interactive shell (cd $PGAI_AGENT_KANBAN_ROOT_PATH; source shell-env if present) |
| Bottom (LEGEND, 1 row) | `legend-render.sh` | `legend-render.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |
<!-- CENSUS-GENERATED:END window=1 variant=- -->

The eight board column panes run the exact same commands as the visibility window's eight columns (same `column-render.sh` calls) — this window's board region is a faithful replica, not a different rendering. `suggested-commands.sh` is a command, not a pane: run in the shell, it prints a curated command reference and exits, and never invokes anything on the operator's behalf.

**The tmux board panes add one flag the standalone commands leave out.** In this window and in window 2, each board column runs its `column-render.sh` command under `cadence-loop.sh` with `--board-widths input=29,queue=26` appended. The BUGS column, for example, runs:

```bash
column-render.sh input none none 13 29 --label BUGS --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH --all-projects --board-widths input=29,queue=26
```

The flag declares the width every board column is drawn at: `input` for the three input columns and `queue` for the five queue columns, the same widths the commands pass positionally. Whichever column builds a tick pre-renders all eight columns at those widths, so the other seven print their pre-rendered slice without starting Python. Output is identical with or without the flag; only the cost changes. The standalone commands in the tables leave it out and print the same content.

## Window 2 — visibility

A grid of nine narrow columns: three input streams on the left (bugs, priorities, requirements), five agent queues on the right (pm / coder / writer / tester / cm), and a one-row legend at the bottom. In tmux, each column is wrapped in a `while sleep` loop so 24-bit ANSI color survives; standalone, each script renders once and exits.

<!-- CENSUS-GENERATED:BEGIN window=2 variant=- -->
| Pane position | Populating script | Standalone command |
|---|---|---|
| left-col-0 (BUGS) | `column-render.sh` | `column-render.sh input none none 13 29 --label BUGS --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH --all-projects` |
| left-col-1 (PRIORITIES) | `column-render.sh` | `column-render.sh input none none 13 29 --label PRIORITIES --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH --all-projects` |
| left-col-2 (REQUIREMENTS) | `column-render.sh` | `column-render.sh input none none 13 29 --label REQUIREMENTS --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH --all-projects` |
| right-col-0 (PM queue) | `column-render.sh` | `column-render.sh queue none 13 26 --label PM --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH --all-projects` |
| right-col-1 (CODER queue) | `column-render.sh` | `column-render.sh queue none 13 26 --label CODER --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH --all-projects` |
| right-col-2 (WRITER queue) | `column-render.sh` | `column-render.sh queue none 13 26 --label WRITER --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH --all-projects` |
| right-col-3 (TESTER queue) | `column-render.sh` | `column-render.sh queue none 13 26 --label TESTER --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH --all-projects` |
| right-col-4 (CM queue) | `column-render.sh` | `column-render.sh queue none 13 26 --label CM --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH --all-projects` |
| Bottom (LEGEND, 1 row) | `legend-render.sh` | `legend-render.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |
<!-- CENSUS-GENERATED:END window=2 variant=- -->

## Window 3 — attention

Single pane: every BLOCKED task across every project, with its reason and recommended next step.

<!-- CENSUS-GENERATED:BEGIN window=3 variant=- -->
| Pane position | Populating script | Standalone command |
|---|---|---|
| single | `attention.sh` | `attention.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |
<!-- CENSUS-GENERATED:END window=3 variant=- -->

## Window 4 — schedule

The whole cron-driven schedule in one place, as two full-width bands stacked top to bottom — the per-agent list, then the event band. The list answers what each agent last did and when it fires next; the event band answers what has happened so far today. There is no separate roster band: the one-line next-wake summary stays in window 0's header, and here the list's NEXT column carries the same information, one agent per row.

The **list band** answers *what did each lane last do, and when does it fire next*. It renders one row per roster agent under a header of four columns: AGENT, LAST FIRED, LAST RESULT and NEXT. Rows come in fixed roster order — pm first, then every other agent in the order it first appears in the crontab, matching the window-0 header's ordering exactly — rather than sorting by soonest-firing, so each row's identity is stable across renders and only the values change on refresh. LAST RESULT is the terminal log line that firing wrote for itself, distilled to exactly one of four verdicts — `completed <N>`, `idle (no BACKLOG)`, `HALT skip` or `contended` — never the raw line. NEXT is byte-identical to the next-wake text window 0's header shows for the same agent, because it is that text with the leading agent token stripped rather than a second countdown computation. An agent whose cron log does not exist yet — a schedule entry that has never fired — reads `no log yet` in both log-derived columns instead of dropping out of the band. An agent whose cron log exists but carries no line matching any of the four verdicts within the scanned window — a firing still in progress, a freshly created log, or one that predates the timestamped `log()` convention — reads `no data yet` in LAST FIRED (or the real timestamp when one was parseable) and `no result yet` in LAST RESULT, and still renders its own row rather than truncating the rows after it.

**The list keeps every roster row whenever the window is tall enough to hold it.** Its height is the header, one row per roster agent and one row of headroom, counted from the crontab's roster rather than fixed. tmux rescales a window's panes whenever the window changes size, so the session carries hooks that re-apply that height after every size or layout change to the window. That covers the first view of the window from a client attached at another size, a live terminal resize while you are viewing it, an explicit `resize-window`, and a switch to the window. It also covers any other layout change, so a band border you drag by hand snaps back at once. Nothing is re-applied while either schedule pane is zoomed; unzooming re-applies the height. The event band takes the remaining height; when the window is too short for both, the event band shrinks and the list keeps its rows.

The **event band** answers *what has actually happened today*. It merges every agent's cron log into one interleaved, agent-tagged stream in ascending timestamp order, oldest first, and keeps only today's signal-class lines: a task dispatched to an agent, a task finishing with its state, a wake reporting completed work, a HALT, a queue promotion, a reaped worktree, a dual-channel error/message/remedy block, and a sweep-census skip with its reason. Idle-cycle boilerplate — the re-checking, promoted-zero, sleeping and stagger-timing lines that make up most of a quiet cycle — is filtered out. A band with nothing to show prints `no wake events yet today` rather than rendering blank. Classification lives in one shared library, `lib/event_signal_patterns.sh`, so adding a pattern is a single-line edit every consumer picks up at once. Each agent's log is read only to its last `event_band_read_bound_lines` lines (`kanban.cfg [dashboard]`, default 20000, overridable with `PGAI_DASHBOARD_EVENT_BAND_READ_BOUND_LINES`) — sized against the fleet's real daily volume rather than any one log's total historical size — so render cost stays flat as a never-rotated cron log grows for years.

**A signal class that repeats is folded into one row.** A class may declare a collapse identity alongside its pattern in `lib/event_signal_patterns.sh`; the sweep-census skip is one class that does so, because it repeats once per agent per wake for as long as a bundle sits in the same state, identical apart from its timestamp and which agent logged it. Every occurrence sharing an identity renders as a single row at the position of its first occurrence, carrying the occurrence total and that first-seen time — `[pm 06:02:11] sweep-census: ... skipped ... (x37, first seen 06:02:11)`. The fold spans the whole day's merged stream across every agent, not a run of adjacent lines, so a once-a-minute line interleaved by five agents costs one row rather than hundreds. Whatever the identity captures still separates rows: a different bundle or a different stated reason is a different identity and gets its own row, which bounds the band at one row per distinct identity instead of one per occurrence. A class that declares no collapse identity is untouched — every occurrence renders, in its own chronologic position, exactly as it did before.

**A repeat-lock narration folds its whole dual-channel block into one row, agent list included.** `event-band.sh` recognizes the bare `error:` line for the repo-lock contention and stale-threshold-floor-clamp narrations (`lock_provenance.sh`'s own dual-channel refusal keys) and merges it with the `message:`/`remedy:` lines directly beneath it into a single row keyed by the narration name, instead of three separate lines per occurrence — `[pm 06:02:11] lock.provenance.contended (x47, first seen 06:02:11, agents: pm,writer,tester,cm)`. Every other dual-channel block (a HALT reason, a gate refusal, ...) is unaffected and still renders its three lines separately. Both narrations are also rate-limited at the source: `lock_provenance.sh` narrates each at most once per lock per wake-firing process regardless of how many times contention is probed that firing, so the event band's own per-agent, whole-day fold is what keeps a long chain's repeated probes from flooding the band across many firings, not a source-side repeat.

<!-- CENSUS-GENERATED:BEGIN window=4 variant=- -->
| Pane position | Populating script | Standalone command |
|---|---|---|
| Top band (full width, one row per roster agent plus its header, re-applied on resize and client attach) — per-agent LAST FIRED / LAST RESULT / NEXT status | `status-band.sh` | `status-band.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |
| Bottom band (full width, remaining height; shrinks first when the window is short) — merged, chronologic, signal-only wake-log event stream across every agent | `event-band.sh` | `event-band.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |
<!-- CENSUS-GENERATED:END window=4 variant=- -->

**The event band hides and folds lines; it never removes them.** Both the signal filter and the collapse are properties of this view alone. It reads the per-agent cron logs at `$KANBAN_ROOT/logs/cron-<agent>.log` and writes nothing back, so every line it filters out, and every occurrence it folds into a counted row, is still on disk, in full, exactly where it was written. Read them unfiltered in the `logs` window (window 9), which interleaves every agent's log with no filtering at all, or one agent at a time with `cron-tail.sh <agent>`. The signal-only view is for a glance; the raw logs remain the forensic record.

## Window 5 — git

Two vertical panes covering the dev tree's git state. Both panes need `PGAI_DEV_TREE_PATH` to point at a local clone.

<!-- CENSUS-GENERATED:BEGIN window=5 variant=- -->
| Pane position | Populating script | Standalone command |
|---|---|---|
| Left (~65%, git status) | `git-status.sh` | `PGAI_DEV_TREE_PATH=$PGAI_DEV_TREE_PATH git-status.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |
| Right (~35%, recent tags) | `git-recent-tags.sh` | `PGAI_DEV_TREE_PATH=$PGAI_DEV_TREE_PATH git-recent-tags.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |
<!-- CENSUS-GENERATED:END window=5 variant=- -->

The left pane reports current branch and sync state, main sync state, uncommitted changes, and recent rc/* branches. The right pane lists the newest tags (at least 10) sorted newest first.

## Window 6 — metadata

Single pane: kanban version, PM mode, HALT state, and one block per registered project (workflow type, active RC, last released, version ceilings).

<!-- CENSUS-GENERATED:BEGIN window=6 variant=- -->
| Pane position | Populating script | Standalone command |
|---|---|---|
| single | `metadata.sh` | `metadata.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |
<!-- CENSUS-GENERATED:END window=6 variant=- -->

## Window 7 — rc-metrics

Two vertical panes covering RC cost data. The left pane shows today's per-project metrics plus the current-RC block; the right pane is the rolling history view. For column-by-column meaning, sub-views (`--per-agent`), and the wall-time column behavior, see [dashboard.md](dashboard.md).

<!-- CENSUS-GENERATED:BEGIN window=7 variant=- -->
| Pane position | Populating script | Standalone command |
|---|---|---|
| Left (today + current RC) | `metrics.sh` | `metrics.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |
| Right (history.csv) | `show-metrics.sh` | `show-metrics.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |
<!-- CENSUS-GENERATED:END window=7 variant=- -->

## Window 8 — agent-metrics

Two vertical panes, both rendered by `agent-metrics.sh`, both showing per-agent, per-project figures. **The split form was built:** the on-demand full-history report is a second pane to the right of the live table, not a command you have to run elsewhere.

- **Left — the live table.** The newest 10 shipped RCs, under the header `(last 10 RCs)`. This is the window's default view.
- **Right — the full-history report.** The same table computed over every shipped RC (`--all`), under the header `(all shipped RCs)`.

`scripts/dashboard/suggested-commands.sh`, run from the operator window's (window 1) shell, also lists `scripts/dashboard/agent-metrics.sh --all [--project <proj>]`, with `--since vX.Y.Z` and `--last N` named as the narrower spans. Run that from window 1's cmdline shell when you want a report for one project, or a span other than the two this window shows.

**Columns.** Each block has five figures per agent — three averages, whose headers each state their own denominator, then two totals:

- `Avg Wall (min/task)` is a mean per TASK. It is pooled from that agent's own per-task wall-clock data across the RCs it was invoked in, never the RC's shared elapsed time, which every agent touching the RC would otherwise report identically. It renders `--` when none of the agent's invoked RCs carry that per-task data.
- `Avg Tokens (/RC)` and `Avg Tasks (/RC)` are each a mean per invocation RC.
- `Total Tokens` and `Total Tasks`, to the right of the averages, are the agent's sums across the span.

Averages print with exactly one decimal (`2.0`, `4.7`, `41,000.0`) and are never floored to an integer. Totals print as integers. Both are thousands-separated.

**Only shipped RCs count.** A cancelled or otherwise unshipped RC enters neither the numerator nor the denominator of any figure. Each block ends with a footer that counts it instead, for example `window: 10 shipped RCs (2 cancelled excluded)` under the live table, or `all: 38 shipped RCs (2 cancelled excluded)` under the full-history report (both example values). The footer prints even when nothing was excluded.

**Width.** Each pane takes about half the window. The table is roughly 95 columns wide and grows as the totals gain digits, so each half shows it without wrapping only on a terminal about twice that wide. On a narrower terminal the rows wrap.

The arithmetic lives in `team/pgai_agent_kanban/lib/agent_metrics.py` and is shared with the `GET /agent-metrics` REST endpoint documented in `operator-api.md`. That endpoint takes the same span choices as query parameters.

<!-- CENSUS-GENERATED:BEGIN window=8 variant=- -->
| Pane position | Populating script | Standalone command |
|---|---|---|
| Left (~50%, live table: newest 10 shipped RCs) | `agent-metrics.sh` | `agent-metrics.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |
| Right (~50%, full-history report: every shipped RC) | `agent-metrics.sh` | `agent-metrics.sh --all --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |
<!-- CENSUS-GENERATED:END window=8 variant=- -->

## Window 9 — logs

Full-screen view of the merged cron-log tail. Same script as window 0's bottom pane.

<!-- CENSUS-GENERATED:BEGIN window=9 variant=- -->
| Pane position | Populating script | Standalone command |
|---|---|---|
| single | `logs.sh` | `logs.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH --stdout` |
<!-- CENSUS-GENERATED:END window=9 variant=- -->

In tmux this pane runs a blocking `tail -F` so new lines stream in. The `--stdout` flag prints the last 30 lines of each cron log once and exits, which is what you want from a terminal.

## Window 10 — debug-logs

Single-pane merged stream of every agent's debug log under `$KANBAN_ROOT/logs/debug/<agent>.log`. Color-coded per agent.

<!-- CENSUS-GENERATED:BEGIN window=10 variant=- -->
| Pane position | Populating script | Standalone command |
|---|---|---|
| single | `debug-logs.sh` | `debug-logs.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH --stdout` |
<!-- CENSUS-GENERATED:END window=10 variant=- -->

In tmux, this pane is gated per-project by the `[debug] verbose_mode = true` setting in each project's `project.cfg`. When one or more registered projects have verbose mode enabled, the pane runs a blocking `tail -F` merged stream; when no project has it enabled, the pane loops on a placeholder. Each project is checked independently — enabling verbose mode in one project does not affect the others. See `operator-troubleshooting.md` ("Enabling debug logs") for the full configuration reference.

When **multiple projects** have verbose mode enabled simultaneously, every line in the pane is prefixed with the project set, so the operator can see which multi-project context produced the stream:

```
[proj1,proj2] [coder] ...
```

When only **one project** has verbose mode enabled, the project prefix is omitted and lines render as `[agent] ...`. The project annotation exists because the debug log files at `$KANBAN_ROOT/logs/debug/<agent>.log` are kanban-wide rather than per-project — per-line project attribution is not possible from log content alone, so the prefix labels the stream's multi-project context as a whole.

The `--stdout` flag bypasses the gating, prints the last 30 lines of each debug log once, and exits. In `--stdout` mode, a `Projects (verbose): [proj1,proj2]` header is printed when more than one project has verbose mode enabled; the same line prefix is applied to the per-agent output below.

The legacy `PGAI_VERBOSE_MODE=1` env var still works as a deprecated shim (it forces verbose for every project and every agent and emits a deprecation warning); new installs should configure `[debug] verbose_mode` in each `project.cfg` instead.

## Window 11 — training-logs

Single pane: each agent's newest reasoning trace from `$KANBAN_ROOT/logs/training/<agent>/<ts>-<task-id>.md`, sorted by mtime with the newest at the bottom. Color-coded per agent.

<!-- CENSUS-GENERATED:BEGIN window=11 variant=- -->
| Pane position | Populating script | Standalone command |
|---|---|---|
| single | `training-logs.sh` | `PGAI_REASONING_TRACE=1 training-logs.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |
<!-- CENSUS-GENERATED:END window=11 variant=- -->

The script is already standalone-capable: with `PGAI_REASONING_TRACE=1` it renders one pass and exits; with the variable unset or `0` it prints a placeholder and exits immediately. In tmux it runs under `watch -t -c -n 30`.

## Window 12 — terminal

Two panes: a live log stream on the left, and a single interactive bash shell at `$KANBAN_ROOT` on the right. No pane script populates the shell pane — there is no standalone equivalent.

<!-- CENSUS-GENERATED:BEGIN window=12 variant=- -->
| Pane position | Populating script | Standalone command |
|---|---|---|
| Left (~40%, live logs) | `logs.sh` | `logs.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH --stdout` |
| Right (~60%, interactive shell at $KANBAN_ROOT) | (interactive bash) | N/A -- interactive shell |
<!-- CENSUS-GENERATED:END window=12 variant=- -->

## Window 13 — human-review

Two panes: pending HUMAN-APPROVE gate tasks across every registered project (left), and OVERWATCH's autonomous resolution ledger (right, read-only, informs only).

<!-- CENSUS-GENERATED:BEGIN window=13 variant=- -->
| Pane position | Populating script | Standalone command |
|---|---|---|
| Left (~65%, Approvals) | `human-review.sh` | `human-review.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |
| Right (~35%, Auto-Resolutions) | `auto-resolutions.sh` | `auto-resolutions.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |
<!-- CENSUS-GENERATED:END window=13 variant=- -->

The left pane's header line reads `✋ PENDING APPROVALS`. Each pending gate renders as a block with the following rows, in this order:

| Row | Source | Meaning |
|---|---|---|
| `project` | task folder's project directory | Name of the project the gate belongs to. |
| `RC/target` | `## Release Version` in the task README | The RC version awaiting approval (falls back to the task ID if the field is absent). |
| `age` | `status.md` mtime | Human-readable elapsed time since the task last changed state (`45s`, `12m`, `3h 4m`, `2d 1h`). |
| `state` | `## State` in the task's `status.md` | Either `WAITING` or `BACKLOG` — both mean pending human action. |
| Goal lines | `## Goal` in the task README | Up to four lines describing what is being approved and where the report lives. |
| `Review:` block | `scan_human_approvals.py` `_build_review_cmds` | Ordered, verbatim inspect-before-deciding commands (see below). **Rendered above `Approve:` / `Reject:`** so the operator reads before pasting. |
| `Approve:` | fixed command template | `scripts/close.sh --project <proj> --key <task-id>` — copy-paste to advance the gate to DONE. |
| `Reject:` | fixed command template | `scripts/wontdo.sh --project <proj> --key <task-id>` — copy-paste to mark the gate WONT-DO. |

### `Review:` block — ordered commands, omit-when-unknown rule

The `Review:` block sits **immediately above** the `Approve:` / `Reject:` lines, completing the read-decide-paste loop in a single pane. Its command lines are printed verbatim, one per line, indented under the `Review:` label — the same formatting the `Approve:` and `Reject:` lines use, so a copy-paste is safe.

The block is built in a fixed order and follows an omit-when-unknown rule:

1. **`scripts/show.sh --project <proj> --key <task-id>`** — always present. Shows the gate task itself (goal, prerequisites, notes).
2. **`scripts/show-test-report.sh --project <proj> --key <rc-version>`** — present **only when the target RC version is known**, identified by starting with `v`. Omitted rather than invented when the version is unknown; the fallback value for `## Release Version` is the task ID itself, which starts with `HUMAN-APPROVE` and therefore never satisfies the `v` check.

The block never guesses. When TESTER's report cannot be addressed by a version key the scanner can trust, the show-test-report line is dropped and only the `show.sh` line renders — a missing line is honest, an invented key is not. The same rule applies to the `review_cmds` array on `GET /approvals` (documented in `operator-api.md`); both surfaces are populated by the same scanner call and therefore emit byte-identical strings for the same gate.

Empty state (no HUMAN-APPROVE tasks are `WAITING` or `BACKLOG` in any project) renders exactly one indented line:

```
  no approvals pending.
```

Rows are sorted first by project name, then by task ID. A task counts as pending when its folder name starts with `HUMAN-APPROVE` and its `status.md` `## State` field is `WAITING` or `BACKLOG`.

## Window 14+ — drill-N (per project)

One window per registered project, named `drill-1`, `drill-2`, and so on. Which layout a drill window uses depends on that project's `[project] workflow_type` in its `project.cfg`. The cron firings pane (release/feature layout) is shared with window 0 because cron is a system-level schedule, not per-project.

### release / feature workflow projects

Same five-pane layout as window 0, scoped to a single project via `--project <name>`. The commands below show the canonical first project (`pgai-agent-kanban`); substitute the actual project name when calling them standalone.

<!-- CENSUS-GENERATED:BEGIN window=14 variant=release -->
| Pane position | Populating script | Standalone command |
|---|---|---|
| Top (HEADER, ~11%) | `show-header.sh` | `show-header.sh --project <name> --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |
| Bottom (LOGS, ~40%) | `logs.sh` | `logs.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH --stdout` |
| Middle-left (QUEUES, ~30% of middle row) | `show-queues.sh` | `show-queues.sh --project <name> --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |
| Middle-center (PROGRESS, ~45% of middle row) | `show-progress.sh` | `show-progress.sh --project <name> --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |
| Middle-right (CRON, ~25% of middle row) | `next-cron-firings.sh` | `next-cron-firings.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |
<!-- CENSUS-GENERATED:END window=14 variant=release -->

### document-workflow projects

Two-pane layout: the top pane combines the artifact version library and document-pipeline progress; the bottom pane is the shared log stream.

<!-- CENSUS-GENERATED:BEGIN window=14 variant=document -->
| Pane position | Populating script | Standalone command |
|---|---|---|
| Top (~55%, artifact library + pipeline progress) | `show-document-drill.sh` | `show-document-drill.sh --project <name> --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |
| Bottom (~45%, shared log stream) | `logs.sh` | `logs.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH --stdout` |
<!-- CENSUS-GENERATED:END window=14 variant=document -->

## Pane debug logs

No pane prints diagnostics onto its own display. A pane's rendered content is its entire output stream; the narration that goes with a render — a shared-snapshot miss, a cache write that failed, an activation-hook skip — is appended to a per-pane debug log file:

```
<temp-root>/dashboard/logs/<window-name>.<pane-index>.log
```

`<temp-root>` is the framework temp root, `/tmp/pgai_kanban_tmp` unless `PGAI_AGENT_KANBAN_TEMP_DIR` is set or `[paths] tmp_root`/`tmp_subdir` in `kanban.cfg` say otherwise. `<window-name>` is the tmux window name and `<pane-index>` is its 0-based pane index, so the visibility window's fourth pane writes `visibility.3.log`; characters not valid in a filename become underscores, which is what a per-project drill window's name reduces to. Each file is created when its pane's refresh loop starts, and removed with the rest of the dashboard temp subtree when the session is killed.

**Which panes have one.** Every pane that refreshes on a cadence — each pane whose tmux command wraps its populating script in `cadence-loop.sh` — writes one. The continuously streaming panes (`logs.sh`, `cron-tail.sh`, `debug-logs.sh`) and the interactive shells do not run under that loop and so write none of their own. Those three streaming scripts are additionally left out of activation signalling altogether: they never install the repaint-on-activate handler, so signalling them would do nothing and no skip is narrated for them on a window switch. A pane that cannot write its log falls back to discarding the narration and keeps rendering; columns are never held up by a log target.

**Standalone invocations behave differently on purpose.** The commands in the tables above are not wrapped in the pane loop, so any narration they emit arrives on your terminal's stderr while the pane content goes to stdout. That is convenient — redirect stderr to a file (or to `/dev/null`) to separate the two — and it is the reason running a pane script by hand can print lines you never see inside tmux. A standalone run also shares the same snapshot cache as the live session, so it may serve its content from a snapshot a pane wrote, or refresh one that panes then read.

The cooperative snapshot cache these lines describe, its staleness bound, and the meaning of each narration tag are in [dashboard.md](dashboard.md) ("Shared render snapshots" and "Where pane narration goes").

## tmux status bar

The bottom status line of every tmux window is not a pane, but it is a visible dashboard element, so it is documented here for completeness.

Nothing in the status format runs a script. The bottom row of `status-format` is `#{@pgai_status_bottom}`, a session user option that tmux expands without starting a process. `status-bottom-loop.sh` fills that option. `create.sh` starts the loop in the background with the session, and it exits when the session ends. It runs `status-bottom.sh` once at start, then every `PGAI_DASHBOARD_REFRESH_SECONDS` while a client is attached. While none is attached, it renders nothing. Whatever the renderer writes to stderr goes to `<temp-root>/dashboard/logs/status-bottom.log`, not onto the bar. The loop's cadence and the reason for it are in [dashboard.md](dashboard.md) ("The bottom status line").

| Region | Populating script | Standalone command |
|---|---|---|
| Bottom status line (all windows) | `status-bottom.sh`, rendered into `@pgai_status_bottom` by `status-bottom-loop.sh` | `status-bottom.sh $PGAI_AGENT_KANBAN_ROOT_PATH` |

Note: `status-bottom.sh` takes the kanban root as a positional argument, not via `--kanban-root`. The standalone command prints exactly the line the loop would store.

**No tmux option the dashboard sets may contain a `#()` job.** tmux re-runs such a job on almost every redraw of the text it sits in, once per attached client. `team/scripts/dashboard/lint_tmux_format_jobs.py` reads every shell script under `team/scripts/dashboard/` and reports any tmux `set-option`, `set`, `set-window-option`, `setw` or `select-pane -T` line containing `#(`. That covers status formats, `status-left`/`status-right`, window-status formats, pane-border formats and titles. `run-unit-tests.sh` runs it on every certification run as a quality-class check, so a finding is recorded and never fails the run. To add text to the status line, extend `status-bottom.sh`. Do not add a `#()` job to the format.

The status line surfaces three kinds of conditional indicator that are otherwise easy to miss. All of them are absent under normal state and precede the date/time on the right:

- **HALT** — an all-caps bracketed marker (for example `[HALT GLOBAL]` or `[HALT-AFTER:PROJECT <event>]`) rendered when a halt file is present.
- **APPROVAL** — `✋ APPROVAL(n)` in yellow when `n>=1` HUMAN-APPROVE gate tasks are `WAITING` or `BACKLOG` across all registered projects (`[APPROVAL(n)]` on `NO_COLOR` or dumb terminals). Absent when no gates are pending. Drill into window 13 for the row-level detail.
- **DEFER** — one segment per deferral scope in force, resolved through `team/scripts/lib/defer_posture.sh`:
  - **Kanban-wide.** A deferral set in `kanban.cfg` or a root-scoped campaign file renders first, as `✋ DEFER GLOBAL <=level (n waiting)`. Its count covers every project that inherits it.
  - **Per project.** Each project whose own settings defer its bugs gets `✋ DEFER <project> <=level (n waiting)`, alphabetically after the global one. Its own settings mean its own `DEFER-LOW-BUGS` campaign file, or its `project.cfg`.
  - **End version.** `until vX.Y.Z` goes before the count when a campaign file names the release it runs until, for example `✋ DEFER project-a <=low until v2.33.0 (2 waiting)`.
  - **Zero waiting.** A segment shows whenever its deferral is on, `(0 waiting)` included, so a deferral holding nothing back is still on the bar. A project with no deferral of its own gets no segment.
  - **Unreadable posture.** An unparseable campaign file, or a refused `[release]` value, renders `⚠️ DEFER CONFIG UNREADABLE: <path>` instead of falling silent.

  Every form is plain bracket-free text on `NO_COLOR` or dumb terminals. All of them resolve through the single-source `team/scripts/dashboard/lib/defer_scope.sh` helper. For the same resolution spoken in resolver vocabulary, see the `== posture ==` section of `why.sh --details`.

  Given a project name as its second argument, `status-bottom.sh` renders that one project's bar instead. There the deferral keeps a single `BUGS DEFERRED n (<=level)[ until vX.Y.Z]` segment, omitted when `n` is 0.

When more than one indicator fires, HALT is emitted first, then APPROVAL, then the DEFER segments — GLOBAL first, then projects alphabetically.

### Rich-mode glyph legend

In rich mode (color-capable terminal, `NO_COLOR` unset), each status-bar segment is prefixed with a leading glyph so the segment kind is scannable at a glance. Segment order and field content are unchanged from prior versions — only the leading glyph is new. The two siblings (`status-bottom.sh` and `status-right.sh`) source a single library, `team/scripts/lib/status_glyphs.sh`, so the glyph literals live in exactly one place; edits there reflow both siblings on the next render.

| Segment | Rich mode | NO_COLOR / TERM=dumb (unchanged) |
|---|---|---|
| install version | 📝 v1.12.0 | v1.12.0 |
| install version — unknown | 📝 unknown | unknown |
| project:version | 📝 proj:v0.8.0 | proj:v0.8.0 |
| project:version — unknown | 📝 unknown | unknown |
| PM mode — auto | 🟢 PM:auto | PM:auto |
| PM mode — manual | 🟡 PM:manual | PM:manual |
| approval (n>=1 pending) | ✋ APPROVAL(n) | [APPROVAL(n)] |
| HALT (full stop) | 🛑 HALT GLOBAL / 🛑 HALT PROJECT ... | [HALT GLOBAL] / [HALT PROJECT ...] |
| HALT-AFTER (draining) | ⚠️ HALT-AFTER ... | [HALT-AFTER ...] |
| DEFER (deferral in force, one per scope) | ✋ DEFER GLOBAL <=level[ until vX.Y.Z] (n waiting) / ✋ DEFER <project> <=level[ until vX.Y.Z] (n waiting) | DEFER GLOBAL <=level[ until vX.Y.Z] (n waiting) / DEFER <project> <=level[ until vX.Y.Z] (n waiting) |
| BUGS DEFERRED (per-project bar, n>=1) | ✋ BUGS DEFERRED n (<=level)[ until vX.Y.Z] | BUGS DEFERRED n (<=level)[ until vX.Y.Z] |
| DEFER (posture unreadable) | ⚠️ DEFER CONFIG UNREADABLE: <path> | DEFER CONFIG UNREADABLE: <path> |
| timestamp | 📅 Wed 2026-07-09T... | Wed 2026-07-09T... |

The version segments render `📝 unknown` (rich) or `unknown` (dumb) when the version cannot be resolved — no silent blank segment. The 🛑 (HALT) vs ⚠️ (HALT-AFTER) distinction is deliberate: HALT is a full stop, HALT-AFTER is a soft drain to the current RC boundary, and the two must be tellable apart at a glance.

**NO_COLOR / TERM=dumb output is byte-identical to prior versions.** Setting either variable (or running under a dumb terminal) suppresses every glyph prefix; the resulting segments are the exact strings the status bar produced before the glyph set landed. If a terminal renders emoji double-width and misaligns the bar, set `NO_COLOR` for that session — the dumb-mode bar is unchanged by design and is the intended fallback.
