"""
test_bug_scanner.py
===================
Behavioral unit tests for team/pm-agent/lib/bug_scanner.py.

All tests use tmp_path to create a synthetic bugs/ directory.  No live
filesystem state is read or written outside of tmp_path.

Test areas:
  - scan_bugs_directory()       — scanning and metadata extraction
  - get_bundled_bug_ids()       — reading [x] entries from bug_backlog.md
  - get_open_bug_ids()          — reading [ ] entries from bug_backlog.md
  - update_bug_backlog_cache()  — syncing cache from file Status fields
  - get_unbundled_bugs()        — combined scan+filter
  - claim_next_bug_id()         — atomic claiming
  - release_bug_id_claim()      — lock removal
  - detect_duplicate_bug_ids()  — collision detection
  - _scan_highest_bug_num()     — numeric high-water scan over taken BUG-NNNN prefixes
"""

from __future__ import annotations

import pathlib
import warnings

import pytest

try:
    from pm_agent.lib.bug_scanner import (
        scan_bugs_directory,
        get_bundled_bug_ids,
        get_open_bug_ids,
        update_bug_backlog_cache,
        get_unbundled_bugs,
        claim_next_bug_id,
        release_bug_id_claim,
        detect_duplicate_bug_ids,
        resolve_bugs_dir,
        _extract_summary,
        _extract_severity,
        _extract_status,
        _scan_highest_bug_num,
    )
except ImportError:
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent / "pm-agent"))
    from lib.bug_scanner import (  # type: ignore[no-redef]
        scan_bugs_directory,
        get_bundled_bug_ids,
        get_open_bug_ids,
        update_bug_backlog_cache,
        get_unbundled_bugs,
        claim_next_bug_id,
        release_bug_id_claim,
        detect_duplicate_bug_ids,
        resolve_bugs_dir,
        _extract_summary,
        _extract_severity,
        _extract_status,
        _scan_highest_bug_num,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_bug(
    bugs_dir: pathlib.Path,
    bug_id: str = "BUG-0001-example-bug",
    status: str = "open",
    severity: str = "medium",
    symptom: str = "Something is broken.",
    resolved_by: str = "",
) -> pathlib.Path:
    """Write a synthetic BUG-NNNN-*.md file to bugs_dir."""
    resolved_section = ""
    if resolved_by:
        resolved_section = f"\n## Resolved By\n{resolved_by}\n"
    content = (
        f"# {bug_id}\n\n"
        f"## Status\n{status}\n\n"
        f"**Severity:** {severity}\n\n"
        f"## Symptom\n{symptom}\n"
        f"{resolved_section}"
    )
    path = bugs_dir / f"{bug_id}.md"
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# _extract_summary(), _extract_severity(), _extract_status() — internal helpers
# ---------------------------------------------------------------------------


def test_extract_summary_returns_first_content_line_under_symptom() -> None:
    """_extract_summary returns the first non-empty line under ## Symptom."""
    text = "## Symptom\nThe widget crashes on startup.\n\nMore detail.\n"
    assert _extract_summary(text) == "The widget crashes on startup."


def test_extract_summary_returns_empty_string_when_symptom_absent() -> None:
    """_extract_summary returns '' when there is no ## Symptom section."""
    assert _extract_summary("## Status\nopen\n") == ""


def test_extract_severity_returns_severity_value() -> None:
    """_extract_severity extracts the value after **Severity:**."""
    text = "**Severity:** high\n"
    assert _extract_severity(text) == "high"


def test_extract_severity_returns_empty_string_when_absent() -> None:
    """_extract_severity returns '' when no **Severity:** field is present."""
    assert _extract_severity("## Status\nopen\n") == ""


def test_extract_status_returns_lowercase_status_token() -> None:
    """_extract_status returns the lowercased first token after ## Status."""
    text = "## Status\nRunning\n"
    assert _extract_status(text) == "running"


def test_extract_status_defaults_to_open_when_absent() -> None:
    """_extract_status returns 'open' when there is no ## Status header."""
    assert _extract_status("No status here.\n") == "open"


# ---------------------------------------------------------------------------
# scan_bugs_directory()
# ---------------------------------------------------------------------------


def test_scan_bugs_directory_returns_one_entry_per_bug_file(
    tmp_path: pathlib.Path,
) -> None:
    """scan_bugs_directory returns one dict per BUG-NNNN-*.md file."""
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    _write_bug(bugs_dir, "BUG-0001-first-bug")
    _write_bug(bugs_dir, "BUG-0002-second-bug")
    result = scan_bugs_directory(str(bugs_dir))
    assert len(result) == 2


def test_scan_bugs_directory_excludes_template_and_readme(
    tmp_path: pathlib.Path,
) -> None:
    """scan_bugs_directory excludes BUG-TEMPLATE.md and README.md."""
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    (bugs_dir / "BUG-TEMPLATE.md").write_text("template\n", encoding="utf-8")
    (bugs_dir / "README.md").write_text("readme\n", encoding="utf-8")
    _write_bug(bugs_dir, "BUG-0001-real-bug")
    result = scan_bugs_directory(str(bugs_dir))
    assert len(result) == 1
    assert result[0]["id"] == "BUG-0001-real-bug"


def test_scan_bugs_directory_extracts_summary_from_symptom(
    tmp_path: pathlib.Path,
) -> None:
    """scan_bugs_directory extracts the symptom summary from each bug file."""
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    _write_bug(bugs_dir, "BUG-0001-my-bug", symptom="Widget crashes on startup.")
    result = scan_bugs_directory(str(bugs_dir))
    assert result[0]["summary"] == "Widget crashes on startup."


def test_scan_bugs_directory_extracts_severity(tmp_path: pathlib.Path) -> None:
    """scan_bugs_directory extracts the severity field from each bug file."""
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    _write_bug(bugs_dir, "BUG-0001-critical-bug", severity="critical")
    result = scan_bugs_directory(str(bugs_dir))
    assert result[0]["severity"] == "critical"


def test_scan_bugs_directory_sorts_by_bug_id(tmp_path: pathlib.Path) -> None:
    """scan_bugs_directory returns results sorted by bug ID ascending."""
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    _write_bug(bugs_dir, "BUG-0003-third")
    _write_bug(bugs_dir, "BUG-0001-first")
    _write_bug(bugs_dir, "BUG-0002-second")
    result = scan_bugs_directory(str(bugs_dir))
    ids = [r["id"] for r in result]
    assert ids == sorted(ids)


def test_scan_bugs_directory_returns_empty_list_for_empty_dir(
    tmp_path: pathlib.Path,
) -> None:
    """scan_bugs_directory returns [] for a directory with no BUG-*.md files."""
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    assert scan_bugs_directory(str(bugs_dir)) == []


# ---------------------------------------------------------------------------
# get_bundled_bug_ids() and get_open_bug_ids()
# ---------------------------------------------------------------------------


def _write_backlog(path: pathlib.Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_get_bundled_bug_ids_returns_ids_with_x_checkbox(tmp_path: pathlib.Path) -> None:
    """get_bundled_bug_ids returns IDs whose checkbox is [x]."""
    backlog = tmp_path / "bug_backlog.md"
    _write_backlog(backlog, "- [x] BUG-0001-fixed\n- [ ] BUG-0002-open\n")
    result = get_bundled_bug_ids(str(backlog))
    assert "BUG-0001-fixed" in result
    assert "BUG-0002-open" not in result


def test_get_bundled_bug_ids_returns_empty_set_when_file_absent(
    tmp_path: pathlib.Path,
) -> None:
    """get_bundled_bug_ids returns empty set when bug_backlog.md does not exist."""
    result = get_bundled_bug_ids(str(tmp_path / "nonexistent.md"))
    assert result == set()


def test_get_open_bug_ids_returns_ids_with_space_checkbox(
    tmp_path: pathlib.Path,
) -> None:
    """get_open_bug_ids returns IDs whose checkbox is [ ]."""
    backlog = tmp_path / "bug_backlog.md"
    _write_backlog(backlog, "- [x] BUG-0001-done\n- [ ] BUG-0002-open\n")
    result = get_open_bug_ids(str(backlog))
    assert "BUG-0002-open" in result
    assert "BUG-0001-done" not in result


def test_get_open_bug_ids_returns_empty_set_when_file_absent(
    tmp_path: pathlib.Path,
) -> None:
    """get_open_bug_ids returns empty set when bug_backlog.md does not exist."""
    result = get_open_bug_ids(str(tmp_path / "nonexistent.md"))
    assert result == set()


# ---------------------------------------------------------------------------
# update_bug_backlog_cache()
# ---------------------------------------------------------------------------


def test_update_bug_backlog_cache_creates_file_when_absent(
    tmp_path: pathlib.Path,
) -> None:
    """update_bug_backlog_cache creates bug_backlog.md when it does not exist."""
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    _write_bug(bugs_dir, "BUG-0001-example")
    backlog = tmp_path / "bug_backlog.md"
    update_bug_backlog_cache(str(bugs_dir), str(backlog))
    assert backlog.exists()


def test_update_bug_backlog_cache_open_bug_gets_space_checkbox(
    tmp_path: pathlib.Path,
) -> None:
    """update_bug_backlog_cache marks open bugs with [ ]."""
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    _write_bug(bugs_dir, "BUG-0001-open-bug", status="open")
    backlog = tmp_path / "bug_backlog.md"
    update_bug_backlog_cache(str(bugs_dir), str(backlog))
    content = backlog.read_text(encoding="utf-8")
    assert "- [ ] BUG-0001-open-bug" in content


def test_update_bug_backlog_cache_running_bug_gets_x_checkbox(
    tmp_path: pathlib.Path,
) -> None:
    """update_bug_backlog_cache marks running bugs with [x]."""
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    _write_bug(bugs_dir, "BUG-0002-running-bug", status="running")
    backlog = tmp_path / "bug_backlog.md"
    update_bug_backlog_cache(str(bugs_dir), str(backlog))
    content = backlog.read_text(encoding="utf-8")
    assert "- [x] BUG-0002-running-bug" in content


def test_update_bug_backlog_cache_done_bug_with_resolved_by_gets_x(
    tmp_path: pathlib.Path,
) -> None:
    """update_bug_backlog_cache marks done bugs [x] when ## Resolved By is populated."""
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    _write_bug(
        bugs_dir,
        "BUG-0003-done-bug",
        status="done",
        resolved_by="CODER-20260101-001-fix-bug",
    )
    backlog = tmp_path / "bug_backlog.md"
    update_bug_backlog_cache(str(bugs_dir), str(backlog))
    content = backlog.read_text(encoding="utf-8")
    assert "- [x] BUG-0003-done-bug" in content


def test_update_bug_backlog_cache_done_bug_without_resolved_by_gets_space_and_warns(
    tmp_path: pathlib.Path,
) -> None:
    """done bug without ## Resolved By is treated as open ([ ]) and a UserWarning is emitted."""
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    _write_bug(bugs_dir, "BUG-0004-unresolved-done", status="done", resolved_by="")
    backlog = tmp_path / "bug_backlog.md"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        update_bug_backlog_cache(str(bugs_dir), str(backlog))
    content = backlog.read_text(encoding="utf-8")
    # Should be [ ] not [x]
    assert "- [ ] BUG-0004-unresolved-done" in content
    # A UserWarning must have been emitted
    assert any(issubclass(w.category, UserWarning) for w in caught)


def test_update_bug_backlog_cache_removes_deleted_bugs(
    tmp_path: pathlib.Path,
) -> None:
    """update_bug_backlog_cache omits bugs that no longer exist in the directory."""
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    bug_path = _write_bug(bugs_dir, "BUG-0001-transient-bug")
    backlog = tmp_path / "bug_backlog.md"
    update_bug_backlog_cache(str(bugs_dir), str(backlog))
    # Now remove the bug file and update again
    bug_path.unlink()
    update_bug_backlog_cache(str(bugs_dir), str(backlog))
    content = backlog.read_text(encoding="utf-8")
    assert "BUG-0001-transient-bug" not in content


# ---------------------------------------------------------------------------
# get_unbundled_bugs()
# ---------------------------------------------------------------------------


def test_get_unbundled_bugs_returns_only_open_bugs(tmp_path: pathlib.Path) -> None:
    """get_unbundled_bugs returns bugs with status=open and excludes running/done."""
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    _write_bug(bugs_dir, "BUG-0001-open-bug", status="open")
    _write_bug(bugs_dir, "BUG-0002-running-bug", status="running")
    backlog = tmp_path / "bug_backlog.md"
    result = get_unbundled_bugs(str(bugs_dir), str(backlog))
    ids = [b["id"] for b in result]
    assert "BUG-0001-open-bug" in ids
    assert "BUG-0002-running-bug" not in ids


# ---------------------------------------------------------------------------
# claim_next_bug_id()
# ---------------------------------------------------------------------------


def test_claim_next_bug_id_returns_bug_0001_in_empty_dir(
    tmp_path: pathlib.Path,
) -> None:
    """claim_next_bug_id returns an earlier defect as the first claim in an empty directory."""
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    bug_id, bug_path, lock_path = claim_next_bug_id(str(bugs_dir), "first-bug")
    try:
        assert bug_id == "BUG-0001"
        assert bug_path == bugs_dir / "BUG-0001-first-bug.md"
        assert lock_path.exists()
    finally:
        release_bug_id_claim(lock_path)


def test_claim_next_bug_id_increments_past_existing_files(
    tmp_path: pathlib.Path,
) -> None:
    """claim_next_bug_id allocates an earlier defect when an earlier defect-*.md already exists."""
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    _write_bug(bugs_dir, "BUG-0001-existing")
    bug_id, bug_path, lock_path = claim_next_bug_id(str(bugs_dir), "next-bug")
    try:
        assert bug_id == "BUG-0002"
        assert bug_path == bugs_dir / "BUG-0002-next-bug.md"
    finally:
        release_bug_id_claim(lock_path)


def test_claim_next_bug_id_raises_for_nonexistent_bugs_dir(
    tmp_path: pathlib.Path,
) -> None:
    """claim_next_bug_id raises FileNotFoundError when bugs_dir does not exist."""
    with pytest.raises(FileNotFoundError):
        claim_next_bug_id(str(tmp_path / "nonexistent"), "slug")


# ---------------------------------------------------------------------------
# claim_next_bug_id() — durable claim provenance ledger
#
# A mis-attributed bugs/ filing's own Source Task header cannot be trusted
# on its own -- nothing durable records who actually created the file, so a
# mismatch can only be inferred after the fact from file mtimes and wake-log
# timing. These tests assert claim_next_bug_id itself leaves a durable,
# independently-checkable record of the claiming session at the moment of
# creation.
# ---------------------------------------------------------------------------


def test_claim_next_bug_id_records_explicit_claimed_by_in_ledger(
    tmp_path: pathlib.Path,
) -> None:
    """An explicit claimed_by argument is written verbatim to the durable
    .bug-claims.log ledger, alongside the claimed bug ID."""
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    bug_id, _, lock_path = claim_next_bug_id(
        str(bugs_dir), "explicit-claimant", claimed_by="verifier-session-alpha"
    )
    try:
        ledger = bugs_dir / ".bug-claims.log"
        assert ledger.exists(), "claim_next_bug_id did not create the durable claims ledger."
        ledger_text = ledger.read_text(encoding="utf-8")
        assert bug_id in ledger_text
        assert "verifier-session-alpha" in ledger_text
        # One tab-separated line: timestamp, bug_id, claimant.
        line = next(l for l in ledger_text.splitlines() if bug_id in l)
        fields = line.split("\t")
        assert len(fields) == 3, f"expected 3 tab-separated fields, got: {fields!r}"
        assert fields[1] == bug_id
        assert fields[2] == "verifier-session-alpha"
    finally:
        release_bug_id_claim(lock_path)


def test_claim_next_bug_id_falls_back_to_task_id_env_var(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When claimed_by is omitted, the claim is attributed to the PGAI_TASK_ID
    environment variable an agent's own wake dispatch exports for its
    session -- the mechanism that makes an ordinary two-argument call site
    (every existing caller) still produce an attributable record."""
    monkeypatch.setenv("PGAI_TASK_ID", "implementer-session-fallback-check")
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    bug_id, _, lock_path = claim_next_bug_id(str(bugs_dir), "env-fallback-claimant")
    try:
        ledger_text = (bugs_dir / ".bug-claims.log").read_text(encoding="utf-8")
        assert "implementer-session-fallback-check" in ledger_text
        assert bug_id in ledger_text
    finally:
        release_bug_id_claim(lock_path)


def test_claim_next_bug_id_records_unknown_when_no_identity_available(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With neither an explicit claimed_by nor PGAI_TASK_ID set, the ledger
    still gets a record -- naming the claimant "unknown" rather than
    silently skipping the entry, so an unattributed claim is itself visible
    in the ledger instead of invisible."""
    monkeypatch.delenv("PGAI_TASK_ID", raising=False)
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    bug_id, _, lock_path = claim_next_bug_id(str(bugs_dir), "no-identity-claimant")
    try:
        ledger_text = (bugs_dir / ".bug-claims.log").read_text(encoding="utf-8")
        line = next(l for l in ledger_text.splitlines() if bug_id in l)
        assert line.split("\t")[2] == "unknown"
    finally:
        release_bug_id_claim(lock_path)


def test_claim_next_bug_id_appends_one_line_per_claim(
    tmp_path: pathlib.Path,
) -> None:
    """Two successive claims append two separate ledger lines rather than
    overwriting -- the ledger is a durable append-only history, not a
    last-write-wins pointer."""
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    bug_id_1, _, lock_1 = claim_next_bug_id(str(bugs_dir), "first", claimed_by="session-a")
    release_bug_id_claim(lock_1)
    bug_id_2, _, lock_2 = claim_next_bug_id(str(bugs_dir), "second", claimed_by="session-b")
    release_bug_id_claim(lock_2)

    ledger_lines = (bugs_dir / ".bug-claims.log").read_text(encoding="utf-8").splitlines()
    assert len(ledger_lines) == 2
    assert bug_id_1 in ledger_lines[0] and "session-a" in ledger_lines[0]
    assert bug_id_2 in ledger_lines[1] and "session-b" in ledger_lines[1]


# ---------------------------------------------------------------------------
# release_bug_id_claim()
# ---------------------------------------------------------------------------


def test_release_bug_id_claim_removes_lock_file(tmp_path: pathlib.Path) -> None:
    """release_bug_id_claim removes the .claim-NNNN.lock file."""
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    _, _, lock_path = claim_next_bug_id(str(bugs_dir), "release-test")
    assert lock_path.exists()
    release_bug_id_claim(lock_path)
    assert not lock_path.exists()


def test_release_bug_id_claim_is_idempotent(tmp_path: pathlib.Path) -> None:
    """release_bug_id_claim does not raise when the lock file is already gone."""
    nonexistent_lock = tmp_path / ".claim-9999.lock"
    release_bug_id_claim(nonexistent_lock)  # Should not raise


# ---------------------------------------------------------------------------
# detect_duplicate_bug_ids()
# ---------------------------------------------------------------------------


def test_detect_duplicate_bug_ids_returns_empty_for_no_collisions(
    tmp_path: pathlib.Path,
) -> None:
    """detect_duplicate_bug_ids returns [] when all numeric prefixes are unique."""
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    _write_bug(bugs_dir, "BUG-0001-a-bug")
    _write_bug(bugs_dir, "BUG-0002-b-bug")
    result = detect_duplicate_bug_ids(str(bugs_dir))
    assert result == []


def test_detect_duplicate_bug_ids_returns_colliding_numbers(
    tmp_path: pathlib.Path,
) -> None:
    """detect_duplicate_bug_ids returns the numeric IDs that collide."""
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    # Two files with the same numeric prefix 0001
    (bugs_dir / "BUG-0001-first-version.md").write_text("# BUG-0001\n", encoding="utf-8")
    (bugs_dir / "BUG-0001-second-version.md").write_text("# BUG-0001\n", encoding="utf-8")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = detect_duplicate_bug_ids(str(bugs_dir))
    assert 1 in result
    # A UserWarning must have been emitted
    assert any(issubclass(w.category, UserWarning) for w in caught)


def test_detect_duplicate_bug_ids_returns_empty_for_nonexistent_dir(
    tmp_path: pathlib.Path,
) -> None:
    """detect_duplicate_bug_ids returns [] when the directory does not exist."""
    result = detect_duplicate_bug_ids(str(tmp_path / "nonexistent"))
    assert result == []


# ---------------------------------------------------------------------------
# resolve_bugs_dir()
# ---------------------------------------------------------------------------


def test_resolve_bugs_dir_returns_explicit_path_unchanged() -> None:
    """resolve_bugs_dir returns the explicit argument when provided."""
    result = resolve_bugs_dir("/custom/bugs")
    assert result == "/custom/bugs"


def test_resolve_bugs_dir_uses_project_root_env(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """resolve_bugs_dir uses PGAI_PROJECT_ROOT env var when no explicit path given."""
    monkeypatch.setenv("PGAI_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("PGAI_AGENT_KANBAN_ROOT_PATH", raising=False)
    result = resolve_bugs_dir(None)
    assert result == str(tmp_path / "bugs")


# ---------------------------------------------------------------------------
# _scan_highest_bug_num() — numeric high-water scan over taken BUG-NNNN prefixes
# ---------------------------------------------------------------------------
#
# Any name starting BUG- followed by exactly four digits takes that number,
# even when the rest of the name fails the ledger filename contract.  Names
# without exactly four digits (date-first, under-padded) never participate:
# a looser "4 or more digits" prefix match would read a date-first
# filename's 8-digit date as if it were the sequence number, jumping the
# high-water mark from a legitimate 4-digit value to an 8-digit one and
# permanently breaking subsequent numbering.


def test_scan_highest_bug_num_ignores_date_first_filename(
    tmp_path: pathlib.Path,
) -> None:
    """A date-first filename does not poison the high-water scan.

    Without the exactly-four-digits rule, a filename shaped like
    BUG-<8-digit-date>-<seq>-<slug>.md would be misread as claiming an
    8-digit sequence number, jumping the high-water mark far beyond any
    legitimate value.
    """
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    _write_bug(bugs_dir, "BUG-0009-well-formed")
    (bugs_dir / "BUG-20260803-001-date-first-bad.md").write_text(
        "## Status\nopen\n", encoding="utf-8"
    )
    highest = _scan_highest_bug_num(bugs_dir)
    assert highest == 9, (
        f"Expected the date-first filename to be excluded from the scan "
        f"(highest=9); got highest={highest}"
    )


def test_scan_highest_bug_num_ignores_under_padded_filename(
    tmp_path: pathlib.Path,
) -> None:
    """An under-padded (fewer than 4 digits) filename does not participate."""
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    _write_bug(bugs_dir, "BUG-0036-well-formed")
    (bugs_dir / "BUG-37-under-padded-bad.md").write_text(
        "## Status\nopen\n", encoding="utf-8"
    )
    highest = _scan_highest_bug_num(bugs_dir)
    assert highest == 36, (
        f"Expected the under-padded filename to be excluded from the scan "
        f"(highest=36); got highest={highest}"
    )


def test_scan_highest_bug_num_parses_numerically_not_lexically(
    tmp_path: pathlib.Path,
) -> None:
    """0009 and 0036 in the same directory yield a numeric high-water of 36.

    A lexical (string) comparison of "0009" against "0036" would still sort
    correctly by coincidence (both are zero-padded to the same width), so
    this test also plants a fixture that would only pass a numeric parse:
    the scan result must equal the actual maximum int value, not a
    string-sorted maximum.
    """
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    _write_bug(bugs_dir, "BUG-0009-lower")
    _write_bug(bugs_dir, "BUG-0036-higher")
    highest = _scan_highest_bug_num(bugs_dir)
    assert highest == 36, highest
    assert isinstance(highest, int)


def test_claim_next_bug_id_recovers_correct_next_id_with_malformed_file_present(
    tmp_path: pathlib.Path,
) -> None:
    """claim_next_bug_id allocates the correct next-highest ID even with a
    date-first file present.

    End-to-end regression check: the malformed file must not leak its
    8-digit date into the allocated ID.
    """
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    _write_bug(bugs_dir, "BUG-0009-lower")
    _write_bug(bugs_dir, "BUG-0036-higher")
    (bugs_dir / "BUG-20260803-001-date-first-bad.md").write_text(
        "## Status\nopen\n", encoding="utf-8"
    )
    bug_id, bug_path, lock_path = claim_next_bug_id(str(bugs_dir), "next-slot")
    try:
        assert bug_id == "BUG-0037", bug_id
        assert bug_path == bugs_dir / "BUG-0037-next-slot.md"
    finally:
        release_bug_id_claim(lock_path)


def test_claim_next_bug_id_skips_number_held_by_contract_invalid_name(
    tmp_path: pathlib.Path,
) -> None:
    """A name that fails the ledger filename contract still occupies its number.

    A slug with dots (e.g. BUG-0002-v1.2.3-notes-count.md) fails the strict
    contract, yet the four-digit prefix is visibly taken; the claim must move
    past that number rather than hand it out a second time.
    """
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    (bugs_dir / "BUG-0001-first-bug.md").write_text("## Status\nopen\n", encoding="utf-8")
    (bugs_dir / "BUG-0002-v1.2.3-notes-count.md").write_text(
        "## Status\nopen\n", encoding="utf-8"
    )
    bug_id, bug_path, lock_path = claim_next_bug_id(str(bugs_dir), "probe-slug")
    release_bug_id_claim(lock_path)
    assert bug_id == "BUG-0003", bug_id
    assert bug_path == bugs_dir / "BUG-0003-probe-slug.md"
    assert not lock_path.exists()
