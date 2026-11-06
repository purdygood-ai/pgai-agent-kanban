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
The same resolver names the provider skill homes (``skills`` in place of
``agents``).

A seventh set covers install.sh's skill staging: every shipped skill lands
byte-identical under ``$KANBAN_ROOT/skills/<provider>/<name>/``, the shipped
set is recorded with SHA-256 digests in ``.installed-skills``, nothing is
written to a provider skill home without a skill flag, and an operator-added
skill in the staging directory survives a re-install untouched. With
``--add-claude-skills`` the staged claude skill is installed byte-identical
into ``$CLAUDE_CONFIG_DIR/skills``, and ``--help`` lists both skill flags
right after the agent flags. With ``kanban.cfg [providers] available =
claude``, ``--add-codex-agents`` and ``--add-codex-skills`` deploy codex with
a notice naming it, the same notice upgrade.sh prints. ``deploy_record_unedited``, the
helper an upgrade uses to prove a staged copy is the one last shipped, and
``deploy_record_skills``, which lists the skills a record names, are covered
in isolation.

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

import hashlib
import os
import pathlib
import subprocess
import textwrap

import pytest

from pgai_agent_kanban.lib.skills_install import read_shipped_manifest
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
    names = [ln.split("#", 1)[0].strip() for ln in lines]
    return len([n for n in names if n])


_EXPECTED_WRAPPER_COUNT = _expected_wrapper_count()

# Default for the run helpers' ``claude_config_dir``: point CLAUDE_CONFIG_DIR
# at ``<home>/.claude``, the layout most tests here assert on.
_CLAUDE_CONFIG_IN_HOME = object()

# Default provider flags for the run helpers.
_BOTH_AGENT_FLAGS = ("--add-claude-agents", "--add-codex-agents")


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
    extra_args: tuple[str, ...] = (),
    provider_flags: tuple[str, ...] = _BOTH_AGENT_FLAGS,
) -> subprocess.CompletedProcess:
    """Run the real install.sh with *provider_flags* (default: both
    provider-agent flags).

    Uses --force --no-system-cron so the run is non-interactive and never
    touches the host crontab; --stamp-version avoids a git resolution call.
    *extra_args* are appended to the command line.
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
            *provider_flags,
            "--stamp-version",
            "v0.0.0-sandbox-test",
            *extra_args,
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
    provider_flags: tuple[str, ...] = _BOTH_AGENT_FLAGS,
) -> subprocess.CompletedProcess:
    """Run the real upgrade.sh phase-2 body directly against kanban_root
    with *provider_flags* (default: both provider-agent flags).

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
            *provider_flags,
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
    manifest_names = [ln.split("#", 1)[0].strip() for ln in _REAL_MANIFEST.read_text(encoding="utf-8").splitlines()]
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

    manifest_names = [ln.split("#", 1)[0].strip() for ln in _REAL_MANIFEST.read_text(encoding="utf-8").splitlines()]
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


def _run_ensure_provider_agents_dir(agents_dir: pathlib.Path, dry_run: str = "false") -> subprocess.CompletedProcess:
    """Source subagent_deploy.sh in isolation and call the function directly.

    Supplies minimal info()/ok()/err() stand-ins matching install.sh's and
    upgrade.sh's real signatures (info/ok to stdout, err to stderr) so the
    function's narration can be asserted without running a full script.
    """
    script = textwrap.dedent(f"""\
        set -uo pipefail
        info() {{ echo "[test] $*"; }}
        ok()   {{ echo "[test] $*"; }}
        err()  {{ echo "[test] $*" >&2; }}
        source "{_REAL_SUBAGENT_DEPLOY_SH}"
        ensure_provider_agents_dir "{agents_dir}" "{dry_run}"
        echo "RC=$?"
        """)
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
# Manifest completeness — every shipped wrapper; OVERWATCH ships none
# ---------------------------------------------------------------------------

_REAL_SUBAGENTS_DIR = _REPO_ROOT / "subagents"


def _manifest_names() -> list[str]:
    """Non-comment, non-blank names listed in the real subagent manifest."""
    names = [ln.split("#", 1)[0].strip() for ln in _REAL_MANIFEST.read_text(encoding="utf-8").splitlines()]
    return [n for n in names if n]


def _installed_subagents_state(kanban_root: pathlib.Path) -> list[str]:
    """Names recorded in the root's .installed-subagents state file."""
    state = kanban_root / ".installed-subagents"
    assert state.is_file(), f".installed-subagents was not written under {kanban_root}"
    names = [ln.split("#", 1)[0].strip() for ln in state.read_text(encoding="utf-8").splitlines()]
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
    assert not missing, f"wrappers shipped under {_REAL_SUBAGENTS_DIR} but absent from " f"{_REAL_MANIFEST}: {missing}"
    # OVERWATCH dispatches no model, so it ships no wrapper.
    assert "overwatch" not in listed, f"overwatch must not be listed in {_REAL_MANIFEST}"
    assert not (_REAL_SUBAGENTS_DIR / "overwatch.md").exists()


@pytest.mark.skipif(not _REAL_INSTALL_SH.exists(), reason="Real install.sh not found; skipping.")
def test_install_removes_a_previously_installed_overwatch_wrapper(tmp_path: pathlib.Path) -> None:
    """An install that earlier deployed agents/overwatch.md removes it.

    The wrapper is no longer in the manifest, so install.sh's state-file
    cleanup deletes the copy it recorded in .installed-subagents and stops
    tracking it.
    """
    home = tmp_path / "home"
    home.mkdir()
    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir()
    (kanban_root / ".installed-subagents").write_text("coder\noverwatch\n", encoding="utf-8")
    stale = home / ".claude" / "agents" / "overwatch.md"
    stale.parent.mkdir(parents=True)
    stale.write_text("---\nname: overwatch\n---\n", encoding="utf-8")

    result = _run_install(tmp_path, home=home, kanban_root=kanban_root)
    output = _combined_output(result)
    assert result.returncode == 0, f"install.sh failed:\n{output}"

    assert not stale.exists(), f"the previously installed overwatch wrapper was not removed:\n{output}"
    assert "overwatch" not in _installed_subagents_state(kanban_root), output


# ---------------------------------------------------------------------------
# Claude wrapper destination — CLAUDE_CONFIG_DIR set or unset
# ---------------------------------------------------------------------------

_ENTRY_POINTS = {
    "install": (_REAL_INSTALL_SH, _run_install),
    "upgrade": (_REAL_UPGRADE_SH, _run_upgrade_phase2),
}


def _assert_every_manifest_wrapper_in(agents_dir: pathlib.Path, output: str) -> None:
    for name in _manifest_names():
        assert (agents_dir / f"{name}.md").is_file(), f"missing wrapper {name}.md under {agents_dir}:\n{output}"


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
        f"nothing may be written under {home / '.claude'} when " f"CLAUDE_CONFIG_DIR is set:\n{output}"
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


def _run_provider_agents_dir(provider: str, *, home: pathlib.Path, claude_config_dir: str | None) -> str:
    """Source subagent_deploy.sh in isolation and print provider_agents_dir."""
    return _run_provider_resolver(f'provider_agents_dir "{provider}"', home=home, claude_config_dir=claude_config_dir)


def _run_provider_resolver(call: str, *, home: pathlib.Path, claude_config_dir: str | None) -> str:
    """Source subagent_deploy.sh in isolation and print what *call* prints."""
    env = dict(os.environ)
    env["HOME"] = str(home)
    env.pop("CLAUDE_CONFIG_DIR", None)
    if claude_config_dir is not None:
        env["CLAUDE_CONFIG_DIR"] = claude_config_dir
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'set -u; source "{_REAL_SUBAGENT_DEPLOY_SH}"; {call}',
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


@pytest.mark.skipif(
    not _REAL_SUBAGENT_DEPLOY_SH.exists(),
    reason=f"subagent_deploy.sh not found at {_REAL_SUBAGENT_DEPLOY_SH}; skipping.",
)
@pytest.mark.parametrize(
    "call,claude_config_dir,expected",
    [
        ("provider_skills_dir claude", "/cfg/claude", "/cfg/claude/skills"),
        ("provider_skills_dir claude", None, "{home}/.claude/skills"),
        ("provider_skills_dir claude", "", "{home}/.claude/skills"),
        ("provider_skills_dir codex", "/cfg/claude", "{home}/.codex/skills"),
        ("provider_skills_dir codex", None, "{home}/.codex/skills"),
        ("provider_home_dir claude agents", "/cfg/claude", "/cfg/claude/agents"),
        ("provider_home_dir codex skills", None, "{home}/.codex/skills"),
    ],
)
def test_provider_skills_dir_resolution(
    tmp_path: pathlib.Path,
    call: str,
    claude_config_dir: str | None,
    expected: str,
) -> None:
    """Skill homes follow the agents rule: claude honours a non-empty
    CLAUDE_CONFIG_DIR, other providers use ``$HOME/.<provider>``."""
    home = tmp_path / "home"
    got = _run_provider_resolver(call, home=home, claude_config_dir=claude_config_dir)
    assert got == expected.format(home=home), got


@pytest.mark.skipif(
    not _REAL_SUBAGENT_DEPLOY_SH.exists(),
    reason=f"subagent_deploy.sh not found at {_REAL_SUBAGENT_DEPLOY_SH}; skipping.",
)
def test_provider_home_dir_ignores_a_pp_run_ops_the_caller_defined(tmp_path: pathlib.Path) -> None:
    """The rule is read from subagent_deploy.sh's own tree, not through a
    pp_run_ops the sourcing script defined first."""
    home = tmp_path / "home"
    got = _run_provider_resolver(
        "pp_run_ops() { printf /elsewhere; }; provider_skills_dir codex",
        home=home,
        claude_config_dir=None,
    )
    assert got == f"{home}/.codex/skills", got


@pytest.mark.skipif(
    not _REAL_SUBAGENT_DEPLOY_SH.exists(),
    reason=f"subagent_deploy.sh not found at {_REAL_SUBAGENT_DEPLOY_SH}; skipping.",
)
def test_provider_home_dir_fails_loud_with_no_path_when_the_rule_cannot_run(tmp_path: pathlib.Path) -> None:
    """With python3 failing, a set -e caller stops at the lookup: nothing on
    stdout, one line on stderr naming the provider and kind, non-zero exit --
    an empty directory never reaches a deploy step."""
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    stub = stub_bin / "python3"
    stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    stub.chmod(0o755)
    env = build_scrubbed_env(home_dir=tmp_path / "home", extra_path_dirs=[stub_bin])
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'set -euo pipefail; source "{_REAL_SUBAGENT_DEPLOY_SH}"; '
            'dir="$(provider_agents_dir claude)"; echo "deployed to [$dir]"',
        ],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )
    assert result.returncode != 0, result.stdout
    assert result.stdout == ""
    assert "could not resolve the agents directory for claude" in result.stderr, result.stderr


# ---------------------------------------------------------------------------
# install.sh — skill staging deposit and the shipped-skill record
# ---------------------------------------------------------------------------

_REAL_SKILLS_DIR = _REPO_ROOT / "skills"
_SKILL_RECORD = ".installed-skills"
_SANDBOX_INSTALL_ARGS = ("--wake-tier", "none")


def _shipped_skill_files() -> list[str]:
    """Repository-relative paths of every file of every shipped skill."""
    # Intentional pattern-scan: every file of every skills/<provider>/<name>/
    # holding a SKILL.md is shipped, so every one must be staged and recorded.
    return sorted(
        f.relative_to(_REPO_ROOT).as_posix()
        for skill_md in _REAL_SKILLS_DIR.glob("*/*/SKILL.md")
        for f in skill_md.parent.rglob("*")
        if f.is_file()
    )


def _skill_record(kanban_root: pathlib.Path) -> dict[str, str]:
    """``{path: sha256}`` from the root's .installed-skills record."""
    record = kanban_root / _SKILL_RECORD
    assert record.is_file(), f"{_SKILL_RECORD} was not written under {kanban_root}"
    entries = {}
    for line in record.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        digest, path = line.split(None, 1)
        entries[path] = digest
    return entries


def _tree_bytes(directory: pathlib.Path) -> dict[str, bytes]:
    return {p.relative_to(directory).as_posix(): p.read_bytes() for p in sorted(directory.rglob("*")) if p.is_file()}


@pytest.mark.skipif(not _REAL_INSTALL_SH.exists(), reason="Real install.sh not found; skipping.")
def test_install_stages_every_shipped_skill_and_records_its_digest(tmp_path: pathlib.Path) -> None:
    """A fresh install deposits both shipped skills byte-identical into the
    staging directory, records every shipped file with its SHA-256 digest,
    and writes nothing to a provider skill home."""
    home = tmp_path / "home"
    home.mkdir()
    kanban_root = tmp_path / "kanban_root"

    result = _run_install(tmp_path, home=home, kanban_root=kanban_root, extra_args=_SANDBOX_INSTALL_ARGS)
    output = _combined_output(result)
    assert result.returncode == 0, f"install.sh failed:\n{output}"

    for provider in ("claude", "codex"):
        rel = f"skills/{provider}/pgai-agent-kanban/SKILL.md"
        assert (kanban_root / rel).read_bytes() == (_REPO_ROOT / rel).read_bytes(), output
    shipped = _shipped_skill_files()
    for rel in shipped:
        assert (kanban_root / rel).read_bytes() == (
            _REPO_ROOT / rel
        ).read_bytes(), f"{rel} not staged byte-identical:\n{output}"

    assert _skill_record(kanban_root) == {
        rel: hashlib.sha256((_REPO_ROOT / rel).read_bytes()).hexdigest() for rel in shipped
    }
    assert read_shipped_manifest(kanban_root / _SKILL_RECORD) == {
        "claude": frozenset({"pgai-agent-kanban"}),
        "codex": frozenset({"pgai-agent-kanban"}),
    }
    assert not (home / ".claude" / "skills").exists(), output
    assert not (home / ".codex" / "skills").exists(), output


@pytest.mark.skipif(not _REAL_INSTALL_SH.exists(), reason="Real install.sh not found; skipping.")
def test_reinstall_leaves_an_operator_added_skill_untouched(tmp_path: pathlib.Path) -> None:
    """A re-install with --force over a root holding an operator-added skill
    leaves every file of that skill byte-identical and in place, and the
    record names only shipped skills."""
    home = tmp_path / "home"
    home.mkdir()
    kanban_root = tmp_path / "kanban_root"
    first = _run_install(tmp_path, home=home, kanban_root=kanban_root, extra_args=_SANDBOX_INSTALL_ARGS)
    assert first.returncode == 0, f"first install.sh failed:\n{_combined_output(first)}"

    operator_skill = kanban_root / "skills" / "claude" / "my-skill"
    (operator_skill / "scripts").mkdir(parents=True)
    (operator_skill / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: An operator's own skill.\n---\nBody.\n", encoding="utf-8"
    )
    (operator_skill / "scripts" / "run.sh").write_text("echo mine\n", encoding="utf-8")
    before = _tree_bytes(operator_skill)

    result = _run_install(tmp_path, home=home, kanban_root=kanban_root, extra_args=_SANDBOX_INSTALL_ARGS)
    output = _combined_output(result)
    assert result.returncode == 0, f"re-install failed:\n{output}"

    assert _tree_bytes(operator_skill) == before, output
    assert "my-skill" not in output, output
    assert not any("my-skill" in path for path in _skill_record(kanban_root)), output
    assert sorted(_skill_record(kanban_root)) == _shipped_skill_files()


@pytest.mark.skipif(not _REAL_INSTALL_SH.exists(), reason="Real install.sh not found; skipping.")
def test_install_dry_run_stages_nothing_and_writes_no_record(tmp_path: pathlib.Path) -> None:
    """--dry-run reports the skill staging without making the staging
    directory or writing the record."""
    home = tmp_path / "home"
    home.mkdir()
    kanban_root = tmp_path / "kanban_root"

    result = _run_install(
        tmp_path, home=home, kanban_root=kanban_root, extra_args=(*_SANDBOX_INSTALL_ARGS, "--dry-run")
    )
    output = _combined_output(result)
    assert result.returncode == 0, f"install.sh --dry-run failed:\n{output}"

    assert "[dry-run] would copy: skills/claude/pgai-agent-kanban/SKILL.md" in output, output
    assert not (kanban_root / "skills").exists(), output
    assert not (kanban_root / _SKILL_RECORD).exists(), output


@pytest.mark.skipif(not _REAL_INSTALL_SH.exists(), reason="Real install.sh not found; skipping.")
def test_install_claude_skill_flag_installs_the_shipped_skill_into_the_claude_home(tmp_path: pathlib.Path) -> None:
    """--add-claude-skills installs every staged claude skill into
    $CLAUDE_CONFIG_DIR/skills byte-identical to the shipped copy, and writes
    nothing to the codex skill home, whose flag was not passed."""
    home = tmp_path / "home"
    home.mkdir()
    claude_config_dir = tmp_path / "claude_config"
    kanban_root = tmp_path / "kanban_root"

    result = _run_install(
        tmp_path,
        home=home,
        kanban_root=kanban_root,
        claude_config_dir=claude_config_dir,
        extra_args=_SANDBOX_INSTALL_ARGS,
        provider_flags=("--add-claude-skills",),
    )
    output = _combined_output(result)
    assert result.returncode == 0, f"install.sh failed:\n{output}"

    rel = "pgai-agent-kanban/SKILL.md"
    installed = claude_config_dir / "skills" / rel
    assert installed.read_bytes() == (_REAL_SKILLS_DIR / "claude" / rel).read_bytes(), output
    assert (kanban_root / "skills" / "claude" / rel).is_file(), output
    assert "Provider skills: install complete for claude" in output, output
    assert "Provider skills: skipping codex (--add-codex-skills not set)" in output, output
    assert not (home / ".codex").exists(), output
    assert not (home / ".claude").exists(), output
    assert not (claude_config_dir / "agents").exists(), "no agent flag was passed:\n" + output


_NOT_LISTED_NOTICE = "is not listed in kanban.cfg [providers] available (claude)"


def _not_listed_notices(output: str, label: str) -> list[str]:
    """Each not-listed notice line, from its step label on (the script's
    own [install] / [upgrade] tag dropped)."""
    return [ln[ln.index(label) :].rstrip() for ln in output.splitlines() if _NOT_LISTED_NOTICE in ln]


@pytest.mark.skipif(not _REAL_INSTALL_SH.exists(), reason="Real install.sh not found; skipping.")
@pytest.mark.skipif(not _REAL_UPGRADE_SH.exists(), reason="Real upgrade.sh not found; skipping.")
@pytest.mark.parametrize(
    ("flag", "label", "codex_home"),
    [
        ("--add-codex-agents", "Provider-agent wrappers", ".codex/agents"),
        ("--add-codex-skills", "Provider skills", ".codex/skills"),
    ],
)
def test_install_codex_flag_missing_from_available_providers_deploys_codex_with_upgrades_notice(
    tmp_path: pathlib.Path, flag: str, label: str, codex_home: str
) -> None:
    """With kanban.cfg [providers] available = claude, a codex flag deploys
    codex anyway and prints one notice naming codex and the flag, the same
    notice upgrade.sh prints for the same configuration and flag."""
    results = {}
    for entry_point, run in (("install", _run_install), ("upgrade", _run_upgrade_phase2)):
        sandbox = tmp_path / entry_point
        home = sandbox / "home"
        home.mkdir(parents=True)
        kanban_root = sandbox / "kanban_root"
        kanban_root.mkdir()
        (kanban_root / "kanban.cfg").write_text("[providers]\navailable = claude\n", encoding="utf-8")
        result = run(sandbox, home=home, kanban_root=kanban_root, provider_flags=(flag,))
        output = _combined_output(result)
        assert result.returncode == 0, f"{entry_point} failed:\n{output}"
        assert any((home / codex_home).iterdir()), f"{entry_point} did not deploy codex:\n{output}"
        results[entry_point] = _not_listed_notices(output, label)

    expected = f"{label}: codex {_NOT_LISTED_NOTICE}; deploying it anyway because {flag} was passed"
    assert results["install"] == [expected], results
    assert results["upgrade"] == results["install"], results


@pytest.mark.skipif(not _REAL_INSTALL_SH.exists(), reason="Real install.sh not found; skipping.")
def test_install_help_lists_the_skill_flags_beside_the_agent_flags(tmp_path: pathlib.Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    env = _sandbox_env(
        home=home,
        kanban_root=tmp_path / "kanban_root",
        temp_dir=tmp_path / "sandbox_tmp",
        bin_dir=tmp_path / "stub_bin",
        claude_config_dir=None,
    )
    result = subprocess.run(
        ["bash", str(_REAL_INSTALL_SH), "--help"], capture_output=True, text=True, timeout=_TIMEOUT, env=env
    )
    assert result.returncode == 0, _combined_output(result)
    options = [ln.split()[0] for ln in result.stdout.splitlines() if ln.startswith("  --")]
    agent_flags = options.index("--add-claude-agents")
    assert options[agent_flags : agent_flags + 4] == [
        "--add-claude-agents",
        "--add-codex-agents",
        "--add-claude-skills",
        "--add-codex-skills",
    ], result.stdout
    assert not (tmp_path / "kanban_root").exists()


def _run_record_unedited(state: pathlib.Path, base: pathlib.Path, path: str) -> subprocess.CompletedProcess:
    """Source subagent_deploy.sh and call deploy_record_unedited."""
    script = textwrap.dedent(f"""\
        set -uo pipefail
        source "{_REAL_SUBAGENT_DEPLOY_SH}"
        deploy_record_unedited "{state}" "{base}" "{path}"
        echo "RC=$?"
        """)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=10)


def _write_record(state: pathlib.Path, source: pathlib.Path, path: str) -> None:
    """Record *path* under *source* with deploy_record_write."""
    script = textwrap.dedent(f"""\
        set -euo pipefail
        info() {{ echo "[test] $*"; }}
        ok()   {{ echo "[test] $*"; }}
        err()  {{ echo "[test] $*" >&2; }}
        source "{_REAL_SUBAGENT_DEPLOY_SH}"
        deploy_record_write "{state}" "{source}" "test record" "{path}"
        """)
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def _leave_unchanged(skill: pathlib.Path) -> None:
    pass


def _edit_nested_file(skill: pathlib.Path) -> None:
    (skill / "scripts" / "run.sh").write_text("echo edited\n", encoding="utf-8")


def _add_operator_file(skill: pathlib.Path) -> None:
    (skill / "notes.md").write_text("mine\n", encoding="utf-8")


def _delete_skill_file(skill: pathlib.Path) -> None:
    (skill / "SKILL.md").unlink()


@pytest.mark.skipif(
    not _REAL_SUBAGENT_DEPLOY_SH.exists(),
    reason=f"subagent_deploy.sh not found at {_REAL_SUBAGENT_DEPLOY_SH}; skipping.",
)
@pytest.mark.parametrize(
    "mutate,expected_rc",
    [
        (_leave_unchanged, 0),
        (_edit_nested_file, 1),
        (_add_operator_file, 1),
        (_delete_skill_file, 1),
    ],
)
def test_record_proves_only_an_unedited_staged_copy(tmp_path: pathlib.Path, mutate, expected_rc: int) -> None:
    """deploy_record_unedited succeeds only for a staged copy whose files are
    exactly the recorded set with the recorded digests, and never writes."""
    rel = "skills/claude/retired"
    source_skill = tmp_path / "repo" / rel
    (source_skill / "scripts").mkdir(parents=True)
    (source_skill / "SKILL.md").write_text("---\nname: retired\n---\n", encoding="utf-8")
    (source_skill / "scripts" / "run.sh").write_text("echo shipped\n", encoding="utf-8")
    state = tmp_path / _SKILL_RECORD
    _write_record(state, tmp_path / "repo", rel)

    staged_skill = tmp_path / "root" / rel
    staged_skill.parent.mkdir(parents=True)
    staged_skill.mkdir()
    for name, data in _tree_bytes(source_skill).items():
        (staged_skill / name).parent.mkdir(parents=True, exist_ok=True)
        (staged_skill / name).write_bytes(data)
    mutate(staged_skill)
    before = _tree_bytes(staged_skill)

    result = _run_record_unedited(state, tmp_path / "root", rel)

    assert result.stdout.splitlines() == [f"RC={expected_rc}"], result.stdout + result.stderr
    assert _tree_bytes(staged_skill) == before


@pytest.mark.skipif(
    not _REAL_SUBAGENT_DEPLOY_SH.exists(),
    reason=f"subagent_deploy.sh not found at {_REAL_SUBAGENT_DEPLOY_SH}; skipping.",
)
def test_record_proves_nothing_for_an_unrecorded_skill(tmp_path: pathlib.Path) -> None:
    """A skill the record does not name is never provably unedited."""
    operator_skill = tmp_path / "root" / "skills" / "claude" / "my-skill"
    operator_skill.mkdir(parents=True)
    (operator_skill / "SKILL.md").write_text("mine\n", encoding="utf-8")
    state = tmp_path / _SKILL_RECORD
    state.write_text("# header\n", encoding="utf-8")

    result = _run_record_unedited(state, tmp_path / "root", "skills/claude/my-skill")

    assert result.stdout.splitlines() == ["RC=1"], result.stdout + result.stderr


@pytest.mark.skipif(
    not _REAL_SUBAGENT_DEPLOY_SH.exists(),
    reason=f"subagent_deploy.sh not found at {_REAL_SUBAGENT_DEPLOY_SH}; skipping.",
)
def test_record_skills_lists_each_recorded_skill_once_and_ignores_other_shapes(
    tmp_path: pathlib.Path,
) -> None:
    """deploy_record_skills prints each <provider>/<name> a record names once,
    sorted, and never a path that is not a file inside a skill directory or
    whose provider or name is hidden; an absent record prints nothing."""
    state = tmp_path / _SKILL_RECORD
    digest = "0" * 64
    state.write_text(
        "# header\n"
        "\n"
        f"{digest}  skills/codex/beta/SKILL.md\n"
        f"{digest}  skills/claude/alpha/SKILL.md\n"
        f"{digest}  skills/claude/alpha/scripts/run.sh\n"
        f"{digest}  skills/claude/SKILL.md\n"
        f"{digest}  skills/claude/../escape/SKILL.md\n"
        f"{digest}  skills/claude/.hidden/SKILL.md\n"
        f"{digest}  skills/.git/name/SKILL.md\n"
        f"{digest}  docs/guide.md\n",
        encoding="utf-8",
    )
    script = textwrap.dedent(f"""\
        set -euo pipefail
        source "{_REAL_SUBAGENT_DEPLOY_SH}"
        deploy_record_skills "{state}"
        echo "--"
        deploy_record_skills "{tmp_path / 'absent'}"
        echo "RC=$?"
        """)
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=10)

    assert result.stdout.splitlines() == ["claude/alpha", "codex/beta", "--", "RC=0"], result.stdout + result.stderr
