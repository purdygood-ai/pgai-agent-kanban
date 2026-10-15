"""
test_release_tag_prefix_lookups.py
==================================
Every release-tag lookup and push names the tag the release cuts:
``<branch_prefix><version>`` (``ai_v1.2.3`` with ``branch_prefix = ai_``,
``v1.2.3`` with none) -- the rule ``pp_prefix_tag`` and its Python
counterpart ``release_tag_name`` share.

Covered:

  - release_tag_name builds the same name pp_prefix_tag does;
  - the OVERWATCH resolution matrix's ``shipped_or_tagged`` fact sees a
    prefixed release tag, ignores another lane's bare tag on a prefixed
    project, and still sees the bare tag on an unprefixed one;
  - cm/open-rc.sh Guard 1 refuses a version whose prefixed release tag
    already exists locally (and the bare tag on an unprefixed project, as
    before);
  - cm/push-watchdog.sh, against a local bare repository standing in as
    origin, reports an unpushed prefixed release tag as missing and pushes
    exactly that ref, never an unrelated local vA.B.C tag.

Every fixture lives under pytest's tmp_path; subprocess environments are
built by build_scrubbed_env and point every kanban-root variable at a
throwaway root. The only remote any test pushes to is a bare repository the
test created itself under tmp_path.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from pgai_agent_kanban.lib.branch_prefix import release_tag_name
from pgai_agent_kanban.overwatch.resolution_matrix import (
    CANCEL_AND_REBUNDLE,
    HALT_HARD,
    resolve_block,
)
from tests.fixtures.binary_guard import require_binary
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.unit.shell_harness import run_bash
from tests.unit.test_resolution_matrix import _git, _golden_evidence

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_OPEN_RC = _TEAM_DIR / "scripts" / "cm" / "open-rc.sh"
_WATCHDOG = "scripts/cm/push-watchdog.sh"
_PROJECT = "tag-prefix-project"


@pytest.fixture(autouse=True)
def _require_git() -> None:
    require_binary("git", label="release-tag-prefix-lookups")


# ---------------------------------------------------------------------------
# release_tag_name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "prefix", "expected"),
    [("v1.2.3", "ai_", "ai_v1.2.3"), ("v1.2.3", "", "v1.2.3")],
    ids=["prefixed", "unprefixed"],
)
def test_release_tag_name_matches_the_prefix_tag_rule(version: str, prefix: str, expected: str) -> None:
    assert release_tag_name(version, prefix) == expected


# ---------------------------------------------------------------------------
# OVERWATCH resolution matrix: shipped_or_tagged
# ---------------------------------------------------------------------------


def test_prefixed_release_tag_counts_as_tagged_and_resolves_to_a_hard_halt(tmp_path: pathlib.Path) -> None:
    evidence = _golden_evidence(tmp_path, branch_prefix="ai_")
    _git(pathlib.Path(evidence.dev_tree_path), "tag", "ai_v2.0.1", "ai_main")

    result = resolve_block(evidence)

    assert result.facts.shipped_or_tagged is True
    assert result.resolution == HALT_HARD


def test_another_lanes_bare_tag_is_not_a_prefixed_projects_release(tmp_path: pathlib.Path) -> None:
    evidence = _golden_evidence(tmp_path, branch_prefix="ai_")
    _git(pathlib.Path(evidence.dev_tree_path), "tag", "v2.0.1", "ai_main")

    result = resolve_block(evidence)

    assert result.facts.shipped_or_tagged is False
    assert result.resolution == CANCEL_AND_REBUNDLE


@pytest.mark.parametrize("prefix", [None, ""], ids=["prefix-not-given", "empty-prefix"])
def test_unprefixed_project_still_sees_its_bare_release_tag(tmp_path: pathlib.Path, prefix) -> None:
    evidence = _golden_evidence(tmp_path, branch_prefix=prefix)
    _git(pathlib.Path(evidence.dev_tree_path), "tag", "v2.0.1", "ai_main")

    result = resolve_block(evidence)

    assert result.facts.shipped_or_tagged is True
    assert result.resolution == HALT_HARD


# ---------------------------------------------------------------------------
# Shared sandbox: a throwaway kanban root holding one project
# ---------------------------------------------------------------------------


def _git_run(*args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=tag-test", "-c", "user.email=tag-test@example.invalid", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class _Sandbox:
    """A kanban root with one registered project whose dev tree is a real
    git repository; *origin* (a bare repository under tmp_path) is its
    remote when *with_origin* is set."""

    def __init__(self, tmp_path: pathlib.Path, *, prefix: str, push: bool, with_origin: bool = False) -> None:
        self.tmp_path = tmp_path
        self.prefix = prefix
        self.root = tmp_path / "kanban"
        self.project_dir = self.root / "projects" / _PROJECT
        self.project_dir.mkdir(parents=True)
        (self.root / "tmp").mkdir()
        (self.root / "logs").mkdir()
        (tmp_path / "home").mkdir()
        (self.root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\n", encoding="utf-8")

        self.main = f"{prefix}main"
        self.dev = tmp_path / "dev"
        _git_run("init", "-q", "-b", self.main, str(self.dev))
        _git_run("-C", str(self.dev), "commit", "-q", "--allow-empty", "-m", "initial")
        self.origin = tmp_path / "origin.git"
        if with_origin:
            _git_run("init", "-q", "--bare", str(self.origin))
            _git_run("-C", str(self.dev), "remote", "add", "origin", str(self.origin))
            _git_run("-C", str(self.dev), "push", "-q", "origin", self.main)

        cfg = f"[project]\nproject_name = {_PROJECT}\ndev_tree_path = {self.dev}\nbranch_prefix = {prefix}\n"
        if not push:
            cfg += "push_to_remote = false\n"
        (self.project_dir / "project.cfg").write_text(cfg, encoding="utf-8")
        (self.project_dir / "release-state.md").write_text(
            "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n"
            "## RC Opened By Task\nnone\n\n## Last Released\nnone\n",
            encoding="utf-8",
        )
        # Live-install layout: the CM scripts run their field readers from
        # $KANBAN_ROOT/pgai_agent_kanban/cm/, never from the dev tree.
        cm_py_dir = self.root / "pgai_agent_kanban" / "cm"
        cm_py_dir.mkdir(parents=True)
        for py_file in (_TEAM_DIR / "pgai_agent_kanban" / "cm").glob("*.py"):
            (cm_py_dir / py_file.name).write_text(py_file.read_text(encoding="utf-8"), encoding="utf-8")

    def env(self) -> dict:
        return build_scrubbed_env(
            home_dir=self.tmp_path / "home",
            extra_path_dirs=[pathlib.Path(sys.executable).parent],
            extra_env={
                "KANBAN_ROOT": str(self.root),
                "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
                "TEAM_ROOT": str(self.root),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.root / "tmp"),
                "PGAI_PROJECT_NAME": _PROJECT,
            },
        )

    def tag(self, name: str) -> None:
        _git_run("-C", str(self.dev), "tag", name)

    def branches(self) -> list[str]:
        return _git_run("-C", str(self.dev), "branch", "--format=%(refname:short)").splitlines()

    def origin_tags(self) -> list[str]:
        return _git_run("-C", str(self.origin), "tag", "--list").splitlines()

    def release_state(self) -> str:
        return (self.project_dir / "release-state.md").read_text(encoding="utf-8")

    def open_rc(self, version: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(_OPEN_RC), version],
            capture_output=True,
            text=True,
            env=self.env(),
            cwd=str(self.root),
            timeout=120,
        )

    def run_watchdog(self, *args: str) -> subprocess.CompletedProcess:
        return run_bash(
            self.tmp_path,
            " ".join(["bash", _WATCHDOG, "--project", _PROJECT, *args]),
            base_env=self.env(),
            timeout=120,
        )

    def watchdog_log(self) -> str:
        log = self.project_dir / "logs" / "cm-push-watchdog.log"
        return log.read_text(encoding="utf-8") if log.is_file() else ""


# ---------------------------------------------------------------------------
# cm/open-rc.sh Guard 1
# ---------------------------------------------------------------------------


def test_open_rc_refuses_a_version_whose_prefixed_release_tag_exists(tmp_path: pathlib.Path) -> None:
    box = _Sandbox(tmp_path, prefix="ai_", push=False)
    box.tag("ai_v1.2.3")
    state_before = box.release_state()

    result = box.open_rc("v1.2.3")

    assert result.returncode != 0, result.stdout + result.stderr
    assert "git tag 'ai_v1.2.3' already exists" in result.stderr, result.stderr
    assert "ai_rc/v1.2.3" not in box.branches()
    assert box.release_state() == state_before


def test_open_rc_on_an_unprefixed_project_refuses_its_bare_release_tag(tmp_path: pathlib.Path) -> None:
    box = _Sandbox(tmp_path, prefix="", push=False)
    box.tag("v1.2.3")

    result = box.open_rc("v1.2.3")

    assert result.returncode != 0, result.stdout + result.stderr
    assert "git tag 'v1.2.3' already exists" in result.stderr, result.stderr
    assert "rc/v1.2.3" not in box.branches()


# ---------------------------------------------------------------------------
# cm/push-watchdog.sh against a local bare origin
# ---------------------------------------------------------------------------


def test_watchdog_pushes_only_the_missing_prefixed_release_tag(tmp_path: pathlib.Path) -> None:
    box = _Sandbox(tmp_path, prefix="ai_", push=True, with_origin=True)
    box.tag("ai_v1.2.3")
    box.tag("v0.9.0")  # another lane's unpublished tag in the shared repository

    result = box.run_watchdog()

    assert result.returncode == 0, result.stderr
    log = box.watchdog_log()
    assert "Tags missing from origin: ai_v1.2.3" in log, log
    assert "refs/tags/ai_v1.2.3" in log, log
    assert box.origin_tags() == ["ai_v1.2.3"], log


def test_watchdog_dry_run_names_the_release_tag_ref(tmp_path: pathlib.Path) -> None:
    box = _Sandbox(tmp_path, prefix="ai_", push=True, with_origin=True)
    box.tag("ai_v1.2.3")
    box.tag("v0.9.0")

    result = box.run_watchdog("--dry-run")

    assert result.returncode == 0, result.stderr
    log = box.watchdog_log()
    assert "would: git -C" in log and "push origin refs/tags/ai_v1.2.3" in log, log
    assert "v0.9.0" not in log, log
    assert box.origin_tags() == []


def test_watchdog_on_an_unprefixed_project_pushes_its_bare_release_tag(tmp_path: pathlib.Path) -> None:
    box = _Sandbox(tmp_path, prefix="", push=True, with_origin=True)
    box.tag("v1.2.3")
    box.tag("ai_v0.9.0")  # a prefixed lane's tag is not this project's release

    result = box.run_watchdog()

    assert result.returncode == 0, result.stderr
    assert box.origin_tags() == ["v1.2.3"], box.watchdog_log()
