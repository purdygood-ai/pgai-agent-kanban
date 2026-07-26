"""
test_subagent_deploy_dir_creation.py
=====================================
Behavioral tests for the provider-agent wrapper deploy's agents-directory
creation step, shared by install.sh and team/scripts/upgrade.sh via
team/scripts/lib/subagent_deploy.sh's ``ensure_provider_agents_dir``.

WHAT IS TESTED
--------------
Before this fix, a missing target directory (``~/.claude/agents/``,
``~/.codex/agents/``) either required an interactive confirmation
(install.sh) or was skipped outright without ``--force`` (upgrade.sh) — on
a fresh machine where the provider CLI has not yet created its config tree,
this produced a silent no-op: no directory, no wrappers, no error.

These tests verify the fixed contract for BOTH entry points (install.sh and
the phase-2 body of team/scripts/upgrade.sh) and BOTH providers (claude,
codex):

1. Missing agents directory: the deploy creates it unconditionally, narrates
   "created <path>", deposits the manifest wrappers, and narrates
   "deployed N wrapper(s)".
2. Pre-existing agents directory: behavior is unchanged except for the added
   "deployed N wrapper(s)" narration — no "created" line is emitted.
3. Unwritable HOME (directory creation impossible): the deploy fails loud,
   names the failing path, and exits non-zero.

A fourth set of tests exercises ``ensure_provider_agents_dir`` directly
(sourced in isolation) for fast, precise coverage of the narration and
fail-loud contract without paying for a full script run.

SANDBOX DESIGN
---------------
install.sh runs directly from the real dev tree (this checkout) against a
throwaway ``PGAI_AGENT_KANBAN_ROOT_PATH`` and throwaway ``HOME`` — this is
exactly how a fresh install invocation works, so no synthetic entry-point
stub is needed.

upgrade.sh's phase-2 body is invoked directly with ``--phase2
--phase2-protocol 1 --kanban-root <path>``, bypassing the phase-1
bootstrap/re-exec dance (already covered by other tests in this suite) so
these tests stay focused on the deploy step itself. Phase 2 infers DEV_TREE
from its own BASH_SOURCE, so pointing at the real dev tree's
team/scripts/upgrade.sh exercises the real, just-edited code.

Fake ``claude`` / ``codex`` binaries are placed on PATH (outside HOME) so
the CLI-detection gate in both entry points passes without depending on the
real provider CLIs being installed on the test host.

TEST NAMING CONVENTION
-----------------------
Function names describe the behavior under test, not the scaffolding that
supports it.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import textwrap

import pytest

# ---------------------------------------------------------------------------
# Paths to the real scripts (dev tree layout, relative to this test file)
# ---------------------------------------------------------------------------

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_REPO_ROOT = _TEAM_DIR.parent

_REAL_INSTALL_SH = _REPO_ROOT / "install.sh"
_REAL_UPGRADE_SH = _TEAM_DIR / "scripts" / "upgrade.sh"
_REAL_SUBAGENT_DEPLOY_SH = _TEAM_DIR / "scripts" / "lib" / "subagent_deploy.sh"
_REAL_MANIFEST = _REPO_ROOT / "subagents" / "MANIFEST.txt"

_TIMEOUT = 60


def _expected_wrapper_count() -> int:
    """Number of non-comment, non-blank entries in the real subagent manifest."""
    lines = _REAL_MANIFEST.read_text(encoding="utf-8").splitlines()
    names = [
        ln.split("#", 1)[0].strip()
        for ln in lines
    ]
    return len([n for n in names if n])


_EXPECTED_WRAPPER_COUNT = _expected_wrapper_count()


# ---------------------------------------------------------------------------
# Shared scaffolding
# ---------------------------------------------------------------------------


def _write_stub_provider_binaries(bin_dir: pathlib.Path) -> None:
    """Create no-op executable stubs named 'claude' and 'codex' on PATH.

    Both entry points gate provider deployment on `command -v <provider>`
    finding a binary. The stubs let the CLI-detection gate pass without
    requiring the real provider CLIs on the test host.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in ("claude", "codex"):
        stub = bin_dir / name
        stub.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)


def _run_install(
    tmp_path: pathlib.Path,
    *,
    home: pathlib.Path,
    kanban_root: pathlib.Path,
) -> subprocess.CompletedProcess:
    """Run the real install.sh with both provider-agent flags enabled.

    Uses --force --no-system-cron so the run is non-interactive and never
    touches the host crontab; --stamp-version avoids a git resolution call.
    """
    bin_dir = tmp_path / "stub_bin"
    _write_stub_provider_binaries(bin_dir)

    env = dict(os.environ)
    env.update(
        {
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
            "HOME": str(home),
            "PATH": f"{bin_dir}:{env.get('PATH', '')}",
        }
    )

    return subprocess.run(
        [
            "bash",
            str(_REAL_INSTALL_SH),
            "--force",
            "--no-system-cron",
            "--add-claude-agents",
            "--add-codex-agents",
            "--stamp-version",
            "v0.0.0-sandbox-test",
        ],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        env=env,
    )


def _run_upgrade_phase2(
    tmp_path: pathlib.Path,
    *,
    home: pathlib.Path,
    kanban_root: pathlib.Path,
) -> subprocess.CompletedProcess:
    """Run the real upgrade.sh phase-2 body directly against kanban_root.

    Bypasses the phase-1 bootstrap/re-exec dance (covered elsewhere) so the
    test stays focused on the deploy step. DEV_TREE is inferred by the
    script itself from BASH_SOURCE, so this exercises the real, just-edited
    deploy code in this checkout.
    """
    bin_dir = tmp_path / "stub_bin"
    _write_stub_provider_binaries(bin_dir)
    kanban_root.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env.update(
        {
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
            "HOME": str(home),
            "PATH": f"{bin_dir}:{env.get('PATH', '')}",
        }
    )

    return subprocess.run(
        [
            "bash",
            str(_REAL_UPGRADE_SH),
            "--phase2",
            "--phase2-protocol",
            "1",
            "--kanban-root",
            str(kanban_root),
            "--force",
            "--add-claude-agents",
            "--add-codex-agents",
            "--stamp-version",
            "v0.0.1-sandbox-test",
        ],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        env=env,
    )


def _combined_output(result: subprocess.CompletedProcess) -> str:
    return f"{result.stdout}\n{result.stderr}"


# ---------------------------------------------------------------------------
# install.sh — missing agents directory
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _REAL_INSTALL_SH.exists(),
    reason=f"Real install.sh not found at {_REAL_INSTALL_SH}; skipping.",
)
def test_install_creates_missing_agents_dirs_and_narrates_for_both_providers(
    tmp_path: pathlib.Path,
) -> None:
    """A fresh HOME without ~/.claude or ~/.codex gets both dirs created.

    Both provider directories are created, wrappers are deposited into each,
    and the run narrates both the creation and the deployed-count line for
    claude and codex.
    """
    home = tmp_path / "home"
    home.mkdir()
    kanban_root = tmp_path / "kanban_root"

    result = _run_install(tmp_path, home=home, kanban_root=kanban_root)
    output = _combined_output(result)

    assert result.returncode == 0, f"install.sh failed:\n{output}"

    claude_dir = home / ".claude" / "agents"
    codex_dir = home / ".codex" / "agents"

    assert f"created {claude_dir}" in output, output
    assert f"created {codex_dir}" in output, output
    assert output.count(f"deployed {_EXPECTED_WRAPPER_COUNT} wrapper(s)") == 2, output

    assert claude_dir.is_dir()
    assert codex_dir.is_dir()
    manifest_names = [
        ln.split("#", 1)[0].strip()
        for ln in _REAL_MANIFEST.read_text(encoding="utf-8").splitlines()
    ]
    for name in [n for n in manifest_names if n]:
        assert (claude_dir / f"{name}.md").is_file(), f"missing claude wrapper: {name}"
        assert (codex_dir / f"{name}.md").is_file(), f"missing codex wrapper: {name}"


# ---------------------------------------------------------------------------
# install.sh — pre-existing agents directory
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _REAL_INSTALL_SH.exists(),
    reason=f"Real install.sh not found at {_REAL_INSTALL_SH}; skipping.",
)
def test_install_existing_agents_dir_omits_created_narration(
    tmp_path: pathlib.Path,
) -> None:
    """A pre-existing agents directory is left alone apart from the count line.

    No "created <path>" narration is emitted since the directory already
    existed; the "deployed N wrapper(s)" line still appears for both
    providers, and the wrappers still land on disk.
    """
    home = tmp_path / "home"
    home.mkdir()
    claude_dir = home / ".claude" / "agents"
    codex_dir = home / ".codex" / "agents"
    claude_dir.mkdir(parents=True)
    codex_dir.mkdir(parents=True)
    kanban_root = tmp_path / "kanban_root"

    result = _run_install(tmp_path, home=home, kanban_root=kanban_root)
    output = _combined_output(result)

    assert result.returncode == 0, f"install.sh failed:\n{output}"
    assert "created " not in output, output
    assert output.count(f"deployed {_EXPECTED_WRAPPER_COUNT} wrapper(s)") == 2, output
    assert any(claude_dir.iterdir())
    assert any(codex_dir.iterdir())


# ---------------------------------------------------------------------------
# install.sh — unwritable HOME
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _REAL_INSTALL_SH.exists(),
    reason=f"Real install.sh not found at {_REAL_INSTALL_SH}; skipping.",
)
def test_install_unwritable_home_fails_loud_and_exits_nonzero(
    tmp_path: pathlib.Path,
) -> None:
    """A read-only HOME makes directory creation impossible; the deploy must
    fail loud naming the exact path and exit non-zero rather than silently
    skip the provider.
    """
    home = tmp_path / "home"
    home.mkdir()
    kanban_root = tmp_path / "kanban_root"
    claude_dir = home / ".claude" / "agents"

    home.chmod(0o555)
    try:
        result = _run_install(tmp_path, home=home, kanban_root=kanban_root)
    finally:
        home.chmod(0o755)
    output = _combined_output(result)

    assert result.returncode != 0, f"expected non-zero exit; got 0.\n{output}"
    assert f"Failed to create provider agents directory: {claude_dir}" in output, output


# ---------------------------------------------------------------------------
# upgrade.sh phase 2 — missing agents directory
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.",
)
def test_upgrade_phase2_creates_missing_agents_dirs_and_narrates_for_both_providers(
    tmp_path: pathlib.Path,
) -> None:
    """Same fixture as install.sh, run through upgrade.sh's phase-2 body."""
    home = tmp_path / "home"
    home.mkdir()
    kanban_root = tmp_path / "kanban_root"

    result = _run_upgrade_phase2(tmp_path, home=home, kanban_root=kanban_root)
    output = _combined_output(result)

    assert result.returncode == 0, f"upgrade.sh --phase2 failed:\n{output}"

    claude_dir = home / ".claude" / "agents"
    codex_dir = home / ".codex" / "agents"

    assert f"created {claude_dir}" in output, output
    assert f"created {codex_dir}" in output, output
    assert output.count(f"deployed {_EXPECTED_WRAPPER_COUNT} wrapper(s)") == 2, output

    manifest_names = [
        ln.split("#", 1)[0].strip()
        for ln in _REAL_MANIFEST.read_text(encoding="utf-8").splitlines()
    ]
    for name in [n for n in manifest_names if n]:
        assert (claude_dir / f"{name}.md").is_file(), f"missing claude wrapper: {name}"
        assert (codex_dir / f"{name}.md").is_file(), f"missing codex wrapper: {name}"


# ---------------------------------------------------------------------------
# upgrade.sh phase 2 — pre-existing agents directory
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.",
)
def test_upgrade_phase2_existing_agents_dir_omits_created_narration(
    tmp_path: pathlib.Path,
) -> None:
    """A pre-existing agents directory is left alone apart from the count line."""
    home = tmp_path / "home"
    home.mkdir()
    claude_dir = home / ".claude" / "agents"
    codex_dir = home / ".codex" / "agents"
    claude_dir.mkdir(parents=True)
    codex_dir.mkdir(parents=True)
    kanban_root = tmp_path / "kanban_root"

    result = _run_upgrade_phase2(tmp_path, home=home, kanban_root=kanban_root)
    output = _combined_output(result)

    assert result.returncode == 0, f"upgrade.sh --phase2 failed:\n{output}"
    assert "created " not in output, output
    assert output.count(f"deployed {_EXPECTED_WRAPPER_COUNT} wrapper(s)") == 2, output
    assert any(claude_dir.iterdir())
    assert any(codex_dir.iterdir())


# ---------------------------------------------------------------------------
# upgrade.sh phase 2 — unwritable HOME
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.",
)
def test_upgrade_phase2_unwritable_home_fails_loud_and_exits_nonzero(
    tmp_path: pathlib.Path,
) -> None:
    """A read-only HOME makes directory creation impossible during upgrade;
    the deploy must fail loud naming the exact path and exit non-zero.
    """
    home = tmp_path / "home"
    home.mkdir()
    kanban_root = tmp_path / "kanban_root"
    claude_dir = home / ".claude" / "agents"

    home.chmod(0o555)
    try:
        result = _run_upgrade_phase2(tmp_path, home=home, kanban_root=kanban_root)
    finally:
        home.chmod(0o755)
    output = _combined_output(result)

    assert result.returncode != 0, f"expected non-zero exit; got 0.\n{output}"
    assert f"Failed to create provider agents directory: {claude_dir}" in output, output


# ---------------------------------------------------------------------------
# ensure_provider_agents_dir — isolated lib-function coverage
# ---------------------------------------------------------------------------


def _run_ensure_provider_agents_dir(
    agents_dir: pathlib.Path, dry_run: str = "false"
) -> subprocess.CompletedProcess:
    """Source subagent_deploy.sh in isolation and call the function directly.

    Supplies minimal info()/ok()/err() stand-ins matching install.sh's and
    upgrade.sh's real signatures (info/ok to stdout, err to stderr) so the
    function's narration can be asserted without running a full script.
    """
    script = textwrap.dedent(
        f"""\
        set -uo pipefail
        info() {{ echo "[test] $*"; }}
        ok()   {{ echo "[test] $*"; }}
        err()  {{ echo "[test] $*" >&2; }}
        source "{_REAL_SUBAGENT_DEPLOY_SH}"
        ensure_provider_agents_dir "{agents_dir}" "{dry_run}"
        echo "RC=$?"
        """
    )
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.skipif(
    not _REAL_SUBAGENT_DEPLOY_SH.exists(),
    reason=f"subagent_deploy.sh not found at {_REAL_SUBAGENT_DEPLOY_SH}; skipping.",
)
def test_ensure_provider_agents_dir_creates_and_narrates_when_missing(
    tmp_path: pathlib.Path,
) -> None:
    agents_dir = tmp_path / "provider" / "agents"
    result = _run_ensure_provider_agents_dir(agents_dir)

    assert "RC=0" in result.stdout, result.stdout + result.stderr
    assert f"created {agents_dir}" in result.stdout
    assert agents_dir.is_dir()


@pytest.mark.skipif(
    not _REAL_SUBAGENT_DEPLOY_SH.exists(),
    reason=f"subagent_deploy.sh not found at {_REAL_SUBAGENT_DEPLOY_SH}; skipping.",
)
def test_ensure_provider_agents_dir_silent_when_already_present(
    tmp_path: pathlib.Path,
) -> None:
    agents_dir = tmp_path / "provider" / "agents"
    agents_dir.mkdir(parents=True)
    result = _run_ensure_provider_agents_dir(agents_dir)

    assert "RC=0" in result.stdout, result.stdout + result.stderr
    assert "created" not in result.stdout


@pytest.mark.skipif(
    not _REAL_SUBAGENT_DEPLOY_SH.exists(),
    reason=f"subagent_deploy.sh not found at {_REAL_SUBAGENT_DEPLOY_SH}; skipping.",
)
def test_ensure_provider_agents_dir_dry_run_reports_without_creating(
    tmp_path: pathlib.Path,
) -> None:
    agents_dir = tmp_path / "provider" / "agents"
    result = _run_ensure_provider_agents_dir(agents_dir, dry_run="true")

    assert "RC=0" in result.stdout, result.stdout + result.stderr
    assert f"[dry-run] would create directory: {agents_dir}" in result.stdout
    assert not agents_dir.exists()


@pytest.mark.skipif(
    not _REAL_SUBAGENT_DEPLOY_SH.exists(),
    reason=f"subagent_deploy.sh not found at {_REAL_SUBAGENT_DEPLOY_SH}; skipping.",
)
def test_ensure_provider_agents_dir_fails_loud_on_unwritable_parent(
    tmp_path: pathlib.Path,
) -> None:
    parent = tmp_path / "readonly_parent"
    parent.mkdir()
    agents_dir = parent / "agents"

    parent.chmod(0o555)
    try:
        result = _run_ensure_provider_agents_dir(agents_dir)
    finally:
        parent.chmod(0o755)

    assert "RC=1" in result.stdout, result.stdout + result.stderr
    assert f"Failed to create provider agents directory: {agents_dir}" in result.stderr
    assert not agents_dir.exists()
