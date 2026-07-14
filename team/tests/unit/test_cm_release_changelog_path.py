"""
test_cm_release_changelog_path.py
==================================
Regression gate: cm-release.sh CHANGELOG-update path uses the canonical
`python3 -m pgai_agent_kanban.cm.changelog_writer` module invocation, and
the output of that invocation is byte-identical to a direct changelog_writer
call on the same fixture.

Acceptance criteria addressed:
  1. Structural: cm-release.sh Step 11b contains the `-m pgai_agent_kanban.cm.changelog_writer`
     invocation form — fails immediately if the script is reverted to the legacy
     heredoc approach.
  2. Behavioral: two independent subprocess invocations of the module on the same
     hermetic fixture produce byte-identical output.
  3. Cross-path: the subprocess invocation (reproducing what cm-release.sh does)
     and a direct Python `regenerate()` call on the same fixture produce
     byte-identical output.

All fixtures are hermetic — they do not touch the live CHANGELOG.md or bugs dir.
All temp paths use pytest's tmp_path fixture (redirected to the framework temp root).
Determinism comes from changelog_writer.py sorting heading collections alphabetically
(``sorted(_IMPLEMENTED_HEADINGS)`` / ``sorted(_FIXED_HEADINGS)``) before iteration,
not from any specific PYTHONHASHSEED value.  PYTHONHASHSEED=0 is set in subprocess
environments as belt-and-braces but is not the mechanism responsible for ordering.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

from pgai_agent_kanban.cm.changelog_writer import (
    load_published_manifest,
    regenerate,
)

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

_THIS_FILE = pathlib.Path(__file__).resolve()
# tests/unit/test_cm_release_changelog_path.py
# three levels up: unit/ → tests/ → team/
_TEAM_DIR = _THIS_FILE.parent.parent.parent       # team/
_DEV_TREE_ROOT = _TEAM_DIR.parent                  # project root
_CM_RELEASE_SCRIPT = _TEAM_DIR / "scripts" / "cm" / "release.sh"

# The Python package lives under team/, so PYTHONPATH must point there.
_PYTHONPATH_ROOT = str(_TEAM_DIR)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _make_release_notes(
    repo_root: pathlib.Path,
    version: str,
    date: str,
    *,
    summary: str = "",
    features: str = "None",
    bug_fixes: str = "None",
) -> pathlib.Path:
    """Create a minimal release-notes/<version>.md under repo_root/release-notes/."""
    notes_dir = repo_root / "release-notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    path = notes_dir / f"{version}.md"
    summary_text = summary or f"Release {version}."
    content = textwrap.dedent(f"""\
        # Release Notes: fixture {version}

        **Release Date:** {date}
        **Released By:** fixture

        ## Status
        FUNCTIONAL

        ## Summary
        {summary_text}

        ## Features
        {features}

        ## Bug Fixes
        {bug_fixes}

        ## Known Issues
        None
        """)
    path.write_text(content, encoding="utf-8")
    return path


def _make_published(repo_root: pathlib.Path, versions: list[str]) -> pathlib.Path:
    """Create release-notes/PUBLISHED under repo_root."""
    notes_dir = repo_root / "release-notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    path = notes_dir / "PUBLISHED"
    path.write_text(
        "\n".join(versions) + ("\n" if versions else ""),
        encoding="utf-8",
    )
    return path


def _make_bugs_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create an empty bugs directory (no bug files needed for basic fixture)."""
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir(parents=True, exist_ok=True)
    return bugs_dir


def _build_fixture(
    tmp_path: pathlib.Path,
    versions_newest_first: list[str],
    published_versions: list[str],
) -> tuple[pathlib.Path, pathlib.Path]:
    """Build a minimal hermetic repo fixture.

    Returns:
        (repo_root, bugs_dir) — both under tmp_path.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    bugs_dir = _make_bugs_dir(tmp_path)
    _make_published(repo_root, published_versions)
    for i, version in enumerate(versions_newest_first):
        _make_release_notes(
            repo_root,
            version,
            f"2026-01-{i + 1:02d}",
            features=f"- Feature in {version}",
        )
    return repo_root, bugs_dir


def _subprocess_writer_output(
    repo_root: pathlib.Path,
    bugs_dir: pathlib.Path,
) -> bytes:
    """Invoke `python3 -m pgai_agent_kanban.cm.changelog_writer` via subprocess.

    Reproduces the invocation in cm-release.sh Step 11b:
        PYTHONPATH="$KANBAN_ROOT" PYTHONHASHSEED=0 \\
          python3 -m pgai_agent_kanban.cm.changelog_writer \\
          "$REPO_ROOT" "$_cl_bugs_dir"

    PYTHONHASHSEED=0 is passed as belt-and-braces.  The actual determinism comes
    from changelog_writer.py sorting heading collections alphabetically before
    iterating (``sorted(_IMPLEMENTED_HEADINGS)`` / ``sorted(_FIXED_HEADINGS)``),
    which makes output stable regardless of the process hash seed.

    Returns the raw bytes written to stdout by the subprocess.

    Raises:
        AssertionError: if the subprocess exits non-zero.
    """
    env = os.environ.copy()
    env["PYTHONHASHSEED"] = "0"
    env["PYTHONPATH"] = _PYTHONPATH_ROOT

    result = subprocess.run(
        [
            sys.executable,
            "-m", "pgai_agent_kanban.cm.changelog_writer",
            str(repo_root),
            str(bugs_dir),
        ],
        env=env,
        capture_output=True,
        cwd=str(_TEAM_DIR),
    )
    assert result.returncode == 0, (
        "changelog_writer subprocess exited non-zero.\n"
        f"stderr: {result.stderr.decode(errors='replace')!r}\n"
        f"stdout: {result.stdout.decode(errors='replace')!r}"
    )
    return result.stdout


# ---------------------------------------------------------------------------
# Structural test: cm-release.sh uses the module invocation form
# ---------------------------------------------------------------------------


class TestCmReleaseShellInvocationForm:
    """cm-release.sh Step 11b must use the module invocation form, not the legacy heredoc."""

    def test_script_uses_module_invocation(self) -> None:
        """cm-release.sh Step 11b contains the `-m pgai_agent_kanban.cm.changelog_writer` pattern.

        If cm-release.sh is reverted to the legacy Python heredoc (which loaded the writer
        via `importlib.util.spec_from_file_location`), this assertion fails immediately.
        The regression gate is the presence of the module-invocation string in the
        CHANGELOG-update section of the script.
        """
        assert _CM_RELEASE_SCRIPT.exists(), (
            f"cm-release.sh not found at expected location: {_CM_RELEASE_SCRIPT}"
        )
        source = _CM_RELEASE_SCRIPT.read_text(encoding="utf-8")

        assert "-m pgai_agent_kanban.cm.changelog_writer" in source, (
            "cm-release.sh does not contain the canonical module invocation "
            "`-m pgai_agent_kanban.cm.changelog_writer`.\n"
            "The CHANGELOG-update path (Step 11b) must invoke the writer as a Python "
            "module, not via a heredoc or spec_from_file_location. Check whether the "
            "script was reverted to the legacy inline approach."
        )

    def test_legacy_heredoc_pattern_absent(self) -> None:
        """cm-release.sh Step 11b does not use the legacy spec_from_file_location heredoc.

        The legacy approach loaded changelog_writer.py via importlib.util.spec_from_file_location
        inside a bash heredoc. That approach produced non-deterministic output (hash-seed
        dependent) and diverged from the freshness gate. The presence of this pattern
        in the CHANGELOG-writing block indicates a reversion.
        """
        assert _CM_RELEASE_SCRIPT.exists(), (
            f"cm-release.sh not found at expected location: {_CM_RELEASE_SCRIPT}"
        )
        source = _CM_RELEASE_SCRIPT.read_text(encoding="utf-8")

        # Extract the Step 11b block (between "Step 11b" and "Step 11c")
        step11b_start = source.find("Step 11b")
        step11c_start = source.find("Step 11c")
        assert step11b_start != -1, "Step 11b marker not found in cm-release.sh"
        assert step11c_start != -1, "Step 11c marker not found in cm-release.sh"

        step11b_block = source[step11b_start:step11c_start]

        assert "spec_from_file_location" not in step11b_block, (
            "cm-release.sh Step 11b contains `spec_from_file_location` — the legacy "
            "heredoc pattern. The CHANGELOG path was reverted. It must use "
            "`python3 -m pgai_agent_kanban.cm.changelog_writer` instead."
        )


# ---------------------------------------------------------------------------
# Behavioral test: byte-identical output across invocations
# ---------------------------------------------------------------------------


class TestChangelogWriterDeterminism:
    """Two subprocess invocations on the same fixture produce byte-identical output.

    Determinism is guaranteed by changelog_writer.py sorting heading collections
    alphabetically before iterating (``sorted(_IMPLEMENTED_HEADINGS)`` /
    ``sorted(_FIXED_HEADINGS)``), not by any specific PYTHONHASHSEED value.
    """

    def test_identical_runs_produce_same_bytes(self, tmp_path: pathlib.Path) -> None:
        """Two invocations of changelog_writer on the same fixture are byte-identical.

        Runs the writer twice on a minimal fixture.  Byte-identity is the core
        determinism property that makes the freshness gate reliable: given unchanged
        inputs, two runs must produce the same file.  The stable ordering comes from
        sorted() over the heading collections in changelog_writer.py.
        """
        repo_root, bugs_dir = _build_fixture(
            tmp_path,
            versions_newest_first=["v1.1.0", "v1.0.0"],
            published_versions=["v1.0.0"],
        )

        run1 = _subprocess_writer_output(repo_root, bugs_dir)
        run2 = _subprocess_writer_output(repo_root, bugs_dir)

        assert run1 == run2, (
            "Two changelog_writer invocations on identical inputs produced different output.\n"
            "Determinism comes from sorted() over the heading collections in changelog_writer.py.\n"
            "Check that no non-deterministic logic was introduced in changelog_writer.py."
        )

    def test_output_is_non_empty(self, tmp_path: pathlib.Path) -> None:
        """changelog_writer produces non-empty output for a minimal fixture."""
        repo_root, bugs_dir = _build_fixture(
            tmp_path,
            versions_newest_first=["v1.0.0"],
            published_versions=["v1.0.0"],
        )

        output = _subprocess_writer_output(repo_root, bugs_dir)

        assert len(output) > 0, "changelog_writer produced empty output for a minimal fixture."
        assert b"# Changelog" in output, (
            "changelog_writer output does not contain the expected '# Changelog' header."
        )


# ---------------------------------------------------------------------------
# Cross-path equivalence: subprocess vs. direct Python call
# ---------------------------------------------------------------------------


class TestSubprocessVsDirectCallEquivalence:
    """Subprocess invocation and direct `regenerate()` call on the same fixture are byte-identical.

    This is the core regression gate for the cm-release.sh unification:
    the module's CLI path (`-m pgai_agent_kanban.cm.changelog_writer`) and the
    Python API (`changelog_writer.regenerate()`) must produce identical output.
    If cm-release.sh were to use a different invocation (e.g. the legacy heredoc)
    the output could diverge from what the freshness gate expects.
    """

    def test_subprocess_and_direct_call_produce_identical_output(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Subprocess and direct Python regenerate() on the same fixture are byte-identical.

        Runs the writer via subprocess (replicating the cm-release.sh invocation) and via
        the Python API directly, then asserts the outputs are the same bytes. This test
        would fail if the subprocess path and the API path diverged — for example, if the
        subprocess path applied a post-processing step the API does not.
        """
        repo_root, bugs_dir = _build_fixture(
            tmp_path,
            versions_newest_first=["v1.1.0", "v1.0.0"],
            published_versions=["v1.0.0"],
        )

        # Subprocess invocation — mirrors cm-release.sh Step 11b
        subprocess_bytes = _subprocess_writer_output(repo_root, bugs_dir)

        # Direct Python API call
        published = load_published_manifest(repo_root)
        notes_dir = repo_root / "release-notes"
        versions = sorted(
            [p.stem for p in notes_dir.glob("v*.md")],
            key=lambda v: tuple(int(x) for x in v.lstrip("v").split(".")),
            reverse=True,
        )
        direct_output = regenerate(repo_root, versions, published, bugs_dir)
        direct_bytes = direct_output.encode("utf-8")

        assert subprocess_bytes == direct_bytes, (
            "Subprocess invocation and direct regenerate() call produced different output.\n"
            f"Subprocess bytes: {len(subprocess_bytes)}, Direct bytes: {len(direct_bytes)}\n"
            "The CLI path and the Python API path must be equivalent. Check whether the "
            "_main() function in changelog_writer.py applies any transformation not in regenerate()."
        )

    def test_fixture_with_empty_bugs_dir_is_hermetic(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Fixture with empty bugs dir produces deterministic output independent of live state.

        Verifies that the test does not accidentally import the live bugs directory.
        An empty bugs_dir must produce output with no Known Issues items.
        """
        repo_root, bugs_dir = _build_fixture(
            tmp_path,
            versions_newest_first=["v1.0.0"],
            published_versions=[],   # no published versions → no Known Issues
        )

        output = _subprocess_writer_output(repo_root, bugs_dir)
        text = output.decode("utf-8")

        # With no published versions, Known Issues section must say None everywhere
        assert "KI-" not in text, (
            "Known Issue IDs found in output despite no published versions in the fixture.\n"
            "The test may be using live state rather than the hermetic fixture."
        )
