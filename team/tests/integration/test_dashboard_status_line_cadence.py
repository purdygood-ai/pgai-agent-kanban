"""
test_dashboard_status_line_cadence.py
=====================================
The dashboard's bottom status line costs one render per refresh interval,
however many clients are attached and however often the panes redraw.

End to end on a private tmux server (tmux -L <private>) with a fixture
kanban root: team/scripts/dashboard/create.sh builds the real board, then
two clients attach through pseudo-terminals for 60 seconds while every pane
refreshes on its own cadence. Pinned:

  * status-format[2] runs no #() job: it reads the session's
    @pgai_status_bottom option;
  * exactly one status-bottom-loop.sh runs for the session, started by
    create.sh, and the option carries the rendered line (the fixture's
    global HALT shows in it);
  * over the 60 attached seconds, status-bottom.sh starts at most 13 times
    (once per 5-second interval, plus one) -- counted by process start, the
    renderer's own subshells excluded;
  * after kill.sh tears the session down, the loop exits within one
    interval and a margin.

Skipped (narrated) when tmux is not on PATH or /proc is unavailable.
"""

from __future__ import annotations

import os
import pathlib
import pty
import shlex
import subprocess
import sys
import threading
import time
import uuid
from typing import Iterator, NamedTuple

import pytest

from tests.fixtures.binary_guard import require_binary, teardown_private_tmux
from tests.fixtures.process_progress import proc_start_time, require_procfs
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_REPO_ROOT = _TEAM_DIR.parent
_CREATE_SH = _TEAM_DIR / "scripts" / "dashboard" / "create.sh"
_KILL_SH = _TEAM_DIR / "scripts" / "dashboard" / "kill.sh"
_LABEL = "status-line-cadence"

_ATTACHED_SECONDS = 60
_REFRESH_SECONDS = 5
_MAX_RENDERS = _ATTACHED_SECONDS // _REFRESH_SECONDS + 1
_PROC = pathlib.Path("/proc")


class PrivateTmux(NamedTuple):
    real_tmux: str
    socket_name: str
    env: dict

    def run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [self.real_tmux, "-L", self.socket_name, *args],
            capture_output=True, text=True, timeout=15, check=False,
        )


def _fixture_root(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "kanban-root"
    (root / "projects" / "alpha").mkdir(parents=True)
    (root / "VERSION").write_text("v9.9.9\n", encoding="utf-8")
    (root / "projects.cfg").write_text("[project:alpha]\npriority=1\n\n", encoding="utf-8")
    (root / "projects" / "alpha" / "project.cfg").write_text(
        "[project]\nworkflow_type = release\n", encoding="utf-8"
    )
    (root / "HALT").write_text("", encoding="utf-8")
    return root


@pytest.fixture
def private_tmux(tmp_path: pathlib.Path) -> Iterator[PrivateTmux]:
    """create.sh and kill.sh run the bare `tmux` word, so a PATH-first
    wrapper puts every one of their calls on the private server."""
    require_procfs(_LABEL)
    guard = require_binary("tmux", label=_LABEL)
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
    root = _fixture_root(tmp_path)
    env = build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[bin_dir, pathlib.Path(sys.executable).parent],
        extra_env={
            "TERM": "xterm-256color",
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "scratch"),
            "PGAI_DEV_TREE_PATH": str(_REPO_ROOT),
            "PGAI_DASHBOARD_REFRESH_SECONDS": str(_REFRESH_SECONDS),
        },
    )
    try:
        yield PrivateTmux(guard.path, socket_name, env)
    finally:
        teardown_private_tmux(guard, socket_name, env=env)


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
        threading.Thread(target=self._drain, daemon=True).start()

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


def _argv(pid: "int | str") -> list[str]:
    try:
        raw = (_PROC / str(pid) / "cmdline").read_bytes()
    except OSError:
        return []
    return [part.decode(errors="replace") for part in raw.split(b"\0") if part]


def _ppid(pid: str) -> "str | None":
    try:
        stat = (_PROC / pid / "stat").read_text()
    except OSError:
        return None
    return stat.rsplit(")", 1)[1].split()[1]


def _runs_script(argv: list[str], script_name: str, root: pathlib.Path) -> bool:
    """True when *argv* is a shell running *script_name* (as the script
    itself, not as an argument to it) against the fixture *root*."""
    head = argv[:2]
    return any(part.endswith("/" + script_name) for part in head) and str(root) in argv


class _RendererStartCounter:
    """Counts status-bottom.sh process starts against one fixture root.

    Polls /proc; a process counts when it runs status-bottom.sh and its
    parent does not (the renderer's own command-substitution subshells
    carry the same command line and are not separate renders)."""

    def __init__(self, root: pathlib.Path) -> None:
        self._root = root
        self.seen: set[tuple[str, str]] = set()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._poll, daemon=True)

    def _poll(self) -> None:
        while not self._stop.is_set():
            for entry in _PROC.iterdir():
                if not entry.name.isdigit():
                    continue
                argv = _argv(entry.name)
                if not _runs_script(argv, "status-bottom.sh", self._root):
                    continue
                parent = _ppid(entry.name)
                if parent and _runs_script(_argv(parent), "status-bottom.sh", self._root):
                    continue
                start = proc_start_time(int(entry.name))
                if start is not None:
                    self.seen.add((entry.name, start))
            time.sleep(0.05)

    def __enter__(self) -> "_RendererStartCounter":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=10)


def _loop_pids(session: str) -> list[int]:
    pids = []
    for entry in _PROC.iterdir():
        if entry.name.isdigit():
            argv = _argv(entry.name)
            if any(p.endswith("/status-bottom-loop.sh") for p in argv[:2]) and session in argv:
                pids.append(int(entry.name))
    return pids


def test_two_attached_clients_cost_one_render_per_interval(
    private_tmux: PrivateTmux, tmp_path: pathlib.Path
) -> None:
    session = f"cadence-{uuid.uuid4().hex[:6]}"
    root = pathlib.Path(private_tmux.env["PGAI_AGENT_KANBAN_ROOT_PATH"])

    log_path = tmp_path / "create.log"
    with log_path.open("w") as log:
        proc = subprocess.Popen(
            ["bash", str(_CREATE_SH), "--kanban-root", str(root), "--session", session],
            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=private_tmux.env,
        )
        # The final exec-attach fails headless ("open terminal failed"); the
        # session, its panes and the status loop already exist by then.
        try:
            proc.wait(timeout=600)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
    create_output = log_path.read_text()
    assert private_tmux.run("has-session", "-t", f"={session}").returncode == 0, create_output

    status_format = private_tmux.run("show-options", "-t", session, "-v", "status-format[2]").stdout
    assert "#(" not in status_format, status_format
    assert "#{@pgai_status_bottom}" in status_format, status_format

    loops = _loop_pids(session)
    assert len(loops) == 1, f"expected one status loop for {session}, found pids {loops}"

    clients = [_AttachedClient(private_tmux, session) for _ in range(2)]
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            listed = private_tmux.run("list-clients", "-t", f"={session}").stdout.splitlines()
            if len(listed) == 2:
                break
            time.sleep(0.2)
        assert len(listed) == 2, f"expected two attached clients, tmux lists {listed}"

        with _RendererStartCounter(root) as counter:
            time.sleep(_ATTACHED_SECONDS)
    finally:
        for client in clients:
            client.close()

    starts = len(counter.seen)
    assert starts <= _MAX_RENDERS, (
        f"status-bottom.sh started {starts} times in {_ATTACHED_SECONDS}s with two clients "
        f"attached; at most {_MAX_RENDERS} (one per {_REFRESH_SECONDS}s interval, plus one) "
        "is the bound"
    )
    assert starts >= 3, f"the status loop rendered only {starts} times while clients were attached"

    line = private_tmux.run("show-options", "-v", "-t", f"={session}:", "@pgai_status_bottom").stdout
    assert "HALT GLOBAL" in line, line

    kill = subprocess.run(
        ["bash", str(_KILL_SH), "--session", session], env=private_tmux.env,
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert kill.returncode == 0, kill.stdout + kill.stderr
    deadline = time.monotonic() + _REFRESH_SECONDS + 15
    while time.monotonic() < deadline and _loop_pids(session):
        time.sleep(0.2)
    assert not _loop_pids(session), "the status loop outlived its session"
