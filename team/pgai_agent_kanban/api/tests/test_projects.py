"""
test_projects.py — Tests for GET /projects/{name} metadata card endpoint.

Acceptance criteria verified:
  1. GET /projects/<fixture> returns 200 with every required field present.
  2. last_released in the response equals the value read by
     board_classifier.get_last_released_for_project (behavioral cross-check:
     the endpoint and the helper use the same source).
  3. queue_counts includes all five agent roles with {open, working, done}
     counts matching the fixture.
  4. GET /projects/does-not-exist returns 404 with the name echoed in the body.
  5. active_rc is null (JSON null) when release-state.md has "Active RC = none".

Test structure:
  - ``TestProjectMetadataEndpoint`` — acceptance-criteria tests.
  - Fixtures are materialised under pytest's tmp_path (no live kanban state).
  - No shell-outs; all state is file-based.
"""

from __future__ import annotations

import pathlib
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from pgai_agent_kanban.api.board_classifier import get_last_released_for_project

# ---------------------------------------------------------------------------
# Fixture constants
# ---------------------------------------------------------------------------

_PROJECT_NAME = "meta-test-project"
_PROJECT_COLOR = "#639922"
_PROJECT_PRIORITY = 5

_ACTIVE_RC_VERSION = "v1.6.0"
_LAST_RELEASED_VERSION = "v1.5.0"

# Task IDs designed to exercise all three queue-count buckets.
_WORKING_TASK = "CODER-20260707-001-working"
_DONE_TASK_1 = "CODER-20260707-002-done"
_DONE_TASK_2 = "PM-20260707-003-done"
_WONT_DO_TASK = "WRITER-20260707-004-wontdo"
_OPEN_TASK_1 = "CODER-20260707-005-open"
_OPEN_TASK_2 = "TESTER-20260707-006-open"
_BLOCKED_TASK = "CODER-20260707-007-blocked"


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write(path: pathlib.Path, text: str) -> pathlib.Path:
    """Write text to path, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _materialise_project_fixture(root: pathlib.Path) -> None:
    """Materialise a single-project kanban root under *root*.

    The project ``meta-test-project`` is configured with:
      - project.cfg: workflow_type=release, branch_prefix=ai_,
                     dev_tree_path, git_repo_url, version ceilings.
      - projects.cfg: priority=5, color=#639922.
      - release-state.md: Active RC = v1.6.0, Last Released = v1.5.0.
      - CODER queue: 1 working, 2 done (including 1 wont-do), 2 open, 1 blocked.
      - PM queue: 1 done.
      - Other agent queues: empty.
    """
    # projects.cfg
    _write(
        root / "projects.cfg",
        f"[project:{_PROJECT_NAME}]\n"
        f"priority={_PROJECT_PRIORITY}\n"
        f"dashboard_color={_PROJECT_COLOR}\n"
        f"dashboard_max_rows=20\n",
    )

    project_dir = root / "projects" / _PROJECT_NAME

    # project.cfg
    _write(
        project_dir / "project.cfg",
        f"[project]\n"
        f"project_name = {_PROJECT_NAME}\n"
        f"dev_tree_path = /tmp/fake-dev-tree\n"
        f"git_repo_url = git@github.com:example/meta-test.git\n"
        f"workflow_type = release\n"
        f"branch_prefix = ai_\n"
        f"\n"
        f"[versioning]\n"
        f"max_major = 0\n"
        f"max_minor = 13\n"
        f"max_patch = 21\n",
    )

    # release-state.md
    _write(
        project_dir / "release-state.md",
        f"# Release State\n\n"
        f"## Active RC\n{_ACTIVE_RC_VERSION}\n\n"
        f"## Last Released\n{_LAST_RELEASED_VERSION}\n\n"
        f"## State\nWORKING\n",
    )

    # CODER backlog: 1 working, 2 done, 1 wont-do (counted as done), 2 open, 1 blocked
    coder_backlog = (
        "# CODER Backlog\n\n"
        f"- [A] {_WORKING_TASK}\n"
        f"- [X] {_DONE_TASK_1}\n"
        f"- [ ] {_OPEN_TASK_1}\n"
        f"- [B] {_BLOCKED_TASK}\n"
        f"- [ ] {_OPEN_TASK_2}\n"
    )
    _write(project_dir / "tasks" / "queues" / "coder_backlog.md", coder_backlog)

    # Task status.md files for CODER tasks (confirms state override from status.md)
    _write(
        project_dir / "tasks" / _WORKING_TASK / "status.md",
        f"# Status\n\n## Task\n{_WORKING_TASK}\n\n## State\nWORKING\n",
    )
    _write(
        project_dir / "tasks" / _DONE_TASK_1 / "status.md",
        f"# Status\n\n## Task\n{_DONE_TASK_1}\n\n## State\nDONE\n",
    )
    _write(
        project_dir / "tasks" / _BLOCKED_TASK / "status.md",
        f"# Status\n\n## Task\n{_BLOCKED_TASK}\n\n## State\nBLOCKED\n",
    )
    # _OPEN_TASK_1, _OPEN_TASK_2: no status.md — falls back to queue marker.

    # PM backlog: 1 done task (_DONE_TASK_2)
    pm_backlog = (
        "# PM Backlog\n\n"
        f"- [X] {_DONE_TASK_2}\n"
    )
    _write(project_dir / "tasks" / "queues" / "pm_backlog.md", pm_backlog)
    _write(
        project_dir / "tasks" / _DONE_TASK_2 / "status.md",
        f"# Status\n\n## Task\n{_DONE_TASK_2}\n\n## State\nDONE\n",
    )

    # WRITER backlog: 1 wont-do task
    writer_backlog = (
        "# WRITER Backlog\n\n"
        f"- [X] {_WONT_DO_TASK}\n"
    )
    _write(project_dir / "tasks" / "queues" / "writer_backlog.md", writer_backlog)
    _write(
        project_dir / "tasks" / _WONT_DO_TASK / "status.md",
        f"# Status\n\n## Task\n{_WONT_DO_TASK}\n\n## State\nWONT-DO\n",
    )

    # Empty queues for tester and cm.
    for agent in ("tester", "cm"):
        _write(
            project_dir / "tasks" / "queues" / f"{agent}_backlog.md",
            f"# {agent.upper()} Backlog\n\n",
        )


# ---------------------------------------------------------------------------
# pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project_fixture_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """Materialise the single-project fixture root under tmp_path."""
    root = tmp_path / "meta_test_root"
    _materialise_project_fixture(root)
    return root


@pytest.fixture
def meta_client(
    project_fixture_root: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    """Create a TestClient bound to the single-project fixture root."""
    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(project_fixture_root))
    monkeypatch.setenv("KANBAN_ROOT", str(project_fixture_root))

    cfg = ApiConfig(
        host="127.0.0.1",
        port=0,
        kanban_root=project_fixture_root,
    )
    app = create_app(cfg=cfg)
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


# ---------------------------------------------------------------------------
# TestProjectMetadataEndpoint
# ---------------------------------------------------------------------------


class TestProjectMetadataEndpoint:
    """Acceptance-criteria tests for GET /projects/{name}."""

    # -----------------------------------------------------------------------
    # AC1: 200 with all required fields present
    # -----------------------------------------------------------------------

    def test_returns_200_for_known_project(
        self, meta_client: TestClient
    ) -> None:
        """GET /projects/<fixture> returns 200."""
        resp = meta_client.get(f"/projects/{_PROJECT_NAME}")
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )

    def test_all_required_fields_present(
        self, meta_client: TestClient
    ) -> None:
        """Response carries every Goal-2 field."""
        resp = meta_client.get(f"/projects/{_PROJECT_NAME}")
        assert resp.status_code == 200
        body = resp.json()

        required_top = {
            "name", "workflow_type", "branch_prefix", "dev_tree_path",
            "git_repo", "priority", "color", "ceilings", "last_released",
            "active_rc", "halt", "queue_counts",
        }
        missing = required_top - set(body.keys())
        assert not missing, f"Response missing top-level fields: {missing}"

        # ceilings sub-fields
        ceilings = body["ceilings"]
        required_ceilings = {"max_major", "max_minor", "max_patch"}
        missing_ceilings = required_ceilings - set(ceilings.keys())
        assert not missing_ceilings, f"ceilings missing: {missing_ceilings}"

        # queue_counts sub-fields
        queue_counts = body["queue_counts"]
        required_agents = {"PM", "CODER", "WRITER", "TESTER", "CM"}
        missing_agents = required_agents - set(queue_counts.keys())
        assert not missing_agents, f"queue_counts missing agents: {missing_agents}"

        # Each agent bucket must have open/working/done
        for agent, counts in queue_counts.items():
            required_buckets = {"open", "working", "done"}
            missing_buckets = required_buckets - set(counts.keys())
            assert not missing_buckets, (
                f"queue_counts[{agent!r}] missing buckets: {missing_buckets}"
            )

    def test_field_values_match_fixture(
        self, meta_client: TestClient
    ) -> None:
        """Field values match the fixture configuration."""
        resp = meta_client.get(f"/projects/{_PROJECT_NAME}")
        assert resp.status_code == 200
        body = resp.json()

        assert body["name"] == _PROJECT_NAME
        assert body["workflow_type"] == "release"
        assert body["branch_prefix"] == "ai_"
        assert body["dev_tree_path"] == "/tmp/fake-dev-tree"
        assert body["git_repo"] == "git@github.com:example/meta-test.git"
        assert body["priority"] == _PROJECT_PRIORITY
        assert body["color"] == _PROJECT_COLOR

    def test_ceilings_match_fixture(
        self, meta_client: TestClient
    ) -> None:
        """Version ceiling values match project.cfg [versioning]."""
        resp = meta_client.get(f"/projects/{_PROJECT_NAME}")
        body = resp.json()
        ceilings = body["ceilings"]

        assert ceilings["max_major"] == 0
        assert ceilings["max_minor"] == 13
        assert ceilings["max_patch"] == 21

    # -----------------------------------------------------------------------
    # AC2: last_released cross-check against board_classifier helper
    # -----------------------------------------------------------------------

    def test_last_released_matches_classifier_helper(
        self,
        meta_client: TestClient,
        project_fixture_root: pathlib.Path,
    ) -> None:
        """last_released equals the value board_classifier.get_last_released_for_project returns.

        This is the behavioral cross-check: the endpoint and the helper share the
        same resolution path (release-state.md ## Last Released), so they must
        always agree.
        """
        resp = meta_client.get(f"/projects/{_PROJECT_NAME}")
        assert resp.status_code == 200
        body = resp.json()

        project_dir = project_fixture_root / "projects" / _PROJECT_NAME
        helper_value = get_last_released_for_project(project_dir)
        # Helper returns "" for sentinels; endpoint emits null in that case.
        expected = helper_value if helper_value else None

        assert body["last_released"] == expected, (
            f"last_released mismatch: endpoint={body['last_released']!r}, "
            f"helper={expected!r}"
        )
        # Also verify the concrete value from the fixture.
        assert body["last_released"] == _LAST_RELEASED_VERSION

    # -----------------------------------------------------------------------
    # AC3: queue_counts match the fixture
    # -----------------------------------------------------------------------

    def test_coder_queue_counts_match_fixture(
        self, meta_client: TestClient
    ) -> None:
        """CODER queue counts match the fixture: 1 working, 1 done, 3 open (2 open + 1 blocked).

        Fixture CODER queue:
          _WORKING_TASK  → status.md: WORKING  → working
          _DONE_TASK_1   → status.md: DONE     → done
          _OPEN_TASK_1   → no status.md, marker ' ' → BACKLOG → open
          _BLOCKED_TASK  → status.md: BLOCKED  → open (blocked counts as open)
          _OPEN_TASK_2   → no status.md, marker ' ' → BACKLOG → open
        """
        resp = meta_client.get(f"/projects/{_PROJECT_NAME}")
        assert resp.status_code == 200
        body = resp.json()

        coder_counts = body["queue_counts"]["CODER"]
        assert coder_counts["working"] == 1, (
            f"Expected 1 working CODER task, got {coder_counts['working']}"
        )
        assert coder_counts["done"] == 1, (
            f"Expected 1 done CODER task, got {coder_counts['done']}"
        )
        assert coder_counts["open"] == 3, (
            f"Expected 3 open CODER tasks (2 open + 1 blocked), got {coder_counts['open']}"
        )

    def test_pm_queue_counts_match_fixture(
        self, meta_client: TestClient
    ) -> None:
        """PM queue counts: 0 working, 1 done, 0 open."""
        resp = meta_client.get(f"/projects/{_PROJECT_NAME}")
        body = resp.json()

        pm_counts = body["queue_counts"]["PM"]
        assert pm_counts["working"] == 0
        assert pm_counts["done"] == 1
        assert pm_counts["open"] == 0

    def test_writer_wont_do_task_counted_as_done(
        self, meta_client: TestClient
    ) -> None:
        """WONT-DO tasks are counted in the 'done' bucket (completed, not active)."""
        resp = meta_client.get(f"/projects/{_PROJECT_NAME}")
        body = resp.json()

        writer_counts = body["queue_counts"]["WRITER"]
        assert writer_counts["done"] == 1, (
            f"WONT-DO task should count as 'done'; got {writer_counts['done']}"
        )
        assert writer_counts["working"] == 0
        assert writer_counts["open"] == 0

    def test_empty_queue_agents_have_zero_counts(
        self, meta_client: TestClient
    ) -> None:
        """Agents with empty queues (tester, cm) report all-zero counts."""
        resp = meta_client.get(f"/projects/{_PROJECT_NAME}")
        body = resp.json()

        for agent in ("TESTER", "CM"):
            counts = body["queue_counts"][agent]
            assert counts == {"open": 0, "working": 0, "done": 0}, (
                f"{agent} expected all-zero counts, got {counts}"
            )

    # -----------------------------------------------------------------------
    # AC4: 404 for unknown project name
    # -----------------------------------------------------------------------

    def test_unknown_project_returns_404(
        self, meta_client: TestClient
    ) -> None:
        """GET /projects/does-not-exist returns 404."""
        resp = meta_client.get("/projects/does-not-exist")
        assert resp.status_code == 404, (
            f"Expected 404 for unknown project, got {resp.status_code}: {resp.text}"
        )

    def test_404_echoes_name_in_body(
        self, meta_client: TestClient
    ) -> None:
        """404 response echoes the requested name in the body."""
        bad_name = "does-not-exist"
        resp = meta_client.get(f"/projects/{bad_name}")
        assert resp.status_code == 404
        body = resp.json()
        assert "name" in body, f"404 body must include 'name' field: {body}"
        assert body["name"] == bad_name, (
            f"404 body must echo the requested name; got {body['name']!r}"
        )

    # -----------------------------------------------------------------------
    # AC5: active_rc is null when release-state.md has "none"
    # -----------------------------------------------------------------------

    def test_active_rc_is_set_when_release_state_has_version(
        self, meta_client: TestClient
    ) -> None:
        """active_rc is the vX.Y.Z string when release-state.md has a valid version."""
        resp = meta_client.get(f"/projects/{_PROJECT_NAME}")
        body = resp.json()
        assert body["active_rc"] == _ACTIVE_RC_VERSION, (
            f"Expected active_rc={_ACTIVE_RC_VERSION!r}, got {body['active_rc']!r}"
        )

    def test_active_rc_is_null_when_release_state_has_none(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """active_rc is JSON null (Python None) when release-state.md has 'none'."""
        root = tmp_path / "no_rc_root"
        proj_name = "no-rc-project"

        _write(
            root / "projects.cfg",
            f"[project:{proj_name}]\npriority=1\ndashboard_color=#378ADD\n",
        )
        proj_dir = root / "projects" / proj_name
        _write(
            proj_dir / "project.cfg",
            f"[project]\nproject_name = {proj_name}\n",
        )
        _write(
            proj_dir / "release-state.md",
            "# Release State\n\n## Active RC\nnone\n\n## Last Released\nnone\n",
        )
        # Empty queues
        for agent in ("pm", "coder", "writer", "tester", "cm"):
            _write(
                proj_dir / "tasks" / "queues" / f"{agent}_backlog.md",
                f"# {agent.upper()} Backlog\n\n",
            )

        from pgai_agent_kanban.api.app import create_app
        from pgai_agent_kanban.api.config import ApiConfig

        monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))
        cfg = ApiConfig(host="127.0.0.1", port=0, kanban_root=root)
        app = create_app(cfg=cfg)

        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get(f"/projects/{proj_name}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["active_rc"] is None, (
            f"active_rc must be null (not 'none') when release-state has 'none'; "
            f"got {body['active_rc']!r}"
        )

    def test_last_released_is_null_when_release_state_has_none(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """last_released is JSON null when release-state.md has 'none'."""
        root = tmp_path / "no_lr_root"
        proj_name = "no-lr-project"

        _write(
            root / "projects.cfg",
            f"[project:{proj_name}]\npriority=1\ndashboard_color=#378ADD\n",
        )
        proj_dir = root / "projects" / proj_name
        _write(
            proj_dir / "project.cfg",
            f"[project]\nproject_name = {proj_name}\n",
        )
        _write(
            proj_dir / "release-state.md",
            "# Release State\n\n## Active RC\nnone\n\n## Last Released\nnone\n",
        )
        for agent in ("pm", "coder", "writer", "tester", "cm"):
            _write(
                proj_dir / "tasks" / "queues" / f"{agent}_backlog.md",
                f"# {agent.upper()} Backlog\n\n",
            )

        from pgai_agent_kanban.api.app import create_app
        from pgai_agent_kanban.api.config import ApiConfig

        monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))
        cfg = ApiConfig(host="127.0.0.1", port=0, kanban_root=root)
        app = create_app(cfg=cfg)

        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get(f"/projects/{proj_name}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["last_released"] is None, (
            f"last_released must be null when release-state has 'none'; "
            f"got {body['last_released']!r}"
        )

    # -----------------------------------------------------------------------
    # Additional: halt flag, color, ceilings null when absent
    # -----------------------------------------------------------------------

    def test_halt_false_when_no_halt_file(
        self, meta_client: TestClient
    ) -> None:
        """halt is false when the HALT file is absent."""
        resp = meta_client.get(f"/projects/{_PROJECT_NAME}")
        body = resp.json()
        assert body["halt"] is False, (
            f"halt must be False when HALT file absent; got {body['halt']!r}"
        )

    def test_halt_true_when_halt_file_present(
        self,
        project_fixture_root: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """halt is true when the HALT file exists in the project directory."""
        halt_path = project_fixture_root / "projects" / _PROJECT_NAME / "HALT"
        halt_path.write_text("halted\n", encoding="utf-8")

        from pgai_agent_kanban.api.app import create_app
        from pgai_agent_kanban.api.config import ApiConfig

        monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(project_fixture_root))
        cfg = ApiConfig(host="127.0.0.1", port=0, kanban_root=project_fixture_root)
        app = create_app(cfg=cfg)

        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get(f"/projects/{_PROJECT_NAME}")

        body = resp.json()
        assert body["halt"] is True, (
            f"halt must be True when HALT file exists; got {body['halt']!r}"
        )

    def test_ceilings_null_when_project_cfg_absent(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Version ceilings are null when project.cfg has no [versioning] section."""
        root = tmp_path / "no_cfg_root"
        proj_name = "no-cfg-project"

        _write(
            root / "projects.cfg",
            f"[project:{proj_name}]\npriority=1\ndashboard_color=#378ADD\n",
        )
        proj_dir = root / "projects" / proj_name
        # project.cfg exists but has no [versioning] section
        _write(
            proj_dir / "project.cfg",
            f"[project]\nproject_name = {proj_name}\n",
        )
        _write(
            proj_dir / "release-state.md",
            "# Release State\n\n## Active RC\nnone\n\n## Last Released\nnone\n",
        )
        for agent in ("pm", "coder", "writer", "tester", "cm"):
            _write(
                proj_dir / "tasks" / "queues" / f"{agent}_backlog.md",
                f"# {agent.upper()} Backlog\n\n",
            )

        from pgai_agent_kanban.api.app import create_app
        from pgai_agent_kanban.api.config import ApiConfig

        monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))
        cfg = ApiConfig(host="127.0.0.1", port=0, kanban_root=root)
        app = create_app(cfg=cfg)

        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get(f"/projects/{proj_name}")

        assert resp.status_code == 200
        body = resp.json()
        ceilings = body["ceilings"]
        assert ceilings["max_major"] is None
        assert ceilings["max_minor"] is None
        assert ceilings["max_patch"] is None

    def test_color_uses_palette_fallback_when_absent_in_registry(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """color uses the deterministic palette fallback when not set in projects.cfg."""
        root = tmp_path / "palette_root"
        proj_name = "palette-project"

        # No dashboard_color in projects.cfg
        _write(
            root / "projects.cfg",
            f"[project:{proj_name}]\npriority=1\n",
        )
        proj_dir = root / "projects" / proj_name
        _write(proj_dir / "project.cfg", f"[project]\nproject_name = {proj_name}\n")
        _write(
            proj_dir / "release-state.md",
            "# Release State\n\n## Active RC\nnone\n\n## Last Released\nnone\n",
        )
        for agent in ("pm", "coder", "writer", "tester", "cm"):
            _write(
                proj_dir / "tasks" / "queues" / f"{agent}_backlog.md",
                f"# {agent.upper()} Backlog\n\n",
            )

        from pgai_agent_kanban.api.app import create_app
        from pgai_agent_kanban.api.config import ApiConfig

        monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))
        cfg = ApiConfig(host="127.0.0.1", port=0, kanban_root=root)
        app = create_app(cfg=cfg)

        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get(f"/projects/{proj_name}")

        assert resp.status_code == 200
        body = resp.json()
        # Should be the first palette color (index 0) since it's the first project.
        assert body["color"] == "#378ADD", (
            f"Expected first palette color '#378ADD', got {body['color']!r}"
        )
