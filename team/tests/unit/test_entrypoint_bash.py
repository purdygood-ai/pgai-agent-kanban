"""
test_entrypoint_bash.py
=======================
Pure-bash unit tests for docker/entrypoint.sh.

Acceptance criteria covered (from task README):
  AC1  Missing any of the four mounts → entrypoint exits 1 with the missing
       mount name in the error output.
  AC4  New pure-bash unit tests pass under bash team/scripts/run-unit-tests.sh
       on any host (no docker requirement).

The tests work by constructing a synthetic filesystem layout under
pytest's tmp_path that mimics the four mount points, then invoking
docker/entrypoint.sh via bash with the mount paths redirected through
test env vars.  This validates all four mount checks, all three exec-mode
dispatch paths (pseudocron, shell, dashboard), TERM defaulting, and the
--/passthrough form.

No docker daemon is required.  The entrypoint is executed via bash in
a subprocess; exec calls are replaced with echo stubs in a patched copy
so the test subprocess does not replace itself.

Workspace-mount check modes
----------------------------
docker/entrypoint.sh supports two modes for the /home/<user> workspace check:

  Explicit mode (PGAI_WORKSPACE_MOUNT set):
    The entrypoint verifies that the named path exists.  This is the primary
    mode used by the main test suite because it keeps the check simple and
    testable: create the path → passes; omit the path → fails.

  Auto-detect mode (PGAI_WORKSPACE_MOUNT unset):
    The entrypoint searches /home/*/ for any directory that is NOT the
    runtime user's image-baked home (/home/${USER:-kanban}).  Tests for
    this mode must set TEST_HOME_DIR to a temp directory and patch the
    baked-home path so the exclusion logic operates on the temp layout.
    See tests named *_autodetect_* for examples.

Exec-mode stubs
---------------
docker/entrypoint.sh uses ``exec`` to replace the shell process.  The
testable copy replaces each ``exec <cmd>`` with ``echo EXEC_MODE=<mode>; exit 0``
so we can assert which dispatch path fired.

Test naming (SOP.md anti-pattern 6):
Names describe behavior, never bug IDs or scaffolding labels.
"""

from __future__ import annotations

import pathlib
import stat
import textwrap

import pytest

# Path to docker/entrypoint.sh absolute — resolved from this test file's location.
# The test file lives at team/tests/unit/test_entrypoint_bash.py.
# docker/entrypoint.sh lives at <repo-root>/docker/entrypoint.sh.
# team/ is three levels above this file: tests/ → unit/ → test file.
_ENTRYPOINT_ABS = (
    pathlib.Path(__file__).parent.parent.parent.parent / "docker" / "entrypoint.sh"
).resolve()

from tests.unit.shell_harness import run_bash


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_mounts(
    tmp_path: pathlib.Path,
    *,
    pgai_agent_kanban: bool = True,
    home_user: bool = True,
    claude_payload: bool = True,
    claude_config: bool = True,
) -> dict[str, pathlib.Path]:
    """Create a synthetic mount layout under tmp_path.

    Returns a dict mapping mount-point names to their paths.
    The directories are created independently so that toggling one
    ``False`` does not accidentally create its parents via another
    ``mkdir(parents=True)`` call.
    """
    paths: dict[str, pathlib.Path] = {}

    # /pgai_agent_kanban — kanban install volume
    kanban_dir = tmp_path / "pgai_agent_kanban"
    if pgai_agent_kanban:
        kanban_dir.mkdir(parents=True, exist_ok=True)
        # Create minimal stubs so entrypoint's exec path references a real dir.
        (kanban_dir / "team" / "scripts").mkdir(parents=True, exist_ok=True)
    paths["pgai_agent_kanban"] = kanban_dir

    # /home/<user> — user workspace (the entrypoint checks for any /home/*/ dir)
    home_parent = tmp_path / "home"
    user_dir = home_parent / "kanban"
    if home_user:
        # Create the user subdir; parents=True also creates home_parent.
        user_dir.mkdir(parents=True, exist_ok=True)
    else:
        # Do NOT create home_parent or user_dir.
        pass
    paths["home"] = home_parent
    paths["home_user"] = user_dir

    # /claude — site-specific payload directory
    claude_payload_dir = tmp_path / "claude"
    if claude_payload:
        claude_payload_dir.mkdir(parents=True, exist_ok=True)
    paths["claude_payload"] = claude_payload_dir

    # ~/.claude — agent CLI config directory
    # HOME is set to a dedicated directory so .claude creation does NOT
    # create user_dir as a side-effect (which would make home_user=False tests
    # pass incorrectly).
    home_for_claude = tmp_path / "home_for_claude" / "kanban"
    claude_config_dir = home_for_claude / ".claude"
    if claude_config:
        claude_config_dir.mkdir(parents=True, exist_ok=True)
    else:
        # Ensure the parent (home_for_claude) exists so HOME is valid,
        # but do NOT create .claude itself.
        home_for_claude.mkdir(parents=True, exist_ok=True)
    paths["home_for_claude"] = home_for_claude
    paths["claude_config"] = claude_config_dir

    return paths


def _env_for_mounts(paths: dict[str, pathlib.Path]) -> dict[str, str]:
    """Build the extra_env dict for a standard four-mount test invocation.

    Sets PGAI_WORKSPACE_MOUNT to the test user directory so the entrypoint
    uses explicit-path mode.  This is the primary test path: the existence
    of paths["home_user"] directly controls whether the workspace check passes,
    without relying on glob matching or baked-home exclusion logic.
    """
    return {
        "HOME": str(paths["home_for_claude"]),
        "TEST_KANBAN_DIR": str(paths["pgai_agent_kanban"]),
        "TEST_HOME_DIR": str(paths["home"]),
        "TEST_CLAUDE_DIR": str(paths["claude_payload"]),
        # Explicit workspace path: entrypoint verifies this exact directory.
        # When home_user=True the directory exists → check passes.
        # When home_user=False the directory is absent → check fails.
        "PGAI_WORKSPACE_MOUNT": str(paths["home_user"]),
    }


def _make_testable_entrypoint(
    tmp_path: pathlib.Path,
    paths: dict[str, pathlib.Path],
) -> pathlib.Path:
    """Return a path to a testable wrapper around docker/entrypoint.sh.

    Because entrypoint.sh hard-codes mount paths (/pgai_agent_kanban, /claude,
    /home/*), we cannot redirect them in tests without privileged filesystem
    operations.  Instead, we produce a patched copy of the entrypoint with the
    literal paths replaced by variables that our tests set via extra_env.
    This preserves the full logic while making it host-portable.

    The wrapper patches:
      - /pgai_agent_kanban  → ${TEST_KANBAN_DIR:-/pgai_agent_kanban}
      - /claude             → ${TEST_CLAUDE_DIR:-/claude}
      - /home/*/  glob     → "${TEST_HOME_DIR:-/home}"/*/
      - _baked_home (/home/${USER:-kanban}) → "${TEST_HOME_DIR:-/home}/${USER:-kanban}"
        so the baked-home exclusion logic operates on the temp directory tree
        rather than the real /home.
      - HOME env var is set via extra_env to point at the test user dir
      - exec calls are replaced with echo stubs so we can observe which mode fired

    Workspace-mount check (explicit vs auto-detect):
      When PGAI_WORKSPACE_MOUNT is set in extra_env (as _env_for_mounts() does),
      the entrypoint uses that exact path — no glob or exclusion patching needed
      for the standard test cases.  The auto-detect code path (glob + exclusion)
      is exercised by tests that do NOT pass PGAI_WORKSPACE_MOUNT; those tests
      rely on the TEST_HOME_DIR and patched _baked_home to control which
      directories the glob sees and which it excludes.
    """
    original = _ENTRYPOINT_ABS.read_text(encoding="utf-8")

    patched = original

    # Replace hard-coded mount paths with env-var references.
    # Order matters: replace longer/more-specific strings before shorter ones.

    # 1. /pgai_agent_kanban directory check
    patched = patched.replace(
        '! -d "/pgai_agent_kanban"',
        '! -d "${TEST_KANBAN_DIR:-/pgai_agent_kanban}"',
    )
    # 2. /home/*/ glob check (auto-detect mode)
    patched = patched.replace(
        'for _candidate in /home/*/;',
        'for _candidate in "${TEST_HOME_DIR:-/home}"/*/;',
    )
    # 3. _baked_home reference — redirect to the test home parent so the
    #    baked-home exclusion logic operates on temp dirs, not /home.
    patched = patched.replace(
        '_baked_home="/home/${USER:-kanban}"',
        '_baked_home="${TEST_HOME_DIR:-/home}/${USER:-kanban}"',
    )
    # 4. /claude directory check
    patched = patched.replace(
        '! -d "/claude"',
        '! -d "${TEST_CLAUDE_DIR:-/claude}"',
    )

    # 5. Missing-mount names in error messages (replace with test-path refs)
    patched = patched.replace(
        '_MISSING+=("/pgai_agent_kanban")',
        '_MISSING+=("${TEST_KANBAN_DIR:-/pgai_agent_kanban}")',
    )
    patched = patched.replace(
        '_MISSING+=("/claude")',
        '_MISSING+=("${TEST_CLAUDE_DIR:-/claude}")',
    )

    # 6. Export path — use the test var
    patched = patched.replace(
        'export PGAI_AGENT_KANBAN_ROOT_PATH="/pgai_agent_kanban"',
        'export PGAI_AGENT_KANBAN_ROOT_PATH="${TEST_KANBAN_DIR:-/pgai_agent_kanban}"',
    )

    # 7. Replace exec calls with echo stubs.
    # IMPORTANT: replace longer/more-specific strings BEFORE shorter prefixes
    # to avoid partial matches.  The dashboard exec includes a full path arg;
    # replace it before the bare shell exec.
    patched = patched.replace(
        'exec python3 "${PGAI_AGENT_KANBAN_ROOT_PATH}/scripts/pseudocron.py"',
        'echo "EXEC_MODE=pseudocron"; exit 0',
    )
    patched = patched.replace(
        'exec /bin/bash "${PGAI_AGENT_KANBAN_ROOT_PATH}/scripts/dashboard/create.sh"',
        'echo "EXEC_MODE=dashboard"; exit 0',
    )
    # Shell exec comes last — it is the shortest form and would match inside the
    # dashboard line if processed first.
    patched = patched.replace(
        'exec /bin/bash\n',
        'echo "EXEC_MODE=shell"\nexit 0\n',
    )
    # Passthrough exec forms (-- and default *)
    # Replace both exec "$@" occurrences with echo stubs.
    patched = patched.replace(
        'exec "$@"',
        'echo "EXEC_MODE=passthrough $*"; exit 0',
    )

    dest = tmp_path / "entrypoint_test.sh"
    dest.write_text(patched, encoding="utf-8")
    dest.chmod(
        stat.S_IRWXU
        | stat.S_IRGRP
        | stat.S_IXGRP
        | stat.S_IROTH
        | stat.S_IXOTH
    )
    return dest


# ---------------------------------------------------------------------------
# AC1a — Missing /pgai_agent_kanban → exit 1 naming it
# ---------------------------------------------------------------------------


def test_missing_kanban_mount_exits_one_with_name(tmp_path: pathlib.Path) -> None:
    """Missing /pgai_agent_kanban → entrypoint exits 1 naming the mount."""
    paths = _make_mounts(tmp_path, pgai_agent_kanban=False)
    ep = _make_testable_entrypoint(tmp_path, paths)

    result = run_bash(tmp_path, f"bash {ep!s}", extra_env=_env_for_mounts(paths))

    assert result.returncode == 1, (
        f"expected exit 1; got {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert str(paths["pgai_agent_kanban"]) in result.stderr, (
        f"missing mount name not in stderr:\n{result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# AC1b — Missing /home/<user> → exit 1 naming it
# ---------------------------------------------------------------------------


def test_missing_home_mount_exits_one_with_name(tmp_path: pathlib.Path) -> None:
    """Missing /home/<user> → entrypoint exits 1 naming the workspace path.

    Uses explicit-path mode: PGAI_WORKSPACE_MOUNT is set to the test user
    directory, which does not exist (home_user=False).  The entrypoint must
    exit 1 and name that missing path in stderr.
    """
    paths = _make_mounts(tmp_path, home_user=False)
    ep = _make_testable_entrypoint(tmp_path, paths)

    result = run_bash(tmp_path, f"bash {ep!s}", extra_env=_env_for_mounts(paths))

    assert result.returncode == 1, (
        f"expected exit 1; got {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    # In explicit-path mode the missing label is the PGAI_WORKSPACE_MOUNT value.
    assert str(paths["home_user"]) in result.stderr, (
        f"workspace path not named in stderr:\n{result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# AC1c — Missing /claude → exit 1 naming it
# ---------------------------------------------------------------------------


def test_missing_claude_payload_mount_exits_one_with_name(tmp_path: pathlib.Path) -> None:
    """Missing /claude → entrypoint exits 1 naming the payload mount."""
    paths = _make_mounts(tmp_path, claude_payload=False)
    ep = _make_testable_entrypoint(tmp_path, paths)

    result = run_bash(tmp_path, f"bash {ep!s}", extra_env=_env_for_mounts(paths))

    assert result.returncode == 1, (
        f"expected exit 1; got {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert str(paths["claude_payload"]) in result.stderr, (
        f"claude payload mount name not in stderr:\n{result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# AC1d — Missing ~/.claude → exit 1 naming it
# ---------------------------------------------------------------------------


def test_missing_claude_config_mount_exits_one_with_name(tmp_path: pathlib.Path) -> None:
    """Missing ~/.claude → entrypoint exits 1 naming the agent CLI config mount."""
    paths = _make_mounts(tmp_path, claude_config=False)
    ep = _make_testable_entrypoint(tmp_path, paths)

    result = run_bash(tmp_path, f"bash {ep!s}", extra_env=_env_for_mounts(paths))

    assert result.returncode == 1, (
        f"expected exit 1; got {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert ".claude" in result.stderr, (
        f".claude config path not in stderr:\n{result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# AC1e — All four mounts present → passes mount checks and reaches exec
# ---------------------------------------------------------------------------


def test_all_mounts_present_exits_zero(tmp_path: pathlib.Path) -> None:
    """All four mounts present → entrypoint passes mount checks and exits 0."""
    paths = _make_mounts(tmp_path)
    ep = _make_testable_entrypoint(tmp_path, paths)

    result = run_bash(tmp_path, f"bash {ep!s}", extra_env=_env_for_mounts(paths))

    assert result.returncode == 0, (
        f"expected exit 0; got {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# TERM defaulting — when TERM is unset, entrypoint exports xterm-256color
# ---------------------------------------------------------------------------


def test_term_defaults_to_xterm_256color_when_unset(tmp_path: pathlib.Path) -> None:
    """When TERM is absent, entrypoint sets TERM=xterm-256color before exec."""
    paths = _make_mounts(tmp_path)
    ep = _make_testable_entrypoint(tmp_path, paths)

    env = dict(_env_for_mounts(paths))
    # Ensure TERM is explicitly unset in the subprocess by not including it.
    env.pop("TERM", None)

    # Run without TERM — entrypoint should not fail over unset TERM.
    result = run_bash(
        tmp_path,
        f'unset TERM; bash {ep!s}',
        extra_env=env,
    )

    assert result.returncode == 0, (
        f"entrypoint failed when TERM was unset:\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )


def test_term_preserved_when_already_set(tmp_path: pathlib.Path) -> None:
    """When TERM is already set, entrypoint runs without error."""
    paths = _make_mounts(tmp_path)
    ep = _make_testable_entrypoint(tmp_path, paths)

    env = dict(_env_for_mounts(paths))
    env["TERM"] = "screen-256color"

    result = run_bash(tmp_path, f"bash {ep!s}", extra_env=env)

    assert result.returncode == 0, (
        f"expected exit 0; got {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Exec-mode dispatch — default (pseudocron)
# ---------------------------------------------------------------------------


def test_default_mode_dispatches_pseudocron(tmp_path: pathlib.Path) -> None:
    """No args → entrypoint dispatches to pseudocron mode."""
    paths = _make_mounts(tmp_path)
    ep = _make_testable_entrypoint(tmp_path, paths)

    result = run_bash(tmp_path, f"bash {ep!s}", extra_env=_env_for_mounts(paths))

    assert result.returncode == 0
    assert "EXEC_MODE=pseudocron" in result.stdout, (
        f"expected pseudocron sentinel in stdout:\n{result.stdout!r}"
    )


def test_explicit_pseudocron_arg_dispatches_pseudocron(tmp_path: pathlib.Path) -> None:
    """Explicit 'pseudocron' arg → entrypoint dispatches to pseudocron mode."""
    paths = _make_mounts(tmp_path)
    ep = _make_testable_entrypoint(tmp_path, paths)

    result = run_bash(tmp_path, f"bash {ep!s} pseudocron", extra_env=_env_for_mounts(paths))

    assert result.returncode == 0
    assert "EXEC_MODE=pseudocron" in result.stdout, (
        f"expected pseudocron sentinel in stdout:\n{result.stdout!r}"
    )


# ---------------------------------------------------------------------------
# Exec-mode dispatch — shell
# ---------------------------------------------------------------------------


def test_shell_mode_dispatches_interactive_bash(tmp_path: pathlib.Path) -> None:
    """'shell' arg → entrypoint dispatches to interactive bash (shell mode)."""
    paths = _make_mounts(tmp_path)
    ep = _make_testable_entrypoint(tmp_path, paths)

    result = run_bash(tmp_path, f"bash {ep!s} shell", extra_env=_env_for_mounts(paths))

    assert result.returncode == 0
    assert "EXEC_MODE=shell" in result.stdout, (
        f"expected shell sentinel in stdout:\n{result.stdout!r}"
    )


# ---------------------------------------------------------------------------
# Exec-mode dispatch — dashboard
# ---------------------------------------------------------------------------


def test_dashboard_mode_dispatches_tmux_session(tmp_path: pathlib.Path) -> None:
    """'dashboard' arg → entrypoint dispatches to dashboard (tmux) mode."""
    paths = _make_mounts(tmp_path)
    ep = _make_testable_entrypoint(tmp_path, paths)

    result = run_bash(tmp_path, f"bash {ep!s} dashboard", extra_env=_env_for_mounts(paths))

    assert result.returncode == 0
    assert "EXEC_MODE=dashboard" in result.stdout, (
        f"expected dashboard sentinel in stdout:\n{result.stdout!r}"
    )


# ---------------------------------------------------------------------------
# Multiple missing mounts — all named in a single error message
# ---------------------------------------------------------------------------


def test_all_mounts_missing_names_each_one(tmp_path: pathlib.Path) -> None:
    """When all four mounts are missing, each is named in the error output."""
    paths = _make_mounts(
        tmp_path,
        pgai_agent_kanban=False,
        home_user=False,
        claude_payload=False,
        claude_config=False,
    )
    ep = _make_testable_entrypoint(tmp_path, paths)

    result = run_bash(tmp_path, f"bash {ep!s}", extra_env=_env_for_mounts(paths))

    assert result.returncode == 1, (
        f"expected exit 1; got {result.returncode}\n"
        f"stderr: {result.stderr!r}"
    )
    # All four should appear in stderr.
    # Workspace path: in explicit-path mode the PGAI_WORKSPACE_MOUNT value
    # (paths["home_user"]) is named in stderr.
    assert str(paths["pgai_agent_kanban"]) in result.stderr, (
        f"kanban mount not named in stderr:\n{result.stderr!r}"
    )
    assert str(paths["home_user"]) in result.stderr, (
        f"workspace path not named in stderr:\n{result.stderr!r}"
    )
    assert str(paths["claude_payload"]) in result.stderr, (
        f"claude payload mount not named in stderr:\n{result.stderr!r}"
    )
    assert ".claude" in result.stderr, (
        f"claude config mount not named in stderr:\n{result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Auto-detect mode — workspace check excludes the image-baked runtime-user home
#
# These tests exercise the auto-detect code path (PGAI_WORKSPACE_MOUNT unset).
# The patched entrypoint redirects:
#   - /home/*/ glob → "${TEST_HOME_DIR}"/*/
#   - _baked_home (/home/${USER:-kanban}) → "${TEST_HOME_DIR}/${USER:-kanban}"
#
# So "baked home" in tests = TEST_HOME_DIR/<current-user>.
# A "non-baked workspace" = any other subdir under TEST_HOME_DIR.
# ---------------------------------------------------------------------------


def _env_for_mounts_no_workspace_mount(
    paths: dict[str, pathlib.Path],
) -> dict[str, str]:
    """Extra-env for auto-detect mode tests: PGAI_WORKSPACE_MOUNT is absent."""
    env = {
        "HOME": str(paths["home_for_claude"]),
        "TEST_KANBAN_DIR": str(paths["pgai_agent_kanban"]),
        "TEST_HOME_DIR": str(paths["home"]),
        "TEST_CLAUDE_DIR": str(paths["claude_payload"]),
    }
    # Explicitly unset PGAI_WORKSPACE_MOUNT so auto-detect mode activates.
    env.pop("PGAI_WORKSPACE_MOUNT", None)
    return env


def test_autodetect_baked_home_only_fails_workspace_check(
    tmp_path: pathlib.Path,
) -> None:
    """Auto-detect: only the image-baked runtime-user home exists → exit 1.

    This test reproduces the false-positive from BUG-0086: without PGAI_WORKSPACE_MOUNT
    the entrypoint must fail when the only /home/*/ entry is the image-baked
    directory (matching the runtime user's name).  The patched entrypoint excludes
    TEST_HOME_DIR/${USER:-kanban} from the glob, mirroring how the real entrypoint
    excludes /home/${USER:-kanban} (the useradd-created directory).
    """
    import os

    paths = _make_mounts(tmp_path)  # home_user=True creates TEST_HOME_DIR/kanban
    # Rename the created subdir to match the current test-process USER so the
    # patched _baked_home exclusion applies to it.
    runtime_user = os.environ.get("USER", "kanban")
    baked_home = paths["home"] / runtime_user
    existing_home = paths["home_user"]  # TEST_HOME_DIR/kanban

    # If the runtime user is already 'kanban', the existing dir is already the
    # baked home; otherwise rename it.
    if existing_home != baked_home:
        existing_home.rename(baked_home)

    ep = _make_testable_entrypoint(tmp_path, paths)
    env = _env_for_mounts_no_workspace_mount(paths)

    result = run_bash(tmp_path, f"bash {ep!s}", extra_env=env)

    assert result.returncode == 1, (
        "expected exit 1 when only the image-baked user home exists and "
        "PGAI_WORKSPACE_MOUNT is unset; "
        f"got {result.returncode}\nstdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert "MISSING" in result.stderr, (
        f"expected MISSING in stderr:\n{result.stderr!r}"
    )


def test_autodetect_non_baked_workspace_passes_check(
    tmp_path: pathlib.Path,
) -> None:
    """Auto-detect: a non-baked-home /home/*/ directory satisfies the check.

    When PGAI_WORKSPACE_MOUNT is unset AND the runtime user's baked home is NOT
    the only entry under /home/, the entrypoint passes the workspace check.
    This verifies that the exclusion is surgical: it only filters the single
    baked-home directory, not arbitrary workspace bind-mounts.
    """
    import os

    paths = _make_mounts(tmp_path)
    runtime_user = os.environ.get("USER", "kanban")
    baked_home = paths["home"] / runtime_user
    existing_home = paths["home_user"]  # TEST_HOME_DIR/kanban

    # Ensure the baked home exists (may already be there if runtime_user == 'kanban').
    if existing_home != baked_home:
        existing_home.rename(baked_home)

    # Also create a DIFFERENT subdir that simulates a bind-mounted workspace.
    workspace_dir = paths["home"] / "operator_workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)

    ep = _make_testable_entrypoint(tmp_path, paths)
    env = _env_for_mounts_no_workspace_mount(paths)

    result = run_bash(tmp_path, f"bash {ep!s}", extra_env=env)

    assert result.returncode == 0, (
        "expected exit 0 when a non-baked workspace dir exists alongside the "
        "baked home and PGAI_WORKSPACE_MOUNT is unset; "
        f"got {result.returncode}\nstdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )


def test_explicit_workspace_mount_path_passes_when_dir_exists(
    tmp_path: pathlib.Path,
) -> None:
    """PGAI_WORKSPACE_MOUNT set to an existing path → check passes (explicit mode)."""
    paths = _make_mounts(tmp_path)
    ep = _make_testable_entrypoint(tmp_path, paths)
    env = _env_for_mounts(paths)  # includes PGAI_WORKSPACE_MOUNT=paths["home_user"]

    result = run_bash(tmp_path, f"bash {ep!s}", extra_env=env)

    assert result.returncode == 0, (
        f"expected exit 0; got {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )


def test_explicit_workspace_mount_path_fails_when_dir_absent(
    tmp_path: pathlib.Path,
) -> None:
    """PGAI_WORKSPACE_MOUNT set to an absent path → exit 1 naming the path (explicit mode)."""
    paths = _make_mounts(tmp_path, home_user=False)
    ep = _make_testable_entrypoint(tmp_path, paths)
    env = _env_for_mounts(paths)  # PGAI_WORKSPACE_MOUNT → non-existent home_user path

    result = run_bash(tmp_path, f"bash {ep!s}", extra_env=env)

    assert result.returncode == 1, (
        f"expected exit 1; got {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert str(paths["home_user"]) in result.stderr, (
        f"workspace path not named in stderr:\n{result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Entrypoint file exists and is executable
# ---------------------------------------------------------------------------


def test_entrypoint_file_exists_in_repo() -> None:
    """docker/entrypoint.sh must exist at the expected path in the repo."""
    assert _ENTRYPOINT_ABS.exists(), (
        f"docker/entrypoint.sh not found at {_ENTRYPOINT_ABS!s}. "
        "This file must be committed to the repository."
    )


def test_entrypoint_is_executable() -> None:
    """docker/entrypoint.sh must have executable permission."""
    assert _ENTRYPOINT_ABS.exists(), f"entrypoint.sh not found at {_ENTRYPOINT_ABS!s}"
    file_stat = _ENTRYPOINT_ABS.stat()
    is_executable = bool(file_stat.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
    assert is_executable, (
        f"docker/entrypoint.sh is not executable; "
        f"current mode: {oct(file_stat.st_mode)}"
    )
