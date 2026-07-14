"""
api_server.py
=============
Teardown-guaranteed API-server fixture.

Starts the pgai-agent-kanban API server as a real uvicorn subprocess on an
ephemeral port, records the process PID, and guarantees teardown via a
try/finally block regardless of whether the calling test raises an exception.

WHY THIS EXISTS
---------------
Tests that exercise the live HTTP surface (real socket, real uvicorn) must
not leak a bound listener when they finish.  A leaked listener on the
production default port (8300) squats the operator's well-known address and
can silently answer /health with fixture state, masquerading as the real
service.  A leaked listener on any port is a background process the operator
did not ask for.

This fixture closes both failure modes:

  1. **Teardown guarantee** — the server is stopped in a finally block keyed
     on the recorded PID, so teardown runs even when the test raises.

  2. **Port hygiene** — the fixture never binds port 8300; it uses an
     OS-assigned ephemeral port (port 0 technique) or a caller-supplied port.
     After the server stops, the fixture actively verifies the port is free
     (attempts a TCP connect and expects ECONNREFUSED or ENOENT) before
     returning.  The test cannot proceed until the port is provably free.

HOW TO USE
----------
As a plain function (any test file that needs a running HTTP server):

    from team.tests.fixtures.api_server import (
        api_server_fixture,
        ServerHandle,
    )

    def test_health_endpoint(tmp_path):
        with api_server_fixture(tmp_path) as handle:
            import urllib.request
            resp = urllib.request.urlopen(
                f"http://127.0.0.1:{handle.port}/health"
            )
            assert resp.status == 200

As a pytest fixture (import into conftest.py or request in a test):

    from team.tests.fixtures.api_server import api_server  # pytest fixture
    # or, using conftest re-export:
    def test_health(api_server):
        import urllib.request
        resp = urllib.request.urlopen(
            f"http://127.0.0.1:{api_server.port}/health"
        )
        assert resp.status == 200

SERVERHANDLE FIELDS
-------------------
    port  (int)   — actual TCP port the server is bound to
    pid   (int)   — PID of the uvicorn subprocess

CONSTRAINTS HONOURED
--------------------
  - Never binds port 8300.
  - Teardown runs in a finally block; no separate teardown fixture to skip.
  - Port-free assertion after stop: connect refused or ss scan confirms free.
  - Subprocess stdin/stdout/stderr are redirected to devnull/logfile so the
    test runner is not polluted.
  - All temp paths use pytest's tmp_path fixture (routed through the
    framework temp root by the parent conftest.py).
"""

from __future__ import annotations

import os
import pathlib
import signal
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Generator

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Startup probe: number of seconds to wait for the server to accept connections.
_STARTUP_TIMEOUT_SECONDS = 10

# Teardown probe: max seconds to wait for the process to exit after SIGTERM.
_SIGTERM_WAIT_SECONDS = 8

# Port-free assertion: retry interval and max wait after the process exits.
_PORT_FREE_POLL_INTERVAL = 0.1  # seconds between connect attempts
_PORT_FREE_TIMEOUT = 5          # seconds before giving up

# The production default port — must never be used by this fixture.
_PRODUCTION_DEFAULT_PORT = 8300


# ---------------------------------------------------------------------------
# ServerHandle dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ServerHandle:
    """Immutable reference to a running API server instance.

    Attributes:
        port: The TCP port the server is bound to.  Always an ephemeral port;
              never the production default (8300).
        pid:  The PID of the uvicorn subprocess.
    """

    port: int
    pid: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_free_port() -> int:
    """Return a free ephemeral TCP port on 127.0.0.1.

    Binds a socket to port 0 (which asks the OS to assign a free port),
    reads the assigned port number, then closes the socket before returning.
    There is a small TOCTOU window between close and the subprocess binding
    the same port, but on loopback this is acceptable in test contexts.

    Never returns the production default port 8300.

    Returns:
        int — the free port number.

    Raises:
        RuntimeError: when the OS assigns port 8300 (should not happen on
                      a well-configured system, but guard defensively).
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    if port == _PRODUCTION_DEFAULT_PORT:
        raise RuntimeError(
            f"OS assigned port {_PRODUCTION_DEFAULT_PORT} — the production "
            "default.  Refusing to use it.  Re-run to obtain a different port."
        )

    return port


def _wait_for_port_open(host: str, port: int, timeout: float) -> bool:
    """Poll until a TCP connect to (host, port) succeeds or timeout elapses.

    Used after process startup to verify the server is accepting connections
    before returning to the caller.

    Args:
        host:    IP address to connect to (typically "127.0.0.1").
        port:    TCP port number.
        timeout: Maximum seconds to wait.

    Returns:
        True  — connection succeeded within timeout (server is up).
        False — timeout elapsed without a successful connect.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.2)
    return False


def _assert_port_free(host: str, port: int) -> None:
    """Assert that (host, port) refuses connections within _PORT_FREE_TIMEOUT.

    Polls by attempting TCP connects.  The assertion passes when every connect
    attempt raises ConnectionRefusedError or another OS-level refusal within
    the timeout window.  Raises AssertionError with context if the port is
    still accepting connections at timeout.

    Args:
        host: IP address to probe.
        port: TCP port number.

    Raises:
        AssertionError: if the port is still bound and accepting connections
                        after _PORT_FREE_TIMEOUT seconds.
    """
    deadline = time.monotonic() + _PORT_FREE_TIMEOUT
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                # Connection succeeded — port still open; keep polling.
                time.sleep(_PORT_FREE_POLL_INTERVAL)
        except (ConnectionRefusedError, OSError):
            # Connect refused or OS error — port is free.
            return
    raise AssertionError(
        f"Port {host}:{port} is still accepting connections "
        f"{_PORT_FREE_TIMEOUT}s after the server was stopped.  "
        "The uvicorn process may still be running."
    )


def _stop_server(pid: int, host: str, port: int) -> None:
    """Send SIGTERM to *pid*, wait for it to exit, then assert the port is free.

    Called unconditionally from the fixture's finally block.  Safe to call
    even when the process has already exited (kill returns non-zero, which is
    caught and ignored).

    Args:
        pid:  PID of the uvicorn subprocess.
        host: Bound host address (used for the port-free assertion).
        port: Bound port (used for the port-free assertion).
    """
    # Send SIGTERM; ignore errors if the process is already gone.
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass  # process already exited; nothing to kill

    # Wait for the process to exit.
    deadline = time.monotonic() + _SIGTERM_WAIT_SECONDS
    while time.monotonic() < deadline:
        try:
            result = os.waitpid(pid, os.WNOHANG)
            if result != (0, 0):
                # Process has exited.
                break
        except ChildProcessError:
            # Process was not a direct child (e.g. was already reaped),
            # or the PID no longer exists; treat as exited.
            break
        time.sleep(0.1)
    else:
        # SIGTERM did not work within the window; escalate to SIGKILL.
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass

    # Actively verify the port is free before returning to the test.
    _assert_port_free(host, port)


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


@contextmanager
def api_server_fixture(
    tmp_path: pathlib.Path,
    port: int = 0,
    kanban_root: pathlib.Path | None = None,
) -> Generator[ServerHandle, None, None]:
    """Context-managed API server that is guaranteed to stop on exit.

    Starts a real uvicorn subprocess on an ephemeral port, yields a
    ServerHandle with the bound port and PID, then stops the subprocess in a
    finally block regardless of whether the caller raised an exception.

    Args:
        tmp_path:     Base temp directory for this fixture instance.  Used for
                      the server log file.  Must exist; caller is responsible
                      for providing a valid path (e.g. pytest's tmp_path).
        port:         TCP port to bind.  Pass 0 (default) to let the OS assign
                      an ephemeral port.  Never pass 8300.
        kanban_root:  Value of PGAI_AGENT_KANBAN_ROOT_PATH for the subprocess.
                      When None, a minimal temp directory is created so the
                      server starts without a real kanban installation.

    Yields:
        ServerHandle(port=<bound_port>, pid=<uvicorn_pid>)

    Raises:
        ValueError:   When *port* is 8300.
        RuntimeError: When the server does not accept connections within
                      _STARTUP_TIMEOUT_SECONDS seconds.
        AssertionError: (from _assert_port_free) when the port is still open
                        after teardown.
    """
    if port == _PRODUCTION_DEFAULT_PORT:
        raise ValueError(
            f"api_server_fixture: port {_PRODUCTION_DEFAULT_PORT} is the "
            "production default and must never be used in tests.  "
            "Pass port=0 (default) or a high ephemeral port."
        )

    # Resolve the actual port.
    actual_port = port if port != 0 else _find_free_port()

    # Ensure the fixture never silently binds 8300.
    assert actual_port != _PRODUCTION_DEFAULT_PORT, (
        f"Resolved port is {_PRODUCTION_DEFAULT_PORT} — production default.  "
        "This must not happen; see _find_free_port()."
    )

    # Resolve kanban root for the subprocess.
    if kanban_root is None:
        kanban_root = tmp_path / "api_server_kanban_root"
        kanban_root.mkdir(parents=True, exist_ok=True)

    # Server log file: lives under tmp_path so the anti-pattern linter is
    # satisfied (tmp_path is routed to PGAI_AGENT_KANBAN_TEMP_DIR/tests).
    log_file = tmp_path / "api_server.log"

    # Build the subprocess command.  Launch uvicorn directly (not via
    # api-server.sh) so we control the port argument precisely.  We set
    # workers=1 to match the production configuration and to ensure a single
    # child PID to track.
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "pgai_agent_kanban.api.app:create_app",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        str(actual_port),
        "--workers",
        "1",
    ]

    # Build the subprocess environment: propagate current env, then set the
    # kanban root so the app finds its config.  Explicitly clear PGAI_AGENT_KANBAN_ROOT_PATH
    # from the parent and replace it with the fixture root so that the subprocess
    # does not inherit the live-install path from the test harness.
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)

    # Ensure the dev-tree package is importable inside the subprocess.
    # The parent conftest ensures PGAI_DEV_TREE_PATH and PYTHONPATH are set for
    # the test session; we propagate them as-is.
    dev_tree = env.get("PGAI_DEV_TREE_PATH", "")
    if dev_tree and (pathlib.Path(dev_tree) / "team").is_dir():
        current_pythonpath = env.get("PYTHONPATH", "")
        team_path = str(pathlib.Path(dev_tree) / "team")
        if team_path not in current_pythonpath.split(os.pathsep):
            env["PYTHONPATH"] = (
                team_path + os.pathsep + current_pythonpath
                if current_pythonpath
                else team_path
            )

    # Start the server subprocess.  stdout/stderr go to the log file.
    # stdin is redirected to devnull so the subprocess does not block reading.
    proc = subprocess.Popen(
        cmd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log_file.open("wb"),
        stderr=subprocess.STDOUT,
    )

    pid = proc.pid
    host = "127.0.0.1"

    try:
        # Wait for the server to accept connections.
        is_up = _wait_for_port_open(host, actual_port, _STARTUP_TIMEOUT_SECONDS)
        if not is_up:
            # Read the log to provide a useful error message.
            log_text = ""
            try:
                log_text = log_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
            raise RuntimeError(
                f"api_server_fixture: server (pid {pid}) did not accept connections "
                f"on {host}:{actual_port} within {_STARTUP_TIMEOUT_SECONDS}s.  "
                f"Server log:\n{log_text}"
            )

        yield ServerHandle(port=actual_port, pid=pid)

    finally:
        # Guaranteed teardown: stop by recorded PID, then assert port is free.
        _stop_server(pid, host, actual_port)
        # Reap the subprocess to prevent it from becoming a zombie.
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


# ---------------------------------------------------------------------------
# pytest fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def api_server(tmp_path: pathlib.Path) -> Generator[ServerHandle, None, None]:
    """Pytest fixture: start the API server on an ephemeral port.

    Yields a ServerHandle for the duration of the test, then stops the
    server in a finally block and asserts the port is free.

    The fixture never binds port 8300 (the production default).

    Example usage in a test:

        def test_health_endpoint(api_server):
            import urllib.request
            url = f"http://127.0.0.1:{api_server.port}/health"
            resp = urllib.request.urlopen(url)
            assert resp.status == 200

    Attributes of the yielded ServerHandle:
        port (int) — the ephemeral port the server is listening on
        pid  (int) — the PID of the uvicorn subprocess

    The fixture is re-exported from both team/tests/unit/conftest.py and
    team/tests/integration/conftest.py so tests can request it by name without
    importing this module directly.
    """
    with api_server_fixture(tmp_path) as handle:
        yield handle
