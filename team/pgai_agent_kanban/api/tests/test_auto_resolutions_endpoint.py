"""
test_auto_resolutions_endpoint.py — Tests for GET /auto-resolutions endpoint.

Covers:
  1. Empty state — clean system (no ledger anywhere) returns [].
  2. Shape test — response is a JSON array; each record has the required fields.
  3. Project filter — ?project=<name> narrows results to that project.
  4. Aggregation — absent ?project= returns records from ALL projects.
  5. Unknown project — ?project=<nonexistent> returns 422 (not empty 200).
  6. 422-before-subprocess — unknown project 422 fires without invoking any
     subprocess (no shell-out for this pure-Python endpoint).
  7. Path confinement — GET /auto-resolutions does not mutate any file in the
     sandbox (verb-discipline check).
  8. Read-only — no approve/reject/close/wontdo affordance appears anywhere
     in the response (the surface informs, it never mutates state).
  9. JSON-vs-tmux-window equality — every (project, rc, entry_id) in the
     tmux Auto-Resolutions rendered output also appears in the endpoint
     JSON, and vice versa, against a fixture with resolutions in two
     projects — proving both surfaces consume the same underlying data
     function (v2.0.0 Goal 6: browser tab is the sibling of the tmux
     surface, both read the ledger).

Design notes
-----------
- All tests use the FastAPI TestClient (no real network socket).
- Fixture sandboxes are built under tmp_path (no bare /tmp paths).
- PGAI_AGENT_KANBAN_ROOT_PATH is monkeypatched to point at the sandbox so the
  endpoint's _live_kanban_root() reads from the fixture, not the live install.
- Ledger fixtures are built with pgai_agent_kanban.overwatch.ledger.append_entry,
  mirroring team/tests/unit/test_auto_resolutions_window.py's fixture pattern.
"""

from __future__ import annotations

import io
import pathlib
import sys
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

from pgai_agent_kanban.overwatch.ledger import (  # noqa: E402
    LedgerEntry,
    append_entry,
    ledger_path_for_project,
)


# ---------------------------------------------------------------------------
# Fixture builder helpers (mirrors test_auto_resolutions_window.py pattern)
# ---------------------------------------------------------------------------

_PROJ_ALPHA = "proj-alpha"
_PROJ_BETA = "proj-beta"


def _write_ceiling_cfg(kanban_root: pathlib.Path, max_rc_retries: int = 3) -> None:
    """Write a kanban.cfg with [release] max_rc_retries so the ceiling resolves."""
    (kanban_root / "kanban.cfg").write_text(
        f"[release]\nmax_rc_retries = {max_rc_retries}\n",
        encoding="utf-8",
    )


def _append_resolution(
    kanban_root: pathlib.Path,
    project: str,
    rc: str = "v2.0.1",
    attempt_from: int = 1,
    attempt_to: int = 2,
    trigger: str = "ship-policy-halt",
    matrix_row: str = "CANCEL-AND-REBUNDLE",
    findings: list | None = None,
    evidence_paths: list | None = None,
    outcome: str | None = None,
    timestamp: str = "2026-07-27T18:03:00Z",
) -> str:
    """Append one ledger entry for *project* and return its entry_id."""
    (kanban_root / "projects" / project).mkdir(parents=True, exist_ok=True)
    ledger_path = ledger_path_for_project(str(kanban_root), project)
    entry = LedgerEntry(
        timestamp=timestamp,
        rc=rc,
        attempt_from=attempt_from,
        attempt_to=attempt_to,
        trigger=trigger,
        matrix_row=matrix_row,
        findings=findings or [],
        evidence_paths=evidence_paths or [],
        outcome=outcome,
    )
    return append_entry(ledger_path, entry)


def _register_project(kanban_root: pathlib.Path, project: str) -> None:
    """Register a project directory with no ledger entries (empty state fixture)."""
    (kanban_root / "projects" / project).mkdir(parents=True, exist_ok=True)


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
    endpoints in the same app; /auto-resolutions does not shell out but
    other read-layer routes do).

    Args:
        sandbox_root: Kanban root the endpoint should scan for ledger data.
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


class TestAutoResolutionsEmptyState:
    """GET /auto-resolutions returns [] when no ledger entries exist anywhere."""

    def test_no_projects_dir_returns_empty_array(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No projects/ directory → response is []."""
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/auto-resolutions")

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        assert resp.json() == [], f"Expected [], got {resp.json()}"

    def test_registered_project_with_no_ledger_returns_empty_array(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Project directory exists but no auto-resolutions.md yet → response is []."""
        _register_project(tmp_path, _PROJ_ALPHA)
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/auto-resolutions")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_project_filter_on_project_with_no_ledger_returns_empty_array(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """?project= for a registered project with no ledger entries → []."""
        _register_project(tmp_path, _PROJ_ALPHA)
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/auto-resolutions", params={"project": _PROJ_ALPHA})

        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# 2. Shape test — response fields
# ---------------------------------------------------------------------------


_REQUIRED_FIELDS = {
    "project", "rc", "attempt_from", "attempt_to", "ceiling",
    "attempt_display", "decision", "outcome", "trigger", "findings",
    "evidence_paths", "ledger_path", "entry_id", "family_key", "timestamp",
}


class TestAutoResolutionsShape:
    """GET /auto-resolutions response records have the required field set."""

    def test_single_entry_record_has_required_fields(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """One ledger entry record contains all required fields."""
        _write_ceiling_cfg(tmp_path)
        _append_resolution(tmp_path, _PROJ_ALPHA)
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/auto-resolutions")

        assert resp.status_code == 200
        records = resp.json()
        assert len(records) == 1
        missing = _REQUIRED_FIELDS - records[0].keys()
        assert not missing, f"Missing fields in response record: {missing}"

    def test_record_rc_matches_fixture(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_ceiling_cfg(tmp_path)
        _append_resolution(tmp_path, _PROJ_ALPHA, rc="v2.0.1")
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/auto-resolutions")

        assert resp.json()[0]["rc"] == "v2.0.1"

    def test_record_attempt_display_is_n_over_ceiling(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_ceiling_cfg(tmp_path, max_rc_retries=3)
        _append_resolution(tmp_path, _PROJ_ALPHA, attempt_from=1, attempt_to=2)
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/auto-resolutions")

        assert resp.json()[0]["attempt_display"] == "1/3"

    def test_record_decision_matches_matrix_row(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_ceiling_cfg(tmp_path)
        _append_resolution(tmp_path, _PROJ_ALPHA, matrix_row="CANCEL-AND-REBUNDLE")
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/auto-resolutions")

        assert resp.json()[0]["decision"] == "CANCEL-AND-REBUNDLE"

    def test_pending_outcome_renders_as_pending(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An entry whose Outcome has not been backfilled renders 'pending'."""
        _write_ceiling_cfg(tmp_path)
        _append_resolution(tmp_path, _PROJ_ALPHA, outcome=None)
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/auto-resolutions")

        assert resp.json()[0]["outcome"] == "pending"

    def test_backfilled_outcome_renders_verbatim(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_ceiling_cfg(tmp_path)
        _append_resolution(tmp_path, _PROJ_ALPHA, outcome="SHIPPED attempt 2")
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/auto-resolutions")

        assert resp.json()[0]["outcome"] == "SHIPPED attempt 2"

    def test_record_evidence_paths_and_ledger_link_present(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """evidence_paths and ledger_path/entry_id together form the drilldown
        the browser Auto-Resolutions tab renders (TESTER report + ledger entry)."""
        _write_ceiling_cfg(tmp_path)
        entry_id = _append_resolution(
            tmp_path,
            _PROJ_ALPHA,
            evidence_paths=[
                "/kanban/projects/proj-alpha/tasks/CM-x/artifacts/report.md",
            ],
        )
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/auto-resolutions")

        record = resp.json()[0]
        assert record["evidence_paths"] == [
            "/kanban/projects/proj-alpha/tasks/CM-x/artifacts/report.md",
        ]
        expected_ledger_path = ledger_path_for_project(str(tmp_path), _PROJ_ALPHA)
        assert record["ledger_path"] == expected_ledger_path
        assert record["entry_id"] == entry_id

    def test_missing_ceiling_config_renders_question_mark_not_crash(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No kanban.cfg and no project.cfg override: ceiling degrades to '?'
        rather than the endpoint raising CeilingConfigError."""
        _append_resolution(tmp_path, _PROJ_ALPHA, attempt_from=2, attempt_to=3)
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/auto-resolutions")

        assert resp.status_code == 200
        record = resp.json()[0]
        assert record["ceiling"] == "?"
        assert record["attempt_display"] == "2/?"


# ---------------------------------------------------------------------------
# 3. Project filter
# ---------------------------------------------------------------------------


class TestAutoResolutionsProjectFilter:
    """?project=<name> narrows results to the named project."""

    def test_project_filter_returns_only_matching_project(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_ceiling_cfg(tmp_path)
        _append_resolution(tmp_path, _PROJ_ALPHA, rc="v1.0.1")
        _append_resolution(tmp_path, _PROJ_BETA, rc="v2.0.1")
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/auto-resolutions", params={"project": _PROJ_ALPHA})

        assert resp.status_code == 200
        records = resp.json()
        assert len(records) == 1
        assert records[0]["project"] == _PROJ_ALPHA
        assert records[0]["rc"] == "v1.0.1"

    def test_project_filter_excludes_other_projects(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_ceiling_cfg(tmp_path)
        _append_resolution(tmp_path, _PROJ_ALPHA, rc="v1.0.1")
        _append_resolution(tmp_path, _PROJ_BETA, rc="v2.0.1")
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/auto-resolutions", params={"project": _PROJ_ALPHA})

        project_names = {r["project"] for r in resp.json()}
        assert _PROJ_BETA not in project_names


# ---------------------------------------------------------------------------
# 4. Aggregation — absent project= returns all projects
# ---------------------------------------------------------------------------


class TestAutoResolutionsAggregation:
    """Absent ?project= parameter returns records from ALL projects."""

    def test_no_filter_returns_both_projects(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_ceiling_cfg(tmp_path)
        _append_resolution(tmp_path, _PROJ_ALPHA, rc="v1.0.1")
        _append_resolution(tmp_path, _PROJ_BETA, rc="v2.0.1")
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/auto-resolutions")

        assert resp.status_code == 200
        records = resp.json()
        assert len(records) == 2
        project_names = {r["project"] for r in records}
        assert _PROJ_ALPHA in project_names
        assert _PROJ_BETA in project_names


# ---------------------------------------------------------------------------
# 5 & 6. Unknown project — 422 before subprocess
# ---------------------------------------------------------------------------


class TestAutoResolutionsUnknownProject:
    """?project=<nonexistent> returns 422, not an empty 200."""

    def test_unknown_project_returns_422(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/auto-resolutions", params={"project": "does-not-exist"})

        assert resp.status_code == 422, (
            f"Expected 422 for unknown project, got {resp.status_code}: {resp.text}"
        )

    def test_unknown_project_detail_names_project(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/auto-resolutions", params={"project": "does-not-exist"})

        detail = resp.json().get("detail", "")
        assert "does-not-exist" in detail

    def test_unknown_project_422_before_subprocess(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """422 for unknown project fires before any subprocess is spawned.

        GET /auto-resolutions is a pure-Python endpoint — it never shells out.
        """
        with patch("subprocess.run") as mock_run:
            with _make_client(tmp_path, monkeypatch) as client:
                resp = client.get("/auto-resolutions", params={"project": "does-not-exist"})

            mock_run.assert_not_called()

        assert resp.status_code == 422

    def test_known_project_with_no_resolutions_not_422(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A registered project with no ledger entries returns 200 [], not 422."""
        _register_project(tmp_path, _PROJ_ALPHA)
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/auto-resolutions", params={"project": _PROJ_ALPHA})

        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# 7. Path confinement — GET /auto-resolutions must not mutate the sandbox
# ---------------------------------------------------------------------------


class TestAutoResolutionsPathConfinement:
    """GET /auto-resolutions must not write any file (verb discipline)."""

    def _checksum_dir(self, root: pathlib.Path) -> dict[str, bytes]:
        result = {}
        if not root.is_dir():
            return result
        for p in sorted(root.rglob("*")):
            if p.is_file():
                rel = str(p.relative_to(root))
                result[rel] = p.read_bytes()
        return result

    def test_no_mutation_empty_root(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        before = self._checksum_dir(tmp_path)
        with _make_client(tmp_path, monkeypatch) as client:
            client.get("/auto-resolutions")
        after = self._checksum_dir(tmp_path)
        assert before == after, (
            "GET /auto-resolutions mutated the sandbox (verb-discipline violation).\n"
            f"Files changed: {set(after) - set(before)}"
        )

    def test_no_mutation_with_ledger_entries(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_ceiling_cfg(tmp_path)
        _append_resolution(tmp_path, _PROJ_ALPHA)
        before = self._checksum_dir(tmp_path)
        with _make_client(tmp_path, monkeypatch) as client:
            client.get("/auto-resolutions")
        after = self._checksum_dir(tmp_path)
        assert before == after, (
            "GET /auto-resolutions mutated the sandbox (verb-discipline violation).\n"
            f"Files changed: {set(after) ^ set(before)}"
        )


# ---------------------------------------------------------------------------
# 8. Read-only — no action affordances anywhere in the response
# ---------------------------------------------------------------------------


class TestAutoResolutionsReadOnly:
    """The endpoint never emits an approve/reject/close/wontdo affordance —
    it informs, it never mutates state (v2.0.0 Goal 6 constraint, and the
    task's own 'Read-only: no approve/mutate affordances' constraint)."""

    def test_response_contains_no_action_commands(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_ceiling_cfg(tmp_path)
        _append_resolution(tmp_path, _PROJ_ALPHA)
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/auto-resolutions")

        body = resp.text
        assert "close.sh" not in body
        assert "wontdo.sh" not in body
        assert "approve_cmd" not in body
        assert "reject_cmd" not in body


# ---------------------------------------------------------------------------
# 9. JSON-vs-tmux-window equality test
# ---------------------------------------------------------------------------


class TestAutoResolutionsJsonVsTmuxWindow:
    """Assert bidirectional set equality between GET /auto-resolutions JSON
    and the human-review tmux window's Auto-Resolutions rendered output.

    Against a fixture with resolutions in two projects:
    - Every (project, rc, entry_id) in the endpoint JSON also appears in the
      tmux text render.
    - Every (project, rc, entry_id) in the tmux text render also appears in
      the endpoint JSON.

    This proves that the tmux renderer and the endpoint consume the same
    underlying data function (collect_auto_resolutions /
    _collect_auto_resolutions in scan_auto_resolutions.py) — one
    implementation, two surfaces (browser tab is the tmux surface's sibling,
    per the task notes).
    """

    def _parse_tmux_records(self, rendered: str) -> set[tuple[str, str, str]]:
        """Extract (project, rc, entry_id) tuples from tmux rendered text.

        Each entry renders as:
            * RC  [project]
              attempt: N/M   decision: ...   outcome: ...
              Evidence: ...
              Ledger:  <path>  (entry <entry_id>)

        Parsing is intentionally loose (substring/regex matching) to remain
        stable against minor formatting changes in the renderer.
        """
        import re

        records: set[tuple[str, str, str]] = set()

        header_re = re.compile(r"^\S?\s*(\S+)\s+\[(\S+)\]\s*$")
        ledger_re = re.compile(r"\(entry\s+(\S+)\)")

        lines = rendered.splitlines()
        i = 0
        while i < len(lines):
            hm = header_re.match(lines[i].strip())
            if hm:
                rc = hm.group(1)
                project = hm.group(2)
                entry_id = ""
                for j in range(i + 1, min(i + 6, len(lines))):
                    lm = ledger_re.search(lines[j])
                    if lm:
                        entry_id = lm.group(1)
                        break
                records.add((project, rc, entry_id))
            i += 1

        return records

    def test_json_vs_tmux_two_project_fixture(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from pgai_agent_kanban.dashboard.scan_auto_resolutions import (
            scan_auto_resolutions,
        )

        _write_ceiling_cfg(tmp_path)
        _append_resolution(tmp_path, _PROJ_ALPHA, rc="v1.0.1")
        _append_resolution(tmp_path, _PROJ_BETA, rc="v2.0.1")

        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/auto-resolutions")
        assert resp.status_code == 200
        json_records = resp.json()

        json_set = {
            (r["project"], r["rc"], r["entry_id"])
            for r in json_records
        }

        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            scan_auto_resolutions(str(tmp_path), use_color=False)
        finally:
            sys.stdout = old_stdout
        rendered = buf.getvalue()

        tmux_set = self._parse_tmux_records(rendered)

        assert tmux_set, (
            "tmux window parsed no records from rendered output.\n"
            f"Rendered text:\n{rendered}"
        )
        assert json_set, (
            "JSON endpoint returned no records.\n"
            f"Response: {json_records}"
        )

        missing_from_tmux = json_set - tmux_set
        assert not missing_from_tmux, (
            "Records in JSON but not in tmux render:\n"
            + "\n".join(str(r) for r in sorted(missing_from_tmux))
            + f"\nRendered text:\n{rendered}"
        )

        missing_from_json = tmux_set - json_set
        assert not missing_from_json, (
            "Records in tmux render but not in JSON:\n"
            + "\n".join(str(r) for r in sorted(missing_from_json))
            + f"\nJSON records: {json_records}"
        )
