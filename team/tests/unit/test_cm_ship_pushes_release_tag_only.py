"""
test_cm_ship_pushes_release_tag_only.py
=======================================
The manual ship path (``pgai_agent_kanban.cm.ship.ship_rc``, behind
``team/scripts/ship-rc.sh`` and ``POST /operations/ship-rc``) publishes the
release it just cut and nothing else: Step 7 pushes the prefixed main
branch and the release tag's own ``refs/tags/<branch_prefix><version>``
ref, never every local tag. In a repository shared by more than one
release lane, the other lane's unpublished tags stay local.

Covered, each against a real dev tree whose origin is a local bare
repository:

  - a prefixed project (``branch_prefix = ai_``) with an unrelated local
    ``v0.9.0`` tag: origin ends up holding ``ai_v1.2.3`` and not
    ``v0.9.0``, and the Step 7 progress line names the explicit ref;
  - an unprefixed project with another lane's ``ai_v0.9.0`` tag: origin
    ends up holding the bare ``v1.2.3`` release tag only;
  - ``push_to_remote = false``: origin receives no tag at all, and the
    Step 7 skip line names the explicit ref.

Every fixture lives under pytest's tmp_path, which the test runners place
under ``PGAI_AGENT_KANBAN_TEMP_DIR``; every kanban-root variable points at
the throwaway root for the length of a test. The only remote any test
pushes to is a bare repository the test created itself under tmp_path.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.cm import ship  # noqa: E402
from pgai_agent_kanban.ops.context import OpsContext  # noqa: E402
from tests.fixtures.binary_guard import require_binary  # noqa: E402

_PROJECT = "ship-tag-project"
_VERSION = "v1.2.3"

# Fixed identity and no user or system git configuration, so the squash
# commit ship_rc makes does not depend on the host.
_GIT_ENV = {
    "GIT_AUTHOR_NAME": "PGAI Test",
    "GIT_AUTHOR_EMAIL": "test@pgai.local",
    "GIT_COMMITTER_NAME": "PGAI Test",
    "GIT_COMMITTER_EMAIL": "test@pgai.local",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}

# Names that would point a run at a live install or route the fixture's
# git through the agent-lane remote guard; removed for every test.
_AMBIENT_NAMES = (
    "PGAI_AGENT_ROLE",
    "PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE",
    "PGAI_DEV_TREE_PATH",
    "PGAI_PROJECT_NAME",
    "PGAI_PROJECT_ROOT",
    "PGAI_TASKS_DIR",
    "REPO_ROOT",
)


@pytest.fixture(autouse=True)
def _require_git() -> None:
    require_binary("git", label="cm-ship-pushes-release-tag-only")


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True, stdin=subprocess.DEVNULL
    ).stdout.strip()


class _Sandbox:
    """A throwaway kanban root holding one project mid-RC.

    The project's dev tree is a real git repository on ``<prefix>main``
    with an open ``<prefix>rc/<version>`` carrying one commit; *origin* is
    a bare repository under tmp_path holding both branches and no tag.
    """

    def __init__(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, *, prefix: str, push: bool) -> None:
        self.root = tmp_path / "kanban"
        self.project_dir = self.root / "projects" / _PROJECT
        self.project_dir.mkdir(parents=True)
        (self.root / "tmp").mkdir()
        (self.root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\n", encoding="utf-8")

        for name in _AMBIENT_NAMES:
            monkeypatch.delenv(name, raising=False)
        for name, value in {
            **_GIT_ENV,
            "KANBAN_ROOT": str(self.root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.root / "tmp"),
        }.items():
            monkeypatch.setenv(name, value)

        self.main = f"{prefix}main"
        self.rc = f"{prefix}rc/{_VERSION}"
        self.release_tag = f"{prefix}{_VERSION}"
        self.dev = tmp_path / "dev"
        _git("init", "-q", "-b", self.main, str(self.dev))
        (self.dev / "README").write_text("base\n", encoding="utf-8")
        _git("-C", str(self.dev), "add", "README")
        _git("-C", str(self.dev), "commit", "-q", "-m", "initial")
        _git("-C", str(self.dev), "checkout", "-q", "-b", self.rc)
        (self.dev / "feature.txt").write_text("released work\n", encoding="utf-8")
        _git("-C", str(self.dev), "add", "feature.txt")
        _git("-C", str(self.dev), "commit", "-q", "-m", "release work")
        _git("-C", str(self.dev), "checkout", "-q", self.main)

        self.origin = tmp_path / "origin.git"
        _git("init", "-q", "--bare", str(self.origin))
        _git("-C", str(self.dev), "remote", "add", "origin", str(self.origin))
        _git("-C", str(self.dev), "push", "-q", "origin", self.main, self.rc)
        _git("-C", str(self.dev), "fetch", "-q", "origin")

        cfg = f"[project]\nproject_name = {_PROJECT}\ndev_tree_path = {self.dev}\nbranch_prefix = {prefix}\n"
        cfg += f"push_to_remote = {'true' if push else 'false'}\n"
        (self.project_dir / "project.cfg").write_text(cfg, encoding="utf-8")
        (self.project_dir / "release-state.md").write_text(
            f"# Release State\n\n## Active RC\n{_VERSION}\n\n## RC Opened At\n2026-01-01T00:00:00+00:00\n\n"
            "## RC Opened By Task\nnone\n\n## Last Released\nnone\n",
            encoding="utf-8",
        )

    def tag(self, name: str) -> None:
        _git("-C", str(self.dev), "tag", name, self.main)

    def local_tags(self) -> list[str]:
        return _git("-C", str(self.dev), "tag", "--list").splitlines()

    def origin_tags(self) -> list[str]:
        return _git("-C", str(self.origin), "tag", "--list").splitlines()

    def origin_branches(self) -> list[str]:
        return _git("-C", str(self.origin), "branch", "--list", "--format=%(refname:short)").splitlines()

    def ship(self) -> tuple[ship.ShipOutcome, str]:
        """Run ship_rc for the sandbox project; return its outcome and stdout."""
        out: list[str] = []
        err: list[str] = []
        try:
            outcome = ship.ship_rc(
                OpsContext(kanban_root=self.root), _PROJECT, _VERSION, out=out.append, err=err.append
            )
        except ship.ShipRcError as exc:
            pytest.fail(f"ship_rc refused ({exc.error}):\n{exc.text}\nstdout:\n{''.join(out)}\nstderr:\n{''.join(err)}")
        return outcome, "".join(out)


def test_prefixed_ship_publishes_its_release_tag_and_not_another_lanes(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    box = _Sandbox(tmp_path, monkeypatch, prefix="ai_", push=True)
    box.tag("v0.9.0")  # another lane's unpublished tag in the shared repository

    outcome, out = box.ship()

    assert outcome.outcome == "shipped" and outcome.pushed is True, outcome
    assert outcome.release_tag == "ai_v1.2.3"
    origin_tags = box.origin_tags()
    assert "ai_v1.2.3" in origin_tags, out
    assert "v0.9.0" not in origin_tags, f"ship_rc published another lane's tag:\n{out}"
    assert origin_tags == ["ai_v1.2.3"], out
    assert "v0.9.0" in box.local_tags(), "the unrelated tag must stay in the dev tree, untouched"
    assert "[Step 7] Pushing ai_main and refs/tags/ai_v1.2.3 to origin...\n" in out
    assert box.origin_branches() == ["ai_main"], "origin keeps main and loses the shipped RC branch"


def test_unprefixed_ship_publishes_its_bare_release_tag(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    box = _Sandbox(tmp_path, monkeypatch, prefix="", push=True)
    box.tag("ai_v0.9.0")  # a prefixed lane's tag is not this project's release

    outcome, out = box.ship()

    assert outcome.outcome == "shipped" and outcome.pushed is True, outcome
    assert box.origin_tags() == ["v1.2.3"], out
    assert "[Step 7] Pushing main and refs/tags/v1.2.3 to origin...\n" in out


def test_local_only_ship_names_the_explicit_ref_and_publishes_nothing(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    box = _Sandbox(tmp_path, monkeypatch, prefix="ai_", push=False)
    box.tag("v0.9.0")

    outcome, out = box.ship()

    assert outcome.outcome == "shipped" and outcome.pushed is False, outcome
    assert "ai_v1.2.3" in box.local_tags()
    assert box.origin_tags() == [], out
    assert (
        f"[push_to_remote=false] skipping origin push for {_PROJECT}: git push origin ai_main refs/tags/ai_v1.2.3\n"
    ) in out
