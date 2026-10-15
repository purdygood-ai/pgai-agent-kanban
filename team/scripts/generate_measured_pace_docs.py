#!/usr/bin/env python3
"""
generate_measured_pace_docs.py
================================
Renders a "measured pace" block -- per-task wall-time range, its central
tendency, a composed RC-cost figure, and the sample size behind all three
-- into marker-delimited generated blocks in README.md and HOW_TO.md. In
`--check` mode, refuses when a rendered block has drifted from what's on
disk (a hand-edited figure, most likely).

WHERE THE NUMBERS COME FROM
============================
Every figure in the rendered block is derived from the project's own
per-RC metric rollups (`projects/<project>/metrics/rc/v*.json`, produced
by `team/scripts/lib/metrics_aggregator.py`). This script reads them; it
never writes them -- they are kanban process state, not dev-tree content.

This script does not re-implement the averaging arithmetic. Per-agent
task averages come from `compute_agent_averages()` in
`pgai_agent_kanban.lib.agent_metrics` -- the one module both the CLI
(`team/scripts/dashboard/agent-metrics.sh`) and the REST twin
(`GET /agent-metrics`) already call. This script only composes a new
question ("what does a task, and an RC, cost in minutes") on top of that
shared arithmetic; it does not define a second per-agent average that
could disagree with the dashboard.

WHAT "PER-TASK MINUTES" MEANS -- READ THIS BEFORE TRUSTING THE NUMBER
========================================================================
The rollup schema records ONE wall-clock figure per RC --
`wall_time_minutes`, computed from the RC's own `opened_at`/`closed_at`
timestamps -- and a task count per agent. It does not record how long any
INDIVIDUAL task took. There is no per-task timer anywhere in the pipeline
to derive one from.

This script's "per-task minutes" is therefore a derived proxy, not a
direct measurement of an individual task: an RC's own wall_time_minutes
divided by that RC's total task count, i.e. the RC's clock-to-clock
duration spread evenly across the tasks that happened during it. That
proxy is deliberately clock-to-clock (it includes the wake-cadence idle
time between one task finishing and the next agent's wake tick picking up
the baton), not an isolated active-compute duration -- which is the
figure a newcomer actually wants when asking "how long until my task is
done," and the figure the docs' own prior prose ("a couple of hours of
wall-clock time") was already trying to describe. The header on the
rendered block repeats this in short form so nobody mistakes the number
for a stopwatch reading on a single task.

WINDOW CHOICE
=============
The default window is EVERY rollup file the project has (`--window`
overrides this with a fixed count, newest-first, matching
`agent-metrics.sh`'s own `--window` semantics). This differs from that
script's own default of 10: agent-metrics.sh exists to show a *live
operational* average (how is the shop doing lately), where recency is the
point. This script exists to answer a newcomer's *pace* question once,
in prose, where sample size matters more than recency and a wrong recency
assumption biases the published number. A check of this project's own
history (56 older RCs vs. 56 newer RCs, split at the midpoint) found the
newer half's median per-task minutes was NOT lower than the older half's
(20.0 vs. 16.2) -- there is no evidence recent RCs are "more
representative" of a newcomer's likely experience than older ones, so
trimming to a recent window would only shrink the sample without buying
accuracy. Full-history is therefore the more defensible default; the
rendered block always names the window it used (an RC count and a task
count) so a reader can judge the claim for themselves regardless of which
default this script ships with next.

CENTRAL TENDENCY: MEDIAN, NOT MEAN
====================================
`wall_time_minutes` is real clock time, including any gap where an RC sat
open between wake ticks (overnight, a stalled dependency, an operator
pause). Those gaps produce a heavy right tail -- this project's own
history has per-task-minute values from under 6 minutes to over 140
minutes in the same window. A mean is pulled hard by a handful of such
RCs; a median is not. The rendered block's headline figure is the median;
the full min-max range is rendered alongside it precisely so an outlier-
skewed mean is never the only number a reader sees.

CONFIGURATION FACTS ARE OUT OF SCOPE
=======================================
`max_task_seconds`, the wake-tick cron cadence, and any other configured
ceiling or schedule are facts about configuration, not measurements of
what actually happened -- this script never touches prose describing
them, and never folds them into a rendered range. `docs/coding-
standards.md`'s key-parity lint is what keeps *those* claims honest; this
script's remit is measurements only.

THIN OR ABSENT DATA
======================
A fresh install (or a project that has not shipped an RC yet) has no
rollups to read. Rendering a zero or a fabricated placeholder range would
be exactly the folklore this script exists to replace, so it renders
neither: zero usable rollups produces an explicit "not measured yet"
block with no numbers in it at all. Between 1 and `_THIN_THRESHOLD - 1`
usable rollups still renders the real, honestly-computed numbers (a
range of one point is still the true range of that one point) but flags
the sample as thin inline, so a reader knows not to treat it as settled.

Usage:
    python3 generate_measured_pace_docs.py             # render: write both docs
    python3 generate_measured_pace_docs.py --check      # verify: report drift, don't write

Options:
    --check                Compare rendered content against what's on disk;
                            print every disagreement and exit 1 without
                            writing anything. Exit 0 when everything already
                            matches.
    --project NAME          Project whose rollups to read. Default:
                             $PGAI_PROJECT_NAME or 'pgai-agent-kanban'.
    --kanban-root PATH       Override $PGAI_AGENT_KANBAN_ROOT_PATH.
    --rc-dir PATH            Read rollups directly from this directory
                             instead of resolving --project/--kanban-root
                             (used by tests against fixture rollup sets;
                             takes precedence over --project/--kanban-root
                             when given).
    --window N               Number of newest rollups to read. Default:
                             every rollup file present (see WINDOW CHOICE
                             above).
    --readme PATH            README.md to render/check. Default: this
                             file's own dev-tree README.md.
    --how-to PATH             HOW_TO.md to render/check. Default: this
                             file's own dev-tree HOW_TO.md.
    --help, -h               Show this help and exit.

Exit codes:
    0   Render succeeded, or --check found no drift.
    1   --check found one or more disagreements (CONVERT: a stale rendered
        figure carries zero runtime-failure risk -- see the refusal site
        below for the full classification rationale).
    2   Usage error, an unreadable --rc-dir, or a target file missing its
        marker pair (a structural problem the render can't repair itself).
"""

from __future__ import annotations

import argparse
import pathlib
import statistics
import sys
from dataclasses import dataclass, field
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Dev-tree root resolution -- each tree finds itself.
# This file lives at team/scripts/generate_measured_pace_docs.py.
# Two parent levels: scripts/ -> team/ -> project_root/
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent
_DEV_TREE_ROOT = _TEAM_DIR.parent
_LIB_DIR = _TEAM_DIR / "scripts" / "lib"

_team_dir_str = str(_TEAM_DIR)
if _team_dir_str not in sys.path:
    sys.path.insert(0, _team_dir_str)
_lib_dir_str = str(_LIB_DIR)
if _lib_dir_str not in sys.path:
    sys.path.insert(0, _lib_dir_str)

from pgai_agent_kanban.lib.agent_metrics import (  # noqa: E402
    compute_agent_averages,
    load_rc_window,
)
from pgai_agent_kanban.lib.error_contract import emit_error_contract  # noqa: E402

import metrics_aggregator as _agg  # noqa: E402  -- resolve_kanban_root/resolve_project_dir

_DEFAULT_README = _DEV_TREE_ROOT / "README.md"
_DEFAULT_HOW_TO = _DEV_TREE_ROOT / "HOW_TO.md"
_DEFAULT_PROJECT = "pgai-agent-kanban"

_BEGIN = "<!-- MEASURED-PACE-GENERATED:BEGIN -->"
_END = "<!-- MEASURED-PACE-GENERATED:END -->"

# Fewer than this many usable RCs and the "range" is one or two points --
# real, but not yet a distribution. Flagged inline rather than withheld,
# since one or two honestly-measured points beat a guessed range.
_THIN_THRESHOLD = 3

# Rendered in the order the brief's own shorthand names them (N coder tasks
# + tester + bookends); writer is real in the data and gets its own line
# rather than being silently folded into the total.
_COMPOSED_ORDER = ("pm", "coder", "writer", "tester", "cm")
_BOOKEND_AGENTS = ("pm", "cm")


# ---------------------------------------------------------------------------
# Computation (pure; no I/O)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PaceStats:
    """The computed measured-pace figures for one window of RC rollups.

    `has_data=False` means zero usable rollups -- every other field is
    None/empty and the renderer emits the explicit "not measured yet"
    block rather than a fabricated number.
    """

    has_data: bool
    rc_count: int
    task_count: int
    per_task_low: Optional[float] = None
    per_task_median: Optional[float] = None
    per_task_high: Optional[float] = None
    per_task_mean: Optional[float] = None
    thin: bool = False
    window_requested: int = 0
    window_loaded: int = 0
    window_oldest: Optional[str] = None
    window_newest: Optional[str] = None
    agent_avg_tasks: dict[str, float] = field(default_factory=dict)
    composed: dict[str, float] = field(default_factory=dict)
    composed_total: Optional[float] = None


def compute_pace_stats(
    rc_rollups: list[dict[str, Any]],
    *,
    window_requested: int = 0,
    thin_threshold: int = _THIN_THRESHOLD,
) -> PaceStats:
    """Compute PaceStats over an already-loaded window of RC rollups.

    Args:
      rc_rollups: rollup dicts as returned by `load_rc_window()`, newest
        first. Order matters only for `window_oldest`/`window_newest`
        reporting -- the arithmetic itself does not depend on order.
      window_requested: the `--window` value the caller asked for (0 means
        "everything available"), carried through for the rendered header.
      thin_threshold: fewer than this many usable RCs sets `thin=True`.

    Returns:
      A PaceStats. `has_data=False` when no rollup in the window has both
      a populated `wall_time_minutes` and at least one task -- the
      "nothing to measure yet" case.
    """
    window_loaded = len(rc_rollups)
    window_oldest = rc_rollups[-1].get("rc") if rc_rollups else None
    window_newest = rc_rollups[0].get("rc") if rc_rollups else None

    usable: list[tuple[float, int]] = []
    for rc in rc_rollups:
        wall = rc.get("wall_time_minutes")
        total = (rc.get("tasks") or {}).get("total") or 0
        if wall is not None and total > 0:
            usable.append((float(wall), int(total)))

    if not usable:
        return PaceStats(
            has_data=False,
            rc_count=0,
            task_count=0,
            window_requested=window_requested,
            window_loaded=window_loaded,
            window_oldest=window_oldest,
            window_newest=window_newest,
        )

    per_task = [wall / total for wall, total in usable]
    rc_count = len(usable)
    task_count = sum(total for _, total in usable)
    median = statistics.median(per_task)
    mean = statistics.mean(per_task)
    low = min(per_task)
    high = max(per_task)
    thin = rc_count < thin_threshold

    agent_averages = compute_agent_averages(rc_rollups)
    agent_avg_tasks = {
        agent: vals["avg_tasks"] for agent, vals in agent_averages.items()
    }
    composed = {
        agent: round(avg_tasks * median, 1)
        for agent, avg_tasks in agent_avg_tasks.items()
    }
    composed_total = round(sum(composed.values()), 1) if composed else None

    return PaceStats(
        has_data=True,
        rc_count=rc_count,
        task_count=task_count,
        per_task_low=round(low, 1),
        per_task_median=round(median, 1),
        per_task_high=round(high, 1),
        per_task_mean=round(mean, 1),
        thin=thin,
        window_requested=window_requested,
        window_loaded=window_loaded,
        window_oldest=window_oldest,
        window_newest=window_newest,
        agent_avg_tasks=agent_avg_tasks,
        composed=composed,
        composed_total=composed_total,
    )


def full_window_size(rc_dir: pathlib.Path) -> int:
    """Return the count of `v*.json` rollup files under `rc_dir` (0 if the
    directory does not exist). Used as the effective `--window` value when
    the caller does not name one -- see WINDOW CHOICE in the module
    docstring for why "everything available" is this script's default.
    """
    if not rc_dir.is_dir():
        return 0
    return sum(1 for _ in rc_dir.glob("v*.json"))


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _composed_formula_text(stats: PaceStats) -> str:
    """Render the composed-RC-cost formula as 'N×coder + tester + bookends'
    style prose, using the measured avg-tasks-per-RC for each role that is
    actually present in the window.
    """
    parts: list[str] = []
    for agent in _COMPOSED_ORDER:
        if agent not in stats.composed:
            continue
        n = stats.agent_avg_tasks[agent]
        if agent in _BOOKEND_AGENTS:
            label = f"{agent} bookend"
        else:
            label = agent
        parts.append(f"{n:.1f}×{label}")
    return " + ".join(parts) if parts else "(no agent data in window)"


def render_pace_block(stats: PaceStats) -> str:
    """Render the full BEGIN/END marker block (both inclusive) for one
    PaceStats. Identical content is placed in both README.md and HOW_TO.md
    -- one measured figure, not two independently-worded ones.
    """
    lines = [_BEGIN]
    lines.append(
        "<!-- generated by team/scripts/generate_measured_pace_docs.py from"
    )
    lines.append(
        "     projects/<project>/metrics/rc/v*.json. Do not hand-edit"
    )
    lines.append(
        "     between these markers; run 'python3 team/scripts/"
    )
    lines.append(
        "     generate_measured_pace_docs.py' (from the repo root) and"
    )
    lines.append(
        "     commit the result instead. -->"
    )
    lines.append("")

    if not stats.has_data:
        lines.append(
            "**Measured pace:** not available yet -- no per-RC metric "
            "rollups were found for this project (a fresh install, or a "
            "project that has not shipped an RC, has nothing to measure). "
            "This is not rendered as a zero or a guess; it ships once RCs "
            "have run and `python3 team/scripts/"
            "generate_measured_pace_docs.py` is re-run."
        )
        lines.append("")
        lines.append(_END)
        return "\n".join(lines)

    thin_note = ""
    if stats.thin:
        thin_note = (
            f" -- thin sample ({stats.rc_count} RC(s)); treat as "
            "provisional until more RCs have shipped"
        )

    lines.append(
        f"**Per-task wall time (measured):** {stats.per_task_low:.1f}"
        f"–{stats.per_task_high:.1f} min, median "
        f"{stats.per_task_median:.1f} min. Sample: {stats.rc_count} RC(s), "
        f"{stats.task_count} task(s), window = every rollup available at "
        f"render time ({stats.window_oldest} .. {stats.window_newest})"
        f"{thin_note}. This is an RC's own wall-clock duration spread "
        "evenly across its tasks (clock-to-clock, including wake-cadence "
        "idle time), not an isolated per-task stopwatch reading -- see "
        "this generator's module docstring for why."
    )
    lines.append("")
    lines.append(
        f"**Composed RC cost (measured):** ~{stats.composed_total:.0f} min "
        f"= {_composed_formula_text(stats)}, each term the average tasks "
        "per RC for that role times the median per-task minutes above."
    )
    lines.append("")
    lines.append(_END)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Marker-pair replace/extract (same primitive generate_window_docs.py and
# generate_operator_verb_docs.py both use)
# ---------------------------------------------------------------------------


class MarkerNotFoundError(ValueError):
    """The MEASURED-PACE-GENERATED marker pair is missing from a target file."""


def _replace_between_markers(
    content: str, begin_marker: str, end_marker: str, new_inner: str
) -> str:
    begin_idx = content.find(begin_marker)
    if begin_idx == -1:
        raise MarkerNotFoundError(f"marker not found: {begin_marker!r}")
    end_idx = content.find(end_marker, begin_idx)
    if end_idx == -1:
        raise MarkerNotFoundError(f"marker not found: {end_marker!r}")
    end_idx += len(end_marker)
    return content[:begin_idx] + new_inner + content[end_idx:]


def _extract_between_markers(content: str, begin_marker: str, end_marker: str) -> str:
    begin_idx = content.find(begin_marker)
    if begin_idx == -1:
        raise MarkerNotFoundError(f"marker not found: {begin_marker!r}")
    end_idx = content.find(end_marker, begin_idx)
    if end_idx == -1:
        raise MarkerNotFoundError(f"marker not found: {end_marker!r}")
    end_idx += len(end_marker)
    return content[begin_idx:end_idx]


def _render_target(stats: PaceStats, target_path: pathlib.Path) -> tuple[str, str]:
    """Return (current_content, rendered_content) for one target doc.

    Raises:
        MarkerNotFoundError: the marker pair is absent from target_path.
    """
    current = target_path.read_text(encoding="utf-8")
    block = render_pace_block(stats)
    try:
        rendered = _replace_between_markers(current, _BEGIN, _END, block)
    except MarkerNotFoundError:
        raise MarkerNotFoundError(
            f"{target_path} is missing the measured-pace marker pair "
            f"({_BEGIN!r} / {_END!r}); add the marker pair where the block "
            "should render (see README.md or HOW_TO.md for the shape once "
            "either carries one) and re-run."
        ) from None
    return current, rendered


# ---------------------------------------------------------------------------
# Rollup loading
# ---------------------------------------------------------------------------


def resolve_rc_dir(
    project: str, kanban_root_override: Optional[str]
) -> pathlib.Path:
    """Resolve `<kanban_root>/projects/<project>/metrics/rc/` via the same
    resolution chain metrics_aggregator.py itself uses -- this script never
    re-derives kanban-root or project-directory resolution a second way.
    """
    root = _agg.resolve_kanban_root(kanban_root_override)
    project_dir = _agg.resolve_project_dir(root, project)
    return project_dir / "metrics" / "rc"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="generate_measured_pace_docs.py",
        description=(
            "Render (or, with --check, verify) the measured-pace block in "
            "README.md and HOW_TO.md from the project's own per-RC metric "
            "rollups."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify only -- report drift and exit non-zero, write nothing.",
    )
    parser.add_argument("--project", metavar="NAME", help="Project name.")
    parser.add_argument(
        "--kanban-root", metavar="PATH", help="Override the kanban root."
    )
    parser.add_argument(
        "--rc-dir",
        metavar="PATH",
        help=(
            "Read rollups directly from this directory, bypassing "
            "--project/--kanban-root resolution."
        ),
    )
    parser.add_argument(
        "--window",
        type=int,
        metavar="N",
        help="Number of newest rollups to read. Default: every rollup present.",
    )
    parser.add_argument("--readme", metavar="PATH", help="README.md path.")
    parser.add_argument("--how-to", metavar="PATH", help="HOW_TO.md path.")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    """Run the measured-pace docs generator: render, or with ``--check`` compare.

    Exit codes: 0 rendered, or ``--check`` found no drift; 1 ``--check`` found
    one or more disagreements (printed; nothing written); 2 usage error, an
    unreadable ``--rc-dir``, or a target file missing its markers.
    """
    args = _parse_args(argv)

    readme_path = pathlib.Path(args.readme) if args.readme else _DEFAULT_README
    how_to_path = pathlib.Path(args.how_to) if args.how_to else _DEFAULT_HOW_TO

    if args.rc_dir:
        rc_dir = pathlib.Path(args.rc_dir)
    else:
        project = args.project or _DEFAULT_PROJECT
        try:
            rc_dir = resolve_rc_dir(project, args.kanban_root)
        except SystemExit:
            # resolve_kanban_root()/resolve_project_dir() already printed an
            # ERROR line and exited 1; normalize to this script's usage-error
            # exit code (2) so callers can distinguish it from --check drift.
            return 2

    window_requested = args.window if args.window is not None else full_window_size(rc_dir)
    rollups = load_rc_window(rc_dir, window=max(window_requested, 0))
    stats = compute_pace_stats(rollups, window_requested=window_requested)

    try:
        readme_current, readme_rendered = _render_target(stats, readme_path)
        how_to_current, how_to_rendered = _render_target(stats, how_to_path)
    except MarkerNotFoundError as exc:
        print(f"generate_measured_pace_docs: {exc}", file=sys.stderr)
        return 2

    if not args.check:
        if readme_rendered != readme_current:
            readme_path.write_text(readme_rendered, encoding="utf-8")
            print(f"generate_measured_pace_docs: wrote {readme_path}")
        else:
            print(f"generate_measured_pace_docs: {readme_path} already up to date")
        if how_to_rendered != how_to_current:
            how_to_path.write_text(how_to_rendered, encoding="utf-8")
            print(f"generate_measured_pace_docs: wrote {how_to_path}")
        else:
            print(f"generate_measured_pace_docs: {how_to_path} already up to date")
        if stats.has_data:
            print(
                f"generate_measured_pace_docs: per-task {stats.per_task_low:.1f}"
                f"-{stats.per_task_high:.1f} min (median {stats.per_task_median:.1f}, "
                f"mean {stats.per_task_mean:.1f}), composed RC ~"
                f"{stats.composed_total:.0f} min, n={stats.rc_count} RC(s)/"
                f"{stats.task_count} task(s), window {stats.window_oldest}.."
                f"{stats.window_newest}"
            )
        else:
            print("generate_measured_pace_docs: no usable rollups in window")
        return 0

    # --check mode: report every disagreement, write nothing.
    disagreements: list[str] = []
    if readme_rendered != readme_current:
        disagreements.append(f"{readme_path}: measured-pace block disagrees with the rollups")
    if how_to_rendered != how_to_current:
        disagreements.append(f"{how_to_path}: measured-pace block disagrees with the rollups")

    if not disagreements:
        print("generate_measured_pace_docs --check: OK -- both docs match the measured rollups.")
        return 0

    for d in disagreements:
        print(f"generate_measured_pace_docs --check: DRIFT -- {d}", file=sys.stderr)

    # CONVERT: a stale rendered pace figure (a hand-edited number, or a doc
    # that was not re-rendered after new RCs shipped) carries zero runtime-
    # failure risk -- nothing in the pipeline reads README.md/HOW_TO.md's
    # prose. This refusal only ever stops a quality-class pre-flight lint,
    # never the runner itself; see team/SOP-core.md "Gates and Refusals".
    emit_error_contract(
        "docs.measured-pace-drift",
        (
            f"{len(disagreements)} measured-pace doc block(s) disagree with "
            f"the project's per-RC rollups: " + "; ".join(disagreements)
        ),
        "run 'python3 team/scripts/generate_measured_pace_docs.py' from the "
        "repo root and commit the result.",
    )
    # CENSUS-ANCHOR: generate-measured-pace-docs-check-drift-01
    sys.exit(1)


if __name__ == "__main__":
    sys.exit(main())
