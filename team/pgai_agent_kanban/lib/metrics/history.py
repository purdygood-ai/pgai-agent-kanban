"""
pgai_agent_kanban.lib.metrics.history — the RC history surfaces.

``show_metrics()`` renders the rc-metrics view: one table per project from
``projects/<name>/metrics/history.csv`` (RC, Wall, Blocked, Input, Output,
Cache Read, Cache Write, Tasks), or with ``per_agent`` the newest per-RC
metrics roll-up's per-agent token table. ``scripts/dashboard/show-metrics.sh``
reaches it through ``pp_run_ops pgai_agent_kanban.lib.metrics show_metrics``
and ``GET /metrics`` calls it in process; both print what it returns.

``history_csv()`` and ``rc_rollups_jsonl()`` are ``metrics-report.sh``'s
``--csv`` and ``--format jsonl`` views of the same files.

``read_history_csv()`` is the one reader of ``history.csv``: the table
above, the CSV view, and GET /metrics?format=json's rows
(rows.metrics_rows_for_project) all read through it.

THE COLUMN CONTRACT
-------------------
``HISTORY_COLUMNS`` is the one declaration of history.csv's columns and
their order; the writer (``scripts/lib/metrics_csv_writer.py``) imports it
from here. Columns are only ever added at the end, so the header of a file
written before a column existed is a strict prefix of ``HISTORY_COLUMNS``.

A SHORT HEADER
--------------
The writer appends every row in full ``HISTORY_COLUMNS`` order, so a file
whose header predates a column can hold rows with more fields than its
header names. ``read_history_csv()`` reads such a file as if its header
were already complete: when the header is a strict prefix of
``HISTORY_COLUMNS``, each field past the header's end is keyed by the
``HISTORY_COLUMNS`` name at its position instead of landing under the
``None`` rest key, and the returned field names widen to cover the widest
row. A row too short to reach a widened column reads that column as
``None`` (missing), never as 0. A header that is not a prefix of
``HISTORY_COLUMNS`` is read exactly as written. The writer rewrites a short
header on its next append, so this tolerance matters only until then.

THE HISTORY TABLE
-----------------
    === RC Metrics: <project> (last <N>) ===
    <blank>
    RC  Wall  Blocked  Input  Output  Cache Read  Cache Write  Tasks
    <one row per history.csv row, the last N>
    Blocked tracked from v2.24.0          (only when a row has no Blocked value)

Wall prints ``--`` when ``wall_time_minutes`` is empty and the raw value
otherwise. Blocked prints a single ``-`` when ``blocked_minutes`` is empty
or absent, and the comma-formatted minutes (a real 0 included) otherwise.
The token and task columns are comma-formatted by
``pgai_agent_kanban.lib.number_format.format_int`` (``--`` for empty). A
history.csv with a header and no rows prints ``No data``; a project with
no history.csv prints the ``(no releases yet)`` placeholder.
"""

from __future__ import annotations

import csv
import io
import json
import pathlib
import re
from typing import Iterable, Optional, Sequence

from ..number_format import format_int
from .common import Colors, TextOutput, TextResult, colors_for, load_json_object, registered_projects

__all__ = [
    "HISTORY_COLUMNS",
    "is_short_history_header",
    "read_history_csv",
    "show_metrics",
    "render_history_table",
    "render_per_agent",
    "history_csv",
    "rc_rollups_jsonl",
]

# Column layout of the history table; Blocked sits right of Wall.
_HISTORY_ROW = "{:<12} {:<10} {:>8} {:>10} {:>10} {:>12} {:>13}  {:>5}"
_HISTORY_HEADER = ("RC", "Wall", "Blocked", "Input", "Output", "Cache Read", "Cache Write", "Tasks")
# The footer printed once when any rendered row has no Blocked value.
_BLOCKED_UNTRACKED_FOOTER = "Blocked tracked from v2.24.0"

# Column layout of the per-agent table.
_AGENT_ROW = "{:<12} {:>10} {:>10} {:>12} {:>13} {:>11}"
_AGENT_HEADER = ("Agent", "Input", "Output", "Cache Read", "Cache Write", "Invocations")


#: history.csv's columns in the order the writer appends them -- a public
#: contract. Never reorder or rename a column; a new column goes at the end,
#: which is what lets a short header be widened (see "A SHORT HEADER" above).
HISTORY_COLUMNS: tuple[str, ...] = (
    "rc",
    "project",
    "workflow_type",
    "opened_at",
    "closed_at",
    "wall_time_minutes",
    "outcome",
    "tasks_total",
    "tasks_pm",
    "tasks_coder",
    "tasks_writer",
    "tasks_tester",
    "tasks_cm",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "cache_hit_rate_pct",
    "bugs_filed_during",
    "operator_waivers",
    "blocked_minutes",
)


def is_short_history_header(fieldnames: Sequence[str]) -> bool:
    """True when *fieldnames* is a non-empty, strict prefix of ``HISTORY_COLUMNS``.

    Names are compared with surrounding whitespace stripped. Such a header
    predates one or more columns and can be widened safely; any other
    header (reordered, renamed, empty, or longer) cannot.
    """
    names = tuple(str(name).strip() for name in fieldnames)
    return 0 < len(names) < len(HISTORY_COLUMNS) and names == HISTORY_COLUMNS[: len(names)]


def _widen_short_header(
    fieldnames: list[str], rows: list[dict]
) -> tuple[list[str], list[dict]]:
    """Key each row's fields past a short header by their ``HISTORY_COLUMNS`` names.

    ``csv.DictReader`` collects a row's surplus fields as a list under the
    ``None`` key; they move to the column names at their positions. Fields
    beyond the last known column stay under ``None``. When any row reached
    a column past the header, the field names widen to the widest row and a
    row that stops short of a widened column reads it as ``None``.
    """
    width = len(fieldnames)
    widest = width
    for row in rows:
        extras = row.get(None)
        if not extras:
            continue
        named = list(zip(HISTORY_COLUMNS[width:], extras))
        for name, value in named:
            row[name] = value
        leftover = extras[len(named):]
        if leftover:
            row[None] = leftover
        else:
            del row[None]
        widest = max(widest, width + len(named))
    if widest == width:
        return fieldnames, rows
    widened = fieldnames + list(HISTORY_COLUMNS[width:widest])
    for row in rows:
        for name in widened[width:]:
            row.setdefault(name, None)
    return widened, rows


def read_history_csv(path: pathlib.Path) -> tuple[list[str], list[dict[str, str]]]:
    """Read *path* as a header-led CSV; return ``(fieldnames, rows)``.

    A header that is a strict prefix of ``HISTORY_COLUMNS`` is read as if
    complete: fields past its end are keyed by their ``HISTORY_COLUMNS``
    names, and *fieldnames* widens to cover them (module docstring, "A
    SHORT HEADER"). Any other header is read exactly as written.

    Raises OSError when the file cannot be opened; each caller decides what
    an unreadable history means for its own surface.
    """
    with open(path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    if is_short_history_header(fieldnames):
        return _widen_short_header(fieldnames, rows)
    return fieldnames, rows


# ---------------------------------------------------------------------------
# The history table
# ---------------------------------------------------------------------------


def _fmt_wall(raw: object) -> str:
    """Wall time: ``--`` when empty, the raw value otherwise."""
    s = str(raw).strip()
    return s if s else "--"


def _fmt_blocked(raw: Optional[str]) -> str:
    """Blocked minutes: a single ``-`` when the row carries no value (the
    column predates the row, or the RC's ledger could not be read) -- not
    Wall's ``--``, because a present value, a real 0 included, is a
    measured figure rather than an absence."""
    s = "" if raw is None else str(raw).strip()
    return format_int(s) if s else "-"


def render_history_table(
    rows: Sequence[dict[str, str]], project: str, last_n: int, colors: Colors
) -> list[str]:
    """The history table's lines for *rows* (already read), last *last_n* of them."""
    if not rows:
        return ["No data"]
    rows = list(rows)[-last_n:]
    lines = [f"{colors.cyan}{colors.bold}=== RC Metrics: {project} (last {last_n}) ==={colors.reset}", ""]
    lines.append(f"{colors.bold}{_HISTORY_ROW.format(*_HISTORY_HEADER)}{colors.reset}")
    saw_untracked_row = False
    for row in rows:
        blocked = _fmt_blocked(row.get("blocked_minutes"))
        if blocked == "-":
            saw_untracked_row = True
        lines.append(
            _HISTORY_ROW.format(
                str(row.get("rc", "")).strip(),
                _fmt_wall(row.get("wall_time_minutes", "")),
                blocked,
                format_int(row.get("input_tokens", "")),
                format_int(row.get("output_tokens", "")),
                format_int(row.get("cache_read_tokens", "")),
                format_int(row.get("cache_write_tokens", "")),
                format_int(row.get("tasks_total", "")),
            )
        )
    if saw_untracked_row:
        lines.append(f"{colors.dim}{_BLOCKED_UNTRACKED_FOOTER}{colors.reset}")
    return lines


def _no_releases(project: str, colors: Colors) -> list[str]:
    return [
        f"{colors.cyan}{colors.bold}=== RC Metrics: {project} ==={colors.reset}",
        "",
        f"  {colors.dim}(no releases yet){colors.reset}",
        "",
    ]


def _no_projects(colors: Colors) -> list[str]:
    return [
        f"{colors.cyan}{colors.bold}=== RC Metrics History ==={colors.reset}",
        "",
        f"  {colors.dim}(no projects registered){colors.reset}",
        "",
    ]


# ---------------------------------------------------------------------------
# The per-agent table
# ---------------------------------------------------------------------------

_VERSION_RUN_RE = re.compile(r"(\d+)")


def _version_sort_key(name: str) -> tuple:
    """Order file names the way ``sort -V`` does for ``vX.Y.Z.json``: digit runs numerically."""
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part) for part in _VERSION_RUN_RE.split(name) if part
    )


def latest_rc_rollup(rc_dir: pathlib.Path) -> Optional[pathlib.Path]:
    """The ``v*.json`` roll-up in *rc_dir* that sorts last by version, or None."""
    if not rc_dir.is_dir():
        return None
    candidates = sorted(rc_dir.glob("v*.json"), key=lambda p: _version_sort_key(p.name))
    return candidates[-1] if candidates else None


def render_per_agent(data: dict, project: str, colors: Colors) -> list[str]:
    """The per-agent token table for one per-RC metrics roll-up."""
    rc_version = data.get("rc", "unknown")
    by_agent = data.get("tokens", {}).get("by_agent", {})
    lines = [f"{colors.cyan}{colors.bold}=== RC Metrics: {project} — per-agent ({rc_version}) ==={colors.reset}", ""]
    lines.append(f"{colors.bold}{_AGENT_ROW.format(*_AGENT_HEADER)}{colors.reset}")
    for agent in sorted(by_agent.keys()):
        tok = by_agent[agent]
        lines.append(
            _AGENT_ROW.format(
                agent,
                format_int(tok.get("input", 0)),
                format_int(tok.get("output", 0)),
                format_int(tok.get("cache_read", 0)),
                format_int(tok.get("cache_write", 0)),
                format_int(tok.get("invocations", 0)),
            )
        )
    return lines


# ---------------------------------------------------------------------------
# show_metrics
# ---------------------------------------------------------------------------


def _render_history_file(csv_path: pathlib.Path, project: str, last: str, colors: Colors, out: TextOutput) -> bool:
    """Render one project's history.csv into *out*; False when the run must stop (exit 1)."""
    try:
        last_n = int(last)
    except ValueError:
        out.err.append(f"ERROR: --last must be an integer (got: {last})")
        return False
    try:
        _fieldnames, rows = read_history_csv(csv_path)
    except OSError as exc:
        out.err.append(f"ERROR: Cannot read {csv_path}: {exc}")
        return False
    out.out.extend(render_history_table(rows, project, last_n, colors))
    return True


def show_metrics(
    kanban_root: str | pathlib.Path,
    *,
    project: str = "",
    last: str = "10",
    history_csv: str = "",
    per_agent: bool = False,
    use_color: bool = False,
    env_project: str = "",
) -> TextResult:
    """Render the rc-metrics view; the text both show-metrics.sh and GET /metrics print.

    Args:
        kanban_root: The kanban root; projects are read from
            ``<kanban_root>/projects/<name>/``.
        project:     One project to render. Empty renders every project
            registered in ``projects.cfg``, in priority order.
        last:        How many of the newest history rows to show, as given
            on the command line (default ``"10"``).
        history_csv: Render this CSV instead of a project's own history
            (needs a project, from *project* or *env_project*).
        per_agent:   Render the newest per-RC metrics roll-up's per-agent
            token table instead (needs a project, likewise).
        use_color:   Emit ANSI escapes (see common.color_enabled()).
        env_project: The ``PGAI_PROJECT_NAME`` fallback for the two modes
            that need a project.

    Returns:
        The exit code, stdout and stderr of the run: 0 on success, 1 when
        a mode lacks its project, its input file is missing or unreadable,
        or *last* is not an integer.
    """
    root = pathlib.Path(kanban_root) if str(kanban_root) else None
    colors = colors_for(use_color)
    out = TextOutput()

    if per_agent or history_csv:
        name = project or env_project
        if not name:
            out.err.append("ERROR: no project specified; pass --project <name> or set PGAI_PROJECT_NAME")
            return out.result(1)
        if per_agent:
            if root is None:
                return out.result(1)
            rc_dir = root / "projects" / name / "metrics" / "rc"
            latest = latest_rc_rollup(rc_dir)
            if latest is None or not latest.is_file():
                out.err.append(f"No RC metrics files found in {rc_dir}")
                return out.result(1)
            try:
                data = json.loads(latest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                out.err.append(f"ERROR: Cannot read {latest}: {exc}")
                return out.result(1)
            out.out.extend(render_per_agent(data, name, colors))
            return out.result(0)
        csv_path = pathlib.Path(history_csv)
        if not csv_path.is_file():
            out.err.append(f"No history CSV found at: {history_csv}")
            return out.result(1)
        ok = _render_history_file(csv_path, name, last, colors, out)
        return out.result(0 if ok else 1)

    if root is None:
        return out.result(1)

    names = [project] if project else registered_projects(root)
    if not names:
        out.out.extend(_no_projects(colors))
        return out.result(0)
    for name in names:
        csv_path = root / "projects" / name / "metrics" / "history.csv"
        if not csv_path.is_file():
            out.out.extend(_no_releases(name, colors))
            continue
        if not _render_history_file(csv_path, name, last, colors, out):
            return out.result(1)
    return out.result(0)


# ---------------------------------------------------------------------------
# metrics-report.sh views
# ---------------------------------------------------------------------------


def history_csv(csv_path: pathlib.Path, project_filter: str = "") -> str:
    """history.csv re-emitted as CSV, keeping only *project_filter*'s rows when given.

    With no matching row the header alone is printed, so a caller can still
    read the schema.
    """
    header, rows = read_history_csv(csv_path)
    rows = [row for row in rows if not project_filter or row.get("project", "") == project_filter]
    if not rows:
        return ",".join(header) + "\n"
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=header, lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def rc_rollups_jsonl(paths: Iterable[pathlib.Path], project_filter: str = "") -> TextResult:
    """One compact, key-sorted JSON line per roll-up in *paths*, in the order given.

    A roll-up whose ``project`` is not *project_filter* (when given) is left
    out; an unreadable one is skipped with a warning on stderr.
    """
    out = TextOutput()
    for path in paths:
        path = pathlib.Path(path)
        if not path.is_file():
            continue
        data, reason = load_json_object(path)
        if data is None:
            out.err.append(f"WARNING: skipping {path}: {reason}")
            continue
        if project_filter and data.get("project", "") != project_filter:
            continue
        out.out.append(json.dumps(data, separators=(",", ":"), sort_keys=True))
    return out.result(0)
