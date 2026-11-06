"""
test_stale_artifact_branches_smoke_temp_root.py
================================================
``cleanup/test-stale-artifact-branches.sh`` builds its synthetic task folders
under the framework temp root that ``lib/temp.sh`` (``pgai_temp_dir``)
resolves, never under a root of its own.

Covered, against the real smoke script and the real
``cleanup-stale-artifact-only-branches.sh`` it drives, run from a foreign
cwd against a throwaway git repository and a sandbox kanban root, with an
environment built by ``build_scrubbed_env``. The script deletes its scratch
directory before exiting, so a ``mktemp`` shim placed first on PATH records
each template it is handed and then runs the real binary; the assertions
read where the scratch was actually created:

  * with PGAI_AGENT_KANBAN_TEMP_DIR set to a sandbox path, the scratch is
    made directly under it, the smoke tests pass, and nothing is left under
    the sandbox or an unrelated TMPDIR;
  * with PGAI_AGENT_KANBAN_TEMP_DIR unset, the root comes from the sandbox
    kanban.cfg ``[paths]`` ``tmp_root``/``tmp_subdir``;
  * a copy of the script whose ``lib/`` has no ``temp.sh`` refuses
    dual-channel, naming the missing path, and creates no branch.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import signal
import subprocess

import pytest

from tests.fixtures.binary_guard import require_binary
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_SMOKE_SH = _SCRIPTS_DIR / "cleanup" / "test-stale-artifact-branches.sh"
_CLEANUP_SH = _SCRIPTS_DIR / "cleanup-stale-artifact-only-branches.sh"
_SCRATCH_TEMPLATE = "smoke-stale-branches-XXXXXX"
_TIMEOUT_SECONDS = 120


@pytest.fixture(autouse=True)
def _require_git() -> None:
    require_binary("git", label="stale-artifact-branches-smoke-temp-root")


def _git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=_TIMEOUT_SECONDS,
    ).stdout


def _scratch_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """A one-commit repository on ai_main with an RC branch for --rc detection."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "--initial-branch=ai_main")
    _git(repo, "config", "user.email", "smoke@example.com")
    _git(repo, "config", "user.name", "Smoke")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "initial")
    _git(repo, "branch", "ai_rc/v1.0.0")
    return repo


def _kanban_root(tmp_path: pathlib.Path, cfg: str = "") -> pathlib.Path:
    root = tmp_path / "kanban"
    root.mkdir()
    (root / "kanban.cfg").write_text(cfg, encoding="utf-8")
    (root / "projects.cfg").write_text("", encoding="utf-8")
    return root


def _mktemp_shim(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """A ``mktemp`` ahead of the real one that logs its arguments."""
    real = shutil.which("mktemp")
    assert real, "mktemp must be on PATH"
    bin_dir = tmp_path / "shim_bin"
    bin_dir.mkdir()
    log = tmp_path / "mktemp.log"
    shim = bin_dir / "mktemp"
    shim.write_text(
        "#!/bin/bash\n" f"printf '%s\\n' \"$*\" >> '{log}'\n" f"exec '{real}' \"$@\"\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return bin_dir, log


def _run(
    script: pathlib.Path,
    tmp_path: pathlib.Path,
    root: pathlib.Path,
    repo: pathlib.Path,
    env_extra: dict[str, str],
    shim_dir: pathlib.Path,
) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    cwd = tmp_path / "elsewhere"
    cwd.mkdir(exist_ok=True)
    env = build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[shim_dir],
        extra_env={"PGAI_AGENT_KANBAN_ROOT_PATH": str(root), "KANBAN_ROOT": str(root), **env_extra},
    )
    proc = subprocess.Popen(
        ["bash", str(script), "--worktree", str(repo), "--kanban-root", str(root)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=str(cwd),
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.communicate()
        raise
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)


def _diag(result: subprocess.CompletedProcess) -> str:
    return f"exit={result.returncode}\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _scratch_templates(log: pathlib.Path) -> list[str]:
    """The ``mktemp -d`` templates naming the smoke script's own scratch root."""
    assert log.is_file(), "mktemp was never invoked through the shim"
    return [line for line in log.read_text(encoding="utf-8").splitlines() if _SCRATCH_TEMPLATE in line]


def test_scratch_lands_under_the_temp_dir_override(tmp_path: pathlib.Path) -> None:
    root = _kanban_root(tmp_path)
    repo = _scratch_repo(tmp_path)
    sandbox = tmp_path / "sandbox_tmp"
    other_tmpdir = tmp_path / "other_tmpdir"
    other_tmpdir.mkdir()
    shim_dir, log = _mktemp_shim(tmp_path)

    result = _run(
        _SMOKE_SH,
        tmp_path,
        root,
        repo,
        {"PGAI_AGENT_KANBAN_TEMP_DIR": str(sandbox), "TMPDIR": str(other_tmpdir)},
        shim_dir,
    )

    assert result.returncode == 0, _diag(result)
    assert "0 failed" in result.stdout, _diag(result)
    assert _scratch_templates(log) == [f"-d {sandbox}/{_SCRATCH_TEMPLATE}"]
    assert list(sandbox.iterdir()) == [], "the scratch root is removed before exit"
    assert list(other_tmpdir.iterdir()) == [], "nothing may be made under TMPDIR"


def test_scratch_follows_kanban_cfg_paths_without_an_override(tmp_path: pathlib.Path) -> None:
    configured = tmp_path / "configured"
    root = _kanban_root(tmp_path, f"[paths]\ntmp_root = {configured}\ntmp_subdir = scratch\n")
    repo = _scratch_repo(tmp_path)
    shim_dir, log = _mktemp_shim(tmp_path)

    result = _run(_SMOKE_SH, tmp_path, root, repo, {}, shim_dir)

    assert result.returncode == 0, _diag(result)
    assert _scratch_templates(log) == [f"-d {configured}/scratch/{_SCRATCH_TEMPLATE}"]


def test_missing_temp_sh_refuses_before_any_branch_is_made(tmp_path: pathlib.Path) -> None:
    root = _kanban_root(tmp_path)
    repo = _scratch_repo(tmp_path)
    branches_before = _git(repo, "branch", "--list")
    scripts = tmp_path / "broken_install" / "scripts"
    shutil.copytree(_SCRIPTS_DIR / "lib", scripts / "lib", ignore=shutil.ignore_patterns("temp.sh", "__pycache__"))
    (scripts / "cleanup").mkdir()
    shutil.copy2(_CLEANUP_SH, scripts / _CLEANUP_SH.name)
    broken = scripts / "cleanup" / _SMOKE_SH.name
    shutil.copy2(_SMOKE_SH, broken)
    sandbox = tmp_path / "sandbox_tmp"
    shim_dir, log = _mktemp_shim(tmp_path)

    result = _run(broken, tmp_path, root, repo, {"PGAI_AGENT_KANBAN_TEMP_DIR": str(sandbox)}, shim_dir)

    assert result.returncode == 1, _diag(result)
    assert "error:   test-stale-artifact-branches.temp-sh-missing" in result.stderr, _diag(result)
    assert str(scripts / "lib" / "temp.sh") in result.stderr, _diag(result)
    assert "remedy:" in result.stderr
    assert not log.exists(), "no scratch directory may be created"
    assert _git(repo, "branch", "--list") == branches_before
