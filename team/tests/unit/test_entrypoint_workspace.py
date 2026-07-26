"""
test_entrypoint_workspace.py
=============================
Pure-bash unit tests for docker/entrypoint.sh under the workspace model.

The entrypoint has exactly two responsibilities: verify that
$PGAI_AGENT_KANBAN_ROOT_PATH names an existing directory (fail loud, naming
the path, if not), then `cd` into it and `exec bash -l`. There is no mount
enforcement and no startup-mode dispatch — a container is a workspace, not a
supervised scheduler.

These tests invoke docker/entrypoint.sh directly via bash in a subprocess.
No docker daemon is required: the script only touches the filesystem and
environment, so its full logic (including the final `exec bash -l`) can be
exercised on any host by feeding the spawned login shell commands on stdin
and reading them back from stdout.

Test naming describes behavior, never bug IDs or scaffolding labels.
"""

from __future__ import annotations

import pathlib
import stat

from tests.unit.shell_harness import run_bash

# Path to docker/entrypoint.sh, resolved from this test file's location.
# team/tests/unit/test_entrypoint_workspace.py -> repo root is three levels up.
_ENTRYPOINT_ABS = (
    pathlib.Path(__file__).parent.parent.parent.parent / "docker" / "entrypoint.sh"
).resolve()


def _isolated_home(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return a fresh, empty directory to use as HOME for exec'd login shells.

    `bash -l` sources /etc/profile and the user's own login dotfiles
    (~/.bash_profile, ~/.bashrc, ...). Tests that reach the final `exec
    bash -l` must never inherit the real operator HOME — an empty tmp
    directory guarantees no dotfile sourcing happens, keeping stdout limited
    to exactly what the test itself asked the shell to print and keeping the
    exec'd shell fully isolated from the operator's own shell configuration.
    """
    home = tmp_path / "isolated_home"
    home.mkdir()
    return home


# ---------------------------------------------------------------------------
# File sanity
# ---------------------------------------------------------------------------


def test_entrypoint_file_exists_in_repo() -> None:
    """docker/entrypoint.sh must exist at the expected path in the repo."""
    assert _ENTRYPOINT_ABS.exists(), (
        f"docker/entrypoint.sh not found at {_ENTRYPOINT_ABS!s}. "
        "This file must be committed to the repository."
    )


def test_entrypoint_is_executable() -> None:
    """docker/entrypoint.sh must have executable permission."""
    file_stat = _ENTRYPOINT_ABS.stat()
    is_executable = bool(file_stat.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
    assert is_executable, (
        f"docker/entrypoint.sh is not executable; current mode: {oct(file_stat.st_mode)}"
    )


# ---------------------------------------------------------------------------
# Fail-loud on missing root
# ---------------------------------------------------------------------------


def test_unset_root_path_exits_one_naming_the_variable(tmp_path: pathlib.Path) -> None:
    """PGAI_AGENT_KANBAN_ROOT_PATH unset -> exit 1, error names the variable.

    The env var is unset inside the bash script text itself so the test is
    immune to whatever the parent test process happens to have exported.
    """
    result = run_bash(
        tmp_path,
        f'unset PGAI_AGENT_KANBAN_ROOT_PATH; bash {_ENTRYPOINT_ABS!s} < /dev/null',
    )

    assert result.returncode == 1, (
        f"expected exit 1; got {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert "PGAI_AGENT_KANBAN_ROOT_PATH" in result.stderr, (
        f"variable name not named in stderr:\n{result.stderr!r}"
    )


def test_root_path_set_to_nonexistent_directory_exits_one_naming_it(
    tmp_path: pathlib.Path,
) -> None:
    """PGAI_AGENT_KANBAN_ROOT_PATH pointing at an absent directory -> exit 1, path named."""
    missing_root = tmp_path / "does-not-exist"

    result = run_bash(
        tmp_path,
        f"bash {_ENTRYPOINT_ABS!s} < /dev/null",
        extra_env={"PGAI_AGENT_KANBAN_ROOT_PATH": str(missing_root)},
    )

    assert result.returncode == 1, (
        f"expected exit 1; got {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert str(missing_root) in result.stderr, (
        f"missing root path not named in stderr:\n{result.stderr!r}"
    )


def test_root_path_set_to_a_file_not_a_directory_exits_one(tmp_path: pathlib.Path) -> None:
    """PGAI_AGENT_KANBAN_ROOT_PATH pointing at a plain file (not a directory) -> exit 1."""
    not_a_dir = tmp_path / "root-is-a-file"
    not_a_dir.write_text("not a directory\n", encoding="utf-8")

    result = run_bash(
        tmp_path,
        f"bash {_ENTRYPOINT_ABS!s} < /dev/null",
        extra_env={"PGAI_AGENT_KANBAN_ROOT_PATH": str(not_a_dir)},
    )

    assert result.returncode == 1, (
        f"expected exit 1; got {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert str(not_a_dir) in result.stderr, (
        f"root path not named in stderr:\n{result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# cd + exec bash -l behavior
# ---------------------------------------------------------------------------


def test_valid_root_lands_the_shell_in_that_directory(tmp_path: pathlib.Path) -> None:
    """A valid root -> the exec'd shell's cwd is that root directory.

    Feeds `pwd` on stdin to the spawned login shell (bash -l reads commands
    from stdin when stdin is not a terminal) and asserts the printed cwd
    matches the target directory exactly.
    """
    root = tmp_path / "kanban-root"
    root.mkdir()

    result = run_bash(
        tmp_path,
        f"bash {_ENTRYPOINT_ABS!s}",
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "HOME": str(_isolated_home(tmp_path)),
        },
        stdin="pwd\n",
    )

    assert result.returncode == 0, (
        f"expected exit 0; got {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    printed_cwd = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    assert printed_cwd == str(root), (
        f"shell did not land in the kanban root; expected {str(root)!r}, "
        f"got {printed_cwd!r}\nfull stdout: {result.stdout!r}"
    )


def test_valid_root_execs_a_login_shell(tmp_path: pathlib.Path) -> None:
    """The exec'd shell is a login shell (bash -l), not a plain interactive shell.

    `shopt login_shell` only reports on when bash was invoked with -l (or as
    a login shell via argv[0]). Confirms the entrypoint's contract literally:
    `exec bash -l`, not `exec bash`.
    """
    root = tmp_path / "kanban-root"
    root.mkdir()

    result = run_bash(
        tmp_path,
        f"bash {_ENTRYPOINT_ABS!s}",
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "HOME": str(_isolated_home(tmp_path)),
        },
        stdin='shopt -q login_shell && echo IS_LOGIN_SHELL || echo NOT_LOGIN_SHELL\n',
    )

    assert result.returncode == 0, (
        f"expected exit 0; got {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert "IS_LOGIN_SHELL" in result.stdout, (
        f"exec'd shell is not a login shell:\nstdout: {result.stdout!r}"
    )


def test_valid_root_replaces_the_process_not_a_subshell(tmp_path: pathlib.Path) -> None:
    """exec replaces the entrypoint process; the shell inherits entrypoint's PID.

    Confirms `exec bash -l` (process replacement) rather than a forked child
    shell. $$ inside the login shell must equal the PID the parent bash
    process reported for itself before the exec.
    """
    root = tmp_path / "kanban-root"
    root.mkdir()

    result = run_bash(
        tmp_path,
        f'echo "PARENT_PID=$$"; exec bash {_ENTRYPOINT_ABS!s}',
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "HOME": str(_isolated_home(tmp_path)),
        },
        stdin='echo "CHILD_PID=$$"\n',
    )

    assert result.returncode == 0, (
        f"expected exit 0; got {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    parent_pid = next((l for l in lines if l.startswith("PARENT_PID=")), None)
    child_pid = next((l for l in lines if l.startswith("CHILD_PID=")), None)
    assert parent_pid is not None and child_pid is not None, (
        f"expected PARENT_PID and CHILD_PID markers in stdout:\n{result.stdout!r}"
    )
    assert parent_pid.split("=", 1)[1] == child_pid.split("=", 1)[1], (
        "exec did not replace the process — the login shell has a different "
        f"PID than the shell that called entrypoint.sh: {parent_pid!r} vs {child_pid!r}"
    )
