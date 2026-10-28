"""
blocked_ledger.py — the per-project block ledger.

An RC's rc-metrics "Wall" column is honest open-to-ship elapsed time, and
stays that way. This ledger carries the second number a release can be
read against: how many of those minutes the RC spent unable to proceed at
all — parked under a HALT, or with its chain stalled on a BLOCKED task
waiting on a human — so a caller can report real execution as Wall minus
Blocked without ever mutating Wall itself. It is also the one record of
every block the project has had, each carrying its class (did a human have
to clear it) and its cause (did the chain raise it, or an operator), which
is what the reliability streaks are computed from.

Record path convention: <kanban_root>/projects/<project_name>/metrics/
blocked-intervals.jsonl — a project-scoped metrics sidecar, resolved
through pp_project_root (team/scripts/lib/project_paths.sh). Deliberately
NOT release-state.md: that file is CM's, with four fixed fields that
always exist, so it cannot carry this ledger's lifecycle — an RC that was
never blocked has no entry here at all, and "no entry" must read as zero
blocked minutes rather than as a missing required field.

Lifecycle: the file is created lazily by the first block-start or
block-end event stamped for ANY RC of the project (append_block_start /
append_block_end below). A project that has never had a blocked RC has no
file; an RC within an existing file that was never blocked has no lines
naming its version. Both read back as zero blocked minutes — absence is a
valid state, never an error. The file accumulates boundary events for
every RC of the project across releases (the same per-project, many-
records shape as pgai_agent_kanban.deep_verify.ledger, filtered by a join
key at read time rather than split one-file-per-RC); each RC's own slice
of records is meant to stop changing once that RC ships — this module does
not enforce that immutability itself (it never deletes or rewrites a past
boundary, and the reader never writes), it is a discipline the writers
uphold when they stamp events.

On-disk format: one JSON object per line (JSON Lines). An append is a
single self-contained line, so a torn write from a crashed process can
only ever corrupt the file's own last line, and a parse fault is
localized to naming the offending line rather than an entire document:

    {"version": "v2.24.0", "kind": "block-start", "source": "halt:project", "timestamp": "2026-09-18T09:15:22+00:00"}
    {"version": "v2.24.0", "kind": "block-end",   "source": "halt:project", "timestamp": "2026-09-18T09:55:22+00:00"}

and, with the optional fields:

    {"class": "any", "cause": "chain", "kind": "block-start", "reason_code": "agent_unclassified", "source": "task:example-task", "timestamp": "2026-09-18T10:00:00+00:00", "version": "v2.24.0"}
    {"class": "human", "kind": "block-escalate", "source": "task:example-task", "timestamp": "2026-09-18T10:20:00+00:00", "version": "v2.24.0"}
    {"class": "human", "kind": "block-end", "reason_code": "reset", "source": "task:example-task", "timestamp": "2026-09-18T11:00:00+00:00", "version": "v2.24.0"}
    {"cause": "operator", "kind": "block-start", "source": "halt:project", "timestamp": "2026-09-18T12:00:00+00:00", "version": "no-rc"}

Required fields, all single-line strings:
  version    the RC's release version, e.g. "v2.24.0" — the join key the
             reader filters on; lines for every other RC (and, since the
             ledger is per-project, only ever this project) coexist
             harmlessly in the same file and are ignored. A block raised
             while the project's Active RC is "none" is recorded under
             NO_RC_VERSION ("no-rc"), which is not a vX.Y.Z version and so
             never equals one: blocked_minutes() for any real RC never
             counts it. version_for_active_rc() maps an Active RC value
             (including "none") to the version to write.
  kind       "block-start", "block-end" or "block-escalate" (VALID_KINDS
             below) — no other value is ever written or accepted on read.
  source     a free-form label naming WHAT blocked — e.g. "halt:project",
             "halt:global", or "task:<TASK-ID>". Boundaries pair by
             (version, source): two independent, concurrent blockers (a
             HALT and a BLOCKED task) each open and close their own
             interval without one source's end ever closing another's
             still-open start.
  timestamp  ISO-8601, e.g. "2026-09-18T09:15:22+00:00" or the "Z" suffix
             spelling; both parse.

Optional fields — written only when a writer is given them, so a record
without them is still the complete original shape:
  class        "human" or "any" (VALID_CLASSES). On a block-start: the
               class known when the block was raised. On a block-end: the
               class of the resolution — "human" when a human had to clear
               it. On a block-escalate: always "human".
  cause        "chain" or "operator" (VALID_CAUSES) — who raised the
               block. Read from the block-start.
  reason_code  free-form single-line code — why the block was raised (on a
               block-start) or how it was resolved (on a block-end).

A record without class reads as "any"; without cause, as "chain"; without
reason_code, as None. record_class(), record_cause() and
record_reason_code() are the accessors that apply those defaults; a value
outside the vocabulary also reads as the default (matched case-
insensitively, surrounding whitespace ignored), so the reader never
refuses a line over an optional field.

A block's class — what block_intervals() reports — is "human" when its
block-start says "human", when it carries a block-escalate mark, or when
its block-end's resolution class is "human"; otherwise "any".

Three write operations, all atomic (temp file in the same directory +
os.replace — the same pattern pgai_agent_kanban.deep_verify.ledger and
pgai_agent_kanban.overwatch.ledger both use for their own atomic writes):

  - append_block_start(ledger_path, version, source, timestamp,
    *, block_class=None, cause=None, reason_code=None) — opens a
    new interval for (version, source). Raises LedgerError, writing
    nothing, if (version, source) already has an open, unclosed start —
    a source cannot block twice without unblocking first; this
    invariant is what makes the reader's start/end pairing unambiguous.
  - append_block_end(ledger_path, version, source, timestamp,
    *, block_class=None, cause=None, reason_code=None) — closes
    the open interval for (version, source); block_class is the class
    of the resolution. Raises LedgerError, writing nothing, if there is
    no matching open start.
  - mark_block_escalated(ledger_path, version, source, timestamp,
    *, reason_code=None) — appends a block-escalate line (class
    "human") for the open interval of (version, source), so that block
    reads class "human" from then on without any earlier line changing.
    It neither opens nor closes the interval. Raises LedgerError, writing
    nothing, if there is no open start, or if the open block already
    reads "human" (its start says so, or it was already escalated).

  After each successful append, a ledger at the convention path
  (<project>/metrics/blocked-intervals.jsonl) has the reliability.json
  beside it rewritten (pgai_agent_kanban.metrics.reliability). The refresh
  is best-effort: a failure prints one warning and never changes the
  append's result.

  A block-end and a block-escalate must name the version the block-start
  was stamped under. A caller that may cross an RC boundary while a block
  is open (raised under "no-rc", resolved after an RC opened) finds that
  version through block_intervals(): the entry for its source whose
  "end" is None.

  The refusals above — a second start while one is already open, an end
  or an escalation with no open start, an escalation of a block that
  already reads "human" — mean the boundary was already
  in the state THIS call asked for: a caller re-attempting the same
  boundary event every cycle (the common wake-path shape) hits one of
  these every time after the first and is expected to. They all carry the
  distinct machine error key ``blocked-ledger.no-op`` (ERROR_NO_OP
  below), separate from ``blocked-ledger.malformed``, so a caller — such
  as the wake-path adapter — can treat "already in the requested state"
  as its own designed steady state rather than as a write failure, while
  every other LedgerError (a malformed ledger, an unwritable directory,
  an invalid window, an out-of-vocabulary class or cause handed to a
  writer — ``blocked-ledger.invalid-field``) keeps its own distinguishing
  key and still reads as a genuine fault.

Read path — the readers every consumer of this ledger calls, never
re-derived (this module's whole reason to exist):

  - blocked_minutes(ledger_path, version, window_opened_at, window_closed_at)
    returns the merged, clamped blocked-minutes total (a float; whole
    when every boundary falls on a whole minute) for *version* within the
    RC's own [window_opened_at, window_closed_at] window:
      1. every block-start/block-end pair recorded for *version* (paired
         per source, in file order) becomes one interval; a source still
         open when the file is read — no block-end recorded at all, e.g.
         the RC shipped while still blocked — is treated as open-ended
         and clamped to window_closed_at,
      2. every interval is clamped to [window_opened_at, window_closed_at]:
         a boundary outside the window is cut to the window's own edge,
         and an interval falling entirely outside the window contributes
         nothing,
      3. clamped intervals are merged — overlapping or touching spans
         collapse into one — before summing, so two sources blocked over
         the same wall-clock minutes are never double-counted,
      4. the merged total, in minutes, is returned.
    block-escalate lines and the optional fields never change which
    intervals it counts: every block counts, whatever its class or cause.
    An absent ledger file, or one with no boundaries recorded for
    *version*, returns 0.0 — absence is a valid state, never an error.
    Raises LedgerError — carrying error/message/remedy fields, and
    rendering as the three-line dual-channel house shape via
    pgai_agent_kanban.lib.error_contract.format_error_contract when
    stringified — when the ledger file has non-whitespace content that
    fails to parse cleanly: an unparseable line, a line missing a
    required field, an out-of-vocabulary kind, or a start/end pairing
    that cannot be resolved (an end with no open start, or two opens for
    the same source with no end between them). A malformed ledger
    refuses rather than silently reporting a fabricated or partial
    number — CENSUS CLASS CONVERT (docs/blocking-site-census.md): the
    refusal never halts a release path on its own; it yields no number
    for the caller to display instead, exactly like the CLI's own `read`
    subcommand below.
  - block_intervals(ledger_path, version=None) returns one dict per block,
    in block-start file order, for *version* (every version when None):
      version, source, start, end (None while still open), class (the
      block's class, rule above), cause, reason_code (from the
      block-start), escalated (bool), escalated_at (timestamp or None),
      resolution_class and resolution_reason_code (from the block-end;
      None while still open).
    Same LedgerError conditions as blocked_minutes(), plus a
    block-escalate with no open block-start to mark.
  - parse_records(ledger_path) returns the raw records in file order.

Usage (CLI):
    python3 blocked_ledger.py start    <ledger_path> <version> <source> <timestamp> [--class human|any] [--cause chain|operator] [--reason-code CODE] [--project NAME]
    python3 blocked_ledger.py end      <ledger_path> <version> <source> <timestamp> [--class human|any] [--cause chain|operator] [--reason-code CODE] [--project NAME]
    python3 blocked_ledger.py escalate <ledger_path> <version> <source> <timestamp> [--reason-code CODE] [--project NAME]
    python3 blocked_ledger.py read     <ledger_path> <version> <window_opened_at> <window_closed_at> [--project NAME]

  <version> is the Active RC, or no-rc when the Active RC is none.

  --project names the project the ledger belongs to: a refusal is recorded
  in that project's refusal ledger (default: PGAI_PROJECT_NAME). The
  blocked-ledger.no-op outcome still prints its dual-channel block to
  stderr and exits 1, but is recorded in no refusal ledger -- it is the
  designed steady state, not a refusal.

Usage (import):
    from pgai_agent_kanban.metrics.blocked_ledger import (
        LedgerError, ERROR_NO_OP, NO_RC_VERSION, append_block_start,
        append_block_end, mark_block_escalated, block_intervals,
        blocked_minutes, ledger_path_for_project, version_for_active_rc,
    )
    append_block_start(ledger_path, "v2.24.0", "halt:project", "2026-09-18T09:15:22+00:00")
    append_block_end(ledger_path, "v2.24.0", "halt:project", "2026-09-18T09:55:22+00:00")
    minutes = blocked_minutes(
        ledger_path, "v2.24.0",
        "2026-09-18T00:00:00+00:00", "2026-09-19T00:00:00+00:00",
    )   # -> 40.0
    append_block_start(
        ledger_path, version_for_active_rc("none"), "task:example-task",
        "2026-09-18T10:00:00+00:00", reason_code="agent_unclassified",
    )   # recorded under "no-rc"
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
from datetime import datetime
from typing import Optional

# Run by file path (``python3 .../blocked_ledger.py``) or loaded from a file
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
from pgai_agent_kanban.lib.timeutil import try_parse_iso  # noqa: E402

KIND_START = "block-start"
KIND_END = "block-end"
KIND_ESCALATE = "block-escalate"

VALID_KINDS = (KIND_START, KIND_END, KIND_ESCALATE)

_REQUIRED_FIELDS = ("version", "kind", "source", "timestamp")

#: Block class: "human" — a human had to clear it; "any" — any block.
CLASS_HUMAN = "human"
CLASS_ANY = "any"
VALID_CLASSES = (CLASS_HUMAN, CLASS_ANY)
DEFAULT_CLASS = CLASS_ANY

#: Block cause: raised by the chain itself, or by an operator's decision.
CAUSE_CHAIN = "chain"
CAUSE_OPERATOR = "operator"
VALID_CAUSES = (CAUSE_CHAIN, CAUSE_OPERATOR)
DEFAULT_CAUSE = CAUSE_CHAIN

#: The version a block is recorded under when the project's Active RC is
#: "none". Not vX.Y.Z-shaped, so it never equals an RC version and no
#: per-RC blocked_minutes() total ever counts it.
NO_RC_VERSION = "no-rc"

# Optional record field names, as they appear on disk.
_FIELD_CLASS = "class"
_FIELD_CAUSE = "cause"
_FIELD_REASON_CODE = "reason_code"

# Machine error key for a write attempt that found the boundary already in
# the state the caller asked for -- a second block-start with one already
# open, or a block-end with no open block-start to close. Distinct from
# "blocked-ledger.malformed" (used for actual structural corruption) so a
# caller can tell its own designed steady state apart from a genuine fault.
# See the module docstring's "Two write operations" section.
ERROR_NO_OP = "blocked-ledger.no-op"


class LedgerError(Exception):
    """Raised for structural problems with the ledger: an unparseable
    line, a record missing a required field, an out-of-vocabulary kind,
    or a start/end pairing that does not resolve. Carries the
    dual-channel error-contract fields (error/message/remedy) so a
    caller — CLI or library — can speak the house shape without
    re-deriving it; str(exc) already renders the three-line block."""

    def __init__(self, error: str, message: str, remedy: str):
        from pgai_agent_kanban.lib.error_contract import format_error_contract

        self.error = error
        self.message = message
        self.remedy = remedy
        super().__init__(format_error_contract(error, message, remedy))


# ---------------------------------------------------------------------------
# Optional fields: accessors (liberal read) and validation (strict write)
# ---------------------------------------------------------------------------


def _vocab_value(raw: object, vocabulary: tuple, default: str) -> str:
    """*raw* as its canonical vocabulary word, or *default* when it is
    absent or not one of *vocabulary* (case and surrounding whitespace
    ignored)."""
    if isinstance(raw, str):
        word = raw.strip().lower()
        if word in vocabulary:
            return word
    return default


def record_class(record: dict) -> str:
    """The record's own ``class`` field: "human" or "any"; "any" when the
    field is absent or out of vocabulary. This is one line's field — the
    class of a whole block is block_intervals()'s ``class``."""
    return _vocab_value(record.get(_FIELD_CLASS), VALID_CLASSES, DEFAULT_CLASS)


def record_cause(record: dict) -> str:
    """The record's ``cause`` field: "chain" or "operator"; "chain" when
    the field is absent or out of vocabulary."""
    return _vocab_value(record.get(_FIELD_CAUSE), VALID_CAUSES, DEFAULT_CAUSE)


def record_reason_code(record: dict) -> Optional[str]:
    """The record's ``reason_code`` field, stripped; None when absent or
    blank."""
    raw = record.get(_FIELD_REASON_CODE)
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def version_for_active_rc(active_rc: Optional[str]) -> str:
    """The ledger version to record a block under, given the project's
    ``## Active RC`` value: the RC version itself, or NO_RC_VERSION when
    the Active RC is "none" (any case), empty or None."""
    text = (active_rc or "").strip()
    if not text or text.lower() == "none":
        return NO_RC_VERSION
    return text


def _optional_fields(
    block_class: Optional[str],
    cause: Optional[str],
    reason_code: Optional[str],
) -> dict:
    """Validate and canonicalise the optional fields a writer was handed,
    returning only the ones given. A class or cause outside its vocabulary
    raises LedgerError ``blocked-ledger.invalid-field`` before anything is
    written; a blank reason_code is omitted."""
    fields: dict = {}
    for name, value, vocabulary, example in (
        (_FIELD_CLASS, block_class, VALID_CLASSES, "--class human"),
        (_FIELD_CAUSE, cause, VALID_CAUSES, "--cause chain"),
    ):
        if value is None:
            continue
        word = str(value).strip().lower()
        if word not in vocabulary:
            raise LedgerError(
                "blocked-ledger.invalid-field",
                f"{name} {value!r} is not one of {', '.join(vocabulary)}; "
                "nothing was written",
                f"pass one of the accepted values, e.g. {example}",
            )
        fields[name] = word
    if reason_code is not None:
        text = " ".join(str(reason_code).split())
        if text:
            fields[_FIELD_REASON_CODE] = text
    return fields


def _write_ledger_file(target: str, content: str) -> None:
    """Atomically replace *target* with *content* via the shared writer,
    creating its directory first. A write failure is a LedgerError
    naming the directory to repair.
    """
    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    try:
        atomic_write(target, content, prefix=".blocked_ledger_tmp_")
    except OSError as exc:
        raise LedgerError(
            "blocked-ledger.write-failed",
            f"writing the blocked-interval ledger at {target} failed: {exc}",
            f"confirm the parent directory is writable, then retry: "
            f"install -d -m 0755 {os.path.dirname(os.path.abspath(target))}",
        ) from exc


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_records(ledger_path: str) -> list:
    """Parse *ledger_path* into a list of boundary-event dicts, in file
    (append) order.

    Returns [] when the file does not exist, or exists but is empty or
    whitespace-only — both mean "no boundary ever stamped", which is
    exactly what an unblocked project/RC looks like.

    Raises LedgerError on the first line that fails to parse cleanly: not
    valid JSON, missing one of version/kind/source/timestamp, an "kind"
    outside VALID_KINDS, or a "timestamp" that does not parse — a
    malformed ledger refuses rather than silently skipping the bad line
    and reporting a partial answer.
    """
    if not os.path.isfile(ledger_path):
        return []
    with open(ledger_path, "r", encoding="utf-8") as handle:
        text = handle.read()
    if not text.strip():
        return []

    records = []
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LedgerError(
                "blocked-ledger.malformed",
                f"{ledger_path} line {lineno} is not valid JSON: {exc}",
                f"inspect and repair or remove the offending line: "
                f"sed -n '{lineno}p' {ledger_path}",
            ) from exc
        if not isinstance(record, dict):
            raise LedgerError(
                "blocked-ledger.malformed",
                f"{ledger_path} line {lineno} is not a JSON object "
                f"(got {type(record).__name__})",
                f"inspect and repair or remove the offending line: "
                f"sed -n '{lineno}p' {ledger_path}",
            )
        missing = [f for f in _REQUIRED_FIELDS if not record.get(f)]
        if missing:
            raise LedgerError(
                "blocked-ledger.malformed",
                f"{ledger_path} line {lineno} is missing required field(s): "
                f"{', '.join(missing)}",
                f"inspect and repair or remove the offending line: "
                f"sed -n '{lineno}p' {ledger_path}",
            )
        if record["kind"] not in VALID_KINDS:
            raise LedgerError(
                "blocked-ledger.malformed",
                f"{ledger_path} line {lineno} has kind={record['kind']!r}, "
                f"not one of {VALID_KINDS}",
                f"inspect and repair or remove the offending line: "
                f"sed -n '{lineno}p' {ledger_path}",
            )
        if try_parse_iso(str(record["timestamp"]), naive_as_utc=False) is None:
            raise LedgerError(
                "blocked-ledger.malformed",
                f"{ledger_path} line {lineno} has an unparseable timestamp: "
                f"{record['timestamp']!r}",
                f"inspect and repair or remove the offending line: "
                f"sed -n '{lineno}p' {ledger_path}",
            )
        records.append(record)
    return records


# ---------------------------------------------------------------------------
# append_block_start / append_block_end / mark_block_escalated
# ---------------------------------------------------------------------------


def _append_line(ledger_path: str, record: dict) -> None:
    existing = ""
    if os.path.isfile(ledger_path):
        with open(ledger_path, "r", encoding="utf-8") as handle:
            existing = handle.read()
    line = json.dumps(record, sort_keys=True)
    new_content = existing if existing.endswith("\n") or not existing else existing + "\n"
    new_content += line + "\n"
    _write_ledger_file(ledger_path, new_content)
    _refresh_reliability_best_effort(ledger_path)


def _refresh_reliability_best_effort(ledger_path: str) -> None:
    """Rewrite the reliability.json beside a ledger that was just appended
    to (pgai_agent_kanban.metrics.reliability.refresh_for_ledger; a ledger
    outside the <project>/metrics/ convention is skipped there).

    CENSUS CLASS: CONVERT (docs/blocking-site-census.md) — best-effort: a
    failed refresh prints one warning to stderr and leaves the previous
    file in place; the append above has already landed and its result
    never changes."""
    try:
        from pgai_agent_kanban.metrics.reliability import refresh_for_ledger

        refresh_for_ledger(ledger_path)
    except Exception as exc:  # noqa: BLE001 — any refresh failure is a warning only
        detail = " ".join(str(exc).split())
        # <kanban_root>/projects/<project>/metrics/blocked-intervals.jsonl
        project_dir = os.path.dirname(os.path.dirname(os.path.abspath(ledger_path)))
        project = os.path.basename(project_dir)
        kanban_root = os.path.dirname(os.path.dirname(project_dir))
        print(
            f"[blocked-ledger] WARNING: reliability.json beside {ledger_path} was not "
            f"refreshed ({type(exc).__name__}: {detail}); the ledger write stands. "
            f"Re-run: PYTHONPATH={kanban_root} python3 -m "
            f"pgai_agent_kanban.metrics.reliability refresh --project {project} "
            f"--kanban-root {kanban_root}",
            file=sys.stderr,
        )


def _open_blocks(records: list, version: str) -> dict:
    """Replay *records* for *version* and return {source: state} for
    every source with an unclosed open interval after the replay, where
    state is {"start": <block-start record>, "escalated": bool}.

    A block-escalate line neither opens nor closes an interval; it marks
    the source's open interval escalated (one with no open interval to
    mark is malformed)."""
    open_by_source: dict = {}
    for record in records:
        if record["version"] != version:
            continue
        source = record["source"]
        kind = record["kind"]
        if kind == KIND_START:
            if source in open_by_source:
                raise LedgerError(
                    "blocked-ledger.malformed",
                    f"version {version!r} source {source!r} has two "
                    "block-start events with no block-end between them",
                    "append a matching block-end for the source before "
                    "appending another block-start, or repair the ledger "
                    "by hand to remove the duplicate start",
                )
            open_by_source[source] = {"start": record, "escalated": False}
        elif kind == KIND_ESCALATE:
            if source not in open_by_source:
                raise LedgerError(
                    "blocked-ledger.malformed",
                    f"version {version!r} source {source!r} has a "
                    "block-escalate event with no open block-start to mark",
                    "remove the orphaned block-escalate, or append the "
                    "missing block-start ahead of it, then re-read",
                )
            open_by_source[source]["escalated"] = True
        else:  # block-end
            if source not in open_by_source:
                raise LedgerError(
                    "blocked-ledger.malformed",
                    f"version {version!r} source {source!r} has a "
                    "block-end event with no matching open block-start",
                    "remove the orphaned block-end, or append the missing "
                    "block-start ahead of it, then re-read",
                )
            del open_by_source[source]
    return open_by_source


def _boundary_record(
    version: str, kind: str, source: str, timestamp: str, optional: dict
) -> dict:
    """One record: the four required fields plus the optional ones given."""
    record = {"version": version, "kind": kind, "source": source, "timestamp": timestamp}
    record.update(optional)
    return record


def append_block_start(
    ledger_path: str,
    version: str,
    source: str,
    timestamp: str,
    *,
    block_class: Optional[str] = None,
    cause: Optional[str] = None,
    reason_code: Optional[str] = None,
) -> None:
    """Stamp the start of a blocked interval for (*version*, *source*).
    See module docstring "Three write operations" for the pairing rule this
    enforces and the atomicity guarantee. The no-op refusal below is the
    designed steady state for a caller that re-attempts the same
    block-start every cycle while still blocked -- it carries the
    distinct ERROR_NO_OP key precisely so callers can tell it apart from
    genuine ledger corruption.

    *block_class* ("human"/"any"), *cause* ("chain"/"operator") and
    *reason_code* are written only when given; a class or cause outside
    its vocabulary raises LedgerError ``blocked-ledger.invalid-field``
    and writes nothing. Pass *version* as version_for_active_rc(...) so a
    block with no Active RC lands under NO_RC_VERSION."""
    optional = _optional_fields(block_class, cause, reason_code)
    records = parse_records(ledger_path)
    open_by_source = _open_blocks(records, version)
    if source in open_by_source:
        raise LedgerError(
            ERROR_NO_OP,
            f"version {version!r} source {source!r} is already blocked "
            f"(open since {open_by_source[source]['start']['timestamp']}) — "
            "refusing a second block-start",
            "append a block-end for this source before starting a new "
            "block, or confirm the caller is not double-firing",
        )
    _append_line(ledger_path, _boundary_record(version, KIND_START, source, timestamp, optional))


def append_block_end(
    ledger_path: str,
    version: str,
    source: str,
    timestamp: str,
    *,
    block_class: Optional[str] = None,
    cause: Optional[str] = None,
    reason_code: Optional[str] = None,
) -> None:
    """Stamp the end of a blocked interval for (*version*, *source*). See
    module docstring "Three write operations" for the pairing rule this
    enforces and the atomicity guarantee. The no-op refusal below is the
    designed steady state for a caller that re-attempts the same
    block-end every cycle once the interval is already closed (or was
    never opened) -- it carries the distinct ERROR_NO_OP key precisely so
    callers can tell it apart from genuine ledger corruption.

    *block_class* is the class of the resolution: "human" when a human
    had to clear the block, "any" when the system cleared it itself.
    It, *cause* and *reason_code* are written only when given, validated
    as for append_block_start()."""
    optional = _optional_fields(block_class, cause, reason_code)
    records = parse_records(ledger_path)
    open_by_source = _open_blocks(records, version)
    if source not in open_by_source:
        raise LedgerError(
            ERROR_NO_OP,
            f"version {version!r} source {source!r} has no open "
            "block-start to close",
            "append a block-start for this source first, or confirm the "
            "caller is not closing a block that was never opened",
        )
    _append_line(ledger_path, _boundary_record(version, KIND_END, source, timestamp, optional))


def mark_block_escalated(
    ledger_path: str,
    version: str,
    source: str,
    timestamp: str,
    *,
    reason_code: Optional[str] = None,
) -> None:
    """Mark the open block of (*version*, *source*) escalated to a human,
    so it reads class "human" from now on. Appends one block-escalate
    line (class "human", plus *reason_code* when given); no earlier line
    changes, and the interval stays open.

    Raises LedgerError ERROR_NO_OP, writing nothing, when there is no open
    block for (*version*, *source*), or when the open block already reads
    "human" (its block-start says so, or it was already escalated) -- the
    same "already in the requested state" steady state as the other two
    writers."""
    optional = _optional_fields(CLASS_HUMAN, None, reason_code)
    records = parse_records(ledger_path)
    open_by_source = _open_blocks(records, version)
    state = open_by_source.get(source)
    if state is None:
        raise LedgerError(
            ERROR_NO_OP,
            f"version {version!r} source {source!r} has no open "
            "block-start to escalate",
            "append a block-start for this source first, or confirm the "
            "caller is escalating the version the block was opened under",
        )
    if state["escalated"] or record_class(state["start"]) == CLASS_HUMAN:
        raise LedgerError(
            ERROR_NO_OP,
            f"version {version!r} source {source!r} already reads class "
            f"{CLASS_HUMAN!r} (open since {state['start']['timestamp']})",
            "nothing to do: the open block is already escalated; close it "
            "with a block-end when it is resolved",
        )
    _append_line(
        ledger_path, _boundary_record(version, KIND_ESCALATE, source, timestamp, optional)
    )


# ---------------------------------------------------------------------------
# blocked_minutes — the single reader
# ---------------------------------------------------------------------------


def _intervals_for_version(records: list, version: str, window_closed_at: datetime) -> list:
    """Return a list of (start, end) datetime pairs for *version*, pairing
    block-start/block-end per source in file order. A source still open
    at the end of the replay is closed at *window_closed_at* — "still
    blocked when the RC shipped" contributes only up to the window edge.
    block-escalate lines carry no boundary and are skipped: a block's
    class never changes whether or how long it counts."""
    open_by_source: dict = {}
    intervals = []
    for record in records:
        if record["version"] != version or record["kind"] == KIND_ESCALATE:
            continue
        source = record["source"]
        ts = try_parse_iso(str(record["timestamp"]), naive_as_utc=False)
        if record["kind"] == "block-start":
            if source in open_by_source:
                raise LedgerError(
                    "blocked-ledger.malformed",
                    f"version {version!r} source {source!r} has two "
                    "block-start events with no block-end between them",
                    "repair the ledger to remove the duplicate start, or "
                    "append the missing block-end",
                )
            open_by_source[source] = ts
        else:  # block-end
            if source not in open_by_source:
                raise LedgerError(
                    "blocked-ledger.malformed",
                    f"version {version!r} source {source!r} has a "
                    "block-end event with no matching open block-start",
                    "repair the ledger to remove the orphaned block-end, "
                    "or append the missing block-start ahead of it",
                )
            intervals.append((open_by_source.pop(source), ts))
    for start in open_by_source.values():
        intervals.append((start, window_closed_at))
    return intervals


def _clamp(start: datetime, end: datetime, window_opened_at: datetime, window_closed_at: datetime):
    """Clamp one (start, end) interval to [window_opened_at,
    window_closed_at]. Returns None when the interval falls entirely
    outside the window (contributes nothing)."""
    clamped_start = max(start, window_opened_at)
    clamped_end = min(end, window_closed_at)
    if clamped_start >= clamped_end:
        return None
    return (clamped_start, clamped_end)


def _merge(intervals: list) -> list:
    """Merge overlapping or touching (start, end) pairs, sorted by
    start."""
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda pair: pair[0])
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def blocked_minutes(
    ledger_path: str,
    version: str,
    window_opened_at: str,
    window_closed_at: str,
) -> float:
    """Return the merged, clamped blocked-minutes total for *version*
    within [window_opened_at, window_closed_at]. See the module
    docstring's "Read path" section for the full contract, including the
    absent-ledger-means-zero rule and the exact LedgerError conditions."""
    opened_dt = try_parse_iso(str(window_opened_at), naive_as_utc=False)
    if opened_dt is None:
        raise LedgerError(
            "blocked-ledger.invalid-window",
            f"window_opened_at {window_opened_at!r} is not a parseable "
            "ISO-8601 timestamp",
            "pass the RC's own opened timestamp in ISO-8601, e.g. "
            "2026-09-18T09:15:22+00:00",
        )
    closed_dt = try_parse_iso(str(window_closed_at), naive_as_utc=False)
    if closed_dt is None:
        raise LedgerError(
            "blocked-ledger.invalid-window",
            f"window_closed_at {window_closed_at!r} is not a parseable "
            "ISO-8601 timestamp",
            "pass the RC's own closed/shipped timestamp in ISO-8601, e.g. "
            "2026-09-18T09:15:22+00:00",
        )

    records = parse_records(ledger_path)
    if not records:
        return 0.0

    raw_intervals = _intervals_for_version(records, version, closed_dt)
    if not raw_intervals:
        return 0.0

    clamped = []
    for start, end in raw_intervals:
        result = _clamp(start, end, opened_dt, closed_dt)
        if result is not None:
            clamped.append(result)
    if not clamped:
        return 0.0

    merged = _merge(clamped)
    total_seconds = sum((end - start).total_seconds() for start, end in merged)
    return total_seconds / 60.0


# ---------------------------------------------------------------------------
# block_intervals — one entry per block, with its class and cause
# ---------------------------------------------------------------------------


def _block_entry(start: dict) -> dict:
    """A still-open block entry built from its block-start record."""
    return {
        "version": start["version"],
        "source": start["source"],
        "start": start["timestamp"],
        "end": None,
        "class": record_class(start),
        "cause": record_cause(start),
        "reason_code": record_reason_code(start),
        "escalated": False,
        "escalated_at": None,
        "resolution_class": None,
        "resolution_reason_code": None,
    }


def block_intervals(ledger_path: str, version: Optional[str] = None) -> list:
    """Return one dict per block recorded for *version* (every version,
    NO_RC_VERSION included, when *version* is None), in block-start file
    order. Keys: version, source, start, end (None while open), class,
    cause, reason_code, escalated, escalated_at, resolution_class,
    resolution_reason_code — see the module docstring's "Read path" for
    the meaning of each and the rule that gives a block its class.

    An absent or empty ledger returns []. Raises LedgerError
    ``blocked-ledger.malformed`` under the same conditions as
    blocked_minutes(), and for a block-escalate with no open block to
    mark."""
    records = parse_records(ledger_path)
    entries: list = []
    open_by_pair: dict = {}
    for record in records:
        if version is not None and record["version"] != version:
            continue
        pair = (record["version"], record["source"])
        kind = record["kind"]
        if kind == KIND_START:
            if pair in open_by_pair:
                raise LedgerError(
                    "blocked-ledger.malformed",
                    f"version {pair[0]!r} source {pair[1]!r} has two "
                    "block-start events with no block-end between them",
                    "repair the ledger to remove the duplicate start, or "
                    "append the missing block-end",
                )
            entry = _block_entry(record)
            open_by_pair[pair] = entry
            entries.append(entry)
            continue
        entry = open_by_pair.get(pair)
        if entry is None:
            raise LedgerError(
                "blocked-ledger.malformed",
                f"version {pair[0]!r} source {pair[1]!r} has a {kind} "
                "event with no matching open block-start",
                f"repair the ledger to remove the orphaned {kind}, or "
                "append the missing block-start ahead of it",
            )
        if kind == KIND_ESCALATE:
            if not entry["escalated"]:
                entry["escalated"] = True
                entry["escalated_at"] = record["timestamp"]
            entry["class"] = CLASS_HUMAN
        else:  # block-end
            entry["end"] = record["timestamp"]
            entry["resolution_class"] = record_class(record)
            entry["resolution_reason_code"] = record_reason_code(record)
            if entry["resolution_class"] == CLASS_HUMAN:
                entry["class"] = CLASS_HUMAN
            del open_by_pair[pair]
    return entries


# ---------------------------------------------------------------------------
# Path convention helper
# ---------------------------------------------------------------------------


def ledger_path_for_project(kanban_root: str, project_name: str) -> str:
    """Return the standard ledger path:
    <kanban_root>/projects/<project_name>/metrics/blocked-intervals.jsonl."""
    return os.path.join(kanban_root, "projects", project_name, "metrics", "blocked-intervals.jsonl")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    """Run the blocked-interval ledger CLI.

    ``start|end|escalate LEDGER VERSION SOURCE TIMESTAMP`` stamps one event
    and prints ``{"written": true}``; ``read LEDGER VERSION OPENED_AT
    CLOSED_AT`` prints ``{"blocked_minutes": N}``. Exit codes: 0 done; 1 a
    ledger error, reported through the dual-channel error contract; 2 usage
    error (argparse).
    """
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Append or read the per-project blocked-interval ledger. "
            "'start'/'end' stamp one boundary event, optionally carrying the "
            "block's class, cause and reason code. 'escalate' marks an open "
            "block as needing a human (class human) without closing it. "
            "'read' prints the merged, clamped blocked-minutes total for a "
            "version, clamped to its own opened/closed window, as JSON."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_start = subparsers.add_parser("start", help="Stamp a block-start event.")
    p_end = subparsers.add_parser("end", help="Stamp a block-end event.")
    p_escalate = subparsers.add_parser(
        "escalate",
        help="Mark the open block of a source escalated to a human (class human).",
    )
    for subparser in (p_start, p_end, p_escalate):
        subparser.add_argument(
            "ledger_path",
            help="path to the project's blocked-intervals.jsonl ledger (created on first write)",
        )
        subparser.add_argument(
            "version",
            help=(
                "the RC's release version the event belongs to, e.g. v2.24.0; "
                f"{NO_RC_VERSION} when the project's Active RC is none"
            ),
        )
        subparser.add_argument(
            "source",
            help="what blocked the RC, e.g. halt:project, halt:global or task:<TASK-ID>",
        )
        subparser.add_argument(
            "timestamp",
            help="ISO-8601 time of the boundary, e.g. 2026-09-18T09:15:22+00:00",
        )
        subparser.add_argument(
            "--reason-code",
            dest="reason_code",
            default=None,
            help=(
                "why the block was raised (start), how it was resolved (end) "
                "or why it was escalated, e.g. agent_unclassified; omitted when not given"
            ),
        )

    p_start.add_argument(
        "--class",
        dest="block_class",
        type=str.lower,
        choices=VALID_CLASSES,
        default=None,
        help="class known when the block was raised; a record without it reads as any",
    )
    p_end.add_argument(
        "--class",
        dest="block_class",
        type=str.lower,
        choices=VALID_CLASSES,
        default=None,
        help="class of the resolution: human when a human had to clear the block; "
        "a record without it reads as any",
    )
    for subparser in (p_start, p_end):
        subparser.add_argument(
            "--cause",
            type=str.lower,
            choices=VALID_CAUSES,
            default=None,
            help="who raised the block; a record without it reads as chain",
        )

    p_read = subparsers.add_parser("read", help="Print the merged, clamped blocked-minutes total.")
    p_read.add_argument(
        "ledger_path",
        help="path to the project's blocked-intervals.jsonl ledger (absent reads as zero)",
    )
    p_read.add_argument("version", help="the RC's release version to total, e.g. v2.24.0")
    p_read.add_argument(
        "window_opened_at",
        help="ISO-8601 time the RC opened; intervals are clamped to start no earlier",
    )
    p_read.add_argument(
        "window_closed_at",
        help="ISO-8601 time the RC closed or shipped; intervals are clamped to end no later",
    )

    for subparser in (p_start, p_end, p_escalate, p_read):
        subparser.add_argument(
            "--project",
            default=None,
            help=(
                "project the ledger belongs to; a refusal is recorded in its "
                "refusal ledger (default: PGAI_PROJECT_NAME)"
            ),
        )

    args = parser.parse_args()

    try:
        if args.command in ("start", "end"):
            writer = append_block_start if args.command == "start" else append_block_end
            writer(
                args.ledger_path, args.version, args.source, args.timestamp,
                block_class=args.block_class, cause=args.cause, reason_code=args.reason_code,
            )
            sys.stdout.write(json.dumps({"written": True}) + "\n")
        elif args.command == "escalate":
            mark_block_escalated(
                args.ledger_path, args.version, args.source, args.timestamp,
                reason_code=args.reason_code,
            )
            sys.stdout.write(json.dumps({"written": True}) + "\n")
        elif args.command == "read":
            minutes = blocked_minutes(
                args.ledger_path, args.version, args.window_opened_at, args.window_closed_at
            )
            sys.stdout.write(json.dumps({"blocked_minutes": minutes}) + "\n")
    except LedgerError as exc:
        from pgai_agent_kanban.lib.error_contract import emit_error_contract, format_error_contract

        if exc.error == ERROR_NO_OP:
            # Not a refusal: the boundary was already in the state asked
            # for, the designed steady state of a caller re-attempting it
            # every cycle. The block still reaches stderr (the wake adapter
            # keys on its error line) but no refusal ledger records it.
            print(format_error_contract(exc.error, exc.message, exc.remedy), file=sys.stderr)
            return 1
        # CENSUS CLASS: CONVERT (docs/blocking-site-census.md) — a
        # malformed ledger never halts a release path; it speaks the
        # dual-channel contract and yields no number for the caller to
        # display instead. The refusal is recorded against --project.
        # CENSUS-ANCHOR: blocked-ledger-01
        emit_error_contract(exc.error, exc.message, exc.remedy, project=args.project)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
