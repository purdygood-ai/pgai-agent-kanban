"""
test_cli_rest_agent_metrics_parity.py — CLI/REST agent-metrics parity.

Drives BOTH surfaces -- team/scripts/dashboard/agent-metrics.sh (the CLI,
invoked as a real subprocess) and GET /agent-metrics (the REST endpoint,
invoked through the FastAPI TestClient) -- over the SAME fixture kanban
root, and asserts they agree field for field: every agent's one-decimal
averages and integer totals, and the shipped / excluded RC counts the CLI
footer narrates, for every span mode (the default newest-10 window,
window/last N, all, since vX.Y.Z).  This is a comparison test, not two
independently pinned expectations: if either surface is changed to
disagree with the other (a re-derived average, a stale copy of the span
logic, a rounding drift), this test fails.  Two tests that each assert a
hardcoded number would not catch that drift.

Both surfaces resolve through the exact same functions, select_rc_span(),
compute_agent_averages() and round_one_decimal(), defined in exactly one
place -- team/pgai_agent_kanban/lib/agent_metrics.py:
  - agent-metrics.sh's own team/scripts/lib/agent_metrics.py imports those
    functions (never redefining them) and calls them from its
    render_project_block().
  - GET /agent-metrics (team/pgai_agent_kanban/api/reads.py) imports the
    same package module directly and calls the same functions.

Value comparison note: the CLI renders THOUSANDS-SEPARATED numbers
(e.g. "1,250.0" for an average, "12,500" for a total); the REST endpoint
returns plain JSON numbers (1250.0, 12500) -- thousands separation is a
presentation concern belonging to the CLI's renderer alone.  The
comparison strips the CLI's commas and nothing else: both surfaces round
every average with the same one-decimal rule, so no re-rounding is
applied on either side.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_API_TESTS_DIR = _THIS_FILE.parent
_TEAM_DIR = _API_TESTS_DIR.parent.parent.parent  # team/
_PROJECT_ROOT = _TEAM_DIR.parent                  # project root (contains team/)
_AGENT_METRICS_SH = _TEAM_DIR / "scripts" / "dashboard" / "agent-metrics.sh"

for _path in (_PROJECT_ROOT, _TEAM_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from tests.fixtures.rc_rollup_history import (  # noqa: E402
    AgentLoad,
    RcSeed,
    build_rc_history,
    write_rollups,
)


# ---------------------------------------------------------------------------
# Fixture builder helpers
# ---------------------------------------------------------------------------


def _write_projects_cfg(kanban_root: pathlib.Path, projects: list[tuple[str, int]]) -> None:
    lines = ["# Format: INI-style"]
    for name, priority in projects:
        lines.append(f"[project:{name}]")
        lines.append(f"priority={priority}")
        lines.append("enabled=true")
        lines.append("")
    (kanban_root / "projects.cfg").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _rc_dir(kanban_root: pathlib.Path, project: str) -> pathlib.Path:
    return kanban_root / "projects" / project / "metrics" / "rc"


def _write_rc_json(rc_dir: pathlib.Path, version: str, payload: dict) -> None:
    """Write a shipped RC rollup (outcome defaults to "shipped"; only
    shipped rollups enter the averaging span)."""
    rc_dir.mkdir(parents=True, exist_ok=True)
    (rc_dir / f"{version}.json").write_text(json.dumps({"outcome": "shipped", **payload}), encoding="utf-8")


def _build_fixture(kanban_root: pathlib.Path) -> None:
    """Two projects: one with a normal multi-RC, multi-agent window
    (including an absent-wall-time RC and a single-invocation agent), one
    registered project with no metrics/rc/ directory at all (the CLI's
    "(no metrics yet)" placeholder case)."""
    _write_projects_cfg(kanban_root, [("demo-project", 1), ("empty-project", 2)])

    rc_dir = _rc_dir(kanban_root, "demo-project")
    _write_rc_json(
        rc_dir,
        "v0.1.0",
        {
            "wall_time_minutes": 30,
            "tasks": {"by_agent": {"coder": 2, "writer": 1}},
            "tokens": {
                "by_agent": {
                    "coder": {"input": 1000, "output": 500, "cache_read": 0, "cache_write": 0},
                    "writer": {"input": 2000, "output": 1000, "cache_read": 0, "cache_write": 0},
                }
            },
            # Per-task wall data (the `wall.by_agent` block): coder averages
            # 30 min/task, writer 45 min/task -- deliberately different, so
            # an RC-level wall join (both would then read 30) is caught by
            # the sanity assertion below rather than passing by coincidence.
            "wall": {
                "by_agent": {
                    "coder": {"seconds": 3600, "task_count": 2},
                    "writer": {"seconds": 2700, "task_count": 1},
                }
            },
        },
    )
    _write_rc_json(
        rc_dir,
        "v0.2.0",
        {
            "wall_time_minutes": None,
            "tasks": {"total": 1, "by_agent": {"coder": 3}},
            "tokens": {
                "by_agent": {
                    "coder": {"input": 500, "output": 500, "cache_read": 0, "cache_write": 0},
                }
            },
            # No "wall" key -- a legacy rollup predating per-task capture;
            # coder's avg_wall_minutes must be derived from v0.1.0 alone.
        },
    )

    (kanban_root / "projects" / "empty-project" / "metrics").mkdir(parents=True)


def _cm_only(tasks: int) -> dict[str, AgentLoad]:
    return {"cm": AgentLoad(tasks=tasks, input=100 * tasks, output=10 * tasks)}


def _build_five_shipped_one_cancelled(kanban_root: pathlib.Path, project: str) -> None:
    """Five shipped rollups where cm works 2 tasks each, and one cancelled
    rollup (cm: 7 tasks) in the middle of the newest-10 range -- the
    fixture the shared span arithmetic is proven on."""
    _write_projects_cfg(kanban_root, [(project, 1)])
    write_rollups(
        _rc_dir(kanban_root, project),
        [
            RcSeed("v1.0.0", "shipped", _cm_only(2)),
            RcSeed("v1.1.0", "shipped", _cm_only(2)),
            RcSeed("v1.2.0", "cancelled", _cm_only(7)),
            RcSeed("v1.3.0", "shipped", _cm_only(2)),
            RcSeed("v1.4.0", "shipped", _cm_only(2)),
            RcSeed("v1.5.0", "shipped", _cm_only(2)),
        ],
    )


# Indices (oldest first) of the cancelled rollups in the 40-RC history:
# one below v0.20.0, one inside the newest-10 window, one in between.
_FORTY_RC_CANCELLED = frozenset({3, 17, 35})


def _build_forty_rc_history(kanban_root: pathlib.Path, project: str) -> None:
    """40 rollups over a version series crossing v0.20.0 (lexicographic and
    semantic order disagree there), three of them cancelled."""
    _write_projects_cfg(kanban_root, [(project, 1)])
    build_rc_history(_rc_dir(kanban_root, project), 40, cancelled=_FORTY_RC_CANCELLED)


# ---------------------------------------------------------------------------
# CLI-side helper: run agent-metrics.sh, parse the rendered rows and footer
# ---------------------------------------------------------------------------


def _cli_env(kanban_root: pathlib.Path) -> dict[str, str]:
    env = dict(os.environ)
    for key in (
        "PGAI_PROJECT_NAME",
        "PGAI_DEV_TREE_PATH",
        "PGAI_PROJECT_ROOT",
        "PGAI_AGENT_KANBAN_ROOT_PATH",
    ):
        env.pop(key, None)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    return env


def _parse_average(raw: str) -> float | None:
    """A CLI average cell ('--', '30.0', or '1,250.0') with its commas
    stripped: None for the placeholder, else the one-decimal value."""
    if raw == "--":
        return None
    return float(raw.replace(",", ""))


def _parse_total(raw: str) -> int:
    """A CLI total cell ('10' or '12,500') with its commas stripped."""
    return int(raw.replace(",", ""))


# A rendered agent row: "<agent>  <wall>  <tokens>  <tasks>  <total tokens>
# <total tasks>", whitespace-padded; the three averages carry one decimal.
_ROW_RE = re.compile(
    r"^(\S+)\s+(--|[\d,]+\.\d)\s+(--|[\d,]+\.\d)\s+(--|[\d,]+\.\d)\s+([\d,]+)\s+([\d,]+)\s*$"
)

# The footer after the rows: "<label>: <N> shipped RCs (<M> cancelled excluded)",
# where <label> is "window", "all" or "since vX.Y.Z".
_FOOTER_RE = re.compile(
    r"^\s*(window|all|since\s+\S+):\s+([\d,]+)\s+shipped\s+RCs\s+\(([\d,]+)\s+cancelled\s+excluded\)\s*$"
)


def _run_cli(kanban_root: pathlib.Path, project: str, span_args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "bash", str(_AGENT_METRICS_SH),
            "--kanban-root", str(kanban_root),
            "--project", project,
            *span_args,
            "--no-color",
        ],
        capture_output=True,
        text=True,
        env=_cli_env(kanban_root),
        timeout=30,
    )


def _cli_agent_metrics(kanban_root: pathlib.Path, project: str, span_args: list[str]) -> dict:
    """Run agent-metrics.sh --project <project> <span_args>; parse rows and
    footer.

    Returns {"agents": {agent: {"avg_wall_minutes": float|None,
    "avg_tokens": float, "avg_tasks": float, "total_tokens": int,
    "total_tasks": int}}, "footer": (label, shipped, excluded) | None}.
    A project with no rendered rows (the "(no metrics yet)" placeholder)
    has an empty agents dict.
    """
    result = _run_cli(kanban_root, project, span_args)
    assert result.returncode == 0, f"agent-metrics.sh {span_args} failed: {result.stderr!r}"

    agents: dict[str, dict] = {}
    footer: tuple[str, int, int] | None = None
    for line in result.stdout.splitlines():
        fm = _FOOTER_RE.match(line)
        if fm:
            footer = (fm.group(1), _parse_total(fm.group(2)), _parse_total(fm.group(3)))
            continue
        m = _ROW_RE.match(line)
        if not m:
            continue
        agent, wall_raw, tokens_raw, tasks_raw, total_tokens_raw, total_tasks_raw = m.groups()
        agents[agent] = {
            "avg_wall_minutes": _parse_average(wall_raw),
            "avg_tokens": _parse_average(tokens_raw),
            "avg_tasks": _parse_average(tasks_raw),
            "total_tokens": _parse_total(total_tokens_raw),
            "total_tasks": _parse_total(total_tasks_raw),
        }
    return {"agents": agents, "footer": footer}


# ---------------------------------------------------------------------------
# REST-side helper: TestClient over the same fixture
# ---------------------------------------------------------------------------

# The per-agent fields the CLI renders as columns; REST's rc_count has no
# CLI column and is not part of the comparison.
_COMPARED_FIELDS = ("avg_wall_minutes", "avg_tokens", "avg_tasks", "total_tokens", "total_tasks")


def _make_client(sandbox_root: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    monkeypatch.delenv("PGAI_PROJECT_NAME", raising=False)
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(sandbox_root))
    monkeypatch.setenv("KANBAN_ROOT", str(sandbox_root))

    cfg = ApiConfig(host="127.0.0.1", port=0, kanban_root=sandbox_root)
    return TestClient(create_app(cfg=cfg), raise_server_exceptions=True)


def _rest_body(client: TestClient, project: str, params: dict) -> dict:
    resp = client.get("/agent-metrics", params={"project": project, **params})
    assert resp.status_code == 200, f"GET /agent-metrics {params} failed: {resp.status_code} {resp.text}"
    return resp.json()


def _rest_agents(body: dict) -> dict[str, dict]:
    return {entry["agent"]: {f: entry[f] for f in _COMPARED_FIELDS} for entry in body["agents"]}


def _assert_pair_agrees(cli: dict, body: dict, label: str) -> None:
    """The CLI rows equal the REST agents array field for field, and the
    CLI footer's shipped / excluded counts equal the JSON's."""
    rest_agents = _rest_agents(body)
    assert cli["agents"] == rest_agents, (
        f"[{label}] CLI and REST agent maps diverge over the SAME fixture.\n"
        f"CLI:  {cli['agents']}\n"
        f"REST: {rest_agents}"
    )
    assert cli["footer"] is not None, f"[{label}] CLI printed no footer line"
    _, cli_shipped, cli_excluded = cli["footer"]
    assert (cli_shipped, cli_excluded) == (body["shipped_count"], body["excluded_count"]), (
        f"[{label}] CLI footer counts {(cli_shipped, cli_excluded)} != REST "
        f"shipped_count/excluded_count {(body['shipped_count'], body['excluded_count'])}"
    )


# ---------------------------------------------------------------------------
# The parity tests
# ---------------------------------------------------------------------------


class TestCliRestAgentMetricsParity:
    def test_every_agent_field_matches_between_cli_and_rest(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """CLI and REST agree on every agent's averages and totals for a
        project with a normal multi-RC window (including an
        absent-wall-time RC and a single-invocation agent)."""
        _build_fixture(tmp_path)

        cli = _cli_agent_metrics(tmp_path, "demo-project", ["--window", "10"])
        with _make_client(tmp_path, monkeypatch) as client:
            body = _rest_body(client, "demo-project", {"window": 10})

        _assert_pair_agrees(cli, body, "window=10")
        # Sanity: the fixture actually exercised both a legacy RC with no
        # "wall"."by_agent" data and a multi-RC average -- a parity test
        # collapsed to a single trivial RC would not catch a span regression.
        agents = cli["agents"]
        assert set(agents.keys()) == {"coder", "writer"}
        assert agents["coder"]["avg_wall_minutes"] == 30.0  # only v0.1.0 carries wall data for coder
        assert agents["coder"]["avg_tasks"] == 2.5          # (2+3)/2, one decimal, never floored
        assert agents["coder"]["total_tasks"] == 5
        assert agents["coder"]["total_tokens"] == 2500
        # coder and writer are both invoked in v0.1.0 -- an RC-level join
        # would print the SAME figure for both. They must render each
        # agent's own per-task mean instead.
        assert agents["writer"]["avg_wall_minutes"] == 45.0
        assert agents["coder"]["avg_wall_minutes"] != agents["writer"]["avg_wall_minutes"]

    def test_cancelled_rc_is_excluded_and_counted_on_both_surfaces(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Five shipped RCs of 2 cm tasks each plus one cancelled RC: both
        surfaces read cm as 2.0 tasks/RC over 10 total tasks, and both
        count 5 shipped and 1 excluded RC."""
        _build_five_shipped_one_cancelled(tmp_path, "demo")

        cli = _cli_agent_metrics(tmp_path, "demo", [])
        with _make_client(tmp_path, monkeypatch) as client:
            body = _rest_body(client, "demo", {})

        _assert_pair_agrees(cli, body, "default")
        cm = _rest_agents(body)["cm"]
        assert cm["avg_tasks"] == 2.0
        assert cm["total_tasks"] == 10
        assert body["shipped_count"] == 5
        assert body["excluded_count"] == 1
        assert body["window"] == 10
        assert body["span"] == {"kind": "last", "count": 10, "since": None}

    @pytest.mark.parametrize(
        ("cli_args", "rest_params", "span"),
        [
            ([], {}, {"kind": "last", "count": 10, "since": None}),
            (["--window", "10"], {"window": 10}, {"kind": "last", "count": 10, "since": None}),
            (["--all"], {"all": "true"}, {"kind": "all", "count": None, "since": None}),
            (["--since", "v0.20.0"], {"since": "v0.20.0"}, {"kind": "since", "count": None, "since": "v0.20.0"}),
            (["--last", "3"], {"last": 3}, {"kind": "last", "count": 3, "since": None}),
        ],
        ids=["default", "window-10", "all", "since-v0.20.0", "last-3"],
    )
    def test_every_span_mode_matches_between_cli_and_rest(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        cli_args: list[str],
        rest_params: dict,
        span: dict,
    ) -> None:
        """Over a 40-RC history with cancelled RCs, each span flag and its
        query parameter select the same RCs: same agent rows, same footer
        counts, and the JSON names the span it selected."""
        _build_forty_rc_history(tmp_path, "demo")

        cli = _cli_agent_metrics(tmp_path, "demo", cli_args)
        with _make_client(tmp_path, monkeypatch) as client:
            body = _rest_body(client, "demo", rest_params)

        _assert_pair_agrees(cli, body, " ".join(cli_args) or "default")
        assert body["span"] == span
        assert body["agents"], "the 40-RC fixture must yield agent rows in every span"
        if span["kind"] == "last":
            assert body["window"] == span["count"]
        else:
            assert body["window"] == body["shipped_count"]

    def test_span_modes_select_different_rc_sets(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The span parameters are honored, not ignored: on the 40-RC
        history --all covers every shipped RC, --since and --last fewer."""
        _build_forty_rc_history(tmp_path, "demo")

        with _make_client(tmp_path, monkeypatch) as client:
            everything = _rest_body(client, "demo", {"all": "true"})
            since = _rest_body(client, "demo", {"since": "v0.20.0"})
            last3 = _rest_body(client, "demo", {"last": 3})

        assert everything["shipped_count"] == 40 - len(_FORTY_RC_CANCELLED)
        assert everything["excluded_count"] == len(_FORTY_RC_CANCELLED)
        assert 3 < since["shipped_count"] < everything["shipped_count"]
        assert last3["shipped_count"] == 3

    @pytest.mark.parametrize(
        ("rest_params", "cli_args", "named"),
        [
            ({"all": "true", "last": 3}, ["--all", "--last", "3"], ("all", "last")),
            ({"window": 5, "last": 3}, ["--window", "5", "--last", "3"], ("window", "last")),
            ({"last": 0}, ["--last", "0"], ("last",)),
            ({"window": 0}, ["--window", "0"], ("window",)),
            ({"since": "notaversion"}, ["--since", "notaversion"], ("since",)),
        ],
        ids=["all-and-last", "window-and-last", "last-zero", "window-zero", "since-not-a-version"],
    )
    def test_invalid_span_is_refused_on_both_surfaces_naming_the_parameter(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        rest_params: dict,
        cli_args: list[str],
        named: tuple[str, ...],
    ) -> None:
        """A conflicting or invalid span is a 422 on REST and a usage error
        (exit 1) on the CLI, and both messages name the parameter."""
        _build_five_shipped_one_cancelled(tmp_path, "demo")

        cli = _run_cli(tmp_path, "demo", cli_args)
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/agent-metrics", params={"project": "demo", **rest_params})

        assert resp.status_code == 422, f"{rest_params}: expected 422, got {resp.status_code} {resp.text}"
        detail = resp.json()["detail"]
        assert isinstance(detail, str)
        assert cli.returncode == 1, f"{cli_args}: expected exit 1, got {cli.returncode}"
        for name in named:
            assert re.search(rf"\b{name}\b", detail), f"422 detail {detail!r} does not name {name!r}"
            assert f"--{name}" in cli.stderr, f"CLI stderr {cli.stderr!r} does not name --{name}"

    def test_empty_project_matches_the_no_metrics_placeholder_on_both_surfaces(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A registered project with no metrics/rc/ directory renders as
        an empty agent list on both surfaces -- never a stack trace."""
        _build_fixture(tmp_path)

        cli = _cli_agent_metrics(tmp_path, "empty-project", ["--window", "10"])
        with _make_client(tmp_path, monkeypatch) as client:
            body = _rest_body(client, "empty-project", {"window": 10})

        assert cli["agents"] == {}
        assert body["agents"] == []
        assert (body["shipped_count"], body["excluded_count"]) == (0, 0)

    def test_unknown_project_matches_the_no_metrics_placeholder_on_both_surfaces(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A project name that is not registered in projects.cfg at all
        renders the SAME empty-agents placeholder on both surfaces --
        never a stack trace and never a divergent error shape."""
        _build_fixture(tmp_path)

        cli = _cli_agent_metrics(tmp_path, "never-registered", ["--window", "10"])
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/agent-metrics", params={"project": "never-registered", "window": 10})

        assert cli["agents"] == {}
        assert resp.status_code == 200
        assert resp.json()["agents"] == []

    def test_changing_the_fixture_data_changes_both_surfaces_in_lockstep(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A single fixture mutation (adding a new RC rollup) is reflected
        identically by both surfaces, proving they read the same underlying
        data rather than each carrying its own pinned expectation."""
        _write_projects_cfg(tmp_path, [("demo", 1)])
        rc_dir = _rc_dir(tmp_path, "demo")
        _write_rc_json(
            rc_dir,
            "v0.1.0",
            {
                "wall_time_minutes": 10,
                "tasks": {"by_agent": {"coder": 1}},
                "tokens": {"by_agent": {"coder": {"input": 100, "output": 0, "cache_read": 0, "cache_write": 0}}},
                "wall": {"by_agent": {"coder": {"seconds": 600, "task_count": 1}}},  # 10 min/task
            },
        )

        cli_before = _cli_agent_metrics(tmp_path, "demo", ["--window", "10"])
        with _make_client(tmp_path, monkeypatch) as client:
            body_before = _rest_body(client, "demo", {"window": 10})
        _assert_pair_agrees(cli_before, body_before, "before")
        assert _rest_agents(body_before) == {
            "coder": {
                "avg_wall_minutes": 10.0, "avg_tokens": 100.0, "avg_tasks": 1.0,
                "total_tokens": 100, "total_tasks": 1,
            }
        }

        _write_rc_json(
            rc_dir,
            "v0.2.0",
            {
                "wall_time_minutes": 30,
                "tasks": {"by_agent": {"coder": 3}},
                "tokens": {"by_agent": {"coder": {"input": 500, "output": 0, "cache_read": 0, "cache_write": 0}}},
                # Pooled with v0.1.0's (600s, 1 task): (600+4200)/(1+3)/60 == 20 min/task.
                "wall": {"by_agent": {"coder": {"seconds": 4200, "task_count": 3}}},
            },
        )

        cli_after = _cli_agent_metrics(tmp_path, "demo", ["--window", "10"])
        with _make_client(tmp_path, monkeypatch) as client:
            body_after = _rest_body(client, "demo", {"window": 10})
        _assert_pair_agrees(cli_after, body_after, "after")
        assert _rest_agents(body_after) == {
            "coder": {
                "avg_wall_minutes": 20.0, "avg_tokens": 300.0, "avg_tasks": 2.0,
                "total_tokens": 600, "total_tasks": 4,
            }
        }
