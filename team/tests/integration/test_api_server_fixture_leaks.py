"""
test_api_server_fixture_leaks.py
================================
Synthetic tests that verify the api_server_fixture's teardown guarantees and
the runner post-flight listener cleanliness check.

Two scenarios are exercised:

Positive test — injected-exception path leaves no listener
-----------------------------------------------------------
The fixture is entered, the server starts on an ephemeral port, then an
exception is raised inside the with-block BEFORE the caller explicitly stops
anything.  The fixture's finally block must fire regardless, stop the server,
and release the port.  After the context manager exits (via the exception),
this test asserts that a TCP connect to the recorded port is refused — proving
that the fixture's teardown guarantee holds even on exception paths.

Negative test — intentional leak detected by the post-flight helper
--------------------------------------------------------------------
A raw subprocess is spawned that binds an ephemeral port and holds it open.
The subprocess's working directory is set to a path under the framework temp
root so the post-flight helper (`pgai_listener_cleanliness_check` from
team/scripts/lib/temp.sh) can detect it.  The helper is invoked directly and
is expected to return non-zero with the leaked pid printed to stderr.  The
test asserts both outcomes.  The subprocess is unconditionally killed in a
finally block (the test's own hygiene) before the test returns, so no
framework-rooted listener leaks from this test into the suite's post-flight
check.

Constraints honoured
--------------------
- No fixture binds port 8300.
- Ephemeral ports only (port 0 / OS-assigned).
- The negative test's leaked subprocess is killed in a finally block so it
  does not survive into the post-flight listener gate that the gated runners
  run after pytest exits.
- All temp paths route through pytest's tmp_path (which conftest.py redirects
  to $PGAI_AGENT_KANBAN_TEMP_DIR/tests on framework-managed runs).

Test-name policy (SOP.md Anti-pattern 6):
  Names describe behavior, not bug IDs or scaffolding labels.
"""

from __future__ import annotations

import os
import pathlib
import socket
import subprocess
import sys
import textwrap
import time

import pytest

from tests.fixtures.api_server import ServerHandle, api_server_fixture

# ---------------------------------------------------------------------------
# Paths used across this module
# ---------------------------------------------------------------------------

# Parent of this file: team/tests/integration/
_INTEGRATION_DIR = pathlib.Path(__file__).parent
# team/
_TEAM_DIR = _INTEGRATION_DIR.parent.parent
# team/scripts/lib/temp.sh  — contains pgai_listener_cleanliness_check
_TEMP_SH = _TEAM_DIR / "scripts" / "lib" / "temp.sh"

# Production default port — asserted absent from all fixture bindings.
_PRODUCTION_DEFAULT_PORT = 8300


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_port_free(host: str, port: int, *, timeout: float = 5.0) -> bool:
    """Return True when (host, port) refuses connections within *timeout* seconds.

    Polls by attempting TCP connects.  Returns True as soon as a connect is
    refused; returns False if the port is still accepting connections at timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                # Still open — keep polling.
                time.sleep(0.1)
        except (ConnectionRefusedError, OSError):
            return True
    return False


def _find_free_port() -> int:
    """Return a free ephemeral TCP port on 127.0.0.1 (never 8300)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert port != _PRODUCTION_DEFAULT_PORT, (
        f"OS assigned port {_PRODUCTION_DEFAULT_PORT} — production default; refusing."
    )
    return port


def _run_listener_cleanliness_check() -> subprocess.CompletedProcess:
    """Source temp.sh and call pgai_listener_cleanliness_check via bash subprocess.

    Returns the CompletedProcess so the caller can inspect returncode and stderr.
    """
    script = f"source {str(_TEMP_SH)!r} && pgai_listener_cleanliness_check"
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# Positive test: injected-exception path leaves no listener
# ---------------------------------------------------------------------------


def test_injected_exception_leaves_no_listener(tmp_path: pathlib.Path) -> None:
    """The fixture stops the server and frees the port even when the caller raises.

    Steps:
      1. Enter api_server_fixture as a context manager.
      2. Record the port and pid from the returned ServerHandle.
      3. Assert the port is open (server is up) — confirms startup succeeded.
      4. Raise a RuntimeError inside the with-block to simulate a test failure.
      5. After the context manager exits (via the exception path), assert the
         port is now free — proves the finally block ran and teardown completed.

    The exception is caught by pytest.raises so the test itself passes on the
    healthy path; the port-free assertion is the behavioral guarantee under test.
    """
    recorded_port: int | None = None

    with pytest.raises(RuntimeError, match="injected exception"):
        with api_server_fixture(tmp_path) as handle:
            assert handle.port != _PRODUCTION_DEFAULT_PORT, (
                f"Fixture unexpectedly bound the production default port {_PRODUCTION_DEFAULT_PORT}."
            )
            recorded_port = handle.port

            # Confirm the server is up (port is open) before injecting the
            # exception — ensures the fixture actually started the server.
            try:
                with socket.create_connection(("127.0.0.1", recorded_port), timeout=2):
                    pass
            except (ConnectionRefusedError, OSError) as exc:
                pytest.fail(
                    f"Server on port {recorded_port} is not accepting connections "
                    f"before the injected exception: {exc}"
                )

            # Inject the exception.  The fixture's finally block must fire and
            # stop the server regardless of this raise.
            raise RuntimeError("injected exception — fixture teardown must still run")

    # Context manager has exited via the exception path.  Assert the port is free.
    assert recorded_port is not None, "recorded_port was never set — fixture did not start"
    port_is_free = _is_port_free("127.0.0.1", recorded_port, timeout=6.0)
    assert port_is_free, (
        f"Port 127.0.0.1:{recorded_port} is still accepting connections after "
        "the fixture exited via the injected-exception path.  "
        "The fixture's teardown guarantee is broken: a listener leaked."
    )


# ---------------------------------------------------------------------------
# Negative test: intentional leak detected by the post-flight helper
# ---------------------------------------------------------------------------


def test_intentional_leaked_listener_detected_by_postflight_helper(
    tmp_path: pathlib.Path,
) -> None:
    """The post-flight helper returns non-zero and names the pid of a leaked listener.

    This test proves that the runner's post-flight listener check can detect a
    listener whose owning process is rooted under the framework temp directory.

    Steps:
      1. Find a free ephemeral port (never 8300).
      2. Spawn a raw subprocess that binds the port and holds it open.  The
         subprocess's cwd is set to a directory under tmp_path (which conftest.py
         routes under $PGAI_AGENT_KANBAN_TEMP_DIR/tests on framework runs),
         so the post-flight check's basename scan picks it up.
      3. Wait for the subprocess to signal readiness (it writes the bound port
         to stdout so the test can confirm the port is live).
      4. Call pgai_listener_cleanliness_check directly and assert it returns
         non-zero with the subprocess's pid appearing in stderr.
      5. Kill the subprocess in a finally block (the test's own hygiene) before
         returning, so the leaked listener does not survive into the suite-wide
         post-flight check that the gated runners execute after pytest exits.
    """
    ephemeral_port = _find_free_port()

    # A small Python script that:
    #   - Binds the specified port on 127.0.0.1 (SO_REUSEADDR so we can rebind
    #     quickly if the earlier probe left the port in TIME_WAIT).
    #   - Prints "READY:<port>" to stdout so the parent knows the socket is bound.
    #   - Loops sleeping until killed.
    #
    # The script is passed as -c so the cmdline does not reference any path, but
    # the cwd will contain the framework temp root basename, which is what the
    # pgai_listener_cleanliness_check uses to match framework-rooted listeners.
    listener_script = textwrap.dedent(f"""\
        import socket, sys, time
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", {ephemeral_port}))
        s.listen(1)
        sys.stdout.write("READY:" + str({ephemeral_port}) + "\\n")
        sys.stdout.flush()
        # Hold the socket open until killed.
        while True:
            time.sleep(1)
    """)

    # Create a working directory under tmp_path so the subprocess cwd contains
    # the framework temp root basename (pgai_kanban_tmp when using the default,
    # or whatever $PGAI_AGENT_KANBAN_TEMP_DIR resolves to).  The post-flight
    # check inspects /proc/<pid>/cwd against the basename, so this is the
    # mechanism by which the check identifies framework-rooted listeners.
    leak_cwd = tmp_path / "leaked_listener_workdir"
    leak_cwd.mkdir(parents=True, exist_ok=True)

    leaked_proc: subprocess.Popen | None = None
    try:
        leaked_proc = subprocess.Popen(
            [sys.executable, "-c", listener_script],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=str(leak_cwd),
        )
        leaked_pid = leaked_proc.pid

        # Wait for the subprocess to signal readiness (prints "READY:<port>").
        assert leaked_proc.stdout is not None
        ready_line = leaked_proc.stdout.readline().decode("utf-8", errors="replace").strip()
        assert ready_line.startswith("READY:"), (
            f"Leaked listener subprocess did not emit READY signal; got: {ready_line!r}"
        )

        # Confirm the listener is actually bound.
        try:
            with socket.create_connection(("127.0.0.1", ephemeral_port), timeout=2):
                pass
        except (ConnectionRefusedError, OSError) as exc:
            pytest.fail(
                f"Leaked listener on port {ephemeral_port} (pid {leaked_pid}) "
                f"is not accepting connections: {exc}"
            )

        # --- Invoke the post-flight helper directly ---
        result = _run_listener_cleanliness_check()

        # The helper must return non-zero: at least one framework-rooted listener
        # is alive (the one we just spawned with cwd under tmp_path).
        assert result.returncode != 0, (
            f"pgai_listener_cleanliness_check returned 0 (expected non-zero) when "
            f"a framework-rooted listener is alive on port {ephemeral_port} "
            f"(pid {leaked_pid}).  stderr: {result.stderr!r}"
        )

        # The helper must name the leaked pid in its stderr output.
        assert str(leaked_pid) in result.stderr, (
            f"pgai_listener_cleanliness_check stderr does not mention the leaked "
            f"pid {leaked_pid}.  stderr: {result.stderr!r}"
        )

    finally:
        # The test's own hygiene: kill the intentionally leaked listener before
        # returning so it does not survive into the suite-wide post-flight check
        # that the gated runners execute after pytest exits.
        if leaked_proc is not None:
            try:
                leaked_proc.kill()
                leaked_proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass
