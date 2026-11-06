"""
task_tokens.py — per-task token records and their RC versions, read once.

The token aggregators (``team/pm-agent/aggregate_tokens.py`` and
``team/scripts/lib/metrics_aggregator.py``) both walk a project's task
folders, read each folder's ``artifacts/tokens.json``, the
``## Release Version`` its README names and the agent that ran it, and list
the RC versions and UTC days a set of records spans. These are those readers;
both aggregators import them, so both reports put a record on the same day.

Day rule: a record's ``timestamp`` is parsed by
``pgai_agent_kanban.lib.timeutil.try_parse_iso`` (surrounding whitespace
ignored; a ``Z``, ``+HH:MM`` or ``-HH:MM`` suffix honoured; no offset read as
UTC) and the record belongs to the UTC calendar day of that instant. A
missing or unparseable timestamp belongs to no day.

Public API:
    TOKENS_JSON, README_MD, STATUS_MD
    load_tokens_json(task_dir) -> dict | None
    is_new_schema(tok) -> bool
    is_captured(tok) -> bool
    read_field_from_markdown(text, heading) -> str | None
    read_rc_version(task_dir) -> str | None
    read_agent(task_dir) -> str
    scan_tasks(tasks_dir) -> list[dict]
    normalise_version(version) -> str
    all_rc_versions(records) -> list[str]
    record_timestamp(record) -> str | None
    timestamp_utc_day(ts_str) -> date | None
    ts_in_day(ts_str, day) -> bool
    all_days(records) -> list[str]
"""

from __future__ import annotations

import json
import pathlib
import sys
from datetime import UTC, date
from typing import Any

from pgai_agent_kanban.archive.lookup import task_dirs_with_archived
from pgai_agent_kanban.lib.timeutil import try_parse_iso
from pgai_agent_kanban.task_status import section_value

__all__ = [
    "README_MD",
    "STATUS_MD",
    "TOKENS_JSON",
    "all_days",
    "all_rc_versions",
    "is_captured",
    "is_new_schema",
    "load_tokens_json",
    "normalise_version",
    "read_agent",
    "read_field_from_markdown",
    "read_rc_version",
    "record_timestamp",
    "scan_tasks",
    "timestamp_utc_day",
    "ts_in_day",
]

TOKENS_JSON = "artifacts/tokens.json"
README_MD = "README.md"
STATUS_MD = "status.md"

# The prefix of the legacy task-ID form CLAUDE-<ROLE>-YYYYMMDD-NNN-slug.
_LEGACY_TASK_ID_PREFIX = "claude"


def load_tokens_json(task_dir: pathlib.Path) -> dict[str, Any] | None:
    """
    Load artifacts/tokens.json for a task directory.

    Returns the parsed dict, or None if the file is absent (silent) or
    malformed (with a stderr warning).
    """
    path = task_dir / TOKENS_JSON
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("top-level value is not a JSON object")
        return data
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        print(
            f"WARNING: skipping malformed tokens.json for task {task_dir.name}: {exc}",
            file=sys.stderr,
        )
        return None


def is_new_schema(tok: dict[str, Any]) -> bool:
    """
    Return True when tok has the new-schema fields: total_cost_usd (float or int)
    and model_usage (non-empty dict).

    New schema has total_cost_usd and model_usage (non-empty dict).
    Legacy schema has top-level model/input_tokens/output_tokens/cache_* fields
    and no total_cost_usd or model_usage.
    """
    total_cost = tok.get("total_cost_usd")
    model_usage = tok.get("model_usage")
    return isinstance(total_cost, (int, float)) and isinstance(model_usage, dict) and len(model_usage) > 0


def is_captured(tok: dict[str, Any]) -> bool:
    """
    Return True when the tokens.json record represents a successful capture.

    Returns False only when tok has an explicit ``captured: false`` field,
    which is written by token_capture.sh when the agent exited without emitting
    usage JSON (e.g. was killed mid-run).  Records that pre-date this field
    (no ``captured`` key) are assumed to be successful captures.
    """
    val = tok.get("captured", True)
    # Explicit False (not falsy-zero, not absent) means capture failed.
    return val is not False


def read_field_from_markdown(text: str, heading: str) -> str | None:
    """Return the value of the '## <heading>' section, or None.

    The value is read by task_status.section_value -- the first non-blank
    line of the section, HTML comments dropped and a bold wrapper removed.
    An empty section reads as None rather than as the next section's text,
    and a '## ' line inside a fenced code block is not a heading.

    Returns None if the heading or a value line is not found.
    """
    return section_value(text, heading, fences=True) or None


def read_rc_version(task_dir: pathlib.Path) -> str | None:
    """
    Read '## Release Version' from the task README.md.

    Returns the raw string value (e.g. "v0.23.22") or None when the README,
    the heading or a value line is absent. A '## ' line inside a fenced code
    block is not a heading.
    """
    readme = task_dir / README_MD
    if not readme.is_file():
        return None
    try:
        text = readme.read_text(encoding="utf-8")
    except OSError:
        return None
    return section_value(text, "Release Version", fences=True) or None


def _read_task_field(path: pathlib.Path, heading: str) -> str | None:
    """The '## <heading>' value of the markdown file at *path*; None when unreadable or absent."""
    if not path.is_file():
        return None
    try:
        return read_field_from_markdown(path.read_text(encoding="utf-8"), heading)
    except OSError:
        return None


def read_agent(task_dir: pathlib.Path) -> str:
    """
    Read the agent type for a task, lowercased.

    Priority:
      1. status.md '## Agent' field, model suffix dropped
         ("coder (claude-sonnet-4-6)" -> "coder")
      2. README.md '## Role' field
      3. The task folder name: ROLE-YYYYMMDD-NNN-slug -> role, and the
         legacy CLAUDE-ROLE-YYYYMMDD-NNN-slug -> role
      4. 'unknown' when the folder name is empty
    """
    agent = _read_task_field(task_dir / STATUS_MD, "Agent")
    if agent:
        return agent.split("(")[0].strip().lower()

    role = _read_task_field(task_dir / README_MD, "Role")
    if role:
        return role.lower()

    parts = task_dir.name.lower().split("-")
    if len(parts) >= 2 and parts[0] == _LEGACY_TASK_ID_PREFIX:
        return parts[1]
    return parts[0] or "unknown"


def scan_tasks(tasks_dir: pathlib.Path) -> list[dict[str, Any]]:
    """
    Walk the tasks/ directory and collect metadata for every task that has
    a tokens.json file. The project's archived task folders
    (archive/<project>/tasks/<ID>/, for IDs with no live folder) are walked
    too, merged with the live ones in task-ID order -- see
    pgai_agent_kanban.archive.lookup.task_dirs_with_archived -- so a roll-up
    rebuilt after an archive run counts exactly the tasks, tokens and wall
    time it counted before the run.

    Returns a list of records:
      {
        "task_id":    str,
        "task_dir":   Path,
        "rc_version": str | None,  -- from README.md ## Release Version
        "agent":      str,         -- see read_agent
        "tokens":     dict,        -- raw tokens.json data
        "timestamp":  str | None,  -- tokens.json 'timestamp' field
        "new_schema": bool,        -- True when total_cost_usd + model_usage present
        "captured":   bool,        -- False only for an explicit captured: false
      }
    """
    records: list[dict[str, Any]] = []

    for entry in task_dirs_with_archived(tasks_dir):
        tokens = load_tokens_json(entry)
        if tokens is None:
            continue  # No tokens.json or malformed (warning already emitted)

        records.append(
            {
                "task_id": entry.name,
                "task_dir": entry,
                "rc_version": read_rc_version(entry),
                "agent": read_agent(entry),
                "tokens": tokens,
                "timestamp": tokens.get("timestamp"),
                "new_schema": is_new_schema(tokens),
                "captured": is_captured(tokens),
            }
        )

    return records


def normalise_version(version: str) -> str:
    """Ensure the version string starts with 'v'."""
    version = version.strip()
    if version and not version.startswith("v"):
        return f"v{version}"
    return version


def all_rc_versions(records: list[dict[str, Any]]) -> list[str]:
    """Return a sorted list of distinct RC versions seen in the records."""
    versions: set[str] = set()
    for r in records:
        rv = r.get("rc_version")
        if rv:
            versions.add(normalise_version(rv))
    return sorted(versions)


def record_timestamp(record: dict[str, Any]) -> str | None:
    """The tokens.json timestamp of a scan record, or None.

    Reads the record's own ``timestamp`` (set by scan_tasks) and falls back
    to ``tokens["timestamp"]``, so a record built with only the raw
    tokens.json dict reads the same value.
    """
    ts = record.get("timestamp")
    if ts is None:
        tokens = record.get("tokens")
        if isinstance(tokens, dict):
            ts = tokens.get("timestamp")
    return ts


def timestamp_utc_day(ts_str: str | None) -> date | None:
    """The UTC calendar day of an ISO-8601 timestamp; None when absent or unparseable.

    The module docstring's day rule: ``2026-05-18T01:30:00+02:00`` is
    2026-05-17 in UTC, and ``2026-05-17T10:00:00`` (no offset) is read as UTC.
    """
    if not isinstance(ts_str, str) or not ts_str.strip():
        return None
    moment = try_parse_iso(ts_str)
    if moment is None:
        return None
    return moment.astimezone(UTC).date()


def ts_in_day(ts_str: str | None, day: date) -> bool:
    """True when *ts_str* falls on the UTC calendar *day*; False when absent or unparseable."""
    return timestamp_utc_day(ts_str) == day


def all_days(records: list[dict[str, Any]]) -> list[str]:
    """Return a sorted list of the distinct UTC days (YYYY-MM-DD) the records' timestamps fall on.

    Records with a missing or unparseable timestamp are skipped.
    """
    days: set[str] = set()
    for r in records:
        day = timestamp_utc_day(record_timestamp(r))
        if day is not None:
            days.add(day.isoformat())
    return sorted(days)
