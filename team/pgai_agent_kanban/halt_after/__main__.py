#!/usr/bin/env python3
"""
__main__.py — CLI entry point for the halt_after module.

Usage:
    python3 -m halt_after <scope_root> [<release_state_root>]

*scope_root* is the directory that contains (or will contain) the HALT-AFTER
and HALT sentinel files.  The module reads ``<scope_root>/HALT-AFTER``,
evaluates the drain condition, and either promotes (HALT-AFTER → HALT) or
exits with a code that tells the bash wake script what happened.

*release_state_root* is an optional second positional argument.  When
provided, ``release-state.md`` is read from this directory instead of
*scope_root*.  Use this when the HALT-AFTER sentinel lives at a different
scope than the project (for example, a root-scope HALT-AFTER at
``$KANBAN_ROOT`` while ``release-state.md`` is at
``$KANBAN_ROOT/projects/<name>/release-state.md``).  When omitted,
*scope_root* is used for both the sentinel files and ``release-state.md``
(backward-compatible default).

Exit codes:
    0  — HALT-AFTER promoted to HALT (drain satisfied; project is now halted)
    1  — drain condition not yet satisfied (chain continues normally)
    2  — invalid / unrecognised token (treat as absent; chain continues)
    3  — no HALT-AFTER file present (nothing to do; chain continues)

The wake script should handle each exit code:
    0 → skip this project on this wake (HALT is now in place)
    1 → continue normal wake processing for this project
    2 → log warning from stderr, continue normal wake processing
    3 → continue normal wake processing (no sentinel)

Logging goes to stderr so the bash caller can capture it separately from the
exit code if needed.
"""

import logging
import pathlib
import sys

# Configure logging to stderr so bash callers can redirect as needed
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="halt_after: %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

from .token import parse_token
from .drain import check_drain
from .promote import promote

_EXIT_PROMOTED = 0
_EXIT_DRAIN_IN_PROGRESS = 1
_EXIT_INVALID_TOKEN = 2
_EXIT_NO_HALT_AFTER = 3

_HALT_AFTER_FILENAME = "HALT-AFTER"


def main() -> int:
    """Run the HALT-AFTER check for a given scope root.

    Returns the exit code (see module docstring for meanings).
    """
    if len(sys.argv) < 2 or len(sys.argv) > 3:
        print(
            "Usage: python3 -m halt_after <scope_root> [<release_state_root>]",
            file=sys.stderr,
        )
        return 1  # treat as drain-in-progress to avoid spurious halts

    project_root = pathlib.Path(sys.argv[1])
    # Optional second arg: directory containing release-state.md.
    # Falls back to project_root when not provided (backward-compatible).
    release_state_root: "pathlib.Path | None" = (
        pathlib.Path(sys.argv[2]) if len(sys.argv) == 3 else None
    )

    halt_after_path = project_root / _HALT_AFTER_FILENAME

    if not halt_after_path.exists():
        logger.debug("no HALT-AFTER file at %s", halt_after_path)
        return _EXIT_NO_HALT_AFTER

    try:
        content = halt_after_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("could not read %s: %s — treating as absent", halt_after_path, exc)
        return _EXIT_INVALID_TOKEN

    event = parse_token(content)
    if event is None:
        # parse_token already logged the warning
        logger.warning(
            "invalid HALT-AFTER token in %s — chain continues normally",
            halt_after_path,
        )
        return _EXIT_INVALID_TOKEN

    logger.info("HALT-AFTER token=%r project_root=%s", event, project_root)

    try:
        drained = check_drain(event, project_root, release_state_root)
    except ValueError as exc:
        logger.warning("check_drain error: %s — treating token as absent", exc)
        return _EXIT_INVALID_TOKEN

    if not drained:
        logger.info(
            "drain not yet satisfied for event=%r — chain continues", event
        )
        return _EXIT_DRAIN_IN_PROGRESS

    logger.info(
        "drain satisfied for event=%r — promoting HALT-AFTER → HALT", event
    )
    try:
        promote(project_root, event)
    except OSError as exc:
        logger.error("promote failed: %s — manual intervention required", exc)
        # Still return DRAIN_IN_PROGRESS so the chain continues; the operator
        # will see the OSError in the wake log and can handle the stale state.
        return _EXIT_DRAIN_IN_PROGRESS

    return _EXIT_PROMOTED


if __name__ == "__main__":
    sys.exit(main())
