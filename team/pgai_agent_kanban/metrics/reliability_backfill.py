"""
reliability_backfill.py — rebuild a project's block-ledger records, with
class, cause and reason_code, from the evidence already on disk.

The block ledger (pgai_agent_kanban.metrics.blocked_ledger) only learned a
block's class (did a human have to clear it) and cause (did the chain or an
operator raise it) after most of a project's history was written, and its
older lines leave many task blocks open forever. This module reads the
evidence that history left and writes the ledger records it supports to a
sidecar file for the operator to review; ``apply`` then appends the records
the ledger does not already have.

Files
-----
  <root>/projects/<P>/metrics/blocked-intervals.jsonl            the ledger (read; apply appends)
  <root>/projects/<P>/metrics/blocked-intervals.backfill.jsonl   the sidecar (SIDECAR_FILENAME)

Evidence read (never written)
-----------------------------
  - the ledger itself: every block it records, open or closed;
  - triage evidence, ``artifacts/block-evidence/triage-*.json``, in every task
    folder of the project, live (``projects/<P>/tasks/``) and archived
    (``archive/<P>/tasks/``);
  - each of those task folders' status.md: ``## State`` and ``## Block Record``;
  - ``logs/sentinel-writes.log``, through
    pgai_agent_kanban.lib.sentinel_provenance.sentinel_cause;
  - the per-RC rollups ``metrics/rc/*.json`` (``opened_at``, ``closed_at``),
    and release-state.md's ``## Active RC`` / ``## RC Opened At`` for the RC
    still in flight.

How evidence maps to a record
-----------------------------
  cause        A field the ledger line already carries wins. A task block is
               ``chain``. A HALT block is what the sentinel-writes log says of
               that HALT file at the block's start; the fleet HALT under the
               kanban root is always ``operator``.
  reason_code  The line's own, else the task's triage evidence or Block
               Record, else ``halt_project`` / ``halt_global`` for a HALT.
  escalation   A triage file with outcome ``escalate`` inside a block adds a
               block-escalate at its ``recorded_at`` (class human).
  end          A block the ledger leaves open is closed by, in order: a triage
               file with outcome ``act`` inside it (class any, reason_code
               ``requeue``, at its ``recorded_at``); otherwise, unless the
               task is BLOCKED now, the close of the block's RC — the rollup's
               ``closed_at``, or for an RC with no rollup the ``opened_at`` of
               the next RC (reason_code ``rc_closed``). An RC that has not
               closed leaves the block open.
  class        A task block closed by that RC bound is class human: a BLOCKED
               task returns to work only through triage (which leaves an
               ``act`` file) or a human. A HALT block takes the class of the
               task blocks of the same RC whose intervals overlap or touch it
               (human if any is human); with none, the ledger default ``any``.
  new blocks   Triage evidence, or the Block Record of a task BLOCKED now,
               that no ledger block of that task covers becomes a new block,
               starting at the evidence's block epoch (the Block Record's
               ``last_blocked_at``) under the RC whose window holds it.

The sidecar
-----------
One JSON object per line: a ledger record (version, kind, source,
timestamp, and class / cause / reason_code where known) plus a ``backfill``
object: ``block`` (the block the line belongs to, numbered in start order),
``block_class`` (the class the block reads as by the ledger's own rule),
``in_ledger`` (the ledger already has a line of this kind, version, source
and time) and ``basis`` (the evidence the line rests on). Stripped of
``backfill``, the sidecar is itself a complete, well-paired ledger.

A ledger line is never rewritten, so a rebuilt field the ledger lacks on a
line it already has (a closed HALT interval's class, say) shows in the
sidecar for review and stays out of the ledger.

Apply
-----
``apply`` reads the sidecar, not the evidence. It appends, through the
ledger's own writers, every record whose line the ledger does not have:
first the escalate and end lines of blocks the ledger already opened (only
while the ledger's open block of that source is that same block), then
whole new blocks (a block whose start the ledger refuses is skipped whole).
It never rewrites or deletes a line, then rewrites metrics/reliability.json
(best-effort). Run again, it appends nothing.

Refusals (dual-channel, exit 2, nothing written): an unknown project, a
malformed ledger, ``apply`` with no sidecar, and a malformed sidecar.

Usage (CLI; team/scripts/reliability-backfill.sh calls it):
    python3 -m pgai_agent_kanban.metrics.reliability_backfill --project NAME [--kanban-root DIR] [--apply]

Exit codes: 0 done; 2 refused, nothing written.

Usage (import):
    from pgai_agent_kanban.metrics.reliability_backfill import (
        rebuild, write_sidecar, apply_sidecar, BackfillRefused,
    )
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import os
import pathlib
import sys
from collections.abc import Callable

# Run by file path, this module has no parent package and only its own
# directory is on sys.path. Put this file's own package root -- the
# directory holding the pgai_agent_kanban/ this file sits in -- first, so
# the imports below resolve from the same tree, never another. Imported as
# a package member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.archive.layout import archive_layout, display_relpath, live_layout  # noqa: E402
from pgai_agent_kanban.cm.read_state_field import read_state_field  # noqa: E402
from pgai_agent_kanban.lib import semver  # noqa: E402
from pgai_agent_kanban.lib.atomic_write import atomic_write  # noqa: E402
from pgai_agent_kanban.lib.error_contract import emit_error_contract  # noqa: E402
from pgai_agent_kanban.lib.sentinel_provenance import sentinel_cause  # noqa: E402
from pgai_agent_kanban.lib.timeutil import format_iso, try_parse_iso  # noqa: E402
from pgai_agent_kanban.metrics.blocked_ledger import (  # noqa: E402
    CAUSE_CHAIN,
    CAUSE_OPERATOR,
    CLASS_ANY,
    CLASS_HUMAN,
    KIND_END,
    KIND_ESCALATE,
    KIND_START,
    LedgerError,
    NO_RC_VERSION,
    VALID_CAUSES,
    VALID_CLASSES,
    VALID_KINDS,
    append_block_end,
    append_block_start,
    block_intervals,
    ledger_path_for_project,
    mark_block_escalated,
    parse_records,
    record_class,
    record_reason_code,
)
from pgai_agent_kanban.metrics.reliability import write_reliability  # noqa: E402
from pgai_agent_kanban.overwatch.block_record import read_block_record  # noqa: E402
from pgai_agent_kanban.overwatch.block_triage import (  # noqa: E402
    EVIDENCE_DIR,
    OUTCOME_ACT,
    OUTCOME_ESCALATE,
    REQUEUE_LEDGER_REASON_CODE,
)
from pgai_agent_kanban.task_status import section_value  # noqa: E402

#: The sidecar's file name, beside the ledger in <project>/metrics/.
SIDECAR_FILENAME = "blocked-intervals.backfill.jsonl"

#: reason_code of a block-end placed at the close of the block's RC.
RC_CLOSED_REASON_CODE = "rc_closed"

#: The sidecar's per-line review annotation; never written to the ledger.
ANNOTATION_KEY = "backfill"

EXIT_OK = 0
EXIT_REFUSED = 2

_TASK_PREFIX = "task:"
_HALT_PROJECT = "halt:project"
_HALT_GLOBAL = "halt:global"
_TRIAGE_GLOB = "triage-*.json"
_STATE_BLOCKED = "BLOCKED"
#: The operator script, in this module's own tree (team/scripts/ in a
#: repository, scripts/ in an install): what a remedy tells the reader to run.
_SCRIPT = str(pathlib.Path(__file__).resolve().parents[2] / "scripts" / "reliability-backfill.sh")

_Timestamp = datetime.datetime


class BackfillRefused(Exception):
    """A refusal: nothing was written. Carries the dual-channel fields, and
    whether the named project exists to record the refusal against."""

    def __init__(self, error: str, message: str, remedy: str, *, project_exists: bool = True):
        super().__init__(message)
        self.error = error
        self.message = message
        self.remedy = remedy
        self.project_exists = project_exists


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class RcWindow:
    """One per-RC rollup: its version and when it opened and closed."""

    version: str
    key: tuple
    opened_at: _Timestamp | None
    closed_at: _Timestamp | None


@dataclasses.dataclass(frozen=True)
class Triage:
    """One triage evidence file."""

    task_id: str
    recorded_at: _Timestamp
    outcome: str
    reason_code: str | None
    epoch: _Timestamp | None
    path: str


@dataclasses.dataclass(frozen=True)
class TaskStatus:
    """One task folder's status.md: its state and Block Record, if any."""

    task_id: str
    state: str
    reason_code: str | None
    last_blocked_at: _Timestamp | None
    path: str


@dataclasses.dataclass
class Evidence:
    """Everything the rebuild reads besides the ledger."""

    rc_windows: list
    active_rc: str | None
    active_opened_at: _Timestamp | None
    triage: list
    statuses: dict
    halt_cause: Callable[[str, _Timestamp], str]


def _parse_ts(value: object) -> _Timestamp | None:
    if value is None:
        return None
    return try_parse_iso(str(value).strip())


def _code(value: object) -> str | None:
    """A reason code token, or None for blank / ``none``."""
    text = " ".join(str(value or "").split())
    return text if text and text.lower() != "none" else None


def load_rc_windows(rc_dir: str | os.PathLike) -> list:
    """Every rollup under *rc_dir* with a semver version, ordered by version.
    A rollup that does not parse is skipped; an absent directory is none."""
    rc_dir = os.fspath(rc_dir)
    if not os.path.isdir(rc_dir):
        return []
    by_key: dict = {}
    for name in sorted(os.listdir(rc_dir)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(rc_dir, name), encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        version = str(payload.get("rc") or "").strip() or semver.from_filename(name)
        key = semver.parse_version(version)
        if key is None:
            continue
        by_key[key] = RcWindow(version, key, _parse_ts(payload.get("opened_at")), _parse_ts(payload.get("closed_at")))
    return [by_key[key] for key in sorted(by_key)]


def _task_dirs(kanban_root: str, project: str) -> list:
    """Every task folder of *project*, live first, then archived."""
    dirs = []
    for layout in (live_layout(kanban_root, project), archive_layout(kanban_root, project)):
        tasks = pathlib.Path(layout.tasks)
        if not tasks.is_dir():
            continue
        for child in sorted(tasks.iterdir()):
            if child.is_dir() and ((child / "status.md").is_file() or (child / EVIDENCE_DIR).is_dir()):
                dirs.append(child)
    return dirs


def load_triage(task_dirs: list, kanban_root: str | None = None) -> list:
    """Every parseable triage evidence file under *task_dirs*, by time. Each
    file's path is kept relative to *kanban_root* when given."""
    found = []
    for task_dir in task_dirs:
        evidence_dir = pathlib.Path(task_dir) / EVIDENCE_DIR
        if not evidence_dir.is_dir():
            continue
        for path in sorted(evidence_dir.glob(_TRIAGE_GLOB)):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            recorded_at = _parse_ts(payload.get("recorded_at"))
            outcome = str(payload.get("outcome") or "").strip().lower()
            if recorded_at is None or not outcome:
                continue
            record = payload.get("block_record")
            record_code = record.get("reason_code") if isinstance(record, dict) else None
            found.append(
                Triage(
                    task_id=str(payload.get("task_id") or pathlib.Path(task_dir).name).strip(),
                    recorded_at=recorded_at,
                    outcome=outcome,
                    reason_code=_code(payload.get("reason_code")) or _code(record_code),
                    epoch=_parse_ts(payload.get("block_epoch")),
                    path=display_relpath(kanban_root, path) if kanban_root else str(path),
                )
            )
    return sorted(found, key=lambda t: (t.recorded_at, t.path))


def load_statuses(task_dirs: list, kanban_root: str | None = None) -> dict:
    """``{task_id: TaskStatus}`` for every readable status.md; a live folder
    wins over an archived one of the same name. Paths as in load_triage()."""
    statuses: dict = {}
    for task_dir in task_dirs:
        path = pathlib.Path(task_dir) / "status.md"
        task_id = pathlib.Path(task_dir).name
        if task_id in statuses:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            record = read_block_record(text, path)
        except Exception:  # noqa: BLE001 - an unreadable record is no evidence
            record = None
        state = section_value(text, "State").split()
        statuses[task_id] = TaskStatus(
            task_id=task_id,
            state=state[0].upper() if state else "",
            reason_code=_code(record.reason_code) if record is not None else None,
            last_blocked_at=_parse_ts(record.last_blocked_at) if record is not None else None,
            path=display_relpath(kanban_root, path) if kanban_root else str(path),
        )
    return statuses


def load_evidence(kanban_root: str, project: str) -> Evidence:
    """Read every evidence source for *project* under *kanban_root*."""
    project_dir = os.path.join(kanban_root, "projects", project)
    task_dirs = _task_dirs(kanban_root, project)
    release_state = os.path.join(project_dir, "release-state.md")
    active_rc = read_state_field(release_state, "Active RC").strip()
    active_opened = _parse_ts(read_state_field(release_state, "RC Opened At"))
    halt_files = {
        _HALT_PROJECT: os.path.join(project_dir, "HALT"),
        _HALT_GLOBAL: os.path.join(kanban_root, "HALT"),
    }

    def halt_cause(source: str, at: _Timestamp) -> str:
        return sentinel_cause(halt_files[source], format_iso(at), kanban_root=kanban_root)

    return Evidence(
        rc_windows=load_rc_windows(os.path.join(project_dir, "metrics", "rc")),
        active_rc=None if active_rc.lower() == "none" else active_rc,
        active_opened_at=active_opened,
        triage=load_triage(task_dirs, kanban_root),
        statuses=load_statuses(task_dirs, kanban_root),
        halt_cause=halt_cause,
    )


# ---------------------------------------------------------------------------
# Rebuild
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class Block:
    """One block being rebuilt: the ledger lines it already has, and what the
    evidence adds."""

    version: str
    source: str
    start: _Timestamp
    start_line: dict | None = None
    escalate_lines: list = dataclasses.field(default_factory=list)
    end_line: dict | None = None
    triage: list = dataclasses.field(default_factory=list)
    status: TaskStatus | None = None
    start_basis: str = "ledger"
    # Derived:
    cause: str = CAUSE_CHAIN
    reason_code: str | None = None
    escalate_at: _Timestamp | None = None
    escalate_code: str | None = None
    escalate_basis: str = ""
    end: _Timestamp | None = None
    end_class: str = CLASS_ANY
    end_code: str | None = None
    end_basis: str = ""
    block_class: str = CLASS_ANY

    @property
    def is_task(self) -> bool:
        """True for a task-level block (source ``task:<ID>``)."""
        return self.source.startswith(_TASK_PREFIX)

    @property
    def task_id(self) -> str:
        """The task ID of a task-level block; "" for any other source."""
        return self.source[len(_TASK_PREFIX) :].strip() if self.is_task else ""

    @property
    def in_ledger(self) -> bool:
        """True when the ledger already has this block's start line."""
        return self.start_line is not None

    def ledger_end(self) -> _Timestamp | None:
        """When the ledger's own end line closes the block; None while open."""
        return _parse_ts(self.end_line["timestamp"]) if self.end_line else None

    def covers(self, moment: _Timestamp) -> bool:
        """True when *moment* falls inside the block as the ledger has it."""
        end = self.ledger_end()
        return self.start <= moment and (end is None or moment <= end)


def _ledger_blocks(records: list) -> list:
    """Group the ledger's raw lines into blocks, in start order. The ledger
    has already been validated by block_intervals(), so pairing holds."""
    blocks: list = []
    open_by_pair: dict = {}
    for record in records:
        pair = (record["version"], record["source"])
        if record["kind"] == KIND_START:
            block = Block(record["version"], record["source"], _parse_ts(record["timestamp"]), start_line=record)
            open_by_pair[pair] = block
            blocks.append(block)
            continue
        block = open_by_pair.get(pair)
        if block is None:
            continue
        if record["kind"] == KIND_ESCALATE:
            block.escalate_lines.append(record)
        else:
            block.end_line = record
            del open_by_pair[pair]
    return blocks


def _version_at(moment: _Timestamp, evidence: Evidence) -> str:
    """The RC whose window holds *moment*: a rollup's [opened_at, closed_at],
    else the Active RC once it opened, else the ledger's no-RC version."""
    for window in evidence.rc_windows:
        if window.opened_at and window.closed_at and window.opened_at <= moment <= window.closed_at:
            return window.version
    if evidence.active_rc and evidence.active_opened_at and evidence.active_opened_at <= moment:
        return evidence.active_rc
    return NO_RC_VERSION


def _rc_close(version: str, evidence: Evidence) -> _Timestamp | None:
    """When *version*'s RC closed: its rollup's closed_at, else the earliest
    opened_at of a later RC. None for a no-RC version or an RC not closed."""
    key = semver.parse_version(version)
    if key is None:
        return None
    for window in evidence.rc_windows:
        if window.key == key and window.closed_at is not None:
            return window.closed_at
    later = [w.opened_at for w in evidence.rc_windows if w.key > key and w.opened_at is not None]
    return min(later) if later else None


def _overlaps(block: Block, start: _Timestamp, end: _Timestamp | None) -> bool:
    """True when [start, end] (end None: open) meets *block*'s interval."""
    block_end = block.end or block.ledger_end()
    far = datetime.datetime.max.replace(tzinfo=datetime.UTC)
    return start <= (block_end or far) and block.start <= (end or far)


def _attach_evidence(blocks: list, evidence: Evidence, notes: list) -> list:
    """Hang triage files and Block Records on the blocks they belong to,
    creating a block for evidence no ledger block covers."""
    groups: dict = {}
    for item in evidence.triage:
        groups.setdefault((item.task_id, item.epoch or item.recorded_at), []).append(item)

    for (task_id, epoch), items in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        source = _TASK_PREFIX + task_id
        target = next((b for b in blocks if b.source == source and b.covers(items[0].recorded_at)), None)
        if target is None:
            target = Block(_version_at(epoch, evidence), source, epoch, start_basis=f"triage evidence {items[0].path}")
            last_seen = max(item.recorded_at for item in items)
            if any(
                b.source == source and b.version == target.version and _overlaps(b, epoch, last_seen) for b in blocks
            ):
                notes.append(f"skipped triage evidence for {source} at {format_iso(epoch)}: overlaps a recorded block")
                continue
            blocks.append(target)
        target.triage.extend(items)

    for status in evidence.statuses.values():
        source = _TASK_PREFIX + status.task_id
        target = None
        if status.last_blocked_at is not None:
            target = next((b for b in blocks if b.source == source and b.covers(status.last_blocked_at)), None)
        if target is None and status.state == _STATE_BLOCKED:
            open_block = next((b for b in blocks if b.source == source and b.ledger_end() is None), None)
            if open_block is not None:
                target = open_block
            elif status.last_blocked_at is not None:
                target = Block(
                    _version_at(status.last_blocked_at, evidence),
                    source,
                    status.last_blocked_at,
                    start_basis=f"Block Record {status.path}",
                )
                blocks.append(target)
        if target is not None:
            target.status = status
    return blocks


def _derive_task(block: Block, evidence: Evidence) -> None:
    start_line = block.start_line or {}
    block.cause = _vocab(start_line.get("cause"), VALID_CAUSES) or CAUSE_CHAIN
    codes = [t.reason_code for t in block.triage if t.reason_code]
    block.reason_code = (
        record_reason_code(start_line)
        or (codes[0] if codes else None)
        or (block.status.reason_code if block.status else None)
    )

    ledger_end = block.ledger_end()
    escalated = bool(block.escalate_lines) or (block.start_line is not None and record_class(start_line) == CLASS_HUMAN)
    act = next((t for t in block.triage if t.outcome == OUTCOME_ACT and t.recorded_at >= block.start), None)
    for item in block.triage:
        if item.outcome != OUTCOME_ESCALATE or item.recorded_at < block.start:
            continue
        if act is not None and item.recorded_at > act.recorded_at:
            continue
        if not escalated and ledger_end is None:
            block.escalate_at = item.recorded_at
            block.escalate_code = item.reason_code
            block.escalate_basis = f"triage outcome escalate {item.path}"
        escalated = True
        break

    if ledger_end is not None:
        block.end = ledger_end
        block.end_class = record_class(block.end_line)
        block.end_code = record_reason_code(block.end_line)
        block.end_basis = "ledger"
        escalated = escalated or any(
            t.outcome == OUTCOME_ESCALATE and block.start <= t.recorded_at <= ledger_end for t in block.triage
        )
    elif act is not None:
        block.end, block.end_class, block.end_code = act.recorded_at, CLASS_ANY, REQUEUE_LEDGER_REASON_CODE
        block.end_basis = f"triage outcome act {act.path}"
    elif block.status is None or block.status.state != _STATE_BLOCKED:
        closed = _rc_close(block.version, evidence)
        if closed is not None and closed >= block.start:
            block.end, block.end_class, block.end_code = closed, CLASS_HUMAN, RC_CLOSED_REASON_CODE
            block.end_basis = f"RC {block.version} closed with the task released and no triage requeue on record"
    block.block_class = CLASS_HUMAN if (escalated or block.end_class == CLASS_HUMAN) else CLASS_ANY
    _settle_end_class(block)


def _settle_end_class(block: Block) -> None:
    """A ledger end line that carries no class shows the block's rebuilt
    class in the sidecar, so the sidecar alone reads every block's class."""
    if block.end_line is not None and block.end_line.get("class") is None:
        block.end_class = block.block_class


def _derive_other(block: Block, tasks: list, evidence: Evidence) -> None:
    """A HALT (or any non-task) block: cause from provenance, class from the
    task blocks of the same RC it overlaps."""
    start_line = block.start_line or {}
    if _vocab(start_line.get("cause"), VALID_CAUSES):
        block.cause = _vocab(start_line.get("cause"), VALID_CAUSES)
    elif block.source in (_HALT_PROJECT, _HALT_GLOBAL):
        block.cause = evidence.halt_cause(block.source, block.start)
    else:
        block.cause = CAUSE_CHAIN
    block.reason_code = record_reason_code(start_line) or block.source.replace(":", "_")

    ledger_end = block.ledger_end()
    if ledger_end is not None:
        block.end = ledger_end
        block.end_class = record_class(block.end_line)
        block.end_code = record_reason_code(block.end_line)
        block.end_basis = "ledger"
    else:
        closed = _rc_close(block.version, evidence)
        if closed is not None and closed >= block.start:
            block.end, block.end_code = closed, RC_CLOSED_REASON_CODE
            block.end_basis = f"RC {block.version} closed"
    companions = [t for t in tasks if t.version == block.version and _overlaps(t, block.start, block.end)]
    human = (
        record_class(start_line) == CLASS_HUMAN
        or bool(block.escalate_lines)
        or (block.end_line is not None and record_class(block.end_line) == CLASS_HUMAN)
        or (block.cause != CAUSE_OPERATOR and any(t.block_class == CLASS_HUMAN for t in companions))
    )
    block.block_class = CLASS_HUMAN if human else CLASS_ANY
    if block.end_line is None:
        block.end_class = block.block_class
    _settle_end_class(block)
    if companions:
        block.start_basis += f"; class from {', '.join(sorted({t.source for t in companions}))}"


def _vocab(value: object, vocabulary: tuple) -> str | None:
    word = str(value or "").strip().lower()
    return word if word in vocabulary else None


def _record(
    block: Block, kind: str, moment: _Timestamp, fields: dict, *, line: dict | None, basis: str, number: int
) -> dict:
    """One sidecar line. A line the ledger has keeps its own version, kind,
    source and timestamp text."""
    record = {
        "version": block.version,
        "kind": kind,
        "source": block.source,
        "timestamp": line["timestamp"] if line else format_iso(moment),
    }
    record.update({k: v for k, v in fields.items() if v is not None})
    record[ANNOTATION_KEY] = {
        "block": number,
        "block_class": block.block_class,
        "in_ledger": line is not None,
        "basis": basis,
    }
    return record


def _block_records(block: Block, number: int) -> list:
    lines = [
        _record(
            block,
            KIND_START,
            block.start,
            {
                "class": record_class(block.start_line) if block.start_line else CLASS_ANY,
                "cause": block.cause,
                "reason_code": block.reason_code,
            },
            line=block.start_line,
            basis=block.start_basis,
            number=number,
        )
    ]
    for line in block.escalate_lines:
        lines.append(
            _record(
                block,
                KIND_ESCALATE,
                _parse_ts(line["timestamp"]),
                {"class": CLASS_HUMAN, "reason_code": record_reason_code(line)},
                line=line,
                basis="ledger",
                number=number,
            )
        )
    if block.escalate_at is not None:
        lines.append(
            _record(
                block,
                KIND_ESCALATE,
                block.escalate_at,
                {"class": CLASS_HUMAN, "reason_code": block.escalate_code},
                line=None,
                basis=block.escalate_basis,
                number=number,
            )
        )
    if block.end is not None:
        lines.append(
            _record(
                block,
                KIND_END,
                block.end,
                {"class": block.end_class, "cause": block.cause, "reason_code": block.end_code},
                line=block.end_line,
                basis=block.end_basis,
                number=number,
            )
        )
    return lines


def rebuild(ledger_path: str, evidence: Evidence) -> tuple:
    """Rebuild every block from the ledger at *ledger_path* and *evidence*.
    Returns ``(sidecar_records, notes)``: the sidecar lines in block start
    order, and one line per piece of evidence that was set aside.

    Raises LedgerError when the ledger is malformed."""
    block_intervals(ledger_path)  # validates pairing; raises on a malformed ledger
    records = parse_records(ledger_path)
    notes: list = []
    blocks = _attach_evidence(_ledger_blocks(records), evidence, notes)
    tasks = [b for b in blocks if b.is_task]
    for block in tasks:
        _derive_task(block, evidence)
    for block in blocks:
        if not block.is_task:
            _derive_other(block, tasks, evidence)
    ordered = sorted(enumerate(blocks), key=lambda pair: (pair[1].start, pair[0]))
    sidecar: list = []
    for number, (_, block) in enumerate(ordered, start=1):
        sidecar.extend(_block_records(block, number))
    return sidecar, notes


# ---------------------------------------------------------------------------
# Sidecar I/O
# ---------------------------------------------------------------------------


def sidecar_path_for_ledger(ledger_path: str) -> str:
    """<metrics dir>/blocked-intervals.backfill.jsonl beside *ledger_path*."""
    return os.path.join(os.path.dirname(os.path.abspath(ledger_path)), SIDECAR_FILENAME)


def write_sidecar(path: str, records: list) -> None:
    """Rewrite the sidecar whole (atomic replace), one sorted-key line each."""
    content = "".join(json.dumps(r, sort_keys=True) + "\n" for r in records)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write(path, content, prefix=".reliability_backfill_tmp_")


def _ledger_key(record: dict) -> tuple:
    return (str(record["version"]), str(record["kind"]), str(record["source"]), _parse_ts(record["timestamp"]))


def read_sidecar(path: str, project: str) -> list:
    """Parse and validate the sidecar at *path*. Raises BackfillRefused for
    a missing or malformed sidecar."""
    if not os.path.isfile(path):
        raise BackfillRefused(
            "reliability-backfill.no-sidecar",
            f"there is no sidecar to apply at {path}; --apply merges a sidecar written and reviewed earlier",
            f"{_SCRIPT} --project {project}",
        )
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    records = []
    for lineno, raw in enumerate(lines, start=1):
        if not raw.strip():
            continue
        problem = None
        try:
            record = json.loads(raw)
        except ValueError as exc:
            record, problem = None, f"is not valid JSON ({exc})"
        if problem is None:
            problem = _sidecar_problem(record)
        if problem is not None:
            raise BackfillRefused(
                "reliability-backfill.malformed-sidecar",
                f"{path} line {lineno} {problem}; nothing was applied",
                f"{_SCRIPT} --project {project}",
            )
        records.append(record)
    return records


def _sidecar_problem(record: object) -> str | None:
    if not isinstance(record, dict):
        return "is not a JSON object"
    missing = [f for f in ("version", "kind", "source", "timestamp") if not record.get(f)]
    if missing:
        return f"is missing {', '.join(missing)}"
    if record["kind"] not in VALID_KINDS:
        return f"has kind {record['kind']!r}, not one of {', '.join(VALID_KINDS)}"
    if _parse_ts(record["timestamp"]) is None:
        return f"has an unparseable timestamp {record['timestamp']!r}"
    if "class" in record and _vocab(record["class"], VALID_CLASSES) is None:
        return f"has class {record['class']!r}, not one of {', '.join(VALID_CLASSES)}"
    if "cause" in record and _vocab(record["cause"], VALID_CAUSES) is None:
        return f"has cause {record['cause']!r}, not one of {', '.join(VALID_CAUSES)}"
    annotation = record.get(ANNOTATION_KEY)
    if not isinstance(annotation, dict) or not isinstance(annotation.get("block"), int):
        return f"has no {ANNOTATION_KEY}.block number"
    return None


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ApplyResult:
    """What apply_sidecar() did: the records appended, how many sidecar
    records the ledger already had, and each skipped record with why."""

    appended: list = dataclasses.field(default_factory=list)
    present: int = 0
    skipped: list = dataclasses.field(default_factory=list)


def _write(ledger_path: str, record: dict) -> None:
    kind = record["kind"]
    args = (ledger_path, record["version"], record["source"], record["timestamp"])
    if kind == KIND_START:
        append_block_start(
            *args, block_class=record.get("class"), cause=record.get("cause"), reason_code=record.get("reason_code")
        )
    elif kind == KIND_ESCALATE:
        mark_block_escalated(*args, reason_code=record.get("reason_code"))
    else:
        append_block_end(
            *args, block_class=record.get("class"), cause=record.get("cause"), reason_code=record.get("reason_code")
        )


def apply_sidecar(ledger_path: str, records: list) -> ApplyResult:
    """Append every sidecar record the ledger lacks, through the ledger's own
    writers; never rewrites a line. See the module docstring, "Apply"."""
    result = ApplyResult()
    groups: dict = {}
    for record in records:
        groups.setdefault(record[ANNOTATION_KEY]["block"], []).append(record)

    have = {_ledger_key(r) for r in parse_records(ledger_path)}
    continuing, new = [], []
    for number in sorted(groups):
        group = groups[number]
        start = next((r for r in group if r["kind"] == KIND_START), None)
        (continuing if start is not None and _ledger_key(start) in have else new).append(group)

    for group in continuing + new:
        starts = [r for r in group if r["kind"] == KIND_START]
        missing = [r for r in group if _ledger_key(r) not in have]
        result.present += len(group) - len(missing)
        if not missing:
            continue
        if len(starts) != 1 or group[0] is not starts[0]:
            result.skipped.append((missing[0], "the block does not open with exactly one start line"))
            continue
        if starts[0] not in missing and not _open_block_is(ledger_path, starts[0]):
            result.skipped.append((missing[0], "the ledger's open block for this source is not this block"))
            continue
        for record in missing:
            fields = {k: v for k, v in record.items() if k != ANNOTATION_KEY}
            try:
                _write(ledger_path, fields)
            except LedgerError as exc:
                result.skipped.append((record, exc.message))
                break
            result.appended.append(fields)
            have.add(_ledger_key(record))
    return result


def _open_block_is(ledger_path: str, start: dict) -> bool:
    """True when the ledger's open block for *start*'s (version, source) is
    the block *start* opened."""
    when = _parse_ts(start["timestamp"])
    return any(
        b["version"] == start["version"]
        and b["source"] == start["source"]
        and b["end"] is None
        and _parse_ts(b["start"]) == when
        for b in block_intervals(ledger_path)
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _describe(record: dict) -> str:
    extras = " ".join(f"{k}={record[k]}" for k in ("class", "cause", "reason_code") if record.get(k))
    return f"{record['kind']:<14} {record['version']:<9} {record['source']} {record['timestamp']} {extras}".rstrip()


def _resolve_project(kanban_root: str, project: str) -> str:
    project_dir = os.path.join(kanban_root, "projects", project)
    if not project or os.sep in project or not os.path.isdir(project_dir):
        raise BackfillRefused(
            "reliability-backfill.unknown-project",
            f"project {project!r} has no directory under {os.path.join(kanban_root, 'projects')}; "
            "nothing was read or written",
            f"ls {os.path.join(kanban_root, 'projects')}",
            project_exists=False,
        )
    return project_dir


def run(kanban_root: str, project: str, *, apply: bool, out=None) -> int:
    """Write the sidecar (or, with *apply*, merge it) and print a summary.
    Raises BackfillRefused before any write."""
    out = out or sys.stdout
    _resolve_project(kanban_root, project)
    ledger = ledger_path_for_project(kanban_root, project)
    sidecar = sidecar_path_for_ledger(ledger)
    try:
        if apply:
            records = read_sidecar(sidecar, project)
            parse_records(ledger)
            block_intervals(ledger)
        else:
            records, notes = rebuild(ledger, load_evidence(kanban_root, project))
    except LedgerError as exc:
        raise BackfillRefused(
            "reliability-backfill.malformed-ledger",
            f"the block ledger at {ledger} does not parse, so no record can be checked against it: {exc.message}",
            f"cat -n {ledger}",
        ) from exc

    if not apply:
        write_sidecar(sidecar, records)
        fresh = [r for r in records if not r[ANNOTATION_KEY]["in_ledger"]]
        blocks = len({r[ANNOTATION_KEY]["block"] for r in records})
        print(f"sidecar: {sidecar}", file=out)
        print(
            f"blocks: {blocks}; records: {len(records)} ({len(fresh)} new, "
            f"{len(records) - len(fresh)} already in the ledger)",
            file=out,
        )
        for record in fresh:
            print(f"  new {_describe(record)}", file=out)
        for note in notes:
            print(f"  note: {note}", file=out)
        print(f"ledger unchanged; review the sidecar, then run: {_SCRIPT} --project {project} --apply", file=out)
        return EXIT_OK

    result = apply_sidecar(ledger, records)
    print(f"ledger: {ledger}", file=out)
    print(
        f"appended: {len(result.appended)}; already present: {result.present}; " f"skipped: {len(result.skipped)}",
        file=out,
    )
    for record in result.appended:
        print(f"  appended {_describe(record)}", file=out)
    for record, why in result.skipped:
        print(f"  skipped {_describe(record)} ({why})", file=out)
    try:
        # CONVERT: best-effort, like the ledger's own refresh hook; the
        # appends above have landed whatever happens here.
        path = write_reliability(project, kanban_root=kanban_root)
        print(f"reliability: {path}", file=out)
    except LedgerError as exc:
        print(
            f"[reliability-backfill] WARNING: reliability.json was not refreshed: {exc.message}. "
            f"Re-run: PYTHONPATH={kanban_root} python3 -m pgai_agent_kanban.metrics.reliability "
            f"refresh --project {project} --kanban-root {kanban_root}",
            file=sys.stderr,
        )
    return EXIT_OK


def main(argv: list | None = None) -> int:
    """CLI entry point; see the module docstring for output and exit codes."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.metrics.reliability_backfill",
        description=(
            "Rebuild a project's block-ledger records (class, cause, reason_code) "
            f"from existing evidence into metrics/{SIDECAR_FILENAME}; --apply appends "
            "the records the ledger lacks and refreshes metrics/reliability.json."
        ),
    )
    parser.add_argument("--project", required=True, help="project name, e.g. pgai-agent-kanban")
    parser.add_argument(
        "--kanban-root",
        dest="kanban_root",
        default=None,
        help="kanban root holding projects/ (default: PGAI_AGENT_KANBAN_ROOT_PATH)",
    )
    parser.add_argument("--apply", action="store_true", help="merge the sidecar into the ledger")
    args = parser.parse_args(argv)

    root = args.kanban_root
    if root is None:
        from pgai_agent_kanban.env import resolve_kanban_root

        root = str(resolve_kanban_root())
    try:
        return run(os.path.abspath(root), args.project, apply=args.apply)
    except BackfillRefused as refusal:
        # BLOCK-WORTHY: every refusal (unknown project, malformed ledger,
        # no sidecar, malformed sidecar) is raised before the first write;
        # going on would append unchecked records to the ledger.
        # A project with no directory gets no refusal ledger created for it.
        record_in = args.project if refusal.project_exists else None
        emit_error_contract(refusal.error, refusal.message, refusal.remedy, project=record_in)
        # CENSUS-ANCHOR: reliability-backfill-01
        return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
