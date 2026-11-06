"""
test_lib_metrics.py — Unit tests for pgai_agent_kanban.lib.metrics: the one
implementation behind cost-report.sh, metrics-report.sh,
dashboard/show-metrics.sh, dashboard/costs.sh, dashboard/agent-metrics.sh
and GET /metrics, GET /costs and GET /agent-metrics.

Coverage:

  1. Behaviour against the committed golden
     (tests/fixtures/golden/metrics_costs_cli.json, recorded from the scripts
     and routes before the logic moved into the package) on the fixture
     usage tree in tests/fixtures/metrics_cases.py: every CLI case's exit
     code, stdout and stderr and every REST case's status and body are
     unchanged, except the outcomes recorded beside the old ones with their
     reason.
  2. The text routes print what the scripts print: GET /costs' stdout equals
     cost-report.sh's for the same scope, byte for byte, and GET /metrics'
     equals show-metrics.sh's.
  3. Route/CLI parity (tests/fixtures/route_cli_parity.py): GET /metrics,
     GET /costs and GET /agent-metrics call show_metrics, cost_report and
     compute_agent_averages with no subprocess, and show-metrics.sh,
     cost-report.sh, agent-metrics.sh, metrics-report.sh and costs.sh reach
     the package's functions through pp_run_ops.
  4. The pieces: the range scopes against a pinned "today", the aggregator
     hand-off for a missing roll-up, the colour decision, the costs pane's
     block total and day total, and that no script keeps inline Python.
  5. The pricing table and the aggregator are the kanban's: an
     install-shaped root's scripts/lib/token_pricing.json gives a project
     whose dev tree has none the subscription comparison with no warning and
     no message naming the project's tree; a source checkout finds its own
     team/scripts/lib/ table; with no table the warning names the kanban-side
     path.
"""

from __future__ import annotations

import datetime
import json
import pathlib
import re
import sys
import types

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.lib import metrics  # noqa: E402
from pgai_agent_kanban.lib import token_pricing  # noqa: E402
from pgai_agent_kanban.lib.metrics import costs as cost_report_module  # noqa: E402
from pgai_agent_kanban.lib.metrics import costs_pane  # noqa: E402
from pgai_agent_kanban.lib.metrics.common import color_enabled  # noqa: E402
from tests.fixtures import metrics_cases as cases  # noqa: E402
from tests.fixtures.route_cli_parity import (  # noqa: E402
    assert_cli_reaches_function,
    assert_route_cli_parity,
    assert_route_reaches_function,
    build_api_client,
)


def _golden() -> dict:
    return json.loads(cases.GOLDEN_FILE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def tree(tmp_path_factory) -> tuple[pathlib.Path, pathlib.Path, datetime.date]:
    """(kanban root, work dir, build day) of one fixture usage tree for the module."""
    work = tmp_path_factory.mktemp("metrics-cases")
    today = datetime.date.today()
    return cases.build_usage_tree(work / "kanban", today), work, today


@pytest.fixture(scope="module")
def client(tree):
    return cases.build_client(tree[0])


def _cli_case(label: str) -> tuple:
    return next(c for c in cases.CLI_CASES if c[0] == label)


def _rest_case(label: str) -> tuple:
    return next(c for c in cases.REST_CASES if c[0] == label)


# ---------------------------------------------------------------------------
# 1. Behaviour equals the recorded behaviour
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", [c[0] for c in cases.CLI_CASES])
def test_cli_case_matches_the_recorded_outcome(tree, label):
    root, work, today = tree
    golden = _golden()
    expected = golden["deliberate_changes"].get("cli", {}).get(label, {}).get("outcome", golden["cli"][label])
    assert cases.run_cli_case(_cli_case(label), root, work, today) == expected


@pytest.mark.parametrize("label", [c[0] for c in cases.REST_CASES])
def test_rest_case_matches_the_recorded_outcome(tree, client, label):
    root, _work, today = tree
    golden = _golden()
    expected = golden["deliberate_changes"].get("rest", {}).get(label, {}).get("outcome", golden["rest"][label])
    assert cases.run_rest_case(_rest_case(label), client, root, today) == expected


def test_every_case_is_recorded():
    golden = _golden()
    assert set(golden["cli"]) == {c[0] for c in cases.CLI_CASES}
    assert set(golden["rest"]) == {c[0] for c in cases.REST_CASES}


def test_each_deliberate_change_names_its_reason_and_differs_from_the_record():
    golden = _golden()
    assert golden["deliberate_changes"], "the recorded deliberate changes are part of the contract"
    for surface, labels in golden["deliberate_changes"].items():
        for label, change in labels.items():
            assert change["reason"].strip()
            assert change["outcome"] != golden[surface][label]


def test_the_per_agent_query_renders_the_per_agent_view():
    change = _golden()["deliberate_changes"]["rest"]["GET /metrics per agent"]["outcome"]
    assert change["status"] == 200
    assert "per-agent (v1.10.0)" in change["body"]["stdout"]
    assert change["body"]["stdout"] == _golden()["cli"]["show per agent color"]["stdout"]


# ---------------------------------------------------------------------------
# 2. The text routes print what the scripts print
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cli_label, rest_label",
    [
        ("cost month", "GET /costs month"),
        ("cost day", "GET /costs day"),
        ("cost rc", "GET /costs rc"),
        ("cost month no dev tree", "GET /costs no dev tree"),
        ("show project color", "GET /metrics"),
        ("show all projects color", "GET /metrics all projects"),
    ],
)
def test_text_route_stdout_equals_the_script_stdout(tree, client, cli_label, rest_label):
    root, work, today = tree
    cli = cases.run_cli_case(_cli_case(cli_label), root, work, today)
    rest = cases.run_rest_case(_rest_case(rest_label), client, root, today)
    assert rest["status"] == 200
    assert rest["body"]["stdout"] == cli["stdout"]
    assert rest["body"]["stderr"] == cli["stderr"]
    assert rest["body"]["exit_code"] == cli["exit_code"] == 0


def test_costs_route_reports_a_missing_roll_up_without_aggregating(tree, client):
    root, _work, _today = tree
    with cases.rest_environment(root):
        response = assert_route_reaches_function(client, _costs_row(), params={"project": "demo", "day": "2026-06-30"})
    assert response.json()["stdout"] == "no data for day=2026-06-30\n"
    assert not (root / "projects" / "demo" / "usage" / "daily" / "2026-06-30.json").exists()


# ---------------------------------------------------------------------------
# 3. Route/CLI parity
# ---------------------------------------------------------------------------


def _costs_row():
    """GET /costs and cost-report.sh have no manifest row; this one names their shared function."""
    return types.SimpleNamespace(
        name="cost-report",
        impl="module:pgai_agent_kanban.lib.metrics.cost_report",
        rest_method="GET",
        rest_route="/costs",
        script="scripts/cost-report.sh",
    )


def _cli_only_row(function: str, script: str):
    return types.SimpleNamespace(
        name=pathlib.Path(script).stem,
        impl=f"module:pgai_agent_kanban.lib.metrics.{function}",
        rest_method="GET",
        rest_route="/",
        script=script,
    )


def _parity_env(tree, tmp_path: pathlib.Path, **extra: str) -> dict:
    root, _work, _today = tree
    return cases.cli_env(root, tmp_path / "home", tmp_path / "scratch", extra)


def test_manifest_rows_name_the_package_functions():
    from pgai_agent_kanban.lib import operator_verb_manifest as ovm

    assert ovm.get_verb("rc-metrics").impl == "module:pgai_agent_kanban.lib.metrics.show_metrics"
    assert ovm.get_verb("agent-metrics").impl == "module:pgai_agent_kanban.lib.metrics.compute_agent_averages"


def test_metrics_route_and_show_metrics_reach_show_metrics(tree, tmp_path):
    root, _work, _today = tree
    (tmp_path / "scratch").mkdir()
    with cases.rest_environment(root):
        result = assert_route_cli_parity(
            "rc-metrics",
            build_api_client(root),
            params={"project": "demo"},
            cli_args=["--project", "demo"],
            env=_parity_env(tree, tmp_path, TERM="xterm"),
            scratch_dir=tmp_path / "scratch",
        )
    assert result.cli.completed.returncode == 0
    assert result.response.json()["stdout"] == result.cli.completed.stdout


def test_agent_metrics_route_and_script_reach_compute_agent_averages(tree, tmp_path):
    root, _work, _today = tree
    (tmp_path / "scratch").mkdir()
    with cases.rest_environment(root):
        result = assert_route_cli_parity(
            "agent-metrics",
            build_api_client(root),
            params={"project": "demo"},
            cli_args=["--project", "demo", "--no-color"],
            env=_parity_env(tree, tmp_path),
            scratch_dir=tmp_path / "scratch",
        )
    assert result.response.status_code == 200
    assert "coder" in result.cli.completed.stdout


def test_costs_route_and_cost_report_reach_cost_report(tree, tmp_path):
    root, _work, _today = tree
    (tmp_path / "scratch").mkdir()
    with cases.rest_environment(root):
        result = assert_route_cli_parity(
            _costs_row(),
            build_api_client(root),
            params={"project": "demo", "rc": "v1.2.0"},
            cli_args=["--project", "demo", "--rc", "v1.2.0"],
            env=_parity_env(tree, tmp_path),
            scratch_dir=tmp_path / "scratch",
        )
    assert result.response.json()["stdout"] == result.cli.completed.stdout


@pytest.mark.parametrize(
    "function, script, cli_args, extra",
    [
        ("history_csv", "scripts/metrics-report.sh", ["--csv", "--project", "demo"], {}),
        ("rc_rollups_jsonl", "scripts/metrics-report.sh", ["--format", "jsonl", "--project", "demo"], {}),
        ("cost_block", "scripts/dashboard/costs.sh", ["--no-color"], {"TERM": "xterm"}),
        ("rc_label", "scripts/dashboard/costs.sh", ["--no-color"], {"TERM": "xterm"}),
        ("sum_amounts", "scripts/dashboard/costs.sh", ["--no-color"], {"TERM": "xterm"}),
    ],
)
def test_scripts_without_a_route_reach_the_package(tree, tmp_path, function, script, cli_args, extra):
    (tmp_path / "scratch").mkdir()
    run = assert_cli_reaches_function(
        _cli_only_row(function, script),
        cli_args=cli_args,
        env=_parity_env(tree, tmp_path, **extra),
        scratch_dir=tmp_path / "scratch",
    )
    assert run.completed.returncode == 0, run.completed.stderr


def test_no_listed_script_keeps_inline_python():
    heredoc = re.compile(r"python3?\b[^#]*<<")
    for rel in ("cost-report.sh", "metrics-report.sh", "dashboard/show-metrics.sh", "dashboard/costs.sh"):
        text = (_TEAM_DIR / "scripts" / rel).read_text(encoding="utf-8")
        assert not heredoc.search(text), rel
        assert "python3 -c" not in text, rel


# ---------------------------------------------------------------------------
# 4. The pieces
# ---------------------------------------------------------------------------

_PINNED_TODAY = datetime.date(2026, 5, 14)  # a Thursday


@pytest.mark.parametrize(
    "argv, value",
    [
        (["--week"], "2026-05-11|2026-05-14|Week 2026-W20 (2026-05-11 through 2026-05-14)"),
        (["--week", "current"], "2026-05-11|2026-05-14|Week 2026-W20 (2026-05-11 through 2026-05-14)"),
        (["--week", "2026-W09"], "2026-02-23|2026-03-01|Week 2026-W09 (2026-02-23 through 2026-03-01)"),
        (["--week", "2026-W20"], "2026-05-11|2026-05-14|Week 2026-W20 (2026-05-11 through 2026-05-14)"),
        (["--year"], "2026-01-01|2026-05-14|Year 2026 YTD (2026-01-01 through 2026-05-14)"),
        (["--year", "2026"], "2026-01-01|2026-05-14|Year 2026 (2026-01-01 through 2026-05-14)"),
        (["--last", "1"], "2026-05-14|2026-05-14|Last 1 days (2026-05-14 through 2026-05-14)"),
        (["--last-N-days", "7"], "2026-05-08|2026-05-14|Last 7 days (2026-05-08 through 2026-05-14)"),
        (["--since", "2026-05-01"], "2026-05-01|2026-05-14|Since 2026-05-01 through 2026-05-14"),
        (["--until", "2026-05-03"], "earliest|2026-05-03|Through 2026-05-03"),
    ],
)
def test_range_scopes_resolve_against_today(argv, value):
    run = cost_report_module._Run()
    args = cost_report_module.parse_cost_report_args(argv, run)
    assert cost_report_module.resolve_range(args, _PINNED_TODAY, run) == value


def test_csv_rows_carry_the_resolved_range_value(tree):
    root, _work, _today = tree
    result = metrics.cost_report(
        ["--project", "demo", "--last", "3", "--csv"],
        default_kanban_root=str(root),
        today=datetime.date(2026, 5, 3),
    )
    assert result.exit_code == 0
    rows = result.stdout.splitlines()[1:]
    assert rows and all(
        r.startswith("range,2026-05-01|2026-05-03|Last 3 days (2026-05-01 through 2026-05-03),demo,") for r in rows
    )


def test_default_scope_is_the_current_month(tree):
    root, _work, _today = tree
    result = metrics.cost_report(["--project", "demo"], default_kanban_root=str(root), today=datetime.date(2026, 5, 14))
    assert result.stdout.startswith("=== Token Usage: May 2026 — demo ===\nDays with activity: 3 / 14\n")


def test_a_missing_roll_up_is_handed_to_the_aggregator(tree):
    root, _work, _today = tree
    calls: list[tuple[str, list[str]]] = []
    result = metrics.cost_report(
        ["--project", "demo", "--rc", "v9.9.9"],
        default_kanban_root=str(root),
        aggregator=str(root / "aggregate_tokens.py"),
        aggregate=lambda path, args: calls.append((path, args)),
    )
    assert result.stdout == "no data for rc=v9.9.9\n"
    # The named aggregator does not exist and the kanban root has no pm-agent/:
    # the copy in this package's own tree runs, never a project's dev tree copy.
    own_tree_aggregator = str(token_pricing.aggregator_path(token_pricing.OWN_TREE_ROOT))
    assert calls == [(own_tree_aggregator, ["--project", "demo", "--rc", "v9.9.9"])]
    calls.clear()

    stub = root / "aggregate_tokens.py"
    stub.write_text("", encoding="utf-8")
    try:
        metrics.cost_report(
            ["--project", "demo", "--rc", "v9.9.9", "--kanban-root", str(root)],
            default_kanban_root="",
            aggregator=str(stub),
            aggregate=lambda path, args: calls.append((path, args)),
        )
    finally:
        stub.unlink()
    assert calls == [(str(stub), ["--project", "demo", "--rc", "v9.9.9", "--kanban-root", str(root)])]


@pytest.mark.parametrize(
    "flag, environ, expected",
    [
        (False, {"TERM": "xterm"}, True),
        (True, {"TERM": "xterm"}, False),
        (False, {"TERM": "xterm", "NO_COLOR": "1"}, False),
        (False, {"TERM": "xterm", "NO_COLOR": ""}, True),
        (False, {"TERM": "dumb"}, False),
        (False, {}, False),
        (False, {"TERM": ""}, True),
    ],
)
def test_colour_follows_the_flag_no_color_and_term(flag, environ, expected):
    assert color_enabled(flag, environ) is expected


def test_block_total_sums_the_printed_subtotals():
    lines = ["    subtotal:" + " " * 35 + "$1,235", "    subtotal:" + " " * 35 + "$0.03", "    claude / x"]
    assert costs_pane.block_total(lines) == "1235.03"
    assert costs_pane.block_total(["    (no data yet)"]) == "0.00"


def test_day_total_rounds_each_running_sum_and_is_zero_without_roll_ups():
    assert costs_pane.sum_amounts([]) == "0"
    assert costs_pane.sum_amounts(["1235.03", "0.00"]) == "1235.03"
    assert costs_pane.sum_amounts(["0.10", "0.20"]) == "0.30"


def test_rc_label_names_the_start_and_task_count(tmp_path):
    rollup = tmp_path / "v1.0.0-tokens.json"
    rollup.write_text(json.dumps({"shipped_at": "", "started_at": "2026-05-01", "tasks": [{}, {}]}), encoding="utf-8")
    assert costs_pane.rc_label(str(rollup)) == "started 2026-05-01, 2 tasks"
    rollup.write_text(json.dumps({"tasks": [{}]}), encoding="utf-8")
    assert costs_pane.rc_label(str(rollup)) == "1 tasks"
    rollup.write_text("{", encoding="utf-8")
    assert costs_pane.rc_label(str(rollup)) == ""


def test_show_metrics_needs_an_integer_last(tree):
    root, _work, _today = tree
    result = metrics.show_metrics(root, project="demo", last="ten")
    assert result.exit_code == 1
    assert result.stderr == "ERROR: --last must be an integer (got: ten)\n"


def test_json_rows_and_text_read_one_history(tree):
    root, _work, _today = tree
    rows = metrics.metrics_rows_for_project(root / "projects" / "demo")
    text = metrics.show_metrics(root, project="demo").stdout
    assert [r["version"] for r in rows] == re.findall(r"^(v\S+)\s", text, flags=re.MULTILINE)


# ---------------------------------------------------------------------------
# --help on the module and on a positional verb
# ---------------------------------------------------------------------------


def _metrics_cli(tmp_path: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    import subprocess

    from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

    team_dir = pathlib.Path(__file__).resolve().parents[2]
    env = build_scrubbed_env(home_dir=tmp_path / "home", extra_env={"PYTHONPATH": str(team_dir)})
    return subprocess.run(
        [sys.executable, "-m", "pgai_agent_kanban.lib.metrics", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=60,
        check=False,
    )


def test_module_help_prints_the_verb_list_and_exits_zero(tmp_path: pathlib.Path) -> None:
    result = _metrics_cli(tmp_path, "--help")
    assert result.returncode == 0, result.stderr
    assert "command-line entry point" in result.stdout
    for verb in ("show_metrics", "metrics_pane", "cost_block", "compute_agent_averages"):
        assert verb in result.stdout


def test_metrics_pane_help_prints_its_usage_and_exits_zero(tmp_path: pathlib.Path) -> None:
    result = _metrics_cli(tmp_path, "metrics_pane", "--help")
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(
        "usage: metrics_pane USE_COLOR TODAY PROJECTS_JSON RESET BOLD DIM CYAN GREEN YELLOW"
    )
    assert "Metrics pane" in result.stdout


@pytest.mark.parametrize("verb", ["history_csv", "rc_rollups_jsonl", "cost_block", "rc_label", "sum_amounts"])
def test_every_positional_verb_answers_help(verb: str, tmp_path: pathlib.Path) -> None:
    result = _metrics_cli(tmp_path, verb, "-h")
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(f"usage: {verb} ")


# ---------------------------------------------------------------------------
# 5. The pricing table and the aggregator are the kanban's, never a project's
# ---------------------------------------------------------------------------

#: A subscription price no shipped table carries, so the report shows which table it read.
_SANDBOX_PRO_CREDIT = 37.0


def _install_shaped_root(tmp_path: pathlib.Path, *, with_pricing: bool) -> tuple[pathlib.Path, pathlib.Path]:
    """(kanban root, project dev tree) for one project ``alpha`` with one daily roll-up.

    The root is laid out as installed -- ``scripts/lib/`` at its top, no
    ``team/`` -- and carries the pricing table when *with_pricing*. Alpha's
    ``dev_tree_path`` names a repository that carries no pricing table and no
    aggregator.
    """
    root = tmp_path / "kanban"
    if with_pricing:
        table = token_pricing.pricing_table_path(root)
        table.parent.mkdir(parents=True)
        table.write_text(
            json.dumps({"subscriptions": {"claude_pro_programmatic_credit": _SANDBOX_PRO_CREDIT}}),
            encoding="utf-8",
        )
    dev_tree = tmp_path / "alpha-repo"
    dev_tree.mkdir()
    project = root / "projects" / "alpha"
    (project / "usage" / "daily").mkdir(parents=True)
    (project / "project.cfg").write_text(
        f"[project]\nproject_name = alpha\ndev_tree_path = {dev_tree}\n", encoding="utf-8"
    )
    (project / "usage" / "daily" / "2026-05-02.json").write_text(
        json.dumps(
            {
                "totals": {"input_tokens": 500, "output_tokens": 100, "cost_usd": 0.05, "invocations": 1},
                "by_agent": {"pm": {"input": 500, "output": 100, "cost_usd": 0.05, "invocations": 1}},
                "by_model": {"claude-sonnet-4-6": {"input": 500, "output": 100, "cost_usd": 0.05, "invocations": 1}},
                "rcs_shipped": [],
            }
        ),
        encoding="utf-8",
    )
    return root, dev_tree


def test_install_layout_pricing_gives_a_project_without_one_the_subscription_comparison(tmp_path):
    root, dev_tree = _install_shaped_root(tmp_path, with_pricing=True)

    result = metrics.cost_report(["--project", "alpha", "--day", "2026-05-02"], default_kanban_root=str(root))

    assert result.exit_code == 0, result.stderr
    assert "Subscription comparison:" in result.stdout, result.stdout
    assert f"(${_SANDBOX_PRO_CREDIT:.2f})" in result.stdout, "the comparison is priced from <root>/scripts/lib"
    assert "token_pricing.json not found" not in result.stderr
    assert str(dev_tree) not in result.stdout + result.stderr


def test_the_pricing_table_and_aggregator_resolve_from_the_root_before_the_own_tree(tmp_path):
    root, dev_tree = _install_shaped_root(tmp_path, with_pricing=True)
    aggregator = token_pricing.aggregator_path(root)
    aggregator.parent.mkdir(parents=True)
    aggregator.write_text("", encoding="utf-8")

    assert token_pricing.find_pricing_table(root) == root / "scripts" / "lib" / "token_pricing.json"
    assert token_pricing.find_aggregator(root) == root / "pm-agent" / "aggregate_tokens.py"
    assert dev_tree not in token_pricing.kanban_tree_candidates(root)


def test_a_source_checkout_finds_its_own_team_scripts_lib_pricing_table(tmp_path):
    root, _dev_tree = _install_shaped_root(tmp_path, with_pricing=False)
    source_table = _TEAM_DIR / "scripts" / "lib" / "token_pricing.json"
    assert source_table.is_file(), f"source checkout has no pricing table at {source_table}"

    assert token_pricing.OWN_TREE_ROOT == _TEAM_DIR
    assert token_pricing.find_pricing_table(root) == source_table
    assert token_pricing.find_aggregator(root) == _TEAM_DIR / "pm-agent" / "aggregate_tokens.py"

    result = metrics.cost_report(["--project", "alpha", "--day", "2026-05-02"], default_kanban_root=str(root))
    assert "Subscription comparison:" in result.stdout, result.stdout
    assert "token_pricing.json not found" not in result.stderr


def test_with_no_pricing_table_the_warning_names_the_kanban_side_path_only(tmp_path, monkeypatch):
    root, dev_tree = _install_shaped_root(tmp_path, with_pricing=False)
    monkeypatch.setattr(token_pricing, "OWN_TREE_ROOT", tmp_path / "no-such-tree")

    result = metrics.cost_report(["--project", "alpha", "--day", "2026-05-02"], default_kanban_root=str(root))

    assert result.exit_code == 0, result.stderr
    assert "Subscription comparison:" not in result.stdout
    expected = root / "scripts" / "lib" / "token_pricing.json"
    assert (
        "WARNING: token_pricing.json not found; subscription comparison unavailable. " f"Expected at: {expected}"
    ) in result.stderr
    assert str(dev_tree) not in result.stdout + result.stderr
