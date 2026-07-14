"""
board_classifier.py — Shared status-classification logic for GET /board.

Classification is the source of truth for how items appear on the board.  The
rules here are an exact Python mirror of the classification logic embedded in
team/scripts/dashboard/column-render.sh (the tmux dashboard renderer).  Both
the /board endpoint and the parity-pin test import this module; diverging the
logic between them would silently break the house-sibling rule.

Source reference (the classification functions mirrored here):
  team/scripts/dashboard/column-render.sh
    — queue_state_to_status()   ~line 794
    — get_file_status()         ~line 655 / ~line 1479
    — STATUS_RE                 ~line 649 / ~line 1477
    — TARGET_VER_RE             ~line 650 / ~line 1464
    — matches_active_rc()       ~line 694 / ~line 1428
    — is_shipped()              ~line 703 (two-arg) / ~line 1438 (one-arg)
    — parse_version()           ~line 684 / ~line 1417
    — get_target_version()      ~line 663 / ~line 1466
    — QUEUE_LINE_RE             ~line 736
    — parse_queue_line_match()  ~line 743
    — read_task_state()         ~line 771
    — marker_to_state()         ~line 782

Board status vocabulary (maps from renderer terms to API terms):
  Renderer term  → Board status string
  "running"      → "working"   (task in WORKING state)
  active RC req  → "label"     (requirements item targeting the active RC)
  "done"         → "done"
  "wont-do"      → "wont-do"
  "blocked"      → "blocked"
  "open"         → "open"
  quarantine     → "blocked"   (files in .rejected/ dirs)
"""

from __future__ import annotations

import re
import pathlib
from typing import Optional

__all__ = [
    "classify_input_item",
    "classify_queue_item",
    "get_target_version_for_item",
    "marker_to_state",
    "parse_queue_line",
    "read_task_state_from_status_md",
    "get_active_rc_for_project",
    "get_last_released_for_project",
    "COLUMN_ORDER",
    "COLUMN_KINDS",
]

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
# Regex patterns — mirrors of column-render.sh constants
# (Source: team/scripts/dashboard/column-render.sh ~lines 649-653, 736-741, 1464, 1477)
# ---------------------------------------------------------------------------

_STATUS_RE = re.compile(
    r"^##\s+Status\s*\n+\s*(\S+)",
    re.MULTILINE | re.IGNORECASE,
)
_TARGET_VER_RE = re.compile(
    r"^##\s+Target Version\s*\n+\s*(v?\d+\.\d+\.\d+)",
    re.MULTILINE | re.IGNORECASE,
)
_STATE_RE = re.compile(
    r"^##\s+State\s*\n+\s*(\S+)",
    re.MULTILINE | re.IGNORECASE,
)
_ACTIVE_RC_RE = re.compile(
    r"^##\s+Active RC\s*\n+\s*(\S+)",
    re.MULTILINE | re.IGNORECASE,
)
_LAST_RELEASED_RE = re.compile(
    r"^##\s+Last Released\s*\n+\s*(\S+)",
    re.MULTILINE | re.IGNORECASE,
)

# Queue line pattern — mirrors column-render.sh QUEUE_LINE_RE (~line 736).
# Accepts both old format (PARTICIPANT-AGENT-YYYYMMDD-NNN-slug) and
# new format (AGENT-YYYYMMDD-NNN-slug).
_QUEUE_LINE_RE = re.compile(
    r"^\s*-\s+\[(.)\]\s+"
    r"(((?:CLAUDE|CODEX|GEMINI)-[A-Z][A-Z0-9]*-(\d{8})-(\d+)-\S+)"  # old: grp2=id grp3=date grp4=seq
    r"|([A-Z][A-Z0-9]*-(\d{8})-(\d+)-\S+))",                          # new: grp5=id grp6=date grp7=seq
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Version helpers — mirrors of column-render.sh parse_version / matches_active_rc
# (Source: team/scripts/dashboard/column-render.sh ~lines 684-715)
# ---------------------------------------------------------------------------


def _parse_version(v: str) -> Optional[tuple]:
    """Parse vX.Y.Z or X.Y.Z into a (major, minor, patch) tuple, or None."""
    v = v.lstrip("vV")
    parts = v.split(".")
    if len(parts) < 3:
        return None
    try:
        return tuple(int(p) for p in parts[:3])
    except ValueError:
        return None


def _matches_active_rc(version_str: str, active_rc: str) -> bool:
    """Return True if version_str matches the active RC version string.

    Mirrors matches_active_rc() in column-render.sh (~line 694).
    Returns False when active_rc is empty or "none".
    """
    if not active_rc or active_rc.lower() in ("none", ""):
        return False
    tv = _parse_version(version_str)
    arc = _parse_version(active_rc)
    if tv is None or arc is None:
        return False
    return tv == arc


def _is_shipped(version_str: str, last_released: str) -> bool:
    """Return True if version_str <= last_released.

    Mirrors is_shipped() in column-render.sh (~lines 703-715 two-arg form).
    Returns False when last_released is empty, "none", or "v0.0.0" (sentinels).
    """
    if not last_released or last_released.lower() in ("none", "v0.0.0", ""):
        return False
    tv = _parse_version(version_str)
    lv = _parse_version(last_released)
    if tv is None or lv is None:
        return False
    return tv <= lv


# ---------------------------------------------------------------------------
# File readers — mirrors of column-render.sh get_file_status / get_target_version
# (Source: team/scripts/dashboard/column-render.sh ~lines 655-669, 1479-1486)
# ---------------------------------------------------------------------------


def _get_file_status(path: pathlib.Path) -> str:
    """Return the ## Status field value from a markdown file, or 'open'.

    Mirrors get_file_status() in column-render.sh (~line 655 and ~line 1479).
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        m = _STATUS_RE.search(text)
        return m.group(1).strip().lower() if m else "open"
    except OSError:
        return "open"


def _get_target_version(path: pathlib.Path) -> str:
    """Return the ## Target Version field from a requirements file, or ''.

    Mirrors get_target_version() in column-render.sh (~line 663 and ~line 1466).
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        m = _TARGET_VER_RE.search(text)
        return m.group(1).strip() if m else ""
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Task state readers — mirrors of column-render.sh read_task_state / marker_to_state
# (Source: team/scripts/dashboard/column-render.sh ~lines 771-792)
# ---------------------------------------------------------------------------


def _marker_to_state(marker: str) -> str:
    """Map a queue file marker character to a task state string.

    Mirrors marker_to_state() in column-render.sh (~line 782).
    """
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

    Mirrors read_task_state() in column-render.sh (~line 771).
    Returns empty string when the status.md is absent or unreadable.
    """
    status_path = tasks_dir / task_id / "status.md"
    if not status_path.is_file():
        return ""
    try:
        text = status_path.read_text(encoding="utf-8", errors="replace")
        m = _STATE_RE.search(text)
        return m.group(1).strip().upper() if m else ""
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Release-state readers
# ---------------------------------------------------------------------------


def get_active_rc_for_project(project_dir: pathlib.Path) -> str:
    """Return the Active RC version string from release-state.md, or ''.

    Returns empty string when the file is absent, or when the Active RC
    value is "none", empty, or not a valid vX.Y.Z semver string.
    """
    release_state = project_dir / "release-state.md"
    if not release_state.is_file():
        return ""
    try:
        text = release_state.read_text(encoding="utf-8", errors="replace")
        m = _ACTIVE_RC_RE.search(text)
        if not m:
            return ""
        val = m.group(1).strip()
        if not val or val.lower() == "none":
            return ""
        # Accept only vX.Y.Z semver.
        if not re.match(r"^v\d+\.\d+\.\d+$", val):
            return ""
        return val
    except OSError:
        return ""


def get_last_released_for_project(project_dir: pathlib.Path) -> str:
    """Return the Last Released version string from release-state.md, or ''.

    Returns empty string when the file is absent, or when the value is
    "none", "v0.0.0", or empty (sentinel values per column-render.sh convention).
    """
    release_state = project_dir / "release-state.md"
    if not release_state.is_file():
        return ""
    try:
        text = release_state.read_text(encoding="utf-8", errors="replace")
        m = _LAST_RELEASED_RE.search(text)
        if not m:
            return ""
        val = m.group(1).strip()
        if not val or val.lower() in ("none", "v0.0.0"):
            return ""
        return val
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Queue line parser
# (Source: team/scripts/dashboard/column-render.sh ~lines 736-768)
# ---------------------------------------------------------------------------


def parse_queue_line(line: str) -> Optional[tuple]:
    """Parse one line from an agent backlog file.

    Returns ``(marker, task_id, date_str, seq_int)`` when the line matches
    the queue-line pattern, or ``None`` when it does not.

    Mirrors QUEUE_LINE_RE + parse_queue_line_match() in column-render.sh
    (~lines 736-768).  Handles both old (PARTICIPANT-AGENT-DATE-SEQ-slug)
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

    Maps task states to the API board status vocabulary.  Mirrors
    queue_state_to_status() in column-render.sh (~line 794), with the
    renderer's "running" translated to the API's "working".

    Args:
        state: The task state string (e.g. "WORKING", "DONE", "BLOCKED").

    Returns:
        One of: "working", "done", "wont-do", "blocked", "open".
    """
    s = state.upper()
    if s == "WORKING":
        return "working"
    if s == "DONE":
        return "done"
    if s == "WONT-DO":
        return "wont-do"
    if s == "BLOCKED":
        return "blocked"
    # BACKLOG, WAITING, unknown → open
    return "open"


def get_target_version_for_item(path: pathlib.Path) -> str:
    """Return the ## Target Version field from a requirements file, or ''.

    Public wrapper for _get_target_version so board.py does not import private names.
    """
    return _get_target_version(path)


def marker_to_state(marker: str) -> str:
    """Map a queue file marker character to a task state string.

    Public wrapper for _marker_to_state so board.py does not import private names.
    """
    return _marker_to_state(marker)


def classify_input_item(
    path: pathlib.Path,
    column: str,
    active_rc: str,
    last_released: str,
) -> str:
    """Classify an input-directory item (bug, priority, or requirement).

    Mirrors the status derivation logic in column-render.sh for IS_INPUT
    items (~lines 878-895 multi-project heredoc and ~lines 1582-1622
    single-project heredoc), with the renderer's "running" translated to
    the API's "working" and the "[active]" annotation translated to "label".

    Args:
        path:          Path to the markdown input file.
        column:        Column name ("BUGS", "PRIORITIES", or "REQUIREMENTS").
        active_rc:     Active RC version string (e.g. "v1.6.0") or "" when none.
        last_released: Last released version string (e.g. "v1.5.0") or "" when none.

    Returns:
        One of: "open", "working", "done", "wont-do", "blocked", "label".
    """
    is_req = (column == "REQUIREMENTS")

    if is_req:
        target_ver = _get_target_version(path)
        if target_ver and _matches_active_rc(target_ver, active_rc):
            # Requirements item targeting the active RC → "label" in the API
            # (renderer shows this with the "[active]" suffix / yellow color).
            return "label"
        if target_ver and _is_shipped(target_ver, last_released):
            return "done"
        # Unshipped or no target version → open.
        return "open"
    else:
        # Bugs and priorities: read the ## Status field directly.
        raw_status = _get_file_status(path)
        s = raw_status.strip().lower()
        if s == "running":
            return "working"
        if s in ("done", "wont-do", "blocked"):
            return s
        # open or anything else → open.
        return "open"
