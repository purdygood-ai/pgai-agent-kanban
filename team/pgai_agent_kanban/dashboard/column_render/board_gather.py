"""
pgai_agent_kanban.dashboard.column_render.board_gather — every board
column's per-project sources, gathered in one Python process.

The bash gather stage (``column_render_gather_board_multi`` in
``team/scripts/lib/dashboard/column_render_data_multi.sh``) resolves, for
every registered project and every board column, the project's colour and
row cap, its intake directory or queue file, its active RC and last release
(requirements column) and its deferral posture (bugs column).  Each answer
there costs one or more child processes per project, which made the
snapshot build the most expensive step of a board tick.  This module gives
the same answers through the Python twins of those bash helpers, in
process:

    bash helper                        Python twin used here
    ---------------------------------  ------------------------------------------
    projects_cfg_list / _color /       ops.projects.dashboard_registry_rows
      _max_rows
    pp_tasks_dir, pp_bugs_dir, ...,    <kanban_root>/projects/<name>/...
      pp_queue_path, pp_release_state  (string concatenation, as the bash does)
    release_state_active_rc            cm.read_state_field.read_state_section
    pp_last_released_version           lib.released_version.last_released_version
    column_render_defer_multi_line     lib.defer_posture.resolve_defer_posture and
                                       dashboard.status_line.deferred_bug_ids

The result is the environment mapping the bash stage exports (the
``_PROJ_*_STR`` and ``_ALL_*`` lists, one line per project, with the
``__<column>`` suffix for column-specific lists), read back through
:func:`~pgai_agent_kanban.dashboard.column_render.multi.sources_from_env`,
so a source built here is exactly the source the bash stage's exports
produce for the same tree.

What it does not cover
----------------------
:class:`BoardGatherUnavailable` is raised -- and the caller gathers through
the bash stage instead -- when projects.cfg is absent, is in the
colon-legacy format, or registers no project.  An unexpected error while
reading a project is not swallowed either: it propagates, and the caller
falls back the same way.  A single field the bash helpers would have read
as missing (an unreadable release-state.md, a failed tag scan) reads as
missing here too.
"""

from __future__ import annotations

import os
import pathlib
import re
from typing import Mapping, Optional

from pgai_agent_kanban.dashboard.column_render.board_snapshot import BOARD_COLUMNS, column_env_suffix
from pgai_agent_kanban.dashboard.column_render.multi import sources_from_env

__all__ = ["BoardGatherUnavailable", "gather_board_env", "gather_board_sources"]

#: The field separator of a deferral-info line (``DFS_SEP`` in defer_scope.sh).
_DFS_SEP = "\x1f"

#: The queue column types, each read from ``tasks/queues/<column>_backlog.md``.
_QUEUE_COLUMNS = ("pm", "coder", "writer", "tester", "cm")

#: The version shape release_state_active_rc accepts (its bash regex).
_VERSION_RE = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")

#: Intake column type -> the project subdirectory it lists.
_INTAKE_DIRS = {"bugs": "bugs", "priorities": "priority", "requirements": "requirements"}


class BoardGatherUnavailable(Exception):
    """The registry is one this gather does not read; gather through bash instead."""


def _gray_fallback() -> str:
    from pgai_agent_kanban.lib.palette import load_default_palette

    return load_default_palette()[-1]


def _active_rc(release_state: pathlib.Path) -> str:
    """``release_state_active_rc <file> none``: a vX.Y.Z version, else ``none``."""
    from pgai_agent_kanban.cm.read_state_field import read_state_section

    try:
        value = read_state_section(str(release_state), "Active RC") or ""
    except (OSError, UnicodeDecodeError, ValueError):
        value = ""
    value = "".join(value.split())
    return value if _VERSION_RE.fullmatch(value) else "none"


def _last_released(kanban_root: pathlib.Path, project: str) -> str:
    """``pp_last_released_version`` with ``v0.0.0`` and failures read as ``none``."""
    from pgai_agent_kanban.lib.released_version import last_released_version

    try:
        value = last_released_version(kanban_root, project)
    except (OSError, UnicodeDecodeError, ValueError):
        return "none"
    return "none" if not value or value == "v0.0.0" else value


def _defer_line(kanban_root: pathlib.Path, project: str, bugs_dir: str) -> str:
    """``column_render_defer_multi_line``: ``mode<SEP>errfile<SEP>id|id|...|``.

    A refusal that names no file falls back to the kanban root's campaign
    file, the path the bash resolver (``_defer_scope_resolve`` in
    defer_scope.sh) names for the same refusal.
    """
    from pgai_agent_kanban.dashboard.status_line import deferred_bug_ids
    from pgai_agent_kanban.lib.defer_posture import (
        DeferPostureError,
        campaign_file_path,
        resolve_defer_posture,
    )

    try:
        posture = resolve_defer_posture(kanban_root, project)
    except DeferPostureError as exc:
        return f"malformed{_DFS_SEP}{exc.file or campaign_file_path(kanban_root)}{_DFS_SEP}"
    if not posture.deferring:
        return f"off{_DFS_SEP}{_DFS_SEP}"
    ids: list = []
    if bugs_dir and os.path.isdir(bugs_dir):
        try:
            ids = deferred_bug_ids(bugs_dir, posture.deferred_bug_level.value)
        except (OSError, UnicodeDecodeError, ValueError):
            ids = []
    joined = "".join(f"{bug_id}|" for bug_id in ids)
    return f"on{_DFS_SEP}{_DFS_SEP}{joined}"


def gather_board_env(
    kanban_root: "pathlib.Path | str",
    *,
    dashboard_max_rows: str = "",
    environ: Optional[Mapping[str, str]] = None,
) -> dict:
    """Return the environment mapping ``column_render_gather_board_multi`` exports.

    *dashboard_max_rows* is the caller's ``DASHBOARD_MAX_ROWS`` (empty when
    unset; ``20`` then applies); the deferral glyphs are taken from
    *environ* (``GLYPH_DEFERRED_BUG`` / ``GLYPH_DEFER_WARNING``, which the
    bash caller exports from status_glyphs.sh).  Raises
    :class:`BoardGatherUnavailable` as the module docstring describes.
    """
    from pgai_agent_kanban.ops.projects import dashboard_registry_rows

    root = pathlib.Path(kanban_root)
    env_in = os.environ if environ is None else environ
    rows = dashboard_registry_rows(root, dashboard_max_rows or "20")
    if not rows:
        raise BoardGatherUnavailable(f"no INI project registry with a registered project under {root}")

    names = [name for name, _color, _max_rows in rows]
    colors = [color or _gray_fallback() for _name, color, _max_rows in rows]
    project_roots = {name: f"{root}/projects/{name}" for name in names}

    env = {
        "_PROJ_NAMES_STR": "\n".join(names),
        "_PROJ_COLORS_STR": "\n".join(colors),
        "_PROJ_MAX_ROWS_STR": "\n".join(max_rows for _name, _color, max_rows in rows),
        "_PROJ_TASKS_DIRS_STR": "\n".join(f"{project_roots[name]}/tasks" for name in names),
        "DFS_SEP": _DFS_SEP,
        "GLYPH_DEFERRED_BUG": env_in.get("GLYPH_DEFERRED_BUG", ""),
        "GLYPH_DEFER_WARNING": env_in.get("GLYPH_DEFER_WARNING", ""),
    }
    for _subcommand, column_type in BOARD_COLUMNS:
        sfx = column_env_suffix(column_type)
        input_dirs, backlog_files, defer_info = [], [], []
        active_rcs, last_released = [], []
        for name in names:
            project_root = project_roots[name]
            if column_type in _INTAKE_DIRS:
                input_dir = f"{project_root}/{_INTAKE_DIRS[column_type]}"
                input_dirs.append(input_dir)
                backlog_files.append("none")
                if column_type == "bugs":
                    defer_info.append(_defer_line(root, name, input_dir))
            elif column_type in _QUEUE_COLUMNS:
                input_dirs.append("none")
                backlog_files.append(f"{project_root}/tasks/queues/{column_type}_backlog.md")
            if column_type == "requirements":
                active_rcs.append(_active_rc(pathlib.Path(f"{project_root}/release-state.md")))
                last_released.append(_last_released(root, name))
            else:
                active_rcs.append("none")
                last_released.append("none")
        env[f"_PROJ_INPUT_DIRS_STR{sfx}"] = "\n".join(input_dirs)
        env[f"_PROJ_BACKLOG_FILES_STR{sfx}"] = "\n".join(backlog_files)
        env[f"_PROJ_DEFER_INFO_STR{sfx}"] = "\n".join(defer_info)
        env[f"_ALL_ACTIVE_RCS{sfx}"] = "".join(f"{rc}\n" for rc in active_rcs)
        env[f"_ALL_LAST_RELEASED{sfx}"] = "".join(f"{lr}\n" for lr in last_released)
    return env


def gather_board_sources(
    kanban_root: "pathlib.Path | str",
    *,
    dashboard_max_rows: str = "",
    environ: Optional[Mapping[str, str]] = None,
) -> dict:
    """Map every board column type to its per-project ``ProjectSource`` list (registry order)."""
    env = gather_board_env(kanban_root, dashboard_max_rows=dashboard_max_rows, environ=environ)
    return {
        column_type: sources_from_env(env, column_env_suffix(column_type))
        for _subcommand, column_type in BOARD_COLUMNS
    }
