"""
test_dashboard_pane_visibility.py
=================================
End-to-end coverage of the dashboard's render-only-what-is-visible
behaviour (team/scripts/dashboard/lib/cadence.sh, cadence-loop.sh,
activation-hook-sender.sh, lib/activation_hook.sh):

  * A dashboard left detached on a private tmux socket (`tmux -L`) renders
    each pane once on arrival and never again -- including the window tmux
    marks active -- because nobody is attached to see it.
  * When a client attaches, the active window renders within one cadence
    (the client-attached hook wakes its loops); other windows stay quiet.
  * On a window switch the arriving window renders within one cadence and
    the departed window's loops stop: their next wait is the hidden
    re-check, with no renderer running.
  * A --stream pane's renderer is stopped while hidden and continued when
    visible, and never outlives its loop. The test finds the renderer as
    the one loop child that leads its own process group (the loop starts
    it under setsid), never by command line, which the loop's own
    subshells share.
  * A loop registers its pid for its pane only after its wake handler is
    installed; the sender wakes only a registered, still-running
    cadence-loop.sh -- a stale registration never signals another process.

The tmux tests run every pane through the real pane preamble
(lib/pane_readiness.sh dashboard_pane_wrap_cmd), the real cadence-loop.sh,
and the real activation hook, on a private socket; a client is attached on
a pseudo-terminal. Renders are counted by a renderer that appends one line
per render to a per-pane file. tmux-dependent tests narrate a skip when
tmux is not on PATH.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import pty
import select
import shlex
import signal
import subprocess
import threading
import time
import uuid
from typing import Callable, Iterator, NamedTuple

import pytest

from tests.fixtures.binary_guard import narrate_binary_skip, try_resolve_binary

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_DASHBOARD = _TEAM_DIR / "scripts" / "dashboard"
_CADENCE_LOOP = _DASHBOARD / "cadence-loop.sh"
_CADENCE_LIB = _DASHBOARD / "lib" / "cadence.sh"
_READINESS_LIB = _DASHBOARD / "lib" / "pane_readiness.sh"
_HOOK_LIB = _DASHBOARD / "lib" / "activation_hook.sh"

_INTERVAL = 3
_HIDDEN_INTERVAL = _INTERVAL * 10
_HANG_GUARD_SECONDS = 60.0


def _wait_until(condition: Callable[[], bool], what: str, diagnostics: Callable[[], str] = lambda: "") -> float:
    """Poll <condition>; return the seconds it took. Fails as hung after
    _HANG_GUARD_SECONDS."""
    started = time.monotonic()
    while not condition():
        if time.monotonic() - started > _HANG_GUARD_SECONDS:
            pytest.fail(f"waiting for {what}: nothing happened within {_HANG_GUARD_SECONDS:.0f}s\n{diagnostics()}")
        time.sleep(0.05)
    return time.monotonic() - started


class Fixture(NamedTuple):
    tmux: str
    socket: str
    session: str
    root: pathlib.Path
    env: dict[str, str]

    def run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([self.tmux, "-L", self.socket, *args], capture_output=True, text=True, timeout=15)

    def counter(self, window: str) -> pathlib.Path:
        return self.root / f"renders-{window}.log"

    def renders(self, window: str) -> int:
        path = self.counter(window)
        return len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0


def _bash(env: dict[str, str], script: str) -> str:
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture
def dashboard(tmp_path: pathlib.Path) -> Iterator[Fixture]:
    """Two windows (winA, winB), one counting cadence-loop pane each,
    built detached on a private socket, all panes armed, activation hooks
    installed. winB (created last) is the active window."""
    guard = try_resolve_binary("tmux")
    socket = f"pgai-vis-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    try:
        if not guard.resolved:
            narrate_binary_skip("tmux", label="dashboard-pane-visibility")
        root = tmp_path / "kanban_root"
        (root / "tmp").mkdir(parents=True)
        shim = tmp_path / "bin"
        shim.mkdir()
        (shim / "tmux").write_text(
            f'#!/usr/bin/env bash\nexec {shlex.quote(guard.path)} -L {shlex.quote(socket)} "$@"\n', encoding="utf-8"
        )
        (shim / "tmux").chmod(0o755)
        env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE", "PGAI_DASHBOARD_CADENCE_FORCE_STATE")}
        env.update(
            PATH=f"{shim}{os.pathsep}{env.get('PATH', '')}",
            PGAI_AGENT_KANBAN_ROOT_PATH=str(root),
            PGAI_AGENT_KANBAN_TEMP_DIR=str(root / "tmp"),
            TERM="xterm-256color",
        )
        session = f"vis-{uuid.uuid4().hex[:6]}"
        fx = Fixture(guard.path, socket, session, tmp_path, env)
        # The server inherits this environment (PATH with the shim, the
        # sandboxed roots), as create.sh's server does.
        subprocess.run([guard.path, "-L", socket, "new-session", "-d", "-s", session, "-n", "winA", "-x", "120", "-y", "30"],
                       env=env, check=True, timeout=15)
        assert fx.run("new-window", "-t", session, "-n", "winB").returncode == 0

        armed_dir = _bash(env, f"source {shlex.quote(str(_READINESS_LIB))}; "
                               f"dashboard_pane_armed_dir {shlex.quote(str(root))} {shlex.quote(session)}").strip()
        for window in ("winA", "winB"):
            renderer = f"echo render >> {shlex.quote(str(fx.counter(window)))}; date +%s.%N"
            inner = (f"{shlex.quote(str(_CADENCE_LOOP))} --interval {_INTERVAL} --jitter-bound 0 -- "
                     f"bash -c {shlex.quote(renderer)}")
            wrapped = _bash(env, f"source {shlex.quote(str(_READINESS_LIB))}; "
                                 f"dashboard_pane_wrap_cmd {shlex.quote(armed_dir)} {window}.0 {shlex.quote(inner)}")
            assert fx.run("send-keys", "-t", f"{session}:{window}.0", wrapped, "Enter").returncode == 0
        for window in ("winA", "winB"):
            _wait_until(lambda w=window: fx.renders(w) >= 1, f"the arrival render of {window}")
        _wait_until(lambda: all(_loop_pid(fx, w) for w in ("winA", "winB")), "both loops to register")
        _bash(env, f"source {shlex.quote(str(_HOOK_LIB))}; dashboard_install_activation_hook "
                   f"{shlex.quote(str(root))} {shlex.quote(session)} {shlex.quote(str(_DASHBOARD))}")
        yield fx
    finally:
        guard.teardown(lambda path: subprocess.run([path, "-L", socket, "kill-server"], capture_output=True, timeout=10))


def _pane_id(fx: Fixture, window: str) -> str:
    return fx.run("display-message", "-p", "-t", f"{fx.session}:{window}.0", "#{pane_id}").stdout.strip()


def _loop_pid(fx: Fixture, window: str) -> int | None:
    registry = _bash(fx.env, f"source {shlex.quote(str(_CADENCE_LIB))}; "
                             f"dashboard_cadence_loop_registry_path {shlex.quote(_pane_id(fx, window))}").strip()
    try:
        text = pathlib.Path(registry).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None


def _children(pid: int) -> list[tuple[int, str]]:
    out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True).stdout.split()
    result = []
    for child in out:
        with contextlib.suppress(OSError):
            cmd = pathlib.Path(f"/proc/{child}/cmdline").read_bytes().replace(b"\0", b" ").decode().strip()
            result.append((int(child), cmd))
    return result


def _pending_wait_seconds(fx: Fixture, window: str) -> str | None:
    """The argument of the `sleep` the window's loop is waiting on, or None
    when it is not waiting (e.g. mid-render)."""
    pid = _loop_pid(fx, window)
    if pid is None:
        return None
    for _, cmd in _children(pid):
        parts = cmd.split()
        if parts and parts[0].endswith("sleep") and len(parts) == 2:
            return parts[1]
    return None


@contextlib.contextmanager
def _attached(fx: Fixture) -> Iterator[None]:
    pid, fd = pty.fork()
    if pid == 0:  # pragma: no cover - child process
        os.environ["TERM"] = "xterm-256color"
        os.execv(fx.tmux, [fx.tmux, "-L", fx.socket, "attach-session", "-t", fx.session])
    stop = threading.Event()

    def _drain() -> None:
        while not stop.is_set():
            try:
                ready, _, _ = select.select([fd], [], [], 0.1)
                if ready and not os.read(fd, 65536):
                    return
            except OSError:
                return

    drainer = threading.Thread(target=_drain, daemon=True)
    drainer.start()
    try:
        _wait_until(lambda: fx.run("display-message", "-p", "-t", fx.session, "#{session_attached}").stdout.strip()
                    not in ("", "0"), "a client to attach")
        yield
    finally:
        fx.run("detach-client", "-s", fx.session)
        with contextlib.suppress(ChildProcessError):
            os.waitpid(pid, 0)
        stop.set()
        drainer.join(timeout=5)
        with contextlib.suppress(OSError):
            os.close(fd)


def test_detached_dashboard_renders_nothing_after_its_arrival_renders(dashboard: Fixture) -> None:
    fx = dashboard
    # Three full visible intervals: a pane that still rendered while
    # detached would have rendered at least twice more by now.
    time.sleep(_INTERVAL * 3)
    assert fx.renders("winA") == 1, "a hidden window rendered after its arrival render"
    assert fx.renders("winB") == 1, "the window tmux marks active rendered while nobody was attached"
    assert _pending_wait_seconds(fx, "winA") == str(_HIDDEN_INTERVAL)
    assert _pending_wait_seconds(fx, "winB") == str(_HIDDEN_INTERVAL)


def test_attach_renders_the_active_window_and_a_switch_moves_rendering_with_it(dashboard: Fixture) -> None:
    fx = dashboard
    with _attached(fx):
        latency = _wait_until(lambda: fx.renders("winB") >= 2, "the active window to render after attach")
        assert latency <= _INTERVAL, f"the active window took {latency:.2f}s to render after attach"
        _wait_until(lambda: fx.renders("winB") >= 3, "the active window's next cadence render")
        assert fx.renders("winA") == 1, "a hidden window rendered while another window was active"

        assert fx.run("select-window", "-t", f"{fx.session}:winA").returncode == 0
        departed_at_switch = fx.renders("winB")
        latency = _wait_until(lambda: fx.renders("winA") >= 2, "the arriving window to render")
        assert latency <= _INTERVAL, f"the arriving window took {latency:.2f}s to render"

        # The departed window's loop notices at its next tick and suspends:
        # its pending wait becomes the hidden re-check, with no renderer.
        _wait_until(lambda: _pending_wait_seconds(fx, "winB") == str(_HIDDEN_INTERVAL),
                    "the departed window's loop to suspend")
        settled = fx.renders("winB")
        assert settled <= departed_at_switch + 1, "the departed window kept rendering after the switch"
        time.sleep(_INTERVAL * 2)
        assert fx.renders("winB") == settled, "the departed window rendered while hidden"
        assert fx.renders("winA") >= 3, "the visible window stopped refreshing"

    # Detached again: everything suspends.
    _wait_until(lambda: _pending_wait_seconds(fx, "winA") == str(_HIDDEN_INTERVAL),
                "the last visible window to suspend after detach")
    after_detach = fx.renders("winA")
    time.sleep(_INTERVAL * 2)
    assert fx.renders("winA") == after_detach


# ---------------------------------------------------------------------------
# --stream panes (no tmux: the file-backed FORCE_STATE override)
# ---------------------------------------------------------------------------


def _proc_state(pid: int) -> str:
    try:
        return pathlib.Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[0]
    except OSError:
        return "gone"


def _group_members(pgid: int) -> list[int]:
    members = []
    for stat in pathlib.Path("/proc").glob("[0-9]*/stat"):
        with contextlib.suppress(OSError, IndexError, ValueError):
            fields = stat.read_text().rsplit(") ", 1)[1].split()
            if int(fields[2]) == pgid:
                members.append(int(stat.parent.name))
    return members


def _stream_pid(loop_pid: int) -> int | None:
    """The loop's stream: the one direct child that leads its own process
    group. The loop starts its renderer under setsid, so the stream is a
    group leader; every other child -- a command substitution, a visibility
    probe, the interval sleep -- stays in the loop's group, whatever its
    command line carries."""
    for pid, _cmd in _children(loop_pid):
        with contextlib.suppress(OSError):
            if os.getpgid(pid) == pid:
                return pid
    return None


def _end_stream_loop(proc: subprocess.Popen, stream: int | None) -> None:
    """End a --stream loop and everything it started, stopped or not.

    SIGTERM first, so the loop runs its stop trap, which continues and
    terminates the stream's process group. A trap never runs on SIGKILL, and
    a group-kill of the loop's own group never reaches the stream, which
    leads a group of its own -- so whatever is still left afterwards in the
    loop's group or in the stream's group (<stream>, its pgid) is continued
    and then killed: a stopped process only dies once it is continued.
    Safe when the loop or the stream has already exited."""
    proc.send_signal(signal.SIGTERM)  # a no-op once the loop has been reaped
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=5)
    for pgid in (proc.pid, stream):
        if pgid is None:
            continue
        for sig in (signal.SIGCONT, signal.SIGKILL):
            with contextlib.suppress(ProcessLookupError):
                os.killpg(pgid, sig)
    proc.wait(timeout=10)


@contextlib.contextmanager
def _hidden_stream_loop(tmp_path: pathlib.Path) -> Iterator[tuple[subprocess.Popen, pathlib.Path, pathlib.Path]]:
    """A --stream cadence loop whose pane reads hidden, in its own session;
    yields (loop process, stream output file, visibility state file).

    On exit it ends the loop and its stream, then asserts that neither the
    loop's process group nor the stream's process group has a member left."""
    state = tmp_path / "state"
    state.write_text("hidden\n", encoding="utf-8")
    out = tmp_path / "stream.out"
    env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")}
    env.update(PGAI_DASHBOARD_CADENCE_FORCE_STATE=f"file:{state}", PGAI_AGENT_KANBAN_TEMP_DIR=str(tmp_path / "t"),
               TERM="xterm-256color")
    with out.open("wb") as sink:
        proc = subprocess.Popen(
            ["bash", str(_CADENCE_LOOP), "--stream", "--interval", "0.2", "--",
             "bash", "-c", "while :; do echo tick; sleep 0.05; done"],
            env=env, stdin=subprocess.DEVNULL, stdout=sink, stderr=subprocess.DEVNULL, start_new_session=True,
        )
    try:
        yield proc, out, state
    finally:
        # The stream is found by walking the loop's children, so it can only
        # be found while the loop is still running.
        stream = _stream_pid(proc.pid) if proc.poll() is None else None
        _end_stream_loop(proc, stream)
        groups = {"loop": proc.pid, "stream": stream}
        # An ended process stays in its group until it is reaped (the stream
        # by init, once its loop has gone), so wait for the groups to empty.
        _wait_until(lambda: not any(_group_members(g) for g in groups.values() if g is not None),
                    "the loop's and the stream's process groups to have no member left",
                    lambda: "\n".join(f"{name} group {g}: members {[(m, _proc_state(m)) for m in _group_members(g)]}"
                                      for name, g in groups.items() if g is not None))


def test_stream_selector_returns_only_the_stream_during_loop_start_up(tmp_path: pathlib.Path) -> None:
    """Sampled through the loop's start-up, when its short-lived subshells
    carry the same command line as the stream, the selector only ever
    returns the stream: every pid it returned ends stopped (state T) once
    the hidden stream is paused."""
    returned: set[int] = set()
    with _hidden_stream_loop(tmp_path) as (proc, _out, _state):
        sample_until = time.monotonic() + 2.0
        while time.monotonic() < sample_until:
            pid = _stream_pid(proc.pid)
            if pid is not None:
                returned.add(pid)
            time.sleep(0.005)
        assert returned, "the stream never started during the sampled start-up"
        for pid in returned:
            _wait_until(lambda p=pid: _proc_state(p) == "T", f"selected pid {pid} to be the stopped stream",
                        lambda p=pid: f"state of {p}: {_proc_state(p)}; returned pids: {sorted(returned)}")
        assert len(returned) == 1, f"the selector returned more than one pid: {sorted(returned)}"


def test_stream_pane_is_stopped_while_hidden_and_continued_when_visible(tmp_path: pathlib.Path) -> None:
    with _hidden_stream_loop(tmp_path) as (proc, out, state):
        _wait_until(lambda: _stream_pid(proc.pid) is not None, "the stream to start")
        stream = _stream_pid(proc.pid)
        assert stream is not None
        _wait_until(lambda: _proc_state(stream) == "T", "the hidden stream to be stopped")
        size = out.stat().st_size
        time.sleep(0.5)
        assert out.stat().st_size == size, "a stopped stream kept writing"
        assert os.getpgid(stream) == stream, "the stream runs in its own process group"

        state.write_text("active\n", encoding="utf-8")
        os.kill(proc.pid, signal.SIGUSR1)
        _wait_until(lambda: _proc_state(stream) != "T", "the visible stream to continue")
        _wait_until(lambda: out.stat().st_size > size, "the continued stream to write")

        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)
        _wait_until(lambda: not _group_members(stream), "the stream group to end with its loop")


# ---------------------------------------------------------------------------
# Loop registration and wake
# ---------------------------------------------------------------------------


def test_registered_loop_is_woken_and_unregisters_on_exit(tmp_path: pathlib.Path) -> None:
    state = tmp_path / "state"
    state.write_text("hidden\n", encoding="utf-8")
    counter = tmp_path / "renders"
    env = {k: v for k, v in os.environ.items() if k != "TMUX"}
    env.update(PGAI_DASHBOARD_CADENCE_FORCE_STATE=f"file:{state}", PGAI_AGENT_KANBAN_TEMP_DIR=str(tmp_path / "t"),
               TMUX_PANE="%4242", TERM="xterm-256color")
    registry = pathlib.Path(_bash(env, f"source {shlex.quote(str(_CADENCE_LIB))}; "
                                       "dashboard_cadence_loop_registry_path %4242").strip())
    # The pane preamble ignores the pane signal before the loop starts, as
    # it does in a real pane: the loop must still be wakeable.
    script = (f"trap '' USR1; exec bash {shlex.quote(str(_CADENCE_LOOP))} --interval 60 --jitter-bound 0 -- "
              f"bash -c {shlex.quote(f'echo r >> {counter}')}")
    proc = subprocess.Popen(["bash", "-c", script], env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        _wait_until(lambda: registry.exists() and registry.read_text().strip() == str(proc.pid),
                    "the loop to register its pid")
        _wait_until(lambda: counter.exists(), "the arrival render")
        state.write_text("active\n", encoding="utf-8")
        _bash(env, f"source {shlex.quote(str(_CADENCE_LIB))}; dashboard_cadence_wake_loop %4242")
        _wait_until(lambda: len(counter.read_text().splitlines()) >= 2, "a render after the wake")
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)
        assert not registry.exists(), "the registration must be removed when the loop exits"
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)


def test_stale_registration_never_signals_an_unrelated_process(tmp_path: pathlib.Path) -> None:
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(tmp_path / "t")
    bystander = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        registry = pathlib.Path(_bash(env, f"source {shlex.quote(str(_CADENCE_LIB))}; "
                                           "dashboard_cadence_loop_registry_path %77").strip())
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(f"{bystander.pid}\n", encoding="utf-8")
        result = subprocess.run(
            ["bash", "-c", f"source {shlex.quote(str(_CADENCE_LIB))}; dashboard_cadence_wake_loop %77"],
            capture_output=True, text=True, env=env, timeout=30,
        )
        assert result.returncode == 1
        time.sleep(0.3)
        assert bystander.poll() is None, "a stale registration signalled a process that is not a cadence loop"
    finally:
        bystander.kill()
        bystander.wait(timeout=10)
