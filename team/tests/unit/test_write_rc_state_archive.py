"""
test_write_rc_state_archive.py
==============================
Unit tests for the archive_cancelled and promote_cancelled_to_history functions
in pgai_agent_kanban.cm.write_rc_state.

These functions implement the freeing semantics for cancelled per-RC JSON files:
- archive_cancelled: marks a JSON cancelled, then moves it to history/ and frees
  the version slot so open-rc can reuse the number without collision.
- promote_cancelled_to_history: used by open-rc.sh to archive a stale cancelled
  JSON before writing a fresh in_progress record.

All tests use tmp_path; no live filesystem paths.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pgai_agent_kanban.cm.write_rc_state import (
    archive_cancelled,
    promote_cancelled_to_history,
    write_cancel,
    write_open,
)


# ---------------------------------------------------------------------------
# archive_cancelled — basic behaviour
# ---------------------------------------------------------------------------


def test_archive_cancelled_moves_file_to_history(tmp_path: Path) -> None:
    """archive_cancelled moves the JSON from the active slot to history/."""
    state_dir = tmp_path / "release-state"
    state_dir.mkdir()
    state_file = state_dir / "v1.5.0.json"
    state_file.write_text(
        json.dumps(
            {"rc": "v1.5.0", "opened_at": "2026-01-01T00:00:00Z",
             "closed_at": "2026-01-02T00:00:00Z", "outcome": "cancelled"},
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    archive_cancelled(str(state_file), "v1.5.0", "2026-01-02T00:00:00Z")

    assert not state_file.exists(), "Active slot must be removed after archiving"
    history_dir = state_dir / "history"
    assert history_dir.exists(), "history/ directory must be created"
    archived = list(history_dir.iterdir())
    assert len(archived) == 1, f"Expected 1 archive file, found {len(archived)}"
    archive_name = archived[0].name
    assert archive_name.startswith("v1.5.0-cancelled-"), (
        f"Archive filename must start with version-cancelled-: {archive_name!r}"
    )


def test_archive_cancelled_preserves_json_content(tmp_path: Path) -> None:
    """archive_cancelled copies the full JSON content to the archive file."""
    state_dir = tmp_path / "release-state"
    state_dir.mkdir()
    state_file = state_dir / "v1.5.0.json"
    original = {
        "rc": "v1.5.0",
        "opened_at": "2026-01-01T00:00:00Z",
        "closed_at": "2026-01-02T00:00:00Z",
        "outcome": "cancelled",
    }
    state_file.write_text(json.dumps(original, indent=2) + "\n", encoding="utf-8")

    archive_cancelled(str(state_file), "v1.5.0", "2026-01-02T00:00:00Z")

    history_dir = state_dir / "history"
    archived_file = next(history_dir.iterdir())
    payload = json.loads(archived_file.read_text())
    assert payload["rc"] == "v1.5.0"
    assert payload["opened_at"] == "2026-01-01T00:00:00Z"
    assert payload["outcome"] == "cancelled"


def test_archive_cancelled_frees_version_slot(tmp_path: Path) -> None:
    """archive_cancelled removes the active slot file so the version number is free."""
    state_dir = tmp_path / "release-state"
    state_dir.mkdir()
    state_file = state_dir / "v2.0.0.json"
    state_file.write_text(
        json.dumps({"rc": "v2.0.0", "outcome": "cancelled"}, indent=2) + "\n",
        encoding="utf-8",
    )

    archive_cancelled(str(state_file), "v2.0.0", "2026-07-01T12:00:00Z")

    assert not state_file.exists(), (
        "Version slot must be removed so open-rc can write a fresh record here"
    )


def test_archive_cancelled_is_noop_when_file_absent(tmp_path: Path) -> None:
    """archive_cancelled silently does nothing when the file does not exist."""
    state_file = tmp_path / "v0.0.1.json"
    result = archive_cancelled(str(state_file), "v0.0.1", "2026-01-01T00:00:00Z")
    assert result == '', "Should return empty string when file absent"


def test_archive_cancelled_does_not_move_non_cancelled_file(tmp_path: Path) -> None:
    """archive_cancelled skips files whose outcome is not 'cancelled'."""
    state_dir = tmp_path / "release-state"
    state_dir.mkdir()
    state_file = state_dir / "v1.0.0.json"
    state_file.write_text(
        json.dumps({"rc": "v1.0.0", "outcome": "in_progress"}, indent=2) + "\n",
        encoding="utf-8",
    )

    result = archive_cancelled(str(state_file), "v1.0.0", "2026-01-01T00:00:00Z")

    assert result == '', "Should not archive a non-cancelled file"
    assert state_file.exists(), "Non-cancelled file must remain in place"


def test_archive_cancelled_idempotent_on_repeated_call(tmp_path: Path) -> None:
    """archive_cancelled is idempotent: calling it twice produces no error."""
    state_dir = tmp_path / "release-state"
    state_dir.mkdir()
    state_file = state_dir / "v1.5.0.json"
    state_file.write_text(
        json.dumps({"rc": "v1.5.0", "outcome": "cancelled"}, indent=2) + "\n",
        encoding="utf-8",
    )

    archive_cancelled(str(state_file), "v1.5.0", "2026-01-02T00:00:00Z")
    # Second call: file is gone, should be a no-op
    result = archive_cancelled(str(state_file), "v1.5.0", "2026-01-02T00:00:00Z")
    assert result == '', "Second archive call must be a no-op (file already gone)"


def test_archive_cancelled_creates_history_dir_when_absent(tmp_path: Path) -> None:
    """archive_cancelled creates the history/ directory if it does not exist."""
    state_dir = tmp_path / "release-state"
    state_dir.mkdir()
    state_file = state_dir / "v1.5.0.json"
    state_file.write_text(
        json.dumps({"rc": "v1.5.0", "outcome": "cancelled"}, indent=2) + "\n",
        encoding="utf-8",
    )

    assert not (state_dir / "history").exists(), "history/ should not exist before test"

    archive_cancelled(str(state_file), "v1.5.0", "2026-01-02T00:00:00Z")

    assert (state_dir / "history").is_dir(), "archive_cancelled must create history/"


# ---------------------------------------------------------------------------
# archive_cancelled — multiple cancel/reopen cycles
# ---------------------------------------------------------------------------


def test_archive_cancelled_second_cycle_produces_distinct_archive(tmp_path: Path) -> None:
    """Two cancel/reopen cycles produce two distinct archive files in history/."""
    state_dir = tmp_path / "release-state"
    state_dir.mkdir()
    state_file = state_dir / "v1.5.0.json"

    # First cancel + archive
    state_file.write_text(
        json.dumps({"rc": "v1.5.0", "outcome": "cancelled"}, indent=2) + "\n",
        encoding="utf-8",
    )
    archive_cancelled(str(state_file), "v1.5.0", "2026-01-02T00:00:00Z")

    # Second cancel + archive (different timestamp)
    state_file.write_text(
        json.dumps({"rc": "v1.5.0", "outcome": "cancelled"}, indent=2) + "\n",
        encoding="utf-8",
    )
    archive_cancelled(str(state_file), "v1.5.0", "2026-01-10T00:00:00Z")

    history_dir = state_dir / "history"
    archived = sorted(history_dir.iterdir())
    assert len(archived) == 2, (
        f"Two cancel cycles must produce two distinct archive files; found: {archived}"
    )


# ---------------------------------------------------------------------------
# promote_cancelled_to_history — used by open-rc.sh before writing fresh record
# ---------------------------------------------------------------------------


def test_promote_cancelled_to_history_moves_cancelled_file(tmp_path: Path) -> None:
    """promote_cancelled_to_history archives a cancelled JSON and returns archive path."""
    state_dir = tmp_path / "release-state"
    state_dir.mkdir()
    state_file = state_dir / "v1.7.0.json"
    state_file.write_text(
        json.dumps(
            {"rc": "v1.7.0", "opened_at": "2026-03-01T00:00:00Z",
             "closed_at": "2026-03-02T00:00:00Z", "outcome": "cancelled"},
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    archive_path = promote_cancelled_to_history(str(state_file))

    assert archive_path, "Should return the archive path when something was moved"
    assert not state_file.exists(), "Active slot must be freed after promote"
    assert Path(archive_path).exists(), "Archive file must exist at returned path"


def test_promote_cancelled_to_history_is_noop_when_file_absent(tmp_path: Path) -> None:
    """promote_cancelled_to_history returns '' when no file exists at the slot."""
    result = promote_cancelled_to_history(str(tmp_path / "v0.1.0.json"))
    assert result == ''


def test_promote_cancelled_to_history_is_noop_for_in_progress(tmp_path: Path) -> None:
    """promote_cancelled_to_history does not touch in_progress records."""
    state_file = tmp_path / "v1.0.0.json"
    state_file.write_text(
        json.dumps({"rc": "v1.0.0", "outcome": "in_progress"}, indent=2) + "\n",
        encoding="utf-8",
    )
    result = promote_cancelled_to_history(str(state_file))
    assert result == ''
    assert state_file.exists(), "in_progress record must not be moved"


def test_promote_cancelled_to_history_is_noop_for_shipped(tmp_path: Path) -> None:
    """promote_cancelled_to_history does not touch shipped records."""
    state_file = tmp_path / "v1.0.0.json"
    state_file.write_text(
        json.dumps({"rc": "v1.0.0", "outcome": "shipped"}, indent=2) + "\n",
        encoding="utf-8",
    )
    result = promote_cancelled_to_history(str(state_file))
    assert result == ''
    assert state_file.exists(), "shipped record must not be moved"


# ---------------------------------------------------------------------------
# Round-trip: write_cancel → archive_cancelled → write_open (slot free)
# ---------------------------------------------------------------------------


def test_cancel_then_archive_frees_slot_for_reopen(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """Full round-trip: write_cancel → archive_cancelled frees slot → write_open succeeds.

    This covers the core acceptance criterion: after cancel + archive, writing a
    fresh in_progress record to the same path succeeds without collision, and the
    history record is preserved.
    """
    state_dir = tmp_path / "release-state"
    state_dir.mkdir()
    state_file = state_dir / "v1.26.4.json"

    # Simulate what open-rc does when it first opens the RC
    state_file.write_text(
        json.dumps(
            {"rc": "v1.26.4", "opened_at": "2026-07-20T01:00:00Z",
             "closed_at": None, "outcome": "in_progress"},
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    assert json.loads(state_file.read_text())["outcome"] == "in_progress"

    # Simulate what cancel-rc does: mark cancelled, then archive
    write_cancel(str(state_file), "v1.26.4", "2026-07-20T06:00:00Z")
    assert json.loads(state_file.read_text())["outcome"] == "cancelled"

    archive_cancelled(str(state_file), "v1.26.4", "2026-07-20T06:00:00Z")
    assert not state_file.exists(), "Slot must be free after archive"

    # Simulate what open-rc does on re-open: write a fresh record to the same path.
    # write_open emits JSON to stdout; capture and parse only the JSON portion
    # (ignore any diagnostic print lines from archive_cancelled that preceded this call).
    capsys.readouterr()  # clear any prior output
    write_open("v1.26.4", "2026-07-21T08:00:00Z")
    captured = capsys.readouterr()
    # write_open emits pure JSON — no extra lines; parse directly
    fresh_record = json.loads(captured.out)
    state_file.write_text(captured.out, encoding="utf-8")

    assert fresh_record["outcome"] == "in_progress"
    assert fresh_record["opened_at"] == "2026-07-21T08:00:00Z"

    # The history record must still be present and readable
    history_dir = state_dir / "history"
    assert history_dir.exists()
    history_files = list(history_dir.iterdir())
    assert len(history_files) == 1, "One history record expected"
    hist = json.loads(history_files[0].read_text())
    assert hist["outcome"] == "cancelled"
    assert hist["rc"] == "v1.26.4"
