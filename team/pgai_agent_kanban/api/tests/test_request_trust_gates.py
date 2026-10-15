"""
test_request_trust_gates.py — Tests for the two request-trust gates in the
operator API's middleware stack.

The loopback Host gate (``LoopbackHostMiddleware``) refuses any request whose
Host header is not a loopback name, on every route.  The JSON content-type
gate (``JsonMutationMiddleware``) refuses a mutating request that does not
carry ``Content-Type: application/json`` before it reaches the route.

Tests:
  1. Foreign Host — a JSON POST to /operations/wake-now with Host
     ``evil.example`` is refused 400 with a dual-channel body and no
     subprocess is spawned; a GET read route refuses the same Host the same
     way.
  2. Loopback Host — 127.0.0.1, localhost and [::1], each with and without a
     port, reach the route: GET /health returns 200 with the same body the
     unguarded route returned.
  3. Non-JSON mutating request — a form-encoded POST, and a POST with no
     content type, are refused 415 with a dual-channel body and no
     subprocess is spawned; GET routes ignore the content type.
  4. JSON pass-through — a JSON POST (with and without a charset parameter)
     reaches the route: the route's own required-field refusal (422) is the
     proof it was routed, and a runnable route runs to completion.
  5. Both refusals carry the same top-level error/message/remedy/warnings
     keys the action routes' pre-dispatch refusals carry, and the remedy is
     a curl line with no placeholder.

All requests use FastAPI's TestClient (ASGI transport — no real network
socket).  A deliberately foreign Host is sent as a per-request header, which
overrides the loopback base_url the framework-wide fixture gives the client.
"""

from __future__ import annotations

import pathlib
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# This file lives at team/pgai_agent_kanban/api/tests/; four levels up is team/.
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent.parent

_FOREIGN_HOST = "evil.example"
_LOOPBACK_HOSTS = (
    "127.0.0.1",
    "127.0.0.1:8300",
    "localhost",
    "localhost:3000",
    "LOCALHOST",
    "[::1]",
    "[::1]:8300",
)
_MUTATING_ROUTE = "/operations/wake-now"
_REFUSAL_KEYS = {"error", "message", "remedy", "warnings"}


@pytest.fixture
def client(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Return a TestClient over a full create_app() bound to a sandbox root.

    Scripts resolve to the real team/ dev tree; every kanban root name points
    at a throwaway directory so no live state is touched.
    """
    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    sandbox = tmp_path / "trust_sandbox"
    sandbox.mkdir()
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(sandbox))
    monkeypatch.setenv("KANBAN_ROOT", str(sandbox))

    cfg = ApiConfig(host="127.0.0.1", port=8300, kanban_root=_TEAM_DIR)
    return TestClient(create_app(cfg=cfg), raise_server_exceptions=True)


def _assert_dual_channel(body: dict, expected_error: str) -> None:
    """Assert *body* is the four-key refusal shape under *expected_error*."""
    assert set(body) == _REFUSAL_KEYS, (
        f"refusal body must carry exactly {sorted(_REFUSAL_KEYS)}; got {sorted(body)}"
    )
    assert body["error"] == expected_error, f"machine key: {body['error']!r}"
    assert body["message"].strip(), "message must be non-empty"
    assert body["remedy"].startswith("curl "), f"remedy must be a runnable curl line: {body['remedy']!r}"
    assert "<" not in body["remedy"] and ">" not in body["remedy"], (
        f"remedy must carry no placeholder: {body['remedy']!r}"
    )
    assert body["warnings"] == [], f"no route ran, so warnings must be empty: {body['warnings']!r}"


# ---------------------------------------------------------------------------
# 1. Foreign Host is refused on mutating and read routes
# ---------------------------------------------------------------------------


def test_foreign_host_json_post_refused_400_before_any_subprocess(client: TestClient) -> None:
    """A JSON POST with Host evil.example is refused 400 and spawns nothing."""
    with patch("pgai_agent_kanban.api.adapter.subprocess.run") as run:
        response = client.post(
            _MUTATING_ROUTE,
            json={"agent": "coder"},
            headers={"Host": _FOREIGN_HOST},
        )
    assert response.status_code == 400, response.text
    _assert_dual_channel(response.json(), "api.host_not_loopback")
    assert _FOREIGN_HOST in response.json()["message"]
    assert "http://127.0.0.1:8300/operations/wake-now" in response.json()["remedy"]
    run.assert_not_called()


def test_foreign_host_read_route_refused_400(client: TestClient) -> None:
    """GET /health with Host evil.example is refused exactly like a POST."""
    response = client.get("/health", headers={"Host": _FOREIGN_HOST})
    assert response.status_code == 400, response.text
    _assert_dual_channel(response.json(), "api.host_not_loopback")
    assert response.json()["remedy"] == "curl -sS http://127.0.0.1:8300/health"


def test_foreign_host_with_port_remedy_targets_that_port(client: TestClient) -> None:
    """The remedy re-targets the port the caller reached, on a loopback name."""
    response = client.get("/status?project=demo", headers={"Host": f"{_FOREIGN_HOST}:9999"})
    assert response.status_code == 400, response.text
    assert response.json()["remedy"] == "curl -sS 'http://127.0.0.1:9999/status?project=demo'"


def test_empty_host_refused_400(client: TestClient) -> None:
    """A request naming no host at all is refused: it has not named the server."""
    response = client.get("/health", headers={"Host": ""})
    assert response.status_code == 400, response.text
    assert response.json()["error"] == "api.host_not_loopback"


def test_foreign_host_preflight_refused_before_cors(client: TestClient) -> None:
    """The CORS preflight from a foreign Host is refused by the Host gate."""
    response = client.options(
        _MUTATING_ROUTE,
        headers={
            "Host": _FOREIGN_HOST,
            "Origin": "http://127.0.0.1:8000",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 400, response.text
    assert "access-control-allow-origin" not in response.headers


# ---------------------------------------------------------------------------
# 2. Loopback Hosts reach the route unchanged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("host", _LOOPBACK_HOSTS)
def test_loopback_host_reaches_route(client: TestClient, host: str) -> None:
    """Every loopback spelling, with or without a port, is admitted."""
    response = client.get("/health", headers={"Host": host})
    assert response.status_code == 200, f"Host {host!r}: {response.status_code} {response.text}"
    body = response.json()
    assert body["service"] == "pgai-agent-kanban-api"
    assert body["warnings"] == []


def test_default_test_client_host_is_loopback(client: TestClient) -> None:
    """The framework fixture gives the client a loopback Host, not testserver."""
    assert client.base_url.host == "127.0.0.1"
    assert client.get("/health").status_code == 200


def test_loopback_preflight_still_answered(client: TestClient) -> None:
    """The CORS preflight from a loopback Host is answered as before."""
    response = client.options(
        _MUTATING_ROUTE,
        headers={"Origin": "http://127.0.0.1:8000", "Access-Control-Request-Method": "POST"},
    )
    assert response.status_code in (200, 204), response.text
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:8000"


# ---------------------------------------------------------------------------
# 3. Non-JSON mutating requests are refused before the route
# ---------------------------------------------------------------------------


def test_form_post_refused_415_before_any_subprocess(client: TestClient) -> None:
    """A form-encoded POST to a mutating route is refused 415 and spawns nothing."""
    with patch("pgai_agent_kanban.api.adapter.subprocess.run") as run:
        response = client.post(_MUTATING_ROUTE, data={"agent": "coder"})
    assert response.status_code == 415, response.text
    body = response.json()
    _assert_dual_channel(body, "api.content_type_not_json")
    assert "application/x-www-form-urlencoded" in body["message"]
    assert body["remedy"] == (
        "curl -sS -X POST http://127.0.0.1/operations/wake-now "
        "-H 'Content-Type: application/json' -d '{}'"
    )
    run.assert_not_called()


def test_post_without_content_type_refused_415(client: TestClient) -> None:
    """A POST carrying no Content-Type header at all is refused 415."""
    with patch("pgai_agent_kanban.api.adapter.subprocess.run") as run:
        response = client.post(_MUTATING_ROUTE, content=b'{"agent": "coder"}')
    assert response.status_code == 415, response.text
    _assert_dual_channel(response.json(), "api.content_type_not_json")
    assert "no Content-Type header" in response.json()["message"]
    run.assert_not_called()


def test_text_plain_post_refused_415(client: TestClient) -> None:
    """text/plain -- the other browser simple-request type -- is refused too."""
    response = client.post(
        _MUTATING_ROUTE, content=b'{"agent": "coder"}', headers={"Content-Type": "text/plain"}
    )
    assert response.status_code == 415, response.text


def test_get_route_ignores_content_type(client: TestClient) -> None:
    """The content-type gate never touches a read route."""
    response = client.get(
        "/health", headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    assert response.status_code == 200, response.text


def test_host_gate_runs_before_content_type_gate(client: TestClient) -> None:
    """A foreign-Host form POST is refused as a Host failure, the outer gate."""
    response = client.post(
        _MUTATING_ROUTE, data={"agent": "coder"}, headers={"Host": _FOREIGN_HOST}
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"] == "api.host_not_loopback"


# ---------------------------------------------------------------------------
# 4. JSON POSTs pass through to the route
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content_type", ["application/json", "application/json; charset=utf-8", "Application/JSON"]
)
def test_json_post_reaches_route(client: TestClient, content_type: str) -> None:
    """A JSON POST passes both gates: the route's own 422 proves it was routed.

    ``POST /operations/halt`` with an empty body is refused by the route
    itself (``halt.project_required``) before any subprocess runs, so the
    422 is evidence the request passed both middleware gates and reached
    the route, with nothing spawned.
    """
    with patch("pgai_agent_kanban.api.adapter.subprocess.run") as run:
        response = client.post(
            "/operations/halt", content=b"{}", headers={"Content-Type": content_type}
        )
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"] == "halt.project_required", body
    run.assert_not_called()


def test_json_post_runs_route_to_completion(client: TestClient, tmp_path: pathlib.Path) -> None:
    """A JSON POST to a runnable route behaves exactly as before the gates."""
    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    # POST /operations/halt-global calls ops.write.halt_global in process
    # against the API's own kanban root, so this client is rooted at the
    # sandbox the ``client`` fixture created (it names it in the env too).
    sandbox = pathlib.Path(tmp_path / "trust_sandbox")
    cfg = ApiConfig(host="127.0.0.1", port=8300, kanban_root=sandbox)
    with TestClient(create_app(cfg=cfg), raise_server_exceptions=True) as sandbox_client:
        response = sandbox_client.post("/operations/halt-global", json={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["exit_code"] == 0, body
    assert (sandbox / "HALT").exists(), "the halt-global operation should have written the sentinel"
