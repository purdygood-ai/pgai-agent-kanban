"""
projects.py — per-project reads for the pgai-agent-kanban operator API:
GET /projects/{name} (the metadata card), GET /projects/{name}/reliability
(the reliability streaks) and GET /projects/{name}/inbox (the operator
inbox); see each handler's docstring for the last two.

GET /projects/{name} returns the full metadata card for a registered project:
configuration values from projects.cfg and project.cfg, release state, and
per-agent queue counts.

Response shape (HTTP 200):
  {
    "name":           str,           # project name
    "workflow_type":  str,           # release | feature | document (from project.cfg)
    "branch_prefix":  str,           # e.g. "ai_" or "" (from project.cfg)
    "dev_tree_path":  str,           # absolute path to the git checkout (from project.cfg)
    "git_repo":       str,           # remote URL (from project.cfg; never redacted)
    "priority":       int,           # sort priority from projects.cfg
    "color":          str,           # #RRGGBB: dashboard_color, else the palette slot
    "ceilings": {
      "max_major":    int | null,    # ceiling in effect: project.cfg [versioning], or the
      "max_minor":    int | null,    #   documented default (0 / 13 / 21) when absent;
      "max_patch":    int | null,    #   null = no ceiling (written `none`) or unreadable
    },
    "last_released":  str | null,    # vX.Y.Z from release-state.md ## Last Released; null when none/absent
    "active_rc":      str | null,    # vX.Y.Z from release-state.md ## Active RC; null when none/absent
    "halt":           bool,          # the dashboard's HALT flag (project or kanban-wide HALT)
    "halt_text":      str,           # "" | "HALT" | "HALT-AFTER <event>" (project or kanban-wide)
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
    field — the same source pgai_agent_kanban.board.get_last_released_for_project reads.
    This is the Python counterpart to the bash pp_last_released_version tier-aware
    resolver (which writes to release-state.md via finalize.sh after each release).
  - active_rc is null when release-state.md has "none" or is absent — never the
    string "none".
  - halt and halt_text are every HALT the dashboard header shows for the
    project, from
    pgai_agent_kanban.dashboard.halt_state.effective_halt_flag_and_text:
    halt is true for a project HALT or, with neither project sentinel, the
    kanban-wide HALT; a project HALT-AFTER -- or, with no project sentinel
    and no kanban-wide HALT, a kanban-wide HALT-AFTER -- sets halt_text to
    "HALT-AFTER <event>" and leaves halt false (the chain keeps running
    while it drains).
  - color is pgai_agent_kanban.ops.projects.project_color, the rule
    projects_cfg_color applies and GET /board shares: the explicit
    dashboard_color, else the default-palette entry at the project's
    ordinal in priority order.
  - ceilings resolve through pgai_agent_kanban.lib.version_ceiling, the rule
    every ceiling reader shares. A malformed value is reported null and adds
    one ``warnings`` entry naming the file, key and value; discovery treats
    that project as ceiling-blocked, not unlimited.
  - queue_counts covers all five agent roles (pm, coder, writer, tester, cm),
    each pgai_agent_kanban.dashboard.task_counts.agent_queue_counts over the
    agent's backlog: a task's state comes from its status.md, else the queue
    marker; WORKING counts as working, DONE / WONT-DO as done, anything else
    as open.
"""

from __future__ import annotations

import pathlib
import re

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from pgai_agent_kanban.dashboard.halt_state import effective_halt_flag_and_text
from pgai_agent_kanban.dashboard.task_counts import agent_queue_counts
from pgai_agent_kanban.lib import ini
from pgai_agent_kanban.lib.branch_prefix import InvalidBranchPrefixError, ini_branch_prefix_from_cfg
from pgai_agent_kanban.lib.version_ceiling import (
    CEILING_KEYS,
    CeilingValueError,
    project_cfg_file,
    read_ceiling,
)
from pgai_agent_kanban.board import (
    get_active_rc_for_project,
    get_last_released_for_project,
)
from pgai_agent_kanban.metrics.reliability import reliability_path_for_project
from pgai_agent_kanban.metrics.reliability_view import PLACEHOLDER_MISSING, load_reliability
from ..adapter import http_status_for
from pgai_agent_kanban.lib.error_contract import dual_channel_from_text
from pgai_agent_kanban.ops import resolve as ops_resolve
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.errors import OpsError, cli_exit_code
from pgai_agent_kanban.ops.projects import project_color
from pgai_agent_kanban.overwatch import inbox as overwatch_inbox
from ..component_refusal import _component_refusal
from ..dependencies import warn_unknown_query_params

__all__ = ["router"]

router = APIRouter(tags=["projects"])

# ---------------------------------------------------------------------------
# Agent roles (queue columns)
# ---------------------------------------------------------------------------

_AGENT_ROLES = ["pm", "coder", "writer", "tester", "cm"]

# ---------------------------------------------------------------------------
# INI helpers
# ---------------------------------------------------------------------------


def _read_project_cfg(project_dir: pathlib.Path) -> ini.IniParser | None:
    """Return the parsed project.cfg in *project_dir*.

    Returns None when project.cfg is absent or unreadable.
    Accepts both project.cfg (INI-format) and PROJECT.cfg (legacy fallback).
    """
    for cfg_name in ("project.cfg", "PROJECT.cfg"):
        cfg_path = project_dir / cfg_name
        if cfg_path.is_file():
            try:
                return ini.load(cfg_path)
            except ini.IniError:
                return None
    return None


def _cfg_get(
    parser: ini.IniParser | None,
    section: str,
    key: str,
    fallback: str = "",
) -> str:
    """Read a key from the parsed config, returning fallback on any failure."""
    if parser is None:
        return fallback
    try:
        return parser.get(section, key).strip()
    except (ini.NoSectionError, ini.NoOptionError):
        return fallback


def _resolve_ceilings(project_dir: pathlib.Path) -> tuple[dict[str, int | None], list[str]]:
    """Return the ceilings in effect for *project_dir* and any unreadable-value warnings.

    Each value is the configured number, the documented default for an
    absent key (or an absent project.cfg), or None for an explicit ``none``.
    A malformed value is None too, with one warning naming the file, key and
    value -- the same line the bash accessors print.
    """
    cfg_file = project_cfg_file(project_dir)
    ceilings: dict[str, int | None] = {}
    problems: list[str] = []
    for key in CEILING_KEYS:
        try:
            ceilings[key] = read_ceiling(cfg_file, key).limit
        except CeilingValueError as exc:
            ceilings[key] = None
            problems.append(str(exc))
    return ceilings, problems


# ---------------------------------------------------------------------------
# projects.cfg helpers
# ---------------------------------------------------------------------------


def _read_projects_registry(
    kanban_root: pathlib.Path,
) -> dict[str, dict]:
    """Return a name-keyed dict of project registry entries from projects.cfg.

    Each entry contains:
      - priority: int
    """
    return {entry.name: {"priority": entry.priority} for entry in ini.projects_registry(kanban_root)}


# ---------------------------------------------------------------------------
# Release-state helpers
# ---------------------------------------------------------------------------


def _normalize_active_rc(raw: str) -> str | None:
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
    - ``color``         — hex color string (#RRGGBB): ``dashboard_color`` from
                          projects.cfg, else the default-palette entry at the
                          project's ordinal in priority order (the colour
                          GET /board and the dashboard show).
    - ``ceilings``      — version ceiling object with max_major, max_minor,
                          max_patch: each the int ceiling in effect (an absent
                          key reads as its documented default, 0 / 13 / 21),
                          or null when the value is ``none`` (unlimited) or
                          malformed; a malformed value also adds a
                          ``warnings`` entry naming the file, key and value.
    - ``last_released`` — vX.Y.Z string from release-state.md ## Last Released;
                          null when absent or sentinel "none"/"v0.0.0".
    - ``active_rc``     — vX.Y.Z string from release-state.md ## Active RC;
                          null when absent or sentinel "none".
    - ``halt``          — the dashboard's HALT flag: true for a project HALT,
                          or for the kanban-wide HALT when the project has no
                          HALT / HALT-AFTER of its own.
    - ``halt_text``     — the dashboard's HALT_TEXT: ``""``, ``"HALT"`` or
                          ``"HALT-AFTER <event>"`` (a draining project, halt
                          false).
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
    # The same file _read_project_cfg parsed (project.cfg, else PROJECT.cfg),
    # read as pp_branch_prefix reads it; a value it refuses reports as "",
    # as an unreadable project.cfg does.
    try:
        branch_prefix = ini_branch_prefix_from_cfg(project_cfg_file(project_dir))
    except InvalidBranchPrefixError:
        branch_prefix = ""
    dev_tree_path = _cfg_get(project_cfg, "project", "dev_tree_path", fallback="")
    git_repo = _cfg_get(project_cfg, "project", "git_repo_url", fallback="")

    # --- Version ceilings in effect (shared rule: lib.version_ceiling) ---
    ceilings, ceiling_warnings = _resolve_ceilings(project_dir)

    # --- Color: the dashboard's colour rule, shared with GET /board ---
    color = project_color(kanban_root, name) or ""

    # --- Release state ---
    # last_released: read via the canonical pgai_agent_kanban.board helper (same source
    # as pp_last_released_version's release-state.md fallback path).
    last_released_raw = get_last_released_for_project(project_dir)
    # get_last_released_for_project returns "" for sentinels; convert to None.
    last_released: str | None = last_released_raw if last_released_raw else None

    # active_rc: read via the canonical pgai_agent_kanban.board helper, then normalize
    # "none" and non-semver values to null (never emit the string "none").
    active_rc_raw = get_active_rc_for_project(project_dir)
    active_rc: str | None = _normalize_active_rc(active_rc_raw)

    # --- HALT: the dashboard header's flag and text (project, else kanban-wide) ---
    halt_flag, halt_text = effective_halt_flag_and_text(project_dir, kanban_root)

    # --- Per-agent queue counts ---
    tasks_dir = project_dir / "tasks"
    queue_counts = {agent.upper(): agent_queue_counts(tasks_dir, agent) for agent in _AGENT_ROLES}

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
                "max_major": ceilings["max_major"],
                "max_minor": ceilings["max_minor"],
                "max_patch": ceilings["max_patch"],
            },
            "last_released": last_released,
            "active_rc": active_rc,
            "halt": halt_flag == "yes",
            "halt_text": halt_text,
            "queue_counts": queue_counts,
            "warnings": [*warnings, *ceiling_warnings],
        },
        status_code=200,
    )


# ---------------------------------------------------------------------------
# GET /projects/{name}/reliability
# ---------------------------------------------------------------------------


def _reliability_refresh_command(kanban_root: pathlib.Path, name: str) -> str:
    """The command that (re)writes *name*'s reliability.json under *kanban_root*."""
    return f"python3 -m pgai_agent_kanban.metrics.reliability refresh --project {name}" f" --kanban-root {kanban_root}"


@router.get(
    "/projects/{name}/reliability",
    summary="Project reliability streaks",
    response_description=(
        "The project's metrics/reliability.json as stored -- the Hands-off and "
        "Clean streaks and the last block of each class -- plus ``warnings``."
    ),
)
def get_project_reliability(
    name: str,
    request: Request,
    warnings: list[str] = Depends(warn_unknown_query_params),
) -> JSONResponse:
    """Return the reliability streaks of a registered project.

    Path parameter:

    - ``name`` — the registered project name (must match an entry in projects.cfg).

    Read-only: the handler reads
    ``<kanban_root>/projects/<name>/metrics/reliability.json`` through
    ``pgai_agent_kanban.metrics.reliability_view.load_reliability`` — the
    reader ``metrics-report.sh --reliability --format json`` and the
    rc-metrics pane use — and never computes or writes it. The file is
    written by ``pgai_agent_kanban.metrics.reliability`` at every block-ledger
    write and at every RC close.

    Response (HTTP 200): the file's JSON object as stored — ``schema_version``,
    ``project``, ``computed_at``, ``first_record_at``, ``hands_off`` and
    ``clean`` (each streak ``{class, status, rcs, hours, since, last_block}``;
    the schema is in ``pgai_agent_kanban.metrics.reliability``'s module
    docstring) — with ``warnings`` added.

    HTTP status:

    - 200 when the project is registered and its reliability.json is readable.
    - 404 ``{"error": "project not found", "name", "warnings"}`` when the name
      is not registered, as GET /projects/{name} answers.
    - 404 ``{"error": "reliability not computed", "name", "message", "remedy",
      "warnings"}`` when the project is registered but has no reliability.json
      yet; ``remedy`` is the refresh command that writes it.
    - 422 ``{"error": "reliability unreadable", "name", "message", "remedy",
      "warnings"}`` when the file does not parse or lacks a streak field the
      pane needs; ``remedy`` is the same refresh command, which rewrites it.
    """
    kanban_root: pathlib.Path = request.app.state.api_cfg.kanban_root

    if name not in _read_projects_registry(kanban_root):
        return JSONResponse(
            content={"error": "project not found", "name": name, "warnings": warnings},
            status_code=404,
        )

    path = reliability_path_for_project(kanban_root, name)
    document, placeholder = load_reliability(path)
    if document is None:
        missing = placeholder == PLACEHOLDER_MISSING
        return JSONResponse(
            content={
                "error": "reliability not computed" if missing else "reliability unreadable",
                "name": name,
                "message": (
                    f"Project '{name}' has no reliability.json yet at {path}; it is written "
                    "at the project's first block-ledger write or RC close."
                    if missing
                    else f"Project '{name}' has a reliability.json at {path} that does not "
                    "parse or lacks a streak field."
                ),
                "remedy": _reliability_refresh_command(kanban_root, name),
                "warnings": warnings,
            },
            status_code=404 if missing else 422,
        )

    return JSONResponse(content={**document, "warnings": warnings}, status_code=200)


# ---------------------------------------------------------------------------
# GET /projects/{name}/inbox
# ---------------------------------------------------------------------------


def _inbox_refusal(name: str, exc: OpsError, warnings: list[str]) -> JSONResponse:
    """Return a refused inbox read as ``{project, error, message, remedy, warnings}``.

    The triple is the refusal's own house-shape block (``str(exc)``) -- the
    block ``inbox.sh list`` prints for the same call -- and the status is
    the one every action route maps that refusal to (``http_status_for``).
    """
    triple = dual_channel_from_text(
        str(exc),
        "inbox.read_refused",
        f"python3 -m pgai_agent_kanban.overwatch.inbox list --project {name} --all",
    )
    return JSONResponse(
        content={"project": name, **triple, "warnings": warnings},
        status_code=http_status_for(cli_exit_code(exc), error_key=triple["error"], error=exc),
    )


@router.get(
    "/projects/{name}/inbox",
    summary="Project operator inbox",
    response_description=(
        "The project's operator inbox: the open questions parked for a human "
        "answer and the closed ones, each entry with its fields, plus ``warnings``."
    ),
)
def get_project_inbox(
    name: str,
    request: Request,
    warnings: list[str] = Depends(warn_unknown_query_params),
) -> JSONResponse:
    """Return every entry in a project's operator inbox, open and closed.

    The REST twin of ``inbox.sh --project NAME list``: both resolve the
    project the same way and read through
    ``pgai_agent_kanban.overwatch.inbox.read_inbox``, the module that is the
    inbox file's only reader and writer. Read-only; a missing inbox file is
    an empty inbox and is not created. Answering is
    ``POST /operations/inbox-answer``.

    Path parameter:

    - ``name`` — the project name, resolved as the CLI resolves ``--project``:
      an exact registered name, else an unambiguous prefix of one.

    Response (HTTP 200)::

        {"project": str,          # the resolved project name
         "open": [entry, ...],    # ## Open, in file order
         "done": [entry, ...],    # ## Done, in the order closed
         "warnings": [str, ...]}

    Each entry: ``{entry_id, status, date, source, question, meanwhile,
    how_to_answer, answer, answered_at, closed_at, extra}`` -- ``status`` is
    ``open`` or ``done``; ``answer``, ``answered_at`` and ``closed_at`` are
    ``""`` until set; ``extra`` lists lines inside the entry the reader did
    not recognise, kept verbatim.

    A refusal answers ``{project, error, message, remedy, warnings}`` with
    the triple the CLI prints for the same call: 404
    ``project_resolve.not_found`` for a name no registered project matches,
    409 ``project_resolve.ambiguous`` for a prefix two projects share, 404
    ``inbox.unknown_project`` for a registered project with no directory,
    422 ``inbox.name_invalid`` for a name that is not one plain path
    component, 500 ``inbox.read_failed`` for a file that cannot be read.
    """
    refused = _component_refusal(request, "inbox", {"name": name}, None, warnings)
    if refused is not None:
        return refused

    ctx = OpsContext(kanban_root=request.app.state.api_cfg.kanban_root)
    try:
        project = ops_resolve.resolve_project_name(ctx, name)
        inbox = overwatch_inbox.read_inbox(ctx, project)
    except OpsError as exc:
        return _inbox_refusal(name, exc, warnings)

    return JSONResponse(
        content={"project": project, **inbox.to_dict(), "warnings": warnings},
        status_code=200,
    )
