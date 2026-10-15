"""
block_storm.py — whether a project's human-gated blocks are systemic, and so
hold the wake's task dispatch for the whole project.

A human-gated block is a task whose queue line carries ``[B]``, whose
``## State`` reads BLOCKED and whose ``## Needs Human`` reads ``yes``,
found across every ``*_backlog.md`` queue of the project. Each one is
classified by its ``## Block Record`` (:mod:`block_record`): the record's
``reason_code`` and its ``last_blocked_at`` time.

The blocks are **systemic** — the wake holds every role's task dispatch
for the project — when either holds:

* **A systemic reason code.** A block whose ``reason_code`` is on the
  systemic list: a configuration or plugin fault that will block the next
  task the same way. The list is ``[wake] systemic_block_reasons``
  (exported as ``SYSTEMIC_BLOCK_REASONS``); left blank or absent it is
  :data:`DEFAULT_SYSTEMIC_REASON_CODES`, and ``none`` empties it.
* **A block storm.** Two or more blocks sharing one ``reason_code`` whose
  ``last_blocked_at`` times lie within ``[wake] block_storm_window``
  seconds (``BLOCK_STORM_WINDOW``, default :data:`DEFAULT_STORM_WINDOW_S`)
  measured back from now. A storm therefore ends on its own once the
  second-newest of its blocks is older than the window. The per-task
  catch-all an agent sets on itself, ``agent_unclassified``
  (:data:`STORM_EXEMPT_REASON_CODES`), never forms a storm: two unrelated
  agent blocks close together are two task-local blocks.

**Every hold is capped.** No hold lasts longer than ``[wake]
block_hold_cap`` seconds (``BLOCK_HOLD_CAP``, default
:data:`DEFAULT_HOLD_CAP_S`) from the block that began it; a block arriving
during a hold does not extend it past the cap, and one arriving after the
hold lifted begins a new hold. A held decision names the time the hold
lifts (``lifts_at``). A block whose ``last_blocked_at`` does not parse
cannot be dated against the window or the cap, so it never holds.

Any other block is **task-local**: it holds only the tasks that name it as a
prerequisite, which their own WAITING state already does, so it never stops
unrelated work. A block with no Block Record, or one that cannot be read,
carries no reason code and is task-local.

The decision is read-only: nothing here writes a file. Which agent the hold
applies to is the caller's business (the wake never holds OVERWATCH, whose
wake is what triages the blocks).

Usage (CLI; the wake's dispatch gate in team/scripts/lib/wake_driver.sh
calls it through pp_run_ops)::

    python3 -m pgai_agent_kanban.overwatch.block_storm decide \\
        QUEUE_DIR TASKS_ROOT [--id-pattern REGEX] \\
        [--window SECONDS] [--hold-cap SECONDS] [--systemic-codes LIST] \\
        [--now ISO-8601]
    python3 -m pgai_agent_kanban.overwatch.block_storm defaults

``decide`` prints six ``key=value`` lines, always in this order::

    verdict=systemic            # systemic | task_local | none
    count=2                     # human-gated blocks found
    reason_code=per_task_timeout   # the systemic reason code, else none
    why=2 human-gated blocks share reason_code per_task_timeout within ...
    remedy=grep -A8 -e '^## Blockers' -e '^## Block Record' /path/status.md
    lifts_at=2026-01-01T11:00:00+00:00   # when the hold lifts, else none

(values above are an example). ``remedy`` and ``lifts_at`` are ``none``
unless the verdict is systemic. ``--window``, ``--hold-cap`` and
``--systemic-codes`` take the raw configured values; a blank one means the
default. A window or cap that is not a whole number of seconds, or a listed
code the Block Record vocabulary does not know, is reported on stderr
through the dual-channel error contract and set aside (the default is used;
the unknown code is dropped) — the decision is still printed. ``--now``
fixes the clock the window and cap are measured from (default: the current
time). ``defaults`` prints the default window, hold cap and systemic list,
one ``key=value`` line each.

Exit codes: 0 whenever a decision was printed; 2 for a usage error
(argparse).

Usage (import)::

    from pgai_agent_kanban.overwatch.block_storm import (
        HumanGatedBlock, GateDecision, scan_human_gated_blocks,
        parse_systemic_codes, parse_window, decide,
        DEFAULT_HOLD_CAP_S, STORM_EXEMPT_REASON_CODES,
    )
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import pathlib
import re
import shlex
import sys
from pathlib import Path
from typing import Iterable, Optional, Sequence

# Run by file path or loaded from a file outside its package, this module
# has no parent package and only its own directory is on sys.path; put its
# own package root first so the package imports below resolve from the same
# tree, never another. Imported as a package member, sys.path is untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban import task_status  # noqa: E402
from pgai_agent_kanban.lib.error_contract import emit_error_contract  # noqa: E402
from pgai_agent_kanban.lib.timeutil import format_iso, try_parse_iso  # noqa: E402
from pgai_agent_kanban.ops.errors import IoError  # noqa: E402
from pgai_agent_kanban.ops.write import parse_scan_rule, scan_queue  # noqa: E402
from pgai_agent_kanban.overwatch import block_record  # noqa: E402

#: Seconds back from now inside which two same-code blocks are a storm.
DEFAULT_STORM_WINDOW_S = 3600

#: The longest any hold lasts, in seconds, from the block that began it.
DEFAULT_HOLD_CAP_S = 3600

#: Reason codes that never form a storm: the per-task catch-all an agent
#: sets on its own block, which says nothing about a shared cause.
STORM_EXEMPT_REASON_CODES: tuple[str, ...] = ("agent_unclassified",)

#: Reason codes that are systemic on their own: configuration and plugin
#: faults that block the next task of any role the same way.
DEFAULT_SYSTEMIC_REASON_CODES: tuple[str, ...] = (
    "invalid_effort_config",
    "workflow_type_unparseable",
    "plugin_load_failed",
    "branch_prefix_failed",
    "invalid_assignment",
)

#: The ``--systemic-codes`` value that empties the systemic list.
NONE_VALUE = "none"

#: Verdicts, from holding dispatch to finding nothing.
VERDICT_SYSTEMIC = "systemic"
VERDICT_TASK_LOCAL = "task_local"
VERDICT_NONE = "none"

#: How a block with no readable Block Record is named in a narration.
UNRECORDED = "unrecorded"

#: Queue lines whose marker is [B] and whose task's State is BLOCKED.
_BLOCKED_RULE = parse_scan_rule("B=BLOCKED")

_REMEDY_SECTIONS = ("Blockers", "Block Record")
_REMEDY_CONTEXT_LINES = 8


@dataclasses.dataclass(frozen=True)
class HumanGatedBlock:
    """One BLOCKED, Needs Human: yes task, as its Block Record classifies it.

    ``reason_code`` and ``blocked_at`` are None when the task has no Block
    Record or the record cannot be read; ``blocked_at`` is also None when
    ``last_blocked_at`` does not parse.
    """

    task_id: str
    status_file: Path
    reason_code: Optional[str]
    blocked_at: Optional[datetime.datetime]


@dataclasses.dataclass(frozen=True)
class GateDecision:
    """Whether the human-gated blocks hold dispatch, and why.

    ``tasks`` names the tasks behind a systemic verdict (empty otherwise),
    ``remedy`` is a command that prints their blockers and Block Records
    (``none`` otherwise), and ``lifts_at`` is when the hold lifts unless
    the blocks are resolved first (None unless systemic).
    """

    verdict: str
    count: int
    reason_code: Optional[str]
    why: str
    tasks: tuple[str, ...] = ()
    remedy: str = NONE_VALUE
    lifts_at: Optional[datetime.datetime] = None

    @property
    def systemic(self) -> bool:
        """True when the blocks hold the project's dispatch."""
        return self.verdict == VERDICT_SYSTEMIC

    def as_lines(self) -> list[str]:
        """The decision as the CLI's six ``key=value`` lines."""
        return [
            f"verdict={self.verdict}",
            f"count={self.count}",
            f"reason_code={self.reason_code or NONE_VALUE}",
            f"why={_one_line(self.why)}",
            f"remedy={_one_line(self.remedy)}",
            f"lifts_at={format_iso(self.lifts_at) if self.lifts_at else NONE_VALUE}",
        ]


def _one_line(text: str) -> str:
    """Collapse any line break in *text* so a value stays on its own line."""
    return re.sub(r"\s*[\r\n]+\s*", " ", text).strip()


# ---------------------------------------------------------------------------
# Reading the blocks
# ---------------------------------------------------------------------------


def _needs_human(status_text: str) -> bool:
    """True when ``## Needs Human`` reads ``yes`` (any case)."""
    return task_status.section_value(status_text, "Needs Human").strip().lower() == "yes"


def _classify(task_id: str, status_file: Path, status_text: str) -> HumanGatedBlock:
    """Build the HumanGatedBlock for one task from its status.md text."""
    try:
        record = block_record.read_block_record(status_text, status_file)
    except block_record.BlockRecordError:
        record = None
    if record is None:
        return HumanGatedBlock(task_id, status_file, None, None)
    return HumanGatedBlock(
        task_id,
        status_file,
        record.reason_code,
        try_parse_iso(record.last_blocked_at),
    )


def scan_human_gated_blocks(
    queue_dir: "Path | str",
    tasks_root: "Path | str",
    id_pattern: Optional[str] = None,
) -> list[HumanGatedBlock]:
    """Return every human-gated block across the project's agent queues.

    Reads every ``*_backlog.md`` in *queue_dir* (in name order) for ``[B]``
    lines whose task's ``## State`` is BLOCKED (the wake's own queue scan,
    :func:`pgai_agent_kanban.ops.write.scan_queue`), then keeps the tasks
    whose ``## Needs Human`` reads ``yes``. A task listed in more than one
    queue is returned once. An unreadable queue file or status.md is
    skipped, never fatal: the gate reads what it can.

    Args:
        queue_dir:  The project's ``tasks/queues`` directory.
        tasks_root: The directory holding one folder per task.
        id_pattern: A regex every task id must match from its start; an id
                    that does not is skipped, so it never becomes a path.

    Returns:
        The blocks in queue-file then line order.
    """
    queue_path = Path(queue_dir)
    root = Path(tasks_root)
    blocks: list[HumanGatedBlock] = []
    seen: set[str] = set()
    for queue_file in sorted(queue_path.glob("*_backlog.md")):
        try:
            task_ids = scan_queue(queue_file, root, [_BLOCKED_RULE], id_pattern=id_pattern)
        except IoError:
            continue
        for task_id in task_ids:
            if task_id in seen:
                continue
            seen.add(task_id)
            status_file = root / task_id / "status.md"
            try:
                text = status_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if _needs_human(text):
                blocks.append(_classify(task_id, status_file, text))
    return blocks


# ---------------------------------------------------------------------------
# Configuration values
# ---------------------------------------------------------------------------


def parse_systemic_codes(value: Optional[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Parse a systemic-list value into ``(codes, unknown)``.

    Blank or None gives :data:`DEFAULT_SYSTEMIC_REASON_CODES`; ``none`` (any
    case) gives an empty list. Otherwise the value is split on commas and
    whitespace, each member is normalised like a Block Record reason code
    (any case, ``-``/``.`` read as ``_``) and duplicates are dropped. A
    member outside the Block Record vocabulary is returned in *unknown* and
    left out of *codes*.
    """
    text = (value or "").strip()
    if not text:
        return DEFAULT_SYSTEMIC_REASON_CODES, ()
    if text.lower() == NONE_VALUE:
        return (), ()
    codes: list[str] = []
    unknown: list[str] = []
    for member in re.split(r"[,\s]+", text):
        if not member:
            continue
        code = block_record.normalize_reason_code(member)
        if not block_record.is_known_reason_code(code):
            if member not in unknown:
                unknown.append(member)
        elif code not in codes:
            codes.append(code)
    return tuple(codes), tuple(unknown)


def parse_window(value: Optional[str], default: int = DEFAULT_STORM_WINDOW_S) -> tuple[int, Optional[str]]:
    """Parse a whole-seconds value into ``(seconds, rejected)``.

    Blank or None gives *default* (:data:`DEFAULT_STORM_WINDOW_S` unless
    given; the hold cap passes :data:`DEFAULT_HOLD_CAP_S`). A run of
    decimal digits is the value. Anything else gives the default and is
    returned as *rejected* so the caller can report it.
    """
    text = (value or "").strip()
    if not text:
        return default, None
    if text.isdigit():
        return int(text), None
    return default, text


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _Hold:
    """One hold: when it began, when its blocks alone would end it (None:
    only resolution ends it), and when it lifts with the cap applied."""

    start: datetime.datetime
    natural_end: Optional[datetime.datetime]
    end: datetime.datetime


def _holds(
    intervals: Iterable[tuple[datetime.datetime, Optional[datetime.datetime]]],
    cap: datetime.timedelta,
) -> list[_Hold]:
    """Merge the spans in which blocks call for a hold into capped holds.

    Each interval is ``(start, natural_end)``; a natural end of None means
    the blocks hold until resolved. An interval that starts before the
    current hold lifts joins it but never moves its lift past
    ``hold.start + cap``; one that starts later begins a new hold.
    """
    holds: list[_Hold] = []
    for start, natural_end in sorted(intervals, key=lambda item: item[0]):
        if holds and start <= holds[-1].end:
            hold = holds[-1]
            if hold.natural_end is not None:
                hold.natural_end = None if natural_end is None else max(hold.natural_end, natural_end)
            hold.end = hold.start + cap if hold.natural_end is None else min(hold.natural_end, hold.start + cap)
            continue
        end = start + cap if natural_end is None else min(natural_end, start + cap)
        holds.append(_Hold(start, natural_end, end))
    return holds


def _held_until(
    intervals: Iterable[tuple[datetime.datetime, Optional[datetime.datetime]]],
    cap_s: int,
    now: datetime.datetime,
) -> Optional[datetime.datetime]:
    """When the hold covering *now* lifts, or None when *now* is not held."""
    for hold in _holds(intervals, datetime.timedelta(seconds=cap_s)):
        if hold.start <= now <= hold.end:
            return hold.end
    return None


def _storm_intervals(
    times: Sequence[datetime.datetime], window_s: int
) -> list[tuple[datetime.datetime, Optional[datetime.datetime]]]:
    """The spans in which two of *times* lie within *window_s* back from then.

    Two blocks at ``a <= b`` are both inside the window from ``b`` until
    ``a + window``; consecutive pairs cover every such span.
    """
    window = datetime.timedelta(seconds=window_s)
    ordered = sorted(times)
    return [
        (later, earlier + window)
        for earlier, later in zip(ordered, ordered[1:])
        if later - earlier <= window
    ]


def _remedy(blocks: Iterable[HumanGatedBlock]) -> str:
    """A command printing the blockers and Block Record of each block."""
    patterns = " ".join(f"-e {shlex.quote('^## ' + name)}" for name in _REMEDY_SECTIONS)
    files = " ".join(shlex.quote(str(b.status_file)) for b in blocks)
    return f"grep -A{_REMEDY_CONTEXT_LINES} {patterns} {files}"


def _task_list(blocks: Iterable[HumanGatedBlock]) -> str:
    return ", ".join(b.task_id for b in blocks)


def decide(
    blocks: Sequence[HumanGatedBlock],
    window_s: int = DEFAULT_STORM_WINDOW_S,
    systemic_codes: Sequence[str] = DEFAULT_SYSTEMIC_REASON_CODES,
    cap_s: int = DEFAULT_HOLD_CAP_S,
    now: Optional[datetime.datetime] = None,
) -> GateDecision:
    """Decide whether *blocks* hold the project's task dispatch at *now*.

    A block on the systemic list is reported ahead of a storm, the list's
    own order choosing among several listed codes; among storms the largest
    wins, then the reason code in name order. Every further systemic reason
    code is named at the end of ``why``. The hold lifts when the last of
    the held reason codes lifts (``lifts_at``), and never more than *cap_s*
    after the block that began it.

    Args:
        blocks:         The project's human-gated blocks
                        (:func:`scan_human_gated_blocks`).
        window_s:       The storm window in seconds, measured back from *now*.
        systemic_codes: Reason codes that are systemic on their own.
        cap_s:          The longest any hold lasts, in seconds.
        now:            The decision time (default: the current UTC time).
                        A block time later than *now* is read as *now*.

    Returns:
        The decision: ``systemic`` (hold dispatch), ``task_local`` (blocks
        exist, none holding) or ``none`` (no human-gated block).
    """
    count = len(blocks)
    if count == 0:
        return GateDecision(VERDICT_NONE, 0, None, "no human-gated block")
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    window = datetime.timedelta(seconds=window_s)

    # Timed blocks per reason code; a block cannot have happened after now.
    by_code: dict[str, list[tuple[datetime.datetime, HumanGatedBlock]]] = {}
    for block in blocks:
        if block.reason_code is not None and block.blocked_at is not None:
            by_code.setdefault(block.reason_code, []).append((min(block.blocked_at, now), block))

    # (reason_code, why, blocks, lifts_at) for every held reason code, in report order.
    found: list[tuple[str, str, list[HumanGatedBlock], datetime.datetime]] = []
    for code in systemic_codes:
        if code not in by_code:
            continue
        lifts_at = _held_until(((at, None) for at, _ in by_code[code]), cap_s, now)
        if lifts_at is not None:
            members = [b for _, b in by_code[code]]
            found.append(
                (
                    code,
                    f"reason_code {code} is on the systemic list "
                    f"([wake] systemic_block_reasons): {_task_list(members)}",
                    members,
                    lifts_at,
                )
            )
    storms: list[tuple[str, list[HumanGatedBlock], datetime.datetime]] = []
    for code in sorted(by_code):
        if code in systemic_codes or code in STORM_EXEMPT_REASON_CODES:
            continue
        timed = by_code[code]
        lifts_at = _held_until(_storm_intervals([at for at, _ in timed], window_s), cap_s, now)
        if lifts_at is not None:
            in_window = sorted((pair for pair in timed if now - pair[0] <= window), key=lambda p: (p[0], p[1].task_id))
            storms.append((code, [b for _, b in in_window], lifts_at))
    storms.sort(key=lambda item: -len(item[1]))
    for code, cluster, lifts_at in storms:
        found.append(
            (
                code,
                f"{len(cluster)} human-gated blocks share reason_code {code} within "
                f"block_storm_window={window_s}s of now: {_task_list(cluster)}",
                cluster,
                lifts_at,
            )
        )

    if found:
        code, why, members, _ = found[0]
        lifts_at = max(item[3] for item in found)
        if len(found) > 1:
            why += "; also systemic: " + ", ".join(item[0] for item in found[1:])
        why += f"; the hold lifts at {format_iso(lifts_at)} (block_hold_cap={cap_s}s) unless resolved sooner"
        return GateDecision(
            VERDICT_SYSTEMIC,
            count,
            code,
            why,
            tuple(b.task_id for b in members),
            _remedy(members),
            lifts_at,
        )

    parts = [f"{b.task_id} ({b.reason_code or UNRECORDED})" for b in blocks]
    return GateDecision(
        VERDICT_TASK_LOCAL,
        count,
        None,
        f"{count} task-local block(s), none systemic: " + ", ".join(parts),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _warn(error_key: str, message: str, remedy: str) -> None:
    """Report a configuration value the decision set aside.

    The one warning site the CLI shares; the decision is still printed.
    """
    # CONVERT: a malformed storm window or hold cap falls back to its
    # default, an unparseable --now to the current time, and an unknown
    # systemic-list code is dropped; the gate still decides and the wake
    # still runs, so the cost is a hold decided on the default value.
    # CENSUS-ANCHOR: block-storm-config-01
    emit_error_contract(error_key, message, remedy)


def _block_record_codes_command() -> str:
    """A runnable command listing every Block Record reason code."""
    return f"python3 {shlex.quote(str(Path(block_record.__file__).resolve()))} codes"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.overwatch.block_storm",
        description=(
            "Decide whether a project's human-gated blocks (BLOCKED, Needs Human: yes) "
            "are systemic and so hold the wake's task dispatch for the whole project."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_decide = sub.add_parser(
        "decide",
        help="print the hold decision as key=value lines",
        description=(
            "Scan every *_backlog.md queue for [B] tasks that are BLOCKED with Needs Human: "
            "yes and print verdict, count, reason_code, why, remedy and lifts_at as key=value lines."
        ),
    )
    p_decide.add_argument("queue_dir", help="the project's tasks/queues directory")
    p_decide.add_argument("tasks_root", help="the directory holding one folder per task")
    p_decide.add_argument(
        "--id-pattern",
        default=None,
        help="regex every task id must match from its start (others are skipped)",
    )
    p_decide.add_argument(
        "--window",
        default="",
        help=(
            "storm window in whole seconds, measured back from now ([wake] block_storm_window); "
            f"blank means {DEFAULT_STORM_WINDOW_S}"
        ),
    )
    p_decide.add_argument(
        "--hold-cap",
        default="",
        help=(
            "longest hold in whole seconds ([wake] block_hold_cap); "
            f"blank means {DEFAULT_HOLD_CAP_S}"
        ),
    )
    p_decide.add_argument(
        "--now",
        default="",
        help="ISO-8601 time to decide at (default: now); the window and cap are measured from it",
    )
    p_decide.add_argument(
        "--systemic-codes",
        default="",
        help=(
            "comma- or space-separated reason codes that are systemic on their own "
            "([wake] systemic_block_reasons); blank means the default list, 'none' "
            "means no code"
        ),
    )

    sub.add_parser(
        "defaults",
        help="print the default storm window, hold cap and systemic list",
        description=(
            "Print window=SECONDS, hold_cap=SECONDS and systemic_codes=LIST, "
            "the values a blank setting means."
        ),
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """Run the block-storm CLI.

    ``defaults`` prints the default ``window``, ``hold_cap`` and
    ``systemic_codes`` as ``key=value`` lines; ``decide QUEUE_DIR TASKS_ROOT
    [...]`` prints the dispatch gate's decision as six ``key=value`` lines
    (verdict, count, reason_code, why, remedy, lifts_at). An unusable window,
    hold cap, ``--now`` or systemic code is warned about dual-channel and its
    default used. Exit codes: 0 always once arguments parse; 2 usage error
    (argparse).
    """
    args = _parser().parse_args(argv)
    if args.command == "defaults":
        print(f"window={DEFAULT_STORM_WINDOW_S}")
        print(f"hold_cap={DEFAULT_HOLD_CAP_S}")
        print(f"systemic_codes={','.join(DEFAULT_SYSTEMIC_REASON_CODES)}")
        return 0

    window_s, rejected = parse_window(args.window)
    if rejected is not None:
        _warn(
            "block_storm.invalid_window",
            f"block storm window {rejected!r} is not a whole number of seconds; "
            f"the dispatch gate used the default, {DEFAULT_STORM_WINDOW_S}s.",
            f"python3 -m pgai_agent_kanban.overwatch.block_storm decide {shlex.quote(args.queue_dir)} "
            f"{shlex.quote(args.tasks_root)} --window {DEFAULT_STORM_WINDOW_S}",
        )
    cap_s, rejected_cap = parse_window(args.hold_cap, DEFAULT_HOLD_CAP_S)
    if rejected_cap is not None:
        _warn(
            "block_storm.invalid_hold_cap",
            f"block hold cap {rejected_cap!r} is not a whole number of seconds; "
            f"the dispatch gate used the default, {DEFAULT_HOLD_CAP_S}s.",
            f"python3 -m pgai_agent_kanban.overwatch.block_storm decide {shlex.quote(args.queue_dir)} "
            f"{shlex.quote(args.tasks_root)} --hold-cap {DEFAULT_HOLD_CAP_S}",
        )
    now = try_parse_iso(args.now) if args.now.strip() else None
    if args.now.strip() and now is None:
        _warn(
            "block_storm.invalid_now",
            f"--now {args.now!r} is not an ISO-8601 time; the dispatch gate decided at the current time.",
            f"python3 -m pgai_agent_kanban.overwatch.block_storm decide {shlex.quote(args.queue_dir)} "
            f"{shlex.quote(args.tasks_root)} --now 2026-01-01T10:00:00+00:00",
        )
    codes, unknown = parse_systemic_codes(args.systemic_codes)
    if unknown:
        _warn(
            "block_storm.unknown_systemic_code",
            "[wake] systemic_block_reasons names "
            + ", ".join(repr(u) for u in unknown)
            + ", not a Block Record reason code; the dispatch gate ignored "
            + ("it." if len(unknown) == 1 else "them."),
            _block_record_codes_command(),
        )

    blocks = scan_human_gated_blocks(args.queue_dir, args.tasks_root, args.id_pattern)
    for line in decide(blocks, window_s, codes, cap_s, now).as_lines():
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
