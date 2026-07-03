#!/usr/bin/env python3
"""
promote.py — HALT-AFTER sentinel promotion and audit-entry writer.

When the drain condition for a given event is satisfied, the wake script calls
``promote()`` to:

  1. Remove the ``HALT-AFTER`` file at the project scope.
  2. Create a ``HALT`` file at the same scope.
  3. Append a ``## HALT Event`` audit entry to ``release-state.md``.

Audit-entry format:

    ## HALT Event
    - Timestamp: 2026-05-29T01:23:45+00:00
    - Trigger: HALT-AFTER rc auto-promotion
    - Event drained: rc (Active RC cleared after v0.39.6 shipped)
    - Promoted: HALT-AFTER → HALT

The Timestamp is always UTC ISO-8601.  The "Event drained" note for 'rc' reads
"Active RC cleared" if release-state.md now shows Active RC = none; for
per-role events it reads "no WORKING <ROLE> tasks found".

All filesystem operations are atomic where possible (rename for HALT creation,
direct unlink for HALT-AFTER removal) so a crash mid-promotion leaves the
system in a recoverable state (both files present → operator can re-run promote
or manually clean up).
"""

import datetime
import logging
import pathlib

logger = logging.getLogger(__name__)

_HALT_AFTER_FILENAME = "HALT-AFTER"
_HALT_FILENAME = "HALT"
_RELEASE_STATE_FILENAME = "release-state.md"


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string with timezone offset."""
    return datetime.datetime.now(tz=datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00"
    )


def _event_drained_note(event: str) -> str:
    """Return the human-readable note for the 'Event drained' audit field."""
    if event == "rc":
        return "rc (Active RC cleared)"
    if event.startswith("rc:"):
        ver = event[3:]  # strip "rc:" prefix to get the version tag
        return f"rc (arm-time version {ver} shipped)"
    return f"{event} (no WORKING {event.upper()} tasks found)"


def _build_audit_entry(event: str, timestamp: str) -> str:
    """Build the audit-entry block to append to release-state.md.

    Args:
        event:     The canonical event token (e.g. 'rc', 'coder').
        timestamp: ISO-8601 UTC timestamp string.

    Returns:
        A string starting with a blank line (to separate from prior content),
        followed by the ``## HALT Event`` heading and four bullet fields.
    """
    note = _event_drained_note(event)
    return (
        "\n"
        "## HALT Event\n"
        f"- Timestamp: {timestamp}\n"
        f"- Trigger: HALT-AFTER {event} auto-promotion\n"
        f"- Event drained: {note}\n"
        "- Promoted: HALT-AFTER → HALT\n"
    )


def promote(project_root: "str | pathlib.Path", event: str) -> None:
    """Promote a HALT-AFTER sentinel to HALT and write an audit entry.

    Steps:
      1. Create the ``HALT`` file at *project_root* (empty file).
      2. Remove the ``HALT-AFTER`` file from *project_root*.
      3. Append a ``## HALT Event`` block to *project_root*/release-state.md.

    HALT is created before HALT-AFTER is removed so that if this process is
    interrupted mid-way the project is still in a stopped state (both HALT and
    HALT-AFTER present is safe — the wake script will halt on HALT).

    Args:
        project_root: Filesystem path to the project root.
        event:        The canonical event token (e.g. 'rc', 'coder').  Used
                      only to compose the audit entry — drain must already have
                      been verified by the caller.

    Raises:
        OSError: if the HALT file cannot be created or HALT-AFTER cannot be
                 removed.  The caller should handle and log this.
    """
    project_root = pathlib.Path(project_root)
    halt_after_path = project_root / _HALT_AFTER_FILENAME
    halt_path = project_root / _HALT_FILENAME
    release_state_path = project_root / _RELEASE_STATE_FILENAME

    timestamp = _utc_now_iso()

    # Step 1 — create HALT (empty sentinel)
    halt_path.touch()
    logger.info("promote: created %s", halt_path)

    # Step 2 — remove HALT-AFTER
    if halt_after_path.exists():
        halt_after_path.unlink()
        logger.info("promote: removed %s", halt_after_path)
    else:
        logger.warning(
            "promote: HALT-AFTER not found at %s; continuing with audit entry",
            halt_after_path,
        )

    # Step 3 — append audit entry to release-state.md
    entry = _build_audit_entry(event, timestamp)
    if release_state_path.exists():
        with release_state_path.open("a", encoding="utf-8") as fh:
            fh.write(entry)
        logger.info("promote: audit entry appended to %s", release_state_path)
    else:
        # release-state.md may not exist for some project configurations;
        # log a warning but do not fail — the HALT file is the critical output
        logger.warning(
            "promote: release-state.md not found at %s; audit entry not written",
            release_state_path,
        )

    logger.info(
        "promote: HALT-AFTER %s auto-promotion complete at %s (ts=%s)",
        event,
        project_root,
        timestamp,
    )
