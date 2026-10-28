"""
test_overwatch_decision_executor.py
===================================
Behaviour of the decision executor
(``pgai_agent_kanban.overwatch.decision_executor``) and the Python HALT-first
envelope it runs under (``pgai_agent_kanban.overwatch.halt_first``).

Covered:
  1. Per reason code, end to end (decide -> execute), iterated from
     REASON_CODES: every code's first decision leaves its task requeued,
     reset or with an operator-inbox entry, and never with Needs Human
     raised; each carried-out decision appends one Decision event and
     closes the task's blocked-ledger block.
  2. A verb runs through an existing operator write: the status-writer
     returns the task to BACKLOG with a ``[ ]`` marker, the files it changed
     are backed up with their pre-change bytes, one Decision event is
     appended (counted by block_stage_deferral's reader) and the action log
     row names every backup. restamp-fixed-in runs through the same path and
     stamps its bugs.
  3. HALT-first: an operator HALT, the fleet-wide HALT, HALT_OVERWATCH and a
     held repo lock each defer the verb with nothing written but the
     deferral row the bash protocol writes; the bash wrapper defers the same
     HALT the same way. A repair HALT is held while the verb runs (the bash
     side reads it as a live repair) and removed after; a dead repair's HALT
     is cleared first. Backups use the bash layout.
  4. Ledger: a verb's pair is class any, a runaway's and an inbox entry that
     leaves its task BLOCKED are class human; a chain-HALT clear closes
     ``halt:project`` class any even though ops.write.unhalt closes it class
     human.
  5. Inbox: the entry is written once; running the same outcome again writes
     nothing. A verb that cannot be carried out parks its question instead.
  6. Decision events: a release-state.md holding earlier events still parses
     through block_stage_deferral and the lane verdict's reader after the
     executor appends; task_history counts the executor's own verbs so a
     verb's budget is spent on the next decision.
  7. none writes nothing.
  8. A decided action that did not run is told to the operator once: a verb
     no executor carries out, a verb [overwatch] block_triage = false keeps
     unrun while the task stays stopped (nothing when it is no longer
     stopped), and a HALT-first envelope that could not write its repair
     HALT; each answer carries runnable commands and no placeholder.
  9. A TESTER task's own block while it verifies a release: the first is
     returned to BACKLOG with its block cause in the README's Triage Note
     and the RC's retry record; the next, with the requeue spent, is closed
     WONT-DO with Needs Human no; close-unverified on a task no longer
     blocked writes nothing.
  10. The inbox Meanwhile line and the Blockers note: when every other task
     of the blocked task's ## Release Version is DONE or WONT-DO, both say
     the release is waiting on it and name the version; when another task
     of that version is still open (or has no status.md), or the task names
     no release version, both keep today's text.

Every fixture builds a sandbox kanban root under pytest's tmp_path with the
kanban root variables set or cleared together; nothing reads or writes the
live install.
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import subprocess
from typing import Optional

import pytest

from pgai_agent_kanban.metrics.blocked_ledger import block_intervals, ledger_path_for_project
from pgai_agent_kanban.ops import verdict
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.locks import acquire_lock, repo_lock_path
from pgai_agent_kanban.overwatch import decide as dc
from pgai_agent_kanban.overwatch import decision_executor as dx
from pgai_agent_kanban.overwatch import halt_first as hf
from pgai_agent_kanban.overwatch import inbox, runaway
from pgai_agent_kanban.overwatch.block_record import (
    REASON_CODES,
    load_block_record,
    update_block_record,
    write_block_record,
)
from pgai_agent_kanban.overwatch.block_stage_deferral import STAGE_EXECUTED, count_decision_events
from pgai_agent_kanban.overwatch.halt_clearing import CauseCheck
from pgai_agent_kanban.overwatch.halt_provenance import (
    WRITER_CM_RELEASE,
    WRITER_ZERO_PROGRESS_CAP,
    classify_halt,
    provenance_lines,
)
from pgai_agent_kanban.overwatch.resolution_stage import (
    ResolutionStageOutcome,
    clear_chain_halt,
    write_decision_event,
)
from pgai_agent_kanban.task_status import (
    read_section,
    section_value,
    set_or_append_section,
    stamp_ledger_block_start,
)
from tests.unit.shell_harness import run_bash

_PROJECT = "exec-proj"
_TASK = "CODER-20260101-005-example-task"
_NOW = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
_PAST = "2026-01-01T11:00:00+00:00"
_FIRING = "2026-01-01T12:00:00Z"

_BRANCH = "worktree=gone; branch=ai_feature/example; ahead=2; merged=no"

# The first block per code a decide fixture needs: (owner, evidence, facts).
_FIRST = {
    "per_task_timeout": ("CODER", [_BRANCH, "timeout_s=1800"], {"killed_at": "1800s"}),
    "provider_refusal": ("CODER", ["span_s=12", "signature=model-not-found"], {}),
    "transient_api_error": ("WRITER", ["batch_log=none"], {}),
    "gate_merge_not_found": ("WRITER", [_BRANCH], {"branch_ancestor_of_source": True}),
    "gate_empty_commit": ("CODER", [_BRANCH], {"shas": "abc1234"}),
    "worktree_create_failed": ("CODER", [_BRANCH], {
        "worktree_residue_in_base": True, "residue_holds_no_unique_commits": True, "source_branch_exists": True,
    }),
    "ship_policy_halt": ("CM", [], {}),
    "agent_operator_data_action": (
        "CM", ["verb=restamp-fixed-in", f"project={_PROJECT}", "version=v9.9.9", "bugs=BUG-0412,BUG-0413"],
        {"action_targets_verified": True},
    ),
}


def _bug_text(name: str, status: str) -> str:
    return (
        f"# {name}\n\n## Status\n{status}\n\n## Symptom\nSomething misbehaves.\n\n"
        "## Fixed In\n<!-- Written by the release engine at the fixing release. -->\n\n"
        "## Public ID\n<!-- Assigned at first disclosure. -->\n"
    )


class Sandbox:
    """A throwaway kanban root with one project and one task."""

    def __init__(self, tmp_path: pathlib.Path) -> None:
        self.tmp = tmp_path
        self.root = tmp_path / "kanban"
        self.project_root = self.root / "projects" / _PROJECT
        self.task_dir = self.project_root / "tasks" / _TASK
        self.queue = self.project_root / "tasks" / "queues" / "coder_backlog.md"
        self.bugs = self.project_root / "bugs"
        self.repo = tmp_path / "repo"
        for d in (self.project_root / "overwatch" / "backups", self.task_dir / "artifacts", self.task_dir / "logs",
                  self.queue.parent, self.bugs, self.root / "locks", self.root / "tmp", self.root / "logs"):
            d.mkdir(parents=True, exist_ok=True)
        (self.project_root / "overwatch" / "actions.log").write_text("", encoding="utf-8")
        (self.project_root / "project.cfg").write_text(
            f"[project]\nproject_name = {_PROJECT}\ndev_tree_path = {self.repo}\nbranch_prefix = ai_\n",
            encoding="utf-8",
        )
        (self.root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\n", encoding="utf-8")
        (self.root / "kanban.cfg").write_text("[wake]\n", encoding="utf-8")
        self.release_state.write_text(
            "# Release State\n\n## Active RC\nv9.9.9\n\n## RC Opened At\n2026-01-01T00:00:00Z\n\n"
            "## RC Opened By Task\nCM-20260101-001-open-rc\n",
            encoding="utf-8",
        )
        (self.task_dir / "README.md").write_text(
            f"# Task\n\n## Task ID\n{_TASK}\n\n## Role\nCODER\n\n## Goal\nDo the thing.\n", encoding="utf-8"
        )
        self.status.write_text(
            f"# Status\n\n## Task\n{_TASK}\n\n## State\nWORKING\n\n## Summary\nWorking.\n\n"
            "## Blockers\nnone\n\n## Needs Human\nno\n",
            encoding="utf-8",
        )
        self.set_marker("A")
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)], check=True, capture_output=True)

    @property
    def status(self) -> pathlib.Path:
        return self.task_dir / "status.md"

    @property
    def release_state(self) -> pathlib.Path:
        return self.project_root / "release-state.md"

    @property
    def halt(self) -> pathlib.Path:
        return self.project_root / "HALT"

    @property
    def actions(self) -> str:
        return (self.project_root / "overwatch" / "actions.log").read_text(encoding="utf-8")

    def text(self) -> str:
        return self.status.read_text(encoding="utf-8")

    def field(self, heading: str) -> str:
        return section_value(self.text(), heading).strip()

    def set_marker(self, marker: str) -> None:
        self.queue.write_text(f"# CODER Backlog\n\n## Queue\n\n- [{marker}] {_TASK} — example\n", encoding="utf-8")

    def marker(self) -> str:
        line = next(line for line in self.queue.read_text(encoding="utf-8").splitlines() if _TASK in line)
        return line.split("[", 1)[1].split("]", 1)[0]

    def add_bug(self, bug_id: str, status: str = "done") -> pathlib.Path:
        path = self.bugs / f"{bug_id}-example-{status}.md"
        path.write_text(_bug_text(path.stem, status), encoding="utf-8")
        return path

    def block(self, code: str, evidence: list, *, owner: str = "CODER", cooldown: str = "") -> None:
        """Block the task as a block site does: BLOCKED, Needs Human yes, a Block Record."""
        text = self.text()
        for heading, body in (("State", "BLOCKED"), ("Needs Human", "yes"), ("Blockers", f"{code} block")):
            text = set_or_append_section(text, heading, body)
        self.status.write_text(text, encoding="utf-8")
        write_block_record(self.status, code, owner, evidence, now=_PAST)
        if cooldown:
            update_block_record(self.status, cooldown_until=cooldown)
        self.set_marker("B")
        # The block site's status write opens the task's ledger block.
        from pgai_agent_kanban.task_status import stamp_ledger_block_start, task_ledger_source

        stamp_ledger_block_start(self.root, _PROJECT, task_ledger_source(_TASK), reason_code=code, timestamp=_PAST)

    def inputs(self, **overrides) -> dc.DecisionInput:
        values = dict(
            subject=dc.SUBJECT_BLOCK, project=_PROJECT, task_id=_TASK, record=load_block_record(self.status),
            task_state=self.field("State"), active_rc="v9.9.9", now=_NOW, kanban_root=str(self.root),
            history=dx.task_history(self.release_state, _TASK),
        )
        values.update(overrides)
        return dc.DecisionInput(**values)

    def blocks(self, source: str = f"task:{_TASK}") -> list:
        ledger = ledger_path_for_project(str(self.root), _PROJECT)
        if not os.path.isfile(ledger):
            return []
        return [b for b in block_intervals(ledger) if b["source"] == source]

    def open_inbox(self) -> tuple:
        return inbox.list_open(OpsContext(kanban_root=self.root), _PROJECT)

    def backups(self) -> list:
        return sorted(p for p in (self.project_root / "overwatch" / "backups").rglob("*") if p.is_file())

    def tree(self) -> dict:
        """Every project file, byte for byte, except the sandbox plumbing."""
        return {
            p.relative_to(self.root).as_posix(): p.read_bytes()
            for p in sorted(self.root.rglob("*"))
            if p.is_file() and not p.relative_to(self.root).as_posix().startswith(("locks/", "tmp/", "logs/"))
        }


@pytest.fixture()
def box(tmp_path, monkeypatch) -> Sandbox:
    sandbox = Sandbox(tmp_path)
    for name in ("PGAI_TASKS_DIR", "PGAI_PROJECT_ROOT", "PGAI_PROJECT_NAME", "HALT_OVERWATCH",
                 "OVERWATCH_FIRING_TIMESTAMP", "OVERWATCH_LOCK_WAIT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("KANBAN_ROOT", str(sandbox.root))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(sandbox.root))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(sandbox.root / "tmp"))
    return sandbox


# Codes whose row waits one cool-down window before its first action; their
# representative first block has already waited it out.
_WAITS_A_FIRST_WINDOW = ("provider_refusal", "agent_transient_provider_error", "agent_unclassified")


def _block_first(box: Sandbox, code: str) -> dc.DecisionInput:
    """Block the task on *code*'s representative first block; return its inputs."""
    owner, evidence, facts = _FIRST.get(code, ("CODER", [], {}))
    if code == "agent_operator_data_action":
        box.add_bug("BUG-0412")
        box.add_bug("BUG-0413")
    box.block(code, evidence, owner=owner, cooldown=_PAST if code in _WAITS_A_FIRST_WINDOW else "")
    extra = {"transient_retry": (3, 3)} if code == "transient_api_error" else {}
    return box.inputs(facts=facts, **extra)


def _decide_and_execute(box: Sandbox, inputs: dc.DecisionInput, **kwargs) -> tuple:
    outcome = dc.decide(inputs)
    return outcome, dx.execute(outcome, inputs, kanban_root=box.root, now=_NOW, **kwargs)


# ---------------------------------------------------------------------------
# 1. Every reason code, end to end
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", REASON_CODES)
def test_every_reason_code_ends_requeued_reset_or_in_the_inbox_never_needs_human(box, code):
    events_before = count_decision_events(box.release_state)
    outcome, result = _decide_and_execute(box, _block_first(box, code))

    state = box.field("State")
    entries = [e for e in box.open_inbox() if e.source == _TASK]
    assert outcome.kind in (dc.OUTCOME_VERB, dc.OUTCOME_INBOX), (code, outcome)
    assert box.field("Needs Human") == "no", (code, result)
    assert state in ("BACKLOG", "WONT-DO", "DONE") or (state == "BLOCKED" and entries), (code, state, result)
    assert result.status in (dx.STATUS_EXECUTED, dx.STATUS_INBOX), (code, result)
    assert count_decision_events(box.release_state) == events_before + 1, code
    blocks = box.blocks()
    assert len(blocks) == 1 and blocks[0]["end"] is not None, (code, blocks)


def test_the_codes_that_return_to_backlog_do_so_through_the_status_writer(box):
    # zero_progress requeues its owner; invalid_state resets to BACKLOG.
    for code, verb in (("zero_progress", dc.VERB_REQUEUE), ("invalid_state", dc.VERB_RESET_TO_BACKLOG)):
        box.status.write_text(box.text().replace("## State\nBACKLOG", "## State\nWORKING"), encoding="utf-8")
        outcome, result = _decide_and_execute(box, _block_first(box, code))
        assert (outcome.kind, outcome.name) == (dc.OUTCOME_VERB, verb)
        assert result.status == dx.STATUS_EXECUTED, result
        assert box.field("State") == "BACKLOG" and box.field("Blockers") == "none"
        assert box.marker() == " "
        assert "Promoted from BLOCKED to BACKLOG by OVERWATCH" in box.field("Summary")


# ---------------------------------------------------------------------------
# 2. A verb through an existing write: backup, Decision event, action log
# ---------------------------------------------------------------------------


def test_a_verb_backs_up_what_it_changes_and_appends_one_decision_event(box):
    inputs = _block_first(box, "invalid_state")
    before_status = box.status.read_bytes()
    before_queue = box.queue.read_bytes()
    before_events = count_decision_events(box.release_state)

    _outcome, result = _decide_and_execute(box, inputs)

    assert result.status == dx.STATUS_EXECUTED and result.verb == dc.VERB_RESET_TO_BACKLOG
    status_copy = box.project_root / "overwatch" / "backups" / _FIRING / "project-root" / "tasks" / _TASK / "status.md"
    queue_copy = box.project_root / "overwatch" / "backups" / _FIRING / "project-root" / "tasks" / "queues" / box.queue.name
    assert status_copy.read_bytes() == before_status
    assert queue_copy.read_bytes() == before_queue
    assert str(status_copy) in result.backups and str(queue_copy) in result.backups
    assert count_decision_events(box.release_state) == before_events + 1
    event = read_section(box.release_state.read_text(encoding="utf-8"), "OVERWATCH Decision", last=True)
    assert f"Lane: {dx.LANE_DECISION_WAKE}" in event and "Version: v9.9.9" in event
    assert f"decision: kind=verb name=reset-to-backlog source={_TASK} result=executed" in event
    row = [line for line in box.actions.splitlines() if "\tdecision-executor\t" in line][-1].split("\t")
    assert row[3] == "auto-fix:reset-to-backlog"
    assert set(row[4].split(",")) == set(result.backups)


def test_restamp_fixed_in_runs_through_the_executor_and_requeues_its_owner(box):
    inputs = _block_first(box, "agent_operator_data_action")
    bugs = [box.bugs / "BUG-0412-example-done.md", box.bugs / "BUG-0413-example-done.md"]
    bug_before = [path.read_bytes() for path in bugs]

    outcome, result = _decide_and_execute(box, inputs)

    assert (outcome.kind, outcome.name) == (dc.OUTCOME_VERB, "restamp-fixed-in")
    assert result.status == dx.STATUS_EXECUTED, result
    copies = [box.project_root / "overwatch" / "backups" / _FIRING / "project-root" / "bugs" / p.name for p in bugs]
    assert ["v9.9.9" in (read_section(p.read_text(encoding="utf-8"), "Fixed In") or "") for p in bugs] == [True, True]
    assert [copy.read_bytes() for copy in copies] == bug_before
    assert box.field("State") == "BACKLOG" and box.marker() == " "
    assert box.field("Needs Human") == "no"
    assert "restamp-fixed-in ran with validated arguments" in (box.task_dir / "README.md").read_text(encoding="utf-8")


def test_a_verb_that_cannot_be_carried_out_parks_the_rows_question_instead(box):
    # The row says requeue onto the branch, but the dirty patch cannot be
    # saved (no worktree): the handler refuses, and the question is parked.
    inputs = _block_first(box, "gate_dirty_worktree")
    outcome, result = _decide_and_execute(box, inputs)

    assert (outcome.kind, outcome.name) == (dc.OUTCOME_VERB, dc.VERB_REQUEUE)
    assert result.status == dx.STATUS_INBOX and result.verb_error
    entry = next(e for e in box.open_inbox() if e.source == _TASK)
    assert "refused by the completion gate for uncommitted changes" in entry.question
    assert box.field("State") == "BLOCKED" and box.field("Needs Human") == "no"
    assert entry.entry_id in read_section(box.text(), "Blockers")
    assert read_section(box.text(), "OVERWATCH Diagnosis") is None


def test_a_refused_verb_is_not_retried_on_every_firing(box):
    _decide_and_execute(box, _block_first(box, "gate_dirty_worktree"))
    assert load_block_record(box.status).attempts == 1
    before = box.tree()

    # The next firing: the refused attempt spent the row's one attempt, so
    # the row asks its question, which is already open.
    outcome, again = _decide_and_execute(box, box.inputs())

    assert outcome.kind == dc.OUTCOME_INBOX, outcome
    assert again.status == dx.STATUS_NOTHING
    assert box.tree() == before


# ---------------------------------------------------------------------------
# 3. HALT-first
# ---------------------------------------------------------------------------


def _deferred_unchanged(box: Sandbox, result: dx.ExecutionResult, before: dict, *, row: bool = True) -> None:
    assert result.status == dx.STATUS_DEFERRED, result
    # The action log gains the deferral row and the shared emitter records
    # the refusal in the project's refusal ledger, as every refusal does.
    plumbing = (f"projects/{_PROJECT}/overwatch/actions.log", f"projects/{_PROJECT}/refusal-ledger.md")
    after = {k: v for k, v in box.tree().items() if k not in plumbing}
    before = {k: v for k, v in before.items() if k not in plumbing}
    assert after == before
    assert box.backups() == []
    if row:
        assert "\toverwatch-halt-first-fix\treset-to-backlog\tfix-deferred\t" in box.actions


def test_an_operator_halt_defers_the_verb_and_changes_nothing(box, capsys):
    inputs = _block_first(box, "invalid_state")
    box.halt.write_text("", encoding="utf-8")
    before = box.tree()

    _outcome, result = _decide_and_execute(box, inputs)

    _deferred_unchanged(box, result, before)
    assert result.halt_path == str(box.halt)
    assert "error:   overwatch.fix_deferred_halt_present" in capsys.readouterr().err


def test_the_fleet_wide_halt_defers_the_verb(box):
    inputs = _block_first(box, "invalid_state")
    (box.root / "HALT").write_text("", encoding="utf-8")
    before = box.tree()
    _outcome, result = _decide_and_execute(box, inputs)
    _deferred_unchanged(box, result, before)


def test_halt_overwatch_and_a_held_repo_lock_decline_the_verb(box, monkeypatch):
    inputs = _block_first(box, "invalid_state")
    before = box.tree()
    monkeypatch.setenv("HALT_OVERWATCH", "1")
    _outcome, result = _decide_and_execute(box, inputs)
    _deferred_unchanged(box, result, before, row=False)
    monkeypatch.delenv("HALT_OVERWATCH")

    held = acquire_lock(repo_lock_path(box.root, _PROJECT), holder="wake", pid=os.getpid(), op="test", task="none")
    try:
        _outcome, result = _decide_and_execute(box, inputs)
    finally:
        held.release()
    _deferred_unchanged(box, result, before, row=False)
    assert "holder=wake" in result.detail


def test_the_bash_protocol_defers_the_same_operator_halt_the_same_way(box, tmp_path):
    box.halt.write_text("", encoding="utf-8")
    script = (
        "set -uo pipefail\nsource scripts/lib/overwatch_protocol.sh\n"
        "fixture_fix() { echo RAN; }\noverwatch_halt_first_fix fixture_fix\necho \"RC=$?\"\n"
    )
    bash = run_bash(tmp_path, script, extra_env={"KANBAN_ROOT": str(box.root), "OVERWATCH_PROJECT": _PROJECT})
    assert "RC=5" in bash.stdout and "RAN" not in bash.stdout

    envelope = hf.run_halt_first(box.root, _PROJECT, lambda: "RAN", op="fixture_fix")
    assert envelope.status == hf.STATUS_DEFERRED_HALT and envelope.value is None
    rows = [line.split("\t") for line in box.actions.splitlines()]
    assert [r[1:4] for r in rows] == [["overwatch-halt-first-fix", "fixture_fix", "fix-deferred"]] * 2
    assert box.halt.read_bytes() == b""


def test_the_verb_runs_under_a_repair_halt_the_bash_side_reads_as_live(box, tmp_path):
    seen = {}

    def fix() -> str:
        script = f"source scripts/lib/overwatch_protocol.sh\noverwatch_repair_halt_state '{box.halt}'\n"
        seen["state"] = run_bash(tmp_path, script).stdout.strip()
        seen["provenance"] = classify_halt(box.halt, kanban_root=box.root)
        return "done"

    envelope = hf.run_halt_first(box.root, _PROJECT, fix, op="reset-to-backlog", task=_TASK)

    assert envelope.ran and envelope.value == "done"
    assert seen["state"] == "live"
    assert seen["provenance"].is_chain and seen["provenance"].writer == "overwatch-repair"
    assert not box.halt.exists()
    assert not repo_lock_path(box.root, _PROJECT).exists()


def test_a_dead_repairs_halt_is_cleared_before_the_verb_runs(box):
    box.halt.write_text(
        "# HALT created by OVERWATCH while it repairs this project (old_fix); removed when the repair ends.\n"
        "# overwatch-repair-halt: pid=999999999 start=1 fn=old_fix at=2026-01-01T00:00:00Z\n"
        "# halt-writer: overwatch-repair\n# halt-reason: OVERWATCH repair in progress (old_fix)\n",
        encoding="utf-8",
    )
    envelope = hf.run_halt_first(box.root, _PROJECT, lambda: "ran", op="reset-to-backlog")
    assert envelope.ran and not box.halt.exists()
    assert "stale-repair-halt-removed" in box.actions


def test_a_backup_lands_where_the_bash_backup_puts_it(box, tmp_path):
    script = f"source scripts/lib/overwatch_protocol.sh\noverwatch_backup_file '{box.status}'\n"
    env = {"KANBAN_ROOT": str(box.root), "OVERWATCH_PROJECT": _PROJECT, "OVERWATCH_FIRING_TIMESTAMP": "bash-firing"}
    bash_copy = pathlib.Path(run_bash(tmp_path, script, extra_env=env).stdout.strip().splitlines()[-1])
    python_copy = pathlib.Path(hf.backup_file(box.root, _PROJECT, box.status, "py-firing"))
    backups = box.project_root / "overwatch" / "backups"
    assert bash_copy.relative_to(backups / "bash-firing") == python_copy.relative_to(backups / "py-firing")


# ---------------------------------------------------------------------------
# 4. The ledger pair
# ---------------------------------------------------------------------------


def test_a_verb_writes_one_pair_class_any(box):
    _decide_and_execute(box, _block_first(box, "invalid_state"))
    blocks = box.blocks()
    assert len(blocks) == 1
    assert blocks[0]["end"] is not None and blocks[0]["class"] == "any"


def test_an_inbox_entry_that_stops_a_task_writes_one_pair_class_human(box):
    outcome, result = _decide_and_execute(box, _block_first(box, "missing_readme"))
    assert outcome.kind == dc.OUTCOME_INBOX and result.status == dx.STATUS_INBOX
    assert result.ledger_class == "human"
    blocks = box.blocks()
    assert len(blocks) == 1 and blocks[0]["end"] is not None and blocks[0]["class"] == "human"
    assert blocks[0]["resolution_reason_code"] == "inbox"


def test_a_runaway_stops_the_project_and_writes_one_pair_class_human(box):
    inputs = _block_first(box, "agent_unclassified")
    ctx = OpsContext(kanban_root=box.root)
    for hour in range(4):
        runaway.record_event(ctx, _PROJECT, runaway.COUNTER_REASON_REPEATS, key="", subject=_TASK,
                             detail="agent_unclassified", at=f"2026-01-01T0{hour + 1}:00:00+00:00")
    trips = runaway.evaluate(ctx, _PROJECT, now=_NOW)
    assert trips

    outcome, result = _decide_and_execute(box, box.inputs(runaway_trips=trips))

    assert outcome.kind == dc.OUTCOME_RUNAWAY and result.status == dx.STATUS_RUNAWAY, result
    assert box.halt.is_file() and "halt-writer: overwatch-runaway" in box.halt.read_text(encoding="utf-8")
    assert result.inbox_entry and any(e.entry_id == result.inbox_entry for e in box.open_inbox())
    blocks = box.blocks()
    assert len(blocks) == 1 and blocks[0]["class"] == "human" and blocks[0]["resolution_reason_code"] == "runaway"
    assert result.decision_event

    # Run again: the project is already halted, so nothing more is written.
    before = box.tree()
    again = dx.execute(outcome, inputs, kanban_root=box.root, now=_NOW)
    assert again.status == dx.STATUS_NOTHING and box.tree() == before


def test_a_chain_halt_clear_closes_the_halt_block_class_any(box):
    box.halt.write_text(
        "# HALT created by the zero-progress cap\n"
        + "\n".join(provenance_lines(WRITER_ZERO_PROGRESS_CAP, f"{_TASK} reached the zero-progress cap")) + "\n",
        encoding="utf-8",
    )
    halt_bytes = box.halt.read_bytes()
    inputs = dc.DecisionInput(
        subject=dc.SUBJECT_HALT, project=_PROJECT, halt=classify_halt(box.halt, kanban_root=box.root),
        cause_checks=(CauseCheck("task-reset", True, f"{_TASK} reads BACKLOG"),), now=_NOW,
        kanban_root=str(box.root),
    )
    outcome, result = _decide_and_execute(box, inputs)

    assert (outcome.kind, outcome.name) == (dc.OUTCOME_VERB, dc.VERB_CLEAR_CHAIN_HALT)
    assert result.status == dx.STATUS_EXECUTED, result
    assert not box.halt.exists()
    copy = box.project_root / "overwatch" / "backups" / _FIRING / "project-root" / "HALT"
    assert copy.read_bytes() == halt_bytes
    blocks = box.blocks("halt:project")
    assert len(blocks) == 1 and blocks[0]["class"] == "any" and blocks[0]["end"] is not None
    assert result.decision_event


def test_a_resolution_stage_that_clears_a_chain_halt_closes_the_halt_block_once_class_any(box):
    box.halt.write_text(
        "# HALT created by the CM ship policy\n"
        + "\n".join(provenance_lines(WRITER_CM_RELEASE, "ship policy stopped the release", trigger="5")) + "\n",
        encoding="utf-8",
    )
    stamp_ledger_block_start(box.root, _PROJECT, "halt:project", reason_code="halt_project", timestamp=_PAST)
    inputs = dc.DecisionInput(
        subject=dc.SUBJECT_HALT, project=_PROJECT, halt=classify_halt(box.halt, kanban_root=box.root), now=_NOW,
        kanban_root=str(box.root),
    )

    def stage(project: str, kanban_root: str) -> ResolutionStageOutcome:
        # The stage's own HALT clear: re-check, back up, ops.write.unhalt.
        clear = clear_chain_halt(
            OpsContext(kanban_root=kanban_root), kanban_root, project, step="ship-policy drill cleared the HALT",
            writer=WRITER_CM_RELEASE,
        )
        assert clear.cleared, clear.error
        return ResolutionStageOutcome(status=STAGE_EXECUTED, reason="the ship-policy drill ran")

    outcome, result = _decide_and_execute(box, inputs, stage_runner=stage)

    assert (outcome.kind, outcome.name) == (dc.OUTCOME_VERB, dc.VERB_RESOLUTION_STAGE)
    assert result.status == dx.STATUS_EXECUTED, result
    assert not box.halt.exists()
    ledger = pathlib.Path(ledger_path_for_project(str(box.root), _PROJECT))
    ends = [
        record for record in map(json.loads, ledger.read_text(encoding="utf-8").splitlines())
        if record["kind"] == "block-end" and record["source"] == "halt:project"
    ]
    assert len(ends) == 1, ends
    assert ends[0]["class"] == "any" and ends[0]["reason_code"] == dc.VERB_RESOLUTION_STAGE
    assert (result.ledger_source, result.ledger_class) == ("halt:project", "any")


def test_an_inbox_reask_on_a_still_blocked_task_opens_its_pair_no_later_than_it_closes(box):
    inputs = _block_first(box, "missing_readme")
    outcome, first = _decide_and_execute(box, inputs)
    assert first.status == dx.STATUS_INBOX and first.inbox_created
    # The operator closes the question but leaves the task BLOCKED, so the
    # task has no open ledger block when the next firing asks again.
    inbox.close_entry(OpsContext(kanban_root=box.root), _PROJECT, first.inbox_entry)
    assert box.field("State") == "BLOCKED" and all(b["end"] is not None for b in box.blocks())

    # A decision moment an hour after the first and long before wall clock.
    later = _NOW + datetime.timedelta(hours=1)
    again = dx.execute(outcome, box.inputs(now=later), kanban_root=box.root, now=later)

    assert again.status == dx.STATUS_INBOX and again.inbox_created and again.inbox_entry != first.inbox_entry
    blocks = box.blocks()
    assert len(blocks) == 2 and blocks[-1]["end"] is not None, blocks
    start = datetime.datetime.fromisoformat(blocks[-1]["start"])
    end = datetime.datetime.fromisoformat(blocks[-1]["end"])
    assert start <= end, blocks[-1]
    assert end == later
    last_blocked_at = datetime.datetime.fromisoformat(load_block_record(box.status).last_blocked_at)
    assert start in (last_blocked_at, later), blocks[-1]


def test_an_operator_halt_beside_decides_none_and_none_writes_nothing(box):
    box.halt.write_text("", encoding="utf-8")
    inputs = dc.DecisionInput(
        subject=dc.SUBJECT_HALT, project=_PROJECT, halt=classify_halt(box.halt, kanban_root=box.root), now=_NOW,
    )
    before = box.tree()
    outcome, result = _decide_and_execute(box, inputs)
    assert outcome.kind == dc.OUTCOME_NONE and result.status == dx.STATUS_NOTHING
    assert box.tree() == before


# ---------------------------------------------------------------------------
# 5. Inbox idempotence
# ---------------------------------------------------------------------------


def test_running_the_same_inbox_outcome_again_writes_nothing_more(box):
    inputs = _block_first(box, "missing_readme")
    outcome, first = _decide_and_execute(box, inputs)
    assert first.status == dx.STATUS_INBOX and first.inbox_created
    before = box.tree()

    again = dx.execute(outcome, box.inputs(), kanban_root=box.root, now=_NOW)

    assert again.status == dx.STATUS_NOTHING and again.inbox_entry == first.inbox_entry
    assert box.tree() == before


# ---------------------------------------------------------------------------
# 6. Decision events stay readable; the executor's own feed the budgets
# ---------------------------------------------------------------------------


def test_earlier_decision_events_still_parse_after_the_executor_appends(box):
    write_decision_event(
        str(box.release_state), timestamp="2025-12-31T00:00:00Z", version="v9.9.8", lane="B",
        evidence_refs=["projects/exec-proj/HALT"], verbs_run=["cancel-rc.sh --yes"], outcome="older event",
    )
    write_decision_event(
        str(box.release_state), timestamp="2025-12-31T01:00:00Z", version="v9.9.9", lane="chain-halt",
        evidence_refs=[], verbs_run=[], outcome="another older event",
    )
    assert count_decision_events(box.release_state) == 2

    _decide_and_execute(box, _block_first(box, "invalid_state"))

    assert count_decision_events(box.release_state) == 3
    last = verdict._last_decision(str(box.release_state))
    assert (last.lane, last.version) == (dx.LANE_DECISION_WAKE, "v9.9.9")
    assert "returned to BACKLOG" in last.outcome
    assert dx.task_history(box.release_state, _TASK).uses(dc.VERB_RESET_TO_BACKLOG) == 1


def test_a_spent_verb_budget_asks_on_the_next_block(box):
    _decide_and_execute(box, _block_first(box, "invalid_state"))
    assert box.field("State") == "BACKLOG"

    # The task blocks the same way again: the reset's budget (1) is spent.
    outcome, result = _decide_and_execute(box, _block_first(box, "invalid_state"))

    assert outcome.kind == dc.OUTCOME_INBOX, outcome
    assert result.status == dx.STATUS_INBOX
    assert box.field("State") == "BLOCKED" and box.field("Needs Human") == "no"


def test_the_result_is_json_ready(box):
    _outcome, result = _decide_and_execute(box, _block_first(box, "missing_readme"))
    data = json.loads(json.dumps(result.as_json_dict()))
    assert data["status"] == dx.STATUS_INBOX and data["inbox_entry"] == result.inbox_entry


# ---------------------------------------------------------------------------
# 8. A decided action that did not run reaches the operator inbox once
# ---------------------------------------------------------------------------


def _task_entries(box: Sandbox) -> list:
    return [e for e in box.open_inbox() if e.source == _TASK]


def test_a_verb_no_executor_carries_out_parks_one_question(box):
    inputs = _block_first(box, "agent_unclassified")
    outcome = dc.Outcome(kind=dc.OUTCOME_VERB, why="fixture", detail="fixture", name="drop-tables")

    first = dx.execute(outcome, inputs, kanban_root=box.root, now=_NOW)
    again = dx.execute(outcome, box.inputs(), kanban_root=box.root, now=_NOW)

    assert first.status == dx.STATUS_INBOX and "'drop-tables' has no executor" in first.verb_error, first
    assert "drop-tables could not be carried out" in first.detail, first
    assert again.status == dx.STATUS_NOTHING, again
    entries = _task_entries(box)
    assert len(entries) == 1 and entries[0].entry_id == first.inbox_entry, entries
    assert "<" not in entries[0].how_to_answer


def test_a_verb_block_triage_off_keeps_unrun_asks_once_and_writes_nothing_else(box):
    inputs = _block_first(box, "invalid_state")
    outcome = dc.decide(inputs)
    before = box.status.read_bytes()

    first = dx.execute(outcome, inputs, kanban_root=box.root, now=_NOW, triage_enabled=False)
    again = dx.execute(outcome, inputs, kanban_root=box.root, now=_NOW, triage_enabled=False)

    assert first.status == dx.STATUS_INBOX and first.inbox_created, first
    assert again.status == dx.STATUS_NOTHING and again.inbox_entry == first.inbox_entry, again
    (entry,) = _task_entries(box)
    assert entry.entry_id == first.inbox_entry
    assert "verb reset-to-backlog" in entry.question and "did not run" in entry.question
    assert "block_triage is false" in entry.question and f"{_TASK} is still BLOCKED" in entry.question
    assert "--state BACKLOG --needs-human no" in entry.how_to_answer and "<" not in entry.how_to_answer
    assert box.status.read_bytes() == before
    assert not first.decision_event and count_decision_events(box.release_state) == 0


def test_a_declined_verb_on_a_task_no_longer_stopped_asks_nothing(box):
    inputs = _block_first(box, "invalid_state")
    outcome = dc.decide(inputs)
    text = box.text().replace("## State\nBLOCKED", "## State\nBACKLOG")
    box.status.write_text(text.replace("## Needs Human\nyes", "## Needs Human\nno"), encoding="utf-8")

    result = dx.execute(outcome, inputs, kanban_root=box.root, now=_NOW, triage_enabled=False)

    assert result.status == dx.STATUS_NOTHING and result.inbox_entry is None, result
    assert box.open_inbox() == ()


def test_a_halt_first_envelope_that_fails_asks_the_operator_once(box, monkeypatch):
    inputs = _block_first(box, "invalid_state")
    outcome = dc.decide(inputs)

    def unwritable(_halt, _op):
        raise PermissionError(13, "Permission denied", str(box.halt))

    monkeypatch.setattr(hf, "write_repair_halt", unwritable)
    first = dx.execute(outcome, inputs, kanban_root=box.root, now=_NOW)
    again = dx.execute(outcome, inputs, kanban_root=box.root, now=_NOW)

    assert first.status == dx.STATUS_INBOX and first.halt_first == hf.STATUS_FAILED, first
    assert again.status == dx.STATUS_DEFERRED and again.inbox_entry == first.inbox_entry, again
    (entry,) = _task_entries(box)
    assert "repair HALT" in entry.question and "did not run" in entry.question
    assert "ls -ld" in entry.how_to_answer and "<" not in entry.how_to_answer
    assert box.field("State") == "BLOCKED" and not box.halt.exists()


# ---------------------------------------------------------------------------
# 9. A TESTER task's own block while it verifies a release: the RC's one
#    requeue, then close-unverified
# ---------------------------------------------------------------------------


def _verification_block(box: Sandbox, blockers: str) -> dc.DecisionInput:
    """Make the sandbox task a TESTER task verifying v9.9.9 and block it on
    agent_unclassified with *blockers*; return its inputs with the facts the
    decision wake gathers."""
    from pgai_agent_kanban.cm import verification_retry

    (box.task_dir / "README.md").write_text(
        f"# Task\n\n## Task ID\n{_TASK}\n\n## Role\nTESTER\n\n## Release Version\nv9.9.9\n", encoding="utf-8"
    )
    box.block("agent_unclassified", [], owner="TESTER")
    box.status.write_text(set_or_append_section(box.text(), "Blockers", blockers), encoding="utf-8")
    return box.inputs(facts=verification_retry.verification_facts(box.task_dir, "v9.9.9"))


def test_a_verification_block_is_requeued_once_with_its_cause_then_closed(box):
    record = box.project_root / "release-state" / "verification-retry-v9.9.9"

    outcome, result = _decide_and_execute(box, _verification_block(box, "the suite runner could not start"))
    assert (outcome.name, result.status) == (dc.VERB_RESET_TO_BACKLOG, dx.STATUS_EXECUTED), result
    assert box.field("State") == "BACKLOG" and box.field("Needs Human") == "no"
    assert box.marker() == " "
    note = read_section((box.task_dir / "README.md").read_text(encoding="utf-8"), "Triage Note")
    assert "the suite runner could not start" in note and "v9.9.9" in note
    assert "requeued_by=decision-wake" in record.read_text(encoding="utf-8")

    # The requeued attempt blocks again: the requeue is spent, so the task
    # is closed and the release waiting on it can run.
    outcome, result = _decide_and_execute(box, _verification_block(box, "disk full"))
    assert (outcome.name, result.status) == (dc.VERB_CLOSE_UNVERIFIED, dx.STATUS_EXECUTED), result
    assert box.field("State") == "WONT-DO" and box.field("Needs Human") == "no"
    assert "verification of v9.9.9 did not run" in box.field("Summary")
    assert record.read_text(encoding="utf-8").count("requeued_at=") == 1


def test_close_unverified_on_a_task_no_longer_blocked_writes_nothing(box):
    inputs = _verification_block(box, "runner crashed")
    outcome = dc.Outcome(
        kind=dc.OUTCOME_VERB, why=dc.WHY_VERIFICATION_UNVERIFIED, detail="test", name=dc.VERB_CLOSE_UNVERIFIED,
        args={"project": _PROJECT, "task_id": _TASK, "version": "v9.9.9", "cause": "runner crashed"},
    )
    box.status.write_text(box.text().replace("## State\nBLOCKED", "## State\nBACKLOG"), encoding="utf-8")
    result = dx.execute(outcome, inputs, kanban_root=box.root, now=_NOW)
    assert result.status == dx.STATUS_NOTHING, result
    assert box.field("State") == "BACKLOG"


# ---------------------------------------------------------------------------
# 10. The inbox Meanwhile line and Blockers note when the release waits on
#     the task
# ---------------------------------------------------------------------------

_TODAYS_MEANWHILE = (
    f"Left {_TASK} as it is with Needs Human cleared, so the chain carries on around this question; "
    "every other task keeps running."
)


def _release_task(box: Sandbox, version: str = "v9.9.9") -> None:
    """Give the sandbox task a ``## Release Version``."""
    (box.task_dir / "README.md").write_text(
        f"# Task\n\n## Task ID\n{_TASK}\n\n## Role\nCODER\n\n## Release Version\n{version}\n\n## Goal\nDo it.\n",
        encoding="utf-8",
    )


def _sibling(box: Sandbox, task_id: str, version: str, state: Optional[str]) -> None:
    """Another task folder of the project: README naming *version*, and a
    status.md reading *state* (none written when *state* is None)."""
    folder = box.task_dir.parent / task_id
    folder.mkdir()
    (folder / "README.md").write_text(
        f"# Task\n\n## Task ID\n{task_id}\n\n## Release Version\n{version}\n", encoding="utf-8"
    )
    if state is not None:
        (folder / "status.md").write_text(f"# Status\n\n## State\n{state}\n\n## Needs Human\nno\n", encoding="utf-8")


def _park_inbox(box: Sandbox) -> inbox.InboxEntry:
    """Block the task on a decision-only row (CODER: asked on the first look)
    and carry the inbox outcome out; the entry it wrote."""
    outcome, result = _decide_and_execute(box, _block_first(box, "missing_readme"))
    assert outcome.kind == dc.OUTCOME_INBOX and result.status == dx.STATUS_INBOX, result
    return next(e for e in box.open_inbox() if e.source == _TASK)


def test_the_last_open_task_of_its_release_says_the_release_waits_on_it(box):
    _release_task(box)
    _sibling(box, "CODER-20260101-006-done-sibling", "v9.9.9", "DONE")
    _sibling(box, "WRITER-20260101-007-declined-sibling", "ai_v9.9.9", "WONT-DO")
    _sibling(box, "CODER-20260101-008-other-release", "v9.9.8", "BACKLOG")

    entry = _park_inbox(box)

    assert "Release v9.9.9 is waiting on it" in entry.meanwhile, entry.meanwhile
    assert "every other task of v9.9.9 is DONE or WONT-DO" in entry.meanwhile, entry.meanwhile
    assert "every other task keeps running" not in entry.meanwhile
    blockers = read_section(box.text(), "Blockers") or ""
    assert entry.entry_id in blockers
    assert "release v9.9.9 is waiting on this task" in blockers, blockers
    assert "chain carries on" not in blockers, blockers
    assert dx.release_waiting_on(box.task_dir) == "v9.9.9"


@pytest.mark.parametrize(
    "state",
    [
        pytest.param("BACKLOG", id="backlog"),
        pytest.param("WAITING", id="waiting"),
        pytest.param("BLOCKED", id="blocked"),
        pytest.param(None, id="no-status-file"),
    ],
)
def test_another_open_task_of_the_release_keeps_todays_meanwhile_and_blockers(box, state):
    _release_task(box)
    _sibling(box, "CODER-20260101-006-done-sibling", "v9.9.9", "DONE")
    _sibling(box, "TESTER-20260101-009-open-sibling", "v9.9.9", state)

    entry = _park_inbox(box)

    assert entry.meanwhile == _TODAYS_MEANWHILE, entry.meanwhile
    blockers = read_section(box.text(), "Blockers") or ""
    assert "Needs Human is cleared so the chain carries on around it" in blockers, blockers
    assert dx.release_waiting_on(box.task_dir) == ""


def test_a_task_naming_no_release_version_keeps_todays_meanwhile(box):
    entry = _park_inbox(box)
    assert entry.meanwhile == _TODAYS_MEANWHILE, entry.meanwhile
    assert dx.release_waiting_on(box.task_dir) == ""
