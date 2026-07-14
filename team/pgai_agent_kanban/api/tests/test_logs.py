"""
test_logs.py — Tests for GET /logs/{kind} log-tail endpoint.

Acceptance criteria verified:

  1. Each kind returns its fixture file's tail with the correct payload in the envelope.
  2. Each kind with a missing required param returns 422 with a body naming the missing param.
  3. GET /logs/agent?project=../../etc/passwd&agent=coder returns 422; a file-open spy
     verifies open() is never invoked in that request path.
  4. GET /logs/debug?project=..%2F..&agent=pm returns 422 (URL-decoded traversal also blocked).
  5. GET /logs/agent?project=<valid>&agent=<invalid> returns 422 (agent validated against
     the fixed set).
  6. tail=5000 is clamped to 2000; tail=0 → 422.
  7. Unknown kind → 404 with kind echoed in the body.
  8. CORS: /logs/{kind} inherits the loopback-origin policy (inherits from app middleware).

Test structure:
  - ``TestLogTailKinds``          — per-kind happy-path and missing-param tests.
  - ``TestTraversalConfinement``  — traversal negative tests with open() spy.
  - ``TestTailParameter``         — tail validation tests (clamp, 422 on invalid).
  - ``TestUnknownKind``           — 404 for unknown kind.
  - ``TestAgentValidation``       — invalid agent rejected with 422.

Fixtures are materialised under pytest's tmp_path (no live kanban state).
The open() spy uses unittest.mock.patch("builtins.open") to assert that
filesystem access is never reached when a traversal attempt is made.
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

_PROJECT_NAME = "logs-test-project"

# Sample log content for fixture files.
_WAKE_LOG_CONTENT = "2026-07-07T10:00:00 [pm] wake: start\n2026-07-07T10:00:01 [pm] task dispatched\n"
_CM_LOG_CONTENT = "2026-07-07T09:00:00 [cm] wake: start\n2026-07-07T09:01:00 [cm] release shipped\n"
_AGENT_LOG_CONTENT = "2026-07-07T08:00:00 [coder] task start\n2026-07-07T08:10:00 [coder] task done\n"
_DEBUG_LOG_CONTENT = "2026-07-07T07:00:00 [coder] debug: reading file\n2026-07-07T07:00:01 [coder] debug: done\n"
_OVERWATCH_LOG_CONTENT = "2026-07-07T06:00:00 sweep-start project=logs-test-project\n2026-07-07T06:00:05 sweep-end\n"
_API_SERVER_LOG_CONTENT = "INFO:     Started server process\nINFO:     Application startup complete.\n"

# A multi-line log for testing tail truncation.
_LONG_LOG_LINES = [f"line {i}" for i in range(1, 2201)]  # 2200 lines
_LONG_LOG_CONTENT = "\n".join(_LONG_LOG_LINES) + "\n"


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write(path: pathlib.Path, text: str) -> pathlib.Path:
    """Write text to path, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _materialise_logs_fixture(root: pathlib.Path, temp_root: pathlib.Path) -> None:
    """Materialise a fixture kanban root with log files for all kinds.

    Project ``logs-test-project`` is registered in projects.cfg.
    Log files are placed at the paths the confinement table resolves to.
    """
    # projects.cfg
    _write(
        root / "projects.cfg",
        f"[project:{_PROJECT_NAME}]\npriority=1\ndashboard_color=#378ADD\n",
    )

    # project.cfg (minimal — just enough for the project to be registered)
    _write(
        root / "projects" / _PROJECT_NAME / "project.cfg",
        f"[project]\nproject_name = {_PROJECT_NAME}\n",
    )

    # kind=wake: <kanban_root>/logs/cron-pm.log
    _write(root / "logs" / "cron-pm.log", _WAKE_LOG_CONTENT)

    # kind=cm: <kanban_root>/logs/cron-cm.log
    _write(root / "logs" / "cron-cm.log", _CM_LOG_CONTENT)

    # kind=agent: <kanban_root>/projects/<project>/logs/agents/coder.log
    _write(
        root / "projects" / _PROJECT_NAME / "logs" / "agents" / "coder.log",
        _AGENT_LOG_CONTENT,
    )

    # kind=debug: <kanban_root>/projects/<project>/logs/debug/coder.log
    _write(
        root / "projects" / _PROJECT_NAME / "logs" / "debug" / "coder.log",
        _DEBUG_LOG_CONTENT,
    )

    # kind=overwatch (project): <kanban_root>/projects/<project>/logs/overwatch/sweep.log
    _write(
        root / "projects" / _PROJECT_NAME / "logs" / "overwatch" / "sweep.log",
        _OVERWATCH_LOG_CONTENT,
    )

    # kind=overwatch (global): <kanban_root>/logs/overwatch.log
    _write(root / "logs" / "overwatch.log", "global overwatch entry\n")

    # kind=api-server: <temp_root>/api/api-server.log
    _write(temp_root / "api" / "api-server.log", _API_SERVER_LOG_CONTENT)

    # Long log for tail-truncation tests.
    _write(root / "logs" / "cron-coder.log", _LONG_LOG_CONTENT)


# ---------------------------------------------------------------------------
# pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logs_fixture_root(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """Return (kanban_root, temp_root) with all fixture log files materialised."""
    root = tmp_path / "logs_test_root"
    temp_root = tmp_path / "logs_test_temp"
    _materialise_logs_fixture(root, temp_root)
    return root, temp_root


@pytest.fixture
def logs_client(
    logs_fixture_root: tuple[pathlib.Path, pathlib.Path],
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    """TestClient bound to the logs fixture root."""
    root, temp_root = logs_fixture_root

    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(temp_root))

    cfg = ApiConfig(host="127.0.0.1", port=0, kanban_root=root)
    app = create_app(cfg=cfg)
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


# ---------------------------------------------------------------------------
# TestLogTailKinds — happy path and missing-param tests per kind
# ---------------------------------------------------------------------------


class TestLogTailKinds:
    """Per-kind acceptance tests: fixture content returned; missing params → 422."""

    # -----------------------------------------------------------------------
    # kind=wake
    # -----------------------------------------------------------------------

    def test_wake_returns_200_with_content(self, logs_client: TestClient) -> None:
        """GET /logs/wake?agent=pm returns 200 with fixture content in stdout."""
        resp = logs_client.get("/logs/wake?agent=pm")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["exit_code"] == 0
        assert "wake" in body["stdout"], f"Expected wake content in stdout: {body['stdout']!r}"

    def test_wake_missing_agent_returns_422(self, logs_client: TestClient) -> None:
        """GET /logs/wake (no agent param) returns 422 naming 'agent'."""
        resp = logs_client.get("/logs/wake")
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert "agent" in str(body).lower(), (
            f"422 body must name the missing param 'agent': {body}"
        )

    # -----------------------------------------------------------------------
    # kind=cm
    # -----------------------------------------------------------------------

    def test_cm_returns_200_with_content(self, logs_client: TestClient) -> None:
        """GET /logs/cm returns 200 with fixture content in stdout."""
        resp = logs_client.get("/logs/cm")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["exit_code"] == 0
        assert "release shipped" in body["stdout"], (
            f"Expected cm log content in stdout: {body['stdout']!r}"
        )

    def test_cm_no_params_required(self, logs_client: TestClient) -> None:
        """GET /logs/cm requires no params; extra params are ignored."""
        resp = logs_client.get("/logs/cm?agent=pm&project=whatever")
        # Extra params for cm kind are ignored; the endpoint still returns 200.
        assert resp.status_code == 200

    # -----------------------------------------------------------------------
    # kind=agent
    # -----------------------------------------------------------------------

    def test_agent_returns_200_with_content(self, logs_client: TestClient) -> None:
        """GET /logs/agent?project=<valid>&agent=coder returns 200 with content."""
        resp = logs_client.get(f"/logs/agent?project={_PROJECT_NAME}&agent=coder")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["exit_code"] == 0
        assert "task start" in body["stdout"], (
            f"Expected agent log content in stdout: {body['stdout']!r}"
        )

    def test_agent_missing_project_returns_422(self, logs_client: TestClient) -> None:
        """GET /logs/agent?agent=coder (no project) returns 422 naming 'project'."""
        resp = logs_client.get("/logs/agent?agent=coder")
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert "project" in str(body).lower(), (
            f"422 body must name the missing param 'project': {body}"
        )

    def test_agent_missing_agent_returns_422(self, logs_client: TestClient) -> None:
        """GET /logs/agent?project=<valid> (no agent) returns 422 naming 'agent'."""
        resp = logs_client.get(f"/logs/agent?project={_PROJECT_NAME}")
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert "agent" in str(body).lower(), (
            f"422 body must name the missing param 'agent': {body}"
        )

    # -----------------------------------------------------------------------
    # kind=debug
    # -----------------------------------------------------------------------

    def test_debug_returns_200_with_content(self, logs_client: TestClient) -> None:
        """GET /logs/debug?project=<valid>&agent=coder returns 200 with content."""
        resp = logs_client.get(f"/logs/debug?project={_PROJECT_NAME}&agent=coder")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["exit_code"] == 0
        assert "reading file" in body["stdout"], (
            f"Expected debug log content in stdout: {body['stdout']!r}"
        )

    def test_debug_missing_project_returns_422(self, logs_client: TestClient) -> None:
        """GET /logs/debug?agent=coder (no project) returns 422 naming 'project'."""
        resp = logs_client.get("/logs/debug?agent=coder")
        assert resp.status_code == 422
        body = resp.json()
        assert "project" in str(body).lower(), f"422 body must name 'project': {body}"

    def test_debug_missing_agent_returns_422(self, logs_client: TestClient) -> None:
        """GET /logs/debug?project=<valid> (no agent) returns 422 naming 'agent'."""
        resp = logs_client.get(f"/logs/debug?project={_PROJECT_NAME}")
        assert resp.status_code == 422
        body = resp.json()
        assert "agent" in str(body).lower(), f"422 body must name 'agent': {body}"

    # -----------------------------------------------------------------------
    # kind=overwatch
    # -----------------------------------------------------------------------

    def test_overwatch_project_returns_200_with_content(self, logs_client: TestClient) -> None:
        """GET /logs/overwatch?project=<valid> returns 200 with sweep log content."""
        resp = logs_client.get(f"/logs/overwatch?project={_PROJECT_NAME}")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["exit_code"] == 0
        assert "sweep" in body["stdout"], (
            f"Expected overwatch log content in stdout: {body['stdout']!r}"
        )

    def test_overwatch_global_returns_200(self, logs_client: TestClient) -> None:
        """GET /logs/overwatch?project=global returns 200 (global sentinel accepted)."""
        resp = logs_client.get("/logs/overwatch?project=global")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["exit_code"] == 0
        assert "global overwatch" in body["stdout"], (
            f"Expected global overwatch content: {body['stdout']!r}"
        )

    def test_overwatch_missing_project_returns_422(self, logs_client: TestClient) -> None:
        """GET /logs/overwatch (no project) returns 422 naming 'project'."""
        resp = logs_client.get("/logs/overwatch")
        assert resp.status_code == 422
        body = resp.json()
        assert "project" in str(body).lower(), f"422 body must name 'project': {body}"

    # -----------------------------------------------------------------------
    # kind=api-server
    # -----------------------------------------------------------------------

    def test_api_server_returns_200_with_content(self, logs_client: TestClient) -> None:
        """GET /logs/api-server returns 200 with fixture content in stdout."""
        resp = logs_client.get("/logs/api-server")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["exit_code"] == 0
        assert "startup complete" in body["stdout"], (
            f"Expected api-server log content in stdout: {body['stdout']!r}"
        )

    def test_api_server_no_params_required(self, logs_client: TestClient) -> None:
        """GET /logs/api-server requires no params."""
        resp = logs_client.get("/logs/api-server")
        assert resp.status_code == 200

    # -----------------------------------------------------------------------
    # Missing log file: exit_code 1, not a 404
    # -----------------------------------------------------------------------

    def test_missing_log_file_returns_200_with_exit_code_1(
        self, logs_client: TestClient
    ) -> None:
        """A missing log file returns HTTP 200 with exit_code=1 and populated stderr."""
        # tester has no log fixture in the project.
        resp = logs_client.get(f"/logs/debug?project={_PROJECT_NAME}&agent=tester")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["exit_code"] == 1, (
            f"Missing log file must return exit_code=1; got {body['exit_code']}"
        )
        assert body["stdout"] == "", f"stdout must be empty on missing file: {body['stdout']!r}"
        assert body["stderr"], f"stderr must be non-empty on missing file: {body['stderr']!r}"


# ---------------------------------------------------------------------------
# TestTraversalConfinement
# ---------------------------------------------------------------------------


class TestTraversalConfinement:
    """Traversal negative tests: 422 before any open() call.

    Each test verifies that:
      (a) The response is HTTP 422.
      (b) The open() built-in was NOT called during the request — proving the
          422 fires at validation, before any filesystem access.
    """

    def _assert_422_no_open(
        self, client: TestClient, url: str, open_mock: unittest.mock.MagicMock
    ) -> None:
        """Assert the request returns 422 and open() was not called."""
        resp = client.get(url)
        assert resp.status_code == 422, (
            f"Expected 422 for traversal attempt; got {resp.status_code}: {resp.text}"
        )
        assert not open_mock.called, (
            f"open() must NOT be called for traversal attempt; "
            f"was called with: {open_mock.call_args_list}"
        )

    def test_project_dotdot_traversal_blocked_before_open(
        self, logs_client: TestClient
    ) -> None:
        """AC3: project=../../etc/passwd → 422; open() not called."""
        with unittest.mock.patch("builtins.open") as mock_open:
            self._assert_422_no_open(
                logs_client,
                "/logs/agent?project=../../etc/passwd&agent=coder",
                mock_open,
            )

    def test_project_url_encoded_traversal_blocked_before_open(
        self, logs_client: TestClient
    ) -> None:
        """AC4: project=..%2F.. (URL-decoded traversal) → 422; open() not called."""
        with unittest.mock.patch("builtins.open") as mock_open:
            self._assert_422_no_open(
                logs_client,
                "/logs/debug?project=..%2F..&agent=pm",
                mock_open,
            )

    def test_agent_dotdot_traversal_blocked_before_open(
        self, logs_client: TestClient
    ) -> None:
        """agent=../../etc/passwd → 422; open() not called."""
        with unittest.mock.patch("builtins.open") as mock_open:
            self._assert_422_no_open(
                logs_client,
                f"/logs/debug?project={_PROJECT_NAME}&agent=../../etc/passwd",
                mock_open,
            )

    def test_agent_url_encoded_slash_blocked_before_open(
        self, logs_client: TestClient
    ) -> None:
        """agent value with URL-encoded slash → 422; open() not called."""
        with unittest.mock.patch("builtins.open") as mock_open:
            self._assert_422_no_open(
                logs_client,
                f"/logs/wake?agent=pm%2F..%2F..%2Fetc%2Fpasswd",
                mock_open,
            )

    def test_project_slash_traversal_blocked_before_open(
        self, logs_client: TestClient
    ) -> None:
        """project value with slash → 422; open() not called."""
        with unittest.mock.patch("builtins.open") as mock_open:
            self._assert_422_no_open(
                logs_client,
                "/logs/agent?project=alpha/../../etc&agent=coder",
                mock_open,
            )

    def test_project_null_byte_blocked_before_open(
        self, logs_client: TestClient
    ) -> None:
        """project value with %00 null byte → 422; open() not called."""
        with unittest.mock.patch("builtins.open") as mock_open:
            self._assert_422_no_open(
                logs_client,
                "/logs/agent?project=alpha%00inject&agent=coder",
                mock_open,
            )


# ---------------------------------------------------------------------------
# TestTailParameter
# ---------------------------------------------------------------------------


class TestTailParameter:
    """Tests for tail query parameter validation."""

    def test_tail_default_returns_up_to_200_lines(
        self, logs_client: TestClient
    ) -> None:
        """GET /logs/wake?agent=coder (no tail) returns up to 200 lines."""
        # The coder fixture has 2200 lines; default tail=200 should return 200.
        resp = logs_client.get("/logs/wake?agent=coder")
        assert resp.status_code == 200
        body = resp.json()
        assert body["exit_code"] == 0
        lines = [ln for ln in body["stdout"].splitlines() if ln]
        assert len(lines) == 200, (
            f"Default tail=200 should return 200 lines; got {len(lines)}"
        )

    def test_tail_5000_clamped_to_2000(self, logs_client: TestClient) -> None:
        """AC6: tail=5000 is clamped to 2000 (the hard cap)."""
        resp = logs_client.get("/logs/wake?agent=coder&tail=5000")
        assert resp.status_code == 200
        body = resp.json()
        assert body["exit_code"] == 0
        lines = [ln for ln in body["stdout"].splitlines() if ln]
        assert len(lines) == 2000, (
            f"tail=5000 clamped to 2000; got {len(lines)} lines"
        )

    def test_tail_2001_clamped_to_2000(self, logs_client: TestClient) -> None:
        """Values above 2000 are clamped to 2000."""
        resp = logs_client.get("/logs/wake?agent=coder&tail=2001")
        assert resp.status_code == 200
        body = resp.json()
        lines = [ln for ln in body["stdout"].splitlines() if ln]
        assert len(lines) == 2000, (
            f"tail=2001 clamped to 2000; got {len(lines)} lines"
        )

    def test_tail_0_returns_422(self, logs_client: TestClient) -> None:
        """AC6: tail=0 returns 422."""
        resp = logs_client.get("/logs/wake?agent=pm&tail=0")
        assert resp.status_code == 422, f"Expected 422 for tail=0; got {resp.status_code}"

    def test_tail_negative_returns_422(self, logs_client: TestClient) -> None:
        """tail=-1 returns 422."""
        resp = logs_client.get("/logs/wake?agent=pm&tail=-1")
        assert resp.status_code == 422

    def test_tail_non_integer_returns_422(self, logs_client: TestClient) -> None:
        """tail=abc (non-integer) returns 422."""
        resp = logs_client.get("/logs/wake?agent=pm&tail=abc")
        assert resp.status_code == 422

    def test_tail_float_returns_422(self, logs_client: TestClient) -> None:
        """tail=1.5 (float) returns 422."""
        resp = logs_client.get("/logs/wake?agent=pm&tail=1.5")
        assert resp.status_code == 422

    def test_tail_5_returns_5_lines(self, logs_client: TestClient) -> None:
        """tail=5 returns exactly 5 lines from the end of the fixture."""
        resp = logs_client.get("/logs/wake?agent=coder&tail=5")
        assert resp.status_code == 200
        body = resp.json()
        lines = [ln for ln in body["stdout"].splitlines() if ln]
        assert len(lines) == 5, f"tail=5 should return 5 lines; got {len(lines)}"
        # Verify we got the LAST 5 lines.
        expected_last = [f"line {i}" for i in range(2196, 2201)]
        assert lines == expected_last, (
            f"Expected last 5 lines {expected_last!r}, got {lines!r}"
        )

    def test_tail_1_returns_1_line(self, logs_client: TestClient) -> None:
        """tail=1 returns the last single line."""
        resp = logs_client.get("/logs/wake?agent=coder&tail=1")
        assert resp.status_code == 200
        body = resp.json()
        lines = [ln for ln in body["stdout"].splitlines() if ln]
        assert len(lines) == 1, f"tail=1 should return 1 line; got {len(lines)}"
        assert lines[0] == "line 2200", f"Expected last line 'line 2200', got {lines[0]!r}"


# ---------------------------------------------------------------------------
# TestUnknownKind
# ---------------------------------------------------------------------------


class TestUnknownKind:
    """404 for unknown kind values."""

    def test_unknown_kind_returns_404(self, logs_client: TestClient) -> None:
        """GET /logs/foobar returns 404."""
        resp = logs_client.get("/logs/foobar")
        assert resp.status_code == 404, (
            f"Expected 404 for unknown kind; got {resp.status_code}: {resp.text}"
        )

    def test_unknown_kind_body_echoes_kind(self, logs_client: TestClient) -> None:
        """404 body echoes the unknown kind."""
        resp = logs_client.get("/logs/nonexistent")
        assert resp.status_code == 404
        body = resp.json()
        assert "kind" in body, f"404 body must include 'kind' field: {body}"
        assert body["kind"] == "nonexistent", (
            f"404 body must echo the kind; got {body['kind']!r}"
        )

    def test_unknown_kind_with_params_returns_404(self, logs_client: TestClient) -> None:
        """GET /logs/badkind?agent=pm still returns 404 (kind check precedes param validation)."""
        resp = logs_client.get("/logs/badkind?agent=pm&project=foo")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# TestAgentValidation
# ---------------------------------------------------------------------------


class TestAgentValidation:
    """Invalid agent values → 422 (agent validated against the fixed set)."""

    def test_invalid_agent_returns_422(self, logs_client: TestClient) -> None:
        """AC5: GET /logs/agent?project=<valid>&agent=<invalid> returns 422."""
        resp = logs_client.get(f"/logs/agent?project={_PROJECT_NAME}&agent=badagent")
        assert resp.status_code == 422, (
            f"Expected 422 for invalid agent; got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert "agent" in str(body).lower(), (
            f"422 body must mention 'agent': {body}"
        )

    def test_valid_agents_accepted(self, logs_client: TestClient) -> None:
        """All valid agents in the fixed set are accepted without 422."""
        valid_agents = ["pm", "po", "coder", "writer", "tester", "cm", "overwatch"]
        for agent_name in valid_agents:
            resp = logs_client.get(f"/logs/wake?agent={agent_name}")
            # The log file may not exist (exit_code=1), but the HTTP status is 200.
            assert resp.status_code == 200, (
                f"Valid agent {agent_name!r} should not return 422; "
                f"got {resp.status_code}: {resp.text}"
            )

    def test_empty_agent_returns_422(self, logs_client: TestClient) -> None:
        """agent= (empty value) returns 422."""
        resp = logs_client.get("/logs/wake?agent=")
        assert resp.status_code == 422

    def test_unknown_project_returns_422(self, logs_client: TestClient) -> None:
        """project= an unregistered name returns 422 (project registry check)."""
        resp = logs_client.get("/logs/agent?project=does-not-exist&agent=coder")
        assert resp.status_code == 422, (
            f"Expected 422 for unknown project; got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert "project" in str(body).lower(), (
            f"422 body must mention 'project': {body}"
        )

    def test_overwatch_global_sentinel_not_rejected(
        self, logs_client: TestClient
    ) -> None:
        """project=global is valid for the overwatch kind (special sentinel)."""
        resp = logs_client.get("/logs/overwatch?project=global")
        assert resp.status_code == 200, (
            f"project=global must be accepted for overwatch kind; "
            f"got {resp.status_code}: {resp.text}"
        )

    def test_overwatch_global_rejected_for_agent_kind(
        self, logs_client: TestClient
    ) -> None:
        """project=global is not accepted for kinds other than overwatch."""
        resp = logs_client.get("/logs/agent?project=global&agent=coder")
        assert resp.status_code == 422, (
            f"project=global must be rejected for non-overwatch kind; "
            f"got {resp.status_code}: {resp.text}"
        )


# ---------------------------------------------------------------------------
# TestEnvelopeShape
# ---------------------------------------------------------------------------


class TestEnvelopeShape:
    """The response envelope always contains exit_code, stdout, stderr."""

    def test_envelope_fields_present_on_success(
        self, logs_client: TestClient
    ) -> None:
        """Successful response carries exit_code, stdout, stderr."""
        resp = logs_client.get("/logs/cm")
        assert resp.status_code == 200
        body = resp.json()
        assert "exit_code" in body, f"Missing 'exit_code' in envelope: {body}"
        assert "stdout" in body, f"Missing 'stdout' in envelope: {body}"
        assert "stderr" in body, f"Missing 'stderr' in envelope: {body}"

    def test_envelope_stdout_contains_tail_text(
        self, logs_client: TestClient
    ) -> None:
        """stdout contains the tail text; stderr is empty on success."""
        resp = logs_client.get("/logs/cm")
        body = resp.json()
        assert body["exit_code"] == 0
        assert body["stderr"] == "", f"stderr must be empty on success: {body['stderr']!r}"
        assert body["stdout"], "stdout must be non-empty when log file exists"
