"""
pgai_agent_kanban.board.classifier — the board's status classification.

One classifier decides which status every board item carries: the input
columns (bugs, priorities, requirements) and the agent queue columns (PM,
CODER, WRITER, TESTER, CM).  ``GET /board`` classifies through it, and so
does the tmux dashboard's column renderer
(``pgai_agent_kanban.dashboard.column_render``, driven by
``team/scripts/dashboard/column-render.sh``), which draws each row in the
colour of the status this module assigns -- so the board and the dashboard
cannot disagree about an item.

Board status vocabulary (the dashboard's display term -> board status):

    "running"      -> "working"   task in WORKING state, running intake file
                                  a live bundle (or none) still lists
    "carried"      -> "carried"   running intake file whose every listing
                                  bundle is closed -- see below
    "needs-operator" -> "needs-operator"
                                  needs-operator intake file (waiting on an
                                  operator-inbox answer)
    active RC req  -> "label"     requirements item targeting the active RC
    "done"         -> "done"      DONE task, shipped requirement, done intake file
    "wont-do"      -> "wont-do"   WONT-DO task, wont-do intake file (and a
                                  legacy "superseded" one -- see below)
    "duplicate"    -> "duplicate" duplicate intake file (closed in favour of
                                  the item its ## Close Note names)
    "blocked"      -> "blocked"   BLOCKED task, blocked intake file
    "open"         -> "open"      BACKLOG, WAITING, unknown, everything else
    quarantine     -> "blocked"   quarantined intake files (the project's rejected/
                                  directory, or a legacy <intake>/.rejected/)

Carried: a running item no live bundle is working
-------------------------------------------------
A bug or priority file at ``running`` is ``CARRIED_STATUS`` (``"carried"``)
when at least one bundle in the project's ``requirements/`` lists it and
every bundle that lists it is closed: the release shipped without fixing it,
and the next discovery pass converts it to ``needs-operator``.  The answer is
``lib.bundle_items.carried_item_names`` / ``is_carried`` -- the reading
discovery uses -- over the ``requirements/`` directory beside the item's own
intake directory (``intake_carried_names``).  A running item a bundle still
open or running lists, or one no bundle lists (orphaned), stays
``"working"``.  ``duplicate`` keeps its own token for the reason WONT-DO
does below: folding it into another closed status loses information.

WONT-DO renders as "wont-do" — its own status, never folded into "done"
-----------------------------------------------------------------------
A task whose ``## State`` is ``WONT-DO`` classifies as ``WONT_DO_STATUS``
(``"wont-do"``), and so does an intake file whose ``## Status`` is
``wont-do``.  The reasons:

* WONT-DO and DONE mean different things.  DONE is work that shipped;
  WONT-DO is work that was declined or closed without completion.  A status
  that cannot tell them apart loses information that no consumer can
  recover from the response.
* ``"wont-do"`` is already the board's public token: ``GET /board`` has
  always returned it for WONT-DO tasks, and the ICD's item-status
  vocabulary names it.  Folding it into ``"done"`` would change the API
  contract; keeping it changes nothing for API clients.
* The dashboard draws ``"done"`` and ``"wont-do"`` in the same green
  (both are settled, needing no action), so keeping the two apart costs
  the terminal rendering nothing.

A retired status read as a closed one
-------------------------------------
The closed states are ``lib.terminal_states.CLOSED_STATES`` (done,
wont-do). An existing file whose status is still the retired
``superseded`` reads as ``wont-do`` (``terminal_states.canonical_state``),
so it is drawn closed rather than open.

Release-state reads go through ``cm.read_state_field`` (the one
``release-state.md`` reader); the ``## State`` read of a task's
``status.md`` goes through ``task_status.read_state``.
"""

from __future__ import annotations

import logging
import re
import pathlib
from typing import Iterable, Optional

from pgai_agent_kanban.cm.read_state_field import read_release_state_field
from pgai_agent_kanban.lib import bundle_items
from pgai_agent_kanban.lib.semver import parse_version
from pgai_agent_kanban.lib.terminal_states import (
    CLOSED_STATES,
    DUPLICATE_STATE,
    NEEDS_OPERATOR_STATE,
    RUNNING_STATE,
    canonical_state,
)
from pgai_agent_kanban.task_status import read_state, section_value

_log = logging.getLogger(__name__)

__all__ = [
    "CARRIED_STATUS",
    "DUPLICATE_STATUS",
    "NEEDS_OPERATOR_STATUS",
    "WONT_DO_STATUS",
    "classify_input_item",
    "classify_intake_file",
    "classify_intake_status",
    "classify_queue_item",
    "classify_requirement",
    "get_target_version_for_item",
    "intake_carried_names",
    "read_intake_status",
    "marker_to_state",
    "parse_queue_line",
    "read_task_state_from_status_md",
    "get_active_rc_for_project",
    "get_last_released_for_project",
    "COLUMN_ORDER",
    "COLUMN_KINDS",
]

#: The board status a WONT-DO task or a wont-do intake file classifies as.
#: Kept distinct from "done" (see the module docstring for why).
WONT_DO_STATUS = "wont-do"

#: The board status a needs-operator intake file classifies as.
NEEDS_OPERATOR_STATUS = NEEDS_OPERATOR_STATE

#: The board status a duplicate intake file classifies as; its own token,
#: never folded into "wont-do".
DUPLICATE_STATUS = DUPLICATE_STATE

#: The board status of a running intake file whose every listing bundle is
#: closed (see the module docstring).
CARRIED_STATUS = "carried"

# ---------------------------------------------------------------------------
# Column definitions (canonical order per requirements spec)
# ---------------------------------------------------------------------------

#: Eight columns in the required order.
COLUMN_ORDER = ["BUGS", "PRIORITIES", "REQUIREMENTS", "PM", "CODER", "WRITER", "TESTER", "CM"]

#: Maps each column name to its data-source kind.
#: "input" columns read files from a directory; "queue" columns read a backlog file.
COLUMN_KINDS = {
    "BUGS": "input",
    "PRIORITIES": "input",
    "REQUIREMENTS": "input",
    "PM": "queue",
    "CODER": "queue",
    "WRITER": "queue",
    "TESTER": "queue",
    "CM": "queue",
}

# ---------------------------------------------------------------------------
# Regex patterns — the requirements Target Version shape, queue lines
# ---------------------------------------------------------------------------

# A ``## Target Version`` value's leading dotted version (``v`` optional).
_TARGET_VER_RE = re.compile(r"v?\d+\.\d+\.\d+")

# Queue line pattern.
# Accepts both old format (PARTICIPANT-AGENT-YYYYMMDD-NNN-slug) and
# new format (AGENT-YYYYMMDD-NNN-slug).
_QUEUE_LINE_RE = re.compile(
    r"^\s*-\s+\[(.)\]\s+"
    r"(((?:CLAUDE|CODEX|GEMINI)-[A-Z][A-Z0-9]*-(\d{8})-(\d+)-\S+)"  # old: grp2=id grp3=date grp4=seq
    r"|([A-Z][A-Z0-9]*-(\d{8})-(\d+)-\S+))",                          # new: grp5=id grp6=date grp7=seq
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Version helpers — active-RC match and shipped test
# ---------------------------------------------------------------------------


def _matches_active_rc(version_str: str, active_rc: str) -> bool:
    """Return True if version_str matches the active RC version string.

    Returns False when active_rc is empty or "none".
    """
    if not active_rc or active_rc.lower() in ("none", ""):
        return False
    tv = parse_version(version_str)
    arc = parse_version(active_rc)
    if tv is None or arc is None:
        return False
    return tv == arc


def _is_shipped(version_str: str, last_released: str) -> bool:
    """Return True if version_str <= last_released.

    Returns False when last_released is empty, "none", or "v0.0.0" (sentinels).
    """
    if not last_released or last_released.lower() in ("none", "v0.0.0", ""):
        return False
    tv = parse_version(version_str)
    lv = parse_version(last_released)
    if tv is None or lv is None:
        return False
    return tv <= lv


# ---------------------------------------------------------------------------
# File readers — intake ## Status and requirements ## Target Version
# ---------------------------------------------------------------------------


def read_intake_status(path: pathlib.Path, default: str = "open") -> str:
    """Return an intake file's ``## Status`` value, lowercased, or *default*.

    The value is the first whitespace-delimited token of the section's
    value, read through ``task_status.section_value``.  *default* is
    returned when the file has no ``## Status`` heading, the section holds
    no value, or the file cannot be read.  The dashboard's single-project
    pane passes ``default=""`` so a deferral glyph is drawn only on a card
    whose file says ``open``; classification treats both answers as
    ``"open"``.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        tokens = section_value(text, "Status").split()
        return tokens[0].lower() if tokens else default
    except OSError:
        return default


def _get_file_status(path: pathlib.Path) -> str:
    """Return the ## Status field value from a markdown file, or 'open'."""
    return read_intake_status(path)


def _get_target_version(path: pathlib.Path) -> str:
    """Return the ## Target Version field from a requirements file, or ''.

    The field is the dotted version (``v`` optional) that starts the first
    token of the section's value; '' when the value starts with anything
    else (``auto``) or the section is absent or empty.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        tokens = section_value(text, "Target Version").split()
        m = _TARGET_VER_RE.match(tokens[0]) if tokens else None
        return m.group(0) if m else ""
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Task state readers — status.md ## State and the queue-marker fallback
# ---------------------------------------------------------------------------


def _marker_to_state(marker: str) -> str:
    """Map a queue file marker character to a task state string."""
    m = marker.upper()
    if m == "X":
        return "DONE"
    if m == "A":
        return "WORKING"
    if m == "B":
        return "BLOCKED"
    if m == "W":
        return "WAITING"
    return "BACKLOG"


def read_task_state_from_status_md(tasks_dir: pathlib.Path, task_id: str) -> str:
    """Read the ## State field from a task's status.md.

    Returns the first whitespace-delimited token of the value, uppercased.
    The value is read through task_status.read_state, the one status.md
    State reader.  Returns empty string when the status.md is absent or
    unreadable.
    """
    status_path = tasks_dir / task_id / "status.md"
    if not status_path.is_file():
        return ""
    tokens = read_state(status_path).split()
    return tokens[0] if tokens else ""


# ---------------------------------------------------------------------------
# Release-state readers
# ---------------------------------------------------------------------------


def _release_state_field(project_dir: pathlib.Path, heading: str) -> str:
    """Return *heading*'s validated value from *project_dir*/release-state.md.

    Reads through ``cm.read_state_field.read_release_state_field`` — the one
    release-state.md reader — so the board and ``/projects`` see the value
    exactly as the loader validates it. The board is a display surface, so a
    refused field (missing heading, empty value, a value outside the schema's
    accepted forms) or an unreadable file is logged with its dual-channel
    refusal and reads as ``""`` rather than failing the whole response; a
    missing file reads as ``""`` too.
    """
    release_state = project_dir / "release-state.md"
    try:
        val = read_release_state_field(str(release_state), heading)
    except (OSError, ValueError) as exc:
        _log.warning(
            "release-state.md field %r unreadable for %s; shown as blank:\n%s",
            heading, project_dir, exc,
        )
        return ""
    return "" if val == "none" else val


def get_active_rc_for_project(project_dir: pathlib.Path) -> str:
    """Return the Active RC version string from release-state.md, or ''.

    Returns empty string when the file is absent, or when the Active RC
    value is "none", empty, or not a valid vX.Y.Z semver string (the
    loader's schema refuses the last two).
    """
    return _release_state_field(project_dir, "Active RC")


def get_last_released_for_project(project_dir: pathlib.Path) -> str:
    """Return the Last Released version string from release-state.md, or ''.

    Returns empty string when the file is absent, or when the value is
    "none", "v0.0.0", empty, or not a vX.Y.Z version (the first two are
    "nothing released yet" sentinels; the loader's schema refuses the last
    two).
    """
    val = _release_state_field(project_dir, "Last Released")
    return "" if val == "v0.0.0" else val


# ---------------------------------------------------------------------------
# Queue line parser
# ---------------------------------------------------------------------------


def parse_queue_line(line: str) -> Optional[tuple]:
    """Parse one line from an agent backlog file.

    Returns ``(marker, task_id, date_str, seq_int)`` when the line matches
    the queue-line pattern, or ``None`` when it does not.

    Handles both old (PARTICIPANT-AGENT-DATE-SEQ-slug)
    and new (AGENT-DATE-SEQ-slug) task ID formats.
    """
    m = _QUEUE_LINE_RE.match(line)
    if not m:
        return None
    marker = m.group(1)
    task_id = m.group(2)
    if not task_id:
        return None
    # Determine date/seq from whichever format matched.
    if m.group(3) is not None:
        # Old format.
        date_str, seq_str = m.group(4), m.group(5)
    else:
        # New format.
        date_str, seq_str = m.group(7), m.group(8)
    if not date_str or not seq_str:
        return None
    return marker, task_id.strip(), date_str, int(seq_str)


# ---------------------------------------------------------------------------
# Core classification functions — the API-facing status vocabulary
# ---------------------------------------------------------------------------


def classify_queue_item(state: str) -> str:
    """Classify a queue item's task state into a board status string.

    Maps task states to the board status vocabulary.  A WONT-DO state stays
    ``WONT_DO_STATUS`` rather than folding into "done" (the module
    docstring records why).

    Args:
        state: The task state string (e.g. "WORKING", "DONE", "WONT-DO").

    Returns:
        One of: "working", "done", "wont-do", "blocked", "open".
    """
    s = canonical_state(state)
    if s == "working":
        return "working"
    if s in CLOSED_STATES:
        return s
    if s == "blocked":
        return "blocked"
    # BACKLOG, WAITING, unknown → open
    return "open"


def get_target_version_for_item(path: pathlib.Path) -> str:
    """Return the ## Target Version field from a requirements file, or ''.

    Public wrapper for _get_target_version so callers do not import private names.
    """
    return _get_target_version(path)


def marker_to_state(marker: str) -> str:
    """Map a queue file marker character to a task state string.

    Public wrapper for _marker_to_state so callers do not import private names.
    """
    return _marker_to_state(marker)


def classify_input_item(
    path: pathlib.Path,
    column: str,
    active_rc: str,
    last_released: str,
    carried: Optional[Iterable[str]] = None,
) -> str:
    """Classify an input-directory item (bug, priority, or requirement).

    Requirements classify by target version (``classify_requirement``);
    bugs and priorities by their ``## Status`` and file name
    (``classify_intake_status``).  An intake file whose ## Status is
    ``wont-do`` classifies as ``WONT_DO_STATUS``.

    Args:
        path:          Path to the markdown input file.
        column:        Column name ("BUGS", "PRIORITIES", or "REQUIREMENTS").
        active_rc:     Active RC version string (e.g. "v1.6.0") or "" when none.
        last_released: Last released version string (e.g. "v1.5.0") or "" when none.
        carried:       The carried item names of the file's intake directory
                       (``intake_carried_names``).  ``None`` reads them for
                       a running file only; a caller classifying a whole
                       directory passes them once.

    Returns:
        One of: "open", "working", "carried", "needs-operator", "done",
        "wont-do", "duplicate", "blocked", "label".
    """
    if column == "REQUIREMENTS":
        return classify_requirement(_get_target_version(path), active_rc, last_released)
    # Bugs and priorities: read the ## Status field directly.
    return classify_intake_file(path, _get_file_status(path), carried)


def classify_intake_file(
    path: pathlib.Path,
    raw_status: str,
    carried: Optional[Iterable[str]] = None,
) -> str:
    """Classify the bug or priority file at *path* whose ``## Status`` is *raw_status*.

    ``classify_intake_status`` with the file's own name.  When *carried* is
    ``None`` and the file is ``running``, the carried set is read from the
    bundles beside its directory (``intake_carried_names(path.parent)``), so
    a caller that classifies one file needs nothing more; a caller
    classifying a whole directory reads the set once and passes it.

    Returns:
        One of: "working", "carried", "needs-operator", "done", "wont-do",
        "duplicate", "blocked", "open".
    """
    if carried is None and canonical_state(raw_status) == RUNNING_STATE:
        carried = intake_carried_names(path.parent)
    return classify_intake_status(raw_status, path.name, carried or ())


def intake_carried_names(input_dir: pathlib.Path) -> frozenset:
    """Return the names of *input_dir*'s items whose every listing bundle is closed.

    *input_dir* is a project's ``bugs/`` or ``priority/`` directory; the
    bundles are the ``requirements/`` directory beside it, read through
    ``lib.bundle_items.carried_item_names`` (the reading discovery's carried
    pass uses).  Pass the answer to ``classify_intake_status`` as *carried*:
    only a ``running`` item in it classifies as ``CARRIED_STATUS``.  A
    missing ``requirements/`` directory gives an empty set.
    """
    return bundle_items.carried_item_names(pathlib.Path(input_dir).parent / "requirements")


def classify_intake_status(
    raw_status: str,
    name: str = "",
    carried: Iterable[str] = (),
) -> str:
    """Classify a bug or priority file's ``## Status`` value.

    ``running`` is ``CARRIED_STATUS`` (``"carried"``) when the file *name*
    is in *carried* (``intake_carried_names`` of its directory: every bundle
    listing it is closed), else ``"working"``.  ``needs-operator`` is
    ``NEEDS_OPERATOR_STATUS``; the closed states (``CLOSED_STATES``:
    ``done``, ``wont-do``, ``duplicate``) and ``blocked`` pass through, and
    the retired ``superseded`` reads as ``wont-do``; anything else
    (``open``, an empty value, an unknown word) is ``"open"``.  Case and
    surrounding whitespace are ignored.

    Args:
        raw_status: The file's ``## Status`` value.
        name:       The file's name (``BUG-0001-example.md``); only a
                    running file's name is looked up.
        carried:    The carried item names of the file's directory.  The
                    default (none) classifies every running file as
                    ``"working"``.

    Returns:
        One of: "working", "carried", "needs-operator", "done", "wont-do",
        "duplicate", "blocked", "open".
    """
    s = canonical_state(raw_status)
    if s == RUNNING_STATE:
        return CARRIED_STATUS if bundle_items.is_carried(s, name, carried) else "working"
    if s == NEEDS_OPERATOR_STATE:
        return NEEDS_OPERATOR_STATUS
    if s in CLOSED_STATES or s == "blocked":
        return s
    return "open"


def classify_requirement(target_version: str, active_rc: str, last_released: str) -> str:
    """Classify a requirements item from its ``## Target Version``.

    A target equal to the active RC is ``"label"`` (the dashboard draws it
    with an ``[active]`` suffix); a target at or below the last release is
    ``"done"``; anything else -- unshipped, or no target version -- is
    ``"open"``.  The requirements file's own ``## Status`` plays no part.

    Args:
        target_version: The item's target version (e.g. "v1.6.0") or "".
        active_rc:      Active RC version string or "" / "none" when none.
        last_released:  Last released version or "" / "none" / "v0.0.0".

    Returns:
        One of: "label", "done", "open".
    """
    if target_version and _matches_active_rc(target_version, active_rc):
        return "label"
    if target_version and _is_shipped(target_version, last_released):
        return "done"
    return "open"
