"""
test_subagent_deploy_dir_creation.py
=====================================
Behavioral tests for the provider-agent wrapper deploy's agents-directory
creation step, shared by install.sh and team/scripts/upgrade.sh via
team/scripts/lib/subagent_deploy.sh's ``ensure_provider_agents_dir``.

WHAT IS TESTED
--------------
The guarded defect: a missing target directory (``~/.claude/agents/``,
``~/.codex/agents/``) that either requires an interactive confirmation
(install.sh) or is skipped outright without ``--force`` (upgrade.sh) — on
a fresh machine where the provider CLI has not yet created its config tree,
this produces a silent no-op: no directory, no wrappers, no error.

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

A fifth set covers manifest completeness: every wrapper shipped under
``subagents/`` (OVERWATCH included) is listed in the manifest, and both
entry points install the OVERWATCH wrapper byte-identical to its source and
record it in ``.installed-subagents``.

A sixth set covers where the claude wrappers land: under
``$CLAUDE_CONFIG_DIR/agents`` when that variable is set (and nothing under
``$HOME/.claude/agents``), under ``$HOME/.claude/agents`` when it is unset,
for both entry points; codex wrappers stay under ``$HOME/.codex/agents``.

SANDBOX DESIGN
---------------
install.sh runs directly from the real dev tree (this checkout) against a
throwaway kanban root, throwaway ``HOME`` and a ``CLAUDE_CONFIG_DIR`` inside
that ``HOME`` — this is exactly how a fresh install invocation works, so no
synthetic entry-point stub is needed.

Every install and upgrade run gets an environment built by
``build_scrubbed_env`` rather than inherited, with all five live-install
variables set or cleared together: ``KANBAN_ROOT``,
``PGAI_AGENT_KANBAN_ROOT_PATH`` and ``PGAI_AGENT_KANBAN_TEMP_DIR`` point
into ``tmp_path``; ``PGAI_TASKS_DIR`` and ``PGAI_PROJECT_ROOT`` are absent.
A direct pytest run from an agent wake or an operator shell therefore never
hands the real scripts the live install's paths.

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

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

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

# Default for the run helpers' ``claude_config_dir``: point CLAUDE_CONFIG_DIR
# at ``<home>/.claude``, the layout most tests here assert on.
_CLAUDE_CONFIG_IN_HOME = object()


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


def _sandbox_env(
    *,
    home: pathlib.Path,
    kanban_root: pathlib.Path,
    temp_dir: pathlib.Path,
    bin_dir: pathlib.Path,
    claude_config_dir: object,
) -> dict[str, str]:
    """Scrubbed subprocess environment for the real install.sh / upgrade.sh.

    Built by ``build_scrubbed_env`` from a fixed set of names, never from
    ``os.environ``: HOME is *home*, PATH is *bin_dir* ahead of the standard
    toolchain, and all five live-install variables are set or cleared
    together — KANBAN_ROOT and PGAI_AGENT_KANBAN_ROOT_PATH are *kanban_root*,
    PGAI_AGENT_KANBAN_TEMP_DIR is *temp_dir* (created here), and
    PGAI_TASKS_DIR and PGAI_PROJECT_ROOT are absent, so neither of those
    pre-resolved paths can outrank the sandboxed roots.

    *claude_config_dir* is ``_CLAUDE_CONFIG_IN_HOME`` (``<home>/.claude``), a
    path, or ``None`` to leave CLAUDE_CONFIG_DIR out of the environment so
    the installers fall back to their default.
    """
    temp_dir.mkdir(parents=True, exist_ok=True)
    extra_env = {
        "KANBAN_ROOT": str(kanban_root),
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_dir),
    }
    if claude_config_dir is _CLAUDE_CONFIG_IN_HOME:
        extra_env["CLAUDE_CONFIG_DIR"] = str(home / ".claude")
    elif claude_config_dir is not None:
        extra_env["CLAUDE_CONFIG_DIR"] = str(claude_config_dir)
    return build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[bin_dir],
        extra_env=extra_env,
    )


def _run_install(
    tmp_path: pathlib.Path,
    *,
    home: pathlib.Path,
    kanban_root: pathlib.Path,
    claude_config_dir: object = _CLAUDE_CONFIG_IN_HOME,
) -> subprocess.CompletedProcess:
    """Run the real install.sh with both provider-agent flags enabled.

    Uses --force --no-system-cron so the run is non-interactive and never
    touches the host crontab; --stamp-version avoids a git resolution call.
    """
    bin_dir = tmp_path / "stub_bin"
    _write_stub_provider_binaries(bin_dir)

    env = _sandbox_env(
        home=home,
        kanban_root=kanban_root,
        temp_dir=tmp_path / "sandbox_tmp",
        bin_dir=bin_dir,
        claude_config_dir=claude_config_dir,
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
    claude_config_dir: object = _CLAUDE_CONFIG_IN_HOME,
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

    env = _sandbox_env(
        home=home,
        kanban_root=kanban_root,
        temp_dir=tmp_path / "sandbox_tmp",
        bin_dir=bin_dir,
        claude_config_dir=claude_config_dir,
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
# Sandbox environment — no live-install path reaches the real scripts
# ---------------------------------------------------------------------------

_LIVE_INSTALL_ENV_NAMES = (
    "KANBAN_ROOT",
    "PGAI_AGENT_KANBAN_ROOT_PATH",
    "PGAI_AGENT_KANBAN_TEMP_DIR",
    "PGAI_TASKS_DIR",
    "PGAI_PROJECT_ROOT",
)


def test_sandbox_env_carries_no_inherited_live_install_path(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All five live-install names set in the parent never reach the child.

    The parent environment points every live-install variable at a sentinel
    path; the sandbox environment must carry that sentinel in no value, must
    point the two roots and the temp directory into ``tmp_path``, and must
    leave PGAI_TASKS_DIR and PGAI_PROJECT_ROOT absent.
    """
    sentinel = "/nonexistent/live-install-sentinel"
    for name in _LIVE_INSTALL_ENV_NAMES:
        monkeypatch.setenv(name, f"{sentinel}/{name.lower()}")

    kanban_root = tmp_path / "kanban_root"
    temp_dir = tmp_path / "sandbox_tmp"
    env = _sandbox_env(
        home=tmp_path / "home",
        kanban_root=kanban_root,
        temp_dir=temp_dir,
        bin_dir=tmp_path / "stub_bin",
        claude_config_dir=_CLAUDE_CONFIG_IN_HOME,
    )

    leaked = {name: value for name, value in env.items() if sentinel in value}
    assert leaked == {}, f"inherited live-install paths reached the sandbox: {leaked}"
    assert env["KANBAN_ROOT"] == str(kanban_root)
    assert env["PGAI_AGENT_KANBAN_ROOT_PATH"] == str(kanban_root)
    assert env["PGAI_AGENT_KANBAN_TEMP_DIR"] == str(temp_dir)
    assert "PGAI_TASKS_DIR" not in env
    assert "PGAI_PROJECT_ROOT" not in env


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


# ---------------------------------------------------------------------------
# Manifest completeness — every shipped wrapper, OVERWATCH included
# ---------------------------------------------------------------------------

_REAL_SUBAGENTS_DIR = _REPO_ROOT / "subagents"
_OVERWATCH_SRC = _REAL_SUBAGENTS_DIR / "overwatch.md"


def _manifest_names() -> list[str]:
    """Non-comment, non-blank names listed in the real subagent manifest."""
    names = [
        ln.split("#", 1)[0].strip()
        for ln in _REAL_MANIFEST.read_text(encoding="utf-8").splitlines()
    ]
    return [n for n in names if n]


def _installed_subagents_state(kanban_root: pathlib.Path) -> list[str]:
    """Names recorded in the root's .installed-subagents state file."""
    state = kanban_root / ".installed-subagents"
    assert state.is_file(), f".installed-subagents was not written under {kanban_root}"
    names = [
        ln.split("#", 1)[0].strip()
        for ln in state.read_text(encoding="utf-8").splitlines()
    ]
    return [n for n in names if n]


def test_manifest_lists_every_shipped_subagent_wrapper() -> None:
    """Every wrapper under subagents/ is installed, so every one is listed.

    The wake dispatches each of these wrappers by name; one missing from the
    manifest is never installed or refreshed by install.sh or upgrade.sh.
    """
    # Intentional pattern-scan: every current and future subagents/*.md is a
    # dispatched wrapper and must be in the manifest the installers read.
    shipped = sorted(p.stem for p in _REAL_SUBAGENTS_DIR.glob("*.md"))
    listed = _manifest_names()
    missing = [name for name in shipped if name not in listed]
    assert not missing, (
        f"wrappers shipped under {_REAL_SUBAGENTS_DIR} but absent from "
        f"{_REAL_MANIFEST}: {missing}"
    )
    assert "overwatch" in listed, f"overwatch must be listed in {_REAL_MANIFEST}"


@pytest.mark.skipif(
    not _REAL_INSTALL_SH.exists() or not _OVERWATCH_SRC.exists(),
    reason="Real install.sh or subagents/overwatch.md not found; skipping.",
)
def test_install_deploys_overwatch_wrapper_identical_to_source_and_tracks_it(
    tmp_path: pathlib.Path,
) -> None:
    """A fixture install writes agents/overwatch.md byte-identical to its source.

    The install runs into a sandbox root with a temporary HOME and
    CLAUDE_CONFIG_DIR, and records overwatch in .installed-subagents.
    """
    home = tmp_path / "home"
    home.mkdir()
    kanban_root = tmp_path / "kanban_root"

    result = _run_install(tmp_path, home=home, kanban_root=kanban_root)
    output = _combined_output(result)
    assert result.returncode == 0, f"install.sh failed:\n{output}"

    deployed = home / ".claude" / "agents" / "overwatch.md"
    assert deployed.is_file(), f"overwatch wrapper not deployed:\n{output}"
    assert deployed.read_bytes() == _OVERWATCH_SRC.read_bytes(), (
        f"{deployed} differs from {_OVERWATCH_SRC}"
    )
    assert "overwatch" in _installed_subagents_state(kanban_root)


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists() or not _OVERWATCH_SRC.exists(),
    reason="Real upgrade.sh or subagents/overwatch.md not found; skipping.",
)
def test_upgrade_add_claude_agents_deploys_overwatch_wrapper_and_tracks_it(
    tmp_path: pathlib.Path,
) -> None:
    """upgrade.sh --add-claude-agents installs and tracks overwatch too."""
    home = tmp_path / "home"
    home.mkdir()
    kanban_root = tmp_path / "kanban_root"

    result = _run_upgrade_phase2(tmp_path, home=home, kanban_root=kanban_root)
    output = _combined_output(result)
    assert result.returncode == 0, f"upgrade.sh --phase2 failed:\n{output}"

    deployed = home / ".claude" / "agents" / "overwatch.md"
    assert deployed.is_file(), f"overwatch wrapper not deployed:\n{output}"
    assert deployed.read_bytes() == _OVERWATCH_SRC.read_bytes(), (
        f"{deployed} differs from {_OVERWATCH_SRC}"
    )
    assert "overwatch" in _installed_subagents_state(kanban_root)


# ---------------------------------------------------------------------------
# Claude wrapper destination — CLAUDE_CONFIG_DIR set or unset
# ---------------------------------------------------------------------------

_ENTRY_POINTS = {
    "install": (_REAL_INSTALL_SH, _run_install),
    "upgrade": (_REAL_UPGRADE_SH, _run_upgrade_phase2),
}


def _assert_every_manifest_wrapper_in(agents_dir: pathlib.Path, output: str) -> None:
    for name in _manifest_names():
        assert (agents_dir / f"{name}.md").is_file(), (
            f"missing wrapper {name}.md under {agents_dir}:\n{output}"
        )


@pytest.mark.parametrize("entry_point", sorted(_ENTRY_POINTS))
def test_claude_wrappers_land_in_claude_config_dir_when_set(
    tmp_path: pathlib.Path,
    entry_point: str,
) -> None:
    """With CLAUDE_CONFIG_DIR outside HOME, claude wrappers go there only.

    Every manifest wrapper lands in ``$CLAUDE_CONFIG_DIR/agents`` and
    nothing is written under ``$HOME/.claude``; codex wrappers still land
    in ``$HOME/.codex/agents``.
    """
    script, run = _ENTRY_POINTS[entry_point]
    if not script.exists():
        pytest.skip(f"{script} not found")
    home = tmp_path / "home"
    home.mkdir()
    claude_config_dir = tmp_path / "claude_config"
    kanban_root = tmp_path / "kanban_root"

    result = run(
        tmp_path,
        home=home,
        kanban_root=kanban_root,
        claude_config_dir=claude_config_dir,
    )
    output = _combined_output(result)
    assert result.returncode == 0, f"{entry_point} failed:\n{output}"

    claude_agents = claude_config_dir / "agents"
    assert f"created {claude_agents}" in output, output
    _assert_every_manifest_wrapper_in(claude_agents, output)
    assert not (home / ".claude").exists(), (
        f"nothing may be written under {home / '.claude'} when "
        f"CLAUDE_CONFIG_DIR is set:\n{output}"
    )
    _assert_every_manifest_wrapper_in(home / ".codex" / "agents", output)


@pytest.mark.parametrize("entry_point", sorted(_ENTRY_POINTS))
def test_claude_wrappers_land_in_home_dot_claude_when_config_dir_unset(
    tmp_path: pathlib.Path,
    entry_point: str,
) -> None:
    """With CLAUDE_CONFIG_DIR unset, claude wrappers go to ``$HOME/.claude/agents``."""
    script, run = _ENTRY_POINTS[entry_point]
    if not script.exists():
        pytest.skip(f"{script} not found")
    home = tmp_path / "home"
    home.mkdir()
    kanban_root = tmp_path / "kanban_root"

    result = run(tmp_path, home=home, kanban_root=kanban_root, claude_config_dir=None)
    output = _combined_output(result)
    assert result.returncode == 0, f"{entry_point} failed:\n{output}"

    _assert_every_manifest_wrapper_in(home / ".claude" / "agents", output)


def _run_provider_agents_dir(
    provider: str, *, home: pathlib.Path, claude_config_dir: str | None
) -> str:
    """Source subagent_deploy.sh in isolation and print provider_agents_dir."""
    env = dict(os.environ)
    env["HOME"] = str(home)
    env.pop("CLAUDE_CONFIG_DIR", None)
    if claude_config_dir is not None:
        env["CLAUDE_CONFIG_DIR"] = claude_config_dir
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'set -u; source "{_REAL_SUBAGENT_DEPLOY_SH}"; provider_agents_dir "{provider}"',
        ],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.skipif(
    not _REAL_SUBAGENT_DEPLOY_SH.exists(),
    reason=f"subagent_deploy.sh not found at {_REAL_SUBAGENT_DEPLOY_SH}; skipping.",
)
@pytest.mark.parametrize(
    "provider,claude_config_dir,expected",
    [
        ("claude", "/cfg/claude", "/cfg/claude/agents"),
        ("claude", None, "{home}/.claude/agents"),
        ("claude", "", "{home}/.claude/agents"),
        ("codex", "/cfg/claude", "{home}/.codex/agents"),
        ("codex", None, "{home}/.codex/agents"),
    ],
)
def test_provider_agents_dir_resolution(
    tmp_path: pathlib.Path,
    provider: str,
    claude_config_dir: str | None,
    expected: str,
) -> None:
    """claude honours a non-empty CLAUDE_CONFIG_DIR; other providers use ``$HOME/.<provider>``."""
    home = tmp_path / "home"
    got = _run_provider_agents_dir(provider, home=home, claude_config_dir=claude_config_dir)
    assert got == expected.format(home=home), got
