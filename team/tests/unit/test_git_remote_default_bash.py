"""
test_git_remote_default_bash.py
===============================
The bash default git remote name lives in one file,
``scripts/lib/git_remote_default.sh``, and both of its readers get it from
there:

  1. ``scripts/dashboard/lib/version.sh`` sourced on its own -- no
     project_paths.sh, from an unrelated working directory -- resolves the
     default to ``origin``: ``PP_GIT_REMOTE_NAME_DEFAULT`` is ``origin``,
     ``_pgai_framework_release_refs`` answers ``origin`` with no prefix, and
     the one-argument ``get_latest_released_tag`` reads ``origin/main``.
  2. A default already loaded (project_paths.sh sourced first) is kept, not
     re-read from beside version.sh.
  3. ``scripts/lib/project_paths.sh`` takes its ``PP_GIT_REMOTE_NAME_DEFAULT``
     from the same file, and ``pp_git_remote_name`` answers it for a project
     with no config.
  4. Across ``scripts/`` and ``pgai_agent_kanban/`` (tests excluded), the
     ``origin`` default is assigned in exactly two places: the bash constant
     file and ``pgai_agent_kanban/lib/git_remote.py``.

Every bash run gets an explicit environment (build_scrubbed_env) whose
kanban root is a throwaway directory under tmp_path. The git repositories
are local, under tmp_path; the remote-tracking refs are written with
``update-ref``, so nothing contacts a remote.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.lib import git_remote  # noqa: E402
from tests.fixtures.binary_guard import require_binary  # noqa: E402
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env  # noqa: E402

_LIB = _TEAM_DIR / "scripts" / "lib"
_CONSTANT_FILE = _LIB / "git_remote_default.sh"
_VERSION_SH = _TEAM_DIR / "scripts" / "dashboard" / "lib" / "version.sh"
_PROJECT_PATHS = _LIB / "project_paths.sh"

# The only files allowed to assign the origin default, relative to team/.
_DEFINITIONS = {
    "scripts/lib/git_remote_default.sh",
    "pgai_agent_kanban/lib/git_remote.py",
}
_ASSIGNS_ORIGIN = re.compile(r'= *"origin"')


def _bash(tmp_path: pathlib.Path, body: str, *, cwd: pathlib.Path) -> subprocess.CompletedProcess:
    root = tmp_path / "kanban"
    (root / "tmp").mkdir(parents=True, exist_ok=True)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = build_scrubbed_env(
        home_dir=home,
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
            "GIT_CONFIG_NOSYSTEM": "1",
        },
    )
    return subprocess.run(
        ["bash", "-c", f"set -euo pipefail\n{body}\n"],
        capture_output=True, text=True, env=env, cwd=cwd, timeout=60,
    )


def _repo_with_two_remotes(tmp_path: pathlib.Path) -> pathlib.Path:
    """A repository tagged v1.0.0 on origin/main and v2.0.0 on upstream/main."""
    require_binary("git", label="git-remote-default-bash")
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True,
            env=build_scrubbed_env(home_dir=tmp_path / "home", extra_env={"GIT_CONFIG_NOSYSTEM": "1"}),
        ).stdout.strip()

    (tmp_path / "home").mkdir(exist_ok=True)
    git("init", "-b", "main")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    for tag, remote in (("v1.0.0", "origin"), ("v2.0.0", "upstream")):
        (repo / f"{tag}.txt").write_text(f"{tag}\n", encoding="utf-8")
        git("add", f"{tag}.txt")
        git("commit", "-m", tag)
        git("tag", tag)
        git("update-ref", f"refs/remotes/{remote}/main", git("rev-parse", "HEAD"))
    return repo


# ---------------------------------------------------------------------------
# 1. version.sh on its own resolves origin
# ---------------------------------------------------------------------------


def test_version_sh_alone_resolves_the_default_remote_to_origin(tmp_path: pathlib.Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    result = _bash(
        tmp_path,
        f'source "{_VERSION_SH}"\n'
        'declare -F pp_git_remote_name >/dev/null && echo "project_paths loaded" || true\n'
        'printf "default=%s\\n" "$PP_GIT_REMOTE_NAME_DEFAULT"\n'
        'printf "refs=[%s]\\n" "$(_pgai_framework_release_refs "")"',
        cwd=elsewhere,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["default=origin", "refs=[origin ]"], result.stdout


def test_version_sh_alone_reads_origin_main_by_default(tmp_path: pathlib.Path) -> None:
    repo = _repo_with_two_remotes(tmp_path)
    result = _bash(tmp_path, f'source "{_VERSION_SH}"\nget_latest_released_tag "{repo}"', cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "v1.0.0", "the one-argument form must read origin/main"


# ---------------------------------------------------------------------------
# 2. A default already loaded is kept
# ---------------------------------------------------------------------------


def test_version_sh_keeps_a_default_already_loaded(tmp_path: pathlib.Path) -> None:
    repo = _repo_with_two_remotes(tmp_path)
    result = _bash(
        tmp_path,
        f'PP_GIT_REMOTE_NAME_DEFAULT=upstream\nsource "{_VERSION_SH}"\nget_latest_released_tag "{repo}"',
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "v2.0.0"


# ---------------------------------------------------------------------------
# 3. project_paths.sh reads the same file
# ---------------------------------------------------------------------------


def test_project_paths_takes_its_default_from_the_constant_file(tmp_path: pathlib.Path) -> None:
    result = _bash(
        tmp_path,
        f'source "{_CONSTANT_FILE}"\nconstant="$PP_GIT_REMOTE_NAME_DEFAULT"\n'
        f'unset PP_GIT_REMOTE_NAME_DEFAULT\nsource "{_PROJECT_PATHS}"\n'
        'printf "%s %s %s\\n" "$constant" "$PP_GIT_REMOTE_NAME_DEFAULT" "$(pp_git_remote_name no-such-project)"',
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    expected = git_remote.DEFAULT_REMOTE_NAME
    assert result.stdout.split() == [expected, expected, expected]


# ---------------------------------------------------------------------------
# 4. One definition per language
# ---------------------------------------------------------------------------


def test_the_origin_default_is_assigned_once_per_language() -> None:
    found = set()
    for top in ("scripts", "pgai_agent_kanban"):
        for path in (_TEAM_DIR / top).rglob("*"):
            if path.suffix not in (".sh", ".py") or "tests" in path.relative_to(_TEAM_DIR).parts:
                continue
            if _ASSIGNS_ORIGIN.search(path.read_text(encoding="utf-8", errors="replace")):
                found.add(path.relative_to(_TEAM_DIR).as_posix())
    assert found == _DEFINITIONS, (
        "assign the default remote only in scripts/lib/git_remote_default.sh (bash) and "
        "pgai_agent_kanban/lib/git_remote.py (Python); read it from there everywhere else"
    )


@pytest.mark.parametrize("relative", sorted(_DEFINITIONS))
def test_each_definition_assigns_origin_exactly_once(relative: str) -> None:
    text = (_TEAM_DIR / relative).read_text(encoding="utf-8")
    assert len(_ASSIGNS_ORIGIN.findall(text)) == 1
