"""
test_dashboard_status_bottom_loop.py
====================================
team/scripts/dashboard/status-bottom-loop.sh renders the bottom status line
into a tmux user option on a fixed cadence, so status-format[2] reads it
with no fork.

Pinned on a private tmux server (tmux -L <private>) with a bare session and
a counting renderer stub (it appends one line per start to a file):

  * one render at start, then none while no client is attached;
  * renders resume, one per interval, while a client is attached, and the
    rendered text lands in the session's option;
  * a render that prints nothing leaves the previous line in the option,
    and a render that prints a line is stored whatever its exit status;
  * the loop exits once its session is gone;
  * a malformed command line exits 2 and --help exits 0.

Every read of the option that a verdict rests on comes from a wait that
polls the option itself, so the verdict is what the option demonstrably
held, and a read tmux answers with no output at all is retried rather
than taken as the value.

Skipped (narrated) when tmux is not on PATH.
"""

from __future__ import annotations

import os
import pathlib
import pty
import re
import shlex
import subprocess
import sys
import threading
import time
import uuid
from typing import Callable, Iterator, NamedTuple, Optional

import pytest

from tests.fixtures.binary_guard import require_binary, teardown_private_tmux
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_LOOP = _TEAM_DIR / "scripts" / "dashboard" / "status-bottom-loop.sh"
_OPTION = "@pgai_status_bottom"
_INTERVAL = 1


class PrivateTmux(NamedTuple):
    real_tmux: str
    socket_name: str
    env: dict

    def run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [self.real_tmux, "-L", self.socket_name, *args],
            capture_output=True, text=True, timeout=15, check=False,
        )


@pytest.fixture
def private_tmux(tmp_path: pathlib.Path) -> Iterator[PrivateTmux]:
    guard = require_binary("tmux", label="status-bottom-loop")
    socket_name = f"pgai-test-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    wrapper = bin_dir / "tmux"
    wrapper.write_text(
        f"#!/usr/bin/env bash\nexec {shlex.quote(guard.path)} -L {shlex.quote(socket_name)} \"$@\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    env = build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[bin_dir, pathlib.Path(sys.executable).parent],
        extra_env={"PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "scratch"), "TERM": "xterm-256color"},
    )
    try:
        yield PrivateTmux(guard.path, socket_name, env)
    finally:
        teardown_private_tmux(guard, socket_name, env=env)


def _counting_renderer(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    starts = tmp_path / "starts.log"
    renderer = tmp_path / "renderer.sh"
    renderer.write_text(
        f"#!/usr/bin/env bash\necho start >> {shlex.quote(str(starts))}\n"
        "printf 'rendered line %s' \"$(wc -l < " + shlex.quote(str(starts)) + ")\"\n",
        encoding="utf-8",
    )
    renderer.chmod(0o755)
    return renderer, starts


def _starts(starts: pathlib.Path) -> int:
    return len(starts.read_text().splitlines()) if starts.exists() else 0


def _wait_for(predicate, timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise AssertionError(f"timed out after {timeout}s waiting for {what}")


def _option_value(tmux: PrivateTmux, session: str) -> Optional[str]:
    """The session's status option, surrounding whitespace dropped.

    tmux prints a set option's value followed by a newline, so an option
    holding an empty string reads back as an empty line, which is "" here.
    A read that prints nothing at all (the option is unset, or no reply came
    back) says nothing about the value and is None.
    """
    stdout = tmux.run("show-options", "-v", "-t", f"={session}:", _OPTION).stdout
    return stdout.strip() if stdout else None


def _rendered_number(value: Optional[str]) -> int:
    """N from a ``rendered line N`` value; 0 for anything else, empty included."""
    match = re.fullmatch(r"rendered\s+line\s+(\d+)", (value or "").strip())
    return int(match.group(1)) if match else 0


def _wait_for_option(
    tmux: PrivateTmux, session: str, accept: Callable[[Optional[str]], bool], timeout: float, what: str
) -> Optional[str]:
    """Poll the session's option until ``accept`` takes its value; return that value.

    The value returned is the read the verdict rests on, so a test asserts on
    what the option demonstrably held rather than on a single read taken
    after some other readiness signal.
    """
    deadline = time.monotonic() + timeout
    seen = None
    while time.monotonic() < deadline:
        seen = _option_value(tmux, session)
        if accept(seen):
            return seen
        time.sleep(0.1)
    raise AssertionError(f"timed out after {timeout}s waiting for {what}; the option last read {seen!r}")


class _AttachedClient:
    """A tmux client attached through a pseudo-terminal, its output drained."""

    def __init__(self, tmux: PrivateTmux, session: str) -> None:
        master, slave = pty.openpty()
        self._master = master
        self.proc = subprocess.Popen(
            [tmux.real_tmux, "-L", tmux.socket_name, "attach-session", "-t", f"={session}"],
            stdin=slave, stdout=slave, stderr=slave, env=tmux.env, close_fds=True,
        )
        os.close(slave)
        self._reader = threading.Thread(target=self._drain, daemon=True)
        self._reader.start()

    def _drain(self) -> None:
        while True:
            try:
                if not os.read(self._master, 65536):
                    return
            except OSError:
                return

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        os.close(self._master)


def test_loop_renders_only_while_attached_and_ends_with_its_session(
    private_tmux: PrivateTmux, tmp_path: pathlib.Path
) -> None:
    session = f"loop-{uuid.uuid4().hex[:6]}"
    assert private_tmux.run("new-session", "-d", "-s", session, "-x", "120", "-y", "30").returncode == 0
    renderer, starts = _counting_renderer(tmp_path)

    loop = subprocess.Popen(
        ["bash", str(_LOOP), "--session", session, "--interval", str(_INTERVAL), "--", str(renderer)],
        env=private_tmux.env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    try:
        _wait_for_option(
            private_tmux, session, lambda value: value == "rendered line 1", 15,
            "the loop's first render to land in the option",
        )
        # No client: several intervals pass with no further render. Proving
        # an absence needs a window; the verdict is the start count, which
        # only a render moves.
        time.sleep(_INTERVAL * 4 + 0.5)
        assert _starts(starts) == 1, "the loop rendered while no client was attached"

        client = _AttachedClient(private_tmux, session)
        try:
            # Renders resume: the option comes to hold a third render's line,
            # which only a render made while the client is attached can write.
            shown = _wait_for_option(
                private_tmux, session, lambda value: _rendered_number(value) >= 3, 20,
                "a render made while a client is attached to land in the option",
            )
        finally:
            client.close()
        assert shown is not None and shown.startswith("rendered line "), shown
        assert _starts(starts) >= 3, f"the option read {shown!r} after only {_starts(starts)} renders"

        assert private_tmux.run("kill-session", "-t", f"={session}").returncode == 0
        loop.wait(timeout=_INTERVAL * 5 + 10)
        assert loop.returncode == 0, loop.stderr.read() if loop.stderr else ""
    finally:
        if loop.poll() is None:
            loop.kill()
            loop.wait()


def test_an_empty_render_leaves_the_previous_line_in_the_option(
    private_tmux: PrivateTmux, tmp_path: pathlib.Path
) -> None:
    """The first render prints a line and exits 3; every later render prints
    nothing and exits 3. The option keeps the first line throughout.

    The loop renders one tick at a time, so a third renderer start proves the
    loop has finished with the second (empty) render's output -- stored it or
    withheld it -- before the option is read.
    """
    session = f"keep-{uuid.uuid4().hex[:6]}"
    assert private_tmux.run("new-session", "-d", "-s", session, "-x", "120", "-y", "30").returncode == 0
    starts = tmp_path / "starts.log"
    renderer = tmp_path / "renderer.sh"
    renderer.write_text(
        "#!/usr/bin/env bash\n"
        f"echo start >> {shlex.quote(str(starts))}\n"
        f"if [ \"$(wc -l < {shlex.quote(str(starts))})\" -eq 1 ]; then printf 'rendered line 1'; fi\n"
        "exit 3\n",
        encoding="utf-8",
    )
    renderer.chmod(0o755)

    loop = subprocess.Popen(
        ["bash", str(_LOOP), "--session", session, "--interval", str(_INTERVAL), "--", str(renderer)],
        env=private_tmux.env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    try:
        # A line is stored even though its render exited non-zero.
        _wait_for_option(
            private_tmux, session, lambda value: value == "rendered line 1", 15,
            "the first render's line to land in the option",
        )

        client = _AttachedClient(private_tmux, session)
        try:
            _wait_for(lambda: _starts(starts) >= 3, 20, "the loop to move past an empty render")
            # The first read tmux answers with a value is the verdict; an
            # option overwritten with "" would read back as "".
            shown = _wait_for_option(
                private_tmux, session, lambda value: value is not None, 15,
                "tmux to report the option's value",
            )
        finally:
            client.close()
        assert shown == "rendered line 1", (
            f"after {_starts(starts)} renders, the last of them empty, the option read {shown!r}"
        )

        assert private_tmux.run("kill-session", "-t", f"={session}").returncode == 0
        loop.wait(timeout=_INTERVAL * 5 + 10)
        assert loop.returncode == 0, loop.stderr.read() if loop.stderr else ""
    finally:
        if loop.poll() is None:
            loop.kill()
            loop.wait()


def test_loop_exits_at_once_when_the_session_does_not_exist(
    private_tmux: PrivateTmux, tmp_path: pathlib.Path
) -> None:
    renderer, starts = _counting_renderer(tmp_path)
    result = subprocess.run(
        ["bash", str(_LOOP), "--session", "no-such-session", "--interval", "1", "--", str(renderer)],
        env=private_tmux.env, capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert _starts(starts) == 0


@pytest.mark.parametrize(
    "args",
    [
        ["--interval", "5", "--", "true"],
        ["--session", "s", "--", "true"],
        ["--session", "s", "--interval", "0", "--", "true"],
        ["--session", "s", "--interval", "5"],
        ["--session", "s", "--interval", "5", "--option", "status-right", "--", "true"],
    ],
    ids=["no-session", "no-interval", "zero-interval", "no-renderer", "not-a-user-option"],
)
def test_malformed_command_line_exits_2(args: list[str], tmp_path: pathlib.Path) -> None:
    result = subprocess.run(
        ["bash", str(_LOOP), *args], capture_output=True, text=True, timeout=30, check=False,
        env=build_scrubbed_env(home_dir=tmp_path),
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "Usage:" in result.stderr


def test_help_exits_0_and_describes_every_flag(tmp_path: pathlib.Path) -> None:
    result = subprocess.run(
        ["bash", str(_LOOP), "--help"], capture_output=True, text=True, timeout=30, check=False,
        env=build_scrubbed_env(home_dir=tmp_path),
    )
    assert result.returncode == 0, result.stderr
    for flag in ("--session", "--interval", "--option", "--help"):
        assert flag in result.stdout, result.stdout
