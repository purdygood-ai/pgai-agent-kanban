"""
test_operations_action_twins.py — Request tests for the action twins that
surface a script's refusals as JSON error/message/remedy. Four routes are
exercised in depth:

  POST /operations/cold-build-now   → cold-build-now.sh --project <name> (detached)
  POST /operations/cancel-rc        → cm.cancel.cancel_rc (CLI: cm/cancel-rc.sh
                                      --project <name> --key vX.Y.Z --yes [--force])
  POST /operations/release-locks    → ops.locks.release_lock (CLI: release-locks.sh
                                      --release <name> [--force])
  POST /operations/halt-after       → halt-after.sh --project <name> [--key <token>] [--until vX.Y.Z]
  POST /operations/remove-project   → ops.projects.remove_project (CLI: remove-project.sh
                                      --project <name> [--force])
  POST /operations/unwind-rc        → cm.unwind.unwind_rc (CLI: unwind-rc.sh
                                      --project <name> --key vX.Y.Z --confirm [--force])

and section 7 proves the same refusal shape over EVERY action route: a body
missing a required field refuses 422 with top-level error/message/remedy
(never the framework's ``detail`` list) and spawns nothing; a script that
refuses dual-channel has its triple lifted to the top level at 500; a script
that refuses with plain stderr has that text lifted as ``message`` under the
route's ``<verb>.refused`` fallback key with a runnable CLI remedy; and every
action census row declares DualChannelShellEnvelope.

Acceptance criteria exercised, one success and one refusal per route:

  1. cold-build-now returns promptly with a "started" narration (never a
     verdict) while the backgrounded run still writes a real ledger record;
     a missing project is refused 422 with error/message/remedy and no
     subprocess is spawned.
  2. cancel-rc hands ``project``/``key`` to cm.cancel.cancel_rc confirmed,
     with ``force`` as its own separate argument; a refusal (plain stderr,
     no house-shape block) is returned 500 with the CLI's text as
     ``message`` under the route's stable fallback key and stderr
     byte-identical to the CLI's; a request without ``yes`` -- ``force``
     included -- is refused 422 at the API layer before cancel_rc runs.
  3. release-locks releases a stale lock exactly as the CLI does; the
     shared function's dual-channel refusals (not_found, holder_alive) reach
     the caller with the same machine key, message and remedy the CLI
     prints, and ``force`` clears the live-holder refusal.
  4. Every new route carries exactly the dependency set the existing action
     routes carry (loopback-only access, no auth dependency to add or omit),
     so an unauthenticated call is treated identically on all of them.
  5. The census rows for the three verbs carry real REST columns and the
     DualChannelShellEnvelope schema; no active action verb is ABSENT.
  6. halt-after accepts the CLI's minimal parameter set: a body with
     ``project`` and ``until`` and no ``key`` arms the sentinel byte-identical
     to ``halt-after.sh --project P --until vX.Y.Z`` (the CLI's ``rc``
     default applies when ``key`` is absent; a given ``key`` is armed
     verbatim); a malformed ``until`` without ``key`` is refused with the
     script's own ``halt-after.until_malformed`` block, byte-identical; a
     body with no ``project`` is refused 422 with error/message/remedy and
     never the framework's ``detail`` list.
  7. remove-project follows the CLI's safety ladder: the default call
     runs the safe unregister-only mode (directory kept); ``force`` is the
     destructive escalation and is refused 422 before anything runs unless
     ``confirm`` equals ``remove-project <project>`` byte-for-byte; a
     ``dry_run`` preview needs neither. The Active-RC guard refuses both
     modes dual-channel (``remove_project.active_rc``, exit 2) with the
     CLI's own stderr, and changes nothing.
  8. unwind-rc separates confirmation from force: ``confirm`` true (or
     the typed phrase ``unwind-rc <project> <key>``) answers the CLI's
     prompt and reaches cm.unwind.unwind_rc as ``confirm=True`` (the CLI's
     ``--confirm``, which it proves satisfies the prompt over a closed
     stdin); ``force`` alone, or ``confirm`` false, is refused 422 before
     unwind_rc runs; ``force`` with ``confirm`` reaches unwind_rc as its own
     argument and bypasses only the Active-RC mismatch check, which a
     confirmed non-force call still hits; ``dry_run`` runs nothing.

Design notes:
  - The API is exercised via FastAPI TestClient. For a route that shells to
    its script (``client``), scripts resolve to the real team/ dev tree while
    every script reads/writes state under a tmp_path sandbox root
    (PGAI_AGENT_KANBAN_ROOT_PATH), the same split the rest of this suite
    uses. A route that calls its ``pgai_agent_kanban.ops`` function in
    process (halt, unhalt, halt-after, defer, undefer, set-version-ceiling,
    switch-provider, add-project, remove-project) acts on
    ``ApiConfig.kanban_root``, so its tests use ``ops_client``, whose config
    names the sandbox itself.
  - cancel-rc's success path replaces cm.cancel.cancel_rc: the real cancel
    needs a git checkout and a project.cfg with a dev tree (the integration
    suite's tests/integration/test_cm_cancel_rc.py runs it end to end on a
    sandbox repository). Its refusal path runs the real function and the
    real script, which both stop before any git work.
  - unwind-rc's argument-passing tests replace cm.unwind.unwind_rc; its
    confirmed paths run the real function (and the real script, for the
    CLI side) against a sandbox whose dev tree is a throwaway ``git init``
    repository with no branches: every pre-flight check and the
    confirmation prompt are exercised for real, the backup step writes
    under the sandbox temp root, and the git unwind step then fails on the
    empty repository -- so the evidence a test reads is what was printed on
    the way past the prompt, never a clean exit. The full unwind runs end
    to end in tests/integration/test_cm_unwind_rc.py.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import time
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# This file lives at team/pgai_agent_kanban/api/tests/; four levels up is team/.
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent.parent
_REPO_ROOT = _TEAM_DIR.parent
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_CANCEL_RC_SH = _SCRIPTS_DIR / "cm" / "cancel-rc.sh"
_RELEASE_LOCKS_SH = _SCRIPTS_DIR / "release-locks.sh"
_HALT_AFTER_SH = _SCRIPTS_DIR / "halt-after.sh"
_REMOVE_PROJECT_SH = _SCRIPTS_DIR / "remove-project.sh"
_UNWIND_RC_SH = _SCRIPTS_DIR / "unwind-rc.sh"

_team_str = str(_TEAM_DIR)
if _team_str not in sys.path:
    sys.path.insert(0, _team_str)

from pgai_agent_kanban.lib.error_contract import parse_error_contract  # noqa: E402
from pgai_agent_kanban.ops import projects as _ops_projects  # noqa: E402
from tests.fixtures.binary_guard import require_binary  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _register_project(root: pathlib.Path, name: str) -> pathlib.Path:
    """Register *name* in a sandbox root's projects.cfg and create its directory."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "projects.cfg").write_text(
        f"[project:{name}]\npriority=1\n\n", encoding="utf-8"
    )
    project_dir = root / "projects" / name
    project_dir.mkdir(parents=True, exist_ok=True)
    return project_dir


def _write_lock(root: pathlib.Path, name: str, pid: int) -> pathlib.Path:
    """Write a lock file carrying the four-field provenance record lock_provenance.sh reads."""
    locks = root / "locks"
    locks.mkdir(parents=True, exist_ok=True)
    lock = locks / f"{name}.lock"
    lock.write_text(
        f"holder=test-holder\npid={pid}\nop=wake\ntask=none\n", encoding="utf-8"
    )
    return lock


def _dead_pid() -> int:
    """Return the pid of a process that has already exited (so kill -0 fails)."""
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def _cli_env(root: pathlib.Path) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PGAI_PROJECT_NAME", None)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
    env["KANBAN_ROOT"] = str(root)
    return env


def _run_cli(script: pathlib.Path, args: list[str], root: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(script), *args],
        capture_output=True,
        text=True,
        env=_cli_env(root),
        timeout=30,
        stdin=subprocess.DEVNULL,
    )


@pytest.fixture
def sandbox_root(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """A throwaway kanban root every script under test reads and writes."""
    root = tmp_path / "kanban_root"
    root.mkdir()
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))
    monkeypatch.setenv("KANBAN_ROOT", str(root))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(tmp_path / "tmp"))
    monkeypatch.delenv("PGAI_PROJECT_NAME", raising=False)
    return root


@pytest.fixture
def client(sandbox_root: pathlib.Path) -> Generator[TestClient, None, None]:
    """A TestClient whose scripts resolve to the real team/ dev tree."""
    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    cfg = ApiConfig(host="127.0.0.1", port=0, kanban_root=_TEAM_DIR)
    with TestClient(create_app(cfg=cfg), raise_server_exceptions=True) as c:
        yield c


@pytest.fixture
def ops_client(sandbox_root: pathlib.Path) -> Generator[TestClient, None, None]:
    """A TestClient rooted at the sandbox, for routes that call an ops function in process."""
    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    cfg = ApiConfig(host="127.0.0.1", port=0, kanban_root=sandbox_root)
    with TestClient(create_app(cfg=cfg), raise_server_exceptions=True) as c:
        yield c


# Routes that call their pgai_agent_kanban.ops function in process.
_OPS_ROUTES = frozenset({
    "/operations/halt", "/operations/unhalt", "/operations/halt-after",
    "/operations/defer", "/operations/undefer",
    "/operations/set-version-ceiling", "/operations/switch-provider",
    "/operations/create-project", "/operations/add-project", "/operations/remove-project",
    "/operations/release-locks", "/operations/cancel-rc", "/operations/unwind-rc",
})


_REFUSAL_KEYS = {"error", "message", "remedy"}


def _assert_dual_channel(body: dict) -> None:
    """Assert *body* carries non-empty error/message/remedy keys."""
    missing = _REFUSAL_KEYS - set(body)
    assert not missing, f"refusal body is missing {sorted(missing)}: {body!r}"
    for key in _REFUSAL_KEYS:
        assert isinstance(body[key], str) and body[key].strip(), (
            f"refusal body key {key!r} must be a non-empty string: {body!r}"
        )


# ---------------------------------------------------------------------------
# 1. POST /operations/cold-build-now
# ---------------------------------------------------------------------------


_STUB_COLD_LANE = """\
#!/usr/bin/env bash
sleep 3
echo "[cold-build] [docker] [claude-code] Image built successfully: tag:test"
echo "Cold-build lane PASSED"
exit 0
"""


class TestColdBuildNow:
    def test_returns_started_promptly_and_backgrounded_run_writes_the_ledger(
        self,
        client: TestClient,
        sandbox_root: pathlib.Path,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _register_project(sandbox_root, "proj")
        stub = tmp_path / "slow_cold_lane.sh"
        stub.write_text(_STUB_COLD_LANE, encoding="utf-8")
        stub.chmod(0o755)
        monkeypatch.setenv("PGAI_DEEP_VERIFY_COLD_LANE_CMD", str(stub))
        # The stubbed lane stands in for the container tier, which runs only
        # with operator authorization and never inside an agent wake.
        monkeypatch.setenv("PGAI_CONTAINER_TIER", "1")
        monkeypatch.delenv("PGAI_AGENT_WAKE_ACTIVE", raising=False)
        monkeypatch.setenv("PGAI_DEV_TREE_PATH", str(_REPO_ROOT))
        # The REST twin passes no --image, so the operator's kanban.cfg
        # default is what selects the images the backgrounded run builds.
        with (sandbox_root / "kanban.cfg").open("a", encoding="utf-8") as cfg:
            cfg.write("\n[release]\ncontainer_images = all\n")

        started = time.monotonic()
        resp = client.post("/operations/cold-build-now", json={"project": "proj"})
        elapsed = time.monotonic() - started

        assert resp.status_code == 200, resp.json()
        body = resp.json()
        assert elapsed < 2.0, (
            f"POST /operations/cold-build-now took {elapsed:.2f}s; the stubbed "
            "lane sleeps 3s, so the route must have blocked on it."
        )
        assert set(body) == {"exit_code", "stdout", "stderr", "warnings"}, body
        assert body["exit_code"] == 0
        assert "started for proj" in body["stdout"], body
        assert "no verdict" in body["stdout"], body
        assert "Verdict: built" not in body["stdout"], body

        ledger = sandbox_root / "projects" / "proj" / "deep-verify-ledger.md"
        deadline = time.monotonic() + 15
        text = ""
        while time.monotonic() < deadline:
            if ledger.exists():
                text = ledger.read_text(encoding="utf-8")
                if "Verdict: built" in text:
                    break
            time.sleep(0.2)
        assert "Verdict: built" in text, (
            f"backgrounded cold-build-now run never wrote a built verdict to "
            f"{ledger}; ledger: {text!r}"
        )

    def test_missing_project_is_refused_dual_channel_and_spawns_nothing(
        self, client: TestClient
    ) -> None:
        with patch("subprocess.Popen") as mock_popen, patch("subprocess.run") as mock_run:
            resp = client.post("/operations/cold-build-now", json={})
            mock_popen.assert_not_called()
            mock_run.assert_not_called()

        assert resp.status_code == 422, resp.text
        body = resp.json()
        _assert_dual_channel(body)
        assert body["error"] == "cold_build_now.project_required"
        assert "project" in body["message"]
        assert body["remedy"].startswith("curl "), body["remedy"]
        assert "/operations/cold-build-now" in body["remedy"]
        assert body["warnings"] == []

    def test_dry_run_short_circuits_without_a_subprocess(self, client: TestClient) -> None:
        with patch("subprocess.Popen") as mock_popen:
            resp = client.post(
                "/operations/cold-build-now", json={"project": "proj", "dry_run": True}
            )
            mock_popen.assert_not_called()
        assert resp.status_code == 200
        assert "dry-run: would execute cold-build-now.sh --project proj" in resp.json()["stdout"]


# ---------------------------------------------------------------------------
# 2. POST /operations/cancel-rc
# ---------------------------------------------------------------------------


class TestCancelRc:
    @staticmethod
    def _replace_cancel_rc(monkeypatch: pytest.MonkeyPatch) -> list:
        """Swap cm.cancel.cancel_rc for a recorder that narrates one line."""
        from pgai_agent_kanban.cm import cancel as cm_cancel

        calls: list = []

        def _fake(ctx, project, version, **kwargs):
            calls.append((ctx, project, version, kwargs))
            kwargs["out"]("RC cancellation complete.\n")
            return cm_cancel.CancelOutcome(f"rc/{version}", "cancelled")

        monkeypatch.setattr(cm_cancel, "cancel_rc", _fake)
        return calls

    def test_success_hands_project_key_and_confirmation_to_cancel_rc(
        self, ops_client: TestClient, sandbox_root: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._replace_cancel_rc(monkeypatch)
        with patch("subprocess.run") as mock_run:
            resp = ops_client.post(
                "/operations/cancel-rc",
                json={"project": "proj", "key": "v1.2.0", "yes": True},
            )
            mock_run.assert_not_called()

        assert len(calls) == 1, calls
        ctx, project, version, kwargs = calls[0]
        assert ctx.kanban_root == sandbox_root.resolve()
        assert (project, version) == ("proj", "v1.2.0")
        assert kwargs["yes"] is True and kwargs["force"] is False, kwargs
        assert resp.status_code == 200, resp.json()
        body = resp.json()
        assert set(body) == {"exit_code", "stdout", "stderr", "warnings"}, body
        assert body["exit_code"] == 0
        assert body["stdout"] == "RC cancellation complete.\n"

    def test_force_reaches_cancel_rc_as_its_own_argument(
        self, ops_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._replace_cancel_rc(monkeypatch)
        resp = ops_client.post(
            "/operations/cancel-rc",
            json={"project": "proj", "key": "v1.2.0", "yes": True, "force": True},
        )
        assert resp.status_code == 200, resp.json()
        assert calls[0][3]["yes"] is True and calls[0][3]["force"] is True, calls

    def test_force_without_yes_is_refused_before_cancel_rc_runs(
        self, ops_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._replace_cancel_rc(monkeypatch)
        resp = ops_client.post(
            "/operations/cancel-rc", json={"project": "proj", "key": "v1.2.0", "force": True}
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"] == "cancel_rc.confirmation_required"
        assert calls == [], "force is not a confirmation; nothing may run without yes"

    def test_script_refusal_returns_its_stderr_as_dual_channel_json(
        self, ops_client: TestClient, sandbox_root: pathlib.Path
    ) -> None:
        # A registered project with no project.cfg: cancel_rc refuses while
        # resolving the dev tree, before any git or release-state work -- a
        # real refusal, printed as plain stderr lines, on both surfaces.
        client = ops_client
        _register_project(sandbox_root, "proj")
        cli = _run_cli(_CANCEL_RC_SH, ["--project", "proj", "--key", "v1.2.0", "--yes"], sandbox_root)
        assert cli.returncode != 0, cli.stdout + cli.stderr
        assert parse_error_contract(cli.stderr) is None, (
            "this scenario relies on cancel-rc refusing with plain stderr; "
            f"it emitted a house-shape block instead:\n{cli.stderr}"
        )

        resp = client.post(
            "/operations/cancel-rc", json={"project": "proj", "key": "v1.2.0", "yes": True}
        )
        assert resp.status_code == 500, resp.json()
        body = resp.json()
        _assert_dual_channel(body)
        assert body["exit_code"] == cli.returncode
        assert body["stderr"] == cli.stderr, (
            "HTTP stderr must be byte-identical to the CLI's own stderr for "
            "the same arguments."
        )
        assert body["error"] == "cancel_rc.refused"
        assert body["message"] == cli.stderr.strip()
        assert "cancel-rc.sh --project proj --key v1.2.0" in body["remedy"]
        assert "--yes" not in body["remedy"], (
            "the fallback remedy must point at the prompting CLI form, never "
            "a pre-confirmed one"
        )

    def test_missing_yes_is_refused_before_anything_runs(
        self, ops_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._replace_cancel_rc(monkeypatch)
        with patch("subprocess.run") as mock_run:
            resp = ops_client.post("/operations/cancel-rc", json={"project": "proj", "key": "v1.2.0"})
            mock_run.assert_not_called()
        assert calls == []
        assert resp.status_code == 422, resp.text
        body = resp.json()
        _assert_dual_channel(body)
        assert body["error"] == "cancel_rc.confirmation_required"
        assert "v1.2.0" in body["message"] and "proj" in body["message"]
        assert '"dry_run": true' in body["remedy"], body["remedy"]
        assert "/operations/cancel-rc" in body["remedy"]

    def test_dry_run_previews_without_yes(self, client: TestClient) -> None:
        with patch("subprocess.run") as mock_run:
            resp = client.post(
                "/operations/cancel-rc",
                json={"project": "proj", "key": "v1.2.0", "dry_run": True},
            )
            mock_run.assert_not_called()
        assert resp.status_code == 200
        assert (
            "dry-run: would execute cancel-rc.sh --project proj --key v1.2.0"
            in resp.json()["stdout"]
        )

    def test_dry_run_preview_names_force(self, client: TestClient) -> None:
        with patch("subprocess.run") as mock_run:
            resp = client.post(
                "/operations/cancel-rc",
                json={"project": "proj", "key": "v1.2.0", "force": True, "dry_run": True},
            )
            mock_run.assert_not_called()
        assert resp.status_code == 200
        assert "cancel-rc.sh --project proj --key v1.2.0 --force" in resp.json()["stdout"]

    def test_missing_key_is_refused_dual_channel(self, client: TestClient) -> None:
        with patch("subprocess.run") as mock_run:
            resp = client.post("/operations/cancel-rc", json={"project": "proj", "yes": True})
            mock_run.assert_not_called()
        assert resp.status_code == 422
        body = resp.json()
        _assert_dual_channel(body)
        assert body["error"] == "cancel_rc.required_field_missing"
        assert "'key'" in body["message"]


# ---------------------------------------------------------------------------
# 3. POST /operations/release-locks
# ---------------------------------------------------------------------------


class TestReleaseLocks:
    def test_releases_a_stale_lock_like_the_cli(
        self, ops_client: TestClient, sandbox_root: pathlib.Path, tmp_path: pathlib.Path
    ) -> None:
        dead = _dead_pid()
        cli_root = tmp_path / "cli_root"
        _write_lock(cli_root, "repo-wake-proj", dead)
        cli = _run_cli(_RELEASE_LOCKS_SH, ["--release", "repo-wake-proj"], cli_root)
        assert cli.returncode == 0, cli.stdout + cli.stderr

        http_lock = _write_lock(sandbox_root, "repo-wake-proj", dead)
        resp = ops_client.post("/operations/release-locks", json={"release": "repo-wake-proj"})
        assert resp.status_code == 200, resp.json()
        body = resp.json()
        assert set(body) == {"exit_code", "stdout", "stderr", "warnings"}, body
        assert body["exit_code"] == 0
        assert "released stale lock repo-wake-proj" in body["stdout"], body
        # Same wording as the CLI, modulo the sandbox path each run named.
        assert body["stdout"].replace(str(sandbox_root), "ROOT") == cli.stdout.replace(
            str(cli_root), "ROOT"
        )
        assert http_lock.read_text(encoding="utf-8") == "", (
            "release must swap the lock file for a fresh, empty one"
        )

    def test_not_found_refusal_lifts_the_scripts_dual_channel_block(
        self, ops_client: TestClient, sandbox_root: pathlib.Path
    ) -> None:
        (sandbox_root / "locks").mkdir()
        cli = _run_cli(_RELEASE_LOCKS_SH, ["--release", "no-such-lock"], sandbox_root)
        assert cli.returncode == 3, cli.stdout + cli.stderr
        cli_triple = parse_error_contract(cli.stderr)
        assert cli_triple is not None, cli.stderr

        resp = ops_client.post("/operations/release-locks", json={"release": "no-such-lock"})
        assert resp.status_code == 404, resp.json()
        body = resp.json()
        _assert_dual_channel(body)
        assert body["exit_code"] == 3
        assert body["stderr"] == cli.stderr, "HTTP stderr must match the CLI byte-for-byte"
        assert body["error"] == "release_locks.not_found"
        assert body["error"] == cli_triple["error"]
        assert body["message"] == cli_triple["message"]
        assert body["remedy"] == cli_triple["remedy"]

    def test_live_holder_is_refused_until_force_is_given(
        self, ops_client: TestClient, sandbox_root: pathlib.Path
    ) -> None:
        lock = _write_lock(sandbox_root, "repo-wake-proj", os.getpid())

        refused = ops_client.post("/operations/release-locks", json={"release": "repo-wake-proj"})
        assert refused.status_code == 409, refused.json()
        body = refused.json()
        _assert_dual_channel(body)
        assert body["exit_code"] == 4
        assert body["error"] == "release_locks.holder_alive"
        assert "--force" in body["remedy"]
        assert "pid=" in lock.read_text(encoding="utf-8"), "a refused release must change nothing"

    def test_force_signals_a_live_holder_instead_of_swapping_its_file(
        self, ops_client: TestClient, sandbox_root: pathlib.Path
    ) -> None:
        """``force: true`` sends the recorded holder SIGTERM and waits for its
        own release path to let go; the file is never replaced. The holder is
        a real subprocess (never this test process's own pid, which the
        signal would end) that acquired the lock through lock_provenance.sh,
        so its exit trap performs the release."""
        lock = sandbox_root / "locks" / "repo-wake-proj.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        ready = sandbox_root / "holder-ready"
        holder_script = (
            f'source "{_SCRIPTS_DIR}/lib/lock_provenance.sh"\n'
            "trap 'exit 0' TERM\n"
            'fd=""\n'
            f'pgai_lock_acquire "{lock}" test-holder "$$" wake none fd || exit 97\n'
            f'touch "{ready}"\n'
            "while true; do sleep 0.1; done\n"
        )
        holder = subprocess.Popen(["bash", "-c", holder_script], cwd=str(_TEAM_DIR))
        try:
            deadline = time.time() + 5
            while not ready.exists() and time.time() < deadline:
                time.sleep(0.05)
            assert ready.exists(), "the holder never acquired the lock"
            forced = ops_client.post(
                "/operations/release-locks", json={"release": "repo-wake-proj", "force": True}
            )
            assert forced.status_code == 200, forced.json()
            assert f"sent SIGTERM to live holder pid {holder.pid}" in forced.json()["stdout"]
            holder.wait(timeout=5)
            assert not lock.exists(), (
                "the holder's own release removes the file; the verb must never swap it"
            )
        finally:
            if holder.poll() is None:
                holder.kill()
                holder.wait(timeout=5)

    def test_missing_release_is_refused_dual_channel_and_spawns_nothing(
        self, client: TestClient
    ) -> None:
        with patch("subprocess.run") as mock_run:
            resp = client.post("/operations/release-locks", json={})
            mock_run.assert_not_called()
        assert resp.status_code == 422
        body = resp.json()
        _assert_dual_channel(body)
        assert body["error"] == "release_locks.release_required"
        assert "GET /release-locks" in body["message"]

    def test_unknown_body_field_warns_and_still_executes(
        self, ops_client: TestClient, sandbox_root: pathlib.Path
    ) -> None:
        _write_lock(sandbox_root, "repo-wake-proj", _dead_pid())
        resp = ops_client.post(
            "/operations/release-locks", json={"release": "repo-wake-proj", "project": "x"}
        )
        assert resp.status_code == 200, resp.json()
        assert resp.json()["warnings"] == ["unknown field: project"]

    def test_listing_get_still_refuses_release_naming_the_post_twin(
        self, client: TestClient
    ) -> None:
        resp = client.get("/release-locks", params={"release": "repo-wake-proj"})
        assert resp.status_code == 422
        assert "POST /operations/release-locks" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 6. POST /operations/halt-after -- the CLI's minimal parameter set
# ---------------------------------------------------------------------------


class TestHaltAfterMinimalParameters:
    def test_project_and_until_without_key_arm_the_same_sentinel_as_the_cli(
        self, ops_client: TestClient, sandbox_root: pathlib.Path, tmp_path: pathlib.Path
    ) -> None:
        client = ops_client
        cli_root = tmp_path / "cli_root"
        _register_project(cli_root, "proj")
        cli = _run_cli(_HALT_AFTER_SH, ["--project", "proj", "--until", "v99.0.0"], cli_root)
        assert cli.returncode == 0, cli.stdout + cli.stderr
        cli_sentinel = cli_root / "projects" / "proj" / "HALT-AFTER"
        assert cli_sentinel.is_file(), "the CLI did not arm HALT-AFTER"

        _register_project(sandbox_root, "proj")
        resp = client.post("/operations/halt-after", json={"project": "proj", "until": "v99.0.0"})
        assert resp.status_code == 200, resp.json()
        body = resp.json()
        assert set(body) == {"exit_code", "stdout", "stderr", "warnings"}, body
        assert body["exit_code"] == 0
        assert body["warnings"] == [], "key is a declared field; omitting it is not an unknown-field warning"

        rest_sentinel = sandbox_root / "projects" / "proj" / "HALT-AFTER"
        assert rest_sentinel.is_file(), "the route answered 200 but armed no HALT-AFTER"
        assert rest_sentinel.read_bytes() == cli_sentinel.read_bytes(), (
            f"HALT-AFTER content differs: CLI={cli_sentinel.read_bytes()!r} "
            f"REST={rest_sentinel.read_bytes()!r}"
        )
        assert rest_sentinel.read_bytes() == b"rc:v99.0.0\n"

    def test_absent_key_arms_the_cli_default_and_a_given_key_is_armed_verbatim(
        self, ops_client: TestClient, sandbox_root: pathlib.Path
    ) -> None:
        from pgai_agent_kanban.ops import write as ops_write

        _register_project(sandbox_root, "proj")
        armed: list[str] = []
        with patch.object(
            ops_write, "halt_after", side_effect=lambda ctx, project, token: armed.append(token)
        ):
            ops_client.post("/operations/halt-after", json={"project": "proj", "until": "v99.0.0"})
            ops_client.post("/operations/halt-after", json={"project": "proj", "until": "99.0.0"})
            ops_client.post("/operations/halt-after", json={"project": "proj", "key": "coder"})
            ops_client.post("/operations/halt-after", json={"project": "proj"})

        assert armed == ["rc:v99.0.0", "rc:v99.0.0", "coder", "rc"], (
            "an absent key arms the CLI's rc default; until arms rc:vX.Y.Z with the "
            "leading v normalised; a given key is armed verbatim"
        )

    def test_malformed_until_without_key_is_the_scripts_own_refusal_and_writes_nothing(
        self, ops_client: TestClient, sandbox_root: pathlib.Path
    ) -> None:
        client = ops_client
        _register_project(sandbox_root, "proj")
        cli = _run_cli(_HALT_AFTER_SH, ["--project", "proj", "--until", "not-a-version"], sandbox_root)
        assert cli.returncode != 0, cli.stdout
        cli_triple = parse_error_contract(cli.stderr)
        assert cli_triple is not None, cli.stderr

        resp = client.post(
            "/operations/halt-after", json={"project": "proj", "until": "not-a-version"}
        )
        assert resp.status_code == 422, resp.json()
        body = resp.json()
        _assert_dual_channel(body)
        assert body["error"] == "halt-after.until_malformed"
        assert body["error"] == cli_triple["error"]
        assert body["message"] == cli_triple["message"]
        assert body["remedy"] == cli_triple["remedy"]
        assert body["stderr"] == cli.stderr, "HTTP stderr must match the CLI byte-for-byte"
        assert "--key" not in body["remedy"], (
            "the fallback remedy must not name a --key the caller never sent"
        )
        assert not (sandbox_root / "projects" / "proj" / "HALT-AFTER").exists(), (
            "a refused arm must write no sentinel"
        )

    def test_missing_project_is_refused_dual_channel_and_spawns_nothing(
        self, client: TestClient
    ) -> None:
        with patch("subprocess.run") as mock_run:
            resp = client.post("/operations/halt-after", json={"until": "v99.0.0"})
            mock_run.assert_not_called()

        assert resp.status_code == 422, resp.text
        body = resp.json()
        _assert_dual_channel(body)
        assert "detail" not in body, f"framework validation list leaked into the refusal: {body}"
        assert body["error"] == "halt_after.project_required"
        assert "project" in body["message"]
        assert body["remedy"].startswith("curl "), body["remedy"]
        assert "/operations/halt-after" in body["remedy"]
        assert body["warnings"] == []


# ---------------------------------------------------------------------------
# 4. Authorization: identical to the existing action routes
# ---------------------------------------------------------------------------


_NEW_ROUTES = (
    "/operations/cold-build-now",
    "/operations/cancel-rc",
    "/operations/release-locks",
)


def _route_dependency_names(app, path: str) -> list[str]:
    for route in app.routes:
        if getattr(route, "path", None) == path and "POST" in getattr(route, "methods", set()):
            return sorted(dep.call.__name__ for dep in route.dependant.dependencies)
    raise AssertionError(f"no POST route registered at {path}")


class TestAuthorizationMatchesExistingActionRoutes:
    @pytest.mark.parametrize("path", _NEW_ROUTES)
    def test_dependency_set_equals_the_existing_action_routes(
        self, client: TestClient, path: str
    ) -> None:
        reference = _route_dependency_names(client.app, "/operations/halt")
        assert _route_dependency_names(client.app, path) == reference, (
            f"{path} must carry exactly the dependency set the existing action "
            "routes carry -- no second auth path, and none omitted"
        )

    @pytest.mark.parametrize("path", _NEW_ROUTES)
    def test_unauthenticated_call_is_treated_like_the_existing_routes(
        self, client: TestClient, path: str
    ) -> None:
        with patch("subprocess.run"), patch("subprocess.Popen"):
            reference = client.post("/operations/halt", json={})
            resp = client.post(path, json={})
        # The existing routes bind loopback-only and carry no credential
        # check; the new routes must reject (or accept) a credential-less
        # call exactly the same way -- never with an auth status of their own.
        assert reference.status_code not in (401, 403)
        assert resp.status_code not in (401, 403), resp.text


# ---------------------------------------------------------------------------
# 5. Census rows
# ---------------------------------------------------------------------------


class TestCensusRowsCarryTheRealTwins:
    @pytest.mark.parametrize(
        "verb_name, route, body_model",
        [
            ("cold-build-now", "/operations/cold-build-now", "ColdBuildNowBody"),
            ("cancel-rc", "/operations/cancel-rc", "CancelRcBody"),
            ("release-locks", "/operations/release-locks", "ReleaseLocksBody"),
        ],
    )
    def test_row_names_its_post_twin_and_schema(
        self, verb_name: str, route: str, body_model: str
    ) -> None:
        from pgai_agent_kanban.lib.operator_verb_manifest import load_manifest

        verb = load_manifest()[verb_name]
        assert verb.rest_method == "POST", verb
        assert verb.rest_route == route, verb
        assert verb.rest_body_model == body_model, verb
        assert verb.schema_ref == "DualChannelShellEnvelope", verb

    def test_no_active_action_verb_is_absent(self) -> None:
        from pgai_agent_kanban.lib.operator_verb_manifest import load_manifest

        absent = sorted(
            name
            for name, verb in load_manifest().items()
            if verb.status == "active"
            and verb.verb_class == "action"
            and "ABSENT" in (verb.rest_method, verb.rest_route, verb.schema_ref)
        )
        assert absent == [], f"active action verbs still without a REST twin: {absent}"


# ---------------------------------------------------------------------------
# 7. Every action route refuses dual-channel
# ---------------------------------------------------------------------------


# (route, body missing a required field or failing an API-layer gate, expected error key)
_MISSING_FIELD_REFUSALS = [
    ("/operations/halt", {}, "halt.project_required"),
    ("/operations/unhalt", {}, "unhalt.project_required"),
    ("/operations/halt-after", {"until": "v1.2.3"}, "halt_after.project_required"),
    ("/operations/defer", {"until": "v1.2.3"}, "defer.project_required"),
    ("/operations/undefer", {}, "undefer.project_required"),
    ("/operations/reset", {"key": "CODER-20260101-001-slug"}, "reset.project_required"),
    ("/operations/reset", {"project": "proj"}, "reset.selector_required"),
    ("/operations/reset", {"project": "proj", "key": "a", "bug": "BUG-0001"}, "reset.selector_conflict"),
    ("/operations/close", {"project": "proj"}, "close.key_required"),
    ("/operations/wontdo", {"key": "CODER-20260101-001-slug"}, "wontdo.project_required"),
    ("/operations/delete", {"project": "proj"}, "delete.key_required"),
    ("/operations/intake", {"project": "proj"}, "intake.filename_required"),
    ("/operations/intake", {"project": "proj", "filename": "BUG-0001-x.md"}, "intake.content_required"),
    ("/operations/unwind-rc", {"project": "proj"}, "unwind_rc.key_required"),
    ("/operations/unwind-rc", {"project": "proj", "key": "v1.2.3"}, "unwind_rc.confirm_required"),
    ("/operations/unwind-rc", {"project": "proj", "key": "v1.2.3", "force": True}, "unwind_rc.confirm_required"),
    ("/operations/unwind-rc", {"project": "proj", "key": "v1.2.3", "confirm": False}, "unwind_rc.confirm_required"),
    ("/operations/unwind-rc", {"project": "proj", "key": "v1.2.3", "confirm": "no"}, "unwind_rc.confirm_mismatch"),
    ("/operations/set-version-ceiling", {"show": True}, "set_version_ceiling.project_required"),
    ("/operations/switch-provider", {}, "switch_provider.provider_required"),
    ("/operations/create-project", {}, "create_project.project_required"),
    ("/operations/add-project", {}, "add_project.project_required"),
    ("/operations/remove-project", {}, "remove_project.project_required"),
    ("/operations/remove-project", {"project": "proj", "force": True}, "remove_project.confirm_required"),
    ("/operations/remove-project", {"project": "proj", "force": True, "confirm": "no"}, "remove_project.confirm_mismatch"),
    ("/operations/ship-rc", {"project": "proj", "key": "v1.2.3"}, "ship_rc.confirm_required"),
    ("/operations/ship-rc", {"project": "proj", "key": "v1.2.3", "confirm": "no"}, "ship_rc.confirm_mismatch"),
    ("/operations/deep-verify", {}, "deep_verify.project_required"),
    ("/operations/cold-build-now", {}, "cold_build_now.project_required"),
    ("/operations/cancel-rc", {"project": "proj", "yes": True}, "cancel_rc.required_field_missing"),
    ("/operations/release-locks", {}, "release_locks.release_required"),
]


class TestEveryActionRouteRefusesDualChannel:
    @pytest.mark.parametrize(
        "route, body, error_key",
        _MISSING_FIELD_REFUSALS,
        ids=[f"{r.rsplit('/', 1)[1]}:{k}" for r, _, k in _MISSING_FIELD_REFUSALS],
    )
    def test_api_layer_refusal_is_422_dual_channel_and_spawns_nothing(
        self, client: TestClient, route: str, body: dict, error_key: str
    ) -> None:
        with patch("subprocess.run") as mock_run, patch("subprocess.Popen") as mock_popen:
            resp = client.post(route, json=body)
            mock_run.assert_not_called()
            mock_popen.assert_not_called()

        assert resp.status_code == 422, resp.text
        payload = resp.json()
        _assert_dual_channel(payload)
        assert "detail" not in payload, f"framework validation list leaked into the refusal: {payload}"
        assert payload["error"] == error_key, payload
        assert payload["remedy"].startswith("curl "), payload["remedy"]
        assert route in payload["remedy"], payload["remedy"]
        assert "<" not in payload["remedy"], (
            f"remedy must be runnable as printed, with no placeholder: {payload['remedy']}"
        )
        assert payload["warnings"] == []

    def test_unknown_field_warning_survives_a_missing_field_refusal(
        self, client: TestClient
    ) -> None:
        with patch("subprocess.run") as mock_run:
            resp = client.post("/operations/halt", json={"projekt": "proj"})
            mock_run.assert_not_called()
        assert resp.status_code == 422
        payload = resp.json()
        assert payload["error"] == "halt.project_required"
        assert payload["warnings"] == ["unknown field: projekt — did you mean project? (operation executed)"]

    @pytest.mark.parametrize(
        "route, script, body",
        [
            ("/operations/halt", "halt.sh", {"project": "ghost-project"}),
            ("/operations/unhalt", "unhalt.sh", {"project": "ghost-project"}),
            ("/operations/undefer", "undefer.sh", {"project": "ghost-project"}),
            ("/operations/set-version-ceiling", "set-version-ceiling.sh", {"project": "ghost-project", "show": True}),
        ],
    )
    def test_script_dual_channel_refusal_is_lifted_to_top_level_keys(
        self,
        client: TestClient,
        ops_client: TestClient,
        sandbox_root: pathlib.Path,
        route: str,
        script: str,
        body: dict,
    ) -> None:
        if route in _OPS_ROUTES:
            client = ops_client
        _register_project(sandbox_root, "proj")
        cli_args = [f"--{k}" if v is True else f"--{k}={v}" for k, v in body.items()]
        cli_args = [a for pair in ((f"--{k}", v) for k, v in body.items()) for a in ((pair[0],) if pair[1] is True else pair)]
        cli = _run_cli(_SCRIPTS_DIR / script, cli_args, sandbox_root)
        assert cli.returncode != 0, cli.stdout + cli.stderr
        cli_triple = parse_error_contract(cli.stderr)
        assert cli_triple is not None, cli.stderr
        assert cli_triple["error"] == "project_resolve.not_found", cli_triple

        resp = client.post(route, json=body)
        assert resp.status_code == 404, resp.json()
        payload = resp.json()
        _assert_dual_channel(payload)
        assert set(payload) == {
            "exit_code", "stdout", "stderr", "error", "message", "remedy", "warnings"
        }, payload
        assert payload["exit_code"] == cli.returncode
        assert payload["stderr"] == cli.stderr, "HTTP stderr must match the CLI byte-for-byte"
        assert payload["error"] == cli_triple["error"]
        assert payload["message"] == cli_triple["message"]
        assert payload["remedy"] == cli_triple["remedy"]

    def test_wake_now_unknown_agent_refusal_is_lifted_to_top_level_keys(
        self, client: TestClient, sandbox_root: pathlib.Path
    ) -> None:
        cli = _run_cli(_SCRIPTS_DIR / "wake-now.sh", ["--agent", "nobody"], sandbox_root)
        assert cli.returncode != 0, cli.stdout + cli.stderr
        cli_triple = parse_error_contract(cli.stderr)
        assert cli_triple is not None and cli_triple["error"] == "wake_now.unknown_agent", cli.stderr

        resp = client.post("/operations/wake-now", json={"agent": "nobody"})
        assert resp.status_code == 500, resp.json()
        payload = resp.json()
        _assert_dual_channel(payload)
        assert payload["error"] == "wake_now.unknown_agent"
        assert payload["message"] == cli_triple["message"]
        assert payload["remedy"] == cli_triple["remedy"]

    def test_plain_stderr_refusal_is_lifted_under_the_fallback_key_with_a_cli_remedy(
        self, ops_client: TestClient, sandbox_root: pathlib.Path
    ) -> None:
        # add-project refuses a project with no directory in plain ERROR:
        # lines (no house-shape block); the CLI prints the same text.
        cli = _run_cli(_SCRIPTS_DIR / "add-project.sh", ["--project", "nobody"], sandbox_root)
        assert cli.returncode == 2, cli.stdout + cli.stderr
        assert parse_error_contract(cli.stderr) is None, cli.stderr

        resp = ops_client.post("/operations/add-project", json={"project": "nobody"})

        assert resp.status_code == 409, resp.json()
        payload = resp.json()
        _assert_dual_channel(payload)
        assert payload["exit_code"] == cli.returncode
        assert payload["stderr"] == cli.stderr, "HTTP stderr must match the CLI byte-for-byte"
        assert payload["error"] == "add_project.refused"
        assert payload["message"] == cli.stderr.strip()
        assert "project directory does not exist" in payload["message"]
        assert payload["remedy"].endswith("team/scripts/add-project.sh --project nobody"), (
            payload["remedy"]
        )
        assert "<" not in payload["remedy"]

    def test_success_body_is_the_plain_four_key_envelope(
        self, ops_client: TestClient, sandbox_root: pathlib.Path
    ) -> None:
        _register_project(sandbox_root, "proj")
        resp = ops_client.post("/operations/halt", json={"project": "proj"})
        assert resp.status_code == 200
        assert set(resp.json()) == {"exit_code", "stdout", "stderr", "warnings"}

    def test_every_action_census_row_declares_the_dual_channel_schema(self) -> None:
        from pgai_agent_kanban.lib.operator_verb_manifest import load_manifest

        plain = sorted(
            name
            for name, verb in load_manifest().items()
            if verb.status == "active"
            and verb.verb_class == "action"
            and verb.schema_ref != "DualChannelShellEnvelope"
        )
        assert plain == [], f"action rows not declaring DualChannelShellEnvelope: {plain}"


# ---------------------------------------------------------------------------
# 8. POST /operations/remove-project -- the script's safety ladder, not its inverse
# ---------------------------------------------------------------------------


def _diagnostic(body: dict) -> str:
    return f"--- stdout ---\n{body.get('stdout')}\n--- stderr ---\n{body.get('stderr')}"


def _write_active_rc(project_dir: pathlib.Path, version: str) -> None:
    (project_dir / "release-state.md").write_text(
        f"# Release State\n\n## Active RC\n{version}\n", encoding="utf-8"
    )


class TestRemoveProjectSafetyLadder:
    def test_default_call_unregisters_and_leaves_the_directory_in_place(
        self, ops_client: TestClient, sandbox_root: pathlib.Path
    ) -> None:
        project_dir = _register_project(sandbox_root, "proj")
        (project_dir / "README.md").write_text("keep me\n", encoding="utf-8")

        resp = ops_client.post("/operations/remove-project", json={"project": "proj"})

        body = resp.json()
        assert resp.status_code == 200, body
        assert body["exit_code"] == 0, _diagnostic(body)
        assert "[project:proj]" not in (sandbox_root / "projects.cfg").read_text(encoding="utf-8")
        assert (project_dir / "README.md").is_file(), (
            "the default call must be the safe mode: unregister only, directory untouched"
        )
        assert "directory will be left in place" in body["stdout"], _diagnostic(body)

    def test_default_call_passes_no_force(self, ops_client: TestClient) -> None:
        with patch.object(_ops_projects, "remove_project") as remove:
            resp = ops_client.post("/operations/remove-project", json={"project": "proj"})
        assert resp.status_code == 200, resp.json()
        remove.assert_called_once()
        assert remove.call_args.args[1:] == ("proj",)
        assert remove.call_args.kwargs == {"force": False}

    @pytest.mark.parametrize(
        "cli_args, rest_body",
        [
            (["--project", "proj"], {"project": "proj"}),
            (
                ["--project", "proj", "--force"],
                {"project": "proj", "force": True, "confirm": "remove-project proj"},
            ),
        ],
        ids=["safe", "force"],
    )
    def test_active_rc_refuses_both_modes_dual_channel_and_changes_nothing(
        self,
        ops_client: TestClient,
        sandbox_root: pathlib.Path,
        cli_args: list[str],
        rest_body: dict,
    ) -> None:
        project_dir = _register_project(sandbox_root, "proj")
        (project_dir / "README.md").write_text("keep me\n", encoding="utf-8")
        _write_active_rc(project_dir, "v1.2.3")
        registry = (sandbox_root / "projects.cfg").read_text(encoding="utf-8")

        cli = _run_cli(_REMOVE_PROJECT_SH, cli_args, sandbox_root)
        assert cli.returncode == 2, cli.stdout + cli.stderr
        cli_triple = parse_error_contract(cli.stderr)
        assert cli_triple is not None and cli_triple["error"] == "remove_project.active_rc", cli.stderr

        resp = ops_client.post("/operations/remove-project", json=rest_body)

        body = resp.json()
        assert resp.status_code == 409, body
        _assert_dual_channel(body)
        assert body["exit_code"] == cli.returncode
        assert body["stderr"] == cli.stderr, "HTTP stderr must be byte-identical to the CLI's"
        assert body["error"] == "remove_project.active_rc"
        assert body["message"] == cli_triple["message"]
        assert body["remedy"] == cli_triple["remedy"]
        assert "Active RC = v1.2.3" in body["message"]
        assert "with or without --force" in body["message"]
        assert "remove-project.sh --project proj" in body["remedy"]
        assert "<" not in body["remedy"]
        assert cli.stdout == "" and body["stdout"] == ""
        assert (sandbox_root / "projects.cfg").read_text(encoding="utf-8") == registry, (
            "a refused call must leave the registration in place"
        )
        assert (project_dir / "README.md").is_file(), "a refused call must leave the directory"

    def test_invalid_active_rc_field_is_refused_in_both_modes(
        self, ops_client: TestClient, sandbox_root: pathlib.Path
    ) -> None:
        project_dir = _register_project(sandbox_root, "proj")
        _write_active_rc(project_dir, "NONE")
        for body in (
            {"project": "proj"},
            {"project": "proj", "force": True, "confirm": "remove-project proj"},
        ):
            resp = ops_client.post("/operations/remove-project", json=body)
            payload = resp.json()
            assert resp.status_code == 422, payload
            assert payload["exit_code"] == 2
            assert payload["error"] == "remove_project.release_state_invalid", payload
            assert "Active RC='NONE'" in payload["message"]
        assert project_dir.is_dir()
        assert "[project:proj]" in (sandbox_root / "projects.cfg").read_text(encoding="utf-8")

    def test_force_without_confirm_is_refused_before_any_subprocess(
        self, client: TestClient
    ) -> None:
        with patch("subprocess.run") as mock_run:
            resp = client.post(
                "/operations/remove-project", json={"project": "proj", "force": True}
            )
            mock_run.assert_not_called()
        assert resp.status_code == 422, resp.text
        body = resp.json()
        _assert_dual_channel(body)
        assert "detail" not in body
        assert body["error"] == "remove_project.confirm_required"
        assert "'confirm'" in body["message"]
        assert '"confirm": "remove-project proj"' in body["remedy"], body["remedy"]
        assert '"force": true' in body["remedy"] and '"dry_run": true' in body["remedy"]
        assert "<" not in body["remedy"]

    @pytest.mark.parametrize(
        "wrong",
        [
            "remove-project proj ",
            " remove-project proj",
            "Remove-Project proj",
            "remove-project other",
            "remove-project",
            "yes",
        ],
        ids=["trailing-space", "leading-space", "case-folded", "other-project", "no-project", "yes"],
    )
    def test_force_with_wrong_confirm_is_refused_before_any_subprocess(
        self, client: TestClient, wrong: str
    ) -> None:
        with patch("subprocess.run") as mock_run:
            resp = client.post(
                "/operations/remove-project",
                json={"project": "proj", "force": True, "confirm": wrong},
            )
            mock_run.assert_not_called()
        assert resp.status_code == 422, resp.text
        body = resp.json()
        _assert_dual_channel(body)
        assert body["error"] == "remove_project.confirm_mismatch"
        assert "expected 'remove-project proj'" in body["message"]
        assert f"got '{wrong}'" in body["message"]
        assert '"confirm": "remove-project proj"' in body["remedy"], body["remedy"]

    def test_force_with_exact_confirm_passes_force_and_no_confirm(
        self, ops_client: TestClient
    ) -> None:
        with patch.object(_ops_projects, "remove_project") as remove:
            resp = ops_client.post(
                "/operations/remove-project",
                json={"project": "proj", "force": True, "confirm": "remove-project proj"},
            )
        assert resp.status_code == 200, resp.json()
        remove.assert_called_once()
        assert remove.call_args.kwargs == {"force": True}, (
            "confirm is an API-layer gate; remove_project takes no such argument"
        )

    def test_force_with_exact_confirm_runs_the_destructive_mode(
        self, ops_client: TestClient, sandbox_root: pathlib.Path
    ) -> None:
        project_dir = _register_project(sandbox_root, "proj")
        (project_dir / "README.md").write_text("gone\n", encoding="utf-8")
        _write_active_rc(project_dir, "none")

        resp = ops_client.post(
            "/operations/remove-project",
            json={"project": "proj", "force": True, "confirm": "remove-project proj"},
        )

        body = resp.json()
        assert resp.status_code == 200, body
        assert body["exit_code"] == 0, _diagnostic(body)
        assert not project_dir.exists(), "force must delete the project directory"
        assert "[project:proj]" not in (sandbox_root / "projects.cfg").read_text(encoding="utf-8")
        assert f"+ deleted: {project_dir}" in body["stdout"], _diagnostic(body)

    @pytest.mark.parametrize(
        "body, expected_tail",
        [
            ({"project": "proj"}, "remove-project.sh --project proj"),
            ({"project": "proj", "force": True}, "remove-project.sh --project proj --force"),
        ],
        ids=["safe", "force"],
    )
    def test_dry_run_previews_without_confirm_and_spawns_nothing(
        self, client: TestClient, body: dict, expected_tail: str
    ) -> None:
        with patch("subprocess.run") as mock_run:
            resp = client.post("/operations/remove-project", json={**body, "dry_run": True})
            mock_run.assert_not_called()
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["exit_code"] == 0
        assert payload["stdout"] == f"dry-run: would execute {expected_tail}", payload


# ---------------------------------------------------------------------------
# 9. POST /operations/unwind-rc -- confirm answers the prompt, force skips one check
# ---------------------------------------------------------------------------


def _unwind_sandbox(root: pathlib.Path, name: str, active_rc: str) -> pathlib.Path:
    """A registered, halted project whose dev tree is an empty throwaway git
    repository, with *active_rc* recorded in release-state.md -- everything
    unwind-rc.sh's pre-flight reads, and nothing it could damage."""
    git = require_binary("git", label="unwind-rc-sandbox").path
    project_dir = _register_project(root, name)
    dev_tree = root / f"{name}-dev-tree"
    dev_tree.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [git, "init", "-q"], cwd=str(dev_tree), capture_output=True, text=True, check=True, timeout=30
    )
    (project_dir / "project.cfg").write_text(
        "[project]\n"
        f"project_name = {name}\n"
        f"dev_tree_path = {dev_tree}\n"
        "workflow_type = release\n",
        encoding="utf-8",
    )
    (project_dir / "HALT").write_text("", encoding="utf-8")
    _write_active_rc(project_dir, active_rc)
    return project_dir


# The last line the CLI prints before it prompts, and the first line it
# prints after the prompt is answered. Over a closed stdin the prompt's
# read hits EOF and the run exits 1 right there, so "reached but not
# passed" is the signature of an unanswered prompt; a run that answers it
# with --confirm (or skips it with --force) never prints the preamble at
# all and goes straight to step (a).
_UNWIND_REACHED_THE_PROMPT = "There is NO undo except the backup created in step (a)."
_UNWIND_PASSED_THE_PROMPT = "--- Step a: Backup ---"


class TestUnwindRcSafetyLadder:
    @staticmethod
    def _replace_unwind_rc(monkeypatch: pytest.MonkeyPatch) -> list:
        """Swap cm.unwind.unwind_rc for a recorder that narrates one line."""
        from pgai_agent_kanban.cm import unwind as cm_unwind

        calls: list = []

        def _fake(ctx, project, version, **kwargs):
            calls.append((ctx, project, version, kwargs))
            kwargs["out"]("Unwind complete.\n")
            return cm_unwind.UnwindOutcome(project, f"rc/{version}", "unwound")

        monkeypatch.setattr(cm_unwind, "unwind_rc", _fake)
        return calls

    def test_cli_prompt_aborts_on_closed_stdin_and_confirm_flag_answers_it(
        self, sandbox_root: pathlib.Path
    ) -> None:
        _unwind_sandbox(sandbox_root, "proj", "v9.9.9")
        args = ["--project", "proj", "--key", "v9.9.9"]

        unanswered = _run_cli(_UNWIND_RC_SH, args, sandbox_root)
        assert unanswered.returncode == 1, unanswered.stdout + unanswered.stderr
        assert _UNWIND_REACHED_THE_PROMPT in unanswered.stdout, unanswered.stdout
        assert _UNWIND_PASSED_THE_PROMPT not in unanswered.stdout, (
            "an unanswered prompt over a closed stdin must stop the run\n" + unanswered.stdout
        )

        confirmed = _run_cli(_UNWIND_RC_SH, [*args, "--confirm"], sandbox_root)
        assert _UNWIND_PASSED_THE_PROMPT in confirmed.stdout, (
            "--confirm must carry the run past the prompt over a closed stdin\n"
            + confirmed.stdout + confirmed.stderr
        )
        assert "--confirm (confirmation prompt answered non-interactively)" in confirmed.stdout
        assert "ERROR: Active RC mismatch." not in confirmed.stderr

    @pytest.mark.parametrize("confirm", [True, "unwind-rc proj v1.2.0"], ids=["true", "typed-phrase"])
    def test_confirm_reaches_unwind_rc_confirmed_without_force(
        self, ops_client: TestClient, sandbox_root: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch, confirm,
    ) -> None:
        calls = self._replace_unwind_rc(monkeypatch)
        with patch("subprocess.run") as mock_run:
            resp = ops_client.post(
                "/operations/unwind-rc",
                json={"project": "proj", "key": "v1.2.0", "confirm": confirm},
            )
            mock_run.assert_not_called()
        assert len(calls) == 1, calls
        ctx, project, version, kwargs = calls[0]
        assert ctx.kanban_root == sandbox_root.resolve()
        assert (project, version) == ("proj", "v1.2.0")
        assert kwargs["confirm"] is True and kwargs["force"] is False, kwargs
        assert resp.status_code == 200, resp.json()
        assert resp.json() == {
            "exit_code": 0, "stdout": "Unwind complete.\n", "stderr": "", "warnings": [],
        }

    def test_force_reaches_unwind_rc_as_its_own_argument(
        self, ops_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._replace_unwind_rc(monkeypatch)
        resp = ops_client.post(
            "/operations/unwind-rc",
            json={"project": "proj", "key": "v1.2.0", "confirm": True, "force": True},
        )
        assert resp.status_code == 200, resp.json()
        assert calls[0][3]["confirm"] is True and calls[0][3]["force"] is True, calls

    @pytest.mark.parametrize(
        "body",
        [
            {"project": "proj", "key": "v1.2.0"},
            {"project": "proj", "key": "v1.2.0", "force": True},
            {"project": "proj", "key": "v1.2.0", "confirm": ""},
            {"project": "proj", "key": "v1.2.0", "confirm": False},
            {"project": "proj", "key": "v1.2.0", "confirm": False, "force": True},
        ],
        ids=["no-confirm", "force-without-confirm", "empty-confirm", "confirm-false", "confirm-false-force"],
    )
    def test_missing_confirm_is_refused_before_anything_runs(
        self, ops_client: TestClient, monkeypatch: pytest.MonkeyPatch, body: dict
    ) -> None:
        calls = self._replace_unwind_rc(monkeypatch)
        with patch("subprocess.run") as mock_run:
            resp = ops_client.post("/operations/unwind-rc", json=body)
            mock_run.assert_not_called()
        assert calls == [], "nothing may run without confirm; force is not a confirmation"
        assert resp.status_code == 422, resp.text
        payload = resp.json()
        _assert_dual_channel(payload)
        assert "detail" not in payload
        assert payload["error"] == "unwind_rc.confirm_required"
        assert "'confirm': true" in payload["message"]
        assert '"confirm": true' in payload["remedy"], payload["remedy"]
        assert ('"force": true' in payload["remedy"]) is bool(body.get("force")), payload["remedy"]
        assert "<" not in payload["remedy"]

    @pytest.mark.parametrize(
        "wrong",
        ["unwind-rc proj v1.2.0 ", "Unwind-RC proj v1.2.0", "unwind-rc proj v1.2.1", "yes", "true"],
        ids=["trailing-space", "case-folded", "other-key", "yes", "true-as-a-string"],
    )
    def test_wrong_confirm_is_refused_before_anything_runs(
        self, ops_client: TestClient, monkeypatch: pytest.MonkeyPatch, wrong: str
    ) -> None:
        calls = self._replace_unwind_rc(monkeypatch)
        resp = ops_client.post(
            "/operations/unwind-rc",
            json={"project": "proj", "key": "v1.2.0", "confirm": wrong, "force": True},
        )
        assert calls == []
        assert resp.status_code == 422, resp.text
        payload = resp.json()
        _assert_dual_channel(payload)
        assert payload["error"] == "unwind_rc.confirm_mismatch"
        assert "expected 'unwind-rc proj v1.2.0'" in payload["message"]
        assert f"got '{wrong}'" in payload["message"]
        assert '"confirm": true' in payload["remedy"]

    def test_dry_run_runs_nothing_and_describes_the_confirmed_cli_call(
        self, ops_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._replace_unwind_rc(monkeypatch)
        with patch("subprocess.run") as mock_run:
            resp = ops_client.post(
                "/operations/unwind-rc",
                json={"project": "proj", "key": "v1.2.0", "dry_run": True},
            )
            mock_run.assert_not_called()
        assert calls == []
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["exit_code"] == 0
        assert payload["stdout"] == (
            "dry-run: would execute unwind-rc.sh --project proj --key v1.2.0 --confirm"
        ), payload
        assert "--dry-run" not in payload["stdout"], (
            "the route's dry_run is the API-layer short-circuit; it is not the "
            "CLI's --dry-run plan"
        )

    def test_confirmed_call_still_hits_the_scripts_active_rc_mismatch_check(
        self, ops_client: TestClient, sandbox_root: pathlib.Path
    ) -> None:
        _unwind_sandbox(sandbox_root, "proj", "v9.9.9")
        cli = _run_cli(
            _UNWIND_RC_SH, ["--project", "proj", "--key", "v1.0.0", "--confirm"], sandbox_root
        )
        assert cli.returncode == 1, cli.stdout + cli.stderr
        assert "ERROR: Active RC mismatch." in cli.stderr, cli.stderr

        resp = ops_client.post(
            "/operations/unwind-rc",
            json={"project": "proj", "key": "v1.0.0", "confirm": True},
        )

        body = resp.json()
        assert resp.status_code == 500, body
        _assert_dual_channel(body)
        assert body["exit_code"] == cli.returncode
        assert body["stderr"] == cli.stderr, "HTTP stderr must be byte-identical to the CLI's"
        assert body["error"] == "unwind_rc.refused"
        assert body["message"] == cli.stderr.strip()
        assert "unwind-rc.sh --project proj --key v1.0.0 --confirm" in body["remedy"]
        assert _UNWIND_PASSED_THE_PROMPT not in body["stdout"], _diagnostic(body)

    def test_confirmed_call_answers_the_prompt_over_http(
        self, ops_client: TestClient, sandbox_root: pathlib.Path
    ) -> None:
        _unwind_sandbox(sandbox_root, "proj", "v9.9.9")

        resp = ops_client.post(
            "/operations/unwind-rc",
            json={"project": "proj", "key": "v9.9.9", "confirm": True},
        )

        body = resp.json()
        assert _UNWIND_PASSED_THE_PROMPT in body["stdout"], _diagnostic(body)
        assert "WARNING: Active RC mismatch" not in body["stderr"], _diagnostic(body)

    def test_force_with_confirm_bypasses_only_the_mismatch_check(
        self, ops_client: TestClient, sandbox_root: pathlib.Path
    ) -> None:
        _unwind_sandbox(sandbox_root, "proj", "v9.9.9")

        resp = ops_client.post(
            "/operations/unwind-rc",
            json={
                "project": "proj",
                "key": "v1.0.0",
                "confirm": True,
                "force": True,
            },
        )

        body = resp.json()
        assert "WARNING: Active RC mismatch (--force bypassing check)." in body["stderr"], (
            _diagnostic(body)
        )
        assert "ERROR: Active RC mismatch." not in body["stderr"]
        assert _UNWIND_PASSED_THE_PROMPT in body["stdout"], _diagnostic(body)

    def test_force_with_confirm_does_not_bypass_the_halt_check(
        self, ops_client: TestClient, sandbox_root: pathlib.Path
    ) -> None:
        project_dir = _unwind_sandbox(sandbox_root, "proj", "v1.0.0")
        (project_dir / "HALT").unlink()

        resp = ops_client.post(
            "/operations/unwind-rc",
            json={
                "project": "proj",
                "key": "v1.0.0",
                "confirm": True,
                "force": True,
            },
        )

        body = resp.json()
        assert resp.status_code == 500, body
        assert body["exit_code"] == 1, _diagnostic(body)
        assert "ERROR: no HALT file found." in body["stderr"], _diagnostic(body)
        assert _UNWIND_PASSED_THE_PROMPT not in body["stdout"]
