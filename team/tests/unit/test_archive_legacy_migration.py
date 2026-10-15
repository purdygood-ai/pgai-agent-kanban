"""
test_archive_legacy_migration.py
================================
Tests for folding the in-project requirements archive older installs kept
at ``projects/<p>/tasks/archive/requirements/`` into
``archive/<p>/`` (``pgai_agent_kanban.archive.legacy``), as the archive run
(``archive.run.run_archive``, what ``scripts/archive.sh`` and
``POST /operations/archive`` call) performs it.

Locked contracts:

  1. An archive run moves every file under the legacy directory, at any
     depth, into ``archive/<p>/requirements/`` -- and the PRIORITY-NNNN
     items held there into ``archive/<p>/priority/`` -- with one ledger row
     per file naming its category, ID, legacy path and archive path. The
     legacy directory is left with no file in it.
  2. A second run migrates nothing and writes no ledger row; a project
     with no legacy directory runs cleanly and writes no ledger.
  3. A migrated requirement is found by ID in the archive and can be
     restored to the live project.
  4. A destination name already taken in the archive is never overwritten:
     both copies are kept, the moved one under ``<stem>.legacy-<n><suffix>``.
  5. ``--dry-run`` lists the files that would move and changes nothing;
     ``archive_count`` 0 migrates nothing.
  6. A held repo lock skips the migration with nothing moved.

Every test builds its own sandbox kanban root under pytest's tmp_path
(``tests/fixtures/archive_sandbox.py``) and hands it to the code under test
explicitly. Nothing here points the archive at the live install.
"""

from __future__ import annotations

import os
import pathlib

from pgai_agent_kanban.archive import find_archived, read_rows
from pgai_agent_kanban.archive.apply import RESULT_OK, RESULT_SKIPPED
from pgai_agent_kanban.archive.legacy import LEGACY_REQUIREMENTS_DIR, migrate_legacy
from pgai_agent_kanban.archive.run import EXIT_OK, run_archive
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.locks import acquire_lock, repo_lock_path
from tests.fixtures.archive_sandbox import ArchiveSandbox

# Legacy files the sandbox seeds, relative to the legacy directory, and
# where each must land, relative to archive/<project>/.
_SEEDED = {
    "v1.0.0-first-feature.md": "requirements/v1.0.0-first-feature.md",
    "v1.0.1-bugfix-bundle-20260301.md": "requirements/v1.0.1-bugfix-bundle-20260301.md",
    "priority/v1.0.2-priority-bundle-20260302.md": "requirements/v1.0.2-priority-bundle-20260302.md",
    "priority/PRIORITY-0007-urgent-fix.md": "priority/PRIORITY-0007-urgent-fix.md",
    "notes/release-notes.txt": "requirements/release-notes.txt",
}

# The ledger ID each seeded file is recorded under.
_LEDGER_IDS = {
    "v1.0.0-first-feature.md": "v1.0.0-first-feature.md",
    "v1.0.1-bugfix-bundle-20260301.md": "v1.0.1-bugfix-bundle-20260301.md",
    "priority/v1.0.2-priority-bundle-20260302.md": "v1.0.2-priority-bundle-20260302.md",
    "priority/PRIORITY-0007-urgent-fix.md": "PRIORITY-0007",
    "notes/release-notes.txt": "release-notes.txt",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sandbox(tmp_path: pathlib.Path) -> ArchiveSandbox:
    """A sandbox project with every seeded legacy file, each holding its own path."""
    sandbox = ArchiveSandbox(tmp_path)
    for relpath in _SEEDED:
        path = _legacy_dir(sandbox) / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"legacy content of {relpath}\n", encoding="utf-8")
    return sandbox


def _legacy_dir(sandbox: ArchiveSandbox) -> pathlib.Path:
    return sandbox.project_root / LEGACY_REQUIREMENTS_DIR


def _archive_dir(sandbox: ArchiveSandbox) -> pathlib.Path:
    return sandbox.root / "archive" / sandbox.project


def _ledger(sandbox: ArchiveSandbox) -> pathlib.Path:
    return _archive_dir(sandbox) / "ARCHIVE-LEDGER.tsv"


def _files_under(directory: pathlib.Path) -> list[pathlib.Path]:
    return sorted(path for path in directory.rglob("*") if not path.is_dir())


def _run(sandbox: ArchiveSandbox, **kwargs):
    out: list[str] = []
    err: list[str] = []
    run = run_archive(
        OpsContext(kanban_root=sandbox.root), sandbox.project, out=out.append, err=err.append, **kwargs
    )
    return run, out, err


def _diagnostic(out: list[str], err: list[str]) -> str:
    return "--- out ---\n" + "\n".join(out) + "\n--- err ---\n" + "\n".join(err)


# ---------------------------------------------------------------------------
# 1. Every legacy file moves, with a ledger row, and the directory empties
# ---------------------------------------------------------------------------


def test_archive_run_moves_every_legacy_file_with_one_ledger_row_each(tmp_path: pathlib.Path) -> None:
    sandbox = _sandbox(tmp_path)

    run, out, err = _run(sandbox)

    assert run.exit_code == EXIT_OK, _diagnostic(out, err)
    archive = _archive_dir(sandbox)
    for relpath, destination in _SEEDED.items():
        landed = archive / destination
        assert landed.is_file(), f"{relpath} did not land at {landed}\n" + _diagnostic(out, err)
        assert landed.read_text(encoding="utf-8") == f"legacy content of {relpath}\n"

    # The legacy directory holds no file; its directories are left in place.
    assert _files_under(_legacy_dir(sandbox)) == []
    assert _legacy_dir(sandbox).is_dir()

    rows = read_rows(_ledger(sandbox))
    project_rel = f"projects/{sandbox.project}/{LEGACY_REQUIREMENTS_DIR}"
    expected = {
        (
            "priority" if destination.startswith("priority/") else "requirements",
            _LEDGER_IDS[relpath],
            f"{project_rel}/{relpath}",
            f"archive/{sandbox.project}/{destination}",
        )
        for relpath, destination in _SEEDED.items()
    }
    assert {(row.category, row.item_id, row.source, row.destination) for row in rows} == expected
    assert len(rows) == len(_SEEDED)
    # The release column is the project's last released version.
    assert {row.release for row in rows} == {"v1.0.0"}
    assert run.legacy is not None and len(run.legacy.rows) == len(_SEEDED)


# ---------------------------------------------------------------------------
# 2. Idempotence, and a project with no legacy directory
# ---------------------------------------------------------------------------


def test_second_archive_run_migrates_nothing_and_writes_no_ledger_row(tmp_path: pathlib.Path) -> None:
    sandbox = _sandbox(tmp_path)
    first, out, err = _run(sandbox)
    assert first.exit_code == EXIT_OK, _diagnostic(out, err)
    ledger_before = _ledger(sandbox).read_bytes()
    before = sandbox.snapshot()

    second, out, err = _run(sandbox)

    assert second.exit_code == EXIT_OK, _diagnostic(out, err)
    assert second.legacy is not None and second.legacy.rows == ()
    assert _ledger(sandbox).read_bytes() == ledger_before
    assert sandbox.snapshot() == before


def test_project_without_a_legacy_directory_runs_cleanly(tmp_path: pathlib.Path) -> None:
    sandbox = ArchiveSandbox(tmp_path)
    assert not _legacy_dir(sandbox).exists()

    run, out, err = _run(sandbox)

    assert run.exit_code == EXIT_OK, _diagnostic(out, err)
    assert err == [], _diagnostic(out, err)
    assert run.legacy is not None and run.legacy.ok and run.legacy.rows == ()
    assert not _ledger(sandbox).exists()
    assert not _legacy_dir(sandbox).exists()


# ---------------------------------------------------------------------------
# 3. A migrated item is found by ID and can be restored
# ---------------------------------------------------------------------------


def test_migrated_requirement_is_found_by_id_and_restores_to_the_live_project(tmp_path: pathlib.Path) -> None:
    sandbox = _sandbox(tmp_path)
    run, out, err = _run(sandbox)
    assert run.exit_code == EXIT_OK, _diagnostic(out, err)

    found = find_archived(sandbox.root, sandbox.project, "v1.0.0-first-feature")
    assert found == _archive_dir(sandbox) / "requirements" / "v1.0.0-first-feature.md"
    assert find_archived(sandbox.root, sandbox.project, "PRIORITY-7") == (
        _archive_dir(sandbox) / "priority" / "PRIORITY-0007-urgent-fix.md"
    )

    restored, out, err = _run(sandbox, restore_id="v1.0.0-first-feature.md")

    assert restored.exit_code == EXIT_OK, _diagnostic(out, err)
    live = sandbox.project_root / "requirements" / "v1.0.0-first-feature.md"
    assert live.read_text(encoding="utf-8") == "legacy content of v1.0.0-first-feature.md\n"
    assert read_rows(_ledger(sandbox))[-1].category == "restore"


# ---------------------------------------------------------------------------
# 4. An occupied destination is never overwritten
# ---------------------------------------------------------------------------


def test_clashing_file_keeps_both_copies_and_still_empties_the_legacy_directory(tmp_path: pathlib.Path) -> None:
    sandbox = ArchiveSandbox(tmp_path)
    existing = _archive_dir(sandbox) / "requirements" / "v1.0.0-first-feature.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("already archived\n", encoding="utf-8")
    legacy = _legacy_dir(sandbox)
    (legacy / "priority").mkdir(parents=True)
    # The same name twice in the legacy tree, and once already archived.
    (legacy / "v1.0.0-first-feature.md").write_text("legacy top\n", encoding="utf-8")
    (legacy / "priority" / "v1.0.0-first-feature.md").write_text("legacy priority\n", encoding="utf-8")

    run, out, err = _run(sandbox)

    assert run.exit_code == EXIT_OK, _diagnostic(out, err)
    requirements = _archive_dir(sandbox) / "requirements"
    assert existing.read_text(encoding="utf-8") == "already archived\n"
    landed = {
        (requirements / "v1.0.0-first-feature.legacy-1.md").read_text(encoding="utf-8"),
        (requirements / "v1.0.0-first-feature.legacy-2.md").read_text(encoding="utf-8"),
    }
    assert landed == {"legacy top\n", "legacy priority\n"}
    assert _files_under(legacy) == []
    assert {row.item_id for row in read_rows(_ledger(sandbox))} == {
        "v1.0.0-first-feature.legacy-1.md",
        "v1.0.0-first-feature.legacy-2.md",
    }


# ---------------------------------------------------------------------------
# 5. Dry run and archive_count 0
# ---------------------------------------------------------------------------


def test_dry_run_lists_the_legacy_files_and_moves_nothing(tmp_path: pathlib.Path) -> None:
    sandbox = _sandbox(tmp_path)
    before = sandbox.snapshot()

    run, out, err = _run(sandbox, dry_run=True)

    assert run.exit_code == EXIT_OK, _diagnostic(out, err)
    assert sandbox.snapshot() == before
    text = "\n".join(out)
    assert f"legacy requirements archive: {len(_SEEDED)} file(s) would move" in text, text
    for relpath, destination in _SEEDED.items():
        assert f"{LEGACY_REQUIREMENTS_DIR}/{relpath} -> archive/{sandbox.project}/{destination}" in text, text


def test_archive_count_zero_migrates_nothing(tmp_path: pathlib.Path) -> None:
    sandbox = _sandbox(tmp_path)
    before = sandbox.snapshot()

    run, out, err = _run(sandbox, count=0)

    assert run.exit_code == EXIT_OK, _diagnostic(out, err)
    assert run.legacy is None
    assert sandbox.snapshot() == before
    assert not _ledger(sandbox).exists()


# ---------------------------------------------------------------------------
# 6. A held repo lock skips the migration
# ---------------------------------------------------------------------------


def test_held_repo_lock_skips_the_migration_with_nothing_moved(tmp_path: pathlib.Path) -> None:
    sandbox = _sandbox(tmp_path)
    before = sandbox.snapshot()
    lock_file = repo_lock_path(sandbox.root, sandbox.project)
    lines: list[str] = []

    with acquire_lock(lock_file, holder="wake", pid=os.getpid(), op="test-hold", task="none"):
        result = migrate_legacy(sandbox.project, kanban_root=sandbox.root, log=lines.append)

    assert result.status == RESULT_SKIPPED, "\n".join(lines)
    assert result.rows == ()
    assert "archive.repo_lock_busy" in result.error
    after = sandbox.snapshot()
    # Taking the lock writes its own provenance file; nothing else changed.
    changed = {path for path in set(before) | set(after) if before.get(path) != after.get(path)}
    assert all("lock" in path for path in changed), changed
    assert len(_files_under(_legacy_dir(sandbox))) == len(_SEEDED)


def test_accept_chain_lock_migrates_under_a_lock_its_own_chain_holds(tmp_path: pathlib.Path) -> None:
    sandbox = _sandbox(tmp_path)
    lock_file = repo_lock_path(sandbox.root, sandbox.project)
    lines: list[str] = []

    with acquire_lock(lock_file, holder="wake-chain", pid=os.getppid(), op="wake-chain:demo", task="none"):
        result = migrate_legacy(sandbox.project, kanban_root=sandbox.root, log=lines.append, accept_chain_lock=True)

    assert result.status == RESULT_OK, "\n".join(lines)
    assert len(result.rows) == len(_SEEDED)
    assert _files_under(_legacy_dir(sandbox)) == []

