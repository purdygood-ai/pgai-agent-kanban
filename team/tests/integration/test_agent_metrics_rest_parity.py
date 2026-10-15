"""
test_agent_metrics_rest_parity.py — golden-pair proof that agent-metrics
resolves to the SAME data through the CLI (``agent-metrics.sh``) and its
REST twin (``GET /agent-metrics``) for every span the two surfaces offer:
the default newest-10 window, ``--all``, ``--since vX.Y.Z`` and ``--last N``.

Neither surface re-derives the averaging arithmetic: both select the span
with ``select_rc_span()``, compute with ``compute_agent_averages()`` and
round with ``round_one_decimal()``, defined exactly once in
``team/pgai_agent_kanban/lib/agent_metrics.py``. The CLI's own
``team/scripts/lib/agent_metrics.py`` imports those functions rather than
redefining them, and ``GET /agent-metrics``
(``team/pgai_agent_kanban/api/reads.py``) imports the same package module.

Two kinds of fixture feed the pairs:

- A two-rollup board whose agents have different per-task wall averages,
  both different from either RC's own RC-level ``wall_time_minutes``, so a
  regression back to the RC-level join (every agent in an RC would then
  read that RC's shared figure) is caught rather than passing by
  coincidence.
- A 40-RC history seeded through the shared rollup-history builder
  (``tests/fixtures/rc_rollup_history.py``) onto the golden-pair synthetic
  board, with three cancelled rollups interleaved among the shipped ones --
  one of them inside the default newest-10 window. Cancelled rollups carry
  a deliberately heavier load than any shipped one, plus an agent that
  works ONLY cancelled RCs, so a cancelled rollup leaking into any figure
  changes a value or adds a row.

Over the 40-RC history every span is held to three things: the CLI rows
equal the REST ``agents`` array on every field the CLI renders (commas
stripped on the CLI side only), the CLI footer's shipped and excluded
counts equal the JSON's ``shipped_count`` / ``excluded_count``, and both
equal a HAND computation -- sums, counts and one-decimal means taken in
this module from the builder's own literal per-RC numbers, never by
importing the library under test. The ``--all`` pair additionally holds
the REST-only ``rc_count`` field to the hand count.

The CLI is read only through the row and footer shapes its renderer
documents (``team/scripts/lib/agent_metrics.py``, "RENDERED BLOCK").

Integration test contract (team/tests/integration/conftest.py): no
integration point is mocked. The REST server is a real uvicorn subprocess;
the CLI call is a real ``bash agent-metrics.sh`` subprocess; only the RC
rollup JSON files' DATA is fake, and every seed is written under pytest's
``tmp_path``.

Test-name policy (roles/TESTER.md Anti-pattern 6): names describe behavior,
never bug IDs, task IDs, or scaffolding labels.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from decimal import ROUND_HALF_UP, Decimal

from tests.fixtures.api_server import api_server_fixture
from tests.fixtures.golden_board import PROJECT_NAME, build_synthetic_board
from tests.fixtures.golden_comparator import assert_json_parity
from tests.fixtures.rc_rollup_history import (
    SHIPPED,
    AgentLoad,
    RcSeed,
    build_rc_history,
    default_agents,
)

# A rendered agent row: "<agent>  <wall>  <tokens>  <tasks>  <total tokens>
# <total tasks>", whitespace padded, the three averages with one decimal
# ('--' when absent) and the two totals as thousands-separated integers.
_ROW_RE = re.compile(
    r"^\s*(\S+)\s+(--|[\d,]+\.\d)\s+(--|[\d,]+\.\d)\s+(--|[\d,]+\.\d)\s+([\d,]+)\s+([\d,]+)\s*$"
)

# The footer after the rows: "<label>: <N> shipped RCs (<M> cancelled
# excluded)", the label "window", "all" or "since vX.Y.Z".
_FOOTER_RE = re.compile(
    r"^\s*(window|all|since\s+\S+)\s*:\s*([\d,]+)\s+shipped\s+RCs\s+\(\s*([\d,]+)\s+cancelled\s+excluded\s*\)\s*$"
)

# The fields a CLI row renders, in column order -- the REST agents array
# is compared on exactly these (its extra ``rc_count`` has no CLI column).
_ROW_FIELDS = ("agent", "avg_wall_minutes", "avg_tokens", "avg_tasks", "total_tokens", "total_tasks")

_WINDOW = 10

# ---------------------------------------------------------------------------
# The 40-RC history
# ---------------------------------------------------------------------------

_HISTORY_SIZE = 40

# Oldest-first indices written as cancelled. 35 falls inside the default
# newest-10 window (the newest ten shipped are 39..36 and 34..29); 20 and 3
# sit further back, so --last and --since each cross at least one.
_CANCELLED_INDICES = frozenset({3, 20, 35})
_CANCELLED_IN_DEFAULT_WINDOW = 35

# The --since bound: index 22 of the builder's default version series is
# v0.20.0, and every older version (v0.9.x .. v0.19.x) sorts ABOVE it
# lexicographically -- a string comparison would widen the span.
_SINCE_INDEX = 22
_SINCE_VERSION = "v0.20.0"

# The --last bound: wide enough to cross the cancelled rollups at 35 and 20.
_LAST_COUNT = 25

# An agent that works ONLY cancelled RCs: a row for it on either surface
# means a cancelled rollup entered the span's figures.
_CANCELLED_ONLY_AGENT = "reviewer"


def _history_agents(index: int) -> dict[str, AgentLoad]:
    """Per-agent load for the `index`-th RC of the 40-RC history.

    Shipped RCs get the builder's default load: cm 2 tasks at 5 min/task,
    coder 1-3 tasks at 20 min/task, writer 1 task at 45 min/task on every
    other RC -- three agents with differing per-task wall averages.
    Cancelled RCs get a load no shipped RC carries (and the cancelled-only
    agent), so counting one would move every average it touched.
    """
    if index not in _CANCELLED_INDICES:
        return default_agents(index)
    return {
        "cm": AgentLoad(tasks=9, input=900_000, output=90_000, wall_seconds=9 * 6000.0, wall_task_count=9),
        "coder": AgentLoad(tasks=12, input=1_200_000, output=120_000, wall_seconds=12 * 9000.0, wall_task_count=12),
        "writer": AgentLoad(tasks=7, input=700_000, output=70_000, wall_seconds=7 * 7200.0, wall_task_count=7),
        _CANCELLED_ONLY_AGENT: AgentLoad(tasks=5, input=50_000, wall_seconds=5 * 600.0, wall_task_count=5),
    }


def _seed_history(board_root: pathlib.Path) -> tuple[pathlib.Path, list[RcSeed]]:
    """Build the golden-pair board and seed the 40-RC history onto it.

    Returns the rollup directory and the seeds, oldest version first (the
    builder's contract) -- the literal numbers the hand computation reads.
    """
    project_dir = build_synthetic_board(board_root)
    rc_dir = project_dir / "metrics" / "rc"
    seeds = build_rc_history(
        rc_dir, _HISTORY_SIZE, cancelled=_CANCELLED_INDICES, agents_for=_history_agents
    )
    assert len(seeds) == _HISTORY_SIZE
    assert seeds[_SINCE_INDEX].version == _SINCE_VERSION, (
        f"the builder's version series moved: index {_SINCE_INDEX} is "
        f"{seeds[_SINCE_INDEX].version}, not {_SINCE_VERSION}"
    )
    return rc_dir, seeds


# ---------------------------------------------------------------------------
# Hand computation over the fixture's literal numbers
# ---------------------------------------------------------------------------


def _one_decimal(numerator: int | float, denominator: int | float) -> float:
    """numerator / denominator to one decimal, halves rounded away from zero
    -- the display rule both surfaces document, done here in exact decimal
    arithmetic rather than by calling the library's rounding function."""
    exact = Decimal(str(numerator)) / Decimal(str(denominator))
    return float(exact.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _hand_rows(selected: Iterable[RcSeed]) -> list[dict]:
    """Per-agent rows over `selected` (the shipped RCs a span holds), summed
    and counted here from each seed's literal AgentLoad fields.

    An agent's invocation RCs are those where it has at least one task;
    avg_tokens / avg_tasks divide by their count (rc_count), avg_wall_minutes
    pools seconds and task counts across them before dividing (None when no
    invocation RC carries wall data). Sorted by agent name, as both
    surfaces sort.
    """
    sums: dict[str, dict] = {}
    for seed in selected:
        for agent, load in seed.agents.items():
            if load.tasks <= 0:
                continue
            acc = sums.setdefault(
                agent, {"rc_count": 0, "tasks": 0, "tokens": 0, "wall_seconds": 0.0, "wall_tasks": 0}
            )
            acc["rc_count"] += 1
            acc["tasks"] += load.tasks
            acc["tokens"] += load.input + load.output + load.cache_read + load.cache_write
            if load.wall_seconds is not None and load.wall_task_count:
                acc["wall_seconds"] += load.wall_seconds
                acc["wall_tasks"] += load.wall_task_count

    rows = []
    for agent in sorted(sums):
        acc = sums[agent]
        rows.append(
            {
                "agent": agent,
                "avg_wall_minutes": (
                    _one_decimal(acc["wall_seconds"], acc["wall_tasks"] * 60) if acc["wall_tasks"] else None
                ),
                "avg_tokens": _one_decimal(acc["tokens"], acc["rc_count"]),
                "avg_tasks": _one_decimal(acc["tasks"], acc["rc_count"]),
                "rc_count": acc["rc_count"],
                "total_tokens": acc["tokens"],
                "total_tasks": acc["tasks"],
            }
        )
    return rows


def _hand_newest_shipped(seeds: Sequence[RcSeed], count: int) -> tuple[list[RcSeed], int]:
    """The newest `count` shipped seeds, and how many non-shipped seeds lie
    newer than the oldest one taken (every non-shipped seed when fewer than
    `count` shipped) -- walked by hand over the oldest-first seed list."""
    taken: list[RcSeed] = []
    skipped = 0
    for seed in reversed(seeds):
        if len(taken) >= count:
            break
        if seed.outcome == SHIPPED:
            taken.append(seed)
        else:
            skipped += 1
    return taken, skipped


def _hand_expected(selected: Sequence[RcSeed], excluded: int) -> dict:
    """The pair shape (see ``_cli_pair``) the hand computation predicts."""
    return {
        "agents": [{k: row[k] for k in _ROW_FIELDS} for row in _hand_rows(selected)],
        "shipped_count": len(selected),
        "excluded_count": excluded,
    }


# ---------------------------------------------------------------------------
# The two surfaces
# ---------------------------------------------------------------------------


def _cli_env(kanban_root: pathlib.Path) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PGAI_PROJECT_NAME", None)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    return env


def _parse_average(raw: str) -> float | None:
    """A CLI one-decimal average cell with its commas stripped; None for the
    '--' placeholder. Both surfaces round with the same one-decimal rule,
    so the value compares directly against the REST JSON number."""
    if raw == "--":
        return None
    return float(raw.replace(",", ""))


def _parse_total(raw: str) -> int:
    """A CLI thousands-separated total cell with its commas stripped."""
    return int(raw.replace(",", ""))


def _run_cli(board_root: pathlib.Path, span_args: Sequence[str]) -> str:
    """Run the real agent-metrics.sh for the fixture project with
    `span_args` (empty for the default span) and return its stdout."""
    agent_metrics_sh = board_root / "scripts" / "dashboard" / "agent-metrics.sh"
    result = subprocess.run(
        [
            "bash", str(agent_metrics_sh),
            "--project", PROJECT_NAME,
            *span_args,
            "--kanban-root", str(board_root),
            "--no-color",
        ],
        capture_output=True,
        text=True,
        env=_cli_env(board_root),
        timeout=15,
    )
    assert result.returncode == 0, (
        f"agent-metrics.sh {' '.join(span_args)} exited {result.returncode}: {result.stderr!r}"
    )
    return result.stdout


def _cli_rows(stdout: str) -> list[dict]:
    """Parse the rendered agent rows into dicts keyed by ``_ROW_FIELDS``,
    sorted by agent name (the renderer's own sort), header row excluded."""
    agents: list[dict] = []
    for line in stdout.splitlines():
        m = _ROW_RE.match(line)
        if not m:
            continue
        agent, wall_raw, tokens_raw, tasks_raw, total_tokens_raw, total_tasks_raw = m.groups()
        if agent == "Agent":  # header row shares the column shape
            continue
        agents.append(
            {
                "agent": agent,
                "avg_wall_minutes": _parse_average(wall_raw),
                "avg_tokens": _parse_average(tokens_raw),
                "avg_tasks": _parse_average(tasks_raw),
                "total_tokens": _parse_total(total_tokens_raw),
                "total_tasks": _parse_total(total_tasks_raw),
            }
        )
    return sorted(agents, key=lambda a: a["agent"])


def _cli_footer(stdout: str) -> tuple[str, int, int]:
    """The single footer line's (label, shipped, excluded), commas stripped."""
    footers = [m for m in (_FOOTER_RE.match(line) for line in stdout.splitlines()) if m]
    assert len(footers) == 1, f"expected exactly one footer line in agent-metrics.sh output:\n{stdout}"
    label, shipped, excluded = footers[0].groups()
    return re.sub(r"\s+", " ", label), _parse_total(shipped), _parse_total(excluded)


def _cli_pair(board_root: pathlib.Path, span_args: Sequence[str]) -> tuple[dict, str]:
    """The CLI side of a pair: {"agents", "shipped_count", "excluded_count"}
    read from the rows and footer, plus the footer label."""
    stdout = _run_cli(board_root, span_args)
    label, shipped, excluded = _cli_footer(stdout)
    return {"agents": _cli_rows(stdout), "shipped_count": shipped, "excluded_count": excluded}, label


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as resp:
        assert resp.status == 200, f"GET {url} returned HTTP {resp.status}"
        return json.loads(resp.read().decode("utf-8"))


def _rest_body(port: int, span_params: dict[str, str]) -> dict:
    query = urllib.parse.urlencode({"project": PROJECT_NAME, **span_params})
    return _get_json(f"http://127.0.0.1:{port}/agent-metrics?{query}")


def _rest_pair(body: dict) -> dict:
    """The REST side of a pair, in the CLI side's shape: every agent entry
    narrowed to the fields a CLI row renders."""
    return {
        "agents": [{k: entry[k] for k in _ROW_FIELDS} for entry in body["agents"]],
        "shipped_count": body["shipped_count"],
        "excluded_count": body["excluded_count"],
    }


def _server_logs(tmp_path: pathlib.Path) -> pathlib.Path:
    logs = tmp_path / "server_logs"
    logs.mkdir(parents=True, exist_ok=True)
    return logs


def _assert_span_pair(
    tmp_path: pathlib.Path,
    span_args: Sequence[str],
    span_params: dict[str, str],
    expected_label: str,
    expect: Callable[[list[RcSeed]], dict],
) -> tuple[dict, list[RcSeed]]:
    """Seed the 40-RC history, run one span on both surfaces, and hold the
    CLI to REST and both to ``expect(seeds)`` -- the hand computation over
    the seeds just written. Returns the REST body and the seeds."""
    board_root = tmp_path / "board"
    _, seeds = _seed_history(board_root)
    expected = expect(seeds)

    cli, label = _cli_pair(board_root, span_args)
    with api_server_fixture(_server_logs(tmp_path), kanban_root=board_root) as handle:
        body = _rest_body(handle.port, span_params)

    assert label == expected_label, f"CLI footer label {label!r}; expected {expected_label!r}"
    assert_json_parity(cli, _rest_pair(body))
    assert_json_parity(cli, expected)
    agents = {row["agent"] for row in cli["agents"]}
    assert _CANCELLED_ONLY_AGENT not in agents, "a cancelled rollup's agent reached the rows"
    return body, seeds


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _seed_rc_rollups(project_dir: pathlib.Path) -> None:
    """Two shipped RC rollups: coder averages 30 min/task, writer 45
    min/task -- deliberately different, and both deliberately different
    from either RC's own RC-level wall_time_minutes (20 and 40). The second
    rollup has no "wall" key, like a rollup written before per-task wall
    capture, so coder's wall average comes from the first alone."""
    rc_dir = project_dir / "metrics" / "rc"
    rc_dir.mkdir(parents=True, exist_ok=True)
    rollups = {
        "v0.1.0": {
            "wall_time_minutes": 20,
            "tasks": {"by_agent": {"coder": 2, "writer": 1}},
            "tokens": {
                "by_agent": {
                    "coder": {"input": 1000, "output": 500, "cache_read": 0, "cache_write": 0},
                    "writer": {"input": 2000, "output": 1000, "cache_read": 0, "cache_write": 0},
                }
            },
            "wall": {
                "by_agent": {
                    "coder": {"seconds": 3600, "task_count": 2},  # 30 min/task
                    "writer": {"seconds": 2700, "task_count": 1},  # 45 min/task
                }
            },
        },
        "v0.2.0": {
            "wall_time_minutes": 40,
            "tasks": {"by_agent": {"coder": 3}},
            "tokens": {
                "by_agent": {"coder": {"input": 500, "output": 500, "cache_read": 0, "cache_write": 0}},
            },
        },
    }
    for version, payload in rollups.items():
        (rc_dir / f"{version}.json").write_text(
            json.dumps({"outcome": "shipped", **payload}), encoding="utf-8"
        )


def test_agent_metrics_per_task_wall_matches_between_cli_and_rest(
    tmp_path: pathlib.Path,
) -> None:
    """``agent-metrics.sh --project NAME --window 10`` and
    ``GET /agent-metrics?project=NAME&window=10`` resolve to the identical
    per-agent averages and totals over the SAME two seeded rollups, and the
    per-task wall average is each agent's own, not the RC-level figure."""
    board_root = tmp_path / "board"
    project_dir = build_synthetic_board(board_root)
    _seed_rc_rollups(project_dir)

    cli, label = _cli_pair(board_root, ["--window", str(_WINDOW)])
    with api_server_fixture(_server_logs(tmp_path), kanban_root=board_root) as handle:
        body = _rest_body(handle.port, {"window": str(_WINDOW)})

    assert label == "window"
    assert_json_parity(cli, _rest_pair(body))

    by_agent = {a["agent"]: a for a in cli["agents"]}
    assert by_agent.keys() == {"coder", "writer"}
    assert by_agent["coder"]["avg_wall_minutes"] == 30.0
    assert by_agent["writer"]["avg_wall_minutes"] == 45.0
    assert by_agent["coder"]["total_tasks"] == 5
    assert by_agent["coder"]["total_tokens"] == 2500


def test_all_shipped_span_matches_between_cli_and_rest_and_equals_the_hand_computation(
    tmp_path: pathlib.Path,
) -> None:
    """Over the 40-RC history, ``--all`` and ``all=true`` render the same
    rows and footer counts, and every agent's totals and one-decimal
    averages equal sums and counts taken by hand over the shipped seeds --
    the three cancelled rollups entering no figure and counted excluded."""

    def expect(seeds: list[RcSeed]) -> dict:
        shipped = [s for s in seeds if s.outcome == SHIPPED]
        return _hand_expected(shipped, excluded=len(seeds) - len(shipped))

    body, seeds = _assert_span_pair(tmp_path, ["--all"], {"all": "true"}, "all", expect)
    shipped = [s for s in seeds if s.outcome == SHIPPED]

    # The REST-only rc_count is held to the hand count as well.
    hand = _hand_rows(shipped)
    assert {e["agent"]: e["rc_count"] for e in body["agents"]} == {r["agent"]: r["rc_count"] for r in hand}

    # Literal anchors for the hand computation itself: 37 of 40 shipped;
    # cm works every shipped RC with 2 tasks, so it reads 2.0 tasks/RC and
    # 2 x 37 tasks; the three agents' per-task walls differ (5, 20 and 45
    # minutes).
    by_agent = {row["agent"]: row for row in hand}
    assert len(shipped) == 37
    assert body["excluded_count"] == 3
    assert by_agent["cm"]["avg_tasks"] == 2.0
    assert by_agent["cm"]["total_tasks"] == 74
    assert [by_agent[a]["avg_wall_minutes"] for a in ("cm", "coder", "writer")] == [5.0, 20.0, 45.0]


def test_cancelled_rollup_in_default_window_is_excluded_alike_and_removing_it_changes_no_agent_value(
    tmp_path: pathlib.Path,
) -> None:
    """With no span flag or parameter, both surfaces take the newest 10
    SHIPPED RCs, passing over the cancelled rollup that sits among them:
    both report it in the excluded count, and deleting that rollup and
    re-running the pair leaves every agent value on both sides unchanged."""
    board_root = tmp_path / "board"
    rc_dir, seeds = _seed_history(board_root)
    selected, skipped = _hand_newest_shipped(seeds, _WINDOW)
    assert skipped >= 1
    expected = _hand_expected(selected, excluded=skipped)

    cancelled_path = rc_dir / f"{seeds[_CANCELLED_IN_DEFAULT_WINDOW].version}.json"
    assert cancelled_path.is_file()

    with api_server_fixture(_server_logs(tmp_path), kanban_root=board_root) as handle:
        cli_before, label = _cli_pair(board_root, [])
        rest_before = _rest_body(handle.port, {})

        cancelled_path.unlink()

        cli_after, _ = _cli_pair(board_root, [])
        rest_after = _rest_body(handle.port, {})

    assert label == "window"
    assert_json_parity(cli_before, _rest_pair(rest_before))
    assert_json_parity(cli_before, expected)
    assert cli_before["excluded_count"] >= 1
    assert rest_before["excluded_count"] >= 1
    assert _CANCELLED_ONLY_AGENT not in {row["agent"] for row in cli_before["agents"]}

    assert_json_parity(cli_after, _rest_pair(rest_after))
    assert cli_after["agents"] == cli_before["agents"], "removing a cancelled rollup moved a CLI value"
    assert rest_after["agents"] == rest_before["agents"], "removing a cancelled rollup moved a REST value"
    assert cli_after["shipped_count"] == cli_before["shipped_count"] == _WINDOW
    assert cli_after["excluded_count"] == cli_before["excluded_count"] - 1
    assert rest_after["excluded_count"] == rest_before["excluded_count"] - 1


def test_since_span_selects_the_same_rows_and_values_on_cli_and_rest(
    tmp_path: pathlib.Path,
) -> None:
    """``--since v0.20.0`` and ``since=v0.20.0`` both take every shipped RC
    at or above the bound in semantic-version order -- the same rows, the
    same values and the same footer counts, equal to the hand computation
    over the seeds from that version on (the older v0.9.x .. v0.19.x,
    which sort above the bound as strings, stay out)."""

    def expect(seeds: list[RcSeed]) -> dict:
        in_range = seeds[_SINCE_INDEX:]
        shipped = [s for s in in_range if s.outcome == SHIPPED]
        assert 0 < len(shipped) < len(in_range), "the --since range must hold a cancelled rollup"
        return _hand_expected(shipped, excluded=len(in_range) - len(shipped))

    _assert_span_pair(
        tmp_path,
        ["--since", _SINCE_VERSION],
        {"since": _SINCE_VERSION},
        f"since {_SINCE_VERSION}",
        expect,
    )


def test_last_span_selects_the_same_rows_and_values_on_cli_and_rest(
    tmp_path: pathlib.Path,
) -> None:
    """``--last 25`` and ``last=25`` both take the newest 25 SHIPPED RCs,
    the cancelled rollups inside that range taking no slot -- the same
    rows, the same values and the same footer counts, equal to the hand
    computation over those seeds."""

    def expect(seeds: list[RcSeed]) -> dict:
        selected, skipped = _hand_newest_shipped(seeds, _LAST_COUNT)
        assert len(selected) == _LAST_COUNT
        assert skipped >= 2, "the --last range must cross two cancelled rollups"
        return _hand_expected(selected, excluded=skipped)

    _assert_span_pair(
        tmp_path,
        ["--last", str(_LAST_COUNT)],
        {"last": str(_LAST_COUNT)},
        "window",
        expect,
    )
