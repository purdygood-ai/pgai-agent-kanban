"""
test_ops_ride_along_item.py
===========================
The two ops verbs the PM materializer records a plan's intake fields with:

  - ``ride_along_item`` lists an open bug in a requirement's
    ``## Bundled Items`` (creating the section at the end of the file, or
    adding to one already there), sets the bug ``running`` and its backlog
    line ``[x]``; the bundle readers then see the requirement as the bug's
    one listing, so the bug is neither carried nor orphaned while the
    requirement runs. A repeated call writes nothing.
  - It refuses -- writing nothing -- an unknown key, a priority, a bug that
    is not open, and a requirement outside the project's requirements/.
  - ``check_needs_operator`` accepts what ``set_needs_operator`` accepts and
    refuses what it refuses, with the same error key, and writes nothing.

Every test builds its own sandbox kanban root under ``tmp_path``. Bug IDs
are in the fictitious BUG-9xxx range.
"""

from __future__ import annotations

import pathlib

import pytest

from pgai_agent_kanban.lib import bundle_items
from pgai_agent_kanban.ops import write as ops_write
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.errors import ContractError
from pgai_agent_kanban.overwatch import inbox

_PROJECT = "ride-proj"
_BUG = "BUG-9821-small-glyph"


@pytest.fixture
def root(tmp_path: pathlib.Path) -> pathlib.Path:
    sandbox = tmp_path / "root"
    project_root = sandbox / "projects" / _PROJECT
    for sub in ("bugs", "priority", "requirements", "tasks/queues"):
        (project_root / sub).mkdir(parents=True)
    (sandbox / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\nenabled = true\n", encoding="utf-8")
    _bug(sandbox, _BUG, "open")
    (project_root / "tasks" / "queues" / "bug_backlog.md").write_text(
        f"# Backlog Cache\n\n## Queue\n\n- [ ] {_BUG} — small glyph\n", encoding="utf-8"
    )
    return sandbox


def _ctx(root: pathlib.Path) -> OpsContext:
    return OpsContext(kanban_root=root)


def _bug(root: pathlib.Path, stem: str, status: str) -> pathlib.Path:
    path = root / "projects" / _PROJECT / "bugs" / f"{stem}.md"
    path.write_text(
        f"# {stem}\n\n**Severity:** low\n\n## Status\n{status}\n\n## Symptom\nsynthetic\n", encoding="utf-8"
    )
    return path


def _requirement(root: pathlib.Path, extra: str = "") -> pathlib.Path:
    path = root / "projects" / _PROJECT / "requirements" / "v9.9.0-convergence.md"
    path.write_text(
        f"# v9.9.0-convergence\n\n## Status\nrunning\n\n## Deliverables\n1. Things.\n{extra}", encoding="utf-8"
    )
    return path


def _status(path: pathlib.Path) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    index = lines.index("## Status")
    return next(line.strip() for line in lines[index + 1 :] if line.strip())


def _snapshot(root: pathlib.Path) -> dict:
    project_root = root / "projects" / _PROJECT
    return {p: p.read_bytes() for p in sorted(project_root.rglob("*")) if p.is_file()}


def test_a_ride_along_is_listed_set_running_and_marked(root) -> None:
    requirement = _requirement(root)

    path = ops_write.ride_along_item(_ctx(root), _PROJECT, "BUG-9821", requirement)

    assert path.name == f"{_BUG}.md"
    assert _status(path) == "running"
    text = requirement.read_text(encoding="utf-8")
    assert text.endswith(f"\n\n## Bundled Items\n\n- {_BUG}.md (`{path}`) — ride-along\n"), text
    assert bundle_items.parse_bundled_item_paths(text) == [str(path)]
    backlog = (root / "projects" / _PROJECT / "tasks" / "queues" / "bug_backlog.md").read_text(encoding="utf-8")
    assert f"- [x] {_BUG} — small glyph" in backlog
    requirements_dir = requirement.parent
    assert path.name not in bundle_items.carried_item_names(requirements_dir)
    assert not bundle_items.is_orphaned("running", path.name, bundle_items.bundle_listings(requirements_dir))


def test_a_second_ride_along_joins_the_existing_section_and_a_repeat_writes_nothing(root) -> None:
    requirement = _requirement(root)
    second = _bug(root, "BUG-9822-other-glyph", "open")
    ctx = _ctx(root)
    first = ops_write.ride_along_item(ctx, _PROJECT, _BUG, requirement)
    ops_write.ride_along_item(ctx, _PROJECT, "BUG-9822", requirement)

    assert bundle_items.parse_bundled_item_paths(requirement.read_text(encoding="utf-8")) == [str(first), str(second)]
    before = _snapshot(root)
    ops_write.ride_along_item(ctx, _PROJECT, "BUG-9822", requirement)
    assert _snapshot(root) == before


@pytest.mark.parametrize(
    ("key", "setup", "error"),
    [
        ("BUG-9899", None, "ride_along.unknown_key"),
        ("PRIORITY-9823", "priority", "ride_along.not_bug"),
        ("BUG-9824", "running", "ride_along.bad_status"),
        ("BUG-9825", "done", "ride_along.bad_status"),
        (_BUG, "outside", "ride_along.requirement_outside"),
    ],
)
def test_a_refused_ride_along_writes_nothing(root, tmp_path, key, setup, error) -> None:
    requirement = _requirement(root)
    if setup == "priority":
        (root / "projects" / _PROJECT / "priority" / "PRIORITY-9823-feature.md").write_text(
            "# PRIORITY-9823-feature\n\n## Status\nopen\n", encoding="utf-8"
        )
    elif setup in ("running", "done"):
        _bug(root, f"{key}-thing", setup)
    elif setup == "outside":
        requirement = tmp_path / "elsewhere.md"
        requirement.write_text("# elsewhere\n\n## Status\nrunning\n", encoding="utf-8")
    before = _snapshot(root)

    with pytest.raises(ContractError) as caught:
        ops_write.ride_along_item(_ctx(root), _PROJECT, key, requirement)

    assert caught.value.error == error
    assert caught.value.remedy and "<" not in caught.value.remedy
    assert _snapshot(root) == before


def test_check_needs_operator_accepts_what_the_verb_accepts_and_writes_nothing(root) -> None:
    before = _snapshot(root)

    path = ops_write.check_needs_operator(_ctx(root), _PROJECT, "BUG-9821", "Which glyph?")

    assert path.name == f"{_BUG}.md"
    assert _snapshot(root) == before
    assert inbox.list_open(_ctx(root), _PROJECT) == ()


@pytest.mark.parametrize(
    ("key", "question", "error"),
    [
        ("BUG-9899", "Which glyph?", "needs_operator.unknown_key"),
        (_BUG, "   ", "needs_operator.empty_question"),
        ("BUG-9826", "Which glyph?", "needs_operator.item_closed"),
    ],
)
def test_check_needs_operator_refuses_as_the_verb_does(root, key, question, error) -> None:
    _bug(root, "BUG-9826-closed", "done")
    before = _snapshot(root)

    with pytest.raises(ContractError) as checked:
        ops_write.check_needs_operator(_ctx(root), _PROJECT, key, question)
    with pytest.raises(ContractError) as written:
        ops_write.set_needs_operator(_ctx(root), _PROJECT, key, question)

    assert checked.value.error == written.value.error == error
    assert _snapshot(root) == before
