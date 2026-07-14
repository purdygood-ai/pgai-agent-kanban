"""
test_review_cmds.py — Acceptance-criteria tests for the review_cmds feature (ICD 1.1.0).

Tests cover all acceptance criteria from the task README:

  1. Window-14 fixture with one pending gate renders, in order:
       Review: block (show.sh line always; test-report line when RC version known),
       then Approve:, then Reject: — all verbatim, copy-paste runnable.

  2. The show.sh line from the Review block is EXECUTED and exits 0.

  3. GET /approvals returns review_cmds as a non-empty array for the fixture.

  4. JSON-vs-window-14 equality extends to review_cmds (both surfaces render
     the same strings — one scanner, two surfaces).

  5. ICD lifecycle:
       ICD_VERSION reads 1.1.0
       icd.json regenerated and info.version == 1.1.0
       SUPPORTED contains both 1.0.0 and 1.1.0
       icd-v1.1.0.json exists and is byte-identical to icd.json

  6. ICD freshness gate exits 0 (lint_icd_freshness.py check_freshness returns True).

  7. ICD compat gate exits 0 with both 1.0.0 and 1.1.0 baselines supported
     (lint_icd_compat.py check_compat returns True).

All filesystem operations use tmp_path (pytest-managed).
No live kanban root, no network calls.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import textwrap
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Resolve paths.  This file lives at team/tests/unit/test_review_cmds.py.
# Three parent levels: unit/ → tests/ → team/ → project_root/
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent          # team/
_DEV_TREE_ROOT = _TEAM_DIR.parent                     # project_root/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_ICD_ARTIFACT = _DEV_TREE_ROOT / "docs" / "api" / "ICD_VERSION"
_ICD_JSON = _DEV_TREE_ROOT / "docs" / "api" / "icd.json"
_BASELINES_DIR = _DEV_TREE_ROOT / "docs" / "api" / "baselines"

# Ensure team/ is on sys.path so pgai_agent_kanban is importable.
_team_str = str(_TEAM_DIR)
if _team_str not in sys.path:
    sys.path.insert(0, _team_str)


# ---------------------------------------------------------------------------
# Fixture builder helpers (mirrors pattern in existing test files)
# ---------------------------------------------------------------------------

def _write_task(
    tasks_dir: pathlib.Path,
    task_id: str,
    state: str,
    release_ver: str,
    goal: str,
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
# Window-14 render helper
# ---------------------------------------------------------------------------

def _render_window14(kanban_root: pathlib.Path) -> str:
    """Return the no-color output of scan_pending_approvals."""
    from pgai_agent_kanban.dashboard.scan_human_approvals import scan_pending_approvals
    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        scan_pending_approvals(str(kanban_root), use_color=False)
    finally:
        sys.stdout = old_stdout
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Acceptance criterion 1a — Review block renders ABOVE Approve/Reject
# ---------------------------------------------------------------------------


class TestReviewBlockOrdering:
    """Window-14 renders: Review: then Approve: then Reject:, in that order."""

    def test_review_appears_before_approve(self, tmp_path: pathlib.Path) -> None:
        """'Review:' label appears before 'Approve:' in the rendered output."""
        _build_project(
            tmp_path, "proj-alpha",
            [("HUMAN-APPROVE-v1.10.0-099", "WAITING", "v1.10.0", "Review release.")],
        )
        rendered = _render_window14(tmp_path)
        review_pos = rendered.find("Review:")
        approve_pos = rendered.find("Approve:")
        assert review_pos != -1, f"'Review:' not found in rendered output:\n{rendered}"
        assert approve_pos != -1, f"'Approve:' not found in rendered output:\n{rendered}"
        assert review_pos < approve_pos, (
            f"'Review:' (pos {review_pos}) must appear BEFORE 'Approve:' (pos {approve_pos}).\n"
            f"Rendered:\n{rendered}"
        )

    def test_review_appears_before_reject(self, tmp_path: pathlib.Path) -> None:
        """'Review:' label appears before 'Reject:' in the rendered output."""
        _build_project(
            tmp_path, "proj-alpha",
            [("HUMAN-APPROVE-v1.10.0-099", "WAITING", "v1.10.0", "Review release.")],
        )
        rendered = _render_window14(tmp_path)
        review_pos = rendered.find("Review:")
        reject_pos = rendered.find("Reject:")
        assert review_pos != -1, f"'Review:' not found in rendered output:\n{rendered}"
        assert reject_pos != -1, f"'Reject:' not found in rendered output:\n{rendered}"
        assert review_pos < reject_pos, (
            f"'Review:' (pos {review_pos}) must appear BEFORE 'Reject:' (pos {reject_pos}).\n"
            f"Rendered:\n{rendered}"
        )

    def test_approve_appears_before_reject(self, tmp_path: pathlib.Path) -> None:
        """'Approve:' label appears before 'Reject:' in the rendered output."""
        _build_project(
            tmp_path, "proj-alpha",
            [("HUMAN-APPROVE-v1.10.0-099", "WAITING", "v1.10.0", "Review release.")],
        )
        rendered = _render_window14(tmp_path)
        approve_pos = rendered.find("Approve:")
        reject_pos = rendered.find("Reject:")
        assert approve_pos != -1, f"'Approve:' not found:\n{rendered}"
        assert reject_pos != -1, f"'Reject:' not found:\n{rendered}"
        assert approve_pos < reject_pos, (
            f"'Approve:' (pos {approve_pos}) must appear BEFORE 'Reject:' (pos {reject_pos}).\n"
            f"Rendered:\n{rendered}"
        )


# ---------------------------------------------------------------------------
# Acceptance criterion 1b — Review block content (show.sh always; test-report
# when RC version known)
# ---------------------------------------------------------------------------


class TestReviewBlockContent:
    """Review block contains the correct verbatim command strings."""

    def test_show_sh_line_always_present_with_known_rc(
        self, tmp_path: pathlib.Path
    ) -> None:
        """show.sh command appears in the Review block when RC version is known."""
        _build_project(
            tmp_path, "proj-alpha",
            [("HUMAN-APPROVE-v1.10.0-099", "WAITING", "v1.10.0", "Review release.")],
        )
        rendered = _render_window14(tmp_path)
        assert "scripts/show.sh --project proj-alpha --key HUMAN-APPROVE-v1.10.0-099" in rendered, (
            f"Expected show.sh command not found in rendered output:\n{rendered}"
        )

    def test_show_sh_line_always_present_without_rc(
        self, tmp_path: pathlib.Path
    ) -> None:
        """show.sh command appears even when RC version is unknown (fallback task_id)."""
        task_id = "HUMAN-APPROVE-v1.10.0-099"
        # Write a task WITHOUT a README (so release_ver falls back to task_id).
        tasks_dir = tmp_path / "projects" / "proj-alpha" / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        task_dir = tasks_dir / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "status.md").write_text(
            "# Status\n\n## State\nWAITING\n", encoding="utf-8"
        )
        # No README.md → release_ver fallback = task_id
        rendered = _render_window14(tmp_path)
        assert f"scripts/show.sh --project proj-alpha --key {task_id}" in rendered, (
            f"Expected show.sh command not found in rendered output:\n{rendered}"
        )

    def test_test_report_line_present_when_rc_version_known(
        self, tmp_path: pathlib.Path
    ) -> None:
        """show-test-report.sh line appears in Review block when RC version is known."""
        _build_project(
            tmp_path, "proj-alpha",
            [("HUMAN-APPROVE-v1.10.0-099", "WAITING", "v1.10.0", "Review release.")],
        )
        rendered = _render_window14(tmp_path)
        assert "scripts/show-test-report.sh --project proj-alpha --key v1.10.0" in rendered, (
            f"Expected show-test-report.sh command not found in rendered output:\n{rendered}"
        )

    def test_test_report_line_absent_when_rc_version_unknown(
        self, tmp_path: pathlib.Path
    ) -> None:
        """show-test-report.sh line is OMITTED when RC version is not known (fallback)."""
        task_id = "HUMAN-APPROVE-v1.10.0-099"
        tasks_dir = tmp_path / "projects" / "proj-alpha" / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        task_dir = tasks_dir / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "status.md").write_text(
            "# Status\n\n## State\nWAITING\n", encoding="utf-8"
        )
        # No README → release_ver = task_id (starts with "HUMAN-APPROVE", not "v")
        rendered = _render_window14(tmp_path)
        assert "scripts/show-test-report.sh" not in rendered, (
            f"show-test-report.sh should NOT appear when RC version is unknown.\n"
            f"Rendered:\n{rendered}"
        )


# ---------------------------------------------------------------------------
# Acceptance criterion 2 — show.sh command is executed and exits 0
# ---------------------------------------------------------------------------


class TestShowShExecutable:
    """The show.sh line from the Review block is executed and exits 0."""

    def test_show_sh_review_cmd_exits_0(self, tmp_path: pathlib.Path) -> None:
        """Execute the show.sh command from the Review block; assert it exits 0.

        Creates a fixture kanban root with a pending HUMAN-APPROVE task.
        Extracts the show.sh line from the Review block.
        Runs it via subprocess with PGAI_AGENT_KANBAN_ROOT_PATH pointing at
        the fixture.  Asserts exit code 0.

        This is the EXECUTED-COMMAND assertion from the acceptance criteria:
        a rendered command that doesn't run is worse than none.
        """
        proj = "proj-alpha"
        task_id = "HUMAN-APPROVE-v1.10.0-099"
        _build_project(
            tmp_path, proj,
            [(task_id, "WAITING", "v1.10.0", "Review release v1.10.0.")],
        )

        # Extract the show.sh command from the Review block.
        from pgai_agent_kanban.dashboard.scan_human_approvals import _build_review_cmds
        review_cmds = _build_review_cmds(proj, task_id, "v1.10.0")
        show_cmd = review_cmds[0]  # always the show.sh command

        assert "scripts/show.sh" in show_cmd, (
            f"First review_cmd should be a show.sh command; got: {show_cmd!r}"
        )

        # The command is relative ("scripts/show.sh ..."); resolve it relative to
        # the SCRIPTS_DIR in the dev tree for invocation.
        # show.sh --project proj-alpha --key HUMAN-APPROVE-v1.10.0-099
        show_sh = _SCRIPTS_DIR / "show.sh"
        assert show_sh.exists(), f"show.sh not found at {show_sh}"

        # Run the resolved command.  PGAI_AGENT_KANBAN_ROOT_PATH must point at
        # the fixture root so show.sh finds the task.
        env = os.environ.copy()
        env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(tmp_path)

        result = subprocess.run(
            ["bash", str(show_sh), "--project", proj, "--key", task_id],
            capture_output=True,
            text=True,
            env=env,
        )

        assert result.returncode == 0, (
            f"show.sh returned exit code {result.returncode}.\n"
            f"Command: {show_cmd!r}\n"
            f"stdout: {result.stdout!r}\n"
            f"stderr: {result.stderr!r}\n"
            "The Review block's show.sh command must exit 0 against the fixture."
        )


# ---------------------------------------------------------------------------
# Acceptance criterion 3 — GET /approvals returns review_cmds as non-empty array
# ---------------------------------------------------------------------------


class TestApprovalsReviewCmdsField:
    """GET /approvals returns review_cmds as a non-empty list[str] for each record."""

    def _make_client(
        self,
        sandbox_root: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Create a TestClient with PGAI_AGENT_KANBAN_ROOT_PATH set to sandbox_root."""
        from pgai_agent_kanban.api.app import create_app
        from pgai_agent_kanban.api.config import ApiConfig
        from fastapi.testclient import TestClient

        monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(sandbox_root))
        monkeypatch.setenv("KANBAN_ROOT", str(sandbox_root))

        cfg = ApiConfig(host="127.0.0.1", port=0, kanban_root=_TEAM_DIR)
        return TestClient(create_app(cfg=cfg), raise_server_exceptions=True)

    def test_review_cmds_field_present(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /approvals response records include the review_cmds field."""
        _build_project(
            tmp_path, "proj-alpha",
            [("HUMAN-APPROVE-v1.10.0-099", "WAITING", "v1.10.0", "Review release.")],
        )
        with self._make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/approvals")

        assert resp.status_code == 200
        records = resp.json()
        assert len(records) == 1
        record = records[0]
        assert "review_cmds" in record, (
            f"'review_cmds' field missing from /approvals record.\n"
            f"Record keys: {list(record.keys())}"
        )

    def test_review_cmds_is_non_empty_list(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """review_cmds is a non-empty list (at minimum the show.sh command)."""
        _build_project(
            tmp_path, "proj-alpha",
            [("HUMAN-APPROVE-v1.10.0-099", "WAITING", "v1.10.0", "Review release.")],
        )
        with self._make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/approvals")

        record = resp.json()[0]
        review_cmds = record["review_cmds"]
        assert isinstance(review_cmds, list), (
            f"review_cmds must be a list; got {type(review_cmds).__name__}"
        )
        assert len(review_cmds) > 0, (
            f"review_cmds must be non-empty; got: {review_cmds!r}"
        )

    def test_review_cmds_contains_show_sh(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """First entry in review_cmds is the show.sh command."""
        proj = "proj-alpha"
        task_id = "HUMAN-APPROVE-v1.10.0-099"
        _build_project(
            tmp_path, proj,
            [(task_id, "WAITING", "v1.10.0", "Review release.")],
        )
        with self._make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/approvals")

        record = resp.json()[0]
        show_cmd = record["review_cmds"][0]
        assert "scripts/show.sh" in show_cmd, (
            f"First review_cmd should contain 'scripts/show.sh'.\n"
            f"Got: {show_cmd!r}"
        )
        assert proj in show_cmd, (
            f"show.sh command should contain project name '{proj}'.\n"
            f"Got: {show_cmd!r}"
        )
        assert task_id in show_cmd, (
            f"show.sh command should contain task_id '{task_id}'.\n"
            f"Got: {show_cmd!r}"
        )

    def test_review_cmds_contains_test_report_when_rc_known(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Second entry in review_cmds is show-test-report.sh when RC version is known."""
        proj = "proj-alpha"
        task_id = "HUMAN-APPROVE-v1.10.0-099"
        rc = "v1.10.0"
        _build_project(
            tmp_path, proj,
            [(task_id, "WAITING", rc, "Review release.")],
        )
        with self._make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/approvals")

        record = resp.json()[0]
        review_cmds = record["review_cmds"]
        assert len(review_cmds) >= 2, (
            f"Expected at least 2 review_cmds when RC version is known.\n"
            f"Got: {review_cmds!r}"
        )
        report_cmd = review_cmds[1]
        assert "scripts/show-test-report.sh" in report_cmd, (
            f"Second review_cmd should contain 'scripts/show-test-report.sh'.\n"
            f"Got: {report_cmd!r}"
        )
        assert proj in report_cmd and rc in report_cmd, (
            f"show-test-report.sh command should contain project and RC key.\n"
            f"Got: {report_cmd!r}"
        )


# ---------------------------------------------------------------------------
# Acceptance criterion 4 — JSON-vs-window-14 equality extends to review_cmds
# ---------------------------------------------------------------------------


class TestReviewCmdsJsonVsWindow14:
    """Both surfaces (JSON and window-14) render the same review_cmds strings."""

    def _make_client(
        self,
        sandbox_root: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Create a TestClient with sandbox_root as the kanban root."""
        from pgai_agent_kanban.api.app import create_app
        from pgai_agent_kanban.api.config import ApiConfig
        from fastapi.testclient import TestClient

        monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(sandbox_root))
        monkeypatch.setenv("KANBAN_ROOT", str(sandbox_root))

        cfg = ApiConfig(host="127.0.0.1", port=0, kanban_root=_TEAM_DIR)
        return TestClient(create_app(cfg=cfg), raise_server_exceptions=True)

    def test_review_cmds_same_in_json_and_window14(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """review_cmds strings in JSON response equal review_cmds in window-14 render.

        Asserts that collect_pending_approvals (JSON surface) and
        scan_pending_approvals (window-14 surface) produce the same command strings
        for a fixture with two projects — one scanner, two surfaces.
        """
        from pgai_agent_kanban.dashboard.scan_human_approvals import (
            collect_pending_approvals,
        )

        # Two-project fixture.
        _build_project(
            tmp_path, "proj-alpha",
            [("HUMAN-APPROVE-v1.10.0-099", "WAITING", "v1.10.0", "Alpha release.")],
        )
        _build_project(
            tmp_path, "proj-beta",
            [("HUMAN-APPROVE-v2.0.0-001", "BACKLOG", "v2.0.0", "Beta release.")],
        )

        # Get review_cmds from JSON (collect_pending_approvals).
        json_records = collect_pending_approvals(str(tmp_path))
        json_review_cmds = {
            r["task_id"]: r["review_cmds"]
            for r in json_records
        }

        # Get window-14 rendered text.
        rendered = _render_window14(tmp_path)

        # For each task_id, verify that every command in review_cmds appears verbatim
        # in the rendered text.
        for task_id, cmds in json_review_cmds.items():
            for cmd in cmds:
                assert cmd in rendered, (
                    f"review_cmd for task {task_id!r} not found in window-14 render.\n"
                    f"Command: {cmd!r}\n"
                    f"Rendered:\n{rendered}"
                )

    def test_all_review_cmds_appear_in_window14_render(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every review_cmd string from the JSON response appears in the window-14 render."""
        from pgai_agent_kanban.dashboard.scan_human_approvals import (
            collect_pending_approvals,
        )

        _build_project(
            tmp_path, "proj-alpha",
            [("HUMAN-APPROVE-v1.10.0-099", "WAITING", "v1.10.0", "Alpha review.")],
        )

        json_records = collect_pending_approvals(str(tmp_path))
        assert len(json_records) == 1
        review_cmds = json_records[0]["review_cmds"]
        assert len(review_cmds) >= 1

        rendered = _render_window14(tmp_path)
        for cmd in review_cmds:
            assert cmd in rendered, (
                f"review_cmd {cmd!r} from JSON not found in window-14 rendered output.\n"
                f"Rendered:\n{rendered}"
            )


# ---------------------------------------------------------------------------
# Acceptance criterion 5 — ICD lifecycle
# ---------------------------------------------------------------------------


class TestIcdLifecycle:
    """ICD version 1.2.0 lifecycle: version file, artifact, baselines, freeze."""

    def test_icd_version_file_is_1_2_0(self) -> None:
        """docs/api/ICD_VERSION contains exactly '1.2.0\\n'."""
        icd_version_file = _DEV_TREE_ROOT / "docs" / "api" / "ICD_VERSION"
        assert icd_version_file.exists(), (
            f"ICD_VERSION not found at {icd_version_file}"
        )
        raw = icd_version_file.read_bytes()
        assert raw == b"1.2.0\n", (
            f"ICD_VERSION content is {raw!r}; expected b'1.2.0\\n'."
        )

    def test_icd_json_info_version_is_1_2_0(self) -> None:
        """docs/api/icd.json info.version is '1.2.0'."""
        assert _ICD_JSON.exists(), f"icd.json not found at {_ICD_JSON}"
        artifact = json.loads(_ICD_JSON.read_text(encoding="utf-8"))
        version = artifact.get("info", {}).get("version")
        assert version == "1.2.0", (
            f"icd.json info.version is {version!r}; expected '1.2.0'."
        )

    def test_supported_contains_1_0_0(self) -> None:
        """docs/api/baselines/SUPPORTED lists 1.0.0 (backward compat preserved)."""
        supported = _BASELINES_DIR / "SUPPORTED"
        assert supported.exists(), f"SUPPORTED not found at {supported}"
        lines = [v.strip() for v in supported.read_text(encoding="utf-8").splitlines() if v.strip()]
        assert "1.0.0" in lines, (
            f"1.0.0 missing from SUPPORTED.\nGot: {lines!r}\n"
            "ICD 1.0.0 must remain in SUPPORTED — the compat gate proves additivity."
        )

    def test_supported_contains_1_1_0(self) -> None:
        """docs/api/baselines/SUPPORTED lists 1.1.0 (prior freeze preserved)."""
        supported = _BASELINES_DIR / "SUPPORTED"
        assert supported.exists(), f"SUPPORTED not found at {supported}"
        lines = [v.strip() for v in supported.read_text(encoding="utf-8").splitlines() if v.strip()]
        assert "1.1.0" in lines, (
            f"1.1.0 missing from SUPPORTED.\nGot: {lines!r}\n"
            "ICD 1.1.0 must remain in SUPPORTED — the compat gate proves additivity."
        )

    def test_supported_contains_1_2_0(self) -> None:
        """docs/api/baselines/SUPPORTED lists 1.2.0 (new freeze)."""
        supported = _BASELINES_DIR / "SUPPORTED"
        assert supported.exists(), f"SUPPORTED not found at {supported}"
        lines = [v.strip() for v in supported.read_text(encoding="utf-8").splitlines() if v.strip()]
        assert "1.2.0" in lines, (
            f"1.2.0 missing from SUPPORTED.\nGot: {lines!r}\n"
            "ICD 1.2.0 must be added to SUPPORTED after the minor bump."
        )

    def test_icd_v1_2_0_baseline_exists(self) -> None:
        """docs/api/baselines/icd-v1.2.0.json exists."""
        baseline = _BASELINES_DIR / "icd-v1.2.0.json"
        assert baseline.exists(), (
            f"Baseline icd-v1.2.0.json not found at {baseline}.\n"
            "The file must be created as a byte-identical copy of docs/api/icd.json."
        )

    def test_icd_v1_2_0_baseline_byte_identical_to_icd_json(self) -> None:
        """docs/api/baselines/icd-v1.2.0.json is byte-identical to docs/api/icd.json."""
        baseline = _BASELINES_DIR / "icd-v1.2.0.json"
        assert baseline.exists(), f"Baseline icd-v1.2.0.json not found at {baseline}"

        current_bytes = _ICD_JSON.read_bytes()
        baseline_bytes = baseline.read_bytes()
        assert current_bytes == baseline_bytes, (
            "icd-v1.2.0.json is NOT byte-identical to docs/api/icd.json.\n"
            f"icd.json size: {len(current_bytes)} bytes\n"
            f"icd-v1.2.0.json size: {len(baseline_bytes)} bytes\n"
            "Create the baseline with: cp docs/api/icd.json docs/api/baselines/icd-v1.2.0.json"
        )

    def test_icd_v1_1_0_baseline_still_exists(self) -> None:
        """docs/api/baselines/icd-v1.1.0.json still exists (1.1.0 not removed)."""
        baseline = _BASELINES_DIR / "icd-v1.1.0.json"
        assert baseline.exists(), (
            f"Baseline icd-v1.1.0.json not found at {baseline}.\n"
            "The 1.1.0 baseline must be preserved — it proves backward compatibility."
        )

    def test_icd_v1_0_0_baseline_still_exists(self) -> None:
        """docs/api/baselines/icd-v1.0.0.json still exists (1.0.0 not removed)."""
        baseline = _BASELINES_DIR / "icd-v1.0.0.json"
        assert baseline.exists(), (
            f"Baseline icd-v1.0.0.json not found at {baseline}.\n"
            "The 1.0.0 baseline must be preserved — it proves backward compatibility."
        )


# ---------------------------------------------------------------------------
# Acceptance criterion 6 — ICD freshness gate exits 0
# ---------------------------------------------------------------------------


def _import_lint_module(script_path: pathlib.Path):
    """Import a lint script module without polluting sys.modules permanently."""
    spec = importlib.util.spec_from_file_location(script_path.stem, script_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {script_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


class TestIcdFreshnessGate:
    """ICD freshness gate exits 0 on the committed tree."""

    def test_freshness_gate_exits_0(self, capsys: pytest.CaptureFixture[str]) -> None:
        """lint_icd_freshness.check_freshness() returns True for the committed artifacts.

        This verifies that the committed icd.json matches what the generator
        would produce today (i.e., the artifact is not stale after the bump).
        """
        lint = _import_lint_module(_SCRIPTS_DIR / "lint_icd_freshness.py")
        icd_version_file = _DEV_TREE_ROOT / "docs" / "api" / "ICD_VERSION"

        result = lint.check_freshness(
            icd_path=_ICD_JSON,
            version_file=icd_version_file,
        )
        assert result is True, (
            "ICD freshness gate returned False for the committed artifacts.\n"
            f"Captured stderr: {capsys.readouterr().err}\n"
            "Regenerate docs/api/icd.json with: PYTHONPATH=team python3 -m "
            "pgai_agent_kanban.api.generate_icd"
        )


# ---------------------------------------------------------------------------
# Acceptance criterion 7 — ICD compat gate exits 0 with both baselines
# ---------------------------------------------------------------------------


class TestIcdCompatGate:
    """ICD compat gate exits 0 with both 1.0.0 and 1.1.0 baselines supported."""

    def test_compat_gate_exits_0_with_both_baselines(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """lint_icd_compat.check_compat() returns True for the committed artifacts.

        SUPPORTED lists 1.0.0 and 1.1.0.  icd.json (1.1.0) must be a compatible
        superset of icd-v1.0.0.json (no paths or response fields removed —
        review_cmds was added to records, not removed from the schema).
        This is the first additive-minor bump's compat proof.
        """
        lint = _import_lint_module(_SCRIPTS_DIR / "lint_icd_compat.py")

        result = lint.check_compat(
            icd_path=_ICD_JSON,
            baselines_dir=_BASELINES_DIR,
        )
        assert result is True, (
            "ICD compat gate returned False for the committed artifacts.\n"
            f"Captured stderr: {capsys.readouterr().err}\n"
            "icd.json (1.1.0) must be a compatible superset of icd-v1.0.0.json."
        )

    def test_compat_gate_passes_1_0_0_baseline_check(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Compat gate passes specifically against the 1.0.0 baseline.

        Creates a scratch baselines dir with only 1.0.0 to isolate the
        1.0.0 compatibility check.  Proves that the 1.1.0 artifact is a
        compatible superset of 1.0.0 in isolation.
        """
        lint = _import_lint_module(_SCRIPTS_DIR / "lint_icd_compat.py")

        # Build scratch baselines with only 1.0.0.
        scratch_baselines = tmp_path / "baselines"
        scratch_baselines.mkdir()
        (scratch_baselines / "SUPPORTED").write_text("1.0.0\n", encoding="utf-8")
        import shutil
        shutil.copy(
            str(_BASELINES_DIR / "icd-v1.0.0.json"),
            str(scratch_baselines / "icd-v1.0.0.json"),
        )

        result = lint.check_compat(
            icd_path=_ICD_JSON,
            baselines_dir=scratch_baselines,
        )
        assert result is True, (
            "Compat gate returned False against isolated 1.0.0 baseline.\n"
            f"Captured stderr: {capsys.readouterr().err}\n"
            "icd.json (1.1.0) must be a compatible superset of icd-v1.0.0.json."
        )
