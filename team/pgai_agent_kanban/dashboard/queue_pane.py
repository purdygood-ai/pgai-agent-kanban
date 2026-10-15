"""
queue_pane.py — the text renderer of the queue-state pane.

The one renderer behind ``team/scripts/dashboard/show-queues.sh`` (through
``python3 -m pgai_agent_kanban.ops get_queues``) and the text body of
``GET /show-queues`` (team/pgai_agent_kanban/api/reads.py). Both surfaces
read their data through ``pgai_agent_kanban.ops.read.get_queues`` and hand
the results to this module, so the CLI pane and the REST envelope's
``stdout`` are the same text.

Every caller follows the same three steps::

    plan = plan_queue_pane(ctx, project, data_project, details)
    queues = {name: get_queues(ctx, name) for name in plan.fetch}
    pane = render_queue_pane(ctx, plan, queues, environ=os.environ, now=time.time())
    # pane.stdout is the pane; pane.stderr carries any warning line

TWO MODES
---------
Compact (``details`` false)::

    Active provider: claude
    pm:     1/2                      50%
    coder:  4/7  1W 1B               57%
    writer: 0/0
    ...

    — currently working —
    <task slug>
      started: HH:MM:SS
      elapsed: 7m 17s

  One row per role (pm, coder, writer, tester, cm): done/total, the
  WORKING (``W``), BLOCKED (``B``) and WAITING (``Wa``) counts when
  non-zero, and the done percentage right-aligned to the pane width
  (``PGAI_AGENT_PANE_WIDTH``, default 36). The counters and the working
  task come from the *data project*, which is always read: with no data
  project, or one with no directory, ``get_queues`` refuses and no pane is
  rendered.

Details (``details`` true): for each rendered project, every agent with an
active task gets a ``=== <agent> ===`` header and one ``[STATE]`` block per
state, each task on its own line with any queue-reconciliation warning
(see ``queue_details.resolve_queue_details``). Agent headers are coloured
by the data project's halt state: the halt colour when halted, yellow when
draining, the label colour otherwise.

Both modes start with an ``Active provider:`` line
(``lib.active_provider``).

PROJECTS
--------
``project`` is the project the caller named (``--project`` / ``?project=``),
already resolved against projects.cfg, or ``""``. ``data_project`` is where
the compact counters and the agent-header halt colour come from: the named
project, or -- when none was named -- the ``PGAI_PROJECT_NAME`` the caller
runs under, or ``""``. Details mode with no named project lists every
registered project, in projects.cfg priority order. A named project, and
in compact mode the data project, is always fetched, so ``get_queues``
refuses an empty name (``show_queues.no_project``) or one whose directory
is missing (``show_queues.project_directory_missing``). In details mode a
fallback or registry project whose directory is missing contributes
nothing (an empty listing; the header takes the kanban-wide halt colour).

COLOURS
-------
Colour is on unless ``TERM=dumb`` or ``NO_COLOR=1``. The done, working,
blocked, waiting and label colours come from ``PGAI_DASHBOARD_COLOR_<NAME>``
when set, else ``kanban.cfg [dashboard] color_<name>``, else green, yellow,
red, yellow and cyan; the halt colour from ``PGAI_DASHBOARD_COLOR_HALT``,
else red. Escape codes are ``lib.palette.ansi_code``'s.
"""

from __future__ import annotations

import dataclasses
import pathlib
import re
import time
from typing import Mapping, Optional

from pgai_agent_kanban.dashboard.halt_state import halt_flag_and_text
from pgai_agent_kanban.dashboard.queue_details import AGENT_ORDER, STATE_ORDER
from pgai_agent_kanban.dashboard.task_counts import QUEUE_ROLES, QUEUE_STATS
from pgai_agent_kanban.lib import ini
from pgai_agent_kanban.lib.active_provider import read_active_provider
from pgai_agent_kanban.lib.palette import ansi_code
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.errors import OpsError

__all__ = [
    "DEFAULT_PANE_WIDTH",
    "PaneOutput",
    "PanePalette",
    "PanePlan",
    "pane_width",
    "plan_queue_pane",
    "render_compact_rows",
    "render_details_listing",
    "render_queue_pane",
    "resolve_pane_palette",
]

#: Width, in characters, the compact rows' percentage is right-aligned to.
DEFAULT_PANE_WIDTH = 36

# Colour roles read from the environment, then kanban.cfg [dashboard], then
# the default: (environment variable, kanban.cfg key or None, default name).
_COLOR_SETTINGS: dict[str, tuple[str, Optional[str], str]] = {
    "done": ("PGAI_DASHBOARD_COLOR_DONE", "color_done", "green"),
    "working": ("PGAI_DASHBOARD_COLOR_WORKING", "color_working", "yellow"),
    "blocked": ("PGAI_DASHBOARD_COLOR_BLOCKED", "color_blocked", "red"),
    "waiting": ("PGAI_DASHBOARD_COLOR_WAITING", "color_waiting", "yellow"),
    "label": ("PGAI_DASHBOARD_COLOR_LABEL", "color_label", "cyan"),
    "halt": ("PGAI_DASHBOARD_COLOR_HALT", None, "red"),
}

# Task IDs of the form AGENT-YYYYMMDD-NNN-slug; the compact pane shows the slug.
_TASK_ID_RE = re.compile(r"([A-Z]+)-([0-9]{8})-([0-9]+)-(.+)")

_WARNING_TEXT = {
    "folder_missing": "⚠ task folder missing",
    "no_queue_entry": "⚠ no queue entry",
    "marker_mismatch": "⚠ marker/status mismatch",
}
_UNKNOWN_STATE_PREFIX = "unknown_state:"


# ---------------------------------------------------------------------------
# Palette and width
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class PanePalette:
    """The escape codes one pane render uses ("" for each when colour is off)."""

    use_color: bool
    reset: str
    label: str
    done: str
    working: str
    blocked: str
    waiting: str
    halt: str
    dim: str
    yellow: str
    bold: str


def resolve_pane_palette(kanban_root, environ: Mapping[str, str]) -> PanePalette:
    """Resolve the pane's colours from *environ* and ``<kanban_root>/kanban.cfg``.

    Args:
        kanban_root: The kanban root whose kanban.cfg ``[dashboard]`` section
                     supplies colour names not set in the environment.
        environ:     The environment to read ``TERM``, ``NO_COLOR`` and the
                     ``PGAI_DASHBOARD_COLOR_*`` overrides from.

    Returns:
        The palette; see the module docstring for the precedence.
    """
    use_color = not (environ.get("TERM", "") == "dumb" or environ.get("NO_COLOR", "") == "1")
    cfg = pathlib.Path(kanban_root) / "kanban.cfg"
    names: dict[str, str] = {}
    for role, (env_var, cfg_key, default) in _COLOR_SETTINGS.items():
        name = environ.get(env_var, "")
        if not name and cfg_key is not None:
            name = ini.get_value(cfg, "dashboard", cfg_key)
        names[role] = name or default

    def code(name: str) -> str:
        return ansi_code(name, use_color)

    return PanePalette(
        use_color=use_color,
        reset=code("reset"),
        label=code(names["label"]),
        done=code(names["done"]),
        working=code(names["working"]),
        blocked=code(names["blocked"]),
        waiting=code(names["waiting"]),
        halt=code(names["halt"]),
        dim=code("dim"),
        yellow=code("yellow"),
        bold=code("bold"),
    )


def pane_width(environ: Mapping[str, str]) -> int:
    """``PGAI_AGENT_PANE_WIDTH`` as an integer, or ``DEFAULT_PANE_WIDTH``."""
    try:
        return int(environ.get("PGAI_AGENT_PANE_WIDTH", "") or DEFAULT_PANE_WIDTH)
    except ValueError:
        return DEFAULT_PANE_WIDTH


# ---------------------------------------------------------------------------
# Compact rows
# ---------------------------------------------------------------------------


def _compact_row(label: str, stats: Mapping[str, int], palette: PanePalette, width: int) -> str:
    total, done = stats["TOTAL"], stats["DONE"]
    working, blocked, waiting = stats["WORKING"], stats["BLOCKED"], stats["WAITING"]
    p = palette
    padded_label = f"{label}:".ljust(8)
    if total == 0:
        return f"{p.label}{padded_label}{p.reset}{p.dim}0/0{p.reset}\n"

    pct_str = f"{done * 100 // total}%"
    done_total = f"{done}/{total}"
    plain_codes = []
    colored_codes = []
    for count, suffix, color in ((working, "W", p.working), (blocked, "B", p.blocked), (waiting, "Wa", p.waiting)):
        if count > 0:
            plain_codes.append(f"{count}{suffix}")
            colored_codes.append(f"{color}{count}{suffix}{p.reset}")
    codes = " ".join(plain_codes)
    shown_codes = " ".join(colored_codes) if p.use_color and codes else codes

    pad = max(width - (len(padded_label) + len(done_total) + 2 + len(codes)) - len(pct_str), 1)
    return (
        f"{p.label}{padded_label}{p.reset}{p.done}{done_total}{p.reset}  "
        f"{shown_codes}{' ' * pad}{pct_str}\n"
    )


def _elapsed(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


def render_compact_rows(
    queues: "Mapping | None", palette: PanePalette, width: int, now: float
) -> str:
    """Render the compact rows and the currently-working section.

    Args:
        queues:  The data project's ``get_queues`` result, or ``None`` for
                 an all-zero pane with no working task.
        palette: Escape codes (``resolve_pane_palette``).
        width:   Pane width (``pane_width``).
        now:     Current time, epoch seconds, for the elapsed figure.

    Returns:
        The rows, newline-terminated, without the provider line.
    """
    counts = queues["counts"] if queues else {}
    zero = {stat: 0 for stat in QUEUE_STATS}
    out = [_compact_row(role.lower(), counts.get(role, zero), palette, width) for role in QUEUE_ROLES]

    working = queues.get("working") if queues else None
    if working and working.get("task_id"):
        task_id = working["task_id"]
        match = _TASK_ID_RE.fullmatch(task_id)
        short = (match.group(4) if match else task_id)[:width]
        out.append("\n")
        out.append(f"{palette.dim}— currently working —{palette.reset}\n")
        out.append(f"{short}\n")
        mtime = int(working.get("mtime") or 0)
        if mtime > 0:
            started = time.strftime("%H:%M:%S", time.localtime(mtime))
            out.append(f"  started: {started}\n")
            out.append(f"  elapsed: {_elapsed(int(now) - mtime)}\n")
    return "".join(out)


# ---------------------------------------------------------------------------
# Details listing
# ---------------------------------------------------------------------------


def render_details_listing(details: Mapping, header_color: str, palette: PanePalette) -> str:
    """Render one project's per-agent, per-state listing.

    Args:
        details:      ``get_queues(...)["details"]`` -- the
                      ``queue_details.resolve_queue_details`` structure.
        header_color: Escape for the ``=== <agent> ===`` headers; ``""``
                      uses the palette's label colour.
        palette:      Escape codes (``resolve_pane_palette``).

    Returns:
        The listing, newline-terminated; ``""`` when no agent has an active
        task. Agents are separated by one blank line.
    """
    p = palette
    state_color = {"WORKING": p.working, "BLOCKED": p.blocked, "WAITING": p.waiting, "BACKLOG": p.dim}
    warn = p.yellow
    blocks = []
    for agent in AGENT_ORDER:
        states = details.get(agent, {})
        if not any(states.get(state) for state in STATE_ORDER):
            continue
        lines = [f"{header_color or p.label}{p.bold}=== {agent} ==={p.reset}"]
        for state in STATE_ORDER:
            entries = states.get(state) or []
            if not entries:
                continue
            lines.append(f"{state_color.get(state, '')}[{state}]{p.reset}")
            for entry in sorted(entries, key=lambda item: item["task_id"]):
                task_id, warning = entry["task_id"], entry.get("warning")
                if warning in _WARNING_TEXT:
                    lines.append(f"  {p.dim}{task_id}{p.reset}  {warn}{_WARNING_TEXT[warning]}{p.reset}")
                elif isinstance(warning, str) and warning.startswith(_UNKNOWN_STATE_PREFIX):
                    bad_state = warning.split(":", 1)[1]
                    lines.append(f"  {p.dim}{task_id}{p.reset}  {warn}⚠ [UNKNOWN STATE: {bad_state}]{p.reset}")
                else:
                    lines.append(f"  {p.dim}{task_id}{p.reset}")
        blocks.append("\n".join(lines) + "\n")
    return "\n".join(blocks)


# ---------------------------------------------------------------------------
# Planning and the whole pane
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class PanePlan:
    """Which projects one pane render reads and shows.

    Attributes:
        details:      Details mode (else compact).
        project:      The project the caller named, or ``""``.
        data_project: Source of the compact counters and the header halt
                      colour, or ``""``.
        listed:       Details mode: the projects whose listings are
                      rendered, in order. Empty in compact mode.
        fetch:        Every project whose ``get_queues`` result the render
                      reads -- the named project first, so a refusal for it
                      comes before anything else is read. Compact mode:
                      exactly ``(data_project,)``, even when that is ``""``
                      (which ``get_queues`` refuses).
    """

    details: bool
    project: str
    data_project: str
    listed: tuple[str, ...]
    fetch: tuple[str, ...]


def plan_queue_pane(ctx: OpsContext, project: str, data_project: str, details: bool) -> PanePlan:
    """Decide which projects a pane render needs.

    Args:
        ctx:          OpsContext for the kanban root.
        project:      The resolved project the caller named, or ``""``.
        data_project: The named project, else the caller's fallback project
                      (``PGAI_PROJECT_NAME``), else ``""``.
        details:      Details mode.

    Returns:
        The plan. In compact mode the data project is always fetched -- even
        an empty name or one with no directory -- so ``get_queues`` refuses
        it (``show_queues.no_project`` / ``show_queues.project_directory_missing``)
        rather than the pane printing counters it never read. In details
        mode, projects other than the named one are fetched only when their
        directory exists.
    """
    from pgai_agent_kanban.ops.read import list_projects

    if not details:
        # Compact rows are the data project's counters and nothing else, so
        # the data project is the one read -- whatever its name.
        return PanePlan(details=False, project=project, data_project=data_project, listed=(),
                        fetch=(data_project,))

    def exists(name: str) -> bool:
        return bool(name) and ctx.project_root(name).is_dir()

    if project:
        listed: tuple[str, ...] = (project,)
    else:
        try:
            registered = list_projects(ctx)
        except OpsError:
            registered = []
        listed = tuple(name for name in registered if exists(name))

    fetch: list[str] = [project] if project else []
    for name in (*listed, data_project):
        if name and name not in fetch and exists(name):
            fetch.append(name)
    return PanePlan(details=details, project=project, data_project=data_project, listed=listed, fetch=tuple(fetch))


@dataclasses.dataclass(frozen=True)
class PaneOutput:
    """A rendered pane: the text for stdout and any warning text for stderr."""

    stdout: str
    stderr: str


def render_queue_pane(
    ctx: OpsContext,
    plan: PanePlan,
    queues: Mapping[str, Mapping],
    *,
    environ: Mapping[str, str],
    now: float,
) -> PaneOutput:
    """Render the whole pane: the provider line, then compact rows or listings.

    Args:
        ctx:     OpsContext for the kanban root (kanban.cfg and the global
                 HALT sentinel).
        plan:    ``plan_queue_pane``'s result.
        queues:  ``get_queues`` results keyed by project, for every name in
                 ``plan.fetch``.
        environ: Environment for colours and pane width.
        now:     Current time, epoch seconds.

    Returns:
        The pane text and the provider warning line (``""`` when none).

    Raises:
        KeyError: In compact mode, when ``queues`` lacks the data project's
            result -- the caller skipped a fetch the plan named.
    """
    provider, warning = read_active_provider(ctx.kanban_root)
    palette = resolve_pane_palette(ctx.kanban_root, environ)
    out = f"Active provider: {provider}\n"
    data = queues.get(plan.data_project) if plan.data_project else None

    if plan.details:
        if data is not None:
            flag, text = data["halt"]["flag"], data["halt"]["text"]
        elif plan.data_project:
            flag, text = halt_flag_and_text(ctx.project_root(plan.data_project), ctx.kanban_root)
        else:
            flag, text = "no", ""
        header_color = palette.halt if flag == "yes" else (palette.yellow if text else "")
        out += "".join(render_details_listing(queues[name]["details"], header_color, palette) for name in plan.listed)
    else:
        # Indexed, not .get(): the compact plan always fetches its data
        # project, and a counter row is never rendered from a result that
        # was not read.
        out += render_compact_rows(queues[plan.data_project], palette, pane_width(environ), now)

    return PaneOutput(stdout=out, stderr=f"{warning}\n" if warning else "")
