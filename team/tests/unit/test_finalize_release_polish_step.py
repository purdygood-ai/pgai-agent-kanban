"""
test_finalize_release_polish_step.py
====================================
cm/finalize-release.sh Step 2b: WRITER polish of release-notes/<VERSION>.md
that arrives after cm/release.sh tagged the release.

Behaviour under test:
  1. The CHANGELOG.md writer failing leaves CHANGELOG.md byte-identical,
     makes no commit and stages nothing; the refusal is dual-channel.
  2. HEAD on a branch other than main with polish pending refuses before
     anything is staged or committed, on either branch.
  3. A local, unpublished release tag on main's tip ends on the polish
     commit, which holds the polished notes.
  4. A tag that is not on main's tip, or one already published on the
     remote, is refused before anything is staged or committed.

Design: a throwaway kanban root and a local dev repository under tmp_path;
the script runs with a scrubbed environment from an unrelated directory.
The failing writer is a stub package placed ahead of this tree on
PYTHONPATH that extends its __path__ to the real package, so only
pgai_agent_kanban.cm.changelog_writer is replaced. The published-tag case
uses a bare clone of the dev repository as its remote; nothing is pushed.
"""

from __future__ import annotations

import os
import pathlib
import signal
import subprocess
import sys

import pytest

from tests.fixtures.binary_guard import require_binary
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_FINALIZE = _TEAM_DIR / "scripts" / "cm" / "finalize-release.sh"
_PROJECT = "polish-fixture"
_VERSION = "v0.1.0"
_NOTES = f"release-notes/{_VERSION}.md"
_TIMEOUT_SECONDS = 90

_EXTEND = "import pkgutil\n__path__ = pkgutil.extend_path(__path__, __name__)\n"
_FAILING_WRITER = "import sys\nprint('changelog_writer: stub failure', file=sys.stderr)\nsys.exit(3)\n"


@pytest.fixture(autouse=True)
def _require_git() -> None:
    require_binary("git", label="finalize-release-polish-step")


def _git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def _build(tmp_path: pathlib.Path, *, push: str = "false") -> tuple[pathlib.Path, pathlib.Path]:
    """A dev repo on main tagged _VERSION at its tip, with polish pending; and a kanban root."""
    repo = tmp_path / "dev_repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "--local", "user.email", "test@pgai.local")
    _git(repo, "config", "--local", "user.name", "PGAI Test")
    (repo / "release-notes").mkdir()
    (repo / _NOTES).write_text(f"# {_VERSION}\n\nDraft notes.\n", encoding="utf-8")
    (repo / "CHANGELOG.md").write_text("# Changelog\n\nprevious render\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", f"Release {_VERSION}")
    _git(repo, "tag", _VERSION)
    (repo / _NOTES).write_text(f"# {_VERSION}\n\nPolished notes.\n", encoding="utf-8")

    root = tmp_path / "kanban_root"
    proj = root / "projects" / _PROJECT
    (proj / "bugs").mkdir(parents=True)
    (root / "tmp").mkdir()
    (root / "kanban.cfg").write_text("[paths]\n", encoding="utf-8")
    (root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority=1\nenabled=true\n", encoding="utf-8")
    (proj / "project.cfg").write_text(
        f"[project]\nproject_name = {_PROJECT}\ndev_tree_path = {repo}\nworkflow_type = release\n"
        f"push_to_remote = {push}\n",
        encoding="utf-8",
    )
    return repo, root


def _failing_writer_path(tmp_path: pathlib.Path) -> pathlib.Path:
    stub = tmp_path / "stub_py"
    (stub / "pgai_agent_kanban" / "cm").mkdir(parents=True)
    (stub / "pgai_agent_kanban" / "__init__.py").write_text(_EXTEND, encoding="utf-8")
    (stub / "pgai_agent_kanban" / "cm" / "__init__.py").write_text(_EXTEND, encoding="utf-8")
    (stub / "pgai_agent_kanban" / "cm" / "changelog_writer.py").write_text(_FAILING_WRITER, encoding="utf-8")
    return stub


def _finalize(tmp_path: pathlib.Path, root: pathlib.Path, extra_env: dict | None = None):
    cwd = tmp_path / "elsewhere"
    cwd.mkdir(exist_ok=True)
    (tmp_path / "home").mkdir(exist_ok=True)
    env_extra = {
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "KANBAN_ROOT": str(root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
    }
    env_extra.update(extra_env or {})
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env=env_extra,
    )
    proc = subprocess.Popen(
        ["bash", str(_FINALIZE), "--project", _PROJECT, "--yes", _VERSION],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env, cwd=str(cwd), start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.communicate()
        raise
    return proc.returncode, out, err


def _diag(rc: int, out: str, err: str) -> str:
    return f"exit={rc}\n--- stdout ---\n{out}\n--- stderr ---\n{err}"


def _refs(repo: pathlib.Path) -> str:
    return _git(repo, "for-each-ref", "--format=%(refname) %(objectname)")


def test_failing_changelog_writer_leaves_changelog_and_history_untouched(tmp_path: pathlib.Path) -> None:
    repo, root = _build(tmp_path)
    changelog_before = (repo / "CHANGELOG.md").read_bytes()
    refs_before = _refs(repo)

    rc, out, err = _finalize(tmp_path, root, {"PYTHONPATH": str(_failing_writer_path(tmp_path))})

    assert rc != 0, _diag(rc, out, err)
    assert "cm-finalize-release.changelog-render-failed" in err, _diag(rc, out, err)
    assert "remedy:" in err, _diag(rc, out, err)
    assert (repo / "CHANGELOG.md").read_bytes() == changelog_before
    assert _refs(repo) == refs_before, "no commit and no tag move"
    assert _git(repo, "diff", "--cached", "--name-only") == "", "nothing may be left staged"
    assert [p.name for p in repo.iterdir() if p.name.startswith(".CHANGELOG")] == []


def test_polish_on_another_branch_is_refused_before_committing(tmp_path: pathlib.Path) -> None:
    repo, root = _build(tmp_path)
    _git(repo, "checkout", "-b", "feature/elsewhere")
    refs_before = _refs(repo)

    rc, out, err = _finalize(tmp_path, root)

    assert rc != 0, _diag(rc, out, err)
    assert "cm-finalize-release.polish-off-main" in err, _diag(rc, out, err)
    assert f"git -C '{repo}' checkout main && scripts/cm/finalize-release.sh --project {_PROJECT} {_VERSION}" in err
    assert _refs(repo) == refs_before
    assert _git(repo, "diff", "--cached", "--name-only") == ""


def test_unpublished_tag_moves_onto_the_polish_commit(tmp_path: pathlib.Path) -> None:
    repo, root = _build(tmp_path)
    released = _git(repo, "rev-parse", "HEAD").strip()

    rc, out, err = _finalize(tmp_path, root)

    assert rc == 0, _diag(rc, out, err)
    head = _git(repo, "rev-parse", "HEAD").strip()
    assert head != released, "a polish commit was made"
    assert _git(repo, "rev-parse", f"{_VERSION}^{{commit}}").strip() == head, _diag(rc, out, err)
    assert "Polished notes." in _git(repo, "show", f"{_VERSION}:{_NOTES}")
    assert _git(repo, "status", "--porcelain") == ""
    assert f"local tag {_VERSION} moved onto it" in out, _diag(rc, out, err)


def test_tag_behind_main_tip_is_refused(tmp_path: pathlib.Path) -> None:
    repo, root = _build(tmp_path)
    (repo / "later.txt").write_text("later\n", encoding="utf-8")
    _git(repo, "add", "later.txt")
    _git(repo, "commit", "-m", "later work")
    refs_before = _refs(repo)

    rc, out, err = _finalize(tmp_path, root)

    assert rc != 0, _diag(rc, out, err)
    assert "cm-finalize-release.polish-would-strand-tag" in err, _diag(rc, out, err)
    assert "not main's tip" in err
    assert _refs(repo) == refs_before
    assert _git(repo, "diff", "--cached", "--name-only") == ""


def test_published_tag_is_refused(tmp_path: pathlib.Path) -> None:
    repo, root = _build(tmp_path, push="true")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "--bare", "-q", str(repo), str(origin)], check=True, capture_output=True)
    _git(repo, "remote", "add", "origin", str(origin))
    refs_before = _refs(repo)

    rc, out, err = _finalize(tmp_path, root)

    assert rc != 0, _diag(rc, out, err)
    assert "cm-finalize-release.polish-would-strand-tag" in err, _diag(rc, out, err)
    assert "already published on origin" in err
    assert _refs(repo) == refs_before
