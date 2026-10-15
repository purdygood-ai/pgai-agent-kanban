"""
test_deep_verify_ledger.py
============================
Unit tests for pgai_agent_kanban.deep_verify.ledger — the append-only
per-project deep-verify record: the library that writes and reads the
per-project deep-verify record, meant to be the one reader every other
consumer calls rather than re-deriving the record's shape.

Coverage:
  - latest_record() on a project with no ledger file at all returns None
    ("never run"), distinguishable from a green run without parsing prose.
  - append_record() creates the ledger with its standard header plus one
    record; a second append preserves the first record byte-for-byte and
    adds the new one (never interleaved, never overwritten).
  - latest_record() returns the MOST RECENT record after multiple appends.
  - Each of the three verdict classes round-trips through append/parse
    exactly, and a built record and a could-not-attempt record are
    distinguishable by verdict field value alone.
  - append_record() refuses an out-of-vocabulary verdict and writes
    nothing (the ledger file is untouched, or not created).
  - latest_record() raises LedgerError when the ledger file has
    non-whitespace content that does not parse into any record at all
    (malformed, distinct from "never run").
  - latest_record() raises LedgerError when the most recent record is
    missing its Verdict field or carries an out-of-vocabulary value (a
    hand-edited or corrupted ledger).
  - ledger_path_for_project() returns the documented path convention.

All tests use tmp_path; no live filesystem paths. Function names describe
behaviour under test, never bug/task IDs (roles/TESTER.md "Test Authoring
Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pgai_agent_kanban.deep_verify.ledger import (
    DeepVerifyRecord,
    LedgerError,
    append_record,
    latest_record,
    ledger_path_for_project,
    parse_records,
)


def _make_record(**overrides) -> DeepVerifyRecord:
    defaults = dict(
        timestamp="2026-09-03T09:15:22+00:00",
        project="pgai-agent-kanban",
        version="v2.9.12",
        commit="e240ee1",
        verdict="built",
        detail="docker/claude-code: built; podman/claude-code: built",
    )
    defaults.update(overrides)
    return DeepVerifyRecord(**defaults)


# ---------------------------------------------------------------------------
# Never-run vs ran-and-was-green
# ---------------------------------------------------------------------------


def test_latest_record_on_missing_ledger_is_none(tmp_path: Path) -> None:
    ledger_path = str(tmp_path / "deep-verify-ledger.md")
    assert latest_record(ledger_path) is None


def test_latest_record_on_empty_ledger_file_is_none(tmp_path: Path) -> None:
    ledger_path = tmp_path / "deep-verify-ledger.md"
    ledger_path.write_text("", encoding="utf-8")
    assert latest_record(str(ledger_path)) is None


def test_never_run_and_ran_green_are_distinguishable_without_parsing_prose(
    tmp_path: Path,
) -> None:
    ledger_path = str(tmp_path / "deep-verify-ledger.md")
    assert latest_record(ledger_path) is None

    append_record(ledger_path, _make_record(verdict="built"))
    record = latest_record(ledger_path)
    assert record is not None
    assert record["verdict"] == "built"


# ---------------------------------------------------------------------------
# append_record — creation, append-only, ordering
# ---------------------------------------------------------------------------


def test_append_record_creates_ledger_with_header_and_one_record(tmp_path: Path) -> None:
    ledger_path = str(tmp_path / "deep-verify-ledger.md")
    append_record(ledger_path, _make_record())

    text = Path(ledger_path).read_text(encoding="utf-8")
    assert "Append-only" in text
    assert text.count("## Deep-Verify Run:") == 1


def test_second_append_preserves_first_record_and_adds_second(tmp_path: Path) -> None:
    ledger_path = str(tmp_path / "deep-verify-ledger.md")
    append_record(ledger_path, _make_record(commit="aaa1111", verdict="built"))
    append_record(
        ledger_path,
        _make_record(
            timestamp="2026-09-04T09:15:22+00:00",
            commit="bbb2222",
            verdict="failed-to-build",
            detail="docker/claude-code: failed at step: RUN npm ci",
        ),
    )

    records = parse_records(ledger_path)
    assert len(records) == 2
    assert records[0]["commit"] == "aaa1111"
    assert records[0]["verdict"] == "built"
    assert records[1]["commit"] == "bbb2222"
    assert records[1]["verdict"] == "failed-to-build"


def test_latest_record_returns_most_recent_after_multiple_appends(tmp_path: Path) -> None:
    ledger_path = str(tmp_path / "deep-verify-ledger.md")
    append_record(ledger_path, _make_record(commit="aaa1111", verdict="built"))
    append_record(
        ledger_path,
        _make_record(
            timestamp="2026-09-04T09:15:22+00:00",
            commit="bbb2222",
            verdict="could-not-attempt",
            detail="podman engine not found",
        ),
    )

    latest = latest_record(ledger_path)
    assert latest is not None
    assert latest["commit"] == "bbb2222"
    assert latest["verdict"] == "could-not-attempt"


# ---------------------------------------------------------------------------
# All three verdict classes round-trip and are distinguishable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("verdict", ["built", "failed-to-build", "could-not-attempt"])
def test_each_verdict_class_round_trips_through_append_and_parse(
    tmp_path: Path, verdict: str
) -> None:
    ledger_path = str(tmp_path / "deep-verify-ledger.md")
    append_record(ledger_path, _make_record(verdict=verdict, detail=f"detail for {verdict}"))
    record = latest_record(ledger_path)
    assert record is not None
    assert record["verdict"] == verdict
    assert record["detail"] == f"detail for {verdict}"


def test_built_and_could_not_attempt_records_distinguishable_by_field_alone(
    tmp_path: Path,
) -> None:
    built_ledger = str(tmp_path / "built.md")
    skipped_ledger = str(tmp_path / "skipped.md")
    append_record(built_ledger, _make_record(verdict="built"))
    append_record(
        skipped_ledger,
        _make_record(verdict="could-not-attempt", detail="docker engine not found"),
    )

    built_record = latest_record(built_ledger)
    skipped_record = latest_record(skipped_ledger)
    assert built_record["verdict"] != skipped_record["verdict"]
    assert built_record["verdict"] == "built"
    assert skipped_record["verdict"] == "could-not-attempt"


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_append_record_refuses_out_of_vocabulary_verdict(tmp_path: Path) -> None:
    ledger_path = tmp_path / "deep-verify-ledger.md"
    with pytest.raises(LedgerError):
        append_record(str(ledger_path), _make_record(verdict="green"))
    assert not ledger_path.exists()


def test_latest_record_raises_on_non_whitespace_unparseable_content(tmp_path: Path) -> None:
    ledger_path = tmp_path / "deep-verify-ledger.md"
    ledger_path.write_text("this is not a deep-verify ledger at all\n", encoding="utf-8")
    with pytest.raises(LedgerError):
        latest_record(str(ledger_path))


def test_latest_record_raises_when_most_recent_record_missing_verdict(tmp_path: Path) -> None:
    ledger_path = tmp_path / "deep-verify-ledger.md"
    ledger_path.write_text(
        "# Deep-Verify Ledger — demo\n\n"
        "## Deep-Verify Run: 2026-09-03T09:15:22+00:00 — demo — v2.9.12\n"
        "- Commit: e240ee1\n"
        "- Detail: no verdict line at all\n",
        encoding="utf-8",
    )
    with pytest.raises(LedgerError):
        latest_record(str(ledger_path))


def test_latest_record_raises_when_most_recent_record_has_unknown_verdict(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "deep-verify-ledger.md"
    ledger_path.write_text(
        "# Deep-Verify Ledger — demo\n\n"
        "## Deep-Verify Run: 2026-09-03T09:15:22+00:00 — demo — v2.9.12\n"
        "- Commit: e240ee1\n"
        "- Verdict: green\n"
        "- Detail: hand-edited\n",
        encoding="utf-8",
    )
    with pytest.raises(LedgerError):
        latest_record(str(ledger_path))


def test_older_malformed_record_does_not_block_a_well_formed_latest_record(
    tmp_path: Path,
) -> None:
    """Only the MOST RECENT record's shape gates latest_record() -- an
    older record hand-edited into a bad state does not poison every read
    forever once a fresh, well-formed run has landed on top of it."""
    ledger_path = tmp_path / "deep-verify-ledger.md"
    ledger_path.write_text(
        "# Deep-Verify Ledger — demo\n\n"
        "## Deep-Verify Run: 2026-09-03T09:15:22+00:00 — demo — v2.9.12\n"
        "- Commit: e240ee1\n"
        "- Verdict: green\n"
        "- Detail: hand-edited\n\n"
        "## Deep-Verify Run: 2026-09-04T09:15:22+00:00 — demo — v2.9.12\n"
        "- Commit: f351ff2\n"
        "- Verdict: built\n"
        "- Detail: docker/claude-code: built\n",
        encoding="utf-8",
    )
    record = latest_record(str(ledger_path))
    assert record is not None
    assert record["verdict"] == "built"
    assert record["commit"] == "f351ff2"


# ---------------------------------------------------------------------------
# Path convention
# ---------------------------------------------------------------------------


def test_ledger_path_for_project_matches_documented_convention(tmp_path: Path) -> None:
    path = ledger_path_for_project(str(tmp_path), "pgai-agent-kanban")
    assert path == str(tmp_path / "projects" / "pgai-agent-kanban" / "deep-verify-ledger.md")
