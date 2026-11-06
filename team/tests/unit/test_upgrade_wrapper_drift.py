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
   changes nothing on disk. ``deploy_drift``, which it delegates to, also
   compares whole skill directories.
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
4. The provider skill homes, the same way: without --add-claude-skills /
   --add-codex-skills an edited and a missing shipped skill are warned by
   provider and name with the install-skills.sh refresh command, the summary
   carries one drift line per provider and neither home changes; with both
   flags (CLAUDE_CONFIG_DIR and HOME pointed into the sandbox, stub provider
   binaries on PATH) every shipped skill is installed into both homes; a
   provider whose binary is not on PATH is skipped with a notice.
5. ``deploy_tree_current``, the deployed-copy comparator behind
   ``deploy_drift`` and the upgrade deposit's directory-current check, in
   isolation: extra deployed files do not count, a missing or edited shipped
   file does, ``--exclude`` names are not compared, and nothing is written.
6. upgrade.sh with --add-claude-agents removes a wrapper .installed-subagents
   records but the manifest no longer lists (the shared obsolete-wrapper
   removal in subagent_deploy.sh), and leaves an unrecorded one.

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
import shutil
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
    names = [ln.split("#", 1)[0].strip() for ln in _REAL_MANIFEST.read_text(encoding="utf-8").splitlines()]
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


def _write_stub_provider_binaries(bin_dir: pathlib.Path, providers: tuple[str, ...] = ("claude", "codex")) -> None:
    """No-op provider executables so the opt-in CLI gate passes."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in providers:
        stub = bin_dir / name
        stub.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)


def _run_upgrade_phase2(
    tmp_path: pathlib.Path,
    *extra_args: str,
    extra_env: dict[str, str] | None = None,
    stub_providers: tuple[str, ...] = ("claude", "codex"),
) -> tuple[subprocess.CompletedProcess, pathlib.Path]:
    """Run the real upgrade.sh phase-2 body against a sandboxed root.

    *extra_env* adds variables (e.g. a sandboxed CLAUDE_CONFIG_DIR);
    *stub_providers* names the provider binaries put on PATH.
    Returns the completed process and the sandbox kanban root.
    """
    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir(exist_ok=True)
    sandbox_temp = tmp_path / "sandbox_tmp"
    sandbox_temp.mkdir(exist_ok=True)
    foreign_cwd = tmp_path / "cwd"
    foreign_cwd.mkdir(exist_ok=True)
    bin_dir = tmp_path / "stub_bin"
    _write_stub_provider_binaries(bin_dir, stub_providers)

    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_path_dirs=[bin_dir],
        extra_env={
            "KANBAN_ROOT": str(kanban_root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(sandbox_temp),
            **(extra_env or {}),
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
    source_dir: pathlib.Path, agents_dir: pathlib.Path, *names: str, call: str = "subagent_wrapper_drift"
) -> subprocess.CompletedProcess:
    quoted = " ".join(f'"{n}"' for n in names)
    script = textwrap.dedent(f"""\
        set -euo pipefail
        source "{_REAL_SUBAGENT_DEPLOY_SH}"
        rc=0
        {call} "{source_dir}" "{agents_dir}" {quoted} || rc=$?
        echo "RC=$rc"
        """)
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


def test_deploy_drift_compares_whole_skill_directories(
    tmp_path: pathlib.Path,
) -> None:
    """A skill directory differs when any shipped file under it is missing or
    edited in the deployed copy; a file only in the deployed copy is not a
    difference, and nothing on disk changes."""
    source_dir = tmp_path / "skills"
    home_dir = tmp_path / "home_skills"
    for name in ("alpha", "beta", "gamma"):
        (source_dir / name / "scripts").mkdir(parents=True)
        (source_dir / name / "SKILL.md").write_text(f"{name}\n", encoding="utf-8")
        (source_dir / name / "scripts" / "run.sh").write_text("echo run\n", encoding="utf-8")
    for name in ("alpha", "beta"):
        (home_dir / name / "scripts").mkdir(parents=True)
        (home_dir / name / "SKILL.md").write_text(f"{name}\n", encoding="utf-8")
        (home_dir / name / "scripts" / "run.sh").write_text("echo run\n", encoding="utf-8")
    (home_dir / "alpha" / "notes.md").write_text("operator notes\n", encoding="utf-8")
    (home_dir / "beta" / "scripts" / "run.sh").write_text("echo edited\n", encoding="utf-8")
    before = _snapshot(home_dir)

    result = _run_drift_helper(source_dir, home_dir, "alpha", "beta", "gamma", call="deploy_drift")

    assert result.stdout.splitlines() == ["differs beta", "missing gamma", "RC=0"], _diagnostic(result)
    assert _snapshot(home_dir) == before


def _make_tree(root: pathlib.Path) -> None:
    (root / "pkg" / "__pycache__").mkdir(parents=True)
    (root / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (root / "pkg" / "__pycache__" / "mod.cpython-312.pyc").write_bytes(b"\x00cache")
    (root / "README.md").write_text("readme\n", encoding="utf-8")


def _edit_file(dst: pathlib.Path) -> None:
    (dst / "pkg" / "mod.py").write_text("x = 2\n", encoding="utf-8")


def _drop_cache_dir(dst: pathlib.Path) -> None:
    shutil.rmtree(dst / "pkg" / "__pycache__")


def _add_extra_file(dst: pathlib.Path) -> None:
    (dst / "pkg" / "operator.py").write_text("mine\n", encoding="utf-8")


def _remove_file(dst: pathlib.Path) -> None:
    (dst / "README.md").unlink()


@pytest.mark.parametrize(
    ("mutate", "excludes", "expected_rc"),
    [
        (None, (), 0),
        (_add_extra_file, (), 0),
        (_edit_file, (), 1),
        (_remove_file, (), 1),
        (_drop_cache_dir, (), 1),
        (_drop_cache_dir, ("__pycache__",), 0),
        (_edit_file, ("__pycache__", "*.pyc"), 1),
    ],
)
def test_deploy_tree_current_holds_only_when_every_shipped_file_is_in_place(
    tmp_path: pathlib.Path, mutate, excludes: tuple[str, ...], expected_rc: int
) -> None:
    """The comparator upgrade.sh's deposit and deploy_drift both use: a file
    only in the deployed copy does not count, any missing or edited shipped
    file does, and an --exclude name is not compared. Nothing is written."""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _make_tree(src)
    shutil.copytree(src, dst)
    if mutate is not None:
        mutate(dst)
    before = _snapshot(dst)
    opts = " ".join(f"--exclude '{glob}'" for glob in excludes)
    script = textwrap.dedent(f"""\
        set -euo pipefail
        source "{_REAL_SUBAGENT_DEPLOY_SH}"
        rc=0
        deploy_tree_current {opts} "{src}" "{dst}" || rc=$?
        echo "RC=$rc"
        """)

    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30, cwd=str(tmp_path))

    assert result.stdout.splitlines() == [f"RC={expected_rc}"], _diagnostic(result)
    assert _snapshot(dst) == before


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
    assert (
        kanban_root / "scripts" / "upgrade.sh"
    ).is_file(), "the refresh command must name an upgrade.sh that exists after the upgrade"

    assert _snapshot(claude_dir) == before, "protected agents directory was modified"
    assert not (kanban_root / ".installed-subagents").exists(), output
    assert _GENERIC_SUMMARY_LINE not in output, output
    # The summary lists drifted wrappers in manifest order.
    assert f"agent wrappers, claude: 2 out of date (claude/{edited}.md, claude/{deleted}.md)" in output, _diagnostic(
        result
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
            f"agent wrappers, {provider}: current ({count} wrappers match the shipped copies)" in output
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
        assert f"agent wrappers, {provider}: not compared (no agents directory" in output, _diagnostic(result)


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
    assert warnings[0].rstrip().endswith(f"refresh: {kanban_root}/scripts/upgrade.sh --add-codex-agents"), _diagnostic(
        result
    )
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
        assert (
            deployed.read_bytes() == (_REAL_SUBAGENTS_DIR / f"{name}.md").read_bytes()
        ), f"{deployed} not refreshed\n{_diagnostic(result)}"
    state = (kanban_root / ".installed-subagents").read_text(encoding="utf-8")
    recorded = [ln.strip() for ln in state.splitlines() if ln.strip() and not ln.startswith("#")]
    assert recorded == names, state
    assert "agent wrappers, claude: refreshed (--add-claude-agents)" in output, _diagnostic(result)


@_requires_upgrade_sh
def test_upgrade_with_agent_flag_removes_only_a_recorded_wrapper_the_manifest_dropped(
    tmp_path: pathlib.Path,
) -> None:
    """A wrapper .installed-subagents records but the manifest no longer
    lists is removed from the flagged provider's agents directory; a wrapper
    no install recorded stays."""
    names = _manifest_names()
    claude_dir = tmp_path / "home" / ".claude" / "agents"
    _deploy_all_wrappers(claude_dir)
    (claude_dir / "retired.md").write_text("---\nname: retired\n---\n", encoding="utf-8")
    (claude_dir / "mine.md").write_text("---\nname: mine\n---\n", encoding="utf-8")
    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir()
    (kanban_root / ".installed-subagents").write_text("\n".join([*names, "retired"]) + "\n", encoding="utf-8")

    result, _ = _run_upgrade_phase2(tmp_path, "--add-claude-agents")
    output = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == 0, _diagnostic(result)
    assert not (claude_dir / "retired.md").exists(), _diagnostic(result)
    assert "Removing obsolete subagent (no longer in manifest): claude/retired" in output, _diagnostic(result)
    assert (claude_dir / "mine.md").is_file(), _diagnostic(result)
    state = (kanban_root / ".installed-subagents").read_text(encoding="utf-8")
    assert [ln for ln in state.splitlines() if ln.strip() and not ln.startswith("#")] == names, state


# ---------------------------------------------------------------------------
# upgrade.sh phase 2 — provider skill homes
# ---------------------------------------------------------------------------

_REAL_SKILLS_DIR = _REPO_ROOT / "skills"
_SKILL_DRIFT_WARNING = "Skill out of date:"


def _shipped_skills(provider: str) -> list[str]:
    """Names of the skills this tree ships for *provider*."""
    return sorted(p.parent.name for p in (_REAL_SKILLS_DIR / provider).glob("*/SKILL.md"))


def _install_shipped_skills(home: pathlib.Path, provider: str) -> None:
    """Copy every skill this tree ships for *provider* into *home*."""
    for name in _shipped_skills(provider):
        source = _REAL_SKILLS_DIR / provider / name
        for path in source.rglob("*"):
            if path.is_file():
                target = home / name / path.relative_to(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(path.read_bytes())


def _skill_homes(tmp_path: pathlib.Path) -> dict[str, pathlib.Path]:
    """Sandboxed provider skill homes: claude under CLAUDE_CONFIG_DIR, codex under HOME."""
    return {
        "claude": tmp_path / "claude_config" / "skills",
        "codex": tmp_path / "home" / ".codex" / "skills",
    }


def _skill_env(tmp_path: pathlib.Path) -> dict[str, str]:
    return {"CLAUDE_CONFIG_DIR": str(tmp_path / "claude_config")}


@_requires_upgrade_sh
def test_upgrade_without_skill_flags_reports_skill_drift_per_provider_and_writes_nothing(
    tmp_path: pathlib.Path,
) -> None:
    homes = _skill_homes(tmp_path)
    for provider, home in homes.items():
        assert _shipped_skills(provider), f"this tree ships no {provider} skill"
        _install_shipped_skills(home, provider)
    claude_skill = _shipped_skills("claude")[0]
    codex_skill = _shipped_skills("codex")[0]
    with (homes["claude"] / claude_skill / "SKILL.md").open("a", encoding="utf-8") as fh:
        fh.write("\nlocal edit\n")
    shutil.rmtree(homes["codex"] / codex_skill)
    before = {provider: _snapshot(home) for provider, home in homes.items()}

    result, kanban_root = _run_upgrade_phase2(tmp_path, extra_env=_skill_env(tmp_path))
    output = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == 0, _diagnostic(result)
    warnings = [ln for ln in result.stdout.splitlines() if _SKILL_DRIFT_WARNING in ln]
    assert len(warnings) == 2, _diagnostic(result)
    for provider, name in (("claude", claude_skill), ("codex", codex_skill)):
        refresh = f"refresh: {kanban_root}/scripts/install-skills.sh --provider {provider} --force"
        matching = [w for w in warnings if f"{provider}/{name} " in w]
        assert len(matching) == 1, _diagnostic(result)
        assert matching[0].rstrip().endswith(refresh), _diagnostic(result)
        assert f"provider skills, {provider}: 1 out of date ({provider}/{name}) -- {refresh}" in output, _diagnostic(
            result
        )
    assert (
        kanban_root / "scripts" / "install-skills.sh"
    ).is_file(), "the refresh command must name an install-skills.sh that exists after the upgrade"
    assert {
        provider: _snapshot(home) for provider, home in homes.items()
    } == before, "a protected provider skill home was modified"


@_requires_upgrade_sh
def test_upgrade_with_both_skill_flags_installs_every_shipped_skill_into_both_homes(
    tmp_path: pathlib.Path,
) -> None:
    homes = _skill_homes(tmp_path)

    result, kanban_root = _run_upgrade_phase2(
        tmp_path, "--add-claude-skills", "--add-codex-skills", extra_env=_skill_env(tmp_path)
    )
    output = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == 0, _diagnostic(result)
    for provider, home in homes.items():
        names = _shipped_skills(provider)
        assert names, f"this tree ships no {provider} skill"
        for name in names:
            assert _snapshot(home / name) == _snapshot(
                _REAL_SKILLS_DIR / provider / name
            ), f"{provider}/{name} not installed whole into {home}\n{_diagnostic(result)}"
        assert f"provider skills, {provider}: refreshed (--add-{provider}-skills)" in output, _diagnostic(result)
    assert [ln for ln in output.splitlines() if _SKILL_DRIFT_WARNING in ln] == [], _diagnostic(result)
    assert not (
        tmp_path / "home" / ".claude"
    ).exists(), "claude skills must follow CLAUDE_CONFIG_DIR, not HOME\n" + _diagnostic(result)


@_requires_upgrade_sh
def test_upgrade_skill_flag_skips_provider_whose_binary_is_not_on_path(
    tmp_path: pathlib.Path,
) -> None:
    homes = _skill_homes(tmp_path)

    result, _ = _run_upgrade_phase2(
        tmp_path,
        "--add-claude-skills",
        "--add-codex-skills",
        extra_env=_skill_env(tmp_path),
        stub_providers=("claude",),
    )
    output = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == 0, _diagnostic(result)
    assert "Skipping codex skills: CLI binary not found on PATH" in output, _diagnostic(result)
    assert "provider skills, codex: not refreshed (CLI binary not found on PATH)" in output, _diagnostic(result)
    assert not homes["codex"].exists(), _diagnostic(result)
    assert (homes["claude"] / _shipped_skills("claude")[0] / "SKILL.md").is_file(), _diagnostic(result)


# ---------------------------------------------------------------------------
# upgrade.sh phase 2 — explicit provider flags and kanban.cfg [providers]
# ---------------------------------------------------------------------------

_NOT_LISTED_NOTICE = "is not listed in kanban.cfg [providers] available"


def _write_available_providers(tmp_path: pathlib.Path, available: str) -> None:
    """Give the sandbox root a kanban.cfg whose [providers] available is *available*."""
    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir(exist_ok=True)
    (kanban_root / "kanban.cfg").write_text(f"[providers]\navailable = {available}\n", encoding="utf-8")


@_requires_upgrade_sh
@pytest.mark.parametrize(
    ("flag", "label", "summary_prefix", "other_prefix"),
    [
        ("--add-codex-skills", "Provider skills", "provider skills", "agent wrappers"),
        ("--add-codex-agents", "Provider-agent wrappers", "agent wrappers", "provider skills"),
    ],
)
def test_upgrade_codex_flag_refreshes_codex_missing_from_available_providers_with_notice(
    tmp_path: pathlib.Path, flag: str, label: str, summary_prefix: str, other_prefix: str
) -> None:
    _write_available_providers(tmp_path, "claude")
    homes = {
        "provider skills": tmp_path / "home" / ".codex" / "skills",
        "agent wrappers": tmp_path / "home" / ".codex" / "agents",
    }

    result, _ = _run_upgrade_phase2(tmp_path, flag, extra_env=_skill_env(tmp_path))
    output = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == 0, _diagnostic(result)
    notices = [ln for ln in output.splitlines() if _NOT_LISTED_NOTICE in ln]
    assert len(notices) == 1, _diagnostic(result)
    assert f"{label}: codex {_NOT_LISTED_NOTICE} (claude)" in notices[0], _diagnostic(result)
    assert notices[0].rstrip().endswith(f"because {flag} was passed"), _diagnostic(result)
    assert f"{summary_prefix}, codex: refreshed ({flag})" in output, _diagnostic(result)
    assert any(homes[summary_prefix].iterdir()), f"{homes[summary_prefix]} was not refreshed\n{_diagnostic(result)}"
    # The other step still walks only the configured providers.
    assert f"{other_prefix}, codex:" not in output, _diagnostic(result)
    assert not homes[other_prefix].exists(), _diagnostic(result)


@_requires_upgrade_sh
def test_upgrade_codex_flags_print_no_notice_when_available_providers_list_codex(
    tmp_path: pathlib.Path,
) -> None:
    _write_available_providers(tmp_path, "claude codex")

    result, _ = _run_upgrade_phase2(
        tmp_path, "--add-codex-skills", "--add-codex-agents", extra_env=_skill_env(tmp_path)
    )
    output = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == 0, _diagnostic(result)
    assert _NOT_LISTED_NOTICE not in output, _diagnostic(result)
    assert "agent wrappers, codex: refreshed (--add-codex-agents)" in output, _diagnostic(result)
    assert "provider skills, codex: refreshed (--add-codex-skills)" in output, _diagnostic(result)
    for summary_prefix in ("agent wrappers", "provider skills"):
        lines = [ln for ln in output.splitlines() if ln.strip().startswith(f"{summary_prefix}, codex:")]
        assert len(lines) == 1, _diagnostic(result)
