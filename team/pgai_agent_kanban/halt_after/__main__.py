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
    0  — HALT-AFTER promoted to HALT (drain satisfied AND Active RC is
         "none"; project is now halted)
    1  — drain condition not yet satisfied (chain continues normally)
    2  — invalid / unrecognised token (treat as absent, removing any
         rc-gate latch; chain continues)
    3  — no HALT-AFTER file present (nothing to do; chain continues; any
         rc-gate latch left beside it is removed)
    5  — drain condition satisfied but promotion deferred because the
         project's Active RC is not "none" (chain continues normally; the
         satisfied drain is latched to HALT-AFTER.rc-gate and will promote
         at the next evaluation after RC close — see rc_gate.py)

The wake script should handle each exit code:
    0 → skip this project on this wake (HALT is now in place)
    1 → continue normal wake processing for this project
    2 → log warning from stderr, continue normal wake processing
    3 → continue normal wake processing (no sentinel)
    5 → continue normal wake processing (RC still open; deferred)

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
from .rc_gate import RcGateOutcome, clear_latch, evaluate as rc_gate_evaluate

_EXIT_PROMOTED = 0
_EXIT_DRAIN_IN_PROGRESS = 1
_EXIT_INVALID_TOKEN = 2
_EXIT_NO_HALT_AFTER = 3
_EXIT_DEFERRED_RC_OPEN = 5

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

    # A latch left beside an absent or unusable sentinel was latched against a
    # HALT-AFTER the operator has since removed: it is cleared here so it can
    # never promote on the first evaluation after a later arm.
    if not halt_after_path.exists():
        logger.debug("no HALT-AFTER file at %s", halt_after_path)
        if clear_latch(project_root):
            logger.info("HALT-AFTER is absent at %s — removed its stale rc-gate latch", halt_after_path)
        return _EXIT_NO_HALT_AFTER

    try:
        content = halt_after_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("could not read %s: %s — treating as absent", halt_after_path, exc)
        clear_latch(project_root)
        return _EXIT_INVALID_TOKEN

    event = parse_token(content)
    if event is None:
        # parse_token already logged the warning
        logger.warning(
            "invalid HALT-AFTER token in %s — chain continues normally",
            halt_after_path,
        )
        clear_latch(project_root)
        return _EXIT_INVALID_TOKEN

    logger.info("HALT-AFTER token=%r project_root=%s", event, project_root)

    # rc_gate.evaluate wants a concrete release-state directory; fall back to
    # project_root when the caller passed no second positional argument
    # (backward-compatible default — matches check_drain's own fallback).
    _release_state_root = (
        release_state_root if release_state_root is not None else project_root
    )

    try:
        result = rc_gate_evaluate(project_root, _release_state_root, event)
    except ValueError as exc:
        logger.warning("rc_gate.evaluate error: %s — treating token as absent", exc)
        return _EXIT_INVALID_TOKEN
    except OSError as exc:
        logger.error("rc_gate.evaluate failed: %s — manual intervention required", exc)
        # Still return DRAIN_IN_PROGRESS so the chain continues; the operator
        # will see the OSError in the wake log and can handle the stale state.
        return _EXIT_DRAIN_IN_PROGRESS

    if result.outcome is RcGateOutcome.NOT_DRAINED:
        logger.info(
            "drain not yet satisfied for event=%r — chain continues", result.event
        )
        return _EXIT_DRAIN_IN_PROGRESS

    if result.outcome is RcGateOutcome.DEFERRED:
        logger.info(
            "drain satisfied for event=%r but Active RC is open — promotion "
            "deferred to RC close — chain continues",
            result.event,
        )
        return _EXIT_DEFERRED_RC_OPEN

    logger.info(
        "drain satisfied for event=%r and RC is closed — promoted HALT-AFTER → HALT",
        result.event,
    )
    return _EXIT_PROMOTED


if __name__ == "__main__":
    sys.exit(main())
