"""
test_cli_rest_verdict_parity.py — CLI/REST verdict parity.

Drives BOTH surfaces -- why.sh (the CLI, invoked as a real subprocess) and
GET /verdicts (the REST endpoint, invoked through the FastAPI TestClient) --
over the SAME fixture kanban root, and asserts the verdict for every project
matches between the two.  This is a comparison test, not two independently
pinned expectations: if either surface is changed to disagree with the
other (a re-derived verdict, a stale copy of the precedence logic, a text
scrape that drifts from the resolver's real fields), this test fails.  Two
tests that each assert a hardcoded "RUNNING" (or similar) would not catch
that drift.

Both surfaces resolve through the exact same function,
resolve_verdicts in team/pgai_agent_kanban/ops/verdict.py:
  - why.sh reaches it through pp_run_ops and prints its "line" field.
  - GET /verdicts calls it in process and returns the SAME resolved fields
    as JSON instead of prose.

Fixture matrix: one project per verdict word (RUNNING, BLOCKED, HALTED,
IDLE), plus a RUNNING project whose task-id/state/minutes must also match
between the two surfaces (the CLI line names them in prose; the REST JSON
carries them as fields -- both must name the SAME task, state, and minutes).
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_API_TESTS_DIR = _THIS_FILE.parent
_TEAM_DIR = _API_TESTS_DIR.parent.parent.parent  # team/
_PROJECT_ROOT = _TEAM_DIR.parent                  # project root (contains team/)
_WHY_SH = _TEAM_DIR / "scripts" / "why.sh"

_project_root_str = str(_PROJECT_ROOT)
if _project_root_str not in sys.path:
    sys.path.insert(0, _project_root_str)


# ---------------------------------------------------------------------------
# Fixture builder helpers
# ---------------------------------------------------------------------------


def _write_projects_cfg(kanban_root: pathlib.Path, projects: list[tuple[str, int, bool]]) -> None:
    lines = ["# Format: INI-style"]
    for name, priority, enabled in projects:
        lines.append(f"[project:{name}]")
        lines.append(f"priority={priority}")
        lines.append(f"enabled={'true' if enabled else 'false'}")
        lines.append("")
    (kanban_root / "projects.cfg").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _make_project_dir(kanban_root: pathlib.Path, name: str) -> pathlib.Path:
    project_dir = kanban_root / "projects" / name
    (project_dir / "tasks").mkdir(parents=True, exist_ok=True)
    return project_dir


def _write_task(project_dir: pathlib.Path, task_id: str, state: str) -> None:
    task_dir = project_dir / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "status.md").write_text(
        f"## State\n{state}\n\n## Summary\nfixture task\n", encoding="utf-8"
    )


def _build_four_verdict_fixture(kanban_root: pathlib.Path) -> None:
    """One project per verdict word, plus one aggregate walk over all four."""
    _write_projects_cfg(
        kanban_root,
        [
            ("proj-running", 1, True),
            ("proj-blocked", 2, True),
            ("proj-halted", 3, True),
            ("proj-idle", 4, True),
        ],
    )

    running_dir = _make_project_dir(kanban_root, "proj-running")
    _write_task(running_dir, "CODER-20260910-001-demo", "WORKING")

    blocked_dir = _make_project_dir(kanban_root, "proj-blocked")
    _write_task(blocked_dir, "CODER-20260910-002-demo", "BLOCKED")

    halted_dir = _make_project_dir(kanban_root, "proj-halted")
    (halted_dir / "HALT").write_text("# CM HALT: fixture\n", encoding="utf-8")

    _make_project_dir(kanban_root, "proj-idle")  # no tasks at all


# ---------------------------------------------------------------------------
# CLI-side helper: run why.sh, parse "<project>: <VERDICT>" lines
# ---------------------------------------------------------------------------


def _cli_env(kanban_root: pathlib.Path) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PGAI_PROJECT_NAME", None)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    return env


def _cli_verdicts(kanban_root: pathlib.Path) -> dict[str, str]:
    """Run why.sh with no --project and return {project: verdict_word}."""
    result = subprocess.run(
        ["bash", str(_WHY_SH)],
        capture_output=True,
        text=True,
        env=_cli_env(kanban_root),
        timeout=15,
    )
    assert result.returncode != 1, f"why.sh refused: {result.stderr!r}"

    verdicts: dict[str, str] = {}
    for line in result.stdout.strip("\n").splitlines():
        m = re.match(r"^([^:]+):\s+(RUNNING|BLOCKED|HALTED|IDLE)\b", line)
        assert m, f"CLI line does not open with '<project>: <VERDICT>': {line!r}"
        verdicts[m.group(1)] = m.group(2)
    return verdicts


def _cli_running_task_evidence(kanban_root: pathlib.Path, project: str) -> tuple[str, str, str]:
    """Run why.sh --project <name> for a RUNNING project; extract (task_id, state, minutes) from the line."""
    result = subprocess.run(
        ["bash", str(_WHY_SH), "--project", project],
        capture_output=True,
        text=True,
        env=_cli_env(kanban_root),
        timeout=15,
    )
    line = result.stdout.strip()
    m = re.search(r"task (\S+) is (WORKING), (\d+)m in that state", line)
    assert m, f"CLI RUNNING line did not name task/state/minutes: {line!r}"
    return m.group(1), m.group(2), m.group(3)


# ---------------------------------------------------------------------------
# REST-side helper: TestClient over the same fixture
# ---------------------------------------------------------------------------


def _make_client(sandbox_root: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    monkeypatch.delenv("PGAI_PROJECT_NAME", raising=False)
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(sandbox_root))
    monkeypatch.setenv("KANBAN_ROOT", str(sandbox_root))

    cfg = ApiConfig(host="127.0.0.1", port=0, kanban_root=sandbox_root)
    return TestClient(create_app(cfg=cfg), raise_server_exceptions=True)


def _rest_verdicts(client: TestClient) -> dict[str, str]:
    resp = client.get("/verdicts")
    assert resp.status_code == 200, f"GET /verdicts failed: {resp.status_code} {resp.text}"
    return {entry["project"]: entry["verdict"] for entry in resp.json()["verdicts"]}


# ---------------------------------------------------------------------------
# The parity test
# ---------------------------------------------------------------------------


class TestCliRestVerdictParity:
    def test_every_project_verdict_matches_between_cli_and_rest(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """CLI and REST agree on the verdict word for every project in a
        four-verdict fixture (RUNNING, BLOCKED, HALTED, IDLE)."""
        _build_four_verdict_fixture(tmp_path)

        cli_verdicts = _cli_verdicts(tmp_path)

        with _make_client(tmp_path, monkeypatch) as client:
            rest_verdicts = _rest_verdicts(client)

        assert cli_verdicts == rest_verdicts, (
            "CLI and REST verdict maps diverge over the SAME fixture.\n"
            f"CLI:  {cli_verdicts}\n"
            f"REST: {rest_verdicts}"
        )
        # Sanity: the fixture actually exercised all four verdict words —
        # a parity test whose fixture collapsed to one verdict would not
        # catch a precedence-order regression.
        assert set(cli_verdicts.values()) == {"RUNNING", "BLOCKED", "HALTED", "IDLE"}

    def test_running_task_evidence_matches_between_cli_line_and_rest_fields(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """For the RUNNING project, the CLI line's task id/state/minutes
        match the REST entry's task_id/task_state/minutes_in_state fields."""
        _build_four_verdict_fixture(tmp_path)

        cli_task_id, cli_state, cli_minutes = _cli_running_task_evidence(tmp_path, "proj-running")

        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/verdicts", params={"project": "proj-running"})
        assert resp.status_code == 200
        entry = resp.json()["verdicts"][0]

        assert entry["task_id"] == cli_task_id
        assert entry["task_state"] == cli_state
        assert str(entry["minutes_in_state"]) == cli_minutes

    def test_changing_the_fixture_state_changes_both_surfaces_in_lockstep(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A single fixture mutation (RUNNING -> BLOCKED) is reflected
        identically by both surfaces, proving they read the same underlying
        state rather than each carrying its own pinned expectation."""
        _write_projects_cfg(tmp_path, [("demo", 1, True)])
        project_dir = _make_project_dir(tmp_path, "demo")
        status_file = project_dir / "tasks" / "CODER-20260910-001-demo" / "status.md"
        status_file.parent.mkdir(parents=True, exist_ok=True)
        status_file.write_text("## State\nWORKING\n", encoding="utf-8")

        cli_before = _cli_verdicts(tmp_path)
        with _make_client(tmp_path, monkeypatch) as client:
            rest_before = _rest_verdicts(client)
        assert cli_before == rest_before == {"demo": "RUNNING"}

        status_file.write_text("## State\nBLOCKED\n", encoding="utf-8")

        cli_after = _cli_verdicts(tmp_path)
        with _make_client(tmp_path, monkeypatch) as client:
            rest_after = _rest_verdicts(client)
        assert cli_after == rest_after == {"demo": "BLOCKED"}
