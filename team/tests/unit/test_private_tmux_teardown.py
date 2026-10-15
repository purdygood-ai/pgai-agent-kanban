"""
test_private_tmux_teardown.py
=============================
The shared private-tmux teardown (teardown_private_tmux in
tests/fixtures/binary_guard.py) stops the fixture's server and removes its
socket file whatever the test's outcome.

The outcome check runs a real inner pytest session whose fixture starts a
private tmux server the way the dashboard tests do and tears it down
through the helper. The session holds one passing test, one failing test
and one test whose fixture errors after its server started. The tmux
directory lives under a temporary ``TMUX_TMPDIR``, and the ``pgai-*``
sockets in it are counted before and after: the session must add none.
Each inner test also records that its socket existed while it ran, so the
"none left" result cannot come from servers that never started.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import textwrap

from pgai_agent_kanban.testing.tmux_sockets import socket_path, tmux_socket_dir
from tests.fixtures.binary_guard import require_binary

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]

_INNER_TESTS = textwrap.dedent(
    '''
    import os
    import pathlib
    import subprocess
    import uuid

    import pytest

    from pgai_agent_kanban.testing.tmux_sockets import socket_path
    from tests.fixtures.binary_guard import teardown_private_tmux, try_resolve_binary

    _MARKS = pathlib.Path(os.environ["INNER_MARK_DIR"])


    def _start(name):
        guard = try_resolve_binary("tmux")
        socket_name = f"pgai-test-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        return guard, socket_name


    def _launch(guard, socket_name, name):
        subprocess.run(
            [guard.path, "-L", socket_name, "-f", "/dev/null", "new-session", "-d", "-s", "s"],
            check=True, timeout=15,
        )
        (_MARKS / name).write_text(str(socket_path(socket_name).is_socket()))


    @pytest.fixture
    def private_server(request):
        guard, socket_name = _start(request.node.name)
        try:
            _launch(guard, socket_name, request.node.name)
            yield socket_name
        finally:
            teardown_private_tmux(guard, socket_name)


    @pytest.fixture
    def private_server_then_setup_error(request):
        guard, socket_name = _start(request.node.name)
        try:
            _launch(guard, socket_name, request.node.name)
            raise RuntimeError("fixture setup failed after its server started")
            yield socket_name  # pragma: no cover
        finally:
            teardown_private_tmux(guard, socket_name)


    def test_passes(private_server):
        assert private_server


    def test_fails(private_server):
        assert False, "deliberate failure"


    def test_errors(private_server_then_setup_error):
        pass  # pragma: no cover
    '''
)


def _pgai_sockets(directory: pathlib.Path) -> set[str]:
    if not directory.is_dir():
        return set()
    return {p.name for p in directory.iterdir() if p.name.startswith("pgai-") and p.is_socket()}


def test_socket_path_follows_tmux_tmpdir() -> None:
    uid = os.getuid()
    assert socket_path("pgai-x", {"TMUX_TMPDIR": "/srv/t"}) == pathlib.Path(f"/srv/t/tmux-{uid}/pgai-x")
    # tmux's own fallback when TMUX_TMPDIR is unset or empty is /tmp.
    assert str(socket_path("pgai-x", {"TMUX_TMPDIR": ""})) == f"/tmp/tmux-{uid}/pgai-x"
    assert str(tmux_socket_dir({})) == f"/tmp/tmux-{uid}"


def test_passing_failing_and_erroring_uses_leave_no_socket(
    tmp_path: pathlib.Path, pgai_tests_temp_dir: pathlib.Path
) -> None:
    guard = require_binary("tmux", label="private-tmux-teardown")
    # Short on purpose: a long TMUX_TMPDIR can push the socket path past the
    # kernel's unix-socket length limit.
    tmux_tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="tmx", dir=str(pgai_tests_temp_dir)))
    try:
        _run_inner_session_and_check(tmp_path, tmux_tmpdir)
    finally:
        # Whatever the verdict, stop any server still on a socket here and
        # remove the directory, so a regression in the helper leaks nothing.
        for leftover in tmux_tmpdir.rglob("*"):
            if leftover.is_socket():
                subprocess.run([guard.path, "-S", str(leftover), "kill-server"],
                               capture_output=True, timeout=10)
        shutil.rmtree(tmux_tmpdir, ignore_errors=True)


def _run_inner_session_and_check(tmp_path: pathlib.Path, tmux_tmpdir: pathlib.Path) -> None:
    marks = tmp_path / "marks"
    marks.mkdir()
    inner_dir = tmp_path / "inner"
    inner_dir.mkdir()
    (inner_dir / "test_inner_private_tmux.py").write_text(_INNER_TESTS, encoding="utf-8")

    env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE", "PYTEST_ADDOPTS")}
    env.update(
        TMUX_TMPDIR=str(tmux_tmpdir),
        PYTHONPATH=str(_TEAM_DIR),
        INNER_MARK_DIR=str(marks),
    )
    socket_dir = tmux_socket_dir(env)
    before = _pgai_sockets(socket_dir)

    result = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "no:randomly",
            "--rootdir", str(inner_dir), "-q", "-rA", str(inner_dir / "test_inner_private_tmux.py"),
        ],
        capture_output=True, text=True, env=env, cwd=str(inner_dir), timeout=120,
    )

    diagnostic = f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    assert "1 failed, 1 passed, 1 error" in result.stdout, diagnostic
    recorded = {p.name: p.read_text() for p in marks.iterdir()}
    assert recorded == {"test_passes": "True", "test_fails": "True", "test_errors": "True"}, (
        f"each inner test must have had a live socket to clean up: {recorded}\n{diagnostic}"
    )
    after = _pgai_sockets(socket_dir)
    assert after - before == set(), (
        f"the private-tmux teardown left sockets behind in {socket_dir}: "
        f"{sorted(after - before)}\n{diagnostic}"
    )
