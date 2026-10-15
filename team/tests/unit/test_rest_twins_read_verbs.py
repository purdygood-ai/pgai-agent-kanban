"""
test_rest_twins_read_verbs.py
==============================
Acceptance-criteria tests for the REST twins fronting the five read verbs --
why (via GET /verdicts), show, show-queues, verify-rc-state, and
release-locks (read-only listing mode).

Acceptance criteria exercised:

  1. GET /verdicts returns the same verdict for a project that why.sh
     reports for that project, including the verdict token and its cause.
  2. Each of the five endpoints returns the shared response envelope, and a
     test asserts the envelope shape for each.
  3. A refusal reproduced on both surfaces -- show.sh's project-not-found --
     yields the same stable machine error key from the CLI and from HTTP.
  4. Each endpoint calls the shipped implementation rather than a Python
     re-derivation: the endpoint's output changes when the underlying
     script's output changes, and matches a direct CLI invocation of the
     same script against the same fixture byte-for-byte.
  5. The operator verb manifest's absent-twin markers for these five verbs
     are flipped to real values (no "ABSENT" REST columns remain).

All fixture construction uses tmp_path (pytest-managed, redirected under
PGAI_AGENT_KANBAN_TEMP_DIR by the parent conftest.py's autouse fixture).  No
live kanban root is touched. A route that shells out to a script gets an
API whose kanban_root is the real team/ dev tree (so scripts resolve to
production code) while PGAI_AGENT_KANBAN_ROOT_PATH points the script at the
tmp_path sandbox; a route that reads data in process (GET /verdicts, GET
/show, ...) gets an API rooted at the sandbox itself, since every such
route reads the server's own kanban root.

Naming: function names describe behavior under test, never bug IDs or task
IDs (see roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess

import pytest

# ---------------------------------------------------------------------------
# Paths (relative to this file: team/tests/unit/ -> team/ -> repo root)
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_WHY_SH = _SCRIPTS_DIR / "why.sh"
_SHOW_SH = _SCRIPTS_DIR / "show.sh"
_SHOW_QUEUES_SH = _SCRIPTS_DIR / "dashboard" / "show-queues.sh"
_VERIFY_RC_STATE_SH = _SCRIPTS_DIR / "verify-rc-state.sh"
_RELEASE_LOCKS_SH = _SCRIPTS_DIR / "release-locks.sh"

# Ensure team/ is importable so pgai_agent_kanban resolves regardless of the
# caller's cwd (mirrors test_review_cmds.py's own sys.path bootstrap).
import sys  # noqa: E402
from tests.fixtures.binary_guard import require_binary


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH.

    Every test here builds, inspects, or drives a real git repository; a
    module-wide autouse guard keeps that requirement in one place instead
    of repeating a per-test check at each git-dependent call site.
    """
    require_binary("git", label="rest-twins-read-verbs")

_team_str = str(_TEAM_DIR)
if _team_str not in sys.path:
    sys.path.insert(0, _team_str)


# ---------------------------------------------------------------------------
# Shared fixture builders
# ---------------------------------------------------------------------------


def _write_projects_cfg(root: pathlib.Path, projects: list[tuple[str, int]]) -> None:
    lines = []
    for name, priority in projects:
        lines.append(f"[project:{name}]")
        lines.append(f"priority={priority}")
        lines.append("")
    (root / "projects.cfg").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_task(project_dir: pathlib.Path, task_id: str, state: str) -> None:
    task_dir = project_dir / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "status.md").write_text(
        f"## State\n{state}\n\n## Summary\nfixture task\n", encoding="utf-8"
    )


def _env_for(kanban_root: pathlib.Path) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PGAI_PROJECT_NAME", None)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env["KANBAN_ROOT"] = str(kanban_root)
    return env


def _make_client(
    sandbox_root: pathlib.Path, monkeypatch: pytest.MonkeyPatch, *, in_process: bool = False
):
    """Create a TestClient whose scripts resolve to the real team/ dev tree
    while a script's data resolves against sandbox_root (through
    PGAI_AGENT_KANBAN_ROOT_PATH).

    ``in_process=True`` roots the API itself at sandbox_root instead, for a
    route that reads in process against the API's own kanban_root (GET
    /verdicts, GET /show, GET /show-queues, GET /release-locks, GET
    /verify-rc-state) rather than through a script.
    """
    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig
    from fastapi.testclient import TestClient

    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(sandbox_root))
    monkeypatch.setenv("KANBAN_ROOT", str(sandbox_root))
    monkeypatch.delenv("PGAI_PROJECT_NAME", raising=False)

    api_root = sandbox_root if in_process else _TEAM_DIR
    cfg = ApiConfig(host="127.0.0.1", port=0, kanban_root=api_root)
    return TestClient(create_app(cfg=cfg), raise_server_exceptions=True)


def _init_local_repo(repo_path: pathlib.Path) -> None:
    """Initialise a minimal local git repo with one commit -- no remote."""
    repo_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True
    )
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo_path, check=True)
    (repo_path / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo_path, check=True)


def _build_verify_rc_state_kanban_root(
    parent: pathlib.Path, project_name: str, repo_path: pathlib.Path
) -> pathlib.Path:
    """Minimal kanban root verify-rc-state.sh can run against (mirrors
    test_verify_rc_state_release_check.py's own fixture builder)."""
    root = parent / "kanban_root"
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "locks").mkdir(parents=True, exist_ok=True)
    (root / "projects.cfg").write_text(
        f"[project:{project_name}]\npriority=1\nenabled=true\n", encoding="utf-8"
    )
    proj = root / "projects" / project_name
    (proj / "tasks" / "queues").mkdir(parents=True, exist_ok=True)
    (proj / "project.cfg").write_text(
        "[project]\n"
        f"project_name = {project_name}\n"
        f"dev_tree_path = {repo_path}\n"
        f"git_repo_url = git@example.com:{project_name}.git\n"
        "git_remote_name = origin\n"
        "workflow_type = release\n"
        "is_self_build = false\n"
        "push_to_remote = false\n"
        "\n[versioning]\nmax_patch = 99\nmax_minor = 9\nmax_major = 0\n",
        encoding="utf-8",
    )
    return root


def _write_release_state(project_root: pathlib.Path, body: str) -> None:
    (project_root / "release-state.md").write_text(body, encoding="utf-8")


_ELAPSED_LINE_RE = re.compile(r"^  elapsed: .*$", re.MULTILINE)


def _normalize_elapsed(text: str) -> str:
    """Mask show-queues.sh's wall-clock 'elapsed: Ns' line before comparing.

    The CLI and HTTP calls in a same-fixture comparison run a second or so
    apart; the "currently working" section's elapsed-seconds figure can
    legitimately tick over between the two calls even though every other
    byte of output is identical. Masking this one timing artifact keeps the
    comparison meaningful (same data -> same rendered content) without
    making the test flaky on a clock-boundary crossing.
    """
    return _ELAPSED_LINE_RE.sub("  elapsed: <masked>", text)


# ---------------------------------------------------------------------------
# Acceptance criterion 1 -- GET /verdicts matches why.sh, verdict + cause
# ---------------------------------------------------------------------------


class TestVerdictsMatchesWhy:
    """GET /verdicts and why.sh resolve through the same engine -- same verdict,
    same cause, and (for a resolved project) the identical rendered line."""

    def test_running_project_verdict_and_line_match_cli(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        proj = "proj-running"
        _write_projects_cfg(tmp_path, [(proj, 1)])
        _write_task(tmp_path / "projects" / proj, "CODER-20260101-001-fixture", "WORKING")

        cli = subprocess.run(
            ["bash", str(_WHY_SH), "--project", proj],
            capture_output=True, text=True, env=_env_for(tmp_path), timeout=15,
        )
        assert cli.returncode == 0, cli.stdout + cli.stderr
        cli_line = cli.stdout.strip()
        assert "RUNNING" in cli_line, cli_line

        with _make_client(tmp_path, monkeypatch, in_process=True) as client:
            resp = client.get("/verdicts", params={"project": proj})
        assert resp.status_code == 200
        body = resp.json()
        entries = body["verdicts"]
        assert len(entries) == 1
        entry = entries[0]

        assert entry["verdict"] == "RUNNING", entry
        assert entry["line"] == cli_line, (
            f"HTTP line {entry['line']!r} does not match CLI line {cli_line!r} -- "
            "both surfaces must resolve through the same verdict engine."
        )
        assert "cause" in entry and entry["cause"], entry

    def test_idle_project_verdict_matches_cli(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        proj = "proj-idle"
        _write_projects_cfg(tmp_path, [(proj, 1)])
        (tmp_path / "projects" / proj / "tasks").mkdir(parents=True, exist_ok=True)

        cli = subprocess.run(
            ["bash", str(_WHY_SH), "--project", proj],
            capture_output=True, text=True, env=_env_for(tmp_path), timeout=15,
        )
        cli_line = cli.stdout.strip()
        assert "IDLE" in cli_line, cli_line

        with _make_client(tmp_path, monkeypatch, in_process=True) as client:
            resp = client.get("/verdicts", params={"project": proj})
        entry = resp.json()["verdicts"][0]
        assert entry["verdict"] == "IDLE"
        assert entry["line"] == cli_line


# ---------------------------------------------------------------------------
# Acceptance criterion 2 -- shared response envelope for each of the five
# ---------------------------------------------------------------------------


class TestEnvelopeShapeForEachEndpoint:
    """Every one of the five endpoints returns its documented envelope shape,
    and every shape carries a 'warnings' field (the universal contract)."""

    def test_verdicts_envelope_shape(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with _make_client(tmp_path, monkeypatch, in_process=True) as client:
            resp = client.get("/verdicts")
        assert resp.status_code == 200
        body = resp.json()
        assert "verdicts" in body and isinstance(body["verdicts"], list)
        assert "warnings" in body and isinstance(body["warnings"], list)

    def test_show_envelope_shape(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        proj = "proj-show"
        _write_projects_cfg(tmp_path, [(proj, 1)])
        _write_task(tmp_path / "projects" / proj, "CODER-20260101-002-fixture", "BACKLOG")
        with _make_client(tmp_path, monkeypatch, in_process=True) as client:
            resp = client.get(
                "/show", params={"project": proj, "key": "CODER-20260101-002-fixture"}
            )
        body = resp.json()
        for key in ("exit_code", "stdout", "stderr", "warnings"):
            assert key in body, f"'{key}' missing from GET /show envelope: {body!r}"

    def test_show_queues_envelope_shape(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        proj = "proj-queues"
        _write_projects_cfg(tmp_path, [(proj, 1)])
        (tmp_path / "projects" / proj / "tasks" / "queues").mkdir(parents=True, exist_ok=True)
        with _make_client(tmp_path, monkeypatch, in_process=True) as client:
            resp = client.get("/show-queues", params={"project": proj})
        body = resp.json()
        for key in ("exit_code", "stdout", "stderr", "warnings"):
            assert key in body, f"'{key}' missing from GET /show-queues envelope: {body!r}"

    def test_verify_rc_state_envelope_shape(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Deliberately an unresolvable project: the envelope shape must be
        # present regardless of whether the underlying check succeeds.
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/verify-rc-state", params={"project": "no-such-project"})
        body = resp.json()
        for key in ("exit_code", "stdout", "stderr", "warnings"):
            assert key in body, f"'{key}' missing from GET /verify-rc-state envelope: {body!r}"
        assert body["exit_code"] != 0

    def test_release_locks_envelope_shape(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with _make_client(tmp_path, monkeypatch) as client:
            resp = client.get("/release-locks")
        assert resp.status_code == 200
        body = resp.json()
        for key in ("exit_code", "stdout", "stderr", "warnings"):
            assert key in body, f"'{key}' missing from GET /release-locks envelope: {body!r}"
        assert body["exit_code"] == 0


# ---------------------------------------------------------------------------
# Acceptance criterion 3 -- refusal reproduces the same machine error key
# ---------------------------------------------------------------------------


class TestRefusalMachineKeyParityAcrossSurfaces:
    """A not-found refusal yields the identical stable machine error key
    ('project_resolve.not_found', emitted by pp_resolve_project_name and
    shared by every verb that resolves --project through it, show.sh
    included) whether it is observed via the CLI or via HTTP -- proof the
    REST layer never re-derives or reformats a script's own refusal."""

    def test_show_project_not_found_key_matches_cli(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_projects_cfg(tmp_path, [("registered-proj", 1)])
        (tmp_path / "projects" / "registered-proj" / "tasks").mkdir(
            parents=True, exist_ok=True
        )

        cli = subprocess.run(
            ["bash", str(_SHOW_SH), "--project", "ghost-project", "--key", "anything"],
            capture_output=True, text=True, env=_env_for(tmp_path), timeout=15,
        )
        assert cli.returncode != 0
        assert "project_resolve.not_found" in cli.stderr, cli.stderr

        with _make_client(tmp_path, monkeypatch, in_process=True) as client:
            resp = client.get(
                "/show", params={"project": "ghost-project", "key": "anything"}
            )
        body = resp.json()
        assert resp.status_code == 404
        assert "project_resolve.not_found" in body["stderr"], body
        assert body["stderr"] == cli.stderr, (
            "HTTP stderr must be byte-identical to the CLI's own stderr -- "
            "the API never reformats a script's refusal."
        )


# ---------------------------------------------------------------------------
# Acceptance criterion 4 -- endpoints call the shipped implementation
# ---------------------------------------------------------------------------


class TestEndpointsCallShippedImplementationNotAReDerivation:
    """Each endpoint's HTTP output changes when the underlying script's
    output changes, and matches a direct CLI invocation of that same
    script byte-for-byte against the identical fixture -- proof of 'one
    engine, two surfaces', never a Python re-derivation."""

    def test_verdicts_output_changes_with_task_state(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        proj = "proj-toggle"
        _write_projects_cfg(tmp_path, [(proj, 1)])
        project_dir = tmp_path / "projects" / proj
        (project_dir / "tasks").mkdir(parents=True, exist_ok=True)

        with _make_client(tmp_path, monkeypatch, in_process=True) as client:
            idle_resp = client.get("/verdicts", params={"project": proj})
        idle_verdict = idle_resp.json()["verdicts"][0]["verdict"]

        _write_task(project_dir, "CODER-20260101-003-fixture", "WORKING")

        with _make_client(tmp_path, monkeypatch, in_process=True) as client:
            running_resp = client.get("/verdicts", params={"project": proj})
        running_verdict = running_resp.json()["verdicts"][0]["verdict"]

        assert idle_verdict == "IDLE"
        assert running_verdict == "RUNNING"
        assert idle_verdict != running_verdict

    def test_show_queues_output_matches_cli_and_changes_with_fixture(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        proj = "proj-sq"
        _write_projects_cfg(tmp_path, [(proj, 1)])
        queues_dir = tmp_path / "projects" / proj / "tasks" / "queues"
        queues_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("TERM", "dumb")

        # Each phase gets its own PGAI_AGENT_KANBAN_TEMP_DIR so nothing a
        # surface might cache under the temp root in the "empty" phase can
        # be read back in the "populated" phase -- the comparison must see
        # each phase's own board.

        def _run_cli(temp_dir: pathlib.Path) -> str:
            env = {**_env_for(tmp_path), "TERM": "dumb", "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_dir)}
            result = subprocess.run(
                ["bash", str(_SHOW_QUEUES_SH), "--project", proj],
                capture_output=True, text=True, env=env, timeout=15,
            )
            assert result.returncode == 0, result.stderr
            return result.stdout

        def _run_http(temp_dir: pathlib.Path) -> str:
            monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(temp_dir))
            with _make_client(tmp_path, monkeypatch, in_process=True) as client:
                resp = client.get("/show-queues", params={"project": proj})
            assert resp.status_code == 200
            return resp.json()["stdout"]

        empty_temp = tmp_path / "snapshot_cache_empty"
        empty_cli = _run_cli(empty_temp)
        empty_http = _run_http(empty_temp)
        assert empty_http == empty_cli, "HTTP stdout must match CLI stdout byte-for-byte"

        (queues_dir / "coder_backlog.md").write_text(
            "- [A] CODER-20260101-004-fixture\n", encoding="utf-8"
        )
        _write_task(tmp_path / "projects" / proj, "CODER-20260101-004-fixture", "WORKING")

        populated_temp = tmp_path / "snapshot_cache_populated"
        populated_cli = _run_cli(populated_temp)
        populated_http = _run_http(populated_temp)
        assert _normalize_elapsed(populated_http) == _normalize_elapsed(populated_cli)
        assert populated_http != empty_http, (
            "GET /show-queues did not change when the underlying queue state "
            "changed -- suspect a cached or re-derived response."
        )

    def test_verify_rc_state_output_matches_cli_and_changes_with_fixture(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        proj = "verifyproj"
        repo = tmp_path / "repo"
        _init_local_repo(repo)
        kanban_root = _build_verify_rc_state_kanban_root(tmp_path, proj, repo)

        def _run_cli() -> tuple[int, str]:
            result = subprocess.run(
                ["bash", str(_VERIFY_RC_STATE_SH), proj, "--verbose"],
                capture_output=True, text=True, env=_env_for(kanban_root), timeout=30,
            )
            return result.returncode, result.stdout

        def _run_http() -> tuple[int, str]:
            with _make_client(kanban_root, monkeypatch, in_process=True) as client:
                resp = client.get(
                    "/verify-rc-state", params={"project": proj, "verbose": True}
                )
            body = resp.json()
            return body["exit_code"], body["stdout"]

        # Well-formed release-state.md -> 0 errors.
        _write_release_state(
            kanban_root / "projects" / proj,
            "# Release State\n\n## Active RC\nnone\n\n"
            "## RC Opened At\nnone\n\n## RC Opened By Task\nnone\n",
        )
        clean_cli = _run_cli()
        clean_http = _run_http()
        assert clean_cli[0] == 0
        assert clean_http == clean_cli

        # Malformed Active RC value -> at least one ERROR finding, different
        # exit code and different stdout.
        _write_release_state(
            kanban_root / "projects" / proj,
            "# Release State\n\n## Active RC\nNONE\n\n"
            "## RC Opened At\nnone\n\n## RC Opened By Task\nnone\n",
        )
        broken_cli = _run_cli()
        broken_http = _run_http()
        assert broken_cli[0] == 1
        assert broken_http == broken_cli
        assert broken_http[1] != clean_http[1], (
            "GET /verify-rc-state did not change when release-state.md "
            "changed -- suspect a cached or re-derived response."
        )

    def test_release_locks_output_matches_cli_and_changes_with_fixture(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        locks_dir = tmp_path / "locks"
        locks_dir.mkdir(parents=True, exist_ok=True)

        def _run_cli() -> str:
            result = subprocess.run(
                ["bash", str(_RELEASE_LOCKS_SH)],
                capture_output=True, text=True, env=_env_for(tmp_path), timeout=15,
            )
            assert result.returncode == 0, result.stderr
            return result.stdout

        def _run_http() -> str:
            with _make_client(tmp_path, monkeypatch, in_process=True) as client:
                resp = client.get("/release-locks")
            assert resp.status_code == 200
            return resp.json()["stdout"]

        empty_cli = _run_cli()
        empty_http = _run_http()
        assert empty_http == empty_cli
        assert "no lock files found" in empty_http

        # Write a lock file with provenance -- output must change and match.
        lock_file = locks_dir / "fixture-lock.lock"
        lock_file.write_text(
            "holder=fixture-holder\npid=999999\nop=test\ntask=NONE\n", encoding="utf-8"
        )

        populated_cli = _run_cli()
        populated_http = _run_http()
        assert populated_http == populated_cli
        assert populated_http != empty_http
        assert "fixture-lock" in populated_http


# ---------------------------------------------------------------------------
# Acceptance criterion 5 -- manifest absent-twin markers flipped
# ---------------------------------------------------------------------------


class TestManifestAbsentTwinMarkersFlippedForFiveVerbs:
    """The operator verb manifest carries real REST columns (never 'ABSENT')
    for why, show, show-queues, verify-rc-state, and release-locks.

    release-locks' row names the verb's mutating mode (POST
    /operations/release-locks with a body model); the read-only listing
    mode this file exercises, GET /release-locks, is the verb's second
    route, registered in the census-exempt sidecar rather than as a second
    row -- the manifest carries one REST twin per verb.
    """

    @pytest.mark.parametrize(
        "verb_name, expected_method, expected_route, expected_body_model",
        [
            ("why", "GET", "/verdicts", "NONE"),
            ("show", "GET", "/show", "NONE"),
            ("show-queues", "GET", "/show-queues", "NONE"),
            ("verify-rc-state", "GET", "/verify-rc-state", "NONE"),
            ("release-locks", "POST", "/operations/release-locks", "ReleaseLocksBody"),
        ],
    )
    def test_verb_rest_columns_are_not_absent(
        self,
        verb_name: str,
        expected_method: str,
        expected_route: str,
        expected_body_model: str,
    ) -> None:
        from pgai_agent_kanban.lib.operator_verb_manifest import load_manifest

        verbs = load_manifest()
        verb = verbs[verb_name]
        assert verb.rest_method == expected_method, verb
        assert verb.rest_route == expected_route, verb
        assert verb.rest_body_model == expected_body_model, verb
