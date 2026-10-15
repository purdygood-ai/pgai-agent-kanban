"""
block_record.py — the durable ``## Block Record`` section of a task status.md.

Every site that blocks a task records why through this one module, so
OVERWATCH's block triage can read a machine-classifiable record instead of
``## Blockers`` prose. The record is ten ``key: value`` lines, always in
this order::

    ## Block Record
    reason_code: worktree_create_failed
    owner_role: CODER
    attempts: 0
    first_blocked_at: 2026-10-04T15:43:09+00:00
    last_blocked_at: 2026-10-04T15:43:09+00:00
    cooldown_until: none
    evidence: worktree=gone; branch=ai_feature/example; ahead=2; merged=no
    resume: fresh
    requeues: 0
    requeue_reasons: none

(values above are an example). Field meanings:

    reason_code       why the task blocked; one of REASON_CODES (closed).
    owner_role        the role whose work the task is; one of OWNER_ROLES.
    attempts          triage attempts made on this block so far.
    first_blocked_at  when the first block of this record was written.
    last_blocked_at   when the latest block was written.
    cooldown_until    no triage action before this time; ``none`` for none.
    evidence          ``; ``-joined ``key=value`` pointers the block site
                      gathered, conventionally ``worktree=<path|gone>;
                      branch=<name>; ahead=<n>; merged=<yes|no>;
                      batch_log=<path>#<from>-<to>; diag=<log:line>``;
                      ``none`` when the site gave none.
    resume            ``branch`` to re-dispatch onto the existing feature
                      branch, ``fresh`` to start a new one.
    requeues          how many times triage has returned the task to its
                      owner over the whole life of this record, across
                      every re-block and block epoch; triage compares it
                      with ``[overwatch] requeue_budget``.
    requeue_reasons   the reason_code of each of those requeues, oldest
                      first, ``, ``-joined; ``none`` before the first.

``requeues`` and ``requeue_reasons`` are optional on read: a record written
before they existed reads as ``requeues: 0`` and ``requeue_reasons: none``,
and its next write adds both lines.

Lifecycle
---------
* ``write_block_record`` — the block write. The first one creates the
  section (appended at the end of status.md) with ``attempts: 0``,
  ``cooldown_until: none``, ``resume: fresh`` and both timestamps set to
  now, ``requeues: 0`` and ``requeue_reasons: none``. A re-block on a task
  that already carries a record keeps ``first_blocked_at``, ``attempts``,
  ``cooldown_until``, ``resume``, ``requeues`` and ``requeue_reasons``, and
  replaces ``reason_code``, ``owner_role``, ``evidence`` and
  ``last_blocked_at``. A block write never changes ``attempts`` or
  ``requeues``.
* ``update_block_record`` — the triage entry point: sets ``attempts`` (or
  adds one), ``cooldown_until`` and ``resume`` on an existing record,
  records a requeue (``requeues`` plus one, its reason_code appended to
  ``requeue_reasons``), or restarts the block epoch (``first_blocked_at``
  becomes ``last_blocked_at``, ``attempts`` 0, ``cooldown_until`` none) once
  triage has verified that its last action worked and the latest block is a
  new incident. An epoch restart keeps ``requeues`` and ``requeue_reasons``:
  they are the budget that stops blocks alternating between reasons from
  being requeued forever.
* ``remove_block_record`` — drops the section; called when the task
  reaches DONE or WONT-DO. Removing an absent record is a no-op.
* The wake's ``promote_blocked_to_backlog`` leaves the section alone, so a
  record survives the task's return to BACKLOG. So does the wake's budget
  stop (``requeue_after_budget_stop``, a watchdog kill at the batch or
  project clock rather than the task's own limit): every path but DONE and
  WONT-DO keeps ``requeues`` and ``requeue_reasons``.
* ``render_block_record`` / ``strip_block_record`` are the text-level forms
  of a block write and a removal, for a writer that commits status.md in
  one atomic write with other sections: the status-writer tool
  (``pgai_agent_kanban.task_status``) records an agent self-block this way
  on every BLOCKED write and drops the record on DONE / WONT-DO.

Agent reason codes and typed action arguments
---------------------------------------------
``AGENT_REASON_CODES`` is the closed ``agent_<...>`` list an agent passes
to the status-writer tool's ``--reason-code``; ``agent_unclassified`` is
recorded when it passes none. ``agent_operator_data_action`` carries typed
``key=value`` action arguments (``--action-args``) validated by
``parse_action_args`` against ``ACTION_VERBS``: ``verb`` names an
allowlisted verb and the remaining keys are exactly that verb's typed
arguments. They are stored in ``evidence`` (``verb`` first) and read back
with ``read_action_args``.

Every write reads status.md, rewrites only the ``## Block Record`` section
through the shared ``pgai_agent_kanban.task_status`` section primitives,
and replaces the file atomically (``pgai_agent_kanban.lib.atomic_write``),
keeping its mode: every other section is preserved byte-for-byte, and a
concurrent reader sees the old file or the new one, never a partial one.

Refusals
--------
An unknown ``reason_code`` or ``owner_role``, a missing status.md, an
update with nothing to change or no record to change, and a malformed
update value are refused: the library raises ``BlockRecordError`` (with
``error_key`` / ``message`` / ``remedy``), the CLI prints the dual-channel
error contract and exits 1, and status.md is not touched. A refused write
is a lost record, never a lost block: block sites call the helper beside
their own BLOCKED transition and never let its exit status stop that
transition.

Reading is liberal: keys match in any case, with ``-``, ``.`` or a space
in place of ``_``, any spacing around the colon, and reason codes and
roles in any case. Writing is canonical.

Usage (CLI; the bash wrapper team/scripts/lib/block_record.sh calls it):
    python3 -m pgai_agent_kanban.overwatch.block_record write \\
        [--now ISO] STATUS_FILE REASON_CODE OWNER_ROLE [EVIDENCE ...]
    python3 -m pgai_agent_kanban.overwatch.block_record read \\
        STATUS_FILE [--field NAME [--default TEXT]]
    python3 -m pgai_agent_kanban.overwatch.block_record update STATUS_FILE \\
        [--restart-epoch] [--attempts N | --bump-attempts] \\
        [--cooldown-until ISO|none] [--resume branch|fresh] \\
        [--record-requeue REASON_CODE]
    python3 -m pgai_agent_kanban.overwatch.block_record remove STATUS_FILE
    python3 -m pgai_agent_kanban.overwatch.block_record codes \\
        [--owner-roles | --agent | --action-verbs]

Exit codes: 0 success; 1 refused (dual-channel error printed, nothing
written); 3 ``read`` without ``--field`` found no record.

Usage (import):
    from pgai_agent_kanban.overwatch.block_record import (
        BlockRecord, BlockRecordError, write_block_record, load_block_record,
        update_block_record, remove_block_record, render_block_record,
        strip_block_record, parse_action_args, read_action_args,
    )
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import os
import pathlib
import re
import shlex
import sys
from pathlib import Path
from typing import Iterable, NoReturn, Optional, Sequence

# Run by file path (``python3 .../block_record.py``) or loaded from a file
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
from pgai_agent_kanban.task_status import (  # noqa: E402
    TaskStatusError,
    read_section,
    remove_section,
    set_or_append_section,
)

# ---------------------------------------------------------------------------
# Closed vocabularies
# ---------------------------------------------------------------------------

#: The status.md section this module owns.
SECTION_HEADING = "Block Record"

#: The ten record keys, in the order they are written.
FIELD_ORDER: tuple[str, ...] = (
    "reason_code",
    "owner_role",
    "attempts",
    "first_blocked_at",
    "last_blocked_at",
    "cooldown_until",
    "evidence",
    "resume",
    "requeues",
    "requeue_reasons",
)

#: Keys a record may lack and still read: a record written before they
#: existed reads them at their initial values (``INITIAL_REQUEUES`` and
#: ``none``).
OPTIONAL_FIELDS: tuple[str, ...] = ("requeues", "requeue_reasons")

#: The roles a blocked task can belong to.
OWNER_ROLES: tuple[str, ...] = ("CODER", "WRITER", "TESTER", "CM", "PM")

#: Reason codes an agent records when it blocks its own task. The closed
#: ``agent_<...>`` list; ``agent_unclassified`` is the catch-all.
AGENT_REASON_CODES: tuple[str, ...] = (
    "agent_operator_data_action",
    "agent_unclassified",
)

#: The agent reason code that carries typed action arguments.
OPERATOR_DATA_ACTION = "agent_operator_data_action"

#: Every reason code a Block Record may carry. Codes the block-triage
#: classifier can act on come first, then the codes it always leaves for a
#: human, then the agent self-block codes.
REASON_CODES: tuple[str, ...] = (
    "per_task_timeout",
    "provider_refusal",
    "zero_progress",
    "transient_api_error",
    "gate_merge_not_found",
    "gate_dirty_worktree",
    "gate_empty_commit",
    "worktree_create_failed",
    "ship_policy_halt",
    "missing_readme",
    "invalid_assignment",
    "invalid_effort_config",
    "invalid_state",
    "branch_prefix_failed",
    "workflow_type_unparseable",
    "plugin_load_failed",
    "source_branch_missing",
    "gate_merge_evidence_not_found",
) + AGENT_REASON_CODES

#: How the next dispatch treats the task's feature branch.
RESUME_MODES: tuple[str, ...] = ("branch", "fresh")

#: The value of cooldown_until / evidence when there is none.
NONE_VALUE = "none"

#: Values a first block write starts the triage fields at.
INITIAL_ATTEMPTS = 0
INITIAL_RESUME = "fresh"
INITIAL_REQUEUES = 0

#: How ``requeue_reasons`` joins its reason codes when written.
REQUEUE_REASONS_SEPARATOR = ", "

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_RECORD_ABSENT = 3

_ATOMIC_PREFIX = ".block_record_tmp_"
_MODULE_FILE = str(pathlib.Path(__file__).resolve())


class BlockRecordError(TaskStatusError):
    """A refused Block Record operation, carrying the dual-channel fields.

    A status.md-writer refusal like its base class: library callers get
    ``.error_key`` / ``.message`` / ``.remedy`` to log, re-emit or surface;
    the CLI is the one place that prints them.
    """


def _refuse(error_key: str, message: str, remedy: str) -> NoReturn:
    """Raise a BlockRecordError — the one refusal site every check shares.

    Nothing has been written when this runs: every check happens before the
    atomic write.
    """
    # CONVERT: a refused Block Record write or update leaves status.md
    # untouched and the caller's own BLOCKED transition goes ahead; the cost
    # is a missing or stale triage record, never a stopped task or release.
    # CENSUS-ANCHOR: block-record-refusal-01
    raise BlockRecordError(error_key=error_key, message=message, remedy=remedy)


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class BlockRecord:
    """One parsed ``## Block Record``, its fields in written order."""

    reason_code: str
    owner_role: str
    attempts: int
    first_blocked_at: str
    last_blocked_at: str
    cooldown_until: str
    evidence: str
    resume: str
    requeues: int = INITIAL_REQUEUES
    requeue_reasons: str = NONE_VALUE

    def to_body(self) -> str:
        """Render the canonical section body (no heading, no trailing newline)."""
        return "\n".join(f"{name}: {getattr(self, name)}" for name in FIELD_ORDER)

    def as_dict(self) -> dict:
        """The record as an ordered ``{field: value}`` dict."""
        return {name: getattr(self, name) for name in FIELD_ORDER}

    def requeue_reason_list(self) -> list[str]:
        """``requeue_reasons`` as a list of reason codes, oldest first (``[]`` for none)."""
        return parse_requeue_reasons(self.requeue_reasons)


def _ident(value: str) -> str:
    """Normalise an identifier: lower case, ``-``/``.``/space read as ``_``."""
    return re.sub(r"[-.\s]+", "_", value.strip()).lower()


def normalize_reason_code(value: str) -> str:
    """Return *value* as a canonical reason code (validity not checked)."""
    return _ident(value)


def normalize_owner_role(value: str) -> str:
    """Return *value* as a canonical owner role (validity not checked)."""
    return value.strip().upper()


def is_known_reason_code(value: str) -> bool:
    """True when *value*, normalised, is in REASON_CODES."""
    return normalize_reason_code(value) in REASON_CODES


def is_known_owner_role(value: str) -> bool:
    """True when *value*, normalised, is in OWNER_ROLES."""
    return normalize_owner_role(value) in OWNER_ROLES


_FIELD_LINE_RE = re.compile(r"^[ \t]*([A-Za-z][A-Za-z0-9_.\- \t]*?)[ \t]*:[ \t]*(.*?)[ \t\r]*$")


def parse_fields(body: str) -> dict[str, str]:
    """Return the ``key: value`` lines of a record body as a dict.

    Keys are normalised (:func:`_ident`); the first occurrence of a key
    wins; lines that are not ``key: value`` are skipped. A value may itself
    contain colons (``diag=wake.log:12``) — only the first colon splits.
    """
    fields: dict[str, str] = {}
    for line in body.splitlines():
        m = _FIELD_LINE_RE.match(line)
        if m is None:
            continue
        fields.setdefault(_ident(m.group(1)), m.group(2))
    return fields


def _canonical_iso(value: str) -> Optional[str]:
    """Return *value* as canonical UTC ISO-8601, or None when it does not parse."""
    parsed = try_parse_iso(value)
    return format_iso(parsed) if parsed is not None else None


def _canonical_cooldown(value: str) -> Optional[str]:
    if value.strip().lower() == NONE_VALUE:
        return NONE_VALUE
    return _canonical_iso(value)


def _parse_attempts(value: str) -> Optional[int]:
    text = value.strip()
    return int(text) if text.isdigit() else None


def parse_requeue_reasons(value: str) -> list[str]:
    """Split a ``requeue_reasons`` value into reason codes, oldest first.

    Read liberally: codes may be separated by commas, semicolons or
    whitespace, in any case, with ``-``/``.`` for ``_``; ``none`` or an
    empty value gives ``[]``. Codes are not checked against REASON_CODES.
    """
    if value.strip().lower() in ("", NONE_VALUE):
        return []
    return [normalize_reason_code(part) for part in re.split(r"[,;\s]+", value) if part.strip()]


def format_requeue_reasons(codes: Iterable[str]) -> str:
    """Join reason codes into the canonical ``requeue_reasons`` value (``none`` for none)."""
    cleaned = [normalize_reason_code(code) for code in codes if code and code.strip()]
    return REQUEUE_REASONS_SEPARATOR.join(cleaned) if cleaned else NONE_VALUE


def record_from_fields(fields: dict[str, str], status_file: "Path | str | None" = None) -> BlockRecord:
    """Build a BlockRecord from parsed fields, refusing a malformed one.

    ``reason_code`` and ``owner_role`` are normalised but not checked
    against the closed vocabularies — a reader decides what an unknown
    code means (the triage classifier escalates it). Every other field must
    be present and well-formed. *status_file*, when given, is named in a
    refusal's remedy.

    Raises:
        BlockRecordError: ``block-record.unreadable``, naming every missing
            or malformed field.
    """
    problems: list[str] = []
    missing = [name for name in FIELD_ORDER if name not in fields and name not in OPTIONAL_FIELDS]
    if missing:
        problems.append("missing " + ", ".join(missing))

    attempts = _parse_attempts(fields.get("attempts", "0"))
    if attempts is None:
        problems.append(f"attempts {fields['attempts']!r} is not a whole number")
    requeues = _parse_attempts(fields.get("requeues", str(INITIAL_REQUEUES)))
    if requeues is None:
        problems.append(f"requeues {fields['requeues']!r} is not a whole number")
    stamps = {}
    for name in ("first_blocked_at", "last_blocked_at"):
        stamps[name] = _canonical_iso(fields.get(name, "")) if name in fields else ""
        if name in fields and stamps[name] is None:
            problems.append(f"{name} {fields[name]!r} is not an ISO-8601 time")
    cooldown = _canonical_cooldown(fields.get("cooldown_until", NONE_VALUE))
    if cooldown is None:
        problems.append(f"cooldown_until {fields['cooldown_until']!r} is neither an ISO-8601 time nor none")
    resume = fields.get("resume", INITIAL_RESUME).strip().lower()
    if resume not in RESUME_MODES:
        problems.append(f"resume {fields['resume']!r} is not one of {'|'.join(RESUME_MODES)}")

    if problems:
        _refuse(
            "block-record.unreadable",
            "the ## Block Record section cannot be read: " + "; ".join(problems),
            (
                f"python3 {_MODULE_FILE} write {shlex.quote(str(status_file))} agent_unclassified CODER"
                if status_file is not None
                else f"python3 {_MODULE_FILE} write --help"
            ),
        )
    return BlockRecord(
        reason_code=normalize_reason_code(fields["reason_code"]),
        owner_role=normalize_owner_role(fields["owner_role"]),
        attempts=attempts,  # type: ignore[arg-type]  # checked above
        first_blocked_at=stamps["first_blocked_at"],  # type: ignore[arg-type]
        last_blocked_at=stamps["last_blocked_at"],  # type: ignore[arg-type]
        cooldown_until=cooldown,  # type: ignore[arg-type]
        evidence=fields["evidence"].strip() or NONE_VALUE,
        resume=resume,
        requeues=requeues,  # type: ignore[arg-type]  # checked above
        requeue_reasons=format_requeue_reasons(parse_requeue_reasons(fields.get("requeue_reasons", NONE_VALUE))),
    )


def _record_body(text: str) -> Optional[str]:
    """The section body, or None when status.md carries no live record.

    An empty body or the literal ``none`` reads as no record.
    """
    body = read_section(text, SECTION_HEADING)
    if body is None or not body.strip() or body.strip().lower() == NONE_VALUE:
        return None
    return body


def read_block_record(text: str, status_file: "Path | str | None" = None) -> Optional[BlockRecord]:
    """Return the Block Record in status.md *text*, or None when there is none.

    *status_file*, when given, is named in a refusal's remedy.

    Raises:
        BlockRecordError: ``block-record.unreadable`` for a malformed record.
    """
    body = _record_body(text)
    if body is None:
        return None
    return record_from_fields(parse_fields(body), status_file)


def format_evidence(parts: Iterable[str]) -> str:
    """Join evidence tokens into the one-line ``evidence`` value.

    Each token is stripped and any line break in it becomes a space (the
    record is one line per field); empty tokens are dropped; the rest are
    joined with ``"; "``. No tokens gives ``none``.
    """
    cleaned = [re.sub(r"\s*[\r\n]+\s*", " ", part).strip() for part in parts]
    cleaned = [part for part in cleaned if part]
    return "; ".join(cleaned) if cleaned else NONE_VALUE


def parse_evidence(value: str) -> dict[str, str]:
    """Split an ``evidence`` value into ``{key: value}`` pairs.

    Tokens are split on ``;``; each ``key=value`` token is kept (key
    normalised as an identifier, first occurrence wins); a token without
    ``=`` is skipped. ``none`` gives ``{}``.
    """
    pairs: dict[str, str] = {}
    if value.strip().lower() == NONE_VALUE:
        return pairs
    for token in value.split(";"):
        key, sep, val = token.partition("=")
        if sep and key.strip():
            pairs.setdefault(_ident(key), val.strip())
    return pairs


# ---------------------------------------------------------------------------
# Typed action arguments (agent_operator_data_action)
# ---------------------------------------------------------------------------
#
# An agent that blocks because an operator-data action is needed names the
# action as typed ``key=value`` pairs, never prose: ``verb`` picks one of
# ACTION_VERBS and the rest are exactly that verb's arguments. The pairs are
# stored in the record's ``evidence`` line, ``verb`` first and then the
# verb's arguments in their declared order, e.g. (values are an example)::
#
#     evidence: verb=restamp-fixed-in; project=pgai-agent-kanban; version=v2.28.0; bugs=BUG-0412,BUG-0413  (example)
#
# so ``parse_evidence`` (or ``read_action_args``) reads them back.


@dataclasses.dataclass(frozen=True)
class ActionArgSpec:
    """One typed argument of an allowlisted verb.

    ``pattern`` is matched against the canonical value (after the liberal
    reading ``_canonical_action_value`` applies); ``shape`` describes the
    accepted form for a refusal message.
    """

    name: str
    pattern: "re.Pattern[str]"
    shape: str


#: The key naming the verb an operator-data action asks for.
ACTION_VERB_KEY = "verb"

#: Allowlisted verbs and their typed arguments (every argument is required).
#: The closed list ``--action-args`` is validated against; extend it here.
ACTION_VERBS: dict[str, tuple[ActionArgSpec, ...]] = {
    "restamp-fixed-in": (
        ActionArgSpec("project", re.compile(r"^[a-z0-9][a-z0-9-]*$"), "a project name, e.g. pgai-agent-kanban"),
        ActionArgSpec("version", re.compile(r"^v\d+\.\d+\.\d+$"), "a release version, e.g. v2.28.0"),
        ActionArgSpec("bugs", re.compile(r"^BUG-\d{4}(?:,BUG-\d{4})*$"), "comma-separated bug IDs, e.g. BUG-0412,BUG-0413"),
    ),
}


class ActionArgsError(ValueError):
    """Typed action arguments that do not validate; ``problems`` names each fault."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


def _split_action_tokens(values: Iterable[str]) -> list[str]:
    """Split raw ``--action-args`` values into ``key=value`` tokens.

    Each value may hold several pairs separated by whitespace or ``;``.
    """
    tokens: list[str] = []
    for value in values:
        tokens.extend(t for t in re.split(r"[;\s]+", value or "") if t)
    return tokens


def _canonical_action_value(key: str, value: str) -> str:
    """Read an argument value liberally: the canonical form it is checked in."""
    value = value.strip()
    if key == ACTION_VERB_KEY:
        value = re.sub(r"\.sh$", "", value.lower()).replace("_", "-")
    elif key == "version" and re.match(r"^[Vv]?\d", value):
        value = "v" + value.lstrip("vV")
    elif key == "bugs":
        value = ",".join(part.strip().upper() for part in value.split(",") if part.strip())
    return value


def parse_action_args(values: Iterable[str]) -> dict[str, str]:
    """Validate typed action arguments; return them canonical and in order.

    Args:
        values: Raw values, each one or more ``key=value`` pairs separated
            by whitespace or ``;``. Keys read liberally (any case, ``-``
            for ``_``); values as :func:`_canonical_action_value` reads them.

    Returns:
        ``{"verb": ..., <arg>: ..., ...}``, verb first, then the verb's
        arguments in their declared order.

    Raises:
        ActionArgsError: naming every fault — no pairs, a token without
            ``=``, an empty value, an unknown or repeated key, a missing or
            unknown verb, a missing argument, or a value of the wrong shape.
    """
    problems: list[str] = []
    pairs: dict[str, str] = {}
    tokens = _split_action_tokens(values)
    if not tokens:
        raise ActionArgsError(["no key=value pairs were given"])
    for token in tokens:
        key, sep, value = token.partition("=")
        key = _ident(key)
        if not sep or not key:
            problems.append(f"{token!r} is not a key=value pair")
            continue
        if key in pairs:
            problems.append(f"{key} is given more than once")
            continue
        if not value.strip():
            problems.append(f"{key} has an empty value")
        pairs[key] = _canonical_action_value(key, value)

    verb = pairs.get(ACTION_VERB_KEY)
    specs = ACTION_VERBS.get(verb or "")
    if verb is None:
        problems.append(f"{ACTION_VERB_KEY}= is missing (one of: {', '.join(ACTION_VERBS)})")
    elif specs is None:
        problems.append(f"verb {verb!r} is not allowlisted (one of: {', '.join(ACTION_VERBS)})")
    if specs is not None:
        names = [spec.name for spec in specs]
        for key in pairs:
            if key != ACTION_VERB_KEY and key not in names:
                problems.append(f"{key} is not an argument of {verb} (expected: {', '.join(names)})")
        for spec in specs:
            value = pairs.get(spec.name)
            if value is None:
                problems.append(f"{spec.name}= is missing ({spec.shape})")
            elif value and not spec.pattern.match(value):
                problems.append(f"{spec.name} {value!r} is not {spec.shape}")
    if problems:
        raise ActionArgsError(problems)
    ordered = {ACTION_VERB_KEY: verb}
    ordered.update((spec.name, pairs[spec.name]) for spec in specs)  # type: ignore[union-attr]
    return ordered  # type: ignore[return-value]


def format_action_args(args: "dict[str, str]") -> list[str]:
    """Return validated action arguments as ``key=value`` evidence tokens."""
    return [f"{key}={value}" for key, value in args.items()]


def read_action_args(record: BlockRecord) -> dict[str, str]:
    """Return the typed action arguments a record's evidence carries.

    Only ``verb`` and the named verb's argument keys are returned; ``{}``
    when the evidence names no allowlisted verb. Values are returned as
    written — a triage action re-validates them with
    :func:`parse_action_args` before acting.
    """
    pairs = parse_evidence(record.evidence)
    specs = ACTION_VERBS.get(pairs.get(ACTION_VERB_KEY, ""))
    if specs is None:
        return {}
    keys = (ACTION_VERB_KEY, *(spec.name for spec in specs))
    return {key: pairs[key] for key in keys if key in pairs}


def example_action_args() -> str:
    """A runnable ``--action-args`` value for the first allowlisted verb."""
    return "verb=restamp-fixed-in project=pgai-agent-kanban version=v2.28.0 bugs=BUG-0412"  # example values


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------


def _read_status(status_file: Path) -> str:
    if not status_file.is_file():
        _refuse(
            "block-record.missing-status-file",
            f"no status.md found at {status_file}; a Block Record is only "
            "written into an existing task status file",
            f"ls -l {shlex.quote(str(status_file.parent))}",
        )
    try:
        return status_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        _refuse(
            "block-record.read-failed",
            f"cannot read {status_file}: {exc}",
            f"ls -l {shlex.quote(str(status_file))}",
        )


def _write_status(status_file: Path, new_text: str) -> None:
    """Replace status.md atomically, keeping the file's own mode."""
    try:
        mode = status_file.stat().st_mode & 0o7777
        atomic_write(status_file, new_text, prefix=_ATOMIC_PREFIX)
        os.chmod(status_file, mode)
    except OSError as exc:
        _refuse(
            "block-record.write-failed",
            f"failed writing {status_file}: {exc}",
            f"df -h {shlex.quote(str(status_file.parent))}; ls -ld {shlex.quote(str(status_file.parent))}",
        )


def _apply(status_file: Path, text: str, record: BlockRecord) -> BlockRecord:
    new_text = set_or_append_section(text, SECTION_HEADING, record.to_body())
    if new_text != text:
        _write_status(status_file, new_text)
    return record


def load_block_record(status_file: "Path | str") -> Optional[BlockRecord]:
    """Read *status_file*'s Block Record; None when it carries none.

    Raises:
        BlockRecordError: missing/unreadable status.md, or a malformed record.
    """
    return read_block_record(_read_status(Path(status_file)), status_file)


def _validated_code_and_role(status_file: Path, reason_code: str, owner_role: str) -> tuple[str, str]:
    code = normalize_reason_code(reason_code or "")
    role = normalize_owner_role(owner_role or "")
    example_role = role if role in OWNER_ROLES else "CODER"
    example_code = code if code in REASON_CODES else "agent_unclassified"
    example = (
        f"python3 {_MODULE_FILE} write {shlex.quote(str(status_file))} "
        f"{example_code} {example_role}"
    )
    if code not in REASON_CODES:
        _refuse(
            "block-record.unknown-reason-code",
            f"reason_code {reason_code!r} is not in the closed Block Record "
            f"vocabulary ({', '.join(REASON_CODES)}); nothing was written",
            f"python3 {_MODULE_FILE} codes; {example}",
        )
    if role not in OWNER_ROLES:
        _refuse(
            "block-record.unknown-owner-role",
            f"owner_role {owner_role!r} is not one of {'|'.join(OWNER_ROLES)}; "
            "nothing was written",
            f"python3 {_MODULE_FILE} codes --owner-roles; {example}",
        )
    return code, role


def _now_iso(now: "datetime.datetime | str | None", status_file: Path) -> str:
    if now is None:
        return format_iso(datetime.datetime.now(datetime.timezone.utc))
    if isinstance(now, datetime.datetime):
        return format_iso(now)
    canonical = _canonical_iso(now)
    if canonical is None:
        _refuse(
            "block-record.bad-time",
            f"--now {now!r} is not an ISO-8601 time",
            f"python3 {_MODULE_FILE} write --now 2026-01-01T00:00:00+00:00 "
            f"{shlex.quote(str(status_file))} agent_unclassified CODER",
        )
    return canonical


def _salvaged(body: str) -> dict[str, str]:
    """The carried-over fields of an existing record, field by field.

    A re-block keeps first_blocked_at, attempts, cooldown_until, resume,
    requeues and requeue_reasons. When an earlier record was hand-edited
    into a shape that does not parse, each of those is kept if it still
    reads and otherwise left out, so the re-block rewrites a clean record
    instead of refusing.
    """
    fields = parse_fields(body)
    kept: dict[str, str] = {}
    first = _canonical_iso(fields.get("first_blocked_at", ""))
    if first is not None:
        kept["first_blocked_at"] = first
    attempts = _parse_attempts(fields.get("attempts", ""))
    if attempts is not None:
        kept["attempts"] = str(attempts)
    cooldown = _canonical_cooldown(fields.get("cooldown_until", ""))
    if cooldown is not None:
        kept["cooldown_until"] = cooldown
    resume = fields.get("resume", "").strip().lower()
    if resume in RESUME_MODES:
        kept["resume"] = resume
    requeues = _parse_attempts(fields.get("requeues", ""))
    if requeues is not None:
        kept["requeues"] = str(requeues)
    if "requeue_reasons" in fields:
        kept["requeue_reasons"] = format_requeue_reasons(parse_requeue_reasons(fields["requeue_reasons"]))
    return kept


def write_block_record(
    status_file: "Path | str",
    reason_code: str,
    owner_role: str,
    evidence: "Sequence[str] | str" = (),
    *,
    now: "datetime.datetime | str | None" = None,
) -> BlockRecord:
    """Write (first block) or re-write (re-block) the Block Record.

    Args:
        status_file: The task's status.md; must exist.
        reason_code: One of REASON_CODES (any case; ``-``/``.`` read as ``_``).
        owner_role:  One of OWNER_ROLES (any case).
        evidence:    Evidence tokens (``key=value``), joined with ``"; "``;
                     a single string is taken as one token.
        now:         The block time; defaults to the current UTC time.

    Returns:
        The record as written.

    Raises:
        BlockRecordError: unknown reason_code or owner_role, a bad *now*, or
            an unreadable/unwritable status.md. Nothing is written.
    """
    path = Path(status_file)
    code, role = _validated_code_and_role(path, reason_code, owner_role)
    stamp = _now_iso(now, path)
    text = _read_status(path)
    return _apply(path, text, _block_write_record(text, code, role, evidence, stamp))


def _block_write_record(
    text: str, code: str, role: str, evidence: "Sequence[str] | str", stamp: str
) -> BlockRecord:
    """The record a block write at *stamp* leaves, given status.md *text*.

    A first block starts the triage fields fresh; a re-block keeps what
    :func:`_salvaged` reads from the existing record.
    """
    tokens = [evidence] if isinstance(evidence, str) else list(evidence)
    existing = _record_body(text)
    kept = _salvaged(existing) if existing is not None else {}
    return BlockRecord(
        reason_code=code,
        owner_role=role,
        attempts=int(kept.get("attempts", INITIAL_ATTEMPTS)),
        first_blocked_at=kept.get("first_blocked_at", stamp),
        last_blocked_at=stamp,
        cooldown_until=kept.get("cooldown_until", NONE_VALUE),
        evidence=format_evidence(tokens),
        resume=kept.get("resume", INITIAL_RESUME),
        requeues=int(kept.get("requeues", INITIAL_REQUEUES)),
        requeue_reasons=kept.get("requeue_reasons", NONE_VALUE),
    )


def render_block_record(
    text: str,
    reason_code: str,
    owner_role: str,
    evidence: "Sequence[str] | str" = (),
    *,
    now: "datetime.datetime | str | None" = None,
    status_file: "Path | str | None" = None,
) -> tuple[str, BlockRecord]:
    """Text-level block write: return *text* carrying the new record, and the record.

    The same record :func:`write_block_record` writes (first block or
    re-block), for a caller that commits status.md in its own single atomic
    write together with other sections — the status-writer tool writes
    ``## State`` and the record this way. Nothing is read or written here.

    Args:
        text:        Full status.md contents.
        reason_code: One of REASON_CODES.
        owner_role:  One of OWNER_ROLES.
        evidence:    Evidence tokens, as for :func:`write_block_record`.
        now:         The block time; defaults to the current UTC time.
        status_file: Named in a refusal's remedy; not opened.

    Raises:
        BlockRecordError: unknown reason_code or owner_role, or a bad *now*.
    """
    path = Path(status_file) if status_file is not None else Path("status.md")
    code, role = _validated_code_and_role(path, reason_code, owner_role)
    record = _block_write_record(text, code, role, evidence, _now_iso(now, path))
    return set_or_append_section(text, SECTION_HEADING, record.to_body()), record


def has_block_record(text: str) -> bool:
    """True when status.md *text* carries a live (non-empty, non-``none``) record."""
    return _record_body(text) is not None


def strip_block_record(text: str) -> str:
    """Text-level removal: *text* without its ``## Block Record`` section.

    Every other section is untouched; *text* comes back unchanged when it
    carries no record. :func:`remove_block_record` is the file form.
    """
    return remove_section(text, SECTION_HEADING)


def update_block_record(
    status_file: "Path | str",
    *,
    attempts: Optional[int] = None,
    bump_attempts: bool = False,
    cooldown_until: "datetime.datetime | str | None" = None,
    resume: Optional[str] = None,
    restart_epoch: bool = False,
    record_requeue: Optional[str] = None,
) -> BlockRecord:
    """Update the triage fields of an existing Block Record.

    Args:
        status_file:    The task's status.md; must carry a readable record.
        attempts:       Set ``attempts`` to this whole number.
        bump_attempts:  Add one to ``attempts`` (not with *attempts*).
        cooldown_until: A datetime, an ISO-8601 string, or ``"none"``.
        resume:         ``branch`` or ``fresh``.
        restart_epoch:  Start a new block epoch at the latest block:
                        ``first_blocked_at`` becomes ``last_blocked_at``,
                        ``attempts`` 0 and ``cooldown_until`` none, applied
                        first so the other arguments land on the new epoch.
                        ``resume`` keeps its value unless *resume* is given:
                        a branch a previous action resumed may hold work.
                        ``requeues`` and ``requeue_reasons`` are kept.
        record_requeue: Record one requeue of the task for this reason code
                        (any case; ``-``/``.`` read as ``_``): ``requeues``
                        goes up by one and the code is appended to
                        ``requeue_reasons``.

    Returns:
        The record as written. Every field not named keeps its value.

    Raises:
        BlockRecordError: nothing to update, conflicting or malformed values,
            no record, an unreadable record, or an unreadable/unwritable
            status.md. Nothing is written.
    """
    path = Path(status_file)
    example = f"python3 {_MODULE_FILE} update {shlex.quote(str(path))} --bump-attempts --cooldown-until none"
    if (
        attempts is None and not bump_attempts and cooldown_until is None and resume is None
        and not restart_epoch and record_requeue is None
    ):
        _refuse(
            "block-record.nothing-to-update",
            "a Block Record update names no field to change",
            example,
        )
    if attempts is not None and bump_attempts:
        _refuse(
            "block-record.conflicting-attempts",
            "an update cannot both set attempts and add one to it",
            example,
        )
    if attempts is not None and (isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0):
        _refuse(
            "block-record.bad-attempts",
            f"attempts {attempts!r} is not a whole number of zero or more",
            example,
        )
    new_cooldown: Optional[str] = None
    if cooldown_until is not None:
        new_cooldown = (
            format_iso(cooldown_until)
            if isinstance(cooldown_until, datetime.datetime)
            else _canonical_cooldown(str(cooldown_until))
        )
        if new_cooldown is None:
            _refuse(
                "block-record.bad-cooldown",
                f"cooldown_until {cooldown_until!r} is neither an ISO-8601 time nor none",
                example,
            )
    new_resume: Optional[str] = None
    if resume is not None:
        new_resume = resume.strip().lower()
        if new_resume not in RESUME_MODES:
            _refuse(
                "block-record.bad-resume",
                f"resume {resume!r} is not one of {'|'.join(RESUME_MODES)}",
                f"python3 {_MODULE_FILE} update {shlex.quote(str(path))} --resume branch",
            )
    requeue_code: Optional[str] = None
    if record_requeue is not None:
        requeue_code = normalize_reason_code(record_requeue)
        if requeue_code not in REASON_CODES:
            _refuse(
                "block-record.unknown-reason-code",
                f"--record-requeue {record_requeue!r} is not in the closed Block Record vocabulary "
                f"({', '.join(REASON_CODES)}); nothing was written",
                f"python3 {_MODULE_FILE} codes; python3 {_MODULE_FILE} update {shlex.quote(str(path))} "
                "--record-requeue per_task_timeout",
            )

    text = _read_status(path)
    current = read_block_record(text, path)
    if current is None:
        _refuse(
            "block-record.absent",
            f"{path} carries no ## Block Record to update; a block write creates one",
            f"python3 {_MODULE_FILE} read {shlex.quote(str(path))}",
        )
    changes: dict = {}
    if restart_epoch:
        changes.update(
            first_blocked_at=current.last_blocked_at, attempts=INITIAL_ATTEMPTS, cooldown_until=NONE_VALUE
        )
    if attempts is not None:
        changes["attempts"] = attempts
    if bump_attempts:
        changes["attempts"] = changes.get("attempts", current.attempts) + 1
    if new_cooldown is not None:
        changes["cooldown_until"] = new_cooldown
    if new_resume is not None:
        changes["resume"] = new_resume
    if requeue_code is not None:
        changes["requeues"] = current.requeues + 1
        changes["requeue_reasons"] = format_requeue_reasons([*current.requeue_reason_list(), requeue_code])
    return _apply(path, text, dataclasses.replace(current, **changes))


def remove_block_record(status_file: "Path | str") -> bool:
    """Remove the ``## Block Record`` section from *status_file*.

    Returns:
        True when a section was removed, False when there was none (nothing
        is written then).

    Raises:
        BlockRecordError: an unreadable or unwritable status.md.
    """
    path = Path(status_file)
    text = _read_status(path)
    new_text = strip_block_record(text)
    if new_text == text:
        return False
    _write_status(path, new_text)
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="block_record",
        description=(
            "Write, read, update or remove the ## Block Record section of a "
            "task status.md. Every write is atomic and preserves every other "
            "section byte-for-byte; a refusal prints error/message/remedy, "
            "exits 1 and writes nothing."
        ),
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    w = sub.add_parser(
        "write",
        help="record a block (first block creates the record; a re-block updates it)",
        description=(
            "Record a block. A first write creates the section with attempts 0, "
            "cooldown_until none, resume fresh, requeues 0 and requeue_reasons none; a "
            "re-block keeps first_blocked_at, attempts, cooldown_until, resume, requeues "
            "and requeue_reasons and replaces "
            "reason_code, owner_role, evidence and last_blocked_at. Options go "
            "before the positional arguments; put an evidence token that starts "
            "with '-' after '--'."
        ),
    )
    w.add_argument("--now", metavar="ISO", help="block time (ISO-8601); default: the current UTC time")
    w.add_argument("status_file", metavar="STATUS_FILE", help="the task's status.md (must exist)")
    w.add_argument("reason_code", metavar="REASON_CODE", nargs="?", help="a code from 'codes'")
    w.add_argument("owner_role", metavar="OWNER_ROLE", nargs="?", help="one of " + "|".join(OWNER_ROLES))
    w.add_argument(
        "evidence", metavar="EVIDENCE", nargs="*",
        help="evidence tokens such as worktree=gone branch=NAME ahead=2; joined with '; '",
    )

    r = sub.add_parser(
        "read",
        help="print the record (exit 3 when there is none) or one field",
        description=(
            "Print the record's ten lines, or with --field one value. With no "
            "record: exit 3 and print nothing, or with --field print --default "
            "(empty when not given) and exit 0."
        ),
    )
    r.add_argument("status_file", metavar="STATUS_FILE", help="the task's status.md (must exist)")
    r.add_argument("--field", choices=FIELD_ORDER, help="print only this field's value")
    r.add_argument("--default", default="", help="with --field: printed when there is no record")

    u = sub.add_parser(
        "update",
        help="set attempts, cooldown_until or resume on an existing record, record a requeue, or restart its epoch",
        description="Change the triage fields of an existing record; every other field keeps its value.",
    )
    u.add_argument("status_file", metavar="STATUS_FILE", help="the task's status.md (must carry a record)")
    u.add_argument(
        "--restart-epoch", action="store_true",
        help="start a new block epoch at the latest block: first_blocked_at becomes last_blocked_at, "
        "attempts 0 and cooldown_until none (applied before the other options); requeues and "
        "requeue_reasons are kept",
    )
    group = u.add_mutually_exclusive_group()
    group.add_argument("--attempts", metavar="N", help="set attempts to N (a whole number)")
    group.add_argument("--bump-attempts", action="store_true", help="add one to attempts")
    u.add_argument("--cooldown-until", metavar="ISO|none", help="no triage action before this time")
    u.add_argument("--resume", metavar="branch|fresh", help="how the next dispatch treats the feature branch")
    u.add_argument(
        "--record-requeue", metavar="REASON_CODE",
        help="record one requeue for this reason code: requeues goes up by one and the code is appended to "
        "requeue_reasons",
    )

    rm = sub.add_parser(
        "remove",
        help="remove the record (a no-op when there is none)",
        description="Remove the ## Block Record section; used when a task reaches DONE or WONT-DO.",
    )
    rm.add_argument("status_file", metavar="STATUS_FILE", help="the task's status.md (must exist)")

    c = sub.add_parser(
        "codes",
        help="list the accepted reason codes (or owner roles)",
        description=(
            "Print the closed reason_code vocabulary, one per line; with --agent only "
            "the agent_<...> codes; with --owner-roles the owner roles; with "
            "--action-verbs each allowlisted action verb and its typed arguments."
        ),
    )
    listing = c.add_mutually_exclusive_group()
    listing.add_argument("--owner-roles", action="store_true", help="print the owner roles instead")
    listing.add_argument(
        "--agent", action="store_true",
        help="print only the agent_<...> codes the status-writer tool's --reason-code accepts",
    )
    listing.add_argument(
        "--action-verbs", action="store_true",
        help="print each allowlisted --action-args verb with its typed arguments",
    )
    return parser


def _run(args: argparse.Namespace) -> int:
    if args.command == "codes":
        if args.action_verbs:
            for verb, specs in ACTION_VERBS.items():
                print(f"{ACTION_VERB_KEY}={verb} " + " ".join(f"{spec.name}=({spec.shape})" for spec in specs))
            return EXIT_OK
        listed = OWNER_ROLES if args.owner_roles else AGENT_REASON_CODES if args.agent else REASON_CODES
        print("\n".join(listed))
        return EXIT_OK

    if args.command == "write":
        missing = [name for name in ("reason_code", "owner_role") if not getattr(args, name)]
        if missing:
            _refuse(
                "block-record.missing-argument",
                f"block write is missing {' and '.join(m.upper() for m in missing)}",
                f"python3 {_MODULE_FILE} write {shlex.quote(args.status_file)} agent_unclassified CODER",
            )
        write_block_record(args.status_file, args.reason_code, args.owner_role, args.evidence, now=args.now)
        return EXIT_OK

    if args.command == "read":
        record = load_block_record(args.status_file)
        if args.field:
            print(record.as_dict()[args.field] if record is not None else args.default)
            return EXIT_OK
        if record is None:
            return EXIT_RECORD_ABSENT
        print(record.to_body())
        return EXIT_OK

    if args.command == "update":
        attempts: Optional[int] = None
        if args.attempts is not None:
            if not args.attempts.strip().isdigit():
                _refuse(
                    "block-record.bad-attempts",
                    f"attempts {args.attempts!r} is not a whole number of zero or more",
                    f"python3 {_MODULE_FILE} update {shlex.quote(args.status_file)} --attempts 1",
                )
            attempts = int(args.attempts.strip())
        update_block_record(
            args.status_file,
            attempts=attempts,
            bump_attempts=args.bump_attempts,
            cooldown_until=args.cooldown_until,
            resume=args.resume,
            restart_epoch=args.restart_epoch,
            record_requeue=args.record_requeue,
        )
        return EXIT_OK

    # remove
    remove_block_record(args.status_file)
    return EXIT_OK


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point; returns the exit code."""
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return EXIT_OK
    try:
        return _run(args)
    except BlockRecordError as exc:
        emit_error_contract(exc.error_key, exc.message, exc.remedy)
        return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
