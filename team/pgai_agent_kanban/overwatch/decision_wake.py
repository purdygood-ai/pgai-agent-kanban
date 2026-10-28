"""
decision_wake.py — the decision wake: every stop in one project, decided once
and carried out.

:func:`run_project` is what OVERWATCH's sweep runs for a project, through
``check-block-triage.sh``: the hourly ``overwatch-sweep.sh`` and the on-BLOCK
nudge's ``overwatch-sweep.sh --only block-triage`` both reach it. For each
stop it finds in the project it gathers, read-only, what
:func:`pgai_agent_kanban.overwatch.decide.decide` reads, calls ``decide``
once, and hands the one outcome to
:func:`pgai_agent_kanban.overwatch.decision_executor.execute`: a verb runs
under HALT-first and the repo lock, a question is parked in the operator
inbox, a runaway project is stopped, or nothing is done. Nothing here asks a
model anything.

The stops, in the order they are decided
----------------------------------------
``halt``         The project's own HALT. An operator's HALT is decided
                 ``none`` and never touched; a chain HALT is cleared
                 (``clear-chain-halt``) when one of its cause checks passes,
                 and otherwise goes to the resolution stage, which keys on
                 its writer and trigger.
``rc-failure``   A chain HALT CM's release wrote on one of its triggers:
                 the project's RC-level failure. It is decided as the HALT
                 subject above, so it is cleared once its trigger's cause
                 check passes (RC cancelled and rebundled, the TESTER task
                 reset, trigger 9 re-checked green) or goes to the
                 resolution stage.
``block``        Every task whose ``## State`` is BLOCKED, with its
                 ``## Block Record`` (an absent or unreadable record decides
                 inbox).
``needs-human``  Every BACKLOG or WAITING task whose ``## Needs Human`` reads
                 yes. A Block Record left on a task that is no longer
                 BLOCKED describes a block that is over, so the task is
                 decided without one: its question goes to the operator
                 inbox and Needs Human is cleared.
``project``      The project itself, only when a runaway counter trips and
                 nothing above is stopped, so a runaway still stops it.

The HALT goes first, so a chain HALT cleared now lets the task decisions run
in the same firing; while a HALT stands every task write is deferred by
HALT-first, as every other OVERWATCH fix is. A firing that finds the same
stop again adds nothing: an inbox question still open, a verb whose budget
is spent, a block inside its cool-down and a HALT that waits each decide
again and write nothing.

What each stop's inputs are read from
-------------------------------------
* Block Record, ``## Transient Retry`` and classifier facts: the task's
  status.md and, for the git-facing reason codes, read-only git
  (:func:`pgai_agent_kanban.overwatch.block_triage.classifier_facts`). When
  block triage's verification of an earlier requeue restarted the block
  epoch, the restarted record is the one decided on
  (:func:`~pgai_agent_kanban.overwatch.block_triage.plan_task`).
* The task's history: the executor's Decision events in release-state.md
  (:func:`pgai_agent_kanban.overwatch.decision_executor.task_history`), which
  is why a project needs a release-state.md for its decisions to be counted.
* ``[overwatch] requeue_budget``: project.cfg, then kanban.cfg.
* Runaway trips: the runaway detector's persisted counters, after it has
  observed the project's records (:func:`pgai_agent_kanban.overwatch.runaway.
  observe` and ``evaluate``).
* HALT provenance and the operator HALTs present: the project's and the
  kanban root's HALT (:func:`pgai_agent_kanban.overwatch.halt_provenance.
  classify_halt`).
* Cause checks: a CM HALT's trigger row (``clears_on`` in
  resolution-matrix.yaml, run by
  :func:`pgai_agent_kanban.overwatch.resolution_stage.run_cause_checks`); a
  zero-progress-cap HALT's task reset
  (:func:`pgai_agent_kanban.overwatch.halt_clearing.check_task_reset`).
* ``## Active RC``: release-state.md.
* A TESTER task's own block: the release it verifies, whether that RC's one
  verification requeue is spent, and its block cause
  (:func:`pgai_agent_kanban.cm.verification_retry.verification_facts`).

Writes
------
Everything the executor writes for an outcome it carries out, plus:

* the runaway detector's counters, observed once per run;
* the first cool-down window of a block whose classifier row waits one
  window before its first action (``provider_refusal``,
  ``agent_unclassified``, ``agent_transient_provider_error``, and a PM,
  TESTER or CM block on a decision-only row, which the classifier's
  pipeline ladder retries): block triage's own
  record step (:func:`~pgai_agent_kanban.overwatch.block_triage.apply_task`),
  under HALT-first, after a backup of status.md, with one action-log row. A
  window that is never recorded would restart on every firing;
* one ``decision-wake-declined`` action-log row when a run stops early, and
  one ``decision-wake-dry-run`` row per stop under ``--dry-run``, which
  otherwise writes nothing (the counters are evaluated, not observed);
* one operator-inbox entry for a stop whose decision or execution raised
  (:func:`pgai_agent_kanban.overwatch.decision_executor.
  report_not_carried_out`: what was decided, that it did not run, the
  error, and how to resolve the stop), and one for a runaway detector a
  setting turned off (a threshold that is not a whole number): it names the
  key, the file and the command that fixes it. Either is reused while it is
  open, so a later firing meeting the same failure adds none. A busy
  runaway-counters lock is not reported; the next firing reads them again.

Stopping early
--------------
A write declined because ``HALT_OVERWATCH`` is set or the project's repo lock
stayed held past ``--lock-wait`` would be declined the same way for every
later stop, so the run stops there and the stops after it are decided on the
next firing.

Usage (CLI; check-block-triage.sh runs it through ``pp_run_ops``)::

    python3 -m pgai_agent_kanban.overwatch.decision_wake --kanban-root DIR --project NAME \\
        [--triage-enabled true|false] [--worktree-base DIR] [--lock-wait SECONDS] \\
        [--now ISO] [--dry-run]

It prints one tab-separated line per stop on stdout: ITEM, SUBJECT, OUTCOME
(verb|inbox|runaway|none), VERB (``-`` for none), RESULT (the executor's
status: executed|inbox|runaway|nothing|deferred, or dry-run|failed) and
DETAIL. Exit 0 when every stop was decided (a deferral included), 1 when one
or more could not be decided or carried out (each printed dual-channel on
stderr; the rest were still decided), 2 for a usage error.

Usage (import)::

    from pgai_agent_kanban.overwatch.decision_wake import run_project
    report = run_project("/path/to/kanban", "example-project")
    for line in report.results:
        line.as_tsv()
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import pathlib
import sys
from pathlib import Path
from typing import Mapping, Optional

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
from pgai_agent_kanban.lib.error_contract import emit_error_contract, module_file_command  # noqa: E402
from pgai_agent_kanban.lib.text import one_line  # noqa: E402
from pgai_agent_kanban.lib.timeutil import moment_or_now, try_parse_iso  # noqa: E402
from pgai_agent_kanban.ops.context import OpsContext  # noqa: E402
from pgai_agent_kanban.ops.errors import ContractError, OpsError  # noqa: E402
from pgai_agent_kanban.overwatch import block_classifier as bc  # noqa: E402
from pgai_agent_kanban.overwatch import block_triage as bt  # noqa: E402
from pgai_agent_kanban.overwatch import decide as dc  # noqa: E402
from pgai_agent_kanban.overwatch import decision_executor as dx  # noqa: E402
from pgai_agent_kanban.overwatch import halt_first, runaway  # noqa: E402
from pgai_agent_kanban.overwatch.block_record import BlockRecordError, read_block_record  # noqa: E402
from pgai_agent_kanban.overwatch.block_requeue import (  # noqa: E402
    DEFAULT_REQUEUE_BUDGET,
    resolve_requeue_budget,
    transient_retry_state,
)
from pgai_agent_kanban.overwatch.halt_clearing import check_task_reset, task_id_in  # noqa: E402
from pgai_agent_kanban.overwatch.halt_provenance import (  # noqa: E402
    ORIGIN_OPERATOR,
    WRITER_CM_RELEASE,
    WRITER_ZERO_PROGRESS_CAP,
    HaltProvenance,
    classify_halt,
)
from pgai_agent_kanban.overwatch.resolution_matrix import ResolutionMatrixError, load_trigger_rows  # noqa: E402
from pgai_agent_kanban.overwatch.resolution_stage import log_action, run_cause_checks  # noqa: E402

__all__ = [
    "ITEM_BLOCK",
    "ITEM_HALT",
    "ITEM_NEEDS_HUMAN",
    "ITEM_PROJECT",
    "ITEM_RC_FAILURE",
    "RESULT_DRY_RUN",
    "RESULT_FAILED",
    "Stop",
    "StopResult",
    "WakeReport",
    "gather",
    "main",
    "run_project",
]

#: Item kinds, as the output's ITEM column names them.
ITEM_HALT = "halt"
ITEM_RC_FAILURE = "rc-failure"
ITEM_BLOCK = "block"
ITEM_NEEDS_HUMAN = "needs-human"
ITEM_PROJECT = "project"

#: RESULT for a stop decided under --dry-run (nothing carried out).
RESULT_DRY_RUN = "dry-run"
#: RESULT for a stop that could not be decided or carried out.
RESULT_FAILED = "failed"

#: Task states whose ``Needs Human: yes`` is a stop of its own. BLOCKED is a
#: block; WORKING belongs to a running agent; DONE and WONT-DO are closed.
_NEEDS_HUMAN_STATES = frozenset({"BACKLOG", "WAITING"})

#: HALT-first statuses that decline every later write of the run as well.
_STOPS_THE_RUN = frozenset({halt_first.STATUS_DECLINED_HALT_OVERWATCH, halt_first.STATUS_DECLINED_LOCK_BUSY})

#: The action-log name every row written here carries.
_LOG_NAME = "decision-wake"

#: The error key for a stop that could not be decided or carried out when
#: the failure carries no key of its own.
_ITEM_FAILED_KEY = "decision-wake.item-failed"

#: The failures a stop's gathering, decision or execution may raise for a
#: cause outside this module (an unreadable or unwritable file, a refused
#: operator write, a malformed value); each fails that stop alone.
_STOP_FAILURES = (OSError, UnicodeDecodeError, OpsError, ts.TaskStatusError, ValueError)

#: Runaway refusals the next firing clears by itself, so the operator is not
#: asked about them: the counters' lock was held by another reader.
_RUNAWAY_TRANSIENT = frozenset({"runaway.lock_busy"})


@dataclasses.dataclass(frozen=True)
class Stop:
    """One thing in the project that is stopped, with decide's inputs for it.

    Attributes:
        item:        One of the ``ITEM_*`` kinds.
        subject:     The task ID, or ``halt:<project>`` / ``project:<project>``.
        inputs:      What ``decide`` reads.
        status_file: The task's status.md, for a task stop.
    """

    item: str
    subject: str
    inputs: dc.DecisionInput
    status_file: Optional[Path] = None


@dataclasses.dataclass(frozen=True)
class StopResult:
    """What became of one stop.

    Attributes:
        stop:      The stop.
        result:    The executor's status, or :data:`RESULT_DRY_RUN` /
                   :data:`RESULT_FAILED`.
        detail:    One line saying what happened.
        outcome:   What ``decide`` returned (None when gathering or deciding failed).
        execution: What the executor returned, when it ran.
        error:     ``(error key, message, remedy)`` for a failed stop.
    """

    stop: Stop
    result: str
    detail: str
    outcome: Optional[dc.Outcome] = None
    execution: Optional[dx.ExecutionResult] = None
    error: Optional[tuple] = None

    def as_tsv(self) -> str:
        """ITEM, SUBJECT, OUTCOME, VERB, RESULT, DETAIL, tab-separated, one line."""
        kind = self.outcome.kind if self.outcome is not None else "-"
        verb = (self.outcome.name if self.outcome is not None else None) or "-"
        fields = (self.stop.item, self.stop.subject, kind, verb, self.result, self.detail)
        return "\t".join(one_line(field) or "-" for field in fields)


@dataclasses.dataclass
class WakeReport:
    """What one run did for one project.

    Attributes:
        project:  The project.
        results:  One :class:`StopResult` per stop decided, in order.
        stopped:  Why the run stopped before deciding every stop, if it did.
        warnings: Things that did not stop the run (the runaway counters
                  could not be read, a stop that could not be gathered).
    """

    project: str
    results: list = dataclasses.field(default_factory=list)
    stopped: Optional[str] = None
    warnings: list = dataclasses.field(default_factory=list)

    @property
    def failed(self) -> int:
        """How many stops could not be decided or carried out."""
        return sum(1 for line in self.results if line.result == RESULT_FAILED)


def _failure_fields(exc: BaseException, ctx: OpsContext, project: str) -> tuple:
    """``(error key, message, remedy)`` for *exc*: its own contract when it
    carries one, else a generic key with a dry-run of this project as the
    remedy (it re-reads and re-decides without writing)."""
    if isinstance(exc, ts.TaskStatusError):
        return exc.error_key, exc.message, exc.remedy
    if isinstance(exc, ContractError):
        return exc.error, exc.message, exc.remedy
    remedy = module_file_command(
        Path(__file__).resolve(), "--kanban-root", str(ctx.kanban_root), "--project", project, "--dry-run"
    )
    return _ITEM_FAILED_KEY, f"{type(exc).__name__}: {one_line(exc)}", remedy


# ---------------------------------------------------------------------------
# Gathering (read-only)
# ---------------------------------------------------------------------------


def _active_rc(release_state: Path) -> str:
    try:
        value = read_state_field(str(release_state), "Active RC").strip()
    except (OSError, UnicodeDecodeError):
        value = ""
    return value or dc.NO_ACTIVE_RC


def _cause_checks(ctx: OpsContext, project: str, provenance: HaltProvenance) -> tuple:
    """The cause checks of a chain HALT: a CM HALT's trigger row, a
    zero-progress-cap HALT's task reset; none for any other writer (the
    resolution stage keys on those itself)."""
    project_root = ctx.project_root(project)
    if provenance.writer == WRITER_ZERO_PROGRESS_CAP:
        return (check_task_reset(project_root, task_id_in(provenance.reason)),)
    if provenance.writer != WRITER_CM_RELEASE or not provenance.trigger:
        return ()
    try:
        row = load_trigger_rows(kanban_root=str(ctx.kanban_root)).get(provenance.trigger)
    except ResolutionMatrixError:
        return ()
    if row is None or not row.clears_on:
        return ()
    try:
        halt_text = Path(provenance.path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ()
    return tuple(
        run_cause_checks(row, str(ctx.kanban_root), project, str(project_root), provenance.path, halt_text)
    )


def _halt_stop(ctx: OpsContext, project: str, provenance: HaltProvenance, common: Mapping) -> Stop:
    checks = _cause_checks(ctx, project, provenance) if provenance.is_chain else ()
    item = ITEM_RC_FAILURE if provenance.is_chain and provenance.writer == WRITER_CM_RELEASE else ITEM_HALT
    inputs = dc.DecisionInput(
        subject=dc.SUBJECT_HALT, project=project, halt=provenance, cause_checks=checks, **common
    )
    return Stop(item, f"halt:{project}", inputs)


class _Budget:
    """``[overwatch] requeue_budget``, read once and only when a block needs it."""

    def __init__(self, ctx: OpsContext, project: str) -> None:
        self._ctx, self._project, self._value = ctx, project, None

    def get(self) -> int:
        if self._value is None:
            self._value = resolve_requeue_budget(self._ctx.kanban_root, self._project)
        return self._value


def _block_inputs(
    status: Path,
    text: str,
    state: str,
    *,
    project: str,
    release_state: Path,
    budget: _Budget,
    worktree_base: Optional[str],
    table_path: Optional[str],
    moment: datetime.datetime,
    common: Mapping,
) -> dc.DecisionInput:
    task_id = status.parent.name
    record, record_error, facts = None, None, {}
    try:
        record = read_block_record(text, status)
    except BlockRecordError as exc:
        record_error = exc.message
    requeue_budget = DEFAULT_REQUEUE_BUDGET
    if record is not None:
        requeue_budget = budget.get()
        plan = bt.plan_task(
            status, now=moment, table_path=table_path, worktree_base=worktree_base, requeue_budget=requeue_budget
        )
        if plan.restart_epoch and plan.record is not None:
            record = plan.record
        base = Path(worktree_base) if worktree_base else None
        facts = bt.classifier_facts(record.reason_code, text, record, status.parent, base)
        if record.reason_code == dc.VERIFICATION_REASON_CODE and (record.owner_role or "").upper() == "TESTER":
            # A TESTER task's own block while it verifies a release: whether
            # the RC's one requeue is spent, and the block cause it carries.
            facts = {**facts, **verification_retry.verification_facts(status.parent, common.get("active_rc", ""))}
    return dc.DecisionInput(
        subject=dc.SUBJECT_BLOCK, project=project, task_id=task_id, record=record, record_error=record_error,
        task_state=state, history=dx.task_history(release_state, task_id), facts=facts,
        transient_retry=transient_retry_state(text), requeue_budget=requeue_budget, **common,
    )


def gather(
    kanban_root: "Path | str",
    project: str,
    *,
    now: "datetime.datetime | str | None" = None,
    runaway_trips: tuple = (),
    worktree_base: Optional[str] = None,
    table_path: Optional[str] = None,
    warnings: Optional[list] = None,
) -> list:
    """Every stop in *project*, in the order they are decided, with decide's
    inputs. Reads only (block triage's plan and facts run read-only git).

    A task whose stop cannot be gathered (an unreadable status.md, a
    ``[overwatch] requeue_budget`` that is not a whole number) is left out
    and named in *warnings* when a list is given.
    """
    ctx = OpsContext(kanban_root=kanban_root)
    moment = moment_or_now(now)
    project_root = ctx.project_root(project)
    release_state = project_root / "release-state.md"
    project_halt = classify_halt(project_root / "HALT", kanban_root=ctx.kanban_root)
    fleet_halt = classify_halt(ctx.kanban_root / "HALT", kanban_root=ctx.kanban_root)
    common = dict(
        active_rc=_active_rc(release_state),
        runaway_trips=tuple(runaway_trips),
        operator_halts=tuple(p.path for p in (project_halt, fleet_halt) if p.origin == ORIGIN_OPERATOR),
        kanban_root=str(ctx.kanban_root),
        now=moment,
    )
    stops: list = []
    if (project_root / "HALT").is_file():
        stops.append(_halt_stop(ctx, project, project_halt, common))

    blocks, needs_human = [], []
    budget = _Budget(ctx, project)
    tasks_dir = ctx.tasks_dir(project)
    for status in sorted(tasks_dir.glob("*/status.md")) if tasks_dir.is_dir() else []:
        try:
            text = status.read_text(encoding="utf-8")
            state = ts.section_value(text, "State").strip().upper()
            if state == "BLOCKED":
                inputs = _block_inputs(
                    status, text, state, project=project, release_state=release_state, budget=budget,
                    worktree_base=worktree_base, table_path=table_path, moment=moment, common=common,
                )
                blocks.append(Stop(ITEM_BLOCK, status.parent.name, inputs, status))
            elif state in _NEEDS_HUMAN_STATES and ts.section_value(text, "Needs Human").strip().lower() == "yes":
                inputs = dc.DecisionInput(
                    subject=dc.SUBJECT_NEEDS_HUMAN, project=project, task_id=status.parent.name, task_state=state,
                    history=dx.task_history(release_state, status.parent.name), **common,
                )
                needs_human.append(Stop(ITEM_NEEDS_HUMAN, status.parent.name, inputs, status))
        except _STOP_FAILURES as exc:
            if warnings is not None:
                key, message, _remedy = _failure_fields(exc, ctx, project)
                warnings.append(f"{status.parent.name}: not decided this firing ({key}: {message})")
    stops += blocks + needs_human
    if runaway_trips and not stops:
        inputs = dc.DecisionInput(subject=dc.SUBJECT_PROJECT, project=project, **common)
        stops.append(Stop(ITEM_PROJECT, f"project:{project}", inputs))
    return stops


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def _report_detector_off(ctx: OpsContext, project: str, exc: runaway.RunawayError) -> str:
    """Ask the operator, once while it stays open, to fix what turned the
    runaway detector off for *project*; one line saying what was written."""
    lead = f"The runaway detector is off for project {project}"
    entry_id, created, error = dx.ask_operator(
        ctx.kanban_root, project, source=project, lead=lead,
        question=(
            f"{lead}: {one_line(exc.message)} Until this is fixed nothing stops {project} if it starts "
            "repeating itself (releases, RC cycles, the same block, inbox entries). Will you fix the setting?"
        ),
        meanwhile=f"OVERWATCH keeps deciding every stop in project {project}; only its runaway counters are not "
        "checked.",
        how_to_answer=(
            f"Fix the cause ({exc.error}); this command fixes or shows it: {exc.remedy} -- the next sweep reads "
            "the setting again and the detector is back on"
        ),
    )
    if error is not None:
        return f"the operator inbox refused the report ({one_line(error)})"
    return f"operator inbox entry {entry_id} {'written' if created else 'still open'}"


def _runaway_trips(
    ctx: OpsContext, project: str, moment: datetime.datetime, *, observe: bool, report: bool, warnings: list
):
    """The runaway counters that trip now (observing the project's records
    first unless *observe* is false); none when the counters cannot be read.
    A refusal the next firing does not clear by itself (a threshold that is
    not a whole number) is reported in the operator inbox when *report* is true."""
    try:
        if observe:
            runaway.observe(ctx, project, now=moment)
        return runaway.evaluate(ctx, project, now=moment)
    except runaway.RunawayError as exc:
        warning = f"runaway counters not read this firing ({exc.error}: {exc.message})"
        if report and exc.error not in _RUNAWAY_TRANSIENT:
            warning = f"{warning}; {_report_detector_off(ctx, project, exc)}"
        warnings.append(warning)
        return ()


def _record_first_cooldown(
    ctx: OpsContext,
    stop: Stop,
    *,
    moment: datetime.datetime,
    worktree_base: Optional[str],
    table_path: Optional[str],
    lock_wait_seconds: Optional[float],
    env: Optional[Mapping[str, str]],
) -> str:
    """Record a block's first cool-down window through block triage's own
    record step, under HALT-first; one line saying what happened."""
    project, task_id, status = stop.inputs.project, stop.subject, stop.status_file
    assert status is not None
    budget = stop.inputs.requeue_budget

    def _fix() -> str:
        plan = bt.plan_task(
            status, now=moment, table_path=table_path, worktree_base=worktree_base, requeue_budget=budget
        )
        if plan.outcome != bt.OUTCOME_RECORD:
            return f"no cool-down recorded: under the lock block triage plans {plan.outcome} ({plan.detail})"
        backup = halt_first.backup_file(ctx.kanban_root, project, status, halt_first.firing_timestamp(moment))
        queue = dx.find_queue_file(ctx.project_root(project), task_id) or status.parent / "no-queue-line"
        result = bt.apply_task(
            status, queue, project=project, kanban_root=ctx.kanban_root, backups={"status.md": backup, "queue": ""},
            now=moment, table_path=table_path, worktree_base=worktree_base, requeue_budget=budget,
        )
        log_action(
            str(ctx.kanban_root), project, _LOG_NAME, task_id, f"block-triage:{result.reason_code}:record",
            one_line(result.detail), backup_path=backup,
        )
        return f"block triage recorded the first cool-down: {result.detail}"

    envelope = halt_first.run_halt_first(
        ctx.kanban_root, project, _fix, op="record-cooldown", task=task_id, lock_wait_seconds=lock_wait_seconds,
        env=env,
    )
    return envelope.value if envelope.ran else envelope.detail


def _first_cooldown(outcome: dc.Outcome) -> bool:
    """True for a ``none`` whose classifier row waits a first window not yet recorded."""
    classifier = outcome.classifier
    return (
        outcome.kind == dc.OUTCOME_NONE
        and classifier is not None
        and classifier.why == bc.WHY_FIRST_COOLDOWN
        and bool(classifier.cooldown_until)
    )


def _failed(
    ctx: OpsContext, stop: Stop, exc: BaseException, outcome: Optional[dc.Outcome], *, report: bool
) -> StopResult:
    """The failed stop's result; with *report*, the operator is told in the
    inbox (what was decided, that it did not run, the error, the fix)."""
    fields = _failure_fields(exc, ctx, stop.inputs.project)
    detail = f"{fields[0]}: {fields[1]}"
    if report:
        try:
            entry_id, created, error = dx.report_not_carried_out(
                ctx.kanban_root, stop.inputs, outcome, cause=f"{fields[1]} ({fields[0]})",
                fix=f"Fix what the error names; this command shows or fixes it: {fields[2]}",
            )
        except _STOP_FAILURES as report_exc:
            entry_id, created, error = None, False, f"{type(report_exc).__name__}: {one_line(report_exc)}"
        if error is not None:
            detail += f"; the operator inbox refused the report ({one_line(error)})"
        else:
            detail += f"; operator inbox entry {entry_id} {'written' if created else 'still open'}"
    return StopResult(stop, RESULT_FAILED, detail, outcome, error=fields)


def _decide_one(
    ctx: OpsContext,
    stop: Stop,
    *,
    moment: datetime.datetime,
    dry_run: bool,
    triage_enabled: bool,
    worktree_base: Optional[str],
    table_path: Optional[str],
    lock_wait_seconds: Optional[float],
    env: Optional[Mapping[str, str]],
) -> StopResult:
    table = bc.load_table(table_path) if table_path else None
    outcome = dc.decide(stop.inputs, table=table)
    if dry_run:
        name = f" {outcome.name}" if outcome.name else ""
        detail = f"would carry out {outcome.kind}{name} ({outcome.why}): {outcome.detail}; nothing written"
        log_action(str(ctx.kanban_root), stop.inputs.project, _LOG_NAME, stop.subject, "decision-wake-dry-run",
                   one_line(detail))
        return StopResult(stop, RESULT_DRY_RUN, detail, outcome)
    try:
        execution = dx.execute(
            outcome, stop.inputs, kanban_root=ctx.kanban_root, now=moment, triage_enabled=triage_enabled,
            worktree_base=worktree_base, table_path=table_path, lock_wait_seconds=lock_wait_seconds, env=env,
        )
    except Exception as exc:  # noqa: BLE001 - one stop's failure is reported, never the end of the run
        return _failed(ctx, stop, exc, outcome, report=True)
    detail = execution.detail
    if _first_cooldown(outcome) and stop.status_file is not None:
        if triage_enabled:
            said = _record_first_cooldown(
                ctx, stop, moment=moment, worktree_base=worktree_base, table_path=table_path,
                lock_wait_seconds=lock_wait_seconds, env=env,
            )
        else:
            said = "[overwatch] block_triage is false: the first cool-down is not recorded"
        detail = f"{detail}; {said}"
    return StopResult(stop, execution.status, detail, outcome, execution)


def run_project(
    kanban_root: "Path | str",
    project: str,
    *,
    triage_enabled: bool = True,
    worktree_base: Optional[str] = None,
    lock_wait_seconds: Optional[float] = None,
    now: "datetime.datetime | str | None" = None,
    dry_run: bool = False,
    table_path: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
) -> WakeReport:
    """Decide every stop in *project* once and carry each outcome out (see
    the module docstring).

    Args:
        kanban_root:       The kanban root.
        project:           The project.
        triage_enabled:    ``[overwatch] block_triage`` for the project; false
                           keeps every task verb unrun (inbox questions are
                           still parked).
        worktree_base:     Where task worktrees live (block triage's residue check).
        lock_wait_seconds: How long a held repo lock is retried per write.
        now:               The decision time for the whole run (default now).
        dry_run:           Decide and narrate only; nothing is carried out.
        table_path:        The classifier table (default: the shipped one).
        env:               The environment HALT-first reads ``HALT_OVERWATCH`` from.

    Returns:
        The :class:`WakeReport`. A stop that fails is reported, not raised.
    """
    ctx = OpsContext(kanban_root=kanban_root)
    moment = moment_or_now(now)
    report = WakeReport(project)
    trips = _runaway_trips(ctx, project, moment, observe=not dry_run, report=not dry_run, warnings=report.warnings)
    stops = gather(
        ctx.kanban_root, project, now=moment, runaway_trips=trips, worktree_base=worktree_base,
        table_path=table_path, warnings=report.warnings,
    )
    for stop in stops:
        try:
            line = _decide_one(
                ctx, stop, moment=moment, dry_run=dry_run, triage_enabled=triage_enabled,
                worktree_base=worktree_base, table_path=table_path, lock_wait_seconds=lock_wait_seconds, env=env,
            )
        except _STOP_FAILURES as exc:
            line = _failed(ctx, stop, exc, None, report=not dry_run)
        report.results.append(line)
        execution = line.execution
        if execution is not None and execution.halt_first in _STOPS_THE_RUN:
            left = len(stops) - len(report.results)
            report.stopped = f"{one_line(execution.detail)}; {left} stop(s) left for the next firing"
            log_action(str(ctx.kanban_root), project, _LOG_NAME, project, "decision-wake-declined", report.stopped)
            break
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _seconds_arg(value: str) -> float:
    text = value.strip()
    if not text.isdigit():
        raise argparse.ArgumentTypeError(f"{value!r} is not a whole number of seconds")
    return float(text)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.overwatch.decision_wake",
        description=(
            "OVERWATCH decision wake for one project: find every stop (the project's HALT, an RC-level "
            "failure CM's release HALTed on, every BLOCKED task, every BACKLOG or WAITING task with Needs "
            "Human yes, and the project itself when a runaway counter trips with nothing else stopped), "
            "decide each once (pgai_agent_kanban.overwatch.decide) and carry the outcome out "
            "(pgai_agent_kanban.overwatch.decision_executor). Prints one tab-separated line per stop: ITEM, "
            "SUBJECT, OUTCOME, VERB, RESULT, DETAIL. Exit 0 when every stop was decided, 1 when one or more "
            "failed (printed dual-channel on stderr), 2 for a usage error."
        ),
    )
    parser.add_argument("--kanban-root", required=True, metavar="DIR", help="the kanban root holding projects/")
    parser.add_argument("--project", required=True, metavar="NAME", help="the project whose stops are decided")
    parser.add_argument(
        "--triage-enabled", type=bt.true_false_arg, default=True, metavar="true|false",
        help="the resolved [overwatch] block_triage value; false leaves every task verb unrun (default: true)",
    )
    parser.add_argument(
        "--worktree-base", metavar="DIR",
        help="the directory task worktrees live under (worktree.sh pgai_worktree_base); without it no "
        "worktree residue is shown to be inside it",
    )
    parser.add_argument(
        "--lock-wait", type=_seconds_arg, default=None, metavar="SECONDS",
        help="how long a held repo lock is retried per write before it is declined and the run stops "
        "(default: OVERWATCH_LOCK_WAIT_SECONDS, else 0)",
    )
    parser.add_argument("--now", metavar="ISO", help="the decision time (ISO-8601); default: the current UTC time")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="decide and print only: nothing is carried out, the runaway counters are not updated, and one "
        "decision-wake-dry-run action-log row is written per stop",
    )
    return parser


def main(argv: Optional[list] = None) -> int:
    """CLI entry point; returns the exit code."""
    parser = _parser()
    args = parser.parse_args(argv)
    if args.now is not None and try_parse_iso(args.now) is None:
        parser.error(f"--now {args.now!r} is not an ISO-8601 time (e.g. 2026-01-01T00:00:00+00:00)")
    project_root = Path(args.kanban_root) / "projects" / args.project
    if not project_root.is_dir():
        parser.error(f"project root {project_root} does not exist")
    report = run_project(
        args.kanban_root, args.project, triage_enabled=args.triage_enabled, worktree_base=args.worktree_base,
        lock_wait_seconds=args.lock_wait, now=args.now, dry_run=args.dry_run,
    )
    for warning in report.warnings:
        sys.stderr.write(f"decision-wake: {args.project}: warning: {one_line(warning)}\n")
    for line in report.results:
        sys.stdout.write(line.as_tsv() + "\n")
        if line.error is None:
            continue
        error, message, remedy = line.error
        # CONVERT: one stop not decided or not carried out this firing; the
        # stops around it still are, nothing is stopped, and the next firing
        # decides it again.
        # CENSUS-ANCHOR: decision-wake-stop-failed-01
        emit_error_contract(error, f"{line.stop.subject}: {message}", remedy, project=args.project)
    if report.stopped:
        sys.stderr.write(f"decision-wake: {args.project}: stopped early: {report.stopped}\n")
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
