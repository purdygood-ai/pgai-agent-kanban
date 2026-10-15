"""
metrics_cases.py
================
A fixture usage tree and a case runner for the metrics and cost surfaces:
``cost-report.sh``, ``metrics-report.sh``, ``dashboard/show-metrics.sh``,
``dashboard/costs.sh``, ``dashboard/agent-metrics.sh`` and the ``GET
/metrics``, ``GET /costs`` and ``GET /agent-metrics`` routes -- every one of
them answered by ``pgai_agent_kanban.lib.metrics``.

WHAT THE TREE HOLDS
-------------------
:func:`build_usage_tree` lays out a throwaway kanban root:

- ``projects.cfg`` registering ``demo`` (priority 1), ``quiet`` (2) and
  ``fresh`` (3).
- ``demo``: a ``project.cfg`` whose ``dev_tree_path`` points at a fixture
  dev tree carrying its own ``token_pricing.json``; daily roll-ups across
  April and May 2026 (one with per-category costs, one with no activity,
  one unreadable); RC roll-ups for a shipped, a second shipped and an
  active RC; a ``metrics/history.csv`` whose rows cover a tracked, a zero
  and an untracked Blocked value; per-RC metrics roll-ups whose version
  order differs from their name order; a ``release-state.md`` naming the
  active RC; and a roll-up for the day the tree is built (the costs pane
  reads "today").
- ``quiet``: no ``dev_tree_path`` (so no pricing file), one daily roll-up,
  and a header-only ``history.csv``.
- ``fresh``: registered with nothing on disk but its directory.

No case asks for a roll-up that is missing: the CLI builds a missing one by
running the aggregator, which would write into the tree.

WHAT A CASE IS
--------------
A CLI case names a script, its arguments and any extra environment (a run
has no ``TERM`` unless the case sets one, and bash reads an unset ``TERM`` as
``dumb``, so colour cases set ``TERM=xterm``); a REST
case names a path and query parameters. :func:`run_cli_case` and
:func:`run_rest_case` return one normalised outcome each -- exit code,
stdout and stderr for a CLI run; HTTP status and body for a request. The
tree root, the script path, the build day and the costs pane's "updated"
stamp are replaced by placeholders so the outcome is stable.

``tests/fixtures/golden/metrics_costs_cli.json`` holds the outcomes as the
scripts and routes produced them before the metrics and cost logic moved
into ``pgai_agent_kanban.lib.metrics``; ``tests/unit/test_lib_metrics.py``
replays every case and compares.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
from typing import Any, Iterator, Optional

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from tests.fixtures.rc_rollup_history import AgentLoad, RcSeed, rollup_payload  # noqa: E402
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env  # noqa: E402

GOLDEN_FILE = _TEAM_DIR / "tests" / "fixtures" / "golden" / "metrics_costs_cli.json"

SCRIPTS = {
    "cost-report": _TEAM_DIR / "scripts" / "cost-report.sh",
    "metrics-report": _TEAM_DIR / "scripts" / "metrics-report.sh",
    "show-metrics": _TEAM_DIR / "scripts" / "dashboard" / "show-metrics.sh",
    "costs": _TEAM_DIR / "scripts" / "dashboard" / "costs.sh",
    "agent-metrics": _TEAM_DIR / "scripts" / "dashboard" / "agent-metrics.sh",
}

PROJECTS = (("demo", 1), ("quiet", 2), ("fresh", 3))

#: history.csv columns in the order metrics_csv_writer.py writes them.
HISTORY_COLUMNS = (
    "rc", "project", "workflow_type", "opened_at", "closed_at", "wall_time_minutes", "outcome",
    "tasks_total", "tasks_pm", "tasks_coder", "tasks_writer", "tasks_tester", "tasks_cm",
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
    "cache_hit_rate_pct", "bugs_filed_during", "operator_waivers", "blocked_minutes",
)

_PRICING = {
    "providers": {
        "claude": {
            "models": {
                "claude-sonnet-4-6": {
                    "input_per_1m": 3.0, "output_per_1m": 15.0,
                    "cache_creation_per_1m": 3.75, "cache_read_per_1m": 0.3,
                },
                "claude-opus-4-8": {
                    "input_per_1m": 5.0, "output_per_1m": 25.0,
                    "cache_creation_per_1m": 6.25, "cache_read_per_1m": 0.5,
                },
            }
        },
        "codex": {"models": {"gpt-5-codex": {"input_per_1m": 1.25, "output_per_1m": 10.0}}},
    },
    "subscriptions": {
        "claude_pro_programmatic_credit": 20.0,
        "claude_max_programmatic_credit": 200.0,
        "chatgpt_plus": 20.0,
        "chatgpt_pro": 200.0,
    },
}


# ---------------------------------------------------------------------------
# Tree building
# ---------------------------------------------------------------------------


def _write_json(path: pathlib.Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _day(totals: dict, by_agent: dict, by_model: dict, rcs: list | None = None) -> dict:
    return {"totals": totals, "by_agent": by_agent, "by_model": by_model, "rcs_shipped": rcs or []}


def _agent(i: int, o: int, cost: float, inv: int) -> dict:
    return {"input": i, "output": o, "cost_usd": cost, "invocations": inv}


def _model(i: int, o: int, cc: int, cr: int, cost: float, inv: int, **category: float) -> dict:
    entry = {
        "input": i, "output": o, "cache_creation_tokens": cc, "cache_read_tokens": cr,
        "cost_usd": cost, "invocations": inv,
    }
    entry.update(category)
    return entry


def _daily_rollups() -> dict[str, Any]:
    """The demo project's usage/daily files, by file name (a str is written raw)."""
    return {
        "2026-04-20.json": _day(
            {
                "input_tokens": 120_000, "output_tokens": 30_000, "cache_creation_tokens": 8_000,
                "cache_read_tokens": 400_000, "cost_usd": 3.4, "invocations": 6,
                "input_cost_usd": 1.8, "cache_creation_cost_usd": 0.15,
                "cache_read_cost_usd": 0.6, "output_cost_usd": 0.85,
            },
            {"coder": _agent(120_000, 30_000, 3.4, 6)},
            {"claude-opus-4-7": _model(120_000, 30_000, 8_000, 400_000, 3.4, 6)},
        ),
        "2026-05-01.json": _day(
            {
                "input_tokens": 50_000, "output_tokens": 12_000, "cache_creation_tokens": 4_000,
                "cache_read_tokens": 900_000, "cost_usd": 12.75, "invocations": 14,
            },
            {"pm": _agent(10_000, 2_000, 1.25, 2), "Coder": _agent(40_000, 10_000, 11.5, 12)},
            {
                "claude-sonnet-4-6": _model(10_000, 2_000, 1_000, 100_000, 1.25, 2),
                "claude-opus-4-8": _model(40_000, 10_000, 3_000, 800_000, 11.5, 12),
            },
        ),
        "2026-05-03.json": _day(
            {
                "input_tokens": 75_000, "output_tokens": 20_500, "cache_creation_tokens": 6_000,
                "cache_read_tokens": 2_500_000, "cost_usd": 431.204, "invocations": 41,
            },
            {
                "coder": _agent(60_000, 18_000, 402.004, 33),
                "tester": _agent(15_000, 2_500, 29.2, 8),
            },
            {"claude-opus-4-8": _model(75_000, 20_500, 6_000, 2_500_000, 431.204, 41)},
            rcs=["v1.2.0"],
        ),
        "2026-05-10.json": _day(
            {
                "input_tokens": 9_000, "output_tokens": 1_000, "cache_creation_tokens": 0,
                "cache_read_tokens": 0, "cost_usd": 0.9, "invocations": 3,
            },
            {"cm": _agent(9_000, 1_000, 0.9, 3)},
            {"gpt-5-codex": _model(9_000, 1_000, 0, 0, 0.9, 3)},
        ),
        "2026-05-11.json": _day(
            {
                "input_tokens": 0, "output_tokens": 0, "cache_creation_tokens": 0,
                "cache_read_tokens": 0, "cost_usd": 0, "invocations": 0,
            },
            {},
            {},
        ),
        "2026-05-12.json": "{ not json",
    }


def _rc_rollups() -> dict[str, dict]:
    """The demo project's usage/rc files, by version."""

    def _task(agent: str, model: str, i: int, o: int, cc: int, cr: int, cost: float, inv: int, **cat) -> dict:
        entry = {
            "agent": agent, "model": model, "input": i, "output": o,
            "cache_creation_tokens": cc, "cache_read_tokens": cr, "cost_usd": cost, "invocations": inv,
        }
        entry.update(cat)
        return entry

    return {
        "v1.1.0": {
            "version": "v1.1.0", "shipped_at": "2026-04-21T09:30:00Z",
            "totals": {
                "input_tokens": 120_000, "output_tokens": 30_000, "cache_read_tokens": 400_000,
                "cost_usd": 3.4, "invocations": 6, "tasks": 2,
            },
            "tasks": [
                _task("coder", "claude-opus-4-7", 120_000, 30_000, 8_000, 400_000, 3.4, 6,
                      input_cost_usd=1.8, cache_creation_cost_usd=0.15,
                      cache_read_cost_usd=0.6, output_cost_usd=0.85),
            ],
        },
        "v1.2.0": {
            "version": "v1.2.0", "shipped_at": "2026-05-03T10:00:00Z",
            "totals": {
                "input_tokens": 125_000, "output_tokens": 32_500, "cache_read_tokens": 3_400_000,
                "cost_usd": 443.954, "invocations": 55, "tasks": 5,
            },
            "tasks": [
                _task("pm", "claude-sonnet-4-6", 10_000, 2_000, 1_000, 100_000, 1.25, 2),
                _task("Coder", "claude-opus-4-8", 100_000, 28_000, 9_000, 3_300_000, 413.504, 45),
                _task("tester", "claude-opus-4-8", 15_000, 2_500, 0, 0, 29.2, 8),
            ],
        },
        "v1.3.0": {
            "version": "v1.3.0", "shipped_at": "",
            "totals": {
                "input_tokens": 8_000, "output_tokens": 900, "cache_read_tokens": 0,
                "cost_usd": 0.0, "invocations": 1,
            },
            "tasks": [
                _task("pm", "claude-sonnet-4-6", 8_000, 900, 0, 0, 0.0, 1,
                      input_cost_usd=0.024, output_cost_usd=0.0135),
            ],
        },
    }


def _history_rows() -> list[dict[str, str]]:
    base = {c: "" for c in HISTORY_COLUMNS}
    rows = []
    for values in (
        {"rc": "v1.0.0", "project": "demo", "tasks_total": "3", "input_tokens": "1000",
         "output_tokens": "500", "cache_read_tokens": "0", "cache_write_tokens": "0"},
        {"rc": "v1.1.0", "project": "demo", "wall_time_minutes": "95", "tasks_total": "2",
         "input_tokens": "120000", "output_tokens": "30000", "cache_read_tokens": "400000",
         "cache_write_tokens": "8000", "blocked_minutes": "0"},
        {"rc": "v1.2.0", "project": "demo", "wall_time_minutes": "120.5", "tasks_total": "5",
         "input_tokens": "1234567", "output_tokens": "32500", "cache_read_tokens": "3400000",
         "cache_write_tokens": "10000", "blocked_minutes": "45"},
        {"rc": "v0.9.0", "project": "other", "wall_time_minutes": "10", "tasks_total": "1",
         "input_tokens": "10", "output_tokens": "5", "blocked_minutes": "1234"},
    ):
        row = dict(base)
        row.update(values)
        rows.append(row)
    return rows


def _write_history(path: pathlib.Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [",".join(HISTORY_COLUMNS)]
    lines.extend(",".join(row[c] for c in HISTORY_COLUMNS) for row in rows)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _metrics_rollups() -> dict[str, dict]:
    """The demo project's metrics/rc files: v1.10.0 sorts last by version, not by name."""
    out: dict[str, dict] = {}
    for version, agents in (
        ("v1.9.0", {"coder": AgentLoad(tasks=2, input=1_000, output=200, cache_read=5_000, cache_write=300,
                                       wall_seconds=1_800, wall_task_count=2)}),
        ("v1.10.0", {
            "coder": AgentLoad(tasks=3, input=2_500_000, output=41_000, cache_read=900_000, cache_write=12_000,
                               wall_seconds=5_400, wall_task_count=3),
            "pm": AgentLoad(tasks=1, input=4_000, output=900),
        }),
    ):
        payload = rollup_payload(RcSeed(version, "shipped", agents))
        payload["project"] = "demo"
        for agent, entry in payload["tokens"]["by_agent"].items():
            entry["invocations"] = agents[agent].tasks * 2
        out[version] = payload
    other = rollup_payload(RcSeed("v1.2.0", "cancelled", {"cm": AgentLoad(tasks=1, input=7)}))
    other["project"] = "other"
    out["v1.2.0"] = other
    return out


def build_usage_tree(root: pathlib.Path, today: Optional[datetime.date] = None) -> pathlib.Path:
    """Lay out the fixture kanban root under *root* and return it.

    *today* names the daily roll-up the costs pane reads (default: the
    local date, which is what ``date +%Y-%m-%d`` in the pane returns).
    """
    today = today or datetime.date.today()
    root.mkdir(parents=True, exist_ok=True)
    (root / "projects.cfg").write_text(
        "".join(f"[project:{name}]\npriority={prio}\nenabled=true\n\n" for name, prio in PROJECTS),
        encoding="utf-8",
    )

    dev_tree = root / "devtree"
    _write_json(dev_tree / "team" / "scripts" / "lib" / "token_pricing.json", _PRICING)

    demo = root / "projects" / "demo"
    (demo / "tasks").mkdir(parents=True, exist_ok=True)
    (demo / "project.cfg").write_text(
        f"[project]\nproject_name = demo\ndev_tree_path = {dev_tree}\n", encoding="utf-8"
    )
    (demo / "release-state.md").write_text(
        "# Release State\n\n## Active RC\nv1.3.0\n\n## RC Opened At\n2026-05-20T00:00:00Z\n\n"
        "## RC Opened By Task\nCM-open\n\n## Last Released\nv1.2.0\n",
        encoding="utf-8",
    )
    for name, data in _daily_rollups().items():
        path = demo / "usage" / "daily" / name
        if isinstance(data, str):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(data, encoding="utf-8")
        else:
            _write_json(path, data)
    _write_json(
        demo / "usage" / "daily" / f"{today.isoformat()}.json",
        _day(
            {"input_tokens": 3_000, "output_tokens": 700, "cost_usd": 1.2, "invocations": 2},
            {"coder": _agent(3_000, 700, 1.2, 2)},
            {
                "claude-sonnet-4-6": _model(2_000, 500, 1_000, 40_000, 0.9, 1,
                                            input_cost_usd=0.006, cache_creation_cost_usd=0.00375,
                                            cache_read_cost_usd=0.012, output_cost_usd=0.0075),
                "mystery-model": _model(1_000, 200, 0, 0, 0.3, 1,
                                        input_cost_usd=1234.5, output_cost_usd=0.25),
            },
        ),
    )
    for version, data in _rc_rollups().items():
        _write_json(demo / "usage" / "rc" / f"{version}-tokens.json", data)
    _write_history(demo / "metrics" / "history.csv", _history_rows())
    for version, data in _metrics_rollups().items():
        _write_json(demo / "metrics" / "rc" / f"{version}.json", data)

    quiet = root / "projects" / "quiet"
    (quiet / "tasks").mkdir(parents=True, exist_ok=True)
    (quiet / "project.cfg").write_text("[project]\nproject_name = quiet\n", encoding="utf-8")
    _write_json(
        quiet / "usage" / "daily" / "2026-05-02.json",
        _day(
            {"input_tokens": 500, "output_tokens": 100, "cost_usd": 0.05, "invocations": 1},
            {"pm": _agent(500, 100, 0.05, 1)},
            {"claude-sonnet-4-6": _model(500, 100, 0, 0, 0.05, 1)},
        ),
    )
    _write_history(quiet / "metrics" / "history.csv", [])

    (root / "projects" / "fresh").mkdir(parents=True, exist_ok=True)
    return root


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------

#: (label, script, argv, extra environment)
CLI_CASES: tuple[tuple[str, str, tuple[str, ...], dict[str, str]], ...] = (
    ("cost month", "cost-report", ("--project", "demo", "--month", "2026-05"), {}),
    ("cost month legacy", "cost-report", ("--project", "demo", "--month", "2026-04"), {}),
    ("cost month no pricing", "cost-report", ("--project", "quiet", "--month", "2026-05"), {}),
    ("cost day", "cost-report", ("--project", "demo", "--day", "2026-05-03"), {}),
    ("cost day idle", "cost-report", ("--project", "demo", "--day", "2026-05-11"), {}),
    ("cost rc", "cost-report", ("--project", "demo", "--rc", "v1.2.0"), {}),
    ("cost rc bare", "cost-report", ("--rc", "1.1.0"), {"PGAI_PROJECT_NAME": "demo"}),
    ("cost since until", "cost-report", ("--project", "demo", "--since", "2026-05-01", "--until", "2026-05-10"), {}),
    ("cost until", "cost-report", ("--project", "demo", "--until", "2026-05-03"), {}),
    ("cost week", "cost-report", ("--project", "demo", "--week", "2026-W18"), {}),
    ("cost year empty", "cost-report", ("--project", "demo", "--year", "2025"), {}),
    ("cost csv month", "cost-report", ("--project", "demo", "--month", "2026-05", "--csv"), {}),
    ("cost csv rc", "cost-report", ("--csv", "--project", "demo", "--rc", "v1.2.0"), {}),
    ("cost csv range", "cost-report", ("--project", "demo", "--since", "2026-05-02", "--until", "2026-05-03", "--csv"), {}),
    ("cost kanban root flag", "cost-report", ("--kanban-root", "{ROOT}", "--project", "demo", "--day", "2026-05-01"), {}),
    ("cost conflict", "cost-report", ("--project", "demo", "--month", "2026-05", "--day", "2026-05-01"), {}),
    ("cost no project", "cost-report", ("--month", "2026-05"), {}),
    ("cost unknown project", "cost-report", ("--project", "nope", "--month", "2026-05"), {}),
    ("cost unknown arg", "cost-report", ("--project", "demo", "--bogus"), {}),
    ("cost month missing value", "cost-report", ("--project", "demo", "--month"), {}),
    ("cost last zero", "cost-report", ("--project", "demo", "--last", "0"), {}),
    ("cost last word", "cost-report", ("--project", "demo", "--last", "abc"), {}),
    ("cost help", "cost-report", ("--help",), {}),
    ("metrics csv project", "metrics-report", ("--csv", "--project", "demo"), {}),
    ("metrics csv env project", "metrics-report", ("--csv",), {"PGAI_PROJECT_NAME": "demo"}),
    ("metrics csv empty", "metrics-report", ("--csv", "--project", "quiet"), {}),
    ("metrics jsonl project", "metrics-report", ("--format", "jsonl", "--project", "demo"), {}),
    ("metrics jsonl env project", "metrics-report", ("--format", "jsonl"), {"PGAI_PROJECT_NAME": "demo"}),
    ("metrics csv jsonl", "metrics-report", ("--csv", "--format", "jsonl", "--project", "demo"), {}),
    ("metrics rc", "metrics-report", ("--rc", "1.9.0", "--project", "demo"), {}),
    ("metrics bad format", "metrics-report", ("--format", "xml", "--project", "demo"), {}),
    ("show project", "show-metrics", ("--project", "demo", "--no-color"), {}),
    ("show project color", "show-metrics", ("--project", "demo"), {"TERM": "xterm"}),
    ("show last", "show-metrics", ("--project", "demo", "--last", "2", "--no-color"), {}),
    ("show all projects", "show-metrics", ("--no-color",), {}),
    ("show all projects color", "show-metrics", (), {"TERM": "xterm"}),
    ("show no color env", "show-metrics", ("--project", "demo"), {"NO_COLOR": "1", "TERM": "xterm"}),
    ("show dumb term", "show-metrics", ("--project", "demo"), {"TERM": "dumb"}),
    ("show quiet", "show-metrics", ("--project", "quiet", "--no-color"), {}),
    ("show fresh", "show-metrics", ("--project", "fresh"), {"TERM": "xterm"}),
    ("show per agent", "show-metrics", ("--per-agent", "--project", "demo", "--no-color"), {}),
    ("show per agent color", "show-metrics", ("--per-agent",), {"PGAI_PROJECT_NAME": "demo", "TERM": "xterm"}),
    ("show per agent no rollups", "show-metrics", ("--per-agent", "--project", "quiet"), {}),
    ("show per agent no project", "show-metrics", ("--per-agent",), {}),
    ("show history csv", "show-metrics", ("--history-csv", "{ROOT}/projects/demo/metrics/history.csv", "--project", "demo", "--last", "1"), {"TERM": "xterm"}),
    ("show history csv missing", "show-metrics", ("--history-csv", "{ROOT}/nope.csv", "--project", "demo"), {}),
    ("show history csv no project", "show-metrics", ("--history-csv", "{ROOT}/projects/demo/metrics/history.csv"), {}),
    ("show kanban root flag", "show-metrics", ("--kanban-root", "{ROOT}", "--project", "demo", "--no-color"), {}),
    ("show unknown arg", "show-metrics", ("--per_agent",), {}),
    ("show help", "show-metrics", ("--help",), {}),
    ("costs pane", "costs", ("--no-color",), {"PGAI_DEV_TREE_PATH": "{REPO}", "TERM": "xterm"}),
    ("costs pane color", "costs", (), {"PGAI_DEV_TREE_PATH": "{REPO}", "TERM": "xterm"}),
    ("agent metrics", "agent-metrics", ("--project", "demo", "--no-color"), {}),
    ("agent metrics all", "agent-metrics", ("--all",), {"TERM": "xterm"}),
)

#: (label, path, query parameters)
REST_CASES: tuple[tuple[str, str, dict[str, Any]], ...] = (
    ("GET /costs month", "/costs", {"project": "demo", "month": "2026-05"}),
    ("GET /costs day", "/costs", {"project": "demo", "day": "2026-05-03"}),
    ("GET /costs rc", "/costs", {"project": "demo", "rc": "1.2.0"}),
    ("GET /costs no pricing", "/costs", {"project": "quiet", "month": "2026-05"}),
    ("GET /costs conflict", "/costs", {"project": "demo", "month": "2026-05", "rc": "v1.2.0"}),
    ("GET /costs unknown project", "/costs", {"project": "nope", "day": "2026-05-01"}),
    ("GET /costs json", "/costs", {"project": "demo", "format": "json"}),
    ("GET /costs json month", "/costs", {"project": "demo", "format": "json", "month": "2026-05"}),
    ("GET /costs json rc", "/costs", {"project": "demo", "format": "json", "rc": "1.1.0"}),
    ("GET /metrics", "/metrics", {"project": "demo"}),
    ("GET /metrics last", "/metrics", {"project": "demo", "last": 2}),
    ("GET /metrics all projects", "/metrics", {}),
    ("GET /metrics per agent", "/metrics", {"project": "demo", "per_agent": "true"}),
    ("GET /metrics json", "/metrics", {"project": "demo", "format": "json"}),
    ("GET /metrics json last", "/metrics", {"project": "demo", "format": "json", "last": 1}),
    ("GET /agent-metrics", "/agent-metrics", {"project": "demo"}),
)


def cli_label(case: tuple) -> str:
    return case[0]


# ---------------------------------------------------------------------------
# Running and normalising
# ---------------------------------------------------------------------------

_UPDATED_RE = re.compile(r"updated \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")


def normalise(text: str, root: pathlib.Path, today: datetime.date, script: Optional[pathlib.Path] = None) -> str:
    """Replace the run-specific parts of *text* with placeholders."""
    if script is not None:
        text = text.replace(str(script), "<SCRIPT>")
    text = text.replace(str(root.resolve()), "<ROOT>").replace(str(root), "<ROOT>")
    text = text.replace(str(_TEAM_DIR), "<TEAM>")
    text = _UPDATED_RE.sub("updated <NOW>", text)
    return text.replace(today.isoformat(), "<TODAY>")


def cli_env(root: pathlib.Path, home: pathlib.Path, scratch: pathlib.Path, extra: dict[str, str]) -> dict[str, str]:
    """The explicit environment every CLI case runs under."""
    env = build_scrubbed_env(
        home_dir=home,
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(scratch),
        },
    )
    env.update({k: v.replace("{REPO}", str(_TEAM_DIR.parent)) for k, v in extra.items()})
    return env


def run_cli_case(case: tuple, root: pathlib.Path, work: pathlib.Path, today: datetime.date) -> dict[str, Any]:
    """Run one CLI case against *root*; return its normalised outcome."""
    label, script_key, argv, extra = case
    script = SCRIPTS[script_key]
    home = work / "home"
    scratch = work / "scratch"
    home.mkdir(parents=True, exist_ok=True)
    scratch.mkdir(parents=True, exist_ok=True)
    args = [a.replace("{ROOT}", str(root)) for a in argv]
    completed = subprocess.run(
        ["bash", str(script), *args],
        cwd=str(scratch),
        env=cli_env(root, home, scratch, extra),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return {
        "exit_code": completed.returncode,
        "stdout": normalise(completed.stdout, root, today, script),
        "stderr": normalise(completed.stderr, root, today, script),
    }


@contextlib.contextmanager
def rest_environment(root: pathlib.Path) -> Iterator[None]:
    """Point this process at *root* for the duration of a request."""
    saved = {k: os.environ.get(k) for k in ("PGAI_AGENT_KANBAN_ROOT_PATH", "KANBAN_ROOT", "PGAI_PROJECT_NAME", "NO_COLOR", "TERM")}
    os.environ["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
    os.environ["KANBAN_ROOT"] = str(root)
    for name in ("PGAI_PROJECT_NAME", "NO_COLOR"):
        os.environ.pop(name, None)
    os.environ["TERM"] = "xterm"
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def build_client(root: pathlib.Path) -> Any:
    """An in-process client rooted at *root*, the usage tree every route reads."""
    from fastapi.testclient import TestClient

    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    cfg = ApiConfig(host="127.0.0.1", port=0, kanban_root=root)
    return TestClient(create_app(cfg=cfg), base_url="http://127.0.0.1", raise_server_exceptions=True)


def run_rest_case(case: tuple, client: Any, root: pathlib.Path, today: datetime.date) -> dict[str, Any]:
    """Send one REST case through *client* with *root* as the kanban root."""
    _label, path, params = case
    with rest_environment(root):
        response = client.get(path, params=params)
    return {
        "status": response.status_code,
        "body": json.loads(normalise(response.text, root, today)),
    }


def record(work: pathlib.Path) -> dict[str, Any]:
    """Build a tree under *work* and run every case; the golden file's shape."""
    today = datetime.date.today()
    root = build_usage_tree(work / "kanban", today)
    out: dict[str, Any] = {"cli": {}, "rest": {}}
    for case in CLI_CASES:
        out["cli"][case[0]] = run_cli_case(case, root, work, today)
    client = build_client(root)
    for case in REST_CASES:
        out["rest"][case[0]] = run_rest_case(case, client, root, today)
    return out


if __name__ == "__main__":
    # Recording entry point: python3 -m tests.fixtures.metrics_cases WORKDIR
    print(json.dumps(record(pathlib.Path(sys.argv[1])), indent=2, sort_keys=True))
