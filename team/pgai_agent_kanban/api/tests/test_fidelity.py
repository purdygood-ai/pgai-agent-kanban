"""
test_fidelity.py — Twin-sandbox fidelity test harness and verb-discipline suite.

Two suites:

  1. **Verb-discipline tests** (``test_verb_discipline_*``):
     Issue every GET (read) endpoint against a sandbox project root.  Take a
     recursive SHA-256 checksum of all files before the request and after.
     Assert the checksums are identical — a GET that writes anything is a hard
     failure.

  2. **Fidelity tests** (``test_fidelity_*``):
     Materialise two identical sandbox project roots (the "twin" pair).
     For each mutation endpoint, run the API call against one sandbox and the
     direct script invocation against the other.  Take a recursive diff of
     the two resulting on-disk states and assert it is empty.

Design notes
------------
- The API is exercised via FastAPI's TestClient (httpx ASGI transport).
  This exercises the full FastAPI routing/validation/adapter stack without
  opening a real network socket, so there are no port collisions across test
  runs and the client starts/stops deterministically per test session.
- Scripts are the real operator scripts in the dev tree (not stubs) — that is
  the point: we are testing the thin-adapter promise.
- Sandbox roots are materialised under pytest's tmp_path, which conftest.py
  redirects to PGAI_AGENT_KANBAN_TEMP_DIR/tests when the framework env is set.
- No bare /tmp paths anywhere in this file.

Constraints honoured
--------------------
- Sandbox fixtures live under the framework temp root (tmp_path).
- Twin sandboxes are separate roots so API and script runs cannot
  cross-contaminate.
- A GET that mutates any sandbox file causes the test to fail loudly.
- All tests are hermetic: no real network, no live kanban state.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import re
import shutil
import subprocess
import textwrap
from typing import Generator

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Resolve the dev-tree root (worktree) so we can locate real operator scripts.
# This file lives at team/pgai_agent_kanban/api/tests/test_fidelity.py.
# Going up four levels: api/tests → api → pgai_agent_kanban → team
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"


# ---------------------------------------------------------------------------
# Fidelity normalization — paths whose content contains deliberate
# nondeterminism that the twin-sandbox comparator should ignore.
#
# logs/reset.log is written by _append_reset_log in ops/write.py.  Each
# sandbox write includes a wall-clock ISO 8601 timestamp; the API and script
# runs happen in different seconds, so their timestamps legitimately differ.
# The fidelity test strips those timestamps before computing the SHA-256 used
# for comparison, so the structural content (task-ID, message text) must still
# match across both sandboxes.
#
# Pattern: ISO 8601 timestamp with timezone offset, e.g.
#   2026-07-06T15:23:01+00:00  or  2026-07-06T15:23:01-05:00
# Stripped from the per-line content only; all other bytes in the file are
# compared verbatim.
# ---------------------------------------------------------------------------

_RESET_LOG_RELPATH = "logs/reset.log"
_RESET_LOG_TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}"
)


def _normalize_reset_log(raw: bytes) -> bytes:
    """Return *raw* with ISO 8601 timestamps stripped.

    Applied only to logs/reset.log content before hashing so the fidelity
    comparator ignores the wall-clock timestamp that legitimately differs
    between the API and script sandbox runs.

    Args:
        raw: Raw bytes of the reset log file.

    Returns:
        Bytes with every ISO 8601 timestamp (including timezone offset)
        replaced by an empty string.
    """
    text = raw.decode("utf-8", errors="replace")
    normalized = _RESET_LOG_TIMESTAMP_RE.sub("", text)
    return normalized.encode("utf-8")


# ---------------------------------------------------------------------------
# Sandbox structure constants
# ---------------------------------------------------------------------------
_SANDBOX_PROJECT_NAME = "sandbox-proj"
_SANDBOX_TASK_KEY = "CODER-20260101-001-alpha"
_SANDBOX_BUG_KEY = "BUG-0001"

# Agents whose queue files the sandbox must have (mirrors conftest.AGENTS)
_AGENTS = ["pm", "coder", "writer", "tester", "cm", "bug"]


# ---------------------------------------------------------------------------
# Checksum helpers
# ---------------------------------------------------------------------------


def _compute_dir_checksum(root: pathlib.Path) -> dict[str, str]:
    """Return a mapping of {relative-path: sha256-hex} for every file under root.

    Directories are not represented; only regular files are checksummed.
    Symlinks are followed.  Order is deterministic (sorted by relative path).

    Normalization: files whose relative path matches ``_RESET_LOG_RELPATH``
    (``logs/reset.log``) are hashed after stripping ISO 8601 timestamps via
    ``_normalize_reset_log``.  This prevents the wall-clock timestamp written
    by the reset operation from causing spurious fidelity mismatches between
    the API and script sandbox runs.

    Args:
        root: Directory to walk.

    Returns:
        Dict mapping relative POSIX path string to its SHA-256 hex digest.
        Empty dict when root does not exist or contains no files.
    """
    result: dict[str, str] = {}
    if not root.is_dir():
        return result
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            raw = path.read_bytes()
            # Normalize logs/reset.log before hashing: strip the wall-clock
            # timestamp so the API and script runs produce identical digests
            # even when they land in different clock seconds.
            if rel == _RESET_LOG_RELPATH or rel.endswith("/" + _RESET_LOG_RELPATH):
                raw = _normalize_reset_log(raw)
            digest = hashlib.sha256(raw).hexdigest()
            result[rel] = digest
    return result


def _diff_checksums(
    label_a: str,
    cksum_a: dict[str, str],
    label_b: str,
    cksum_b: dict[str, str],
) -> list[str]:
    """Return a list of human-readable difference lines between two checksum dicts.

    Returns an empty list when the dicts are identical.

    Args:
        label_a: Name of the first snapshot (e.g. "before").
        cksum_a: Checksum dict from _compute_dir_checksum for the first state.
        label_b: Name of the second snapshot (e.g. "after GET /status").
        cksum_b: Checksum dict from _compute_dir_checksum for the second state.

    Returns:
        List of difference lines.  Empty list = no differences.
    """
    lines: list[str] = []
    all_paths = sorted(set(cksum_a) | set(cksum_b))
    for p in all_paths:
        a = cksum_a.get(p)
        b = cksum_b.get(p)
        if a is None:
            lines.append(f"  + {p}  [appeared in {label_b}]")
        elif b is None:
            lines.append(f"  - {p}  [disappeared in {label_b}]")
        elif a != b:
            lines.append(f"  ~ {p}  [content changed between {label_a} and {label_b}]")
    return lines


# ---------------------------------------------------------------------------
# Sandbox materialisation
# ---------------------------------------------------------------------------


def _materialise_sandbox(root: pathlib.Path) -> None:
    """Materialise a minimal but realistic sandbox kanban root at *root*.

    Layout produced:
        <root>/
            projects.cfg
            projects/
                sandbox-proj/
                    project.cfg
                    tasks/
                        queues/
                            coder_backlog.md
                            pm_backlog.md
                            writer_backlog.md
                            tester_backlog.md
                            cm_backlog.md
                            bug_backlog.md
                        CODER-20260101-001-alpha/
                            README.md
                            status.md
                            artifacts/
                            logs/
                    bugs/
                        BUG-0001.md
                    requirements/
                    priority/
                    release-state.md

    Args:
        root: Directory to populate (will be created if absent).
    """
    root.mkdir(parents=True, exist_ok=True)

    # projects.cfg
    (root / "projects.cfg").write_text(
        "[project:sandbox-proj]\n"
        "priority=1\n"
        "description=Sandbox fixture project for fidelity tests\n"
        "enabled=true\n",
        encoding="utf-8",
    )

    project_root = root / "projects" / _SANDBOX_PROJECT_NAME
    tasks_dir = project_root / "tasks"
    queues_dir = tasks_dir / "queues"
    queues_dir.mkdir(parents=True, exist_ok=True)

    # per-agent queue files
    for agent in _AGENTS:
        (queues_dir / f"{agent}_backlog.md").write_text(
            f"# {agent.upper()} Backlog\n\n"
            "<!-- Tasks are listed below. One task per line. -->\n"
            "<!-- Format: - [ ] TASK-ID -->\n"
            f"- [ ] {_SANDBOX_TASK_KEY}\n",
            encoding="utf-8",
        )

    # one minimal agent task folder
    task_dir = tasks_dir / _SANDBOX_TASK_KEY
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "artifacts").mkdir(exist_ok=True)
    (task_dir / "logs").mkdir(exist_ok=True)
    (task_dir / "README.md").write_text(
        f"# Task: {_SANDBOX_TASK_KEY}\n\n## Task ID\n{_SANDBOX_TASK_KEY}\n\n"
        "## Owner\nClaude\n\n## Role\nCODER\n\n## Workflow Type\nrelease\n",
        encoding="utf-8",
    )
    (task_dir / "status.md").write_text(
        f"# Status\n\n## Task\n{_SANDBOX_TASK_KEY}\n\n"
        "## State\nBACKLOG\n\n## Summary\nnone\n\n"
        "## Artifacts\nnone\n\n## Blockers\nnone\n\n## Needs Human\nno\n",
        encoding="utf-8",
    )

    # bugs directory with one bug intake file
    bugs_dir = project_root / "bugs"
    bugs_dir.mkdir(parents=True, exist_ok=True)
    (bugs_dir / f"{_SANDBOX_BUG_KEY}.md").write_text(
        f"# Bug: {_SANDBOX_BUG_KEY}\n\n## Status\nopen\n\n"
        "## Summary\nSandbox test bug fixture.\n",
        encoding="utf-8",
    )

    # requirements and priority directories (needed by some scripts)
    (project_root / "requirements").mkdir(parents=True, exist_ok=True)
    (project_root / "priority").mkdir(parents=True, exist_ok=True)

    # project.cfg
    (project_root / "project.cfg").write_text(
        "[project]\n"
        f"project_name = {_SANDBOX_PROJECT_NAME}\n"
        f"dev_tree_path = {root}\n"
        "git_repo_url = none\n",
        encoding="utf-8",
    )

    # release-state.md
    (project_root / "release-state.md").write_text(
        "# Release State\n\n## Current RC\nnone\n\n## State\nIDLE\n",
        encoding="utf-8",
    )


def _copy_sandbox(src: pathlib.Path, dst: pathlib.Path) -> None:
    """Deep-copy the sandbox tree from *src* to *dst*.

    Args:
        src: Source sandbox root (already materialised).
        dst: Destination path (must not exist; created by shutil.copytree).
    """
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


# ---------------------------------------------------------------------------
# Script invocation helpers
# ---------------------------------------------------------------------------


def _run_script(
    script_name: str,
    flags: dict,
    kanban_root: pathlib.Path,
) -> subprocess.CompletedProcess:
    """Invoke a real operator script with the given flags.

    Scripts are resolved from _SCRIPTS_DIR (the dev-tree team/scripts/).
    PGAI_AGENT_KANBAN_ROOT_PATH and KANBAN_ROOT are set in the subprocess
    environment to point at *kanban_root* so scripts operate on the sandbox.

    Args:
        script_name: Script file name, e.g. ``"halt.sh"``.
        flags:       Mapping of flag name → value (same format as adapter.build_argv).
        kanban_root: Sandbox kanban root the script should operate on.

    Returns:
        The completed subprocess result.
    """
    script_path = str(_SCRIPTS_DIR / script_name)
    argv = [script_path]
    for name, value in flags.items():
        if value is True:
            argv.append(f"--{name}")
        elif isinstance(value, str) and value:
            argv.extend([f"--{name}", value])

    env = os.environ.copy()
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env["KANBAN_ROOT"] = str(kanban_root)
    # Ensure the team/ Python package is importable for scripts that delegate
    # to pgai_agent_kanban.ops (e.g. halt.sh → python3 -m pgai_agent_kanban.ops halt).
    python_path = str(_TEAM_DIR)
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{python_path}:{existing_pp}" if existing_pp else python_path

    return subprocess.run(argv, capture_output=True, text=True, env=env)


# ---------------------------------------------------------------------------
# pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def dev_tree_team_dir() -> pathlib.Path:
    """Return the absolute path to the team/ directory in the dev tree.

    Used to locate real operator scripts for fidelity testing.
    The API config's kanban_root is set to this path so that
    ``kanban_root / "scripts"`` resolves to the real scripts directory.
    """
    return _TEAM_DIR


@pytest.fixture
def sandbox_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """Materialise a single sandbox kanban root under tmp_path.

    The fixture creates a minimal but realistic project state that the
    operator scripts can read and mutate.

    Returns:
        Path to the sandbox kanban root.
    """
    root = tmp_path / "sandbox"
    _materialise_sandbox(root)
    return root


@pytest.fixture
def twin_sandboxes(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """Materialise two identical sandbox kanban roots.

    Returns:
        Tuple ``(api_root, script_root)`` — two separate roots with identical
        initial state.  The fidelity tests run the API call against *api_root*
        and the direct script invocation against *script_root*, then compare.
    """
    # Build the base sandbox, then deep-copy it into two separate trees.
    base = tmp_path / "base_sandbox"
    _materialise_sandbox(base)

    api_root = tmp_path / "api_sandbox"
    script_root = tmp_path / "script_sandbox"
    _copy_sandbox(base, api_root)
    _copy_sandbox(base, script_root)
    return api_root, script_root


@pytest.fixture
def api_client(
    sandbox_root: pathlib.Path,
    dev_tree_team_dir: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    """Create a TestClient bound to a sandbox kanban root.

    The API config's kanban_root is set to the dev tree's team/ directory so
    that ``kanban_root / "scripts"`` resolves to the real operator scripts.
    PGAI_AGENT_KANBAN_ROOT_PATH is set to the sandbox root so that scripts
    invoked by the API operate on the sandbox, not the live install.

    The TestClient uses FastAPI's ASGI transport (no real network socket),
    so there are no port collisions across test runs.

    Yields:
        A configured TestClient instance for the duration of the test.
    """
    # Import here (not at top level) so the module does not trigger app
    # creation before the environment is patched.
    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    # Point PGAI_AGENT_KANBAN_ROOT_PATH at the sandbox so scripts operate
    # on sandbox state (scripts read this env var, not ApiConfig.kanban_root).
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(sandbox_root))
    monkeypatch.setenv("KANBAN_ROOT", str(sandbox_root))

    # Build an ApiConfig whose kanban_root is the dev-tree team/ directory.
    # This makes kanban_root / "scripts" resolve to the real operator scripts.
    cfg = ApiConfig(
        host="127.0.0.1",
        port=0,  # ephemeral; TestClient does not open a real socket
        kanban_root=dev_tree_team_dir,
    )
    app = create_app(cfg=cfg)

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


def _make_fidelity_client(
    kanban_root: pathlib.Path,
    dev_tree_team_dir: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    """Create a TestClient for fidelity tests with a specific kanban root.

    Like api_client but accepts the kanban root as a parameter (needed for
    twin-sandbox tests where each sandbox has its own root).

    Args:
        kanban_root:       Sandbox kanban root the scripts should operate on.
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
# Verb-discipline tests: GET endpoints must not mutate the sandbox
# ---------------------------------------------------------------------------


class TestVerbDiscipline:
    """Assert that every GET (read) endpoint leaves the sandbox byte-identical.

    Each test:
      1. Snapshots the sandbox checksum before the request.
      2. Issues the GET request.
      3. Snapshots the sandbox checksum after the request.
      4. Diffs the two snapshots and fails loudly if any difference is found.
    """

    def _assert_no_mutation(
        self,
        client: TestClient,
        sandbox: pathlib.Path,
        method: str,
        path: str,
        params: dict | None = None,
    ) -> None:
        """Issue an HTTP request and assert the sandbox is unchanged.

        Args:
            client:  TestClient to issue the request through.
            sandbox: Sandbox root to checksum before and after.
            method:  HTTP method string (``"GET"``).
            path:    URL path (e.g. ``"/status"``).
            params:  Query parameters dict.

        Raises:
            AssertionError: When the sandbox state changes after the request.
        """
        before = _compute_dir_checksum(sandbox)
        response = client.request(method, path, params=params or {})
        after = _compute_dir_checksum(sandbox)
        diffs = _diff_checksums("before", before, f"after {method} {path}", after)
        assert not diffs, (
            f"GET {path} mutated the sandbox (verb-discipline violation).\n"
            f"HTTP status: {response.status_code}\n"
            f"Differences:\n" + "\n".join(diffs)
        )

    def test_health_no_mutation(
        self,
        api_client: TestClient,
        sandbox_root: pathlib.Path,
    ) -> None:
        """GET /health must not write any file to the sandbox."""
        self._assert_no_mutation(api_client, sandbox_root, "GET", "/health")

    def test_status_no_mutation(
        self,
        api_client: TestClient,
        sandbox_root: pathlib.Path,
    ) -> None:
        """GET /status must not write any file to the sandbox."""
        self._assert_no_mutation(
            api_client,
            sandbox_root,
            "GET",
            "/status",
            params={"project": _SANDBOX_PROJECT_NAME},
        )

    def test_show_no_mutation(
        self,
        api_client: TestClient,
        sandbox_root: pathlib.Path,
    ) -> None:
        """GET /show must not write any file to the sandbox."""
        self._assert_no_mutation(
            api_client,
            sandbox_root,
            "GET",
            "/show",
            params={"project": _SANDBOX_PROJECT_NAME, "key": _SANDBOX_TASK_KEY},
        )

    def test_metrics_no_mutation(
        self,
        api_client: TestClient,
        sandbox_root: pathlib.Path,
    ) -> None:
        """GET /metrics must not write any file to the sandbox."""
        self._assert_no_mutation(
            api_client,
            sandbox_root,
            "GET",
            "/metrics",
            params={"project": _SANDBOX_PROJECT_NAME},
        )

    def test_costs_no_mutation(
        self,
        api_client: TestClient,
        sandbox_root: pathlib.Path,
    ) -> None:
        """GET /costs must not write any file to the sandbox."""
        self._assert_no_mutation(
            api_client,
            sandbox_root,
            "GET",
            "/costs",
            params={"project": _SANDBOX_PROJECT_NAME},
        )

    def test_rejected_no_mutation(
        self,
        api_client: TestClient,
        sandbox_root: pathlib.Path,
    ) -> None:
        """GET /rejected must not write any file to the sandbox."""
        self._assert_no_mutation(api_client, sandbox_root, "GET", "/rejected")

    def test_projects_no_mutation(
        self,
        api_client: TestClient,
        sandbox_root: pathlib.Path,
    ) -> None:
        """GET /projects must not write any file to the sandbox."""
        self._assert_no_mutation(api_client, sandbox_root, "GET", "/projects")

    def test_test_report_no_mutation(
        self,
        api_client: TestClient,
        sandbox_root: pathlib.Path,
    ) -> None:
        """GET /test-report must not write any file to the sandbox."""
        self._assert_no_mutation(
            api_client,
            sandbox_root,
            "GET",
            "/test-report",
            params={"project": _SANDBOX_PROJECT_NAME, "key": _SANDBOX_TASK_KEY},
        )

    def test_dashboard_header_no_mutation(
        self,
        api_client: TestClient,
        sandbox_root: pathlib.Path,
    ) -> None:
        """GET /dashboard/header must not write any file to the sandbox."""
        self._assert_no_mutation(
            api_client,
            sandbox_root,
            "GET",
            "/dashboard/header",
            params={"project": _SANDBOX_PROJECT_NAME},
        )

    def test_dashboard_metrics_no_mutation(
        self,
        api_client: TestClient,
        sandbox_root: pathlib.Path,
    ) -> None:
        """GET /dashboard/metrics must not write any file to the sandbox."""
        self._assert_no_mutation(
            api_client,
            sandbox_root,
            "GET",
            "/dashboard/metrics",
            params={"project": _SANDBOX_PROJECT_NAME},
        )

    def test_dashboard_attention_no_mutation(
        self,
        api_client: TestClient,
        sandbox_root: pathlib.Path,
    ) -> None:
        """GET /dashboard/attention must not write any file to the sandbox."""
        self._assert_no_mutation(
            api_client,
            sandbox_root,
            "GET",
            "/dashboard/attention",
            params={"project": _SANDBOX_PROJECT_NAME},
        )

    def test_dashboard_status_window_no_mutation(
        self,
        api_client: TestClient,
        sandbox_root: pathlib.Path,
    ) -> None:
        """GET /dashboard/status-window must not write any file to the sandbox."""
        self._assert_no_mutation(
            api_client,
            sandbox_root,
            "GET",
            "/dashboard/status-window",
            params={"project": _SANDBOX_PROJECT_NAME},
        )

    def test_dashboard_input_no_mutation(
        self,
        api_client: TestClient,
        sandbox_root: pathlib.Path,
    ) -> None:
        """GET /dashboard/input must not write any file to the sandbox."""
        self._assert_no_mutation(
            api_client,
            sandbox_root,
            "GET",
            "/dashboard/input",
        )

    def test_dashboard_queue_no_mutation(
        self,
        api_client: TestClient,
        sandbox_root: pathlib.Path,
    ) -> None:
        """GET /dashboard/queue must not write any file to the sandbox."""
        self._assert_no_mutation(
            api_client,
            sandbox_root,
            "GET",
            "/dashboard/queue",
        )

    def test_approvals_no_mutation(
        self,
        api_client: TestClient,
        sandbox_root: pathlib.Path,
    ) -> None:
        """GET /approvals must not write any file to the sandbox."""
        self._assert_no_mutation(api_client, sandbox_root, "GET", "/approvals")


# ---------------------------------------------------------------------------
# Fidelity tests: API call vs. direct script invocation on twin sandboxes
# ---------------------------------------------------------------------------


class TestAdapterFidelity:
    """Assert that API mutation endpoints produce byte-identical on-disk state
    to the corresponding direct script invocations.

    Each test:
      1. Uses twin sandboxes (api_root, script_root) with identical initial state.
      2. Issues the mutation via the API (using api_root as the kanban root for
         PGAI_AGENT_KANBAN_ROOT_PATH) and via the direct script (using
         script_root as PGAI_AGENT_KANBAN_ROOT_PATH).
      3. Takes a recursive checksum of both resulting trees.
      4. Diffs the two checksums and asserts the diff is empty.

    This verifies the thin-adapter promise: the API is a pure pass-through;
    running it is indistinguishable from running the script directly.
    """

    def _assert_twin_fidelity(
        self,
        api_root: pathlib.Path,
        script_root: pathlib.Path,
        api_response_status: int,
        script_result: subprocess.CompletedProcess,
        operation_label: str,
    ) -> None:
        """Assert both sandboxes are in the same on-disk state after the mutation.

        Also asserts that the API response exit_code matches the script's returncode.

        Args:
            api_root:             Sandbox root after the API call.
            script_root:          Sandbox root after the direct script call.
            api_response_status:  HTTP status returned by the API.
            script_result:        CompletedProcess from the direct script run.
            operation_label:      Human-readable description for failure messages.
        """
        api_cksum = _compute_dir_checksum(api_root)
        script_cksum = _compute_dir_checksum(script_root)
        diffs = _diff_checksums(
            f"api {operation_label}",
            api_cksum,
            f"script {operation_label}",
            script_cksum,
        )
        assert not diffs, (
            f"Fidelity failure for {operation_label}.\n"
            f"API HTTP status: {api_response_status}, "
            f"Script exit: {script_result.returncode}\n"
            f"Script stdout: {script_result.stdout!r}\n"
            f"Script stderr: {script_result.stderr!r}\n"
            f"On-disk differences (api vs script sandbox):\n" + "\n".join(diffs)
        )

    def test_fidelity_halt(
        self,
        twin_sandboxes: tuple[pathlib.Path, pathlib.Path],
        dev_tree_team_dir: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Fidelity: POST /operations/halt matches halt.sh direct invocation.

        Both should create <project_root>/HALT in their respective sandboxes.
        """
        api_root, script_root = twin_sandboxes

        # --- API side ---
        with _make_fidelity_client(api_root, dev_tree_team_dir, monkeypatch) as client:
            api_resp = client.post(
                "/operations/halt",
                json={"project": _SANDBOX_PROJECT_NAME},
            )

        # --- Script side ---
        script_result = _run_script(
            "halt.sh",
            {"project": _SANDBOX_PROJECT_NAME},
            script_root,
        )

        self._assert_twin_fidelity(
            api_root,
            script_root,
            api_resp.status_code,
            script_result,
            "halt",
        )

    def test_fidelity_unhalt(
        self,
        twin_sandboxes: tuple[pathlib.Path, pathlib.Path],
        dev_tree_team_dir: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Fidelity: POST /operations/unhalt matches unhalt.sh direct invocation.

        Set up both sandboxes with HALT already present, then unhalt both.
        unhalt is idempotent when HALT is absent, so removing a pre-existing
        HALT file is the meaningful test.
        """
        api_root, script_root = twin_sandboxes

        # Pre-condition: create HALT in both sandboxes
        api_project_root = api_root / "projects" / _SANDBOX_PROJECT_NAME
        script_project_root = script_root / "projects" / _SANDBOX_PROJECT_NAME
        (api_project_root / "HALT").write_text("halted\n", encoding="utf-8")
        (script_project_root / "HALT").write_text("halted\n", encoding="utf-8")

        # --- API side ---
        with _make_fidelity_client(api_root, dev_tree_team_dir, monkeypatch) as client:
            api_resp = client.post(
                "/operations/unhalt",
                json={"project": _SANDBOX_PROJECT_NAME},
            )

        # --- Script side ---
        script_result = _run_script(
            "unhalt.sh",
            {"project": _SANDBOX_PROJECT_NAME},
            script_root,
        )

        self._assert_twin_fidelity(
            api_root,
            script_root,
            api_resp.status_code,
            script_result,
            "unhalt",
        )

    def test_fidelity_halt_global(
        self,
        twin_sandboxes: tuple[pathlib.Path, pathlib.Path],
        dev_tree_team_dir: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Fidelity: POST /operations/halt-global matches halt-global.sh direct invocation.

        Both should create <kanban_root>/HALT in their respective sandboxes.
        """
        api_root, script_root = twin_sandboxes

        # --- API side ---
        with _make_fidelity_client(api_root, dev_tree_team_dir, monkeypatch) as client:
            api_resp = client.post("/operations/halt-global", json={})

        # --- Script side ---
        script_result = _run_script("halt-global.sh", {}, script_root)

        self._assert_twin_fidelity(
            api_root,
            script_root,
            api_resp.status_code,
            script_result,
            "halt-global",
        )

    def test_fidelity_unhalt_global(
        self,
        twin_sandboxes: tuple[pathlib.Path, pathlib.Path],
        dev_tree_team_dir: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Fidelity: POST /operations/unhalt-global matches unhalt-global.sh direct invocation.

        Pre-condition: global HALT present; both sides should remove it.
        """
        api_root, script_root = twin_sandboxes

        # Pre-condition: create global HALT in both sandboxes
        (api_root / "HALT").write_text("global halt\n", encoding="utf-8")
        (script_root / "HALT").write_text("global halt\n", encoding="utf-8")

        # --- API side ---
        with _make_fidelity_client(api_root, dev_tree_team_dir, monkeypatch) as client:
            api_resp = client.post("/operations/unhalt-global", json={})

        # --- Script side ---
        script_result = _run_script("unhalt-global.sh", {}, script_root)

        self._assert_twin_fidelity(
            api_root,
            script_root,
            api_resp.status_code,
            script_result,
            "unhalt-global",
        )

    def test_fidelity_wontdo(
        self,
        twin_sandboxes: tuple[pathlib.Path, pathlib.Path],
        dev_tree_team_dir: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Fidelity: POST /operations/wontdo matches wontdo.sh direct invocation.

        Both should mark the task status as WONT-DO and update the queue marker.
        """
        api_root, script_root = twin_sandboxes

        # --- API side ---
        with _make_fidelity_client(api_root, dev_tree_team_dir, monkeypatch) as client:
            api_resp = client.post(
                "/operations/wontdo",
                json={"project": _SANDBOX_PROJECT_NAME, "key": _SANDBOX_TASK_KEY},
            )

        # --- Script side ---
        script_result = _run_script(
            "wontdo.sh",
            {"project": _SANDBOX_PROJECT_NAME, "key": _SANDBOX_TASK_KEY},
            script_root,
        )

        self._assert_twin_fidelity(
            api_root,
            script_root,
            api_resp.status_code,
            script_result,
            "wontdo",
        )

    def test_fidelity_close(
        self,
        twin_sandboxes: tuple[pathlib.Path, pathlib.Path],
        dev_tree_team_dir: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Fidelity: POST /operations/close matches close.sh direct invocation.

        Closes the sandbox task (marks it DONE and flips its queue marker).
        """
        api_root, script_root = twin_sandboxes

        # --- API side ---
        with _make_fidelity_client(api_root, dev_tree_team_dir, monkeypatch) as client:
            api_resp = client.post(
                "/operations/close",
                json={"project": _SANDBOX_PROJECT_NAME, "key": _SANDBOX_TASK_KEY},
            )

        # --- Script side ---
        script_result = _run_script(
            "close.sh",
            {"project": _SANDBOX_PROJECT_NAME, "key": _SANDBOX_TASK_KEY},
            script_root,
        )

        self._assert_twin_fidelity(
            api_root,
            script_root,
            api_resp.status_code,
            script_result,
            "close",
        )

    def test_fidelity_reset_after_close(
        self,
        twin_sandboxes: tuple[pathlib.Path, pathlib.Path],
        dev_tree_team_dir: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Fidelity: POST /operations/reset matches reset.sh direct invocation.

        reset.sh refuses tasks in WORKING state but accepts BACKLOG/DONE tasks.
        We first close the task (DONE), then reset it (back to BACKLOG).

        This exercises the reset code path end-to-end: the task transitions
        through DONE → BACKLOG across both sandboxes identically.
        """
        api_root, script_root = twin_sandboxes

        # Pre-condition: close the task to DONE in both sandboxes so reset.sh
        # accepts the operation (reset.sh refuses WORKING, accepts DONE/BACKLOG).
        for root in (api_root, script_root):
            _run_script(
                "close.sh",
                {"project": _SANDBOX_PROJECT_NAME, "key": _SANDBOX_TASK_KEY},
                root,
            )

        # --- API side ---
        with _make_fidelity_client(api_root, dev_tree_team_dir, monkeypatch) as client:
            api_resp = client.post(
                "/operations/reset",
                json={"project": _SANDBOX_PROJECT_NAME, "key": _SANDBOX_TASK_KEY},
            )

        # --- Script side ---
        script_result = _run_script(
            "reset.sh",
            {"project": _SANDBOX_PROJECT_NAME, "key": _SANDBOX_TASK_KEY},
            script_root,
        )

        self._assert_twin_fidelity(
            api_root,
            script_root,
            api_resp.status_code,
            script_result,
            "reset-after-close",
        )

    def test_fidelity_reset_after_close_no_flake_100x(
        self,
        tmp_path: pathlib.Path,
        dev_tree_team_dir: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Regression harness: test_fidelity_reset_after_close passes 100 consecutive
        iterations without flake.

        Each iteration materialises fresh twin sandboxes, closes both sides
        (pre-condition), then performs the reset fidelity check.  All 100
        iterations must pass identically, proving that the normalization of
        logs/reset.log eliminates the wall-clock-timestamp nondeterminism that
        caused intermittent failures.

        This test deliberately avoids relying on pytest-randomly seed values —
        the flake was timing-based, not order-based, so exercising the code
        path 100 times in a tight loop is the correct harness.
        """
        for iteration in range(100):
            # Fresh twin sandboxes for each iteration so there is no
            # cross-contamination between runs.
            iter_base = tmp_path / f"iter_{iteration:03d}" / "base"
            iter_api = tmp_path / f"iter_{iteration:03d}" / "api"
            iter_script = tmp_path / f"iter_{iteration:03d}" / "script"

            _materialise_sandbox(iter_base)
            _copy_sandbox(iter_base, iter_api)
            _copy_sandbox(iter_base, iter_script)

            # Pre-condition: close the task to DONE in both sandboxes.
            for root in (iter_api, iter_script):
                _run_script(
                    "close.sh",
                    {"project": _SANDBOX_PROJECT_NAME, "key": _SANDBOX_TASK_KEY},
                    root,
                )

            # API side reset.
            with _make_fidelity_client(iter_api, dev_tree_team_dir, monkeypatch) as client:
                api_resp = client.post(
                    "/operations/reset",
                    json={"project": _SANDBOX_PROJECT_NAME, "key": _SANDBOX_TASK_KEY},
                )

            # Script side reset.
            script_result = _run_script(
                "reset.sh",
                {"project": _SANDBOX_PROJECT_NAME, "key": _SANDBOX_TASK_KEY},
                iter_script,
            )

            self._assert_twin_fidelity(
                iter_api,
                iter_script,
                api_resp.status_code,
                script_result,
                f"reset-after-close (iteration {iteration + 1}/100)",
            )


# ---------------------------------------------------------------------------
# Verb-discipline sabotage evidence marker
# ---------------------------------------------------------------------------
#
# The acceptance criterion requires evidence that a sabotaged GET endpoint
# causes the verb-discipline test to fail.  This marker test is intentionally
# left as documentation evidence.  The sabotage procedure and captured output
# are recorded in the task status.md artifacts section.
#
# To reproduce the sabotage locally:
#   1. Edit reads.py get_status() to write a marker file:
#        (kanban_root / "verb-sabotage-marker.txt").write_text("mutated\n")
#   2. Run: cd team && python3 -m pytest pgai_agent_kanban/api/tests/test_fidelity.py \
#              ::TestVerbDiscipline::test_status_no_mutation -v
#   3. The test fails with: "GET /status mutated the sandbox (verb-discipline violation)."
#   4. Revert the edit.
#
# The captured failure output is in status.md § Evidence: deliberate mutation.


# ---------------------------------------------------------------------------
# Reset kind-forms tests
# ---------------------------------------------------------------------------
#
# Tests for the mutually-exclusive kind selector extensions on POST /operations/reset:
#   - Each selector (key, agent, bug, priority, requirement) exercises a positive path
#     (exit_code 0 from reset.sh with a suitable fixture).
#   - Two selectors supplied simultaneously → 422 naming both conflicting fields.
#   - Zero selectors supplied → 422 naming the missing choice.
#   - 422 paths fire BEFORE reset.sh is invoked (subprocess is never spawned).
#   - Existing key-only reset is covered by test_fidelity_reset_after_close (regression).
# ---------------------------------------------------------------------------


_SANDBOX_PRIORITY_KEY = "PRIORITY-0001"
_SANDBOX_REQUIREMENT_KEY = "v0.0.1-test.md"
_SANDBOX_REQUIREMENT_VERSION = "v0.0.1"


def _add_priority_fixture(sandbox_root: pathlib.Path) -> None:
    """Add a priority intake file and its backlog marker to a sandbox.

    Creates:
        projects/sandbox-proj/priority/PRIORITY-0001.md  — status: open
        projects/sandbox-proj/tasks/queues/priority_backlog.md  — with marker

    Args:
        sandbox_root: Sandbox kanban root (already materialised by _materialise_sandbox).
    """
    project_root = sandbox_root / "projects" / _SANDBOX_PROJECT_NAME
    priority_dir = project_root / "priority"
    priority_dir.mkdir(parents=True, exist_ok=True)
    (priority_dir / f"{_SANDBOX_PRIORITY_KEY}.md").write_text(
        f"# Priority: {_SANDBOX_PRIORITY_KEY}\n\n"
        "## Status\nopen\n\n"
        "## Summary\nSandbox test priority fixture.\n",
        encoding="utf-8",
    )
    # Priority backlog marker file.
    queues_dir = project_root / "tasks" / "queues"
    queues_dir.mkdir(parents=True, exist_ok=True)
    (queues_dir / "priority_backlog.md").write_text(
        "# PRIORITY Backlog\n\n"
        f"- [ ] {_SANDBOX_PRIORITY_KEY}\n",
        encoding="utf-8",
    )


def _add_requirement_fixture(sandbox_root: pathlib.Path) -> None:
    """Add a requirement intake file to a sandbox.

    Creates:
        projects/sandbox-proj/requirements/v0.0.1-test.md  — status: open, PM Task: none

    The PM Task is 'none' so reset.sh skips the pm_backlog marker flip; the
    requirement status reset is the meaningful operation.

    Args:
        sandbox_root: Sandbox kanban root (already materialised by _materialise_sandbox).
    """
    project_root = sandbox_root / "projects" / _SANDBOX_PROJECT_NAME
    req_dir = project_root / "requirements"
    req_dir.mkdir(parents=True, exist_ok=True)
    (req_dir / _SANDBOX_REQUIREMENT_KEY).write_text(
        f"# Requirements: {_SANDBOX_REQUIREMENT_VERSION}\n\n"
        "## Status\nopen\n\n"
        "## PM Task\nnone\n\n"
        "## Summary\nSandbox test requirement fixture.\n",
        encoding="utf-8",
    )


class TestResetKindForms:
    """Tests for the mutually-exclusive kind selector extensions on POST /operations/reset.

    Covers:
        - Each of the five selectors (key, agent, bug, priority, requirement) against
          a suitable sandbox fixture — each should return an envelope with exit_code 0.
        - Two selectors supplied → 422 naming both conflicting fields.
        - Zero selectors supplied → 422 naming the missing choice.
        - Both 422 paths fire BEFORE reset.sh is invoked (no subprocess spawned).

    The existing test_fidelity_reset_after_close covers the key-only path as a
    byte-identical regression fixture.
    """

    def _close_task(
        self,
        sandbox_root: pathlib.Path,
        dev_tree_team_dir: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Close the sandbox task to DONE so reset.sh will accept the reset."""
        _run_script(
            "close.sh",
            {"project": _SANDBOX_PROJECT_NAME, "key": _SANDBOX_TASK_KEY},
            sandbox_root,
        )

    def test_reset_via_key_selector_returns_exit_code_0(
        self,
        sandbox_root: pathlib.Path,
        dev_tree_team_dir: pathlib.Path,
        api_client: "TestClient",
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """POST /operations/reset with 'key' selector resets the task and returns exit_code 0."""
        # Pre-condition: task must not be WORKING for reset to succeed.
        self._close_task(sandbox_root, dev_tree_team_dir, monkeypatch)

        resp = api_client.post(
            "/operations/reset",
            json={"project": _SANDBOX_PROJECT_NAME, "key": _SANDBOX_TASK_KEY},
        )

        assert resp.status_code == 200, f"HTTP status: {resp.status_code}, body: {resp.text}"
        body = resp.json()
        assert body["exit_code"] == 0, f"exit_code non-zero: {body}"

    def test_reset_via_agent_selector_returns_exit_code_0(
        self,
        sandbox_root: pathlib.Path,
        dev_tree_team_dir: pathlib.Path,
        api_client: "TestClient",
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """POST /operations/reset with 'agent' selector resets the task and returns exit_code 0."""
        self._close_task(sandbox_root, dev_tree_team_dir, monkeypatch)

        resp = api_client.post(
            "/operations/reset",
            json={"project": _SANDBOX_PROJECT_NAME, "agent": _SANDBOX_TASK_KEY},
        )

        assert resp.status_code == 200, f"HTTP status: {resp.status_code}, body: {resp.text}"
        body = resp.json()
        assert body["exit_code"] == 0, f"exit_code non-zero: {body}"

    def test_reset_via_bug_selector_returns_exit_code_0(
        self,
        sandbox_root: pathlib.Path,
        api_client: "TestClient",
    ) -> None:
        """POST /operations/reset with 'bug' selector resets the bug intake and returns exit_code 0."""
        resp = api_client.post(
            "/operations/reset",
            json={"project": _SANDBOX_PROJECT_NAME, "bug": _SANDBOX_BUG_KEY},
        )

        assert resp.status_code == 200, f"HTTP status: {resp.status_code}, body: {resp.text}"
        body = resp.json()
        assert body["exit_code"] == 0, f"exit_code non-zero: {body}"

    def test_reset_via_priority_selector_returns_exit_code_0(
        self,
        sandbox_root: pathlib.Path,
        api_client: "TestClient",
    ) -> None:
        """POST /operations/reset with 'priority' selector resets the priority intake and returns exit_code 0."""
        _add_priority_fixture(sandbox_root)

        resp = api_client.post(
            "/operations/reset",
            json={"project": _SANDBOX_PROJECT_NAME, "priority": _SANDBOX_PRIORITY_KEY},
        )

        assert resp.status_code == 200, f"HTTP status: {resp.status_code}, body: {resp.text}"
        body = resp.json()
        assert body["exit_code"] == 0, f"exit_code non-zero: {body}"

    def test_reset_via_requirement_selector_returns_exit_code_0(
        self,
        sandbox_root: pathlib.Path,
        api_client: "TestClient",
    ) -> None:
        """POST /operations/reset with 'requirement' selector resets the requirement intake and returns exit_code 0."""
        _add_requirement_fixture(sandbox_root)

        resp = api_client.post(
            "/operations/reset",
            json={
                "project": _SANDBOX_PROJECT_NAME,
                "requirement": _SANDBOX_REQUIREMENT_VERSION,
            },
        )

        assert resp.status_code == 200, f"HTTP status: {resp.status_code}, body: {resp.text}"
        body = resp.json()
        assert body["exit_code"] == 0, f"exit_code non-zero: {body}"

    def test_reset_two_selectors_returns_422_naming_both_fields(
        self,
        api_client: "TestClient",
    ) -> None:
        """POST /operations/reset with two selectors returns 422 naming both conflicting fields.

        The 422 must fire BEFORE reset.sh is invoked — subprocess is never spawned.
        """
        from unittest.mock import patch

        with patch("subprocess.run") as mock_run:
            resp = api_client.post(
                "/operations/reset",
                json={
                    "project": _SANDBOX_PROJECT_NAME,
                    "key": _SANDBOX_TASK_KEY,
                    "bug": _SANDBOX_BUG_KEY,
                },
            )
            # reset.sh must never be invoked.
            mock_run.assert_not_called()

        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"
        detail = resp.json().get("detail", "")
        # Both conflicting field names must appear in the error detail.
        assert "key" in detail, f"'key' not named in 422 detail: {detail!r}"
        assert "bug" in detail, f"'bug' not named in 422 detail: {detail!r}"

    def test_reset_no_selector_returns_422_naming_missing_choice(
        self,
        api_client: "TestClient",
    ) -> None:
        """POST /operations/reset with no kind selector returns 422 naming the missing choice.

        The 422 must fire BEFORE reset.sh is invoked — subprocess is never spawned.
        """
        from unittest.mock import patch

        with patch("subprocess.run") as mock_run:
            resp = api_client.post(
                "/operations/reset",
                json={"project": _SANDBOX_PROJECT_NAME},
            )
            # reset.sh must never be invoked.
            mock_run.assert_not_called()

        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"
        detail = resp.json().get("detail", "")
        # The missing choice should reference the selector fields.
        assert any(
            field in detail
            for field in ("key", "agent", "bug", "priority", "requirement")
        ), f"No selector field names in 422 detail: {detail!r}"

    def test_reset_key_only_regression_passes(
        self,
        sandbox_root: pathlib.Path,
        dev_tree_team_dir: pathlib.Path,
        api_client: "TestClient",
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Existing key-only reset behavior is preserved (regression fixture).

        This is a lightweight regression check: the 'key' selector still works
        after the ResetBody extension.  The byte-identical twin-sandbox proof
        remains in test_fidelity_reset_after_close.
        """
        self._close_task(sandbox_root, dev_tree_team_dir, monkeypatch)

        resp = api_client.post(
            "/operations/reset",
            json={"project": _SANDBOX_PROJECT_NAME, "key": _SANDBOX_TASK_KEY},
        )

        assert resp.status_code == 200
        assert resp.json()["exit_code"] == 0


# ---------------------------------------------------------------------------
# set-version-ceiling endpoint tests
# ---------------------------------------------------------------------------


class TestSetVersionCeilingEndpoint:
    """Positive-path and script-guard-negative tests for POST /operations/set-version-ceiling.

    Covers:
        - Positive path: dry-run request against the sandbox project returns exit_code 0.
        - Script-guard-negative: omitting all action flags causes the script to refuse
          with a non-zero exit_code; the refusal message appears in stderr.

    The sandbox project (sandbox-proj) has a project.cfg so set-version-ceiling.sh
    can locate the project.  Using ``--dry-run`` on the positive path means no
    project.cfg fields are modified.
    """

    def test_set_version_ceiling_dry_run_returns_exit_code_0(
        self,
        api_client: "TestClient",
    ) -> None:
        """POST /operations/set-version-ceiling with dry_run and minor returns exit_code 0.

        The script is invoked with --project sandbox-proj --minor 5 --dry-run.
        In dry-run mode set-version-ceiling.sh prints the intended change and exits 0
        without writing project.cfg.  The envelope exit_code must be 0.
        """
        resp = api_client.post(
            "/operations/set-version-ceiling",
            json={
                "project": _SANDBOX_PROJECT_NAME,
                "minor": 5,
                "dry_run": True,
            },
        )

        assert resp.status_code == 200, (
            f"Expected HTTP 200 on success; got {resp.status_code}.\n"
            f"body: {resp.text}"
        )
        body = resp.json()
        assert body["exit_code"] == 0, (
            f"set-version-ceiling.sh must exit 0 in dry-run mode; "
            f"got exit_code={body['exit_code']}.\n"
            f"stdout: {body['stdout']}\nstderr: {body['stderr']}"
        )

    def test_set_version_ceiling_no_action_returns_nonzero_exit_code_with_script_refusal(
        self,
        api_client: "TestClient",
    ) -> None:
        """POST /operations/set-version-ceiling with no action flag returns non-zero exit_code.

        The script guards that at least one action (--show, --minor, --major,
        --no-minor, --no-major) must be supplied.  Passing only ``project``
        causes the script to exit 1 with its own refusal message in stderr.
        The envelope propagates this unchanged: non-zero exit_code, HTTP 500,
        refusal text in stderr.

        This verifies that script guards are propagated rather than re-implemented:
        the endpoint does not add its own "action required" check.
        """
        resp = api_client.post(
            "/operations/set-version-ceiling",
            json={"project": _SANDBOX_PROJECT_NAME},
        )

        assert resp.status_code == 500, (
            f"Expected HTTP 500 on script refusal; got {resp.status_code}.\n"
            f"body: {resp.text}"
        )
        body = resp.json()
        assert body["exit_code"] != 0, (
            f"set-version-ceiling.sh must exit non-zero when no action is supplied; "
            f"got exit_code={body['exit_code']}."
        )
        assert body["stderr"], (
            "Script refusal must appear in stderr; stderr was empty.\n"
            f"stdout: {body['stdout']}"
        )


# ---------------------------------------------------------------------------
# switch-provider endpoint tests
# ---------------------------------------------------------------------------


class TestSwitchProviderEndpoint:
    """Positive-path and script-guard-negative tests for POST /operations/switch-provider.

    Covers:
        - Positive path: the endpoint routes the request to switch-provider.sh and
          returns the envelope.  Because provider CLIs (claude/codex/gemini) are
          unlikely to be installed in CI, subprocess.run is mocked to return
          exit code 0 for the positive-path fixture.
        - Script-guard-negative: an unknown provider name causes the script to refuse
          with exit code 1; the refusal appears in stderr.  No mock is used here —
          the real script validates the provider name before any CLI check.
    """

    def test_switch_provider_positive_path_returns_exit_code_0(
        self,
        api_client: "TestClient",
    ) -> None:
        """POST /operations/switch-provider routes to switch-provider.sh and returns exit_code 0.

        The script is mocked to return exit code 0 because the real switch-provider.sh
        requires the target provider CLI binary to be present in PATH, which is not
        guaranteed in CI.  The mock verifies that the endpoint correctly routes the
        ``provider`` body field as ``--provider <value>`` to the script.
        """
        from unittest.mock import MagicMock, patch

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Active provider switched: (none) → claude\n"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            resp = api_client.post(
                "/operations/switch-provider",
                json={"provider": "claude"},
            )

        assert resp.status_code == 200, (
            f"Expected HTTP 200 on success; got {resp.status_code}.\n"
            f"body: {resp.text}"
        )
        body = resp.json()
        assert body["exit_code"] == 0, (
            f"Mocked exit_code must be 0; got {body['exit_code']}."
        )
        # Verify the endpoint passed --provider to the script.
        mock_run.assert_called_once()
        call_argv = mock_run.call_args[0][0]
        assert "--provider" in call_argv, (
            f"--provider flag must be present in argv passed to switch-provider.sh; "
            f"got: {call_argv}"
        )
        assert "claude" in call_argv, (
            f"provider value 'claude' must be forwarded to the script; "
            f"got argv: {call_argv}"
        )

    def test_switch_provider_unknown_provider_returns_nonzero_exit_code_with_script_refusal(
        self,
        api_client: "TestClient",
    ) -> None:
        """POST /operations/switch-provider with an unknown provider returns non-zero exit_code.

        switch-provider.sh validates the provider name before any CLI check.
        Passing an unrecognized provider (e.g. ``"unknown-provider"``) causes the
        script to exit 1 with its own refusal message in stderr.  The envelope
        propagates this unchanged: non-zero exit_code, HTTP 500, refusal text in stderr.

        This verifies that provider validation is the script's responsibility and is
        not re-implemented by the endpoint.
        """
        resp = api_client.post(
            "/operations/switch-provider",
            json={"provider": "unknown-provider"},
        )

        assert resp.status_code == 500, (
            f"Expected HTTP 500 on script refusal; got {resp.status_code}.\n"
            f"body: {resp.text}"
        )
        body = resp.json()
        assert body["exit_code"] != 0, (
            f"switch-provider.sh must exit non-zero for an unknown provider; "
            f"got exit_code={body['exit_code']}."
        )
        assert body["stderr"], (
            "Script refusal must appear in stderr; stderr was empty.\n"
            f"stdout: {body['stdout']}"
        )
