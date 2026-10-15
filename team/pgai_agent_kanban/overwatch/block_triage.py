"""
block_triage.py — OVERWATCH's block-triage executor: turn the classifier's
decision about a blocked task into the writes OVERWATCH may make.

The sweep check ``team/scripts/lib/overwatch-checks/check-block-triage.sh``
and the overwatch wake (which runs that check before it takes the project's
repo lock) are the two callers; both reach this module through its CLI.

Division of labour
------------------
The bash check owns the safety envelope around every write this module
makes: it runs each task under ``overwatch_halt_first_fix`` (HALT_OVERWATCH
and the per-project repo lock are refused there, leaving the task
unchanged), backs up ``status.md`` and the queue file with
``overwatch_backup_file`` before calling ``apply``, performs the one state
mutation ``apply`` asks for through ``promote_blocked_to_backlog`` or
``transition_task_state``, and logs ``block-triage:<code>:<action>`` with
``overwatch_log_action``.

This module decides and writes the rest, in this order:

1. ``plan`` (read only) — for every ``[B]`` queue line whose task reads
   ``## State`` BLOCKED and carries a ``## Block Record``, whatever its
   ``## Needs Human`` value, classify the record
   (:mod:`pgai_agent_kanban.overwatch.block_classifier`) and say what
   ``apply`` would do.
2. ``apply`` — re-plan the one task (under the lock the check holds, so the
   plan cannot go stale), write an evidence file under the task's
   ``artifacts/block-evidence/`` FIRST, then make the outcome's own writes,
   then print the state mutation (if any) the check must perform.

Outcomes
--------
``skip``
    Nothing is written: the task is no longer BLOCKED or carries no record;
    its block epoch is already escalated (``Needs Human: yes`` and an
    ``## OVERWATCH Diagnosis`` naming the record's ``first_blocked_at``);
    it is inside its cool-down window; ``[overwatch] block_triage`` is
    false and the decision is an action; or the decision is an action this
    executor does not perform yet on a task whose ``Needs Human`` is
    ``no`` — that block is left to the handler that recorded it (the wake's
    transient-error auto-retry), never escalated over its head.
``record``
    A first sighting of a row that waits one cool-down window before its
    first action: ``cooldown_until`` is recorded in the Block Record.
``escalate``
    The block stays with a human: ``## Needs Human`` becomes ``yes`` and a
    one-paragraph ``## OVERWATCH Diagnosis`` names the block epoch, the
    cause, the evidence pointers and concrete options. Also the outcome for
    an action this executor does not perform yet (fail closed), and for a
    requeue the task's requeue budget no longer allows (see "Requeue
    budget" below). Escalation
    is not a triage attempt: ``attempts`` and ``cooldown_until`` keep their
    values. Written whatever ``block_triage`` says — records and diagnoses
    are always written.
``act``
    An action with a registered handler (``ACTION_HANDLERS``): the handler
    runs its own preparatory step and names the state mutation; a
    ``## Triage Note`` line is appended to the task README (the next
    dispatch reads it); the Block Record's ``attempts`` goes up by one and
    ``cooldown_until`` (and ``resume``, when the row names one) is set, and
    an action that returns the task to its owner (mutation ``promote``) is
    recorded as a requeue (``requeues`` plus one, its reason_code appended
    to ``requeue_reasons``); a
    ``pending`` entry is appended to the verification ledger (already
    settled when the action proves its own verification); the check then
    performs the mutation. A handler that refuses at execution time
    escalates instead.
``defer``
    ``defer_to_resolution_stage`` (``ship_policy_halt``): the block is
    handed to the existing resolution stage, which decides under its own
    rules and ceiling. Nothing is written by this module — no evidence
    file, Block Record, Triage Note, ledger entry or diagnosis — and no
    state mutation is asked for; the check writes only its action-log row.
    See "Ship-policy deferral" below.

Requeue classes
---------------
``requeue_owner`` is performed for three reason codes (handlers are looked
up as ``<action>:<reason_code>`` first, then ``<action>``):

``per_task_timeout``
    The commits the feature branch holds ahead of the source branch are
    snapshotted to ``artifacts/block-evidence/commits-ahead-<UTC>.txt``
    (read-only git; the branch is left for the owner), then the owner is
    requeued with ``resume: branch`` and the note "previous attempt was
    killed at Ns". A snapshot that cannot be taken escalates. Once (max 1).
``provider_refusal``
    After the row's first cool-down, the owner is requeued and the
    ``## Zero Progress`` count is reset to 0, so the requeued attempt starts
    below the zero-progress ceiling; the window grows 15 -> 30 -> 60 min
    between attempts, at most 3. Exhausted, the diagnosis names the
    fleet-level cause (the provider's authentication, the configured model
    or a usage limit).
``zero_progress``
    The owner is requeued with a note. Once.

Gate and worktree classes
-------------------------
Four git-facing reason codes are acted on with facts read from the
repository at decision time (read-only git; :mod:`block_gate` and
:mod:`worktree_residue`), each at most once per block epoch:

``gate_merge_not_found``
    (a) The feature branch is already an ancestor of its source branch:
    ``recompute_gate`` runs the completion gate's checks again, read-only
    (no ref moves, no reflog grows), requires the ``--no-ff`` merge commit
    that brings the branch in, sets ``## Needs Human`` no and ``## Blockers``
    none, and transitions the task to DONE; its verification is settled at
    once. (b) Otherwise, when ``merge-tree --write-tree`` finds no conflict
    and the owner is CODER or WRITER, the owner is requeued with ``resume:
    branch`` and the note "merge only, then re-complete". A conflict (the
    diagnosis names the paths) or a second refusal stays with a human.
``gate_dirty_worktree``
    The worktree's uncommitted changes are saved to
    ``artifacts/block-evidence/dirty.patch`` (with ``untracked.txt`` and the
    patch's ``dirty.patch.sha256``) and never committed; the owner is
    requeued with ``resume: branch``, the Triage Note naming the patch and
    its hash. A worktree that is gone, or no longer dirty, escalates.
``gate_empty_commit``
    The owner is requeued with ``resume: branch`` and a note quoting the
    empty commits' shas; with no sha to quote it escalates.
``worktree_create_failed``
    A registered worktree that claims the task (its path or its feature
    branch) and sits inside the worktree base (``--worktree-base``) is
    removed (``git worktree remove``, never ``--force``) and pruned, after
    its list is written to the evidence directory, and the task is
    requeued so the next dispatch retries the create. Residue whose HEAD
    reaches a commit no other ref holds, residue outside the base or locked,
    a live residue holding changes, or a missing source branch escalates and
    nothing is removed.

Verified on the next block by ``gate_passes`` (failed on another gate
refusal) and ``next_create_succeeds`` (failed on another failed create).

Operator data actions
---------------------
``agent_operator_data_action`` is acted on with ``run_allowlisted_verb``
only for a verb on the allowlist
(:mod:`pgai_agent_kanban.overwatch.allowlisted_verbs`) whose typed
arguments validate and whose targets check out — for the first entry,
``restamp-fixed-in``, an existing project, a ``vX.Y.Z`` version and
``BUG-NNNN`` bugs that each exist and are done. Every Block Record value is
untrusted: the arguments are re-validated at planning time (the
``action_targets_verified`` precondition) and again immediately before the
run. The verb runs once, in-process with its typed arguments (no shell);
its output and an immediate re-run's output are written to
``artifacts/block-evidence/verb-<verb>-<UTC>.txt``; the re-run must report
0 changes, or the block escalates. Then the owner is requeued with a note
naming both results. A record naming no verb (a prose-only remedy), a verb
not on the allowlist, an invalid argument, an unknown project, or a bug
that is missing or not done escalates, and the verb is never run. Verified
on the next block by ``rerun_reports_no_changes`` (failed when the task
asks for an operator data action again). Once per block epoch.

Ship-policy deferral
--------------------
``ship_policy_halt`` is deferred to the existing resolution stage
(:mod:`pgai_agent_kanban.overwatch.block_stage_deferral` calls the stage's
``run_resolution_stage``) — no new authority, and the stage's own ceiling.
Planning requires the project's ``HALT`` to be a CM ship-policy HALT;
otherwise the block is skipped and stays with a human. Applying runs the
stage once and reports its outcome: verified when the stage executed and
appended an ``## OVERWATCH Decision`` event to release-state.md; a decline
leaves the block with a human. Every write that follows is the stage's own;
this module writes nothing for the class and asks for no state mutation.

``transient_api_error`` is the transient-error handlers' own (the wake's
auto-retry and the OVERWATCH transient check, both behind the backoff in
:mod:`pgai_agent_kanban.overwatch.transient_backoff`): below the
``## Transient Retry`` ceiling the block is left to them (``skip``); at the
ceiling it escalates. The executor never requeues it itself.

Verification and block epochs
-----------------------------
A block epoch is keyed on the Block Record's ``first_blocked_at``; a
re-block keeps it. When a task the executor requeued blocks again (its
``last_blocked_at`` differs from the block acted on), the newest
``pending`` ledger entry for the epoch is verified against the new block
(:func:`pgai_agent_kanban.overwatch.block_requeue.verify`):

* passed — the action worked and the new block is a new incident: the
  epoch restarts at the new block (``first_blocked_at`` = ``last_blocked_at``,
  ``attempts`` 0, ``cooldown_until`` none) and the new block is classified
  on its own;
* failed — the same failure came back. When it is the same reason_code
  and the row still allows an attempt (a provider that keeps refusing is
  what provider_refusal's backoff is for), the row decides as usual;
  otherwise the block escalates, naming the failed verification;
* unverified (no verification for the row) — the row decides as usual.

A task still BLOCKED on the very block occurrence an action was taken on
(the requeue never landed) escalates: the same block is never acted on
twice, and an escalated epoch is never acted on again.

Requeue budget
--------------
A verifier passes on any block other than the one its action addressed, so
blocks that alternate between reasons (``per_task_timeout`` then
``gate_dirty_worktree``, or ``zero_progress`` then ``per_task_timeout``)
would each restart the epoch and be requeued forever. The Block Record's
``requeues`` counts every requeue over the life of the record — it
survives re-blocks and epoch restarts and goes only when the task reaches
DONE or WONT-DO — and ``[overwatch] requeue_budget`` (project.cfg first,
then kanban.cfg; default 3; resolved by
:func:`pgai_agent_kanban.overwatch.block_requeue.resolve_requeue_budget`)
caps it. When the row would act and ``requeues`` has reached the budget,
the block escalates instead; the cause names the reason codes of every
requeue so far and of the block that found the budget spent. Each row's
``max_attempts`` still bounds one epoch on its own; the default budget is
no lower than any row's ``max_attempts``, so a task blocked again and again
for one reason reaches its row's own ceiling exactly as before. ``recompute_gate``
closes a task rather than requeueing it, so it is never refused by the
budget and never counted.

Swim lane
---------
This module never commits, merges, checks out, pushes or deletes a branch,
and never resets a task: it reads files, writes the task's status.md,
README.md and evidence files, runs an allowlisted verb with validated
typed arguments, hands a ship-policy block to the resolution stage, and
prints. Every git call goes through
:mod:`pgai_agent_kanban.overwatch.readonly_git`, which refuses anything but
read-only subcommands and its two worktree-registration cleanup verbs (used
only on stale worktree residue whose commits all exist elsewhere). Any git
work a fix needs is the owning role's, reached by requeueing the task with a
note.

Usage (CLI):
    python3 -m pgai_agent_kanban.overwatch.block_triage plan \\
        --kanban-root DIR --project NAME [--id-pattern REGEX] \\
        [--triage-enabled true|false] [--now ISO] [--table YAML_FILE] \\
        [--worktree-base DIR]
    python3 -m pgai_agent_kanban.overwatch.block_triage apply \\
        --kanban-root DIR --project NAME --task-id ID --queue-file FILE \\
        [--triage-enabled true|false] [--status-backup PATH] \\
        [--queue-backup PATH] [--now ISO] [--table YAML_FILE] \\
        [--worktree-base DIR] [--defer-only]

``plan`` prints one tab-separated line per candidate task::

    TASK_ID  QUEUE_FILE  OUTCOME  REASON_CODE  ACTION  DETAIL

``apply`` prints one tab-separated line::

    OUTCOME  REASON_CODE  ACTION  MUTATION  DETAIL

where OUTCOME is ``skip``, ``record``, ``escalate``, ``act`` or ``defer``
and MUTATION is ``none``, ``promote`` or ``transition:<STATE>``. For
``promote`` and ``transition`` DETAIL is the cleared-reason text the check
passes to the mutation; for ``defer`` it is the resolution stage's outcome.
``--defer-only`` is for a task the plan named ``defer`` (the check takes no
backup for it, since nothing of the task is written): when the plan under
the lock is anything else, ``apply`` writes nothing and reports ``skip``.
Both commands read ``[overwatch] requeue_budget`` first; a value that is not
a whole number refuses the call before anything is planned.
Exit codes: 0 success; 1 refused (dual-channel error printed, nothing
written); 2 usage error.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import pathlib
import re
import shlex
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

# Run by file path (``python3 .../block_triage.py``) or loaded from a file
# outside its package, this module has no parent package and the interpreter
# puts only its own directory on sys.path, so the package imports below would
# not resolve. Put this file's own package root -- the directory holding the
# pgai_agent_kanban/ this file sits in -- at the front of sys.path so the
# imports resolve from the same tree, never another. Imported as a package
# member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.lib.atomic_write import atomic_write  # noqa: E402
from pgai_agent_kanban.lib.error_contract import emit_error_contract  # noqa: E402
from pgai_agent_kanban.lib.timeutil import format_iso, try_parse_iso  # noqa: E402
from pgai_agent_kanban.overwatch.allowlisted_verbs import (  # noqa: E402
    VerbRefused,
    VerbRun,
    run_allowlisted_verb,
    target_problems,
    validated_args,
)
from pgai_agent_kanban.overwatch.block_classifier import (  # noqa: E402
    ESCALATE,
    NOT_YET,
    WHY_ALWAYS_HUMAN,
    WHY_ATTEMPTS_EXHAUSTED,
    WHY_PRECONDITION_UNMET,
    Decision,
    classify,
)
from pgai_agent_kanban.overwatch.block_gate import (  # noqa: E402
    GateTriageError,
    empty_commit_shas,
    gate_empty_commit_facts,
    gate_merge_facts,
    recompute_gate_for_task,
    snapshot_dirty_worktree,
)
from pgai_agent_kanban.overwatch.block_record import (  # noqa: E402
    NONE_VALUE,
    SECTION_HEADING as RECORD_HEADING,
    BlockRecord,
    BlockRecordError,
    normalize_reason_code,
    parse_evidence,
    parse_fields,
    read_block_record,
    update_block_record,
)
from pgai_agent_kanban.overwatch.block_requeue import (  # noqa: E402
    REQUEUE_BUDGET_KEY,
    REQUEUE_BUDGET_SECTION,
    STATUS_FAILED,
    STATUS_PASSED,
    PendingCheck,
    RequeueBudgetError,
    SnapshotError,
    Verdict,
    append_pending,
    latest_pending,
    read_ledger,
    resolve_requeue_budget,
    settle_entry,
    snapshot_commits_ahead,
    timeout_seconds,
    transient_retry_state,
    verify,
    zero_progress_count,
    zero_progress_reset_text,
)
from pgai_agent_kanban.overwatch.block_stage_deferral import (  # noqa: E402
    StageRunner,
    defer_to_stage,
    ship_policy_halt_state,
)
from pgai_agent_kanban.overwatch.worktree_residue import ResidueError, clear_residue, residue_facts  # noqa: E402
from pgai_agent_kanban.task_status import (  # noqa: E402
    TaskStatusError,
    read_section,
    section_value,
    set_or_append_section,
)

# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------

OUTCOME_SKIP = "skip"
OUTCOME_RECORD = "record"
OUTCOME_ESCALATE = "escalate"
OUTCOME_ACT = "act"
OUTCOME_DEFER = "defer"

#: The action that hands a block to the existing resolution stage.
DEFER_ACTION = "defer_to_resolution_stage"

#: The state mutations a handler may ask the check to perform.
MUTATION_NONE = "none"
MUTATION_PROMOTE = "promote"
MUTATION_TRANSITION_PREFIX = "transition:"

#: The status.md section an escalation writes.
DIAGNOSIS_HEADING = "OVERWATCH Diagnosis"

#: The README section an action appends to.
TRIAGE_NOTE_HEADING = "Triage Note"

#: Where evidence files are written, relative to the task folder.
EVIDENCE_DIR = Path("artifacts") / "block-evidence"

#: The reason_code token used when a record names none.
NO_CODE = "none"

#: A key may name a task folder only when it matches this shape (no path
#: separators, no leading dot). The caller's ``--id-pattern`` narrows it.
_SAFE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

#: A queue line: optional "- ", a bracketed marker, the key.
_QUEUE_LINE_RE = re.compile(r"^\s*-?\s*\[\s*([^\]\n]*?)\s*\]\s+(\S+)")

#: Reads the epoch an existing diagnosis names (liberal spacing and case).
_DIAGNOSIS_EPOCH_RE = re.compile(r"block\s+epoch\s+([^\s()]+)", re.IGNORECASE)

_ATOMIC_PREFIX = ".block_triage_tmp_"
_MODULE_FILE = str(pathlib.Path(__file__).resolve())

# Why, and what to do, for each reason_code that always stays with a human.
# The option text names where to look; the diagnosis always adds the
# runnable return-to-BACKLOG and close commands after it.
_ALWAYS_HUMAN_GUIDANCE: dict[str, tuple[str, str]] = {
    "missing_readme": (
        "the task folder has no README.md, so no agent can be dispatched on it",
        "restore README.md in the task folder from the decomposition that created it",
    ),
    "invalid_assignment": (
        "the task README assigns a role or owner the wake cannot dispatch",
        "correct ## Role (and ## Owner) in the task README to a dispatchable role such as CODER",
    ),
    "invalid_effort_config": (
        "the effort configuration the task resolves to is invalid",
        "correct the task's effort setting in its README, or the role's effort keys in kanban.cfg",
    ),
    "invalid_state": (
        "status.md carries a ## State outside the six canonical states",
        "rewrite ## State with the status-writer tool (the BACKLOG command below does this)",
    ),
    "branch_prefix_failed": (
        "the project's branch prefix could not be resolved",
        "check branch_prefix in the project's project.cfg",
    ),
    "workflow_type_unparseable": (
        "the task README's ## Workflow Type cannot be parsed",
        "set ## Workflow Type in the task README to release, feature, document or an installed workflow plugin",
    ),
    "plugin_load_failed": (
        "the workflow-type plugin the task names failed to load",
        "check that the workflow plugin named in the task README's ## Workflow Type is installed under workflows/ "
        "with a readable workflow.cfg",
    ),
    "source_branch_missing": (
        "the source branch the task README names does not exist in the dev tree",
        "create the branch named in ## Source Branch (an RC branch is opened by CM) or correct the field",
    ),
    "gate_merge_evidence_not_found": (
        "the completion gate could not find evidence that the feature branch was merged",
        "check the source branch history in the dev tree for the task's merge, then mark the task DONE "
        "or return it to its owner",
    ),
    "agent_unclassified": (
        "the agent blocked its own task without naming a reason code; ## Blockers holds its explanation",
        "read ## Blockers in status.md and supply what the agent asked for",
    ),
}

_DEFAULT_OPTION = "read ## Blockers and the evidence named above, and resolve the cause"

#: The reason codes whose requeue_owner action this executor performs.
REQUEUE_CLASSES: tuple[str, ...] = ("per_task_timeout", "provider_refusal", "zero_progress")

#: The reason code whose requeue belongs to the transient-error handlers.
TRANSIENT_CODE = "transient_api_error"

#: The reason code whose exhausted diagnosis names a fleet-level cause.
PROVIDER_REFUSAL = "provider_refusal"

# What to do about a requeue class that ends with a human, the first option
# its diagnosis names.
_CLASS_OPTIONS: dict[str, str] = {
    "per_task_timeout": (
        "split the task into smaller tickets or raise [wake] max_task_seconds; the feature branch and the "
        "commits-ahead snapshot under artifacts/block-evidence/ keep the work done so far, and the Block Record's "
        "resume: branch keeps that branch on the next dispatch"
    ),
    "provider_refusal": (
        "check the active provider's login (authentication), the model it is configured to use and the "
        "account's usage limit; these are fleet-wide and refuse every task until fixed"
    ),
    "zero_progress": (
        "read the task README, its ## Triage Note and the batch-log span named in the evidence to see why the agent "
        "exits without moving the task, then clarify the README or split the task"
    ),
    "transient_api_error": (
        "check the provider's status for a sustained outage and the ## Transient Retry record's last_error; "
        "return the task once the provider is healthy"
    ),
    "gate_merge_not_found": (
        "merge the feature branch into the source branch in the dev tree, resolving the conflicting paths named "
        "above, then mark the task DONE; or return the task to its owner with a note to merge"
    ),
    "gate_dirty_worktree": (
        "inspect the retained task worktree and any dirty.patch / untracked.txt under artifacts/block-evidence/, "
        "commit or discard what belongs to the task, then re-complete it"
    ),
    "gate_empty_commit": (
        "drop the empty commit(s) quoted in ## Blockers and the Triage Note from the feature branch, re-merge it "
        "--no-ff into the source branch, then re-complete the task"
    ),
    "worktree_create_failed": (
        "run 'git worktree list' in the dev tree; put any commits the stale worktree holds on a branch, remove "
        "that worktree and run 'git worktree prune', or open the missing source branch, then return the task"
    ),
    "agent_operator_data_action": (
        "run the operator data action the agent asked for by hand once its arguments and targets are right "
        "(for a restamp, see 'team/scripts/restamp-fixed-in.sh --help'), or correct the typed action arguments "
        "in the Block Record evidence, then return the task"
    ),
    "ship_policy_halt": (
        "read the resolution stage's decision in the project's overwatch/actions.log and the ## OVERWATCH "
        "Decision events in release-state.md, resolve the ship-policy HALT as roles/CM.md describes, then "
        "return the task"
    ),
}

#: Facts a precondition gatherer may add to explain an unmet precondition in
#: a diagnosis, and how each is introduced.
_FACT_CONTEXT: tuple[tuple[str, str], ...] = (
    ("conflicts", "merge-tree reports conflicts in"),
    ("refusal", "worktree residue"),
    ("action_refusal", "operator data action refused"),
)

#: The reason code whose action is an allowlisted operator data verb.
OPERATOR_DATA_ACTION = "agent_operator_data_action"

#: Actions that close a task rather than return it to its owner: the
#: requeue budget never refuses them and never counts them.
REQUEUE_FREE_ACTIONS: tuple[str, ...] = ("recompute_gate",)

# The first option a diagnosis names when the requeue budget is spent.
_BUDGET_OPTION = (
    "read the task README's ## Triage Note lines and the evidence of each requeue to find the cause the "
    "successive blocks share (a task too large for the per-task timeout is often killed with uncommitted "
    "changes, and then refused by the completion gate), then split the task into smaller tickets or fix that "
    "cause; the Block Record keeps its requeue count until the task reaches DONE or WONT-DO, so a later block "
    "of this task goes straight to a human"
)


class TriageError(TaskStatusError):
    """A refused triage call, carrying the dual-channel fields
    (``.error_key`` / ``.message`` / ``.remedy``); the CLI is the one place
    that prints them."""


class HandlerRefused(Exception):
    """Raised by an action handler that finds, at execution time, that it
    must not act. The task escalates with the exception's text as its cause."""


# ---------------------------------------------------------------------------
# Action handlers (the extension point for the action classes)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class TriageContext:
    """What an action handler is given.

    Fields:
        task_id      the task key.
        task_dir     the task folder.
        status_file  the task's status.md.
        queue_file   the queue file carrying the task's ``[B]`` line.
        record       the parsed Block Record.
        decision     the classifier's decision (``decision.params`` carries
                     the row's rendered note, resume, snapshot, ...).
        evidence_dir the task's ``artifacts/block-evidence/`` (exists).
        now          the decision time.
        worktree_base the directory every task worktree lives under, or
                     None when the caller did not give it.
    """

    task_id: str
    task_dir: Path
    status_file: Path
    queue_file: Path
    record: BlockRecord
    decision: Decision
    evidence_dir: Path
    now: datetime.datetime
    worktree_base: Optional[Path] = None


@dataclasses.dataclass(frozen=True)
class HandlerResult:
    """What a handler returns.

    Fields:
        mutation  ``promote`` (BLOCKED -> BACKLOG through
                  ``promote_blocked_to_backlog``) or ``transition:<STATE>``
                  (through ``transition_task_state``).
        note      the Triage Note text; the row's rendered note by default.
        evidence  extra evidence pointers (paths written by the handler),
                  named in the Triage Note and the log detail.
        verdict   the action's verification when the action itself proves
                  it (a recomputed gate that passes); the ledger entry is
                  then written already settled instead of pending.
    """

    mutation: str = MUTATION_PROMOTE
    note: Optional[str] = None
    evidence: tuple[str, ...] = ()
    verdict: Optional[Verdict] = None


ActionHandler = Callable[[TriageContext], HandlerResult]


def _requeue_owner(ctx: TriageContext) -> HandlerResult:
    """``requeue_owner`` for the requeue classes: take the row's snapshots,
    reset the Zero Progress count when the row says so, then ask for the
    promotion back to BACKLOG.

    Refuses (the task escalates instead) when a snapshot the row names
    cannot be taken: a requeue onto a branch whose commits were not recorded
    first is not made.
    """
    params = ctx.decision.params
    note = str(params.get("note", "")) or ctx.decision.detail
    evidence: list[str] = []
    for name in params.get("snapshot", ()):
        if name == "dirty_patch":
            note += _snapshot_dirty(ctx, evidence)
            continue
        if name != "commits_ahead":
            raise HandlerRefused(f"the {name} snapshot the row names is not taken by this executor")
        try:
            snap = snapshot_commits_ahead(ctx.task_dir, ctx.evidence_dir, ctx.record, now=ctx.now)
        except SnapshotError as exc:
            raise HandlerRefused(f"the commits ahead could not be snapshotted before the requeue: {exc}") from exc
        rel = _relative_to_task(snap.path, ctx.task_dir)
        evidence.append(rel)
        if snap.branch_present:
            note += (
                f". Branch {snap.branch} holds {snap.ahead} commit(s) ahead of {snap.source} (tip {snap.tip[:12]}); "
                "they are kept for the resume"
            )
        else:
            note += f". Branch {snap.branch} no longer exists, so the next attempt starts from {snap.source}"
    if params.get("reset_zero_progress"):
        text = ctx.status_file.read_text(encoding="utf-8")
        before = zero_progress_count(text)
        reset = zero_progress_reset_text(text, "reset by OVERWATCH block triage before a provider-refusal requeue")
        if reset != text:
            _write_keeping_mode(ctx.status_file, reset)
            note += f". Zero Progress count reset from {before} to 0"
    return HandlerResult(mutation=MUTATION_PROMOTE, note=note, evidence=tuple(evidence))


def _snapshot_dirty(ctx: TriageContext, evidence: list) -> str:
    """Save the worktree's uncommitted changes (never committing them);
    append the written paths to *evidence* and return the note text naming
    the patch and its hash."""
    try:
        snap = snapshot_dirty_worktree(ctx.task_dir, ctx.evidence_dir, ctx.record, now=ctx.now)
    except (GateTriageError, OSError) as exc:
        raise HandlerRefused(f"the uncommitted changes could not be saved before the requeue: {exc}") from exc
    paths = [_relative_to_task(p, ctx.task_dir) for p in (snap.patch, snap.untracked, snap.checksum)]
    evidence.extend(paths)
    skipped = f", {snap.untracked_skipped} too large to save (listed only)" if snap.untracked_skipped else ""
    return (
        f". Patch {paths[0]} (sha256 {snap.sha256}) holds the tracked changes"
        f"{'' if snap.tracked_changes else ' (none)'} and {snap.untracked_saved} untracked file(s){skipped}; "
        f"apply it on the branch with 'git apply {snap.patch.name}'"
    )


def _requeue_empty_commit(ctx: TriageContext) -> HandlerResult:
    """``requeue_owner`` for ``gate_empty_commit``: only with the shas to quote."""
    shas = empty_commit_shas(ctx.task_dir, ctx.record, ctx.status_file.read_text(encoding="utf-8"))
    if not shas:
        raise HandlerRefused("neither the repository nor ## Blockers names the empty commit(s) the gate refused")
    return _requeue_owner(ctx)


def _recompute_gate(ctx: TriageContext) -> HandlerResult:
    """``recompute_gate``: the branch is already an ancestor of its source
    branch. Run the completion gate again, read-only; when it passes, clear
    the refusal (``## Needs Human`` no, ``## Blockers`` none) and ask for
    the transition to DONE."""
    try:
        account = recompute_gate_for_task(ctx.task_dir, ctx.record)
    except GateTriageError as exc:
        raise HandlerRefused(str(exc)) from exc
    text = ctx.status_file.read_text(encoding="utf-8")
    text = set_or_append_section(text, "Needs Human", "no")
    text = set_or_append_section(text, "Blockers", "none")
    _write_keeping_mode(ctx.status_file, text)
    return HandlerResult(
        mutation=f"{MUTATION_TRANSITION_PREFIX}DONE",
        note=f"the branch is already merged; {account}",
        verdict=Verdict(ctx.decision.verification, STATUS_PASSED, f"the recomputed gate passed: {account}"),
    )


def _prune_worktree(ctx: TriageContext) -> HandlerResult:
    """``prune_worktree_then_requeue``: clear the stale worktree that made the
    create fail (no branch or commit is deleted), then requeue so the next
    dispatch retries the create."""
    try:
        cleared = clear_residue(ctx.task_dir, ctx.evidence_dir, ctx.record, ctx.worktree_base, now=ctx.now)
    except (ResidueError, OSError) as exc:
        raise HandlerRefused(f"the stale worktree was not cleared: {exc}") from exc
    note = str(ctx.decision.params.get("note", "")) or ctx.decision.detail
    removed = f"removed {', '.join(cleared.removed)}; " if cleared.removed else ""
    pruned = f"pruned the registration(s) of {', '.join(cleared.pruned)}" if cleared.pruned else "ran git worktree prune"
    return HandlerResult(
        mutation=MUTATION_PROMOTE,
        note=f"{note} ({removed}{pruned}; no branch or commit was deleted)",
        evidence=(_relative_to_task(cleared.evidence, ctx.task_dir),),
    )


def _kanban_root_of(task_dir: Path) -> Path:
    """The kanban root holding ``projects/<name>/tasks/<task_id>/``."""
    return task_dir.parent.parent.parent.parent


def _write_verb_evidence(ctx: TriageContext, runs: "tuple[VerbRun, ...]") -> Path:
    """Write the verb's run(s) to ``verb-<verb>-<UTC>.txt`` (never overwriting)."""
    base = f"verb-{runs[0].verb}-{_compact_stamp(ctx.now)}"
    target = ctx.evidence_dir / f"{base}.txt"
    n = 2
    while target.exists():
        target = ctx.evidence_dir / f"{base}-{n}.txt"
        n += 1
    labels = ("run", "idempotence re-run")
    text = "\n".join(f"# {labels[min(i, 1)]}\n{run.as_text()}" for i, run in enumerate(runs))
    atomic_write(target, text, prefix=_ATOMIC_PREFIX)
    return target


def _run_verb(ctx: TriageContext) -> HandlerResult:
    """``run_allowlisted_verb``: re-validate the typed arguments and their
    targets, run the verb once, re-run it to prove the run is complete (0
    changes), then ask for the requeue. Refuses (the task escalates) when
    anything does not check out, when the run reports a failed target, or
    when the re-run still reports changes."""
    kanban_root = _kanban_root_of(ctx.task_dir)
    try:
        args = validated_args(ctx.record)
        first = run_allowlisted_verb(kanban_root, args)
    except VerbRefused as exc:
        raise HandlerRefused(f"the operator data action was not run: {exc}") from exc
    if first.failed:
        path = _write_verb_evidence(ctx, (first,))
        raise HandlerRefused(
            f"{first.verb} could not read or write {first.failed} target(s) ({first.summary}); its output is in "
            f"{_relative_to_task(path, ctx.task_dir)}"
        )
    try:
        rerun = run_allowlisted_verb(kanban_root, args)
    except VerbRefused as exc:
        path = _write_verb_evidence(ctx, (first,))
        raise HandlerRefused(
            f"{first.verb} ran ({first.summary}) but its re-run was refused: {exc}; its output is in "
            f"{_relative_to_task(path, ctx.task_dir)}"
        ) from exc
    path = _write_verb_evidence(ctx, (first, rerun))
    rel = _relative_to_task(path, ctx.task_dir)
    if rerun.changes or rerun.failed:
        raise HandlerRefused(
            f"{first.verb} ran ({first.summary}) but a re-run still reported {rerun.changes} change(s) and "
            f"{rerun.failed} failure(s) ({rerun.summary}), so the run is not complete; both runs are in {rel}"
        )
    note = str(ctx.decision.params.get("note", "")) or ctx.decision.detail
    note += f". Run: {first.summary} Re-run: {rerun.summary} (0 changes); output in {rel}"
    return HandlerResult(mutation=MUTATION_PROMOTE, note=note, evidence=(rel,))


#: Handlers, keyed ``<action>:<reason_code>`` (one class) or ``<action>``
#: (every class choosing that action); :func:`handler_for` tries the first
#: form, then the second. An action the classifier decides with no handler
#: here is not performed: it escalates (or, on a task whose Needs Human is
#: no, is left to the handler that recorded the block).
ACTION_HANDLERS: dict[str, ActionHandler] = {
    **{f"requeue_owner:{code}": _requeue_owner for code in REQUEUE_CLASSES},
    "recompute_gate:gate_merge_not_found": _recompute_gate,
    "requeue_owner:gate_merge_not_found": _requeue_owner,
    "requeue_owner:gate_dirty_worktree": _requeue_owner,
    "requeue_owner:gate_empty_commit": _requeue_empty_commit,
    "prune_worktree_then_requeue:worktree_create_failed": _prune_worktree,
    f"run_allowlisted_verb:{OPERATOR_DATA_ACTION}": _run_verb,
}


def handler_for(handlers: Mapping[str, ActionHandler], action: str, reason_code: str) -> Optional[ActionHandler]:
    """The handler for *action* on a *reason_code* block, or None."""
    return handlers.get(f"{action}:{reason_code}") or handlers.get(action)


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Plan:
    """What ``apply`` will do about one task.

    ``action`` is the classifier's decision (``escalate``, ``not_yet`` or a
    table action); ``detail`` explains the outcome in plain language;
    ``cause`` is the diagnosis cause for an escalation. ``candidate`` is
    false for a task that is not a triage candidate at all (unreadable, no
    Block Record, or no longer BLOCKED); ``plan`` lists only candidates.

    ``verdict`` is the verification of the previous requeue in this epoch
    (when the task blocked again since), ``pending_index`` that ledger
    entry's index, and ``restart_epoch`` true when the verification passed
    and the new block starts a new epoch (``record`` and ``epoch`` are then
    the restarted ones). ``option`` is the first option an escalation's
    diagnosis names when the plan has a more specific one than the reason
    code's own.
    """

    task_id: str
    status_file: Path
    outcome: str
    reason_code: str
    action: str
    detail: str
    needs_human: str = "yes"
    epoch: str = "unknown"
    decision: Optional[Decision] = None
    record: Optional[BlockRecord] = None
    raw_record: str = ""
    cause: str = ""
    candidate: bool = True
    verdict: Optional[Verdict] = None
    pending_index: Optional[int] = None
    restart_epoch: bool = False
    option: str = ""


def _clean(text: str) -> str:
    """One line: tabs and line breaks become single spaces."""
    return re.sub(r"\s+", " ", text).strip()


def _code_token(value: Optional[str]) -> str:
    """A reason_code as it appears in a log action (``none`` when absent)."""
    code = normalize_reason_code(value or "")
    code = re.sub(r"[^a-z0-9_]", "", code)
    return code or NO_CODE


def epoch_token(value: str) -> str:
    """The block epoch as written in a diagnosis: canonical ISO when it parses."""
    parsed = try_parse_iso(value.strip()) if value and value.strip() else None
    if parsed is not None:
        return format_iso(parsed)
    return _clean(value) or "unknown"


def diagnosis_epoch(status_text: str) -> Optional[str]:
    """The epoch an existing ``## OVERWATCH Diagnosis`` names, or None."""
    body = read_section(status_text, DIAGNOSIS_HEADING)
    if not body:
        return None
    m = _DIAGNOSIS_EPOCH_RE.search(body)
    return epoch_token(m.group(1)) if m else None


def _not_implemented_cause(decision: Decision) -> str:
    return (
        f"the classifier chose {decision.action} ({decision.detail}), which this executor does not "
        "perform yet, so the block fails closed to a human"
    )


def _fleet_cause(record: Optional[BlockRecord], attempts: int) -> str:
    """Why a provider refusal that outlasted its attempts is a fleet-level matter."""
    evidence = parse_evidence(record.evidence) if record is not None else {}
    return (
        f"the provider is still refusing after {attempts} triage attempt(s) "
        f"(signature={evidence.get('signature', 'unknown')}, span_s={evidence.get('span_s', 'unknown')}), so the "
        "cause is fleet-level (the provider's authentication, the configured model or a usage limit) and "
        "affects every task, not this one alone"
    )


def _escalation_cause(
    decision: Decision, code: str, record: Optional[BlockRecord] = None, facts: Optional[Mapping] = None
) -> str:
    if decision.why == WHY_ALWAYS_HUMAN and code in _ALWAYS_HUMAN_GUIDANCE:
        return _ALWAYS_HUMAN_GUIDANCE[code][0]
    if decision.why == WHY_ATTEMPTS_EXHAUSTED and code == PROVIDER_REFUSAL:
        return f"{_fleet_cause(record, record.attempts if record is not None else 0)} ({decision.detail})"
    if decision.why == WHY_PRECONDITION_UNMET and facts:
        context = [f"{label}: {facts[key]}" for key, label in _FACT_CONTEXT if isinstance(facts.get(key), str)]
        if context:
            return f"{decision.detail} ({'; '.join(context)})"
    return decision.detail


def _verification_cause(pending: PendingCheck, verdict: Verdict, record: BlockRecord) -> str:
    """The diagnosis cause for a requeue whose verification failed."""
    cause = (
        f"{pending.reason_code} attempt {pending.attempt} of {pending.max_attempts} ({pending.action}) was taken at "
        f"{pending.acted_at} and its verification {verdict.name} failed on the task's next terminal outcome: "
        f"{verdict.detail}"
    )
    if pending.reason_code == PROVIDER_REFUSAL:
        cause += f"; {_fleet_cause(record, pending.attempt)}"
    return cause


def _retry_allowed(pending: PendingCheck, record: BlockRecord) -> bool:
    """True when a failed verification may still be retried: the same
    failure (reason_code) came back and its row allows another attempt."""
    ceiling = pending.max_attempts
    return (
        record.reason_code == pending.reason_code
        and isinstance(ceiling, int)
        and not isinstance(ceiling, bool)
        and pending.attempt < ceiling
    )


def _facts(
    code: str, text: str, record: Optional[BlockRecord], task_dir: Path, worktree_base: Optional[Path] = None
) -> dict:
    """What the classifier needs and the record does not carry: precondition
    facts read from the repository (read-only git) and note placeholder values."""
    if code == "per_task_timeout":
        seconds = timeout_seconds(text, record, task_dir)
        return {
            "killed_at": f"{seconds}s" if seconds is not None else "the per-task timeout (its length was not recorded)"
        }
    if code == "gate_merge_not_found":
        return gate_merge_facts(task_dir, record)
    if code == "gate_empty_commit":
        return gate_empty_commit_facts(task_dir, record, text)
    if code == "worktree_create_failed":
        return residue_facts(task_dir, record, worktree_base)
    if code == OPERATOR_DATA_ACTION:
        return _operator_action_facts(record, task_dir)
    return {}


def _operator_action_facts(record: Optional[BlockRecord], task_dir: Path) -> dict:
    """``action_targets_verified`` for an operator data action, and, when it
    is unmet, ``action_refusal`` naming why (arguments or targets)."""
    if record is None:
        return {"action_targets_verified": False, "action_refusal": "the Block Record cannot be read"}
    try:
        args = validated_args(record)
    except VerbRefused as exc:
        return {"action_targets_verified": False, "action_refusal": str(exc)}
    problems = target_problems(_kanban_root_of(task_dir), args)
    if problems:
        return {"action_targets_verified": False, "action_refusal": "; ".join(problems)}
    return {"action_targets_verified": True}


def _budget_for(status_file: Path) -> int:
    """``[overwatch] requeue_budget`` for the project holding *status_file*
    (``<kanban_root>/projects/<project>/tasks/<task_id>/status.md``).

    Raises:
        RequeueBudgetError: the configured value is not a whole number.
    """
    project_root = status_file.parent.parent.parent
    return resolve_requeue_budget(project_root.parent.parent, project_root.name)


def _budget_cause(record: BlockRecord, budget: int, code: str) -> str:
    """The diagnosis cause for a requeue the budget refuses: how many
    requeues were made, and every reason code seen, oldest first."""
    seen = [*record.requeue_reason_list(), code]
    distinct = list(dict.fromkeys(seen))
    cause = (
        f"the requeue budget is spent: block triage has requeued this task {record.requeues} time(s) over the "
        f"life of its Block Record ([{REQUEUE_BUDGET_SECTION}] {REQUEUE_BUDGET_KEY} = {budget}) and it blocked "
        f"again; reason codes seen, oldest first: {' -> '.join(seen)}"
    )
    if len(distinct) > 1:
        cause += (
            f". Its blocks alternate between {', '.join(distinct)}: each new block passes the previous "
            "requeue's verification and restarts the block epoch, so another requeue would repeat the cycle"
        )
    return cause


def _option_for(code: str) -> str:
    """The first option an escalation's diagnosis names for *code*."""
    if code in _ALWAYS_HUMAN_GUIDANCE:
        return _ALWAYS_HUMAN_GUIDANCE[code][1]
    return _CLASS_OPTIONS.get(code, _DEFAULT_OPTION)


def plan_task(
    status_file: "Path | str",
    *,
    triage_enabled: bool = True,
    now: "datetime.datetime | str | None" = None,
    table_path: "str | Path | None" = None,
    handlers: Optional[Mapping[str, ActionHandler]] = None,
    worktree_base: "Path | str | None" = None,
    requeue_budget: Optional[int] = None,
) -> Plan:
    """Decide what ``apply`` would do about one task. Reads only.

    Args:
        status_file:    The task's status.md.
        triage_enabled: ``[overwatch] block_triage``; false keeps the
                        outcome to records and diagnoses.
        now:            The decision time; the current UTC time by default.
        table_path:     The classifier table; the shipped one by default.
        handlers:       Action handlers; ``ACTION_HANDLERS`` by default.
        worktree_base:  The directory task worktrees live under; without it
                        no worktree residue is shown to be inside it.
        requeue_budget: ``[overwatch] requeue_budget``; read from the
                        project.cfg / kanban.cfg above *status_file* when
                        None, and only when a requeue is about to be planned.

    Raises:
        RequeueBudgetError: *requeue_budget* is None and the configured
            value is not a whole number.
    """
    path = Path(status_file)
    task_id = path.parent.name
    handlers = ACTION_HANDLERS if handlers is None else handlers
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return Plan(task_id, path, OUTCOME_SKIP, NO_CODE, NOT_YET, f"cannot read {path}: {exc}", candidate=False)

    raw_record = read_section(text, RECORD_HEADING) or ""
    if not raw_record.strip() or raw_record.strip().lower() == NONE_VALUE:
        return Plan(task_id, path, OUTCOME_SKIP, NO_CODE, NOT_YET, "carries no ## Block Record", candidate=False)
    raw_fields = parse_fields(raw_record)
    state = (read_section(text, "State") or "").strip().upper()
    if state != "BLOCKED":
        return Plan(
            task_id, path, OUTCOME_SKIP, _code_token(raw_fields.get("reason_code")), NOT_YET,
            f"## State reads {state or 'nothing'}, not BLOCKED", candidate=False,
        )

    needs_human = section_value(text, "Needs Human").strip().lower() or "yes"
    try:
        record = read_block_record(text, path)
    except BlockRecordError:
        record = None
    epoch = epoch_token(record.first_blocked_at if record else raw_fields.get("first_blocked_at", ""))
    code = _code_token(record.reason_code if record else raw_fields.get("reason_code"))
    common = dict(needs_human=needs_human, epoch=epoch, record=record, raw_record=raw_record)

    if needs_human == "yes" and diagnosis_epoch(text) == epoch:
        return Plan(
            task_id, path, OUTCOME_SKIP, code, ESCALATE,
            f"block epoch {epoch} is already escalated (Needs Human yes, OVERWATCH Diagnosis present)",
            **common,
        )

    # The previous requeue in this epoch, if any: verify it against the
    # block that followed it (see "Verification and block epochs").
    if record is not None:
        found = latest_pending(read_ledger(path.parent / EVIDENCE_DIR), epoch)
        if found is not None:
            pending_index, pending = found
            common["pending_index"] = pending_index
            if record.last_blocked_at == pending.blocked_at:
                cause = (
                    f"{pending.reason_code} attempt {pending.attempt} ({pending.action}) was taken at "
                    f"{pending.acted_at}, but the task is still BLOCKED on the block it was taken on, so the "
                    "requeue did not land; the same block is never acted on twice"
                )
                common["verdict"] = Verdict(pending.verification, STATUS_FAILED, "the requeue did not land")
                return Plan(task_id, path, OUTCOME_ESCALATE, code, ESCALATE, cause, cause=cause, **common)
            verdict = verify(pending, text, record)
            common["verdict"] = verdict
            if verdict.passed:
                record = dataclasses.replace(
                    record, first_blocked_at=record.last_blocked_at, attempts=0, cooldown_until=NONE_VALUE
                )
                text = set_or_append_section(text, RECORD_HEADING, record.to_body())
                epoch = epoch_token(record.first_blocked_at)
                common.update(record=record, epoch=epoch, restart_epoch=True)
            elif verdict.failed and not _retry_allowed(pending, record):
                cause = _verification_cause(pending, verdict, record)
                return Plan(task_id, path, OUTCOME_ESCALATE, code, ESCALATE, cause, cause=cause, **common)

    facts = _facts(code, text, record, path.parent, Path(worktree_base) if worktree_base else None)
    decision = classify(text, now=now, table_path=table_path, status_file=path, facts=facts)
    common["decision"] = decision

    # A transient block is the transient-error handlers' to requeue (behind
    # their backoff); triage only escalates it once their ceiling is reached.
    if code == TRANSIENT_CODE and record is not None and isinstance(decision.max_attempts, int):
        count, limit = transient_retry_state(text)
        ceiling = limit if limit is not None else decision.max_attempts
        if count >= ceiling:
            cause = (
                f"the transient-error handler reached its retry ceiling ({count} of {ceiling} in the "
                "## Transient Retry record) and stops retrying: the provider kept failing with transient errors"
            )
            return Plan(task_id, path, OUTCOME_ESCALATE, code, ESCALATE, cause, cause=cause, **common)
        return Plan(
            task_id, path, OUTCOME_SKIP, code, decision.action,
            f"transient retry {count} of {ceiling}: the block is left to the handler that recorded it (the "
            "transient-error auto-retry and the OVERWATCH transient check requeue it after their backoff)",
            **common,
        )

    if decision.action == NOT_YET:
        if decision.cooldown_until is not None:
            return Plan(
                task_id, path, OUTCOME_RECORD, code, NOT_YET,
                f"first sighting: cool-down recorded until {decision.cooldown_until}", **common,
            )
        return Plan(task_id, path, OUTCOME_SKIP, code, NOT_YET, decision.detail, **common)

    if decision.action == ESCALATE:
        cause = _escalation_cause(decision, code, record, facts)
        return Plan(task_id, path, OUTCOME_ESCALATE, code, ESCALATE, cause, cause=cause, **common)

    if decision.action == DEFER_ACTION:
        return _plan_deferral(task_id, path, code, decision, triage_enabled, common)

    if handler_for(handlers, decision.action, code) is None:
        if needs_human == "no":
            return Plan(
                task_id, path, OUTCOME_SKIP, code, decision.action,
                f"the classifier chose {decision.action}, which this executor does not perform yet; "
                "Needs Human is no, so the block is left to the handler that recorded it",
                **common,
            )
        cause = _not_implemented_cause(decision)
        return Plan(task_id, path, OUTCOME_ESCALATE, code, decision.action, cause, cause=cause, **common)

    if not triage_enabled:
        return Plan(
            task_id, path, OUTCOME_SKIP, code, decision.action,
            f"[overwatch] block_triage is false: {decision.action} not taken ({decision.detail})", **common,
        )

    # The requeue budget: bounds requeues across epoch restarts, which the
    # rows' own max_attempts (per epoch) cannot (see "Requeue budget").
    if record is not None and decision.action not in REQUEUE_FREE_ACTIONS:
        budget = requeue_budget if requeue_budget is not None else _budget_for(path)
        if record.requeues >= budget:
            cause = _budget_cause(record, budget, code)
            return Plan(
                task_id, path, OUTCOME_ESCALATE, code, ESCALATE, cause, cause=cause, option=_BUDGET_OPTION, **common
            )
    return Plan(task_id, path, OUTCOME_ACT, code, decision.action, decision.detail, **common)


def _plan_deferral(
    task_id: str, path: Path, code: str, decision: Decision, triage_enabled: bool, common: dict
) -> Plan:
    """The plan for a block the classifier defers to the resolution stage:
    ``defer`` when the project's HALT is a ship-policy HALT the stage can
    resolve, else ``skip`` (the block stays with a human). Reads only."""
    if not triage_enabled:
        return Plan(
            task_id, path, OUTCOME_SKIP, code, decision.action,
            f"[overwatch] block_triage is false: {decision.action} not taken ({decision.detail})", **common,
        )
    project_root = path.parent.parent.parent
    ready, why = ship_policy_halt_state(project_root.parent.parent, project_root.name)
    if not ready:
        return Plan(
            task_id, path, OUTCOME_SKIP, code, decision.action,
            f"{why}; the block stays with a human", **common,
        )
    return Plan(task_id, path, OUTCOME_DEFER, code, decision.action, f"{decision.detail} ({why})", **common)


def _queue_files(project_root: Path) -> list[Path]:
    queues = project_root / "tasks" / "queues"
    found = sorted(queues.glob("*_backlog.md")) + sorted(queues.glob("*/*_backlog.md"))
    return [q for q in found if q.is_file()]


def _key_ok(key: str, id_pattern: "re.Pattern[str] | None") -> bool:
    if not _SAFE_KEY_RE.match(key) or ".." in key:
        return False
    return id_pattern is None or id_pattern.match(key) is not None


def blocked_candidates(
    project_root: "Path | str", *, id_pattern: Optional[str] = None
) -> list[tuple[str, Path]]:
    """``(task_id, queue_file)`` for every ``[B]`` line whose task carries a
    status.md, in queue-file then line order, each task once."""
    root = Path(project_root)
    pattern = re.compile(id_pattern) if id_pattern else None
    seen: set = set()
    out: list[tuple[str, Path]] = []
    for queue in _queue_files(root):
        try:
            lines = queue.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line in lines:
            m = _QUEUE_LINE_RE.match(line)
            if m is None or m.group(1).strip().upper() != "B":
                continue
            key = m.group(2)
            if key in seen or not _key_ok(key, pattern):
                continue
            if not (root / "tasks" / key / "status.md").is_file():
                continue
            seen.add(key)
            out.append((key, queue))
    return out


def plan_project(
    project_root: "Path | str",
    *,
    id_pattern: Optional[str] = None,
    triage_enabled: bool = True,
    now: "datetime.datetime | str | None" = None,
    table_path: "str | Path | None" = None,
    worktree_base: "Path | str | None" = None,
    requeue_budget: Optional[int] = None,
) -> list[tuple[Plan, Path]]:
    """Plan every ``[B]`` task of a project that is BLOCKED with a Block Record."""
    root = Path(project_root)
    plans = []
    for key, queue in blocked_candidates(root, id_pattern=id_pattern):
        plan = plan_task(
            root / "tasks" / key / "status.md", triage_enabled=triage_enabled, now=now, table_path=table_path,
            worktree_base=worktree_base, requeue_budget=requeue_budget,
        )
        if plan.candidate:
            plans.append((plan, queue))
    return plans


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def _write_keeping_mode(path: Path, text: str) -> None:
    mode = path.stat().st_mode & 0o7777 if path.exists() else None
    atomic_write(path, text, prefix=_ATOMIC_PREFIX)
    if mode is not None:
        path.chmod(mode)


def _compact_stamp(moment: datetime.datetime) -> str:
    return moment.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_evidence(
    task_dir: Path,
    plan: Plan,
    *,
    now: datetime.datetime,
    triage_enabled: bool,
    backups: Mapping[str, str],
) -> Path:
    """Write the triage evidence file and return its path.

    ``artifacts/block-evidence/triage-<UTC stamp>-<outcome>.json``; a name
    already taken gets a ``-2``, ``-3``... suffix, so no evidence is ever
    overwritten.
    """
    evidence_dir = task_dir / EVIDENCE_DIR
    evidence_dir.mkdir(parents=True, exist_ok=True)
    base = f"triage-{_compact_stamp(now)}-{plan.outcome}"
    target = evidence_dir / f"{base}.json"
    n = 2
    while target.exists():
        target = evidence_dir / f"{base}-{n}.json"
        n += 1
    payload: dict[str, Any] = {
        "task_id": plan.task_id,
        "recorded_at": format_iso(now),
        "outcome": plan.outcome,
        "reason_code": plan.reason_code,
        "action": plan.action,
        "detail": plan.detail,
        "block_epoch": plan.epoch,
        "needs_human_before": plan.needs_human,
        "triage_enabled": triage_enabled,
        "decision": plan.decision.as_json_dict() if plan.decision is not None else None,
        "block_record": plan.record.as_dict() if plan.record is not None else plan.raw_record,
        "verification": plan.verdict.as_dict() if plan.verdict is not None else None,
        "epoch_restarted": plan.restart_epoch,
        "backups": dict(backups),
    }
    atomic_write(target, json.dumps(payload, indent=2, sort_keys=True) + "\n", prefix=_ATOMIC_PREFIX)
    return target


def render_diagnosis(
    plan: Plan,
    *,
    project: str,
    kanban_root: "Path | str",
    evidence_file: str,
    backups: Mapping[str, str],
) -> str:
    """The one-paragraph ``## OVERWATCH Diagnosis`` body for an escalation.

    Names the block epoch (the record's ``first_blocked_at``, which is how a
    later sweep recognises an already-escalated epoch), the cause, the
    evidence pointers and at least one concrete option, ending with the
    runnable commands that return the task to BACKLOG or close it.
    """
    record = plan.record
    owner = record.owner_role if record is not None else "unknown"
    attempts = record.attempts if record is not None else 0
    evidence_line = record.evidence if record is not None else "unreadable"
    cause = (plan.cause or plan.detail).rstrip(".")
    option = plan.option or _option_for(plan.reason_code)
    tool = f"bash {shlex.quote(str(Path(kanban_root) / 'scripts' / 'task-status.sh'))}"
    target = f"--project {shlex.quote(project)} --key {shlex.quote(plan.task_id)}"
    backup_text = "; ".join(f"{name} backup {path}" for name, path in sorted(backups.items()) if path) or "no backup"
    paragraph = (
        f"Block epoch {plan.epoch} (reason_code {plan.reason_code}, owner {owner}, "
        f"{attempts} triage attempt(s) made): {cause}. "
        f"Evidence: the Block Record evidence line ({evidence_line}); the triage evidence file {evidence_file}; "
        f"{backup_text}. "
        f"Options: (1) {option}; (2) once the cause is resolved, return the task to BACKLOG with "
        f"`{tool} {target} --state BACKLOG --needs-human no`; (3) if the task is no longer wanted, close it with "
        f"`{tool} {target} --state WONT-DO --needs-human yes --summary 'Closed after OVERWATCH diagnosis'`."
    )
    return _clean(paragraph)


def write_diagnosis(status_file: Path, body: str) -> None:
    """Set ``## Needs Human`` to yes and ``## OVERWATCH Diagnosis`` to *body*
    in one atomic write; every other section is kept byte-for-byte."""
    text = status_file.read_text(encoding="utf-8")
    text = set_or_append_section(text, "Needs Human", "yes")
    text = set_or_append_section(text, DIAGNOSIS_HEADING, body)
    _write_keeping_mode(status_file, text)


def append_triage_note(readme: Path, line: str) -> None:
    """Append one line to the README's ``## Triage Note`` (created at the end
    of the README when absent); earlier notes are kept."""
    text = readme.read_text(encoding="utf-8")
    existing = (read_section(text, TRIAGE_NOTE_HEADING) or "").strip()
    body = f"{existing}\n{line}" if existing else line
    _write_keeping_mode(readme, set_or_append_section(text, TRIAGE_NOTE_HEADING, body))


@dataclasses.dataclass(frozen=True)
class ApplyResult:
    """The outcome ``apply`` reports back to the check."""

    outcome: str
    reason_code: str
    action: str
    mutation: str
    detail: str
    evidence_file: str = ""

    def as_tsv(self) -> str:
        """Return outcome, reason code, action, mutation and detail as one tab-separated line.

        Each field is flattened to a single line; an empty field is written as
        ``none``. The evidence file is not included.
        """
        return "\t".join(
            _clean(v) or NO_CODE for v in (self.outcome, self.reason_code, self.action, self.mutation, self.detail)
        )


def _refuse(error_key: str, message: str, remedy: str) -> "TriageError":
    return TriageError(error_key=error_key, message=message, remedy=remedy)


def _escalate(
    plan: Plan, status_file: Path, *, project: str, kanban_root: "Path | str", evidence_file: Path,
    backups: Mapping[str, str],
) -> ApplyResult:
    rel = _relative_to_task(evidence_file, status_file.parent)
    body = render_diagnosis(plan, project=project, kanban_root=kanban_root, evidence_file=rel, backups=backups)
    write_diagnosis(status_file, body)
    return ApplyResult(
        OUTCOME_ESCALATE, plan.reason_code, ESCALATE, MUTATION_NONE,
        f"escalated: Needs Human yes, OVERWATCH Diagnosis written ({plan.cause or plan.detail}); evidence {rel}",
        str(evidence_file),
    )


def _moment(now: "datetime.datetime | str | None") -> datetime.datetime:
    """*now* as an aware datetime; the current UTC time when None or unparseable."""
    if isinstance(now, datetime.datetime):
        return now if now.tzinfo is not None else now.replace(tzinfo=datetime.timezone.utc)
    parsed = try_parse_iso(now) if isinstance(now, str) else None
    return parsed or datetime.datetime.now(datetime.timezone.utc)


def _relative_to_task(path: Path, task_dir: Path) -> str:
    try:
        return str(path.relative_to(task_dir))
    except ValueError:
        return str(path)


def apply_task(
    status_file: "Path | str",
    queue_file: "Path | str",
    *,
    project: str,
    kanban_root: "Path | str",
    triage_enabled: bool = True,
    backups: Optional[Mapping[str, str]] = None,
    now: "datetime.datetime | str | None" = None,
    table_path: "str | Path | None" = None,
    handlers: Optional[Mapping[str, ActionHandler]] = None,
    worktree_base: "Path | str | None" = None,
    defer_only: bool = False,
    stage_runner: Optional[StageRunner] = None,
    requeue_budget: Optional[int] = None,
) -> ApplyResult:
    """Re-plan one task and make its writes (see the module docstring).

    The evidence file is written before status.md or README.md is touched.
    The state mutation itself is the caller's: the result names it.

    A ``defer`` plan writes nothing: the resolution stage (*stage_runner*,
    the stage's ``run_resolution_stage`` by default) is run once and its
    outcome reported. With *defer_only*, any other plan writes nothing and
    reports ``skip`` (the caller took no backup). *requeue_budget* is
    ``[overwatch] requeue_budget``; None reads it from the project's config
    (see :func:`plan_task`).

    Raises:
        TriageError: the task folder or a file it needs cannot be read or
            written. What was written before the failure stays (evidence
            first, so it is never the state that is half-done).
        RequeueBudgetError: *requeue_budget* is None and the configured
            value is not a whole number; nothing is written.
    """
    path = Path(status_file)
    handlers = ACTION_HANDLERS if handlers is None else handlers
    moment = _moment(now)
    backups = dict(backups or {})
    base = Path(worktree_base) if worktree_base else None
    plan = plan_task(
        path, triage_enabled=triage_enabled, now=moment, table_path=table_path, handlers=handlers, worktree_base=base,
        requeue_budget=requeue_budget,
    )
    if defer_only and plan.outcome != OUTCOME_DEFER:
        return ApplyResult(
            OUTCOME_SKIP, plan.reason_code, plan.action, MUTATION_NONE,
            f"planned as a deferral, but under the lock the plan is {plan.outcome} ({plan.detail}); nothing "
            "written, left for the next sweep",
        )
    if plan.outcome == OUTCOME_SKIP:
        return ApplyResult(OUTCOME_SKIP, plan.reason_code, plan.action, MUTATION_NONE, plan.detail)
    if plan.outcome == OUTCOME_DEFER:
        deferral = defer_to_stage(kanban_root, project, runner=stage_runner)
        return ApplyResult(OUTCOME_DEFER, plan.reason_code, plan.action, MUTATION_NONE, deferral.detail)

    task_dir = path.parent
    try:
        evidence_file = write_evidence(task_dir, plan, now=moment, triage_enabled=triage_enabled, backups=backups)
        if plan.pending_index is not None and plan.verdict is not None:
            settle_entry(evidence_file.parent, plan.pending_index, plan.verdict.status, plan.verdict.detail, now=moment)
        if plan.restart_epoch:
            update_block_record(path, restart_epoch=True)
        if plan.outcome == OUTCOME_RECORD:
            update_block_record(path, cooldown_until=plan.decision.cooldown_until)
            return ApplyResult(
                OUTCOME_RECORD, plan.reason_code, NOT_YET, MUTATION_NONE, plan.detail, str(evidence_file)
            )
        if plan.outcome == OUTCOME_ESCALATE:
            return _escalate(
                plan, path, project=project, kanban_root=kanban_root, evidence_file=evidence_file, backups=backups
            )
        handler = handler_for(handlers, plan.action, plan.reason_code)
        assert handler is not None  # an act plan is only made when a handler exists
        return _act(
            plan, path, Path(queue_file), handler, moment, evidence_file,
            project=project, kanban_root=kanban_root, backups=backups, worktree_base=base,
        )
    except (OSError, UnicodeDecodeError, BlockRecordError, ValueError) as exc:
        raise _refuse(
            "block-triage.write-failed",
            f"block triage could not finish its writes for {plan.task_id} ({exc}); the task's state was not "
            "changed and the evidence written so far is kept",
            f"ls -l {shlex.quote(str(task_dir))} {shlex.quote(str(task_dir / EVIDENCE_DIR))}",
        ) from exc


def _act(
    plan: Plan, status_file: Path, queue_file: Path, handler: ActionHandler, moment: datetime.datetime,
    evidence_file: Path, *, project: str, kanban_root: "Path | str", backups: Mapping[str, str],
    worktree_base: Optional[Path] = None,
) -> ApplyResult:
    decision = plan.decision
    record = plan.record
    assert decision is not None and record is not None  # an act plan always carries both
    task_dir = status_file.parent
    context = TriageContext(
        task_id=plan.task_id, task_dir=task_dir, status_file=status_file, queue_file=queue_file,
        record=record, decision=decision, evidence_dir=evidence_file.parent, now=moment,
        worktree_base=worktree_base,
    )
    try:
        result = handler(context)
    except HandlerRefused as exc:
        refused = dataclasses.replace(
            plan, outcome=OUTCOME_ESCALATE, cause=f"{decision.action} refused at execution time: {exc}"
        )
        return _escalate(
            refused, status_file, project=project, kanban_root=kanban_root, evidence_file=evidence_file,
            backups=backups,
        )
    mutation = result.mutation
    if mutation != MUTATION_PROMOTE and not mutation.startswith(MUTATION_TRANSITION_PREFIX):
        raise ValueError(f"handler for {decision.action} returned unknown mutation {mutation!r}")

    attempt = decision.attempt if decision.attempt is not None else record.attempts + 1
    note = _clean(result.note or str(decision.params.get("note", "")) or decision.detail)
    pointers = [_relative_to_task(evidence_file, task_dir), *result.evidence]
    ceiling = decision.max_attempts if decision.max_attempts is not None else "?"
    line = (
        f"- {format_iso(moment)} OVERWATCH block triage, {plan.reason_code}: {decision.action}, "
        f"attempt {attempt} of {ceiling}. {note} (evidence: {', '.join(pointers)})"
    )
    append_triage_note(task_dir / "README.md", line)
    update_block_record(
        status_file,
        bump_attempts=True,
        cooldown_until=decision.cooldown_until or NONE_VALUE,
        resume=decision.params.get("resume"),
        # A return to the owner spends the requeue budget; a transition (the
        # recomputed gate's DONE) does not.
        record_requeue=plan.reason_code if mutation == MUTATION_PROMOTE else None,
    )
    append_pending(
        evidence_file.parent,
        PendingCheck(
            epoch=plan.epoch,
            reason_code=plan.reason_code,
            action=decision.action,
            attempt=attempt,
            max_attempts=decision.max_attempts,
            verification=decision.verification,
            acted_at=format_iso(moment),
            blocked_at=record.last_blocked_at,
            zero_progress_count=zero_progress_count(status_file.read_text(encoding="utf-8")),
            **(
                dict(status=result.verdict.status, settled_at=format_iso(moment), detail=result.verdict.detail)
                if result.verdict is not None
                else {}
            ),
        ),
    )
    cleared = f"OVERWATCH block triage {plan.reason_code} {decision.action}, attempt {attempt}: {note}"
    return ApplyResult(OUTCOME_ACT, plan.reason_code, decision.action, mutation, cleared, str(evidence_file))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _bool_arg(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered not in ("true", "false"):
        raise argparse.ArgumentTypeError(f"{value!r} is not true or false")
    return lowered == "true"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="block_triage",
        description=(
            "OVERWATCH block-triage executor: classify each blocked task's ## Block Record and make "
            "the writes the decision allows (evidence, Block Record, OVERWATCH Diagnosis, Triage Note). "
            "The state mutation itself (promote / transition) is printed for the calling check to perform. "
            "[overwatch] requeue_budget (project.cfg, then kanban.cfg; default 3) caps how many times one "
            "task is requeued over the life of its Block Record; a value that is not a whole number refuses."
        ),
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    def _common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--kanban-root", required=True, metavar="DIR", help="the kanban root holding projects/")
        p.add_argument("--project", required=True, metavar="NAME", help="the project whose tasks are triaged")
        p.add_argument(
            "--triage-enabled", type=_bool_arg, default=True, metavar="true|false",
            help="the resolved [overwatch] block_triage value; false keeps triage to records and diagnoses "
            "(default: true)",
        )
        p.add_argument("--now", metavar="ISO", help="the decision time (ISO-8601); default: the current UTC time")
        p.add_argument("--table", metavar="YAML_FILE", help="the classifier table; default: the shipped table")
        p.add_argument(
            "--worktree-base", metavar="DIR",
            help="the directory task worktrees live under (worktree.sh pgai_worktree_base); without it a "
            "worktree_create_failed block cannot be shown to have residue inside it and escalates",
        )

    plan = sub.add_parser(
        "plan",
        help="print what apply would do for every blocked task (reads only)",
        description=(
            "Print one tab-separated line per [B] task that reads BLOCKED and carries a ## Block Record: "
            "TASK_ID, QUEUE_FILE, OUTCOME (skip|record|escalate|act|defer), REASON_CODE, ACTION, DETAIL. "
            "Writes nothing."
        ),
    )
    _common(plan)
    plan.add_argument(
        "--id-pattern", metavar="REGEX",
        help="a regex every task key must match from its start; other keys are skipped (default: any safe key)",
    )

    apply = sub.add_parser(
        "apply",
        help="re-plan one task and make its writes",
        description=(
            "Re-plan TASK_ID, write its evidence file, then its Block Record / OVERWATCH Diagnosis / Triage "
            "Note writes. Prints one tab-separated line: OUTCOME, REASON_CODE, ACTION, MUTATION "
            "(none|promote|transition:STATE), DETAIL. A defer outcome (a ship-policy block) writes nothing "
            "of the task: it runs the resolution stage once and DETAIL reports the stage's decision. Run it "
            "only after backing up status.md and the queue file (not needed with --defer-only), and under the "
            "project's repo lock."
        ),
    )
    _common(apply)
    apply.add_argument("--task-id", required=True, metavar="ID", help="the task key")
    apply.add_argument(
        "--queue-file", required=True, metavar="FILE", help="the queue file carrying the task's [B] line"
    )
    apply.add_argument("--status-backup", default="", metavar="PATH", help="the status.md backup, named in evidence")
    apply.add_argument("--queue-backup", default="", metavar="PATH", help="the queue-file backup, named in evidence")
    apply.add_argument(
        "--defer-only", action="store_true",
        help="the plan named this task defer (no backup was taken): run the resolution stage when the plan "
        "under the lock is still defer, otherwise write nothing and report skip",
    )
    return parser


def _emit(exc: TriageError, project: Optional[str]) -> int:
    # CONVERT: a refused triage call leaves the task's state untouched (the
    # calling check performs no mutation after a failed apply) and the sweep
    # carries on; the cost is one task not triaged this firing, never a
    # stopped wake or release.
    # CENSUS-ANCHOR: block-triage-refusal-01
    emit_error_contract(exc.error_key, exc.message, exc.remedy, project=project)
    return 1


def main(argv: Optional[list] = None) -> int:
    """CLI entry point; returns the exit code."""
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    if args.now is not None and try_parse_iso(args.now) is None:
        parser.error(f"--now {args.now!r} is not an ISO-8601 time (e.g. 2026-01-01T00:00:00+00:00)")
    project_root = Path(args.kanban_root) / "projects" / args.project
    try:
        if not project_root.is_dir():
            raise _refuse(
                "block-triage.project-missing",
                f"project root {project_root} does not exist; nothing was triaged",
                f"ls -l {shlex.quote(str(Path(args.kanban_root) / 'projects'))}",
            )
        # Read before anything is planned, so a bad value refuses the whole
        # call, the way an unreadable [overwatch] block_triage does.
        try:
            requeue_budget = resolve_requeue_budget(args.kanban_root, args.project)
        except RequeueBudgetError as exc:
            raise _refuse(exc.error_key, exc.message, exc.remedy) from exc
        if args.command == "plan":
            for plan, queue in plan_project(
                project_root, id_pattern=args.id_pattern, triage_enabled=args.triage_enabled,
                now=args.now, table_path=args.table, worktree_base=args.worktree_base,
                requeue_budget=requeue_budget,
            ):
                fields = (plan.task_id, str(queue), plan.outcome, plan.reason_code, plan.action, plan.detail)
                sys.stdout.write("\t".join(_clean(f) or NO_CODE for f in fields) + "\n")
            return 0
        if not _key_ok(args.task_id, None):
            raise _refuse(
                "block-triage.bad-task-id",
                f"--task-id {args.task_id!r} is not a task key (letters, digits, '.', '_' and '-' only); "
                "nothing was written",
                f"ls {shlex.quote(str(project_root / 'tasks'))}",
            )
        status = project_root / "tasks" / args.task_id / "status.md"
        if not status.is_file():
            raise _refuse(
                "block-triage.status-missing",
                f"no status.md at {status}; nothing was written",
                f"ls -l {shlex.quote(str(status.parent))}",
            )
        result = apply_task(
            status, args.queue_file, project=args.project, kanban_root=args.kanban_root,
            triage_enabled=args.triage_enabled,
            backups={"status.md": args.status_backup, "queue": args.queue_backup},
            now=args.now, table_path=args.table, worktree_base=args.worktree_base,
            defer_only=args.defer_only, requeue_budget=requeue_budget,
        )
        sys.stdout.write(result.as_tsv() + "\n")
        return 0
    except TriageError as exc:
        return _emit(exc, args.project)


if __name__ == "__main__":
    sys.exit(main())
