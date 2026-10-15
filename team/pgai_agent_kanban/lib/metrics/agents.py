"""
pgai_agent_kanban.lib.metrics.agents -- the agent-metrics text renderer:
per-agent, per-project averages.

Computes the CLI twin of the metrics window's per-agent breakdown: instead
of a single RC's raw token counts (team/scripts/dashboard/show-metrics.sh
--per-agent), this module averages wall time, tokens, and task count per
agent, per project, and totals tokens and tasks, across a SPAN of SHIPPED
per-RC metrics rollups (projects/<name>/metrics/rc/v*.json, produced by
team/scripts/lib/metrics_aggregator.py -- read that module for the exact
tokens.by_agent / tasks.by_agent / wall.by_agent field shapes; this module
does not change that schema). Avg Wall is a mean per TASK; Avg Tokens and
Avg Tasks are each a mean per invocation RC -- the rendered column headers
name each column's own denominator so "Avg Tasks" is never misread as a
window total. Total Tokens and Total Tasks are the plain sums over the
same invocation RCs.

RENDERED BLOCK (one per project)
================================

    === Agent Metrics: <project> (<span>) ===
    <blank>
    Agent  Avg Wall (min/task)  Avg Tokens (/RC)  Avg Tasks (/RC)  Total Tokens  Total Tasks
    <one row per agent, sorted by agent name>
    <span label>: <N> shipped RCs (<M> cancelled excluded)
    <blank>

Averages print with exactly one decimal and thousands separators
(2.0, 4.7, 41,000.0), rounded by the shared one-decimal rule
(pgai_agent_kanban.lib.agent_metrics.round_one_decimal(), the same rule
the REST twin applies) and formatted by number_format.format_one_decimal();
totals print as thousands-separated integers via number_format.format_int().
An absent average (an agent with no per-task wall data) prints '--'.

The span and its header/footer wording:

    span                  header span               footer label
    last N (default 10)   last N RCs                window
    all shipped           all shipped RCs           all
    since vX.Y.Z          shipped RCs since vX.Y.Z  since vX.Y.Z

The footer's N is the number of shipped RCs the span selected (it can be
below the requested window when fewer have shipped) and M the number of
non-shipped rollups inside the span's range, which contribute to no figure.
The footer prints whenever the table does, including when M is 0. A
project with no shipped rollup in the span prints "(no metrics yet)", plus
the footer when M is above 0.

THE AVERAGING ARITHMETIC LIVES IN pgai_agent_kanban.lib.agent_metrics
====================================================================

`select_rc_span()`, `compute_agent_averages()` and `round_one_decimal()`
come from `pgai_agent_kanban.lib.agent_metrics` -- the ONE place the span
selection and averaging arithmetic are defined. The REST twin
(`GET /agent-metrics` in `team/pgai_agent_kanban/api/reads.py`) calls the
exact same functions. This module never re-derives the arithmetic; it is
the text renderer (thousands separation, column layout, argument parsing)
that a REST JSON response has no use for. See that module's docstring for
the full averaging-window contract (which RCs enter which average, the
absent-agent and absent-wall-time contracts).

`scripts/dashboard/agent-metrics.sh` reaches :func:`main` through
`pp_run_ops pgai_agent_kanban.lib.metrics compute_agent_averages`, the
verb named for the shared function every rendered figure goes through.
`team/scripts/lib/agent_metrics.py` re-exports this module (and the
arithmetic) for a caller that puts `team/scripts/lib/` on `sys.path`, and
runs :func:`main` when executed directly.

USAGE (CLI; either entry point takes the same arguments):

    pp_run_ops pgai_agent_kanban.lib.metrics compute_agent_averages ARGS...
    python3 team/scripts/lib/agent_metrics.py ARGS...

    ARGS is one of:
        --kanban-root PATH --project NAME [SPAN]
        --kanban-root PATH [SPAN]
            (no --project: iterates every subdirectory of PATH/projects/)
        --projects-json '[{"name":"p","rc_dir":"/abs/path"}]' [SPAN]
            (what team/scripts/dashboard/agent-metrics.sh passes: it
            resolves the registered-project list and each project's rc_dir
            via the shared bash projects.cfg reader)

    SPAN is at most one of:
        --window N       newest N shipped RCs (default: --window 10)
        --last N         newest N shipped RCs (same span as --window N)
        --all            every shipped RC
        --since vX.Y.Z   every shipped RC at or above vX.Y.Z (semver order;
                         the leading "v" is optional)

Exactly one of --project (with --kanban-root) or --projects-json must be
given.

Exit codes:
    0 -- always (read-only; missing/malformed metrics render placeholders)
    1 -- usage error: neither or both of --project/--projects-json given,
         more than one SPAN flag given, --window or --last not a positive
         integer, or --since not a version of the form vX.Y.Z. The message
         is one "ERROR: ..." line on stderr naming the flag.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from typing import NoReturn

from ..agent_metrics import (
    RcSpan,
    compute_agent_averages,
    load_rc_window,
    round_one_decimal,
    select_rc_span,
    total_tokens,
)
from ..number_format import format_int, format_one_decimal

__all__ = [
    "total_tokens",
    "compute_agent_averages",
    "load_rc_window",
    "span_header",
    "span_footer",
    "render_project_block",
    "main",
]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

# Every averaged column names its own denominator so "Avg Tasks" can never
# be misread as a window total: Avg Wall is a mean per TASK (pooled from
# wall.by_agent across the agent's invocation RCs -- see this module's
# docstring and pgai_agent_kanban.lib.agent_metrics's docstring for why
# that is a different, and different-shaped, average from the other two);
# Avg Tokens and Avg Tasks are each a mean per invocation RC. The two
# totals sit right of the averages and are sums over the same RCs.
_COLUMNS = (
    "Agent",
    "Avg Wall (min/task)",
    "Avg Tokens (/RC)",
    "Avg Tasks (/RC)",
    "Total Tokens",
    "Total Tasks",
)
_COL_WIDTHS = (12, 20, 17, 16, 14, 12)


def _fmt_row(values: tuple[str, ...]) -> str:
    fmt = "{:<%d}" % _COL_WIDTHS[0] + "".join(" {:>%d}" % w for w in _COL_WIDTHS[1:])
    return fmt.format(*values)


def _fmt_average(value: float | None) -> str:
    """One average cell: shared one-decimal rounding, then the shared
    one-decimal formatter ('--' for an absent average)."""
    return format_one_decimal(round_one_decimal(value))


def _as_span(span: RcSpan | int) -> RcSpan:
    """Accept a bare window size as shorthand for RcSpan.last(window)."""
    if isinstance(span, RcSpan):
        return span
    return RcSpan.last(span)


def span_header(span: RcSpan | int) -> str:
    """The span phrase in a block's header: "last 10 RCs", "all shipped
    RCs", or "shipped RCs since vX.Y.Z" (the span's own bound)."""
    span = _as_span(span)
    if span.kind == "all":
        return "all shipped RCs"
    if span.kind == "since":
        return f"shipped RCs since {span.since}"
    return f"last {format_int(span.count)} RCs"


def span_footer(span: RcSpan | int, shipped_count: int, excluded_count: int) -> str:
    """The footer line narrating what the span selected, e.g.
    "window: 5 shipped RCs (1 cancelled excluded)"; the label is "all" or
    "since vX.Y.Z" for those spans."""
    span = _as_span(span)
    if span.kind == "all":
        label = "all"
    elif span.kind == "since":
        label = f"since {span.since}"
    else:
        label = "window"
    return (
        f"{label}: {format_int(shipped_count)} shipped RCs "
        f"({format_int(excluded_count)} cancelled excluded)"
    )


def render_project_block(
    project: str,
    rc_dir: pathlib.Path,
    span: RcSpan | int,
    colors: dict[str, str],
) -> list[str]:
    """Return the printable lines for one project's agent-metrics block.

    `span` selects the shipped rollups (an RcSpan; a bare int N means the
    newest N, RcSpan.last(N)). The block layout is documented in this
    module's docstring ("RENDERED BLOCK").

    `colors` carries the four ANSI escapes this renderer uses (reset, bold,
    dim, cyan), pre-computed by the caller (empty strings when color is
    disabled) -- the same argv-threading convention used throughout
    team/scripts/dashboard/ so the ANSI escape table stays single-sourced
    in team/scripts/lib/script_common.sh rather than re-transcribed here.
    """
    reset = colors.get("reset", "")
    bold = colors.get("bold", "")
    dim = colors.get("dim", "")
    cyan = colors.get("cyan", "")

    span = _as_span(span)
    lines: list[str] = []
    lines.append(f"{cyan}{bold}=== Agent Metrics: {project} ({span_header(span)}) ==={reset}")
    lines.append("")

    selection = select_rc_span(rc_dir, span)
    footer = f"{dim}{span_footer(span, selection.shipped_count, selection.excluded_count)}{reset}"
    if not selection.rollups:
        lines.append(f"  {dim}(no metrics yet){reset}")
        if selection.excluded_count:
            lines.append(footer)
        lines.append("")
        return lines

    averages = compute_agent_averages(selection.rollups)
    if not averages:
        lines.append(f"  {dim}(no agent invocations in this window){reset}")
        lines.append(footer)
        lines.append("")
        return lines

    lines.append(bold + _fmt_row(_COLUMNS) + reset)
    for agent in sorted(averages.keys()):
        a = averages[agent]
        lines.append(
            _fmt_row(
                (
                    agent,
                    _fmt_average(a["avg_wall_minutes"]),
                    _fmt_average(a["avg_tokens"]),
                    _fmt_average(a["avg_tasks"]),
                    format_int(a["total_tokens"]),
                    format_int(a["total_tasks"]),
                )
            )
        )

    lines.append(footer)
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


_DEFAULT_WINDOW = 10

# The span flags; at most one may be given. --window and --last select the
# same span (the newest N shipped RCs); --window is the long-standing name
# the dashboard's cadence command and the REST twin's ``window`` parameter
# use, --last the name that sits beside --all and --since.
_SPAN_FLAGS = ("--window", "--last", "--all", "--since")

_POSITIVE_INT_RE = re.compile(r"^\s*[0-9]+\s*$")


class _UsageError(Exception):
    """A command-line usage error; main() prints it as one "ERROR: ..."
    line on stderr and exits 1."""


class _ArgumentParser(argparse.ArgumentParser):
    """argparse, but a parse failure is a usage error with exit 1 (the
    shape every flag check in this module uses) instead of argparse's own
    exit 2."""

    def error(self, message: str) -> "NoReturn":  # type: ignore[override]
        raise _UsageError(message)


def _positive_int(flag: str, raw: str) -> int:
    if not _POSITIVE_INT_RE.match(raw) or int(raw) == 0:
        raise _UsageError(f"{flag} must be a positive integer (got: {raw})")
    return int(raw)


def span_from_args(
    window: str | None,
    last: str | None,
    all_shipped: bool,
    since: str | None,
) -> RcSpan:
    """Build the RcSpan the span flags name, validating them.

    At most one of the four may be given; with none, the span is the
    newest 10 shipped RCs. Raises _UsageError naming the offending flag(s)
    for a conflicting combination, a non-positive or non-integer --window
    or --last, or a --since that is not a version of the form vX.Y.Z.
    """
    given = [
        flag
        for flag, present in zip(
            _SPAN_FLAGS, (window is not None, last is not None, all_shipped, since is not None)
        )
        if present
    ]
    if len(given) > 1:
        raise _UsageError(
            "--window, --last, --all and --since are mutually exclusive; give at most one "
            f"(got: {' '.join(given)})"
        )
    if window is not None:
        return RcSpan.last(_positive_int("--window", window))
    if last is not None:
        return RcSpan.last(_positive_int("--last", last))
    if all_shipped:
        return RcSpan.all_shipped()
    if since is not None:
        try:
            return RcSpan.since_version(since)
        except ValueError:
            raise _UsageError(f"--since must be a version of the form vX.Y.Z (got: {since})") from None
    return RcSpan.last(_DEFAULT_WINDOW)


def main(argv: list[str] | None = None) -> int:
    """Render the agent-metrics blocks for *argv* (default: ``sys.argv[1:]``); return the exit code."""
    try:
        return _run(argv)
    except _UsageError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def _run(argv: list[str] | None) -> int:
    parser = _ArgumentParser(
        prog="agent_metrics.py",
        description=(
            "Print average wall time, average tokens and average tasks (one "
            "decimal) and total tokens and total tasks per agent per project, "
            "computed over a span of shipped per-RC metrics rollups, with a "
            "footer counting the shipped RCs used and the cancelled ones "
            "excluded. Read-only; exits 0 on missing or malformed metrics "
            "files, rendering placeholders instead. Usage errors exit 1."
        ),
    )
    parser.add_argument(
        "--project",
        metavar="NAME",
        help="Single project name. Requires --kanban-root. Mutually exclusive with --projects-json.",
    )
    parser.add_argument(
        "--kanban-root",
        metavar="PATH",
        help="Kanban root directory (used with --project). Projects are read from PATH/projects/.",
    )
    parser.add_argument(
        "--projects-json",
        metavar="JSON",
        help=(
            'JSON array of {"name": <project>, "rc_dir": <path>} descriptors. '
            "Mutually exclusive with --project/--kanban-root. When omitted and "
            "--project is also omitted, --kanban-root's PATH/projects/ subdirectories "
            "are each treated as a project."
        ),
    )
    span_group = parser.add_argument_group(
        "span (at most one of --window, --last, --all, --since; default: --window 10)"
    )
    span_group.add_argument(
        "--window",
        metavar="N",
        help="Average over the newest N shipped RCs (default: 10).",
    )
    span_group.add_argument(
        "--last",
        metavar="N",
        help="Average over the newest N shipped RCs (the same span as --window N).",
    )
    span_group.add_argument(
        "--all",
        action="store_true",
        dest="all_shipped",
        help="Average over every shipped RC.",
    )
    span_group.add_argument(
        "--since",
        metavar="vX.Y.Z",
        help="Average over every shipped RC at or above vX.Y.Z (semantic-version order).",
    )
    parser.add_argument(
        "--color",
        choices=("true", "false"),
        default="false",
        help="Enable ANSI color output (default: false).",
    )
    parser.add_argument(
        "--color-reset",
        default="",
        metavar="ESC",
        help="escape sequence that resets colour, used with --color true (default: empty)",
    )
    parser.add_argument(
        "--color-bold",
        default="",
        metavar="ESC",
        help="escape sequence for bold text, used with --color true (default: empty)",
    )
    parser.add_argument(
        "--color-dim",
        default="",
        metavar="ESC",
        help="escape sequence for dim text, used with --color true (default: empty)",
    )
    parser.add_argument(
        "--color-cyan",
        default="",
        metavar="ESC",
        help="escape sequence for cyan text, used with --color true (default: empty)",
    )

    args = parser.parse_args(argv)

    span = span_from_args(args.window, args.last, args.all_shipped, args.since)

    if args.projects_json and args.project:
        parser.error("--project and --projects-json are mutually exclusive")

    colors = {
        "reset": args.color_reset,
        "bold": args.color_bold,
        "dim": args.color_dim,
        "cyan": args.color_cyan,
    }

    descriptors: list[dict[str, str]] = []

    if args.projects_json:
        try:
            parsed = json.loads(args.projects_json)
        except json.JSONDecodeError as exc:
            parser.error(f"--projects-json is not valid JSON: {exc}")
            return 1  # unreachable; parser.error() raises, but satisfies type-checkers
        if not isinstance(parsed, list):
            parser.error("--projects-json must decode to a JSON array")
        descriptors = parsed
    else:
        if not args.kanban_root:
            parser.error("--kanban-root is required with --project (or omit --project to scan PATH/projects/)")
        root = pathlib.Path(args.kanban_root).expanduser()
        projects_dir = root / "projects"
        if args.project:
            descriptors = [
                {"name": args.project, "rc_dir": str(projects_dir / args.project / "metrics" / "rc")}
            ]
        else:
            if projects_dir.is_dir():
                for entry in sorted(projects_dir.iterdir()):
                    if entry.is_dir():
                        descriptors.append(
                            {"name": entry.name, "rc_dir": str(entry / "metrics" / "rc")}
                        )

    if not descriptors:
        print(f"{colors['cyan']}{colors['bold']}=== Agent Metrics ==={colors['reset']}")
        print()
        print(f"  {colors['dim']}(no projects found){colors['reset']}")
        print()
        return 0

    for desc in descriptors:
        name = desc.get("name", "unknown")
        rc_dir_str = desc.get("rc_dir") or ""
        rc_dir = pathlib.Path(rc_dir_str) if rc_dir_str else pathlib.Path("/nonexistent")
        for line in render_project_block(name, rc_dir, span, colors):
            print(line)

    return 0

