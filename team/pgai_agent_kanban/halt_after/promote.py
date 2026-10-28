#!/usr/bin/env python3
"""
promote.py — HALT-AFTER sentinel promotion and audit-entry writer.

Called only after the RC-close gate (``rc_gate.evaluate``) has confirmed a
drain condition is both satisfied AND the project's Active RC reads "none"
-- either from the wake loop (`team/scripts/lib/wake_project.sh`) or from
CM spending a version-pinned sentinel synchronously at ship time
(`team/scripts/lib/cm/release_finalize.sh`, Step 12). ``promote()`` itself
does not evaluate drain or RC state; it performs the mechanical steps:

  1. Remove the ``HALT-AFTER`` file at the project scope.
  2. Create a ``HALT`` file at the same scope.
  3. Append a ``## HALT Event`` audit entry to ``release-state.md``.

HALT body:

    # HALT created by HALT-AFTER promotion at 2026-05-29T01:23:45+00:00: rc (Active RC cleared)
    # halt-writer: halt-after-promotion
    # halt-reason: HALT-AFTER rc auto-promotion: rc (Active RC cleared)
    # Resume: /home/op/pgai_agent_kanban/scripts/unhalt.sh --project demo

The first line is what the HALT readers display; the ``halt-writer`` /
``halt-reason`` lines are the provenance block
(pgai_agent_kanban.overwatch.halt_provenance), which marks the HALT as the
chain's rather than an operator's. The resume line names the installed
script under the kanban root, so it runs as printed:
``<kanban root>/scripts/unhalt.sh --project <name>`` for a project, and
``<kanban root>/scripts/unhalt-global.sh`` for a promotion at the kanban
root.

Audit-entry format:

    ## HALT Event
    - Timestamp: 2026-05-29T01:23:45+00:00
    - Trigger: HALT-AFTER rc auto-promotion
    - Event drained: rc (Active RC cleared)
    - Promoted: HALT-AFTER → HALT

The Timestamp is always UTC ISO-8601.  The "Event drained" note for 'rc'
reads "Active RC cleared"; for the versioned 'rc:vX.Y.Z' family it reads
"rc (until vX.Y.Z shipped)", composed via token.describe_token so the audit
entry never drifts from what the status bar and arm confirmation say; for
per-role events it reads "no WORKING <ROLE> tasks found".

Each sentinel change -- HALT created, HALT-AFTER removed -- appends a
``promote`` record to ``<kanban_root>/logs/sentinel-writes.log`` (see
pgai_agent_kanban.lib.sentinel_provenance), so a gated suite's sentinel-leak
check attributes a promotion that lands mid-run instead of blaming the
suite. A record that cannot be appended is a stderr warning and changes
nothing else.

All filesystem operations are atomic where possible (rename for HALT creation,
direct unlink for HALT-AFTER removal) so a crash mid-promotion leaves the
system in a recoverable state (both files present → operator can re-run promote
or manually clean up).
"""

import datetime
import logging
import pathlib

from pgai_agent_kanban.lib.sentinel_provenance import (
    kanban_root_for_scope,
    record_sentinel_write,
)
from pgai_agent_kanban.overwatch.halt_provenance import (
    WRITER_HALT_AFTER_PROMOTION,
    provenance_lines,
    unhalt_command,
    unhalt_global_command,
)

from .token import describe_token

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
    """Return the human-readable note for the 'Event drained' audit field.

    The versioned ``rc:vX.Y.Z`` family names its target through
    :func:`describe_token` (``"until vX.Y.Z"``) rather than composing an
    "arm-time version" phrase locally -- the target may have been named
    directly by an operator's ``--until`` rather than captured from an
    in-flight RC, and even a captured version may be several releases
    behind the one that actually satisfied the latching ``>=`` comparison,
    so "arm-time" would misdescribe it. This is the one shared phrase the
    status bar, the arm confirmation, and this audit entry all read from
    describe_token, per team/SOP.md ("Audit entry shape").
    """
    if event == "rc":
        return "rc (Active RC cleared)"
    if event.startswith("rc:"):
        return f"rc ({describe_token(event)} shipped)"
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


def _build_halt_body(project_root: pathlib.Path, kanban_root: pathlib.Path, event: str, timestamp: str) -> str:
    """Build the HALT file body: a display header, the provenance block, and how to resume.

    Args:
        project_root: The scope the HALT is created in.
        kanban_root:  That scope's kanban root (equal to *project_root* for a
                      root-scope promotion).
        event:        The canonical event token that drained.
        timestamp:    ISO-8601 UTC timestamp string.
    """
    note = _event_drained_note(event)
    if project_root == kanban_root:
        resume = unhalt_global_command(kanban_root)
    else:
        resume = unhalt_command(kanban_root, project_root.name)
    lines = [
        f"# HALT created by HALT-AFTER promotion at {timestamp}: {note}",
        *provenance_lines(WRITER_HALT_AFTER_PROMOTION, f"HALT-AFTER {event} auto-promotion: {note}"),
        f"# Resume: {resume}",
    ]
    return "\n".join(lines) + "\n"


def promote(project_root: "str | pathlib.Path", event: str) -> None:
    """Promote a HALT-AFTER sentinel to HALT and write an audit entry.

    Steps:
      1. Create the ``HALT`` file at *project_root*, carrying the body the
         module docstring shows.  A HALT already present is left exactly
         as it is.
      2. Remove the ``HALT-AFTER`` file from *project_root*.
      3. Append a ``## HALT Event`` block to *project_root*/release-state.md.

    Steps 1 and 2 each append a ``promote`` provenance record when they
    change the sentinel (a HALT already present is left as it was and not
    recorded). Recording never raises.

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

    kanban_root = kanban_root_for_scope(project_root)

    timestamp = _utc_now_iso()

    # Step 1 — create HALT with its provenance body. Exclusive create: a
    # HALT that is already there (an operator's, or CM's) is never
    # overwritten, even one that appears between a check and the write.
    body = _build_halt_body(project_root, pathlib.Path(kanban_root), event, timestamp)
    try:
        with halt_path.open("x", encoding="utf-8") as fh:
            fh.write(body)
    except FileExistsError:
        logger.info("promote: %s already present; left as it was", halt_path)
    else:
        logger.info("promote: created %s", halt_path)
        record_sentinel_write(kanban_root, halt_path, "promote")

    # Step 2 — remove HALT-AFTER
    if halt_after_path.exists():
        halt_after_path.unlink()
        logger.info("promote: removed %s", halt_after_path)
        record_sentinel_write(kanban_root, halt_after_path, "promote")
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
