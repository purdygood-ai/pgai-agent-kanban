"""
test_gated_runner_tmux_socket_sweep.py
======================================
The gated runners' post-run tmux socket sweep
(gated_runner_tmux_socket_sweep in team/scripts/lib/gated_runner_lint.sh,
rules in pgai_agent_kanban.testing.tmux_sockets) removes a stale ``pgai-*``
socket -- one nothing is listening on -- and leaves alone a ``pgai-*``
socket with a live tmux server, a stale socket under any other name, and a
stale ``pgai-*`` socket too young to judge.

Every case runs the real bash function under a temporary ``TMUX_TMPDIR``,
so the operator's tmux directory is never read or written. A stale socket
is made the way a stopped server leaves one: bound, then closed without a
listener.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import socket
import subprocess
import tempfile
import time

from tests.fixtures.binary_guard import require_binary, teardown_private_tmux

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_LINT_LIB = _TEAM_DIR / "scripts" / "lib" / "gated_runner_lint.sh"
_AN_HOUR_AGO = time.time() - 3600


def _stale_socket(path: pathlib.Path, *, old: bool = True) -> pathlib.Path:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(path))
    sock.close()
    if old:
        os.utime(path, (_AN_HOUR_AGO, _AN_HOUR_AGO))
    return path


def _sweep(env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", 'source "$1" && gated_runner_tmux_socket_sweep', "sweep", str(_LINT_LIB)],
        capture_output=True, text=True, env=env, timeout=60, cwd=str(_TEAM_DIR),
    )


def test_sweep_removes_only_stale_pgai_sockets(pgai_tests_temp_dir: pathlib.Path) -> None:
    guard = require_binary("tmux", label="gated-runner-tmux-socket-sweep")
    # Short on purpose: a long TMUX_TMPDIR can push the socket path past the
    # kernel's unix-socket length limit.
    tmux_tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="tmx", dir=str(pgai_tests_temp_dir)))
    env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")}
    env["TMUX_TMPDIR"] = str(tmux_tmpdir)
    socket_dir = tmux_tmpdir / f"tmux-{os.getuid()}"
    socket_dir.mkdir(mode=0o700)
    live_name = f"pgai-live-{os.getpid()}"
    try:
        stale = _stale_socket(socket_dir / "pgai-test-stale")
        young = _stale_socket(socket_dir / "pgai-test-young", old=False)
        foreign = _stale_socket(socket_dir / "other-stale")
        subprocess.run(
            [guard.path, "-L", live_name, "-f", "/dev/null", "new-session", "-d", "-s", "s"],
            env=env, check=True, capture_output=True, timeout=15,
        )
        live = socket_dir / live_name
        assert live.is_socket(), f"tmux did not create its socket at {live}"
        os.utime(live, (_AN_HOUR_AGO, _AN_HOUR_AGO))

        result = _sweep(env)

        diagnostic = f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        assert result.returncode == 0, diagnostic
        assert "[gated-runner] tmux_socket_sweep: removed 1 stale pgai-* socket(s)" in result.stdout, diagnostic
        assert not stale.exists(), "a stale pgai-* socket must be removed\n" + diagnostic
        assert live.is_socket(), "a socket with a live server must be kept\n" + diagnostic
        alive = subprocess.run(
            [guard.path, "-L", live_name, "has-session", "-t", "s"],
            env=env, capture_output=True, timeout=10,
        )
        assert alive.returncode == 0, "the sweep must never stop a live server\n" + diagnostic
        assert foreign.is_socket(), "a socket not named pgai-* must be kept\n" + diagnostic
        assert young.is_socket(), "a socket younger than the minimum age must be kept\n" + diagnostic

        again = _sweep(env)
        assert "tmux_socket_sweep: OK" in again.stdout, again.stdout + again.stderr
    finally:
        teardown_private_tmux(guard, live_name, env=env)
        shutil.rmtree(tmux_tmpdir, ignore_errors=True)


def test_sweep_with_no_tmux_directory_is_a_quiet_no_op(tmp_path: pathlib.Path) -> None:
    env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")}
    env["TMUX_TMPDIR"] = str(tmp_path / "absent")

    result = _sweep(env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "tmux_socket_sweep: OK" in result.stdout, result.stdout + result.stderr
