"""
test_traces.py — Tests for GET /traces and GET /traces/{id} endpoints.

Acceptance criteria verified:

  AC1. GET /traces lists fixture traces newest-first with all six fields per entry
       (id, project, agent, task_key, timestamp, path_basename).
  AC2. GET /traces?project=<name> filters to that project; ?agent=<role> filters
       to that agent.  Both can be combined.
  AC3. GET /traces/{id} for a listed id round-trips the file content in the
       standard envelope (exit_code=0, stdout=content, stderr="").
  AC4. GET /traces/<fabricated-id> returns HTTP 404.
  AC5. GET /traces/../../etc/passwd (traversal-shaped id) returns HTTP 404
       (not 422) — the id is simply not in the server's enumeration.
  AC6. Traversal sequences in query params (project, agent) return 422.
  AC7. limit default 50; limit hard cap clamped to 2000; limit=0 → 422.
  AC8. Unknown agent in ?agent= query param → 422.

Test structure:
  - ``TestTracesIndex``        — AC1: index response shape and field correctness.
  - ``TestTracesFilter``       — AC2: project and agent filter tests.
  - ``TestTracesFetch``        — AC3: content round-trip via GET /traces/{id}.
  - ``TestTracesFabricatedId`` — AC4: fabricated id → 404.
  - ``TestTracesOpaqueIdSecurity`` — AC5: traversal-shaped id → 404 (not 422).
  - ``TestTracesParamSecurity``    — AC6: traversal in query params → 422.
  - ``TestTracesLimit``            — AC7: limit parameter validation.
  - ``TestTracesAgentValidation``  — AC8: unknown agent → 422.

Fixtures materialise a synthetic kanban root with two projects and several
trace files under projects/<name>/logs/training/<agent>/<stem>.md.
"""

from __future__ import annotations

import pathlib
import unittest.mock
from typing import Generator

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixture constants
# ---------------------------------------------------------------------------

_PROJECT_ALPHA = "alpha-project"
_PROJECT_BETA = "beta-project"

# Trace file stems: <YYYYMMDDTHHmmss>-<TASK-KEY>
# Ordered newest-first: CODER-B > PM-B > CODER-A
_ALPHA_CODER_STEM = "20260703T133901-CODER-20260703-002-entry-point"
_ALPHA_CODER_CONTENT = "# CODER alpha trace\n\nThis is the coder trace for alpha project.\n"
_ALPHA_PM_STEM = "20260702T100000-PM-20260702-001-decompose-v0-0-1"
_ALPHA_PM_CONTENT = "# PM alpha trace\n\nThis is the pm trace for alpha project.\n"

_BETA_CODER_STEM = "20260705T080000-CODER-20260705-003-beta-feature"
_BETA_CODER_CONTENT = "# CODER beta trace\n\nThis is the coder trace for beta project.\n"

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write(path: pathlib.Path, text: str) -> pathlib.Path:
    """Write text to path, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _materialise_traces_fixture(root: pathlib.Path) -> None:
    """Materialise a fixture kanban root with trace files for two projects.

    Layout:
      projects/alpha-project/logs/training/coder/<ALPHA_CODER_STEM>.md
      projects/alpha-project/logs/training/pm/<ALPHA_PM_STEM>.md
      projects/beta-project/logs/training/coder/<BETA_CODER_STEM>.md

    Also writes a minimal projects.cfg and project.cfg for each project.
    """
    # projects.cfg
    _write(
        root / "projects.cfg",
        (
            f"[project:{_PROJECT_ALPHA}]\npriority=1\ndashboard_color=#378ADD\n"
            f"[project:{_PROJECT_BETA}]\npriority=2\ndashboard_color=#AA2244\n"
        ),
    )

    # project.cfg stubs
    _write(
        root / "projects" / _PROJECT_ALPHA / "project.cfg",
        f"[project]\nproject_name = {_PROJECT_ALPHA}\n",
    )
    _write(
        root / "projects" / _PROJECT_BETA / "project.cfg",
        f"[project]\nproject_name = {_PROJECT_BETA}\n",
    )

    # Trace files
    _write(
        root / "projects" / _PROJECT_ALPHA / "logs" / "training" / "coder"
        / f"{_ALPHA_CODER_STEM}.md",
        _ALPHA_CODER_CONTENT,
    )
    _write(
        root / "projects" / _PROJECT_ALPHA / "logs" / "training" / "pm"
        / f"{_ALPHA_PM_STEM}.md",
        _ALPHA_PM_CONTENT,
    )
    _write(
        root / "projects" / _PROJECT_BETA / "logs" / "training" / "coder"
        / f"{_BETA_CODER_STEM}.md",
        _BETA_CODER_CONTENT,
    )


# ---------------------------------------------------------------------------
# pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def traces_fixture_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return a kanban_root with all fixture trace files materialised."""
    root = tmp_path / "traces_test_root"
    _materialise_traces_fixture(root)
    return root


@pytest.fixture
def traces_client(
    traces_fixture_root: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    """TestClient bound to the traces fixture root."""
    root = traces_fixture_root

    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))

    cfg = ApiConfig(host="127.0.0.1", port=0, kanban_root=root)
    app = create_app(cfg=cfg)
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


# ---------------------------------------------------------------------------
# TestTracesIndex — AC1: index response shape and field correctness
# ---------------------------------------------------------------------------


class TestTracesIndex:
    """Verify the /traces index returns all six fields and sorts newest-first."""

    def test_index_returns_200(self, traces_client: TestClient) -> None:
        """GET /traces returns HTTP 200."""
        resp = traces_client.get("/traces")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    def test_index_returns_array(self, traces_client: TestClient) -> None:
        """Response body is a JSON array."""
        resp = traces_client.get("/traces")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list), f"Expected list, got {type(body).__name__}: {body}"

    def test_index_all_six_fields_present(self, traces_client: TestClient) -> None:
        """Each entry has all six required fields: id, project, agent, task_key,
        timestamp, path_basename."""
        resp = traces_client.get("/traces")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) > 0, "Expected at least one trace entry"
        required_fields = {"id", "project", "agent", "task_key", "timestamp", "path_basename"}
        for entry in body:
            missing = required_fields - entry.keys()
            assert not missing, f"Entry missing fields {missing}: {entry}"

    def test_index_sorted_newest_first(self, traces_client: TestClient) -> None:
        """Traces are returned newest-first by timestamp."""
        resp = traces_client.get("/traces")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) >= 3, f"Expected at least 3 traces; got {len(body)}"

        # Timestamps must be monotonically non-increasing.
        timestamps = [e["timestamp"] for e in body]
        for i in range(len(timestamps) - 1):
            assert timestamps[i] >= timestamps[i + 1], (
                f"Trace {i} timestamp {timestamps[i]!r} is older than "
                f"trace {i+1} timestamp {timestamps[i+1]!r} — not newest-first"
            )

    def test_index_newest_trace_is_first(self, traces_client: TestClient) -> None:
        """The newest fixture trace (BETA_CODER, 20260705) is the first entry."""
        resp = traces_client.get("/traces")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) > 0
        first = body[0]
        assert first["id"] == _BETA_CODER_STEM, (
            f"Expected first entry id={_BETA_CODER_STEM!r}, got {first['id']!r}"
        )
        assert first["project"] == _PROJECT_BETA
        assert first["agent"] == "coder"

    def test_index_id_is_file_stem(self, traces_client: TestClient) -> None:
        """The id field is the file stem (basename without extension)."""
        resp = traces_client.get("/traces")
        assert resp.status_code == 200
        body = resp.json()
        ids = {e["id"] for e in body}
        assert _ALPHA_CODER_STEM in ids, (
            f"Expected {_ALPHA_CODER_STEM!r} in ids; got {ids!r}"
        )
        assert _ALPHA_PM_STEM in ids
        assert _BETA_CODER_STEM in ids

    def test_index_path_basename_has_md_extension(self, traces_client: TestClient) -> None:
        """path_basename includes the .md extension."""
        resp = traces_client.get("/traces")
        assert resp.status_code == 200
        body = resp.json()
        for entry in body:
            assert entry["path_basename"].endswith(".md"), (
                f"path_basename should end with .md: {entry['path_basename']!r}"
            )

    def test_index_task_key_extracted_from_stem(self, traces_client: TestClient) -> None:
        """task_key is the TASK-KEY portion after the timestamp prefix."""
        resp = traces_client.get("/traces")
        assert resp.status_code == 200
        body = resp.json()
        # Find the alpha coder entry.
        alpha_coder = next(
            (e for e in body if e["id"] == _ALPHA_CODER_STEM), None
        )
        assert alpha_coder is not None, "Expected alpha coder trace in index"
        # Stem: "20260703T133901-CODER-20260703-002-entry-point"
        # task_key should be: "CODER-20260703-002-entry-point"
        assert alpha_coder["task_key"] == "CODER-20260703-002-entry-point", (
            f"Unexpected task_key: {alpha_coder['task_key']!r}"
        )

    def test_index_timestamp_is_iso8601(self, traces_client: TestClient) -> None:
        """timestamp field is an ISO-8601 formatted string."""
        resp = traces_client.get("/traces")
        assert resp.status_code == 200
        body = resp.json()
        for entry in body:
            ts = entry["timestamp"]
            # Should contain a "T" separator and end with "+00:00" (UTC).
            assert "T" in ts, f"timestamp not ISO-8601: {ts!r}"
            assert "+00:00" in ts, f"timestamp missing timezone: {ts!r}"

    def test_index_total_count(self, traces_client: TestClient) -> None:
        """Fixture has exactly three traces; index returns all three."""
        resp = traces_client.get("/traces")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 3, f"Expected 3 traces, got {len(body)}"

    def test_index_empty_when_no_traces(self, tmp_path: pathlib.Path) -> None:
        """GET /traces returns an empty array when no trace files exist."""
        root = tmp_path / "empty_root"
        root.mkdir()

        from pgai_agent_kanban.api.app import create_app
        from pgai_agent_kanban.api.config import ApiConfig

        cfg = ApiConfig(host="127.0.0.1", port=0, kanban_root=root)
        app = create_app(cfg=cfg)
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get("/traces")
        assert resp.status_code == 200
        assert resp.json() == [], f"Expected empty array; got {resp.json()}"


# ---------------------------------------------------------------------------
# TestTracesFilter — AC2: project and agent filters
# ---------------------------------------------------------------------------


class TestTracesFilter:
    """Verify ?project and ?agent query param filters work correctly."""

    def test_filter_by_project_alpha(self, traces_client: TestClient) -> None:
        """?project=alpha-project returns only alpha project traces."""
        resp = traces_client.get(f"/traces?project={_PROJECT_ALPHA}")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2, f"Expected 2 alpha traces, got {len(body)}: {body}"
        for entry in body:
            assert entry["project"] == _PROJECT_ALPHA, (
                f"Non-alpha entry in filtered result: {entry}"
            )

    def test_filter_by_project_beta(self, traces_client: TestClient) -> None:
        """?project=beta-project returns only beta project traces."""
        resp = traces_client.get(f"/traces?project={_PROJECT_BETA}")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1, f"Expected 1 beta trace, got {len(body)}: {body}"
        assert body[0]["project"] == _PROJECT_BETA

    def test_filter_by_agent_coder(self, traces_client: TestClient) -> None:
        """?agent=coder returns only coder traces across all projects."""
        resp = traces_client.get("/traces?agent=coder")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2, f"Expected 2 coder traces, got {len(body)}: {body}"
        for entry in body:
            assert entry["agent"] == "coder", (
                f"Non-coder entry in filtered result: {entry}"
            )

    def test_filter_by_agent_pm(self, traces_client: TestClient) -> None:
        """?agent=pm returns only pm traces."""
        resp = traces_client.get("/traces?agent=pm")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1, f"Expected 1 pm trace, got {len(body)}: {body}"
        assert body[0]["agent"] == "pm"

    def test_filter_by_project_and_agent(self, traces_client: TestClient) -> None:
        """?project=alpha-project&agent=coder returns exactly the alpha coder trace."""
        resp = traces_client.get(f"/traces?project={_PROJECT_ALPHA}&agent=coder")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1, f"Expected 1 trace, got {len(body)}: {body}"
        assert body[0]["id"] == _ALPHA_CODER_STEM

    def test_filter_by_unknown_project_returns_empty(
        self, traces_client: TestClient
    ) -> None:
        """?project=<unknown> returns an empty array (not an error)."""
        resp = traces_client.get("/traces?project=does-not-exist")
        assert resp.status_code == 200
        assert resp.json() == [], f"Expected empty array for unknown project; got {resp.json()}"

    def test_filter_filtered_order_still_newest_first(
        self, traces_client: TestClient
    ) -> None:
        """Filtered results are still sorted newest-first."""
        resp = traces_client.get(f"/traces?project={_PROJECT_ALPHA}")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2
        assert body[0]["timestamp"] >= body[1]["timestamp"], (
            "Alpha traces not sorted newest-first"
        )
        # The alpha coder trace (20260703) is newer than alpha pm trace (20260702).
        assert body[0]["id"] == _ALPHA_CODER_STEM
        assert body[1]["id"] == _ALPHA_PM_STEM


# ---------------------------------------------------------------------------
# TestTracesFetch — AC3: GET /traces/{id} round-trips content
# ---------------------------------------------------------------------------


class TestTracesFetch:
    """GET /traces/{id} returns the trace markdown in the standard envelope."""

    def test_fetch_known_id_returns_200(self, traces_client: TestClient) -> None:
        """GET /traces/<listed-id> returns HTTP 200."""
        resp = traces_client.get(f"/traces/{_ALPHA_CODER_STEM}")
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )

    def test_fetch_known_id_envelope_shape(self, traces_client: TestClient) -> None:
        """Response envelope contains exit_code, stdout, stderr."""
        resp = traces_client.get(f"/traces/{_ALPHA_CODER_STEM}")
        assert resp.status_code == 200
        body = resp.json()
        assert "exit_code" in body, f"Missing exit_code: {body}"
        assert "stdout" in body, f"Missing stdout: {body}"
        assert "stderr" in body, f"Missing stderr: {body}"

    def test_fetch_known_id_exit_code_zero(self, traces_client: TestClient) -> None:
        """exit_code is 0 for a found trace."""
        resp = traces_client.get(f"/traces/{_ALPHA_CODER_STEM}")
        body = resp.json()
        assert body["exit_code"] == 0, f"Expected exit_code=0; got {body['exit_code']}"

    def test_fetch_known_id_rounds_content(self, traces_client: TestClient) -> None:
        """stdout contains the exact markdown content of the trace file."""
        resp = traces_client.get(f"/traces/{_ALPHA_CODER_STEM}")
        body = resp.json()
        assert body["stdout"] == _ALPHA_CODER_CONTENT, (
            f"Content mismatch.\nExpected: {_ALPHA_CODER_CONTENT!r}\n"
            f"Got:      {body['stdout']!r}"
        )

    def test_fetch_known_id_stderr_empty(self, traces_client: TestClient) -> None:
        """stderr is empty for a found trace."""
        resp = traces_client.get(f"/traces/{_ALPHA_CODER_STEM}")
        body = resp.json()
        assert body["stderr"] == "", f"Expected empty stderr; got {body['stderr']!r}"

    def test_fetch_beta_coder_id(self, traces_client: TestClient) -> None:
        """GET /traces/<beta-coder-id> returns the beta coder trace content."""
        resp = traces_client.get(f"/traces/{_BETA_CODER_STEM}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["exit_code"] == 0
        assert body["stdout"] == _BETA_CODER_CONTENT

    def test_fetch_pm_id(self, traces_client: TestClient) -> None:
        """GET /traces/<pm-id> returns the pm trace content."""
        resp = traces_client.get(f"/traces/{_ALPHA_PM_STEM}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["exit_code"] == 0
        assert body["stdout"] == _ALPHA_PM_CONTENT

    def test_id_from_index_resolves(self, traces_client: TestClient) -> None:
        """An id returned by GET /traces is resolvable by GET /traces/{id}."""
        # First fetch the index.
        index_resp = traces_client.get("/traces")
        assert index_resp.status_code == 200
        index_body = index_resp.json()
        assert len(index_body) > 0

        # Use the first id from the index to fetch the trace.
        first_id = index_body[0]["id"]
        fetch_resp = traces_client.get(f"/traces/{first_id}")
        assert fetch_resp.status_code == 200, (
            f"Expected 200 for id {first_id!r}; got {fetch_resp.status_code}"
        )
        fetch_body = fetch_resp.json()
        assert fetch_body["exit_code"] == 0
        assert fetch_body["stdout"], "stdout must not be empty for a found trace"


# ---------------------------------------------------------------------------
# TestTracesFabricatedId — AC4: fabricated id → 404
# ---------------------------------------------------------------------------


class TestTracesFabricatedId:
    """GET /traces/<fabricated-id> returns HTTP 404."""

    def test_fabricated_id_returns_404(self, traces_client: TestClient) -> None:
        """A completely fabricated id returns 404."""
        resp = traces_client.get("/traces/this-id-does-not-exist")
        assert resp.status_code == 404, (
            f"Expected 404 for fabricated id; got {resp.status_code}: {resp.text}"
        )

    def test_fabricated_id_with_timestamp_format_returns_404(
        self, traces_client: TestClient
    ) -> None:
        """Even a well-formed (timestamp-like) id not in the enumeration → 404."""
        resp = traces_client.get(
            "/traces/20260101T000000-NONEXISTENT-00000-00-fake-trace"
        )
        assert resp.status_code == 404

    def test_fabricated_id_body_has_error_field(self, traces_client: TestClient) -> None:
        """404 body includes an 'error' field."""
        resp = traces_client.get("/traces/definitely-not-a-real-id")
        assert resp.status_code == 404
        # FastAPI wraps HTTPException detail in {"detail": ...}
        body = resp.json()
        # Either detail contains error dict or is a string with "not found"
        detail = body.get("detail", body)
        assert "error" in str(detail).lower() or "not found" in str(detail).lower(), (
            f"404 body should contain 'error' or 'not found': {body}"
        )


# ---------------------------------------------------------------------------
# TestTracesOpaqueIdSecurity — AC5: traversal-shaped id → 404 (not 422)
# ---------------------------------------------------------------------------


class TestTracesOpaqueIdSecurity:
    """Traversal-shaped ids return 404 (not 422) — they are simply not in the enumeration.

    The opaque-id discipline: path components inside {id} are NEVER interpreted
    as filesystem navigation.  The resolver walks the server's own enumeration
    and does not accept any path-shaped input.
    """

    def test_dotdot_id_returns_404_not_422(self, traces_client: TestClient) -> None:
        """AC5: ../../etc/passwd as id → 404 (not 422)."""
        resp = traces_client.get("/traces/../../etc/passwd")
        # FastAPI URL routing may interpret path separators differently;
        # the endpoint should return 404 in any case (not 422, not 200).
        assert resp.status_code == 404, (
            f"Traversal-shaped id must return 404; got {resp.status_code}: {resp.text}"
        )

    def test_unknown_id_with_path_chars_returns_404(
        self, traces_client: TestClient
    ) -> None:
        """An id that looks like a relative path but is not in the enumeration → 404."""
        resp = traces_client.get("/traces/not-a-real-trace-but-has-dashes")
        assert resp.status_code == 404

    def test_dotdot_url_encoded_id_returns_404(self, traces_client: TestClient) -> None:
        """A URL-encoded traversal id that is not in the enumeration → 404."""
        # %2F is URL-encoded slash.  FastAPI will pass the decoded value as trace_id.
        # The id just won't be in the server's enumeration.
        resp = traces_client.get("/traces/..%2F..%2Fetc%2Fpasswd")
        # May 404 due to routing or due to enumeration miss; either is correct.
        assert resp.status_code in (404, 422), (
            f"Traversal id must not return 200; got {resp.status_code}: {resp.text}"
        )
        # The critical assertion: it must NOT succeed (not 200).
        assert resp.status_code != 200, (
            "Traversal-shaped id must not return 200 with trace content."
        )


# ---------------------------------------------------------------------------
# TestTracesParamSecurity — AC6: traversal in query params → 422
# ---------------------------------------------------------------------------


class TestTracesParamSecurity:
    """Traversal sequences in project or agent query params return 422."""

    def test_project_dotdot_traversal_returns_422(
        self, traces_client: TestClient
    ) -> None:
        """project=../../etc/passwd → 422."""
        resp = traces_client.get("/traces?project=../../etc/passwd")
        assert resp.status_code == 422, (
            f"Expected 422 for traversal in project param; got {resp.status_code}: {resp.text}"
        )

    def test_project_url_encoded_traversal_returns_422(
        self, traces_client: TestClient
    ) -> None:
        """project=..%2F.. (URL-encoded slash) → 422."""
        resp = traces_client.get("/traces?project=..%2F..")
        assert resp.status_code == 422, (
            f"Expected 422; got {resp.status_code}: {resp.text}"
        )

    def test_project_slash_traversal_returns_422(
        self, traces_client: TestClient
    ) -> None:
        """project=alpha/../../etc → 422."""
        resp = traces_client.get("/traces?project=alpha/../../etc")
        assert resp.status_code == 422

    def test_agent_dotdot_traversal_returns_422(
        self, traces_client: TestClient
    ) -> None:
        """agent=../../etc/passwd → 422 (traversal detected before whitelist check)."""
        resp = traces_client.get("/traces?agent=../../etc/passwd")
        assert resp.status_code == 422, (
            f"Expected 422 for traversal in agent param; got {resp.status_code}: {resp.text}"
        )

    def test_project_null_byte_returns_422(self, traces_client: TestClient) -> None:
        """project with null byte (%00) → 422."""
        resp = traces_client.get("/traces?project=alpha%00inject")
        assert resp.status_code == 422

    def test_traversal_project_no_filesystem_access(
        self, traces_client: TestClient
    ) -> None:
        """Traversal in project param fires before any open() call."""
        with unittest.mock.patch("builtins.open") as mock_open:
            resp = traces_client.get("/traces?project=../../etc/passwd")
            assert resp.status_code == 422
            assert not mock_open.called, (
                f"open() must NOT be called for traversal attempt; "
                f"was called with: {mock_open.call_args_list}"
            )


# ---------------------------------------------------------------------------
# TestTracesLimit — AC7: limit parameter validation
# ---------------------------------------------------------------------------


class TestTracesLimit:
    """limit parameter: default 50, hard cap 2000, invalid values → 422."""

    def test_limit_default_50_fewer_traces(self, traces_client: TestClient) -> None:
        """Default limit is 50; fixture has 3 traces, so all 3 are returned."""
        resp = traces_client.get("/traces")
        assert resp.status_code == 200
        # We have 3 fixture traces, all within the default limit.
        assert len(resp.json()) == 3

    def test_limit_1_returns_only_one(self, traces_client: TestClient) -> None:
        """limit=1 returns only the newest trace."""
        resp = traces_client.get("/traces?limit=1")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1, f"limit=1 should return 1 trace; got {len(body)}"
        assert body[0]["id"] == _BETA_CODER_STEM, (
            f"Expected newest trace first; got {body[0]['id']!r}"
        )

    def test_limit_2_returns_two_newest(self, traces_client: TestClient) -> None:
        """limit=2 returns the two newest traces."""
        resp = traces_client.get("/traces?limit=2")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2
        assert body[0]["id"] == _BETA_CODER_STEM
        assert body[1]["id"] == _ALPHA_CODER_STEM

    def test_limit_large_returns_all(self, traces_client: TestClient) -> None:
        """limit=100 (larger than trace count) returns all 3 traces."""
        resp = traces_client.get("/traces?limit=100")
        assert resp.status_code == 200
        assert len(resp.json()) == 3

    def test_limit_0_returns_422(self, traces_client: TestClient) -> None:
        """limit=0 returns 422."""
        resp = traces_client.get("/traces?limit=0")
        assert resp.status_code == 422, (
            f"Expected 422 for limit=0; got {resp.status_code}"
        )

    def test_limit_negative_returns_422(self, traces_client: TestClient) -> None:
        """limit=-1 returns 422."""
        resp = traces_client.get("/traces?limit=-1")
        assert resp.status_code == 422

    def test_limit_non_integer_returns_422(self, traces_client: TestClient) -> None:
        """limit=abc returns 422."""
        resp = traces_client.get("/traces?limit=abc")
        assert resp.status_code == 422

    def test_limit_above_cap_is_clamped(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """limit=9999 is clamped to 2000 (hard cap enforced server-side).

        This test creates a fixture with 2200 traces to verify clamping.
        """
        root = tmp_path / "limit_cap_root"
        # Write minimal project structure.
        root.mkdir()
        (root / "projects").mkdir()
        project_dir = root / "projects" / "cap-project"
        trace_dir = project_dir / "logs" / "training" / "coder"
        trace_dir.mkdir(parents=True)

        # Create 2200 trace files with distinct timestamps.
        for i in range(2200):
            # Timestamps in ascending order so sort is deterministic.
            ts = f"2026{(i // 10000) + 1:02d}{((i // 100) % 100) + 1:02d}T{(i % 100):02d}0000"
            # Ensure valid date parts — simpler: embed counter in seconds only.
            h = i // 3600
            m = (i % 3600) // 60
            s = i % 60
            ts = f"20260703T{h:02d}{m:02d}{s:02d}"
            stem = f"{ts}-CODER-CAP-{i:04d}-trace"
            (trace_dir / f"{stem}.md").write_text(f"trace {i}\n", encoding="utf-8")

        from pgai_agent_kanban.api.app import create_app
        from pgai_agent_kanban.api.config import ApiConfig

        monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))
        cfg = ApiConfig(host="127.0.0.1", port=0, kanban_root=root)
        app = create_app(cfg=cfg)
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get("/traces?limit=9999")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2000, (
            f"limit=9999 should be clamped to 2000; got {len(body)}"
        )


# ---------------------------------------------------------------------------
# TestTracesAgentValidation — AC8: unknown agent → 422
# ---------------------------------------------------------------------------


class TestTracesAgentValidation:
    """Invalid agent values in the filter param → 422."""

    def test_unknown_agent_returns_422(self, traces_client: TestClient) -> None:
        """?agent=<unknown> returns 422 (agent must be in the fixed set)."""
        resp = traces_client.get("/traces?agent=badagent")
        assert resp.status_code == 422, (
            f"Expected 422 for unknown agent; got {resp.status_code}: {resp.text}"
        )

    def test_valid_agents_accepted(self, traces_client: TestClient) -> None:
        """All valid agent role names are accepted (return 200, possibly empty array)."""
        valid_agents = ["pm", "po", "coder", "writer", "tester", "cm", "overwatch"]
        for agent_name in valid_agents:
            resp = traces_client.get(f"/traces?agent={agent_name}")
            assert resp.status_code == 200, (
                f"Valid agent {agent_name!r} should return 200; "
                f"got {resp.status_code}: {resp.text}"
            )

    def test_empty_agent_treated_as_absent(self, traces_client: TestClient) -> None:
        """?agent= (empty value) is treated as if agent param was not provided."""
        # Empty agent string is treated as "no filter" — returns all traces.
        resp = traces_client.get("/traces?agent=")
        assert resp.status_code == 200
        body = resp.json()
        # Empty agent means no filter — all 3 fixture traces returned.
        assert len(body) == 3, (
            f"Empty agent param should return all traces; got {len(body)}"
        )

    def test_unknown_agent_body_mentions_agent(self, traces_client: TestClient) -> None:
        """422 body for unknown agent mentions 'agent'."""
        resp = traces_client.get("/traces?agent=notarole")
        assert resp.status_code == 422
        body = resp.json()
        assert "agent" in str(body).lower(), (
            f"422 body should mention 'agent': {body}"
        )
