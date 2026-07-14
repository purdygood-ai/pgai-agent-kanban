"""
test_cors.py — Tests for loopback-scoped CORS middleware in the operator API.

The CORSMiddleware added to create_app() permits browser pages served from any
loopback origin (127.0.0.1 or localhost, any port) to call the API.
Non-loopback origins receive no CORS headers.

Tests:
  1. Positive — a loopback origin (127.0.0.1:8000 style) is echoed in the
     Access-Control-Allow-Origin response header.
  2. Positive arbitrary port — a loopback origin on an operator-chosen port
     (localhost:3000) is also echoed.
  3. Negative — a non-loopback origin receives no Access-Control-Allow-Origin
     header.
  4. Preflight (OPTIONS) — a preflight request from a loopback origin against
     a POST /operations route returns 200 with the CORS allow headers.

All requests use FastAPI's TestClient (ASGI transport — no real network socket).
"""

from __future__ import annotations

import pathlib

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Resolve the dev-tree root so ApiConfig can find scripts (mirrors test_fidelity).
# This file lives at team/pgai_agent_kanban/api/tests/test_cors.py.
# Going up four levels: api/tests → api → pgai_agent_kanban → team
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent.parent  # team/


# ---------------------------------------------------------------------------
# Fixture: a minimal TestClient bound to a throwaway sandbox
# ---------------------------------------------------------------------------


@pytest.fixture
def cors_client(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Return a TestClient with CORS middleware active.

    Uses a throwaway sandbox for kanban_root so no live state is touched.
    The ASGI transport means no real port is opened.

    Args:
        tmp_path:    pytest-provided per-test temporary directory.
        monkeypatch: pytest monkeypatch fixture for environment isolation.

    Returns:
        A configured TestClient for the duration of the test.
    """
    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    sandbox = tmp_path / "cors_sandbox"
    sandbox.mkdir()

    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(sandbox))
    monkeypatch.setenv("KANBAN_ROOT", str(sandbox))

    cfg = ApiConfig(
        host="127.0.0.1",
        port=0,          # ephemeral; TestClient does not open a real socket
        kanban_root=_TEAM_DIR,
    )
    app = create_app(cfg=cfg)
    # raise_server_exceptions=False so that 4xx/5xx from routing do not blow
    # up the test — we only care about response headers here, not body status.
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Positive case 1: 127.0.0.1:<port> loopback origin is echoed
# ---------------------------------------------------------------------------


def test_cors_loopback_127_origin_echoed(cors_client: TestClient) -> None:
    """A request from http://127.0.0.1:8000 receives the echoed origin header.

    The Access-Control-Allow-Origin response header must equal the supplied
    loopback Origin exactly.  This verifies the positive case using the
    canonical UI serving origin.
    """
    origin = "http://127.0.0.1:8000"
    response = cors_client.get("/health", headers={"Origin": origin})

    assert "access-control-allow-origin" in response.headers, (
        "Expected Access-Control-Allow-Origin header for loopback origin "
        f"{origin!r}, but it was absent. Response headers: {dict(response.headers)}"
    )
    assert response.headers["access-control-allow-origin"] == origin, (
        f"Access-Control-Allow-Origin should echo {origin!r} exactly; "
        f"got {response.headers['access-control-allow-origin']!r}"
    )


# ---------------------------------------------------------------------------
# Positive case 2: localhost:<arbitrary-port> loopback origin is echoed
# ---------------------------------------------------------------------------


def test_cors_localhost_arbitrary_port_echoed(cors_client: TestClient) -> None:
    """A request from http://localhost:3000 receives the echoed origin header.

    Verifies that the regex allows 'localhost' as the host and accepts an
    operator-chosen port that differs from the API's own port.
    """
    origin = "http://localhost:3000"
    response = cors_client.get("/health", headers={"Origin": origin})

    assert "access-control-allow-origin" in response.headers, (
        "Expected Access-Control-Allow-Origin header for loopback origin "
        f"{origin!r}, but it was absent. Response headers: {dict(response.headers)}"
    )
    assert response.headers["access-control-allow-origin"] == origin, (
        f"Access-Control-Allow-Origin should echo {origin!r} exactly; "
        f"got {response.headers['access-control-allow-origin']!r}"
    )


# ---------------------------------------------------------------------------
# Negative case: non-loopback origin receives no CORS header
# ---------------------------------------------------------------------------


def test_cors_non_loopback_origin_blocked(cors_client: TestClient) -> None:
    """A request from http://evil.example receives no Access-Control-Allow-Origin.

    Non-loopback origins must not receive CORS headers.  This ensures the
    middleware does not fall back to a wildcard or leak any loopback permission
    to arbitrary origins.
    """
    origin = "http://evil.example"
    response = cors_client.get("/health", headers={"Origin": origin})

    assert "access-control-allow-origin" not in response.headers, (
        "Expected NO Access-Control-Allow-Origin header for non-loopback origin "
        f"{origin!r}, but found: "
        f"{response.headers.get('access-control-allow-origin')!r}"
    )


# ---------------------------------------------------------------------------
# Preflight case: OPTIONS against a POST /operations route from loopback origin
# ---------------------------------------------------------------------------


def test_cors_preflight_operations_halt(cors_client: TestClient) -> None:
    """OPTIONS preflight from a loopback origin against /operations/halt returns allow headers.

    A browser issues a preflight OPTIONS request before a cross-origin POST.
    The CORS middleware must respond with:
      - HTTP 200 (or 204)
      - Access-Control-Allow-Origin echoing the loopback origin
      - Access-Control-Allow-Methods containing POST

    This verifies that the browser's preflight check succeeds so that the
    subsequent POST /operations/halt request is allowed through.
    """
    origin = "http://127.0.0.1:8000"
    response = cors_client.options(
        "/operations/halt",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code in (200, 204), (
        f"Expected 200 or 204 for CORS preflight, got {response.status_code}. "
        f"Response body: {response.text!r}"
    )
    assert "access-control-allow-origin" in response.headers, (
        "Expected Access-Control-Allow-Origin header in preflight response, "
        f"but it was absent. Response headers: {dict(response.headers)}"
    )
    assert response.headers["access-control-allow-origin"] == origin, (
        f"Access-Control-Allow-Origin should echo {origin!r}; "
        f"got {response.headers['access-control-allow-origin']!r}"
    )
    # The CORS middleware should include POST in the allowed methods.
    allow_methods_header = response.headers.get("access-control-allow-methods", "")
    assert "POST" in allow_methods_header, (
        f"Expected 'POST' in Access-Control-Allow-Methods, "
        f"got {allow_methods_header!r}"
    )
