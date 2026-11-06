"""
test_discovery_refile_loop.py
=============================
The re-file loop detector, ``pgai_agent_kanban.discovery.refile_loop``, and
the set-wide same-defect match it groups by,
``pgai_agent_kanban.lib.bug_filing.same_defect_matches``:

  - same_defect_matches pairs bugs whose slugs differ only by a re-file
    marker (both ways) and a bug with the live bug its body cites (one
    way); a passing mention, a status remark, a self-citation and a
    real-word slug difference pair nothing;
  - refile_groups keeps the lowest-numbered member, or the lowest-numbered
    ``running`` one, never closes a ``running`` bug, joins bugs linked
    through a third, leaves distinct bugs alone -- including two whose
    slugs differ only by a number mid-slug -- and writes nothing;
  - ``close`` sets each duplicate's status and a close note naming the kept
    ID, narrates one stderr line per closure naming both IDs, prints
    nothing on stdout, and writes nothing on a second run; outside a
    projects layout it warns and writes nothing;
  - ``list`` prints each planned closure; a wrong argument count exits 2.

Every fixture is a throwaway kanban root under pytest's tmp_path, and every
subprocess runs with all five kanban-root variables set or cleared.
Fixture bug IDs are in the fictitious BUG-9xxx range.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

from pgai_agent_kanban.discovery import refile_loop
from pgai_agent_kanban.lib.bug_filing import iter_live_bugs, same_defect_matches
from pgai_agent_kanban.task_status import section_value

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_PROJECT = "refile-unit"
_MODULE = "pgai_agent_kanban.discovery.refile_loop"
_ORDINAL_SLUGS = [
    "BUG-9201-lock-leak.md",
    "BUG-9202-lock-leak-again.md",
    "BUG-9203-second-lock-leak.md",
    "BUG-9204-lock-leak-3.md",
    "BUG-9205-lock-leak-redux.md",
]


def _bugs_dir(tmp_path: pathlib.Path, project: str = _PROJECT) -> pathlib.Path:
    """A sandbox kanban root holding one project; returns its bugs/ directory."""
    root = tmp_path / "kanban"
    project_root = root / "projects" / project
    for sub in ("bugs", "priority", "requirements", "tasks/queues"):
        (project_root / sub).mkdir(parents=True, exist_ok=True)
    (root / "tmp").mkdir(exist_ok=True)
    (root / "projects.cfg").write_text(f"[project:{project}]\npriority = 1\nenabled = true\n", encoding="utf-8")
    return project_root / "bugs"


def _bug(bugs_dir: pathlib.Path, name: str, status: str = "open", symptom: str = "synthetic") -> pathlib.Path:
    path = bugs_dir / name
    path.write_text(
        f"# {name[:-3]}\n\n**Severity:** medium\n\n## Status\n{status}\n\n## Symptom\n{symptom}\n", encoding="utf-8"
    )
    return path


def _env(root: pathlib.Path) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PGAI_TASKS_DIR", None)
    env.pop("PGAI_PROJECT_ROOT", None)
    env["KANBAN_ROOT"] = str(root)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(root / "tmp")
    env["PYTHONPATH"] = str(_TEAM_DIR)
    return env


def _run(root: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", _MODULE, *args],
        capture_output=True,
        text=True,
        env=_env(root),
        cwd=str(root),
        timeout=60,
    )


def _tree_bytes(path: pathlib.Path) -> dict[str, bytes]:
    return {str(f): f.read_bytes() for f in sorted(path.rglob("*")) if f.is_file()}


def _ids(bugs) -> list[str]:
    return [bug.bug_id for bug in bugs]


def _pairs(bugs_dir: pathlib.Path) -> set[tuple[str, str, str]]:
    return {
        (bug.bug_id, match.bug.bug_id, match.rule)
        for bug, match in same_defect_matches(list(iter_live_bugs(str(bugs_dir))))
    }


# ---------------------------------------------------------------------------
# same_defect_matches
# ---------------------------------------------------------------------------


def test_slugs_differing_only_by_a_count_word_match_both_ways(tmp_path) -> None:
    bugs = tmp_path / "bugs"
    bugs.mkdir()
    _bug(bugs, "BUG-9201-lock-leak.md")
    _bug(bugs, "BUG-9202-lock-leak-again.md")
    _bug(bugs, "BUG-9203-lock-file-stale.md")

    assert _pairs(bugs) == {("BUG-9201", "BUG-9202", "slug"), ("BUG-9202", "BUG-9201", "slug")}


def test_a_citation_matches_one_way_only(tmp_path) -> None:
    bugs = tmp_path / "bugs"
    bugs.mkdir()
    _bug(bugs, "BUG-9201-cache-stale.md")
    _bug(bugs, "BUG-9202-cache-miss.md", symptom="Same defect as BUG-9201, still happening.")

    assert _pairs(bugs) == {("BUG-9202", "BUG-9201", "cites")}


@pytest.mark.parametrize(
    "symptom",
    [
        "Fails as BUG-9201 did before it.",
        "See also BUG-9201.",
        "This is not the same defect as BUG-9201.",
        "Same defect as BUG-9202 -- this very bug.",
        "Related: BUG-9201 is still open, but this is a different defect.",
    ],
    ids=["passing-mention", "see-also", "negated", "self-citation", "status-remark"],
)
def test_a_passing_mention_negation_or_self_citation_matches_nothing(tmp_path, symptom) -> None:
    bugs = tmp_path / "bugs"
    bugs.mkdir()
    _bug(bugs, "BUG-9201-port-clash.md")
    _bug(bugs, "BUG-9202-socket-timeout.md", symptom=symptom)

    assert _pairs(bugs) == set()


# ---------------------------------------------------------------------------
# refile_groups
# ---------------------------------------------------------------------------


def test_five_ordinal_slugs_keep_the_lowest_numbered_and_close_four(tmp_path) -> None:
    bugs = _bugs_dir(tmp_path)
    for name in reversed(_ORDINAL_SLUGS):
        _bug(bugs, name)
    before = _tree_bytes(bugs.parent)

    [group] = refile_loop.refile_groups(bugs)

    assert group.kept.bug_id == "BUG-9201"
    assert _ids(group.members) == ["BUG-9201", "BUG-9202", "BUG-9203", "BUG-9204", "BUG-9205"]
    assert [c.bug.bug_id for c in group.closures] == ["BUG-9202", "BUG-9203", "BUG-9204", "BUG-9205"]
    assert all(c.note.startswith("duplicate of BUG-9201 (BUG-9201-lock-leak): ") for c in group.closures)
    assert _tree_bytes(bugs.parent) == before


def test_a_running_member_is_kept_and_never_closed(tmp_path) -> None:
    bugs = _bugs_dir(tmp_path)
    _bug(bugs, "BUG-9201-lock-leak.md", "open")
    _bug(bugs, "BUG-9202-lock-leak-again.md", "needs-operator")
    _bug(bugs, "BUG-9203-lock-leak-3.md", "running")
    _bug(bugs, "BUG-9204-second-lock-leak.md", "running")

    [group] = refile_loop.refile_groups(bugs)

    assert group.kept.bug_id == "BUG-9203"
    assert [c.bug.bug_id for c in group.closures] == ["BUG-9201", "BUG-9202"]
    assert all(c.kept.bug_id == "BUG-9203" for c in group.closures)


def test_a_group_of_running_bugs_closes_nothing(tmp_path) -> None:
    bugs = _bugs_dir(tmp_path)
    _bug(bugs, "BUG-9201-lock-leak.md", "running")
    _bug(bugs, "BUG-9202-lock-leak-again.md", "running")

    [group] = refile_loop.refile_groups(bugs)

    assert group.kept.bug_id == "BUG-9201"
    assert group.closures == ()


def test_bugs_linked_through_a_third_form_one_group(tmp_path) -> None:
    bugs = _bugs_dir(tmp_path)
    _bug(bugs, "BUG-9201-cache-stale.md")
    _bug(bugs, "BUG-9202-cache-miss.md", symptom="Re-file of BUG-9201.")
    _bug(bugs, "BUG-9203-cache-miss-again.md")

    [group] = refile_loop.refile_groups(bugs)

    assert group.kept.bug_id == "BUG-9201"
    whys = {c.bug.bug_id: c.why for c in group.closures}
    assert whys["BUG-9202"] == "its body cites BUG-9201 as the defect it reports"
    assert "BUG-9202 is the same defect as BUG-9201" in whys["BUG-9203"], whys


def test_distinct_bugs_mentioning_each_other_in_passing_form_no_group(tmp_path) -> None:
    bugs = _bugs_dir(tmp_path)
    _bug(bugs, "BUG-9201-port-clash.md")
    _bug(bugs, "BUG-9202-socket-timeout.md", symptom="Fails as BUG-9201 before it.")

    assert refile_loop.refile_groups(bugs) == []


def test_slugs_differing_only_by_a_mid_slug_number_are_both_left_untouched(tmp_path) -> None:
    bugs = _bugs_dir(tmp_path)
    root = bugs.parent.parent.parent
    first = _bug(bugs, "BUG-9201-intake-exits-1-on-bad-name.md")
    second = _bug(bugs, "BUG-9202-intake-exits-2-on-bad-name.md")
    before = _tree_bytes(bugs.parent)

    assert _pairs(bugs) == set()
    assert refile_loop.refile_groups(bugs) == []
    result = _run(root, "close", str(bugs))

    assert result.returncode == 0, result.stderr
    assert result.stdout == "" and result.stderr == ""
    assert _tree_bytes(bugs.parent) == before
    assert [section_value(p.read_text(encoding="utf-8"), "Status") for p in (first, second)] == ["open", "open"]


def test_closed_bugs_take_no_part(tmp_path) -> None:
    bugs = _bugs_dir(tmp_path)
    _bug(bugs, "BUG-9201-lock-leak.md", "done")
    _bug(bugs, "BUG-9202-lock-leak-again.md", "duplicate")
    _bug(bugs, "BUG-9203-lock-leak-3.md", "open")

    assert refile_loop.refile_groups(bugs) == []
    assert refile_loop.refile_groups(tmp_path / "missing") == []


# ---------------------------------------------------------------------------
# The close command
# ---------------------------------------------------------------------------


def test_close_writes_each_duplicate_once_and_a_second_run_writes_nothing(tmp_path) -> None:
    bugs = _bugs_dir(tmp_path)
    root = bugs.parent.parent.parent
    paths = [_bug(bugs, name) for name in _ORDINAL_SLUGS]

    first = _run(root, "close", str(bugs))

    assert first.returncode == 0, first.stderr
    assert first.stdout == ""
    assert section_value(paths[0].read_text(encoding="utf-8"), "Status") == "open"
    for path in paths[1:]:
        text = path.read_text(encoding="utf-8")
        assert section_value(text, "Status") == "duplicate", text
        assert "duplicate of BUG-9201 (BUG-9201-lock-leak)" in section_value(text, "Close Note"), text
    lines = first.stderr.splitlines()
    assert len(lines) == 4, first.stderr
    for path, line in zip(paths[1:], lines):
        assert line.startswith(f"closed {path.name} as duplicate of BUG-9201 "), line

    before = _tree_bytes(bugs.parent)
    second = _run(root, "close", str(bugs))

    assert second.returncode == 0, second.stderr
    assert second.stdout == "" and second.stderr == ""
    assert _tree_bytes(bugs.parent) == before


def test_close_flips_the_backlog_marker_of_each_closed_bug(tmp_path) -> None:
    bugs = _bugs_dir(tmp_path)
    root = bugs.parent.parent.parent
    _bug(bugs, "BUG-9201-lock-leak.md")
    _bug(bugs, "BUG-9202-lock-leak-again.md")
    backlog = bugs.parent / "tasks" / "queues" / "bug_backlog.md"
    backlog.write_text(
        "# Bug Backlog\n\n## Queue\n\n- [ ] BUG-9201-lock-leak\n- [ ] BUG-9202-lock-leak-again\n", encoding="utf-8"
    )

    result = _run(root, "close", str(bugs))

    assert result.returncode == 0, result.stderr
    assert backlog.read_text(encoding="utf-8").splitlines()[-2:] == [
        "- [ ] BUG-9201-lock-leak",
        "- [x] BUG-9202-lock-leak-again",
    ]


def test_close_outside_a_projects_layout_warns_and_writes_nothing(tmp_path) -> None:
    bugs = tmp_path / "loose" / "bugs"
    bugs.mkdir(parents=True)
    _bug(bugs, "BUG-9201-lock-leak.md")
    _bug(bugs, "BUG-9202-lock-leak-again.md")
    before = _tree_bytes(bugs)

    result = _run(tmp_path, "close", str(bugs))

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr.startswith("WARNING: cannot close re-filed duplicate(s) BUG-9202-lock-leak-again.md")
    assert _tree_bytes(bugs) == before


def test_list_prints_each_planned_closure_and_its_kept_bug(tmp_path) -> None:
    bugs = _bugs_dir(tmp_path)
    kept = _bug(bugs, "BUG-9201-lock-leak.md")
    dup = _bug(bugs, "BUG-9202-lock-leak-again.md")

    result = _run(bugs.parent.parent.parent, "list", str(bugs))

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [f"{dup.resolve()}\t{kept.resolve()}"]


def test_help_exits_0_and_a_wrong_argument_count_exits_2(tmp_path) -> None:
    helped = _run(tmp_path, "--help")
    assert helped.returncode == 0
    assert "list DIR" in helped.stdout and "close DIR" in helped.stdout

    result = _run(tmp_path, "close")
    assert result.returncode == 2
    assert f"usage: python3 -m {_MODULE} close DIR" in result.stderr
