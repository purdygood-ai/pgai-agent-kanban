"""
test_bug0041_acceptance.py — Acceptance tests proving BUG-0041 is fixed.

BUG-0041 established two universal API contracts:
  1. dry_run on every operation (all 17), uniform semantics — dispatch-layer
     short-circuit, no subprocess spawned, zero filesystem/git side effects.
  2. warnings: list[str] on every response, always present — unknown query
     parameters on reads and unknown body fields on operations are warned,
     not rejected.

This module covers four acceptance requirements:

  (a) Four literal reference-shape tests:
      - GET /health                              → warnings: []
      - GET /health?bob=unknown_var              → warnings: ["unknown parameter: bob"]
      - POST /operations/halt {"dry_run":true}   → 200, no HALT file, stdout describes
                                                   the planned action, warnings: []
      - POST /operations/halt {"forse":true}     → executes halt, warnings naming "forse"

  (b) Single parametrized 17-operation probe:
      - Unknown body field → response includes a warning naming the field
      - dry_run=true → HTTP 200, exit_code=0, stdout describes planned action, sandbox
        unchanged (primary artifact absent after call)

  (c) Single parametrized read-route probe:
      - Unknown query param → HTTP 200, warnings includes "unknown parameter: <name>"
      - Clean call → warnings == []

  (d) Native-dry-run regression over the 8 verbs with native script dry-run:
      - dry_run=true via the API short-circuit → HTTP 200, exit_code=0, stdout
        describes the planned action, warnings==[]

Design notes
------------
- API exercised via FastAPI TestClient (ASGI transport; no real network socket).
- Tests that exercise real scripts use isolated sandboxes under tmp_path;
  sandboxes are populated with enough layout for the scripts to resolve the
  project root.
- Tests that only test the API routing layer use mocked subprocess.run so they
  remain hermetic and do not depend on script availability.
- Cleanup: non-dry-run probes that mutate state (the 'forse' test creates a HALT
  file) clean up their side effects so the suite is idempotent.
- Test names describe observable behavior per SOP.md naming convention.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
from typing import NamedTuple
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Resolve the dev tree from this file's location.
# This file lives at team/pgai_agent_kanban/api/tests/test_bug0041_acceptance.py.
# Going up four levels: tests/ → api/ → pgai_agent_kanban/ → team/
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"

# Project name used in sandbox fixtures
_SANDBOX_PROJECT = "acceptance-test-proj"


# ---------------------------------------------------------------------------
# Sandbox helpers (shared across tests that need real script invocation)
# ---------------------------------------------------------------------------


def _make_minimal_sandbox(root: pathlib.Path, project: str = _SANDBOX_PROJECT) -> pathlib.Path:
    """Create a minimal kanban sandbox suitable for halt/unhalt script invocation.

    Layout:
        <root>/
            projects.cfg
            projects/<project>/
                project.cfg
                release-state.md
                tasks/queues/

    Args:
        root:    Directory to create (will be created if absent).
        project: Project name to register.

    Returns:
        Path to the project root directory under the sandbox.
    """
    root.mkdir(parents=True, exist_ok=True)

    (root / "projects.cfg").write_text(
        f"[project:{project}]\npriority = 1\n",
        encoding="utf-8",
    )

    project_root = root / "projects" / project
    (project_root / "tasks" / "queues").mkdir(parents=True, exist_ok=True)

    (project_root / "project.cfg").write_text(
        "[project]\n"
        f"project_name = {project}\n"
        f"dev_tree_path = {root}\n"
        "git_repo_url = none\n",
        encoding="utf-8",
    )
    (project_root / "release-state.md").write_text(
        "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n",
        encoding="utf-8",
    )

    return project_root


def _dir_checksum(root: pathlib.Path) -> dict[str, str]:
    """Return {relative-path: sha256-hex} for every file under root.

    Used to assert sandbox is unchanged after a dry-run call.

    Args:
        root: Directory to walk.

    Returns:
        Dict mapping relative POSIX path to SHA-256 hex digest.
    """
    result: dict[str, str] = {}
    if not root.is_dir():
        return result
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            result[rel] = digest
    return result


# ---------------------------------------------------------------------------
# TestClient factory
# ---------------------------------------------------------------------------


def _make_client(
    kanban_root: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    dev_tree: pathlib.Path | None = None,
) -> TestClient:
    """Return a TestClient with the API's kanban_root configured.

    The API config's kanban_root is set to the dev tree's team/ directory so
    that ``kanban_root / "scripts"`` resolves to real operator scripts.
    PGAI_AGENT_KANBAN_ROOT_PATH is set to *kanban_root* so scripts operate
    on the sandbox.

    Args:
        kanban_root:  Sandbox root for script invocation (e.g. tmp_path/"sb").
        monkeypatch:  pytest monkeypatch fixture for env var isolation.
        dev_tree:     team/ directory containing scripts/; defaults to _TEAM_DIR.

    Returns:
        TestClient instance (use as a context manager).
    """
    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(kanban_root))
    monkeypatch.setenv("KANBAN_ROOT", str(kanban_root))

    team_dir = dev_tree if dev_tree is not None else _TEAM_DIR
    cfg = ApiConfig(host="127.0.0.1", port=0, kanban_root=team_dir)
    return TestClient(create_app(cfg=cfg), raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# (a) Four literal reference-shape tests
# ---------------------------------------------------------------------------


class TestReferenceShapes:
    """Four literal reference shapes from BUG-0041 — exact target behavior."""

    def test_health_baseline_warnings_empty(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """GET /health returns warnings: [] on a clean call (no unknown query params).

        Reference shape: GET /health → {…, "warnings": []}
        """
        kanban_root = tmp_path / "kanban"
        kanban_root.mkdir()
        (kanban_root / "VERSION").write_text("v1.20.7\n", encoding="utf-8")

        with _make_client(kanban_root, monkeypatch) as client:
            resp = client.get("/health")

        assert resp.status_code == 200, (
            f"GET /health must return 200; got {resp.status_code}."
        )
        body = resp.json()
        assert "warnings" in body, (
            f"'warnings' key must always be present in /health response.\n"
            f"Got keys: {sorted(body.keys())}"
        )
        assert body["warnings"] == [], (
            f"GET /health with no unknown params must return warnings=[]; "
            f"got {body['warnings']!r}"
        )

    def test_health_unknown_query_param_produces_named_warning(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """GET /health?bob=unknown_var returns warnings: ["unknown parameter: bob"].

        Reference shape: GET /health?bob=unknown_var → {…, "warnings": ["unknown parameter: bob"]}
        """
        kanban_root = tmp_path / "kanban"
        kanban_root.mkdir()
        (kanban_root / "VERSION").write_text("v1.20.7\n", encoding="utf-8")

        with _make_client(kanban_root, monkeypatch) as client:
            resp = client.get("/health?bob=unknown_var")

        assert resp.status_code == 200, (
            f"GET /health?bob=unknown_var must return 200; got {resp.status_code}."
        )
        body = resp.json()
        assert "warnings" in body, (
            f"'warnings' key must be present in /health response.\n"
            f"Got keys: {sorted(body.keys())}"
        )
        assert body["warnings"] == ["unknown parameter: bob"], (
            f"Unknown param 'bob' must produce exactly ['unknown parameter: bob']; "
            f"got {body['warnings']!r}"
        )

    def test_halt_dry_run_returns_200_no_halt_file_created(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """POST /operations/halt with dry_run:true returns 200, stdout describes plan, no HALT file.

        Reference shape:
          POST /operations/halt {"project":"p","dry_run":true}
                                → 200, no HALT file created,
                                  stdout describes the would-be action,
                                  "warnings": []

        This is the load-bearing assertion: the exact live failure was that dry_run sent
        to halt executed the REAL operation (HALT file was created). Assert the HALT file
        does NOT exist after the call.
        """
        kanban_root = tmp_path / "kanban"
        project_root = _make_minimal_sandbox(kanban_root)
        halt_file = project_root / "HALT"

        # Pre-condition: no HALT file before the call
        assert not halt_file.exists(), (
            "Pre-condition violated: HALT file must not exist before dry-run call."
        )

        with _make_client(kanban_root, monkeypatch) as client:
            resp = client.post(
                "/operations/halt",
                json={"project": _SANDBOX_PROJECT, "dry_run": True},
            )

        assert resp.status_code == 200, (
            f"POST /operations/halt with dry_run:true must return HTTP 200; "
            f"got {resp.status_code}.\nbody: {resp.text}"
        )
        body = resp.json()

        assert body["exit_code"] == 0, (
            f"dry_run halt must return exit_code=0; got {body['exit_code']}.\n"
            f"stdout: {body['stdout']}\nstderr: {body['stderr']}"
        )

        assert "dry-run" in body["stdout"].lower(), (
            f"dry_run halt stdout must describe the planned action (contain 'dry-run'); "
            f"got: {body['stdout']!r}"
        )

        assert body["warnings"] == [], (
            f"dry_run halt with no unknown fields must return warnings=[]; "
            f"got {body['warnings']!r}"
        )

        # ON-DISK ASSERTION: the HALT file must NOT exist after the dry-run call.
        # This is the load-bearing check — the exact failure mode observed in the wild.
        assert not halt_file.exists(), (
            f"HALT file must NOT exist after dry_run=true call to /operations/halt.\n"
            f"File found at: {halt_file}\n"
            f"The dry_run flag was not honoured; the real operation was executed."
        )

    def test_halt_near_miss_field_forse_produces_warning_and_executes(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """POST /operations/halt with 'forse':true executes halt and warns about 'forse'.

        Reference shape:
          POST /operations/halt {"project":"p","forse":true}
                                → executes halt,
                                  "warnings": ["unknown field: forse"]

        'forse' is an edit-distance-1 typo of 'force', which is itself a near-miss
        alias for 'dry_run'.  The unknown-field capture must produce a warning naming
        'forse'.  The operation still executes (halt IS created).

        Cleanup: removes the HALT file after assertion so the suite stays idempotent.
        """
        kanban_root = tmp_path / "kanban"
        project_root = _make_minimal_sandbox(kanban_root)
        halt_file = project_root / "HALT"

        with _make_client(kanban_root, monkeypatch) as client:
            resp = client.post(
                "/operations/halt",
                json={"project": _SANDBOX_PROJECT, "forse": True},
            )

        try:
            assert resp.status_code == 200, (
                f"POST /operations/halt with unknown field 'forse' must return 200 "
                f"(unknown fields warn and execute); got {resp.status_code}.\n"
                f"body: {resp.text}"
            )
            body = resp.json()

            assert body["exit_code"] == 0, (
                f"halt with unknown 'forse' field must exit 0 (operation executes); "
                f"got exit_code={body['exit_code']}.\n"
                f"stdout: {body['stdout']}\nstderr: {body['stderr']}"
            )

            assert len(body["warnings"]) >= 1, (
                f"halt with unknown 'forse' field must produce at least one warning; "
                f"got warnings={body['warnings']!r}"
            )

            warning_text = " ".join(body["warnings"])
            assert "forse" in warning_text, (
                f"Warning must name the unknown field 'forse'; "
                f"got warnings={body['warnings']!r}"
            )
        finally:
            # Cleanup: remove HALT file so subsequent tests see a clean state
            if halt_file.exists():
                halt_file.unlink()


# ---------------------------------------------------------------------------
# (b) Single parametrized 17-operation probe
# ---------------------------------------------------------------------------


class _OpSpec(NamedTuple):
    """Specification for a single operation endpoint probe.

    Attributes:
        route:          URL path relative to the API root (e.g. "/operations/halt").
        minimal_body:   Minimal valid body dict (project, key, etc.) without
                        dry_run or unknown fields.
        primary_artifact: Relative path (from project_root) of the primary artifact
                          the operation would create.  Used for side-effect-absence
                          assertion on dry_run=true calls.  None when the operation
                          does not create a named artifact (e.g. switch-provider).
        special_kwargs: Extra body fields needed to make the request valid (e.g.
                        confirm for ship-rc, force for remove-project).
    """

    route: str
    minimal_body: dict
    primary_artifact: str | None
    special_kwargs: dict


# All 17 POST /operations/* routes with their minimal valid bodies.
# For dry_run side-effect tests: the dispatch-layer short-circuit fires before
# any subprocess, so no artifact is created.  The assertion is that
# primary_artifact does NOT exist after the dry_run=true call.
#
# For switch-provider and create-project, the "primary artifact" is the config
# file that would change; for operations with no named per-project artifact,
# primary_artifact is None and we assert the sandbox checksum is unchanged.
_ALL_17_OPERATIONS: list[_OpSpec] = [
    _OpSpec(
        route="/operations/halt",
        minimal_body={"project": _SANDBOX_PROJECT},
        primary_artifact="HALT",
        special_kwargs={},
    ),
    _OpSpec(
        route="/operations/unhalt",
        minimal_body={"project": _SANDBOX_PROJECT},
        primary_artifact=None,  # unhalt removes HALT; absent by default = correct
        special_kwargs={},
    ),
    _OpSpec(
        route="/operations/halt-after",
        minimal_body={"project": _SANDBOX_PROJECT, "key": "rc"},
        primary_artifact="HALT-AFTER",
        special_kwargs={},
    ),
    _OpSpec(
        route="/operations/halt-global",
        minimal_body={},
        primary_artifact=None,  # global HALT lives at kanban_root/HALT, checked separately
        special_kwargs={},
    ),
    _OpSpec(
        route="/operations/unhalt-global",
        minimal_body={},
        primary_artifact=None,
        special_kwargs={},
    ),
    _OpSpec(
        route="/operations/reset",
        minimal_body={"project": _SANDBOX_PROJECT, "key": "CODER-20260101-001-alpha"},
        primary_artifact=None,
        special_kwargs={},
    ),
    _OpSpec(
        route="/operations/close",
        minimal_body={"project": _SANDBOX_PROJECT, "key": "CODER-20260101-001-alpha"},
        primary_artifact=None,
        special_kwargs={},
    ),
    _OpSpec(
        route="/operations/wontdo",
        minimal_body={"project": _SANDBOX_PROJECT, "key": "CODER-20260101-001-alpha"},
        primary_artifact=None,
        special_kwargs={},
    ),
    _OpSpec(
        route="/operations/delete",
        minimal_body={"project": _SANDBOX_PROJECT, "key": "CODER-20260101-001-alpha"},
        primary_artifact=None,
        special_kwargs={},
    ),
    _OpSpec(
        route="/operations/intake",
        minimal_body={
            "project": _SANDBOX_PROJECT,
            "filename": "BUG-9999-test.md",
            "content": "# Bug test\n## Status\nopen\n",
        },
        primary_artifact=None,
        special_kwargs={},
    ),
    _OpSpec(
        route="/operations/unwind-rc",
        minimal_body={"project": _SANDBOX_PROJECT, "key": "v1.0.0"},
        primary_artifact=None,
        special_kwargs={},
    ),
    _OpSpec(
        route="/operations/set-version-ceiling",
        minimal_body={"project": _SANDBOX_PROJECT},
        primary_artifact=None,
        special_kwargs={},
    ),
    _OpSpec(
        route="/operations/switch-provider",
        minimal_body={"provider": "claude"},
        primary_artifact=None,
        special_kwargs={},
    ),
    _OpSpec(
        route="/operations/create-project",
        minimal_body={"project": "dry-run-new-proj"},
        primary_artifact=None,
        special_kwargs={},
    ),
    _OpSpec(
        route="/operations/add-project",
        minimal_body={"project": _SANDBOX_PROJECT},
        primary_artifact=None,
        special_kwargs={},
    ),
    _OpSpec(
        route="/operations/remove-project",
        minimal_body={"project": _SANDBOX_PROJECT},
        primary_artifact=None,
        special_kwargs={"force": True},  # force required when not dry_run, included here for clarity
    ),
    _OpSpec(
        route="/operations/ship-rc",
        minimal_body={"project": _SANDBOX_PROJECT, "key": "v1.20.7"},
        primary_artifact=None,
        special_kwargs={"confirm": f"ship-rc {_SANDBOX_PROJECT} v1.20.7"},
    ),
]

# Parameter IDs for the 17-operation parametrize — uses route slug for readability.
_OP_IDS = [spec.route.split("/")[-1] for spec in _ALL_17_OPERATIONS]


class TestSeventeenOperationProbe:
    """Single parametrized probe over all 17 operation endpoints.

    Two assertions per verb:
      1. Unknown body field → response includes a warning naming the field.
      2. dry_run=true → HTTP 200, exit_code=0, stdout describes plan, no filesystem mutation.
    """

    @pytest.mark.parametrize("spec", _ALL_17_OPERATIONS, ids=_OP_IDS)
    def test_unknown_body_field_produces_named_warning_and_executes(
        self,
        spec: _OpSpec,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Unknown body field on any operation produces a warning naming the field.

        The operation still executes-or-dry-runs (is not rejected).  The warning
        must contain the name of the unknown field.

        Uses a dry_run=true call so the real operation does not execute (keeping
        the test hermetic and side-effect-free), while still exercising the
        unknown-body-field capture path.
        """
        kanban_root = tmp_path / "kanban"
        _make_minimal_sandbox(kanban_root)

        body = {
            **spec.minimal_body,
            **spec.special_kwargs,
            "unknown_test_field": "unexpected_value",
            "dry_run": True,  # short-circuit so no real mutation occurs
        }

        with _make_client(kanban_root, monkeypatch) as client:
            resp = client.post(spec.route, json=body)

        assert resp.status_code == 200, (
            f"{spec.route}: expected HTTP 200 (unknown field warns and executes); "
            f"got {resp.status_code}.\nbody: {resp.text}"
        )
        response_body = resp.json()

        assert "warnings" in response_body, (
            f"{spec.route}: 'warnings' key must be present in all operation responses.\n"
            f"Got keys: {sorted(response_body.keys())}"
        )
        warning_text = " ".join(response_body["warnings"])
        assert "unknown_test_field" in warning_text, (
            f"{spec.route}: warning must name the unknown field 'unknown_test_field';\n"
            f"got warnings={response_body['warnings']!r}"
        )

    @pytest.mark.parametrize("spec", _ALL_17_OPERATIONS, ids=_OP_IDS)
    def test_dry_run_produces_no_filesystem_or_git_side_effects(
        self,
        spec: _OpSpec,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """dry_run=true on any operation: HTTP 200, exit_code=0, sandbox unchanged.

        The dispatch-layer short-circuit fires before any subprocess is spawned,
        so by construction: no script runs, no files are written, no git ops occur.
        We verify this by checksumming the sandbox before and after the call.

        Additionally:
          - HTTP status must be 200
          - exit_code must be 0
          - stdout must describe the planned action (contain "dry-run")
          - warnings must be [] (no unknown fields in the body)
        """
        kanban_root = tmp_path / "kanban"
        project_root = _make_minimal_sandbox(kanban_root)

        # Create the primary artifact BEFORE the call if it would normally need
        # to be absent; we snapshot the full sandbox instead.
        checksum_before = _dir_checksum(kanban_root)

        # Check primary artifact specifically when defined
        halt_artifact: pathlib.Path | None = None
        if spec.primary_artifact is not None:
            halt_artifact = project_root / spec.primary_artifact

        body = {
            **spec.minimal_body,
            **spec.special_kwargs,
            "dry_run": True,
        }

        with _make_client(kanban_root, monkeypatch) as client:
            resp = client.post(spec.route, json=body)

        assert resp.status_code == 200, (
            f"{spec.route}: dry_run=true must return HTTP 200; "
            f"got {resp.status_code}.\nbody: {resp.text}"
        )
        response_body = resp.json()

        assert response_body["exit_code"] == 0, (
            f"{spec.route}: dry_run=true must return exit_code=0; "
            f"got {response_body['exit_code']}.\n"
            f"stdout: {response_body['stdout']}\nstderr: {response_body['stderr']}"
        )

        assert "dry-run" in response_body["stdout"].lower(), (
            f"{spec.route}: dry_run=true stdout must describe planned action (contain 'dry-run'); "
            f"got stdout={response_body['stdout']!r}"
        )

        assert response_body["warnings"] == [], (
            f"{spec.route}: dry_run=true with no unknown fields must return warnings=[]; "
            f"got {response_body['warnings']!r}"
        )

        # Primary artifact absence: for verbs that create a named artifact, verify it
        # was NOT created.
        if halt_artifact is not None:
            assert not halt_artifact.exists(), (
                f"{spec.route}: primary artifact '{spec.primary_artifact}' must NOT exist "
                f"after dry_run=true call.\n"
                f"Found at: {halt_artifact}\n"
                f"The dry_run short-circuit was not honoured; the real operation executed."
            )

        # Sandbox checksum invariant: no file in the kanban root changed.
        checksum_after = _dir_checksum(kanban_root)
        added = set(checksum_after) - set(checksum_before)
        removed = set(checksum_before) - set(checksum_after)
        changed = {
            p for p in set(checksum_before) & set(checksum_after)
            if checksum_before[p] != checksum_after[p]
        }
        if added or removed or changed:
            lines = []
            for p in sorted(added):
                lines.append(f"  + {p}  [file created]")
            for p in sorted(removed):
                lines.append(f"  - {p}  [file deleted]")
            for p in sorted(changed):
                lines.append(f"  ~ {p}  [file modified]")
            pytest.fail(
                f"{spec.route}: dry_run=true must produce zero filesystem side effects.\n"
                f"Sandbox mutations detected:\n" + "\n".join(lines)
            )


# ---------------------------------------------------------------------------
# (c) Single parametrized read-route probe
# ---------------------------------------------------------------------------


class _ReadRouteSpec(NamedTuple):
    """Specification for a single read (GET) route probe.

    Attributes:
        path:           Full URL path to GET (with any required path params substituted).
        required_params: Query parameters required to avoid 422 on a clean call.
        uses_subprocess: Whether the route shells out (if True, subprocess.run is mocked
                         to return exit_code=0 so the test is hermetic).
        response_has_warnings: Whether the response body carries a top-level 'warnings'
                               key.  Routes that return array bodies (e.g. /projects, /approvals
                               as array) or non-envelope shapes set this False.
    """

    path: str
    required_params: dict
    uses_subprocess: bool
    response_has_warnings: bool


# All read (GET) routes that inject warn_unknown_query_params.
# /board, /logs/{kind}, /traces, /traces/{trace_id}, /projects/{name},
# /dashboard/{pane} all shell out; we mock subprocess.run for those.
_ALL_READ_ROUTES: list[_ReadRouteSpec] = [
    _ReadRouteSpec(
        path="/health",
        required_params={},
        uses_subprocess=False,
        response_has_warnings=True,
    ),
    _ReadRouteSpec(
        path="/status",
        required_params={"project": _SANDBOX_PROJECT},
        uses_subprocess=True,
        response_has_warnings=True,
    ),
    _ReadRouteSpec(
        path="/show",
        required_params={"project": _SANDBOX_PROJECT, "key": "CODER-20260101-001-alpha"},
        uses_subprocess=True,
        response_has_warnings=True,
    ),
    _ReadRouteSpec(
        path="/test-report",
        required_params={"project": _SANDBOX_PROJECT, "key": "v1.0.0"},
        uses_subprocess=True,
        response_has_warnings=True,
    ),
    _ReadRouteSpec(
        path="/metrics",
        required_params={"project": _SANDBOX_PROJECT},
        uses_subprocess=True,
        response_has_warnings=True,
    ),
    _ReadRouteSpec(
        path="/costs",
        required_params={"project": _SANDBOX_PROJECT},
        uses_subprocess=True,
        response_has_warnings=True,
    ),
    _ReadRouteSpec(
        path="/rejected",
        required_params={},
        uses_subprocess=True,
        response_has_warnings=True,
    ),
    _ReadRouteSpec(
        path="/projects",
        required_params={},
        uses_subprocess=False,
        response_has_warnings=True,
    ),
    _ReadRouteSpec(
        path="/dashboard/metrics",
        required_params={},
        uses_subprocess=True,
        response_has_warnings=True,
    ),
    _ReadRouteSpec(
        path="/approvals",
        required_params={},
        uses_subprocess=False,
        response_has_warnings=False,  # returns JSON array, no top-level warnings key
    ),
    _ReadRouteSpec(
        path="/board",
        required_params={},
        uses_subprocess=True,
        response_has_warnings=True,
    ),
    _ReadRouteSpec(
        path=f"/projects/{_SANDBOX_PROJECT}",
        required_params={},
        uses_subprocess=False,  # pure Python metadata read; uses sandbox kanban_root for registry
        response_has_warnings=True,
    ),
    _ReadRouteSpec(
        path="/logs/api-server",
        required_params={},
        uses_subprocess=False,  # reads log file directly
        response_has_warnings=True,
    ),
    _ReadRouteSpec(
        path="/traces",
        required_params={},
        uses_subprocess=False,  # reads trace files directly
        response_has_warnings=False,  # returns JSON array; warnings not surfaced in array response
    ),
]

_READ_ROUTE_IDS = [spec.path.replace("/", "_").lstrip("_") for spec in _ALL_READ_ROUTES]


def _make_client_for_route(
    spec: "_ReadRouteSpec",
    kanban_root: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> "TestClient":
    """Return a TestClient appropriate for the given route spec.

    Routes that perform pure Python registry/filesystem reads (like /projects/{name})
    need the API config's kanban_root to point at the sandbox (so the registry lookup
    finds the test project).  Script-based routes need kanban_root to point at team/
    so scripts resolve correctly.

    For /projects/{name}: use the sandbox root as kanban_root (no script invocation).
    For all other routes: use _TEAM_DIR as kanban_root (scripts are resolved there).

    Args:
        spec:        The read-route spec being tested.
        kanban_root: Sandbox kanban root (used directly for /projects/{name}).
        monkeypatch: pytest monkeypatch fixture.

    Returns:
        TestClient configured appropriately for the route.
    """
    # /projects/<name> (concrete project metadata) reads the registry from
    # kanban_root — point the API at the sandbox so the test project is found.
    # This route does not shell out, so scripts are not needed.
    if spec.path.startswith("/projects/") and spec.path != "/projects":
        return _make_client(kanban_root, monkeypatch, dev_tree=kanban_root)
    return _make_client(kanban_root, monkeypatch)


def _mock_subprocess_ok() -> MagicMock:
    """Return a mock subprocess.CompletedProcess with exit_code 0."""
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = "mocked ok output\n"
    mock.stderr = ""
    return mock


class TestReadRouteProbe:
    """Single parametrized probe over all read (GET) routes.

    Two assertions per route:
      1. Unknown query param → HTTP 200, warnings contains "unknown parameter: <name>".
      2. Clean call → warnings == [] (when route carries warnings in its response body).
    """

    @pytest.mark.parametrize("spec", _ALL_READ_ROUTES, ids=_READ_ROUTE_IDS)
    def test_unknown_query_param_produces_named_warning(
        self,
        spec: _ReadRouteSpec,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Unknown query param on any read route returns 200 with a named warning.

        Appends '?xyzzy_unknown=1' to the declared query params.  The response
        must be HTTP 200 and the 'warnings' field (when present) must contain
        'unknown parameter: xyzzy_unknown'.
        """
        kanban_root = tmp_path / "kanban"
        _make_minimal_sandbox(kanban_root)
        (kanban_root / "VERSION").write_text("v1.20.7\n", encoding="utf-8")

        # Build query string with declared params + unknown param
        params = {**spec.required_params, "xyzzy_unknown": "1"}

        with _make_client_for_route(spec, kanban_root, monkeypatch) as client:
            if spec.uses_subprocess:
                with patch("subprocess.run", return_value=_mock_subprocess_ok()):
                    resp = client.get(spec.path, params=params)
            else:
                resp = client.get(spec.path, params=params)

        # Routes should return 200 (warnings do not cause failures)
        assert resp.status_code == 200, (
            f"GET {spec.path}?xyzzy_unknown=1 must return 200 "
            f"(unknown params warn and execute); "
            f"got {resp.status_code}.\nbody: {resp.text[:400]}"
        )

        # Check warnings when the route carries them in the response body
        if spec.response_has_warnings:
            body = resp.json()
            assert "warnings" in body, (
                f"GET {spec.path}: 'warnings' key must be present in response.\n"
                f"Got keys: {sorted(body.keys()) if isinstance(body, dict) else type(body)}"
            )
            warning_text = " ".join(body["warnings"])
            assert "xyzzy_unknown" in warning_text, (
                f"GET {spec.path}: warning must name the unknown param 'xyzzy_unknown';\n"
                f"got warnings={body['warnings']!r}"
            )

    @pytest.mark.parametrize("spec", _ALL_READ_ROUTES, ids=_READ_ROUTE_IDS)
    def test_clean_call_returns_empty_warnings(
        self,
        spec: _ReadRouteSpec,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Clean call to any read route returns warnings == [] (when route carries warnings).

        No unknown params supplied; the warnings list must be present and empty.
        Routes that return array-shaped bodies are excluded from this assertion
        (their spec.response_has_warnings is False).
        """
        if not spec.response_has_warnings:
            pytest.skip(
                f"Route {spec.path} returns an array body without top-level 'warnings'; "
                "clean-call baseline not applicable."
            )

        kanban_root = tmp_path / "kanban"
        _make_minimal_sandbox(kanban_root)
        (kanban_root / "VERSION").write_text("v1.20.7\n", encoding="utf-8")

        with _make_client_for_route(spec, kanban_root, monkeypatch) as client:
            if spec.uses_subprocess:
                with patch("subprocess.run", return_value=_mock_subprocess_ok()):
                    resp = client.get(spec.path, params=spec.required_params)
            else:
                resp = client.get(spec.path, params=spec.required_params)

        assert resp.status_code == 200, (
            f"GET {spec.path} (clean call) must return 200; "
            f"got {resp.status_code}.\nbody: {resp.text[:400]}"
        )
        body = resp.json()
        assert "warnings" in body, (
            f"GET {spec.path}: 'warnings' key must be present in response.\n"
            f"Got keys: {sorted(body.keys()) if isinstance(body, dict) else type(body)}"
        )
        assert body["warnings"] == [], (
            f"GET {spec.path}: clean call must return warnings=[]; "
            f"got {body['warnings']!r}"
        )


# ---------------------------------------------------------------------------
# (d) Native-dry-run regression — the 8 verbs with native script dry-run
# ---------------------------------------------------------------------------


# The 8 verbs whose underlying scripts accept --dry-run natively.
# The API's dispatch-layer short-circuit fires BEFORE the native script dry-run,
# so the observable contract through the API is always:
#   - HTTP 200
#   - exit_code 0
#   - stdout describes the planned action (contains "dry-run")
#   - warnings == []
# Both the API short-circuit and the native script dry-run satisfy the same
# observable contract: zero mutation, action described.
_NATIVE_DRY_RUN_VERBS: list[tuple[str, dict, dict]] = [
    # (route, minimal_body, special_kwargs)
    (
        "/operations/close",
        {"project": _SANDBOX_PROJECT, "key": "CODER-20260101-001-alpha"},
        {},
    ),
    (
        "/operations/delete",
        {"project": _SANDBOX_PROJECT, "key": "CODER-20260101-001-alpha"},
        {},
    ),
    (
        "/operations/unwind-rc",
        {"project": _SANDBOX_PROJECT, "key": "v1.0.0"},
        {},
    ),
    (
        "/operations/set-version-ceiling",
        {"project": _SANDBOX_PROJECT},
        {},
    ),
    (
        "/operations/create-project",
        {"project": "new-dry-run-project"},
        {},
    ),
    (
        "/operations/add-project",
        {"project": _SANDBOX_PROJECT},
        {},
    ),
    (
        "/operations/remove-project",
        {"project": _SANDBOX_PROJECT},
        {"force": True},
    ),
    (
        "/operations/ship-rc",
        {"project": _SANDBOX_PROJECT, "key": "v1.20.7"},
        {"confirm": f"ship-rc {_SANDBOX_PROJECT} v1.20.7"},
    ),
]

_NATIVE_DRY_RUN_IDS = [route.split("/")[-1] for route, _, _ in _NATIVE_DRY_RUN_VERBS]


class TestNativeDryRunRegression:
    """Regression: native-dry-run verbs produce identical observable envelopes through the API.

    The 8 verbs with native script dry-run must produce the same API contract as
    non-native verbs when dry_run=true: HTTP 200, exit_code=0, stdout describes
    the plan, warnings==[].  The dispatch-layer short-circuit ensures this by
    firing before any subprocess invocation.
    """

    @pytest.mark.parametrize(
        "route,minimal_body,special_kwargs",
        _NATIVE_DRY_RUN_VERBS,
        ids=_NATIVE_DRY_RUN_IDS,
    )
    def test_native_dry_run_verb_produces_identical_observable_envelope(
        self,
        route: str,
        minimal_body: dict,
        special_kwargs: dict,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """dry_run=true via the API produces: HTTP 200, exit_code=0, stdout with 'dry-run', warnings=[].

        Verifies that the API dispatch-layer short-circuit fires for native-dry-run
        verbs and produces the expected envelope.  This is the 'identical observable
        contract' claim: whether or not the underlying script has native --dry-run,
        the API's dry_run=true always short-circuits at the dispatch layer, producing
        the same observable result.
        """
        kanban_root = tmp_path / "kanban"
        _make_minimal_sandbox(kanban_root)

        body = {
            **minimal_body,
            **special_kwargs,
            "dry_run": True,
        }

        with _make_client(kanban_root, monkeypatch) as client:
            resp = client.post(route, json=body)

        assert resp.status_code == 200, (
            f"{route}: native-dry-run verb with dry_run=true must return HTTP 200; "
            f"got {resp.status_code}.\nbody: {resp.text}"
        )
        response_body = resp.json()

        assert response_body["exit_code"] == 0, (
            f"{route}: dry_run=true must return exit_code=0; "
            f"got {response_body['exit_code']}.\n"
            f"stdout: {response_body['stdout']}\nstderr: {response_body['stderr']}"
        )

        assert "dry-run" in response_body["stdout"].lower(), (
            f"{route}: dry_run=true stdout must describe the planned action (contain 'dry-run'); "
            f"got stdout={response_body['stdout']!r}"
        )

        assert response_body["warnings"] == [], (
            f"{route}: dry_run=true with no unknown fields must return warnings=[]; "
            f"got {response_body['warnings']!r}"
        )

        # The 'stderr' field must be empty (no errors from the short-circuit path)
        assert response_body["stderr"] == "", (
            f"{route}: dry_run=true must return empty stderr; "
            f"got stderr={response_body['stderr']!r}"
        )
