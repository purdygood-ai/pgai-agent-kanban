#!/usr/bin/env python3
"""
write_rc_state.py — Write or update a per-RC release-state JSON file.

This module is an as-is extraction of two inline Python heredocs previously
embedded in the cm shell scripts:

  - team/scripts/cm/open-rc.sh   (lines ~340-349): write a fresh JSON record
  - team/scripts/cm/cancel-rc.sh (lines ~369-384): merge cancelled state into
    an existing JSON record (or create a minimal one if the file is absent)

The JSON payload behavior is preserved byte-for-byte — do NOT change key names,
sort_keys, indent, or trailing-newline handling without filing a separate task.

Usage (CLI):
    python3 write_rc_state.py open <rc> <opened_at>
    python3 write_rc_state.py ship <path> <rc> <closed_at>
    python3 write_rc_state.py cancel <path> <rc> <closed_at>
    python3 write_rc_state.py archive-cancelled <path> <rc> <closed_at>

Usage (import):
    from pgai_agent_kanban.cm.write_rc_state import write_open, write_ship, write_cancel, archive_cancelled
    write_open("v1.8.0", "2026-07-07T12:00:00Z")
    write_ship("/path/to/v1.8.0.json", "v1.8.0", "2026-07-07T13:00:00Z")
    write_cancel("/path/to/v1.8.0.json", "v1.8.0", "2026-07-07T13:00:00Z")
    archive_cancelled("/path/to/v1.8.0.json", "v1.8.0", "2026-07-07T13:00:00Z")
"""

import argparse
import json
import os
import shutil
import sys


def write_open(rc: str, opened_at: str) -> None:
    """Write a fresh per-RC release-state JSON to stdout.

    Produces a JSON object with keys: closed_at, opened_at, outcome, rc
    (alphabetical order due to sort_keys=True), followed by a trailing newline.
    The caller is responsible for redirecting stdout to the target file, exactly
    as open-rc.sh does today.

    This is an as-is extraction of the heredoc in team/scripts/cm/open-rc.sh.
    Do NOT change key names or output format.

    Args:
        rc:        The RC version string (e.g. "v1.8.0").
        opened_at: ISO8601 UTC timestamp string (e.g. "2026-07-07T12:00:00Z").
    """
    payload = {
        'rc': rc,
        'opened_at': opened_at,
        'closed_at': None,
        'outcome': 'in_progress',
    }
    sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + '\n')


def write_ship(path: str, rc: str, closed_at: str) -> None:
    """Update an existing per-RC release-state JSON with shipped outcome.

    Reads the JSON file at *path* (or starts from an empty dict if the file is
    missing or corrupt), merges in the shipped state, and writes the result
    back in-place.  Prints a status message to stdout on success.

    Key merging rules (mirrors write_cancel, differing only in outcome value):
      - state.setdefault('rc', rc) — rc is only set when not already present
      - opened_at is preserved when present; not added when absent
      - closed_at and outcome are always overwritten
      - outcome is set to 'shipped' (not 'cancelled')

    Args:
        path:      Path to the JSON state file to read and update in-place.
        rc:        The RC version string (e.g. "v1.8.0").
        closed_at: ISO8601 UTC timestamp string for the ship time.
    """
    try:
        state = json.loads(open(path).read()) if os.path.exists(path) else {}
    except (json.JSONDecodeError, OSError):
        state = {}
    state.setdefault('rc', rc)
    # opened_at is preserved when present; not added when absent (no error on missing key)
    state['closed_at'] = closed_at
    state['outcome'] = 'shipped'
    open(path, 'w').write(json.dumps(state, indent=2, sort_keys=True) + '\n')
    print('  Per-RC release-state JSON updated (shipped): ' + path)


def write_cancel(path: str, rc: str, closed_at: str) -> None:
    """Update an existing per-RC release-state JSON with cancelled outcome.

    Reads the JSON file at *path* (or starts from an empty dict if the file is
    missing or corrupt), merges in the cancelled state, and writes the result
    back in-place.  Prints a status message to stdout on success.

    Key merging rules (preserved from cancel-rc.sh heredoc):
      - state.setdefault('rc', rc) — rc is only set when not already present
      - opened_at is preserved when present; not added when absent
      - closed_at and outcome are always overwritten

    This is an as-is extraction of the heredoc in team/scripts/cm/cancel-rc.sh.
    Do NOT change key names, merge logic, or output format.

    Args:
        path:      Path to the JSON state file to read and update in-place.
        rc:        The RC version string (e.g. "v1.8.0").
        closed_at: ISO8601 UTC timestamp string for the cancellation time.
    """
    try:
        state = json.loads(open(path).read()) if os.path.exists(path) else {}
    except (json.JSONDecodeError, OSError):
        state = {}
    state.setdefault('rc', rc)
    # opened_at is preserved when present; not added when absent (no error on missing key)
    state['closed_at'] = closed_at
    state['outcome'] = 'cancelled'
    open(path, 'w').write(json.dumps(state, indent=2, sort_keys=True) + '\n')
    print('  Per-RC release-state JSON updated (cancelled): ' + path)


def archive_cancelled(path: str, rc: str, closed_at: str) -> str:
    """Archive a cancelled per-RC JSON to history/ and free the version slot.

    Moves a cancelled per-RC JSON file from *path* (e.g. release-state/vX.Y.Z.json)
    into a history/ subdirectory as release-state/history/vX.Y.Z-cancelled-TIMESTAMP.json,
    preserving the complete audit record.  The original slot (*path*) is then
    removed so that a subsequent open-rc on the same version number finds no
    blocking JSON.

    If the file at *path* does not exist or is already archived, the function is
    a no-op (idempotent).  If the file exists but has an outcome other than
    'cancelled', it is NOT moved — only cancelled records are archived by this
    function.

    Args:
        path:      Path to the active per-RC JSON file (e.g. .../release-state/v1.8.0.json).
        rc:        The RC version string (e.g. "v1.8.0").
        closed_at: ISO8601 UTC timestamp string for the cancellation time
                   (used to disambiguate multiple cancel/reopen cycles; derived
                   from the cancelled_at field in the JSON if present).

    Returns:
        str: path to the archive file, or empty string if nothing was moved.
    """
    if not os.path.exists(path):
        return ''

    try:
        state = json.loads(open(path).read())
    except (json.JSONDecodeError, OSError):
        state = {}

    # Only archive records that are actually cancelled; skip all others.
    if state.get('outcome') != 'cancelled':
        return ''

    # Build the archive path: history/vX.Y.Z-cancelled-TIMESTAMP.json
    # Timestamp is derived from closed_at (already set by write_cancel).
    state_dir = os.path.dirname(path)
    history_dir = os.path.join(state_dir, 'history')
    os.makedirs(history_dir, exist_ok=True)

    # Sanitise the timestamp for use in a filename (replace colons).
    ts = closed_at.replace(':', '-').replace('T', '-')
    base_name = f'{rc}-cancelled-{ts}'
    archive_name = f'{base_name}.json'
    archive_path = os.path.join(history_dir, archive_name)

    # Handle collision: multiple cancel cycles in the same second produce the
    # same timestamp.  Append a counter suffix to disambiguate.
    counter = 1
    while os.path.exists(archive_path):
        archive_name = f'{base_name}-{counter}.json'
        archive_path = os.path.join(history_dir, archive_name)
        counter += 1

    shutil.copy2(path, archive_path)
    os.remove(path)
    print(f'  Per-RC history archived: {archive_path}')
    return archive_path


def promote_cancelled_to_history(path: str) -> str:
    """If an existing per-RC JSON at *path* is cancelled, archive it and return the archive path.

    Convenience wrapper used by open-rc.sh before writing a fresh record: if the
    slot is already cancelled (from a prior attempt), archive it first so the new
    open does not overwrite the audit trail.

    Returns the archive path if something was archived, or '' if nothing needed archiving.
    If the JSON has outcome != 'cancelled' the function does nothing and returns '' —
    the caller must decide whether to error out on a non-cancelled incumbent.
    """
    if not os.path.exists(path):
        return ''

    try:
        state = json.loads(open(path).read())
    except (json.JSONDecodeError, OSError):
        return ''

    if state.get('outcome') != 'cancelled':
        return ''

    # Derive timestamp from closed_at in the record; fall back to rc value.
    closed_at = state.get('closed_at') or 'unknown'
    rc = state.get('rc', os.path.splitext(os.path.basename(path))[0])

    state_dir = os.path.dirname(path)
    history_dir = os.path.join(state_dir, 'history')
    os.makedirs(history_dir, exist_ok=True)

    ts = str(closed_at).replace(':', '-').replace('T', '-')
    base_name = f'{rc}-cancelled-{ts}'
    archive_name = f'{base_name}.json'
    archive_path = os.path.join(history_dir, archive_name)

    # Handle collision: multiple cancel cycles in the same second produce the
    # same timestamp.  Append a counter suffix to disambiguate.
    counter = 1
    while os.path.exists(archive_path):
        archive_name = f'{base_name}-{counter}.json'
        archive_path = os.path.join(history_dir, archive_name)
        counter += 1

    shutil.copy2(path, archive_path)
    os.remove(path)
    print(f'  Prior cancelled RC archived before re-open: {archive_path}')
    return archive_path


def main() -> None:
    """CLI entry point: dispatch to write_open, write_ship, write_cancel, or archive-cancelled."""
    parser = argparse.ArgumentParser(
        description=(
            "Write or update a per-RC release-state JSON file. "
            "Use the 'open' subcommand to create a fresh record (stdout), "
            "the 'ship' subcommand to merge a shipped outcome in-place, "
            "the 'cancel' subcommand to merge a cancelled outcome in-place, "
            "or the 'archive-cancelled' subcommand to move a cancelled JSON "
            "to history/ and free the version slot."
        ),
    )
    subparsers = parser.add_subparsers(dest='command', required=True)

    # open subcommand
    p_open = subparsers.add_parser(
        'open',
        help=(
            "Write a fresh per-RC release-state JSON to stdout. "
            "Redirect stdout to the target file (as open-rc.sh does)."
        ),
    )
    p_open.add_argument(
        'rc',
        help="The RC version string (e.g. 'v1.8.0').",
    )
    p_open.add_argument(
        'opened_at',
        help="ISO8601 UTC timestamp string (e.g. '2026-07-07T12:00:00Z').",
    )

    # ship subcommand
    p_ship = subparsers.add_parser(
        'ship',
        help=(
            "Merge a shipped outcome into an existing per-RC release-state "
            "JSON file, updating it in-place."
        ),
    )
    p_ship.add_argument(
        'path',
        help="Path to the JSON state file to read and update in-place.",
    )
    p_ship.add_argument(
        'rc',
        help="The RC version string (e.g. 'v1.8.0').",
    )
    p_ship.add_argument(
        'closed_at',
        help="ISO8601 UTC timestamp string for the ship time.",
    )

    # cancel subcommand
    p_cancel = subparsers.add_parser(
        'cancel',
        help=(
            "Merge a cancelled outcome into an existing per-RC release-state "
            "JSON file, updating it in-place."
        ),
    )
    p_cancel.add_argument(
        'path',
        help="Path to the JSON state file to read and update in-place.",
    )
    p_cancel.add_argument(
        'rc',
        help="The RC version string (e.g. 'v1.8.0').",
    )
    p_cancel.add_argument(
        'closed_at',
        help="ISO8601 UTC timestamp string for the cancellation time.",
    )

    # archive-cancelled subcommand
    p_archive = subparsers.add_parser(
        'archive-cancelled',
        help=(
            "Move a cancelled per-RC JSON from the active slot to history/, "
            "freeing the version number for re-use. Idempotent when the file "
            "is absent or not cancelled."
        ),
    )
    p_archive.add_argument(
        'path',
        help="Path to the active per-RC JSON file (e.g. release-state/v1.8.0.json).",
    )
    p_archive.add_argument(
        'rc',
        help="The RC version string (e.g. 'v1.8.0').",
    )
    p_archive.add_argument(
        'closed_at',
        help="ISO8601 UTC timestamp string used to name the archive file.",
    )

    args = parser.parse_args()
    if args.command == 'open':
        write_open(args.rc, args.opened_at)
    elif args.command == 'ship':
        write_ship(args.path, args.rc, args.closed_at)
    elif args.command == 'cancel':
        write_cancel(args.path, args.rc, args.closed_at)
    elif args.command == 'archive-cancelled':
        archive_cancelled(args.path, args.rc, args.closed_at)


if __name__ == '__main__':
    main()
