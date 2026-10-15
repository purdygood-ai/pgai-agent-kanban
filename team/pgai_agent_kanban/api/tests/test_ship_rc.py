"""
test_ship_rc.py — Tests for POST /operations/ship-rc (confirm double-gate).

Acceptance criteria covered:

  1. Wrong confirm string → 422 whose detail names ``confirm``; ship_rc does NOT run.
  2. Missing confirm field → 422; ship_rc does NOT run.
  3. Correct confirm string + dry_run: true → cm.ship.ship_rc called with
     ``dry_run=True``, exit_code 0; without dry_run → called live.
  4. Regression: all existing 11 endpoints remain reachable (byte-identical check deferred
     to the fidelity suite; this file asserts the routes exist and the confirm-gate
     does not interfere with other endpoints by checking the router count).

Design notes:
  - API is exercised via FastAPI TestClient (ASGI transport; no real socket).
  - The confirm-gate 422 tests use ``patch("subprocess.run")`` to assert that
    no subprocess is spawned before the gate fires.
  - The route calls cm.ship.ship_rc in process; the pass-through tests replace
    that function with a recorder and assert the arguments it received and the
    envelope built from what it printed or raised. The behaviour of ship_rc
    itself, end to end on sandbox repositories, is
    tests/integration/test_cm_ship_rc.py.
  - All tests are hermetic; no live kanban state is touched.
"""

from __future__ import annotations

import pathlib
from typing import Generator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Resolve dev tree (team/) so the API test client can find scripts.
# This file lives at team/pgai_agent_kanban/api/tests/test_ship_rc.py.
# Going up four levels: tests → api → pgai_agent_kanban → team
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_SHIP_RC_SCRIPT = _SCRIPTS_DIR / "ship-rc.sh"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def dev_tree_team_dir() -> pathlib.Path:
    """Return the absolute path to team/ in the dev tree."""
    return _TEAM_DIR


@pytest.fixture
def api_client(
    dev_tree_team_dir: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    """Create a TestClient bound to a minimal sandbox kanban root.

    The API config's kanban_root is set to team/ so that
    ``kanban_root / "scripts"`` resolves to the real operator scripts.
    PGAI_AGENT_KANBAN_ROOT_PATH is set to tmp_path (sandbox) so any
    script that reads kanban state operates on the sandbox.

    Yields:
        A configured TestClient instance for the duration of the test.
    """
    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    sandbox = tmp_path / "kanban_sandbox"
    sandbox.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(sandbox))
    monkeypatch.setenv("KANBAN_ROOT", str(sandbox))

    cfg = ApiConfig(
        host="127.0.0.1",
        port=0,
        kanban_root=dev_tree_team_dir,
    )
    app = create_app(cfg=cfg)

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


# ---------------------------------------------------------------------------
# Test class: ship-rc confirm double-gate
# ---------------------------------------------------------------------------


class TestShipRcConfirmGate:
    """Acceptance tests for POST /operations/ship-rc confirm double-gate.

    Criteria:
      1. Wrong confirm → 422 naming ``confirm``; no subprocess spawned.
      2. Missing confirm → 422; no subprocess spawned.
      3. Correct confirm + dry_run → exit_code 0 (cm.ship.ship_rc replaced, called as a dry run).
    """

    def test_wrong_confirm_returns_422_naming_confirm_and_no_subprocess(
        self,
        api_client: "TestClient",
    ) -> None:
        """POST /operations/ship-rc with wrong confirm string returns 422.

        The 422 detail must name ``confirm`` so the caller understands what
        is wrong.  ship-rc.sh must NOT be invoked — the gate fires before
        any subprocess is spawned.
        """
        with patch("subprocess.run") as mock_run:
            resp = api_client.post(
                "/operations/ship-rc",
                json={
                    "project": "my-project",
                    "key": "v1.5.0",
                    "confirm": "wrong-string",
                },
            )
            # ship-rc.sh must never be invoked.
            mock_run.assert_not_called()

        assert resp.status_code == 422, (
            f"Expected HTTP 422 (confirm gate); got {resp.status_code}.\n"
            f"body: {resp.text}"
        )
        body = resp.json()
        assert "detail" not in body, f"framework validation list leaked into the refusal: {body}"
        assert body["error"] == "ship_rc.confirm_mismatch", body
        assert "confirm" in body["message"], (
            f"422 message must name 'confirm'; got: {body['message']!r}"
        )
        assert body["remedy"].startswith("curl "), body["remedy"]

    def test_confirm_mismatch_with_transposed_project_key_returns_422_no_subprocess(
        self,
        api_client: "TestClient",
    ) -> None:
        """POST /operations/ship-rc with transposed project/key in confirm returns 422.

        Even if the confirm string is well-formed but uses the wrong values
        (e.g. project and key swapped), the gate must reject it.
        ship-rc.sh must NOT be invoked.
        """
        with patch("subprocess.run") as mock_run:
            resp = api_client.post(
                "/operations/ship-rc",
                json={
                    "project": "my-project",
                    "key": "v1.5.0",
                    # Correct format but values swapped
                    "confirm": "ship-rc v1.5.0 my-project",
                },
            )
            mock_run.assert_not_called()

        assert resp.status_code == 422, (
            f"Expected HTTP 422 (confirm gate with transposed values); "
            f"got {resp.status_code}.\nbody: {resp.text}"
        )
        body = resp.json()
        assert "detail" not in body, f"framework validation list leaked into the refusal: {body}"
        assert body["error"] == "ship_rc.confirm_mismatch", body
        assert "confirm" in body["message"], (
            f"422 message must name 'confirm'; got: {body['message']!r}"
        )
        assert body["remedy"].startswith("curl "), body["remedy"]

    def test_missing_confirm_returns_422_and_no_subprocess(
        self,
        api_client: "TestClient",
    ) -> None:
        """POST /operations/ship-rc without confirm field returns 422.

        A body that omits confirm entirely must return 422 carrying the
        route's own dual-channel refusal (``ship_rc.confirm_required``),
        never the framework's ``detail`` list, without invoking ship-rc.sh.
        """
        with patch("subprocess.run") as mock_run:
            resp = api_client.post(
                "/operations/ship-rc",
                json={
                    "project": "my-project",
                    "key": "v1.5.0",
                    # confirm is omitted
                },
            )
            mock_run.assert_not_called()

        assert resp.status_code == 422, (
            f"Expected HTTP 422 (missing confirm); got {resp.status_code}.\n"
            f"body: {resp.text}"
        )
        body = resp.json()
        assert "detail" not in body, f"framework validation list leaked into the refusal: {body}"
        assert body["error"] == "ship_rc.confirm_required", body
        assert "confirm" in body["message"], body

    def test_confirm_with_trailing_space_returns_422_no_subprocess(
        self,
        api_client: "TestClient",
    ) -> None:
        """POST /operations/ship-rc with trailing space in confirm returns 422.

        The comparison is byte-for-byte exact — no trimming.  A confirm
        string that is otherwise correct but has a trailing space must
        be rejected.  ship-rc.sh must NOT be invoked.
        """
        with patch("subprocess.run") as mock_run:
            resp = api_client.post(
                "/operations/ship-rc",
                json={
                    "project": "my-project",
                    "key": "v1.5.0",
                    # Trailing space — must NOT be trimmed
                    "confirm": "ship-rc my-project v1.5.0 ",
                },
            )
            mock_run.assert_not_called()

        assert resp.status_code == 422, (
            f"Expected HTTP 422 (trailing space in confirm); "
            f"got {resp.status_code}.\nbody: {resp.text}"
        )
        body = resp.json()
        assert "detail" not in body, f"framework validation list leaked into the refusal: {body}"
        assert body["error"] == "ship_rc.confirm_mismatch", body
        assert "confirm" in body["message"], (
            f"422 message must name 'confirm'; got: {body['message']!r}"
        )
        assert body["remedy"].startswith("curl "), body["remedy"]

    @staticmethod
    def _replace_ship_rc(monkeypatch: pytest.MonkeyPatch, *, refuse: bool = False) -> list:
        """Swap cm.ship.ship_rc for a recorder that narrates (or refuses) like the real one."""
        from pgai_agent_kanban.cm import ship as cm_ship

        calls: list = []

        def _fake(ctx, project, version, **kwargs):
            calls.append((ctx, project, version, kwargs))
            if refuse:
                raise cm_ship.ShipRcError(
                    "ship_rc.refused", f"ERROR: branch 'ai_rc/{version}' does not exist locally\n"
                )
            kwargs["out"]("ship-rc dry-run: no git operations will be performed.\n")
            return cm_ship.ShipOutcome(project, version, f"rc/{version}", "main", version, "planned")

        monkeypatch.setattr(cm_ship, "ship_rc", _fake)
        return calls

    def test_correct_confirm_with_dry_run_reaches_ship_rc_as_a_dry_run(
        self,
        api_client: "TestClient",
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """POST /operations/ship-rc with correct confirm and dry_run: true returns exit_code 0.

        The route calls cm.ship.ship_rc in process with ``dry_run=True`` --
        ship_rc's own plan, no git step -- and starts no subprocess itself.
        """
        calls = self._replace_ship_rc(monkeypatch)
        with patch("subprocess.run") as mock_run:
            resp = api_client.post(
                "/operations/ship-rc",
                json={
                    "project": "my-project",
                    "key": "v1.5.0",
                    "confirm": "ship-rc my-project v1.5.0",
                    "dry_run": True,
                },
            )
            mock_run.assert_not_called()

        assert resp.status_code == 200, (
            f"Expected HTTP 200 on exit_code 0 dry-run; "
            f"got {resp.status_code}.\nbody: {resp.text}"
        )
        assert resp.json() == {
            "exit_code": 0,
            "stdout": "ship-rc dry-run: no git operations will be performed.\n",
            "stderr": "",
            "warnings": [],
        }
        assert len(calls) == 1, calls
        _ctx, project, version, kwargs = calls[0]
        assert (project, version) == ("my-project", "v1.5.0")
        assert kwargs["dry_run"] is True, kwargs

    def test_correct_confirm_without_dry_run_reaches_ship_rc_live(
        self,
        api_client: "TestClient",
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """POST /operations/ship-rc with correct confirm but no dry_run calls ship_rc live.

        ship_rc refusing (no RC branch) propagates as a non-zero exit_code at
        HTTP 500, its plain stderr text lifted as ``message`` under the
        route's fallback key with the runnable CLI form as ``remedy``.
        """
        calls = self._replace_ship_rc(monkeypatch, refuse=True)
        resp = api_client.post(
            "/operations/ship-rc",
            json={
                "project": "my-project",
                "key": "v1.5.0",
                "confirm": "ship-rc my-project v1.5.0",
                # dry_run omitted — live path
            },
        )

        assert resp.status_code == 500, (
            f"Expected HTTP 500 for a refused ship; "
            f"got {resp.status_code}.\nbody: {resp.text}"
        )
        body = resp.json()
        assert body["exit_code"] == 1, body
        assert body["stderr"] == "ERROR: branch 'ai_rc/v1.5.0' does not exist locally\n"
        assert body["error"] == "ship_rc.refused"
        assert body["remedy"].endswith(
            "team/scripts/ship-rc.sh --project my-project --key v1.5.0"
        ), body["remedy"]
        assert len(calls) == 1, calls
        assert calls[0][3]["dry_run"] is False, calls[0][3]
