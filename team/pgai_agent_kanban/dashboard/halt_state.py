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

    state, event = compute_halt_state(Path("/opt/pgai"))
"""

import sys
from pathlib import Path

from pgai_agent_kanban.halt_after.token import parse_token

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
