# Dashboard

Operator reference for the tmux dashboard. The dashboard surfaces live kanban state — version, RC progress, queues, blocked tasks — across a multi-window tmux session. This guide focuses on the metrics window, which monitors per-RC token spend and task throughput over time.

For the broader dashboard tour (status window, logs window, terminal window, attention window) see the **Dashboard** section of the top-level `README.md`. This document is the source of truth for the metrics window specifically.

## Metrics window

The metrics window is window 6 in the tmux dashboard (`Ctrl-B 6`). It is split into two vertical panes that together cover both the in-flight and historical views of RC cost data.

| Pane | Script | What it shows |
|------|--------|---------------|
| Left | `$KANBAN_ROOT/scripts/dashboard/metrics.sh` | Today's per-project metrics — RCs shipped, wall time, tokens with cache hit %, task count — plus a current-RC block (tasks done, elapsed wall time, tokens so far). Reads `metrics/day/<date>.json` and `metrics/rc/<v>.json`. |
| Right | `$KANBAN_ROOT/scripts/dashboard/show-metrics.sh` | Historical RC metrics from `metrics/history.csv`: the last N release candidates with token totals, cache hit rate, and task counts. |

Both panes refresh on the standard dashboard interval (`PGAI_DASHBOARD_REFRESH_SECONDS`, default 5 seconds).

The metrics window is a dedicated view so operators can scan recent-release economics without leaving the dashboard.

### Historical view (right pane)

The right pane runs `show-metrics.sh`, which reads `projects/<name>/metrics/history.csv` and renders the last N completed RCs as a single tabular view. The default is the last 10 RCs.

Columns:

| Column | Source field | Meaning |
|--------|--------------|---------|
| `rc` | `rc` | Release-candidate version string (e.g. `v0.29.15`). |
| `wall_time` | `wall_time_minutes` | RC duration in minutes, measured from `opened_at` to `closed_at`. Renders `--` for any RC whose `wall_time_minutes` field is absent (see below). |
| `input` | `input_tokens` | Total input tokens consumed by every agent invocation for the RC. Comma-separated for readability. |
| `output` | `output_tokens` | Total output tokens produced across the RC. |
| `cache_read` | `cache_read_tokens` | Tokens served from the prompt cache rather than re-billed at the input rate. Higher is cheaper. |
| `hit_rate%` | `cache_hit_rate_pct` | Cache hit rate as a percentage with one decimal place. Computed as `cache_read / (cache_read + input)`. |
| `tasks` | `tasks_total` | Total task folders produced for the RC across all agents. |

Numeric columns are formatted with comma separators (for example, `5,644,879`). Empty cells render as `--` rather than `0` so missing data is visually distinct from a real zero.

### Per-agent sub-view

Pass `--per-agent` to switch the view from history (the default) to a per-agent token breakdown for the **most recent** RC. The script locates the highest-version `metrics/rc/v*.json` file and renders the `tokens.by_agent` block as a table.

Columns:

| Column | Source field | Meaning |
|--------|--------------|---------|
| `agent` | object key | Agent role (`pm`, `coder`, `writer`, `tester`, `cm`, etc.). |
| `input` | `input` | Input tokens that agent consumed during the RC. |
| `output` | `output` | Output tokens that agent produced during the RC. |
| `cache_read` | `cache_read` | Cache-read tokens for that agent. |
| `invocations` | `invocations` | Number of times the agent was woken during the RC. |

Agents are sorted alphabetically. The header reads `RC Metrics: <project> — per-agent (<version>)` so the RC under inspection is unambiguous.

### Wall time

The `wall_time` column reads `wall_time_minutes` from `history.csv`. The metrics pipeline records `opened_at` when CM opens an RC and `closed_at` when CM finalizes it, and the aggregator computes `wall_time_minutes` from the two timestamps for the row the dashboard reads.

A row renders `wall_time` as `--` only when the `wall_time_minutes` field is absent for that RC — for example, an RC that has not yet closed, or an older history row written before both timestamps were captured. A `--` is not a signal that the RC failed or was abandoned; it means only that no wall-time value exists for that row.

### Invoking show-metrics.sh manually

The right pane runs `show-metrics.sh` under `watch`, but the script is also a standalone operator tool. Run it any time you want a one-shot historical view outside the tmux dashboard.

```bash
# Default: last 10 RCs across all registered projects
$KANBAN_ROOT/scripts/dashboard/show-metrics.sh

# Show the last 25 RCs instead of the default 10
$KANBAN_ROOT/scripts/dashboard/show-metrics.sh --last 25

# Scope to a specific project (when the kanban hosts more than one)
$KANBAN_ROOT/scripts/dashboard/show-metrics.sh --project my-project

# Combine the two
$KANBAN_ROOT/scripts/dashboard/show-metrics.sh --project my-project --last 50

# Per-agent token breakdown for the most recent RC
$KANBAN_ROOT/scripts/dashboard/show-metrics.sh --per-agent

# Per-agent breakdown scoped to a project
$KANBAN_ROOT/scripts/dashboard/show-metrics.sh --project my-project --per-agent
```

Useful flags at a glance:

| Flag | Default | Effect |
|------|---------|--------|
| `--project <name>` | all registered projects (iterate-all) | Project whose metrics directory to read; omit to cover every registered project. |
| `--last <N>` | `10` | Show the last N rows of `history.csv`. Ignored when `--per-agent` is set. |
| `--per-agent` | off | Switch to the per-agent token breakdown for the most recent RC. |
| `--history-csv <path>` | derived from project | Override the CSV path (primarily for testing). |
| `--kanban-root <path>` | `$PGAI_AGENT_KANBAN_ROOT_PATH` | Point the script at a different kanban root. |
| `--no-color` | off | Disable ANSI color output. Also disabled when `NO_COLOR` is set or `TERM=dumb`. |
| `-h`, `--help` | — | Print the inline help banner and exit. |

The script exits non-zero with a message on stderr when the requested CSV (default view) or RC JSON file (per-agent view) is missing. This is the expected behavior for a brand-new project that has not yet shipped an RC.
