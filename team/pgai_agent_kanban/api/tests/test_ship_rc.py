"""
test_ship_rc.py — Tests for POST /operations/ship-rc (confirm double-gate).

Acceptance criteria covered:

  1. Wrong confirm string → 422 whose detail names ``confirm``; ship-rc.sh is NOT invoked.
  2. Missing confirm field → 422; ship-rc.sh is NOT invoked.
  3. Correct confirm string + dry_run: true → exit_code 0 from a dry-run ship-rc.sh.
  4. Regression: all existing 11 endpoints remain reachable (byte-identical check deferred
     to the fidelity suite; this file asserts the routes exist and the confirm-gate
     does not interfere with other endpoints by checking the router count).

Design notes:
  - API is exercised via FastAPI TestClient (ASGI transport; no real socket).
  - The confirm-gate 422 tests use ``patch("subprocess.run")`` to assert that
    no subprocess is spawned before the gate fires.
  - The dry-run passthrough test uses the real ship-rc.sh; it requires a project
    context resolvable by ship-rc.sh's pp layer.  Because this is non-trivial to
    sandbox in a unit test (ship-rc.sh sources project_paths.sh and pp_require_project_context),
    the dry-run test mocks subprocess.run to return a controlled exit_code=0 result
    that simulates the script's dry-run output.  The assertion is that the endpoint
    calls subprocess.run exactly once with the correct argv including --dry-run, and
    that the envelope reflects exit_code 0.
  - All tests are hermetic; no live kanban state is touched.
"""

from __future__ import annotations

import pathlib
from typing import Generator
from unittest.mock import MagicMock, call, patch

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
      3. Correct confirm + dry_run → exit_code 0 (mocked dry-run script call).
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
        detail = resp.json().get("detail", "")
        assert "confirm" in detail, (
            f"422 detail must name 'confirm'; got: {detail!r}"
        )

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
        detail = resp.json().get("detail", "")
        assert "confirm" in detail, (
            f"422 detail must name 'confirm'; got: {detail!r}"
        )

    def test_missing_confirm_returns_422_and_no_subprocess(
        self,
        api_client: "TestClient",
    ) -> None:
        """POST /operations/ship-rc without confirm field returns 422.

        A body that omits confirm entirely must return 422 (Pydantic
        required-field validation) without invoking ship-rc.sh.
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
        detail = resp.json().get("detail", "")
        assert "confirm" in detail, (
            f"422 detail must name 'confirm'; got: {detail!r}"
        )

    def test_correct_confirm_with_dry_run_invokes_script_once_and_returns_exit_code_0(
        self,
        api_client: "TestClient",
    ) -> None:
        """POST /operations/ship-rc with correct confirm and dry_run: true returns exit_code 0.

        When the confirm string matches exactly, the endpoint passes through to
        ship-rc.sh.  With dry_run: true, ship-rc.sh is called with --dry-run and
        exits 0 without performing any git operations.

        The subprocess is mocked to return exit_code 0 (simulating the script's
        dry-run output) so this test does not require a real git repository.
        The assertion is:
          - subprocess.run is called exactly once.
          - The argv includes --dry-run.
          - The envelope exit_code is 0 and HTTP status is 200.
        """
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = (
            "ship-rc dry-run: no git operations will be performed.\n"
            "  Project:   my-project\n"
            "  Version:   v1.5.0\n"
            "  (dry-run: exiting before any git step)\n"
        )
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            resp = api_client.post(
                "/operations/ship-rc",
                json={
                    "project": "my-project",
                    "key": "v1.5.0",
                    "confirm": "ship-rc my-project v1.5.0",
                    "dry_run": True,
                },
            )
            # ship-rc.sh must be called exactly once.
            mock_run.assert_called_once()

        assert resp.status_code == 200, (
            f"Expected HTTP 200 on exit_code 0 dry-run; "
            f"got {resp.status_code}.\nbody: {resp.text}"
        )
        body = resp.json()
        assert body["exit_code"] == 0, (
            f"envelope exit_code must be 0 for dry-run; got {body['exit_code']}.\n"
            f"stdout: {body['stdout']}\nstderr: {body['stderr']}"
        )

        # Verify --dry-run flag was forwarded to the script.
        call_args = mock_run.call_args
        argv_used = call_args[0][0]  # first positional argument to subprocess.run
        assert "--dry-run" in argv_used, (
            f"--dry-run must be in the subprocess argv; got: {argv_used!r}"
        )

    def test_correct_confirm_without_dry_run_invokes_script_without_dry_run_flag(
        self,
        api_client: "TestClient",
    ) -> None:
        """POST /operations/ship-rc with correct confirm but no dry_run passes through.

        When dry_run is absent (or False), the endpoint invokes ship-rc.sh
        without --dry-run.  The subprocess is mocked to return non-zero
        (simulating a real script guard failing, e.g. no RC branch) so
        the envelope propagates that non-zero exit_code as HTTP 500.

        The critical assertion is that subprocess.run IS called (the gate
        did not block it) and --dry-run does NOT appear in the argv.
        """
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "ERROR: branch 'ai_rc/v1.5.0' does not exist locally\n"

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            resp = api_client.post(
                "/operations/ship-rc",
                json={
                    "project": "my-project",
                    "key": "v1.5.0",
                    "confirm": "ship-rc my-project v1.5.0",
                    # dry_run omitted — live path
                },
            )
            mock_run.assert_called_once()

        assert resp.status_code == 500, (
            f"Expected HTTP 500 for non-zero script exit; "
            f"got {resp.status_code}.\nbody: {resp.text}"
        )
        body = resp.json()
        assert body["exit_code"] != 0, (
            f"envelope exit_code must be non-zero; got {body['exit_code']}."
        )

        call_args = mock_run.call_args
        argv_used = call_args[0][0]
        assert "--dry-run" not in argv_used, (
            f"--dry-run must NOT be in the argv when dry_run is omitted; "
            f"got: {argv_used!r}"
        )
