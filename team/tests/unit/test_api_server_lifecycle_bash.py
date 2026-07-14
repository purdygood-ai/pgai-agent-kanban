"""
test_api_server_lifecycle_bash.py
==================================
Behavioral unit tests for team/scripts/api-server.sh lifecycle commands.

Tests cover two behavioral contracts:

Normal lifecycle (regression lock)
------------------------------------
With an intact pidfile pointing at a live process:
  - status reports "running (pid <N>)"
  - stop terminates the process and removes the pidfile
  - start is a no-op when a server is already running

Orphan detection (the fix for the pidfile-loss failure mode)
-------------------------------------------------------------
When the port is occupied by an untracked process (pidfile missing or gone):
  - status reports the occupant PID and start time
  - stop kills the orphan
  - start reports the squatter PID and "address already in use" cause

Pidfile location
----------------
The pidfile must live under <kanban_root>/run/api/, not under the framework
temp root, so temp-tree cleanup cannot orphan a running server.

Env-absent fail-loud contract
------------------------------
When PGAI_AGENT_KANBAN_ROOT_PATH is absent and --kanban-root is not supplied:
  - start exits non-zero before creating any pidfile
  - The exact source-instruction message is printed on stderr
  - No pidfile is created under any candidate location
  - The API port remains unbound

Absolute kanban_root contract
------------------------------
The resolved kanban root (from env or --kanban-root) is always absolute.
The /health endpoint's kanban_root field starts with '/'.

Port constraint
---------------
All tests use an OS-assigned ephemeral port via the PGAI_API_PORT env var.
Port 8300 is never used in tests.

Test naming convention (SOP.md Anti-pattern 6):
  Names describe behavior, never bug IDs or scaffolding labels.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import socket
import subprocess
import sys
import textwrap
import time

import pytest

from fastapi.testclient import TestClient

from tests.unit.shell_harness import run_bash

# ---------------------------------------------------------------------------
# Path to api-server.sh (relative to the team/ cwd used by run-unit-tests.sh)
# ---------------------------------------------------------------------------
_API_SERVER_SH = "scripts/api-server.sh"

# Absolute path helpers for constructing synthetic no-shell-env sandboxes.
_TEAM_SCRIPTS_DIR = pathlib.Path(__file__).parent.parent.parent / "scripts"
_REAL_API_SERVER_SH = _TEAM_SCRIPTS_DIR / "api-server.sh"
_REAL_ENV_BOOTSTRAP_SH = _TEAM_SCRIPTS_DIR / "lib" / "env_bootstrap.sh"

# Production default port — must never appear in test bindings.
_PRODUCTION_DEFAULT_PORT = 8300


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_free_port() -> int:
    """Return a free ephemeral TCP port on 127.0.0.1, never 8300."""
    for _ in range(10):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        if port != _PRODUCTION_DEFAULT_PORT:
            return port
    raise RuntimeError(
        f"Could not obtain a free port that is not {_PRODUCTION_DEFAULT_PORT} after 10 attempts."
    )


def _start_fake_listener(port: int) -> subprocess.Popen:
    """Spawn a subprocess that binds *port* on 127.0.0.1 and holds it open.

    The subprocess writes "READY:<port>" to stdout once the socket is bound.
    The caller must kill and wait on the returned Popen object.

    Args:
        port: The port to bind.

    Returns:
        The running subprocess.Popen instance.
    """
    script = textwrap.dedent(f"""\
        import socket, sys, time
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", {port}))
        s.listen(1)
        sys.stdout.write("READY:{port}\\n")
        sys.stdout.flush()
        while True:
            time.sleep(1)
    """)
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    # Wait for the READY signal.
    assert proc.stdout is not None
    line = proc.stdout.readline().decode("utf-8", errors="replace").strip()
    assert line.startswith("READY:"), (
        f"fake listener did not emit READY; got: {line!r}"
    )
    return proc


def _invoke_api_server(
    tmp_path: pathlib.Path,
    kanban_root: pathlib.Path,
    subcommand: str,
    port: int,
) -> "run_bash.__class__":  # type: ignore[name-defined]
    """Invoke api-server.sh with the given subcommand against a synthetic root.

    Args:
        tmp_path:     pytest tmp_path for temp file placement.
        kanban_root:  The fake kanban root; pidfile ends up under run/api/.
        subcommand:   One of: start, stop, status.
        port:         The port api-server.sh should manage.

    Returns:
        BashResult with stdout, stderr, returncode.
    """
    # Build the temp dir for log output (api-server.sh writes log to
    # <temp_root>/api/api-server.log).
    api_temp = tmp_path / "api"
    api_temp.mkdir(parents=True, exist_ok=True)

    env = {
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path),
        "PGAI_API_PORT": str(port),
    }
    return run_bash(
        tmp_path,
        f"bash {_API_SERVER_SH} {subcommand} --kanban-root {kanban_root!s}",
        extra_env=env,
        timeout=15,
    )


# ---------------------------------------------------------------------------
# Normal lifecycle: pidfile intact
# ---------------------------------------------------------------------------


def test_status_reports_running_when_pidfile_points_to_live_process(
    tmp_path: pathlib.Path,
) -> None:
    """status prints 'running (pid N)' when the pidfile holds a live PID.

    A fake sleep process is started; its PID is written to the expected
    pidfile location.  status must find it and report running.
    """
    port = _find_free_port()
    kanban_root = tmp_path / "kanban_root"
    run_dir = kanban_root / "run" / "api"
    run_dir.mkdir(parents=True, exist_ok=True)
    pidfile = run_dir / "api-server.pid"

    # Start a real process and record its PID.
    sleeper = subprocess.Popen(["sleep", "60"])
    try:
        pidfile.write_text(str(sleeper.pid) + "\n", encoding="utf-8")

        result = _invoke_api_server(tmp_path, kanban_root, "status", port)

        assert result.returncode == 0, (
            f"status returned {result.returncode} (expected 0); "
            f"stdout: {result.stdout!r}; stderr: {result.stderr!r}"
        )
        assert f"running (pid {sleeper.pid})" in result.stdout, (
            f"Expected 'running (pid {sleeper.pid})' in stdout; got: {result.stdout!r}"
        )
    finally:
        sleeper.terminate()
        sleeper.wait(timeout=5)


def test_stop_terminates_tracked_process_and_removes_pidfile(
    tmp_path: pathlib.Path,
) -> None:
    """stop sends SIGTERM to the tracked PID and removes the pidfile.

    A fake sleep process is started and recorded in the pidfile.  stop
    must terminate the process and remove the pidfile.
    """
    port = _find_free_port()
    kanban_root = tmp_path / "kanban_root"
    run_dir = kanban_root / "run" / "api"
    run_dir.mkdir(parents=True, exist_ok=True)
    pidfile = run_dir / "api-server.pid"

    sleeper = subprocess.Popen(["sleep", "60"])
    try:
        pidfile.write_text(str(sleeper.pid) + "\n", encoding="utf-8")

        result = _invoke_api_server(tmp_path, kanban_root, "stop", port)

        assert result.returncode == 0, (
            f"stop returned {result.returncode}; "
            f"stdout: {result.stdout!r}; stderr: {result.stderr!r}"
        )
        assert "stopped" in result.stdout.lower() or "stopping" in result.stdout.lower(), (
            f"Expected stop confirmation in stdout; got: {result.stdout!r}"
        )
        # Process should be dead.
        assert sleeper.poll() is not None, (
            f"Tracked process {sleeper.pid} is still running after stop."
        )
        # Pidfile should be gone.
        assert not pidfile.exists(), (
            f"Pidfile {pidfile} still exists after stop."
        )
    finally:
        # Safety cleanup: kill the sleeper if it somehow survived.
        if sleeper.poll() is None:
            sleeper.terminate()
            sleeper.wait(timeout=5)


def test_start_is_noop_when_tracked_server_is_running(
    tmp_path: pathlib.Path,
) -> None:
    """start reports 'already running' without starting a second process.

    A fake process is recorded in the pidfile.  start must detect the live
    process via the pidfile and exit without launching a second one.
    """
    port = _find_free_port()
    kanban_root = tmp_path / "kanban_root"
    run_dir = kanban_root / "run" / "api"
    run_dir.mkdir(parents=True, exist_ok=True)
    pidfile = run_dir / "api-server.pid"

    sleeper = subprocess.Popen(["sleep", "60"])
    try:
        pidfile.write_text(str(sleeper.pid) + "\n", encoding="utf-8")

        result = _invoke_api_server(tmp_path, kanban_root, "start", port)

        assert result.returncode == 0, (
            f"start returned {result.returncode}; "
            f"stdout: {result.stdout!r}; stderr: {result.stderr!r}"
        )
        assert "already running" in result.stderr.lower(), (
            f"Expected 'already running' message; stderr: {result.stderr!r}"
        )
    finally:
        sleeper.terminate()
        sleeper.wait(timeout=5)


# ---------------------------------------------------------------------------
# Pidfile location: must be under kanban_root/run/api/, not under temp
# ---------------------------------------------------------------------------


def test_pidfile_lives_in_kanban_root_run_directory(
    tmp_path: pathlib.Path,
) -> None:
    """The pidfile is written to <kanban_root>/run/api/, not under the temp root.

    After a fake start (we write the pidfile manually, simulating what the
    script would do), we verify the pidfile location is NOT inside the
    framework temp tree.  Then we delete the pidfile and verify status still
    reports the orphan via the port probe (pidfile loss is non-blinding).

    This test also covers the core acceptance criterion: deleting the pidfile
    between start and status must NOT produce "not running".
    """
    port = _find_free_port()
    kanban_root = tmp_path / "kanban_root"
    run_dir = kanban_root / "run" / "api"
    run_dir.mkdir(parents=True, exist_ok=True)
    pidfile = run_dir / "api-server.pid"

    # The temp root is tmp_path — different from kanban_root.
    temp_root = tmp_path / "temp"
    temp_root.mkdir(parents=True, exist_ok=True)

    fake_listener: subprocess.Popen | None = None
    try:
        fake_listener = _start_fake_listener(port)
        server_pid = fake_listener.pid

        # Write the pidfile as start would.
        pidfile.write_text(str(server_pid) + "\n", encoding="utf-8")

        # Verify pidfile is in kanban_root, not in temp_root.
        assert pidfile.parent.is_relative_to(kanban_root), (
            f"Pidfile {pidfile} is not inside kanban_root {kanban_root}."
        )
        assert not str(pidfile).startswith(str(temp_root)), (
            f"Pidfile {pidfile} is inside the temp root {temp_root} — "
            "it must be in the durable state directory."
        )

        # Now delete the pidfile to simulate a temp-cleanup event.
        pidfile.unlink()
        assert not pidfile.exists()

        # status must NOT say "not running" — the port probe must find the orphan.
        env = {
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_root),
            "PGAI_API_PORT": str(port),
        }
        result = run_bash(
            tmp_path,
            f"bash {_API_SERVER_SH} status --kanban-root {kanban_root!s}",
            extra_env=env,
            timeout=15,
        )

        assert "not running" not in result.stdout, (
            "status reported 'not running' after pidfile deletion even though "
            f"the port {port} is occupied by PID {server_pid}.  "
            "The port probe failed to detect the orphan."
        )
        assert str(server_pid) in result.stdout, (
            f"status did not mention the orphan PID {server_pid} in stdout; "
            f"got: {result.stdout!r}"
        )

    finally:
        if fake_listener is not None and fake_listener.poll() is None:
            fake_listener.terminate()
            fake_listener.wait(timeout=5)


# ---------------------------------------------------------------------------
# Orphan detection: port occupied, no pidfile
# ---------------------------------------------------------------------------


def test_status_reports_orphan_pid_when_port_occupied_without_pidfile(
    tmp_path: pathlib.Path,
) -> None:
    """status reports the orphan PID when the port is busy but no pidfile exists.

    Simulates the exact an earlier defect reproduction: start → delete pidfile →
    status.  status must NOT say "not running"; it must name the occupant.
    """
    port = _find_free_port()
    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir(parents=True, exist_ok=True)

    fake_listener: subprocess.Popen | None = None
    try:
        fake_listener = _start_fake_listener(port)
        orphan_pid = fake_listener.pid

        # No pidfile exists — simulates the pidfile having been cleaned up.
        result = _invoke_api_server(tmp_path, kanban_root, "status", port)

        # Must NOT say "not running".
        assert "not running" not in result.stdout, (
            f"status said 'not running' when orphan PID {orphan_pid} "
            f"holds port {port}.  stdout: {result.stdout!r}"
        )
        # Must mention the orphan PID.
        assert str(orphan_pid) in result.stdout, (
            f"Orphan PID {orphan_pid} not mentioned in status output; "
            f"stdout: {result.stdout!r}"
        )

    finally:
        if fake_listener is not None and fake_listener.poll() is None:
            fake_listener.terminate()
            fake_listener.wait(timeout=5)


def test_stop_kills_orphan_process_holding_port(
    tmp_path: pathlib.Path,
) -> None:
    """stop kills the process holding the port when no pidfile is present.

    Confirms the stop contract: when an orphan occupies the port with no
    tracked pidfile, stop sends SIGTERM and the process exits.
    """
    port = _find_free_port()
    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir(parents=True, exist_ok=True)

    fake_listener: subprocess.Popen | None = None
    try:
        fake_listener = _start_fake_listener(port)
        orphan_pid = fake_listener.pid

        result = _invoke_api_server(tmp_path, kanban_root, "stop", port)

        assert result.returncode == 0, (
            f"stop returned {result.returncode} (expected 0); "
            f"stdout: {result.stdout!r}; stderr: {result.stderr!r}"
        )
        # The orphan PID should be mentioned.
        assert str(orphan_pid) in result.stdout, (
            f"stop did not mention the orphan PID {orphan_pid}; "
            f"stdout: {result.stdout!r}"
        )

        # Wait briefly for the process to actually exit.
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if fake_listener.poll() is not None:
                break
            time.sleep(0.2)

        assert fake_listener.poll() is not None, (
            f"Orphan process {orphan_pid} is still running after stop killed it."
        )
        # Port should now be free.
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                pytest.fail(
                    f"Port {port} is still accepting connections after stop killed the orphan."
                )
        except (ConnectionRefusedError, OSError):
            pass  # Port is free — expected.

    finally:
        if fake_listener is not None and fake_listener.poll() is None:
            fake_listener.terminate()
            fake_listener.wait(timeout=5)


def test_start_names_squatter_pid_when_port_busy_without_pidfile(
    tmp_path: pathlib.Path,
) -> None:
    """start reports the squatter PID and cause when the port is occupied.

    When no pidfile exists but the port is busy, start must name the squatter
    PID and the 'address already in use' cause rather than emitting the
    generic 'failed to start' message.
    """
    port = _find_free_port()
    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir(parents=True, exist_ok=True)

    fake_listener: subprocess.Popen | None = None
    try:
        fake_listener = _start_fake_listener(port)
        squatter_pid = fake_listener.pid

        result = _invoke_api_server(tmp_path, kanban_root, "start", port)

        # start must fail (port is busy).
        assert result.returncode != 0, (
            f"start returned 0 (expected non-zero) when port {port} is busy; "
            f"stdout: {result.stdout!r}; stderr: {result.stderr!r}"
        )
        # The squatter PID must be mentioned.
        assert str(squatter_pid) in result.stderr, (
            f"start did not mention squatter PID {squatter_pid}; "
            f"stderr: {result.stderr!r}"
        )
        # The 'address already in use' cause must appear.
        assert "address already in use" in result.stderr.lower(), (
            f"'address already in use' not in start stderr; got: {result.stderr!r}"
        )

    finally:
        if fake_listener is not None and fake_listener.poll() is None:
            fake_listener.terminate()
            fake_listener.wait(timeout=5)


def test_status_returns_not_running_when_port_is_free_and_no_pidfile(
    tmp_path: pathlib.Path,
) -> None:
    """status returns 'not running' and exits 1 when neither pidfile nor port is occupied."""
    port = _find_free_port()
    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir(parents=True, exist_ok=True)

    result = _invoke_api_server(tmp_path, kanban_root, "status", port)

    assert result.returncode == 1, (
        f"status returned {result.returncode} (expected 1) when nothing is running; "
        f"stdout: {result.stdout!r}"
    )
    assert "not running" in result.stdout, (
        f"Expected 'not running' in stdout; got: {result.stdout!r}"
    )


# ---------------------------------------------------------------------------
# Env-absent fail-loud contract
# ---------------------------------------------------------------------------


def test_start_exits_nonzero_with_message_when_root_env_absent(
    tmp_path: pathlib.Path,
) -> None:
    """start exits non-zero and prints the source-instruction message when root env is absent.

    PGAI_AGENT_KANBAN_ROOT_PATH must not be set (neither via env nor --kanban-root).
    The script must refuse before doing any pidfile or state-path work.

    The error message comes from the env_bootstrap.sh prelude (sourced as the
    first act in api-server.sh).  When the env var is unset and no shell-env
    is present at the candidate root, env_bootstrap.sh emits:
        PGAI_AGENT_KANBAN_ROOT_PATH not set — shell-env missing or broken at <path>

    Implementation note: the test uses a synthetic scripts directory under
    tmp_path (no shell-env at its parent) so that env_bootstrap.sh cannot
    auto-discover the kanban root from the dev-tree's own shell-env file.
    """
    port = _find_free_port()

    # Build a minimal synthetic scripts directory: api-server.sh + lib/env_bootstrap.sh.
    # The parent of this "scripts/" dir (tmp_path itself) has no shell-env, so
    # env_bootstrap.sh will fail loud when PGAI_AGENT_KANBAN_ROOT_PATH is unset.
    synthetic_scripts = tmp_path / "scripts"
    synthetic_lib = synthetic_scripts / "lib"
    synthetic_lib.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_REAL_API_SERVER_SH, synthetic_scripts / "api-server.sh")
    shutil.copy2(_REAL_ENV_BOOTSTRAP_SH, synthetic_lib / "env_bootstrap.sh")

    api_temp = tmp_path / "api"
    api_temp.mkdir(parents=True, exist_ok=True)

    env = {
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path),
        "PGAI_API_PORT": str(port),
    }
    # Use env -u to remove PGAI_AGENT_KANBAN_ROOT_PATH even if the test harness
    # set it in the inherited environment.  Do NOT pass --kanban-root.
    # The synthetic api-server.sh is in tmp_path/scripts/, whose parent (tmp_path)
    # has no shell-env, so env_bootstrap.sh fails loud before any state-path work.
    result = run_bash(
        tmp_path,
        f"env -u PGAI_AGENT_KANBAN_ROOT_PATH bash {synthetic_scripts / 'api-server.sh'} start",
        extra_env=env,
        timeout=15,
    )

    assert result.returncode != 0, (
        "start returned 0 (expected non-zero) when PGAI_AGENT_KANBAN_ROOT_PATH "
        f"is absent; stderr: {result.stderr!r}; stdout: {result.stdout!r}"
    )
    # env_bootstrap.sh emits the fail-loud message when the root var is unset and
    # no shell-env exists at the candidate location derived from BASH_SOURCE[1].
    expected_fragment = "PGAI_AGENT_KANBAN_ROOT_PATH not set — shell-env missing or broken at"
    assert expected_fragment in result.stderr, (
        f"Expected fragment {expected_fragment!r} in stderr; got: {result.stderr!r}"
    )


def test_no_pidfile_created_when_root_env_absent(
    tmp_path: pathlib.Path,
) -> None:
    """No pidfile is created under any candidate location when root env is absent.

    After a failed env-absent start, both candidate pidfile locations must be empty:
      - <kanban_root>/run/api/api-server.pid  (durable state directory)
      - <temp>/api/api-server.pid             (framework temp root)
    Neither should be created; the script exits before any state-path work.
    """
    port = _find_free_port()

    # Candidate kanban root (would be used if the check were absent).
    candidate_kanban_root = tmp_path / "kanban_root"
    candidate_kanban_root.mkdir(parents=True, exist_ok=True)

    # Candidate temp root.
    candidate_temp = tmp_path / "temp"
    candidate_temp.mkdir(parents=True, exist_ok=True)

    env = {
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(candidate_temp),
        "PGAI_API_PORT": str(port),
    }
    run_bash(
        tmp_path,
        f"env -u PGAI_AGENT_KANBAN_ROOT_PATH bash {_API_SERVER_SH} start",
        extra_env=env,
        timeout=15,
    )

    # Neither candidate pidfile location should exist.
    durable_pidfile = candidate_kanban_root / "run" / "api" / "api-server.pid"
    temp_pidfile = candidate_temp / "api" / "api-server.pid"

    assert not durable_pidfile.exists(), (
        f"Pidfile found at durable location {durable_pidfile} after env-absent start; "
        "the fail-loud guard must fire before any state-path work."
    )
    assert not temp_pidfile.exists(), (
        f"Pidfile found at temp location {temp_pidfile} after env-absent start; "
        "the fail-loud guard must fire before any state-path work."
    )


def test_port_unbound_after_failed_env_absent_start(
    tmp_path: pathlib.Path,
) -> None:
    """The API port remains free after a failed env-absent start.

    The script must exit before binding any port when PGAI_AGENT_KANBAN_ROOT_PATH
    is absent.
    """
    port = _find_free_port()

    env = {
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path),
        "PGAI_API_PORT": str(port),
    }
    run_bash(
        tmp_path,
        f"env -u PGAI_AGENT_KANBAN_ROOT_PATH bash {_API_SERVER_SH} start",
        extra_env=env,
        timeout=15,
    )

    # Port must be free — connection must be refused.
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pytest.fail(
                f"Port {port} is accepting connections after env-absent start; "
                "the script must not bind the port when root env is absent."
            )
    except (ConnectionRefusedError, OSError):
        pass  # Port is free — expected.


# ---------------------------------------------------------------------------
# Absolute kanban_root contract: /health response
# ---------------------------------------------------------------------------


def test_health_kanban_root_is_absolute_path() -> None:
    """GET /health returns a kanban_root field that starts with '/'.

    Creates a FastAPI app with an explicit ApiConfig whose kanban_root is an
    absolute path (the norm after load_api_config() applies .resolve()), then
    asserts the /health response's kanban_root field is absolute.
    """
    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    # anti-pattern-allowlist: 2 (justification: value is used as an assertion
    # subject only — no file I/O occurs at this path; the test verifies that
    # a path starting with '/' is reported as absolute, so a literal absolute
    # path string is the minimal, correct input for this check)
    abs_root = pathlib.Path("/tmp/kanban_root_test")
    cfg = ApiConfig(kanban_root=abs_root)
    app = create_app(cfg)

    client = TestClient(app, raise_server_exceptions=True)
    response = client.get("/health")

    assert response.status_code == 200, (
        f"/health returned {response.status_code}; body: {response.text!r}"
    )
    body = response.json()
    assert "kanban_root" in body, (
        f"/health response missing 'kanban_root' field; body: {body!r}"
    )
    assert body["kanban_root"].startswith("/"), (
        f"/health kanban_root is not absolute: {body['kanban_root']!r}; "
        "the value must start with '/'."
    )


def test_load_api_config_resolves_kanban_root_to_absolute_path(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """load_api_config() returns an absolute kanban_root when env is set.

    Sets PGAI_AGENT_KANBAN_ROOT_PATH to a real directory and asserts the
    returned ApiConfig's kanban_root is an absolute path (starts with '/').
    """
    from pgai_agent_kanban.api.config import load_api_config

    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(kanban_root))

    cfg = load_api_config()

    assert str(cfg.kanban_root).startswith("/"), (
        f"load_api_config() returned a relative kanban_root: {cfg.kanban_root!r}; "
        "expected an absolute path (starts with '/')."
    )


# ---------------------------------------------------------------------------
# Process-group stop: port-probe confirms release before success message
# ---------------------------------------------------------------------------


def _start_fake_group_listener(port: int) -> subprocess.Popen:
    """Spawn a two-process group: a parent (group leader) that holds no port,
    and a child that binds *port*.  The group is in its own session so that
    a group-kill from the test does not escape to the test runner.

    Returns the Popen handle for the PARENT (group leader).  The parent's PID
    is what the pidfile would record in the real server lifecycle.  The child
    holds the port.

    The parent writes "READY:<parent_pid>" to stdout once the child has
    confirmed the socket is bound.

    Args:
        port: The port for the child to bind.

    Returns:
        Popen of the parent (group leader).
    """
    # Build the parent script using string concatenation to avoid f-string
    # nesting issues with triple-quoted inner scripts.
    parent_script = (
        "import os, subprocess, sys, time\n"
        "import socket\n"
        # Inline the port as a literal so there is no string nesting.
        "PORT = " + str(port) + "\n"
        # Child script passed as a list of lines joined at runtime to avoid
        # triple-quote collisions with the outer textwrap.dedent block.
        "child_lines = [\n"
        "    'import socket, sys, time',\n"
        "    's = socket.socket(socket.AF_INET, socket.SOCK_STREAM)',\n"
        "    's.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)',\n"
        "    's.bind((\"127.0.0.1\", PORT))',\n"
        "    's.listen(1)',\n"
        "    'sys.stdout.write(\"CHILD_READY\\\\n\")',\n"
        "    'sys.stdout.flush()',\n"
        "    'while True:',\n"
        "    '    time.sleep(1)',\n"
        "]\n"
        "child_code = 'PORT=' + str(PORT) + '\\n' + '\\n'.join(child_lines)\n"
        "child = subprocess.Popen(\n"
        "    [sys.executable, '-c', child_code],\n"
        "    stdout=subprocess.PIPE,\n"
        "    stderr=subprocess.DEVNULL,\n"
        ")\n"
        "assert child.stdout is not None\n"
        "line = child.stdout.readline().decode('utf-8', errors='replace').strip()\n"
        "assert line == 'CHILD_READY', 'child did not emit CHILD_READY; got: ' + repr(line)\n"
        "sys.stdout.write('READY:' + str(os.getpid()) + '\\n')\n"
        "sys.stdout.flush()\n"
        "try:\n"
        "    while True:\n"
        "        time.sleep(1)\n"
        "except (KeyboardInterrupt, SystemExit):\n"
        "    pass\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", parent_script],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        # start_new_session puts the parent in its own process group so that
        # kill -- -PGID from api-server.sh targets only this group, not the
        # test runner's process group.
        start_new_session=True,
    )
    assert proc.stdout is not None
    line = proc.stdout.readline().decode("utf-8", errors="replace").strip()
    assert line.startswith("READY:"), (
        f"group-leader did not emit READY; got: {line!r}"
    )
    return proc


def test_tracked_stop_confirms_port_free_before_reporting_success(
    tmp_path: pathlib.Path,
) -> None:
    """stop probes the port and confirms it is free BEFORE printing 'stopped'.

    This is the load-bearing regression for the field observation: tracked stop
    declared 'API server stopped' while the uvicorn child was still listening.
    The fix: stop waits for the port to be released, not merely for the tracked
    PID to disappear.

    Setup: write a pidfile pointing to a fake process that is also the port
    holder (single-process case; the two-process regression is covered by
    test_tracked_stop_kills_whole_process_group).  Call stop.  Assert the port
    refuses connections before checking the exit code / message.
    """
    port = _find_free_port()
    kanban_root = tmp_path / "kanban_root"
    run_dir = kanban_root / "run" / "api"
    run_dir.mkdir(parents=True, exist_ok=True)
    pidfile = run_dir / "api-server.pid"

    fake_server: subprocess.Popen | None = None
    try:
        fake_server = _start_fake_listener(port)
        pidfile.write_text(str(fake_server.pid) + "\n", encoding="utf-8")

        result = _invoke_api_server(tmp_path, kanban_root, "stop", port)

        # Port-free is the load-bearing assertion — checked before message text.
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                pytest.fail(
                    f"Port {port} is still accepting connections after stop; "
                    "stop must not declare success while the port still answers."
                )
        except (ConnectionRefusedError, OSError):
            pass  # Port is free — expected.

        assert result.returncode == 0, (
            f"stop returned {result.returncode} (expected 0); "
            f"stdout: {result.stdout!r}; stderr: {result.stderr!r}"
        )
        assert "stopped" in result.stdout.lower(), (
            f"Expected 'stopped' confirmation in stdout; got: {result.stdout!r}"
        )

    finally:
        if fake_server is not None and fake_server.poll() is None:
            fake_server.terminate()
            fake_server.wait(timeout=5)


def test_tracked_stop_kills_whole_process_group(
    tmp_path: pathlib.Path,
) -> None:
    """stop kills the entire process group, not only the tracked (launcher) PID.

    Regression lock for the field observation where the tracked stop killed
    the launcher PID while the uvicorn child (bound to the port) survived.

    Setup: spawn a two-process group where the parent is the group leader
    (pidfile PID) and the child holds the API port.  Call stop.  Assert the
    port is free after stop — which is only possible if the child was also
    killed (group-kill semantics).
    """
    port = _find_free_port()
    kanban_root = tmp_path / "kanban_root"
    run_dir = kanban_root / "run" / "api"
    run_dir.mkdir(parents=True, exist_ok=True)
    pidfile = run_dir / "api-server.pid"

    group_leader: subprocess.Popen | None = None
    try:
        group_leader = _start_fake_group_listener(port)
        # The pidfile records only the group leader (launcher) PID.  The port
        # is held by the leader's child — a separate process.
        pidfile.write_text(str(group_leader.pid) + "\n", encoding="utf-8")

        result = _invoke_api_server(tmp_path, kanban_root, "stop", port)

        # Port-free is the load-bearing assertion — killing only the leader
        # would leave the child alive on the port (the original bug).
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                pytest.fail(
                    f"Port {port} is still accepting connections after stop; "
                    "only the launcher PID was killed (the original bug), "
                    "not the whole process group."
                )
        except (ConnectionRefusedError, OSError):
            pass  # Port is free — the whole group was killed.

        assert result.returncode == 0, (
            f"stop returned {result.returncode} (expected 0); "
            f"stdout: {result.stdout!r}; stderr: {result.stderr!r}"
        )

    finally:
        if group_leader is not None and group_leader.poll() is None:
            import os
            import signal
            try:
                os.killpg(os.getpgid(group_leader.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
            group_leader.wait(timeout=5)


def test_stop_reports_failure_and_exits_nonzero_when_port_stays_bound(
    tmp_path: pathlib.Path,
) -> None:
    """stop exits non-zero and names the surviving PID when the port stays bound.

    Simulates a scenario where stop sends SIGTERM but the port-holder survives
    the full wait loop (e.g. because SIGTERM is ignored).  The stop command must
    print a failure line naming the surviving PID and exit non-zero.

    Setup: a fake process that ignores SIGTERM and holds the port.  After the
    wait loop, the port is still occupied.  Assert the failure path.
    """
    port = _find_free_port()
    kanban_root = tmp_path / "kanban_root"
    run_dir = kanban_root / "run" / "api"
    run_dir.mkdir(parents=True, exist_ok=True)
    pidfile = run_dir / "api-server.pid"

    # A process that ignores SIGTERM and holds the port.
    script = textwrap.dedent(f"""\
        import signal, socket, sys, time
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", {port}))
        s.listen(1)
        sys.stdout.write("READY:{port}\\n")
        sys.stdout.flush()
        while True:
            time.sleep(1)
    """)
    stubborn: subprocess.Popen | None = None
    try:
        stubborn = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        assert stubborn.stdout is not None
        line = stubborn.stdout.readline().decode("utf-8", errors="replace").strip()
        assert line.startswith("READY:"), (
            f"stubborn listener did not emit READY; got: {line!r}"
        )

        pidfile.write_text(str(stubborn.pid) + "\n", encoding="utf-8")

        result = _invoke_api_server(tmp_path, kanban_root, "stop", port)

        # Must exit non-zero — port is still bound.
        assert result.returncode != 0, (
            f"stop returned 0 (expected non-zero) when port {port} "
            f"is still bound; stdout: {result.stdout!r}; stderr: {result.stderr!r}"
        )
        # Must name the surviving PID.
        assert str(stubborn.pid) in result.stderr, (
            f"stop failure message does not name the surviving PID {stubborn.pid}; "
            f"stderr: {result.stderr!r}"
        )
        # Must mention 'stop failed' or 'still bound'.
        assert "stop failed" in result.stderr.lower() or "still bound" in result.stderr.lower(), (
            f"stop stderr does not describe the failure clearly; got: {result.stderr!r}"
        )

    finally:
        if stubborn is not None and stubborn.poll() is None:
            import os
            import signal
            try:
                os.killpg(os.getpgid(stubborn.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
            stubborn.wait(timeout=5)


def test_orphan_stop_confirms_port_free_before_reporting_success(
    tmp_path: pathlib.Path,
) -> None:
    """orphan stop branch: port-probe confirms release before success message.

    The orphan stop branch (port occupied, no pidfile) must apply the same
    port-probe-verify contract as the tracked stop branch: success is only
    declared when the port is confirmed free.

    Setup: fake listener on the port, no pidfile.  Call stop.  Assert port
    refuses connections, then check exit code and message.
    """
    port = _find_free_port()
    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir(parents=True, exist_ok=True)

    fake_listener: subprocess.Popen | None = None
    try:
        fake_listener = _start_fake_listener(port)

        result = _invoke_api_server(tmp_path, kanban_root, "stop", port)

        # Port-free is the load-bearing assertion — checked before message text.
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                pytest.fail(
                    f"Port {port} still accepting connections after orphan stop; "
                    "orphan stop must confirm port is free before success."
                )
        except (ConnectionRefusedError, OSError):
            pass  # Port is free — expected.

        assert result.returncode == 0, (
            f"orphan stop returned {result.returncode} (expected 0); "
            f"stdout: {result.stdout!r}; stderr: {result.stderr!r}"
        )
        assert "stopped" in result.stdout.lower(), (
            f"Expected 'stopped' confirmation in stdout after orphan stop; "
            f"got: {result.stdout!r}"
        )

    finally:
        if fake_listener is not None and fake_listener.poll() is None:
            fake_listener.terminate()
            fake_listener.wait(timeout=5)
