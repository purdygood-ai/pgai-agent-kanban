"""
test_overwatch_sweep_check_scope.py
===================================
``scripts/overwatch-sweep.sh`` runs a check whose module header declares
``# OVERWATCH-CHECK-SCOPE: kanban-only`` only for the kanban's own project --
the one whose dev tree holds ``install.sh`` and
``team/pgai_agent_kanban/__init__.py`` -- and records every other project as
not applicable, never as an error.

Covered:
  1. ``--only changelog-drift`` on a project whose dev tree has no ``team/``
     (on its main branch, with a CHANGELOG.md and a bug ledger, so the check
     would otherwise reach regeneration): no "team/ directory not found"
     line, no ``changelog-drift-regen-error`` row, one ``check-not-applicable``
     row naming the check, and the sweep counts 0 errors.
  2. A project that names no dev tree at all is not applicable too.
  3. Two registered projects, one ``--only version-divergence`` run, the
     kanban's installed VERSION behind its dev tree: exactly one
     version-divergence record, for the kanban project, reporting the
     divergence; none for the other project.
  4. For the kanban's own project the sweep still runs changelog-drift: a
     stale CHANGELOG.md on the main branch is regenerated and committed.
  5. ``--help`` names exactly changelog-drift and version-divergence as the
     kanban-only checks, so every other check still runs for every project.

Every fixture lives under pytest's tmp_path: a throwaway kanban root and
throwaway dev trees. Nothing reads or writes the live install or a remote.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

from tests.fixtures.binary_guard import require_binary
from tests.unit.shell_harness import run_bash

_SWEEP = "scripts/overwatch-sweep.sh"
_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Every fixture here builds a real git dev tree."""
    require_binary("git", label="overwatch-sweep-check-scope")


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=scope-test", "-c", "user.email=scope-test@example.invalid", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _plain_tree(path: pathlib.Path, *, tag: str = "v0.33.2") -> pathlib.Path:
    """A managed project's dev tree that is not the kanban: on ``main``, with
    a committed CHANGELOG.md and a release tag, and no ``team/``."""
    _git("init", "-q", "-b", "main", str(path))
    (path / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
    _git("-C", str(path), "add", "-A")
    _git("-C", str(path), "commit", "-q", "-m", "initial")
    _git("-C", str(path), "tag", tag)
    return path


def _kanban_tree(path: pathlib.Path, *, tag: str = "ai_v2.50.0") -> pathlib.Path:
    """A dev tree shaped like the kanban source: ``install.sh`` and the
    ``team/pgai_agent_kanban`` package, on ``ai_main``, tagged."""
    _git("init", "-q", "-b", "ai_main", str(path))
    (path / "install.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (path / "team" / "pgai_agent_kanban").mkdir(parents=True)
    (path / "team" / "pgai_agent_kanban" / "__init__.py").write_text("", encoding="utf-8")
    _git("-C", str(path), "add", "-A")
    _git("-C", str(path), "commit", "-q", "-m", "initial")
    _git("-C", str(path), "tag", tag)
    return path


class _Fleet:
    """A throwaway kanban root with any number of registered projects."""

    def __init__(self, root: pathlib.Path, *, installed_version: str = "v2.49.2") -> None:
        self.root = root
        self.projects: list[str] = []
        for sub in ("locks", "logs", "tmp"):
            (root / sub).mkdir(parents=True)
        (root / "kanban.cfg").write_text("[wake]\n", encoding="utf-8")
        (root / "VERSION").write_text(installed_version + "\n", encoding="utf-8")

    def add(self, name: str, dev_tree: pathlib.Path | None, *, branch_prefix: str = "") -> pathlib.Path:
        proj = self.root / "projects" / name
        (proj / "overwatch" / "backups").mkdir(parents=True)
        (proj / "overwatch" / "actions.log").write_text("", encoding="utf-8")
        (proj / "bugs").mkdir()
        (proj / "release-state.md").write_text(
            "# Release State\n\n## Active RC\nnone\n\n## Last Released\nnone\n\n## RC Opened At\nnone\n",
            encoding="utf-8",
        )
        cfg = f"[project]\nproject_name = {name}\nbranch_prefix = {branch_prefix}\npush_to_remote = false\n"
        if dev_tree is not None:
            cfg += f"dev_tree_path = {dev_tree}\n"
        (proj / "project.cfg").write_text(cfg, encoding="utf-8")
        self.projects.append(name)
        (self.root / "projects.cfg").write_text(
            "".join(f"[project:{p}]\npriority = {i + 1}\n\n" for i, p in enumerate(self.projects)),
            encoding="utf-8",
        )
        return proj

    def env(self) -> dict:
        return {
            "KANBAN_ROOT": str(self.root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.root / "tmp"),
            "HALT_OVERWATCH": "",
        }

    def rows(self, name: str) -> list:
        """The project's action-log rows as (name, target, action, backup, reason)."""
        text = (self.root / "projects" / name / "overwatch" / "actions.log").read_text(encoding="utf-8")
        return [tuple(line.split("\t")[1:6]) for line in text.splitlines() if line.strip()]

    def sweep_log(self, name: str) -> str:
        path = self.root / "projects" / name / "logs" / "overwatch" / "sweep.log"
        return path.read_text(encoding="utf-8") if path.is_file() else ""


def _sweep(fleet: _Fleet, tmp_path: pathlib.Path, args: str):
    # The sweep resolves a project's dev tree from its own project.cfg; the
    # suite already clears an inherited PGAI_DEV_TREE_PATH (conftest).
    return run_bash(tmp_path, f"bash {_SWEEP} {args}", extra_env=fleet.env(), timeout=300)


def _not_applicable(rows: list, slug: str) -> list:
    return [
        r for r in rows if r[0] == "overwatch-sweep" and r[2] == "check-not-applicable" and r[4].startswith(f"{slug}:")
    ]


# ---------------------------------------------------------------------------
# 1-2. changelog-drift on a project that is not the kanban
# ---------------------------------------------------------------------------


def test_changelog_drift_is_not_applicable_to_a_project_without_team_dir(tmp_path):
    fleet = _Fleet(tmp_path / "kanban")
    dev = _plain_tree(tmp_path / "repos" / "video")
    fleet.add("video-proj", dev)

    result = _sweep(fleet, tmp_path, "--only changelog-drift")

    assert result.returncode == 0, result.stderr
    assert "team/ directory not found" not in result.stderr, result.stderr
    rows = fleet.rows("video-proj")
    assert [r for r in rows if r[2] == "changelog-drift-regen-error"] == [], rows
    assert [r for r in rows if r[0] == "check-changelog-drift"] == [], "the check ran for a non-kanban project"
    skipped = _not_applicable(rows, "changelog-drift")
    assert len(skipped) == 1, rows
    assert str(dev) in skipped[0][4], "the not-applicable row should name the dev tree it judged"
    assert (
        "overwatch-sweep",
        "video-proj",
        "sweep-end",
        "none",
        "0 check(s) ok, 0 check(s) error, 1 check(s) not applicable",
    ) in rows, rows
    assert "\tcheck-not-applicable\tnone\tchangelog-drift" in fleet.sweep_log("video-proj")
    assert "\tcheck-error\t" not in fleet.sweep_log("video-proj")
    assert "1 project(s) swept, 0 error(s)" in result.stderr, result.stderr


def test_a_project_with_no_dev_tree_is_not_applicable(tmp_path):
    fleet = _Fleet(tmp_path / "kanban")
    fleet.add("docs-proj", None)

    result = _sweep(fleet, tmp_path, "--only changelog-drift")

    assert result.returncode == 0, result.stderr
    skipped = _not_applicable(fleet.rows("docs-proj"), "changelog-drift")
    assert len(skipped) == 1 and "names no dev_tree_path" in skipped[0][4], fleet.rows("docs-proj")
    assert "cannot resolve dev tree path" not in result.stderr, result.stderr


# ---------------------------------------------------------------------------
# 3. version-divergence: one record, for the kanban project only
# ---------------------------------------------------------------------------


def test_version_divergence_records_only_for_the_kanban_project(tmp_path):
    fleet = _Fleet(tmp_path / "kanban", installed_version="v2.49.2")
    fleet.add("kanban-proj", _kanban_tree(tmp_path / "repos" / "kanban", tag="ai_v2.50.0"), branch_prefix="ai_")
    fleet.add("video-proj", _plain_tree(tmp_path / "repos" / "video", tag="v0.33.2"))

    result = _sweep(fleet, tmp_path, "--only version-divergence")

    assert result.returncode == 0, result.stderr
    kanban_records = [r for r in fleet.rows("kanban-proj") if r[0] == "check-version-divergence"]
    other_records = [r for r in fleet.rows("video-proj") if r[0] == "check-version-divergence"]
    assert len(kanban_records) == 1, fleet.rows("kanban-proj")
    assert other_records == [], f"a non-kanban project got a version-divergence record: {other_records}"
    # The kanban's installed VERSION is behind its dev tree: still reported.
    assert kanban_records[0][2] == "report:version-divergence", kanban_records
    assert "installed=v2.49.2" in kanban_records[0][4] and "ai_v2.50.0" in kanban_records[0][4]
    assert len(_not_applicable(fleet.rows("video-proj"), "version-divergence")) == 1, fleet.rows("video-proj")
    assert "v0.33.2" not in "".join(
        r[4] for r in fleet.rows("video-proj")
    ), "the kanban VERSION was compared with the project's tag"


# ---------------------------------------------------------------------------
# 4. changelog-drift for the kanban's own project still regenerates
# ---------------------------------------------------------------------------


def test_kanban_project_changelog_drift_still_regenerates_and_commits(tmp_path):
    fleet = _Fleet(tmp_path / "kanban")
    dev = tmp_path / "repos" / "kanban"
    _git("init", "-q", "-b", "ai_main", str(dev))
    (dev / "install.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    # The real package, so the check's changelog_writer import resolves.
    os.symlink(str(_TEAM_DIR), str(dev / "team"))
    (dev / "release-notes").mkdir()
    (dev / "release-notes" / "PUBLISHED").write_text("", encoding="utf-8")
    (dev / "release-notes" / "v0.1.0.md").write_text(
        "# Release v0.1.0\n\n## Summary\nInitial release.\n\n## Implemented\n- Initial feature.\n", encoding="utf-8"
    )
    (dev / "CHANGELOG.md").write_text("# STALE\n", encoding="utf-8")
    _git("-C", str(dev), "add", "-A")
    _git("-C", str(dev), "commit", "-q", "-m", "stale changelog")
    head_before = _git("-C", str(dev), "rev-parse", "HEAD")
    proj = fleet.add("kanban-proj", dev, branch_prefix="ai_")
    (proj / "bugs" / "BUG-9999-scope-fixture.md").write_text(
        "# BUG-9999-scope-fixture\n\n## Status\nopen\n\n## Symptom\nFixture.\n", encoding="utf-8"
    )

    result = _sweep(fleet, tmp_path, "--only changelog-drift")

    assert result.returncode == 0, result.stderr
    assert "running changelog-drift" in result.stderr, result.stderr
    rows = fleet.rows("kanban-proj")
    assert _not_applicable(rows, "changelog-drift") == [], rows
    assert [
        r for r in rows if r[0] == "check-changelog-drift" and r[2] == "changelog-drift-fixed"
    ], f"the kanban project's drift was not regenerated and committed:\n{rows}\n{result.stderr}"
    assert _git("-C", str(dev), "rev-parse", "HEAD") != head_before
    assert (dev / "CHANGELOG.md").read_text(encoding="utf-8") != "# STALE\n"


# ---------------------------------------------------------------------------
# 5. which checks are kanban-only
# ---------------------------------------------------------------------------


def test_help_names_exactly_the_kanban_only_checks(tmp_path):
    result = run_bash(tmp_path, f"bash {_SWEEP} --help", timeout=60)

    assert result.returncode == 0, result.stderr
    assert "Kanban-only checks: changelog-drift, version-divergence." in result.stdout, result.stdout
