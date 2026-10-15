"""
rc_rollup_history.py
====================
Test-fidelity helper: seed a ``metrics/rc/`` directory with per-RC rollup
files of a chosen count, outcome mix, and per-agent load.

WHY THIS EXISTS
---------------
The per-agent averaging reader
(``pgai_agent_kanban.lib.agent_metrics``) and both surfaces built on it --
the ``agent-metrics.sh`` CLI and ``GET /agent-metrics`` -- read rollups
shaped like ``team/scripts/lib/metrics_aggregator.py``'s ``aggregate_rc()``
output. Unit, REST, and integration tests all need histories of those
files: some shipped, some cancelled, with known per-agent tasks, tokens,
and wall time. This module writes them one way, so every test seeds the
same kind of rollup and the expected values can be hand-summed from the
``RcSeed`` records the builder returns rather than recomputed through the
library under test.

HOW TO USE
----------
    from tests.fixtures.rc_rollup_history import (
        AgentLoad, RcSeed, build_rc_history, write_rollups,
    )

    # Explicit seeds: every number chosen by the test.
    seeds = [
        RcSeed("v0.1.0", "shipped", {"cm": AgentLoad(tasks=2, input=100)}),
        RcSeed("v0.2.0", "cancelled", {"cm": AgentLoad(tasks=7)}),
    ]
    write_rollups(rc_dir, seeds)

    # A generated history: 40 rollups over a semver series that crosses
    # both a single-digit and a double-digit minor, two of them cancelled.
    seeds = build_rc_history(rc_dir, 40, cancelled={3, 17})
    shipped = [s for s in seeds if s.outcome == "shipped"]
    hand_total = sum(s.agents["coder"].tasks for s in shipped if "coder" in s.agents)

An ``RcSeed`` whose ``outcome`` is ``None`` writes a rollup with no
``outcome`` field at all.

WHAT THIS HELPER DOES NOT DO
-----------------------------
- It does not run ``metrics_aggregator.py``; it writes the rollup shape
  directly, which is what the reader consumes.
- It writes nothing outside the ``rc_dir`` the caller supplies (pass a
  path under pytest's ``tmp_path``).
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field

SHIPPED = "shipped"
CANCELLED = "cancelled"


@dataclass(frozen=True)
class AgentLoad:
    """One agent's work in one RC.

    tasks:            written to ``tasks.by_agent[<agent>]``.
    input/output/cache_read/cache_write:
                      written to ``tokens.by_agent[<agent>]``.
    wall_seconds / wall_task_count:
                      written to ``wall.by_agent[<agent>]`` when both are
                      set; leave either as None to model an RC with no
                      per-agent wall data for this agent.
    """

    tasks: int
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    wall_seconds: float | None = None
    wall_task_count: int | None = None

    @property
    def token_total(self) -> int:
        """Sum of the four token counters -- the hand-summed figure a
        reader's per-RC token total must equal."""
        return self.input + self.output + self.cache_read + self.cache_write


@dataclass(frozen=True)
class RcSeed:
    """One rollup to write: its version, its ``outcome`` field (None to
    omit the field), and each agent's load in that RC."""

    version: str
    outcome: str | None = SHIPPED
    agents: Mapping[str, AgentLoad] = field(default_factory=dict)


def rollup_payload(seed: RcSeed) -> dict:
    """Build the rollup dict for `seed`, shaped like aggregate_rc()'s
    output (only the fields the averaging reader consumes, plus ``rc``)."""
    wall_by_agent = {
        agent: {"seconds": load.wall_seconds, "task_count": load.wall_task_count}
        for agent, load in seed.agents.items()
        if load.wall_seconds is not None and load.wall_task_count is not None
    }
    payload: dict = {
        "rc": seed.version,
        "tasks": {
            "total": sum(load.tasks for load in seed.agents.values()),
            "by_agent": {agent: load.tasks for agent, load in seed.agents.items()},
        },
        "tokens": {
            "by_agent": {
                agent: {
                    "input": load.input,
                    "output": load.output,
                    "cache_read": load.cache_read,
                    "cache_write": load.cache_write,
                }
                for agent, load in seed.agents.items()
            }
        },
        "wall": {"by_agent": wall_by_agent},
    }
    if seed.outcome is not None:
        payload["outcome"] = seed.outcome
    return payload


def write_rollups(rc_dir: pathlib.Path, seeds: Iterable[RcSeed]) -> list[pathlib.Path]:
    """Write one ``<version>.json`` per seed under `rc_dir` (created if
    absent). Returns the written paths in seed order."""
    rc_dir.mkdir(parents=True, exist_ok=True)
    paths: list[pathlib.Path] = []
    for seed in seeds:
        path = rc_dir / f"{seed.version}.json"
        path.write_text(json.dumps(rollup_payload(seed)), encoding="utf-8")
        paths.append(path)
    return paths


def version_series(count: int, *, major: int = 0, first_minor: int = 9, per_minor: int = 2) -> list[str]:
    """`count` ascending versions, `per_minor` patches per minor starting
    at ``v<major>.<first_minor>.0``. The defaults start at v0.9.0 so a
    series of 40 crosses v0.20.0 -- lexicographic and semantic order
    disagree across that boundary, which is what a version-bound test
    needs."""
    return [
        f"v{major}.{first_minor + i // per_minor}.{i % per_minor}"
        for i in range(count)
    ]


def default_agents(index: int) -> dict[str, AgentLoad]:
    """Deterministic, varied per-agent load for the `index`-th RC.

    cm works every RC (2 tasks, like an open-rc + release pair); coder
    works every RC with a load that varies by index; writer works every
    other RC. Each agent has a different per-task wall time so an
    average that collapses agents together is visible.
    """
    coder_tasks = 1 + index % 3
    agents = {
        "cm": AgentLoad(
            tasks=2, input=1000 + index, output=200, cache_read=10 * index, cache_write=5,
            wall_seconds=2 * 300.0, wall_task_count=2,
        ),
        "coder": AgentLoad(
            tasks=coder_tasks, input=5000 + 7 * index, output=1500, cache_read=100 * index,
            cache_write=50, wall_seconds=coder_tasks * 1200.0, wall_task_count=coder_tasks,
        ),
    }
    if index % 2 == 0:
        agents["writer"] = AgentLoad(
            tasks=1, input=3000 + index, output=900, cache_read=0, cache_write=0,
            wall_seconds=2700.0, wall_task_count=1,
        )
    return agents


def build_rc_history(
    rc_dir: pathlib.Path,
    count: int,
    *,
    cancelled: Collection[int] = (),
    outcomes: Mapping[int, str | None] | None = None,
    versions: list[str] | None = None,
    agents_for: Callable[[int], Mapping[str, AgentLoad]] = default_agents,
) -> list[RcSeed]:
    """Write `count` rollups under `rc_dir` and return their seeds, oldest
    version first.

    versions:   the versions to use (default ``version_series(count)``);
                must have exactly `count` entries.
    cancelled:  indices (into the oldest-first series) written with
                outcome ``cancelled``; every other index is ``shipped``.
    outcomes:   per-index overrides applied after `cancelled` -- any
                string, or None to omit the field.
    agents_for: index -> per-agent load (default ``default_agents``).
    """
    series = versions if versions is not None else version_series(count)
    if len(series) != count:
        raise ValueError(f"versions has {len(series)} entries; expected {count}")
    overrides = dict(outcomes or {})
    seeds: list[RcSeed] = []
    for index, version in enumerate(series):
        outcome: str | None = CANCELLED if index in cancelled else SHIPPED
        if index in overrides:
            outcome = overrides[index]
        seeds.append(RcSeed(version=version, outcome=outcome, agents=dict(agents_for(index))))
    write_rollups(rc_dir, seeds)
    return seeds
