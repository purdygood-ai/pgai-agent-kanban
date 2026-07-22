"""
test_lint_python_import_completeness.py
========================================
Tests for team/scripts/lint_python_import_completeness.py — the import-
completeness lint that verifies every third-party Python import across the
team/ source trees is declared in requirements.txt or requirements-test.txt.

Test cases:

  1. **Real-tree green** — the lint exits 0 on the actual codebase (the main
     acceptance criterion: current tree is clean).

  2. **Clean-fixture green** — the lint exits 0 against the clean fixture
     (team/tests/fixtures/import_completeness/clean_project/), where all
     imports are declared.

  3. **Stray-import fixture red** — the lint exits 1 against the stray-import
     fixture (team/tests/fixtures/import_completeness/stray_import_project/),
     which has a planted import that is NOT in the requirements files.

  4. **Stray-import fixture names module and file** — when the lint fails, the
     output identifies the missing module name ('httpx') AND the path to the
     file that imports it.

  5. **main() returns 0 for a synthetic clean tree** — two synthetic files
     created in tmp_path: a requirements.txt declaring 'requests' and a Python
     file that only imports 'requests' and stdlib modules.

  6. **main() returns 1 for a synthetic tree with a gap** — a synthetic
     Python file imports a package that is absent from the synthetic
     requirements files.

  7. **main() returns 2 when team-dir not found**.

  8. **--help exits 0** (argparse integration smoke test via subprocess).

  9. **Stdlib imports are not flagged** — a Python file that imports only
     stdlib modules (os, sys, pathlib) produces no violations.

 10. **Relative imports are not flagged** — a Python file with relative-import
     syntax (from . import something) produces no violations.

 11. **parse_requirements_file handles -r includes** — a requirements-test.txt
     that includes requirements.txt via '-r' resolves the included packages.

 12. **_PACKAGE_TO_IMPORT_NAMES mapping applied** — PyYAML declared in
     requirements produces 'yaml' in the covered-import set, not 'pyyaml'.

All tests use pytest's ``tmp_path`` for isolation — no bare /tmp paths,
no live kanban state mutations.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Load lint_python_import_completeness as an importlib module.
# Path hierarchy: team/tests/unit/ → team/ (three levels up)
# ---------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent          # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_LINT_SCRIPT = _SCRIPTS_DIR / "lint_python_import_completeness.py"
_FIXTURES_DIR = _TEAM_DIR / "tests" / "fixtures" / "import_completeness"
_REPO_ROOT = _TEAM_DIR.parent                        # repo root (contains requirements*.txt)


def _import_lint_module():
    """Import lint_python_import_completeness as an isolated module."""
    spec = importlib.util.spec_from_file_location(
        "lint_python_import_completeness", _LINT_SCRIPT
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {_LINT_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_lint = _import_lint_module()


# ---------------------------------------------------------------------------
# Helper: write a minimal fixture project layout in tmp_path
# ---------------------------------------------------------------------------


def _make_minimal_tree(
    tmp_path: Path,
    *,
    requirements_txt: str = "",
    requirements_test_txt: str = "",
    script_content: str = "",
) -> tuple[Path, Path]:
    """Build a minimal project tree in tmp_path.

    Layout:
        tmp_path/
            requirements.txt
            requirements-test.txt
            team/
                scripts/
                    test_script.py

    Returns (team_dir, repo_root).
    """
    repo_root = tmp_path
    team_dir = tmp_path / "team"
    scripts_dir = team_dir / "scripts"
    scripts_dir.mkdir(parents=True)

    (repo_root / "requirements.txt").write_text(
        requirements_txt, encoding="utf-8"
    )
    (repo_root / "requirements-test.txt").write_text(
        requirements_test_txt, encoding="utf-8"
    )

    if script_content:
        (scripts_dir / "test_script.py").write_text(
            script_content, encoding="utf-8"
        )

    return team_dir, repo_root


# ---------------------------------------------------------------------------
# Test 1: Real-tree green
# ---------------------------------------------------------------------------


def test_real_tree_lint_clean():
    """The lint exits 0 on the actual codebase."""
    exit_code = _lint.main([
        "--team-dir", str(_TEAM_DIR),
        "--repo-root", str(_REPO_ROOT),
    ])
    assert exit_code == 0, (
        "lint_python_import_completeness found uncovered third-party imports "
        "in the real tree — add the missing package to requirements.txt or "
        "requirements-test.txt."
    )


# ---------------------------------------------------------------------------
# Test 2: Clean fixture green
# ---------------------------------------------------------------------------


def test_clean_fixture_exits_zero():
    """The lint exits 0 against the clean fixture project."""
    clean_dir = _FIXTURES_DIR / "clean_project"
    assert clean_dir.is_dir(), f"Fixture directory not found: {clean_dir}"

    exit_code = _lint.main([
        "--team-dir", str(clean_dir / "team"),
        "--repo-root", str(clean_dir),
    ])
    assert exit_code == 0, (
        f"Clean fixture unexpectedly triggered a lint failure in {clean_dir}"
    )


# ---------------------------------------------------------------------------
# Test 3: Stray-import fixture red
# ---------------------------------------------------------------------------


def test_stray_import_fixture_exits_nonzero():
    """The lint exits 1 against the stray-import fixture."""
    stray_dir = _FIXTURES_DIR / "stray_import_project"
    assert stray_dir.is_dir(), f"Fixture directory not found: {stray_dir}"

    exit_code = _lint.main([
        "--team-dir", str(stray_dir / "team"),
        "--repo-root", str(stray_dir),
    ])
    assert exit_code == 1, (
        f"Stray-import fixture did not trigger a lint failure (exit {exit_code}); "
        "the guard is not working."
    )


# ---------------------------------------------------------------------------
# Test 4: Stray-import fixture names module and source file
# ---------------------------------------------------------------------------


def test_stray_import_fixture_names_module_and_file(capsys):
    """The lint output names the missing module ('httpx') and the source file."""
    stray_dir = _FIXTURES_DIR / "stray_import_project"
    assert stray_dir.is_dir(), f"Fixture directory not found: {stray_dir}"

    exit_code = _lint.main([
        "--team-dir", str(stray_dir / "team"),
        "--repo-root", str(stray_dir),
    ])
    assert exit_code == 1

    captured = capsys.readouterr()
    output = captured.out

    assert "httpx" in output, (
        "Lint output does not name the missing module 'httpx'.\n"
        f"Captured stdout:\n{output}"
    )
    assert "stray_script.py" in output, (
        "Lint output does not name the source file 'stray_script.py'.\n"
        f"Captured stdout:\n{output}"
    )


# ---------------------------------------------------------------------------
# Test 5: Synthetic clean tree exits 0
# ---------------------------------------------------------------------------


def test_main_clean_synthetic_tree_exits_zero(tmp_path):
    """main() returns 0 for a synthetic tree where the import is declared."""
    team_dir, repo_root = _make_minimal_tree(
        tmp_path,
        requirements_txt="requests>=2.0\n",
        script_content=textwrap.dedent("""\
            import os
            import sys
            import requests
        """),
    )
    exit_code = _lint.main([
        "--team-dir", str(team_dir),
        "--repo-root", str(repo_root),
    ])
    assert exit_code == 0


# ---------------------------------------------------------------------------
# Test 6: Synthetic tree with missing import exits 1
# ---------------------------------------------------------------------------


def test_main_missing_import_exits_one(tmp_path):
    """main() returns 1 for a synthetic tree with an undeclared import."""
    team_dir, repo_root = _make_minimal_tree(
        tmp_path,
        requirements_txt="requests>=2.0\n",
        script_content=textwrap.dedent("""\
            import requests
            import boto3  # not in requirements
        """),
    )
    exit_code = _lint.main([
        "--team-dir", str(team_dir),
        "--repo-root", str(repo_root),
    ])
    assert exit_code == 1


# ---------------------------------------------------------------------------
# Test 7: main() returns 2 when team-dir not found
# ---------------------------------------------------------------------------


def test_main_returns_2_when_team_dir_missing(tmp_path):
    """main() returns 2 when the specified team directory does not exist."""
    nonexistent = tmp_path / "does_not_exist" / "team"
    exit_code = _lint.main([
        "--team-dir", str(nonexistent),
        "--repo-root", str(tmp_path),
    ])
    assert exit_code == 2


# ---------------------------------------------------------------------------
# Test 8: --help exits 0
# ---------------------------------------------------------------------------


def test_help_flag_exits_zero():
    """--help prints usage and exits 0."""
    result = subprocess.run(
        [sys.executable, str(_LINT_SCRIPT), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"--help exited with code {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "lint_python_import_completeness" in result.stdout.lower() or \
           "import" in result.stdout.lower(), (
        "Help output does not contain expected content."
    )


# ---------------------------------------------------------------------------
# Test 9: Stdlib imports are not flagged
# ---------------------------------------------------------------------------


def test_stdlib_imports_not_flagged(tmp_path):
    """A file that imports only stdlib modules produces no violations."""
    team_dir, repo_root = _make_minimal_tree(
        tmp_path,
        requirements_txt="",
        script_content=textwrap.dedent("""\
            import os
            import sys
            import pathlib
            import json
            import re
            from collections import defaultdict
        """),
    )
    exit_code = _lint.main([
        "--team-dir", str(team_dir),
        "--repo-root", str(repo_root),
    ])
    assert exit_code == 0


# ---------------------------------------------------------------------------
# Test 10: Relative imports are not flagged
# ---------------------------------------------------------------------------


def test_relative_imports_not_flagged(tmp_path):
    """A file with relative imports produces no violations (relative = local)."""
    team_dir, repo_root = _make_minimal_tree(
        tmp_path,
        requirements_txt="",
        script_content=textwrap.dedent("""\
            from . import sibling
            from .utils import helper
        """),
    )
    exit_code = _lint.main([
        "--team-dir", str(team_dir),
        "--repo-root", str(repo_root),
    ])
    assert exit_code == 0


# ---------------------------------------------------------------------------
# Test 11: parse_requirements_file handles -r includes
# ---------------------------------------------------------------------------


def test_parse_requirements_handles_includes(tmp_path):
    """parse_requirements_file resolves -r include directives."""
    repo_root = tmp_path
    (repo_root / "requirements.txt").write_text(
        "requests>=2.0\npydantic>=2.0\n", encoding="utf-8"
    )
    (repo_root / "requirements-test.txt").write_text(
        "-r requirements.txt\npytest>=7\n", encoding="utf-8"
    )

    packages = _lint.build_requirements_union(repo_root)

    # All three packages must appear in the union.
    assert "requests" in packages, f"'requests' missing from union: {packages}"
    assert "pydantic" in packages, f"'pydantic' missing from union: {packages}"
    assert "pytest" in packages, f"'pytest' missing from union: {packages}"


# ---------------------------------------------------------------------------
# Test 12: _PACKAGE_TO_IMPORT_NAMES mapping for PyYAML
# ---------------------------------------------------------------------------


def test_pyyaml_maps_to_yaml(tmp_path):
    """PyYAML declared in requirements maps to import name 'yaml'."""
    repo_root = tmp_path
    (repo_root / "requirements.txt").write_text(
        "PyYAML>=6.0\n", encoding="utf-8"
    )
    (repo_root / "requirements-test.txt").write_text("", encoding="utf-8")

    packages = _lint.build_requirements_union(repo_root)
    covered = _lint._packages_to_import_names(packages)

    assert "yaml" in covered, (
        f"PyYAML declaration should produce 'yaml' in covered imports, got: {covered}"
    )
