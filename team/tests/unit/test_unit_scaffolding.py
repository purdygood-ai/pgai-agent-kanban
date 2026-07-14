"""
test_unit_scaffolding.py
========================
Seed tests that verify the unit-test scaffolding is importable, functional,
and self-cleaning.

These tests are intentionally minimal.  Their purpose is to:
  1. Confirm that unit/conftest.py fixtures are discoverable by pytest.
  2. Confirm that shell_harness.run_bash() works end-to-end.
  3. Confirm that fixture-created directories are cleaned up by tmp_path.
  4. Produce at least one collected test so the gated runner (run-unit-tests.sh)
     exits 0 rather than 5 (NO_TESTS_COLLECTED) while the regenerated suite
     is still being authored.

Each test is named for the behavior it asserts, not for any scaffolding phase
or internal ID (SOP.md Anti-pattern 6).
"""

from __future__ import annotations

import pathlib

from tests.unit.shell_harness import run_bash


# ---------------------------------------------------------------------------
# Fixture smoke tests
# ---------------------------------------------------------------------------


def test_minimal_kanban_root_contains_release_state_file(
    minimal_kanban_root: pathlib.Path,
) -> None:
    """minimal_kanban_root fixture creates a release-state.md with idle state."""
    release_state = minimal_kanban_root / "release-state.md"
    assert release_state.exists(), (
        "minimal_kanban_root must create release-state.md in the root"
    )
    content = release_state.read_text(encoding="utf-8")
    assert "Active RC" in content, (
        "release-state.md must contain an 'Active RC' field"
    )
    assert "none" in content, (
        "Fresh synthetic kanban root must have Active RC: none"
    )


def test_installed_root_has_no_team_shim_directory(
    installed_root: pathlib.Path,
) -> None:
    """installed_root fixture mirrors the post-install layout without the dev-tree shim.

    After install.sh runs, the kanban root does not contain a team/ sub-directory.
    Tests using installed_root exercise code that must work without the shim package
    being importable from the kanban root, closing the test-fidelity gap where
    dev-tree tests silently relied on the shim (TESTING.md failure mode 2).
    """
    assert not (installed_root / "team").exists(), (
        "installed_root must not contain team/ — it mirrors the production layout "
        "without the dev-tree Python shim package"
    )


def test_installed_root_contains_workflow_definitions(
    installed_root: pathlib.Path,
) -> None:
    """installed_root fixture provides workflow plugin directories under workflows/."""
    workflows_dir = installed_root / "workflows"
    assert workflows_dir.is_dir(), (
        "installed_root must contain a workflows/ directory"
    )
    release_pipeline = workflows_dir / "release" / "pipeline.yaml"
    assert release_pipeline.exists(), (
        "installed_root/workflows/release/pipeline.yaml must exist (copied from dev tree)"
    )


def test_two_project_root_isolates_projects_from_each_other(
    two_project_root: pathlib.Path,
) -> None:
    """two_project_root fixture creates independent project directories.

    Writing to project_a's release state must not affect project_b's release
    state, demonstrating the per-project isolation that single-project fixtures
    cannot verify (TESTING.md failure mode 3).
    """
    project_a_state = two_project_root / "projects" / "project_a" / "release-state.md"
    project_b_state = two_project_root / "projects" / "project_b" / "release-state.md"

    assert project_a_state.exists(), "project_a must have release-state.md"
    assert project_b_state.exists(), "project_b must have release-state.md"

    # Mutate project_a's state.
    project_a_state.write_text(
        "# Release State\n\n## Active RC\nrc/v9.9.9\n",
        encoding="utf-8",
    )

    # project_b's state must be unchanged.
    b_content = project_b_state.read_text(encoding="utf-8")
    assert "rc/v9.9.9" not in b_content, (
        "Mutating project_a's release state must not affect project_b's state"
    )


# ---------------------------------------------------------------------------
# Shell harness smoke tests
# ---------------------------------------------------------------------------


def test_shell_harness_captures_stdout_and_exit_zero(
    tmp_path: pathlib.Path,
) -> None:
    """run_bash captures stdout and reports exit code 0 for a successful script."""
    result = run_bash(tmp_path, "echo 'hello from bash'")
    assert result.returncode == 0, (
        f"Simple echo script must exit 0; got {result.returncode}\nstderr: {result.stderr}"
    )
    assert "hello from bash" in result.stdout, (
        f"stdout must contain the echoed string; got: {result.stdout!r}"
    )


def test_shell_harness_captures_nonzero_exit_code(
    tmp_path: pathlib.Path,
) -> None:
    """run_bash captures the exit code from a failing script."""
    result = run_bash(tmp_path, "exit 42")
    assert result.returncode == 42, (
        f"exit 42 must produce returncode 42; got {result.returncode}"
    )


def test_shell_harness_captures_stderr(
    tmp_path: pathlib.Path,
) -> None:
    """run_bash captures output written to stderr separately from stdout."""
    result = run_bash(tmp_path, "echo 'to-stderr' >&2")
    assert result.returncode == 0
    assert "to-stderr" in result.stderr, (
        f"stderr must contain the message; got: {result.stderr!r}"
    )
    # stdout should be empty (the message went to stderr only)
    assert result.stdout.strip() == "", (
        f"stdout must be empty when output goes to stderr; got: {result.stdout!r}"
    )


def test_shell_harness_passes_extra_env_to_subprocess(
    tmp_path: pathlib.Path,
) -> None:
    """run_bash propagates extra_env variables into the subprocess environment."""
    result = run_bash(
        tmp_path,
        "echo $MY_TEST_VAR",
        extra_env={"MY_TEST_VAR": "scaffolding-check"},
    )
    assert result.returncode == 0
    assert "scaffolding-check" in result.stdout, (
        f"extra_env variable must be visible inside the subprocess; "
        f"got stdout: {result.stdout!r}"
    )


def test_shell_harness_writes_no_filesystem_artifacts_after_run(
    tmp_path: pathlib.Path,
) -> None:
    """run_bash does not leave any files in tmp_path itself.

    The harness creates no hidden temp files of its own — all temp I/O for
    the test is the caller's responsibility via tmp_path.  This confirms the
    helper is self-cleaning within the test's tmp_path scope.
    """
    contents_before = set(tmp_path.iterdir())
    run_bash(tmp_path, "echo 'no artifacts'")
    contents_after = set(tmp_path.iterdir())
    assert contents_after == contents_before, (
        f"run_bash must not create files in tmp_path; "
        f"new entries: {contents_after - contents_before}"
    )
