"""
pgai_agent_kanban.lib.metrics.costs — the token-usage and cost report.

``cost_report()`` is the one implementation of ``scripts/cost-report.sh``:
it parses the script's own arguments, resolves the project, pricing file
and scope, reads the roll-ups ``team/pm-agent/aggregate_tokens.py``
writes (``usage/daily/<YYYY-MM-DD>.json`` and
``usage/rc/<version>-tokens.json``) and renders the human-readable report
or the CSV rows. ``cost-report.sh`` reaches it through
``pp_run_ops pgai_agent_kanban.lib.metrics cost_report`` and ``GET /costs``
calls it in process with the route's query parameters as the same
arguments, so the two print the same bytes.

SCOPES
------
``--month YYYY-MM`` (default: the current month), ``--day YYYY-MM-DD``,
``--rc <version>`` (a missing leading ``v`` is added), and the date ranges
``--week [YYYY-Www|current]``, ``--year [YYYY|current]``, ``--since``,
``--until`` and ``--last N`` (alias ``--last-N-days``), which become one
``range`` scope whose value is ``START|END|LABEL``. At most one scope group
may be given; ``--since`` with ``--until`` is one group.

A ``--week YYYY-Www`` starts on the Monday that is ``(W-1)*7 - D + 1``
days after 1 January, D being 1 January's ISO weekday (1 = Monday); a week
or year that reaches past today ends today.

COST SOURCE
-----------
Totals are the aggregator's ``cost_usd`` fields, summed; nothing is
recomputed from token counts here. A roll-up carrying per-category costs
(``input_cost_usd`` and its siblings) was priced from the pricing table by
the aggregator, which the report announces with a warning block above its
header and shows as per-category cost columns.

PRICING
-------
``token_pricing.json`` is read from the project's ``dev_tree_path`` (in
``project.cfg``, or ``PROJECT.cfg`` on an older install) under
``team/scripts/lib/``, else from ``<kanban root>/team/scripts/lib/``.
Only its ``subscriptions`` block is used, for the subscription
comparison; without the file that block is left out and a warning goes to
stderr.

MISSING ROLL-UPS
----------------
When the roll-up a scope needs is missing, ``cost_report()`` hands the
aggregator's path and arguments to its *aggregate* callable (the CLI runs
the aggregator; a caller that must not start a process passes None and the
scope reports "no data").
"""

from __future__ import annotations

import calendar
import csv
import dataclasses
import datetime
import io
import pathlib
import re
from typing import Any, Callable, Optional, Sequence

from .common import TextOutput, TextResult, load_json_object

__all__ = [
    "CostReportArgs",
    "RollupRecord",
    "Aggregate",
    "cost_report",
    "parse_cost_report_args",
    "resolve_range",
    "script_help",
]

#: Runs the aggregator: called with its path and its arguments.
Aggregate = Callable[[str, list[str]], None]

_PRICING_REL = "team/scripts/lib/token_pricing.json"
_AGGREGATOR_REL = "team/pm-agent/aggregate_tokens.py"

_USAGE_HINT = "Run with --help for usage."

_FOOTER = (
    "",
    "--- New metrics surface (per-RC rollup + streaming) ---",
    "  metrics-report.sh --rc <version>   per-RC JSON rollup",
    "  metrics-report.sh --csv             cumulative RC history CSV",
    "  metrics-report.sh --format jsonl    JSON Lines for streaming",
    "  metrics-report.sh --tail            live-tail RC close events",
    "  metrics-report.sh --help            full usage",
)

_LEGACY_WARNING = (
    "*** WARNING: some or all costs in this report were computed from the",
    "*** pricing table (legacy tokens.json schema without total_cost_usd).",
    "*** Total cost may be under-reported.  Upgrade token_capture.sh to fix.",
    "",
)

_CSV_HEADER = (
    "scope_type", "scope_value", "project",
    "total_input_tokens", "total_output_tokens",
    "total_cache_create_tokens", "total_cache_read_tokens",
    "total_cost_usd", "total_invocations",
    "days_with_activity", "rcs_shipped",
    "agent", "agent_input_tokens", "agent_output_tokens",
    "agent_cost_usd", "agent_invocations",
    "model", "model_input_tokens", "model_output_tokens",
    "model_cost_usd", "model_invocations",
)


class _Stop(Exception):
    """Ends a run early with the given exit code (stdout/stderr already recorded)."""

    def __init__(self, exit_code: int) -> None:
        super().__init__(exit_code)
        self.exit_code = exit_code


class _Run(TextOutput):
    """The stdout and stderr lines one report run produces, and its two early exits."""

    def fail(self, *lines: str) -> "_Stop":
        self.err.extend(lines)
        return _Stop(1)

    def no_data(self, scope_label: str) -> "_Stop":
        self.out.append(f"no data for {scope_label}")
        return _Stop(0)


# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class CostReportArgs:
    """cost-report.sh's arguments, as parsed."""

    scope_type: str = "month"
    scope_value: str = ""
    project: str = ""
    csv_mode: bool = False
    kanban_root_override: str = ""
    scope_flag: str = ""
    since: str = ""
    until: str = ""
    week: Optional[str] = None
    year: Optional[str] = None
    last: Optional[str] = None
    help: bool = False


def parse_cost_report_args(argv: Sequence[str], run: _Run, default_project: str = "") -> CostReportArgs:
    """Parse *argv* left to right, the way cost-report.sh always has.

    A usage error records its message on *run* and raises ``_Stop(1)``;
    ``-h``/``--help`` sets ``help`` and stops parsing.
    """
    args = CostReportArgs(project=default_project)
    argv = list(argv)

    def _set_scope_flag(flag: str) -> None:
        if args.scope_flag:
            if (args.scope_flag, flag) in (("--since", "--until"), ("--until", "--since")):
                args.scope_flag = f"{args.scope_flag}+{flag}"
                return
            raise run.fail(
                f"ERROR: conflicting scope flags: {args.scope_flag} and {flag} cannot be combined.",
                _USAGE_HINT,
            )
        args.scope_flag = flag

    def _value(i: int, message: str) -> str:
        value = argv[i + 1] if i + 1 < len(argv) else ""
        if not value:
            raise run.fail(message)
        return value

    def _optional_value(i: int) -> Optional[str]:
        value = argv[i + 1] if i + 1 < len(argv) else ""
        return value if value and not value.startswith("--") else None

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--month":
            value = _value(i, "ERROR: --month requires YYYY-MM argument")
            _set_scope_flag("--month")
            args.scope_type, args.scope_value = "month", value
            i += 2
        elif arg == "--day":
            value = _value(i, "ERROR: --day requires YYYY-MM-DD argument")
            _set_scope_flag("--day")
            args.scope_type, args.scope_value = "day", value
            i += 2
        elif arg == "--rc":
            value = _value(i, "ERROR: --rc requires a version argument")
            _set_scope_flag("--rc")
            args.scope_type, args.scope_value = "rc", value
            i += 2
        elif arg in ("--week", "--year"):
            _set_scope_flag(arg)
            args.scope_type = "range"
            value = _optional_value(i)
            setattr(args, arg[2:], value if value is not None else "current")
            i += 2 if value is not None else 1
        elif arg in ("--since", "--until"):
            value = _value(i, f"ERROR: {arg} requires YYYY-MM-DD argument")
            _set_scope_flag(arg)
            args.scope_type = "range"
            setattr(args, arg[2:], value)
            i += 2
        elif arg in ("--last", "--last-N-days"):
            value = _value(i, f"ERROR: {arg} requires an integer argument")
            _set_scope_flag(arg)
            args.scope_type = "range"
            args.last = value
            i += 2
        elif arg == "--project":
            args.project = _value(i, "ERROR: --project requires a name argument")
            i += 2
        elif arg == "--csv":
            args.csv_mode = True
            i += 1
        elif arg == "--kanban-root":
            args.kanban_root_override = _value(i, "ERROR: --kanban-root requires a path argument")
            i += 2
        elif arg in ("-h", "--help"):
            args.help = True
            return args
        else:
            raise run.fail(f"ERROR: unknown argument: {arg}", _USAGE_HINT)
    return args


def script_help(script: str) -> str:
    """The script's usage text: its header comment from line 2 up to ``set -euo pipefail``.

    Each line loses a leading ``#`` and the one space after it, if any.
    """
    lines = pathlib.Path(script).read_text(encoding="utf-8").splitlines(keepends=True)
    out: list[str] = []
    for index, line in enumerate(lines[1:], start=2):
        if index > 2 and line.startswith("set -euo pipefail"):
            break
        out.append(re.sub(r"^# ?", "", line))
    return "".join(out)


def resolve_range(args: CostReportArgs, today: datetime.date, run: _Run) -> str:
    """The ``START|END|LABEL`` value of a range scope; a bad value stops the run."""
    today_str = today.isoformat()
    if args.week is not None:
        if args.week == "current":
            start = today - datetime.timedelta(days=today.isoweekday() - 1)
            end = today_str
            label = f"Week {start.year}-W{start.isocalendar()[1]:02d} ({start.isoformat()} through {end})"
        else:
            year_part = args.week.rsplit("-W", 1)[0]
            week_part = args.week.rsplit("-W", 1)[-1]
            try:
                jan1 = datetime.date(int(year_part), 1, 1)
                offset = (int(week_part) - 1) * 7 - jan1.isoweekday() + 1
                start = jan1 + datetime.timedelta(days=offset)
            except (ValueError, OverflowError):
                raise run.fail(f"ERROR: --week requires YYYY-Www or current (got '{args.week}')")
            end = (start + datetime.timedelta(days=6)).isoformat()
            if end > today_str:
                end = today_str
            label = f"Week {args.week} ({start.isoformat()} through {end})"
        return f"{start.isoformat()}|{end}|{label}"
    if args.year is not None:
        if args.year == "current":
            start_str = f"{today.year}-01-01"
            return f"{start_str}|{today_str}|Year {today.year} YTD ({start_str} through {today_str})"
        start_str, end = f"{args.year}-01-01", f"{args.year}-12-31"
        if end > today_str:
            end = today_str
        return f"{start_str}|{end}|Year {args.year} ({start_str} through {end})"
    if args.last is not None:
        if not re.fullmatch(r"[0-9]+", args.last):
            raise run.fail(f"ERROR: --last requires a positive integer argument (got '{args.last}')")
        if int(args.last) < 1:
            raise run.fail(f"ERROR: --last requires N >= 1 (got {args.last}). Use --day today for a single day.")
        start = today - datetime.timedelta(days=int(args.last) - 1)
        return f"{start.isoformat()}|{today_str}|Last {args.last} days ({start.isoformat()} through {today_str})"
    if args.since and args.until:
        return f"{args.since}|{args.until}|Range {args.since} through {args.until}"
    if args.since:
        return f"{args.since}|{today_str}|Since {args.since} through {today_str}"
    return f"earliest|{args.until}|Through {args.until}"


def _dev_tree_path(project_dir: pathlib.Path) -> str:
    """``dev_tree_path`` from the project's config, read as ``key=value`` lines.

    Prefers ``project.cfg`` and falls back to ``PROJECT.cfg``. The key loses
    any ``#`` comment and every space; the value loses one leading and one
    trailing space and one pair of surrounding quotes.
    """
    for name in ("project.cfg", "PROJECT.cfg"):
        cfg = project_dir / name
        if cfg.is_file():
            break
    else:
        return ""
    for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
        key, _sep, val = line.partition("=")
        key = key.split("#", 1)[0].replace(" ", "")
        val = val[1:] if val.startswith(" ") else val
        val = val[:-1] if val.endswith(" ") else val
        for quote in ('"', "'"):
            val = val[1:] if val.startswith(quote) else val
            val = val[:-1] if val.endswith(quote) else val
        if key == "dev_tree_path" and val:
            return val
    return ""


# ---------------------------------------------------------------------------
# Roll-ups
# ---------------------------------------------------------------------------


def _blank_breakdown() -> dict[str, Any]:
    return {"input": 0, "output": 0, "cost_usd": 0.0, "invocations": 0}


@dataclasses.dataclass
class RollupRecord:
    """The totals and breakdowns of every roll-up absorbed for one scope."""

    total_input: int = 0
    total_output: int = 0
    total_cache_create: int = 0
    total_cache_read: int = 0
    total_cost: float = 0.0
    total_input_cost: float = 0.0
    total_cache_create_cost: float = 0.0
    total_cache_read_cost: float = 0.0
    total_output_cost: float = 0.0
    total_invocations: int = 0
    by_agent: dict[str, dict[str, Any]] = dataclasses.field(default_factory=dict)
    by_model: dict[str, dict[str, Any]] = dataclasses.field(default_factory=dict)
    days_with_activity: int = 0
    rcs_shipped: int = 0
    days_observed: int = 0
    scope_label: str = ""
    daily_costs: list[float] = dataclasses.field(default_factory=list)
    # True once an absorbed roll-up carried per-category costs, which the
    # aggregator writes only when it priced tokens from the pricing table.
    has_legacy_data: bool = False

    def _add_totals(self, i: int, o: int, cc: int, cr: int, cost: float, inv: int, category: tuple) -> None:
        i_cost, cc_cost, cr_cost, o_cost = category
        if i_cost + cc_cost + cr_cost + o_cost > 0:
            self.has_legacy_data = True
        self.total_input += i
        self.total_output += o
        self.total_cache_create += cc
        self.total_cache_read += cr
        self.total_input_cost += i_cost
        self.total_cache_create_cost += cc_cost
        self.total_cache_read_cost += cr_cost
        self.total_output_cost += o_cost
        self.total_cost += cost
        self.total_invocations += inv

    @staticmethod
    def _add(table: dict[str, dict[str, Any]], key: str, i: int, o: int, cost: float, inv: int) -> None:
        entry = table.setdefault(key, _blank_breakdown())
        entry["input"] += i
        entry["output"] += o
        entry["cost_usd"] += cost
        entry["invocations"] += inv

    def absorb_rc_file(self, data: dict[str, Any]) -> None:
        """Absorb an RC roll-up: one entry per task, then one RC shipped."""
        for task in data.get("tasks", []):
            i = int(task.get("input", 0) or 0)
            o = int(task.get("output", 0) or 0)
            cc = int(task.get("cache_creation_tokens", 0) or 0)
            cr = int(task.get("cache_read_tokens", 0) or 0)
            inv = int(task.get("invocations", 1) or 1)
            agent = str(task.get("agent", "unknown")).lower()
            model = str(task.get("model", "unknown"))
            cost = float(task.get("cost_usd", 0) or 0)
            self._add_totals(i, o, cc, cr, cost, inv, _category_costs(task))
            self._add(self.by_agent, agent, i, o, cost, inv)
            self._add(self.by_model, model, i, o, cost, inv)
        self.rcs_shipped += 1

    def absorb_day_file(self, data: dict[str, Any]) -> None:
        """Absorb a daily roll-up: its totals, its by-agent and by-model tables, its shipped RCs."""
        totals = data.get("totals", {})
        i = int(totals.get("input_tokens", 0) or 0)
        o = int(totals.get("output_tokens", 0) or 0)
        cc = int(totals.get("cache_creation_tokens", 0) or 0)
        cr = int(totals.get("cache_read_tokens", 0) or 0)
        cost = float(totals.get("cost_usd", 0) or 0)
        inv = int(totals.get("invocations", 0) or 0)
        self._add_totals(i, o, cc, cr, cost, inv, _category_costs(totals))
        if i > 0 or o > 0 or cc > 0 or cr > 0 or cost > 0:
            self.days_with_activity += 1
            self.daily_costs.append(cost)
        for agent, entry in data.get("by_agent", {}).items():
            self._add(
                self.by_agent, agent.lower(),
                int(entry.get("input", 0) or 0), int(entry.get("output", 0) or 0),
                float(entry.get("cost_usd", 0) or 0), int(entry.get("invocations", 0) or 0),
            )
        for model, entry in data.get("by_model", {}).items():
            self._add(
                self.by_model, model,
                int(entry.get("input", 0) or 0), int(entry.get("output", 0) or 0),
                float(entry.get("cost_usd", 0) or 0), int(entry.get("invocations", 0) or 0),
            )
        for rc in data.get("rcs_shipped", []):
            if rc:
                self.rcs_shipped += 1


def _category_costs(entry: dict[str, Any]) -> tuple[float, float, float, float]:
    """(input, cache creation, cache read, output) cost fields; 0 where absent."""
    return (
        float(entry.get("input_cost_usd", 0) or 0),
        float(entry.get("cache_creation_cost_usd", 0) or 0),
        float(entry.get("cache_read_cost_usd", 0) or 0),
        float(entry.get("output_cost_usd", 0) or 0),
    )


def _load(path: pathlib.Path, run: _Run) -> Optional[dict[str, Any]]:
    """A roll-up file's object; None when absent, a warning on stderr when unreadable."""
    if not path.is_file():
        return None
    data, reason = load_json_object(path)
    if data is None:
        run.err.append(f"WARNING: cannot read {path}: {reason}")
    return data


def _json_files(directory: pathlib.Path, keep: Callable[[pathlib.Path], bool]) -> list[pathlib.Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if keep(p))


def _build_record(
    args: CostReportArgs,
    usage_rc_dir: pathlib.Path,
    usage_daily_dir: pathlib.Path,
    today: datetime.date,
    aggregate: Callable[[list[str]], None],
    run: _Run,
) -> RollupRecord:
    """Absorb every roll-up the scope covers; "no data" stops the run with exit 0."""
    rec = RollupRecord()
    scope_type, scope_value = args.scope_type, args.scope_value

    if scope_type == "rc":
        rc_file = usage_rc_dir / f"{scope_value}-tokens.json"
        if not rc_file.is_file():
            aggregate(["--rc", scope_value])
        data = _load(rc_file, run)
        if data is None:
            raise run.no_data(f"rc={scope_value}")
        if not data.get("tasks") and int(data.get("totals", {}).get("invocations", 0)) == 0:
            raise run.no_data(f"rc={scope_value}")
        rec.absorb_rc_file(data)
        rec.scope_label = f"RC {scope_value}"
        return rec

    if scope_type == "day":
        day_file = usage_daily_dir / f"{scope_value}.json"
        if not day_file.is_file():
            aggregate(["--day", scope_value])
        data = _load(day_file, run)
        if data is None:
            raise run.no_data(f"day={scope_value}")
        totals = data.get("totals", {})
        if int(totals.get("invocations", 0)) == 0 and float(totals.get("cost_usd", 0)) == 0:
            raise run.no_data(f"day={scope_value}")
        rec.absorb_day_file(data)
        rec.scope_label = f"Day {scope_value}"
        return rec

    if scope_type == "month":
        month_prefix = scope_value

        def _in_month(p: pathlib.Path) -> bool:
            return p.name.startswith(month_prefix) and p.name.endswith(".json")

        day_files = _json_files(usage_daily_dir, _in_month)
        if not day_files:
            aggregate(["--all"])
            day_files = _json_files(usage_daily_dir, _in_month)
        try:
            month_year, month_m = int(month_prefix[:4]), int(month_prefix[5:7])
        except ValueError:
            raise run.fail(f"ERROR: --month requires YYYY-MM (got '{month_prefix}')")
        if today.year == month_year and today.month == month_m:
            rec.days_observed = today.day
            rec.scope_label = today.strftime("%B %Y")
        else:
            rec.days_observed = calendar.monthrange(month_year, month_m)[1]
            rec.scope_label = f"{month_prefix[:4]}-{month_prefix[5:7]}"
        found_any = False
        for day_file in day_files:
            data = _load(day_file, run)
            if data is None:
                continue
            rec.absorb_day_file(data)
            found_any = True
        rc_seen: set[str] = set()
        for rc_file in _json_files(usage_rc_dir, lambda p: p.name.endswith("-tokens.json")):
            rd = _load(rc_file, run)
            if rd is None:
                continue
            if rd.get("shipped_at", "").startswith(month_prefix):
                rc_seen.add(rd.get("version", ""))
        rec.rcs_shipped = len(rc_seen)
        if not found_any:
            raise run.no_data(f"month={scope_value}")
        return rec

    if scope_type == "range":
        parts = scope_value.split("|", 2)
        if len(parts) != 3:
            raise run.fail(f"ERROR: malformed range scope_value: {scope_value!r}")
        range_start, range_end, range_label = parts
        all_day_files = _json_files(usage_daily_dir, lambda p: p.suffix == ".json" and len(p.stem) == 10)
        if range_start == "earliest":
            if not all_day_files:
                raise run.no_data("range (no day-rollup files found)")
            range_start = all_day_files[0].stem
        found_any = False
        for day_file in all_day_files:
            if not range_start <= day_file.stem <= range_end:
                continue
            data = _load(day_file, run)
            if data is None:
                continue
            rec.absorb_day_file(data)
            found_any = True
        if not found_any:
            raise run.no_data(f"range ({range_start} through {range_end})")
        rec.scope_label = range_label
        try:
            rec.days_observed = (
                datetime.date.fromisoformat(range_end) - datetime.date.fromisoformat(range_start)
            ).days + 1
        except (ValueError, AttributeError):
            rec.days_observed = 0
        return rec

    raise run.fail(f"ERROR: unknown scope_type '{scope_type}'")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _fmt_tokens(n: int) -> str:
    return f"{n:,}"


def _fmt_usd(v: float) -> str:
    if v >= 1000:
        return f"${v:,.0f}"
    return f"${v:.2f}"


def _fmt_pct(part: float, total: float) -> str:
    if total == 0:
        return " 0%"
    return f"{part / total * 100:.0f}%"


def _render_csv(args: CostReportArgs, project: str, rec: RollupRecord) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(_CSV_HEADER)
    totals = [
        args.scope_type, args.scope_value, project,
        rec.total_input, rec.total_output,
        rec.total_cache_create, rec.total_cache_read,
        round(rec.total_cost, 6), rec.total_invocations,
        rec.days_with_activity, rec.rcs_shipped,
    ]
    everything = [rec.total_input, rec.total_output, round(rec.total_cost, 6), rec.total_invocations]
    writer.writerow(totals + ["ALL", *everything, "ALL", *everything])
    for agent in sorted(rec.by_agent):
        entry = rec.by_agent[agent]
        writer.writerow(
            totals
            + [agent, entry["input"], entry["output"], round(entry["cost_usd"], 6), entry["invocations"]]
            + [""] * 5
        )
    for model in sorted(rec.by_model):
        entry = rec.by_model[model]
        writer.writerow(
            totals
            + [""] * 5
            + [model, entry["input"], entry["output"], round(entry["cost_usd"], 6), entry["invocations"]]
        )
    return buf.getvalue()


def _shortfall(credit: float, cost: float) -> str:
    diff = credit - cost
    if diff >= 0:
        return f"${diff:.0f} surplus"
    return f"${abs(diff):,.0f} short"


def _render_text(args: CostReportArgs, project: str, rec: RollupRecord, pricing: dict[str, Any]) -> list[str]:
    scope_type = args.scope_type
    subs = pricing.get("subscriptions", {})
    have_category_costs = (
        rec.total_input_cost != 0
        or rec.total_cache_create_cost != 0
        or rec.total_cache_read_cost != 0
        or rec.total_output_cost != 0
    )
    display_total = rec.total_cost
    proj_monthly: Optional[float] = None
    if scope_type == "month" and rec.days_with_activity:
        proj_monthly = display_total / rec.days_with_activity * 30
    daily_avg = display_total / max(rec.days_with_activity, 1) if scope_type == "month" else None
    if scope_type in ("month", "range") and rec.days_with_activity > 0:
        days_denom: Optional[int] = rec.days_with_activity
    elif scope_type == "day":
        days_denom = 1
    else:
        days_denom = None

    lines: list[str] = list(_LEGACY_WARNING) if rec.has_legacy_data else []
    lines.append(f"=== Token Usage: {rec.scope_label} — {project} ===")
    if scope_type in ("month", "range"):
        lines.append(f"Days with activity: {rec.days_with_activity} / {rec.days_observed or '?'}")
        if scope_type == "month":
            lines.append(f"RCs shipped:        {rec.rcs_shipped}")
    elif scope_type == "day":
        lines.append(f"RCs on this day:    {rec.rcs_shipped}")
    elif scope_type == "rc":
        lines.append(f"RC:                 {args.scope_value}")
    lines.append(f"Total invocations:  {rec.total_invocations:,}")
    lines.append("")

    lines.append("Token totals:")
    categories = (
        ("Input (new):", rec.total_input, rec.total_input_cost),
        ("Cache writes:", rec.total_cache_create, rec.total_cache_create_cost),
        ("Cache reads:", rec.total_cache_read, rec.total_cache_read_cost),
        ("Output:", rec.total_output, rec.total_output_cost),
    )
    for label, tokens, cost in categories:
        if have_category_costs:
            lines.append(f"  {label:<16} {_fmt_tokens(tokens):>15} tokens    {_fmt_usd(cost)}")
        else:
            lines.append(f"  {label:<25} {_fmt_tokens(tokens):>15} tokens")
    lines.append("")

    if rec.by_agent:
        lines.append("By agent (cost share):")
        for agent, entry in sorted(rec.by_agent.items(), key=lambda kv: kv[1]["cost_usd"], reverse=True):
            share = f"{agent.upper():<7} {_fmt_usd(entry['cost_usd'])} ({_fmt_pct(entry['cost_usd'], display_total)})"
            if days_denom is not None and days_denom > 0:
                lines.append(f"  {share}  — {entry['invocations'] / days_denom:.0f} invocations/day avg")
            else:
                lines.append(f"  {share}  — {entry['invocations']} invocations")
        lines.append("")

    if rec.by_model:
        lines.append("By model:")
        for model, entry in sorted(rec.by_model.items(), key=lambda kv: kv[1]["cost_usd"], reverse=True):
            lines.append(f"  {model:<28} {_fmt_usd(entry['cost_usd'])} ({_fmt_pct(entry['cost_usd'], display_total)})")
        lines.append("")

    lines.append(f"Total cost:         {_fmt_usd(display_total)}")
    if daily_avg is not None:
        lines.append(f"Daily average:      {_fmt_usd(daily_avg)}")
    if proj_monthly is not None:
        lines.append(f"Projected monthly:  {_fmt_usd(proj_monthly)} (extrapolated from observed days)")
    lines.append("")

    if subs:
        pro_credit = float(subs.get("claude_pro_programmatic_credit", 0))
        max_credit = float(subs.get("claude_max_programmatic_credit", 0))
        plus_credit = float(subs.get("chatgpt_plus", 0))
        pro2_credit = float(subs.get("chatgpt_pro", 0))
        # The projection when there is one, else the scope's own total.
        compare_cost = proj_monthly if proj_monthly is not None else display_total
        lines.append("Subscription comparison:")
        lines.append(f"  Anthropic Pro programmatic credit ({_fmt_usd(pro_credit)}):   {_shortfall(pro_credit, compare_cost)}")
        lines.append(f"  Anthropic Max programmatic credit ({_fmt_usd(max_credit)}):  {_shortfall(max_credit, compare_cost)}")
        lines.append(f"  Anthropic API direct (pay-as-you-go):           {_fmt_usd(compare_cost)}")
        lines.append(f"  Codex via ChatGPT Plus ({_fmt_usd(plus_credit)}):              {_shortfall(plus_credit, compare_cost)}")
        lines.append(f"  Codex via ChatGPT Pro ({_fmt_usd(pro2_credit)}):               {_shortfall(pro2_credit, compare_cost)}")
        lines.append("")
        # The Max subscription is prepaid and its credit covers usage up to
        # its own value; usage above it is paid at API rates.
        operator_cost = max_credit + max(0, compare_cost - max_credit)
        if compare_cost > max_credit:
            savings_vs_pure_api = compare_cost - operator_cost
        else:
            savings_vs_pure_api = compare_cost - max_credit
        lines.append("Cheapest if mixed:")
        if savings_vs_pure_api < 0:
            lines.append(
                f"  Max subscription + API overflow: {_fmt_usd(operator_cost)}"
                f" (costs ${abs(savings_vs_pure_api):.0f} MORE vs. pure API at low usage)"
            )
        else:
            lines.append(
                f"  Max subscription + API overflow: {_fmt_usd(operator_cost)}"
                f" (saves {_fmt_usd(savings_vs_pure_api)} vs. pure API)"
            )

    lines.extend(_FOOTER)
    return lines


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def cost_report(
    argv: Sequence[str],
    *,
    default_kanban_root: str,
    default_project: str = "",
    aggregator: str = "",
    aggregate: Optional[Aggregate] = None,
    help_source: str = "",
    today: Optional[datetime.date] = None,
) -> TextResult:
    """Run cost-report.sh's report for *argv*; return its exit code, stdout and stderr.

    Args:
        argv:                cost-report.sh's arguments, unchanged.
        default_kanban_root: The kanban root when ``--kanban-root`` is not
            given (the CLI passes ``PGAI_AGENT_KANBAN_ROOT_PATH``).
        default_project:     The project when ``--project`` is not given
            (``PGAI_PROJECT_NAME``).
        aggregator:          The aggregator the caller found beside itself;
            the project's dev tree copy is used when this is empty or
            missing.
        aggregate:           Runs the aggregator for a missing roll-up, or
            None to report the scope as it stands.
        help_source:         The script whose header is printed for
            ``--help``.
        today:               "Today" for the default month and the ranges
            (default: the local date).

    Returns:
        Exit 0 with the report (or a "no data for <scope>" line), or exit 1
        with an ``ERROR:`` line on stderr for a usage or configuration error.
    """
    run = _Run()
    try:
        return _report(
            argv, run, default_kanban_root, default_project, aggregator, aggregate, help_source,
            today or datetime.date.today(),
        )
    except _Stop as stop:
        return run.result(stop.exit_code)


def _report(
    argv: Sequence[str],
    run: _Run,
    default_kanban_root: str,
    default_project: str,
    aggregator: str,
    aggregate: Optional[Aggregate],
    help_source: str,
    today: datetime.date,
) -> TextResult:
    args = parse_cost_report_args(argv, run, default_project)
    if args.help:
        return TextResult(0, script_help(help_source) if help_source else "")

    kanban_root = args.kanban_root_override or default_kanban_root
    if not kanban_root or not pathlib.Path(kanban_root).is_dir():
        raise run.fail(f"ERROR: kanban root not found: {kanban_root}")
    if not args.project:
        raise run.fail(
            "ERROR: no project specified. Pass --project <name> or set PGAI_PROJECT_NAME.", _USAGE_HINT
        )
    project_dir = pathlib.Path(f"{kanban_root}/projects/{args.project}")
    if not project_dir.is_dir():
        raise run.fail(f"ERROR: project directory not found: {kanban_root}/projects/{args.project}")

    dev_tree = _dev_tree_path(project_dir)
    pricing_file = ""
    if dev_tree and pathlib.Path(f"{dev_tree}/{_PRICING_REL}").is_file():
        pricing_file = f"{dev_tree}/{_PRICING_REL}"
    elif pathlib.Path(f"{kanban_root}/{_PRICING_REL}").is_file():
        pricing_file = f"{kanban_root}/{_PRICING_REL}"
    else:
        run.err.append("WARNING: token_pricing.json not found; subscription comparison unavailable.")

    if not (aggregator and pathlib.Path(aggregator).is_file()):
        aggregator = ""
        if dev_tree and pathlib.Path(f"{dev_tree}/{_AGGREGATOR_REL}").is_file():
            aggregator = f"{dev_tree}/{_AGGREGATOR_REL}"

    if not args.scope_value and args.scope_type == "month" and not args.scope_flag:
        args.scope_value = today.strftime("%Y-%m")
    if args.scope_type == "range":
        args.scope_value = resolve_range(args, today, run)
    if args.scope_type == "rc" and not args.scope_value.startswith("v"):
        args.scope_value = f"v{args.scope_value}"

    def _aggregate(extra: list[str]) -> None:
        if aggregate is None or not aggregator:
            return
        cmd = ["--project", args.project, *extra]
        if args.kanban_root_override:
            cmd += ["--kanban-root", args.kanban_root_override]
        aggregate(aggregator, cmd)

    pricing: dict[str, Any] = {}
    if pricing_file:
        try:
            loaded, reason = load_json_object(pathlib.Path(pricing_file))
        except UnicodeDecodeError as exc:
            loaded, reason = None, str(exc)
        if loaded is None:
            run.err.append(f"WARNING: malformed token_pricing.json: {reason}")
        else:
            pricing = loaded

    rec = _build_record(args, project_dir / "usage" / "rc", project_dir / "usage" / "daily", today, _aggregate, run)
    if args.csv_mode:
        return TextResult(0, _render_csv(args, args.project, rec), run.result(0).stderr)
    run.out.extend(_render_text(args, args.project, rec, pricing))
    return run.result(0)
