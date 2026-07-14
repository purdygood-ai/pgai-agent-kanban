"""
test_metrics_json.py — Equality comparison tests for ?format=json on /metrics and /costs.

Two suites:

  1. **Equality tests** (``TestMetricsJsonEquality``, ``TestCostsJsonEquality``):
     Build a fixture project with known history.csv and/or usage/rc/ data.
     Assert that the JSON rows' field values equal the values in the source files
     field-by-field.  This is the load-bearing check that keeps the two renderers
     (text shell-out and Python JSON reader) from drifting apart.

  2. **Text-identical regression tests** (``TestMetricsCostsTextRegression``):
     Assert that /metrics and /costs with no ``format`` parameter return a text
     envelope, not JSON rows.  The text path must continue to call the underlying
     shell scripts — the JSON branch must be fully gated on ``format=json``.

  3. **Missing-field omission tests** (``TestMissingFieldOmission``):
     Assert that rows whose source has empty/absent fields omit those fields from
     the JSON output — never null or zero-fill.

  4. **Validation tests** (``TestFormatJsonValidation``):
     Assert that ``format=json`` without a ``project`` parameter returns 422.

Design notes
------------
- Fixtures are materialised under pytest's tmp_path (redirected to the framework
  temp root by the parent conftest.py).
- The TestClient uses FastAPI's ASGI transport — no real network socket.
- The live kanban root is monkeypatched via PGAI_AGENT_KANBAN_ROOT_PATH so that
  the JSON branch's pathlib.Path(os.environ["PGAI_AGENT_KANBAN_ROOT_PATH"]) reads
  the fixture data rather than any live install.
"""

from __future__ import annotations

import csv
import io
import json
import pathlib
from typing import Generator

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Resolve dev-tree root for API imports
# (this file lives at team/pgai_agent_kanban/api/tests/)
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write_history_csv(
    project_root: pathlib.Path,
    rows: list[dict],
) -> None:
    """Write a history.csv fixture under project_root/metrics/.

    Args:
        project_root: The project directory under the kanban root.
        rows:         List of dicts with CSV column values.  Columns:
                      rc, wall_time_minutes, input_tokens, output_tokens,
                      cache_read_tokens, cache_write_tokens, tasks_total.
                      Missing keys default to empty string.
    """
    metrics_dir = project_root / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "rc",
        "wall_time_minutes",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "tasks_total",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({f: row.get(f, "") for f in fieldnames})
    (metrics_dir / "history.csv").write_text(buf.getvalue(), encoding="utf-8")


def _write_rc_tokens_file(
    project_root: pathlib.Path,
    version: str,
    data: dict,
) -> None:
    """Write a usage/rc/<version>-tokens.json fixture.

    Args:
        project_root: The project directory under the kanban root.
        version:      RC version string, e.g. "v1.5.0".
        data:         Dict to serialise as the tokens file content.
    """
    rc_dir = project_root / "usage" / "rc"
    rc_dir.mkdir(parents=True, exist_ok=True)
    (rc_dir / f"{version}-tokens.json").write_text(
        json.dumps(data, indent=2),
        encoding="utf-8",
    )


def _materialise_project(
    kanban_root: pathlib.Path,
    project_name: str,
) -> pathlib.Path:
    """Create the minimal project directory structure and return the project root."""
    project_root = kanban_root / "projects" / project_name
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "project.cfg").write_text(
        f"[project]\nproject_name = {project_name}\n",
        encoding="utf-8",
    )
    # projects.cfg required by the kanban registry
    cfg = kanban_root / "projects.cfg"
    if not cfg.is_file():
        cfg.write_text(
            f"[project:{project_name}]\npriority=1\nenabled=true\n",
            encoding="utf-8",
        )
    return project_root


# ---------------------------------------------------------------------------
# pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def metrics_kanban_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """Materialise a kanban root with a fixture project for metrics tests."""
    root = tmp_path / "kanban"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def metrics_api_client(
    metrics_kanban_root: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    """Create a TestClient whose PGAI_AGENT_KANBAN_ROOT_PATH points at the fixture root."""
    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(metrics_kanban_root))
    monkeypatch.setenv("KANBAN_ROOT", str(metrics_kanban_root))

    cfg = ApiConfig(
        host="127.0.0.1",
        port=0,
        kanban_root=_TEAM_DIR,  # scripts resolve from dev-tree team/
    )
    app = create_app(cfg=cfg)

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


# ---------------------------------------------------------------------------
# TestMetricsJsonEquality
# ---------------------------------------------------------------------------


class TestMetricsJsonEquality:
    """Assert that /metrics?format=json field values equal history.csv source values.

    Each test:
      1. Writes a known history.csv fixture.
      2. Calls GET /metrics?format=json.
      3. Parses the JSON response.
      4. Asserts each row's field values match the fixture data field-by-field.

    This is the load-bearing equality check specified in the acceptance criteria.
    """

    def test_metrics_json_parses_as_json(
        self,
        metrics_kanban_root: pathlib.Path,
        metrics_api_client: TestClient,
    ) -> None:
        """GET /metrics?format=json returns a valid JSON array."""
        project_name = "test-proj"
        project_root = _materialise_project(metrics_kanban_root, project_name)
        _write_history_csv(
            project_root,
            [
                {
                    "rc": "v1.0.0",
                    "input_tokens": "1000",
                    "output_tokens": "200",
                    "cache_read_tokens": "500",
                    "cache_write_tokens": "100",
                    "tasks_total": "5",
                    "wall_time_minutes": "30",
                },
            ],
        )

        resp = metrics_api_client.get(
            "/metrics",
            params={"project": project_name, "format": "json"},
        )

        assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
        rows = resp.json()
        assert isinstance(rows, list), f"Response must be a JSON array; got {type(rows)}"

    def test_metrics_json_version_equals_csv_rc(
        self,
        metrics_kanban_root: pathlib.Path,
        metrics_api_client: TestClient,
    ) -> None:
        """JSON row 'version' equals the 'rc' column from history.csv."""
        project_name = "eq-version"
        project_root = _materialise_project(metrics_kanban_root, project_name)
        _write_history_csv(
            project_root,
            [{"rc": "v2.3.0", "tasks_total": "10"}],
        )

        resp = metrics_api_client.get(
            "/metrics",
            params={"project": project_name, "format": "json"},
        )
        rows = resp.json()
        assert len(rows) == 1, f"Expected 1 row; got {len(rows)}"
        assert rows[0]["version"] == "v2.3.0", (
            f"row['version'] must equal CSV 'rc' field; got {rows[0]['version']!r}"
        )

    def test_metrics_json_tasks_equals_csv_tasks_total(
        self,
        metrics_kanban_root: pathlib.Path,
        metrics_api_client: TestClient,
    ) -> None:
        """JSON row 'tasks' equals the integer value of 'tasks_total' from history.csv."""
        project_name = "eq-tasks"
        project_root = _materialise_project(metrics_kanban_root, project_name)
        _write_history_csv(
            project_root,
            [{"rc": "v1.0.0", "tasks_total": "42"}],
        )

        resp = metrics_api_client.get(
            "/metrics",
            params={"project": project_name, "format": "json"},
        )
        rows = resp.json()
        assert rows[0]["tasks"] == 42, (
            f"row['tasks'] must equal int(tasks_total)=42; got {rows[0].get('tasks')!r}"
        )

    def test_metrics_json_tokens_in_equals_csv_input_tokens(
        self,
        metrics_kanban_root: pathlib.Path,
        metrics_api_client: TestClient,
    ) -> None:
        """JSON row 'tokens_in' equals the integer value of 'input_tokens' from history.csv."""
        project_name = "eq-tokens-in"
        project_root = _materialise_project(metrics_kanban_root, project_name)
        _write_history_csv(
            project_root,
            [{"rc": "v1.0.0", "input_tokens": "5000000"}],
        )

        resp = metrics_api_client.get(
            "/metrics",
            params={"project": project_name, "format": "json"},
        )
        rows = resp.json()
        assert rows[0]["tokens_in"] == 5000000, (
            f"row['tokens_in'] must equal int(input_tokens); got {rows[0].get('tokens_in')!r}"
        )

    def test_metrics_json_tokens_out_equals_csv_output_tokens(
        self,
        metrics_kanban_root: pathlib.Path,
        metrics_api_client: TestClient,
    ) -> None:
        """JSON row 'tokens_out' equals the integer value of 'output_tokens' from history.csv."""
        project_name = "eq-tokens-out"
        project_root = _materialise_project(metrics_kanban_root, project_name)
        _write_history_csv(
            project_root,
            [{"rc": "v1.0.0", "output_tokens": "250000"}],
        )

        resp = metrics_api_client.get(
            "/metrics",
            params={"project": project_name, "format": "json"},
        )
        rows = resp.json()
        assert rows[0]["tokens_out"] == 250000, (
            f"row['tokens_out'] must equal int(output_tokens); got {rows[0].get('tokens_out')!r}"
        )

    def test_metrics_json_wall_seconds_equals_csv_wall_minutes_times_60(
        self,
        metrics_kanban_root: pathlib.Path,
        metrics_api_client: TestClient,
    ) -> None:
        """JSON row 'wall_seconds' equals wall_time_minutes * 60 from history.csv."""
        project_name = "eq-wall"
        project_root = _materialise_project(metrics_kanban_root, project_name)
        _write_history_csv(
            project_root,
            [{"rc": "v1.0.0", "wall_time_minutes": "45.0"}],
        )

        resp = metrics_api_client.get(
            "/metrics",
            params={"project": project_name, "format": "json"},
        )
        rows = resp.json()
        assert rows[0]["wall_seconds"] == pytest.approx(2700.0, abs=0.5), (
            f"row['wall_seconds'] must equal wall_time_minutes * 60 = 2700.0; "
            f"got {rows[0].get('wall_seconds')!r}"
        )

    def test_metrics_json_cache_read_pct_computed_from_csv(
        self,
        metrics_kanban_root: pathlib.Path,
        metrics_api_client: TestClient,
    ) -> None:
        """JSON row 'cache_read_pct' is cache_read / (input + cache_read) * 100.

        With input_tokens=1000 and cache_read_tokens=500:
          pct = 500 / (1000 + 500) * 100 = 33.3...
        """
        project_name = "eq-cache-pct"
        project_root = _materialise_project(metrics_kanban_root, project_name)
        _write_history_csv(
            project_root,
            [{"rc": "v1.0.0", "input_tokens": "1000", "cache_read_tokens": "500"}],
        )

        resp = metrics_api_client.get(
            "/metrics",
            params={"project": project_name, "format": "json"},
        )
        rows = resp.json()
        expected_pct = round(500 / (1000 + 500) * 100, 1)
        assert rows[0]["cache_read_pct"] == pytest.approx(expected_pct, abs=0.05), (
            f"row['cache_read_pct'] must equal {expected_pct}; "
            f"got {rows[0].get('cache_read_pct')!r}"
        )

    def test_metrics_json_est_cost_from_rc_tokens_file(
        self,
        metrics_kanban_root: pathlib.Path,
        metrics_api_client: TestClient,
    ) -> None:
        """JSON row 'est_cost' equals totals.cost_usd from the RC usage file.

        The value from history.csv alone does not include est_cost — it comes from
        usage/rc/<version>-tokens.json.  Both sources are read; their row values
        must match the underlying data.
        """
        project_name = "eq-cost"
        project_root = _materialise_project(metrics_kanban_root, project_name)
        version = "v3.1.0"
        _write_history_csv(
            project_root,
            [{"rc": version, "input_tokens": "1000", "output_tokens": "100"}],
        )
        _write_rc_tokens_file(
            project_root,
            version,
            {
                "version": version,
                "totals": {
                    "input_tokens": 1000,
                    "output_tokens": 100,
                    "cost_usd": 1.234567,
                },
            },
        )

        resp = metrics_api_client.get(
            "/metrics",
            params={"project": project_name, "format": "json"},
        )
        rows = resp.json()
        assert rows[0]["est_cost"] == pytest.approx(1.234567, abs=0.000001), (
            f"row['est_cost'] must equal totals.cost_usd=1.234567; "
            f"got {rows[0].get('est_cost')!r}"
        )

    def test_metrics_json_multiple_rows_in_csv_order(
        self,
        metrics_kanban_root: pathlib.Path,
        metrics_api_client: TestClient,
    ) -> None:
        """JSON array preserves history.csv row order (oldest to newest)."""
        project_name = "eq-order"
        project_root = _materialise_project(metrics_kanban_root, project_name)
        versions = ["v1.0.0", "v1.1.0", "v1.2.0"]
        csv_rows = [
            {"rc": v, "tasks_total": str(i + 5)} for i, v in enumerate(versions)
        ]
        _write_history_csv(project_root, csv_rows)

        resp = metrics_api_client.get(
            "/metrics",
            params={"project": project_name, "format": "json"},
        )
        rows = resp.json()
        assert len(rows) == 3, f"Expected 3 rows; got {len(rows)}"
        for i, expected_version in enumerate(versions):
            assert rows[i]["version"] == expected_version, (
                f"Row {i}: version must be {expected_version!r}; got {rows[i]['version']!r}"
            )

    def test_metrics_json_last_param_limits_rows(
        self,
        metrics_kanban_root: pathlib.Path,
        metrics_api_client: TestClient,
    ) -> None:
        """?last=2 limits JSON output to the last 2 CSV rows."""
        project_name = "eq-last"
        project_root = _materialise_project(metrics_kanban_root, project_name)
        _write_history_csv(
            project_root,
            [
                {"rc": "v1.0.0", "tasks_total": "3"},
                {"rc": "v1.1.0", "tasks_total": "4"},
                {"rc": "v1.2.0", "tasks_total": "5"},
            ],
        )

        resp = metrics_api_client.get(
            "/metrics",
            params={"project": project_name, "format": "json", "last": 2},
        )
        rows = resp.json()
        assert len(rows) == 2, f"Expected 2 rows with last=2; got {len(rows)}"
        assert rows[0]["version"] == "v1.1.0", (
            f"First row with last=2 must be v1.1.0; got {rows[0]['version']!r}"
        )
        assert rows[1]["version"] == "v1.2.0", (
            f"Second row with last=2 must be v1.2.0; got {rows[1]['version']!r}"
        )

    def test_metrics_json_empty_csv_returns_empty_array(
        self,
        metrics_kanban_root: pathlib.Path,
        metrics_api_client: TestClient,
    ) -> None:
        """When history.csv is absent, /metrics?format=json returns an empty array."""
        project_name = "eq-empty"
        _materialise_project(metrics_kanban_root, project_name)
        # No history.csv written — project_root/metrics/ does not exist

        resp = metrics_api_client.get(
            "/metrics",
            params={"project": project_name, "format": "json"},
        )
        assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
        rows = resp.json()
        assert rows == [], f"Empty history must return []; got {rows!r}"


# ---------------------------------------------------------------------------
# TestCostsJsonEquality
# ---------------------------------------------------------------------------


class TestCostsJsonEquality:
    """Assert that /costs?format=json field values equal the RC usage file source values.

    Each test:
      1. Writes a known usage/rc/<version>-tokens.json fixture.
      2. Calls GET /costs?format=json&rc=<version>.
      3. Parses the JSON response.
      4. Asserts each row's field values match the fixture data field-by-field.
    """

    def test_costs_json_parses_as_json(
        self,
        metrics_kanban_root: pathlib.Path,
        metrics_api_client: TestClient,
    ) -> None:
        """GET /costs?format=json&rc=v1.0.0 returns a valid JSON array."""
        project_name = "costs-proj"
        project_root = _materialise_project(metrics_kanban_root, project_name)
        version = "v1.0.0"
        _write_rc_tokens_file(
            project_root,
            version,
            {
                "version": version,
                "totals": {
                    "input_tokens": 10000,
                    "output_tokens": 2000,
                    "cost_usd": 0.05,
                },
            },
        )

        resp = metrics_api_client.get(
            "/costs",
            params={"project": project_name, "rc": version, "format": "json"},
        )

        assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
        rows = resp.json()
        assert isinstance(rows, list), f"Response must be a JSON array; got {type(rows)}"

    def test_costs_json_version_equals_rc_tokens_version(
        self,
        metrics_kanban_root: pathlib.Path,
        metrics_api_client: TestClient,
    ) -> None:
        """JSON row 'version' equals the 'version' field in the RC tokens file."""
        project_name = "costs-ver"
        project_root = _materialise_project(metrics_kanban_root, project_name)
        version = "v2.0.0"
        _write_rc_tokens_file(
            project_root,
            version,
            {"version": version, "totals": {"input_tokens": 100, "cost_usd": 0.01}},
        )

        resp = metrics_api_client.get(
            "/costs",
            params={"project": project_name, "rc": version, "format": "json"},
        )
        rows = resp.json()
        assert len(rows) == 1, f"Expected 1 row for rc scope; got {len(rows)}"
        assert rows[0]["version"] == version, (
            f"row['version'] must be {version!r}; got {rows[0]['version']!r}"
        )

    def test_costs_json_tokens_in_equals_totals_input_tokens(
        self,
        metrics_kanban_root: pathlib.Path,
        metrics_api_client: TestClient,
    ) -> None:
        """JSON row 'tokens_in' equals totals.input_tokens from the RC usage file."""
        project_name = "costs-tin"
        project_root = _materialise_project(metrics_kanban_root, project_name)
        version = "v1.0.0"
        _write_rc_tokens_file(
            project_root,
            version,
            {"version": version, "totals": {"input_tokens": 999888, "cost_usd": 0.01}},
        )

        resp = metrics_api_client.get(
            "/costs",
            params={"project": project_name, "rc": version, "format": "json"},
        )
        rows = resp.json()
        assert rows[0]["tokens_in"] == 999888, (
            f"row['tokens_in'] must equal totals.input_tokens=999888; "
            f"got {rows[0].get('tokens_in')!r}"
        )

    def test_costs_json_tokens_out_equals_totals_output_tokens(
        self,
        metrics_kanban_root: pathlib.Path,
        metrics_api_client: TestClient,
    ) -> None:
        """JSON row 'tokens_out' equals totals.output_tokens from the RC usage file."""
        project_name = "costs-tout"
        project_root = _materialise_project(metrics_kanban_root, project_name)
        version = "v1.0.0"
        _write_rc_tokens_file(
            project_root,
            version,
            {"version": version, "totals": {"output_tokens": 123456, "cost_usd": 0.01}},
        )

        resp = metrics_api_client.get(
            "/costs",
            params={"project": project_name, "rc": version, "format": "json"},
        )
        rows = resp.json()
        assert rows[0]["tokens_out"] == 123456, (
            f"row['tokens_out'] must equal totals.output_tokens=123456; "
            f"got {rows[0].get('tokens_out')!r}"
        )

    def test_costs_json_est_cost_equals_totals_cost_usd(
        self,
        metrics_kanban_root: pathlib.Path,
        metrics_api_client: TestClient,
    ) -> None:
        """JSON row 'est_cost' equals totals.cost_usd from the RC usage file."""
        project_name = "costs-usd"
        project_root = _materialise_project(metrics_kanban_root, project_name)
        version = "v1.0.0"
        _write_rc_tokens_file(
            project_root,
            version,
            {"version": version, "totals": {"cost_usd": 7.891011}},
        )

        resp = metrics_api_client.get(
            "/costs",
            params={"project": project_name, "rc": version, "format": "json"},
        )
        rows = resp.json()
        assert rows[0]["est_cost"] == pytest.approx(7.891011, abs=0.000001), (
            f"row['est_cost'] must equal totals.cost_usd=7.891011; "
            f"got {rows[0].get('est_cost')!r}"
        )

    def test_costs_json_cache_read_pct_computed_from_totals(
        self,
        metrics_kanban_root: pathlib.Path,
        metrics_api_client: TestClient,
    ) -> None:
        """JSON row 'cache_read_pct' is cache_read / (input + cache_read) * 100.

        With input_tokens=2000 and cache_read_tokens=1000:
          pct = 1000 / (2000 + 1000) * 100 = 33.3...
        """
        project_name = "costs-cpct"
        project_root = _materialise_project(metrics_kanban_root, project_name)
        version = "v1.0.0"
        _write_rc_tokens_file(
            project_root,
            version,
            {
                "version": version,
                "totals": {
                    "input_tokens": 2000,
                    "cache_read_tokens": 1000,
                    "cost_usd": 0.01,
                },
            },
        )

        resp = metrics_api_client.get(
            "/costs",
            params={"project": project_name, "rc": version, "format": "json"},
        )
        rows = resp.json()
        expected_pct = round(1000 / (2000 + 1000) * 100, 1)
        assert rows[0]["cache_read_pct"] == pytest.approx(expected_pct, abs=0.05), (
            f"row['cache_read_pct'] must equal {expected_pct}; "
            f"got {rows[0].get('cache_read_pct')!r}"
        )

    def test_costs_json_missing_rc_file_returns_empty_array(
        self,
        metrics_kanban_root: pathlib.Path,
        metrics_api_client: TestClient,
    ) -> None:
        """When the RC usage file is absent, /costs?format=json&rc=v9.9.9 returns []."""
        project_name = "costs-nomatch"
        _materialise_project(metrics_kanban_root, project_name)
        # No RC tokens file written

        resp = metrics_api_client.get(
            "/costs",
            params={"project": project_name, "rc": "v9.9.9", "format": "json"},
        )
        assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
        rows = resp.json()
        assert rows == [], f"Missing RC file must return []; got {rows!r}"


# ---------------------------------------------------------------------------
# TestMetricsCostsTextRegression
# ---------------------------------------------------------------------------


class TestMetricsCostsTextRegression:
    """Assert that /metrics and /costs without format param return text envelopes.

    The text path must be byte-identical to the pre-v1.6.0 behavior: it calls the
    underlying shell scripts and returns the standard envelope.  The JSON branch
    must be fully gated on ``format=json``.
    """

    def test_metrics_no_format_returns_text_envelope(
        self,
        metrics_kanban_root: pathlib.Path,
        metrics_api_client: TestClient,
    ) -> None:
        """GET /metrics without format param returns the text envelope, not a JSON array."""
        project_name = "text-metrics"
        project_root = _materialise_project(metrics_kanban_root, project_name)
        _write_history_csv(
            project_root,
            [{"rc": "v1.0.0", "tasks_total": "3"}],
        )

        resp = metrics_api_client.get(
            "/metrics",
            params={"project": project_name},
        )

        assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
        body = resp.json()
        # Text envelope must have these keys; a bare JSON array would not.
        assert "exit_code" in body, (
            "Text path must return envelope with 'exit_code'; "
            f"body keys: {list(body.keys()) if isinstance(body, dict) else type(body)}"
        )
        assert "stdout" in body, (
            "Text path must return envelope with 'stdout'; "
            f"body keys: {list(body.keys()) if isinstance(body, dict) else type(body)}"
        )

    def test_costs_no_format_returns_text_envelope(
        self,
        metrics_kanban_root: pathlib.Path,
        metrics_api_client: TestClient,
    ) -> None:
        """GET /costs without format param returns the text envelope, not a JSON array."""
        project_name = "text-costs"
        _materialise_project(metrics_kanban_root, project_name)

        resp = metrics_api_client.get(
            "/costs",
            params={"project": project_name},
        )

        # cost-report.sh requires a project; if it exits non-zero the envelope
        # still has the expected keys — we just check the shape, not exit_code.
        body = resp.json()
        assert isinstance(body, dict), (
            "Text path must return a dict envelope; "
            f"got {type(body)}"
        )
        assert "exit_code" in body, (
            "Text path must return envelope with 'exit_code'; "
            f"body keys: {list(body.keys())}"
        )
        assert "stdout" in body, (
            "Text path must return envelope with 'stdout'; "
            f"body keys: {list(body.keys())}"
        )


# ---------------------------------------------------------------------------
# TestMissingFieldOmission
# ---------------------------------------------------------------------------


class TestMissingFieldOmission:
    """Assert that absent source fields are omitted from JSON rows, not null-filled."""

    def test_missing_wall_time_omits_wall_seconds(
        self,
        metrics_kanban_root: pathlib.Path,
        metrics_api_client: TestClient,
    ) -> None:
        """When wall_time_minutes is empty in history.csv, 'wall_seconds' must not appear."""
        project_name = "omit-wall"
        project_root = _materialise_project(metrics_kanban_root, project_name)
        _write_history_csv(
            project_root,
            [{"rc": "v1.0.0", "wall_time_minutes": ""}],  # empty — "not yet populated"
        )

        resp = metrics_api_client.get(
            "/metrics",
            params={"project": project_name, "format": "json"},
        )
        rows = resp.json()
        assert len(rows) == 1
        assert "wall_seconds" not in rows[0], (
            f"'wall_seconds' must be absent when wall_time_minutes is empty; "
            f"row: {rows[0]}"
        )

    def test_missing_est_cost_omits_est_cost(
        self,
        metrics_kanban_root: pathlib.Path,
        metrics_api_client: TestClient,
    ) -> None:
        """When no RC usage file exists for a version, 'est_cost' must not appear."""
        project_name = "omit-cost"
        project_root = _materialise_project(metrics_kanban_root, project_name)
        _write_history_csv(
            project_root,
            [{"rc": "v1.0.0", "tasks_total": "3"}],
        )
        # No RC tokens file — usage/rc/ does not exist

        resp = metrics_api_client.get(
            "/metrics",
            params={"project": project_name, "format": "json"},
        )
        rows = resp.json()
        assert len(rows) == 1
        assert "est_cost" not in rows[0], (
            f"'est_cost' must be absent when no RC usage file exists; row: {rows[0]}"
        )

    def test_zero_cache_read_omits_cache_read_pct(
        self,
        metrics_kanban_root: pathlib.Path,
        metrics_api_client: TestClient,
    ) -> None:
        """When cache_read_tokens=0 and input_tokens=0, 'cache_read_pct' must not appear."""
        project_name = "omit-pct"
        project_root = _materialise_project(metrics_kanban_root, project_name)
        _write_history_csv(
            project_root,
            [{"rc": "v1.0.0", "input_tokens": "0", "cache_read_tokens": "0"}],
        )

        resp = metrics_api_client.get(
            "/metrics",
            params={"project": project_name, "format": "json"},
        )
        rows = resp.json()
        assert len(rows) == 1
        assert "cache_read_pct" not in rows[0], (
            f"'cache_read_pct' must be absent when denominator is zero; row: {rows[0]}"
        )

    def test_costs_missing_cost_usd_omits_est_cost(
        self,
        metrics_kanban_root: pathlib.Path,
        metrics_api_client: TestClient,
    ) -> None:
        """When totals.cost_usd is 0 or absent, 'est_cost' must not appear in costs JSON."""
        project_name = "costs-omit"
        project_root = _materialise_project(metrics_kanban_root, project_name)
        version = "v1.0.0"
        _write_rc_tokens_file(
            project_root,
            version,
            {
                "version": version,
                "totals": {"input_tokens": 100, "cost_usd": 0},  # zero cost
            },
        )

        resp = metrics_api_client.get(
            "/costs",
            params={"project": project_name, "rc": version, "format": "json"},
        )
        rows = resp.json()
        assert len(rows) == 1
        assert "est_cost" not in rows[0], (
            f"'est_cost' must be absent when cost_usd is 0; row: {rows[0]}"
        )


# ---------------------------------------------------------------------------
# TestFormatJsonValidation
# ---------------------------------------------------------------------------


class TestFormatJsonValidation:
    """Assert validation requirements for format=json."""

    def test_metrics_format_json_without_project_returns_422(
        self,
        metrics_api_client: TestClient,
    ) -> None:
        """GET /metrics?format=json without project param returns 422."""
        resp = metrics_api_client.get(
            "/metrics",
            params={"format": "json"},
        )
        assert resp.status_code == 422, (
            f"Expected 422 when project is missing for format=json; "
            f"got HTTP {resp.status_code}"
        )

    def test_costs_format_json_without_project_returns_422(
        self,
        metrics_api_client: TestClient,
    ) -> None:
        """GET /costs?format=json without project param returns 422."""
        resp = metrics_api_client.get(
            "/costs",
            params={"format": "json"},
        )
        assert resp.status_code == 422, (
            f"Expected 422 when project is missing for format=json; "
            f"got HTTP {resp.status_code}"
        )

    def test_metrics_format_json_case_insensitive(
        self,
        metrics_kanban_root: pathlib.Path,
        metrics_api_client: TestClient,
    ) -> None:
        """GET /metrics?format=JSON (uppercase) is treated the same as format=json."""
        project_name = "fmt-case"
        project_root = _materialise_project(metrics_kanban_root, project_name)
        _write_history_csv(
            project_root,
            [{"rc": "v1.0.0", "tasks_total": "5"}],
        )

        resp = metrics_api_client.get(
            "/metrics",
            params={"project": project_name, "format": "JSON"},
        )
        assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
        rows = resp.json()
        assert isinstance(rows, list), f"format=JSON must return an array; got {type(rows)}"
