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
  - Strict mode (CM_SQUASH_STRICT_POLLUTION_GUARD=true) HALTs on trigger 10
    through the real cm_halt, and the HALT's error key, reason and remedy
    also reach the project's operator inbox; warn mode writes neither.

The guard logic is extracted into a standalone bash function (run_guard) that
accepts a newline-separated list of paths on stdin and echoes each flagged
path to stdout.  This lets the tests inject arbitrary path lists without
needing a real git repo or a real RC branch.

Test naming follows roles/TESTER.md Anti-pattern 6: names describe behavior, not bug
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


@pytest.mark.parametrize(
    "paths, template_kind",
    [
        (
            [
                "team/templates/project/release/PRIORITY-TEMPLATE.md",
                "team/templates/project/document/PRIORITY-TEMPLATE.md",
            ],
            "PRIORITY-TEMPLATE.md",
        ),
        (
            [
                "team/templates/project/release/BUG-TEMPLATE.md",
                "team/templates/project/document/BUG-TEMPLATE.md",
            ],
            "BUG-TEMPLATE.md",
        ),
        (
            [
                "team/templates/project/release/REQUIREMENTS-TEMPLATE.md",
                "team/templates/project/document/REQUIREMENTS-TEMPLATE.md",
            ],
            "REQUIREMENTS-TEMPLATE.md",
        ),
    ],
)
def test_template_source_in_templates_tree_is_not_flagged(
    tmp_path: pathlib.Path, paths: list, template_kind: str
) -> None:
    """Template source files under team/templates/ pass silently, regardless of PRIORITY/BUG/REQUIREMENTS shape.

    This is the canonical false-positive from an earlier defect: the guard flagged
    legitimate template source files because they matched the *-TEMPLATE.md
    pattern without a location-based exclusion.
    """
    output = _run_guard(tmp_path, paths)
    assert output == "", (
        f"Template source {template_kind} files should not be flagged; "
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


@pytest.mark.parametrize(
    "stray_path, location_description",
    [
        ("PRIORITY-0042-some-important-thing.md", "repo root"),
        ("team/tasks/PRIORITY-0001-urgent.md", "team/tasks/"),
        ("projects/my-project/backlog/PRIORITY-0007-todo.md", "outside projects/<name>/priority/"),
    ],
)
def test_priority_file_outside_sanctioned_location_is_flagged(
    tmp_path: pathlib.Path, stray_path: str, location_description: str
) -> None:
    """A PRIORITY-*.md deposited outside team/templates/ or projects/<name>/priority/ is still flagged.

    This is the regression guard: the template-source and priority-directory
    exclusions must not blind the guard to genuine priority-intake strays.
    """
    output = _run_guard(tmp_path, [stray_path])
    assert "stray" in output, (
        f"A PRIORITY-*.md in {location_description} should be flagged; "
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


# ---------------------------------------------------------------------------
# Strict mode: the trigger 10 HALT also writes the operator inbox
# ---------------------------------------------------------------------------

_SHIP_POLICY_SH = pathlib.Path("scripts/lib/cm/release_ship_policy.sh")
_STRICT_GUARD_LINE = 'if [[ "${CM_SQUASH_STRICT_POLLUTION_GUARD:-false}" == "true" ]]; then'
_CM_LIBS = (
    "scripts/lib/error_contract.sh",
    "scripts/lib/pp_run_ops.sh",
    "scripts/lib/project_paths.sh",
    "scripts/lib/cm/release_preflight.sh",
)


def _extract_strict_block() -> str:
    """The strict-mode ``if ... fi`` block, verbatim from the live file."""
    lines = _SHIP_POLICY_SH.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == _STRICT_GUARD_LINE)
    indent = lines[start][: len(lines[start]) - len(lines[start].lstrip())]
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == f"{indent}fi")
    return "\n".join(lines[start : end + 1])


def _run_strict_block(tmp_path: pathlib.Path, strict: str) -> tuple:
    root = tmp_path / "kanban"
    project = root / "projects" / "demo"
    project.mkdir(parents=True)
    (root / "tmp").mkdir()
    (project / "release-state.md").write_text("# Release State\n\n## Active RC\nv1.2.3\n", encoding="utf-8")
    sources = "\n".join(f"source {lib}" for lib in _CM_LIBS)
    script = (
        "set -uo pipefail\n"
        f"{sources}\n"
        "PROJECT_NAME=demo\nACTIVE_RC=v1.2.3\nRC_BRANCH=ai_rc/v1.2.3\n"
        f"PROJECT_HALT_FILE={str(project / 'HALT')!r}\n"
        f"RELEASE_STATE={str(project / 'release-state.md')!r}\n"
        "_guard_flagged=('artifacts/stray.md  [stray: repo-root artifacts/]')\n"
        f"{_extract_strict_block()}\n"
        "echo 'guard: continued'\n"
    )
    env = {
        "KANBAN_ROOT": str(root),
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
        "PGAI_PROJECT_ROOT": "",
        "PGAI_TASKS_DIR": "",
        "CM_SQUASH_STRICT_POLLUTION_GUARD": strict,
    }
    return run_bash(tmp_path, script, extra_env=env, timeout=60), project


def test_strict_mode_halt_writes_one_inbox_entry_with_the_halt_triple(tmp_path: pathlib.Path) -> None:
    result, project = _run_strict_block(tmp_path, "true")

    assert result.returncode == 1, result.stderr
    halt = (project / "HALT").read_text(encoding="utf-8")
    key = "cm-release.trigger-10.pollution-guard-strict"
    assert f"# halt-reason: {key}" in halt, halt
    message = next(l for l in halt.splitlines() if l.startswith("# message: ")).removeprefix("# message: ")
    remedy = next(l for l in halt.splitlines() if l.startswith("# remedy:  ")).removeprefix("# remedy:  ")
    text = (project / "operator-inbox.md").read_text(encoding="utf-8")
    assert text.count("### INBOX-") == 1, text
    entry = text.replace("\n  ", "\n")
    assert key in entry and message in entry and f"Remedy: {remedy}" in entry, entry
    assert "guard: continued" not in result.stdout


def test_warn_mode_writes_no_halt_and_no_inbox_entry(tmp_path: pathlib.Path) -> None:
    result, project = _run_strict_block(tmp_path, "false")

    assert result.returncode == 0, result.stderr
    assert "guard: continued" in result.stdout
    assert not (project / "HALT").exists()
    assert not (project / "operator-inbox.md").exists()
