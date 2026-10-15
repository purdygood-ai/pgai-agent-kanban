"""
test_changelog_writer_public_id_spent.py
========================================
A Public ID that appears in the published CHANGELOG.md is spent.

Covers the changelog writer's treatment of the published artifact:

  1. A published ID whose bug file no longer exists is never reissued: a
     new bug against the same version receives the next counter, and the
     published line is emitted byte-identical.
  2. A published Fixed-section line whose bug file no longer exists is kept
     verbatim in the section it was published under.
  3. A bug file that persists an ID the artifact publishes for a different
     title is given a fresh ID; the published line is untouched.
  4. A bug whose published title matches keeps its ID when only its status
     clause moves.
  5. Regeneration is a fixed point: rendering the output again reproduces it
     byte-for-byte.
  6. published_changelog=None reserves only the IDs bug files persist.
  7. The writer resolves the shared reader when loaded by file path.
  8. The freshness lint names a same-ID / different-title divergence as a
     collision and a same-title / different-clause divergence as a status
     move; in RC mode an added line carrying a published ID is not tolerated.

Every scratch repo and bugs directory lives under pytest's tmp_path (which
conftest redirects beneath the framework temp root); nothing here reads or
writes the live tree.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

from pgai_agent_kanban.cm import changelog_writer
from pgai_agent_kanban.cm.changelog_writer import parse_bug_file, regenerate
from pgai_agent_kanban.lib.public_id import extract_ki_ids

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_WRITER_PATH = _TEAM_DIR / "pgai_agent_kanban" / "cm" / "changelog_writer.py"
_LINT_PATH = _TEAM_DIR / "scripts" / "lint_changelog_freshness.py"

_VERSION = "v1.0.0"
_ANCHOR = "1.0.0"

_OLDER_TITLE = "An older defect whose bug file has since been recycled"
_OLDER_KNOWN_ISSUE = f"KI-{_ANCHOR}.1 — {_OLDER_TITLE}; affects {_VERSION} · open"
_OLDER_FIXED_LINE = f"- KI-{_ANCHOR}.1 — resolved (the older defect, as the notes once put it)"
_NEW_TITLE = "A newly filed defect against the same published version"


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _seed_repo(
    tmp_path: pathlib.Path,
    known_issues: str = "None",
    fixed: str = "None",
) -> tuple[pathlib.Path, pathlib.Path]:
    """Create a scratch repo with one published release and a CHANGELOG.md.

    Returns (repo_root, bugs_dir).  The CHANGELOG.md is shaped exactly as
    the writer renders a single-entry artifact, with the given Known Issues
    and Fixed section bodies, so a byte-identical regeneration is possible.
    """
    repo_root = tmp_path / "repo"
    notes_dir = repo_root / "release-notes"
    notes_dir.mkdir(parents=True)
    (notes_dir / "PUBLISHED").write_text(f"{_VERSION}\n", encoding="utf-8")
    (notes_dir / f"{_VERSION}.md").write_text(
        textwrap.dedent(
            f"""\
            # Release {_VERSION}

            **Release Date:** 2026-01-01

            ## Summary

            First public release.

            ## Features

            - Initial feature set.
            """
        ),
        encoding="utf-8",
    )
    (repo_root / "CHANGELOG.md").write_text(
        f"{changelog_writer._HEADER}\n---\n\n"
        f"## {_VERSION} — 2026-01-01\n\n"
        f"First public release.\n\n"
        f"### Implemented\n- Initial feature set.\n\n"
        f"### Fixed\n{fixed}\n\n"
        f"### Known Issues\n{known_issues}\n\n"
        f"*Details: [release-notes/{_VERSION}.md](release-notes/{_VERSION}.md)*\n\n---\n",
        encoding="utf-8",
    )
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    return repo_root, bugs_dir


def _write_bug(
    bugs_dir: pathlib.Path,
    number: int,
    symptom: str,
    public_id: str | None = None,
    status: str = "open",
    fixed_in: str | None = None,
) -> pathlib.Path:
    """Write a minimal bug file affecting the published version."""
    body = textwrap.dedent(
        f"""\
        # BUG-{number:04d}-seeded

        **Bug ID:** BUG-{number:04d}-seeded
        **Severity:** low

        ## Status
        {status}

        ## Symptom
        {symptom}.

        ## Affects
        {_VERSION}
        """
    )
    if fixed_in:
        body += f"\n## Fixed In\n{fixed_in}\n"
    if public_id:
        body += f"\n## Public ID\n{public_id}\n"
    path = bugs_dir / f"BUG-{number:04d}-seeded.md"
    path.write_text(body, encoding="utf-8")
    return path


def _regenerate(repo_root: pathlib.Path, bugs_dir: pathlib.Path, **kwargs) -> str:
    return regenerate(repo_root, [_VERSION], [_VERSION], bugs_dir, **kwargs)


def _known_issue_lines(content: str) -> list[str]:
    """Return the body lines of the (single) ### Known Issues section."""
    section = content.split("### Known Issues\n", 1)[1].split("\n\n", 1)[0]
    return section.splitlines()


def _fixed_lines(content: str) -> list[str]:
    """Return the body lines of the (single) ### Fixed section."""
    section = content.split("### Fixed\n", 1)[1].split("\n\n", 1)[0]
    return section.splitlines()


def _import_lint():
    spec = importlib.util.spec_from_file_location("lint_changelog_freshness", _LINT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# ---------------------------------------------------------------------------
# The writer
# ---------------------------------------------------------------------------


class TestPublishedIdIsSpent:
    def test_published_id_without_surviving_file_is_never_reissued(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A new bug gets the next counter; the orphaned published line is kept."""
        repo_root, bugs_dir = _seed_repo(tmp_path, known_issues=_OLDER_KNOWN_ISSUE)
        bug_path = _write_bug(bugs_dir, 2, _NEW_TITLE)

        content = _regenerate(repo_root, bugs_dir)

        assert parse_bug_file(bug_path).public_id == f"KI-{_ANCHOR}.2", (
            "the new bug must receive the counter after the published one, "
            f"got {parse_bug_file(bug_path).public_id!r}"
        )
        lines = _known_issue_lines(content)
        assert lines == [
            _OLDER_KNOWN_ISSUE,
            f"KI-{_ANCHOR}.2 — {_NEW_TITLE}; affects {_VERSION} · open",
        ], f"Known Issues body was:\n{lines}"

    def test_published_fixed_line_without_surviving_file_is_kept(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A Fixed-section line no release note or bug file produces is history."""
        repo_root, bugs_dir = _seed_repo(tmp_path, fixed=_OLDER_FIXED_LINE)
        bug_path = _write_bug(bugs_dir, 2, _NEW_TITLE)

        content = _regenerate(repo_root, bugs_dir)

        assert _fixed_lines(content) == [_OLDER_FIXED_LINE]
        assert parse_bug_file(bug_path).public_id == f"KI-{_ANCHOR}.2"
        assert _OLDER_FIXED_LINE in content.splitlines()

    def test_persisted_id_published_for_a_different_title_is_reissued(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The published line stays; the bug is given an ID the artifact lacks."""
        repo_root, bugs_dir = _seed_repo(tmp_path, known_issues=_OLDER_KNOWN_ISSUE)
        bug_path = _write_bug(bugs_dir, 2, _NEW_TITLE, public_id=f"KI-{_ANCHOR}.1")
        published_ids = extract_ki_ids((repo_root / "CHANGELOG.md").read_text())

        content = _regenerate(repo_root, bugs_dir)

        reissued = parse_bug_file(bug_path).public_id
        assert reissued and reissued not in published_ids, (
            f"bug still persists a published ID: {reissued!r}"
        )
        assert reissued == f"KI-{_ANCHOR}.2"
        assert _known_issue_lines(content) == [
            _OLDER_KNOWN_ISSUE,
            f"{reissued} — {_NEW_TITLE}; affects {_VERSION} · open",
        ]
        # The bug file carries exactly one Public ID value after the rewrite.
        text = bug_path.read_text(encoding="utf-8")
        assert text.count("KI-") == 1 and f"## Public ID\n{reissued}\n" in text, text

    def test_same_title_with_moved_status_keeps_its_id(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A status clause moving from open to pending is the same entry."""
        repo_root, bugs_dir = _seed_repo(tmp_path, known_issues=_OLDER_KNOWN_ISSUE)
        bug_path = _write_bug(
            bugs_dir, 2, _OLDER_TITLE, public_id=f"KI-{_ANCHOR}.1",
            status="resolved", fixed_in="v1.0.1",
        )

        content = _regenerate(repo_root, bugs_dir)

        assert parse_bug_file(bug_path).public_id == f"KI-{_ANCHOR}.1"
        assert _known_issue_lines(content) == [
            f"KI-{_ANCHOR}.1 — {_OLDER_TITLE}; affects {_VERSION} · fix pending next release"
        ]

    def test_regeneration_is_a_fixed_point(self, tmp_path: pathlib.Path) -> None:
        """Writing the output back and regenerating reproduces it byte-for-byte."""
        repo_root, bugs_dir = _seed_repo(tmp_path, known_issues=_OLDER_KNOWN_ISSUE)
        _write_bug(bugs_dir, 2, _NEW_TITLE)

        first = _regenerate(repo_root, bugs_dir)
        (repo_root / "CHANGELOG.md").write_text(first, encoding="utf-8")
        second = _regenerate(repo_root, bugs_dir)

        assert second == first

    def test_nothing_published_reserves_only_bug_file_ids(
        self, tmp_path: pathlib.Path
    ) -> None:
        """published_changelog=None ignores the artifact on disk entirely."""
        repo_root, bugs_dir = _seed_repo(tmp_path, known_issues=_OLDER_KNOWN_ISSUE)
        bug_path = _write_bug(bugs_dir, 2, _NEW_TITLE)

        content = _regenerate(repo_root, bugs_dir, published_changelog=None)

        assert parse_bug_file(bug_path).public_id == f"KI-{_ANCHOR}.1"
        assert _OLDER_KNOWN_ISSUE not in content

    def test_writer_loaded_by_file_path_resolves_shared_reader(
        self, tmp_path: pathlib.Path
    ) -> None:
        """An interpreter that loads the writer by path still finds the lib."""
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        script = textwrap.dedent(
            f"""\
            import importlib.util
            spec = importlib.util.spec_from_file_location("changelog_writer", {str(_WRITER_PATH)!r})
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            print(module._extract_ki_id_from_line("- KI-1.0.0.1 — resolved"))
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(tmp_path), env=env, capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "KI-1.0.0.1"


# ---------------------------------------------------------------------------
# The shared reader and the freshness lint
# ---------------------------------------------------------------------------


class TestSharedReaderAndLint:
    def test_reader_returns_full_public_ids(self) -> None:
        assert extract_ki_ids("KI-1.0.0.1 — a\n- KI-1.23.0.1 — b\nprose") == {
            "KI-1.0.0.1", "KI-1.23.0.1",
        }

    def test_lint_names_a_rewritten_title_as_a_collision(
        self, tmp_path: pathlib.Path
    ) -> None:
        lint = _import_lint()
        checked_in = f"### Known Issues\n{_OLDER_KNOWN_ISSUE}\n"
        regenerated = f"### Known Issues\nKI-{_ANCHOR}.1 — {_NEW_TITLE}; affects {_VERSION} · open\n"
        path = tmp_path / "CHANGELOG.md"

        causes = lint._build_causes(path, checked_in, regenerated)

        assert len(causes) == 1, causes
        assert causes[0].startswith(f"{path}:2: collision — 'KI-{_ANCHOR}.1'"), causes[0]
        assert _OLDER_TITLE in causes[0] and _NEW_TITLE in causes[0]

    def test_lint_names_a_moved_status_clause_not_a_collision(
        self, tmp_path: pathlib.Path
    ) -> None:
        lint = _import_lint()
        checked_in = f"### Known Issues\n{_OLDER_KNOWN_ISSUE}\n"
        regenerated = (
            f"### Known Issues\nKI-{_ANCHOR}.1 — {_OLDER_TITLE}; "
            f"affects {_VERSION} · fix pending next release\n"
        )
        path = tmp_path / "CHANGELOG.md"

        causes = lint._build_causes(path, checked_in, regenerated)

        assert len(causes) == 1, causes
        assert "collision" not in causes[0]
        assert causes[0].startswith(f"{path}:2: 'KI-{_ANCHOR}.1' entry's status clause")

    def test_rc_mode_rejects_an_added_line_carrying_a_published_id(self) -> None:
        """An added KI line whose ID the artifact already publishes is not new."""
        lint = _import_lint()
        checked_in = f"### Known Issues\n{_OLDER_KNOWN_ISSUE}\n"
        regenerated = (
            f"### Known Issues\n{_OLDER_KNOWN_ISSUE}\n"
            f"KI-{_ANCHOR}.1 — {_NEW_TITLE}; affects {_VERSION} · open\n"
        )

        tolerable, _ = lint._diff_is_rc_tolerable(checked_in, regenerated)

        assert tolerable is False
