"""
test_block_triage_ledger_stamps.py
==================================
OVERWATCH block triage records each outcome on the project's block ledger
(``pgai_agent_kanban.overwatch.block_triage`` -> ``pgai_agent_kanban.metrics.
blocked_ledger``, source ``task:<TASK-ID>``).

Covered:
  1. An ``act`` that requeues the owner (the real requeue handler) opens a
     block-start carrying the Block Record's reason_code at the record's
     last_blocked_at, then closes it with a block-end of class any.
  2. A block the Block Record write already opened gets no second
     block-start; the requeue closes that one.
  3. An ``escalate`` marks the open block escalated: one block-escalate line
     is appended, every earlier ledger line is byte-for-byte unchanged, and
     the block reads class human. A handler refused at execution time
     escalates the same way and closes nothing.
  4. A ``record`` (first cool-down) opens the block and leaves it open.
  5. A task in a project whose Active RC is none is recorded under the
     ledger's no-RC version.
  6. A ledger that cannot be read never changes the outcome or the triage
     writes; it prints one warning.
  7. Driven through the real sweep check, an escalation reaches the ledger.

Every fixture builds a sandbox kanban root under pytest's tmp_path; nothing
reads or writes the live install or a real provider.

Naming: function names describe behaviour under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import datetime
import json
import pathlib
import re
import textwrap

import pytest

from pgai_agent_kanban.metrics.blocked_ledger import NO_RC_VERSION, block_intervals
from pgai_agent_kanban.overwatch import block_triage as bt
from pgai_agent_kanban.overwatch.block_record import load_block_record, write_block_record
from tests.unit.shell_harness import run_bash

_CHECK = "scripts/lib/overwatch-checks/check-block-triage.sh"

_PROJECT = "triage-proj"
_TASK = "CODER-20260101-001-example-task"
_SOURCE = f"task:{_TASK}"
_BLOCKED_AT = "2026-01-01T10:00:00+00:00"
_EVIDENCE = "worktree=gone; branch=ai_feature/example; ahead=2; merged=no"
_README = "# Task: example\n\n## Role\nCODER\n\n## Goal\nDo the thing.\n"
_NOW = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
_NOW_ISO = "2026-01-01T12:00:00+00:00"


def _record(reason_code: str, *, cooldown: str = "none") -> str:
    return (
        f"reason_code: {reason_code}\nowner_role: CODER\nattempts: 0\n"
        f"first_blocked_at: {_BLOCKED_AT}\nlast_blocked_at: {_BLOCKED_AT}\ncooldown_until: {cooldown}\n"
        f"evidence: {_EVIDENCE}\nresume: fresh"
    )


def _status(record: str, *, needs_human: str = "yes") -> str:
    return (
        f"# Status\n\n## Task\n{_TASK}\n\n## State\nBLOCKED\n\n## Summary\nBlocked.\n\n"
        f"## Blockers\nsomething blocked\n\n## Needs Human\n{needs_human}\n\n## Block Record\n{record}\n"
    )


class Sandbox:
    """A throwaway kanban root with one project and one blocked task."""

    def __init__(self, root: pathlib.Path, *, active_rc: str = "v9.9.9") -> None:
        self.root = root
        self.project_root = root / "projects" / _PROJECT
        self.task_dir = self.project_root / "tasks" / _TASK
        self.queue = self.project_root / "tasks" / "queues" / "coder_backlog.md"
        (self.project_root / "overwatch" / "backups").mkdir(parents=True)
        (self.project_root / "overwatch" / "actions.log").write_text("", encoding="utf-8")
        (self.task_dir / "artifacts").mkdir(parents=True)
        (self.task_dir / "logs").mkdir()
        self.queue.parent.mkdir(parents=True)
        for name in ("locks", "logs", "tmp"):
            (root / name).mkdir()
        (self.project_root / "project.cfg").write_text(f"[project]\nproject_name = {_PROJECT}\n", encoding="utf-8")
        (self.project_root / "release-state.md").write_text(
            f"# Release State\n\n## Active RC\n{active_rc}\n\n## RC Opened At\n2026-01-01T00:00:00+00:00\n\n"
            "## RC Opened By Task\nnone\n",
            encoding="utf-8",
        )
        (root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\n", encoding="utf-8")
        (root / "kanban.cfg").write_text("[wake]\n", encoding="utf-8")

    @property
    def status(self) -> pathlib.Path:
        return self.task_dir / "status.md"

    @property
    def readme(self) -> pathlib.Path:
        return self.task_dir / "README.md"

    @property
    def ledger(self) -> pathlib.Path:
        return self.project_root / "metrics" / "blocked-intervals.jsonl"

    def seed(self, status_text: str) -> None:
        self.status.write_text(status_text, encoding="utf-8")
        self.readme.write_text(_README, encoding="utf-8")
        self.queue.write_text(f"# CODER Backlog\n\n## Queue\n\n- [B] {_TASK} — example\n", encoding="utf-8")

    def records(self) -> list:
        if not self.ledger.is_file():
            return []
        return [json.loads(line) for line in self.ledger.read_text(encoding="utf-8").splitlines() if line.strip()]

    def blocks(self) -> list:
        return block_intervals(str(self.ledger))

    def apply(self, **kwargs) -> bt.ApplyResult:
        return bt.apply_task(self.status, self.queue, project=_PROJECT, kanban_root=self.root, now=_NOW, **kwargs)

    def env(self) -> dict:
        return {
            "KANBAN_ROOT": str(self.root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
            "OVERWATCH_PROJECT": _PROJECT,
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.root / "tmp"),
            "HALT_OVERWATCH": "",
        }


@pytest.fixture
def sandbox(tmp_path: pathlib.Path) -> Sandbox:
    return Sandbox(tmp_path / "kanban")


def _section(text: str, heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}[ \t]*\n(.*?)(?=^## |\Z)", text, flags=re.M | re.S)
    return m.group(1).strip() if m else ""


# ---------------------------------------------------------------------------
# 1-2. act (requeue to the owner)
# ---------------------------------------------------------------------------


def test_requeue_opens_the_block_with_its_reason_code_then_closes_it_class_any(sandbox):
    sandbox.seed(_status(_record("zero_progress")))
    assert sandbox.records() == []

    result = sandbox.apply()  # the shipped handlers: zero_progress -> requeue_owner
    assert result.outcome == bt.OUTCOME_ACT and result.mutation == bt.MUTATION_PROMOTE

    records = sandbox.records()
    assert [r["kind"] for r in records] == ["block-start", "block-end"]
    start, end = records
    assert start["source"] == end["source"] == _SOURCE
    assert start["reason_code"] == "zero_progress"
    assert start["version"] == end["version"] == "v9.9.9"
    assert start["timestamp"] == _BLOCKED_AT
    assert (start["class"], start["cause"]) == ("any", "chain")
    assert end["class"] == "any"
    assert end["reason_code"] == bt.REQUEUE_LEDGER_REASON_CODE
    assert end["timestamp"] == _NOW_ISO
    (block,) = sandbox.blocks()
    assert block["class"] == "any" and block["resolution_class"] == "any"


def test_block_opened_by_the_record_write_gets_no_second_start(sandbox):
    sandbox.seed(_status(_record("zero_progress")).split("## Block Record")[0])
    write_block_record(
        sandbox.status,
        "zero_progress",
        "CODER",
        [_EVIDENCE],
        now=datetime.datetime(2026, 1, 1, 9, 0, tzinfo=datetime.UTC),
    )
    assert [r["kind"] for r in sandbox.records()] == ["block-start"]

    result = sandbox.apply()
    assert result.outcome == bt.OUTCOME_ACT

    records = sandbox.records()
    assert [r["kind"] for r in records] == ["block-start", "block-end"]
    assert records[0]["timestamp"] == "2026-01-01T09:00:00+00:00"
    assert records[1]["class"] == "any"


# ---------------------------------------------------------------------------
# 3. escalate
# ---------------------------------------------------------------------------


def test_escalation_marks_the_open_block_human_without_rewriting_a_line(sandbox):
    sandbox.seed(_status(_record("missing_readme")).split("## Block Record")[0])
    write_block_record(
        sandbox.status,
        "missing_readme",
        "CODER",
        [_EVIDENCE],
        now=datetime.datetime(2026, 1, 1, 9, 0, tzinfo=datetime.UTC),
    )
    before = sandbox.ledger.read_bytes()
    assert before

    result = sandbox.apply()
    assert result.outcome == bt.OUTCOME_ESCALATE

    after = sandbox.ledger.read_bytes()
    assert after.startswith(before), "an earlier ledger line was rewritten"
    added = [json.loads(line) for line in after[len(before) :].decode("utf-8").splitlines() if line.strip()]
    assert [(r["kind"], r["source"], r["class"]) for r in added] == [("block-escalate", _SOURCE, "human")]
    assert added[0]["reason_code"] == "missing_readme"
    (block,) = sandbox.blocks()
    assert block["class"] == "human" and block["escalated"] is True and block["end"] is None
    assert _section(sandbox.status.read_text(encoding="utf-8"), "Needs Human") == "yes"


def test_escalation_with_no_block_yet_opens_one_then_marks_it_human(sandbox):
    sandbox.seed(_status(_record("missing_readme")))

    result = sandbox.apply()
    assert result.outcome == bt.OUTCOME_ESCALATE

    assert [r["kind"] for r in sandbox.records()] == ["block-start", "block-escalate"]
    (block,) = sandbox.blocks()
    assert block["class"] == "human" and block["reason_code"] == "missing_readme"


def test_refused_handler_escalates_the_block_and_closes_nothing(sandbox):
    sandbox.seed(_status(_record("zero_progress")))

    def refusing(ctx):
        raise bt.HandlerRefused("the branch holds commits found nowhere else")

    result = sandbox.apply(handlers={"requeue_owner": refusing})
    assert result.outcome == bt.OUTCOME_ESCALATE

    assert [r["kind"] for r in sandbox.records()] == ["block-start", "block-escalate"]
    (block,) = sandbox.blocks()
    assert block["class"] == "human" and block["end"] is None


# ---------------------------------------------------------------------------
# 4-5. record, and a project between releases
# ---------------------------------------------------------------------------


def test_first_cool_down_opens_the_block_and_leaves_it_open(sandbox):
    sandbox.seed(_status(_record("provider_refusal")))

    result = sandbox.apply()
    assert result.outcome == bt.OUTCOME_RECORD

    (block,) = sandbox.blocks()
    assert block["source"] == _SOURCE and block["end"] is None
    assert block["reason_code"] == "provider_refusal" and block["class"] == "any"


def test_project_without_an_active_rc_records_under_the_no_rc_version(tmp_path):
    sb = Sandbox(tmp_path / "kanban", active_rc="none")
    sb.seed(_status(_record("zero_progress")))

    result = sb.apply()
    assert result.outcome == bt.OUTCOME_ACT

    assert [(r["kind"], r["version"]) for r in sb.records()] == [
        ("block-start", NO_RC_VERSION),
        ("block-end", NO_RC_VERSION),
    ]


# ---------------------------------------------------------------------------
# 6. Best-effort
# ---------------------------------------------------------------------------


def test_unreadable_ledger_never_changes_the_outcome(sandbox, capsys):
    sandbox.seed(_status(_record("zero_progress")))
    sandbox.ledger.parent.mkdir(parents=True)
    sandbox.ledger.write_text("not json at all\n", encoding="utf-8")

    result = sandbox.apply()

    assert result.outcome == bt.OUTCOME_ACT and result.mutation == bt.MUTATION_PROMOTE
    assert load_block_record(sandbox.status).attempts == 1
    assert "zero_progress: requeue_owner" in _section(sandbox.readme.read_text(encoding="utf-8"), "Triage Note")
    assert sandbox.ledger.read_text(encoding="utf-8") == "not json at all\n"
    err = capsys.readouterr().err
    assert "WARNING" in err and "blocked-intervals.jsonl" in err


def test_unreadable_ledger_never_changes_an_escalation(sandbox, capsys):
    sandbox.seed(_status(_record("missing_readme")))
    sandbox.ledger.parent.mkdir(parents=True)
    sandbox.ledger.write_text("not json at all\n", encoding="utf-8")

    result = sandbox.apply()

    assert result.outcome == bt.OUTCOME_ESCALATE
    assert _section(sandbox.status.read_text(encoding="utf-8"), "OVERWATCH Diagnosis").startswith("Block epoch")
    assert "[block-triage] WARNING: blocked-ledger block-escalate" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 7. Through the real sweep check
# ---------------------------------------------------------------------------


def test_escalation_through_the_sweep_check_reaches_the_ledger(sandbox, tmp_path):
    sandbox.seed(_status(_record("missing_readme"), needs_human="no"))
    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_CHECK}
        rc=0
        overwatch_check_block_triage || rc=$?
        echo "CHECK_EXIT=$rc"
    """)
    result = run_bash(tmp_path, script, extra_env=sandbox.env(), timeout=120)
    assert "CHECK_EXIT=0" in result.stdout, result.stderr
    assert "block-triage:missing_readme:escalate" in (sandbox.project_root / "overwatch" / "actions.log").read_text(
        encoding="utf-8"
    )

    (block,) = sandbox.blocks()
    assert block["source"] == _SOURCE and block["class"] == "human" and block["version"] == "v9.9.9"
