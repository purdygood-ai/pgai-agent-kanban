"""
test_release_notes_changelog_temp_root.py
=========================================
``cm_release_generate_notes`` (``team/scripts/lib/cm/release_notes.sh``)
renders Step 11b's CHANGELOG.md scratch buffer under the framework temp
root that ``lib/temp.sh`` resolves, and the stage refuses to load at all
when that wrapper is missing.

Covered:

  * with PGAI_AGENT_KANBAN_TEMP_DIR set to a sandbox path, the ``_cl_buf``
    scratch render the stage leaves for the EXIT-trap cleanup is a file
    under that sandbox and holds the writer's render;
  * a copy of the stage whose parent ``lib/`` has no ``temp.sh`` exits
    non-zero at source time, dual-channel, naming the missing path.

Design: the stage is sourced into a bash subprocess against a throwaway
git repository under tmp_path whose release notes, CHANGELOG.md and
README.md are already committed, so Steps 11a, 8b, 11b's commit and 11c
are all their idempotent no-ops. The functions the stage calls from its
caller (``git_step``, ``pp_strip_prefix_from_tag``) and the two module
calls (``pp_run_ops``: the changelog writer render; Step 11d's VERSION
writer) are stubbed in the harness; ``pgai_temp_dir`` is the real one.
The full release path through ``cm/release.sh`` is exercised end to end by
``tests/integration/test_cm_release_synthetic.py``.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import signal
import subprocess
import sys

import pytest

from tests.fixtures.binary_guard import require_binary
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_LIB_DIR = _TEAM_DIR / "scripts" / "lib"
_RELEASE_NOTES_SH = _LIB_DIR / "cm" / "release_notes.sh"
_VERSION = "v0.1.0"
_RENDER = "# Changelog\n\n## v0.1.0\n"
_TIMEOUT_SECONDS = 60

# Harness: stubs for what the caller (cm/release.sh) and the Python modules
# provide, then one call into the stage and a report of the buffer it kept.
_HARNESS = r"""
set -euo pipefail
source "$STAGE"
git_step() { shift; "$@"; }
pp_strip_prefix_from_tag() { printf '%s\n' "$2"; }
pp_run_ops() { [[ "$1" == pgai_agent_kanban.cm.changelog_writer ]] && printf '%s' "$RENDER"; }
cm_release_write_version_file() { :; }
cm_release_generate_notes
printf 'CL_BUF=%s\n' "$_cl_buf"
"""


@pytest.fixture(autouse=True)
def _require_git() -> None:
    require_binary("git", label="release-notes-changelog-temp-root")


def _git(repo: pathlib.Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)


def _released_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    repo = tmp_path / "dev_repo"
    (repo / "release-notes").mkdir(parents=True)
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "--local", "user.email", "test@pgai.local")
    _git(repo, "config", "--local", "user.name", "PGAI Test")
    (repo / "release-notes" / f"{_VERSION}.md").write_text(f"# {_VERSION}\n", encoding="utf-8")
    (repo / "CHANGELOG.md").write_text(_RENDER, encoding="utf-8")
    (repo / "README.md").write_text("See CHANGELOG.md.\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", f"Release {_VERSION}")
    return repo


def _run_stage(tmp_path: pathlib.Path, stage: pathlib.Path, temp_root: pathlib.Path) -> subprocess.CompletedProcess:
    repo = _released_repo(tmp_path)
    project_root = tmp_path / "kanban" / "projects" / "fixture"
    (project_root / "bugs").mkdir(parents=True)
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    (tmp_path / "home").mkdir()
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={
            "STAGE": str(stage),
            "RENDER": _RENDER,
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(tmp_path / "kanban"),
            "KANBAN_ROOT": str(tmp_path / "kanban"),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_root),
            "REPO_ROOT": str(repo),
            "MAIN_BRANCH": "main",
            "RC_BRANCH": f"rc/{_VERSION}",
            "ACTIVE_RC": _VERSION,
            "PROJECT_NAME": "fixture",
            "_CM_PROJECT_ROOT": str(project_root),
        },
    )
    proc = subprocess.Popen(
        ["bash", "-c", _HARNESS],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env, cwd=str(cwd), start_new_session=True,
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


def test_changelog_scratch_render_lands_under_the_temp_root(tmp_path: pathlib.Path) -> None:
    temp_root = tmp_path / "sandbox_tmp"

    result = _run_stage(tmp_path, _RELEASE_NOTES_SH, temp_root)

    assert result.returncode == 0, _diag(result)
    reported = [line for line in result.stdout.splitlines() if line.startswith("CL_BUF=")]
    assert len(reported) == 1, _diag(result)
    buffer = pathlib.Path(reported[0].removeprefix("CL_BUF="))
    assert buffer.parent == temp_root, f"scratch render {buffer} is not directly under {temp_root}"
    assert buffer.read_text(encoding="utf-8") == _RENDER
    assert "byte-identical to a fresh changelog_writer render" in result.stdout, _diag(result)


def test_missing_temp_sh_refuses_at_source_time(tmp_path: pathlib.Path) -> None:
    lib = tmp_path / "broken_install" / "scripts" / "lib"
    shutil.copytree(_LIB_DIR, lib, ignore=shutil.ignore_patterns("temp.sh", "__pycache__"))
    temp_root = tmp_path / "sandbox_tmp"

    result = _run_stage(tmp_path, lib / "cm" / _RELEASE_NOTES_SH.name, temp_root)

    assert result.returncode != 0, _diag(result)
    assert "error:   cm-release-notes.temp-sh-missing" in result.stderr, _diag(result)
    assert str(lib / "temp.sh") in result.stderr, _diag(result)
    assert "remedy:" in result.stderr, _diag(result)
    assert "CL_BUF=" not in result.stdout, "the stage must not run"
    assert not temp_root.exists(), "no temp root may be guessed"
