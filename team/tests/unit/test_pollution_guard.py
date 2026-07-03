"""
test_pollution_guard.py
=======================
Unit tests for the squash pollution guard logic in team/scripts/cm/release.sh
(Step 4f).

These tests directly exercise the guard's path-matching patterns by running
the filter logic in isolation — feeding a controlled list of paths and
asserting on which paths are flagged or allowed through.

Key behaviors verified:
  - Template source files under team/templates/ are NEVER flagged, regardless
    of filename (BUG-TEMPLATE.md, PRIORITY-TEMPLATE.md, etc.).
  - A genuine intake stray (PRIORITY-*.md deposited in an intake-shaped dir
    outside team/templates/ and outside projects/<name>/priority/) IS flagged.
  - Correctly-placed priority files (projects/<name>/priority/PRIORITY-*.md)
    pass silently.

The guard logic is extracted into a standalone bash function (run_guard) that
accepts a newline-separated list of paths on stdin and echoes each flagged
path to stdout.  This lets the tests inject arbitrary path lists without
needing a real git repo or a real RC branch.

Test naming follows SOP.md Anti-pattern 6: names describe behavior, not bug
IDs or version numbers.
"""

from __future__ import annotations

import pathlib
import textwrap

import pytest

from tests.unit.shell_harness import run_bash

# ---------------------------------------------------------------------------
# Guard bash snippet
# ---------------------------------------------------------------------------
# This reproduces the inner loop logic from Step 4f of release.sh so it can
# be exercised in isolation.  When the guard changes, this snippet must be
# kept in sync.
#
# run_guard reads paths from stdin (one per line) and prints each flagged path
# to stdout with its reason tag.  A path that is NOT flagged produces no
# output for that line.

_GUARD_SNIPPET = textwrap.dedent("""\
    run_guard() {
        local _flagged=()
        while IFS= read -r _gpath; do
            [[ -z "$_gpath" ]] && continue

            # --- Templates tree: location-based exclusion (BUG-0012) ---
            # Files under team/templates/ are framework scaffolding (source), not
            # deposited intake items.  Skip all pattern checks for this tree.
            if [[ "$_gpath" == team/templates/* ]]; then
                continue
            fi

            # --- Pattern (a): repo-root artifacts/ tree ---
            if [[ "$_gpath" == artifacts/* ]]; then
                _flagged+=("$_gpath  [stray: repo-root artifacts/]")
                continue
            fi

            # --- Pattern (c): task-ID-named dirs outside projects/<name>/tasks/ ---
            if echo "$_gpath" | grep -qE '^[A-Z]+-[0-9]{8}-[0-9]+-[a-z0-9-]+/' ; then
                _flagged+=("$_gpath  [stray: task-ID-named path outside projects/<name>/tasks/]")
                continue
            fi
            if echo "$_gpath" | grep -qE '^team/[A-Z]+-[0-9]{8}-[0-9]+-[a-z0-9-]+/' ; then
                _flagged+=("$_gpath  [stray: task-ID-named path under team/ (outside projects/<name>/tasks/)]")
                continue
            fi

            # --- Pattern (d): PRIORITY-*.md misplaced intake files ---
            _basename_gpath_d="$(basename "$_gpath")"
            if [[ "$_basename_gpath_d" == PRIORITY-*.md ]]; then
                if [[ "$_gpath" != projects/*/priority/PRIORITY-*.md ]]; then
                    _flagged+=("$_gpath  [stray: priority-intake file outside projects/<name>/priority/]")
                    continue
                fi
            fi

            # --- Pattern (e): requirements v*.md misplaced intake files ---
            _basename_gpath_e="$(basename "$_gpath")"
            if [[ "$_basename_gpath_e" == v*.md ]]; then
                if [[ "$_gpath" != projects/*/requirements/v*.md ]]; then
                    _flagged+=("$_gpath  [stray: requirements-intake file outside projects/<name>/requirements/]")
                    continue
                fi
            fi

        done
        for _f in "${_flagged[@]}"; do
            echo "$_f"
        done
    }
""")


def _run_guard(tmp_path: pathlib.Path, paths: list[str]) -> str:
    """Run the guard logic over *paths* and return the flagged-path output.

    Each element of *paths* is a repo-relative file path, as the real guard
    receives from ``git diff --name-only``.  Returns the concatenated stdout
    of the guard function (one flagged path per line; empty string if nothing
    was flagged).

    Path list is written to a temp file and fed to the guard via redirection
    so that newlines are real newlines, not Python repr escape sequences.
    """
    path_file = tmp_path / "guard_paths.txt"
    path_file.write_text("\n".join(paths) + "\n", encoding="utf-8")
    script = _GUARD_SNIPPET + f"\nrun_guard < {path_file!s}"
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, (
        f"Guard snippet exited {result.returncode}; stderr: {result.stderr!r}"
    )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Template source tree — must NEVER be flagged
# ---------------------------------------------------------------------------


def test_priority_template_in_templates_tree_is_not_flagged(
    tmp_path: pathlib.Path,
) -> None:
    """PRIORITY-TEMPLATE.md under team/templates/ must pass silently.

    This is the canonical false-positive from BUG-0012: the guard flagged
    legitimate template source files because they matched the PRIORITY-*.md
    pattern without a location-based exclusion.
    """
    paths = [
        "team/templates/project/release/PRIORITY-TEMPLATE.md",
        "team/templates/project/document/PRIORITY-TEMPLATE.md",
    ]
    output = _run_guard(tmp_path, paths)
    assert output == "", (
        "Template source PRIORITY-TEMPLATE.md files should not be flagged; "
        f"guard produced: {output!r}"
    )


def test_bug_template_in_templates_tree_is_not_flagged(
    tmp_path: pathlib.Path,
) -> None:
    """BUG-TEMPLATE.md under team/templates/ must pass silently."""
    paths = [
        "team/templates/project/release/BUG-TEMPLATE.md",
        "team/templates/project/document/BUG-TEMPLATE.md",
    ]
    output = _run_guard(tmp_path, paths)
    assert output == "", (
        "Template source BUG-TEMPLATE.md files should not be flagged; "
        f"guard produced: {output!r}"
    )


def test_requirements_template_in_templates_tree_is_not_flagged(
    tmp_path: pathlib.Path,
) -> None:
    """REQUIREMENTS-TEMPLATE.md under team/templates/ must pass silently."""
    paths = [
        "team/templates/project/release/REQUIREMENTS-TEMPLATE.md",
        "team/templates/project/document/REQUIREMENTS-TEMPLATE.md",
    ]
    output = _run_guard(tmp_path, paths)
    assert output == "", (
        "Template source REQUIREMENTS-TEMPLATE.md files should not be flagged; "
        f"guard produced: {output!r}"
    )


def test_tester_priority_template_in_agent_templates_is_not_flagged(
    tmp_path: pathlib.Path,
) -> None:
    """TESTER-PRIORITY-TEMPLATE.md under team/templates/agent/ must pass silently."""
    paths = ["team/templates/agent/TESTER-PRIORITY-TEMPLATE.md"]
    output = _run_guard(tmp_path, paths)
    assert output == "", (
        "Agent template TESTER-PRIORITY-TEMPLATE.md should not be flagged; "
        f"guard produced: {output!r}"
    )


def test_any_file_under_templates_tree_is_not_flagged(
    tmp_path: pathlib.Path,
) -> None:
    """All files under team/templates/ pass silently regardless of name.

    The exclusion is location-based — the entire team/templates/ subtree is
    unconditionally excluded so future template additions do not require guard
    updates.
    """
    paths = [
        "team/templates/project/release/RELEASE-NOTES-TEMPLATE.md",
        "team/templates/install/crontab.example",
        "team/templates/task/task-readme/README.md",
        "team/templates/task/task-status/status.md",
    ]
    output = _run_guard(tmp_path, paths)
    assert output == "", (
        "All files under team/templates/ should be allowed through; "
        f"guard produced: {output!r}"
    )


# ---------------------------------------------------------------------------
# Regression: genuine intake strays MUST still be flagged
# ---------------------------------------------------------------------------


def test_priority_file_in_intake_dir_is_flagged(tmp_path: pathlib.Path) -> None:
    """A PRIORITY-*.md deposited in a non-sanctioned location is still flagged.

    This is the regression guard: the fix must not blind the guard to real
    priority-intake strays.  A file at the repo root level that is NOT inside
    team/templates/ AND NOT inside projects/<name>/priority/ must be caught.
    """
    paths = ["PRIORITY-0042-some-important-thing.md"]
    output = _run_guard(tmp_path, paths)
    assert "stray" in output, (
        "A PRIORITY-*.md at repo root should be flagged as a stray; "
        f"guard produced: {output!r}"
    )


def test_priority_file_in_team_subdir_is_flagged(tmp_path: pathlib.Path) -> None:
    """A PRIORITY-*.md inside team/ but outside team/templates/ is flagged."""
    paths = ["team/tasks/PRIORITY-0001-urgent.md"]
    output = _run_guard(tmp_path, paths)
    assert "stray" in output, (
        "A PRIORITY-*.md in team/tasks/ should be flagged; "
        f"guard produced: {output!r}"
    )


def test_priority_file_in_wrong_project_subdir_is_flagged(
    tmp_path: pathlib.Path,
) -> None:
    """A PRIORITY-*.md in projects/<name>/ but outside priority/ is flagged."""
    paths = ["projects/my-project/backlog/PRIORITY-0007-todo.md"]
    output = _run_guard(tmp_path, paths)
    assert "stray" in output, (
        "A PRIORITY-*.md outside projects/<name>/priority/ should be flagged; "
        f"guard produced: {output!r}"
    )


# ---------------------------------------------------------------------------
# Correctly-placed files — must pass silently
# ---------------------------------------------------------------------------


def test_correctly_placed_priority_file_is_not_flagged(
    tmp_path: pathlib.Path,
) -> None:
    """A PRIORITY-*.md in its sanctioned location passes silently."""
    paths = ["projects/my-project/priority/PRIORITY-0042-feature-x.md"]
    output = _run_guard(tmp_path, paths)
    assert output == "", (
        "A correctly-placed PRIORITY-*.md should not be flagged; "
        f"guard produced: {output!r}"
    )


# ---------------------------------------------------------------------------
# Combined: mixed path list
# ---------------------------------------------------------------------------


def test_mixed_path_list_flags_only_strays(tmp_path: pathlib.Path) -> None:
    """A mixed list flags only the genuine strays, not the template source files.

    This is the core discrimination test: template source ignored, intake
    stray caught.
    """
    template_paths = [
        "team/templates/project/release/PRIORITY-TEMPLATE.md",
        "team/templates/project/document/BUG-TEMPLATE.md",
        "team/templates/agent/TESTER-PRIORITY-TEMPLATE.md",
    ]
    stray_paths = [
        "PRIORITY-0001-leaked-intake-item.md",  # at repo root — stray
    ]
    clean_paths = [
        "projects/acme/priority/PRIORITY-0042-valid.md",  # correctly placed
    ]

    all_paths = template_paths + stray_paths + clean_paths
    output = _run_guard(tmp_path, all_paths)

    flagged_lines = [ln for ln in output.splitlines() if ln.strip()]

    # Every stray must appear in the output
    for stray in stray_paths:
        assert any(stray in ln for ln in flagged_lines), (
            f"Expected {stray!r} to be flagged but it was not; "
            f"guard output:\n{output}"
        )

    # Template source and clean paths must NOT appear in the output
    for safe in template_paths + clean_paths:
        assert not any(safe in ln for ln in flagged_lines), (
            f"Expected {safe!r} to be allowed through but it was flagged; "
            f"guard output:\n{output}"
        )

    # Exactly the stray paths should be flagged
    assert len(flagged_lines) == len(stray_paths), (
        f"Expected exactly {len(stray_paths)} flagged path(s), "
        f"got {len(flagged_lines)}; guard output:\n{output}"
    )
