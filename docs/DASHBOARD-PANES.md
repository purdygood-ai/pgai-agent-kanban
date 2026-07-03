# Dashboard panes — script and standalone-command reference

Every visible region of the tmux dashboard is populated by a single shell script (or Python module) under `$KANBAN_ROOT/scripts/dashboard/`. Each of those scripts also runs as a standalone CLI tool that prints the same content to your terminal and exits. This document maps every window and pane in the dashboard to the script that populates it and the exact command you can run from any shell to view that pane's output directly — useful for debugging a pane, scripting against its data, or inspecting kanban state without launching tmux.

The window numbers, names, and pane positions are taken from `$KANBAN_ROOT/scripts/dashboard/create.sh`, which is the source of truth for the dashboard layout. The metrics window (window 6) has its own deeper column reference in [dashboard.md](dashboard.md) — this document does not duplicate that detail.

## Required environment

Most commands need only `PGAI_AGENT_KANBAN_ROOT_PATH`. Window 4 (git) additionally requires `PGAI_DEV_TREE_PATH`. Set both before running any standalone command:

```bash
export PGAI_AGENT_KANBAN_ROOT_PATH=$HOME/pgai_agent_kanban
export PGAI_DEV_TREE_PATH=$HOME/develop/pgai-agent-kanban
```

All script paths in the tables below are relative to `$KANBAN_ROOT/scripts/dashboard/`. Run them from there, or prepend the full path.

## Window 1 — main

The overview window. Header on top, three middle columns (queues, progress, next cron firings), and a live log tail on the bottom.

| Pane position | Populating script | Standalone command |
|---|---|---|
| Top (HEADER, ~11%) | `show-multi.sh` | `show-multi.sh --mode header --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |
| Left (QUEUES, ~30%) | `show-multi.sh` | `show-multi.sh --mode queues --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |
| Center (PROGRESS, ~45%) | `show-multi.sh` | `show-multi.sh --mode progress --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |
| Right (CRON FIRINGS, ~25%) | `next-cron-firings.sh` | `next-cron-firings.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |
| Bottom (LOGS, ~40%) | `logs.sh` | `logs.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH --stdout` |

`show-multi.sh` dispatches to `show-header.sh`, `show-queues.sh`, or `show-progress.sh` per project according to the `--mode` argument. In tmux, the bottom logs pane runs a blocking `tail -F`; the `--stdout` flag prints the last 30 lines of each cron log file once and exits.

## Window 2 — visibility

A grid of nine narrow columns: three input streams on the left (bugs, priorities, requirements), five agent queues on the right (pm / coder / writer / tester / cm), and a one-row legend at the bottom. In tmux, each column is wrapped in a `while sleep` loop so 24-bit ANSI color survives; standalone, each script renders once and exits.

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

## Window 3 — attention

Single pane: every BLOCKED task across every project, with its reason and recommended next step.

| Pane position | Populating script | Standalone command |
|---|---|---|
| single | `attention.sh` | `attention.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |

## Window 4 — git

Two vertical panes covering the dev tree's git state. Both panes need `PGAI_DEV_TREE_PATH` to point at a local clone.

| Pane position | Populating script | Standalone command |
|---|---|---|
| Left (~65%, git status) | `git-status.sh` | `PGAI_DEV_TREE_PATH=$PGAI_DEV_TREE_PATH git-status.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |
| Right (~35%, recent tags) | `git-recent-tags.sh` | `PGAI_DEV_TREE_PATH=$PGAI_DEV_TREE_PATH git-recent-tags.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |

The left pane reports current branch and sync state, develop and main sync state, uncommitted changes, and recent rc/* branches. The right pane lists the newest tags (at least 10) sorted newest first.

## Window 5 — metadata

Single pane: kanban version, PM mode, HALT state, and one block per registered project (workflow type, active RC, last released, version ceilings).

| Pane position | Populating script | Standalone command |
|---|---|---|
| single | `metadata.sh` | `metadata.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |

## Window 6 — metrics

Two vertical panes covering RC cost data. The left pane shows today's per-project metrics plus the current-RC block; the right pane is the rolling history view. For column-by-column meaning, sub-views (`--per-agent`), and the wall-time column behavior, see [dashboard.md](dashboard.md).

| Pane position | Populating script | Standalone command |
|---|---|---|
| Left (today + current RC) | `metrics.sh` | `metrics.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |
| Right (history.csv) | `show-metrics.sh` | `show-metrics.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |

## Window 7 — logs

Full-screen view of the merged cron-log tail. Same script as window 1's bottom pane.

| Pane position | Populating script | Standalone command |
|---|---|---|
| single | `logs.sh` | `logs.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH --stdout` |

In tmux this pane runs a blocking `tail -F` so new lines stream in. The `--stdout` flag prints the last 30 lines of each cron log once and exits, which is what you want from a terminal.

## Window 8 — debug-logs

Single-pane merged stream of every agent's debug log under `$KANBAN_ROOT/logs/debug/<agent>.log`. Color-coded per agent.

| Pane position | Populating script | Standalone command |
|---|---|---|
| single | `debug-logs.sh` | `debug-logs.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH --stdout` |

In tmux, this pane is gated per-project by the `[debug] verbose_mode = true` setting in each project's `project.cfg`. When one or more registered projects have verbose mode enabled, the pane runs a blocking `tail -F` merged stream; when no project has it enabled, the pane loops on a placeholder. Each project is checked independently — enabling verbose mode in one project does not affect the others. See `operator-troubleshooting.md` ("Enabling debug logs") for the full configuration reference.

When **multiple projects** have verbose mode enabled simultaneously, every line in the pane is prefixed with the project set, so the operator can see which multi-project context produced the stream:

```
[proj1,proj2] [coder] ...
```

When only **one project** has verbose mode enabled, the project prefix is omitted and lines render as `[agent] ...`. The project annotation exists because the debug log files at `$KANBAN_ROOT/logs/debug/<agent>.log` are kanban-wide rather than per-project — per-line project attribution is not possible from log content alone, so the prefix labels the stream's multi-project context as a whole.

The `--stdout` flag bypasses the gating, prints the last 30 lines of each debug log once, and exits. In `--stdout` mode, a `Projects (verbose): [proj1,proj2]` header is printed when more than one project has verbose mode enabled; the same line prefix is applied to the per-agent output below.

The legacy `PGAI_VERBOSE_MODE=1` env var still works as a deprecated shim (it forces verbose for every project and every agent and emits a deprecation warning); new installs should configure `[debug] verbose_mode` in each `project.cfg` instead.

## Window 9 — training-logs

Single pane: each agent's newest reasoning trace from `$KANBAN_ROOT/logs/training/<agent>/<ts>-<task-id>.md`, sorted by mtime with the newest at the bottom. Color-coded per agent.

| Pane position | Populating script | Standalone command |
|---|---|---|
| single | `training-logs.sh` | `PGAI_REASONING_TRACE=1 training-logs.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |

The script is already standalone-capable: with `PGAI_REASONING_TRACE=1` it renders one pass and exits; with the variable unset or `0` it prints a placeholder and exits immediately. In tmux it runs under `watch -t -c -n 30`.

## Window 10 — terminal

Three interactive bash shells pre-`cd`'d to useful directories (kanban root, tasks, queues). No pane script populates this window — there is no standalone equivalent. Open a terminal yourself if you want one.

| Pane position | Populating script | Standalone command |
|---|---|---|
| top-left | (interactive bash) | N/A — interactive shell |
| top-right | (interactive bash) | N/A — interactive shell |
| bottom | (interactive bash) | N/A — interactive shell |

## Window 11+ — drill-N (per project)

One window per registered project, named `drill-1`, `drill-2`, and so on. Each drill window uses the same five-pane layout as window 1 but every pane is scoped to a single project via `--project <name>`. The commands below show the canonical first project (`pgai-agent-kanban`); substitute the actual project name when calling them standalone.

The cron firings pane is shared with window 1 because cron is a system-level schedule, not per-project.

| Pane position | Populating script | Standalone command |
|---|---|---|
| Top (HEADER, ~11%) | `show-header.sh` | `show-header.sh --project <name> --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |
| Left (QUEUES, ~30%) | `show-queues.sh` | `show-queues.sh --project <name> --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |
| Center (PROGRESS, ~45%) | `show-progress.sh` | `show-progress.sh --project <name> --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |
| Right (CRON, ~25%) | `next-cron-firings.sh` | `next-cron-firings.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |
| Bottom (LOGS, ~40%) | `logs.sh` | `logs.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH --stdout` |

## tmux status bar

The bottom status line of every tmux window is not a pane — it is populated via tmux's `status-format` mechanism — but it surfaces a visible dashboard element, so it is documented here for completeness.

| Region | Populating script | Standalone command |
|---|---|---|
| Bottom status line (all windows) | `status-bottom.sh` | `status-bottom.sh $PGAI_AGENT_KANBAN_ROOT_PATH` |

Note: `status-bottom.sh` takes the kanban root as a positional argument, not via `--kanban-root`.
