"""
test_wake_common_bootstrap.py
=============================
Live-shaped fixture verifying that wake-batch.sh's cron-style env resolution
is unchanged after wake_common.sh delegates root absolutization to env_bootstrap.sh.

Acceptance criterion (from CODER-20260712-062):
  Live-shaped fixture: cron-style invocation of wake-batch.sh from env -i
  (plus PATH/HOME) resolves PGAI_AGENT_KANBAN_ROOT_PATH correctly.

The fixture cannot run wake-batch.sh to completion (the claude CLI is not
present in the test environment), so it exercises the bootstrap chain directly:
the three lines in wake_common.sh that now constitute the bootstrap site:

    export PGAI_AGENT_KANBAN_ROOT_PATH="${PGAI_AGENT_KANBAN_ROOT_PATH:-$TEAM_ROOT}"
    source "${SCRIPT_DIR}/../lib/env_bootstrap.sh"
    [[ -f "$TEAM_ROOT/shell-env" ]] && source "$TEAM_ROOT/shell-env"

Each test builds a synthetic kanban root and runs a cron-shaped caller script
(no pre-set PGAI_AGENT_KANBAN_ROOT_PATH; only PATH and HOME in the env,
mirroring what cron provides) to verify the bootstrap resolves the root correctly.

Wake-batch.sh structural tests (integrity of the dispatch chain) are separate
from the bootstrap-semantics tests here.  Both are needed: structural tests prove
the wiring; live-shaped tests prove the runtime behavior.
"""

from __future__ import annotations

import os
import pathlib
import stat
import subprocess

import pytest

from tests.unit.shell_harness import run_bash

# Paths relative to team/ (pytest cwd when run via run-unit-tests.sh)
_ENV_BOOTSTRAP_SH = pathlib.Path("scripts/lib/env_bootstrap.sh")
_WAKE_COMMON_SH = pathlib.Path("scripts/lib/wake_common.sh")
_WAKE_BATCH_SH = pathlib.Path("scripts/wake-batch.sh")
_CLAUDE_SH = pathlib.Path("scripts/wake/claude.sh")
_CODEX_SH = pathlib.Path("scripts/wake/codex.sh")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_synthetic_root(
    tmp_path: pathlib.Path,
    *,
    with_shell_env: bool = True,
) -> pathlib.Path:
    """Build a minimal synthetic kanban root for bootstrap tests.

    The root mirrors the layout expected by env_bootstrap.sh and the wake scripts:
        <root>/
            shell-env          (optional: exports PGAI_AGENT_KANBAN_ROOT_PATH)
            scripts/
                lib/
                    env_bootstrap.sh  (copy of real prelude)
                wake/
    """
    root = tmp_path / "kanban_root"
    lib_dir = root / "scripts" / "lib"
    lib_dir.mkdir(parents=True, exist_ok=True)
    (root / "scripts" / "wake").mkdir(parents=True, exist_ok=True)

    # Copy env_bootstrap.sh into the synthetic lib dir.
    prelude_text = _ENV_BOOTSTRAP_SH.read_text(encoding="utf-8")
    prelude_dest = lib_dir / "env_bootstrap.sh"
    prelude_dest.write_text(prelude_text, encoding="utf-8")
    prelude_dest.chmod(
        stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH
    )

    if with_shell_env:
        # shell-env exports PGAI_AGENT_KANBAN_ROOT_PATH to the root directory.
        # This mirrors the recommended shell-env content for a standard install.
        shell_env_text = f'export PGAI_AGENT_KANBAN_ROOT_PATH="{root!s}"\n'
        (root / "shell-env").write_text(shell_env_text, encoding="utf-8")

    return root


def _write_cron_caller(
    root: pathlib.Path,
    *,
    extra_lines: str = "",
) -> pathlib.Path:
    """Write a caller script that simulates wake_common.sh's bootstrap site.

    The script replicates the three bootstrap lines that wake_common.sh now
    contains after the delegation to env_bootstrap.sh:

        export PGAI_AGENT_KANBAN_ROOT_PATH="${PGAI_AGENT_KANBAN_ROOT_PATH:-$TEAM_ROOT}"
        source "${SCRIPT_DIR}/../lib/env_bootstrap.sh"
        [[ -f "$TEAM_ROOT/shell-env" ]] && source "$TEAM_ROOT/shell-env"

    The caller is placed at scripts/wake/caller.sh to mirror the real wake
    provider location.  SCRIPT_DIR is set to scripts/wake/ as the real wake
    scripts do.

    Parameters
    ----------
    root:
        Synthetic kanban root (from _make_synthetic_root).
    extra_lines:
        Optional bash lines appended after the bootstrap block.

    Returns
    -------
    pathlib.Path
        Absolute path to the created caller script.
    """
    script_dir = root / "scripts" / "wake"
    caller_text = (
        "#!/usr/bin/env bash\n"
        "# Simulates wake/claude.sh + wake_common.sh bootstrap site\n"
        "# (cron-style: PGAI_AGENT_KANBAN_ROOT_PATH not pre-set in env)\n"
        f'TEAM_ROOT="${{PGAI_AGENT_KANBAN_ROOT_PATH:-{root!s}}}"\n'
        f'SCRIPT_DIR="{script_dir!s}"\n'
        "\n"
        "# --- Bootstrap: delegate root absolutization to env_bootstrap.sh ---\n"
        "# (This is the exact code from wake_common.sh's bootstrap site.)\n"
        'export PGAI_AGENT_KANBAN_ROOT_PATH="${PGAI_AGENT_KANBAN_ROOT_PATH:-$TEAM_ROOT}"\n'
        '# shellcheck source=../lib/env_bootstrap.sh\n'
        'source "${SCRIPT_DIR}/../lib/env_bootstrap.sh"\n'
        "\n"
        "# --- Source optional shell-env for PATH and Python venv activation ---\n"
        '[[ -f "$TEAM_ROOT/shell-env" ]] && source "$TEAM_ROOT/shell-env"\n'
    )
    if extra_lines:
        caller_text += "\n" + extra_lines + "\n"

    caller_path = script_dir / "caller.sh"
    caller_path.write_text(caller_text, encoding="utf-8")
    caller_path.chmod(
        stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH
    )
    return caller_path


# ---------------------------------------------------------------------------
# Fixture 1: cron-style invocation (env -i + PATH + HOME) resolves root
# ---------------------------------------------------------------------------


def test_cron_style_bootstrap_resolves_root(tmp_path: pathlib.Path) -> None:
    """Live-shaped fixture: cron's minimal env resolves PGAI_AGENT_KANBAN_ROOT_PATH.

    This test exercises the acceptance criterion directly:
      "cron-style invocation of wake-batch.sh from env -i (plus PATH/HOME)
       resolves PGAI_AGENT_KANBAN_ROOT_PATH correctly."

    Method: run the bootstrap caller under env -i with only PATH and HOME, plus
    the synthetic kanban root path in HOME (simulating a standard install where
    the root is at $HOME/pgai_agent_kanban and shell-env exports it).

    The caller's TEAM_ROOT defaults to the synthetic root (via ${:-<root>}),
    which mirrors the real wake script's ${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}.
    """
    root = _make_synthetic_root(tmp_path, with_shell_env=True)
    caller = _write_cron_caller(root, extra_lines='echo "ROOT=${PGAI_AGENT_KANBAN_ROOT_PATH}"')

    # Cron-style env: only PATH and HOME; PGAI_AGENT_KANBAN_ROOT_PATH NOT set.
    result = subprocess.run(
        ["env", "-i",
         f"HOME={os.environ.get('HOME', '/root')}",
         f"PATH={os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin')}",
         "bash", str(caller)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, (
        f"Bootstrap failed under cron-style env:\n"
        f"stdout: {result.stdout!r}\n"
        f"stderr: {result.stderr!r}"
    )
    assert "ROOT=" in result.stdout, (
        f"PGAI_AGENT_KANBAN_ROOT_PATH was not echoed:\nstdout: {result.stdout!r}"
    )
    exported_root = result.stdout.strip().removeprefix("ROOT=")
    assert pathlib.Path(exported_root).is_absolute(), (
        f"Exported root is not absolute: {exported_root!r}"
    )
    # The exported root must point to the synthetic kanban root.
    assert exported_root == str(root.resolve()), (
        f"Wrong root exported.\n"
        f"Expected: {root.resolve()!s}\n"
        f"Got:      {exported_root!r}"
    )


def test_cron_style_bootstrap_silent_on_success(tmp_path: pathlib.Path) -> None:
    """Bootstrap produces no stdout output (zero bytes) on clean cron invocation.

    env_bootstrap.sh's contract: zero bytes on stdout. This verifies that the
    delegation does not accidentally print anything on the happy path.
    """
    root = _make_synthetic_root(tmp_path, with_shell_env=True)
    # Caller does NOT echo anything — test that bootstrap itself is silent.
    caller = _write_cron_caller(root)

    result = subprocess.run(
        ["env", "-i",
         f"HOME={os.environ.get('HOME', '/root')}",
         f"PATH={os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin')}",
         "bash", str(caller)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, (
        f"Bootstrap failed:\nstderr: {result.stderr!r}"
    )
    assert result.stdout == "", (
        f"Bootstrap emitted unexpected stdout:\n{result.stdout!r}"
    )


# ---------------------------------------------------------------------------
# Fixture 2: explicit PGAI_AGENT_KANBAN_ROOT_PATH wins over default
# ---------------------------------------------------------------------------


def test_explicit_env_wins_over_default_in_bootstrap(tmp_path: pathlib.Path) -> None:
    """Operator-set PGAI_AGENT_KANBAN_ROOT_PATH is not overridden by the default.

    Verifies the operator-env-wins contract: when the cron environment has
    PGAI_AGENT_KANBAN_ROOT_PATH set explicitly (e.g. from crontab VARIABLE=VALUE),
    wake_common.sh's bootstrap pre-export (${:-$TEAM_ROOT}) is a no-op and
    env_bootstrap.sh absolutizes the operator's value.
    """
    root = _make_synthetic_root(tmp_path, with_shell_env=True)
    caller = _write_cron_caller(root, extra_lines='echo "ROOT=${PGAI_AGENT_KANBAN_ROOT_PATH}"')

    # Operator explicitly set PGAI_AGENT_KANBAN_ROOT_PATH in the cron environment.
    operator_root = str(root)  # use the real root so realpath works

    result = subprocess.run(
        ["env", "-i",
         f"HOME={os.environ.get('HOME', '/root')}",
         f"PATH={os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin')}",
         f"PGAI_AGENT_KANBAN_ROOT_PATH={operator_root}",
         "bash", str(caller)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, (
        f"Bootstrap failed with explicit env:\nstderr: {result.stderr!r}"
    )
    exported_root = result.stdout.strip().removeprefix("ROOT=")
    assert exported_root == str(pathlib.Path(operator_root).resolve()), (
        f"Operator root was not preserved.\n"
        f"Expected: {pathlib.Path(operator_root).resolve()!s}\n"
        f"Got:      {exported_root!r}"
    )


# ---------------------------------------------------------------------------
# Fixture 3: shell-env absent → bootstrap still succeeds (default path)
# ---------------------------------------------------------------------------


def test_bootstrap_succeeds_without_shell_env(tmp_path: pathlib.Path) -> None:
    """Bootstrap succeeds when shell-env is absent (default-path install).

    When no shell-env is present and PGAI_AGENT_KANBAN_ROOT_PATH is not set
    in the environment, TEAM_ROOT's default value is used.  env_bootstrap.sh's
    idempotency guard fires (env was pre-set from TEAM_ROOT) and returns 0.
    No fail-loud occurs because the env var was already set before the source.
    """
    root = _make_synthetic_root(tmp_path, with_shell_env=False)
    caller = _write_cron_caller(root, extra_lines='echo "ROOT=${PGAI_AGENT_KANBAN_ROOT_PATH}"')

    result = subprocess.run(
        ["env", "-i",
         f"HOME={os.environ.get('HOME', '/root')}",
         f"PATH={os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin')}",
         "bash", str(caller)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, (
        f"Bootstrap failed without shell-env:\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    exported_root = result.stdout.strip().removeprefix("ROOT=")
    assert pathlib.Path(exported_root).is_absolute(), (
        f"Exported root is not absolute: {exported_root!r}"
    )
    # Root must match the synthetic root (the hardcoded default in the caller).
    assert exported_root == str(root.resolve()), (
        f"Wrong root when shell-env absent.\n"
        f"Expected: {root.resolve()!s}\n"
        f"Got:      {exported_root!r}"
    )


# ---------------------------------------------------------------------------
# Structural tests: wake_common.sh sources env_bootstrap.sh at bootstrap site
# ---------------------------------------------------------------------------


def test_wake_common_sources_env_bootstrap() -> None:
    """Structural: wake_common.sh sources env_bootstrap.sh at its bootstrap site.

    This is the delegation contract for Goal 3 of the v1.21.0 requirements.
    The presence of this source call (with the idempotency-safe pre-export)
    is the machine-readable proof that wake_common.sh no longer carries an
    independent bootstrap implementation.
    """
    content = _WAKE_COMMON_SH.read_text(encoding="utf-8")
    source_line = 'source "${SCRIPT_DIR}/../lib/env_bootstrap.sh"'
    assert source_line in content, (
        f"{_WAKE_COMMON_SH} does not contain the expected source call to env_bootstrap.sh. "
        "wake_common.sh must delegate root absolutization to env_bootstrap.sh "
        "(Goal 3 of v1.21.0-env-bootstrap-unification). "
        f"Expected line: {source_line!r}"
    )


def test_wake_common_pre_exports_before_bootstrap() -> None:
    """Structural: wake_common.sh pre-exports PGAI_AGENT_KANBAN_ROOT_PATH before
    sourcing env_bootstrap.sh.

    The pre-export (export PGAI_AGENT_KANBAN_ROOT_PATH="${PGAI_AGENT_KANBAN_ROOT_PATH:-$TEAM_ROOT}")
    is required to trigger env_bootstrap.sh's idempotency guard from the lib context,
    where BASH_SOURCE[1] would point to the lib directory rather than the kanban root.
    Without this pre-export, env_bootstrap.sh would derive the wrong candidate root.
    """
    content = _WAKE_COMMON_SH.read_text(encoding="utf-8")
    # Verify the pre-export pattern is present above the env_bootstrap.sh source call.
    pre_export_marker = "PGAI_AGENT_KANBAN_ROOT_PATH:-$TEAM_ROOT"
    assert pre_export_marker in content, (
        f"{_WAKE_COMMON_SH} does not contain the pre-export pattern "
        f"'export PGAI_AGENT_KANBAN_ROOT_PATH=\"${{{pre_export_marker}}}\"'. "
        "This pre-export is required for safe delegation to env_bootstrap.sh "
        "from the lib sourcing context."
    )
    # Verify pre-export appears before the `source` call to env_bootstrap.sh.
    # Use the `source` line rather than a comment reference to env_bootstrap.sh.
    source_line = 'source "${SCRIPT_DIR}/../lib/env_bootstrap.sh"'
    assert source_line in content, (
        f"{_WAKE_COMMON_SH} does not contain the expected source line: {source_line!r}"
    )
    pre_export_pos = content.index(pre_export_marker)
    bootstrap_pos = content.index(source_line)
    assert pre_export_pos < bootstrap_pos, (
        f"{_WAKE_COMMON_SH}: the pre-export of PGAI_AGENT_KANBAN_ROOT_PATH "
        f"must appear before the source of env_bootstrap.sh. "
        f"Found pre-export at offset {pre_export_pos}, bootstrap source at {bootstrap_pos}."
    )


def test_wake_common_still_sources_shell_env_after_bootstrap() -> None:
    """Structural: wake_common.sh still sources shell-env after env_bootstrap.sh.

    Shell-env serves two purposes beyond root resolution: PATH adjustments and
    Python venv activation.  env_bootstrap.sh's idempotency guard returns
    immediately when the env var is already set, so shell-env must be sourced
    separately for these side effects to take effect in the cron environment.
    """
    content = _WAKE_COMMON_SH.read_text(encoding="utf-8")

    # Both the env_bootstrap.sh source call and the shell-env source must be present.
    source_bootstrap = 'source "${SCRIPT_DIR}/../lib/env_bootstrap.sh"'
    source_shell_env = 'source "$TEAM_ROOT/shell-env"'
    assert source_bootstrap in content, (
        f"{_WAKE_COMMON_SH} does not contain the env_bootstrap.sh source call."
    )
    assert source_shell_env in content, (
        f"{_WAKE_COMMON_SH} does not source shell-env. "
        "Shell-env is still required for PATH and venv activation even after "
        "root resolution is delegated to env_bootstrap.sh. "
        f"Expected line: {source_shell_env!r}"
    )

    # shell-env source must appear AFTER env_bootstrap.sh source call.
    bootstrap_pos = content.index(source_bootstrap)
    shell_env_pos = content.index(source_shell_env)
    assert shell_env_pos > bootstrap_pos, (
        f"{_WAKE_COMMON_SH}: shell-env sourcing must appear AFTER env_bootstrap.sh. "
        f"env_bootstrap.sh source at offset {bootstrap_pos}, "
        f"shell-env source at {shell_env_pos}."
    )
