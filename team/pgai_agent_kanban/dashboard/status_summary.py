"""
status_summary.py — the one-shot terminal status view.

Renders what ``kanban-status.sh`` prints and ``GET /status`` returns in its
envelope's ``stdout``, from the :class:`StatusSnapshot` that
``pgai_agent_kanban.ops.read.get_status`` reads::

    pgai-kanban vX.Y.Z    RC: vX.Y.Z (3h 12m)    Last: vX.Y.Z
    HALT: off    Working: CODER-...-005-model-resolution (3m 47s)

    Queue:
      pm:     1/1 done
      coder:  3/5 done, 1 working
      writer: 0/0
      tester: 0/1 done, waiting
      cm:     1/2 done, waiting

    Progress: [######.........] 45%

    (no blocked tasks)

    Quarantined: 0
    Ship posture: matrix

The last line is the project's resolved ship posture, ``matrix`` or
``always``: what ``[release] ship_posture`` resolves to once the remote-lane
guard has been applied (see ``StatusSnapshot.ship_posture``).

Colour follows the dashboard rule (``lib.metrics.color_enabled``): off with
``--no-color``, a non-empty ``NO_COLOR``, or a ``dumb`` or unset ``TERM``.
With colour on, the progress bar is drawn with shade blocks; without it,
with ``#`` and ``.``. The colour each element takes is named by the
``PGAI_DASHBOARD_COLOR_*`` environment variables (see :data:`COLOR_ENV`).

Elapsed times are whole seconds between the recorded instant and *now*,
divided with truncation toward zero:

  - the RC's age (shown only with an Active RC and a parseable
    ``RC Opened At``): ``<N>m`` under an hour, ``<H>h <M>m`` under a day,
    ``<D>d`` beyond;
  - the working task's age (from its status.md mtime): ``<S>s`` under a
    minute, ``<M>m <S>s`` under an hour, ``<H>h <M>m`` beyond.
"""

from __future__ import annotations

import dataclasses
from typing import Mapping, Optional

from pgai_agent_kanban.dashboard.task_counts import QUEUE_ROLES, TaskCounts
from pgai_agent_kanban.lib.palette import ansi_code
from pgai_agent_kanban.lib.timeutil import try_parse_iso

__all__ = [
    "COLOR_ENV",
    "StatusSnapshot",
    "render_status_summary",
]

#: Display element -> (environment variable naming its colour, default colour).
COLOR_ENV: dict[str, tuple[str, str]] = {
    "done": ("PGAI_DASHBOARD_COLOR_DONE", "green"),
    "working": ("PGAI_DASHBOARD_COLOR_WORKING", "yellow"),
    "blocked": ("PGAI_DASHBOARD_COLOR_BLOCKED", "red"),
    "waiting": ("PGAI_DASHBOARD_COLOR_WAITING", "yellow"),
    "label": ("PGAI_DASHBOARD_COLOR_LABEL", "cyan"),
    "header": ("PGAI_DASHBOARD_COLOR_HEADER", "cyan"),
    "halt_on": ("PGAI_DASHBOARD_COLOR_HALT", "red"),
    "halt_off": ("PGAI_DASHBOARD_COLOR_OK", "green"),
}

_BAR_WIDTH = 15
_SHADE_FILLED = "▓"  # DARK SHADE
_SHADE_EMPTY = "░"  # LIGHT SHADE


@dataclasses.dataclass(frozen=True)
class StatusSnapshot:
    """Everything the status summary shows for one project.

    Attributes:
        project:        The project name.
        kanban_version: The deployed framework version (``unknown`` when none).
        active_rc:      ``vX.Y.Z`` or ``none``.
        rc_opened_at:   The ``RC Opened At`` value as written, or ``none``.
        last_released:  The project's last released version.
        halt_flag:      ``yes`` when the project or the kanban is halted.
        counts:         The task-state counts of the project's ``tasks/``.
        blocked_tasks:  Task ids whose ``## State`` reads exactly ``BLOCKED``,
                        in path order.
        quarantined:    Files quarantined in the project's ``rejected/``.
        ship_posture:   The project's resolved ship posture, ``matrix`` or
                        ``always``, after the remote-lane guard: a lane that
                        pushes to origin with ``always`` set but not
                        confirmed reads ``matrix``.
        ship_posture_notice:
                        The warning lines the posture resolver wrote while
                        resolving (an unrecognized value, or ``always``
                        falling back to ``matrix`` under the remote-lane
                        guard), each newline-terminated; ``""`` when it
                        resolved without one.
    """

    project: str
    kanban_version: str
    active_rc: str
    rc_opened_at: str
    last_released: str
    halt_flag: str
    counts: TaskCounts
    blocked_tasks: tuple[str, ...]
    quarantined: int
    ship_posture: str
    ship_posture_notice: str = ""


def _tdiv(numerator: int, denominator: int) -> int:
    """Integer division truncating toward zero."""
    quotient = abs(numerator) // denominator
    return quotient if numerator >= 0 else -quotient


def _rc_age(opened_at: str, now: int) -> str:
    """The RC age label, or ``""`` when *opened_at* does not parse."""
    opened = try_parse_iso(opened_at, naive_as_utc=False) if opened_at else None
    if opened is None:
        return ""
    elapsed = now - int(opened.timestamp())
    if elapsed < 3600:
        return f"{_tdiv(elapsed, 60)}m"
    if elapsed < 86400:
        return f"{_tdiv(elapsed, 3600)}h {_tdiv(elapsed % 3600, 60)}m"
    return f"{_tdiv(elapsed, 86400)}d"


def _working_age(mtime: int, now: int) -> str:
    """The working task's age label."""
    elapsed = now - mtime
    if elapsed < 60:
        return f"{elapsed}s"
    if elapsed < 3600:
        return f"{elapsed // 60}m {elapsed % 60}s"
    return f"{elapsed // 3600}h {(elapsed % 3600) // 60}m"


def render_status_summary(
    snapshot: StatusSnapshot,
    *,
    use_color: bool,
    now: int,
    environ: Optional[Mapping[str, str]] = None,
) -> str:
    """Render *snapshot* as the status summary text (see the module docstring).

    Args:
        snapshot:  What :func:`pgai_agent_kanban.ops.read.get_status` read.
        use_color: Whether ANSI colour codes are emitted.
        now:       The current time, whole epoch seconds.
        environ:   Where the ``PGAI_DASHBOARD_COLOR_*`` names are read
                   (default: no overrides).

    Returns:
        The summary, every line newline-terminated.
    """
    env = environ or {}
    c = {name: ansi_code(env.get(var) or default, use_color) for name, (var, default) in COLOR_ENV.items()}
    reset, bold, dim = (ansi_code(name, use_color) for name in ("reset", "bold", "dim"))
    counts = snapshot.counts
    lines: list[str] = []

    rc_display = snapshot.active_rc
    rc_age = _rc_age(snapshot.rc_opened_at, now)
    if rc_age and snapshot.active_rc != "none":
        rc_display = f"{snapshot.active_rc} ({rc_age})"
    lines.append(
        f"{bold}{c['header']}pgai-kanban {snapshot.kanban_version}{reset}"
        f"    RC: {rc_display}    Last: {snapshot.last_released}"
    )

    if snapshot.halt_flag == "yes":
        halt = f"{c['halt_on']}HALT: on{reset}"
    else:
        halt = f"{c['halt_off']}HALT: off{reset}"
    working = counts.working
    if working is None:
        working_display = f"{dim}(none){reset}"
    elif working.mtime > 0:
        working_display = f"{c['working']}{working.task_id}{reset} ({_working_age(working.mtime, now)})"
    else:
        working_display = f"{c['working']}{working.task_id}{reset}"
    lines.append(f"{halt}    Working: {working_display}")

    lines += ["", f"{c['label']}Queue:{reset}"]
    for role in QUEUE_ROLES:
        row = counts.queues[role]
        annotations = []
        if row["WORKING"] > 0:
            annotations.append(f"{c['working']}{row['WORKING']} working{reset}")
        if row["BLOCKED"] > 0:
            annotations.append(f"{c['blocked']}{row['BLOCKED']} blocked{reset}")
        if row["WAITING"] > 0 and row["DONE"] < row["TOTAL"]:
            annotations.append(f"{c['waiting']}waiting{reset}")
        label = f"{c['label']}{role.lower() + ':':<8}{reset}"
        if row["TOTAL"] == 0:
            lines.append(f"  {label}{dim}0/0{reset}")
        else:
            done = f"{c['done']}{row['DONE']}/{row['TOTAL']} done{reset}"
            lines.append(f"  {label}{done}" + (", " + ", ".join(annotations) if annotations else ""))

    total, done_count = counts.totals["TOTAL"], counts.totals["DONE"]
    filled = done_count * _BAR_WIDTH // total if total > 0 else 0
    percent = done_count * 100 // total if total > 0 else 0
    filled_char, empty_char = (_SHADE_FILLED, _SHADE_EMPTY) if use_color else ("#", ".")
    bar = filled_char * filled + empty_char * (_BAR_WIDTH - filled)
    lines += ["", f"{c['label']}Progress:{reset} [{c['done']}{bar}{reset}] {percent}%"]

    lines.append("")
    blocked = counts.totals["BLOCKED"]
    if blocked > 0:
        lines.append(f"{c['blocked']}Blocked ({blocked}):{reset}")
        lines += [f"  {c['blocked']}{task_id}{reset}" for task_id in snapshot.blocked_tasks]
    else:
        lines.append(f"{dim}(no blocked tasks){reset}")

    lines += ["", f"{c['label']}Quarantined:{reset} {dim}{snapshot.quarantined}{reset}"]
    lines.append(f"{c['label']}Ship posture:{reset} {snapshot.ship_posture}")
    return "\n".join(lines) + "\n"
