"""
pgai_agent_kanban.lib.metrics — command-line entry point.

Invoked by the operator scripts as
``pp_run_ops pgai_agent_kanban.lib.metrics <verb> [args...]``; each verb is
the name of the function it runs, and prints what that function returns.

    show_metrics --kanban-root=ROOT [--project=NAME] [--last=N]
                 [--history-csv=PATH] [--per-agent=true|false]
                 [--no-color=true|false] [--env-project=NAME]
        scripts/dashboard/show-metrics.sh, after its own argument parsing.
        Colour also follows NO_COLOR and TERM (see common.color_enabled).

    cost_report [--script=PATH] [--aggregator=PATH]
                [--default-kanban-root=ROOT] [--default-project=NAME]
                -- COST_REPORT_ARGS...
        scripts/cost-report.sh: everything after ``--`` is the script's own
        argument list. A missing roll-up runs the aggregator
        (``python3 AGGREGATOR ...``), whose output reaches this process's
        stdout and stderr directly.

    history_csv CSV_PATH [PROJECT_FILTER]
    rc_rollups_jsonl PROJECT_FILTER FILE...
        scripts/metrics-report.sh --csv and --format jsonl ("" filters
        nothing).

    cost_block ROLLUP PRICING INDENT_COUNT
    rc_label RC_ROLLUP
    sum_amounts AMOUNT...
        scripts/dashboard/costs.sh.

    metrics_pane USE_COLOR TODAY PROJECTS_JSON RESET BOLD DIM CYAN GREEN YELLOW [NOW]
        scripts/dashboard/metrics.sh: the Metrics pane, printed line by line
        (see metrics_pane.metrics_pane_lines). USE_COLOR is "true" or
        "false"; each escape is "" when colour is off. NOW, an ISO-8601
        time, is the render time (empty or absent: the current time).

    compute_agent_averages AGENT_METRICS_ARGS...
        scripts/dashboard/agent-metrics.sh (see agents.main()).

``--help`` / ``-h`` on its own prints this text; after a verb it prints
that verb's usage. Either exits 0.

Exit codes: the function's own; 2 for an unknown verb or malformed
options.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import UTC
from collections.abc import Callable

from ..timeutil import try_parse_iso

from . import agents
from .common import TextResult, color_enabled
from .costs import cost_report
from .costs_pane import cost_block, rc_label, sum_amounts
from .history import history_csv, rc_rollups_jsonl, show_metrics
from .metrics_pane import metrics_pane_lines

EXIT_USAGE = 2


def _emit(result: TextResult) -> int:
    sys.stdout.write(result.stdout)
    sys.stdout.flush()
    sys.stderr.write(result.stderr)
    return result.exit_code


def _is_true(value: str) -> bool:
    return value == "true"


def _show_metrics(args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="show_metrics", allow_abbrev=False)
    parser.add_argument(
        "--kanban-root",
        default="",
        help="kanban root; projects are read from ROOT/projects/<name>/ (default: empty)",
    )
    parser.add_argument(
        "--project",
        default="",
        help=("render only this project (default: every project in projects.cfg, in priority order)"),
    )
    parser.add_argument(
        "--last",
        default="10",
        help="how many of the newest history rows to show (default: 10)",
    )
    parser.add_argument(
        "--history-csv",
        default="",
        help="render this history CSV instead of the project's own (needs a project)",
    )
    parser.add_argument(
        "--per-agent",
        default="false",
        help="true renders the newest RC roll-up's per-agent token table (default: false)",
    )
    parser.add_argument(
        "--no-color",
        default="false",
        help="true forces colour off; NO_COLOR and TERM are also honoured (default: false)",
    )
    parser.add_argument(
        "--env-project",
        default="",
        help=("fallback project (PGAI_PROJECT_NAME) for --history-csv and --per-agent (default: " "empty)"),
    )
    opts = parser.parse_args(args)
    return _emit(
        show_metrics(
            opts.kanban_root,
            project=opts.project,
            last=opts.last,
            history_csv=opts.history_csv,
            per_agent=_is_true(opts.per_agent),
            use_color=color_enabled(_is_true(opts.no_color)),
            env_project=opts.env_project,
        )
    )


def _run_aggregator(aggregator: str, args: list[str]) -> None:
    """Run the token aggregator, its output going straight to ours."""
    try:
        subprocess.run([sys.executable, aggregator, *args], check=False)
    except OSError:
        pass


def _cost_report(args: list[str]) -> int:
    if "--" in args:
        split = args.index("--")
        own, report_args = args[:split], args[split + 1 :]
    else:
        own, report_args = args, []
    parser = argparse.ArgumentParser(prog="cost_report", allow_abbrev=False)
    parser.add_argument(
        "--script",
        default="",
        help="the script whose header --help prints (default: empty)",
    )
    parser.add_argument(
        "--aggregator",
        default="",
        help=(
            "the token aggregator to run for a missing roll-up (default: the kanban root's "
            "pm-agent/aggregate_tokens.py, else the copy in this package's own tree)"
        ),
    )
    parser.add_argument(
        "--default-kanban-root",
        default="",
        help="kanban root when the report arguments carry no --kanban-root (default: empty)",
    )
    parser.add_argument(
        "--default-project",
        default="",
        help="project when the report arguments carry no --project (default: empty)",
    )
    opts = parser.parse_args(own)
    return _emit(
        cost_report(
            report_args,
            default_kanban_root=opts.default_kanban_root,
            default_project=opts.default_project,
            aggregator=opts.aggregator,
            aggregate=_run_aggregator,
            help_source=opts.script,
        )
    )


def _history_csv(args: list[str]) -> int:
    if not 1 <= len(args) <= 2:
        print("history_csv: expected CSV_PATH [PROJECT_FILTER]", file=sys.stderr)
        return EXIT_USAGE
    sys.stdout.write(history_csv(args[0], args[1] if len(args) == 2 else ""))
    return 0


def _rc_rollups_jsonl(args: list[str]) -> int:
    if not args:
        print("rc_rollups_jsonl: expected PROJECT_FILTER FILE...", file=sys.stderr)
        return EXIT_USAGE
    return _emit(rc_rollups_jsonl(args[1:], args[0]))


def _cost_block(args: list[str]) -> int:
    if len(args) != 3 or not args[2].isdigit():
        print("cost_block: expected ROLLUP PRICING INDENT_COUNT", file=sys.stderr)
        return EXIT_USAGE
    sys.stdout.write(cost_block(args[0], args[1], " " * int(args[2])))
    return 0


def _rc_label(args: list[str]) -> int:
    if len(args) != 1:
        print("rc_label: expected RC_ROLLUP", file=sys.stderr)
        return EXIT_USAGE
    label = rc_label(args[0])
    if label:
        print(label)
    return 0


def _sum_amounts(args: list[str]) -> int:
    print(sum_amounts(args))
    return 0


def _metrics_pane(args: list[str]) -> int:
    if len(args) not in (9, 10):
        print(
            "metrics_pane: expected USE_COLOR TODAY PROJECTS_JSON RESET BOLD DIM CYAN GREEN YELLOW [NOW]",
            file=sys.stderr,
        )
        return EXIT_USAGE
    use_color, today, projects_json, reset, bold, dim, cyan, green, yellow = args[:9]
    now = None
    if len(args) == 10 and args[9].strip():
        now = try_parse_iso(args[9].strip())
        if now is None:
            print(f"metrics_pane: NOW is not an ISO-8601 time: {args[9]!r}", file=sys.stderr)
            return EXIT_USAGE
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
    # Print each line as it is rendered, so a malformed roll-up that raises
    # part-way leaves the lines before it on the pane.
    for line in metrics_pane_lines(
        _is_true(use_color), today, projects_json, reset, bold, dim, cyan, green, yellow, now=now
    ):
        print(line)
    return 0


#: Usage of the verbs that take positional arguments only, printed for
#: ``VERB --help``; the argparse verbs answer ``--help`` themselves.
_POSITIONAL_USAGE: dict[str, str] = {
    "history_csv": (
        "usage: history_csv CSV_PATH [PROJECT_FILTER]\n"
        "Print the history.csv at CSV_PATH as CSV, keeping only PROJECT_FILTER's rows\n"
        '("" or absent keeps every row); with no matching row, the header alone.'
    ),
    "rc_rollups_jsonl": (
        "usage: rc_rollups_jsonl PROJECT_FILTER FILE...\n"
        "Print each per-RC roll-up FILE as one compact, key-sorted JSON line, in the\n"
        'order given, leaving out roll-ups of other projects ("" keeps every one);\n'
        "an unreadable FILE is skipped with a warning on stderr."
    ),
    "cost_block": (
        "usage: cost_block ROLLUP PRICING INDENT_COUNT\n"
        "Print what scripts/dashboard/costs.sh shows for one roll-up: its total on the\n"
        "first line, then the cost block priced by PRICING, indented INDENT_COUNT spaces."
    ),
    "rc_label": (
        "usage: rc_label RC_ROLLUP\n"
        "Print an RC header's detail for RC_ROLLUP: 'started <shipped_at>, <N> tasks' or\n"
        "'<N> tasks'; nothing for an unreadable roll-up."
    ),
    "sum_amounts": (
        "usage: sum_amounts AMOUNT...\n"
        "Print the sum of the dollar AMOUNTs, each running sum rounded to cents; 0 for none."
    ),
    "metrics_pane": (
        "usage: metrics_pane USE_COLOR TODAY PROJECTS_JSON RESET BOLD DIM CYAN GREEN YELLOW [NOW]\n"
        "Print the dashboard Metrics pane line by line (metrics_pane.metrics_pane_lines),\n"
        'as scripts/dashboard/metrics.sh shows it. USE_COLOR is "true" or "false";\n'
        'each escape (RESET ... YELLOW) is "" when colour is off. NOW, an ISO-8601\n'
        "time, is the render time (empty or absent: the current time)."
    ),
}

_HELP_FLAGS = ("--help", "-h")

_VERBS: dict[str, Callable[[list[str]], int]] = {
    "show_metrics": _show_metrics,
    "cost_report": _cost_report,
    "history_csv": _history_csv,
    "rc_rollups_jsonl": _rc_rollups_jsonl,
    "cost_block": _cost_block,
    "rc_label": _rc_label,
    "sum_amounts": _sum_amounts,
    "metrics_pane": _metrics_pane,
    "compute_agent_averages": agents.main,
}


def main(argv: list[str] | None = None) -> int:
    """Run one metrics verb: ``VERB [ARGS...]`` (see the module docstring for each verb).

    Returns the verb's own exit code; 2 (``EXIT_USAGE``) for a missing or
    unknown verb, or for options argparse rejects.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in _HELP_FLAGS:
        print(__doc__.strip())
        return 0
    if len(args) == 2 and args[0] in _POSITIONAL_USAGE and args[1] in _HELP_FLAGS:
        print(_POSITIONAL_USAGE[args[0]])
        return 0
    if not args or args[0] not in _VERBS:
        print(f"pgai_agent_kanban.lib.metrics: VERB must be one of: {', '.join(_VERBS)}", file=sys.stderr)
        return EXIT_USAGE
    try:
        return _VERBS[args[0]](args[1:])
    except SystemExit as exc:  # argparse's own usage error
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE


sys.exit(main())
