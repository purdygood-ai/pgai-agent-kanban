"""
test_overwatch_pipeline_role_block_ladder.py
============================================
A release cannot carry on without its PM, TESTER or CM task, so OVERWATCH's
decision wake (``pgai_agent_kanban.overwatch.decision_wake.run_project``)
retries a block one of them holds on a decision-only classifier row on the
operator's ladder — wait 15 minutes and requeue, wait 30 and requeue, wait
60 and requeue — before the row's own decision (the operator inbox) applies.

Covered:
  1. For each of PM, TESTER and CM, a block on ``source_branch_missing`` (a
     row that carries only a ``decision:``): the first sighting records a
     900 s cool-down; the task is requeued three times, the cool-down
     recorded after each requeue growing 1800 s then 3600 s; a look inside a
     window does nothing; no inbox entry exists until the task blocks again
     after its third requeue, and that look writes exactly one, asking the
     row's own question. The task is the only one of its release, so the
     entry's Meanwhile line says the release is waiting on it. The project
     is not halted.
  2. The Triage Note a pipeline-ladder requeue appends names the reason
     code and the owner role and quotes the block's ``## Blockers``.
  3. A CODER-owned block on the same row goes to its decision on the first
     look: one inbox entry, no requeue, no cool-down recorded.
  4. A CM-owned ``ship_policy_halt`` block keeps its row's own action: decide
     hands it to the resolution stage at every attempt count, never a
     requeue.

``[overwatch]`` is left at the shipped defaults, so the runaway detector's
``reason_repeats`` counter runs at its default threshold and the ladder's
end reaches the row's decision with no project HALT.

Every fixture is a throwaway kanban root under pytest's tmp_path with the
kanban root variables set or cleared together; nothing reads or writes the
live install, a repository or a provider.
"""

from __future__ import annotations

import datetime
import pathlib

import pytest

from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.overwatch import decide as dc
from pgai_agent_kanban.overwatch import decision_executor as dx
from pgai_agent_kanban.overwatch import decision_wake as dw
from pgai_agent_kanban.overwatch import inbox
from pgai_agent_kanban.overwatch.block_record import BlockRecord, load_block_record, write_block_record
from pgai_agent_kanban.task_status import read_section, section_value, set_or_append_section

_PROJECT = "pipeline-proj"
_VERSION = "v1.4.2"
_CODE = "source_branch_missing"
_BLOCKED_AT = datetime.datetime(2026, 1, 1, 11, 0, 0, tzinfo=datetime.UTC)
_FIRST_LOOK = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
_BLOCKERS = "The source branch ai_rc/v1.4.2 named in the README does not exist in the dev tree."

# The operator's ladder, written out by hand: the cool-down recorded at the
# first sighting, then after the first and the second requeue.
_LADDER_S = [900, 1800, 3600]

# What source_branch_missing's own inbox question says.
_QUESTION = "does not exist in the dev tree"

_TASK_IDS = {
    "PM": "PM-20260101-001-plan-example",
    "TESTER": "TESTER-20260101-002-verify-example",
    "CM": "CM-20260101-003-release-example",
    "CODER": "CODER-20260101-004-implement-example",
}


class Root:
    """A throwaway kanban root with one project and one task owned by *role*."""

    def __init__(self, tmp_path: pathlib.Path, role: str) -> None:
        self.role = role
        self.task = _TASK_IDS[role]
        self.root = tmp_path / "kanban"
        self.project = self.root / "projects" / _PROJECT
        self.task_dir = self.project / "tasks" / self.task
        self.queue = self.project / "tasks" / "queues" / f"{role.lower()}_backlog.md"
        for d in (
            self.project / "overwatch" / "backups",
            self.queue.parent,
            self.task_dir / "artifacts",
            self.root / "locks",
            self.root / "tmp",
            self.root / "logs",
        ):
            d.mkdir(parents=True, exist_ok=True)
        (self.project / "project.cfg").write_text(f"[project]\nproject_name = {_PROJECT}\n", encoding="utf-8")
        (self.root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\n", encoding="utf-8")
        # [overwatch] is left at the shipped defaults: the runaway detector
        # must let the ladder run out and the row's own decision apply.
        (self.root / "kanban.cfg").write_text("[wake]\n\n[overwatch]\n", encoding="utf-8")
        (self.project / "release-state.md").write_text(
            f"# Release State\n\n## Active RC\n{_VERSION}\n\n## RC Opened At\n2026-01-01T00:00:00Z\n\n"
            "## RC Opened By Task\nCM-20260101-000-open-rc\n",
            encoding="utf-8",
        )
        self.readme.write_text(
            f"# Task\n\n## Task ID\n{self.task}\n\n## Role\n{role}\n\n## Release Version\n{_VERSION}\n\n"
            "## Goal\nDo the release's part.\n",
            encoding="utf-8",
        )
        self.status.write_text(
            f"# Status\n\n## Task\n{self.task}\n\n## State\nWORKING\n\n## Summary\nWorking.\n\n"
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

    def block(self, at: datetime.datetime) -> None:
        """Block the task as the wake's block site does (a re-block keeps the triage fields)."""
        text = self.status.read_text(encoding="utf-8")
        for heading, body in (("State", "BLOCKED"), ("Needs Human", "yes"), ("Blockers", _BLOCKERS)):
            text = set_or_append_section(text, heading, body)
        self.status.write_text(text, encoding="utf-8")
        write_block_record(self.status, _CODE, self.role, [], now=at)
        self.queue.write_text(f"# {self.role} Backlog\n\n## Queue\n\n- [B] {self.task} — example\n", encoding="utf-8")

    def look(self, at: datetime.datetime) -> dw.StopResult:
        """One decision-wake firing at *at*; the task's own stop result."""
        report = dw.run_project(self.root, _PROJECT, now=at)
        (line,) = [line for line in report.results if line.stop.subject == self.task]
        return line

    def entries(self) -> list:
        return [e for e in inbox.list_open(OpsContext(kanban_root=self.root), _PROJECT) if e.source == self.task]

    def cooldown_until(self) -> datetime.datetime:
        return datetime.datetime.fromisoformat(load_block_record(self.status).cooldown_until)

    def triage_notes(self) -> list:
        body = read_section(self.readme.read_text(encoding="utf-8"), "Triage Note") or ""
        return [line for line in body.splitlines() if line.strip()]


def _sandbox(tmp_path, monkeypatch, role: str) -> Root:
    root = Root(tmp_path, role)
    for name in (
        "PGAI_TASKS_DIR",
        "PGAI_PROJECT_ROOT",
        "PGAI_PROJECT_NAME",
        "HALT_OVERWATCH",
        "OVERWATCH_FIRING_TIMESTAMP",
        "OVERWATCH_LOCK_WAIT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("KANBAN_ROOT", str(root.root))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root.root))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(root.root / "tmp"))
    return root


# ---------------------------------------------------------------------------
# 1. Three requeues on the ladder, then the inbox
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["PM", "TESTER", "CM"])
def test_pipeline_role_block_on_a_decision_only_row_is_requeued_three_times_before_the_inbox(
    tmp_path, monkeypatch, role
):
    kanban = _sandbox(tmp_path, monkeypatch, role)
    kanban.block(_BLOCKED_AT)

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
        assert (acted.outcome.kind, acted.outcome.name, acted.result) == (
            "verb",
            "requeue",
            dx.STATUS_EXECUTED,
        ), acted.detail
        assert kanban.field("State") == "BACKLOG" and kanban.field("Needs Human") == "no"
        record = load_block_record(kanban.status)
        assert (record.attempts, record.requeues) == (requeue, requeue)
        if requeue < 3:
            windows.append(int((kanban.cooldown_until() - due).total_seconds()))
        assert kanban.entries() == [], f"an inbox entry was written before the ladder was spent ({requeue})"

        # The requeued attempt blocks again the same way, a few minutes in.
        kanban.block(due + datetime.timedelta(minutes=5))

    assert windows == _LADDER_S
    assert len(kanban.triage_notes()) == 3

    # The look after the third requeue's block asks the operator, once.
    last = kanban.look(kanban.cooldown_until() + datetime.timedelta(minutes=1))
    assert last.outcome.kind == "inbox" and last.result == dx.STATUS_INBOX, last.detail
    entries = kanban.entries()
    assert len(entries) == 1, entries
    assert _QUESTION in entries[0].question, entries[0].question
    assert kanban.field("State") == "BLOCKED" and kanban.field("Needs Human") == "no"
    assert len(kanban.triage_notes()) == 3, "no fourth requeue"

    # The task is its release's only one: the release waits on it.
    assert f"Release {_VERSION} is waiting on it" in entries[0].meanwhile, entries[0].meanwhile
    assert "every other task keeps running" not in entries[0].meanwhile
    blockers = read_section(kanban.status.read_text(encoding="utf-8"), "Blockers") or ""
    assert f"release {_VERSION} is waiting on this task" in blockers, blockers
    assert "chain carries on" not in blockers, blockers
    halt = kanban.project / "HALT"
    assert not halt.exists(), halt.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 2. The Triage Note names the code and the role and quotes ## Blockers
# ---------------------------------------------------------------------------


def test_pipeline_ladder_requeue_note_names_the_code_and_role_and_quotes_the_blockers(tmp_path, monkeypatch):
    kanban = _sandbox(tmp_path, monkeypatch, "PM")
    kanban.block(_BLOCKED_AT)
    kanban.look(_FIRST_LOOK)

    acted = kanban.look(kanban.cooldown_until())

    assert acted.result == dx.STATUS_EXECUTED, acted.detail
    (note,) = kanban.triage_notes()
    assert f"{_CODE}: requeue_owner, attempt 1 of 3" in note, note
    assert f"previous attempt blocked ({_CODE}) and the release cannot carry on without this PM task" in note, note
    assert f"its ## Blockers read: '{_BLOCKERS}'" in note, note


# ---------------------------------------------------------------------------
# 3. A CODER block on the same row is decided on the first look
# ---------------------------------------------------------------------------


def test_coder_block_on_the_same_decision_only_row_goes_to_its_decision_on_the_first_look(tmp_path, monkeypatch):
    kanban = _sandbox(tmp_path, monkeypatch, "CODER")
    kanban.block(_BLOCKED_AT)

    first = kanban.look(_FIRST_LOOK)

    assert first.outcome.kind == "inbox" and first.result == dx.STATUS_INBOX, first.detail
    entries = kanban.entries()
    assert len(entries) == 1 and _QUESTION in entries[0].question, entries
    record = load_block_record(kanban.status)
    assert (record.attempts, record.requeues, record.cooldown_until) == (0, 0, "none")
    assert kanban.triage_notes() == []
    assert kanban.field("State") == "BLOCKED" and kanban.field("Needs Human") == "no"


# ---------------------------------------------------------------------------
# 4. A ship-policy HALT block is never requeued past its resolution stage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("attempts", [0, 1, 3, 9])
def test_cm_ship_policy_halt_block_goes_to_the_resolution_stage_never_a_requeue(attempts):
    record = BlockRecord(
        reason_code="ship_policy_halt",
        owner_role="CM",
        attempts=attempts,
        first_blocked_at=_BLOCKED_AT.isoformat(),
        last_blocked_at=_BLOCKED_AT.isoformat(),
        cooldown_until="none",
        evidence="none",
        resume="fresh",
    )
    outcome = dc.decide(
        dc.DecisionInput(
            subject=dc.SUBJECT_BLOCK,
            project=_PROJECT,
            task_id=_TASK_IDS["CM"],
            record=record,
            task_state="BLOCKED",
            active_rc=_VERSION,
            now=_FIRST_LOOK,
        )
    )
    assert (outcome.kind, outcome.name) == (dc.OUTCOME_VERB, dc.VERB_RESOLUTION_STAGE), outcome
    assert outcome.classifier.action == "defer_to_resolution_stage"
    assert not outcome.classifier.pipeline_ladder
