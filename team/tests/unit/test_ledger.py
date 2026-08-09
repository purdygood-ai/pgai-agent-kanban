"""
test_ledger.py
================
Unit tests for pgai_agent_kanban.overwatch.ledger — the v2.0.0 Goal 5
("the ledger") append-only auto-resolutions ledger.

Coverage (mirrors the task's Acceptance Criteria):
  - One append-only entry per resolution event carries every required
    field (timestamp, RC, attempt N -> N+1, trigger, findings, matrix row
    matched, actions taken, evidence paths, Outcome).
  - Entries reference evidence by path only — no report/bug content is
    ever embedded in the rendered entry.
  - append_entry() writes atomically: prior content is preserved
    byte-for-byte and the new entry is appended, never interleaved or
    partially written (verified via the temp-file-then-rename mechanism
    and via a forced-failure fixture that leaves the ledger untouched).
  - backfill_outcome() sets a pending entry's Outcome exactly once, is a
    no-op when called again with the identical value, and refuses (raises
    LedgerError) to silently overwrite a different already-recorded value.
  - backfill_family_outcome() backfills every still-pending entry sharing a
    family_key and leaves already-terminal entries and unrelated families
    untouched.
  - parse_entries()/find_entry_ids_by_family() round-trip every field
    written by append_entry(), including the empty-findings and
    empty-actions edge cases.
  - No field other than Outcome is ever rewritten by a backfill.

All tests use tmp_path; no live filesystem paths. Function names describe
behaviour under test, never bug/task IDs (SOP.md "Test Authoring
Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pgai_agent_kanban.overwatch.ledger import (
    LedgerEntry,
    LedgerError,
    append_entry,
    backfill_family_outcome,
    backfill_outcome,
    find_entry_ids_by_family,
    format_entry,
    ledger_path_for_project,
    parse_entries,
)


def _make_entry(**overrides) -> LedgerEntry:
    defaults = dict(
        timestamp="2026-07-27T18:03:00Z",
        rc="v2.0.1",
        attempt_from=1,
        attempt_to=2,
        trigger="ship-policy-halt",
        matrix_row="CANCEL-AND-REBUNDLE",
        findings=["BUG-0041-foo.md", "BUG-0042-bar.md"],
        actions_taken=["cm-cancel-rc --yes v2.0.1", "wontdo CODER-20260727-010"],
        evidence_paths=["/kanban/projects/p/tasks/CM-X/artifacts/report.md", "/kanban/HALT"],
        family_key="family-abc123",
    )
    defaults.update(overrides)
    return LedgerEntry(**defaults)


# ---------------------------------------------------------------------------
# append_entry — required fields and one-entry-per-event
# ---------------------------------------------------------------------------


def test_append_entry_creates_ledger_with_header_and_one_entry(tmp_path: Path) -> None:
    """A fresh ledger gets the standard append-only header plus one entry."""
    ledger_path = str(tmp_path / "auto-resolutions.md")
    entry = _make_entry()
    entry_id = append_entry(ledger_path, entry)

    text = Path(ledger_path).read_text(encoding="utf-8")
    assert "Append-only" in text
    assert text.count("## Entry:") == 1
    assert entry_id in text


def test_appended_entry_carries_every_required_field(tmp_path: Path) -> None:
    """Timestamp, RC, attempt N->N+1, trigger, findings, matrix row, actions,
    evidence, and Outcome are all present in the rendered entry.
    """
    ledger_path = str(tmp_path / "auto-resolutions.md")
    entry = _make_entry()
    append_entry(ledger_path, entry)

    parsed = parse_entries(ledger_path)
    assert len(parsed) == 1
    record = parsed[0]
    assert record["timestamp"] == "2026-07-27T18:03:00Z"
    assert record["rc"] == "v2.0.1"
    assert record["attempt_from"] == 1
    assert record["attempt_to"] == 2
    assert record["trigger"] == "ship-policy-halt"
    assert record["findings"] == ["BUG-0041-foo.md", "BUG-0042-bar.md"]
    assert record["matrix_row"] == "CANCEL-AND-REBUNDLE"
    assert record["actions_taken"] == [
        "cm-cancel-rc --yes v2.0.1",
        "wontdo CODER-20260727-010",
    ]
    assert record["evidence_paths"] == [
        "/kanban/projects/p/tasks/CM-X/artifacts/report.md",
        "/kanban/HALT",
    ]
    assert record["outcome"] is None  # rendered as "pending" until backfilled


def test_second_append_adds_a_new_entry_without_touching_the_first(tmp_path: Path) -> None:
    """Two resolution events produce two entries; the first entry's text is
    byte-identical before and after the second append.
    """
    ledger_path = str(tmp_path / "auto-resolutions.md")
    first_id = append_entry(ledger_path, _make_entry())
    text_after_first = Path(ledger_path).read_text(encoding="utf-8")

    second_id = append_entry(
        ledger_path,
        _make_entry(
            timestamp="2026-07-27T19:10:00Z",
            rc="v2.0.2",
            attempt_from=2,
            attempt_to=3,
            findings=[],
            actions_taken=["cm-cancel-rc --yes v2.0.2"],
        ),
    )
    text_after_second = Path(ledger_path).read_text(encoding="utf-8")

    assert first_id != second_id
    assert text_after_first in text_after_second
    parsed = parse_entries(ledger_path)
    assert len(parsed) == 2
    assert parsed[0]["entry_id"] == first_id
    assert parsed[1]["entry_id"] == second_id
    assert parsed[1]["findings"] == []


def test_empty_findings_and_actions_round_trip_as_empty_lists(tmp_path: Path) -> None:
    """The "none" placeholder used in the rendered markdown parses back to []."""
    ledger_path = str(tmp_path / "auto-resolutions.md")
    entry = _make_entry(findings=[], actions_taken=[])
    append_entry(ledger_path, entry)

    parsed = parse_entries(ledger_path)
    assert parsed[0]["findings"] == []
    assert parsed[0]["actions_taken"] == []


# ---------------------------------------------------------------------------
# Evidence by path only — no duplicated report content
# ---------------------------------------------------------------------------


def test_evidence_field_carries_paths_not_content(tmp_path: Path) -> None:
    """format_entry() never inlines file content — only the path strings the
    caller supplied travel into the rendered entry.
    """
    entry = _make_entry(
        evidence_paths=["/kanban/projects/p/tasks/TESTER-X/artifacts/report.md"]
    )
    rendered = format_entry(entry)
    assert "/kanban/projects/p/tasks/TESTER-X/artifacts/report.md" in rendered
    # The rendered block is short and structural — it cannot contain a
    # multi-paragraph report body because format_entry() never reads any
    # file; it only ever serializes fields already on the LedgerEntry.
    assert "Trigger:" in rendered
    assert rendered.count("\n") < 20


# ---------------------------------------------------------------------------
# append_entry atomicity
# ---------------------------------------------------------------------------


def test_append_entry_write_is_atomic_no_partial_file_left_behind(tmp_path: Path) -> None:
    """A failed write (target directory made read-only mid-flight) must not
    leave a half-written ledger or a stray temp file behind.
    """
    ledger_path = str(tmp_path / "auto-resolutions.md")
    append_entry(ledger_path, _make_entry())
    original_text = Path(ledger_path).read_text(encoding="utf-8")

    tmp_path.chmod(0o500)  # read+execute only: mkstemp inside this dir will fail
    try:
        with pytest.raises(LedgerError):
            append_entry(
                ledger_path,
                _make_entry(timestamp="2026-07-27T20:00:00Z", rc="v2.0.3"),
            )
    finally:
        tmp_path.chmod(0o700)

    # Original ledger content is untouched, and no stray temp file survives.
    assert Path(ledger_path).read_text(encoding="utf-8") == original_text
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".ledger_tmp_")]
    assert leftovers == []


# ---------------------------------------------------------------------------
# backfill_outcome — single-shot, idempotent, conflict-refusing
# ---------------------------------------------------------------------------


def test_backfill_outcome_sets_pending_entry_to_terminal_value(tmp_path: Path) -> None:
    ledger_path = str(tmp_path / "auto-resolutions.md")
    entry_id = append_entry(ledger_path, _make_entry())

    changed = backfill_outcome(ledger_path, entry_id, "SHIPPED attempt 2")
    assert changed is True

    record = parse_entries(ledger_path)[0]
    assert record["outcome"] == "SHIPPED attempt 2"


def test_backfill_outcome_is_idempotent_for_the_same_value(tmp_path: Path) -> None:
    ledger_path = str(tmp_path / "auto-resolutions.md")
    entry_id = append_entry(ledger_path, _make_entry())
    backfill_outcome(ledger_path, entry_id, "CEILING-BLOCKED")

    changed_again = backfill_outcome(ledger_path, entry_id, "CEILING-BLOCKED")
    assert changed_again is False
    record = parse_entries(ledger_path)[0]
    assert record["outcome"] == "CEILING-BLOCKED"


def test_backfill_outcome_refuses_to_overwrite_a_different_value(tmp_path: Path) -> None:
    ledger_path = str(tmp_path / "auto-resolutions.md")
    entry_id = append_entry(ledger_path, _make_entry())
    backfill_outcome(ledger_path, entry_id, "SHIPPED attempt 2")

    with pytest.raises(LedgerError):
        backfill_outcome(ledger_path, entry_id, "CEILING-BLOCKED")

    # The original backfilled value survives the refused overwrite attempt.
    record = parse_entries(ledger_path)[0]
    assert record["outcome"] == "SHIPPED attempt 2"


def test_backfill_outcome_raises_when_entry_id_not_found(tmp_path: Path) -> None:
    ledger_path = str(tmp_path / "auto-resolutions.md")
    append_entry(ledger_path, _make_entry())

    with pytest.raises(LedgerError):
        backfill_outcome(ledger_path, "entry-does-not-exist", "SHIPPED attempt 2")


def test_backfill_outcome_raises_when_ledger_file_missing(tmp_path: Path) -> None:
    ledger_path = str(tmp_path / "does-not-exist.md")
    with pytest.raises(LedgerError):
        backfill_outcome(ledger_path, "entry-anything", "SHIPPED attempt 1")


def test_backfill_outcome_does_not_touch_other_entries(tmp_path: Path) -> None:
    """Backfilling one entry's Outcome leaves a sibling entry's every field,
    including its own (still-pending) Outcome, byte-identical.
    """
    ledger_path = str(tmp_path / "auto-resolutions.md")
    first_id = append_entry(ledger_path, _make_entry())
    second_id = append_entry(
        ledger_path,
        _make_entry(timestamp="2026-07-27T19:10:00Z", rc="v2.0.2", attempt_from=2, attempt_to=3),
    )

    backfill_outcome(ledger_path, first_id, "SHIPPED attempt 2")

    parsed = {record["entry_id"]: record for record in parse_entries(ledger_path)}
    assert parsed[first_id]["outcome"] == "SHIPPED attempt 2"
    assert parsed[second_id]["outcome"] is None
    assert parsed[second_id]["rc"] == "v2.0.2"


# ---------------------------------------------------------------------------
# backfill_family_outcome — batch backfill across a bundle family
# ---------------------------------------------------------------------------


def test_backfill_family_outcome_updates_every_pending_entry_in_the_family(
    tmp_path: Path,
) -> None:
    ledger_path = str(tmp_path / "auto-resolutions.md")
    id1 = append_entry(ledger_path, _make_entry(family_key="family-abc"))
    id2 = append_entry(
        ledger_path,
        _make_entry(
            timestamp="2026-07-27T19:10:00Z",
            rc="v2.0.2",
            attempt_from=2,
            attempt_to=3,
            family_key="family-abc",
        ),
    )
    unrelated_id = append_entry(
        ledger_path,
        _make_entry(
            timestamp="2026-07-27T20:00:00Z",
            rc="v2.0.5",
            attempt_from=1,
            attempt_to=2,
            family_key="family-xyz",
        ),
    )

    updated = backfill_family_outcome(ledger_path, "family-abc", "SHIPPED attempt 3")

    assert sorted(updated) == sorted([id1, id2])
    parsed = {record["entry_id"]: record for record in parse_entries(ledger_path)}
    assert parsed[id1]["outcome"] == "SHIPPED attempt 3"
    assert parsed[id2]["outcome"] == "SHIPPED attempt 3"
    assert parsed[unrelated_id]["outcome"] is None


def test_backfill_family_outcome_skips_already_terminal_entries(tmp_path: Path) -> None:
    """Re-running the family backfill after one entry was already set returns
    only the entries that actually changed on this call.
    """
    ledger_path = str(tmp_path / "auto-resolutions.md")
    id1 = append_entry(ledger_path, _make_entry(family_key="family-abc"))
    id2 = append_entry(
        ledger_path,
        _make_entry(
            timestamp="2026-07-27T19:10:00Z",
            rc="v2.0.2",
            attempt_from=2,
            attempt_to=3,
            family_key="family-abc",
        ),
    )
    backfill_outcome(ledger_path, id1, "SHIPPED attempt 3")

    updated = backfill_family_outcome(ledger_path, "family-abc", "SHIPPED attempt 3")
    assert updated == [id2]


def test_find_entry_ids_by_family_returns_empty_for_unknown_family(tmp_path: Path) -> None:
    ledger_path = str(tmp_path / "auto-resolutions.md")
    append_entry(ledger_path, _make_entry(family_key="family-abc"))
    assert find_entry_ids_by_family(ledger_path, "family-does-not-exist") == []


def test_find_entry_ids_by_family_returns_empty_when_ledger_missing(tmp_path: Path) -> None:
    ledger_path = str(tmp_path / "does-not-exist.md")
    assert find_entry_ids_by_family(ledger_path, "family-abc") == []


# ---------------------------------------------------------------------------
# parse_entries — empty/missing ledger
# ---------------------------------------------------------------------------


def test_parse_entries_returns_empty_list_when_ledger_missing(tmp_path: Path) -> None:
    ledger_path = str(tmp_path / "does-not-exist.md")
    assert parse_entries(ledger_path) == []


# ---------------------------------------------------------------------------
# ledger_path_for_project — standard path convention
# ---------------------------------------------------------------------------


def test_ledger_path_for_project_follows_projects_dir_convention(tmp_path: Path) -> None:
    kanban_root = str(tmp_path)
    result = ledger_path_for_project(kanban_root, "pgai-agent-kanban")
    assert result == str(tmp_path / "projects" / "pgai-agent-kanban" / "auto-resolutions.md")
