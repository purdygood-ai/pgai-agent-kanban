"""
test_project_name_rule_sites.py
===============================
Every module that checks a project name before it builds a path or reads a
project's files takes the rule from its one definition --
``PROJECTS_CFG_NAME_RE`` in ``scripts/lib/projects_cfg.sh``, read in Python
through ``pgai_agent_kanban.lib.ini.is_project_name`` -- so each accepts and
refuses exactly the names the definition does:

  * ``pm-agent/lib/artifacts.py`` -- ``get_project_path`` refuses with
    ``ArtifactsError``;
  * ``overwatch/inbox.py`` -- ``read_inbox`` refuses with ``inbox.bad_project``;
  * ``overwatch/runaway.py`` -- ``evaluate`` refuses with
    ``runaway.bad_project``;
  * ``overwatch/block_record.py`` -- ``parse_action_args`` refuses a
    ``restamp-fixed-in`` ``project=`` argument;
  * ``dashboard/scan_attention.py`` -- the OPERATOR INBOX and NEEDS OPERATOR
    sections render no line for a project directory whose name the rule
    refuses.

Each site is called through its own entry point with a sandbox kanban root
under ``tmp_path`` in which every non-empty candidate has a project
directory, so the name rule alone decides the answer. Nothing reads or
writes the live install.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
from collections.abc import Callable

import pytest

from pgai_agent_kanban.dashboard import scan_attention
from pgai_agent_kanban.dashboard import status_line
from pgai_agent_kanban.lib import ini
from pgai_agent_kanban.metrics import inbox_view
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.overwatch import block_record, inbox, runaway

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]  # team/

ACCEPTED = ("pgai-agent-kanban", "my-app")
REFUSED = ("My_App", "my_app", "-leading-dash", "")
CANDIDATES = ACCEPTED + REFUSED


def _load_artifacts():
    """Load pm-agent/lib/artifacts.py by path under a name no other test uses."""
    path = _TEAM_DIR / "pm-agent" / "lib" / "artifacts.py"
    spec = importlib.util.spec_from_file_location("_name_rule_sites_artifacts", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_artifacts = _load_artifacts()


@pytest.fixture
def root(tmp_path: pathlib.Path) -> pathlib.Path:
    """A sandbox kanban root holding a project directory for every non-empty candidate."""
    kanban_root = tmp_path / "kanban"
    for name in CANDIDATES:
        (kanban_root / "projects" / name).mkdir(parents=True, exist_ok=True)
    return kanban_root


def _artifacts_accepts(kanban_root: pathlib.Path, name: str) -> bool:
    try:
        _artifacts.get_project_path(name, kanban_root=kanban_root)
    except _artifacts.ArtifactsError as exc:
        assert "project-name rule" in str(exc), str(exc)
        return False
    return True


def _inbox_accepts(kanban_root: pathlib.Path, name: str) -> bool:
    try:
        inbox.read_inbox(OpsContext(kanban_root=kanban_root), name)
    except inbox.InboxRefused as exc:
        assert exc.error == "inbox.bad_project", str(exc)
        return False
    return True


def _runaway_accepts(kanban_root: pathlib.Path, name: str) -> bool:
    try:
        runaway.evaluate(OpsContext(kanban_root=kanban_root), name, thresholds={})
    except runaway.RunawayError as exc:
        assert exc.error == "runaway.bad_project", str(exc)
        return False
    return True


def _block_record_accepts(kanban_root: pathlib.Path, name: str) -> bool:
    try:
        block_record.parse_action_args(["verb=restamp-fixed-in", f"project={name}", "version=v1.2.3", "bugs=BUG-0001"])
    except block_record.ActionArgsError as exc:
        assert all(problem.startswith("project") for problem in exc.problems), exc.problems
        return False
    return True


def _rendered_projects(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    kanban_root: pathlib.Path,
    name: str,
    scan: Callable[[str, bool], None],
    line_re: str,
) -> bool:
    """True when *scan* renders a line for a project directory called *name*.

    The project list is the one name, the inbox always reads as unreadable
    and every intake directory holds one needs-operator item, so a project
    the rule accepts always renders and one it refuses never does.
    """
    monkeypatch.setattr(scan_attention, "_iter_projects", lambda _root: iter([(name, kanban_root / "projects" / name)]))
    monkeypatch.setattr(inbox_view, "read_open_entries", lambda _root, _project: (None, "inbox unreadable"))
    monkeypatch.setattr(status_line, "needs_operator_keys", lambda _dir: ["BUG-0001"])
    scan(str(kanban_root), False)
    rendered = re.findall(line_re, capsys.readouterr().out, re.MULTILINE)
    assert rendered in ([], [name]), rendered
    return rendered == [name]


def _scan_inbox_accepts(monkeypatch, capsys, kanban_root, name) -> bool:
    return _rendered_projects(
        monkeypatch, capsys, kanban_root, name, scan_attention.scan_inbox_section, r"^  (.*): inbox unreadable$"
    )


def _scan_needs_operator_accepts(monkeypatch, capsys, kanban_root, name) -> bool:
    return _rendered_projects(
        monkeypatch,
        capsys,
        kanban_root,
        name,
        scan_attention.scan_needs_operator_section,
        r"^  (.*): \d+ needs-operator$",
    )


def test_candidates_are_what_the_definition_accepts_and_refuses() -> None:
    """The names below are the rule's own answers, read from its definition."""
    assert [name for name in CANDIDATES if ini.is_project_name(name)] == list(ACCEPTED)


@pytest.mark.parametrize("name", CANDIDATES, ids=lambda n: n or "empty")
@pytest.mark.parametrize(
    "site",
    [_artifacts_accepts, _inbox_accepts, _runaway_accepts, _block_record_accepts],
    ids=["artifacts", "inbox", "runaway", "block_record"],
)
def test_site_answers_as_the_definition(root: pathlib.Path, site, name: str) -> None:
    assert site(root, name) is ini.is_project_name(name), name


@pytest.mark.parametrize("name", CANDIDATES, ids=lambda n: n or "empty")
@pytest.mark.parametrize(
    "site", [_scan_inbox_accepts, _scan_needs_operator_accepts], ids=["scan_inbox", "scan_needs_operator"]
)
def test_attention_pane_answers_as_the_definition(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], root: pathlib.Path, site, name: str
) -> None:
    assert site(monkeypatch, capsys, root, name) is ini.is_project_name(name), name


@pytest.mark.parametrize(
    "path",
    [
        "pm-agent/lib/artifacts.py",
        "pgai_agent_kanban/overwatch/inbox.py",
        "pgai_agent_kanban/overwatch/runaway.py",
        "pgai_agent_kanban/overwatch/block_record.py",
        "pgai_agent_kanban/dashboard/scan_attention.py",
    ],
)
def test_site_holds_no_copy_of_the_rule(path: str) -> None:
    """The rule's text appears only at its definition, never in a site."""
    assert ini.project_name_rule().pattern not in (_TEAM_DIR / path).read_text(encoding="utf-8")
