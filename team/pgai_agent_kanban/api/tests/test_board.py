"""
test_board.py — Tests for GET /board and the classification-parity guarantee.

Two suites:

1. **Endpoint tests** (``TestBoardEndpoint``):
   - 200 with all eight columns present in the correct order.
   - Both projects from a two-project fixture appear in the projects list with
     distinct color strings.
   - Composite ids are ``<project>/<kind>/<key>`` verbatim.
   - At least one ``active_rc: true`` item, one ``label`` status item, one
     ``blocked`` status item, and one ``kind=quarantine`` item.
   - ``GET /board?project=x`` returns 422.
   - Per-column ``{truncated: N}`` appears when the per-project cap is exceeded.
   - CORS: loopback origin receives CORS headers; non-loopback does not.

2. **Parity-pin test** (``TestBoardClassifierParity``):
   - For the same fixture inputs, the ``status`` field returned by ``GET /board``
     equals the status produced by calling ``board_classifier`` functions directly.
   - This is the load-bearing artifact: it ensures a future edit to either the
     board handler or the classifier module does not silently diverge.

Design notes
------------
- All tests use FastAPI's TestClient (ASGI transport, no real port).
- Fixtures are materialised under pytest's tmp_path (routed through the
  framework temp root by conftest.py).
- No bare /tmp paths.
- Tests are hermetic: no live kanban state, no shell-outs.
"""

from __future__ import annotations

import pathlib
import re
from typing import Generator

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Classifier imports (parity-pin test uses these directly)
# ---------------------------------------------------------------------------
from pgai_agent_kanban.api.board_classifier import (
    classify_input_item,
    classify_queue_item,
    get_active_rc_for_project,
    get_last_released_for_project,
    marker_to_state,
    parse_queue_line,
    read_task_state_from_status_md,
    COLUMN_ORDER,
)

# ---------------------------------------------------------------------------
# Fixture constants
# ---------------------------------------------------------------------------

_PROJECT_ALPHA = "alpha-project"
_PROJECT_BETA = "beta-project"
_COLOR_ALPHA = "#378ADD"
_COLOR_BETA = "#E24B4A"

_ACTIVE_RC_VERSION = "v1.6.0"
_LAST_RELEASED_VERSION = "v1.5.0"
_SHIPPED_REQ_VERSION = "v1.5.0"
_ACTIVE_REQ_VERSION = _ACTIVE_RC_VERSION

# Task IDs used in the fixture
_WORKING_TASK = "CODER-20260101-001-working"
_BLOCKED_TASK = "CODER-20260101-002-blocked"
_DONE_TASK = "CODER-20260101-003-done"
_OPEN_TASK = "CODER-20260101-004-open"


# ---------------------------------------------------------------------------
# Fixture materialisation
# ---------------------------------------------------------------------------


def _write(path: pathlib.Path, text: str) -> pathlib.Path:
    """Write text to path, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _materialise_two_project_fixture(root: pathlib.Path) -> None:
    """Materialise a two-project kanban root under *root*.

    Projects:
      - alpha-project: color #378ADD, has bugs, priorities, requirements, and
        agent queue tasks.  Includes one quarantined bug.
      - beta-project: color #E24B4A, has a single open bug.

    Items designed to cover all acceptance criteria:
      - One item with status "label" (requirement targeting active RC).
      - One item with active_rc=True (WORKING task).
      - One item with status "blocked" (BLOCKED task + quarantine item).
      - One item with kind=quarantine (quarantined bug).
      - One item with status "done" (shipped requirement, DONE task).
    """
    # projects.cfg
    _write(
        root / "projects.cfg",
        f"[project:{_PROJECT_ALPHA}]\n"
        f"priority=1\n"
        f"description=Alpha test project\n"
        f"enabled=true\n"
        f"dashboard_color={_COLOR_ALPHA}\n"
        f"dashboard_max_rows=20\n"
        f"\n"
        f"[project:{_PROJECT_BETA}]\n"
        f"priority=2\n"
        f"description=Beta test project\n"
        f"enabled=true\n"
        f"dashboard_color={_COLOR_BETA}\n"
        f"dashboard_max_rows=20\n",
    )

    # -----------------------------------------------------------------------
    # Alpha project
    # -----------------------------------------------------------------------
    alpha = root / "projects" / _PROJECT_ALPHA

    # release-state.md: active RC set so requirements items get "label" status.
    _write(
        alpha / "release-state.md",
        f"# Release State\n\n"
        f"## Active RC\n{_ACTIVE_RC_VERSION}\n\n"
        f"## Last Released\n{_LAST_RELEASED_VERSION}\n\n"
        f"## State\nWORKING\n",
    )

    # project.cfg
    _write(
        alpha / "project.cfg",
        f"[project]\nproject_name = {_PROJECT_ALPHA}\n"
        f"dev_tree_path = {root}\ngit_repo_url = none\n",
    )

    # Bugs: one open bug
    _write(
        alpha / "bugs" / "BUG-0001.md",
        "# Bug: BUG-0001\n\n## Status\nopen\n\n## Summary\nTest bug.\n",
    )
    # Quarantined bug: in bugs/.rejected/
    _write(
        alpha / "bugs" / ".rejected" / "BUG-9999.md",
        "# Bug: BUG-9999\n\n## Status\nopen\n\n## Summary\nQuarantined.\n",
    )

    # Priorities: one open priority item
    _write(
        alpha / "priority" / "PRIORITY-0001.md",
        "# Priority: PRIORITY-0001\n\n## Status\nopen\n\n## Summary\nTest priority.\n",
    )

    # Requirements:
    #  - v1.6.0: targets active RC → status="label"
    #  - v1.5.0: shipped (last_released >= v1.5.0) → status="done"
    _write(
        alpha / "requirements" / f"{_ACTIVE_REQ_VERSION}-board-endpoint.md",
        f"# Requirements: {_ACTIVE_REQ_VERSION}\n\n"
        f"## Status\nrunning\n\n"
        f"## Target Version\n{_ACTIVE_REQ_VERSION}\n\n"
        f"## Summary\nActive RC requirement.\n",
    )
    _write(
        alpha / "requirements" / f"{_SHIPPED_REQ_VERSION}-shipped.md",
        f"# Requirements: {_SHIPPED_REQ_VERSION}\n\n"
        f"## Status\ndone\n\n"
        f"## Target Version\n{_SHIPPED_REQ_VERSION}\n\n"
        f"## Summary\nShipped requirement.\n",
    )

    # Coder queue backlog: four tasks in different states
    coder_backlog = (
        "# CODER Backlog\n\n"
        f"- [A] {_WORKING_TASK}\n"
        f"- [B] {_BLOCKED_TASK}\n"
        f"- [X] {_DONE_TASK}\n"
        f"- [ ] {_OPEN_TASK}\n"
    )
    _write(alpha / "tasks" / "queues" / "coder_backlog.md", coder_backlog)

    # Task status.md files — confirms state reading from status.md.
    _write(
        alpha / "tasks" / _WORKING_TASK / "status.md",
        f"# Status\n\n## Task\n{_WORKING_TASK}\n\n## State\nWORKING\n\n"
        "## Summary\nIn progress.\n",
    )
    _write(
        alpha / "tasks" / _BLOCKED_TASK / "status.md",
        f"# Status\n\n## Task\n{_BLOCKED_TASK}\n\n## State\nBLOCKED\n\n"
        "## Summary\nBlocked.\n\n## Blockers\nWaiting on upstream.\n",
    )
    _write(
        alpha / "tasks" / _DONE_TASK / "status.md",
        f"# Status\n\n## Task\n{_DONE_TASK}\n\n## State\nDONE\n\n"
        "## Summary\nDone.\n",
    )
    # OPEN task: no status.md — falls back to marker ( = BACKLOG → "open").

    # Empty queues for other agents so the endpoint doesn't error.
    for agent in ("pm", "writer", "tester", "cm"):
        _write(
            alpha / "tasks" / "queues" / f"{agent}_backlog.md",
            f"# {agent.upper()} Backlog\n\n",
        )

    # -----------------------------------------------------------------------
    # Beta project
    # -----------------------------------------------------------------------
    beta = root / "projects" / _PROJECT_BETA

    _write(
        beta / "release-state.md",
        "# Release State\n\n## Active RC\nnone\n\n## Last Released\nnone\n\n## State\nIDLE\n",
    )
    _write(
        beta / "project.cfg",
        f"[project]\nproject_name = {_PROJECT_BETA}\n"
        f"dev_tree_path = {root}\ngit_repo_url = none\n",
    )
    _write(
        beta / "bugs" / "BUG-0001.md",
        "# Bug: BUG-0001\n\n## Status\nopen\n\n## Summary\nBeta test bug.\n",
    )
    # Empty queues for beta project.
    for agent in ("pm", "coder", "writer", "tester", "cm"):
        _write(
            beta / "tasks" / "queues" / f"{agent}_backlog.md",
            f"# {agent.upper()} Backlog\n\n",
        )


# ---------------------------------------------------------------------------
# pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def two_project_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """Materialise a two-project kanban root under tmp_path."""
    root = tmp_path / "board_fixture"
    _materialise_two_project_fixture(root)
    return root


@pytest.fixture
def board_client(
    two_project_root: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    """Create a TestClient bound to the two-project fixture root."""
    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(two_project_root))
    monkeypatch.setenv("KANBAN_ROOT", str(two_project_root))

    cfg = ApiConfig(
        host="127.0.0.1",
        port=0,
        kanban_root=two_project_root,
    )
    app = create_app(cfg=cfg)
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


# ---------------------------------------------------------------------------
# TestBoardEndpoint
# ---------------------------------------------------------------------------


class TestBoardEndpoint:
    """Acceptance-criteria tests for GET /board."""

    def test_returns_200_with_eight_columns_in_order(
        self,
        board_client: TestClient,
    ) -> None:
        """GET /board returns 200 with all eight columns in the required order."""
        resp = board_client.get("/board")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        body = resp.json()
        assert "columns" in body, "Response must have 'columns' key."
        column_names = [c["name"] for c in body["columns"]]
        assert column_names == COLUMN_ORDER, (
            f"Column order mismatch.\n"
            f"Expected: {COLUMN_ORDER}\n"
            f"Got:      {column_names}"
        )

    def test_both_projects_in_projects_list_with_distinct_colors(
        self,
        board_client: TestClient,
    ) -> None:
        """Both projects appear in the projects list with distinct color strings."""
        resp = board_client.get("/board")
        assert resp.status_code == 200

        body = resp.json()
        projects = body["projects"]
        names = [p["name"] for p in projects]
        assert _PROJECT_ALPHA in names, f"{_PROJECT_ALPHA!r} missing from projects list."
        assert _PROJECT_BETA in names, f"{_PROJECT_BETA!r} missing from projects list."

        # Colors must be distinct.
        colors = {p["name"]: p["color"] for p in projects}
        assert colors[_PROJECT_ALPHA] != colors[_PROJECT_BETA], (
            f"Projects must have distinct colors; both got {colors[_PROJECT_ALPHA]!r}."
        )
        # Colors must be non-empty strings.
        assert colors[_PROJECT_ALPHA], f"{_PROJECT_ALPHA} color is empty."
        assert colors[_PROJECT_BETA], f"{_PROJECT_BETA} color is empty."

    def test_projects_carry_verbatim_registry_colors(
        self,
        board_client: TestClient,
    ) -> None:
        """Color strings are taken verbatim from the registry (not CSS)."""
        resp = board_client.get("/board")
        body = resp.json()
        colors = {p["name"]: p["color"] for p in body["projects"]}

        assert colors[_PROJECT_ALPHA] == _COLOR_ALPHA, (
            f"alpha color: expected {_COLOR_ALPHA!r}, got {colors[_PROJECT_ALPHA]!r}."
        )
        assert colors[_PROJECT_BETA] == _COLOR_BETA, (
            f"beta color: expected {_COLOR_BETA!r}, got {colors[_PROJECT_BETA]!r}."
        )
        # Colors must not contain 'rgb' or CSS syntax.
        for name, color in colors.items():
            assert "rgb" not in color.lower(), (
                f"Project {name!r} color contains CSS syntax: {color!r}."
            )

    def test_composite_ids_are_project_slash_kind_slash_key(
        self,
        board_client: TestClient,
    ) -> None:
        """All item ids have the form <project>/<kind>/<key>."""
        resp = board_client.get("/board")
        body = resp.json()
        id_re = re.compile(r"^[^/]+/[^/]+/[^/]+$")
        for col in body["columns"]:
            for item in col["items"]:
                assert id_re.match(item["id"]), (
                    f"Item id {item['id']!r} does not match <project>/<kind>/<key> format."
                )
                # The id prefix must equal item["project"].
                prefix = item["id"].split("/")[0]
                assert prefix == item["project"], (
                    f"Id prefix {prefix!r} != project {item['project']!r} for id {item['id']!r}."
                )

    def test_at_least_one_active_rc_true_item(
        self,
        board_client: TestClient,
    ) -> None:
        """At least one item has active_rc=True."""
        resp = board_client.get("/board")
        body = resp.json()
        all_items = [item for col in body["columns"] for item in col["items"]]
        active_rc_items = [i for i in all_items if i["active_rc"]]
        assert active_rc_items, (
            "Expected at least one item with active_rc=True. "
            f"Statuses found: {[i['status'] for i in all_items]}"
        )

    def test_at_least_one_item_classified_label(
        self,
        board_client: TestClient,
    ) -> None:
        """At least one item has status='label' (requirements targeting active RC)."""
        resp = board_client.get("/board")
        body = resp.json()
        all_items = [item for col in body["columns"] for item in col["items"]]
        label_items = [i for i in all_items if i["status"] == "label"]
        assert label_items, (
            "Expected at least one item with status='label'. "
            f"Statuses found: {sorted({i['status'] for i in all_items})}"
        )

    def test_at_least_one_item_classified_blocked(
        self,
        board_client: TestClient,
    ) -> None:
        """At least one item has status='blocked'."""
        resp = board_client.get("/board")
        body = resp.json()
        all_items = [item for col in body["columns"] for item in col["items"]]
        blocked_items = [i for i in all_items if i["status"] == "blocked"]
        assert blocked_items, (
            "Expected at least one item with status='blocked'. "
            f"Statuses found: {sorted({i['status'] for i in all_items})}"
        )

    def test_at_least_one_quarantine_item(
        self,
        board_client: TestClient,
    ) -> None:
        """At least one item has kind=quarantine."""
        resp = board_client.get("/board")
        body = resp.json()
        all_items = [item for col in body["columns"] for item in col["items"]]
        quarantine_items = [i for i in all_items if i["kind"] == "quarantine"]
        assert quarantine_items, (
            "Expected at least one item with kind='quarantine'. "
            f"Kinds found: {sorted({i['kind'] for i in all_items})}"
        )
        # Quarantine items must have status=blocked.
        for qi in quarantine_items:
            assert qi["status"] == "blocked", (
                f"Quarantine item {qi['id']!r} has status {qi['status']!r}; "
                "expected 'blocked'."
            )

    def test_project_filter_returns_422(
        self,
        board_client: TestClient,
    ) -> None:
        """GET /board?project=x returns 422 (unfiltered aggregation view)."""
        resp = board_client.get("/board", params={"project": _PROJECT_ALPHA})
        assert resp.status_code == 422, (
            f"Expected 422 for ?project= query param, got {resp.status_code}: {resp.text}"
        )

    def test_truncated_field_present_on_all_columns(
        self,
        board_client: TestClient,
    ) -> None:
        """Every column object carries a 'truncated' field (integer >= 0)."""
        resp = board_client.get("/board")
        body = resp.json()
        for col in body["columns"]:
            assert "truncated" in col, (
                f"Column {col['name']!r} missing 'truncated' field."
            )
            assert isinstance(col["truncated"], int), (
                f"Column {col['name']!r} 'truncated' must be int, "
                f"got {type(col['truncated']).__name__!r}."
            )
            assert col["truncated"] >= 0, (
                f"Column {col['name']!r} 'truncated' must be >= 0, "
                f"got {col['truncated']}."
            )

    def test_truncated_nonzero_when_cap_exceeded(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """truncated > 0 when items exceed the per-project cap.

        Sets dashboard_max_rows=1 and adds two bug files to confirm
        truncated=1 appears on the BUGS column.
        """
        root = tmp_path / "trunc_fixture"

        _write(
            root / "projects.cfg",
            "[project:trunc-proj]\npriority=1\ndashboard_color=#378ADD\n"
            "dashboard_max_rows=1\n",
        )
        proj = root / "projects" / "trunc-proj"
        _write(proj / "release-state.md",
               "# Release State\n\n## Active RC\nnone\n\n## Last Released\nnone\n")
        _write(proj / "bugs" / "BUG-0001.md",
               "# Bug: BUG-0001\n\n## Status\nopen\n")
        _write(proj / "bugs" / "BUG-0002.md",
               "# Bug: BUG-0002\n\n## Status\nopen\n")
        # Empty queues.
        for agent in ("pm", "coder", "writer", "tester", "cm"):
            _write(proj / "tasks" / "queues" / f"{agent}_backlog.md",
                   f"# {agent.upper()} Backlog\n\n")

        from pgai_agent_kanban.api.app import create_app
        from pgai_agent_kanban.api.config import ApiConfig

        monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))
        cfg = ApiConfig(host="127.0.0.1", port=0, kanban_root=root)
        app = create_app(cfg=cfg)

        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get("/board")

        assert resp.status_code == 200
        body = resp.json()
        bugs_col = next(c for c in body["columns"] if c["name"] == "BUGS")
        assert len(bugs_col["items"]) <= 1, (
            f"Expected at most 1 item (cap=1), got {len(bugs_col['items'])}."
        )
        assert bugs_col["truncated"] == 1, (
            f"Expected truncated=1, got {bugs_col['truncated']}."
        )

    def test_response_structure_has_generated_at(
        self,
        board_client: TestClient,
    ) -> None:
        """Response includes 'generated_at' ISO-8601 field."""
        resp = board_client.get("/board")
        body = resp.json()
        assert "generated_at" in body, "Response must have 'generated_at' key."
        # Verify it looks like an ISO-8601 timestamp.
        ts_re = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
        assert ts_re.match(body["generated_at"]), (
            f"generated_at {body['generated_at']!r} does not look like ISO-8601."
        )

    def test_cors_loopback_origin_receives_headers(
        self,
        board_client: TestClient,
    ) -> None:
        """GET /board with a loopback origin receives CORS headers."""
        resp = board_client.get(
            "/board",
            headers={"Origin": "http://localhost:3000"},
        )
        assert resp.status_code == 200
        assert "access-control-allow-origin" in resp.headers, (
            "Loopback origin must receive Access-Control-Allow-Origin header."
        )

    def test_cors_non_loopback_origin_receives_no_cors_header(
        self,
        board_client: TestClient,
    ) -> None:
        """GET /board with a non-loopback origin does NOT receive CORS headers."""
        resp = board_client.get(
            "/board",
            headers={"Origin": "https://attacker.example.com"},
        )
        # The endpoint still returns 200 (CORS is not an auth mechanism here);
        # the loopback binding is the real access control.  What matters is that
        # the Allow-Origin header is absent.
        assert "access-control-allow-origin" not in resp.headers, (
            "Non-loopback origin must NOT receive Access-Control-Allow-Origin header."
        )

    def test_items_have_all_required_fields(
        self,
        board_client: TestClient,
    ) -> None:
        """Every item carries all required fields with correct types."""
        resp = board_client.get("/board")
        body = resp.json()
        required = {"id", "project", "kind", "key", "title", "status",
                    "version_label", "active_rc", "color"}
        valid_statuses = {"open", "working", "done", "wont-do", "blocked", "label"}

        for col in body["columns"]:
            for item in col["items"]:
                missing = required - set(item.keys())
                assert not missing, (
                    f"Item {item.get('id', '<no id>')} missing fields: {missing}."
                )
                assert isinstance(item["active_rc"], bool), (
                    f"active_rc must be bool for {item['id']!r}."
                )
                assert item["status"] in valid_statuses, (
                    f"Item {item['id']!r} has invalid status {item['status']!r}. "
                    f"Valid: {valid_statuses}"
                )


# ---------------------------------------------------------------------------
# TestBoardClassifierParity  — the load-bearing parity-pin test
# ---------------------------------------------------------------------------


class TestBoardClassifierParity:
    """Parity-pin: GET /board status must equal board_classifier's classification.

    For each item returned by /board, re-classify it independently using the
    board_classifier functions and assert the two status values are equal.
    This is the house-sibling rule: if the board handler and the classifier
    diverge, this test fails immediately.
    """

    def _reclassify_item(
        self,
        item: dict,
        two_project_root: pathlib.Path,
    ) -> str:
        """Re-classify one board item using board_classifier directly.

        Returns the status string the classifier produces for this item.
        Raises AssertionError when the item's kind is unrecognized.
        """
        project_name = item["project"]
        kind = item["kind"]
        key = item["key"]
        project_dir = two_project_root / "projects" / project_name

        if kind == "quarantine":
            # Quarantine items are always "blocked" — fixed classification.
            return "blocked"

        elif kind in ("bug", "priority", "requirement"):
            # Input item: classify via classify_input_item.
            column_map = {"bug": "BUGS", "priority": "PRIORITIES", "requirement": "REQUIREMENTS"}
            column = column_map[kind]
            dir_map = {
                "bug": project_dir / "bugs",
                "priority": project_dir / "priority",
                "requirement": project_dir / "requirements",
            }
            input_dir = dir_map[kind]

            active_rc = get_active_rc_for_project(project_dir)
            last_released = get_last_released_for_project(project_dir)

            # Find the actual file matching this key.
            found_path = None
            if input_dir.is_dir():
                for entry in input_dir.iterdir():
                    if not entry.is_file() or not entry.name.endswith(".md"):
                        continue
                    if entry.name.upper().startswith("README"):
                        continue
                    stem = entry.stem
                    # Match by compact key.
                    if kind == "requirement":
                        import re as _re
                        m = _re.match(r"^(v\d+\.\d+\.\d+)(?:-.+)?$", stem, _re.IGNORECASE)
                        cid = m.group(1) if m else stem
                    elif kind == "bug":
                        import re as _re
                        m = _re.match(r"^(BUG-\d+)", stem, _re.IGNORECASE)
                        cid = m.group(1) if m else stem
                    else:
                        import re as _re
                        m = _re.match(r"^(PRIORITY-\d+)", stem, _re.IGNORECASE)
                        cid = m.group(1) if m else stem
                    if cid == key:
                        found_path = entry
                        break

            assert found_path is not None, (
                f"Could not find file for item {item['id']!r} in {input_dir}."
            )
            return classify_input_item(found_path, column, active_rc, last_released)

        elif kind == "task":
            # Queue item: classify via classify_queue_item.
            # key is the task_id; read state from status.md or infer from queue.
            task_id = key
            tasks_dir = project_dir / "tasks"
            state = read_task_state_from_status_md(tasks_dir, task_id)

            if not state:
                # Fall back to the queue marker by scanning all queue files.
                queues_dir = tasks_dir / "queues"
                if queues_dir.is_dir():
                    for qfile in queues_dir.iterdir():
                        if not qfile.is_file() or not qfile.name.endswith(".md"):
                            continue
                        for line in qfile.read_text(encoding="utf-8").splitlines():
                            parsed = parse_queue_line(line)
                            if not parsed:
                                continue
                            marker, tid, _date, _seq = parsed
                            if tid == task_id:
                                state = marker_to_state(marker)
                                break
                        if state:
                            break
                if not state:
                    state = "BACKLOG"

            return classify_queue_item(state)

        else:
            raise AssertionError(f"Unrecognized item kind {kind!r} for {item['id']!r}.")

    def test_board_status_matches_classifier_for_all_items(
        self,
        board_client: TestClient,
        two_project_root: pathlib.Path,
    ) -> None:
        """For every item returned by /board, its status matches board_classifier's output.

        This is the parity-pin: the board handler and the classifier must agree
        on every item's status for the same fixture inputs.
        """
        resp = board_client.get("/board")
        assert resp.status_code == 200

        body = resp.json()
        mismatches = []

        for col in body["columns"]:
            for item in col["items"]:
                board_status = item["status"]
                try:
                    classifier_status = self._reclassify_item(item, two_project_root)
                except AssertionError as exc:
                    mismatches.append(
                        f"  {item['id']!r}: classifier error — {exc}"
                    )
                    continue

                if board_status != classifier_status:
                    mismatches.append(
                        f"  {item['id']!r}: board={board_status!r}, "
                        f"classifier={classifier_status!r}"
                    )

        assert not mismatches, (
            f"Classification parity failures ({len(mismatches)}):\n"
            + "\n".join(mismatches)
        )

    def test_parity_covers_all_status_classes_in_fixture(
        self,
        board_client: TestClient,
    ) -> None:
        """The two-project fixture exercises all board status classes.

        At minimum: open, working, done, blocked, label must each appear at
        least once.  This ensures the parity test is not vacuous (e.g. only
        testing "open" items).

        Note: "wont-do" is not exercised by this fixture — a WONT-DO task
        would require additional fixture state.  The six-class completeness
        check is the responsibility of TESTER; this test checks the five
        classes present in the fixture.
        """
        resp = board_client.get("/board")
        body = resp.json()
        all_items = [item for col in body["columns"] for item in col["items"]]
        statuses_found = {item["status"] for item in all_items}

        required_classes = {"open", "working", "done", "blocked", "label"}
        missing = required_classes - statuses_found
        assert not missing, (
            f"Fixture does not exercise all required status classes. "
            f"Missing: {missing}. Found: {statuses_found}"
        )
