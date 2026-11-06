"""
test_ride_along_lint.py
=======================
The plan lint for a PM plan's ``ride_along`` list
(team/pm-agent/lib/ride_along_lint.py):

  - Under ``bundle_mixing = ride-along``, three open low/medium bugs naming
    no deliverable file are accepted, in plan order.
  - ``bundle_mixing = separate`` (also the default with no cfg at all)
    refuses any entry; an absent or empty list is accepted without reading
    the posture.
  - More than ``ride_along_max`` distinct bugs is refused; a bug listed
    twice counts once.
  - A high, a critical, or an unrecognised severity is refused, naming the
    bug.
  - A bug that names a file the requirement's ``## Deliverables`` names is
    refused, naming the bug and the file; a same-basename file under a
    different directory is not an overlap.
  - An unknown key, a bug that is not open, and an unreadable requirement
    are refused; a bug already running and listed by this requirement is
    accepted (a re-run).

Every test builds its own sandbox kanban root under ``tmp_path``. Bug IDs
are in the fictitious BUG-9xxx range.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "ride_along_lint_under_test", _TEAM_DIR / "pm-agent" / "lib" / "ride_along_lint.py"
)
lint = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = lint  # dataclasses look their module up by name
_SPEC.loader.exec_module(lint)

_PROJECT = "ride-proj"
_DELIVERABLES = (
    "## Deliverables\n"
    "1. **Close path.** `close.sh` gains a duplicate state.\n"
    "2. **Docs.** OPERATIONS.md and `team/roles/PM.md` table rows, e.g. one per state.\n"
)


def _root(tmp_path: pathlib.Path, mixing: str | None = "ride-along", max_count: str | None = None) -> pathlib.Path:
    root = tmp_path / "root"
    project_root = root / "projects" / _PROJECT
    for sub in ("bugs", "requirements", "tasks/queues"):
        (project_root / sub).mkdir(parents=True)
    (root / "kanban.cfg").write_text("[paths]\n", encoding="utf-8")
    release = ""
    if mixing is not None:
        release += f"bundle_mixing = {mixing}\n"
    if max_count is not None:
        release += f"ride_along_max = {max_count}\n"
    (project_root / "project.cfg").write_text(
        f"[project]\nproject_name = {_PROJECT}\n\n[release]\n{release}", encoding="utf-8"
    )
    return root


def _requirement(root: pathlib.Path, extra: str = "") -> pathlib.Path:
    path = root / "projects" / _PROJECT / "requirements" / "v9.9.0-convergence.md"
    path.write_text(
        f"# v9.9.0-convergence\n\n## Status\nrunning\n\n{_DELIVERABLES}\n## Acceptance\n- works\n{extra}",
        encoding="utf-8",
    )
    return path


def _bug(root: pathlib.Path, stem: str, severity: str = "low", status: str = "open", body: str = "") -> pathlib.Path:
    path = root / "projects" / _PROJECT / "bugs" / f"{stem}.md"
    path.write_text(
        f"# {stem}\n\n**Severity:** {severity}\n\n## Status\n{status}\n\n## Symptom\n{body or 'synthetic'}\n",
        encoding="utf-8",
    )
    return path


def _lint(root: pathlib.Path, entries, requirement: pathlib.Path | str | None):
    return lint.lint_ride_along(entries, kanban_root=root, project=_PROJECT, requirements_path=requirement)


def _three(root: pathlib.Path) -> list:
    _bug(root, "BUG-9801-dashboard-glyph", "low")
    _bug(root, "BUG-9802-help-typo", "medium", body="The --help of team/scripts/status.sh misspells it.")
    _bug(root, "BUG-9803-log-prefix", "Low.")
    return ["BUG-9801", "BUG-9802-help-typo", {"item": "BUG-9803"}]


def test_three_eligible_bugs_are_accepted_in_plan_order(tmp_path) -> None:
    root = _root(tmp_path)
    result = _lint(root, _three(root), _requirement(root))

    assert result.ok, lint.describe(result.findings)
    assert [bug.path.name for bug in result.accepted] == [
        "BUG-9801-dashboard-glyph.md",
        "BUG-9802-help-typo.md",
        "BUG-9803-log-prefix.md",
    ]


@pytest.mark.parametrize("mixing", ["separate", None], ids=["separate", "default"])
def test_separate_mixing_refuses_any_entry(tmp_path, mixing) -> None:
    root = _root(tmp_path, mixing=mixing)
    _bug(root, "BUG-9804-small-thing")

    result = _lint(root, ["BUG-9804"], _requirement(root))

    assert not result.accepted
    assert lint.error_key(result.findings) == "ride_along_lint.mixing"
    assert "bundle_mixing resolves to 'separate'" in lint.describe(result.findings)


@pytest.mark.parametrize("entries", [None, []], ids=["absent", "empty"])
def test_no_entries_are_accepted_without_reading_the_posture(tmp_path, entries) -> None:
    root = _root(tmp_path, mixing="sideways")  # would refuse if the posture were read

    result = _lint(root, entries, None)

    assert result.ok and not result.accepted


def test_more_bugs_than_ride_along_max_is_refused(tmp_path) -> None:
    root = _root(tmp_path, max_count="2")

    result = _lint(root, _three(root), _requirement(root))

    assert not result.accepted
    assert [f.rule for f in result.findings] == ["count"]
    assert "lists 3 bugs; [release] ride_along_max allows 2" in lint.describe(result.findings)


def test_a_bug_listed_twice_counts_once(tmp_path) -> None:
    root = _root(tmp_path, max_count="1")
    _bug(root, "BUG-9805-twice")

    result = _lint(root, ["BUG-9805", "BUG-9805-twice"], _requirement(root))

    assert result.ok, lint.describe(result.findings)
    assert len(result.accepted) == 1


@pytest.mark.parametrize("severity", ["high", "Critical", "**high**", "urgent", ""])
def test_high_critical_or_unrecognised_severity_is_refused_naming_the_bug(tmp_path, severity) -> None:
    root = _root(tmp_path, max_count="4")  # four bugs, so only the severity rule fires
    entries = _three(root)
    _bug(root, "BUG-9806-big-thing", severity)

    result = _lint(root, entries + ["BUG-9806"], _requirement(root))

    assert not result.accepted
    assert [(f.rule, f.item) for f in result.findings] == [("severity", "BUG-9806-big-thing.md")]
    assert lint.error_key(result.findings) == "ride_along_lint.severity"


@pytest.mark.parametrize("named", ["close.sh", "team/scripts/close.sh", "roles/PM.md", "OPERATIONS.md"])
def test_a_bug_naming_a_deliverable_file_is_refused_naming_bug_and_file(tmp_path, named) -> None:
    root = _root(tmp_path)
    _bug(root, "BUG-9807-overlap", body=f"Seen in {named}, line 3.")

    result = _lint(root, ["BUG-9807"], _requirement(root))

    assert not result.accepted
    [finding] = result.findings
    assert (finding.rule, finding.item) == ("file-overlap", "BUG-9807-overlap.md")
    assert lint.error_key(result.findings) == "ride_along_lint.file_overlap"


@pytest.mark.parametrize("named", ["docs/PM.md", "team/scripts/close.shx", "e.g.", "v9.9.0"])
def test_a_different_file_is_not_an_overlap(tmp_path, named) -> None:
    root = _root(tmp_path)
    _bug(root, "BUG-9808-elsewhere", body=f"Seen in {named}.")

    result = _lint(root, ["BUG-9808"], _requirement(root))

    assert result.ok, lint.describe(result.findings)


def test_an_unknown_key_and_a_closed_bug_are_refused(tmp_path) -> None:
    root = _root(tmp_path)
    _bug(root, "BUG-9809-closed", status="done")

    result = _lint(root, ["BUG-9899", "BUG-9809", ""], _requirement(root))

    assert not result.accepted
    assert sorted(f.rule for f in result.findings) == ["bug", "entry", "status"]


def test_a_running_bug_this_requirement_already_lists_is_accepted(tmp_path) -> None:
    root = _root(tmp_path)
    bug = _bug(root, "BUG-9810-again", status="running")
    requirement = _requirement(root, extra=f"\n## Bundled Items\n\n- {bug.name} (`{bug}`) — ride-along\n")

    assert _lint(root, ["BUG-9810"], requirement).ok
    other = _requirement(root)
    assert [f.rule for f in _lint(root, ["BUG-9810"], other).findings] == ["status"]


@pytest.mark.parametrize("requirement", [None, "none", "missing.md"])
def test_an_unreadable_requirement_is_refused(tmp_path, requirement) -> None:
    root = _root(tmp_path)
    _bug(root, "BUG-9811-fine")
    path = tmp_path / requirement if requirement == "missing.md" else requirement

    result = _lint(root, ["BUG-9811"], path)

    assert [f.rule for f in result.findings] == ["requirement"]


def test_a_non_list_value_is_refused(tmp_path) -> None:
    result = _lint(_root(tmp_path), "BUG-9812", None)
    assert [f.rule for f in result.findings] == ["entry"]


def test_file_tokens_and_paths_match() -> None:
    tokens = lint.file_tokens("See `./team/a.sh`, docs/X.md. Also e.g. v1.2.3 and run.py: done")
    assert tokens == {"team/a.sh", "docs/X.md", "run.py"}
    assert lint.paths_match("a.sh", "team/scripts/a.sh")
    assert lint.paths_match("scripts/a.sh", "team/scripts/a.sh")
    assert not lint.paths_match("docs/README.md", "team/README.md")
    assert not lint.paths_match("bscripts/a.sh", "scripts/a.sh")
