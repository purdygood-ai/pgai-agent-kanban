"""
test_readme.py — Tests for GET /projects/{project}/tasks/{key}/readme.

Acceptance criteria verified:

  AC1. GET .../readme returns byte-identical README content for a valid
       (project, task-key) pair, in the standard envelope.
  AC2. Prefix-key resolution matches show.sh/show-readme.sh behavior (an
       agent+date+seq prefix resolves unambiguously to the full task key).
  AC3. A path-traversal fixture on the ``key`` or ``project`` path parameter
       returns 4xx (422) before any path is built or subprocess spawned.
  AC4. An unknown task key returns 404.
  AC5. A ledger key (bug/priority/requirement, not a task) returns 404 —
       READMEs are task artifacts only, matching show-readme.sh's refusal.
  AC6. The endpoint is listed in the generated OpenAPI/Swagger schema.

Test structure:
  - ``TestReadmeHappyPath``       — AC1, AC2: content round-trip + prefix resolution.
  - ``TestReadmeTraversalSecurity`` — AC3: traversal in path params → 422, no subprocess.
  - ``TestReadmeNotFound``        — AC4, AC5: unknown key and ledger key → 404.
  - ``TestReadmeOpenApiSurface``  — AC6: route appears in /openapi.json.

Fixtures materialise a synthetic kanban root under pytest's tmp_path with a
real task (README.md + status.md) and a real bug intake file. The API
config's kanban_root is that sandbox root -- the route reads the README
in-process through ``ops.read.get_task_readme`` from the server's own root --
and PGAI_AGENT_KANBAN_ROOT_PATH points at it too, so nothing reads the live
kanban state.
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

_PROJECT_NAME = "readme-test-project"
_TASK_KEY = "CODER-20260101-001-alpha"
_BUG_KEY = "BUG-0001"

_README_CONTENT = (
    "# Task: CODER-20260101-001-alpha\n\n"
    "## Task ID\nCODER-20260101-001-alpha\n\n"
    "## Owner\nClaude\n\n## Role\nCODER\n\n"
    "## Goal\nFixture task for the README endpoint test suite.\n"
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write(path: pathlib.Path, text: str) -> pathlib.Path:
    """Write text to path, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _materialise_readme_fixture(root: pathlib.Path) -> None:
    """Materialise a minimal kanban root with one task and one bug intake file.

    Layout:
      projects.cfg
      projects/readme-test-project/
          project.cfg
          tasks/<fixture-task-id>/{README.md, status.md}
          bugs/<fixture-bug-id>.md
    """
    _write(
        root / "projects.cfg",
        f"[project:{_PROJECT_NAME}]\npriority=1\ndashboard_color=#378ADD\n",
    )

    project_root = root / "projects" / _PROJECT_NAME
    _write(
        project_root / "project.cfg",
        f"[project]\nproject_name = {_PROJECT_NAME}\n",
    )

    task_dir = project_root / "tasks" / _TASK_KEY
    _write(task_dir / "README.md", _README_CONTENT)
    _write(
        task_dir / "status.md",
        f"# Status\n\n## Task\n{_TASK_KEY}\n\n## State\nBACKLOG\n\n"
        "## Summary\nnone\n\n## Artifacts\nnone\n\n## Blockers\nnone\n\n"
        "## Needs Human\nno\n",
    )

    _write(
        project_root / "bugs" / f"{_BUG_KEY}.md",
        f"# Bug: {_BUG_KEY}\n\n## Status\nopen\n\n## Summary\nFixture bug.\n",
    )


# ---------------------------------------------------------------------------
# pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def readme_fixture_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return a sandbox kanban_root with the readme fixture materialised."""
    root = tmp_path / "readme_test_root"
    _materialise_readme_fixture(root)
    return root


@pytest.fixture
def readme_client(
    readme_fixture_root: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    """TestClient whose API is rooted at the sandbox kanban root.

    ApiConfig.kanban_root is the sandbox root, where the route's in-process
    read finds the fixture project; PGAI_AGENT_KANBAN_ROOT_PATH and
    KANBAN_ROOT point at it as well, the same env separation every other
    fidelity-style fixture in this suite uses.
    """
    root = readme_fixture_root

    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))
    monkeypatch.setenv("KANBAN_ROOT", str(root))

    cfg = ApiConfig(host="127.0.0.1", port=0, kanban_root=root)
    app = create_app(cfg=cfg)
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


# ---------------------------------------------------------------------------
# TestReadmeHappyPath — AC1, AC2
# ---------------------------------------------------------------------------


class TestReadmeHappyPath:
    """Verify the happy-path content round-trip and prefix-key resolution."""

    def test_readme_returns_200(self, readme_client: TestClient) -> None:
        """GET .../readme for a valid (project, key) pair returns HTTP 200."""
        resp = readme_client.get(
            f"/projects/{_PROJECT_NAME}/tasks/{_TASK_KEY}/readme"
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    def test_readme_content_byte_identical(self, readme_client: TestClient) -> None:
        """AC1: stdout carries the README.md content byte-identical to the file."""
        resp = readme_client.get(
            f"/projects/{_PROJECT_NAME}/tasks/{_TASK_KEY}/readme"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["exit_code"] == 0
        assert body["stdout"] == _README_CONTENT, (
            f"README content mismatch:\nexpected: {_README_CONTENT!r}\n"
            f"got:      {body['stdout']!r}"
        )
        assert body["stderr"] == ""
        assert "warnings" in body

    def test_readme_prefix_key_resolves(self, readme_client: TestClient) -> None:
        """AC2: a shorter prefix key (agent+date+seq) resolves the same as the full key."""
        prefix_key = "CODER-20260101-001"
        resp = readme_client.get(
            f"/projects/{_PROJECT_NAME}/tasks/{prefix_key}/readme"
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["stdout"] == _README_CONTENT


# ---------------------------------------------------------------------------
# TestReadmeTraversalSecurity — AC3
# ---------------------------------------------------------------------------


class TestReadmeTraversalSecurity:
    """Traversal-shaped path parameters return 4xx before any path is built.

    Two distinct confinement layers are exercised here, both landing on a
    4xx status with no subprocess ever spawned:

    1. A slash (raw or ``%2F``-decoded) in ``{key}``/``{project}`` breaks
       Starlette's single-segment path-parameter match entirely — the
       request never reaches this router's handler and 404s at the routing
       layer.  This is the same confinement precedent
       ``TestTracesOpaqueIdSecurity`` documents for ``GET /traces/{id}``.
    2. A traversal-shaped value with NO slash (e.g. a bare ``..``) does
       reach the handler and is caught by this endpoint's own
       ``has_traversal`` gate, which returns 422 before the read runs.
    """

    def _assert_4xx_no_subprocess(
        self,
        client: TestClient,
        url: str,
        run_mock: unittest.mock.MagicMock,
    ) -> None:
        resp = client.get(url)
        assert 400 <= resp.status_code < 500, (
            f"Expected a 4xx for traversal attempt; got {resp.status_code}: {resp.text}"
        )
        assert not run_mock.called, (
            f"subprocess.run must NOT be called for a traversal attempt; "
            f"was called with: {run_mock.call_args_list}"
        )

    def test_key_dotdot_slash_traversal_blocked_at_routing_layer(
        self, readme_client: TestClient
    ) -> None:
        """key=../../etc/passwd (URL-encoded slash) → 4xx; no subprocess spawned.

        The slash breaks Starlette's single-segment {key} match, so this
        404s at the routing layer before the handler (and its own traversal
        gate) is ever reached.
        """
        with unittest.mock.patch(
            "pgai_agent_kanban.api.adapter.subprocess.run"
        ) as run_mock:
            self._assert_4xx_no_subprocess(
                readme_client,
                f"/projects/{_PROJECT_NAME}/tasks/..%2F..%2Fetc%2Fpasswd/readme",
                run_mock,
            )

    def test_key_bare_dotdot_blocked_by_handler_gate(
        self, readme_client: TestClient
    ) -> None:
        """key='..' (no slash — reaches the handler) → 422 from the traversal gate."""
        with unittest.mock.patch(
            "pgai_agent_kanban.api.adapter.subprocess.run"
        ) as run_mock:
            resp = readme_client.get(f"/projects/{_PROJECT_NAME}/tasks/../readme")
            # A bare ".." key segment is itself special to URL path semantics
            # (it collapses at the client/normalisation layer in some HTTP
            # stacks); assert whichever 4xx results and that no subprocess ran.
            assert 400 <= resp.status_code < 500, (
                f"Expected a 4xx for a bare '..' key; got {resp.status_code}: {resp.text}"
            )
            assert not run_mock.called

    def test_key_encoded_dotdot_no_slash_blocked_by_handler_gate(
        self, readme_client: TestClient
    ) -> None:
        """key with a literal '..' but no slash reaches the handler and is
        rejected by has_traversal with 422."""
        with unittest.mock.patch(
            "pgai_agent_kanban.api.adapter.subprocess.run"
        ) as run_mock:
            resp = readme_client.get(
                f"/projects/{_PROJECT_NAME}/tasks/CODER-..-001/readme"
            )
            assert resp.status_code == 422, (
                f"Expected 422 for a '..'-bearing key; got {resp.status_code}: {resp.text}"
            )
            assert not run_mock.called, (
                f"subprocess.run must NOT be called for a traversal attempt; "
                f"was called with: {run_mock.call_args_list}"
            )

    def test_project_dotdot_slash_traversal_blocked_at_routing_layer(
        self, readme_client: TestClient
    ) -> None:
        """project=../../etc (URL-encoded slash) → 4xx; no subprocess spawned."""
        with unittest.mock.patch(
            "pgai_agent_kanban.api.adapter.subprocess.run"
        ) as run_mock:
            self._assert_4xx_no_subprocess(
                readme_client,
                f"/projects/..%2F..%2Fetc/tasks/{_TASK_KEY}/readme",
                run_mock,
            )

    def test_project_encoded_dotdot_no_slash_blocked_by_handler_gate(
        self, readme_client: TestClient
    ) -> None:
        """project with a literal '..' but no slash reaches the handler and is
        rejected by has_traversal with 422."""
        with unittest.mock.patch(
            "pgai_agent_kanban.api.adapter.subprocess.run"
        ) as run_mock:
            resp = readme_client.get(
                f"/projects/my..project/tasks/{_TASK_KEY}/readme"
            )
            assert resp.status_code == 422, (
                f"Expected 422 for a '..'-bearing project; got {resp.status_code}: {resp.text}"
            )
            assert not run_mock.called


# ---------------------------------------------------------------------------
# TestReadmeNotFound — AC4, AC5
# ---------------------------------------------------------------------------


class TestReadmeNotFound:
    """Unknown task keys and non-task ledger keys both return 404."""

    def test_unknown_task_key_returns_404(self, readme_client: TestClient) -> None:
        """AC4: a fabricated task key returns 404."""
        resp = readme_client.get(
            f"/projects/{_PROJECT_NAME}/tasks/CODER-99999999-999-nope/readme"
        )
        assert resp.status_code == 404, (
            f"Expected 404 for unknown key; got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body["key"] == "CODER-99999999-999-nope"

    def test_ledger_key_returns_404(self, readme_client: TestClient) -> None:
        """AC5: a bug (ledger) key is not a task — no README resource; 404."""
        resp = readme_client.get(
            f"/projects/{_PROJECT_NAME}/tasks/{_BUG_KEY}/readme"
        )
        assert resp.status_code == 404, (
            f"Expected 404 for ledger key; got {resp.status_code}: {resp.text}"
        )

    def test_unknown_project_returns_404(self, readme_client: TestClient) -> None:
        """An unregistered project also yields no README resource — 404."""
        resp = readme_client.get(
            f"/projects/no-such-project/tasks/{_TASK_KEY}/readme"
        )
        assert resp.status_code == 404, (
            f"Expected 404 for unknown project; got {resp.status_code}: {resp.text}"
        )


# ---------------------------------------------------------------------------
# TestReadmeOpenApiSurface — AC6
# ---------------------------------------------------------------------------


class TestReadmeOpenApiSurface:
    """The endpoint must appear in the generated OpenAPI/Swagger schema."""

    def test_readme_route_in_openapi_schema(self, readme_client: TestClient) -> None:
        """GET /openapi.json lists the readme path with a GET operation."""
        resp = readme_client.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        paths = schema.get("paths", {})
        assert "/projects/{project}/tasks/{key}/readme" in paths, (
            f"readme route missing from OpenAPI schema; paths: {sorted(paths)}"
        )
        assert "get" in paths["/projects/{project}/tasks/{key}/readme"]
