"""
test_verdicts_endpoint.py — Tests for GET /verdicts endpoint.

Covers:
  1. Envelope shape — response is {"verdicts": [...], "warnings": [...]}.
  2. Empty state — no registered projects returns {"verdicts": [], ...} at 200.
  3. Priority order — verdicts array follows projects.cfg priority order.
  4. Field presence — a resolved entry carries verdict/exit_code/cause/
     remedy/line; RUNNING additionally carries task_id/task_state/
     minutes_in_state; IDLE/HALTED carry none of those three.
  5. Registered-but-unresolvable project directory — 200 with an "error"
     entry, never a 500 and never silently dropped.
  6. project= filter — narrows to one project; unregistered name returns 422.
  7. warn_unknown_query_params — an undeclared query parameter produces a
     warning entry in the response, consistent with the other read routes.
  8. Path confinement / verb discipline — GET /verdicts never mutates the
     sandbox.
  9. details=true release_locks — each entry carries held_seconds from the
     lock record's acquired_at (about 10800 for a lock acquired 3h ago and
     refreshed just now, while age_seconds, the refresh age, is about 0),
     and held_seconds is null for a record without acquired_at.

Design notes
-----------
- All tests use the FastAPI TestClient (no real network socket).
- Fixture sandboxes are built under tmp_path (no bare /tmp paths).
- PGAI_AGENT_KANBAN_ROOT_PATH is monkeypatched to point at the sandbox
  (mirrors test_approvals.py's _make_client pattern): data (projects.cfg,
  project directories) is read from the sandbox via the env var, the root
  the endpoint's in-process resolve_verdicts call reads, while
  ApiConfig.kanban_root stays pointed at the real dev tree's team/.
"""

from __future__ import annotations

import datetime
import os
import pathlib
import sys

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
# Fixture builder helpers
# ---------------------------------------------------------------------------


def _write_projects_cfg(kanban_root: pathlib.Path, projects: list[tuple[str, int, bool]]) -> None:
    """projects: list of (name, priority, enabled)."""
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


# ---------------------------------------------------------------------------
# App factory and TestClient fixture
# ---------------------------------------------------------------------------


def _make_client(
    sandbox_root: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    """Create a TestClient with PGAI_AGENT_KANBAN_ROOT_PATH set to sandbox_root.

    The ApiConfig kanban_root is the sandbox root: the endpoint reads its
    data (projects.cfg, project directories) from the server's own
    configured root, the one every route uses.
    """
    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    monkeypatch.delenv("PGAI_PROJECT_NAME", raising=False)
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(sandbox_root))
    monkeypatch.setenv("KANBAN_ROOT", str(sandbox_root))

    cfg = ApiConfig(
        host="127.0.0.1",
        port=0,
        kanban_root=sandbox_root,
    )
    return TestClient(create_app(cfg=cfg), raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# 1 & 2. Envelope shape and empty state
# ---------------------------------------------------------------------------


class TestVerdictsEnvelope:
    def test_empty_kanban_root_returns_empty_verdicts_array(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/verdicts")

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body == {"verdicts": [], "warnings": []}

    def test_response_is_object_with_verdicts_and_warnings_keys(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_projects_cfg(tmp_path, [("demo", 1, True)])
        _make_project_dir(tmp_path, "demo")
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/verdicts")

        body = resp.json()
        assert set(body.keys()) == {"verdicts", "warnings"}
        assert isinstance(body["verdicts"], list)
        assert isinstance(body["warnings"], list)


# ---------------------------------------------------------------------------
# 3. Priority order
# ---------------------------------------------------------------------------


class TestVerdictsPriorityOrder:
    def test_verdicts_follow_projects_cfg_priority_order(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Registered in reverse-priority order in the cfg file.
        _write_projects_cfg(tmp_path, [("zeta", 2, True), ("alpha", 1, True)])
        _make_project_dir(tmp_path, "zeta")
        _make_project_dir(tmp_path, "alpha")

        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/verdicts")

        verdicts = resp.json()["verdicts"]
        assert [v["project"] for v in verdicts] == ["alpha", "zeta"]


# ---------------------------------------------------------------------------
# 4. Field presence per verdict
# ---------------------------------------------------------------------------


class TestVerdictsFieldPresence:
    def test_idle_entry_has_no_task_fields(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_projects_cfg(tmp_path, [("demo", 1, True)])
        _make_project_dir(tmp_path, "demo")
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/verdicts")

        entry = resp.json()["verdicts"][0]
        assert entry["verdict"] == "IDLE"
        assert entry["exit_code"] == 2
        assert entry["cause"]
        assert entry["remedy"]
        assert entry["line"].startswith("demo: IDLE")
        assert "task_id" not in entry
        assert "task_state" not in entry
        assert "minutes_in_state" not in entry

    def test_running_entry_carries_task_id_state_and_minutes_in_state(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_projects_cfg(tmp_path, [("demo", 1, True)])
        project_dir = _make_project_dir(tmp_path, "demo")
        _write_task(project_dir, "CODER-20260910-001-demo", "WORKING")

        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/verdicts")

        entry = resp.json()["verdicts"][0]
        assert entry["verdict"] == "RUNNING"
        assert entry["task_id"] == "CODER-20260910-001-demo"
        assert entry["task_state"] == "WORKING"
        assert isinstance(entry["minutes_in_state"], int)

    def test_blocked_entry_carries_task_id_and_state_but_not_minutes(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_projects_cfg(tmp_path, [("demo", 1, True)])
        project_dir = _make_project_dir(tmp_path, "demo")
        _write_task(project_dir, "CODER-20260910-001-demo", "BLOCKED")

        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/verdicts")

        entry = resp.json()["verdicts"][0]
        assert entry["verdict"] == "BLOCKED"
        assert entry["task_id"] == "CODER-20260910-001-demo"
        assert entry["task_state"] == "BLOCKED"
        assert "minutes_in_state" not in entry

    def test_halted_entry_names_halt_file_and_has_no_task_fields(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_projects_cfg(tmp_path, [("demo", 1, True)])
        project_dir = _make_project_dir(tmp_path, "demo")
        (project_dir / "HALT").write_text("# operator pause\n", encoding="utf-8")

        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/verdicts")

        entry = resp.json()["verdicts"][0]
        assert entry["verdict"] == "HALTED"
        assert entry["exit_code"] == 5
        assert "task_id" not in entry
        assert "minutes_in_state" not in entry


# ---------------------------------------------------------------------------
# 5. Registered-but-unresolvable project directory
# ---------------------------------------------------------------------------


class TestVerdictsUnresolvableProject:
    def test_registered_project_with_no_directory_yields_error_entry_not_500(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_projects_cfg(tmp_path, [("ghost", 1, True)])
        # Deliberately do not create projects/ghost/.

        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/verdicts")

        assert resp.status_code == 200
        assert resp.status_code < 500
        entry = resp.json()["verdicts"][0]
        assert entry["project"] == "ghost"
        assert entry["error"] == "project directory not found"
        assert "verdict" not in entry
        assert entry["cause"]
        assert entry["remedy"]

    def test_unresolvable_project_is_not_silently_dropped_from_the_walk(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_projects_cfg(tmp_path, [("alpha", 1, True), ("ghost", 2, True)])
        _make_project_dir(tmp_path, "alpha")

        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/verdicts")

        verdicts = resp.json()["verdicts"]
        assert len(verdicts) == 2, f"expected both projects present, got: {verdicts!r}"
        assert {v["project"] for v in verdicts} == {"alpha", "ghost"}

    def test_project_filter_on_unresolvable_but_registered_project_is_200(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_projects_cfg(tmp_path, [("ghost", 1, True)])

        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/verdicts", params={"project": "ghost"})

        assert resp.status_code == 200
        verdicts = resp.json()["verdicts"]
        assert len(verdicts) == 1
        assert verdicts[0]["error"] == "project directory not found"


# ---------------------------------------------------------------------------
# 6. project= filter and unregistered-name 422
# ---------------------------------------------------------------------------


class TestVerdictsProjectFilter:
    def test_project_filter_returns_only_that_project(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_projects_cfg(tmp_path, [("alpha", 1, True), ("beta", 2, True)])
        _make_project_dir(tmp_path, "alpha")
        _make_project_dir(tmp_path, "beta")

        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/verdicts", params={"project": "beta"})

        verdicts = resp.json()["verdicts"]
        assert len(verdicts) == 1
        assert verdicts[0]["project"] == "beta"

    def test_unregistered_project_name_returns_422(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_projects_cfg(tmp_path, [("alpha", 1, True)])
        _make_project_dir(tmp_path, "alpha")

        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/verdicts", params={"project": "does-not-exist"})

        assert resp.status_code == 422
        assert "does-not-exist" in resp.json().get("detail", "")


# ---------------------------------------------------------------------------
# 7. warn_unknown_query_params
# ---------------------------------------------------------------------------


class TestVerdictsUnknownQueryParam:
    def test_unknown_query_parameter_produces_a_warning(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_projects_cfg(tmp_path, [("demo", 1, True)])
        _make_project_dir(tmp_path, "demo")

        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/verdicts", params={"bogus": "1"})

        assert resp.status_code == 200
        warnings = resp.json()["warnings"]
        assert any("bogus" in w for w in warnings), f"expected a warning naming 'bogus', got: {warnings!r}"

    def test_no_unknown_parameters_produces_no_warnings(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_projects_cfg(tmp_path, [("demo", 1, True)])
        _make_project_dir(tmp_path, "demo")

        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/verdicts")

        assert resp.json()["warnings"] == []


# ---------------------------------------------------------------------------
# 8. Path confinement / verb discipline
# ---------------------------------------------------------------------------


class TestVerdictsPathConfinement:
    def _checksum_dir(self, root: pathlib.Path) -> dict[str, bytes]:
        result = {}
        if not root.is_dir():
            return result
        for p in sorted(root.rglob("*")):
            if p.is_file():
                rel = str(p.relative_to(root))
                result[rel] = p.read_bytes()
        return result

    def test_verdicts_does_not_mutate_the_sandbox(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_projects_cfg(tmp_path, [("demo", 1, True)])
        project_dir = _make_project_dir(tmp_path, "demo")
        _write_task(project_dir, "CODER-20260910-001-demo", "WORKING")

        before = self._checksum_dir(tmp_path)
        with _make_client(tmp_path, monkeypatch) as client:
            client.get("/verdicts")
        after = self._checksum_dir(tmp_path)

        assert before == after, (
            "GET /verdicts mutated the sandbox (verb-discipline violation).\n"
            f"Files changed: {set(after) ^ set(before)}"
        )


# ---------------------------------------------------------------------------
# 9. details=true release_locks: held_seconds from acquired_at
# ---------------------------------------------------------------------------

_HELD_THREE_HOURS = 3 * 3600
# Seconds of slack for a slow test run between seeding the lock and the
# script reading the clock.
_CLOCK_SLACK_SECONDS = 30


def _seed_fresh_lock(kanban_root: pathlib.Path, name: str, record: str) -> None:
    """Write a lock record under locks/ and touch it so its mtime is now --
    what the held-lock refresh leaves behind on a lock held for hours."""
    locks_dir = kanban_root / "locks"
    locks_dir.mkdir(parents=True, exist_ok=True)
    lock_file = locks_dir / name
    lock_file.write_text(record, encoding="utf-8")
    os.utime(lock_file, None)


class TestVerdictsDetailsReleaseLockHeldTime:
    def test_release_locks_carry_held_seconds_from_acquired_at_and_null_when_not_recorded(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_projects_cfg(tmp_path, [("demo", 1, True)])
        _make_project_dir(tmp_path, "demo")
        acquired = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            seconds=_HELD_THREE_HOURS
        )
        _seed_fresh_lock(
            tmp_path, "repo-wake-demo.lock",
            f"holder=coder\npid={os.getpid()}\nop=wake-chain:demo\ntask=none\n"
            f"acquired_at={acquired.strftime('%Y-%m-%dT%H:%M:%SZ')}\n",
        )
        _seed_fresh_lock(
            tmp_path, "repo-legacy-demo.lock",
            f"holder=coder\npid={os.getpid()}\nop=wake-chain:demo\ntask=T-1\n",
        )

        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/verdicts", params={"project": "demo", "details": "true"})

        assert resp.status_code == 200, resp.text
        locks = {pathlib.Path(e["path"]).name: e for e in resp.json()["release_locks"]}
        assert set(locks) == {"repo-wake-demo.lock", "repo-legacy-demo.lock"}, locks

        held_lock = locks["repo-wake-demo.lock"]
        assert isinstance(held_lock["held_seconds"], int), held_lock
        assert _HELD_THREE_HOURS <= held_lock["held_seconds"] <= _HELD_THREE_HOURS + _CLOCK_SLACK_SECONDS
        assert isinstance(held_lock["age_seconds"], int), held_lock
        assert 0 <= held_lock["age_seconds"] <= _CLOCK_SLACK_SECONDS, (
            f"age_seconds is the refresh age (mtime), which was just touched: {held_lock}"
        )

        legacy_lock = locks["repo-legacy-demo.lock"]
        assert "held_seconds" in legacy_lock, legacy_lock
        assert legacy_lock["held_seconds"] is None, (
            f"a record without acquired_at must never publish an mtime number as held time: {legacy_lock}"
        )
