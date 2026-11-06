"""
pgai_agent_kanban.lib.metrics.metrics_pane — the dashboard Metrics pane.

``metrics_pane_lines()`` renders the pane ``scripts/dashboard/metrics.sh``
shows, from a JSON array of project descriptors the script builds:

    [ { "name": "...", "day_file": "...", "rc": "...",
        "rc_file": "...", "opened_at": "...",
        "reliability_file": "...",
        "bugs_dir": "...", "kanban_root": "..." }, ... ]

Four sections follow, then a footer:

  * **Today (<date>)** — one block per project from its day roll-up
    (``metrics/day/<date>.json``): RCs shipped (the length of
    ``rcs_included``), wall time, total tokens with the cache-hit
    percentage, and the task total. A project with no readable roll-up
    shows "(no activity yet today)".
  * **Current RC** — one block per project with an active RC, from its RC
    roll-up (``metrics/rc/<version>.json``): tasks done, elapsed time (the
    roll-up's wall time, else the time since ``opened_at``), blocked
    minutes, and tokens so far. An RC with no readable roll-up shows "(no
    data yet)" with the elapsed time since ``opened_at`` and a blocked
    figure of 0. With no RC active anywhere the section shows "(no RC
    active in any project)".
  * **Reliability** — one entry per project from its
    ``metrics/reliability.json`` (``reliability_file``): the Hands-off and
    Clean streak lines, rendered by
    ``pgai_agent_kanban.metrics.reliability_view`` -- the renderer
    ``metrics-report.sh --reliability`` prints through too. A missing or
    unreadable file shows a placeholder line under the project name.
  * **Bugs** -- one line per project with a bug to count, from its
    ``bugs_dir``, through
    ``pgai_agent_kanban.dashboard.status_line.bug_status_split``:
    ``<N> open / <N> deferred / <N> needs-operator``. Deferred bugs are the
    ``open`` bugs the project's resolved deferral posture
    (``pgai_agent_kanban.lib.defer_posture.resolve_defer_posture`` over
    ``kanban_root``) holds back -- the severity decision the dashboard's
    status line counts with; with deferral off, or no ``kanban_root``, the
    deferred count is 0. A posture that refuses to resolve counts nothing
    as deferred and says so on the line. A project with no ``bugs_dir``, or
    none of the three kinds of bug, has no line; with no line anywhere the
    section is omitted.
  * the local "Updated:" time and the history CSV path hint.

An empty or unparseable descriptor list shows "(no projects registered)"
in every section. The bash pane passes the ANSI escapes it computed with
``ansi_code()`` (empty strings when colour is off), so the escape table is
never copied here.

The script reaches this through
``pp_run_ops pgai_agent_kanban.lib.metrics metrics_pane ...``; the output
is a contract, printed line for line.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, UTC
from typing import Any
from collections.abc import Iterator

from ...dashboard.status_line import BugStatusSplit, bug_status_split
from ...metrics.reliability_view import reliability_section_lines
from ..defer_posture import DeferPostureError, resolve_defer_posture
from ..number_format import format_int

__all__ = [
    "POSTURE_UNREADABLE_NOTE",
    "bug_line",
    "cache_hit_pct",
    "fmt_blocked",
    "fmt_elapsed_since",
    "fmt_tokens",
    "fmt_wall_time",
    "metrics_pane_lines",
    "project_bug_split",
    "total_tokens",
]

#: The note a bug line carries when the project's deferral posture refuses
#: to resolve (a malformed campaign file or ``[release]`` value).
POSTURE_UNREADABLE_NOTE = "(deferral posture unreadable)"


def fmt_tokens(n: int) -> str:
    """Format a token count compactly: ``47.2M``, ``1.2M``, ``834K``, ``512``."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def cache_hit_pct(tok: dict[str, Any]) -> float | None:
    """Return cache_read / (cache_read + cache_write + input + output) as a
    percentage rounded to one decimal, or None when every count is 0."""
    cr = int(tok.get("cache_read", 0) or 0)
    cw = int(tok.get("cache_write", 0) or 0)
    inp = int(tok.get("input", 0) or 0)
    out = int(tok.get("output", 0) or 0)
    total = cr + cw + inp + out
    if total == 0:
        return None
    return round(cr / total * 100, 1)


def total_tokens(tok: dict[str, Any]) -> int:
    """Sum the input, output, cache_read and cache_write counts (missing or
    null counts are 0)."""
    return (
        int(tok.get("input", 0) or 0)
        + int(tok.get("output", 0) or 0)
        + int(tok.get("cache_read", 0) or 0)
        + int(tok.get("cache_write", 0) or 0)
    )


def fmt_wall_time(minutes: int | float | None) -> str:
    """Format minutes (rounded up) as ``Xh YYm`` from an hour up, else
    ``Ym``; None is ``(unknown)``."""
    if minutes is None:
        return "(unknown)"
    minutes = int(math.ceil(float(minutes)))
    if minutes >= 60:
        h, m = divmod(minutes, 60)
        return f"{h}h {m:02d}m"
    return f"{minutes}m"


def fmt_elapsed_since(opened_at: str | None, now: datetime | None = None) -> str:
    """Format the whole minutes from *opened_at* to *now* as ``fmt_wall_time``
    does.

    *opened_at* is ISO 8601 (a ``Z`` suffix or an offset; a value with
    neither is read as UTC). *now* is a timezone-aware time, the current
    UTC time when None. An empty or unparseable *opened_at* is
    ``(unknown)``; a time in the future is ``0m``.
    """
    if not opened_at:
        return "(unknown)"
    try:
        dt_str = opened_at.replace("Z", "+00:00")
        opened = datetime.fromisoformat(dt_str)
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=UTC)
        current = now if now is not None else datetime.now(tz=UTC)
        delta_sec = (current - opened).total_seconds()
        if delta_sec < 0:
            return "0m"
        minutes = int(delta_sec // 60)
        return fmt_wall_time(minutes)
    except (ValueError, OverflowError):
        return "(unknown)"


def fmt_blocked(minutes: int | float | None) -> str:
    """Format the current RC's blocked minutes with thousands separators.

    None -- an RC with no roll-up yet, or a roll-up without the field --
    is a real ``0``, never blank: no blocking observed is itself a measured
    figure.
    """
    if minutes is None:
        return "0"
    return format_int(minutes)


def project_bug_split(bugs_dir: str | None, kanban_root: str | None, project: str) -> tuple[BugStatusSplit, bool]:
    """Return *project*'s open / deferred / needs-operator split and whether
    its deferral posture resolved.

    The level is the one ``resolve_defer_posture(kanban_root, project)``
    puts in force while it is deferring, else none (nothing deferred). An
    empty *kanban_root* reads as deferral off. A posture that refuses
    (``DeferPostureError``) returns ``False`` with nothing deferred. An
    empty *bugs_dir* gives all zeros.
    """
    if not bugs_dir:
        return BugStatusSplit(), True
    level = ""
    resolved = True
    if kanban_root:
        try:
            posture = resolve_defer_posture(kanban_root, project)
        except DeferPostureError:
            resolved = False
        else:
            if posture.deferring:
                level = posture.deferred_bug_level.value
    return bug_status_split(bugs_dir, level), resolved


def bug_line(split: BugStatusSplit, posture_resolved: bool = True) -> str:
    """Return the pane's figure line for *split*, without indent or colour.

    ``2 open / 3 deferred / 1 needs-operator``, with
    :data:`POSTURE_UNREADABLE_NOTE` appended when the posture did not
    resolve.
    """
    line = (
        f"{format_int(split.open)} open / {format_int(split.deferred)} deferred"
        f" / {format_int(split.needs_operator)} needs-operator"
    )
    if not posture_resolved:
        line += f"  {POSTURE_UNREADABLE_NOTE}"
    return line


def _load_json(path: str | None) -> dict[str, Any] | None:
    """Return the JSON object in *path*, or None when the path is empty,
    unreadable, not JSON, or holds something other than an object."""
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return None
        return data
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def metrics_pane_lines(
    use_color: bool,
    today_date: str,
    projects_json: str,
    reset: str = "",
    bold: str = "",
    dim: str = "",
    cyan: str = "",
    green: str = "",
    yellow: str = "",
    now: datetime | None = None,
) -> Iterator[str]:
    """Yield the Metrics pane's lines, without trailing newlines.

    Args:
        use_color: Whether the pane is in colour. The output depends on
            the escapes alone -- they are empty strings when colour is off
            -- so this flag is accepted for the pane's calling convention
            and changes nothing by itself.
        today_date: The ``YYYY-MM-DD`` date shown in the Today header.
        projects_json: The JSON array of project descriptors described in
            the module docstring. Text that is not JSON reads as no
            projects.
        reset, bold, dim, cyan: The escapes the pane draws with.
        green, yellow: Accepted with the pane's other escapes; no line
            uses them.
        now: A timezone-aware time standing for the current moment, used
            for the elapsed time since ``opened_at``, the Reliability
            streaks' hours and, in local time, for the "Updated:" footer.
            Defaults to the current time.

    Lines are yielded as they are rendered, so a caller printing them as
    they come shows every line before a malformed roll-up value (one
    ``int()`` cannot read) raises.
    """
    del use_color, green, yellow  # see Args: the escapes carry the colour
    current = now if now is not None else datetime.now(tz=UTC)

    def section_header(title: str) -> str:
        return f"{cyan}{bold}=== {title} ==={reset}"

    try:
        projects: list[dict[str, Any]] = json.loads(projects_json)
    except (json.JSONDecodeError, ValueError):
        projects = []

    # Section 1: Today
    yield section_header(f"Today ({today_date})")
    yield ""

    if not projects:
        yield f"  {dim}(no projects registered){reset}"
    else:
        for proj in projects:
            name = proj.get("name", "unknown")
            yield f"  {bold}{name}{reset}"

            day_data = _load_json(proj.get("day_file"))
            if day_data is None:
                yield f"    {dim}(no activity yet today){reset}"
            else:
                tok = day_data.get("tokens", {}).get("total", {})
                tot_tok = total_tokens(tok)
                hit_pct = cache_hit_pct(tok)
                rc_count = len(day_data.get("rcs_included", []))
                wall_min = day_data.get("wall_time_minutes")
                tasks_total = day_data.get("tasks", {})
                if isinstance(tasks_total, dict):
                    tasks_total = tasks_total.get("total", 0)
                else:
                    tasks_total = 0

                tok_str = fmt_tokens(tot_tok)
                if hit_pct is not None:
                    tok_str += f"  (cache hit: {hit_pct:.0f}%)"

                yield f"    RCs shipped:  {rc_count}"
                yield f"    Wall time:    {fmt_wall_time(wall_min)}"
                yield f"    Tokens:       {tok_str}"
                yield f"    Tasks:        {format_int(tasks_total)}"

            yield ""

    # Section 2: Current RC
    yield section_header("Current RC")
    yield ""

    if not projects:
        yield f"  {dim}(no projects registered){reset}"
    elif not any(proj.get("rc") for proj in projects):
        yield f"  {dim}(no RC active in any project){reset}"
    else:
        for proj in projects:
            name = proj.get("name", "unknown")
            rc = proj.get("rc")
            opened_at = proj.get("opened_at")
            if not rc:
                continue

            yield f"  {bold}{name} {rc}{reset}"

            rc_data = _load_json(proj.get("rc_file"))
            if rc_data is None:
                # No roll-up yet: elapsed from opened_at only, and no ledger
                # reading either -- a real 0, not a blank.
                yield "    Tasks:    (no data yet)"
                yield f"    Elapsed:  {fmt_elapsed_since(opened_at, current)}"
                yield f"    Blocked:  {fmt_blocked(None)}"
                yield "    Tokens:   (no data yet)"
            else:
                tok = rc_data.get("tokens", {}).get("total", {})
                tot_tok = total_tokens(tok)
                tasks = rc_data.get("tasks", {})
                # Every task in the roll-up ran, so its count reads as "done".
                t_total = int(tasks.get("total", 0) if isinstance(tasks, dict) else 0)
                wall_min = rc_data.get("wall_time_minutes")
                if wall_min is not None:
                    elapsed = fmt_wall_time(wall_min)
                else:
                    elapsed = fmt_elapsed_since(opened_at, current)
                blocked = fmt_blocked(rc_data.get("blocked_minutes"))

                yield f"    Tasks:    {format_int(t_total)} done"
                yield f"    Elapsed:  {elapsed}"
                yield f"    Blocked:  {blocked}"
                yield f"    Tokens:   {fmt_tokens(tot_tok)} (so far)"

            yield ""

    # Section 3: Reliability (streak hours counted to the render time)
    yield from reliability_section_lines(projects, reset=reset, bold=bold, dim=dim, cyan=cyan, now=current)

    # Section 4: Bugs -- only projects with a bug to count, and only when
    # there is at least one such project.
    bug_blocks: list[tuple[str, str]] = []
    for proj in projects:
        name = proj.get("name", "unknown")
        split, resolved = project_bug_split(proj.get("bugs_dir"), proj.get("kanban_root"), name)
        if split.total:
            bug_blocks.append((name, bug_line(split, resolved)))
    if bug_blocks:
        yield section_header("Bugs")
        yield ""
        for name, line in bug_blocks:
            yield f"  {bold}{name}{reset}"
            yield f"    {line}"
            yield ""

    # Footer
    now_str = current.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    yield f"{dim}Updated: {now_str}{reset}"
    yield f"{dim}History CSV: projects/*/metrics/history.csv{reset}"
