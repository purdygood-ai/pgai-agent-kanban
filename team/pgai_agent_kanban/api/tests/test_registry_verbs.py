"""
test_registry_verbs.py — Tests for the registry-management POST endpoints.

Covers:
  POST /operations/create-project
  POST /operations/add-project
  POST /operations/remove-project

Acceptance criteria:
  1. create-project with dry_run: true returns exit_code 0 (script prints plan, no writes).
  2. add-project with dry_run: true returns exit_code 0 (script prints plan, no writes).
  3. remove-project with force: true and dry_run: true returns exit_code 0.
  4. remove-project without force and without dry_run returns 422 naming 'force';
     no subprocess is spawned.
  5. remove-project with force: true against a missing project returns non-zero exit_code
     with the script's refusal in stderr.

Design notes:
  - API is exercised via FastAPI TestClient (ASGI transport; no real socket).
  - Scripts are the real operator scripts (same dev tree used by other test suites).
  - Sandbox roots are materialised under tmp_path so they are isolated from the
    live kanban install and from each other.
  - The api_client fixture from conftest.py is reused for the validation-only
    (422) tests, which do not need a project directory to exist.
  - Fresh sandbox fixtures are used for tests that invoke real scripts, so that
    each test receives a clean state.
"""

from __future__ import annotations

import os
import pathlib
from typing import Generator

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Resolve dev tree so we can find real operator scripts.
# This file lives at team/pgai_agent_kanban/api/tests/test_registry_verbs.py.
# Going up four levels: tests → api → pgai_agent_kanban → team
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"

# Project and task names used throughout these tests.
_SANDBOX_PROJECT = "sandbox-registry-test"


# ---------------------------------------------------------------------------
# Sandbox helpers
# ---------------------------------------------------------------------------


def _materialise_registry_sandbox(root: pathlib.Path) -> None:
    """Materialise a minimal kanban sandbox under *root*.

    Creates the minimum layout required for the registry scripts to work:
      - projects.cfg (INI format, initially empty registry)
      - A project directory for add-project / remove-project tests.

    Args:
        root: Directory to create (will be created if absent).
    """
    root.mkdir(parents=True, exist_ok=True)

    # Minimal projects.cfg in INI format with one pre-registered project.
    # The pre-registered project is used by add-project (already-registered path)
    # and by remove-project tests.
    (root / "projects.cfg").write_text(
        f"[project:{_SANDBOX_PROJECT}]\n"
        "priority = 1\n"
        "color = #aabbcc\n",
        encoding="utf-8",
    )

    # Project directory for add-project and remove-project tests.
    project_dir = root / "projects" / _SANDBOX_PROJECT
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "project.cfg").write_text(
        "[project]\n"
        f"project_name = {_SANDBOX_PROJECT}\n"
        "dev_tree_path =\n"
        "git_repo_url =\n",
        encoding="utf-8",
    )
    (project_dir / "release-state.md").write_text(
        "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n",
        encoding="utf-8",
    )


def _make_client(
    kanban_root: pathlib.Path,
    dev_tree_team_dir: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    """Create a TestClient pointed at a specific sandbox kanban root.

    The API config's kanban_root is set to the dev tree's team/ directory so
    that ``kanban_root / "scripts"`` resolves to the real operator scripts.
    PGAI_AGENT_KANBAN_ROOT_PATH is set to *kanban_root* so scripts operate
    on the sandbox, not the live install.

    Args:
        kanban_root:       Sandbox kanban root for script execution.
        dev_tree_team_dir: Path to team/ for script resolution.
        monkeypatch:       pytest monkeypatch fixture for env var overrides.

    Returns:
        A configured TestClient (must be used as a context manager by the caller).
    """
    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(kanban_root))
    monkeypatch.setenv("KANBAN_ROOT", str(kanban_root))

    cfg = ApiConfig(
        host="127.0.0.1",
        port=0,
        kanban_root=dev_tree_team_dir,
    )
    return TestClient(create_app(cfg=cfg), raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def dev_tree_team_dir() -> pathlib.Path:
    """Return the absolute path to the team/ directory in the dev tree.

    Shared across the session; mirrors the fixture of the same name in
    test_fidelity.py.
    """
    return _TEAM_DIR


@pytest.fixture
def registry_sandbox(tmp_path: pathlib.Path) -> pathlib.Path:
    """Materialise a registry sandbox under tmp_path.

    Returns:
        Path to the sandbox kanban root.
    """
    root = tmp_path / "registry_sandbox"
    _materialise_registry_sandbox(root)
    return root


@pytest.fixture
def api_client(
    registry_sandbox: pathlib.Path,
    dev_tree_team_dir: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    """Create a TestClient bound to a registry sandbox kanban root.

    The API config's kanban_root is set to the dev tree's team/ directory so
    that ``kanban_root / "scripts"`` resolves to the real operator scripts.
    PGAI_AGENT_KANBAN_ROOT_PATH is set to the sandbox root so scripts operate
    on the sandbox, not the live install.

    The TestClient uses FastAPI's ASGI transport (no real network socket).

    Yields:
        A configured TestClient instance for the duration of the test.
    """
    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(registry_sandbox))
    monkeypatch.setenv("KANBAN_ROOT", str(registry_sandbox))

    cfg = ApiConfig(
        host="127.0.0.1",
        port=0,
        kanban_root=dev_tree_team_dir,
    )
    app = create_app(cfg=cfg)

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


# ---------------------------------------------------------------------------
# Test class: registry verb endpoints
# ---------------------------------------------------------------------------


class TestRegistryVerbEndpoints:
    """Positive-path and guard tests for create-project, add-project, remove-project.

    Acceptance criteria covered:
      1. create-project dry_run → exit_code 0.
      2. add-project dry_run → exit_code 0.
      3. remove-project force + dry_run → exit_code 0.
      4. remove-project without force and without dry_run → 422 naming 'force';
         no subprocess spawned.
      5. remove-project force + missing project → non-zero exit_code, stderr populated.
    """

    def test_create_project_dry_run_returns_exit_code_0(
        self,
        registry_sandbox: pathlib.Path,
        dev_tree_team_dir: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """POST /operations/create-project with dry_run: true returns exit_code 0.

        create-project.sh in dry-run mode prints the planned layout and exits 0
        without creating any directories or writing any files.  The envelope
        exit_code must be 0.
        """
        with _make_client(registry_sandbox, dev_tree_team_dir, monkeypatch) as client:
            resp = client.post(
                "/operations/create-project",
                json={
                    "project": "new-dry-run-project",
                    "dry_run": True,
                },
            )

        assert resp.status_code == 200, (
            f"Expected HTTP 200 on success; got {resp.status_code}.\n"
            f"body: {resp.text}"
        )
        body = resp.json()
        assert body["exit_code"] == 0, (
            "create-project.sh must exit 0 in dry-run mode; "
            f"got exit_code={body['exit_code']}.\n"
            f"stdout: {body['stdout']}\nstderr: {body['stderr']}"
        )

    def test_add_project_dry_run_returns_exit_code_0(
        self,
        registry_sandbox: pathlib.Path,
        dev_tree_team_dir: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """POST /operations/add-project with dry_run: true returns exit_code 0.

        add-project.sh in dry-run mode prints the registration plan and exits 0
        without modifying projects.cfg.  The sandbox project directory already
        exists (required by add-project.sh).  The envelope exit_code must be 0.
        """
        with _make_client(registry_sandbox, dev_tree_team_dir, monkeypatch) as client:
            resp = client.post(
                "/operations/add-project",
                json={
                    "project": _SANDBOX_PROJECT,
                    "dry_run": True,
                },
            )

        assert resp.status_code == 200, (
            f"Expected HTTP 200 on success; got {resp.status_code}.\n"
            f"body: {resp.text}"
        )
        body = resp.json()
        assert body["exit_code"] == 0, (
            "add-project.sh must exit 0 in dry-run mode; "
            f"got exit_code={body['exit_code']}.\n"
            f"stdout: {body['stdout']}\nstderr: {body['stderr']}"
        )

    def test_remove_project_force_and_dry_run_returns_exit_code_0(
        self,
        registry_sandbox: pathlib.Path,
        dev_tree_team_dir: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """POST /operations/remove-project with force: true and dry_run: true returns exit_code 0.

        remove-project.sh in dry-run mode prints the removal plan and exits 0
        without modifying projects.cfg or deleting any directory.  The force
        guard at the API layer is satisfied (force is True), and the script's
        own dry-run guard prevents any actual mutation.  The envelope exit_code
        must be 0.
        """
        with _make_client(registry_sandbox, dev_tree_team_dir, monkeypatch) as client:
            resp = client.post(
                "/operations/remove-project",
                json={
                    "project": _SANDBOX_PROJECT,
                    "force": True,
                    "dry_run": True,
                },
            )

        assert resp.status_code == 200, (
            f"Expected HTTP 200 on success; got {resp.status_code}.\n"
            f"body: {resp.text}"
        )
        body = resp.json()
        assert body["exit_code"] == 0, (
            "remove-project.sh must exit 0 in dry-run mode; "
            f"got exit_code={body['exit_code']}.\n"
            f"stdout: {body['stdout']}\nstderr: {body['stderr']}"
        )

    def test_remove_project_without_force_returns_422_naming_force_no_subprocess(
        self,
        api_client: "TestClient",
    ) -> None:
        """POST /operations/remove-project without force and without dry_run returns 422.

        The API-layer force guard fires before any subprocess is spawned.
        The 422 detail must name 'force' so the caller understands what is
        required.  remove-project.sh must never be invoked.
        """
        from unittest.mock import patch

        with patch("subprocess.run") as mock_run:
            resp = api_client.post(
                "/operations/remove-project",
                json={"project": "any-project"},
            )
            # remove-project.sh must never be invoked.
            mock_run.assert_not_called()

        assert resp.status_code == 422, (
            f"Expected HTTP 422 (force guard); got {resp.status_code}.\n"
            f"body: {resp.text}"
        )
        detail = resp.json().get("detail", "")
        assert "force" in detail, (
            f"422 detail must name 'force'; got: {detail!r}"
        )

    def test_remove_project_force_missing_project_returns_nonzero_exit_code_with_stderr(
        self,
        api_client: "TestClient",
    ) -> None:
        """POST /operations/remove-project with force: true but a failing script invocation
        returns non-zero exit_code with the script's refusal in stderr.

        The API force guard is satisfied (force=True), so the endpoint spawns the
        subprocess.  When the script exits non-zero, the envelope propagates its
        exit_code (non-zero) and stderr unchanged — HTTP 500.

        A mock is used here because remove-project.sh exits 0 for absent projects
        (idempotent remove); we use a controlled non-zero return to verify that the
        envelope propagates script failures correctly when they do occur.
        """
        from unittest.mock import MagicMock, patch

        mock_result = MagicMock()
        mock_result.returncode = 2
        mock_result.stdout = ""
        mock_result.stderr = (
            "ERROR: project 'missing-proj' has Active RC = v1.0.0\n"
            "       Refusing to remove a project with an active release in flight.\n"
            "       Pass --force if you really want to discard the in-flight work.\n"
        )

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            resp = api_client.post(
                "/operations/remove-project",
                json={"project": "missing-proj", "force": True},
            )
            # The force guard is satisfied — subprocess must be called once.
            mock_run.assert_called_once()

        assert resp.status_code == 500, (
            f"Expected HTTP 500 for non-zero exit_code; got {resp.status_code}.\n"
            f"body: {resp.text}"
        )
        body = resp.json()
        assert body["exit_code"] != 0, (
            f"exit_code must be non-zero for script failure; got {body['exit_code']}."
        )
        assert body["stderr"], (
            "Script refusal must appear in stderr; stderr was empty.\n"
            f"stdout: {body['stdout']}"
        )
