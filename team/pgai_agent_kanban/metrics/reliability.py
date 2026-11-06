"""
reliability.py — the two reliability streaks of a project, and the
metrics/reliability.json file readers take them from.

A streak answers "how long has the chain run without a block?" as a pair
``(rcs, hours)``: the number of releases shipped since the resolution of
the last qualifying block, and the hours elapsed since that resolution.
There are two:

  hands_off  class "human" — since the last block a human had to clear.
  clean      class "any"   — since the last block of any kind, human
             blocks included, so Clean is never longer than Hands-off.

Inputs (files only — no subprocess, no git):

  <project>/metrics/blocked-intervals.jsonl
      the block ledger, read through
      pgai_agent_kanban.metrics.blocked_ledger.block_intervals() and
      parse_records(); each block's class, cause and resolution come from
      there, old-shape records included (they read as cause "chain",
      class "any").
  <project>/metrics/rc/<version>.json
      the per-RC rollups metrics_aggregator.py writes. An RC counts as
      shipped when its rollup says ``"outcome": "shipped"`` and carries a
      parseable ``closed_at``; it is placed in time by ``closed_at``. A
      rollup that does not parse, or lacks either field, is not counted.

Rules:

  - A block whose cause is "operator" (an operator-raised HALT interval)
    is left out of both streaks. It stays in the ledger, where it drives
    the per-RC blocked minutes.
  - The last block of a class is the qualifying block resolved most
    recently. A block still open is a current block: it is the last block,
    and the streak reads rcs 0, hours 0 (status "open").
  - rcs counts the shipped RCs whose closed_at is after the resolution,
    except the block's own RC (the version it was recorded under; a block
    recorded under blocked_ledger.NO_RC_VERSION has none). Versions are
    matched through pgai_agent_kanban.lib.semver, never as strings.
  - hours is the time from the resolution to *now*, rounded to 2 places.
  - With no qualifying block the status is "no block on record" and the
    streak counts from the earliest ledger record of any kind (operator
    records included — that is when the ledger began). With no ledger
    records at all there is nothing to count from: rcs 0, hours 0,
    since null.
  - Clean is never longer than Hands-off: each of Clean's rcs and hours is
    capped at Hands-off's.

reliability.json schema (written to <project>/metrics/reliability.json,
two-space indent, sorted keys, trailing newline; every timestamp is UTC
``YYYY-MM-DDThh:mm:ss+00:00``)::

    {
      "schema_version": 1,
      "project": "pgai-agent-kanban",
      "computed_at": "2026-10-18T12:00:00+00:00",
      "first_record_at": "2026-09-20T02:22:14+00:00",   # null: empty ledger
      "hands_off": <streak>,
      "clean": <streak>
    }

    <streak> = {
      "class": "human",              # "human" for hands_off, "any" for clean
      "status": "resolved",          # "resolved" | "open" | "no block on record"
      "rcs": 12,                     # shipped RCs since "since"; 0 when open
      "hours": 240.5,                # hours from "since" to computed_at; 0 when open
      "since": "2026-10-08T03:31:00+00:00",
                                     # resolved: the last block's resolution;
                                     # no block on record: first_record_at;
                                     # open: null
      "last_block": <block> | null   # null only for "no block on record"
    }

    <block> = {
      "time": "2026-10-08T01:00:00+00:00",   # when the block started
      "rc": "v2.40.0",               # version recorded; null for a no-RC block
      "task": "CM-...-release",      # the task ID for a "task:<ID>" source, else null
      "source": "task:CM-...-release",
      "reason_code": "agent_unclassified",   # why it was raised; may be null
      "class": "human",              # the block's own class
      "cause": "chain",
      "escalated": false,
      "resolution": {                # null while the block is still open
        "time": "2026-10-08T03:31:00+00:00",
        "class": "human",            # who cleared it
        "reason_code": "reset"       # how it was cleared; may be null
      } | null
    }

Refresh: write_reliability() rewrites the file whole (atomic replace).
blocked_ledger calls refresh_for_ledger() after every successful append,
and release_finalize.sh runs ``refresh`` once at RC close; both are
best-effort — a failed refresh never changes a ledger write's result or a
release's outcome, it only leaves the previous file in place.

Usage (CLI):
    python3 -m pgai_agent_kanban.metrics.reliability refresh --project NAME [--kanban-root DIR] [--now TS]
    python3 -m pgai_agent_kanban.metrics.reliability show    --project NAME [--kanban-root DIR] [--now TS]

Usage (import):
    from pgai_agent_kanban.metrics.reliability import reliability, write_reliability
    result = reliability("pgai-agent-kanban")
    result["hands_off"]["rcs"], result["clean"]["hours"]
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import sys

# Run by file path, this module has no parent package and only its own
# directory is on sys.path. Put this file's own package root -- the
# directory holding the pgai_agent_kanban/ this file sits in -- first, so
# the imports below resolve from the same tree, never another. Imported as
# a package member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.lib import semver  # noqa: E402
from pgai_agent_kanban.lib.atomic_write import atomic_write  # noqa: E402
from pgai_agent_kanban.lib.timeutil import format_iso, try_parse_iso  # noqa: E402
from pgai_agent_kanban.metrics.blocked_ledger import (  # noqa: E402
    CAUSE_OPERATOR,
    CLASS_ANY,
    CLASS_HUMAN,
    LedgerError,
    NO_RC_VERSION,
    block_intervals,
    parse_records,
)

SCHEMA_VERSION = 1

#: File name of the streak file, beside the ledger in <project>/metrics/.
RELIABILITY_FILENAME = "reliability.json"
#: File name of the ledger the refresh hook recognises.
LEDGER_FILENAME = "blocked-intervals.jsonl"
METRICS_DIRNAME = "metrics"
RC_ROLLUP_DIRNAME = "rc"

STATUS_RESOLVED = "resolved"
STATUS_OPEN = "open"
STATUS_NO_BLOCK = "no block on record"

_TASK_SOURCE_PREFIX = "task:"
_SHIPPED = "shipped"

_Timestamp = str | datetime.datetime | None


class ReliabilityError(LedgerError):
    """A refresh that could not complete (an unreadable --now, a failed
    write). Same dual-channel fields as LedgerError (error/message/remedy;
    str(exc) is the three-line house shape), so a caller catching
    LedgerError catches both."""


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def metrics_dir_for_project(kanban_root: str | os.PathLike, project: str) -> str:
    """<kanban_root>/projects/<project>/metrics — the directory holding the
    ledger, the rc/ rollups and reliability.json."""
    return os.path.join(os.fspath(kanban_root), "projects", project, METRICS_DIRNAME)


def reliability_path_for_project(kanban_root: str | os.PathLike, project: str) -> str:
    """<kanban_root>/projects/<project>/metrics/reliability.json."""
    return os.path.join(metrics_dir_for_project(kanban_root, project), RELIABILITY_FILENAME)


def _resolve_root(kanban_root: str | os.PathLike | None) -> str:
    if kanban_root is not None:
        return os.fspath(kanban_root)
    from pgai_agent_kanban.env import resolve_kanban_root

    return str(resolve_kanban_root())


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


def _as_datetime(value: _Timestamp) -> datetime.datetime:
    """*value* as an aware UTC datetime; the current time when None."""
    if value is None:
        return datetime.datetime.now(datetime.UTC)
    if isinstance(value, datetime.datetime):
        dt = value
    else:
        dt = try_parse_iso(str(value))
        if dt is None:
            raise ReliabilityError(
                "reliability.invalid-time",
                f"now={value!r} is not an ISO-8601 timestamp",
                "pass an ISO-8601 time, e.g. --now 2026-10-18T12:00:00+00:00",
            )
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.UTC)
    return dt


def shipped_rcs(rc_dir: str | os.PathLike) -> list:
    """The shipped RCs recorded under *rc_dir* (metrics/rc/), as a list of
    ``(version, closed_at)`` pairs — version the rollup's ``rc`` (or the
    file name's version), closed_at an aware datetime — ordered by version
    through semver. One entry per version; a rollup that does not parse,
    is not ``outcome: shipped``, or has no parseable closed_at is skipped.
    An absent directory is no RCs."""
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
        if str(payload.get("outcome") or "").strip().lower() != _SHIPPED:
            continue
        closed_at = try_parse_iso(str(payload.get("closed_at") or ""))
        if closed_at is None:
            continue
        version = str(payload.get("rc") or "").strip() or semver.from_filename(name)
        key = semver.parse_version(version)
        if key is None:
            continue
        by_key[key] = (version, closed_at)
    return [by_key[key] for key in sorted(by_key)]


def _rcs_after(rcs: list, since: datetime.datetime, own_version: str | None) -> int:
    """How many of *rcs* closed after *since*, not counting *own_version*."""
    own_key = semver.parse_version(own_version) if own_version else None
    return sum(
        1
        for version, closed_at in rcs
        if closed_at > since and (own_key is None or semver.parse_version(version) != own_key)
    )


def hours_between(since: datetime.datetime, now: datetime.datetime) -> float:
    """Hours from *since* to *now*, two decimals, never negative -- a
    streak's ``hours``. The view ages a stored streak with it too."""
    return round(max(0.0, (now - since).total_seconds() / 3600.0), 2)


# ---------------------------------------------------------------------------
# Computation
# ---------------------------------------------------------------------------


def _block_detail(block: dict) -> dict:
    """The schema's <block> object for one block_intervals() entry."""
    source = block["source"]
    task = source[len(_TASK_SOURCE_PREFIX) :].strip() if source.startswith(_TASK_SOURCE_PREFIX) else ""
    resolution = None
    if block["end"] is not None:
        resolution = {
            "time": format_iso(try_parse_iso(block["end"])),
            "class": block["resolution_class"],
            "reason_code": block["resolution_reason_code"],
        }
    return {
        "time": format_iso(try_parse_iso(block["start"])),
        "rc": None if block["version"] == NO_RC_VERSION else block["version"],
        "task": task or None,
        "source": source,
        "reason_code": block["reason_code"],
        "class": block["class"],
        "cause": block["cause"],
        "escalated": bool(block["escalated"]),
        "resolution": resolution,
    }


def _last_block(blocks: list) -> dict | None:
    """The block a streak runs from: an open block when there is one (the
    latest-started), otherwise the one resolved most recently."""
    open_blocks = [b for b in blocks if b["end"] is None]
    if open_blocks:
        return max(open_blocks, key=lambda b: try_parse_iso(b["start"]))
    if not blocks:
        return None
    return max(blocks, key=lambda b: try_parse_iso(b["end"]))


def _streak(
    block_class: str,
    blocks: list,
    rcs: list,
    first_record_at: datetime.datetime | None,
    now: datetime.datetime,
) -> dict:
    last = _last_block(blocks)
    if last is None:
        since = first_record_at
        return {
            "class": block_class,
            "status": STATUS_NO_BLOCK,
            "rcs": _rcs_after(rcs, since, None) if since else 0,
            "hours": hours_between(since, now) if since else 0.0,
            "since": format_iso(since) if since else None,
            "last_block": None,
        }
    if last["end"] is None:
        return {
            "class": block_class,
            "status": STATUS_OPEN,
            "rcs": 0,
            "hours": 0.0,
            "since": None,
            "last_block": _block_detail(last),
        }
    resolved_at = try_parse_iso(last["end"])
    own_version = None if last["version"] == NO_RC_VERSION else last["version"]
    return {
        "class": block_class,
        "status": STATUS_RESOLVED,
        "rcs": _rcs_after(rcs, resolved_at, own_version),
        "hours": hours_between(resolved_at, now),
        "since": format_iso(resolved_at),
        "last_block": _block_detail(last),
    }


def compute_reliability(
    project: str,
    metrics_dir: str | os.PathLike,
    *,
    now: _Timestamp = None,
) -> dict:
    """Both streaks for *project* from the ledger and rc/ rollups under
    *metrics_dir*, as the reliability.json document (module docstring).
    Reads files only. Raises LedgerError when the ledger is malformed — a
    malformed ledger yields no streak rather than a fabricated one."""
    metrics_dir = os.fspath(metrics_dir)
    now_dt = _as_datetime(now)
    ledger_path = os.path.join(metrics_dir, LEDGER_FILENAME)

    records = parse_records(ledger_path)
    stamps = [try_parse_iso(str(r["timestamp"])) for r in records]
    first_record_at = min(stamps) if stamps else None

    counted = [b for b in block_intervals(ledger_path) if b["cause"] != CAUSE_OPERATOR]
    human = [b for b in counted if b["class"] == CLASS_HUMAN]
    rcs = shipped_rcs(os.path.join(metrics_dir, RC_ROLLUP_DIRNAME))

    hands_off = _streak(CLASS_HUMAN, human, rcs, first_record_at, now_dt)
    clean = _streak(CLASS_ANY, counted, rcs, first_record_at, now_dt)
    # Human blocks are a subset of all blocks, so Clean's anchor is never
    # earlier than Hands-off's; the cap keeps the invariant when an RC
    # closed out of order would otherwise count toward Clean alone.
    clean["rcs"] = min(clean["rcs"], hands_off["rcs"])
    clean["hours"] = min(clean["hours"], hands_off["hours"])

    return {
        "schema_version": SCHEMA_VERSION,
        "project": project,
        "computed_at": format_iso(now_dt),
        "first_record_at": format_iso(first_record_at) if first_record_at else None,
        "hands_off": hands_off,
        "clean": clean,
    }


def reliability(
    project: str,
    *,
    kanban_root: str | os.PathLike | None = None,
    now: _Timestamp = None,
) -> dict:
    """Both streaks for *project* under *kanban_root* (default: the
    resolved PGAI_AGENT_KANBAN_ROOT_PATH). See compute_reliability()."""
    return compute_reliability(project, metrics_dir_for_project(_resolve_root(kanban_root), project), now=now)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _write_document(path: str, document: dict) -> None:
    content = json.dumps(document, indent=2, sort_keys=True) + "\n"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        atomic_write(path, content, prefix=".reliability_tmp_", preserve_mode=True)
    except OSError as exc:
        raise ReliabilityError(
            "reliability.write-failed",
            f"writing {path} failed: {exc}",
            f"confirm the directory is writable, then retry: " f"install -d -m 0755 {os.path.dirname(path)}",
        ) from exc


def write_reliability(
    project: str,
    *,
    kanban_root: str | os.PathLike | None = None,
    now: _Timestamp = None,
) -> str:
    """Compute both streaks for *project* and rewrite its
    metrics/reliability.json whole. Returns the path written. Raises
    LedgerError (malformed ledger) or ReliabilityError (write failed); on
    either the previous file is left as it was."""
    root = _resolve_root(kanban_root)
    path = reliability_path_for_project(root, project)
    document = compute_reliability(project, metrics_dir_for_project(root, project), now=now)
    _write_document(path, document)
    return path


def refresh_for_ledger(ledger_path: str | os.PathLike, *, now: _Timestamp = None) -> str | None:
    """Rewrite the reliability.json beside *ledger_path* — the hook
    blocked_ledger calls after every append. Only a ledger at the
    convention path ``<...>/<project>/metrics/blocked-intervals.jsonl`` has
    a project to refresh; any other path is skipped and returns None.
    Returns the path written. Raises like write_reliability()."""
    ledger = pathlib.Path(os.fspath(ledger_path)).absolute()
    metrics_dir = ledger.parent
    if ledger.name != LEDGER_FILENAME or metrics_dir.name != METRICS_DIRNAME:
        return None
    project = metrics_dir.parent.name
    path = str(metrics_dir / RELIABILITY_FILENAME)
    _write_document(path, compute_reliability(project, metrics_dir, now=now))
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list | None = None) -> int:
    """Run the reliability CLI.

    ``refresh`` rewrites the project's metrics/reliability.json and prints
    ``{"written": PATH}``; ``show`` prints the document without writing.
    Exit codes: 0 done; 1 the ledger is malformed, the kanban root is
    unknown or the write failed, reported through the dual-channel error
    contract on stderr; 2 usage error (argparse).
    """
    import argparse

    from pgai_agent_kanban.lib.error_contract import format_error_contract

    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.metrics.reliability",
        description=(
            "Compute a project's reliability streaks (Hands-off: since the "
            "last block a human cleared; Clean: since the last block of any "
            "kind) from its block ledger and per-RC rollups. 'refresh' "
            "rewrites metrics/reliability.json; 'show' prints the same JSON "
            "without writing."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, text in (
        ("refresh", "Rewrite projects/<project>/metrics/reliability.json."),
        ("show", "Print the streak document as JSON without writing it."),
    ):
        sub = subparsers.add_parser(name, help=text, description=text)
        sub.add_argument("--project", required=True, help="project name, e.g. pgai-agent-kanban")
        sub.add_argument(
            "--kanban-root",
            dest="kanban_root",
            default=None,
            help="kanban root holding projects/ (default: PGAI_AGENT_KANBAN_ROOT_PATH)",
        )
        sub.add_argument(
            "--now",
            default=None,
            help="ISO-8601 time to measure hours to, e.g. 2026-10-18T12:00:00+00:00 " "(default: the current time)",
        )
    args = parser.parse_args(argv)

    try:
        if args.command == "refresh":
            path = write_reliability(args.project, kanban_root=args.kanban_root, now=args.now)
            sys.stdout.write(json.dumps({"written": path}) + "\n")
        else:
            document = reliability(args.project, kanban_root=args.kanban_root, now=args.now)
            sys.stdout.write(json.dumps(document, indent=2, sort_keys=True) + "\n")
    except LedgerError as exc:
        print(format_error_contract(exc.error, exc.message, exc.remedy), file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(
            format_error_contract(
                "reliability.no-kanban-root",
                str(exc),
                'pass the root explicitly, e.g. --kanban-root "$HOME/pgai_agent_kanban"',
            ),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
