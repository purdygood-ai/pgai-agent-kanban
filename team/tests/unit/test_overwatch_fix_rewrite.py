"""
test_overwatch_fix_rewrite.py
==============================
Unit tests for pgai_agent_kanban.overwatch.fix_rewrite — the Lane A
Fix-rewrite step: given a bug file whose ``## Fix`` section admitted two
readings and produced a regression, append a single-reading,
OVERWATCH-attributed rewrite below it.

Coverage:
  - The rewritten Fix section explicitly names the failed approach as
    forbidden.
  - The rewritten section reproduces at least one ground-truth reference
    verbatim.
  - The original ``## Fix`` section (and every other section) is present
    and byte-identical after the rewrite.
  - The appended block carries OVERWATCH attribution and a timestamp.
  - A backup of the pre-rewrite bug file exists under the project's
    overwatch/backups/ tree and is named in the action log.
  - Preconditions fail loud: no bug file, no existing Fix section, no
    forbidden approach named, no ground-truth reference supplied.
  - find_bug_file() resolves a short id to its one bugs/ file.
"""

from __future__ import annotations

import pathlib

import pytest

from pgai_agent_kanban.overwatch.fix_rewrite import (
    FixRewriteError,
    backup_bug_file,
    find_bug_file,
    rewrite_bug_fix_section,
)

_TIMESTAMP = "2026-09-15T14:00:00Z"

_ORIGINAL_BUG = """# BUG-0163-inverted-column-check

**Bug ID:** BUG-0163-inverted-column-check
**Filed By:** TESTER
**Date:** 2026-09-04
**Severity:** high

## Status
open

## Category
regression

---

## Symptom
The dashboard's active-column count is wrong after the fix landed.

## Expected
Active tasks are counted when their state is WORKING or BLOCKED.

## Actual
Active tasks are counted when their state is NOT WORKING or BLOCKED — the
condition reads backward.

## Fix
Invert the state check in src/dashboard/column.py's is_active() so it
returns true for WORKING/BLOCKED tasks (the check currently reads
backward, or possibly the caller's polarity is backward — either reading
resolves the symptom).

## Acceptance
- is_active() returns true only for WORKING/BLOCKED tasks.

## Severity
high

## Fixed In
"""


def _write_bug(bugs_dir: pathlib.Path, name: str = "BUG-0163-inverted-column-check.md") -> pathlib.Path:
    bugs_dir.mkdir(parents=True, exist_ok=True)
    path = bugs_dir / name
    path.write_text(_ORIGINAL_BUG, encoding="utf-8")
    return path


def _rewrite(bug_path, kanban_root, project_name="proj", **overrides):
    kwargs = dict(
        forbidden_approach=(
            "inverting is_active()'s own condition in src/dashboard/column.py "
            "(this was implemented in the regressed RC)"
        ),
        correct_direction=(
            "invert the CALLER's polarity at src/dashboard/render.py:88 "
            "(`if not is_active(task):`  ->  `if is_active(task):`); "
            "is_active() itself is correct and must not change"
        ),
        ground_truth_refs=[
            "commit 5db7045: \"is_active() returns true for WORKING/BLOCKED — unchanged, correct\"",
            "src/dashboard/render.py:88 (pre-fix, verbatim): `if not is_active(task):`",
        ],
        kanban_root=str(kanban_root),
        project_name=project_name,
        timestamp=_TIMESTAMP,
    )
    kwargs.update(overrides)
    return rewrite_bug_fix_section(str(bug_path), **kwargs)


# ---------------------------------------------------------------------------
# Acceptance: forbidden approach named
# ---------------------------------------------------------------------------


def test_rewrite_names_the_failed_approach_as_forbidden(tmp_path: pathlib.Path) -> None:
    bug_path = _write_bug(tmp_path / "bugs")

    result = _rewrite(bug_path, tmp_path / "kanban_root")

    assert "FORBIDDEN" in result.appended_block
    assert "inverting is_active()'s own condition in src/dashboard/column.py" in result.appended_block
    # The forbidden-approach text and the word "FORBIDDEN" both land in the
    # rewritten file on disk, not merely in the in-memory result.
    rewritten = bug_path.read_text(encoding="utf-8")
    assert "FORBIDDEN" in rewritten
    assert "inverting is_active()'s own condition in src/dashboard/column.py" in rewritten


# ---------------------------------------------------------------------------
# Acceptance: verbatim ground-truth reference present
# ---------------------------------------------------------------------------


def test_rewrite_reproduces_ground_truth_reference_verbatim(tmp_path: pathlib.Path) -> None:
    bug_path = _write_bug(tmp_path / "bugs")
    ref = "commit 5db7045: \"is_active() returns true for WORKING/BLOCKED — unchanged, correct\""

    result = _rewrite(bug_path, tmp_path / "kanban_root", ground_truth_refs=[ref])

    assert ref in result.appended_block
    assert ref in bug_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Acceptance: original Fix section (and every other section) preserved
# ---------------------------------------------------------------------------


def test_original_fix_section_and_every_other_section_unchanged(tmp_path: pathlib.Path) -> None:
    bug_path = _write_bug(tmp_path / "bugs")

    _rewrite(bug_path, tmp_path / "kanban_root")

    rewritten = bug_path.read_text(encoding="utf-8")
    assert rewritten.startswith(_ORIGINAL_BUG.rstrip("\n"))
    # The original Fix section's exact wording is untouched, not edited in place.
    assert (
        "Invert the state check in src/dashboard/column.py's is_active() so it\n"
        "returns true for WORKING/BLOCKED tasks (the check currently reads\n"
        "backward, or possibly the caller's polarity is backward — either reading\n"
        "resolves the symptom)."
    ) in rewritten
    # Every other section — Status, Acceptance, Severity, Fixed In — is present
    # and unchanged.
    assert "## Status\nopen" in rewritten
    assert "## Acceptance\n- is_active() returns true only for WORKING/BLOCKED tasks." in rewritten
    assert "## Severity\nhigh" in rewritten
    assert "## Fixed In" in rewritten


# ---------------------------------------------------------------------------
# Acceptance: OVERWATCH attribution and timestamp
# ---------------------------------------------------------------------------


def test_appended_block_carries_overwatch_attribution_and_timestamp(tmp_path: pathlib.Path) -> None:
    bug_path = _write_bug(tmp_path / "bugs")

    result = _rewrite(bug_path, tmp_path / "kanban_root")

    assert "OVERWATCH" in result.appended_block
    assert _TIMESTAMP in result.appended_block
    assert result.timestamp == _TIMESTAMP


# ---------------------------------------------------------------------------
# Acceptance: backup exists and is named in the action log
# ---------------------------------------------------------------------------


def test_backup_exists_and_is_named_in_the_action_log(tmp_path: pathlib.Path) -> None:
    bug_path = _write_bug(tmp_path / "bugs")
    kanban_root = tmp_path / "kanban_root"

    result = _rewrite(bug_path, kanban_root, project_name="proj")

    backup_path = pathlib.Path(result.backup_path)
    assert backup_path.is_file()
    assert backup_path.parent.parent.name == "backups"
    assert backup_path.read_text(encoding="utf-8") == _ORIGINAL_BUG

    action_log = kanban_root / "projects" / "proj" / "overwatch" / "actions.log"
    assert action_log.is_file()
    log_text = action_log.read_text(encoding="utf-8")
    assert result.backup_path in log_text
    assert "fix-rewrite" in log_text


def test_backup_bug_file_is_idempotent_within_the_same_timestamp(tmp_path: pathlib.Path) -> None:
    bug_path = _write_bug(tmp_path / "bugs")
    kanban_root = tmp_path / "kanban_root"

    first = backup_bug_file(str(bug_path), str(kanban_root), "proj", timestamp=_TIMESTAMP)
    second = backup_bug_file(str(bug_path), str(kanban_root), "proj", timestamp=_TIMESTAMP)

    assert first == second
    assert pathlib.Path(first).is_file()


# ---------------------------------------------------------------------------
# Fail-loud preconditions
# ---------------------------------------------------------------------------


def test_missing_bug_file_raises(tmp_path: pathlib.Path) -> None:
    with pytest.raises(FixRewriteError):
        _rewrite(tmp_path / "bugs" / "BUG-9999-does-not-exist.md", tmp_path / "kanban_root")


def test_bug_file_with_no_fix_section_raises(tmp_path: pathlib.Path) -> None:
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir(parents=True)
    bug_path = bugs_dir / "BUG-0001-no-fix-section.md"
    bug_path.write_text("# BUG-0001-no-fix-section\n\n## Status\nopen\n", encoding="utf-8")

    with pytest.raises(FixRewriteError, match="no '## Fix' section"):
        _rewrite(bug_path, tmp_path / "kanban_root")


def test_empty_forbidden_approach_raises(tmp_path: pathlib.Path) -> None:
    bug_path = _write_bug(tmp_path / "bugs")

    with pytest.raises(FixRewriteError, match="forbidden_approach"):
        _rewrite(bug_path, tmp_path / "kanban_root", forbidden_approach="   ")


def test_empty_ground_truth_refs_raises(tmp_path: pathlib.Path) -> None:
    bug_path = _write_bug(tmp_path / "bugs")

    with pytest.raises(FixRewriteError, match="ground_truth_refs"):
        _rewrite(bug_path, tmp_path / "kanban_root", ground_truth_refs=[])


# ---------------------------------------------------------------------------
# find_bug_file() — short id to path resolution
# ---------------------------------------------------------------------------


def test_find_bug_file_resolves_id_to_the_one_matching_file(tmp_path: pathlib.Path) -> None:
    bugs_dir = tmp_path / "bugs"
    _write_bug(bugs_dir, "BUG-0163-inverted-column-check.md")
    (bugs_dir / "BUG-0007-unrelated.md").write_text("# BUG-0007-unrelated\n", encoding="utf-8")

    found = find_bug_file(str(bugs_dir), "BUG-0163")

    assert found == str(bugs_dir / "BUG-0163-inverted-column-check.md")


def test_find_bug_file_returns_none_when_no_match(tmp_path: pathlib.Path) -> None:
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir(parents=True)

    assert find_bug_file(str(bugs_dir), "BUG-9999") is None


def test_find_bug_file_returns_none_for_missing_directory(tmp_path: pathlib.Path) -> None:
    assert find_bug_file(str(tmp_path / "no-such-dir"), "BUG-0001") is None
