"""
decision_executor.py — carries out the one outcome
:func:`pgai_agent_kanban.overwatch.decide.decide` returned.

:func:`execute` takes the :class:`~pgai_agent_kanban.overwatch.decide.Outcome`
and the :class:`~pgai_agent_kanban.overwatch.decide.DecisionInput` it was
decided from, and does exactly what the outcome names, through writes that
already exist. It never asks a model anything and adds no mutation path.

``verb``
    Run under the HALT-first protocol and the project's repo lock
    (:func:`pgai_agent_kanban.overwatch.halt_first.run_halt_first`): every
    file the verb changes is backed up first
    (:func:`~pgai_agent_kanban.overwatch.halt_first.backup_file`), then the
    verb runs through its existing write:

    ============================  ===================================================
    verb                          existing write
    ============================  ===================================================
    requeue,                      block triage's handler
    prune-worktree-then-requeue,  (:func:`pgai_agent_kanban.overwatch.block_triage.
    recompute-gate, and the       apply_task`, ``escalate=False``), then the state
    allowlisted operator data     mutation the sweep check performs: BACKLOG through
    verbs (restamp-fixed-in)      the status-writer (``task_status.write_task_status``,
                                  Needs Human no) and the ``[ ]`` marker through
                                  ``ops.write.set_marker``; DONE through the
                                  status-writer
    reset-to-backlog              the status-writer: BACKLOG, Needs Human no
                                  (skipped, like block triage's own verbs, when
                                  ``triage_enabled`` is false); for a TESTER
                                  task's verification block, through
                                  ``cm.verification_retry.requeue_task`` (the
                                  RC's retry record and a Triage Note with the
                                  block cause first)
    close-unverified              the status-writer: WONT-DO, Needs Human no,
                                  for a TESTER task still blocked after its
                                  RC's one requeue (skipped like reset-to-backlog)
    clear-chain-halt              ``resolution_stage.clear_chain_halt`` (re-check,
                                  back up, ``ops.write.unhalt``)
    resolution-stage              ``resolution_stage.run_resolution_stage`` through
                                  ``block_stage_deferral.defer_to_stage``
    ============================  ===================================================

    A HALT defers the verb exactly as the protocol defers every OVERWATCH
    fix: nothing is backed up, run, stamped or recorded, and the next firing
    decides again. Only ``clear-chain-halt`` and ``resolution-stage``, whose
    subject is the project's chain HALT, run with that HALT present.

    A verb that cannot be carried out (a handler that refuses at execution
    time — counted on the Block Record as an attempt, so the row's own
    ``max_attempts`` ends the retries — a block triage now plans to
    escalate, a resolution stage that declines a task's ship-policy block)
    is never escalated as Needs Human: the task's own classifier row asks
    its question in the operator inbox instead
    (:func:`pgai_agent_kanban.overwatch.decide.inbox_after_verb`), as an
    ``inbox`` outcome does. A plan that changed under the lock (the task is
    no longer blocked, its block is inside a cool-down) changes nothing; the
    backups taken before the verb ran are kept.

``inbox``
    One operator-inbox entry (``resolution_stage.write_inbox_question``,
    which reuses an open entry asking the same question). For a task, under
    the same HALT-first envelope, its ``## Needs Human`` is set to no
    through the status-writer and ``## Blockers`` names the entry, so the
    chain carries on around the question: the task stays as it is (a
    BLOCKED task stays BLOCKED) and nothing ends with Needs Human raised.
    When every other task of the task's ``## Release Version`` is DONE or
    WONT-DO (:func:`release_waiting_on`), nothing else of that release can
    run: the entry's Meanwhile line and the Blockers note say the release
    is waiting on the task, naming the version, instead of promising that
    the chain carries on.

``runaway``
    The runaway detector's own stop (:func:`pgai_agent_kanban.overwatch.
    runaway.run`): a project-scoped chain HALT naming the counter, and one
    inbox entry. A project that already has a HALT is left alone.

``none``
    Nothing is written.

What every carried-out decision writes
--------------------------------------
* One ``## OVERWATCH Decision`` event in the project's release-state.md
  (``resolution_stage.write_decision_event``, lane ``decision-wake``), read
  by every existing Decision-event reader. Its first Evidence line is
  ``decision: kind=<kind> name=<verb|none> source=<subject> result=<status>``
  (:func:`task_history` counts a task's verbs from it, for decide's verb
  budgets). A resolution stage that appended its own event is not recorded
  twice. A project with no release-state.md gets no event, as the stage's
  own ``_record_decision`` does.
* One row in the project's OVERWATCH action log naming every backup, so
  ``team/scripts/overwatch-revert.sh`` can undo it.
* One blocked-ledger block-start/block-end pair for the subject's source
  (``task:<TASK-ID>``, ``halt:project``, ``project:<name>``), through
  ``task_status.stamp_ledger_block_start`` / ``stamp_ledger_block_end``:
  the start opens a block only when none is open (the status-writer's
  BLOCKED write normally opened it), the end closes it. An inbox entry on a
  BLOCKED task whose block is already closed (the question asked again
  after the operator closed the first entry) opens the block before the
  status-writer's write, at the Block Record's last_blocked_at or the
  decision time, so the start is never later than the end. The end's class is
  ``any`` for a verb that ran, ``human`` for a runaway stop and for an inbox
  entry that leaves its task BLOCKED (a task stopped on a human answer),
  ``any`` for any other inbox entry. A verb's own write that already closed
  the block (a return to BACKLOG closes it class any) leaves the end a
  no-op, so a decision never writes two pairs. ``ops.write.unhalt`` would
  close a HALT's block class human, so a chain-HALT clear (``clear-chain-halt``,
  and the resolution stage's own clear on a HALT subject) closes it class
  any immediately before the unhalt and opens it again if the HALT is still
  there afterwards.

A decision that carried nothing out — ``none``, a HALT-first deferral, an
inbox question already open with nothing new to change on the task, a
declined resolution stage on a chain HALT, a runaway on a project already
halted — writes no event, no action-log row and no ledger pair, so an
hourly sweep that keeps finding the same stop adds nothing.

A decided action that did not run, told to the operator
-------------------------------------------------------
Some decided actions do not run and would not run on a later firing either:
a verb ``[overwatch] block_triage = false`` keeps unrun while its task is
still stopped, and a HALT-first envelope that failed (the repair HALT could
not be written). Each writes one operator-inbox entry and nothing else
(:func:`report_not_carried_out`): what was decided, that it did not run and
why, and runnable commands that resolve the stop. The decision wake reports
an executor that raised the same way. The entry's question starts with a
fixed lead naming the action and the subject, and an open entry with that
lead and source is reused, so a firing that meets the same failure again
adds nothing. A HALT that defers the action, ``HALT_OVERWATCH`` and a busy
repo lock are not reported: the first two are the operator's own stops, and
the lock is free again on a later firing.

Usage (import)::

    from pgai_agent_kanban.overwatch.decide import decide
    from pgai_agent_kanban.overwatch.decision_executor import execute
    outcome = decide(inputs)
    result = execute(outcome, inputs, kanban_root=kanban_root)
    result.status, result.detail
"""

from __future__ import annotations

import dataclasses
import datetime
import pathlib
import re
import shlex
import sys
from pathlib import Path
from typing import Any
from collections.abc import Callable, Mapping

# Run by file path the module has no parent package; put this tree's own
# package root first so the package imports resolve from the same tree.
# Imported as a package member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban import task_status as ts  # noqa: E402
from pgai_agent_kanban.cm import verification_retry  # noqa: E402
from pgai_agent_kanban.cm.read_state_field import read_state_field  # noqa: E402
from pgai_agent_kanban.cm.release_version import task_release_version  # noqa: E402
from pgai_agent_kanban.lib.text import one_line  # noqa: E402
from pgai_agent_kanban.lib.timeutil import format_iso, moment_or_now, try_parse_iso  # noqa: E402
from pgai_agent_kanban.ops.context import OpsContext  # noqa: E402
from pgai_agent_kanban.ops.errors import OpsError  # noqa: E402
from pgai_agent_kanban.overwatch import block_triage as bt  # noqa: E402
from pgai_agent_kanban.overwatch import decide as dc  # noqa: E402
from pgai_agent_kanban.overwatch import inbox as operator_inbox  # noqa: E402
from pgai_agent_kanban.overwatch import runaway  # noqa: E402
from pgai_agent_kanban.overwatch.block_record import ACTION_VERBS, update_block_record  # noqa: E402
from pgai_agent_kanban.overwatch.block_stage_deferral import (  # noqa: E402
    STAGE_EXECUTED,
    StageRunner,
    defer_to_stage,
)
from pgai_agent_kanban.overwatch.halt_first import STATUS_FAILED as ENVELOPE_FAILED  # noqa: E402
from pgai_agent_kanban.overwatch.halt_first import backup_file, firing_timestamp, run_halt_first  # noqa: E402
from pgai_agent_kanban.overwatch.halt_provenance import installed_script_command, unhalt_command  # noqa: E402
from pgai_agent_kanban.overwatch.task_return import find_queue_file, return_to_backlog  # noqa: E402
from pgai_agent_kanban.overwatch.resolution_stage import (  # noqa: E402
    before_each_unhalt,
    clear_chain_halt,
    log_action,
    write_decision_event,
    write_inbox_question,
)

__all__ = [
    "LANE_DECISION_WAKE",
    "STATUS_DEFERRED",
    "STATUS_EXECUTED",
    "STATUS_INBOX",
    "STATUS_NOTHING",
    "STATUS_RUNAWAY",
    "ExecutionResult",
    "ask_operator",
    "execute",
    "find_queue_file",
    "release_waiting_on",
    "report_not_carried_out",
    "task_history",
]

#: A verb ran.
STATUS_EXECUTED = "executed"
#: An inbox entry was written, or a task's Needs Human cleared beside one.
STATUS_INBOX = "inbox"
#: The runaway detector stopped the project.
STATUS_RUNAWAY = "runaway"
#: Nothing was carried out and nothing written (see the module docstring).
STATUS_NOTHING = "nothing"
#: The HALT-first protocol deferred or declined the write, or the inbox or
#: runaway counters were locked: nothing written, the next firing decides again.
STATUS_DEFERRED = "deferred"

#: The Decision-event lane every executor event carries.
LANE_DECISION_WAKE = "decision-wake"

#: The action-log name every executor row carries.
_LOG_NAME = "decision-executor"

#: Blocked-ledger classes (``metrics.blocked_ledger`` vocabulary).
_CLASS_ANY = "any"
_CLASS_HUMAN = "human"

#: The resolution of an inbox entry and of a runaway stop, as the ledger's
#: block-end reason_code records it.
_LEDGER_INBOX = "inbox"
_LEDGER_RUNAWAY = "runaway"

#: Why a verb ``[overwatch] block_triage = false`` keeps unrun did not run.
_TRIAGE_OFF = "[overwatch] block_triage is false, which keeps OVERWATCH to records"

#: Task states that end a task, and those that mean it is not blocked.
_TERMINAL = frozenset({"DONE", "WONT-DO"})
_NOT_BLOCKED = frozenset({"DONE", "WONT-DO", "WORKING", "BACKLOG", "WAITING"})

#: The first Evidence line of an executor Decision event (liberal spacing).
_DECISION_LINE_RE = re.compile(
    r"^\s*-\s*decision:\s+kind=(\S+)\s+name=(\S+)\s+source=(\S+)\s+result=(\S+)\s*$", re.MULTILINE
)
#: The heading every Decision event carries in release-state.md.
_DECISION_HEADING = "OVERWATCH Decision"


@dataclasses.dataclass(frozen=True)
class ExecutionResult:
    """What :func:`execute` did.

    Attributes:
        status:          One of the ``STATUS_*`` values.
        outcome_kind:    The outcome's kind (``verb``, ``inbox``, ``runaway``, ``none``).
        detail:          One line saying what happened.
        verb:            The verb, for a verb outcome.
        verb_error:      Why the verb could not be carried out, when it could not
                         (the question then went to the inbox).
        inbox_entry:     The inbox entry written or found open, if any.
        inbox_created:   True when this call wrote the entry.
        backups:         Every backup taken.
        decision_event:  True when an ``## OVERWATCH Decision`` event was appended
                         (by the executor, or by the resolution stage it ran).
        ledger_source:   The blocked-ledger source of the pair, when one was written.
        ledger_class:    The class the pair's block-end carries, when one was written.
        halt_path:       The HALT that deferred the verb, or the runaway HALT written.
        halt_first:      The HALT-first envelope's status (``halt_first.STATUS_*``)
                         when it did not run the write; a caller deciding several
                         stops stops at ``declined-halt-overwatch`` and
                         ``declined-lock-busy``, as every later write would be.
    """

    status: str
    outcome_kind: str
    detail: str
    verb: str | None = None
    verb_error: str | None = None
    inbox_entry: str | None = None
    inbox_created: bool = False
    backups: tuple[str, ...] = ()
    decision_event: bool = False
    ledger_source: str | None = None
    ledger_class: str | None = None
    halt_path: str | None = None
    halt_first: str | None = None

    def as_json_dict(self) -> dict:
        """The result as a JSON-ready dict."""
        out = dataclasses.asdict(self)
        out["backups"] = list(self.backups)
        return out


@dataclasses.dataclass
class _VerbRun:
    """What one verb runner did: ``done``, ``skipped`` (nothing to do), or
    neither (``error`` says why it could not be carried out). A skip that
    leaves the block unresolved for a reason no later firing changes by
    itself names that reason in ``declined``."""

    done: bool = False
    skipped: bool = False
    detail: str = ""
    error: str = ""
    declined: str = ""
    verbs_run: list = dataclasses.field(default_factory=list)
    stage_event: bool = False
    closes_any: bool = False


class _Run:
    """One execution: the outcome, its inputs and everything resolved from them."""

    def __init__(
        self,
        outcome: dc.Outcome,
        inputs: dc.DecisionInput,
        *,
        kanban_root: Path | str,
        now: datetime.datetime | str | None,
        queue_file: Path | str | None,
        triage_enabled: bool,
        worktree_base: Path | str | None,
        table_path: Path | str | None,
        handlers: Mapping[str, bt.ActionHandler] | None,
        stage_runner: StageRunner | None,
        lock_wait_seconds: float | None,
        env: Mapping[str, str] | None,
    ) -> None:
        self.outcome = outcome
        self.inputs = inputs
        self.ctx = OpsContext(kanban_root=kanban_root)
        self.root = self.ctx.kanban_root
        self.project = inputs.project
        self.project_root = self.ctx.project_root(self.project)
        self.moment = moment_or_now(now if now is not None else inputs.now)
        self.stamp = format_iso(self.moment)
        self.firing = firing_timestamp(self.moment)
        self.task_id = inputs.task_id if inputs.subject in (dc.SUBJECT_BLOCK, dc.SUBJECT_NEEDS_HUMAN) else None
        self.status_file = self.ctx.tasks_dir(self.project) / self.task_id / "status.md" if self.task_id else None
        if queue_file is not None:
            self.queue_file: Path | None = Path(queue_file)
        elif self.task_id:
            self.queue_file = find_queue_file(self.project_root, self.task_id)
        else:
            self.queue_file = None
        self.triage_enabled = triage_enabled
        self.worktree_base = worktree_base
        self.table_path = table_path
        self.handlers = handlers
        self.stage_runner = stage_runner
        self.lock_wait_seconds = lock_wait_seconds
        self.env = env
        self.backups: list[str] = []
        self.ledger_closed = 0

    # -- paths and reads --------------------------------------------------

    @property
    def halt_path(self) -> Path:
        return self.project_root / "HALT"

    @property
    def release_state(self) -> Path:
        return self.project_root / "release-state.md"

    def has_task(self) -> bool:
        return self.status_file is not None and self.status_file.is_file()

    def status_text(self) -> str:
        assert self.status_file is not None
        return self.status_file.read_text(encoding="utf-8")

    def task_state(self) -> str:
        return ts.section_value(self.status_text(), "State").strip().upper()

    def active_rc(self) -> str:
        try:
            value = read_state_field(str(self.release_state), "Active RC").strip()
        except (OSError, UnicodeDecodeError):
            value = ""
        return value or dc.NO_ACTIVE_RC

    @property
    def subject_source(self) -> str:
        """The subject as the Decision event and the action log name it."""
        if self.task_id:
            return self.task_id
        if self.inputs.subject == dc.SUBJECT_HALT:
            return f"halt:{self.project}"
        return self.project

    @property
    def ledger_source(self) -> str:
        """The blocked-ledger source the decision's pair is stamped on."""
        if self.task_id:
            return ts.task_ledger_source(self.task_id)
        if self.inputs.subject == dc.SUBJECT_HALT:
            return "halt:project"
        return f"project:{self.project}"

    # -- backups, ledger, records ------------------------------------------

    def back_up(self, *paths: Path | None) -> None:
        """Back up every existing file in *paths* (first copy in the firing wins)."""
        for path in paths:
            if path is None or not path.is_file():
                continue
            copy = backup_file(self.root, self.project, path, self.firing)
            if copy not in self.backups:
                self.backups.append(copy)

    def task_files(self) -> tuple:
        if self.status_file is None:
            return ()
        return (self.status_file, self.status_file.parent / "README.md", self.queue_file)

    def _began(self) -> str:
        """When the stopped thing began: the Block Record's last_blocked_at, the
        HALT's modification time, else the decision time (never later than it)."""
        began: datetime.datetime | None = None
        record = self.inputs.record
        if self.task_id and record is not None and record.last_blocked_at:
            began = try_parse_iso(str(record.last_blocked_at))
        elif self.inputs.subject == dc.SUBJECT_HALT and self.halt_path.is_file():
            began = datetime.datetime.fromtimestamp(self.halt_path.stat().st_mtime, datetime.UTC)
        if began is None or began > self.moment:
            began = self.moment
        return format_iso(began)

    def _start_reason(self) -> str | None:
        record = self.inputs.record
        if self.task_id and record is not None:
            return record.reason_code
        if self.inputs.subject == dc.SUBJECT_HALT and self.inputs.halt is not None:
            return self.inputs.halt.writer
        return None

    def _task_is_stopped(self) -> bool:
        """BLOCKED, or Needs Human yes on a live task: the status-writer's own
        rule for when a task's ledger block is open."""
        if not self.has_task():
            return False
        text = self.status_text()
        state = ts.section_value(text, "State").strip().upper()
        needs_human = ts.section_value(text, "Needs Human").strip().lower() or "yes"
        return state == "BLOCKED" or (needs_human == "yes" and state not in _TERMINAL)

    def ledger_open(self) -> None:
        """Open the subject's block when none is open (a task only while it is stopped)."""
        if self.task_id and not self._task_is_stopped():
            return
        if self.inputs.subject == dc.SUBJECT_HALT and not self.halt_path.is_file():
            return
        ts.stamp_ledger_block_start(
            self.root, self.project, self.ledger_source, reason_code=self._start_reason(), timestamp=self._began()
        )

    def ledger_close(self, block_class: str, reason_code: str) -> int:
        """Close the subject's open block with *block_class*; 0 when none was open."""
        closed = ts.stamp_ledger_block_end(
            self.root,
            self.project,
            self.ledger_source,
            block_class=block_class,
            reason_code=reason_code,
            timestamp=self.stamp,
        )
        self.ledger_closed += closed
        return closed

    def record_decision(self, *, status: str, verbs_run: list, detail: str) -> bool:
        """Append the decision's ``## OVERWATCH Decision`` event; False when the
        project has no release-state.md."""
        if not self.release_state.is_file():
            return False
        kind = self.outcome.kind
        name = self.outcome.name or "none"
        evidence = [
            f"decision: kind={kind} name={name} source={self.subject_source} result={status}",
            f"why: {self.outcome.why}",
            *(one_line(line) for line in self.outcome.evidence),
            *self.backups,
        ]
        write_decision_event(
            str(self.release_state),
            timestamp=self.stamp,
            version=self.active_rc(),
            lane=LANE_DECISION_WAKE,
            evidence_refs=[line for line in evidence if line],
            verbs_run=[one_line(line) for line in verbs_run if one_line(line)],
            outcome=one_line(detail),
        )
        return True

    def log(self, action: str, detail: str) -> None:
        """One action-log row naming every backup (best-effort)."""
        try:
            log_action(
                str(self.root),
                self.project,
                _LOG_NAME,
                self.subject_source,
                action,
                one_line(detail),
                backup_path=",".join(self.backups) or "none",
            )
        except OSError:
            pass

    def result(self, status: str, detail: str, **fields: Any) -> ExecutionResult:
        return ExecutionResult(
            status=status,
            outcome_kind=self.outcome.kind,
            detail=one_line(detail),
            verb=self.outcome.name if self.outcome.kind == dc.OUTCOME_VERB else None,
            backups=tuple(self.backups),
            **fields,
        )

    # -- the HALT-first envelope ----------------------------------------------

    def under_halt_first(self, fix: Callable[[], ExecutionResult], *, op: str, past_project_halt: bool = False):
        envelope = run_halt_first(
            self.root,
            self.project,
            fix,
            op=op,
            task=self.task_id or "none",
            past_project_halt=past_project_halt,
            lock_wait_seconds=self.lock_wait_seconds,
            env=self.env,
        )
        if envelope.ran:
            return envelope.value
        if envelope.status == ENVELOPE_FAILED:
            # Nothing ran, and nothing will until the project directory takes
            # the repair HALT again: the operator is told.
            where = shlex.quote(str(self.project_root))
            return _report_declined(
                self,
                envelope.detail,
                f"Make {self.project_root} writable for OVERWATCH again (check it with `ls -ld {where}`)",
                STATUS_DEFERRED,
                envelope.detail,
                halt_path=envelope.halt_path,
                halt_first=envelope.status,
            )
        return self.result(STATUS_DEFERRED, envelope.detail, halt_path=envelope.halt_path, halt_first=envelope.status)


# ---------------------------------------------------------------------------
# Task status writes (the status-writer and the queue marker)
# ---------------------------------------------------------------------------


def _return_to_backlog(run: _Run, cleared: str) -> None:
    """BLOCKED -> BACKLOG as ``promote_blocked_to_backlog`` writes it: State,
    Blockers, Needs Human and Summary through the status-writer, Blocked By
    Agent and Blocked Reason reset where present, then the ``[ ]`` marker."""
    assert run.status_file is not None and run.task_id is not None
    return_to_backlog(
        run.status_file,
        run.queue_file,
        project=run.project,
        task_id=run.task_id,
        summary=f"Promoted from BLOCKED to BACKLOG by OVERWATCH: blocker cleared ({one_line(cleared)}).",
    )


def _clear_needs_human(run: _Run, entry_id: str | None) -> bool:
    """Set the task's Needs Human to no, and name a new inbox entry in its
    Blockers, through the status-writer; True when status.md changed. A task
    whose ## State is not one of the six states is left untouched."""
    assert run.status_file is not None and run.task_id is not None
    text = run.status_text()
    state = ts.section_value(text, "State").strip().upper()
    needs_human = ts.section_value(text, "Needs Human").strip().lower()
    if needs_human == "no" and not entry_id:
        return False
    try:
        state = ts.validate_state(state)
    except ts.TaskStatusError:
        return False
    blockers = None
    if entry_id:
        old = (ts.read_section(text, "Blockers") or "").strip()
        waiting = release_waiting_on(run.status_file.parent)
        if waiting:
            note = (
                f"OVERWATCH parked this question in the operator inbox as {entry_id}; Needs Human is cleared, and "
                f"release {waiting} is waiting on this task, its last open one, while the operator answers there."
            )
        else:
            note = (
                f"OVERWATCH parked this question in the operator inbox as {entry_id}; Needs Human is cleared so "
                "the chain carries on around it while the operator answers there."
            )
        blockers = f"{old}\n\n{note}" if old and old.lower() != "none" else note
    if state == "BLOCKED":
        # The status-writer's BLOCKED write opens the task's block at wall
        # clock when none is open (a question asked again after its first
        # entry was closed); opened here first, the block starts when the
        # stop did, never later than the decision time its end carries.
        run.ledger_open()
    ts.write_task_status(
        run.status_file,
        state=state,
        needs_human="no",
        blockers=blockers,
        project=run.project,
        key=run.task_id,
    )
    return True


# ---------------------------------------------------------------------------
# Verb runners
# ---------------------------------------------------------------------------


def _run_triage_verb(run: _Run, verb: str) -> _VerbRun:
    """A block-triage action: re-plan under the lock, apply it, perform the mutation."""
    assert run.status_file is not None and run.task_id is not None
    common = dict(
        triage_enabled=run.triage_enabled,
        now=run.moment,
        table_path=run.table_path,
        handlers=run.handlers,
        worktree_base=run.worktree_base,
        requeue_budget=run.inputs.requeue_budget,
    )
    # Re-planned under the lock with the facts read now, block triage's plan
    # is the authority: its action runs even when the caller's facts chose a
    # sibling action of the same row (the Decision event names the one run).
    plan = bt.plan_task(run.status_file, **common)
    if plan.outcome == bt.OUTCOME_ESCALATE:
        cause = plan.cause or plan.detail
        return _VerbRun(error=cause, verbs_run=[f"{verb} on {run.task_id}: block triage will not take it ({cause})"])
    if plan.outcome != bt.OUTCOME_ACT:
        detail = f"under the lock block triage plans {plan.outcome}: {plan.detail}"
        if not run.triage_enabled and bt.plan_task(run.status_file, **{**common, "triage_enabled": True}).outcome == (
            bt.OUTCOME_ACT
        ):
            # Only the switch keeps it unrun: with triage on, the same plan acts.
            return _VerbRun(skipped=True, detail=detail, declined=_TRIAGE_OFF)
        return _VerbRun(skipped=True, detail=detail)
    queue = run.queue_file if run.queue_file is not None else run.status_file.parent / "no-queue-line"
    backups = {"status.md": run.backups[0] if run.backups else "", "queue": ""}
    if run.queue_file is not None:
        backups["queue"] = next((b for b in run.backups if b.endswith(run.queue_file.name)), "")
    result = bt.apply_task(
        run.status_file, queue, project=run.project, kanban_root=run.root, backups=backups, escalate=False, **common
    )
    if result.outcome == bt.OUTCOME_ESCALATE:
        # The handler refused at execution time. A refused attempt is an
        # attempt: counting it lets the row's own max_attempts end the
        # retries, where an escalation's Needs Human used to.
        update_block_record(run.status_file, bump_attempts=True)
        return _VerbRun(error=result.detail, verbs_run=[f"{verb} on {run.task_id}: {result.detail}"])
    if result.outcome != bt.OUTCOME_ACT:
        return _VerbRun(skipped=True, detail=f"block triage reported {result.outcome}: {result.detail}")
    if result.mutation == bt.MUTATION_PROMOTE:
        _return_to_backlog(run, result.detail)
        mutation = "returned to BACKLOG"
    elif result.mutation.startswith(bt.MUTATION_TRANSITION_PREFIX):
        state = result.mutation[len(bt.MUTATION_TRANSITION_PREFIX) :]
        ts.write_task_status(run.status_file, state=state, project=run.project, key=run.task_id)
        mutation = f"moved to {state}"
    else:
        mutation = "left as it is"
    detail = f"{verb} on {run.task_id} ({result.action}); task {mutation}: {result.detail}"
    return _VerbRun(done=True, detail=detail, verbs_run=[detail], closes_any=True)


def _run_reset_to_backlog(run: _Run, verb: str) -> _VerbRun:
    """The status-writer's BACKLOG with Needs Human no, for a task still blocked."""
    assert run.task_id is not None
    if not run.triage_enabled:
        # [overwatch] block_triage = false keeps OVERWATCH to records: no
        # state or queue write, as block triage's own verbs skip under it.
        return _VerbRun(
            skipped=True,
            detail=f"[overwatch] block_triage is false: {verb} on {run.task_id} not carried out; " "nothing written",
            declined=_TRIAGE_OFF,
        )
    state = run.task_state()
    if state in _NOT_BLOCKED:
        return _VerbRun(
            skipped=True, detail=f"{run.task_id} reads ## State {state}, no longer blocked; nothing written"
        )
    args = run.outcome.args
    why = f"{verb} for {args.get('reason_code') or 'its block'}, use {args.get('use', 1)} of {args.get('budget', 1)}"
    if args.get("verification_retry"):
        # A TESTER task's verification block: spend the RC's one requeue
        # (record, Triage Note carrying the block cause, return to BACKLOG).
        record = verification_retry.requeue_task(
            run.root,
            run.project,
            str(args["verification_retry"]),
            run.task_id,
            case=verification_retry.CASE_TESTER_BLOCKED,
            cause=str(args.get("cause") or ""),
            requeued_by=verification_retry.BY_DECISION_WAKE,
            now=run.moment,
            project_root=run.project_root,
        )
        if record is None:
            return _VerbRun(
                skipped=True,
                detail=f"{run.task_id}: the verification requeue of {args['verification_retry']} is already spent; "
                "nothing written",
            )
        why = f"{why}; verification of {record.version} requeued once (cause: {record.cause})"
    else:
        _return_to_backlog(run, f"OVERWATCH decision {why}")
    detail = f"{verb} on {run.task_id}: returned to BACKLOG with Needs Human no ({why})"
    return _VerbRun(
        done=True,
        detail=detail,
        verbs_run=[f"task-status.sh --state BACKLOG --needs-human no: {detail}"],
        closes_any=True,
    )


def _run_close_unverified(run: _Run, verb: str) -> _VerbRun:
    """The status-writer's WONT-DO with Needs Human no, for a TESTER task still
    blocked after its RC's one verification requeue: its release, which
    waits on it, then runs and ships NON-FUNCTIONAL as unverified."""
    assert run.task_id is not None and run.status_file is not None
    if not run.triage_enabled:
        return _VerbRun(
            skipped=True,
            detail=f"[overwatch] block_triage is false: {verb} on {run.task_id} not carried out; " "nothing written",
            declined=_TRIAGE_OFF,
        )
    state = run.task_state()
    if state != "BLOCKED":
        return _VerbRun(skipped=True, detail=f"{run.task_id} reads ## State {state}, not BLOCKED; nothing written")
    args = run.outcome.args
    version, cause = str(args.get("version") or "the RC"), one_line(args.get("cause") or "no block cause recorded")
    ts.write_task_status(
        run.status_file,
        state="WONT-DO",
        needs_human="no",
        project=run.project,
        key=run.task_id,
        summary=(
            f"Closed by OVERWATCH: verification of {version} did not run after the RC's one requeue "
            f"(cause: {cause}). The release ships NON-FUNCTIONAL as unverified and records it in the operator inbox."
        ),
    )
    detail = f"{verb} on {run.task_id}: closed WONT-DO with Needs Human no (verification of {version} did not run)"
    return _VerbRun(
        done=True,
        detail=detail,
        verbs_run=[f"task-status.sh --state WONT-DO --needs-human no: {detail}"],
        closes_any=True,
    )


def _halt_block_closer(run: _Run, verb: str, closed: list) -> Callable[[], None]:
    """The callback a chain-HALT clear runs immediately before ``ops.write.unhalt``.

    ``ops.write.unhalt`` closes ``halt:project`` class human; an OVERWATCH
    clear is not a human's, so the callback opens the block if none is open
    and closes it class any first, appending the count closed to *closed*.
    """

    def _close_before_unhalt() -> None:
        run.ledger_open()
        closed.append(run.ledger_close(_CLASS_ANY, verb))

    return _close_before_unhalt


def _run_clear_chain_halt(run: _Run, verb: str) -> _VerbRun:
    """``clear_chain_halt``: re-check, back up, ``ops.write.unhalt``."""
    if not run.halt_path.is_file():
        return _VerbRun(skipped=True, detail=f"no HALT at {run.halt_path} any more; nothing written")
    writer = run.outcome.args.get("writer") or None
    closed: list = []
    clear = clear_chain_halt(
        run.ctx,
        str(run.root),
        run.project,
        step=f"ops.write unhalt --project {run.project}: cleared the {writer or 'chain'} HALT {run.halt_path}",
        writer=writer,
        timestamp=run.firing,
        before_unhalt=_halt_block_closer(run, verb, closed),
    )
    if clear.backup_path and clear.backup_path not in run.backups:
        run.backups.append(clear.backup_path)
    if closed and run.halt_path.is_file():
        run.ledger_open()  # the unhalt failed: the HALT stands, so its block does too
    if clear.cleared:
        line = f"{clear.step} (backup: {clear.backup_path})"
        return _VerbRun(done=True, detail=f"{verb}: {clear.step}", verbs_run=[line])
    return _VerbRun(skipped=True, detail=f"{verb} left the HALT in place: {clear.error}")


def _run_resolution_stage(run: _Run, verb: str) -> _VerbRun:
    """The resolution stage, once; for a task only when its block is ready for it."""
    if run.task_id is not None:
        plan = bt.plan_task(
            run.status_file,
            triage_enabled=run.triage_enabled,
            now=run.moment,
            table_path=run.table_path,
            handlers=run.handlers,
            worktree_base=run.worktree_base,
            requeue_budget=run.inputs.requeue_budget,
        )
        if plan.outcome != bt.OUTCOME_DEFER:
            return _VerbRun(error=f"the block is not one the resolution stage can take ({plan.detail})")
    closed: list = []
    # On a HALT subject the stage's own HALT clear closes halt:project class
    # any, as clear-chain-halt's does; a stage that declines clears nothing
    # and so closes nothing. A task subject's pair is the task's own.
    on_halt = run.task_id is None and run.inputs.subject == dc.SUBJECT_HALT
    closer = _halt_block_closer(run, verb, closed) if on_halt else None
    with before_each_unhalt(closer):
        deferral = defer_to_stage(run.root, run.project, runner=run.stage_runner)
    if closed and run.halt_path.is_file():
        run.ledger_open()  # the HALT stands after the stage, so its block does too
    line = f"{verb}: {deferral.detail}"
    if deferral.status == STAGE_EXECUTED:
        return _VerbRun(done=True, detail=line, verbs_run=[line], stage_event=deferral.decision_event_appended)
    if run.task_id is not None:
        return _VerbRun(error=deferral.detail, verbs_run=[line])
    return _VerbRun(skipped=True, detail=line)


_VERB_RUNNERS: Mapping[str, Callable[[_Run, str], _VerbRun]] = {
    dc.VERB_REQUEUE: _run_triage_verb,
    dc.VERB_PRUNE_WORKTREE_THEN_REQUEUE: _run_triage_verb,
    dc.VERB_RECOMPUTE_GATE: _run_triage_verb,
    dc.VERB_RESET_TO_BACKLOG: _run_reset_to_backlog,
    dc.VERB_CLOSE_UNVERIFIED: _run_close_unverified,
    dc.VERB_CLEAR_CHAIN_HALT: _run_clear_chain_halt,
    dc.VERB_RESOLUTION_STAGE: _run_resolution_stage,
    **{verb: _run_triage_verb for verb in ACTION_VERBS},
}

#: Verbs about the project's chain HALT, run with that HALT present.
_HALT_SUBJECT_VERBS = frozenset({dc.VERB_CLEAR_CHAIN_HALT, dc.VERB_RESOLUTION_STAGE})

#: Verbs that need a task folder to act on.
_TASK_VERBS = frozenset(
    {
        dc.VERB_REQUEUE,
        dc.VERB_PRUNE_WORKTREE_THEN_REQUEUE,
        dc.VERB_RECOMPUTE_GATE,
        dc.VERB_RESET_TO_BACKLOG,
        dc.VERB_CLOSE_UNVERIFIED,
        *ACTION_VERBS,
    }
)


def _verb_files(run: _Run, verb: str) -> tuple:
    """Every file *verb* may change, backed up before it runs."""
    files = list(run.task_files())
    if verb in ACTION_VERBS:
        args = run.outcome.args
        bugs_dir = run.ctx.bugs_dir(str(args.get("project") or run.project))
        for bug in str(args.get("bugs") or "").split(","):
            bug = bug.strip()
            if bug:
                files += sorted(bugs_dir.glob(f"{bug}.md")) + sorted(bugs_dir.glob(f"{bug}-*.md"))
    if verb in _HALT_SUBJECT_VERBS:
        files += [run.halt_path, run.release_state]
    return tuple(files)


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------


def release_waiting_on(task_dir: Path | str) -> str:
    """The release version waiting on the task in *task_dir* alone, or ``""``.

    That is the task README's ``## Release Version`` (read through
    :func:`~pgai_agent_kanban.cm.release_version.task_release_version`) when
    every other task folder beside it naming the same version reads
    ``## State`` DONE or WONT-DO. ``""`` when the task names no release
    version, or another task of that version is open — including one whose
    status.md is missing or names no state, which is never counted as
    finished. Reads only.
    """
    task_dir = Path(task_dir)
    version = task_release_version(task_dir / "README.md")
    if not version:
        return ""
    for readme in sorted(task_dir.parent.glob("*/README.md")):
        other = readme.parent
        if other.name == task_dir.name:
            continue
        try:
            if task_release_version(readme) != version:
                continue
            words = read_state_field(str(other / "status.md"), "State").split()
        except (OSError, UnicodeDecodeError):
            return ""  # unreadable: not proven finished
        if not words or words[0].upper() not in _TERMINAL:
            return ""
    return version


def _meanwhile(run: _Run) -> str:
    if run.task_id:
        waiting = release_waiting_on(run.status_file.parent) if run.status_file is not None else ""
        if waiting:
            return (
                f"Left {run.task_id} as it is with Needs Human cleared. Release {waiting} is waiting on it: every "
                f"other task of {waiting} is DONE or WONT-DO, so nothing else of that release runs until this "
                "question is answered."
            )
        return (
            f"Left {run.task_id} as it is with Needs Human cleared, so the chain carries on around this question; "
            "every other task keeps running."
        )
    return "Left the project as it is; the chain carries on around this question."


def _needs_human(run: _Run) -> str:
    return ts.section_value(run.status_text(), "Needs Human").strip().lower()


def _open_entry(run: _Run, inbox: dc.Outcome) -> str | None:
    """The open inbox entry already asking *inbox*'s question, matched as
    ``write_inbox_question`` matches it (source and question, spacing folded)."""
    wanted = (one_line(inbox.source or run.subject_source), one_line(inbox.question or inbox.detail))
    try:
        entries = operator_inbox.list_open(run.ctx, run.project)
    except operator_inbox.InboxError:
        return None
    return next((e.entry_id for e in entries if (one_line(e.source), one_line(e.question)) == wanted), None)


def _park(
    run: _Run, inbox: dc.Outcome, *, verb_error: str | None = None, verbs_run: list | None = None
) -> ExecutionResult:
    """Write *inbox*'s entry (reusing an open one), clear the task's Needs
    Human, and record it all when anything changed. Runs inside the envelope
    for a task, outside it otherwise."""
    already = _open_entry(run, inbox)
    if already is not None and not (run.has_task() and _needs_human(run) != "no"):
        return run.result(
            STATUS_NOTHING,
            f"inbox entry {already} is still open and nothing else needed changing",
            inbox_entry=already,
            verb_error=verb_error,
        )
    run.back_up(*run.task_files(), run.project_root / "operator-inbox.md")
    entry_id, created, error = write_inbox_question(
        run.ctx,
        run.project,
        source=inbox.source or run.subject_source,
        question=inbox.question or inbox.detail,
        meanwhile=_meanwhile(run),
        how_to_answer=inbox.how_to_answer or "read the evidence named in the question and resolve the cause",
    )
    if error is not None:
        return run.result(
            STATUS_DEFERRED, f"the inbox refused the question ({error}); nothing else written", verb_error=verb_error
        )
    changed = _clear_needs_human(run, entry_id if created else None) if run.has_task() else False
    if not created and not changed:
        return run.result(
            STATUS_NOTHING,
            f"inbox entry {entry_id} is still open and nothing else needed changing",
            inbox_entry=entry_id,
            verb_error=verb_error,
        )
    stopped = run.has_task() and run.task_state() == "BLOCKED"
    block_class = _CLASS_HUMAN if stopped else _CLASS_ANY
    run.ledger_open()
    run.ledger_close(block_class, _LEDGER_INBOX)
    written = f"inbox entry {entry_id} {'written' if created else 'still open'}"
    if run.task_id:
        written += f"; {run.task_id} Needs Human cleared" + (" (still BLOCKED)" if stopped else "")
    detail = f"{written}: {one_line(inbox.question or inbox.detail)}"
    if verb_error:
        detail = f"{run.outcome.name} could not be carried out ({one_line(verb_error)}); {detail}"
    event = run.record_decision(status=STATUS_INBOX, verbs_run=[*(verbs_run or []), written], detail=detail)
    run.log("inbox", detail)
    return run.result(
        STATUS_INBOX,
        detail,
        verb_error=verb_error,
        inbox_entry=entry_id,
        inbox_created=created,
        decision_event=event,
        ledger_source=run.ledger_source,
        ledger_class=block_class,
    )


def _execute_inbox(run: _Run) -> ExecutionResult:
    if run.has_task():
        return run.under_halt_first(lambda: _park(run, run.outcome), op="operator-inbox")
    return _park(run, run.outcome)


def _execute_runaway(run: _Run) -> ExecutionResult:
    try:
        stop = runaway.run(run.ctx, run.project, now=run.moment)
    except runaway.RunawayError as exc:
        return run.result(STATUS_DEFERRED, f"the runaway detector refused: {exc.error}: {exc.message}")
    if stop.outcome != runaway.OUTCOME_TRIPPED:
        halted = stop.outcome == runaway.OUTCOME_ALREADY_HALTED
        why = "the project already has a HALT" if halted else "no counter trips now"
        return run.result(STATUS_NOTHING, f"runaway detector: {stop.outcome} ({why}); nothing written")
    run.ledger_open()
    run.ledger_close(_CLASS_HUMAN, _LEDGER_RUNAWAY)
    reasons = "; ".join(trip.reason for trip in stop.trips)
    detail = f"runaway: HALT written at {stop.halt_path}, inbox entry {stop.inbox_entry or 'not written'} ({reasons})"
    verbs = [f"runaway detector: HALT {stop.halt_path}", *(f"warning: {w}" for w in stop.warnings)]
    event = run.record_decision(status=STATUS_RUNAWAY, verbs_run=verbs, detail=detail)
    run.log("runaway", detail)
    return run.result(
        STATUS_RUNAWAY,
        detail,
        inbox_entry=stop.inbox_entry or None,
        inbox_created=bool(stop.inbox_entry),
        decision_event=event,
        ledger_source=run.ledger_source,
        ledger_class=_CLASS_HUMAN,
        halt_path=stop.halt_path,
    )


def _carry_out_verb(run: _Run, verb: str, runner: Callable[[_Run, str], _VerbRun]) -> ExecutionResult:
    """The verb under the envelope: back up, run, then record or park."""
    run.back_up(*_verb_files(run, verb))
    if run.task_id:
        run.ledger_open()
    done = runner(run, verb)
    if done.skipped:
        if done.declined and run._task_is_stopped():
            return _report_declined(
                run,
                done.declined,
                "To let OVERWATCH run such verbs, set block_triage = true under [overwatch] in "
                f"{run.project_root / 'project.cfg'} (or kanban.cfg for every project)",
                STATUS_NOTHING,
                done.detail,
            )
        return run.result(STATUS_NOTHING, done.detail)
    if not done.done:
        inbox = dc.inbox_after_verb(run.inputs, verb, done.error)
        return _park(run, inbox, verb_error=done.error, verbs_run=done.verbs_run)
    run.ledger_open()
    run.ledger_close(_CLASS_ANY, verb)
    event = done.stage_event or run.record_decision(
        status=STATUS_EXECUTED, verbs_run=done.verbs_run, detail=done.detail
    )
    run.log(f"auto-fix:{verb}", done.detail)
    # The class is reported only for a block-end this verb wrote: its own
    # close (a chain-HALT clear's, immediately before the unhalt), or the
    # status-writer's BACKLOG/DONE write (class any).
    pair = bool(run.ledger_closed) or done.closes_any
    return run.result(
        STATUS_EXECUTED,
        done.detail,
        decision_event=event,
        ledger_source=run.ledger_source if pair else None,
        ledger_class=_CLASS_ANY if pair else None,
    )


def _execute_verb(run: _Run) -> ExecutionResult:
    verb = run.outcome.name or ""
    runner = _VERB_RUNNERS.get(verb)
    if runner is None or (verb in _TASK_VERBS and not run.has_task()):
        cause = (
            f"verb {verb!r} has no executor"
            if runner is None
            else f"{run.status_file or 'the task folder'} does not exist"
        )
        inbox = dc.inbox_after_verb(run.inputs, verb or "the verb", cause)
        if run.has_task():
            return run.under_halt_first(lambda: _park(run, inbox, verb_error=cause), op="operator-inbox")
        return _park(run, inbox, verb_error=cause)
    return run.under_halt_first(
        lambda: _carry_out_verb(run, verb, runner),
        op=verb,
        past_project_halt=verb in _HALT_SUBJECT_VERBS,
    )


def execute(
    outcome: dc.Outcome,
    inputs: dc.DecisionInput,
    *,
    kanban_root: Path | str,
    now: datetime.datetime | str | None = None,
    queue_file: Path | str | None = None,
    triage_enabled: bool = True,
    worktree_base: Path | str | None = None,
    table_path: Path | str | None = None,
    handlers: Mapping[str, bt.ActionHandler] | None = None,
    stage_runner: StageRunner | None = None,
    lock_wait_seconds: float | None = None,
    env: Mapping[str, str] | None = None,
) -> ExecutionResult:
    """Carry out *outcome*, decided from *inputs* (see the module docstring).

    Args:
        outcome:           What :func:`~pgai_agent_kanban.overwatch.decide.decide` returned.
        inputs:            The inputs it was decided from.
        kanban_root:       The kanban root.
        now:               The decision time (default ``inputs.now``, else now).
        queue_file:        The queue file carrying the task's line (found under
                           the project's ``tasks/queues/`` when None).
        triage_enabled:    ``[overwatch] block_triage``, passed to block triage.
        worktree_base:     Where task worktrees live (block triage's residue check).
        table_path:        The classifier table (default: the shipped one).
        handlers:          Block-triage action handlers (default: its own).
        stage_runner:      The resolution stage (default: ``run_resolution_stage``).
        lock_wait_seconds: How long a busy repo lock is retried (HALT-first).
        env:               The environment HALT-first reads ``HALT_OVERWATCH`` from.

    Returns:
        The :class:`ExecutionResult`. Never raises for a write that could not
        be made through no fault of the caller: that is a ``deferred`` or
        ``nothing`` result naming why.

    Raises:
        OSError, ops.errors.OpsError, task_status.TaskStatusError: a file the decision needs could
            not be read or written mid-way; what was written before stays
            (backups first, so a revert can restore it).
    """
    run = _Run(
        outcome,
        inputs,
        kanban_root=kanban_root,
        now=now,
        queue_file=queue_file,
        triage_enabled=triage_enabled,
        worktree_base=worktree_base,
        table_path=table_path,
        handlers=handlers,
        stage_runner=stage_runner,
        lock_wait_seconds=lock_wait_seconds,
        env=env,
    )
    if outcome.kind == dc.OUTCOME_NONE:
        return run.result(STATUS_NOTHING, f"decision none ({outcome.why}): {outcome.detail}")
    if outcome.kind == dc.OUTCOME_RUNAWAY:
        return _execute_runaway(run)
    if outcome.kind == dc.OUTCOME_INBOX:
        return _execute_inbox(run)
    if outcome.kind == dc.OUTCOME_VERB:
        return _execute_verb(run)
    inbox = dc.inbox_after_verb(inputs, f"outcome {outcome.kind!r}", "the executor knows no such outcome")
    run.outcome = dataclasses.replace(outcome, kind=dc.OUTCOME_INBOX)
    if run.has_task():
        return run.under_halt_first(lambda: _park(run, inbox), op="operator-inbox")
    return _park(run, inbox)


# ---------------------------------------------------------------------------
# Telling the operator about a decided action that did not run
# ---------------------------------------------------------------------------


def ask_operator(
    kanban_root: Path | str,
    project: str,
    *,
    source: str,
    lead: str,
    question: str,
    meanwhile: str,
    how_to_answer: str,
) -> tuple:
    """Park *question* in *project*'s operator inbox unless it is already asked.

    An open entry whose source is *source* and whose question starts with
    *lead* (spacing folded) is the same question asked earlier, so nothing
    is written: *lead* names the condition, and the rest of the question
    may carry detail that changes between firings. *how_to_answer* gets the
    command that closes the entry appended. The write is the inbox writer
    ``resolution_stage.write_inbox_question`` uses.

    Returns:
        ``(entry_id, created, error)``: the entry's ID (None on failure),
        whether this call wrote it, and the refusal text when the inbox
        could not be read or written.
    """
    ctx = OpsContext(kanban_root=kanban_root)
    wanted_source, wanted_lead = one_line(source), one_line(lead)
    try:
        for entry in operator_inbox.list_open(ctx, project):
            if one_line(entry.source) == wanted_source and one_line(entry.question).startswith(wanted_lead):
                return entry.entry_id, False, None
    except operator_inbox.InboxError as exc:
        return None, False, str(exc)
    close = installed_script_command(ctx.kanban_root, "inbox.sh", "--project", project, "close", "--id")
    return write_inbox_question(
        ctx,
        project,
        source=source,
        question=question,
        meanwhile=meanwhile,
        how_to_answer=f"{how_to_answer.rstrip('. ')}. Then close this entry with {close} followed by its INBOX ID.",
    )


def _subject_label(inputs: dc.DecisionInput) -> str:
    """The stop as the decision wake names it: a task ID, ``halt:<project>`` or ``project:<project>``."""
    if inputs.task_id and inputs.subject in (dc.SUBJECT_BLOCK, dc.SUBJECT_NEEDS_HUMAN):
        return inputs.task_id
    return f"{inputs.subject}:{inputs.project}"


def _decided_phrase(outcome: dc.Outcome | None) -> str:
    if outcome is None:
        return ""
    if outcome.kind == dc.OUTCOME_VERB:
        return f"verb {outcome.name or 'with no name'}"
    if outcome.kind == dc.OUTCOME_INBOX:
        return "to park an operator-inbox question"
    if outcome.kind == dc.OUTCOME_RUNAWAY:
        return "a runaway stop"
    return f"outcome {outcome.kind}"


def _task_state_now(ctx: OpsContext, inputs: dc.DecisionInput) -> str:
    """The task's ``## State`` as it reads now; blank when it cannot be read."""
    if not inputs.task_id or inputs.subject not in (dc.SUBJECT_BLOCK, dc.SUBJECT_NEEDS_HUMAN):
        return ""
    try:
        text = (ctx.tasks_dir(inputs.project) / inputs.task_id / "status.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, OpsError):
        return ""
    return ts.section_value(text, "State").strip().upper()


def _resolve_yourself(ctx: OpsContext, inputs: dc.DecisionInput) -> str:
    """Runnable commands that resolve the stop by hand; blank for the project itself."""
    if inputs.task_id and inputs.subject in (dc.SUBJECT_BLOCK, dc.SUBJECT_NEEDS_HUMAN):
        target = ("--project", inputs.project, "--key", inputs.task_id)
        backlog = installed_script_command(
            ctx.kanban_root, "task-status.sh", *target, "--state", "BACKLOG", "--needs-human", "no"
        )
        wontdo = installed_script_command(
            ctx.kanban_root,
            "task-status.sh",
            *target,
            "--state",
            "WONT-DO",
            "--needs-human",
            "yes",
            "--summary",
            "Closed after an operator inbox question",
        )
        return (
            f"Or resolve it yourself: return {inputs.task_id} to BACKLOG with `{backlog}`, "
            f"or close it with `{wontdo}`"
        )
    if inputs.subject == dc.SUBJECT_HALT:
        unhalt = unhalt_command(ctx.kanban_root, inputs.project)
        return f"Or, once the HALT's cause is gone, clear it yourself with `{unhalt}`"
    return ""


def report_not_carried_out(
    kanban_root: Path | str,
    inputs: dc.DecisionInput,
    outcome: dc.Outcome | None,
    *,
    cause: str,
    fix: str,
) -> tuple:
    """Tell the operator, in one inbox entry, that a decision for *inputs* did not run.

    The question names the stop, what was decided (*outcome*; None when
    deciding itself failed), that it did not run and *cause*, and the
    task's state now. The answer is *fix* (what clears the cause), the
    commands that resolve the stop by hand, and the inbox close command;
    none carries a placeholder. Nothing but the inbox is written: not the
    task, not a Decision event, not the ledger. An open entry asking about
    the same subject and decision is reused (:func:`ask_operator`).

    Returns:
        ``(entry_id, created, error)``, as :func:`ask_operator` does.
    """
    ctx = OpsContext(kanban_root=kanban_root)
    subject = _subject_label(inputs)
    decided = _decided_phrase(outcome)
    if decided:
        lead = f"OVERWATCH decided {decided} for {subject}, but it did not run"
    else:
        lead = f"OVERWATCH could not decide what to do about {subject}"
    state = _task_state_now(ctx, inputs)
    standing = f" {inputs.task_id} is still {state}." if state else ""
    question = f"{lead}: {one_line(cause).rstrip('.')}.{standing} What should happen to it?"
    meanwhile = (
        f"Left {subject} as it is and wrote nothing else for it; OVERWATCH keeps deciding every other stop in "
        f"project {inputs.project}."
    )
    resolve = _resolve_yourself(ctx, inputs)
    how = f"{fix.rstrip('. ')}; the next sweep decides {subject} again."
    if resolve:
        how = f"{how} {resolve}."
    source = inputs.task_id if subject == inputs.task_id else inputs.project
    return ask_operator(
        kanban_root,
        inputs.project,
        source=source,
        lead=lead,
        question=question,
        meanwhile=meanwhile,
        how_to_answer=how,
    )


def _report_declined(run: _Run, why: str, fix: str, status: str, detail: str, **fields: Any) -> ExecutionResult:
    """*detail*'s result with the operator told: :data:`STATUS_INBOX` when the
    entry is new, else *status* naming the entry still open (or the refusal)."""
    entry_id, created, error = report_not_carried_out(run.root, run.inputs, run.outcome, cause=why, fix=fix)
    if error is not None:
        return run.result(
            status, f"{detail}; the operator inbox refused the report ({one_line(error)})", verb_error=why, **fields
        )
    said = f"operator inbox entry {entry_id} {'written' if created else 'still open'}"
    return run.result(
        STATUS_INBOX if created else status,
        f"{detail}; {said}",
        verb_error=why,
        inbox_entry=entry_id,
        inbox_created=created,
        **fields,
    )


# ---------------------------------------------------------------------------
# Reading the executor's own Decision events back
# ---------------------------------------------------------------------------


def _decision_events(text: str) -> list:
    """The body of every ``## OVERWATCH Decision`` event in *text*, in file
    order, found by the one section reader's pattern (``read_section`` walks
    the same matches for its ``last=True`` reading)."""
    normalised = text.replace("\r\n", "\n")
    pattern = ts.section_regex(_DECISION_HEADING)
    bodies = []
    match = pattern.search(normalised)
    while match is not None:
        bodies.append(match.group(2))
        match = pattern.search(normalised, match.end(2))
    return bodies


def task_history(release_state_path: Path | str, task_id: str) -> dc.TaskHistory:
    """How many times each verb ran on *task_id*, from the executor's Decision
    events in *release_state_path* — the history decide's verb budgets read.

    Only events whose ``decision:`` line names the task and ``result=executed``
    count; any other event (the resolution stage's, an older one) is read past.
    An absent or unreadable file is an empty history.
    """
    try:
        text = Path(release_state_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return dc.TaskHistory()
    uses: dict = {}
    for body in _decision_events(text):
        for kind, name, source, result in _DECISION_LINE_RE.findall(body):
            if kind == dc.OUTCOME_VERB and source == task_id and result == STATUS_EXECUTED:
                uses[name] = uses.get(name, 0) + 1
    return dc.TaskHistory(verb_uses=uses)
