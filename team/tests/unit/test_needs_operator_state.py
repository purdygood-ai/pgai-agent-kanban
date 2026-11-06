"""
test_needs_operator_state.py
============================
The ``needs-operator`` intake status and the verbs that move a bug or
priority item into and out of it:

  - ``python3 -m pgai_agent_kanban.ops set_needs_operator PROJECT_ROOT KEY
    QUESTION`` sets the item's ``## Status`` to ``needs-operator`` and writes
    exactly one open operator-inbox entry whose Source names the item and
    whose Question is the given text.
  - An unknown key, an empty question or a closed item is refused with the
    error/message/remedy triple and a non-zero exit, and both the item file
    and ``operator-inbox.md`` stay byte-identical.
  - Answering that entry -- through ``inbox.answer_entry`` or the inbox
    module's own command line -- returns the item to ``open``; an item no
    longer waiting keeps its status (the answer is still recorded on it);
    answering an entry whose Source is not an intake item (an RC) leaves
    every intake file byte-identical.
  - Closing a needs-operator item closes its open inbox entry.

Every test builds its own throwaway kanban root under pytest's ``tmp_path``;
subprocesses run under a rebuilt environment whose roots all name that
sandbox. Bug IDs are in the fictitious BUG-9xxx range.
"""

from __future__ import annotations

import datetime
import pathlib
import re
import subprocess
import sys

import pytest

from pgai_agent_kanban.lib.error_contract import parse_error_contract
from pgai_agent_kanban.ops import write as ops_write
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.errors import OpsError, cli_exit_code
from pgai_agent_kanban.overwatch import inbox
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent  # team/tests/unit/ -> team/
_PROJECT = "demo-proj"
_BUG = "BUG-9101-widget-port"
_QUESTION = "Which port should the widget service listen on?"
_T0 = datetime.datetime(2026, 1, 1, 10, 0, 0, tzinfo=datetime.UTC)


# ---------------------------------------------------------------------------
# Sandbox and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def root(tmp_path: pathlib.Path) -> pathlib.Path:
    """A sandbox kanban root with one registered project, one open bug and its backlog line."""
    sandbox = tmp_path / "root"
    project_root = sandbox / "projects" / _PROJECT
    (project_root / "bugs").mkdir(parents=True)
    (project_root / "priority").mkdir()
    (project_root / "tasks" / "queues").mkdir(parents=True)
    (sandbox / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\nenabled = true\n", encoding="utf-8")
    _intake(sandbox, "bugs", _BUG, "open")
    (project_root / "tasks" / "queues" / "bug_backlog.md").write_text(f"- [x] {_BUG} — widget port\n", encoding="utf-8")
    return sandbox


def _ctx(root: pathlib.Path) -> OpsContext:
    return OpsContext(kanban_root=root)


def _project_root(root: pathlib.Path) -> pathlib.Path:
    return root / "projects" / _PROJECT


def _intake(root: pathlib.Path, subdir: str, stem: str, status: str) -> pathlib.Path:
    path = root / "projects" / _PROJECT / subdir / f"{stem}.md"
    path.write_text(
        f"# {stem}\n\n## Status\n{status}\n\n## Summary\nThe widget port is not decided.\n",
        encoding="utf-8",
    )
    return path


def _bug_file(root: pathlib.Path, stem: str = _BUG) -> pathlib.Path:
    return _project_root(root) / "bugs" / f"{stem}.md"


def _inbox_file(root: pathlib.Path) -> pathlib.Path:
    return inbox.inbox_path(_ctx(root), _PROJECT)


def _status(path: pathlib.Path) -> str:
    match = re.search(r"(?m)^##\s+Status\s*\n+\s*(\S+)", path.read_text(encoding="utf-8"))
    assert match, f"no ## Status in {path}"
    return match.group(1)


def _snapshot(*paths: pathlib.Path) -> tuple:
    return tuple(p.read_bytes() if p.exists() else None for p in paths)


def _intake_snapshot(root: pathlib.Path) -> dict:
    """Every file under bugs/ and priority/, by path, with its bytes."""
    project_root = _project_root(root)
    return {p: p.read_bytes() for sub in ("bugs", "priority") for p in sorted((project_root / sub).glob("*.md"))}


def _run_module(root: pathlib.Path, tmp_path: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    """Run ``python3 -m <args>`` from team/ against the sandbox under a rebuilt environment."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    temp_dir = tmp_path / "framework-tmp"
    temp_dir.mkdir(exist_ok=True)
    env = build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_dir),
        },
    )
    return subprocess.run(
        [sys.executable, "-m", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_TEAM_DIR),
        timeout=60,
        stdin=subprocess.DEVNULL,
    )


def _set_via_cli(root, tmp_path, key: str = _BUG, question: str = _QUESTION):
    return _run_module(
        root,
        tmp_path,
        "pgai_agent_kanban.ops",
        "set_needs_operator",
        str(_project_root(root)),
        key,
        question,
    )


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"exit {result.returncode}\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _contract(stderr: str) -> dict:
    fields = parse_error_contract(stderr)
    assert fields is not None, f"no error/message/remedy block in:\n{stderr}"
    for name in ("error", "message", "remedy"):
        assert fields[name].strip(), f"empty {name} in:\n{stderr}"
    assert not re.search(r"<[A-Za-z_ -]+>", fields["remedy"]), f"remedy carries a placeholder: {fields['remedy']!r}"
    return fields


# ---------------------------------------------------------------------------
# Setting needs-operator
# ---------------------------------------------------------------------------


def test_verb_parks_an_open_bug_and_writes_one_inbox_entry_naming_it(root, tmp_path):
    result = _set_via_cli(root, tmp_path)

    assert result.returncode == 0, _diagnostic(result)
    assert _status(_bug_file(root)) == "needs-operator"
    entries = inbox.list_open(_ctx(root), _PROJECT)
    assert len(entries) == 1, entries
    assert entries[0].source == _BUG
    assert entries[0].question == _QUESTION
    assert inbox.read_inbox(_ctx(root), _PROJECT).done == ()
    assert f"### {entries[0].entry_id}" in result.stdout


def test_a_running_priority_can_be_parked_too(root):
    priority = _intake(root, "priority", "PRIORITY-9102-widget-colour", "running")

    entry = ops_write.set_needs_operator(_ctx(root), _PROJECT, "PRIORITY-9102", "Blue or green?", now=_T0)

    assert _status(priority) == "needs-operator"
    assert entry.source == "PRIORITY-9102-widget-colour"


def test_asking_the_same_question_again_keeps_one_entry(root):
    first = ops_write.set_needs_operator(_ctx(root), _PROJECT, _BUG, _QUESTION, now=_T0)
    before = _snapshot(_bug_file(root), _inbox_file(root))

    again = ops_write.set_needs_operator(_ctx(root), _PROJECT, _BUG, _QUESTION, now=_T0)

    assert again.entry_id == first.entry_id
    assert _snapshot(_bug_file(root), _inbox_file(root)) == before


@pytest.mark.parametrize(
    "key, question, status, error",
    [
        ("BUG-9199-no-such-bug", _QUESTION, "open", "needs_operator.unknown_key"),
        (_BUG, "", "open", "needs_operator.empty_question"),
        (_BUG, "   \n  ", "open", "needs_operator.empty_question"),
        (_BUG, _QUESTION, "done", "needs_operator.item_closed"),
    ],
    ids=["unknown-key", "empty-question", "blank-question", "done-bug"],
)
def test_refusals_carry_the_triple_and_write_nothing(root, tmp_path, key, question, status, error):
    _intake(root, "bugs", _BUG, status)
    before = _snapshot(_bug_file(root), _inbox_file(root))

    result = _set_via_cli(root, tmp_path, key, question)

    assert result.returncode != 0, _diagnostic(result)
    assert _contract(result.stderr)["error"] == error
    assert _snapshot(_bug_file(root), _inbox_file(root)) == before
    assert not _inbox_file(root).exists()


def test_a_task_key_is_refused_as_not_an_intake_item(root):
    task_dir = _project_root(root) / "tasks" / "CODER-20260101-001-widget"
    task_dir.mkdir(parents=True)
    (task_dir / "status.md").write_text("# Status\n\n## State\nWORKING\n", encoding="utf-8")

    with pytest.raises(OpsError) as excinfo:
        ops_write.set_needs_operator(_ctx(root), _PROJECT, "CODER-20260101-001", _QUESTION)

    assert excinfo.value.error == "needs_operator.not_intake_item"
    assert cli_exit_code(excinfo.value) == 2
    assert not _inbox_file(root).exists()


def test_a_failed_inbox_write_restores_the_previous_status(root, monkeypatch):
    before = _snapshot(_bug_file(root))

    def refuse(*_args, **_kwargs):
        raise inbox.InboxIoError("inbox.write_failed", "disk full", "df -h")

    monkeypatch.setattr(inbox, "write_entry", refuse)
    with pytest.raises(inbox.InboxIoError):
        ops_write.set_needs_operator(_ctx(root), _PROJECT, _BUG, _QUESTION)

    assert _snapshot(_bug_file(root)) == before


# ---------------------------------------------------------------------------
# Answering returns the item to open
# ---------------------------------------------------------------------------


def test_answer_entry_returns_the_bug_to_open(root):
    entry = ops_write.set_needs_operator(_ctx(root), _PROJECT, _BUG, _QUESTION, now=_T0)

    inbox.answer_entry(_ctx(root), _PROJECT, entry.entry_id, "8080", now=_T0)

    assert _status(_bug_file(root)) == "open"
    backlog = (_project_root(root) / "tasks" / "queues" / "bug_backlog.md").read_text(encoding="utf-8")
    assert backlog == f"- [ ] {_BUG} — widget port\n"


def test_answering_through_the_inbox_cli_returns_the_bug_to_open(root, tmp_path):
    entry = ops_write.set_needs_operator(_ctx(root), _PROJECT, _BUG, _QUESTION, now=_T0)

    result = _run_module(
        root,
        tmp_path,
        "pgai_agent_kanban.overwatch.inbox",
        "answer",
        "--project",
        _PROJECT,
        "--kanban-root",
        str(root),
        "--id",
        entry.entry_id,
        "--answer",
        "8080",
    )

    assert result.returncode == 0, _diagnostic(result)
    assert _status(_bug_file(root)) == "open"


def test_answering_an_rc_entry_leaves_every_intake_file_byte_identical(root):
    _intake(root, "bugs", "BUG-9103-other", "needs-operator")
    _intake(root, "priority", "PRIORITY-9104-other", "needs-operator")
    entry = inbox.write_entry(
        _ctx(root),
        _PROJECT,
        source="rc:v1.2.3",
        question="Cancel the RC?",
        meanwhile="Kept it open.",
        how_to_answer="cancel or keep",
        now=_T0,
    )
    before = _intake_snapshot(root)

    inbox.answer_entry(_ctx(root), _PROJECT, entry.entry_id, "keep", now=_T0)

    assert _intake_snapshot(root) == before


def test_answering_an_entry_for_an_item_no_longer_waiting_keeps_its_status(root):
    entry = ops_write.set_needs_operator(_ctx(root), _PROJECT, _BUG, _QUESTION, now=_T0)
    _intake(root, "bugs", _BUG, "running")
    before = _intake_snapshot(root)

    inbox.answer_entry(_ctx(root), _PROJECT, entry.entry_id, "8080", now=_T0)

    after = _intake_snapshot(root)
    assert _status(_bug_file(root)) == "running"
    assert ops_write.recorded_operator_answers(_bug_file(root)) == [
        f"- 2026-01-01T10:00:00+00:00 {entry.entry_id}: 8080"
    ]
    assert {p: b for p, b in after.items() if p != _bug_file(root)} == {
        p: b for p, b in before.items() if p != _bug_file(root)
    }


# ---------------------------------------------------------------------------
# Closing a needs-operator item
# ---------------------------------------------------------------------------


def test_closing_a_needs_operator_bug_closes_its_open_inbox_entry(root):
    entry = ops_write.set_needs_operator(_ctx(root), _PROJECT, _BUG, _QUESTION, now=_T0)
    other = inbox.write_entry(
        _ctx(root),
        _PROJECT,
        source="rc:v1.2.3",
        question="Cancel the RC?",
        meanwhile="Kept it open.",
        how_to_answer="cancel or keep",
        now=_T0,
    )

    ops_write.close_item(_ctx(root), _PROJECT, _BUG, state="wont-do", note="not worth it")

    assert _status(_bug_file(root)) == "wont-do"
    state = inbox.read_inbox(_ctx(root), _PROJECT)
    assert [e.entry_id for e in state.done] == [entry.entry_id]
    assert [e.entry_id for e in state.open] == [other.entry_id]
