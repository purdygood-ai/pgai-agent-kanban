"""
projects.py — GET /projects/{name} metadata card endpoint for the pgai-agent-kanban operator API.

Returns the full metadata card for a registered project: configuration values from
projects.cfg and project.cfg, release state, and per-agent queue counts.

Response shape (HTTP 200):
  {
    "name":           str,           # project name
    "workflow_type":  str,           # release | feature | document (from project.cfg)
    "branch_prefix":  str,           # e.g. "ai_" or "" (from project.cfg)
    "dev_tree_path":  str,           # absolute path to the git checkout (from project.cfg)
    "git_repo":       str,           # remote URL (from project.cfg; never redacted)
    "priority":       int,           # sort priority from projects.cfg
    "color":          str,           # #RRGGBB hex string from projects.cfg
    "ceilings": {
      "max_major":    int | null,    # from project.cfg [versioning]; null when absent
      "max_minor":    int | null,
      "max_patch":    int | null,
    },
    "last_released":  str | null,    # vX.Y.Z from release-state.md ## Last Released; null when none/absent
    "active_rc":      str | null,    # vX.Y.Z from release-state.md ## Active RC; null when none/absent
    "halt":           bool,          # true when the per-project HALT file is present
    "queue_counts": {
      "<AGENT>": {"open": int, "working": int, "done": int},
      ...
    }
  }

HTTP 404 when the project name is not registered:
  {"error": "project not found", "name": "<requested name>"}

Constraints:
  - Read-only; no mutations.
  - Loopback trust boundary: dev_tree_path and git_repo are returned verbatim,
    never redacted.
  - last_released is resolved from the canonical release-state.md ## Last Released
    field — the same source board_classifier.get_last_released_for_project reads.
    This is the Python counterpart to the bash pp_last_released_version tier-aware
    resolver (which writes to release-state.md via finalize.sh after each release).
  - active_rc is null when release-state.md has "none" or is absent — never the
    string "none".
  - halt reflects the HALT file presence verbatim.
  - queue_counts covers all five agent roles (pm, coder, writer, tester, cm).
    Counts only include tasks whose status.md was found and readable; tasks with
    no status.md fall back to the queue marker state.
"""

from __future__ import annotations

import configparser
import pathlib
import re
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from pgai_agent_kanban.lib.terminal_states import is_terminal as _is_terminal_state
from ..dependencies import warn_unknown_query_params
from ..board_classifier import (
    get_active_rc_for_project,
    get_last_released_for_project,
    marker_to_state,
    parse_queue_line,
    read_task_state_from_status_md,
)

__all__ = ["router"]

router = APIRouter(tags=["projects"])

# ---------------------------------------------------------------------------
# Agent roles (queue columns)
# ---------------------------------------------------------------------------

_AGENT_ROLES = ["pm", "coder", "writer", "tester", "cm"]

# ---------------------------------------------------------------------------
# Default palette (mirrors board.py / projects.sh)
# ---------------------------------------------------------------------------

_DEFAULT_PALETTE = [
    "#378ADD",
    "#E24B4A",
    "#639922",
    "#BA7517",
    "#7B5EA7",
    "#2BA0A0",
    "#C4762A",
    "#4A7FA5",
]


# ---------------------------------------------------------------------------
# INI helpers
# ---------------------------------------------------------------------------


def _read_project_cfg(project_dir: pathlib.Path) -> configparser.RawConfigParser | None:
    """Return a parsed RawConfigParser for project.cfg in *project_dir*.

    Returns None when project.cfg is absent or unreadable.
    Accepts both project.cfg (INI-format) and PROJECT.cfg (legacy fallback).
    """
    for cfg_name in ("project.cfg", "PROJECT.cfg"):
        cfg_path = project_dir / cfg_name
        if cfg_path.is_file():
            parser = configparser.RawConfigParser()
            try:
                parser.read(str(cfg_path), encoding="utf-8")
                return parser
            except configparser.Error:
                return None
    return None


def _cfg_get(
    parser: configparser.RawConfigParser | None,
    section: str,
    key: str,
    fallback: str = "",
) -> str:
    """Read a key from the parsed config, returning fallback on any failure."""
    if parser is None:
        return fallback
    try:
        return parser.get(section, key).strip()
    except (configparser.NoSectionError, configparser.NoOptionError):
        return fallback


def _cfg_get_int_or_none(
    parser: configparser.RawConfigParser | None,
    section: str,
    key: str,
) -> Optional[int]:
    """Read an integer key from the parsed config, returning None on absence or error."""
    if parser is None:
        return None
    try:
        return parser.getint(section, key)
    except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
        return None


# ---------------------------------------------------------------------------
# projects.cfg helpers
# ---------------------------------------------------------------------------


def _read_projects_registry(
    kanban_root: pathlib.Path,
) -> dict[str, dict]:
    """Return a name-keyed dict of project registry entries from projects.cfg.

    Each entry contains:
      - priority: int
      - color: str       (hex string or '' when absent; caller must apply palette)
      - registration_index: int  (position in projects.cfg for palette assignment)
    """
    cfg_path = kanban_root / "projects.cfg"
    if not cfg_path.is_file():
        return {}

    parser = configparser.RawConfigParser()
    parser.read(str(cfg_path), encoding="utf-8")

    entries: dict[str, dict] = {}
    for idx, section in enumerate(parser.sections()):
        if not section.startswith("project:"):
            continue
        name = section[len("project:"):]
        try:
            priority = parser.getint(section, "priority")
        except (configparser.NoOptionError, ValueError):
            priority = 999

        color = parser.get(section, "dashboard_color", fallback="").strip()
        entries[name] = {
            "priority": priority,
            "color": color,
            "registration_index": idx,
        }

    return entries


def _resolve_color(color: str, registration_index: int) -> str:
    """Return the project color, applying the palette fallback when absent.

    Mirrors the palette-fallback logic in board.py / projects.sh.
    """
    if color:
        return color
    return _DEFAULT_PALETTE[registration_index % len(_DEFAULT_PALETTE)]


# ---------------------------------------------------------------------------
# Queue count helpers
# ---------------------------------------------------------------------------


def _count_tasks_for_agent(
    project_dir: pathlib.Path,
    agent: str,
) -> dict:
    """Return {open, working, done} counts from an agent's backlog file.

    Reads ``tasks/queues/<agent>_backlog.md``.  For each task line, the state
    is read from the task's status.md (when present); falling back to the queue
    marker character when status.md is absent.

    Terminal-state detection uses ``lib.terminal_states.is_terminal`` so the
    vocabulary is defined in exactly one place.

    State → counter mapping:
      WORKING             → working
      DONE / WONT-DO      → done   (terminal states; detected via is_terminal)
      BACKLOG/WAITING     → open
      BLOCKED             → open   (blocked tasks still represent open work)
    """
    backlog_path = project_dir / "tasks" / "queues" / f"{agent.lower()}_backlog.md"
    tasks_dir = project_dir / "tasks"

    counts = {"open": 0, "working": 0, "done": 0}

    if not backlog_path.is_file():
        return counts

    content = backlog_path.read_text(encoding="utf-8", errors="replace")
    for line in content.splitlines():
        parsed = parse_queue_line(line)
        if not parsed:
            continue
        marker, task_id, _date, _seq = parsed

        # Read state from status.md; fall back to queue marker.
        state = read_task_state_from_status_md(tasks_dir, task_id)
        if not state:
            state = marker_to_state(marker)

        s = state.upper()
        if s == "WORKING":
            counts["working"] += 1
        elif _is_terminal_state(state):
            counts["done"] += 1
        else:
            # BACKLOG, WAITING, BLOCKED, unknown
            counts["open"] += 1

    return counts


# ---------------------------------------------------------------------------
# Release-state helpers
# ---------------------------------------------------------------------------


def _normalize_active_rc(raw: str) -> Optional[str]:
    """Return the active RC version string, or None when none/absent.

    Normalises the release-state.md value: returns None when the value is
    the sentinel string "none", empty, or not a valid vX.Y.Z semver string.
    Never returns the string "none" — callers receive None (JSON null).
    """
    if not raw:
        return None
    if raw.lower() == "none":
        return None
    # Accept only vX.Y.Z semver.
    if not re.match(r"^v\d+\.\d+\.\d+$", raw):
        return None
    return raw


# ---------------------------------------------------------------------------
# GET /projects/{name}
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{name}",
    summary="Project metadata card",
    response_description=(
        "JSON metadata card for the named project: configuration, release state, "
        "version ceilings, and per-agent queue counts."
    ),
)
def get_project_metadata(
    name: str,
    request: Request,
    warnings: list[str] = Depends(warn_unknown_query_params),
) -> JSONResponse:
    """Return the metadata card for a registered project.

    Path parameter:

    - ``name`` — the registered project name (must match an entry in projects.cfg).

    Response fields (HTTP 200):

    - ``name``          — project name.
    - ``workflow_type`` — release | feature | document (from project.cfg).
    - ``branch_prefix`` — git branch prefix (e.g. "ai_") or "" when empty.
    - ``dev_tree_path`` — absolute path to the git checkout (from project.cfg).
    - ``git_repo``      — remote repository URL (from project.cfg; never redacted).
    - ``priority``      — sort priority from projects.cfg (lower = higher priority).
    - ``color``         — hex color string (#RRGGBB) from projects.cfg; palette
                          fallback when absent.
    - ``ceilings``      — version ceiling object with max_major, max_minor,
                          max_patch (each int or null when unconfigured).
    - ``last_released`` — vX.Y.Z string from release-state.md ## Last Released;
                          null when absent or sentinel "none"/"v0.0.0".
    - ``active_rc``     — vX.Y.Z string from release-state.md ## Active RC;
                          null when absent or sentinel "none".
    - ``halt``          — true when the per-project HALT file is present.
    - ``queue_counts``  — object keyed by agent role (pm, coder, writer, tester,
                          cm); each value is ``{open, working, done}`` integer counts.

    HTTP status:

    - 200 when the project exists.
    - 404 when the project name is not registered, with ``name`` echoed in the body.
    """
    kanban_root: pathlib.Path = request.app.state.api_cfg.kanban_root

    # Verify the project is registered in projects.cfg.
    registry = _read_projects_registry(kanban_root)
    if name not in registry:
        return JSONResponse(
            content={"error": "project not found", "name": name},
            status_code=404,
        )

    reg_entry = registry[name]
    project_dir = kanban_root / "projects" / name

    # Read project.cfg (optional — some projects have no project.cfg).
    project_cfg = _read_project_cfg(project_dir)

    # --- Core identity fields from project.cfg ---
    workflow_type = _cfg_get(project_cfg, "project", "workflow_type", fallback="release")
    branch_prefix = _cfg_get(project_cfg, "project", "branch_prefix", fallback="")
    # Strip surrounding quotes from branch_prefix (config value may use "ai_").
    branch_prefix = branch_prefix.strip('"')
    dev_tree_path = _cfg_get(project_cfg, "project", "dev_tree_path", fallback="")
    git_repo = _cfg_get(project_cfg, "project", "git_repo_url", fallback="")

    # --- Version ceilings from project.cfg [versioning] ---
    max_major = _cfg_get_int_or_none(project_cfg, "versioning", "max_major")
    max_minor = _cfg_get_int_or_none(project_cfg, "versioning", "max_minor")
    max_patch = _cfg_get_int_or_none(project_cfg, "versioning", "max_patch")

    # --- Color (with palette fallback) ---
    color = _resolve_color(
        reg_entry["color"],
        reg_entry["registration_index"],
    )

    # --- Release state ---
    # last_released: read via the canonical board_classifier helper (same source
    # as pp_last_released_version's release-state.md fallback path).
    last_released_raw = get_last_released_for_project(project_dir)
    # get_last_released_for_project returns "" for sentinels; convert to None.
    last_released: Optional[str] = last_released_raw if last_released_raw else None

    # active_rc: read via the canonical board_classifier helper, then normalize
    # "none" and non-semver values to null (never emit the string "none").
    active_rc_raw = get_active_rc_for_project(project_dir)
    active_rc: Optional[str] = _normalize_active_rc(active_rc_raw)

    # --- Halt flag ---
    halt = (project_dir / "HALT").is_file()

    # --- Per-agent queue counts ---
    queue_counts: dict[str, dict] = {}
    for agent in _AGENT_ROLES:
        queue_counts[agent.upper()] = _count_tasks_for_agent(project_dir, agent)

    return JSONResponse(
        content={
            "name": name,
            "workflow_type": workflow_type,
            "branch_prefix": branch_prefix,
            "dev_tree_path": dev_tree_path,
            "git_repo": git_repo,
            "priority": reg_entry["priority"],
            "color": color,
            "ceilings": {
                "max_major": max_major,
                "max_minor": max_minor,
                "max_patch": max_patch,
            },
            "last_released": last_released,
            "active_rc": active_rc,
            "halt": halt,
            "queue_counts": queue_counts,
            "warnings": warnings,
        },
        status_code=200,
    )
