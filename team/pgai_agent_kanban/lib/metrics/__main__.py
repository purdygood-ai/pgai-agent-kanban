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

    compute_agent_averages AGENT_METRICS_ARGS...
        scripts/dashboard/agent-metrics.sh (see agents.main()).

Exit codes: the function's own; 2 for an unknown verb or malformed
options.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from typing import Callable, Optional

from . import agents
from .common import TextResult, color_enabled
from .costs import cost_report
from .costs_pane import cost_block, rc_label, sum_amounts
from .history import history_csv, rc_rollups_jsonl, show_metrics

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
        help=(
            "render only this project (default: every project in projects.cfg, in priority order)"
        ),
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
        help=(
            "fallback project (PGAI_PROJECT_NAME) for --history-csv and --per-agent (default: "
            "empty)"
        ),
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
        own, report_args = args[:split], args[split + 1:]
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
            "the token aggregator to run for a missing roll-up (default: the project's dev-tree "
            "copy)"
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


_VERBS: dict[str, Callable[[list[str]], int]] = {
    "show_metrics": _show_metrics,
    "cost_report": _cost_report,
    "history_csv": _history_csv,
    "rc_rollups_jsonl": _rc_rollups_jsonl,
    "cost_block": _cost_block,
    "rc_label": _rc_label,
    "sum_amounts": _sum_amounts,
    "compute_agent_averages": agents.main,
}


def main(argv: Optional[list[str]] = None) -> int:
    """Run one metrics verb: ``VERB [ARGS...]`` (see the module docstring for each verb).

    Returns the verb's own exit code; 2 (``EXIT_USAGE``) for a missing or
    unknown verb, or for options argparse rejects.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in _VERBS:
        print(f"pgai_agent_kanban.lib.metrics: VERB must be one of: {', '.join(_VERBS)}", file=sys.stderr)
        return EXIT_USAGE
    try:
        return _VERBS[args[0]](args[1:])
    except SystemExit as exc:  # argparse's own usage error
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE


sys.exit(main())
