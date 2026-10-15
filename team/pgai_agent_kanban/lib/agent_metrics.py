"""
agent_metrics.py — Shared per-agent, per-project averaging arithmetic.

The ONE computation both the CLI twin
(``team/scripts/dashboard/agent-metrics.sh`` / ``team/scripts/lib/agent_metrics.py``)
and the REST twin (``GET /agent-metrics`` in ``team/pgai_agent_kanban/api/reads.py``)
call. Averages wall time, tokens, and task count per agent, per project,
and sums tokens and tasks per agent, across a SPAN of SHIPPED per-RC
metrics rollups (``projects/<name>/metrics/rc/v*.json``, produced by
``team/scripts/lib/metrics_aggregator.py`` -- read that module for the exact
``tokens.by_agent`` / ``tasks.by_agent`` field shapes; this module does not
change that schema).

Lives in the ``pgai_agent_kanban`` package -- not in ``team/scripts/lib`` --
so it ships with the package regardless of install topology (a live install
that relocates ``pgai_agent_kanban`` on its own, with no ``team/scripts/``
sibling, still has this module; see ``docs/coding-standards.md`` and
``team/roles/CODER.md``'s "Each tree finds itself" principle). The CLI's own
``team/scripts/lib/agent_metrics.py`` imports these functions from here
rather than defining a second copy -- it always runs from within the full
kanban tree, where this package is a guaranteed sibling, so reaching INTO
the package from a loose script is the safe direction; the reverse (this
package reaching OUT to ``team/scripts/``) is not, and is exactly what this
module's placement avoids.

WHICH RCS COUNT AS SHIPPED
==========================

An RC counts as shipped when its own rollup's ``outcome`` field is the
string ``shipped`` (matched after trimming surrounding whitespace and
ignoring letter case, so a hand-edited ``SHIPPED`` still counts). Every
other value -- ``cancelled``, ``failed``, ``in_progress``, ``unknown``, any
unrecognised string, a non-string value, or no ``outcome`` field at all --
is NOT shipped: that rollup contributes to neither the numerator nor the
denominator of any average or total, and it is counted as EXCLUDED instead
(see below). The rollup's own field is the only evidence read: this module
never shells out to git and never reads release-state JSON, so it behaves
identically on a gitless install and over test fixtures.

WHICH RCS EACH AVERAGE IS COMPUTED OVER (THE SPAN)
===================================================

For a given project, the RC rollup files under its ``metrics/rc/``
directory are sorted by semantic version (the filename stem, parsed by
``_version_sort_key()``), newest first, and one of three caller-chosen
spans (an ``RcSpan``; every bound is a caller-supplied argument, never a
hidden constant) selects the shipped rollups to compute over:

- ``RcSpan.last(n)`` -- the newest ``n`` SHIPPED rollups. The scan walks
  newest-first and stops once ``n`` shipped rollups are collected, so a
  non-shipped rollup never consumes a slot: a window of 10 over 12 shipped
  and 2 cancelled rollups still yields 10 shipped ones.
- ``RcSpan.all_shipped()`` -- every shipped rollup in the directory.
- ``RcSpan.since_version("vX.Y.Z")`` -- every shipped rollup whose version
  is at or above ``vX.Y.Z`` by semantic-version order (``v0.9.7`` is below
  ``v0.20.0``). The comparison reuses the same sort key; a malformed bound
  is the caller's usage error and raises ``ValueError`` rather than
  silently selecting nothing.

``select_rc_span()`` returns the selected rollups together with the
EXCLUDED COUNT: the number of readable, non-shipped rollups the scan
passed over inside the span's range -- for ``last(n)`` the rollups newer
than the oldest selected one (or every rollup, when fewer than ``n`` have
shipped), for ``all_shipped()`` the whole directory, for
``since_version()`` every rollup at or above the bound. A renderer
narrates it, e.g. "10 shipped RCs (2 cancelled excluded)". A file that is
missing, unreadable, or not a JSON object is a different case: it is
skipped with a stderr warning, consumes no slot, and is NOT part of the
excluded count, which counts only rollups whose outcome was read and was
not shipped.

For a single agent within that span, ``avg_tokens`` and ``avg_tasks``
are taken only over the RCs in the window where the agent has a non-zero
entry in that RC's ``tasks.by_agent`` -- i.e. the RCs the agent actually
worked -- and both are PER-RC means (token/task sum divided by the count
of invocation RCs, ``rc_count``). An agent absent from every RC in the
window has zero invocations and is simply absent from
``compute_agent_averages()``'s returned dict -- never a zero average or a
``ZeroDivisionError``.

``avg_wall_minutes`` is a PER-TASK mean, not a per-RC one, and reads a
different field: each RC rollup's ``wall``.``by_agent``.``<agent>`` block
(written by ``metrics_aggregator.py``'s ``aggregate_rc()``), which carries
that agent's own summed task wall-clock ``seconds`` and the ``task_count``
of tasks that contributed a usable value -- never the RC-level
``wall_time_minutes`` field, which is the whole RC's elapsed time shared
by every agent that touched it and therefore cannot answer "how long does
this agent's own work take" (the defect this module used to have: every
agent invoked in the same RCs rendered the identical figure). The average
pools ``seconds`` and ``task_count`` across every invocation RC in the
window that carries a ``wall.by_agent`` entry for the agent, THEN divides
the pooled sums -- so a window mixing RCs with different task loads still
weights every task equally, rather than averaging a per-RC average of
averages. An RC with no ``wall.by_agent`` entry for the agent (no task of
theirs in that RC had a usable ``elapsed_seconds``, or the RC rollup
predates this field entirely) contributes to neither sum, exactly as a
zero-task RC never contributes to ``avg_tokens``/``avg_tasks``. An agent
whose every invocation RC lacks that entry has ``avg_wall_minutes = None``
-- rendered as the placeholder, never a wrong number computed from a
mismatched denominator.

WHY A NEWLY ADDED WALL FIELD DOES NOT APPEAR IMMEDIATELY, AND WHY OLD
ROLLUPS ARE NEVER BACKFILLED
=======================================================================

An RC rollup lacking ``wall``.``by_agent`` for an agent has two distinct
causes this module deliberately does not -- and cannot -- distinguish: (a)
none of that agent's tasks in that RC had a usable ``elapsed_seconds``, or
(b) the RC was finalized before the live-install copy of
``metrics_aggregator.py`` carried the ``wall`` write at all (aggregator
fields take one release to reach rollups because the aggregator runs from
the live install at release-finalize time, not from the dev tree -- see
``metrics_aggregator.py``'s "ONE-RELEASE LAG" docstring section for the
full mechanism). Both cases are handled identically here: the RC is simply
excluded from that agent's wall average. Historical rollups are never
backfilled with the field after the fact -- see that same module's
"BACKFILL DECISION" section for why -- so an agent whose whole window
predates the field renders ``avg_wall_minutes = None`` / the CLI's ``--``
placeholder until enough wall-bearing RCs have shipped to fill the window;
this is expected steady-state behavior for WINDOW releases after the field
merges, not a defect to chase.

PER-AGENT TOTALS
================

Alongside the averages, ``compute_agent_averages()`` returns
``total_tokens`` and ``total_tasks`` per agent: the plain sums over that
agent's invocation RCs in the span (the same RCs ``rc_count`` counts), so
``avg_tokens * rc_count == total_tokens`` and ``avg_tasks * rc_count ==
total_tasks``. Averages are returned as unrounded floats.

THE ONE-DECIMAL ROUNDING RULE
=============================

Every surface that shows an average -- the CLI table and the REST JSON --
presents it to exactly one decimal place, rounded by ``round_one_decimal()``
below and by no other rule. The rule rounds the value's shortest decimal
representation half away from zero (2.25 -> 2.3, 2.35 -> 2.4, -2.25 ->
-2.3), so the digit a reader sees is the digit a person rounding by hand
would write -- unlike the built-in ``round()`` and ``'{:.1f}'``, which
round the binary value half-to-even and turn 2.25 into 2.2. The CLI then
hands the rounded value to ``number_format.format_one_decimal()`` for
thousands separation; the REST twin emits the rounded value as a JSON
number.

USAGE:

    from pgai_agent_kanban.lib.agent_metrics import (
        RcSpan, compute_agent_averages, select_rc_span,
    )
    selection = select_rc_span(rc_dir, RcSpan.last(10))
    averages = compute_agent_averages(selection.rollups)
    # selection.shipped_count == 10, selection.excluded_count == 2
    # averages["coder"] == {"avg_wall_minutes": 12.5, "avg_tokens": 41000.0,
    #                        "avg_tasks": 2.0, "rc_count": 4,
    #                        "total_tokens": 164000, "total_tasks": 8}
    #
    # load_rc_window(rc_dir, window=10) is shorthand for
    # select_rc_span(rc_dir, RcSpan.last(10)).rollups.
    # avg_wall_minutes is the mean per-TASK wall time (seconds summed
    # across every invocation RC's wall.by_agent entry, divided by the
    # summed task_count, converted to minutes); avg_tokens and avg_tasks
    # are means per invocation RC (rc_count). An agent with zero
    # invocations in the window is simply absent from the returned dict --
    # callers render its row as all '--' or, for the REST twin, omit it
    # from the JSON 'agents' array.
"""

from __future__ import annotations

import json
import pathlib
import sys
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

# Run by file path (``python3 .../agent_metrics.py``) or loaded from a file
# outside its package, this module has no parent package and the interpreter
# puts only its own directory on sys.path, so the package import below would
# not resolve. Put this file's own package root -- the directory holding the
# pgai_agent_kanban/ this file sits in -- at the front of sys.path so the
# import resolves from the same tree, never another. Imported as a package
# member, sys.path is left untouched. Run by path, the module defines its
# functions and exits 0; it has no command-line interface of its own.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.lib import semver  # noqa: E402

__all__ = [
    "SHIPPED_OUTCOME",
    "RcSpan",
    "RcSpanSelection",
    "is_shipped",
    "round_one_decimal",
    "total_tokens",
    "compute_agent_averages",
    "select_rc_span",
    "load_rc_window",
]

# The one rollup ``outcome`` value that counts an RC as shipped. See the
# module docstring's "WHICH RCS COUNT AS SHIPPED" section.
SHIPPED_OUTCOME = "shipped"


# ---------------------------------------------------------------------------
# Token accounting
# ---------------------------------------------------------------------------


def total_tokens(entry: dict[str, Any] | None) -> int:
    """Sum the four token counters in a tokens.by_agent[<agent>] entry.

    Returns 0 when `entry` is None or missing individual fields -- matches
    metrics_aggregator.py's own _zero_token_entry() shape.
    """
    if not entry:
        return 0
    return (
        int(entry.get("input", 0) or 0)
        + int(entry.get("output", 0) or 0)
        + int(entry.get("cache_read", 0) or 0)
        + int(entry.get("cache_write", 0) or 0)
    )


# ---------------------------------------------------------------------------
# Display rounding (shared by the CLI and REST surfaces)
# ---------------------------------------------------------------------------

_ONE_DECIMAL = Decimal("0.1")


def round_one_decimal(value: float | int | None) -> float | None:
    """Round an average to one decimal place by the shared display rule.

    The value's shortest decimal representation (``repr()``) is rounded
    half away from zero, so ``2.25 -> 2.3`` and ``41000 -> 41000.0``. See
    the module docstring's "THE ONE-DECIMAL ROUNDING RULE". ``None`` (an
    absent average, e.g. no wall-time data) passes through as ``None``.
    Raises ValueError for a non-finite value, which no average this module
    computes can produce.
    """
    if value is None:
        return None
    dec = Decimal(repr(float(value)))
    if not dec.is_finite():
        raise ValueError(f"cannot round a non-finite average to one decimal (got: {value!r})")
    return float(dec.quantize(_ONE_DECIMAL, rounding=ROUND_HALF_UP))


# ---------------------------------------------------------------------------
# Core averaging arithmetic (pure function; no I/O)
# ---------------------------------------------------------------------------


def compute_agent_averages(
    rc_rollups: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Compute per-agent averages over an already-loaded window of RC rollups.

    Args:
      rc_rollups: a list of per-RC rollup dicts, each shaped like
        metrics_aggregator.py's aggregate_rc() output (only the
        "tasks"."by_agent", "tokens"."by_agent", and "wall"."by_agent"
        keys are read; extra keys, including the RC-level
        "wall_time_minutes", are ignored -- see the module docstring for
        why the RC-level field is the wrong one to average). Order does
        not matter.

    Returns:
      A dict keyed by agent name. Each value is:
        {
          "avg_wall_minutes": float | None,  # per-TASK mean; None when no
                                              # invocation RC in the window
                                              # carries a "wall"."by_agent"
                                              # entry for this agent
          "avg_tokens":        float,        # per-RC mean (rc_count)
          "avg_tasks":         float,        # per-RC mean (rc_count)
          "rc_count":          int,          # RCs in the window this
                                              # agent was invoked in --
                                              # the avg_tokens/avg_tasks
                                              # denominator
          "total_tokens":      int,          # sum of the four token
                                              # counters over those RCs
          "total_tasks":       int,          # sum of task counts over
                                              # those RCs
        }
      Averages are unrounded floats. Every rollup passed in is counted:
      choosing which rollups enter (shipped only, which span) is
      select_rc_span()'s job, not this function's.
      An agent with zero invocations across the whole window is simply
      absent from the returned dict -- there is never a zero-average or
      a division-by-zero entry for it. Callers render an absent agent (or
      an agent name they expected but do not find here) as '--' for every
      column (CLI) or omit it entirely (REST).
    """
    # agent -> list of (tasks_count, token_total) -- one entry per invocation RC
    per_agent: dict[str, list[tuple[int, int]]] = {}
    # agent -> list of (wall_seconds, wall_task_count) -- one entry per RC
    # that carries a usable "wall"."by_agent" entry for this agent; pooled
    # (not averaged per RC first) so every task weighs equally regardless
    # of how tasks are distributed across the window's RCs.
    per_agent_wall: dict[str, list[tuple[float, int]]] = {}

    for rc in rc_rollups:
        tasks_by_agent = (rc.get("tasks") or {}).get("by_agent") or {}
        tokens_by_agent = (rc.get("tokens") or {}).get("by_agent") or {}
        wall_by_agent = (rc.get("wall") or {}).get("by_agent") or {}

        for agent, task_count in tasks_by_agent.items():
            task_count = int(task_count or 0)
            if task_count <= 0:
                continue  # zero tasks in this RC is not an invocation
            tok_total = total_tokens(tokens_by_agent.get(agent))
            per_agent.setdefault(agent, []).append((task_count, tok_total))

        for agent, entry in wall_by_agent.items():
            if not isinstance(entry, dict):
                continue
            seconds = entry.get("seconds")
            wall_task_count = entry.get("task_count")
            # Liberal about numeric shape (int or float; reject bool, which
            # is an int subclass and would silently count True/False as
            # 1/0) but strict about requiring an actual positive
            # denominator -- an entry with task_count <= 0 carries no
            # usable timing and must not enter the pool, never divide by
            # zero downstream.
            if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
                continue
            if isinstance(wall_task_count, bool) or not isinstance(wall_task_count, (int, float)):
                continue
            wall_task_count = int(wall_task_count)
            if wall_task_count <= 0:
                continue
            per_agent_wall.setdefault(agent, []).append((float(seconds), wall_task_count))

    result: dict[str, dict[str, Any]] = {}
    for agent, records in per_agent.items():
        rc_count = len(records)
        if rc_count == 0:
            continue  # unreachable given the setdefault-on-append above; guard kept explicit

        task_sum = sum(r[0] for r in records)
        token_sum = sum(r[1] for r in records)

        wall_records = per_agent_wall.get(agent) or []
        avg_wall: float | None
        if wall_records:
            total_seconds = sum(r[0] for r in wall_records)
            total_wall_tasks = sum(r[1] for r in wall_records)
            avg_wall = (total_seconds / total_wall_tasks) / 60.0
        else:
            avg_wall = None

        result[agent] = {
            "avg_wall_minutes": avg_wall,
            "avg_tokens": token_sum / rc_count,
            "avg_tasks": task_sum / rc_count,
            "rc_count": rc_count,
            "total_tokens": token_sum,
            "total_tasks": task_sum,
        }

    return result


# ---------------------------------------------------------------------------
# Version sort (parsing is pgai_agent_kanban.lib.semver.parse)
# ---------------------------------------------------------------------------


# Sort key every malformed version maps to; it orders below every
# well-formed version, and RcSpan.since_version() rejects a bound that maps
# to it.
_MALFORMED_VERSION_KEY: tuple[int, int, int] = (-1, -1, -1)


def _version_sort_key(version: str) -> tuple[int, int, int]:
    """Parse 'vX.Y.Z' into a (X, Y, Z) tuple for sort(). Input that
    ``semver.parse`` rejects sorts before every well-formed version (key
    ``_MALFORMED_VERSION_KEY``)."""
    try:
        return semver.parse(version)
    except ValueError:
        return _MALFORMED_VERSION_KEY


# ---------------------------------------------------------------------------
# Shipped-outcome test and span selection
# ---------------------------------------------------------------------------


def is_shipped(rollup: dict[str, Any]) -> bool:
    """True when `rollup`'s own ``outcome`` field names a shipped RC.

    Liberal about surrounding whitespace and letter case; anything that
    is not a string equal to ``shipped`` under that normalisation --
    including an absent field -- is not shipped.
    """
    outcome = rollup.get("outcome")
    return isinstance(outcome, str) and outcome.strip().lower() == SHIPPED_OUTCOME


_SPAN_LAST = "last"
_SPAN_ALL = "all"
_SPAN_SINCE = "since"


@dataclass(frozen=True)
class RcSpan:
    """Which shipped RC rollups to compute over:

      RcSpan.last(10)                  -- the newest 10 shipped rollups
      RcSpan.all_shipped()             -- every shipped rollup
      RcSpan.since_version("v0.20.0")  -- every shipped rollup >= v0.20.0

    `kind` is one of ``"last"``, ``"all"``, ``"since"``; `count` is set
    only for ``last`` and `since` only for ``since``. Construction
    validates the bound and raises ValueError on a usage error -- a
    non-positive count, or a version that does not parse as vX.Y.Z -- so
    a bad bound never silently selects an empty span.
    """

    kind: str
    count: int | None = None
    since: str | None = None

    def __post_init__(self) -> None:
        if self.kind == _SPAN_LAST:
            count = self.count
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise ValueError(
                    f"span 'last' needs a positive integer count of shipped RCs (got: {count!r}); "
                    "pass e.g. RcSpan.last(10)"
                )
        elif self.kind == _SPAN_SINCE:
            version = self.since
            if not isinstance(version, str) or _version_sort_key(version) == _MALFORMED_VERSION_KEY:
                raise ValueError(
                    f"span 'since' needs a version of the form vX.Y.Z (got: {version!r}); "
                    "pass e.g. RcSpan.since_version('v0.20.0')"
                )
        elif self.kind != _SPAN_ALL:
            raise ValueError(
                f"unknown span kind {self.kind!r}; build the span with RcSpan.last(), "
                "RcSpan.all_shipped() or RcSpan.since_version()"
            )

    @classmethod
    def last(cls, count: int) -> "RcSpan":
        """The newest `count` shipped rollups (`count` >= 1)."""
        return cls(kind=_SPAN_LAST, count=count)

    @classmethod
    def all_shipped(cls) -> "RcSpan":
        """Every shipped rollup in the directory."""
        return cls(kind=_SPAN_ALL)

    @classmethod
    def since_version(cls, version: str) -> "RcSpan":
        """Every shipped rollup at or above `version` (``vX.Y.Z`` or
        ``X.Y.Z``) by semantic-version order."""
        if isinstance(version, str):
            version = version.strip()
        return cls(kind=_SPAN_SINCE, since=version)


@dataclass(frozen=True)
class RcSpanSelection:
    """The result of select_rc_span().

    rollups:        the shipped rollups in the span, newest version first --
                    exactly the list to hand to compute_agent_averages().
    excluded_count: readable, non-shipped rollups the scan passed over
                    inside the span's range (see the module docstring).
                    Unreadable or non-object files are not counted here.
    span:           the RcSpan that produced this selection.
    """

    rollups: list[dict[str, Any]]
    excluded_count: int
    span: RcSpan

    @property
    def shipped_count(self) -> int:
        """Number of shipped rollups selected (the span's RC count)."""
        return len(self.rollups)


def _read_rollup(path: pathlib.Path) -> dict[str, Any] | None:
    """Read one rollup file; warn on stderr and return None when it is
    unreadable, not valid JSON, or not a JSON object."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"WARNING: skipping malformed RC rollup {path}: {exc}",
            file=sys.stderr,
        )
        return None
    if not isinstance(data, dict):
        print(
            f"WARNING: skipping RC rollup {path}: top-level value is not a JSON object",
            file=sys.stderr,
        )
        return None
    return data


def select_rc_span(rc_dir: pathlib.Path, span: RcSpan) -> RcSpanSelection:
    """Select the shipped RC rollups under `rc_dir` that `span` names.

    Files (``v*.json``) are visited newest version first. Each readable
    rollup is either shipped -- and selected while the span still has
    room -- or not shipped, and counted in ``excluded_count`` without
    taking a slot. The scan stops at the first rollup outside the span:
    once ``last(n)`` holds `n` shipped rollups, or at the first version
    below a ``since_version()`` bound. An unreadable or non-object file is
    skipped with a stderr warning and counts toward neither figure.

    Returns an empty selection (no error) when `rc_dir` does not exist or
    holds no matching files -- the read-only, missing-data-renders-a-
    placeholder contract this module's callers depend on.
    """
    if not rc_dir.is_dir():
        return RcSpanSelection(rollups=[], excluded_count=0, span=span)

    candidates = sorted(
        rc_dir.glob("v*.json"),
        key=lambda p: _version_sort_key(p.stem),
        reverse=True,
    )
    since_key = _version_sort_key(span.since) if span.kind == _SPAN_SINCE and span.since is not None else None

    rollups: list[dict[str, Any]] = []
    excluded = 0
    for path in candidates:
        if span.kind == _SPAN_LAST and span.count is not None and len(rollups) >= span.count:
            break
        if since_key is not None and _version_sort_key(path.stem) < since_key:
            break  # sorted newest first: every remaining file is below the bound
        data = _read_rollup(path)
        if data is None:
            continue
        if is_shipped(data):
            rollups.append(data)
        else:
            excluded += 1

    return RcSpanSelection(rollups=rollups, excluded_count=excluded, span=span)


def load_rc_window(rc_dir: pathlib.Path, window: int) -> list[dict[str, Any]]:
    """Load the newest `window` SHIPPED per-RC rollups under `rc_dir`.

    Shorthand for ``select_rc_span(rc_dir, RcSpan.last(window)).rollups``:
    non-shipped rollups are skipped without consuming a window slot, and a
    file that does not exist, is not valid JSON, or does not decode to a
    JSON object is skipped with a stderr warning without consuming one
    either -- e.g. requesting window=10 against a directory with two
    shipped files and three malformed ones returns 2 rollups, not 0.

    Returns an empty list (with no error) when `window` is not positive,
    or when `rc_dir` does not exist or contains no matching files.
    """
    if isinstance(window, bool) or not isinstance(window, int) or window < 1:
        return []
    return select_rc_span(rc_dir, RcSpan.last(window)).rollups
