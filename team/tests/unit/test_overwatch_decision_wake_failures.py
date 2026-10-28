"""
test_overwatch_decision_wake_failures.py
========================================
Every non-success outcome of the decision wake
(``pgai_agent_kanban.overwatch.decision_wake.run_project``) reaches the
operator inbox, once.

Covered:
  1. A verb whose executor raises: the stop is reported failed, the rest of
     the run is still decided, and one open inbox entry names the verb, that
     it did not run, the error and runnable commands; a second firing adds
     no entry.
  2. A verb the allowlist refuses (an operator data action naming a verb
     that is not allowlisted): one open inbox entry naming the task.
  3. A stop the wake declines while the task stays stopped
     (``[overwatch] block_triage = false`` keeps the decided verb unrun):
     one open inbox entry naming the task, the task untouched, and no
     second entry on the next firing.
  4. ``runaway_releases_per_hour = abc`` in kanban.cfg: one open inbox entry
     saying the runaway detector is off for the project, naming the key and
     the file; a second firing adds none, and --dry-run writes none.

Every fixture is a throwaway kanban root under pytest's tmp_path with the
kanban root variables set or cleared together; nothing reads or writes the
live install.
"""

from __future__ import annotations

import datetime
import pathlib
import re

import pytest

from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.overwatch import decision_executor as dx
from pgai_agent_kanban.overwatch import decision_wake as dw
from pgai_agent_kanban.overwatch import inbox
from pgai_agent_kanban.overwatch.block_record import write_block_record
from pgai_agent_kanban.task_status import section_value, set_or_append_section

_PROJECT = "wake-proj"
_NOW = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
_PAST = "2026-01-01T11:00:00+00:00"
_PLACEHOLDER = re.compile(r"<[^<>\s]+>")


class Root:
    """A throwaway kanban root with one project."""

    def __init__(self, tmp_path: pathlib.Path) -> None:
        self.root = tmp_path / "kanban"
        self.project = self.root / "projects" / _PROJECT
        self.queue = self.project / "tasks" / "queues" / "coder_backlog.md"
        for d in (self.project / "overwatch" / "backups", self.queue.parent, self.root / "locks",
                  self.root / "tmp", self.root / "logs"):
            d.mkdir(parents=True, exist_ok=True)
        self.queue.write_text("# CODER Backlog\n\n## Queue\n\n", encoding="utf-8")
        (self.project / "project.cfg").write_text(f"[project]\nproject_name = {_PROJECT}\n", encoding="utf-8")
        (self.root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\n", encoding="utf-8")
        self.kanban_cfg.write_text("[wake]\n", encoding="utf-8")
        (self.project / "release-state.md").write_text(
            "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n## RC Opened By Task\nnone\n",
            encoding="utf-8",
        )

    @property
    def kanban_cfg(self) -> pathlib.Path:
        return self.root / "kanban.cfg"

    def blocked(self, task_id: str, code: str, evidence: tuple = (), *, owner: str = "CODER") -> pathlib.Path:
        """A task blocked as a block site leaves it: BLOCKED, Needs Human yes, a Block Record."""
        task_dir = self.project / "tasks" / task_id
        (task_dir / "artifacts").mkdir(parents=True)
        (task_dir / "README.md").write_text(f"# Task\n\n## Task ID\n{task_id}\n\n## Role\nCODER\n", encoding="utf-8")
        status = task_dir / "status.md"
        text = f"# Status\n\n## Task\n{task_id}\n\n## State\nWORKING\n\n## Summary\nWorking.\n"
        for heading, body in (("State", "BLOCKED"), ("Needs Human", "yes"), ("Blockers", f"{code} block")):
            text = set_or_append_section(text, heading, body)
        status.write_text(text, encoding="utf-8")
        write_block_record(status, code, owner, list(evidence), now=_PAST)
        with self.queue.open("a", encoding="utf-8") as handle:
            handle.write(f"- [B] {task_id} — fixture\n")
        return status

    def open_entries(self) -> tuple:
        return inbox.list_open(OpsContext(kanban_root=self.root), _PROJECT)

    def run(self, **kwargs) -> dw.WakeReport:
        return dw.run_project(self.root, _PROJECT, now=_NOW, **kwargs)


@pytest.fixture()
def kanban(tmp_path, monkeypatch) -> Root:
    root = Root(tmp_path)
    for name in ("PGAI_TASKS_DIR", "PGAI_PROJECT_ROOT", "PGAI_PROJECT_NAME", "HALT_OVERWATCH",
                 "OVERWATCH_FIRING_TIMESTAMP", "OVERWATCH_LOCK_WAIT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("KANBAN_ROOT", str(root.root))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root.root))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(root.root / "tmp"))
    return root


def _no_placeholder(entry: inbox.InboxEntry) -> None:
    assert not _PLACEHOLDER.search(entry.how_to_answer), entry.how_to_answer


# ---------------------------------------------------------------------------
# 1. A verb whose executor raises
# ---------------------------------------------------------------------------


def test_a_verb_whose_executor_raises_asks_the_operator_once(kanban, monkeypatch):
    task = "CODER-20260101-001-reset-raises"
    kanban.blocked(task, "invalid_state")
    other = "CODER-20260101-002-also-blocked"
    kanban.blocked(other, "missing_readme")

    def raises(run, verb):
        raise RuntimeError(f"the {verb} runner broke")

    monkeypatch.setitem(dx._VERB_RUNNERS, "reset-to-backlog", raises)
    report = kanban.run()

    by_subject = {line.stop.subject: line for line in report.results}
    failed = by_subject[task]
    assert failed.result == dw.RESULT_FAILED and failed.outcome.name == "reset-to-backlog", failed
    assert "operator inbox entry INBOX-" in failed.detail and "written" in failed.detail
    # The failure stopped that stop alone: the next one was still decided.
    assert by_subject[other].result == dx.STATUS_INBOX, by_subject[other]

    entries = [e for e in kanban.open_entries() if e.source == task]
    assert len(entries) == 1, kanban.open_entries()
    entry = entries[0]
    assert "verb reset-to-backlog" in entry.question and "did not run" in entry.question
    assert "RuntimeError: the reset-to-backlog runner broke" in entry.question
    assert f"{task} is still BLOCKED" in entry.question
    assert "task-status.sh" in entry.how_to_answer and "--state BACKLOG" in entry.how_to_answer
    assert "--dry-run" in entry.how_to_answer and "inbox.sh" in entry.how_to_answer
    _no_placeholder(entry)

    again = kanban.run()
    assert {line.stop.subject: line.result for line in again.results}[task] == dw.RESULT_FAILED
    assert len([e for e in kanban.open_entries() if e.source == task]) == 1
    assert "still open" in {line.stop.subject: line.detail for line in again.results}[task]


def test_a_dry_run_reports_a_failure_without_writing_the_inbox(kanban, monkeypatch):
    kanban.blocked("CODER-20260101-001-decide-raises", "invalid_state")

    def raises(*_args, **_kwargs):
        raise ValueError("unreadable decision input")

    monkeypatch.setattr(dw.dc, "decide", raises)
    report = kanban.run(dry_run=True)
    assert [line.result for line in report.results] == [dw.RESULT_FAILED]
    assert kanban.open_entries() == ()


# ---------------------------------------------------------------------------
# 2. A verb the allowlist refuses
# ---------------------------------------------------------------------------


def test_a_verb_the_allowlist_refuses_asks_the_operator_once(kanban):
    task = "CM-20260101-001-wants-a-data-fix"
    kanban.blocked(task, "agent_operator_data_action", ("verb=drop-tables", f"project={_PROJECT}"), owner="CM")

    first = kanban.run()
    second = kanban.run()

    assert [line.result for line in first.results] == [dx.STATUS_INBOX], first.results
    assert [line.result for line in second.results] == [dx.STATUS_NOTHING], second.results
    entries = kanban.open_entries()
    assert len(entries) == 1 and entries[0].source == task, entries
    assert "drop-tables" in entries[0].question
    _no_placeholder(entries[0])


# ---------------------------------------------------------------------------
# 3. A stop the wake declines while the task stays stopped
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code, verb", [("invalid_state", "reset-to-backlog"), ("zero_progress", "requeue")])
def test_a_declined_stop_that_leaves_its_task_stopped_asks_the_operator_once(kanban, code, verb):
    task = "CODER-20260101-001-left-blocked"
    status = kanban.blocked(task, code)
    before = status.read_bytes()

    first = kanban.run(triage_enabled=False)

    (line,) = first.results
    assert (line.outcome.kind, line.outcome.name) == ("verb", verb), line
    assert line.result == dx.STATUS_INBOX and "operator inbox entry" in line.detail, line
    assert line.execution.verb_error and "block_triage is false" in line.execution.verb_error
    entries = kanban.open_entries()
    assert len(entries) == 1 and entries[0].source == task, entries
    assert f"verb {verb}" in entries[0].question and "did not run" in entries[0].question
    assert f"{task} is still BLOCKED" in entries[0].question
    assert "block_triage = true" in entries[0].how_to_answer and "--state BACKLOG" in entries[0].how_to_answer
    _no_placeholder(entries[0])
    # block_triage = false keeps OVERWATCH to records: the task is untouched.
    assert status.read_bytes() == before
    assert section_value(status.read_text(encoding="utf-8"), "State").strip() == "BLOCKED"

    second = kanban.run(triage_enabled=False)
    assert [line.result for line in second.results] == [dx.STATUS_NOTHING], second.results
    assert "still open" in second.results[0].detail
    assert len(kanban.open_entries()) == 1


def test_a_verb_carried_out_with_triage_on_asks_nothing(kanban):
    kanban.blocked("CODER-20260101-001-reset-runs", "invalid_state")
    report = kanban.run()
    assert [line.result for line in report.results] == [dx.STATUS_EXECUTED], report.results
    assert kanban.open_entries() == ()


# ---------------------------------------------------------------------------
# 4. A runaway threshold that is not a whole number
# ---------------------------------------------------------------------------


def test_a_malformed_runaway_threshold_says_the_detector_is_off_once(kanban):
    kanban.kanban_cfg.write_text("[wake]\n\n[overwatch]\nrunaway_releases_per_hour = abc\n", encoding="utf-8")

    dry = kanban.run(dry_run=True)
    assert any("runaway.bad_threshold" in w for w in dry.warnings), dry.warnings
    assert kanban.open_entries() == ()

    first = kanban.run()
    second = kanban.run()

    entries = kanban.open_entries()
    assert len(entries) == 1, entries
    entry = entries[0]
    assert entry.source == _PROJECT
    assert f"runaway detector is off for project {_PROJECT}" in entry.question
    assert "runaway_releases_per_hour" in entry.question and str(kanban.kanban_cfg) in entry.question
    assert "sed -i" in entry.how_to_answer and "runaway_releases_per_hour = 5" in entry.how_to_answer
    _no_placeholder(entry)
    assert any("written" in w for w in first.warnings), first.warnings
    assert any("still open" in w for w in second.warnings), second.warnings
