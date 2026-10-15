"""
pgai_agent_kanban.dashboard.column_render.cells — cell formatting shared by
the single-project and multi-project board column panes.

Everything here is pure: no file reads, no environment reads.  The two pane
renderers (``single`` and ``multi``) read the board, classify each item
through ``pgai_agent_kanban.board`` and format the result with these
helpers, so a row's colour always follows the board status ``GET /board``
reports for the same item.

Display status
--------------
The panes colour, pin and anchor rows by a *display status*, derived from
the board status in exactly one place (:func:`display_status`):

    board status          display status   colour
    "working"             "running"        yellow
    "label" (active RC)   "running"        yellow (plus an "[active]" suffix)
    "done"                "done"           green
    "wont-do"             "wont-do"        green
    "blocked"             "blocked"        red
    "open"                "open"           white

``"running"`` is the word the row-selection helpers key on
(``pgai_agent_kanban.dashboard.status_priority_cap`` never evicts a
``"running"`` row; the requirements window centres on the first one).
``"done"`` and ``"wont-do"`` share one colour because both are settled and
need no action; they stay distinct statuses, as on the board.
"""

from __future__ import annotations

import dataclasses
import pathlib
import re
import sys
from typing import Callable, Sequence, TypeVar

# Run by file path (``python3 .../cells.py``) or loaded from a file
# outside its package, this module has no parent package and the interpreter
# puts only its own directory on sys.path, so the package imports below would
# not resolve. Put this file's own package root -- the directory holding the
# pgai_agent_kanban/ this file sits in -- at the front of sys.path so the
# imports resolve from the same tree, never another. Imported as a package
# member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[3])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.board import WONT_DO_STATUS  # noqa: E402
from pgai_agent_kanban.dashboard.defer_card_glyph import defer_card_glyph_suffix  # noqa: E402
from pgai_agent_kanban.lib.version_ceiling import exceeded_ceiling  # noqa: E402

__all__ = [
    "ANSI_ESC_RE",
    "CEILING_BLOCKED_SUFFIX",
    "CEILING_UNREADABLE_SUFFIX",
    "CeilingCards",
    "DeferCards",
    "Palette",
    "active_window",
    "compact_id",
    "display_status",
    "middle_position_window",
    "queue_window_bucket",
    "status_color",
    "truncate_ansi",
    "visible_len",
]

T = TypeVar("T")

#: One SGR escape sequence (colour / reset).  Never counted toward width.
ANSI_ESC_RE = re.compile(r"\033\[[0-9;]*m")

_BUG_ID_RE = re.compile(r"^(BUG-\d+)", re.IGNORECASE)
_PRIORITY_ID_RE = re.compile(r"^(PRIORITY-\d+)", re.IGNORECASE)
# A requirements file stem is vX.Y.Z optionally followed by any suffix (a
# bundle name or an operator-authored slug); only the vX.Y.Z part is shown.
_REQ_ID_RE = re.compile(r"^(v\d+\.\d+\.\d+)(?:-.*)?$", re.IGNORECASE)

_DISPLAY_STATUS = {
    "working": "running",
    "label": "running",
    "done": "done",
    WONT_DO_STATUS: WONT_DO_STATUS,
    "blocked": "blocked",
    "open": "open",
}


@dataclasses.dataclass(frozen=True)
class Palette:
    """The ANSI escapes a pane draws with, as the bash driver resolved them.

    ``use_color`` is False under ``NO_COLOR`` / ``TERM=dumb``; the escapes
    are then empty strings and no row is coloured.
    """

    use_color: bool
    red: str = ""
    yellow: str = ""
    green: str = ""
    white: str = ""
    dim: str = ""
    reset: str = ""


@dataclasses.dataclass(frozen=True)
class DeferCards:
    """One project's deferral posture, as the bash gather stage resolved it.

    ``mode`` is "off" | "on" | "malformed"; ``ids`` the compact IDs of the
    bugs currently deferred.  The glyph decision itself is
    ``pgai_agent_kanban.dashboard.defer_card_glyph``'s.
    """

    mode: str = "off"
    ids: frozenset = frozenset()
    glyph_deferred_bug: str = ""
    glyph_defer_warning: str = ""

    def suffix(self, cid: str, raw_status: str, use_color: bool) -> str:
        """Return the glyph suffix (" <glyph>" or "") for one bug card."""
        return defer_card_glyph_suffix(
            self.mode, cid, raw_status, self.ids,
            self.glyph_deferred_bug, self.glyph_defer_warning, use_color,
        )


#: Suffix on an open requirement whose target version exceeds a ceiling in effect.
CEILING_BLOCKED_SUFFIX = " [ceiling]"
#: Suffix on every open requirement while a ceiling in project.cfg is unreadable.
CEILING_UNREADABLE_SUFFIX = " [ceiling?]"


@dataclasses.dataclass(frozen=True)
class CeilingCards:
    """One project's version ceilings, as the bash gather stage resolved them.

    ``limits`` maps ``max_major`` / ``max_minor`` / ``max_patch`` to the
    ceiling in effect, ``None`` meaning no ceiling (``none`` in project.cfg);
    a key left out is not checked, so the default instance marks nothing.
    ``unreadable`` is True when an accessor refused a malformed value: the
    gate then treats the project as ceiling-blocked, so discovery queues
    none of its requirements, and the pane must never draw one as free to go.

    The mark is a suffix outside the row's colour, like a bug card's deferral
    glyph: the colour stays the board status ``GET /board`` reports.
    """

    limits: dict = dataclasses.field(default_factory=dict)
    unreadable: bool = False

    @property
    def active(self) -> bool:
        """True when any ceiling can mark a row."""
        return self.unreadable or bool(self.limits)

    def suffix(self, board_status: str, target_version: str) -> str:
        """Return the ceiling suffix for one requirements row ("" for none).

        Only an ``open`` requirement is marked -- the active RC is already in
        flight and a shipped one is settled. An unreadable ceiling marks every
        open requirement; otherwise one is marked when its explicit target
        version exceeds a ceiling (an ``auto`` or absent target is not).
        """
        if board_status != "open":
            return ""
        if self.unreadable:
            return CEILING_UNREADABLE_SUFFIX
        if exceeded_ceiling(target_version, self.limits):
            return CEILING_BLOCKED_SUFFIX
        return ""


def display_status(board_status: str) -> str:
    """Map a board status to the display status a pane colours by."""
    return _DISPLAY_STATUS.get(board_status, "open")


def status_color(status: str, palette: Palette) -> str:
    """Return the colour escape for a display status (white when unknown)."""
    s = (status or "").strip().lower()
    if s == "running":
        return palette.yellow
    if s in ("done", WONT_DO_STATUS):
        return palette.green
    if s == "blocked":
        return palette.red
    return palette.white


def visible_len(text: str) -> int:
    """Return *text*'s length with ANSI escapes removed."""
    return len(ANSI_ESC_RE.sub("", text))


def truncate_ansi(text: str, max_width: int, palette: Palette) -> str:
    """Truncate *text* to at most *max_width* visible characters.

    ANSI escapes are kept but not counted.  When truncation is needed the
    last visible character becomes "…", preceded by the palette's reset
    when the kept text carries a colour, so the colour does not bleed.
    """
    if visible_len(text) <= max_width:
        return text
    result = []
    vlen = 0
    i = 0
    target = max_width - 1  # one column is reserved for the ellipsis
    while i < len(text):
        m = ANSI_ESC_RE.match(text, i)
        if m:
            result.append(m.group(0))
            i = m.end()
            continue
        if vlen >= target:
            break
        result.append(text[i])
        vlen += 1
        i += 1
    if palette.use_color and any(ANSI_ESC_RE.search(s) for s in result):
        result.append(palette.reset)
    result.append("…")
    return "".join(result)


def compact_id(stem: str, is_requirements: bool) -> str:
    """Return the display ID for an intake file stem (its name without .md).

    Requirements show their vX.Y.Z; bugs and priorities their BUG-NNNN /
    PRIORITY-NNN prefix.  A stem matching neither shape is shown whole.
    """
    if is_requirements:
        m = _REQ_ID_RE.match(stem)
        return m.group(1) if m else stem
    m = _BUG_ID_RE.match(stem) or _PRIORITY_ID_RE.match(stem)
    return m.group(1) if m else stem


def queue_window_bucket(state: str) -> str:
    """Place a task state in a queue pane's window bucket.

    Returns "active" (WORKING, BACKLOG, WAITING — the centre block),
    "closed" (DONE, WONT-DO, BLOCKED — below it) or "open" (any other
    state — above it).
    """
    s = state.upper()
    if s in ("WORKING", "BACKLOG", "WAITING"):
        return "active"
    if s in ("DONE", "WONT-DO", "BLOCKED"):
        return "closed"
    return "open"


def middle_position_window(items: Sequence[T], state_of: Callable[[T], str], n_rows: int) -> list:
    """Select a queue pane's rows with the active block in the middle.

    *items* are newest first.  The active bucket takes up to *n_rows*
    rows; the remaining rows split evenly between the open bucket (above,
    floor) and the closed bucket (below, ceiling), and a bucket too small
    for its share gives the unused rows to the other.  Returns the open
    slice, then the active slice, then the closed slice; no padding.
    """
    q_open = [e for e in items if queue_window_bucket(state_of(e)) == "open"]
    q_active = [e for e in items if queue_window_bucket(state_of(e)) == "active"]
    q_closed = [e for e in items if queue_window_bucket(state_of(e)) == "closed"]

    n_active = min(len(q_active), n_rows)
    remaining = n_rows - n_active
    above_ideal = remaining // 2
    below_ideal = remaining - above_ideal

    above_avail = min(above_ideal, len(q_open))
    leftover_above = above_ideal - above_avail
    below_avail = min(below_ideal + leftover_above, len(q_closed))
    leftover_below = (below_ideal + leftover_above) - below_avail
    above_final = min(above_avail + leftover_below, len(q_open))

    return q_open[:above_final] + q_active[:n_active] + q_closed[:below_avail]


def active_window(n_items: int, rows: int, active_idx: int) -> tuple:
    """Return ``(start, end)`` of a *rows*-high window kept around *active_idx*.

    Used when *n_items* exceeds *rows*.  A "... (N more above/below)"
    marker row is drawn for each hidden side and counts against *rows*, so
    the window is sized in two passes: first assuming both markers, then
    with the markers that window actually needs.  An active item within
    three of either end pins the window to that end; otherwise the window
    is centred on it.
    """
    n, i = n_items, active_idx
    start, end = 0, 0
    for pass_no in range(2):
        markers = 2 if pass_no == 0 else (1 if start > 0 else 0) + (1 if end < n else 0)
        budget = max(1, rows - markers)
        if i < 3:
            start, end = 0, min(n, budget)
        elif i + 3 >= n:
            start, end = max(0, n - budget), n
        else:
            start = max(0, i - budget // 2)
            end = min(n, start + budget)
            if end == n:
                start = max(0, n - budget)
    return start, end
