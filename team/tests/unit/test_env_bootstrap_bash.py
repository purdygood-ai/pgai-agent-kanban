"""
test_env_bootstrap_bash.py
==========================
Behavioral unit tests for team/scripts/lib/env_bootstrap.sh.

Tests exercise the four acceptance criteria for the source-then-fail-loud
prelude contract:

  1. Unset env + shell-env present → PGAI_AGENT_KANBAN_ROOT_PATH is set,
     absolutized, and no output is written to stdout.
  2. Unset env + shell-env absent → exits 1 with the exact missing-or-broken
     error message on stderr.
  3. Pre-set PGAI_AGENT_KANBAN_ROOT_PATH → prelude leaves the value unchanged;
     shell-env's value (if any) is not substituted.
  4. Sourcing the prelude twice → idempotent; no duplicate exports, no errors.

Each test builds a synthetic kanban root layout inside pytest's tmp_path and
writes a small "caller" shell script at the correct entry-point depth.  This
gives BASH_SOURCE[1] a real path so the walk-upward derivation can function.

The caller scripts mirror real entry-point patterns:
  - scripts/caller.sh          (top-level operator command, depth 1)
  - scripts/cm/caller.sh       (CM command, depth 2)
  - scripts/dashboard/caller.sh (dashboard pane, depth 2)

ENV HYGIENE
-----------
Tests use extra_env to pass only what the subprocess needs.  The parent
conftest's _block_live_kanban_writes fixture has already scrubbed
PGAI_AGENT_KANBAN_ROOT_PATH from the inherited env, so a test that does
NOT pass it in extra_env will have it unset in the subprocess.
"""

from __future__ import annotations

import pathlib
import stat

import pytest

from tests.unit.shell_harness import run_bash

# Path to the script under test, relative to the team/ directory where pytest runs.
_PRELUDE = pathlib.Path("scripts/lib/env_bootstrap.sh")


# ---------------------------------------------------------------------------
# Shared helper: build a synthetic kanban root layout
# ---------------------------------------------------------------------------


def _make_root(
    tmp_path: pathlib.Path,
    *,
    with_shell_env: bool = True,
    shell_env_exports_root: bool = True,
) -> pathlib.Path:
    """Build a minimal synthetic root directory for prelude tests.

    Layout created:
        tmp_path/
            shell-env          (optional)
            scripts/
                lib/
                    env_bootstrap.sh  → symlink or copy pointing to _PRELUDE
                cm/
                dashboard/

    Parameters
    ----------
    tmp_path:
        The pytest tmp_path fixture value.  All directories are created here.
    with_shell_env:
        When True (default), creates a shell-env file that exports
        PGAI_AGENT_KANBAN_ROOT_PATH to the candidate root value.
    shell_env_exports_root:
        When True (default), the shell-env file exports the canonical env var.
        Set to False to test a "broken" shell-env scenario.

    Returns
    -------
    pathlib.Path
        The synthetic root directory (tmp_path itself).
    """
    root = tmp_path
    lib_dir = root / "scripts" / "lib"
    lib_dir.mkdir(parents=True, exist_ok=True)
    (root / "scripts" / "cm").mkdir(parents=True, exist_ok=True)
    (root / "scripts" / "dashboard").mkdir(parents=True, exist_ok=True)

    # Copy the prelude script into the synthetic root's lib dir so the caller
    # can source it via a relative path from its own location.
    prelude_text = _PRELUDE.read_text(encoding="utf-8")
    prelude_dest = lib_dir / "env_bootstrap.sh"
    prelude_dest.write_text(prelude_text, encoding="utf-8")
    prelude_dest.chmod(stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)

    if with_shell_env:
        if shell_env_exports_root:
            # shell-env uses $_eb_candidate (the prelude's internal variable
            # that holds the derived root) to export the canonical env var.
            shell_env_text = 'export PGAI_AGENT_KANBAN_ROOT_PATH="${_eb_candidate}"\n'
        else:
            # A broken shell-env that does NOT set the env var.
            shell_env_text = "# shell-env present but does not export root\n"
        (root / "shell-env").write_text(shell_env_text, encoding="utf-8")

    return root


def _write_caller(
    root: pathlib.Path,
    *,
    subdir: str = "",
    extra_lines: str = "",
) -> pathlib.Path:
    """Write a minimal caller script at the correct entry-point depth.

    Parameters
    ----------
    root:
        The synthetic kanban root (tmp_path).
    subdir:
        Optional sub-directory under scripts/ (e.g. "cm", "dashboard").
        When empty, the caller is placed directly under scripts/.
    extra_lines:
        Optional bash lines appended after the source call (e.g. to echo
        the env var value).

    Returns
    -------
    pathlib.Path
        Absolute path to the created caller script.
    """
    script_dir = root / "scripts" / subdir if subdir else root / "scripts"

    # Path from caller's directory to env_bootstrap.sh in lib/.
    # From scripts/:           ./lib/env_bootstrap.sh
    # From scripts/cm/:        ../lib/env_bootstrap.sh
    # From scripts/dashboard/: ../lib/env_bootstrap.sh
    lib_rel = "lib/env_bootstrap.sh" if not subdir else "../lib/env_bootstrap.sh"

    caller_text = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'# shellcheck source={lib_rel}\n'
        f'source "$(dirname "${{BASH_SOURCE[0]}}")/{lib_rel}"\n'
    )
    if extra_lines:
        caller_text += extra_lines + "\n"

    caller_path = script_dir / "caller.sh"
    caller_path.write_text(caller_text, encoding="utf-8")
    caller_path.chmod(
        stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH
    )
    return caller_path


# ---------------------------------------------------------------------------
# Criterion 1: unset env + shell-env present → env set, absolutized, no stdout
# ---------------------------------------------------------------------------


def test_root_derived_and_exported_when_shell_env_present(
    tmp_path: pathlib.Path,
) -> None:
    """Prelude sets PGAI_AGENT_KANBAN_ROOT_PATH from shell-env when env is unset.

    Verifies: no stdout output; exit code 0; env var is set to the absolutized
    candidate root derived from the caller's location.
    """
    root = _make_root(tmp_path, with_shell_env=True)
    caller = _write_caller(root, extra_lines='echo "ROOT=${PGAI_AGENT_KANBAN_ROOT_PATH}"')

    result = run_bash(
        tmp_path,
        f"bash {caller!s}",
        extra_env={"PGAI_AGENT_KANBAN_ROOT_PATH": ""},
    )

    assert result.returncode == 0, f"unexpected failure:\n{result.stderr}"
    # stdout must contain the ROOT= line with the absolutized path
    assert "ROOT=" in result.stdout
    # The exported value must be the absolutized synthetic root
    exported_root = result.stdout.strip().removeprefix("ROOT=")
    assert pathlib.Path(exported_root).is_absolute(), (
        f"exported root is not absolute: {exported_root!r}"
    )
    # No stray output beyond the echo we added
    assert result.stdout.count("\n") <= 1, (
        f"unexpected extra stdout lines:\n{result.stdout!r}"
    )


def test_root_is_absolutized_on_success(tmp_path: pathlib.Path) -> None:
    """The exported root path is an absolutized (realpath) value.

    Verifies that the prelude calls realpath (or equivalent) before exporting
    so downstream scripts receive a fully-resolved path.
    """
    root = _make_root(tmp_path, with_shell_env=True)
    caller = _write_caller(root, extra_lines='echo "ROOT=${PGAI_AGENT_KANBAN_ROOT_PATH}"')

    result = run_bash(
        tmp_path,
        f"bash {caller!s}",
        extra_env={"PGAI_AGENT_KANBAN_ROOT_PATH": ""},
    )

    assert result.returncode == 0
    exported = result.stdout.strip().removeprefix("ROOT=")
    assert exported == str(pathlib.Path(exported).resolve()), (
        f"path is not absolutized: {exported!r}"
    )


def test_no_stdout_on_success(tmp_path: pathlib.Path) -> None:
    """Prelude produces zero bytes on stdout when it succeeds.

    The contract requires silent success: zero output on stdout.
    """
    root = _make_root(tmp_path, with_shell_env=True)
    # Caller does NOT echo anything — tests that the prelude itself is silent.
    caller = _write_caller(root)

    result = run_bash(
        tmp_path,
        f"bash {caller!s}",
        extra_env={"PGAI_AGENT_KANBAN_ROOT_PATH": ""},
    )

    assert result.returncode == 0
    assert result.stdout == "", (
        f"prelude emitted unexpected stdout:\n{result.stdout!r}"
    )


# ---------------------------------------------------------------------------
# Criterion 2: unset env + shell-env absent → exit 1 with error message
# ---------------------------------------------------------------------------


def test_exits_one_with_error_when_shell_env_absent(tmp_path: pathlib.Path) -> None:
    """Prelude exits 1 with the exact missing-or-broken message when shell-env is absent.

    Verifies the fail-loud contract: the error is written to stderr, the exit
    code is 1, and the message text matches the exact string specified in the
    task constraints.
    """
    root = _make_root(tmp_path, with_shell_env=False)
    caller = _write_caller(root, extra_lines='echo "should not reach here"')

    result = run_bash(
        tmp_path,
        f"bash {caller!s}",
        extra_env={"PGAI_AGENT_KANBAN_ROOT_PATH": ""},
    )

    assert result.returncode == 1, (
        f"expected exit 1, got {result.returncode};\nstdout: {result.stdout!r}\n"
        f"stderr: {result.stderr!r}"
    )
    expected_fragment = "PGAI_AGENT_KANBAN_ROOT_PATH not set — shell-env missing or broken at"
    assert expected_fragment in result.stderr, (
        f"error message missing expected fragment.\nExpected fragment: {expected_fragment!r}\n"
        f"Actual stderr: {result.stderr!r}"
    )
    # The message must name the candidate shell-env path
    assert "shell-env" in result.stderr
    assert "should not reach here" not in result.stdout, (
        "caller continued execution after prelude returned 1"
    )


def test_error_message_names_candidate_path(tmp_path: pathlib.Path) -> None:
    """The fail-loud message includes the full path to the candidate shell-env.

    Verifies the human-readable diagnostic: the operator must be able to see
    which shell-env file is missing or broken without re-reading the source.
    """
    root = _make_root(tmp_path, with_shell_env=False)
    caller = _write_caller(root)

    result = run_bash(
        tmp_path,
        f"bash {caller!s}",
        extra_env={"PGAI_AGENT_KANBAN_ROOT_PATH": ""},
    )

    assert result.returncode == 1
    # The candidate path should appear in the error message
    candidate_shell_env = str(root / "shell-env")
    assert candidate_shell_env in result.stderr, (
        f"candidate shell-env path not in error message.\n"
        f"Expected path: {candidate_shell_env!r}\n"
        f"Actual stderr: {result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Criterion 3: pre-set PGAI_AGENT_KANBAN_ROOT_PATH → prelude leaves it unchanged
# ---------------------------------------------------------------------------


def test_preset_env_wins_over_shell_env(tmp_path: pathlib.Path) -> None:
    """A pre-set PGAI_AGENT_KANBAN_ROOT_PATH is not overridden by shell-env.

    Verifies the operator-env-wins contract: when the caller's environment
    already has PGAI_AGENT_KANBAN_ROOT_PATH set (e.g. from the operator's
    shell or from --kanban-root processing), the prelude never replaces it
    with the shell-env value.
    """
    root = _make_root(tmp_path, with_shell_env=True)
    # shell-env would export the candidate root if given the chance
    caller = _write_caller(root, extra_lines='echo "ROOT=${PGAI_AGENT_KANBAN_ROOT_PATH}"')

    # Operator-set value: a path that does NOT match the candidate root
    operator_root = "/operator/custom/root"

    result = run_bash(
        tmp_path,
        f"bash {caller!s}",
        extra_env={"PGAI_AGENT_KANBAN_ROOT_PATH": operator_root},
    )

    assert result.returncode == 0, f"unexpected failure:\n{result.stderr}"
    exported_root = result.stdout.strip().removeprefix("ROOT=")
    # The prelude uses realpath -m (resolves without requiring the path to exist),
    # so /operator/custom/root → /operator/custom/root (already absolute, no symlinks).
    assert str(root) not in exported_root, (
        f"shell-env candidate root leaked into result.\n"
        f"Exported: {exported_root!r}\n"
        f"Root dir: {root!s}"
    )
    # The operator-specified path should be returned as-is (it is already absolute).
    assert exported_root == operator_root, (
        f"operator root was not preserved (absolutized with realpath -m): {exported_root!r}"
    )


def test_preset_env_unchanged_when_shell_env_absent(tmp_path: pathlib.Path) -> None:
    """Pre-set env var is kept even when shell-env is absent.

    The idempotency guard fires before any walk derivation, so a pre-set
    value means neither shell-env sourcing nor the fail-loud path is reached.
    """
    root = _make_root(tmp_path, with_shell_env=False)
    caller = _write_caller(root, extra_lines='echo "ROOT=${PGAI_AGENT_KANBAN_ROOT_PATH}"')

    operator_root = str(root)  # use the real root so realpath works
    result = run_bash(
        tmp_path,
        f"bash {caller!s}",
        extra_env={"PGAI_AGENT_KANBAN_ROOT_PATH": operator_root},
    )

    assert result.returncode == 0, (
        f"unexpected failure with pre-set env and absent shell-env:\n{result.stderr}"
    )
    exported = result.stdout.strip().removeprefix("ROOT=")
    assert exported == str(pathlib.Path(operator_root).resolve()), (
        f"pre-set value not preserved: {exported!r}"
    )


# ---------------------------------------------------------------------------
# Criterion 4: sourcing the prelude twice is a no-op
# ---------------------------------------------------------------------------


def test_double_source_is_idempotent(tmp_path: pathlib.Path) -> None:
    """Sourcing env_bootstrap.sh twice produces no errors and no extra output.

    Verifies the idempotency contract: the second source call sees the env
    var already set, skips all derivation, and returns 0 without side effects.
    """
    root = _make_root(tmp_path, with_shell_env=True)
    lib_rel = "lib/env_bootstrap.sh"

    double_source_lines = (
        f'source "$(dirname "${{BASH_SOURCE[0]}}")/{lib_rel}"\n'
        f'echo "ROOT_AFTER_SECOND_SOURCE=${{PGAI_AGENT_KANBAN_ROOT_PATH}}"\n'
    )
    caller = _write_caller(root, extra_lines=double_source_lines)

    result = run_bash(
        tmp_path,
        f"bash {caller!s}",
        extra_env={"PGAI_AGENT_KANBAN_ROOT_PATH": ""},
    )

    assert result.returncode == 0, f"double-source failed:\n{result.stderr}"
    assert result.stderr == "", (
        f"unexpected stderr on double-source:\n{result.stderr!r}"
    )
    assert "ROOT_AFTER_SECOND_SOURCE=" in result.stdout
    # Both reads of the env var should yield the same absolutized root
    lines = [ln for ln in result.stdout.splitlines() if ln.startswith("ROOT")]
    values = [ln.split("=", 1)[1] for ln in lines]
    assert len(set(values)) == 1, (
        f"env var value changed between first and second source:\n{values}"
    )


def test_double_source_no_extra_stdout(tmp_path: pathlib.Path) -> None:
    """Double-sourcing produces exactly the lines the caller explicitly echoes.

    The prelude itself must be silent (zero stdout output), so sourcing it
    twice must not produce extra stdout lines beyond what the caller writes.
    """
    root = _make_root(tmp_path, with_shell_env=True)
    lib_rel = "lib/env_bootstrap.sh"

    # Caller sources twice, echoes one sentinel line
    double_source_lines = (
        f'source "$(dirname "${{BASH_SOURCE[0]}}")/{lib_rel}"\n'
        'echo "SENTINEL"\n'
    )
    caller = _write_caller(root, extra_lines=double_source_lines)

    result = run_bash(
        tmp_path,
        f"bash {caller!s}",
        extra_env={"PGAI_AGENT_KANBAN_ROOT_PATH": ""},
    )

    assert result.returncode == 0
    # Exactly one stdout line from the first source (nothing) + the sentinel
    assert result.stdout.strip() == "SENTINEL", (
        f"unexpected stdout on double-source:\n{result.stdout!r}"
    )


# ---------------------------------------------------------------------------
# Criterion 1 extended: depth-2 entry points (scripts/cm/, scripts/dashboard/)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("subdir", ["cm", "dashboard"])
def test_root_derived_from_depth_two_entry_point(
    tmp_path: pathlib.Path,
    subdir: str,
) -> None:
    """Prelude correctly walks up two directory levels for cm/ and dashboard/ callers.

    Entry points under scripts/cm/ and scripts/dashboard/ are at depth 2
    below the kanban root.  The walk-upward algorithm must traverse both
    the sub-directory and the scripts/ layer to reach the root.
    """
    root = _make_root(tmp_path, with_shell_env=True)
    caller = _write_caller(
        root,
        subdir=subdir,
        extra_lines='echo "ROOT=${PGAI_AGENT_KANBAN_ROOT_PATH}"',
    )

    result = run_bash(
        tmp_path,
        f"bash {caller!s}",
        extra_env={"PGAI_AGENT_KANBAN_ROOT_PATH": ""},
    )

    assert result.returncode == 0, (
        f"depth-2 ({subdir}/) derivation failed:\n{result.stderr}"
    )
    exported_root = result.stdout.strip().removeprefix("ROOT=")
    assert pathlib.Path(exported_root).is_absolute()
    # The derived root must be the synthetic root, not scripts/ or scripts/<subdir>/
    assert exported_root == str(root.resolve()), (
        f"wrong root derived for {subdir}/ entry point.\n"
        f"Expected: {root.resolve()!s}\n"
        f"Got:      {exported_root!r}"
    )
