#!/usr/bin/env python3
"""
write_rc_state.py — Write or update a per-RC release-state JSON file.

Two record writers carry the core JSON shape:

  - write_open:   write a fresh JSON record when an RC opens
  - write_cancel: merge cancelled state into an existing JSON record (or
    create a minimal one if the file is absent)

The JSON payload shape is a contract — do NOT change key names, sort_keys,
indent, or trailing-newline handling without filing a separate task.

write_open_with_attempt is a sibling of write_open, adding the
retry-counter tracking keys (attempt, family_key, cargo) computed by
pgai_agent_kanban.cm.bundle_family. It does not replace write_open — both
exist so any caller not opted into family tracking is unaffected.

The project-scoped release-state.md markdown file has one writer here too:
update_release_state_fields (CLI subcommand ``release-state``). It is the only
path open-rc.sh, release_finalize.sh and cancel-rc.sh use to change that file.
It edits the four schema fields — Active RC, RC Opened At, RC Opened By Task,
Last Released — in place and leaves every other line of the file byte-for-byte
as it was, so blocks appended after the fields (``## HALT Event``,
``## Operator Override``, ``## Campaign Spend Event``,
``## OVERWATCH Decision``, ``## Auto-Resolve Event``, ...) survive every RC
open, release and cancel. The write is atomic: the new content goes to a
temporary file in the same directory, which is then renamed over the target.

append_auto_ship_event (CLI subcommand ``auto-ship-event``) appends one
``## Auto-Ship Event`` block to the end of release-state.md: the record of a
release that shipped under ``ship_posture = always`` where the ship-policy
matrix would have stopped. It carries the timestamp, the version, the trigger
number and error key of the stop that did not happen, the ship posture, the
TESTER task and, optionally, the bug filed for the pattern. Like
``## Operator Override`` it is a record only: nothing reads it back to
decide a later release. Every existing byte of the file is kept; each call
adds one more block, so earlier events are never overwritten. The append is
atomic in the same way as the field writer.

Usage (CLI):
    python3 write_rc_state.py open <rc> <opened_at>
    python3 write_rc_state.py open-with-attempt <rc> <opened_at> <attempt> <family_key> <cargo_json>
    python3 write_rc_state.py ship <path> <rc> <closed_at>
    python3 write_rc_state.py cancel <path> <rc> <closed_at>
    python3 write_rc_state.py archive-cancelled <path> <rc> <closed_at>
    python3 write_rc_state.py release-state <path> [--active-rc V] [--rc-opened-at V]
                                            [--rc-opened-by-task V] [--last-released V]
    python3 write_rc_state.py auto-ship-event <path> --version V --trigger N
                                              --error-key KEY --ship-posture P
                                              --tester-task ID [--filed-bug NAME]
                                              [--timestamp TS]

Usage (import):
    from pgai_agent_kanban.cm.write_rc_state import write_open, write_open_with_attempt, write_ship, write_cancel, archive_cancelled
    write_open("v1.8.0", "2026-07-07T12:00:00Z")
    write_open_with_attempt("v2.0.1", "2026-07-27T12:00:00Z", 2, "family-abc123", ["BUG-NNNN-foo.md"])
    write_ship("/path/to/v1.8.0.json", "v1.8.0", "2026-07-07T13:00:00Z")
    write_cancel("/path/to/v1.8.0.json", "v1.8.0", "2026-07-07T13:00:00Z")
    archive_cancelled("/path/to/v1.8.0.json", "v1.8.0", "2026-07-07T13:00:00Z")
    update_release_state_fields("/path/to/release-state.md",
                                {"Active RC": "none", "Last Released": "v1.8.0"})
    append_auto_ship_event("/path/to/release-state.md", version="v1.8.0",
                           trigger=2, error_key="cm-release.trigger-2.systemic-risk-high",
                           ship_posture="always", tester_task="TESTER-example-verify")
"""

import argparse
import datetime
import json
import os
import re
import shutil
import sys
import tempfile


def write_open(rc: str, opened_at: str) -> None:
    """Write a fresh per-RC release-state JSON to stdout.

    Produces a JSON object with keys: closed_at, opened_at, outcome, rc
    (alphabetical order due to sort_keys=True), followed by a trailing newline.
    The caller is responsible for redirecting stdout to the target file, as
    open-rc.sh does.

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


def write_open_with_attempt(
    rc: str,
    opened_at: str,
    attempt: int,
    family_key,
    cargo,
) -> None:
    """Write a fresh per-RC release-state JSON, including the
    retry-counter fields, to stdout.

    Produces the same base shape as write_open() (rc, opened_at, closed_at,
    outcome) plus three additional keys carried forward by write_cancel /
    write_ship's setdefault-and-preserve merge (both functions only
    overwrite closed_at and outcome, so these three keys survive a later
    cancel or ship untouched):

      - attempt:     the retry attempt number within the bundle family (see
                     pgai_agent_kanban.cm.bundle_family.compute_bundle_family)
      - family_key:  the family identity string, or None when the RC has no
                     trackable cargo (e.g. operator-authored requirements
                     with no `## Bundled Items` section)
      - cargo:       the sorted, de-duplicated list of bundled-item
                     basenames that make up the family (empty list when
                     family_key is None)

    write_open() itself is intentionally untouched — its docstring's
    "do NOT change key names or output format" contract still holds for
    existing callers. This is the function team/scripts/cm/open-rc.sh calls
    once it has computed the family/attempt facts via bundle_family.py.

    Args:
        rc:         The RC version string (e.g. "v2.0.1").
        opened_at:  ISO8601 UTC timestamp string.
        attempt:    The attempt number within the bundle family (>= 1).
        family_key: The family identity string, or None.
        cargo:      Sorted list of bundled-item basenames, or [].
    """
    payload = {
        'rc': rc,
        'opened_at': opened_at,
        'closed_at': None,
        'outcome': 'in_progress',
        'attempt': attempt,
        'family_key': family_key,
        'cargo': cargo,
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


def write_cancel(path: str, rc: str, closed_at: str, say=None) -> None:
    """Update an existing per-RC release-state JSON with cancelled outcome.

    Reads the JSON file at *path* (or starts from an empty dict if the file is
    missing or corrupt), merges in the cancelled state, and writes the result
    back in-place.  Prints a status message to stdout on success, or hands it
    to *say* (one line, no trailing newline) when given.

    Key merging rules:
      - state.setdefault('rc', rc) — rc is only set when not already present
      - opened_at is preserved when present; not added when absent
      - closed_at and outcome are always overwritten

    Do NOT change key names, merge logic, or output format.

    Args:
        path:      Path to the JSON state file to read and update in-place.
        rc:        The RC version string (e.g. "v1.8.0").
        closed_at: ISO8601 UTC timestamp string for the cancellation time.
        say:       Receives the status line instead of stdout (default: print).
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
    (say or print)('  Per-RC release-state JSON updated (cancelled): ' + path)


def archive_cancelled(path: str, rc: str, closed_at: str, say=None) -> str:
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
        say:       Receives the "archived" status line instead of stdout
                   (default: print).

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
    (say or print)(f'  Per-RC history archived: {archive_path}')
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


# ---------------------------------------------------------------------------
# release-state.md — in-place field writer
# ---------------------------------------------------------------------------

# The four schema fields, in the order a freshly written file lays them out.
# Kept in step with RELEASE_STATE_SCHEMA in read_state_field.py (the loader);
# this module stays importable as a bare script, so it names them itself.
RELEASE_STATE_FIELDS = (
    'Active RC',
    'RC Opened At',
    'RC Opened By Task',
    'Last Released',
)

# Retired field headings an older file may still carry among the schema
# fields. They count as part of the field region (never as an appended
# block) and are left untouched.
_RETIRED_RELEASE_STATE_FIELDS = (
    'Last Released At',
    'Last Released By Task',
)

# Title line written when the file is created from nothing.
_RELEASE_STATE_TITLE = '# Release State\n'

# Liberal on read: any run of whitespace after '##', trailing whitespace ok.
_LEVEL2_HEADING_RE = re.compile(r'^##\s+(.*?)\s*$')
# Any heading of level two or deeper ends a field's section.
_SECTION_BOUNDARY_RE = re.compile(r'^\s*##')
_HORIZONTAL_RULE_RE = re.compile(r'^\s*-{3,}\s*$')
_HTML_COMMENT_OPEN = '<!--'
_HTML_COMMENT_CLOSE = '-->'


def _heading_name(line: str):
    """Return the heading text of a '## ...' line, or None for any other line."""
    match = _LEVEL2_HEADING_RE.match(line.rstrip('\r\n'))
    return match.group(1) if match else None


def _field_region_end(lines: list) -> int:
    """Return the index where the field region ends: the first '##' heading
    that is neither a schema field nor a retired field (the start of the
    appended blocks), or len(lines) when there is none.

    Schema headings are only looked for inside this region, so a heading-
    shaped line inside an appended event (an override's free-text reason,
    say) is never mistaken for a field.
    """
    known = set(RELEASE_STATE_FIELDS) | set(_RETIRED_RELEASE_STATE_FIELDS)
    for idx, line in enumerate(lines):
        if _SECTION_BOUNDARY_RE.match(line) and _heading_name(line) not in known:
            return idx
    return len(lines)


def _heading_index(lines: list, heading: str, end: int):
    """Return the index of the first '## <heading>' line in lines[:end], or None.

    Matching is whitespace-tolerant ('##   Active RC  ' matches); the caller
    rewrites a matched line to the canonical '## <heading>' form so the
    loader, which compares the stripped line exactly, finds it afterwards.
    """
    for idx in range(min(end, len(lines))):
        if _heading_name(lines[idx]) == heading:
            return idx
    return None


def _value_index(lines: list, heading_idx: int):
    """Return the index of the value line under the heading at *heading_idx*.

    The value is the first line after the heading that is not blank, not a
    '#' comment, and not inside an HTML comment block — the same line the
    loader reads. Scanning stops at the next '##' heading or a horizontal
    rule, so a value is never taken from a neighbouring block. Returns None
    when the section holds no value line.
    """
    in_html_comment = False
    for idx in range(heading_idx + 1, len(lines)):
        text = lines[idx].strip()
        if in_html_comment:
            if _HTML_COMMENT_CLOSE in text:
                in_html_comment = False
            continue
        if _SECTION_BOUNDARY_RE.match(text) or _HORIZONTAL_RULE_RE.match(text):
            return None
        if not text or text.startswith('#'):
            continue
        if text.startswith(_HTML_COMMENT_OPEN):
            in_html_comment = _HTML_COMMENT_CLOSE not in text
            continue
        return idx
    return None


def _next_boundary_index(lines: list, start: int) -> int:
    """Return the index of the first '##' heading at or after *start*, else len(lines)."""
    for idx in range(start, len(lines)):
        if _SECTION_BOUNDARY_RE.match(lines[idx]):
            return idx
    return len(lines)


def render_release_state(text: str, updates: dict) -> str:
    """Return *text* with the named release-state fields set to new values.

    Only the four schema fields may be named. For each one named in
    *updates*:

      - heading present with a value line: that one line is replaced;
      - heading present with no value line: the value is inserted directly
        beneath the heading;
      - heading absent: a '## <heading>' section is inserted after the
        preceding schema field's section (or ahead of the first '##' block
        when no earlier schema field exists), so the fields stay grouped
        above any appended blocks.

    Schema headings are matched only in the field region above the first
    appended block, never inside an event's own text.

    Every other line — the title, blank lines, comments and every appended
    block — is carried over unchanged. Fields not named in *updates* are left
    exactly as they are. An empty *text* starts from the '# Release State'
    title.

    Raises:
        ValueError: when *updates* names a field outside the schema, or a
            value is empty or spans more than one line (either would leave
            a file the loader refuses).
    """
    unknown = sorted(set(updates) - set(RELEASE_STATE_FIELDS))
    if unknown:
        raise ValueError(
            f'not release-state fields: {", ".join(unknown)} '
            f'(accepted: {", ".join(RELEASE_STATE_FIELDS)})'
        )
    for heading, value in updates.items():
        if not isinstance(value, str) or not value.strip() or '\n' in value or '\r' in value:
            raise ValueError(
                f"release-state field '{heading}' needs a single-line, non-empty "
                f"value (got {value!r}); use 'none' for an unset field"
            )

    if not text:
        text = _RELEASE_STATE_TITLE
    lines = text.splitlines(keepends=True)
    if not lines[-1].endswith('\n'):
        lines[-1] += '\n'

    # Index just past the most recent schema field's section content; a
    # missing field is inserted at the next '##' boundary from here.
    anchor = 0
    for heading in RELEASE_STATE_FIELDS:
        new_value = updates.get(heading)
        heading_idx = _heading_index(lines, heading, _field_region_end(lines))

        if heading_idx is None:
            if new_value is None:
                continue
            insert_at = _next_boundary_index(lines, anchor)
            if insert_at < len(lines):
                block = [f'## {heading}\n', f'{new_value.strip()}\n', '\n']
            else:
                block = [] if lines[-1].strip() == '' else ['\n']
                block += [f'## {heading}\n', f'{new_value.strip()}\n']
            lines[insert_at:insert_at] = block
            anchor = insert_at + len(block)
            continue

        if new_value is not None:
            lines[heading_idx] = f'## {heading}\n'
        value_idx = _value_index(lines, heading_idx)
        if new_value is not None:
            if value_idx is None:
                lines.insert(heading_idx + 1, f'{new_value.strip()}\n')
                value_idx = heading_idx + 1
            else:
                lines[value_idx] = f'{new_value.strip()}\n'
        anchor = (value_idx if value_idx is not None else heading_idx) + 1

    return ''.join(lines)


def update_release_state_fields(path: str, updates: dict) -> None:
    """Set release-state fields in the markdown file at *path*, in place, atomically.

    Reads the file (a missing file starts empty), applies
    render_release_state, writes the result to a temporary file in the same
    directory and renames it over *path*, so a reader sees either the old
    file or the new one, never a partial write. A symlinked *path* is
    resolved first so the link is kept and its target updated. The file's
    existing permission bits are kept; a new file gets 0644 less the umask.

    Args:
        path:    Path to release-state.md.
        updates: Mapping of field heading (one of RELEASE_STATE_FIELDS) to
                 its new value, e.g. {"Active RC": "none"}.
    """
    _rewrite_release_state(
        path,
        lambda original: render_release_state(original, updates),
        create_missing=True,
    )


def _rewrite_release_state(path: str, transform, create_missing: bool) -> None:
    """Replace release-state.md at *path* with ``transform(current_text)``, atomically.

    The one read-transform-replace path both release-state.md writers use.
    A symlinked *path* is resolved first so the link is kept and its target
    updated. The new content goes to a temporary file in the same directory,
    is fsynced, takes the old file's permission bits (0644 less the umask for
    a new file) and is renamed over the target, so a reader sees either the
    old file or the new one, never a partial write. When *transform* raises,
    nothing is written.

    Args:
        path:           Path to release-state.md.
        transform:      Callable taking the current text ('' for a missing
                        file) and returning the full new text.
        create_missing: When False, a missing file raises FileNotFoundError
                        instead of being created.
    """
    target = os.path.realpath(path)
    try:
        with open(target, encoding='utf-8') as fh:
            original = fh.read()
        mode = os.stat(target).st_mode & 0o7777
    except FileNotFoundError:
        if not create_missing:
            raise
        original = ''
        umask = os.umask(0)
        os.umask(umask)
        mode = 0o644 & ~umask

    rendered = transform(original)

    directory = os.path.dirname(target) or '.'
    fd, tmp_path = tempfile.mkstemp(
        dir=directory, prefix='.release-state.', suffix='.tmp'
    )
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            fh.write(rendered)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, target)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


# ---------------------------------------------------------------------------
# release-state.md — appended Auto-Ship Event record
# ---------------------------------------------------------------------------

AUTO_SHIP_EVENT_HEADING = 'Auto-Ship Event'

# Timestamp shape written when the caller does not supply one; the same
# UTC form the shell helpers stamp on their own appended events.
_EVENT_TIMESTAMP_FORMAT = '%Y-%m-%dT%H:%M:%SZ'


def _event_value(label: str, value) -> str:
    """Return *value* as a stripped single-line string, or raise ValueError.

    An event line is ``<label>: <value>``. A value spanning lines could
    plant a '## <heading>' line in the file, which a reader of an absent
    optional field would then take for that field, so a multi-line or
    empty value is refused rather than written.
    """
    text = '' if value is None else str(value)
    if '\n' in text or '\r' in text or not text.strip():
        raise ValueError(
            f"Auto-Ship Event '{label}' needs a single-line, non-empty value "
            f"(got {value!r})"
        )
    return text.strip()


def render_auto_ship_event(
    *,
    timestamp: str,
    version: str,
    trigger,
    error_key: str,
    ship_posture: str,
    tester_task: str,
    filed_bug=None,
) -> str:
    """Return one '## Auto-Ship Event' block, ending in a newline.

    The block lists, one 'Label: value' line each and in this order:
    Timestamp, Version, Trigger (the ship-policy trigger number that would
    have stopped the release), Error Key (that stop's machine key), Ship
    Posture, TESTER Task and — only when *filed_bug* is given — Filed Bug
    (the bug filed for a repeated-pattern stop).

    Raises:
        ValueError: when *trigger* is not a positive whole number, or any
            value is empty or spans more than one line.
    """
    trigger_text = _event_value('Trigger', trigger)
    if not re.fullmatch(r'[0-9]+', trigger_text) or int(trigger_text) < 1:
        raise ValueError(
            f"Auto-Ship Event 'Trigger' must be a positive whole number "
            f"naming the ship-policy trigger, e.g. 2 (got {trigger!r})"
        )
    fields = [
        ('Timestamp', timestamp),
        ('Version', version),
        ('Trigger', str(int(trigger_text))),
        ('Error Key', error_key),
        ('Ship Posture', ship_posture),
        ('TESTER Task', tester_task),
    ]
    if filed_bug is not None:
        fields.append(('Filed Bug', filed_bug))
    lines = [f'## {AUTO_SHIP_EVENT_HEADING}\n']
    lines += [f'{label}: {_event_value(label, value)}\n' for label, value in fields]
    return ''.join(lines)


def _append_block(text: str, block: str) -> str:
    """Return *text* with *block* appended after exactly one blank line.

    Every byte of *text* is kept. Only the separator is added: a newline
    to finish an unterminated last line, then a blank line unless *text*
    already ends in one. An empty *text* takes the block alone.
    """
    if not text or text.endswith('\n\n'):
        separator = ''
    elif text.endswith('\n'):
        separator = '\n'
    else:
        separator = '\n\n'
    return text + separator + block


def append_auto_ship_event(
    path: str,
    *,
    version: str,
    trigger,
    error_key: str,
    ship_posture: str,
    tester_task: str,
    filed_bug=None,
    timestamp=None,
) -> str:
    """Append one '## Auto-Ship Event' block to release-state.md, atomically.

    Records a release that shipped under a ship posture where the
    ship-policy matrix would have stopped it. The block goes after
    everything already in the file — the four schema fields and any
    earlier appended events, '## Operator Override' and '## Auto-Ship
    Event' included — and no existing byte changes, so a second call adds
    a second block and never overwrites the first. The event is a record
    only; no release decision reads it back.

    Args:
        path:         Path to release-state.md. It must already exist.
        version:      The version that shipped, e.g. "v1.8.0".
        trigger:      The ship-policy trigger number that would have
                      stopped the release, e.g. 2.
        error_key:    That stop's machine error key, e.g.
                      "cm-release.trigger-2.systemic-risk-high".
        ship_posture: The resolved ship posture the release shipped under,
                      e.g. "always".
        tester_task:  The TESTER task whose report drove the decision.
        filed_bug:    The bug filed for the pattern, when one was filed
                      (e.g. a repeated NON-FUNCTIONAL stop); omitted
                      from the block when None.
        timestamp:    When the event happened; defaults to the current
                      UTC time as YYYY-MM-DDThh:mm:ssZ.

    Returns:
        The block that was appended.

    Raises:
        FileNotFoundError: when *path* does not exist; an event is never
            the first thing written to a release-state.md.
        ValueError: when a value is empty, spans lines, or the trigger is
            not a positive whole number. Nothing is written.
    """
    if timestamp is None:
        timestamp = datetime.datetime.now(datetime.timezone.utc).strftime(
            _EVENT_TIMESTAMP_FORMAT
        )
    block = render_auto_ship_event(
        timestamp=timestamp,
        version=version,
        trigger=trigger,
        error_key=error_key,
        ship_posture=ship_posture,
        tester_task=tester_task,
        filed_bug=filed_bug,
    )
    _rewrite_release_state(
        path, lambda original: _append_block(original, block), create_missing=False
    )
    return block


def main() -> None:
    """CLI entry point: dispatch each subcommand to its function.

    open, open-with-attempt, ship, cancel and archive-cancelled act on a
    per-RC JSON file; release-state and auto-ship-event act on the
    project-scoped release-state.md.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Write or update a per-RC release-state JSON file. "
            "Use the 'open' subcommand to create a fresh record (stdout), "
            "the 'ship' subcommand to merge a shipped outcome in-place, "
            "the 'cancel' subcommand to merge a cancelled outcome in-place, "
            "or the 'archive-cancelled' subcommand to move a cancelled JSON "
            "to history/ and free the version slot. The 'release-state' "
            "subcommand sets release-state.md fields in place, and the "
            "'auto-ship-event' subcommand appends an '## Auto-Ship Event' "
            "block to it."
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

    # open-with-attempt subcommand (adds the bundle-family retry counter)
    p_open_attempt = subparsers.add_parser(
        'open-with-attempt',
        help=(
            "Write a fresh per-RC release-state JSON to stdout, including "
            "the bundle-family attempt counter. Redirect stdout to the "
            "target file (as open-rc.sh does)."
        ),
    )
    p_open_attempt.add_argument(
        'rc',
        help="The RC version string (e.g. 'v2.0.1').",
    )
    p_open_attempt.add_argument(
        'opened_at',
        help="ISO8601 UTC timestamp string (e.g. '2026-07-07T12:00:00Z').",
    )
    p_open_attempt.add_argument(
        'attempt',
        type=int,
        help="Attempt number within the bundle family (>= 1).",
    )
    p_open_attempt.add_argument(
        'family_key',
        help="Family identity string, or the literal 'none' when the RC has no trackable cargo.",
    )
    p_open_attempt.add_argument(
        'cargo_json',
        help="JSON-encoded list of bundled-item basenames (e.g. '[\"BUG-0001-foo.md\"]').",
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

    # release-state subcommand (the release-state.md markdown field writer)
    p_state = subparsers.add_parser(
        'release-state',
        help=(
            "Set fields of the project-scoped release-state.md in place, "
            "atomically. Only the named fields change; every other line, "
            "including appended event blocks, is kept byte-for-byte."
        ),
        description=(
            "Set one or more of the four release-state.md fields in place. "
            "Name at least one field. Values are written as given; use the "
            "lowercase literal 'none' for an unset field. A missing file is "
            "created with a '# Release State' title."
        ),
    )
    p_state.add_argument(
        'path',
        help="Path to the release-state.md file to update.",
    )
    for heading in RELEASE_STATE_FIELDS:
        p_state.add_argument(
            '--' + heading.lower().replace(' ', '-'),
            dest=heading.lower().replace(' ', '_'),
            metavar='VALUE',
            default=None,
            help=f"New value for '## {heading}'.",
        )

    # auto-ship-event subcommand (appends one ## Auto-Ship Event block)
    p_event = subparsers.add_parser(
        'auto-ship-event',
        help=(
            "Append one '## Auto-Ship Event' block to the end of an existing "
            "release-state.md, atomically. Every existing byte is kept; a "
            "second call adds a second block."
        ),
        description=(
            "Append one '## Auto-Ship Event' block recording a release that "
            "shipped under a ship posture where the ship-policy matrix would "
            "have stopped it. The block lists Timestamp, Version, Trigger, "
            "Error Key, Ship Posture, TESTER Task and, when given, Filed Bug, "
            "one 'Label: value' line each. Every value must be a single "
            "non-empty line. The file must already exist. The event is a "
            "record only; no release decision reads it back."
        ),
    )
    p_event.add_argument(
        'path',
        help="Path to the existing release-state.md to append to.",
    )
    p_event.add_argument(
        '--version', required=True, metavar='VERSION',
        help="The version that shipped (e.g. 'v1.8.0').",
    )
    p_event.add_argument(
        '--trigger', required=True, metavar='N',
        help="Ship-policy trigger number that would have stopped the release (e.g. 2).",
    )
    p_event.add_argument(
        '--error-key', required=True, metavar='KEY',
        help=(
            "Machine error key of the stop that did not happen "
            "(e.g. 'cm-release.trigger-2.systemic-risk-high')."
        ),
    )
    p_event.add_argument(
        '--ship-posture', required=True, metavar='POSTURE',
        help="Resolved ship posture the release shipped under (e.g. 'always').",
    )
    p_event.add_argument(
        '--tester-task', required=True, metavar='TASK_ID',
        help="TESTER task whose report drove the ship decision.",
    )
    p_event.add_argument(
        '--filed-bug', default=None, metavar='NAME',
        help=(
            "Bug filed for the pattern, when one was filed; adds a "
            "'Filed Bug:' line. Omit when no bug was filed."
        ),
    )
    p_event.add_argument(
        '--timestamp', default=None, metavar='TS',
        help=(
            "When the event happened. Default: the current UTC time as "
            "YYYY-MM-DDThh:mm:ssZ."
        ),
    )

    args = parser.parse_args()
    if args.command == 'open':
        write_open(args.rc, args.opened_at)
    elif args.command == 'open-with-attempt':
        family_key = None if args.family_key == 'none' else args.family_key
        cargo = json.loads(args.cargo_json)
        write_open_with_attempt(args.rc, args.opened_at, args.attempt, family_key, cargo)
    elif args.command == 'ship':
        write_ship(args.path, args.rc, args.closed_at)
    elif args.command == 'cancel':
        write_cancel(args.path, args.rc, args.closed_at)
    elif args.command == 'archive-cancelled':
        archive_cancelled(args.path, args.rc, args.closed_at)
    elif args.command == 'release-state':
        updates = {
            heading: getattr(args, heading.lower().replace(' ', '_'))
            for heading in RELEASE_STATE_FIELDS
            if getattr(args, heading.lower().replace(' ', '_')) is not None
        }
        if not updates:
            p_state.error(
                'name at least one field, e.g. --active-rc none'
            )
        try:
            update_release_state_fields(args.path, updates)
        except ValueError as exc:
            p_state.error(str(exc))
    elif args.command == 'auto-ship-event':
        try:
            append_auto_ship_event(
                args.path,
                version=args.version,
                trigger=args.trigger,
                error_key=args.error_key,
                ship_posture=args.ship_posture,
                tester_task=args.tester_task,
                filed_bug=args.filed_bug,
                timestamp=args.timestamp,
            )
        except FileNotFoundError:
            p_event.error(
                f'release-state.md not found: {args.path} — an Auto-Ship '
                f'Event is appended to an existing file; check the path'
            )
        except ValueError as exc:
            p_event.error(str(exc))


if __name__ == '__main__':
    main()
