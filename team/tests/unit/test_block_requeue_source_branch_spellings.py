"""
test_block_requeue_source_branch_spellings.py
=============================================
OVERWATCH's block-requeue path resolves a task README's ``## Source Branch``
to the branch that really exists in the dev tree, whichever mainline
spelling the ticket carries.

Tickets written before the materializer applied ``branch_prefix`` to its
mainline fallback carry a bare ``main`` on a project whose real mainline is
``ai_main``; tickets written since carry ``ai_main`` itself. Both must keep
resolving — and snapshotting the commits ahead of them — so a blocked task
of either vintage can still be requeued onto its branch.

Behavior covered:

  1. ``## Source Branch = main`` on an ``ai_``-prefixed project resolves to
     ``ai_main`` (the written value is kept as ``source_written``).
  2. ``## Source Branch = ai_main`` resolves to itself, never to a doubled
     ``ai_ai_main``.
  3. With an empty prefix, ``main`` resolves to ``main``.
  4. The commits-ahead snapshot counts the feature branch against the
     resolved mainline when the README names a bare ``main``.
  5. A mainline that exists under neither spelling is refused, naming the
     written value.

Every repository and kanban root is built under pytest's ``tmp_path``; git
runs only against those throwaway repositories, with repository-local
identity config.
"""

from __future__ import annotations

import datetime
import pathlib
import subprocess

import pytest

from pgai_agent_kanban.overwatch.block_record import BlockRecord
from pgai_agent_kanban.overwatch.block_requeue import (
    GitTargetError,
    resolve_git_target,
    snapshot_commits_ahead,
)

_PROJECT = "spelling-proj"
_TASK = "CODER-20260101-001-example-task"


def _git(repo: pathlib.Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)
    return done.stdout.strip()


def _record(feature_branch: str) -> BlockRecord:
    """A Block Record whose evidence names *feature_branch*."""
    return BlockRecord(
        reason_code="per_task_timeout",
        owner_role="CODER",
        attempts=1,
        first_blocked_at="2026-01-01T00:00:00+00:00",
        last_blocked_at="2026-01-01T00:00:00+00:00",
        cooldown_until="none",
        evidence=f"branch={feature_branch}",
        resume="none",
    )


def _build(
    tmp_path: pathlib.Path,
    *,
    branch_prefix: str,
    mainline: str,
    source_written: str,
    feature_commits: int = 0,
) -> tuple:
    """A kanban root with one task whose dev tree's only mainline is *mainline*.

    The feature branch ``<branch_prefix>feature/<task>`` is cut from the
    mainline and carries *feature_commits* commits. Returns
    ``(task_dir, feature_branch)``.
    """
    repo = tmp_path / "repo"
    project_root = tmp_path / "kanban" / "projects" / _PROJECT
    task_dir = project_root / "tasks" / _TASK
    (task_dir / "artifacts").mkdir(parents=True)
    (project_root / "project.cfg").write_text(
        f"[project]\nproject_name = {_PROJECT}\ndev_tree_path = {repo}\nbranch_prefix = {branch_prefix}\n",
        encoding="utf-8",
    )
    feature = f"{branch_prefix}feature/{_TASK}"
    (task_dir / "README.md").write_text(
        f"# Task\n\n## Task ID\n{_TASK}\n\n## Role\nCODER\n\n## Workflow Type\nrelease\n\n"
        f"## Source Branch\n{source_written}\n\n## Feature Branch\n{feature}\n\n## Goal\nDo the thing.\n",
        encoding="utf-8",
    )

    repo.mkdir()
    _git(repo, "init", "-q", "-b", mainline)
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "fixture")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "base.txt")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", feature)
    for n in range(feature_commits):
        (repo / f"work{n}.txt").write_text(f"{n}\n", encoding="utf-8")
        _git(repo, "add", f"work{n}.txt")
        _git(repo, "commit", "-q", "-m", f"work {n}")
    _git(repo, "checkout", "-q", mainline)
    return task_dir, feature


@pytest.mark.parametrize(
    "branch_prefix, mainline, source_written",
    [
        pytest.param("ai_", "ai_main", "main", id="bare-main-on-ai_-project"),
        pytest.param("ai_", "ai_main", "ai_main", id="prefixed-main-on-ai_-project"),
        pytest.param("", "main", "main", id="main-on-unprefixed-project"),
    ],
)
def test_source_branch_resolves_to_the_real_mainline(
    tmp_path: pathlib.Path, branch_prefix: str, mainline: str, source_written: str
) -> None:
    """Either mainline spelling on the ticket resolves to the branch the dev tree has."""
    task_dir, feature = _build(
        tmp_path, branch_prefix=branch_prefix, mainline=mainline, source_written=source_written
    )
    target = resolve_git_target(task_dir, _record(feature))
    assert target.source == mainline
    assert target.source_written == source_written
    assert target.branch == feature and target.branch_present


def test_bare_main_ticket_snapshots_commits_ahead_of_the_prefixed_mainline(tmp_path: pathlib.Path) -> None:
    """The requeue snapshot of a bare-main ticket counts the feature commits against ai_main."""
    task_dir, feature = _build(
        tmp_path, branch_prefix="ai_", mainline="ai_main", source_written="main", feature_commits=2
    )
    evidence_dir = task_dir / "artifacts" / "block-evidence"
    evidence_dir.mkdir()
    snap = snapshot_commits_ahead(
        task_dir, evidence_dir, _record(feature),
        now=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
    )
    assert snap.source == "ai_main"
    assert snap.ahead == 2
    assert snap.branch_present


def test_mainline_missing_under_both_spellings_is_refused(tmp_path: pathlib.Path) -> None:
    """A ticket naming a mainline the dev tree lacks under either spelling is refused by name."""
    task_dir, feature = _build(
        tmp_path, branch_prefix="ai_", mainline="trunk", source_written="main"
    )
    with pytest.raises(GitTargetError, match=r"source branch main does not exist"):
        resolve_git_target(task_dir, _record(feature))
