"""
test_git_remote_name_callers.py
===============================
The default git remote name is defined once per language, and the callers
that name a project's remote outside the CM verbs take it from the shared
resolver (``pp_git_remote_name`` / ``pgai_agent_kanban.lib.git_remote``).

Covered:

  1. Default parity: bash ``PP_GIT_REMOTE_NAME_DEFAULT`` (project_paths.sh),
     Python ``git_remote.DEFAULT_REMOTE_NAME``, ``ops.projects``'
     ``DEFAULT_GIT_REMOTE`` and the stand-alone fallback of
     ``scripts/dashboard/lib/version.sh`` (sourced without project_paths.sh)
     are one value.
  2. ``_disc_git_tag_exists`` (discovery_guards.sh) asks the project's
     configured remote for a tag the dev tree lacks: ``origin`` when
     ``git_remote_name`` is absent, the configured name when it is set, and
     no remote at all (one log line, "not found") when it is invalid.
  3. ``scan_and_bundle_bugs`` (wake_autonomous_scan.sh) records the URL of
     the configured remote as the bundle's Git Repo.
  4. ``get_kanban_version`` (version.sh) and Python
     ``released_version.kanban_version`` read the same
     ``<remote>/<prefix>main`` for a framework project with a non-default
     ``branch_prefix`` and ``git_remote_name``, and give the same answer.

Every kanban root is a throwaway directory under tmp_path, and every bash
run gets an explicit environment (build_scrubbed_env) pointing every root
variable at it. No test contacts a real remote: the "remotes" are local
bare repositories (cloned from a local repository) or remote entries whose
URL is only read back from git config.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.lib import git_remote  # noqa: E402
from pgai_agent_kanban.lib.released_version import kanban_version  # noqa: E402
from pgai_agent_kanban.ops import projects  # noqa: E402
from tests.fixtures.binary_guard import require_binary  # noqa: E402
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env  # noqa: E402

_LIB = _TEAM_DIR / "scripts" / "lib"
_VERSION_SH = _TEAM_DIR / "scripts" / "dashboard" / "lib" / "version.sh"
_PROJECT = "remote-callers"
_FRAMEWORK = "pgai-agent-kanban"
_TAG = "ai_v1.2.3"


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Every test but the parity check drives real git repositories."""
    require_binary("git", label="git-remote-name-callers")


def _env(tmp_path: pathlib.Path, root: pathlib.Path) -> dict:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (root / "tmp").mkdir(parents=True, exist_ok=True)
    return build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
            "GIT_CONFIG_NOSYSTEM": "1",
        },
    )


def _bash(
    tmp_path: pathlib.Path, root: pathlib.Path, body: str, *, project_paths: bool = True
) -> subprocess.CompletedProcess:
    prelude = f'source "{_LIB / "ini_parser.sh"}"\nsource "{_LIB / "project_paths.sh"}"\n' if project_paths else ""
    return subprocess.run(
        ["bash", "-c", f"{prelude}{body}\n"],
        capture_output=True,
        text=True,
        env=_env(tmp_path, root),
        timeout=60,
    )


def _git(tmp_path: pathlib.Path, repo: pathlib.Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        env=_env(tmp_path, tmp_path / "kanban"),
    )
    return result.stdout.strip()


def _repo(tmp_path: pathlib.Path, name: str, tag: str | None = None) -> pathlib.Path:
    """A local repository with one commit, tagged *tag* when given."""
    repo = tmp_path / name
    repo.mkdir()
    _git(tmp_path, repo, "init", "-b", "main")
    _git(tmp_path, repo, "config", "user.email", "test@example.com")
    _git(tmp_path, repo, "config", "user.name", "Test")
    (repo / "f.txt").write_text(f"{name}\n", encoding="utf-8")
    _git(tmp_path, repo, "add", "f.txt")
    _git(tmp_path, repo, "commit", "-m", name)
    if tag:
        _git(tmp_path, repo, "tag", tag)
    return repo


def _bare_remote(tmp_path: pathlib.Path, name: str, tag: str | None) -> pathlib.Path:
    """A local bare repository holding *tag* (or no tag), cloned from a local source."""
    source = _repo(tmp_path, f"{name}-src", tag)
    bare = tmp_path / f"{name}.git"
    subprocess.run(
        ["git", "clone", "--bare", "--quiet", str(source), str(bare)],
        capture_output=True,
        text=True,
        check=True,
        env=_env(tmp_path, tmp_path / "kanban"),
    )
    return bare


def _project(root: pathlib.Path, name: str, lines: list[str]) -> pathlib.Path:
    project = root / "projects" / name
    project.mkdir(parents=True, exist_ok=True)
    (project / "project.cfg").write_text("[project]\n" + "".join(f"{line}\n" for line in lines), encoding="utf-8")
    return project


# ---------------------------------------------------------------------------
# 1. One default per language, held equal
# ---------------------------------------------------------------------------


def test_bash_and_python_default_remote_names_are_equal(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "kanban"
    bash_default = _bash(tmp_path, root, 'printf "%s" "$PP_GIT_REMOTE_NAME_DEFAULT"')
    assert bash_default.returncode == 0, bash_default.stderr
    assert bash_default.stdout == git_remote.DEFAULT_REMOTE_NAME
    assert projects.DEFAULT_GIT_REMOTE == git_remote.DEFAULT_REMOTE_NAME

    standalone = _bash(tmp_path, root, f'source "{_VERSION_SH}"\n_pgai_framework_release_refs ""', project_paths=False)
    assert standalone.returncode == 0, standalone.stderr
    assert standalone.stdout == f"{git_remote.DEFAULT_REMOTE_NAME} \n"


# ---------------------------------------------------------------------------
# 2. _disc_git_tag_exists asks the project's remote
# ---------------------------------------------------------------------------


@pytest.fixture
def _tag_fixture(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """A dev tree without the tag, remote ``origin`` without it and ``upstream`` with it."""
    root = tmp_path / "kanban"
    root.mkdir()
    dev = _repo(tmp_path, "dev")
    _git(tmp_path, dev, "remote", "add", "origin", str(_bare_remote(tmp_path, "origin", None)))
    _git(tmp_path, dev, "remote", "add", "upstream", str(_bare_remote(tmp_path, "upstream", _TAG)))
    return root, dev


def _tag_exists(
    tmp_path: pathlib.Path, root: pathlib.Path, call: str, *, cwd: pathlib.Path | None = None
) -> subprocess.CompletedProcess:
    body = f'source "{_LIB / "discovery.sh"}"\n'
    if cwd is not None:
        body += f'cd "{cwd}"\n'
    body += f'{call}\necho "RC=$?"'
    return _bash(tmp_path, root, body)


@pytest.mark.parametrize(
    ("cfg_line", "expected_rc"),
    [
        (None, "RC=1"),  # origin, which lacks the tag
        ("git_remote_name = origin", "RC=1"),
        ("git_remote_name = upstream", "RC=0"),  # the configured remote holds it
        ('git_remote_name = "upstream"', "RC=0"),
    ],
)
def test_tag_lookup_asks_the_configured_remote(tmp_path, _tag_fixture, cfg_line, expected_rc) -> None:
    root, dev = _tag_fixture
    _project(root, _PROJECT, [cfg_line] if cfg_line else [])
    result = _tag_exists(tmp_path, root, f'_disc_git_tag_exists {_TAG} "{dev}" {_PROJECT}')
    assert expected_rc in result.stdout, (result.stdout, result.stderr)


def test_absent_remote_name_still_asks_origin(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "kanban"
    root.mkdir()
    dev = _repo(tmp_path, "dev")
    _git(tmp_path, dev, "remote", "add", "origin", str(_bare_remote(tmp_path, "origin", _TAG)))
    _project(root, _PROJECT, [])
    result = _tag_exists(tmp_path, root, f'_disc_git_tag_exists {_TAG} "{dev}" {_PROJECT}')
    assert "RC=0" in result.stdout, (result.stdout, result.stderr)


def test_cwd_fallback_asks_the_configured_remote(tmp_path, _tag_fixture) -> None:
    root, dev = _tag_fixture
    _project(root, _PROJECT, ["git_remote_name = upstream"])
    result = _tag_exists(tmp_path, root, f'_disc_git_tag_exists {_TAG} "" {_PROJECT}', cwd=dev)
    assert "RC=0" in result.stdout, (result.stdout, result.stderr)


def test_invalid_remote_name_skips_the_remote_check(tmp_path, _tag_fixture) -> None:
    root, dev = _tag_fixture
    _project(root, _PROJECT, ["git_remote_name = -x"])
    result = _tag_exists(tmp_path, root, f'_disc_git_tag_exists {_TAG} "{dev}" {_PROJECT}')
    assert "RC=1" in result.stdout, (result.stdout, result.stderr)
    assert f"git_remote_name for project '{_PROJECT}' is not a valid git remote name" in result.stderr
    assert "git_remote_name.invalid" not in result.stderr, "the quiet read must not emit the refusal block"


# ---------------------------------------------------------------------------
# 3. scan_and_bundle_bugs records the configured remote's URL
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cfg_line", "expected_repo"),
    [
        (None, "git@example.invalid:org/origin-repo.git"),
        ("git_remote_name = upstream", "git@example.invalid:org/upstream-repo.git"),
        ("git_remote_name = -x", "none"),
    ],
)
def test_bug_bundle_git_repo_is_the_configured_remote_url(tmp_path, cfg_line, expected_repo) -> None:
    root = tmp_path / "kanban"
    project = _project(root, _PROJECT, [cfg_line] if cfg_line else [])
    (project / "bugs").mkdir()
    (project / "bugs" / "BUG-0001-sample.md").write_text("# Sample bug\n", encoding="utf-8")
    dev = _repo(tmp_path, "dev")
    _git(tmp_path, dev, "remote", "add", "origin", "git@example.invalid:org/origin-repo.git")
    _git(tmp_path, dev, "remote", "add", "upstream", "git@example.invalid:org/upstream-repo.git")

    body = f"""
log() {{ printf '%s\\n' "$*" >&2; }}
source "{_LIB / "wake_autonomous_scan.sh"}"
get_release_state_field() {{ case "$1" in "Active RC") echo none ;; "Last Released") echo v1.2.3 ;; esac; }}
TEAM_ROOT="{tmp_path}"
_CURRENT_PROJECT="{_PROJECT}"
PP_dev_tree_path="{dev}"
unset PP_git_repo_url
scan_and_bundle_bugs
echo "RC=$?"
"""
    result = _bash(tmp_path, root, body)
    assert "RC=0" in result.stdout, (result.stdout, result.stderr)
    assert f"project Git Repo = {expected_repo}\n" in result.stderr, result.stderr
    bundle = next((project / "requirements" / "priority").glob("v1.2.4-bugfix-bundle-*.md"))
    assert f"## Git Repo\n{expected_repo}\n" in bundle.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 4. version.sh and released_version agree on a non-default framework project
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cfg_lines", "expected"),
    [
        (["branch_prefix = ai_", "git_remote_name = upstream"], "v1.1.0"),
        (["branch_prefix = ai_"], "v1.0.0"),
        (["branch_prefix = ai_", "git_remote_name = -x"], "unknown"),
    ],
)
def test_bash_and_python_framework_version_agree(tmp_path, cfg_lines, expected) -> None:
    root = tmp_path / "kanban"
    _project(root, _FRAMEWORK, cfg_lines)
    repo = _repo(tmp_path, "framework", "ai_v1.0.0")
    older = _git(tmp_path, repo, "rev-parse", "HEAD")
    (repo / "g.txt").write_text("newer\n", encoding="utf-8")
    _git(tmp_path, repo, "add", "g.txt")
    _git(tmp_path, repo, "commit", "-m", "newer")
    _git(tmp_path, repo, "tag", "ai_v1.1.0")
    newer = _git(tmp_path, repo, "rev-parse", "HEAD")
    _git(tmp_path, repo, "update-ref", "refs/remotes/origin/ai_main", older)
    _git(tmp_path, repo, "update-ref", "refs/remotes/upstream/ai_main", newer)

    result = _bash(tmp_path, root, f'source "{_VERSION_SH}"\nget_kanban_version "{root}" "{repo}"')
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected
    assert kanban_version(root, repo) == expected
