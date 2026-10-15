"""
test_promote_bundled_items_version_scope.py
===========================================
Release-time bundled-item promotion (team/scripts/cm/promote_bundled_items.py)
counts a DONE task as evidence that an item's fix shipped only when the
task's README ``## Release Version`` names the version being shipped.

Covered, on both promotion paths (the ## Bundled Items enumeration gate and
the task-scanning derivation path):

  - a DONE task of another version names a running bug: the bug is neither
    promoted nor passed by the gate -- it stays running;
  - a DONE task of the shipped version promotes its bug, whether the field
    is written ``vX.Y.Z``, ``X.Y.Z``, branch-prefixed or in bold;
  - a DONE task with no Release Version, or one reading ``none``, counts
    for nothing;
  - a version argument that names no release counts no task and says so.

The shared normaliser (pgai_agent_kanban.cm.release_version) is covered
directly too, including the forms the CM TESTER-task lookup relies on.

Fixture identifiers use the fictitious BUG-9xxx range other suites use for
synthetic fixtures.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from pgai_agent_kanban.cm.release_version import release_version_of, task_release_version, version_token

_TEAM_DIR = Path(__file__).resolve().parents[2]
_PROMOTE_SCRIPT = _TEAM_DIR / "scripts" / "cm" / "promote_bundled_items.py"

_SHIPPED = "v9.9.20"


def _load_promote_module():
    spec = importlib.util.spec_from_file_location("promote_bundled_items_version_scope", _PROMOTE_SCRIPT)
    assert spec is not None and spec.loader is not None, _PROMOTE_SCRIPT
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_pbi = _load_promote_module()


class _Project:
    """A throwaway tasks/, bugs/ and requirements/ trio under tmp_path."""

    def __init__(self, tmp_path: Path) -> None:
        self.tasks = tmp_path / "tasks"
        self.bugs = tmp_path / "bugs"
        self.requirements = tmp_path / "requirements"
        for d in (self.tasks, self.bugs, self.requirements):
            d.mkdir()

    def bug(self, name: str) -> Path:
        path = self.bugs / name
        path.write_text(f"# {name}\n\n## Status\nrunning\n", encoding="utf-8")
        return path

    def done_task(self, name: str, claims: str, release_version: "str | None") -> None:
        task_dir = self.tasks / name
        task_dir.mkdir()
        body = f"# Task\n\n## Goal\nFix {claims}.\n"
        if release_version is not None:
            body += f"\n## Release Version\n{release_version}\n"
        (task_dir / "README.md").write_text(body, encoding="utf-8")
        (task_dir / "status.md").write_text("## State\nDONE\n", encoding="utf-8")

    def bundle(self, items: list[Path]) -> Path:
        lines = ["## Status", "running", "", "## Bundled Items", ""]
        lines += [f"- {p.name} (`{p}`)" for p in items]
        path = self.requirements / f"{_SHIPPED}-bugfix-bundle-test.md"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def promote(self, bundle: Path, version: str = _SHIPPED) -> dict:
        return _pbi.promote_bundle(bundle, version, tasks_dir=self.tasks, bugs_dir=self.bugs)


def _status(path: Path) -> str:
    value = _pbi.read_status_value(path)
    assert value is not None, path
    return value.strip().lower()


# ---------------------------------------------------------------------------
# A DONE task of another release is no evidence for this one
# ---------------------------------------------------------------------------


def test_done_task_of_another_version_does_not_promote_a_bundled_bug(tmp_path: Path) -> None:
    project = _Project(tmp_path)
    bug = project.bug("BUG-9301-cancelled-fix.md")
    project.done_task("CODER-20990101-001-cancelled-fix", "BUG-9301", release_version="v9.9.19")

    result = project.promote(project.bundle([bug]))

    assert _status(bug) == "running"
    assert result["promoted"] == 0
    assert result["carried_items"] == ["BUG-9301-cancelled-fix.md"]


def test_done_task_of_another_version_does_not_promote_an_unbundled_bug(tmp_path: Path) -> None:
    project = _Project(tmp_path)
    bug = project.bug("BUG-9302-older-fix.md")
    project.done_task("CODER-20990101-002-older-fix", "BUG-9302", release_version="ai_v9.9.18")

    result = project.promote(project.bundle([]))

    assert _status(bug) == "running"
    assert result["derived_promoted"] == 0
    assert result["cannot_auto_promote"] == ["BUG-9302-older-fix.md"]


@pytest.mark.parametrize(
    "written",
    ["v9.9.20", "9.9.20", "ai_v9.9.20", "**ai_v9.9.20**", "`v9.9.20` (bugfix bundle)"],
    ids=["v-form", "bare", "branch-prefixed", "bold-prefixed", "backticked-annotated"],
)
def test_done_task_of_the_shipped_version_promotes_on_both_paths(tmp_path: Path, written: str) -> None:
    project = _Project(tmp_path)
    bundled = project.bug("BUG-9303-bundled-fix.md")
    unbundled = project.bug("BUG-9304-unbundled-fix.md")
    project.done_task("CODER-20990101-003-fix", "BUG-9303 and BUG-9304", release_version=written)

    result = project.promote(project.bundle([bundled]))

    assert _status(bundled) == "done"
    assert _status(unbundled) == "done"
    assert result["promoted"] == 1
    assert result["derived_promoted"] == 1
    assert result["carried_items"] == []


@pytest.mark.parametrize("written", [None, "none"], ids=["absent", "none"])
def test_done_task_without_a_release_version_counts_for_nothing(tmp_path: Path, written: "str | None") -> None:
    project = _Project(tmp_path)
    bundled = project.bug("BUG-9305-bundled.md")
    unbundled = project.bug("BUG-9306-unbundled.md")
    project.done_task("CODER-20990101-005-fix", "BUG-9305 and BUG-9306", release_version=written)

    result = project.promote(project.bundle([bundled]))

    assert _status(bundled) == "running"
    assert _status(unbundled) == "running"
    assert result["carried_items"] == ["BUG-9305-bundled.md"]
    assert result["cannot_auto_promote"] == ["BUG-9306-unbundled.md"]


def test_only_the_shipped_versions_task_counts_when_both_are_present(tmp_path: Path) -> None:
    project = _Project(tmp_path)
    shipped_fix = project.bug("BUG-9307-shipped.md")
    stale_fix = project.bug("BUG-9308-stale.md")
    project.done_task("CODER-20990101-007-shipped", "BUG-9307", release_version=_SHIPPED)
    project.done_task("CODER-20990101-008-stale", "BUG-9308", release_version="v9.9.17")

    result = project.promote(project.bundle([shipped_fix, stale_fix]))

    assert _status(shipped_fix) == "done"
    assert _status(stale_fix) == "running"
    assert result["carried_items"] == ["BUG-9308-stale.md"]


def test_version_argument_naming_no_release_counts_no_task_and_says_so(tmp_path: Path, capsys) -> None:
    project = _Project(tmp_path)
    bug = project.bug("BUG-9309-any.md")
    project.done_task("CODER-20990101-009-fix", "BUG-9309", release_version=_SHIPPED)

    result = project.promote(project.bundle([bug]), version="not-a-version")

    assert _status(bug) == "running"
    assert result["carried_items"] == ["BUG-9309-any.md"]
    assert "names no release version" in capsys.readouterr().out


def test_derive_shipped_item_ids_reads_only_the_named_version(tmp_path: Path) -> None:
    project = _Project(tmp_path)
    project.done_task("CODER-20990101-010-a", "BUG-9310", release_version="v9.9.20")
    project.done_task("CODER-20990101-011-b", "BUG-9311", release_version="v9.9.21")

    assert _pbi.derive_shipped_item_ids_from_tasks(project.tasks, "v9.9.20") == {"BUG-9310"}
    assert _pbi.derive_shipped_item_ids_from_tasks(project.tasks, "ai_v9.9.21") == {"BUG-9311"}
    assert _pbi.derive_shipped_item_ids_from_tasks(project.tasks, "none") == set()


def test_command_line_run_scopes_promotion_to_the_version_argument(tmp_path: Path) -> None:
    import os
    import subprocess
    import sys

    project = _Project(tmp_path)
    shipped_fix = project.bug("BUG-9312-shipped.md")
    stale_fix = project.bug("BUG-9313-stale.md")
    project.done_task("CODER-20990101-012-shipped", "BUG-9312", release_version=_SHIPPED)
    project.done_task("CODER-20990101-013-stale", "BUG-9313", release_version="v9.9.11")
    bundle = project.bundle([shipped_fix])

    run = subprocess.run(
        [sys.executable, str(_PROMOTE_SCRIPT), str(bundle), _SHIPPED, str(project.tasks), str(project.bugs)],
        capture_output=True,
        text=True,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(_TEAM_DIR)},
        timeout=60,
    )

    assert run.returncode == 0, run.stderr
    assert _status(shipped_fix) == "done"
    assert _status(stale_fix) == "running"


# ---------------------------------------------------------------------------
# The shared Release Version normaliser
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("v1.2.3", "v1.2.3"),
        ("1.2.3", "v1.2.3"),
        ("V1.2.3", "v1.2.3"),
        ("ai_v1.2.3", "v1.2.3"),
        ("ai_1.2.3", "v1.2.3"),
        ("ai_v12.3.4", "v12.3.4"),
        ("**v1.2.3** <!-- set by PM -->", "v1.2.3"),
        ("none", ""),
        ("", ""),
        ("v1.2.x", ""),
    ],
)
def test_release_version_of_reads_every_written_form(raw: str, expected: str) -> None:
    assert release_version_of(raw) == expected


def test_version_token_keeps_an_unexpected_word_as_written_for_narration() -> None:
    # The TESTER-task lookup names a non-version-shaped value in its own
    # narration, so the token keeps it rather than collapsing it.
    assert version_token("v1.2.x") == "v1.2.x"
    assert version_token("**NONE**") == "none"
    assert version_token("1.2.3.") == "v1.2.3"


def test_task_release_version_reads_the_readme_field(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("# Task\n\n## Release Version\n<!-- vX.Y.Z -->\nai_v2.0.1\n", encoding="utf-8")
    assert task_release_version(readme) == "v2.0.1"
    assert task_release_version(tmp_path / "missing.md") == ""
