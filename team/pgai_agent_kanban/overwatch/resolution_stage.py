"""
resolution_stage.py — OVERWATCH's autonomous block-resolution execution stage.

Wires OVERWATCH's block handling to the resolution matrix
(pgai_agent_kanban.overwatch.resolution_matrix) and the append-only ledger
(pgai_agent_kanban.overwatch.ledger). This module is the call site both
wake provider siblings invoke, once per project, on every OVERWATCH
firing (see "run_overwatch_resolution_stage" in team/scripts/lib/
wake_project.sh), and the stage block triage hands a ship_policy_halt block
to (pgai_agent_kanban.overwatch.block_triage) — which is how a fresh
ship-policy block reaches it within seconds: the on-BLOCK nudge a wake chain
fires after releasing its repo lock runs block triage
(fire_block_triage_nudge, team/scripts/lib/wake_project.sh).

Which HALT it acts on, read through pgai_agent_kanban.overwatch.
halt_provenance (the HALT body, else the sentinel-writes log):

  - No HALT: no-op.
  - An operator HALT -- the project's own, or the kanban root's, which is
    always the operator's: decision none. It is never read as anything
    else and never touched, and a chain HALT beside it is left alone too.
  - A chain HALT, by the writer its provenance names:
      cm-release            the trigger's row in the matrix's ``triggers``
                            list (resolution_matrix.load_trigger_rows). The
                            HALT is cleared as soon as one of the row's
                            ``clears_on`` checks passes (halt_clearing: RC
                            cancelled and rebundled, task reset, trigger 9
                            re-checked green). While none passes, ``matrix``
                            runs the ship-policy path below and ``wait``
                            decides none. A CM HALT that names no trigger
                            number, or one with no row, takes the ship-policy
                            path and is never cleared by a check.
      halt-after-promotion  a completed drain. With ``[overwatch]
                            halt_after_auto_resume = true`` it is cleared and
                            an inbox entry reads "stopped after rc:vX.Y.Z as
                            armed; resume with unhalt"; false (the default)
                            leaves it for the operator.
      zero-progress-cap     cleared once the task its reason names is reset.
      overwatch-runaway     never cleared here: it stops the project until the
                            operator answers its inbox entry.
      overwatch-repair      left to the repair that wrote it.

Every clear takes one path, clear_chain_halt(): re-read the HALT and
require the bytes the decision read, copy it under the project's
overwatch/backups/, remove it with ops.write.unhalt, then append a
"## OVERWATCH Decision" event naming the evidence. The drills below clear
the ship-policy HALT through the same path.

A question only a human can answer (Lane C, the Lane A single-attempt
guard) becomes one operator inbox entry (pgai_agent_kanban.overwatch.inbox);
a later firing that finds the same question still open writes no second
entry.

On a ship-policy HALT this module does exactly one of two things:

  - EXECUTE — the current block matches the shipped CANCEL-AND-REBUNDLE row.
    Performs the operator's documented cancel drill through the existing
    tools (team/scripts/cm/cancel-rc.sh --yes, wontdo_item, close_item,
    reset_item), and writes ONE ledger entry atomically with the decision
    (decide-and-log, never log-after — the ledger entry is appended BEFORE
    the drill's actions run, so a kill mid-drill leaves either no entry at
    all (killed before the decision was reached) or an entry whose
    actions_taken list already names everything the drill was about to do
    (killed during or after the drill) — OVERWATCH's next sweep reconciles
    a partially-applied drill against this record).
  - DECLINE — anything else (no ship-policy HALT present, an operator HALT
    present alongside a ship-policy HALT, an unmatched/unknown block shape,
    a ceiling-config or evidence error). No cancel/reversible drill action
    is ever taken on a decline; the decision is narrated as one line in the
    project's OVERWATCH action log ($KANBAN_ROOT/projects/<project>/
    overwatch/actions.log) so the decline is auditable without EVER writing
    a NEW ledger entry (the ledger records only resolutions actually
    executed, per ledger.py's own docstring). The one exception: when the
    decline is specifically because the bundle family has reached the
    retry ceiling, this module backfills the family's existing ledger
    entries' Outcome to CEILING-BLOCKED (closing out that family's story,
    per ledger.py's own "terminal state" contract) and narrates a richer
    decline reason carrying the family's full retry history ("attempt
    3/3; prior attempts cancelled for: ..."), a strictly richer human
    briefing than a single-failure view. This backfill never creates a
    ledger entry and never touches any other family's entries.

The same drill serves a squash conflict, before any HALT exists:
cm/release.sh, when its squash into main conflicts, backs the squash out and
calls resolve_squash_conflict() (through pgai_agent_kanban.cm.release_resolve).
The matrix is asked with trigger class ``squash-conflict``; a matched
CANCEL-AND-REBUNDLE row runs run_cancel_and_rebundle() -- the same ledger
entry, drill and Decision event as the ship-policy path, with no HALT to
clear -- and anything else declines, so the release writes its trigger 5
HALT instead.

This module never overrides a content verdict (SOP-core.md / OVERWATCH.md
authority boundary): it only executes the ONE prescribed, fully-reversible
remediation the matrix names, or does nothing at all.

Usage (CLI, the shape both wake siblings invoke via pp_run_ops):
    python3 -m pgai_agent_kanban.overwatch.resolution_stage \
        --project <name> --kanban-root <path>

Usage (import):
    from pgai_agent_kanban.overwatch.resolution_stage import run_resolution_stage
    outcome = run_resolution_stage(project_name, kanban_root)
"""

from __future__ import annotations

import argparse
import contextlib
import contextvars
import os
import pathlib
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterator, Optional

# Run by file path (``python3 .../resolution_stage.py``) or loaded from a file
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

from pgai_agent_kanban.cm.bundle_family import compute_bundle_family  # noqa: E402
from pgai_agent_kanban.cm.read_state_field import read_state_field  # noqa: E402
from pgai_agent_kanban.lib import ini  # noqa: E402
from pgai_agent_kanban.lib.atomic_write import atomic_write  # noqa: E402
from pgai_agent_kanban.lib.branch_prefix import InvalidBranchPrefixError, ini_branch_prefix_from_cfg  # noqa: E402
from pgai_agent_kanban.lib.error_contract import format_error_contract, module_file_command  # noqa: E402
from pgai_agent_kanban.ops.context import OpsContext  # noqa: E402
from pgai_agent_kanban.ops.errors import OpsError  # noqa: E402
from pgai_agent_kanban.ops.write import close_item, delete_item, reset_item, unhalt, wontdo_item  # noqa: E402
from pgai_agent_kanban.overwatch import halt_provenance, inbox  # noqa: E402
from pgai_agent_kanban.overwatch.halt_clearing import (  # noqa: E402
    check_notes_status_green,
    check_rc_cancelled,
    check_task_reset,
    halt_after_resume_text,
    halt_after_version,
    halt_written_at,
    task_id_in,
)
from pgai_agent_kanban.overwatch.halt_provenance import (  # noqa: E402
    WRITER_CM_RELEASE,
    WRITER_HALT_AFTER_PROMOTION,
    WRITER_OVERWATCH_REPAIR,
    WRITER_OVERWATCH_RUNAWAY,
    WRITER_ZERO_PROGRESS_CAP,
    HaltProvenance,
    classify_halt,
    installed_script_command,
)
from pgai_agent_kanban.overwatch.intent import read_project_intent  # noqa: E402
from pgai_agent_kanban.overwatch.ledger import (  # noqa: E402
    LedgerEntry,
    LedgerError,
    append_entry,
    backfill_family_outcome,
    ledger_path_for_project,
    parse_entries,
)
from pgai_agent_kanban.overwatch.resolution_matrix import (  # noqa: E402
    CANCEL_AND_REBUNDLE,
    CLEAR_NOTES_STATUS_GREEN,
    CLEAR_RC_CANCELLED,
    CLEAR_TASK_RESET,
    HALT_HARD,
    LANE_A,
    LANE_B,
    LANE_C,
    WHILE_OPEN_MATRIX,
    BlockEvidence,
    LaneClassification,
    ResolutionMatrixError,
    TRIGGER_CLASS_SQUASH_CONFLICT,
    TriggerRow,
    cargo_bug_ids,
    classify_lane,
    load_trigger_rows,
    matrix_findings,
    resolve_block,
    resolve_overwatch_resolve,
)
from pgai_agent_kanban.overwatch.runaway import RunawayError, resolve_halt_after_auto_resume  # noqa: E402
from pgai_agent_kanban.reports.tester_report import parse_report  # noqa: E402
from pgai_agent_kanban.task_status import read_state  # noqa: E402

# ---------------------------------------------------------------------------
# Outcome model
# ---------------------------------------------------------------------------

DECLINED = "declined"
EXECUTED = "executed"
NO_OP = "no-op"

# What the stage decided, in the decision wake's vocabulary: leave the HALT
# and the block as they are, run an operator verb (a drill, a forced ship,
# clearing a chain HALT), or park a question in the operator inbox.
DECISION_NONE = "none"
DECISION_VERB = "verb"
DECISION_INBOX = "inbox"

# classify_halt_file() values.
HALT_ABSENT = "absent"
HALT_OPERATOR = "operator"
HALT_SHIP_POLICY = "ship-policy"
HALT_CHAIN = "chain"


@dataclass
class ResolutionStageOutcome:
    """What one resolution-stage call did, for callers (tests, CLI) to inspect.

    ``decision`` is DECISION_NONE, DECISION_VERB or DECISION_INBOX;
    ``inbox_entry_id`` names the inbox entry a DECISION_INBOX outcome wrote
    or found still open (and the one a cleared HALT-AFTER HALT left).
    """

    status: str  # NO_OP | DECLINED | EXECUTED
    reason: str
    ledger_entry_id: Optional[str] = None
    actions_taken: list = field(default_factory=list)
    action_errors: list = field(default_factory=list)
    decision: str = DECISION_NONE
    inbox_entry_id: Optional[str] = None


# ---------------------------------------------------------------------------
# HALT file classification -- through halt_provenance, the one reader that
# tells a chain HALT from an operator's
# ---------------------------------------------------------------------------


def classify_halt_file(path: str, *, kanban_root: Optional[str] = None) -> str:
    """Return "ship-policy", "chain", "operator" or "absent" for the HALT at *path*.

    Read through :func:`halt_provenance.classify_halt`: "ship-policy" is a
    chain HALT CM wrote (writer cm-release); "chain" is a chain HALT any
    other chain writer wrote; "operator" is everything the reader cannot
    prove the chain wrote, the kanban root's own HALT included; "absent"
    means no file. *kanban_root* defaults to the root derived from the
    HALT's directory (``<root>/projects/<name>/HALT`` -> ``<root>``).
    """
    if not path:
        return HALT_ABSENT
    provenance = classify_halt(path, kanban_root=kanban_root)
    if provenance.origin == halt_provenance.ORIGIN_ABSENT:
        return HALT_ABSENT
    if not provenance.is_chain:
        return HALT_OPERATOR
    return HALT_SHIP_POLICY if provenance.writer == WRITER_CM_RELEASE else HALT_CHAIN


def _locate_halt_files(kanban_root: str, project_name: str) -> tuple:
    """Return (project_halt_path, global_halt_path) — the two conventional
    HALT locations this kanban uses (SOP-core.md "HALT Flags"; unwind-rc.sh
    pre-flight check 2 recognizes both). Paths are returned unconditionally;
    callers check existence via classify_halt_file().
    """
    project_halt = os.path.join(kanban_root, "projects", project_name, "HALT")
    global_halt = os.path.join(kanban_root, "HALT")
    return project_halt, global_halt


# ---------------------------------------------------------------------------
# Action log (Python counterpart of overwatch_log_action in
# team/scripts/lib/overwatch_protocol.sh — same tab-separated format, same
# target file, so operator tooling reads one uniform log regardless of
# whether an entry came from a Tier-1 bash check or this Python stage)
# ---------------------------------------------------------------------------


def log_action(
    kanban_root: str,
    project_name: str,
    name: str,
    target: str,
    action: str,
    reason: str,
    backup_path: str = "none",
) -> None:
    """Append one line to the project's OVERWATCH action log.

    Mirrors overwatch_log_action's record format exactly: tab-separated
    timestamp, name, target, action, backup_path, reason. Creates the
    project's overwatch/ state directory if it does not yet exist (the
    bootstrap OVERWATCH.md pre-flight Step 3 normally performs, defensively
    repeated here since this module may run before any bash-side bootstrap).
    """
    state_dir = os.path.join(kanban_root, "projects", project_name, "overwatch")
    os.makedirs(state_dir, exist_ok=True)
    log_file = os.path.join(state_dir, "actions.log")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = "\t".join(
        [timestamp, name, target, action, backup_path or "none", reason or ""]
    )
    with open(log_file, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Clearing a chain HALT -- the one path every clear in this module takes:
# re-check, back up, ops.write.unhalt. The caller records the Decision event.
# ---------------------------------------------------------------------------

# The Decision-event lane for a chain HALT cleared because its cause is gone
# (the drills and lanes record their own lane).
LANE_CHAIN_HALT = "chain-halt"


@dataclass
class HaltClear:
    """What one clear_chain_halt() call did.

    ``step`` is the action description the caller records; ``error`` is set
    (and ``cleared`` False) when the HALT was left in place, saying why.
    """

    cleared: bool
    step: str
    backup_path: Optional[str] = None
    error: Optional[str] = None


def _project_halt_path(kanban_root: str, project_name: str) -> str:
    return os.path.join(kanban_root, "projects", project_name, "HALT")


def read_halt_bytes(path: str) -> Optional[bytes]:
    """The HALT file's bytes, or None when it is absent or unreadable."""
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        return None


def backup_halt(kanban_root: str, project_name: str, halt_path: str, timestamp: str) -> str:
    """Copy the project HALT into OVERWATCH's backups and return the copy's path.

    The layout is overwatch_backup_file's (team/scripts/lib/
    overwatch_protocol.sh), ``<project>/overwatch/backups/<timestamp>/
    project-root/HALT``, so team/scripts/overwatch-revert.sh restores the
    copy to the path it came from. A copy already there for the same
    timestamp is kept: the first copy in a firing is the pre-firing file.
    """
    backup_dir = os.path.join(
        kanban_root, "projects", project_name, "overwatch", "backups", timestamp, "project-root"
    )
    os.makedirs(backup_dir, exist_ok=True)
    destination = os.path.join(backup_dir, os.path.basename(halt_path))
    if not os.path.isfile(destination):
        shutil.copy2(halt_path, destination)
    return destination


#: The callback clear_chain_halt() runs immediately before ops.write.unhalt
#: when its caller passed none of its own; set by before_each_unhalt().
_BEFORE_UNHALT: "contextvars.ContextVar[Optional[Callable[[], None]]]" = contextvars.ContextVar(
    "resolution_stage_before_unhalt", default=None
)


@contextlib.contextmanager
def before_each_unhalt(callback: Optional[Callable[[], None]]) -> Iterator[None]:
    """Run *callback* before every HALT clear made inside the ``with`` block.

    A caller that runs the whole stage (the decision executor's
    ``resolution-stage`` verb) cannot hand ``before_unhalt`` to each clear
    the stage makes. Inside this block, every clear_chain_halt() call that
    passes no ``before_unhalt`` of its own runs *callback* at that same
    moment instead. ``None`` sets no callback. The previous callback is
    restored on exit.
    """
    token = _BEFORE_UNHALT.set(callback)
    try:
        yield
    finally:
        _BEFORE_UNHALT.reset(token)


def clear_chain_halt(
    ctx: OpsContext,
    kanban_root: str,
    project_name: str,
    *,
    step: str,
    expected: Optional[bytes] = None,
    writer: Optional[str] = None,
    timestamp: Optional[str] = None,
    before_unhalt: Optional[Callable[[], None]] = None,
) -> HaltClear:
    """Clear the project's chain HALT through ``ops.write.unhalt``, after a backup.

    The HALT is re-read immediately before removal. It is left in place --
    an ``error`` saying why -- when it is gone, when *expected* is given and
    the bytes differ from what the decision read, when it no longer reads
    as the chain's, or when *writer* is given and the provenance names
    another writer. Only the project HALT is ever cleared: the kanban
    root's HALT is always the operator's.

    Args:
        step:      The action description to record (and to prefix errors).
        expected:  The HALT bytes the decision was made on.
        writer:    The chain writer the HALT must still name.
        timestamp: The backup directory's timestamp (default: now).
        before_unhalt: Called once every check has passed and the backup is
                   written, immediately before ``ops.write.unhalt`` (the
                   decision executor closes the HALT's blocked-ledger block
                   class any there, before unhalt would close it class human).
                   When None, the callback an enclosing before_each_unhalt()
                   block set runs instead, if any.
    """
    halt_path = _project_halt_path(kanban_root, project_name)
    current = read_halt_bytes(halt_path)
    if current is None:
        return HaltClear(False, step, error=f"{step} SKIPPED: no HALT at {halt_path} any more")
    provenance = classify_halt(halt_path, kanban_root=kanban_root)
    if expected is not None and current != expected:
        problem = "its bytes changed after the decision read it"
    elif not provenance.is_chain:
        problem = f"it now reads as the operator's ({provenance.detail})"
    elif writer is not None and provenance.writer != writer:
        problem = f"it now names writer {provenance.writer}, not {writer}"
    else:
        problem = ""
    if problem:
        contract = format_error_contract(
            "overwatch.chain-halt-changed",
            f"OVERWATCH did not clear {halt_path}: {problem}. A HALT is cleared only when it is "
            "still exactly the chain HALT the decision was made on.",
            module_file_command(
                halt_provenance.__file__, "classify", halt_path, "--kanban-root", kanban_root
            ),
        )
        # BLOCK-WORTHY: an approval-by-design invariant. The stage never
        # removes a HALT it did not decide on -- an operator's HALT, or one
        # rewritten since the decision -- so the HALT stays and the project
        # stays stopped until the next firing decides on what is there now.
        # CENSUS-ANCHOR: chain-halt-clear-recheck-01
        return HaltClear(False, step, error=f"{step} SKIPPED: {contract}")
    try:
        backup = backup_halt(kanban_root, project_name, halt_path, timestamp or _utc_stamp())
    except OSError as exc:
        return HaltClear(False, step, error=f"{step} FAILED: backup could not be written ({exc}); HALT left in place")
    callback = before_unhalt if before_unhalt is not None else _BEFORE_UNHALT.get()
    if callback is not None:
        callback()
    try:
        unhalt(ctx, project_name)
    except OpsError as exc:
        return HaltClear(False, step, backup_path=backup, error=f"{step} FAILED: {exc}")
    return HaltClear(True, step, backup_path=backup)


# ---------------------------------------------------------------------------
# Operator inbox -- where a question only a human can answer goes
# ---------------------------------------------------------------------------


def write_inbox_question(
    ctx: OpsContext,
    project_name: str,
    *,
    source: str,
    question: str,
    meanwhile: str,
    how_to_answer: str,
) -> tuple:
    """Park *question* in the project's operator inbox once.

    An open entry with the same source and question is reused, so the
    stage firing again on an unchanged block writes no second entry.

    Returns:
        ``(entry_id, created, error)``: the entry's ID (None on failure),
        whether this call wrote it, and the refusal text when the inbox
        refused the write.
    """
    try:
        wanted = (" ".join(source.split()), " ".join(question.split()))
        for entry in inbox.list_open(ctx, project_name):
            if (" ".join(entry.source.split()), " ".join(entry.question.split())) == wanted:
                return entry.entry_id, False, None
        entry = inbox.write_entry(
            ctx, project_name,
            source=source, question=question, meanwhile=meanwhile, how_to_answer=how_to_answer,
        )
    except OpsError as exc:
        return None, False, str(exc)
    return entry.entry_id, True, None


def _inbox_close_command(ctx: OpsContext, project_name: str) -> str:
    """The inbox close command, completed by the entry's own ID.

    The command names ``<kanban root>/scripts/inbox.sh``, built through
    :func:`halt_provenance.installed_script_command` so every word is
    shell-quoted; the trailing prose tells the operator to append the ID.
    """
    command = halt_provenance.installed_script_command(
        ctx.kanban_root, "inbox.sh", "--project", project_name, "close", "--id"
    )
    return f"{command} followed by this entry's INBOX ID"


def _halt_after_command(kanban_root: str, project_name: str) -> str:
    """The command that arms *project_name*'s HALT-AFTER again, runnable as printed.

    Names ``<kanban root>/scripts/halt-after.sh --project <name>``, built
    through :func:`halt_provenance.installed_script_command` (shell-quoted).
    """
    return halt_provenance.installed_script_command(kanban_root, "halt-after.sh", "--project", project_name)


# ---------------------------------------------------------------------------
# project.cfg reading (dev_tree_path, branch_prefix) — minimal by design:
# only the two keys this module needs, from project.cfg alone (no legacy
# PROJECT.cfg). branch_prefix is read through lib/branch_prefix.py; a value
# pp_branch_prefix refuses reads as none, as an unreadable project.cfg does.
# ---------------------------------------------------------------------------


def _read_project_cfg(project_root: str) -> dict:
    cfg_path = os.path.join(project_root, "project.cfg")
    try:
        branch_prefix = ini_branch_prefix_from_cfg(cfg_path)
    except InvalidBranchPrefixError:
        branch_prefix = ""
    result = {"dev_tree_path": "", "branch_prefix": branch_prefix}
    if not os.path.isfile(cfg_path):
        return result
    try:
        parser = ini.load(cfg_path)
    except ini.IniError:
        return result
    if parser.has_option("project", "dev_tree_path"):
        result["dev_tree_path"] = parser.get("project", "dev_tree_path").strip()
    return result


# ---------------------------------------------------------------------------
# RC task-set / TESTER-report discovery: does this task's README reference
# this RC version -- as a whole version token, never a substring, so
# v1.4.1 never matches a README that names only v1.4.10 or v1.4.1.2
# ---------------------------------------------------------------------------


def references_version(text: str, version: str) -> bool:
    """Whether *text* names *version* as a whole version token.

    The version may sit after a branch or tag prefix (``ai_v1.4.1``,
    ``ai_rc/v1.4.1``) or before a suffix that is not another version
    component (``v1.4.1-bugfix-bundle.md``, ``v1.4.1.`` ending a sentence);
    it never matches inside a longer version (``v1.4.10``, ``v1.4.1.2``,
    ``v11.4.1``).
    """
    if not version:
        return False
    pattern = r"(?<![0-9A-Za-z.])" + re.escape(version) + r"(?![0-9]|\.[0-9])"
    return re.search(pattern, text) is not None


def find_rc_task_ids(tasks_dir: str, version: str) -> list:
    """Return task IDs (any role) whose README references *version* and whose
    current state is not DONE or WONT-DO — the RC's still-active task set.
    The README must name the version as a whole token (references_version).
    """
    task_ids = []
    if not version or not os.path.isdir(tasks_dir):
        return task_ids
    for entry in sorted(os.listdir(tasks_dir)):
        task_dir = os.path.join(tasks_dir, entry)
        if not os.path.isdir(task_dir) or entry == "queues":
            continue
        readme = os.path.join(task_dir, "README.md")
        status_file = os.path.join(task_dir, "status.md")
        if not os.path.isfile(readme):
            continue
        try:
            readme_text = open(readme, "r", encoding="utf-8").read()
        except OSError:
            continue
        if not references_version(readme_text, version):
            continue
        state = read_state(status_file) if os.path.isfile(status_file) else ""
        if state in ("DONE", "WONT-DO"):
            continue
        task_ids.append(entry)
    return task_ids


def find_tester_report(tasks_dir: str, version: str) -> Optional[str]:
    """Best-effort: locate the artifacts/report.md of the most recent DONE
    TESTER-* task whose README references *version* as a whole token
    (references_version). Returns None when no
    such task is found — the caller's fail-closed evidence extraction (see
    resolution_matrix.py's _fact_findings_all_filed) then defaults to False,
    which is the conservative, correct outcome when no report can be traced.
    """
    if not version or not os.path.isdir(tasks_dir):
        return None
    candidates = []
    for entry in sorted(os.listdir(tasks_dir)):
        if not entry.startswith("TESTER-"):
            continue
        task_dir = os.path.join(tasks_dir, entry)
        readme = os.path.join(task_dir, "README.md")
        status_file = os.path.join(task_dir, "status.md")
        report = os.path.join(task_dir, "artifacts", "report.md")
        if not (os.path.isfile(readme) and os.path.isfile(status_file) and os.path.isfile(report)):
            continue
        try:
            readme_text = open(readme, "r", encoding="utf-8").read()
        except OSError:
            continue
        if not references_version(readme_text, version):
            continue
        if read_state(status_file) != "DONE":
            continue
        candidates.append(entry)
    if not candidates:
        return None
    # Task IDs embed YYYYMMDD-NNN; lexical order == chronological order.
    latest = sorted(candidates)[-1]
    return os.path.join(tasks_dir, latest, "artifacts", "report.md")


# ---------------------------------------------------------------------------
# The drill: cm-cancel-rc --yes -> wontdo the RC task set -> close the
# bundle intake item wont-do -> reset the cargo bugs to open -> clear ONLY
# the ship-policy HALT it is resolving
# ---------------------------------------------------------------------------


def _cancel_rc_step(project_name: str, version: str) -> str:
    """The action description for the drill's cancel step — one spelling
    shared by the ledger's intended-actions list, execute_drill()'s
    completed/errors lists, and the caller's did-the-cancel-land check."""
    return f"scripts/cm/cancel-rc.sh --project {project_name} --key {version} --yes"


def _run_cm_cancel_rc(kanban_root: str, project_name: str, version: str) -> tuple:
    """Invoke team/scripts/cm/cancel-rc.sh --yes as a subprocess.

    Returns (ok, output). Never raises — a failure comes back as
    (False, output), and both callers stop there: execute_drill() runs none
    of its status reverts, and execute_lane_a_drill() neither deletes the
    bundle document nor clears the ship-policy HALT.
    """
    script = os.path.join(kanban_root, "scripts", "cm", "cancel-rc.sh")
    cmd = [script, "--project", project_name, "--key", version, "--yes"]
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = kanban_root
    env["KANBAN_ROOT"] = kanban_root
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)
    except OSError as exc:
        return False, f"scripts/cm/cancel-rc.sh could not be executed: {exc}"
    ok = result.returncode == 0
    output = (result.stdout or "") + (result.stderr or "")
    return ok, output


def _clear_ship_policy_halt(
    ctx: OpsContext,
    kanban_root: str,
    project_name: str,
    ship_halt_path: str,
    expected_halt: Optional[bytes],
    clears: Optional[list],
    completed: list,
    errors: list,
) -> None:
    """The drills' last step: clear the ship-policy HALT through clear_chain_halt().

    Records the step in *completed* or the reason it was left in *errors*,
    and appends the HaltClear to *clears* when a list is given.
    """
    result = clear_chain_halt(
        ctx,
        kanban_root,
        project_name,
        step=f"cleared ship-policy HALT: {ship_halt_path}",
        expected=expected_halt,
        writer=WRITER_CM_RELEASE,
    )
    if clears is not None:
        clears.append(result)
    if result.cleared:
        completed.append(result.step)
    else:
        errors.append(result.error)


def execute_drill(
    ctx: OpsContext,
    kanban_root: str,
    project_name: str,
    version: str,
    rc_task_ids: list,
    bundle_key: Optional[str],
    bug_keys: list,
    ship_halt_path: Optional[str],
    expected_halt: Optional[bytes] = None,
    clears: Optional[list] = None,
) -> tuple:
    """Perform the five-step cancel drill, all-or-nothing on the cancel.

    Returns (completed, errors): completed lists the action descriptions
    that succeeded, errors the ones that failed or were skipped.

    Step 1, scripts/cm/cancel-rc.sh, is the gate for everything after it. The four
    later steps (wont-do the RC task set, close the bundle intake item
    wont-do, reset the cargo bugs to open, clear the ship-policy HALT) only
    make sense for an RC that really was cancelled:

      - Cancel failed (any non-zero exit, e.g. the project lock was held and
        the script reported LOCKED): no later step runs. The RC task set,
        the bundle intake item and every cargo bug keep the statuses they
        had (bundle and bugs stay "running"), so a later ship of the same
        RC still promotes them to done. The ship-policy HALT is also left
        in place: the RC is still active and still under the ship-policy
        verdict, so the HALT keeps it in front of the operator and stops
        the chain from shipping it. The next OVERWATCH sweep sees the same
        HALT and Active RC and re-runs this drill from the top. errors
        carries two entries: the cancel failure with the tail of the
        script's output, then one entry stating that the status-revert
        steps were not run and what was left unchanged.
      - Cancel succeeded: the four later steps each run best-effort and
        independently — a failure in one is recorded in errors and the rest
        are still attempted, so a partially-applied drill is fully visible
        rather than silently truncated.

    The HALT is cleared through clear_chain_halt(): only while it still
    holds *expected_halt* (when given) and still names CM as its chain
    writer, after a backup, with ops.write.unhalt. The HaltClear is
    appended to *clears* when a list is given, for the caller's Decision
    event. With *ship_halt_path* None there is no HALT to clear (a squash
    conflict the release resolves before writing one) and the drill ends
    after the cargo reset.
    """
    completed: list = []
    errors: list = []

    ok, output = _run_cm_cancel_rc(kanban_root, project_name, version)
    step = _cancel_rc_step(project_name, version)
    if not ok:
        errors.append(f"{step} FAILED: {output.strip()[-500:]}")
        errors.append(
            "status-revert steps not run because the RC cancel failed: "
            f"{len(rc_task_ids)} RC task(s), bundle intake item "
            f"{bundle_key or 'none'} and {len(bug_keys)} cargo bug(s) left "
            + (f"unchanged; ship-policy HALT left in place ({ship_halt_path})" if ship_halt_path else "unchanged")
        )
        return completed, errors
    completed.append(step)

    for task_id in rc_task_ids:
        step = f"wontdo: {task_id}"
        try:
            wontdo_item(ctx, project_name, task_id)
            completed.append(step)
        except OpsError as exc:
            errors.append(f"{step} FAILED: {exc}")

    if bundle_key:
        step = f"close (wont-do): {bundle_key}"
        try:
            close_item(ctx, project_name, bundle_key, state="wont-do", note="OVERWATCH auto-resolution: CANCEL-AND-REBUNDLE")
            completed.append(step)
        except OpsError as exc:
            errors.append(f"{step} FAILED: {exc}")
    else:
        errors.append("close (wont-do): no bundle intake item located — skipped")

    for bug_key in bug_keys:
        step = f"reset (open): {bug_key}"
        try:
            reset_item(ctx, project_name, bug_key)
            completed.append(step)
        except OpsError as exc:
            errors.append(f"{step} FAILED: {exc}")

    if ship_halt_path:
        _clear_ship_policy_halt(
            ctx, kanban_root, project_name, ship_halt_path, expected_halt, clears, completed, errors
        )
    return completed, errors


# ---------------------------------------------------------------------------
# The ceiling — the terminal path complementing the matched-resolution
# path above. Reads the retry counter's history (compute_bundle_family)
# and the ceiling already resolved into result.facts by
# resolution_matrix.py to produce a HALT-hard decline whose block text is
# a strictly richer briefing than a single-failure view: the bundle
# family's full attempt history.
# ---------------------------------------------------------------------------


def _is_ceiling_reached(facts) -> bool:
    """True when the HALT-hard decline is specifically because the bundle
    family's attempt count has reached (or passed) the retry ceiling, as
    opposed to any other unmatched/unknown block shape.

    Both attempt and ceiling must be known — an undeterminable attempt count
    already fails closed to attempts_below_ceiling=False (see
    resolution_matrix._fact_attempts_below_ceiling), but that is a distinct
    "insufficient evidence" decline, not "ceiling reached"; this predicate
    only fires when the resolver can actually name the number that hit the
    wall.
    """
    return (
        facts.attempt is not None
        and facts.ceiling is not None
        and facts.attempt >= facts.ceiling
    )


def _format_ceiling_block_text(facts, family: dict) -> str:
    """Build the ceiling decline's block text: the full attempt history for
    the bundle family ("attempt 3/3; prior attempts cancelled for: ...")
    — a strictly richer human briefing than a single-failure view.
    """
    cancelled_rcs = [
        str(record.get("rc") or "unknown")
        for record in family.get("history", [])
        if record.get("outcome") == "cancelled"
    ]
    history_clause = (
        f"prior attempts cancelled for: {', '.join(cancelled_rcs)}"
        if cancelled_rcs
        else "prior attempts cancelled for: none recorded"
    )
    family_key = family.get("family_key") or "untracked (no bundled cargo)"
    return (
        f"ceiling reached: attempt {facts.attempt}/{facts.ceiling}; {history_clause} "
        f"(bundle family {family_key}; trigger_class={facts.trigger_class})"
    )


def _backfill_ceiling_ledger(ledger_path: str, family_key: Optional[str]) -> list:
    """Close out the bundle family's ledger story at the ceiling: backfill
    every still-pending entry for *family_key* to Outcome=CEILING-BLOCKED
    (ledger.py's own documented terminal state for a family that never
    shipped). Never creates a new ledger entry — only the matched-resolution
    path (execute_drill's caller) writes new entries.

    Best-effort and non-fatal: a family with no ledger yet (untracked cargo,
    or a ceiling reached before any resolution was ever executed for it) or
    a ledger.py structural problem must not turn a clean HALT-hard decline
    into an unhandled exception — the decline already stands on its own;
    OVERWATCH's next sweep can reconcile a missed backfill same as it
    reconciles a killed-mid-drill execution.
    """
    if not family_key or not os.path.isfile(ledger_path):
        return []
    try:
        return backfill_family_outcome(ledger_path, family_key, "CEILING-BLOCKED")
    except LedgerError:
        return []


# ---------------------------------------------------------------------------
# Three-lane classification — the shared record every lane
# writes, plus the Lane A recovery drill, the Lane B force-ship playbook,
# and the Lane C escalation path.
# Lane A's recovery-playbook steps (b)-(e) — unwind-rc, cancel-rc, delete
# the bundle doc, clear the ship-policy HALT — are executed here (see the
# "Lane A recovery playbook" section below), gated on step (a), the
# Fix-rewrite (pgai_agent_kanban.overwatch.fix_rewrite.
# rewrite_bug_fix_section()), already being present and
# attributed on the blamed bug: _run_lane_a() below declines steps (b)-(e)
# rather than assume the rewrite already ran. Lane B's
# force-ship playbook is executed here too — see "Lane B
# force-ship playbook" below: it forces cm/release.sh past the ship-policy stop it is
# resolving, with the matched INTENT.md entry's own text as --reason, then
# clears that ship-policy HALT on success. All of this runs ONLY when
# overwatch_resolve is true; with the flag false, run_resolution_stage()
# takes none of the code paths below: an unmatched block is only narrated
# and declined.
# ---------------------------------------------------------------------------


def _atomic_append_release_state_event(release_state_path: str, block: str) -> None:
    """Append *block* to the project's release-state.md, atomically
    (pgai_agent_kanban.lib.atomic_write: temp file in the same directory +
    fsync + os.replace). Creates the file with a minimal header if it does
    not yet exist (defensive; release-state.md is normally created by
    scripts/cm/open-rc.sh well before OVERWATCH ever fires). Never touches any
    byte already in the file — this is what makes the Decision event
    append-only: prior events, and every other section of
    release-state.md, are carried through unchanged.
    """
    existing = ""
    if os.path.isfile(release_state_path):
        with open(release_state_path, "r", encoding="utf-8") as handle:
            existing = handle.read()
    else:
        existing = "# Release State\n"

    new_content = existing.rstrip("\n") + "\n\n" + block.strip("\n") + "\n"

    os.makedirs(os.path.dirname(os.path.abspath(release_state_path)), exist_ok=True)
    atomic_write(release_state_path, new_content, prefix=".release_state_tmp_")


def _format_bullets(values: list) -> str:
    if not values:
        return "  - none"
    return "\n".join(f"  - {value}" for value in values)


def write_decision_event(
    release_state_path: str,
    *,
    timestamp: str,
    version: str,
    lane: str,
    evidence_refs: list,
    verbs_run: list,
    outcome: str,
) -> None:
    """Append one "## OVERWATCH Decision" event to *release_state_path* —
    the shared record every lane (A, B, C) writes when the three-lane
    classifier runs. Mirrors the "## Operator Override" event shape
    team/scripts/lib/cm/release_preflight.sh's
    _cm_record_operator_override_event documents (Timestamp/Version/...
    fields), extended with the lane, the evidence references, and the
    verbs run. Append-only: never edits or removes a
    prior event (see _atomic_append_release_state_event above).
    """
    block = (
        "## OVERWATCH Decision\n"
        f"Timestamp: {timestamp}\n"
        f"Version: {version}\n"
        f"Lane: {lane}\n"
        "Evidence:\n"
        f"{_format_bullets(evidence_refs)}\n"
        "Verbs Run:\n"
        f"{_format_bullets(verbs_run)}\n"
        f"Outcome: {outcome}\n"
    )
    _atomic_append_release_state_event(release_state_path, block)


# ---------------------------------------------------------------------------
# In-progress resolution marker — the
# ONLY evidence a live surface has that a lane is actively executing right
# now, as opposed to merely HALTed with nothing happening. Written
# atomically immediately before the one drill/ship subprocess call a lane
# actually runs (never before that point — a marker for a lane that never
# attempts anything would itself be the "implies a resolution that did not
# run" failure this marker exists to rule out), and
# removed immediately after write_decision_event() records the outcome, in
# both the success and error-carrying branches. Both
# pgai_agent_kanban.ops.verdict (the lane verdict behind why.sh) and
# pgai_agent_kanban.dashboard.scan_attention.scan_overwatch_section read
# this same file, so why.sh and the attention window can never disagree
# about whether a lane is currently resolving.
#
# A stale marker left behind by a kill mid-drill is a legitimate,
# intentional leftover, not a bug: it is exactly the surfaced evidence a
# silent kill should leave, mirroring the ledger's own "decide-and-log"
# ordering and its documented "OVERWATCH's next sweep reconciles a
# partially-applied drill against this record" recovery story.
# ---------------------------------------------------------------------------


def _resolving_marker_path(project_root: str) -> str:
    return os.path.join(project_root, "overwatch", "resolving.md")


def write_resolving_marker(
    project_root: str, *, lane: str, attempt: int, version: str, timestamp: str
) -> None:
    """Atomically write the in-progress resolution marker, overwriting any
    prior marker. At most one resolution runs per project at a time —
    every OVERWATCH entry point that reaches this stage (the wake's
    run_overwatch_resolution_stage, team/scripts/lib/wake_project.sh) holds
    OVERWATCH's per-project firing lock (overwatch_run_with_firing_lock,
    team/scripts/lib/overwatch_lib.sh) for the stage's duration — so
    overwriting rather than refusing on an existing marker is safe.
    """
    marker_path = _resolving_marker_path(project_root)
    state_dir = os.path.dirname(marker_path)
    os.makedirs(state_dir, exist_ok=True)
    content = (
        f"lane: {lane}\n"
        f"attempt: {attempt}\n"
        f"version: {version}\n"
        f"started_at: {timestamp}\n"
    )
    atomic_write(marker_path, content, prefix=".resolving_tmp_")


def clear_resolving_marker(project_root: str) -> None:
    """Remove the in-progress marker if present. Best-effort: a missing
    marker (already cleared, or a code path that never wrote one) is not
    an error.
    """
    marker_path = _resolving_marker_path(project_root)
    try:
        os.remove(marker_path)
    except OSError:
        pass


def _park_lane_question(
    kanban_root: str,
    project_name: str,
    version: str,
    question: str,
    ship_halt_path: str,
    lane: str,
) -> tuple:
    """Write a lane's question for the operator to the inbox, once.

    Returns ``(verbs_run_line, entry_id)``: the line the Decision event's
    Verbs Run records (the entry written, the entry still open, or the
    inbox refusal) and the entry's ID, None when the write failed.
    """
    ctx = OpsContext(kanban_root=kanban_root)
    unhalt_cmd = halt_provenance.unhalt_command(kanban_root, project_name)
    entry_id, created, error = write_inbox_question(
        ctx,
        project_name,
        source=f"rc:{version}",
        question=question,
        meanwhile=(
            f"Left the HALT at {ship_halt_path} and {version} as they are and recorded an OVERWATCH "
            f"Decision (lane {lane}) in release-state.md; every other project keeps running."
        ),
        how_to_answer=(
            "Take one of the options in the question. If the HALT is still there once the cause is "
            f"fixed, resume with {unhalt_cmd}; then close this entry with "
            f"{_inbox_close_command(ctx, project_name)}."
        ),
    )
    if error is not None:
        return f"inbox write FAILED: {error}", None
    if created:
        return f"inbox entry {entry_id} written", entry_id
    return f"inbox entry {entry_id} still open (not written again)", entry_id


def _format_lane_c_read(lane_result: LaneClassification, version: str) -> str:
    """Build Lane C's one-paragraph OVERWATCH read: names the cause (why
    classification landed on the default lane) and at least one option, so
    the operator opening this block starts oriented instead of from zero.
    """
    return (
        f"OVERWATCH read: {version} is HALTed and classifies as Lane C — {lane_result.reason} "
        "Options: (1) review the cited TESTER report and its filed bugs directly and "
        "resolve manually, clearing the HALT when done; (2) if this state is deliberate, "
        "add a dated entry to this project's INTENT.md describing it in enough detail to "
        "satisfy the full-explanation match test, then re-fire OVERWATCH; or (3) if a fix "
        "landed for a genuine bad-fix regression, correct the originating bug's Fix section "
        "and let discovery re-bundle."
    )


# ---------------------------------------------------------------------------
# Lane A recovery playbook — steps (b)-(f): unwind-rc, cancel-rc,
# delete the bundle doc, clear the ship-policy HALT, and (f) no action —
# discovery re-decomposes the rewritten bug on its own clock. Step (a), the
# Fix-rewrite (pgai_agent_kanban.overwatch.fix_rewrite.
# rewrite_bug_fix_section()), is this deterministic
# stage's precondition, not its own action: _run_lane_a() below declines
# steps (b)-(e) whenever the blamed bug does not yet carry an attributed
# rewrite, rather than assume one already ran — the
# forbidden-approach/correct-direction judgment call is the reasoning
# tier's, performed out-of-band (team/scripts/overwatch-attach-fix-rewrite.sh,
# or a hand-edit carrying the same shape) against the same regression_bug_id
# before this drill is ever reached.
#
# Single-attempt guard: a Lane A execution is recorded in the shared
# auto-resolutions ledger exactly like a CANCEL-AND-REBUNDLE entry —
# matrix_row=LANE_A_MATRIX_ROW, findings=[regression_bug_id]. A SECOND HALT
# that classifies Lane A against the SAME regression_bug_id finds that
# entry already recorded and escalates to Lane C instead of running the
# drill again. "The same finding class" means, for this drill, the same
# originating bug being blamed for the regression.
# ---------------------------------------------------------------------------

LANE_A_TRIGGER = "lane-a-bad-fix-regression"
LANE_A_MATRIX_ROW = "LANE-A-RECOVERY"


def _run_unwind_rc(kanban_root: str, project_name: str, version: str) -> tuple:
    """Invoke team/scripts/unwind-rc.sh --project --key --force as a
    subprocess. --force is required for this non-interactive caller: it
    bypasses BOTH the Active-RC-version mismatch pre-flight check (this
    stage already verified Active RC == *version* before ever reaching
    Lane A — see run_resolution_stage) AND the interactive confirmation
    prompt, which would otherwise block forever with no stdin attached.
    Mirrors _run_cm_cancel_rc's contract exactly: never raises, returns
    (ok, output).
    """
    script = os.path.join(kanban_root, "scripts", "unwind-rc.sh")
    cmd = [script, "--project", project_name, "--key", version, "--force"]
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = kanban_root
    env["KANBAN_ROOT"] = kanban_root
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)
    except OSError as exc:
        return False, f"unwind-rc.sh could not be executed: {exc}"
    ok = result.returncode == 0
    output = (result.stdout or "") + (result.stderr or "")
    return ok, output


def _lane_a_unwind_step(project_name: str, version: str) -> str:
    """The action description for Lane A's unwind step — one spelling shared
    by _run_lane_a()'s intended-actions list, execute_lane_a_drill()'s
    completed/errors lists, and _run_lane_a()'s did-the-unwind-land check."""
    return f"unwind-rc.sh --project {project_name} --key {version} --force"


def _lane_a_cancel_step(project_name: str, version: str) -> str:
    """The action description for Lane A's cancel step — shared exactly as
    _lane_a_unwind_step() is."""
    return f"cancel-rc.sh --project {project_name} --key {version} --yes"


def _resolve_current_bundle_key(
    project_root: str, version: str, bundle_key: Optional[str]
) -> Optional[str]:
    """Resolve the requirements/ item key Lane A's bundle-deletion step
    should delete. unwind-rc.sh's own Step f may already have renamed the
    bundle file to "<stem>.SUPERSEDED-on-cancel-<ts>.md" (the same glob
    unwind-rc.sh itself uses to detect an already-superseded file on a
    re-run) by the time this function runs — this is a read-only directory
    listing, never a mutation, so it stays inside the "every mutation goes
    through an operator verb or the ops write layer" constraint. Falls back
    to *bundle_key* unchanged when no superseded variant is found on disk.
    Returns None when neither exists — nothing to delete.
    """
    requirements_dir = os.path.join(project_root, "requirements")
    if os.path.isdir(requirements_dir):
        for name in sorted(os.listdir(requirements_dir)):
            if (
                name.startswith(f"{version}-")
                and ".SUPERSEDED-on-cancel-" in name
                and name.endswith(".md")
            ):
                return name[: -len(".md")]
    if bundle_key and os.path.isfile(os.path.join(requirements_dir, f"{bundle_key}.md")):
        return bundle_key
    return None


def _lane_a_steps_not_run_note(
    failed_step: str,
    skipped_steps: list,
    project_root: str,
    version: str,
    bundle_key: Optional[str],
    ship_halt_path: str,
) -> str:
    """The second errors entry execute_lane_a_drill() returns when an RC
    step fails: names the step that failed, every later step that was not
    run, and the bundle document and ship-policy HALT left in place. The
    bundle document is named by its current on-disk key (unwind-rc.sh may
    already have renamed it), read-only — this never touches the file.
    """
    current_key = _resolve_current_bundle_key(project_root, version, bundle_key)
    return (
        f"later steps not run because the {failed_step} failed: "
        f"{', '.join(skipped_steps)}; bundle doc {current_key or 'none located'} "
        f"left in place; ship-policy HALT left in place ({ship_halt_path})"
    )


def execute_lane_a_drill(
    ctx: OpsContext,
    kanban_root: str,
    project_name: str,
    project_root: str,
    version: str,
    bundle_key: Optional[str],
    ship_halt_path: str,
    expected_halt: Optional[bytes] = None,
    clears: Optional[list] = None,
) -> tuple:
    """Perform Lane A recovery-playbook steps (b)-(e), in order: unwind-rc,
    cancel-rc, delete the bundle doc, clear the ship-policy HALT.

    Returns (completed, errors) — same shape as execute_drill(): completed
    lists the action descriptions that succeeded, errors the ones that
    failed or were not run.

    The drill is all-or-nothing on its two RC steps, unwind-rc then
    cancel-rc, exactly as execute_drill() is on its cancel. Deleting the
    bundle document and clearing the ship-policy HALT only make sense for
    an RC that really was unwound and cancelled; done to an RC that is
    still active, they leave it with no bundle record and nothing stopping
    it from shipping.

      - unwind-rc failed (any non-zero exit, e.g. the project lock was held
        and the script reported LOCKED): nothing after it runs — not
        cancel-rc either. cancel-rc resets the Active RC field, and once
        that field reads none this stage declines every later sweep, so
        cancelling an RC whose unwind did not complete would strand its
        task set, queue entries and bundle half-reverted with nothing left
        to drive a retry.
      - cancel-rc failed: the bundle-document deletion and the HALT clear
        do not run.

    On either failure the bundle document and the ship-policy HALT are left
    exactly where they were, so the RC stays in front of the operator and
    the chain cannot ship it. errors carries two entries: the failed step
    with the tail of the script's output, then one entry naming the later
    steps that were not run and what was left in place.

    When both RC steps succeed, the bundle-document deletion and the HALT
    clear each run best-effort and independently: a failure in one is
    recorded in errors and the other is still attempted. Step (f)
    (discovery re-decomposing) is deliberately no action here — discovery
    picks the rewritten bug up on its own clock.
    """
    completed: list = []
    errors: list = []

    ok, output = _run_unwind_rc(kanban_root, project_name, version)
    step = _lane_a_unwind_step(project_name, version)
    if not ok:
        errors.append(f"{step} FAILED: {output.strip()[-500:]}")
        errors.append(
            _lane_a_steps_not_run_note(
                "RC unwind",
                [_lane_a_cancel_step(project_name, version), "delete bundle doc", "clear ship-policy HALT"],
                project_root,
                version,
                bundle_key,
                ship_halt_path,
            )
        )
        return completed, errors
    completed.append(step)

    ok, output = _run_cm_cancel_rc(kanban_root, project_name, version)
    step = _lane_a_cancel_step(project_name, version)
    if not ok:
        errors.append(f"{step} FAILED: {output.strip()[-500:]}")
        errors.append(
            _lane_a_steps_not_run_note(
                "RC cancel",
                ["delete bundle doc", "clear ship-policy HALT"],
                project_root,
                version,
                bundle_key,
                ship_halt_path,
            )
        )
        return completed, errors
    completed.append(step)

    resolved_key = _resolve_current_bundle_key(project_root, version, bundle_key)
    if resolved_key:
        step = f"delete bundle doc: {resolved_key}"
        try:
            delete_item(ctx, project_name, resolved_key, force=True)
            completed.append(step)
        except OpsError as exc:
            errors.append(f"{step} FAILED: {exc}")
    else:
        errors.append("delete bundle doc: no bundle intake item located — skipped")

    # The same HALT-clear step, and the same re-check, execute_drill() runs.
    if ship_halt_path:
        _clear_ship_policy_halt(
            ctx, kanban_root, project_name, ship_halt_path, expected_halt, clears, completed, errors
        )
    return completed, errors


def _lane_a_prior_attempts(ledger_path: str, regression_bug_id: Optional[str]) -> list:
    """Every ledger entry already recorded for a Lane A recovery attempt on
    *regression_bug_id* — entries tagged matrix_row=LANE_A_MATRIX_ROW
    carrying findings=[regression_bug_id]. File (append) order, oldest
    first. Empty on the first attempt: no ledger yet, or no entry for this
    exact bug id.
    """
    if not regression_bug_id or not os.path.isfile(ledger_path):
        return []
    return [
        entry
        for entry in parse_entries(ledger_path)
        if entry.get("matrix_row") == LANE_A_MATRIX_ROW
        and regression_bug_id in (entry.get("findings") or [])
    ]


def _format_lane_a_escalation(
    lane_result: LaneClassification, prior_attempt: dict, version: str
) -> str:
    """Build the single-attempt guard's Lane C escalation text: names both
    attempts' evidence so the operator sees the whole story, not the
    latest slice of it.
    """
    prior_ts = prior_attempt.get("timestamp") or "unknown timestamp"
    prior_rc = prior_attempt.get("rc") or "unknown RC"
    prior_evidence = ", ".join(prior_attempt.get("evidence_paths") or []) or "none recorded"
    return (
        f"OVERWATCH read: {version} HALTed again on the same finding class as a prior Lane A "
        f"attempt — both trace to {lane_result.regression_bug_id}. Single-attempt guard: Lane A "
        f"already ran once, at {prior_ts} for {prior_rc} (evidence: {prior_evidence}); it does "
        "not run a second time on the same finding class. Escalating to Lane C with both "
        "attempts' evidence. Options: (1) review both TESTER reports and the rewritten Fix "
        f"section on {lane_result.regression_bug_id} directly, then resolve manually; (2) hand-"
        "author a corrected Fix and let discovery re-bundle; (3) if this state is deliberate, "
        "add a dated entry to this project's INTENT.md and re-fire OVERWATCH."
    )


def _format_lane_a_rewrite_missing(
    lane_result: LaneClassification,
    bug_path: Optional[str],
    bugs_dir: str,
    version: str,
    kanban_root: str,
    project_name: str,
) -> str:
    """Build the dual-channel refusal text for the rewrite-precondition
    guard (see _run_lane_a's CENSUS-ANCHOR comment): a stable machine key,
    a plain-language explanation, and a runnable remedy naming the CLI
    that actually performs step (a).

    The remedy substitutes every value this stage already resolved
    (--project, --kanban-root, --bug-id) verbatim — it names
    --forbidden-approach/--correct-direction/--ground-truth-ref only by
    flag, never with an angle-bracket placeholder, because their content is
    the reasoning tier's judgment call, not a value this deterministic
    stage could ever resolve on the caller's behalf (lint_runnable_remedy.py
    flags a literal '<...>' template slot; naming a flag without one is not
    a template — the caller still has to type real text after it).
    """
    from pgai_agent_kanban.lib.error_contract import format_error_contract

    bug_ref = bug_path or f"(no file matching {lane_result.regression_bug_id!r} found under {bugs_dir})"
    bug_id_arg = lane_result.regression_bug_id or "(unresolved — inspect the TESTER report directly)"
    return format_error_contract(
        "overwatch.lane-a-rewrite-missing",
        f"{version} classifies as Lane A (regression traced to "
        f"{lane_result.regression_bug_id}) but the blamed bug does not yet carry an "
        f"attributed OVERWATCH Fix rewrite ({bug_ref}) — the recovery drill's steps "
        "(b)-(e) do not run without it.",
        "attach the rewrite first: "
        + installed_script_command(
            kanban_root, "overwatch-attach-fix-rewrite.sh",
            "--project", project_name, "--kanban-root", kanban_root, "--bug-id", bug_id_arg,
        )
        + ", adding "
        "--forbidden-approach, --correct-direction, and one or more --ground-truth-ref flags "
        "naming the actual failed approach, the actual correct fix, and a verbatim ground-truth "
        "reference respectively (run the script with --help for each flag's contract) — or "
        "hand-append the same '## Fix (OVERWATCH rewrite)' shape directly. Then let the next "
        "OVERWATCH firing re-classify this block.",
    )


def _run_lane_a(
    ctx: OpsContext,
    kanban_root: str,
    project_name: str,
    project_root: str,
    version: str,
    lane_result: LaneClassification,
    family: dict,
    ship_halt_path: str,
    evidence_refs: list,
    timestamp: str,
    expected_halt: Optional[bytes] = None,
) -> ResolutionStageOutcome:
    """Lane A's full handling: the single-attempt guard first, then — on a
    first attempt only — the recovery drill itself. Called only from
    _run_lane_classification when lane_result.lane == LANE_A.
    """
    ledger_path = ledger_path_for_project(kanban_root, project_name)
    prior_attempts = _lane_a_prior_attempts(ledger_path, lane_result.regression_bug_id)

    if prior_attempts:
        # Single-attempt guard: escalate to Lane C rather than run the
        # drill a second time. No operator verb is invoked on this path —
        # the block is left exactly as classify_lane and the earlier
        # matrix consultation found it.
        prior = prior_attempts[-1]
        combined_evidence = list(evidence_refs)
        for path in prior.get("evidence_paths") or []:
            if path not in combined_evidence:
                combined_evidence.append(path)

        outcome_text = _format_lane_a_escalation(lane_result, prior, version)
        inbox_line, entry_id = _park_lane_question(
            kanban_root, project_name, version, outcome_text, ship_halt_path, LANE_C
        )
        write_decision_event(
            os.path.join(project_root, "release-state.md"),
            timestamp=timestamp,
            version=version,
            lane=LANE_C,
            evidence_refs=combined_evidence,
            verbs_run=[f"{inbox_line} (Lane A single-attempt guard escalation)"],
            outcome=outcome_text,
        )
        reason = f"Lane A single-attempt guard escalated to Lane C: {outcome_text}; {inbox_line}"
        log_action(kanban_root, project_name, "resolution-stage", f"{project_name}:{version}", "declined", reason)
        return ResolutionStageOutcome(
            status=DECLINED, reason=reason, decision=DECISION_INBOX, inbox_entry_id=entry_id
        )

    # Rewrite precondition: step (a) — deferred import to avoid a circular
    # top-level import (fix_rewrite.py imports log_action from this module;
    # see fix_rewrite.py's own module docstring for the full contract).
    from pgai_agent_kanban.overwatch.fix_rewrite import find_bug_file, has_attributed_rewrite

    bugs_dir = os.path.join(project_root, "bugs")
    bug_path = find_bug_file(bugs_dir, lane_result.regression_bug_id) if lane_result.regression_bug_id else None
    rewrite_present = False
    if bug_path:
        try:
            rewrite_present = has_attributed_rewrite(open(bug_path, "r", encoding="utf-8").read())
        except OSError:
            rewrite_present = False

    if not rewrite_present:
        # BLOCK-WORTHY: the destructive drill (unwind/cancel/delete-bundle/
        # clear-HALT) must never run without the reasoning tier's attributed
        # Fix rewrite already on the blamed bug — an approval-by-design
        # invariant (team/roles/OVERWATCH.md, "Performing that rewrite is
        # the reasoning tier's duty, not the drill's"), not a judgment call
        # this deterministic stage can make for itself. No ledger entry is
        # written on this path — a decline never counts as an "attempt" the
        # single-attempt guard above should later see, so the next firing
        # re-checks freely once the rewrite is attached.
        # CENSUS-ANCHOR: lane-a-rewrite-precondition-01
        outcome_text = _format_lane_a_rewrite_missing(
            lane_result, bug_path, bugs_dir, version, kanban_root, project_name
        )
        write_decision_event(
            os.path.join(project_root, "release-state.md"),
            timestamp=timestamp,
            version=version,
            lane=LANE_A,
            evidence_refs=evidence_refs,
            verbs_run=["declined pending attributed Fix rewrite (step (a) precondition unmet)"],
            outcome=outcome_text,
        )
        reason = f"Lane A declined — attributed Fix rewrite precondition unmet: {outcome_text}"
        log_action(kanban_root, project_name, "resolution-stage", f"{project_name}:{version}", "declined", reason)
        return ResolutionStageOutcome(status=DECLINED, reason=reason)

    bundle_key = None
    if family.get("bundle_file"):
        bundle_key = os.path.splitext(os.path.basename(family["bundle_file"]))[0]

    intended_actions = [
        _lane_a_unwind_step(project_name, version),
        _lane_a_cancel_step(project_name, version),
        f"delete bundle doc: {bundle_key}" if bundle_key else "delete bundle doc: none — no bundle file located",
        f"cleared ship-policy HALT: {ship_halt_path}",
    ]

    entry = LedgerEntry(
        timestamp=timestamp,
        rc=version,
        attempt_from=0,
        attempt_to=1,
        trigger=LANE_A_TRIGGER,
        matrix_row=LANE_A_MATRIX_ROW,
        findings=[lane_result.regression_bug_id] if lane_result.regression_bug_id else [],
        actions_taken=intended_actions,
        evidence_paths=evidence_refs,
        family_key=family.get("family_key"),
    )
    # Decide-and-log: the ledger entry is written BEFORE the drill's actions
    # run, naming everything the drill is about to do — same "never
    # log-after" ordering run_resolution_stage's CANCEL-AND-REBUNDLE path
    # uses (see that function's own comment on this).
    entry_id = append_entry(ledger_path, entry)

    # In-progress marker: written only now that the drill is actually about
    # to run (never earlier — the single-attempt-guard escalation branch
    # above never reaches this line, so it never writes one). Cleared right
    # after write_decision_event() below records the outcome, in both the
    # clean and errors-present branches.
    write_resolving_marker(
        project_root,
        lane=LANE_A,
        attempt=entry.attempt_to,
        version=version,
        timestamp=timestamp,
    )

    clears: list = []
    completed, errors = execute_lane_a_drill(
        ctx=ctx,
        kanban_root=kanban_root,
        project_name=project_name,
        project_root=project_root,
        version=version,
        bundle_key=bundle_key,
        ship_halt_path=ship_halt_path,
        expected_halt=expected_halt,
        clears=clears,
    )
    evidence_refs = list(evidence_refs) + [c.backup_path for c in clears if c.backup_path]

    if errors:
        outcome_text = (
            f"Lane A recovery drill executed for regression traced to "
            f"{lane_result.regression_bug_id} with {len(errors)} action(s) reporting errors: "
            + "; ".join(errors)
        )
    else:
        outcome_text = (
            f"Lane A recovery drill executed cleanly for regression traced to "
            f"{lane_result.regression_bug_id}: RC unwound and cancelled, bundle doc deleted, "
            "ship-policy HALT cleared."
        )

    write_decision_event(
        os.path.join(project_root, "release-state.md"),
        timestamp=timestamp,
        version=version,
        lane=LANE_A,
        evidence_refs=evidence_refs,
        verbs_run=completed,
        outcome=outcome_text,
    )
    clear_resolving_marker(project_root)

    reason = f"Lane A recovery drill executed; ledger entry {entry_id}"
    # The drill stops at a failed RC step. Say which one on the summary line
    # itself, so an operator reading only this line knows why the bundle
    # document and the HALT are still there.
    for rc_step, label in (
        (_lane_a_unwind_step(project_name, version), "RC unwind"),
        (_lane_a_cancel_step(project_name, version), "RC cancel"),
    ):
        if rc_step not in completed:
            reason += (
                f"; {label} FAILED, later steps not run (bundle doc and "
                "ship-policy HALT left in place)"
            )
            break
    if errors:
        reason += f"; {len(errors)} action(s) reported errors (see below)"
    log_action(
        kanban_root,
        project_name,
        "resolution-stage",
        f"{project_name}:{version}",
        "auto-fix:lane-a-recovery",
        reason,
    )
    for err in errors:
        log_action(
            kanban_root,
            project_name,
            "resolution-stage",
            f"{project_name}:{version}",
            "auto-fix-error",
            err,
        )

    return ResolutionStageOutcome(
        status=EXECUTED,
        reason=reason,
        ledger_entry_id=entry_id,
        actions_taken=completed,
        action_errors=errors,
        decision=DECISION_VERB,
    )


# ---------------------------------------------------------------------------
# Lane B force-ship playbook — the one action Lane B's own
# classification (pgai_agent_kanban.overwatch.resolution_matrix.classify_lane,
# LANE_B branch) prescribes: force cm/release.sh past the ship-policy stop it
# is resolving, with the matched INTENT.md entry's own text as --reason
# (never a generated summary of it), then clear that ship-policy HALT on
# success. No ledger entry is written here — Lane B is a one-time ship, not
# a recyclable bundle family the retry ceiling tracks, so
# pgai_agent_kanban.overwatch.ledger's schema (attempt/family bookkeeping
# built for CANCEL-AND-REBUNDLE) does not apply. The "## Operator Override"
# event cm/release.sh itself writes (team/scripts/lib/cm/
# release_operator_override.sh, _cm_apply_matrix_trigger) and the
# "## OVERWATCH Decision" event write_decision_event() appends below are
# Lane B's complete permanent record.
# ---------------------------------------------------------------------------


def _run_cm_release_force(kanban_root: str, project_name: str, reason: str) -> tuple:
    """Invoke team/scripts/cm/release.sh --project <p> --force --reason
    <reason> --overwatch-lane-b-ship as a subprocess.

    --overwatch-lane-b-ship is the discriminator release.sh's own
    operator-force-wake-context refusal narrowly admits; passing
    it here proves nothing by itself — release.sh independently
    re-verifies *reason* against the resolved project's live INTENT.md
    (its own "_cm_release_lane_b_intent_verified" helper, reading through
    this same package's pgai_agent_kanban.overwatch.intent.parse_entries)
    before honouring the override. See release.sh's
    "operator-force-wake-context-01" / "overwatch-lane-b-intent-unmatched-01"
    CENSUS-ANCHOR sites and docs/blocking-site-census.md for the full
    contract.

    No --project version argument is passed: release.sh derives the
    version to release from the project's own release-state.md "## Active
    RC" field at its own preflight, exactly as an unforced invocation does
    — this call relies on that field still naming the same RC this stage
    is resolving, which holds because nothing between the original
    ship-policy HALT and this call has touched release-state.md.

    PGAI_AGENT_WAKE_ACTIVE is deliberately NOT set here: it is inherited
    from the ambient environment exactly like _run_unwind_rc /
    _run_cm_cancel_rc's own env handling, because this stage only ever
    runs inside an OVERWATCH wake, which already carries the signal.

    Never raises; returns (ok, output).
    """
    script = os.path.join(kanban_root, "scripts", "cm", "release.sh")
    cmd = [
        script,
        "--project", project_name,
        "--force",
        "--reason", reason,
        "--overwatch-lane-b-ship",
    ]
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = kanban_root
    env["KANBAN_ROOT"] = kanban_root
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)
    except OSError as exc:
        return False, f"cm/release.sh --overwatch-lane-b-ship could not be executed: {exc}"
    ok = result.returncode == 0
    output = (result.stdout or "") + (result.stderr or "")
    return ok, output


def _run_lane_b(
    kanban_root: str,
    project_name: str,
    project_root: str,
    version: str,
    lane_result: LaneClassification,
    ship_halt_path: str,
    evidence_refs: list,
    timestamp: str,
    expected_halt: Optional[bytes] = None,
) -> ResolutionStageOutcome:
    """Lane B's full handling: force-ship through cm/release.sh, then clear
    the ship-policy HALT this stage is resolving on success. Called only
    from _run_lane_classification when lane_result.lane == LANE_B.

    No entry means no force, ever, from OVERWATCH's own judgment: this
    function is only ever reached with lane_result.intent_declaration set,
    because classify_lane() only returns LANE_B when
    find_lane_b_declaration() found a fully-explaining entry — there is no
    path here that invents a reason.
    """
    # In-progress marker: Lane B has no retry ceiling / attempt bookkeeping
    # (module docstring above — "a one-time ship, not a recyclable bundle
    # family the retry ceiling tracks"), so attempt is always 1 here. Written
    # right before the one subprocess call this playbook makes; cleared
    # right after write_decision_event() records the outcome below, in both
    # the ok and FAILED branches.
    write_resolving_marker(
        project_root, lane=LANE_B, attempt=1, version=version, timestamp=timestamp
    )

    reason_text = lane_result.intent_declaration or ""
    ok, output = _run_cm_release_force(kanban_root, project_name, reason_text)
    verb = (
        f"cm/release.sh --project {project_name} --force --reason "
        "<INTENT.md entry, verbatim> --overwatch-lane-b-ship"
    )

    if ok:
        # The ship-policy HALT this stage is resolving was written by an
        # EARLIER, unforced release.sh invocation (the one that originally
        # HALTed) — the forced invocation above never touches it, so it is
        # still on disk after a successful ship and must be cleared here.
        # Re-verify content immediately before removal — the same
        # race-closing re-check execute_drill() / execute_lane_a_drill()'s
        # own HALT-clear step performs.
        halt_clear = clear_chain_halt(
            OpsContext(kanban_root=kanban_root),
            kanban_root,
            project_name,
            step=f"cleared ship-policy HALT: {ship_halt_path}",
            expected=expected_halt,
            writer=WRITER_CM_RELEASE,
        )
        if halt_clear.cleared:
            halt_step = f"{halt_clear.step} (backup: {halt_clear.backup_path})"
        else:
            halt_step = halt_clear.error
        verbs_run = [verb, halt_step]
        outcome = (
            "classified Lane B (evidence fully explained by a declared INTENT.md entry); "
            f"forced ship succeeded — reason recorded verbatim: {reason_text!r}."
        )
        status = EXECUTED
        action_errors: list = [] if halt_clear.cleared else [halt_clear.error]
        if halt_clear.backup_path:
            evidence_refs = list(evidence_refs) + [halt_clear.backup_path]
    else:
        verbs_run = [f"{verb} FAILED"]
        outcome = (
            "classified Lane B (evidence fully explained by a declared INTENT.md entry); "
            f"forced ship FAILED: {output.strip()[-500:]}"
        )
        status = DECLINED
        action_errors = [output.strip()[-500:]]

    write_decision_event(
        os.path.join(project_root, "release-state.md"),
        timestamp=timestamp,
        version=version,
        lane=LANE_B,
        evidence_refs=evidence_refs,
        verbs_run=verbs_run,
        outcome=outcome,
    )
    clear_resolving_marker(project_root)

    reason = f"lane {LANE_B} classified: {lane_result.reason}; ship {'succeeded' if ok else 'FAILED'}"
    log_action(
        kanban_root,
        project_name,
        "resolution-stage",
        f"{project_name}:{version}",
        "auto-fix:lane-b-ship" if ok else "declined",
        reason,
    )
    return ResolutionStageOutcome(
        status=status,
        reason=reason,
        actions_taken=verbs_run if ok else [],
        action_errors=action_errors,
        decision=DECISION_VERB if ok else DECISION_NONE,
    )


def _run_lane_classification(
    ctx: OpsContext,
    kanban_root: str,
    project_name: str,
    project_root: str,
    version: str,
    evidence: BlockEvidence,
    ship_halt_path: str,
    tester_report_path: Optional[str],
    expected_halt: Optional[bytes] = None,
) -> ResolutionStageOutcome:
    """Gather Lane A/B/C evidence, classify, record the shared Decision
    event, and execute Lane C's escalation. Called from run_resolution_stage
    only when the pre-existing matrix consultation declined to HALT_HARD,
    the block is not at the retry ceiling, and overwatch_resolve is true.
    """
    findings = matrix_findings(
        parse_report(pathlib.Path(tester_report_path))
    ) if tester_report_path and os.path.isfile(tester_report_path) else []

    requirements_dir = str(ctx.requirements_dir(project_name))
    state_dir = os.path.join(project_root, "release-state")
    family = compute_bundle_family(requirements_dir, version, state_dir)
    cargo_ids = cargo_bug_ids(family.get("cargo", []))

    intent_entries = read_project_intent(kanban_root, project_name)
    intent_declarations = [entry.declaration for entry in intent_entries]

    evidence_texts = [finding.get("body") for finding in findings]
    evidence_texts += [evidence.target_version, evidence.rc_branch, evidence.base_branch]

    lane_result = classify_lane(findings, cargo_ids, intent_declarations, evidence_texts)

    intent_path = os.path.join(project_root, "INTENT.md")
    evidence_refs = [
        p for p in (ship_halt_path, tester_report_path, evidence.release_state_path, intent_path) if p
    ]
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if lane_result.lane == LANE_A:
        return _run_lane_a(
            ctx=ctx,
            kanban_root=kanban_root,
            project_name=project_name,
            project_root=project_root,
            version=version,
            lane_result=lane_result,
            family=family,
            ship_halt_path=ship_halt_path,
            evidence_refs=evidence_refs,
            timestamp=timestamp,
            expected_halt=expected_halt,
        )

    if lane_result.lane == LANE_B:
        return _run_lane_b(
            kanban_root=kanban_root,
            project_name=project_name,
            project_root=project_root,
            version=version,
            lane_result=lane_result,
            ship_halt_path=ship_halt_path,
            evidence_refs=evidence_refs,
            timestamp=timestamp,
            expected_halt=expected_halt,
        )

    outcome = _format_lane_c_read(lane_result, version)
    inbox_line, entry_id = _park_lane_question(
        kanban_root, project_name, version, outcome, ship_halt_path, lane_result.lane
    )

    write_decision_event(
        os.path.join(project_root, "release-state.md"),
        timestamp=timestamp,
        version=version,
        lane=lane_result.lane,
        evidence_refs=evidence_refs,
        verbs_run=[inbox_line],
        outcome=outcome,
    )

    reason = f"lane {lane_result.lane} classified: {lane_result.reason}; {inbox_line}"
    log_action(kanban_root, project_name, "resolution-stage", f"{project_name}:{version}", "declined", reason)
    return ResolutionStageOutcome(status=DECLINED, reason=reason, decision=DECISION_INBOX, inbox_entry_id=entry_id)


# ---------------------------------------------------------------------------
# The ship-policy path: the fact matrix, then the three lanes
# ---------------------------------------------------------------------------


def _resolve_ship_policy_halt(
    ctx: OpsContext,
    kanban_root: str,
    project_name: str,
    project_root: str,
    ship_halt_path: str,
    expected_halt: Optional[bytes],
) -> ResolutionStageOutcome:
    """The ship-policy path for a CM chain HALT: the fact matrix, then the
    three lanes when overwatch_resolve allows them. See the module
    docstring's EXECUTE / DECLINE shape. *expected_halt* is the HALT's
    bytes as the decision read them; a drill clears the HALT only while it
    still holds them.
    """
    release_state_md = os.path.join(project_root, "release-state.md")
    version = read_state_field(release_state_md, "Active RC")
    if version in ("none", ""):
        reason = "ship-policy HALT present but Active RC is none — nothing to resolve"
        log_action(kanban_root, project_name, "resolution-stage", project_name, "declined", reason)
        return ResolutionStageOutcome(status=DECLINED, reason=reason)

    cfg = _read_project_cfg(project_root)
    dev_tree_path = cfg["dev_tree_path"] or None
    branch_prefix = cfg["branch_prefix"]
    rc_branch = f"{branch_prefix}rc/{version}"
    base_branch = f"{branch_prefix}main"

    tasks_dir = os.path.join(project_root, "tasks")
    release_state_json = os.path.join(project_root, "release-state", f"{version}.json")
    tester_report_path = find_tester_report(tasks_dir, version)

    evidence = BlockEvidence(
        halt_file_path=ship_halt_path,
        tester_report_path=tester_report_path,
        release_state_path=release_state_json if os.path.isfile(release_state_json) else None,
        dev_tree_path=dev_tree_path,
        target_version=version,
        branch_prefix=branch_prefix,
        rc_branch=rc_branch,
        base_branch=base_branch,
        project_tasks_dir=tasks_dir,
        project_name=project_name,
        kanban_root=kanban_root,
    )

    try:
        result = resolve_block(evidence, kanban_root=kanban_root)
    except ResolutionMatrixError as exc:
        reason = f"resolution matrix error: {exc}"
        log_action(kanban_root, project_name, "resolution-stage", f"{project_name}:{version}", "declined", reason)
        return ResolutionStageOutcome(status=DECLINED, reason=reason)

    requirements_dir = str(ctx.requirements_dir(project_name))
    state_dir = os.path.join(project_root, "release-state")
    ledger_path = ledger_path_for_project(kanban_root, project_name)

    if result.resolution == HALT_HARD:
        if _is_ceiling_reached(result.facts):
            # The ceiling: no cancel/reversible action is taken —
            # the block is terminal. The only additions over a plain decline
            # are narration (the full attempt history in the block text) and
            # closing out the bundle family's ledger story.
            family = compute_bundle_family(requirements_dir, version, state_dir)
            reason = _format_ceiling_block_text(result.facts, family)
            _backfill_ceiling_ledger(ledger_path, family.get("family_key"))
        else:
            # An unmatched (non-ceiling) block gets a second look —
            # the three-lane classifier — but ONLY when this project has
            # opted in via overwatch_resolve. A malformed config value fails
            # closed to False rather than raising here: this call must never
            # grant new autonomous authority from a config-read error, and
            # the decline narration below already covers that path.
            try:
                overwatch_resolve = resolve_overwatch_resolve(project_name, kanban_root)
            except ResolutionMatrixError:
                overwatch_resolve = False

            if overwatch_resolve:
                return _run_lane_classification(
                    ctx=ctx,
                    kanban_root=kanban_root,
                    project_name=project_name,
                    project_root=project_root,
                    version=version,
                    evidence=evidence,
                    ship_halt_path=ship_halt_path,
                    tester_report_path=tester_report_path,
                    expected_halt=expected_halt,
                )

            reason = (
                f"no matrix row matched (trigger_class={result.facts.trigger_class}, "
                f"findings_all_filed={result.facts.findings_all_filed}, "
                f"shipped_or_tagged={result.facts.shipped_or_tagged}, "
                f"approval_gate_present={result.facts.approval_gate_present}, "
                f"operator_commits_in_lineage={result.facts.operator_commits_in_lineage}, "
                f"attempts_below_ceiling={result.facts.attempts_below_ceiling})"
            )
        log_action(kanban_root, project_name, "resolution-stage", f"{project_name}:{version}", "declined", reason)
        return ResolutionStageOutcome(status=DECLINED, reason=reason)

    # --- Matched: CANCEL-AND-REBUNDLE ---
    evidence_paths = [p for p in (ship_halt_path, tester_report_path, evidence.release_state_path) if p]
    return run_cancel_and_rebundle(
        ctx, kanban_root, project_name, project_root, version, result.facts,
        evidence_paths=evidence_paths, ship_halt_path=ship_halt_path, expected_halt=expected_halt,
    )


def run_cancel_and_rebundle(
    ctx: OpsContext,
    kanban_root: str,
    project_name: str,
    project_root: str,
    version: str,
    facts,
    *,
    evidence_paths: list,
    ship_halt_path: Optional[str] = None,
    expected_halt: Optional[bytes] = None,
) -> ResolutionStageOutcome:
    """Carry out a matched CANCEL-AND-REBUNDLE row for *version*.

    Decide-and-log: one ledger entry naming every intended action is
    appended first, then the drill runs (execute_drill), then one
    "## OVERWATCH Decision" event records what ran, and the outcome is
    narrated to the action log. *facts* is the matched BlockFacts (its
    trigger class and attempt number go in the ledger entry).
    *ship_halt_path* names the ship-policy HALT the drill clears last;
    None (a squash conflict resolved before any HALT was written) clears
    nothing. Returns EXECUTED with decision verb, whether or not the
    cancel itself succeeded -- ``actions_taken`` says what ran.
    """
    release_state_md = os.path.join(project_root, "release-state.md")
    tasks_dir = os.path.join(project_root, "tasks")
    requirements_dir = str(ctx.requirements_dir(project_name))
    state_dir = os.path.join(project_root, "release-state")
    ledger_path = ledger_path_for_project(kanban_root, project_name)
    family = compute_bundle_family(requirements_dir, version, state_dir)

    bundle_key = None
    if family.get("bundle_file"):
        bundle_key = os.path.splitext(os.path.basename(family["bundle_file"]))[0]

    bug_keys = [os.path.splitext(c)[0] for c in family.get("cargo", []) if c.startswith("BUG-")]
    rc_task_ids = find_rc_task_ids(tasks_dir, version)

    attempt_from = facts.attempt if facts.attempt is not None else 1
    attempt_to = attempt_from + 1

    actions_taken = [_cancel_rc_step(project_name, version)]
    actions_taken += [f"wontdo: {task_id}" for task_id in rc_task_ids]
    actions_taken.append(
        f"close (wont-do): {bundle_key}" if bundle_key else "close (wont-do): none — no bundle file located"
    )
    actions_taken += [f"reset (open): {bug_key}" for bug_key in bug_keys]
    if ship_halt_path:
        actions_taken.append(f"cleared ship-policy HALT: {ship_halt_path}")

    entry = LedgerEntry(
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        rc=version,
        attempt_from=attempt_from,
        attempt_to=attempt_to,
        trigger=facts.trigger_class,
        matrix_row=CANCEL_AND_REBUNDLE,
        findings=bug_keys,
        actions_taken=actions_taken,
        evidence_paths=evidence_paths,
        family_key=family.get("family_key"),
    )

    # Decide-and-log: the ledger entry is written BEFORE the drill's actions
    # run ("never log-after"). A kill between this call and the actions
    # below leaves the entry present with its intended actions already
    # recorded — OVERWATCH's next sweep reconciles.
    entry_id = append_entry(ledger_path, entry)

    clears: list = []
    completed, errors = execute_drill(
        ctx=ctx,
        kanban_root=kanban_root,
        project_name=project_name,
        version=version,
        rc_task_ids=rc_task_ids,
        bundle_key=bundle_key,
        bug_keys=bug_keys,
        ship_halt_path=ship_halt_path,
        expected_halt=expected_halt,
        clears=clears,
    )
    # The drill's Decision event: what it ran, and the HALT backup when it
    # cleared the HALT.
    write_decision_event(
        release_state_md,
        timestamp=entry.timestamp,
        version=version,
        lane=CANCEL_AND_REBUNDLE,
        evidence_refs=evidence_paths + [c.backup_path for c in clears if c.backup_path],
        verbs_run=completed,
        outcome=(
            f"CANCEL-AND-REBUNDLE drill ran for {version} (ledger entry {entry_id}); "
            + (f"{len(errors)} action(s) reported errors: " + "; ".join(errors) if errors else "every step completed.")
        ),
    )

    reason = f"CANCEL-AND-REBUNDLE matched; ledger entry {entry_id}"
    if _cancel_rc_step(project_name, version) not in completed:
        # The drill stopped at the cancel. Say so on the summary line itself
        # so an operator reading only this line knows why nothing reverted.
        reason += (
            "; RC cancel FAILED, status-revert steps not run (RC task set, "
            "bundle and cargo bug statuses unchanged"
            + ("; ship-policy HALT left in place)" if ship_halt_path else ")")
        )
    if errors:
        reason += f"; {len(errors)} action(s) reported errors (see below)"
    log_action(
        kanban_root,
        project_name,
        "resolution-stage",
        f"{project_name}:{version}",
        "auto-fix:cancel-and-rebundle",
        reason,
    )
    for err in errors:
        log_action(
            kanban_root,
            project_name,
            "resolution-stage",
            f"{project_name}:{version}",
            "auto-fix-error",
            err,
        )

    return ResolutionStageOutcome(
        status=EXECUTED,
        reason=reason,
        ledger_entry_id=entry_id,
        actions_taken=completed,
        action_errors=errors,
        decision=DECISION_VERB,
    )


def cancel_landed(outcome: ResolutionStageOutcome, project_name: str, version: str) -> bool:
    """Whether a run_cancel_and_rebundle() outcome's cancel step succeeded."""
    return outcome.status == EXECUTED and _cancel_rc_step(project_name, version) in outcome.actions_taken


def resolve_squash_conflict(
    project_name: str,
    kanban_root: str,
    version: str,
    *,
    repo_root: Optional[str] = None,
) -> ResolutionStageOutcome:
    """Ask the matrix whether *version*'s RC may be cancelled and rebundled
    after its squash into main conflicted, and carry the answer out.

    The block is described to the matrix with trigger class
    ``squash-conflict`` and the same evidence a ship-policy HALT gets (the
    TESTER report, the per-RC release-state JSON, the dev tree's RC and
    main branches, the task graph, the retry ceiling). A matched
    CANCEL-AND-REBUNDLE row runs run_cancel_and_rebundle() with no HALT to
    clear; anything else -- no row matched (an approval gate, the retry
    ceiling, a tag already cut), or evidence the matrix could not read --
    is DECLINED with the reason, having changed nothing, so the caller
    writes its trigger 5 HALT instead. *repo_root* names the dev tree when
    the caller knows it (cm/release.sh's REPO_ROOT); project.cfg's
    dev_tree_path otherwise.
    """
    ctx = OpsContext(kanban_root=kanban_root)
    project_root = str(ctx.project_root(project_name))
    cfg = _read_project_cfg(project_root)
    dev_tree_path = repo_root or cfg["dev_tree_path"] or None
    branch_prefix = cfg["branch_prefix"]
    tasks_dir = os.path.join(project_root, "tasks")
    release_state_json = os.path.join(project_root, "release-state", f"{version}.json")
    tester_report_path = find_tester_report(tasks_dir, version)
    target = f"{project_name}:{version}"
    evidence = BlockEvidence(
        trigger_class=TRIGGER_CLASS_SQUASH_CONFLICT,
        tester_report_path=tester_report_path,
        release_state_path=release_state_json if os.path.isfile(release_state_json) else None,
        dev_tree_path=dev_tree_path,
        target_version=version,
        branch_prefix=branch_prefix,
        rc_branch=f"{branch_prefix}rc/{version}",
        base_branch=f"{branch_prefix}main",
        project_tasks_dir=tasks_dir,
        project_name=project_name,
        kanban_root=kanban_root,
    )
    try:
        result = resolve_block(evidence, kanban_root=kanban_root)
    except ResolutionMatrixError as exc:
        return _decline(kanban_root, project_name, target, f"squash conflict: resolution matrix error: {exc}")
    if result.resolution == HALT_HARD:
        facts = result.facts
        if _is_ceiling_reached(facts):
            family = compute_bundle_family(
                str(ctx.requirements_dir(project_name)), version, os.path.join(project_root, "release-state")
            )
            why = _format_ceiling_block_text(facts, family)
        else:
            why = (
                f"no matrix row matched (trigger_class={facts.trigger_class}, "
                f"shipped_or_tagged={facts.shipped_or_tagged}, "
                f"approval_gate_present={facts.approval_gate_present}, "
                f"attempts_below_ceiling={facts.attempts_below_ceiling})"
            )
        return _decline(kanban_root, project_name, target, f"squash conflict: {why}")
    evidence_paths = [p for p in (tester_report_path, evidence.release_state_path) if p]
    return run_cancel_and_rebundle(
        ctx, kanban_root, project_name, project_root, version, result.facts, evidence_paths=evidence_paths,
    )


# ---------------------------------------------------------------------------
# Chain HALTs: clear once the cause is resolved, by writer
# ---------------------------------------------------------------------------


def _decline(kanban_root: str, project_name: str, target: str, reason: str) -> ResolutionStageOutcome:
    """Narrate a decision-none decline to the action log and return it."""
    log_action(kanban_root, project_name, "resolution-stage", target, "declined", reason)
    return ResolutionStageOutcome(status=DECLINED, reason=reason, decision=DECISION_NONE)


def _record_decision(project_root: str, **event) -> bool:
    """Append a Decision event to the project's release-state.md when it exists.

    A project with no release-state.md gets none: creating one here would
    leave a file without the headings its loader requires. Returns whether
    the event was written.
    """
    release_state_md = os.path.join(project_root, "release-state.md")
    if not os.path.isfile(release_state_md):
        return False
    write_decision_event(release_state_md, **event)
    return True


def _rc_branch_and_tree(project_root: str, version: str) -> tuple:
    """``(dev_tree_path, rc_branch)`` for *version* from project.cfg; dev tree None when unset."""
    cfg = _read_project_cfg(project_root)
    return cfg["dev_tree_path"] or None, f"{cfg['branch_prefix']}rc/{version}"


def find_latest_tester_task(tasks_dir: str, version: str) -> Optional[str]:
    """The newest TESTER-* task whose README names *version*, in any state; None when none does."""
    if not version or not os.path.isdir(tasks_dir):
        return None
    found = []
    for entry in sorted(os.listdir(tasks_dir)):
        readme = os.path.join(tasks_dir, entry, "README.md")
        if not entry.startswith("TESTER-") or not os.path.isfile(readme):
            continue
        try:
            text = open(readme, "r", encoding="utf-8").read()
        except OSError:
            continue
        if references_version(text, version):
            found.append(entry)
    # Task IDs embed YYYYMMDD-NNN; lexical order is chronological order.
    return found[-1] if found else None


def run_cause_checks(
    row: TriggerRow,
    kanban_root: str,
    project_name: str,
    project_root: str,
    halt_path: str,
    halt_text: str,
) -> list:
    """Run *row*'s clears_on checks for the project's CM HALT; one CauseCheck each."""
    active_rc = read_state_field(os.path.join(project_root, "release-state.md"), "Active RC").strip()
    version = "" if active_rc in ("", "none") else active_rc
    results = []
    for name in row.clears_on:
        if name == CLEAR_RC_CANCELLED:
            results.append(check_rc_cancelled(project_root, halt_written_at(halt_path, halt_text)))
        elif name == CLEAR_TASK_RESET:
            task_id = find_latest_tester_task(os.path.join(project_root, "tasks"), version)
            results.append(check_task_reset(project_root, task_id))
        elif name == CLEAR_NOTES_STATUS_GREEN:
            dev_tree, rc_branch = _rc_branch_and_tree(project_root, version)
            results.append(check_notes_status_green(version, dev_tree, rc_branch if version else None))
    return results


def _clear_resolved_halt(
    ctx: OpsContext,
    kanban_root: str,
    project_name: str,
    project_root: str,
    provenance: HaltProvenance,
    expected: bytes,
    label: str,
    checks: list,
) -> ResolutionStageOutcome:
    """Clear a chain HALT whose cause a check proved resolved, and record why."""
    halt_path = provenance.path
    timestamp = _utc_stamp()
    version = read_state_field(os.path.join(project_root, "release-state.md"), "Active RC").strip() or "none"
    result = clear_chain_halt(
        ctx,
        kanban_root,
        project_name,
        step=f"ops.write unhalt --project {project_name}: cleared {label} HALT {halt_path}",
        expected=expected,
        writer=provenance.writer,
        timestamp=timestamp,
    )
    passed = [check for check in checks if check.passed]
    evidence = [halt_path] + [f"{check.check}: {check.evidence}" for check in checks]
    for check in checks:
        evidence += [path for path in check.paths if path not in evidence]
    if result.backup_path:
        evidence.append(result.backup_path)
    cause = "; ".join(f"{check.check}: {check.evidence}" for check in passed)
    if result.cleared:
        outcome = f"{label} HALT cleared, its cause resolved ({cause}); backup at {result.backup_path}."
        verbs = [f"{result.step} (backup: {result.backup_path})"]
    else:
        outcome = f"{label} HALT not cleared although its cause is resolved ({cause}): {result.error}"
        verbs = [result.error]
    _record_decision(
        project_root,
        timestamp=timestamp,
        version=version,
        lane=LANE_CHAIN_HALT,
        evidence_refs=evidence,
        verbs_run=verbs,
        outcome=outcome,
    )
    log_action(
        kanban_root,
        project_name,
        "resolution-stage",
        project_name,
        "auto-fix:clear-chain-halt" if result.cleared else "declined",
        outcome,
        backup_path=result.backup_path or "none",
    )
    if result.cleared:
        return ResolutionStageOutcome(
            status=EXECUTED, reason=outcome, actions_taken=verbs, decision=DECISION_VERB
        )
    return ResolutionStageOutcome(
        status=DECLINED, reason=outcome, action_errors=[result.error], decision=DECISION_NONE
    )


def _resolve_halt_after(
    ctx: OpsContext,
    kanban_root: str,
    project_name: str,
    project_root: str,
    provenance: HaltProvenance,
    expected: bytes,
) -> ResolutionStageOutcome:
    """A HALT the HALT-AFTER promotion wrote: clear it only for a completed
    drain with ``[overwatch] halt_after_auto_resume = true``, leaving an
    inbox entry that names the release it stopped after."""
    halt_path = provenance.path
    if os.path.exists(os.path.join(project_root, "HALT-AFTER")):
        return _decline(
            kanban_root, project_name, project_name,
            f"HALT-AFTER promotion HALT at {halt_path} is not complete (HALT-AFTER is still present) "
            "— decision none",
        )
    try:
        auto_resume = resolve_halt_after_auto_resume(kanban_root, project_name)
    except RunawayError as exc:
        return _decline(kanban_root, project_name, project_name, f"HALT-AFTER HALT left in place — {exc}")
    if not auto_resume:
        return _decline(
            kanban_root, project_name, project_name,
            f"HALT-AFTER drain completed ({provenance.reason or 'no reason recorded'}); "
            "[overwatch] halt_after_auto_resume is false, so the HALT stays for the operator — decision none",
        )
    release_state_md = os.path.join(project_root, "release-state.md")
    version = halt_after_version(provenance.reason, release_state_md)
    if version is None:
        return _decline(
            kanban_root, project_name, project_name,
            f"HALT-AFTER drain completed but no release can be named for it (no rc:vX.Y.Z token in "
            f"{halt_path}, no Last Released value in {release_state_md}) — HALT left in place, decision none",
        )

    timestamp = _utc_stamp()
    result = clear_chain_halt(
        ctx,
        kanban_root,
        project_name,
        step=f"ops.write unhalt --project {project_name}: cleared HALT-AFTER HALT {halt_path}",
        expected=expected,
        writer=WRITER_HALT_AFTER_PROMOTION,
        timestamp=timestamp,
    )
    evidence = [halt_path, f"halt-reason: {provenance.reason or 'none recorded'}", release_state_md]
    if not result.cleared:
        outcome = f"HALT-AFTER HALT not cleared: {result.error}"
        _record_decision(
            project_root, timestamp=timestamp, version=version, lane=LANE_CHAIN_HALT,
            evidence_refs=evidence, verbs_run=[result.error], outcome=outcome,
        )
        log_action(kanban_root, project_name, "resolution-stage", project_name, "declined", outcome)
        return ResolutionStageOutcome(
            status=DECLINED, reason=outcome, action_errors=[result.error], decision=DECISION_NONE
        )

    evidence.append(result.backup_path)
    halt_after_cmd = _halt_after_command(kanban_root, project_name)
    entry_id, created, error = write_inbox_question(
        ctx,
        project_name,
        source=f"rc:{version}",
        question=halt_after_resume_text(version),
        meanwhile=(
            f"Cleared the HALT the HALT-AFTER promotion wrote at {halt_path} because [overwatch] "
            f"halt_after_auto_resume = true (backup: {result.backup_path}); the chain runs again."
        ),
        how_to_answer=(
            f"Nothing to decide: the chain is running. To stop it after a later release, arm "
            f"{halt_after_cmd} again; close this entry with {_inbox_close_command(ctx, project_name)}."
        ),
    )
    verbs = [f"{result.step} (backup: {result.backup_path})"]
    errors = []
    if error is not None:
        verbs.append(f"inbox write FAILED: {error}")
        errors.append(error)
    else:
        verbs.append(f"inbox entry {entry_id} {'written' if created else 'still open (not written again)'}")
    outcome = (
        f"HALT-AFTER drain completed after {version}; halt_after_auto_resume = true, so the HALT was "
        f"cleared and the inbox says: {halt_after_resume_text(version)}"
    )
    _record_decision(
        project_root, timestamp=timestamp, version=version, lane=LANE_CHAIN_HALT,
        evidence_refs=evidence, verbs_run=verbs, outcome=outcome,
    )
    log_action(
        kanban_root, project_name, "resolution-stage", project_name, "auto-fix:clear-chain-halt",
        outcome, backup_path=result.backup_path,
    )
    return ResolutionStageOutcome(
        status=EXECUTED, reason=outcome, actions_taken=verbs, action_errors=errors,
        decision=DECISION_VERB, inbox_entry_id=entry_id,
    )


def _resolve_cm_halt(
    ctx: OpsContext,
    kanban_root: str,
    project_name: str,
    project_root: str,
    provenance: HaltProvenance,
    expected: bytes,
) -> ResolutionStageOutcome:
    """A HALT CM wrote: its trigger row's checks first, then its while_open action."""
    halt_path = provenance.path
    try:
        rows = load_trigger_rows(kanban_root=kanban_root)
    except ResolutionMatrixError as exc:
        return _decline(kanban_root, project_name, project_name, f"resolution matrix error: {exc}")
    row = rows.get(provenance.trigger) if provenance.trigger else None

    checks: list = []
    if row is not None and row.clears_on:
        halt_text = expected.decode("utf-8", errors="replace")
        checks = run_cause_checks(row, kanban_root, project_name, project_root, halt_path, halt_text)
        if any(check.passed for check in checks):
            return _clear_resolved_halt(
                ctx, kanban_root, project_name, project_root, provenance, expected,
                f"CM trigger {row.trigger} ({row.name})", checks,
            )

    if row is None or row.while_open == WHILE_OPEN_MATRIX:
        return _resolve_ship_policy_halt(ctx, kanban_root, project_name, project_root, halt_path, expected)

    unresolved = "; ".join(f"{check.check}: {check.evidence}" for check in checks)
    return _decline(
        kanban_root, project_name, project_name,
        f"CM trigger {row.trigger} ({row.name}) HALT at {halt_path}: "
        + (f"cause not resolved ({unresolved})" if checks else "no cause check clears this trigger")
        + " — decision none; it waits for the operator",
    )


def _resolve_chain_halt(
    ctx: OpsContext,
    kanban_root: str,
    project_name: str,
    project_root: str,
    provenance: HaltProvenance,
    expected: bytes,
) -> ResolutionStageOutcome:
    """Dispatch a project chain HALT on the writer its provenance names."""
    halt_path = provenance.path
    writer = provenance.writer
    why = provenance.reason or "no reason recorded"
    if writer == WRITER_CM_RELEASE:
        return _resolve_cm_halt(ctx, kanban_root, project_name, project_root, provenance, expected)
    if writer == WRITER_HALT_AFTER_PROMOTION:
        return _resolve_halt_after(ctx, kanban_root, project_name, project_root, provenance, expected)
    if writer == WRITER_OVERWATCH_RUNAWAY:
        return _decline(
            kanban_root, project_name, project_name,
            f"runaway HALT at {halt_path} ({why}) — decision none: the resolution stage never clears a "
            "runaway HALT; it stays until the operator answers its inbox entry",
        )
    if writer == WRITER_ZERO_PROGRESS_CAP:
        check = check_task_reset(project_root, task_id_in(provenance.reason))
        if check.passed:
            return _clear_resolved_halt(
                ctx, kanban_root, project_name, project_root, provenance, expected, "zero-progress-cap", [check]
            )
        return _decline(
            kanban_root, project_name, project_name,
            f"zero-progress-cap HALT at {halt_path}: cause not resolved ({check.evidence}) — decision none",
        )
    if writer == WRITER_OVERWATCH_REPAIR:
        return _decline(
            kanban_root, project_name, project_name,
            f"OVERWATCH repair HALT at {halt_path} ({why}) — decision none: the repair that wrote it clears it",
        )
    return _decline(
        kanban_root, project_name, project_name,
        f"chain HALT at {halt_path} names writer {writer}, which this stage has no rule for — decision none",
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_resolution_stage(project_name: str, kanban_root: str) -> ResolutionStageOutcome:
    """Decide on the project's HALT state and act on the decision. Never
    touches an operator HALT. See the module docstring for the full
    decision shape.
    """
    ctx = OpsContext(kanban_root=kanban_root)
    project_root = str(ctx.project_root(project_name))

    project_halt, global_halt = _locate_halt_files(kanban_root, project_name)
    # The bytes are read before the provenance, so a HALT replaced in between
    # never matches what a clear later requires.
    expected = read_halt_bytes(project_halt)
    project_provenance = classify_halt(project_halt, kanban_root=kanban_root)
    global_provenance = classify_halt(global_halt, kanban_root=kanban_root)

    if project_provenance.origin == halt_provenance.ORIGIN_ABSENT and global_provenance.origin == halt_provenance.ORIGIN_ABSENT:
        # No HALT at all — nothing to decide; stay silent (every OVERWATCH
        # firing reaches this call, and logging a decline for each one with
        # no HALT would drown the action log in noise).
        return ResolutionStageOutcome(status=NO_OP, reason="no HALT file present")

    operator_halts = [
        p.path for p in (project_provenance, global_provenance) if p.origin == halt_provenance.ORIGIN_OPERATOR
    ]
    if not project_provenance.is_chain:
        # The ordinary "operator halted the chain" case. OVERWATCH never
        # reads an operator HALT as anything else and never touches it.
        return _decline(
            kanban_root, project_name, project_name,
            f"operator HALT present ({', '.join(operator_halts)}) — not a ship-policy or other chain "
            "HALT; decision none: OVERWATCH never clears an operator HALT",
        )
    if operator_halts:
        # An operator HALT beside a chain HALT is inviolate, and so is the
        # chain HALT while it is there: decline before any check or matrix.
        kind = "ship-policy" if project_provenance.writer == WRITER_CM_RELEASE else "chain"
        return _decline(
            kanban_root, project_name, project_name,
            f"operator-authored HALT present alongside the {kind} HALT ({', '.join(operator_halts)}) "
            "— decision none; declining",
        )
    if expected is None:
        return _decline(kanban_root, project_name, project_name, f"the HALT at {project_halt} could not be read — decision none")
    return _resolve_chain_halt(ctx, kanban_root, project_name, project_root, project_provenance, expected)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    """Run the resolution stage once for ``--project`` under ``--kanban-root``.

    Prints ``resolution-stage: status=<status> reason=<reason>`` and any action
    errors on stderr. Exit codes: 0 whatever the stage decided (the outcome is
    in the printed status); 2 usage error (argparse).
    """
    parser = argparse.ArgumentParser(
        description=(
            "OVERWATCH's autonomous block-resolution execution stage. "
            "Evaluates the current HALT/block state for a project and either "
            "executes the matrix-prescribed cancel-and-rebundle drill or "
            "declines, narrating the decision to the OVERWATCH action log."
        )
    )
    parser.add_argument("--project", required=True, help="Project name as registered in projects.cfg.")
    parser.add_argument("--kanban-root", required=True, help="Kanban root path.")
    args = parser.parse_args()

    outcome = run_resolution_stage(args.project, args.kanban_root)
    sys.stdout.write(f"resolution-stage: status={outcome.status} reason={outcome.reason}\n")
    if outcome.action_errors:
        for err in outcome.action_errors:
            sys.stderr.write(f"resolution-stage: action error: {err}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
