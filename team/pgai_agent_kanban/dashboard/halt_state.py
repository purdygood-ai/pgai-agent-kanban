#!/usr/bin/env python3
"""
halt_state.py — Compute the current halt state of a kanban project root.

Exposes a single public function:

    compute_halt_state(project_root: Path) -> tuple[str, str | None]

Returns a ``(state, event)`` pair that describes whether the project is
running normally, draining toward a halt, or fully halted.

State values
------------
``'halted'``
    A ``HALT`` sentinel file is present in *project_root*.  The event string
    is read from the file body (stripped and lowercased).  Known event tokens
    are the supported halt_after tokens plus 'rc' and 'pm': rc, pm, coder,
    writer, tester, cm.  An unrecognised token passes through as the raw
    lowercased value rather than raising.  An empty or whitespace-only body
    yields ``None`` for the event.

``'draining'``
    A ``HALT-AFTER`` sentinel file is present and its body parses to a valid
    token (via :func:`pgai_agent_kanban.halt_after.token.parse_token`).  The
    event string is the parsed token.  If ``parse_token`` returns ``None``
    (invalid token) the file is treated as absent and state falls through to
    ``'normal'``.

``'normal'``
    Neither ``HALT`` nor a valid ``HALT-AFTER`` file is present.  Event is
    always ``None``.

Priority: ``HALT`` beats ``HALT-AFTER``; if both files exist, the result is
``'halted'``.

Usage (CLI)
-----------
::

    python3 -m pgai_agent_kanban.dashboard.halt_state <project_root>

Prints ``state\\tevent`` to stdout (``event`` is the string ``None`` when no
event was found).  Bash callers may use ``IFS=$'\\t' read -r state event`` to
split.

Usage (import)
--------------
::

    from pgai_agent_kanban.dashboard.halt_state import compute_halt_state
    from pgai_agent_kanban.env import resolve_kanban_root

    # $PGAI_AGENT_KANBAN_ROOT_PATH, the fleet-scope HALT's home
    state, event = compute_halt_state(resolve_kanban_root())
"""

import pathlib
import sys
from pathlib import Path

# Run by file path (``python3 .../halt_state.py``) or loaded from a file
# outside its package, this module has no parent package and the interpreter
# puts only its own directory on sys.path, so the package import below would
# not resolve. Put this file's own package root -- the directory holding the
# pgai_agent_kanban/ this file sits in -- at the front of sys.path so the
# import resolves from the same tree, never another. Imported as a package
# member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.halt_after.token import parse_token  # noqa: E402

# Supported event tokens for the HALT file body.  Unknown tokens fall through
# as-is (raw lowercased string) rather than raising.
_SUPPORTED_EVENTS = frozenset({"rc", "pm", "coder", "writer", "tester", "cm"})


def compute_halt_state(project_root: Path) -> "tuple[str, str | None]":
    """Return ``(state, event)`` reflecting the current halt state.

    Checks for ``HALT`` and ``HALT-AFTER`` sentinel files under
    *project_root* and returns a two-tuple describing the project's halt
    condition.

    Priority order:

    1. ``HALT`` present → ``('halted', event_or_None)``
    2. ``HALT-AFTER`` present with valid token → ``('draining', token)``
    3. ``HALT-AFTER`` present with invalid token → falls through to normal
    4. Neither present → ``('normal', None)``

    Args:
        project_root: Path to the project root directory.  The function
            never raises if *project_root* does not exist or contains no
            sentinel files.

    Returns:
        A ``(state, event)`` tuple where *state* is one of ``'halted'``,
        ``'draining'``, or ``'normal'``, and *event* is a lowercase string
        token or ``None``.

    Examples:
        >>> from pathlib import Path
        >>> import tempfile, os
        >>> with tempfile.TemporaryDirectory() as d:
        ...     compute_halt_state(Path(d))
        ('normal', None)
    """
    halt_path = project_root / "HALT"
    halt_after_path = project_root / "HALT-AFTER"

    # --- HALT takes priority over HALT-AFTER ---
    if halt_path.exists():
        try:
            body = halt_path.read_text(encoding="utf-8")
        except OSError:
            body = ""
        normalised = body.strip().lower()
        event: "str | None" = normalised if normalised else None
        return ("halted", event)

    # --- HALT-AFTER: delegate token parsing to the existing helper ---
    if halt_after_path.exists():
        try:
            content = halt_after_path.read_text(encoding="utf-8")
        except OSError:
            content = ""
        token = parse_token(content)
        if token is not None:
            return ("draining", token)
        # Invalid token: treat as absent, fall through to normal.

    return ("normal", None)


def halt_flag_and_text(project_root: Path, kanban_root: Path) -> "tuple[str, str]":
    """Return the dashboard's ``(HALT_FLAG, HALT_TEXT)`` pair for a project.

    The same pair ``team/scripts/dashboard/data.sh`` publishes, from
    :func:`compute_halt_state` on *project_root* with the kanban-wide
    ``HALT`` sentinel as the fallback:

    - halted                 -> ``("yes", "HALT")``
    - draining with event E  -> ``("no", "HALT-AFTER E")``
    - normal, but *kanban_root*/HALT exists -> ``("yes", "HALT")``
    - otherwise              -> ``("no", "")``

    Args:
        project_root: The project's root directory; it need not exist (a
            missing directory reads as normal, leaving only the global
            sentinel to consult).
        kanban_root:  The kanban root holding the global ``HALT`` sentinel.

    Returns:
        ``(flag, text)``: flag ``"yes"`` or ``"no"``; text ``""``,
        ``"HALT"``, or ``"HALT-AFTER <event>"``.
    """
    state, event = compute_halt_state(project_root)
    if state == "halted":
        return ("yes", "HALT")
    if state == "draining":
        return ("no", f"HALT-AFTER {event}" if event else "HALT-AFTER")
    if (kanban_root / "HALT").is_file():
        return ("yes", "HALT")
    return ("no", "")


# ---------------------------------------------------------------------------
# CLI entry point (bash callers)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <project_root>", file=sys.stderr)
        sys.exit(1)

    project_root_arg = Path(sys.argv[1])
    state, event = compute_halt_state(project_root_arg)
    # Print state TAB event (or the string 'None' for absent event).
    # Bash callers: IFS=$'\t' read -r state event <<< "$output"
    print(f"{state}\t{event}")
