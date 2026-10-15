"""
test_upgrade_wrapper_drift.py
=============================
Behavioral tests for the read-only deployed-wrapper comparison: the
``subagent_wrapper_drift`` helper in team/scripts/lib/subagent_deploy.sh and
its use on upgrade.sh's protected path (no --add-claude-agents /
--add-codex-agents flag).

WHAT IS TESTED
--------------
1. ``subagent_wrapper_drift`` in isolation: prints ``missing <name>`` /
   ``differs <name>`` for out-of-date wrappers only, prints nothing when all
   match, returns 1 without output for an absent agents directory, and
   changes nothing on disk.
2. upgrade.sh phase 2 without the agent flags:
   - one deployed claude wrapper edited and one deleted: exactly those two
     are warned by provider and file name, each with the runnable refresh
     command, and the agents directory's file list and bytes are unchanged;
   - every wrapper matching: no drift warning, and the summary says the
     wrappers are current instead of the generic refresh hint;
   - a provider whose agents directory is absent is neither compared nor
     created;
   - codex drift names the codex refresh flag.
3. upgrade.sh phase 2 with --add-claude-agents over a drifted directory:
   wrappers are refreshed to the shipped bytes, ``.installed-subagents`` is
   written, and no drift warning is printed for claude.

SANDBOX DESIGN
--------------
Every upgrade run uses a throwaway kanban root under ``tmp_path`` with all
five live-install variables set or cleared together (KANBAN_ROOT,
PGAI_AGENT_KANBAN_ROOT_PATH and PGAI_AGENT_KANBAN_TEMP_DIR set into the
sandbox; PGAI_TASKS_DIR and PGAI_PROJECT_ROOT absent), a scratch HOME, no
CLAUDE_CONFIG_DIR, and an environment built by ``build_scrubbed_env`` rather
than inherited. upgrade.sh's phase-2 body is invoked directly, as
test_subagent_deploy_dir_creation.py does, from a working directory outside
the repository.
"""

from __future__ import annotations

import hashlib
import pathlib
import subprocess
import textwrap

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_REPO_ROOT = _TEAM_DIR.parent

_REAL_UPGRADE_SH = _TEAM_DIR / "scripts" / "upgrade.sh"
_REAL_SUBAGENT_DEPLOY_SH = _TEAM_DIR / "scripts" / "lib" / "subagent_deploy.sh"
_REAL_SUBAGENTS_DIR = _REPO_ROOT / "subagents"
_REAL_MANIFEST = _REAL_SUBAGENTS_DIR / "MANIFEST.txt"

_TIMEOUT = 120
_DRIFT_WARNING = "Agent wrapper out of date:"
_GENERIC_SUMMARY_LINE = "agent wrappers (pass --add-claude-agents / --add-codex-agents to refresh)"


def _manifest_names() -> list[str]:
    """Non-comment, non-blank names listed in the real subagent manifest."""
    names = [
        ln.split("#", 1)[0].strip()
        for ln in _REAL_MANIFEST.read_text(encoding="utf-8").splitlines()
    ]
    return [n for n in names if n]


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _snapshot(directory: pathlib.Path) -> dict[str, str]:
    """Relative path -> sha256 of every file under *directory*."""
    return {
        str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def _deploy_all_wrappers(agents_dir: pathlib.Path) -> None:
    """Populate *agents_dir* with byte-identical copies of every manifest wrapper."""
    agents_dir.mkdir(parents=True, exist_ok=True)
    for name in _manifest_names():
        src = _REAL_SUBAGENTS_DIR / f"{name}.md"
        (agents_dir / f"{name}.md").write_bytes(src.read_bytes())


def _write_stub_provider_binaries(bin_dir: pathlib.Path) -> None:
    """No-op ``claude`` / ``codex`` executables so the opt-in CLI gate passes."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in ("claude", "codex"):
        stub = bin_dir / name
        stub.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)


def _run_upgrade_phase2(
    tmp_path: pathlib.Path, *extra_args: str
) -> tuple[subprocess.CompletedProcess, pathlib.Path]:
    """Run the real upgrade.sh phase-2 body against a sandboxed root.

    Returns the completed process and the sandbox kanban root.
    """
    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir(exist_ok=True)
    sandbox_temp = tmp_path / "sandbox_tmp"
    sandbox_temp.mkdir(exist_ok=True)
    foreign_cwd = tmp_path / "cwd"
    foreign_cwd.mkdir(exist_ok=True)
    bin_dir = tmp_path / "stub_bin"
    _write_stub_provider_binaries(bin_dir)

    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_path_dirs=[bin_dir],
        extra_env={
            "KANBAN_ROOT": str(kanban_root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(sandbox_temp),
        },
    )
    result = subprocess.run(
        [
            "bash",
            str(_REAL_UPGRADE_SH),
            "--phase2",
            "--phase2-protocol",
            "1",
            "--kanban-root",
            str(kanban_root),
            "--force",
            "--stamp-version",
            "v0.0.1-sandbox-test",
            *extra_args,
        ],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        env=env,
        cwd=str(foreign_cwd),
    )
    return result, kanban_root


def _drift_lines(output: str) -> list[str]:
    return [ln for ln in output.splitlines() if _DRIFT_WARNING in ln]


# ---------------------------------------------------------------------------
# subagent_wrapper_drift — isolated helper coverage
# ---------------------------------------------------------------------------


def _run_drift_helper(
    source_dir: pathlib.Path, agents_dir: pathlib.Path, *names: str
) -> subprocess.CompletedProcess:
    quoted = " ".join(f'"{n}"' for n in names)
    script = textwrap.dedent(
        f"""\
        set -euo pipefail
        source "{_REAL_SUBAGENT_DEPLOY_SH}"
        rc=0
        subagent_wrapper_drift "{source_dir}" "{agents_dir}" {quoted} || rc=$?
        echo "RC=$rc"
        """
    )
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=str(source_dir.parent),
    )


def test_drift_helper_lists_missing_and_differing_wrappers_only(
    tmp_path: pathlib.Path,
) -> None:
    source_dir = tmp_path / "subagents"
    agents_dir = tmp_path / "agents"
    source_dir.mkdir()
    agents_dir.mkdir()
    for name in ("alpha", "beta", "gamma"):
        (source_dir / f"{name}.md").write_text(f"{name}\n", encoding="utf-8")
    (agents_dir / "alpha.md").write_text("alpha\n", encoding="utf-8")
    (agents_dir / "beta.md").write_text("beta edited\n", encoding="utf-8")
    before = _snapshot(agents_dir)

    result = _run_drift_helper(source_dir, agents_dir, "alpha", "beta", "gamma")

    assert result.stdout.splitlines() == ["differs beta", "missing gamma", "RC=0"], _diagnostic(result)
    assert _snapshot(agents_dir) == before


def test_drift_helper_prints_nothing_when_every_wrapper_matches(
    tmp_path: pathlib.Path,
) -> None:
    source_dir = tmp_path / "subagents"
    agents_dir = tmp_path / "agents"
    source_dir.mkdir()
    agents_dir.mkdir()
    for name in ("alpha", "beta"):
        (source_dir / f"{name}.md").write_text(f"{name}\n", encoding="utf-8")
        (agents_dir / f"{name}.md").write_text(f"{name}\n", encoding="utf-8")

    result = _run_drift_helper(source_dir, agents_dir, "alpha", "beta")

    assert result.stdout.splitlines() == ["RC=0"], _diagnostic(result)


def test_drift_helper_skips_name_without_shipped_source(
    tmp_path: pathlib.Path,
) -> None:
    source_dir = tmp_path / "subagents"
    agents_dir = tmp_path / "agents"
    source_dir.mkdir()
    agents_dir.mkdir()

    result = _run_drift_helper(source_dir, agents_dir, "unshipped")

    assert result.stdout.splitlines() == ["RC=0"], _diagnostic(result)


def test_drift_helper_returns_1_and_creates_nothing_for_absent_agents_dir(
    tmp_path: pathlib.Path,
) -> None:
    source_dir = tmp_path / "subagents"
    source_dir.mkdir()
    (source_dir / "alpha.md").write_text("alpha\n", encoding="utf-8")
    agents_dir = tmp_path / "absent" / "agents"

    result = _run_drift_helper(source_dir, agents_dir, "alpha")

    assert result.stdout.splitlines() == ["RC=1"], _diagnostic(result)
    assert not (tmp_path / "absent").exists()


# ---------------------------------------------------------------------------
# upgrade.sh phase 2 — protected path (no agent flags)
# ---------------------------------------------------------------------------

_requires_upgrade_sh = pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.",
)


@_requires_upgrade_sh
def test_upgrade_without_flags_warns_for_exactly_the_edited_and_deleted_wrappers(
    tmp_path: pathlib.Path,
) -> None:
    names = _manifest_names()
    assert len(names) >= 2, f"manifest lists fewer than two wrappers: {names}"
    edited, deleted = names[0], names[1]
    claude_dir = tmp_path / "home" / ".claude" / "agents"
    _deploy_all_wrappers(claude_dir)
    with (claude_dir / f"{edited}.md").open("a", encoding="utf-8") as fh:
        fh.write("\nlocal edit\n")
    (claude_dir / f"{deleted}.md").unlink()
    before = _snapshot(claude_dir)

    result, kanban_root = _run_upgrade_phase2(tmp_path)
    output = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == 0, _diagnostic(result)
    warnings = _drift_lines(result.stdout)
    assert len(warnings) == 2, _diagnostic(result)
    refresh = f"refresh: {kanban_root}/scripts/upgrade.sh --add-claude-agents"
    for name in (edited, deleted):
        matching = [w for w in warnings if f"claude/{name}.md" in w]
        assert len(matching) == 1, _diagnostic(result)
        assert matching[0].rstrip().endswith(refresh), _diagnostic(result)
    assert (kanban_root / "scripts" / "upgrade.sh").is_file(), (
        "the refresh command must name an upgrade.sh that exists after the upgrade"
    )

    assert _snapshot(claude_dir) == before, "protected agents directory was modified"
    assert not (kanban_root / ".installed-subagents").exists(), output
    assert _GENERIC_SUMMARY_LINE not in output, output
    # The summary lists drifted wrappers in manifest order.
    assert f"agent wrappers, claude: 2 out of date (claude/{edited}.md, claude/{deleted}.md)" in output, (
        _diagnostic(result)
    )


@_requires_upgrade_sh
def test_upgrade_without_flags_reports_wrappers_current_when_all_match(
    tmp_path: pathlib.Path,
) -> None:
    claude_dir = tmp_path / "home" / ".claude" / "agents"
    codex_dir = tmp_path / "home" / ".codex" / "agents"
    _deploy_all_wrappers(claude_dir)
    _deploy_all_wrappers(codex_dir)
    before = {"claude": _snapshot(claude_dir), "codex": _snapshot(codex_dir)}

    result, _ = _run_upgrade_phase2(tmp_path)
    output = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == 0, _diagnostic(result)
    assert _drift_lines(output) == [], _diagnostic(result)
    assert _GENERIC_SUMMARY_LINE not in output, _diagnostic(result)
    count = len(_manifest_names())
    for provider in ("claude", "codex"):
        assert (
            f"agent wrappers, {provider}: current ({count} wrappers match the shipped copies)"
            in output
        ), _diagnostic(result)
    assert {"claude": _snapshot(claude_dir), "codex": _snapshot(codex_dir)} == before


@_requires_upgrade_sh
def test_upgrade_without_flags_neither_compares_nor_creates_absent_agents_dirs(
    tmp_path: pathlib.Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()

    result, _ = _run_upgrade_phase2(tmp_path)
    output = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == 0, _diagnostic(result)
    assert not (home / ".claude").exists(), _diagnostic(result)
    assert not (home / ".codex").exists(), _diagnostic(result)
    assert _drift_lines(output) == [], _diagnostic(result)
    for provider in ("claude", "codex"):
        assert f"agent wrappers, {provider}: not compared (no agents directory" in output, (
            _diagnostic(result)
        )


@_requires_upgrade_sh
def test_upgrade_without_flags_names_codex_refresh_flag_for_codex_drift(
    tmp_path: pathlib.Path,
) -> None:
    name = _manifest_names()[0]
    codex_dir = tmp_path / "home" / ".codex" / "agents"
    _deploy_all_wrappers(codex_dir)
    (codex_dir / f"{name}.md").write_text("older copy\n", encoding="utf-8")
    before = _snapshot(codex_dir)

    result, kanban_root = _run_upgrade_phase2(tmp_path)

    assert result.returncode == 0, _diagnostic(result)
    warnings = _drift_lines(result.stdout)
    assert len(warnings) == 1, _diagnostic(result)
    assert f"codex/{name}.md" in warnings[0], _diagnostic(result)
    assert warnings[0].rstrip().endswith(
        f"refresh: {kanban_root}/scripts/upgrade.sh --add-codex-agents"
    ), _diagnostic(result)
    assert _snapshot(codex_dir) == before
    assert not (tmp_path / "home" / ".claude").exists(), _diagnostic(result)


# ---------------------------------------------------------------------------
# upgrade.sh phase 2 — opt-in refresh unchanged
# ---------------------------------------------------------------------------


@_requires_upgrade_sh
def test_upgrade_with_add_claude_agents_refreshes_drifted_wrappers_without_warning(
    tmp_path: pathlib.Path,
) -> None:
    names = _manifest_names()
    claude_dir = tmp_path / "home" / ".claude" / "agents"
    _deploy_all_wrappers(claude_dir)
    (claude_dir / f"{names[0]}.md").write_text("older copy\n", encoding="utf-8")
    (claude_dir / f"{names[1]}.md").unlink()

    result, kanban_root = _run_upgrade_phase2(tmp_path, "--add-claude-agents")
    output = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == 0, _diagnostic(result)
    assert [w for w in _drift_lines(output) if "claude/" in w] == [], _diagnostic(result)
    for name in names:
        deployed = claude_dir / f"{name}.md"
        assert deployed.read_bytes() == (_REAL_SUBAGENTS_DIR / f"{name}.md").read_bytes(), (
            f"{deployed} not refreshed\n{_diagnostic(result)}"
        )
    state = (kanban_root / ".installed-subagents").read_text(encoding="utf-8")
    recorded = [ln.strip() for ln in state.splitlines() if ln.strip() and not ln.startswith("#")]
    assert recorded == names, state
    assert "agent wrappers, claude: refreshed (--add-claude-agents)" in output, _diagnostic(result)
