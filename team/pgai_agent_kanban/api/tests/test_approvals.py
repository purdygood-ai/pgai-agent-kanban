"""
test_approvals.py — Tests for GET /approvals endpoint.

Covers:
  1. Shape test — response is a JSON array; each record has the required fields.
  2. Empty state — clean system returns [].
  3. Project filter — ?project=<name> narrows results to that project.
  4. Aggregation — absent ?project= returns records from ALL projects.
  5. Unknown project — ?project=<nonexistent> returns 422 (not empty 200).
  6. 422-before-subprocess — unknown project 422 fires without invoking any
     subprocess (no shell-out for this pure-Python endpoint).
  7. Path confinement — GET /approvals does not mutate any file in the sandbox
     (verb-discipline check).
  8. JSON-vs-window-14 equality — every task_id/project/rc in the window-14
     rendered output also appears in the endpoint JSON, and vice versa, against
     a fixture with pending approvals in two projects.

Design notes
-----------
- All tests use the FastAPI TestClient (no real network socket).
- Fixture sandboxes are built under tmp_path (no bare /tmp paths).
- PGAI_AGENT_KANBAN_ROOT_PATH is monkeypatched to point at the sandbox so the
  endpoint's _live_kanban_root() reads from the fixture, not the live install.
- The JSON-vs-window-14 equality test parses the rendered text output of
  scan_pending_approvals to extract task_id/project/rc values and asserts
  bidirectional set equality with the endpoint JSON.
"""

from __future__ import annotations

import io
import pathlib
import sys
import textwrap
from typing import Generator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Ensure the package is importable regardless of the test invocation root.
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_API_TESTS_DIR = _THIS_FILE.parent
_TEAM_DIR = _API_TESTS_DIR.parent.parent.parent  # team/
_PROJECT_ROOT = _TEAM_DIR.parent                  # project root (contains team/)

_project_root_str = str(_PROJECT_ROOT)
if _project_root_str not in sys.path:
    sys.path.insert(0, _project_root_str)


# ---------------------------------------------------------------------------
# Fixture builder helpers (mirrors test_human_review_window.py pattern)
# ---------------------------------------------------------------------------

_PROJ_ALPHA = "proj-alpha"
_PROJ_BETA = "proj-beta"
_TASK_ALPHA = "HUMAN-APPROVE-v1.10.0-099"
_TASK_BETA = "HUMAN-APPROVE-v2.0.0-001"
_RC_ALPHA = "v1.10.0"
_RC_BETA = "v2.0.0"


def _write_task(
    tasks_dir: pathlib.Path,
    task_id: str,
    state: str,
    release_ver: str = "v1.10.0",
    goal: str = "Review and approve this release.",
) -> None:
    """Create a minimal HUMAN-APPROVE task folder under tasks_dir."""
    task_dir = tasks_dir / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    (task_dir / "status.md").write_text(
        f"# Status\n\n## State\n{state}\n",
        encoding="utf-8",
    )
    (task_dir / "README.md").write_text(
        textwrap.dedent(f"""\
            # Task: Human Approval Gate

            ## Task ID
            {task_id}

            ## Role
            HUMAN

            ## Goal
            {goal}

            ## Release Version
            {release_ver}
        """),
        encoding="utf-8",
    )


def _build_project(
    kanban_root: pathlib.Path,
    proj: str,
    tasks: list[tuple],
) -> None:
    """Create projects/<proj>/tasks with the given task list.

    Each entry in tasks is (task_id, state, release_ver, goal).
    """
    tasks_dir = kanban_root / "projects" / proj / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    for task_id, state, release_ver, goal in tasks:
        _write_task(tasks_dir, task_id, state, release_ver, goal)


# ---------------------------------------------------------------------------
# App factory and TestClient fixture
# ---------------------------------------------------------------------------


def _make_client(
    sandbox_root: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    """Create a TestClient with PGAI_AGENT_KANBAN_ROOT_PATH set to sandbox_root.

    The ApiConfig kanban_root is set to _TEAM_DIR so that
    ``kanban_root / "scripts"`` resolves to real scripts (needed for other
    endpoints in the same app; /approvals does not shell out but other
    read-layer routes do).

    Args:
        sandbox_root: Kanban root the endpoint should scan for approvals data.
        monkeypatch:  pytest monkeypatch fixture to override env vars.

    Returns:
        A configured TestClient for the duration of the test.
    """
    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(sandbox_root))
    monkeypatch.setenv("KANBAN_ROOT", str(sandbox_root))

    cfg = ApiConfig(
        host="127.0.0.1",
        port=0,
        kanban_root=_TEAM_DIR,
    )
    return TestClient(create_app(cfg=cfg), raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# 1. Empty state — clean system returns []
# ---------------------------------------------------------------------------


class TestApprovalsEmptyState:
    """GET /approvals returns [] when no HUMAN-APPROVE tasks are pending."""

    def test_empty_projects_dir_returns_empty_array(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No projects/ directory → response is []."""
        # No projects/ directory created → truly empty kanban root.
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/approvals")

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        assert resp.json() == [], f"Expected [], got {resp.json()}"

    def test_project_with_no_pending_tasks_returns_empty_array(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Project directory exists but no pending tasks → response is []."""
        _build_project(
            tmp_path, _PROJ_ALPHA,
            [(_TASK_ALPHA, "DONE", _RC_ALPHA, "Already approved.")],
        )
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/approvals")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_project_filter_on_project_with_no_pending_returns_empty_array(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """?project= for a registered project with no pending tasks → []."""
        _build_project(
            tmp_path, _PROJ_ALPHA,
            [(_TASK_ALPHA, "DONE", _RC_ALPHA, "Already approved.")],
        )
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/approvals", params={"project": _PROJ_ALPHA})

        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# 2. Shape test — response fields
# ---------------------------------------------------------------------------


_REQUIRED_FIELDS = {
    "task_id", "project", "state", "rc", "target_version",
    "age", "review", "review_cmds", "approve_cmd", "reject_cmd",
}


class TestApprovalsShape:
    """GET /approvals response records have the required field set."""

    def test_single_pending_record_has_required_fields(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """One pending approval record contains all required fields."""
        _build_project(
            tmp_path, _PROJ_ALPHA,
            [(_TASK_ALPHA, "WAITING", _RC_ALPHA, "Review release v1.10.0.")],
        )
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/approvals")

        assert resp.status_code == 200
        records = resp.json()
        assert len(records) == 1
        record = records[0]
        missing = _REQUIRED_FIELDS - record.keys()
        assert not missing, f"Missing fields in response record: {missing}"

    def test_record_task_id_matches_fixture(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """task_id in the response matches the task directory name."""
        _build_project(
            tmp_path, _PROJ_ALPHA,
            [(_TASK_ALPHA, "WAITING", _RC_ALPHA, "Review.")],
        )
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/approvals")

        record = resp.json()[0]
        assert record["task_id"] == _TASK_ALPHA

    def test_record_project_matches_fixture(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """project in the response matches the fixture project name."""
        _build_project(
            tmp_path, _PROJ_ALPHA,
            [(_TASK_ALPHA, "WAITING", _RC_ALPHA, "Review.")],
        )
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/approvals")

        record = resp.json()[0]
        assert record["project"] == _PROJ_ALPHA

    def test_record_rc_matches_release_version(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """rc in the response matches ## Release Version from README."""
        _build_project(
            tmp_path, _PROJ_ALPHA,
            [(_TASK_ALPHA, "WAITING", _RC_ALPHA, "Review.")],
        )
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/approvals")

        record = resp.json()[0]
        assert record["rc"] == _RC_ALPHA

    def test_record_target_version_equals_rc(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """target_version is the same value as rc."""
        _build_project(
            tmp_path, _PROJ_ALPHA,
            [(_TASK_ALPHA, "WAITING", _RC_ALPHA, "Review.")],
        )
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/approvals")

        record = resp.json()[0]
        assert record["target_version"] == record["rc"]

    def test_record_approve_cmd_contains_project_and_task_id(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """approve_cmd contains the project name and task_id."""
        _build_project(
            tmp_path, _PROJ_ALPHA,
            [(_TASK_ALPHA, "WAITING", _RC_ALPHA, "Review.")],
        )
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/approvals")

        record = resp.json()[0]
        assert _PROJ_ALPHA in record["approve_cmd"]
        assert _TASK_ALPHA in record["approve_cmd"]
        assert "close.sh" in record["approve_cmd"]

    def test_record_reject_cmd_contains_project_and_task_id(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """reject_cmd contains the project name and task_id."""
        _build_project(
            tmp_path, _PROJ_ALPHA,
            [(_TASK_ALPHA, "WAITING", _RC_ALPHA, "Review.")],
        )
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/approvals")

        record = resp.json()[0]
        assert _PROJ_ALPHA in record["reject_cmd"]
        assert _TASK_ALPHA in record["reject_cmd"]
        assert "wontdo.sh" in record["reject_cmd"]

    def test_record_state_is_waiting_or_backlog(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """state field is one of the pending states (WAITING or BACKLOG)."""
        _build_project(
            tmp_path, _PROJ_ALPHA,
            [
                (_TASK_ALPHA, "WAITING", _RC_ALPHA, "Review."),
                ("HUMAN-APPROVE-v1.10.0-100", "BACKLOG", _RC_ALPHA, "Review2."),
            ],
        )
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/approvals")

        for record in resp.json():
            assert record["state"] in ("WAITING", "BACKLOG"), (
                f"Unexpected state value: {record['state']}"
            )


# ---------------------------------------------------------------------------
# 3. Project filter
# ---------------------------------------------------------------------------


class TestApprovalsProjectFilter:
    """?project=<name> narrows results to the named project."""

    def test_project_filter_returns_only_matching_project(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """?project=proj-alpha returns only proj-alpha records."""
        _build_project(
            tmp_path, _PROJ_ALPHA,
            [(_TASK_ALPHA, "WAITING", _RC_ALPHA, "Alpha review.")],
        )
        _build_project(
            tmp_path, _PROJ_BETA,
            [(_TASK_BETA, "WAITING", _RC_BETA, "Beta review.")],
        )
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/approvals", params={"project": _PROJ_ALPHA})

        assert resp.status_code == 200
        records = resp.json()
        assert len(records) == 1
        assert records[0]["project"] == _PROJ_ALPHA
        assert records[0]["task_id"] == _TASK_ALPHA

    def test_project_filter_excludes_other_projects(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """?project=proj-alpha result does not contain proj-beta records."""
        _build_project(
            tmp_path, _PROJ_ALPHA,
            [(_TASK_ALPHA, "WAITING", _RC_ALPHA, "Alpha review.")],
        )
        _build_project(
            tmp_path, _PROJ_BETA,
            [(_TASK_BETA, "WAITING", _RC_BETA, "Beta review.")],
        )
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/approvals", params={"project": _PROJ_ALPHA})

        project_names = {r["project"] for r in resp.json()}
        assert _PROJ_BETA not in project_names, (
            f"proj-beta records appeared in proj-alpha filter response: {resp.json()}"
        )


# ---------------------------------------------------------------------------
# 4. Aggregation — absent project= returns all projects
# ---------------------------------------------------------------------------


class TestApprovalsAggregation:
    """Absent ?project= parameter returns records from ALL projects."""

    def test_no_filter_returns_both_projects(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without ?project=, records from both projects are returned."""
        _build_project(
            tmp_path, _PROJ_ALPHA,
            [(_TASK_ALPHA, "WAITING", _RC_ALPHA, "Alpha review.")],
        )
        _build_project(
            tmp_path, _PROJ_BETA,
            [(_TASK_BETA, "WAITING", _RC_BETA, "Beta review.")],
        )
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/approvals")

        assert resp.status_code == 200
        records = resp.json()
        assert len(records) == 2

        project_names = {r["project"] for r in records}
        assert _PROJ_ALPHA in project_names
        assert _PROJ_BETA in project_names

    def test_no_filter_all_task_ids_present(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without ?project=, all task IDs from all projects are present."""
        _build_project(
            tmp_path, _PROJ_ALPHA,
            [(_TASK_ALPHA, "WAITING", _RC_ALPHA, "Alpha review.")],
        )
        _build_project(
            tmp_path, _PROJ_BETA,
            [(_TASK_BETA, "WAITING", _RC_BETA, "Beta review.")],
        )
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/approvals")

        task_ids = {r["task_id"] for r in resp.json()}
        assert _TASK_ALPHA in task_ids
        assert _TASK_BETA in task_ids


# ---------------------------------------------------------------------------
# 5 & 6. Unknown project — 422 before subprocess
# ---------------------------------------------------------------------------


class TestApprovalsUnknownProject:
    """?project=<nonexistent> returns 422, not an empty 200."""

    def test_unknown_project_returns_422(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Requesting a nonexistent project returns HTTP 422."""
        # No projects created in tmp_path.
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/approvals", params={"project": "does-not-exist"})

        assert resp.status_code == 422, (
            f"Expected 422 for unknown project, got {resp.status_code}: {resp.text}"
        )

    def test_unknown_project_returns_422_not_empty_200(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Unknown project MUST NOT return 200 with empty array."""
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/approvals", params={"project": "does-not-exist"})

        assert resp.status_code != 200, (
            "Unknown project must not return 200; returned empty 200 instead of 422."
        )

    def test_unknown_project_detail_names_project(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """422 detail message names the unknown project."""
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/approvals", params={"project": "does-not-exist"})

        detail = resp.json().get("detail", "")
        assert "does-not-exist" in detail, (
            f"422 detail must name the unknown project; got: {detail!r}"
        )

    def test_unknown_project_422_before_subprocess(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """422 for unknown project fires before any subprocess is spawned.

        GET /approvals is a pure-Python endpoint — it never shells out.
        This test verifies that no subprocess is invoked even when the
        project validation fails.
        """
        with patch("subprocess.run") as mock_run:
            with _make_client(tmp_path, monkeypatch) as client:
                resp = client.get("/approvals", params={"project": "does-not-exist"})

            mock_run.assert_not_called()

        assert resp.status_code == 422

    def test_known_project_with_no_approvals_not_422(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A registered project with no pending approvals returns 200 [], not 422."""
        _build_project(
            tmp_path, _PROJ_ALPHA,
            [(_TASK_ALPHA, "DONE", _RC_ALPHA, "Already done.")],
        )
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/approvals", params={"project": _PROJ_ALPHA})

        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# 7. Path confinement — GET /approvals must not mutate the sandbox
# ---------------------------------------------------------------------------


class TestApprovalsPathConfinement:
    """GET /approvals must not write any file to the sandbox (verb discipline)."""

    def _checksum_dir(self, root: pathlib.Path) -> dict[str, bytes]:
        """Return {relative-path: content-bytes} for all files under root."""
        result = {}
        if not root.is_dir():
            return result
        for p in sorted(root.rglob("*")):
            if p.is_file():
                rel = str(p.relative_to(root))
                result[rel] = p.read_bytes()
        return result

    def test_approvals_no_mutation_empty_root(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """GET /approvals on an empty kanban root does not create any files."""
        before = self._checksum_dir(tmp_path)
        with _make_client(tmp_path, monkeypatch) as client:
            client.get("/approvals")
        after = self._checksum_dir(tmp_path)
        assert before == after, (
            "GET /approvals mutated the sandbox (verb-discipline violation).\n"
            f"Files changed: {set(after) - set(before)}"
        )

    def test_approvals_no_mutation_with_pending_tasks(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """GET /approvals with pending tasks does not modify any existing file."""
        _build_project(
            tmp_path, _PROJ_ALPHA,
            [(_TASK_ALPHA, "WAITING", _RC_ALPHA, "Review.")],
        )
        before = self._checksum_dir(tmp_path)
        with _make_client(tmp_path, monkeypatch) as client:
            client.get("/approvals")
        after = self._checksum_dir(tmp_path)
        assert before == after, (
            "GET /approvals mutated the sandbox (verb-discipline violation).\n"
            f"Files changed: {set(after) ^ set(before)}"
        )


# ---------------------------------------------------------------------------
# 8. JSON-vs-window-14 equality test
# ---------------------------------------------------------------------------


class TestApprovalsJsonVsWindow14:
    """Assert bidirectional set equality between GET /approvals JSON and window-14 rendered output.

    Against a fixture with pending approvals in two projects:
    - Every task_id/project/rc in the window-14 text render also appears in the
      endpoint JSON.
    - Every task_id/project/rc in the endpoint JSON also appears in the
      window-14 text render.

    This proves that the window-14 renderer and the endpoint consume the same
    underlying data function — one implementation, two surfaces.
    """

    def _parse_window14_records(self, rendered: str) -> set[tuple[str, str, str]]:
        """Extract (task_id, project, rc) tuples from window-14 rendered text.

        Parses the no-color output of scan_pending_approvals.  Each pending
        entry is formatted as:

            ! TASK-ID  [project]
              RC/target:  rc-value   age: ...   state: ...
              Review:
                ...
              Approve:  scripts/close.sh --project project --key TASK-ID
              Reject:   scripts/wontdo.sh --project project --key TASK-ID

        We extract task_id from "! TASK-ID", project from "[project]", and
        rc from "RC/target:  rc-value".  This parsing is intentionally loose
        (uses substring matching) to remain stable against minor formatting
        changes in the renderer.

        Returns:
            Set of (task_id, project, rc) tuples.
        """
        import re
        records: set[tuple[str, str, str]] = set()

        # Pattern for header line: "! TASK-ID  [project]"
        header_re = re.compile(r"!\s+(HUMAN-APPROVE\S+)\s+\[(\S+)\]")
        # Pattern for RC line: "RC/target:  <value>"
        rc_re = re.compile(r"RC/target:\s+(\S+)")

        lines = rendered.splitlines()
        i = 0
        while i < len(lines):
            hm = header_re.search(lines[i])
            if hm:
                task_id = hm.group(1)
                project = hm.group(2)
                # Look for RC line in the next few lines
                rc = ""
                for j in range(i + 1, min(i + 5, len(lines))):
                    rm = rc_re.search(lines[j])
                    if rm:
                        rc = rm.group(1)
                        break
                records.add((task_id, project, rc))
            i += 1

        return records

    def test_json_vs_window14_two_project_fixture(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Bidirectional equality: JSON and window-14 render match on two-project fixture.

        Both directions are asserted:
          - Every (task_id, project, rc) in JSON is present in window-14.
          - Every (task_id, project, rc) in window-14 is present in JSON.
        """
        from pgai_agent_kanban.dashboard.scan_human_approvals import (
            scan_pending_approvals,
        )

        # Build two-project fixture.
        _build_project(
            tmp_path, _PROJ_ALPHA,
            [(_TASK_ALPHA, "WAITING", _RC_ALPHA, "Approve alpha release.")],
        )
        _build_project(
            tmp_path, _PROJ_BETA,
            [(_TASK_BETA, "BACKLOG", _RC_BETA, "Approve beta release.")],
        )

        # Get endpoint JSON.
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/approvals")
        assert resp.status_code == 200
        json_records = resp.json()

        # Build set of (task_id, project, rc) from JSON.
        json_set = {
            (r["task_id"], r["project"], r["rc"])
            for r in json_records
        }

        # Get window-14 rendered text (no-color, no subprocess).
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            scan_pending_approvals(str(tmp_path), use_color=False)
        finally:
            sys.stdout = old_stdout
        rendered = buf.getvalue()

        # Parse rendered text into (task_id, project, rc) set.
        window14_set = self._parse_window14_records(rendered)

        assert window14_set, (
            "window-14 parsed no records from rendered output.\n"
            f"Rendered text:\n{rendered}"
        )
        assert json_set, (
            "JSON endpoint returned no records.\n"
            f"Response: {json_records}"
        )

        # JSON → window-14: every JSON record appears in the render.
        missing_from_window14 = json_set - window14_set
        assert not missing_from_window14, (
            "Records in JSON but not in window-14 render:\n"
            + "\n".join(str(r) for r in sorted(missing_from_window14))
            + f"\nRendered text:\n{rendered}"
        )

        # window-14 → JSON: every rendered record appears in JSON.
        missing_from_json = window14_set - json_set
        assert not missing_from_json, (
            "Records in window-14 render but not in JSON:\n"
            + "\n".join(str(r) for r in sorted(missing_from_json))
            + f"\nJSON records: {json_records}"
        )
