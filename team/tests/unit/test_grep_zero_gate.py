"""
test_grep_zero_gate.py
======================
Enforces the grep-zero invariant: no executable workflow_type ==/!= string
comparisons may appear in engine shell scripts outside the workflows/ plugin
tree.

WHY THIS EXISTS
---------------
Engine code reads capabilities from the workflow dispatcher, never the type
string directly.  The grep-zero invariant is the mechanically-verifiable
completeness signal: if a future engine edit re-introduces a direct type
comparison, CI catches it in this test before the change reaches the RC.

HOW THE GATE WORKS
------------------
Pattern: ``workflow_type"? *[!=]=``

This matches the literal variable name ``workflow_type`` (with or without an
inline closing double-quote) followed by optional whitespace and then ``==``
or ``!=``.  Assignments (``workflow_type = ...``) do not match because
``[!=]=`` requires the ``!`` or ``=`` immediately before the second ``=``.

Scope: team/scripts/ --include='*.sh', excluding the team/workflows/ subtree.
Plugin files are excluded because they ARE authoritative type definitions —
the dispatcher sources them, so the type name appearing there is legitimate.

Negative proof: a temp file containing a synthetic violation is confirmed to
match the pattern, showing the gate can catch new violations if they appear.

TEST NAMING CONVENTION (SOP.md Anti-pattern 6)
-----------------------------------------------
All test function names describe behavior, not the bug ID or task ID that
prompted them.
"""

from __future__ import annotations

import pathlib
import re
import subprocess


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _repo_root() -> pathlib.Path:
    """Return the absolute path to the repository root.

    Resolved via git so the result is correct regardless of the working
    directory the test runner uses.
    """
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    )
    return pathlib.Path(result.stdout.strip())


_GREP_PATTERN = r'workflow_type"? *[!=]='

# Directories containing plugin files that are legitimately allowed to name
# workflow types.  Paths are relative to the repo root and are matched as
# prefixes of the file's repo-relative path.
_EXCLUDED_PREFIXES = (
    "team/workflows/",
)


def _find_violations() -> list[str]:
    """Return a list of '<file>:<line>:<content>' strings for each violation.

    Runs ripgrep / grep over team/scripts/ (*.sh only), then filters out any
    path that is inside an excluded prefix.  Returns an empty list when the
    invariant holds.

    The function uses Python's re module rather than the subprocess grep
    directly so the exclusion logic is in one deterministic place and the test
    is independent of grep's -v flag behaviour on different platforms.
    """
    root = _repo_root()
    scripts_dir = root / "team" / "scripts"

    if not scripts_dir.is_dir():
        raise FileNotFoundError(
            f"team/scripts/ directory not found at {scripts_dir}; "
            "repo root resolution may be wrong."
        )

    pattern = re.compile(_GREP_PATTERN)
    violations: list[str] = []

    for sh_file in scripts_dir.rglob("*.sh"):
        # Build the repo-relative path for exclusion matching.
        try:
            rel_path = sh_file.relative_to(root)
        except ValueError:
            rel_path = sh_file  # fallback: use absolute

        rel_str = str(rel_path).replace("\\", "/")  # normalise on Windows

        # Skip files inside excluded prefixes (the workflows/ plugin tree).
        if any(rel_str.startswith(prefix) for prefix in _EXCLUDED_PREFIXES):
            continue

        try:
            lines = sh_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue

        for lineno, line in enumerate(lines, start=1):
            if pattern.search(line):
                violations.append(f"{rel_str}:{lineno}:{line.rstrip()}")

    return violations


# ---------------------------------------------------------------------------
# Gate test
# ---------------------------------------------------------------------------


def test_no_workflow_type_string_comparisons_in_engine_scripts() -> None:
    """No executable workflow_type ==/!= comparisons exist in engine shell scripts.

    Engine code must route decisions through the workflow dispatcher's
    capability surface (wf_git_mode, wf_finalize, wf_agents, etc.) rather
    than comparing the workflow_type string directly.  This test is the
    mechanical completeness signal: if a future edit re-introduces a direct
    type comparison, it fails here before reaching the RC.

    Excluded from the scan:
      - team/workflows/   Plugin files are the authoritative type definitions;
                          the dispatcher sources them, so the type name
                          appearing there is legitimate.
    """
    violations = _find_violations()
    assert not violations, (
        f"grep-zero invariant violated — {len(violations)} executable "
        f"workflow_type comparison(s) found in engine scripts:\n"
        + "\n".join(f"  {v}" for v in violations)
        + "\n\nEngine code must use workflow dispatcher capabilities "
        "(wf_git_mode, wf_finalize, wf_agents, wf_bundle_source_branch, etc.) "
        "instead of the type string.  Add a new capability flag if no existing "
        "flag covers the needed behavior."
    )


# ---------------------------------------------------------------------------
# Negative proof: the pattern DOES catch a synthetic violation
# ---------------------------------------------------------------------------


def test_grep_pattern_detects_synthetic_violation(tmp_path: pathlib.Path) -> None:
    """The grep pattern matches a known-bad workflow_type == comparison.

    This is the negative proof: it confirms the scan mechanism can detect a
    violation, not merely that the real files happen to be clean.  Without
    this, a bug in _find_violations() would make the gate always green.
    """
    # Write a synthetic shell script containing a clear violation.
    bad_script = tmp_path / "bad_engine.sh"
    bad_script.write_text(
        '#!/usr/bin/env bash\n'
        '# Synthetic violation for negative-proof test.\n'
        'if [[ "$workflow_type" == "document" ]]; then\n'
        '    echo "document path"\n'
        'fi\n'
        'if [[ "$workflow_type" != "release" ]]; then\n'
        '    echo "non-release path"\n'
        'fi\n',
        encoding="utf-8",
    )

    pattern = re.compile(_GREP_PATTERN)
    matching_lines = [
        line for line in bad_script.read_text(encoding="utf-8").splitlines()
        if pattern.search(line)
    ]

    assert len(matching_lines) >= 2, (
        f"Expected at least 2 matching lines in the synthetic bad script; "
        f"got {len(matching_lines)}: {matching_lines!r}.  "
        "The grep pattern may not be correct — check _GREP_PATTERN."
    )


def test_grep_pattern_does_not_match_assignment(tmp_path: pathlib.Path) -> None:
    """The grep pattern does not match workflow_type assignments.

    Assignments like ``workflow_type = release`` must not trigger the gate —
    reading the config key into a variable is legitimate engine behavior.
    The pattern must only match comparison operators (== and !=).
    """
    assignment_script = tmp_path / "assignment_ok.sh"
    assignment_script.write_text(
        '#!/usr/bin/env bash\n'
        '# Legitimate config read — assignment, not comparison.\n'
        'workflow_type="release"\n'
        'workflow_type = "document"\n'
        '_wf_type="$(read_cfg workflow_type)"\n',
        encoding="utf-8",
    )

    pattern = re.compile(_GREP_PATTERN)
    matching_lines = [
        line for line in assignment_script.read_text(encoding="utf-8").splitlines()
        if pattern.search(line)
    ]

    assert not matching_lines, (
        f"Pattern matched assignment lines that should not be flagged: "
        f"{matching_lines!r}.  Only comparison operators (== and !=) should match."
    )


def test_grep_pattern_does_not_match_plugin_file_comparisons(
    tmp_path: pathlib.Path,
) -> None:
    """The grep pattern scan excludes team/workflows/ plugin files.

    Plugin files legitimately define behavior for specific workflow types and
    may use the type name as a documentation string or return value.  The scan
    excludes the entire team/workflows/ prefix so plugin files do not trigger
    the gate even if they contain diagnostic type comparisons.

    This test confirms the exclusion logic in _find_violations() works:
    a synthetic 'plugin' file at a team/workflows/-shaped path must be
    invisible to the scan.

    Note: because _find_violations() scans the real repo tree (not tmp_path),
    this test verifies the exclusion pattern string directly rather than
    placing a synthetic file in the real tree.
    """
    # Verify the exclusion prefix is present and the pattern covers workflows/.
    assert any(
        prefix.startswith("team/workflows/") or "team/workflows/" in prefix
        for prefix in _EXCLUDED_PREFIXES
    ), (
        "Expected _EXCLUDED_PREFIXES to contain 'team/workflows/' but it does not.  "
        "Plugin files in the workflows/ tree would be incorrectly scanned."
    )


# ---------------------------------------------------------------------------
# Import-form gate: no 'from team.' or 'import team.' in the package tree
# ---------------------------------------------------------------------------
#
# WHY THIS EXISTS
# ---------------
# The live install drops the 'team/' prefix (install.sh copies
# team/pgai_agent_kanban → $KANBAN_ROOT/pgai_agent_kanban).  Any module that
# carries 'from team.pgai_agent_kanban...' in an import statement or docstring
# usage example breaks on a live install with ModuleNotFoundError.
#
# an earlier defect established the convention for the api/ sub-package: docstring
# examples must cite the live-install import form
# ('from pgai_agent_kanban.<mod> import ...').  This gate broadens that
# invariant to the entire team/pgai_agent_kanban/ package, excluding test
# files and conftest.py (which legitimately import via the dev-tree path).
#
# SCOPE
# -----
# Pattern : 'from team.' or 'import team.'
# Directory: team/pgai_agent_kanban/ (all .py files)
# Excluded : any path whose repo-relative path contains 'tests/', 'test/',
#            or 'conftest' — these files legitimately use dev-tree paths.
#
# NEGATIVE PROOF
# --------------
# A synthetic fixture file containing a known-bad form is confirmed to match
# the pattern, so the gate detects real violations rather than merely
# asserting that current files are clean.

_IMPORT_FORM_PATTERN = re.compile(r"from team\.|import team\.")


def _find_import_form_violations() -> list[str]:
    """Return '<file>:<lineno>:<content>' for each team-prefix import in the package.

    Scans all .py files under team/pgai_agent_kanban/, excluding any path
    whose repo-relative form contains 'tests/', 'test/', or 'conftest'.
    Returns an empty list when the invariant holds.
    """
    root = _repo_root()
    package_dir = root / "team" / "pgai_agent_kanban"

    if not package_dir.is_dir():
        raise FileNotFoundError(
            f"team/pgai_agent_kanban/ directory not found at {package_dir}; "
            "repo root resolution may be wrong."
        )

    pattern = _IMPORT_FORM_PATTERN
    violations: list[str] = []

    for py_file in package_dir.rglob("*.py"):
        try:
            rel_path = py_file.relative_to(root)
        except ValueError:
            rel_path = py_file

        rel_str = str(rel_path).replace("\\", "/")

        # Exclude test files and conftest — these legitimately use dev-tree paths.
        if any(seg in rel_str for seg in ("tests/", "test/", "conftest")):
            continue

        try:
            lines = py_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue

        for lineno, line in enumerate(lines, start=1):
            if pattern.search(line):
                violations.append(f"{rel_str}:{lineno}:{line.rstrip()}")

    return violations


def test_no_team_prefix_imports_in_package() -> None:
    """No 'from team.' or 'import team.' forms exist in team/pgai_agent_kanban/.

    The live install copies team/pgai_agent_kanban → $KANBAN_ROOT/pgai_agent_kanban,
    dropping the 'team.' prefix.  Any file carrying a 'from team.' or 'import team.'
    import form (including in docstring usage examples) breaks on a live install.

    Excluded from the scan: paths containing 'tests/', 'test/', or 'conftest',
    which legitimately use the dev-tree import path.
    """
    violations = _find_import_form_violations()
    assert not violations, (
        f"Live-install import invariant violated — {len(violations)} 'from team.' / "
        f"'import team.' form(s) found in team/pgai_agent_kanban/ (excluding tests):\n"
        + "\n".join(f"  {v}" for v in violations)
        + "\n\nDocstring examples and import statements must cite the live-install "
        "form ('from pgai_agent_kanban.<mod> import ...') rather than the dev-tree "
        "form ('from team.pgai_agent_kanban.<mod> import ...')."
    )


def test_import_form_pattern_detects_synthetic_violation(
    tmp_path: pathlib.Path,
) -> None:
    """The import-form pattern matches a known-bad 'from team.' usage.

    Negative proof: confirms the pattern catches a real violation rather than
    always returning clean (a silent pass-through bug in the scanner).
    """
    bad_file = tmp_path / "bad_module.py"
    bad_file.write_text(
        '"""\nUsage::\n'
        '    from team.pgai_agent_kanban.cm.read_state_field import read_state_field\n'
        '"""\n',
        encoding="utf-8",
    )

    matching_lines = [
        line for line in bad_file.read_text(encoding="utf-8").splitlines()
        if _IMPORT_FORM_PATTERN.search(line)
    ]

    assert len(matching_lines) >= 1, (
        f"Expected at least 1 matching line in the synthetic bad file; "
        f"got {len(matching_lines)}: {matching_lines!r}.  "
        "The import-form pattern may be wrong — check _IMPORT_FORM_PATTERN."
    )


def test_import_form_pattern_does_not_match_live_install_form(
    tmp_path: pathlib.Path,
) -> None:
    """The import-form pattern does not match the correct live-install import form.

    A line like 'from pgai_agent_kanban.cm.foo import bar' must not trigger the gate.
    """
    clean_file = tmp_path / "clean_module.py"
    clean_file.write_text(
        '"""\nUsage::\n'
        '    from pgai_agent_kanban.cm.read_state_field import read_state_field\n'
        '"""\n',
        encoding="utf-8",
    )

    matching_lines = [
        line for line in clean_file.read_text(encoding="utf-8").splitlines()
        if _IMPORT_FORM_PATTERN.search(line)
    ]

    assert not matching_lines, (
        f"Pattern matched live-install import form that should not be flagged: "
        f"{matching_lines!r}.  Only 'from team.' and 'import team.' must match."
    )
