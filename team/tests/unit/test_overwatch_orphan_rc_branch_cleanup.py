"""
test_overwatch_orphan_rc_branch_cleanup.py
==========================================
check-orphan-rc-branches.sh deletes an orphan RC branch only when its
release tag provably holds the branch's work, contacts the remote only for
a project that pushes, and reports a branch it will not delete once:

  1. A squash release (the RC tip's tree equals the tagged tree, the tip is
     not an ancestor of main): the local branch is deleted.
  2. push_to_remote = false: no git push is ever run, and the remote delete
     is logged as skipped.
  3. push_to_remote = true: the remote delete names the project's remote
     (the push itself is recorded by a git stub, never run).
  4. A branch whose work is not on the tag: one bug report is filed, the
     branch stays, and a second sweep files nothing and attempts nothing.

The repair runs through the real overwatch_protocol.sh (repo lock and HALT
handling) in a throwaway kanban root; overwatch_log_action is replaced by a
recorder and git by a function that logs every call and refuses to push.
Every path lives under tmp_path.
"""

from __future__ import annotations

import pathlib
import subprocess
import textwrap

import pytest

from tests.fixtures.binary_guard import require_binary
from tests.unit.shell_harness import run_bash

_CHECK = "scripts/lib/overwatch-checks/check-orphan-rc-branches.sh"
_PROTOCOL = "scripts/lib/overwatch_protocol.sh"
_PROJECT = "orphan-fixture"
_BRANCH = "ai_rc/v1.0.0"
_TAG = "ai_v1.0.0"


@pytest.fixture(autouse=True)
def _require_git() -> None:
    require_binary("git", label="overwatch-orphan-rc-branch-cleanup")


def _git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def _repo(tmp_path: pathlib.Path, *, squash_released: bool) -> pathlib.Path:
    """ai_main plus ai_rc/v1.0.0 one commit ahead; ai_v1.0.0 tags main after the release."""
    repo = tmp_path / "dev"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=ai_main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    _git(repo, "checkout", "-q", "-b", _BRANCH)
    (repo / "feature.txt").write_text("feature\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "feature")
    _git(repo, "checkout", "-q", "ai_main")
    if squash_released:
        _git(repo, "merge", "--squash", _BRANCH)
        _git(repo, "commit", "-m", "Release v1.0.0")
    else:
        (repo / "other.txt").write_text("unrelated\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "Release v1.0.0 without the RC's work")
    _git(repo, "tag", _TAG)
    _git(repo, "remote", "add", "origin", str(tmp_path / "origin.git"))
    return repo


def _kanban(tmp_path: pathlib.Path, repo: pathlib.Path, push: str) -> pathlib.Path:
    root = tmp_path / "kanban"
    proj = root / "projects" / _PROJECT
    (proj / "overwatch" / "backups").mkdir(parents=True)
    (proj / "bugs").mkdir()
    (root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\n", encoding="utf-8")
    (proj / "project.cfg").write_text(
        f"[project]\nproject_name={_PROJECT}\ndev_tree_path={repo}\nbranch_prefix=ai_\npush_to_remote={push}\n",
        encoding="utf-8",
    )
    (proj / "release-state.md").write_text("# Release State\n\n## Active RC\nnone\n", encoding="utf-8")
    return root


def _sweep(tmp_path: pathlib.Path, root: pathlib.Path):
    proj = root / "projects" / _PROJECT
    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_PROTOCOL}
        overwatch_log_action() {{
            printf '%s\\t%s\\t%s\\n' "$3" "$2" "$5" >> "{proj}/overwatch/actions.log"
        }}
        overwatch_backup_file() {{ return 0; }}
        git() {{
            printf '%s\\n' "$*" >> "{tmp_path}/git-calls.log"
            case " $* " in
                *" push "*) return 0 ;;
            esac
            command git "$@"
        }}
        export KANBAN_ROOT="{root}"
        export OVERWATCH_PROJECT="{_PROJECT}"
        export PGAI_AGENT_KANBAN_ROOT_PATH="{root}"
        export PGAI_AGENT_KANBAN_TEMP_DIR="{tmp_path}/tmp"
        source {_CHECK}
        overwatch_check_orphan_rc_branches
    """)
    result = run_bash(tmp_path, script, timeout=90)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    calls = (tmp_path / "git-calls.log").read_text(encoding="utf-8") if (tmp_path / "git-calls.log").exists() else ""
    log = (proj / "overwatch" / "actions.log").read_text(encoding="utf-8") if (proj / "overwatch" / "actions.log").exists() else ""
    return result, calls, log


def _branches(repo: pathlib.Path) -> list[str]:
    return _git(repo, "branch", "--list", "--format=%(refname:short)").splitlines()


def test_squash_released_branch_is_deleted_and_a_local_only_project_never_pushes(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path, squash_released=True)
    root = _kanban(tmp_path, repo, "false")
    assert _git(repo, "rev-parse", f"{_BRANCH}^{{tree}}") == _git(repo, "rev-parse", f"{_TAG}^{{tree}}")

    result, calls, log = _sweep(tmp_path, root)

    assert _BRANCH not in _branches(repo), result.stderr
    assert "local-branch-deleted" in log, log
    assert "remote-branch-delete-skipped" in log, log
    assert not [c for c in calls.splitlines() if " push " in f" {c} "], calls
    assert not (root / "projects" / _PROJECT / "HALT").exists()


def test_pushing_project_deletes_the_branch_on_its_remote(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path, squash_released=True)
    root = _kanban(tmp_path, repo, "true")

    result, calls, log = _sweep(tmp_path, root)

    assert _BRANCH not in _branches(repo), result.stderr
    assert f"-C {repo} push origin --delete {_BRANCH}" in calls, calls
    assert "remote-branch-deleted" in log, log


def test_unprovable_branch_is_reported_once_and_never_retried(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path, squash_released=False)
    root = _kanban(tmp_path, repo, "false")
    bugs = root / "projects" / _PROJECT / "bugs"

    first, first_calls, first_log = _sweep(tmp_path, root)

    assert _BRANCH in _branches(repo), first.stderr
    filed = sorted(p.name for p in bugs.glob("BUG-*.md"))
    assert len(filed) == 1, first.stderr
    assert _BRANCH in (bugs / filed[0]).read_text(encoding="utf-8")
    assert "orphan-rc-branch-reported" in first_log, first_log
    assert "branch -D" not in first_calls and "branch -d" not in first_calls

    (tmp_path / "git-calls.log").unlink()
    second, second_calls, _second_log = _sweep(tmp_path, root)

    assert sorted(p.name for p in bugs.glob("BUG-*.md")) == filed, "no second bug"
    assert "already reported at its current tip; not retried" in second.stderr
    assert "branch -D" not in second_calls and "branch -d" not in second_calls
    assert _BRANCH in _branches(repo)
    assert not (root / "projects" / _PROJECT / "HALT").exists()
