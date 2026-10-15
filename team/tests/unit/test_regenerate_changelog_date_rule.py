"""
test_regenerate_changelog_date_rule.py
======================================
scripts/regenerate-changelog.sh dates each release with the same rule as the
release-time changelog writer: **Release Date:**, else **Date:**, else
"unknown" with exactly one stderr warning naming the notes file.

The script runs for real, as an operator runs it, against a throwaway kanban
root under tmp_path. Its environment is built from an explicit set: the two
roots and the temp dir point into the sandbox, and PGAI_TASKS_DIR,
PGAI_PROJECT_ROOT and PGAI_PROJECT_NAME are absent, so nothing resolves to
the live install.

A structural check pins the rule to one module: the date regexes are
defined only in pgai_agent_kanban.cm.release_notes_date, and both renderers
call the reader that module defines.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

from pgai_agent_kanban.cm import changelog_writer, regenerate_changelog, release_notes_date
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SCRIPT = _TEAM_DIR / "scripts" / "regenerate-changelog.sh"
_PACKAGE_DIR = _TEAM_DIR / "pgai_agent_kanban"
_PROJECT = "demo"
_TIMEOUT_SECONDS = 60


def _make_sandbox(tmp_path: pathlib.Path) -> pathlib.Path:
    """A throwaway kanban root holding one project with an empty release-notes/."""
    root = tmp_path / "kanban-root"
    (root / "tmp").mkdir(parents=True)
    (root / "projects" / _PROJECT / "release-notes").mkdir(parents=True)
    (root / "projects.cfg").write_text(f"[{_PROJECT}]\n", encoding="utf-8")
    return root


def _write_notes(root: pathlib.Path, version: str, text: str) -> pathlib.Path:
    path = root / "projects" / _PROJECT / "release-notes" / f"{version}.md"
    path.write_text(text, encoding="utf-8")
    return path


def _run_script(tmp_path: pathlib.Path, root: pathlib.Path) -> subprocess.CompletedProcess:
    """Run regenerate-changelog.sh for the sandbox project, default output path."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
        },
    )
    return subprocess.run(
        ["bash", str(_SCRIPT), "--project", _PROJECT],
        cwd=str(tmp_path),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_SECONDS,
    )


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _release_headings(root: pathlib.Path) -> list[str]:
    changelog = root / "projects" / _PROJECT / "CHANGELOG.md"
    return [
        line for line in changelog.read_text(encoding="utf-8").splitlines()
        if line.startswith("## v")
    ]


def _warning_lines(stderr: str) -> list[str]:
    return [line for line in stderr.splitlines() if "Warning:" in line]


# ---------------------------------------------------------------------------
# The script, run for real in a throwaway root
# ---------------------------------------------------------------------------


def test_date_line_dates_the_release_without_a_warning(tmp_path: pathlib.Path) -> None:
    root = _make_sandbox(tmp_path)
    _write_notes(root, "v1.0.0", "# v1.0.0\n\n**Date:** 2026-01-02\n\n## Summary\nShipped.\n")

    result = _run_script(tmp_path, root)

    assert result.returncode == 0, _diagnostic(result)
    assert _release_headings(root) == ["## v1.0.0 — 2026-01-02"], _diagnostic(result)
    assert _warning_lines(result.stderr) == [], _diagnostic(result)


def test_notes_with_no_date_line_render_unknown_and_warn_once_naming_the_file(
    tmp_path: pathlib.Path,
) -> None:
    root = _make_sandbox(tmp_path)
    undated = _write_notes(root, "v1.0.0", "# v1.0.0\n\n## Summary\nShipped.\n")

    result = _run_script(tmp_path, root)

    assert result.returncode == 0, _diagnostic(result)
    assert _release_headings(root) == ["## v1.0.0 — unknown"], _diagnostic(result)
    warnings = _warning_lines(result.stderr)
    assert len(warnings) == 1, _diagnostic(result)
    assert str(undated) in warnings[0], _diagnostic(result)


def test_release_date_line_wins_over_date_line(tmp_path: pathlib.Path) -> None:
    root = _make_sandbox(tmp_path)
    _write_notes(
        root,
        "v1.0.0",
        "# v1.0.0\n\n**Date:** 2026-01-02\n**Release Date:** 2026-01-05\n",
    )

    result = _run_script(tmp_path, root)

    assert result.returncode == 0, _diagnostic(result)
    assert _release_headings(root) == ["## v1.0.0 — 2026-01-05"], _diagnostic(result)


def test_only_the_undated_file_of_several_warns(tmp_path: pathlib.Path) -> None:
    root = _make_sandbox(tmp_path)
    _write_notes(root, "v1.0.0", "# v1.0.0\n\n**Release Date:** 2026-01-02\n")
    undated = _write_notes(root, "v1.1.0", "# v1.1.0\n\nNo date here.\n")
    _write_notes(root, "v1.2.0", "# v1.2.0\n\n**Date:** 2026-03-04\n")

    result = _run_script(tmp_path, root)

    assert result.returncode == 0, _diagnostic(result)
    assert _release_headings(root) == [
        "## v1.2.0 — 2026-03-04",
        "## v1.1.0 — unknown",
        "## v1.0.0 — 2026-01-02",
    ], _diagnostic(result)
    warnings = _warning_lines(result.stderr)
    assert len(warnings) == 1 and str(undated) in warnings[0], _diagnostic(result)


# ---------------------------------------------------------------------------
# Version selection and ordering, through the shared semver helper
# ---------------------------------------------------------------------------


def test_releases_are_ordered_by_version_not_by_text(tmp_path: pathlib.Path) -> None:
    root = _make_sandbox(tmp_path)
    for version in ("v0.9.7", "v0.17.1", "v0.17.0"):
        _write_notes(root, version, f"# {version}\n\n**Date:** 2026-01-02\n")

    result = _run_script(tmp_path, root)

    assert result.returncode == 0, _diagnostic(result)
    assert _release_headings(root) == [
        "## v0.17.1 — 2026-01-02",
        "## v0.17.0 — 2026-01-02",
        "## v0.9.7 — 2026-01-02",
    ], _diagnostic(result)


def test_only_names_that_start_with_a_version_are_rendered(tmp_path: pathlib.Path) -> None:
    notes = tmp_path / "release-notes"
    notes.mkdir()
    for stem in ("v1.2.3-draft", "v1.10.0", "notes-v9.9.9", "v1.2", "README"):
        (notes / f"{stem}.md").write_text(f"# {stem}\n\n**Date:** 2026-01-02\n", encoding="utf-8")

    content, count = regenerate_changelog.render_changelog(notes)

    headings = [line for line in content.splitlines() if line.startswith("## ")]
    assert headings == ["## v1.10.0 — 2026-01-02", "## v1.2.3-draft — 2026-01-02"]
    assert count == 2


def test_version_ordering_comes_from_the_shared_helper() -> None:
    source = pathlib.Path(regenerate_changelog.__file__).read_text(encoding="utf-8")
    assert "from pgai_agent_kanban.lib.semver import" in source
    assert "SEMVER_RE" not in source
    assert "def semver_key" not in source


# ---------------------------------------------------------------------------
# One date rule, in one module
# ---------------------------------------------------------------------------


def test_both_renderers_call_the_one_shared_date_reader() -> None:
    shared = release_notes_date.read_release_date
    assert changelog_writer.read_release_date is shared
    assert regenerate_changelog.read_release_date is shared


def test_date_regexes_are_defined_in_one_module_only() -> None:
    patterns = (release_notes_date.RELEASE_DATE_RE.pattern, release_notes_date.DATE_RE.pattern)
    defining = sorted(
        path.relative_to(_TEAM_DIR).as_posix()
        for path in _PACKAGE_DIR.rglob("*.py")
        if any(pattern in path.read_text(encoding="utf-8") for pattern in patterns)
    )
    assert defining == ["pgai_agent_kanban/cm/release_notes_date.py"]


def test_script_carries_no_inline_python_renderer() -> None:
    text = _SCRIPT.read_text(encoding="utf-8")
    assert "<<'REGEN_PY'" not in text
    assert "pgai_agent_kanban.cm.regenerate_changelog" in text
