"""
test_stdin_closed.py — The operator API never hands its own stdin to a
fronted script.

The hazard: a fronted operator script that prompts (``read -r x``) would,
if it inherited the server's stdin, block on the terminal the server was
started from and the HTTP request would never return. The guard is that
every subprocess the API launches gets ``stdin=subprocess.DEVNULL``, so a
prompting script reads EOF at once, fails loud with its own message, and
the envelope carries that message back to the caller.

Each test first makes the hazard real -- ``held_open_stdin`` replaces fd 0
of the test process with the read end of a pipe whose write end stays open,
so a child that DOES inherit stdin blocks on ``read`` until the fixture
releases it -- and then runs the protected operation under a bounded
timeout. A hang shows up as a timed-out join, never as a suite that sits
waiting for a keyboard. Three lanes are covered:

  1. ``adapter.shell_out`` — the blocking bridge every action route uses.
  2. ``operations._spawn_detached`` — the fork-and-return path for the
     long-running verbs, whose child outlives the request.
  3. The HTTP surface itself, via TestClient: POST /operations/wake-now in
     its foreground form (a ``shell_out`` route), fronting a scratch
     scripts/ directory whose script prompts. Routes that call their ops function in process start
     no child at all, so they have no stdin to hand over.

No test here needs a TTY, and none reads the real stdin.
"""

from __future__ import annotations

import os
import pathlib
import stat
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Generator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from pgai_agent_kanban.api.adapter import ShellResult, shell_out
from pgai_agent_kanban.api.routers import operations as operations_module

# How long a fronted call may take before the test calls it a hang. The
# expected return is sub-second (bash starts, ``read`` hits EOF, exits), so
# this bound only has to absorb a loaded CI host, never a prompt.
_RETURN_TIMEOUT_SECONDS = 15.0

# What the prompting stub prints when its ``read`` gets EOF instead of an
# answer; the tests assert this reaches the caller as the script's own
# stderr.
_STUB_REFUSAL_TEXT = "no answer on stdin: refusing"
_STUB_EXIT_CODE = 3


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@dataclass
class _HeldOpenStdin:
    """Handle on the pipe standing in for the process's stdin.

    ``release()`` closes the write end so any child still blocked on the
    inherited read end sees EOF and can exit; it is idempotent so both a
    test's timeout path and the fixture teardown may call it.
    """

    write_fd: int | None

    def release(self) -> None:
        if self.write_fd is not None:
            os.close(self.write_fd)
            self.write_fd = None


@pytest.fixture
def held_open_stdin() -> Generator[_HeldOpenStdin, None, None]:
    """Put a never-written pipe on fd 0 for the duration of the test.

    A child that inherits stdin now blocks on ``read`` exactly as it would
    on an idle terminal; a child launched with ``stdin=DEVNULL`` is
    unaffected. The original fd 0 is restored on teardown, after the write
    end is closed so no blocked child is left behind.
    """
    read_fd, write_fd = os.pipe()
    saved_stdin = os.dup(0)
    os.dup2(read_fd, 0)
    os.close(read_fd)
    handle = _HeldOpenStdin(write_fd=write_fd)
    try:
        yield handle
    finally:
        handle.release()
        os.dup2(saved_stdin, 0)
        os.close(saved_stdin)


def _write_prompting_stub(path: pathlib.Path) -> str:
    """Write an executable bash script that prompts on stdin.

    With an answer it exits 0; at EOF (a closed stdin) ``read`` fails and
    the script prints ``_STUB_REFUSAL_TEXT`` to stderr and exits
    ``_STUB_EXIT_CODE`` -- the shape a real prompting operator script has.
    """
    path.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'Proceed? '\n"
        "if ! read -r x; then\n"
        f"  printf '%s\\n' '{_STUB_REFUSAL_TEXT}' >&2\n"
        f"  exit {_STUB_EXIT_CODE}\n"
        "fi\n"
        "printf 'answered: %s\\n' \"$x\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


def _call_with_timeout(
    fn: Callable[[], Any],
    stdin: _HeldOpenStdin,
    what: str,
) -> Any:
    """Run *fn* on a helper thread and return its result within the bound.

    On timeout the held-open pipe is released first (so the blocked child
    exits and the helper thread can finish) and the test fails naming the
    call that hung. The helper thread is a daemon so a still-blocked call
    cannot pin the interpreter at exit either.
    """
    outcome: dict[str, Any] = {}

    def _runner() -> None:
        try:
            outcome["result"] = fn()
        except BaseException as exc:  # surfaced on the test thread below
            outcome["error"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join(_RETURN_TIMEOUT_SECONDS)
    if thread.is_alive():
        stdin.release()
        thread.join(5.0)
        pytest.fail(
            f"{what} did not return within {_RETURN_TIMEOUT_SECONDS}s: the "
            "fronted script was left blocked on the server's stdin, so the "
            "child inherited fd 0 instead of getting stdin=subprocess.DEVNULL"
        )
    if "error" in outcome:
        raise outcome["error"]
    return outcome["result"]


def _wait_for_pid(pid: int, deadline_seconds: float) -> int | None:
    """Poll *pid* until it exits or the deadline passes.

    Returns the child's exit code, or None when it is still running at the
    deadline (the hang this suite guards against). A child already reaped
    by the interpreter's own subprocess bookkeeping is reported as exit
    code -1 so the caller falls back to the log file for evidence.
    """
    stop_at = time.monotonic() + deadline_seconds
    while time.monotonic() < stop_at:
        try:
            done_pid, status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return -1
        if done_pid == pid:
            return os.waitstatus_to_exitcode(status)
        time.sleep(0.05)
    return None


# ---------------------------------------------------------------------------
# Lane 1: adapter.shell_out
# ---------------------------------------------------------------------------


def test_shell_out_prompting_script_returns_with_its_own_refusal(
    tmp_path: pathlib.Path, held_open_stdin: _HeldOpenStdin
) -> None:
    """shell_out returns promptly, non-zero, with the script's stderr, when the script prompts."""
    stub = _write_prompting_stub(tmp_path / "prompts.sh")

    result = _call_with_timeout(
        lambda: shell_out(stub, flags={}), held_open_stdin, "shell_out()"
    )

    assert isinstance(result, ShellResult)
    assert result.exit_code == _STUB_EXIT_CODE
    assert _STUB_REFUSAL_TEXT in result.stderr
    assert "answered:" not in result.stdout


def test_shell_out_passes_devnull_stdin_to_the_process_it_starts(tmp_path: pathlib.Path) -> None:
    """The subprocess.Popen call shell_out makes names stdin=DEVNULL explicitly."""
    stub = _write_prompting_stub(tmp_path / "prompts.sh")
    real_popen = subprocess.Popen

    with patch("pgai_agent_kanban.api.adapter.subprocess.Popen", side_effect=real_popen) as mock_popen:
        shell_out(stub, flags={})

    mock_popen.assert_called_once()
    assert mock_popen.call_args.kwargs.get("stdin") is subprocess.DEVNULL


# ---------------------------------------------------------------------------
# Lane 2: operations._spawn_detached
# ---------------------------------------------------------------------------


def test_spawn_detached_prompting_script_exits_with_its_own_refusal(
    tmp_path: pathlib.Path, held_open_stdin: _HeldOpenStdin
) -> None:
    """A detached prompting child exits promptly and logs its refusal rather than waiting on stdin."""
    stub = _write_prompting_stub(tmp_path / "prompts.sh")
    log_path = tmp_path / "logs" / "detached.log"

    pid = operations_module._spawn_detached([stub], log_path)
    exit_code = _wait_for_pid(pid, _RETURN_TIMEOUT_SECONDS)

    if exit_code is None:
        held_open_stdin.release()
        _wait_for_pid(pid, 5.0)
        pytest.fail(
            f"_spawn_detached child pid={pid} was still running after "
            f"{_RETURN_TIMEOUT_SECONDS}s: it inherited the server's stdin and "
            "blocked on the script's prompt"
        )
    if exit_code != -1:
        assert exit_code == _STUB_EXIT_CODE
    logged = log_path.read_text(encoding="utf-8")
    assert _STUB_REFUSAL_TEXT in logged
    assert "answered:" not in logged


def test_spawn_detached_passes_devnull_stdin_to_popen(tmp_path: pathlib.Path) -> None:
    """The Popen call _spawn_detached makes names stdin=DEVNULL explicitly."""
    stub = _write_prompting_stub(tmp_path / "prompts.sh")
    log_path = tmp_path / "logs" / "detached.log"

    with patch.object(operations_module.subprocess, "Popen") as mock_popen:
        mock_popen.return_value.pid = 4242
        pid = operations_module._spawn_detached([stub], log_path)

    assert pid == 4242
    mock_popen.assert_called_once()
    assert mock_popen.call_args.kwargs.get("stdin") is subprocess.DEVNULL


# ---------------------------------------------------------------------------
# Lane 3: the HTTP surface
# ---------------------------------------------------------------------------


def _client_fronting(scratch_root: pathlib.Path) -> TestClient:
    """A TestClient whose scripts/ directory is *scratch_root*/scripts."""
    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    cfg = ApiConfig(host="127.0.0.1", port=0, kanban_root=scratch_root)
    return TestClient(create_app(cfg=cfg), raise_server_exceptions=True)


@pytest.mark.parametrize(
    ("route", "script_name", "body"),
    [
        pytest.param(
            "/operations/wake-now",
            "wake-now.sh",
            {"agent": "coder"},
            id="wake-now-foreground-via-shell_out",
        ),
    ],
)
def test_route_fronting_prompting_script_returns_500_with_its_stderr(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    held_open_stdin: _HeldOpenStdin,
    route: str,
    script_name: str,
    body: dict,
) -> None:
    """A POST whose script prompts returns 500 promptly with the script's own message in stderr."""
    scratch_root = tmp_path / "root"
    (scratch_root / "scripts").mkdir(parents=True)
    _write_prompting_stub(scratch_root / "scripts" / script_name)
    # Keep anything a route writes under the framework temp root inside the
    # test's own sandbox.
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(tmp_path / "tmp"))

    with _client_fronting(scratch_root) as client:
        response = _call_with_timeout(
            lambda: client.post(route, json=body), held_open_stdin, f"POST {route}"
        )

    assert response.status_code == 500, response.text
    envelope = response.json()
    assert envelope["exit_code"] == _STUB_EXIT_CODE
    assert _STUB_REFUSAL_TEXT in envelope["stderr"]
    assert "answered:" not in envelope["stdout"]
