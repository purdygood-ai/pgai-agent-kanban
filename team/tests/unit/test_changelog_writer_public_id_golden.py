"""
test_changelog_writer_public_id_golden.py — the release render's Public IDs on a fixed ledger.

Pins the CHANGELOG text and the Public IDs the changelog writer's release
render writes back into bug files against checked-in expected output, on the
committed fixture under team/tests/fixtures/changelog_public_id_ledger/:

  input/release-notes/         three releases; PUBLISHED names v1.0.0 and v1.1.0
  input/bugs/                  seven bug files; one already carries a Public ID,
                               the rest carry none (one is internal-only and one
                               has a prose ## Affects, so neither gets an ID)
  input/published-CHANGELOG.md a published artifact showing one ID whose bug
                               file no longer exists
  expected/<scenario>/         CHANGELOG.md and the bug files after one render

Two scenarios render the same ledger: ``unpublished`` with no published
CHANGELOG.md in the repository, and ``published`` with
input/published-CHANGELOG.md committed as CHANGELOG.md, whose orphaned ID is
spent (never handed to another bug) and whose line is kept as history.

The expected output was produced by an earlier release of the writer on this
same fixture; a difference here means the release render's Public-ID
assignment changed. Every render runs on a copy under tmp_path — the fixture
itself is never written.
"""

from __future__ import annotations

import pathlib
import re
import shutil

import pytest

from pgai_agent_kanban.cm.changelog_writer import (
    load_published_manifest,
    parse_bug_file,
    regenerate,
)

_FIXTURE_DIR = (
    pathlib.Path(__file__).resolve().parent.parent
    / "fixtures"
    / "changelog_public_id_ledger"
)
_INPUT_DIR = _FIXTURE_DIR / "input"
_EXPECTED_DIR = _FIXTURE_DIR / "expected"

_VERSIONS_NEWEST_FIRST = ["v1.2.0", "v1.1.0", "v1.0.0"]
_SCENARIOS = ["unpublished", "published"]

_PERSISTED_BUG = "BUG-0104-persisted-public-id.md"
_PERSISTED_ID = "KI-1.1.0.1"
_PERSISTED_LINE = f"{_PERSISTED_ID} — Board colours ignore the theme; affects v1.1.0 · open"


def _copy_fixture(tmp_path: pathlib.Path, scenario: str) -> tuple[pathlib.Path, pathlib.Path]:
    """Copy the fixture ledger for *scenario* under tmp_path; return (repo_root, bugs_dir)."""
    repo_root = tmp_path / "repo"
    bugs_dir = tmp_path / "bugs"
    shutil.copytree(_INPUT_DIR / "release-notes", repo_root / "release-notes")
    shutil.copytree(_INPUT_DIR / "bugs", bugs_dir)
    if scenario == "published":
        shutil.copyfile(_INPUT_DIR / "published-CHANGELOG.md", repo_root / "CHANGELOG.md")
    return repo_root, bugs_dir


def _release_render(repo_root: pathlib.Path, bugs_dir: pathlib.Path) -> str:
    """Render as the release does: the default published artifact, repo_root/CHANGELOG.md."""
    return regenerate(
        repo_root,
        _VERSIONS_NEWEST_FIRST,
        load_published_manifest(repo_root),
        bugs_dir,
    )


def _file_bytes(directory: pathlib.Path) -> dict[str, bytes]:
    """Every file in *directory*, by name, with its exact bytes."""
    return {p.name: p.read_bytes() for p in sorted(directory.iterdir()) if p.is_file()}


def test_fixture_bug_files_start_without_public_ids_except_the_persisted_one() -> None:
    """The input ledger is what the golden tests assume: one persisted ID, the rest blank."""
    ids = {p.name: parse_bug_file(p).public_id for p in sorted((_INPUT_DIR / "bugs").glob("BUG-*.md"))}

    assert ids.pop(_PERSISTED_BUG) == _PERSISTED_ID
    assert ids and all(public_id == "" for public_id in ids.values()), ids


@pytest.mark.parametrize("scenario", _SCENARIOS)
def test_release_render_matches_the_expected_changelog_and_public_ids(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], scenario: str
) -> None:
    """The CHANGELOG text and every bug file after one render equal the checked-in output."""
    repo_root, bugs_dir = _copy_fixture(tmp_path, scenario)
    expected = _EXPECTED_DIR / scenario

    content = _release_render(repo_root, bugs_dir)
    capsys.readouterr()  # the prose-Affects warning is expected on stderr

    assert content == (expected / "CHANGELOG.md").read_text(encoding="utf-8")
    assert _file_bytes(bugs_dir) == _file_bytes(expected / "bugs")


@pytest.mark.parametrize("scenario", _SCENARIOS)
def test_persisted_public_id_is_kept_byte_for_byte_and_rendered(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], scenario: str
) -> None:
    """A bug file that already carries a Public ID is not rewritten, and its ID is shown."""
    repo_root, bugs_dir = _copy_fixture(tmp_path, scenario)
    persisted = bugs_dir / _PERSISTED_BUG
    before = persisted.read_bytes()

    content = _release_render(repo_root, bugs_dir)
    capsys.readouterr()

    assert persisted.read_bytes() == before
    assert _PERSISTED_LINE in content.splitlines(), content


@pytest.mark.parametrize("scenario", _SCENARIOS)
def test_second_render_reads_the_persisted_ids_and_writes_nothing(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], scenario: str
) -> None:
    """IDs written by the first render are read back, never recomputed, by the next."""
    repo_root, bugs_dir = _copy_fixture(tmp_path, scenario)
    first = _release_render(repo_root, bugs_dir)
    after_first = _file_bytes(bugs_dir)

    second = _release_render(repo_root, bugs_dir)
    capsys.readouterr()

    assert second == first
    assert _file_bytes(bugs_dir) == after_first


def test_published_orphan_id_is_spent_and_its_line_kept(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An ID the published artifact shows for a bug file that no longer exists is never reissued."""
    repo_root, bugs_dir = _copy_fixture(tmp_path, "published")
    orphan_line = "KI-1.0.0.1 — Search ignores archived tasks; affects v1.0.0 · open"
    assert orphan_line in (repo_root / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()

    content = _release_render(repo_root, bugs_dir)
    capsys.readouterr()

    assert orphan_line in content.splitlines(), content
    assigned = [parse_bug_file(p).public_id for p in sorted(bugs_dir.glob("BUG-*.md"))]
    assert "KI-1.0.0.1" not in assigned, assigned
    assert sorted(i for i in assigned if re.match(r"KI-1\.0\.0\.", i)) == [
        "KI-1.0.0.2",
        "KI-1.0.0.3",
        "KI-1.0.0.4",
    ], assigned
