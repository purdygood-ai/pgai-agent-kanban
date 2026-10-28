"""
test_promote_bundled_items_plan_fields.py
=========================================
Ship-time promotion (team/scripts/cm/promote_bundled_items.py) of the two
outcomes a PM plan records:

  - A bundled item at ``needs-operator`` is never promoted: its file is
    left byte-identical, it is reported as left for the operator, and it
    is in neither the carried list nor the cannot-auto-promote list --
    even when a DONE task of the release names it, on the enumeration path
    and on the derivation path alike. The other items close as usual.
  - A requirement RC's ride-along bugs, listed in the requirement's own
    ``## Bundled Items`` with the ``— ride-along`` suffix, are promoted
    when a DONE task of the release claims them and carried when none
    does, exactly as bundle items; the requirement itself closes.

Fixture bug IDs are in the fictitious BUG-9xxx range.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_TEAM_DIR = Path(__file__).resolve().parents[2]
_PROMOTE_SCRIPT = _TEAM_DIR / "scripts" / "cm" / "promote_bundled_items.py"
_spec = importlib.util.spec_from_file_location("promote_bundled_items_plan_fields", _PROMOTE_SCRIPT)
_pbi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pbi)

_VERSION = "v9.9.9"


def _dirs(tmp_path: Path) -> tuple[Path, Path, Path]:
    dirs = tuple(tmp_path / name for name in ("tasks", "bugs", "requirements"))
    for d in dirs:
        d.mkdir()
    return dirs  # type: ignore[return-value]


def _item(bugs_dir: Path, name: str, status: str) -> Path:
    path = bugs_dir / name
    path.write_text(f"# {name}\n\n## Status\n{status}\n\n## Symptom\nsynthetic\n", encoding="utf-8")
    return path


def _task(tasks_dir: Path, name: str, body: str) -> None:
    task_dir = tasks_dir / name
    task_dir.mkdir()
    (task_dir / "README.md").write_text(f"# Task\n{body}\n## Release Version\n{_VERSION}\n", encoding="utf-8")
    (task_dir / "status.md").write_text("## State\nDONE\n", encoding="utf-8")


def _listing(path: Path, suffix: str = "") -> str:
    return f"- {path.name} (`{path}`){suffix}\n"


def test_a_needs_operator_item_is_left_untouched_and_never_carried(tmp_path, capsys) -> None:
    tasks_dir, bugs_dir, req_dir = _dirs(tmp_path)
    fixed = _item(bugs_dir, "BUG-9841-fixed-thing.md", "running")
    waiting = _item(bugs_dir, "BUG-9842-waiting-thing.md", "needs-operator")
    other = _item(bugs_dir, "BUG-9843-other-thing.md", "running")
    # The ticket names every bundled item -- the waiting one too.
    _task(tasks_dir, "CODER-20990101-001-fix", f"## Inputs\n- {fixed}\n- {other}\n- {waiting}\n")
    bundle = req_dir / "v9.9.9-bugfix-bundle-20990101.md"
    bundle.write_text(
        "# bundle\n\n## Status\nrunning\n\n## Bundled Items\n\n"
        + _listing(fixed) + _listing(waiting) + _listing(other),
        encoding="utf-8",
    )
    waiting_before = waiting.read_bytes()

    result = _pbi.promote_bundle(bundle, _VERSION, tasks_dir=tasks_dir, bugs_dir=bugs_dir)
    out = capsys.readouterr().out

    assert _pbi.read_status_value(fixed) == "done"
    assert _pbi.read_status_value(other) == "done"
    assert waiting.read_bytes() == waiting_before
    assert result["needs_operator_items"] == [waiting.name]
    assert result["carried_items"] == []
    assert result["cannot_auto_promote"] == []
    assert f"left for the operator (needs-operator): {waiting.name}" in out
    report = out[out.index("post-ship report"):]
    assert waiting.name not in report
    assert "1 needs-operator (left for the operator)" in out
    assert _pbi.read_status_value(bundle) == "done"


def test_the_derivation_path_never_promotes_a_needs_operator_bug(tmp_path) -> None:
    tasks_dir, bugs_dir, req_dir = _dirs(tmp_path)
    waiting = _item(bugs_dir, "BUG-9844-unlisted-waiting.md", "needs-operator")
    _task(tasks_dir, "CODER-20990101-002-fix", f"Fixes {waiting.stem}.\n")
    requirement = req_dir / "v9.9.9-feature.md"
    requirement.write_text("# feature\n\n## Status\nrunning\n", encoding="utf-8")
    before = waiting.read_bytes()

    result = _pbi.promote_bundle(requirement, _VERSION, tasks_dir=tasks_dir, bugs_dir=bugs_dir)

    assert waiting.read_bytes() == before
    assert result["derived_promoted"] == 0
    assert result["cannot_auto_promote"] == []


def test_ride_alongs_are_promoted_or_carried_exactly_as_bundle_items(tmp_path, capsys) -> None:
    tasks_dir, bugs_dir, req_dir = _dirs(tmp_path)
    claimed = _item(bugs_dir, "BUG-9845-claimed-ride.md", "running")
    unclaimed = _item(bugs_dir, "BUG-9846-unclaimed-ride.md", "running")
    _task(tasks_dir, "CODER-20990101-003-feature", f"## Inputs\n- {claimed}\n")
    requirement = req_dir / "v9.9.9-feature.md"
    requirement.write_text(
        "# feature\n\n## Status\nrunning\n\n## Deliverables\n1. A feature.\n\n## Bundled Items\n\n"
        + _listing(claimed, " — ride-along") + _listing(unclaimed, " — ride-along"),
        encoding="utf-8",
    )

    result = _pbi.promote_bundle(requirement, _VERSION, tasks_dir=tasks_dir, bugs_dir=bugs_dir)
    out = capsys.readouterr().out

    assert _pbi.read_status_value(claimed) == "done"
    assert _pbi.read_status_value(unclaimed) == "running"
    assert result["promoted"] == 1
    assert result["carried_items"] == [unclaimed.name]
    assert "Carried (not closed): 1 item(s)" in out
    assert _pbi.read_status_value(requirement) == "done"
