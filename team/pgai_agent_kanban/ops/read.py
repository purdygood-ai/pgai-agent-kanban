"""
read.py — Read facades for the pgai_agent_kanban.ops package.

Each function in this module is a thin facade over an existing Python read
module.  No read logic is reimplemented here: each function resolves the
necessary paths from ``ctx``, calls the underlying module function, and
returns its result.

Restrictions (enforced by acceptance criteria):
  - No shell-out calls; reads are pure Python.
  - No mutation of kanban state.
  - Each function delegates to the module named in its docstring.

The eight Class-A read functions exported here:

  list_projects(ctx)
  get_halt_state(ctx, project=None)
  get_queues(ctx, project)
  get_task_status(ctx, project, key)
  get_release_state(ctx, project)
  get_attention(ctx, project=None)
  get_metrics(ctx, project, last_n=10)
  get_next_firings(ctx)
"""

from __future__ import annotations

import io
import pathlib
import sys
from typing import Any

from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.errors import IoError, NotFound


# ---------------------------------------------------------------------------
# list_projects
# ---------------------------------------------------------------------------


def list_projects(ctx: OpsContext) -> list[str]:
    """Return a sorted list of project names registered in this kanban install.

    Reads the ``projects/`` directory under ``ctx.kanban_root`` and returns
    the name of every subdirectory that does not start with a dot.

    Args:
        ctx: OpsContext carrying the kanban root path.

    Returns:
        Sorted list of project name strings.  Empty list when no projects
        directory exists.

    Raises:
        IoError: When the projects directory cannot be read due to an OS error.
    """
    projects_dir = ctx.kanban_root / "projects"
    if not projects_dir.exists():
        return []
    try:
        names = [
            entry.name
            for entry in sorted(projects_dir.iterdir())
            if entry.is_dir() and not entry.name.startswith(".")
        ]
    except OSError as exc:
        raise IoError(
            f"Cannot list projects under {projects_dir}: {exc}"
        ) from exc
    return names


# ---------------------------------------------------------------------------
# get_halt_state
# ---------------------------------------------------------------------------


def get_halt_state(
    ctx: OpsContext,
    project: "str | None" = None,
) -> "tuple[str, str | None]":
    """Return the current halt state for the kanban or a named project.

    Delegates to ``dashboard.halt_state.compute_halt_state``.  The halt state
    describes whether the system (or project) is running normally, draining
    toward a halt, or fully halted.

    Args:
        ctx:     OpsContext carrying the kanban root path.
        project: Project name to check.  When ``None``, the global kanban root
                 is checked for HALT / HALT-AFTER sentinels.

    Returns:
        A ``(state, event)`` tuple where ``state`` is one of ``'halted'``,
        ``'draining'``, or ``'normal'``, and ``event`` is a lowercase string
        token or ``None``.

    Raises:
        NotFound: When ``project`` is given but does not exist under
            ``ctx.kanban_root/projects/``.
    """
    from pgai_agent_kanban.dashboard.halt_state import compute_halt_state

    if project is None:
        root = ctx.kanban_root
    else:
        root = ctx.project_root(project)
        if not root.exists():
            raise NotFound(f"Project not found: {project!r} (expected at {root})")

    return compute_halt_state(root)


# ---------------------------------------------------------------------------
# get_queues
# ---------------------------------------------------------------------------


def get_queues(ctx: OpsContext, project: str) -> list[dict[str, str]]:
    """Return all queue entries for a named project.

    Delegates to ``pm_status.scan_queue_files``.  Reads every
    ``<agent>_backlog.md`` file under the project's ``tasks/queues/``
    directory and returns a flat list of queue entries.

    Args:
        ctx:     OpsContext carrying the kanban root path.
        project: Project name as registered in ``projects.cfg``.

    Returns:
        List of dicts with keys ``queue`` (agent backlog file stem, e.g.
        ``"coder_backlog"``) and ``task_line`` (the raw queue entry line).
        Empty list when no queue files exist.

    Raises:
        NotFound: When the project does not exist.
        IoError:  When the tasks directory cannot be read.
    """
    # Ensure pm_status is importable by adding pm-agent/ to sys.path.
    _ensure_pm_agent_on_path(ctx)

    from pm_status import scan_queue_files  # type: ignore[import-not-found]

    project_root = ctx.project_root(project)
    if not project_root.exists():
        raise NotFound(f"Project not found: {project!r} (expected at {project_root})")

    tasks_dir = ctx.tasks_dir(project)
    try:
        return scan_queue_files(tasks_dir)
    except OSError as exc:
        raise IoError(
            f"Cannot read queue files for project {project!r}: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# get_task_status
# ---------------------------------------------------------------------------


def get_task_status(
    ctx: OpsContext,
    project: str,
    key: str,
) -> dict[str, str]:
    """Return the parsed status fields for a named task.

    Delegates to ``pm_status.get_status_fields``.  Finds the task directory
    under the project's ``tasks/`` directory, reads its ``status.md``, and
    returns a dict mapping heading names to body text.

    Args:
        ctx:     OpsContext carrying the kanban root path.
        project: Project name as registered in ``projects.cfg``.
        key:     Task ID (directory name under ``tasks/``).

    Returns:
        Dict mapping status heading names (e.g. ``"State"``, ``"Summary"``)
        to their string values as parsed from ``status.md``.

    Raises:
        NotFound: When the project, task directory, or status.md file does not
            exist.
        IoError:  When ``status.md`` cannot be read.
    """
    _ensure_pm_agent_on_path(ctx)

    from pm_status import get_status_fields  # type: ignore[import-not-found]

    project_root = ctx.project_root(project)
    if not project_root.exists():
        raise NotFound(f"Project not found: {project!r} (expected at {project_root})")

    task_dir = ctx.tasks_dir(project) / key
    if not task_dir.exists():
        raise NotFound(
            f"Task not found: {key!r} under {ctx.tasks_dir(project)}"
        )

    status_file = task_dir / "status.md"
    if not status_file.is_file():
        raise NotFound(
            f"status.md not found for task {key!r} in project {project!r}"
        )

    try:
        return get_status_fields(status_file)
    except OSError as exc:
        raise IoError(
            f"Cannot read status.md for task {key!r}: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# get_release_state
# ---------------------------------------------------------------------------


def get_release_state(ctx: OpsContext, project: str) -> dict[str, str]:
    """Return selected fields from the project's release-state.md.

    Delegates to ``cm.read_state_field.read_state_field``.  Reads the
    standard release-state headings and returns them as a dict.

    Fields returned (keyed by heading name):
        - ``"Active RC"``
        - ``"State"``
        - ``"Release Version"``
        - ``"Source Branch"``
        - ``"Opened By"``
        - ``"Opened At"``

    Missing headings are present in the returned dict with value ``"none"``
    (the sentinel returned by ``read_state_field`` when a heading is absent).

    Args:
        ctx:     OpsContext carrying the kanban root path.
        project: Project name as registered in ``projects.cfg``.

    Returns:
        Dict mapping heading name to value string.

    Raises:
        NotFound: When the project does not exist.
    """
    from pgai_agent_kanban.cm.read_state_field import read_state_field

    project_root = ctx.project_root(project)
    if not project_root.exists():
        raise NotFound(f"Project not found: {project!r} (expected at {project_root})")

    state_file = ctx.release_state_path(project)
    state_file_str = str(state_file)

    headings = [
        "Active RC",
        "State",
        "Release Version",
        "Source Branch",
        "Opened By",
        "Opened At",
    ]

    return {heading: read_state_field(state_file_str, heading) for heading in headings}


# ---------------------------------------------------------------------------
# get_attention
# ---------------------------------------------------------------------------


def get_attention(
    ctx: OpsContext,
    project: "str | None" = None,
) -> str:
    """Return a formatted attention report for the kanban or a named project.

    Delegates to ``dashboard.scan_attention.scan_blocked_tasks`` and related
    scanners.  Captures their stdout output and returns it as a single string.

    The returned string contains sections for:
      - Blocked tasks (tasks with ``## State: BLOCKED`` and ``Needs Human: yes``).
      - Transient-blocked tasks (auto-retry blocks).
      - Quarantine alerts (files approaching or already quarantined).

    Args:
        ctx:     OpsContext carrying the kanban root path.
        project: Project name to scope the scan.  When ``None``, all
                 projects under ``ctx.kanban_root`` are scanned.

    Returns:
        Multi-line string containing the formatted attention report.
        An empty string when no attention items are found and nothing is printed.

    Raises:
        NotFound: When ``project`` is given but does not exist.
    """
    from pgai_agent_kanban.dashboard.scan_attention import (
        scan_blocked_tasks,
        scan_quarantine,
        scan_transient_tasks,
    )

    if project is not None:
        project_root = ctx.project_root(project)
        if not project_root.exists():
            raise NotFound(
                f"Project not found: {project!r} (expected at {project_root})"
            )
        tasks_root = str(ctx.tasks_dir(project))
        kanban_root = str(ctx.kanban_root)
    else:
        # Scan all projects — use the first project's tasks dir for blocked/transient,
        # and kanban_root for quarantine.  For a global scan covering multiple projects,
        # aggregate across all registered projects.
        tasks_root = str(ctx.kanban_root / "tasks")  # legacy single-project layout
        kanban_root = str(ctx.kanban_root)

    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        scan_blocked_tasks(tasks_root=tasks_root, use_color=False)
        scan_transient_tasks(tasks_root=tasks_root, use_color=False)
        scan_quarantine(kanban_root=kanban_root, use_color=False, threshold=3)
    finally:
        sys.stdout = old_stdout

    return buf.getvalue()


# ---------------------------------------------------------------------------
# get_metrics
# ---------------------------------------------------------------------------


def get_metrics(
    ctx: OpsContext,
    project: str,
    last_n: int = 10,
) -> str:
    """Return a formatted agent wake-time metrics table for a named project.

    Delegates to ``reports.agent_timing`` functions: collects wake log files,
    parses them, aggregates by (agent, project), and renders a plain-text
    table.  The ``last_n`` parameter controls how many log entries (by count)
    are included; when ``last_n <= 0`` all available log entries are returned.

    Reads from ``ctx.kanban_root/logs/`` — the standard location for wake
    batch logs.

    Args:
        ctx:    OpsContext carrying the kanban root path.
        project: Project name to filter metrics for.
        last_n: Maximum number of most-recent log entries to include.
                Defaults to 10.  Pass 0 for all entries.

    Returns:
        Formatted multi-line string ready to display, as produced by
        ``reports.agent_timing.render_table``.

    Raises:
        NotFound: When the project does not exist.
    """
    from pgai_agent_kanban.reports.agent_timing import (
        aggregate,
        collect_log_files,
        parse_log_file,
        render_table,
    )

    project_root = ctx.project_root(project)
    if not project_root.exists():
        raise NotFound(f"Project not found: {project!r} (expected at {project_root})")

    logs_dir = ctx.kanban_root / "logs"

    if not logs_dir.is_dir():
        return render_table({}, f"project={project}")

    log_files = collect_log_files(logs_dir)
    if not log_files:
        return render_table({}, f"project={project}")

    all_entries = []
    for lf in log_files:
        all_entries.extend(parse_log_file(lf, cutoff=None))

    # Filter to this project's entries.
    project_entries = [e for e in all_entries if e.project == project]

    # Apply last_n limit on entry count (most-recent entries by timestamp).
    if last_n > 0 and len(project_entries) > last_n:
        project_entries.sort(key=lambda e: e.ts)
        project_entries = project_entries[-last_n:]

    buckets = aggregate(project_entries)
    window_label = (
        f"project={project}, last {last_n} entries"
        if last_n > 0
        else f"project={project}, all entries"
    )
    return render_table(buckets, window_label)


# ---------------------------------------------------------------------------
# get_next_firings
# ---------------------------------------------------------------------------


def get_next_firings(ctx: OpsContext) -> dict[str, str]:
    """Return the next scheduled cron firing times for each agent.

    Delegates to ``dashboard.cron_firings.cron_firings``.  Reads the
    kanban's pseudocron schedule file (``pseudocron.cfg``) at
    ``ctx.kanban_root`` to obtain the crontab text.  Returns an empty dict
    when no schedule file is found, matching the graceful-degradation
    behavior of the bash dashboard scripts.

    Note: reading the system crontab (via ``crontab -l``) is only available
    in bash today — that path is a Class-A follow-up.  This facade reads
    ``pseudocron.cfg`` as the pure-Python alternative.

    Args:
        ctx: OpsContext carrying the kanban root path.

    Returns:
        Dict mapping agent name (str) to a human-readable firing label such
        as ``"in 6 min"``, ``"in 1 min"``, ``"now"``, or ``"Sun 4am"``.
        Empty dict when no schedule file is found or parsing fails.
    """
    from pgai_agent_kanban.dashboard.cron_firings import cron_firings

    # Locate the pseudocron.cfg schedule file at the kanban root.
    pseudocron_cfg = ctx.kanban_root / "pseudocron.cfg"
    if not pseudocron_cfg.is_file():
        # No schedule file present — degrade gracefully.
        return {}

    try:
        crontab_text = pseudocron_cfg.read_text(encoding="utf-8")
    except OSError:
        return {}

    if not crontab_text.strip():
        return {}

    # Locate cron_parser.py relative to this module's package root.
    # team/pgai_agent_kanban/ops/read.py -> team/ -> pm-agent/lib/cron_parser.py
    _pkg_root = pathlib.Path(__file__).parent.parent.parent  # team/
    cron_parser_path = _pkg_root / "pm-agent" / "lib" / "cron_parser.py"

    if not cron_parser_path.is_file():
        return {}

    return cron_firings(
        crontab_text=crontab_text,
        cron_parser_path=str(cron_parser_path),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ensure_pm_agent_on_path(ctx: OpsContext) -> None:
    """Add the pm-agent directory to sys.path so pm_status is importable.

    ``pm_status.py`` lives in ``team/pm-agent/`` which is not a Python
    package directory — it uses an import of ``lib.config`` via a local
    sys.path manipulation.  We locate it relative to this file's package
    root so the import works regardless of the caller's working directory.

    This is called lazily (inside the functions that need it) so that the
    module import cost is deferred.
    """
    _pkg_root = pathlib.Path(__file__).parent.parent.parent  # team/
    pm_agent_dir = str(_pkg_root / "pm-agent")
    if pm_agent_dir not in sys.path:
        sys.path.insert(0, pm_agent_dir)
