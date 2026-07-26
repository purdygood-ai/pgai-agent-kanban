#!/usr/bin/env python3
"""
lint_python_import_completeness.py
===================================
Pre-flight lint that scans Python files for third-party imports and verifies
each imported package is declared in requirements.txt or requirements-test.txt.

The lint prevents the class of failure where a new import is added to the
codebase but the corresponding pip package is not added to the requirements
files, causing ModuleNotFoundError inside a container or a fresh environment.

SCAN DIRECTORIES
----------------
By default, the lint walks three directory trees relative to the team/ root:

  team/scripts/                — Python entry-point scripts
  team/pgai_agent_kanban/      — the main Python package
  team/tests/                  — test suite

REQUIREMENTS FILES
------------------
The lint reads the union of requirements.txt and requirements-test.txt, both
located at the repository root (two levels above team/).  The ``-r <file>``
include directives in requirements-test.txt are resolved automatically.

STDLIB EXCLUSION
----------------
The standard-library module set is derived from ``sys.stdlib_module_names``
(available on Python 3.10+, targeting Python >= 3.12 per the project's
pyproject.toml).  No additional hard-coded list is used — the stdlib set is
always authoritative for the running interpreter.

LOCAL PACKAGE EXCLUSION
-----------------------
Imports whose top-level name resolves to a file or directory under the scan
trees (i.e. packages developed in this repo) are excluded from the check.
The exclusion is computed dynamically by walking team/ for ``__init__.py``
markers, top-level ``.py`` files, and known root package names
(``pgai_agent_kanban``, ``tests``, etc.).

PACKAGE NAME → IMPORT NAME MAPPING
------------------------------------
Pip package names do not always match the Python import name they provide
(e.g. ``PyYAML`` installs as ``yaml``).  The canonical mapping used by this
lint is declared in ``_PACKAGE_TO_IMPORT_NAMES`` below and should be updated
whenever a new package with a non-obvious import name is added to the
requirements files.

EXIT CODES
----------
  0   All third-party imports are covered by the requirements union.
  1   One or more imports are not covered; see stdout for details.
  2   Usage error or the specified directory was not found.

USAGE
-----
  python3 scripts/lint_python_import_completeness.py [OPTIONS]

  --team-dir PATH         Root of the team/ directory (default: parent of
                          this script's directory, i.e. team/ when the
                          script lives at team/scripts/).
  --repo-root PATH        Repository root (default: two levels above team/).
                          Used to locate requirements.txt and
                          requirements-test.txt.
  --verbose, -v           Print each file as it is checked.

EXAMPLES
--------
  # From the repository root:
  python3 team/scripts/lint_python_import_completeness.py

  # With explicit paths:
  python3 team/scripts/lint_python_import_completeness.py \\
      --team-dir /path/to/team \\
      --repo-root /path/to/repo
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Directories scanned relative to team/.
# ---------------------------------------------------------------------------

_SCAN_SUBDIRS: tuple[str, ...] = (
    "scripts",
    "pgai_agent_kanban",
    "tests",
)

# ---------------------------------------------------------------------------
# Directory names excluded from the scan.
#
# Files under directories whose name (not full path) matches one of these
# entries are skipped.  The exclusions prevent deliberate bad-example fixtures
# and virtual-environment artifacts from being evaluated.
#
# ``fixtures`` — the tests/fixtures/ subdirectory contains intentional
#    anti-pattern examples used by lint unit tests.  Scanning them would
#    produce false violations (the stray imports are planted deliberately).
# ``__pycache__`` — compiled bytecode; not source.
# ``.venv`` — virtualenv packages; third-party code, not project source.
# ---------------------------------------------------------------------------

_EXCLUDED_DIR_NAMES: frozenset[str] = frozenset({
    "fixtures",
    "__pycache__",
    ".venv",
})

# ---------------------------------------------------------------------------
# Requirements files, relative to the repository root.
# ---------------------------------------------------------------------------

_REQUIREMENTS_FILES: tuple[str, ...] = (
    "requirements.txt",
    "requirements-test.txt",
)

# ---------------------------------------------------------------------------
# Canonical pip-package-name → Python import name(s) mapping.
#
# Keys are normalised pip package names (lower-case, hyphens → underscores).
# Values are the set of top-level Python module names that package installs.
#
# Add an entry here when the pip package name differs from the import name, or
# when a single package installs multiple importable top-level names, or when
# a package (e.g. a pytest plugin) installs no importable top-level module at
# all (map to an empty set to mark it as "declared but not directly imported").
#
# Packages whose pip name matches the import name (e.g. ``pydantic``,
# ``fastapi``, ``pytest``) do not need an entry — the default normalisation
# (lower-case, hyphens → underscores) handles them automatically.
# ---------------------------------------------------------------------------

_PACKAGE_TO_IMPORT_NAMES: dict[str, frozenset[str]] = {
    # PyYAML installs as the 'yaml' module.
    "pyyaml": frozenset({"yaml"}),
    # pytest-cov is a pytest plugin — it does not expose a top-level import.
    "pytest_cov": frozenset(),
}


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


class ImportViolation(NamedTuple):
    """A single import-completeness violation."""

    source_file: Path   # Python file containing the import
    module_name: str    # Top-level import name that is not covered


# ---------------------------------------------------------------------------
# Requirements parsing
# ---------------------------------------------------------------------------


def _normalise_pkg_name(name: str) -> str:
    """Normalise a pip package name to the canonical comparison key.

    Pip normalises package names by lower-casing and replacing hyphens,
    underscores, and dots with a single canonical separator.  We use
    lower-case + hyphen-to-underscore as a lightweight approximation that
    covers every package in the current requirements files.
    """
    return name.lower().replace("-", "_").replace(".", "_")


def _strip_extras(name: str) -> str:
    """Remove the extras bracket from a package name, e.g. ``fastapi[standard]`` → ``fastapi``."""
    bracket = name.find("[")
    if bracket != -1:
        return name[:bracket]
    return name


def _strip_version(name: str) -> str:
    """Strip any version specifier from a requirement string.

    Examples:
        ``pydantic>=2.0``      → ``pydantic``
        ``pytest==7.4``        → ``pytest``
        ``PyYAML``             → ``PyYAML``
    """
    return re.split(r"[><=!~;]", name, maxsplit=1)[0].strip()


def parse_requirements_file(
    req_path: Path,
    *,
    repo_root: Path,
    _seen: set[Path] | None = None,
) -> set[str]:
    """Parse a requirements file and return the set of normalised package names.

    Handles:
      - Blank lines and comment lines (``#``).
      - ``-r <other-file>`` include directives (resolved relative to repo_root).
      - Version specifiers (``>=``, ``==``, ``~=``, etc.).
      - Extras (``fastapi[standard]``).

    Args:
        req_path:   Absolute path to the requirements file.
        repo_root:  Repository root, used to resolve ``-r`` includes.
        _seen:      Internal guard against circular includes.

    Returns:
        Set of normalised package names (e.g. ``{'fastapi', 'pydantic', 'pyyaml'}``).
    """
    if _seen is None:
        _seen = set()

    if req_path in _seen:
        return set()
    _seen.add(req_path)

    if not req_path.is_file():
        return set()

    packages: set[str] = set()
    for raw_line in req_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()

        # Skip blank lines and comments.
        if not line or line.startswith("#"):
            continue

        # Handle -r includes.
        if line.startswith("-r ") or line.startswith("-r\t"):
            included = repo_root / line[2:].strip()
            packages |= parse_requirements_file(
                included, repo_root=repo_root, _seen=_seen
            )
            continue

        # Skip other option lines (-c, --index-url, etc.).
        if line.startswith("-"):
            continue

        # Strip extras, version specifier, and normalise.
        bare = _strip_extras(_strip_version(line))
        if bare:
            packages.add(_normalise_pkg_name(bare))

    return packages


def build_requirements_union(repo_root: Path) -> set[str]:
    """Return the union of all declared package names from all requirements files.

    Args:
        repo_root:  Repository root directory.

    Returns:
        Set of normalised package names.
    """
    union: set[str] = set()
    for filename in _REQUIREMENTS_FILES:
        req_path = repo_root / filename
        union |= parse_requirements_file(req_path, repo_root=repo_root)
    return union


# ---------------------------------------------------------------------------
# Import-name set derivation from declared packages
# ---------------------------------------------------------------------------


def _packages_to_import_names(packages: set[str]) -> set[str]:
    """Convert a set of normalised pip package names to Python import names.

    For each package:
      1. Check ``_PACKAGE_TO_IMPORT_NAMES`` for an explicit mapping.
         - If found and the value is non-empty, add all mapped import names.
         - If found and the value is empty (plugin), skip (no import expected).
      2. Otherwise, the import name is the package name itself (normalised).

    Args:
        packages:  Set of normalised pip package names.

    Returns:
        Set of Python module names that are covered by the declared packages.
    """
    covered: set[str] = set()
    for pkg in packages:
        if pkg in _PACKAGE_TO_IMPORT_NAMES:
            covered |= _PACKAGE_TO_IMPORT_NAMES[pkg]
        else:
            # Default: the import name is the normalised package name.
            covered.add(pkg)
    return covered


# ---------------------------------------------------------------------------
# Local package name discovery
# ---------------------------------------------------------------------------


def discover_local_names(team_dir: Path) -> set[str]:
    """Return the set of top-level Python names that are local to this repo.

    A name is "local" when it corresponds to a file or directory under team/
    that could be imported as a top-level module when team/ is on sys.path.

    This covers:
      - ``pgai_agent_kanban/`` (the main package)
      - ``scripts/*.py`` (lint scripts, pseudocron, etc.)
      - ``pm-agent/`` sub-directories and scripts
      - ``tests/`` package and its sub-packages

    Additionally, a fixed set of well-known local root names is always included
    to handle cases where the repo layout changes.

    Args:
        team_dir:  Absolute path to the team/ directory.

    Returns:
        Set of top-level module/package names that should NOT be treated as
        third-party.
    """
    # Seed with known root package names that are always local.
    local: set[str] = {"pgai_agent_kanban", "tests", "conftest", "pm_agent"}

    # Walk team/ for package directories (__init__.py present) and .py files.
    for path in team_dir.rglob("*"):
        if path.is_dir() and (path / "__init__.py").exists():
            local.add(path.name)
        elif path.is_file() and path.suffix == ".py" and path.name != "__init__.py":
            local.add(path.stem)

    return local


# ---------------------------------------------------------------------------
# AST-based import extraction
# ---------------------------------------------------------------------------


def extract_third_party_imports(
    py_file: Path,
    *,
    stdlib_names: frozenset[str],
    local_names: set[str],
) -> list[str]:
    """Return the list of third-party top-level import names in a Python file.

    Parses the file with ast.parse and walks Import and ImportFrom nodes.
    An import is "third-party" when its top-level name is:
      - Not in sys.stdlib_module_names
      - Not in local_names (in-repo modules/packages)
      - Not a relative import (level > 0)
      - Not a private name (starts with ``_``)

    SyntaxError is caught and reported as a warning on stderr; the file is
    then skipped (no imports collected from a file that cannot be parsed).

    Args:
        py_file:       Path to the Python source file.
        stdlib_names:  Frozen set of standard-library module names.
        local_names:   Set of local (in-repo) importable names to exclude.

    Returns:
        Sorted list of distinct third-party top-level import names.
    """
    try:
        source = py_file.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(py_file))
    except SyntaxError as exc:
        print(
            f"WARNING: cannot parse {py_file}: {exc}",
            file=sys.stderr,
        )
        return []

    found: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if _is_third_party(top, stdlib_names, local_names):
                    found.add(top)

        elif isinstance(node, ast.ImportFrom):
            # Relative imports (level > 0) are always local.
            if node.level and node.level > 0:
                continue
            if node.module:
                top = node.module.split(".")[0]
                if _is_third_party(top, stdlib_names, local_names):
                    found.add(top)

    return sorted(found)


def _is_third_party(
    name: str,
    stdlib_names: frozenset[str],
    local_names: set[str],
) -> bool:
    """Return True when name is a third-party top-level import."""
    if not name:
        return False
    if name.startswith("_"):
        return False
    if name in stdlib_names:
        return False
    if name in local_names:
        return False
    return True


# ---------------------------------------------------------------------------
# Core lint runner
# ---------------------------------------------------------------------------


def lint_import_completeness(
    team_dir: Path,
    repo_root: Path,
    *,
    verbose: bool = False,
) -> list[ImportViolation]:
    """Scan the declared directories for uncovered third-party imports.

    Args:
        team_dir:  Absolute path to the team/ directory.
        repo_root: Absolute path to the repository root (contains requirements files).
        verbose:   When True, print each file being checked.

    Returns:
        Sorted list of ImportViolation tuples (source_file, module_name).
        Empty list means the lint is green.
    """
    stdlib_names: frozenset[str] = frozenset(sys.stdlib_module_names)
    local_names = discover_local_names(team_dir)

    # Build the set of covered import names from the requirements union.
    req_packages = build_requirements_union(repo_root)
    covered_imports = _packages_to_import_names(req_packages)

    if verbose:
        print(f"lint_python_import_completeness: declared packages: {sorted(req_packages)}")
        print(f"lint_python_import_completeness: covered imports: {sorted(covered_imports)}")
        print(f"lint_python_import_completeness: local names (sample): "
              f"{sorted(local_names)[:10]} ...")

    violations: list[ImportViolation] = []

    for subdir in _SCAN_SUBDIRS:
        scan_dir = team_dir / subdir
        if not scan_dir.is_dir():
            if verbose:
                print(f"lint_python_import_completeness: directory not found, skipping: {scan_dir}")
            continue

        for py_file in sorted(scan_dir.rglob("*.py")):
            # Skip files under excluded directories (e.g. fixtures/, __pycache__).
            # Check only the parts RELATIVE TO scan_dir, not the full absolute path,
            # so that running the lint against a fixture tree that itself lives
            # under a 'fixtures/' directory does not accidentally exclude its files.
            try:
                relative_parts = py_file.relative_to(scan_dir).parts
            except ValueError:
                relative_parts = py_file.parts
            if any(part in _EXCLUDED_DIR_NAMES for part in relative_parts):
                if verbose:
                    print(f"lint_python_import_completeness: [excluded] {py_file}")
                continue

            if verbose:
                print(f"lint_python_import_completeness: checking {py_file}")

            third_party = extract_third_party_imports(
                py_file,
                stdlib_names=stdlib_names,
                local_names=local_names,
            )

            for mod_name in third_party:
                norm = _normalise_pkg_name(mod_name)
                if norm not in covered_imports and mod_name not in covered_imports:
                    violations.append(ImportViolation(
                        source_file=py_file,
                        module_name=mod_name,
                    ))

    # Sort violations for deterministic output: by module name, then by source file.
    violations.sort(key=lambda v: (v.module_name, str(v.source_file)))
    return violations


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lint_python_import_completeness.py",
        description=(
            "Lint that every third-party Python import across team/scripts/, "
            "team/pgai_agent_kanban/, and team/tests/ is declared in "
            "requirements.txt or requirements-test.txt.  Exits 1 when any "
            "import is not covered."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exit codes:\n"
            "  0  All third-party imports are covered.\n"
            "  1  One or more imports are not covered; see stdout.\n"
            "  2  Usage error or directory not found.\n"
        ),
    )
    parser.add_argument(
        "--team-dir",
        metavar="PATH",
        default=None,
        help=(
            "Root of the team/ directory (default: parent of this script's "
            "directory, i.e. team/ when the script lives at team/scripts/)."
        ),
    )
    parser.add_argument(
        "--repo-root",
        metavar="PATH",
        default=None,
        help=(
            "Repository root that contains requirements.txt and "
            "requirements-test.txt (default: two levels above team/)."
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Print each file as it is checked.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the import-completeness lint and return an exit code."""
    args = _parse_args(argv)

    # Resolve team_dir.
    if args.team_dir:
        team_dir = Path(args.team_dir).resolve()
    else:
        # Default: team/ is the parent of the directory containing this script.
        # This script lives at team/scripts/lint_python_import_completeness.py.
        team_dir = Path(__file__).resolve().parent.parent

    # Resolve repo_root.
    if args.repo_root:
        repo_root = Path(args.repo_root).resolve()
    else:
        # Default: two levels up from team/ (team/../).
        repo_root = team_dir.parent

    if not team_dir.is_dir():
        print(
            f"ERROR: team directory not found: {team_dir}\n"
            "Use --team-dir to specify the correct team/ root.",
            file=sys.stderr,
        )
        return 2

    if not repo_root.is_dir():
        print(
            f"ERROR: repository root not found: {repo_root}\n"
            "Use --repo-root to specify the repository root.",
            file=sys.stderr,
        )
        return 2

    print(
        f"lint_python_import_completeness: team dir: {team_dir}",
        flush=True,
    )
    print(
        f"lint_python_import_completeness: repo root: {repo_root}",
        flush=True,
    )

    violations = lint_import_completeness(team_dir, repo_root, verbose=args.verbose)

    if violations:
        print(
            f"\nlint_python_import_completeness: FAIL — "
            f"{len(violations)} uncovered import(s):\n",
            flush=True,
        )
        for v in violations:
            print(
                f"  missing: '{v.module_name}' imported in {v.source_file}",
                flush=True,
            )
        print(
            "\nFix: add the missing package to requirements.txt or "
            "requirements-test.txt, or add a mapping to "
            "_PACKAGE_TO_IMPORT_NAMES in "
            "team/scripts/lint_python_import_completeness.py if the pip "
            "package name differs from the import name.",
            flush=True,
        )
        return 1

    print(
        "lint_python_import_completeness: ok — all third-party imports are covered.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
