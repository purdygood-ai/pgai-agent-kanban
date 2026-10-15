"""
test_write_rc_state_attempt.py
===============================
Unit tests for write_open_with_attempt in pgai_agent_kanban.cm.write_rc_state
— the retry-counter writer.

Coverage:
  - write_open_with_attempt emits the same base shape as write_open() plus
    attempt/family_key/cargo.
  - family_key of None round-trips as JSON null (not the literal string
    "None" or "none").
  - The new fields survive write_cancel's read-modify-write merge (it only
    ever overwrites closed_at and outcome), proving the counter persists
    across the cancel step in the cancel -> rebundle cycle.
  - The new fields survive archive_cancelled's copy-then-remove (content is
    preserved verbatim into history/).

All tests use tmp_path; no live filesystem paths.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pgai_agent_kanban.cm.write_rc_state import (
    archive_cancelled,
    write_cancel,
    write_open_with_attempt,
)


def test_write_open_with_attempt_emits_base_and_new_fields(capsys: pytest.CaptureFixture) -> None:
    """The payload carries rc/opened_at/closed_at/outcome plus the three new keys."""
    write_open_with_attempt(
        "v2.0.1", "2026-07-27T12:00:00Z", 2, "family-abc123", ["BUG-0001-foo.md"]
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["rc"] == "v2.0.1"
    assert payload["opened_at"] == "2026-07-27T12:00:00Z"
    assert payload["closed_at"] is None
    assert payload["outcome"] == "in_progress"
    assert payload["attempt"] == 2
    assert payload["family_key"] == "family-abc123"
    assert payload["cargo"] == ["BUG-0001-foo.md"]


def test_write_open_with_attempt_family_key_none_round_trips_as_json_null(
    capsys: pytest.CaptureFixture,
) -> None:
    """A None family_key (no cargo, e.g. operator-authored requirements)
    serialises as JSON null, not the string 'none' or 'None'."""
    write_open_with_attempt("v3.0.0", "2026-07-27T12:00:00Z", 1, None, [])
    payload = json.loads(capsys.readouterr().out)

    assert payload["family_key"] is None
    assert payload["cargo"] == []
    assert payload["attempt"] == 1


def test_write_open_with_attempt_first_attempt_defaults_to_one(
    capsys: pytest.CaptureFixture,
) -> None:
    """The happy-path shape: attempt 1, matching the AC 'first-attempt happy
    path records attempt 1'."""
    write_open_with_attempt("v2.0.0", "2026-07-20T09:00:00Z", 1, "family-xyz789", ["BUG-0007.md"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["attempt"] == 1


def test_attempt_fields_survive_write_cancel_merge(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """write_cancel only overwrites closed_at/outcome — attempt, family_key,
    and cargo must pass through untouched, proving the counter is preserved
    across the cancel step of the cancel -> rebundle cycle."""
    state_file = tmp_path / "v2.0.1.json"

    write_open_with_attempt(
        "v2.0.1", "2026-07-27T12:00:00Z", 2, "family-abc123", ["BUG-0001-foo.md", "BUG-0002-bar.md"]
    )
    state_file.write_text(capsys.readouterr().out, encoding="utf-8")

    write_cancel(str(state_file), "v2.0.1", "2026-07-27T18:00:00Z")

    cancelled = json.loads(state_file.read_text())
    assert cancelled["outcome"] == "cancelled"
    assert cancelled["closed_at"] == "2026-07-27T18:00:00Z"
    assert cancelled["attempt"] == 2, "attempt must survive the cancel merge"
    assert cancelled["family_key"] == "family-abc123", "family_key must survive the cancel merge"
    assert cancelled["cargo"] == ["BUG-0001-foo.md", "BUG-0002-bar.md"], (
        "cargo must survive the cancel merge"
    )


def test_attempt_fields_survive_archive_to_history(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """After write_cancel + archive_cancelled, the archived history record in
    history/ still carries the attempt/family_key/cargo fields — this is what
    lets the NEXT open (a new version, same cargo) find this record and
    increment the count."""
    state_dir = tmp_path / "release-state"
    state_dir.mkdir()
    state_file = state_dir / "v2.0.1.json"

    write_open_with_attempt("v2.0.1", "2026-07-27T12:00:00Z", 1, "family-abc123", ["BUG-0001-foo.md"])
    state_file.write_text(capsys.readouterr().out, encoding="utf-8")

    write_cancel(str(state_file), "v2.0.1", "2026-07-27T18:00:00Z")
    archive_path = archive_cancelled(str(state_file), "v2.0.1", "2026-07-27T18:00:00Z")

    assert archive_path, "archive_cancelled must return the archive path"
    archived = json.loads(Path(archive_path).read_text())
    assert archived["attempt"] == 1
    assert archived["family_key"] == "family-abc123"
    assert archived["cargo"] == ["BUG-0001-foo.md"]
