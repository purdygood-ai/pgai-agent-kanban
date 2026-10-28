"""
decide.py — the decision wake's one function: what OVERWATCH does next about
something that stopped.

:func:`decide` is run for every new BLOCK, Needs Human and chain HALT (an
RC-level failure is the chain HALT CM's release wrote, decided as one), and
for the project itself when a runaway counter trips while nothing in it is
stopped. It returns exactly one :class:`Outcome`:

``verb``
    Run one existing operator write (:data:`VERBS`), named with its
    arguments. No verb is a new mutation path: each is a write the operator
    already has (the block-triage executor's handlers, the status-writer
    tool, the resolution stage, ``ops.write.unhalt``, the allowlisted
    operator data verbs).
``inbox``
    Park a question in the project's operator inbox and let the chain carry
    on around it: the question, its source and how to answer it.
``runaway``
    A runaway counter passed its threshold: the evidence names each counter
    and its last events. Only the runaway detector stops a project.
``none``
    Nothing to do now: an operator HALT (never touched), a block inside its
    cool-down window, a block its own handler still owns, a chain HALT its
    own writer clears.

It is pure: it reads only its arguments — the Block Record, the release
state, the task's history, the runaway trips and the HALT provenance the
caller gathered — and the classifier table (the shipped
``block-classifier.yaml`` unless the caller passes one). It writes nothing,
runs no command and asks no model anything. Carrying the outcome out is the
executor's job, under HALT-first and the repo lock.

Decision order
--------------
The first rule that applies wins:

1. An unknown subject                                     -> inbox
2. A HALT subject that is absent, or the operator's (the fleet-scoped
   HALT always is), or a chain HALT beside an operator HALT -> none
3. Any runaway trip, whatever the reason code             -> runaway
4. By subject:

   ``halt`` (a chain HALT): written by the runaway detector or an
   OVERWATCH repair -> none (its own writer clears it); a cause check
   passed -> verb ``clear-chain-halt``; otherwise -> verb
   ``resolution-stage`` (the stage keys on the writer and the CM trigger).

   ``block`` / ``needs_human``: no Block Record, or one that does not parse,
   -> inbox. A TESTER task that blocked itself (``agent_unclassified``)
   while verifying a release (the ``verification_*`` facts, gathered from
   :mod:`pgai_agent_kanban.cm.verification_retry`): the RC's one requeue
   still available and the RC open -> verb ``reset-to-backlog`` carrying the
   block cause (the executor spends the requeue); spent -> verb
   ``close-unverified``, so the release waiting on it runs and ships
   NON-FUNCTIONAL as unverified. A table that does not load -> inbox.
   Otherwise the classifier
   (:mod:`pgai_agent_kanban.overwatch.block_classifier`) decides, and:

   * ``transient_api_error`` below the transient handler's ceiling -> none
     (the handler requeues it); at the ceiling -> the row's decision;
   * not yet (a cool-down window) -> none;
   * a row action -> its verb (:data:`TRIAGE_ACTION_VERBS`; an allowlisted
     operator data action is the record's own verb and typed arguments),
     unless it returns the task to its owner and the Block Record's
     ``requeues`` has reached ``requeue_budget`` -> the row's decision;
   * no triage step, or attempts exhausted -> the row's decision; a
     decision-only row whose owner role the table's pipeline ladder names
     (PM, TESTER and CM in the shipped table) is classified on that ladder
     first, so it is requeued (verb ``requeue``, under the same requeue
     budget) until the ladder's attempts are spent;
   * an unmet precondition -> the row's inbox question (never its verb);
   * any other fail-closed verdict (no row, unreadable evidence, unknown
     owner) -> inbox.

   The row's decision is its verb while the task's history has used it
   fewer than ``budget`` times, then its inbox question.

   ``project`` (the project itself) -> none: only a runaway counter
   (rule 3) decides about a project with nothing stopped.

Usage (import)::

    from pgai_agent_kanban.overwatch.decide import DecisionInput, decide
    outcome = decide(DecisionInput(subject="block", project="example",
                                   task_id="CODER-20260101-001-example",
                                   record=record))
    if outcome.kind == "verb":
        ...  # outcome.name, outcome.args
"""

from __future__ import annotations

import dataclasses
import datetime
import shlex
from typing import Any, Mapping, Optional

from pgai_agent_kanban.overwatch import block_classifier as bc
from pgai_agent_kanban.overwatch.block_record import (
    ACTION_VERB_KEY,
    ACTION_VERBS,
    BlockRecord,
    parse_evidence,
    read_action_args,
)
from pgai_agent_kanban.overwatch.block_requeue import DEFAULT_REQUEUE_BUDGET
from pgai_agent_kanban.overwatch.halt_clearing import CauseCheck
from pgai_agent_kanban.overwatch.halt_provenance import (
    ORIGIN_CHAIN,
    ORIGIN_OPERATOR,
    WRITER_OVERWATCH_REPAIR,
    WRITER_OVERWATCH_RUNAWAY,
    HaltProvenance,
)
from pgai_agent_kanban.overwatch.runaway import Trip

__all__ = [
    "OUTCOMES",
    "SUBJECTS",
    "TRIAGE_ACTION_VERBS",
    "VERBS",
    "DecisionInput",
    "Outcome",
    "TaskHistory",
    "decide",
    "inbox_after_verb",
]

# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------

#: What stopped.
SUBJECT_BLOCK = "block"
SUBJECT_NEEDS_HUMAN = "needs_human"
SUBJECT_HALT = "halt"
#: The project itself, decided for its runaway counters alone: a counter can
#: trip while no task or HALT of the project is stopped.
SUBJECT_PROJECT = "project"
SUBJECTS: tuple[str, ...] = (SUBJECT_BLOCK, SUBJECT_NEEDS_HUMAN, SUBJECT_HALT, SUBJECT_PROJECT)

#: The four outcomes.
OUTCOME_VERB = "verb"
OUTCOME_INBOX = "inbox"
OUTCOME_RUNAWAY = "runaway"
OUTCOME_NONE = "none"
OUTCOMES: tuple[str, ...] = (OUTCOME_VERB, OUTCOME_INBOX, OUTCOME_RUNAWAY, OUTCOME_NONE)

#: Block triage's requeue to the owner (``promote_blocked_to_backlog`` after
#: the triage handler's preparatory step).
VERB_REQUEUE = "requeue"
#: Block triage's stale-worktree removal and prune, then the requeue.
VERB_PRUNE_WORKTREE_THEN_REQUEUE = "prune-worktree-then-requeue"
#: Block triage's read-only completion-gate recompute, then DONE.
VERB_RECOMPUTE_GATE = "recompute-gate"
#: The resolution stage (``run_resolution_stage``): the ship-policy drill,
#: cancel-rc and rebundle, and the chain-HALT rules keyed on provenance.
VERB_RESOLUTION_STAGE = "resolution-stage"
#: The status-writer tool's ``--state BACKLOG --needs-human no``.
VERB_RESET_TO_BACKLOG = bc.DECISION_VERB_RESET_TO_BACKLOG
#: The resolution stage's ``clear_chain_halt`` (back up, ``ops.write.unhalt``).
VERB_CLEAR_CHAIN_HALT = "clear-chain-halt"
#: The status-writer tool's ``--state WONT-DO --needs-human no`` for a TESTER
#: task whose verification did not run after the RC's one requeue.
VERB_CLOSE_UNVERIFIED = "close-unverified"

#: Every verb an outcome may name; the allowlisted operator data verbs
#: (``block_record.ACTION_VERBS``) are named as themselves.
VERBS: tuple[str, ...] = (
    VERB_REQUEUE,
    VERB_PRUNE_WORKTREE_THEN_REQUEUE,
    VERB_RECOMPUTE_GATE,
    VERB_RESOLUTION_STAGE,
    VERB_RESET_TO_BACKLOG,
    VERB_CLEAR_CHAIN_HALT,
    VERB_CLOSE_UNVERIFIED,
) + tuple(ACTION_VERBS)

#: The block-triage action that runs the record's allowlisted verb.
RUN_ALLOWLISTED_VERB = "run_allowlisted_verb"
#: The block-triage action that hands the block to the resolution stage.
DEFER_TO_RESOLUTION_STAGE = "defer_to_resolution_stage"

#: The verb each block-triage row action is carried out as.
#: ``run_allowlisted_verb`` is the record's own verb (see :data:`VERBS`).
TRIAGE_ACTION_VERBS: Mapping[str, str] = {
    "requeue_owner": VERB_REQUEUE,
    "prune_worktree_then_requeue": VERB_PRUNE_WORKTREE_THEN_REQUEUE,
    "recompute_gate": VERB_RECOMPUTE_GATE,
    DEFER_TO_RESOLUTION_STAGE: VERB_RESOLUTION_STAGE,
}

#: The transient-error reason code, whose requeue is its handler's own.
TRANSIENT_REASON_CODE = "transient_api_error"

#: The ``## Active RC`` value meaning no RC is open.
NO_ACTIVE_RC = "none"

#: The reason code a TESTER task records when it blocks itself.
VERIFICATION_REASON_CODE = "agent_unclassified"
#: The facts that say a blocked TESTER task is a release's verification
#: (the caller gathers them; see ``verification_retry.verification_facts``):
#: the release version it verifies, whether that RC's one requeue is spent,
#: and its block cause.
FACT_VERIFICATION_VERSION = "verification_version"
FACT_VERIFICATION_RETRY_SPENT = "verification_retry_spent"
FACT_VERIFICATION_CAUSE = "verification_cause"

#: Stands in for the kanban root in a command when the caller names none.
_ROOT_FALLBACK = '"${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"'

# Stable keys naming why an outcome came out as it did.
WHY_SUBJECT_UNKNOWN = "decide.subject-unknown"
WHY_NO_HALT = "decide.no-halt"
WHY_OPERATOR_HALT = "decide.operator-halt"
WHY_RUNAWAY = "decide.runaway"
WHY_HALT_OWN_WRITER = "decide.halt-cleared-by-its-writer"
WHY_HALT_CAUSE_RESOLVED = "decide.halt-cause-resolved"
WHY_HALT_TO_STAGE = "decide.halt-to-resolution-stage"
WHY_RECORD_ABSENT = "decide.record-absent"
WHY_RECORD_UNREADABLE = "decide.record-unreadable"
WHY_TABLE_INVALID = "decide.table-invalid"
WHY_TRANSIENT_HANDLER = "decide.transient-handler-owns-it"
WHY_NOT_YET = "decide.not-yet"
WHY_TRIAGE_ACTION = "decide.triage-action"
WHY_ROW_VERB = "decide.row-verb"
WHY_ROW_INBOX = "decide.row-inbox"
WHY_FAIL_CLOSED = "decide.fail-closed"
WHY_VERB_NOT_CARRIED_OUT = "decide.verb-not-carried-out"
WHY_NOTHING_STOPPED = "decide.nothing-stopped"
WHY_VERIFICATION_REQUEUE = "decide.verification-requeue"
WHY_VERIFICATION_UNVERIFIED = "decide.verification-did-not-run"


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class TaskHistory:
    """What earlier decisions already did to the task.

    Attributes:
        verb_uses: ``{verb: times a decision ran it on this task}``, counted
                   by the caller from the task's Decision events; a verb
                   absent from the mapping has not been used.
    """

    verb_uses: Mapping[str, int] = dataclasses.field(default_factory=dict)

    def uses(self, verb: str) -> int:
        """Times *verb* already ran on the task (0 for an unreadable count)."""
        value = self.verb_uses.get(verb, 0)
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


@dataclasses.dataclass(frozen=True)
class DecisionInput:
    """Everything :func:`decide` reads, gathered by the caller.

    Attributes:
        subject:         one of SUBJECTS.
        project:         the project's name.
        task_id:         the task, for ``block`` and ``needs_human``.
        record:          the task's parsed ``## Block Record``, or None.
        record_error:    why the Block Record did not parse, when it did not.
        task_state:      the task's ``## State``, for messages.
        active_rc:       the release state's ``## Active RC`` (``none`` or vX.Y.Z).
        history:         what earlier decisions did to the task.
        runaway_trips:   the runaway counters that trip now
                         (:func:`pgai_agent_kanban.overwatch.runaway.evaluate`).
        halt:            the project HALT's provenance, for ``halt``.
        operator_halts:  paths of the operator HALTs present (the project's
                         or the kanban root's), whatever the subject.
        cause_checks:    the chain HALT's cause checks
                         (:mod:`pgai_agent_kanban.overwatch.halt_clearing`).
        facts:           the classifier's facts (``{precondition: bool}`` and
                         ``{note_key: text}``), gathered read-only.
        transient_retry: ``(retry_count, retry_limit)`` from the task's
                         ``## Transient Retry`` record; None reads as (0, None).
        requeue_budget:  ``[overwatch] requeue_budget`` for the project.
        kanban_root:     used only to print runnable commands in an inbox
                         answer; blank prints the root variable instead.
        now:             the decision time (datetime or ISO text); the current
                         UTC time by default.
    """

    subject: str
    project: str
    task_id: Optional[str] = None
    record: Optional[BlockRecord] = None
    record_error: Optional[str] = None
    task_state: Optional[str] = None
    active_rc: str = NO_ACTIVE_RC
    history: TaskHistory = dataclasses.field(default_factory=TaskHistory)
    runaway_trips: tuple[Trip, ...] = ()
    halt: Optional[HaltProvenance] = None
    operator_halts: tuple[str, ...] = ()
    cause_checks: tuple[CauseCheck, ...] = ()
    facts: Mapping[str, Any] = dataclasses.field(default_factory=dict)
    transient_retry: Optional[tuple[int, Optional[int]]] = None
    requeue_budget: int = DEFAULT_REQUEUE_BUDGET
    kanban_root: str = ""
    now: "datetime.datetime | str | None" = None


# ---------------------------------------------------------------------------
# The outcome
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Outcome:
    """The one thing to do.

    Attributes:
        kind:          one of OUTCOMES.
        why:           a stable ``decide.*`` key naming the cause.
        detail:        the cause in plain language.
        name:          the verb, for ``verb``; None otherwise.
        args:          the verb's arguments (``project`` and ``task_id`` where
                       there is one); for ``runaway``, the counters.
        question:      the inbox question, for ``inbox``.
        source:        the inbox entry's source (a task ID or the project),
                       for ``inbox``.
        how_to_answer: what the human does, with runnable commands, for ``inbox``.
        evidence:      lines naming what the decision read.
        classifier:    the block classifier's verdict, when it was asked.
    """

    kind: str
    why: str
    detail: str
    name: Optional[str] = None
    args: Mapping[str, Any] = dataclasses.field(default_factory=dict)
    question: Optional[str] = None
    source: Optional[str] = None
    how_to_answer: Optional[str] = None
    evidence: tuple[str, ...] = ()
    classifier: Optional[bc.Decision] = None

    def as_json_dict(self) -> dict:
        """The outcome as a JSON-ready dict."""
        out = {f.name: getattr(self, f.name) for f in dataclasses.fields(self)}
        out["args"] = {k: list(v) if isinstance(v, tuple) else v for k, v in self.args.items()}
        out["evidence"] = list(self.evidence)
        out["classifier"] = self.classifier.as_json_dict() if self.classifier is not None else None
        return out


def _verb(name: str, args: Mapping[str, Any], why: str, detail: str, evidence: tuple, classifier=None) -> Outcome:
    return Outcome(
        kind=OUTCOME_VERB, why=why, detail=detail, name=name, args=dict(args),
        evidence=tuple(evidence), classifier=classifier,
    )


def _inbox(
    question: str, source: str, how_to_answer: str, why: str, detail: str, evidence: tuple, classifier=None
) -> Outcome:
    return Outcome(
        kind=OUTCOME_INBOX, why=why, detail=detail, question=question, source=source,
        how_to_answer=how_to_answer, evidence=tuple(evidence), classifier=classifier,
    )


def _none(why: str, detail: str, evidence: tuple = (), classifier=None) -> Outcome:
    return Outcome(kind=OUTCOME_NONE, why=why, detail=detail, evidence=tuple(evidence), classifier=classifier)


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def _root(inputs: DecisionInput) -> str:
    return shlex.quote(inputs.kanban_root.rstrip("/")) if inputs.kanban_root.strip() else _ROOT_FALLBACK


def _task_commands(inputs: DecisionInput) -> str:
    """The runnable return-to-BACKLOG and close commands for the task."""
    tool = f"bash {_root(inputs)}/scripts/task-status.sh"
    target = f"--project {shlex.quote(inputs.project)} --key {shlex.quote(inputs.task_id or '')}"
    return (
        f"then return the task to BACKLOG with `{tool} {target} --state BACKLOG --needs-human no`, "
        f"or, if it is no longer wanted, close it with `{tool} {target} --state WONT-DO --needs-human yes "
        f"--summary 'Closed after an operator inbox question'`"
    )


def _template_values(inputs: DecisionInput, record: Optional[BlockRecord]) -> dict:
    """Placeholder values for a row's question and answer (before the evidence)."""
    values = {key: value for key, value in inputs.facts.items() if isinstance(value, str)}
    values.update(
        task_id=inputs.task_id or "the task",
        project=inputs.project,
        reason_code=(record.reason_code if record is not None else "") or "unknown",
        owner_role=(record.owner_role if record is not None else "") or "unknown",
    )
    return values


def _record_evidence(inputs: DecisionInput, record: Optional[BlockRecord]) -> list:
    lines = [f"subject={inputs.subject}", f"project={inputs.project}"]
    if inputs.task_id:
        lines.append(f"task_id={inputs.task_id}")
    if inputs.task_state:
        lines.append(f"task_state={inputs.task_state}")
    if record is not None:
        lines.append(
            f"block_record: reason_code={record.reason_code}; owner_role={record.owner_role}; "
            f"attempts={record.attempts}; requeues={record.requeues}; evidence={record.evidence}"
        )
    return lines


def _fail_closed_inbox(inputs: DecisionInput, why: str, cause: str, evidence: list, classifier=None) -> Outcome:
    """The inbox outcome for anything that cannot be decided safely."""
    subject = inputs.task_id or inputs.project
    question = f"{subject} stopped and OVERWATCH cannot decide what resolves it ({cause}): what should happen?"
    answer = "read the task's status.md (## Blockers, ## Block Record) and resolve the cause; "
    answer += _task_commands(inputs) if inputs.task_id else "then resume the project"
    return _inbox(question, inputs.task_id or inputs.project, answer, why, cause, tuple(evidence), classifier)


# ---------------------------------------------------------------------------
# Block and Needs Human
# ---------------------------------------------------------------------------


def _row_decision(
    inputs: DecisionInput,
    rule: bc.Rule,
    record: BlockRecord,
    classifier: bc.Decision,
    cause: str,
    evidence: list,
    *,
    allow_verb: bool = True,
) -> Outcome:
    """The row's decision: its verb while the task's history allows, else its question."""
    decision = rule.decision
    if decision is None:
        return _fail_closed_inbox(
            inputs, WHY_FAIL_CLOSED, f"{rule.reason_code} has no decision in the classifier table ({cause})",
            evidence, classifier,
        )
    if allow_verb and decision.verb is not None:
        used = inputs.history.uses(decision.verb)
        if used < decision.budget:
            return _verb(
                decision.verb,
                {
                    "project": inputs.project,
                    "task_id": inputs.task_id,
                    "reason_code": rule.reason_code,
                    "use": used + 1,
                    "budget": decision.budget,
                },
                WHY_ROW_VERB,
                f"{cause}; {rule.reason_code} decides {decision.verb} (use {used + 1} of {decision.budget})",
                tuple(evidence) + (f"history: {decision.verb} used {used} of {decision.budget}",),
                classifier,
            )
        evidence = evidence + [f"history: {decision.verb} used {used} of {decision.budget} (budget spent)"]
    record_evidence = parse_evidence(record.evidence)
    values = _template_values(inputs, record)
    question = bc.render_note(decision.inbox, record_evidence, values)
    answer = f"{bc.render_note(decision.answer, record_evidence, values)}; {_task_commands(inputs)}"
    return _inbox(
        question, inputs.task_id or inputs.project, answer, WHY_ROW_INBOX,
        f"{cause}; {rule.reason_code} asks the operator", tuple(evidence), classifier,
    )


def _triage_verb(inputs: DecisionInput, record: BlockRecord, classifier: bc.Decision, evidence: list) -> Outcome:
    """The verb a classifier action is carried out as."""
    args: dict = {
        "project": inputs.project,
        "task_id": inputs.task_id,
        "reason_code": record.reason_code,
        "triage_action": classifier.action,
        "attempt": classifier.attempt,
    }
    args.update({key: value for key, value in classifier.params.items()})
    if classifier.action == RUN_ALLOWLISTED_VERB:
        typed = read_action_args(record)
        name = typed.pop(ACTION_VERB_KEY, "")
        args.update(typed)
    else:
        name = TRIAGE_ACTION_VERBS.get(classifier.action, "")
    if name not in VERBS:
        return _fail_closed_inbox(
            inputs, WHY_FAIL_CLOSED,
            f"the classifier chose {classifier.action}, which no operator verb carries out", evidence, classifier,
        )
    return _verb(name, args, WHY_TRIAGE_ACTION, classifier.detail, tuple(evidence), classifier)


def _decide_verification(inputs: DecisionInput, record: BlockRecord, evidence: list) -> Optional[Outcome]:
    """A TESTER task's own block while it verifies a release, or None.

    The RC's one requeue still available (and the RC still open) returns it
    to BACKLOG with its block cause; once spent, the task is closed so the
    release that waits on it ships recorded as unverified.
    """
    if record.reason_code != VERIFICATION_REASON_CODE or (record.owner_role or "").upper() != "TESTER":
        return None
    version = inputs.facts.get(FACT_VERIFICATION_VERSION)
    if not isinstance(version, str) or not version.strip():
        return None
    spent = inputs.facts.get(FACT_VERIFICATION_RETRY_SPENT) is True
    cause = " ".join(str(inputs.facts.get(FACT_VERIFICATION_CAUSE) or "no block cause recorded").split())
    args = {
        "project": inputs.project, "task_id": inputs.task_id, "reason_code": record.reason_code,
        "version": version, "cause": cause,
    }
    evidence = evidence + [f"verification: {version}; requeue {'spent' if spent else 'available'}; cause: {cause}"]
    if not spent:
        if version != inputs.active_rc:
            return None
        return _verb(
            VERB_RESET_TO_BACKLOG, {**args, "verification_retry": version, "use": 1, "budget": 1},
            WHY_VERIFICATION_REQUEUE,
            f"{inputs.task_id} blocked while verifying {version}: the RC's one requeue returns it to BACKLOG "
            f"with its block cause ({cause})",
            tuple(evidence),
        )
    return _verb(
        VERB_CLOSE_UNVERIFIED, args, WHY_VERIFICATION_UNVERIFIED,
        f"{inputs.task_id} blocked again while verifying {version} after the RC's one requeue: it is closed so "
        f"the release ships NON-FUNCTIONAL as unverified ({cause})",
        tuple(evidence),
    )


def _decide_task(inputs: DecisionInput, table: Optional[bc.ClassifierTable]) -> Outcome:
    record = inputs.record
    evidence = _record_evidence(inputs, record)
    if inputs.record_error:
        return _fail_closed_inbox(
            inputs, WHY_RECORD_UNREADABLE, f"its Block Record cannot be read: {inputs.record_error}", evidence
        )
    if record is None:
        cause = (
            "it asks for a human but carries no ## Block Record"
            if inputs.subject == SUBJECT_NEEDS_HUMAN
            else "it is blocked without a ## Block Record"
        )
        return _fail_closed_inbox(inputs, WHY_RECORD_ABSENT, cause, evidence)
    verification = _decide_verification(inputs, record, evidence)
    if verification is not None:
        return verification
    if table is None:
        try:
            table = bc.load_table()
        except bc.TableError as exc:
            return _fail_closed_inbox(inputs, WHY_TABLE_INVALID, str(exc), evidence)

    classifier = bc.decide(record, table, facts=inputs.facts, now=inputs.now)
    evidence.append(f"classifier: {classifier.action} ({classifier.why}): {classifier.detail}")
    rule = table.rules.get(record.reason_code)

    if record.reason_code == TRANSIENT_REASON_CODE and rule is not None and isinstance(rule.max_attempts, int):
        count, limit = inputs.transient_retry or (0, None)
        ceiling = limit if limit is not None else rule.max_attempts
        evidence.append(f"transient_retry: {count} of {ceiling}")
        if count < ceiling:
            return _none(
                WHY_TRANSIENT_HANDLER,
                f"transient retry {count} of {ceiling}: the transient-error handler still requeues it after its backoff",
                tuple(evidence), classifier,
            )
        return _row_decision(
            inputs, rule, record, classifier,
            f"the transient-error handler reached its retry ceiling ({count} of {ceiling})", evidence,
        )

    if classifier.action == bc.NOT_YET:
        return _none(WHY_NOT_YET, classifier.detail, tuple(evidence), classifier)

    if classifier.action == bc.ESCALATE:
        if rule is None or classifier.why not in (
            bc.WHY_ALWAYS_HUMAN, bc.WHY_ATTEMPTS_EXHAUSTED, bc.WHY_PRECONDITION_UNMET,
        ):
            return _fail_closed_inbox(inputs, WHY_FAIL_CLOSED, classifier.detail, evidence, classifier)
        return _row_decision(
            inputs, rule, record, classifier, classifier.detail, evidence,
            allow_verb=classifier.why != bc.WHY_PRECONDITION_UNMET,
        )

    if (
        rule is not None
        and classifier.action not in bc.REQUEUE_FREE_ACTIONS
        and classifier.action != DEFER_TO_RESOLUTION_STAGE
        and record.requeues >= inputs.requeue_budget
    ):
        evidence.append(f"requeue budget: {record.requeues} of {inputs.requeue_budget} spent")
        return _row_decision(
            inputs, rule, record, classifier,
            f"the requeue budget is spent ({record.requeues} of {inputs.requeue_budget}: "
            f"{record.requeue_reasons})",
            evidence,
        )
    return _triage_verb(inputs, record, classifier, evidence)


# ---------------------------------------------------------------------------
# HALT
# ---------------------------------------------------------------------------


def _decide_chain_halt(inputs: DecisionInput, halt: HaltProvenance) -> Outcome:
    evidence = [
        f"halt: {halt.path}; origin={halt.origin}; writer={halt.writer}; reason={halt.reason}; "
        f"trigger={halt.trigger}; source={halt.source}"
    ]
    evidence += [f"cause check {check.check}: {'passed' if check.passed else 'failed'}: {check.evidence}"
                 for check in inputs.cause_checks]
    if halt.writer in (WRITER_OVERWATCH_RUNAWAY, WRITER_OVERWATCH_REPAIR):
        return _none(
            WHY_HALT_OWN_WRITER,
            f"the {halt.writer} HALT at {halt.path} is cleared by its own writer (a runaway HALT waits for the "
            "operator's answer to its inbox entry)",
            tuple(evidence),
        )
    passed = [check for check in inputs.cause_checks if check.passed]
    args = {
        "project": inputs.project,
        "halt": halt.path,
        "writer": halt.writer,
        "reason": halt.reason,
        "trigger": halt.trigger,
    }
    if passed:
        args["checks"] = tuple(check.check for check in passed)
        return _verb(
            VERB_CLEAR_CHAIN_HALT, args, WHY_HALT_CAUSE_RESOLVED,
            f"the chain HALT's cause is resolved ({'; '.join(c.check + ': ' + c.evidence for c in passed)})",
            tuple(evidence),
        )
    return _verb(
        VERB_RESOLUTION_STAGE, args, WHY_HALT_TO_STAGE,
        f"the {halt.writer} chain HALT goes to the resolution stage, which keys on its writer and trigger",
        tuple(evidence),
    )


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------


def _runaway(inputs: DecisionInput) -> Outcome:
    trips = inputs.runaway_trips
    evidence = []
    for trip in trips:
        evidence.append(trip.reason)
        evidence += [f"{trip.counter} event: {event.at} {event.key} {event.detail}".rstrip() for event in trip.events]
    return Outcome(
        kind=OUTCOME_RUNAWAY,
        why=WHY_RUNAWAY,
        detail="; ".join(trip.reason for trip in trips),
        args={"project": inputs.project, "counters": tuple(trip.counter for trip in trips)},
        evidence=tuple(evidence),
    )


def inbox_after_verb(
    inputs: DecisionInput, verb: str, cause: str, *, table: Optional[bc.ClassifierTable] = None
) -> Outcome:
    """The inbox question to park when *verb*, decided for *inputs*, could not be carried out.

    The executor calls this when a verb's handler refuses at execution time,
    the resolution stage declines, or no operator write carries the verb, so
    the block lands in the operator inbox rather than as Needs Human. A task
    whose Block Record's classifier row has a decision asks the row's own
    question (never its verb again); anything else asks the fail-closed
    question. Each names *cause*. Pure, as :func:`decide` is: *table* when
    given, else the shipped table, read only.
    """
    cause = f"{verb} could not be carried out: {' '.join(str(cause).split()) or 'no reason recorded'}"
    record = inputs.record
    evidence = _record_evidence(inputs, record) + [cause]
    rule = None
    if record is not None and inputs.subject in (SUBJECT_BLOCK, SUBJECT_NEEDS_HUMAN):
        try:
            rule = (table if table is not None else bc.load_table()).rules.get(record.reason_code)
        except bc.TableError:
            rule = None
    if rule is None or rule.decision is None or record is None:
        return dataclasses.replace(
            _fail_closed_inbox(inputs, WHY_VERB_NOT_CARRIED_OUT, cause, evidence), why=WHY_VERB_NOT_CARRIED_OUT
        )
    record_evidence = parse_evidence(record.evidence)
    values = _template_values(inputs, record)
    question = bc.render_note(rule.decision.inbox, record_evidence, values)
    answer = f"{bc.render_note(rule.decision.answer, record_evidence, values)}; {_task_commands(inputs)}"
    return _inbox(question, inputs.task_id or inputs.project, answer, WHY_VERB_NOT_CARRIED_OUT, cause, tuple(evidence))


def decide(inputs: DecisionInput, *, table: Optional[bc.ClassifierTable] = None) -> Outcome:
    """Decide what OVERWATCH does about *inputs* (see the module docstring).

    Pure: reads nothing but *inputs* and the classifier table — *table* when
    given, else the shipped ``block-classifier.yaml`` (read only; a table
    that does not load decides inbox). Writes nothing and starts nothing.

    Raises:
        ValueError: ``inputs.now`` is text that is not an ISO-8601 time.
    """
    if inputs.subject not in SUBJECTS:
        return _fail_closed_inbox(
            inputs, WHY_SUBJECT_UNKNOWN,
            f"subject {inputs.subject!r} is not one of {', '.join(SUBJECTS)}", [f"subject={inputs.subject}"],
        )
    if inputs.subject == SUBJECT_HALT:
        halt = inputs.halt
        if halt is None or halt.origin not in (ORIGIN_CHAIN, ORIGIN_OPERATOR):
            return _none(WHY_NO_HALT, "the project has no HALT to decide about")
        if halt.origin != ORIGIN_CHAIN or halt.fleet_scope or inputs.operator_halts:
            paths = ", ".join(inputs.operator_halts) or halt.path
            return _none(
                WHY_OPERATOR_HALT,
                f"an operator HALT is present ({paths}): OVERWATCH never touches it, nor a chain HALT beside it",
                (f"halt: {halt.path}; origin={halt.origin}; source={halt.source}",),
            )
    if inputs.runaway_trips:
        return _runaway(inputs)
    if inputs.subject == SUBJECT_PROJECT:
        return _none(WHY_NOTHING_STOPPED, "no runaway counter trips and the project is decided about for nothing else")
    if inputs.subject == SUBJECT_HALT:
        return _decide_chain_halt(inputs, inputs.halt)
    return _decide_task(inputs, table)
