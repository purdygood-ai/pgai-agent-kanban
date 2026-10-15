"""
block_classifier.py — decide what OVERWATCH's block triage may do about a
blocked task, from its ``## Block Record`` and a YAML table.

The table (``block-classifier.yaml`` next to this module; its header
documents the row shape in full) maps each Block Record ``reason_code`` to a
row::

    reason_code -> {action, max_attempts, cooldown_s, backoff,
                    verification, preconditions}

plus the optional ``params``, ``cooldown_before_first`` and ``otherwise``.
The action is one of ``ACTIONS``: ``requeue_owner``,
``prune_worktree_then_requeue``, ``recompute_gate``,
``defer_to_resolution_stage``, ``run_allowlisted_verb`` or ``escalate``.

This module only decides. It reads the record, the caller's facts and the
table, and returns a :class:`Decision`; it runs no git command, writes no
file and changes no task state. Acting on a decision (backups, evidence
snapshots, the requeue, bumping ``attempts`` and recording
``cooldown_until``) is the triage executor's job.

Decision order
--------------
:func:`decide` checks, in this order, and the first that applies wins:

1. no record, an unknown ``owner_role``, or an ``evidence`` line that is
   neither ``none`` nor ``key=value`` pairs           -> ``escalate``
2. an absent ``reason_code``, or one with no row      -> ``escalate``
3. a row whose action is ``escalate``                 -> ``escalate``
4. ``attempts`` >= the row's ``max_attempts``          -> ``escalate``
   (a ``stage`` ceiling is the resolution stage's to enforce)
5. now < ``cooldown_until``                            -> ``not_yet``
6. a ``cooldown_before_first`` row seen for the first time
   (attempts 0, no cooldown_until)                     -> ``not_yet``,
   with the window end for the executor to record
7. the row's preconditions all hold                    -> the row's action;
   else each ``otherwise`` in turn; else              -> ``escalate``

:func:`classify` wraps :func:`decide` so that every failure upstream of it
also fails closed: a table that does not load or validate, and a status.md
text whose record does not parse, each decide ``escalate``.

Preconditions
-------------
Facts come from the caller as a ``{name: bool}`` mapping (read-only git and
task-graph reads are the caller's). A fact the caller did not supply, or
supplied as anything but a bool, is unmet. Three are read from the record
itself instead:

    owner_is_coder_or_writer   the record's owner_role is CODER or WRITER
                               (the caller's value is ignored)
    action_args_valid          the evidence names an allowlisted verb with
                               typed arguments that validate (the caller's
                               value is ignored)
    branch_ancestor_of_source  the caller's value when it gives one, else the
                               evidence's ``merged=yes``

The same mapping may carry string values that fill ``{key}`` placeholders in
a row's ``note`` (before the record's evidence values; ``unknown`` when
neither has the key).

Usage (CLI):
    python3 -m pgai_agent_kanban.overwatch.block_classifier classify \\
        STATUS_FILE [--facts JSON_FILE] [--now ISO] [--table YAML_FILE]

Prints the decision as JSON and exits 0, whatever was decided — escalate is
a decision, not an error. Usage errors exit 2.

Usage (import):
    from pgai_agent_kanban.overwatch.block_classifier import (
        ESCALATE, NOT_YET, Decision, classify, classify_status_file, decide,
        load_table,
    )
    decision = classify_status_file(status_file, facts={"merge_tree_clean": True})
    if decision.action == ESCALATE:
        ...  # leave blocked; decision.why / decision.detail name the cause
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import pathlib
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Union

import yaml

# Run by file path (``python3 .../block_classifier.py``) or loaded from a file
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

from pgai_agent_kanban.lib.timeutil import format_iso, try_parse_iso  # noqa: E402
from pgai_agent_kanban.overwatch.block_record import (  # noqa: E402
    NONE_VALUE,
    OWNER_ROLES,
    REASON_CODES,
    RESUME_MODES,
    ActionArgsError,
    BlockRecord,
    BlockRecordError,
    format_action_args,
    parse_action_args,
    parse_evidence,
    read_action_args,
    read_block_record,
)

# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------

ESCALATE = "escalate"

#: The actions a table row may prescribe.
ACTIONS: tuple[str, ...] = (
    "requeue_owner",
    "prune_worktree_then_requeue",
    "recompute_gate",
    "defer_to_resolution_stage",
    "run_allowlisted_verb",
    ESCALATE,
)

#: The decision for a task inside its cool-down window: take no action now.
#: Never a table action — load_table() refuses a row that declares it.
NOT_YET = "not_yet"

#: ``max_attempts`` value meaning the resolution stage's own ceiling applies.
STAGE_CEILING = "stage"

#: Checks a row may name for the next terminal outcome to pass.
VERIFICATIONS: tuple[str, ...] = (
    "next_outcome_not_timeout",
    "zero_progress_not_readvanced",
    "gate_passes",
    "next_create_succeeds",
    "stage_decision_event",
    "rerun_reports_no_changes",
)

#: Facts a row may require before its action is taken.
PRECONDITIONS: tuple[str, ...] = (
    "branch_ancestor_of_source",
    "merge_tree_clean",
    "owner_is_coder_or_writer",
    "worktree_residue_in_base",
    "residue_holds_no_unique_commits",
    "source_branch_exists",
    "action_args_valid",
    "action_targets_verified",
)

#: Evidence a requeue_owner action saves under artifacts/block-evidence/ first.
SNAPSHOTS: tuple[str, ...] = ("commits_ahead", "dirty_patch")

#: Owner roles whose work a merge-only requeue can finish.
_MERGING_ROLES = ("CODER", "WRITER")

#: Params each action accepts, and which of them it requires.
_ACTION_PARAMS: dict[str, tuple[frozenset, frozenset]] = {
    "requeue_owner": (
        frozenset({"note", "resume", "snapshot", "reset_zero_progress"}),
        frozenset({"note"}),
    ),
    "prune_worktree_then_requeue": (frozenset({"note", "resume"}), frozenset({"note"})),
    "run_allowlisted_verb": (frozenset({"note", "resume"}), frozenset({"note"})),
    "recompute_gate": (frozenset(), frozenset()),
    "defer_to_resolution_stage": (frozenset(), frozenset()),
    ESCALATE: (frozenset(), frozenset()),
}

_ROW_REQUIRED_KEYS = frozenset(
    {"action", "max_attempts", "cooldown_s", "backoff", "verification", "preconditions"}
)
_ROW_OPTIONAL_KEYS = frozenset({"params", "cooldown_before_first", "otherwise"})
_STEP_REQUIRED_KEYS = frozenset({"action", "preconditions"})
_STEP_OPTIONAL_KEYS = frozenset({"params", "otherwise"})
_BACKOFF_KEYS = frozenset({"factor", "cap_s"})
_TOP_LEVEL_KEY = "reason_codes"

#: Placeholder a note gets for a key neither the facts nor the evidence carry.
UNKNOWN_VALUE = "unknown"

_NOTE_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

# Stable keys naming why a decision came out as it did.
WHY_TABLE_INVALID = "block-classifier.table-invalid"
WHY_RECORD_ABSENT = "block-classifier.record-absent"
WHY_RECORD_UNREADABLE = "block-classifier.record-unreadable"
WHY_FACTS_UNREADABLE = "block-classifier.facts-unreadable"
WHY_OWNER_UNKNOWN = "block-classifier.owner-role-unknown"
WHY_EVIDENCE_UNPARSEABLE = "block-classifier.evidence-unparseable"
WHY_REASON_ABSENT = "block-classifier.reason-code-absent"
WHY_REASON_UNKNOWN = "block-classifier.reason-code-unknown"
WHY_ALWAYS_HUMAN = "block-classifier.always-human"
WHY_ATTEMPTS_EXHAUSTED = "block-classifier.attempts-exhausted"
WHY_COOLING_DOWN = "block-classifier.cooling-down"
WHY_FIRST_COOLDOWN = "block-classifier.first-cooldown"
WHY_PRECONDITION_UNMET = "block-classifier.precondition-unmet"
WHY_ROW_MATCHED = "block-classifier.row-matched"


class TableError(ValueError):
    """The classifier table is missing, unreadable or fails validation.

    The message names the file and every fault found in it.
    """


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Backoff:
    """Window growth: before attempt k+1 the window is min(base * factor**k, cap_s)."""

    factor: float
    cap_s: int


@dataclasses.dataclass(frozen=True)
class Step:
    """One action a row may take, the facts it needs, and the fallback step."""

    action: str
    params: Mapping[str, Any]
    preconditions: tuple[str, ...]
    otherwise: Optional["Step"] = None


@dataclasses.dataclass(frozen=True)
class Rule:
    """One validated table row."""

    reason_code: str
    step: Step
    max_attempts: Union[int, str]
    cooldown_s: int
    backoff: Optional[Backoff]
    verification: Optional[str]
    cooldown_before_first: bool = False

    @property
    def action(self) -> str:
        """The row's primary action."""
        return self.step.action

    def window_s(self, completed_attempts: int) -> int:
        """Seconds of cool-down before attempt ``completed_attempts + 1``."""
        if self.backoff is None:
            return self.cooldown_s
        grown = self.cooldown_s * (self.backoff.factor ** completed_attempts)
        return int(min(grown, self.backoff.cap_s))


@dataclasses.dataclass(frozen=True)
class ClassifierTable:
    """A validated table: ``rules`` keyed by reason_code, and where it came from."""

    path: str
    rules: Mapping[str, Rule]


def default_table_path() -> str:
    """Path to the shipped table, next to this module."""
    return str(Path(__file__).resolve().with_name("block-classifier.yaml"))


class _UniqueKeyLoader(yaml.SafeLoader):
    """A safe YAML loader that refuses a mapping repeating a key."""


def _construct_unique_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    loader.flatten_mapping(node)
    seen: set = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping", node.start_mark, f"found duplicate key {key!r}", key_node.start_mark
            )
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)


def _is_none(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip().lower() == NONE_VALUE)


def _is_whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _validate_params(where: str, action: str, raw: Any, problems: list) -> dict:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        problems.append(f"{where}: params must be a mapping, got {type(raw).__name__}")
        return {}
    allowed, required = _ACTION_PARAMS[action]
    for key in sorted(set(raw) - allowed):
        problems.append(
            f"{where}: {action} takes no param {key!r}"
            + (f" (accepted: {', '.join(sorted(allowed))})" if allowed else "")
        )
    for key in sorted(required - set(raw)):
        problems.append(f"{where}: {action} requires param {key!r}")
    params: dict = {}
    note = raw.get("note")
    if "note" in raw:
        if not isinstance(note, str) or not note.strip():
            problems.append(f"{where}: param 'note' must be non-empty text")
        else:
            params["note"] = note.strip()
    if "resume" in raw:
        resume = str(raw["resume"]).strip().lower()
        if resume not in RESUME_MODES:
            problems.append(f"{where}: param 'resume' {raw['resume']!r} is not one of {'|'.join(RESUME_MODES)}")
        else:
            params["resume"] = resume
    if "snapshot" in raw:
        snapshot = raw["snapshot"]
        if not isinstance(snapshot, list) or not all(isinstance(s, str) for s in snapshot):
            problems.append(f"{where}: param 'snapshot' must be a list of names")
        else:
            unknown = [s for s in snapshot if s not in SNAPSHOTS]
            if unknown:
                problems.append(
                    f"{where}: param 'snapshot' names unknown evidence {', '.join(unknown)} "
                    f"(accepted: {', '.join(SNAPSHOTS)})"
                )
            params["snapshot"] = tuple(snapshot)
    if "reset_zero_progress" in raw:
        if not isinstance(raw["reset_zero_progress"], bool):
            problems.append(f"{where}: param 'reset_zero_progress' must be true or false")
        else:
            params["reset_zero_progress"] = raw["reset_zero_progress"]
    return params


def _validate_preconditions(where: str, raw: Any, problems: list) -> tuple[str, ...]:
    if not isinstance(raw, list) or not all(isinstance(p, str) for p in raw):
        problems.append(f"{where}: preconditions must be a list of names (may be empty)")
        return ()
    unknown = [p for p in raw if p not in PRECONDITIONS]
    if unknown:
        problems.append(
            f"{where}: unknown precondition(s) {', '.join(unknown)} (accepted: {', '.join(PRECONDITIONS)})"
        )
    if len(set(raw)) != len(raw):
        problems.append(f"{where}: preconditions repeat a name")
    return tuple(raw)


def _validate_step(where: str, raw: Mapping, required: frozenset, optional: frozenset, problems: list) -> Step:
    """Validate the action/params/preconditions/otherwise part of a row or fallback."""
    keys = set(raw)
    missing = required - keys
    extra = keys - required - optional
    if missing:
        problems.append(f"{where}: missing key(s) {', '.join(sorted(missing))}")
    if extra:
        problems.append(
            f"{where}: unrecognized key(s) {', '.join(sorted(extra))} "
            f"(accepted: {', '.join(sorted(required | optional))})"
        )
    action = raw.get("action")
    if action not in ACTIONS:
        problems.append(f"{where}: action {action!r} is not one of {', '.join(ACTIONS)}")
        action = ESCALATE
    params = _validate_params(where, action, raw.get("params"), problems)
    preconditions = _validate_preconditions(where, raw.get("preconditions", []), problems)
    otherwise: Optional[Step] = None
    if "otherwise" in raw:
        fallback = raw["otherwise"]
        if not isinstance(fallback, dict):
            problems.append(f"{where}: otherwise must be a mapping, got {type(fallback).__name__}")
        else:
            otherwise = _validate_step(
                f"{where} otherwise", fallback, _STEP_REQUIRED_KEYS, _STEP_OPTIONAL_KEYS, problems
            )
    return Step(action=action, params=params, preconditions=preconditions, otherwise=otherwise)


def _validate_backoff(where: str, raw: Any, cooldown_s: int, problems: list) -> Optional[Backoff]:
    if _is_none(raw):
        return None
    if not isinstance(raw, dict) or set(raw) != _BACKOFF_KEYS:
        problems.append(f"{where}: backoff must be none or {{factor: F, cap_s: C}}, got {raw!r}")
        return None
    factor, cap_s = raw["factor"], raw["cap_s"]
    if isinstance(factor, bool) or not isinstance(factor, (int, float)) or factor < 1:
        problems.append(f"{where}: backoff factor {factor!r} must be a number of 1 or more")
        return None
    if not _is_whole(cap_s) or cap_s < cooldown_s:
        problems.append(f"{where}: backoff cap_s {cap_s!r} must be a whole number of at least cooldown_s ({cooldown_s})")
        return None
    return Backoff(factor=factor, cap_s=cap_s)


def _validate_rule(reason_code: Any, raw: Any, problems: list) -> Optional[Rule]:
    where = f"row {reason_code!r}"
    if reason_code not in REASON_CODES:
        problems.append(f"{where}: not a Block Record reason_code (accepted: {', '.join(REASON_CODES)})")
    if not isinstance(raw, dict):
        problems.append(f"{where}: must be a mapping, got {type(raw).__name__}")
        return None
    step = _validate_step(where, raw, _ROW_REQUIRED_KEYS, _ROW_OPTIONAL_KEYS, problems)

    max_attempts = raw.get("max_attempts")
    if max_attempts == STAGE_CEILING:
        if step.action != "defer_to_resolution_stage":
            problems.append(f"{where}: max_attempts {STAGE_CEILING!r} is only for defer_to_resolution_stage")
    elif not _is_whole(max_attempts):
        problems.append(f"{where}: max_attempts {max_attempts!r} must be a whole number or {STAGE_CEILING!r}")
    cooldown_s = raw.get("cooldown_s")
    if not _is_whole(cooldown_s):
        problems.append(f"{where}: cooldown_s {cooldown_s!r} must be a whole number of seconds")
        cooldown_s = 0
    backoff = _validate_backoff(where, raw.get("backoff"), cooldown_s, problems)
    verification = raw.get("verification")
    if _is_none(verification):
        verification = None
    elif verification not in VERIFICATIONS:
        problems.append(f"{where}: verification {verification!r} is not none or one of {', '.join(VERIFICATIONS)}")
    before_first = raw.get("cooldown_before_first", False)
    if not isinstance(before_first, bool):
        problems.append(f"{where}: cooldown_before_first must be true or false")
        before_first = False
    return Rule(
        reason_code=str(reason_code),
        step=step,
        max_attempts=max_attempts,
        cooldown_s=cooldown_s,
        backoff=backoff,
        verification=verification,
        cooldown_before_first=before_first,
    )


def load_table(table_path: "str | Path | None" = None) -> ClassifierTable:
    """Load and validate the classifier table.

    Args:
        table_path: The YAML file; the shipped ``block-classifier.yaml`` by default.

    Raises:
        TableError: the file is missing or unreadable, is not YAML, repeats a
            key, or breaks the row shape the file's header documents. The
            message names the file and every fault.
    """
    path = str(table_path) if table_path else default_table_path()
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise TableError(f"cannot read classifier table {path}: {exc}") from exc
    try:
        data = yaml.load(text, Loader=_UniqueKeyLoader)  # noqa: S506 — a SafeLoader subclass
    except yaml.YAMLError as exc:
        raise TableError(f"classifier table {path} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict) or set(data) != {_TOP_LEVEL_KEY}:
        raise TableError(f"classifier table {path} must be a mapping with exactly one key, {_TOP_LEVEL_KEY!r}")
    raw_rows = data[_TOP_LEVEL_KEY]
    if not isinstance(raw_rows, dict):
        raise TableError(f"classifier table {path}: {_TOP_LEVEL_KEY!r} must map reason_code to a row")
    problems: list = []
    rules = {}
    for code, raw in raw_rows.items():
        rule = _validate_rule(code, raw, problems)
        if rule is not None:
            rules[rule.reason_code] = rule
    if problems:
        raise TableError(f"classifier table {path} is invalid: " + "; ".join(problems))
    return ClassifierTable(path=path, rules=rules)


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Decision:
    """What block triage may do about one Block Record.

    Fields:
        action          one of ACTIONS, or NOT_YET.
        why             a stable ``block-classifier.*`` key naming the cause.
        detail          the cause in plain language.
        reason_code     the record's reason_code (None when there is none).
        params          the action's params, ``note`` rendered; {} otherwise.
        attempt         the attempt number an action would be; None for
                        escalate and not_yet.
        max_attempts    the matched row's ceiling (a whole number, or
                        ``stage``); None when no row matched.
        cooldown_s      the matched row's base cool-down; None without a row.
        backoff         the matched row's backoff; None without one.
        verification    the matched row's verification; None without one.
        cooldown_until  the value to record in the Block Record: after an
                        action, the end of the next window (or ``none``); on
                        a first-sighting not_yet, the window end. None means
                        leave the field as it is.
        not_before      for not_yet, the earliest time to decide again.
    """

    action: str
    why: str
    detail: str
    reason_code: Optional[str] = None
    params: Mapping[str, Any] = dataclasses.field(default_factory=dict)
    attempt: Optional[int] = None
    max_attempts: Union[int, str, None] = None
    cooldown_s: Optional[int] = None
    backoff: Optional[Backoff] = None
    verification: Optional[str] = None
    cooldown_until: Optional[str] = None
    not_before: Optional[str] = None

    @property
    def is_escalate(self) -> bool:
        """True when the block stays with a human."""
        return self.action == ESCALATE

    def as_json_dict(self) -> dict:
        """The decision as a JSON-ready dict (tuples become lists)."""
        out = {f.name: getattr(self, f.name) for f in dataclasses.fields(self)}
        out["params"] = {k: list(v) if isinstance(v, tuple) else v for k, v in self.params.items()}
        out["backoff"] = dataclasses.asdict(self.backoff) if self.backoff is not None else None
        return out


def _escalate(why: str, detail: str, record: Optional[BlockRecord] = None, rule: Optional[Rule] = None) -> Decision:
    return Decision(action=ESCALATE, why=why, detail=detail, **_rule_fields(record, rule))


def _rule_fields(record: Optional[BlockRecord], rule: Optional[Rule]) -> dict:
    fields: dict = {"reason_code": (record.reason_code or None) if record is not None else None}
    if rule is not None:
        fields.update(
            max_attempts=rule.max_attempts,
            cooldown_s=rule.cooldown_s,
            backoff=rule.backoff,
            verification=rule.verification,
        )
    return fields


def _as_utc(now: "datetime.datetime | str | None") -> datetime.datetime:
    if now is None:
        return datetime.datetime.now(datetime.timezone.utc)
    if isinstance(now, datetime.datetime):
        return now if now.tzinfo is not None else now.replace(tzinfo=datetime.timezone.utc)
    parsed = try_parse_iso(now)
    if parsed is None:
        raise ValueError(f"now {now!r} is not an ISO-8601 time (expected e.g. 2026-01-01T00:00:00+00:00)")
    return parsed


def _precondition_holds(name: str, record: BlockRecord, evidence: Mapping[str, str], facts: Mapping[str, Any]) -> bool:
    """Whether one precondition holds; anything unproven is unmet."""
    if name == "owner_is_coder_or_writer":
        return record.owner_role in _MERGING_ROLES
    if name == "action_args_valid":
        args = read_action_args(record)
        if not args:
            return False
        try:
            parse_action_args(format_action_args(args))
        except ActionArgsError:
            return False
        return True
    supplied = facts.get(name)
    if isinstance(supplied, bool):
        return supplied
    if name == "branch_ancestor_of_source":
        return evidence.get("merged", "").strip().lower() == "yes"
    return False


def _render_note(template: str, evidence: Mapping[str, str], facts: Mapping[str, Any]) -> str:
    def _value(match: "re.Match[str]") -> str:
        key = match.group(1)
        supplied = facts.get(key)
        if isinstance(supplied, (str, int, float)) and not isinstance(supplied, bool) and str(supplied).strip():
            return str(supplied).strip()
        return evidence.get(key.lower()) or UNKNOWN_VALUE

    return _NOTE_PLACEHOLDER_RE.sub(_value, template)


def decide(
    record: Optional[BlockRecord],
    table: ClassifierTable,
    *,
    facts: Optional[Mapping[str, Any]] = None,
    now: "datetime.datetime | str | None" = None,
) -> Decision:
    """Decide what block triage may do about *record* (see the module docstring).

    Pure: reads nothing but its arguments.

    Args:
        record: The parsed Block Record, or None when the task carries none.
        table:  A table from :func:`load_table`.
        facts:  ``{precondition: bool}`` and ``{note_key: text}`` values the
                caller gathered; anything not given is unmet / ``unknown``.
        now:    The decision time; the current UTC time by default.

    Raises:
        ValueError: *now* is a string that is not an ISO-8601 time.
    """
    facts = facts or {}
    moment = _as_utc(now)
    if record is None:
        return _escalate(WHY_RECORD_ABSENT, "the task carries no ## Block Record, so its block cannot be classified")
    if record.owner_role not in OWNER_ROLES:
        return _escalate(
            WHY_OWNER_UNKNOWN,
            f"owner_role {record.owner_role!r} is not one of {'|'.join(OWNER_ROLES)}; there is no owner to requeue to",
            record,
        )
    evidence = parse_evidence(record.evidence)
    if record.evidence.strip().lower() != NONE_VALUE and not evidence:
        return _escalate(
            WHY_EVIDENCE_UNPARSEABLE,
            f"the evidence line {record.evidence!r} carries no key=value pairs to act on",
            record,
        )
    if not record.reason_code:
        return _escalate(WHY_REASON_ABSENT, "the Block Record carries no reason_code", record)
    rule = table.rules.get(record.reason_code)
    if rule is None:
        return _escalate(
            WHY_REASON_UNKNOWN,
            f"reason_code {record.reason_code!r} has no row in the classifier table {table.path}",
            record,
        )
    if rule.action == ESCALATE:
        return _escalate(WHY_ALWAYS_HUMAN, f"{rule.reason_code} blocks always stay with a human", record, rule)
    if rule.max_attempts != STAGE_CEILING and record.attempts >= rule.max_attempts:
        return _escalate(
            WHY_ATTEMPTS_EXHAUSTED,
            f"{record.attempts} triage attempt(s) already made on this block; {rule.reason_code} allows "
            f"{rule.max_attempts}",
            record,
            rule,
        )

    cooldown_until = try_parse_iso(record.cooldown_until) if record.cooldown_until != NONE_VALUE else None
    if cooldown_until is not None and moment < cooldown_until:
        return Decision(
            action=NOT_YET,
            why=WHY_COOLING_DOWN,
            detail=f"inside the cool-down window until {format_iso(cooldown_until)}",
            not_before=format_iso(cooldown_until),
            **_rule_fields(record, rule),
        )
    if rule.cooldown_before_first and record.attempts == 0 and cooldown_until is None:
        window_end = format_iso(moment + datetime.timedelta(seconds=rule.window_s(0)))
        return Decision(
            action=NOT_YET,
            why=WHY_FIRST_COOLDOWN,
            detail=f"{rule.reason_code} waits one cool-down window before its first action, until {window_end}",
            cooldown_until=window_end,
            not_before=window_end,
            **_rule_fields(record, rule),
        )

    unmet: list = []
    step: Optional[Step] = rule.step
    while step is not None:
        missing = [p for p in step.preconditions if not _precondition_holds(p, record, evidence, facts)]
        if not missing:
            break
        unmet.append(f"{step.action} needs {', '.join(missing)}")
        step = step.otherwise
    if step is None:
        return _escalate(
            WHY_PRECONDITION_UNMET,
            f"{rule.reason_code}: precondition(s) unmet — " + "; ".join(unmet),
            record,
            rule,
        )
    if step.action == ESCALATE:
        return _escalate(WHY_ALWAYS_HUMAN, f"{rule.reason_code} falls through to a human", record, rule)

    params = dict(step.params)
    if "note" in params:
        params["note"] = _render_note(params["note"], evidence, facts)
    next_window = rule.window_s(record.attempts + 1)
    ceiling = (
        "under the resolution stage's own ceiling"
        if rule.max_attempts == STAGE_CEILING
        else f"of {rule.max_attempts}"
    )
    return Decision(
        action=step.action,
        why=WHY_ROW_MATCHED,
        detail=f"{rule.reason_code}: {step.action}, attempt {record.attempts + 1} {ceiling}",
        params=params,
        attempt=record.attempts + 1,
        cooldown_until=(
            format_iso(moment + datetime.timedelta(seconds=next_window)) if next_window > 0 else NONE_VALUE
        ),
        **_rule_fields(record, rule),
    )


def classify(
    status_text: str,
    *,
    facts: Optional[Mapping[str, Any]] = None,
    now: "datetime.datetime | str | None" = None,
    table_path: "str | Path | None" = None,
    status_file: "str | Path | None" = None,
) -> Decision:
    """Decide from a status.md's text, failing closed on anything unreadable.

    A table that does not load or validate, and a Block Record that does not
    parse, each decide ``escalate`` instead of raising. *status_file* is only
    named in messages.
    """
    try:
        table = load_table(table_path)
    except TableError as exc:
        return _escalate(WHY_TABLE_INVALID, str(exc))
    try:
        record = read_block_record(status_text, status_file)
    except BlockRecordError as exc:
        return _escalate(WHY_RECORD_UNREADABLE, exc.message)
    return decide(record, table, facts=facts, now=now)


def classify_status_file(
    status_file: "str | Path",
    *,
    facts: Optional[Mapping[str, Any]] = None,
    now: "datetime.datetime | str | None" = None,
    table_path: "str | Path | None" = None,
) -> Decision:
    """:func:`classify` for a status.md on disk (read only); unreadable decides escalate."""
    try:
        text = Path(status_file).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return _escalate(WHY_RECORD_UNREADABLE, f"cannot read {status_file}: {exc}")
    return classify(text, facts=facts, now=now, table_path=table_path, status_file=status_file)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="block_classifier",
        description=(
            "Decide what OVERWATCH's block triage may do about a blocked task, from "
            "its ## Block Record and the classifier table. Reads only; changes nothing. "
            "Prints the decision as JSON and exits 0 whatever it decides."
        ),
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    c = sub.add_parser(
        "classify",
        help="print the decision for one task's status.md",
        description=(
            "Print the decision for STATUS_FILE as JSON. Anything that cannot be read — "
            "the status file, its Block Record, the facts file or the table — decides "
            "escalate."
        ),
    )
    c.add_argument("status_file", metavar="STATUS_FILE", help="the blocked task's status.md")
    c.add_argument(
        "--facts", metavar="JSON_FILE",
        help=(
            "a JSON object of facts the caller gathered: precondition names mapped to "
            "true/false, and note placeholder values mapped to text; default: none"
        ),
    )
    c.add_argument("--now", metavar="ISO", help="the decision time (ISO-8601); default: the current UTC time")
    c.add_argument("--table", metavar="YAML_FILE", help="the classifier table; default: the shipped block-classifier.yaml")
    return parser


def _read_facts(path: str) -> Mapping[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} holds a JSON {type(data).__name__}, not an object")
    return data


def main(argv: Optional[list] = None) -> int:
    """CLI entry point; returns the exit code."""
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    if args.now is not None and try_parse_iso(args.now) is None:
        parser.error(f"--now {args.now!r} is not an ISO-8601 time (e.g. 2026-01-01T00:00:00+00:00)")
    decision: Optional[Decision] = None
    facts: Mapping[str, Any] = {}
    if args.facts:
        try:
            facts = _read_facts(args.facts)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            decision = _escalate(WHY_FACTS_UNREADABLE, f"cannot read the facts file: {exc}")
    if decision is None:
        decision = classify_status_file(args.status_file, facts=facts, now=args.now, table_path=args.table)
    sys.stdout.write(json.dumps(decision.as_json_dict(), indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
