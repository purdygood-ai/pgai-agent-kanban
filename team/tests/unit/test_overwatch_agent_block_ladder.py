"""
test_overwatch_agent_block_ladder.py
====================================
An agent's own block — ``agent_unclassified`` (no reason code) or
``agent_transient_provider_error`` — is retried by OVERWATCH's decision wake
(``pgai_agent_kanban.overwatch.decision_wake.run_project``) on the operator's
ladder before it reaches the operator inbox: wait 15 minutes and requeue,
wait 30 and requeue, wait 60 and requeue, and only then ask.

Covered:
  1. A CM-owned task, for each of the two codes: its first sighting records
     a 900 s cool-down; it is requeued three times, the cool-down recorded
     after each requeue growing 1800 s then 3600 s; a look inside a window
     does nothing; no inbox entry exists until the task blocks again after
     its third requeue, and that look writes exactly one, asking the row's
     own question; the project is not halted.
  2. The Triage Note an ``agent_unclassified`` requeue appends to the task
     README says the previous attempt blocked without a reason code and
     quotes its ``## Blockers`` text.
  3. A CM-owned ``provider_refusal`` block (the wake's zero-progress block
     site, its ``## Zero Progress`` count at the cap) is requeued through its
     row's three attempts, then asks the row's own question; the project is
     not halted.
  4. A task that keeps blocking with one reason code after its ladder is
     spent is still stopped by the runaway detector: the third block after
     the one that ended the ladder writes the project's runaway HALT.

``[overwatch]`` is left at the shipped defaults, so the runaway detector's
``reason_repeats`` counter runs at its default threshold throughout.

Every fixture is a throwaway kanban root under pytest's tmp_path with the
kanban root variables set or cleared together; nothing reads or writes the
live install, a repository or a provider.
"""

from __future__ import annotations

import datetime
import pathlib

import pytest

from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.overwatch import decision_executor as dx
from pgai_agent_kanban.overwatch import decision_wake as dw
from pgai_agent_kanban.overwatch import inbox
from pgai_agent_kanban.overwatch.block_record import load_block_record, write_block_record
from pgai_agent_kanban.task_status import read_section, section_value, set_or_append_section

_PROJECT = "ladder-proj"
_TASK = "CM-20260101-001-release-example"
_BLOCKED_AT = datetime.datetime(2026, 1, 1, 11, 0, 0, tzinfo=datetime.timezone.utc)
_FIRST_LOOK = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
_BLOCKERS = "Transient/platform API error: the subagent was lost twice before release.sh ran. Requeue to BACKLOG"

# The operator's ladder, written out by hand: the cool-down recorded at the
# first sighting, then after the first and the second requeue.
_LADDER_S = [900, 1800, 3600]

# What each row's own inbox question says once the ladder is spent.
_QUESTION = {
    "agent_unclassified": "blocked itself without a reason code",
    "agent_transient_provider_error": "sustained outage",
}


class Root:
    """A throwaway kanban root with one project and one CM task."""

    def __init__(self, tmp_path: pathlib.Path) -> None:
        self.root = tmp_path / "kanban"
        self.project = self.root / "projects" / _PROJECT
        self.task_dir = self.project / "tasks" / _TASK
        self.queue = self.project / "tasks" / "queues" / "cm_backlog.md"
        for d in (self.project / "overwatch" / "backups", self.queue.parent, self.task_dir / "artifacts",
                  self.root / "locks", self.root / "tmp", self.root / "logs"):
            d.mkdir(parents=True, exist_ok=True)
        (self.project / "project.cfg").write_text(f"[project]\nproject_name = {_PROJECT}\n", encoding="utf-8")
        (self.root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\n", encoding="utf-8")
        # [overwatch] is left at the shipped defaults: the runaway detector
        # must let the ladder run out and the row's own decision apply.
        (self.root / "kanban.cfg").write_text("[wake]\n\n[overwatch]\n", encoding="utf-8")
        (self.project / "release-state.md").write_text(
            "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n## RC Opened By Task\nnone\n",
            encoding="utf-8",
        )
        self.readme.write_text(f"# Task\n\n## Task ID\n{_TASK}\n\n## Role\nCM\n\n## Goal\nShip it.\n", encoding="utf-8")
        self.status.write_text(
            f"# Status\n\n## Task\n{_TASK}\n\n## State\nWORKING\n\n## Summary\nWorking.\n\n"
            "## Blockers\nnone\n\n## Needs Human\nno\n",
            encoding="utf-8",
        )

    @property
    def status(self) -> pathlib.Path:
        return self.task_dir / "status.md"

    @property
    def readme(self) -> pathlib.Path:
        return self.task_dir / "README.md"

    def field(self, heading: str) -> str:
        return section_value(self.status.read_text(encoding="utf-8"), heading).strip()

    @property
    def halt(self) -> pathlib.Path:
        return self.project / "HALT"

    def block(self, code: str, at: datetime.datetime) -> None:
        """Block the task as the agent's status-writer call does (a re-block keeps the triage fields)."""
        text = self.status.read_text(encoding="utf-8")
        for heading, body in (("State", "BLOCKED"), ("Needs Human", "yes"), ("Blockers", _BLOCKERS)):
            text = set_or_append_section(text, heading, body)
        self.status.write_text(text, encoding="utf-8")
        write_block_record(self.status, code, "CM", [], now=at)
        self.queue.write_text(f"# CM Backlog\n\n## Queue\n\n- [B] {_TASK} — release\n", encoding="utf-8")

    def block_refused(self, at: datetime.datetime) -> None:
        """Block the task as the wake's zero-progress site does for a provider refusal: the
        ``## Zero Progress`` count at its cap, then a ``provider_refusal`` Block Record."""
        text = self.status.read_text(encoding="utf-8")
        for heading, body in (
            ("State", "BLOCKED"), ("Needs Human", "yes"), ("Blockers", "The provider refused every dispatch."),
            ("Zero Progress", "reason_code: clean_exit_no_progress\ncount: 3\nlimit: 3\ndetail: refused dispatches"),
        ):
            text = set_or_append_section(text, heading, body)
        self.status.write_text(text, encoding="utf-8")
        write_block_record(self.status, "provider_refusal", "CM", ["signature=auth_401"], now=at)
        self.queue.write_text(f"# CM Backlog\n\n## Queue\n\n- [B] {_TASK} — release\n", encoding="utf-8")

    def look(self, at: datetime.datetime) -> dw.StopResult:
        """One decision-wake firing at *at*; the task's own stop result."""
        report = dw.run_project(self.root, _PROJECT, now=at)
        (line,) = [line for line in report.results if line.stop.subject == _TASK]
        return line

    def entries(self) -> list:
        return [e for e in inbox.list_open(OpsContext(kanban_root=self.root), _PROJECT) if e.source == _TASK]

    def cooldown_until(self) -> datetime.datetime:
        return datetime.datetime.fromisoformat(load_block_record(self.status).cooldown_until)

    def triage_notes(self) -> list:
        body = read_section(self.readme.read_text(encoding="utf-8"), "Triage Note") or ""
        return [line for line in body.splitlines() if line.strip()]


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


# ---------------------------------------------------------------------------
# 1. Three requeues on the ladder, then the inbox
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", sorted(_QUESTION))
def test_cm_task_agent_block_is_requeued_three_times_on_the_ladder_before_the_inbox(kanban, code):
    kanban.block(code, _BLOCKED_AT)

    # First sighting: nothing is done but the first window is recorded.
    first = kanban.look(_FIRST_LOOK)
    assert first.outcome.kind == "none" and "recorded the first cool-down" in first.detail, first.detail
    windows = [int((kanban.cooldown_until() - _FIRST_LOOK).total_seconds())]
    assert kanban.field("State") == "BLOCKED" and kanban.entries() == []

    for requeue in (1, 2, 3):
        due = kanban.cooldown_until()
        # A look inside the window does nothing.
        early = kanban.look(due - datetime.timedelta(minutes=1))
        assert early.outcome.kind == "none" and early.result == dx.STATUS_NOTHING, early.detail
        assert kanban.field("State") == "BLOCKED"

        acted = kanban.look(due)
        assert (acted.outcome.kind, acted.outcome.name, acted.result) == ("verb", "requeue", dx.STATUS_EXECUTED), (
            acted.detail
        )
        assert kanban.field("State") == "BACKLOG" and kanban.field("Needs Human") == "no"
        record = load_block_record(kanban.status)
        assert (record.attempts, record.requeues) == (requeue, requeue)
        if requeue < 3:
            windows.append(int((kanban.cooldown_until() - due).total_seconds()))
        assert kanban.entries() == [], f"an inbox entry was written before the ladder was spent ({requeue})"

        # The requeued attempt blocks again the same way, a few minutes in.
        kanban.block(code, due + datetime.timedelta(minutes=5))

    assert windows == _LADDER_S
    assert len(kanban.triage_notes()) == 3

    # The look after the third requeue's block asks the operator, once.
    last = kanban.look(kanban.cooldown_until() + datetime.timedelta(minutes=1))
    assert last.outcome.kind == "inbox" and last.result == dx.STATUS_INBOX, last.detail
    entries = kanban.entries()
    assert len(entries) == 1, entries
    assert _QUESTION[code] in entries[0].question, entries[0].question
    assert kanban.field("State") == "BLOCKED" and kanban.field("Needs Human") == "no"
    assert len(kanban.triage_notes()) == 3, "no fourth requeue"
    assert not kanban.halt.exists(), kanban.halt.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 2. The Triage Note quotes the block's ## Blockers
# ---------------------------------------------------------------------------


def test_unclassified_requeue_note_says_no_reason_code_and_quotes_the_blockers(kanban):
    kanban.block("agent_unclassified", _BLOCKED_AT)
    kanban.look(_FIRST_LOOK)

    acted = kanban.look(kanban.cooldown_until())

    assert acted.result == dx.STATUS_EXECUTED, acted.detail
    (note,) = kanban.triage_notes()
    assert "agent_unclassified: requeue_owner, attempt 1 of 3" in note, note
    assert "previous attempt blocked without a reason code" in note, note
    assert f"its ## Blockers read: '{_BLOCKERS}'" in note, note


# ---------------------------------------------------------------------------
# 3. provider_refusal: requeued through its max_attempts, then the inbox
# ---------------------------------------------------------------------------


def _walk_the_ladder(kanban: Root, block) -> datetime.datetime:
    """Block with *block*, then look at each cool-down's end, blocking again
    after every requeue, until the look after the third requeue's block.
    Asserts three requeues and that last look's inbox outcome; returns its time."""
    block(_BLOCKED_AT)
    first = kanban.look(_FIRST_LOOK)
    assert first.outcome.kind == "none" and "recorded the first cool-down" in first.detail, first.detail
    for requeue in (1, 2, 3):
        due = kanban.cooldown_until()
        acted = kanban.look(due)
        assert (acted.outcome.kind, acted.outcome.name, acted.result) == ("verb", "requeue", dx.STATUS_EXECUTED), (
            acted.detail
        )
        assert load_block_record(kanban.status).requeues == requeue
        assert kanban.entries() == [] and not kanban.halt.exists()
        block(due + datetime.timedelta(minutes=5))
    at = kanban.cooldown_until() + datetime.timedelta(minutes=1)
    last = kanban.look(at)
    assert last.outcome.kind == "inbox" and last.result == dx.STATUS_INBOX, last.detail
    return at


def test_cm_task_provider_refusal_is_requeued_through_its_max_attempts_before_the_inbox(kanban):
    _walk_the_ladder(kanban, kanban.block_refused)

    (entry,) = kanban.entries()
    assert "keeps refusing" in entry.question, entry.question
    assert len(kanban.triage_notes()) == 3, "no fourth requeue"
    assert not kanban.halt.exists(), kanban.halt.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 4. A task that keeps blocking after its ladder is spent is a runaway
# ---------------------------------------------------------------------------


def test_a_task_that_keeps_blocking_after_its_ladder_is_spent_stops_the_project(kanban):
    code = "agent_unclassified"
    at = _walk_the_ladder(kanban, lambda moment: kanban.block(code, moment))
    assert not kanban.halt.exists()

    # The operator returns the task each time; each attempt blocks the same
    # way and triage, its ladder spent, requeues it no more.
    for further in (1, 2, 3):
        at += datetime.timedelta(hours=1)
        kanban.block(code, at)
        line = kanban.look(at + datetime.timedelta(minutes=10))
        assert load_block_record(kanban.status).requeues == 3
        if further < 3:
            assert line.outcome.kind != "runaway" and not kanban.halt.exists(), line.detail

    assert line.outcome.kind == "runaway", line.detail
    halt = kanban.halt.read_text(encoding="utf-8")
    assert "# runaway-counter: reason_repeats\n" in halt, halt
    assert f"# runaway-subject: {_TASK}:{code}\n" in halt, halt
    # Counted: the first block and the three after the ladder; the three
    # blocks that followed a triage requeue are not.
    assert "# runaway-count: 4\n" in halt, halt
